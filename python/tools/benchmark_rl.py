"""Bounded diagnostic of the production RL update; never publishes or starts actors.

Input JSONL must be an authorized TRAIN engineering prefix with observation/reference.
Probabilities and advantages below are diagnostic constructions, not actor trajectories.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import torch

from stpd.p1.export import load_model, runtime_projection
from stpd.p1.input import project
from stpd.rl.loss import LossConfig, RlSample, rl_update


@contextmanager
def phase_ranges(model: Any, reference: Any, optimizer: Any) -> Any:
    """Diagnostic-only hooks, restored even on profiler failure."""
    from stpd.p1 import tensor_input

    saved = []

    def wrap(owner: Any, name: str, label: str) -> None:
        original = getattr(owner, name)

        def measured(*args: Any, **kwargs: Any) -> Any:
            with torch.profiler.record_function(label):
                return original(*args, **kwargs)

        saved.append((owner, name, original))
        setattr(owner, name, measured)

    wrap(model, "forward_batch", "D2/train_forward")
    wrap(reference, "forward_batch", "D2/reference_forward")
    wrap(optimizer, "step", "D2/optimizer")
    wrap(torch.Tensor, "backward", "D2/backward")
    wrap(tensor_input, "compile_frame", "D2/input_compile")
    try:
        yield
    finally:
        for owner, name, original in reversed(saved):
            setattr(owner, name, original)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--rl-input", type=Path)
    parser.add_argument("--source-revision", default=None)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--steps", type=int, default=18)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=1800)
    args = parser.parse_args()
    if args.steps < 3 or args.warmup < 0 or args.seconds <= 0:
        parser.error("positive bounded window required")
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "identity.json").exists() or (args.output / "results.json").exists():
        parser.error("use a fresh diagnostic output directory")
    deadline = time.monotonic() + args.seconds
    descriptor = json.loads((args.package / "model.json").read_bytes())
    base, _ = load_model(args.package / "model.tensor-tree", descriptor["package_sha256"])
    records = [json.loads(line) for line in args.input.read_text().splitlines()]
    if not records or any(r["reference"]["split"] != "train" for r in records):
        raise ValueError("TRAIN engineering prefix required")
    anchor_frames = [
        project(r["observation"], input_projection=runtime_projection(base.vocabulary))
        for r in records
    ]
    anchor_actions = [
        [c["choice_id"] for c in f.choices].index(r["reference"]["chosen"])
        for f, r in zip(anchor_frames, records, strict=True)
    ]
    rl_rows = (
        [json.loads(line) for line in args.rl_input.read_text().splitlines()]
        if args.rl_input
        else None
    )
    frames = (
        [
            project(r["observation"], input_projection=runtime_projection(base.vocabulary))
            for r in rl_rows
        ]
        if rl_rows
        else anchor_frames
    )
    actions = (
        [
            [c["choice_id"] for c in f.choices].index(r["action_id"])
            for f, r in zip(frames, rl_rows, strict=True)
        ]
        if rl_rows
        else anchor_actions
    )
    device = torch.device(args.device)
    base = base.to(device).eval()

    def sync() -> None:
        if device.type == "cuda":
            torch.cuda.synchronize()

    torch.set_num_threads(4)
    if rl_rows:
        old = [r["old_probability"] for r in rl_rows]
    else:
        with torch.no_grad():
            old_scores = [base(f).float() for f in frames]
            old = [float(s.softmax(-1)[a]) for s, a in zip(old_scores, actions, strict=True)]
    with torch.no_grad():
        initial_reference = [base(f).float().cpu() for f in frames[:64]]
    results: list[dict[str, Any]] = []
    import stpd.p1.execution as execution_module
    import stpd.p1.model as model_module
    import stpd.p1.tensor_input as tensor_module
    import stpd.rl.loss as loss_module
    import stpd.rl.performance as performance_module

    metadata = {
        "schema": "stpd/rl-speed-diagnostic-1",
        "source_revision": args.source_revision,
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "plan_sha256": hashlib.sha256(args.plan.read_bytes()).hexdigest(),
        "module_sha256": {
            m.__name__: hashlib.sha256(Path(cast(str, m.__file__)).read_bytes()).hexdigest()
            for m in (
                execution_module,
                model_module,
                tensor_module,
                loss_module,
                performance_module,
            )
        },
        "torch": str(torch.__version__),
        "cuda": torch.version.cuda,
        "device": str(device),
        "gpu": torch.cuda.get_device_name() if device.type == "cuda" else None,
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "package_sha256": descriptor["package_sha256"],
        "source_execution": asdict(base.execution),
        "unique_frames": len(frames),
        "data_meaning": (
            "local RL trajectories with original behavior; diagnostic advantages; "
            "TRAIN anchor; no gameplay quality"
        )
        if rl_rows
        else "TRAIN frames; constructed RL probabilities/advantages; no gameplay quality",
        "rl_input_sha256": hashlib.sha256(args.rl_input.read_bytes()).hexdigest()
        if args.rl_input
        else None,
        "scene_entities": [len(f.entities) for f in frames],
        "choices": [len(f.choices) for f in frames],
    }
    (args.output / "identity.json").write_text(json.dumps(metadata, indent=2))
    for plan in json.loads(args.plan.read_bytes()):
        if time.monotonic() >= deadline:
            break
        name = plan["name"]
        print(json.dumps({"start": name}), flush=True)
        began = time.monotonic()
        result: dict[str, Any] = {"name": name, "plan": plan, "started_unix": time.time()}
        try:
            torch.set_num_threads(plan.get("threads", 4))
            torch.manual_seed(37)
            model = copy.deepcopy(base).train()
            reference = copy.deepcopy(base).eval()
            for parameter in reference.parameters():
                parameter.requires_grad_(False)
            optimizer = torch.optim.AdamW(
                model.parameters(), lr=1e-5, fused=plan.get("fused", False),
                weight_decay=plan.get("weight_decay", 0.01)
            )
            config = None
            if plan.get("performance"):
                from stpd.rl.performance import PerformanceConfig

                config = PerformanceConfig(**plan["performance"])
            from stpd.rl.performance import prepare_models

            with (
                torch.no_grad(),
                prepare_models(model, reference, config) if config else nullcontext(),
            ):
                model.eval()
                initial = [model(f).float().cpu() for f in frames[:64]]
            result["initial_eval_max_logit_error"] = max(
                float((x - y).abs().max()) for x, y in zip(initial, initial_reference, strict=True)
            )
            result["initial_eval_max_probability_error"] = max(
                float((x.softmax(-1) - y.softmax(-1)).abs().max())
                for x, y in zip(initial, initial_reference, strict=True)
            )
            result["initial_eval_argmax_agreement"] = sum(
                int(x.argmax() == y.argmax())
                for x, y in zip(initial, initial_reference, strict=True)
            ) / len(initial)
            compile_began = time.monotonic()
            if plan.get("compile"):
                # Compile the tensor-heavy submodules actually reached by forward_batch.
                for attr in ("text_blocks", "scene", "cross", "head"):
                    setattr(model, attr, torch.compile(getattr(model, attr), dynamic=True))
            result["compile_setup_seconds"] = time.monotonic() - compile_began
            cache: dict[str, torch.Tensor] = {}
            frame_cache: dict[Any, Any] = {}
            prepared_frames = frames
            if config is not None and config.precompile_inputs:
                # Same CPU-only producer function as the shard reader. Preparation
                # is separately charged; this window measures the consuming update.
                from stpd.p1.tensor_input import compile_frame

                def prepare_inputs(member: Any) -> list[Any]:
                    with torch.device("cpu"):
                        return [compile_frame(frame, member) for frame in frames]

                prepare_began = time.perf_counter()
                with ThreadPoolExecutor(max_workers=1, thread_name_prefix="rl-shard-read") as pool:
                    prepared_frames = pool.submit(prepare_inputs, reference).result()
                result["input_prepare_seconds"] = time.perf_counter() - prepare_began
                result["input_prepare_frames"] = len(prepared_frames)
            batch_size = plan.get("batch_size", 64)
            durations, metrics, misses = [], [], []
            if device.type == "cuda":
                torch.cuda.reset_peak_memory_stats()
            total_start = None
            for step in range(args.steps + args.warmup):
                if time.monotonic() >= deadline:
                    raise TimeoutError("experiment_deadline")
                rng = random.Random(1000 + step)
                indices = list(range(len(frames)))
                rng.shuffle(indices)
                indices = (indices * math.ceil(batch_size / len(indices)))[:batch_size]
                # Same observation reused <=3 times; cold groups account for new trajectories.
                generation = step // 3
                samples = [
                    RlSample(
                        prepared_frames[i],
                        actions[i],
                        rl_rows[i]["behavior_probability"] if rl_rows else max(old[i] * 0.9, 1e-9),
                        max(old[i], 1e-9),
                        rl_rows[i]["advantage"] if rl_rows else (i % 11 - 5) / 5,
                        0,
                        f"{generation}:{i}",
                    )
                    for j, i in enumerate(indices)
                ]
                anchor_indices = list(range(len(anchor_frames)))
                rng.shuffle(anchor_indices)
                anchor = [(anchor_frames[i], anchor_actions[i]) for i in anchor_indices[:32]]
                if step == 0:
                    from stpd.p1.execution import microbatches

                    with prepare_models(model, reference, config) if config else nullcontext():
                        sample_frames = [prepared_frames[i] for i in indices]
                        entity_counts = [
                            f.entity_count if hasattr(f, "entity_count") else len(f.entities)
                            for f in sample_frames
                        ]
                        ordering = list(range(len(samples)))
                        if config and config.bucket:
                            ordering.sort(key=lambda i: entity_counts[i])
                        groups = list(microbatches(sample_frames, ordering, model))
                    actual = sum(count**2 for count in entity_counts)
                    padded = sum(len(g) * max(entity_counts[i] for i in g) ** 2 for g in groups)
                    result["scene_padding_fraction"] = 1 - actual / padded
                    result["rl_microbatches"] = len(groups)
                    result["batch_membership_sha256"] = hashlib.sha256(
                        json.dumps(indices).encode()
                    ).hexdigest()
                missing = sum(s.key not in cache for s in samples)
                sync()
                start = time.perf_counter()
                if total_start is None and step == args.warmup:
                    total_start = start
                kwargs: dict[str, Any] = (
                    {} if config is None else {"performance": config, "frame_cache": frame_cache}
                )
                value = rl_update(
                    model,
                    reference,
                    optimizer,
                    samples,
                    anchor,
                    1e-5,
                    LossConfig(),
                    cache,
                    **kwargs,
                )
                sync()
                elapsed = time.perf_counter() - start
                if step >= args.warmup:
                    durations.append(elapsed)
                    metrics.append(value)
                    misses.append(missing)
                if step % 3 == 2:
                    cache.clear()
                    for key in list(frame_cache):
                        if key[0] == "rl":
                            del frame_cache[key]
            assert total_start is not None
            elapsed = time.perf_counter() - total_start
            result.update(
                seconds=durations,
                median_seconds=statistics.median(durations),
                mean_seconds=statistics.mean(durations),
                p90_seconds=sorted(durations)[min(len(durations) - 1, int(0.9 * len(durations)))],
                samples_per_second=batch_size * len(durations) / elapsed,
                timed_wall_seconds=elapsed,
                metrics=metrics,
                reference_misses=misses,
                peak_cuda_reserved_bytes=torch.cuda.max_memory_reserved()
                if device.type == "cuda"
                else None,
                peak_cuda_bytes=(
                    torch.cuda.max_memory_allocated() if device.type == "cuda" else None
                ),
            )
            # Complete catalogs, eval mode, same original frame order.
            model.eval()
            with torch.no_grad():
                if config is None:
                    scores = [model(f).float().cpu() for f in frames[:64]]
                else:
                    from stpd.rl.performance import prepare_models

                    with prepare_models(model, reference, config):
                        scores = [model(f).float().cpu() for f in frames[:64]]
            torch.save({"scores": scores}, args.output / f"{name}-diagnostic.pt")
            result["weight_delta_l2"] = math.sqrt(
                sum(
                    float((p.detach() - q.detach()).double().square().sum())
                    for p, q in zip(model.parameters(), base.parameters(), strict=True)
                )
            )
            if plan.get("profile"):
                activities = [torch.profiler.ProfilerActivity.CPU]
                if device.type == "cuda":
                    activities.append(torch.profiler.ProfilerActivity.CUDA)
                with (
                    phase_ranges(model, reference, optimizer),
                    torch.profiler.profile(
                        activities=activities, record_shapes=True, profile_memory=True
                    ) as prof,
                ):
                    rl_update(
                        model,
                        reference,
                        optimizer,
                        samples,
                        anchor,
                        1e-5,
                        LossConfig(),
                        {},
                        **kwargs,
                    )
                    sync()
                result["profile_phases"] = {
                    event.key: {
                        "cpu_total_us": event.cpu_time_total,
                        "device_total_us": event.device_time_total,
                        "count": event.count,
                    }
                    for event in prof.key_averages()
                    if event.key.startswith("D2/")
                }
                prof.export_chrome_trace(str(args.output / f"{name}-trace.json"))
                (args.output / f"{name}-profile.txt").write_text(
                    prof.key_averages().table(
                        sort_by="self_cuda_time_total"
                        if device.type == "cuda"
                        else "self_cpu_time_total",
                        row_limit=40,
                    )
                )
            result["status"] = "ok"
        except Exception as error:
            result.update(status="failed", error_type=type(error).__name__, error=str(error)[:1000])
        result["total_seconds"] = time.monotonic() - began
        result["finished_unix"] = time.time()
        results.append(result)
        (args.output / "results.json").write_text(json.dumps(results, indent=2, allow_nan=False))
        print(
            json.dumps({k: v for k, v in result.items() if k not in ("metrics", "seconds")}),
            flush=True,
        )
        if device.type == "cuda":
            torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
