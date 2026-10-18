"""Pure trusted recipe and application-to-worker contracts; no tensor runtime imports.

Applications own operation authority, writer reconciliation, and source permission.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from spireagent.json_boundary import BoundaryError, digest, json_bytes, object_fields

from .structured_code_scope import (
    INFERENCE_SCOPE,
    LEGACY_SCOPE,
    SCOPED_CHECKPOINT_SCHEMA,
    SCOPED_PACKAGE_SCHEMA,
    SCOPED_RUN_SCHEMA,
    TRAINING_SCOPE,
)

if TYPE_CHECKING:
    from .fullrun.structured_sequences import StructuredDataset


@dataclass(frozen=True)
class StructuredTrainingConfig:
    epochs: int = 1
    learning_rate: float = 1e-3
    seed: int = 0
    tbptt_advances: int = 4
    cpu_threads: int = 2
    max_updates: int = 1000

    def validate(self) -> None:
        if (
            type(self.epochs) is not int
            or not 1 <= self.epochs <= 100
            or self.learning_rate != 1e-3
            or type(self.seed) is not int
            or self.seed != 0
            or type(self.tbptt_advances) is not int
            or self.tbptt_advances != 4
            or type(self.cpu_threads) is not int
            or self.cpu_threads != 2
            or type(self.max_updates) is not int
            or not 1 <= self.max_updates <= 10000
        ):
            raise BoundaryError("structured_training", "unsupported_fixed_recipe")


@dataclass(frozen=True)
class StructuredBatchPolicy:
    """Explicit numerical execution; unrelated to native deployment history policy."""

    lanes: int = 1

    def __post_init__(self) -> None:
        if type(self.lanes) is not int or not 1 <= self.lanes <= 32:
            raise BoundaryError("structured_batch", "independent_lane_count_required")

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "stpd/structured-independent-batch-policy-v1", "lanes": self.lanes,
                "schedule": "independent_run_waves", "loss_reduction": "label_mean"}

    @classmethod
    def from_dict(cls, value: object) -> StructuredBatchPolicy:
        row = object_fields(value, {"schema", "lanes", "schedule", "loss_reduction"},
                            "structured_batch")
        policy = cls(row["lanes"])
        if row != policy.to_dict():
            raise BoundaryError("structured_batch", "unsupported_batch_execution_policy")
        return policy


REQUEST_SCHEMA = "stpd/structured-workload-request-v1"
MAX_RESUME_ANCESTRY = 32


class AttemptAuthority(Protocol):
    def assert_current(self, run_id: str, operation_id: str, attempt_id: str) -> None:
        """Raise when this application-owned writer is no longer authorized."""
        ...

    def authorize_resume(self, run_id: str, attempt_id: str, checkpoint_id: str) -> None:
        """Confirm exact checkpoint selection and prior writer terminal reconciliation."""
        ...


class ExecutionControl(Protocol):
    def requested_action(self) -> Literal["continue", "pause", "cancel"]: ...


@dataclass(frozen=True)
class StructuredWorkloadRequest:
    run_id: str
    training_input_id: str
    operation_id: str
    attempt_id: str
    mode: str = "start"
    resume_checkpoint_id: str | None = None
    checkpoint_every_boundaries: int = 1

    def validate(self) -> None:
        digest(self.run_id, "structured_workload.run_id")
        digest(self.training_input_id, "structured_workload.training_input_id")
        digest(self.operation_id, "structured_workload.operation_id", length=32)
        digest(self.attempt_id, "structured_workload.attempt_id", length=32)
        if self.mode not in {"start", "resume", "reconcile"}:
            raise BoundaryError("structured_workload", "unsupported_mode")
        if self.mode == "resume":
            digest(self.resume_checkpoint_id, "structured_workload.resume_checkpoint_id")
        elif self.resume_checkpoint_id is not None:
            raise BoundaryError("structured_workload", "unexpected_checkpoint")
        if (
            type(self.checkpoint_every_boundaries) is not int
            or not 1 <= self.checkpoint_every_boundaries <= 100
        ):
            raise BoundaryError("structured_workload", "checkpoint_interval")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"schema": REQUEST_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, value: object) -> StructuredWorkloadRequest:
        request = object_fields(
            value,
            {
                "schema",
                "run_id",
                "training_input_id",
                "operation_id",
                "attempt_id",
                "mode",
                "resume_checkpoint_id",
                "checkpoint_every_boundaries",
            },
            "structured_workload",
        )
        if request["schema"] != REQUEST_SCHEMA:
            raise BoundaryError("structured_workload", "unsupported_schema")
        result = cls(**{key: item for key, item in request.items() if key != "schema"})
        result.validate()
        return result


@dataclass(frozen=True)
class StructuredExecutionResult:
    state: str
    run_id: str
    attempt_id: str
    checkpoint_id: str | None
    result_id: str | None
    model_id: str | None
    event_step: int
    optimizer_updates: int
    attempt_seconds: float


def structured_workload_capabilities() -> dict[str, Any]:
    """Describe this installed fixed recipe without importing its numerical runtime."""
    return {
        "recipe": "structured-m2-cpu-v2",
        "request_schema": REQUEST_SCHEMA,
        "config_defaults": asdict(StructuredTrainingConfig()),
        "config_bounds": {"epochs": [1, 100], "max_updates": [1, 10000]},
        "fixed_config_fields": ["learning_rate", "seed", "tbptt_advances", "cpu_threads"],
        "device": "cpu",
        "resume": "explicit_verified_checkpoint_and_terminal_prior_writer",
        "control": ["pause", "cancel"],
        "control_boundary": "completed_tbptt_chunk_or_complete_evaluation_pass",
        "checkpoint_schema": "stpd/structured-m2-training-checkpoint-v2",
        "legacy_v1": "final_only_not_resumable",
        "scoped_identity": {
            "selection": "explicit_trusted_opt_in",
            "default_scope": LEGACY_SCOPE,
            "inference_scope": INFERENCE_SCOPE,
            "training_scope": TRAINING_SCOPE,
            "package_schema": SCOPED_PACKAGE_SCHEMA,
            "run_schema": SCOPED_RUN_SCHEMA,
            "checkpoint_schema": SCOPED_CHECKPOINT_SCHEMA,
            "attempt_producer": "explicit_current_source_provenance",
            "legacy_migration": False,
        },
        "automatic_retry": False,
        "max_resume_ancestry": MAX_RESUME_ANCESTRY,
        "checkpoint_every_boundaries_bounds": [1, 100],
    }


@dataclass(frozen=True)
class ChunkPlan:
    run_index: int
    start: int
    end: int
    advances: int
    labels: int


def chunk_plans(dataset: StructuredDataset, config: StructuredTrainingConfig
                ) -> tuple[ChunkPlan, ...]:
    result = []
    for run_index, run in enumerate(run for run in dataset.runs if run.split == "train"):
        start = advances = labels = 0
        for index, step in enumerate(run.steps):
            if index > start and (
                step.reset_before or step.advance and advances == config.tbptt_advances
            ):
                result.append(ChunkPlan(run_index, start, index, advances, labels))
                start, advances, labels = index, 0, 0
            advances += step.advance
            labels += step.chosen_action_id is not None
        result.append(ChunkPlan(run_index, start, len(run.steps), advances, labels))
    return tuple(result)


def batch_rounds(dataset: StructuredDataset, config: StructuredTrainingConfig,
                 policy: StructuredBatchPolicy) -> tuple[tuple[tuple[int, ChunkPlan], ...], ...]:
    """Fixed independent run lanes; exhausted/padded lanes perform no observation."""
    grouped: dict[int, list[ChunkPlan]] = {}
    for plan in chunk_plans(dataset, config):
        grouped.setdefault(plan.run_index, []).append(plan)
    rounds = []
    for first in range(0, len(grouped), policy.lanes):
        lanes = [grouped[index] for index in range(first, min(first + policy.lanes, len(grouped)))]
        for position in range(max(map(len, lanes))):
            rounds.append(tuple((lane, plans[position]) for lane, plans in enumerate(lanes)
                                if position < len(plans)))
    return tuple(rounds)


def batch_chunk_plans(dataset: StructuredDataset, config: StructuredTrainingConfig,
                      policy: StructuredBatchPolicy) -> tuple[ChunkPlan, ...]:
    # Aggregate progress only. Real run/start bindings remain in batch_rounds.
    return tuple(ChunkPlan(-1, 0, sum(plan.end - plan.start for _, plan in group),
                           sum(plan.advances for _, plan in group),
                           sum(plan.labels for _, plan in group))
                 for group in batch_rounds(dataset, config, policy))


def batch_lane_cursors(rounds: tuple[tuple[tuple[int, ChunkPlan], ...], ...],
                       lanes: int, epoch: int, chunk: int, epochs: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = [{"run_index": None, "next_step": None} for _ in range(lanes)]
    if epoch < epochs:
        for lane, plan in rounds[chunk]:
            result[lane] = {"run_index": plan.run_index, "next_step": plan.start}
    return result


def frozen_training_plan(dataset: Any, config: StructuredTrainingConfig, *,
                         data_counts: dict[str, Any], distinct_N_basis: str,
                         batch_policy: StructuredBatchPolicy | None = None) -> dict[str, Any]:
    """Describe the existing fixed-four traversal, without selecting a new plan."""
    config.validate()
    plans = (chunk_plans(dataset, config) if batch_policy is None
             else batch_chunk_plans(dataset, config, batch_policy))
    updates_per_epoch = sum(plan.labels > 0 for plan in plans)
    planned_updates = min(config.epochs * updates_per_epoch, config.max_updates)
    boundaries = updates = completed_epochs = 0
    for _epoch in range(config.epochs):
        for position, plan in enumerate(plans):
            if updates >= config.max_updates:
                break
            boundaries += 1
            updates += plan.labels > 0
            if position == len(plans) - 1:
                completed_epochs += 1
        if updates >= config.max_updates:
            break
    body = {
        "schema":"stpd/structured-training-plan-v1", "source_sha256":dataset.source_sha256,
        "tbptt_advances":config.tbptt_advances,
        **({"batch_policy":batch_policy.to_dict()} if batch_policy is not None else {}),
        "updates_per_epoch":updates_per_epoch, "epochs":config.epochs,
        "update_cap":config.max_updates, "planned_optimizer_updates":planned_updates,
        "chunk_count_per_epoch":len(plans), "planned_boundaries":boundaries,
        "data_counts":data_counts,
        "whole_cohort_target":{"scope":"one_full_frozen_selected_TRAIN_pass",
                               "planned":completed_epochs >= 1},
        "distinct_N_basis":distinct_N_basis,
        "progress_basis":"actual_cumulative_optimizer_updates_label_uses_observations_boundaries",
    }
    return {**body, "plan_sha256":hashlib.sha256(json_bytes(body)).hexdigest()}


def accepted_counters(engine: Any) -> dict[str, int]:
    """Publish counters already maintained and checkpointed by the same engine."""
    return {"optimizer_updates":engine.updates, "label_uses":engine.labels,
        "observations":engine.observations, "boundaries":engine.boundary,
        "epoch":engine.epoch, "whole_cohort_passes":engine.epoch}
