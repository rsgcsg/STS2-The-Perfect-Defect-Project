"""Additive continuous strict policy glue outside the frozen learned v1 closure.

The v1 outcome checker enforces policy validity/presence and the unchanged Next,
manifest-binding and result grammars; it does not enforce refusal thresholds.
For v2 only, a disposable v1 policy sentinel enables that same grammar checker.
The sentinel is never published or returned as policy. Only a disposable
validation copy receives it; declared manifests, results, state and counters
remain unchanged. Runtime/Evidence own the declared policy2 thresholds.
"""

from __future__ import annotations

import copy
from typing import Any

from spireagent.json_boundary import BoundaryError, object_fields
from stpd.policy import native_operational_outcome as v1

CONTINUOUS_STRICT_POLICY_SCHEMA = "sts2.policy-runtime/agent-execution-policy-2"


def progress_aware_strict_policy() -> dict[str, Any]:
    return {
        "schema": CONTINUOUS_STRICT_POLICY_SCHEMA,
        "current_mode": "reader_owned_v1",
        "known_stale": "fresh_changed_current_v1",
        "operational_outcome": "known_not_started_v1",
        "max_consecutive_known_stale_rejections": 3,
    }


def checked_execution_policy(value: object) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("schema") == CONTINUOUS_STRICT_POLICY_SCHEMA:
        fixed = progress_aware_strict_policy()
        policy = object_fields(value, set(fixed), "operational.policy")
        if (
            any(policy[key] != fixed[key] for key in fixed)
            or type(policy["max_consecutive_known_stale_rejections"]) is not int
        ):
            raise BoundaryError("operational", "unsupported_execution_policy")
        return copy.deepcopy(policy)
    return v1.checked_execution_policy(value)


def manifest_execution_policy(manifest: dict[str, Any]) -> dict[str, Any] | None:
    if "execution_policy" not in manifest:
        return v1.manifest_execution_policy(manifest)
    policy = checked_execution_policy(manifest["execution_policy"])
    if policy["schema"] != CONTINUOUS_STRICT_POLICY_SCHEMA:
        return v1.manifest_execution_policy(manifest)
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
    validation_policy = (
        v1.owned_current_known_stale_policy()
        if policy is not None and policy["schema"] == CONTINUOUS_STRICT_POLICY_SCHEMA
        else policy
    )
    return v1.checked_next_input(
        value,
        execution_policy=validation_policy,
        intention=intention,
        previous_outcome=previous_outcome,
    )
