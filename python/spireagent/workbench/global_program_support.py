"""Closed installation of the code-owned GLOBAL program, with no learned model."""
from __future__ import annotations
import hashlib
from pathlib import Path
from spireagent.json_boundary import BoundaryError, object_fields
from spireagent.policy_files import _object_file
from .native_agent_support import _bound_path
from .global_program_agent import ADAPTER, descriptor, adapter_identity
PROFILE = "p1-global-r1"
CONFIG_SCHEMA = "spireagent/global-public-program-installation-1"


def validate(root, config_path, manifest_path, *, binding_root=None):
    owner = (binding_root or root).resolve()
    config = object_fields(_object_file(_bound_path(owner, config_path)),
                           {"schema", "artifact_path", "artifact_sha256"}, "global_program")
    raw, original = original_artifact(owner, config, _object_file(_bound_path(owner, manifest_path)))
    manifest = _object_file(manifest_path)
    if (config["schema"] != CONFIG_SCHEMA or original != descriptor() or manifest.get("adapter") != adapter_identity()
            or manifest.get("input", {}).get("profile") != PROFILE
            or manifest.get("input", {}).get("state_recovery", {}).get("model_bindings") != []
            or manifest.get("agent") != {"id": "global-public-program", "version": "1", "provider": "project-program", "architecture": "model-less-program"}):
        raise BoundaryError("global_program", "code_owned_program_identity_changed")
    return config, manifest


def arguments(entry):
    return ["-m", "spireagent.workbench.global_program_agent", "--manifest", entry["manifest"]]


def original_artifact(owner, config, manifest):
    path = _bound_path(owner, Path(config["artifact_path"]))
    if path.stat().st_size > 1024 * 1024:
        raise BoundaryError("global_program", "artifact_over_limit")
    raw = path.read_bytes()
    if (hashlib.sha256(raw).hexdigest() != config["artifact_sha256"]
            or manifest.get("artifact", {}).get("path") != str(path)
            or manifest.get("artifact", {}).get("sha256") != config["artifact_sha256"]):
        raise BoundaryError("global_program", "original_artifact_changed")
    return raw, _object_file(path)


def metadata(owner, config, manifest):
    return original_artifact(owner, config, manifest)[1]


def inspect(root, entry, manifest, policy_config, *, binding_root=None):
    try:
        owner = binding_root or root
        config, checked = validate(root, owner / entry["config"], owner / entry["manifest"], binding_root=owner)
        if config != policy_config or checked != manifest:
            raise BoundaryError("global_program", "metadata_changed")
        return {"policy_identity": {"status": "pass"}, "backend": {"status": "pass", "code": "not_required_program_agent"}}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"policy_identity": {"status": "blocked", "code": error.code if isinstance(error, BoundaryError) else "global_program_unavailable"}}
