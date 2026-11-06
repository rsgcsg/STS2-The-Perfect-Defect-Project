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


def prepare_segment_diagnostic(model, records, probabilities, outcomes, root, performance):
    """Construct an explicitly synthetic hot workload from authorized TRAIN inputs.

    The constructed successor sequence/probabilities is never offered to a live
    learner. Labels are the observed corpus labels, with no new research claim.
    """
    from spireagent.rl_contracts import RowRef, RunSequence
    from spireagent.rl_exchange import Exchange
    from stpd.evaluation.suites import derive_game_seed, seed_membership
    from stpd.p1.data import digest, json_bytes
    from stpd.p1.export import input_spec
    from stpd.p1.frame_compiler import FrameCompiler
    from stpd.rl.batch_preparation import BatchPreparer
    from stpd.rl.critic import held_out
    from stpd.rl.prepared_batch import AuxiliarySelection, BatchMember, PrepareRequest
    from stpd.rl.segments import minibatch_members

    root.mkdir(parents=True)
    peer = {"kind": "local", "directory": str(root / "peer")}
    exchange = Exchange(peer, "compute-diagnostic")
    compiler = FrameCompiler(model.vocabulary)
    generation = "g-diagnostic"
    mu: dict[str, Any] = {"run": exchange.run, "training_generation": generation,
          "version": 0, "updates": 0, "snapshot_sha256": "a" * 64}
    seed_number = 1
    while True:
        seed = derive_game_seed("rl41-compute-diagnostic", seed_number)
        if not seed_membership(seed) and not held_out(seed):
            break
        seed_number += 1
    members, selections = [], []
    payloads = []
    for start in range(0, len(records), 64):
        corpus = records[start:start + 64]
        run_id = f"diagnostic-{start}"
        segment_id = f"diagnostic-{start}/0"
        inputs = [{"observation": row["observation"], "catalog": row["observation"]["catalog"],
                   "input_spec": input_spec(model.vocabulary, runtime_projection(model.vocabulary)),
                   "acquisition_id": f"{run_id}:{i}", "consumption_unit": {"id": str(i)}}
                  for i, row in enumerate(corpus)]
        steps: list[dict[str, Any]] = []
        for local, (row, value) in enumerate(zip(corpus, inputs, strict=True)):
            index = start + local
            mass = probabilities[index]
            scores = [math.log(max(p, 1e-30)) for p in mass]
            chosen = row["reference"]["chosen"]
            action = [choice["choice_id"] for choice in value["catalog"]].index(chosen)
            successor = inputs[local + 1] if local + 1 < len(inputs) else value
            steps.append({"schema": "sts2.policy-runtime/verified-global-transition-1",
                "run_id": run_id, "step_index": local, "input": value, "successor": successor,
                "result": {"status": "completed"}, "directive": {"directive": {
                    "selection": {"action_id": chosen}, "scores": {"values": scores}}},
                "sampling": {"mode": "temperature_uniform", "temperature": 1.0,
                    "uniform_mix": 0.0, "probabilities": mass, "model_probabilities": mass,
                    "model_logits": scores, "selected_probability": mass[action]},
                "behavior": {"mu": mu, "lease": segment_id, "scores": scores,
                    "floor": row["floor"], "outcome_probabilities": outcomes[index],
                    "compiled_input": compiler.encode(row["observation"]),
                    "acquisition_id": value["acquisition_id"],
                    "input_sha256": digest(json_bytes(value)),
                    "observation_sha256": digest(json_bytes(value["observation"])),
                    "catalog_sha256": digest(json_bytes(value["catalog"])),
                    "consumption_unit": value["consumption_unit"]["id"]}})
        tail = steps[-1]["successor"]
        tail_floor = max(row["floor"] for row in corpus)
        with torch.inference_mode():
            _, logits, _ = model.forward_rl4([compiler.compile(tail["observation"])],
                                              [tail_floor], policy=False)
        segment = {"run": exchange.run, "training_generation": generation,
            "training_split": "TRAIN", "identity": {"intent_id": run_id, "seed": seed},
            "run_id": run_id, "segment_id": segment_id, "rows": len(steps), "mu": mu,
            "steps": steps, "terminal": None, "hp_battles": [],
            "compiler_identity": compiler.identity,
            "compiled_boundary": compiler.encode(tail["observation"]),
            "bootstrap": {"mu_snapshot_sha256": mu["snapshot_sha256"],
                "successor_input_sha256": digest(json_bytes(tail)), "floor": tail_floor,
                "outcome_probabilities": logits.softmax(-1)[0].cpu().tolist()},
            "original_commit": {"run_id": run_id, "segment_id": segment_id,
                "representation": "live-prefix", "journal_prefix_sha256": "d" * 64,
                "behavior_trace_prefix_sha256": "e" * 64}}
        payload = exchange.put_hot_segment(segment, owner="diagnostic")
        payloads.append(payload)
        members.append(BatchMember(segment_id, payload, run_id, len(steps),
                                   mu["snapshot_sha256"], run_id))
        for local, row in enumerate(corpus):
            ref: RowRef = {"run_id": run_id, "segment_id": segment_id,
                   "training_generation": generation,
                   "row": local, "key": f"{run_id}:{local}"}
            if start + local < 64 and row.get("outcome_class") is not None:
                selections.append(AuxiliarySelection(ref, "terminal", row["outcome_class"], 0, 2))
            if row.get("hp") is not None and sum(s.kind == "hp" for s in selections) < 64:
                selections.append(AuxiliarySelection(ref, "hp", row["hp"], 0, 1))
    order = list(range(len(records)))
    random.Random(37).shuffle(order)
    request = PrepareRequest(RunSequence(exchange.run, generation, 1), tuple(members),
        tuple(selections), tuple(tuple(order[i] for i in group)
                                for group in minibatch_members(len(order))),
        tuple(i % 4 for i in range(len(selections))), compiler.identity,
        random.Random(37).getstate(), 1024 * 1024**2)
    preparer = BatchPreparer(model.vocabulary, workers=4, threads=1, peer=peer,
        run=exchange.run, scratch=root, microbatch_size=performance.microbatch_size,
        deduplicate_text=performance.deduplicate_text,
        scene_attention_area=performance.scene_attention_area,
        text_attention_area=performance.text_attention_area)
    try:
        return preparer.submit(request).result()
    finally:
        preparer.shutdown(wait=True)
        for payload in payloads:
            exchange.release_hot_payload(payload, "diagnostic")


