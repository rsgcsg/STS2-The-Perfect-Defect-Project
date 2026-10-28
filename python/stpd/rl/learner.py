"""RL learner: read shard records, train while actors keep playing, publish versions.

The learner never waits for a whole actor batch. It trains whenever enough
usable samples exist, never reuses a sample more than ``max_reuse`` times, drops
trajectories more than ``max_staleness`` versions behind, and publishes a new
model version every ``updates_per_version`` updates. Shard reads/projection and
version publication run on background threads; time spent waiting for data is
measured and reported rather than filled with repeated epochs.
"""
from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
import math
import os
import random
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import torch

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.source import source_identity
from spireagent.workbench.global_evidence_store import publish_detached_p1_model
from spireagent.workbench.managed_trajectory_store import read_shard
from spireagent.workbench.rl_exchange import Exchange
from stpd.p1.export import export_model, input_spec, load_model, runtime_projection
from stpd.p1.input import project
from stpd.workers.checkpoint_codec import encode_checkpoint
from stpd.workers.training_io import CheckpointPublisher, FrozenCheckpoint, ProgressCadence

from .loss import LossConfig, RlSample, rl_update
from .reward import REWARD_SPEC, episode_reward

CONFIG_SCHEMA = "stpd/rl-learner-config-1"
ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LearnerConfig:
    run: str
    peer: dict[str, Any]
    start_package: str
    start_artifact_id: str | None = None
    device: str = "cpu"
    threads: int = 4
    lr: float = 1e-5
    weight_decay: float = 0.0
    batch_size: int = 64
    anchor_batch: int = 32
    updates_per_version: int = 8
    max_reuse: int = 3
    max_staleness: int = 3
    baseline_window: int = 64
    min_episodes_before_training: int = 4
    poll_seconds: float = 5.0
    max_versions: int = 1000
    wall_seconds: float = 3600
    seed: int = 1
    anchor: dict[str, Any] | None = None
    loss: LossConfig = field(default_factory=LossConfig)

    @classmethod
    def read(cls, path: Path) -> LearnerConfig:
        value = json.loads(path.read_bytes())
        if value.pop("schema", None) != CONFIG_SCHEMA:
            raise BoundaryError("rl_learner", "unsupported_config")
        loss = LossConfig(**value.pop("loss", {}))
        return cls(**value, loss=loss)


@dataclass
class Buffered:
    sample: RlSample
    uses: int = 0


def _device(name: str) -> torch.device:
    if name == "mps" and not torch.backends.mps.is_available():
        raise BoundaryError("rl_learner", "mps_unavailable")
    if name == "cuda" and not torch.cuda.is_available():
        raise BoundaryError("rl_learner", "cuda_unavailable")
    return torch.device(name)


class Timer:
    """Mutually exclusive owner-thread phases; background work is measured separately."""

    def __init__(self) -> None:
        self.seconds: dict[str, float] = collections.defaultdict(float)
        self.began = time.monotonic()

    def add(self, name: str, began: float) -> None:
        self.seconds[name] += time.monotonic() - began

    def report(self) -> dict[str, Any]:
        wall = time.monotonic() - self.began
        return {"wall_seconds": wall, "phases": dict(self.seconds),
                "fractions": {k: v / wall for k, v in self.seconds.items()} if wall else {}}


