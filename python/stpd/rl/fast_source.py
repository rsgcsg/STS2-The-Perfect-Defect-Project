"""Learner side of the fast_chain segment hand-off (C-SCALE / WP8, `fast_chain` only).

Three pieces, none used by the old chain:

- `decode_fast_packet`: one sealed `stpd/rl-fast-segment-1` -> `PreparedSource` plus
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
  behavior for verified prefixes). An instance never attested is truncated.
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
"""

from __future__ import annotations

import collections
import os
import re
import threading
import time
from dataclasses import dataclass, field
from multiprocessing import resource_tracker, shared_memory
from multiprocessing.connection import Connection
from typing import Any

import torch

from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.rows_encoder_wire import decode_row

from . import batch_preparation as bp
from .fast_handoff import HANDOFF_KEYS, HANDOFF_SCHEMA, HandoffError
from .fast_segment import FORMAT as FAST_FORMAT
from .fast_segment import decode_segment, sha256
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


class FastIntakeError(ValueError):
    """A broken fast-chain contract on the learner side: loud, never repaired."""


def fast_envelope(header: dict[str, Any], *, size: int, digest: str, run: str,
                  attestation: dict[str, Any], gae_lambda: float) -> dict[str, Any]:
    """The learner's own record of one admitted segment (never sent anywhere)."""
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


def read_handoff(connection: Connection) -> tuple[str, dict[str, Any], bytes | None]:
    """One hand-off message: ("segment", message, verified bytes) or ("closed", message, None).

    A segment is acked by the caller (`ack`) only after it has been taken over, so the
    sender's in-flight bound stays the one back-pressure point.
    """
    message = connection.recv()
    schema = message.get("schema") if isinstance(message, dict) else None
    if schema == CLOSED_SCHEMA:
        return "closed", check_instance_closed(message), None
    if schema != HANDOFF_SCHEMA or set(message) != HANDOFF_KEYS:
        raise HandoffError("handoff_schema")
    block = shared_memory.SharedMemory(name=message["shm_name"])
    try:
        # The sender owns and unlinks the block; this process must not track it.
        resource_tracker.unregister(block._name, "shared_memory")  # type: ignore[attr-defined]
        if type(message["size"]) is not int or block.size < message["size"]:
            raise HandoffError("handoff_size")
        buffer = block.buf
        assert buffer is not None
        data = bytes(buffer[:message["size"]])
    finally:
        block.close()
    if sha256(data) != message["sha256"]:
        raise HandoffError("handoff_digest")
    return "segment", message, data


def ack(connection: Connection, message: dict[str, Any]) -> None:
    connection.send({"schema": HANDOFF_SCHEMA, "ack": message["handoff_sequence"]})


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


@dataclass
class IntakeDecision:
    """What the learner must do after one intake call (under its own lock)."""

    admit: list[tuple[FastSegment, dict[str, Any]]] = field(default_factory=list)
    exclude_instance: str | None = None
    reply: dict[str, Any] | None = None


