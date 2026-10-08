"""Resumable CPU structured computation at completed four-advance TBPTT boundaries.

This owner has no process replacement, permission, operation journal or publication
authority. A checkpoint is a detached numerical state, never an autograd graph.
"""

from __future__ import annotations

import math
import platform
import random
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import torch
from torch import Tensor
from torch.nn import functional as F

from spireagent.json_boundary import BoundaryError, object_fields

from ..canonical import semantic_hash
from ..fullrun.structured_inputs import INPUT_ID, PROJECTION_VERSION
from ..fullrun.structured_sequences import StructuredDataset
from ..policy.structured_export import code_digest
from ..workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from .structured_m2 import GRAPH_ID, SLOTS, WIDTH, StructuredM2
from .structured_training import StructuredTrainingConfig, evaluate_runs

CHECKPOINT_SCHEMA = "stpd/structured-m2-training-checkpoint-v2"
ENGINE_VERSION = "structured-tbptt-boundaries-v1"
MAX_CHECKPOINT_BYTES = 64 * 1024 * 1024


def runtime_identity() -> dict[str, Any]:
    return {
        "torch": str(torch.__version__),
        "numpy": str(np.__version__),
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "device": "cpu",
        "dtype": "float32",
        "threads": torch.get_num_threads(),
        "interop_threads": torch.get_num_interop_threads(),
        "deterministic": torch.are_deterministic_algorithms_enabled(),
        "mkldnn": torch.backends.mkldnn.enabled,
    }


def execution_identity(
    dataset: StructuredDataset, config: StructuredTrainingConfig
) -> dict[str, Any]:
    config.validate()
    if torch.get_num_threads() != config.cpu_threads:
        raise BoundaryError("structured_engine", "cpu_thread_identity_mismatch")
    return {
        "engine": ENGINE_VERSION,
        "code_sha256": code_digest(),
        "runtime": runtime_identity(),
        "source_sha256": dataset.source_sha256,
        "config": asdict(config),
        "graph_id": GRAPH_ID,
        "projection": {"id": INPUT_ID, "version": PROJECTION_VERSION, "I": False, "F": False},
        "event_order_sha256": semantic_hash(
            [
                {
                    "run_id": run.run_id,
                    "group": run.source_group,
                    "split": run.split,
                    "events": [
                        (
                            step.position,
                            step.capture_id,
                            step.capsule_sha256,
                            step.frame.state_digest,
                            step.frame.candidate_digest,
                            step.chosen_action_id,
                            step.reset_before,
                            step.advance,
                        )
                        for step in run.steps
                    ],
                }
                for run in dataset.runs
            ]
        ),
    }


@dataclass(frozen=True)
class ChunkPlan:
    run_index: int
    start: int
    end: int
    advances: int
    labels: int


@dataclass(frozen=True)
class TrainingCursor:
    epoch: int
    chunk: int
    run_index: int | None
    next_step: int | None


@dataclass(frozen=True)
class ChunkProgress:
    boundary: int
    cursor: TrainingCursor
    optimizer_updates: int
    label_uses: int
    observations: int
    advances: int
    chunk_loss: float | None
    training_complete: bool


def _plans(dataset: StructuredDataset, config: StructuredTrainingConfig) -> tuple[ChunkPlan, ...]:
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


