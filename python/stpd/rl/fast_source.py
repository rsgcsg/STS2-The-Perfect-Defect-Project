"""Learner side of the fast_chain segment hand-off (C-SCALE / WP8, `fast_chain` only).

Three pieces, none used by the old chain:

- `decode_fast_packet`: one sealed fast segment (either byte format, `stpd/rl-fast-segment-1`
  JSON rows or `-2` columns; `decode_segment` dispatches on the magic) -> `PreparedSource` plus
  the `{envelope, witness}` header the binary preparer already checks. Rows are
  rebuilt with `decode_row` (validator + row digest), targets come from the
  segment's own mu values (`categorical_lambda_targets`, no theta, no newer mu).
- `FastSegmentIntake`: admission bookkeeping keyed by `game_instance_id`.
  An instance is trainable only after the actor's `fast_instance_admission`
  (seed/holdout check once per game). Segments sealed before that are buffered
  (bounded, oldest dropped and counted). Exclusion (decision D1 = A): when
  `instance_closed` says `close_reason` unknown/env_crash or `failure` unknown:*,
  or the actor sends `fast_instance_excluded`, the instance's not-yet-used
  segments are dropped and the already-used ones are only counted (old-chain
  behavior for verified prefixes). "Used" = already selected into a prepared
  (read-ahead) batch (lead ruling 11-10): such a batch is neither voided nor
  rebuilt; its rows are counted apart from rows already handed to SGD. An
  instance never attested is truncated.
- `read_handoff`: the receiving end of `fast_handoff` (segment messages are
  copied from shared memory, verified and acked; `instance_closed` has no ack).

Receive-side contract (sender: `fast_handoff` / `fast_assembly` / actor, WP8 甲):

- handoff `stpd/rl-fast-segment-handoff-1`: unchanged (see `fast_handoff`).
- handoff `stpd/rl-fast-instance-closed-1`, same Connection, after every segment of
  that instance, no ack: `assembler_id`, `game_instance_id`, `close_reason`
  (episode_close reason), `failure` (first non-completed outcome `status:reason` or
  null), `segments` {bootstrap, terminal, truncated, rejected} counts.
- actor control `fast_instance_admission`: `actor`, `intent_id`, `seed`,
  `game_instance_id`, `training_split`, `run`, `training_generation`; reply
  `{"admitted": true, "game_instance_id"}` or `{"admitted": false, "reason"}`.
- actor control `fast_instance_excluded`: `actor`, `game_instance_id`, `reason`,
  `run`, `training_generation`; reply `{"excluded": true, "game_instance_id"}`.
- handoff `stpd/rl-fast-instance-labels-1` (lead approval 2026-11-09), same
  Connection, after the instance's last segment and before its instance_closed,
  shared memory + ack like a segment. The sender builds it with
  `build_instance_labels(trainable segments in order)` and adds the transport
  fields `handoff_sequence` and `shm_name`; `data` is the referenced original
  segments' exact bytes (re-read from the node-local .stfs originals), 8-byte
  aligned at `segments[i].offset`. The learner recomputes every label from the
  segment headers it received and fences on any difference; the aux pools then
  hold those original segments, exactly as the old chain's aux packets did.
  Label rules (`instance_labels`), the old chain's:
  - terminal: `uniform_depth_indices(n, 64)` over all n trainable rows of the
    game, each labelled with the terminal seal's class (old
    `completed_auxiliary`);
  - hp: `r7_audit.public_battle_hp` (a battle = a maximal run of in_combat rows
    ending where the next row is not in combat or at the natural terminal; label
    = public hp in that last combat row's step outcome / 100, states = the
    battle's rows). Dataplane `fast_aux_labels` defaults to every pool whose
    loss coefficient is > 0 (an explicit list may not leave one out; lead
    ruling 11-10); `compare_hp_labels` still has to show the hp rule equal to
    the old chain on a corpus before a long run relies on hp. It switches to
    `combat_end` once the native side emits it (WP1 slice 3). Two differences
    from the old chain (lead ruling on #450 review N1, accepted as current
    behavior; stop/budget truncation is rare. Note: with the 11-10 default
    above, hp is on whenever hp_coef > 0, and LossConfig.hp_coef is 0.1):
    (1) hp rows enter the pool at game end, not at their segment's commit;
    (2) a game without a clean close (truncated, failed, or across a
    generation change) gives no hp labels.
"""

from __future__ import annotations

import collections
import copy
import mmap
import os
import re
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from multiprocessing import shared_memory
from multiprocessing.connection import Connection
from typing import Any

import torch

if sys.platform != "win32":
    import _posixshmem  # type: ignore[import-not-found]  # CPython-internal, no stub

from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.rows_encoder_wire import decode_row

from . import batch_preparation as bp
from .fast_handoff import (
    HANDOFF_END_SCHEMA,
    HANDOFF_KEYS,
    HANDOFF_SCHEMA,
    HandoffError,
    check_handoff_end,
)
from .fast_segment import FORMAT as FAST_FORMAT
from .fast_segment import MU_KEYS, decode_segment, sha256
from .heads import categorical_lambda_targets

CLOSED_SCHEMA = "stpd/rl-fast-instance-closed-1"
CLOSED_KEYS = frozenset({"schema", "assembler_id", "game_instance_id", "close_reason",
                         "failure", "segments"})
CLOSE_REASONS = frozenset({"terminal", "not_started", "unknown", "stop", "budget", "env_crash"})
SEGMENT_KINDS = ("bootstrap", "terminal", "truncated", "rejected")
ADMISSION_FIELDS = frozenset({"actor", "intent_id", "seed", "game_instance_id",
                              "training_split", "run", "training_generation"})
EXCLUSION_FIELDS = frozenset({"actor", "game_instance_id", "reason", "run",
                              "training_generation"})
