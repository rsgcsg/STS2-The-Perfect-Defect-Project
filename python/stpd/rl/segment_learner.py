"""RL4.1 learner: complete CPU preparation, batched SGD, background mu and CP.

One learning-state owner selects rows, charges successful uses and steps theta.
Independent CPU processes own all parameter-free preparation. The control server
holds identities only; actor-local mu inference is owned by the actor. CP-only
restore discards hot input state, never replays accepted actions or input logs.
"""

from __future__ import annotations

import copy
import hashlib
import io as byte_io
import json
import math
import multiprocessing
import os
import random
import resource
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

import torch

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.rl_contracts import HotPayloadRef, RunSequence
from spireagent.rl_exchange import Exchange, control_input
from spireagent.rl_tensorboard import TensorBoardMetrics
from spireagent.source import source_identity
from stpd.p1.data import json_bytes as p1_json_bytes
from stpd.p1.export import (
    adapter_identity,
    export_model,
    input_spec,
    load_model,
    runtime_projection,
)
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.local_inference import CentralInferencePool, create_segment_inference_pool
from stpd.p1.model import MStd
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.training_io import CheckpointPublisher, FrozenCheckpoint

from .batch_preparation import (
    BatchPreparer,
    pack_transport,
    stage_batch,
    unpack_transport,
    verify_auxiliary,
)
from .checkpoint_state import SCHEMA as CHECKPOINT_SCHEMA
from .checkpoint_state import (
    LearningCounters,
    ModelConstruction,
    RngState,
    SelfContainedCheckpoint,
    construct_model,
    cpu_copy,
    decode_state,
    encode_state,
    snapshot_weights,
    text_digest,
    validate_optimizer_order,
)
from .learner import ROOT, LearnerConfig, _device
from .loss import prepared_update
from .performance import PerformanceConfig
from .prepared_batch import AuxiliarySelection, BatchMember, PreparedBatch, PrepareRequest
from .reward import REWARD_SPEC
from .segments import OPERATIONAL_FIELDS, AuxiliaryPool, SegmentQueue, minibatch_members


@dataclass(frozen=True)
class SegmentLearnerIO:
    # read_segment remains an explicit historical-reader dependency for application
    # composition. The live edge calls Exchange.read_hot_segment, never this reader.
    read_segment: Callable[..., dict[str, Any]]
    publish_model: Callable[..., Any]
    read_hot_segment: Callable[..., dict[str, Any]] | None = None


def _publisher_initialize() -> None:
    torch.set_num_threads(1)
    if torch.get_num_interop_threads() != 1:
        torch.set_num_interop_threads(1)


def _process_memory() -> dict[str, int]:
    # getrusage is process-local; children and their high-water marks are separate.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"pid": os.getpid(),
            "peak_rss_bytes": int(peak * (1 if sys.platform == "darwin" else 1024))}


def _publish_snapshot(
    snapshot: dict[str, Any],
    *,
    peer: dict[str, Any],
    run: str,
    generation: str,
    version: int,
    updates: int,
    producer: Any,
    publish_model: Callable[..., Any],
    work: str,
) -> dict[str, Any]:
    """Independent CPU process: construction, hashing, export, Store publication."""
    began = time.perf_counter()
    snapshot = unpack_transport(snapshot)
    exchange = Exchange(peer, run)
    model = construct_model(snapshot["construction"])
    model.load_state_dict(snapshot["weights"], strict=True)
    model.eval().requires_grad_(False)
    model.deduplicate_text = snapshot["deduplicate_text"]
    destination = Path(work) / "versions" / generation / f"{version:06d}"
    checkpoint = destination.with_suffix(".tensor-tree")
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(encode_checkpoint({"weights": model.state_dict()}))
    export_model(
        model,
        checkpoint,
        destination,
        {
            "run": run,
            "training_generation": generation,
            "version": version,
            "updates": updates,
            "reward": REWARD_SPEC,
            "producer": producer.to_dict(),
        },
    )
    manifest = publish_model(exchange.store, destination, producer)
    weights = manifest.payload("weights").sha256
    raw = encode_checkpoint({"heads": model.rl4_heads.state_dict()})
    payload = exchange.store.put_payload(
        "checkpoint", byte_io.BytesIO(raw), "application/vnd.stpd.tensor-tree"
    )
    heads = Manifest(
        "checkpoint",
        producer,
        parents=(Parent("policy", manifest.artifact_id),),
        payloads=(payload,),
        parameters=FrozenObject.of({"schema": "stpd/rl-behavior-heads-1"}),
    )
    exchange.store.publish(heads)
    numerical = {
        "precision": "fp32",
        "tf32": False,
        "projection": runtime_projection(model.vocabulary),
        "execution": asdict(model.execution),
        "adapter": adapter_identity(),
        "deduplicate_text": model.deduplicate_text,
    }
    identity = {
        "run": run,
        "training_generation": generation,
        "version": version,
        "updates": updates,
        "model_artifact_id": manifest.artifact_id,
        "model_sha256": weights,
        "heads_artifact_id": heads.artifact_id,
        "heads_sha256": payload.sha256,
        **numerical,
        "text_digest": text_digest(snapshot["weights"]),
        "vocabulary_sha256": snapshot["construction"].compiler_identity["vocabulary_sha256"],
        "input_spec": snapshot["construction"].compiler_identity["input_spec"],
        "compiler_identity": snapshot["construction"].compiler_identity,
    }
    identity["snapshot_sha256"] = hashlib.sha256(
        p1_json_bytes({"weights": weights, "heads": payload.sha256, "numerical": numerical})
    ).hexdigest()
    record = exchange.publish_model(
        {
            **identity,
            "parent_version": version - 1 if version else None,
            "published_at": time.time(),
            "trajectory_encoding": "hot-segment",
            "training": {"updates": updates},
        }
    )
    # These are this publication's disposable encoder inputs/copies. Canonical
    # model+heads bytes and identities are already verified in the original Store.
    released = checkpoint.stat().st_size + (destination / "model.tensor-tree").stat().st_size
    checkpoint.unlink()
    (destination / "model.tensor-tree").unlink()
    return {"identity": identity, "record": record, "updates": updates,
            "temporary_export_released_bytes": released,
            "publication_seconds": time.perf_counter() - began,
            "publisher_memory": _process_memory()}


def _encode_snapshot(checkpoint: SelfContainedCheckpoint) -> FrozenCheckpoint:
    began = time.perf_counter()
    raw = encode_state(checkpoint)
    return FrozenCheckpoint(
        raw,
        (),
        FrozenObject.of(
            {
                "schema": CHECKPOINT_SCHEMA,
                "run": checkpoint.identity.run,
                "training_generation": checkpoint.identity.training_generation,
                "sequence": checkpoint.identity.sequence,
                "version": checkpoint.counters.mu_version,
                "encoder_memory": _process_memory(),
            }
        ),
        time.perf_counter() - began,
    )


