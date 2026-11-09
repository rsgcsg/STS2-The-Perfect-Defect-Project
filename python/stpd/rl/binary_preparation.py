"""Bounded node-local byte arenas -> spawn assembly -> pinned current/next.

Only immutable packet bytes cross the provider boundary. Workers receive arena
name/offset/length/epoch descriptors, never a provider, socket or borrowed view.
No planner/RNG, queue, auxiliary uses or model state lives here.
"""

from __future__ import annotations

import copy
import dataclasses as dc
import gc
import hashlib
import mmap
import multiprocessing as mp
import os
import pickle
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import CancelledError, Future, ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import numpy as np
import torch

from spireagent.json_boundary import BoundaryError
from spireagent.rl_dataplane_ports import (
    MAX_PACKET_BYTES,
    TENSOR_SEGMENT_FORMAT,
    BinaryRef,
    BinarySourceProvider,
)
from stpd.p1.frame_compiler import FrameCompiler

from . import batch_preparation as bp
from .fast_segment import FORMAT as FAST_FORMAT
from .fast_source import decode_fast_packet
from .prepared_batch import AuxiliaryInput, PreparedBatch, PrepareRequest
from .tensor_segment import auxiliary_frame, decode_packet, packet_header

_worker_compiler: FrameCompiler | None = None


def _initialize(vocabulary: dict[str, Any], threads: int) -> None:
    global _worker_compiler
    torch.set_num_threads(threads)
    if torch.get_num_interop_threads() != 1:
        torch.set_num_interop_threads(1)
    _worker_compiler = FrameCompiler(vocabulary)


@dc.dataclass(frozen=True)
class _Descriptor:
    name: str
    offset: int
    length: int
    epoch: int
    envelope: dict[str, Any]


@dc.dataclass(frozen=True)
class _SharedArray:
    name: str
    offset: int
    count: int
    dtype: str


@dc.dataclass(frozen=True)
class _ReadySource:
    descriptor: _Descriptor
    tree: bytes
    # fast_chain only: slabs the learner derives itself (targets, decoded rows)
    # live outside the slot, so they travel inline as arrays; old packets are
    # always slot views.
    arrays: tuple[_SharedArray | np.ndarray, ...]
    header: dict[str, Any]
    seconds: float
    pid: int


@dc.dataclass(frozen=True)
class _PackedBatch:
    tree: Any
    arrays: tuple[_SharedArray, ...]
    # Hand-off mode only: the opaque tree stays pickled and the controller reads
    # just this small summary; the SGD process verifies the file digest.
    summary: dict[str, Any] | None = None


@dc.dataclass(frozen=True)
class PreparedHandoff:
    """Controller view of one complete PreparedBatch whose bytes stay in its file.

    Explicit `sgd_process` profile only. The controller never pins, stages or
    unpickles the tensor tree; it keeps the metadata that selection and
    successful-use accounting already read. The spawned SGD process maps the
    same `_write_batch` file, checks `sha256` over `used_bytes`, then pins it.
    """

    request: PrepareRequest
    metadata: tuple[dict[str, Any], ...]
    policy_mask: torch.Tensor  # CPU bool copy; same values as PreparedBatch.policy_mask
    label_updates: tuple[dict[str, Any], ...]
    timings: dict[str, float]
    payload_bytes: int
    minibatch_count: int
    path: str
    tree: bytes  # trusted spawn-IPC metadata, never a durable or public format
    arrays: tuple[_SharedArray, ...]
    used_bytes: int
    sha256: str


@dc.dataclass(frozen=True)
class _WorkerResult:
    value: Any
    pid: int
    wall_seconds: float
    cpu_seconds: float


def _timed(function: Any, *args: Any) -> _WorkerResult:
    wall, cpu = time.perf_counter(), time.process_time()
    value = function(*args)
    return _WorkerResult(value, os.getpid(), time.perf_counter() - wall, time.process_time() - cpu)


def _write_batch(batch: PreparedBatch, path: str, *, handoff: bool = False) -> _PackedBatch:
    tree, arrays = bp.pack_transport(batch)
    with open(path, "r+b") as stream:
        mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_WRITE)
    try:
        cursor, views = 0, []
        for array in arrays:
            alignment = array.dtype.itemsize
            cursor = (cursor + alignment - 1) // alignment * alignment
            if cursor + array.nbytes > len(mapping):
                raise ValueError("binary_prepared_output_byte_limit")
            mapping[cursor : cursor + array.nbytes] = memoryview(array).cast("B")
            views.append(_SharedArray(path, cursor, array.size, array.dtype.str))
            cursor += array.nbytes
        if not handoff:
            return _PackedBatch(tree, tuple(views))
        # Worker-side digest: the controller never hashes or unpickles the batch.
        summary = {
            "request": batch.request,
            "metadata": batch.metadata,
            "policy_mask": tuple(bool(value) for value in batch.policy_mask.tolist()),
            "label_updates": batch.label_updates,
            "timings": dict(batch.timings),
            "payload_bytes": batch.payload_bytes,
            "minibatch_count": len(batch.minibatches),
            "used_bytes": cursor,
            "sha256": hashlib.sha256(memoryview(mapping)[:cursor]).hexdigest(),
        }
        return _PackedBatch(pickle.dumps(tree, protocol=5), tuple(views), summary)
    finally:
        mapping.close()


