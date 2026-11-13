"""Micro-benchmark: SGD-child staging of one realistic prepared batch (CPU).

Builds a synthetic PreparedBatch shaped like a chain batch (4,126 rows, 4
minibatches, 2 groups each, ~850 MB of slabs, thousands of row refs/members/
frame layouts), writes it as a prepared hand-off file, then runs the real
`_SgdWorker._stage_work` in 1 or 2 spawned rank processes, sequentially, with
the earlier options and with the V3 staging options. Each rank also runs a
"stepper" thread (a loop of small torch ops, like the stepping thread's
launches) and reports how much slower it runs while staging is active: the
GIL/CPU interference that slows the first minibatch of each batch on the chain.

CPU only: no pinned memory or H2D (the CUDA direct path also pins/copies the
same bytes and does the same tree walks). Ranks do not need a process group to
stage; with `sgd_verify_split` the follower's chunk hashes are combined by the
same `_DataParallel._verify_split` rank 0 uses.

    PYTHONPATH=python python python/tools/bench_sgd_stage.py --megabytes 850 --batches 6

`--gc-datasets N` instead measures `sgd_gc_freeze`: one rank
holds a long-lived heap sized by `--gc-heap` (objects, standing in for the SGD
child's model, compiled graphs and caches), stages N datasets back to back (each
kept alive until the next, as a running dataset holds its batch; each followed
by a safetensors snapshot like rank 0's) and reports the automatic full
collections that fell inside, the boundary collections, the tracked-object
count and peak RSS over time (leak bound) and one full collect's time, for the
earlier automatic GC, a freeze alone and `sgd_gc_freeze` (freeze + boundary).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import pickle
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

CONFIGS = {
    "before": {},
    "after": {"lean_tree": True, "verify_split": True},
}


def build_batch(megabytes: int, rows: int, minibatches: int, groups: int) -> Any:
    import torch

    from spireagent.rl_contracts import RunSequence
    from spireagent.rl_dataplane_ports import BinaryRef
    from stpd.p1.tensor_input import JointNodes, Reduction
    from stpd.rl.prepared_batch import (
        AdvantageNormalization,
        BatchMember,
        FrameLayout,
        PreparedBatch,
        PreparedGroup,
        PreparedMinibatch,
        PrepareRequest,
    )

    members = tuple(BatchMember(f"segment-{i:06d}",
                                BinaryRef("fmt", f"slot-{i}", "0" * 64, 1 << 20),
                                f"run-{i % 97}", 16, "1" * 64, f"episode-{i // 16}")
                    for i in range(rows // 16))
    per_minibatch = rows // minibatches
    request = PrepareRequest(
        identity=RunSequence("run", "generation", 7), members=members, auxiliary=(),
        minibatch_rows=tuple(tuple(range(m * per_minibatch, (m + 1) * per_minibatch))
                             for m in range(minibatches)),
        auxiliary_minibatches=(), compiler_identity={"compiler": "bench"},
        selection_rng_state=(1, 2, 3), max_output_bytes=1 << 34)
    row_refs: tuple[Any, ...] = tuple({"run_id": "run", "segment_id": f"segment-{i // 16:06d}",
                      "training_generation": "generation", "row": i % 16, "key": f"k{i}"}
                     for i in range(rows))
    frames = tuple(FrameLayout(("play", "end_turn", "potion", "card", "target"), 24, 60, 32)
                   for _ in range(rows))
    group_bytes = megabytes * 1024**2 // (minibatches * groups)
    tensors_per_group = 24
    elements = group_bytes // tensors_per_group // 4

    def group(index: int, members_: tuple[int, ...]) -> Any:
        reduction = Reduction(tuple((b, b + 8, torch.arange(8)) for b in range(0, 64, 8)),
                              torch.arange(64))
        return PreparedGroup(
            rows=members_, policy=True,
            joint=JointNodes({f"j{k}": torch.randn(elements // 8) for k in range(4)},
                             {"nodes": reduction}),
            tensors={f"t{k}": torch.randn(elements) for k in range(tensors_per_group - 1)})

    batch_minibatches = []
    for m in range(minibatches):
        span = request.minibatch_rows[m]
        size = len(span) // groups
        batch_minibatches.append(PreparedMinibatch(
            groups=tuple(group(g, tuple(span[g * size:(g + 1) * size])) for g in range(groups)),
            policy_rows=len(span), td_rows=len(span), terminal_rows=0, hp_rows=0,
            tensors={f"m{k}": torch.randn(len(span)) for k in range(8)}))
    n = rows
    return PreparedBatch(
        request=request, row_refs=row_refs, frames=frames, kinds=cast(Any, ("ppo",) * n),
        floors=torch.zeros(n, dtype=torch.long), actions=torch.zeros(n, dtype=torch.long),
        catalog_offsets=torch.arange(n + 1), behavior_probabilities=torch.rand(5 * n),
        behavior_outcomes=torch.rand(n, 51), outcome_targets=torch.rand(n, 51),
        hp_targets=torch.rand(n), advantages=torch.randn(n),
        policy_mask=torch.ones(n, dtype=torch.bool), td_mask=torch.ones(n, dtype=torch.bool),
        terminal_mask=torch.zeros(n, dtype=torch.bool), hp_mask=torch.zeros(n, dtype=torch.bool),
        normalization=AdvantageNormalization(n, 0.0, 1.0), minibatches=tuple(batch_minibatches),
        prepared_at=0.0, payload_bytes=0)


def write_handoff(batch: Any, directory: Path) -> dict[str, Any]:
    import mmap

    from stpd.rl import batch_preparation as bp
    from stpd.rl.binary_preparation import prepared_digest

    tree, arrays = bp.pack_transport(batch)
    path = directory / "prepared.bin"
    specs, cursor = [], 0
    for array in arrays:
        alignment = array.dtype.itemsize
        cursor = (cursor + alignment - 1) // alignment * alignment
        specs.append((cursor, array.size, array.dtype.str))
        cursor += array.nbytes
    with path.open("wb") as stream:
        stream.truncate(cursor)
    with path.open("r+b") as stream:
        mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_WRITE)
        for (offset, _, _), array in zip(specs, arrays, strict=True):
            mapping[offset:offset + array.nbytes] = memoryview(array).cast("B")
        digest = prepared_digest(memoryview(mapping)[:cursor], 4)
        mapping.close()
    return {"dataset": 7, "path": str(path), "tree": pickle.dumps(tree, protocol=5),
            "arrays": specs, "used_bytes": cursor, "sha256": digest,
            "minibatch_count": len(batch.minibatches)}


def _stepper(stop: threading.Event, samples: list[tuple[float, float]]) -> None:
    import torch

    a = torch.randn(64, 64)
    while not stop.is_set():
        began = time.perf_counter()
        for _ in range(50):  # ~launch-sized ops, each drops and retakes the GIL
            a = torch.tanh(a * 1.0001)
        samples.append((began, time.perf_counter() - began))


def stage_worker(rank: int, world: int, config: dict[str, Any]) -> Any:
    """The parts of a CPU `_SgdWorker` that `_stage_work` reads (no process group)."""
    import torch

    from stpd.rl import sgd_process as sp

    worker = SimpleNamespace(
        device=torch.device("cpu"), rank=rank, torch=torch, stage_executor=None, pinned=None,
        shard=(rank, world) if world > 1 else None, copy_stream=None,
        parallel=SimpleNamespace(rank=rank, world=world) if world > 1 else None,
        staging_config=sp._StageConfig(**{**sp._StageConfig().__dict__, **config}))
    worker._staged_fields = sp._SgdWorker._staged_fields.__get__(worker)
    worker._copy_stream = sp._SgdWorker._copy_stream.__get__(worker)
    return worker


def rank_main(rank: int, world: int, plan: dict[str, Any], config: dict[str, Any],
              batches: int, results: Any) -> None:
    import torch

    from stpd.rl import sgd_process as sp

    torch.set_num_threads(4)
    worker = stage_worker(rank, world, config)
    stop, samples = threading.Event(), cast(list[tuple[float, float]], [])
    stepper = threading.Thread(target=_stepper, args=(stop, samples), daemon=True)
    stepper.start()
    time.sleep(1.0)  # idle baseline for the stepper
    windows, fields_list = [], []
    for _ in range(batches):
        began = time.perf_counter()
        _, fields = sp._SgdWorker._stage_work(cast(Any, worker), plan)
        windows.append((began, time.perf_counter()))
        fields.pop("file_identity", None)
        fields_list.append(fields)
        time.sleep(0.3)  # idle gap: the stepper's baseline between stagings
    stop.set()
    stepper.join()

    def inside(t: float) -> bool:
        return any(a <= t <= b for a, b in windows)

    busy = [s for t, s in samples if inside(t)]
    idle = [s for t, s in samples if not inside(t)]
    parts = [f.pop("verify_parts", None) for f in fields_list]
    results.put({"rank": rank, "stage_seconds": [b - a for a, b in windows],
                 "stepper_idle": statistics.median(idle) if idle else None,
                 "stepper_busy": statistics.median(busy) if busy else None,
                 "fields": fields_list[-1], "parts": parts[-1]})


def run(plan: dict[str, Any], world: int, config: dict[str, Any], batches: int) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    processes = [context.Process(target=rank_main, args=(r, world, plan, config, batches, results))
                 for r in range(world)]
    for process in processes:
        process.start()
    reports = sorted((results.get(timeout=1800) for _ in processes), key=lambda r: r["rank"])
    for process in processes:
        process.join()
        if process.exitcode != 0:
            raise SystemExit(f"rank exited {process.exitcode}")
    if config.get("verify_split") and world > 1:
        from stpd.rl import sgd_process as sp

        combiner = sp._DataParallel.__new__(sp._DataParallel)
        combiner.send = lambda kind, **fields: (_ for _ in ()).throw(
            SystemExit(f"{kind} {fields}"))
        combiner._verify_split(plan["dataset"], plan["sha256"], reports[0]["parts"],
                               {r["rank"]: r["parts"] for r in reports[1:]})
    # Ranks stage in parallel; a batch is staged when the slowest rank is done.
    per_batch = [max(values) for values in zip(*(r["stage_seconds"] for r in reports),
                                                strict=True)][1:]  # drop the cold first
    return {"world": world, "config": config or "earlier defaults",
            "stage_seconds_median": statistics.median(per_batch),
            "batches_per_second": 1 / statistics.median(per_batch),
            "ranks": [{"rank": r["rank"],
                       "stage_seconds_median": statistics.median(r["stage_seconds"][1:]),
                       "stepper_slowdown": (r["stepper_busy"] / r["stepper_idle"]
                                            if r["stepper_busy"] and r["stepper_idle"] else None),
                       "pin_seconds": round(r["fields"]["pin_seconds"], 4),
                       "verify_seconds": round(r["fields"]["verify_seconds"], 4),
                       "verify_wait_seconds": round(r["fields"]["verify_wait_seconds"], 4)}
                      for r in reports]}


def _long_lived_heap(objects: int) -> list[Any]:
    """Container objects that never die, linked like a real object graph."""
    heap: list[Any] = []
    for index in range(objects // 4):
        node = {"i": index, "attrs": [index], "parent": heap[index // 2] if heap else None}
        heap.append(node)
        heap.append((node, index))
    return heap


def gc_rank(plan: dict[str, Any], heap_objects: int, datasets: int, mode: str,
            results: Any) -> None:
    import gc
    import resource

    import torch

    from stpd.rl import sgd_process as sp
    from stpd.rl.checkpoint_codec import encode_checkpoint
    from stpd.rl.checkpoint_state import snapshot_weights

    freeze, boundary = mode != "automatic", mode == "freeze+boundary"
    weights = {f"w{i}": torch.randn(40_000) for i in range(100)}  # ~16 MB, 100 tensors

    torch.set_num_threads(4)
    # The CUDA direct path stages with no explicit `gc.collect()` per batch (the
    # CPU path in `_stage_work` has one); only automatic collections remain.
    sp.gc = SimpleNamespace(collect=lambda *a: 0)  # type: ignore[assignment]
    heap = _long_lived_heap(heap_objects)
    worker = stage_worker(0, 1, {"lean_tree": True})
    held = sp._SgdWorker._stage_work(cast(Any, worker), plan)  # first dataset: warm-up
    gc.collect()
    began = time.perf_counter()
    gc.collect()
    full_before = time.perf_counter() - began
    if boundary:
        gc.set_threshold(*gc.get_threshold()[:2], sp.GC_AUTOMATIC_FULL_OFF)
    if freeze:  # what `SgdWorker.collect_boundary` does after the first dataset
        gc.collect()
        gc.freeze()
    began = time.perf_counter()
    gc.collect()
    full_after = time.perf_counter() - began
    pauses: list[tuple[int, float]] = []
    started: list[float] = []

    def callback(phase: str, info: dict[str, Any]) -> None:
        if phase == "start":
            started.append(time.perf_counter())
        else:
            pauses.append((info["generation"], time.perf_counter() - started.pop()))

    gc.callbacks.append(callback)
    tracked, rss, boundaries = [], [], []
    for index in range(datasets):
        held = sp._SgdWorker._stage_work(cast(Any, worker), plan)
        encode_checkpoint({"weights": snapshot_weights(weights)})  # rank 0's snapshot
        if boundary:
            gc.callbacks.remove(callback)
            began = time.perf_counter()
            gc.collect()
            if len(gc.get_objects(2)) >= sp.GC_REFREEZE_OBJECTS:
                gc.freeze()
            boundaries.append(time.perf_counter() - began)
            gc.callbacks.append(callback)
        if index % max(1, datasets // 10) == 0 or index == datasets - 1:
            tracked.append(len(gc.get_objects()))
            rss.append(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    gc.callbacks.remove(callback)
    del held
    full = [s for g, s in pauses if g == 2]
    results.put({"mode": mode, "heap_objects": heap_objects,
                 "frozen": gc.get_freeze_count(),
                 "full_collect_seconds_before_freeze": round(full_before, 4),
                 "full_collect_seconds_after": round(full_after, 4),
                 "datasets": datasets, "gen2_collections": len(full),
                 "gen2_seconds": round(sum(full), 3),
                 "gen2_max_seconds": round(max(full), 4) if full else 0.0,
                 "all_gc_seconds": round(sum(s for _, s in pauses), 3),
                 "boundary_seconds_median": (round(statistics.median(boundaries), 4)
                                             if boundaries else None),
                 "boundary_seconds_max": round(max(boundaries), 4) if boundaries else None,
                 "tracked_objects": tracked, "peak_rss": rss})
    del heap  # alive until here: the long-lived heap the collections had to scan


def gc_main(plan: dict[str, Any], heap_objects: int, datasets: int) -> None:
    context = multiprocessing.get_context("spawn")
    for mode in ("automatic", "freeze", "freeze+boundary"):
        results = context.Queue()
        process = context.Process(target=gc_rank,
                                  args=(plan, heap_objects, datasets, mode, results))
        process.start()
        report = results.get(timeout=3600)
        process.join()
        if process.exitcode != 0:
            raise SystemExit(f"rank exited {process.exitcode}")
        print(json.dumps(report), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--megabytes", type=int, default=850)
    parser.add_argument("--rows", type=int, default=4126)
    parser.add_argument("--batches", type=int, default=6)
    parser.add_argument("--worlds", default="1,2")
    parser.add_argument("--gc-datasets", type=int, default=0)
    parser.add_argument("--gc-heap", type=int, default=6_000_000)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="rl-stage-bench-") as directory:
        began = time.perf_counter()
        plan = write_handoff(build_batch(args.megabytes, args.rows, 4, 2), Path(directory))
        print(json.dumps({"file_bytes": plan["used_bytes"], "tree_pickle_bytes": len(plan["tree"]),
                          "build_seconds": round(time.perf_counter() - began, 1)}), flush=True)
        if args.gc_datasets:
            gc_main(plan, args.gc_heap, args.gc_datasets)
            return
        for world in (int(w) for w in args.worlds.split(",")):
            for name, config in CONFIGS.items():
                report = run(plan, world, config, args.batches)
                print(json.dumps({"name": name, **report}), flush=True)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    main()
