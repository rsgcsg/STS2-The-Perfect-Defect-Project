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

Bytes equal a local batch only for the same code and worker settings, and only
on the same CPU class with the same torch/numpy versions (across CPU classes,
float features may differ at ulp level; integers, structure and labels do not).
The SGD sha256 check cannot see any of this (it covers the transfer). The hello therefore
carries a digest of the preparation source (`stpd/rl`, `stpd/p1`) and the
learner's worker thread count; a host started for other values refuses it. The
host cache is keyed by packet sha256 and the exact envelope (it carries
learner-side fields such as `gae_lambda` and the training split).

Wire codec (`codec`, both ends must agree, checked in the hello): `none` sends
the prepared bytes as they are; `lz4` (optional dependency `lz4`) sends them as
length-prefixed lz4 frames of at most 4 MiB each. The hand-off file always holds
the decompressed bytes, so the SGD sha256 check is unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import io
import json
import mmap
import os
import pickle
import queue
import socket
import struct
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from contextlib import contextmanager, suppress
from multiprocessing import get_context
from multiprocessing.connection import (
    Client,
    Connection,
    Listener,
    answer_challenge,
    deliver_challenge,
)
from multiprocessing.reduction import ForkingPickler
from pathlib import Path
from typing import Any

from . import binary_preparation as binary
from .prepared_batch import AuxiliaryInput

AUTH_ENV = "STPD_REMOTE_PREPARE_AUTH"
CHUNK_BYTES = 64 * 1024**2
PROTOCOL = "stpd/rl-remote-prepare-4"
CODECS = ("none", "lz4")
LZ4_CHUNK_BYTES = 4 * 1024**2


def _lz4() -> Any:
    try:  # optional dependency, only for codec "lz4"
        return importlib.import_module("lz4.frame")
    except ImportError as error:
        raise ValueError("remote_prepare_lz4_unavailable") from error


def _codec(name: Any) -> str:
    if name not in CODECS:
        raise ValueError("remote_prepare_codec_unknown")
    if name == "lz4":
        _lz4()
    return str(name)


def _recv_exact(raw: socket.socket, view: memoryview) -> None:
    """`raw` carries the task deadline (`_deadline_socket`)."""
    cursor = 0
    while cursor < len(view):
        count = raw.recv_into(view[cursor:], min(len(view) - cursor, CHUNK_BYTES))
        if count == 0:
            raise RuntimeError("binary_remote_assembly_short_transfer")
        cursor += count


def _send_output(raw: socket.socket, view: memoryview, codec: str) -> None:
    """Unframed for `none`; for `lz4`, 8-byte length + one lz4 frame per chunk."""
    if codec == "none":
        raw.sendall(view)
        return
    frame = _lz4()
    for start in range(0, len(view), LZ4_CHUNK_BYTES):
        block = frame.compress(view[start : start + LZ4_CHUNK_BYTES])
        raw.sendall(len(block).to_bytes(8, "little"))
        raw.sendall(block)


def _receive_output(raw: socket.socket, view: memoryview, codec: str) -> int:
    """Fill `view` with the decompressed bytes; return the bytes read off the wire."""
    if codec == "none":
        _recv_exact(raw, view)
        return len(view)
    frame = _lz4()
    header = bytearray(8)
    buffer = bytearray()
    wire = 0
    for start in range(0, len(view), LZ4_CHUNK_BYTES):
        _recv_exact(raw, memoryview(header))
        size = int.from_bytes(header, "little")
        if not 0 < size <= 2 * LZ4_CHUNK_BYTES:
            raise RuntimeError("binary_remote_lz4_frame_size")
        if len(buffer) < size:
            buffer = bytearray(size)
        _recv_exact(raw, memoryview(buffer)[:size])
        block = frame.decompress(memoryview(buffer)[:size])
        end = min(len(view), start + LZ4_CHUNK_BYTES)
        if len(block) != end - start:
            raise RuntimeError("binary_remote_lz4_chunk_length")
        view[start:end] = block
        wire += 8 + size
    return wire
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


