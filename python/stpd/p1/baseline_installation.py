"""Trusted application hooks for the unified comparison-Agent adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .baseline_agent import installation
from .baseline_export import PACKAGE_SCHEMA


def validate(root, config_path, manifest_path, *, binding_root=None):
    config, manifest, _, _ = installation(
        config_path, manifest_path, (binding_root or root).resolve()
    )
    return config, manifest


def arguments(entry):
    return [
        "-m",
        "stpd.p1.baseline_agent",
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
        config, checked = validate(owner, owner / entry["config"], owner / entry["manifest"])
        if config != policy_config or checked != manifest:
            raise ValueError("baseline installation metadata changed")
        return {
            "policy_identity": {"status": "pass"},
            "backend": {"status": "pass", "code": "local_cpu_p1_baseline"},
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "policy_identity": {"status": "blocked", "code": "p1_baseline_installation_unavailable"}
        }


def metadata(owner: Path, config: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    checked, checked_manifest, model, payload = installation(
        owner / "config.json", owner / "manifest.json", owner
    )
    if config != checked or manifest != checked_manifest:
        raise ValueError("baseline installation metadata changed")
    return {
        "schema": PACKAGE_SCHEMA,
        "algorithm": model.algorithm,
        "input_spec": manifest["input"]["input_spec"],
        "input_projection": config["input_projection"],
        "package_input_spec": config["package_input_spec"],
        "checkpoint_sha256": payload["checkpoint_sha256"],
        "parameters": model.weights.numel() if model.algorithm == "linear" else 0,
    }
