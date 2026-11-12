"""Micro-benchmark of one learner assembly task on real fast segments (diagnostic only).

Runs the production worker functions in one process: each segment is written to
an arena file and decoded by `_decode_source` (derived slabs on disk, as in the
learner), then one `_assemble` task builds and writes the batch (handoff mode,
with its sha256). `--aux N` adds N auxiliary-only sources, each one row of a
member segment bound under its own index, as an aux-only source is in a batch.

Variants isolate each change, on the same inputs:
  current   the code as it is;
  gc        the cyclic collector left running during the task;
  full-aux  auxiliary-only sources unpacked whole.
The member batch digest must be equal across variants (aux frames do not enter
it here; tests/test_rl_binary_preparation.py covers aux equivalence).

The segments' own compiler identity is used for decoding (same vocabulary and
input spec; only the code digest may differ from the checkout). Nothing here is
a training, admission or CP path.

Example:
  python tools/bench_binary_assemble.py SEGMENT_DIR --rows 4096 --aux 256 \
      --variants current,gc,full-aux --work /tmp
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import hashlib
import json
import os
import random
import resource
import shutil
import sys
import tempfile
import time
from typing import Any

from spireagent.rl_contracts import HotPayloadRef, RunSequence
from stpd.evaluation.suites import derive_game_seed, seed_membership
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.input import V2_R19_INPUT_SPEC
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl import binary_preparation as binary
from stpd.rl.critic import held_out
from stpd.rl.fast_segment import decode_segment
from stpd.rl.fast_source import decode_fast_packet, fast_envelope
from stpd.rl.prepared_batch import BatchMember, PrepareRequest

OPTIONS = {"microbatch_size": 1280, "deduplicate_text": True,
           "scene_attention_area": 1 << 30, "text_attention_area": 1 << 36,
           "prepared_layout": "rows-v2"}


class SegmentCompiler(FrameCompiler):
    """Same vocabulary/spec; carries the segments' recorded identity."""

    recorded: dict[str, Any] = {}
    identity = property(lambda self: self.recorded)


def _worker_init(identity: dict[str, Any], unfreeze: bool) -> None:
    """The production initializer, then the segments' identity (and, for A/B, unfreeze)."""
    import gc

    binary._initialize(public_vocabulary(V2_R19_INPUT_SPEC), 1)
    SegmentCompiler.recorded = identity
    binary._worker_compiler = SegmentCompiler(public_vocabulary(V2_R19_INPUT_SPEC))
    if unfreeze:
        gc.unfreeze()


def _worker_decode(descriptors: list[Any]) -> float:
    began = time.perf_counter()
    for descriptor in descriptors:
        binary._decode_source(descriptor)
    return time.perf_counter() - began


