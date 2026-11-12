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

Originals are written by `write_workers` threads at once (default 4; c3b 11-12:
one writer on NFS scratch managed ~3.3 segments/s, the queue sat at `max_queue`
and `submit` blocked the service's scoring thread for 66% of its time). Only the
writes overlap: the hand-off thread still takes the queue in seal order, waits
for that segment's own write, and only then hands it off, so "disk first" and
hand-off order = seal order are unchanged; a write error is still recorded,
counted and raised for that segment, in order.

`DurableSegmentWriter` serves one assembler instance and refuses to start when
originals of that instance already exist (a restart must be a new instance).

Same-machine hand-off wire (`stpd/rl-fast-segment-handoff-1`), over one
`multiprocessing.connection` channel (Unix socket, authkey) per SGD consumer:

    sender -> SGD   {"schema", "handoff_sequence", "assembler_id" (the instance id), "segment_id",
                     "training_generation", "mu_snapshot_sha256", "rows",
                     "shm_name", "size", "sha256", "path"}
    SGD -> sender   {"schema", "ack": handoff_sequence}

After the last segment of a game instance the same channel carries one
instance-closed message (`stpd/rl-fast-instance-closed-1`, WP8), never acked:

    sender -> SGD   {"schema", "assembler_id", "game_instance_id", "close_reason",
                     "failure", "segments": {"bootstrap", "terminal", "truncated",
                     "rejected"}}

It is queued behind the instance's segments, so it arrives after every trainable
segment of that instance (`bootstrap` + `terminal` of them). `close_reason` is the
environment's `episode_close.reason` (an evidence failure known at close is
`env_crash`); `failure` is the game's first non-completed outcome
(`"<status>:<reason>"`) or null. What an exclusion drops is the learner's rule
(semantics A: unused queued segments of that instance; already-trained ones stay
counted, as in the old chain). `receive_handoff` reads every message kind.

With a `labeler` (WP8, `stpd/rl-fast-instance-labels-1`, lead approval 2026-11-09;
the learner's `fast_source.build_instance_labels`), one labels message goes between
the instance's last segment and its instance-closed message, in shared memory and
acked like a segment (`send_message`): the labeler's fields plus `handoff_sequence`
(same counter) and `shm_name`; the bytes are the referenced originals re-read from
this node's `.stfs` files and checked against their sha256. It is sent only for a
game every segment of which was trainable and handed off, closed without
`failure` and not `unknown`/`env_crash` (the old chain labels only such games);
any other game (also one across a training-generation change) is counted in
`labels_ineligible`, never labelled in part; a labeler, read-back or send error is
counted in `labels_failed` and stops the chain loudly. Two differences from the old
chain (lead ruling 2026-11-11): hp labels enter the pool at game end, not at their
segment's commit; and a game that did not close cleanly (truncated, failed or across
a generation) gives no hp labels for its finished battles.

`handoff_sequence` counts every acked hand-off message (trainable segments and
labels) in hand-off order from 1 per sender connection, strictly increasing, never
reused or resent; it is the order an
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
from concurrent.futures import Future, ThreadPoolExecutor
from multiprocessing import resource_tracker, shared_memory
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, NamedTuple

from .fast_segment import (
    InstanceClosed,
    RejectedSegment,
    SealedSegment,
    canonical_json,
    decode_segment,
    sha256,
)

HANDOFF_SCHEMA = "stpd/rl-fast-segment-handoff-1"
HANDOFF_KEYS = frozenset({"schema", "handoff_sequence", "assembler_id", "segment_id",
                          "training_generation", "mu_snapshot_sha256", "rows", "shm_name",
                          "size", "sha256", "path"})
INSTANCE_CLOSED_SCHEMA = "stpd/rl-fast-instance-closed-1"
INSTANCE_CLOSED_KEYS = frozenset({"schema", "assembler_id", "game_instance_id",
                                  "close_reason", "failure", "segments"})