# Close facts that make every not-yet-used segment of the game untrainable.
EXCLUDING_CLOSE_REASONS = frozenset({"unknown", "env_crash"})
ENVELOPE_KEYS = frozenset({"format", "payload_kind", "run", "training_generation", "actor",
                           "episode", "segment_id", "run_id", "rows", "mu", "compiler_identity",
                           "assembler_id", "training_split", "seed", "gae_lambda",
                           "packet_size", "packet_sha256"})
_REASON = re.compile(r"[a-z0-9_]{1,64}")
LABELS_SCHEMA = "stpd/rl-fast-instance-labels-1"
LABEL_FIELDS = frozenset({"schema", "assembler_id", "game_instance_id", "training_generation",
                          "terminal", "outcome_rows", "hp_battles", "segments", "size",
                          "sha256"})
LABELS_KEYS = LABEL_FIELDS | {"handoff_sequence", "shm_name"}
AUX_LABEL_KINDS = frozenset({"terminal", "hp"})


class FastIntakeError(ValueError):
    """A broken fast-chain contract on the learner side: loud, never repaired."""


def _identity_fence(code: str, instance_id: str, assembler: Any,
                    earlier: Any) -> FastIntakeError:
    """An instance identity fence that names who collided: the instance, the
    assembler of this message and the one the instance was first seen / finished
    under. The code stays first (the learner keeps `str(error)[:400]`)."""
    return FastIntakeError(f"{code} instance={instance_id} assembler={assembler} "
                           f"earlier_assembler={earlier}")


def fast_envelope(header: dict[str, Any], *, size: int, digest: str, run: str,
                  attestation: dict[str, Any], gae_lambda: float) -> dict[str, Any]:
    """The learner's own record of one admitted segment (never sent anywhere).

    `format` here is the fast-chain family key (`FAST_FORMAT`) used for routing, not
    the segment's byte format: that is the magic / decoded header `format`.
    """
    return {
        "format": FAST_FORMAT, "payload_kind": "ppo", "run": run,
        "training_generation": header["mu"]["training_generation"],
        "actor": attestation["actor"], "episode": attestation["intent_id"],
        "segment_id": header["segment_id"], "run_id": header["game_instance_id"],
        "rows": len(header["rows"]), "mu": dict(header["mu"]),
        "compiler_identity": header["compiler_identity"],
        "assembler_id": header["assembler_instance"],
        "training_split": attestation["training_split"], "seed": attestation["seed"],
        "gae_lambda": float(gae_lambda), "packet_size": size, "packet_sha256": digest,
    }


def decode_fast_packet(raw: bytes | memoryview, *, envelope: dict[str, Any],
                       compiler: FrameCompiler | None) -> tuple[bp.PreparedSource, dict[str, Any]]:
    """Fast segment bytes -> (PreparedSource, {"envelope", "witness"}); never repaired."""
    began = time.perf_counter()
    if compiler is None:
        raise FastIntakeError("fast_source_requires_frame_compiler")
    if set(envelope) != ENVELOPE_KEYS or envelope["format"] != FAST_FORMAT:
        raise FastIntakeError("fast_envelope_fields_changed")
    if len(raw) != envelope["packet_size"]:
        raise FastIntakeError("fast_packet_size_changed")
    header, rows = decode_segment(raw, expected_sha256=envelope["packet_sha256"])
    seal = header["seal"]
    if (not header["trainable"] or seal["kind"] not in ("bootstrap", "terminal")
            or header["segment_id"] != envelope["segment_id"]
            or header["game_instance_id"] != envelope["run_id"]
            or header["assembler_instance"] != envelope["assembler_id"]
            or header["mu"] != envelope["mu"]
            or header["mu"]["training_generation"] != envelope["training_generation"]
            or len(rows) != envelope["rows"]):
        raise FastIntakeError("fast_segment_envelope_binding_changed")
    identity = header["compiler_identity"]
    if identity != envelope["compiler_identity"] or identity != compiler.identity:
        raise FastIntakeError("fast_segment_compiler_identity_changed")
    frames: list[Any] = []
    floors: list[int] = []
    refs: list[Any] = []
    actions: list[int] = []
    old: list[tuple[float, ...]] = []
    metadata: list[dict[str, Any]] = []
    for index, (meta, row) in enumerate(zip(header["rows"], rows, strict=True)):
        frame = decode_row(row, validator=compiler,
                           expected_sha256=meta["payload"]["row_sha256"])
        if not frame.choices or len(frame.choices) != meta["catalog_len"]:
            raise FastIntakeError("fast_row_catalog_changed")
        frames.append(frame)
        floors.append(meta["floor"])
        refs.append({"run_id": header["game_instance_id"], "segment_id": header["segment_id"],
                     "training_generation": envelope["training_generation"], "row": index,
                     "key": f"{header['game_instance_id']}:{meta['step_index']}"})
        # The q the service actually sampled from (validated by the segment reader:
        # finite, sums to 1, selected > 0), in the row's own catalog order.
        actions.append(meta["selected_index"])
        old.append(tuple(float(q) for q in meta["behavior_q"]))
        metadata.append({"generated_at": None, "generated_timestamp": None,
                         "floor": meta["floor"], "mu_version": header["mu"]["version"],
                         "mu_updates": header["mu"]["updates"]})
    decoded = time.perf_counter()
    probabilities = torch.tensor([meta["outcome_probabilities"] for meta in header["rows"]],
                                 dtype=torch.float32)
    terminal = seal["terminal"]
    tail = (None if terminal is not None else
            torch.tensor(seal["bootstrap"]["outcome_probabilities"], dtype=torch.float32))
    targets, advantages = categorical_lambda_targets(
        probabilities, tail, terminal_class=terminal["class"] if terminal is not None else None,
        gae_lambda=envelope["gae_lambda"])
    labels = {"segment_id": header["segment_id"], "run_id": header["game_instance_id"],
              "terminal": terminal, "hp_battles": []}
    source = bp.PreparedSource(
        header["segment_id"], header["game_instance_id"], envelope["training_generation"],
        tuple(frames), tuple(floors), tuple(refs), probabilities, targets, advantages,
        tuple(actions), tuple(old), tuple(metadata),
        {"decode_bind": decoded - began, "lambda_targets": time.perf_counter() - decoded},
        os.getpid(), identity, labels)
    witness = {
        **labels, "run": envelope["run"], "training_generation": envelope["training_generation"],
        "rows": len(rows), "mu": dict(header["mu"]), "compiler_identity": identity,
        "training_split": envelope["training_split"],
        "identity": {"intent_id": envelope["episode"], "seed": envelope["seed"]},
        "steps": [{"step_index": meta["step_index"], "successor": None,
                   "successor_sha256": None} for meta in header["rows"]],
    }
    return source, {"envelope": dict(envelope), "witness": witness}