def _decode_source(descriptor: _Descriptor) -> _ReadySource:
    """Parallel source materialization. Only metadata crosses either IPC hop."""
    began = time.perf_counter()
    with open(descriptor.name, "rb") as stream:
        mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
    try:

        def decode() -> _ReadySource:
            if int.from_bytes(mapping[:8], "little") != descriptor.epoch:
                raise ValueError("binary_shared_slot_epoch_changed")
            raw = memoryview(mapping)[descriptor.offset : descriptor.offset + descriptor.length]
            if descriptor.envelope.get("format") == FAST_FORMAT:
                # fast_chain only: the learner's own envelope binds the sealed segment.
                _source, header = decode_fast_packet(
                    raw, envelope=descriptor.envelope, compiler=_worker_compiler
                )
            else:
                _source, header = decode_packet(
                    raw, envelope=descriptor.envelope, compiler=_worker_compiler
                )
            tree, slabs = bp.pack_transport(_source)
            base = np.frombuffer(mapping, dtype=np.uint8).ctypes.data
            arrays: tuple[_SharedArray | np.ndarray, ...]
            if descriptor.envelope.get("format") == FAST_FORMAT:
                end = base + len(mapping)
                arrays = tuple(
                    _SharedArray(descriptor.name, slab.ctypes.data - base, slab.size,
                                 slab.dtype.str)
                    if base <= slab.ctypes.data and slab.ctypes.data + slab.nbytes <= end
                    else np.array(slab, copy=True)
                    for slab in slabs
                )
            else:
                arrays = tuple(
                    _SharedArray(descriptor.name, slab.ctypes.data - base, slab.size,
                                 slab.dtype.str)
                    for slab in slabs
                )
            witness = dict(header["witness"])
            # The complete InputSpec/public binding was checked above. Label
            # correlation only needs the original step number and successor
            # witness; do not repeat the full InputSpec in either IPC hop.
            witness["steps"] = [
                {k: step[k] for k in ("step_index", "successor", "successor_sha256")}
                for step in witness["steps"]
            ]
            return _ReadySource(
                descriptor,
                # The spawn queue already uses trusted Python IPC. Keep its
                # immutable metadata opaque through the parent, so the learner
                # does not unpickle/re-pickle thousands of per-frame objects.
                # This is never a public packet or a durable restore format.
                pickle.dumps(tree, protocol=5),
                arrays,
                {"envelope": header["envelope"], "witness": witness},
                time.perf_counter() - began,
                os.getpid(),
            )

        return decode()
    finally:
        gc.collect()
        with suppress(BufferError):
            mapping.close()


def _opened(ready: tuple[_ReadySource, ...], operation: Any) -> Any:
    handles = {}
    for name in {item.descriptor.name for item in ready}:
        with open(name, "rb") as stream:
            handles[name] = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
    try:

        def run() -> Any:
            packets = []
            for item in ready:
                d = item.descriptor
                if int.from_bytes(handles[d.name][:8], "little") != d.epoch:
                    raise ValueError("binary_shared_slot_epoch_changed")
                arrays = tuple(
                    a if isinstance(a, np.ndarray) else np.frombuffer(
                        handles[a.name], dtype=np.dtype(a.dtype), count=a.count, offset=a.offset
                    )
                    for a in item.arrays
                )
                source = bp.unpack_transport((pickle.loads(item.tree), arrays))
                source = dc.replace(
                    source, pid=item.pid, seconds={"binary_source_decode_seconds": item.seconds}
                )
                packets.append((source, item.header))
            return operation(packets)

        return run()
    finally:
        gc.collect()
        for handle in handles.values():
            with suppress(BufferError):
                handle.close()


def _assemble(
    ready: tuple[_ReadySource, ...],
    request: PrepareRequest,
    member_indices: tuple[int, ...],
    auxiliary: tuple[tuple[int, Any, Any], ...],
    options: dict[str, Any],
    excluded: frozenset[str],
    submitted: float,
    output: str,
    handoff: bool = False,
) -> Any:
    began = time.perf_counter()

    def work(packets: Any) -> Any:
        sources = []
        for member, index in zip(request.members, member_indices, strict=True):
            source, header = packets[index]
            _member(source, header, member, request.identity.training_generation)
            if header["envelope"]["run"] != request.identity.run:
                raise ValueError("binary_source_run_changed")
            bp.validate_training_source(header["witness"], excluded)
            if header["envelope"]["payload_kind"] != "ppo":
                raise ValueError("binary_PPO_payload_kind_required")
            sources.append(source)
        aux_frames = []
        for index, ref, member in auxiliary:
            source, header = packets[index]
            _member(source, header, member, request.identity.training_generation)
            if (
                header["envelope"]["run"] != request.identity.run
                or source.compiler_identity != request.compiler_identity
            ):
                raise ValueError("binary_auxiliary_run_or_compiler_changed")
            bp.validate_training_source(header["witness"], excluded)
            aux_frames.append(auxiliary_frame(source, ref))
        decoded = time.perf_counter()
        batch = bp.assemble(request, tuple(sources), auxiliary_frames=tuple(aux_frames), **options)
        return _write_batch(
            dc.replace(
                batch,
                timings={
                    **batch.timings,
                    "binary_decode_seconds": decoded - began,
                    "binary_assemble_seconds": time.perf_counter() - decoded,
                    "binary_worker_queue_seconds": began - submitted,
                },
            ),
            output,
            handoff=handoff,
        )

    return _opened(ready, work)


def _member(source: Any, header: dict[str, Any], member: Any, generation: str) -> None:
    env, witness = header["envelope"], header["witness"]
    if (
        source.segment_id != member.segment_id
        or source.run_id != member.run_id
        or witness["rows"] != member.rows
        or env["episode"] != member.episode
        or witness["mu"]["snapshot_sha256"] != member.mu_snapshot_sha256
        or source.generation != generation
    ):
        raise ValueError("binary_source_membership_or_mu_changed")


