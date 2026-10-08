"""Trusted S0 structured-package binding; downloaded artifacts never select code.

Only the existing text-v2 I/F-off projection and port 2 are admitted. Callers
supply exact runtime requirements and support facts, never a latest-version guess.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes, object_fields
from spireagent.package_identity import file_sha256
from spireagent.policy_files import _inside, _object_file

from .token_policy_installation import _manifest_artifact_path

CONFIG_SCHEMA = "stpd/structured-policy-config-v1"
CODE_SCOPE = "python-structured-owner-source-and-lock-v1"
PROTOCOL = "sts2.policy-runtime/decision-only-ndjson-2"
ADAPTER = "stpd-s0-structured-adapter"
CLAIMS = {"full_run": False, "selector": False, "catalog_filtered": False,
          "creates_action_authority": False, "creates_native_operands": False}


def code_digest(root: Path) -> str:
    from .policy.structured_export import code_digest as trusted_digest

    return trusted_digest(root)


def _bound_path(root: Path, path: Path) -> Path:
    relative = path.relative_to(root)
    checked = _inside(root, relative.as_posix())
    current = root
    for segment in relative.parts:
        current = current / segment
        if current.is_symlink():
            raise BoundaryError("structured_policy", "unsafe_binding_path")
    return checked


def bind_structured_export(
    root: Path, export_path: Path, config_path: Path, manifest_path: Path, *,
    manifest_id: str, policy: dict[str, Any], requirements: dict[str, Any],
    support: dict[str, Any], binding_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind checked closed bytes to caller-owned exact environment facts."""
    from .policy.structured_export import MANIFEST_NAME, PROJECTION, load_structured_package

    root = root.resolve()
    binding_root = (binding_root or root).resolve()
    # Do not erase a package symlink before the closed-package verifier sees it.
    package, model = load_structured_package(export_path)
    del model
    export_path = export_path.resolve()
    config_path = _bound_path(binding_root, config_path)
    manifest_path = _bound_path(binding_root, manifest_path)
    if config_path == manifest_path or config_path.exists() or manifest_path.exists():
        raise BoundaryError("structured_policy", "invalid_binding_destination")
    config = {"schema": CONFIG_SCHEMA, "export_path": str(export_path),
              "export_manifest_sha256": hashlib.sha256(json_bytes(package)).hexdigest(),
              "model_id": package["model_id"]}
    manifest = {
        "schema": "sts2.policy-runtime/policy-manifest-1", "manifest_id": manifest_id,
        "policy": policy,
        "adapter": {"id": ADAPTER, "version": "1.0.0", "protocol": PROTOCOL,
                    "code_sha256": code_digest(root)},
        "artifact": {"id": config["model_id"], "path": _manifest_artifact_path(
            export_path / MANIFEST_NAME, manifest_path.parent),
            "sha256": config["export_manifest_sha256"]},
        "representation": {"id": PROJECTION["id"], "version": PROJECTION["version"],
                           "input_schema": PROJECTION["source_schema"]},
        "requirements": requirements, "support": support,
        "adapter_config": {"stage1a": {"code_digest_scope": CODE_SCOPE, "config": {
            "path": config_path.relative_to(binding_root).as_posix(),
            "sha256": hashlib.sha256(json_bytes(config)).hexdigest(), "schema": CONFIG_SCHEMA}}},
        "claims": CLAIMS,
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        config_path.write_bytes(json_bytes(config))
        manifest_path.write_bytes(json_bytes(manifest))
        validate(root, config_path, manifest_path, binding_root=binding_root)
    except Exception:
        config_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise
    return config, manifest


def validate(root: Path, config_path: Path, manifest_path: Path, *,
             binding_root: Path | None = None) -> tuple[dict, dict]:
    from .policy.structured_export import MANIFEST_NAME
    from .policy.structured_port import StructuredPolicyAdapter

    binding_root = (binding_root or root).resolve()
    config_path = _bound_path(binding_root, config_path)
    manifest_path = _bound_path(binding_root, manifest_path)
    config = object_fields(_object_file(config_path), {
        "schema", "export_path", "export_manifest_sha256", "model_id",
    }, "structured_policy.config")
    if config["schema"] != CONFIG_SCHEMA or not isinstance(config["export_path"], str):
        raise BoundaryError("structured_policy", "unsupported_config")
    export = Path(config["export_path"])
    if not export.is_absolute():
        raise BoundaryError("structured_policy", "absolute_export_path_required")
    manifest = _object_file(manifest_path)
    pin = {"code_digest_scope": CODE_SCOPE, "config": {
        "path": config_path.relative_to(binding_root).as_posix(),
        "sha256": file_sha256(config_path), "schema": CONFIG_SCHEMA}}
    if (manifest.get("adapter_config") != {"stage1a": pin}
            or manifest.get("claims") != CLAIMS
            or manifest.get("adapter", {}).get("code_sha256") != code_digest(root)
            or manifest.get("artifact") != {
                "id": config["model_id"], "path": _manifest_artifact_path(
                    export / MANIFEST_NAME, manifest_path.parent),
                "sha256": config["export_manifest_sha256"]}):
        raise BoundaryError("structured_policy", "trusted_policy_identity_drift")
    adapter = StructuredPolicyAdapter(export, manifest_path)
    adapter.close()
    return config, manifest


def arguments(entry: dict[str, Any]) -> list[str]:
    """The application supplies resolved trusted entry paths after validation."""
    config = object_fields(_object_file(Path(entry["config"])), {
        "schema", "export_path", "export_manifest_sha256", "model_id",
    }, "structured_policy.config")
    if config["schema"] != CONFIG_SCHEMA or not Path(config["export_path"]).is_absolute():
        raise BoundaryError("structured_policy", "unsupported_config")
    manifest_path = Path(entry["manifest"])
    manifest = _object_file(manifest_path)
    pin = manifest.get("adapter_config", {}).get("stage1a", {}).get("config", {})
    artifact = manifest.get("artifact", {})
    if (pin.get("schema") != CONFIG_SCHEMA
            or pin.get("sha256") != file_sha256(Path(entry["config"]))
            or artifact.get("id") != config["model_id"]
            or artifact.get("sha256") != config["export_manifest_sha256"]
            or not isinstance(artifact.get("path"), str)
            or (manifest_path.parent / artifact["path"]).resolve()
            != (Path(config["export_path"]) / "model.json").resolve()):
        raise BoundaryError("structured_policy", "export_identity_drift")
    return ["-m", "stpd.policy.structured_port", "--package", config["export_path"],
            "--manifest", entry["manifest"]]


def inspect(root: Path, entry: dict[str, Any], manifest: dict[str, Any],
            policy_config: dict[str, Any], *,
            binding_root: Path | None = None) -> dict[str, dict[str, str]]:
    try:
        owner = binding_root or root
        config, checked = validate(root, owner / entry["config"],
                                   owner / entry["manifest"], binding_root=owner)
        if config != policy_config or checked != manifest:
            raise BoundaryError("structured_policy", "metadata_changed")
        return {"policy_identity": {"status": "pass"},
                "backend": {"status": "pass", "code": "cpu"}}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError) as error:
        return {"policy_identity": {"status": "blocked", "code": (
            error.code if isinstance(error, BoundaryError) else
            "local_models_extra_required" if isinstance(error, ImportError) else
            "metadata_unavailable")}}