def cpu_fingerprint() -> dict[str, str]:
    """CPU vendor/model and torch's CPU kernel level, exchanged in the hello.

    Assembly is bitwise reproducible on one CPU class but not across classes:
    the advantage normalization's float32 reductions differ at ulp level with
    the math library's dispatch (measured on fc430, c8220, r640 and both GPU
    nodes: every integer, mask, label, index and structure field stays equal).
    Verification compares bytes when fingerprints match, else per array within
    float tolerance. This value never gates a task.
    """
    import platform

    import torch

    info = {"vendor": "", "model": platform.processor() or platform.machine()}
    with suppress(OSError):
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            key, _, value = line.partition(":")
            if key.strip() == "vendor_id" and not info["vendor"]:
                info["vendor"] = value.strip()
            elif key.strip() == "model name":
                info["model"] = value.strip()
                break
    info["torch_capability"] = str(torch.backends.cpu.get_cpu_capability())
    return info


@contextmanager
def _deadline_socket(connection: Connection, timeout: float) -> Iterator[socket.socket]:
    """A raw view of the connection's socket whose sends and receives time out.

    The timeout puts the shared file description in non-blocking mode, so it is
    restored before the framed Connection is used again."""
    raw = socket.socket(fileno=os.dup(connection.fileno()))
    try:
        raw.settimeout(timeout)
        yield raw
    except TimeoutError as error:
        raise TimeoutError("remote_prepare_timeout") from error
    finally:
        with suppress(OSError):
            raw.settimeout(None)
        raw.close()


def _send_frame(raw: socket.socket, body: bytes) -> None:
    """`Connection.send_bytes` framing (big-endian length), sent with a deadline."""
    if len(body) > 0x7FFFFFFF:
        raise ValueError("remote_prepare_frame_too_large")
    raw.sendall(struct.pack("!i", len(body)))
    raw.sendall(body)


def _send(connection: Connection, timeout: float, message: Any) -> None:
    """`Connection.send` with a deadline (same pickler, same framing)."""
    body = ForkingPickler.dumps(message)
    with _deadline_socket(connection, timeout) as raw:
        _send_frame(raw, bytes(body))


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
                 cache_bytes: int, codec: str = "none") -> None:
        self.scratch, self.cache_bytes, self.codec = scratch, cache_bytes, _codec(codec)
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
        descriptor = binary._Descriptor(str(path), 8, len(raw), 1, dict(envelope))
        files = (str(path), binary._derived_path(descriptor))
        with self.lock:
            placeholder = self.sources[digest][0]
        try:
            with path.open("xb") as stream:
                os.chmod(path, 0o600)
                stream.write((1).to_bytes(8, "little"))
                stream.write(raw)
            with self.lock:
                self.sources[digest] = placeholder, files, len(raw)
                self.held += len(raw)
            job = self.decode.submit(binary._decode_source, descriptor)
        except BaseException as error:
            # Never leave a file behind or a waiter on a placeholder nobody settles.
            with self.lock:
                if self.sources.get(digest, (None,))[0] is placeholder:
                    if self.sources[digest][1]:
                        self.held -= len(raw)
                    del self.sources[digest]
            for name in files:
                Path(name).unlink(missing_ok=True)
            if not placeholder.done():
                placeholder.set_exception(error)
            raise

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

    def labels(self, ready: tuple[Any, ...], args: tuple[Any, ...]) -> Any:
        assert self.assembly is not None
        return self.assembly.submit(binary._timed, binary._labels, ready, *args).result()

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


