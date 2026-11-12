"""Remote preparation host: the learner's own assembly worker, on another node.

The learner keeps admission, selection, RNG, labels and the hand-off. Only the
CPU work of one `_assemble` task moves: the learner sends the exact packet
bytes of each source the host does not hold yet, the host runs the same
`_decode_source` and `_assemble` functions as the local pools, and the
learner receives the exact `_write_batch` file bytes into its own hand-off
file. The SGD process checks that file's sha256 over `used_bytes` as for a
local batch, so a corrupted transfer fails there and is never trained on.

Transport is `multiprocessing.connection` with an HMAC authkey (hex in an
environment variable on both ends). Messages are pickles between two processes
of the same release and the same trusted job owner, as on the local spawn pools.
HMAC covers the connection handshake only, so hosts belong on a trusted cluster
network. A failed or timed-out task raises on the learner (no local fallback, no
retry); the broken connection is dropped and a new one is opened for a later task.

Bytes equal a local batch only for the same code and worker settings, which the
SGD sha256 check cannot see (it covers the transfer). The hello therefore
carries a digest of the preparation source (`stpd/rl`, `stpd/p1`) and the
learner's worker thread count; a host started for other values refuses it. The
host cache is keyed by packet sha256 and the exact envelope (it carries
learner-side fields such as `gae_lambda` and the training split).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import os
import queue
import socket
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Future, ProcessPoolExecutor
from multiprocessing import get_context
from multiprocessing.connection import (Client, Connection, Listener, answer_challenge,
                                        deliver_challenge)
from pathlib import Path
from typing import Any

from . import binary_preparation as binary

AUTH_ENV = "STPD_REMOTE_PREPARE_AUTH"
CHUNK_BYTES = 64 * 1024**2
PROTOCOL = "stpd/rl-remote-prepare-2"
HANDSHAKE_SECONDS = 60.0
_code_digest: str | None = None


def code_digest() -> str:
    """Path-scoped digest of the preparation source both ends run (cached)."""
    global _code_digest
    if _code_digest is None:
        root = Path(__file__).resolve().parents[1]  # python/stpd
        digest = hashlib.sha256()
        for path in sorted([*(root / "rl").rglob("*.py"), *(root / "p1").rglob("*.py")]):
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        _code_digest = digest.hexdigest()
    return _code_digest


def source_key(envelope: dict[str, Any]) -> str:
    """Cache key: the packet bytes and the exact envelope they are decoded under."""
    canonical = json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=repr)
    return f"{envelope['packet_sha256']}:{hashlib.sha256(canonical.encode()).hexdigest()}"


def _wait(connection: Connection, timeout: float) -> None:
    if not connection.poll(timeout):
        raise TimeoutError("remote_prepare_timeout")


def authkey(name: str = AUTH_ENV) -> bytes:
    value = os.environ.get(name, "")
    try:
        key = bytes.fromhex(value)
    except ValueError:
        key = b""
    if len(key) < 16:
        raise ValueError("remote_prepare_authkey_required")
    return key


def _address(endpoint: str) -> tuple[str, int]:
    host, _, port = endpoint.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError("remote_prepare_endpoint_must_be_host_port")
    return host, int(port)


# --------------------------------------------------------------------------- host


class _Host:
    """One node's decode cache and worker pools, shared by all learner connections."""

    def __init__(self, scratch: Path, decode_workers: int, assembly_workers: int,
                 cache_bytes: int) -> None:
        self.scratch, self.cache_bytes = scratch, cache_bytes
        self.decode_workers, self.assembly_workers = decode_workers, assembly_workers
        self.lock = threading.Lock()
        self.settings: tuple[Any, ...] | None = None
        self.decode: ProcessPoolExecutor | None = None
        self.assembly: ProcessPoolExecutor | None = None
        # packet sha256 -> (decode future, files, bytes); LRU order.
        self.sources: OrderedDict[str, tuple[Future, tuple[str, ...], int]] = OrderedDict()
        self.users: dict[str, int] = {}
        self.held = 0
        self.tasks = 0

    def start(self, vocabulary: dict[str, Any], threads: int, code: str) -> None:
        """Pools are made once, for the first learner's settings; others must match."""
        with self.lock:
            if code != code_digest():
                raise ValueError("remote_prepare_code_changed")
            if type(threads) is not int or threads < 1:
                raise ValueError("remote_prepare_threads_required")
            if self.settings is not None:
                if (vocabulary, threads) != self.settings:
                    raise ValueError("remote_prepare_worker_settings_changed")
                return
            context = get_context("spawn")
            self.decode = ProcessPoolExecutor(self.decode_workers, mp_context=context,
                                              initializer=binary._initialize,
                                              initargs=(vocabulary, threads))
            self.assembly = ProcessPoolExecutor(self.assembly_workers, mp_context=context,
                                                initializer=binary._initialize,
                                                initargs=(vocabulary, threads))
            self.settings = vocabulary, threads

    def reserve(self, digests: list[str]) -> list[str]:
        """Pin every source of a task; return the digests this connection must send.

        A digest another connection is still sending is waited on, not sent twice:
        its entry is a placeholder Future that `add` (or `abandon`) completes.
        """
        with self.lock:
            missing = []
            for digest in digests:
                self.users[digest] = self.users.get(digest, 0) + 1
                if digest in self.sources:
                    self.sources.move_to_end(digest)
                elif digest not in missing:
                    missing.append(digest)
                    self.sources[digest] = Future(), (), 0
            return missing

    def abandon(self, digests: list[str]) -> None:
        """Sources this connection reserved but never delivered fail their waiters."""
        with self.lock:
            for digest in digests:
                entry = self.sources.get(digest)
                if entry is not None and not entry[1] and not entry[0].done():
                    del self.sources[digest]
                    entry[0].set_exception(RuntimeError("remote_prepare_source_not_delivered"))

    def add(self, digest: str, envelope: dict[str, Any], raw: bytes) -> None:
        assert self.decode is not None
        if len(raw) != envelope.get("packet_size"):
            raise ValueError("remote_prepare_packet_size_changed")
        path = self.scratch / f"src-{digest[:32]}-{uuid.uuid4().hex[:8]}"
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            stream.write((1).to_bytes(8, "little"))
            stream.write(raw)
        descriptor = binary._Descriptor(str(path), 8, len(raw), 1, dict(envelope))
        files = (str(path), binary._derived_path(descriptor))
        with self.lock:
            placeholder = self.sources[digest][0]
            self.sources[digest] = placeholder, files, len(raw)
            self.held += len(raw)
        job = self.decode.submit(binary._decode_source, descriptor)

        def settle(done: Future) -> None:
            error = None if done.cancelled() else done.exception()
            if done.cancelled() or error is not None:
                # A failed decode is never cached: the next task sends it again.
                with self.lock:
                    if self.sources.get(digest, (None,))[0] is placeholder:
                        del self.sources[digest]
                        self.held -= len(raw)
                for name in files:
                    Path(name).unlink(missing_ok=True)
                if not placeholder.done():
                    placeholder.set_exception(error or RuntimeError("remote_prepare_cancelled"))
            elif not placeholder.done():  # an evicted entry was already failed
                placeholder.set_result(done.result())

        job.add_done_callback(settle)

    def ready(self, digests: list[str]) -> tuple[Any, ...]:
        with self.lock:
            jobs = [self.sources[digest][0] for digest in digests]
        return tuple(job.result() for job in jobs)

    def release(self, digests: list[str]) -> None:
        with self.lock:
            for digest in digests:
                self.users[digest] -= 1
                if not self.users[digest]:
                    del self.users[digest]
            # Raw bytes stand for the source's footprint (derived slabs scale with it).
            for digest in list(self.sources):
                if self.held <= self.cache_bytes:
                    break
                if digest in self.users:
                    continue
                job, files, size = self.sources.pop(digest)
                self.held -= size
                if not job.done():
                    job.set_exception(RuntimeError("remote_prepare_evicted"))
                for name in files:
                    Path(name).unlink(missing_ok=True)

    def run(self, ready: tuple[Any, ...], args: tuple[Any, ...]) -> tuple[Any, str]:
        assert self.assembly is not None
        request = args[0]
        output = self.scratch / ("out-" + uuid.uuid4().hex)
        with output.open("xb") as stream:
            os.chmod(output, 0o600)
            stream.truncate(request.max_output_bytes + 64)
        # Timing fields use this host's clock; only the queue/worker split differs.
        call = (*args[:5], time.perf_counter(), str(output), True)
        try:
            return self.assembly.submit(binary._timed, binary._assemble, ready, *call).result(), \
                str(output)
        except BaseException:
            output.unlink(missing_ok=True)
            raise


