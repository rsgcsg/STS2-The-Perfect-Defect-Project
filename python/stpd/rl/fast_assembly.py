"""fast_chain inference-side segment assembly and temporary mu handoff (WP5).

Runs beside the GPU inference service (same process, another thread, or a
sibling on the same machine). The service calls it around every scored row; it
owns segment membership, the segment's mu binding, the tail value naming the
old mu, truncation and the seal. It decides no legality, holds no World, never
sees a seed and never resends a score.

Per row, the service does:

    plan = assembler.begin_row(row, row_bytes)      # before the forward
    # forward the row with plan.mu (policy + value);
    # if plan.tail is not None and plan.tail.mu != plan.mu.snapshot_sha256,
    #   also run the value head of plan.tail.mu on this row (floor plan.tail.floor)
    assembler.finish_row(plan, scored, outcome_probabilities, tail_outcome_probabilities)
    # or, when the service refuses the row:
    assembler.abort_row(plan, error_code)

and forwards `episode_open`, `step_outcome` and `episode_close` unchanged. Sealed
segments go to `emit` (normally `fast_handoff.SegmentHandoff.submit`), which
writes them to local disk with fsync before any hand-off to SGD.

Rules (design v2 §2.4–§2.6, `stpd/rl-inference-3` README):
- a segment holds <= 64 rows of one game instance; it binds the latest resident
  mu when it opens and keeps it for every row; a newer mu applies from the next
  segment on;
- the tail value of a full segment is the *segment* mu's value on the successor
  row (the next row of the same game), so that mu stays resident until the seal;
- a mu is evicted only when no unsealed segment references it; at most
  `max_resident` (default 3) are resident; an offer that would exceed it while
  every resident mu is referenced is declined (the learner offers a later one);
- `single_choice: omit` rows never reach the service; the next outcome's
  `forced_steps` is kept per row and summed per segment;
- a game closed other than by a labelled natural terminal seals its unsealed
  segments `truncated`: kept as original, counted, never given to SGD;
- every close (normal or not) then emits one `InstanceClosed` (close reason,
  first failure, segment counts) to `on_closed`, after the instance's segments:
  the learner's game-ended / exclusion signal (WP8; the learner decides what an
  exclusion drops, `fast_handoff` documents the wire);
- the environment's outcome fields are copied unchanged, including `combat_end`,
  which nothing here reads before the Connector fair-projection review;
- a natural terminal is labelled by `public_terminal_label` (lead decision
  2026-11-09): the old chain's `boss_clearance`, `episode_reward` (A0) and
  `checked_terminal_class`, called directly on public facts only (Bosses
  cleared = public act advances). A label the old chain refuses seals
  `truncated` (`terminal_label_refused`, counted); a labeler that breaks its
  contract seals `truncated` (`terminal_label_invalid`) and raises. Missing
  auxiliary labels never truncate: no auxiliary rows are produced here;
- `episode_open.audit_retain_mu` (R7 games): every temporary mu such a game's
  segments bind is archived (`mu_archive`, weights bound by sha256) before any
  of its rows is scored with it, so the complete-chain replay can rescore.

Instance identity: every start of an assembler is a new instance,
`<assembler_id>.<random 12 hex>`. Segment ids (`<instance>.<n>`) and the SGD
lease keys (`<instance>/<handoff_sequence>`) live under it, so a crash restart
never repeats an id; `DurableSegmentWriter` refuses to start when originals of
the same instance already exist. A crash loses only what was in memory: games
open at the crash are unknown to the new instance (their later rows are refused
-> `not_started`, late outcomes counted), and their unsealed rows are never
written; see `fast_handoff` for what "disk first" does not cover.

Exception paths keep originals: a segment is encoded before it leaves its game,
an encoder refusal becomes a `RejectedSegment` original (written to
`rejected/`, counted, raised), and every public method hands what it sealed to
`emit` even when it raises.

Temporary mu (E5 publication change): a `MuHandoff` names the snapshot the
learner pushed to the service every few seconds. It is not a Store asset; its
identity is the snapshot digest plus training generation, version and update
count. Retained originals stay self-describing without the weights: every row
records the actual q vector and the mu's value output. Replaying scores (R7)
needs the weights: the service passes them in `MuHandoff.weights` (not part of
the identity) and the assembler archives them for R7 games.
"""
from __future__ import annotations

import copy
import math
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from stpd.p1.trajectory import boss_clearance

from .fast_segment import (
    FORMAT,
    FORMAT_2,
    FORMATS,
    MAX_ROWS,
    ROW_FORMAT,
    InstanceClosed,
    RejectedSegment,
    SealedSegment,
    SegmentFormatError,
    _same_json,
    check_value_distribution,
    decode_segment,
    encode_segment,
    encode_segment_columns,
    sha256,
    unit_numbers,
)
from .heads import checked_terminal_class
from .reward import episode_reward

OUTCOME_FIELDS = ("status", "reason", "receipt_sha256", "public_facts", "forced_steps",
                  "combat_end", "terminal")
