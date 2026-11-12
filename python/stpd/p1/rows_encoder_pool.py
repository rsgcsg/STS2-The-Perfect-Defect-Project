"""Per-node pool of RowsEncoder processes (P-REWRITE WP3, fast_chain only).

Each worker is a spawned process holding one cached `RowsEncoder` and one
handler (default: one observation's JSON bytes -> EncodedRow; the node
aggregator passes whole link messages so they are parsed once, in a worker). Lanes (one
per game instance, or per environment connection) stick to one worker, so the
adjacent-step content reuse lands in that worker's cache; a new lane goes to the
worker with the fewest live lanes. Workers are started with `spawn` (or
`forkserver`), never `fork`, so the parent may already hold torch or threads.

Failure is loud and final:
* a per-observation encode error (`BoundaryError`, `ValueError`, R7 reference
  mismatch) fails that row's future with the same type and code;
* tag 0 is never a request: it releases a lane on its worker (no reply);
* a worker that dies or answers out of order breaks the whole pool: every
  pending future fails with `encoder_worker_died` and later submits raise.
  Nothing is retried or re-encoded elsewhere, and there is no in-process
  fallback encoder.
"""
from __future__ import annotations

import contextlib
import importlib
import json
import multiprocessing
import os
import struct
import threading
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from multiprocessing.connection import Connection
from typing import Any, cast

from spireagent.json_boundary import BoundaryError

from .rows_encoder import DEFAULT_CACHE_BYTES, EncodedRow

_TAG = struct.Struct("<Q")
_REPLY = struct.Struct("<QI")
_META = ("row_sha256", "observation_sha256", "catalog_sha256", "catalog_len", "in_combat",
         "floor")


def encode_observation(encoder: Any, payload: memoryview) -> tuple[dict[str, Any], bytes]:
    """Default handler: the payload is one observation's JSON bytes."""
    encoded = encoder.encode(payload)
    return {name: getattr(encoded, name) for name in _META}, encoded.row


def _handler(path: str) -> Callable[[Any, memoryview], tuple[dict[str, Any], bytes]]:
    module, _, name = path.partition(":")
    return cast(Callable[[Any, memoryview], tuple[dict[str, Any], bytes]],
                getattr(importlib.import_module(module), name))


def _worker(connection: Connection, vocabulary: dict[str, Any], cache_bytes: int,
            verify_every: int, cpu: int | None, handler_path: str,
            release_path: str | None = None) -> None:
    if cpu is not None:
        if not hasattr(os, "sched_setaffinity"):  # Linux only; never silently unpinned
            raise BoundaryError("p1_rows_encoder", "cpu_affinity_unsupported_on_this_platform")
        os.sched_setaffinity(0, {cpu})
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    import torch

    torch.set_num_threads(1)
    from .rows_encoder import RowsEncoder

    encoder = RowsEncoder(vocabulary, cache_bytes=cache_bytes, verify_every=verify_every)
    handler = _handler(handler_path)
    release = None if release_path is None else cast(
        Callable[[Any, str], None],
        getattr(importlib.import_module(release_path.partition(":")[0]),
                release_path.partition(":")[2]))
    connection.send_bytes(json.dumps(encoder.identity).encode())
    while True:
        try:
            message = connection.recv_bytes()
        except EOFError:
            return
        if not message:  # shutdown
            return
        (tag,) = _TAG.unpack_from(message)
        if tag == 0:  # release a lane (no reply): the payload is its UTF-8 name
            if release is not None:
                release(encoder, bytes(message[_TAG.size:]).decode())
            continue
        if len(message) == _TAG.size:  # stats request
            body = json.dumps({"stats": encoder.cache_stats}).encode()
            connection.send_bytes(_REPLY.pack(tag, len(body)) + body)
            continue
        try:
            meta, row = handler(encoder, memoryview(message)[_TAG.size:])
        except ValueError as error:  # BoundaryError is a ValueError
            boundary = isinstance(error, BoundaryError)
            failure = {"error": "BoundaryError" if boundary else "ValueError",
                       "stage": getattr(error, "stage", None),
                       "code": getattr(error, "code", None), "message": str(error)}
            body = json.dumps(failure).encode()
            connection.send_bytes(_REPLY.pack(tag, len(body)) + body)
            continue
        body = json.dumps(meta).encode()
        connection.send_bytes(_REPLY.pack(tag, len(body)) + body + row)


