"""Run the research RL learner composed with the application's trajectory and model owners.

``stpd.rl.learner`` stays free of workbench imports; this entry point supplies
the Managed trajectory-shard reader and the detached P1 model import.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import threading
from pathlib import Path

from spireagent.json_boundary import BoundaryError
from stpd.rl.learner import LearnerConfig, LearnerIO

from .global_evidence_store import publish_detached_p1_model
from .managed_segment_store import read_segment
from .managed_trajectory_store import read_shard

# Historical readers/engineering fixtures keep the old composition available.
# The live CLI below exclusively selects the short-segment implementation.
IO = LearnerIO(read_shard=read_shard, publish_model=publish_detached_p1_model)


def convert_legacy_checkpoint(config: LearnerConfig, package: Path, work: Path, *,
                              source_generation: str, conversion_generation: str) -> dict:
    """Explicit zero-SGD CP16 migration through the ordinary Store/model codecs.

    The operator supplies CP16, its construction package and both generation
    names. No queue/input ancestry or old mu is restored, and no World is opened.
    """
    from dataclasses import asdict, replace

    from spireagent.artifact_contracts import Manifest
    from spireagent.json_boundary import FrozenObject
    from spireagent.rl_exchange import Exchange
    from spireagent.source import source_identity
    from stpd.p1.export import adapter_identity, input_spec, load_model, runtime_projection
    from stpd.p1.frame_compiler import FrameCompiler
    from stpd.rl.checkpoint_state import (
        SCHEMA,
        CP16ConversionInput,
        ModelConstruction,
        convert_cp16,
        encode_state,
        text_digest,
    )
    from stpd.rl.learner import ROOT
    from stpd.workers.checkpoint_codec import decode_checkpoint

    if (config.segments is None or not config.resume_checkpoint
            or not config.segments.training_generation
            or config.segments.training_generation in {source_generation, conversion_generation}):
        raise ValueError("explicit_CP16_and_distinct_conversion_and_learner_generations_required")
    target = work / "converted-learner.json"
    if target.exists():
        raise ValueError("fresh_conversion_output_required")
    exchange = Exchange(config.peer, config.run)
    original = exchange.store.get_manifest(config.resume_checkpoint)
    payload = original.payload("checkpoint")
    state = decode_checkpoint(exchange.store.bytes(payload, maximum=payload.size))
    metadata = json.loads(state["metadata"].cpu().numpy().tobytes())
    previous = metadata["config"]
    descriptor = json.loads((package / "model.json").read_bytes())
    if (not previous.get("start_model_sha256")
            or descriptor["package_sha256"] != previous["start_model_sha256"]):
        raise ValueError("CP16_original_construction_package_changed")
    model, _ = load_model(package / "model.tensor-tree", descriptor["package_sha256"])
    model.enable_rl4_heads(0)
    model.load_state_dict(state["weights"], strict=True)
    from stpd.p1.execution import MStdExecution

    model.execution = MStdExecution(implementation="experimental-packed-v1",
        microbatch_size=config.performance.microbatch_size if config.performance else 32,
        scene_attention_area=67108864, text_attention_area=67108864, cpu_threads=1)
    # The legacy segment optimizer inserts all action/text parameters first and
    # all RL heads second, preserving named_parameters iteration inside a group.
    names = (tuple(name for name, _ in model.named_parameters()
                   if not name.startswith("rl4_heads.")),
             tuple(name for name, _ in model.named_parameters() if name.startswith("rl4_heads.")))
    compiler = FrameCompiler(model.vocabulary)
    original_compiler = previous.get("segments", {}).get("input_compiler_identity")
    if (not isinstance(original_compiler, dict)
            or any(original_compiler.get(key) != compiler.identity[key]
                   for key in ("input_spec", "vocabulary_sha256"))):
        raise ValueError("CP16_original_vocabulary_or_input_spec_changed")
    # The new producer/codec is explicit; retain original semantic input binding,
    # and bind the continuation to this candidate's actual compiler bytes.
    config = replace(config, segments=replace(config.segments,
        input_compiler_identity=compiler.identity, input_pack_artifact_id=None))
    construction = ModelConstruction(model.architecture, model.vocabulary,
        input_spec(model.vocabulary, runtime_projection(model.vocabulary)), compiler.identity,
        adapter_identity(), {"execution": asdict(model.execution), "model_size": model.model_size,
                             "precision": "fp32"}, text_digest(state["weights"]))
    producer = source_identity(ROOT)
    cp = convert_cp16(CP16ConversionInput(state, construction, names,
        original.artifact_id, source_generation, conversion_generation, asdict(config),
        producer.to_dict()))
    converted = Manifest("checkpoint", producer,
        payloads=(exchange.store.put_bytes("checkpoint", encode_state(cp)),),
        parameters=FrozenObject.of({"schema": SCHEMA, "run": config.run,
            "training_generation": conversion_generation, "sequence": 16}))
    exchange.store.publish(converted)
    continuation = replace(config, start_package=None, start_artifact_id=None,
        start_model_sha256=None, resume_checkpoint=converted.artifact_id,
        resume_source=producer.to_dict())
    work.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"schema": "stpd/rl-learner-config-1",
                                 **asdict(continuation)}, indent=2) + "\n")
    result = {"original_checkpoint": original.artifact_id, "original_sha256": payload.sha256,
        "converted_checkpoint": converted.artifact_id, "config": str(target),
        "counters": asdict(cp.counters), "optimizer_calls": 0, "World_calls": 0}
    (work / "conversion-receipt.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--initialize-only", action="store_true",
                        help="publish the exact warmed start and full CP; no actors or SGD")
    parser.add_argument("--convert-cp16-package", type=Path,
                        help="explicit construction package; convert only, zero SGD/World")
    parser.add_argument("--source-generation")
    parser.add_argument("--conversion-generation")
    args = parser.parse_args()
    if args.convert_cp16_package:
        if not args.source_generation or not args.conversion_generation:
            parser.error("conversion requires --source-generation and --conversion-generation")
        os.umask(0o077)
        print(json.dumps(convert_legacy_checkpoint(LearnerConfig.read(args.config),
            args.convert_cp16_package, args.work.resolve(),
            source_generation=args.source_generation,
            conversion_generation=args.conversion_generation)))
        return
    os.umask(0o077)
    threading.current_thread().name = "rl-learner"
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)

    def stop(_number: int, _frame: object) -> None:
        # Defer graceful stop to the next update boundary; keep final publication alive.
        (work / "STOP").touch()

    previous = {number: signal.signal(number, stop)
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)}
    try:
        config = LearnerConfig.read(args.config)
        if config.segments is None:
            raise BoundaryError("rl_learner", "whole_episode_live_training_retired_use_segments")
        from stpd.rl.segment_learner import SegmentLearner, SegmentLearnerIO

        summary = SegmentLearner(config, work,
            SegmentLearnerIO(read_segment=read_segment,
                             publish_model=publish_detached_p1_model)).run(
                                 initialize_only=args.initialize_only)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