def row_facts(header: dict[str, Any]) -> list[dict[str, Any]]:
    """What the label rule reads from one segment header, per row (no row bytes)."""
    return [{"segment_id": header["segment_id"], "row": index, "step_index": meta["step_index"],
             "in_combat": bool(meta["in_combat"]),
             "outcome": {"public_facts": {"hp": ((meta["outcome"] or {}).get("public_facts")
                                                 or {}).get("hp")},
                         "terminal": (meta["outcome"] or {}).get("terminal")}}
            for index, meta in enumerate(header["rows"])]


def instance_labels(instance: str, facts: Sequence[dict[str, Any]],
                    terminal: dict[str, Any] | None) -> dict[str, Any]:
    """The one fast label rule (sender and learner): one game's row facts in order."""
    from .r7_audit import public_battle_hp
    from .segments import uniform_depth_indices

    def ref(fact: dict[str, Any]) -> dict[str, Any]:
        return {"segment_id": fact["segment_id"], "row": fact["row"],
                "step_index": fact["step_index"]}

    outcome = ([ref(facts[i]) for i in uniform_depth_indices(len(facts))]
               if terminal is not None else [])
    by_step = {fact["step_index"]: fact for fact in facts}
    if len(by_step) != len(facts):
        raise FastIntakeError("fast_labels_step_index_repeated")
    battles = [{"battle_id": f"{instance}:{battle['steps'][0]}",
                "post_hp": battle["post_hp"],
                "states": [ref(by_step[step]) for step in battle["steps"]]}
               for battle in public_battle_hp(facts)
               if battle["post_hp"] >= 0]
    return {"terminal": copy.deepcopy(terminal), "outcome_rows": outcome, "hp_battles": battles}


def _game_order(segments: Sequence[Any]) -> list[Any]:
    ordered = sorted(segments, key=lambda s: s.header["segment_index"])
    if [s.header["segment_index"] for s in ordered] != list(range(len(ordered))):
        raise FastIntakeError("fast_labels_segments_not_contiguous")
    return ordered


def build_instance_labels(segments: Sequence[Any]) -> tuple[dict[str, Any], bytes]:
    """Sender helper: an instance's trainable `SealedSegment`s -> (fields, data).

    The caller adds `handoff_sequence` and `shm_name` and puts `data` in shared
    memory. Only segments a label row references are carried.
    """
    ordered = _game_order(segments)
    if not ordered or any(not s.header["trainable"] for s in ordered):
        raise FastIntakeError("fast_labels_need_trainable_segments")
    first = ordered[0].header
    if any(s.header["game_instance_id"] != first["game_instance_id"]
           or s.header["assembler_instance"] != first["assembler_instance"]
           or s.header["mu"]["training_generation"] != first["mu"]["training_generation"]
           for s in ordered):
        raise FastIntakeError("fast_labels_segments_of_two_instances")
    last = ordered[-1].header
    terminal = last["seal"]["terminal"] if last["seal"]["kind"] == "terminal" else None
    labels = instance_labels(first["game_instance_id"],
                             [fact for s in ordered for fact in row_facts(s.header)], terminal)
    wanted = {r["segment_id"] for r in labels["outcome_rows"]} | {
        r["segment_id"] for battle in labels["hp_battles"] for r in battle["states"]}
    parts, entries, cursor = [], [], 0
    for segment in ordered:
        if segment.segment_id in wanted:
            entries.append({"segment_id": segment.segment_id, "sha256": segment.sha256,
                            "offset": cursor, "size": len(segment.data)})
            parts.append(segment.data + bytes(-len(segment.data) % 8))
            cursor += len(parts[-1])
    data = b"".join(parts)
    return ({"schema": LABELS_SCHEMA, "assembler_id": first["assembler_instance"],
             "game_instance_id": first["game_instance_id"],
             "training_generation": first["mu"]["training_generation"], **labels,
             "segments": entries, "size": len(data), "sha256": sha256(data)}, data)


def check_instance_closed(message: Any) -> dict[str, Any]:
    if (not isinstance(message, dict) or set(message) != CLOSED_KEYS
            or message["schema"] != CLOSED_SCHEMA
            or not isinstance(message["game_instance_id"], str)
            or not message["game_instance_id"]
            or not isinstance(message["assembler_id"], str)
            or message["close_reason"] not in CLOSE_REASONS
            or not (message["failure"] is None or isinstance(message["failure"], str))
            or not isinstance(message["segments"], dict)
            or set(message["segments"]) != set(SEGMENT_KINDS)
            or any(type(v) is not int or v < 0 for v in message["segments"].values())):
        raise HandoffError("instance_closed_schema")
    return message


