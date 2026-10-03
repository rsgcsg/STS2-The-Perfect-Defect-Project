"""Observation-only M2 numerical windows over caller-qualified public decisions.

The caller owns admission, complete catalog provenance, chain boundaries, and
checkpointed cursor/memory. This module never partitions or resets a chain on
its own. Its one optimizer update is the mean listwise loss of this window.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import torch
from torch import Tensor

from spireagent.json_boundary import BoundaryError

from .light_action_m2 import LightActionM2Scorer
from .light_action_m2_training_data import LightActionM2TrainingStep
from .losses import listwise_rank_loss

MAX_PUBLIC_M2_WINDOW_STEPS = 8
MAX_PUBLIC_M2_WINDOW_TOKENS = 65_536
_BOUNDARY = "public_m2_window"


@dataclass(frozen=True)
class PublicM2WindowResult:
    memory: Tensor
    label_count: int
    loss_sum: float
    loss_mean: float
    optimizer_updates: int


def preflight_public_m2_window(
    model: LightActionM2Scorer,
    steps: tuple[LightActionM2TrainingStep, ...],
    memory: Tensor,
    *,
    chain_start: bool,
    max_window_steps: int = MAX_PUBLIC_M2_WINDOW_STEPS,
    max_window_tokens: int = MAX_PUBLIC_M2_WINDOW_TOKENS,
    require_targets: bool = True,
) -> tuple[int | None, ...]:
    """Validate the whole ordered window before a write, including optional eval rows.

    A source owner must establish that action_ids/byte_actions are the complete
    native-order public catalog. This function checks binding and finite model
    inputs, but cannot infer a missing action from a supplied tuple.
    """
    if (
        not isinstance(model, LightActionM2Scorer)
        or not isinstance(steps, tuple)
        or not steps
        or type(chain_start) is not bool
        or type(require_targets) is not bool
        or type(max_window_steps) is not int
        or not 1 <= max_window_steps <= MAX_PUBLIC_M2_WINDOW_STEPS
        or type(max_window_tokens) is not int
        or not 1 <= max_window_tokens <= MAX_PUBLIC_M2_WINDOW_TOKENS
        or len(steps) > max_window_steps
    ):
        raise BoundaryError(_BOUNDARY, "invalid_window")
    # A detached, finite value is the only legal cross-window history. Checking
    # memory here also covers core/model device and dtype agreement.
    model._validate_memory(memory)
    if memory.requires_grad or memory.grad_fn is not None:
        raise BoundaryError(_BOUNDARY, "attached_input_memory")

    device = memory.device
    target_indices: list[int | None] = []
    total_tokens = 0
    for offset, step in enumerate(steps):
        if (
            not isinstance(step, LightActionM2TrainingStep)
            or type(step.position) is not int
            or step.position < 0
            or step.position != steps[0].position + offset
            or type(step.reset_before) is not bool
            or step.reset_before != (chain_start and offset == 0)
            or step.previous_actual_action is not None
            or not isinstance(step.page, tuple)
            or not step.page
            or any(type(token) is not int for token in step.page)
            or not isinstance(step.action_ids, tuple)
            or not step.action_ids
            or any(not isinstance(key, str) or not key for key in step.action_ids)
            or len(set(step.action_ids)) != len(step.action_ids)
            or not isinstance(step.byte_actions, tuple)
            or len(step.byte_actions) != len(step.action_ids)
            or any(
                not isinstance(action, tuple)
                or any(type(token) is not int for token in action)
                for action in step.byte_actions
            )
        ):
            raise BoundaryError(_BOUNDARY, "invalid_step_binding")
        if step.target_action_id is None:
            if require_targets:
                raise BoundaryError(_BOUNDARY, "missing_target")
            target_indices.append(None)
        elif (
            not isinstance(step.target_action_id, str)
            or step.action_ids.count(step.target_action_id) != 1
        ):
            raise BoundaryError(_BOUNDARY, "target_binding_mismatch")
        else:
            target_indices.append(step.action_ids.index(step.target_action_id))

        page = torch.tensor(step.page, dtype=torch.long, device=device)
        model.core.validate_tokens(page)
        for action in step.byte_actions:
            model._validate_action(torch.tensor(action, dtype=torch.long, device=device), device)
        total_tokens += len(step.page) + sum(len(action) for action in step.byte_actions)
        if total_tokens > max_window_tokens:
            raise BoundaryError(_BOUNDARY, "window_input_budget_exceeded")
    return tuple(target_indices)


def _active_parameters(
    model: LightActionM2Scorer, optimizer: torch.optim.Optimizer
) -> list[Tensor]:
    if not isinstance(optimizer, torch.optim.Optimizer):
        raise BoundaryError(_BOUNDARY, "invalid_optimizer")
    parameters: list[Tensor] = list(model.parameters())
    # Public M2 is a scratch experiment: a frozen subset silently changes its
    # meaning even if the optimizer matches that subset.
    if not parameters or any(not parameter.requires_grad for parameter in parameters):
        raise BoundaryError(_BOUNDARY, "frozen_model_parameter")
    optimizer_parameters = [p for group in optimizer.param_groups for p in group["params"]]
    if (
        len(parameters) != len(optimizer_parameters)
        or len({id(p) for p in optimizer_parameters}) != len(optimizer_parameters)
        or {id(p) for p in parameters} != {id(p) for p in optimizer_parameters}
    ):
        raise BoundaryError(_BOUNDARY, "optimizer_model_mismatch")
    return parameters


def train_public_m2_window(
    model: LightActionM2Scorer,
    optimizer: torch.optim.Optimizer,
    steps: tuple[LightActionM2TrainingStep, ...],
    memory: Tensor,
    *,
    chain_start: bool,
    max_window_steps: int = MAX_PUBLIC_M2_WINDOW_STEPS,
    max_window_tokens: int = MAX_PUBLIC_M2_WINDOW_TOKENS,
    gradient_clip: float = 1.0,
) -> PublicM2WindowResult:
    """Run one complete, labeled TBPTT window and exactly one optimizer update."""
    target_indices = preflight_public_m2_window(
        model,
        steps,
        memory,
        chain_start=chain_start,
        max_window_steps=max_window_steps,
        max_window_tokens=max_window_tokens,
    )
    if (
        type(gradient_clip) not in {int, float}
        or not math.isfinite(gradient_clip)
        or gradient_clip <= 0
    ):
        raise BoundaryError(_BOUNDARY, "invalid_gradient_clip")
    parameters = _active_parameters(model, optimizer)

    model.train()
    losses: list[Tensor] = []
    for step, target_index in zip(steps, target_indices, strict=True):
        page = torch.tensor(step.page, dtype=torch.long, device=memory.device)
        actions = tuple(
            torch.tensor(action, dtype=torch.long, device=memory.device)
            for action in step.byte_actions
        )
        scores, memory = model.step(
            page,
            actions,
            memory,
            previous_actual_action=None,
            public_feedback=None,
            reset_before=step.reset_before,
        )
        assert target_index is not None  # preflight requires every target
        losses.append(listwise_rank_loss(scores, target_index))

    loss_values = torch.stack(losses)
    window_loss = loss_values.mean()
    if not bool(torch.isfinite(window_loss)):
        raise BoundaryError(_BOUNDARY, "nonfinite_loss")
    optimizer.zero_grad(set_to_none=True)
    window_loss.backward()
    torch.nn.utils.clip_grad_norm_(parameters, gradient_clip, error_if_nonfinite=True)
    optimizer.step()
    if any(not bool(torch.isfinite(parameter).all()) for parameter in parameters):
        raise BoundaryError(_BOUNDARY, "nonfinite_parameter")
    count = len(losses)
    mean = float(window_loss.detach())
    return PublicM2WindowResult(
        memory=memory.detach().clone(),
        label_count=count,
        loss_sum=float(loss_values.detach().sum()),
        loss_mean=mean,
        optimizer_updates=1,
    )
