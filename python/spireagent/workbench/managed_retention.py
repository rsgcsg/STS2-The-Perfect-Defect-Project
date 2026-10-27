"""Declared Managed preservation intent; promotion never changes score eligibility."""
from __future__ import annotations

import hashlib
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes

SCHEMA = "spireagent/managed-retention-v1"


def retention_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    value = configuration.get("retention", {"tier": "formal", "audit_every": 100})
    if (not isinstance(value, dict) or set(value) != {"tier", "audit_every"}
            or value["tier"] not in {"formal", "diagnostic", "trajectory"}
            or type(value["audit_every"]) is not int or not 1 <= value["audit_every"] <= 10000):
        raise BoundaryError("managed_retention", "invalid_retention")
    return {"schema": SCHEMA, **value, "score_eligible": value["tier"] == "formal"}


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