LABELS_SCHEMA = "stpd/rl-fast-instance-labels-1"
# The sender's orderly goodbye: every hand-off was acked. A channel that reaches EOF
# without it lost its assembler (crash, killed process); the learner fences loudly.
HANDOFF_END_SCHEMA = "stpd/rl-fast-handoff-end-1"
HANDOFF_END_KEYS = frozenset({"schema", "assembler_id", "handed_off"})
# Close reasons after which a game gets no labels (the learner excludes its unused segments).
UNLABELLED_CLOSE_REASONS = frozenset({"unknown", "env_crash"})
SEGMENT_KINDS = ("bootstrap", "terminal", "truncated", "rejected")
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
        # Targets claimed by a write (SegmentHandoff writes on several threads): the
        # exists-then-rename check in `_durable_write` is not atomic between two
        # threads, so a second write of the same original is refused here first.
        self._claimed: set[Path] = set()
        self._claim_lock = threading.Lock()
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
        with self._claim_lock:
            if target in self._claimed:
                raise HandoffError("segment_original_already_exists")
            self._claimed.add(target)
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

    def _drain(self, *, block: bool, until_empty: bool = False) -> None:
        """Read acknowledgements: none waited for (block False), one (block True), or every
        one in flight (until_empty, for close); each wait is bounded by ack_timeout."""
        while self.inflight and (block or until_empty or self.connection.poll(0)):
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
        header = segment.header
        return self.send_message({
            "schema": HANDOFF_SCHEMA, "assembler_id": self.assembler_id,
            "segment_id": segment.segment_id,
            "training_generation": header["mu"]["training_generation"],
            "mu_snapshot_sha256": header["mu"]["snapshot_sha256"],
            "rows": len(header["rows"]), "size": len(segment.data),
            "sha256": segment.sha256, "path": str(path)}, segment.data)

    def send_message(self, fields: dict[str, Any], data: bytes) -> int:
        """`data` in a new shared-memory block, `fields` plus `handoff_sequence` and
        `shm_name` on the channel; acked and bounded like every hand-off."""
        if fields.get("assembler_id") != self.assembler_id:
            raise HandoffError("message_from_another_assembler_instance")
        if (fields.get("size") != len(data) or fields.get("sha256") != sha256(data)
                or {"handoff_sequence", "shm_name"} & set(fields)):
            raise HandoffError("handoff_message_binding")
        size = len(data)
        self._drain(block=False)
        while self.inflight and self.inflight_bytes + size > self.max_inflight_bytes:
            self._drain(block=True)
        # A block cannot be empty; the reader copies exactly `size` bytes.
        block = shared_memory.SharedMemory(create=True, size=max(size, 1))
        try:
            buffer = block.buf
            assert buffer is not None
            buffer[:size] = data
            self.sequence += 1
            self.connection.send({**fields, "handoff_sequence": self.sequence,
                                  "shm_name": block.name})
        except BaseException:
            block.close()
            block.unlink()
            raise
        self.inflight[self.sequence] = (block, size)
        self.inflight_bytes += size
        return self.sequence

    def send_closed(self, closed: InstanceClosed) -> None:
        """The instance-closed message; queued behind that instance's segments."""
        self._drain(block=False)
        message = {"schema": INSTANCE_CLOSED_SCHEMA, "assembler_id": self.assembler_id,
                   "game_instance_id": closed.game_instance_id,
                   "close_reason": closed.close_reason, "failure": closed.failure,
                   "segments": dict(closed.segments)}
        check_instance_closed(message)
        self.connection.send(message)

    def finish(self) -> None:
        """The orderly end: every ack in, then the end message, then release.

        Call it only when the assembler really finished cleanly. A sender that fails
        before the end is sent sends none, and the reader treats the EOF as a lost
        assembler (fence).
        """
        try:
            self._drain(block=True, until_empty=True)
            self.connection.send({"schema": HANDOFF_END_SCHEMA,
                                  "assembler_id": self.assembler_id,
                                  "handed_off": self.sequence})
        finally:
            self.close()

    def close(self) -> None:
        """Release only, never the orderly end (safe in `finally` on a failure path):
        wait for the acks still due, then release the blocks."""
        try:
            # Every block in flight is still mapped by the reader until its ack arrives;
            # unlinking one early would fence the learner's input (shm_open fails).
            self._drain(block=True, until_empty=True)
        finally:
            for block, _ in self.inflight.values():
                block.close()
                block.unlink()
            self.inflight.clear()
            self.inflight_bytes = 0


def check_instance_closed(message: Any) -> dict[str, Any]:
    """Exact keys and types of an instance-closed message; loud on anything else."""
    if (not isinstance(message, dict) or message.get("schema") != INSTANCE_CLOSED_SCHEMA
            or set(message) != INSTANCE_CLOSED_KEYS):
        raise HandoffError("instance_closed_schema")
    segments = message["segments"]
    if (not all(isinstance(message[key], str) and message[key]
                for key in ("assembler_id", "game_instance_id", "close_reason"))
            or not (message["failure"] is None or isinstance(message["failure"], str))
            or not isinstance(segments, dict) or set(segments) != set(SEGMENT_KINDS)
            or any(type(count) is not int or count < 0 for count in segments.values())):
        raise HandoffError("instance_closed_schema")
    return message


