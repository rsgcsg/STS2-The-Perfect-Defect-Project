"""Pure STPD Teacher composition factory; Runtime remains the wire validator.

Builtin Workbench registration and the Source3 collector call this same function.
It constructs no Host, process, controller, recording or model and reads no files.
"""

from __future__ import annotations

import copy
import sys
from pathlib import PurePath
from typing import Any

from spireagent.json_boundary import BoundaryError, decode_json, json_bytes, object_fields
from stpd.policy.native_teacher_agent import ARTIFACT_SCHEMA, MAX_MESSAGE_BYTES, teacher_agent_spec


def required_methods(execution_policy: dict[str, Any] | None = None) -> list[str]:
    return [
        "capabilities",
        "attach",
        "current",
        "read",
        "catalog",
        "submit",
        "result",
        "events",
        "await",
        "cancel_wait",
        "detach",
        "renew",
        "retain",
        "release",
        *(["current_owned"] if execution_policy else []),
    ]


def teacher_manifest(
    descriptor: dict[str, Any],
    artifact: dict[str, Any],
    capabilities: dict[str, Any],
    required_seams: list[dict[str, Any]],
    *,
    max_input_bytes: int = 8 * 1024 * 1024,
    manifest_id: str | None = None,
    execution_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct the one code-owned Teacher manifest, without validating Runtime wire."""
    object_fields(
        descriptor,
        {
            "schema",
            "agent_spec",
            "input_spec_body",
            "input_spec",
            "adapter",
            "code_files",
            "runtime_provenance",
        },
        "teacher_manifest",
    )
    spec = descriptor["agent_spec"]
    if (
        descriptor["schema"] != ARTIFACT_SCHEMA
        or not isinstance(spec, dict)
        or spec.get("learned") is not False
        or spec.get("scores") is not None
        or spec.get("model_bindings") != []
    ):
        raise BoundaryError("teacher_manifest", "explicit_program_teacher_required")
    expected = teacher_agent_spec(execution_policy=execution_policy)
    if execution_policy is not None:
        if (
            spec.get("execution_policy") != expected["execution_policy"]
            or spec.get("teacher") != expected["teacher"]
            or descriptor["adapter"].get("version") != expected["version"]
            or spec.get("version") != expected["version"]
        ):
            raise BoundaryError("teacher_manifest", "teacher_execution_policy_mismatch")
    elif "execution_policy" in spec:
        raise BoundaryError("teacher_manifest", "undeclared_teacher_execution_policy")
    object_fields(artifact, {"id", "path", "sha256"}, "teacher_manifest.artifact")
    if not isinstance(artifact["path"], str) or not PurePath(artifact["path"]).is_absolute():
        raise BoundaryError("teacher_manifest", "owned_teacher_artifact_path_required")
    if type(max_input_bytes) is not int or not 1024 <= max_input_bytes <= 64 * 1024 * 1024:
        raise BoundaryError("teacher_manifest", "finite_capture_limit_required")
    c = capabilities
    return copy.deepcopy(
        {
            "schema": "sts2.policy-runtime/agent-manifest-1",
            "manifest_id": manifest_id or "native-program-teacher-" + artifact["sha256"][:16],
            "agent": {
                "id": spec["id"],
                "version": spec["version"],
                "provider": "stpd",
                "architecture": "explicit_native_public_program_teacher",
            },
            "adapter": descriptor["adapter"],
            "artifact": artifact,
            "input": {
                "profile": "native-logical-v1",
                "input_spec": descriptor["input_spec"],
                "projection": {"id": "stpd-native-program-teacher-current-v1", "version": "1.0.0"},
                "state_format_version": "stpd/native-program-teacher-state-v1",
                "state_recovery": {"mode": "none", "max_state_bytes": 0, "model_bindings": []},
                "history_mode": "sampled_current",
                "consumption_mode": "once_per_occurrence",
                "gap_policy": "handoff",
                "attachment": {
                    "eager_scope": [],
                    "required_seams": required_seams,
                    "delivery_mode": "scoped",
                },
            },
            "requirements": {
                "connector_protocol_version": c["protocol_version"],
                "environment": {
                    "host_kind": c["host"]["host_kind"],
                    "connector_version": c["host"]["version"],
                    "connector_source_revision": c["host"]["implementation"]["source_revision"],
                    "connector_artifact_sha256": c["host"]["implementation"]["artifact_sha256"],
                    "connector_module_version_id": c["host"]["implementation"]["module_version_id"],
                    "modset_status": c["game"]["modset"]["status"],
                    "modset_fingerprint": c["game"]["modset"]["fingerprint"],
                    "loaded_mod_ids": c["game"]["modset"]["loaded_mod_ids"],
                },
                "required_methods": required_methods(execution_policy),
            },
            "support": {
                "game_versions": [c["game"]["version"]],
                "game_commits": [c["game"]["commit"]],
                "interaction_kinds": ["*"],
                "action_verbs": ["*"],
            },
            "limits": {
                "max_message_bytes": MAX_MESSAGE_BYTES,
                "max_acquisitions": 256,
                "max_retained_acquisition_bytes": 256 * 1024 * 1024,
                "max_pending_queries": 8,
                "max_queries_per_turn": 8,
                "max_query_bytes_per_turn": 128 * 1024 * 1024,
                "max_capture_bytes": min(max_input_bytes, 8 * 1024 * 1024),
                "max_catalog_actions": 16384,
                "max_cancelled_ids": 256,
                "agent_timeout_ms": 30_000,
            },
            **({"execution_policy": execution_policy} if execution_policy else {}),
            "claims": {
                "catalog_filtered": False,
                "creates_action_authority": False,
                "creates_native_operands": False,
                "human_origin": False,
                "causal_successor": False,
            },
        }
    )


def main() -> int:
    """Bounded metadata-only bridge for the existing JS collector."""
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise BoundaryError("teacher_manifest", "metadata_over_limit")
        request = object_fields(
            decode_json(raw),
            {
                "descriptor",
                "artifact",
                "capabilities",
                "required_seams",
                "max_input_bytes",
                "manifest_id",
                "execution_policy",
            },
            "teacher_manifest.request",
        )
        result = teacher_manifest(
            request["descriptor"],
            request["artifact"],
            request["capabilities"],
            request["required_seams"],
            max_input_bytes=request["max_input_bytes"],
            manifest_id=request["manifest_id"],
            execution_policy=request["execution_policy"],
        )
        sys.stdout.buffer.write(json_bytes(result))
        return 0
    except (BoundaryError, KeyError, TypeError, ValueError) as error:
        sys.stderr.buffer.write(
            json_bytes(
                {
                    "error": error.code
                    if isinstance(error, BoundaryError)
                    else "teacher_manifest_input_invalid"
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