class StructuredTrainingEngine:
    def __init__(self, dataset: StructuredDataset, config: StructuredTrainingConfig) -> None:
        config.validate()
        if not isinstance(dataset, StructuredDataset):
            raise BoundaryError("structured_engine", "typed_dataset_required")
        self.dataset, self.config = dataset, config
        self.train = tuple(run for run in dataset.runs if run.split == "train")
        if not self.train or not any(
            step.chosen_action_id is not None for run in self.train for step in run.steps
        ):
            raise BoundaryError("structured_engine", "train_choices_required")
        self.identity = execution_identity(dataset, config)
        self.plans = _plans(dataset, config)
        self.model = StructuredM2(seed=config.seed)
        self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=config.learning_rate)
        self.parameter_names = tuple(name for name, _ in self.model.named_parameters())
        # AdamW initializes state lazily, only for parameters with an actual gradient.
        self.optimizer_participation = [0] * len(self.parameter_names)
        self.initial_train = evaluate_runs(self.model, self.train)
        self.memory = self.model.initial_memory()
        self.memory_run: int | None = None
        self.epoch = self.chunk = self.boundary = self.updates = self.labels = 0
        self.observations = self.advances = self.unlabelled_chunks = 0
        self.chunk_losses: list[float] = []
        self.partitions: dict[str, Any] = {}
        self.phase = "train"
        self.failed = False

    @property
    def training_complete(self) -> bool:
        return self.epoch >= self.config.epochs or self.updates >= self.config.max_updates

    @property
    def cursor(self) -> TrainingCursor:
        if self.epoch >= self.config.epochs:
            return TrainingCursor(self.epoch, self.chunk, None, None)
        plan = self.plans[self.chunk]
        return TrainingCursor(self.epoch, self.chunk, plan.run_index, plan.start)

    def _assert_live(self) -> None:
        if self.failed:
            raise BoundaryError("structured_engine", "failed_engine_requires_restore")
        if execution_identity(self.dataset, self.config) != self.identity:
            self.failed = True
            raise BoundaryError("structured_engine", "runtime_code_or_input_changed")

    def advance_chunk(self) -> ChunkProgress:
        self._assert_live()
        if self.training_complete or self.phase != "train":
            raise BoundaryError("structured_engine", "training_exhausted")
        plan = self.plans[self.chunk]
        run = self.train[plan.run_index]
        memory = self.model.initial_memory() if self.memory_run != plan.run_index else self.memory
        losses: list[Tensor] = []
        self.model.train()
        try:
            for step in run.steps[plan.start : plan.end]:
                if step.reset_before:
                    memory = self.model.initial_memory()
                entities = self.model.encode(step.frame)
                if step.advance:
                    memory = self.model.advance(entities, memory)
                if step.chosen_action_id is not None:
                    scores = self.model.score(step.frame, entities, memory)
                    target = step.frame.action_ids.index(step.chosen_action_id)
                    losses.append(F.cross_entropy(scores.unsqueeze(0), torch.tensor([target])))
            loss_value = None
            if losses:
                loss = torch.stack(losses).mean()
                if not bool(torch.isfinite(loss)):
                    raise BoundaryError("structured_engine", "nonfinite_loss")
                self.optimizer.zero_grad(set_to_none=True)
                loss.backward()
                if any(
                    parameter.grad is not None and not bool(torch.isfinite(parameter.grad).all())
                    for parameter in self.model.parameters()
                ):
                    raise BoundaryError("structured_engine", "nonfinite_gradients")
                self.optimizer.step()
                for index, parameter in enumerate(self.model.parameters()):
                    self.optimizer_participation[index] += parameter.grad is not None
                self.model.validate_parameters()
                loss_value = float(loss.detach())
            self.optimizer.zero_grad(set_to_none=True)
            self.memory = memory.detach().clone()
            self.memory_run = plan.run_index
            self.updates += bool(losses)
            self.labels += len(losses)
            self.observations += plan.end - plan.start
            self.advances += plan.advances
            self.unlabelled_chunks += not losses
            if loss_value is not None:
                self.chunk_losses.append(loss_value)
            self.boundary += 1
            self.chunk += 1
            if self.chunk == len(self.plans):
                self.epoch += 1
                self.chunk = 0
                self.memory = self.model.initial_memory()
                self.memory_run = None
            if self.training_complete:
                self.phase = "evaluation"
            return ChunkProgress(
                self.boundary,
                self.cursor,
                self.updates,
                self.labels,
                self.observations,
                self.advances,
                loss_value,
                self.training_complete,
            )
        except Exception:
            self.failed = True
            raise

    def evaluate(self) -> dict[str, Any]:
        self._assert_live()
        if not self.training_complete:
            raise BoundaryError("structured_engine", "training_not_complete")
        # Partition metrics become committed only after the complete fixed-weight pass.
        for split in ("train", "dev", "test"):
            if split not in self.partitions:
                self.partitions[split] = evaluate_runs(
                    self.model, tuple(run for run in self.dataset.runs if run.split == split)
                )
        self.phase = "publication"
        return self.metrics()

    def metrics(self) -> dict[str, Any]:
        return {
            "optimizer_updates": self.updates,
            "label_uses": self.labels,
            "chunk_losses": list(self.chunk_losses),
            "unlabelled_chunks": self.unlabelled_chunks,
            "completed_epochs": self.epoch,
            "budget_reached": self.updates >= self.config.max_updates,
            "initial_train": self.initial_train,
            "partitions": self.partitions,
            "evaluation_scope": "independent_run_test"
            if self.dataset.independent_test_eligible
            else "learning_smoke",
            "capsule_bytes_verified": self.dataset.capsules_verified,
            "parameter_count": sum(parameter.numel() for parameter in self.model.parameters()),
            "claims": {"human": False, "optimal_teacher": False, "all_scene_quality": False},
        }

    def checkpoint(self) -> bytes:
        self._assert_live()
        if self.memory.grad_fn is not None or any(
            parameter.grad is not None for parameter in self.model.parameters()
        ):
            raise BoundaryError("structured_checkpoint", "unsafe_boundary")
        numpy_rng = np.random.get_state()
        if not isinstance(numpy_rng, tuple):
            raise BoundaryError("structured_checkpoint", "unsupported_numpy_rng")
        payload = {
            "schema": CHECKPOINT_SCHEMA,
            "identity": self.identity,
            "parameter_names": self.parameter_names,
            "model": dict(self.model.state_dict()),
            "optimizer": self.optimizer.state_dict(),
            "optimizer_participation": list(self.optimizer_participation),
            "cursor": asdict(self.cursor),
            "boundary": self.boundary,
            "memory": self.memory,
            "memory_run": self.memory_run,
            "phase": self.phase,
            "counters": {
                "updates": self.updates,
                "labels": self.labels,
                "observations": self.observations,
                "advances": self.advances,
                "unlabelled_chunks": self.unlabelled_chunks,
            },
            "chunk_losses": self.chunk_losses,
            "initial_train": self.initial_train,
            "partitions": self.partitions,
            "rng": {
                "torch_cpu": torch.get_rng_state(),
                "python": random.getstate(),
                "numpy": (numpy_rng[0], torch.from_numpy(numpy_rng[1].copy()), *numpy_rng[2:]),
            },
        }
        raw = encode_checkpoint(payload)
        if len(raw) > MAX_CHECKPOINT_BYTES:
            raise BoundaryError("structured_checkpoint", "checkpoint_size_limit")
        return raw

    def restore(self, raw: bytes) -> None:
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_CHECKPOINT_BYTES:
            raise BoundaryError("structured_checkpoint", "checkpoint_size_limit")
        value = object_fields(
            decode_checkpoint(raw),
            {
                "schema",
                "identity",
                "parameter_names",
                "model",
                "optimizer",
                "optimizer_participation",
                "cursor",
                "boundary",
                "memory",
                "memory_run",
                "phase",
                "counters",
                "chunk_losses",
                "initial_train",
                "partitions",
                "rng",
            },
            "structured_checkpoint",
        )
        if (
            value["schema"] != CHECKPOINT_SCHEMA
            or value["identity"] != self.identity
            or execution_identity(self.dataset, self.config) != self.identity
            or value["parameter_names"] != self.parameter_names
        ):
            raise BoundaryError("structured_checkpoint", "exact_resume_identity_mismatch")
        cursor = object_fields(
            value["cursor"],
            {"epoch", "chunk", "run_index", "next_step"},
            "structured_checkpoint.cursor",
        )
        epoch, chunk = cursor["epoch"], cursor["chunk"]
        if (
            type(epoch) is not int
            or not 0 <= epoch <= self.config.epochs
            or type(chunk) is not int
            or not 0 <= chunk < len(self.plans)
            or epoch == self.config.epochs
            and chunk != 0
        ):
            raise BoundaryError("structured_checkpoint", "cursor_outside_plan")
        boundary = epoch * len(self.plans) + chunk
        expected_plan = None if epoch == self.config.epochs else self.plans[chunk]
        if (
            type(value["boundary"]) is not int
            or value["boundary"] != boundary
            or cursor["run_index"] != (None if expected_plan is None else expected_plan.run_index)
            or cursor["next_step"] != (None if expected_plan is None else expected_plan.start)
        ):
            raise BoundaryError("structured_checkpoint", "cursor_plan_mismatch")
        done = list(self.plans) * epoch + list(self.plans[:chunk])
        expected_counters = {
            "updates": sum(plan.labels > 0 for plan in done),
            "labels": sum(plan.labels for plan in done),
            "observations": sum(plan.end - plan.start for plan in done),
            "advances": sum(plan.advances for plan in done),
            "unlabelled_chunks": sum(plan.labels == 0 for plan in done),
        }
        if (
            not isinstance(value["counters"], dict)
            or any(type(counter) is not int for counter in value["counters"].values())
            or value["counters"] != expected_counters
            or expected_counters["updates"] > self.config.max_updates
        ):
            raise BoundaryError("structured_checkpoint", "cumulative_counter_mismatch")
        complete = (
            epoch == self.config.epochs or expected_counters["updates"] == self.config.max_updates
        )
        if value["phase"] not in ({"evaluation", "publication"} if complete else {"train"}):
            raise BoundaryError("structured_checkpoint", "phase_plan_mismatch")
        losses = value["chunk_losses"]
        if (
            not isinstance(losses, list)
            or len(losses) != expected_counters["updates"]
            or any(type(loss) not in {int, float} or not math.isfinite(loss) for loss in losses)
        ):
            raise BoundaryError("structured_checkpoint", "loss_history_mismatch")
        expected = self.model.state_dict()
        weights = value["model"]
        if (
            not isinstance(weights, dict)
            or set(weights) != set(expected)
            or any(
                not isinstance(weights[name], Tensor)
                or weights[name].shape != tensor.shape
                or weights[name].dtype != tensor.dtype
                for name, tensor in expected.items()
            )
        ):
            raise BoundaryError("structured_checkpoint", "model_tensor_mismatch")
        memory = value["memory"]
        if (
            not isinstance(memory, Tensor)
            or memory.shape != (SLOTS, WIDTH)
            or memory.dtype != torch.float32
            or memory.device.type != "cpu"
        ):
            raise BoundaryError("structured_checkpoint", "memory_tensor_mismatch")
        expected_memory_run = None if chunk == 0 else self.plans[chunk - 1].run_index
        if value["memory_run"] != expected_memory_run:
            raise BoundaryError("structured_checkpoint", "memory_cursor_mismatch")
        optimizer = object_fields(
            value["optimizer"], {"state", "param_groups"}, "structured_checkpoint.optimizer"
        )
        if optimizer["param_groups"] != self.optimizer.state_dict()[
            "param_groups"
        ] or not isinstance(optimizer["state"], dict):
            raise BoundaryError("structured_checkpoint", "optimizer_configuration_mismatch")
        if bool(optimizer["state"]) != bool(expected_counters["updates"]):
            raise BoundaryError("structured_checkpoint", "optimizer_update_state_missing")
        parameters = tuple(self.model.parameters())
        participation = value["optimizer_participation"]
        if (
            not isinstance(participation, list)
            or len(participation) != len(parameters)
            or any(
                type(count) is not int or not 0 <= count <= expected_counters["updates"]
                for count in participation
            )
        ):
            raise BoundaryError("structured_checkpoint", "optimizer_participation_mismatch")
        expected_inventory = {index for index, count in enumerate(participation) if count > 0}
        if set(optimizer["state"]) != expected_inventory:
            raise BoundaryError("structured_checkpoint", "optimizer_state_inventory_mismatch")
        for index, state in optimizer["state"].items():
            if type(index) is not int or not 0 <= index < len(parameters):
                raise BoundaryError("structured_checkpoint", "optimizer_parameter_binding")
            node = object_fields(
                state, {"step", "exp_avg", "exp_avg_sq"}, "structured_checkpoint.optimizer_state"
            )
            for role in ("exp_avg", "exp_avg_sq"):
                if (
                    not isinstance(node[role], Tensor)
                    or node[role].shape != parameters[index].shape
                    or node[role].dtype != torch.float32
                ):
                    raise BoundaryError("structured_checkpoint", "optimizer_tensor_mismatch")
            step = node["step"]
            if (
                not isinstance(step, Tensor)
                or step.shape != ()
                or step.dtype != torch.float32
                or float(step) != participation[index]
            ):
                raise BoundaryError("structured_checkpoint", "optimizer_step_mismatch")
        partitions = value["partitions"]
        if (
            not isinstance(partitions, dict)
            or set(partitions) - {"train", "dev", "test"}
            or value["phase"] == "train"
            and partitions
            or value["phase"] == "publication"
            and set(partitions) != {"train", "dev", "test"}
            or value["initial_train"] != self.initial_train
        ):
            raise BoundaryError("structured_checkpoint", "evaluation_state_mismatch")
        rng = object_fields(
            value["rng"], {"torch_cpu", "python", "numpy"}, "structured_checkpoint.rng"
        )
        torch_rng = rng["torch_cpu"]
        numpy_rng = rng["numpy"]
        if (
            not isinstance(torch_rng, Tensor)
            or torch_rng.dtype != torch.uint8
            or torch_rng.shape != torch.get_rng_state().shape
            or not isinstance(numpy_rng, tuple)
            or len(numpy_rng) != 5
            or not isinstance(numpy_rng[1], Tensor)
            or numpy_rng[1].dtype != torch.uint32
        ):
            raise BoundaryError("structured_checkpoint", "rng_state_mismatch")
        # Validate RNG with isolated generators before touching process state.
        torch.Generator().set_state(torch_rng)
        random.Random().setstate(rng["python"])
        probe = np.random.RandomState()
        probe.set_state((numpy_rng[0], numpy_rng[1].numpy(), *numpy_rng[2:]))
        self.model.load_state_dict(weights, strict=True)
        self.optimizer.load_state_dict(optimizer)
        self.optimizer_participation = list(participation)
        self.optimizer.zero_grad(set_to_none=True)
        self.epoch, self.chunk, self.boundary = epoch, chunk, boundary
        self.memory, self.memory_run = memory.detach().clone(), value["memory_run"]
        self.updates, self.labels = expected_counters["updates"], expected_counters["labels"]
        self.observations, self.advances = (
            expected_counters["observations"],
            expected_counters["advances"],
        )
        self.unlabelled_chunks = expected_counters["unlabelled_chunks"]
        self.chunk_losses, self.partitions = list(losses), partitions
        self.phase, self.failed = value["phase"], False
        torch.set_rng_state(torch_rng)
        random.setstate(rng["python"])
        np.random.set_state((numpy_rng[0], numpy_rng[1].numpy(), *numpy_rng[2:]))
