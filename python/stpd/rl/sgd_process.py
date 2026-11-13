"""RL5 S3 I1: one spawned SGD process owns theta/Adam/EMA, torch/CUDA RNG and the GPU.

Explicitly selected alternative E4 implementation (`dataplane.sgd_process: true`,
binary socket profile only). The original learner process becomes the controller:
WAL, socket, queue/leases, selection RNG, auxiliary pools and uses, first-use
counts and CP assembly/publication stay there. The SGD process reports facts per
optimizer attempt; the controller charges them in order. `update` is a fence,
never a second ledger. CP schema v3 is unchanged and only assembled at a dataset
boundary from a state whose counters equal the controller's.

Protocol `stpd/rl-sgd-process-1`: one ordered spawn Pipe of small messages, each
with protocol/run/generation and a gapless sequence. Bulk bytes are node-local
files with sha256: the existing prepared-batch output, and tensor-tree files for
init, mu weights and CP state. Any crash, hang, gap or binding mismatch is fatal
for this generation: no in-process fallback, no same-generation child restart.
"""

from __future__ import annotations

import atexit
import ctypes
import dataclasses
import gc
import hashlib
import math
import mmap
import multiprocessing
import os
import pickle
import queue
import signal
import sys
import threading
import time
from collections import OrderedDict, deque
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from spireagent.json_boundary import BoundaryError

from .offline_step import OfflineDumpOptions, dump_options

PROTOCOL = "stpd/rl-sgd-process-1"
INIT_SCHEMA = "stpd/rl-sgd-init-1"
WEIGHTS_SCHEMA = "stpd/rl-sgd-weights-1"
STATE_SCHEMA = "stpd/rl-sgd-state-1"
GiB = 1024**3
OPTION_KEYS = frozenset((
    "sgd_process", "sgd_stall_seconds", "sgd_start_seconds", "sgd_shutdown_seconds",
    "sgd_cpus", "sgd_offline_step_seconds", "memory_limit_bytes",
    "memory_headroom_fraction", "memory_budget",
    "sgd_offline_dump_every", "sgd_offline_dump_limit", "sgd_offline_dump_directory",
    "sgd_data_parallel", "sgd_compile", "sgd_stage_thread", "sgd_prestart",
))
# Threads that hash one prepared file's chunks (see `prepared_digest`).
VERIFY_THREADS = 4
DATA_PARALLEL_KEYS = frozenset(("world", "backend", "timeout_seconds"))
COMPILE_KEYS = frozenset(("cache_dir", "recompile_limit", "warmup", "size_guard"))
# Per-process bases from measurement where one exists (margin ~15-25%); override
# with `dataplane.memory_budget` once a run measures better. The check refuses;
# it never shrinks a limit.
DECLARED_BYTES = {
    # Not yet measured for the binary-socket controller without CUDA (RL4.1 I1's
    # in-process learner, CUDA included, peaked at 3.21 GiB RSS): declared.
    "controller_base": 6 * GiB,
    # RL4.1 I1: max single spawn-worker RSS 1.30 GiB (shared arena pages included).
    "worker_process": 3 * GiB // 2,
    # RL4.1 I1: publisher lifetime RSS 947 MiB; the budget adds 3 x model bytes.
    "publisher_process": 1 * GiB,
    # S3 I1 GPU replay (Friday job 1444109, A100): SGD process RSS 2.18 GiB after
    # steps incl. 0.21 GiB pinned -> 1.97 GiB base; state capture added 0.15 GiB.
    "sgd_base_cuda": 5 * GiB // 2,
    # CPU tests only.
    "sgd_base_cpu": 2 * GiB,
}
MEMORY_FILESYSTEMS = frozenset(("tmpfs", "ramfs", "devtmpfs", "unknown"))


@dataclass(frozen=True)
class SgdOptions:
    stall_seconds: float = 120.0  # floor; a step wait is max(floor, 10 x rolling P95)
    start_seconds: float = 600.0
    shutdown_seconds: float = 60.0
    cpus: tuple[int, ...] | None = None
    offline_step_seconds: float | None = None
    memory_limit_bytes: int | None = None
    headroom: float = 0.20
    declared: dict[str, int] = field(default_factory=dict)
    # C-SCALE: sampled real online minibatches as offline-step files; None = off.
    offline_dump: OfflineDumpOptions | None = None
    # WP8: {"world", "backend", "timeout_seconds"}; None = one device (default).
    data_parallel: dict[str, Any] | None = None
    # WP8: {"cache_dir", "recompile_limit", "warmup"} for `prepared_compile`;
    # None keeps torch's defaults and no warm-up (default).
    compile: dict[str, Any] | None = None
    # WP8: stage the next prepared batch (verify, pin, H2D) on one side thread
    # while the current dataset steps; False keeps staging inline (default).
    stage_thread: bool = False
    # V3: the controller authorizes the next staged dataset while it still
    # accounts the current one's answers, so the child starts it back to back.
    # At most one dataset beyond the current one; never across a CP request,
    # STOP, deadline or fence (a stop arriving later still finishes the
    # authorized one). Theta/KL/PPO match lockstep; aux selection timing may
    # differ. Default on; False is the rollback to the lockstep start.
    prestart: bool = True


# `prepared_compile` defaults (V3 T6 probes 1458888/1458938): the levered block plus
# node builder reach 11 graphs / 7 recompiles of one function in 300 real steps, over
# torch's limit of 8 when shapes vary more; a cold-cache step can recompile several
# times in a row (13-23 s each), which the 120 s stall floor would misreport.
COMPILED_RECOMPILE_LIMIT = 16
COMPILED_STALL_SECONDS = 300.0


def compiled_defaults(options: SgdOptions, dataplane: dict[str, Any]) -> SgdOptions:
    """Defaults for a learner that selected `prepared_compile`; explicit values win."""
    compiled = dict(options.compile) if options.compile is not None else {
        "cache_dir": None, "recompile_limit": None, "warmup": [], "size_guard": False}
    if compiled["recompile_limit"] is None:
        compiled["recompile_limit"] = COMPILED_RECOMPILE_LIMIT
    stall = (options.stall_seconds if "sgd_stall_seconds" in dataplane
             else max(options.stall_seconds, COMPILED_STALL_SECONDS))
    return replace(options, compile=compiled, stall_seconds=stall)


def sgd_options(dataplane: dict[str, Any] | None) -> SgdOptions | None:
    """None keeps the default in-process E4 byte-identical."""
    if dataplane is None:
        return None
    selected = dataplane.get("sgd_process", False)
    if type(selected) is not bool:
        raise ValueError("sgd_process_selection_must_be_boolean")
    extra = {key for key in dataplane if key in OPTION_KEYS and key != "sgd_process"}
    if not selected:
        if extra:
            raise ValueError("sgd_process_options_without_explicit_selection")
        return None

    def number(key: str, default: float, low: float, high: float) -> float:
        value = dataplane.get(key, default)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError("invalid_" + key)
        return float(value)

    cpus = dataplane.get("sgd_cpus")
    if cpus is not None and (not isinstance(cpus, list) or not cpus or len(set(cpus)) != len(cpus)
                             or any(type(cpu) is not int or cpu < 0 for cpu in cpus)):
        raise ValueError("invalid_sgd_cpus")
    offline = dataplane.get("sgd_offline_step_seconds")
    if offline is not None and (type(offline) not in (int, float) or not 0 < offline < 3600):
        raise ValueError("invalid_sgd_offline_step_seconds")
    limit = dataplane.get("memory_limit_bytes")
    if limit is not None and (type(limit) is not int or limit < GiB):
        raise ValueError("invalid_memory_limit_bytes")
    declared = dataplane.get("memory_budget", {})
    if (not isinstance(declared, dict) or set(declared) - set(DECLARED_BYTES)
            or any(type(value) is not int or value < 0 for value in declared.values())):
        raise ValueError("invalid_memory_budget_declarations")
    parallel = dataplane.get("sgd_data_parallel")
    if parallel is not None:
        timeout: Any = (parallel.get("timeout_seconds", 600) if isinstance(parallel, dict)
                        else None)
        if (not isinstance(parallel, dict) or set(parallel) - DATA_PARALLEL_KEYS
                or type(parallel.get("world")) is not int or not 2 <= parallel["world"] <= 8
                or parallel.get("backend", "nccl") not in ("nccl", "gloo")
                or type(timeout) not in (int, float) or not 10 <= timeout <= 3600):
            raise ValueError("invalid_sgd_data_parallel")
        parallel = {"world": parallel["world"], "backend": parallel.get("backend", "nccl"),
                    "timeout_seconds": float(timeout)}
    compiled = dataplane.get("sgd_compile")
    if compiled is not None:
        limit_value = compiled.get("recompile_limit") if isinstance(compiled, dict) else None
        warm = compiled.get("warmup", []) if isinstance(compiled, dict) else None
        cache_dir = compiled.get("cache_dir") if isinstance(compiled, dict) else None
        size_guard = compiled.get("size_guard", False) if isinstance(compiled, dict) else None
        if (not isinstance(compiled, dict) or set(compiled) - COMPILE_KEYS
                or type(size_guard) is not bool
                or (limit_value is not None and (type(limit_value) is not int
                                                 or not 8 <= limit_value <= 64))
                or not isinstance(warm, list) or len(warm) > 8
                or any(not isinstance(path, str) or not path for path in warm)
                or (cache_dir is not None and (not isinstance(cache_dir, str) or not cache_dir))):
            raise ValueError("invalid_sgd_compile")
        compiled = {"cache_dir": cache_dir, "recompile_limit": limit_value,
                    "warmup": list(warm), "size_guard": size_guard}
    stage_thread = dataplane.get("sgd_stage_thread", False)
    if type(stage_thread) is not bool:
        raise ValueError("invalid_sgd_stage_thread")
    prestart = dataplane.get("sgd_prestart", True)
    if type(prestart) is not bool:
        raise ValueError("invalid_sgd_prestart")
    return SgdOptions(
        stall_seconds=number("sgd_stall_seconds", 120, 1, 3600),
        start_seconds=number("sgd_start_seconds", 600, 1, 3600),
        shutdown_seconds=number("sgd_shutdown_seconds", 60, 1, 600),
        cpus=tuple(cpus) if cpus is not None else None,
        offline_step_seconds=float(offline) if offline is not None else None,
        memory_limit_bytes=limit,
        headroom=number("memory_headroom_fraction", 0.20, 0.0, 0.9),
        declared=dict(declared),
        offline_dump=dump_options(dataplane),
        data_parallel=parallel,
        compile=compiled,
        stage_thread=stage_thread,
        prestart=prestart,
    )