def _pack_labels(value: Any, members: tuple[Any, ...], indices: tuple[int, ...]) -> bytes:
    """`_labels` row snapshots carry whole packet bodies the learner already holds:
    pickle each AuxiliaryInput as (ready index, row ref, member) instead."""
    by_segment = {m.segment_id: i for m, i in zip(members, indices, strict=True)}
    stream = io.BytesIO()

    class Pickler(pickle.Pickler):
        def persistent_id(self, item: Any) -> Any:
            if type(item) is AuxiliaryInput:
                return (by_segment[item.member.segment_id], item.source, item.member)
            return None

    Pickler(stream, protocol=5).dump(value)
    return stream.getvalue()


def _unpack_labels(data: bytes, ready: tuple[Any, ...]) -> Any:
    """Rebuild each AuxiliaryInput with the body read from the learner's own arena."""
    bodies: dict[int, bytes] = {}

    def body(index: int) -> bytes:
        if index not in bodies:
            d = ready[index].descriptor
            with open(d.name, "rb") as stream:
                stream.seek(d.offset)
                bodies[index] = stream.read(d.length)
        return bodies[index]

    class Unpickler(pickle.Unpickler):
        def persistent_load(self, token: Any) -> Any:
            index, source, member = token
            if type(index) is not int or not 0 <= index < len(ready):
                raise ValueError("binary_remote_labels_index_out_of_range")
            return AuxiliaryInput(source, member, body(index), ready[index].descriptor.envelope)

    return Unpickler(io.BytesIO(data)).load()


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
            if hello.get("codec", "none") != host.codec:
                raise ValueError("remote_prepare_codec_changed")
            host.start(hello["vocabulary"], hello["threads"], hello["code"])
        except ValueError as error:
            connection.send({"protocol": PROTOCOL, "error": str(error)})
            return
        connection.send({"protocol": PROTOCOL, "host": socket.gethostname(), "pid": os.getpid(),
                         "cpu": cpu_fingerprint(), "codec": host.codec})
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
                _send(connection, timeout, {"need": missing})
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
                    if task.get("kind") == "labels":
                        labelled = host.labels(ready, task["args"])
                        value = _pack_labels(labelled.value, task["args"][1], task["args"][2])
                    else:
                        result, output = host.run(ready, task["args"])
                except BaseException as error:  # reported, then this connection ends
                    _send(connection, timeout, {"error": f"{type(error).__name__}: {error}"})
                    return
                if task.get("kind") == "labels":
                    _send(connection, timeout, {
                        "value": value,
                        "pid": labelled.pid, "wall": labelled.wall_seconds,
                        "cpu": labelled.cpu_seconds, "decode_seconds": decoded,
                        "host_seconds": time.perf_counter() - began})
                    host.tasks += 1
                    continue
                try:
                    packed = result.value
                    used = packed.summary["used_bytes"]
                    _send(connection, timeout, {
                        "tree": packed.tree, "summary": packed.summary,
                        "arrays": [(a.offset, a.count, a.dtype) for a in packed.arrays],
                        "pid": result.pid, "wall": result.wall_seconds, "cpu": result.cpu_seconds,
                        "host_seconds": time.perf_counter() - began, "missing": len(missing),
                        "decode_seconds": decoded})
                    with open(output, "rb") as stream:
                        mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
                    try:
                        # After the framed reply: exactly `used` bytes, encoded by the codec.
                        with memoryview(mapping) as view, \
                                _deadline_socket(connection, timeout) as raw:
                            body = view[:used]
                            try:
                                _send_output(raw, body, host.codec)
                            finally:  # a traceback must not keep an export of the mmap
                                body.release()
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
    parser.add_argument("--codec", choices=CODECS, default="none")
    parser.add_argument("--ready-file", type=Path,
                        help="written atomically with host:port once listening")
    args = parser.parse_args(argv)
    args.scratch.mkdir(parents=True, exist_ok=True)
    host = _Host(args.scratch, args.decode_workers, args.assembly_workers, args.cache_bytes,
                 args.codec)
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


def connection_slots(endpoints: list[str], connections: int) -> list[str]:
    """One entry per connection, interleaved: the first tasks spread over all hosts."""
    return [endpoint for _ in range(connections) for endpoint in endpoints]