def worker_decode(identity: dict[str, Any], descriptors: list[Any], unfreeze: bool) -> float:
    """All member sources decoded in one real spawned worker (per-source gc.collect included)."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn"),
                             initializer=_worker_init, initargs=(identity, unfreeze)) as pool:
        return pool.submit(_worker_decode, descriptors).result()


def load(directory: str, rows: int) -> tuple[Any, list[tuple[bytes, dict[str, Any]]]]:
    paths = sorted(glob.glob(os.path.join(directory, "**", "*.stfs"), recursive=True))
    if not paths:
        raise SystemExit(f"no .stfs segments under {directory}")
    first = open(paths[0], "rb").read()  # noqa: SIM115
    identity = decode_segment(first, expected_sha256=hashlib.sha256(first).hexdigest())[0][
        "compiler_identity"]

    SegmentCompiler.recorded = identity
    compiler = SegmentCompiler(public_vocabulary(V2_R19_INPUT_SPEC))
    seed = next(s for i in range(1, 300) if not held_out(s := derive_game_seed("bench", i))
                and not seed_membership(s))
    raws, total = [], 0
    for path in paths:
        raw = open(path, "rb").read()  # noqa: SIM115
        digest = hashlib.sha256(raw).hexdigest()
        header, _ = decode_segment(raw, expected_sha256=digest)
        envelope = fast_envelope(
            header, size=len(raw), digest=digest, run="bench",
            attestation={"actor": "bench", "intent_id": "i-" + header["segment_id"],
                         "seed": seed, "training_split": "TRAIN"}, gae_lambda=0.99)
        raws.append((raw, envelope))
        total += envelope["rows"]
        if total >= rows:
            break
    return compiler, raws


def request_for(compiler: Any, raws: list[tuple[bytes, dict[str, Any]]]) -> tuple[Any, Any]:
    sources = [decode_fast_packet(raw, envelope=env, compiler=compiler)[0] for raw, env in raws]
    count = sum(len(s.frames) for s in sources)
    order = list(range(count))
    random.Random(1).shuffle(order)
    return [s.row_refs for s in sources], PrepareRequest(
        RunSequence("bench", sources[0].generation, 1),
        tuple(BatchMember(s.segment_id, HotPayloadRef(f"bench/g/{i}", "f" * 64, 1), s.run_id,
                          len(s.frames), env["mu"]["snapshot_sha256"], env["episode"])
              for i, (s, (_raw, env)) in enumerate(zip(sources, raws, strict=True))),
        (), tuple(tuple(sorted(order[k::4])) for k in range(4)), (), compiler.identity,
        random.Random(1).getstate(), 4 * 1024**3)


@contextlib.contextmanager
def variant(name: str) -> Any:
    """`current` also runs on a checkout without these hooks (old-code A/B)."""
    saved = {hook: getattr(binary, hook) for hook in ("_collector_paused", "_only_rows")
             if hasattr(binary, hook)}
    if name == "gc":
        binary._collector_paused = contextlib.nullcontext  # type: ignore[assignment]
    elif name == "full-aux":
        binary._only_rows = lambda tree, rows: tree
    elif name != "current":
        raise SystemExit(f"unknown variant {name}")
    try:
        yield
    finally:
        for hook, value in saved.items():
            setattr(binary, hook, value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("segments")
    parser.add_argument("--rows", type=int, default=4096)
    parser.add_argument("--aux", type=int, default=256)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--variants", default="current,gc,full-aux")
    parser.add_argument("--worker-decode", action="store_true",
                        help="also time source decode in a spawned worker, frozen vs unfrozen GC")
    parser.add_argument("--work", default=None, help="node-local directory for arena files")
    args = parser.parse_args()
    compiler, raws = load(args.segments, args.rows)
    binary._worker_compiler = compiler
    row_refs, request = request_for(compiler, raws)
    work = tempfile.mkdtemp(prefix="bench-assemble-", dir=args.work)
    try:
        arena, epoch, places = os.path.join(work, "arena"), 7, []
        with open(arena, "wb") as stream:
            stream.write(epoch.to_bytes(8, "little"))
            for raw, env in raws:
                places.append((stream.tell(), len(raw), env))
                stream.write(raw)
        began = time.perf_counter()
        ready = [binary._decode_source(binary._Descriptor(arena, offset, size, epoch, env))
                 for offset, size, env in places]
        decode = time.perf_counter() - began
        if args.worker_decode:
            descriptors = [binary._Descriptor(arena + ".w", offset, size, epoch, env)
                           for offset, size, env in places]
            for unfreeze in (False, True):
                shutil.copyfile(arena, arena + ".w")  # fresh derived-slab paths per run
                seconds = worker_decode(SegmentCompiler.recorded, descriptors, unfreeze)
                for path in glob.glob(arena + ".w*"):
                    os.unlink(path)
                print(json.dumps({"worker_decode": "unfrozen" if unfreeze else "current",
                                  "sources": len(descriptors), "seconds": round(seconds, 3),
                                  "ms_per_row": round(1000 * seconds / sum(
                                      e["rows"] for _o, _s, e in places), 3)}), flush=True)
        rng, auxiliary = random.Random(3), []
        for _ in range(args.aux):
            index = rng.randrange(len(raws))
            row = rng.randrange(len(row_refs[index]))
            auxiliary.append((len(ready), row_refs[index][row], request.members[index]))
            ready.append(ready[index])
        output = os.path.join(work, "out")
        with open(output, "xb") as stream:
            stream.truncate(request.max_output_bytes + 64)
        for name in args.variants.split(","):
            runs = []
            with variant(name):
                for _ in range(args.reps):
                    began = time.perf_counter()
                    result = binary._assemble(
                        tuple(ready), request, tuple(range(len(raws))), tuple(auxiliary),
                        dict(OPTIONS), frozenset(), time.perf_counter(), output, True)
                    timings = result.summary["timings"]
                    runs.append({"task_s": time.perf_counter() - began,
                                 "decode_s": timings["binary_decode_seconds"],
                                 "assemble_s": timings["binary_assemble_seconds"],
                                 "metadata_s": timings.get("metadata", 0.0),
                                 "sha256": result.summary["sha256"]})
            best = min(runs, key=lambda r: r["task_s"])
            print(json.dumps({
                "variant": name, "rows": sum(m.rows for m in request.members),
                "members": len(raws), "aux_sources": args.aux, "source_decode_serial_s":
                round(decode, 2), **{k: round(v, 3) if isinstance(v, float) else v
                                     for k, v in best.items()},
                "same_digest_across_reps": len({r["sha256"] for r in runs}) == 1,
                "max_rss_gib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                                     / (2**30 if sys.platform == "darwin" else 2**20), 2)}),
                flush=True)
    finally:
        shutil.rmtree(work)


if __name__ == "__main__":
    main()
