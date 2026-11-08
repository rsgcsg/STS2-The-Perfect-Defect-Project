"""Sealed fast_chain segments: local disk first, then same-machine shared memory to SGD.

Sender side only (WP5). The SGD side (`sgd_process`, C-SCALE) connects in WP8
with `receive_segment` or its own reader of the same messages.

Order per segment (design v2 §2.8): encode -> write `<root>/<kind>/<segment_id>.stfs`
(temporary file, fsync, rename, directory fsync) -> only then, for trainable
segments, hand-off. Truncated segments are written and counted, never handed
off; encoder-refused segments go to `<root>/rejected/<segment_id>.json`.
Writing and hand-off fail separately: after a hand-off failure (SGD gone) every
queued and later segment is still written, only not handed off
(`not_handed_off`); after a write failure every later segment is still tried.
Either failure is sticky and raised to every later `submit` (after the segment
was queued) and by `raise_if_failed`: the chain stops loudly, nothing falls back.

What "disk first" does not cover (a crash loses these without a record; no
duplicate follows, recovery is CP-only): segments sealed and queued but not
yet written (at most `max_queue`; keep it small), rows of segments not yet
sealed, and the bytes of a row in flight when its game closes.

`DurableSegmentWriter` serves one assembler instance and refuses to start when
originals of that instance already exist (a restart must be a new instance).

Same-machine hand-off wire (`stpd/rl-fast-segment-handoff-1`), over one
`multiprocessing.connection` channel (Unix socket, authkey) per SGD consumer:

    sender -> SGD   {"schema", "handoff_sequence", "assembler_id" (the instance id), "segment_id",
                     "training_generation", "mu_snapshot_sha256", "rows",
                     "shm_name", "size", "sha256", "path"}
    SGD -> sender   {"schema", "ack": handoff_sequence}

`handoff_sequence` counts trainable segments in hand-off order from 1 per sender
connection, strictly increasing, never reused or resent; it is the order an
SGD-side queue (today `SegmentQueue`, lease `<assembler_id>/<sequence>`) needs.
The SGD side attaches the named block, checks size and sha256, copies the bytes
and acks; the sender then unlinks the block. Unacknowledged bytes are bounded by
`max_inflight_bytes`; when full, the hand-off waits (back-pressure, no drop).
A lost SGD connection is fatal for this sender: segments already on disk stay as
originals; recovery is CP-only (the live queue is discarded, never re-offered).
After a training-generation change, trainable segments of the old generation
that were already queued are still handed off; the SGD side drops them by
`training_generation` (WP8 acceptance item). `receive_segment` checks the
message's instance, generation and mu against the segment header.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import threading
from collections.abc import Callable
from multiprocessing import resource_tracker, shared_memory
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from .fast_segment import RejectedSegment, SealedSegment, canonical_json, decode_segment, sha256

HANDOFF_SCHEMA = "stpd/rl-fast-segment-handoff-1"
HANDOFF_KEYS = frozenset({"schema", "handoff_sequence", "assembler_id", "segment_id",
                          "training_generation", "mu_snapshot_sha256", "rows", "shm_name",
                          "size", "sha256", "path"})
SUFFIX = ".stfs"


class HandoffError(RuntimeError):
    """A failed write or hand-off; fatal for this assembler, never retried."""


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_write(target: Path, data: bytes, conflict: str) -> None:
    """Temporary file, fsync, rename, directory fsync; never overwrites.

    The temporary name is unique per write, so a crash leftover (kept, never an
    original: only a renamed file is) never blocks a later write of the target.
    """
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view):]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if target.exists():
        temporary.unlink()
        raise HandoffError(conflict)
    os.rename(temporary, target)
    _fsync_directory(target.parent)


KINDS = ("bootstrap", "terminal", "truncated", "rejected")


class DurableSegmentWriter:
    """Immutable local originals of one assembler instance; never overwritten."""

    def __init__(self, root: Path | str, *, instance_id: str) -> None:
        if not isinstance(instance_id, str) or not instance_id:
            raise HandoffError("assembler_instance_required")
        self.root, self.instance_id = Path(root), instance_id
        prefix = instance_id + "."
        for kind in KINDS:
            directory = self.root / kind
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with os.scandir(directory) as entries:
                if any(entry.name.lstrip(".").startswith(prefix) for entry in entries):
                    raise HandoffError("assembler_instance_already_has_originals")

    def path(self, segment: SealedSegment | RejectedSegment) -> Path:
        suffix = ".json" if isinstance(segment, RejectedSegment) else SUFFIX
        return self.root / segment.kind / (segment.segment_id + suffix)

    def write(self, segment: SealedSegment | RejectedSegment) -> Path:
        if not segment.segment_id.startswith(self.instance_id + "."):
            raise HandoffError("segment_from_another_assembler_instance")
        target = self.path(segment)
        _durable_write(target, segment.data, "segment_original_already_exists")
        return target


MU_ARCHIVE_SCHEMA = "stpd/rl-fast-mu-archive-1"


class DurableMuArchive:
    """Weights of the temporary mu an R7 game used (`episode_open.audit_retain_mu`).

    `<snapshot>.weights` holds the bytes the learner pushed;
    `<snapshot>.<generation digest 16>.json` (written last, the commit marker)
    binds them by sha256 to the mu identity under that training generation (the
    same weights may come back under a new generation after a CP restore).
    Archiving the same mu again is a no-op; a different identity or byte digest
    under the same snapshot and generation is a loud conflict. Kept with the R7
    originals.

    `snapshot_of(weights)` recomputes the snapshot identity from the pushed bytes
    (`segment_learner` digests weights, heads and numerical settings; the pushed
    format is WP4's). It is required: bytes that are not that snapshot are
    refused on write and on read, never archived "correctly" under its name.
    """

    def __init__(self, root: Path | str, *, snapshot_of: Callable[[bytes], str]) -> None:
        if not callable(snapshot_of):
            raise HandoffError("mu_archive_requires_snapshot_check")
        self.root, self.snapshot_of = Path(root) / "mu", snapshot_of
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def manifest(self, handoff: Any) -> bytes:
        weights = handoff.weights
        if not isinstance(weights, bytes):
            raise HandoffError("mu_archive_weights_missing")
        if self.snapshot_of(weights) != handoff.snapshot_sha256:
            raise HandoffError("mu_archive_weights_not_snapshot")
        return canonical_json({"schema": MU_ARCHIVE_SCHEMA, "mu": handoff.identity(),
                               "weights_sha256": sha256(weights), "size": len(weights)})

    def __call__(self, handoff: Any) -> None:
        manifest = self.manifest(handoff)
        weights_path = self.root / (handoff.snapshot_sha256 + ".weights")
        manifest_path = self._manifest_path(handoff.snapshot_sha256,
                                            handoff.training_generation)
        if manifest_path.exists():
            if manifest_path.read_bytes() != manifest:
                raise HandoffError("mu_archive_conflict")
            self.read(handoff.snapshot_sha256, handoff.training_generation)
            return
        if not weights_path.exists():
            _durable_write(weights_path, handoff.weights, "mu_archive_conflict")
        elif sha256(weights_path.read_bytes()) != sha256(handoff.weights):
            raise HandoffError("mu_archive_conflict")
        _durable_write(manifest_path, manifest, "mu_archive_conflict")

    def _manifest_path(self, snapshot_sha256: str, training_generation: str) -> Path:
        generation = sha256(training_generation.encode())[:16]
        return self.root / f"{snapshot_sha256}.{generation}.json"

    def read(self, snapshot_sha256: str,
             training_generation: str) -> tuple[dict[str, Any], bytes]:
        """(mu identity, weights) for the R7 replay; verified, never repaired."""
        try:
            manifest = json.loads(self._manifest_path(snapshot_sha256,
                                                      training_generation).read_bytes())
            weights = (self.root / (snapshot_sha256 + ".weights")).read_bytes()
            mu = manifest["mu"]
            valid = (manifest.get("schema") == MU_ARCHIVE_SCHEMA
                     and mu["snapshot_sha256"] == snapshot_sha256
                     and mu["training_generation"] == training_generation
                     and manifest["weights_sha256"] == sha256(weights)
                     and manifest["size"] == len(weights)
                     and self.snapshot_of(weights) == snapshot_sha256)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise HandoffError("mu_archive_mismatch") from error
        if not valid:
            raise HandoffError("mu_archive_mismatch")
        return mu, weights


class ShmSegmentSender:
    """One SGD consumer on this machine; bounded unacknowledged shared memory."""

    def __init__(self, connection: Connection, *, assembler_id: str,
                 max_inflight_bytes: int = 512 * 1024**2, ack_timeout: float = 600) -> None:
        if max_inflight_bytes < 1:
            raise HandoffError("positive_inflight_limit_required")
        self.connection, self.assembler_id = connection, assembler_id
        self.max_inflight_bytes, self.ack_timeout = max_inflight_bytes, ack_timeout
        self.sequence = 0
        # Sent size per block: on macOS `SharedMemory.size` is rounded up to the
        # page size, so the bound is kept in requested bytes (Linux is exact).
        self.inflight: dict[int, tuple[shared_memory.SharedMemory, int]] = {}
        self.inflight_bytes = 0

    def _drain(self, *, block: bool) -> None:
        while self.inflight and (block or self.connection.poll(0)):
            if not self.connection.poll(self.ack_timeout):
                raise HandoffError("sgd_ack_timeout")
            reply = self.connection.recv()
            sequence = reply.get("ack") if isinstance(reply, dict) else None
            if reply.get("schema") != HANDOFF_SCHEMA or sequence not in self.inflight:
                raise HandoffError("sgd_ack_binding")
            block_, sent = self.inflight.pop(sequence)
            self.inflight_bytes -= sent
            block_.close()
            block_.unlink()
            block = False

    def send(self, segment: SealedSegment, path: Path) -> int:
        if not segment.trainable:
            raise HandoffError("truncated_segment_never_handed_to_sgd")
        if segment.header.get("assembler_instance") != self.assembler_id:
            raise HandoffError("segment_from_another_assembler_instance")
        size = len(segment.data)
        self._drain(block=False)
        while self.inflight and self.inflight_bytes + size > self.max_inflight_bytes:
            self._drain(block=True)
        block = shared_memory.SharedMemory(create=True, size=size)
        try:
            buffer = block.buf
            assert buffer is not None
            buffer[:size] = segment.data
            self.sequence += 1
            header = segment.header
            self.connection.send({
                "schema": HANDOFF_SCHEMA, "handoff_sequence": self.sequence,
                "assembler_id": self.assembler_id, "segment_id": segment.segment_id,
                "training_generation": header["mu"]["training_generation"],
                "mu_snapshot_sha256": header["mu"]["snapshot_sha256"],
                "rows": len(header["rows"]), "shm_name": block.name, "size": size,
                "sha256": segment.sha256, "path": str(path)})
        except BaseException:
            block.close()
            block.unlink()
            raise
        self.inflight[self.sequence] = (block, size)
        self.inflight_bytes += size
        return self.sequence

    def close(self) -> None:
        """Wait for every acknowledgement, then release the blocks."""
        try:
            self._drain(block=True)
        finally:
            for block, _ in self.inflight.values():
                block.close()
                block.unlink()
            self.inflight.clear()
            self.inflight_bytes = 0


def receive_segment(connection: Connection) -> tuple[dict[str, Any], dict[str, Any], list[bytes]]:
    """SGD-side reference reader: (hand-off message, segment header, row bytes).

    Copies and verifies the bytes, acks, and leaves the block to the sender.
    The learner then runs `decode_row` on each row and builds rows-v2 groups.
    """
    message = connection.recv()
    if (not isinstance(message, dict) or message.get("schema") != HANDOFF_SCHEMA
            or set(message) != HANDOFF_KEYS):
        raise HandoffError("handoff_schema")
    block = shared_memory.SharedMemory(name=message["shm_name"])
    try:
        # The sender owns and unlinks the block; this process must not track it.
        resource_tracker.unregister(block._name, "shared_memory")  # type: ignore[attr-defined]
        if block.size < message["size"]:
            raise HandoffError("handoff_size")
        buffer = block.buf
        assert buffer is not None
        data = bytes(buffer[:message["size"]])
    finally:
        block.close()
    if sha256(data) != message["sha256"]:
        raise HandoffError("handoff_digest")
    header, rows = decode_segment(data)
    if (header["segment_id"] != message["segment_id"] or not header["trainable"]
            or len(rows) != message["rows"]
            or header["assembler_instance"] != message["assembler_id"]
            or header["mu"]["training_generation"] != message["training_generation"]
            or header["mu"]["snapshot_sha256"] != message["mu_snapshot_sha256"]):
        raise HandoffError("handoff_segment_binding")
    connection.send({"schema": HANDOFF_SCHEMA, "ack": message["handoff_sequence"]})
    return message, header, rows


class SegmentHandoff:
    """Background writer + sender so the assembler never blocks under its lock."""

    def __init__(self, writer: DurableSegmentWriter, sender: ShmSegmentSender | None = None,
                 *, max_queue: int = 64,
                 on_written: Callable[[SealedSegment | RejectedSegment, Path], None] | None = None
                 ) -> None:
        self.writer, self.sender, self.on_written = writer, sender, on_written
        self.queue: queue.Queue[SealedSegment | RejectedSegment | None] = queue.Queue(max_queue)
        self.write_error: BaseException | None = None
        self.send_error: BaseException | None = None
        self.counters = {"written": 0, "handed_off": 0, "truncated_written": 0,
                         "rejected_written": 0, "bytes": 0, "write_failed": 0,
                         "not_handed_off": 0}
        self.thread = threading.Thread(target=self._run, name="fast-segment-handoff",
                                       daemon=True)
        self.thread.start()

    @property
    def error(self) -> BaseException | None:
        return self.write_error or self.send_error

    def raise_if_failed(self) -> None:
        if self.write_error is not None:
            raise HandoffError("segment_write_failed") from self.write_error
        if self.send_error is not None:
            raise HandoffError("segment_handoff_failed") from self.send_error

    def submit(self, segment: SealedSegment | RejectedSegment) -> None:
        """Always queue the segment first (it will be written), then report failures."""
        self.queue.put(segment)
        self.raise_if_failed()

    def _run(self) -> None:
        while True:
            segment = self.queue.get()
            if segment is None:
                return
            try:
                path = self.writer.write(segment)
            except BaseException as error:  # noqa: BLE001 - recorded and re-raised
                self.counters["write_failed"] += 1
                self.write_error = self.write_error or error
                continue
            self.counters["written"] += 1
            self.counters["bytes"] += len(segment.data)
            if isinstance(segment, RejectedSegment):
                self.counters["rejected_written"] += 1
            elif not segment.trainable:
                self.counters["truncated_written"] += 1
            elif self.sender is not None:
                if self.send_error is not None:
                    self.counters["not_handed_off"] += 1
                else:
                    try:
                        self.sender.send(segment, path)
                        self.counters["handed_off"] += 1
                    except BaseException as error:  # noqa: BLE001 - recorded, re-raised
                        self.send_error = error
                        self.counters["not_handed_off"] += 1
            if self.on_written is not None:
                try:
                    self.on_written(segment, path)
                except BaseException as error:  # noqa: BLE001 - recorded and re-raised
                    self.write_error = self.write_error or error

    def close(self) -> None:
        self.queue.put(None)
        self.thread.join()
        try:
            if self.sender is not None and self.send_error is None:
                self.sender.close()
        finally:
            self.raise_if_failed()
