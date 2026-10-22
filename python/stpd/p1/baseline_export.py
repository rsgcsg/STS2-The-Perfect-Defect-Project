"""One detached package/installation format for all three comparison Agents."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint

from .baseline_features import FEATURE_SPEC
from .baseline_spec import BaselineConfig
from .baselines import BaselineModel
from .data import digest, json_bytes
from .input import INPUT_SPEC

ADAPTER = "stpd-p1-baseline-agent"
PACKAGE_SCHEMA = "stpd/p1-baseline-model-1"
INPUT_PROJECTION = {
    "id": "stpd-p1-baseline-input",
    "version": "1",
    "profile": "p1-global-r1",
    "revision": INPUT_SPEC["revision"],
    "features": FEATURE_SPEC,
    "catalog": "complete unfiltered current legal catalog",
    "history": "none",
    "targets_in_input": False,
}


def adapter_identity() -> dict[str, str]:
    root = Path(__file__).parent
    closure = {
        name: digest((root / name).read_bytes())
        for name in (
            "baseline_agent.py",
            "agent_driver.py",
            "baseline_export.py",
            "baseline_spec.py",
            "baseline_features.py",
            "baselines.py",
            "numerics.py",
            "input.py",
            "data.py",
        )
    }
    closure["checkpoint_codec.py"] = digest(
        (root.parent / "workers/checkpoint_codec.py").read_bytes()
    )
    for name in ("policy_files.py", "json_boundary.py"):
        closure["spireagent/" + name] = digest(
            (root.parent.parent / "spireagent" / name).read_bytes()
        )
    return {
        "id": ADAPTER,
        "version": "1",
        "protocol": "sts2.policy-runtime/agent-session-ndjson-1",
        "code_sha256": digest(json_bytes(closure)),
    }


def input_spec(model: BaselineModel) -> dict[str, str]:
    return {
        "id": INPUT_PROJECTION["id"],
        "version": INPUT_PROJECTION["version"],
        "sha256": digest(
            json_bytes(
                {
                    "projection": INPUT_PROJECTION,
                    "algorithm": model.algorithm,
                    "categories": model.categories,
                }
            )
        ),
    }


def export_model(
    model: BaselineModel, checkpoint: Path, destination: Path, training_identity: dict[str, Any]
) -> dict[str, Any]:
    config = BaselineConfig.decode(training_identity["config"])
    if config.algorithm != model.algorithm:
        raise ValueError("baseline model/config algorithm differs")
    destination.mkdir(parents=True, exist_ok=False)
    state = model.state()
    raw = encode_checkpoint(
        {
            "schema": PACKAGE_SCHEMA,
            "model": state,
            "input_spec": input_spec(model),
            "input_projection": INPUT_PROJECTION,
            "training_identity": training_identity,
            "checkpoint_sha256": digest(checkpoint.read_bytes()),
        }
    )
    descriptor = {
        "schema": PACKAGE_SCHEMA,
        "package_sha256": digest(raw),
        "checkpoint_sha256": digest(checkpoint.read_bytes()),
        "input_spec": input_spec(model),
        "algorithm": model.algorithm,
        "parameters": model.weights.numel() if model.algorithm == "linear" else 0,
        "training_identity": training_identity,
        "agent_spec": {
            "id": "p1-" + model.algorithm,
            "version": "1",
            "task_spec": {"id": "current-public-semantic-choice", "version": "1"},
            "seed": config.seed,
            "state": "ACK-committed session RNG; new session resets seed; no opaque recovery",
            "selection": "uniform over full catalog"
            if model.algorithm == "random"
            else "maximum score; uniform among ties",
        },
    }
    (destination / "model.tensor-tree").write_bytes(raw)
    (destination / "model.json").write_bytes(json_bytes(descriptor))
    return descriptor


def load_model(path: Path, expected_sha256: str) -> tuple[BaselineModel, dict[str, Any]]:
    raw = path.read_bytes()
    if digest(raw) != expected_sha256:
        raise ValueError("baseline package checksum mismatch")
    payload = decode_checkpoint(raw)
    if (
        payload.get("schema") != PACKAGE_SCHEMA
        or payload.get("input_projection") != INPUT_PROJECTION
    ):
        raise ValueError("baseline package schema/projection mismatch")
    model = BaselineModel.from_state(payload["model"])
    config = BaselineConfig.decode(payload["training_identity"]["config"])
    if payload.get("input_spec") != input_spec(model) or config.algorithm != model.algorithm:
        raise ValueError("baseline package input/config mismatch")
    return model, payload


def register(
    package: Path,
    root: Path,
    environment_manifest: dict[str, Any],
    *,
    entry_id: str = "p1-baseline",
    label: str = "P1 baseline (CPU engineering)",
) -> dict[str, Any]:
    if root.exists():
        raise ValueError("private registration directory already exists")
    descriptor = json.loads((package / "model.json").read_bytes())
    model, payload = load_model(package / "model.tensor-tree", descriptor["package_sha256"])
    if (
        descriptor["input_spec"] != input_spec(model)
        or descriptor["checkpoint_sha256"] != payload["checkpoint_sha256"]
        or descriptor["algorithm"] != model.algorithm
        or descriptor["training_identity"] != payload["training_identity"]
    ):
        raise ValueError("baseline descriptor differs from package")
    manifest = copy.deepcopy(environment_manifest)
    if (
        manifest.get("schema") != "sts2.policy-runtime/agent-manifest-1"
        or manifest.get("input", {}).get("profile") != "p1-global-r1"
        or manifest["input"].get("global", {}).get("revision") != INPUT_SPEC["revision"]
    ):
        raise ValueError("baseline registration requires current P1 environment manifest")
    root.mkdir(parents=True)
    path = (root / "model.tensor-tree").resolve()
    shutil.copyfile(package / "model.tensor-tree", path)
    manifest.update(
        manifest_id="p1-" + model.algorithm + "-" + descriptor["package_sha256"][:16],
        agent={
            "id": "p1-" + model.algorithm,
            "version": "1",
            "provider": "stpd",
            "architecture": "p1-" + model.algorithm,
        },
        adapter=adapter_identity(),
        artifact={
            "id": descriptor["package_sha256"],
            "path": str(path),
            "sha256": descriptor["package_sha256"],
        },
    )
    manifest["input"].update(
        input_spec=input_spec(model),
        state_format_version="baseline-session-rng-1",
        state_recovery={"mode": "none", "max_state_bytes": 0, "model_bindings": []},
    )
    config = {
        "schema": "stpd/p1-baseline-installation-1",
        "package_path": str(path),
        "package_sha256": descriptor["package_sha256"],
        "package_model_id": descriptor["package_sha256"],
        "weights_sha256": digest(encode_checkpoint({"model": payload["model"]})),
        "checkpoint_sha256": payload["checkpoint_sha256"],
        "agent_spec": descriptor["agent_spec"],
        "seed": descriptor["agent_spec"]["seed"],
        "input_spec": input_spec(model),
        "package_input_spec": input_spec(model),
        "input_projection": INPUT_PROJECTION,
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