# --- files -----------------------------------------------------------------


def write_tree(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    """Single-writer derived file; published by rename after the full write."""
    from stpd.rl.checkpoint_codec import encode_checkpoint

    raw = encode_checkpoint(value)
    temporary = path.with_name("." + path.name + ".partial")
    with temporary.open("xb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(raw)
    temporary.replace(path)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def read_tree(file: dict[str, Any], schema: str) -> dict[str, Any]:
    from stpd.rl.checkpoint_codec import decode_checkpoint

    raw = Path(file["path"]).read_bytes()
    if len(raw) != file["size"] or hashlib.sha256(raw).hexdigest() != file["sha256"]:
        raise BoundaryError("rl_sgd_process", "sgd_file_integrity_changed")
    value = decode_checkpoint(raw)
    if value.get("schema") != schema:
        raise BoundaryError("rl_sgd_process", "sgd_file_schema_changed")
    return value


def weights_digest(weights: dict[str, Any]) -> str:
    """Exact bytes of every named tensor; equal on CPU and after a device copy."""
    import torch

    result = hashlib.sha256()
    for name in sorted(weights):
        tensor = weights[name].detach().cpu().contiguous()
        result.update(name.encode())
        result.update(str((tensor.dtype, tuple(tensor.shape))).encode())
        result.update(tensor.reshape(-1).view(torch.uint8).numpy().tobytes())
    return result.hexdigest()


# --- memory budget ---------------------------------------------------------


def _filesystem(path: Path) -> str:
    path = path.resolve()
    if sys.platform == "linux":
        mounts = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            left, right = line.split(" - ", 1)
            mount = left.split()[4].replace("\\040", " ")
            if path == Path(mount) or Path(mount) in path.parents:
                mounts.append((len(mount), right.split()[0]))
        return max(mounts)[1] if mounts else "unknown"
    if sys.platform == "darwin":
        return "darwin_local_mount"
    return "unknown"


def _cgroup_limit() -> tuple[int | None, str]:
    """Smallest memory limit on this process's cgroup path (a Slurm job sets an ancestor)."""
    if sys.platform == "linux":
        with suppress(OSError, ValueError):
            for line in Path("/proc/self/cgroup").read_text().splitlines():
                hierarchy, controllers, relative = line.split(":", 2)
                if hierarchy == "0" and controllers == "":
                    root, name, source = Path("/sys/fs/cgroup"), "memory.max", "cgroup_v2"
                elif "memory" in controllers.split(","):
                    root, name = Path("/sys/fs/cgroup/memory"), "memory.limit_in_bytes"
                    source = "cgroup_v1"
                else:
                    continue
                limits = []
                directory = root / relative.strip("/")
                while True:
                    with suppress(OSError, ValueError):
                        raw = (directory / name).read_text().strip()
                        if raw != "max" and int(raw) < 1 << 60:
                            limits.append(int(raw))
                    if directory == root:
                        break
                    directory = directory.parent
                if limits:
                    return min(limits), source
    with suppress(ValueError, OSError, AttributeError):
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"), "physical_memory"
    return None, "unavailable"


def memory_budget(*, options: SgdOptions, device: str, model_bytes: int,
                  source_bytes: int, prepare_source_bytes: int, prepared_bytes: int,
                  pinned_bytes: int, wal_bytes: int, workers: int,
                  paths: dict[str, Path]) -> dict[str, Any]:
    """Itemized upper bounds for the controller and SGD process in one cgroup.

    File-backed bounds count only on memory filesystems (tmpfs/unknown); on a
    disk they are reclaimable page cache and are reported, not summed.
    """
    declared = {**DECLARED_BYTES, **options.declared}
    filesystems = {name: _filesystem(path) for name, path in paths.items()}
    cuda = device.startswith("cuda")
    items = [
        ("controller", "controller_base", declared["controller_base"], "anonymous", "declared"),
        ("controller", "checkpoint_assembly", 8 * model_bytes, "anonymous",
         "derived: decoded state 4x + encoded CP 4x model bytes"),
        ("controller", "worker_processes", workers * declared["worker_process"], "anonymous",
         f"declared: {workers} spawn workers"),
        ("controller", "publisher_process", declared["publisher_process"] + 3 * model_bytes,
         "anonymous", "declared + derived 3x model bytes"),
        ("controller", "source_spool", source_bytes, filesystems["sources"], "configured"),
        ("controller", "prepare_arenas", prepare_source_bytes, filesystems["arenas"],
         "configured"),
        ("controller", "prepared_outputs", prepared_bytes, filesystems["arenas"], "configured"),
        ("controller", "wal", wal_bytes, filesystems["wal"], "configured"),
        ("sgd", "sgd_base", declared["sgd_base_cuda" if cuda else "sgd_base_cpu"], "anonymous",
         "declared"),
        ("sgd", "pinned_slots" if cuda else "owned_batches", pinned_bytes,
         "pinned" if cuda else "anonymous",
         "configured: two-slot cap = prepared_bytes, same cap as the in-process pinned slots"),
        ("sgd", "state_capture", 8 * model_bytes, "anonymous",
         "derived: CPU state 4x + encoded file 4x model bytes"),
    ]
    if options.data_parallel is not None:
        followers = options.data_parallel["world"] - 1
        items.append(("sgd", "data_parallel_ranks", followers * (
            declared["sgd_base_cuda" if cuda else "sgd_base_cpu"] + pinned_bytes),
            "pinned" if cuda else "anonymous",
            f"declared: {followers} follower rank(s) x (SGD base + two-slot pinned cap)"))
    if options.offline_dump is not None:
        items.append(("sgd", "offline_dump", 8 * model_bytes + prepared_bytes // 2, "anonymous",
                      "derived: state 8x model bytes + CPU and encoded copy of one minibatch "
                      "(<= 1/4 of one dataset; prepared_bytes bounds two datasets)"))
    limit, limit_source = ((options.memory_limit_bytes, "profile_memory_limit_bytes")
                           if options.memory_limit_bytes is not None else _cgroup_limit())
    rows, counted = [], 0
    for process, name, size, kind, source in items:
        resident = kind in {"anonymous", "pinned"} or kind in MEMORY_FILESYSTEMS
        counted += size if resident else 0
        rows.append({"process": process, "item": name, "bytes": size, "kind": kind,
                     "source": source, "counted": resident})
    usable = None if limit is None else int(limit * (1 - options.headroom))
    return {"schema": "stpd/rl-sgd-memory-budget-1", "items": rows, "counted_bytes": counted,
            "limit_bytes": limit, "limit_source": limit_source, "headroom": options.headroom,
            "usable_bytes": usable, "filesystems": filesystems, "model_bytes": model_bytes,
            "fits": usable is not None and counted <= usable,
            "rule": "refuse to start when counted upper bounds exceed (1-headroom) x limit; "
                    "nothing is shrunk automatically"}


def mapped_arrays(mapping: Any, specs: Any) -> tuple[Any, ...]:
    """Views of one prepared file's slabs.

    An empty array shares its file offset with the next same-dtype array. Pinning
    re-packs by storage pointer and would alias that array to a zero-length slab,
    so empty arrays get their own (empty) buffer.
    """
    return tuple(np.frombuffer(mapping, dtype=np.dtype(dtype), count=count, offset=offset)
                 if count else np.empty(0, dtype=np.dtype(dtype))
                 for offset, count, dtype in specs)


def _file_identity(stat: os.stat_result) -> tuple[int, ...]:
    """Which file a rank mapped: rank 0 verifies it, followers must map the same."""
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


class _Digest(threading.Thread):
    """`prepared_digest` of one mapped prepared file, off the staging thread.

    Chunk hashes run on `threads` threads (hashlib drops the GIL), alongside the
    pin/H2D of the same file.
    """

    def __init__(self, view: memoryview, threads: int) -> None:
        super().__init__(name="rl-sgd-verify", daemon=True)
        self.view, self.threads = view, threads
        self.value: str | None = None
        self.error: BaseException | None = None
        self.seconds = 0.0
        self.start()

    def run(self) -> None:
        began = time.perf_counter()
        try:
            from .binary_preparation import prepared_digest

            self.value = prepared_digest(self.view, self.threads)
        except BaseException as error:  # re-raised by the staging caller
            self.error = error
        finally:
            self.view.release()
            self.seconds = time.perf_counter() - began


# --- controller handle -----------------------------------------------------


class SgdProcessFailed(BoundaryError):
    def __init__(self, code: str, facts: dict[str, Any]) -> None:
        super().__init__("rl_sgd_process", code)
        self.facts = {"component": "sgd_process", "code": code, **facts}


class SgdProcess:
    """Controller-side owner of the one SGD child for this generation."""

    def __init__(self, *, run: str, generation: str, root: Path, options: SgdOptions,
                 on_staged: Any) -> None:
        self.run, self.generation, self.options = run, generation, options
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.on_staged = on_staged
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe(duplex=True)
        self.process = context.Process(target=_child_main, args=(child, os.getpid()),
                                       name="rl-sgd", daemon=False)
        self._child_end = child
        self.inbox: queue.Queue[Any] = queue.Queue()
        self.sent = self.received = 0
        self.planned: OrderedDict[int, dict[str, Any]] = OrderedDict()
        self.started: int | None = None
        self.staged: dict[int, dict[str, Any]] = {}
        self.send_lock = threading.RLock()
        self.step_walls: deque[float] = deque(maxlen=200)
        self.last_update: int | None = None
        self.last_kind: str | None = None
        self.failure: SgdProcessFailed | None = None
        self.exit: dict[str, Any] | None = None
        self.reader: threading.Thread | None = None
        # A stopped/hung child stops draining its socket; a blocking send on the
        # controller thread would never reach the stall fence. One sender thread
        # owns every write; the controller only enqueues and checks its age.
        self.outbox: queue.Queue[Any] = queue.Queue()
        self.sending_since: float | None = None
        self.send_error: BaseException | None = None
        self.sender: threading.Thread | None = None
        self._closed = False

    @property
    def pid(self) -> int | None:
        return self.process.pid

    def start(self, init: dict[str, Any]) -> dict[str, Any]:
        self.process.start()
        # Last resort for any controller exit path that skips close(): the child
        # ignores SIGTERM and is non-daemon, so interpreter exit would wait on it.
        atexit.register(self.kill)
        self._child_end.close()
        self.reader = threading.Thread(target=self._read, name="rl-sgd-reader", daemon=True)
        self.reader.start()
        self.sender = threading.Thread(target=self._write, name="rl-sgd-sender", daemon=True)
        self.sender.start()
        self.send("init", **init)
        return self.expect("ready", timeout=self.options.start_seconds)

    def _read(self) -> None:
        while True:
            try:
                message = self.connection.recv()
            except (EOFError, OSError):
                self.inbox.put(None)
                return
            message["_received_at"] = time.time()
            self.inbox.put(message)

    def _write(self) -> None:
        while True:
            message = self.outbox.get()
            if message is None:
                return
            self.sending_since = time.monotonic()
            try:
                self.connection.send(message)
            except (BrokenPipeError, EOFError, OSError) as error:
                self.send_error = error
                return
            finally:
                self.sending_since = None

    def _check_sender(self, kind: str | None) -> None:
        if self.send_error is not None:
            raise self._fail("sgd_process_exited", waited_for=kind,
                             send_error=type(self.send_error).__name__)
        since = self.sending_since
        if since is not None and time.monotonic() - since > self.step_timeout():
            raise self._fail("sgd_process_stalled", waited_for=kind, blocked_send=True,
                             waited_seconds=time.monotonic() - since,
                             timeout_seconds=self.step_timeout())

    def send(self, kind: str, **fields: Any) -> None:
        # A plan may be sent from a preparation-completion thread; sequence
        # numbers and the planned/started ledger stay atomic per message.
        with self.send_lock:
            if self.failure is not None:
                raise self.failure
            self._check_sender(kind)
            if kind == "start" and (not self.planned
                                    or next(iter(self.planned)) != fields["dataset"]
                                    or fields["dataset"] not in self.staged):
                # The controller waits for `staged` before it authorizes a start.
                raise self.violation("sgd_start_without_staged_plan",
                                     dataset=fields["dataset"])
            self.sent += 1
            self.outbox.put({"protocol": PROTOCOL, "kind": kind, "sequence": self.sent,
                             "run": self.run, "training_generation": self.generation,
                             "sent_at": time.time(), **fields})
            if kind == "plan":
                self.planned[fields["dataset"]] = fields
            elif kind == "start":
                self.planned.popitem(last=False)
                del self.staged[fields["dataset"]]
                self.started = fields["dataset"]

    def step_timeout(self) -> float:
        if len(self.step_walls) < 5:
            return self.options.stall_seconds
        ordered = sorted(self.step_walls)
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        return max(self.options.stall_seconds, 10 * p95)

    def _facts(self, **extra: Any) -> dict[str, Any]:
        exitcode = self.process.exitcode
        return {"pid": self.process.pid, "exitcode": exitcode,
                "signal": (signal.Signals(-exitcode).name
                           if isinstance(exitcode, int) and exitcode < 0 else None),
                "last_update": self.last_update, "last_message": self.last_kind,
                "messages_received": self.received, "messages_sent": self.sent, **extra}

    def _fail(self, code: str, **extra: Any) -> SgdProcessFailed:
        if self.failure is None:
            if self.process.is_alive():
                self.kill()
            self.process.join(5)
            self.failure = SgdProcessFailed(code, self._facts(**extra))
        return self.failure

    def violation(self, code: str, **extra: Any) -> SgdProcessFailed:
        return self._fail(code, protocol_violation=True, **extra)

    def _get(self, deadline: float, kind: str, timeout: float) -> dict[str, Any]:
        began = time.monotonic()
        while True:
            remaining = deadline - time.monotonic()
            try:
                message = self.inbox.get(timeout=max(0.0, min(0.25, remaining)))
            except queue.Empty:
                self._check_sender(kind)
                if not self.process.is_alive() and self.inbox.empty():
                    self.process.join(1)
                    raise self._fail("sgd_process_exited", waited_for=kind) from None
                if remaining <= 0:
                    raise self._fail("sgd_process_stalled", waited_for=kind,
                                     waited_seconds=time.monotonic() - began,
                                     timeout_seconds=timeout) from None
                continue
            if message is None:
                self.process.join(5)
                raise self._fail("sgd_process_exited", waited_for=kind)
            self.received += 1
            if (message.get("protocol") != PROTOCOL or message.get("run") != self.run
                    or message.get("training_generation") != self.generation
                    or message.get("sequence") != self.received):
                raise self.violation("sgd_message_identity_or_sequence_changed",
                                     waited_for=kind, received_sequence=message.get("sequence"))
            self.last_kind = message["kind"]
            if message["kind"] == "failed":
                self.process.join(10)
                raise self._fail("sgd_process_failed", waited_for=kind,
                                 child={k: v for k, v in message.items()
                                        if k not in {"protocol", "run", "training_generation"}})
            return dict(message)

    def expect(self, kind: str, *, timeout: float, **binding: Any) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            message = self._get(deadline, kind, timeout)
            if message["kind"] == "staged" and kind != "staged":
                self._staged(message)
                continue
            if message["kind"] != kind:
                raise self.violation("sgd_unexpected_message", expected=kind,
                                     received=message["kind"])
            for key, value in binding.items():
                if message.get(key) != value:
                    raise self.violation("sgd_message_binding_changed", expected=kind,
                                         field=key, value=message.get(key), required=value)
            if kind == "step":
                self.step_walls.append(message["timing"]["step_wall_seconds"])
                self.last_update = message["update"]
            return message

    def _staged(self, message: dict[str, Any]) -> None:
        dataset = message["dataset"]
        plan = self.planned.get(dataset)
        if plan is None or dataset in self.staged or message.get("sha256") != plan["sha256"]:
            raise self.violation("sgd_staged_without_matching_plan", dataset=dataset)
        self.staged[dataset] = message
        self.on_staged(message)

    def wait_staged(self, dataset: int) -> dict[str, Any]:
        if dataset not in self.staged:
            deadline = time.monotonic() + self.options.stall_seconds
            while dataset not in self.staged:
                message = self._get(deadline, "staged", self.options.stall_seconds)
                if message["kind"] != "staged":
                    raise self.violation("sgd_unexpected_message", expected="staged",
                                         received=message["kind"])
                self._staged(message)
        return self.staged[dataset]

    def check(self) -> None:
        """Non-blocking liveness check between controller iterations."""
        if self.failure is not None:
            raise self.failure
        self._check_sender(None)
        if not self.process.is_alive():
            child = None
            while True:
                try:
                    message = self.inbox.get_nowait()
                except queue.Empty:
                    break
                if message is not None and message.get("kind") == "failed":
                    child = {k: v for k, v in message.items()
                             if k not in {"protocol", "run", "training_generation"}}
            raise self._fail("sgd_process_failed" if child else "sgd_process_exited",
                             waited_for=None, child=child)

    def kill(self) -> None:
        if self.process.pid is not None and self.process.is_alive():
            with suppress(ProcessLookupError):
                os.kill(self.process.pid, signal.SIGKILL)
            self.process.join(10)

    def close(self) -> dict[str, Any]:
        """Normal shutdown: release GPU and exit 0; a timeout kills and is recorded."""
        if self._closed:
            return self.exit or {}
        self._closed = True
        killed = False
        if self.process.pid is not None and self.process.is_alive() and self.failure is None:
            self.sent += 1
            self.outbox.put({"protocol": PROTOCOL, "kind": "shutdown", "sequence": self.sent,
                             "run": self.run, "training_generation": self.generation,
                             "sent_at": time.time()})
            self.process.join(self.options.shutdown_seconds)
        if self.process.pid is not None and self.process.is_alive():
            killed = True
            self.kill()
        self.outbox.put(None)
        with suppress(OSError):
            self.connection.close()
        for thread in (self.sender, self.reader):
            if thread is not None:
                thread.join(5)
        atexit.unregister(self.kill)
        self.exit = self._facts(shutdown_killed=killed,
                                failure=self.failure.facts if self.failure else None)
        return self.exit


# --- SGD child -------------------------------------------------------------


class _Shutdown(Exception):
    pass


class _ChildViolation(BoundaryError):
    """A fatal protocol/binding fact found by the SGD process; its code is reported."""

    def __init__(self, code: str) -> None:
        super().__init__("rl_sgd_process", code)


def _parent_death_signal(parent: int) -> None:
    if sys.platform == "linux":
        with suppress(OSError, AttributeError):
            ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
    if os.getppid() != parent:
        os._exit(70)


def _child_main(connection: Any, parent: int) -> None:
    # Slurm/terminal signals belong to the controller's original Stop path.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    _parent_death_signal(parent)
    inbox: queue.Queue[Any] = queue.Queue()
    state: dict[str, Any] = {"sent": 0, "received": 0, "run": None, "generation": None,
                             "stage": "init"}
    send_lock = threading.Lock()
    followers: list[_Follower] = []

    def send(kind: str, **fields: Any) -> None:
        with send_lock:
            state["sent"] += 1
            connection.send({"protocol": PROTOCOL, "kind": kind, "sequence": state["sent"],
                             "run": state["run"], "training_generation": state["generation"],
                             "sent_at": time.time(), **fields})

    def receive() -> None:
        while True:
            try:
                message = connection.recv()
            except (EOFError, OSError):
                if state.get("closing"):
                    return
                # Controller died or closed its end: exit at once, write nothing.
                os._exit(70)
            # Data parallel: every follower sees the controller's messages in
            # order; rank 0 alone validates and answers them.
            for follower in followers:
                follower.forward(message)
            # This one thread reads the pipe in order, so the gapless-sequence
            # check is the same here as on the stepping thread.
            try:
                _child_validate(message, state)
            except BaseException as error:
                inbox.put(_Raise(error, "receive"))
                continue
            _child_route(message, state, inbox)

    state["wake"] = inbox.put
    worker = None
    parallel = None
    try:
        message = connection.recv()
        parallel_init = message.get("data_parallel") if isinstance(message, dict) else None
        if parallel_init is not None:
            parallel = _DataParallel.leader(message, send, state, followers)
        _child_validate(message, state)
        inbox.put(message)
        threading.Thread(target=receive, name="rl-sgd-receiver", daemon=True).start()
        message = _child_next(inbox, state, block=True)
        if message["kind"] != "init":
            raise _ChildViolation("sgd_init_required_first")
        worker = _SgdWorker(message, send, state, parallel=parallel)
        worker.loop(lambda block: _child_next(inbox, state, block=block))
    except _Shutdown:
        pass
    except BaseException as error:
        from spireagent.local_diagnostics import exception_facts

        facts: dict[str, Any] = {"stage": state["stage"], "error_type": type(error).__name__,
                                 "error_code": getattr(error, "code", None),
                                 "diagnostic": exception_facts(error)}
        if worker is not None:
            facts.update(updates=worker.updates, datasets=worker.datasets)
            try:
                facts["memory"] = worker.memory(allocator=True)
            except Exception as memory_error:  # a broken CUDA context still reports the failure
                facts["memory_error"] = type(memory_error).__name__
        with suppress(BaseException):
            send("failed", **facts)
        state["closing"] = True
        for follower in followers:
            follower.kill()
        os._exit(1)
    if worker is not None:
        worker.close()
    state["closing"] = True
    if parallel is not None:
        parallel.close(followers)


class _Follower:
    """Rank-0 side of one follower rank: forwarded messages, failure watch."""

    def __init__(self, context: Any, init: dict[str, Any], rank: int, send: Any,
                 state: dict[str, Any], on_staged: Any) -> None:
        self.rank, self.send, self.state, self.on_staged = rank, send, state, on_staged
        self.connection, child = context.Pipe(duplex=True)
        self.process = context.Process(target=_follower_main,
                                       args=(child, init, rank, os.getpid()),
                                       name=f"rl-sgd-rank{rank}", daemon=False)
        self.process.start()
        child.close()
        self.lock = threading.Lock()
        threading.Thread(target=self._watch, name=f"rl-sgd-rank{rank}-watch",
                         daemon=True).start()

    def forward(self, message: Any) -> None:
        with self.lock, suppress(OSError, ValueError):
            self.connection.send(message)

    def _watch(self) -> None:
        while True:
            try:
                report = self.connection.recv()
            except (EOFError, OSError):
                report = None
            if isinstance(report, dict) and set(report) == {"staged", "file"}:
                self.on_staged(self.rank, report["staged"], tuple(report["file"]))
                continue
            break
        if self.state.get("closing"):
            return
        self.process.join(5)
        facts = report if isinstance(report, dict) else {}
        # A rank that failed or vanished would hang the next collective: fail now.
        with suppress(BaseException):
            self.send("failed", stage="data_parallel_rank", error_type=facts.get(
                "error_type", "DataParallelRankExited"), error_code=facts.get("error_code"),
                rank=self.rank, exitcode=self.process.exitcode,
                diagnostic=facts.get("diagnostic"))
        self.state["closing"] = True
        os._exit(1)

    def kill(self) -> None:
        with suppress(BaseException):
            self.process.kill()


class _DataParallel:
    """One process group over the SGD ranks; rank 0 is the controller's SGD child."""

    def __init__(self, init: dict[str, Any], rank: int) -> None:
        import datetime

        import torch
        import torch.distributed as dist

        config = init["data_parallel"]
        self.rank, self.world = rank, config["world"]
        self.backend = config["backend"]
        self.dist = dist
        # Rank 0 only: a "staged" answer waits until every follower has its own
        # copy, because the controller deletes the prepared file once staged.
        self.send: Any = None
        self.staged_lock = threading.Lock()
        self.deferred: dict[int, dict[str, Any]] = {}
        self.follower_ready: dict[int, dict[int, tuple[int, ...]]] = {}
        device = init["device"]
        if device.startswith("cuda"):
            base = int(device.split(":", 1)[1]) if ":" in device else 0
            self.device = f"cuda:{base + rank}"
            torch.cuda.set_device(self.device)
        else:
            if self.backend != "gloo":
                raise _ChildViolation("sgd_data_parallel_cpu_requires_gloo")
            self.device = device
        dist.init_process_group(
            self.backend, init_method="file://" + str(Path(init["root"]) / config["store"]),
            rank=rank, world_size=self.world,
            timeout=datetime.timedelta(seconds=config["timeout_seconds"]))

    @classmethod
    def leader(cls, init: dict[str, Any], send: Any, state: dict[str, Any],
               followers: list[_Follower]) -> _DataParallel:
        import uuid

        context = multiprocessing.get_context("spawn")
        config = init["data_parallel"]
        if (init.get("kind") != "init" or not isinstance(config, dict)
                or type(config.get("world")) is not int):
            raise _ChildViolation("sgd_data_parallel_init_changed")
        # One fresh rendezvous file per SGD child (node-local root, never reused).
        init["data_parallel"] = config = {**config, "store": "dp-store-" + uuid.uuid4().hex}
        holder: list[_DataParallel] = []
        for rank in range(1, config["world"]):
            followers.append(_Follower(context, init, rank, send, state,
                                       lambda r, d, f: holder[0].follower_staged(r, d, f)))
        leader = cls(init, 0)
        leader.send = send
        holder.append(leader)
        return leader

    def staged(self, dataset: int, fields: dict[str, Any]) -> None:
        with self.staged_lock:
            self.deferred[dataset] = fields
            self._flush(dataset)

    def follower_staged(self, rank: int, dataset: int, identity: tuple[int, ...]) -> None:
        with self.staged_lock:
            self.follower_ready.setdefault(dataset, {})[rank] = identity
            self._flush(dataset)

    def _flush(self, dataset: int) -> None:
        if (dataset in self.deferred
                and len(self.follower_ready.get(dataset, ())) == self.world - 1):
            fields = self.deferred.pop(dataset)
            identities = self.follower_ready.pop(dataset)
            verified = tuple(fields["file_identity"])
            if any(identity != verified for identity in identities.values()):
                # Only rank 0 hashed: a rank that mapped another file never trains.
                with suppress(BaseException):
                    self.send("failed", stage="data_parallel_stage",
                              error_type="_ChildViolation",
                              error_code="sgd_data_parallel_file_identity_changed",
                              dataset=dataset, verified=list(verified),
                              ranks={str(r): list(i) for r, i in identities.items()})
                os._exit(1)
            self.send("staged", dataset=dataset, **fields)

    def gather(self, value: Any) -> list[Any] | None:
        parts: list[Any] | None = [None] * self.world if self.rank == 0 else None
        self.dist.gather_object(value, parts, dst=0)
        return parts

    def close(self, followers: list[_Follower]) -> None:
        for follower in followers:
            follower.process.join(30)
            if follower.process.exitcode is None:
                follower.kill()
        with suppress(BaseException):
            self.dist.destroy_process_group()


def _follower_main(connection: Any, init: dict[str, Any], rank: int, leader: int) -> None:
    """A follower rank: same theta/Adam, its share of every minibatch, no answers."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    _parent_death_signal(leader)
    inbox: queue.Queue[Any] = queue.Queue()
    state: dict[str, Any] = {"stage": "init", "follower": True, "wake": inbox.put}
    # The stage thread answers `staged`; a failure report comes from this thread.
    answer_lock = threading.Lock()

    def receive() -> None:
        while True:
            try:
                message = connection.recv()
            except (EOFError, OSError):
                if state.get("closing"):
                    return
                os._exit(70)
            _child_route(message, state, inbox)

    def next_message(block: bool) -> Any:
        try:
            message = inbox.get(block=block)
        except queue.Empty:
            return None
        if isinstance(message, _Raise):
            message.throw(state)
        if message.get("kind") == "shutdown":
            raise _Shutdown
        return message

    worker = None
    parallel = None
    try:
        parallel = _DataParallel(init, rank)
        threading.Thread(target=receive, name="rl-sgd-follower-receiver", daemon=True).start()
        def answer(kind: str, **fields: Any) -> None:
            # Followers answer nothing to the controller; rank 0 waits for these.
            if kind == "staged":
                with answer_lock:
                    connection.send({"staged": fields["dataset"],
                                     "file": fields["file_identity"]})

        worker = _SgdWorker(init, answer, state, parallel=parallel)
        worker.loop(next_message)
    except _Shutdown:
        pass
    except BaseException as error:
        from spireagent.local_diagnostics import exception_facts

        with suppress(BaseException), answer_lock:
            connection.send({"error_type": type(error).__name__,
                             "error_code": getattr(error, "code", None),
                             "diagnostic": exception_facts(error)})
        state["closing"] = True
        os._exit(1)
    if worker is not None:
        worker.close()
    state["closing"] = True
    if parallel is not None:
        with suppress(BaseException):
            parallel.dist.destroy_process_group()


class _Raise:
    """A failure raised off the stepping thread, re-raised on it with its stage."""

    def __init__(self, error: BaseException, stage: str) -> None:
        self.error, self.stage = error, stage

    def throw(self, state: dict[str, Any]) -> None:
        state["stage"] = self.stage
        raise self.error


def _child_validate(message: Any, state: dict[str, Any]) -> None:
    """Gapless sequence and run binding; called by the one thread reading the pipe."""
    state["received"] += 1
    if (not isinstance(message, dict) or message.get("protocol") != PROTOCOL
            or message.get("sequence") != state["received"]
            or (state["run"] is not None and (message.get("run") != state["run"]
                or message.get("training_generation") != state["generation"]))):
        raise _ChildViolation("sgd_message_identity_or_sequence_changed")
    if state["run"] is None:
        state["run"], state["generation"] = message["run"], message["training_generation"]


def _child_route(message: Any, state: dict[str, Any], inbox: queue.Queue[Any]) -> None:
    """A plan goes straight to the stage thread when there is one: it then stages
    while the stepping thread is inside an update, instead of waiting for the next
    minibatch boundary to read it. Everything else keeps the ordered inbox."""
    worker = state.get("worker")
    if (isinstance(message, dict) and message.get("kind") == "plan" and worker is not None
            and worker.stage_executor is not None):
        try:
            worker.stage(message)
        except BaseException as error:
            inbox.put(_Raise(error, "plan"))
        return
    inbox.put(message)


def _child_next(inbox: queue.Queue[Any], state: dict[str, Any], *, block: bool) -> Any:
    try:
        message = inbox.get(block=block)
    except queue.Empty:
        return None
    if isinstance(message, _Raise):
        message.throw(state)
    if message["kind"] == "shutdown":
        raise _Shutdown
    return message


class _SgdWorker:
    def __init__(self, init: dict[str, Any], send: Any, state: dict[str, Any], *,
                 parallel: _DataParallel | None = None) -> None:
        import torch

        from stpd.p1.execution import MStdExecution
        from stpd.p1.model import MStd

        from .binary_preparation import _PinnedBuffers
        from .learner import _device
        from .loss import LossConfig
        from .performance import PerformanceConfig

        self.send, self.state = send, state
        self.torch = torch
        if init.get("schema") != INIT_SCHEMA:
            raise _ChildViolation("sgd_init_schema_changed")
        torch.set_num_threads(init["threads"])
        if init["cpus"] is not None:
            if not hasattr(os, "sched_setaffinity"):
                raise _ChildViolation("sgd_cpu_affinity_unsupported_on_this_platform")
            os.sched_setaffinity(0, set(init["cpus"]))
        torch.manual_seed(init["seed"])
        self.parallel = parallel
        self.rank = parallel.rank if parallel is not None else 0
        self.device = _device(parallel.device if parallel is not None else init["device"])
        values = read_tree(init["state"], INIT_SCHEMA)
        spec = init["model"]
        with torch.random.fork_rng(devices=[]):
            model = MStd(spec["vocabulary"], MStdExecution(**spec["execution"]),
                         model_size=spec["model_size"])
            model.enable_rl4_heads(0)
        if model.architecture != spec["architecture"]:
            raise _ChildViolation("sgd_model_construction_changed")
        model.load_state_dict(values["weights"], strict=True)
        if self.device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(self.device)
        self.model = model.to(self.device).eval().requires_grad_(True)
        self.optimizer = torch.optim.AdamW(
            [{"params": [p for name, p in self.model.named_parameters()
                         if not name.startswith("rl4_heads.")], "lr": init["lr"]},
             {"params": list(self.model.rl4_heads.parameters()), "lr": init["head_lr"]}],
            weight_decay=init["weight_decay"])
        if self.names() != tuple(tuple(group) for group in values["optimizer_parameter_names"]):
            raise _ChildViolation("sgd_optimizer_parameter_order_changed")
        self.optimizer.load_state_dict(values["optimizer"])
        self.ema = {name: value.to(self.device) for name, value in values["ema"].items()}
        self.updates, self.datasets = values["updates"], values["datasets"]
        torch.set_rng_state(values["torch_rng"])
        self.cuda_rng = tuple(values["cuda_rng"])
        if self.device.type == "cuda" and self.cuda_rng:
            torch.cuda.set_rng_state_all(list(self.cuda_rng))
        self.loss = LossConfig(**init["loss"])
        self.performance = PerformanceConfig(**init["performance"])
        self.compile_facts = (self.prepare_compile(init.get("compile"))
                              if self.performance.prepared_compile else None)
        self.target_kl, self.ema_decay = init["target_kl"], init["ema_decay"]
        self.diagnostics_every = init["diagnostics_every"]
        self.root = Path(init["root"])
        self.pinned = _PinnedBuffers(init["pinned_bytes"], slots=2)
        self.staged: OrderedDict[int, tuple[dict[str, Any], Any]] = OrderedDict()
        # Opt-in: plans staged on one side thread (FIFO, one worker), each result
        # delivered (`staged` entry + answer) by that thread as soon as it is done,
        # so plan order holds and no delivery waits for a minibatch boundary. The
        # receiver thread submits plans; the stepping thread pops starts. One lock.
        self.staging: OrderedDict[int, tuple[dict[str, Any], Any]] = OrderedDict()
        self.staged_lock = threading.Lock()
        self.wake = state.get("wake")
        self.next_start: dict[str, Any] | None = None
        self.stage_executor = None
        if init.get("stage_thread"):
            from concurrent.futures import ThreadPoolExecutor

            # Same CUDA device as this thread ("cuda" alone carries no index).
            index = (None if self.device.type != "cuda" else self.device.index
                     if self.device.index is not None else torch.cuda.current_device())
            self.stage_executor = ThreadPoolExecutor(
                1, thread_name_prefix="rl-sgd-stage",
                initializer=(lambda: torch.cuda.set_device(index))
                if index is not None else None)
        self.idle_since = time.perf_counter()
        self.last_step_end: float | None = None
        # Default off. Bounded by `limit`; never part of CP, uses or the step fence.
        self.offline_dump = init.get("offline_dump")
        self.offline_dumps = 0
        self.next_dump = (self.updates + self.offline_dump["every"]
                          if self.offline_dump is not None and self.rank == 0 else None)
        # Data parallel: stage only this rank's groups of each minibatch (plus the
        # shared row tensors). An offline-dump rank keeps the whole batch: the dump
        # writes every group of the minibatch it captures.
        self.shard = ((self.rank, parallel.world)
                      if parallel is not None and parallel.world > 1
                      and self.next_dump is None else None)
        loaded = weights_digest(self.model.state_dict())
        properties = (torch.cuda.get_device_properties(self.device)
                      if self.device.type == "cuda" else None)
        self.sync: Any = None
        self.replica_seconds: float | None = None
        ranks = None
        if parallel is not None:
            from .data_parallel import GradientAverage

            # Same update: each rank sums its partial gradient of the same minibatch.
            self.sync = GradientAverage(reduce="sum")
            ranks = parallel.gather({
                "rank": self.rank, "pid": os.getpid(), "device": str(self.device),
                "gpu_uuid": str(properties.uuid) if properties is not None else None,
                "gpu_name": properties.name if properties is not None else None,
                "weights_sha256": loaded})
            if ranks is not None and any(r["weights_sha256"] != loaded for r in ranks):
                raise _ChildViolation("sgd_data_parallel_ranks_loaded_different_weights")
        self.state["stage"] = "ready"
        # Before `ready`: the controller plans only after it, so every plan is
        # routed the same way (straight to the stage thread when there is one).
        self.state["worker"] = self
        self.send("ready", pid=os.getpid(), device=str(self.device),
                  **({"data_parallel": ranks} if ranks is not None else {}),
                  **({"compile": self.compile_facts} if self.compile_facts is not None else {}),
                  gpu_uuid=str(properties.uuid) if properties is not None else None,
                  gpu_name=properties.name if properties is not None else None,
                  weights_sha256=loaded, updates=self.updates, datasets=self.datasets,
                  optimizer_param_groups=self.groups(), cpus=init["cpus"],
                  cuda_initialized=bool(torch.cuda.is_initialized()), memory=self.memory())

    def prepare_compile(self, options: dict[str, Any] | None) -> dict[str, Any]:
        """F6 preflight, compile limits/cache, then optional warm-up (no learning effect).

        The module-loaded Python links `_bz2` against the EasyBuild bzip2; a GPU
        job without `module load python/...` cannot import it, and inductor's
        first compile then fails mid-run. Refuse here, before `ready`.
        """
        try:
            import bz2  # noqa: F401
        except ImportError as error:
            raise _ChildViolation("sgd_compile_requires_bz2_load_the_python_module") from error
        facts: dict[str, Any] = {"bz2": True}
        if options is None:
            return facts
        if options["cache_dir"] is not None:
            # Read lazily by inductor at each compile: persistent across jobs.
            os.environ["TORCHINDUCTOR_CACHE_DIR"] = options["cache_dir"]
            facts["cache_dir"] = options["cache_dir"]
        if options["recompile_limit"] is not None:
            import torch._dynamo.config as dynamo

            if hasattr(dynamo, "recompile_limit"):
                dynamo.recompile_limit = options["recompile_limit"]
            else:
                dynamo.cache_size_limit = options["recompile_limit"]
            facts["recompile_limit"] = options["recompile_limit"]
        if not options.get("size_guard", False):
            import torch._inductor.config as inductor

            # Inductor's mix-order-reduction fusion skips workloads under 5 Mi elements
            # with a guard on the symbolic size: every rows-v2 graph (node builder,
            # block) then compiles once more when the first group crosses 5 Mi / 192
            # nodes, mid-run, after any warm-up that saw one side only (Friday 1470331).
            # Fusing regardless removes the guard; equal step time on chain dumps
            # (1470333: 0.2377 vs 0.2371-0.2395 s, 3 graphs instead of 5).
            inductor.triton.mix_order_reduction_non_strict_mode = True
        facts["size_guard"] = bool(options.get("size_guard", False))
        facts["warmup"] = [self.warm(Path(path)) for path in options["warmup"]]
        return facts

    def warm(self, root: Path) -> dict[str, Any]:
        """Compile on one dumped real minibatch: forward + backward, never a step.

        `target_kl=-1` makes `prepared_update` take its KL-stop branch, which
        zeroes gradients without an optimizer step: theta/Adam stay bitwise the
        same, and the torch/CUDA RNG states are restored around it.
        """
        from . import batch_preparation as bp
        from .loss import prepared_update
        from .offline_step import load

        torch = self.torch
        began = time.perf_counter()
        _value, minibatch, _receipt = load(root)
        if self.performance.prepared_forward == "rows-v2":
            # Compile the shapes this run's preparation produces: a dump written with
            # another bucket partition (or padded-v1) is re-laid out from its own
            # CPU tensors exactly as `prepare_group` lays out a live group.
            minibatch = dataclasses.replace(minibatch, groups=tuple(
                dataclasses.replace(group, layout=bp.row_layout(
                    group, bucket_rows=self.performance.prepared_bucket_rows))
                for group in minibatch.groups))
        before = weights_digest(self.model.state_dict())
        staged = bp.stage_batch(minibatch, self.device)[0] if self.device.type == "cuda" else (
            minibatch)
        devices = [self.device] if self.device.type == "cuda" else []
        with torch.random.fork_rng(devices=devices):
            result = prepared_update(
                self.model, self.optimizer, staged, config=self.loss,
                performance=self.performance, target_kl=-1.0, diagnostics=False,
                collect_policy_rows=False)
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        if result["stepped"] or weights_digest(self.model.state_dict()) != before:
            raise _ChildViolation("sgd_compile_warmup_changed_theta")
        return {"path": str(root), "rows": result["rows"],
                "seconds": time.perf_counter() - began,
                "rows_forward": result.get("rows_forward")}

    def names(self) -> tuple[tuple[str, ...], ...]:
        names = {id(parameter): name for name, parameter in self.model.named_parameters()}
        return tuple(tuple(names[id(parameter)] for parameter in group["params"])
                     for group in self.optimizer.param_groups)

    def groups(self) -> list[dict[str, Any]]:
        return [{key: value for key, value in group.items() if key != "params"}
                for group in self.optimizer.param_groups]

    def memory(self, *, allocator: bool = False) -> dict[str, Any]:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        values: dict[str, Any] = {
            "pid": os.getpid(),
            "peak_rss_bytes": int(peak * (1 if sys.platform == "darwin" else 1024))}
        if self.device.type == "cuda":
            torch = self.torch
            values.update(cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(self.device),
                          cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved(self.device))
            if allocator:
                stats = torch.cuda.memory_stats(self.device)
                values["allocator"] = {key: stats.get(key) for key in (
                    "allocated_bytes.all.current", "reserved_bytes.all.current",
                    "num_alloc_retries", "num_ooms")}
        return values

    def loop(self, next_message: Any) -> None:
        while True:
            # Stage-thread results deliver themselves; a staging failure also
            # wakes the inbox, so idling never waits on a staging to finish.
            self.drain_staging(block=False)
            self.state["stage"] = "idle"
            # A start the controller authorized during the previous dataset
            # (`sgd_prestart`) runs now, back to back, in message order.
            message, self.next_start = (
                (self.next_start, None) if self.next_start is not None
                else (next_message(True), None))
            kind = message["kind"]
            if kind == "plan":
                self.stage(message)
            elif kind == "start":
                self.run_dataset(message, next_message)
            elif kind == "checkpoint":
                if self.rank == 0:  # followers hold the same theta; rank 0 answers
                    self.capture_state(message)
            else:
                raise _ChildViolation("sgd_unexpected_message_" + str(kind))

    def stage(self, plan: dict[str, Any]) -> None:
        """Map the original prepared-batch file, verify it, pin and H2D it."""
        dataset = plan["dataset"]
        with self.staged_lock:
            last = max([*self.staged, *self.staging], default=None)
            if last is not None and dataset <= last:
                raise _ChildViolation("sgd_plan_order_changed")
            if self.stage_executor is not None:
                self.staging[dataset] = (plan, self.stage_executor.submit(
                    self._stage_and_deliver, plan))
                return
        self.state["stage"] = "stage"
        self._staged_done(plan, *self._stage_work(plan))

    def _stage_and_deliver(self, plan: dict[str, Any]) -> None:
        """Stage thread: stage, then deliver at once; a failure fails the child."""
        try:
            self._staged_done(plan, *self._stage_work(plan))
        except BaseException as error:
            if self.wake is not None:
                self.wake(_Raise(error, "stage_wait"))
            raise

    def drain_staging(self, *, block: bool) -> None:
        """Reap finished side-thread stagings in plan order (re-raise a failure);
        block = wait for all of them. Delivery already happened on that thread."""
        while True:
            with self.staged_lock:
                if not self.staging:
                    return
                dataset, (_, future) = next(iter(self.staging.items()))
            if not block and not future.done():
                return
            self.state["stage"] = "stage_wait"
            future.result()  # a staging failure fails the child loudly
            with self.staged_lock:
                del self.staging[dataset]

    def _stage_work(self, plan: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        """Map the prepared file, pin/H2D this rank's part, verify the whole file once.

        Rank 0 (and a single SGD process) hashes the whole file on a helper thread
        while the pin/H2D runs; the digest must match before `staged` is sent, so
        no step ever starts on unverified bytes. A follower rank does not hash: it
        reports the fstat identity of the file it mapped, and rank 0 answers
        `staged` only when every rank mapped the file rank 0 verified.
        """
        from . import batch_preparation as bp

        began = time.perf_counter()
        dataset = plan["dataset"]
        direct = self.stage_executor is not None and self.device.type == "cuda"
        with open(plan["path"], "rb") as stream:
            identity = _file_identity(os.fstat(stream.fileno()))
            mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        digest: _Digest | None = None
        arrays: Any = None
        try:
            if len(mapping) < plan["used_bytes"]:
                raise _ChildViolation("sgd_prepared_file_integrity_changed")
            if self.rank == 0:
                digest = _Digest(memoryview(mapping)[:plan["used_bytes"]], VERIFY_THREADS)
            tree = pickle.loads(plan["tree"])
            arrays = mapped_arrays(mapping, plan["arrays"])
            if self.shard is not None:
                from .data_parallel import shard_transport

                tree, arrays = shard_transport(tree, arrays, *self.shard)
            if direct:
                # Stage thread: one tree walk (pin the file's slabs, H2D, map the
                # tree once); the walks hold the GIL the stepping thread needs.
                slabs, lease = self.pinned.pin_slabs(arrays, self.pinned.epoch)
                arrays = None
                pinned = time.perf_counter()
                device_batch, copies = bp.stage_pinned_slabs(tree, slabs, lease, self.device)
                del slabs, lease
            else:
                if self.device.type == "cuda":
                    batch = self.pinned.pin(bp.unpack_transport((tree, arrays)),
                                            self.pinned.epoch)
                else:
                    batch = bp.unpack_transport((tree, tuple(a.copy() for a in arrays)))
                arrays = None
                if (batch.request.identity.sequence != dataset
                        or len(batch.minibatches) != plan["minibatch_count"]):
                    raise _ChildViolation("sgd_prepared_batch_binding_changed")
                pinned = time.perf_counter()
                device_batch, copies = bp.stage_batch(batch, self.device)
                del batch  # pinned slot returns once its H2D event completes
            staged_at = time.perf_counter()
            if digest is not None:
                digest.join()
                if digest.error is not None:
                    raise digest.error
                if digest.value != plan["sha256"]:
                    raise _ChildViolation("sgd_prepared_file_integrity_changed")
        finally:
            if digest is not None:
                digest.join()  # the mapping outlives every read of it
            arrays = None
            if not direct:
                gc.collect()
            with suppress(BufferError):
                mapping.close()
        return self._staged_fields(plan, device_batch, copies, began, pinned, staged_at,
                                   digest, identity)

    def _staged_fields(self, plan: dict[str, Any], device_batch: Any, copies: int,
                       began: float, pinned: float, staged_at: float,
                       digest: _Digest | None,
                       identity: tuple[int, ...]) -> tuple[Any, dict[str, Any]]:
        if (device_batch.request.identity.sequence != plan["dataset"]
                or len(device_batch.minibatches) != plan["minibatch_count"]):
            raise _ChildViolation("sgd_prepared_batch_binding_changed")
        done = time.perf_counter()
        fields = dict(sha256=plan["sha256"], copies=copies,
                      verify_seconds=digest.seconds if digest is not None else 0.0,
                      verify_wait_seconds=done - staged_at,
                      pin_seconds=pinned - began, stage_seconds=staged_at - pinned,
                      host_seconds=done - began, file_identity=identity,
                      shard=list(self.shard) if self.shard is not None else None,
                      stage_thread=threading.current_thread().name)
        return device_batch, fields

    def _staged_done(self, plan: dict[str, Any], device_batch: Any,
                     fields: dict[str, Any]) -> None:
        dataset = plan["dataset"]
        with self.staged_lock:
            self.staged[dataset] = (plan, device_batch)
        if self.parallel is not None and self.rank == 0:
            self.parallel.staged(dataset, fields)
        else:
            self.send("staged", dataset=dataset, **fields)

    def run_dataset(self, start: dict[str, Any], next_message: Any) -> None:
        from .loss import prepared_update

        torch = self.torch
        dataset = start["dataset"]
        with self.staged_lock:
            if not self.staged or next(iter(self.staged)) != dataset:
                raise _ChildViolation("sgd_start_without_staged_plan")
            _, (plan, device_batch) = self.staged.popitem(last=False)
        epochs, start_updates = plan["epochs"], self.updates
        minibatch = epoch = 0
        waited = time.perf_counter() - self.idle_since
        boundary = (time.perf_counter() - self.last_step_end
                    if self.last_step_end is not None else None)
        cuda = self.device.type == "cuda"
        while True:
            inbox_began = time.perf_counter()
            while (message := next_message(False)) is not None:
                if message["kind"] == "start" and self.next_start is None:
                    # Prestart: at most one dataset beyond this one; it is run
                    # right after this one ends. Nothing else may follow it
                    # before then except plans.
                    self.next_start = message
                    continue
                if message["kind"] != "plan":
                    raise _ChildViolation("sgd_message_during_dataset_" + str(message["kind"]))
                self.stage(message)
            self.drain_staging(block=False)
            inbox = time.perf_counter() - inbox_began
            batch = device_batch.minibatches[minibatch]
            if batch.epoch_auxiliary:
                terminal_rows, hp_rows = batch.epoch_auxiliary_counts[epoch]
                batch = replace(batch, terminal_rows=terminal_rows, hp_rows=hp_rows,
                                tensors={**batch.tensors, **batch.epoch_auxiliary[epoch]})
            diagnostics = self.updates % self.diagnostics_every == 0
            dumped = None
            if (self.next_dump is not None and self.updates >= self.next_dump
                    and not diagnostics):
                # Before the attempt and outside its timing: pre-step theta/Adam.
                self.state["stage"] = "offline_dump"
                dumped = self.dump_offline_step(batch, device_batch, dataset, minibatch, epoch)
            self.state["stage"] = "update"
            step_began = time.perf_counter()
            observed_at = time.time()
            if cuda:
                events = (torch.cuda.Event(enable_timing=True),
                          torch.cuda.Event(enable_timing=True))
                events[0].record()
            result = prepared_update(
                self.model, self.optimizer, batch, config=self.loss,
                performance=self.performance, target_kl=self.target_kl,
                diagnostics=diagnostics, collect_policy_rows=True,
                gradient_sync=self.sync,
                shard=(self.rank, self.parallel.world) if self.parallel is not None else None)
            optimizer_seconds = time.perf_counter() - step_began
            if self.parallel is not None:
                result = self.gather_step(result, diagnostics)
            ema_seconds = 0.0
            if result["stepped"]:
                self.state["stage"] = "ema"
                ema_began = time.perf_counter()
                if self.rank == 0:  # EMA and every answer live on rank 0 only
                    self.update_ema()
                self.updates += 1
                ema_seconds = time.perf_counter() - ema_began
            cuda_seconds = None
            if cuda:
                events[1].record()
                events[1].synchronize()
                cuda_seconds = events[0].elapsed_time(events[1]) / 1000
            end = time.perf_counter()
            self.last_step_end = end
            groups = self.optimizer.param_groups
            self.send("step", dataset=dataset, minibatch=minibatch, epoch=epoch,
                      update=self.updates, stepped=result["stepped"],
                      kl_stopped=result["kl_stopped"], result=result, observed_at=observed_at,
                      lr=groups[0]["lr"], head_lr=groups[1]["lr"], memory=self.memory(),
                      timing={"optimizer_seconds": optimizer_seconds,
                              "ema_seconds": ema_seconds, "cuda_event_seconds": cuda_seconds,
                              "step_wall_seconds": end - step_began, "inbox_seconds": inbox,
                              "wait_for_start_seconds": waited if minibatch == epoch == 0
                              else 0.0,
                              "boundary_seconds": boundary if minibatch == epoch == 0
                              else None,
                              **({"replica_check_seconds": self.replica_seconds}
                                 if minibatch == epoch == 0
                                 and self.replica_seconds is not None else {}),
                              **({"offline_dump": dumped} if dumped is not None else {})})
            minibatch += 1
            end_epoch = minibatch == len(device_batch.minibatches)
            finished = result["kl_stopped"] or (end_epoch and epoch + 1 >= epochs)
            if end_epoch and not finished:
                epoch, minibatch = epoch + 1, 0
            if finished:
                break
        del device_batch, batch
        self.datasets += 1
        if self.parallel is not None:
            from .data_parallel import replica_check

            # Once per dataset: theta must still be bitwise identical on every rank.
            self.state["stage"] = "replica_check"
            check_began = time.perf_counter()
            replica_check(self.model)
            self.replica_seconds = time.perf_counter() - check_began
        if self.updates > start_updates and self.rank == 0:
            self.snapshot()
        self.idle_since = time.perf_counter()

    def gather_step(self, result: dict[str, Any], diagnostics: bool) -> dict[str, Any]:
        """Rank 0 gets every rank's per-row policy facts (in group order) and timing."""
        assert self.parallel is not None
        began = time.perf_counter()
        rows = result.pop("_policy_rows", [])
        local = {**result["data_parallel"], "seconds": result["seconds"],
                 "host_timing": result["host_timing"]}
        parts = self.parallel.gather((rows, local))
        if parts is None:
            return result
        result["_policy_rows"] = [row for part_rows, _ in parts for row in part_rows]
        result["data_parallel"] = {
            "world": self.parallel.world, "mode": "split_rows",
            "ranks": [facts for _, facts in parts],
            "gather_seconds": time.perf_counter() - began,
            # Per-head trunk diagnostics are computed from rank 0's groups only.
            **({"diagnostics_scope": "rank0_groups"} if diagnostics else {})}
        return result

    def dump_offline_step(self, batch: Any, device_batch: Any, dataset: int, minibatch: int,
                          epoch: int) -> dict[str, Any]:
        """Write one `p3-offline-step-input-1` file of the attempt about to run.

        A diagnostic, not learning state: a failed write is reported on this step
        and ends further dumps; it never fences training or changes the attempt.
        """
        from . import offline_step
        from .checkpoint_state import cpu_copy

        assert self.offline_dump is not None
        began = time.perf_counter()
        update = self.updates + 1
        facts: dict[str, Any] = {"update": update, "dataset": dataset, "minibatch": minibatch,
                                 "epoch": epoch, "index": self.offline_dumps}
        try:
            state = {
                "schema": offline_step.SCHEMA,
                "construction": self.offline_dump["construction"],
                "sgd_execution": self.offline_dump["execution"],
                "weights": cpu_copy(self.model.state_dict()),
                "optimizer": cpu_copy(self.optimizer.state_dict()),
                "minibatch": offline_step.cpu_tree(batch),
                "loss": dataclasses.asdict(self.loss),
                "performance": dataclasses.asdict(self.performance),
                "target_kl": self.target_kl, "diagnostics": False,
                "online_update": update, "dataset": dataset,
                "minibatch_index": minibatch, "epoch": epoch}
            captured = time.perf_counter()
            receipt = {
                **facts, **offline_step.minibatch_facts(batch), "online_update": update,
                "run": self.state["run"], "training_generation": self.state["generation"],
                "dataset_rows": len(device_batch.metadata),
                "dataset_payload_bytes": device_batch.payload_bytes,
                "dataset_bytes_per_ppo_row": (device_batch.payload_bytes
                                              / len(device_batch.metadata)
                                              if device_batch.metadata else None),
                "device": str(self.device), "at": time.time(),
                "capture_seconds": captured - began}
            directory = Path(self.offline_dump["directory"]) / (
                f"u{update:09d}-d{dataset:07d}-m{minibatch}-e{epoch}")
            facts.update(offline_step.write(directory, state, receipt),
                         capture_seconds=captured - began,
                         write_seconds=time.perf_counter() - captured)
            self.offline_dumps += 1
            self.next_dump = (None if self.offline_dumps >= self.offline_dump["limit"]
                              else self.updates + self.offline_dump["every"])
        except Exception as error:  # diagnostics only; reported, then no further dumps
            facts["error_type"] = type(error).__name__
            facts["error_code"] = getattr(error, "code", None)
            self.next_dump = None
        facts["seconds"] = time.perf_counter() - began
        return facts

    def update_ema(self) -> None:
        torch = self.torch
        decay = self.ema_decay
        with torch.no_grad():
            weights = self.model.state_dict()
            floating = [name for name, value in weights.items() if value.is_floating_point()]
            torch._foreach_mul_([self.ema[name] for name in floating], decay)
            torch._foreach_add_([self.ema[name] for name in floating],
                                [weights[name] for name in floating], alpha=1 - decay)
            for name, value in weights.items():
                if not value.is_floating_point():
                    self.ema[name].copy_(value)

    def snapshot(self) -> None:
        from .checkpoint_state import snapshot_weights

        self.state["stage"] = "snapshot"
        began = time.perf_counter()
        weights = snapshot_weights(self.model.state_dict())
        captured = time.perf_counter()
        file = write_tree(self.root / f"mu-{self.updates:09d}.tree",
                          {"schema": WEIGHTS_SCHEMA, "updates": self.updates, "weights": weights})
        self.send("snapshot", updates=self.updates, datasets=self.datasets, file=file,
                  capture_seconds=captured - began, write_seconds=time.perf_counter() - captured)

    def capture_state(self, request: dict[str, Any]) -> None:
        from .checkpoint_state import cpu_copy, snapshot_weights

        torch = self.torch
        self.state["stage"] = "state"
        if request["updates"] != self.updates or request["datasets"] != self.datasets:
            raise _ChildViolation("sgd_checkpoint_counters_changed")
        began = time.perf_counter()
        weights = snapshot_weights(self.model.state_dict())
        optimizer, ema = cpu_copy(self.optimizer.state_dict()), snapshot_weights(self.ema)
        cuda = (tuple(torch.cuda.get_rng_state_all()) if self.device.type == "cuda"
                else self.cuda_rng)
        value = {"schema": STATE_SCHEMA, "updates": self.updates, "datasets": self.datasets,
                 "weights": weights, "optimizer": optimizer,
                 "optimizer_parameter_names": self.names(), "ema": ema,
                 "torch_rng": torch.get_rng_state().clone(),
                 "cuda_rng": tuple(item.cpu().clone() for item in cuda)}
        captured = time.perf_counter()
        file = write_tree(self.root / f"state-{self.updates:09d}-{self.datasets:07d}.tree", value)
        self.send("state", updates=self.updates, datasets=self.datasets, file=file,
                  optimizer_param_groups=self.groups(), capture_seconds=captured - began,
                  write_seconds=time.perf_counter() - captured, memory=self.memory())

    def close(self) -> None:
        self.state["stage"] = "shutdown"
        self.state.pop("worker", None)  # later plans stay in the inbox, unread
        if self.stage_executor is not None:
            self.stage_executor.shutdown(wait=True, cancel_futures=True)
            self.staging.clear()
        self.staged.clear()
        self.pinned.close()
        if self.device.type == "cuda":
            self.torch.cuda.synchronize(self.device)
