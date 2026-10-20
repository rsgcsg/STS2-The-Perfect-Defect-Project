"""Workbench support for its code-owned P1 M-std adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError

from .agent import installation


def validate(root, config_path, manifest_path, *, binding_root=None):
    config, manifest, _, _ = installation(
        config_path, manifest_path, (binding_root or root).resolve()
    )
    return config, manifest


def arguments(entry):
    return [
        "-m",
        "stpd.p1.agent",
        "--manifest",
        entry["manifest"],
        "--config",
        entry["config"],
        "--binding-root",
        entry["binding_root"],
    ]


def inspect(root, entry, manifest, policy_config, *, binding_root=None):
    try:
        owner = (binding_root or root).resolve()
        checked_config, checked_manifest = validate(
            owner, owner / entry["config"], owner / entry["manifest"]
        )
        if checked_config != policy_config or checked_manifest != manifest:
            raise ValueError("P1 installation metadata changed")
        return {
            "policy_identity": {"status": "pass"},
            "backend": {"status": "pass", "code": "local_cpu_p1_mstd"},
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {
            "policy_identity": {
                "status": "blocked",
                "code": error.code
                if isinstance(error, BoundaryError)
                else "p1_mstd_installation_unavailable",
            }
        }


def metadata(owner: Path, config: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    _, _, model, payload = installation(owner / "config.json", owner / "manifest.json", owner)
    return {
        "schema": "stpd/p1-mstd-model-1",
        "input_spec": manifest["input"]["input_spec"],
        "checkpoint_sha256": payload["checkpoint_sha256"],
        "parameters": sum(x.numel() for x in model.parameters()),
    }
