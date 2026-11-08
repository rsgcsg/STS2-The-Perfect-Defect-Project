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
- the environment's outcome fields are copied unchanged, including `combat_end`,
  which no consumer reads before the Connector fair-projection review; the
  terminal labeler is injected and is the only reader.

Temporary mu (E5 publication change): a `MuHandoff` names the snapshot the
learner pushed to the service every few seconds. It is not a Store asset; its
identity is the snapshot digest plus training generation, version and update
count. Retained originals stay self-describing without the weights: every row
records the actual q vector and the mu's value output. Replaying scores (R7)
needs the weights; see `MuLedger.pin`.
"""
from __future__ import annotations

import copy
import math
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .fast_segment import MAX_ROWS, ROW_FORMAT, SealedSegment, encode_segment, sha256

OUTCOME_FIELDS = ("status", "reason", "receipt_sha256", "public_facts", "forced_steps",
                  "combat_end", "terminal")
SINGLE_CHOICE = ("score", "omit")
TerminalLabeler = Callable[[Mapping[str, Any]], "Mapping[str, Any] | None"]


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

    def __post_init__(self) -> None:
        if (not isinstance(self.snapshot_sha256, str) or len(self.snapshot_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.snapshot_sha256)
                or not isinstance(self.training_generation, str) or not self.training_generation
                or type(self.version) is not int or type(self.updates) is not int
                or self.version < 0 or self.updates < 0):
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

    def offer(self, handoff: MuHandoff) -> MuOffer:
        if handoff.snapshot_sha256 in self.resident:
            if self.resident[handoff.snapshot_sha256] != handoff:
                raise AssemblyError("mu_snapshot_identity_changed")
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
    segments: list[_Segment] = field(default_factory=list)
    inflight: RowPlan | None = None
    last_step: int = -1
    next_index: int = 0
    max_floor: int = 0
    combat_end: list[Any] = field(default_factory=list)
    last_public_facts: dict[str, Any] | None = None
    failure: str | None = None
    pending: tuple[dict[str, Any], bytes] | None = None


class FastSegmentAssembler:
    def __init__(self, *, assembler_id: str, compiler_identity: dict[str, Any],
                 emit: Callable[[SealedSegment], None],
                 terminal_labeler: TerminalLabeler | None = None,
                 ledger: MuLedger | None = None, closed_memory: int = 65536) -> None:
        if (not isinstance(assembler_id, str) or not assembler_id or len(assembler_id) > 64
                or any(not (c.isalnum() or c in "._-") for c in assembler_id)):
            raise AssemblyError("invalid_assembler_id")
        self.assembler_id, self.steps = assembler_id, MAX_ROWS
        self.compiler_identity = copy.deepcopy(compiler_identity)
        self.emit, self.terminal_labeler = emit, terminal_labeler
        self.ledger = ledger or MuLedger()
        self.lock = threading.Lock()
        self.games: dict[str, _Game] = {}
        self.closed: OrderedDict[str, None] = OrderedDict()
        self.closed_memory = closed_memory
        self.segment_counter = 0
        self.counters: dict[str, Any] = {
            "rows": 0, "aborted_rows": 0, "forced_steps": 0, "late_discarded": 0,
            "outcomes_without_row": 0, "segments": {"bootstrap": 0, "terminal": 0,
                                                    "truncated": 0},
            "truncated_by_reason": {}, "mu_declined": 0, "generations_discarded": 0}

    # ------------------------------------------------------------------ mu
    def offer_mu(self, handoff: MuHandoff) -> MuOffer:
        """Learner -> service temporary mu.

        A new training generation (learner CP restore) dooms every segment bound
        to an older generation first: they seal `truncated` once their rows
        resolved and are never given to SGD, as the live queue is discarded on a
        CP-only restore.
        """
        sealed: list[SealedSegment] = []
        with self.lock:
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
            result = MuOffer(result.accepted, result.evicted + collected, changed)
        self._emit(sealed)
        return result

    # ------------------------------------------------------------ episodes
    def open_episode(self, message: Mapping[str, Any]) -> None:
        """`episode_open` exactly as received (rl-inference-3)."""
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
            self.games[instance] = _Game(instance, str(message["actor_id"]),
                                         copy.deepcopy(dict(sampling)),
                                         str(message["single_choice"]),
                                         copy.deepcopy(dict(message["start"])))

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
            current = game.segments[-1] if game.segments else None
            if current is not None and current.terminal is not None:
                raise AssemblyError("row_after_terminal")
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
                current = _Segment(f"{self.assembler_id}.{self.segment_counter}",
                                   game.next_index, mu)
                game.next_index += 1
                game.segments.append(current)
            current.inflight = True
            plan = RowPlan(game.game_instance_id, row["request_id"], step, current.segment_id,
                           len(current.rows), current.mu, tail)
            game.inflight = plan
            game.pending = (copy.deepcopy(dict(row)), bytes(row_bytes))
        return plan

    def finish_row(self, plan: RowPlan, scored: Mapping[str, Any],
                   outcome_probabilities: list[float],
                   tail_outcome_probabilities: list[float] | None = None) -> None:
        sealed: list[SealedSegment] = []
        with self.lock:
            game = self.games.get(plan.game_instance_id)
            if game is None or game.inflight != plan or game.pending is None:
                raise AssemblyError("row_plan_not_in_flight")
            row, raw = game.pending
            segment = game.segments[-1]
            self._check_scored(game, plan, row, scored)
            values = self._values(outcome_probabilities)
            tail_values = None
            if plan.tail is not None:
                if plan.tail.mu == plan.mu.snapshot_sha256 and tail_outcome_probabilities is None:
                    tail_values = values if plan.tail.floor == row["floor"] else None
                else:
                    tail_values = self._values(tail_outcome_probabilities)
                if tail_values is None:
                    raise AssemblyError("tail_value_required_at_tail_floor")
            meta = {key: copy.deepcopy(row[key]) for key in (
                "request_id", "step_index", "consumption_id", "catalog_len", "catalog_sha256",
                "observation_sha256", "in_combat", "floor")}
            meta.update(payload={"row_sha256": row["payload"]["row_sha256"]},
                        selected_index=scored["selected_index"],
                        behavior_q=list(scored["behavior_q"]), selected_q=scored["selected_q"],
                        applied=copy.deepcopy(dict(scored["applied"])),
                        outcome_probabilities=values, outcome=None)
            segment.rows.append(meta)
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
        self._emit(sealed)

    def abort_row(self, plan: RowPlan, error_code: str) -> None:
        """The service refused this row; nothing enters a segment, the game will close."""
        sealed: list[SealedSegment] = []
        with self.lock:
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
        self._emit(sealed)

    def step_outcome(self, message: Mapping[str, Any]) -> None:
        sealed: list[SealedSegment] = []
        with self.lock:
            game = self.games.get(str(message.get("game_instance_id")))
            if game is None:
                self.counters["late_discarded"] += 1
                return
            request = message.get("row_request_id")
            target = next(((segment, row) for segment in game.segments for row in segment.rows
                           if row["request_id"] == request), None)
            status = message.get("status")
            if status not in ("completed", "not_started", "unknown"):
                raise AssemblyError("invalid_step_outcome")
            if status != "completed":
                game.failure = game.failure or f"{status}:{message.get('reason')}"
            if target is None:
                self.counters["outcomes_without_row"] += 1
            else:
                segment, row = target
                if row["outcome"] is not None or message.get("step_index") != row["step_index"]:
                    raise AssemblyError("step_outcome_binding")
                row["outcome"] = {key: copy.deepcopy(message[key]) for key in OUTCOME_FIELDS
                                  if key in message}
                forced = message.get("forced_steps", 0)
                if forced and game.single_choice != "omit":
                    raise AssemblyError("forced_steps_require_single_choice_omit")
                self.counters["forced_steps"] += forced or 0
                facts = message.get("public_facts")
                if isinstance(facts, Mapping):
                    game.last_public_facts = copy.deepcopy(dict(facts))
                    if type(facts.get("floor")) is int:
                        game.max_floor = max(game.max_floor, facts["floor"])
                if message.get("combat_end"):
                    game.combat_end.extend(copy.deepcopy(list(message["combat_end"])))
                terminal = message.get("terminal")
                if terminal is not None:
                    if segment is not game.segments[-1] or row is not segment.rows[-1]:
                        raise AssemblyError("terminal_not_on_last_row")
                    segment.terminal = terminal["result"]
            if status != "completed":
                for item in list(game.segments):
                    item.doomed = item.doomed or (str(status), str(message.get("reason")))
            for item in list(game.segments):
                sealed.extend(self._try_seal(game, item))
        self._emit(sealed)

    def close_episode(self, message: Mapping[str, Any]) -> None:
        sealed: list[SealedSegment] = []
        with self.lock:
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
            for segment in list(game.segments):
                if not segment.rows:
                    game.segments.remove(segment)
                    self.ledger.release(segment.mu.snapshot_sha256)
                    continue
                last = segment is game.segments[-1]
                if (last and reason == "terminal" and segment.doomed is None
                        and segment.terminal is not None and segment.completed()):
                    label = self._label(game, segment)
                    if label is not None:
                        sealed.append(self._seal(game, segment, "terminal", terminal=label))
                        continue
                    segment.doomed = ("terminal_unlabeled", segment.terminal)
                cause = segment.doomed or (reason, game.failure or reason)
                sealed.append(self._seal(game, segment, "truncated",
                                         truncation={"reason": cause[0], "detail": cause[1]}))
        self._emit(sealed)

    def report(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy({
                **self.counters, "open_games": len(self.games),
                "open_segments": sum(len(g.segments) for g in self.games.values()),
                "resident_mu": list(self.ledger.resident),
                "referenced_mu": dict(self.ledger.references),
                "latest_mu": self.ledger.latest and self.ledger.latest.snapshot_sha256})

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
                or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                       for v in q)
                or not math.isclose(sum(q), 1.0, abs_tol=1e-6)
                or type(index) is not int or not 0 <= index < len(q)
                or q[index] != scored.get("selected_q") or not q[index] > 0
                or scored.get("applied") != {
                    "arm_id": game.sampling["arm_id"], "decision_class": decision,
                    "temperature": policy["temperature"],
                    "uniform_mix": policy["uniform_mix"]}):
            raise AssemblyError("scored_result_binding")

    @staticmethod
    def _values(values: list[float] | None) -> list[float]:
        if (not isinstance(values, (list, tuple)) or not values
                or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
                       for v in values)
                or not math.isclose(sum(values), 1.0, abs_tol=1e-4)):
            raise AssemblyError("every_mu_value_required_including_forced")
        return [float(v) for v in values]

    def _label(self, game: _Game, segment: _Segment) -> dict[str, Any] | None:
        if self.terminal_labeler is None:
            return None
        label = self.terminal_labeler({
            "game_instance_id": game.game_instance_id, "result": segment.terminal,
            "start": copy.deepcopy(game.start), "max_floor": game.max_floor,
            "public_facts": copy.deepcopy(game.last_public_facts),
            "combat_end": copy.deepcopy(game.combat_end)})
        if label is None:
            return None
        if (set(label) != {"class", "reward"} or type(label["class"]) is not int
                or type(label["reward"]) not in (int, float)
                or not math.isfinite(label["reward"])):
            raise AssemblyError("invalid_terminal_label")
        return {"result": segment.terminal, "class": label["class"],
                "reward": float(label["reward"])}

    def _try_seal(self, game: _Game, segment: _Segment) -> list[SealedSegment]:
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
              truncation: dict[str, Any] | None = None) -> SealedSegment:
        game.segments.remove(segment)
        self.ledger.release(segment.mu.snapshot_sha256)
        header = {
            "format": "stpd/rl-fast-segment-1", "row_format": ROW_FORMAT,
            "segment_id": segment.segment_id, "assembler_id": self.assembler_id,
            "game_instance_id": game.game_instance_id, "actor_id": game.actor_id,
            "segment_index": segment.index, "start": game.start,
            "single_choice": game.single_choice, "sampling": game.sampling,
            "mu": segment.mu.identity(), "compiler_identity": self.compiler_identity,
            "rows": segment.rows,
            "seal": {"kind": kind, "bootstrap": segment.bootstrap if kind == "bootstrap" else None,
                     "terminal": terminal, "truncation": truncation},
            "trainable": kind != "truncated",
            "forced_steps": sum((r["outcome"] or {}).get("forced_steps", 0)
                                for r in segment.rows)}
        sealed = encode_segment(header, segment.data)
        self.counters["segments"][kind] += 1
        if truncation is not None:
            by_reason = self.counters["truncated_by_reason"]
            by_reason[truncation["reason"]] = by_reason.get(truncation["reason"], 0) + 1
        return sealed

    def _emit(self, sealed: list[SealedSegment]) -> None:
        for segment in sealed:
            self.emit(segment)