class RemoteAssembly:
    """Learner side: one task per connection at a time, `connections` per endpoint."""

    def __init__(self, *, endpoints: list[str], vocabulary: dict[str, Any], threads: int,
                 connections: int = 2, auth_env: str = AUTH_ENV,
                 timeout_seconds: float = 600.0, codec: str = "none",
                 slots: list[str] | None = None) -> None:
        if not endpoints or type(connections) is not int or connections < 1:
            raise ValueError("remote_prepare_endpoints_required")
        if not 0 < timeout_seconds <= 3600:
            raise ValueError("remote_prepare_timeout_bound")
        self.vocabulary, self.key = vocabulary, authkey(auth_env)
        self.threads, self.timeout = threads, float(timeout_seconds)
        self.codec = _codec(codec)
        self.idle: queue.Queue[tuple[str, Connection | None]] = queue.Queue()
        self.closed = False
        for endpoint in endpoints:
            _address(endpoint)
        # `slots` (client processes only): this process's share of the endpoint
        # connections, in the order `connection_slots` gives.
        for endpoint in slots if slots is not None else connection_slots(endpoints, connections):
            self.idle.put((endpoint, None))
        self.stats = {"tasks": 0, "label_tasks": 0, "sent_bytes": 0, "received_bytes": 0,
                      "wire_received_bytes": 0, "sources_sent": 0}
        self.cpu = cpu_fingerprint()
        self.host_cpus: dict[str, dict[str, str]] = {}  # endpoint -> host fingerprint
        self.served: OrderedDict[str, str] = OrderedDict()  # output path -> endpoint (last 256)
        self.lock = threading.Lock()

    def _connect(self, endpoint: str) -> Connection:
        connection = Client(_address(endpoint), authkey=self.key)
        try:
            connection.send({"protocol": PROTOCOL, "vocabulary": self.vocabulary,
                             "threads": self.threads, "code": code_digest(),
                             "timeout": self.timeout, "cpu": self.cpu, "codec": self.codec})
            _wait(connection, HANDSHAKE_SECONDS)
            reply = connection.recv()
            if reply.get("protocol") != PROTOCOL:
                raise RuntimeError("remote_prepare_protocol_changed")
            if "error" in reply:
                raise RuntimeError(f"remote_prepare_refused: {reply['error']}")
            if reply.get("codec") != self.codec:
                raise RuntimeError("remote_prepare_codec_changed")
            with self.lock:
                self.host_cpus[endpoint] = dict(reply.get("cpu") or {})
        except BaseException:
            connection.close()
            raise
        return connection

    def run(self, ready: tuple[Any, ...], args: tuple[Any, ...], output: str) -> Any:
        """Same value as `_timed(_assemble, ready, *args, output, True)` on a local pool."""
        if self.closed:
            raise RuntimeError("remote_prepare_closed")
        queued = time.perf_counter()
        endpoint, connection = self.idle.get()
        healthy = False
        try:
            began = time.perf_counter()
            connection, reply, need, sent = self._exchange(
                endpoint, connection, ready, {"args": args[:5]}, "binary_remote_assembly_failed")
            replied = time.perf_counter()
            used = reply["summary"]["used_bytes"]
            with open(output, "r+b") as stream:
                mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_WRITE)
            try:
                if used > len(mapping):
                    raise ValueError("binary_prepared_output_byte_limit")
                # Raw bytes after the framed reply, straight into the hand-off
                # file: no intermediate buffers, and recv_into releases the GIL.
                with memoryview(mapping) as view, \
                        _deadline_socket(connection, self.timeout) as raw:
                    target = view[:used]
                    try:
                        wire = _receive_output(raw, target, self.codec)
                    finally:  # a traceback must not keep an export of the mmap
                        target.release()
            finally:
                mapping.close()
            healthy = True
            self._account({"tasks": 1, "sent_bytes": sent, "sources_sent": len(need),
                           "received_bytes": used, "wire_received_bytes": wire},
                          endpoint, output)
            arrays = tuple(binary._SharedArray(output, offset, count, dtype)
                           for offset, count, dtype in reply["arrays"])
            summary = dict(reply["summary"])
            summary["timings"] = {**summary["timings"],
                                  "binary_remote_host_seconds": reply["host_seconds"],
                                  "binary_remote_sources_sent": float(len(need)),
                                  "binary_remote_assembly_cpu_seconds": float(reply["cpu"]),
                                  "binary_remote_decode_seconds": float(reply["decode_seconds"]),
                                  "binary_remote_wait_seconds": began - queued,
                                  "binary_remote_exchange_seconds": replied - began,
                                  "binary_remote_transfer_seconds":
                                      time.perf_counter() - replied}
            return binary._WorkerResult(binary._PackedBatch(reply["tree"], arrays, summary),
                                        reply["pid"], reply["wall"], reply["cpu"])
        finally:
            if (not healthy or self.closed) and connection is not None:
                connection.close()
            self.idle.put((endpoint, connection if healthy and not self.closed else None))

    def _exchange(self, endpoint: str, connection: Connection | None, ready: tuple[Any, ...],
                  task: dict[str, Any], failure: str) -> tuple[Connection, Any, set[str], int]:
        """Send the task, the sources the host lacks, and wait for its reply."""
        if connection is None:
            connection = self._connect(endpoint)
        _send(connection, self.timeout,
              {**task, "sources": [item.descriptor.envelope for item in ready]})
        _wait(connection, self.timeout)
        need = set(connection.recv()["need"])
        sent = self._send_sources(connection, ready, set(need), self.timeout)
        _wait(connection, self.timeout)  # covers the host's decode + work
        reply = connection.recv()
        if "error" in reply:
            raise RuntimeError(f"{failure}: {reply['error']}")
        return connection, reply, need, sent

    def _account(self, delta: dict[str, int], endpoint: str, output: str | None) -> None:
        with self.lock:
            if output is not None:
                self.served[output] = endpoint
                while len(self.served) > 256:
                    self.served.popitem(last=False)
            for name, value in delta.items():
                self.stats[name] += value

    def labels(self, ready: tuple[Any, ...], args: tuple[Any, ...]) -> Any:
        """Same value as `_timed(_labels, ready, *args)` on a local pool. Row
        snapshots get their packet bodies from this process's own arena."""
        reply = self._labels_reply(ready, args)
        return binary._WorkerResult(_unpack_labels(reply["value"], ready), reply["pid"],
                                    reply["wall"], reply["cpu"])

    def _labels_reply(self, ready: tuple[Any, ...], args: tuple[Any, ...]) -> Any:
        if self.closed:
            raise RuntimeError("remote_prepare_closed")
        endpoint, connection = self.idle.get()
        healthy = False
        try:
            connection, reply, need, sent = self._exchange(
                endpoint, connection, ready, {"kind": "labels", "args": args},
                "binary_remote_labels_failed")
            if type(reply.get("value")) is not bytes:
                raise RuntimeError("binary_remote_labels_failed: value_missing")
            healthy = True
            self._account({"label_tasks": 1, "sent_bytes": sent, "sources_sent": len(need),
                           "received_bytes": len(reply["value"])}, endpoint, None)
            return reply
        finally:
            if (not healthy or self.closed) and connection is not None:
                connection.close()
            self.idle.put((endpoint, connection if healthy and not self.closed else None))

    @staticmethod
    def _send_sources(connection: Connection, ready: tuple[Any, ...], need: set[str],
                      timeout: float) -> int:
        # One file open at a time per task: concurrent tasks x arenas must not
        # exhaust descriptors. The copy is small next to the network send.
        sent = 0
        for item in ready:
            d = item.descriptor
            digest = source_key(d.envelope)
            if digest not in need:
                continue
            need.discard(digest)  # one copy per digest
            fd = os.open(d.name, os.O_RDONLY)
            try:
                if int.from_bytes(os.pread(fd, 8, 0), "little") != d.epoch:
                    raise ValueError("binary_shared_slot_epoch_changed")
                body = os.pread(fd, d.length, d.offset)
            finally:
                os.close(fd)
            if len(body) != d.length:
                raise ValueError("binary_shared_slot_short_read")
            with _deadline_socket(connection, timeout) as raw:
                _send_frame(raw, body)
            sent += d.length
        if need:
            raise RuntimeError("remote_prepare_unknown_source_requested")
        return sent

    def served_by(self, output: str) -> dict[str, str]:
        """CPU fingerprint of the host that wrote a recent output (verification)."""
        with self.lock:
            return dict(self.host_cpus.get(self.served.get(output, ""), {}))

    def close(self) -> None:
        self.closed = True
        while True:
            try:
                _endpoint, connection = self.idle.get_nowait()
            except queue.Empty:
                return
            if connection is not None:
                connection.close()