def _labels(
    ready: tuple[_ReadySource, ...],
    request: dict[str, Any],
    members: tuple[Any, ...],
    indices: tuple[int, ...],
    excluded: frozenset[str],
) -> Any:
    def work(packets: Any) -> Any:
        by_segment = {}
        for member, index in zip(members, indices, strict=True):
            source, header = packets[index]
            _member(source, header, member, request["training_generation"])
            if "run" in request and header["envelope"]["run"] != request["run"]:
                raise ValueError("binary_label_run_changed")
            by_segment[member.segment_id] = source, header, index

        def snapshot(member: Any, witness: Any, row: int) -> AuxiliaryInput:
            source, header, index = by_segment[member.segment_id]
            ref = source.row_refs[row]
            if ref is None:
                raise ValueError("binary_auxiliary_row_binding_changed")
            auxiliary_frame(source, ref)
            # Retain exact typed packet bytes in the original bounded AuxPool.
            # One bytes object per packet is shared by all its row snapshots.
            if index not in bodies:
                raise ValueError("binary_auxiliary_snapshot_missing")
            return AuxiliaryInput(ref, member, bodies[index], ready[index].descriptor.envelope)

        bodies: dict[int, bytes] = {}
        for index, item in enumerate(ready):
            d = item.descriptor
            with open(d.name, "rb") as stream:
                stream.seek(d.offset)
                bodies[index] = stream.read(d.length)
        # EpisodeLabels keeps the canonical original PPO reference after its
        # body is retired. An aux packet carries that exact original reference
        # bound by the actor spool; retain it only as a final-witness locator.
        # The actual row/member snapshots still use the new aux packet below.
        aliases = []
        for member in members:
            original = by_segment[member.segment_id][1]["witness"].get("original_payload")
            if original is not None:
                for record in request.get("segments", ()):
                    payload = record.get("payload")
                    if (
                        payload is not None
                        and payload.get("slot") == original["slot"]
                        and payload != original
                    ):
                        raise ValueError("binary_original_PPO_reference_changed")
                aliases.append(dc.replace(member, payload=BinaryRef(**original)))
        return bp.verify_auxiliary_sources(
            request,
            (*aliases, *members),
            read_source=lambda member: by_segment[member.segment_id][1]["witness"],
            snapshot_row=snapshot,
            excluded_seeds=excluded,
            successor_digest=lambda step: step["successor_sha256"],
        )

    return _opened(ready, work)


@dc.dataclass
class _Arena:
    name: str
    mapping: mmap.mmap
    epoch: int = 1
    cursor: int = 8
    users: int = 0


class _PinnedLease:
    def __init__(self, pool: Any, index: int) -> None:
        self.pool, self.index = pool, index
        self.events: list[Any] = []

    def copied(self, event: Any) -> None:
        self.events.append(event)

    def __del__(self) -> None:
        self.pool.release(self.index, tuple(self.events))


class _PinnedBuffers:
    """Exactly two dtype-slab buffers. Tensor views retain their lease through H2D."""

    def __init__(self, maximum: int, slots: int = 2) -> None:
        self.maximum = maximum
        self.condition = threading.Condition()
        self.buffers: list[dict[torch.dtype, torch.Tensor]] = [{} for _ in range(slots)]
        self.busy = [False] * slots
        self.events: list[Any] = [None] * slots
        self.closed = False
        self.epoch = 0
        self.allocation_lock = threading.Lock()

    def fence(self) -> None:
        with self.condition:
            self.epoch += 1
            self.condition.notify_all()

    def release(self, index: int, event: Any) -> None:
        with self.condition:
            self.events[index] = event
            self.busy[index] = False
            self.condition.notify_all()

    def pin(self, batch: PreparedBatch, epoch: int) -> PreparedBatch:
        if not torch.cuda.is_available():
            return batch
        with self.condition:
            while True:
                if self.closed or epoch != self.epoch:
                    raise CancelledError("binary_pinned_preparation_fenced")
                available = [
                    i
                    for i in range(len(self.buffers))
                    if not self.busy[i]
                    and (self.events[i] is None or all(e.query() for e in self.events[i]))
                ]
                if available:
                    index = available[0]
                    self.busy[index] = True
                    break
                self.condition.wait(0.01)
        lease = _PinnedLease(self, index)
        with self.allocation_lock:
            return self._copy(batch, index, lease)

    def _copy(self, batch: PreparedBatch, index: int, lease: _PinnedLease) -> PreparedBatch:
        buffers = self.buffers[index]
        tree, arrays = bp.pack_transport(batch)
        required: dict[torch.dtype, int] = {}
        for a in arrays:
            tensor = torch.from_numpy(a)
            required[tensor.dtype] = required.get(tensor.dtype, 0) + tensor.numel()
        # Capacity is total retained allocation, including the other buffer.
        projected = sum(
            t.numel() * t.element_size()
            for i, b in enumerate(self.buffers)
            for dtype, t in b.items()
            if i != index or dtype not in required
        )
        projected += sum(
            max(count, buffers[dtype].numel() if dtype in buffers else 0)
            * torch.empty((), dtype=dtype).element_size()
            for dtype, count in required.items()
        )
        if projected > self.maximum:
            raise ValueError("binary_pinned_byte_limit")
        cursors: dict[torch.dtype, int] = {}
        views = []
        for array in arrays:
            tensor = torch.from_numpy(array)
            dtype, count = tensor.dtype, tensor.numel()
            if dtype not in buffers or buffers[dtype].numel() < required[dtype]:
                buffers[dtype] = torch.empty(required[dtype], dtype=dtype, pin_memory=True)
            start = cursors.get(dtype, 0)
            view = buffers[dtype][start : start + count]
            view.copy_(tensor)
            views.append(view)
            cursors[dtype] = start + count

        def replace(node: Any) -> Any:
            if isinstance(node, bp._TensorView):
                tensor = views[node.slab][node.offset : node.offset + math_prod(node.shape)].view(
                    node.shape
                )
                tensor._rl_pinned_lease = lease  # type: ignore[attr-defined]
                return tensor
            return node

        return cast(PreparedBatch, bp._map_structure(tree, replace))

    def close(self) -> None:
        with self.condition:
            self.closed = True
            for events in self.events:
                for event in events or ():
                    event.synchronize()
            self.condition.notify_all()


