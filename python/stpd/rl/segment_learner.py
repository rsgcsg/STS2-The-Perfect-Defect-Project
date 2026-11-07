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
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import ExitStack, nullcontext
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from spireagent.rl_local_wal import WalReplication
    from spireagent.rl_socket_transport import BinarySources
    from stpd.rl.binary_preparation import BinaryBatchPreparer

import torch

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.local_diagnostics import exception_facts
from spireagent.rl_contracts import HotPayloadRef, RunSequence
from spireagent.rl_dataplane_ports import CONTROL_SCHEMA, BinaryRef
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
from stpd.p1.local_inference import (
    CentralInferencePool,
    SegmentInferencePool,
    create_segment_inference_pool,
)
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
from .checkpoint_state import (
    READABLE_SCHEMAS,
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
from .checkpoint_state import SCHEMA as CHECKPOINT_SCHEMA
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
    dataplane: dict[str, Any] | None = None


@dataclass
class _BeginWait:
    request: dict[str, Any]
    condition: threading.Condition
    notified: bool = False
    done: bool = False
    response: dict[str, Any] | None = None
    error: BaseException | None = None
    followers: int = 0


def _publisher_initialize() -> None:
    torch.set_num_threads(1)
    if torch.get_num_interop_threads() != 1:
        torch.set_num_interop_threads(1)


def _process_memory() -> dict[str, int]:
    # getrusage is process-local; children and their high-water marks are separate.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"pid": os.getpid(),
            "peak_rss_bytes": int(peak * (1 if sys.platform == "darwin" else 1024))}


def _first_use_policy_bias(batch: PreparedBatch, newly_used: list[int],
                          samples: list[dict[str, Any]], *, now: float,
                          version: int, updates: int) -> dict[str, Any]:
    """Mergeable observations of successful first-use multi-choice rows only."""
    policy_mask = batch.policy_mask.tolist()
    selected = {row for row in newly_used if policy_mask[row]}
    rows = []
    for sample in samples:
        if sample["row"] not in selected:
            continue
        metadata = batch.metadata[sample["row"]]
        rows.append({**sample,
            "age": (now - metadata["generated_timestamp"]
                    if metadata["generated_timestamp"] is not None else None),
            "versions": version - metadata["mu_version"],
            "SGD_updates": updates - metadata["mu_updates"]})
    ratio_edges = (0., .1, .25, .5, .75, .8, .9, 1., 1.1, 1.2, 1.25, 1.5, 2., 4., 8.)
    kl_edges = (0., 1e-6, 1e-5, 1e-4, 1e-3, .01, .03, .1, .3, 1.)

    def distribution(items: list[dict[str, Any]], key: str,
                     edges: tuple[float, ...]) -> dict[str, Any]:
        values = [float(row[key]) for row in items if row[key] is not None
                  and math.isfinite(row[key])]
        counts = [0] * (len(edges) + 1)
        for value in values:
            index = next((i for i, edge in enumerate(edges) if value < edge), len(edges))
            counts[index] += 1
        return {"finite_n": len(values), "nonfinite_n": len(items) - len(values),
                "sum": sum(values), "histogram": counts}

    def summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {"n": len(items),
            "kl_mu_theta": distribution(items, "kl_mu_theta", kl_edges),
            "ratio": distribution(items, "ratio", ratio_edges),
            **{key + "_n": sum(bool(row[key]) for row in items)
               for key in ("outside_clip", "ppo_objective_clipped", "ratio_above_one")}}

    def bins(key: str, edges: tuple[float, ...]) -> list[dict[str, Any]]:
        bounds = (None, *edges, None)
        return [{"lower_inclusive": lower, "upper_exclusive": upper,
                 **summary([row for row in rows if row[key] is not None
                    and (lower is None or row[key] >= lower)
                    and (upper is None or row[key] < upper)])}
                for lower, upper in zip(bounds[:-1], bounds[1:], strict=True)]

    return {"scope": "successful_first_use_multi", "observed_at": now,
        "age_reference": "optimizer_attempt_start", "pre_update_SGD": updates,
        "published_mu_version": version,
        "expected_n": len(selected), "coverage_complete": len(rows) == len(selected),
        "missing_age_n": sum(row["age"] is None for row in rows),
        "negative_age_n": sum(row["age"] is not None and row["age"] < 0 for row in rows),
        "ratio_histogram_edges": ratio_edges, "kl_histogram_edges": kl_edges,
        "histogram_rule": "bins split at each edge; equality belongs to its right bin",
        "ratio_above_one_scope": "counterfactual rho_bar=1 threshold; no V-trace applied",
        "all": summary(rows), "age_seconds": bins("age", (0., 5., 15., 30., 60., 120.)),
        "version_lag": bins("versions", (0., 1., 2., 4., 8., 16.)),
        "pre_update_SGD_lag": bins("SGD_updates", (0., 1., 2., 4., 8., 16.))}


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
        "operating_budget_parent",
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


def bind_dataplane_capacity(config: LearnerConfig, lanes: int) -> LearnerConfig:
    """Operational row credit for the explicit binary deployment profile."""
    settings = config.segments
    if settings is None or type(lanes) is not int or not 1 <= lanes <= 4096:
        raise ValueError("invalid_binary_dataplane_lanes")
    capacity = lanes * settings.steps + 4 * settings.dataset_rows
    if settings.capacity_limit_rows is not None and settings.capacity_limit_rows < capacity:
        raise ValueError("binary_dataplane_capacity_limit_below_lane_requirement")
    return replace(config, segments=replace(settings,
        capacity_rows=max(capacity, settings.capacity_rows)))


