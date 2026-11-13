"""Deletion of trained fast-chain segment originals (`.stfs`), CARD-RL5 §12.6 B.

Opt-in (`dataplane.release_trained_segments: true`; default false = every
original is kept, as before). The owner is the learner-side data-plane owner of
the segments: the learner process with `dataplane.shards` = 0, else each shard
process for the connections it serves. One `SegmentRelease` per owner.

A hand-off segment message names its node-local original (`path`). An original
is deleted only when all of these hold:

1. it is done here: at a checkpoint capture it is neither in the owner's
   `FastSources` (queued, prepared or training) nor in the intake's
   pre-attestation buffer, i.e. it was trained or dropped;
2. its game instance closed clean and whole (`close_reason` terminal, so not
   censored by stop/budget nor unknown/env_crash; `failure` null; no truncated
   or rejected segment; every sealed trainable segment received; not
   excluded), and that close was seen here before the previous capture
   (one capture of grace: an actor's `fast_instance_excluded` follows the close
   by seconds, and an admission in flight finishes long before);
3. the game is not an R7 audit game (`audit_retain_mu` in the header: kept
   whole, option d);
4. the checkpoint whose capture marked it was published durably
   (`CheckpointPublisher.take`, full readback) and input is not fenced.

Never deleted: segments of abnormal games (counted `kept_abnormal_*`), R7 games
(`kept_r7_*`), truncated / rejected originals (never handed off, so never
named here), anything after a fence, and the last interval of a learner that
dies (no sweeper: those stay on disk). A restore is CP-only
(`discard_hot_inputs_resume_committed_rng`) and reads no original.

Unlinks run on one background thread. A failed unlink is counted
(`delete_failed`), logged to stderr and reported through `alarm`; training
goes on. `status()` reports the bytes pending deletion and the peak.
"""

from __future__ import annotations

import collections
import contextlib
import queue
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fast_handoff import SUFFIX

RELEASE_OPTION = "release_trained_segments"


def release_option(dataplane: dict[str, Any] | None) -> bool:
    value = (dataplane or {}).get(RELEASE_OPTION, False)
    if type(value) is not bool:
        raise ValueError("dataplane_release_trained_segments_must_be_bool")
    return value


@dataclass
class _Game:
    segments: dict[str, tuple[Path, int]] = field(default_factory=dict)
    received: int = 0
    r7: bool = False
    closed_at: int | None = None  # capture epoch the clean close was seen in
    marked: int = 0  # originals already marked for deletion


