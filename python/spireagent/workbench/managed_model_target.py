"""Application projection of a verified Host target into Runtime configuration.

LocalEnvironmentService verifies the installed Host and private profile. Runtime
owns the public binding validator and checks the live instance again. This module
does not launch, observe, claim, reset or close an environment.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError
from spireagent.policy_files import _object_file
from spireagent.workbench.developer import atomic_json

KIND = "managed_text_v2"
TARGET_FIELDS = ("service_instance_id", "runtime_instance_id", "game_continuity_id")
PACKAGE_FIELDS = ("package", "version", "source_revision", "component_tree_revision",
                  "release_asset_sha256", "package_content_sha256")
BUILD_FIELDS = ("upstream_revision", "source_patch_sha256", "artifact_sha256",
                "artifact_mvid", "original_sts2_sha256", "runtime_sts2_sha256")


def managed_manifest(manifest: dict[str, Any]) -> bool:
    return bool(manifest.get("requirements", {}).get("environment", {}).get("kind") == KIND)


def public_binding(target: dict[str, Any]) -> dict[str, Any]:
    """Copy only public identity fields; credentials and local paths stay private."""
    try:
        if target["input_profile"] != "text-menu-v2" or any(
            not isinstance(target[key], str) or not target[key] for key in TARGET_FIELDS
        ):
            raise ValueError
        return {
            "schema": "sts2.policy-runtime/managed-environment-binding-1",
            "profile_sha256": target["profile_sha256"],
            "input_profile": target["input_profile"],
            "host_package_identity": {
                key: target["host_package_pin"][key] for key in PACKAGE_FIELDS
            },
            "candidate_build": {key: target["candidate_build"][key] for key in BUILD_FIELDS},
        }
    except (KeyError, TypeError, ValueError) as error:
        raise BoundaryError("local_model", "managed_environment_target_invalid") from error


def public_target(target: dict[str, Any]) -> dict[str, str]:
    binding = public_binding(target)
    return {"binding_sha256": hashlib.sha256(canonical_json(binding).encode()).hexdigest(),
            **{key: target[key] for key in TARGET_FIELDS}}


def runtime_arguments(target: dict[str, Any], private_root: Path) -> list[str]:
    """Seal one immutable binding for an explicit load, without retaining a token."""
    binding = public_binding(target)
    identity = public_target(target)
    attachment = target.get("client_attachment")
    if not isinstance(attachment, (str, Path)) or not Path(attachment).is_absolute():
        raise BoundaryError("local_model", "managed_environment_target_invalid")
    directory = private_root / "environment-bindings"
    if private_root.is_symlink() or directory.is_symlink():
        raise BoundaryError("local_model", "managed_binding_path_unsafe")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / (identity["binding_sha256"] + ".json")
    if path.exists() or path.is_symlink():
        if _object_file(path) != binding:
            raise BoundaryError("local_model", "managed_binding_collision")
    else:
        atomic_json(path, binding)
    return ["--managed-binding", str(path), "--managed-attachment", str(attachment),
            *[part for key in TARGET_FIELDS
              for part in ("--managed-expected-" + key.replace("_", "-"), identity[key])]]


def confirm_target(expected: object, observed: dict[str, Any]) -> None:
    if expected != public_target(observed):
        raise BoundaryError("local_model", "managed_environment_target_changed")
