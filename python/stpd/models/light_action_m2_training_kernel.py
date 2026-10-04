"""Bounded numerical kernel for concrete light-action M2 episodes."""

from __future__ import annotations

import math

import torch
from torch import Tensor

from spireagent.json_boundary import BoundaryError

from .light_action_m2 import LIGHT_ACTION_M2_WIDTH, LightActionM2Scorer
from .light_action_m2_training_data import (
    LightActionM2TrainingEpisode,
    LightActionM2TrainingStep,
)
from .losses import listwise_rank_loss

MAX_EPISODE_STEPS = 64
MAX_CHUNK_STEPS = 32
MAX_EPISODE_TOKENS = 65_536
MAX_CHUNK_TOKENS = 65_536


def _action_tensor(values: tuple[int, ...], device: torch.device) -> Tensor:
    return torch.tensor(values, dtype=torch.long, device=device)


def _chunks(lengths: tuple[int, ...], steps: int, tokens: int) -> tuple[tuple[int, int], ...]:
    result: list[tuple[int, int]] = []
    start = used = 0
    for index, length in enumerate(lengths):
        if index > start and (index - start >= steps or used + length > tokens):
            result.append((start, index))
            start, used = index, 0
        used += length
    result.append((start, len(lengths)))
    return tuple(result)


def _preflight(
    model: LightActionM2Scorer, episode: LightActionM2TrainingEpisode, *, gradient_clip: float
) -> tuple[tuple[int, ...], int]:
    if (
        not isinstance(model, LightActionM2Scorer)
        or not isinstance(episode, LightActionM2TrainingEpisode)
        or not isinstance(episode.episode_id, str)
        or not episode.episode_id
        or not isinstance(episode.steps, tuple)
        or not episode.steps
        or type(episode.slots) is not int
        or episode.slots not in (1, 8)
        or type(episode.reset_each_step) is not bool
        or episode.slots != model.slots
        or episode.reset_each_step != model.reset_each_step
    ):
        raise BoundaryError("light_action_m2_training_kernel", "invalid_episode")
    limits = (
        episode.max_episode_steps,
        episode.max_chunk_steps,
        episode.max_episode_tokens,
        episode.max_chunk_tokens,
    )
    if (
        any(type(value) is not int for value in limits)
        or not 1 <= episode.max_episode_steps <= MAX_EPISODE_STEPS
        or not 1 <= episode.max_chunk_steps <= MAX_CHUNK_STEPS
        or not 1 <= episode.max_episode_tokens <= MAX_EPISODE_TOKENS
        or not 1 <= episode.max_chunk_tokens <= MAX_CHUNK_TOKENS
        or len(episode.steps) > episode.max_episode_steps
        or type(gradient_clip) not in {int, float}
        or not math.isfinite(gradient_clip)
        or gradient_clip <= 0
    ):
        raise BoundaryError("light_action_m2_training_kernel", "invalid_training_limits")
    device = model.write_queries.device
    lengths: list[int] = []
    total = labels = 0
    for position, step in enumerate(episode.steps):
        if (
            not isinstance(step, LightActionM2TrainingStep)
            or type(step.position) is not int
            or step.position != position
            or type(step.reset_before) is not bool
            or step.reset_before != (position == 0)
            or not isinstance(step.page, tuple)
            or not step.page
            or any(type(token) is not int for token in step.page)
            or len(step.page) > model.core.max_tokens
            or not isinstance(step.action_ids, tuple)
            or not step.action_ids
            or any(not isinstance(key, str) or not key for key in step.action_ids)
            or len(set(step.action_ids)) != len(step.action_ids)
            or not isinstance(step.byte_actions, tuple)
            or len(step.byte_actions) != len(step.action_ids)
            or any(
                not isinstance(action, tuple) or any(type(token) is not int for token in action)
                for action in step.byte_actions
            )
        ):
            raise BoundaryError("light_action_m2_training_kernel", "invalid_step_binding")
        if step.target_action_id is not None:
            if step.action_ids.count(step.target_action_id) != 1:
                raise BoundaryError("light_action_m2_training_kernel", "target_binding_mismatch")
            labels += 1
        page = torch.tensor(step.page, dtype=torch.long, device=device)
        model.core.validate_tokens(page)
        for action in step.byte_actions:
            model._validate_action(_action_tensor(action, device), device)
        if step.previous_actual_action is not None:
            if not isinstance(step.previous_actual_action, tuple) or any(
                type(token) is not int for token in step.previous_actual_action
            ):
                raise BoundaryError("light_action_m2_training_kernel", "invalid_previous_action")
            model._validate_action(_action_tensor(step.previous_actual_action, device), device)
        length = len(step.page) + sum(map(len, step.byte_actions))
        if step.previous_actual_action is not None:
            length += len(step.previous_actual_action)
        if length > episode.max_chunk_tokens:
            raise BoundaryError("light_action_m2_training_kernel", "step_chunk_budget_exceeded")
        total += length
        if total > episode.max_episode_tokens:
            raise BoundaryError("light_action_m2_training_kernel", "episode_input_budget_exceeded")
        lengths.append(length)
    if labels == 0:
        raise BoundaryError("light_action_m2_training_kernel", "episode_has_no_supervised_label")
    return tuple(lengths), labels


