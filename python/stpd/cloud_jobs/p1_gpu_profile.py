"""Opt-in synthetic P1 worker measurement; never a real-data training entry.

The original request executes first, unchanged. Timings then use an independent
clone of its exported weights, without optimizer updates or checkpoint mutation.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, json_bytes
from spireagent.source import source_identity
from spireagent.storage.config import open_store

from .contracts import ComputeReceipt, ComputeRequest


def require_synthetic(store: Any, request: ComputeRequest) -> Any:
    from stpd.p1.workload import verify_p1_run

    workload = verify_p1_run(store, request.input_id, request.producer)
    if workload.config.device != "cuda:0":
        raise BoundaryError("p1_gpu_profile", "explicit_cuda_zero_required")
    for parent in workload.snapshot.parents:
        projection = store.get_manifest(parent.artifact_id)
        original = store.get_manifest(projection.parent("source"))
        if original.parameters.value().get("declared_origin") != "engineering_fixture":
            raise BoundaryError("p1_gpu_profile", "synthetic_originals_required")
    return workload


def sample_gpu(stop: threading.Event, samples: list[dict[str, Any]]) -> None:
    while not stop.is_set():
        value = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            check=True, text=True, capture_output=True, timeout=5,
        ).stdout.strip().splitlines()
        if len(value) != 1:
            raise ValueError("one_gpu_sample_required")
        utilization, used, total = [int(v.strip()) for v in value[0].split(",")]
        samples.append({"monotonic": time.monotonic(), "utilization_percent": utilization,
                        "memory_used_mib": used, "memory_total_mib": total})
        stop.wait(0.25)


def measure_clone(store: Any, workload: Any, receipt: ComputeReceipt) -> dict[str, Any]:
    import torch
    from torch.nn import functional as F

    from stpd.p1.dataset import iter_snapshot_points
    from stpd.p1.input import project
    from stpd.p1.model import MStd
    from stpd.p1.numerics import TrainingNumerics
    from stpd.workers.p1_run import verify_p1_checkpoint

    assert receipt.checkpoint_id is not None
    checkpoint = verify_p1_checkpoint(store, receipt.checkpoint_id, workload)
    rows = []
    for reference, observation in iter_snapshot_points(store, workload.snapshot.artifact_id):
        if reference["split"] == "train":
            frame = project(observation)
            target = [choice["choice_id"] for choice in frame.choices].index(reference["chosen"])
            rows.append((frame, target))
            if len(rows) == 4:
                break
    times = []
    numerics = TrainingNumerics(workload.config.seed, 2, torch.device("cuda:0"))
    with numerics.scope():
        model = MStd(workload.vocabulary)
        model.load_state_dict(checkpoint.state["best_model"], strict=True)
        model.to("cuda:0").train()
        for frame, target in rows:
            model.zero_grad(set_to_none=True)
            first = torch.cuda.Event(enable_timing=True)
            second = torch.cuda.Event(enable_timing=True)
            first.record()
            began = time.perf_counter()
            scores = model(frame)
            second.record()
            torch.cuda.synchronize()
            forward_wall = time.perf_counter() - began
            forward_gpu = first.elapsed_time(second)
            loss = F.cross_entropy(scores.unsqueeze(0), torch.tensor([target], device="cuda:0"))
            first.record()
            began = time.perf_counter()
            (loss / workload.config.batch_size).backward()
            second.record()
            torch.cuda.synchronize()
            times.append({"entities": len(frame.entities), "catalog_choices": len(frame.choices),
                          "forward_wall_seconds": forward_wall,
                          "backward_wall_seconds": time.perf_counter() - began,
                          "forward_cuda_milliseconds": forward_gpu,
                          "backward_cuda_milliseconds": first.elapsed_time(second)})
    return {"scope": "four_synthetic_N_independent_model_clone_no_optimizer_updates",
            "samples": times, "optimizer_updates": 0,
            "mean_forward_seconds": sum(row["forward_wall_seconds"] for row in times) / len(times),
            "mean_backward_seconds": sum(row["backward_wall_seconds"] for row in times)
            / len(times)}


def execute_profile(value: object, *, store_location: str = "s3") -> dict[str, Any]:
    import torch

    from .__main__ import execute_request

    request = ComputeRequest.decode(value)
    runtime = source_identity(Path(__file__).resolve().parents[2])
    if request.kind != "training" or request.producer != runtime:
        raise BoundaryError("p1_gpu_profile", "exact_training_source_required")
    store = open_store(store_location)
    workload = require_synthetic(store, request)
    stop = threading.Event()
    samples: list[dict[str, Any]] = []
    failures: list[str] = []

    def watch() -> None:
        try:
            sample_gpu(stop, samples)
        except Exception as error:
            failures.append(type(error).__name__)

    monitor = threading.Thread(target=watch, name="synthetic-gpu-measurement", daemon=True)
    began = time.perf_counter()
    monitor.start()
    try:
        receipt = ComputeReceipt.decode(execute_request(value, store_location=store_location))
        if receipt.checkpoint_id is None:
            raise BoundaryError("p1_gpu_profile", "P1_worker_checkpoint_required")
        worker_seconds = time.perf_counter() - began
        worker_peak = torch.cuda.max_memory_allocated()
        worker_reserved = torch.cuda.max_memory_reserved()
        clone = None
        if receipt.output_id is not None:
            try:
                clone = measure_clone(store, workload, receipt)
            except (BoundaryError, RuntimeError, ValueError) as error:
                failures.append(error.code if isinstance(error, BoundaryError)
                                else type(error).__name__)
    finally:
        stop.set()
        monitor.join(timeout=6)
    if monitor.is_alive() or not samples:
        failures.append("gpu_observation_incomplete")
    report = {
        "schema": "stpd/p1-synthetic-gpu-measurement-1", "request_id": request.request_id,
        "measurement_state": "failed" if failures else "completed", "failures": failures,
        "attempt_id": request.attempt_id, "receipt": receipt.to_dict(),
        "worker_wall_seconds": worker_seconds, "worker_peak_allocated_bytes": worker_peak,
        "worker_peak_reserved_bytes": worker_reserved,
        "gpu_name": torch.cuda.get_device_name(0), "torch": str(torch.__version__),
        "cuda": torch.version.cuda, "cuda_visible_devices": torch.cuda.device_count(),
        "worker_source": runtime.to_dict(), "configuration": workload.config.to_dict(),
        "graph": workload.training.parameters.value()["architecture"],
        "input_spec": workload.training.parameters.value()["input_spec"],
        "gpu_samples": samples, "independent_clone_measurement": clone,
        "real_data_gradient_updates": 0, "scientific_verdict": "not_claimed",
    }
    parents = [Parent("run", request.input_id), Parent("checkpoint", receipt.checkpoint_id)]
    if receipt.output_id is not None:
        parents.append(Parent("output", receipt.output_id))
    analysis = Manifest("performance", runtime, tuple(parents),
                        parameters=FrozenObject.of(report))
    store.publish(analysis)
    return {"receipt": receipt.to_dict(), "profile_artifact_id": analysis.artifact_id}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--store", default="s3")
    args = parser.parse_args(argv)
    try:
        raw = args.request.read_bytes()
        if len(raw) > 65536:
            raise BoundaryError("p1_gpu_profile", "request_size_limit")
        result = execute_profile(decode_json(raw), store_location=args.store)
    except Exception as error:
        code = error.code if isinstance(error, BoundaryError) else type(error).__name__
        print(json.dumps({"schema": "stpd/compute-error-v1", "code": code}))
        return 1
    print(json_bytes(result).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