def check_handoff_end(message: Any) -> dict[str, Any]:
    """Exact keys and types of the sender's orderly end; loud on anything else."""
    if (not isinstance(message, dict) or message.get("schema") != HANDOFF_END_SCHEMA
            or set(message) != HANDOFF_END_KEYS
            or not isinstance(message["assembler_id"], str) or not message["assembler_id"]
            or type(message["handed_off"]) is not int or message["handed_off"] < 0):
        raise HandoffError("handoff_end_schema")
    return message


def receive_handoff(connection: Connection) -> tuple[str, Any]:
    """SGD-side reference reader for every message of the channel.

    Returns `("segment", (message, header, rows))` (as `receive_segment`),
    `("labels", (message, data))` (bytes verified, acked; the label rule and the
    carried originals are the learner's to check), `("instance_closed", message)` or
    `("end", message)` (the sender's orderly end); anything else raises.
    """
    message = connection.recv()
    schema = message.get("schema") if isinstance(message, dict) else None
    if schema == HANDOFF_END_SCHEMA:
        return "end", check_handoff_end(message)
    if schema == INSTANCE_CLOSED_SCHEMA:
        return "instance_closed", check_instance_closed(message)
    if schema == LABELS_SCHEMA:
        data = _read_block(message)
        connection.send({"schema": HANDOFF_SCHEMA, "ack": message["handoff_sequence"]})
        return "labels", (message, data)
    return "segment", _receive_segment_message(connection, message)


def _read_block(message: dict[str, Any]) -> bytes:
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
    return data


def receive_segment(connection: Connection) -> tuple[dict[str, Any], dict[str, Any], list[bytes]]:
    """SGD-side reference reader: (hand-off message, segment header, row bytes).

    Copies and verifies the bytes, acks, and leaves the block to the sender.
    The learner then runs `decode_row` on each row and builds rows-v2 groups.
    A channel whose sender emits instance-closed messages is read with
    `receive_handoff` instead.
    """
    return _receive_segment_message(connection, connection.recv())


def _receive_segment_message(connection: Connection, message: Any
                             ) -> tuple[dict[str, Any], dict[str, Any], list[bytes]]:
    if (not isinstance(message, dict) or message.get("schema") != HANDOFF_SCHEMA
            or set(message) != HANDOFF_KEYS):
        raise HandoffError("handoff_schema")
    data = _read_block(message)
    header, rows = decode_segment(data)
    if (header["segment_id"] != message["segment_id"] or not header["trainable"]
            or len(rows) != message["rows"]
            or header["assembler_instance"] != message["assembler_id"]
            or header["mu"]["training_generation"] != message["training_generation"]
            or header["mu"]["snapshot_sha256"] != message["mu_snapshot_sha256"]):
        raise HandoffError("handoff_segment_binding")
    connection.send({"schema": HANDOFF_SCHEMA, "ack": message["handoff_sequence"]})
    return message, header, rows


class _Original:
    """A handed-off segment kept for its game's labels: header in memory, bytes on disk."""

    def __init__(self, segment: SealedSegment, path: Path) -> None:
        self.header, self.segment_id = segment.header, segment.segment_id
        self.sha256 = segment.sha256
        self.path = path
        self._data: bytes | None = None
        # The same read-back, started early on a writer thread (SegmentHandoff.submit_closed).
        self.prefetched: Future[bytes] | None = None

    @property
    def data(self) -> bytes:
        if self._data is None:
            prefetched, self.prefetched = self.prefetched, None
            self._data = (prefetched.result() if prefetched is not None
                          else read_original(self.path, self.sha256))
        return self._data


