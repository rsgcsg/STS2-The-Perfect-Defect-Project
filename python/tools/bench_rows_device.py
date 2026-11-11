"""Device-only curve of the rl-inference-3 rows-v2 forward (diagnostic; no game, no CP).

What one device process can score when the front end is not the limit: real
encoded observations are prepared and written to a pinned arena up front, then
each forward stages its group (one H2D DMA, as the service device process does)
and runs `RowsScorer.forward_tensors`. Random-init production-shape M-std, BF16
autocast as in production. Every batch size cycles over several distinct batches
so shapes vary as they do in service (compile guards and recompiles included).

Modes (argv[3], comma list): curve, regions, profile, flops, streams.
  curve    rows/s and per-forward ms per (config, rows): call (CPU launch), GPU
           event, total (with outputs on the host), stage.
  regions  GPU ms per forward region (text, nodes, scene blocks, rest), eager.
  profile  top CUDA kernels, kernel count and GPU busy fraction per forward.
  flops    FLOPs per row (FlopCounterMode) -> achieved TFLOP/s and MFU.
  streams  two forwards in flight: two threads, each on its own CUDA stream.
Configs (env CONFIGS, JSON): [{"name", "compile", "kernels"}...].

Example: python tools/bench_rows_device.py obs.jsonl out.jsonl curve,profile
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time
from typing import Any

DEFAULT_CONFIGS = [
    {"name": "eager", "compile": False, "kernels": ""},
    {"name": "compile", "compile": True, "kernels": ""},
    {"name": "eager-k", "compile": False, "kernels": "cross+heads+nodes"},
    {"name": "compile-k", "compile": True, "kernels": "cross+heads+nodes"},
]
SIZES = [int(x) for x in os.environ.get("SIZES", "16,32,64,128,256,512,1024,1536").split(",")]
VARIANTS = int(os.environ.get("VARIANTS", "4"))
REPS = int(os.environ.get("REPS", "24"))
OUT: Any = None


def emit(d: dict[str, Any]) -> None:
    line = json.dumps({"t": round(time.time(), 1), **d})
    print(line, flush=True)
    if OUT is not None:
        OUT.write(line + "\n")
        OUT.flush()


def load(obs: str) -> tuple[Any, list[Any], list[int]]:
    from stpd.p1.input import V2_R19_INPUT_SPEC
    from stpd.p1.rows_encoder import RowsEncoder
    from stpd.p1.rows_encoder_wire import decode_row
    from stpd.p1.vocabulary import public_vocabulary

    vocabulary = public_vocabulary(V2_R19_INPUT_SPEC)
    encoder = RowsEncoder(vocabulary)
    frames, floors = [], []
    with open(obs, "rb") as handle:
        for line in handle:
            e = encoder.encode(line.rstrip(b"\n"))
            if e.catalog_len >= 1:
                frames.append(decode_row(e.row, validator=None, expected_sha256=None))
                floors.append(e.floor or 0)
    return vocabulary, frames, floors


def build(vocabulary: Any) -> Any:
    import torch

    from stpd.p1.model import MStd, MStdExecution

    torch.manual_seed(3)
    model = MStd(vocabulary, MStdExecution(implementation="experimental-packed-v1",
                                           microbatch_size=4, scene_attention_area=67108864,
                                           text_attention_area=67108864, cpu_threads=1))
    model.enable_rl4_heads(7)
    return model.cuda().eval().requires_grad_(False)


class Batch:
    """One prepared batch written to its own pinned arena; `stage()` = the H2D DMA."""

    def __init__(self, frames: list[Any], floors: list[int], start: int, n: int) -> None:
        import torch

        from stpd.p1.rows_scorer import prepare
        from stpd.rl.batch_preparation import (
            allocate_forward_arena,
            forward_arena_bytes,
            write_forward_inputs,
        )

        total = len(frames)
        index = [(start + i) % total for i in range(n)]
        self.n = n
        self.distinct = len(set(index)) == n
        self.lengths = [len(frames[i].choices) for i in index]
        group = prepare([frames[i] for i in index], [floors[i] for i in index])
        self.arena = allocate_forward_arena(forward_arena_bytes(group) + 4096, pinned=True)
        self.part = write_forward_inputs(self.arena, 0, group)
        self.device = torch.device("cuda")

    def stage(self) -> Any:
        from stpd.rl.batch_preparation import stage_forward_parts

        return stage_forward_parts(self.arena, [self.part], self.device,
                                   rows=tuple(range(self.n)))


def scorer_of(model: Any, config: dict[str, Any]) -> Any:
    from stpd.p1.rows_scorer import RowsScorer

    kwargs: dict[str, Any] = {"bf16": True, "compile": config["compile"]}
    if config.get("kernels"):
        kwargs["kernels"] = config["kernels"]
    return RowsScorer(model, **kwargs)


def raw_forward(scorer: Any, group: Any) -> Any:
    """`forward_tensors` without the host copies (for call/GPU timing)."""
    import torch

    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        return scorer.forward_device(group)


def med(values: list[float]) -> float:
    return round(1000 * statistics.median(values), 3)


def curve(model: Any, frames: list[Any], floors: list[int], configs: list[dict[str, Any]]
          ) -> None:
    import torch

    for config in configs:
        scorer = scorer_of(model, config)
        torch.cuda.reset_peak_memory_stats()
        for n in SIZES:
            batches = [Batch(frames, floors, 977 * v + 31 * n, n) for v in range(VARIANTS)]
            warm = time.perf_counter()
            for _ in range(2):
                for b in batches:
                    scorer.forward_tensors(b.stage())
            torch.cuda.synchronize()
            warm_s = time.perf_counter() - warm
            calls, gpus, totals, stages, fulls = [], [], [], [], []
            for rep in range(REPS):
                b = batches[rep % VARIANTS]
                start = torch.cuda.Event(enable_timing=True)
                end = torch.cuda.Event(enable_timing=True)
                torch.cuda.synchronize()
                t0 = time.perf_counter()
                group = b.stage()
                t1 = time.perf_counter()
                start.record()
                raw_forward(scorer, group)
                end.record()
                t2 = time.perf_counter()
                torch.cuda.synchronize()
                t3 = time.perf_counter()
                stages.append(t1 - t0)
                calls.append(t2 - t1)
                totals.append(t3 - t0)
                gpus.append(start.elapsed_time(end) / 1000)
                t0 = time.perf_counter()
                scorer.forward_tensors(b.stage())
                fulls.append(time.perf_counter() - t0)
            full = statistics.median(fulls)
            emit({"curve": config["name"], "rows": n, "distinct": all(b.distinct for b in batches),
                  "stage_ms": med(stages), "call_ms": med(calls), "gpu_ms": med(gpus),
                  "total_ms": med(totals), "forward_tensors_ms": round(1000 * full, 3),
                  "rows_per_s": round(n / full), "gpu_us_per_row": round(
                      1e6 * statistics.median(gpus) / n, 1),
                  "warm_s": round(warm_s, 1),
                  "peak_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2),
                  **compile_state()})
        del scorer
        torch.cuda.empty_cache()


def compile_state() -> dict[str, int]:
    try:
        from stpd.p1.rows_forward import compile_counters

        return compile_counters()
    except Exception:  # noqa: BLE001 - diagnostics only
        return {}


def regions(model: Any, frames: list[Any], floors: list[int], configs: list[dict[str, Any]]
            ) -> None:
    """Eager only: synchronize around each region (GPU-bound sizes: GPU time)."""
    import torch

    from stpd.p1 import rows_forward, rows_kernels

    spent: dict[str, float] = {}

    def timed(name: str, fn: Any) -> Any:
        def wrapper(*a: Any, **k: Any) -> Any:
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            out = fn(*a, **k)
            torch.cuda.synchronize()
            spent[name] = spent.get(name, 0.0) + time.perf_counter() - t0
            return out
        return wrapper

    originals: dict[str, Any] = {"packed_text": rows_forward.packed_text,
                 "owner_nodes": rows_kernels.owner_nodes,
                 "rows_block_packed": rows_forward.rows_block_packed}
    tensor_nodes = type(model).tensor_nodes
    setattr(rows_forward, "packed_text", timed("text", originals["packed_text"]))  # noqa: B010
    setattr(rows_kernels, "owner_nodes", timed("nodes", originals["owner_nodes"]))  # noqa: B010
    setattr(type(model), "tensor_nodes", timed("nodes", tensor_nodes))  # noqa: B010
    setattr(rows_forward, "rows_block_packed",  # noqa: B010
            timed("blocks", originals["rows_block_packed"]))
    try:
        for config in configs:
            if config["compile"]:
                continue
            scorer = scorer_of(model, config)
            for n in (32, 256, 1024):
                batches = [Batch(frames, floors, 977 * v + 31 * n, n) for v in range(VARIANTS)]
                for b in batches:
                    scorer.forward_tensors(b.stage())
                spent.clear()
                totals = []
                for rep in range(8):
                    group = batches[rep % VARIANTS].stage()
                    torch.cuda.synchronize()
                    t0 = time.perf_counter()
                    raw_forward(scorer, group)
                    torch.cuda.synchronize()
                    totals.append(time.perf_counter() - t0)
                total = sum(totals)
                emit({"regions": config["name"], "rows": n,
                      "total_ms": round(1000 * total / 8, 3),
                      **{f"{k}_ms": round(1000 * v / 8, 3) for k, v in spent.items()},
                      "rest_ms": round(1000 * (total - sum(spent.values())) / 8, 3)})
    finally:
        setattr(rows_forward, "packed_text", originals["packed_text"])  # noqa: B010
        setattr(rows_kernels, "owner_nodes", originals["owner_nodes"])  # noqa: B010
        setattr(rows_forward, "rows_block_packed", originals["rows_block_packed"])  # noqa: B010
        setattr(type(model), "tensor_nodes", tensor_nodes)  # noqa: B010


def profile(model: Any, frames: list[Any], floors: list[int], configs: list[dict[str, Any]]
            ) -> None:
    import torch
    from torch.profiler import ProfilerActivity
    from torch.profiler import profile as torch_profile

    for config in configs:
        scorer = scorer_of(model, config)
        for n in (32, 256):
            batches = [Batch(frames, floors, 977 * v + 31 * n, n) for v in range(VARIANTS)]
            for _ in range(2):
                for b in batches:
                    scorer.forward_tensors(b.stage())
            groups = [b.stage() for b in batches]
            torch.cuda.synchronize()
            with torch_profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA]) as prof:
                t0 = time.perf_counter()
                for g in groups:
                    raw_forward(scorer, g)
                torch.cuda.synchronize()
                wall = time.perf_counter() - t0
            events = [e for e in prof.key_averages() if e.device_type.name == "CUDA"]
            kernel_us = sum(e.self_device_time_total for e in events)
            launches = sum(e.count for e in events)
            top = sorted(events, key=lambda e: -e.self_device_time_total)[:14]
            emit({"profile": config["name"], "rows": n,
                  "kernels_per_forward": round(launches / len(groups), 1),
                  "gpu_kernel_ms_per_forward": round(kernel_us / 1000 / len(groups), 3),
                  "wall_ms_per_forward": round(1000 * wall / len(groups), 3),
                  "gpu_busy": round(kernel_us / 1e6 / wall, 3),
                  "top": [(e.key[:70], e.count // len(groups),
                           round(e.self_device_time_total / 1000 / len(groups), 3))
                          for e in top]})
        del scorer


def flops(model: Any, frames: list[Any], floors: list[int], configs: list[dict[str, Any]]
          ) -> None:
    import torch
    from torch.utils.flop_counter import FlopCounterMode

    from stpd.p1.rows_scorer import RowsScorer

    scorer = RowsScorer(model, bf16=True, compile=False)
    for n in (256, 1024):
        b = Batch(frames, floors, 31 * n, n)
        group = b.stage()
        counter = FlopCounterMode(display=False)
        with counter:
            raw_forward(scorer, group)
        total = counter.get_total_flops()
        by_op = sorted(((str(k), v) for k, v in counter.get_flop_counts()["Global"].items()),
                       key=lambda kv: -kv[1])[:8]
        emit({"flops": n, "gflop_per_forward": round(total / 1e9, 2),
              "mflop_per_row": round(total / n / 1e6, 2), "by_op_gflop": [
                  (k, round(v / 1e9, 2)) for k, v in by_op]})
    del scorer
    torch.cuda.empty_cache()


def streams(model: Any, frames: list[Any], floors: list[int], configs: list[dict[str, Any]]
            ) -> None:
    import torch

    seconds = float(os.environ.get("STREAM_S", "6"))
    for config in configs:
        scorer = scorer_of(model, config)
        for n in (64, 256, 1024):
            for threads in (1, 2):
                batches = [Batch(frames, floors, 977 * v + 31 * n, n) for v in range(threads)]
                lanes = [torch.cuda.Stream() for _ in range(threads)]
                for b, lane in zip(batches, lanes, strict=True):
                    with torch.cuda.stream(lane):
                        for _ in range(3):
                            scorer.forward_tensors(b.stage())
                torch.cuda.synchronize()
                counts = [0] * threads
                barrier = threading.Barrier(threads)

                def run(i: int) -> None:
                    with torch.cuda.stream(lanes[i]):  # noqa: B023
                        barrier.wait()  # noqa: B023
                        t0 = time.perf_counter()
                        while time.perf_counter() - t0 < seconds:
                            scorer.forward_tensors(batches[i].stage())  # noqa: B023
                            counts[i] += 1  # noqa: B023

                workers = [threading.Thread(target=run, args=(i,)) for i in range(threads)]
                t0 = time.perf_counter()
                for w in workers:
                    w.start()
                for w in workers:
                    w.join()
                wall = time.perf_counter() - t0
                emit({"streams": config["name"], "rows": n, "threads": threads,
                      "rows_per_s": round(sum(counts) * n / wall)})


def probe(model: Any, frames: list[Any], floors: list[int], configs: list[dict[str, Any]]
          ) -> None:
    """384-row probe (as #465): each config vs an FP32 reference forward and vs the
    production path (BF16, eager, no levers) on the same rows, in batches of 128 and
    one 384-row batch: max |behavior_q| diff, argmax and selected_index agreement."""
    import torch

    from stpd.p1.rows_scorer import RowsScorer, prepare
    from stpd.p1.sampling import distribution, draw

    count = 384
    rows, rows_floors = frames[:count], floors[:count]

    def scored(scorer: Any, size: int) -> list[Any]:
        out: list[Any] = []
        for start in range(0, count, size):
            sub = rows[start:start + size]
            out.extend(scorer.forward(prepare(sub, rows_floors[start:start + size]),
                                      [len(f.choices) for f in sub]))
        if any(r is None for r in out):
            raise RuntimeError("probe_not_finite")
        return out

    def behaviors(out: list[Any]) -> list[list[float]]:
        return [distribution(r.scores, 1.0, 0.0)[1] for r in out]

    reference = behaviors(scored(RowsScorer(model, bf16=False), 64))
    production = behaviors(scored(RowsScorer(model, bf16=True), 128))
    picks = [draw(b, "bench-yin-device", f"c{i}") for i, b in enumerate(production)]

    def compare(got: list[list[float]], want: list[list[float]]) -> dict[str, Any]:
        diff = max(float((torch.tensor(g) - torch.tensor(w)).abs().max())
                   for g, w in zip(got, want, strict=True))
        argmax = sum(max(range(len(g)), key=g.__getitem__) == max(range(len(w)),
                                                                    key=w.__getitem__)
                     for g, w in zip(got, want, strict=True))
        return {"max_behavior_q_diff": round(diff, 6), "argmax_agree": argmax}

    for config in configs:
        scorer = scorer_of(model, config)
        for size in (128, 384):
            got = behaviors(scored(scorer, size))
            selected = sum(draw(b, "bench-yin-device", f"c{i}") == picks[i]
                           for i, b in enumerate(got))
            emit({"probe": config["name"], "batch": size, "rows": count,
                  "vs_fp32": compare(got, reference), "vs_production": compare(got, production),
                  "selected_agree_vs_production": selected,
                  "selected_agree_vs_fp32": sum(
                      draw(b, "bench-yin-device", f"c{i}") == draw(r, "bench-yin-device", f"c{i}")
                      for i, (b, r) in enumerate(zip(got, reference, strict=True)))})
        del scorer


def republish(model: Any, frames: list[Any], floors: list[int], configs: list[dict[str, Any]]
              ) -> None:
    """Cost of a new mu on a compiled config: the device process loads every published
    model as a new object (torch.load), and the compiled wrappers are cached per model.
    (a) fresh model, own wrappers: first/second forward ms (recompile stall);
    (b) fresh model with different weights reusing the first model's wrappers: first
        forward ms and whether the output is its own (vs its eager forward), not stale."""
    import copy
    import io

    import torch


    def shipped(source: Any, scale: float) -> Any:
        clone = copy.copy(source)
        clone.__dict__ = {k: v for k, v in source.__dict__.items() if not k.startswith("_rows_")}
        blob = io.BytesIO()
        torch.save(clone, blob)
        out = torch.load(io.BytesIO(blob.getvalue()), map_location="cuda", weights_only=False)
        out.eval().requires_grad_(False)
        if scale != 1.0:
            with torch.no_grad():
                for name, parameter in out.named_parameters():
                    if "norm" not in name:
                        parameter.mul_(scale)
        return out

    batches = [Batch(frames, floors, 977 * v + 31 * 256, 256) for v in range(VARIANTS)]
    for config in configs:
        if not config["compile"]:
            continue
        base = shipped(model, 1.0)
        scorer = scorer_of(base, config)
        for _ in range(2):
            for b in batches:
                scorer.forward_tensors(b.stage())
        for label, scale, share in (("fresh-own", 1.0, False), ("fresh-own-2", 1.0, False),
                                    ("fresh-shared", 0.9, True)):
            other = shipped(model, scale)
            if share:
                for key, value in base.__dict__.items():
                    if key.startswith("_rows_"):
                        other.__dict__[key] = value
            other_scorer = scorer_of(other, config)
            times = []
            for b in batches[:3]:
                torch.cuda.synchronize()
                t0 = time.perf_counter()
                got = other_scorer.forward_tensors(b.stage())
                times.append(round(1000 * (time.perf_counter() - t0), 1))
            want = scorer_of(other, {"compile": False, "kernels": config.get("kernels", "")}
                             ).forward_tensors(batches[2].stage())
            stale = scorer.forward_tensors(batches[2].stage())
            emit({"republish": config["name"], "case": label, "first_three_ms": times,
                  "max_diff_vs_own_eager": round(float((got[0] - want[0]).abs().nan_to_num(
                      0, 0, 0).max()), 5),
                  "max_diff_vs_old_model": round(float((got[0] - stale[0]).abs().nan_to_num(
                      0, 0, 0).max()), 5), **compile_state()})
        del scorer


def main() -> None:
    global OUT
    obs, out, modes = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
    OUT = open(out, "a")  # noqa: SIM115 - appended per line for the whole run
    import torch

    torch.backends.cuda.matmul.allow_tf32 = False  # as the service (`serve`)
    configs = json.loads(os.environ["CONFIGS"]) if os.environ.get("CONFIGS") else DEFAULT_CONFIGS
    vocabulary, frames, floors = load(obs)
    model = build(vocabulary)
    emit({"setup": True, "rows": len(frames), "gpu": torch.cuda.get_device_name(0),
          "torch": torch.__version__, "configs": configs, "sizes": SIZES,
          "mean_catalog": round(sum(len(f.choices) for f in frames) / len(frames), 1)})
    table = {"republish": republish, "probe": probe, "curve": curve, "regions": regions,
             "profile": profile, "flops": flops,
             "streams": streams}
    for mode in modes:
        try:
            table[mode](model, frames, floors, configs)
        except Exception as error:  # noqa: BLE001 - a diagnostic mode failing is reported
            import traceback

            emit({"mode_failed": mode, "error": f"{type(error).__name__}: {str(error)[:400]}",
                  "trace": traceback.format_exc()[-1500:]})


if __name__ == "__main__":
    main()
