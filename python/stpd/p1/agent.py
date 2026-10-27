"""Learned stateless P1 Agent on the existing consume/ACK/next Runtime port."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, TextIO

import torch

from .data import digest
from .export import adapter_identity, bound_projection, input_spec, load_model
from .input import INPUT_SPEC, project
from .model import MStd

SCHEMA = "sts2.policy-runtime/agent-session-1"


def installation(
    config_path: Path, manifest_path: Path, owner: Path
) -> tuple[dict[str, Any], dict[str, Any], MStd, dict[str, Any]]:
    from spireagent.policy_files import _object_file, bound_path

    config = _object_file(bound_path(owner, config_path, stage="native_agent_support"))
    manifest = _object_file(bound_path(owner, manifest_path, stage="native_agent_support"))
    path = bound_path(owner, Path(config["package_path"]), stage="native_agent_support")
    model, payload = load_model(path, config["package_sha256"])
    try:
        projection = bound_projection(model.vocabulary, config.get("input_projection"))
    except ValueError as error:
        raise ValueError("P1 model installation binding differs") from error
    expected_artifact = {
        "id": config["package_sha256"],
        "path": str(path),
        "sha256": config["package_sha256"],
    }
    if (
        config["schema"] != "stpd/p1-mstd-installation-1"
        or manifest.get("adapter") != adapter_identity()
        or manifest.get("artifact") != expected_artifact
        or manifest.get("input", {}).get("input_spec") != input_spec(model.vocabulary, projection)
        or manifest["input"].get("profile") != "p1-global-r1"
        or not isinstance(manifest["input"].get("global"), dict)
        or manifest["input"]["global"].get("revision") != INPUT_SPEC["revision"]
        or manifest["input"].get("state_recovery")
        != {"mode": "none", "max_state_bytes": 0, "model_bindings": []}
        or config["checkpoint_sha256"] != payload["checkpoint_sha256"]
        or config["weights_sha256"] != digest(encode_weights(payload["weights"]))
        or config["package_model_id"] != config["package_sha256"]
        or config.get("package_input_spec") != input_spec(model.vocabulary)
        or config["input_spec"] != manifest["input"]["input_spec"]
    ):
        raise ValueError("P1 model installation binding differs")
    return config, manifest, model, payload


def encode_weights(weights: dict[str, Any]) -> bytes:
    from stpd.workers.checkpoint_codec import encode_checkpoint

    return encode_checkpoint({"weights": weights})


def serve(
    config_path: Path,
    manifest_path: Path,
    owner: Path,
    source: TextIO = sys.stdin,
    destination: TextIO = sys.stdout,
    *,
    local_inference: Path | None = None,
) -> None:
    config, manifest, model, _ = installation(config_path, manifest_path, owner)
    from .agent_driver import serve_session

    def score(observation):
        frame = project(observation, input_projection=config["input_projection"])
        with torch.inference_mode():
            return model(frame).tolist() if frame.choices else []

    client = None
    if local_inference is not None:
        import json

        from spireagent.policy_files import bound_path

        from .local_inference import LocalInferenceClient

        path = bound_path(owner, local_inference, stage="native_agent_support")
        attachment = json.loads(path.read_bytes())
        client = LocalInferenceClient(
            attachment, config["package_sha256"], config["input_projection"])
        # Installation still verifies the normal model binding; only scoring is
        # shared. Each actor retains its own original protocol state.
        del model
    try:
        serve_session(
            manifest,
            score if client is None else client,
            lambda scores: max(range(len(scores)), key=scores.__getitem__),
            source,
            destination,
        )
    finally:
        if client is not None:
            client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--binding-root", type=Path, required=True)
    parser.add_argument("--local-inference", type=Path)
    args = parser.parse_args()
    # The CLI owns its dedicated process; the reusable serve() does not.
    torch.set_num_threads(2)
    serve(args.config.resolve(), args.manifest.resolve(), args.binding_root.resolve(),
          local_inference=args.local_inference.resolve() if args.local_inference else None)


if __name__ == "__main__":
    main()
