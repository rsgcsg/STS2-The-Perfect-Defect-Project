"""Bounded training for projected light-action M2 episodes.

Fixed-chunk truncation carries forward memory values and detaches at optimizer
boundaries. This is a bounded, biased TBPTT procedure; it is not full-history
backpropagation or a convergence claim.
"""

from __future__ import annotations

import math

import torch
from torch import Tensor

from spireagent.json_boundary import BoundaryError

from ..fullrun.light_action_m2_sequences import (
    MAX_BPTT_STEPS,
    MAX_CHUNK_INPUT_TOKENS,
    MAX_EPISODE_INPUT_TOKENS,
    MAX_EPISODE_STEPS,
    LightActionM2SequenceConfig,
    LightActionM2SequenceEpisode,
)
from .light_action_m2 import LightActionM2Scorer
from .losses import listwise_rank_loss


def _action_tensor(values: tuple[int, ...], device: torch.device) -> Tensor:
    return torch.tensor(values, dtype=torch.long, device=device)


def _preflight(
    model: LightActionM2Scorer,
    episode: LightActionM2SequenceEpisode,
    *, max_chunk_steps: int,
    max_chunk_input_tokens: int,
    max_episode_input_tokens: int,
    gradient_clip: float,
) -> tuple[tuple[int, ...], int]:
    if (not isinstance(model, LightActionM2Scorer)
            or not isinstance(episode, LightActionM2SequenceEpisode)
            or not episode.episode_id or not episode.steps or episode.split != "train"
            or len(episode.steps) > MAX_EPISODE_STEPS):
        raise BoundaryError("light_action_m2_training", "invalid_episode")
    if (type(max_chunk_steps) is not int or not 1 <= max_chunk_steps <= MAX_BPTT_STEPS
            or type(max_chunk_input_tokens) is not int
            or not 1 <= max_chunk_input_tokens <= MAX_CHUNK_INPUT_TOKENS
            or type(max_episode_input_tokens) is not int
            or not 1 <= max_episode_input_tokens <= MAX_EPISODE_INPUT_TOKENS
            or type(gradient_clip) not in {int, float}
            or not math.isfinite(gradient_clip) or gradient_clip <= 0):
        raise BoundaryError("light_action_m2_training", "invalid_training_limits")
    config = episode.config
    if (not isinstance(config, LightActionM2SequenceConfig)
            or config.slots != model.slots
            or len(episode.steps) > config.max_episode_steps
            or max_chunk_steps > config.max_bptt_steps
            or max_chunk_input_tokens > config.max_chunk_input_tokens
            or max_episode_input_tokens > config.max_episode_input_tokens):
        raise BoundaryError("light_action_m2_training", "training_limits_exceed_sequence_identity")
    device = model.write_queries.device
    lengths: list[int] = []
    total = 0
    for position, step in enumerate(episode.steps):
        if (step.episode_id != episode.episode_id or step.position != position
                or step.reset_before != (position == 0) or step.split != episode.split
                or not step.action_ids or len(step.action_ids) != len(step.actions)
                or len(set(step.action_ids)) != len(step.action_ids)
                or step.target_action_id not in step.action_ids):
            raise BoundaryError("light_action_m2_training", "invalid_step_binding")
        if not step.page or len(step.page) > model.core.max_tokens:
            raise BoundaryError("light_action_m2_training", "invalid_page_tokens")
        page = torch.tensor(step.page, dtype=torch.long, device=device)
        model.core.validate_tokens(page)
        for action in step.actions:
            model._validate_action(_action_tensor(action, device), device)
        if step.previous_actual_action is not None:
            model._validate_action(_action_tensor(step.previous_actual_action, device), device)
        length = (len(step.page) + sum(map(len, step.actions))
                  + (0 if step.previous_actual_action is None
                     else len(step.previous_actual_action)))
        if length > max_chunk_input_tokens:
            raise BoundaryError("light_action_m2_training", "step_chunk_budget_exceeded")
        total += length
        if total > max_episode_input_tokens:
            raise BoundaryError("light_action_m2_training", "episode_input_budget_exceeded")
        lengths.append(length)
    return tuple(lengths), len(episode.steps)


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


def train_light_action_m2_episode(
    model: LightActionM2Scorer,
    optimizer: torch.optim.Optimizer,
    episode: LightActionM2SequenceEpisode,
    *,
    max_chunk_steps: int | None = None,
    max_chunk_input_tokens: int | None = None,
    max_episode_input_tokens: int | None = None,
    gradient_clip: float = 1.0,
) -> float:
    """Train one complete projected episode using fixed-chunk truncated BPTT."""
    if not isinstance(episode, LightActionM2SequenceEpisode):
        raise BoundaryError("light_action_m2_training", "invalid_episode")
    config = episode.config
    max_chunk_steps = config.max_bptt_steps if max_chunk_steps is None else max_chunk_steps
    max_chunk_input_tokens = (config.max_chunk_input_tokens if max_chunk_input_tokens is None
                              else max_chunk_input_tokens)
    max_episode_input_tokens = (
        config.max_episode_input_tokens if max_episode_input_tokens is None
        else max_episode_input_tokens
    )
    lengths, label_count = _preflight(
        model, episode, max_chunk_steps=max_chunk_steps,
        max_chunk_input_tokens=max_chunk_input_tokens,
        max_episode_input_tokens=max_episode_input_tokens,
        gradient_clip=gradient_clip,
    )
    parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer_parameters = [p for group in optimizer.param_groups for p in group["params"]]
    if (not parameters or len(parameters) != len(optimizer_parameters)
            or {id(p) for p in parameters} != {id(p) for p in optimizer_parameters}):
        raise BoundaryError("light_action_m2_training", "optimizer_model_mismatch")
    model.train()
    memory = model.initial_memory()
    total_loss = 0.0
    for start, end in _chunks(lengths, max_chunk_steps, max_chunk_input_tokens):
        losses: list[Tensor] = []
        chunk = episode.steps[start:end]
        for step in chunk:
            page = torch.tensor(step.page, dtype=torch.long, device=memory.device)
            actions = tuple(_action_tensor(value, memory.device) for value in step.actions)
            previous = (None if step.previous_actual_action is None else
                        _action_tensor(step.previous_actual_action, memory.device))
            scores, memory = model.step(
                page, actions, memory, previous_actual_action=previous,
                public_feedback=None, reset_before=step.reset_before,
            )
            losses.append(listwise_rank_loss(scores, step.action_ids.index(step.target_action_id)))
        chunk_loss = torch.stack(losses).mean()
        if not bool(torch.isfinite(chunk_loss)):
            raise BoundaryError("light_action_m2_training", "nonfinite_loss")
        optimizer.zero_grad(set_to_none=True)
        (chunk_loss * (len(losses) / label_count)).backward()
        torch.nn.utils.clip_grad_norm_(parameters, gradient_clip, error_if_nonfinite=True)
        optimizer.step()
        if any(not bool(torch.isfinite(parameter).all()) for parameter in parameters):
            raise BoundaryError("light_action_m2_training", "nonfinite_parameter")
        total_loss += float(chunk_loss.detach()) * len(losses)
        memory = memory.detach()
    return total_loss / label_count
