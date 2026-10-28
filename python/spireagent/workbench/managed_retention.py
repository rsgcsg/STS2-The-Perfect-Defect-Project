"""Declared Managed preservation intent; promotion never changes score eligibility."""
from __future__ import annotations

import hashlib
import math
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes

SCHEMA = "spireagent/managed-retention-v1"


def retention_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    value = configuration.get("retention", {"tier": "formal", "audit_every": 100})
    if (not isinstance(value, dict)
            or set(value) not in ({"tier", "audit_every"}, {"tier", "audit_every", "purpose"})
            or value["tier"] not in {"formal", "diagnostic", "trajectory"}
            or type(value["audit_every"]) is not int or not 1 <= value["audit_every"] <= 10000
            or "purpose" in value and value["purpose"] not in {"training", "evaluation"}
            or value.get("purpose") == "evaluation" and value["tier"] == "diagnostic"
            or value.get("purpose") == "evaluation" and value["tier"] == "trajectory"
            and value["audit_every"] != 50):
        raise BoundaryError("managed_retention", "invalid_retention")
    eligible = value["tier"] == "formal" or value.get("purpose") == "evaluation"
    return {"schema": ("spireagent/managed-retention-v2" if "purpose" in value else SCHEMA),
            **value, "score_eligible": eligible}


def select_formal_audits(slots: list[dict[str, Any]], *, salt: str,
                         audit_every: int) -> dict[str, Any]:
    """Freeze outcome-independent top-k over complete slot/config identities."""
    count = max(1, math.ceil(len(slots) / audit_every)) if slots else 0
    ranked = sorted(slots, key=lambda slot: (
        hashlib.sha256(json_bytes({"salt": salt, "slot_id": slot["slot_id"]})).hexdigest(),
        slot["slot_id"]))
    return {"schema": "spireagent/managed-formal-audits-v1", "salt": salt,
            "planned": len(slots), "audit_every": audit_every, "count": count,
            "selected_slot_ids": [slot["slot_id"] for slot in ranked[:count]]}


def preservation_route(retention: dict[str, Any], row: dict[str, Any]) -> str:
    """Loss is a failed diagnostic; natural RL loss is ordinary trajectory data."""
    tier = retention["tier"]
    terminal = (row.get("agent_task_outcome") or {}).get("terminal_observation") or {}
    abnormal = row.get("execution_status") != "finished" or not row.get("cleanup_accepted")
    if tier == "trajectory" and abnormal:
        return "trajectory_and_full_peer"
    if tier == "formal" or abnormal or tier == "diagnostic" and terminal.get("result") != "win":
        return "full_peer"
    if tier == "diagnostic":
        return "full_local"
    if "formal_audit_selected" in row:
        return "trajectory_and_full_peer" if row["formal_audit_selected"] else "trajectory"
    key = json_bytes({"seed": row["seed"], "selection": row["selection_id"],
                      "manifest": row["manifest_sha256"]})
    audit = int.from_bytes(hashlib.sha256(key).digest(), "big") % retention["audit_every"] == 0
    return "trajectory_and_full_peer" if audit else "trajectory"


def pipeline_configuration(configuration: dict[str, Any]) -> dict[str, int]:
    value = configuration.get("pipeline", {"save_workers": 2, "pending_limit": 8})
    if (not isinstance(value, dict) or set(value) != {"save_workers", "pending_limit"}
            or any(type(v) is not int for v in value.values())
            or not 1 <= value["save_workers"] <= 4
            or not value["save_workers"] <= value["pending_limit"] <= 16):
        raise BoundaryError("managed_retention", "invalid_pipeline")
    return dict(value)
