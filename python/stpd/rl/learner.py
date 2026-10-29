"""RL learner: read shard records, train while actors keep playing, publish versions.

The learner never waits for a whole actor batch. It trains whenever enough
usable samples exist, never reuses a sample more than ``max_reuse`` times, drops
trajectories more than ``max_staleness`` versions behind, and publishes a new
model version every ``updates_per_version`` updates. Shard reads/projection and
version publication run on background threads; time spent waiting for data is
measured and reported rather than filled with repeated epochs.
"""
from __future__ import annotations

import collections
import copy
import hashlib
import json
import math
import random
import tempfile
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import torch

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.rl_exchange import Exchange
from spireagent.rl_tensorboard import TensorBoardMetrics
from spireagent.source import source_identity
from stpd.p1.export import export_model, input_spec, load_model, runtime_projection
from stpd.p1.input import project
from stpd.p1.tensor_input import TensorFrame
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.training_io import CheckpointPublisher, FrozenCheckpoint, ProgressCadence

from .loss import LossConfig, RlSample, rl_update
from .performance import PerformanceConfig
from .reward import REWARD_SPEC, episode_reward

CONFIG_SCHEMA = "stpd/rl-learner-config-1"
CHECKPOINT_SCHEMA = "stpd/rl-learner-checkpoint-2"
ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LearnerConfig:
    run: str
    peer: dict[str, Any]
    # Either a local package directory, or the start ``model`` artifact in the
    # peer plus its pinned weights hash (a fresh GPU host fetches and checks it).
    start_package: str | None = None
    start_artifact_id: str | None = None
    start_model_sha256: str | None = None
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
    # Explicit full learner checkpoint artifact id to continue this run from
    # (a new machine or a new 5 h allocation); never picked implicitly.
    resume_checkpoint: str | None = None
    tensorboard_steps: int = 10
    # Zero retains per-update exchange polling; positive values are opt-in cadence.
    exchange_poll_seconds: float = 0.0
    loss: LossConfig = field(default_factory=LossConfig)
    # Omitted means the exact legacy update path, not an implicit new plan.
    performance: PerformanceConfig | None = None

    @classmethod
    def read(cls, path: Path) -> LearnerConfig:
        value = json.loads(path.read_bytes())
        if value.pop("schema", None) != CONFIG_SCHEMA:
            raise BoundaryError("rl_learner", "unsupported_config")
        loss = LossConfig(**value.pop("loss", {}))
        performance = value.pop("performance", None)
        return cls(**value, loss=loss,
                   performance=(PerformanceConfig(**performance)
                                if performance is not None else None))


@dataclass(frozen=True)
class LearnerIO:
    """Application-owned readers/writers the research learner is composed with.

    ``read_shard(store, shard_artifact_id, scratch)`` yields read-back trajectory
    episodes; ``publish_model(store, package_directory, producer)`` returns the
    published ``model`` manifest. ``spireagent.workbench.rl_learner`` wires the
    Managed trajectory store and the detached P1 model import.
    """
    read_shard: Callable[..., Iterable[dict[str, Any]]]
    publish_model: Callable[..., Any]


