"""Remote preparation throughput on real fast segments (diagnostic only).

`host`: one remote preparation host (`stpd.rl.remote_prepare`), decoding with the
segments' recorded compiler identity, as `bench_binary_assemble.py` does.

`learner`: a real `BinaryBatchPreparer` in hand-off mode with `remote_prepare`:
local copy + local source decode (as the learner keeps for labels), remote
`_assemble`, the batch file received into the local hand-off file. Every
hand-off file is hashed over `used_bytes` (the SGD process check) and must equal
the worker digest; `--local-check` also assembles the first batch in-process and
compares digests. Reports steady-state rows/s and bytes over the wire.

Nothing here is a training, admission or CP path. The authkey is read from
STPD_REMOTE_PREPARE_AUTH (hex) on both sides and never printed.

Example:
  python tools/bench_remote_prepare.py host SEGMENT_DIR --scratch /tmp/h \
      --decode-workers 6 --assembly-workers 16 --ready-file READY
  python tools/bench_remote_prepare.py learner SEGMENT_DIR --endpoints h1:p,h2:p \
      --connections 3 --batches 24 --inflight 9 --work /tmp/l --local-check
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses as dc
import glob
import hashlib
import json
import mmap
import os
import resource
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench_binary_assemble as bench  # noqa: E402

from spireagent.rl_dataplane_ports import BinaryRef  # noqa: E402
from stpd.p1.input import V2_R19_INPUT_SPEC  # noqa: E402
from stpd.p1.vocabulary import public_vocabulary  # noqa: E402
from stpd.rl import binary_preparation as binary  # noqa: E402
from stpd.rl import remote_prepare  # noqa: E402
from stpd.rl.fast_segment import FORMAT as FAST_FORMAT  # noqa: E402

IDENTITY_ENV = "STPD_BENCH_SEGMENT_IDENTITY"


def _initialize(vocabulary: dict[str, Any], threads: int) -> None:
    """Production initializer, then the segments' recorded identity."""
    original(vocabulary, threads)
    bench.SegmentCompiler.recorded = json.loads(Path(os.environ[IDENTITY_ENV]).read_text())
    binary._worker_compiler = bench.SegmentCompiler(vocabulary)


original = binary._initialize


def _identity(directory: str, work: str) -> None:
    compiler, _ = bench.load(directory, 1)
    path = Path(work) / "segment-identity.json"
    path.write_text(json.dumps(compiler.identity))
    os.environ[IDENTITY_ENV] = str(path)
    binary._initialize = _initialize  # pools resolve the module global at spawn


def host(argv: list[str]) -> None:
    directory, rest = argv[0], argv[1:]
    scratch = rest[rest.index("--scratch") + 1]
    Path(scratch).mkdir(parents=True, exist_ok=True)
    _identity(directory, scratch)
    remote_prepare.serve(rest)


class FileProvider:
    def __init__(self) -> None:
        self.packets: dict[BinaryRef, bytes] = {}

    def add(self, raw: bytes, slot: str) -> BinaryRef:
        ref = BinaryRef(FAST_FORMAT, slot, hashlib.sha256(raw).hexdigest(), len(raw))
        self.packets[ref] = raw
        return ref

    @contextlib.contextmanager
    def borrow(self, ref: BinaryRef) -> Any:
        yield memoryview(self.packets[ref])

    def retain(self, ref: Any, owner: Any) -> None:
        raise AssertionError("not used")

    release = retain


def _cpu() -> float:
    own, children = (resource.getrusage(w) for w in (resource.RUSAGE_SELF,
                                                     resource.RUSAGE_CHILDREN))
    return own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime


def _file_digest(path: str, used: int) -> str:
    with open(path, "rb") as stream:
        mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
    try:
        return hashlib.sha256(memoryview(mapping)[:used]).hexdigest()
    finally:
        mapping.close()


