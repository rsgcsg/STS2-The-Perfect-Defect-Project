"""RL4.1 learner: complete CPU preparation, batched SGD, background mu and CP.

One learning-state owner selects rows, charges successful uses and steps theta.
Independent CPU processes own all parameter-free preparation. The control server
holds identities only; actor-local mu inference is owned by the actor. CP-only
restore discards hot input state, never replays accepted actions or input logs.
"""

from __future__ import annotations

import bisect
import copy
import hashlib
import io as byte_io
import itertools
import json
import math
import multiprocessing
import operator
import os
import random
import resource
import sys
import tempfile
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from contextlib import ExitStack, contextmanager, nullcontext, suppress
from dataclasses import asdict, dataclass, replace
from functools import partial
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
from spireagent.rl_contracts import HotPayloadRef, RowRef, RunSequence
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
    ROW_BUCKET_ROWS,
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
from .fast_handoff import HandoffError
from .fast_segment import FORMAT as FAST_FORMAT
from .fast_source import (
    FastSegment,
    FastSegmentIntake,
    FastSources,
    IntakeDecision,
    RoutedSources,
    ack,
    read_handoff,
)
from .fast_verify import FastVerifyClosed, FastVerifyPool
from .learner import ROOT, LearnerConfig, _device
from .loss import prepared_update
from .performance import PerformanceConfig
from .prepared_batch import (
    AuxiliaryInput,
    AuxiliarySelection,
    BatchMember,
    PreparedBatch,
    PrepareRequest,
)
from .reward import REWARD_SPEC
from .segments import OPERATIONAL_FIELDS, AuxiliaryPool, SegmentQueue, minibatch_members
from .sgd_process import (
    INIT_SCHEMA,
    STATE_SCHEMA,
    WEIGHTS_SCHEMA,
    SgdProcess,
    SgdProcessFailed,
    compiled_defaults,
    memory_budget,
    read_tree,
    sgd_options,
    weights_digest,
    write_tree,
)

# The watcher's typed window-end stops (tools/friday/watch.py COMPLETED_WINDOW_STOPS).
WINDOW_END_STOPS = frozenset({"run_budget_complete", "absolute_deadline"})
# Events wait in the telemetry thread's queue, not on the controller. A full
# queue (telemetry behind by 1024 events) waits for the oldest event; no progress
# for TELEMETRY_STALL_SECONDS is a real stall and fails loudly. Nothing is dropped.
TELEMETRY_QUEUE_LIMIT = 1024
TELEMETRY_STALL_SECONDS = 600.0
MEMORY_SAMPLE_SECONDS = 5.0
# fast_chain actor control (learner owns the receiving side; see `fast_source`).
FAST_INSTANCE_ADMISSION = "fast_instance_admission"
FAST_INSTANCE_EXCLUDED = "fast_instance_excluded"


def window_end_drop(window_end: str | None, leases: dict[str, Any], episodes: dict[str, Any],
                    *, fenced: bool) -> dict[str, Any] | None:
    """RL5 OPS-2 (audit row 23): worlds still in flight after a deadline/budget drain are
    a normal window close: dropped and counted, never retried; their originals stay in the
    Store and the final CP is published before this. None = nothing to drop, or not a
    normal window end (the caller then fails closed with unresolved_worlds_after_drain)."""
    if fenced or window_end is None or not (leases or episodes):
        return None
    keys = sorted(episodes)
    return {"reason": window_end, "inflight_leases": len(leases), "episodes": len(keys),
            "episode_keys": keys[:1000], "retried": False}


def _pending_receipt(request: PrepareRequest) -> dict[str, Any]:
    """Bounded CP receipt of one in-flight prepare request.

    Restore never reads it (fresh generations discard hot inputs and re-select
    from the committed RNG), so the CP keeps the identity, sizes and the sha256
    of the whole canonical request instead of every member, row index and RNG
    word: its size no longer grows with each request, only with their count.
    """
    body = asdict(request)
    return {"identity": body["identity"], "members": len(request.members),
            "rows": sum(member.rows for member in request.members),
            "auxiliary": len(request.auxiliary),
            "minibatches": len(request.minibatch_rows), "epochs": request.epochs,
            "sha256": hashlib.sha256(json_bytes(body)).hexdigest()}


@dataclass(frozen=True)
class SegmentLearnerIO:
    # read_segment remains an explicit historical-reader dependency for application
    # composition. The live edge calls Exchange.read_hot_segment, never this reader.
    read_segment: Callable[..., dict[str, Any]]
    publish_model: Callable[..., Any]
    read_hot_segment: Callable[..., dict[str, Any]] | None = None
    dataplane: dict[str, Any] | None = None


