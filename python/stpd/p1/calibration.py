"""Bounded real-TRAIN calibration through verified P1 inputs and the shared update.

This produces performance evidence, never a formal Run result or selected Model.
Source checkpoints are immutable parents. Clones are explicitly cross-source
experiments, not identity-compatible resumes of their source Run.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import platform
import statistics
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, digest, object_fields
from spireagent.source import source_identity
from spireagent.storage.store import ArtifactStore

from .data import json_bytes
from .execution import MStdExecution
from .input import Entity, Frame, project
from .spec import MStdConfig

INPUT_SCHEMA = "stpd/p1-TRAIN-calibration-input-1"
REPORT_SCHEMA = "stpd/p1-TRAIN-calibration-report-1"


def prepare(
    store: ArtifactStore, run_id: str, checkpoint_id: str, producer: Producer,
    operation_id: str, *, updates: int = 4,
) -> Manifest:
    """Caller records original Curation TRAIN use before this input read."""
    from stpd.workers.p1_run import verify_p1_checkpoint

    from .data import digest as payload_digest
    from .workload import verify_p1_run

    digest(operation_id, "p1_calibration.operation_id", length=32)
    if type(updates) is not int or not 2 <= updates <= 8:
        raise BoundaryError("p1_calibration", "bounded_two_to_eight_updates_required")
    source = store.get_manifest(run_id)
    workload = verify_p1_run(store, run_id, source.producer)
    checkpoint = verify_p1_checkpoint(store, checkpoint_id, workload)
    state = checkpoint.state
    train_ids = sorted(p["point_id"] for p in workload.reader.selection["points"]
                       if p["split"] == "train")
    expected_points = state["identity"]["partitions"]["train"]["points_sha256"]
    if payload_digest(json_bytes(train_ids)) != expected_points:
        raise BoundaryError("p1_calibration", "source_sampler_partition_mismatch")
    cursor = state["sampler"]["cursor"]
    selected = state["sampler"]["order"][cursor:cursor + updates * 64]
    if len(selected) != updates * 64:
        raise BoundaryError("p1_calibration", "complete_upcoming_optimizer_batches_required")
    ordered_ids = [train_ids[index] for index in selected]
    from .weighting import point_weights

    policy = workload.training.parameters.value().get("loss_weighting")
    weights = point_weights(policy, workload.reader.selection) if policy is not None else None
    wanted = set(ordered_ids)
    rows = {}
    for reference, observation in workload.reader.iter_points():
        if reference["split"] == "train" and reference["point_id"] in wanted:
            frame = project(observation, input_projection=workload.vocabulary["input_spec"])
            target = [c["choice_id"] for c in frame.choices].index(reference["chosen"])
            rows[reference["point_id"]] = {"point_id": reference["point_id"],
                                            "frame": asdict(frame), "target": target,
                                            "split": "train"}
    if set(rows) != wanted:
        raise BoundaryError("p1_calibration", "source_TRAIN_selection_incomplete")
    samples = json_bytes([rows[point] for point in ordered_ids])
    payloads = (
        store.put_payload("samples", io.BytesIO(samples), "application/json"),
        store.put_payload("vocabulary", io.BytesIO(json_bytes(workload.vocabulary)),
                          "application/json"),
        store.put_payload("source_checkpoint", io.BytesIO(checkpoint.raw),
                          "application/vnd.stpd.tensor-tree"),
    )
    manifest = Manifest(
        "analysis", producer, (Parent("run", run_id), Parent("checkpoint", checkpoint_id),
                               Parent("snapshot", workload.snapshot.artifact_id)), payloads,
        FrozenObject.of({"schema": INPUT_SCHEMA, "operation_id": operation_id,
                         "source_producer": source.producer.to_dict(),
                         "source_checkpoint_sha256": payload_digest(checkpoint.raw),
                         "source_step": state["step"], "source_config": workload.config.to_dict(),
                         "planned_steps": state["identity"]["planned_steps"],
                         "updates": updates, "batch_size": 64, "sample_count": len(rows),
                         "point_ids": ordered_ids, "split": "train",
                         "input_spec": state["identity"]["input_spec"],
                         **({"sample_weights": [weights[point] for point in ordered_ids]}
                            if weights is not None else {}),
                         "purpose": "engineering_performance_calibration",
                         "formal_resume": False, "publishes_model": False,
                         "DEV_consumed": False, "TEST_consumed": False,
                         "consumed_means": "model_forward_gradient_or_selection",
                         "snapshot_validation_reads_all_partitions": True,
                         "DEV_TEST_frames_scored": False, "DEV_TEST_gradients": False}),
    )
    store.publish(manifest)
    return manifest


def read_input(store: ArtifactStore, identity: str) -> tuple[Manifest, list[Frame], list[int],
                                                          dict[str, Any], dict[str, Any]]:
    from stpd.workers.checkpoint_codec import decode_checkpoint

    manifest = store.get_manifest(identity)
    info = manifest.parameters.value()
    if (manifest.kind != "analysis" or info.get("schema") != INPUT_SCHEMA
            or {p.role for p in manifest.parents} != {"run", "checkpoint", "snapshot"}
            or {p.role for p in manifest.payloads} != {"samples", "vocabulary", "source_checkpoint"}
            or info.get("split") != "train" or info.get("DEV_consumed") is not False
            or info.get("TEST_consumed") is not False):
        raise BoundaryError("p1_calibration", "calibration_input_binding_mismatch")
    raw = b"".join(store.read_payload(manifest.payload("source_checkpoint")))
    if manifest.payload("source_checkpoint").sha256 != info.get("source_checkpoint_sha256"):
        raise BoundaryError("p1_calibration", "source_checkpoint_payload_mismatch")
    state = decode_checkpoint(raw)
    source_cp = store.get_manifest(manifest.parent("checkpoint"))
    source_run = store.get_manifest(manifest.parent("run"))
    source_training = store.get_manifest(source_run.parent("training_input"))
    if (source_cp.kind != "checkpoint" or source_cp.parent("run") != source_run.artifact_id
            or source_cp.producer != Producer.decode(info["source_producer"])
            or source_run.producer != source_cp.producer
            or source_cp.payload("checkpoint").sha256 != info["source_checkpoint_sha256"]
            or state["step"] != info["source_step"]
            or state["identity"]["snapshot_id"] != manifest.parent("snapshot")
            or source_training.parent("source") != manifest.parent("snapshot")
            or state["identity"]["input_spec"] != info["input_spec"]
            or state["identity"]["config"] != info["source_config"]):
        raise BoundaryError("p1_calibration", "source_parent_binding_mismatch")
    vocabulary = json.loads(b"".join(store.read_payload(manifest.payload("vocabulary"))))
    if manifest.payload("vocabulary").sha256 != state["identity"]["vocabulary_sha256"]:
        raise BoundaryError("p1_calibration", "source_vocabulary_mismatch")
    rows = json.loads(b"".join(store.read_payload(manifest.payload("samples"))))
    if ([row["point_id"] for row in rows] != info["point_ids"]
            or len(rows) != info["sample_count"] or len(rows) != info["updates"] * 64
            or len({row["point_id"] for row in rows}) != len(rows)
            or any(row["split"] != "train" for row in rows)):
        raise BoundaryError("p1_calibration", "TRAIN_batch_membership_mismatch")
    snapshot = store.get_manifest(manifest.parent("snapshot"))
    selection = json.loads(b"".join(store.read_payload(snapshot.payload("selection"))))
    train_ids = sorted(point["point_id"] for point in selection["points"]
                       if point["split"] == "train")
    from .weighting import point_weights

    policy = source_training.parameters.value().get("loss_weighting")
    weights = point_weights(policy, selection) if policy is not None else None
    expected_weights = ([weights[point] for point in info["point_ids"]]
                        if weights is not None else None)
    if info.get("sample_weights") != expected_weights:
        raise BoundaryError("p1_calibration", "source_TRAIN_weights_mismatch")
    cursor = state["sampler"]["cursor"]
    indices = state["sampler"]["order"][cursor:cursor + len(rows)]
    if [train_ids[index] for index in indices] != info["point_ids"]:
        raise BoundaryError("p1_calibration", "source_sampler_TRAIN_membership_mismatch")
    frames, targets = [], []
    for row in rows:
        value = row["frame"]
        frame = Frame([Entity(**entity) for entity in value["entities"]],
                      value["choices"], [(tuple(s) if isinstance(s, list) else s,
                                          tuple(t) if isinstance(t, list) else t)
                                         for s, t in value["roles"]])
        target = row["target"]
        if type(target) is not int or not 0 <= target < len(frame.choices):
            raise BoundaryError("p1_calibration", "target_outside_complete_catalog")
        frames.append(frame)
        targets.append(target)
    return manifest, frames, targets, vocabulary, state


def _lr(config: MStdConfig, planned_steps: int, step: int) -> float:
    warmup = max(1, math.ceil(planned_steps * config.warmup_fraction))
    if step < warmup:
        return config.learning_rate * (step + 1) / warmup
    return config.learning_rate * (1 + math.cos(math.pi * (step + 1 - warmup)
                                                / (planned_steps - warmup))) / 2


def measure(
    store: ArtifactStore, input_id: str, producer: Producer, plan: object, *,
    device: str = "cuda:0", trace_path: Path | None = None,
) -> Manifest:
    import torch
    from torch.nn import functional as F

    from stpd.workers.checkpoint_codec import encode_checkpoint

    from .model import MStd
    from .numerics import TrainingNumerics
    from .optimization import optimizer_update

    obj = object_fields(plan, {"variants", "profile"}, "p1_calibration.plan")
    if (not isinstance(obj["variants"], list) or not 1 <= len(obj["variants"]) <= 8
            or type(obj["profile"]) is not bool):
        raise BoundaryError("p1_calibration", "bounded_explicit_variants_required")
    executions = [MStdExecution.decode(value) for value in obj["variants"]]
    started = time.perf_counter()
    manifest, frames, targets, vocabulary, state = read_input(store, input_id)
    read_seconds = time.perf_counter() - started
    config = MStdConfig.decode(manifest.parameters.value()["source_config"])
    sample_weights = manifest.parameters.value().get("sample_weights")
    selected_device = torch.device(device)
    if selected_device.type not in {"cpu", "cuda"} or (
            selected_device.type == "cuda" and not torch.cuda.is_available()):
        raise BoundaryError("p1_calibration", "explicit_available_CPU_or_CUDA_required")
    reports = []
    reference_scores = None
    reference_final_scores = None
    trace = None
    for variant_index, execution in enumerate(executions):
        numerics = TrainingNumerics(config.seed, execution.cpu_threads, selected_device)
        with numerics.scope():
            model = MStd(vocabulary, execution).to(selected_device)
            model.load_state_dict(state["model"], strict=True)
            optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate,
                                          weight_decay=config.weight_decay)
            optimizer.load_state_dict(copy.deepcopy(state["optimizer"]))
        # Clone experiments retain the original draw state on the same CUDA family;
        # a CPU diagnostic has its own explicit stream, never an exact GPU-resume claim.
        numerics.rng = copy.deepcopy(state["rng"])
        if selected_device.type == "cpu":
            numerics.rng["torch_cuda"] = None
        elif numerics.rng["torch_cuda"] is None:
            raise BoundaryError("p1_calibration", "CUDA_source_rng_required")
        with numerics.scope(), torch.inference_mode():
            model.eval()
            scores = [model(frame).detach().cpu() for frame in frames[:4]]
        if reference_scores is None:
            reference_scores = scores
        score_difference = 0.0
        for wanted, actual in zip(reference_scores, scores, strict=True):
            torch.testing.assert_close(actual, wanted, rtol=3e-4, atol=2e-5)
            score_difference = max(score_difference, float((wanted - actual).abs().max()))
        if selected_device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(selected_device)
        rows = []
        cpu_started = time.process_time()
        with numerics.scope():
            for offset in range(0, len(frames), 64):
                if selected_device.type == "cuda":
                    torch.cuda.synchronize(selected_device)
                before = time.perf_counter()
                row = optimizer_update(model, optimizer, frames[offset:offset + 64],
                                       targets[offset:offset + 64],
                                       _lr(config, manifest.parameters.value()["planned_steps"],
                                           state["step"] + offset // 64),
                                       **({"weights": sample_weights[offset:offset + 64]}
                                          if sample_weights is not None else {}))
                if selected_device.type == "cuda":
                    torch.cuda.synchronize(selected_device)
                row.update(optimizer_update=state["step"] + offset // 64 + 1,
                           completed_wall_seconds=time.perf_counter() - before)
                rows.append(row)
        timings = [row["completed_wall_seconds"] for row in rows[1:]]
        report = {"execution": execution.to_dict(), "updates": rows,
                  "warmup_updates": 1, "measured_updates": len(timings),
                  "steady_mean_seconds": statistics.mean(timings),
                  "steady_N_per_second": 64 / statistics.mean(timings),
                  "process_cpu_seconds": time.process_time() - cpu_started,
                  "initial_complete_raw_scores_max_abs_vs_first_variant": score_difference,
                  "peak_allocated_bytes": torch.cuda.max_memory_allocated(selected_device)
                  if selected_device.type == "cuda" else None,
                  "peak_reserved_bytes": torch.cuda.max_memory_reserved(selected_device)
                  if selected_device.type == "cuda" else None,
                  "numerical_trajectory": "explicit_clone_not_formal_resume",
                  "FP32_accumulation_reordered":
                  execution.implementation == "experimental-packed-v1"}
        # Diagnostics stay outside the synchronized throughput windows. Hash full
        # numerical contents, rather than treating equal scalar norms as equal AdamW.
        report["variant_index"] = variant_index
        report["final_numerical_sha256"] = {
            name: hashlib.sha256(encode_checkpoint({"value": value})).hexdigest()
            for name, value in (("model", model.state_dict()),
                                ("optimizer", optimizer.state_dict()), ("rng", numerics.rng))
        }
        report["gradient_present"] = [name for name, parameter in model.named_parameters()
                                      if parameter.grad is not None]
        with numerics.scope(), torch.inference_mode():
            model.eval()
            final_scores = [model(frame).detach().cpu() for frame in frames[:4]]
        if reference_final_scores is None:
            reference_final_scores = final_scores
        report["post_update_complete_raw_scores_max_abs_vs_first_variant"] = max(
            float((wanted - actual).abs().max())
            for wanted, actual in zip(reference_final_scores, final_scores, strict=True))
        report["post_update_raw_scores_within_declared_tolerance"] = all(
            torch.allclose(actual, wanted, rtol=3e-4, atol=2e-5)
            for wanted, actual in zip(reference_final_scores, final_scores, strict=True))
        reports.append(report)
        # Instrument only after every variant's throughput measurement. Profiling
        # inside the comparison can alter subsequent execution costs.
        if trace_path is not None and obj["profile"] and variant_index == len(executions) - 1:
            activities = [torch.profiler.ProfilerActivity.CPU]
            if selected_device.type == "cuda":
                activities.append(torch.profiler.ProfilerActivity.CUDA)
            model.train()
            model.zero_grad(set_to_none=True)
            with numerics.scope(), torch.profiler.profile(
                activities=activities, record_shapes=True, profile_memory=True,
            ) as profiler:
                score = model.forward_batch([frames[0]])[0]
                (F.cross_entropy(score[None], torch.tensor([targets[0]], device=selected_device))
                 / 64).backward()
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            profiler.export_chrome_trace(str(trace_path))
            trace = store.put_payload("trace", io.BytesIO(trace_path.read_bytes()),
                                      "application/json")
            report["trace_scope"] = ("one complete real TRAIN frame; training mode; "
                                     "backward; no optimizer update")
            report["profiler_summary"] = profiler.key_averages().table(
                sort_by="self_cpu_time_total", row_limit=20)
            del score, profiler
        del model, optimizer
        if selected_device.type == "cuda":
            torch.cuda.empty_cache()
    info = {"schema": REPORT_SCHEMA, "input_id": input_id, "plan": obj,
            "input_producer": manifest.producer.to_dict(),
            "execution_producer": producer.to_dict(),
            "source_checkpoint": manifest.parent("checkpoint"), "device": device,
            "source_step": state["step"], "sample_count": len(frames),
            "torch": str(torch.__version__), "cuda": torch.version.cuda,
            "python": platform.python_version(),
            "source_runtime": state["identity"]["runtime"],
            "gpu": torch.cuda.get_device_name(selected_device)
            if selected_device.type == "cuda" else None,
            "read_seconds": read_seconds, "total_wall_seconds": time.perf_counter() - started,
            "variants": reports, "DEV_consumed": False, "TEST_consumed": False,
            "consumed_means": "model_forward_gradient_or_selection",
            "snapshot_validation_reads_all_partitions": True,
            "DEV_TEST_frames_scored": False, "DEV_TEST_gradients": False,
            "formal_resume": False, "model_published": False}
    artifact = Manifest("performance", producer, (Parent("calibration_input", input_id),),
                        () if trace is None else (trace,), FrozenObject.of(info))
    store.publish(artifact)
    return artifact


def main(argv: list[str] | None = None) -> int:
    from spireagent.storage.config import open_store

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="s3")
    parser.add_argument("--input", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--expected-lock", required=True)
    args = parser.parse_args(argv)
    try:
        producer = source_identity(Path(__file__).resolve().parents[2])
        if (producer.source_revision != args.expected_source
                or producer.uv_lock_sha256 != args.expected_lock):
            raise BoundaryError("p1_calibration", "exact_worker_source_and_lock_required")
        artifact = measure(open_store(args.store), args.input, producer,
                           json.loads(args.plan.read_bytes()), device=args.device,
                           trace_path=args.trace)
    except Exception as error:
        print(json.dumps({"state": "failed", "error_code": getattr(error, "code",
                                                                    type(error).__name__)}))
        return 1
    print(json.dumps({"state": "completed", "performance_id": artifact.artifact_id,
                      "producer": producer.to_dict()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