def _serve(host: _Host, connection: Connection, key: bytes | None = None) -> None:
    try:
        if key is not None:  # HMAC handshake here, off the accept loop
            deliver_challenge(connection, key)
            answer_challenge(connection, key)
        _wait(connection, HANDSHAKE_SECONDS)
        hello = connection.recv()
        if hello.get("protocol") != PROTOCOL:
            raise ValueError("remote_prepare_protocol_changed")
        timeout = float(hello["timeout"])
        try:
            host.start(hello["vocabulary"], hello["threads"], hello["code"])
        except ValueError as error:
            connection.send({"protocol": PROTOCOL, "error": str(error)})
            return
        connection.send({"protocol": PROTOCOL, "host": socket.gethostname(), "pid": os.getpid()})
        while True:
            # Between tasks this connection holds no pins or files, so the wait
            # for the learner's next task has no deadline; within a task it does.
            try:
                task = connection.recv()
            except EOFError:
                return
            envelopes = {source_key(envelope): envelope for envelope in task["sources"]}
            digests = [source_key(envelope) for envelope in task["sources"]]
            missing = host.reserve(digests)
            try:
                connection.send({"need": missing})
                delivered = 0
                try:
                    for digest in missing:
                        _wait(connection, timeout)
                        host.add(digest, envelopes[digest], connection.recv_bytes())
                        delivered += 1
                finally:
                    host.abandon(missing[delivered:])
                began = time.perf_counter()
                try:
                    ready = host.ready(digests)
                    # Worker seconds of the decodes this task delivered (diagnostic).
                    fresh = set(missing[:delivered])
                    decoded = sum(item.seconds for key, item in
                                  dict(zip(digests, ready, strict=True)).items() if key in fresh)
                    result, output = host.run(ready, task["args"])
                except BaseException as error:  # reported, then this connection ends
                    connection.send({"error": f"{type(error).__name__}: {error}"})
                    return
                try:
                    packed = result.value
                    used = packed.summary["used_bytes"]
                    connection.send({
                        "tree": packed.tree, "summary": packed.summary,
                        "arrays": [(a.offset, a.count, a.dtype) for a in packed.arrays],
                        "pid": result.pid, "wall": result.wall_seconds, "cpu": result.cpu_seconds,
                        "host_seconds": time.perf_counter() - began, "missing": len(missing),
                        "decode_seconds": decoded})
                    with open(output, "rb") as stream:
                        mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
                    try:
                        view = memoryview(mapping)
                        for start in range(0, used, CHUNK_BYTES):
                            connection.send_bytes(view[start : min(used, start + CHUNK_BYTES)])
                        view.release()
                    finally:
                        mapping.close()
                finally:
                    Path(output).unlink(missing_ok=True)
                host.tasks += 1
            finally:
                host.release(digests)
    except Exception as error:  # one client's failure never stops the host
        print(f"remote_prepare connection ended: {type(error).__name__}", flush=True)
    finally:
        connection.close()