SINGLE_CHOICE = ("score", "omit")
TerminalLabeler = Callable[[Mapping[str, Any]], "Mapping[str, Any] | None"]
MuArchive = Callable[["MuHandoff"], None]
TERMINAL_RESULTS = {"victory": "win", "defeat": "loss"}
_ATOMIC = frozenset({int, float, str, bool, type(None)})
_FLOAT = {float}


def _owned(value: Any) -> Any:
    """`copy.deepcopy` for wire values, without its memo: scalars are shared as they are.

    Messages come from JSON/pickle unpacking (trees, no shared parts, no
    cycles); anything else still goes through `copy.deepcopy`.
    """
    kind = type(value)
    if kind in _ATOMIC:
        return value
    if kind is dict:
        return {key: _owned(item) for key, item in value.items()}
    if kind is list:
        return [_owned(item) for item in value]
    return copy.deepcopy(value)


# The fast chain serves A0 only (lead decision 2026-11-09); the gate is WP7's
# (actor side, ascension != 0 refused at game open). The inference side does not
# know the ascension, and the old chain's class table is the A0 one.
LABEL_ASCENSION = 0


def public_terminal_label(context: Mapping[str, Any]) -> dict[str, Any]:
    """The old chain's label path, called directly, on public facts only.

    `trajectory.boss_clearance` over the public act sequence (its act-advance
    witness) -> `reward.episode_reward` for an A0 natural terminal ->
    `heads.checked_terminal_class`. No combat_end, no Store. Raises ValueError
    when the old chain gives no reward or its class check refuses it.
    """
    result = TERMINAL_RESULTS.get(str(context.get("result")))
    floor, acts = context.get("max_floor"), context.get("acts")
    if result is None or type(floor) is not int or not isinstance(acts, list):
        raise ValueError("terminal_label_context")
    observations = [{"index": index, "observation": {"run": {"content": {"act": act}}}}
                    for index, act in enumerate(acts)]
    reward, disposition = episode_reward({
        "identity": {"ascension": LABEL_ASCENSION},
        "outcome": {"status": "finished", "unknown": False,
                    "terminal": {"status": "observed", "result": result},
                    "max_observed_floor": floor,
                    "boss_clearance": boss_clearance(observations)}})
    if reward is None or disposition != result:
        raise ValueError(f"terminal_reward_{disposition}")
    return {"class": checked_terminal_class(result=disposition, floor=floor, reward=reward),
            "reward": float(reward)}


class AssemblyError(ValueError):
    """A protocol violation by the caller; the state is left unchanged."""


class MuNotResident(AssemblyError):
    """No mu has been offered yet (service answers `MU_NOT_RESIDENT`)."""


@dataclass(frozen=True)
class MuHandoff:
    snapshot_sha256: str
    training_generation: str
    version: int
    updates: int
    # The serialized weights of this snapshot, only needed to archive mu for R7
    # games; not part of the identity.
    weights: bytes | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.snapshot_sha256, str) or len(self.snapshot_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.snapshot_sha256)
                or not isinstance(self.training_generation, str) or not self.training_generation
                or type(self.version) is not int or type(self.updates) is not int
                or self.version < 0 or self.updates < 0
                or not (self.weights is None or isinstance(self.weights, bytes))):
            raise AssemblyError("invalid_mu_handoff")

    def identity(self) -> dict[str, Any]:
        return {"snapshot_sha256": self.snapshot_sha256,
                "training_generation": self.training_generation,
                "version": self.version, "updates": self.updates}


@dataclass(frozen=True)
class MuOffer:
    accepted: bool
    evicted: tuple[str, ...]
    generation_changed: bool = False


