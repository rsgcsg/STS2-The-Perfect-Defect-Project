"""Complete P1 comparison Agent using the shared consume/ACK driver."""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Any, TextIO

import torch

from stpd.workers.checkpoint_codec import encode_checkpoint

from .agent_driver import serve_session
from .baseline_export import INPUT_PROJECTION, adapter_identity, input_spec, load_model
from .baselines import BaselineModel
from .data import digest


def installation(
    config_path: Path, manifest_path: Path, owner: Path
) -> tuple[dict[str, Any], dict[str, Any], BaselineModel, dict[str, Any]]:
    from spireagent.policy_files import _object_file, bound_path

    config = _object_file(bound_path(owner, config_path, stage="native_agent_support"))
    manifest = _object_file(bound_path(owner, manifest_path, stage="native_agent_support"))
    path = bound_path(owner, Path(config["package_path"]), stage="native_agent_support")
    model, payload = load_model(path, config["package_sha256"])
    if (
        config["schema"] != "stpd/p1-baseline-installation-1"
        or manifest.get("adapter") != adapter_identity()
        or manifest.get("artifact")
        != {"id": config["package_sha256"], "path": str(path), "sha256": config["package_sha256"]}
        or manifest.get("input", {}).get("input_spec") != input_spec(model)
        or manifest["input"].get("profile") != "p1-global-r1"
        or manifest["input"].get("global", {}).get("revision") != INPUT_PROJECTION["revision"]
        or manifest["input"].get("state_recovery")
        != {"mode": "none", "max_state_bytes": 0, "model_bindings": []}
        or manifest["input"].get("state_format_version") != "baseline-session-rng-1"
        or config.get("input_projection") != INPUT_PROJECTION
        or config.get("input_spec") != input_spec(model)
        or config.get("package_input_spec") != input_spec(model)
        or config.get("package_model_id") != config["package_sha256"]
        or config["checkpoint_sha256"] != payload["checkpoint_sha256"]
        or config["weights_sha256"] != digest(encode_checkpoint({"model": payload["model"]}))
        or config.get("seed") != payload["training_identity"]["config"]["seed"]
        or type(config.get("seed")) is not int
        or config.get("agent_spec", {}).get("seed") != config["seed"]
    ):
        raise ValueError("baseline installation binding differs")
    return config, manifest, model, payload


def serve(
    config_path: Path,
    manifest_path: Path,
    owner: Path,
    source: TextIO = sys.stdin,
    destination: TextIO = sys.stdout,
) -> None:
    config, manifest, model, _ = installation(config_path, manifest_path, owner)
    rng = random.Random(config["seed"])

    def choose(scores: list[float]) -> int:
        candidates = (
            list(range(len(scores)))
            if model.algorithm == "random"
            else [i for i, value in enumerate(scores) if value == max(scores)]
        )
        return rng.choice(candidates)

    serve_session(
        manifest,
        model.scores,
        choose,
        source,
        destination,
        consumption_prefix="baseline-",
        reuse_selection=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--binding-root", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    serve(args.config.resolve(), args.manifest.resolve(), args.binding_root.resolve())


if __name__ == "__main__":
    main()
