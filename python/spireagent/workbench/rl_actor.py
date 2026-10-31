"""CPU RL actor over the existing Managed ledger, executor and trajectory pipeline.

One actor process plays one game at a time on the original ``run_managed`` path
(no second game loop). Before each small ledger it takes the newest model
version that has already been fetched and hash-checked in the background, so a
model download never pauses play. Each sealed trajectory shard is announced to
the learner as one immutable exchange record; unknown/censored games are
announced too, never retried. Many actor processes, on one or many machines,
differ only in ``actor`` name and their execution file.
"""
from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
import os
import re
import resource
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.rl_exchange import Exchange
from spireagent.storage.blobs import StoreError

CONFIG_SCHEMA = "spireagent/rl-actor-config-1"
SEED_GAME_VERSION = "v0.111.0"


def reject(reason: str) -> None:
    raise BoundaryError("rl_actor", reason)


def _hashed(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _cpu_seconds() -> float:
    """This actor plus every reaped child (Host, Runtime, Agent) in user+system seconds."""
    total = 0.0
    for who in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN):
        usage = resource.getrusage(who)
        total += usage.ru_utime + usage.ru_stime
    return total


class ModelPrefetch:
    """Background: notice newer versions and materialize them; play never waits on this."""

    def __init__(self, exchange: Exchange, cache: Path, poll_seconds: float) -> None:
        self.exchange, self.cache, self.poll_seconds = exchange, cache, poll_seconds
        self.ready: dict[int, tuple[dict[str, Any], Path]] = {}
        self.fetch_seconds: list[float] = []
        self.error: dict[str, Any] | None = None
        self.failures = 0
        self.operation = "latest_model"
        self.events: list[dict[str, Any]] = []
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, name="rl-model-prefetch", daemon=True)

    def start(self) -> None:
        self._refresh_once()
        if self.error is None:
            self.thread.start()

    def refresh(self) -> None:
        self.operation = "latest_model"
        latest = self.exchange.latest_model()
        if latest is None:
            return
        with self.lock:
            if latest["version"] in self.ready:
                return
        began = time.monotonic()
        self.operation = "materialize_model"
        path = self.exchange.materialize_model(latest, self.cache)
        with self.lock:
            self.ready[latest["version"]] = (latest, path)
            self.fetch_seconds.append(time.monotonic() - began)

    def _event(self, kind: str, **fields: Any) -> None:
        # Classified codes only: raw SDK exception text can contain credentials/URLs.
        event = {"kind": kind, "at": time.time(), **fields}
        self.events.append(event)
        print(json.dumps({"stage": "rl_model_prefetch", **event}), file=sys.stderr, flush=True)

    def _refresh_once(self) -> None:
        try:
            self.refresh()
        except Exception as error:  # noqa: BLE001 -- every failure is recorded and bounded
            self.failures += 1
            transient = (isinstance(error, StoreError)
                         and error.code in {"s3_get_failed", "s3_list_failed",
                                            "s3_stream_read_failed"})
            failure = {"operation": self.operation, "error_type": type(error).__name__,
                       "error_code": error.code if isinstance(error, BoundaryError) else None,
                       "errno": error.errno if isinstance(error, OSError) else None,
                       "consecutive_failures": self.failures}
            self._event("read_failed", **failure,
                        retry=transient and self.failures < 3)
            if not transient or self.failures >= 3:
                self.error = failure
        else:
            if self.failures:
                self._event("read_recovered", consecutive_failures=self.failures)
            self.failures = 0

    def _loop(self) -> None:
        while not self.stopping.wait(self.poll_seconds):
            self._refresh_once()
            if self.error is not None:
                return

    def newest(self) -> tuple[dict[str, Any], Path] | None:
        if self.error:
            raise BoundaryError("rl_actor", "model_prefetch_failed",
                                json.dumps(self.error, sort_keys=True)) from None
        with self.lock:
            return self.ready[max(self.ready)] if self.ready else None

    def stop(self) -> None:
        self.stopping.set()


