"""Trusted Stage 1a installation metadata; never loads Torch or model weights here."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, json_bytes, object_fields
from spireagent.package_identity import file_sha256
from spireagent.policy_files import _inside, _object_file

CONFIG_SCHEMA = "stpd/token-policy-config-v1"
CODE_SCOPE = "python-owner-source-and-lock-v1"
PROTOCOL = "sts2.policy-runtime/decision-only-ndjson-1"


def bind_text_menu_export(
    root: Path, export_path: Path, config_path: Path, manifest_path: Path,
    *, manifest_id: str, policy: dict[str, Any], requirements: dict[str, Any],
    support: dict[str, Any], qwen_snapshot: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind an exported text-menu model to caller-owned environment/support facts.

    No game, Connector, or action-support identity is inferred here. The exported
    model's exact serializer and bytes determine the representation and artifact
    pins; the caller supplies the remaining reviewed Policy Manifest fields.
    """
    from .fullrun.text_menu_inputs import IDENTITY, SNAPSHOT_SCHEMA

    root = root.resolve()
    export_path = export_path.resolve()
    config_path = config_path.resolve()
    manifest_path = manifest_path.resolve()
    if (not config_path.is_relative_to(root) or not manifest_path.is_relative_to(root)
            or config_path == manifest_path or config_path.exists() or manifest_path.exists()
            or not isinstance(manifest_id, str) or not manifest_id
            or not all(isinstance(value, dict) for value in (policy, requirements, support))):
        raise BoundaryError("token_policy", "invalid_binding_destination_or_facts")
    object_fields(policy, {"id", "version", "provider", "architecture"}, "token_policy.policy")
    object_fields(support, {"game_versions", "game_commits", "interaction_kinds",
                            "action_verbs"}, "token_policy.support")
    environment = object_fields(requirements.get("environment"), {
        "host_kind", "connector_version", "connector_source_revision",
        "connector_artifact_sha256", "connector_module_version_id", "modset_status",
        "modset_fingerprint", "loaded_mod_ids",
    }, "token_policy.environment")
    if (any(not isinstance(value, str) or not value for value in policy.values())
            or environment["host_kind"] not in {"live_ui", "headless", "replay", "test"}
            or any(not isinstance(environment[key], str) or not environment[key] for key in (
                "connector_version", "connector_source_revision", "connector_module_version_id",
                "modset_status", "modset_fingerprint"))
            or not isinstance(environment["connector_artifact_sha256"], str)
            or len(environment["connector_artifact_sha256"]) != 64
            or any(not isinstance(support[key], list) or not support[key]
                   or any(not isinstance(item, str) or not item for item in support[key])
                   for key in ("game_versions", "game_commits", "interaction_kinds",
                               "action_verbs"))):
        raise BoundaryError("token_policy", "invalid_binding_destination_or_facts")
    envelope = _object_file(export_path / "model.json")
    if envelope.get("schema") != "stpd/stage1a-export-v1":
        raise BoundaryError("token_policy", "unsupported_export")
    identity = envelope.get("model_id")
    if not isinstance(identity, str) or len(identity) != 64:
        raise BoundaryError("token_policy", "export_identity_mismatch")
    from spireagent.artifact_contracts import Manifest

    artifact = Manifest.from_bytes(json_bytes(envelope.get("model")), identity)
    if artifact.kind != "model":
        raise BoundaryError("token_policy", "unsupported_export")
    if artifact.parameters.value().get("serializer") != IDENTITY:
        raise BoundaryError("token_policy", "text_menu_model_required")
    config = {"schema": CONFIG_SCHEMA, "export_path": str(export_path),
              "export_manifest_sha256": file_sha256(export_path / "model.json"),
              "model_id": identity,
              "qwen_snapshot": str(qwen_snapshot.resolve()) if qwen_snapshot else None}
    manifest = {
        "schema": "sts2.policy-runtime/policy-manifest-1", "manifest_id": manifest_id,
        "policy": policy,
        "adapter": {"id": "stpd-token-decision-adapter", "version": "1.0.0",
                    "protocol": PROTOCOL, "code_sha256": code_digest(root)},
        "artifact": {"id": identity,
                     "path": os.path.relpath(export_path / "model.json", manifest_path.parent),
                     "sha256": config["export_manifest_sha256"]},
        "representation": {"id": IDENTITY["profile"], "version": IDENTITY["version"],
                           "input_schema": SNAPSHOT_SCHEMA},
        "requirements": requirements, "support": support,
        "adapter_config": {"stage1a": {"code_digest_scope": CODE_SCOPE,
            "config": {"path": config_path.relative_to(root).as_posix(),
                       "sha256": hashlib.sha256(json_bytes(config)).hexdigest(),
                       "schema": CONFIG_SCHEMA}}},
        "claims": {"full_run": False, "selector": False, "catalog_filtered": False,
                   "creates_action_authority": False, "creates_native_operands": False},
    }
    if (set(requirements) != {
            "connector_protocol_version", "environment", "reads",
            "whole_decision_admission", "candidate_order_digest",
            "score_count_matches_candidate_count", "selected_index", "successor_required",
        } or requirements.get("reads") != []
            or requirements.get("candidate_order_digest")
            != "sha256-json-menu-action-id-order"
            or requirements.get("whole_decision_admission") is not True
            or requirements.get("score_count_matches_candidate_count") is not True
            or requirements.get("selected_index") is not True
            or requirements.get("successor_required") is not True):
        raise BoundaryError("token_policy", "text_menu_runtime_requirements_mismatch")
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


