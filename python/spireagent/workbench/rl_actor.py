"""CPU actor on the original Managed ledger, executor and save pipeline.

Live RL4 keeps the executor/registration package stable, adopts central immutable
mu at segment boundaries, and publishes verified short segments through the same
Store/Exchange. Legacy prefetch/whole-episode helpers remain for archived data
and engineering fixtures; the live CLI requires explicit segments.
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

    def __init__(self, exchange: Exchange, cache: Path, poll_seconds: float, *,
                 training_generation: str | None = None) -> None:
        self.exchange, self.cache, self.poll_seconds = exchange, cache, poll_seconds
        self.training_generation = training_generation
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
        latest = (self.exchange.latest_model(training_generation=self.training_generation)
                  if self.training_generation is not None else self.exchange.latest_model())
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
        self.segment_mode = "segments" in configuration
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.base = json.loads(Path(configuration["execution"]).read_bytes())
        if self.base.get("peer") != configuration["peer"]:
            reject("actor_exchange_must_use_execution_peer")
        from .managed_retention import retention_configuration

        retention = retention_configuration(self.base)
        if (retention["tier"] != "trajectory" or retention.get("purpose") != "training"
                or retention["score_eligible"]):
            reject("unscored_trajectory_training_intent_required")
        self.exchange = Exchange(configuration["peer"], configuration["run"])
        # OPS-2: no NFS archive lock or R2 upload on the actor's hot path, but only when
        # the plan says a dedicated archiver (SegmentWatcher) consumes the shared outbox.
        # Otherwise the actor copies its own intents as before: never a silent loss.
        publisher = configuration.get("archive_publisher")
        if publisher not in {None, "shared"}:
            reject("explicit_archive_publisher_required")
        if publisher == "shared":
            self.exchange.defer_archive()
        self.name = configuration["actor"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", self.name):
            reject("invalid_actor_name")
        generation = (configuration["segments"]["inference"].get("training_generation")
                      if self.segment_mode else None)
        if self.segment_mode and not isinstance(generation, str):
            reject("explicit_segment_training_generation_required")
        self.prefetch = ModelPrefetch(self.exchange, work / "models",
            configuration.get("model_poll_seconds", 5), training_generation=generation)
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
        self.segment_stop_reason: str | None = None
        self.run_deadline: float | None = None
        self.service = None
        if self.segment_mode:
            from stpd.p1.local_inference import SegmentConnection
            from stpd.p1.local_mu import (
                ActorControlConnection,
                ActorInferencePool,
                LocalMuScorer,
                RemoteMuScorer,
            )

            remote = dict(self.config["segments"]["inference"])
            if "rendezvous" in self.config["segments"]:
                remote["rendezvous"] = self.config["segments"]["rendezvous"]
            scoring = self.config["segments"].get("scoring_service")
            if scoring is not None:
                # RL5 S2-1, explicit opt-in: control stays on the learner
                # channel; only scores go to the standalone service. No
                # fallback to local mu when the service is unavailable.
                service = json.loads(Path(scoring).read_bytes())
                if service.get("role") != "inference_service":
                    reject("scoring_service_attachment_role_differs")
                scorer: LocalMuScorer | RemoteMuScorer = RemoteMuScorer(
                    {**service, "rendezvous": scoring}, exchange=self.exchange,
                    cache_root=work / "mu")
            elif remote.get("role") == "learner_central":
                scorer = RemoteMuScorer(remote)
            else:
                scorer = LocalMuScorer(self.exchange, work / "mu")
            self.actor_pool = ActorInferencePool(SegmentConnection(remote), scorer)
            self.local_attachment = self.actor_pool.attachment()
            self.service = ActorControlConnection(self.actor_pool)
        # RL5 P-REWRITE WP7, explicit opt-in (default absent = the old chain unchanged):
        # K games per environment process. A failed start or run stops loudly; never
        # a fallback to the old chain.
        self.fast_chain: dict[str, Any] | None = None
        if "fast_chain" in configuration:
            self.fast_chain = self._fast_chain_configuration(configuration["fast_chain"])

    def _fast_chain_configuration(self, value: Any) -> dict[str, Any]:
        from .fast_chain_episodes import load_env_config
        from .managed_retention import pipeline_configuration

        if (not self.segment_mode or not isinstance(value, dict)
                or set(value) - {"command", "env_config", "step_sync_ms",
                                 "inference_failure_streak", "r7"}
                or not isinstance(value.get("command"), list) or not value["command"]
                or any(not isinstance(item, str) or not item for item in value["command"])
                or not isinstance(value.get("env_config"), str)):
            reject("fast_chain_configuration_invalid")
        env = load_env_config(Path(value["env_config"]))
        # Owner rule (CARD-RL5 §12.6, 2026-11-06, restated 11-09 and 11-13): an ordinary
        # clean fast-chain training game keeps only its one-line record; there is no
        # whole-game sample, so `retention.audit_every` does not apply here (the old
        # chain keeps its 5% sample unchanged). Originals are kept for evaluation, R7
        # (`r7`) and every abnormal close (the environment's evidence sink).
        pipeline = pipeline_configuration({"pipeline": self.config.get("pipeline", {
            "save_workers": 1, "pending_limit": 2})})
        if pipeline["pending_limit"] < env["slots"]:
            # The ledger admits at most pending_limit unsettled games at once.
            reject("fast_chain_pending_limit_below_slots")
        # One R7 selection per actor, shared by every executor build (model versions);
        # its window count persists under this actor's work directory by default.
        r7 = None
        if "r7" in value:
            from .fast_chain_episodes import R7Selection

            r7 = R7Selection.from_configuration(value["r7"], work=self.work)
        return {**{k: v for k, v in value.items() if k != "r7"}, "env": env, "r7": r7}

    def _stop_before_offer(self) -> bool:
        while True:
            if self._stop_or_ready_before_offer():
                return True
            if self.service is None or not getattr(self, "archive_paused", False):
                return False
            deadline = getattr(self, "run_deadline", None)
            if deadline is not None and time.monotonic() + 60 >= deadline:
                self.segment_stop_reason = "actor_wall_limit_before_offer"
                return True
            time.sleep(1)

    def _stop_or_ready_before_offer(self) -> bool:
        if self.signalled is not None or (self.work/"STOP").exists():
            return True
        if self.segment_stop_reason:
            return True
        if self.exchange.admission_pause(self.config.get("budget_id")) is not None:
            self.segment_stop_reason = "sampling_audit_inconclusive_admission_paused"
            return True
        if self.service is not None:
            try:
                state = self.service.request("status")
            except (OSError, ValueError, TimeoutError):
                # This read precedes the durable World offer. No game/action is
                # unknown or retried merely because the learner has closed.
                self.segment_stop_reason = "service_status_unavailable_before_offer"
            else:
                if not state["accepting_new_segments"]:
                    self.segment_stop_reason = "learner_segment_budget_complete"
                self.archive_paused = not state.get("archive_capacity", {"ready": True})["ready"]
        return self.segment_stop_reason is not None

    def _signal(self, number: int, _frame: Any) -> None:
        """Batch schedulers warn before the limit: start no more games. The old chain
        finishes the current game; the fast chain closes in-flight games at their next
        boundary (censored ``stop``) within its Stop bound."""
        self.signalled = number
        if getattr(self, "fast_chain", None) is not None:
            self._fast_chain_stop("signal")

    def _executor(self, record: dict[str, Any], package: Path) -> str:
        from .managed_pipeline import PipelinedManagedExecutor

        selection = f"rl-v{record['version']:06d}"
        if self.segment_mode and self.executor is not None:
            # Registration/package/lifecycle stay stable; this actor's CPU scorer
            # adopts new immutable weights only at segment boundaries.
            return f"rl-v{self.executor_version:06d}"
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
        if self.fast_chain is not None:
            self.executor = self._fast_chain_executor(execution)
        elif self.segment_mode:
            from .rl_segment_producer import SegmentManagedExecutor

            self.executor = SegmentManagedExecutor(execution,
                episode_seconds=self.config["episode_seconds"], cleanup_seconds=120,
                exchange=self.exchange, actor=self.name,
                attachment=self.local_attachment, admission=self.service,
                budget_id=self.config.get("budget_id"))
        else:
            self.executor = PipelinedManagedExecutor(
                execution, episode_seconds=self.config["episode_seconds"], cleanup_seconds=120)
        self.executor.execution_path = path
        self.executor_version = record["version"]
        self.timing["executor_prepare"] += time.monotonic() - began
        self.counts["executor_builds"] += 1
        return selection

    def _fast_chain_executor(self, execution: dict[str, Any]) -> Any:
        from functools import partial

        from .fast_chain_episodes import FastChainEpisodeExecutor, GameManager
        from .rl_segment_producer import (
            fast_instance_admission,
            fast_instance_excluded,
            open_learner_episode,
            publish_world_offered,
        )

        assert self.fast_chain is not None
        manager = GameManager(self.fast_chain["command"], self.fast_chain["env"],
            work=self.work / "fast-env", step_sync_ms=self.fast_chain.get("step_sync_ms", 4),
            inference_failure_streak=self.fast_chain.get("inference_failure_streak", 3))
        manager.start()  # Loud on failure: BoundaryError fast_env_start_failed.
        return FastChainEpisodeExecutor(execution, manager=manager,
            episode_seconds=self.config["episode_seconds"],
            before_world_offer=partial(open_learner_episode, exchange=self.exchange,
                                       admission=self.service, actor=self.name,
                                       budget_id=self.config.get("budget_id"),
                                       whole_game_sample=False),
            world_offered=partial(publish_world_offered, exchange=self.exchange,
                                  actor=self.name),
            # After a checked ``opened``: the learner attests the instance once (seed
            # check, game_instance_id -> intent); no fast-chain episode_end is sent.
            instance_opened=partial(fast_instance_admission, admission=self.service,
                                    actor=self.name),
            instance_excluded=partial(fast_instance_excluded, admission=self.service,
                                      actor=self.name),
            r7_select=self.fast_chain["r7"])

    def _fast_chain_stop(self, reason: str) -> None:
        """Bounded Stop of in-flight games on its own thread (never in a handler).

        Started once; in-flight games are closed at their next boundary (censored
        ``stop``), not played to the end; past the Stop bound the group is killed.
        """
        manager = getattr(self.executor, "manager", None)
        if (self.fast_chain is not None and manager is not None
                and not getattr(self, "_fast_chain_stopping", False)):
            self._fast_chain_stopping = True
            threading.Thread(target=manager.stop, args=(reason,), name="fast-chain-stop",
                             daemon=True).start()

    def _fast_chain_stop_before_offer(self) -> bool:
        if (self.work / "STOP").exists() or self.signalled is not None:
            self._fast_chain_stop("stop_requested")
        if self._stop_before_offer():
            return True
        closed = self.executor.admission_closed()
        if closed is not None:
            self.segment_stop_reason = "fast_chain:" + closed
            return True
        return False

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
        if self.segment_mode:
            from stpd.rl.critic import held_out

            excluded = set(self.config["segments"]["excluded_seeds"])
            # The existing generator already rejects permanent suites. Preserve
            # the old RL3 held-out membership and explicit historical manifests.
            # Only never-offered seed candidates are skipped, never old attempts.
            seeds = manifest["seeds"]
            if any(seed in excluded or held_out(seed) for seed in seeds):
                return None
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
            matches = [row for row in report.get("episodes", ())
                       if row.get("run_id") == slot.get("run_id")]
            row = matches[0] if len(matches) == 1 else {}
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
            if self.segment_mode:
                game.update(intent_id=row.get("intent_id"),
                            training_generation=(row.get("segment_admission_owner") or {}).get(
                                "training_generation"),
                            world_mutation_offered=slot.get("world_mutation_offered") is True,
                            segment_budget_close=row.get("segment_budget_close") is True,
                            cleanup_accepted=row.get("cleanup_accepted") is True,
                            segment_prefix_close=bool(row.get("segment_delivery")))
                if row.get("chain") == "fast_chain":
                    close = row.get("fast_chain_close") or {}
                    game.update(game_instance_id=row.get("game_instance_id"),
                                fast_chain_slot=row.get("fast_chain_slot"),
                                close_reason=close.get("reason"),
                                close_detail=close.get("detail"))
            if slot["status"] in {"unknown", "save_failed"}:
                # Diagnostic close facts only (U3): reason + last receipt reach
                # the durable learner event/WAL and the watcher even when this
                # node-local summary is never copied. No training field changes.
                game["failure"] = row.get("failure_facts") or {
                    "execution_reason": row.get("execution_reason"),
                    "save_error": row.get("save_error")}
            self.games.append(game)
            self.counts["games_" + str(slot["status"])] += 1
            shards[game["shard"]].append(game)
        if self.segment_mode:
            self.exchange.publish_event("actor_games_closed", actor=self.name,
                budget_id=self.config.get("budget_id"),
                games=[game for group in shards.values() for game in group],
                trajectory_encoding="short-segment-1")
            return
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
        absolute = self.exchange.run_deadline(self.config.get("deadline_at"),
                                              budget_id=self.config.get("budget_id"))
        if absolute is not None:
            deadline = min(deadline, began + max(0, absolute - time.time() - 600))
        self.run_deadline = deadline
        episode_seconds = self.config["episode_seconds"]
        if self.segment_mode:
            self.prefetch.refresh()  # Download the registration package only once.
        else:
            self.prefetch.start()
        status = "completed"
        previous = {number: signal.signal(number, self._signal)
                    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)}
        try:
            admission_margin = 60 if self.segment_mode else episode_seconds + 180
            while (len(self.games) < self.config["max_games"]
                   and time.monotonic() + admission_margin < deadline
                   and not (self.work / "STOP").exists() and self.signalled is None):
                if self.segment_mode and self._stop_before_offer():
                    status = "stopped_before_offer"
                    break
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
                if ledger is None:
                    continue
                step = time.monotonic()
                fast = self.fast_chain is not None
                workers = self.fast_chain["env"]["slots"] if self.fast_chain is not None else 1
                result = ledger.run_managed(
                    workers=workers,
                    max_episodes=self.config["games_per_ledger"],
                    limits=SuiteLimits(150, episode_seconds, 120,
                                       max(1, deadline - time.monotonic() - 120), 1, 30),
                    executor=self.executor,
                    should_stop=(self._fast_chain_stop_before_offer if fast
                                 else self._stop_before_offer if self.segment_mode else None))
                self.timing["ledger_run"] += time.monotonic() - step
                step = time.monotonic()
                self._announce(record, [s for s in ledger.slots() if s["status"] != "not_started"])
                self.timing["announce"] += time.monotonic() - step
                if result.get("status") == "stopped":
                    # Every ledger stop is the owner's to read (unknown cleanup not
                    # accepted, save failed, capacity, wall limit); never play on past it.
                    status = ("stopped_before_offer" if self.segment_stop_reason is not None
                              else f"stopped_ledger:{result.get('stop_reason')}")
                    break
                if self._unknown_limit():
                    status = "stopped_unknown_limit"
                    break
                if fast and self.executor.admission_closed() is not None:
                    status = "stopped_fast_chain:" + str(self.executor.admission_closed())
                    break
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
            self.prefetch.stop()
            if self.executor is not None:
                closed = self.executor.close()
                if self.fast_chain is not None:
                    self.fast_chain_stop = closed
            if self.service is not None:
                self.service.close()
                self.actor_pool.close()
        summary = {"schema": "spireagent/rl-actor-summary-1", "actor": self.name,
                   "run": self.config["run"],
                   "status": "stopped_signal" if self.signalled and status == "completed"
                   else status, "signal": self.signalled,
                   "stop_reason": self.segment_stop_reason,
                   "wall_seconds": time.monotonic() - began, "counts": dict(self.counts),
                   "cpu_seconds": _cpu_seconds() - cpu_began,
                   "timing": dict(self.timing),
                   "model_fetch_seconds": self.prefetch.fetch_seconds,
                   "model_prefetch_events": self.prefetch.events, "games": self.games}
        if self.fast_chain is not None:
            summary["fast_chain"] = getattr(self, "fast_chain_stop", None)
        if self.segment_mode:
            summary["local_mu"] = self.actor_pool.scorer.report()
            summary["segment_timing"] = self.actor_pool.timing_report()
        archive = self.exchange.archive_status()  # intents only: nothing to drain here
        if archive is not None:
            summary["archive"] = archive
        (self.work / "actor-summary.json").write_bytes(json_bytes(summary))
        return summary

    def _unknown_limit(self) -> bool:
        """Only an explicitly configured segment Actor stops for a local count.

        Unknown games still end once; verified prefixes remain usable. Native
        cleanup and preservation failures keep their original owner stops.
        """
        # SegmentWatcher owns the run-wide 5% gate and its complete denominator.
        if self.segment_mode and "unknown_stop" not in self.config:
            return False
        limit = self.config.get("unknown_stop", {"consecutive": 3, "fraction": 0.05,
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
    if 'segments' not in configuration:
        reject('whole_episode_live_training_retired_use_segments')
    if args.actor:
        configuration["actor"] = args.actor
    summary = Actor(configuration, args.work.resolve()).run()
    print(json.dumps({k: summary[k] for k in ("actor", "status", "counts")}))
    archive = summary.get("archive", {})
    if archive.get("full_readback") is False and archive.get("publisher") != "shared":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
