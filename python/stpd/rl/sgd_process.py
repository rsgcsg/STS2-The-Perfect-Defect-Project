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

PROTOCOL = "stpd/rl-sgd-process-1"
INIT_SCHEMA = "stpd/rl-sgd-init-1"
WEIGHTS_SCHEMA = "stpd/rl-sgd-weights-1"
STATE_SCHEMA = "stpd/rl-sgd-state-1"
GiB = 1024**3
OPTION_KEYS = frozenset((
    "sgd_process", "sgd_stall_seconds", "sgd_start_seconds", "sgd_shutdown_seconds",
    "sgd_cpus", "sgd_offline_step_seconds", "memory_limit_bytes",
    "memory_headroom_fraction", "memory_budget",
))
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
    return SgdOptions(
        stall_seconds=number("sgd_stall_seconds", 120, 1, 3600),
        start_seconds=number("sgd_start_seconds", 600, 1, 3600),
        shutdown_seconds=number("sgd_shutdown_seconds", 60, 1, 600),
        cpus=tuple(cpus) if cpus is not None else None,
        offline_step_seconds=float(offline) if offline is not None else None,
        memory_limit_bytes=limit,
        headroom=number("memory_headroom_fraction", 0.20, 0.0, 0.9),
        declared=dict(declared),
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
        if self.failure is not None:
            raise self.failure
        self._check_sender(kind)
        self.sent += 1
        self.outbox.put({"protocol": PROTOCOL, "kind": kind, "sequence": self.sent,
                         "run": self.run, "training_generation": self.generation,
                         "sent_at": time.time(), **fields})
        if kind == "plan":
            self.planned[fields["dataset"]] = fields
        elif kind == "start":
            # The controller waits for `staged` before it authorizes a start.
            if (not self.planned or next(iter(self.planned)) != fields["dataset"]
                    or fields["dataset"] not in self.staged):
                raise self.violation("sgd_start_without_staged_plan", dataset=fields["dataset"])
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
            inbox.put(message)

    threading.Thread(target=receive, name="rl-sgd-receiver", daemon=True).start()
    worker = None
    try:
        message = _child_next(inbox, state, block=True)
        if message["kind"] != "init":
            raise _ChildViolation("sgd_init_required_first")
        worker = _SgdWorker(message, send, state)
        worker.loop(lambda block: _child_next(inbox, state, block=block))
    except _Shutdown:
        pass
    except BaseException as error:
        from spireagent.local_diagnostics import exception_facts

        facts: dict[str, Any] = {"stage": state["stage"], "error_type": type(error).__name__,
                                 "error_code": getattr(error, "code", None),
                                 "diagnostic": exception_facts(error)}
        if worker is not None:
            facts.update(updates=worker.updates, datasets=worker.datasets,
                         memory=worker.memory(allocator=True))
        with suppress(BaseException):
            send("failed", **facts)
        state["closing"] = True
        os._exit(1)
    if worker is not None:
        worker.close()
    state["closing"] = True


def _child_next(inbox: queue.Queue[Any], state: dict[str, Any], *, block: bool) -> Any:
    try:
        message = inbox.get(block=block)
    except queue.Empty:
        return None
    state["received"] += 1
    if (message.get("protocol") != PROTOCOL or message.get("sequence") != state["received"]
            or (state["run"] is not None and (message.get("run") != state["run"]
                or message.get("training_generation") != state["generation"]))):
        raise _ChildViolation("sgd_message_identity_or_sequence_changed")
    if state["run"] is None:
        state["run"], state["generation"] = message["run"], message["training_generation"]
    if message["kind"] == "shutdown":
        raise _Shutdown
    return message


class _SgdWorker:
    def __init__(self, init: dict[str, Any], send: Any, state: dict[str, Any]) -> None:
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
        self.device = _device(init["device"])
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
        self.target_kl, self.ema_decay = init["target_kl"], init["ema_decay"]
        self.diagnostics_every = init["diagnostics_every"]
        self.root = Path(init["root"])
        self.pinned = _PinnedBuffers(init["pinned_bytes"], slots=2)
        self.staged: OrderedDict[int, tuple[dict[str, Any], Any]] = OrderedDict()
        self.idle_since = time.perf_counter()
        self.last_step_end: float | None = None
        loaded = weights_digest(self.model.state_dict())
        self.state["stage"] = "ready"
        properties = (torch.cuda.get_device_properties(self.device)
                      if self.device.type == "cuda" else None)
        self.send("ready", pid=os.getpid(), device=str(self.device),
                  gpu_uuid=str(properties.uuid) if properties is not None else None,
                  gpu_name=properties.name if properties is not None else None,
                  weights_sha256=loaded, updates=self.updates, datasets=self.datasets,
                  optimizer_param_groups=self.groups(), cpus=init["cpus"],
                  cuda_initialized=bool(torch.cuda.is_initialized()), memory=self.memory())

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
            self.state["stage"] = "idle"
            message = next_message(True)
            kind = message["kind"]
            if kind == "plan":
                self.stage(message)
            elif kind == "start":
                self.run_dataset(message, next_message)
            elif kind == "checkpoint":
                self.capture_state(message)
            else:
                raise _ChildViolation("sgd_unexpected_message_" + str(kind))

    def stage(self, plan: dict[str, Any]) -> None:
        """Map the original prepared-batch file, verify it, pin and H2D it."""
        from . import batch_preparation as bp

        self.state["stage"] = "stage"
        began = time.perf_counter()
        dataset = plan["dataset"]
        if dataset in self.staged or (self.staged and dataset <= next(reversed(self.staged))):
            raise _ChildViolation("sgd_plan_order_changed")
        with open(plan["path"], "rb") as stream:
            mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            if (len(mapping) < plan["used_bytes"] or hashlib.sha256(
                    memoryview(mapping)[:plan["used_bytes"]]).hexdigest() != plan["sha256"]):
                raise _ChildViolation("sgd_prepared_file_integrity_changed")
            verified = time.perf_counter()

            def load() -> Any:
                arrays = mapped_arrays(mapping, plan["arrays"])
                if self.device.type == "cuda":
                    batch = bp.unpack_transport((pickle.loads(plan["tree"]), arrays))
                    return self.pinned.pin(batch, self.pinned.epoch)
                return bp.unpack_transport((pickle.loads(plan["tree"]),
                                            tuple(array.copy() for array in arrays)))

            batch = load()
        finally:
            gc.collect()
            with suppress(BufferError):
                mapping.close()
        if batch.request.identity.sequence != dataset or len(batch.minibatches) != plan[
                "minibatch_count"]:
            raise _ChildViolation("sgd_prepared_batch_binding_changed")
        pinned = time.perf_counter()
        device_batch, copies = bp.stage_batch(batch, self.device)
        del batch  # pinned slot returns once its H2D event completes
        self.staged[dataset] = (plan, device_batch)
        done = time.perf_counter()
        self.send("staged", dataset=dataset, sha256=plan["sha256"], copies=copies,
                  verify_seconds=verified - began, pin_seconds=pinned - verified,
                  stage_seconds=done - pinned, host_seconds=done - began)

    def run_dataset(self, start: dict[str, Any], next_message: Any) -> None:
        from .loss import prepared_update

        torch = self.torch
        dataset = start["dataset"]
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
                if message["kind"] != "plan":
                    raise _ChildViolation("sgd_message_during_dataset_" + str(message["kind"]))
                self.stage(message)
            inbox = time.perf_counter() - inbox_began
            self.state["stage"] = "update"
            step_began = time.perf_counter()
            observed_at = time.time()
            batch = device_batch.minibatches[minibatch]
            if batch.epoch_auxiliary:
                terminal_rows, hp_rows = batch.epoch_auxiliary_counts[epoch]
                batch = replace(batch, terminal_rows=terminal_rows, hp_rows=hp_rows,
                                tensors={**batch.tensors, **batch.epoch_auxiliary[epoch]})
            if cuda:
                events = (torch.cuda.Event(enable_timing=True),
                          torch.cuda.Event(enable_timing=True))
                events[0].record()
            result = prepared_update(
                self.model, self.optimizer, batch, config=self.loss,
                performance=self.performance, target_kl=self.target_kl,
                diagnostics=self.updates % self.diagnostics_every == 0,
                collect_policy_rows=True)
            optimizer_seconds = time.perf_counter() - step_began
            ema_seconds = 0.0
            if result["stepped"]:
                self.state["stage"] = "ema"
                ema_began = time.perf_counter()
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
                              else None})
            minibatch += 1
            end_epoch = minibatch == len(device_batch.minibatches)
            finished = result["kl_stopped"] or (end_epoch and epoch + 1 >= epochs)
            if end_epoch and not finished:
                epoch, minibatch = epoch + 1, 0
            if finished:
                break
        del device_batch, batch
        self.datasets += 1
        if self.updates > start_updates:
            self.snapshot()
        self.idle_since = time.perf_counter()

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
        self.staged.clear()
        self.pinned.close()
        if self.device.type == "cuda":
            self.torch.cuda.synchronize(self.device)
