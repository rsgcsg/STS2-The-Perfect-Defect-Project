"""Pure, declared operational notifications outside model inputs and memory.

The child checks its emitted intention and the full result grammar. Runtime and
Evidence alone can join the first actual dispatch request/client/controller.
"""

from __future__ import annotations

import copy
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes, object_fields

POLICY_SCHEMA = "sts2.policy-runtime/agent-execution-policy-1"
OUTCOME_SCHEMA = "sts2.policy-runtime/known-not-started-outcome-1"
NEXT_FIELDS = {
    "continuity_token", "consumption_id", "state_version",
    "basis_acquisition_id", "received_cursor",
}


def owned_current_known_stale_policy() -> dict[str, Any]:
    """An explicit fresh 8/3 policy; calling this does not authorize a run."""
    return {
        "schema": POLICY_SCHEMA,
        "current_mode": "reader_owned_v1",
        "known_stale": "fresh_changed_current_v1",
        "operational_outcome": "known_not_started_v1",
        "max_known_stale_rejections": 8,
        "max_consecutive_known_stale_rejections": 3,
    }


def checked_execution_policy(value: object) -> dict[str, Any]:
    policy = object_fields(value, set(owned_current_known_stale_policy()), "operational.policy")
    fixed = owned_current_known_stale_policy()
    for key in ("schema", "current_mode", "known_stale", "operational_outcome"):
        if policy[key] != fixed[key]:
            raise BoundaryError("operational", "unsupported_execution_policy")
    total, consecutive = policy["max_known_stale_rejections"], policy[
        "max_consecutive_known_stale_rejections"
    ]
    if (type(total) is not int or not 1 <= total <= 16 or type(consecutive) is not int
            or not 1 <= consecutive <= min(total, 4)):
        raise BoundaryError("operational", "execution_policy_bounds")
    return copy.deepcopy(policy)


def manifest_execution_policy(manifest: dict[str, Any]) -> dict[str, Any] | None:
    if "execution_policy" not in manifest:
        return None
    policy = checked_execution_policy(manifest["execution_policy"])
    input_spec = manifest.get("input", {})
    if not isinstance(input_spec, dict) or not isinstance(manifest.get("requirements"), dict):
        raise BoundaryError("operational", "execution_policy_input_binding")
    attachment = input_spec.get("attachment", {})
    if not isinstance(attachment, dict):
        raise BoundaryError("operational", "execution_policy_input_binding")
    methods = manifest.get("requirements", {}).get("required_methods")
    if (input_spec.get("history_mode") != "sampled_current"
            or input_spec.get("consumption_mode") != "once_per_occurrence"
            or json_bytes(input_spec.get("state_recovery"))
            != json_bytes({"mode": "none", "max_state_bytes": 0, "model_bindings": []})
            or attachment.get("eager_scope") != []
            or attachment.get("delivery_mode") != "scoped"
            or not isinstance(methods, list)
            or any(not isinstance(method, str) or not method for method in methods)
            or len(set(methods)) != len(methods)
            or not {"current", "current_owned", "retain", "release"} <= set(methods)):
        raise BoundaryError("operational", "execution_policy_input_binding")
    return policy


def _text(
    value: object, *, maximum: int = 65536, identifier: bool = False, utf16: bool = False
) -> None:
    if not isinstance(value, str):
        raise BoundaryError("operational", "result_text_required")
    try:
        length = len(value.encode("utf-16-le")) // 2 if utf16 else len(value.encode())
    except UnicodeError as error:
        raise BoundaryError("operational", "result_scalar_required") from error
    if length > maximum or identifier and not value:
        raise BoundaryError("operational", "result_text_bounds")


def _integer(value: object, *, positive: bool = False) -> None:
    if type(value) is not int or not (1 if positive else 0) <= value <= 2**53 - 1:
        raise BoundaryError("operational", "result_integer_required")