# ------------------------------------------------------------ learner client processes
#
# Opt-in (`remote_prepare.client_processes: N`): the network and byte work of
# `RemoteAssembly.run`/`labels` (reading arena bytes, sending sources, receiving
# the batch into the hand-off file) runs in N spawned processes, each holding its
# share of the same connection slots. The learner sends each task's source
# descriptors (arena file path, offset, length, epoch, envelope), args and output
# path over a pipe and gets back the reply's small fields; its coordinator thread
# then only waits. Same task, same host protocol, same bytes and errors: a task
# is never re-sent or re-routed; a client process that exits fails its in-flight
# tasks with `remote_client_process_died` and is started again for a later task.

DIED = "remote_client_process_died"


class _ClientAssembly(RemoteAssembly):
    """Inside a client process: per-task stat deltas go back to the learner."""

    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        self.local = threading.local()

    def _account(self, delta: dict[str, int], endpoint: str, output: str | None) -> None:
        self.local.account = delta, endpoint


def _portable(error: BaseException) -> BaseException:
    """The original exception if it survives a pickle round trip, else its text."""
    try:
        clone = pickle.loads(pickle.dumps(error))
        if type(clone) is type(error) and str(clone) == str(error):
            return clone
    except Exception:
        pass
    return RuntimeError(f"{type(error).__name__}: {error}")


