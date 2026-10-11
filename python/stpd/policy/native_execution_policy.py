"""Closed policies 1/2/3 over the unchanged original outcome/Next grammar.

The disposable v1 validation sentinel never leaves this decoder. Runtime owns
the selected policy's deadlines and counters; neither model inputs nor W gains
operational feedback from accepting a declaration.
"""

from __future__ import annotations

import copy
from typing import Any

from spireagent.json_boundary import BoundaryError, object_fields

from . import native_operational_outcome as v1

CONTINUOUS_STRICT_POLICY_SCHEMA = "sts2.policy-runtime/agent-execution-policy-2"
KNOWN_STALE_WINDOW_POLICY_SCHEMA = "sts2.policy-runtime/agent-execution-policy-3"


def known_stale_window_policy() -> dict[str, Any]:
    return {
        "schema": KNOWN_STALE_WINDOW_POLICY_SCHEMA,
        "current_mode": "reader_owned_v1",
        "known_stale": "fresh_changed_current_v1",
        "operational_outcome": "known_not_started_v1",
        "known_stale_recovery_window_ms": 10000,
    }


def checked_execution_policy(value: object) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("schema") == CONTINUOUS_STRICT_POLICY_SCHEMA:
        fixed = {
            "schema": CONTINUOUS_STRICT_POLICY_SCHEMA,
            "current_mode": "reader_owned_v1",
            "known_stale": "fresh_changed_current_v1",
            "operational_outcome": "known_not_started_v1",
            "max_consecutive_known_stale_rejections": 3,
        }
        policy = object_fields(value, set(fixed), "operational.policy")
        if (
            any(policy[key] != fixed[key] for key in fixed)
            or type(policy["max_consecutive_known_stale_rejections"]) is not int
        ):
            raise BoundaryError("operational", "unsupported_execution_policy")
        return copy.deepcopy(policy)
    if isinstance(value, dict) and value.get("schema") == KNOWN_STALE_WINDOW_POLICY_SCHEMA:
        fixed = known_stale_window_policy()
        policy = object_fields(value, set(fixed), "operational.policy")
        if (
            any(policy[key] != fixed[key] for key in fixed)
            or type(policy["known_stale_recovery_window_ms"]) is not int
        ):
            raise BoundaryError("operational", "unsupported_execution_policy")
        return copy.deepcopy(policy)
    return v1.checked_execution_policy(value)


def manifest_execution_policy(manifest: dict[str, Any]) -> dict[str, Any] | None:
    if "execution_policy" not in manifest:
        return v1.manifest_execution_policy(manifest)
    policy = checked_execution_policy(manifest["execution_policy"])
    validation_only = copy.deepcopy(manifest)
    validation_only["execution_policy"] = v1.owned_current_known_stale_policy()
    v1.manifest_execution_policy(validation_only)
    return policy


def checked_next_input(
    value: object,
    *,
    execution_policy: dict[str, Any] | None,
    intention: dict[str, Any] | None = None,
    previous_outcome: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    policy = checked_execution_policy(execution_policy) if execution_policy is not None else None
    return v1.checked_next_input(
        value,
        execution_policy=v1.owned_current_known_stale_policy() if policy is not None else None,
        intention=intention,
        previous_outcome=previous_outcome,
    )