def serve(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default="0.0.0.0:0")
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--decode-workers", type=int, required=True)
    parser.add_argument("--assembly-workers", type=int, required=True)
    parser.add_argument("--cache-bytes", type=int, default=8 * 1024**3)
    parser.add_argument("--ready-file", type=Path,
                        help="written atomically with host:port once listening")
    args = parser.parse_args(argv)
    args.scratch.mkdir(parents=True, exist_ok=True)
    host = _Host(args.scratch, args.decode_workers, args.assembly_workers, args.cache_bytes)
    key = authkey()
    listener = Listener(_address(args.listen))  # handshake runs per connection thread
    bound = listener.address
    endpoint = f"{socket.gethostname()}:{bound[1]}"
    if args.ready_file is not None:
        temporary = args.ready_file.with_suffix(".tmp")
        temporary.write_text(endpoint + "\n")
        os.replace(temporary, args.ready_file)
    print(f"remote_prepare listening {endpoint}", flush=True)
    while True:
        try:
            connection = listener.accept()
        except Exception as error:  # keep serving
            print(f"remote_prepare accept failed: {type(error).__name__}", flush=True)
            continue
        threading.Thread(target=_serve, args=(host, connection, key), daemon=True).start()


# --------------------------------------------------------------------------- learner


class RemoteAssembly:
    """Learner side: one task per connection at a time, `connections` per endpoint."""

    def __init__(self, *, endpoints: list[str], vocabulary: dict[str, Any], threads: int,
                 connections: int = 2, auth_env: str = AUTH_ENV,
                 timeout_seconds: float = 600.0) -> None:
        if not endpoints or type(connections) is not int or connections < 1:
            raise ValueError("remote_prepare_endpoints_required")
        if not 0 < timeout_seconds <= 3600:
            raise ValueError("remote_prepare_timeout_bound")
        self.vocabulary, self.key = vocabulary, authkey(auth_env)
        self.threads, self.timeout = threads, float(timeout_seconds)
        self.idle: queue.Queue[tuple[str, Connection | None]] = queue.Queue()
        self.closed = False
        for endpoint in endpoints:
            _address(endpoint)
            for _ in range(connections):
                self.idle.put((endpoint, None))
        self.stats = {"tasks": 0, "sent_bytes": 0, "received_bytes": 0, "sources_sent": 0}
        self.lock = threading.Lock()

    def _connect(self, endpoint: str) -> Connection:
        connection = Client(_address(endpoint), authkey=self.key)
        try:
            connection.send({"protocol": PROTOCOL, "vocabulary": self.vocabulary,
                             "threads": self.threads, "code": code_digest(),
                             "timeout": self.timeout})
            _wait(connection, HANDSHAKE_SECONDS)
            reply = connection.recv()
            if reply.get("protocol") != PROTOCOL:
                raise RuntimeError("remote_prepare_protocol_changed")
            if "error" in reply:
                raise RuntimeError(f"remote_prepare_refused: {reply['error']}")
        except BaseException:
            connection.close()
            raise
        return connection

    def run(self, ready: tuple[Any, ...], args: tuple[Any, ...], output: str) -> Any:
        """Same value as `_timed(_assemble, ready, *args, output, True)` on a local pool."""
        if self.closed:
            raise RuntimeError("remote_prepare_closed")
        endpoint, connection = self.idle.get()
        healthy = False
        try:
            if connection is None:
                connection = self._connect(endpoint)
            connection.send({"sources": [item.descriptor.envelope for item in ready],
                             "args": args[:5]})
            _wait(connection, self.timeout)
            need = set(connection.recv()["need"])
            sent = self._send_sources(connection, ready, set(need))
            _wait(connection, self.timeout)  # covers the host's decode + assembly
            reply = connection.recv()
            if "error" in reply:
                raise RuntimeError(f"binary_remote_assembly_failed: {reply['error']}")
            used = reply["summary"]["used_bytes"]
            with open(output, "r+b") as stream:
                mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_WRITE)
            try:
                if used > len(mapping):
                    raise ValueError("binary_prepared_output_byte_limit")
                cursor = 0
                while cursor < used:
                    _wait(connection, self.timeout)
                    cursor += connection.recv_bytes_into(mapping, cursor)
                if cursor != used:
                    raise RuntimeError("binary_remote_assembly_short_transfer")
            finally:
                mapping.close()
            healthy = True
            with self.lock:
                self.stats["tasks"] += 1
                self.stats["sent_bytes"] += sent
                self.stats["sources_sent"] += len(need)
                self.stats["received_bytes"] += used
            arrays = tuple(binary._SharedArray(output, offset, count, dtype)
                           for offset, count, dtype in reply["arrays"])
            summary = dict(reply["summary"])
            summary["timings"] = {**summary["timings"],
                                  "binary_remote_host_seconds": reply["host_seconds"],
                                  "binary_remote_sources_sent": float(len(need)),
                                  "binary_remote_assembly_cpu_seconds": float(reply["cpu"]),
                                  "binary_remote_decode_seconds": float(reply["decode_seconds"])}
            return binary._WorkerResult(binary._PackedBatch(reply["tree"], arrays, summary),
                                        reply["pid"], reply["wall"], reply["cpu"])
        finally:
            if (not healthy or self.closed) and connection is not None:
                connection.close()
            self.idle.put((endpoint, connection if healthy and not self.closed else None))

    @staticmethod
    def _send_sources(connection: Connection, ready: tuple[Any, ...], need: set[str]) -> int:
        handles: dict[str, mmap.mmap] = {}
        sent = 0
        try:
            for item in ready:
                d = item.descriptor
                digest = source_key(d.envelope)
                if digest not in need:
                    continue
                need.discard(digest)  # one copy per digest
                if d.name not in handles:
                    with open(d.name, "rb") as stream:
                        handles[d.name] = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
                mapping = handles[d.name]
                if int.from_bytes(mapping[:8], "little") != d.epoch:
                    raise ValueError("binary_shared_slot_epoch_changed")
                view = memoryview(mapping)[d.offset : d.offset + d.length]
                try:
                    connection.send_bytes(view)
                finally:
                    view.release()
                sent += d.length
            if need:
                raise RuntimeError("remote_prepare_unknown_source_requested")
        finally:
            for handle in handles.values():
                handle.close()
        return sent

    def close(self) -> None:
        self.closed = True
        while True:
            try:
                _endpoint, connection = self.idle.get_nowait()
            except queue.Empty:
                return
            if connection is not None:
                connection.close()


if __name__ == "__main__":
    serve()