def _client_task(remote: _ClientAssembly, kind: str, ready: tuple[Any, ...],
                 args: tuple[Any, ...], output: str | None) -> dict[str, Any]:
    remote.local.account = None
    if kind == "run":
        result = remote.run(ready, args, str(output))
        packed = result.value
        body: dict[str, Any] = {
            "tree": packed.tree, "summary": packed.summary,
            "arrays": [(a.offset, a.count, a.dtype) for a in packed.arrays]}
    else:
        reply = remote._labels_reply(ready, args)
        body = {"value": reply["value"]}
        result = binary._WorkerResult(None, reply["pid"], reply["wall"], reply["cpu"])
    delta, endpoint = remote.local.account
    with remote.lock:
        host_cpu = dict(remote.host_cpus.get(endpoint, {}))
    body.update(pid=result.pid, wall=result.wall_seconds, cpu=result.cpu_seconds,
                delta=delta, endpoint=endpoint, host_cpu=host_cpu, client_pid=os.getpid())
    return body


def _client_main(connection: Connection, options: dict[str, Any]) -> None:
    """Client process: run tasks from the learner, one thread per connection slot."""
    from concurrent.futures import ThreadPoolExecutor

    remote = _ClientAssembly(**options)
    send_lock = threading.Lock()
    pool = ThreadPoolExecutor(len(options["slots"]), thread_name_prefix="rl-remote-client")

    def handle(task_id: int, kind: str, ready: tuple[Any, ...], args: tuple[Any, ...],
               output: str | None) -> None:
        try:
            message: tuple[Any, ...] = (task_id, True, _client_task(remote, kind, ready, args,
                                                                  output))
        except BaseException as error:  # reported to the learner as it was raised
            message = (task_id, False, _portable(error))
        with send_lock:
            try:
                connection.send(message)
            except BaseException as error:  # e.g. an unpicklable reply: report that
                try:
                    connection.send((task_id, False, _portable(error)))
                except BaseException:
                    # The learner must never wait on an answer that cannot come:
                    # exit, and its reader fails every in-flight task as died.
                    os._exit(70)

    try:
        while True:
            try:
                task = connection.recv()
            except (EOFError, OSError):
                return
            if task is None:
                return
            pool.submit(handle, *task)
    finally:
        remote.close()
        pool.shutdown(wait=False, cancel_futures=True)