class SnapshotFlights:
    """At most one mu materialization per snapshot at a time (single flight).

    Callers of the same snapshot queue on one lock; whoever holds it re-checks
    COMPLETE first, so only the first caller materializes. If that caller
    fails, it raises to its own requester and releases the lock; the next
    waiter then makes its own attempt (and raises its own error if the cause
    persists). Nobody waits forever, every failure is reported where it
    happens, and no failure state has to be shared. The entry is dropped once
    no caller uses it, so the map holds only snapshots being materialized.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._entries: dict[str, list[Any]] = {}

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._guard:
            entry = self._entries.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._guard:
                entry[1] -= 1
                if not entry[1]:
                    del self._entries[key]

    def __len__(self) -> int:
        with self._guard:
            return len(self._entries)


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
                          version: int, updates: int,
                          policy_mask: list[bool] | None = None) -> dict[str, Any]:
    """Mergeable observations of successful first-use multi-choice rows only.

    `policy_mask`: `batch.policy_mask.tolist()` computed once per dataset; only
    `batch.metadata` is read then."""
    if policy_mask is None:
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
        for value in values:  # the first edge above the value; equality goes right
            counts[bisect.bisect_right(edges, value)] += 1
        return {"finite_n": len(values), "nonfinite_n": len(items) - len(values),
                "sum": sum(values), "histogram": counts}

    def summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {"n": len(items),
            "kl_mu_theta": distribution(items, "kl_mu_theta", kl_edges),
            "ratio": distribution(items, "ratio", ratio_edges),
            **{key + "_n": sum(bool(row[key]) for row in items)
               for key in ("outside_clip", "ppo_objective_clipped", "ratio_above_one")}}

    def bins(key: str, edges: tuple[float, ...]) -> list[dict[str, Any]]:
        # One pass: each row joins the one bin [lower, upper) holding it, in row
        # order (same sums as filtering per bin); None and NaN join none.
        members: list[list[dict[str, Any]]] = [[] for _ in range(len(edges) + 1)]
        for row in rows:
            value = row[key]
            if value is not None and value == value:
                members[bisect.bisect_right(edges, value)].append(row)
        bounds = (None, *edges, None)
        return [{"lower_inclusive": lower, "upper_exclusive": upper, **summary(items)}
                for lower, upper, items in zip(bounds[:-1], bounds[1:], members, strict=True)]

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


def _first_use_fields(metadata: list[dict[str, Any]], policy_mask: list[bool],
                      newly_used: list[int], samples: list[dict[str, Any]], *, now: float,
                      version: int, updates: int, policy_observed_at: float,
                      policy_version: int, policy_updates: int) -> dict[str, Any]:
    """Progress-event first-use summaries from values captured at accounting time.

    Pure: the same inputs give the same event wherever it runs (the controller
    in process, the telemetry thread on the SGD-process path)."""
    rows = [metadata[i] for i in newly_used]
    ages = sorted(now - row["generated_timestamp"] for row in rows
                  if row["generated_timestamp"] is not None)
    return {
        "first_use_age_samples_seconds": ages,
        "first_use_age_seconds": {
            "n": len(ages),
            "median": ages[len(ages) // 2] if ages else None,
            "p95": ages[min(len(ages) - 1, int(len(ages) * 0.95))] if ages else None,
        },
        "first_use_floors": [row["floor"] for row in rows],
        "first_use_version_lag": [version - row["mu_version"] for row in rows],
        "first_use_pre_update_SGD_lag": [updates - 1 - row["mu_updates"] for row in rows],
        "first_use_policy_bias": _first_use_policy_bias(
            cast(Any, _BiasRows(metadata)), newly_used, samples, now=policy_observed_at,
            version=policy_version, updates=policy_updates, policy_mask=policy_mask),
    }


@dataclass(frozen=True)
class _BiasRows:
    metadata: list[dict[str, Any]]


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


def _publish_snapshot_file(snapshot: dict[str, Any], *, weights: dict[str, Any],
                           **publication: Any) -> dict[str, Any]:
    """SGD-process profile: the publisher reads the SGD-written weights file itself."""
    value = read_tree(weights, WEIGHTS_SCHEMA)
    if value["updates"] != publication["updates"]:
        raise ValueError("sgd_snapshot_updates_changed")
    return _publish_snapshot(pack_transport({**unpack_transport(snapshot),
                                             "weights": value["weights"]}), **publication)


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


def binary_preparer_arguments(dataplane: dict[str, Any], settings: Any,
                              prepared_batches: int) -> dict[str, Any]:
    """`BinaryBatchPreparer` sizes of one data plane (the learner's own, or one
    shard's with its share of the same `dataplane` keys)."""
    return dict(
        workers=dataplane.get("preparation_workers", settings.preparation_workers),
        threads=dataplane.get("preparation_threads", settings.preparation_threads),
        assembly_workers=dataplane.get("assembly_workers", 2),
        prepared_batches=prepared_batches,
        label_workers=dataplane.get("label_workers", 1),
        max_prepared_bytes=settings.prepared_bytes,
        max_pinned_bytes=settings.prepared_bytes,
        source_slots=dataplane.get("prepare_slots", 2 * (prepared_batches + 2)),
        max_source_bytes=dataplane.get("prepare_source_bytes",
            dataplane.get("source_bytes", 20 * 1024**3) * (prepared_batches + 2) // 4),
        source_wait_seconds=dataplane.get("source_wait_seconds", 60),
        persistent_pools=dataplane.get("persistent_pools", False),
        remote_prepare=dataplane.get("remote_prepare"))


class SegmentLearner:
    memory_sample: tuple[float, dict[str, Any]] | None
    policy_mask_cache: tuple[Any, list[bool]] | None  # (batch, mask list): per dataset
    def __init__(self, config: LearnerConfig, work: Path, io: SegmentLearnerIO) -> None:
        if config.segments is None or config.anchor is not None or config.critic is not None:
            raise BoundaryError("rl_learner", "explicit_segment_config_without_legacy_objectives")
        settings = config.segments
        self.dataplane = copy.deepcopy(io.dataplane)
        # `dataplane.shards` > 0: the fast data plane (hand-off receive, intake,
        # queue, eviction, selection, source copy, prepare dispatch, aux pools)
        # runs in that many shard processes (`dataplane_shards.py`); 0 = here.
        self.shards: Any = None
        self.shard_count = (self.dataplane or {}).get("shards", 0)
        if type(self.shard_count) is not int or not 0 <= self.shard_count <= 8:
            raise BoundaryError("rl_learner", "dataplane_shards_must_be_int_0_to_8")
        # Explicit, default off: continue a CP written without shards with shards.
        self.shards_adopt = (self.dataplane or {}).get(
            "shards_adopt_unsharded_checkpoint", False)
        if type(self.shards_adopt) is not bool or (self.shards_adopt and not self.shard_count):
            raise BoundaryError("rl_learner",
                                "dataplane_shards_adopt_switch_must_be_bool_with_shards")
        # Threads that copy batch sources into preparer arenas concurrently.
        self.dispatch_workers = (self.dataplane.get("prepare_dispatch_workers", 4)
                                 if self.dataplane is not None else 1)
        if type(self.dispatch_workers) is not int or self.dispatch_workers < 1:
            raise ValueError("invalid_prepare_dispatch_workers")
        # Opt-in, process-wide: a thread that gives up the GIL (lock wait, I/O)
        # waits up to one switch interval to get it back while hundreds of
        # learner threads are busy. None keeps the interpreter default (5 ms).
        # Process-global, set here before later constructor validation and
        # never restored: a failed construction can leave it changed.
        switch = (self.dataplane or {}).get("gil_switch_interval_seconds")
        if switch is not None:
            if type(switch) is not float or not 0.0001 <= switch <= 0.005:
                raise ValueError("invalid_gil_switch_interval_seconds")
            sys.setswitchinterval(switch)
        # Explicit S3 alternative only; None keeps the default in-process E4.
        self.sgd_options = sgd_options(self.dataplane)
        self.sgd: SgdProcess | None = None
        self.sgd_owns_theta = False
        self.sgd_snapshot_updates = -1  # updates of the last theta file the child handed over
        self.sgd_failure: dict[str, Any] | None = None
        if self.sgd_options is not None:
            if settings.central_scoring:
                raise BoundaryError("rl_learner", "sgd_process_requires_actor_local_mu")
            if torch.cuda.is_initialized():
                raise BoundaryError("rl_learner", "sgd_process_controller_has_CUDA_context")
        if (config.performance is not None and config.performance.prepared_forward != "padded-v1"
                and self.sgd_options is None):
            # The faster rows-v2 step is what the in-process GIL convoy eats (S0-GPU
            # I0: 7.15x), so it is selected only together with the SGD process.
            raise BoundaryError("rl_learner", "rows_v2_requires_sgd_process")
        if (self.sgd_options is not None and config.performance is not None
                and config.performance.prepared_compile):
            assert self.dataplane is not None
            self.sgd_options = compiled_defaults(self.sgd_options, self.dataplane)
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
        self.window_end: str | None = None
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
        # With an SGD process the controller holds theta on CPU only until the
        # child reports ready; the child alone validates and opens the device.
        self.device = (torch.device("cpu") if self.sgd_options is not None
                       else _device(config.device))
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
        self.mu_flights = SnapshotFlights()
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
        # fast_chain (WP8) only; None keeps every old-chain path unchanged.
        self.fast_intake: FastSegmentIntake | None = None
        self.fast_sources: FastSources | None = None
        self.fast_admitted_rows: OrderedDict[str, int] = OrderedDict()
        self.fast_counters: dict[str, int] = {}
        self.fast_receivers: list[threading.Thread] = []
        # `dataplane.fast_verify_workers` > 0: hand-off reads in verify processes.
        self.fast_verifier: FastVerifyPool | None = None
        self.input_sequence = 0
        self.input_head: str | None = None
        self.input_anchor = config.resume_checkpoint
        self.updates = self.datasets = self.version = self.checkpoint_sequence = 0
        self.published_updates = self.first_use = self.first_use_multi = 0
        self.active: dict[str, Any] | None = None
        self.active_batch: PreparedBatch | None = None
        # SGD process + `sgd_prestart`: the next dataset, taken and started while
        # the current one's answers are still accounted (at most one).
        self.next_active: tuple[dict[str, Any], Any] | None = None
        self.device_batch: PreparedBatch | None = None
        self.pending_batch: tuple[dict[str, Any], Future[PreparedBatch]] | None = None
        self.ahead_batches: deque[tuple[dict[str, Any], Future[PreparedBatch]]] = deque()
        self.prepared_batches = (self.dataplane.get("prepared_batches", 4)
                                 if self.dataplane is not None else 1)
        if type(self.prepared_batches) is not int or self.prepared_batches < 1:
            raise ValueError("positive_prepared_batches_required")
        # Keep the read-ahead window filled while the loop waits for the next
        # prepared batch (default off: the old loop only refills between steps).
        self.ahead_while_waiting = (self.dataplane or {}).get("ahead_while_waiting", False)
        if type(self.ahead_while_waiting) is not bool:
            raise ValueError("ahead_while_waiting_must_be_explicit")
        # The source prefetch pre-copies only segments a read-ahead batch already
        # holds (default off: the whole FIFO window). On a full fast queue the
        # oldest unreferenced segments, the window's tail, are the next capacity
        # victims: copying them is wasted hash/copy work and dead arena bytes.
        self.prefetch_referenced_only = (self.dataplane or {}).get(
            "prefetch_referenced_only", False)
        if type(self.prefetch_referenced_only) is not bool:
            raise ValueError("prefetch_referenced_only_must_be_explicit")
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
            "telemetry_full_wait_seconds": 0.0,
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
        # A CP names its shard count and every shard's committed selection RNG. A
        # restore keeps both: another count (a CP without shards counts as 0) is
        # refused, never answered with fresh or re-dealt RNG streams.
        self.shard_rng_states: list[tuple[Any, ...]] | None = None
        if restored is not None:
            recorded = (restored.input_pipeline or {}).get("dataplane_shards")
            if not recorded and self.shards_adopt:
                # The one explicit exception: a CP without shards. Each shard's RNG
                # follows from the CP's committed RNG and the shard index; the next
                # CP records the shards like any other.
                from .dataplane_shards import adopted_rng_state

                self.shard_rng_states = [
                    adopted_rng_state(restored.rng.learner_random, index)
                    for index in range(self.shard_count)]
            elif (recorded["count"] if recorded else 0) != self.shard_count:
                raise BoundaryError("rl_learner", "checkpoint_dataplane_shard_count_changed")
            if recorded:
                states = [shard["committed_selection_rng"] for shard in recorded["shards"]]
                if len(states) != self.shard_count:
                    raise BoundaryError("rl_learner", "checkpoint_dataplane_shard_rng_missing")
                self.shard_rng_states = [(state[0], tuple(state[1]), state[2])
                                         for state in states]
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
        if self.performance.prepared_forward != "padded-v1":
            # S3 rows-v2: preparation workers add the CPU row layout; the forward
            # refuses any group whose layout does not match this selection.
            preparation_options["prepared_layout"] = self.performance.prepared_forward
            if self.performance.prepared_bucket_rows != ROW_BUCKET_ROWS:
                # Only a non-default partition is passed: default options stay byte-equal.
                preparation_options["prepared_bucket_rows"] = (
                    self.performance.prepared_bucket_rows)
        if (self.sgd_options is not None and self.sgd_options.compile is not None
                and not self.performance.prepared_compile):
            raise BoundaryError("rl_learner", "sgd_compile_options_require_prepared_compile")
        if self.sgd_options is not None and self.sgd_options.data_parallel is not None:
            # WP8 same-update data parallel: no physical group crosses a rank's
            # equal share of a minibatch (the SGD ranks split by group).
            preparation_options["group_shards"] = self.sgd_options.data_parallel["world"]
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
            fast_chain = self.dataplane.get("fast_chain", False)
            if type(fast_chain) is not bool:
                raise BoundaryError("rl_learner", "fast_chain_switch_must_be_explicit_bool")
            provider: Any = self.binary_sources
            if fast_chain:
                from stpd.rl.fast_segment import MAX_ROWS

                # A sealed bootstrap segment is exactly MAX_ROWS rows; a learner
                # with a different trace length would mis-bind every segment.
                if settings.steps != MAX_ROWS or settings.capacity_rows < MAX_ROWS:
                    raise BoundaryError("rl_learner", "fast_chain_requires_steps_equal_max_rows")
                if self.shard_count:
                    if self.sgd_options is None:
                        # A prepared dataset reaches this process as a file hand-off
                        # only (never its tensors through a pipe).
                        raise BoundaryError("rl_learner", "dataplane_shards_require_sgd_process")
                    if (self.dataplane.get("fast_verify_workers", 0) != 0
                            or not settings.prepare_ahead):
                        # A shard reads hand-offs in its own process (no verify
                        # pool) and selects by read-ahead only.
                        raise BoundaryError(
                            "rl_learner", "dataplane_shards_need_prepare_ahead_and_no_verify_pool")
                else:
                    self.fast_sources = FastSources()
                # Every aux pool the loss weights (coef > 0) takes the fast labels
                # by default (lead ruling 11-10); an explicit list that leaves out
                # such a pool is refused, so its loss never silently becomes 0.
                weighted = [kind for kind, coef in (("terminal", config.loss.terminal_coef),
                                                    ("hp", config.loss.hp_coef)) if coef > 0]
                aux_labels = self.dataplane.get("fast_aux_labels", weighted)
                if (not isinstance(aux_labels, list)
                        or not set(aux_labels) <= {"terminal", "hp"}):
                    raise BoundaryError("rl_learner", "fast_aux_labels_must_list_terminal_or_hp")
                if not set(weighted) <= set(aux_labels):
                    raise BoundaryError("rl_learner",
                                        "fast_aux_labels_omit_a_pool_with_positive_coef")
                self.fast_aux_labels = sorted(aux_labels)
                if not self.shard_count:
                    self.fast_intake = FastSegmentIntake(
                        run=config.run, generation=self.generation,
                        excluded_seeds=frozenset(settings.excluded_seeds),
                        buffer_segments=self.dataplane.get("fast_buffer_segments", 512),
                        gae_lambda=settings.gae_lambda, aux_labels=frozenset(aux_labels))
                verify_workers = self.dataplane.get("fast_verify_workers", 0)
                if type(verify_workers) is not int or not 0 <= verify_workers <= 8:
                    raise BoundaryError("rl_learner", "fast_verify_workers_must_be_int_0_to_8")
                if verify_workers:
                    # Default arena: 64 x 2 MiB (a 64-row segment is ~1.25 MB; a
                    # labels message spans several contiguous slots).
                    self.fast_verifier = FastVerifyPool(
                        workers=verify_workers,
                        slots=self.dataplane.get("fast_verify_arena_slots", 64),
                        slot_bytes=self.dataplane.get("fast_verify_slot_bytes", 2 * 1024**2))
                self.fast_counters = {key: 0 for key in (
                    "admitted", "admitted_rows", "capacity_dropped", "capacity_dropped_rows",
                    # capacity_dropped = capacity_evicted (an older queued segment
                    # made room) + capacity_refused (no room: the new one dropped).
                    "capacity_evicted", "capacity_evicted_rows",
                    "capacity_refused", "capacity_refused_rows",
                    "excluded_instances", "excluded_dropped_rows", "excluded_used_rows",
                    "excluded_trained_rows", "excluded_prefetched_rows",
                    "excluded_admit_raced", "excluded_admit_raced_rows",
                    "excluded_aux_rows")}
                if self.fast_sources is not None:
                    provider = RoutedSources(self.binary_sources, self.fast_sources)
            if self.shard_count and not fast_chain:
                raise BoundaryError("rl_learner", "dataplane_shards_require_fast_chain")
            self.preparation_options = preparation_options
            if self.shard_count:
                # The shards own every preparation; this process keeps one small
                # idle preparer (no remote hosts, no warm pools) because status,
                # the SGD memory budget and close still read it.
                arguments = binary_preparer_arguments(
                    {**self.dataplane, "persistent_pools": False, "remote_prepare": None,
                     "preparation_workers": 1, "assembly_workers": 1, "label_workers": 1},
                    settings, self.prepared_batches)
            else:
                arguments = binary_preparer_arguments(self.dataplane, settings,
                                                      self.prepared_batches)
            self.input_preparer = BinaryBatchPreparer(provider=provider,
                vocabulary=self.model.vocabulary, scratch=self.live_work,
                handoff=self.sgd_options is not None, **arguments, **preparation_options)
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
        # Several dispatch threads copy different batches' sources at once (one
        # thread capped the chain at ~1/(per-batch copy) batch/s); each task's
        # preparer submission still runs alone and in dispatch order.
        self.preparation_dispatch = ThreadPoolExecutor(
            max_workers=self.dispatch_workers, thread_name_prefix="rl-prepare-dispatch")
        self.dispatch_order = threading.Condition()
        self.dispatch_issued = self.dispatch_turn = 0
        # SGD process only: a staged hand-off's release (preparer lock + unlink) runs
        # here, FIFO, not on the main thread. Every user of released state waits for
        # the queued releases first (`_await_releases`); failures surface at the next
        # main-loop check (`_check_releases`).
        self.handoff_releaser = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rl-handoff-release")
        self.handoff_releases: deque[Future[None]] = deque()
        self.handoff_release_lock = threading.Lock()
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
        self._init_progress_telemetry()
        self.checkpoint_encoder = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rl-cp-encode"
        )
        self.checkpoint_encoding: Future[FrozenCheckpoint] | None = None
        self.checkpoints = CheckpointPublisher(self.exchange.store, self.producer, background=True)
        self.checkpoint_requested = False
        self.pending_publication: Future[dict[str, Any]] | None = None
        self.pending_snapshot: dict[str, Any] | None = None
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
        if self.sgd_options is not None:
            # Before any listener or attachment: a refused budget or a failed
            # child start means this learner never accepts a connection.
            try:
                self._start_sgd_process(restored)
            except BaseException as error:
                self._abort_before_listener(error)
                raise
        try:
            if self.shard_count:
                from .dataplane_shards import ShardSet

                # Before any listener or attachment, like the SGD process: a shard
                # that fails to start means this learner accepts no connection.
                self.shards = ShardSet.start(self)
                if restored is not None and self.shards_adopt and not (
                        restored.input_pipeline or {}).get("dataplane_shards"):
                    self._event("dataplane_shards_adopted_unsharded_checkpoint",
                                shards=self.shard_count,
                                checkpoint_artifact_id=config.resume_checkpoint)
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
            if self.fast_intake is not None:
                # The start (or restored) mu is this learner's first publication.
                # Known limit: mu published by an earlier process is not carried
                # across a resume, so its in-flight segments are refused loudly.
                self.fast_intake.publish_mu(initial["identity"])
            if self.shards is not None:
                self.shards.publish_mu(initial["identity"])
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
                (Path(settings.rendezvous_path) if settings.rendezvous_path
                 else work / "inference.json"),
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
            if self.sgd is not None:
                # theta/Adam/EMA now exist only in the SGD process.
                del self.model, self.optimizer, self.ema
                self.sgd_owns_theta = True
        except BaseException:
            # A live SGD child must never outlive a learner that failed to come up
            # (it is non-daemon; interpreter exit would wait on it).
            if self.sgd is not None:
                self.sgd.kill()
                self.sgd.close()
            if self.shards is not None:
                self.shards.kill()
            raise

    # `input_fenced` / `newlease_stopped` stay plain attributes for every reader
    # and writer; the setters only add one thing: a False -> True change is passed
    # on to the data-plane shards (`dataplane.shards` > 0), which hold their own
    # copy of both flags. Without shards the hook does nothing.
    @property
    def input_fenced(self) -> bool:
        return self._input_fenced

    @input_fenced.setter
    def input_fenced(self, value: bool) -> None:
        changed = bool(value) and not getattr(self, "_input_fenced", False)
        self._input_fenced = value
        if changed:
            self._flags_changed()

    @property
    def newlease_stopped(self) -> bool:
        return self._newlease_stopped

    @newlease_stopped.setter
    def newlease_stopped(self, value: bool) -> None:
        changed = bool(value) and not getattr(self, "_newlease_stopped", False)
        self._newlease_stopped = value
        if changed:
            self._flags_changed()

    def _flags_changed(self) -> None:
        shards = getattr(self, "shards", None)
        if shards is not None:
            shards.flags(stopped=bool(getattr(self, "_newlease_stopped", False)),
                         fenced=bool(getattr(self, "_input_fenced", False)))

    def _shard_fenced(self, index: int, failure: dict[str, Any]) -> None:
        """A data-plane shard fenced itself, failed an operation or died: all input
        is fenced (every other shard too). Nothing is restarted or re-routed."""
        with self.condition:
            if not self.input_fenced:
                self.input_failure = {"operation": "dataplane_shard", "shard": index,
                                      "failure": failure}
            self.input_fenced = self.newlease_stopped = True
            self.condition.notify_all()
            self._notify_capacity(force=True)

    def _shard_ready(self) -> None:
        """Main thread: ask every shard to keep its read-ahead filled, and queue
        the datasets they announced as prepared, in arrival order. The dataset
        number is given here (the same count `_select` uses), so numbers stay
        gapless in take order over all shards."""
        assert self.shards is not None
        stopping = (self.newlease_stopped or self.input_fenced
                    or (self.selection_deadline is not None
                        and time.monotonic() >= self.selection_deadline))
        if not stopping:
            self.shards.ahead()
        for shard, local, batch in self.shards.drain_ready():
            with self.condition:
                number = (self.datasets + int(self.active is not None)
                          + int(self.next_active is not None)
                          + len(self._pending_batches()) + 1)
                if self.shards.count == 1 and number != local:
                    raise ValueError("dataplane_shard_dataset_number_changed")
                request = replace(batch.request, identity=RunSequence(
                    self.config.run, self.generation, number))
                self.shards.numbered(number, shard, local)
                future: Future[PreparedBatch] = Future()
                future.set_result(replace(batch, request=request))
                active = {"request": request, "segments": [], "terminal": [], "hp": [],
                          "used": [], "minibatch": 0, "epoch": 0,
                          "start_updates": self.updates, "taken": False,
                          "shard": shard, "shard_sequence": local}
                pending = active, future
                if self.pending_batch is None:
                    self.pending_batch = pending
                else:
                    self.ahead_batches.append(pending)
            if self.sgd is not None and self.sgd.options.stage_thread:
                pending[1].add_done_callback(self._sgd_prepared)
        if self.sgd is not None and not self.newlease_stopped and not self.input_fenced:
            self._sgd_plan_ahead()

    def _start_sgd_process(self, restored: SelfContainedCheckpoint | None) -> None:
        assert self.sgd_options is not None and self.wal is not None and self.dataplane
        options, settings = self.sgd_options, self.settings
        model_bytes = sum(value.numel() * value.element_size()
                          for value in self.model.state_dict().values())
        # Same retained-allocation cap as the in-process pinned slots (growth-only
        # per-dtype buffers across two slots); counted in full by the budget.
        pinned_bytes = settings.prepared_bytes
        preparer = cast("BinaryBatchPreparer", self.input_preparer)
        prepare_source_bytes, prepared_bytes = preparer.maximum, settings.prepared_bytes
        workers = preparer.workers + preparer.assembly_workers + preparer.label_workers + 4
        if self.shard_count:
            # The shards' preparers and processes, not this process's idle one.
            from .dataplane_shards import shard_budget

            prepare_source_bytes, prepared_bytes, shard_workers = shard_budget(
                self.dataplane, settings, self.prepared_batches, self.shard_count)
            workers += shard_workers
        budget = memory_budget(
            options=options, device=self.config.device, model_bytes=model_bytes,
            source_bytes=self.binary_sources.max_bytes,
            prepare_source_bytes=prepare_source_bytes, prepared_bytes=prepared_bytes,
            pinned_bytes=pinned_bytes,
            wal_bytes=self.dataplane.get("wal_max_bytes", 4 * 1024**3),
            workers=workers,
            paths={"sources": self.live_work / "sources", "arenas": self.live_work,
                   "wal": self.live_work / "control.sqlite"})
        (self.work / "sgd-memory-budget.json").write_bytes(json_bytes(budget))
        if not budget["fits"]:
            raise BoundaryError("rl_learner", "sgd_process_memory_budget_exceeded")
        root = self.live_work / "sgd"
        root.mkdir(parents=True, exist_ok=True)
        weights = snapshot_weights(self.model.state_dict())
        self.optimizer_names = self._optimizer_names()
        state = write_tree(root / "init.tree", {
            "schema": INIT_SCHEMA, "updates": self.updates, "datasets": self.datasets,
            "weights": weights, "optimizer": cpu_copy(self.optimizer.state_dict()),
            "optimizer_parameter_names": self.optimizer_names,
            "ema": snapshot_weights(self.ema),
            # Same point as the in-process owner: resumed RNG, or the seeded state.
            "torch_rng": (restored.rng.torch_rng if restored is not None
                          else torch.get_rng_state()).clone(),
            "cuda_rng": tuple(value.cpu().clone() for value in self.cuda_rng)})
        self.sgd = SgdProcess(run=self.config.run, generation=self.generation, root=root,
                              options=options, on_staged=self._sgd_staged)
        self.sgd_planned: dict[int, Any] = {}
        # Plan decisions (and the start that ends one) are made under this lock:
        # a plan can also be sent by the thread that completes a preparation.
        self.sgd_lock = threading.RLock()
        self.sgd_callback_error: BaseException | None = None
        # Main-thread time blocked on `staged` when a dataset is taken, and how
        # many datasets were already staged ahead at that point (SGD process only).
        self.timing.update(sgd_stage_wait_seconds=0.0, sgd_staged_ahead_batches=0.0,
                           sgd_prestarted_batches=0.0)
        self.sgd_rates = (self.optimizer.param_groups[0]["lr"],
                          self.optimizer.param_groups[1]["lr"])
        self.sgd_memory: dict[str, Any] | None = None
        ready = self.sgd.start({
            "schema": INIT_SCHEMA, "state": state, "root": str(root),
            "device": self.config.device, "threads": self.config.threads,
            "seed": self.config.seed, "cpus": list(options.cpus) if options.cpus else None,
            "model": {"vocabulary": self.model.vocabulary,
                      "execution": asdict(self.model.execution),
                      "model_size": self.model.model_size,
                      "architecture": self.model.architecture},
            "lr": self.config.lr, "head_lr": settings.head_lr,
            "weight_decay": self.config.weight_decay, "loss": asdict(self.config.loss),
            "performance": asdict(self.performance), "target_kl": settings.target_kl,
            "ema_decay": settings.ema_decay, "diagnostics_every": settings.diagnostics_every,
            "pinned_bytes": pinned_bytes, "offline_dump": self._offline_dump_init(),
            **({"data_parallel": dict(options.data_parallel)}
               if options.data_parallel is not None else {}),
            **({"compile": dict(options.compile)} if options.compile is not None else {}),
            **({"stage_thread": True} if options.stage_thread else {})})
        Path(state["path"]).unlink()
        if (ready["weights_sha256"] != weights_digest(weights)
                or ready["updates"] != self.updates or ready["datasets"] != self.datasets):
            raise self.sgd.violation("sgd_ready_state_changed")
        if torch.cuda.is_initialized():
            raise BoundaryError("rl_learner", "sgd_process_controller_has_CUDA_context")
        self.sgd_groups = ready["optimizer_param_groups"]
        self.sgd_memory = ready["memory"]
        # Preflight/limits/warm-up facts of `prepared_compile` (None when not compiled).
        self.sgd_compile = ready.get("compile")
        if options.data_parallel is not None and (
                not isinstance(ready.get("data_parallel"), list)
                or len(ready["data_parallel"]) != options.data_parallel["world"]):
            raise self.sgd.violation("sgd_data_parallel_ranks_not_ready")
        self._event("sgd_process_started", pid=ready["pid"], device=ready["device"],
                    **({"data_parallel": ready["data_parallel"]}
                       if options.data_parallel is not None else {}),
                    **({"compile": ready["compile"]} if "compile" in ready else {}),
                    gpu_uuid=ready["gpu_uuid"], gpu_name=ready["gpu_name"], cpus=ready["cpus"],
                    weights_sha256=ready["weights_sha256"], updates=ready["updates"],
                    memory_budget=budget, memory=ready["memory"])

    def _offline_dump_init(self) -> dict[str, Any] | None:
        """C-SCALE sampled offline-step files: only with `sgd_offline_dump_every`."""
        assert self.sgd_options is not None
        dump = self.sgd_options.offline_dump
        if dump is None:
            return None
        directory = Path(dump.directory) if dump.directory else self.work / "offline-steps"
        directory.mkdir(parents=True, exist_ok=True)
        return {"every": dump.every, "limit": dump.limit, "directory": str(directory),
                "construction": asdict(self.construction_template),
                "execution": asdict(self.model.execution)}

    def _abort_before_listener(self, error: BaseException) -> None:
        """A refused/failed SGD start: one failed event, then release what exists."""
        facts = (error.facts if isinstance(error, SgdProcessFailed)
                 else {"component": "sgd_process", "code": getattr(error, "code", None),
                       "error_type": type(error).__name__})
        self.sgd_failure = facts
        with ExitStack() as cleanup:
            cleanup.callback(self._close_journal)
            cleanup.callback(self.input_preparer.shutdown, wait=True, cancel_futures=True)
            cleanup.callback(self.handoff_releaser.shutdown, wait=True)
            cleanup.callback(self.preparation_dispatch.shutdown, wait=True)
            cleanup.callback(self.publisher.shutdown, wait=True, cancel_futures=True)
            cleanup.callback(self.header_checks.shutdown, wait=True, cancel_futures=True)
            cleanup.callback(self.checkpoint_encoder.shutdown, wait=True)
            cleanup.callback(self.telemetry.shutdown, wait=True)
            cleanup.callback(self.checkpoints.__exit__, RuntimeError)
            cleanup.callback(self.binary_sources.stop)
            if self.sgd is not None:
                cleanup.callback(self.sgd.close)
            # The derived init copy of theta/Adam never outlives a refused start.
            cleanup.callback((self.live_work / "sgd" / "init.tree").unlink, missing_ok=True)
            with suppress(Exception):
                self.exchange.publish_event(
                    "failed", training_generation=self.generation, updates=self.updates,
                    budget_id=(self.config.operating_budget or {}).get("id"),
                    error_type=type(error).__name__, error_code=getattr(error, "code", None),
                    diagnostic=exception_facts(error), sgd_failure=facts)

    def _sgd_staged(self, message: dict[str, Any]) -> None:
        # The SGD process owns pinned/device copies; the derived file goes now, on
        # the releaser thread (the preparer lock is contended; the unlink is IO).
        handoff = self.sgd_planned.pop(message["dataset"])
        if self.shards is not None:
            # The owning shard's preparer holds the derived file; it releases it
            # on its own releaser thread and reports a failure as a fence.
            self.shards.release(message["dataset"])
        else:
            release = cast("BinaryBatchPreparer", self.input_preparer).release_handoff
            with self.handoff_release_lock:
                self.handoff_releases.append(self.handoff_releaser.submit(release, handoff))
        self.timing["input_transfer_host_seconds"] += message["host_seconds"]
        self.timing["input_transfer_copies"] += message["copies"]

    def _check_releases(self, *, wait: bool = False) -> None:
        """Main thread: drop finished hand-off releases, raising the first failure
        (the run stops loudly); `wait` first lets every queued release finish."""
        while True:
            with self.handoff_release_lock:
                if not self.handoff_releases or not (
                        wait or self.handoff_releases[0].done()):
                    return
                future = self.handoff_releases[0]
            future.result()  # waits when `wait`; a failure stays queued and re-raises
            with self.handoff_release_lock:
                if self.handoff_releases and self.handoff_releases[0] is future:
                    self.handoff_releases.popleft()

    def _await_releases(self) -> None:
        """Any thread: return once every release queued so far has finished.

        Before a preparer submit, which counts not-yet-released hand-offs against
        its current-plus-next limit (the main thread used to release inline before
        that point). A failed release is not consumed here: the main thread raises
        the original error at its next check; this caller gets a RuntimeError (not
        ValueError, which the prepare path would classify as an invalid packet).
        """
        with self.handoff_release_lock:
            queued = tuple(self.handoff_releases)
        wait_futures(queued)
        if any(future.exception() is not None for future in queued):
            raise RuntimeError("binary_handoff_release_failed")

    def _sgd_plan(self, handoff: Any) -> None:
        assert self.sgd is not None
        sequence = handoff.request.identity.sequence
        with self.sgd_lock:
            if (sequence in self.sgd_planned or sequence in self.sgd.staged
                    or (self.sgd.started is not None and sequence <= self.sgd.started)):
                return
            self.sgd_planned[sequence] = handoff
            self.sgd.send("plan", dataset=sequence, path=handoff.path, tree=handoff.tree,
                          arrays=[(a.offset, a.count, a.dtype) for a in handoff.arrays],
                          used_bytes=handoff.used_bytes, sha256=handoff.sha256,
                          minibatch_count=handoff.minibatch_count,
                          epochs=handoff.request.epochs,
                          # The number its shard wrote into the file (the learner
                          # numbers datasets over all shards).
                          **({"bound": self.shards.numbers[sequence][1]}
                             if self.shards is not None else {}))

    def _sgd_plan_ahead(self) -> None:
        """At most one dataset beyond the started one is staged ahead in the SGD process."""
        assert self.sgd is not None
        if self.sgd_callback_error is not None:
            raise self.sgd_callback_error
        with self.sgd_lock:
            # A prestarted next dataset counts as the one staged ahead: the child
            # holds at most the current and the next dataset on its device.
            if self.sgd.planned or self.pending_batch is None or self.next_active is not None:
                return
            future = self.pending_batch[1]
            if future.done() and not future.cancelled() and future.exception() is None:
                self._sgd_plan(future.result())

    def _sgd_prepared(self, future: Future[PreparedBatch]) -> None:
        """Preparation-completion thread: plan the next dataset the moment it is
        prepared, instead of at the next minibatch answer, so its staging starts
        while the current dataset still trains. Only the one dataset right after
        the started, active one is planned here (same order, same bound as
        `_sgd_plan_ahead`); anything else is left to the controller thread."""
        if future.cancelled() or future.exception() is not None:
            return  # the controller thread reads this failure when it takes it
        try:
            assert self.sgd is not None
            with self.sgd_lock:
                pending, active = self.pending_batch, self.active_batch
                if (self.newlease_stopped or self.input_fenced or self.sgd.failure is not None
                        or self.sgd.planned or pending is None or pending[1] is not future
                        or self.next_active is not None or active is None
                        or active.request.identity.sequence != self.sgd.started):
                    return
                self._sgd_plan(future.result())
        except SgdProcessFailed:
            return  # kept on `self.sgd.failure`; the controller thread raises it
        except BaseException as error:  # never lost in a callback: raised by the controller
            self.sgd_callback_error = error

    def _sgd_fence(self, failure: SgdProcessFailed) -> None:
        with self.condition:
            self.input_fenced = self.newlease_stopped = True
            self.sgd_failure = failure.facts
            self.input_failure = {"operation": "sgd_process", **failure.facts}
            self.condition.notify_all()
            self._notify_capacity(force=True)
        assert self.sgd is not None
        self.sgd.kill()

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
            if not record.get("fast"):
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

    def _discard_stopped_shards(self) -> None:
        """The same terminal training discard for what the shards still queue: one
        durable disposition here (the shards journal nothing), bytes released there."""
        records = self.shards.discard_stopped()
        if not records:
            return
        disposition = {"run": self.config.run, "training_generation": self.generation,
            "operation": "training_discard", "reason": "STOPPED", "segments": records}
        self._append_durable("discard-stop:" + hashlib.sha256(json_bytes(disposition)).hexdigest(),
                        disposition, {"discarded_rows": sum(r["rows"] for r in records)},
                        kind="disposition")

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
            # Every inference service asks for a new mu at once: only the first
            # materializes (and hashes); the others wait, then find COMPLETE.
            # self.condition is not held here.
            with self.mu_flights.hold(identity["snapshot_sha256"]):
                if not (root / "COMPLETE").exists():
                    model = self.exchange.store.get_manifest(identity["model_artifact_id"])
                    heads = self.exchange.store.get_manifest(identity["heads_artifact_id"])
                    for name, payload in (("model.tensor-tree", model.payload("weights")),
                                          ("model.json", model.payload("package_manifest")),
                                          ("heads.tensor-tree", heads.payload("checkpoint"))):
                        self.exchange.store.materialize(payload, root / name)
                    # Whole-file replace, as before: a retry after a failed attempt (or
                    # another process) never leaves a truncated manifest.
                    for name, body in (("model-manifest.json", model.to_bytes()),
                                       ("heads-manifest.json", heads.to_bytes()),
                                       ("COMPLETE", identity["snapshot_sha256"].encode())):
                        pending = root / f".{name}.{threading.get_ident()}.pending"
                        pending.write_bytes(body)
                        pending.replace(root / name)
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
        if self.sgd_owns_theta:
            return self._sgd_snapshot(began)
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

    def _sgd_snapshot(self, began: float) -> dict[str, Any]:
        """The SGD process wrote this dataset's final theta right after its last step."""
        assert self.sgd is not None
        message = self.sgd.expect("snapshot", timeout=self.sgd.options.stall_seconds,
                                  updates=self.updates, datasets=self.datasets)
        self.sgd_snapshot_updates = self.updates
        snapshot = {
            "weights_file": message["file"],
            "construction": self.construction_template,
            "deduplicate_text": self.performance.deduplicate_text,
            "updates": self.updates,
        }
        self.timing["snapshot_seconds"] += time.perf_counter() - began
        return snapshot

    def _discard_snapshot(self, snapshot: dict[str, Any] | None) -> None:
        # An unsent/finished SGD weights file is a derived copy; theta stays in the child.
        if snapshot is not None and "weights_file" in snapshot:
            Path(snapshot["weights_file"]["path"]).unlink(missing_ok=True)

    def _submit_publication(
        self, snapshot: dict[str, Any], version: int, updates: int
    ) -> Future[Any]:
        if "weights_file" in snapshot:
            metadata = {key: value for key, value in snapshot.items() if key != "weights_file"}
            return self.publisher.submit(
                _publish_snapshot_file, pack_transport(metadata),
                weights=snapshot["weights_file"], peer=self.config.peer, run=self.config.run,
                generation=self.generation, version=version, updates=updates,
                producer=self.producer, publish_model=self.io.publish_model,
                work=str(self.live_work))
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
            # Only the SGD profile's file needs a later release; never retain theta.
            self.pending_snapshot = snapshot if "weights_file" in snapshot else None

    def _collect_publication(self, *, wait: bool = False) -> None:
        while self.pending_publication is not None:
            if not wait and not self.pending_publication.done():
                return
            publication = self.pending_publication.result()
            self._discard_snapshot(self.pending_snapshot)
            self.pending_snapshot = None
            with self.condition:
                if self.fast_intake is not None:
                    # Before any service can hold it: a fast segment trains only
                    # under a mu recorded here (fast_segment_mu_not_published).
                    self.fast_intake.publish_mu(publication["identity"])
                if self.shards is not None:
                    # Every shard has recorded it (acknowledged) before any
                    # service can hold it.
                    self.shards.publish_mu(publication["identity"])
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
        if self.sgd_owns_theta and self.sgd_snapshot_updates == self.updates:
            # The SGD process already handed over theta for these updates at the
            # dataset boundary; it is queued or in flight. No second copy exists.
            self._collect_publication(wait=False)
            self._kick_publication()
            return
        snapshot = self._capture_model()
        self._discard_snapshot(self.latest_snapshot)
        self.latest_snapshot = snapshot  # replace unsent snapshot; skip intermediate mu
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
        if operation in (FAST_INSTANCE_ADMISSION, FAST_INSTANCE_EXCLUDED):
            return self._fast_control(operation, request)
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
                    "training_discarded_rows": self.training_discarded_rows + (
                        self.shards.discarded_rows() if self.shards is not None else 0),
                    "stop_epoch": self.stop_epoch, "owner": self.pool.pool_id,
                    "control_lock_timing": (self.control_lock.snapshot()
                        if hasattr(self.control_lock, "snapshot") else [])}
                   if self.wal is not None else {}),
                **self.fast_status(),
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

    # -- fast_chain (WP8): sealed segments from the assembler, no actor lease -------------
    def _fast_control(self, operation: str, request: dict[str, Any]) -> dict[str, Any]:
        """Non-mutation actor requests: never journaled, never replayed by the client."""
        if self.shards is not None:
            return cast(dict[str, Any], self.shards.control(operation, request))
        if self.fast_intake is None:
            raise BoundaryError("rl_learner", "fast_chain_not_enabled_on_this_learner")
        self.fast_intake.set_generation(self.generation)
        if operation == FAST_INSTANCE_ADMISSION:
            decision = self.fast_intake.attest(request)
        else:
            decision = self.fast_intake.exclude(request)
        try:
            self._fast_apply(decision)
        except BaseException as error:
            # Released buffered segments fail exactly as on the hand-off thread:
            # input is fenced, not only reported back to this actor (review rec. 3).
            self._fast_fence("fast_control", error)
            raise
        assert decision.reply is not None
        return decision.reply

    def attach_fast_handoff(self, connection: Any) -> threading.Thread:
        """Start the reader of one assembler's `fast_handoff` Connection.

        Any broken message, digest or contract fences new input loudly; nothing
        falls back to the old chain and no segment is re-requested.
        """
        if self.shards is not None:
            # One shard owns this Connection for its whole life (round-robin in
            # attach order); the returned thread ends when its reader ends.
            return cast(threading.Thread, self.shards.attach(connection))
        if self.fast_intake is None:
            raise BoundaryError("rl_learner", "fast_chain_not_enabled_on_this_learner")
        thread = threading.Thread(target=self._fast_receive, args=(connection,),
                                  name="rl-fast-handoff", daemon=True)
        self.fast_receivers.append(thread)
        thread.start()
        return thread

    def _fast_receive(self, connection: Any) -> None:
        assert self.fast_intake is not None
        last_sequence, assembler = 0, None  # what this connection carried so far
        try:
            while True:
                try:
                    if self.fast_verifier is None:
                        kind, message, data = read_handoff(connection)
                        checked = None
                    else:
                        # Same read, digest and header parse in one verify worker;
                        # its errors come back as raised here, a gone worker is
                        # `fast_verify_worker_died` (never EOFError: fences below).
                        kind, message, data, checked = self.fast_verifier.read_handoff(
                            connection, decode_headers=bool(self.fast_intake.aux_labels))
                except (EOFError, ConnectionResetError):
                    # A sender process that dies with our ack unread resets the socket
                    # instead of a clean EOF: the same lost assembler.
                    self._fast_lost()
                    return
                if kind == "end":  # orderly: every hand-off of this assembler was acked
                    if (message["handed_off"] != last_sequence
                            or assembler not in (None, message["assembler_id"])):
                        raise HandoffError("handoff_end_mismatch")
                    self._event("fast_handoff_ended", assembler_id=message["assembler_id"],
                                handed_off=message["handed_off"])
                    return
                if assembler not in (None, message["assembler_id"]):
                    raise HandoffError("handoff_assembler_changed")
                assembler = message["assembler_id"]
                if kind in ("segment", "labels"):
                    last_sequence = message["handoff_sequence"]
                self.fast_intake.set_generation(self.generation)
                if kind in ("segment", "labels"):
                    assert data is not None
                    intake = (self.fast_intake.offer if kind == "segment"
                              else self.fast_intake.labels)
                    self._fast_apply(intake(message, data) if checked is None
                                     else intake(message, data, checked))
                    # Ack after the bytes are owned here: the sender's in-flight
                    # bound is the only back-pressure on the assembler.
                    ack(connection, message)
                else:
                    self._fast_apply(self.fast_intake.closed(message))
        except BaseException as error:
            if isinstance(error, FastVerifyClosed):
                # The learner closed the verify pool: a stopping learner just
                # ends this reader (its telemetry may already be shut down);
                # otherwise it is a loud fence like any hand-off error.
                with self.condition:
                    stopping = self.newlease_stopped
                if stopping:
                    return
            self._fast_fence("fast_handoff", error)
            raise

    def _fast_lost(self) -> None:
        """EOF without the sender's end: its assembler/service died. Fence input loudly so
        the training loop stops now instead of waiting for data until its deadline; a
        learner that is already stopping only records it."""
        with self.condition:
            stopping = self.newlease_stopped
        self._event("fast_handoff_lost", learner_stopping=stopping)
        if not stopping:
            self._fast_fence("fast_handoff_lost", HandoffError("assembler_gone_without_end"))

    def _fast_fence(self, operation: str, error: BaseException) -> None:
        with self.condition:
            self.input_fenced = self.newlease_stopped = True
            self.input_failure = {"operation": operation, "code": "INVALID_PACKET",
                                  "error_type": type(error).__name__,
                                  "error": str(error)[:400]}
            self.condition.notify_all()
            self._notify_capacity(force=True)

    def _fast_referenced(self) -> set[str]:
        """Segments a prepared (read-ahead) batch already holds: treated as used."""
        return {record["segment_id"] for active, _ in self._pending_batches()
                for record in active["segments"]}

    def _fast_apply(self, decision: IntakeDecision) -> None:
        assert self.fast_intake is not None and self.fast_sources is not None
        if (decision.exclude_instance is None and not decision.admit
                and not decision.auxiliary):
            return  # buffered / counted-drop decisions change nothing here
        # Aux row records are pure functions of the verified groups (the learner's
        # generation is fixed for its lifetime): built before the condition.
        auxiliary = (self._fast_auxiliary(decision.auxiliary) if decision.auxiliary
                     else None)
        # Each admission's queue record (and its private deep copy) is likewise a
        # pure function of the segment, its envelope, the run and the generation:
        # built before the condition, so the hold only decides and publishes.
        # The payload bytes are not copied anywhere here: `FastSources.put`
        # stores the hand-off's own `bytes` object (already copied off the
        # socket / verify arena before this call).
        admits = [(segment, envelope, self._fast_record(segment, envelope))
                  for segment, envelope in decision.admit]
        with self.condition:
            if decision.exclude_instance is not None:
                self._fast_exclude(decision.exclude_instance)
            # One wake-up per decision, not per segment: every admission and
            # capacity eviction below runs under this one condition hold, so no
            # waiter can run (or start waiting) in between; the single
            # `notify_all` after them wakes exactly the waiters each per-segment
            # notify would have. `finally`: an admission that raises after an
            # earlier one changed the queue still wakes them, as before.
            changed: list[str] = []
            # The prepared-batch referenced set, computed at most once per hold
            # (only if some admission overflows): admissions and evictions never
            # change `pending_batch` / `ahead_batches`, and no other thread can
            # while this condition is held.
            referenced: list[set[str]] = []
            try:
                for segment, envelope, prepared in admits:
                    self._fast_admit(segment, envelope, changed, prepared=prepared,
                                     referenced=referenced)
            finally:
                if changed:
                    self.condition.notify_all()
                if "admitted" in changed:
                    self.source_prefetch_changed.set()
            if auxiliary:
                self._add_auxiliary(auxiliary)

    def _fast_auxiliary(self, groups: list[tuple[str, str, list[dict[str, Any]]]]
                        ) -> list[tuple[str, str, list[dict[str, Any]]]]:
        """Verified fast labels -> the old pools' rows; each holds its original segment."""
        result = []
        for kind, identity, rows in groups:
            values = []
            for row in rows:
                segment, envelope = row["segment"], row["envelope"]
                ref = BinaryRef(FAST_FORMAT, f"{self.config.run}/{self.generation}/fast/"
                                f"{segment.segment_id}", envelope["packet_sha256"],
                                envelope["packet_size"])
                member = BatchMember(segment.segment_id, ref, envelope["run_id"],
                                     envelope["rows"], envelope["mu"]["snapshot_sha256"],
                                     envelope["episode"])
                # The same row reference `decode_fast_packet` gives that row.
                source: RowRef = {"run_id": envelope["run_id"],
                                  "segment_id": segment.segment_id,
                                  "training_generation": envelope["training_generation"],
                                  "row": row["row"],
                                  "key": f"{envelope['run_id']}:{row['step_index']}"}
                values.append({"key": source["key"], "source": source, "member": member,
                               "outcome" if kind == "terminal" else "hp": row["label"],
                               "prepared_input": AuxiliaryInput(source, member, segment.data,
                                                                envelope)})
            result.append((kind, identity, values))
        return result

    def _fast_record(self, segment: FastSegment, envelope: dict[str, Any]
                     ) -> tuple[BinaryRef, str, dict[str, Any]]:
        """(source ref, owner, private queue record) of one admission; no learner
        state is read beyond the fixed run/generation, so callers build it
        without the condition."""
        ref = BinaryRef(FAST_FORMAT, f"{self.config.run}/{self.generation}/fast/"
                        f"{segment.segment_id}", envelope["packet_sha256"],
                        envelope["packet_size"])
        record = {"segment_id": segment.segment_id, "fast": True,
                  "payload": {"format": ref.format, "slot": ref.slot, "sha256": ref.sha256,
                              "size": ref.size},
                  "run_id": envelope["run_id"], "rows": envelope["rows"], "mu": envelope["mu"],
                  "episode": envelope["episode"], "actor": envelope["actor"],
                  "envelope": envelope}
        # The copy `SegmentQueue.admit` would make under the lock, made here.
        return ref, "ppo:" + segment.segment_id, copy.deepcopy(record)

    def _fast_admit(self, segment: FastSegment, envelope: dict[str, Any],
                    changed: list[str], *,
                    prepared: tuple[BinaryRef, str, dict[str, Any]] | None = None,
                    referenced: list[set[str]] | None = None) -> None:
        """Admit one segment under `condition`. Each queue change is appended to
        `changed` as it happens ("evicted", "admitted"); the caller
        (`_fast_apply`) notifies the condition (and, after an admission, the
        source prefetch) once for its whole decision, even if this raises.

        `prepared`: `_fast_record(segment, envelope)`, built before the hold.
        `referenced`: a per-hold cache cell for `_fast_referenced()`."""
        assert self.fast_sources is not None
        if self.newlease_stopped or self.input_fenced:
            self.training_discarded_rows += segment.rows
            return
        if envelope["compiler_identity"] != self.compiler_identity:
            raise ValueError("fast_segment_compiler_identity_changed")
        assert self.fast_intake is not None
        if self.fast_intake.excluded(envelope["run_id"]):
            # The intake decided "admit", then an exclusion of this instance was
            # applied (another thread) before this admission ran: drop, counted.
            self.fast_counters["excluded_admit_raced"] += 1
            self.fast_counters["excluded_admit_raced_rows"] += segment.rows
            self.fast_counters["excluded_dropped_rows"] += segment.rows
            return
        # Full queue: drop the oldest queued, unprepared segments (whole segments,
        # no trace crosses them), counted; decision of 11-09.
        overflow = self.queue.occupied + segment.rows - self.settings.capacity_rows
        victims: set[str] = set()
        if overflow > 0:
            # Only a full queue needs the (prepared-batch) referenced set.
            if referenced is None:
                referenced = []
            if not referenced:
                referenced.append(self._fast_referenced())
            held = referenced[0]
            pending = self.queue.pending
            # The same oldest-first walk, with the records a prepared batch
            # holds skipped in C (they are usually the queue's head).
            unheld = itertools.compress(pending, map(operator.not_, map(
                held.__contains__, map(operator.itemgetter("segment_id"), pending))))
            for record in unheld:
                if overflow <= 0:
                    break
                # Only fast records: an old-chain lease's segment has its own
                # episode accounting and is never a fast capacity drop (review S4).
                if record.get("fast"):
                    victims.add(record["segment_id"])
                    overflow -= record["rows"]
        if overflow > 0:
            for counter in ("capacity_dropped", "capacity_refused"):
                self.fast_counters[counter] += 1
                self.fast_counters[counter + "_rows"] += segment.rows
            return
        evicted = self.queue.discard(victims, notify=False)
        if evicted:
            changed.append("evicted")
        for record in evicted:
            self._fast_forget(record, counters=("capacity_dropped", "capacity_evicted"))
        ref, owner, record = (prepared if prepared is not None
                              else self._fast_record(segment, envelope))
        self.fast_sources.put(ref, envelope, segment.data, owner)
        self.hot_references[ref.slot, owner] = ref
        self.queue.admit(record, notify=False, owned=True)
        changed.append("admitted")
        self.fast_admitted_rows[envelope["run_id"]] = (
            self.fast_admitted_rows.pop(envelope["run_id"], 0) + envelope["rows"])
        while len(self.fast_admitted_rows) > 65536:
            self.fast_admitted_rows.popitem(last=False)
        self.fast_counters["admitted"] += 1
        self.fast_counters["admitted_rows"] += envelope["rows"]

    def _fast_forget(self, record: dict[str, Any], *, counters: tuple[str, ...]) -> None:
        payload = self._member(record).payload
        self._release(payload, "ppo:" + record["segment_id"])
        for counter in counters:
            self.fast_counters[counter] += 1
            self.fast_counters[counter + "_rows"] += record["rows"]
        if record["run_id"] in self.fast_admitted_rows:
            self.fast_admitted_rows[record["run_id"]] -= record["rows"]

    def _fast_exclude(self, instance: str) -> None:
        """D1 = A: drop what is still queued and unprepared; count what was used.

        Used = selected into a prepared (read-ahead) batch (lead ruling 11-10):
        that batch is neither voided nor rebuilt. Its rows are counted as
        `excluded_prefetched_rows`, rows already handed to SGD as
        `excluded_trained_rows`; `excluded_used_rows` is their sum."""
        assert self.fast_intake is not None
        referenced = self._fast_referenced()
        mine = [record for record in self.queue.pending
                if record.get("fast") and record["run_id"] == instance]
        ids = {record["segment_id"] for record in mine
               if record["segment_id"] not in referenced}
        prefetched = sum(record["rows"] for record in mine
                         if record["segment_id"] in referenced)
        dropped = 0
        for record in self.queue.discard(ids):
            payload = self._member(record).payload
            self._release(payload, "ppo:" + record["segment_id"])
            dropped += record["rows"]
        used = self.fast_admitted_rows.pop(instance, 0) - dropped
        # Its label rows leave the aux pools too; rows a prepared batch already
        # holds count as used, like its PPO rows.
        for pool in (self.terminal, self.hp):
            for key in [key for key, rows in pool.groups.items()
                        if any(r["source"]["run_id"] == instance for r in rows)]:
                self.fast_counters["excluded_aux_rows"] += len(pool.groups.pop(key))
        self.fast_counters["excluded_instances"] += 1
        self.fast_counters["excluded_dropped_rows"] += dropped
        self.fast_counters["excluded_used_rows"] += used
        self.fast_counters["excluded_prefetched_rows"] += prefetched
        self.fast_counters["excluded_trained_rows"] += used - prefetched
        if dropped:
            self._notify_capacity()

    def fast_status(self) -> dict[str, Any]:
        if self.shards is not None:
            return cast(dict[str, Any], self.shards.status())
        if self.fast_intake is None or self.fast_sources is None:
            return {}
        with self.condition:
            counters = dict(self.fast_counters)
        verify = ({"verify": self.fast_verifier.status()} if self.fast_verifier is not None
                  else {})
        return {"fast_chain": {"learner": counters, "intake": self.fast_intake.report(),
                               **self.fast_sources.status(), **verify}}

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

    def _binary_owner(self, payload: BinaryRef) -> Any:
        if payload.format == FAST_FORMAT and self.fast_sources is not None:
            return self.fast_sources
        return self.binary_sources

    def _retain(self, payload: HotPayloadRef | BinaryRef, owner: str) -> None:
        with self.condition:
            if isinstance(payload, BinaryRef):
                self._binary_owner(payload).retain(payload, owner)
            else:
                self.exchange.retain_hot_payload(payload, owner)
            self.hot_references[payload.slot, owner] = payload

    def _release(self, payload: HotPayloadRef | BinaryRef, owner: str) -> None:
        with self.condition:
            if isinstance(payload, BinaryRef):
                self._binary_owner(payload).release(payload, owner)
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

    def _prefetch_members(self, members: tuple[BatchMember, ...], *,
                          wait: bool = True) -> dict[str, float]:
        """Copy members into preparer arenas; returns seconds per phase.

        Envelopes come from each owner in one lock hold, and the preparer
        reserves/publishes the whole list in one hold per round, instead of
        several contended lock hand-offs per member.
        """
        if self.wal is None or not members:
            return {}
        began = time.perf_counter()
        refs = [member.payload for member in members]
        assert all(isinstance(ref, BinaryRef) for ref in refs)
        envelopes: dict[int, dict[str, Any]] = {}
        groups: dict[int, tuple[Any, list[int]]] = {}
        for index, ref in enumerate(refs):
            owner = self._binary_owner(cast(BinaryRef, ref))
            groups.setdefault(id(owner), (owner, []))[1].append(index)
        for owner, indices in groups.values():
            for index, envelope in zip(
                    indices, owner.envelopes([refs[i] for i in indices]), strict=True):
                envelopes[index] = envelope
        enveloped = time.perf_counter()
        timing = cast("BinaryBatchPreparer", self.input_preparer).prefetch_many(
            [(cast(BinaryRef, ref), envelopes[i]) for i, ref in enumerate(refs)], wait=wait)
        return {"envelope": enveloped - began, **timing}

    def _source_prefetch_loop(self) -> None:
        """Coalesce commit hints; copy/decode sources before numerical selection.

        A normal admitted queue already enforces the per-episode row limit, so
        its preparation window is a FIFO prefix. This only reads that prefix;
        it never freezes a request, samples auxiliary rows or advances RNG.
        """
        # This thread's own `_member` results for the window it read last time,
        # keyed by record object (kept alive here, so its id is never reused).
        # A queued record is never mutated (built, appended, then only read,
        # taken or discarded), so its member is a fixed function of the object:
        # one wake (one per admission decision) no longer rebuilds every
        # BinaryRef/BatchMember of the window under the learner condition.
        members: dict[int, tuple[dict[str, Any], BatchMember]] = {}
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
                    window: dict[int, tuple[dict[str, Any], BatchMember]] = {}
                    referenced = (self._fast_referenced() if self.prefetch_referenced_only
                                  else None)
                    for record in self.queue.pending:
                        known = members.get(id(record))
                        if known is None or known[0] is not record:
                            known = record, self._member(record)
                        window[id(record)] = known
                        member = known[1]
                        if referenced is not None and record["segment_id"] not in referenced:
                            pass  # not selected yet: its batch's dispatch copies it
                        elif member.payload not in cached:
                            self._retain(member.payload, "source-prefetch")
                            held.append(member)
                        rows += member.rows
                        if rows >= (self.prepared_batches + 1) * self.settings.dataset_rows:
                            break
                    members = window
                # Provider borrow, hashing, arena copy and worker submission
                # hold neither the learner condition nor a socket handler.
                # Short chunks: two preparer lock holds per chunk, not per
                # member, while the gate is never held for a whole dataset.
                for start in range(0, len(held), 16):
                    if self.source_prefetch_stopping.is_set():
                        break
                    try:
                        # A CP barrier temporarily fences the same preparer.
                        # Serialize only this source copy against that window;
                        # never acquire this gate under the learner condition.
                        with self.source_prefetch_gate:
                            self._prefetch_members(tuple(held[start:start + 16]), wait=False)
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
            def prefetch_labels(members: tuple[BatchMember, ...] = members) -> None:
                self._prefetch_members(members)

            def submit_labels(request: dict[str, Any] = request,
                              members: tuple[BatchMember, ...] = members) -> Future[Any]:
                return (cast("BinaryBatchPreparer", self.input_preparer).verify_labels(
                         request, members)
                     if self.wal is not None
                     else self.input_preparer.pool.submit(verify_auxiliary, request, members))
            self.pending_labels.append(
                (
                    self._dispatch_preparation(prefetch_labels, submit_labels),
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
            # Aux rows of every dataset not yet fully charged: the current one, the
            # prestarted next one and every prepared one.
            upcoming = (((self.active,) if self.active else ())
                        + ((self.next_active[0],) if self.next_active is not None else ())
                        + tuple(a for a, _ in pending))
            reserved = {row["key"] for name in ("terminal", "hp")
                        for active in upcoming for row in active[name]}
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
            number = (self.datasets + int(self.active is not None)
                      + int(self.next_active is not None) + len(pending) + 1)
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
                if not record.get("fast"):
                    self._episode_rows(record["actor"], record["episode"],
                                       queued=-record["rows"])
            self._notify_capacity()
            self.source_prefetch_changed.set()

    def _dispatch_preparation(self, prefetch: Callable[[], Any],
                              submit: Callable[[], Future[Any]]) -> Future[Any]:
        """Run `prefetch` on any dispatch thread, then `submit` alone in issue order.

        The executor starts tasks FIFO, so every earlier ticket already holds a
        thread or has finished; a prefetch never waits for a later ticket (a
        copied source keeps no arena credit while its task waits for its turn).
        A failed prefetch still takes and passes its turn, failing only its own
        result, so a later task never waits forever.
        """
        result: Future[Any] = Future()

        def complete(future: Future[Any]) -> None:
            try:
                result.set_result(future.result())
            except BaseException as error:
                result.set_exception(error)

        def dispatch(ticket: int) -> None:
            failure: BaseException | None = None
            try:
                prefetch()
            except BaseException as error:
                failure = error
            with self.dispatch_order:
                while self.dispatch_turn != ticket:
                    self.dispatch_order.wait()
            try:
                if failure is not None:
                    raise failure
                submit().add_done_callback(complete)
            except BaseException as error:
                result.set_exception(error)
            finally:
                with self.dispatch_order:
                    self.dispatch_turn += 1
                    self.dispatch_order.notify_all()

        # Ticket order must equal executor FIFO order; a refused submit takes none.
        with self.dispatch_order:
            self.preparation_dispatch.submit(dispatch, self.dispatch_issued)
            self.dispatch_issued += 1
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
        marks: dict[str, float] = {}
        phases: dict[str, float] = {}

        def prefetch() -> None:
            marks["started"] = time.perf_counter()
            phases.update(self._prefetch_members(request.members))
            marks["prefetched"] = time.perf_counter()

        def submit() -> Future[PreparedBatch]:
            turn = time.perf_counter()
            self._await_releases()
            future = self.input_preparer.submit(request, inputs)
            submitted = time.perf_counter()
            started, prefetched = marks["started"], marks["prefetched"]

            def observe(finished: Future[PreparedBatch]) -> None:
                if not finished.cancelled() and finished.exception() is None:
                    finished.result().timings.update(
                        frontend_retain_seconds=scheduled - began,
                        frontend_dispatch_queue_seconds=started - scheduled,
                        frontend_prefetch_seconds=prefetched - started,
                        **{f"frontend_prefetch_{name}_seconds": seconds
                           for name, seconds in phases.items()},
                        frontend_prefetch_other_seconds=max(
                            0.0, prefetched - started - sum(phases.values())),
                        frontend_order_wait_seconds=turn - prefetched,
                        frontend_submit_seconds=submitted - turn,
                        frontend_wall_seconds=submitted - started)

            future.add_done_callback(observe)
            return future
        return self._dispatch_preparation(prefetch, submit)

    def _prepare_ahead(self) -> None:
        if self.shards is not None:
            self._shard_ready()
            return
        while (self.settings.prepare_ahead
               and not self.newlease_stopped and not self.input_fenced
               and (self.selection_deadline is None
                    or time.monotonic() < self.selection_deadline)
               and len(self._pending_batches()) + int(self.next_active is not None)
               < self.prepared_batches):
            # A read-ahead selection stays in the queue (take=False), so it must be
            # retained and registered as pending under the same lock: otherwise a
            # concurrent fast admission or exclusion sees it as unreferenced, drops
            # it and releases its bytes before `_submit_batch` retains them.
            with self.condition:
                active = self._select(take=False)
                if active is None:
                    break
                pending = active, self._submit_batch(active)
                if self.pending_batch is None:
                    self.pending_batch = pending
                else:
                    self.ahead_batches.append(pending)
            if self.sgd is not None and self.sgd.options.stage_thread:
                # Registered after it is queued and outside `condition`: an
                # already-done Future plans inline here, and a failing send must
                # not hold `condition` while the failure path joins the child.
                pending[1].add_done_callback(self._sgd_prepared)
        if self.sgd is not None and not self.newlease_stopped and not self.input_fenced:
            self._sgd_plan_ahead()

    def _prepare(self) -> bool:
        if self.active_batch is not None:
            return True
        self._collect_labels()
        if self.pending_batch is None and self.shards is not None:
            self._shard_ready()  # the shards select and prepare; this only collects
            if self.pending_batch is None:
                return False
        elif self.pending_batch is None:
            active = self._select()
            if active is None:
                return False
            self.pending_batch = active, self._submit_batch(active)
        assert self.pending_batch is not None
        active, future = self.pending_batch
        began = time.perf_counter()
        while self.ahead_while_waiting and not future.done():
            # Same selections in the same order; they are only submitted while
            # this one is still being prepared instead of after it.
            wait_futures((future,), timeout=0.05)
            if not future.done():
                self._prepare_ahead()
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
        return self._take(active, batch, ahead=False)

    def _take(self, active: dict[str, Any], batch: Any, *, ahead: bool) -> bool:
        """Take the prepared head dataset: the current one, or (`ahead`, SGD
        process only) the next one, authorized while the current still trains."""
        timing_key = "binary_ipc_wall_seconds" if self.wal is not None else "request_wall_seconds"
        self.timing["preparation_seconds"] += batch.timings[timing_key]
        self.timing["prepared_batches"] += 1
        if not active["taken"] and self.shards is not None:
            # The same stop check, membership check and queue removal, in the
            # owning shard under its own lock; False = stopped, nothing taken.
            if not self.shards.take(active):
                return False
            active["taken"] = True
        elif not active["taken"]:
            with self.condition:
                if self.wal is not None and self.newlease_stopped:
                    return False
                if self.queue.dataset() != active["segments"]:
                    raise ValueError("prepared_dataset_membership_changed")
                self._dataset_released(active["segments"])
            active["taken"] = True
        # One step under `sgd_lock`: the prepared-callback must never see the head
        # popped while the taken dataset is not yet current or next (it would plan
        # a third dataset onto the device).
        with self.sgd_lock if self.sgd is not None else nullcontext():
            self.pending_batch = self.ahead_batches.popleft() if self.ahead_batches else None
            if ahead:
                # Counted from this dataset's own first answer (see `_promote`).
                self.next_active = active, batch
                self.timing["sgd_prestarted_batches"] += 1
            else:
                self.active, self.active_batch = active, batch
                active["start_updates"] = self.updates
        if self.sgd is not None:
            # Same point as the in-process stage: the SGD process owns its pinned
            # and device copies before preparation and PPO references are released.
            sequence = batch.request.identity.sequence
            self.timing["sgd_staged_ahead_batches"] += sequence in self.sgd.staged
            self._sgd_plan(batch)
            stage_wait = time.perf_counter()
            self.sgd.wait_staged(sequence)
            self.timing["sgd_stage_wait_seconds"] += time.perf_counter() - stage_wait
        else:
            transfer_started = time.perf_counter()
            self.device_batch, copies = stage_batch(batch, self.device)
            self.timing["input_transfer_host_seconds"] += time.perf_counter() - transfer_started
            self.timing["input_transfer_copies"] += copies
        if self.shards is not None:
            # Same point, same work (`_ready_labels`, then both releases), done
            # by the owning shard: the bytes and the aux pools live there.
            self.shards.staged(active)
        else:
            self._ready_labels(batch)
            # Complete batch owns its tensors now. Release preparation and PPO bytes;
            # delayed-label/aux owners independently protect their own future reads.
            # One `condition` hold for all of them (same releases, same order): each
            # separate acquisition waited behind the receive/prefetch threads.
            self._release_taken(batch.request)
        self._event(
            "batch_ready",
            dataset=batch.request.identity.sequence,
            rows=len(batch.metadata),
            preparation=batch.timings,
            bytes=batch.payload_bytes,
        )
        if self.sgd is not None:
            # Boundary checks are done; the SGD process never starts a dataset itself.
            with self.sgd_lock:
                self.sgd.send("start", dataset=batch.request.identity.sequence)
            # The next dataset may already be prepared: stage it during this one.
            if not self.newlease_stopped and not self.input_fenced:
                self._sgd_plan_ahead()
        return True

    def _release_taken(self, request: PrepareRequest) -> None:
        with self.condition:
            for member in request.members:
                self._release(
                    member.payload, f"prepare:{self.generation}:{request.identity.sequence}")
            for member in request.members:
                self._release(member.payload, "ppo:" + member.segment_id)

    def _sgd_prestart(self) -> None:
        """Authorize the next dataset before the current one's answers are all
        accounted, so the SGD child starts it right after the current one instead
        of after a controller round trip. Only an already-prepared and already
        staged dataset (never a wait), at most one beyond the current, and never
        when the controller would not take another dataset at this boundary: a
        CP request (CP needs the child quiescent), STOP, deadline, stop or fence.

        Theta, KL stop, clip and PPO are unchanged. Not strictly lockstep: N+1's
        take (aux selection timing for later datasets, `_ready_labels`,
        `_dataset_released`) runs before N's answers are all accounted. A remote
        stop request or max_versions arriving after a prestart still trains the
        authorized N+1 fully (one extra dataset)."""
        if (self.sgd is None or not self.sgd.options.prestart or self.active_batch is None
                # The SGD process always runs with the binary dataplane's WAL, whose
                # exit path finishes the in-flight dataset before the final CP.
                or self.wal is None
                or self.next_active is not None or self.pending_batch is None
                or self.checkpoint_requested or self.newlease_stopped or self.input_fenced
                or self.stop_fact is not None or self.version >= self.config.max_versions
                or (self.selection_deadline is not None
                    and time.monotonic() >= self.selection_deadline)
                or (self.deadline_at is not None and time.time() >= self.deadline_at - 600)):
            return
        active, future = self.pending_batch
        if not future.done() or future.cancelled() or future.exception() is not None:
            return  # `_prepare` owns waiting and preparation failures
        batch = future.result()
        if batch.request.identity.sequence not in self.sgd.staged:
            return
        if (self.work / "STOP").exists():
            return
        self._take(active, batch, ahead=True)

    def _promote(self) -> None:
        """The current dataset finished: the authorized next one becomes current."""
        if self.next_active is not None:
            (self.active, self.active_batch), self.next_active = self.next_active, None
            self.active["start_updates"] = self.updates

    def _step(self) -> dict[str, Any]:
        active, batch = self.active, self.active_batch
        assert active is not None and batch is not None
        if self.sgd is not None:
            return self._remote_step(active, batch)
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
        return self._account_step(active, batch, index, len(batch.minibatches), result,
                                  policy_samples, policy_observed_at, policy_version,
                                  policy_updates)

    def _remote_step(self, active: dict[str, Any], batch: Any) -> dict[str, Any]:
        """Charge one SGD-process attempt; `update` and position are the fence."""
        assert self.sgd is not None
        if self.sgd_callback_error is not None:  # also after stop, when no plan-ahead runs
            raise self.sgd_callback_error
        self._check_releases()
        self._sgd_prestart()  # before waiting: the start then precedes this answer
        index = active["minibatch"]
        message = self.sgd.expect("step", timeout=self.sgd.step_timeout())
        result = message["result"]
        stepped = message["stepped"]
        expected = {"dataset": batch.request.identity.sequence, "minibatch": index,
                    "epoch": active["epoch"], "update": self.updates + int(stepped)}
        if (type(stepped) is not bool or result.get("stepped") is not stepped
                or result.get("kl_stopped") is stepped or message["kl_stopped"] is stepped
                or any(message[key] != value for key, value in expected.items())):
            raise self.sgd.violation("sgd_step_fence_mismatch", expected=expected,
                                     received={key: message.get(key) for key in expected})
        policy_samples = result.pop("_policy_rows", [])
        timing = message["timing"]
        self.timing["optimizer_attempt_seconds"] += timing["optimizer_seconds"]
        self.sgd_rates = message["lr"], message["head_lr"]
        self.sgd_memory = message["memory"]
        reference = self.sgd.options.offline_step_seconds
        result["sgd_timing"] = {
            **timing, "step_ended_at": message["sent_at"],
            "message_lag_seconds": message["_received_at"] - message["sent_at"],
            "accounted_lag_seconds": time.time() - message["sent_at"],
            "online_offline_ratio": (timing["optimizer_seconds"] / reference
                                     if reference is not None else None)}
        return self._account_step(active, batch, index, batch.minibatch_count, result,
                                  policy_samples, message["observed_at"], self.version,
                                  self.updates)

    def _account_step(self, active: dict[str, Any], batch: Any, index: int, minibatches: int,
                      result: dict[str, Any], policy_samples: list[dict[str, Any]],
                      policy_observed_at: float, policy_version: int,
                      policy_updates: int) -> dict[str, Any]:
        """The single successful-use owner: first use, aux uses, counters, events."""
        build: Callable[[], dict[str, Any]] | None = None
        if result["stepped"]:
            used = set(active["used"])  # was a list scan per row: O(rows x used) per step
            newly_used = [i for i in batch.request.minibatch_rows[index] if i not in used]
            self.first_use += len(newly_used)
            if self.policy_mask_cache is None or self.policy_mask_cache[0] is not batch:
                self.policy_mask_cache = batch, batch.policy_mask.tolist()  # once per dataset
            policy_mask = self.policy_mask_cache[1]
            self.first_use_multi += sum(policy_mask[i] for i in newly_used)
            active["used"].extend(newly_used)
            if self.shards is not None:
                self.shards.stepped(active, index)  # the owning shard charges its pools
            else:
                self._charge_auxiliary(active, batch.request, index)
            if self.sgd is None:
                self._update_ema()
            self.updates += 1
            captured: dict[str, Any] = dict(
                now=time.time(), version=self.version, updates=self.updates,
                policy_observed_at=policy_observed_at, policy_version=policy_version,
                policy_updates=policy_updates)
            metadata = batch.metadata
            if self.sgd is None:
                result.update(_first_use_fields(metadata, policy_mask, newly_used,
                                                policy_samples, **captured))
            else:
                # SGD-process path: summaries are built in the telemetry thread from
                # these captured values; nothing here feeds a learning decision.
                build = partial(_first_use_fields, metadata, policy_mask, newly_used,
                                policy_samples, **captured)
        lr, head_lr = self._learning_rates()
        result.update(
            update=self.updates,
            lr=lr,
            head_lr=head_lr,
            dataset=batch.request.identity.sequence,
            minibatch=index,
            epoch=active["epoch"],
            first_use=self.first_use,
            first_use_multi=self.first_use_multi,
            memory=self._memory(),
        )
        active["minibatch"] += 1
        end_epoch = active["minibatch"] == minibatches
        finished = result["kl_stopped"] or (
            end_epoch and active["epoch"] + 1 >= self.settings.epochs)
        if end_epoch and not finished:
            active["epoch"] += 1
            active["minibatch"] = 0
        if finished:
            if self.wal is not None:
                self.committed_selection_rng = batch.request.selection_rng_state
            self.datasets += 1
            if self.shards is not None:
                # Its shard commits that selection RNG, forgets the dataset and
                # promotes its own next one, in the same order as here.
                self.shards.finished(active)
            self.active = self.active_batch = None
            self.device_batch = None
            self.policy_mask_cache = None  # never retain a finished batch
            self._promote()  # before forgetting: its sources stay retained
            self._forget_unreferenced()
            if self.updates > active["start_updates"]:
                self._schedule_publication()
        # Fallible IO follows the complete learning transition; CP never pairs
        # new Adam/theta with old successful-use counters.
        self._event("progress", _build=build, **result, version=self.version)
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

    def _charge_auxiliary(self, active: dict[str, Any], request: PrepareRequest,
                          index: int) -> None:
        """Aux uses of one successful minibatch (never for discarded KL work)."""
        with self.condition:
            for kind, pool in (("terminal", self.terminal), ("hp", self.hp)):
                offset = 0 if kind == "terminal" else len(active["terminal"])
                rows = [
                    row
                    for local, row in enumerate(active[kind])
                    if request.auxiliary_minibatches[offset + local] == index
                    and row["uses"] < pool.max_uses
                ]
                pool.used(rows, request.identity.sequence, epochs=1)
                # Only rows just charged can newly reach the limit.
                for identity, retired in pool.retire_exhausted(rows).items():
                    remaining = {
                        row["member"].segment_id for row in pool.groups.get(identity, [])
                    }
                    for member in {
                        row["member"].segment_id: row["member"] for row in retired
                    }.values():
                        if (member.segment_id not in remaining
                                and not isinstance(member.payload, BinaryRef)):
                            self._release(member.payload, f"aux:{kind}:{identity}")

    def _update_ema(self) -> None:
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

    def _learning_rates(self) -> tuple[float, float]:
        if self.sgd_owns_theta:
            return self.sgd_rates
        return self.optimizer.param_groups[0]["lr"], self.optimizer.param_groups[1]["lr"]

    def _optimizer_groups(self) -> list[dict[str, Any]]:
        if self.sgd_owns_theta:
            return copy.deepcopy(self.sgd_groups)
        return [{key: value for key, value in group.items() if key != "params"}
                for group in self.optimizer.param_groups]

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
        upcoming = (self.next_active[0],) if self.next_active is not None else ()
        for active in (self.active, *upcoming, *(a for a, _ in self._pending_batches())):
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

    def _init_progress_telemetry(self) -> None:
        self.memory_sample = None
        self.policy_mask_cache = None

    def _memory(self) -> dict[str, Any]:
        """Peak memory, sampled at most every MEMORY_SAMPLE_SECONDS (peaks only grow)."""
        now = time.monotonic()
        if self.memory_sample is None or now - self.memory_sample[0] >= MEMORY_SAMPLE_SECONDS:
            sample: dict[str, Any] = dict(_process_memory())
            if self.device.type == "cuda":
                sample.update(
                    cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(self.device),
                    cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved(self.device))
            self.memory_sample = now, sample
        values = dict(self.memory_sample[1])
        if self.sgd is not None:
            values["sgd_process"] = self.sgd_memory  # latest child-reported peaks
        return values

    def _event(self, kind: str, *, _build: Any = None, **fields: Any) -> None:
        """Queue one event for the telemetry thread; the controller never waits on it.

        `_build` adds fields computed in that thread. A telemetry error surfaces on
        the next event. A full queue waits (bounded memory) for the oldest event;
        no progress within TELEMETRY_STALL_SECONDS fails loudly, never drops."""
        while self.telemetry_futures and self.telemetry_futures[0].done():
            self.telemetry_futures.popleft().result()
        if len(self.telemetry_futures) >= TELEMETRY_QUEUE_LIMIT:
            began = time.monotonic()
            try:
                self.telemetry_futures[0].result(timeout=TELEMETRY_STALL_SECONDS)
            except TimeoutError:
                raise RuntimeError(
                    f"telemetry_queue_stalled: {len(self.telemetry_futures)} events pending, "
                    f"no progress for {TELEMETRY_STALL_SECONDS:.0f} s; kind={kind}") from None
            self.telemetry_futures.popleft()
            self.timing["telemetry_full_wait_seconds"] += time.monotonic() - began
        common = {"training_generation": self.generation,
                  "budget_id": (self.config.operating_budget or {}).get("id")}

        def publish() -> None:
            built = _build() if _build is not None else {}
            self.exchange.publish_event(kind, **common, **fields, **built)

        self.telemetry_futures.append(self.telemetry.submit(publish))

    def _sgd_state_message(self) -> dict[str, Any]:
        """Dataset-boundary handshake with the SGD process; counters must match exactly.

        Only the handshake: the state file it names is read (and unlinked) later by
        `_read_sgd_state`, possibly on the checkpoint encoder thread.
        """
        assert self.sgd is not None
        self.sgd.send("checkpoint", updates=self.updates, datasets=self.datasets)
        message = self.sgd.expect("state", timeout=self.sgd.options.stall_seconds)
        if message["updates"] != self.updates or message["datasets"] != self.datasets:
            # Never pair new theta/Adam with other successful-use counters.
            raise self.sgd.violation("sgd_state_counters_changed", updates=message["updates"],
                                     datasets=message["datasets"], expected_updates=self.updates,
                                     expected_datasets=self.datasets)
        self.sgd_groups = message["optimizer_param_groups"]
        self.sgd_memory = message["memory"]
        return message

    @staticmethod
    def _read_sgd_state(sgd: Any, file: dict[str, Any], updates: int, datasets: int,
                        names: tuple[tuple[str, ...], ...]) -> dict[str, Any]:
        """The state file of one handshake, bound to the counters captured with it."""
        value = read_tree(file, STATE_SCHEMA)
        Path(file["path"]).unlink()
        if (value["updates"] != updates or value["datasets"] != datasets
                or tuple(tuple(group) for group in value["optimizer_parameter_names"])
                != names):
            raise sgd.violation("sgd_state_file_binding_changed")
        return value

    def _capture_learning_state(self) -> Callable[[], SelfContainedCheckpoint]:
        """Capture everything that belongs to this instant on the calling thread.

        The returned function does the rest and touches no live learner state: it
        reads and checks the SGD state file (when the SGD process owns theta),
        validates the optimizer order, digests the weights and builds the
        checkpoint. It can run on the checkpoint encoder thread while training
        continues; its errors surface where the encode's do (`_collect_checkpoint`).
        """
        file: dict[str, Any] | None = None
        sgd: Any = None
        weights: Any = None
        optimizer: Any = None
        ema: Any = None
        torch_rng: Any = None
        cuda: Any = None
        if self.sgd_owns_theta:
            # Only this path has an SGD process (`self.sgd` is not always set otherwise).
            sgd, file = self.sgd, self._sgd_state_message()["file"]
            names = self.optimizer_names
        else:
            with self.gpu_lock:
                weights = snapshot_weights(self.model.state_dict())
                optimizer, ema = cpu_copy(self.optimizer.state_dict()), snapshot_weights(self.ema)
            names = self._optimizer_names()
            torch_rng = torch.get_rng_state().clone()
            cuda = tuple(value.cpu().clone() for value in (
                tuple(torch.cuda.get_rng_state_all()) if self.device.type == "cuda"
                else self.cuda_rng))
        configuration = asdict(self.config)
        if configuration["operating_budget"] is not None:
            active = (time.monotonic() - self.active_training_began
                      if self.active_training_began is not None else self.active_training_seconds)
            configuration["operating_budget"]["consumed_seconds"] += active
        updates, datasets = self.updates, self.datasets
        identity = RunSequence(self.config.run, self.generation, self.checkpoint_sequence)
        template = self.construction_template
        producer = self.producer.to_dict()
        counters = LearningCounters(
            self.updates,
            self.datasets,
            self.first_use,
            self.first_use_multi,
            self.version,
            self.published_updates,
        )
        python_rng = random.getstate()
        selection_rng = (self.committed_selection_rng if self.wal is not None
                         else self.rng.getstate())
        conversion = self.conversion
        recovery = ({"generation": self.generation,
                     "committed_selection_rng": self.committed_selection_rng,
                     "speculative_selection_rng": self.rng.getstate(),
                     "pending_requests": [_pending_receipt(active["request"])
                                          for active, _ in self._pending_batches()],
                     "pending_labels": copy.deepcopy(self.pending_label_requests),
                     "restore_disposition": "discard_hot_inputs_resume_committed_rng",
                     # Shard count and each shard's committed/speculative selection
                     # RNG and in-flight receipts; a restore keeps both (another
                     # count is refused at start, never a silent RNG reset).
                     **({"dataplane_shards": self.shards.snapshot()}
                        if self.shards is not None else {})}
                    if self.wal is not None else None)

        def finish() -> SelfContainedCheckpoint:
            nonlocal weights, optimizer, ema, torch_rng, cuda
            if file is not None:
                remote = SegmentLearner._read_sgd_state(sgd, file, updates, datasets, names)
                weights, optimizer, ema = remote["weights"], remote["optimizer"], remote["ema"]
                torch_rng = remote["torch_rng"]
                cuda = tuple(value.cpu().clone() for value in remote["cuda_rng"])
            validate_optimizer_order(optimizer, names, weights)
            return SelfContainedCheckpoint(
                identity,
                replace(template, text_digest=text_digest(weights)),
                configuration,
                producer,
                weights,
                optimizer,
                names,
                ema,
                counters,
                RngState(python_rng, selection_rng, torch_rng, cuda),
                conversion,
                recovery,
            )

        return finish

    def _learning_state(self) -> SelfContainedCheckpoint:
        """The full checkpoint object, synchronously (restore checks and tests)."""
        return self._capture_learning_state()()

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
        self._check_releases(wait=True)  # a CP never precedes a staged release
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
        # Only the boundary capture runs here; the state-file read, its checks and
        # the checkpoint build run on the encoder thread before the encode.
        finish = self._capture_learning_state()
        self._event("checkpoint_captured", sequence=self.checkpoint_sequence,
                    capture_seconds=time.perf_counter() - began, memory=self._memory())
        self.checkpoint_encoding = self.checkpoint_encoder.submit(
            lambda: _encode_snapshot(finish()))
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
            # Runs last: a failed hand-off release is raised after the cleanup below.
            cleanup.callback(self._check_releases, wait=True)
            if self.wal is not None and not keep_journal:
                cleanup.callback(self._close_journal)
            cleanup.callback(self.publisher.shutdown, wait=True, cancel_futures=True)
            cleanup.callback(self.header_checks.shutdown, wait=True, cancel_futures=True)
            cleanup.callback(self.telemetry.shutdown, wait=True, cancel_futures=False)
            cleanup.callback(self.checkpoint_encoder.shutdown, wait=True, cancel_futures=False)
            cleanup.callback(self.checkpoints.__exit__, RuntimeError if self.input_fenced else None)
            if self.archive is not None:
                cleanup.callback(self.archive.stop_capacity_monitor)
            verifier = getattr(self, "fast_verifier", None)
            if verifier is not None:
                # Refuses new reads and drains in-flight ones before the workers
                # and arena go; hand-off threads then stop on
                # `fast_verify_pool_closed` without fencing (newlease_stopped).
                cleanup.callback(verifier.close)
            shards = getattr(self, "shards", None)
            if shards is not None:
                # Stops each shard's readers, prefetch and preparer and joins the
                # process; a shard that does not answer is killed and reported.
                cleanup.callback(shards.close)
            try:
                self.pool.close()
            finally:
                try:
                    if self.sgd is not None:
                        self.sgd.close()  # shutdown, join; a timeout kills and is recorded
                finally:
                    try:
                        self.preparation_dispatch.shutdown(wait=True, cancel_futures=False)
                    finally:
                        try:
                            # Queued releases finish before the preparer's barrier
                            # forgets the hand-offs (a failed one is raised last).
                            self.handoff_releaser.shutdown(wait=True)
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
            if self.sgd_failure is None:
                # A failure outside the training-loop fence (final publication,
                # final CP state capture) still reports the child's facts.
                cause: BaseException | None = error
                while cause is not None and not isinstance(cause, SgdProcessFailed):
                    cause = cause.__cause__ or cause.__context__
                if cause is not None:
                    self.sgd_failure = cause.facts
            if self.sgd_failure is not None:
                fields["sgd_failure"] = self.sgd_failure
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

    def _window_end(self, deadline: float, initialize_only: bool) -> str | None:
        """Why the training loop ended, when that is a normal window end: the absolute
        deadline/compute budget, the version budget, or the watcher's typed window-end
        stop. Anything else (operator STOP, a failure stop, a fence, unreadable stop
        facts) is None, and unresolved worlds then stay a failure (fail closed)."""
        if initialize_only or self.input_fenced:
            return None
        stop = self.stop_fact
        if stop is None:
            try:
                stop = self.exchange.stop_request(training_generation=self.generation)
            except Exception:  # noqa: BLE001 -- an unreadable stop fact is not a window end
                return None
        if stop is not None:
            return stop["reason"] if stop.get("reason") in WINDOW_END_STOPS else None
        stop_file = self.work / "STOP"
        if stop_file.exists():
            # The watcher writes its typed reason and budget into STOP before the socket
            # stop can arrive; an empty or foreign STOP (operator, signal) fails closed.
            try:
                written = json.loads(stop_file.read_bytes() or b"null")
            except (OSError, ValueError):
                return None
            budget = getattr(self.config, "operating_budget", None)
            reason = written.get("reason") if isinstance(written, dict) else None
            if (isinstance(reason, str) and reason in WINDOW_END_STOPS
                    and (budget is None or written.get("budget_id") == budget["id"])):
                return reason
            return None
        if self.version >= self.config.max_versions:
            return "max_versions"
        now = time.monotonic()
        if (now >= deadline
                or (self.selection_deadline is not None and now >= self.selection_deadline)
                or (self.deadline_at is not None and time.time() >= self.deadline_at - 600)):
            return "deadline"
        return None

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
                if self.sgd is not None:
                    self.sgd.check()
                if time.monotonic() >= stop_at:
                    stop_at = time.monotonic() + self.config.remote_stop_seconds
                    if on_tick is not None:
                        on_tick()
                    self._event("heartbeat", active_seconds=time.monotonic() - began,
                                updates=self.updates, archive_capacity=self.archive_capacity())
                    if self.shards is not None:
                        self.shards.health()
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
                    if self.ahead_while_waiting:
                        self._prepare_ahead()
                    if not self._prepare():
                        waiting = time.monotonic()
                        with self.condition:
                            self.condition.wait(0.1)
                        self.timing["waiting_for_data_seconds"] += time.monotonic() - waiting
                        continue
                self._prepare_ahead()
                self._step()
        except SgdProcessFailed as failure:
            # Original fence path: no new lease/CP; OPS restores the newest CP
            # under a new generation. No in-process or same-generation retry.
            self._sgd_fence(failure)
        finally:
            with (self.input_gate if self.wal is not None else nullcontext()), self.condition:
                self.newlease_stopped = True
                if self.wal is not None:
                    if not self.input_fenced:
                        self._discard_stopped_queue()
                        if self.shards is not None:
                            self._discard_stopped_shards()
                    self.binary_sources.stop()
                    self._notify_capacity(force=True)
            # Finish only the frozen dataset within the original compute and
            # absolute budget. Stop never selects another dataset.
            if self.wal is not None and not self.input_fenced:
                try:
                    while (self.active is not None and (
                            self.deadline_at is None or time.time() < self.deadline_at - 120)):
                        self._step()
                except SgdProcessFailed as failure:
                    self._sgd_fence(failure)
            training_elapsed = time.monotonic() - began
            self.window_end = self._window_end(deadline, initialize_only)
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
                if self.window_end is None:
                    # The socket stop normally lands during the drain; read the facts
                    # again (same fail-closed table) before classifying what is left.
                    self.window_end = self._window_end(deadline, initialize_only)
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
            "learning_configuration": {"lr": self._learning_rates()[0],
                "head_lr": self._learning_rates()[1],
                "optimizer_param_groups": self._optimizer_groups(),
                "weight_decay": self.config.weight_decay, "loss": asdict(self.config.loss),
                "segments": asdict(self.settings),
                "performance": self.config.frozen()["performance"]},
            "window_end": self.window_end,
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
        if self.sgd is not None:
            summary["sgd_process"] = {"implementation": "alternative:sgd_process",
                                      "exit": self.sgd.exit, "failure": self.sgd_failure}
        dropped = window_end_drop(self.window_end, self.queue.leases, self.actor_episodes,
                                  fenced=self.input_fenced)
        if dropped is not None:
            summary["dropped_at_window_end"] = dropped
        (self.work / "learner-summary.json").write_bytes(json_bytes(summary))
        if self.input_fenced:
            raise BoundaryError("rl_learner", "input_owner_fenced_restore_last_checkpoint")
        if (self.queue.leases or self.actor_episodes) and "dropped_at_window_end" not in summary:
            raise BoundaryError("rl_learner", "unresolved_worlds_after_drain")
        if self.actor_allocation_pending:
            raise BoundaryError("rl_learner", "unresolved_actor_allocations_after_drain")
        if self.sgd is not None and (self.sgd.exit or {}).get("exitcode") != 0:
            raise BoundaryError("rl_learner", "sgd_process_shutdown_failed")
        return summary