class SegmentLearner:
    def __init__(self, config: LearnerConfig, work: Path, io: SegmentLearnerIO) -> None:
        if config.segments is None or config.anchor is not None or config.critic is not None:
            raise BoundaryError("rl_learner", "explicit_segment_config_without_legacy_objectives")
        settings = config.segments
        self.dataplane = copy.deepcopy(io.dataplane)
        self.fd_limits = None
        if self.dataplane is not None:
            from spireagent.rl_socket_transport import ensure_fd_capacity

            config = bind_dataplane_capacity(config, self.dataplane["lanes"])
            assert config.segments is not None
            settings = config.segments
            self.fd_limits = ensure_fd_capacity(self.dataplane["lanes"],
                source_slots=self.dataplane.get("source_slots", 8192),
                connections_per_lane=self.dataplane.get("connections_per_lane", 6))
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
        self.wal = None
        self.binary_sources: BinarySources
        self.wal_replication: WalReplication
        self.source_prefetch_changed = threading.Event()
        self.source_prefetch_stopping = threading.Event()
        self.source_prefetch_gate = threading.Lock()
        self.episode_ends: dict[str, threading.Event] = {}
        self.source_prefetch_thread: threading.Thread | None = None
        self.stop_fact: dict[str, Any] | None = None
        self.stop_epoch = 0
        self.training_discarded_rows = 0
        self.live_work = work
        self.generation = settings.training_generation or "g" + uuid.uuid4().hex
        if self.dataplane is not None:
            from spireagent.rl_socket_transport import require_local_scratch

            self.live_work = Path(self.dataplane.get("root") or (
                Path(tempfile.gettempdir()) / f"stpd-rl-{os.getuid()}" / config.run
                / self.generation))
            self.live_work.mkdir(parents=True, exist_ok=True)
            self.local_scratch_probe = require_local_scratch(self.live_work)
            # Publications/CPs retain the existing Store/archive owner, with its
            # active working set on this learner node rather than the shared peer.
            peer = {"kind": "local", "directory": str(self.live_work / "store")}
            peer["archive"] = config.peer.get("archive", config.peer)
            config = replace(config, peer=peer)
            self.config = config
        self.exchange = Exchange(config.peer, config.run)
        self.archive = None
        if self.exchange.stop_request(training_generation=self.generation) is not None:
            raise BoundaryError("rl_learner", "cannot_resume_a_permanently_stopped_run")
        self.deadline_at = None
        self.active_training_began: float | None = None
        self.active_training_seconds = 0.
        self.selection_deadline: float | None = None
        self.device = _device(config.device)
        torch.set_num_threads(config.threads)
        torch.manual_seed(config.seed)
        self.rng = random.Random(config.seed)
        self.queue = SegmentQueue(settings)
        self.input_gate = threading.RLock()
        from spireagent.local_diagnostics import TimedRLock

        self.control_lock: Any = (TimedRLock() if (self.dataplane or {}).get("lock_timing", False)
                             else threading.RLock())
        self.condition = self.queue.condition = threading.Condition(cast(Any, self.control_lock))
        self.capacity_waiters: dict[str, _BeginWait] = {}
        self.capacity_tickets = 0
        self.episode_load: dict[tuple[str, str], dict[str, int]] = {}
        self.gpu_lock = threading.RLock()
        self.terminal = AuxiliaryPool(settings.terminal_games, settings.terminal_uses)
        self.hp = AuxiliaryPool(settings.hp_battles, settings.hp_uses)
        self.lease_owners: dict[str, dict[str, Any]] = {}
        self.actor_episodes: dict[str, str] = {}
        self.retired_actors: set[str] = set()
        self.actor_allocation_pending: frozenset[str] = frozenset()
        self.sources: dict[str, dict[str, Any]] = {}
        self.hp_binary_members: dict[str, tuple[BatchMember, ...]] = {}
        self.hot_references: dict[tuple[str, str], HotPayloadRef | BinaryRef] = {}
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
        self.ahead_batches: deque[tuple[dict[str, Any], Future[PreparedBatch]]] = deque()
        self.prepared_batches = (self.dataplane.get("prepared_batches", 4)
                                 if self.dataplane is not None else 1)
        if type(self.prepared_batches) is not int or self.prepared_batches < 1:
            raise ValueError("positive_prepared_batches_required")
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
        cp_budget = restored.config.get("operating_budget") if restored is not None else None
        budget = copy.deepcopy(config.operating_budget if config.operating_budget is not None
                               else cp_budget)
        if budget is not None and (
            not isinstance(budget, dict)
            or set(budget) != {"id", "total_seconds", "consumed_seconds"}
            or not isinstance(budget["id"], str)
            or any(type(budget[k]) not in (int, float) or not math.isfinite(budget[k])
                   for k in ("total_seconds", "consumed_seconds"))
            or budget["consumed_seconds"] < 0 or not 0 < budget["total_seconds"] <= 72000
        ):
            raise ValueError("invalid_operating_budget")
        new_window = bool(cp_budget and budget and cp_budget["id"] != budget["id"])
        if new_window:
            assert budget is not None and cp_budget is not None
            expected_parent = {"checkpoint_artifact_id": config.resume_checkpoint,
                               "operating_budget": cp_budget, "deadline_at": cp_deadline}
            # consumed_seconds is the new window's own ledger: 0 for its first attempt,
            # or the seconds its earlier attempts charged when they died before the
            # window's first CP and this one re-enters from the same parent. It never
            # inherits the parent's seconds and can only shorten the new budget.
            if (config.operating_budget_parent != expected_parent
                    or config.deadline_at is None):
                raise ValueError("checkpoint_operating_budget_cannot_be_reset")
        if (not new_window and cp_deadline is not None
                and config.deadline_at not in {None, cp_deadline}):
            raise BoundaryError("rl_learner", "checkpoint_deadline_cannot_be_reset")
        if budget is not None and cp_budget is not None and not new_window:
            if any(budget[k] != cp_budget[k] for k in ("id", "total_seconds")):
                raise ValueError("checkpoint_operating_budget_cannot_be_reset")
            budget["consumed_seconds"] = max(budget["consumed_seconds"],
                                              cp_budget["consumed_seconds"])
        self.deadline_at = self.exchange.run_deadline(
            cp_deadline if cp_deadline is not None and not new_window else config.deadline_at,
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
        self.committed_selection_rng = self.rng.getstate()
        self.input_preparer: BatchPreparer | BinaryBatchPreparer
        preparation_options: dict[str, Any] = dict(
            microbatch_size=self.performance.microbatch_size,
            deduplicate_text=self.performance.deduplicate_text,
            scene_attention_area=self.performance.scene_attention_area,
            text_attention_area=self.performance.text_attention_area,
            excluded_seeds=frozenset(settings.excluded_seeds),
            gae_lambda=settings.gae_lambda,
        )
        if self.dataplane is not None:
            from spireagent.rl_local_wal import LocalControlWAL, WalReplication
            from spireagent.rl_socket_transport import BinarySources
            from stpd.rl.binary_preparation import BinaryBatchPreparer

            self.wal = LocalControlWAL(self.live_work / "control.sqlite", run=config.run,
                generation=self.generation,
                max_bytes=self.dataplane.get("wal_max_bytes", 4 * 1024**3))
            if self.wal.reopened:
                self.wal.close()
                raise ValueError("binary_owner_requires_fresh_generation_and_local_journal")
            from spireagent.storage.config import open_peer

            self.wal_replication = WalReplication(self.wal,
                open_peer(config.peer["archive"]).blobs)
            self.binary_sources = BinarySources(self.live_work / "sources", run=config.run,
                generation=self.generation,
                max_bytes=self.dataplane.get("source_bytes", 20 * 1024**3),
                max_slots=self.dataplane.get("source_slots", 8192),
                lock_timing=self.dataplane.get("lock_timing", False))
            self.exchange.event_sink = self._durable_event
            self.exchange.live_generation = self.generation
            self.exchange.stop_source = lambda _generation: self.stop_fact
            self.input_preparer = BinaryBatchPreparer(provider=self.binary_sources,
                vocabulary=self.model.vocabulary,
                workers=self.dataplane.get("preparation_workers", settings.preparation_workers),
                threads=self.dataplane.get("preparation_threads", settings.preparation_threads),
                assembly_workers=self.dataplane.get("assembly_workers", 2),
                prepared_batches=self.prepared_batches,
                label_workers=self.dataplane.get("label_workers", 1),
                max_prepared_bytes=settings.prepared_bytes,
                max_pinned_bytes=settings.prepared_bytes,
                scratch=self.live_work,
                source_slots=self.dataplane.get("prepare_slots", 2 * (self.prepared_batches + 2)),
                max_source_bytes=self.dataplane.get("prepare_source_bytes",
                    self.dataplane.get("source_bytes", 20 * 1024**3)
                    * (self.prepared_batches + 2) // 4),
                source_wait_seconds=self.dataplane.get("source_wait_seconds", 60),
                **preparation_options)
        else:
            self.input_preparer = BatchPreparer(
            self.model.vocabulary,
            workers=settings.preparation_workers,
            threads=settings.preparation_threads,
            peer=config.peer,
            run=config.run,
            scratch=work,
            reader=io.read_hot_segment,
            **preparation_options,
        )
        # Source reservation/copy may wait for arena credit. It must not sit on
        # the theta owner's path before an already-ready GPU minibatch.
        self.preparation_dispatch = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rl-prepare-dispatch")
        self.publisher = ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_publisher_initialize,
        )
        # Commit-time header checks decode ~7 MB of JSON per packet; in this
        # process that held the GIL ~0.2 s per commit and starved the GPU step.
        self.header_checks = ProcessPoolExecutor(
            max_workers=4, mp_context=multiprocessing.get_context("spawn"))
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
        self.pending_label_requests: dict[str, dict[str, Any]] = {}
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
            dataplane={"sources": self.binary_sources, "upload_check": self._check_upload,
                       "model_files": self._model_files, "on_failure": self._transport_failure}
                       if self.dataplane is not None else None,
        )
        if self.dataplane is not None:
            recipe = _resume_config(asdict(self.config))
            cast(SegmentInferencePool, self.pool).binary_profile = {"binary_recipe": {
                "producer": self.producer.to_dict(),
                "recipe_digest": hashlib.sha256(json_bytes(recipe)).hexdigest(),
                "use_and_seed_digest": hashlib.sha256(json_bytes({"training_split": "TRAIN",
                    "excluded_seeds": settings.excluded_seeds})).hexdigest(),
                "excluded_seeds": settings.excluded_seeds, "gae_lambda": settings.gae_lambda},
                "mu_pull_interval_seconds": self.dataplane.get("mu_pull_interval_seconds", 15),
                "mu_cache_bytes": self.dataplane.get("mu_cache_bytes", 512 * 1024**2)}
            (work / "dataplane-profile.json").write_bytes(json_bytes({
                **self.dataplane, "root": str(self.live_work),
                "capacity_rows": settings.capacity_rows,
                "episode_rows": settings.episode_rows, "fd_limits": self.fd_limits,
                "local_scratch_probe": self.local_scratch_probe}))
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
            if self.wal is not None:
                self.archive.start_capacity_monitor(settings.archive_poll_seconds,
                                                     on_change=self._notify_capacity)
            else:
                self.archive.start_capacity_monitor(settings.archive_poll_seconds)
        if self.wal is not None:
            self.source_prefetch_thread = threading.Thread(
                target=self._source_prefetch_loop, name="rl-source-prefetch", daemon=True)
            self.source_prefetch_thread.start()

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

    def _durable_event(self, event: dict[str, Any]) -> dict[str, Any]:
        assert self.wal is not None
        from spireagent.rl_exchange import EVENT_SCHEMA, _open

        event = _open(json_bytes(event), EVENT_SCHEMA)
        if (event.get("run") != self.config.run
                or event.get("training_generation") != self.generation):
            raise ValueError("lifecycle_event_owner_changed")
        try:
            fact = self.wal.append("event:" + event["record_sha256"], event, {}, kind="event")
        except BaseException:
            with self.condition:
                self.input_fenced = self.newlease_stopped = True
                self.condition.notify_all()
                self._notify_capacity(force=True)
            raise
        return {"sequence": fact["sequence"], "record_sha256": event["record_sha256"]}

    def _dataplane_health(self) -> dict[str, Any]:
        transport = cast(SegmentInferencePool, self.pool).transport
        assert transport is not None
        return transport.health()

    def _transport_failure(self, failure: dict[str, Any]) -> None:
        with self.condition:
            if failure["fatal"]:
                self.input_fenced = self.newlease_stopped = True
                self.condition.notify_all()
                self._notify_capacity(force=True)
        self.exchange.publish_event("failed" if failure["fatal"] else "transport_degraded",
            training_generation=self.generation,
            budget_id=(self.config.operating_budget or {}).get("id"),
            failure=failure)

    def _check_upload(self, envelope: dict[str, Any]) -> bool:
        # Byte credit is subordinate to an existing original lease/episode.
        with self.condition:
            recipe = cast(SegmentInferencePool, self.pool).binary_profile["binary_recipe"]
            if any(envelope.get(key) != recipe[key] for key in (
                    "producer", "recipe_digest", "use_and_seed_digest")):
                raise BoundaryError("rl_learner", "IDENTITY_MISMATCH")
            if self.input_fenced:
                raise BoundaryError("rl_learner", "OWNER_FENCED")
            if envelope.get("payload_kind", "ppo") == "ppo":
                owner = self.lease_owners.get(str(envelope.get("segment_id")))
                if owner is None or any(owner.get(k) != envelope.get(k)
                                        for k in ("actor", "episode", "mu")):
                    raise BoundaryError("rl_learner", "IDENTITY_MISMATCH")
            elif f"{envelope.get('actor')}:{envelope.get('episode')}" not in self.actor_episodes:
                raise BoundaryError("rl_learner", "IDENTITY_MISMATCH")
            return True  # original already-granted World/lease may drain after STOP

    def _discard_stopped_queue(self) -> None:
        """Terminal training discard; original accepted facts and World ledger stay intact."""
        assert self.wal is not None
        records = list(self.queue.pending)
        if not records:
            return
        disposition = {"run": self.config.run, "training_generation": self.generation,
            "operation": "training_discard", "reason": "STOPPED",
            "segments": [{"segment_id": r["segment_id"], "rows": r["rows"]} for r in records]}
        self._append_durable("discard-stop:" + hashlib.sha256(json_bytes(disposition)).hexdigest(),
                        disposition, {"discarded_rows": sum(r["rows"] for r in records)},
                        kind="disposition")
        for record in records:
            self._episode_rows(record["actor"], record["episode"], queued=-record["rows"])
            payload = self._member(record).payload
            owner = "ppo:" + record["segment_id"]
            if (payload.slot, owner) in self.hot_references:
                self._release(payload, owner)
            for member in self.hp_binary_members.pop(record["segment_id"], ()):
                self._release(member.payload, "labels:" + record["segment_id"])
        self.training_discarded_rows += sum(record["rows"] for record in records)
        self.queue.pending.clear()
        self.queue.rows = 0

    def _model_files(self, key: str | None, node: str, cached: list[str]) -> tuple[
            dict[str, Any], list[tuple[str, Path]], dict[str, Any]]:
        if not isinstance(node, str) or not 0 < len(node) <= 256:
            raise ValueError("node_identity_required_for_mu_cache")
        with self.condition:
            identity = copy.deepcopy(self.pool.snapshots[key or self.pool.latest])
            if not isinstance(cached, list) or len(cached) > 2048:
                raise ValueError("invalid_node_cache_snapshot_list")
            cast(SegmentInferencePool, self.pool).node_caches[node] = (
                set(cached) & self.pool.snapshots.keys()) | {identity["snapshot_sha256"]}
        root = self.live_work / "mu-network" / identity["snapshot_sha256"]
        root.mkdir(parents=True, exist_ok=True)
        if not (root / "COMPLETE").exists():
            model = self.exchange.store.get_manifest(identity["model_artifact_id"])
            heads = self.exchange.store.get_manifest(identity["heads_artifact_id"])
            for name, payload in (("model.tensor-tree", model.payload("weights")),
                                  ("model.json", model.payload("package_manifest")),
                                  ("heads.tensor-tree", heads.payload("checkpoint"))):
                self.exchange.store.materialize(payload, root / name)
            (root / "model-manifest.json").write_bytes(model.to_bytes())
            (root / "heads-manifest.json").write_bytes(heads.to_bytes())
            (root / "COMPLETE").write_text(identity["snapshot_sha256"])
        publication = self.exchange.model(identity["version"],
                                           training_generation=self.generation)
        return identity, [(name, root / name) for name in (
            "model.tensor-tree", "model.json", "heads.tensor-tree",
            "model-manifest.json", "heads-manifest.json")], publication

    def _read_checkpoint(self, artifact_id: str) -> SelfContainedCheckpoint:
        manifest = self.exchange.store.get_manifest(artifact_id)
        if (
            manifest.kind != "checkpoint"
            or manifest.parameters.value().get("schema") not in READABLE_SCHEMAS
        ):
            raise ValueError("self_contained_checkpoint_required_convert_CP16_explicitly")
        # This is the entire restore IO: one manifest and its own payload.
        checkpoint = decode_state(
            self.exchange.store.bytes(
                manifest.payload("checkpoint"), maximum=manifest.payload("checkpoint").size
            ), expected_schema=manifest.parameters.value()["schema"],
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
                work=str(self.live_work),
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
        if self.wal is not None:
            from spireagent.rl_exchange import input_key

            receipt = self.wal.receipt(input_key(request), request)
            return receipt["response"] if receipt["found"] else None
        legacy_receipt = self.exchange.input_receipt(request)
        return legacy_receipt["response"] if legacy_receipt is not None else None

    def _append_durable(
        self, key: str, request: dict[str, Any], response: dict[str, Any], *, kind: str = "input"
    ) -> dict[str, Any]:
        """Called under input_gate then condition; release only the control lock.

        The mutation gate spans validation -> durable fact -> apply. Training,
        receipt/status and uploads keep running while the writer commits. No ACK
        or committed input cursor is published before FULL durability.
        """
        assert self.wal is not None
        future = self.wal.append_async(key, request, response, kind=kind)
        def completed(_future: Future) -> None:
            with self.condition:
                self.condition.notify_all()
        future.add_done_callback(completed)
        while not future.done():
            self.condition.wait()
        return future.result()

    def _accept(
        self, request: dict[str, Any], response: dict[str, Any], apply: Callable[[], None],
        *, receipt_checked: bool = False,
    ) -> dict[str, Any]:
        duplicate = None if receipt_checked else self._receipt(request)
        if duplicate is not None:
            return duplicate
        try:
            if self.wal is not None:
                from spireagent.rl_exchange import input_key

                fact = self._append_durable(input_key(request), request, response)
                record = {"sequence": fact["sequence"], "input_artifact_id": fact["hash"]}
            else:
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
            self._notify_capacity(force=True)
            raise
        self.condition.notify_all()
        return response

    def _episode_rows(self, actor: str, episode: str, *, queued: int = 0,
                      inflight: int = 0) -> None:
        if self.wal is None:
            return
        key = actor, episode
        load = self.episode_load.setdefault(key, {"queued": 0, "inflight": 0})
        load["queued"] += queued
        load["inflight"] += inflight
        if min(load.values()) < 0:
            raise ValueError("episode_row_accounting_changed")
        if not any(load.values()) and f"{actor}:{episode}" not in self.actor_episodes:
            del self.episode_load[key]

    def _notify_capacity(self, *, force: bool = False) -> None:
        """Wake only eligible capacity waiters, sharing the queue's owning lock.

        Notification tickets reserve wakeups, not leases or business rows. No
        receipt query or pending/lease scan runs in an awakened begin.
        """
        if self.wal is None:
            return
        with self.condition:
            if force or self.newlease_stopped or self.input_fenced:
                for wait in self.capacity_waiters.values():
                    wait.condition.notify_all()  # STOP/fence, or duplicate followers only
                return
            for wait in self.capacity_waiters.values():
                actor, episode = wait.request["actor"], wait.request["episode"]
                if actor in self.retired_actors or f"{actor}:{episode}" not in self.actor_episodes:
                    wait.condition.notify_all()
            available = ((self.settings.capacity_rows - self.queue.occupied) //
                         self.settings.steps - self.capacity_tickets)
            if available <= 0 or not self.archive_capacity()["ready"]:
                return
            for wait in self.capacity_waiters.values():
                if wait.notified or wait.done:
                    continue
                actor, episode = wait.request["actor"], wait.request["episode"]
                if actor in self.retired_actors or f"{actor}:{episode}" not in self.actor_episodes:
                    wait.condition.notify_all()
                    continue
                load = self.episode_load.get((actor, episode), {})
                if sum(load.values()) + self.settings.steps > self.settings.episode_rows:
                    continue
                wait.notified = True
                self.capacity_tickets += 1
                wait.condition.notify(1 + wait.followers)
                available -= 1
                if available == 0:
                    break

    def _begin_binary(self, request: dict[str, Any]) -> dict[str, Any]:
        from spireagent.rl_exchange import input_key

        key = input_key(request)
        with self.condition:
            previous = self.capacity_waiters.get(key)
            if previous is not None:
                previous.followers += 1
                try:
                    while not previous.done:
                        previous.condition.wait()
                finally:
                    previous.followers -= 1
                if previous.error is not None:
                    raise previous.error
                assert previous.response is not None
                return previous.response
            wait = _BeginWait(request, threading.Condition(self.control_lock))
            self.capacity_waiters[key] = wait
        actor, episode = request["actor"], request["episode"]

        def eligible() -> bool:
            load = self.episode_load.get((actor, episode), {})
            return (self.queue.occupied + (self.capacity_tickets + 1) * self.settings.steps
                <= self.settings.capacity_rows
                and sum(load.values()) + self.settings.steps <= self.settings.episode_rows
                and self.archive_capacity()["ready"])

        def stopped() -> bool:
            if self.input_fenced:
                raise BoundaryError("rl_learner", "OWNER_FENCED")
            return (self.newlease_stopped or actor in self.retired_actors
                or f"{actor}:{episode}" not in self.actor_episodes)

        try:
            while True:
                with self.condition:
                    if wait.notified:
                        wait.notified = False
                        self.capacity_tickets -= 1
                    if stopped():
                        wait.response = {"stopped": True}
                        break
                    if not eligible():
                        self._notify_capacity()
                        wait.condition.wait()
                        continue
                # Fixed order: input gate -> control -> short source metadata.
                # A capacity waiter never holds the gate or blocks input commits.
                with (self.input_gate if self.wal is not None else nullcontext()), self.condition:
                    if stopped():
                        wait.response = {"stopped": True}
                        break
                    if not eligible():
                        continue
                    prior = request.get("previous_lease")
                    if (prior is not None
                            and prior != f"{actor}/{self.queue.cursors.get(actor, -1)}"):
                        raise ValueError("segment_begin_predecessor_changed")
                    node, snapshot = request.get("node_id"), request.get("node_cached_mu_snapshot")
                    cache = cast(SegmentInferencePool, self.pool).node_caches
                    if (not isinstance(node, str) or not node
                            or snapshot not in cache.get(node, set())):
                        raise ValueError("cached_mu_publication_changed")
                    lease = f"{actor}/{self.queue.cursors.get(actor, -1) + 1}"
                    mu = copy.deepcopy(self.pool.snapshots[snapshot])
                    def grant(lease: str = lease, mu: dict[str, Any] = mu) -> None:
                        if not self.queue.reserve(lease):
                            raise ValueError("input_reservation_capacity_changed")
                        self.lease_owners[lease] = {"actor": actor, "episode": episode, "mu": mu}
                        self._episode_rows(actor, episode, inflight=self.settings.steps)
                    wait.response = self._accept(request, {
                        "run": self.config.run, "training_generation": self.generation,
                        "lease": lease, "mu": mu, "server_time": time.time()}, grant,
                        receipt_checked=True)
                    break
            assert wait.response is not None
            return wait.response
        except BaseException as error:
            wait.error = error
            raise
        finally:
            with self.condition:
                wait.done = True
                if wait.notified:
                    self.capacity_tickets -= 1
                    wait.notified = False
                wait.condition.notify_all()
                self.capacity_waiters.pop(key)
                self._notify_capacity()

    def control(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = request["operation"]
        if self.wal is not None:
            if operation == "stop":
                with (self.input_gate if self.wal is not None else nullcontext()), self.condition:
                    if request.get("run") != self.config.run or request.get(
                            "training_generation") != self.generation:
                        raise ValueError("stop_owner_identity_changed")
                    epoch = request.get("epoch")
                    if (type(epoch) is not int or epoch < 1
                            or not isinstance(request.get("reason"), str) or not request["reason"]):
                        raise ValueError("invalid_stop_epoch_or_reason")
                    original = {key: request[key] for key in (
                        "run", "training_generation", "epoch", "reason", "operation")}
                    key = "stop:" + hashlib.sha256(json_bytes(original)).hexdigest()
                    stop_receipt = self.wal.receipt(key, original)
                    if stop_receipt["found"]:
                        return cast(dict[str, Any], stop_receipt["response"])
                    response: dict[str, Any] = {"stopped": True,
                        "epoch": max(self.stop_epoch, epoch),
                        "original": self.stop_fact or original}
                    try:
                        self._append_durable(key, original, response, kind="stop")
                    except BaseException:
                        self.input_fenced = self.newlease_stopped = True
                        self.condition.notify_all()
                        raise
                    self.stop_fact = response["original"]
                    self.stop_epoch = response["epoch"]
                    self.newlease_stopped = True
                    self._discard_stopped_queue()
                    self.binary_sources.stop()
                    self.condition.notify_all()
                    self._notify_capacity(force=True)
                    return response
            if operation == "lifecycle_event":
                return self._durable_event(request["event"])
            if operation == "events_since":
                return {"events": self.wal.events_since(request.get("sequence", 0)),
                        "run": self.config.run, "training_generation": self.generation}
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
                **(self._dataplane_health() if self.wal is not None else {}),
                **({**self.wal.status(), **self.binary_sources.status(),
                    **self.wal_replication.status(),
                    "preparation": cast("BinaryBatchPreparer", self.input_preparer).summary(),
                    "training_discarded_rows": self.training_discarded_rows,
                    "stop_epoch": self.stop_epoch, "owner": self.pool.pool_id,
                    "control_lock_timing": (self.control_lock.snapshot()
                        if hasattr(self.control_lock, "snapshot") else [])}
                   if self.wal is not None else {}),
            }
        if operation == "receipt":
            if self.wal is not None:
                from spireagent.rl_exchange import input_key

                original = control_input(request["input"])
                return self.wal.receipt(input_key(original), original)
            # Old generation receipts remain facts; querying does not restore a
            # queue, re-grant a lease or retry a World action.
            previous_response = self._receipt(control_input(request["input"]))
            return {"found": previous_response is not None, "response": previous_response}
        if self.wal is not None and request.get("control_schema") != CONTROL_SCHEMA:
            raise BoundaryError("rl_learner", "SCHEMA_MISMATCH")
        request = control_input(request)
        if request.get("run") != self.config.run:
            return {
                "stopped": True,
                "reason": "generation_changed",
                "training_generation": self.generation,
            }
        with self.condition:
            if self.input_fenced:
                raise BoundaryError("rl_learner", "OWNER_FENCED" if self.wal is not None else
                                    "input_owner_fenced_restore_last_checkpoint")
            duplicate = self._receipt(request)
            if duplicate is not None:
                return duplicate
            if request.get("training_generation") != self.generation:
                return {
                    "stopped": True,
                    "reason": "generation_changed",
                    "training_generation": self.generation,
                }
        if operation == "begin" and self.wal is not None:
            return self._begin_binary(request)
        if operation == "commit":
            return self._admit(request)
        if operation == "episode_end":
            return self._end_episode_once_only(request)
        actor, episode = request["actor"], request["episode"]
        # The legacy begin sleeps on row capacity; it must release every gate
        # needed by its original commits. Binary begin has its own two phases.
        gate = self.input_gate if self.wal is not None else nullcontext()
        with gate, self.condition:
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
                        snapshot = request.get("node_cached_mu_snapshot", self.pool.latest)
                        if self.wal is not None:
                            node = request.get("node_id")
                            node_cache = cast(SegmentInferencePool, self.pool).node_caches
                            if (not isinstance(node, str) or not node
                                    or snapshot not in node_cache.get(node, set())):
                                raise ValueError("cached_mu_publication_changed")
                        mu = copy.deepcopy(self.pool.snapshots[snapshot])

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
                    self.condition.wait()
                return {"stopped": True}
        raise ValueError("unsupported_segment_control")

    @staticmethod
    def _member(record: dict[str, Any]) -> BatchMember:
        payload = record["payload"]
        return BatchMember(
            record["segment_id"],
            payload if isinstance(payload, (HotPayloadRef, BinaryRef)) else (
                BinaryRef(**payload) if "format" in payload else HotPayloadRef(**payload)),
            record["run_id"],
            record["rows"],
            record["mu"]["snapshot_sha256"],
            record["episode"],
        )

    def _retain(self, payload: HotPayloadRef | BinaryRef, owner: str) -> None:
        with self.condition:
            if isinstance(payload, BinaryRef):
                self.binary_sources.retain(payload, owner)
            else:
                self.exchange.retain_hot_payload(payload, owner)
            self.hot_references[payload.slot, owner] = payload

    def _release(self, payload: HotPayloadRef | BinaryRef, owner: str) -> None:
        with self.condition:
            if isinstance(payload, BinaryRef):
                self.binary_sources.release(payload, owner)
            else:
                self.exchange.release_hot_payload(payload, owner)
            del self.hot_references[payload.slot, owner]

    def _admit(self, request: dict[str, Any]) -> dict[str, Any]:
        # ACK checks only the immutable envelope/capacity. W3 verifies the source;
        # workers perform complete read/decoding/target checks before ready.
        if self.wal is not None:
            from spireagent.rl_socket_transport import binary_ref

            ref = binary_ref(request["payload"])
            header = self._packet_header_fields(ref, ("envelope",))
            if {**header["envelope"], "packet_size": ref.size,
                    "packet_sha256": ref.sha256} != request.get("envelope"):
                raise BoundaryError("rl_learner", "IDENTITY_MISMATCH")
        auxiliary_members: tuple[BatchMember, ...] = ()
        if self.wal is not None and request.get("binary_aux"):
            with self.condition:
                owner_snapshot = copy.deepcopy(self.lease_owners.get(request["segment_id"]))
                if owner_snapshot is None:
                    duplicate = self._receipt(request)
                    if duplicate is not None:
                        return duplicate
                    raise ValueError("segment_reserved_behavior_changed")
                frozen = {**request, "actor": owner_snapshot["actor"],
                          "episode": owner_snapshot["episode"]}
                originals = (*self._members_for_run(request["run_id"]), self._member(frozen))
            auxiliary_members = self._binary_auxiliary_members(frozen, originals)
        with (self.input_gate if self.wal is not None else nullcontext()), self.condition:
            duplicate = self._receipt(request)
            if duplicate is not None:
                return duplicate
            owner = self.lease_owners.get(request["segment_id"])
            if owner is None or owner["mu"] != request["mu"]:
                raise ValueError("segment_reserved_behavior_changed")
            record = {**request, "episode": owner["episode"], "actor": owner["actor"]}
            member = self._member(record)
            if isinstance(member.payload, BinaryRef):
                envelope = self.binary_sources.envelope(member.payload)
                if (envelope != request.get("envelope")
                        or envelope.get("payload_kind", "ppo") != "ppo"
                        or any(envelope.get(k) != record[k] for k in (
                            "run", "training_generation", "actor", "episode", "segment_id",
                            "run_id", "rows", "mu"))
                        or envelope["compiler_identity"] != self.compiler_identity):
                    raise ValueError("binary_commit_envelope_changed")
            if member.payload.slot.split("/")[:2] != [self.config.run, self.generation]:
                raise ValueError("segment_payload_generation_changed")
            self.queue.validate_commit(member.segment_id, record)
            reserved_rows = self.queue.leases[member.segment_id]
            # Explicit owners, acquired before ACK; an uncertain write keeps refs
            # and fences the owner rather than deleting/retrying unknown delivery.
            self._retain(member.payload, "ppo:" + member.segment_id)
            if not isinstance(member.payload, BinaryRef):
                self._retain(member.payload, "delayed:" + owner["episode"])

            if auxiliary_members:
                for aux in auxiliary_members:
                    self._retain(aux.payload, "labels:" + member.segment_id)

            def admit() -> None:
                if self.wal is not None and self.newlease_stopped:
                    self.queue.release(member.segment_id)
                    self.training_discarded_rows += member.rows
                else:
                    self.queue.commit(member.segment_id, record)
                self.sources[member.segment_id] = record
                self.lease_owners.pop(member.segment_id)
                self._episode_rows(owner["actor"], owner["episode"],
                    inflight=-reserved_rows, queued=0 if self.newlease_stopped else member.rows)
                if reserved_rows > (0 if self.newlease_stopped else member.rows):
                    self._notify_capacity()
                if auxiliary_members:
                    self.hp_binary_members[member.segment_id] = auxiliary_members

            discarded = self.wal is not None and self.newlease_stopped
            result = self._accept(request, {"segment_id": member.segment_id,
                **({"training_discarded": True, "reason": "STOPPED"} if discarded else {})}, admit)
            if isinstance(member.payload, BinaryRef):
                self.binary_sources.release(member.payload, "upload")
                for aux in auxiliary_members:
                    assert isinstance(aux.payload, BinaryRef)
                    self.binary_sources.release(aux.payload, "upload")
                if discarded:
                    self._release(member.payload, "ppo:" + member.segment_id)
                    for aux in self.hp_binary_members.pop(member.segment_id, ()):
                        self._release(aux.payload, "labels:" + member.segment_id)
                else:
                    self.source_prefetch_changed.set()
            return result

    def _packet_header_fields(self, ref: BinaryRef, fields: tuple[str, ...]) -> dict[str, Any]:
        from stpd.rl.tensor_segment import packet_header_fields

        # The borrow keeps the hash-checked immutable spool file alive while a
        # worker parses it; this thread waits without holding the GIL.
        with self.binary_sources.borrow(ref):
            path = self.binary_sources.path(ref)
            return self.header_checks.submit(
                packet_header_fields, str(path), ref.size, fields).result()

    def _binary_auxiliary_members(self, request: dict[str, Any],
                                  originals: tuple[BatchMember, ...]) -> tuple[BatchMember, ...]:
        from spireagent.rl_socket_transport import binary_ref

        by_segment = {m.segment_id: m for m in originals}
        result = []
        seen = set()
        for item in request.get("binary_aux", []):
            ref = binary_ref(item["payload"])
            envelope = self.binary_sources.envelope(ref)
            if (envelope != item["envelope"] or envelope.get("payload_kind") != "aux"
                    or any(envelope.get(k) != request[k] for k in (
                        "run", "training_generation", "actor", "episode", "run_id"))
                    or item["segment_id"] in seen or item["segment_id"] not in by_segment):
                raise ValueError("binary_auxiliary_owner_or_envelope_changed")
            header = self._packet_header_fields(ref, ("envelope", "witness"))
            original = by_segment[item["segment_id"]]
            witness = header["witness"]
            if ({**header["envelope"], "packet_size": ref.size,
                    "packet_sha256": ref.sha256} != envelope
                    or witness["segment_id"] != original.segment_id
                    or witness["run_id"] != original.run_id
                    or witness["training_generation"] != self.generation
                    or header["witness"]["mu"]["snapshot_sha256"] != original.mu_snapshot_sha256
                    or header["witness"]["rows"] != original.rows):
                raise ValueError("binary_auxiliary_source_binding_changed")
            member = replace(original, payload=ref)
            result.append(member)
            seen.add(item["segment_id"])
        return tuple(result)

    def _prefetch_members(self, members: tuple[BatchMember, ...], *, wait: bool = True) -> None:
        if self.wal is not None:
            for member in members:
                assert isinstance(member.payload, BinaryRef)
                cast("BinaryBatchPreparer", self.input_preparer).prefetch(member.payload,
                    self.binary_sources.envelope(member.payload), wait=wait)

    def _source_prefetch_loop(self) -> None:
        """Coalesce commit hints; copy/decode sources before numerical selection.

        A normal admitted queue already enforces the per-episode row limit, so
        its preparation window is a FIFO prefix. This only reads that prefix;
        it never freezes a request, samples auxiliary rows or advances RNG.
        """
        while True:
            self.source_prefetch_changed.wait()
            self.source_prefetch_changed.clear()
            if self.source_prefetch_stopping.is_set():
                return
            held: list[BatchMember] = []
            try:
                # Read this cache hint before taking the control lock. A later
                # eviction can lose a hint, never change the authoritative
                # consumer's reservation/read. Cached prefixes need no repeated
                # provider retain/envelope/release on every incoming commit.
                cached = cast("BinaryBatchPreparer", self.input_preparer).cached_sources()
                with self.condition:
                    if self.newlease_stopped or self.input_fenced:
                        continue
                    rows = 0
                    for record in self.queue.pending:
                        member = self._member(record)
                        if member.payload not in cached:
                            self._retain(member.payload, "source-prefetch")
                            held.append(member)
                        rows += member.rows
                        if rows >= (self.prepared_batches + 1) * self.settings.dataset_rows:
                            break
                # Provider borrow, hashing, arena copy and worker submission
                # hold neither the learner condition nor a socket handler.
                for member in held:
                    if self.source_prefetch_stopping.is_set():
                        break
                    try:
                        # A CP barrier temporarily fences the same preparer.
                        # Serialize only this source copy against that window;
                        # never acquire this gate under the learner condition.
                        with self.source_prefetch_gate:
                            self._prefetch_members((member,), wait=False)
                    except ValueError as error:
                        if str(error) != "binary_source_slots_busy":
                            raise
                        # Current/next requests still own every arena. Normal
                        # submission remains authoritative; retry on the next
                        # commit or dataset release, never on a timed spin.
                        break
            except BaseException as error:
                with self.condition:
                    self.input_fenced = self.newlease_stopped = True
                    self.input_failure = {"operation": "source_prefetch",
                        "code": "INVALID_PACKET", "error_type": type(error).__name__}
                    self.condition.notify_all()
                    self._notify_capacity(force=True)
                try:
                    self._transport_failure({"fatal": True, **self.input_failure})
                except BaseException as notification_error:
                    with self.condition:
                        self.input_failure["notification_error_type"] = type(
                            notification_error).__name__
                return
            finally:
                for member in held:
                    self._release(member.payload, "source-prefetch")

    def _members_for_run(self, run_id: str) -> tuple[BatchMember, ...]:
        with self.condition:
            return tuple(
                self._member(record)
                for record in self.sources.values()
                if record["run_id"] == run_id
            )

    def _end_episode_once_only(self, request: dict[str, Any]) -> dict[str, Any]:
        # The client's 120 s wait can expire while label validation runs; its
        # reconnect sees no receipt yet and resubmits. Run one original only:
        # the resubmission waits and answers from that original's receipt.
        key = hashlib.sha256(json_bytes(request)).hexdigest()
        with self.condition:
            running = self.episode_ends.get(key)
            if running is None:
                done = self.episode_ends[key] = threading.Event()
        if running is not None:
            running.wait()
            with self.condition:
                duplicate = self._receipt(request)
            return (duplicate if duplicate is not None
                    else self._end_episode_once_only(request))
        try:
            return self._end_episode(request)
        finally:
            with self.condition:
                del self.episode_ends[key]
            done.set()

    def _end_episode(self, request: dict[str, Any]) -> dict[str, Any]:
        with self.condition:
            if f"{request['actor']}:{request['episode']}" not in self.actor_episodes:
                raise ValueError("episode_end_without_open")
            originals = self._members_for_run(request["run_id"])
            request_hash = hashlib.sha256(json_bytes(request)).hexdigest()
        # No condition/GPU lock while source/label validation runs in a CPU worker.
        members = (self._binary_auxiliary_members(request, originals)
                   if self.wal is not None else originals)
        with self.source_prefetch_gate:
            self._prefetch_members(members)
            future = (cast("BinaryBatchPreparer", self.input_preparer).verify_labels(
                          request, members)
                      if self.wal is not None else
                      self.input_preparer.pool.submit(verify_auxiliary, request, members))
        groups = future.result()
        with (self.input_gate if self.wal is not None else nullcontext()), self.condition:
            duplicate = self._receipt(request)
            if duplicate is not None:
                return duplicate

            if (f"{request['actor']}:{request['episode']}" not in self.actor_episodes
                    or originals != self._members_for_run(request["run_id"])
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
                        self._episode_rows(owner["actor"], owner["episode"],
                                           inflight=-self.settings.steps)
                        self.lease_owners.pop(lease)
                self.actor_episodes.pop(f"{request['actor']}:{request['episode']}")
                self._episode_rows(request["actor"], request["episode"])
                self._notify_capacity()
                if self.wal is not None:
                    self.binary_sources.release_uploads(request["actor"], request["episode"])

            response = self._accept(request, {"run_id": request["run_id"]}, end)
            # A queued PPO segment can still carry HP labels for earlier rows
            # in this closed episode. _forget_locked releases delayed owners
            # only after all its PPO sources have handed off their label reads.
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
        with (self.input_gate if self.wal is not None else nullcontext()), self.condition:
            if actor in self.retired_actors:
                return {"actor": actor, "already_retired": True}
            episodes = [key.split(":", 1)[1] for key in self.actor_episodes
                        if key.startswith(actor + ":")]
            leases = [key for key, owner in self.lease_owners.items() if owner["actor"] == actor]
            for key in leases:
                self.queue.release(key)
                self._episode_rows(actor, self.lease_owners[key]["episode"],
                                   inflight=-self.settings.steps)
                self.lease_owners.pop(key)
            for episode in episodes:
                self.actor_episodes.pop(f"{actor}:{episode}")
            self.retired_actors.add(actor)
            if self.wal is not None:
                self.binary_sources.release_uploads(actor)
            result = {"actor": actor, "allocation": allocation,
                      "unresolved_episodes": episodes, "released_leases": leases,
                      "new_games": 0, "original_receipts_changed": False}
            self._forget_locked()
            self.condition.notify_all()
            self._notify_capacity()
        self._event("actor_retired", **result)
        return result

    def _add_auxiliary(self, groups: list[tuple[str, str, list[dict[str, Any]]]]) -> None:
        for kind, identity, rows in groups:
            pool = self.terminal if kind == "terminal" else self.hp
            pool.validate_add(identity, rows)
            before = {key: values for key, values in pool.groups.items()}
            owner = f"aux:{kind}:{identity}"
            for member in {row["member"].segment_id: row["member"] for row in rows}.values():
                # Binary labels already own their complete verified packet in
                # prepared_input. Keeping a second spool pin blocks uploads.
                if not isinstance(member.payload, BinaryRef):
                    self._retain(member.payload, owner)
            pool.add(identity, rows)
            for key, old in before.items():
                if key not in pool.groups:
                    for member in {row["member"].segment_id: row["member"] for row in old}.values():
                        if not isinstance(member.payload, BinaryRef):
                            self._release(member.payload, f"aux:{kind}:{key}")

    def _collect_labels(self, *, wait: bool = False) -> None:
        while self.pending_labels and (wait or self.pending_labels[0][0].done()):
            future, members, owner = self.pending_labels.popleft()
            groups = future.result()
            self.pending_label_requests.pop(owner, None)
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
            members = (self.hp_binary_members.pop(labels["segment_id"], ())
                       if self.wal is not None else self._members_for_run(labels["run_id"]))
            if self.wal is not None and not members:
                raise ValueError("binary_HP_tick_requires_bound_auxiliary_sources")
            owner = "labels:" + labels["segment_id"]
            self.pending_label_requests[owner] = copy.deepcopy(request)
            if self.wal is None:
                for member in members:
                    self._retain(member.payload, owner)
            def submit_labels(request: dict[str, Any] = request,
                              members: tuple[BatchMember, ...] = members) -> Future[Any]:
                self._prefetch_members(members)
                return (cast("BinaryBatchPreparer", self.input_preparer).verify_labels(
                         request, members)
                     if self.wal is not None
                     else self.input_preparer.pool.submit(verify_auxiliary, request, members))
            self.pending_labels.append(
                (
                    self._dispatch_preparation(submit_labels),
                    members,
                    owner,
                )
            )

    def _pending_batches(self) -> tuple[tuple[dict[str, Any], Future[PreparedBatch]], ...]:
        return ((self.pending_batch,) if self.pending_batch is not None else ()) + tuple(
            self.ahead_batches)

    def _select(self, *, take: bool = True) -> dict[str, Any] | None:
        with self.condition:
            if self.wal is not None and (self.newlease_stopped or self.input_fenced):
                return None
            pending = self._pending_batches()
            excluded = frozenset(record["segment_id"]
                for active, _ in pending for record in active["segments"])
            records = self.queue.dataset(take=take, exclude=excluded)
            if records is None:
                return None
            if take:
                self._dataset_released(records)
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
                for active in ((self.active,) if self.active else ()) + tuple(a for a, _ in pending)
                for row in active[name]
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
            number = self.datasets + int(self.active is not None) + len(pending) + 1
            request = PrepareRequest(
                RunSequence(self.config.run, self.generation, number),
                tuple(self._member(record) for record in records),
                tuple(auxiliary),
                parts,
                tuple(owners),
                self.compiler_identity,
                self.rng.getstate(),
                self.settings.prepared_bytes // (self.prepared_batches + 1),
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

    def _dataset_released(self, records: list[dict[str, Any]]) -> None:
        if self.wal is not None:
            for record in records:
                self._episode_rows(record["actor"], record["episode"], queued=-record["rows"])
            self._notify_capacity()
            self.source_prefetch_changed.set()

    def _dispatch_preparation(self, submit: Callable[[], Future[Any]]) -> Future[Any]:
        result: Future[Any] = Future()

        def complete(future: Future[Any]) -> None:
            try:
                result.set_result(future.result())
            except BaseException as error:
                result.set_exception(error)

        def dispatch() -> None:
            try:
                submit().add_done_callback(complete)
            except BaseException as error:
                result.set_exception(error)

        self.preparation_dispatch.submit(dispatch)
        return result

    def _submit_batch(self, active: dict[str, Any]) -> Future[PreparedBatch]:
        began = time.perf_counter()
        request = active["request"]
        owner = f"prepare:{self.generation}:{request.identity.sequence}"
        # Aux holds a complete verified row copy; only PPO still reads hot bytes.
        for member in request.members:
            self._retain(member.payload, owner)
        inputs = tuple(row["prepared_input"] for kind in ("terminal", "hp") for row in active[kind])
        scheduled = time.perf_counter()
        def submit() -> Future[PreparedBatch]:
            started = time.perf_counter()
            self._prefetch_members(request.members)
            prefetched = time.perf_counter()
            future = self.input_preparer.submit(request, inputs)
            submitted = time.perf_counter()

            def observe(finished: Future[PreparedBatch]) -> None:
                if not finished.cancelled() and finished.exception() is None:
                    finished.result().timings.update(
                        frontend_retain_seconds=scheduled - began,
                        frontend_dispatch_queue_seconds=started - scheduled,
                        frontend_prefetch_seconds=prefetched - started,
                        frontend_submit_seconds=submitted - prefetched,
                        frontend_wall_seconds=submitted - started)

            future.add_done_callback(observe)
            return future
        return self._dispatch_preparation(submit)

    def _prepare_ahead(self) -> None:
        while (self.settings.prepare_ahead
               and not self.newlease_stopped and not self.input_fenced
               and (self.selection_deadline is None
                    or time.monotonic() < self.selection_deadline)
               and len(self._pending_batches()) < self.prepared_batches):
            active = self._select(take=False)
            if active is None:
                break
            pending = active, self._submit_batch(active)
            if self.pending_batch is None:
                self.pending_batch = pending
            else:
                self.ahead_batches.append(pending)

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
        try:
            batch = future.result()
        except ValueError as error:
            if self.wal is None:
                raise
            with self.condition:
                self.input_fenced = self.newlease_stopped = True
                self.input_failure = {"operation": "prepare", "code": "INVALID_PACKET",
                    "producer_error_type": type(error).__name__}
                self._notify_capacity(force=True)
            raise BoundaryError("rl_prepare", "INVALID_PACKET") from error
        if self.wal is not None and self.newlease_stopped:
            return False
        self.timing["preparation_wait_seconds"] += time.perf_counter() - began
        timing_key = "binary_ipc_wall_seconds" if self.wal is not None else "request_wall_seconds"
        self.timing["preparation_seconds"] += batch.timings[timing_key]
        self.timing["prepared_batches"] += 1
        if not active["taken"]:
            with self.condition:
                if self.wal is not None and self.newlease_stopped:
                    return False
                if self.queue.dataset() != active["segments"]:
                    raise ValueError("prepared_dataset_membership_changed")
                self._dataset_released(active["segments"])
            active["taken"] = True
        self.pending_batch = self.ahead_batches.popleft() if self.ahead_batches else None
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
        policy_observed_at = time.time()
        policy_version, policy_updates = self.version, self.updates
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
            collect_policy_rows=True,
        )
        policy_samples = result.pop("_policy_rows", [])
        self.timing["optimizer_attempt_seconds"] += time.perf_counter() - began
        if result["stepped"]:
            newly_used = [i for i in batch.request.minibatch_rows[index] if i not in active["used"]]
            self.first_use += len(newly_used)
            policy_mask = batch.policy_mask.tolist()
            self.first_use_multi += sum(policy_mask[i] for i in newly_used)
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
                            if (member.segment_id not in remaining
                                    and not isinstance(member.payload, BinaryRef)):
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
                first_use_policy_bias=_first_use_policy_bias(
                    batch, newly_used, policy_samples, now=policy_observed_at,
                    version=policy_version, updates=policy_updates),
            )
        result.update(
            update=self.updates,
            lr=self.optimizer.param_groups[0]["lr"],
            head_lr=self.optimizer.param_groups[1]["lr"],
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
            if self.wal is not None:
                self.committed_selection_rng = batch.request.selection_rng_state
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
                        "lr",
                        "head_lr",
                    )
                },
                self.updates,
            )
        return result

    def _forget_unreferenced(self) -> None:
        with self.condition:
            self._forget_locked()

    def _forget_locked(self) -> None:
        waiting = {(record["actor"], record["episode"])
            for record in self.sources.values()
            if (self._member(record).payload.slot, "ppo:" + record["segment_id"])
            in self.hot_references}
        for record in self.sources.values():
            actor, episode = record["actor"], record["episode"]
            if f"{actor}:{episode}" not in self.actor_episodes and (actor, episode) not in waiting:
                payload, owner = self._member(record).payload, "delayed:" + episode
                if (payload.slot, owner) in self.hot_references:
                    self._release(payload, owner)
        retained = {record["segment_id"] for record in self.queue.pending}
        for active in (self.active, *(a for a, _ in self._pending_batches())):
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
        # Pending label workers own hot references even after episode close;
        # retain their source envelopes until those exact reads finish.
        referenced = {slot for slot, _owner in self.hot_references}
        retained.update(identity for identity, record in self.sources.items()
                        if self._member(record).payload.slot in referenced)
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
                self.committed_selection_rng if self.wal is not None else self.rng.getstate(),
                torch.get_rng_state().clone(),
                tuple(value.cpu().clone() for value in cuda),
            ),
            self.conversion,
            ({"generation": self.generation,
              "committed_selection_rng": self.committed_selection_rng,
              "speculative_selection_rng": self.rng.getstate(),
              "pending_requests": [asdict(active["request"])
                                   for active, _ in self._pending_batches()],
              "pending_labels": copy.deepcopy(self.pending_label_requests),
              "restore_disposition": "discard_hot_inputs_resume_committed_rng"}
             if self.wal is not None else None),
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
        if self.wal is not None and self.active is not None:
            raise BoundaryError("rl_learner", "binary_checkpoint_requires_dataset_boundary")
        if self.checkpoint_encoding is not None or self.checkpoints.pending:
            return
        # A CP snapshots committed learning state, not a generation transition.
        # Keep ready/in-flight requests and their speculative selection RNG live;
        # the CP records both states explicitly and never drains CPU workers.
        self.checkpoint_sequence += 1
        began = time.perf_counter()
        state = self._learning_state()
        self._event("checkpoint_captured", sequence=self.checkpoint_sequence,
                    capture_seconds=time.perf_counter() - began, memory=self._memory())
        self.checkpoint_encoding = self.checkpoint_encoder.submit(_encode_snapshot, state)
        self.checkpoint_requested = False
        if final:
            self._collect_checkpoint(wait=True)

    def _close_journal(self) -> None:
        if self.wal is not None and not self.wal.closed:
            with ExitStack() as cleanup:
                cleanup.callback(self.wal.close)
                cleanup.callback(self.wal_replication.close)
                # The listener is already closed. Preserve its terminal event
                # suffix from the same WAL for the operational owner's last read;
                # this single closeout write is outside the live control path.
                events: list[dict[str, Any]] = []
                sequence = 0
                while rows := self.wal.events_since(sequence):
                    events.extend(rows)
                    sequence = rows[-1]["sequence"]
                if events and events[-1]["request"]["kind"] in {"finished", "failed"}:
                    target = self.work / "control-wal-final.json"
                    temporary = target.with_suffix(".json.new")
                    with temporary.open("wb") as stream:
                        stream.write(json_bytes({"run": self.config.run,
                            "training_generation": self.generation, "events": events}))
                        stream.flush()
                        os.fsync(stream.fileno())
                    temporary.replace(target)

    def close(self, *, keep_journal: bool = False) -> None:
        with (self.input_gate if self.wal is not None else nullcontext()), self.condition:
            self.newlease_stopped = True
            self.condition.notify_all()
            if self.wal is not None:
                self._notify_capacity(force=True)
        if self.source_prefetch_thread is not None:
            self.source_prefetch_stopping.set()
            self.source_prefetch_changed.set()
            self.source_prefetch_thread.join()
        with ExitStack() as cleanup:
            if self.wal is not None and not keep_journal:
                cleanup.callback(self._close_journal)
            cleanup.callback(self.publisher.shutdown, wait=True, cancel_futures=True)
            cleanup.callback(self.header_checks.shutdown, wait=True, cancel_futures=True)
            cleanup.callback(self.telemetry.shutdown, wait=True, cancel_futures=False)
            cleanup.callback(self.checkpoint_encoder.shutdown, wait=True, cancel_futures=False)
            cleanup.callback(self.checkpoints.__exit__, RuntimeError if self.input_fenced else None)
            if self.archive is not None:
                cleanup.callback(self.archive.stop_capacity_monitor)
            try:
                self.pool.close()
            finally:
                try:
                    self.preparation_dispatch.shutdown(wait=True, cancel_futures=False)
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
            self.exchange.publish_event("finished", **summary)
            return summary
        except BaseException as error:
            fields: dict[str, Any] = {
                "training_generation": self.generation, "updates": self.updates,
                "budget_id": (self.config.operating_budget or {}).get("id"),
                "error_type": type(error).__name__, "error_code": getattr(error, "code", None),
                "diagnostic": exception_facts(error),
            }
            if getattr(self, "finalization_failure", None) is not None:
                fields["finalization_failure"] = self.finalization_failure
            try:
                self.exchange.publish_event("failed", **fields)
            except Exception as publication_error:
                (self.work / "failed-event-unpublished.json").write_bytes(json_bytes(
                    {**fields, "publication_error_type": type(publication_error).__name__}))
            raise
        finally:
            # Terminal events still use the normal durable WAL sink. The writer
            # and its replicator must outlive both success and failure events.
            self._close_journal()

    def _run(self, *, initialize_only: bool = False,
             on_tick: Callable[[], None] | None = None) -> dict[str, Any]:
        began = time.monotonic()
        self.active_training_began = began
        deadline = began + self.config.wall_seconds
        checkpoint_at = began + self.settings.checkpoint_seconds
        stop_at = began
        try:
            self._event("training_started", active_seconds=0, updates=self.updates)
            # Reserve one bounded dataset for normal shutdown. Once frozen, it
            # finishes even when the soft training window expires. The absolute
            # allocation deadline still owns the final 600-second drain reserve.
            finish_reserve = min(30.0, self.config.wall_seconds / 10)
            self.selection_deadline = deadline - finish_reserve
            if self.deadline_at is not None:
                self.selection_deadline = min(self.selection_deadline,
                    time.monotonic() + self.deadline_at - 600 - time.time() - finish_reserve)
            while (
                not initialize_only
                and not self.input_fenced
                and time.monotonic() < deadline
                and (self.deadline_at is None or time.time() < self.deadline_at - 600)
                and not (self.work / "STOP").exists()
                and self.stop_fact is None
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
                    if time.monotonic() >= self.selection_deadline:
                        break
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
            with (self.input_gate if self.wal is not None else nullcontext()), self.condition:
                self.newlease_stopped = True
                if self.wal is not None:
                    if not self.input_fenced:
                        self._discard_stopped_queue()
                    self.binary_sources.stop()
                    self._notify_capacity(force=True)
            # Finish only the frozen dataset within the original compute and
            # absolute budget. Stop never selects another dataset.
            if self.wal is not None and not self.input_fenced:
                while (self.active is not None
                        and (self.deadline_at is None or time.time() < self.deadline_at - 120)):
                    self._step()
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
                    and (self.queue.leases or self.actor_episodes
                         or self.actor_allocation_pending)
                    and time.monotonic() < drain
                ):
                    if on_tick is not None:
                        on_tick()
                    with self.condition:
                        self.condition.wait(0.2)
                if not self.input_fenced:
                    phase = "final_labels"
                    try:
                        self._collect_labels(wait=True)
                        phase = "final_model_publication"
                        if self.updates > self.published_updates:
                            self._schedule_publication()
                        self._collect_publication(wait=True)
                    except Exception as error:
                        self.finalization_failure = {
                            "stage": phase, "diagnostic": exception_facts(error)}
                        raise
                    finally:
                        # Complete SGD state is independent of the disposable mu
                        # export pool. Save before close even if publication fails.
                        self.checkpoint(final=True)
            finally:
                self.close(keep_journal=True)
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
            "learning_configuration": {"lr": self.optimizer.param_groups[0]["lr"],
                "head_lr": self.optimizer.param_groups[1]["lr"],
                "optimizer_param_groups": [
                    {key: value for key, value in group.items() if key != "params"}
                    for group in self.optimizer.param_groups],
                "weight_decay": self.config.weight_decay, "loss": asdict(self.config.loss),
                "segments": asdict(self.settings),
                "performance": self.config.frozen()["performance"]},
            "unresolved_inflight": len(self.queue.leases),
            "queued_rows": self.queue.rows,
            "unresolved_episodes": len(self.actor_episodes),
            "unresolved_actor_allocations": sorted(self.actor_allocation_pending),
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
        if self.actor_allocation_pending:
            raise BoundaryError("rl_learner", "unresolved_actor_allocations_after_drain")
        return summary
