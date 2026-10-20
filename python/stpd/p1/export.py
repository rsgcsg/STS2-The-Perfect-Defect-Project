"""Detached model package and private installation on the existing P1 profile."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import torch

from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint

from .data import digest, json_bytes
from .input import INPUT_SPEC
from .model import ARCHITECTURE, MStd

ADAPTER = "stpd-p1-mstd-agent"
PROTOCOL = "sts2.policy-runtime/agent-session-ndjson-1"
PACKAGE_SCHEMA = "stpd/p1-mstd-model-1"


def adapter_identity() -> dict[str, str]:
    root = Path(__file__).parent
    closure = {
        name: digest((root / name).read_bytes())
        for name in ("agent.py", "export.py", "input.py", "model.py", "data.py")
    }
    closure["checkpoint_codec.py"] = digest(
        (root.parent / "workers/checkpoint_codec.py").read_bytes()
    )
    platform = root.parent.parent / "spireagent"
    for name in ("policy_files.py", "json_boundary.py"):
        closure["spireagent/" + name] = digest((platform / name).read_bytes())
    return {
        "id": ADAPTER,
        "version": "1",
        "protocol": PROTOCOL,
        "code_sha256": digest(json_bytes(closure)),
    }


def input_spec(vocabulary: dict[str, Any]) -> dict[str, str]:
    return {
        "id": INPUT_SPEC["id"],
        "version": INPUT_SPEC["version"],
        "sha256": digest(
            json_bytes(
                {"projection": INPUT_SPEC, "vocabulary_sha256": digest(json_bytes(vocabulary))}
            )
        ),
    }


def export_model(
    model: MStd, checkpoint: Path, destination: Path, training_identity: dict[str, Any]
) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=False)
    state = dict(model.state_dict())
    if not all(bool(torch.isfinite(value).all()) for value in state.values()):
        raise ValueError("cannot export non-finite weights")
    raw = encode_checkpoint(
        {
            "schema": PACKAGE_SCHEMA,
            "architecture": ARCHITECTURE,
            "vocabulary": model.vocabulary,
            "input_spec": input_spec(model.vocabulary),
            "training_identity": training_identity,
            "checkpoint_sha256": digest(checkpoint.read_bytes()),
            "weights": state,
        }
    )
    path = destination / "model.tensor-tree"
    path.write_bytes(raw)
    descriptor = {
        "schema": PACKAGE_SCHEMA,
        "package_sha256": digest(raw),
        "checkpoint_sha256": digest(checkpoint.read_bytes()),
        "input_spec": input_spec(model.vocabulary),
        "architecture": ARCHITECTURE,
        "parameters": sum(x.numel() for x in model.parameters()),
        "training_identity": training_identity,
        "agent_spec": {
            "id": "p1-mstd",
            "version": "1",
            "task_spec": {"id": "current-public-semantic-choice", "version": "1"},
            "selection": "argmax over full current legal catalog",
        },
    }
    (destination / "model.json").write_bytes(json_bytes(descriptor))
    return descriptor


def load_model(path: Path, expected_sha256: str) -> tuple[MStd, dict[str, Any]]:
    raw = path.read_bytes()
    if digest(raw) != expected_sha256:
        raise ValueError("model package checksum mismatch")
    value = decode_checkpoint(raw)
    if (
        value.get("schema") != PACKAGE_SCHEMA
        or value.get("architecture") != ARCHITECTURE
        or value.get("input_spec") != input_spec(value["vocabulary"])
        or value["vocabulary"]["input_spec"] != INPUT_SPEC
    ):
        raise ValueError("model package architecture/InputSpec mismatch")
    # Inference construction must not alter process random state.
    with torch.random.fork_rng(devices=[]):
        model = MStd(value["vocabulary"])
    expected = model.state_dict()
    weights = value["weights"]
    if set(expected) != set(weights) or any(
        expected[k].shape != weights[k].shape or expected[k].dtype != weights[k].dtype
        for k in expected
    ):
        raise ValueError("model parameter structure differs")
    model.load_state_dict(weights, strict=True)
    return model.eval(), value


def register(
    package: Path, root: Path, environment_manifest: dict[str, Any], *,
    entry_id: str = "t0-p1-mstd", label: str = "T0 P1 M-std (CPU engineering)",
) -> dict[str, Any]:
    """Environment owner supplies the existing exact P1 environment envelope.

    This function selects no Host, starts no process and changes no shared registry.
    Recovery model_bindings are empty because M-std has no recurrent state; the
    immutable model dependency is in artifact/config and AgentComposition.
    """
    if root.exists():
        raise ValueError("private registration directory already exists")
    root.mkdir(parents=True)
    descriptor = json.loads((package / "model.json").read_bytes())
    path = (root / "model.tensor-tree").resolve()
    shutil.copyfile(package / "model.tensor-tree", path)
    model, payload = load_model(path, descriptor["package_sha256"])
    manifest = copy.deepcopy(environment_manifest)
    if (
        manifest.get("schema") != "sts2.policy-runtime/agent-manifest-1"
        or manifest.get("input", {}).get("profile") != "p1-global-r1"
    ):
        raise ValueError("registration needs an owner-supplied P1 environment manifest")
    manifest.update(
        manifest_id="p1-mstd-" + descriptor["package_sha256"][:16],
        agent={
            "id": "p1-mstd",
            "version": "1",
            "provider": "stpd",
            "architecture": ARCHITECTURE["id"],
        },
        adapter=adapter_identity(),
        artifact={
            "id": descriptor["package_sha256"],
            "path": str(path),
            "sha256": descriptor["package_sha256"],
        },
    )
    manifest["input"].update(
        input_spec=input_spec(model.vocabulary),
        state_format_version="mstd-stateless-1",
        state_recovery={"mode": "none", "max_state_bytes": 0, "model_bindings": []},
    )
    config = {
        "schema": "stpd/p1-mstd-installation-1",
        "package_path": str(path),
        "package_sha256": descriptor["package_sha256"],
        "package_model_id": descriptor["package_sha256"],
        "weights_sha256": digest(encode_checkpoint({"weights": payload["weights"]})),
        "checkpoint_sha256": payload["checkpoint_sha256"],
        "agent_spec": descriptor["agent_spec"],
        "input_spec": descriptor["input_spec"],
    }
    (root / "manifest.json").write_bytes(json_bytes(manifest))
    (root / "config.json").write_bytes(json_bytes(config))
    entry = {
        "id": entry_id,
        "label": label,
        "adapter": ADAPTER,
        "runtime_profile": "p1-global-r1",
        "manifest": "manifest.json",
        "config": "config.json",
    }
    (root / "token-policies-v1.json").write_bytes(
        json_bytes({"schema": "stpd/local-token-policies-v1", "policies": [entry]})
    )
    return entry
