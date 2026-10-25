"""Bounded continuation authority in the existing Ops DB, never a worker retry loop."""
from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from spireagent.hub.database import Operations
from spireagent.json_boundary import BoundaryError

KEY = "training_continuation"


def read(ops: Operations) -> dict[str, Any] | None:
    with ops.transaction() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
        return json.loads(row[0]) if row else None


def _save(ops: Operations, db: Any, policy: dict[str, Any]) -> None:
    db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (KEY, json.dumps(policy)))
    ops._event(db, "application", "training_continuation", policy["root_job_id"], policy)


def arm(ops: Operations, job: dict[str, Any], *, target_id: str, producer: dict[str, str],
        deadline: float, max_new_segments: int, max_seconds: int) -> dict[str, Any]:
    policy = {"root_job_id": job["id"], "current_job_id": job["id"],
              "run_id": job["input_id"], "target_id": target_id, "producer": producer,
              "deadline_unix": deadline, "max_new_segments": max_new_segments,
              "max_seconds": max_seconds, "new_segments": 0, "state": "armed", "enabled": True}
    with ops.transaction() as db:
        old = db.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
        if old and json.loads(old[0])["enabled"]:
            existing = cast(dict[str, Any], json.loads(old[0]))
            if all(existing[k] == policy[k] for k in (
                    "root_job_id", "run_id", "target_id", "producer", "deadline_unix",
                    "max_new_segments", "max_seconds")):
                return existing
            raise BoundaryError("remote_training", "existing_continuation_must_be_stopped")
        current = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        if (current is None or current["attempt_id"] != job["attempt_id"]
                or current["kind"] != "training" or current["status"] not in {
                    "running", "uncertain", "submission_unknown", "paused"}):
            raise BoundaryError("remote_training", "continuation_original_attempt_required")
        _save(ops, db, policy)
    return policy


def stop(ops: Operations, root: str, reason: str) -> dict[str, Any]:
    with ops.transaction() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
        if row is None or json.loads(row[0])["root_job_id"] != root:
            raise BoundaryError("remote_training", "continuation_not_found")
        policy = cast(dict[str, Any], json.loads(row[0]))
        if policy["enabled"]:
            policy.update(enabled=False, state=reason)
            _save(ops, db, policy)
        return policy


def advance(ops: Operations, policy: dict[str, Any], job: dict[str, Any], checkpoint: str,
            *, now: float, reserved_units: int, budget_limit: int) -> dict[str, Any]:
    """Atomically consume one bounded authorization, enqueue once and move the cursor."""
    with ops.transaction() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
        if row is None or json.loads(row[0]) != policy or not policy["enabled"]:
            raise BoundaryError("remote_training", "stale_continuation")
        current = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        if (current is None or dict(current) != job or job["id"] != policy["current_job_id"]
                or job["input_id"] != policy["run_id"] or job["status"] != "paused"
                or json.loads(job["result"])["checkpoint_id"] != checkpoint):
            raise BoundaryError("remote_training", "exact_paused_checkpoint_required")
        if db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0] == "1":
            return policy
        reason = None
        preparation = json.loads(job["options"]).get("preparation_seconds", 0)
        if now + preparation + policy["max_seconds"] > policy["deadline_unix"]:
            reason = "deadline_exhausted"
        elif policy["new_segments"] >= policy["max_new_segments"]:
            reason = "segment_limit_reached"
        elif db.execute("SELECT 1 FROM jobs WHERE status IN "
                        "('queued','running','uncertain','submission_unknown','cancelling')"
                        ).fetchone():
            reason = "other_operation_present"
        if reason is not None:
            policy = {**policy, "enabled": False, "state": reason}
            _save(ops, db, policy)
            return policy
        intent = hashlib.sha256((policy["root_job_id"] + job["id"] + job["attempt_id"]
                                 + checkpoint).encode()).hexdigest()[:32]
        options = json.dumps({"resume": checkpoint, "stop_after": None,
                              "execution_deadline_unix": policy["deadline_unix"],
                              **({"preparation_seconds": preparation} if preparation else {})},
                             sort_keys=True, separators=(",", ":"))
        successor = ops._enqueue(db, "training", policy["run_id"], intent,
                                 policy["max_seconds"], reserved_units, budget_limit, options)
        policy = {**policy, "current_job_id": successor,
                  "new_segments": policy["new_segments"] + 1, "state": "armed"}
        _save(ops, db, policy)
        return policy