class MuLedger:
    """Residency and references of temporary mu; the service mirrors `evicted`."""

    def __init__(self, max_resident: int = 3) -> None:
        if type(max_resident) is not int or max_resident < 2:
            raise AssemblyError("mu_ledger_requires_two_resident")
        self.max_resident = max_resident
        self.resident: OrderedDict[str, MuHandoff] = OrderedDict()
        self.references: dict[str, int] = {}
        self.pins: dict[str, int] = {}
        self.latest: MuHandoff | None = None
        self.declined = 0

    def check(self, handoff: MuHandoff) -> None:
        """Refuse, before any state changes, a resident snapshot under another identity.

        The same snapshot under a new training generation (learner CP restore with
        unchanged weights) is not a conflict: `offer` registers it under the new one.
        """
        resident = self.resident.get(handoff.snapshot_sha256)
        if (resident is not None and resident != handoff
                and resident.training_generation == handoff.training_generation):
            raise AssemblyError("mu_snapshot_identity_changed")

    def offer(self, handoff: MuHandoff) -> MuOffer:
        self.check(handoff)
        if handoff.snapshot_sha256 in self.resident:
            if self.resident[handoff.snapshot_sha256] != handoff:  # new generation
                self.resident[handoff.snapshot_sha256] = handoff
                self.latest = handoff
                return MuOffer(True, self.collect(), True)
            return MuOffer(True, ())
        changed = (self.latest is not None
                   and self.latest.training_generation != handoff.training_generation)
        evicted = self.collect()
        if len(self.resident) >= self.max_resident:
            self.declined += 1
            return MuOffer(False, evicted, changed)
        self.resident[handoff.snapshot_sha256] = handoff
        self.latest = handoff
        return MuOffer(True, evicted + self.collect(), changed)

    def _held(self, snapshot: str) -> bool:
        return (self.references.get(snapshot, 0) > 0 or self.pins.get(snapshot, 0) > 0
                or (self.latest is not None and snapshot == self.latest.snapshot_sha256))

    def collect(self) -> tuple[str, ...]:
        """Drop every resident mu no unsealed segment, pin or `latest` holds."""
        evicted = tuple(s for s in self.resident if not self._held(s))
        for snapshot in evicted:
            self.resident.pop(snapshot)
        return evicted

    def current(self) -> MuHandoff:
        if self.latest is None:
            raise MuNotResident("mu_not_resident")
        return self.latest

    def retain(self, snapshot: str) -> None:
        if snapshot not in self.resident:
            raise AssemblyError("mu_not_resident")
        self.references[snapshot] = self.references.get(snapshot, 0) + 1

    def release(self, snapshot: str) -> None:
        count = self.references.get(snapshot, 0)
        if count < 1:
            raise AssemblyError("mu_reference_missing")
        if count == 1:
            self.references.pop(snapshot)
        else:
            self.references[snapshot] = count - 1

    def pin(self, snapshot: str) -> None:
        """Keep a mu resident for an original that needs its weights (R7 replay)."""
        if snapshot not in self.resident:
            raise AssemblyError("mu_not_resident")
        self.pins[snapshot] = self.pins.get(snapshot, 0) + 1

    def unpin(self, snapshot: str) -> None:
        count = self.pins.get(snapshot, 0)
        if count < 1:
            raise AssemblyError("mu_pin_missing")
        if count == 1:
            self.pins.pop(snapshot)
        else:
            self.pins[snapshot] = count - 1


@dataclass(frozen=True)
class TailPlan:
    """Value of the previous (full) segment's mu on this row, at this floor."""

    segment_id: str
    mu: str
    floor: int


@dataclass(frozen=True)
class RowPlan:
    game_instance_id: str
    request_id: int
    step_index: int
    segment_id: str
    row_in_segment: int
    mu: MuHandoff
    tail: TailPlan | None


@dataclass
class _Segment:
    segment_id: str
    index: int
    mu: MuHandoff
    rows: list[dict[str, Any]] = field(default_factory=list)
    data: list[bytes] = field(default_factory=list)
    inflight: bool = False
    bootstrap: dict[str, Any] | None = None
    terminal: str | None = None
    doomed: tuple[str, str] | None = None

    def resolved(self) -> bool:
        return not self.inflight and all(row["outcome"] is not None for row in self.rows)

    def completed(self) -> bool:
        return not self.inflight and all(
            (row["outcome"] or {}).get("status") == "completed" for row in self.rows)


@dataclass
class _Game:
    game_instance_id: str
    actor_id: str
    sampling: dict[str, Any]
    single_choice: str
    start: dict[str, Any]
    audit_retain_mu: bool = False
    segments: list[_Segment] = field(default_factory=list)
    inflight: RowPlan | None = None
    last_step: int = -1
    next_index: int = 0
    max_floor: int = 0
    acts: list[int] = field(default_factory=list)
    failure: str | None = None
    pending: tuple[dict[str, Any], bytes] | None = None
    # request_id -> the rows holding it, in segment/row order (step_outcome lookup;
    # the first entry is the row the old full scan found).
    by_request: dict[Any, list[tuple[_Segment, dict[str, Any]]]] = field(default_factory=dict)
    sealed: dict[str, int] = field(default_factory=lambda: dict.fromkeys(
        ("bootstrap", "terminal", "truncated", "rejected"), 0))


def _archive_key(handoff: MuHandoff) -> tuple[str, str]:
    # The same weights under a new generation (CP restore) are bound again.
    return handoff.snapshot_sha256, handoff.training_generation