class SegmentRelease:
    """Tracks one data-plane owner's originals and deletes the covered ones."""

    def __init__(self, *, held: Callable[[], set[str]], fenced: Callable[[], bool],
                 alarm: Callable[..., None] | None = None,
                 remember: int = 65536) -> None:
        self.held, self.fenced, self.alarm = held, fenced, alarm
        self.lock = threading.Lock()
        self.games: dict[str, _Game] = {}
        self.epoch = 0  # captures so far
        # capture sequence -> originals that capture found done (deleted when that
        # checkpoint is published).
        self.marked: dict[int, list[tuple[Path, int]]] = {}
        self.released: collections.OrderedDict[str, None] = collections.OrderedDict()
        self.remember = remember
        self.counters: dict[str, int] = collections.defaultdict(int, {key: 0 for key in (
            "tracked", "deleted", "deleted_bytes", "delete_failed", "delete_failed_bytes",
            "kept_abnormal_segments", "kept_abnormal_bytes", "kept_r7_segments",
            "kept_r7_bytes", "kept_fenced", "excluded_after_release", "untracked_path")})
        self.pending_bytes = self.peak_pending_bytes = 0
        self.work: queue.Queue[list[tuple[Path, int]] | None] = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="rl-segment-release",
                                       daemon=True)
        self.thread.start()

    # -- intake side (receiver threads) -------------------------------------------
    def received(self, message: dict[str, Any], header: dict[str, Any] | None) -> None:
        """A hand-off segment was applied (admitted, buffered or dropped)."""
        path, segment_id = Path(str(message.get("path"))), str(message["segment_id"])
        if header is None or path.name != segment_id + SUFFIX:
            with self.lock:
                self.counters["untracked_path"] += 1
            return
        size = int(message["size"])
        instance = str(header["game_instance_id"])
        with self.lock:
            game = self.games.setdefault(instance, _Game())
            game.received += 1
            game.r7 = game.r7 or bool(header.get("audit_retain_mu"))
            game.segments[segment_id] = (path, size)
            self._pending(size)
            self.counters["tracked"] += 1

    def closed(self, message: dict[str, Any], *, excluded: bool) -> None:
        instance = str(message["game_instance_id"])
        sealed = message["segments"]["bootstrap"] + message["segments"]["terminal"]
        # Censored (stop/budget) and abnormal games keep everything.
        clean = (not excluded and message["failure"] is None
                 and message["close_reason"] == "terminal"
                 and message["segments"]["truncated"] == 0
                 and message["segments"]["rejected"] == 0)
        with self.lock:
            game = self.games.get(instance)
            if game is None:
                return
            if game.r7:
                self._keep(instance, game, "kept_r7")
            elif not clean or game.received != sealed:
                self._keep(instance, game, "kept_abnormal")
            else:
                game.closed_at = self.epoch

    def excluded(self, instance: str) -> None:
        """An exclusion was applied (actor signal, possibly after the close)."""
        with self.lock:
            game = self.games.get(instance)
            if game is not None:
                self._keep(instance, game, "kept_abnormal")
            if (game is not None and game.marked) or (game is None
                                                      and instance in self.released):
                # Later than one capture after its close: some originals may be gone.
                self.counters["excluded_after_release"] += 1
                self._loud("excluded_after_release", instance=instance)

    # -- checkpoint side ----------------------------------------------------------
    def capture(self, sequence: int) -> None:
        """At a checkpoint capture (dataset boundary): mark what is done."""
        held = self.held()
        with self.lock:
            marked: list[tuple[Path, int]] = []
            for instance in list(self.games):
                game = self.games[instance]
                if game.closed_at is None or game.closed_at >= self.epoch:
                    continue
                for segment_id in [s for s in game.segments if s not in held]:
                    marked.append(game.segments.pop(segment_id))
                    game.marked += 1
                if not game.segments:
                    del self.games[instance]
                    self.released[instance] = None
                    while len(self.released) > self.remember:
                        self.released.popitem(last=False)
            if marked:
                self.marked[sequence] = marked
            self.epoch += 1

    def covered(self, sequence: int) -> None:
        """Checkpoint `sequence` is published durably: delete what it covers."""
        with self.lock:
            due = [s for s in self.marked if s <= sequence]
            batch = [item for s in sorted(due) for item in self.marked.pop(s)]
            if not batch:
                return
            if self.fenced():
                self.counters["kept_fenced"] += len(batch)
                self.counters["kept_fenced_bytes"] += sum(size for _, size in batch)
                self._pending(-sum(size for _, size in batch))
                return
        self.work.put(batch)

    def status(self) -> dict[str, Any]:
        with self.lock:
            open_bytes = sum(size for game in self.games.values()
                             for _, size in game.segments.values())
            marked = sum(size for items in self.marked.values() for _, size in items)
            return {**self.counters, "pending_bytes": self.pending_bytes,
                    "peak_pending_bytes": self.peak_pending_bytes,
                    "tracked_open_bytes": open_bytes, "marked_bytes": marked,
                    "tracked_games": len(self.games)}

    def close(self) -> None:
        """Finish the deletions already due; nothing new is started."""
        self.work.put(None)
        self.thread.join()

    # -- internals ----------------------------------------------------------------
    def _pending(self, delta: int) -> None:
        self.pending_bytes += delta
        self.peak_pending_bytes = max(self.peak_pending_bytes, self.pending_bytes)

    def _keep(self, instance: str, game: _Game, counter: str) -> None:
        del self.games[instance]
        size = sum(size for _, size in game.segments.values())
        self.counters[counter + "_games"] += 1
        self.counters[counter + "_segments"] += len(game.segments)
        self.counters[counter + "_bytes"] += size
        self._pending(-size)

    def _loud(self, kind: str, **fields: Any) -> None:
        print(f"segment_release {kind}: {fields}", file=sys.stderr, flush=True)
        if self.alarm is not None:
            with contextlib.suppress(Exception):  # an alarm never stops training
                self.alarm("fast_segment_release_" + kind, **fields)

    def _run(self) -> None:
        while True:
            batch = self.work.get()
            if batch is None:
                return
            for path, size in batch:
                if self.fenced():
                    with self.lock:
                        self.counters["kept_fenced"] += 1
                        self.counters["kept_fenced_bytes"] += size
                        self._pending(-size)
                    continue
                try:
                    path.unlink()
                except OSError as error:
                    with self.lock:
                        self.counters["delete_failed"] += 1
                        self.counters["delete_failed_bytes"] += size
                        self._pending(-size)
                    self._loud("delete_failed", path=str(path),
                               error=f"{type(error).__name__}: {error}"[:300])
                    continue
                with self.lock:
                    self.counters["deleted"] += 1
                    self.counters["deleted_bytes"] += size
                    self._pending(-size)