def _resume_config(value: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(value)
    # Addresses, startup provenance and CPU scheduling do not change objectives.
    for key in (
        "peer",
        "device",
        "threads",
        "wall_seconds",
        "deadline_at",
        "operating_budget",
        "max_versions",
        "resume_checkpoint",
        "resume_source",
        "republish_on_resume",
        "start_package",
        "start_artifact_id",
        "start_model_sha256",
        "tensorboard_steps",
        "remote_stop_seconds",
    ):
        value.pop(key, None)
    for key in OPERATIONAL_FIELDS:
        value["segments"].pop(key, None)
    if value.get("performance") is not None:
        from .performance import PREPARED_PERFORMANCE_FIELDS

        value["performance"] = {key: value["performance"][key]
                                for key in PREPARED_PERFORMANCE_FIELDS}
    return value


class SegmentLearner:
    def __init__(self, config: LearnerConfig, work: Path, io: SegmentLearnerIO) -> None:
        if config.segments is None or config.anchor is not None or config.critic is not None:
            raise BoundaryError("rl_learner", "explicit_segment_config_without_legacy_objectives")
        settings = config.segments
        if config.performance is not None and config.performance.compile_loss:
            raise BoundaryError("rl_learner", "prepared_loss_compile_retired_after_G0")
        if config.loss.mode != "ppo":
            raise BoundaryError("rl_learner", "RL41_requires_PPO_without_legacy_objectives")
        if (type(config.lr) not in (int, float) or not 0 < config.lr < 1
                or type(config.weight_decay) not in (int, float)
                or not 0 <= config.weight_decay < 1):
            raise BoundaryError("rl_learner", "invalid_optimizer_configuration")
        self.config, self.settings, self.work, self.io = config, settings, work, io
        work.mkdir(parents=True, exist_ok=True)
        self.producer = source_identity(ROOT)
        self.exchange = Exchange(config.peer, config.run)
        self.archive = None
        self.generation = settings.training_generation or "g" + uuid.uuid4().hex
        if self.exchange.stop_request(training_generation=self.generation) is not None:
            raise BoundaryError("rl_learner", "cannot_resume_a_permanently_stopped_run")
        self.deadline_at = None
        self.active_training_began: float | None = None
        self.active_training_seconds = 0.
        self.device = _device(config.device)
        torch.set_num_threads(config.threads)
        torch.manual_seed(config.seed)
        self.rng = random.Random(config.seed)
        self.queue = SegmentQueue(settings)
        self.condition = self.queue.condition
        self.gpu_lock = threading.RLock()
        self.terminal = AuxiliaryPool(settings.terminal_games, settings.terminal_uses)
        self.hp = AuxiliaryPool(settings.hp_battles, settings.hp_uses)
        self.lease_owners: dict[str, dict[str, Any]] = {}
        self.actor_episodes: dict[str, str] = {}
        self.retired_actors: set[str] = set()
        self.sources: dict[str, dict[str, Any]] = {}
        self.hot_references: dict[tuple[str, str], HotPayloadRef] = {}
        self.newlease_stopped = self.input_fenced = False
        self.input_failure: dict[str, Any] | None = None
        self.input_sequence = 0
        self.input_head: str | None = None
        self.input_anchor = config.resume_checkpoint
        self.updates = self.datasets = self.version = self.checkpoint_sequence = 0
        self.published_updates = self.first_use = self.first_use_multi = 0
        self.active: dict[str, Any] | None = None
        self.active_batch: PreparedBatch | None = None
        self.device_batch: PreparedBatch | None = None
        self.pending_batch: tuple[dict[str, Any], Future[PreparedBatch]] | None = None
        self.performance = config.performance or PerformanceConfig(
            packed=True,
            tensor_cache=True,
            joint_nodes=True,
            microbatch_size=32,
            deduplicate_text=True,
            scene_attention_area=67108864,
            text_attention_area=67108864,
        )
        self.timing = {
            "preparation_wait_seconds": 0.0,
            "preparation_seconds": 0.0,
            "prepared_batches": 0.0,
            "optimizer_attempt_seconds": 0.0,
            "snapshot_seconds": 0.0,
            "waiting_for_data_seconds": 0.0,
            "input_transfer_host_seconds": 0.0,
            "input_transfer_copies": 0.0,
        }
        self.cuda_rng: tuple[torch.Tensor, ...] = ()
        self.conversion = None
        restored = (
            self._read_checkpoint(config.resume_checkpoint) if config.resume_checkpoint else None
        )
        cp_deadline = restored.config.get("deadline_at") if restored is not None else None
        if cp_deadline is not None and config.deadline_at not in {None, cp_deadline}:
            raise BoundaryError("rl_learner", "checkpoint_deadline_cannot_be_reset")
        cp_budget = restored.config.get("operating_budget") if restored is not None else None
        budget = copy.deepcopy(config.operating_budget if config.operating_budget is not None
                               else cp_budget)
        if budget is not None:
            if (set(budget) != {"id", "total_seconds", "consumed_seconds"}
                    or not isinstance(budget["id"], str)
                    or any(type(budget[k]) not in (int, float) or not math.isfinite(budget[k])
                           for k in ("total_seconds", "consumed_seconds"))
                    or budget["consumed_seconds"] < 0 or not 0 < budget["total_seconds"] <= 72000):
                raise ValueError("invalid_operating_budget")
            if cp_budget is not None:
                if any(budget[k] != cp_budget[k] for k in ("id", "total_seconds")):
                    raise ValueError("checkpoint_operating_budget_cannot_be_reset")
                budget["consumed_seconds"] = max(budget["consumed_seconds"],
                                                  cp_budget["consumed_seconds"])
        self.deadline_at = self.exchange.run_deadline(
            cp_deadline if cp_deadline is not None else config.deadline_at,
            budget_id=budget["id"] if budget is not None else None)
        self.config = replace(config, deadline_at=self.deadline_at, operating_budget=budget,
            wall_seconds=min(config.wall_seconds,
                             max(0, budget["total_seconds"] - budget["consumed_seconds"]))
            if budget is not None else config.wall_seconds)
        if restored is not None:
            if (
                restored.identity.run != config.run
                or restored.identity.training_generation == self.generation
                or _resume_config(restored.config) != _resume_config(asdict(config))
                or restored.producer != (config.resume_source or self.producer.to_dict())
            ):
                raise ValueError("self_contained_resume_source_config_or_generation_changed")
            # Caller has stopped the old learner/preparers. This only revokes
            # the old generation's new hot slots, never original CAS/assets.
            discarded_generations = {restored.identity.training_generation}
            if settings.rendezvous_path and Path(settings.rendezvous_path).exists():
                previous_attachment = json.loads(Path(settings.rendezvous_path).read_bytes())
                if previous_attachment["run"] != config.run:
                    raise ValueError("restore_rendezvous_run_changed")
                previous_generation = previous_attachment["training_generation"]
                if previous_generation == self.generation:
                    raise ValueError("restore_requires_fresh_training_generation")
                discarded_generations.add(previous_generation)
            for generation in sorted(discarded_generations):
                self.exchange.discard_hot_generation(generation)
            self.model = construct_model(restored.construction)
            self.model.load_state_dict(restored.weights, strict=True)
        else:
            self.model = self._start_model()
        if self.device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(self.device)
        self.model.to(self.device).eval().requires_grad_(True)
        self.compiler_identity = (
            settings.input_compiler_identity or FrameCompiler(self.model.vocabulary).identity
        )
        self.construction_template = self._initial_construction()
        self.optimizer = torch.optim.AdamW(
            [
                {
                    "params": [
                        p
                        for name, p in self.model.named_parameters()
                        if not name.startswith("rl4_heads.")
                    ],
                    "lr": config.lr,
                },
                {"params": list(self.model.rl4_heads.parameters()), "lr": settings.head_lr},
            ],
            weight_decay=config.weight_decay,
        )
        self.ema = {name: value.detach().clone() for name, value in self.model.state_dict().items()}
        if restored is not None:
            if self._optimizer_names() != restored.optimizer_parameter_names:
                raise ValueError("checkpoint_optimizer_parameter_order_changed")
            self.optimizer.load_state_dict(restored.optimizer)
            self.ema = {name: value.to(self.device) for name, value in restored.ema.items()}
            counters = restored.counters
            for name, value in (
                ("updates", counters.updates),
                ("datasets", counters.datasets),
                ("first_use", counters.first_use),
                ("first_use_multi", counters.first_use_multi),
                ("version", counters.mu_version),
                ("published_updates", counters.published_updates),
                ("checkpoint_sequence", restored.identity.sequence),
            ):
                setattr(self, name, value)
            self.conversion = restored.conversion
            self.cuda_rng = restored.rng.cuda_rng
            self.rng.setstate(restored.rng.learner_random)
        self.input_preparer = BatchPreparer(
            self.model.vocabulary,
            workers=settings.preparation_workers,
            threads=settings.preparation_threads,
            peer=config.peer,
            run=config.run,
            scratch=work,
            reader=io.read_hot_segment,
            microbatch_size=self.performance.microbatch_size,
            deduplicate_text=self.performance.deduplicate_text,
            scene_attention_area=self.performance.scene_attention_area,
            text_attention_area=self.performance.text_attention_area,
            excluded_seeds=frozenset(settings.excluded_seeds),
            gae_lambda=settings.gae_lambda,
        )
        self.publisher = ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_publisher_initialize,
        )
        self.telemetry = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rl-telemetry")
        self.telemetry_futures: deque[Future[Any]] = deque()
        self.checkpoint_encoder = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rl-cp-encode"
        )
        self.checkpoint_encoding: Future[FrozenCheckpoint] | None = None
        self.checkpoints = CheckpointPublisher(self.exchange.store, self.producer, background=True)
        self.checkpoint_requested = False
        self.pending_publication: Future[dict[str, Any]] | None = None
        self.latest_snapshot: dict[str, Any] | None = None
        self.pending_labels: deque[tuple[Future[Any], tuple[BatchMember, ...], str]] = deque()
        self.metrics = (
            TensorBoardMetrics(
                self.exchange,
                work / "tb",
                value_description="RL4.1 categorical TD and public terminal/HP labels",
            )
            if config.tensorboard_steps
            else None
        )
        # One private CPU template is constructed at initialization. Background
        # publication deep-copies it without touching global RNG or live theta.
        self.central_template = (
            copy.deepcopy(self.model).cpu().eval().requires_grad_(False)
            if settings.central_scoring else None
        )
        # First publication is initialization, outside the training loop. Restores
        # always publish from embedded theta, never fetch an old mu/start package.
        if restored is not None:
            self.version += 1
        initial = self._submit_publication(
            self._capture_model(), self.version, self.updates
        ).result()
        self.published_updates = initial["updates"]
        self.pool = create_segment_inference_pool(
            self.model.vocabulary,
            initial["identity"],
            control=self.control,
            central_scoring=settings.central_scoring,
            model=initial.get("model"),
            max_batch=settings.inference_batch,
            window_ms=settings.inference_window_ms,
            segment_steps=settings.steps,
            compiler_identity=self.compiler_identity,
        )
        for destination in {
            work / "inference.json",
            Path(settings.rendezvous_path) if settings.rendezvous_path else work / "inference.json",
        }:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name("." + destination.name + "." + self.generation)
            temporary.write_bytes(json_bytes(self.pool.attachment()))
            temporary.replace(destination)
        if restored is not None:
            # Constructors/publication workers must not alter the resumed RNG.
            torch.set_rng_state(restored.rng.torch_rng)
            if restored.rng.python_random is not None:
                random.setstate(restored.rng.python_random)
            if self.device.type == "cuda":
                torch.cuda.set_rng_state_all(list(restored.rng.cuda_rng))
        from spireagent.storage.async_archive import ArchivedStore

        self.archive = self.exchange.store.archive if isinstance(
            self.exchange.store, ArchivedStore) else None
        if self.archive is not None:
            self.archive.start_capacity_monitor(settings.archive_poll_seconds)

    def archive_capacity(self) -> dict[str, Any]:
        if self.archive is None:
            return {"ready": True, "archive": "not_configured"}
        state = self.archive.capacity_status()
        ready = bool(state.get("fresh") and state.get("error") is None
            and state.get("quota_status") != "observation_failed"
            and state.get("free_bytes", 0) >= self.settings.archive_min_free_bytes
            and (self.settings.archive_min_free_inodes == 0 or (
                type(state.get("free_inodes")) is int
                and state["free_inodes"] >= self.settings.archive_min_free_inodes))
            and state.get("pending_bytes_estimate", 0) <= self.settings.archive_pending_bytes)
        return {**state, "ready": ready}

    def _read_checkpoint(self, artifact_id: str) -> SelfContainedCheckpoint:
        manifest = self.exchange.store.get_manifest(artifact_id)
        if (
            manifest.kind != "checkpoint"
            or manifest.parameters.value().get("schema") != CHECKPOINT_SCHEMA
        ):
            raise ValueError("self_contained_checkpoint_required_convert_CP16_explicitly")
        # This is the entire restore IO: one manifest and its own payload.
        checkpoint = decode_state(
            self.exchange.store.bytes(
                manifest.payload("checkpoint"), maximum=manifest.payload("checkpoint").size
            )
        )
        if manifest.parents:
            raise ValueError("self_contained_checkpoint_has_no_restore_parents")
        return checkpoint

    def _start_model(self) -> MStd:
        if self.config.start_package:
            package = Path(self.config.start_package)
        elif self.config.start_artifact_id and self.config.start_model_sha256:
            package = self.exchange.materialize_model(
                {
                    "model_artifact_id": self.config.start_artifact_id,
                    "model_sha256": self.config.start_model_sha256,
                },
                self.work / "models",
            )
        else:
            raise BoundaryError("rl_learner", "start_package_or_self_contained_CP_required")
        descriptor = json.loads((package / "model.json").read_bytes())
        model, _ = load_model(
            package / "model.tensor-tree",
            self.config.start_model_sha256 or descriptor["package_sha256"],
        )
        model.enable_rl4_heads(0)
        # A pre-existing fitted head snapshot is an explicit initial state, not
        # a warmup phase. RL4.1 never trains/opens a separate warmup path.
        if self.settings.warmup_artifact_id:
            artifact = self.exchange.store.get_manifest(self.settings.warmup_artifact_id)
            fitted = decode_checkpoint(self.exchange.store.bytes(artifact.payload("checkpoint")))
            if fitted.get("schema") != "stpd/rl4-head-warmup-1":
                raise ValueError("explicit_fitted_RL4_heads_required")
            if fitted["model_package_sha256"] != descriptor["package_sha256"]:
                raise ValueError("fitted_heads_start_policy_changed_use_explicit_CP_conversion")
            model.rl4_heads.load_state_dict(fitted["heads"], strict=True)
        return model

    def _optimizer_names(self) -> tuple[tuple[str, ...], ...]:
        names = {id(parameter): name for name, parameter in self.model.named_parameters()}
        return tuple(
            tuple(names[id(parameter)] for parameter in group["params"])
            for group in self.optimizer.param_groups
        )

    def _initial_construction(self) -> ModelConstruction:
        execution = replace(
            self.model.execution,
            implementation="experimental-packed-v1",
            microbatch_size=min(64, self.settings.inference_batch),
        )
        return ModelConstruction(
            copy.deepcopy(self.model.architecture),
            copy.deepcopy(self.model.vocabulary),
            input_spec(self.model.vocabulary, runtime_projection(self.model.vocabulary)),
            copy.deepcopy(self.compiler_identity),
            adapter_identity(),
            {
                "execution": asdict(execution),
                "model_size": self.model.model_size,
                "precision": "fp32",
            },
            "",
        )

    def _capture_model(self) -> dict[str, Any]:
        began = time.perf_counter()
        with self.gpu_lock:
            weights = snapshot_weights(self.model.state_dict())
        snapshot = {
            "weights": weights,
            "construction": self.construction_template,
            "deduplicate_text": self.performance.deduplicate_text,
            "updates": self.updates,
        }
        self.timing["snapshot_seconds"] += time.perf_counter() - began
        return snapshot

    def _submit_publication(
        self, snapshot: dict[str, Any], version: int, updates: int
    ) -> Future[Any]:
        published = self.publisher.submit(
            _publish_snapshot,
            pack_transport(snapshot),
            peer=self.config.peer,
            run=self.config.run,
            generation=self.generation,
            version=version,
            updates=updates,
            producer=self.producer,
            publish_model=self.io.publish_model,
            work=str(self.work),
        )
        if not self.settings.central_scoring:
            return published
        return self.telemetry.submit(self._central_publication, published, snapshot)

    def _central_publication(
        self, published: Future[Any], snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        result = published.result()
        if self.central_template is None:
            raise ValueError("central_publication_requires_explicit_backend")
        model = copy.deepcopy(self.central_template)
        model.load_state_dict(snapshot["weights"], strict=True)
        from stpd.p1.execution import MStdExecution

        model.execution = MStdExecution.decode(result["identity"]["execution"])
        model.eval().requires_grad_(False)
        model.deduplicate_text = snapshot["deduplicate_text"]
        if self.device.type == "cuda":
            stream = torch.cuda.Stream(device=self.device)
            with torch.cuda.stream(stream):
                model.to(self.device)
            stream.synchronize()  # worker wait; never the learner's training lock
        else:
            model.to(self.device)
        return {**result, "model": model}

    def _kick_publication(self) -> None:
        if self.pending_publication is None and self.latest_snapshot is not None:
            snapshot, self.latest_snapshot = self.latest_snapshot, None
            self.version += 1
            self.pending_publication = self._submit_publication(
                snapshot, self.version, snapshot["updates"]
            )

    def _collect_publication(self, *, wait: bool = False) -> None:
        while self.pending_publication is not None:
            if not wait and not self.pending_publication.done():
                return
            publication = self.pending_publication.result()
            with self.condition:
                if isinstance(self.pool, CentralInferencePool):
                    self.pool.publish(publication["model"], publication["identity"])
                else:
                    self.pool.publish(publication["identity"])
                self.published_updates = publication["updates"]
                self.pending_publication = None
                self.pool.retire(
                    {owner["mu"]["snapshot_sha256"] for owner in self.lease_owners.values()}
                )
            self._event("model_published", version=publication["identity"]["version"],
                        updates=publication["updates"],
                        temporary_export_released_bytes=publication["temporary_export_released_bytes"],
                        publication_seconds=publication["publication_seconds"],
                        publisher_memory=publication["publisher_memory"])
            self._kick_publication()
            if not wait:
                return

    def _schedule_publication(self) -> None:
        self.latest_snapshot = (
            self._capture_model()
        )  # replace unsent snapshot; skip intermediate mu
        self._collect_publication(wait=False)
        self._kick_publication()

    def _receipt(self, request: dict[str, Any]) -> dict[str, Any] | None:
        receipt = self.exchange.input_receipt(request)
        return receipt["response"] if receipt is not None else None

    def _accept(
        self, request: dict[str, Any], response: dict[str, Any], apply: Callable[[], None]
    ) -> dict[str, Any]:
        duplicate = self._receipt(request)
        if duplicate is not None:
            return duplicate
        try:
            record = self.exchange.append_input(
                request,
                response,
                sequence=self.input_sequence + 1,
                previous=self.input_head,
                producer=self.producer,
                anchor=self.input_anchor,
            )
            apply()
            self.input_sequence, self.input_head = record["sequence"], record["input_artifact_id"]
        except BaseException as error:
            self.input_fenced = self.newlease_stopped = True
            self.input_failure = {
                "operation": request["operation"],
                "error_type": type(error).__name__,
                "last_applied_sequence": self.input_sequence,
            }
            self.condition.notify_all()
            raise
        self.condition.notify_all()
        return response

    def control(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = request["operation"]
        if operation == "status":
            return {
                "run": self.config.run,
                "training_generation": self.generation,
                "accepting_new_segments": not self.newlease_stopped,
                "server_time": time.time(),
                "pool_id": self.pool.pool_id,
                "input_sequence": self.input_sequence,
                "input_fenced": self.input_fenced,
                "archive_capacity": self.archive_capacity(),
            }
        if operation == "receipt":
            # Old generation receipts remain facts; querying does not restore a
            # queue, re-grant a lease or retry a World action.
            response = self._receipt(control_input(request["input"]))
            return {"found": response is not None, "response": response}
        request = control_input(request)
        if request.get("run") != self.config.run:
            return {
                "stopped": True,
                "reason": "generation_changed",
                "training_generation": self.generation,
            }
        with self.condition:
            if self.input_fenced:
                raise BoundaryError("rl_learner", "input_owner_fenced_restore_last_checkpoint")
            duplicate = self._receipt(request)
            if duplicate is not None:
                return duplicate
            if request.get("training_generation") != self.generation:
                return {
                    "stopped": True,
                    "reason": "generation_changed",
                    "training_generation": self.generation,
                }
        if operation == "commit":
            return self._admit(request)
        if operation == "episode_end":
            return self._end_episode(request)
        actor, episode = request["actor"], request["episode"]
        with self.condition:
            if actor in self.retired_actors:
                return {"stopped": True, "reason": "actor_allocation_terminal"}
            if operation == "episode_open":
                if self.newlease_stopped:
                    return {"admitted": False}
                return self._accept(
                    request,
                    {"admitted": True, "run": self.config.run,
                     "training_generation": self.generation,
                     "budget_id": (self.config.operating_budget or {}).get("id")},
                    lambda: self.actor_episodes.__setitem__(f"{actor}:{episode}", episode),
                )
            if operation == "begin":
                while not self.newlease_stopped and f"{actor}:{episode}" in self.actor_episodes:
                    if not self.archive_capacity()["ready"]:
                        self.condition.wait(self.settings.archive_poll_seconds)
                        continue
                    duplicate = self._receipt(request)
                    if duplicate is not None:
                        return duplicate
                    previous = request.get("previous_lease")
                    if (
                        previous is not None
                        and previous != f"{actor}/{self.queue.cursors.get(actor, -1)}"
                    ):
                        raise ValueError("segment_begin_predecessor_changed")
                    lease = f"{actor}/{self.queue.cursors.get(actor, -1) + 1}"
                    episode_pending = sum(
                        record["rows"] for record in self.queue.pending
                        if record["actor"] == actor and record["episode"] == episode)
                    episode_inflight = sum(
                        self.queue.leases[lease]
                        for lease, owner in self.lease_owners.items()
                        if owner["actor"] == actor and owner["episode"] == episode)
                    if (self.queue.occupied + self.settings.steps <= self.settings.capacity_rows
                            and episode_pending + episode_inflight + self.settings.steps
                            <= self.settings.episode_rows):
                        mu = copy.deepcopy(self.pool.snapshots[self.pool.latest])

                        def grant(lease: str = lease, mu: dict[str, Any] = mu) -> None:
                            if not self.queue.reserve(lease):
                                raise ValueError("input_reservation_capacity_changed")
                            self.lease_owners[lease] = {
                                "actor": actor,
                                "episode": episode,
                                "mu": mu,
                            }

                        return self._accept(
                            request,
                            {
                                "run": self.config.run,
                                "training_generation": self.generation,
                                "mu": mu,
                                "lease": lease,
                                "server_time": time.time(),
                            },
                            grant,
                        )
                    self.condition.wait(0.1)
                return {"stopped": True}
        raise ValueError("unsupported_segment_control")

    @staticmethod
    def _member(record: dict[str, Any]) -> BatchMember:
        payload = record["payload"]
        return BatchMember(
            record["segment_id"],
            payload if isinstance(payload, HotPayloadRef) else HotPayloadRef(**payload),
            record["run_id"],
            record["rows"],
            record["mu"]["snapshot_sha256"],
            record["episode"],
        )

    def _retain(self, payload: HotPayloadRef, owner: str) -> None:
        with self.condition:
            self.exchange.retain_hot_payload(payload, owner)
            self.hot_references[payload.slot, owner] = payload

    def _release(self, payload: HotPayloadRef, owner: str) -> None:
        with self.condition:
            self.exchange.release_hot_payload(payload, owner)
            del self.hot_references[payload.slot, owner]

    def _admit(self, request: dict[str, Any]) -> dict[str, Any]:
        # ACK checks only the immutable envelope/capacity. W3 verifies the source;
        # workers perform complete read/decoding/target checks before ready.
        with self.condition:
            duplicate = self._receipt(request)
            if duplicate is not None:
                return duplicate
            owner = self.lease_owners.get(request["segment_id"])
            if owner is None or owner["mu"] != request["mu"]:
                raise ValueError("segment_reserved_behavior_changed")
            record = {**request, "episode": owner["episode"], "actor": owner["actor"]}
            member = self._member(record)
            if member.payload.slot.split("/")[:2] != [self.config.run, self.generation]:
                raise ValueError("segment_payload_generation_changed")
            self.queue.validate_commit(member.segment_id, record)
            # Explicit owners, acquired before ACK; an uncertain write keeps refs
            # and fences the owner rather than deleting/retrying unknown delivery.
            self._retain(member.payload, "ppo:" + member.segment_id)
            self._retain(member.payload, "delayed:" + owner["episode"])

            def admit() -> None:
                self.queue.commit(member.segment_id, record)
                self.sources[member.segment_id] = record
                self.lease_owners.pop(member.segment_id)

            return self._accept(request, {"segment_id": member.segment_id}, admit)

    def _members_for_run(self, run_id: str) -> tuple[BatchMember, ...]:
        with self.condition:
            return tuple(
                self._member(record)
                for record in self.sources.values()
                if record["run_id"] == run_id
            )

    def _end_episode(self, request: dict[str, Any]) -> dict[str, Any]:
        with self.condition:
            if f"{request['actor']}:{request['episode']}" not in self.actor_episodes:
                raise ValueError("episode_end_without_open")
            members = self._members_for_run(request["run_id"])
            request_hash = hashlib.sha256(json_bytes(request)).hexdigest()
        # No condition/GPU lock while source/label validation runs in a CPU worker.
        groups = self.input_preparer.pool.submit(verify_auxiliary, request, members).result()
        with self.condition:
            duplicate = self._receipt(request)
            if duplicate is not None:
                return duplicate

            if (f"{request['actor']}:{request['episode']}" not in self.actor_episodes
                    or members != self._members_for_run(request["run_id"])
                    or hashlib.sha256(json_bytes(request)).hexdigest() != request_hash):
                raise ValueError("episode_end_source_or_owner_changed_during_validation")

            def end() -> None:
                self._add_auxiliary(groups)
                for lease, owner in list(self.lease_owners.items()):
                    if (
                        owner["actor"] == request["actor"]
                        and owner["episode"] == request["episode"]
                    ):
                        self.queue.release(lease)
                        self.lease_owners.pop(lease)
                self.actor_episodes.pop(f"{request['actor']}:{request['episode']}")

            response = self._accept(request, {"run_id": request["run_id"]}, end)
            for member in members:
                self._release(member.payload, "delayed:" + request["episode"])
            self._forget_unreferenced()
            return response

    def retire_actor(self, actor: str, allocation: dict[str, Any]) -> dict[str, Any]:
        """Release capacity after the execution owner proves this actor is dead.

        This is not episode_end: no terminal/labels/result is invented, no input
        receipt changes, and replacement must have a new actor/seed namespace.
        """
        if (allocation.get("terminal") is not True or allocation.get("actor") != actor
                or not isinstance(allocation.get("job_id"), str)
                or allocation.get("state") not in {"COMPLETED", "FAILED", "CANCELLED",
                    "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED", "BOOT_FAIL",
                    "DEADLINE", "REVOKED"}):
            raise ValueError("exact_terminal_actor_allocation_required")
        with self.condition:
            if actor in self.retired_actors:
                return {"actor": actor, "already_retired": True}
            episodes = [key.split(":", 1)[1] for key in self.actor_episodes
                        if key.startswith(actor + ":")]
            leases = [key for key, owner in self.lease_owners.items() if owner["actor"] == actor]
            for key in leases:
                self.queue.release(key)
                self.lease_owners.pop(key)
            for episode in episodes:
                self.actor_episodes.pop(f"{actor}:{episode}")
                for record in list(self.sources.values()):
                    owner = "delayed:" + episode
                    if (record["actor"] == actor and record["episode"] == episode
                            and (record["payload"].slot, owner) in self.hot_references):
                        self._release(record["payload"], owner)
            self.retired_actors.add(actor)
            result = {"actor": actor, "allocation": allocation,
                      "unresolved_episodes": episodes, "released_leases": leases,
                      "new_games": 0, "original_receipts_changed": False}
            self._event("actor_retired", **result)
            self._forget_locked()
            self.condition.notify_all()
            return result

    def _add_auxiliary(self, groups: list[tuple[str, str, list[dict[str, Any]]]]) -> None:
        for kind, identity, rows in groups:
            pool = self.terminal if kind == "terminal" else self.hp
            pool.validate_add(identity, rows)
            before = {key: values for key, values in pool.groups.items()}
            owner = f"aux:{kind}:{identity}"
            for member in {row["member"].segment_id: row["member"] for row in rows}.values():
                self._retain(member.payload, owner)
            pool.add(identity, rows)
            for key, old in before.items():
                if key not in pool.groups:
                    for member in {row["member"].segment_id: row["member"] for row in old}.values():
                        self._release(member.payload, f"aux:{kind}:{key}")

    def _collect_labels(self, *, wait: bool = False) -> None:
        while self.pending_labels and (wait or self.pending_labels[0][0].done()):
            future, members, owner = self.pending_labels.popleft()
            groups = future.result()
            with self.condition:
                self._add_auxiliary(groups)
                for member in members:
                    self._release(member.payload, owner)

    def _ready_labels(self, batch: PreparedBatch) -> None:
        for labels in batch.label_updates:
            if not labels["hp_battles"]:
                continue
            request = {**labels, "training_generation": self.generation, "outcome_states": []}
            # Terminal supervision arrives through typed EpisodeLabels; a PPO
            # source's already checked reward boundaries can backfill HP sooner.
            request["terminal"] = None
            members = self._members_for_run(labels["run_id"])
            owner = "labels:" + labels["segment_id"]
            for member in members:
                self._retain(member.payload, owner)
            self.pending_labels.append(
                (
                    self.input_preparer.pool.submit(verify_auxiliary, request, members),
                    members,
                    owner,
                )
            )

    def _select(self, *, take: bool = True) -> dict[str, Any] | None:
        with self.condition:
            records = self.queue.dataset(take=take)
            if records is None:
                return None
            n = sum(record["rows"] for record in records)
            order = list(range(n))
            self.rng.shuffle(order)
            parts = tuple(
                tuple(order[i] for i in group)
                for group in minibatch_members(n, self.settings.minibatches)
            )
            reserved = {
                row["key"]
                for name in ("terminal", "hp")
                for row in (self.active[name] if self.active else [])
            }
            selected = {}
            for kind, pool in (("terminal", self.terminal), ("hp", self.hp)):
                selected[kind] = pool.sample(
                    self.settings.auxiliary_rows, self.rng, epochs=1,
                    exclude=reserved
                )
            auxiliary, owners = [], []
            auxiliary_kinds: tuple[Literal["terminal", "hp"], ...] = ("terminal", "hp")
            for kind in auxiliary_kinds:
                for i, row in enumerate(selected[kind]):
                    auxiliary.append(
                        AuxiliarySelection(
                            row["source"],
                            kind,
                            float(row["outcome" if kind == "terminal" else "hp"]),
                            row["uses"],
                            self.terminal.max_uses if kind == "terminal" else self.hp.max_uses,
                        )
                    )
                    owners.append(i % self.settings.minibatches)
            number = self.datasets + (2 if self.active is not None else 1)
            request = PrepareRequest(
                RunSequence(self.config.run, self.generation, number),
                tuple(self._member(record) for record in records),
                tuple(auxiliary),
                parts,
                tuple(owners),
                self.compiler_identity,
                self.rng.getstate(),
                self.settings.prepared_bytes // 2,
                epochs=self.settings.epochs,
            )
            active = {
                "request": request,
                "segments": records,
                **selected,
                "used": [],
                "minibatch": 0,
                "epoch": 0,
                "start_updates": self.updates,
                "taken": take,
            }
            return active

    def _submit_batch(self, active: dict[str, Any]) -> Future[PreparedBatch]:
        request = active["request"]
        owner = f"prepare:{self.generation}:{request.identity.sequence}"
        # Aux holds a complete verified row copy; only PPO still reads hot bytes.
        for member in request.members:
            self._retain(member.payload, owner)
        inputs = tuple(row["prepared_input"] for kind in ("terminal", "hp") for row in active[kind])
        return self.input_preparer.submit(request, inputs)

    def _prepare_ahead(self) -> None:
        if self.pending_batch is None and self.settings.prepare_ahead:
            active = self._select(take=False)
            if active is not None:
                self.pending_batch = active, self._submit_batch(active)

    def _prepare(self) -> bool:
        if self.active_batch is not None:
            return True
        self._collect_labels()
        if self.pending_batch is None:
            active = self._select()
            if active is None:
                return False
            self.pending_batch = active, self._submit_batch(active)
        active, future = self.pending_batch
        began = time.perf_counter()
        batch = future.result()
        self.timing["preparation_wait_seconds"] += time.perf_counter() - began
        self.timing["preparation_seconds"] += batch.timings["request_wall_seconds"]
        self.timing["prepared_batches"] += 1
        if not active["taken"]:
            with self.condition:
                if self.queue.dataset() != active["segments"]:
                    raise ValueError("prepared_dataset_membership_changed")
            active["taken"] = True
        self.pending_batch = None
        self.active, self.active_batch = active, batch
        active["start_updates"] = self.updates
        transfer_started = time.perf_counter()
        self.device_batch, copies = stage_batch(batch, self.device)
        self.timing["input_transfer_host_seconds"] += time.perf_counter() - transfer_started
        self.timing["input_transfer_copies"] += copies
        self._ready_labels(batch)
        # Complete batch owns its tensors now. Release preparation and PPO bytes;
        # delayed-label/aux owners independently protect their own future reads.
        for member in batch.request.members:
            self._release(
                member.payload, f"prepare:{self.generation}:{batch.request.identity.sequence}"
            )
        for member in batch.request.members:
            self._release(member.payload, "ppo:" + member.segment_id)
        self._event(
            "batch_ready",
            dataset=batch.request.identity.sequence,
            rows=len(batch.metadata),
            preparation=batch.timings,
            bytes=batch.payload_bytes,
        )
        return True

    def _step(self) -> dict[str, Any]:
        active, batch = self.active, self.active_batch
        assert active is not None and batch is not None
        assert self.device_batch is not None
        index = active["minibatch"]
        began = time.perf_counter()
        minibatch = self.device_batch.minibatches[index]
        if minibatch.epoch_auxiliary:
            epoch = active["epoch"]
            terminal_rows, hp_rows = minibatch.epoch_auxiliary_counts[epoch]
            minibatch = replace(minibatch, terminal_rows=terminal_rows, hp_rows=hp_rows,
                                tensors={**minibatch.tensors, **minibatch.epoch_auxiliary[epoch]})
        result = prepared_update(
            self.model,
            self.optimizer,
            minibatch,
            config=self.config.loss,
            performance=self.performance,
            target_kl=self.settings.target_kl,
            gpu_lock=self.gpu_lock,
            diagnostics=self.updates % self.settings.diagnostics_every == 0,
        )
        self.timing["optimizer_attempt_seconds"] += time.perf_counter() - began
        if result["stepped"]:
            newly_used = [i for i in batch.request.minibatch_rows[index] if i not in active["used"]]
            self.first_use += len(newly_used)
            self.first_use_multi += sum(bool(batch.policy_mask[i]) for i in newly_used)
            active["used"].extend(newly_used)
            with self.condition:
                for kind, pool in (("terminal", self.terminal), ("hp", self.hp)):
                    offset = 0 if kind == "terminal" else len(active["terminal"])
                    rows = [
                        row
                        for local, row in enumerate(active[kind])
                        if batch.request.auxiliary_minibatches[offset + local] == index
                        and row["uses"] < pool.max_uses
                    ]
                    pool.used(rows, batch.request.identity.sequence, epochs=1)
                    for identity, retired in pool.retire_exhausted().items():
                        remaining = {
                            row["member"].segment_id for row in pool.groups.get(identity, [])
                        }
                        for member in {
                            row["member"].segment_id: row["member"] for row in retired
                        }.values():
                            if member.segment_id not in remaining:
                                self._release(member.payload, f"aux:{kind}:{identity}")
            decay = self.settings.ema_decay
            with torch.no_grad():
                weights = self.model.state_dict()
                floating = [name for name, value in weights.items() if value.is_floating_point()]
                torch._foreach_mul_([self.ema[name] for name in floating], decay)
                torch._foreach_add_(
                    [self.ema[name] for name in floating],
                    [weights[name] for name in floating],
                    alpha=1 - decay,
                )
                for name, value in weights.items():
                    if not value.is_floating_point():
                        self.ema[name].copy_(value)
            self.updates += 1
            rows = [batch.metadata[i] for i in newly_used]
            now = time.time()
            ages = sorted(
                now - row["generated_timestamp"]
                for row in rows
                if row["generated_timestamp"] is not None
            )
            result.update(
                first_use_age_samples_seconds=ages,
                first_use_age_seconds={
                    "n": len(ages),
                    "median": ages[len(ages) // 2] if ages else None,
                    "p95": ages[min(len(ages) - 1, int(len(ages) * 0.95))] if ages else None,
                },
                first_use_floors=[row["floor"] for row in rows],
                first_use_version_lag=[self.version - row["mu_version"] for row in rows],
                first_use_pre_update_SGD_lag=[self.updates - 1 - row["mu_updates"] for row in rows],
            )
        result.update(
            update=self.updates,
            dataset=batch.request.identity.sequence,
            minibatch=index,
            epoch=active["epoch"],
            first_use=self.first_use,
            first_use_multi=self.first_use_multi,
            memory=self._memory(),
        )
        active["minibatch"] += 1
        end_epoch = active["minibatch"] == len(batch.minibatches)
        finished = result["kl_stopped"] or (
            end_epoch and active["epoch"] + 1 >= self.settings.epochs)
        if end_epoch and not finished:
            active["epoch"] += 1
            active["minibatch"] = 0
        if finished:
            self.datasets += 1
            self.active = self.active_batch = None
            self.device_batch = None
            self._forget_unreferenced()
            if self.updates > active["start_updates"]:
                self._schedule_publication()
        # Fallible IO follows the complete learning transition; CP never pairs
        # new Adam/theta with old successful-use counters.
        self._event("progress", **result, version=self.version)
        if self.metrics is not None and self.updates % self.config.tensorboard_steps == 0:
            self.metrics.scalars(
                {
                    f"train/{name}": result.get(name)
                    for name in (
                        "td_loss",
                        "policy_loss",
                        "entropy",
                        "approx_kl_mu_theta",
                        "clip_fraction",
                        "terminal_loss",
                        "hp_loss",
                        "gradient_norm",
                        "first_use",
                        "first_use_multi",
                    )
                },
                self.updates,
            )
        return result

    def _forget_unreferenced(self) -> None:
        with self.condition:
            self._forget_locked()

    def _forget_locked(self) -> None:
        retained = {record["segment_id"] for record in self.queue.pending}
        for active in (self.active, self.pending_batch[0] if self.pending_batch else None):
            if active:
                retained.update(record["segment_id"] for record in active["segments"])
                retained.update(
                    row["member"].segment_id for name in ("terminal", "hp") for row in active[name]
                )
        retained.update(
            row["member"].segment_id
            for pool in (self.terminal, self.hp)
            for rows in pool.groups.values()
            for row in rows
        )
        for identity, record in list(self.sources.items()):
            if (
                identity not in retained
                and f"{record['actor']}:{record['episode']}" not in self.actor_episodes
            ):
                self.sources.pop(identity)

    def _memory(self) -> dict[str, int]:
        values = _process_memory()
        if self.device.type == "cuda":
            values.update(cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(self.device),
                          cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved(self.device))
        return values

    def _event(self, kind: str, **fields: Any) -> None:
        while self.telemetry_futures and self.telemetry_futures[0].done():
            self.telemetry_futures.popleft().result()
        if len(self.telemetry_futures) >= 8:
            self.telemetry_futures.popleft().result()
        self.telemetry_futures.append(
            self.telemetry.submit(
                self.exchange.publish_event, kind, training_generation=self.generation,
                budget_id=(self.config.operating_budget or {}).get("id"), **fields
            )
        )

    def _learning_state(self) -> SelfContainedCheckpoint:
        with self.gpu_lock:
            weights = snapshot_weights(self.model.state_dict())
            optimizer, ema = cpu_copy(self.optimizer.state_dict()), snapshot_weights(self.ema)
        names = self._optimizer_names()
        validate_optimizer_order(optimizer, names, weights)
        cuda = (
            tuple(torch.cuda.get_rng_state_all()) if self.device.type == "cuda" else self.cuda_rng
        )
        configuration = asdict(self.config)
        if configuration["operating_budget"] is not None:
            active = (time.monotonic() - self.active_training_began
                      if self.active_training_began is not None else self.active_training_seconds)
            configuration["operating_budget"]["consumed_seconds"] += active
        return SelfContainedCheckpoint(
            RunSequence(self.config.run, self.generation, self.checkpoint_sequence),
            replace(self.construction_template, text_digest=text_digest(weights)),
            configuration,
            self.producer.to_dict(),
            weights,
            optimizer,
            names,
            ema,
            LearningCounters(
                self.updates,
                self.datasets,
                self.first_use,
                self.first_use_multi,
                self.version,
                self.published_updates,
            ),
            RngState(
                random.getstate(),
                self.rng.getstate(),
                torch.get_rng_state().clone(),
                tuple(value.cpu().clone() for value in cuda),
            ),
            self.conversion,
        )

    def _state(self) -> tuple[bytes, tuple[Parent, ...]]:
        return encode_state(self._learning_state()), ()

    def _collect_checkpoint(self, *, wait: bool = False) -> None:
        if self.checkpoint_encoding is not None and (wait or self.checkpoint_encoding.done()):
            self.checkpoints.submit(self.checkpoint_encoding.result())
            self.checkpoint_encoding = None
        taken = self.checkpoints.take(wait=wait)
        if taken:
            info = taken[0].parameters.value()
            self.exchange.publish_checkpoint(
                info["version"],
                taken[0].artifact_id,
                sequence=info["sequence"],
                training_generation=info["training_generation"],
            )
            self._event("checkpoint_published", checkpoint_artifact_id=taken[0].artifact_id,
                        sequence=info["sequence"], timing=taken[1],
                        encoder_memory=info["encoder_memory"], memory=self._memory())

    def checkpoint(self, *, final: bool = False) -> None:
        if self.input_fenced:
            raise BoundaryError("rl_learner", "input_owner_fenced_restore_last_checkpoint")
        self._collect_checkpoint(wait=final)
        if self.active is not None and not final:
            self.checkpoint_requested = True
            return
        if self.checkpoint_encoding is not None or self.checkpoints.pending:
            return
        self.checkpoint_sequence += 1
        began = time.perf_counter()
        state = self._learning_state()
        self._event("checkpoint_captured", sequence=self.checkpoint_sequence,
                    capture_seconds=time.perf_counter() - began, memory=self._memory())
        self.checkpoint_encoding = self.checkpoint_encoder.submit(_encode_snapshot, state)
        self.checkpoint_requested = False
        if final:
            self._collect_checkpoint(wait=True)

    def close(self) -> None:
        with self.condition:
            self.newlease_stopped = True
            self.condition.notify_all()
        with ExitStack() as cleanup:
            cleanup.callback(self.publisher.shutdown, wait=True, cancel_futures=True)
            cleanup.callback(self.telemetry.shutdown, wait=True, cancel_futures=False)
            cleanup.callback(self.checkpoint_encoder.shutdown, wait=True, cancel_futures=False)
            cleanup.callback(self.checkpoints.__exit__, RuntimeError if self.input_fenced else None)
            if self.archive is not None:
                cleanup.callback(self.archive.stop_capacity_monitor)
            try:
                self.pool.close()
            finally:
                self.input_preparer.shutdown(wait=True, cancel_futures=False)
            for future, _, _ in self.pending_labels:
                future.result()
            for (_slot, owner), payload in list(self.hot_references.items()):
                self._release(payload, owner)
            for future in self.telemetry_futures:
                future.result()

    def run(self, *, initialize_only: bool = False,
            on_tick: Callable[[], None] | None = None) -> dict[str, Any]:
        try:
            summary = self._run(initialize_only=initialize_only, on_tick=on_tick)
        except BaseException as error:
            fields = {"training_generation": self.generation, "updates": self.updates,
                      "budget_id": (self.config.operating_budget or {}).get("id"),
                      "error_type": type(error).__name__,
                      "error_code": getattr(error, "code", None)}
            try:
                self.exchange.publish_event("failed", **fields)
            except Exception as publication_error:
                (self.work / "failed-event-unpublished.json").write_bytes(json_bytes(
                    {**fields, "publication_error_type": type(publication_error).__name__}))
            raise
        self.exchange.publish_event("finished", **summary)
        return summary

    def _run(self, *, initialize_only: bool = False,
             on_tick: Callable[[], None] | None = None) -> dict[str, Any]:
        began = time.monotonic()
        self.active_training_began = began
        deadline = began + self.config.wall_seconds
        checkpoint_at = began + self.settings.checkpoint_seconds
        stop_at = began
        try:
            self._event("training_started", active_seconds=0, updates=self.updates)
            while (
                not initialize_only
                and not self.input_fenced
                and time.monotonic() < deadline
                and (self.deadline_at is None or time.time() < self.deadline_at - 600)
                and not (self.work / "STOP").exists()
                and self.version < self.config.max_versions
            ):
                if time.monotonic() >= stop_at:
                    stop_at = time.monotonic() + self.config.remote_stop_seconds
                    if on_tick is not None:
                        on_tick()
                    self._event("heartbeat", active_seconds=time.monotonic() - began,
                                updates=self.updates, archive_capacity=self.archive_capacity())
                    if self.exchange.stop_request(training_generation=self.generation) is not None:
                        break
                self._collect_publication()
                self._collect_checkpoint()
                self._collect_labels()
                if time.monotonic() >= checkpoint_at:
                    self.checkpoint_requested = True
                    checkpoint_at = time.monotonic() + self.settings.checkpoint_seconds
                if self.active_batch is None:
                    if self.checkpoint_requested:
                        self.checkpoint()
                    if not self._prepare():
                        waiting = time.monotonic()
                        with self.condition:
                            self.condition.wait(0.1)
                        self.timing["waiting_for_data_seconds"] += time.monotonic() - waiting
                        continue
                self._prepare_ahead()
                self._step()
        finally:
            training_elapsed = time.monotonic() - began
            self.active_training_seconds = training_elapsed
            self.active_training_began = None
            with self.condition:
                self.newlease_stopped = True
                self.condition.notify_all()
            try:
                self._event("training_window", active_seconds=training_elapsed,
                            updates=self.updates)
                # In-flight worlds close through the original producer/receipt
                # path. No input is replayed and this is not a new training window.
                drain = time.monotonic() + 600
                while (
                    not self.input_fenced
                    and (self.queue.leases or self.actor_episodes)
                    and time.monotonic() < drain
                ):
                    if on_tick is not None:
                        on_tick()
                    with self.condition:
                        self.condition.wait(0.2)
                if not self.input_fenced:
                    self._collect_labels(wait=True)
                    if self.updates > self.published_updates:
                        self._schedule_publication()
                    self._collect_publication(wait=True)
                    self.checkpoint(final=True)
            finally:
                self.close()
        summary = {
            "updates": self.updates,
            "published_updates": self.published_updates,
            "versions": self.version,
            "datasets": self.datasets,
            "first_use": self.first_use,
            "first_use_multi": self.first_use_multi,
            "training_generation": self.generation,
            "checkpoint_sequence": self.checkpoint_sequence,
            "elapsed_seconds": time.monotonic() - began,
            "timing": self.timing,
            "memory": self._memory(),
            "learning_configuration": {"lr": self.config.lr,
                "weight_decay": self.config.weight_decay, "loss": asdict(self.config.loss),
                "segments": asdict(self.settings),
                "performance": self.config.frozen()["performance"]},
            "unresolved_inflight": len(self.queue.leases),
            "queued_rows": self.queue.rows,
            "unresolved_episodes": len(self.actor_episodes),
            "input_fenced": self.input_fenced,
            "input_failure": self.input_failure,
            "deadline_at": self.deadline_at,
            "budget_id": (self.config.operating_budget or {}).get("id"),
            "trajectory_encoding": "hot-segment",
        }
        (self.work / "learner-summary.json").write_bytes(json_bytes(summary))
        if self.input_fenced:
            raise BoundaryError("rl_learner", "input_owner_fenced_restore_last_checkpoint")
        if self.queue.leases or self.actor_episodes:
            raise BoundaryError("rl_learner", "unresolved_worlds_after_drain")
        return summary