def _known_stale_result(value: object) -> dict[str, Any]:
    result = object_fields(value, {
        "protocol_version", "schema", "input_profile", "request_id", "snapshot_id",
        "action", "delivery", "execution", "effect", "cancel", "stages", "reason",
        "retry", "observed_frame", "attribution",
    }, "operational.result")
    for key in ("request_id", "snapshot_id"):
        _text(result[key], maximum=128, identifier=True, utf16=True)
    if (result["protocol_version"] != "1.0.0"
            or result["schema"] != "sts2.player-environment/native-logical-result-1"
            or result["input_profile"] != "native-logical-v1"
            or result["delivery"] != "not_started"
            or result["reason"] != "stale_snapshot_or_binding"
            or result["action"] is not None or result["stages"] != []
            or result["retry"] != "never_automatic"
            or result["execution"] not in ("not_started", "native_accepted",
                                           "native_rejected", "unknown")
            or result["effect"] not in ("not_observed", "pending", "observed", "unknown")
            or result["cancel"] not in ("not_requested", "cancelled_before_start",
                                        "too_late", "unknown")):
        raise BoundaryError("operational", "closed_known_stale_result_required")
    attribution = object_fields(result["attribution"], {
        "runtime_instance_id", "client_session_id", "client_instance_id", "product_id",
        "product_name", "product_version", "controller_lease_id", "controller_generation",
    }, "operational.attribution")
    for key in ("runtime_instance_id", "client_session_id", "controller_lease_id"):
        _text(attribution[key], maximum=128, identifier=True, utf16=True)
    for key in ("client_instance_id", "product_id", "product_name", "product_version"):
        _text(attribution[key])
    _integer(attribution["controller_generation"], positive=True)
    if result["observed_frame"] is not None:
        context = object_fields(result["observed_frame"], {
            "schema", "input_profile", "observation_ref", "capture_ref",
            "game_continuity_id", "stream_generation", "publication_cursor",
        }, "operational.context")
        if (context["schema"] != "sts2.player-environment/native-logical-context-1"
                or context["input_profile"] != "native-logical-v1"):
            raise BoundaryError("operational", "result_context_profile")
        for key in ("observation_ref", "capture_ref", "stream_generation"):
            _text(context[key], maximum=128, identifier=True, utf16=True)
        if context["game_continuity_id"] is not None:
            _text(context["game_continuity_id"])
        if context["publication_cursor"] is not None:
            _text(context["publication_cursor"], maximum=1024, identifier=True, utf16=True)
    return copy.deepcopy(result)


def emitted_intention(
    *, acquisition_id: str, consumption_id: str, state_version: int,
    observation: dict[str, Any], action: dict[str, Any],
) -> dict[str, Any]:
    """Only private operational state; no action feedback is encoded for a model."""
    return {
        "basis_acquisition_id": acquisition_id,
        "consumption_id": consumption_id,
        "state_version": state_version,
        "action_id": action["action_id"],
        "snapshot_id": observation["snapshot_id"],
        "runtime_instance_id": observation["session"]["runtime_instance_id"],
        "action": copy.deepcopy(action),
    }


def checked_next_input(
    value: object, *, execution_policy: dict[str, Any] | None,
    intention: dict[str, Any] | None = None, previous_outcome: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return the original five fields and separately validated control metadata."""
    policy = checked_execution_policy(execution_policy) if execution_policy is not None else None
    next_input = object_fields(value, NEXT_FIELDS | (
        {"operational_outcome"} if policy is not None else set()
    ), "operational.next")
    _text(next_input["continuity_token"], maximum=256, identifier=True)
    _integer(next_input["state_version"])
    for key in ("consumption_id", "basis_acquisition_id", "received_cursor"):
        if next_input[key] is not None:
            _text(next_input[key], maximum=1024 if key == "received_cursor" else 256,
                  identifier=True)
    if (next_input["state_version"] == 0) != (next_input["consumption_id"] is None):
        raise BoundaryError("operational", "next_state_watermark")
    outcome = next_input["operational_outcome"] if policy is not None else None
    if outcome is not None:
        outcome = object_fields(outcome, {
            "schema", "basis_acquisition_id", "action_id", "consumption_id",
            "state_version", "result",
        }, "operational.outcome")
        for key in ("basis_acquisition_id", "action_id", "consumption_id"):
            _text(outcome[key], maximum=65536 if key == "action_id" else 256, identifier=True)
        _integer(outcome["state_version"], positive=True)
        result = _known_stale_result(outcome["result"])
        if (outcome["schema"] != OUTCOME_SCHEMA or intention is None
                or any(outcome[key] != intention[key] for key in (
                    "basis_acquisition_id", "action_id", "consumption_id", "state_version"
                ))
                or any(outcome[key] != next_input[key] for key in (
                    "basis_acquisition_id", "consumption_id", "state_version"
                ))
                or result["snapshot_id"] != intention["snapshot_id"]
                or result["attribution"]["runtime_instance_id"]
                != intention["runtime_instance_id"]):
            raise BoundaryError("operational", "last_emitted_intention_binding")
        if (previous_outcome is not None
                and previous_outcome["result"]["request_id"] == result["request_id"]
                and json_bytes(previous_outcome) != json_bytes(outcome)):
            raise BoundaryError("operational", "repeated_result_body_mismatch")
        if (previous_outcome is not None and all(
            previous_outcome[key] == outcome[key]
            for key in ("basis_acquisition_id", "action_id", "consumption_id", "state_version")
        ) and previous_outcome["result"]["request_id"] != result["request_id"]):
            raise BoundaryError("operational", "multiple_results_for_one_intention")
        outcome = copy.deepcopy(outcome)
    return {key: copy.deepcopy(next_input[key]) for key in NEXT_FIELDS}, outcome