def _read_shm(name: str, size: int) -> bytes:
    """Exactly `size` bytes of the sender's named block; the sender owns and unlinks it.

    POSIX opens the block read-only by its shm name (the name CPython 3.11
    `multiprocessing.shared_memory` builds: a leading slash) and never touches the
    resource tracker: attaching with `SharedMemory(name=...)` registers the block and
    the needed `unregister` restarts the tracker check on every segment. A missing
    block raises (FileNotFoundError); a block smaller than `size`, or a read that
    ends early, is `handoff_size`. Linux reads with `pread`; macOS shm objects only
    support mmap (`pread` is ESPIPE there), so darwin copies from a read-only map.
    Windows keeps the `SharedMemory` attach (no POSIX shm, no resource tracker use).
    """
    if sys.platform == "win32":
        block = shared_memory.SharedMemory(name=name)
        try:
            if block.size < size:
                raise HandoffError("handoff_size")
            buffer = block.buf
            assert buffer is not None
            return bytes(buffer[:size])
        finally:
            block.close()
    fd = _posixshmem.shm_open("/" + name, os.O_RDONLY)
    try:
        # macOS rounds st_size up to the page size; Linux is exact.
        if os.fstat(fd).st_size < size:
            raise HandoffError("handoff_size")
        if size == 0:
            return b""
        if sys.platform == "darwin":
            with mmap.mmap(fd, size, prot=mmap.PROT_READ) as mapping:
                return mapping[:size]
        chunks = []
        done = 0
        while done < size:
            chunk = os.pread(fd, size - done, done)
            if not chunk:
                raise HandoffError("handoff_size")
            chunks.append(chunk)
            done += len(chunk)
        return chunks[0] if len(chunks) == 1 else b"".join(chunks)
    finally:
        os.close(fd)


def receive_handoff(connection: Connection) -> tuple[str, dict[str, Any]]:
    """The message half of `read_handoff`: schema and size checks, no shared memory
    read ("segment" / "labels" messages still name a block to read)."""
    message = connection.recv()
    schema = message.get("schema") if isinstance(message, dict) else None
    if schema == HANDOFF_END_SCHEMA:
        return "end", check_handoff_end(message)
    if schema == CLOSED_SCHEMA:
        return "closed", check_instance_closed(message)
    kind = "segment"
    if schema == LABELS_SCHEMA and set(message) == LABELS_KEYS:
        kind = "labels"
    elif schema != HANDOFF_SCHEMA or set(message) != HANDOFF_KEYS:
        raise HandoffError("handoff_schema")
    size = message["size"]
    if type(size) is not int or size < 0:
        raise HandoffError("handoff_size")
    return kind, message


def read_handoff(connection: Connection) -> tuple[str, dict[str, Any], bytes | None]:
    """One hand-off message: ("segment" | "labels", message, verified bytes),
    ("closed", message, None) or ("end", message, None) (the sender's orderly end).

    A segment is acked by the caller (`ack`) only after it has been taken over, so the
    sender's in-flight bound stays the one back-pressure point. (`fast_verify` does
    the same read, digest and header parse in a verify worker process instead.)
    """
    kind, message = receive_handoff(connection)
    if kind in ("end", "closed"):
        return kind, message, None
    size = message["size"]
    data = _read_shm(message["shm_name"], size)
    if sha256(data) != message["sha256"]:
        raise HandoffError("handoff_digest")
    return kind, message, data


def ack(connection: Connection, message: dict[str, Any]) -> None:
    connection.send({"schema": HANDOFF_SCHEMA, "ack": message["handoff_sequence"]})


@dataclass
class VerifiedRead:
    """A verify worker's result for one hand-off's exact bytes (`fast_verify`).

    Segment: the `decode_segment(header_only=True)` header and its `row_facts`,
    or the exception that parse raised. Labels: per carried entry (in message
    order) the sha256 of `data[offset:offset + size]` and, when asked, that
    body's header parse or its exception; None where the entry's offset/size
    do not name bytes inside `data` (the learner refuses such an entry first).
    """

    header: dict[str, Any] | None = None
    facts: list[dict[str, Any]] | None = None
    header_error: BaseException | None = None
    entries: list[tuple[str | None, dict[str, Any] | None, BaseException | None]] = field(
        default_factory=list)

    def segment_header(self) -> dict[str, Any]:
        if self.header_error is not None:
            raise self.header_error
        assert self.header is not None
        return self.header

    def entry_header(self, index: int) -> dict[str, Any]:
        _digest, header, error = self.entries[index]
        if error is not None:
            raise error
        assert header is not None
        return header


@dataclass
class FastSegment:
    """One verified trainable segment as held by the intake."""

    message: dict[str, Any]
    header: dict[str, Any]
    data: bytes

    @property
    def segment_id(self) -> str:
        return str(self.header["segment_id"])

    @property
    def instance(self) -> str:
        return str(self.header["game_instance_id"])

    @property
    def rows(self) -> int:
        return len(self.header["rows"])


@dataclass
class _Instance:
    attestation: dict[str, Any] | None = None
    assembler_id: str | None = None
    buffered: list[FastSegment] = field(default_factory=list)
    received: int = 0
    excluded: str | None = None
    closed: dict[str, Any] | None = None
    # Label rule inputs of every current-generation segment received, in order.
    facts: list[dict[str, Any]] | None = field(default_factory=list)
    segments: dict[str, tuple[int, str, int]] = field(default_factory=dict)
    terminal: dict[str, Any] | None = None
    labelled: bool = False


@dataclass
class IntakeDecision:
    """What the learner must do after one intake call (under its own lock)."""

    admit: list[tuple[FastSegment, dict[str, Any]]] = field(default_factory=list)
    exclude_instance: str | None = None
    reply: dict[str, Any] | None = None
    # Verified aux label groups: (kind, identity, rows); a row names its original
    # segment (`FastSegment`), the learner envelope, row index and label value.
    auxiliary: list[tuple[str, str, list[dict[str, Any]]]] = field(default_factory=list)


