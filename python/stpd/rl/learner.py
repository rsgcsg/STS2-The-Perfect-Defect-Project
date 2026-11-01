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
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, TypeVar, cast

import torch

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.rl_exchange import Exchange
from spireagent.rl_tensorboard import TensorBoardMetrics
from spireagent.source import source_identity
from spireagent.storage.blobs import StoreError
from stpd.p1.export import export_model, input_spec, load_model, runtime_projection
from stpd.p1.input import project
from stpd.p1.tensor_input import ModelFrame, TensorFrame, compile_frame
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.training_io import CheckpointPublisher, FrozenCheckpoint, ProgressCadence

from .checkpoint import METADATA_ENCODING, PPO_SCHEMA, learner_state, metadata_tensor
from .critic import (
    CriticConfig,
    CriticEpisode,
    OnlineCritic,
    enable_value_head,
    held_out,
    optimizer_groups,
    restore_optimizer,
    trace_gae,
)
from .ledger import DecisionLedger
from .loss import LossConfig, RlSample, rl_update
from .performance import PerformanceConfig, prepare_models
from .reward import REWARD_SPEC, episode_reward

CONFIG_SCHEMA = "stpd/rl-learner-config-1"
CHECKPOINT_SCHEMA = "stpd/rl-learner-checkpoint-2"
ROOT = Path(__file__).resolve().parents[2]
T = TypeVar("T")


