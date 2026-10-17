"""Training-only independent run lanes over the unchanged E/W/N graph.

Frames remain ragged and forward calls retain the original graph. One numerical
boundary joins the labelled lane chunks into one label-mean backward/AdamW step.
This does not promise vectorized GPU throughput or equivalence to sequential SGD.
"""

from __future__ import annotations

from typing import Any

import torch
from torch import Tensor
from torch.nn import functional as F

from spireagent.json_boundary import BoundaryError

from ..structured_workload_contracts import (
    ChunkPlan,
    StructuredBatchPolicy,
    batch_chunk_plans,
    batch_lane_cursors,
    batch_rounds,
)
from .structured_device import DeviceStructuredTrainingEngine
from .structured_engine import ChunkProgress, TrainingCursor
from .structured_m2 import WIDTH


class BatchStructuredTrainingEngine(DeviceStructuredTrainingEngine):
    def __init__(
        self, dataset: Any, config: Any, *, code_scope: str, identity: dict[str, Any], policy: Any
    ) -> None:
        self.batch_policy = StructuredBatchPolicy.from_dict(identity.get("batch_policy"))
        self.rounds = batch_rounds(dataset, config, self.batch_policy)
        super().__init__(dataset, config, code_scope=code_scope, identity=identity, policy=policy)
        self.memory = torch.stack(
            [self.model.initial_memory() for _ in range(self.batch_policy.lanes)]
        )

    def _chunk_plans(self) -> tuple[ChunkPlan, ...]:
        return batch_chunk_plans(self.dataset, self.config, self.batch_policy)

    def _cursor_for(self, epoch: int, chunk: int) -> TrainingCursor:
        # A batch boundary has several real run cursors, exposed in batch_lanes.
        return TrainingCursor(epoch, chunk, None, None)

    def _memory_shape(self) -> tuple[int, ...]:
        return (self.batch_policy.lanes, self.model.slots, WIDTH)

    def _expected_memory_run(self, chunk: int) -> None:
        return None

    def _lanes(self, epoch: int, chunk: int) -> list[dict[str, Any]]:
        return batch_lane_cursors(
            self.rounds, self.batch_policy.lanes, epoch, chunk, self.config.epochs
        )

    def _checkpoint_extension(self) -> dict[str, Any]:
        return {"batch_lanes": self._lanes(self.epoch, self.chunk)}

    def _validate_checkpoint_extension(self, value: dict[str, Any], epoch: int, chunk: int) -> None:
        expected = self._lanes(epoch, chunk)
        if value["batch_lanes"] != expected:
            raise BoundaryError("structured_batch", "lane_cursor_mismatch")
        memory = value["memory"]
        if not bool(torch.isfinite(memory).all()) or any(
            (lane["next_step"] is None or lane["next_step"] == 0) and bool(memory[index].any())
            for index, lane in enumerate(expected)
        ):
            raise BoundaryError("structured_batch", "inactive_or_initial_lane_memory")

    def advance_chunk(self) -> ChunkProgress:
        self._assert_live()
        if self.training_complete or self.phase != "train":
            raise BoundaryError("structured_batch", "training_exhausted")
        group = self.rounds[self.chunk]
        previous = self._lanes(self.epoch, self.chunk)
        memories = list(self.memory.unbind(0))
        losses: list[Tensor] = []
        self.model.train()
        try:
            for lane, plan in group:
                memory = memories[lane]
                for step in self.train[plan.run_index].steps[plan.start : plan.end]:
                    if step.reset_before:
                        memory = self.model.initial_memory()
                    entities = self.model.encode(step.frame)
                    if step.advance:
                        memory = self.model.advance(entities, memory)
                    if step.chosen_action_id is not None:
                        scores = self.model.score(step.frame, entities, memory)
                        target = step.frame.action_ids.index(step.chosen_action_id)
                        losses.append(
                            F.cross_entropy(
                                scores.unsqueeze(0), scores.new_tensor([target], dtype=torch.long)
                            )
                        )
                memories[lane] = memory.detach().clone()
            loss_value = None
            if losses:
                loss = torch.stack(losses).mean()
                if not bool(torch.isfinite(loss)):
                    raise BoundaryError("structured_batch", "nonfinite_loss")
                self.optimizer.zero_grad(set_to_none=True)
                loss.backward()
                if any(
                    parameter.grad is not None and not bool(torch.isfinite(parameter.grad).all())
                    for parameter in self.model.parameters()
                ):
                    raise BoundaryError("structured_batch", "nonfinite_gradients")
                self.optimizer.step()
                for index, parameter in enumerate(self.model.parameters()):
                    self.optimizer_participation[index] += parameter.grad is not None
                self.model.validate_parameters()
                loss_value = float(loss.detach())
                self.chunk_losses.append(loss_value)
            self.optimizer.zero_grad(set_to_none=True)
            aggregate = self.plans[self.chunk]
            self.updates += bool(losses)
            self.labels += len(losses)
            self.observations += aggregate.end
            self.advances += aggregate.advances
            self.unlabelled_chunks += not losses
            self.boundary += 1
            self.chunk += 1
            if self.chunk == len(self.plans):
                self.chunk = 0
                self.epoch += 1
            following = self._lanes(self.epoch, self.chunk)
            for lane, cursor in enumerate(following):
                if (
                    cursor["run_index"] != previous[lane]["run_index"]
                    or cursor["next_step"] is None
                    or cursor["next_step"] == 0
                ):
                    memories[lane] = self.model.initial_memory()
            self.memory = torch.stack(memories).detach().clone()
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

    def metrics(self) -> dict[str, Any]:
        return {
            **super().metrics(),
            "batch_policy": self.batch_policy.to_dict(),
            "batch_lanes": self._lanes(self.epoch, self.chunk),
        }


def remote_training_engine(
    dataset: Any, config: Any, *, code_scope: str, identity: dict[str, Any], policy: Any
) -> DeviceStructuredTrainingEngine:
    engine = (
        BatchStructuredTrainingEngine
        if "batch_policy" in identity
        else DeviceStructuredTrainingEngine
    )
    return engine(dataset, config, code_scope=code_scope, identity=identity, policy=policy)