class FastSegmentAssembler:
    def __init__(self, *, assembler_id: str, compiler_identity: dict[str, Any],
                 emit: Callable[[SealedSegment | RejectedSegment], None],
                 terminal_labeler: TerminalLabeler | None = public_terminal_label,
                 ledger: MuLedger | None = None, closed_memory: int = 65536,
                 mu_archive: MuArchive | None = None,
                 on_closed: Callable[[InstanceClosed], None] | None = None,
                 segment_format: str = FORMAT, verify_every: int = 64) -> None:
        if (not isinstance(assembler_id, str) or not assembler_id or len(assembler_id) > 64
                or any(not (c.isalnum() or c in "._-") for c in assembler_id)):
            raise AssemblyError("invalid_assembler_id")
        # T3 (fast_chain only, default off): `stpd/rl-fast-segment-2` seals the same
        # logical segment columnar. Every `verify_every`-th format-2 segment (1 = every
        # one) is decoded again and compared in full with the format-1 logical content;
        # a difference keeps the original as rejected and raises (never a fallback).
        if segment_format not in FORMATS or type(verify_every) is not int or verify_every < 1:
            raise AssemblyError("invalid_segment_format")
        self.segment_format, self.verify_every = segment_format, verify_every
        self.columns_sealed = 0
        self.assembler_id, self.steps = assembler_id, MAX_ROWS
        self.instance_id = f"{assembler_id}.{secrets.token_hex(6)}"
        self.compiler_identity = copy.deepcopy(compiler_identity)
        self.emit, self.terminal_labeler = emit, terminal_labeler
        # Normally `fast_handoff.SegmentHandoff.submit_closed`: queued after the
        # instance's segments, so the learner sees it after the last one (WP8).
        self.on_closed = on_closed
        self.mu_archive = mu_archive
        self.archived: set[tuple[str, str]] = set()  # (snapshot, training generation)
        self.archive_lock = threading.Lock()
        self.ledger = ledger or MuLedger()
        self.lock = threading.Lock()
        self.games: dict[str, _Game] = {}
        self.closed: OrderedDict[str, None] = OrderedDict()
        self.closed_memory = closed_memory
        self.segment_counter = 0
        self.counters: dict[str, Any] = {
            "rows": 0, "aborted_rows": 0, "forced_steps": 0, "late_discarded": 0,
            "outcomes_without_row": 0, "segments": {"bootstrap": 0, "terminal": 0,
                                                    "truncated": 0, "rejected": 0},
            "truncated_by_reason": {}, "mu_declined": 0, "generations_discarded": 0,
            "terminal_label_refused": 0, "mu_archived": 0, "instances_closed": 0,
            "segments_verified": 0}

    # ------------------------------------------------------------------ mu
    def offer_mu(self, handoff: MuHandoff) -> MuOffer:
        """Learner -> service temporary mu.

        A new training generation (learner CP restore) dooms every segment bound
        to an older generation first: they seal `truncated` once their rows
        resolved and are never given to SGD, as the live queue is discarded on a
        CP-only restore.
        """
        with self.lock:
            self.ledger.check(handoff)  # an identity conflict is refused before anything
            audit_open = any(game.audit_retain_mu for game in self.games.values())
        if audit_open:
            self._archive(handoff)
        sealed: list[SealedSegment | RejectedSegment] = []
        try:
            with self.lock:
                return self._offer_mu(handoff, sealed)
        finally:
            self._emit(sealed)

    def _offer_mu(self, handoff: MuHandoff,
                  sealed: list[SealedSegment | RejectedSegment]) -> MuOffer:
        self.ledger.check(handoff)
        latest = self.ledger.latest
        changed = (latest is not None
                   and latest.training_generation != handoff.training_generation)
        collected: tuple[str, ...] = ()
        if changed:
            self.counters["generations_discarded"] += 1
            for game in self.games.values():
                for segment in list(game.segments):
                    if segment.mu.training_generation != handoff.training_generation:
                        segment.doomed = segment.doomed or ("generation_discarded",
                                                            segment.mu.training_generation)
                        sealed.extend(self._try_seal(game, segment))
        result = self.ledger.offer(handoff)
        if changed:
            collected = self.ledger.collect()
        if not result.accepted:
            self.counters["mu_declined"] += 1
        return MuOffer(result.accepted, result.evicted + collected, changed)

    # ------------------------------------------------------------ episodes
    def open_episode(self, message: Mapping[str, Any]) -> None:
        """`episode_open` exactly as received (rl-inference-3)."""
        audit = message.get("audit_retain_mu", False)
        if type(audit) is not bool:
            raise AssemblyError("invalid_episode_open")
        with self.lock:
            instance = message.get("game_instance_id")
            if not isinstance(instance, str) or instance in self.games or instance in self.closed:
                raise AssemblyError("episode_already_open_or_closed")
            sampling = message.get("sampling")
            if (not isinstance(sampling, Mapping) or message.get("single_choice")
                    not in SINGLE_CHOICE or not isinstance(message.get("start"), Mapping)
                    or not isinstance(message.get("actor_id"), str)
                    or set(sampling.get("classes") or {}) != {"combat", "noncombat"}):
                raise AssemblyError("invalid_episode_open")
            start = copy.deepcopy(dict(message["start"]))
            if start.get("kind") == "from_save":
                # rl-inference-3 requires both: never a default act (a made-up Boss witness).
                first_act, first_floor = start.get("act"), start.get("floor")
                if (type(first_act) is not int or first_act < 1
                        or type(first_floor) is not int or first_floor < 0):
                    raise AssemblyError("invalid_episode_open")
            elif start.get("kind") == "new_run":
                first_act, first_floor = 1, 0
            else:
                raise AssemblyError("invalid_episode_open")
            if audit and self.mu_archive is None:
                raise AssemblyError("audit_mu_archive_unconfigured")
            game = _Game(instance, str(message["actor_id"]), copy.deepcopy(dict(sampling)),
                         str(message["single_choice"]), start, audit, acts=[first_act])
            game.max_floor = first_floor
            self.games[instance] = game
        if not audit:
            return
        # A mu offered from now on is archived by offer_mu before it can be bound;
        # one that arrived while this game was opening is archived here.
        try:
            while True:
                with self.lock:
                    current = self.ledger.latest
                    if current is None or _archive_key(current) in self.archived:
                        return
                self._archive(current)
        except BaseException:
            with self.lock:  # a failed open leaves no game behind
                if self.games.get(instance) is game and not game.segments:
                    del self.games[instance]
            raise

    def begin_row(self, row: Mapping[str, Any], row_bytes: bytes) -> RowPlan:
        """Bind a request row to its segment and mu before the forward pass."""
        with self.lock:
            game = self.games.get(str(row.get("game_instance_id")))
            if game is None:
                raise AssemblyError("episode_not_open")
            if game.inflight is not None:
                raise AssemblyError("row_already_in_flight_for_game")
            if game.failure is not None:
                raise AssemblyError("episode_already_failed")
            step = row.get("step_index")
            if type(step) is not int or step <= game.last_step:
                raise AssemblyError("row_step_order")
            payload = row.get("payload") or {}
            if (sha256(row_bytes) != payload.get("row_sha256")
                    or len(row_bytes) != payload.get("length")):
                raise AssemblyError("row_payload_binding")
            if type(row.get("request_id")) is not int or type(row.get("floor")) is not int:
                raise AssemblyError("invalid_row")
            if game.single_choice == "omit" and row.get("catalog_len") == 1:
                raise AssemblyError("single_option_row_under_omit")
            current = game.segments[-1] if game.segments else None
            if current is not None and current.terminal is not None:
                raise AssemblyError("row_after_terminal")
            if (game.audit_retain_mu and (current is None or current.doomed is not None
                                          or len(current.rows) >= self.steps)
                    and _archive_key(self.ledger.current()) not in self.archived):
                # Loud: an R7 game is never scored with a mu whose weights are not kept.
                raise AssemblyError("audit_mu_not_archived")
            tail: TailPlan | None = None
            if (current is None or current.doomed is not None
                    or len(current.rows) >= self.steps):
                mu = self.ledger.current()
                if (current is not None and current.doomed is None
                        and current.bootstrap is None and len(current.rows) >= self.steps):
                    floor = max([row["floor"], *(r["floor"] for r in current.rows)])
                    tail = TailPlan(current.segment_id, current.mu.snapshot_sha256, floor)
                self.ledger.retain(mu.snapshot_sha256)
                self.segment_counter += 1
                current = _Segment(f"{self.instance_id}.{self.segment_counter}",
                                   game.next_index, mu)
                game.next_index += 1
                game.segments.append(current)
            current.inflight = True
            plan = RowPlan(game.game_instance_id, row["request_id"], step, current.segment_id,
                           len(current.rows), current.mu, tail)
            game.inflight = plan
            game.pending = ({key: _owned(value) for key, value in row.items()},
                            bytes(row_bytes))
        return plan

    def finish_row(self, plan: RowPlan, scored: Mapping[str, Any],
                   outcome_probabilities: list[float],
                   tail_outcome_probabilities: list[float] | None = None) -> None:
        sealed: list[SealedSegment | RejectedSegment] = []
        try:
            with self.lock:
                self._finish_row(plan, scored, outcome_probabilities,
                                 tail_outcome_probabilities, sealed)
        finally:
            self._emit(sealed)

    def _finish_row(self, plan: RowPlan, scored: Mapping[str, Any],
                    outcome_probabilities: list[float],
                    tail_outcome_probabilities: list[float] | None,
                    sealed: list[SealedSegment | RejectedSegment]) -> None:
        game = self.games.get(plan.game_instance_id)
        if game is None or game.inflight != plan or game.pending is None:
            raise AssemblyError("row_plan_not_in_flight")
        row, raw = game.pending
        segment = game.segments[-1]
        self._check_scored(game, plan, row, scored)
        values = self._values(outcome_probabilities, row["floor"])
        tail_values = None
        if plan.tail is not None:
            if plan.tail.mu == plan.mu.snapshot_sha256 and tail_outcome_probabilities is None:
                tail_values = values if plan.tail.floor == row["floor"] else None
            else:
                tail_values = self._values(tail_outcome_probabilities, plan.tail.floor)
            if tail_values is None:
                raise AssemblyError("tail_value_required_at_tail_floor")
        # `row` is already the assembler's own copy (begin_row).
        meta = {key: row[key] for key in (
            "request_id", "step_index", "consumption_id", "catalog_len", "catalog_sha256",
            "observation_sha256", "in_combat", "floor")}
        meta.update(payload={"row_sha256": row["payload"]["row_sha256"]},
                    selected_index=scored["selected_index"],
                    behavior_q=list(scored["behavior_q"]), selected_q=scored["selected_q"],
                    applied={key: _owned(value) for key, value in scored["applied"].items()},
                    outcome_probabilities=values, outcome=None)
        segment.rows.append(meta)
        game.by_request.setdefault(meta["request_id"], []).append((segment, meta))
        segment.data.append(raw)
        segment.inflight = False
        game.inflight = None
        game.last_step = plan.step_index
        game.max_floor = max(game.max_floor, row["floor"])
        game.pending = None
        self.counters["rows"] += 1
        if plan.tail is not None:
            previous = next((s for s in game.segments
                             if s.segment_id == plan.tail.segment_id), None)
            if previous is not None and previous.doomed is None:
                previous.bootstrap = {
                    "mu_snapshot_sha256": plan.tail.mu,
                    "successor_request_id": plan.request_id,
                    "successor_step_index": plan.step_index,
                    "successor_row_sha256": meta["payload"]["row_sha256"],
                    "floor": plan.tail.floor, "outcome_probabilities": tail_values}
                sealed.extend(self._try_seal(game, previous))
        sealed.extend(self._try_seal(game, segment))

    def abort_row(self, plan: RowPlan, error_code: str) -> None:
        """The service refused this row; nothing enters a segment, the game will close."""
        sealed: list[SealedSegment | RejectedSegment] = []
        try:
            with self.lock:
                self._abort_row(plan, error_code, sealed)
        finally:
            self._emit(sealed)

    def _abort_row(self, plan: RowPlan, error_code: str,
                   sealed: list[SealedSegment | RejectedSegment]) -> None:
        game = self.games.get(plan.game_instance_id)
        if game is None or game.inflight != plan:
            raise AssemblyError("row_plan_not_in_flight")
        segment = game.segments[-1]
        segment.inflight = False
        game.inflight = None
        game.pending = None
        game.failure = f"refused:{error_code}"
        self.counters["aborted_rows"] += 1
        if not segment.rows:
            game.segments.pop()
            self.ledger.release(segment.mu.snapshot_sha256)
        for item in list(game.segments):
            item.doomed = item.doomed or ("not_started", "refused")
            sealed.extend(self._try_seal(game, item))

    def step_outcome(self, message: Mapping[str, Any]) -> None:
        sealed: list[SealedSegment | RejectedSegment] = []
        try:
            with self.lock:
                self._step_outcome(message, sealed)
        finally:
            self._emit(sealed)

    def _step_outcome(self, message: Mapping[str, Any],
                      sealed: list[SealedSegment | RejectedSegment]) -> None:
        game = self.games.get(str(message.get("game_instance_id")))
        if game is None:
            self.counters["late_discarded"] += 1
            return
        status = message.get("status")
        if status not in ("completed", "not_started", "unknown"):
            raise AssemblyError("invalid_step_outcome")
        request = message.get("row_request_id")
        try:
            holders = game.by_request.get(request)
        except TypeError:  # unhashable: equal to no int request id
            holders = None
        target = holders[0] if holders else None
        # Every check runs before any state changes.
        forced = message.get("forced_steps", 0)
        if type(forced) is not int or forced < 0:
            raise AssemblyError("invalid_forced_steps")
        if forced and game.single_choice != "omit":
            raise AssemblyError("forced_steps_require_single_choice_omit")
        terminal = message.get("terminal")
        if terminal is not None and (not isinstance(terminal, Mapping)
                                     or terminal.get("result") not in ("victory", "defeat")):
            raise AssemblyError("invalid_terminal")
        if target is not None:
            segment, row = target
            if row["outcome"] is not None or message.get("step_index") != row["step_index"]:
                raise AssemblyError("step_outcome_binding")
            if terminal is not None and (segment is not game.segments[-1]
                                         or row is not segment.rows[-1]):
                raise AssemblyError("terminal_not_on_last_row")
        if status != "completed":
            game.failure = game.failure or f"{status}:{message.get('reason')}"
        if target is None:
            self.counters["outcomes_without_row"] += 1
        else:
            segment, row = target
            row["outcome"] = {key: _owned(message[key]) for key in OUTCOME_FIELDS
                              if key in message}
            self.counters["forced_steps"] += forced
            facts = message.get("public_facts")
            if isinstance(facts, Mapping):
                if type(facts.get("floor")) is int:
                    game.max_floor = max(game.max_floor, facts["floor"])
                if type(facts.get("act")) is int:
                    game.acts.append(facts["act"])
            if terminal is not None:
                segment.terminal = str(terminal["result"])
        if status != "completed":
            for item in list(game.segments):
                item.doomed = item.doomed or (str(status), str(message.get("reason")))
        for item in list(game.segments):
            sealed.extend(self._try_seal(game, item))

    def close_episode(self, message: Mapping[str, Any]) -> None:
        """Seal the game's segments, emit them, then emit its `InstanceClosed`.

        The closed signal follows the segments even when a hand-off or the labeler
        raised (the first error is raised after it), so the learner always learns
        the game ended; a late close of an unknown instance emits nothing.
        """
        sealed: list[SealedSegment | RejectedSegment] = []
        closed: list[InstanceClosed] = []
        errors: list[BaseException] = []
        try:
            with self.lock:
                self._close_episode(message, sealed, closed)
        except BaseException as error:  # noqa: BLE001 - re-raised below, first one wins
            errors.append(error)
        try:
            self._emit(sealed)
        except BaseException as error:  # noqa: BLE001
            errors.append(error)
        if closed and self.on_closed is not None:
            try:
                self.on_closed(closed[0])
            except BaseException as error:  # noqa: BLE001
                errors.append(error)
        if errors:
            raise errors[0]  # a later failure never hides the first one

    def _close_episode(self, message: Mapping[str, Any],
                       sealed: list[SealedSegment | RejectedSegment],
                       closed: list[InstanceClosed]) -> None:
        instance = str(message.get("game_instance_id"))
        game = self.games.pop(instance, None)
        if game is None:
            self.counters["late_discarded"] += 1
            return
        self.closed[instance] = None
        while len(self.closed) > self.closed_memory:
            self.closed.popitem(last=False)
        reason = str(message.get("reason"))
        if game.inflight is not None:
            game.segments[-1].inflight = False
            game.inflight, game.pending = None, None
        broken: BaseException | None = None
        for segment in list(game.segments):
            if not segment.rows:
                game.segments.remove(segment)
                self.ledger.release(segment.mu.snapshot_sha256)
                continue
            last = segment is game.segments[-1]
            if (last and reason == "terminal" and segment.doomed is None
                    and segment.terminal is not None and segment.completed()):
                try:
                    label = self._label(game, segment)
                except AssemblyError as error:  # labeler broke its contract: loud
                    broken = broken or error
                    segment.doomed = ("terminal_label_invalid", str(error))
                except ValueError as error:  # the old chain refused the label
                    self.counters["terminal_label_refused"] += 1
                    segment.doomed = ("terminal_label_refused", str(error))
                except Exception as error:  # noqa: BLE001 - original kept, then raised
                    broken = broken or error
                    segment.doomed = ("terminal_label_invalid", type(error).__name__)
                else:
                    if label is not None:
                        sealed.append(self._seal(game, segment, "terminal", terminal=label))
                        continue
                    segment.doomed = ("terminal_unlabeled", segment.terminal)
            cause = segment.doomed or (reason, game.failure or reason)
            sealed.append(self._seal(game, segment, "truncated",
                                     truncation={"reason": cause[0], "detail": cause[1]}))
        closed.append(InstanceClosed(instance, reason, game.failure, dict(game.sealed)))
        self.counters["instances_closed"] += 1
        if broken is not None:
            raise broken

    def report(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy({
                **self.counters, "open_games": len(self.games),
                "open_segments": sum(len(g.segments) for g in self.games.values()),
                "resident_mu": list(self.ledger.resident),
                "referenced_mu": dict(self.ledger.references),
                "latest_mu": self.ledger.latest and self.ledger.latest.snapshot_sha256,
                "instance_id": self.instance_id})

    # -------------------------------------------------------------- inside
    def _check_scored(self, game: _Game, plan: RowPlan, row: Mapping[str, Any],
                      scored: Mapping[str, Any]) -> None:
        """The service's own result must already satisfy the environment's checks."""
        q = scored.get("behavior_q")
        index = scored.get("selected_index")
        decision = "combat" if row["in_combat"] else "noncombat"
        policy = game.sampling["classes"][decision]
        if (scored.get("status") != "scored"
                or (scored.get("request_id"), scored.get("game_instance_id"),
                    scored.get("catalog_sha256"))
                != (row["request_id"], row["game_instance_id"], row["catalog_sha256"])
                or scored.get("segment_id") != plan.segment_id
                or scored.get("row_in_segment") != plan.row_in_segment
                or scored.get("mu_snapshot_sha256") != plan.mu.snapshot_sha256
                or not isinstance(q, list) or len(q) != row["catalog_len"]
                or not unit_numbers(q) or not math.isclose(sum(q), 1.0, abs_tol=1e-6)
                or type(index) is not int or not 0 <= index < len(q)
                or q[index] != scored.get("selected_q") or not q[index] > 0
                or scored.get("applied") != {
                    "arm_id": game.sampling["arm_id"], "decision_class": decision,
                    "temperature": policy["temperature"],
                    "uniform_mix": policy["uniform_mix"]}):
            raise AssemblyError("scored_result_binding")

    @staticmethod
    def _values(values: list[float] | None, floor: int) -> list[float]:
        """The learner's distribution and floor-support checks (`check_value_distribution`)."""
        if not isinstance(values, (list, tuple)) or not values:
            raise AssemblyError("every_mu_value_required_including_forced")
        result = (list(values) if set(map(type, values)) == _FLOAT
                  else [float(v) if type(v) in (int, float) else v for v in values])
        try:
            check_value_distribution(result, floor)
        except SegmentFormatError as error:
            raise AssemblyError(str(error)) from error
        return result

    def _label(self, game: _Game, segment: _Segment) -> dict[str, Any] | None:
        if self.terminal_labeler is None:
            return None
        label = self.terminal_labeler({
            "game_instance_id": game.game_instance_id, "result": segment.terminal,
            "start": copy.deepcopy(game.start), "max_floor": game.max_floor,
            "acts": list(game.acts)})
        if label is None:
            return None
        if (set(label) != {"class", "reward"} or type(label["class"]) is not int
                or type(label["reward"]) not in (int, float)
                or not math.isfinite(label["reward"])):
            raise AssemblyError("invalid_terminal_label")
        return {"result": segment.terminal, "class": label["class"],
                "reward": float(label["reward"])}

    def _try_seal(self, game: _Game,
                  segment: _Segment) -> list[SealedSegment | RejectedSegment]:
        if segment not in game.segments:
            return []
        if segment.doomed is not None and segment.resolved():
            if not segment.rows:
                game.segments.remove(segment)
                self.ledger.release(segment.mu.snapshot_sha256)
                return []
            return [self._seal(game, segment, "truncated", truncation={
                "reason": segment.doomed[0], "detail": segment.doomed[1]})]
        if (segment.doomed is None and segment.bootstrap is not None
                and len(segment.rows) == MAX_ROWS and segment.completed()):
            return [self._seal(game, segment, "bootstrap")]
        return []

    def _seal(self, game: _Game, segment: _Segment, kind: str, *,
              terminal: dict[str, Any] | None = None,
              truncation: dict[str, Any] | None = None) -> SealedSegment | RejectedSegment:
        header = {
            "format": self.segment_format, "row_format": ROW_FORMAT,
            "segment_id": segment.segment_id, "assembler_id": self.assembler_id,
            "assembler_instance": self.instance_id,
            "game_instance_id": game.game_instance_id, "actor_id": game.actor_id,
            "segment_index": segment.index, "start": game.start,
            "audit_retain_mu": game.audit_retain_mu,
            "single_choice": game.single_choice, "sampling": game.sampling,
            "mu": segment.mu.identity(), "compiler_identity": self.compiler_identity,
            "rows": segment.rows,
            "seal": {"kind": kind, "bootstrap": segment.bootstrap if kind == "bootstrap" else None,
                     "terminal": terminal, "truncation": truncation},
            "trainable": kind != "truncated",
            "forced_steps": sum((r["outcome"] or {}).get("forced_steps", 0)
                                for r in segment.rows)}
        result: SealedSegment | RejectedSegment
        try:
            if self.segment_format == FORMAT_2:
                result = encode_segment_columns(header, segment.data, bound=True)
                self._verify(result, header, segment.data)
            else:
                result = encode_segment(header, segment.data)
        except (SegmentFormatError, TypeError, ValueError) as error:
            # Kept as original (`rejected/`), counted, and raised once emitted.
            result = RejectedSegment(segment.segment_id, copy.deepcopy(header),
                                     tuple(segment.data), str(error) or type(error).__name__)
            kind = "rejected"
        # Only an encoded (or rejected-and-kept) segment leaves its game.
        game.segments.remove(segment)
        for row in segment.rows:
            holders = game.by_request.get(row["request_id"], [])
            holders[:] = [held for held in holders if held[0] is not segment]
            if not holders:
                game.by_request.pop(row["request_id"], None)
        self.ledger.release(segment.mu.snapshot_sha256)
        self.counters["segments"][kind] += 1
        game.sealed[kind] += 1
        if truncation is not None and kind == "truncated":
            by_reason = self.counters["truncated_by_reason"]
            by_reason[truncation["reason"]] = by_reason.get(truncation["reason"], 0) + 1
        return result

    def _verify(self, sealed: SealedSegment, header: dict[str, Any], rows: list[bytes]) -> None:
        """Every `verify_every`-th format-2 seal: decode it and compare with format 1.

        The reference is what `encode_segment` would have sealed (its JSON-owned
        logical header with format-1 offsets), re-laid out for format 2; the decoded
        header, the returned header and the row bytes must all equal it.
        """
        self.columns_sealed += 1
        if self.columns_sealed % self.verify_every:
            return
        decoded, decoded_rows = decode_segment(sealed.data)
        reference = encode_segment({**header, "format": FORMAT}, rows).header
        reference["format"] = FORMAT_2
        for row in reference["rows"]:  # format 2 stores these as f8 (int q 1 -> 1.0)
            row["behavior_q"] = list(map(float, row["behavior_q"]))
            row["selected_q"] = float(row["selected_q"])
            row["outcome_probabilities"] = list(map(float, row["outcome_probabilities"]))
        # JSON-type strict (1 != 1.0 != True): a type drift is a difference too.
        if (not _same_json(decoded, reference) or not _same_json(sealed.header, reference)
                or decoded_rows != [bytes(row) for row in rows]):
            raise SegmentFormatError("segment_columns_verify_mismatch")
        self.counters["segments_verified"] += 1

    def _archive(self, handoff: MuHandoff) -> None:
        """Durably keep one mu's weights; outside `self.lock`, before it is bound."""
        with self.archive_lock:
            if _archive_key(handoff) in self.archived:
                return
            if self.mu_archive is None:
                raise AssemblyError("audit_mu_archive_unconfigured")
            if handoff.weights is None:
                raise AssemblyError("audit_mu_weights_missing")
            self.mu_archive(handoff)
            with self.lock:
                self.archived.add(_archive_key(handoff))
                self.counters["mu_archived"] += 1

    def _emit(self, sealed: list[SealedSegment | RejectedSegment]) -> None:
        """Hand every sealed segment on, even when one hand-off raises; then raise."""
        first: BaseException | None = None
        for segment in sealed:
            try:
                self.emit(segment)
            except BaseException as error:  # noqa: BLE001 - re-raised after the rest
                first = first or error
        if first is not None:
            raise first
        rejected = [segment for segment in sealed if isinstance(segment, RejectedSegment)]
        if rejected:
            raise AssemblyError(f"segment_rejected:{rejected[0].error}")