def read_original(path: Path, expected_sha256: str) -> bytes:
    """An original re-read from disk and checked against its sha256 (labels carry these)."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise HandoffError("labels_original_unreadable") from error
    if sha256(data) != expected_sha256:
        raise HandoffError("labels_original_changed")
    return data


class _Queued:
    """A sealed segment in the hand-off queue and the future of its durable write."""

    __slots__ = ("segment", "written", "passed")

    def __init__(self, segment: SealedSegment | RejectedSegment, written: Future[Path]) -> None:
        self.segment, self.written = segment, written
        # Set once the hand-off thread is done with it (handed off, `on_written` run):
        # a labels read-back never reads an original earlier than it did before.
        self.passed = threading.Event()


class _ReadBack(NamedTuple):
    """What a labels read-back of one submitted original needs (not its bytes)."""

    segment_id: str
    sha256: str
    written: Future[Path]
    passed: threading.Event

    def read(self) -> bytes:
        # After the hand-off thread passed it (so after its write and `on_written`);
        # a write failure means the game gets no labels, so this is never used.
        self.passed.wait()
        return read_original(self.written.result(), self.sha256)


class SegmentHandoff:
    """Background writer + sender so the assembler never blocks under its lock."""

    def __init__(self, writer: DurableSegmentWriter, sender: ShmSegmentSender | None = None,
                 *, max_queue: int = 64, write_workers: int = 4,
                 on_written: Callable[[SealedSegment | RejectedSegment, Path], None] | None = None,
                 labeler: Callable[[list[Any]], tuple[dict[str, Any], bytes]] | None = None
                 ) -> None:
        if type(write_workers) is not int or write_workers < 1:
            raise HandoffError("positive_write_workers_required")
        self.writer, self.sender, self.on_written = writer, sender, on_written
        self.labeler = labeler
        # Per open game: its handed-off segments, or None once one segment of it was not
        # (truncated, rejected, write or hand-off failure): that game gets no labels.
        self._games: dict[str, list[_Original] | None] = {}
        # Seal order; a segment travels with the future of its own durable write.
        self.queue: queue.Queue[_Queued | InstanceClosed | None] = queue.Queue(max_queue)
        self._writes = ThreadPoolExecutor(write_workers,
                                          thread_name_prefix="fast-segment-write")
        # Labels read-back started at submit_closed (c3b: done in order on the hand-off
        # thread it took a median 1.35 s per labelled game, 57% of that thread):
        # trainable segments submitted per open game (ids and events only, never the
        # bytes), and the read-backs by segment id. Read-backs run on their own threads,
        # so a read-back waiting for the hand-off thread never holds a writer.
        self._submitted: dict[str, list[_ReadBack]] = {}
        self._reads = ThreadPoolExecutor(2, thread_name_prefix="fast-segment-read-back")
        self._prefetch: dict[str, dict[str, Future[bytes]]] = {}  # game -> segment id
        self._prefetch_lock = threading.Lock()
        self.write_error: BaseException | None = None
        self.send_error: BaseException | None = None
        self.counters = {"written": 0, "handed_off": 0, "truncated_written": 0,
                         "rejected_written": 0, "bytes": 0, "write_failed": 0,
                         "not_handed_off": 0, "instances_closed_sent": 0,
                         "instances_closed_not_sent": 0, "labels_sent": 0,
                         "labels_ineligible": 0, "labels_failed": 0}
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
        """Always queue the segment first (it will be written), then report failures.

        Blocks only while `max_queue` segments wait to be written or handed off.
        """
        queued = _Queued(segment, Future())
        instance = segment.header.get("game_instance_id")
        if (self.labeler is not None and isinstance(segment, SealedSegment)
                and segment.trainable and isinstance(instance, str)):
            with self._prefetch_lock:
                self._submitted.setdefault(instance, []).append(
                    _ReadBack(segment.segment_id, segment.sha256, queued.written,
                              queued.passed))
        self.queue.put(queued)
        try:
            self._writes.submit(self._write, queued)
        except RuntimeError as error:  # submit after close(): never written, loud
            queued.written.set_exception(HandoffError("segment_handoff_closed"))
            self.raise_if_failed()
            raise HandoffError("segment_handoff_closed") from error
        self.raise_if_failed()

    def _write(self, queued: _Queued) -> None:
        """One durable write on a writer thread; the hand-off thread reads the result."""
        try:
            queued.written.set_result(self.writer.write(queued.segment))
        except BaseException as error:  # noqa: BLE001 - recorded and re-raised in order
            self.write_error = self.write_error or error  # loud on the next submit
            queued.written.set_exception(error)

    def submit_closed(self, closed: InstanceClosed) -> None:
        """Queue the instance-closed message behind the instance's segments.

        Without a sender, or after a hand-off failure, it is counted as not sent
        (the chain has already stopped loudly); it is never resent.
        A game that may get labels has its originals read back now, on the writer
        threads, so the hand-off thread finds them verified when it reaches this close
        (same bytes, same checks and errors, same message order).
        """
        with self._prefetch_lock:
            submitted = self._submitted.pop(closed.game_instance_id, [])
        if (submitted and self.labeler is not None and closed.failure is None
                and closed.close_reason not in UNLABELLED_CLOSE_REASONS
                and closed.segments.get("truncated") == 0
                and closed.segments.get("rejected") == 0):
            futures: dict[str, Future[bytes]] = {}
            for item in submitted:
                try:
                    futures[item.segment_id] = self._reads.submit(item.read)
                except RuntimeError:  # after close(): the rest are read in order as before
                    break
            with self._prefetch_lock:
                self._prefetch[closed.game_instance_id] = futures
        self.queue.put(closed)
        self.raise_if_failed()

    def _labelable(self, closed: InstanceClosed, kept: list[_Original] | None) -> bool:
        return (kept is not None and len(kept) > 0 and closed.failure is None
                and closed.close_reason not in UNLABELLED_CLOSE_REASONS
                and closed.segments.get("truncated") == 0
                and closed.segments.get("rejected") == 0
                and len(kept) == closed.segments.get("bootstrap", 0)
                + closed.segments.get("terminal", 0)
                # A game across a generation change: the learner drops the old part.
                and len({o.header["mu"]["training_generation"] for o in kept}) == 1)

    def _send_labels(self, closed: InstanceClosed) -> None:
        """Labels for a game, before its instance-closed message; never in part."""
        kept = self._games.pop(closed.game_instance_id, None)
        with self._prefetch_lock:
            prefetched = self._prefetch.pop(closed.game_instance_id, {})
        if self.labeler is None:
            return
        if self.sender is None or self.send_error is not None:
            # The chain has already stopped loudly (or there is no SGD): not a failure here.
            self.counters["labels_ineligible"] += 1
            return
        if not self._labelable(closed, kept):
            self.counters["labels_ineligible"] += 1
            return
        assert kept is not None
        for original in kept:
            original.prefetched = prefetched.get(original.segment_id)
        try:
            fields, data = self.labeler(kept)
            self.sender.send_message(fields, data)
            self.counters["labels_sent"] += 1
        except BaseException as error:  # noqa: BLE001 - recorded, re-raised
            self.send_error = error
            self.counters["labels_failed"] += 1

    def _keep(self, segment: SealedSegment | RejectedSegment, path: Path | None,
              handed_off: bool) -> None:
        if self.labeler is None:
            return
        instance = segment.header.get("game_instance_id")
        if not isinstance(instance, str):
            return
        kept = self._games.setdefault(instance, [])
        if kept is None:
            return
        if handed_off and isinstance(segment, SealedSegment) and path is not None:
            kept.append(_Original(segment, path))
        else:
            self._games[instance] = None

    def _send_closed(self, closed: InstanceClosed) -> None:
        self._send_labels(closed)
        if self.sender is None or self.send_error is not None:
            self.counters["instances_closed_not_sent"] += 1
            return
        try:
            self.sender.send_closed(closed)
            self.counters["instances_closed_sent"] += 1
        except BaseException as error:  # noqa: BLE001 - recorded, re-raised
            self.send_error = error
            self.counters["instances_closed_not_sent"] += 1

    def _run(self) -> None:
        while True:
            queued = self.queue.get()
            if queued is None:
                return
            if isinstance(queued, InstanceClosed):
                self._send_closed(queued)
                continue
            try:
                self._hand_off(queued)
            finally:
                queued.passed.set()

    def _hand_off(self, queued: _Queued) -> None:
        segment = queued.segment
        try:
            path = queued.written.result()  # this segment is on disk before anything else
        except BaseException as error:  # noqa: BLE001 - recorded and re-raised
            self.counters["write_failed"] += 1
            self.write_error = self.write_error or error
            self._keep(segment, None, handed_off=False)
            return
        handed_off = False
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
                    handed_off = True
                except BaseException as error:  # noqa: BLE001 - recorded, re-raised
                    self.send_error = error
                    self.counters["not_handed_off"] += 1
        self._keep(segment, path, handed_off)
        if self.on_written is not None:
            try:
                self.on_written(segment, path)
            except BaseException as error:  # noqa: BLE001 - recorded and re-raised
                self.write_error = self.write_error or error

    def finish(self) -> None:
        """Orderly end: write/send what is queued, then the sender's end message.

        Only when nothing failed; otherwise this is `close()` (no end message, so the
        learner fences on the EOF) and the failure is raised.
        """
        self.close(orderly=True)

    def close(self, *, orderly: bool = False) -> None:
        """Write/send what is queued and release; never says the orderly end unless
        called through `finish()` with no write or hand-off failure."""
        self.queue.put(None)
        self.thread.join()
        self._writes.shutdown(wait=True)  # every queued write was awaited by the thread
        self._reads.shutdown(wait=True)  # read-backs only wait for items already passed
        try:
            if self.sender is not None and self.send_error is None:
                if orderly and self.write_error is None:
                    self.sender.finish()
                else:
                    self.sender.close()
        finally:
            self.raise_if_failed()