class _Worker:
    def __init__(self, process: Any, connection: Connection) -> None:
        self.process, self.connection = process, connection
        self.pending: deque[tuple[int, Future[Any]]] = deque()
        self.send_lock = threading.Lock()  # send order == pending order
        self.lock = threading.Lock()       # pending only; never held while blocking
        self.lanes = 0
        self.reader: threading.Thread | None = None


class EncoderPool:
    """Encode raw observation JSON on worker processes; one future per row."""

    def __init__(self, vocabulary: dict[str, Any], *, workers: int,
                 cache_bytes: int = DEFAULT_CACHE_BYTES, verify_every: int = 0,
                 cpus: Sequence[int] | None = None, start_method: str = "spawn",
                 handler: str = "stpd.p1.rows_encoder_pool:encode_observation",
                 release_handler: str | None = None) -> None:
        if workers < 1:
            raise ValueError("encoder_pool_requires_workers")
        if start_method not in {"spawn", "forkserver"}:
            raise ValueError("encoder_pool_start_method")
        if cpus is not None and len(cpus) != workers:
            raise ValueError("encoder_pool_cpus_per_worker")
        context: Any = multiprocessing.get_context(start_method)
        self._default = handler == "stpd.p1.rows_encoder_pool:encode_observation"
        self._release = release_handler is not None
        self._workers: list[_Worker] = []
        self._lanes: dict[Any, _Worker] = {}
        self._lock = threading.Lock()
        self._tag = 0
        self._broken: BaseException | None = None
        self._closing = False
        identities = []
        for index in range(workers):
            parent, child = context.Pipe(duplex=True)
            process = context.Process(
                target=_worker, name=f"rows-encoder-{index}", daemon=True,
                args=(child, vocabulary, cache_bytes, verify_every,
                      None if cpus is None else cpus[index], handler, release_handler))
            process.start()
            child.close()
            worker = _Worker(process, parent)
            self._workers.append(worker)
            try:
                identities.append(json.loads(parent.recv_bytes()))
            except (EOFError, OSError) as error:
                self.close()
                raise BoundaryError("p1_rows_encoder", "encoder_worker_start_failed") from error
        if any(identity != identities[0] for identity in identities):
            self.close()
            raise BoundaryError("p1_rows_encoder", "encoder_worker_identity_differs")
        self.identity: dict[str, Any] = identities[0]
        for worker in self._workers:
            worker.reader = threading.Thread(target=self._read, args=(worker,),
                                             name="rows-encoder-reader", daemon=True)
            worker.reader.start()

    # -- lanes -------------------------------------------------------------
    def _worker_for(self, lane: Any) -> _Worker:
        worker = self._lanes.get(lane)
        if worker is None:
            worker = min(self._workers, key=lambda w: w.lanes)
            worker.lanes += 1
            self._lanes[lane] = worker
        return worker

    def release(self, lane: Any) -> None:
        """Forget a finished lane; its cache content stays until evicted.

        With a `release_handler` the lane's worker also runs it (handler(encoder, lane)),
        after every row already submitted for the lane (one pipe, send order).
        """
        with self._lock:
            worker = self._lanes.pop(lane, None)
            if worker is not None:
                worker.lanes -= 1
        if worker is not None and self._release and isinstance(lane, str):
            with worker.send_lock, contextlib.suppress(OSError, ValueError):
                # A dead worker breaks the pool anyway (its reader fails every pending row).
                worker.connection.send_bytes(_TAG.pack(0) + lane.encode())

    # -- requests ----------------------------------------------------------
    def submit(self, lane: Any, raw: bytes) -> Future[Any]:
        """Run the handler on the lane's worker, in submission order per worker.

        With the default handler `raw` is one observation's JSON bytes and the
        future holds an EncodedRow; otherwise it holds the handler's (meta, row).
        """
        with self._lock:
            if self._broken is not None:
                raise BoundaryError("p1_rows_encoder", "encoder_worker_died") from self._broken
            if self._closing:
                raise BoundaryError("p1_rows_encoder", "encoder_pool_closed")
            worker = self._worker_for(lane)
            self._tag += 1
            tag = self._tag
        return self._send(worker, tag, _TAG.pack(tag) + raw)

    def _send(self, worker: _Worker, tag: int, message: bytes) -> Future[Any]:
        future: Future[Any] = Future()
        with worker.send_lock:
            with worker.lock:
                # Checked under worker.lock after the append (#429 review S6):
                # `_break` sets `_broken` before it drains the pending lists, so
                # either that drain sees this future or this check sees the break.
                broken = self._broken
                if broken is None:
                    worker.pending.append((tag, future))
            if broken is not None:
                exception = BoundaryError("p1_rows_encoder", "encoder_worker_died")
                exception.__cause__ = broken
                future.set_exception(exception)
                return future
            try:
                worker.connection.send_bytes(message)
            except (OSError, ValueError) as error:
                self._break(error)
        return future

    def stats(self) -> list[dict[str, int]]:
        """Each worker's cache counters (hits, misses, units, bytes, texts)."""
        with self._lock:
            if self._broken is not None:
                raise BoundaryError("p1_rows_encoder", "encoder_worker_died") from self._broken
            self._tag += 1
            tag = self._tag
        futures = [self._send(worker, tag, _TAG.pack(tag)) for worker in self._workers]
        return [future.result()["stats"] for future in futures]

    def _read(self, worker: _Worker) -> None:
        while True:
            try:
                message = worker.connection.recv_bytes()
            except (EOFError, OSError) as error:
                if not self._closing:
                    self._break(error)
                return
            tag, length = _REPLY.unpack_from(message)
            with worker.lock:
                expected, future = worker.pending.popleft() if worker.pending else (None, None)
            if future is None or expected != tag:
                self._break(RuntimeError("encoder_reply_out_of_order"))
                return
            meta = json.loads(message[_REPLY.size:_REPLY.size + length])
            if "stats" in meta:
                future.set_result(meta)
            elif "error" in meta:
                if meta["error"] == "BoundaryError":
                    future.set_exception(BoundaryError(meta["stage"], meta["code"]))
                else:
                    future.set_exception(ValueError(meta["message"]))
            elif self._default:
                future.set_result(EncodedRow(row=message[_REPLY.size + length:], **meta))
            else:
                future.set_result((meta, message[_REPLY.size + length:]))

    def _break(self, error: BaseException) -> None:
        with self._lock:
            if self._broken is None:
                self._broken = error
        self._fail_pending("encoder_worker_died", error)

    def _fail_pending(self, code: str, cause: BaseException | None) -> None:
        for worker in self._workers:
            with worker.lock:
                pending, worker.pending = list(worker.pending), deque()
            for _, future in pending:
                if not future.done():
                    exception = BoundaryError("p1_rows_encoder", code)
                    exception.__cause__ = cause
                    future.set_exception(exception)

    @property
    def broken(self) -> bool:
        return self._broken is not None

    def close(self) -> None:
        """Stop the workers; rows still pending fail with `encoder_pool_closed`."""
        with self._lock:
            self._closing = True
        for worker in self._workers:
            with worker.send_lock, contextlib.suppress(OSError, ValueError):
                worker.connection.send_bytes(b"")  # a dead worker needs no shutdown
        for worker in self._workers:
            worker.process.join(5)
            if worker.process.is_alive():
                worker.process.kill()
                worker.process.join()
            if worker.reader is not None:
                worker.reader.join(5)
            worker.connection.close()
        self._fail_pending("encoder_pool_closed", None)

    def __enter__(self) -> EncoderPool:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
