"""CPU S0 teacher imitation; four actual observation writes per TBPTT chunk.

Score-only N rows remain losses. Unlabelled observations still advance W.
Chunk losses share one backward; detaching W never invents a reset or a prefix.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import torch
from torch import Tensor
from torch.nn import functional as F

from spireagent.json_boundary import BoundaryError

from ..fullrun.structured_sequences import StructuredDataset, StructuredRun
from .structured_m2 import StructuredM2


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


@dataclass
class StructuredTrainingResult:
    model: StructuredM2
    optimizer_state: dict[str, Any]
    metrics: dict[str, Any]


DEFAULT_CONFIG = StructuredTrainingConfig()


def evaluate_runs(model: StructuredM2, runs: tuple[StructuredRun, ...]) -> dict[str, Any]:
    model.eval()
    losses = []
    correct = multi_correct = labels = multi_labels = advances = score_only = 0
    with torch.inference_mode():
        for run in runs:
            memory = model.initial_memory()
            for step in run.steps:
                if step.reset_before:
                    memory = model.initial_memory()
                entities = model.encode(step.frame)
                if step.advance:
                    memory = model.advance(entities, memory)
                    advances += 1
                if step.chosen_action_id is not None:
                    scores = model.score(step.frame, entities, memory)
                    target = step.frame.action_ids.index(step.chosen_action_id)
                    loss = F.cross_entropy(scores.unsqueeze(0), torch.tensor([target]))
                    losses.append(float(loss))
                    selected = int(scores.argmax())
                    labels += 1
                    correct += selected == target
                    score_only += not step.advance
                    if len(scores) > 1:
                        multi_labels += 1
                        multi_correct += selected == target
    return {
        "runs": len(runs),
        "source_groups": len({run.source_group for run in runs}),
        "observations": sum(len(run.steps) for run in runs),
        "advances": advances,
        "labels": labels,
        "multi_candidate_labels": multi_labels,
        "score_only_labels": score_only,
        "mean_loss": sum(losses) / len(losses) if losses else None,
        "teacher_accuracy": correct / labels if labels else None,
        "multi_candidate_teacher_accuracy": multi_correct / multi_labels if multi_labels else None,
    }


def train_structured_model(
    dataset: StructuredDataset,
    config: StructuredTrainingConfig = DEFAULT_CONFIG,
) -> StructuredTrainingResult:
    config.validate()
    if not isinstance(dataset, StructuredDataset):
        raise BoundaryError("structured_training", "typed_dataset_required")
    train = tuple(run for run in dataset.runs if run.split == "train")
    if not train or not any(
        step.chosen_action_id is not None for run in train for step in run.steps
    ):
        raise BoundaryError("structured_training", "train_choices_required")
    torch.set_num_threads(config.cpu_threads)
    model = StructuredM2(seed=config.seed)
    initial = evaluate_runs(model, train)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
    updates = 0
    label_uses = 0
    chunk_losses: list[float] = []
    no_label_chunks = 0
    budget_reached = False
    completed_epochs = 0
    for _epoch in range(config.epochs):
        for run in train:
            model.train()
            memory = model.initial_memory()
            losses: list[Tensor] = []
            advances = 0

            def flush(losses: list[Tensor] = losses) -> None:
                nonlocal updates, label_uses, memory, advances, no_label_chunks
                if losses:
                    loss = torch.stack(losses).mean()
                    if not bool(torch.isfinite(loss)):
                        raise BoundaryError("structured_training", "nonfinite_loss")
                    optimizer.zero_grad(set_to_none=True)
                    loss.backward()
                    if any(
                        parameter.grad is not None
                        and not bool(torch.isfinite(parameter.grad).all())
                        for parameter in model.parameters()
                    ):
                        raise BoundaryError("structured_training", "nonfinite_gradients")
                    optimizer.step()
                    model.validate_parameters()
                    updates += 1
                    label_uses += len(losses)
                    chunk_losses.append(float(loss.detach()))
                elif advances:
                    no_label_chunks += 1
                losses.clear()
                memory = memory.detach()
                advances = 0

            for step in run.steps:
                if (step.reset_before or step.advance and advances == config.tbptt_advances) and (
                    advances or losses
                ):
                    flush()
                    if updates >= config.max_updates:
                        budget_reached = True
                        break
                if step.reset_before:
                    memory = model.initial_memory()
                entities = model.encode(step.frame)
                if step.advance:
                    memory = model.advance(entities, memory)
                    advances += 1
                if step.chosen_action_id is not None:
                    scores = model.score(step.frame, entities, memory)
                    target = step.frame.action_ids.index(step.chosen_action_id)
                    losses.append(F.cross_entropy(scores.unsqueeze(0), torch.tensor([target])))
            if not budget_reached and (advances or losses):
                flush()
            if updates >= config.max_updates:
                budget_reached = True
            if budget_reached:
                break
        if budget_reached:
            break
        completed_epochs += 1
    partitions = {
        split: evaluate_runs(model, tuple(run for run in dataset.runs if run.split == split))
        for split in ("train", "dev", "test")
    }
    if updates == 0 or any(not math.isfinite(item) for item in chunk_losses):
        raise BoundaryError("structured_training", "no_valid_optimizer_update")
    metrics = {
        "optimizer_updates": updates,
        "label_uses": label_uses,
        "chunk_losses": chunk_losses,
        "unlabelled_chunks": no_label_chunks,
        "completed_epochs": completed_epochs,
        "budget_reached": budget_reached,
        "initial_train": initial,
        "partitions": partitions,
        "evaluation_scope": (
            "independent_run_test" if dataset.independent_test_eligible else "learning_smoke"
        ),
        "capsule_bytes_verified": dataset.capsules_verified,
        "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "claims": {"human": False, "optimal_teacher": False, "all_scene_quality": False},
    }
    return StructuredTrainingResult(model.eval(), optimizer.state_dict(), metrics)
