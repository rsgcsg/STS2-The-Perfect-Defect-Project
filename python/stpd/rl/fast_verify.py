"""Fast hand-off reads in verify worker processes (`fast_chain`, design a').

`dataplane.fast_verify_workers` = 0 (default) keeps `read_handoff` on the
learner's hand-off threads. With N > 0 the learner starts N resident,
stateless spawn processes. For each segment / labels message a hand-off thread
hands (shm name, size, sha256, kind, arena offset) to exactly one worker and
blocks on its reply without the GIL. The worker:

- opens the sender's block by name (as `_read_shm`: read-only, no resource
  tracker), copies `size` bytes into the learner-owned arena slots it was given,
- checks the whole-message sha256 (`handoff_digest`),
- parses the segment header (`decode_segment(header_only=True)`) and its
  `row_facts`; for labels, every carried entry's body sha256 and (when the
  learner's aux pools are on) that body's header parse,

and replies with a `VerifiedRead` (or the exception it raised). The thread
copies the bytes out of the arena, frees the slots and goes on exactly as with
`read_handoff`: the intake (generation, attestation, exclusion, mu ledger),
fence/exclusion checks under the learner condition, `_fast_apply` and the ack
after it all stay in the learner. Nothing about admission changes.

Failure rules: a worker's exception is re-raised on the hand-off thread with
its own class and message (`handoff_size`, `handoff_digest`, FileNotFoundError,
`SegmentFormatError` ...), at the same point the in-process path raises it.
A worker that is gone (EOF / broken pipe) raises `fast_verify_worker_died`,
which fences input like any hand-off error; the message is never re-sent to
another worker and never read in process instead.

Arena (the T4 ring's slot format, so prepare / SGD processes can later read
slots by index): one learner-owned shared memory block = an index region of
`slots` x 16 bytes (little-endian u64 state, u64 length; state 0 = empty,
1 = full; a message spanning k contiguous slots marks all k full and stores its
length in the first) followed by `slots` fixed slots of `slot_bytes`. Here a
slot is full only while one hand-off is in flight: the thread copies the bytes
out before `_fast_apply`. When no run of free slots fits, the thread blocks
(the back-pressure of a slow apply today); nothing is dropped or skipped.
"""

from __future__ import annotations

import mmap
import os
import pickle
import queue
import struct
import sys
import threading
from multiprocessing import get_context, shared_memory
from multiprocessing.connection import Connection, wait
from typing import Any

from .fast_handoff import HandoffError
from .fast_segment import decode_segment, sha256
from .fast_source import VerifiedRead, receive_handoff, row_facts

if sys.platform != "win32":
    import _posixshmem  # type: ignore[import-not-found]  # CPython-internal, no stub

EMPTY, FULL = 0, 1
INDEX_ENTRY = struct.Struct("<QQ")
CHANNELS_PER_WORKER = 8
# Reply room after the data in the same slots: the pickled `VerifiedRead` goes
# there, so the reply on the pipe is a few bytes (a ~66 KB header pickle sent
# over an 8 KB socket buffer costs one GIL hand-back per chunk on this thread).
REPLY_BYTES = 256 * 1024
LABELS_HEADER_REPLY_BYTES = 128 * 1024


class FastVerifyError(RuntimeError):
    """A verify pool failure (worker gone, arena too small): fences input, never retried."""


