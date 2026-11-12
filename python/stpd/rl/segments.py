"""Bounded short-segment batching on the existing actor/learner exchange.

The producer supplies verified public transitions and one immutable behavior
snapshot. This module owns batch membership and use accounting, not legality or
game lifecycle. Whole segments enter a dataset; every admitted row is retained.
"""
from __future__ import annotations

import collections
import copy
import math
import random
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

OPERATIONAL_FIELDS = ("diagnostics_every", "archive_pending_bytes", "archive_min_free_bytes",
                      "archive_min_free_inodes",
                      "archive_poll_seconds", "input_compiler_identity", "input_pack_artifact_id",
                      "prepare_ahead", "inference_stream", "preparation_workers",
                      "preparation_threads", "prepared_bytes", "training_generation",
                      "rendezvous_path", "central_scoring", "checkpoint_seconds", "capacity_rows",
                      "capacity_limit_rows")

@dataclass(frozen=True)
class SegmentConfig:
    steps: int = 64
    dataset_rows: int = 1024
    minibatches: int = 4
    epochs: int = 1
    gae_lambda: float = 0.99
    episode_rows: int = 128
    capacity_rows: int = 4608
    capacity_limit_rows: int | None = None
    terminal_games: int = 256
    hp_battles: int = 128
    auxiliary_rows: int = 128
    terminal_uses: int = 2
    hp_uses: int = 1
    target_kl: float = 0.03
    ema_decay: float = 0.999
    head_lr: float = 1e-4
    checkpoint_seconds: float = 900
    inference_batch: int = 32
    inference_window_ms: float = 2
    diagnostics_every: int = 8
    excluded_seeds: list[str] = field(default_factory=list)
    warmup_artifact_id: str | None = None
    archive_pending_bytes: int = 4 * 1024**3
    archive_min_free_bytes: int = 20 * 1024**3
    archive_min_free_inodes: int = 10000
    archive_poll_seconds: float = 2
    input_compiler_identity: dict[str, Any] | None = None
    input_pack_artifact_id: str | None = None
    prepare_ahead: bool = True
    inference_stream: bool = False
    preparation_workers: int = 4
    preparation_threads: int = 1
    prepared_bytes: int = 1024 * 1024**2  # current + next; no unbounded ready queue
    training_generation: str | None = None
    # Original shared inference.json, atomically replaced on restore.
    rendezvous_path: str | None = None
    central_scoring: bool = False

    def __post_init__(self) -> None:
        positive = (self.steps, self.dataset_rows, self.minibatches, self.epochs,
            self.episode_rows, self.capacity_rows, self.terminal_games,
            self.hp_battles, self.terminal_uses, self.hp_uses,
            self.inference_batch, self.diagnostics_every, self.archive_pending_bytes,
            self.preparation_workers, self.preparation_threads, self.prepared_bytes)
        if (any(type(v) is not int or v < 1 for v in positive)
                or self.steps > 64 or self.episode_rows < self.steps
                or self.dataset_rows < self.minibatches
                or type(self.auxiliary_rows) is not int or self.auxiliary_rows < 0
                or type(self.gae_lambda) not in (int, float) or not 0 <= self.gae_lambda <= 1
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       for v in (self.target_kl, self.ema_decay, self.head_lr,
                                 self.inference_window_ms, self.checkpoint_seconds,
                                 self.archive_poll_seconds))
                or (self.capacity_limit_rows is not None and (
                    type(self.capacity_limit_rows) is not int
                    or self.capacity_limit_rows < 3 * self.dataset_rows))
                or self.capacity_rows < self.dataset_rows + self.steps
                or not 0 < self.target_kl < 1 or not 0 < self.ema_decay < 1
                or not 0 < self.head_lr < 1
                or not 0 <= self.inference_window_ms <= 100
                or not 0 < self.checkpoint_seconds <= 3600
                or type(self.archive_min_free_bytes) is not int or self.archive_min_free_bytes < 0
                or type(self.archive_min_free_inodes) is not int or self.archive_min_free_inodes < 0
                or not 0.01 <= self.archive_poll_seconds <= 30
                or type(self.prepare_ahead) is not bool or type(self.inference_stream) is not bool
                or type(self.central_scoring) is not bool
                or self.preparation_workers * self.preparation_threads > 16
                or (self.training_generation is not None and (
                    not isinstance(self.training_generation, str) or not self.training_generation))
                or (self.rendezvous_path is not None and (
                    not isinstance(self.rendezvous_path, str) or not self.rendezvous_path))
                or (self.input_compiler_identity is not None
                    and not isinstance(self.input_compiler_identity, dict))
                or (self.input_pack_artifact_id is not None and (
                    not isinstance(self.input_pack_artifact_id, str)
                    or len(self.input_pack_artifact_id) != 64
                    or any(c not in '0123456789abcdef' for c in self.input_pack_artifact_id)))
                or any(not isinstance(seed, str) or not seed for seed in self.excluded_seeds)
                or len(self.excluded_seeds) != len(set(self.excluded_seeds))):
            raise ValueError("invalid_segment_configuration")