def code_digest(root: Path) -> str:
    # Conservative explicit scope, not a claimed minimal import closure. No models,
    # datasets, docs or private configuration enter it. Rebuild pins after code changes.
    paths = sorted(
        [
            *root.glob("stpd/**/*.py"),
            *root.glob("spireagent/**/*.py"),
            root / "uv.lock",
            root / "configs/v0/qwen/qwen3-0.6b-base-l2.json",
        ],
        key=lambda p: p.relative_to(root).as_posix(),
    )
    if not (root / "stpd/policy/token_port.py").is_file():
        raise BoundaryError("token_policy", "source_missing")
    rows = [{"path": p.relative_to(root).as_posix(), "sha256": file_sha256(p)} for p in paths]
    return hashlib.sha256(canonical_json(rows).encode()).hexdigest()


def validate(root: Path, config_path: Path, manifest_path: Path) -> tuple[dict, dict]:
    config, manifest = _object_file(config_path), _object_file(manifest_path)
    object_fields(
        config,
        {"schema", "export_path", "export_manifest_sha256", "model_id", "qwen_snapshot"},
        "token_policy.config",
    )
    if config["schema"] != CONFIG_SCHEMA:
        raise BoundaryError("token_policy", "unsupported_config")
    export = Path(config["export_path"])
    if not export.is_absolute():
        raise BoundaryError("token_policy", "absolute_export_path_required")
    pin = manifest.get("adapter_config", {}).get("stage1a", {})
    if (
        manifest.get("schema") != "sts2.policy-runtime/policy-manifest-1"
        or pin.get("code_digest_scope") != CODE_SCOPE
        or pin.get("config")
        != {
            "path": config_path.resolve().relative_to(root.resolve()).as_posix(),
            "sha256": file_sha256(config_path),
            "schema": CONFIG_SCHEMA,
        }
        or manifest.get("adapter")
        != {
            "id": "stpd-token-decision-adapter",
            "version": "1.0.0",
            "protocol": PROTOCOL,
            "code_sha256": code_digest(root),
        }
    ):
        raise BoundaryError("token_policy", "trusted_policy_identity_drift")
    artifact = manifest.get("artifact", {})
    if (
        artifact.get("id") != config["model_id"]
        or artifact.get("sha256") != config["export_manifest_sha256"]
        or (manifest_path.parent / artifact.get("path", "")).resolve()
        != (export / "model.json").resolve()
        or file_sha256(export / "model.json") != config["export_manifest_sha256"]
    ):
        raise BoundaryError("token_policy", "export_identity_drift")
    envelope = _object_file(export / "model.json")
    if envelope.get("model_id") != config["model_id"]:
        raise BoundaryError("token_policy", "export_model_mismatch")
    if manifest.get("requirements", {}).get("reads") != [] or manifest.get("claims") != {
        "full_run": False,
        "selector": False,
        "catalog_filtered": False,
        "creates_action_authority": False,
        "creates_native_operands": False,
    }:
        raise BoundaryError("token_policy", "unsupported_policy_claims")
    return config, manifest


def inspect(
    root: Path, entry: dict[str, Any], manifest: dict[str, Any], policy_config: dict[str, Any]
) -> dict[str, dict[str, str]]:
    try:
        config, checked = validate(
            root, _inside(root, entry["config"]), _inside(root, entry["manifest"])
        )
        if checked != manifest or config != policy_config:
            raise BoundaryError("token_policy", "metadata_changed")
        export = _object_file(Path(config["export_path"]) / "model.json")
        backend = export["model"]["parameters"]["config"]["device"]
        if backend not in {"cpu", "mps"}:
            raise BoundaryError("token_policy", "unsupported_backend")
        script = "import json,torch; print(json.dumps({'mps':torch.backends.mps.is_available()}))"
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=15)
        if result.returncode or (backend == "mps" and not json.loads(result.stdout)["mps"]):
            raise BoundaryError("token_policy", "backend_unavailable")
        return {
            "policy_identity": {"status": "pass"},
            "backend": {"status": "pass", "code": backend},
        }
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
    ) as error:
        return {
            "token_policy": {
                "status": "blocked",
                "code": getattr(error, "code", "metadata_or_backend_unavailable"),
            }
        }


def arguments(entry: dict[str, Any]) -> list[str]:
    return [
        "-m",
        "stpd.policy.token_port",
        "--config",
        entry["config"],
        "--manifest",
        entry["manifest"],
    ]
