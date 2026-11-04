"""Export an immutable RL checkpoint for the ordinary Managed evaluation owner."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stpd.p1.export import export_model, load_model
from stpd.rl.checkpoint_state import SCHEMA, construct_model, decode_state
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint


def export_checkpoint(store: Any, artifact_id: str, output: Path, *,
                      construction_package: Path | None = None) -> dict[str, Any]:
    """Raw theta only; CP16 requires its hash-bound original construction package.

    No learner, optimizer, World, model-version publication or seed offer occurs.
    The returned binding follows this CP through export and evaluation.
    """
    manifest = store.get_manifest(artifact_id)
    if manifest.kind != "checkpoint":
        raise ValueError("evaluation_checkpoint_required")
    payload = manifest.payload("checkpoint")
    raw = store.bytes(payload, maximum=payload.size)
    if manifest.parameters.value().get("schema") == SCHEMA:
        cp = decode_state(raw)
        model = construct_model(cp.construction)
        weights = cp.weights
        identity = {"run": cp.identity.run, "training_generation": cp.identity.training_generation,
                    "sequence": cp.identity.sequence, "updates": cp.counters.updates,
                    "version": cp.counters.mu_version}
    else:
        state = decode_checkpoint(raw)
        if state.get("schema") != "stpd/rl-segment-checkpoint-1":
            raise ValueError("supported_segment_evaluation_checkpoint_required")
        metadata = json.loads(state["metadata"].cpu().numpy().tobytes())
        if metadata["checkpoint_sequence"] != 16 or construction_package is None:
            raise ValueError("CP16_original_construction_package_required")
        expected = metadata["config"]["start_model_sha256"]
        descriptor = json.loads((construction_package / "model.json").read_bytes())
        if descriptor["package_sha256"] != expected:
            raise ValueError("CP16_original_construction_package_changed")
        model, _ = load_model(construction_package / "model.tensor-tree", expected)
        model.enable_rl4_heads(0)
        weights = state["weights"]
        identity = {"run": metadata["config"]["run"], "sequence": 16,
                    "updates": metadata["updates"], "version": metadata["version"]}
    model.load_state_dict(weights, strict=True)
    model.eval().requires_grad_(False)
    output.mkdir(parents=True, exist_ok=False)
    checkpoint = output / "raw-theta.tensor-tree"
    checkpoint.write_bytes(encode_checkpoint({"weights": model.state_dict()}))
    binding = {"checkpoint_artifact_id": artifact_id,
               "checkpoint_payload_sha256": payload.sha256,
               "checkpoint_producer": manifest.producer.to_dict(),
               **identity, "weights": "raw", "behavior_policy": {"mode": "argmax"}}
    descriptor = export_model(model, checkpoint, output / "package", binding)
    binding.update(model_sha256=descriptor["package_sha256"],
                   input_spec=descriptor["input_spec"], package_directory=str(output / "package"))
    (output / "checkpoint-binding.json").write_text(json.dumps(binding, indent=2) + "\n")
    return binding
