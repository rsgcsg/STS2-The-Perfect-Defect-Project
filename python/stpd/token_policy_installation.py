"""Trusted Stage 1a installation metadata; never loads Torch or model weights here."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, object_fields
from spireagent.package_identity import file_sha256
from spireagent.policy_files import _inside, _object_file

CONFIG_SCHEMA = "stpd/token-policy-config-v1"
CODE_SCOPE = "python-owner-source-and-lock-v1"
PROTOCOL = "sts2.policy-runtime/decision-only-ndjson-1"


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
