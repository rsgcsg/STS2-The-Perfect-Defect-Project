"""Closed Workbench installation support for the model-less native Teacher."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, object_fields
from spireagent.policy_files import _object_file
from spireagent.workbench.native_agent_support import _bound_path

ADAPTER = "stpd-native-public-program-teacher"
PROFILE = "native-logical-v1"
CONFIG_SCHEMA = "spireagent/native-teacher-installation-v1"


def validate(
    root: Path, config_path: Path, manifest_path: Path, *, binding_root: Path | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    from stpd.policy.native_teacher_agent import NativeTeacherAgent

    owner = (binding_root or root).resolve()
    config_path = _bound_path(owner, config_path)
    manifest_path = _bound_path(owner, manifest_path)
    config = object_fields(
        _object_file(config_path),
        {"schema", "artifact_path", "artifact_sha256"},
        "native_teacher_support",
    )
    if config["schema"] != CONFIG_SCHEMA:
        raise BoundaryError("native_teacher_support", "unsupported_installation")
    artifact_path = _bound_path(owner, Path(config["artifact_path"]))
    descriptor = _object_file(artifact_path)
    manifest = _object_file(manifest_path)
    if (
        hashlib.sha256(artifact_path.read_bytes()).hexdigest() != config["artifact_sha256"]
        or manifest.get("artifact", {}).get("path") != str(artifact_path)
        or manifest.get("artifact", {}).get("sha256") != config["artifact_sha256"]
        or manifest.get("adapter", {}).get("id") != ADAPTER
        or manifest.get("agent", {}).get("id") != descriptor.get("agent_spec", {}).get("id")
        or manifest.get("agent", {}).get("version")
        != descriptor.get("agent_spec", {}).get("version")
        or descriptor.get("agent_spec", {}).get("model_bindings") != []
    ):
        raise BoundaryError("native_teacher_support", "program_artifact_binding_changed")
    NativeTeacherAgent(manifest_path)  # Pure script/code binding; no NN/backend/Host.
    return config, manifest


def arguments(entry: dict[str, Any]) -> list[str]:
    return ["-m", "stpd.policy.native_teacher_agent", "--manifest", entry["manifest"]]


def inspect(
    root: Path,
    entry: dict[str, Any],
    manifest: dict[str, Any],
    policy_config: dict[str, Any],
    *,
    binding_root: Path | None = None,
) -> dict[str, dict[str, str]]:
    try:
        owner = binding_root or root
        config, checked = validate(
            root, owner / entry["config"], owner / entry["manifest"], binding_root=owner
        )
        if config != policy_config or checked != manifest:
            raise BoundaryError("native_teacher_support", "metadata_changed")
        return {
            "policy_identity": {"status": "pass"},
            "backend": {"status": "pass", "code": "not_required_program_agent"},
        }
    except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
        return {
            "policy_identity": {
                "status": "blocked",
                "code": (
                    error.code
                    if isinstance(error, BoundaryError)
                    else "program_metadata_unavailable"
                ),
            }
        }


def installation_config(artifact_path: Path, artifact_sha256: str) -> dict[str, Any]:
    return {
        "schema": CONFIG_SCHEMA,
        "artifact_path": str(artifact_path),
        "artifact_sha256": artifact_sha256,
    }


def original_artifact(
    owner: Path, config: dict[str, Any], manifest: dict[str, Any]
) -> tuple[bytes, dict[str, Any]]:
    from spireagent.json_boundary import decode_json

    path = _bound_path(owner, Path(config["artifact_path"]))
    with path.open("rb") as handle:
        raw = handle.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise BoundaryError("native_teacher_support", "original_program_artifact_over_limit")
    if hashlib.sha256(raw).hexdigest() != manifest["artifact"]["sha256"]:
        raise BoundaryError("native_teacher_support", "original_program_artifact_changed")
    value = decode_json(raw)
    if not isinstance(value, dict):
        raise BoundaryError("native_teacher_support", "original_program_artifact_invalid")
    return raw, value


def metadata(owner: Path, config: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    return original_artifact(owner, config, manifest)[1]