def learner(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("segments")
    parser.add_argument("--endpoints", required=True)
    parser.add_argument("--connections", type=int, default=2)
    parser.add_argument("--codec", choices=remote_prepare.CODECS, default="none",
                        help="must match the hosts' --codec")
    parser.add_argument("--rows", type=int, default=4096)
    parser.add_argument("--batches", type=int, default=12)
    parser.add_argument("--inflight", type=int, default=6)
    parser.add_argument("--decode-workers", type=int, default=12)
    parser.add_argument("--total", type=int, default=0,
                        help="batches to submit, cycling the loaded ones (0 = each once); "
                        "run hosts with a small --cache-bytes so cycled sources are re-sent")
    parser.add_argument("--warmup", type=int, default=2, help="batches excluded from the rate")
    parser.add_argument("--work", default=None)
    parser.add_argument("--local-check", action="store_true")
    args = parser.parse_args(argv)
    work = tempfile.mkdtemp(prefix="bench-remote-", dir=args.work)
    _identity(args.segments, work)
    compiler, raws = bench.load(args.segments, args.rows * (args.batches + 1))
    vocabulary = public_vocabulary(V2_R19_INPUT_SPEC)
    batches, chunk, total = [], [], 0
    for raw, env in raws:
        chunk.append((raw, env))
        total += env["rows"]
        if total >= args.rows:
            batches.append(chunk)
            if len(batches) == args.batches:
                break
            chunk, total = [], 0
    provider = FileProvider()
    plans = []
    for b, chunk in enumerate(batches):
        _refs, request = bench.request_for(compiler, chunk)
        refs = [provider.add(raw, f"bench/{b}/{i}") for i, (raw, _env) in enumerate(chunk)]
        request = dc.replace(
            request, max_output_bytes=1536 * 1024**2,
            members=tuple(dc.replace(m, payload=ref)
                          for m, ref in zip(request.members, refs, strict=True)))
        plans.append((request, [(ref, env) for ref, (_raw, env) in zip(refs, chunk, strict=True)]))
    batch_raw = max(sum(len(raw) for raw, _ in chunk) for chunk in batches)
    slots = 2 * (args.inflight + 2)
    preparer = binary.BinaryBatchPreparer(
        provider=provider, vocabulary=vocabulary, workers=args.decode_workers, threads=1,
        scratch=work, source_slots=slots, max_source_bytes=slots * (batch_raw + 1024**2),
        assembly_workers=1, prepared_batches=args.inflight, handoff=True,
        persistent_pools=True, max_prepared_bytes=args.inflight * 1536 * 1024**2,
        max_pinned_bytes=64 * 1024**2,
        remote_prepare={"endpoints": args.endpoints.split(","),
                        "connections": args.connections, "codec": args.codec},
        **dict[str, Any](bench.OPTIONS))
    hasher = ThreadPoolExecutor(4)
    try:
        began = time.perf_counter()
        pending: list[tuple[int, Any, float]] = []
        done: list[dict[str, Any]] = []
        checks = []
        local: str | None = None
        local_batch: Any = None
        remote_first: dict[str, Any] = {}
        if args.local_check:  # before the timed loop: it holds this process's GIL
            binary._worker_compiler = compiler
            local, local_batch = _local_digest(plans[0][0], batches[0], work)
            began = time.perf_counter()
        cpu_began = _cpu()
        first: dict[int, str] = {}

        def finish() -> None:
            index, future, submitted = pending.pop(0)
            handoff = future.result()
            at = time.perf_counter()
            first.setdefault(index, handoff.sha256)
            if index == 0 and local is not None:
                remote_first["batch"] = _unpack(handoff.path, handoff.tree, handoff.arrays)
                remote_first["cpu"] = preparer.remote.served_by(handoff.path)
            checks.append((index, handoff, hasher.submit(_file_digest, handoff.path,
                                                         handoff.used_bytes)))
            done.append({"batch": index, "rows": sum(m.rows for m in handoff.request.members),
                         "at": at - began, "latency": at - submitted,
                         "bytes": handoff.used_bytes,
                         "host_s": handoff.timings.get("binary_remote_host_seconds"),
                         "sent": handoff.timings.get("binary_remote_sources_sent"),
                         "asm_cpu": handoff.timings.get("binary_remote_assembly_cpu_seconds"),
                         "dec_s": handoff.timings.get("binary_remote_decode_seconds"),
                         "wait": handoff.timings.get("binary_remote_wait_seconds"),
                         "exch": handoff.timings.get("binary_remote_exchange_seconds"),
                         "xfer": handoff.timings.get("binary_remote_transfer_seconds"),
                         "members": len(handoff.request.members)})

        def drain() -> None:
            # The SGD process's check, then its release: a hand-off holds credit.
            i, h, digest = checks.pop(0)
            if digest.result() != h.sha256:
                raise SystemExit(f"hand-off digest mismatch at batch {i}")
            preparer.release_handoff(h)

        for index in range(args.total or len(plans)):
            request, sources = plans[index % len(plans)]
            for ref, env in sources:
                preparer.prefetch(ref, env)
            while checks and checks[0][2].done():
                drain()
            while len(pending) + len(checks) >= args.inflight:
                if pending:
                    finish()
                else:
                    drain()
            pending.append((index, preparer.submit(request), time.perf_counter()))
        while pending:
            finish()
        while checks:
            drain()
        learner_cpu = _cpu() - cpu_began
        warm = args.warmup if 0 < args.warmup < len(done) - 1 else 0
        steady = done[warm:]
        span = steady[-1]["at"] - (done[warm - 1]["at"] if warm else 0.0)
        rows = sum(d["rows"] for d in steady)
        print(json.dumps({
            "batches": len(done), "rows_per_s": round(rows / span, 1),
            "rows_per_h_million": round(rows / span * 3600 / 1e6, 2),
            "wire_GB_per_s": round(sum(d["bytes"] for d in steady) / span / 1e9, 3),
            "codec": args.codec,
            "mean_latency_s": round(sum(d["latency"] for d in steady) / len(steady), 2),
            "mean_host_s": round(sum(d["host_s"] or 0 for d in steady) / len(steady), 2),
            **{f"mean_{k}_s": round(sum(d[k] or 0 for d in steady) / len(steady), 2)
               for k in ("wait", "exch", "xfer")},
            "host_assemble_ms_core_per_row": round(1000 * sum(d["asm_cpu"] for d in done)
                                                   / sum(d["rows"] for d in done), 2),
            "host_decode_ms_core_per_row": round(1000 * sum(d["dec_s"] for d in done) / max(
                1e-9, sum(d["sent"] * d["rows"] / d["members"] for d in done)), 2),
            # Whole learner process incl. its hand-off sha256 check (the SGD
            # process's work in production) and any local worker children.
            "learner_ms_core_per_row": round(1000 * learner_cpu / sum(d["rows"] for d in done),
                                             2),
            "bytes_per_row": round(sum(d["bytes"] for d in done) / sum(d["rows"] for d in done)),
            "remote": preparer.remote.stats, "all_handoff_digests_equal_worker_digest": True,
            "local_check": None if local is None else _check(
                local, first[0], local_batch, remote_first, preparer.remote.cpu),
        }), flush=True)
        for row in done:
            print(json.dumps(row), flush=True)
    finally:
        preparer.shutdown(wait=True, cancel_futures=True)
        hasher.shutdown(wait=True)
        shutil.rmtree(work, ignore_errors=True)


def _check(local: str, remote: str, local_batch: Any, remote_first: dict[str, Any],
           cpu: dict[str, str]) -> dict[str, Any]:
    """Bytes when host and learner CPU fingerprints match, else per-array tolerance."""
    if not cpu or not remote_first.get("cpu"):  # unknown is not "different": fail loudly
        raise SystemExit("local_check needs both CPU fingerprints")
    same_cpu = remote_first["cpu"] == cpu
    out: dict[str, Any] = {"same_cpu": same_cpu, "bytes_equal": local == remote,
                           "learner_cpu": cpu, "host_cpu": remote_first["cpu"]}
    out.update(compare(local_batch, remote_first["batch"]))
    out["pass"] = out["bytes_equal"] if same_cpu else out["within_tolerance"]
    return out


IGNORED = ("timings", "prepared_at", "worker_pids")


def _unpack(path: str, tree: bytes, arrays: Any) -> Any:
    """A PreparedBatch read back from a `_write_batch` file (owns its bytes)."""
    import pickle

    import numpy as np

    from stpd.rl import batch_preparation as bp

    data = Path(path).read_bytes()
    views = tuple(np.frombuffer(data, dtype=np.dtype(a.dtype), count=a.count, offset=a.offset)
                  for a in arrays)
    return bp.unpack_transport((pickle.loads(tree), views))


def compare(a: Any, b: Any, *, rtol: float = 1e-5, atol: float = 1e-5) -> dict[str, Any]:
    """Cross-CPU verification: integers/structure exact, floats within tolerance."""
    import numpy as np
    import torch

    exact: list[str] = []
    floats: list[dict[str, Any]] = []

    def walk(x: Any, y: Any, path: str) -> None:
        if any(name in path for name in IGNORED):
            return
        if dc.is_dataclass(x) and not isinstance(x, type):
            for field in dc.fields(x):
                walk(getattr(x, field.name), getattr(y, field.name), f"{path}.{field.name}")
        elif isinstance(x, dict):
            if set(x) != set(y):
                exact.append(path)
                return
            for key in x:
                walk(x[key], y[key], f"{path}[{key!r}]")
        elif isinstance(x, (list, tuple)):
            if len(x) != len(y):
                exact.append(path)
                return
            for i, (u, v) in enumerate(zip(x, y, strict=True)):
                walk(u, v, f"{path}[{i}]")
        elif isinstance(x, (torch.Tensor, np.ndarray)):
            u = torch.as_tensor(np.array(x)) if isinstance(x, np.ndarray) else x.detach().cpu()
            v = torch.as_tensor(np.array(y)) if isinstance(y, np.ndarray) else y.detach().cpu()
            if u.shape != v.shape or u.dtype != v.dtype:
                exact.append(path)
            elif u.is_floating_point():
                if not torch.equal(torch.isnan(u), torch.isnan(v)):
                    exact.append(path)
                    return
                d = (u.double() - v.double()).abs().nan_to_num(0)
                if d.max() > 0:
                    floats.append({"path": path, "max_abs": float(d.max()),
                                   "ok": bool(torch.allclose(u.double(), v.double(), rtol=rtol,
                                                             atol=atol, equal_nan=True))})
            elif not torch.equal(u, v):
                exact.append(path)
        elif type(x) is float and type(y) is float:  # e.g. normalization statistics
            if x != y:
                floats.append({"path": path, "max_abs": abs(x - y),
                               "ok": abs(x - y) <= atol + rtol * abs(y)})
        elif x != y:
            exact.append(path)

    walk(a, b, "batch")
    return {"exact_mismatch": exact, "float_diffs": floats,
            "within_tolerance": not exact and all(f["ok"] for f in floats)}


def _local_digest(request: Any, chunk: list[tuple[bytes, dict[str, Any]]],
                  work: str) -> tuple[str, Any]:
    """The same batch assembled in this process by the production functions."""
    arena = os.path.join(work, "local-arena")
    places = []
    with open(arena, "wb") as stream:
        stream.write((3).to_bytes(8, "little"))
        for raw, env in chunk:
            places.append((stream.tell(), len(raw), env))
            stream.write(raw)
    ready = tuple(binary._decode_source(binary._Descriptor(arena, offset, size, 3, env))
                  for offset, size, env in places)
    output = os.path.join(work, "local-out")
    with open(output, "xb") as stream:
        stream.truncate(request.max_output_bytes + 64)
    try:
        result = binary._assemble(ready, request, tuple(range(len(ready))), (),
                                  dict(bench.OPTIONS), frozenset(), time.perf_counter(),
                                  output, True)
        return str(result.summary["sha256"]), _unpack(output, result.tree, result.arrays)
    finally:
        for path in glob.glob(arena + "*") + [output]:
            os.unlink(path)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("host", "learner"):
        raise SystemExit(__doc__)
    (host if sys.argv[1] == "host" else learner)(sys.argv[2:])


if __name__ == "__main__":
    main()