def math_prod(shape: tuple[int, ...]) -> int:
    import math

    return math.prod(shape)


class BinaryBatchPreparer:
    def __init__(
        self,
        *,
        provider: BinarySourceProvider,
        vocabulary: dict[str, Any],
        workers: int,
        threads: int,
        scratch: Path | str,
        source_slots: int,
        max_source_bytes: int,
        assembly_workers: int = 2,
        prepared_batches: int = 2,
        label_workers: int = 1,
        source_wait_seconds: float = 60,
        handoff: bool = False,
        **existing_assembly_options: Any,
    ) -> None:
        if (
            any(
                type(value) is not int
                for value in (workers, threads, source_slots, max_source_bytes, assembly_workers,
                              prepared_batches, label_workers)
            )
            or min(workers, threads, source_slots, max_source_bytes, assembly_workers,
                   prepared_batches, label_workers) < 1
        ):
            raise ValueError("positive_binary_preparation_limits_required")
        if not 0 < source_wait_seconds <= 120:
            raise ValueError("invalid_binary_source_wait_bound")
        if type(handoff) is not bool:
            raise ValueError("binary_handoff_mode_must_be_explicit")
        # Explicit S3 SGD-process profile: complete batches stay in their output
        # files until the SGD process reports them staged; this process never pins.
        self.handoff = handoff
        self.handoffs: dict[str, PreparedHandoff] = {}
        # path -> its collector Future (filled under the condition by _enqueue).
        # A hand-off registered before its Future completes is one batch, not two.
        self.handoff_owners: dict[str, list[Future]] = {}
        self.source_wait_seconds = source_wait_seconds
        self.source_wait_seconds_total = 0.0
        self.main_source_wait_seconds = 0.0
        self.provider, self.vocabulary = provider, vocabulary
        self.workers, self.threads = workers, threads
        self.assembly_workers = assembly_workers
        self.prepared_batches, self.label_workers = prepared_batches, label_workers
        self._started = time.perf_counter()
        self._worker_stats: dict[tuple[str, int], dict[str, float]] = {}
        self._failed_tasks = {"source": 0, "assembly": 0, "labels": 0}
        self.scratch = Path(scratch)
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.capacity = max_source_bytes // source_slots
        self.slot_count, self.maximum = source_slots, max_source_bytes
        if self.capacity < 9:
            raise ValueError("binary_source_slot_byte_limit")
        self.excluded = frozenset(existing_assembly_options.pop("excluded_seeds", ()))
        self.max_prepared_bytes = existing_assembly_options.pop("max_prepared_bytes", 2 * 1024**3)
        self.pinned = _PinnedBuffers(
            existing_assembly_options.pop("max_pinned_bytes", 512 * 1024**2),
            slots=prepared_batches + 1,
        )
        # Old construction-only options do not grant a JSON fallback here.
        for key in ("peer", "run", "reader", "gae_lambda"):
            existing_assembly_options.pop(key, None)
        allowed = {
            "microbatch_size",
            "deduplicate_text",
            "scene_attention_area",
            "text_attention_area",
            "prepared_layout",
        }
        if set(existing_assembly_options) - allowed:
            raise TypeError("unknown_binary_assembly_options")
        self.options = existing_assembly_options
        self.condition = threading.Condition(threading.RLock())
        self.arenas: list[_Arena] = []
        self.next_arena = 0
        self.sources: OrderedDict[BinaryRef, tuple[int, _Descriptor]] = OrderedDict()
        self.source_jobs: dict[BinaryRef, Future] = {}
        # Sources whose arena bytes are being hashed/copied outside the lock.
        self.copying: set[BinaryRef] = set()
        self.pending: set[Future] = set()
        self.preparing: set[Future] = set()
        self.generation: str | None = None
        self.closed = self.fenced = False
        self.coordinator = ThreadPoolExecutor(
            max_workers=prepared_batches, thread_name_prefix="rl-binary-ready")
        self.label_coordinator = ThreadPoolExecutor(
            max_workers=label_workers, thread_name_prefix="rl-binary-labels")
        # ProcessPool done-callbacks run on that pool's manager thread, while
        # pool.submit is called under self.condition. A manager thread waiting
        # for self.condition stops draining results and its wakeup pipe; on
        # Python <3.11.6 (gh-105829) the next submit then blocks forever.
        # Callbacks only enqueue here; this one thread takes the lock.
        self.callbacks = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rl-binary-callbacks")
        self.pool = self._pool()
        self.assembly_pool = self._pool(workers=self.assembly_workers)
        self.label_pool = self._pool(workers=self.label_workers)

    def _record_worker(self, role: str, future: Future) -> None:
        with self.condition:
            try:
                result = future.result()
            except BaseException:
                self._failed_tasks[role] += 1
                return
            stats = self._worker_stats.setdefault(
                (role, result.pid), {"tasks": 0, "busy_seconds": 0, "cpu_seconds": 0}
            )
            stats["tasks"] += 1
            stats["busy_seconds"] += result.wall_seconds
            stats["cpu_seconds"] += result.cpu_seconds

    def summary(self) -> dict[str, Any]:
        """Actual processes and completed-task utilization, never training counters."""
        with self.condition:
            window = max(time.perf_counter() - self._started, 1e-9)
            pools = {"source": self.pool, "assembly": self.assembly_pool, "labels": self.label_pool}
            live = {
                role: tuple((getattr(pool, "_processes", None) or {}).keys())
                for role, pool in pools.items()
            }
            keys = set(self._worker_stats) | {
                (role, pid) for role, pids in live.items() for pid in pids
            }
            rows = []
            for role, pid in sorted(keys):
                stats = self._worker_stats.get(
                    (role, pid), {"tasks": 0, "busy_seconds": 0, "cpu_seconds": 0}
                )
                rows.append(
                    {
                        "pool": role,
                        "pid": pid,
                        **stats,
                        "busy_fraction": stats["busy_seconds"] / window,
                    }
                )
            return {
                "source_workers": self.workers,
                "assembly_workers": self.assembly_workers,
                "label_workers": self.label_workers,
                "prepared_batches": self.prepared_batches,
                "actual_source_workers": len(live["source"]),
                "actual_assembly_workers": len(live["assembly"]),
                "source_slots": self.slot_count,
                "source_capacity_wait_seconds": self.source_wait_seconds_total,
                "main_source_capacity_wait_seconds": self.main_source_wait_seconds,
                "max_source_bytes": self.maximum,
                "source_cached_bytes": sum(d.length for _, d in self.sources.values()),
                "window_seconds": window,
                "workers": rows,
                "failed_tasks_without_timing": dict(self._failed_tasks),
                "busy_fraction_definition": (
                    "completed worker-function wall seconds / preparer lifetime; "
                    "excludes parent work and process startup"
                ),
            }

    def cached_sources(self) -> frozenset[BinaryRef]:
        """A speculative hint only; consumers still reserve/revalidate sources."""
        with self.condition:
            return frozenset(self.sources)

    def _pool(self, workers: int | None = None) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(
            max_workers=self.workers if workers is None else workers,
            mp_context=mp.get_context("spawn"),
            initializer=_initialize,
            initargs=(self.vocabulary, self.threads),
        )

    def _prefetch(
        self, ref: BinaryRef, envelope: dict[str, Any] | None, body: bytes | None = None
    ) -> None:
        copy = self._reserve_copy(ref, envelope)
        if copy is not None:
            self._finish_copy(ref, *copy, self._copy_source(ref, *copy, envelope, body))

    def _reserve_copy(
        self, ref: BinaryRef, envelope: dict[str, Any] | None
    ) -> tuple[int, int] | None:
        """Under the condition: validate and reserve arena bytes; None if cached."""
        if (
            not isinstance(ref, BinaryRef)
            or ref.format not in (TENSOR_SEGMENT_FORMAT, FAST_FORMAT)
            or (ref.format == FAST_FORMAT and (
                envelope is None or envelope.get("format") != FAST_FORMAT))
            or not 0 < ref.size <= min(MAX_PACKET_BYTES, self.capacity - 8)
            or (
                envelope is not None
                and (
                    envelope.get("packet_sha256") != ref.sha256
                    or envelope.get("packet_size") != ref.size
                )
            )
        ):
            raise ValueError("binary_reference_or_slot_byte_limit")
        if ref in self.sources:
            previous = self.sources[ref][1].envelope
            if envelope is not None and previous != envelope:
                raise ValueError("binary_same_reference_changed_envelope")
            self.sources.move_to_end(ref)
            return None
        if self.closed or self.fenced:
            raise RuntimeError("binary_preparer_fenced_or_closed")
        if ref in self.copying:
            raise ValueError("binary_source_slots_busy")
        order = sorted(
            range(len(self.arenas)), key=lambda i: (i - self.next_arena) % self.slot_count
        )
        arena_index = next(
            (i for i in order if self.arenas[i].cursor + ref.size <= self.capacity), None
        )
        if len(self.arenas) < self.slot_count and self.next_arena >= len(self.arenas):
            arena_index = None
        if arena_index is None and len(self.arenas) < self.slot_count:
            path = self.scratch / ("rl-source-" + uuid.uuid4().hex)
            with path.open("xb") as stream:
                os.chmod(path, 0o600)
                stream.truncate(self.capacity)
            with path.open("r+b") as stream:
                mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_WRITE)
            mapping[:8] = (1).to_bytes(8, "little")
            self.arenas.append(_Arena(str(path), mapping))
            arena_index = len(self.arenas) - 1
        if arena_index is None:
            arena_index = next((i for i, a in enumerate(self.arenas) if not a.users), None)
            if arena_index is None:
                raise ValueError("binary_source_slots_busy")
            arena = self.arenas[arena_index]
            for key in [k for k, (i, _) in self.sources.items() if i == arena_index]:
                del self.sources[key]
                del self.source_jobs[key]
            arena.cursor = 8
            arena.epoch += 1
            arena.mapping[:8] = arena.epoch.to_bytes(8, "little")
        arena = self.arenas[arena_index]
        self.next_arena = (arena_index + 1) % self.slot_count
        # The arena cannot be reset or unmapped while it has a user; the
        # reserved byte range is private to this copy until it is published.
        offset = arena.cursor
        arena.cursor += ref.size
        arena.users += 1
        self.copying.add(ref)
        return arena_index, offset

    def _copy_source(
        self, ref: BinaryRef, arena_index: int, offset: int,
        envelope: dict[str, Any] | None, body: bytes | None,
    ) -> dict[str, Any] | BaseException:
        """Hash/copy one immutable packet into its reserved range; may run unlocked."""
        from contextlib import nullcontext

        arena = self.arenas[arena_index]
        try:
            return self._copy_bytes(ref, arena, offset, envelope,
                                    self.provider.borrow(ref) if body is None
                                    else nullcontext(memoryview(body)))
        except BaseException as error:
            return error

    @staticmethod
    def _copy_bytes(ref: BinaryRef, arena: _Arena, offset: int,
                    envelope: dict[str, Any] | None, context: Any) -> dict[str, Any]:
        # Provider owns the borrow until the single complete byte copy finishes.
        with context as raw:
            if len(raw) != ref.size or hashlib.sha256(raw).hexdigest() != ref.sha256:
                raise ValueError("binary_provider_integrity_changed")
            bound_envelope = (
                envelope
                if envelope is not None
                else dict(
                    packet_header(raw)["envelope"], packet_size=ref.size, packet_sha256=ref.sha256
                )
            )
            arena.mapping[offset : offset + ref.size] = raw
        return copy.deepcopy(bound_envelope)

    def _finish_copy(
        self, ref: BinaryRef, arena_index: int, offset: int,
        copied: dict[str, Any] | BaseException,
    ) -> None:
        """Under the condition: publish the copied source and submit its decode."""
        arena = self.arenas[arena_index]
        self.copying.discard(ref)
        self.condition.notify_all()
        try:
            if isinstance(copied, BaseException):
                raise copied
            generation = copied["training_generation"]
            if self.generation is None:
                self.generation = generation
            if generation != self.generation:
                raise ValueError("binary_prefetch_generation_changed")
            if self.closed or self.fenced:
                raise RuntimeError("binary_preparer_fenced_or_closed")
        except BaseException:
            arena.users -= 1
            raise
        descriptor = _Descriptor(arena.name, offset, ref.size, arena.epoch, dict(copied))
        self.sources[ref] = arena_index, descriptor
        try:
            job = self.pool.submit(_timed, _decode_source, descriptor)
        except BaseException:
            arena.users -= 1
            del self.sources[ref]
            raise
        self.source_jobs[ref] = job
        job.add_done_callback(
            lambda done: self.callbacks.submit(self._record_worker, "source", done))
        job.add_done_callback(lambda _job: self.callbacks.submit(self._release, (descriptor,)))

    def _wait_for_source(self, deadline: float) -> None:
        if self.closed or self.fenced:
            raise RuntimeError("binary_preparer_fenced_or_closed")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BoundaryError("rl_prepare", "CAPACITY_WAIT")
        began = time.perf_counter()
        self.condition.wait(remaining)
        waited = time.perf_counter() - began
        self.source_wait_seconds_total += waited
        if threading.current_thread() is threading.main_thread():
            self.main_source_wait_seconds += waited

    def prefetch(self, ref: BinaryRef, envelope: dict[str, Any], *,
                 body: bytes | None = None, wait: bool = True) -> None:
        deadline = time.monotonic() + self.source_wait_seconds
        # A caller already inside the condition (submit) keeps the locked copy.
        nested = self.condition._is_owned()  # type: ignore[attr-defined]
        with self.condition:
            while True:
                try:
                    if nested:
                        self._prefetch(ref, envelope, body)
                        return
                    reserved = self._reserve_copy(ref, envelope)
                    break
                except ValueError as error:
                    if not wait or str(error) != "binary_source_slots_busy":
                        raise
                    self._wait_for_source(deadline)
        if reserved is None:
            return
        # SHA-256 and the arena copy of a 10-30 MB packet run without the
        # preparer condition, so sources copy in parallel with submit/labels.
        copied = self._copy_source(ref, *reserved, envelope, body)
        with self.condition:
            self._finish_copy(ref, *reserved, copied)

    def _reserve(
        self, refs: tuple[BinaryRef, ...],
        snapshots: dict[BinaryRef, AuxiliaryInput] | None = None,
    ) -> tuple[tuple[_Descriptor, ...], tuple[int, ...]]:
        unique = tuple(dict.fromkeys(refs))
        if sum(ref.size for ref in unique) > self.maximum - 8 * self.slot_count:
            raise ValueError("binary_request_source_byte_limit")
        deadline = time.monotonic() + self.source_wait_seconds
        while True:
            indices: dict[BinaryRef, int] = {}
            descriptors: list[_Descriptor] = []
            arenas: list[int] = []
            try:
                for ref in unique:
                    if ref not in self.sources:
                        # AuxPool owns complete verified bytes after the upload
                        # spool is released. A concurrent cache eviction must
                        # refill that same snapshot, never reopen a retired ref.
                        item = (snapshots or {}).get(ref)
                        # A fast segment has no self-describing envelope: the
                        # learner's provider holds the one it admitted.
                        envelope = (item.envelope if item else
                                    cast(Any, self.provider).envelope(ref)
                                    if ref.format == FAST_FORMAT else None)
                        self._prefetch(ref, envelope, item.body if item else None)
                    arena_index, descriptor = self.sources[ref]
                    self.arenas[arena_index].users += 1
                    arenas.append(arena_index)
                    indices[ref] = len(descriptors)
                    descriptors.append(descriptor)
                return tuple(descriptors), tuple(indices[ref] for ref in refs)
            except BaseException as error:
                # Never wait while retaining a partial request: two concurrent
                # label/assembly reservations must be able to free all arenas.
                for index in arenas:
                    self.arenas[index].users -= 1
                self.condition.notify_all()
                if not isinstance(error, ValueError) or str(error) != "binary_source_slots_busy":
                    raise
                self._wait_for_source(deadline)

    def _release(self, descriptors: tuple[_Descriptor, ...]) -> None:
        with self.condition:
            by_name = {a.name: a for a in self.arenas}
            for descriptor in descriptors:
                by_name[descriptor.name].users -= 1
            self.condition.notify_all()

    def _enqueue(
        self,
        function: Any,
        descriptors: tuple[_Descriptor, ...],
        jobs: tuple[Future, ...],
        *args: Any,
    ) -> Future:
        epoch = self.pinned.epoch
        owner: list[Future] = []

        def collect() -> Any:
            began = time.perf_counter()
            output = None
            handed_off = False
            try:
                ready = tuple(job.result().value for job in jobs)
                sources_at = time.perf_counter()
                # The validated trees and array offsets are small internal IPC
                # metadata; the source tensor slabs remain in their shared arenas.
                worker_args = args
                if function is _assemble:
                    output = self.scratch / ("rl-ready-" + uuid.uuid4().hex)
                    with output.open("xb") as stream:
                        os.chmod(output, 0o600)
                        stream.truncate(args[0].max_output_bytes + 64)
                    worker_args = (*args, str(output), self.handoff)
                role = "assembly" if function is _assemble else "labels"
                pool = self.assembly_pool if function is _assemble else self.label_pool
                future = pool.submit(_timed, function, ready, *worker_args)
                future.add_done_callback(
                    lambda done: self.callbacks.submit(self._record_worker, role, done))
                result = future.result().value
                if function is _assemble:
                    if output is None:
                        raise RuntimeError("binary_output_descriptor_missing")
                    if self.handoff:
                        handoff = self._handoff(result, output, began, sources_at, epoch,
                                                owner)
                        handed_off = True
                        return handoff
                    with output.open("rb") as input_stream:
                        mapping = mmap.mmap(input_stream.fileno(), 0, access=mmap.ACCESS_READ)
                    try:

                        def receive() -> PreparedBatch:
                            arrays = tuple(
                                np.frombuffer(
                                    mapping, dtype=np.dtype(a.dtype), count=a.count, offset=a.offset
                                )
                                for a in result.arrays
                            )
                            batch = bp.unpack_transport((result.tree, arrays))
                            batch = dc.replace(
                                batch,
                                timings={
                                    **batch.timings,
                                    "binary_sources_wait_seconds": sources_at - began,
                                    "binary_ipc_wall_seconds": time.perf_counter() - began,
                                },
                            )
                            if torch.cuda.is_available():
                                return self.pinned.pin(batch, epoch)
                            owned = cast(
                                PreparedBatch,
                                bp.unpack_transport(
                                    (result.tree, tuple(array.copy() for array in arrays))
                                ),
                            )
                            return dc.replace(owned, timings=batch.timings)

                        return receive()
                    finally:
                        with suppress(BufferError):
                            mapping.close()
                return result
            finally:
                if output is not None and not handed_off:
                    output.unlink(missing_ok=True)
                self._release(descriptors)

        coordinator = self.coordinator if function is _assemble else self.label_coordinator
        result = coordinator.submit(collect)
        owner.append(result)  # caller holds the condition; _handoff waits for it

        # A cancelled collector has never dispatched assembly. Decode jobs
        # independently retain their arenas, so only this reservation is released.
        def cancelled(done: Future) -> None:
            if done.cancelled():
                self._release(descriptors)

        result.add_done_callback(cancelled)
        self.pending.add(result)
        if function is _assemble:
            self.preparing.add(result)
        return result

    def _handoff(self, result: _PackedBatch, output: Path, began: float,
                 sources_at: float, epoch: int, owner: list[Future]) -> PreparedHandoff:
        summary = result.summary
        if summary is None or not isinstance(result.tree, bytes):
            raise RuntimeError("binary_handoff_summary_missing")
        with self.condition:
            # Same fence as pinning: a barrier/close never hands off an old file.
            if self.closed or epoch != self.pinned.epoch:
                raise CancelledError("binary_pinned_preparation_fenced")
            handoff = PreparedHandoff(
                summary["request"], summary["metadata"],
                torch.tensor(summary["policy_mask"], dtype=torch.bool),
                summary["label_updates"],
                {**summary["timings"], "binary_sources_wait_seconds": sources_at - began,
                 "binary_ipc_wall_seconds": time.perf_counter() - began},
                summary["payload_bytes"], summary["minibatch_count"], str(output),
                result.tree, result.arrays, summary["used_bytes"], summary["sha256"])
            self.handoffs[handoff.path] = handoff
            self.handoff_owners[handoff.path] = owner
        return handoff

    def _handed_off(self) -> int:
        """Hand-offs whose collector Future already left `preparing` (held lock)."""
        return sum(1 for path in self.handoffs
                   if not any(f in self.preparing for f in self.handoff_owners.get(path, ())))

    def release_handoff(self, handoff: PreparedHandoff) -> None:
        """The SGD process owns pinned/device copies; remove the derived file."""
        with self.condition:
            if self.handoffs.pop(handoff.path, None) is not handoff:
                raise ValueError("binary_handoff_not_owned")
            self.handoff_owners.pop(handoff.path, None)
            self.condition.notify_all()
        Path(handoff.path).unlink()

    def submit(
        self, request: PrepareRequest, auxiliary_inputs: tuple = ()
    ) -> Future[PreparedBatch]:
        with self.condition:
            self.pending = {f for f in self.pending if not f.done()}
            self.preparing = {f for f in self.preparing if not f.done()}
            if self.closed or self.fenced:
                raise RuntimeError("binary_preparer_fenced_or_closed")
            if len(self.preparing) + self._handed_off() >= self.prepared_batches:
                raise ValueError("binary_current_plus_next_limit")
            if (
                type(request.max_output_bytes) is not int
                or not 0 < request.max_output_bytes
                <= self.max_prepared_bytes // self.prepared_batches
            ):
                raise ValueError("binary_current_plus_next_byte_limit")
            if self.generation != request.identity.training_generation:
                raise ValueError("binary_request_generation_changed")
            ppo = {m.segment_id for m in request.members}
            cached = {(a.source["segment_id"], a.source["row"]): a for a in auxiliary_inputs}
            selected = []
            # Snapshots are already binary; prefetch exact immutable packet bytes
            # through a local temporary provider, never a Hot/JSON reader fallback.
            for selection in request.auxiliary:
                if selection.source["segment_id"] in ppo:
                    continue
                item = cached[selection.source["segment_id"], selection.source["row"]]
                if item.source != selection.source or not isinstance(
                    item.member.payload, BinaryRef
                ):
                    raise ValueError("binary_auxiliary_snapshot_changed")
                ref = item.member.payload
                if ref not in self.sources:
                    envelope = item.envelope
                    if envelope is None:
                        # Explicit binary snapshots made by an older/manual
                        # caller can reconstruct their own exact header; there
                        # is no alternate JSON/Hot source reader here.
                        envelope = dict(
                            packet_header(item.body)["envelope"],
                            packet_size=len(item.body),
                            packet_sha256=hashlib.sha256(item.body).hexdigest(),
                        )
                    if ref.size != len(item.body) or ref.sha256 != envelope["packet_sha256"]:
                        raise ValueError("binary_auxiliary_snapshot_integrity_changed")
                    self.prefetch(ref, envelope, body=item.body)
                selected.append(item)
            refs = tuple(m.payload for m in request.members) + tuple(
                a.member.payload for a in selected
            )
            descriptors, indices = self._reserve(
                refs, {a.member.payload: a for a in selected})
            offset = len(request.members)
            auxiliary = tuple(
                (indices[offset + i], a.source, a.member) for i, a in enumerate(selected)
            )
            return self._enqueue(
                _assemble,
                descriptors,
                tuple(self.source_jobs[ref] for ref in dict.fromkeys(refs)),
                request,
                indices[:offset],
                auxiliary,
                self.options,
                self.excluded,
                time.perf_counter(),
            )

    def verify_labels(self, request: dict, members: tuple) -> Future:
        with self.condition:
            self.pending = {f for f in self.pending if not f.done()}
            self.preparing = {f for f in self.preparing if not f.done()}
            if self.closed or self.fenced:
                raise RuntimeError("binary_preparer_fenced_or_closed")
            descriptors, indices = self._reserve(tuple(m.payload for m in members))
            return self._enqueue(
                _labels,
                descriptors,
                tuple(self.source_jobs[ref] for ref in dict.fromkeys(m.payload for m in members)),
                request,
                members,
                indices,
                self.excluded,
            )

    def barrier(self, *, generation: str) -> None:
        with self.condition:
            self.fenced = True
            self.condition.notify_all()
            # Unlocked copies still write into reserved arena ranges.
            while self.copying:
                self.condition.wait()
            self.pinned.fence()
            pending = tuple(self.pending)
        errors = []
        for future in pending:
            try:
                future.result()
            except BaseException as error:
                errors.append(error)
        # No old-generation worker/mapping survives this return, even after a
        # failed task retained an exception traceback with shared tensor views.
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.assembly_pool.shutdown(wait=True, cancel_futures=True)
        self.label_pool.shutdown(wait=True, cancel_futures=True)
        # Deferred releases still name these arenas; apply them before teardown.
        self.callbacks.submit(lambda: None).result()
        with self.condition:
            for arena in self.arenas:
                arena.mapping.close()
                Path(arena.name).unlink()
            self.arenas.clear()
            # Unstaged hand-off files are derived bytes of fenced requests.
            for path in self.handoffs:
                Path(path).unlink(missing_ok=True)
            self.handoffs.clear()
            self.handoff_owners.clear()
            self.next_arena = 0
            self.sources.clear()
            self.source_jobs.clear()
            self.pending.clear()
            self.preparing.clear()
            self.generation = generation
            if not self.closed:
                self.pool = self._pool()
                self.assembly_pool = self._pool(workers=self.assembly_workers)
                self.label_pool = self._pool(workers=self.label_workers)
                self.fenced = False
        self.last_barrier_failures = tuple(str(error) for error in errors)
        # Original Future objects preserve each failure. As with Executor.shutdown,
        # draining never rethrows an already-observed task error as a new failure.

    def shutdown(self, *, wait: bool = True, cancel_futures: bool = True) -> None:
        # Resource lifetime requires actual worker completion. wait=False returns
        # while a single cleanup thread owns those resources through completion.
        with self.condition:
            if self.closed:
                return
            self.closed = True
            self.condition.notify_all()
        self.pinned.close()

        def close() -> None:
            try:
                self.barrier(generation=self.generation or "")
            finally:
                self.coordinator.shutdown(wait=True, cancel_futures=cancel_futures)
                self.label_coordinator.shutdown(wait=True, cancel_futures=cancel_futures)
                self.callbacks.shutdown(wait=True)

        if wait:
            close()
        else:
            threading.Thread(target=close, name="rl-binary-close", daemon=False).start()
