"""Immutable request artifact, owned by the existing Workbench process service."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, cast

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, decode_json, json_bytes, object_fields

SCHEMA = "spireagent/agent-run-request-v1"
OPERATIONS = frozenset({"start", "prepare-and-load", "prepare-and-takeover"})


def create(
    composition: dict[str, Any],
    operation: str,
    run_profile: str,
    budget: dict[str, Any] | None,
    target: str,
    native_context: dict[str, Any] | None = None,
    native_episode_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if operation not in OPERATIONS or target not in {"native", "managed"}:
        raise BoundaryError("agent_run_request", "unsupported_operation_or_target")
    definition = composition["semantic_definition"]
    value = {
        "schema": SCHEMA,
        "selection_id": composition["selection_id"],
        "composition_id": composition["composition_id"],
        "operation": operation,
        "run_profile": run_profile,
        "target": target,
        "admission_context": {
            "kind": "existing_application_precondition",
            "native_intent": native_context,
        },
        "task_binding": definition["task_binding"],
        "program_binding": definition["program"],
        "budget": {
            "limits": budget,
            "profile": run_profile,
            "units": {
                "submissions": "Runtime_submission_attempts",
                "policy_calls": "Runtime_program_calls_Decision_or_Next_not_NN",
                "deadline": "autonomy_wall_clock_ms",
            },
        },
        "composition": composition,
    }
    if native_episode_context is not None:
        from .native_episode_binding import checked_context

        if native_context is not None or target != "native":
            raise BoundaryError("agent_run_request", "native_episode_target_conflict")
        value["admission_context"]["native_episode"] = checked_context(native_episode_context)
    return cast(dict[str, Any], decode_json(json_bytes(value)))


def checked(value: object, effective: dict[str, Any]) -> dict[str, Any]:
    request = object_fields(value, set(effective), "agent_run_request")
    if json_bytes(request) != json_bytes(effective):
        # Python equality conflates bool/int (and int/float) in nested metadata.
        # JSON's typed canonical bytes bind the exact admitted semantic values.
        raise BoundaryError("agent_run_request", "effective_composition_or_request_changed")
    return cast(dict[str, Any], decode_json(json_bytes(effective)))


def publish(directory: Path, request: dict[str, Any], operation_id: str) -> dict[str, Any]:
    """One immutable artifact, not a journal or a registry; never gains later links."""
    raw = json_bytes({**request, "operation_id": operation_id})
    sha = hashlib.sha256(raw).hexdigest()
    parent = directory / "requests"
    if directory.is_symlink() or parent.is_symlink():
        raise BoundaryError("agent_run_request", "unsafe_request_owner")
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / (sha + ".json")
    try:
        with target.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if target.is_symlink() or target.read_bytes() != raw:
            raise BoundaryError("agent_run_request", "request_identity_collision") from None
    return {
        "path": target.relative_to(directory).as_posix(),
        "sha256": sha,
        "operation_id": operation_id,
        "composition_id": request["composition_id"],
    }


def original(directory: Path, reference: dict[str, Any], startup: dict[str, Any]) -> dict[str, Any]:
    from spireagent.policy_files import _inside

    path = _inside(directory, reference["path"])
    if path.is_symlink() or not path.is_file():
        raise BoundaryError("agent_run_request", "unsafe_original_request")
    with path.open("rb") as handle:
        raw = handle.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise BoundaryError("agent_run_request", "original_request_over_limit")
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise BoundaryError("agent_run_request", "original_request_changed")
    value = decode_json(raw)
    if not isinstance(value, dict):
        raise BoundaryError("agent_run_request", "original_request_invalid")
    admission = value.get("admission_context")
    if isinstance(admission, dict) and "native_episode" in admission:
        from .native_episode_binding import checked_context

        checked_context(admission["native_episode"])
        if admission.get("native_intent") is not None or value.get("target") != "native":
            raise BoundaryError("agent_run_request", "native_episode_target_conflict")
    composition = dict(value["composition"])
    claimed_composition = composition.pop("composition_id")
    computed_composition = hashlib.sha256(canonical_json(composition).encode()).hexdigest()
    implementation = composition["implementation"]
    manifest_sha = startup.get("agent_manifest_sha256", startup.get("policy_manifest_sha256"))
    manifest_id = startup.get("agent_manifest_id", startup.get("manifest_id"))
    if (
        value["schema"] != SCHEMA
        or value["selection_id"] != composition["selection_id"]
        or claimed_composition != computed_composition
        or value["composition_id"] != claimed_composition
        or value["composition_id"] != reference["composition_id"]
        or value["operation_id"] != reference["operation_id"]
        or implementation["manifest_sha256"] != manifest_sha
        or implementation["original_manifest_id"] != manifest_id
    ):
        raise BoundaryError("agent_run_request", "original_request_run_binding_changed")
    return value
