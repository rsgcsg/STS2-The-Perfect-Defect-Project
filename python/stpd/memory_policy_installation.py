"""Strict portable M2 package and port-2 adapter binding, without activation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, json_bytes, object_fields
from spireagent.package_identity import file_sha256
from spireagent.policy_files import _inside, _object_file

from .fullrun.text_menu_inputs import IDENTITY as TEXT_MENU_IDENTITY
from .policy.memory_export import MANIFEST_NAME, RENDERER, validate_memory_package
from .token_policy_installation import _manifest_artifact_path

CONFIG_SCHEMA = "stpd/m2-policy-config-v1"
PROTOCOL = "sts2.policy-runtime/decision-only-ndjson-2"
ADAPTER = "stpd-m2-decision-adapter"
CODE_SCOPE = "python-m2-owner-source-and-lock-v1"
CLAIMS = {"full_run": False, "selector": False, "catalog_filtered": False,
          "creates_action_authority": False, "creates_native_operands": False}
REQUIREMENT_FIELDS = {"connector_protocol_version", "environment", "reads",
                      "whole_decision_admission", "candidate_order_digest",
                      "score_count_matches_candidate_count", "selected_index",
                      "successor_required"}
ENVIRONMENT_FIELDS = {"host_kind", "connector_version", "connector_source_revision",
                      "connector_artifact_sha256", "connector_module_version_id",
                      "modset_status", "modset_fingerprint", "loaded_mod_ids"}


def _binding_facts(policy: object, requirements: object, support: object) -> None:
    policy = object_fields(policy, {"id", "version", "provider", "architecture"},
                           "m2_policy.policy")
    requirements = object_fields(requirements, REQUIREMENT_FIELDS,
                                 "m2_policy.requirements")
    environment = object_fields(requirements["environment"], ENVIRONMENT_FIELDS,
                                "m2_policy.environment")
    support = object_fields(support, {"game_versions", "game_commits", "interaction_kinds",
                                      "action_verbs"}, "m2_policy.support")
    if (any(not isinstance(item, str) or not item for item in policy.values())
            or environment["host_kind"] not in {"live_ui", "headless", "replay", "test"}
            or any(not isinstance(environment[key], str) or not environment[key]
                   for key in ("connector_version", "connector_source_revision",
                               "connector_module_version_id", "modset_status",
                               "modset_fingerprint"))
            or not isinstance(environment["connector_artifact_sha256"], str)
            or len(environment["connector_artifact_sha256"]) != 64
            or not isinstance(environment["loaded_mod_ids"], list)
            or any(not isinstance(item, str) for item in environment["loaded_mod_ids"])
            or any(not isinstance(support[key], list) or not support[key]
                   or any(not isinstance(item, str) or not item for item in support[key])
                   for key in support)
            or requirements["reads"] != []
            or requirements["candidate_order_digest"]
            != "sha256-json-menu-action-id-order"
            or requirements["whole_decision_admission"] is not True
            or requirements["score_count_matches_candidate_count"] is not True
            or requirements["selected_index"] is not True
            or requirements["successor_required"] is not True):
        raise BoundaryError("m2_policy", "unsupported_binding_facts")


def code_digest(root: Path) -> str:
    paths = sorted([*root.glob("stpd/**/*.py"), *root.glob("spireagent/**/*.py"),
                    root / "uv.lock"], key=lambda path: path.relative_to(root).as_posix())
    if not (root / "stpd/policy/memory_port.py").is_file() or any(
        not path.is_file() for path in paths
    ):
        raise BoundaryError("m2_policy", "source_missing")
    rows = [{"path": path.relative_to(root).as_posix(), "sha256": file_sha256(path)}
            for path in paths]
    return hashlib.sha256(canonical_json(rows).encode()).hexdigest()


def bind_memory_export(root: Path, export_path: Path, config_path: Path,
                       manifest_path: Path, *, manifest_id: str,
                       policy: dict[str, Any], requirements: dict[str, Any],
                       support: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind caller-owned environment facts to an integrity-checked M2 package."""
    root, export_path = root.resolve(), export_path.resolve()
    config_path, manifest_path = config_path.resolve(), manifest_path.resolve()
    if (not config_path.is_relative_to(root) or not manifest_path.is_relative_to(root)
            or config_path == manifest_path or config_path.exists() or manifest_path.exists()
            or not isinstance(manifest_id, str) or not manifest_id):
        raise BoundaryError("m2_policy", "invalid_binding_destination")
    _binding_facts(policy, requirements, support)
    package, _, _, _ = validate_memory_package(export_path)
    config = {"schema": CONFIG_SCHEMA, "export_path": str(export_path),
              "export_manifest_sha256": file_sha256(export_path / MANIFEST_NAME),
              "model_id": package["ids"]["model"]}
    manifest = {
        "schema": "sts2.policy-runtime/policy-manifest-1", "manifest_id": manifest_id,
        "policy": policy,
        "adapter": {"id": ADAPTER, "version": "1.0.0", "protocol": PROTOCOL,
                    "code_sha256": code_digest(root)},
        "artifact": {"id": config["model_id"],
                     "path": _manifest_artifact_path(
                         export_path / MANIFEST_NAME, manifest_path.parent),
                     "sha256": config["export_manifest_sha256"]},
        "representation": {"id": TEXT_MENU_IDENTITY["profile"],
                           "version": TEXT_MENU_IDENTITY["version"],
                           "input_schema": RENDERER["input_schema"]},
        "requirements": requirements, "support": support,
        "adapter_config": {"stage1a": {"code_digest_scope": CODE_SCOPE,
            "config": {"path": config_path.relative_to(root).as_posix(),
                       "sha256": hashlib.sha256(json_bytes(config)).hexdigest(),
                       "schema": CONFIG_SCHEMA}}},
        "claims": CLAIMS,
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        config_path.write_bytes(json_bytes(config))
        manifest_path.write_bytes(json_bytes(manifest))
        validate(root, config_path, manifest_path)
    except Exception:
        config_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise
    return config, manifest


def validate(root: Path, config_path: Path, manifest_path: Path) -> tuple[dict, dict]:
    root = root.resolve()
    config_path = _inside(root, config_path.resolve().relative_to(root).as_posix())
    manifest_path = _inside(root, manifest_path.resolve().relative_to(root).as_posix())
    config = object_fields(_object_file(config_path), {
        "schema", "export_path", "export_manifest_sha256", "model_id",
    }, "m2_policy.config")
    manifest = object_fields(_object_file(manifest_path), {
        "schema", "manifest_id", "policy", "adapter", "artifact", "representation",
        "requirements", "support", "adapter_config", "claims",
    }, "m2_policy.manifest")
    export = Path(config["export_path"])
    if config["schema"] != CONFIG_SCHEMA or not export.is_absolute():
        raise BoundaryError("m2_policy", "unsupported_config")
    package, _, _, _ = validate_memory_package(export)
    if (config["model_id"] != package["ids"]["model"]
            or config["export_manifest_sha256"] != file_sha256(export / MANIFEST_NAME)):
        raise BoundaryError("m2_policy", "export_identity_drift")
    expected_pin = {"code_digest_scope": CODE_SCOPE,
                    "config": {"path": config_path.relative_to(root).as_posix(),
                               "sha256": file_sha256(config_path),
                               "schema": CONFIG_SCHEMA}}
    expected_adapter = {"id": ADAPTER, "version": "1.0.0", "protocol": PROTOCOL,
                        "code_sha256": code_digest(root)}
    artifact = manifest.get("artifact", {})
    if (manifest.get("schema") != "sts2.policy-runtime/policy-manifest-1"
            or not isinstance(manifest["manifest_id"], str) or not manifest["manifest_id"]
            or manifest.get("adapter") != expected_adapter
            or manifest.get("adapter_config", {}).get("stage1a") != expected_pin
            or artifact != {"id": config["model_id"],
                            "path": _manifest_artifact_path(
                                export / MANIFEST_NAME, manifest_path.parent),
                            "sha256": config["export_manifest_sha256"]}
            or (manifest_path.parent / artifact["path"]).resolve()
            != (export / MANIFEST_NAME).resolve()
            or manifest.get("representation") != {
                "id": TEXT_MENU_IDENTITY["profile"],
                "version": TEXT_MENU_IDENTITY["version"],
                "input_schema": RENDERER["input_schema"]}
            or manifest.get("claims") != CLAIMS):
        raise BoundaryError("m2_policy", "trusted_policy_identity_drift")
    _binding_facts(manifest["policy"], manifest["requirements"], manifest["support"])
    return config, manifest


def arguments(entry: dict[str, Any]) -> list[str]:
    return ["-m", "stpd.policy.memory_port", "--config", entry["config"],
            "--manifest", entry["manifest"]]