@dataclass
class Buffered:
    sample: RlSample
    uses: int = 0
    shard: str = ""


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
    def __init__(self, config: LearnerConfig, work: Path, io: LearnerIO) -> None:
        self.config, self.work, self.io = config, work, io
        if type(config.tensorboard_steps) is not int or config.tensorboard_steps < 0:
            raise BoundaryError("rl_learner", "invalid_tensorboard_steps")
        if (type(config.exchange_poll_seconds) not in (int, float)
                or not math.isfinite(config.exchange_poll_seconds)
                or config.exchange_poll_seconds < 0):
            raise BoundaryError("rl_learner", "invalid_exchange_poll_seconds")
        work.mkdir(parents=True, exist_ok=True)
        torch.manual_seed(config.seed)
        torch.set_num_threads(config.threads)
        self.random = random.Random(config.seed)
        self.exchange = Exchange(config.peer, config.run)
        self.metrics = (TensorBoardMetrics(self.exchange, work / "tb")
                        if config.tensorboard_steps else None)
        self.producer = source_identity(ROOT)
        self.device = _device(config.device)
        if config.start_package:
            package = Path(config.start_package)
        elif config.start_artifact_id and config.start_model_sha256:
            package = self.exchange.materialize_model(
                {"model_artifact_id": config.start_artifact_id,
                 "model_sha256": config.start_model_sha256}, work / "start")
        else:
            raise BoundaryError("rl_learner", "start_package_or_pinned_start_artifact_required")
        self.start_package = package
        descriptor = json.loads((package / "model.json").read_bytes())
        self.start_sha256 = descriptor["package_sha256"]
        model, _ = load_model(package / "model.tensor-tree", self.start_sha256)
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
        self.reference_cache: dict[str, torch.Tensor] = {}
        self.frame_cache: dict[object, TensorFrame] = {}
        self.returns: collections.deque[float] = collections.deque(maxlen=config.baseline_window)
        self.seen: set[str] = set()
        self.counts: collections.Counter[str] = collections.Counter()
        self.updates = 0
        self.version_updates = 0
        self.lineage_version = 0
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
        """Human anchor points: a bounded, frozen prefix of the declared TRAIN snapshot.

        The verified (observation, chosen) prefix is kept in an optional private
        node-local cache, bound to the snapshot ID, point count and its own
        SHA-256, so later runs skip the full Store read; projection still runs here.
        """
        spec = self.config.anchor
        if not spec:
            return []
        import gzip

        began = time.monotonic()
        name = hashlib.sha256(json_bytes({"snapshot_id": spec["snapshot_id"],
                                          "points": spec["points"],
                                          "split": "train"})).hexdigest()
        cache = (Path(spec["cache_directory"]) / f"{name}.json.gz"
                 if spec.get("cache_directory") else None)
        source = "store"
        if cache is not None and cache.exists():
            raw = cache.read_bytes()
            if hashlib.sha256(raw).hexdigest() != cache.with_suffix(".sha256").read_text().strip():
                raise BoundaryError("rl_learner", "anchor_cache_integrity_failure")
            rows = json.loads(gzip.decompress(raw))
            source = "node_cache"
        else:
            from spireagent.storage.config import open_peer
            from stpd.p1.dataset import SnapshotReader

            reader = SnapshotReader(open_peer(spec["peer"]), spec["snapshot_id"])
            rows = []
            for reference, observation in reader.iter_points():
                if reference["split"] == "train":
                    rows.append({"chosen": reference["chosen"], "observation": observation})
                if len(rows) >= spec["points"]:
                    break
            if cache is not None:
                cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                raw = gzip.compress(json_bytes(rows), mtime=0)
                cache.with_suffix(".sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
                cache.write_bytes(raw)
        points = []
        for row in rows:
            frame = project(row["observation"], input_projection=self.projection)
            points.append((frame, [c["choice_id"] for c in frame.choices].index(row["chosen"])))
        self.anchor_identity = {"snapshot_id": spec["snapshot_id"], "points": len(points),
                                "split": "train", "source": source,
                                "load_seconds": time.monotonic() - began}
        return points

    def _initial_version(self) -> int:
        latest = self.exchange.latest_model()
        if self.config.resume_checkpoint is not None:
            if latest is None:
                raise BoundaryError("rl_learner", "resume_requires_published_versions")
            return self._resume(self.config.resume_checkpoint, latest["version"])
        if latest is not None:
            raise BoundaryError("rl_learner", "run_already_has_versions_use_resume_checkpoint")
        manifest = self.io.publish_model(self.exchange.store, self.start_package,
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
    def _state(self) -> bytes:
        """Owner thread: everything a successor needs to keep training this run."""
        buffer = [[i.shard, i.sample.key, i.uses, i.sample.advantage, i.sample.model_version]
                  for i in self.buffer]
        state = self.random.getstate()
        learner = {"run": self.config.run, "version": self.lineage_version,
                   "updates": self.updates, "counts": dict(self.counts),
                   "returns": list(self.returns), "seen": sorted(self.seen),
                   "buffer": buffer, "random": [state[0], list(state[1]), state[2]]}
        return encode_checkpoint({
            "schema": CHECKPOINT_SCHEMA,
            "weights": {k: v.detach().cpu().clone() for k, v in self.model.state_dict().items()},
            "optimizer": self.optimizer.state_dict(), "torch_rng": torch.get_rng_state(),
            # One scalar keeps thousands of keys inside the codec's node limit.
            "learner": json.dumps(learner, sort_keys=True)})

    def _resume(self, artifact_id: str, latest: int) -> int:
        began = time.monotonic()
        manifest = self.exchange.store.get_manifest(artifact_id)
        info = manifest.parameters.value()
        if (manifest.kind != "checkpoint" or info.get("schema") != CHECKPOINT_SCHEMA
                or info.get("run") != self.config.run
                or info.get("start_package_sha256") != self.start_sha256
                or manifest.producer != self.producer):
            raise BoundaryError("rl_learner", "resume_checkpoint_binding_mismatch")
        state = decode_checkpoint(b"".join(self.exchange.store.read_payload(
            manifest.payload("checkpoint"))))
        learner = json.loads(state["learner"])
        if (state.get("schema") != CHECKPOINT_SCHEMA or learner["run"] != self.config.run
                or learner["version"] != info["version"] or learner["version"] > latest):
            raise BoundaryError("rl_learner", "resume_checkpoint_state_mismatch")
        self.model.load_state_dict(state["weights"])
        self.optimizer.load_state_dict(state["optimizer"])
        torch.set_rng_state(state["torch_rng"])
        self.random.setstate((learner["random"][0], tuple(learner["random"][1]),
                              learner["random"][2]))
        self.updates = self.version_updates = learner["updates"]
        self.counts.update(learner["counts"])
        self.returns.extend(learner["returns"])
        self.lineage_version = learner["version"]
        # Staleness is measured against what actors play now: the newest version,
        # which may be newer than the checkpoint if its checkpoint never landed.
        self.version = latest
        # The buffer is rebuilt from its own read-back shards, with the saved
        # advantage and reuse count; frames are never stored twice.
        saved = {key: (uses, advantage, version)
                 for _, key, uses, advantage, version in learner["buffer"]}
        shards = sorted({entry[0] for entry in learner["buffer"]})
        for rows in self.reader.map(self._read, shards):
            for row in rows:
                for frame, action, probability, old, key in row.get("samples", ()):
                    if key in saved:
                        uses, advantage, version = saved.pop(key)
                        self.buffer.append(Buffered(RlSample(
                            frame, action, probability, old, advantage, version, key), uses,
                            row["key"]))
        if saved:
            raise BoundaryError("rl_learner", "resume_buffer_samples_missing")
        self.seen = set(learner["seen"])
        self.timer.add("resume", began)
        self._event("resumed", checkpoint=artifact_id, checkpoint_version=learner["version"],
                    latest_version=latest, updates=self.updates, buffer=len(self.buffer))
        return latest

    def _read(self, key: str) -> list[dict[str, Any]]:
        record = self.exchange.shard_record(key)
        rows: list[dict[str, Any]] = []
        if record.get("shard_artifact_id") is None:
            return [{"record": record, "episode": None, "key": key}]
        with tempfile.TemporaryDirectory(prefix="rl-shard-", dir=self.work) as tmp:
            for episode in self.io.read_shard(self.exchange.store, record["shard_artifact_id"],
                                      scratch=Path(tmp)):
                # Staleness trusts the record's version only when it names the
                # model this episode was actually played with.
                if episode["identity"]["model_artifact_id"] != record["model_artifact_id"]:
                    raise BoundaryError("rl_learner", "shard_record_model_mismatch")
                rows.append({"record": record, "episode": episode, "key": key,
                             "samples": self._project(episode)})
        return rows

    def _project(self, episode: dict[str, Any]) -> list[tuple[Any, int, float, float, str]]:
        if episode["identity"]["input_spec"] != self.input_spec:
            raise BoundaryError("rl_learner", "trajectory_input_spec_differs")
        frames = []
        for step in episode["steps"]:
            observation = episode["observations"][step["observation_index"]]["observation"]
            frame = project(observation, input_projection=self.projection)
            action = [c["choice_id"] for c in frame.choices].index(step["action_id"])
            behavior = step["behavior"]
            frames.append((frame, action, behavior["selected_probability"],
                           behavior["model_probabilities"][step["action_index"]],
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
            for frame, action, probability, old, key in row["samples"]:
                self.buffer.append(Buffered(RlSample(frame, action, probability, old, advantage,
                                                     version, key), shard=row["key"]))
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
        live = {item.sample.key for item in kept}
        for key in [k for k in self.reference_cache if k not in live]:
            del self.reference_cache[key]
        for frame_key in [k for k in self.frame_cache
                          if isinstance(k, tuple) and k[0] == "rl" and k[1] not in live]:
            del self.frame_cache[frame_key]
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
                           self.config.loss, self.reference_cache,
                           performance=self.config.performance, frame_cache=self.frame_cache)
        self.updates += 1
        result.update(update=self.updates, version=self.version,
                      buffer_size=len(self.buffer),
                      mean_staleness=sum(self.version - item.sample.model_version
                                         for item in batch) / len(batch),
                      max_staleness=max(self.version - item.sample.model_version for item in batch),
                      sample_versions=dict(collections.Counter(
                          str(item.sample.model_version) for item in batch)))
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
            "version": version, "parent_version": self.lineage_version,
            "updates": self.updates,
            "reward": REWARD_SPEC, "loss": asdict(self.config.loss),
            **({"learner_execution": asdict(self.config.performance)}
               if self.config.performance is not None else {}),
            "learner_config_sha256": hashlib.sha256(json_bytes(
                {**asdict(self.config), "peer": self.config.peer})).hexdigest(),
            "anchor": getattr(self, "anchor_identity", None),
            "producer": self.producer.to_dict()})
        return destination, raw

    def _publish(self, version: int, parent: int, destination: Path,
                 shards: list[str], updates: int, rl_samples: int) -> dict[str, Any]:
        began = time.monotonic()
        manifest = self.io.publish_model(self.exchange.store, destination, self.producer)
        record = self.exchange.publish_model({
            "run": self.config.run, "version": version, "parent_version": parent,
            "model_artifact_id": manifest.artifact_id,
            "model_sha256": manifest.payload("weights").sha256,
            "origin_artifact_id": self.config.start_artifact_id,
            "training": {"updates": updates, "shard_records": shards,
                         "rl_samples": rl_samples},
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
        self._take_checkpoint(wait=False)

    def _take_checkpoint(self, wait: bool) -> None:
        taken = self.checkpoints.take(wait=wait)
        if taken:
            version = taken[0].parameters.value()["version"]
            self.exchange.publish_checkpoint(version, taken[0].artifact_id)
            self._event("checkpoint_published", artifact=taken[0].artifact_id,
                        version=version, **taken[1])
            if self.metrics is not None:
                self.metrics.publish()

    def maybe_version(self, force: bool = False) -> None:
        due = self.updates - self.version_updates
        if due == 0 or not force and due < self.config.updates_per_version:
            return
        from spireagent.json_boundary import FrozenObject

        began = time.monotonic()
        self._collect_version(wait=True)
        version, parent = self.version + 1, self.lineage_version
        destination, _ = self._package(version)
        self.lineage_version, self.version_updates = version, self.updates
        state = self._state() if not self.checkpoints.pending else None
        self.timer.add("version_capture", began)
        self.pending_version = self.publisher.submit(self._publish, version, parent,
            destination, sorted(self.seen), self.updates, self.counts["rl_samples"])
        if state is not None:
            self.checkpoints.submit(FrozenCheckpoint(state, (), FrozenObject.of(
                {"schema": CHECKPOINT_SCHEMA, "run": self.config.run, "version": version,
                 "updates": self.updates, "start_package_sha256": self.start_sha256}), 0.0))

    def _final_checkpoint(self) -> None:
        """A busy background publisher may have skipped the last version's capture."""
        from spireagent.json_boundary import FrozenObject

        latest = self.exchange.latest_checkpoint()
        if self.lineage_version and (latest is None or latest["version"] < self.lineage_version):
            self.checkpoints.submit(FrozenCheckpoint(self._state(), (), FrozenObject.of(
                {"schema": CHECKPOINT_SCHEMA, "run": self.config.run,
                 "version": self.lineage_version, "updates": self.updates,
                 "start_package_sha256": self.start_sha256}), 0.0))
            self._take_checkpoint(wait=True)

    def _event(self, kind: str, **fields: Any) -> None:
        self.last_event_at = time.monotonic()
        self.log.write(json.dumps({"at": time.time(), "kind": kind, **fields},
                                  sort_keys=True) + "\n")
        self.log.flush()
        staleness = [max(0, getattr(self, "version", 0) - item.sample.model_version)
                     for item in self.buffer]
        self.exchange.publish_event(kind, **{
            "version": getattr(self, "version", 0), "updates": self.updates,
            "counts": dict(self.counts), "seen_shards": sorted(self.seen),
            "buffer_size": len(self.buffer),
            "timing": self.timer.report(),
            "mean_staleness": sum(staleness) / len(staleness) if staleness else 0,
            "max_staleness": max(staleness, default=0), **fields})

    def run(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.wall_seconds
        clean = False
        next_exchange_poll = 0.0
        failure: Exception | None = None
        try:
            while time.monotonic() < deadline and not (self.work / "STOP").exists():
                if time.monotonic() >= next_exchange_poll:
                    began = time.monotonic()
                    stop = self.exchange.stop_request()
                    self.timer.add("stop_poll", began)
                    if stop is not None:
                        self._event("stop_requested", reason=stop["reason"])
                        break
                    began = time.monotonic()
                    self.poll()
                    self.timer.add("poll", began)
                    next_exchange_poll = time.monotonic() + self.config.exchange_poll_seconds
                self._collect_version(wait=False)
                began = time.monotonic()
                result = self.train_once()
                if result is None:
                    self.timer.add("wait_data_check", began)
                    began = time.monotonic()
                    time.sleep(self.config.poll_seconds if not self.reads else 0.2)
                    self.timer.add("wait_for_data", began)
                    if time.monotonic() - self.last_event_at >= 30:
                        self._event("waiting")
                    continue
                self.timer.add("train", began)
                if self.metrics is not None and self.updates % self.config.tensorboard_steps == 0:
                    self.metrics.scalars({f"train/{name}": result[name] for name in (
                        "policy_loss", "anchor_loss", "kl", "clipped_fraction", "mean_ratio",
                        "gradient_norm", "buffer_size", "mean_staleness", "max_staleness")},
                        self.updates)
                if self.cadence.offer(self.updates, result):
                    self._event("progress", **result, counts=dict(self.counts))
                    self.cadence.published()
                self.maybe_version()
                if self.version >= self.config.max_versions:  # run-wide, survives resume
                    break
            clean = True
        except Exception as error:
            failure = error
            raise
        finally:
            # A stop or deadline (end of an allocation) keeps every finished update:
            # one last version plus a full checkpoint a successor can resume from.
            try:
                self._take_checkpoint(wait=clean)
                if clean:
                    self.maybe_version(force=True)
                self._collect_version(wait=True)
                self._take_checkpoint(wait=clean)
                if clean:
                    self._final_checkpoint()
                self.checkpoints.__exit__(None)
                if self.metrics is not None:
                    self.metrics.publish()
            except Exception as error:
                failure = failure or error
                raise
            finally:
                self.reader.shutdown(wait=False, cancel_futures=True)
                self.publisher.shutdown(wait=True)
                if failure is not None:
                    self._event("failed", error_type=type(failure).__name__,
                                error_code=getattr(failure, "code", None))
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