class FastSegmentIntake:
    """Per-instance attestation, pre-attestation buffer and exclusion; thread-safe.

    Owns no queue, RNG or model state. The learner applies an `IntakeDecision`:
    admit the listed segments to its queue, or drop the not-yet-used queued
    segments of `exclude_instance` and count the used ones.
    """

    def __init__(self, *, run: str, generation: str, excluded_seeds: frozenset[str] = frozenset(),
                 buffer_segments: int = 512, closed_memory: int = 65536,
                 gae_lambda: float = 0.99, published_memory: int = 65536,
                 aux_labels: frozenset[str] = frozenset()) -> None:
        if buffer_segments < 1 or closed_memory < 1 or published_memory < 1:
            raise ValueError("positive_fast_intake_bounds_required")
        if not aux_labels <= AUX_LABEL_KINDS:
            raise ValueError("fast_aux_label_kinds")
        self.aux_labels = frozenset(aux_labels)
        self.run, self.generation = run, generation
        self.excluded_seeds = excluded_seeds
        self.buffer_segments, self.closed_memory = buffer_segments, closed_memory
        self.gae_lambda = gae_lambda
        self.lock = threading.RLock()
        self.instances: dict[str, _Instance] = {}
        # Finished instances: id -> excluded reason (None = clean). Bounded, FIFO.
        self.finished: collections.OrderedDict[str, str | None] = collections.OrderedDict()
        # The assembler each finished instance closed under (fence diagnostics only).
        self.finished_by: dict[str, str] = {}
        self.buffer: collections.deque[FastSegment] = collections.deque()
        self.counters: dict[str, Any] = collections.defaultdict(int)
        self.excluded_by_reason: dict[str, int] = collections.defaultdict(int)
        # Every mu this learner published in the current generation, by snapshot
        # digest. A segment trains only under a mu found here with the same
        # version/updates; anything else is a broken service -> loud refusal.
        self.published: collections.OrderedDict[str, dict[str, Any]] = (
            collections.OrderedDict())
        self.published_memory = published_memory

    # -- generation -------------------------------------------------------------------
    def set_generation(self, generation: str) -> None:
        """A new training generation: nothing attested or buffered under the old one stays.

        Called on every hand-off/control message with the learner's (fixed)
        generation: an unchanged one returns without the lock (a str compare is
        atomic under the GIL; a real change is re-checked under the lock)."""
        if generation == self.generation:
            return
        with self.lock:
            if generation == self.generation:
                return
            self.counters["generation_dropped_segments"] += len(self.buffer)
            self.buffer.clear()
            self.instances.clear()
            self.finished.clear()
            self.finished_by.clear()
            self.published.clear()
            self.generation = generation

    # -- mu publication -----------------------------------------------------------
    def publish_mu(self, identity: dict[str, Any]) -> None:
        """Record one mu this learner published (its own publication identity)."""
        mu: dict[str, Any] = {key: identity.get(key) for key in MU_KEYS}
        if (not isinstance(mu["snapshot_sha256"], str) or len(mu["snapshot_sha256"]) != 64
                or not isinstance(mu["training_generation"], str)
                or any(type(mu[key]) is not int or mu[key] < 0
                       for key in ("version", "updates"))):
            raise FastIntakeError("fast_published_mu_identity_fields")
        with self.lock:
            if mu["training_generation"] != self.generation:
                raise FastIntakeError("fast_published_mu_generation_changed")
            previous = self.published.get(mu["snapshot_sha256"])
            if previous is not None and previous != mu:
                raise FastIntakeError("fast_published_mu_digest_reused")
            self.published[mu["snapshot_sha256"]] = mu
            self.published.move_to_end(mu["snapshot_sha256"])
            while len(self.published) > self.published_memory:
                self.published.popitem(last=False)

    # -- actor control ----------------------------------------------------------------
    def attest(self, request: dict[str, Any]) -> IntakeDecision:
        values: dict[str, str] = {key: request.get(key) for key in ADMISSION_FIELDS}  # type: ignore[misc]
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise FastIntakeError("fast_instance_admission_fields")
        with self.lock:
            if (values["run"], values["training_generation"]) != (self.run, self.generation):
                self.counters["admission_generation_refused"] += 1
                return IntakeDecision(reply={"admitted": False, "reason": "generation_changed"})
            try:
                bp.validate_training_source({"training_split": values["training_split"],
                                             "identity": {"seed": values["seed"]}},
                                            self.excluded_seeds)
            except ValueError:
                self.counters["admission_seed_refused"] += 1
                return IntakeDecision(reply={"admitted": False, "reason": "seed_held_out"})
            instance_id = values["game_instance_id"]
            attestation = {key: values[key] for key in ("actor", "intent_id", "seed",
                                                         "training_split")}
            if instance_id in self.finished:
                self.counters["admission_after_close_refused"] += 1
                return IntakeDecision(reply={"admitted": False, "reason": "instance_closed"})
            instance = self.instances.setdefault(instance_id, _Instance())
            if instance.attestation is not None:
                if instance.attestation != attestation:
                    raise FastIntakeError("fast_instance_attested_twice_differently")
                return IntakeDecision(reply={"admitted": True, "game_instance_id": instance_id})
            instance.attestation = attestation
            self.counters["attested"] += 1
            decision = IntakeDecision(reply={"admitted": True, "game_instance_id": instance_id})
            if instance.excluded is None:
                for segment in instance.buffered:
                    self.buffer.remove(segment)
                    decision.admit.append((segment, self._envelope(segment, attestation)))
                self.counters["buffered_admitted"] += len(instance.buffered)
            instance.buffered.clear()
            return decision

    def exclude(self, request: dict[str, Any]) -> IntakeDecision:
        values: dict[str, str] = {key: request.get(key) for key in EXCLUSION_FIELDS}  # type: ignore[misc]
        if (any(not isinstance(value, str) or not value for value in values.values())
                or not _REASON.fullmatch(values["reason"])):
            raise FastIntakeError("fast_instance_excluded_fields")
        with self.lock:
            if (values["run"], values["training_generation"]) != (self.run, self.generation):
                return IntakeDecision(reply={"excluded": False, "reason": "generation_changed"})
            instance_id = values["game_instance_id"]
            reply = {"excluded": True, "game_instance_id": instance_id}
            return self._exclude(instance_id, "actor:" + values["reason"], reply)

    # -- hand-off ---------------------------------------------------------------------
    def offer(self, message: dict[str, Any], data: bytes,
              checked: VerifiedRead | None = None) -> IntakeDecision:
        """One verified hand-off segment (bytes already match message sha256).

        `read_handoff` hashed these bytes once; the header is parsed without a
        second whole-segment or per-row hash (the decode worker binds every row
        to its digest before any training use). `checked` (a verify worker's
        result for these exact bytes) carries that same header parse and its row
        facts, or the parse's own exception, raised here at the same point."""
        if checked is None:
            header, _rows = decode_segment(data, header_only=True)
        else:
            header = checked.segment_header()
        if (header["segment_id"] != message["segment_id"] or not header["trainable"]
                or len(header["rows"]) != message["rows"]
                or header["assembler_instance"] != message["assembler_id"]
                or header["mu"]["training_generation"] != message["training_generation"]
                or header["mu"]["snapshot_sha256"] != message["mu_snapshot_sha256"]):
            raise HandoffError("handoff_segment_binding")
        segment = FastSegment(dict(message), header, data)
        # Pure functions of this header: built before the lock (readers stay parallel).
        facts = row_facts(header) if checked is None or checked.facts is None else checked.facts
        terminal = (copy.deepcopy(header["seal"]["terminal"])
                    if header["seal"]["kind"] == "terminal" else None)
        with self.lock:
            self.counters["received"] += 1
            self.counters["received_rows"] += segment.rows
            if message["training_generation"] != self.generation:
                # A mu of an older generation: the W2 rule never trains it here.
                self.counters["old_generation_dropped"] += 1
                return IntakeDecision()
            if self.published.get(header["mu"]["snapshot_sha256"]) != header["mu"]:
                # Not a mu this learner published (or published under another
                # version/updates): never trained, never repaired.
                self.counters["unpublished_mu_refused"] += 1
                raise FastIntakeError("fast_segment_mu_not_published")
            instance_id = segment.instance
            if instance_id in self.finished:
                raise _identity_fence("fast_segment_after_instance_closed", instance_id,
                                      message["assembler_id"],
                                      self.finished_by.get(instance_id))
            instance = self.instances.setdefault(instance_id, _Instance())
            if instance.assembler_id not in (None, message["assembler_id"]):
                raise _identity_fence("fast_instance_from_two_assemblers", instance_id,
                                      message["assembler_id"], instance.assembler_id)
            if instance.assembler_id is None:
                instance.assembler_id = message["assembler_id"]
                self._bound(instance_id)
            instance.received += 1
            if instance.facts is not None:
                if (header["segment_index"] != len(instance.segments)
                        or instance.terminal is not None):
                    # A generation drop (or a rejected segment) left a gap: the
                    # old rule needs the whole game, so this game gets no labels.
                    instance.facts = None
                    self.counters["label_facts_gap"] += 1
                else:
                    instance.segments[segment.segment_id] = (
                        header["segment_index"], message["sha256"], len(data))
                    instance.facts.extend(facts)
                    if header["seal"]["kind"] == "terminal":
                        instance.terminal = terminal
            if instance.excluded is not None:
                self._count_excluded(instance.excluded, segment.rows, used=False)
                return IntakeDecision()
            if instance.attestation is None:
                self._buffer(instance, segment)
                return IntakeDecision()
            return IntakeDecision(admit=[(segment, self._envelope(segment,
                                                                  instance.attestation))])

    def _bound(self, instance_id: str) -> None:
        """Called under the lock when a hand-off first binds an instance to its
        assembler (the only place a binding is made). Nothing to do here; the
        data-plane shard's intake reports it learner-wide."""

    def labels(self, message: dict[str, Any], data: bytes,
               checked: VerifiedRead | None = None) -> IntakeDecision:
        """One verified labels hand-off (`data` already matches message sha256).

        Every label is recomputed here from the headers this intake received; the
        carried original segments must be exactly the ones received. Any
        difference fences (raises); nothing is repaired or partly used.

        Three phases so the intake lock never covers hashing or decoding: (1)
        under the lock, claim the instance (`labelled`) and snapshot its label
        inputs; (2) unlocked, recompute the rule, bind every carried body to the
        digest received with its segment and decode its header; (3) under the
        lock, use the result only if the same instance object is still current
        in the same generation (else counted as an old-generation drop).

        `checked` (a verify worker's result for these exact bytes) supplies each
        carried entry's body digest and header parse; every comparison, its
        order and its error stay here.
        """
        if set(message) != LABELS_KEYS or message["schema"] != LABELS_SCHEMA:
            raise HandoffError("handoff_schema")
        instance_id = message["game_instance_id"]
        with self.lock:
            self.counters["labels_received"] += 1
            if message["training_generation"] != self.generation:
                self.counters["labels_old_generation_dropped"] += 1
                return IntakeDecision()
            if instance_id in self.finished:
                raise _identity_fence("fast_labels_after_instance_closed", instance_id,
                                      message["assembler_id"],
                                      self.finished_by.get(instance_id))
            instance = self.instances.get(instance_id)
            if instance is None or instance.assembler_id != message["assembler_id"]:
                raise FastIntakeError("fast_labels_for_unknown_instance")
            if instance.labelled:
                raise FastIntakeError("fast_labels_twice")
            instance.labelled = True
            if instance.facts is None:
                self.counters["labels_incomplete_instance"] += 1
                return IntakeDecision()
            # Segments of this instance arrive in order on this same Connection
            # (this thread), so nothing appends to these while we work unlocked;
            # shallow copies keep the snapshot exact regardless.
            generation = self.generation
            facts = list(instance.facts)
            terminal = instance.terminal
            received = dict(instance.segments)
        expected = instance_labels(instance_id, facts, terminal)
        if any(message[key] != expected[key]
               for key in ("terminal", "outcome_rows", "hp_battles")):
            raise FastIntakeError("fast_labels_rule_changed")
        wanted = {r["segment_id"] for r in expected["outcome_rows"]} | {
            r["segment_id"] for battle in expected["hp_battles"] for r in battle["states"]}
        order = sorted(wanted, key=lambda sid: received[sid][0])
        entries = message["segments"]
        if (not isinstance(entries, list) or len(data) != message["size"]
                or [entry.get("segment_id") for entry in entries] != order):
            raise FastIntakeError("fast_labels_segments_changed")
        bodies: dict[str, tuple[str, bytes, int]] = {}
        cursor = 0
        for index, entry in enumerate(entries):
            _index, digest, size = received[entry["segment_id"]]
            pad = -size % 8
            if (set(entry) != {"segment_id", "sha256", "offset", "size"}
                    or entry["sha256"] != digest or entry["size"] != size
                    or entry["offset"] != cursor or cursor + size + pad > len(data)
                    or any(data[cursor + size:cursor + size + pad])):
                raise FastIntakeError("fast_labels_segments_changed")
            body = data[cursor:cursor + size]
            actual = sha256(body) if checked is None else checked.entries[index][0]
            if actual != digest:
                raise FastIntakeError("fast_labels_segment_bytes_changed")
            bodies[entry["segment_id"]] = digest, body, index
            cursor += size + pad
        if cursor != len(data):
            raise FastIntakeError("fast_labels_segments_changed")
        headers: dict[str, dict[str, Any]] = {}
        if self.aux_labels:
            for sid, (_digest, body, index) in bodies.items():
                # Header, layout, padding and length checks only, outside the
                # lock. The whole body was bound to `digest` (the digest offer()
                # received) just above, and the decode worker binds every row to
                # its own sha256 when the source is prepared, so no row is
                # copied or hashed here.
                if checked is None:
                    header, _rows = decode_segment(body, header_only=True)
                else:
                    header = checked.entry_header(index)
                headers[sid] = header
        with self.lock:
            if self.generation != generation or self.instances.get(instance_id) is not instance:
                # The generation changed while this ran unlocked: never used here.
                self.counters["labels_old_generation_dropped"] += 1
                return IntakeDecision()
            if instance.excluded is not None or instance.attestation is None:
                self.counters["labels_untrained_instance"] += 1
                return IntakeDecision()
            if not self.aux_labels:
                self.counters["labels_disabled"] += 1
                return IntakeDecision()
            segments = {}
            for sid, (digest, body, _index) in bodies.items():
                segment = FastSegment({"segment_id": sid, "sha256": digest}, headers[sid], body)
                segments[sid] = segment, self._envelope(segment, instance.attestation)
            groups = []
            if "terminal" in self.aux_labels and expected["terminal"] is not None:
                self.counters["labels_terminal_rows"] += len(expected["outcome_rows"])
            if "hp" in self.aux_labels:
                for battle in expected["hp_battles"]:
                    self.counters["labels_hp_rows"] += len(battle["states"])

        def rows(refs: list[dict[str, Any]], label: float) -> list[dict[str, Any]]:
            return [{"segment": segments[r["segment_id"]][0],
                     "envelope": segments[r["segment_id"]][1], "row": r["row"],
                     "step_index": r["step_index"], "label": label} for r in refs]

        if "terminal" in self.aux_labels and expected["terminal"] is not None:
            groups.append(("terminal", instance_id,
                           rows(expected["outcome_rows"], expected["terminal"]["class"])))
        if "hp" in self.aux_labels:
            for battle in expected["hp_battles"]:
                groups.append(("hp", battle["battle_id"],
                               rows(battle["states"], battle["post_hp"] / 100)))
        return IntakeDecision(auxiliary=groups)

    def closed(self, message: dict[str, Any]) -> IntakeDecision:
        check_instance_closed(message)
        instance_id = message["game_instance_id"]
        with self.lock:
            if instance_id in self.finished:
                raise _identity_fence("fast_instance_closed_twice", instance_id,
                                      message["assembler_id"],
                                      self.finished_by.get(instance_id))
            instance = self.instances.setdefault(instance_id, _Instance())
            if instance.assembler_id not in (None, message["assembler_id"]):
                raise _identity_fence("fast_instance_from_two_assemblers", instance_id,
                                      message["assembler_id"], instance.assembler_id)
            sealed = message["segments"]["bootstrap"] + message["segments"]["terminal"]
            if instance.received != sealed:
                # Hand-off is in order on one Connection: fewer can only mean a
                # generation drop counted above; more is a broken sender.
                if instance.received > sealed:
                    raise FastIntakeError("fast_instance_received_more_than_sealed")
                self.counters["closed_segment_count_short"] += 1
            instance.closed = dict(message)
            self.counters["closed_" + message["close_reason"]] += 1
            if (self.aux_labels and not instance.labelled and instance.excluded is None
                    and message["close_reason"] == "terminal"):
                self.counters["closed_without_labels"] += 1
            reason = None
            failure = message["failure"] or ""
            if message["close_reason"] in EXCLUDING_CLOSE_REASONS:
                reason = "close:" + message["close_reason"]
            elif failure.startswith("unknown:"):
                reason = "failure:" + failure
            decision = IntakeDecision()
            if reason is not None:
                decision = self._exclude(instance_id, reason, None)
            if instance.attestation is None:
                # Never attested (yet): its buffered segments are truncated, not trained.
                for segment in instance.buffered:
                    self.buffer.remove(segment)
                    self.counters["unattested_dropped"] += 1
                    self.counters["unattested_dropped_rows"] += segment.rows
                instance.buffered.clear()
            self._finish(instance_id, instance)
            return decision

    def report(self) -> dict[str, Any]:
        with self.lock:
            return {**self.counters, "excluded_by_reason": dict(self.excluded_by_reason),
                    "open_instances": len(self.instances), "buffered": len(self.buffer)}

    def note_excluded_rows(self, reason: str, *, dropped: int, used: int) -> None:
        """The learner reports what an exclusion removed from / left in its queue."""
        with self.lock:
            self._count_excluded(reason, dropped, used=False)
            self._count_excluded(reason, used, used=True)

    # -- internals --------------------------------------------------------------------
    def _envelope(self, segment: FastSegment, attestation: dict[str, Any]) -> dict[str, Any]:
        return fast_envelope(segment.header, size=len(segment.data),
                             digest=segment.message["sha256"], run=self.run,
                             attestation=attestation, gae_lambda=self.gae_lambda)

    def _buffer(self, instance: _Instance, segment: FastSegment) -> None:
        while len(self.buffer) >= self.buffer_segments:
            oldest = self.buffer.popleft()
            owner = self.instances.get(oldest.instance)
            if owner is not None:
                owner.buffered.remove(oldest)
            self.counters["pre_attestation_overflow_dropped"] += 1
            self.counters["pre_attestation_overflow_rows"] += oldest.rows
        instance.buffered.append(segment)
        self.buffer.append(segment)
        self.counters["buffered"] += 1

    def _exclude(self, instance_id: str, reason: str,
                 reply: dict[str, Any] | None) -> IntakeDecision:
        finished = self.finished.get(instance_id, "missing")
        if finished != "missing":
            # Close already processed (actor signal after the hand-off close).
            if finished is None:
                self.finished[instance_id] = reason
                self.excluded_by_reason[reason] += 1
                return IntakeDecision(exclude_instance=instance_id, reply=reply)
            return IntakeDecision(reply=reply)
        instance = self.instances.setdefault(instance_id, _Instance())
        if instance.excluded is not None:
            return IntakeDecision(reply=reply)
        instance.excluded = reason
        self.excluded_by_reason[reason] += 1
        for segment in instance.buffered:
            self.buffer.remove(segment)
            self._count_excluded(reason, segment.rows, used=False)
        instance.buffered.clear()
        return IntakeDecision(exclude_instance=instance_id, reply=reply)

    def excluded(self, instance_id: str) -> bool:
        """Whether this instance is excluded now (checked again at admission:
        an exclusion may be applied between an admit decision and its use)."""
        with self.lock:
            instance = self.instances.get(instance_id)
            if instance is not None:
                return instance.excluded is not None
            return self.finished.get(instance_id) is not None

    def _count_excluded(self, reason: str, rows: int, *, used: bool) -> None:
        if rows:
            self.counters["excluded_used_rows" if used else "excluded_dropped_rows"] += rows

    def _finish(self, instance_id: str, instance: _Instance) -> None:
        if instance.closed is None:
            return
        self.instances.pop(instance_id, None)
        self.finished[instance_id] = instance.excluded
        self.finished_by[instance_id] = instance.closed["assembler_id"]
        while len(self.finished) > self.closed_memory:
            self.finished_by.pop(self.finished.popitem(last=False)[0], None)


