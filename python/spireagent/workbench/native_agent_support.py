"""Static application composition for the domain-owned native Agent package."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, digest, json_bytes, object_fields
from spireagent.package_identity import file_sha256
from spireagent.policy_files import _inside, _object_file

ADAPTER = "stpd-native-structured-m2-agent"
PROFILE = "native-logical-v1"
CONFIG_SCHEMA = "stpd/native-agent-config-v1"
PUBLICATION_PROFILE_ID = "native-logical-publication-profile-v1"
PUBLICATION_PROFILE_SHA256 = "c060cfd354c6702e10711e6329848836ec133f2117841750e317b6ab27b244cf"
CONFIG_FIELDS = {"schema", "export_path", "artifact_id", "package_model_id",
                 "package_manifest_sha256", "weights_sha256", "input_spec",
                 "agent_spec", "state_format_version", "code_identity", "publication_profile"}


def code_digest(root: Path) -> str:
    from stpd.native_code_scope import native_code_sha256

    return native_code_sha256(root)


def _bound_path(owner: Path, path: Path) -> Path:
    relative = path.relative_to(owner)
    checked = _inside(owner, relative.as_posix())
    current = owner
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise BoundaryError("native_agent_support", "unsafe_binding_path")
    return checked


def _config(path: Path) -> dict[str, Any]:
    value = object_fields(_object_file(path), CONFIG_FIELDS, "native_agent_support.config")
    if (value["schema"] != CONFIG_SCHEMA or not isinstance(value["export_path"], str)
            or not Path(value["export_path"]).is_absolute()):
        raise BoundaryError("native_agent_support", "unsupported_config")
    for field in ("artifact_id", "package_model_id", "package_manifest_sha256", "weights_sha256"):
        digest(value[field], "native_agent_support." + field)
    if value["publication_profile"] != {"id": PUBLICATION_PROFILE_ID,
                                        "definition_sha256": PUBLICATION_PROFILE_SHA256}:
        raise BoundaryError("native_agent_support", "publication_profile_identity_drift")
    return value


def bind_native_export(root: Path, export_path: Path, config_path: Path,
                       manifest_path: Path, *, artifact_id: str, manifest_id: str,
                       requirements: dict[str, Any], support: dict[str, Any],
                       required_seams: list[dict[str, str]],
                       binding_root: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    from stpd.policy.native_agent import bind_native_agent
    from stpd.policy.native_structured_export import load_native_package

    owner = (binding_root or root).resolve()
    config_path, manifest_path = (_bound_path(owner, config_path),
                                  _bound_path(owner, manifest_path))
    if (config_path == manifest_path or config_path.exists() or config_path.is_symlink()
            or manifest_path.exists() or manifest_path.is_symlink()):
        raise BoundaryError("native_agent_support", "invalid_binding_destination")
    package, model = load_native_package(export_path)
    del model
    config = {"schema": CONFIG_SCHEMA, "export_path": str(export_path.resolve()),
              "artifact_id": digest(artifact_id, "native_agent_support.artifact_id"),
              "package_model_id": package["model_id"],
              "package_manifest_sha256": file_sha256(export_path / "model.json"),
              "weights_sha256": package["weights"]["sha256"],
              "publication_profile": {"id": PUBLICATION_PROFILE_ID,
                                      "definition_sha256": PUBLICATION_PROFILE_SHA256},
              **{key: package[key] for key in
                 ("input_spec", "agent_spec", "state_format_version", "code_identity")}}
    try:
        manifest = bind_native_agent(export_path, manifest_path, manifest_id=manifest_id,
                                     requirements=requirements, support=support,
                                     required_seams=required_seams)
        config_path.write_bytes(json_bytes(config))
        validate(root, config_path, manifest_path, binding_root=owner)
    except Exception:
        config_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise
    return config, manifest


def validate(root: Path, config_path: Path, manifest_path: Path, *,
             binding_root: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    from stpd.policy.native_agent import NativeStructuredAgent, adapter_identity
    from stpd.policy.native_structured_export import load_native_package

    owner = (binding_root or root).resolve()
    config = _config(_bound_path(owner, config_path))
    manifest_path = _bound_path(owner, manifest_path)
    manifest = _object_file(manifest_path)
    export = Path(config["export_path"])
    package, model = load_native_package(export)
    # The domain loader validates the complete package/control/weight binding.
    # Match the adapter closure selected by that model, never by a config flag.
    expected_adapter = adapter_identity(graph=model.model_control is not None)
    if (config["package_model_id"] != package["model_id"]
            or config["weights_sha256"] != package["weights"]["sha256"]
            or config["package_manifest_sha256"] != file_sha256(export / "model.json")
            or any(config[key] != package[key] for key in
                   ("input_spec", "agent_spec", "state_format_version", "code_identity"))
            or manifest.get("schema") != "sts2.policy-runtime/agent-manifest-1"
            or manifest.get("adapter") != expected_adapter
            or manifest.get("artifact") != {
                "id": config["package_model_id"], "path": str((export / "model.json").resolve()),
                "sha256": config["package_manifest_sha256"]}):
        raise BoundaryError("native_agent_support", "export_identity_drift")
    NativeStructuredAgent(export, manifest_path)
    return config, manifest


def arguments(entry: dict[str, Any]) -> list[str]:
    config_path, manifest_path = Path(entry["config"]), Path(entry["manifest"])
    config = _config(config_path)
    # The service has already validated both registered paths and the domain package.
    manifest = _object_file(manifest_path)
    if (manifest.get("schema") != "sts2.policy-runtime/agent-manifest-1"
            or manifest.get("adapter", {}).get("id") != ADAPTER
            or manifest.get("artifact") != {
                "id": config["package_model_id"],
                "path": str((Path(config["export_path"]) / "model.json").resolve()),
                "sha256": config["package_manifest_sha256"]}):
        raise BoundaryError("native_agent_support", "export_identity_drift")
    return ["-m", "stpd.policy.native_agent", "--package", config["export_path"],
            "--manifest", str(manifest_path)]


def inspect(root: Path, entry: dict[str, Any], manifest: dict[str, Any],
            policy_config: dict[str, Any], *, binding_root: Path | None = None
            ) -> dict[str, dict[str, str]]:
    try:
        owner = binding_root or root
        config, checked = validate(root, owner / entry["config"], owner / entry["manifest"],
                                   binding_root=owner)
        if config != policy_config or checked != manifest:
            raise BoundaryError("native_agent_support", "metadata_changed")
        return {"policy_identity": {"status": "pass"},
                "backend": {"status": "pass", "code": "cpu"}}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError) as error:
        return {"policy_identity": {"status": "blocked", "code": (
            error.code if isinstance(error, BoundaryError) else
            "native_models_extra_required" if isinstance(error, ImportError) else
            "metadata_unavailable")}}