@dataclass(frozen=True)
class LearnerConfig:
    run: str
    peer: dict[str, Any]
    # Either a local package directory, or the start ``model`` artifact in the
    # peer plus its pinned weights hash (a fresh GPU host fetches and checks it).
    start_package: str | None = None
    start_artifact_id: str | None = None
    start_model_sha256: str | None = None
    reference_package: str | None = None
    reference_artifact_id: str | None = None
    reference_model_sha256: str | None = None
    device: str = "cpu"
    threads: int = 4
    lr: float = 1e-5
    weight_decay: float = 0.0
    batch_size: int = 64
    anchor_batch: int = 32
    updates_per_version: int = 8
    max_reuse: int = 3
    max_staleness: int = 3
    # Opt-in wall-clock freshness/cadence; None preserves version/update rules.
    max_episode_age_seconds: float | None = None
    publish_interval_seconds: float | None = None
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
    # Exact prior producer and exact critic old/new values for an intentional upgrade.
    resume_source: dict[str, str] | None = None
    resume_critic_changes: dict[str, Any] | None = None
    # Explicitly publish restored weights before actors receive another version.
    republish_on_resume: bool = False
    tensorboard_steps: int = 10
    # Zero retains per-update exchange polling; positive values are opt-in cadence.
    exchange_poll_seconds: float = 0.0
    # Opt-in bounded discovery; admission and RNG stay on the owner thread.
    background_io: bool = False
    read_ahead: int = 2
    reader_workers: int = 1
    remote_stop_seconds: float = 5.0
    remote_stop_read_timeout_seconds: float = 30.0
    read_retry_attempts: int = 3
    read_retry_seconds: float = 1.0
    clock_offset_bound_seconds: float | None = None
    decision_ledger: bool = True
    # Off by default. Runtime control may enable detached episode targets at an update boundary.
    prepared_targets: bool = False
    initial_policy_check: bool = False
    loss: LossConfig = field(default_factory=LossConfig)
    # Omitted means the exact legacy update path, not an implicit new plan.
    performance: PerformanceConfig | None = None
    critic: CriticConfig | None = None

    @classmethod
    def read(cls, path: Path) -> LearnerConfig:
        value = json.loads(path.read_bytes())
        if value.pop("schema", None) != CONFIG_SCHEMA:
            raise BoundaryError("rl_learner", "unsupported_config")
        loss = LossConfig(**value.pop("loss", {}))
        performance = value.pop("performance", None)
        critic = value.pop("critic", None)
        return cls(**value, loss=loss,
                   critic=CriticConfig(**critic) if critic is not None else None,
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
    episode_started_at: float | None = None


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
        self.lock = threading.Lock()
        self.instance_id = uuid.uuid4().hex

    def add(self, name: str, began: float) -> None:
        elapsed = time.monotonic() - began
        with self.lock:
            self.seconds[name] += elapsed

    def report(self) -> dict[str, Any]:
        wall = time.monotonic() - self.began
        with self.lock:
            phases = dict(self.seconds)
        return {"wall_seconds": wall, "phases": phases, "instance_id": self.instance_id,
                "fractions": {k: v / wall for k, v in phases.items()} if wall else {}}


class Learner:
    def __init__(self, config: LearnerConfig, work: Path, io: LearnerIO) -> None:
        self.config, self.work, self.io = config, work, io
        if (config.critic is not None) != (config.loss.mode == "ppo"):
            raise BoundaryError("rl_learner", "PPO_requires_explicit_critic_and_loss_mode")
        if config.critic is not None and config.loss.value_coef <= 0:
            raise BoundaryError("rl_learner", "PPO_requires_positive_value_loss")
        if (type(config.prepared_targets) is not bool or
                (config.prepared_targets and (config.critic is None or config.max_reuse > 2
                 or config.max_episode_age_seconds is None))):
            raise BoundaryError("rl_learner", "prepared_targets_require_PPO_two_uses_and_age")
        self.runtime_control: dict[str, Any] = {"sequence": 0, "sha256": None}
        self.checkpoint_schema = PPO_SCHEMA if config.critic is not None else CHECKPOINT_SCHEMA
        for value in (config.max_episode_age_seconds, config.publish_interval_seconds):
            if value is not None and (type(value) not in (int, float)
                                     or not math.isfinite(value) or value <= 0):
                raise BoundaryError("rl_learner", "invalid_time_freshness_or_cadence")
        if type(config.tensorboard_steps) is not int or config.tensorboard_steps < 0:
            raise BoundaryError("rl_learner", "invalid_tensorboard_steps")
        if (type(config.exchange_poll_seconds) not in (int, float)
                or not math.isfinite(config.exchange_poll_seconds)
                or config.exchange_poll_seconds < 0):
            raise BoundaryError("rl_learner", "invalid_exchange_poll_seconds")
        if (type(config.background_io) is not bool or type(config.decision_ledger) is not bool
                or type(config.initial_policy_check) is not bool
                or type(config.read_ahead) is not int
                or not 1 <= config.read_ahead <= 32
                or type(config.reader_workers) is not int
                or not 1 <= config.reader_workers <= 8
                or type(config.read_retry_attempts) is not int
                or not 1 <= config.read_retry_attempts <= 3
                or type(config.remote_stop_seconds) not in (int, float)
                or not math.isfinite(config.remote_stop_seconds)
                or not 0 < config.remote_stop_seconds <= 30
                or type(config.remote_stop_read_timeout_seconds) not in (int, float)
                or not math.isfinite(config.remote_stop_read_timeout_seconds)
                or not 1 <= config.remote_stop_read_timeout_seconds <= 60
                or type(config.read_retry_seconds) not in (int, float)
                or not math.isfinite(config.read_retry_seconds)
                or not 0 <= config.read_retry_seconds <= 10):
            raise BoundaryError("rl_learner", "invalid_bounded_io_configuration")
        bound = config.clock_offset_bound_seconds
        if bound is not None and (type(bound) not in (int, float)
                or not math.isfinite(bound) or bound < 0):
            raise BoundaryError("rl_learner", "invalid_clock_offset_bound")
        work.mkdir(parents=True, exist_ok=True)
        self.timer = Timer()
        torch.manual_seed(config.seed)
        torch.set_num_threads(config.threads)
        self.random = random.Random(config.seed)
        self.exchange = Exchange(config.peer, config.run)
        if (config.resume_checkpoint and config.republish_on_resume
                and self._read_retry("resume_stop", self.exchange.stop_request) is not None):
            raise BoundaryError("rl_learner", "cannot_republish_a_permanently_stopped_run")
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
        reference_package = package
        if config.reference_package:
            reference_package = Path(config.reference_package)
        elif config.reference_artifact_id or config.reference_model_sha256:
            if not (config.reference_artifact_id and config.reference_model_sha256):
                raise BoundaryError("rl_learner", "pinned_reference_artifact_required")
            reference_package = self.exchange.materialize_model(
                {"model_artifact_id": config.reference_artifact_id,
                 "model_sha256": config.reference_model_sha256}, work / "reference")
        reference_descriptor = json.loads((reference_package / "model.json").read_bytes())
        self.reference_sha256 = reference_descriptor["package_sha256"]
        reference, _ = load_model(reference_package / "model.tensor-tree", self.reference_sha256)
        if (input_spec(reference.vocabulary, runtime_projection(reference.vocabulary))
                != self.input_spec):
            raise BoundaryError("rl_learner", "reference_input_spec_differs")
        self.reference = reference.to(self.device).eval()
        for parameter in self.reference.parameters():
            parameter.requires_grad_(False)
        self.model = model.to(self.device).train()
        for parameter in self.model.parameters():
            parameter.requires_grad_(True)
        if config.critic is not None:
            enable_value_head(self.model, config.seed)
        self.optimizer = torch.optim.AdamW(optimizer_groups(self.model, config.critic),
                                           lr=config.lr, weight_decay=config.weight_decay)
        self.critic = (OnlineCritic(self.model, self.reference, self.optimizer, config.critic,
                                   config.seed, config.performance) if config.critic else None)
        self.critic_began = self.last_critic_checkpoint_at = time.monotonic()
        self.last_critic_validation_update = -1
        self.version_critic_revision = 0
        self.last_model_capture_at = time.monotonic()
        self.buffer: list[Buffered] = []
        self.initial_behavior: dict[str, dict[str, Any]] = {}
        self.reference_cache: dict[str, torch.Tensor] = {}
        self.frame_cache: dict[object, TensorFrame] = {}
        self.returns: collections.deque[float] = collections.deque(maxlen=config.baseline_window)
        self.seen: set[str] = set()
        self.seen_episodes: dict[str, str] = {}
        self.counts: collections.Counter[str] = collections.Counter()
        self.update_in_progress = False
        self.updates = 0
        self.version_updates = 0
        self.lineage_version = 0
        self.reader = ThreadPoolExecutor(max_workers=config.reader_workers,
                                         thread_name_prefix="rl-shard-read")
        self.discovery = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rl-discovery")
        self.control = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rl-stop")
        self.telemetry = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rl-telemetry")
        self.discovery_future: Future[list[str]] | None = None
        self.stop_future: Future[dict[str, Any] | None] | None = None
        self.stop_read_started_at = 0.0
        self.next_stop_read_at = 0.0
        self.discovered: collections.deque[str] = collections.deque()
        self.telemetry_pending: collections.deque[Future[Any]] = collections.deque()
        self.ledger = DecisionLedger(work / "decision-ledger.jsonl",
            enabled=config.decision_ledger, clock_offset_bound_seconds=bound)
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
        if (self.critic is not None and self.config.performance is not None
                and self.config.performance.probe_inference_microbatches):
            self._probe_inference_microbatches()
        self.resume_republish_pending = bool(
            config.resume_checkpoint and config.republish_on_resume)
        if self.resume_republish_pending:
            self.maybe_version(force=True)
            self._collect_version(wait=True)
            self._take_checkpoint(wait=True)

    def _probe_inference_microbatches(self) -> None:
        from .performance import probe_inference_microbatches

        assert self.critic is not None and self.config.performance is not None
        began = time.monotonic()
        points = [(key, frame) for episode in self.critic.episodes
                  for key, frame in zip(episode.keys, self.critic._frames(episode), strict=True)]
        if not points:
            raise BoundaryError("rl_performance", "restored_frames_required_for_inference_probe")
        points.sort(key=lambda item: item[1].entity_count if isinstance(item[1], TensorFrame)
                    else len(item[1].entities))
        count = min(128, len(points))
        selected = [points[round(i * (len(points) - 1) / max(1, count - 1))]
                    for i in range(count)]
        baseline = PerformanceConfig(**(getattr(self, "resume_performance", None) or
            {"packed": True, "tensor_cache": True, "precompile_inputs": True}))
        result = probe_inference_microbatches(self.model, self.reference,
            [frame for _, frame in selected], self.config.performance, baseline)
        performance = replace(self.config.performance,
                              inference_microbatch_size=cast(int, result["selected_microbatch_size"]))
        self.config = replace(self.config, performance=performance)
        self.critic.performance = performance
        self.critic.revision += 1
        result.update(at=time.time(), frame_keys=[key for key, _ in selected],
                      policy_updates=self.updates, source=self.producer.to_dict())
        (self.work / "inference-microbatch-probe.json").write_bytes(json_bytes(result))
        self.timer.add("inference_microbatch_probe", began)
        self._event("inference_microbatch_selected", **result)

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
        latest = self._read_retry("latest_model", self.exchange.latest_model)
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
        self._event("version_published", version=0, artifact=manifest.artifact_id,
                    start_package_sha256=self.start_sha256,
                    reference_package_sha256=self.reference_sha256,
                    initialization={"mode": "new_run", "optimizer": "reset", "buffer": "empty",
                                    "baseline": "empty", "counts": "reset",
                                    "seed": self.config.seed})
        return 0

    # Data -------------------------------------------------------------
    def _state(self) -> bytes:
        """Owner thread: everything a successor needs to keep training this run."""
        buffer = [[i.shard, i.sample.key, i.uses, i.sample.advantage, i.sample.model_version]
                  for i in self.buffer]
        state = self.random.getstate()
        learner = {"run": self.config.run, "version": self.lineage_version,
                   "updates": self.updates, "counts": dict(self.counts),
                   "version_critic_revision": self.version_critic_revision,
                   "returns": list(self.returns), "seen": sorted(self.seen),
                   "buffer": buffer, "random": [state[0], list(state[1]), state[2]],
                   "seen_episodes": self.seen_episodes,
                   "decision_ledger": self.ledger.state(),
                   "runtime_control": {**self.runtime_control,
                                       "prepared_targets": self.config.prepared_targets}}
        if self.critic is not None:
            self.critic.elapsed_seconds += time.monotonic() - self.critic_began
            self.critic_began = time.monotonic()
            learner["critic"] = self.critic.state()
        return encode_checkpoint({
            "schema": self.checkpoint_schema,
            **({"learner_encoding": METADATA_ENCODING} if self.critic else {}),
            "weights": {k: v.detach().cpu().clone() for k, v in self.model.state_dict().items()},
            "optimizer": self.optimizer.state_dict(), "torch_rng": torch.get_rng_state(),
            "cuda_rng": torch.cuda.get_rng_state_all() if self.device.type == "cuda" else [],
            # One scalar keeps thousands of keys inside the codec's node limit.
            "learner": (metadata_tensor(learner) if self.critic
                        else json.dumps(learner, sort_keys=True))})

    def _resume(self, artifact_id: str, latest: int) -> int:
        began = time.monotonic()
        manifest = self.exchange.store.get_manifest(artifact_id)
        info = manifest.parameters.value()
        self.resume_performance = info.get("config", {}).get("performance")
        migration = (manifest.producer != self.producer and self.config.resume_source is not None
                     and manifest.producer.to_dict() == self.config.resume_source)
        if (manifest.kind != "checkpoint" or info.get("schema") != self.checkpoint_schema
                or info.get("run") != self.config.run
                or info.get("start_package_sha256") != self.start_sha256
                or info.get("reference_package_sha256", self.start_sha256) != self.reference_sha256
                or (manifest.producer != self.producer and not migration)):
            raise BoundaryError("rl_learner", "resume_checkpoint_binding_mismatch")
        state = decode_checkpoint(b"".join(self.exchange.store.read_payload(
            manifest.payload("checkpoint"))))
        learner = learner_state(state)
        if (state.get("schema") != self.checkpoint_schema or learner["run"] != self.config.run
                or learner["version"] != info["version"] or learner["version"] > latest):
            raise BoundaryError("rl_learner", "resume_checkpoint_state_mismatch")
        control = learner.get("runtime_control")
        if control is not None:
            if (not isinstance(control, dict) or type(control.get("sequence")) is not int
                    or control["sequence"] < 0 or type(control.get("prepared_targets")) is not bool
                    or (control["sequence"] > 0 and
                        (not isinstance(control.get("sha256"), str) or len(control["sha256"]) != 64))):
                raise BoundaryError("rl_learner", "invalid_checkpoint_runtime_control")
            self.runtime_control = {k: control[k] for k in ("sequence", "sha256")}
            self.config = replace(self.config, prepared_targets=control["prepared_targets"])
            self._validate_prepared_mode(control["prepared_targets"])
        self.model.load_state_dict(state["weights"])
        restore_optimizer(self.optimizer, self.model, state["optimizer"], migrate=migration)
        critic_saved = learner.get("critic")
        if (self.critic is None) != (critic_saved is None):
            raise BoundaryError("rl_critic", "resume_method_mismatch")
        if self.critic is not None:
            if not isinstance(critic_saved, dict):
                raise BoundaryError("rl_critic", "checkpoint_metadata_object_required")
            self.critic.restore(critic_saved,
                self.config.resume_critic_changes if migration else None)
            self.critic_began = time.monotonic()
        self.version_critic_revision = learner.get("version_critic_revision", 0)
        torch.set_rng_state(state["torch_rng"])
        if self.device.type == "cuda" and state.get("cuda_rng"):
            torch.cuda.set_rng_state_all(state["cuda_rng"])
        self.seen_episodes = learner.get("seen_episodes", {})
        self.ledger.restore(learner.get("decision_ledger", {}))
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
        saved_order = [entry[1] for entry in learner["buffer"]]
        saved = {key: (uses, advantage, version)
                 for _, key, uses, advantage, version in learner["buffer"]}
        restored: dict[str, Buffered] = {}
        critic_episodes = ({e["run_id"]: e for e in critic_saved["episodes"]}
                           if critic_saved else {})
        critic_order = list(critic_episodes)
        rebuilt_critic: dict[str, CriticEpisode] = {}
        shards = sorted({entry[0] for entry in learner["buffer"]}
                        | {e["shard"] for e in critic_episodes.values()})
        for rows in self.reader.map(
                lambda key: self._read_retry("resume_shard",
                    lambda: self._read_once(key, restoring=True)), shards):
            for row in rows:
                kinds = self._action_kinds(row["episode"]) if row["episode"] else {}
                if self.critic is not None and row["episode"] is not None:
                    run_id = row["episode"]["run_id"]
                    if run_id in critic_episodes:
                        identity = critic_episodes.pop(run_id)
                        rebuilt_critic[run_id] = self._critic_episode(row, identity)
                for frame, action, probability, old, key in row.get("samples", ()):
                    if key in saved:
                        uses, advantage, version = saved.pop(key)
                        restored[key] = Buffered(RlSample(
                            frame, action, probability, old, advantage, version, key,
                            action_kind=kinds.get(key, "unknown")), uses,
                            row["key"], self._episode_started_at(row["episode"], row["record"])
                            if self.config.max_episode_age_seconds is not None else None)
        if saved or critic_episodes:
            raise BoundaryError("rl_learner", "resume_buffer_samples_missing")
        self.buffer = [restored[key] for key in saved_order]
        if self.critic is not None:
            self.critic.episodes = [rebuilt_critic[key] for key in critic_order]
            self.critic.freeze_validation()
        self.seen = set(learner["seen"])
        self.timer.add("resume", began)
        self._event("resumed", checkpoint=artifact_id, checkpoint_version=learner["version"],
                    latest_version=latest, updates=self.updates, buffer=len(self.buffer),
                    migrated_from=manifest.producer.to_dict() if migration else None,
                    critic_changes=self.critic.resumed_config_changes if self.critic else {})
        if migration and self.critic and self.critic.fixed_validation_initialized:
            began = time.monotonic()
            validation = self.critic.validate()
            self.timer.add("critic_validation", began)
            self.last_critic_validation_update = self.critic.updates
            self._event("critic_validation", **validation, version=learner["version"],
                        checkpoint_artifact_id=artifact_id, latest_published_version=latest,
                        phase="fixed_cohort_before_first_upgraded_update",
                        policy_updates=self.updates)
        return latest

    def _read_retry(self, operation: str, call: Callable[[], T]) -> T:
        for attempt in range(1, self.config.read_retry_attempts + 1):
            began = time.monotonic()
            try:
                return call()
            except StoreError as error:
                diagnostic = getattr(error, "diagnostic", {})
                retry = (bool(diagnostic.get("retryable"))
                         and attempt < self.config.read_retry_attempts)
                # Local diagnostic only; an unavailable exchange must not hide its own failure.
                self._local_io_event(operation, error, attempt, retry)
                if not retry:
                    raise
                time.sleep(self.config.read_retry_seconds * attempt)
            finally:
                self.timer.add("read_" + operation, began)
        raise AssertionError("unreachable")

    def _local_io_event(self, operation: str, error: StoreError, attempt: int, retry: bool) -> None:
        value = {"at": time.time(), "kind": "read_failed", "operation": operation,
            "error_type": type(error).__name__, "error_code": error.code,
            "attempt": attempt, "retry": retry, **getattr(error, "diagnostic", {})}
        # Independent append-only diagnostic file: no owner-thread log/RNG/buffer mutation.
        with (self.work / "io-errors.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")

    def _read(self, key: str) -> list[dict[str, Any]]:
        return self._read_retry("read_shard", lambda: self._read_once(key))

    def _read_once(self, key: str, *, restoring: bool = False) -> list[dict[str, Any]]:
        began = time.monotonic()
        record = self.exchange.shard_record(key)
        rows: list[dict[str, Any]] = []
        if record.get("shard_artifact_id") is None:
            return [{"record": record, "episode": None, "key": key,
                     "reader_seconds": time.monotonic() - began}]
        with tempfile.TemporaryDirectory(prefix="rl-shard-", dir=self.work) as tmp:
            for episode in self.io.read_shard(self.exchange.store, record["shard_artifact_id"],
                                      scratch=Path(tmp)):
                # Staleness trusts the record's version only when it names the
                # model this episode was actually played with.
                if episode["identity"]["model_artifact_id"] != record["model_artifact_id"]:
                    raise BoundaryError("rl_learner", "shard_record_model_mismatch")
                # Reader may reject known ineligible work before CPU tensor preparation.
                # Owner rechecks at admission; version advances can still retire prepared work.
                eligible = (restoring or (not self._episode_stale(episode, record)
                    and not {step["behavior"]["mode"] for step in episode["steps"]}
                        - {"temperature_uniform"}
                    and episode_reward(episode)[0] is not None))
                projection_began = time.monotonic()
                timing: dict[str, float] = {}
                samples = self._project(episode, timing) if eligible else []
                rows.append({"record": record, "episode": episode, "key": key,
                    "samples": samples,
                    "projection_seconds": time.monotonic() - projection_began,
                    "input_prepare_seconds": timing.get("input_prepare", 0.0)})
        if rows:
            rows[-1]["reader_seconds"] = time.monotonic() - began
        return rows

    def _project(self, episode: dict[str, Any], timing: dict[str, float] | None = None
                 ) -> list[tuple[Any, int, float, float, str]]:
        if episode["identity"]["input_spec"] != self.input_spec:
            raise BoundaryError("rl_learner", "trajectory_input_spec_differs")
        frames = []
        for step in episode["steps"]:
            observation = episode["observations"][step["observation_index"]]["observation"]
            frame = project(observation, input_projection=self.projection)
            action = [c["choice_id"] for c in frame.choices].index(step["action_id"])
            prepared: ModelFrame = frame
            if self.config.performance is not None and self.config.performance.precompile_inputs:
                # Existing reader thread only: immutable CPU input metadata.
                # Its tokenizer cache belongs to the frozen reference, never
                # the model concurrently copied by the owner for publication.
                prepare_began = time.monotonic()
                with torch.device("cpu"):
                    prepared = compile_frame(frame, self.reference)
                if timing is not None:
                    timing["input_prepare"] = (timing.get("input_prepare", 0.0)
                                                + time.monotonic() - prepare_began)
            behavior = step["behavior"]
            frames.append((prepared, action, behavior["selected_probability"],
                           behavior["model_probabilities"][step["action_index"]],
                           f"{episode['run_id']}:{step['index']}"))
        return frames

    def poll(self) -> None:
        if self.config.background_io:
            if self.discovery_future is not None and self.discovery_future.done():
                self.discovered.extend(self.discovery_future.result())
                self.discovery_future = None
            if self.discovery_future is None and not self.discovered:
                self.discovery_future = self.discovery.submit(
                    self._read_retry, "list_shards", self.exchange.shard_keys)
            while self.discovered and len(self.reads) < self.config.read_ahead:
                key = self.discovered.popleft()
                if key not in self.seen and key not in self.reads:
                    self.reads[key] = self.reader.submit(self._read, key)
        else:
            for key in self._read_retry("list_shards", self.exchange.shard_keys):
                if key not in self.seen and key not in self.reads:
                    self.reads[key] = self.reader.submit(self._read, key)
        for key, future in list(self.reads.items()):
            if not future.done():
                # New bounded path consumes FIFO, never completion-order admission.
                if self.config.background_io:
                    break
                continue
            rows = future.result()
            self._admit(rows)
            del self.reads[key]
            self.seen.add(key)


    @staticmethod
    def _episode_started_at(episode: dict[str, Any], record: dict[str, Any]) -> float:
        games = [g for g in record.get("games", ()) if g.get("run_id") == episode["run_id"]]
        value = games[0].get("started_at") if len(games) == 1 else None
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value)):
            raise BoundaryError("rl_learner", "time_freshness_requires_original_game_start")
        return float(value)

    def _episode_stale(self, episode: dict[str, Any], record: dict[str, Any]) -> bool:
        if self.config.max_episode_age_seconds is None:
            return bool(getattr(self, "version", 0) - record["model_version"]
                        > self.config.max_staleness)
        if episode_reward(episode)[0] is None:
            return False  # preserve unknown/censored disposition before time handling
        if self.critic is not None and not self.critic.ready:
            return False  # frozen-policy warmup data trains only the value head
        return (time.time() - self._episode_started_at(episode, record)
                > self.config.max_episode_age_seconds)

    @staticmethod
    def _action_kinds(episode: dict[str, Any]) -> dict[str, str]:
        return {f"{episode['run_id']}:{step['index']}":
            episode["observations"][step["observation_index"]]["observation"]["catalog"][
                step["action_index"]]["kind"] for step in episode["steps"]}

    @staticmethod
    def _critic_episode(row: dict[str, Any], identity: dict[str, Any]) -> CriticEpisode:
        samples = row["samples"]
        probabilities = []
        for sample, step in zip(samples, row["episode"]["steps"], strict=True):
            observation = row["episode"]["observations"][step["observation_index"]]["observation"]
            catalog = observation["catalog"]
            indices = {c["choice_id"]: i for i, c in enumerate(catalog)}
            choices = [c["choice_id"] if isinstance(c, dict) else c for c in sample[0].choices]
            probabilities.append([step["behavior"]["model_probabilities"][indices[c]]
                                  for c in choices])
        return CriticEpisode(**identity, frames=[r[0] for r in samples],
            actions=[r[1] for r in samples], behavior=[r[2] for r in samples],
            old_probabilities=probabilities, keys=[r[4] for r in samples])

    def _admit(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            if self.critic is not None:
                self.critic.revision += 1  # admission/rejection/seen state also needs a final CP
            for name in ("reader_seconds", "projection_seconds", "input_prepare_seconds"):
                self.timer.seconds["background_" + name] += row.get(name, 0.0)
            record, episode = row["record"], row["episode"]
            if episode is None:
                self.counts["record_" + record.get("disposition", "without_shard")] += 1
                continue
            run_id = episode["run_id"]
            content = episode.get("original_content_id", "")
            if run_id in self.seen_episodes:
                if self.seen_episodes[run_id] != content:
                    raise BoundaryError("rl_learner", "duplicate_episode_content_mismatch")
                self.counts["duplicate_episodes"] += 1
                continue
            self.seen_episodes[run_id] = content
            version = record["model_version"]
            if self._episode_stale(episode, record):
                self.ledger.episode("rejected", episode, record, reason="stale")
                self.counts["stale_episodes"] += 1
                continue
            modes = {step["behavior"]["mode"] for step in episode["steps"]}
            if modes - {"temperature_uniform"}:
                self.ledger.episode("rejected", episode, record, reason="non_sampled")
                self.counts["non_sampled_episodes"] += 1
                continue
            reward, disposition = episode_reward(episode)
            self.counts["episodes_" + disposition] += 1
            if reward is None:
                self.ledger.episode("rejected", episode, record, reason=disposition)
                continue
            baseline = sum(self.returns) / len(self.returns) if self.returns else reward
            spread = (math.sqrt(sum((r - baseline) ** 2 for r in self.returns) / len(self.returns))
                      if len(self.returns) > 1 else 1.0)
            self.returns.append(reward)
            advantage = (reward - baseline) / max(spread, 0.05)
            kinds = self._action_kinds(episode)
            if self.critic is not None:
                from stpd.evaluation.suites import require_non_evaluation_seed

                seed = require_non_evaluation_seed(episode["identity"]["seed"])
                for step in episode["steps"]:
                    behavior = step["behavior"]
                    if (behavior.get("temperature") != 1 or behavior.get("uniform_mix") != 0
                            or not math.isclose(behavior["selected_probability"],
                                behavior["model_probabilities"][step["action_index"]],
                                rel_tol=1e-10, abs_tol=1e-12)):
                        raise BoundaryError("rl_learner", "PPO_requires_direct_policy_sampling")
                evicted = self.critic.add(self._critic_episode(row, {
                    "run_id": run_id, "shard": row["key"], "seed": seed,
                    "reward": reward, "version": version,
                    "started_at": self._episode_started_at(episode, record)}))
                kept = []
                for item in self.buffer:
                    if item.sample.key.rsplit(":", 1)[0] in evicted:
                        self.ledger.retire(item.sample.key, item.uses,
                                           "critic_capacity", self.version)
                        self.counts["samples_retired_critic_capacity"] += 1
                    else:
                        kept.append(item)
                self.buffer = kept
                current_episode = self.critic.episodes[-1]
                began = time.monotonic()
                values, ratios, acting_kl = self.critic.evaluate(current_episode)
                self.counts["arrival_policy_drift_microseconds"] += int(
                    (time.monotonic() - began) * 1000000)
                self.ledger.write("arrival_policy_drift", run_id=run_id,
                    ids=current_episode.keys, acting_kl=acting_kl, ratios=ratios,
                    episode_age_seconds=time.time() - current_episode.started_at,
                    model_lag=self.version - version, warmup=not self.critic.ready,
                    clipped_fraction=sum(abs(r - 1) > self.config.loss.clip for r in ratios)
                                     / len(ratios) if ratios else None)
                if held_out(seed):
                    self.counts["critic_validation_episodes"] += 1
                    self.ledger.episode("rejected", episode, record,
                                        reason="critic_validation_only", reward=reward)
                    continue
                if not self.critic.ready:
                    self.counts["critic_warmup_episodes"] += 1
                    self.ledger.episode("rejected", episode, record,
                                        reason="critic_warmup_only", reward=reward)
                    continue
                if self.config.prepared_targets:
                    self._prepare_targets(current_episode, (values, ratios, acting_kl))
                advantage = 0.0  # selected preparation method supplies detached GAE
            self.ledger.episode("admitted", episode, record, reward=reward, advantage=advantage)
            if self.config.initial_policy_check and self.updates == 0:
                for step in episode["steps"]:
                    observation = episode["observations"][step["observation_index"]]["observation"]
                    self.initial_behavior[f"{run_id}:{step['index']}"] = {
                        "choices": [choice["choice_id"] for choice in observation["catalog"]],
                        "behavior": step["behavior"]}
            for frame, action, probability, old, key in row["samples"]:
                self.buffer.append(Buffered(RlSample(frame, action, probability, old, advantage,
                                                     version, key, action_kind=kinds[key]),
                                             shard=row["key"], episode_started_at=(
                    self._episode_started_at(episode, record)
                    if self.config.max_episode_age_seconds else None)))
            self.counts["rl_samples"] += len(row["samples"])
            if record.get("finished_at"):
                self.latencies.append(time.time() - record["finished_at"])

    def usable(self) -> list[Buffered]:
        floor = self.version - self.config.max_staleness
        kept = []
        for item in self.buffer:
            if item.uses >= self.config.max_reuse:
                self.counts["samples_retired_reuse"] += 1
                self.ledger.retire(item.sample.key, item.uses, "reuse", self.version)
            elif ((self.config.max_episode_age_seconds is None
                   and item.sample.model_version < floor)
                  or (self.config.max_episode_age_seconds is not None
                      and item.episode_started_at is not None
                      and time.time() - item.episode_started_at
                          > self.config.max_episode_age_seconds)):
                self.counts["samples_dropped_stale"] += 1
                self.ledger.retire(item.sample.key, item.uses, "stale", self.version)
            else:
                kept.append(item)
        if self.critic is not None and len(kept) != len(self.buffer):
            self.critic.revision += 1
        self.buffer = kept
        live = {item.sample.key for item in kept}
        for key in [k for k in self.reference_cache if k not in live]:
            del self.reference_cache[key]
        for frame_key in [k for k in self.frame_cache
                          if isinstance(k, tuple) and k[0] == "rl" and k[1] not in live]:
            del self.frame_cache[frame_key]
        return kept

    # Training ---------------------------------------------------------
    def _check_initial_policy(self, batch: list[Buffered]) -> None:
        """Check the actor's eval probabilities without changing the train-mode objective."""
        from torch.nn import functional as F

        began = time.monotonic()
        was_training = self.model.training
        maximum_logit_error = maximum_probability_error = maximum_log_ratio = 0.0
        kl_values = []
        distributions_match = True
        try:
            self.model.eval()
            with torch.no_grad():
                for item in batch:
                    sample = item.sample
                    original = self.initial_behavior[sample.key]
                    choices = [choice["choice_id"] if isinstance(choice, dict) else choice
                               for choice in sample.frame.choices]
                    indices = [original["choices"].index(choice) for choice in choices]
                    recorded = original["behavior"]
                    scores = self.model.forward_batch([sample.frame])[0].float()
                    reference_scores = self.reference.forward_batch([sample.frame])[0].float()
                    probabilities = scores.softmax(-1)
                    old = torch.tensor([recorded["model_probabilities"][i] for i in indices],
                                       device=scores.device)
                    maximum_probability_error = max(maximum_probability_error,
                        float((probabilities - old).abs().max()))
                    distributions_match = distributions_match and torch.allclose(
                        probabilities, old, rtol=1e-5, atol=1e-5)
                    if recorded.get("model_logits") is not None:
                        logits = torch.tensor([recorded["model_logits"][i] for i in indices],
                                              device=scores.device)
                        maximum_logit_error = max(maximum_logit_error,
                            float((scores - logits).abs().max()))
                        distributions_match = distributions_match and torch.allclose(
                            scores, logits, rtol=1e-5, atol=1e-5)
                    chosen_log = float(F.log_softmax(scores, -1)[sample.action])
                    maximum_log_ratio = max(maximum_log_ratio,
                        abs(chosen_log - math.log(sample.old_probability)))
                    kl = float((probabilities * (F.log_softmax(scores, -1)
                                   - F.log_softmax(reference_scores, -1))).sum())
                    kl_values.append(kl)
        finally:
            self.model.train(was_training)
        same_reference = self.reference_sha256 == self.start_sha256
        passed = distributions_match and maximum_log_ratio <= 1e-4 and (not same_reference
                    or max(map(abs, kl_values), default=0.0) <= 1e-5)
        report = {"scope": "same acting weights, eval mode, complete original catalogs",
            "samples": len(batch), "max_logit_abs_error": maximum_logit_error,
            "max_probability_abs_error": maximum_probability_error,
            "max_abs_log_ratio": maximum_log_ratio, "reference_equals_start": same_reference,
            "reference_kl": kl_values, "seconds": time.monotonic() - began, "passed": passed,
            "training_randomization": "unchanged; train-mode ratio/KL need not be 1/0"}
        self.timer.seconds["initial_eval_probability_gate"] += report["seconds"]
        (self.work / "initial-policy-check.json").write_bytes(json_bytes(report))
        if not passed:
            raise BoundaryError("rl_learner", "initial_eval_probability_mismatch")

    def _critic_step(self) -> bool:
        critic = self.critic
        self.warmup_trained = False
        if critic is None:
            return True
        began = time.monotonic()
        result = critic.warmup_update()
        self.timer.add("critic_warmup_train", began)
        self.warmup_trained = result is not None
        if result is not None:
            self.ledger.write("critic_trained", **result)
        elapsed = critic.elapsed_seconds + time.monotonic() - self.critic_began
        if (critic.updates >= critic.config.min_updates
                and critic.updates != self.last_critic_validation_update
                and critic.updates % critic.config.validation_every == 0):
            began = time.monotonic()
            validation = critic.validate()
            self.timer.add("critic_validation", began)
            self.last_critic_validation_update = critic.updates
            elapsed = critic.elapsed_seconds + time.monotonic() - self.critic_began
            self._event("critic_validation", **validation, ready=critic.ready,
                        elapsed_seconds=elapsed)
            if (not critic.ready and validation["passed"]
                    and critic.config.warmup_min_seconds <= elapsed
                    < critic.config.warmup_max_seconds):
                critic.ready = True
                critic.revision += 1
                critic.freeze_policy(False)
                self._event("critic_ready", **validation, elapsed_seconds=elapsed)
        if not critic.ready and elapsed >= critic.config.warmup_max_seconds:
            raise BoundaryError("rl_critic", "warmup_not_qualified")
        return critic.ready

    def _ppo_batch(self, usable: list[Buffered]) -> list[Buffered]:
        # Sequence minibatches bound complete-trace current-value evaluation.
        # Pick episodes proportionally to remaining rows; favor fresh uses.
        groups: dict[str, list[Buffered]] = collections.defaultdict(list)
        for item in usable:
            groups[item.sample.key.rsplit(":", 1)[0]].append(item)
        for rows in groups.values():
            self.random.shuffle(rows)
            rows.sort(key=lambda item: item.uses)
        batch: list[Buffered] = []
        while groups and len(batch) < self.config.batch_size:
            least = min(rows[0].uses for rows in groups.values())
            names = [name for name, rows in groups.items() if rows[0].uses == least]
            name = self.random.choices(names, weights=[len(groups[n]) for n in names])[0]
            rows = groups[name]
            take = min(len(rows), self.config.batch_size - len(batch),
                       max(1, self.config.batch_size // 2) if len(groups) > 1
                       else self.config.batch_size)
            batch.extend(rows[:take])
            if take == len(rows):
                del groups[name]
            else:
                groups[name] = rows[take:]
        return batch

    def _validate_prepared_mode(self, enabled: bool) -> None:
        if enabled and (self.critic is None or self.config.max_reuse > 2
                        or self.config.max_episode_age_seconds is None):
            raise BoundaryError("rl_learner", "prepared_targets_require_PPO_two_uses_and_age")

    def _apply_runtime_control(self) -> None:
        """One atomic local command, consumed between successful optimizer updates.

        The checkpoint owns the accepted sequence and effective mode on resume.
        This changes target preparation, never episode age, data use or reward.
        """
        path = self.work / "runtime-control.json"
        if not path.exists():
            return
        raw = path.read_bytes()
        control = json.loads(raw)
        if (not isinstance(control, dict)
                or set(control) != {"schema", "sequence", "prepared_targets"}
                or control["schema"] != "stpd/rl-runtime-control-1"
                or type(control["sequence"]) is not int or control["sequence"] < 1
                or type(control["prepared_targets"]) is not bool):
            raise BoundaryError("rl_learner", "invalid_runtime_control")
        sequence = self.runtime_control["sequence"]
        digest = hashlib.sha256(raw).hexdigest()
        if control["sequence"] <= sequence:
            if control["sequence"] == sequence and digest != self.runtime_control["sha256"]:
                raise BoundaryError("rl_learner", "runtime_control_sequence_content_changed")
            return
        if control["sequence"] != sequence + 1:
            raise BoundaryError("rl_learner", "runtime_control_sequence_gap")
        enabled = control["prepared_targets"]
        self._validate_prepared_mode(enabled)
        previous = self.config.prepared_targets
        self.config = replace(self.config, prepared_targets=enabled)
        self.runtime_control = {"sequence": control["sequence"], "sha256": digest}
        if self.critic is not None:
            self.critic.revision += 1
        self._event("runtime_control_applied", sequence=control["sequence"],
                    command_sha256=digest, prepared_targets=enabled, previous=previous,
                    policy_update=self.updates, method="admission-v1" if enabled else "per-update")

    def _prepare_targets(self, episode: CriticEpisode,
                         evaluated: tuple[list[float], list[float], list[float]] | None = None
                         ) -> dict[str, Any]:
        """One raw whole-episode V-trace/GAE estimate, at most two successful uses.

        This opt-in async admission method is distinct from per-update estimation.
        Preparation/restart never extends the original game's freshness deadline.
        """
        assert self.critic is not None
        config = self.critic.config
        binding = {"method": "admission-v1", "gamma": config.gamma,
            "lambda": config.gae_lambda, "reward": episode.reward, "reward_spec": REWARD_SPEC,
            "control_sequence": self.runtime_control["sequence"],
            "keys_sha256": hashlib.sha256(json_bytes(episode.keys)).hexdigest()}
        saved = episode.prepared
        if saved is not None and saved["binding"] == binding:
            for name in ("values", "ratios", "advantages", "targets"):
                array = saved.get(name)
                if (not isinstance(array, list) or len(array) != len(episode.keys)
                        or not all(type(v) in (int, float) and math.isfinite(v) for v in array)
                        or (name == "ratios" and any(v <= 0 for v in array))):
                    raise BoundaryError("rl_critic", "invalid_prepared_targets")
            return saved
        if saved is not None:
            self.ledger.write("critic_preparation_invalidated", run_id=episode.run_id,
                              previous=saved["binding"], current=binding)
        values, ratios, acting_kl = evaluated or self.critic.evaluate(episode)
        advantages, targets = trace_gae(episode.reward, values, ratios,
                                        config.gamma, config.gae_lambda)
        metadata = {"binding": binding, "policy_update": self.updates,
                    "critic_update": self.critic.updates, "prepared_at": time.time(),
                    "source": self.producer.to_dict()}
        episode.prepared = {**metadata, "values": values, "ratios": ratios,
                            "advantages": advantages, "targets": targets}
        self.ledger.write("critic_prediction", run_id=episode.run_id, ids=episode.keys,
            values=values, targets=targets, advantages=advantages, ratios=ratios,
            acting_kl=acting_kl, critic_updates=self.critic.updates, policy_update=self.updates,
            ready=True, preparation=metadata, acting_kl_scope="preparation_only")
        return episode.prepared

    def _fresh_ppo_samples(self, batch: list[Buffered]) -> list[RlSample]:
        assert self.critic is not None
        began = time.monotonic()
        names = {item.sample.key.rsplit(":", 1)[0] for item in batch}
        estimates: dict[str, tuple[float, float, list[float]]] = {}
        for episode in self.critic.episodes:
            if episode.run_id not in names:
                continue
            if self.config.prepared_targets:
                prepared = self._prepare_targets(episode)
                advantages, targets = prepared["advantages"], prepared["targets"]
            else:
                values, ratios, acting_kl = self.critic.evaluate(episode)
                advantages, targets = trace_gae(episode.reward, values, ratios,
                    self.critic.config.gamma, self.critic.config.gae_lambda)
                self.ledger.write("critic_prediction", run_id=episode.run_id, ids=episode.keys,
                    values=values, targets=targets, advantages=advantages, ratios=ratios,
                    acting_kl=acting_kl,
                    execution_microbatch_sizes=self.critic.last_evaluation_microbatches,
                    critic_updates=self.critic.updates, policy_update=self.updates, ready=True)
            estimates.update(zip(episode.keys,
                zip(advantages, targets, episode.old_probabilities, strict=True), strict=True))
        self.last_raw_advantages = [estimates[item.sample.key][0] for item in batch]
        samples = [replace(item.sample, advantage=estimates[item.sample.key][0],
                           value_target=estimates[item.sample.key][1], acting_kl=None,
                           behavior_probabilities=estimates[item.sample.key][2]) for item in batch]
        mean = sum(s.advantage for s in samples) / len(samples)
        spread = math.sqrt(sum((s.advantage - mean) ** 2 for s in samples) / len(samples))
        self.timer.add("prepared_targets" if self.config.prepared_targets
                       else "current_value_and_advantage", began)
        return [replace(s, advantage=(s.advantage - mean) / max(spread, 1e-8)) for s in samples]

    def train_once(self) -> dict[str, Any] | None:
        self._apply_runtime_control()
        ready = self._critic_step()
        self.policy_train_began = time.monotonic()
        if not ready:
            return None
        sample_began = time.monotonic()
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
        if self.critic is not None:
            batch = self._ppo_batch(usable)
        else:
            self.random.shuffle(usable)
            usable.sort(key=lambda item: item.uses)
            batch = usable[:self.config.batch_size]
        sampling_seconds = time.monotonic() - sample_began
        anchor_began = time.monotonic()
        anchor = (self.random.sample(self.anchor, min(self.config.anchor_batch, len(self.anchor)))
                  if self.anchor else [])
        anchor_seconds = time.monotonic() - anchor_began
        if self.config.initial_policy_check and self.updates == 0:
            if self.config.performance is not None:
                with prepare_models(self.model, self.reference, self.config.performance):
                    self._check_initial_policy(batch)
            else:
                self._check_initial_policy(batch)
        samples = (self._fresh_ppo_samples(batch) if self.critic is not None
                   else [item.sample for item in batch])
        if self.critic is not None:
            scale = self.critic.target_scale([cast(float, s.value_target) for s in samples])
            samples = [replace(s, value_scale=scale) for s in samples]
        self.policy_train_began = time.monotonic()
        self.update_in_progress = True
        result = rl_update(self.model, self.reference, self.optimizer,
                           samples, anchor, self.config.lr,
                           replace(self.config.loss, gradient_diagnostics=True)
                           if (self.critic and
                               self.updates % self.critic.config.validation_every == 0)
                           else self.config.loss, self.reference_cache,
                           performance=self.config.performance, frame_cache=self.frame_cache)
        self.update_in_progress = False
        self.initial_behavior.clear()
        self.last_advantages = [sample.advantage for sample in samples]
        self.updates += 1
        if self.critic is not None:
            self.critic.updates += 1
            self.critic.revision += 1
            result.update(target_preparation=("admission-v1" if self.config.prepared_targets
                                              else "per-update"),
                          raw_advantage_min=min(self.last_raw_advantages),
                          value_target_std=scale,
                          value_head_lr=self.critic.config.head_lr or self.config.lr,
                          raw_advantage_max=max(self.last_raw_advantages),
                          raw_advantage_mean=sum(self.last_raw_advantages) / len(samples))
            self.ledger.write("ppo_diagnostics", update=self.updates, **result)
        for item in batch:
            item.uses += 1
        self.ledger.trained([item.sample.key for item in batch], self.updates, self.version)
        result.update(sampling_seconds=sampling_seconds, anchor_selection_seconds=anchor_seconds)
        result.update(update=self.updates, version=self.version,
                      buffer_size=len(self.buffer),
                      reader_pending=len(self.reads), reader_discovered=len(self.discovered),
                      advantage_min=min(sample.advantage for sample in samples),
                      advantage_max=max(sample.advantage for sample in samples),
                      advantage_mean=sum(sample.advantage for sample in samples) / len(samples),
                      actual_sampling_entropy=(sum(self.ledger.live[item.sample.key][
                          "behavior_entropy"] for item in batch) / len(batch)
                          if self.config.decision_ledger else None),
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
            "critic": ({"config": asdict(self.critic.config), "ready": self.critic.ready,
                        "updates": self.critic.updates} if self.critic else None),
            "start_package_sha256": self.start_sha256,
            "reference_package_sha256": self.reference_sha256,
            "initial_state_mode": ("checkpoint" if self.config.resume_checkpoint
                                   else "new_run_reset"),
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

    def _publish_ledger(self) -> None:
        for identity, raw in self.ledger.freeze_segments():
            key = f"{self.exchange.prefix}decisions/{identity['sha256']}.jsonl"
            def publish(key: str = key, raw: bytes = raw) -> None:
                self.exchange.blobs.put_if_absent(key, raw)
            if self.config.background_io:
                while self.telemetry_pending and self.telemetry_pending[0].done():
                    self.telemetry_pending.popleft().result()
                if len(self.telemetry_pending) >= 8:
                    self.telemetry_pending.popleft().result()
                self.telemetry_pending.append(self.telemetry.submit(publish))
            else:
                publish()

    def _take_checkpoint(self, wait: bool) -> None:
        taken = self.checkpoints.take(wait=wait)
        if taken:
            self._publish_ledger()
            version = taken[0].parameters.value()["version"]
            self.exchange.publish_checkpoint(version, taken[0].artifact_id)
            self._event("checkpoint_published", artifact=taken[0].artifact_id,
                        version=version, **taken[1])
            if self.metrics is not None:
                self.metrics.publish()

    def maybe_version(self, force: bool = False) -> None:
        due = self.updates - self.version_updates
        critic_changed = (force and self.critic is not None
                          and self.critic.revision > self.version_critic_revision)
        cadence_due = (time.monotonic() - self.last_model_capture_at
                       >= self.config.publish_interval_seconds
                       if self.config.publish_interval_seconds is not None
                       else due >= self.config.updates_per_version)
        if (not getattr(self, "resume_republish_pending", False) and not critic_changed
                and (due == 0 or not force and not cadence_due)):
            return
        from spireagent.json_boundary import FrozenObject

        began = time.monotonic()
        self._collect_version(wait=True)
        version, parent = self.version + 1, self.lineage_version
        destination, _ = self._package(version)
        self.lineage_version, self.version_updates = version, self.updates
        self.last_model_capture_at = time.monotonic()
        self.resume_republish_pending = False
        if self.critic is not None:
            self.version_critic_revision = self.critic.revision
            self.last_critic_checkpoint_at = time.monotonic()
        state = self._state() if not self.checkpoints.pending else None
        self.timer.add("version_capture", began)
        self.pending_version = self.publisher.submit(self._publish, version, parent,
            destination, sorted(self.seen), self.updates, self.counts["rl_samples"])
        if state is not None:
            self.checkpoints.submit(FrozenCheckpoint(state, (), FrozenObject.of(
                {"schema": self.checkpoint_schema, "run": self.config.run, "version": version,
                 "updates": self.updates, "start_package_sha256": self.start_sha256,
                 "reference_package_sha256": self.reference_sha256,
                 "config": asdict(self.config), "producer": self.producer.to_dict()}), 0.0))

    def _final_checkpoint(self) -> None:
        """A busy background publisher may have skipped the last version's capture."""
        from spireagent.json_boundary import FrozenObject

        latest = self._read_retry("latest_checkpoint", self.exchange.latest_checkpoint)
        if self.lineage_version and (latest is None or latest["version"] < self.lineage_version):
            self.checkpoints.submit(FrozenCheckpoint(self._state(), (), FrozenObject.of(
                {"schema": self.checkpoint_schema, "run": self.config.run,
                 "version": self.lineage_version, "updates": self.updates,
                 "start_package_sha256": self.start_sha256,
                 "reference_package_sha256": self.reference_sha256,
                 "config": asdict(self.config), "producer": self.producer.to_dict()}), 0.0))
            self._take_checkpoint(wait=True)

    def _event(self, kind: str, **fields: Any) -> None:
        self.last_event_at = time.monotonic()
        event_began = time.monotonic()
        self.log.write(json.dumps({"at": time.time(), "kind": kind, **fields},
                                  sort_keys=True) + "\n")
        self.log.flush()
        self.timer.add("event_local", event_began)
        staleness = [max(0, getattr(self, "version", 0) - item.sample.model_version)
                     for item in self.buffer]
        payload = {
            "captured_at": time.time(),
            "version": getattr(self, "version", 0), "updates": self.updates,
            "counts": dict(self.counts), "seen_shards": sorted(self.seen),
            "buffer_size": len(self.buffer),
            "timing": self.timer.report(),
            "mean_staleness": sum(staleness) / len(staleness) if staleness else 0,
            "max_staleness": max(staleness, default=0),
            "decision_accounting": self.ledger.summary(),
            "decision_ledger_segments": [dict(row) for row in self.ledger.segments], **fields}
        if self.config.background_io and not getattr(self, "telemetry_closed", False):
            while self.telemetry_pending and self.telemetry_pending[0].done():
                self.telemetry_pending.popleft().result()
            if len(self.telemetry_pending) >= 8:
                began = time.monotonic()
                self.telemetry_pending.popleft().result()
                self.timer.add("telemetry_backpressure", began)
            self.telemetry_pending.append(self.telemetry.submit(self._publish_event, kind, payload))
        else:
            self._publish_event(kind, payload)

    def _publish_event(self, kind: str, payload: dict[str, Any]) -> None:
        began = time.monotonic()
        try:
            self.exchange.publish_event(kind, **payload)
        finally:
            self.timer.add("event_publish", began)

    def _stop_request(self) -> dict[str, Any] | None:
        if not self.config.background_io:
            return self._read_retry("stop", self.exchange.stop_request)
        result = None
        if self.stop_future is not None and self.stop_future.done():
            result = self.stop_future.result()
            self.stop_future = None
        if (self.stop_future is not None and not self.stop_future.done()
                and time.monotonic() - self.stop_read_started_at
                    > self.config.remote_stop_read_timeout_seconds):
            raise BoundaryError("rl_learner", "remote_stop_read_timeout")
        if result is not None:
            return result
        if (self.stop_future is None
                and time.monotonic() >= getattr(self, "next_stop_read_at", 0.0)):
            self.stop_read_started_at = time.monotonic()
            self.next_stop_read_at = self.stop_read_started_at + self.config.remote_stop_seconds
            self.stop_future = self.control.submit(
                self._read_retry, "stop", self.exchange.stop_request)
        return result

    def run(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.wall_seconds
        clean = False
        next_exchange_poll = 0.0
        failure: Exception | None = None
        try:
            while time.monotonic() < deadline and not (self.work / "STOP").exists():
                if time.monotonic() >= next_exchange_poll:
                    began = time.monotonic()
                    stop = self._stop_request()
                    self.timer.add("stop_poll", began)
                    if stop is not None:
                        self._event("stop_requested", reason=stop["reason"])
                        break
                    began = time.monotonic()
                    self.poll()
                    self.timer.add("poll", began)
                    next_exchange_poll = time.monotonic() + (
                        min(self.config.exchange_poll_seconds, self.config.remote_stop_seconds)
                        if self.config.background_io else self.config.exchange_poll_seconds)
                self._collect_version(wait=False)
                # A slower full checkpoint can finish after its model. Acknowledge
                # it while waiting for data too, without waiting for another version.
                self._take_checkpoint(wait=False)
                began = time.monotonic()
                result = self.train_once()
                if self.critic is not None:
                    began = self.policy_train_began
                    if (not self.critic.ready and time.monotonic() - self.last_critic_checkpoint_at
                            >= self.critic.config.checkpoint_seconds):
                        self.maybe_version(force=True)
                if result is None:
                    self.timer.add("wait_data_check", began)
                    began = time.monotonic()
                    time.sleep(self.config.poll_seconds if not self.reads else 0.2)
                    self.timer.add("warmup_cadence_wait" if self.warmup_trained
                                   else "wait_for_data", began)
                    if time.monotonic() - self.last_event_at >= 30:
                        self._event("waiting")
                    continue
                self.timer.add("train", began)
                if self.metrics is not None and self.updates % self.config.tensorboard_steps == 0:
                    metrics_began = time.monotonic()
                    self.metrics.scalars({f"train/{name}": result[name] for name in (
                        "policy_loss", "anchor_loss", "kl", "clipped_fraction", "mean_ratio",
                        "gradient_norm", "buffer_size", "mean_staleness", "max_staleness",
                        "policy_entropy", "policy_normalized_entropy", "actual_sampling_entropy",
                        "advantage_min", "advantage_max", "advantage_mean", "reader_pending")},
                        self.updates)
                    self.metrics.histograms({
                        "train/advantage": self.last_advantages,
                        "train/raw_return": list(self.returns),
                        "buffer/uses": [item.uses for item in self.buffer],
                        "buffer/version_lag": [self.version - item.sample.model_version
                                               for item in self.buffer]}, self.updates)
                    self.metrics.scalars({f"decisions/{name}": count
                        for name, count in self.ledger.counts.items()
                        if name.startswith("retired_")}, self.updates)
                    self.timer.add("tensorboard_local", metrics_began)
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
                if not self.update_in_progress:
                    self.maybe_version(force=True)
                self._collect_version(wait=True)
                self._take_checkpoint(wait=clean)
                if not self.update_in_progress:
                    self._final_checkpoint()
                self.checkpoints.__exit__(None)
                self._publish_ledger()
                if self.metrics is not None:
                    self.metrics.publish()
            except Exception as error:
                failure = failure or error
                raise
            finally:
                self.discovery.shutdown(wait=True, cancel_futures=True)
                self.control.shutdown(wait=False, cancel_futures=True)
                self.reader.shutdown(wait=True, cancel_futures=True)
                self.publisher.shutdown(wait=True)
                if failure is not None:
                    self._event("failed", error_type=type(failure).__name__,
                                error_code=getattr(failure, "code", None),
                                diagnostic=getattr(failure, "diagnostic", {}))
                self.telemetry.shutdown(wait=True)
                self.telemetry_closed = True
                for pending in self.telemetry_pending:
                    pending.result()
                self.ledger.close()
        summary = {"schema": "stpd/rl-learner-summary-1", "run": self.config.run,
                   "updates": self.updates, "versions": [v["version"] for v in self.versions],
                   "version_artifacts": {str(v["version"]): v["model_artifact_id"]
                                         for v in self.versions},
                   "counts": dict(self.counts), "timing": self.timer.report(),
                   "trajectory_to_training_seconds": _quantiles(self.latencies),
                   "decision_accounting": self.ledger.summary(),
                   "device": str(self.device), "anchor": getattr(self, "anchor_identity", None),
                   "critic": self.critic.state() if self.critic else None}
        (self.work / "learner-summary.json").write_bytes(json_bytes(summary))
        self._event("finished", **{k: summary[k] for k in ("updates", "counts")})
        return summary


def _quantiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {"count": len(ordered), "median": ordered[len(ordered) // 2],
            "max": ordered[-1], "min": ordered[0]}
