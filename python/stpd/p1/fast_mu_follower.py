"""WP8 mu handoff: the learner's published mu reaches the fast inference service.

The learner owns publication (`SegmentLearner._collect_publication` records the
identity in its fast intake, then serves it through `get_mu`). This follower is
the service-side half: it reads the learner's latest identity through a
`MuSource` (production: `NodeMuSource` on the learner attachment, the same node
cache transfer and load checks as the old S2-1 service), checks its
`compiler_identity` against the service's, loads that snapshot on CPU and
offers it with `FastInferenceService.publish(model, MuHandoff(...))`. Rows bind
their mu per segment (`FastSegmentAssembler.begin_row` keeps an open segment's
mu), so the new identity takes effect from the next segment on; `step_outcome`
scores and segment headers always carry the mu that scored the row
(`mu_snapshot_sha256`), and the learner trains a fast segment only under a mu it
recorded (`fast_segment_mu_not_published`).

Rules:
- one identity is offered at most once; the same weights under a new identity
  (a CP restore's new generation) are offered again, the assembler rebinds;
- an identity whose `compiler_identity` differs from the service's (or lacks
  one) is never loaded: it raises `MU_COMPILER_IDENTITY_DIFFERS` and counts as
  a failure, like the old service's `_load`;
- a refused offer (`MuLedger.offer` found every resident slot held by an open
  segment or a pin) is counted and never forced in. A refusal is a known
  answer, not an unknown delivery: the next poll offers the current mu again
  to the services that refused it (and only to them; a newer publication is
  offered to every service instead), so each service lags the learner by a
  bounded time, not by an unbounded run of refusals. A publish that raises,
  times out or answers in any other shape is never repeated;
- a publish that raises stops the service itself at once (`publish_failed`,
  never repeated); the follower then keeps that error and exits;
- reading `latest()` is an idempotent read and is polled again next interval;
  a failed check or load is counted with its error. `max_failures`
  consecutive failures are fatal: `on_fatal(error)` runs (default: the service
  stops loudly, `FastInferenceService._stop("mu_follower_stopped")`, so later
  rows are refused instead of scored by an ever older mu) and `check()` raises
  the last error. Nothing is ever scored with a substitute mu;
- the service may be an `InferenceCard` (K services): its `publish` answers per
  service (`list[bool]`, each assembler from its own resident mu), and takes
  `services=` for the re-offer. A first offer counts as published when every
  service accepted it, refused when none did, `partial` otherwise; a re-offer
  counts as `reoffered` (and `caught_up` per accepting service). `step()` is
  True when any service accepted; the history keeps the per-service answers
  and `status()["accepted_mu"]` each service's last accepted mu. Until a
  refusing service catches up it scores on its older mu, as one service would
  (rows always carry the mu that scored them). Any other answer shape is fatal
  at once (`MU_PUBLISH_ANSWER_INVALID`: `on_fatal`, by default the card stops
  all K): whether the service took that mu is unknown. The card's `_stop`
  stops all K;
- a poll that does not return within `stall_seconds` (a read, load or publish
  that hangs) is fatal: a watchdog gives up with `MU_FOLLOWER_STALLED`
  (`on_fatal`, by default the service stops loudly), so the service never keeps
  scoring with an old mu behind a follower that silently stopped following;
- each stage of a poll has its own limit too (`stage_seconds`: `read`, `load`,
  `publish`, each at most `stall_seconds`). The poll marks the stage it is in
  with a timestamp; a source with an `on_stage` attribute (`NodeMuSource`)
  names its sub-steps (`read.lock`, `read.connect`, `read.auth`, ...). A stage
  over its limit gives up the same way, and the error and `status()["stalled"]`
  name the stage, how long it ran and its limit. Timing is per sub-stage: each
  mark restarts the clock, so every sub-step of a read is held to the `read`
  limit and the whole poll to `stall_seconds`. `status()["stage_max_seconds"]`
  keeps the longest time seen per (sub-)stage, and `read.total` the longest
  whole read. The read limit must exceed the source's `transfer_timeout` (per
  message) and `handshake_timeout`, so a slow read that is still moving fails
  through those (counted, read again) instead of as a fatal stall. Once the
  follower gives up it unbinds `on_stage`, so a late read changes no mark;
- shutdown order: `close()` stops this follower before it closes the service;
  once the service is stopping the follower publishes nothing and exits;
- R7 mu archive (AUDIT F8): with `weights` set (production:
  `stpd.rl.fast_mu_pack.node_cache_weights` on the source's node cache), every
  offered `MuHandoff` carries the snapshot pack, read in the `load` stage, so
  an assembler with a local `mu_archive` can archive it for R7 games. A failed
  read counts and is retried next poll like a failed load. Without `weights`
  (the default) handoffs carry none, and an R7 game fails loud in the assembler
  (`audit_mu_archive_unconfigured` / `audit_mu_weights_missing`).
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from spireagent.json_boundary import BoundaryError
from stpd.rl.fast_assembly import MuHandoff

from .inference_service import MuSource

# Per-stage limits (seconds), each capped at `stall_seconds`. A read moves one
# snapshot of ~20 MB over the learner socket (normally well under a second) and
# its sub-steps must stay above the 60 s per-message transfer timeout; a load is
# a CPU deserialize; a publish may wait on the device (a cold compile), so it
# keeps the whole poll limit.
STAGE_SECONDS = {"read": 120.0, "load": 120.0, "publish": 300.0}


def _seconds(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


class FastMuFollower:
    """Poll the learner's latest mu and hot-swap it into a FastInferenceService."""

    def __init__(self, source: MuSource, service: Any, *, interval: float = 2.0,
                 max_failures: int = 3, clock: Callable[[], float] = time.monotonic,
                 on_fatal: Callable[[BaseException], None] | None = None,
                 stall_seconds: float = 300.0,
                 stage_seconds: dict[str, float] | None = None,
                 weights: Callable[[dict[str, Any]], bytes] | None = None) -> None:
        if not _seconds(interval) or not _seconds(stall_seconds):
            raise ValueError("invalid_fast_mu_follower_settings")
        limits = {stage: min(default, stall_seconds) for stage, default in STAGE_SECONDS.items()}
        limits.update(stage_seconds or {})
        source_timeouts = [getattr(source, name, None)
                           for name in ("transfer_timeout", "handshake_timeout")]
        if (type(max_failures) is not int or max_failures < 1
                or not stall_seconds > interval or set(limits) != set(STAGE_SECONDS)
                or any(not _seconds(limit) or not interval < limit <= stall_seconds
                       for limit in limits.values())
                or any(timeout is not None and not timeout < limits["read"]
                       for timeout in source_timeouts)):
            raise ValueError("invalid_fast_mu_follower_settings")
        self.stage_seconds = limits
        # (stage, began) of the running poll, swapped as one tuple so the watchdog
        # never pairs a new stage with the previous stage's start.
        self.mark: tuple[str | None, float | None] = (None, None)
        self.stage_max: dict[str, float] = {}
        self.stalled: dict[str, Any] | None = None
        if weights is not None and not callable(weights):
            raise ValueError("invalid_fast_mu_follower_settings")
        self.source, self.service, self.weights = source, service, weights
        self.weights_bytes: bytes | None = None  # `offered`'s pack, kept like `model`
        self.interval, self.max_failures, self.clock = interval, max_failures, clock
        self.stall_seconds = stall_seconds
        self.step_began: float | None = None  # set while a poll runs (watchdog)
        self.give_up_lock = threading.Lock()
        self.offered: dict[str, Any] | None = None
        self.behind: list[int] = []  # services that refused `offered` (re-offered next poll)
        self.model: Any = None  # `offered`'s model, kept only while a service is behind
        self.per_service: bool | None = None  # True: the service is a card (list answers)
        self.accepted_mu: list[dict[str, Any] | None] | None = None
        self.error: BaseException | None = None  # the last failure
        self.fatal: BaseException | None = None  # set once the follower gave up
        self.on_fatal = self._stop_service if on_fatal is None else on_fatal
        self.failures = 0
        self.history: list[dict[str, Any]] = []
        self.counters = {"polls": 0, "published": 0, "refused": 0, "partial": 0,
                         "reoffered": 0, "caught_up": 0, "unchanged": 0,
                         "failures": 0, "service_stopping": 0,
                         "load_seconds": 0.0, "publish_seconds": 0.0}
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.watchdog: threading.Thread | None = None
        if hasattr(source, "on_stage"):
            source.on_stage = self._enter  # sub-steps of a read (`read.connect`, ...)

    def _enter(self, stage: str | None) -> None:
        """Mark the stage the poll is in now; the watchdog holds it to its limit."""
        if self.fatal is not None:
            return  # a read that returns after the give-up changes no mark
        now = self.clock()
        previous, began = self.mark
        if previous is not None and began is not None:
            self.stage_max[previous] = max(self.stage_max.get(previous, 0.0), now - began)
        self.mark = (stage, None if stage is None else now)

    def _limit(self, stage: str) -> float:
        return self.stage_seconds[stage.split(".", 1)[0]]

    @staticmethod
    def handoff(identity: dict[str, Any]) -> MuHandoff:
        return MuHandoff(identity["snapshot_sha256"], identity["training_generation"],
                         identity["version"], identity["updates"])

    def _service_stopping(self) -> bool:
        stopping = getattr(self.service, "stopping", None)
        return stopping is not None and stopping.is_set()

    def _stop_service(self, error: BaseException) -> None:
        self.service._stop("mu_follower_stopped", repr(error))

    def step(self) -> bool:
        """One poll; True when a service accepted the identity it was offered."""
        with self.lock:
            self.counters["polls"] += 1
            if self._service_stopping():
                self.counters["service_stopping"] += 1
                return False
            self._enter("read")
            read_began = self.clock()
            identity = dict(self.source.latest())
            self._enter(None)
            read_seconds = self.clock() - read_began
            self.stage_max["read.total"] = max(self.stage_max.get("read.total", 0.0),
                                               read_seconds)
            if self.fatal is not None or self._service_stopping():
                return False  # a read that returned after the watchdog gave up feeds nothing
            handoff = self.handoff(identity)
            if self.offered is not None and self.offered == handoff.identity():
                if not self.behind:
                    self.counters["unchanged"] += 1
                    return False
                return self._offer(self.model, self._with_weights(handoff, self.weights_bytes),
                                   list(self.behind), 0.0)
            if identity.get("compiler_identity") != self.service.compiler_identity:
                raise BoundaryError("rl_mu", "MU_COMPILER_IDENTITY_DIFFERS")
            self._enter("load")
            began = self.clock()
            model = self.source.load(identity)
            raw = None if self.weights is None else self.weights(identity)
            if raw is not None and type(raw) is not bytes:
                raise BoundaryError("rl_mu", "MU_WEIGHTS_NOT_BYTES")
            load_seconds = self.clock() - began
            self._enter(None)
            self.counters["load_seconds"] += load_seconds
            if self.fatal is not None or self._service_stopping():
                return False
            return self._offer(model, self._with_weights(handoff, raw), None, load_seconds)

    @staticmethod
    def _with_weights(handoff: MuHandoff, raw: bytes | None) -> MuHandoff:
        if raw is None:
            return handoff
        return MuHandoff(handoff.snapshot_sha256, handoff.training_generation,
                         handoff.version, handoff.updates, weights=raw)

    def _offer(self, model: Any, handoff: MuHandoff, targets: list[int] | None,
               load_seconds: float) -> bool:
        """Offer `handoff` to every service (`targets` None) or to the ones that refused it."""
        # This delivery happens once: from here on only an explicit refusal is
        # answered again; a raise or an invalid answer leaves nobody behind.
        self.offered, self.behind, self.model = handoff.identity(), [], None
        self.weights_bytes = None
        self._enter("publish")
        began = self.clock()
        if targets is not None and self.per_service:
            answer = self.service.publish(model, handoff, services=targets)
        else:
            answer = self.service.publish(model, handoff)
        done = self.clock()
        self._enter(None)
        self.counters["publish_seconds"] += done - began
        record = {**handoff.identity(), "load_seconds": load_seconds,
                  "publish_seconds": done - began, "at": done}
        if self.fatal is not None:
            # The watchdog gave up while this publish hung: the delivery is kept as
            # history, but it changes nothing (no accepted mu, counters or failures).
            self.history.append({**record, "accepted": None, "after_fatal": True,
                                 "answer": repr(answer)[:200]})
            return False
        try:
            per_service = self._answers(answer, targets)
        except BoundaryError as error:
            self.history.append({**record, "accepted": None,
                                 "answer_invalid": repr(answer)[:200]})
            self.counters["failures"] += 1
            self.error = error
            self._give_up(error)
            raise
        indices = list(range(len(per_service))) if targets is None else targets
        if self.accepted_mu is None:
            self.accepted_mu = [None] * len(per_service)
        for index, accepted_here in zip(indices, per_service, strict=True):
            if accepted_here:
                self.accepted_mu[index] = {"version": handoff.version,
                                           "training_generation": handoff.training_generation,
                                           "snapshot_sha256": handoff.snapshot_sha256}
        self.behind = [index for index, accepted_here in zip(indices, per_service, strict=True)
                       if not accepted_here]
        if self.behind:
            self.model = model  # kept for the re-offer, dropped once nobody is behind
            self.weights_bytes = handoff.weights
        accepted = any(per_service)
        if targets is None:
            self.counters["published" if all(per_service) else "refused" if not accepted
                          else "partial"] += 1
        else:
            self.counters["reoffered"] += 1
            self.counters["caught_up"] += sum(per_service)
            record["reoffer_to"] = targets
        record["accepted"] = accepted
        if self.per_service:
            record["accepted_by"] = per_service
        self.history.append(record)
        return accepted

    def _answers(self, answer: Any, targets: list[int] | None) -> list[bool]:
        """One service answers a bool; a card answers one bool per service offered."""
        if type(answer) is bool and not self.per_service:
            self.per_service = False
            return [answer]
        expected = (len(targets) if targets is not None
                    else None if self.accepted_mu is None else len(self.accepted_mu))
        if (self.per_service is not False and type(answer) is list and answer
                and all(type(value) is bool for value in answer)
                and (expected is None or len(answer) == expected)):
            self.per_service = True
            return list(answer)
        raise BoundaryError("rl_mu", "MU_PUBLISH_ANSWER_INVALID")

    def _run(self) -> None:
        while not self.stopping.is_set():
            if self._service_stopping():
                return  # the service already stopped (or is closing): nothing to feed
            self.step_began = self.clock()
            try:
                self.step()
                if self.fatal is not None:
                    return  # the watchdog gave up meanwhile: a late result updates nothing
                self.failures = 0
            except BaseException as error:  # noqa: BLE001 - kept and re-raised by check()
                if self.fatal is not None:
                    return  # step() already gave up (an invalid publish answer)
                if self._service_stopping():
                    # The service stopped itself (a failed publish stops it at once as
                    # `publish_failed`, never retried): keep the error for status().
                    self.error = error
                    return
                self.failures += 1
                self.counters["failures"] += 1
                self.error = error
                if self.failures >= self.max_failures:
                    self._give_up(error)
                    return
            finally:
                self.step_began = None
                if self.fatal is None:
                    self._enter(None)
            if self.fatal is not None:
                return  # the watchdog gave up while this poll was running
            self.stopping.wait(self.interval)

    def _watch(self) -> None:
        """A poll that hangs is a failure too: stop loudly instead of following nothing."""
        while not self.stopping.wait(min(self.interval, self.stall_seconds / 4)):
            if self.fatal is not None or (self.thread is not None and not self.thread.is_alive()):
                return
            began, (stage, stage_began) = self.step_began, self.mark
            if began is None:
                continue
            now = self.clock()
            if (stage is not None and stage_began is not None
                    and now - stage_began > self._limit(stage)):
                stalled = {"stage": stage, "seconds": round(now - stage_began, 3),
                           "limit": self._limit(stage)}
            elif now - began > self.stall_seconds:
                stalled = {"stage": stage or "poll", "seconds": round(now - began, 3),
                           "limit": self.stall_seconds}
            else:
                continue
            self.stalled = stalled
            self.failures += 1
            self.counters["failures"] += 1
            error = BoundaryError(
                "rl_mu", "MU_FOLLOWER_STALLED",
                f"stalled_stage={stalled['stage']} seconds={stalled['seconds']} "
                f"limit={stalled['limit']}; repair the producing input")
            self.error = error
            self._give_up(error)
            return

    def _give_up(self, error: BaseException) -> None:
        with self.give_up_lock:
            if self.fatal is not None:
                return  # once: the poll thread and the watchdog may both get here
            self.fatal = error
        if getattr(self.source, "on_stage", None) == self._enter:
            setattr(self.source, "on_stage", None)  # noqa: B010 - MuSource has no hook
        print(json.dumps({"fast_mu_follower_stopped": repr(error),
                          "failures": self.failures, "offered": self.offered,
                          "stalled": self.stalled}),
              file=sys.stderr, flush=True)
        self.on_fatal(error)

    def start(self) -> FastMuFollower:
        if self.thread is not None:
            raise RuntimeError("fast_mu_follower_already_started")
        self.thread = threading.Thread(target=self._run, name="fast-mu-follower", daemon=True)
        self.thread.start()
        self.watchdog = threading.Thread(target=self._watch, name="fast-mu-follower-watchdog",
                                         daemon=True)
        self.watchdog.start()
        return self

    def stopped(self) -> bool:
        return self.thread is not None and not self.thread.is_alive()

    def check(self) -> None:
        """Raise the last error once the follower gave up; transient errors stay counted."""
        if self.fatal is not None:
            raise self.fatal

    def stop(self, timeout: float = 30.0) -> None:
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout)
        if self.watchdog is not None:
            self.watchdog.join(timeout)

    def close(self, timeout: float = 30.0) -> Any:
        """Stop following first, then close the service (never publish into a closed one)."""
        self.stop(timeout)
        if self.thread is not None and self.thread.is_alive():
            raise RuntimeError("fast_mu_follower_did_not_stop")
        return self.service.close()

    def status(self) -> dict[str, Any]:
        counters = {key: value for key, value in self.counters.items() if key != "failures"}
        return {"offered": self.offered, "behind": list(self.behind),
                "accepted_mu": None if self.accepted_mu is None else list(self.accepted_mu),
                "consecutive_failures": self.failures,
                "total_failures": self.counters["failures"],
                "error": None if self.error is None else repr(self.error),
                "fatal": None if self.fatal is None else repr(self.fatal),
                "stopped": self.stopped(), "stage": self.mark[0], "stalled": self.stalled,
                "stage_max_seconds": {key: round(value, 3)
                                      for key, value in dict(self.stage_max).items()},
                **counters}