class FastSegmentIntake:
    """Per-instance attestation, pre-attestation buffer and exclusion; thread-safe.

    Owns no queue, RNG or model state. The learner applies an `IntakeDecision`:
    admit the listed segments to its queue, or drop the not-yet-used queued
    segments of `exclude_instance` and count the used ones.
    """

    def __init__(self, *, run: str, generation: str, excluded_seeds: frozenset[str] = frozenset(),
                 buffer_segments: int = 512, closed_memory: int = 65536,
                 gae_lambda: float = 0.99) -> None:
        if buffer_segments < 1 or closed_memory < 1:
            raise ValueError("positive_fast_intake_bounds_required")
        self.run, self.generation = run, generation
        self.excluded_seeds = excluded_seeds
        self.buffer_segments, self.closed_memory = buffer_segments, closed_memory
        self.gae_lambda = gae_lambda
        self.lock = threading.RLock()
        self.instances: dict[str, _Instance] = {}
        # Finished instances: id -> excluded reason (None = clean). Bounded, FIFO.
        self.finished: collections.OrderedDict[str, str | None] = collections.OrderedDict()
        self.buffer: collections.deque[FastSegment] = collections.deque()
        self.counters: dict[str, Any] = collections.defaultdict(int)
        self.excluded_by_reason: dict[str, int] = collections.defaultdict(int)

    # -- generation -------------------------------------------------------------------
    def set_generation(self, generation: str) -> None:
        """A new training generation: nothing attested or buffered under the old one stays."""
        with self.lock:
            if generation == self.generation:
                return
            self.counters["generation_dropped_segments"] += len(self.buffer)
            self.buffer.clear()
            self.instances.clear()
            self.finished.clear()
            self.generation = generation

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
    def offer(self, message: dict[str, Any], data: bytes) -> IntakeDecision:
        """One verified hand-off segment (bytes already match message sha256)."""
        header, _rows = decode_segment(data, expected_sha256=message["sha256"])
        if (header["segment_id"] != message["segment_id"] or not header["trainable"]
                or len(header["rows"]) != message["rows"]
                or header["assembler_instance"] != message["assembler_id"]
                or header["mu"]["training_generation"] != message["training_generation"]
                or header["mu"]["snapshot_sha256"] != message["mu_snapshot_sha256"]):
            raise HandoffError("handoff_segment_binding")
        segment = FastSegment(dict(message), header, data)
        with self.lock:
            self.counters["received"] += 1
            self.counters["received_rows"] += segment.rows
            if message["training_generation"] != self.generation:
                # A mu of an older generation: the W2 rule never trains it here.
                self.counters["old_generation_dropped"] += 1
                return IntakeDecision()
            instance_id = segment.instance
            if instance_id in self.finished:
                raise FastIntakeError("fast_segment_after_instance_closed")
            instance = self.instances.setdefault(instance_id, _Instance())
            if instance.assembler_id not in (None, message["assembler_id"]):
                raise FastIntakeError("fast_instance_from_two_assemblers")
            instance.assembler_id = message["assembler_id"]
            instance.received += 1
            if instance.excluded is not None:
                self._count_excluded(instance.excluded, segment.rows, used=False)
                return IntakeDecision()
            if instance.attestation is None:
                self._buffer(instance, segment)
                return IntakeDecision()
            return IntakeDecision(admit=[(segment, self._envelope(segment,
                                                                  instance.attestation))])

    def closed(self, message: dict[str, Any]) -> IntakeDecision:
        check_instance_closed(message)
        instance_id = message["game_instance_id"]
        with self.lock:
            if instance_id in self.finished:
                raise FastIntakeError("fast_instance_closed_twice")
            instance = self.instances.setdefault(instance_id, _Instance())
            if instance.assembler_id not in (None, message["assembler_id"]):
                raise FastIntakeError("fast_instance_from_two_assemblers")
            sealed = message["segments"]["bootstrap"] + message["segments"]["terminal"]
            if instance.received != sealed:
                # Hand-off is in order on one Connection: fewer can only mean a
                # generation drop counted above; more is a broken sender.
                if instance.received > sealed:
                    raise FastIntakeError("fast_instance_received_more_than_sealed")
                self.counters["closed_segment_count_short"] += 1
            instance.closed = dict(message)
            self.counters["closed_" + message["close_reason"]] += 1
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

    def _count_excluded(self, reason: str, rows: int, *, used: bool) -> None:
        if rows:
            self.counters["excluded_used_rows" if used else "excluded_dropped_rows"] += rows

    def _finish(self, instance_id: str, instance: _Instance) -> None:
        if instance.closed is None:
            return
        self.instances.pop(instance_id, None)
        self.finished[instance_id] = instance.excluded
        while len(self.finished) > self.closed_memory:
            self.finished.popitem(last=False)


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
