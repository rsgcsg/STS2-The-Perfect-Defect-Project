"""Trusted CPU Public M2 consumer binding over generic Snapshot Port4."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes, object_fields
from spireagent.package_identity import file_sha256
from spireagent.policy_files import _inside, _object_file

from .policy.public_m2_export import EXPORT_SCHEMA, validate_package
from .public_m0_policy_installation import (
    CLAIMS,
    SNAPSHOT_SCHEMA,
    _manifest_artifact_path,
    _validate_requirements,
)

CONFIG_SCHEMA = "stpd/public-m2-policy-config-v1"
PROFILE = "public-snapshot-m2-v1"
ADAPTER_ID = "stpd-public-m2-decision-adapter"
ADAPTER_VERSION = "1.0.0"
PROTOCOL = "sts2.policy-runtime/decision-only-ndjson-4"
MODEL_SCHEMA = "stpd/public-m2-model-v1"
CODE_SCOPE = "public-m2-runtime-import-closure-v1"
ADAPTER_SOURCE_CLOSURE = (
    "spireagent/__init__.py",
    "spireagent/artifact_contracts.py",
    "spireagent/encoding.py",
    "spireagent/hub/__init__.py",
    "spireagent/hub/database.py",
    "spireagent/hub/uploads.py",
    "spireagent/json_boundary.py",
    "spireagent/local_verified_bundle.py",
    "spireagent/package_identity.py",
    "spireagent/policy_files.py",
    "spireagent/storage/__init__.py",
    "spireagent/storage/blobs.py",
    "spireagent/storage/run_reporter.py",
    "spireagent/storage/store.py",
    "stpd/__init__.py",
    "stpd/canonical.py",
    "stpd/contracts.py",
    "stpd/environment/__init__.py",
    "stpd/environment/collector.py",
    "stpd/environment/identity.py",
    "stpd/environment/projector.py",
    "stpd/fullrun/__init__.py",
    "stpd/fullrun/contracts.py",
    "stpd/fullrun/platform_bundle3.py",
    "stpd/fullrun/public_compaction.py",
    "stpd/fullrun/public_inputs.py",
    "stpd/fullrun/representation.py",
    "stpd/fullrun/token_format.py",
    "stpd/light_action_codec.py",
    "stpd/linear_q.py",
    "stpd/models/__init__.py",
    "stpd/models/_backend.py",
    "stpd/models/batches.py",
    "stpd/models/light_action_encoder.py",
    "stpd/models/light_action_m2.py",
    "stpd/models/light_action_m2_training_data.py",
    "stpd/models/losses.py",
    "stpd/models/objectives.py",
    "stpd/models/packed_actions.py",
    "stpd/models/public_m2_window.py",
    "stpd/models/s2_sdt.py",
    "stpd/models/s2_simple.py",
    "stpd/models/scheme1.py",
    "stpd/models/token_core.py",
    "stpd/policy/__init__.py",
    "stpd/policy/adapter.py",
    "stpd/policy/public_m2_cli.py",
    "stpd/policy/public_m2_export.py",
    "stpd/policy/public_m2_port.py",
    "stpd/policy/s1.py",
    "stpd/public_m0_policy_installation.py",
    "stpd/public_m2_policy_installation.py",
    "stpd/qwen/__init__.py",
    "stpd/qwen/l1.py",
    "stpd/qwen/l2.py",
    "stpd/qwen/real_backend.py",
    "stpd/representation.py",
    "stpd/training/__init__.py",
    "stpd/training/checkpoint.py",
    "stpd/training/trainer.py",
    "stpd/workers/__init__.py",
    "stpd/workers/checkpoint_codec.py",
    "stpd/workers/public_m2_engine.py",
    "uv.lock",
)



def code_digest(root: Path) -> str:
    # The pin excludes Workbench and Hub application lifecycle. Shared archive
    # transport helpers imported by the public renderer remain explicit inputs.
    # Numerical compatibility has its separate nine-file implementation pin.
    paths = [root / name for name in ADAPTER_SOURCE_CLOSURE]
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise BoundaryError("public_m2_policy", "consumer_source_missing")
    rows = [{"path": p.relative_to(root).as_posix(), "sha256": file_sha256(p)}
            for p in sorted(paths, key=lambda p: p.relative_to(root).as_posix())]
    return hashlib.sha256(json_bytes(rows)).hexdigest()


def _profile(model: Any, config: Any) -> dict[str, Any]:
    from .fullrun.public_inputs import COMPACT_IDENTITY

    return {"sequence_profile": config.sequence_profile,
            "prior_action_profile": config.prior_action_profile,
            "feedback_profile": config.feedback_profile, "renderer": COMPACT_IDENTITY,
            "weights_sha256": model.payload("weights").sha256,
            "state_tokenizer_sha256": model.payload("state_tokenizer").sha256,
            "engine_input_digest": model.parameters.value()["engine_input_digest"]}


def bind_public_m2_export(
    root: Path, export_path: Path, config_path: Path, manifest_path: Path, *,
    manifest_id: str, policy: dict[str, Any], requirements: dict[str, Any],
    support: dict[str, Any], binding_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root, export_path = root.resolve(), export_path.resolve()
    binding_root = (binding_root or root).resolve()
    config_path, manifest_path = config_path.resolve(), manifest_path.resolve()
    if (not config_path.is_relative_to(binding_root)
            or not manifest_path.is_relative_to(binding_root)
            or config_path == manifest_path or config_path.exists() or manifest_path.exists()
            or not isinstance(manifest_id, str) or not manifest_id):
        raise BoundaryError("public_m2_policy", "invalid_binding_destination")
    object_fields(policy, {"id", "version", "provider", "architecture"}, "public_m2.policy")
    if any(not isinstance(v, str) or not v for v in policy.values()):
        raise BoundaryError("public_m2_policy", "invalid_policy")
    req, sup = _validate_requirements(requirements, support)
    model, config, _lineage = validate_package(export_path)
    from .fullrun.public_inputs import COMPACT_IDENTITY
    from .policy.public_m2_port import RUNTIME_PROFILE

    value = {"schema": CONFIG_SCHEMA, "export_path": str(export_path),
             "export_manifest_sha256": file_sha256(export_path / "model.json"),
             "model_id": model.artifact_id, "runtime_device": "cpu"}
    manifest = {
        "schema": "sts2.policy-runtime/policy-manifest-1", "manifest_id": manifest_id,
        "policy": policy,
        "adapter": {"id": ADAPTER_ID, "version": ADAPTER_VERSION, "protocol": PROTOCOL,
                    "code_sha256": code_digest(root)},
        "artifact": {"id": model.artifact_id,
                     "path": _manifest_artifact_path(export_path / "model.json",
                                                     manifest_path.parent),
                     "sha256": value["export_manifest_sha256"]},
        "representation": {"id": COMPACT_IDENTITY["profile"],
                           "version": COMPACT_IDENTITY["version"], "input_schema": SNAPSHOT_SCHEMA},
        "requirements": req, "support": sup,
        "adapter_config": {
            "public_stateful_profile": RUNTIME_PROFILE, "stpd_public_m2": _profile(model, config),
            "public_m2_installation": {
                "code_digest_scope": CODE_SCOPE,
                "config": {"path": config_path.relative_to(binding_root).as_posix(),
                           "sha256": hashlib.sha256(json_bytes(value)).hexdigest(),
                           "schema": CONFIG_SCHEMA},
                "model_schema": MODEL_SCHEMA, "export_schema": EXPORT_SCHEMA,
                "producer": model.producer.to_dict(),
            },
        }, "claims": dict(CLAIMS),
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        config_path.write_bytes(json_bytes(value))
        manifest_path.write_bytes(json_bytes(manifest))
        validate(root, config_path, manifest_path, binding_root=binding_root)
    except Exception:
        config_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise
    return value, manifest


def validate(root: Path, config_path: Path, manifest_path: Path, *,
             binding_root: Path | None = None, verify_payloads: bool = True
             ) -> tuple[dict[str, Any], dict[str, Any]]:
    binding_root = (binding_root or root).resolve()
    config_path = _inside(binding_root, config_path.resolve().relative_to(binding_root).as_posix())
    manifest_path = _inside(binding_root,
                            manifest_path.resolve().relative_to(binding_root).as_posix())
    config, manifest = _object_file(config_path), _object_file(manifest_path)
    object_fields(config, {"schema", "export_path", "export_manifest_sha256", "model_id",
                           "runtime_device"}, "public_m2.config")
    object_fields(manifest, {"schema", "manifest_id", "policy", "adapter", "artifact",
                             "representation", "requirements", "support", "adapter_config",
                             "claims"}, "public_m2.manifest")
    export = Path(config["export_path"])
    if not export.is_absolute() or config["schema"] != CONFIG_SCHEMA:
        raise BoundaryError("public_m2_policy", "unsupported_config")
    model, model_config, _lineage = validate_package(export, check_payloads=verify_payloads)
    from .fullrun.public_inputs import COMPACT_IDENTITY
    from .policy.public_m2_port import RUNTIME_PROFILE, _manifest_profile

    expected = {
        "public_stateful_profile": RUNTIME_PROFILE, "stpd_public_m2": _profile(model, model_config),
        "public_m2_installation": {
            "code_digest_scope": CODE_SCOPE,
            "config": {"path": config_path.relative_to(binding_root).as_posix(),
                       "sha256": file_sha256(config_path), "schema": CONFIG_SCHEMA},
            "model_schema": MODEL_SCHEMA, "export_schema": EXPORT_SCHEMA,
            "producer": model.producer.to_dict(),
        },
    }
    object_fields(manifest["policy"], {"id", "version", "provider", "architecture"},
                  "public_m2.policy")
    if (manifest["schema"] != "sts2.policy-runtime/policy-manifest-1"
            or not isinstance(manifest["manifest_id"], str) or not manifest["manifest_id"]
            or any(not isinstance(v, str) or not v for v in manifest["policy"].values())
            or manifest["adapter"] != {"id": ADAPTER_ID, "version": ADAPTER_VERSION,
                                        "protocol": PROTOCOL, "code_sha256": code_digest(root)}
            or manifest["adapter_config"] != expected
            or manifest["representation"] != {"id": COMPACT_IDENTITY["profile"],
                    "version": COMPACT_IDENTITY["version"], "input_schema": SNAPSHOT_SCHEMA}
            or manifest["claims"] != CLAIMS or config["runtime_device"] != "cpu"):
        raise BoundaryError("public_m2_policy", "trusted_policy_identity_drift")
    _validate_requirements(manifest["requirements"], manifest["support"])
    _manifest_profile(manifest, model_config, weights_sha256=model.payload("weights").sha256,
                      input_digest=model.parameters.value()["engine_input_digest"])
    if (config["model_id"] != model.artifact_id
            or config["export_manifest_sha256"] != file_sha256(export / "model.json")
            or manifest["artifact"] != {"id": model.artifact_id,
                "path": _manifest_artifact_path(export / "model.json", manifest_path.parent),
                "sha256": config["export_manifest_sha256"]}):
        raise BoundaryError("public_m2_policy", "export_identity_drift")
    return config, manifest


def inspect(root: Path, entry: dict[str, Any], manifest: dict[str, Any],
            policy_config: dict[str, Any], *, binding_root: Path | None = None
            ) -> dict[str, dict[str, str]]:
    owner = binding_root or root
    try:
        config, checked = validate(root, _inside(owner, entry["config"]),
                                   _inside(owner, entry["manifest"]), binding_root=owner)
        if checked != manifest or config != policy_config:
            raise BoundaryError("public_m2_policy", "metadata_changed")
        validate_package(Path(config["export_path"]), load_weights=True)
        return {"policy_identity": {"status": "pass"}, "backend": {"status": "pass", "code": "cpu"}}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return {"policy_identity": {"status": "blocked",
                "code": getattr(error, "code", "metadata_or_backend_unavailable")}}
