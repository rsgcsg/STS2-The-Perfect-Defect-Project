"""A persistent slot ledger over the existing serial Reference dispatcher.

No gameplay, retry, model registry, or evidence format is implemented here.
The immutable plan and append-only slot events outlive any dispatcher process.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from spireagent.instance_lock import instance_lock
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.local import LocalBlobStore

from .developer import ProjectConfig
from .local_model_cli import native_episode_command
from .reference_suite import (
    MAX_REPORT_BYTES,
    SuiteLimits,
    WorkbenchReferenceClient,
    _read,
    load_manifest,
    run_suite,
    verify_completed_row,
)

SCHEMA = "spireagent/reference-evaluation-ledger-v1"


def reject(code: str) -> NoReturn:
    raise BoundaryError("evaluation_ledger", code)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def reference(path: Path) -> dict[str, Any]:
    raw = _read(path, max_bytes=MAX_REPORT_BYTES)
    return {"path": str(path.resolve()), "sha256": digest(raw), "bytes": len(raw)}


def read_reference(ref: dict[str, Any]) -> bytes:
    raw = _read(Path(ref["path"]), max_bytes=MAX_REPORT_BYTES)
    if len(raw) != ref["bytes"] or digest(raw) != ref["sha256"]:
        reject("original_reference_changed")
    return raw


def result_row(row: dict[str, Any], report_path: Path) -> dict[str, Any]:
    verify_completed_row(report_path, row)
    preservation = json.loads(_read(report_path.parent / row["preservation"]["path"]))
    readback = preservation.get("peer_readback") or {}
    if (readback.get("full_readback") is not True
            or readback.get("typed_verification") != "pass"):
        reject("full_typed_R2_receipt_required")
    observation = (row.get("agent_task_outcome") or {}).get("terminal_observation") or {}
    if observation.get("status") != "observed" or observation.get("result") not in {"win", "loss"}:
        reject("natural_terminal_required")
    return {"status": "finished", "run_id": row["run_id"],
            "natural_result": observation["result"],
            "max_observed_floor": (row.get("game_metrics") or {}).get("max_observed_floor"),
            "stored_evidence_id": row["stored_evidence_id"],
            "report": reference(report_path),
            "world_mutation_offered": row.get("world_mutation_offered") is True}


class EvaluationLedger:
    def __init__(self, directory: Path) -> None:
        if directory.is_symlink() or directory.resolve() != directory:
            reject("unsafe_ledger_directory")
        self.directory = directory
        self.store = LocalBlobStore(directory, create=False)
        self.plan = json.loads(self.store.get("plan.json"))
        if self.plan.get("schema") != SCHEMA:
            reject("unsupported_ledger")
        for ref in self.plan["originals"]:
            read_reference(ref)

    @classmethod
    def import_matrix(cls, directory: Path, matrix: Path, episodes: Path,
                      config: ProjectConfig) -> EvaluationLedger:
        if directory.exists() or directory.is_symlink():
            reject("ledger_already_exists")
        frozen = json.loads(_read(matrix))
        rows = list(csv.DictReader(io.StringIO(_read(episodes).decode("utf-8"))))
        expected = {}
        for group in frozen["groups"]:
            manifest_path = Path(group["manifest_path"])
            _, manifest = load_manifest(manifest_path, group["manifest_sha256"])
            if (group["character"] != manifest["character"]
                    or group["ascension"] != manifest["ascension"]):
                reject("frozen_group_changed")
            for ordinal, seed in enumerate(manifest["seeds"], 1):
                for step, selection in group["selections"].items():
                    expected[(group["character"], str(group["ascension"]), seed, step)] = {
                        "character": group["character"], "ascension": group["ascension"],
                        "seed": seed, "seed_ordinal": ordinal, "step": step,
                        "selection_id": selection, "manifest": reference(manifest_path)}
        if len(rows) != frozen["planned"] or len(expected) != len(rows):
            reject("full_denominator_required")
        reports: dict[str, Any] = {}
        conditions = None
        slots = []
        for original in rows:
            key = (original["character"], original["ascension"], original["seed"], original["step"])
            if key not in expected:
                reject("duplicate_or_unplanned_slot")
            slot = expected.pop(key)
            if (original["selection_id"] != slot["selection_id"]
                    or int(original["seed_ordinal"]) != slot["seed_ordinal"]):
                reject("original_slot_binding_changed")
            slot["slot_id"] = digest(json_bytes({"matrix": digest(_read(matrix)), **slot}))
            slot["imported_csv_row"] = original
            if original["status"] == "not_started":
                if original.get("run_id") or original.get("stored_evidence_id"):
                    reject("not_started_slot_has_run")
                path = (Path(original["original_report"])
                        if original.get("original_report") else None)
                if path is not None and path.is_file():
                    if str(path) not in reports:
                        reports[str(path)] = json.loads(_read(path, max_bytes=MAX_REPORT_BYTES))
                    matches = [r for r in reports[str(path)]["episodes"]
                               if r["seed"] == slot["seed"]
                               and r["selection_id"] == slot["selection_id"]]
                    if (len(matches) != 1 or matches[0]["status"] != "not_started"
                            or matches[0].get("world_mutation_offered")
                            or matches[0].get("actor_started")):
                        reject("not_started_original_has_offer")
                slot["status"] = "not_started"
            else:
                path = Path(original["original_report"])
                if str(path) not in reports:
                    reports[str(path)] = json.loads(_read(path, max_bytes=MAX_REPORT_BYTES))
                report = reports[str(path)]
                current = {k: report[k] for k in (
                    "run_profile", "display_mode", "speed", "evidence_peer")}
                if conditions is not None and conditions != current:
                    reject("mixed_original_conditions")
                conditions = current
                if report["manifest"]["sha256"] != slot["manifest"]["sha256"]:
                    reject("original_manifest_changed")
                matches = [r for r in report["episodes"]
                           if r["seed"] == slot["seed"]
                           and r["selection_id"] == slot["selection_id"]]
                if len(matches) != 1 or matches[0].get("run_id") != original["run_id"]:
                    reject("original_episode_changed")
                old = matches[0]
                if original["status"] == "finished":
                    slot.update(result_row(old, path))
                    if slot["natural_result"] != original["natural_result"]:
                        reject("original_result_changed")
                elif original["status"] in {"unknown", "interrupted_unknown"}:
                    slot.update(status="unknown", run_id=original["run_id"],
                                environment_ref=old.get("environment_ref"),
                                world_mutation_offered=old.get("world_mutation_offered") is True,
                                reason=original["reason"], report=reference(path))
                else:
                    reject("unresolved_import_requires_explicit_unknown")
            slots.append(slot)
        if conditions is None:
            reject("original_conditions_required")
        plan = {"schema": SCHEMA, "created_at": datetime.now(UTC).isoformat(),
                "state_dir": str(config.state_dir),
                "originals": [reference(matrix), reference(episodes)],
                "conditions": conditions, "slots": slots, "planned": len(slots),
                "retry_unknown": False, "replacements": "not_implemented"}
        LocalBlobStore(directory).put_if_absent("plan.json", json_bytes(plan))
        return cls(directory)

    def event(self, slot_id: str, kind: str, **value: Any) -> None:
        number = len(self.store.keys("events/")) + 1
        self.store.put_if_absent(f"events/{number:06d}.json", json_bytes({
            "slot_id": slot_id, "kind": kind, "at": datetime.now(UTC).isoformat(), **value}))

    def slots(self) -> list[dict[str, Any]]:
        rows = {r["slot_id"]: dict(r) for r in self.plan["slots"]}
        for key in self.store.keys("events/"):
            event = json.loads(self.store.get(key))
            row = rows[event["slot_id"]]
            kind = event["kind"]
            if kind == "reserved":
                if row["status"] != "not_started":
                    reject("slot_already_offered")
                row.update(status="reserved", attempt_report=event["report_path"])
            elif kind == "result":
                if row["status"] != "reserved":
                    reject("slot_result_without_reservation")
                row.update(event["result"])
            elif kind == "release":
                if row["status"] != "unknown" or event["receipt"]["result"] != "unknown":
                    reject("release_requires_unknown")
                row["unknown_release"] = event["receipt"]
            elif kind == "cache":
                row["cache"] = event["receipt"]
            else:
                reject("unsupported_ledger_event")
        return list(rows.values())

    def seal_unknown(self, config: ProjectConfig, expected: dict[str, Any]) -> dict[str, Any]:
        rows = [r for r in self.slots() if r["status"] == "unknown"
                and r.get("run_id") == expected.get("expected_run_id")
                and r.get("environment_ref") == expected.get("expected_environment_ref")]
        if len(rows) != 1:
            reject("unknown_slot_identity_mismatch")
        receipt = native_episode_command(config, "seal-unknown", expected_release=expected,
                                        timeout_seconds=120)
        if (receipt.get("released") is not True or receipt.get("result") != "unknown"
                or receipt.get("expected") != expected):
            reject("unknown_release_unconfirmed")
        self.event(rows[0]["slot_id"], "release", receipt=receipt)
        return receipt

    def reconcile_reports(self) -> None:
        """Read an abandoned original attempt, never reoffer or recover a command."""
        for slot in self.slots():
            if slot["status"] == "reserved":
                path = Path(slot["attempt_report"])
                if path.exists():
                    report = json.loads(_read(path, max_bytes=MAX_REPORT_BYTES))
                    row = report["episodes"][0]
                    result = {"status": "unknown",
                              "reason": "abandoned_attempt_requires_owner_read",
                              "run_id": row.get("run_id"),
                              "environment_ref": row.get("environment_ref"),
                              "world_mutation_offered": row.get("world_mutation_offered") is True,
                              "report": reference(path)}
                    if row.get("status") == "finished" and not row.get("preservation_error"):
                        try:
                            result = result_row(row, path)
                        except (OSError, ValueError, KeyError, TypeError) as error:
                            result["reason"] = (error.code if isinstance(error, BoundaryError)
                                                else type(error).__name__)
                    elif row.get("status") == "not_started" and report.get("status") != "running":
                        result = {"status": "not_started", "reason": report.get("stop_reason"),
                                  "report": reference(path)}
                else:
                    result = {"status": "unknown", "reason": "reserved_attempt_report_unavailable"}
                self.event(slot["slot_id"], "result", result=result)

    def run(self, config: ProjectConfig, *, max_episodes: int = 200,
            limits: SuiteLimits | None = None, cool: bool = True) -> dict[str, Any]:
        if str(config.state_dir) != self.plan["state_dir"]:
            reject("original_runtime_state_required")
        self.reconcile_reports()
        if any(r["status"] == "unknown" and not r.get("unknown_release") for r in self.slots()):
            return {"status": "stopped", "stop_reason": "unknown_requires_explicit_seal"}
        client = WorkbenchReferenceClient(config)
        resource = client.native_status(30)
        if resource.get("environment_ref") is not None:
            preview = native_episode_command(
                config, "unknown-status", expected_instance_id=client.identity[
                    "application_instance_id"], timeout_seconds=30)
            if (preview.get("publication") or {}).get("requires_confirmation"):
                return {"status": "stopped",
                        "stop_reason": "publication_unknown_requires_explicit_seal"}
            old_unknown = any(r["status"] == "unknown"
                              and r.get("environment_ref") == resource["environment_ref"]
                              for r in self.slots())
            if old_unknown and preview.get("release") is None:
                return {"status": "stopped",
                        "stop_reason": "unknown_requires_current_Application_confirmation"}
            if (resource.get("phase") not in {"idle", "closed", "failed"}
                    and preview.get("release") is None):
                return {"status": "stopped",
                        "stop_reason": "native_environment_busy_or_unknown"}
        limits = limits or SuiteLimits(150, 1800, 180, 86400, 2, 30)
        conditions = self.plan["conditions"]
        count = 0
        for slot in self.slots():
            if slot["status"] != "not_started":
                continue
            if count >= max_episodes or (self.directory / "STOP").exists():
                return {"status": "stopped", "stop_reason": "stop_requested"}
            # Reserve once before invoking even the existing dispatcher's first
            # write. Process loss cannot return an uncertain slot to the queue.
            number = len(self.store.keys("events/")) + 1
            attempt = self.directory / "attempts" / f"{number:06d}"
            report_path = attempt / "report.json"
            self.event(slot["slot_id"], "reserved", report_path=str(report_path))
            result = run_suite(
                Path(slot["manifest"]["path"]), slot["manifest"]["sha256"], client=client,
                run_dir=attempt, selection="", selections={slot["step"]: slot["selection_id"]},
                episode_keys=((slot["seed"], slot["selection_id"]),),
                run_profile=conditions["run_profile"], display_mode=conditions["display_mode"],
                speed=conditions["speed"], evidence_peer=conditions["evidence_peer"],
                limits=limits, max_episodes=1,
                should_stop=lambda: (self.directory / "STOP").exists(),
            )
            row = result["episodes"][0]
            if row["status"] == "not_started":
                value = {"status": "not_started", "reason": result["stop_reason"],
                         "report": reference(report_path)}
            elif row["status"] == "finished" and not row.get("preservation_error"):
                value = result_row(row, report_path)
            else:
                value = {"status": "unknown", "run_id": row.get("run_id"),
                         "environment_ref": row.get("environment_ref"),
                         "reason": result["stop_reason"],
                         "world_mutation_offered": row.get("world_mutation_offered") is True,
                         "report": reference(report_path)}
            self.event(slot["slot_id"], "result", result=value)
            progress = {k: v for k, v in value.items()
                        if k in {"status", "run_id", "natural_result", "reason"}}
            print(json.dumps({"slot_id": slot["slot_id"], "step": slot["step"],
                              "seed_ordinal": slot["seed_ordinal"], **progress}), flush=True)
            if value["status"] != "finished" or result["status"] != "completed":
                return {"status": "stopped", "stop_reason": result["stop_reason"] or "unknown"}
            if cool:
                # The service owns credentials and exact current producer context.
                # The CLI never reads a credential file or opens a second Store.
                receipt = native_episode_command(
                    config, "cool-evidence", run_id=row["run_id"],
                    expected_instance_id=client.identity["application_instance_id"],
                    timeout_seconds=180)
                self.event(slot["slot_id"], "cache", receipt=receipt)
            count += 1
        return {"status": "completed", "new_finished": count}

    def report(self) -> dict[str, Any]:
        rows = self.slots()
        groups = []
        for character, ascension, step in sorted({(r["character"], r["ascension"], r["step"])
                                                  for r in rows}):
            selected = [r for r in rows if (r["character"], r["ascension"], r["step"])
                        == (character, ascension, step)]
            finished = [r for r in selected if r["status"] == "finished"]
            floors = [r["max_observed_floor"] for r in finished
                      if r.get("max_observed_floor") is not None]
            groups.append({"character": character, "ascension": ascension, "step": step,
                           "planned": len(selected), "scored": len(finished),
                           "win": sum(r.get("natural_result") == "win" for r in finished),
                           "loss": sum(r.get("natural_result") == "loss" for r in finished),
                           "mean_floor": sum(floors) / len(floors) if floors else None})
        return {"schema": SCHEMA, "planned": len(rows),
                "started": sum(r.get("world_mutation_offered") is True for r in rows),
                "win": sum(r.get("natural_result") == "win" for r in rows),
                "loss": sum(r.get("natural_result") == "loss" for r in rows),
                "unknown": sum(r["status"] == "unknown" for r in rows),
                "unknown_sealed": sum(bool(r.get("unknown_release")) for r in rows),
                "not_started": sum(r["status"] == "not_started" for r in rows),
                "reserved": sum(r["status"] == "reserved" for r in rows), "replaced": 0,
                "R2_original_receipts": sum(bool(r.get("stored_evidence_id")) for r in rows),
                "groups": groups, "slots": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "run", "status", "report", "seal-unknown"))
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--matrix", type=Path)
    parser.add_argument("--episodes", type=Path)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-episodes", type=int, default=200)
    args = parser.parse_args(argv)
    try:
        directory = args.ledger.resolve()
        lock = (nullcontext() if args.action in {"status", "report"} else
                instance_lock(directory.parent / (directory.name + ".lock")))
        with lock:
            config = ProjectConfig.load(args.config) if args.config else None
            if args.action == "plan":
                if config is None or args.matrix is None or args.episodes is None:
                    reject("matrix_episodes_and_config_required")
                ledger = EvaluationLedger.import_matrix(directory, args.matrix.resolve(),
                                                         args.episodes.resolve(), config)
                result = ledger.report()
            else:
                ledger = EvaluationLedger(directory)
                if args.action == "seal-unknown":
                    if config is None or args.expected is None:
                        reject("expected_identity_and_config_required")
                    result = ledger.seal_unknown(config, json.loads(_read(args.expected)))
                elif args.action == "run":
                    if config is None or not 1 <= args.max_episodes <= 200:
                        reject("config_and_bounded_new_episodes_required")
                    result = ledger.run(config, max_episodes=args.max_episodes)
                else:
                    result = ledger.report()
            if args.output:
                from .developer import atomic_json

                atomic_json(args.output, result)
            if args.action == "status" or args.output:
                result = {k: v for k, v in result.items() if k not in {"slots", "groups"}}
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get("status") != "stopped" else 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "stopped", "error": error.code
                          if isinstance(error, BoundaryError) else type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
