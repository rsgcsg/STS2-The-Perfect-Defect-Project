"""WP8 mu handoff: the learner's published mu reaches the fast inference service.

The learner owns publication (`SegmentLearner._collect_publication` records the
identity in its fast intake, then serves it through `get_mu`). This follower is
the service-side half: it reads the learner's latest identity through a
`MuSource` (production: `NodeMuSource` on the learner attachment, the same node
cache transfer and load checks as the old S2-1 service), loads that snapshot on
CPU and offers it with `FastInferenceService.publish(model, MuHandoff(...))`.
From the next planned row on, `step_outcome` scores and segment headers carry
the new identity (`mu_snapshot_sha256`), and the learner trains a fast segment
only under a mu it recorded (`fast_segment_mu_not_published`).

Rules:
- one identity is offered at most once; the same weights under a new identity
  (a CP restore's new generation) are offered again, the assembler rebinds;
- a refused offer (older generation) is counted, never forced in;
- reading `latest()` is an idempotent read and is polled again next interval;
  a failed load or publish is counted with its error; `max_failures`
  consecutive failures stop the follower and `check()` raises the first error.
  The service keeps the mu it already holds (rows stay labelled with it), it
  never scores with a substitute;
- weights for R7 mu archiving are not attached (`MuHandoff.weights=None`): no
  production `mu_archive` exists yet (AUDIT F8), so an R7 game would fail loud
  in the assembler (`audit_mu_archive_unconfigured` / `audit_mu_weights_missing`).
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from stpd.rl.fast_assembly import MuHandoff

from .inference_service import MuSource


class FastMuFollower:
    """Poll the learner's latest mu and hot-swap it into a FastInferenceService."""

    def __init__(self, source: MuSource, service: Any, *, interval: float = 2.0,
                 max_failures: int = 3, clock: Callable[[], float] = time.monotonic) -> None:
        if not interval > 0 or type(max_failures) is not int or max_failures < 1:
            raise ValueError("invalid_fast_mu_follower_settings")
        self.source, self.service = source, service
        self.interval, self.max_failures, self.clock = interval, max_failures, clock
        self.offered: dict[str, Any] | None = None
        self.error: BaseException | None = None
        self.failures = 0
        self.history: list[dict[str, Any]] = []
        self.counters = {"polls": 0, "published": 0, "refused": 0, "unchanged": 0,
                         "failures": 0, "load_seconds": 0.0, "publish_seconds": 0.0}
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None

    @staticmethod
    def handoff(identity: dict[str, Any]) -> MuHandoff:
        return MuHandoff(identity["snapshot_sha256"], identity["training_generation"],
                         identity["version"], identity["updates"])

    def step(self) -> bool:
        """One poll; True when a new identity was accepted by the service."""
        with self.lock:
            self.counters["polls"] += 1
            identity = dict(self.source.latest())
            handoff = self.handoff(identity)
            if self.offered is not None and self.offered == handoff.identity():
                self.counters["unchanged"] += 1
                return False
            began = self.clock()
            model = self.source.load(identity)
            loaded = self.clock()
            accepted = bool(self.service.publish(model, handoff))
            done = self.clock()
            self.counters["load_seconds"] += loaded - began
            self.counters["publish_seconds"] += done - loaded
            # Offered once either way: a refused identity is not offered again.
            self.offered = handoff.identity()
            self.counters["published" if accepted else "refused"] += 1
            self.history.append({**handoff.identity(), "accepted": accepted,
                                 "load_seconds": loaded - began,
                                 "publish_seconds": done - loaded, "at": done})
            return accepted

    def _run(self) -> None:
        while not self.stopping.is_set():
            try:
                self.step()
                self.failures = 0
            except BaseException as error:  # noqa: BLE001 - kept and re-raised by check()
                self.failures += 1
                self.counters["failures"] += 1
                if self.error is None:
                    self.error = error
                if self.failures >= self.max_failures:
                    return
            self.stopping.wait(self.interval)

    def start(self) -> FastMuFollower:
        if self.thread is not None:
            raise RuntimeError("fast_mu_follower_already_started")
        self.thread = threading.Thread(target=self._run, name="fast-mu-follower", daemon=True)
        self.thread.start()
        return self

    def stopped(self) -> bool:
        return self.thread is not None and not self.thread.is_alive()

    def check(self) -> None:
        """Raise once the follower gave up; transient errors stay counted."""
        if self.stopped() and not self.stopping.is_set() and self.error is not None:
            raise self.error

    def stop(self, timeout: float = 30.0) -> None:
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout)

    def status(self) -> dict[str, Any]:
        return {"offered": self.offered, "failures": self.failures,
                "error": None if self.error is None else repr(self.error),
                "stopped": self.stopped(), **self.counters}