class Actor:
    def __init__(self, configuration: dict[str, Any], work: Path) -> None:
        if configuration.get("schema") != CONFIG_SCHEMA:
            reject("unsupported_actor_config")
        self.config, self.work = configuration, work
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.base = json.loads(Path(configuration["execution"]).read_bytes())
        if self.base.get("peer") != configuration["peer"]:
            reject("actor_exchange_must_use_execution_peer")
        if self.base.get("retention") != {"tier": "trajectory", "purpose": "training",
                                          "audit_every": 50}:
            reject("unscored_trajectory_training_intent_required")
        self.exchange = Exchange(configuration["peer"], configuration["run"])
        self.name = configuration["actor"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", self.name):
            reject("invalid_actor_name")
        self.prefetch = ModelPrefetch(self.exchange, work / "models",
                                      configuration.get("model_poll_seconds", 5))
        self.executor: Any = None
        self.executor_version: int | None = None
        self.sequence = len(self.exchange.blobs.keys(
            f"{self.exchange.prefix}shards/{self.name}/"))
        # A restarted actor of the same name continues past its announced shards,
        # so it never replays (and the learner never double-counts) earlier seeds.
        self.epoch = self.sequence
        ledgers = work / "ledgers"
        self.chunk = len(list(ledgers.iterdir())) if ledgers.exists() else 0
        self.counts: collections.Counter[str] = collections.Counter()
        self.timing: collections.defaultdict[str, float] = collections.defaultdict(float)
        self.games: list[dict[str, Any]] = []
        self.signalled: int | None = None

    def _signal(self, number: int, _frame: Any) -> None:
        """Batch schedulers warn before the limit; finish the current game, start no more."""
        self.signalled = number

    def _executor(self, record: dict[str, Any], package: Path) -> str:
        from .managed_pipeline import PipelinedManagedExecutor

        selection = f"rl-v{record['version']:06d}"
        if self.executor_version == record["version"]:
            return selection
        began = time.monotonic()
        execution = copy.deepcopy(self.base)
        execution["models"] = {selection: {"artifact_id": record["model_artifact_id"],
                                           "package_directory": str(package),
                                           "behavior_policy": self.config["behavior"]}}
        execution["pipeline"] = self.config.get("pipeline", {"save_workers": 1,
                                                             "pending_limit": 2})
        path = self.work / "executions" / f"{selection}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(json_bytes(execution))
        if self.executor is not None:
            self.executor.close()
        self.executor = PipelinedManagedExecutor(
            execution, episode_seconds=self.config["episode_seconds"], cleanup_seconds=120)
        self.executor.execution_path = path
        self.executor_version = record["version"]
        self.timing["executor_prepare"] += time.monotonic() - began
        self.counts["executor_builds"] += 1
        return selection

    def _ledger(self, selection: str) -> Any:
        from stpd.evaluation.suites import diagnostic_reference_manifest

        from .evaluation_ledger import EvaluationLedger

        self.chunk += 1
        root = self.work / "ledgers" / f"{self.chunk:06d}"
        root.mkdir(parents=True)
        group = hashlib.sha256(f"rl\0{self.config['run']}\0{self.config['seed_namespace']}\0"
                               f"{self.name}\0{self.epoch}\0{self.chunk}".encode()).hexdigest()[:32]
        manifest = diagnostic_reference_manifest(SEED_GAME_VERSION, group,
                                                 self.config["games_per_ledger"])
        manifest["host"] = "Managed"
        path = root / "manifest.json"
        path.write_bytes(json_bytes(manifest))
        return EvaluationLedger.create(root / "ledger", path, _hashed(path),
            selections={"model": selection}, execution=self.executor.execution_path)

    def _announce(self, record: dict[str, Any], slots: list[dict[str, Any]]) -> None:
        shards: dict[str | None, list[dict[str, Any]]] = collections.defaultdict(list)
        for slot in slots:
            report = (json.loads(Path(slot["attempt_report"]).read_bytes())
                      if slot.get("attempt_report") else {"episodes": [{}]})
            row = report["episodes"][0] if report.get("episodes") else {}
            game = {"seed": slot["seed"], "status": slot["status"],
                    "model_version": record["version"],
                    "reason": slot.get("reason") or row.get("reason"),
                    "run_id": slot.get("run_id") or row.get("run_id"),
                    "shard": slot.get("trajectory_shard_id") or row.get("trajectory_shard_id"),
                    "elapsed_seconds": row.get("elapsed_seconds"),
                    "started_at": row.get("pipeline_started_at"),
                    "model_published_at": record.get("published_at"),
                    "phase_seconds": row.get("phase_seconds"),
                    "behavior_policy": row.get("behavior_policy")}
            self.games.append(game)
            self.counts["games_" + str(slot["status"])] += 1
            shards[game["shard"]].append(game)
        for shard, games in shards.items():
            disposition = "trajectory" if shard else "without_shard"
            self.exchange.publish_shard(self.name, self.sequence, {
                "shard_artifact_id": shard,
                "run_ids": [g["run_id"] for g in games] if shard else [],
                "model_version": record["version"],
                "model_artifact_id": record["model_artifact_id"],
                "disposition": disposition, "games": games, "finished_at": time.time()})
            self.sequence += 1

    def run(self) -> dict[str, Any]:
        from .reference_suite import SuiteLimits

        began = time.monotonic()
        cpu_began = _cpu_seconds()
        deadline = began + self.config["wall_seconds"]
        episode_seconds = self.config["episode_seconds"]
        self.prefetch.start()
        status = "completed"
        previous = {number: signal.signal(number, self._signal)
                    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)}
        try:
            while (len(self.games) < self.config["max_games"]
                   and time.monotonic() + episode_seconds + 180 < deadline
                   and not (self.work / "STOP").exists() and self.signalled is None):
                newest = self.prefetch.newest()
                if newest is None:
                    wait = time.monotonic()
                    time.sleep(1)
                    self.timing["wait_first_model"] += time.monotonic() - wait
                    continue
                record, package = newest
                selection = self._executor(record, package)
                step = time.monotonic()
                ledger = self._ledger(selection)
                self.timing["ledger_create"] += time.monotonic() - step
                step = time.monotonic()
                result = ledger.run_managed(workers=1, max_episodes=self.config["games_per_ledger"],
                    limits=SuiteLimits(150, episode_seconds, 120,
                                       max(1, deadline - time.monotonic() - 120), 1, 30),
                    executor=self.executor)
                self.timing["ledger_run"] += time.monotonic() - step
                step = time.monotonic()
                self._announce(record, [s for s in ledger.slots() if s["status"] != "not_started"])
                self.timing["announce"] += time.monotonic() - step
                if result.get("status") == "stopped":
                    # Every ledger stop is the owner's to read (unknown cleanup not
                    # accepted, save failed, capacity, wall limit); never play on past it.
                    status = f"stopped_ledger:{result.get('stop_reason')}"
                    break
                if self._unknown_limit():
                    status = "stopped_unknown_limit"
                    break
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
            self.prefetch.stop()
            if self.executor is not None:
                self.executor.close()
        summary = {"schema": "spireagent/rl-actor-summary-1", "actor": self.name,
                   "run": self.config["run"],
                   "status": "stopped_signal" if self.signalled and status == "completed"
                   else status, "signal": self.signalled,
                   "wall_seconds": time.monotonic() - began, "counts": dict(self.counts),
                   "cpu_seconds": _cpu_seconds() - cpu_began,
                   "timing": dict(self.timing),
                   "model_fetch_seconds": self.prefetch.fetch_seconds,
                   "model_prefetch_events": self.prefetch.events, "games": self.games}
        (self.work / "actor-summary.json").write_bytes(json_bytes(summary))
        return summary

    def _unknown_limit(self) -> bool:
        """Stop this actor (never retry) when unknowns cluster; the learner keeps going."""
        limit = self.config.get("unknown_stop", {"consecutive": 3, "fraction": 0.25,
                                                 "after": 8})
        statuses = [g["status"] for g in self.games]
        tail = statuses[-limit["consecutive"]:]
        if len(tail) == limit["consecutive"] and all(s == "unknown" for s in tail):
            return True
        return bool(len(statuses) >= limit["after"]
                    and statuses.count("unknown") / len(statuses) > limit["fraction"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--actor", help="override the actor name, e.g. one per batch task")
    args = parser.parse_args()
    os.umask(0o077)
    configuration = json.loads(args.config.read_bytes())
    if args.actor:
        configuration["actor"] = args.actor
    summary = Actor(configuration, args.work.resolve()).run()
    print(json.dumps({k: summary[k] for k in ("actor", "status", "counts")}))


if __name__ == "__main__":
    main()
