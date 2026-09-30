"""Strict portable M2 package and port-2 adapter binding, without activation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, digest, json_bytes, object_fields
from spireagent.package_identity import file_sha256
from spireagent.policy_files import _inside, _object_file

from .fullrun.confirmed_interaction import (
    HISTORY_PROFILES,
    V2_HISTORY_INPUT_PROFILE,
)
from .fullrun.text_menu_inputs import INPUT_PROFILE, V2_INPUT_PROFILE
from .policy.memory_export import MANIFEST_NAME, validate_memory_package
from .token_policy_installation import _manifest_artifact_path

CONFIG_SCHEMA = "stpd/m2-policy-config-v1"
V2_CONFIG_SCHEMA = "stpd/m2-policy-config-v2"
HISTORY_CONFIG_SCHEMA = "stpd/m2-policy-config-v3"
MANAGED_HISTORY_CONFIG_SCHEMA = "stpd/m2-policy-config-managed-v1"
PROTOCOL = "sts2.policy-runtime/decision-only-ndjson-2"
HISTORY_PROTOCOL = "sts2.policy-runtime/decision-only-ndjson-3"
ADAPTER = "stpd-m2-decision-adapter"
CODE_SCOPE = "python-m2-owner-source-and-lock-v1"
CLAIMS = {"full_run": False, "selector": False, "catalog_filtered": False,
          "creates_action_authority": False, "creates_native_operands": False}
REQUIREMENT_FIELDS = {"connector_protocol_version", "environment", "reads",
                      "whole_decision_admission", "candidate_order_digest",
                      "score_count_matches_candidate_count", "selected_index",
                      "successor_required"}
MANAGED_REQUIREMENT_FIELDS = REQUIREMENT_FIELDS - {"connector_protocol_version"}
ENVIRONMENT_FIELDS = {"host_kind", "connector_version", "connector_source_revision",
                      "connector_artifact_sha256", "connector_module_version_id",
                      "modset_status", "modset_fingerprint", "loaded_mod_ids"}
MANAGED_ENVIRONMENT_FIELDS = {"kind", "text_protocol_version", "input_profile"}


def input_profile_for_config(value: object) -> str:
    """Decode only closed, versioned binding configuration; never a request hint."""
    if not isinstance(value, dict):
        raise BoundaryError("m2_policy", "unsupported_config")
    fields = {"schema", "export_path", "export_manifest_sha256", "model_id"}
    if value.get("schema") == CONFIG_SCHEMA:
        object_fields(value, fields, "m2_policy.config")
        return INPUT_PROFILE
    if value.get("schema") == V2_CONFIG_SCHEMA:
        config = object_fields(value, fields | {"input_profile"}, "m2_policy.config")
        if config["input_profile"] == V2_INPUT_PROFILE:
            return V2_INPUT_PROFILE
    if value.get("schema") == HISTORY_CONFIG_SCHEMA:
        config = object_fields(value, fields | {"input_profile"}, "m2_policy.config")
        profile = config["input_profile"]
        if isinstance(profile, str) and profile in HISTORY_PROFILES:
            return profile
    if value.get("schema") == MANAGED_HISTORY_CONFIG_SCHEMA:
        config = object_fields(value, fields | {"input_profile"}, "m2_policy.config")
        if config["input_profile"] == V2_HISTORY_INPUT_PROFILE:
            return V2_HISTORY_INPUT_PROFILE
    raise BoundaryError("m2_policy", "unsupported_config")


def _representation(package: dict[str, Any]) -> dict[str, str]:
    """The package validator, not the browser, owns this renderer identity."""
    renderer = package["renderer"]
    return {"id": renderer["text_menu"]["profile"],
            "version": renderer["text_menu"]["version"],
            "input_schema": renderer["input_schema"]}


def _adapter_version(input_profile: str) -> str:
    if input_profile in HISTORY_PROFILES:
        return "3.0.0"
    return "2.0.0" if input_profile == V2_INPUT_PROFILE else "1.0.0"


def _protocol(input_profile: str) -> str:
    return HISTORY_PROTOCOL if input_profile in HISTORY_PROFILES else PROTOCOL


def _binding_facts(policy: object, requirements: object, support: object, *,
                   managed: bool = False) -> None:
    policy = object_fields(policy, {"id", "version", "provider", "architecture"},
                           "m2_policy.policy")
    requirements = object_fields(requirements, (MANAGED_REQUIREMENT_FIELDS if managed else
                                                REQUIREMENT_FIELDS),
                                 "m2_policy.requirements")
    environment = object_fields(requirements["environment"],
                                (MANAGED_ENVIRONMENT_FIELDS if managed else
                                 ENVIRONMENT_FIELDS),
                                "m2_policy.environment")
    support = object_fields(support, {"game_versions", "game_commits", "interaction_kinds",
                                      "action_verbs"}, "m2_policy.support")
    if (any(not isinstance(item, str) or not item for item in policy.values())
            or (managed and environment != {
                "kind": "managed_text_v2", "text_protocol_version": "1.0.0",
                "input_profile": V2_INPUT_PROFILE})
            or (not managed and (
                environment["host_kind"] not in {"live_ui", "headless", "replay", "test"}
                or any(not isinstance(environment[key], str) or not environment[key]
                       for key in ("connector_version", "connector_source_revision",
                                   "connector_module_version_id", "modset_status",
                                   "modset_fingerprint"))
                or not isinstance(environment["connector_artifact_sha256"], str)
                or len(environment["connector_artifact_sha256"]) != 64
                or not isinstance(environment["loaded_mod_ids"], list)
                or any(not isinstance(item, str) for item in environment["loaded_mod_ids"])))
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


def _bind_memory_export(root: Path, export_path: Path, config_path: Path,
                        manifest_path: Path, *, manifest_id: str,
                        policy: dict[str, Any], requirements: dict[str, Any],
                        support: dict[str, Any], binding_root: Path | None,
                        input_profile: str, managed: bool,
                        ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind caller-owned environment facts to an integrity-checked M2 package."""
    root, export_path = root.resolve(), export_path.resolve()
    binding_root = (binding_root or root).resolve()
    config_path, manifest_path = config_path.resolve(), manifest_path.resolve()
    if (not config_path.is_relative_to(binding_root)
            or not manifest_path.is_relative_to(binding_root)
            or config_path == manifest_path or config_path.exists() or manifest_path.exists()
            or not isinstance(manifest_id, str) or not manifest_id):
        raise BoundaryError("m2_policy", "invalid_binding_destination")
    if managed and input_profile != V2_HISTORY_INPUT_PROFILE:
        raise BoundaryError("m2_policy", "unsupported_managed_input_profile")
    _binding_facts(policy, requirements, support, managed=managed)
    package, _, _, _ = validate_memory_package(export_path, input_profile=input_profile)
    config_schema = (MANAGED_HISTORY_CONFIG_SCHEMA if managed else
                     HISTORY_CONFIG_SCHEMA if input_profile in HISTORY_PROFILES else
                     V2_CONFIG_SCHEMA if input_profile == V2_INPUT_PROFILE else CONFIG_SCHEMA)
    config = {"schema": config_schema, "export_path": str(export_path),
              # The package validator requires canonical bytes. Pin the bytes
              # already validated, rather than re-reading a replaceable path.
              "export_manifest_sha256": hashlib.sha256(json_bytes(package)).hexdigest(),
              "model_id": package["ids"]["model"]}
    if input_profile != INPUT_PROFILE:
        config["input_profile"] = input_profile
    manifest = {
        "schema": "sts2.policy-runtime/policy-manifest-1", "manifest_id": manifest_id,
        "policy": policy,
        "adapter": {"id": ADAPTER, "version": _adapter_version(input_profile),
                    "protocol": _protocol(input_profile),
                    "code_sha256": code_digest(root)},
        "artifact": {"id": config["model_id"],
                     "path": _manifest_artifact_path(
                         export_path / MANIFEST_NAME, manifest_path.parent),
                     "sha256": config["export_manifest_sha256"]},
        "representation": _representation(package),
        "requirements": requirements, "support": support,
        "adapter_config": {"stage1a": {"code_digest_scope": CODE_SCOPE,
            "config": {"path": config_path.relative_to(binding_root).as_posix(),
                       "sha256": hashlib.sha256(json_bytes(config)).hexdigest(),
                       "schema": config_schema}}},
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


def bind_memory_export(root: Path, export_path: Path, config_path: Path,
                       manifest_path: Path, *, manifest_id: str,
                       policy: dict[str, Any], requirements: dict[str, Any],
                       support: dict[str, Any], binding_root: Path | None = None,
                       input_profile: str = INPUT_PROFILE,
                       ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Retain the exact Connector-bound Native policy contract."""
    return _bind_memory_export(
        root, export_path, config_path, manifest_path, manifest_id=manifest_id,
        policy=policy, requirements=requirements, support=support,
        binding_root=binding_root, input_profile=input_profile, managed=False)


def bind_managed_memory_export(root: Path, export_path: Path, config_path: Path,
                               manifest_path: Path, *, manifest_id: str,
                               policy: dict[str, Any], requirements: dict[str, Any],
                               support: dict[str, Any], binding_root: Path | None = None,
                               ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind the same verified v2 history export to Managed text requirements."""
    return _bind_memory_export(
        root, export_path, config_path, manifest_path, manifest_id=manifest_id,
        policy=policy, requirements=requirements, support=support,
        binding_root=binding_root, input_profile=V2_HISTORY_INPUT_PROFILE, managed=True)


def validate(root: Path, config_path: Path, manifest_path: Path, *,
             binding_root: Path | None = None) -> tuple[dict, dict]:
    root = root.resolve()
    binding_root = (binding_root or root).resolve()
    config_path = _inside(binding_root,
                          config_path.resolve().relative_to(binding_root).as_posix())
    manifest_path = _inside(binding_root,
                            manifest_path.resolve().relative_to(binding_root).as_posix())
    config = _object_file(config_path)
    input_profile = input_profile_for_config(config)
    manifest_pin = digest(config["export_manifest_sha256"], "m2_policy.export_manifest_sha256")
    manifest = object_fields(_object_file(manifest_path), {
        "schema", "manifest_id", "policy", "adapter", "artifact", "representation",
        "requirements", "support", "adapter_config", "claims",
    }, "m2_policy.manifest")
    export = Path(config["export_path"])
    if not export.is_absolute():
        raise BoundaryError("m2_policy", "unsupported_config")
    package, _, _, _ = validate_memory_package(
        export, input_profile=input_profile,
        expected_manifest_sha256=manifest_pin)
    if config["model_id"] != package["ids"]["model"]:
        raise BoundaryError("m2_policy", "export_identity_drift")
    expected_pin = {"code_digest_scope": CODE_SCOPE,
                    "config": {"path": config_path.relative_to(binding_root).as_posix(),
                               "sha256": file_sha256(config_path),
                               "schema": config["schema"]}}
    expected_adapter = {"id": ADAPTER, "version": _adapter_version(input_profile),
                        "protocol": _protocol(input_profile),
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
            or manifest.get("representation") != _representation(package)
            or manifest.get("claims") != CLAIMS):
        raise BoundaryError("m2_policy", "trusted_policy_identity_drift")
    _binding_facts(manifest["policy"], manifest["requirements"], manifest["support"],
                   managed=config["schema"] == MANAGED_HISTORY_CONFIG_SCHEMA)
    return config, manifest


def arguments(entry: dict[str, Any]) -> list[str]:
    return ["-m", "stpd.policy.memory_port", "--config", entry["config"],
            "--manifest", entry["manifest"]]


def inspect(root: Path, entry: dict[str, Any], manifest: dict[str, Any],
            policy_config: dict[str, Any], *,
            binding_root: Path | None = None) -> dict[str, dict[str, str]]:
    """Readiness of the detached train-only package, not policy quality."""
    try:
        binding_root = binding_root or root
        config, checked = validate(root, _inside(binding_root, entry["config"]),
                                   _inside(binding_root, entry["manifest"]),
                                   binding_root=binding_root)
        if config != policy_config or checked != manifest:
            raise BoundaryError("m2_policy", "metadata_changed")
        return {"policy_identity": {"status": "pass"}}
    except (OSError, ValueError, KeyError, TypeError, BoundaryError) as error:
        return {"policy_identity": {"status": "blocked",
                                    "code": (error.code if isinstance(error, BoundaryError)
                                             else "metadata_unavailable")}}