def minibatch_members(count: int, parts: int = 4) -> list[list[int]]:
    """Balanced, exhaustive membership, including the whole-segment overshoot."""
    if type(count) is not int or count < parts or type(parts) is not int or parts < 1:
        raise ValueError("invalid_minibatch_membership")
    quotient, remainder = divmod(count, parts)
    offset, result = 0, []
    for index in range(parts):
        size = quotient + int(index < remainder)
        result.append(list(range(offset, offset + size)))
        offset += size
    return result


class SegmentQueue:
    """One capacity count includes ready rows and in-flight segment reservations.

    The learner removes a frozen dataset before training it. New reservations
    pause at capacity; committing an existing lease always remains possible.
    The live queue is discarded on CP-only restore, never replayed or re-offered.
    """

    def __init__(self, config: SegmentConfig) -> None:
        self.config = config
        self.pending: collections.deque[dict[str, Any]] = collections.deque()
        self.leases: dict[str, int] = {}
        self.cursors: dict[str, int] = {}
        self.rows = 0
        self.condition = threading.Condition()

    @property
    def occupied(self) -> int:
        return self.rows + sum(self.leases.values())

    def reserve(self, lease: str, *, replay: bool = False) -> bool:
        with self.condition:
            actor, separator, number = lease.rpartition("/")
            if not actor or separator != "/" or not number.isdigit():
                raise ValueError("invalid_segment_lease_identity")
            sequence = int(number)
            if sequence != self.cursors.get(actor, -1) + 1:
                raise ValueError("segment_lease_already_offered_or_out_of_order")
            if not replay and self.occupied + self.config.steps > self.config.capacity_rows:
                return False
            self.leases[lease] = self.config.steps
            self.cursors[actor] = sequence
            return True

    def validate_commit(self, lease: str, segment: dict[str, Any]) -> None:
        with self.condition:
            size = segment.get("rows")
            if (lease not in self.leases or segment.get("segment_id") != lease
                    or type(size) is not int or not 1 <= size <= self.leases[lease]
                    or not isinstance(segment.get("run_id"), str)):
                raise ValueError("segment_commit_binding_mismatch")

    def commit(self, lease: str, segment: dict[str, Any]) -> None:
        """After the producer's atomic durable commit, replace the exact lease."""
        with self.condition:
            self.validate_commit(lease, segment)
            size = segment["rows"]
            self.leases.pop(lease)
            self.pending.append(copy.deepcopy(segment))
            self.rows += size
            self.condition.notify_all()

    def admit(self, segment: dict[str, Any]) -> None:
        """fast_chain only: a sealed segment that never had an actor lease.

        The caller has made room first (`discard`); capacity is never exceeded.
        """
        with self.condition:
            size = segment.get("rows")
            if (type(size) is not int or not 1 <= size <= self.config.steps
                    or not isinstance(segment.get("run_id"), str)
                    or not isinstance(segment.get("segment_id"), str)
                    or segment["segment_id"] in self.leases
                    or any(s["segment_id"] == segment["segment_id"] for s in self.pending)):
                raise ValueError("fast_segment_admission_binding_mismatch")
            if self.occupied + size > self.config.capacity_rows:
                raise ValueError("fast_segment_admission_over_capacity")
            self.pending.append(copy.deepcopy(segment))
            self.rows += size
            self.condition.notify_all()

    def discard(self, segment_ids: set[str]) -> list[dict[str, Any]]:
        """Remove these not-yet-taken segments; returns what was removed (never retried)."""
        with self.condition:
            removed = [s for s in self.pending if s["segment_id"] in segment_ids]
            if removed:
                self.pending = collections.deque(
                    s for s in self.pending if s["segment_id"] not in segment_ids)
                self.rows -= sum(s["rows"] for s in removed)
                self.condition.notify_all()
            return removed

    def release(self, lease: str) -> None:
        """Close an unused reservation; this does not retry a game or its actions."""
        with self.condition:
            if lease not in self.leases:
                raise ValueError("segment_lease_missing")
            self.leases.pop(lease)
            self.condition.notify_all()

    def dataset(self, *, take: bool = True,
                exclude: frozenset[str] = frozenset()) -> list[dict[str, Any]] | None:
        with self.condition:
            totals: dict[str, int] = collections.defaultdict(int)
            chosen: list[dict[str, Any]] = []
            count = 0
            for segment in self.pending:
                if segment["segment_id"] in exclude:
                    continue
                run, size = segment["run_id"], segment["rows"]
                if totals[run] + size > self.config.episode_rows:
                    continue
                chosen.append(segment)
                totals[run] += size
                count += size
                if count >= self.config.dataset_rows:
                    break
            if count < self.config.dataset_rows:
                return None
            if not take:
                return chosen  # Read-ahead remains charged to the original queue capacity.
            members = {s["segment_id"] for s in chosen}
            self.pending = collections.deque(s for s in self.pending
                if s["segment_id"] not in members)
            self.rows -= count
            self.condition.notify_all()
            return chosen

    def state(self) -> dict[str, Any]:
        with self.condition:
            return copy.deepcopy({"config": asdict(self.config), "pending": list(self.pending),
                "leases": self.leases, "cursors": self.cursors, "rows": self.rows})

    def restore(self, value: dict[str, Any], *, accepted_input_log: bool = False) -> None:
        with self.condition:
            previous, expected = dict(value["config"]), asdict(self.config)
            for key in OPERATIONAL_FIELDS:
                previous.pop(key, None)
                expected.pop(key, None)
            if previous != expected:
                raise ValueError("segment_queue_configuration_changed")
            pending = copy.deepcopy(value["pending"])
            rows = sum(s["rows"] for s in pending)
            if (rows != value["rows"] or (not accepted_input_log
                    and rows + sum(value["leases"].values()) > self.config.capacity_rows)):
                raise ValueError("segment_queue_capacity_changed")
            self.pending = collections.deque(pending)
            self.leases = dict(value["leases"])
            self.cursors = dict(value["cursors"])
            self.rows = rows