class FastSources:
    """In-memory verified fast segment bytes, pinned by named owners (BinarySourceProvider).

    Bytes enter once (hand-off copy, sha256 already checked) and leave when the
    last owner releases them. Unlike the actor upload spool there is no byte
    credit wait here: the learner bounds what it admits by queue rows and drops
    the oldest queued fast segment when full (decision of 11-09, counted).
    """

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.entries: dict[Any, dict[str, Any]] = {}
        self.bytes = 0
        self.peak_bytes = 0

    def put(self, ref: Any, envelope: dict[str, Any], data: bytes, owner: str) -> None:
        if (ref.format != FAST_FORMAT or len(data) != ref.size
                or envelope.get("packet_sha256") != ref.sha256
                or envelope.get("packet_size") != ref.size):
            raise FastIntakeError("fast_source_reference_changed")
        with self.lock:
            if ref in self.entries:
                raise FastIntakeError("fast_source_admitted_twice")
            self.entries[ref] = {"data": data, "envelope": dict(envelope), "owners": {owner}}
            self.bytes += len(data)
            self.peak_bytes = max(self.peak_bytes, self.bytes)

    def envelope(self, ref: Any) -> dict[str, Any]:
        with self.lock:
            return dict(self.entries[ref]["envelope"])

    def envelopes(self, refs: Any) -> list[dict[str, Any]]:
        """`envelope` for many refs under one lock hold."""
        with self.lock:
            return [dict(self.entries[ref]["envelope"]) for ref in refs]

    def retain(self, ref: Any, owner: str) -> None:
        with self.lock:
            entry = self.entries.get(ref)
            if entry is None:
                raise FastIntakeError("fast_source_already_released")
            entry["owners"].add(owner)

    def release(self, ref: Any, owner: str) -> None:
        with self.lock:
            entry = self.entries[ref]
            entry["owners"].remove(owner)
            if not entry["owners"]:
                del self.entries[ref]
                self.bytes -= ref.size

    def borrow(self, ref: Any) -> Any:
        from contextlib import contextmanager

        @contextmanager
        def view() -> Any:
            with self.lock:
                entry = self.entries.get(ref)
                if entry is None:
                    raise FastIntakeError("fast_source_already_released")
                data = entry["data"]
            yield memoryview(data)

        return view()

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {"fast_source_segments": len(self.entries), "fast_source_bytes": self.bytes,
                    "fast_source_peak_bytes": self.peak_bytes}


class RoutedSources:
    """One provider for the binary preparer: fast refs here, actor uploads in the spool."""

    def __init__(self, uploads: Any, fast: FastSources) -> None:
        self.uploads, self.fast = uploads, fast

    def _owner(self, ref: Any) -> Any:
        return self.fast if ref.format == FAST_FORMAT else self.uploads

    def borrow(self, ref: Any) -> Any:
        return self._owner(ref).borrow(ref)

    def retain(self, ref: Any, owner: str) -> None:
        self._owner(ref).retain(ref, owner)

    def release(self, ref: Any, owner: str) -> None:
        self._owner(ref).release(ref, owner)

    def envelope(self, ref: Any) -> dict[str, Any]:
        return dict(self._owner(ref).envelope(ref))