class _Client:
    """Learner side of one client process: a pipe, its reader and in-flight tasks."""

    def __init__(self, options: dict[str, Any]) -> None:
        context = get_context("spawn")
        self.connection, child = context.Pipe(duplex=True)
        self.process = context.Process(target=_client_main, args=(child, options),
                                       name="rl-remote-client", daemon=True)
        self.process.start()
        child.close()
        self.lock = threading.Lock()  # pending, dead and sends
        self.pending: dict[int, Future] = {}
        self.dead = False
        self.reader = threading.Thread(target=self._read, name="rl-remote-client-reader",
                                       daemon=True)
        self.reader.start()

    def _read(self) -> None:
        closed = False
        while True:
            try:
                task_id, ok, body = self.connection.recv()
            except (EOFError, OSError):  # the process closed its end
                closed = True
                break
            except BaseException:  # a reply this side cannot take: stop the process
                break
            with self.lock:
                future = self.pending.pop(task_id, None)
            if future is not None:
                if ok:
                    future.set_result(body)
                else:
                    future.set_exception(body)
        # DIED means the process is gone: it must not still read an arena the
        # learner releases or reuses for the restarted client.
        if closed:
            self.process.join(timeout=5)
        if self.process.is_alive():
            self.process.kill()
            self.process.join()
        with self.lock:
            self.dead = True
            pending, self.pending = self.pending, {}
        for future in pending.values():
            future.set_exception(RuntimeError(self.died()))

    def died(self) -> str:
        return f"{DIED}: exitcode={self.process.exitcode}"

    def submit(self, task_id: int, task: tuple[Any, ...]) -> Future:
        future: Future = Future()
        with self.lock:
            if self.dead:
                raise RuntimeError(self.died())
            self.pending[task_id] = future
            try:
                self.connection.send((task_id, *task))
            except (OSError, ValueError) as error:
                self.pending.pop(task_id, None)
                raise RuntimeError(self.died()) from error
        return future

    def close(self) -> None:
        with suppress(Exception), self.lock:
            self.connection.send(None)
        self.process.join(timeout=10)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=5)
        self.connection.close()