class Learner:
    def __init__(self, config: LearnerConfig, work: Path) -> None:
        self.config, self.work = config, work
        work.mkdir(parents=True, exist_ok=True)
        torch.manual_seed(config.seed)
        torch.set_num_threads(config.threads)
        self.random = random.Random(config.seed)
        self.exchange = Exchange(config.peer, config.run)
        self.producer = source_identity(ROOT)
        self.device = _device(config.device)
        package = Path(config.start_package)
        descriptor = json.loads((package / "model.json").read_bytes())
        model, _ = load_model(package / "model.tensor-tree", descriptor["package_sha256"])
        self.projection = runtime_projection(model.vocabulary)
        self.input_spec = input_spec(model.vocabulary, self.projection)
        self.reference = model.to(self.device).eval()
        for parameter in self.reference.parameters():
            parameter.requires_grad_(False)
        self.model = copy.deepcopy(self.reference).train()
        for parameter in self.model.parameters():
            parameter.requires_grad_(True)
        self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=config.lr,
                                           weight_decay=config.weight_decay)
        self.buffer: list[Buffered] = []
        self.returns: collections.deque[float] = collections.deque(maxlen=config.baseline_window)
        self.seen: set[str] = set()
        self.counts: collections.Counter[str] = collections.Counter()
        self.updates = 0
        self.timer = Timer()
        self.reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rl-shard-read")
        self.publisher = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rl-version")
        self.reads: dict[str, Future[list[dict[str, Any]]]] = {}
        self.pending_version: Future[dict[str, Any]] | None = None
        self.versions: list[dict[str, Any]] = []
        self.latencies: list[float] = []
        # The Human anchor streams from the Store while version 0 is published and
        # actors play their first games; only the first update ever waits for it.
        self.anchor: list[tuple[Any, int]] = []
        self.anchor_future: Future[list[tuple[Any, int]]] | None = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rl-anchor").submit(self._anchor)
        self.log = (work / "learner-events.jsonl").open("a", encoding="utf-8")
        self.cadence = ProgressCadence(0, steps=10, seconds=30)
        self.checkpoints = CheckpointPublisher(self.exchange.store, self.producer,
                                               background=True)
        self.version = self._initial_version()

    # Setup ------------------------------------------------------------
    def _anchor(self) -> list[tuple[Any, int]]:
        """Human anchor points: a bounded, frozen prefix of the declared TRAIN snapshot."""
        spec = self.config.anchor
        if not spec:
            return []
        from spireagent.storage.config import open_peer
        from stpd.p1.dataset import SnapshotReader

        began = time.monotonic()
        reader = SnapshotReader(open_peer(spec["peer"]), spec["snapshot_id"])
        points = []
        for reference, observation in reader.iter_points():
            if reference["split"] != "train":
                continue
            frame = project(observation, input_projection=self.projection)
            points.append((frame, [c["choice_id"] for c in frame.choices].index(
                reference["chosen"])))
            if len(points) >= spec["points"]:
                break
        self.anchor_identity = {"snapshot_id": spec["snapshot_id"], "points": len(points),
                                "split": "train", "load_seconds": time.monotonic() - began}
        return points

    def _initial_version(self) -> int:
        latest = self.exchange.latest_model()
        if latest is not None:
            raise BoundaryError("rl_learner", "run_already_has_versions_resume_not_implemented")
        manifest = publish_detached_p1_model(self.exchange.store, Path(self.config.start_package),
                                             self.producer)
        record = self.exchange.publish_model({
            "run": self.config.run, "version": 0, "parent_version": None,
            "model_artifact_id": manifest.artifact_id,
            "model_sha256": manifest.payload("weights").sha256,
            "origin_artifact_id": self.config.start_artifact_id,
            "training": {"updates": 0, "shard_records": [], "rl_samples": 0},
            "published_at": time.time()})
        self.versions.append(record)
        self._event("version_published", version=0, artifact=manifest.artifact_id)
        return 0

    # Data -------------------------------------------------------------
    def _read(self, key: str) -> list[dict[str, Any]]:
        record = self.exchange.shard_record(key)
        rows: list[dict[str, Any]] = []
        if record.get("shard_artifact_id") is None:
            return [{"record": record, "episode": None}]
        with tempfile.TemporaryDirectory(prefix="rl-shard-", dir=self.work) as tmp:
            for episode in read_shard(self.exchange.store, record["shard_artifact_id"],
                                      scratch=Path(tmp)):
                rows.append({"record": record, "episode": episode,
                             "samples": self._project(episode)})
        return rows

    def _project(self, episode: dict[str, Any]) -> list[tuple[Any, int, float, str]]:
        if episode["identity"]["input_spec"] != self.input_spec:
            raise BoundaryError("rl_learner", "trajectory_input_spec_differs")
        frames = []
        for step in episode["steps"]:
            observation = episode["observations"][step["observation_index"]]["observation"]
            frame = project(observation, input_projection=self.projection)
            action = [c["choice_id"] for c in frame.choices].index(step["action_id"])
            frames.append((frame, action, step["behavior"]["selected_probability"],
                           f"{episode['run_id']}:{step['index']}"))
        return frames

    def poll(self) -> None:
        for key in self.exchange.shard_keys():
            if key not in self.seen and key not in self.reads:
                self.reads[key] = self.reader.submit(self._read, key)
        for key, future in list(self.reads.items()):
            if future.done():
                del self.reads[key]
                self.seen.add(key)
                self._admit(future.result())

    def _admit(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            record, episode = row["record"], row["episode"]
            if episode is None:
                self.counts["record_" + record.get("disposition", "without_shard")] += 1
                continue
            version = record["model_version"]
            if self.version - version > self.config.max_staleness:
                self.counts["stale_episodes"] += 1
                continue
            modes = {step["behavior"]["mode"] for step in episode["steps"]}
            if modes - {"temperature_uniform"}:
                self.counts["non_sampled_episodes"] += 1
                continue
            reward, disposition = episode_reward(episode)
            self.counts["episodes_" + disposition] += 1
            if reward is None:
                continue
            baseline = sum(self.returns) / len(self.returns) if self.returns else reward
            spread = (math.sqrt(sum((r - baseline) ** 2 for r in self.returns) / len(self.returns))
                      if len(self.returns) > 1 else 1.0)
            self.returns.append(reward)
            advantage = (reward - baseline) / max(spread, 0.05)
            for frame, action, probability, key in row["samples"]:
                self.buffer.append(Buffered(RlSample(frame, action, probability, advantage,
                                                     version, key)))
            self.counts["rl_samples"] += len(row["samples"])
            if record.get("finished_at"):
                self.latencies.append(time.time() - record["finished_at"])

    def usable(self) -> list[Buffered]:
        floor = self.version - self.config.max_staleness
        kept = []
        for item in self.buffer:
            if item.uses >= self.config.max_reuse:
                self.counts["samples_retired_reuse"] += 1
            elif item.sample.model_version < floor:
                self.counts["samples_dropped_stale"] += 1
            else:
                kept.append(item)
        self.buffer = kept
        return kept

    # Training ---------------------------------------------------------
    def train_once(self) -> dict[str, Any] | None:
        usable = self.usable()
        if (len(usable) < self.config.batch_size
                or self.counts["episodes_win"] + self.counts["episodes_loss"]
                < self.config.min_episodes_before_training):
            return None
        if self.anchor_future is not None:
            began = time.monotonic()
            self.anchor = self.anchor_future.result()
            self.anchor_future = None
            self.timer.add("wait_anchor", began)
        # Least-used first, then random: each sample sees <= max_reuse updates.
        self.random.shuffle(usable)
        usable.sort(key=lambda item: item.uses)
        batch = usable[:self.config.batch_size]
        for item in batch:
            item.uses += 1
        anchor = (self.random.sample(self.anchor, min(self.config.anchor_batch, len(self.anchor)))
                  if self.anchor else [])
        result = rl_update(self.model, self.reference, self.optimizer,
                           [item.sample for item in batch], anchor, self.config.lr,
                           self.config.loss)
        self.updates += 1
        result.update(update=self.updates, version=self.version,
                      sample_versions=dict(collections.Counter(
                          item.sample.model_version for item in batch)))
        return result

    def _package(self, version: int) -> tuple[Path, bytes]:
        """Owner thread: freeze weights into a package directory; bytes never change after."""
        destination = self.work / "versions" / f"{version:06d}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        state = {k: v.detach().cpu().clone() for k, v in self.model.state_dict().items()}
        raw = encode_checkpoint({"weights": state})
        checkpoint = destination.with_suffix(".checkpoint.tensor-tree")
        checkpoint.write_bytes(raw)
        cpu = copy.deepcopy(self.model).cpu().eval()
        export_model(cpu, checkpoint, destination, {
            "schema": "stpd/rl-training-identity-1", "run": self.config.run,
            "version": version, "parent_version": self.version, "updates": self.updates,
            "reward": REWARD_SPEC, "loss": asdict(self.config.loss),
            "learner_config_sha256": hashlib.sha256(json_bytes(
                {**asdict(self.config), "peer": self.config.peer})).hexdigest(),
            "anchor": getattr(self, "anchor_identity", None),
            "producer": self.producer.to_dict()})
        return destination, raw

    def _publish(self, version: int, destination: Path, shards: list[str]) -> dict[str, Any]:
        began = time.monotonic()
        manifest = publish_detached_p1_model(self.exchange.store, destination, self.producer)
        record = self.exchange.publish_model({
            "run": self.config.run, "version": version, "parent_version": version - 1,
            "model_artifact_id": manifest.artifact_id,
            "model_sha256": manifest.payload("weights").sha256,
            "origin_artifact_id": self.config.start_artifact_id,
            "training": {"updates": self.updates, "shard_records": shards,
                         "rl_samples": self.counts["rl_samples"]},
            "published_at": time.time()})
        record["publish_seconds"] = time.monotonic() - began
        return record

    def _collect_version(self, wait: bool) -> None:
        if self.pending_version is None or not wait and not self.pending_version.done():
            return
        began = time.monotonic()
        record = self.pending_version.result()
        self.timer.add("version_wait", began)
        self.pending_version = None
        self.versions.append(record)
        self.version = record["version"]
        self._event("version_published", version=record["version"],
                    artifact=record["model_artifact_id"],
                    publish_seconds=record["publish_seconds"])
        taken = self.checkpoints.take(wait=False)
        if taken:
            self._event("checkpoint_published", artifact=taken[0].artifact_id, **taken[1])

    def maybe_version(self) -> None:
        if self.updates == 0 or self.updates % self.config.updates_per_version:
            return
        began = time.monotonic()
        self._collect_version(wait=True)
        version = self.version + 1
        destination, raw = self._package(version)
        self.timer.add("version_capture", began)
        self.pending_version = self.publisher.submit(self._publish, version, destination,
                                                     sorted(self.seen))
        if not self.checkpoints.pending:
            from spireagent.json_boundary import FrozenObject

            self.checkpoints.submit(FrozenCheckpoint(raw, (), FrozenObject.of(
                {"schema": "stpd/rl-learner-checkpoint-1", "run": self.config.run,
                 "version": version, "updates": self.updates}), 0.0))

    def _event(self, kind: str, **fields: Any) -> None:
        self.log.write(json.dumps({"at": time.time(), "kind": kind, **fields},
                                  sort_keys=True) + "\n")
        self.log.flush()

    def run(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.wall_seconds
        try:
            while time.monotonic() < deadline and not (self.work / "STOP").exists():
                began = time.monotonic()
                self.poll()
                self.timer.add("poll", began)
                self._collect_version(wait=False)
                began = time.monotonic()
                result = self.train_once()
                if result is None:
                    self.timer.add("wait_data_check", began)
                    began = time.monotonic()
                    time.sleep(self.config.poll_seconds if not self.reads else 0.2)
                    self.timer.add("wait_for_data", began)
                    continue
                self.timer.add("train", began)
                if self.cadence.offer(self.updates, result):
                    self._event("progress", **result, counts=dict(self.counts))
                    self.cadence.published()
                self.maybe_version()
                if len(self.versions) > self.config.max_versions:
                    break
        finally:
            self._collect_version(wait=True)
            taken = self.checkpoints.take(wait=True)
            if taken:
                self._event("checkpoint_published", artifact=taken[0].artifact_id, **taken[1])
            self.checkpoints.__exit__(None)
            self.reader.shutdown(wait=False, cancel_futures=True)
            self.publisher.shutdown(wait=True)
        summary = {"schema": "stpd/rl-learner-summary-1", "run": self.config.run,
                   "updates": self.updates, "versions": [v["version"] for v in self.versions],
                   "version_artifacts": {str(v["version"]): v["model_artifact_id"]
                                         for v in self.versions},
                   "counts": dict(self.counts), "timing": self.timer.report(),
                   "trajectory_to_training_seconds": _quantiles(self.latencies),
                   "device": str(self.device), "anchor": getattr(self, "anchor_identity", None)}
        (self.work / "learner-summary.json").write_bytes(json_bytes(summary))
        self._event("finished", **{k: summary[k] for k in ("updates", "counts")})
        return summary


def _quantiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {"count": len(ordered), "median": ordered[len(ordered) // 2],
            "max": ordered[-1], "min": ordered[0]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    threading.current_thread().name = "rl-learner"
    summary = Learner(LearnerConfig.read(args.config), args.work.resolve()).run()
    print(json.dumps({k: summary[k] for k in ("updates", "versions", "counts")}))


if __name__ == "__main__":
    main()