def segment_benchmark(args: Any) -> None:
    """Same diagnostic owner, now calling the production short-segment SGD.

    Real TRAIN public states and observed labels supply the workload. Behavior
    probabilities/TD targets are diagnostic constructions, never admitted replay.
    Every case starts with identical weights/Adam/RNG and has no publication.
    """
    from dataclasses import replace

    from stpd.p1.execution import microbatches
    from stpd.p1.tensor_input import compile_frame
    from stpd.rl.batch_preparation import stage_batch
    from stpd.rl.loss import prepared_update
    from stpd.rl.performance import PerformanceConfig
    from stpd.workers.checkpoint_codec import decode_checkpoint

    if args.output.exists():
        raise ValueError("fresh_diagnostic_output_required")
    args.output.mkdir(parents=True)
    began = time.monotonic()
    deadline = began + args.seconds
    torch.set_num_threads(16)
    torch.manual_seed(20261103)
    device = torch.device(args.device)
    descriptor = json.loads((args.package / "model.json").read_bytes())
    base, _ = load_model(args.package / "model.tensor-tree", descriptor["package_sha256"])
    base.enable_rl4_heads(0)
    head_bytes = args.heads_checkpoint.read_bytes()
    base.rl4_heads.load_state_dict(decode_checkpoint(head_bytes)["heads"], strict=True)
    base.eval().to(device).requires_grad_(False)
    records = [json.loads(line) for line in args.input.read_text().splitlines()]
    if len(records) < 256 or any(row["reference"]["split"] != "train" for row in records):
        raise ValueError("at_least_256_real_TRAIN_states_required")
    records = records[:256]
    frames = [
        compile_frame(
            project(row["observation"], input_projection=runtime_projection(base.vocabulary)), base
        )
        for row in records
    ]
    floors = [row["floor"] for row in records]
    base.execution = replace(
        base.execution,
        implementation="experimental-packed-v1",
        microbatch_size=16,
        scene_attention_area=67108864,
        text_attention_area=67108864,
    )
    probabilities: list[list[float]] = []
    outcomes: list[list[float]] = []
    with torch.inference_mode():
        for indices in microbatches(frames, list(range(len(frames))), base):
            scores, logits, _ = base.forward_rl4(
                [frames[i] for i in indices], [floors[i] for i in indices]
            )
            probabilities.extend(score.float().softmax(-1).cpu().tolist() for score in scores)
            outcomes.extend(logits.softmax(-1).cpu().tolist())
    rows = list(range(len(records)))
    terminal = [i for i, row in enumerate(records) if row.get("outcome_class") is not None][:64]
    hp = [i for i, row in enumerate(records) if row.get("hp") is not None][:64]
    # Diagnostic labels remain disclosed constructions; no World or live admission.

    def sync():
        if device.type == "cuda":
            torch.cuda.synchronize()

    identity = {
        "schema": "stpd/rl4-compute-diagnostic-1",
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "heads_sha256": hashlib.sha256(head_bytes).hexdigest(),
        "model_sha256": descriptor["package_sha256"],
        "source_revision": args.source_revision,
        "rows": len(rows),
        "terminal_rows": len(terminal),
        "hp_rows": len(hp),
        "device": str(device),
        "workload": "real TRAIN states; constructed diagnostic PPO/TD",
        "pipeline": "BatchPreparer -> stage_batch -> prepared_update",
        "timing_unit": "one four-minibatch dataset",
        "plan_sha256": hashlib.sha256(args.plan.read_bytes()).hexdigest(),
    }
    (args.output / "identity.json").write_text(json.dumps(identity, indent=2))
    plans = json.loads(args.plan.read_bytes())
    results = []
    for plan in plans:
        if time.monotonic() >= deadline:
            break
        model = copy.deepcopy(base).requires_grad_(True)
        performance = PerformanceConfig(
            packed=True,
            tensor_cache=True,
            precompile_inputs=True,
            microbatch_size=plan["microbatch"],
            precision=plan["precision"],
            deduplicate_text=plan["deduplicate_text"],
            scene_attention_area=67108864,
            text_attention_area=67108864,
        )
        model.deduplicate_text = performance.deduplicate_text
        optimizer = torch.optim.AdamW(
            [
                {
                    "params": [
                        p
                        for name, p in model.named_parameters()
                        if not name.startswith("rl4_heads.")
                    ],
                    "lr": 4e-5,
                },
                {"params": list(model.rl4_heads.parameters()), "lr": 1e-4},
            ],
            weight_decay=0,
        )
        durations, updates = [], []
        torch.manual_seed(20261103)
        if device.type == "cuda":
            torch.cuda.reset_peak_memory_stats()
        result = {"name": plan["name"], "configuration": asdict(performance)}
        try:
            preparation_started = time.perf_counter()
            batch = prepare_segment_diagnostic(
                base, records, probabilities, outcomes, args.output / plan["name"], performance)
            result["preparation_seconds"] = time.perf_counter() - preparation_started
            batch, copies = stage_batch(batch, device)
            result["stage_copies"] = copies

            def update(diagnostics=False, *, batch=batch, model=model,
                       optimizer=optimizer, performance=performance):
                values = []
                for minibatch in batch.minibatches:
                    value = prepared_update(model, optimizer, minibatch,
                        config=LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01),
                        performance=performance, target_kl=0.03, diagnostics=diagnostics)
                    values.append(value)
                    if not value["stepped"]:
                        break
                return {"stepped": all(value["stepped"] for value in values),
                        "minibatches": values}

            for step in range(args.warmup + args.steps):
                if time.monotonic() >= deadline:
                    break
                sync()
                start = time.perf_counter()
                value = update(diagnostics=step == args.warmup)
                sync()
                if step >= args.warmup:
                    updates.append(value)
                    if value["stepped"]:
                        durations.append(time.perf_counter() - start)
                if not value["stepped"]:
                    break
            if plan.get("profile") and time.monotonic() < deadline:
                activities = [torch.profiler.ProfilerActivity.CPU]
                if device.type == "cuda":
                    activities.append(torch.profiler.ProfilerActivity.CUDA)
                with torch.profiler.profile(activities=activities, record_shapes=True) as prof:
                    update()
                    sync()
                prof.export_chrome_trace(str(args.output / f"{plan['name']}-trace.json"))
                (args.output / f"{plan['name']}-profile.txt").write_text(
                    prof.key_averages().table(
                        sort_by="self_cuda_time_total"
                        if device.type == "cuda"
                        else "self_cpu_time_total",
                        row_limit=40,
                    )
                )
            result.update(
                status="ok" if len(durations) == args.steps else "incomplete",
                requested_datasets=args.steps,
                successful_timed_datasets=len(durations),
                requested_SGD=4 * args.steps,
                successful_timed_SGD=sum(int(mb["stepped"]) for value in updates
                                         for mb in value["minibatches"]),
                seconds=durations,
                updates=updates,
                median_seconds=statistics.median(durations) if durations else None,
                peak_cuda_bytes=torch.cuda.max_memory_allocated()
                if device.type == "cuda"
                else None,
            )
        except Exception as error:
            result.update(status="failed", error_type=type(error).__name__, error=str(error)[:1000])
        results.append(result)
        (args.output / "results.json").write_text(json.dumps(results, indent=2, allow_nan=False))
        print(
            json.dumps(
                {key: value for key, value in result.items() if key not in {"updates", "seconds"}}
            ),
            flush=True,
        )
        del optimizer, model
        if device.type == "cuda":
            torch.cuda.empty_cache()
    (args.output / "timing.json").write_text(
        json.dumps({"elapsed_seconds": time.monotonic() - began})
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--rl-input", type=Path)
    parser.add_argument(
        "--heads-checkpoint",
        type=Path,
        help="normal RL4 warmup CP selects the short-segment production update",
    )
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
    if args.heads_checkpoint is not None:
        segment_benchmark(args)
        return
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
                model.parameters(),
                lr=1e-5,
                fused=plan.get("fused", False),
                weight_decay=plan.get("weight_decay", 0.01),
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
