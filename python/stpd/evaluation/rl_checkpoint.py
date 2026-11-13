"""Export an immutable RL checkpoint for the ordinary Managed evaluation owner."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stpd.p1.export import adapter_identity, export_model, load_model
from stpd.rl.checkpoint_state import READABLE_SCHEMAS, construct_model, decode_state
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint


def require_source_producer(producer: dict[str, Any],
                            recorded: dict[str, Any] | None = None) -> None:
    """A CP is evaluated only when a real source checkout produced it.

    The fast-chain launcher's ``producer_identity = "placeholder"`` writes a scratch
    producer (repository ``*-scratch``, revision ``a`` * 40): such CPs are void by
    design and never become an evaluated model. A self-contained CP also records its
    producer inside the state; it must be the manifest's producer.
    """
    from spireagent.source import REPOSITORY_ALIASES

    revision = producer.get("source_revision")
    if (producer.get("repository") not in REPOSITORY_ALIASES
            or not isinstance(revision, str) or len(set(revision)) <= 1):
        raise ValueError("checkpoint_producer_not_a_source_checkout")
    if recorded is not None and recorded != producer:
        raise ValueError("checkpoint_producer_binding_changed")


def export_checkpoint(store: Any, artifact_id: str, output: Path, *,
                      construction_package: Path | None = None) -> dict[str, Any]:
    """Raw theta only; CP16 requires its hash-bound original construction package.

    No learner, optimizer, World, model-version publication or seed offer occurs.
    The returned binding follows this CP through export and evaluation. Training-only
    settings in the CP (`config`: performance levers such as `prepared_kernels`, data
    plane shards, optimizer, RNG, input pipeline) do not enter the evaluated model.
    """
    manifest = store.get_manifest(artifact_id)
    if manifest.kind != "checkpoint":
        raise ValueError("evaluation_checkpoint_required")
    payload = manifest.payload("checkpoint")
    raw = store.bytes(payload, maximum=payload.size)
    if manifest.parameters.value().get("schema") in READABLE_SCHEMAS:
        cp = decode_state(raw, expected_schema=manifest.parameters.value()["schema"])
        require_source_producer(manifest.producer.to_dict(), cp.producer)
        model = construct_model(cp.construction)
        weights = cp.weights
        identity = {"run": cp.identity.run, "training_generation": cp.identity.training_generation,
                    "sequence": cp.identity.sequence, "updates": cp.counters.updates,
                    "version": cp.counters.mu_version}
    else:
        state = decode_checkpoint(raw)
        if state.get("schema") != "stpd/rl-segment-checkpoint-1":
            raise ValueError("supported_segment_evaluation_checkpoint_required")
        require_source_producer(manifest.producer.to_dict())
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


def inspect_checkpoint(store: Any, artifact_id: str, *, run: str | None = None,
                       generation: str | None = None) -> dict[str, Any]:
    """Dry mode: run every check `export_checkpoint` applies, each on its own; write nothing.

    Reports exactly which checks refuse this CP (a failed check does not hide later
    ones whose inputs are still available). `informational` items never refuse: the
    evaluation runs its own frozen adapter, pinned by the execution file.
    """
    checks: list[dict[str, Any]] = []
    info: dict[str, Any] = {}

    def check(name: str, step: Any) -> Any:
        try:
            value = step()
        except Exception as error:  # noqa: BLE001 -- reported, never swallowed silently
            checks.append({"check": name, "ok": False,
                           "refusal": str(error) or type(error).__name__,
                           "error_type": type(error).__name__})
            return None
        checks.append({"check": name, "ok": True})
        return value if value is not None else True

    manifest = check("manifest_readable", lambda: store.get_manifest(artifact_id))
    if manifest is None:
        return {"artifact_id": artifact_id, "evaluable": False, "checks": checks}

    def kind() -> None:
        if manifest.kind != "checkpoint":
            raise ValueError("evaluation_checkpoint_required")

    check("kind_checkpoint", kind)
    schema = manifest.parameters.value().get("schema")
    info.update(schema=schema, producer=manifest.producer.to_dict(),
                parameters={k: v for k, v in manifest.parameters.value().items()
                            if k in ("run", "training_generation", "sequence", "version")})

    def readable() -> None:
        if schema not in READABLE_SCHEMAS:
            raise ValueError("supported_self_contained_checkpoint_schema_required")

    if check("schema_self_contained", readable) is None:
        return {"artifact_id": artifact_id, "evaluable": False, "checks": checks, "info": info}
    raw = check("payload_digest", lambda: store.bytes(manifest.payload("checkpoint"),
                                                       maximum=manifest.payload("checkpoint").size))
    cp = (check("decode_text_digest_optimizer_order",
                lambda: decode_state(raw, expected_schema=schema)) if raw is not None else None)
    check("producer_real_source_identity",
          lambda: require_source_producer(manifest.producer.to_dict()))
    if cp is not None:
        def same_producer() -> None:
            if cp.producer != manifest.producer.to_dict():
                raise ValueError("checkpoint_producer_binding_changed")

        check("producer_state_matches_manifest", same_producer)
        model = check("model_structure_and_input_spec", lambda: construct_model(cp.construction))
        if model is not None:
            check("strict_weight_load", lambda: model.load_state_dict(cp.weights, strict=True))

        def same_run() -> None:
            if run is not None and cp.identity.run != run:
                raise ValueError("checkpoint_evaluation_run_changed")
            if generation is not None and cp.identity.training_generation != generation:
                raise ValueError("checkpoint_training_generation_changed")

        check("run_and_generation_match", same_run)
        info.update(run=cp.identity.run, training_generation=cp.identity.training_generation,
                    sequence=cp.identity.sequence, updates=cp.counters.updates,
                    input_spec=cp.construction.input_spec,
                    training_only_config_keys=sorted(k for k in cp.config
                                                     if k in ("performance", "segments")),
                    informational={
                        "training_adapter_code_sha256": cp.construction.adapter.get("code_sha256"),
                        "this_code_adapter_code_sha256": adapter_identity()["code_sha256"],
                        "note": "the evaluation runs the adapter pinned by its execution file"})
    return {"artifact_id": artifact_id, "evaluable": all(c["ok"] for c in checks),
            "refusals": [c for c in checks if not c["ok"]], "checks": checks, "info": info}
