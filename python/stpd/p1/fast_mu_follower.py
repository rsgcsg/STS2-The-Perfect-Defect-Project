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
- reading `latest()` is an idempotent read and is polled again next interval;
  a failed check, load or publish is counted with its error. `max_failures`
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
- shutdown order: `close()` stops this follower before it closes the service;
  once the service is stopping the follower publishes nothing and exits;
- weights for R7 mu archiving are not attached (`MuHandoff.weights=None`): no
  production `mu_archive` exists yet (AUDIT F8), so an R7 game would fail loud
  in the assembler (`audit_mu_archive_unconfigured` / `audit_mu_weights_missing`).
"""
from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from spireagent.json_boundary import BoundaryError
from stpd.rl.fast_assembly import MuHandoff

from .inference_service import MuSource


class FastMuFollower:
    """Poll the learner's latest mu and hot-swap it into a FastInferenceService."""

    def __init__(self, source: MuSource, service: Any, *, interval: float = 2.0,
                 max_failures: int = 3, clock: Callable[[], float] = time.monotonic,
                 on_fatal: Callable[[BaseException], None] | None = None) -> None:
        if not interval > 0 or type(max_failures) is not int or max_failures < 1:
            raise ValueError("invalid_fast_mu_follower_settings")
        self.source, self.service = source, service
        self.interval, self.max_failures, self.clock = interval, max_failures, clock
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
            identity = dict(self.source.latest())
            handoff = self.handoff(identity)
            if self.offered is not None and self.offered == handoff.identity():
                if not self.behind:
                    self.counters["unchanged"] += 1
                    return False
                return self._offer(self.model, handoff, list(self.behind), 0.0)
            if identity.get("compiler_identity") != self.service.compiler_identity:
                raise BoundaryError("rl_mu", "MU_COMPILER_IDENTITY_DIFFERS")
            began = self.clock()
            model = self.source.load(identity)
            load_seconds = self.clock() - began
            self.counters["load_seconds"] += load_seconds
            return self._offer(model, handoff, None, load_seconds)

    def _offer(self, model: Any, handoff: MuHandoff, targets: list[int] | None,
               load_seconds: float) -> bool:
        """Offer `handoff` to every service (`targets` None) or to the ones that refused it."""
        # This delivery happens once: from here on only an explicit refusal is
        # answered again; a raise or an invalid answer leaves nobody behind.
        self.offered, self.behind, self.model = handoff.identity(), [], None
        began = self.clock()
        if targets is not None and self.per_service:
            answer = self.service.publish(model, handoff, services=targets)
        else:
            answer = self.service.publish(model, handoff)
        done = self.clock()
        self.counters["publish_seconds"] += done - began
        record = {**handoff.identity(), "load_seconds": load_seconds,
                  "publish_seconds": done - began, "at": done}
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
            try:
                self.step()
                self.failures = 0
            except BaseException as error:  # noqa: BLE001 - kept and re-raised by check()
                if self.fatal is not None:
                    return  # step() already gave up (an invalid publish answer)
                if self._service_stopping():
                    return  # a publish raced the service's own stop: not this follower's
                self.failures += 1
                self.counters["failures"] += 1
                self.error = error
                if self.failures >= self.max_failures:
                    self._give_up(error)
                    return
            self.stopping.wait(self.interval)

    def _give_up(self, error: BaseException) -> None:
        self.fatal = error
        print(json.dumps({"fast_mu_follower_stopped": repr(error),
                          "failures": self.failures, "offered": self.offered}),
              file=sys.stderr, flush=True)
        self.on_fatal(error)

    def start(self) -> FastMuFollower:
        if self.thread is not None:
            raise RuntimeError("fast_mu_follower_already_started")
        self.thread = threading.Thread(target=self._run, name="fast-mu-follower", daemon=True)
        self.thread.start()
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
                "stopped": self.stopped(), **counters}