def _page(size: int) -> int:
    return -(-size // mmap.PAGESIZE) * mmap.PAGESIZE


class VerifyArena:
    """Learner-owned shared memory slots with an empty/full index (see module doc)."""

    def __init__(self, *, slots: int, slot_bytes: int) -> None:
        if type(slots) is not int or type(slot_bytes) is not int or slots < 1 or slot_bytes < 1:
            raise ValueError("fast_verify_arena_bounds")
        self.slots, self.slot_bytes = slots, slot_bytes
        self.index_bytes = _page(slots * INDEX_ENTRY.size)
        self.size = self.index_bytes + slots * slot_bytes
        self.block = shared_memory.SharedMemory(create=True, size=self.size)
        self.name = self.block.name
        buffer = self.block.buf
        assert buffer is not None
        self.buf = buffer
        self.condition = threading.Condition()
        self.free = [True] * slots
        self.in_use = self.peak = self.waits = 0
        self.closed = False

    def offset(self, first: int) -> int:
        return self.index_bytes + first * self.slot_bytes

    def _run(self, count: int) -> int | None:
        start = 0
        for slot, free in enumerate(self.free):
            if not free:
                start = slot + 1
            elif slot - start + 1 == count:
                return start
        return None

    def acquire(self, size: int, *, reserve: int = 0) -> tuple[int, int]:
        """(first slot, slot count) for `size` bytes (+ `reserve` bytes of reply room
        after them, 8-aligned); blocks until a free run fits. The index records `size`."""
        count = -(-(size + (-size % 8) + reserve) // self.slot_bytes) if size or reserve else 0
        if count > self.slots:
            raise FastVerifyError(f"fast_verify_arena_too_small size={size} reserve={reserve} "
                                  f"arena={self.slots * self.slot_bytes}")
        if count == 0:
            return 0, 0
        with self.condition:
            waited = False
            while (first := self._run(count)) is None:
                if self.closed:
                    raise FastVerifyError("fast_verify_pool_closed")
                waited = True
                self.condition.wait()
            self.waits += waited
            for slot in range(first, first + count):
                self.free[slot] = False
                INDEX_ENTRY.pack_into(self.buf, slot * INDEX_ENTRY.size, FULL,
                                      size if slot == first else 0)
            self.in_use += count
            self.peak = max(self.peak, self.in_use)
        return first, count

    def release(self, first: int, count: int) -> None:
        if count == 0:
            return
        with self.condition:
            for slot in range(first, first + count):
                INDEX_ENTRY.pack_into(self.buf, slot * INDEX_ENTRY.size, EMPTY, 0)
                self.free[slot] = True
            self.in_use -= count
            self.condition.notify_all()

    def close(self) -> None:
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        del self.buf
        self.block.close()
        self.block.unlink()


# -- worker process ------------------------------------------------------------------
def _attach_arena(name: str, size: int) -> tuple[Any, memoryview]:
    if sys.platform == "win32":
        block = shared_memory.SharedMemory(name=name)
        assert block.buf is not None
        return block, block.buf
    fd = _posixshmem.shm_open("/" + name, os.O_RDWR)
    try:
        mapping = mmap.mmap(fd, size)
    finally:
        os.close(fd)
    return mapping, memoryview(mapping)


def _read_into(name: str, size: int, target: memoryview) -> None:
    """`fast_source._read_shm` into `target`: same open, size check and errors."""
    if sys.platform == "win32":
        block = shared_memory.SharedMemory(name=name)
        try:
            if block.size < size:
                raise HandoffError("handoff_size")
            assert block.buf is not None
            target[:] = block.buf[:size]
        finally:
            block.close()
        return
    fd = _posixshmem.shm_open("/" + name, os.O_RDONLY)
    try:
        if os.fstat(fd).st_size < size:
            raise HandoffError("handoff_size")
        if size == 0:
            return
        if sys.platform == "darwin":
            with mmap.mmap(fd, size, prot=mmap.PROT_READ) as mapping:
                source = memoryview(mapping)
                try:
                    target[:] = source[:size]
                finally:
                    source.release()
            return
        done = 0
        while done < size:
            count = os.preadv(fd, [target[done:]], done)
            if not count:
                raise HandoffError("handoff_size")
            done += count
    finally:
        os.close(fd)


def _serve(view: memoryview, request: tuple[Any, ...]) -> tuple[str, Any]:
    kind, name, size, digest, offset, decode_headers, entries = request[:7]
    target = view[offset:offset + size]
    try:
        _read_into(name, size, target)
        if sha256(target) != digest:
            raise HandoffError("handoff_digest")
    except Exception as error:  # noqa: BLE001 - re-raised by the learner as is
        return "error", error
    if kind == "segment":
        try:
            header, _rows = decode_segment(target, header_only=True)
        except Exception as error:  # noqa: BLE001
            return "ok", VerifiedRead(header_error=error)
        return "ok", VerifiedRead(header=header, facts=row_facts(header))
    checks: list[tuple[str | None, dict[str, Any] | None, BaseException | None]] = []
    for entry in entries if isinstance(entries, list) else ():
        start = entry.get("offset") if isinstance(entry, dict) else None
        length = entry.get("size") if isinstance(entry, dict) else None
        if (type(start) is not int or type(length) is not int or start < 0 or length < 0
                or start + length > size):
            checks.append((None, None, None))
            continue
        body = target[start:start + length]
        parsed: dict[str, Any] | None = None
        refused: BaseException | None = None
        if decode_headers:
            try:
                parsed, _rows = decode_segment(body, header_only=True)
            except Exception as failure:  # noqa: BLE001
                refused = failure
        checks.append((sha256(body), parsed, refused))
    return "ok", VerifiedRead(entries=checks)


def _reply(view: memoryview, request: tuple[Any, ...], reply: tuple[str, Any]) -> Any:
    """The reply in the request's arena room (("arena", length)), or inline when it
    does not fit (the same pickle, only a longer pipe message)."""
    try:
        payload = pickle.dumps(reply, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as error:  # noqa: BLE001 - an unpicklable error is still loud
        return "inline", ("error", FastVerifyError(
            f"fast_verify_unpicklable_result {error!r}"[:400]))
    room, capacity = request[7], request[8]
    if len(payload) > capacity:
        return "inline", reply
    view[room:room + len(payload)] = payload
    return "arena", len(payload)


def _worker_main(arena_name: str, arena_size: int, channels: list[Connection]) -> None:
    handle, view = _attach_arena(arena_name, arena_size)
    live = list(channels)
    try:
        while live:
            for connection in wait(live):
                assert isinstance(connection, Connection)
                try:
                    request = connection.recv()
                except (EOFError, OSError):
                    live.remove(connection)
                    continue
                connection.send(_reply(view, request, _serve(view, request)))
    finally:
        view.release()
        handle.close()


# -- learner side --------------------------------------------------------------------
class FastVerifyPool:
    """N resident verify workers, a fixed set of request channels and the arena."""

    def __init__(self, *, workers: int, slots: int, slot_bytes: int) -> None:
        if type(workers) is not int or workers < 1:
            raise ValueError("fast_verify_workers_positive")
        self.arena = VerifyArena(slots=slots, slot_bytes=slot_bytes)
        context = get_context("spawn")
        self.processes: list[Any] = []
        self.connections: list[Connection] = []
        self.channels: queue.Queue[tuple[int, Connection]] = queue.Queue()
        self.lock = threading.Lock()
        self.requests = [0] * workers
        self.dead: set[int] = set()
        pairs = []
        try:
            for worker in range(workers):
                ends = [context.Pipe() for _ in range(CHANNELS_PER_WORKER)]
                process = context.Process(
                    target=_worker_main, name=f"rl-fast-verify-{worker}", daemon=True,
                    args=(self.arena.name, self.arena.size, [child for _, child in ends]))
                process.start()
                self.processes.append(process)
                for ours, child in ends:
                    child.close()  # only the worker holds it: its exit is an EOF here
                    self.connections.append(ours)
                pairs.append([ours for ours, _ in ends])
        except BaseException:
            self.close()
            raise
        # Interleaved, so consecutive requests go to different workers.
        for channel in range(CHANNELS_PER_WORKER):
            for worker in range(workers):
                self.channels.put((worker, pairs[worker][channel]))

    def _died(self, worker: int, error: BaseException) -> FastVerifyError:
        process = self.processes[worker]
        process.join(0.5)
        with self.lock:
            self.dead.add(worker)
        return FastVerifyError(f"fast_verify_worker_died worker={worker} pid={process.pid} "
                               f"exitcode={process.exitcode} error={type(error).__name__}")

    def verify(self, kind: str, message: dict[str, Any], *,
               decode_headers: bool) -> tuple[bytes, VerifiedRead]:
        """One checked segment / labels message (`receive_handoff` already ran)."""
        size = message["size"]
        entries = message.get("segments") if kind == "labels" else None
        reserve = REPLY_BYTES + (LABELS_HEADER_REPLY_BYTES * len(entries)
                                 if decode_headers and isinstance(entries, list) else 0)
        first, count = self.arena.acquire(size, reserve=reserve)
        offset = self.arena.offset(first)
        room = offset + size + (-size % 8)
        try:
            worker, connection = self.channels.get()
            healthy = False
            try:
                with self.lock:
                    self.requests[worker] += 1
                try:
                    connection.send((kind, message["shm_name"], size, message["sha256"],
                                     offset, decode_headers, entries, room, reserve))
                    where, reply = connection.recv()
                except (EOFError, OSError) as error:
                    raise self._died(worker, error) from error
                healthy = True
            finally:
                if healthy:
                    self.channels.put((worker, connection))
            status, value = (reply if where == "inline"
                             else pickle.loads(self.arena.buf[room:room + reply]))
            if status == "error":
                raise value
            data = bytes(self.arena.buf[offset:offset + size])
        finally:
            self.arena.release(first, count)
        return data, value

    def read_handoff(self, connection: Connection, *, decode_headers: bool
                     ) -> tuple[str, dict[str, Any], bytes | None, VerifiedRead | None]:
        """`read_handoff` with the shared memory read and checks in a worker."""
        kind, message = receive_handoff(connection)
        if kind in ("end", "closed"):
            return kind, message, None, None
        data, checked = self.verify(kind, message, decode_headers=decode_headers)
        return kind, message, data, checked

    def status(self) -> dict[str, Any]:
        with self.lock:
            requests, dead = list(self.requests), sorted(self.dead)
        with self.arena.condition:
            arena = {"slots": self.arena.slots, "slot_bytes": self.arena.slot_bytes,
                     "in_use": self.arena.in_use, "peak": self.arena.peak,
                     "waits": self.arena.waits}
        return {"workers": len(self.processes), "requests": requests, "dead": dead,
                "arena": arena}

    def close(self) -> None:
        for connection in self.connections:
            connection.close()  # workers see EOF on every channel and exit
        for process in self.processes:
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
        self.arena.close()