def train_light_action_m2_training_episode(
    model: LightActionM2Scorer,
    optimizer: torch.optim.Optimizer,
    episode: LightActionM2TrainingEpisode,
    *,
    gradient_clip: float = 1.0,
) -> float:
    """Preflight all rows, write every page once, and train only labeled rows."""
    lengths, label_count = _preflight(model, episode, gradient_clip=gradient_clip)
    parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer_parameters = [p for group in optimizer.param_groups for p in group["params"]]
    if (
        not parameters
        or len(parameters) != len(optimizer_parameters)
        or {id(p) for p in parameters} != {id(p) for p in optimizer_parameters}
    ):
        raise BoundaryError("light_action_m2_training_kernel", "optimizer_model_mismatch")
    model.train()
    memory = model.initial_memory()
    total_loss = 0.0
    for start, end in _chunks(lengths, episode.max_chunk_steps, episode.max_chunk_tokens):
        losses: list[Tensor] = []
        for step in episode.steps[start:end]:
            page = torch.tensor(step.page, dtype=torch.long, device=memory.device)
            actions = tuple(_action_tensor(value, memory.device) for value in step.byte_actions)
            previous = (
                None
                if step.previous_actual_action is None
                else _action_tensor(step.previous_actual_action, memory.device)
            )
            scores, memory = model.step(
                page,
                actions,
                memory,
                previous_actual_action=previous,
                public_feedback=None,
                reset_before=step.reset_before,
            )
            if step.target_action_id is not None:
                losses.append(
                    listwise_rank_loss(scores, step.action_ids.index(step.target_action_id))
                )
        if losses:
            chunk_loss = torch.stack(losses).mean()
            if not bool(torch.isfinite(chunk_loss)):
                raise BoundaryError("light_action_m2_training_kernel", "nonfinite_loss")
            optimizer.zero_grad(set_to_none=True)
            (chunk_loss * (len(losses) / label_count)).backward()
            torch.nn.utils.clip_grad_norm_(parameters, gradient_clip, error_if_nonfinite=True)
            optimizer.step()
            if any(not bool(torch.isfinite(parameter).all()) for parameter in parameters):
                raise BoundaryError("light_action_m2_training_kernel", "nonfinite_parameter")
            total_loss += float(chunk_loss.detach()) * len(losses)
        # A declared TBPTT boundary detaches even if this chunk only reconstructed
        # memory. No optimizer update is made for a chunk without labels.
        memory = memory.detach()
    if memory.shape != (episode.slots, LIGHT_ACTION_M2_WIDTH):
        raise BoundaryError("light_action_m2_training_kernel", "memory_shape_mismatch")
    return total_loss / label_count