class RemoteClients:
    """`RemoteAssembly` interface; the transfers run in `processes` client processes.

    The connection slots (`connections` per endpoint, as in `RemoteAssembly`) are
    dealt round-robin over the processes; a task takes a free slot, so no process
    ever holds more tasks than connections."""

    def __init__(self, *, processes: int, endpoints: list[str], vocabulary: dict[str, Any],
                 threads: int, connections: int = 2, auth_env: str = AUTH_ENV,
                 timeout_seconds: float = 600.0, codec: str = "none") -> None:
        if type(processes) is not int or processes < 1:
            raise ValueError("remote_prepare_client_processes_bound")
        if not endpoints or type(connections) is not int or connections < 1:
            raise ValueError("remote_prepare_endpoints_required")
        if not 0 < timeout_seconds <= 3600:
            raise ValueError("remote_prepare_timeout_bound")
        authkey(auth_env)  # refuse here, as RemoteAssembly does, not in a child
        _codec(codec)
        for endpoint in endpoints:
            _address(endpoint)
        slots = connection_slots(endpoints, connections)
        processes = min(processes, len(slots))
        groups = [slots[index::processes] for index in range(processes)]
        self.options = [{"endpoints": list(endpoints), "vocabulary": vocabulary,
                         "threads": threads, "connections": connections,
                         "auth_env": auth_env, "timeout_seconds": timeout_seconds,
                         "codec": codec, "slots": group} for group in groups]
        self.free: queue.Queue[int] = queue.Queue()
        for depth in range(len(groups[0])):
            for index, group in enumerate(groups):
                if depth < len(group):
                    self.free.put(index)
        self.closed = False
        self.lock = threading.Lock()
        self.clients = [_Client(options) for options in self.options]
        self.next_task = 0
        self.stats = {"tasks": 0, "label_tasks": 0, "sent_bytes": 0, "received_bytes": 0,
                      "wire_received_bytes": 0, "sources_sent": 0, "client_restarts": 0}
        self.cpu = cpu_fingerprint()  # this node's, as RemoteAssembly.cpu
        self.host_cpus: dict[str, dict[str, str]] = {}
        self.served: OrderedDict[str, str] = OrderedDict()

    def _call(self, kind: str, ready: tuple[Any, ...], args: tuple[Any, ...],
              output: str | None) -> tuple[dict[str, Any], float]:
        if self.closed:
            raise RuntimeError("remote_prepare_closed")
        queued = time.perf_counter()
        index = self.free.get()
        waited = time.perf_counter() - queued
        try:
            with self.lock:
                if self.closed:
                    raise RuntimeError("remote_prepare_closed")
                client = self.clients[index]
                if client.dead:  # a later task starts the process again
                    old, client = client, _Client(self.options[index])
                    self.clients[index] = client
                    self.stats["client_restarts"] += 1
                    threading.Thread(target=old.close, daemon=True).start()
                self.next_task += 1
                task_id = self.next_task
            # Descriptors only: the client reads the source bytes from the arena.
            sources = tuple(binary._RemoteSource(item.descriptor) for item in ready)
            body = client.submit(task_id, (kind, sources, args, output)).result()
        finally:
            self.free.put(index)
        with self.lock:
            endpoint = body["endpoint"]
            self.host_cpus[endpoint] = body["host_cpu"]
            if output is not None:
                self.served[output] = endpoint
                while len(self.served) > 256:
                    self.served.popitem(last=False)
            for name, value in body["delta"].items():
                self.stats[name] += value
        return body, waited

    def run(self, ready: tuple[Any, ...], args: tuple[Any, ...], output: str) -> Any:
        """Same value as `RemoteAssembly.run` (timings measured in the client)."""
        body, waited = self._call("run", ready, args[:5], output)
        arrays = tuple(binary._SharedArray(output, offset, count, dtype)
                       for offset, count, dtype in body["arrays"])
        summary = dict(body["summary"])
        timings = dict(summary["timings"])
        timings["binary_remote_wait_seconds"] = timings["binary_remote_wait_seconds"] + waited
        summary["timings"] = timings
        return binary._WorkerResult(binary._PackedBatch(body["tree"], arrays, summary),
                                    body["pid"], body["wall"], body["cpu"])

    def labels(self, ready: tuple[Any, ...], args: tuple[Any, ...]) -> Any:
        """Same value as `RemoteAssembly.labels`; bodies come from this process's arena."""
        body, _waited = self._call("labels", ready, args, None)
        return binary._WorkerResult(_unpack_labels(body["value"], ready), body["pid"],
                                    body["wall"], body["cpu"])

    def served_by(self, output: str) -> dict[str, str]:
        with self.lock:
            return dict(self.host_cpus.get(self.served.get(output, ""), {}))

    def close(self) -> None:
        with self.lock:
            self.closed = True
            clients = list(self.clients)
        for client in clients:
            client.close()


if __name__ == "__main__":
    serve()