class AuxiliaryPool:
    """Recent completed groups; original state references survive pool retirement."""

    def __init__(self, groups: int, uses: int) -> None:
        self.limit, self.max_uses = groups, uses
        self.groups: collections.OrderedDict[str, list[dict[str, Any]]] = collections.OrderedDict()
        # Row object -> its group, for `retire_exhausted(rows)`. Entries may go
        # stale (a group popped or replaced elsewhere); every use re-checks that
        # the row is still in that group, so a stale entry never retires anything.
        self._where: dict[int, tuple[dict[str, Any], str]] = {}

    def validate_add(self, identity: str, rows: list[dict[str, Any]]) -> None:
        if identity in self.groups:
            raise ValueError("auxiliary_group_already_added")
        keys = [r["key"] for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_auxiliary_state")

    def add(self, identity: str, rows: list[dict[str, Any]]) -> None:
        self.validate_add(identity, rows)
        self.groups[identity] = [copy.deepcopy(r) | {"uses": 0, "datasets": []} for r in rows]
        for row in self.groups[identity]:
            self._where[id(row)] = row, identity
        while len(self.groups) > self.limit:
            _, evicted = self.groups.popitem(last=False)
            for row in evicted:
                self._where.pop(id(row), None)
        if len(self._where) > 2 * len(self.groups) * max(1, len(rows)) + 4096:
            self._reindex()  # groups popped elsewhere leave stale entries; bound them

    def _reindex(self) -> None:
        self._where = {id(row): (row, identity)
                       for identity, rows in self.groups.items() for row in rows}

    def sample(self, count: int, rng: random.Random, *, epochs: int = 1,
               exclude: set[str] | None = None) -> list[dict[str, Any]]:
        values = [r for rows in self.groups.values() for r in rows
                  if r["uses"] + epochs <= self.max_uses
                  and len(r["datasets"]) < self.max_uses // epochs
                  and (exclude is None or r["key"] not in exclude)]
        return rng.sample(values, min(count, len(values)))

    def used(self, rows: list[dict[str, Any]], dataset: int, *, epochs: int = 1) -> None:
        # Call only after a successful optimizer.step, never for discarded KL work.
        for row in rows:
            if row["uses"] >= self.max_uses:
                raise ValueError("auxiliary_use_limit")
            if (dataset not in row["datasets"]
                    and len(row["datasets"]) >= self.max_uses // epochs):
                raise ValueError("auxiliary_dataset_limit")
        for row in rows:
            row["uses"] += 1
            if dataset not in row["datasets"]:
                row["datasets"].append(dataset)

    def retire_exhausted(self, rows: list[dict[str, Any]] | None = None
                         ) -> dict[str, list[dict[str, Any]]]:
        """Retire exhausted rows. `rows` (the rows just charged by `used`) checks
        only their groups: the same result as the full scan whenever the full
        scan ran after every earlier `used` (only charged rows can newly reach
        the limit); None is the full scan."""
        if rows is not None:
            touched = set()
            for row in rows:
                if row["uses"] < self.max_uses:
                    continue
                entry = self._where.get(id(row))
                if (entry is not None and entry[0] is row and entry[1] in self.groups
                        and any(kept is row for kept in self.groups[entry[1]])):
                    touched.add(entry[1])
            if not touched:
                return {}
            candidates = [(identity, self.groups[identity]) for identity in self.groups
                          if identity in touched]  # pool order, as the full scan
        else:
            candidates = list(self.groups.items())
        retired = {}
        for identity, rows in candidates:
            kept = [row for row in rows if row["uses"] < self.max_uses]
            if len(kept) != len(rows):
                retired[identity] = [row for row in rows if row["uses"] >= self.max_uses]
                for row in retired[identity]:
                    self._where.pop(id(row), None)
                if kept:
                    self.groups[identity] = kept
                else:
                    self.groups.pop(identity)
        return retired

    def state(self) -> dict[str, Any]:
        return copy.deepcopy({"limit": self.limit, "max_uses": self.max_uses,
            "groups": list(self.groups.items())})

    def restore(self, value: dict[str, Any]) -> None:
        if (value["limit"] != self.limit or value["max_uses"] != self.max_uses
                or len(value["groups"]) > self.limit):
            raise ValueError("auxiliary_pool_configuration_changed")
        self.groups = collections.OrderedDict(copy.deepcopy(value["groups"]))
        self._reindex()


def uniform_depth_indices(count: int, limit: int = 64) -> list[int]:
    if count < 1 or limit < 1:
        return []
    return sorted({round(i * (count - 1) / (min(count, limit) - 1))
                   for i in range(min(count, limit))}) if min(count, limit) > 1 else [0]
