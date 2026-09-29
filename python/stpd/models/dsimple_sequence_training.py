"""In-memory M2 full-prefix loss and bounded episode TBPTT updates.

The caller owns the provenance of observations, complete candidate catalogs,
labels, confirmed previous actions, and optional public feedback. This module
does not admit real data, publish artifacts, or infer execution from a label.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import torch
from torch import Tensor

from .dsimple_memory import ExperimentalDSimpleM2
from .losses import listwise_rank_loss

MAX_WINDOW_STEPS = 64
MAX_LEARN_STEPS = 32
MAX_WINDOW_INPUT_TOKENS = 65_536


@dataclass(frozen=True)
class MemorySequenceStep:
    episode_id: str
    position: int
    page: Tensor
    action_keys: tuple[str, ...]
    actions: tuple[Tensor, ...]
    label_key: str | None = None
    reset_before: bool = False
    previous_actual_action: Tensor | None = None
    public_feedback: Tensor | None = None


@dataclass(frozen=True)
class MemorySequenceWindow:
    episode_id: str
    steps: tuple[MemorySequenceStep | None, ...]
    valid_mask: tuple[bool, ...]
    burn_in_mask: tuple[bool, ...]
    loss_mask: tuple[bool, ...]


@dataclass(frozen=True)
class MemorySequenceEpisode:
    """One ordered, unpadded episode; a label supervises its own observation only."""

    episode_id: str
    steps: tuple[MemorySequenceStep, ...]


def _validate_step(
    model: ExperimentalDSimpleM2, step: MemorySequenceStep, episode_id: str,
    position: int,
) -> int:
    if (
        not isinstance(step, MemorySequenceStep)
        or step.episode_id != episode_id
        or type(step.position) is not int
        or step.position != position
        or type(step.reset_before) is not bool
        or step.reset_before != (position == 0)
    ):
        raise ValueError("memory sequence must contain one complete episode prefix")
    if (
        not isinstance(step.action_keys, tuple)
        or not isinstance(step.actions, tuple)
        or not step.action_keys
        or len(step.action_keys) != len(step.actions)
        or any(not isinstance(key, str) or not key for key in step.action_keys)
        or len(set(step.action_keys)) != len(step.action_keys)
        or step.label_key is not None
        and step.label_key not in step.action_keys
    ):
        raise ValueError("invalid complete-catalog key binding")
    input_tokens = 0
    for ids in (step.page, *step.actions):
        if not isinstance(ids, Tensor) or ids.device != model.write_queries.device:
            raise ValueError("invalid token tensor or device")
        model.core.validate_tokens(ids)
        input_tokens += ids.numel()
    for optional_ids in (step.previous_actual_action, step.public_feedback):
        if optional_ids is None:
            continue
        if (
            not isinstance(optional_ids, Tensor)
            or optional_ids.device != model.write_queries.device
        ):
            raise ValueError("invalid token tensor or device")
        model.core.validate_tokens(optional_ids)
        input_tokens += optional_ids.numel()
    length = (
        2 * model.slots
        + step.page.numel()
        + int(step.previous_actual_action is not None)
        + int(step.public_feedback is not None)
    )
    if length > model.core.max_tokens:
        raise ValueError("page and memory token limit exceeded")
    return input_tokens


def _validate_model(model: ExperimentalDSimpleM2) -> None:
    if not isinstance(model, ExperimentalDSimpleM2):
        raise ValueError("invalid memory model")
    core_parameter = next(model.core.parameters(), None)
    if (
        core_parameter is None
        or core_parameter.device != model.write_queries.device
        or core_parameter.dtype != model.write_queries.dtype
    ):
        raise ValueError("core and memory device or dtype differ")
    if any(not bool(torch.isfinite(parameter).all()) for parameter in model.parameters()):
        raise ValueError("nonfinite model parameter")


def validate_memory_window(model: ExperimentalDSimpleM2, window: MemorySequenceWindow) -> None:
    """Preflight the entire full-prefix window before any model computation."""
    if (
        not isinstance(model, ExperimentalDSimpleM2)
        or not isinstance(window, MemorySequenceWindow)
        or not window.episode_id
        or not window.steps
        or len(window.steps) > MAX_WINDOW_STEPS
        or not (
            len(window.steps)
            == len(window.valid_mask)
            == len(window.burn_in_mask)
            == len(window.loss_mask)
        )
    ):
        raise ValueError("invalid memory window")
    if any(
        type(flag) is not bool
        for mask in (
            window.valid_mask,
            window.burn_in_mask,
            window.loss_mask,
        )
        for flag in mask
    ):
        raise ValueError("invalid memory window masks")
    _validate_model(model)
    valid_indices = [index for index, flag in enumerate(window.valid_mask) if flag]
    if (
        not valid_indices
        or valid_indices != list(range(valid_indices[0], valid_indices[-1] + 1))
        or sum(window.loss_mask) < 1
    ):
        raise ValueError("memory window needs contiguous observations and labels")
    first, last = valid_indices[0], valid_indices[-1]
    if sum(not window.burn_in_mask[index] for index in valid_indices) > MAX_LEARN_STEPS:
        raise ValueError("memory learn span exceeds limit")
    if any(
        window.steps[index] is not None or window.burn_in_mask[index] or window.loss_mask[index]
        for index in range(first)
    ) or any(
        window.steps[index] is not None or window.burn_in_mask[index] or window.loss_mask[index]
        for index in range(last + 1, len(window.steps))
    ):
        raise ValueError("invalid memory window padding")
    passed_burn = False
    input_tokens = 0
    for position, index in enumerate(valid_indices):
        step = window.steps[index]
        if step is None:
            raise ValueError("valid memory observation missing")
        input_tokens += _validate_step(model, step, window.episode_id, position)
        if input_tokens > MAX_WINDOW_INPUT_TOKENS:
            raise ValueError("memory window input token limit exceeded")
        if window.burn_in_mask[index]:
            if passed_burn:
                raise ValueError("burn-in follows learn span")
        else:
            passed_burn = True
        if window.loss_mask[index] and (window.burn_in_mask[index] or step.label_key is None):
            raise ValueError("loss mask has no learn-span label")
    if any(
        window.valid_mask[index] != (window.steps[index] is not None)
        for index in range(len(window.steps))
    ):
        raise ValueError("memory window valid mask disagrees")


def _loss_after_preflight(model: ExperimentalDSimpleM2, window: MemorySequenceWindow) -> Tensor:
    memory = model.initial_memory()
    losses: list[Tensor] = []
    in_learn_span = False
    for step, valid, burn, learn in zip(
        window.steps,
        window.valid_mask,
        window.burn_in_mask,
        window.loss_mask,
        strict=True,
    ):
        if not valid:
            continue
        assert step is not None
        if burn:
            with torch.no_grad():
                memory = model.advance(
                    step.page,
                    memory,
                    previous_actual_action=step.previous_actual_action,
                    feedback=step.public_feedback,
                    reset_before=step.reset_before,
                )
            continue
        if not in_learn_span:
            memory = memory.detach()  # Burn-in reconstructs state, not its gradient history.
            in_learn_span = True
        if learn:
            scores, memory = model.step(
                step.page,
                step.actions,
                memory,
                previous_actual_action=step.previous_actual_action,
                feedback=step.public_feedback,
                reset_before=step.reset_before,
            )
            assert step.label_key is not None
            losses.append(listwise_rank_loss(scores, step.action_keys.index(step.label_key)))
        else:
            memory = model.advance(
                step.page,
                memory,
                previous_actual_action=step.previous_actual_action,
                feedback=step.public_feedback,
                reset_before=step.reset_before,
            )
    loss = torch.stack(losses).mean()
    if not bool(torch.isfinite(loss)):
        raise ValueError("nonfinite memory sequence loss")
    return loss


def memory_sequence_loss(model: ExperimentalDSimpleM2, window: MemorySequenceWindow) -> Tensor:
    """Mean listwise loss over included labels; no memory leaves this window."""
    validate_memory_window(model, window)
    return _loss_after_preflight(model, window)


def train_memory_window(
    model: ExperimentalDSimpleM2,
    optimizer: torch.optim.Optimizer,
    window: MemorySequenceWindow,
    *,
    gradient_clip: float = 1.0,
) -> float:
    """One update using a caller-owned optimizer; validate before touching gradients."""
    validate_memory_window(model, window)
    if (
        type(gradient_clip) not in {int, float}
        or not math.isfinite(gradient_clip)
        or gradient_clip <= 0
    ):
        raise ValueError("invalid memory gradient clip")
    parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer_parameters = [
        parameter for group in optimizer.param_groups for parameter in group["params"]
    ]
    if len(parameters) != len(optimizer_parameters) or {
        id(parameter) for parameter in parameters
    } != {id(parameter) for parameter in optimizer_parameters}:
        raise ValueError("optimizer does not own this model")
    model.train()
    optimizer.zero_grad(set_to_none=True)
    loss = _loss_after_preflight(model, window)
    loss.backward()
    torch.nn.utils.clip_grad_norm_(parameters, gradient_clip, error_if_nonfinite=True)
    optimizer.step()
    if any(not bool(torch.isfinite(parameter).all()) for parameter in parameters):
        raise ValueError("nonfinite model parameter after update")
    return float(loss.detach())


def _episode_chunk_plan(
    input_tokens: tuple[int, ...], max_chunk_steps: int, max_chunk_input_tokens: int,
) -> tuple[tuple[int, int], ...]:
    """Partition validated observations without changing their order or content."""
    chunks: list[tuple[int, int]] = []
    chunk_start = 0
    chunk_tokens = 0
    for position, step_tokens in enumerate(input_tokens):
        if (
            position > chunk_start
            and (position - chunk_start >= max_chunk_steps
                 or chunk_tokens + step_tokens > max_chunk_input_tokens)
        ):
            chunks.append((chunk_start, position))
            chunk_start = position
            chunk_tokens = 0
        chunk_tokens += step_tokens
    chunks.append((chunk_start, len(input_tokens)))
    return tuple(chunks)


def train_memory_episode(
    model: ExperimentalDSimpleM2,
    optimizer: torch.optim.Optimizer,
    episode: MemorySequenceEpisode,
    *,
    max_observations: int,
    max_input_tokens: int,
    max_chunk_steps: int = MAX_LEARN_STEPS,
    max_chunk_input_tokens: int = MAX_WINDOW_INPUT_TOKENS,
    gradient_clip: float = 1.0,
) -> float:
    """Train a complete episode by bounded truncated BPTT; return mean label loss.

    Preflight covers the *whole* episode before the first update. Chunk memory is
    carried by value and detached across optimizer steps; it is never replayed
    under new weights. This is an approximation, not full-history backprop.
    """
    if not isinstance(episode, MemorySequenceEpisode) or not episode.episode_id:
        raise ValueError("invalid memory episode")
    limits = (max_observations, max_input_tokens, max_chunk_steps, max_chunk_input_tokens)
    if (
        any(type(limit) is not int or limit <= 0 for limit in limits)
        or max_chunk_steps > MAX_LEARN_STEPS
        or max_chunk_input_tokens > MAX_WINDOW_INPUT_TOKENS
    ):
        raise ValueError("invalid memory episode limits")
    if not isinstance(episode.steps, tuple) or not episode.steps:
        raise ValueError("memory episode needs observations")
    if len(episode.steps) > max_observations:
        raise ValueError("memory episode observation limit exceeded")
    _validate_model(model)
    if (
        type(gradient_clip) not in {int, float}
        or not math.isfinite(gradient_clip)
        or gradient_clip <= 0
    ):
        raise ValueError("invalid memory gradient clip")
    parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer_parameters = [
        parameter for group in optimizer.param_groups for parameter in group["params"]
    ]
    if len(parameters) != len(optimizer_parameters) or {
        id(parameter) for parameter in parameters
    } != {id(parameter) for parameter in optimizer_parameters}:
        raise ValueError("optimizer does not own this model")

    # Build the full deterministic chunk plan while validating every supplied
    # token and binding. No model forward or optimizer operation occurs here.
    step_tokens_by_position: list[int] = []
    total_tokens = 0
    label_count = 0
    for position, step in enumerate(episode.steps):
        step_tokens = _validate_step(model, step, episode.episode_id, position)
        if step_tokens > max_chunk_input_tokens:
            raise ValueError("memory episode step exceeds chunk input token limit")
        total_tokens += step_tokens
        if total_tokens > max_input_tokens:
            raise ValueError("memory episode input token limit exceeded")
        step_tokens_by_position.append(step_tokens)
        label_count += int(step.label_key is not None)
    if label_count == 0:
        raise ValueError("memory episode needs at least one label")
    chunks = _episode_chunk_plan(
        tuple(step_tokens_by_position), max_chunk_steps, max_chunk_input_tokens,
    )

    model.train()
    memory = model.initial_memory()
    total_loss = 0.0
    for start, end in chunks:
        chunk = episode.steps[start:end]
        has_labels = any(step.label_key is not None for step in chunk)
        losses: list[Tensor] = []
        with torch.set_grad_enabled(has_labels):
            for step in chunk:
                if step.label_key is None:
                    memory = model.advance(
                        step.page, memory,
                        previous_actual_action=step.previous_actual_action,
                        feedback=step.public_feedback,
                        reset_before=step.reset_before,
                    )
                else:
                    scores, memory = model.step(
                        step.page, step.actions, memory,
                        previous_actual_action=step.previous_actual_action,
                        feedback=step.public_feedback,
                        reset_before=step.reset_before,
                    )
                    losses.append(
                        listwise_rank_loss(scores, step.action_keys.index(step.label_key))
                    )
            if losses:
                chunk_loss = torch.stack(losses).mean()
                if not bool(torch.isfinite(chunk_loss)):
                    raise ValueError("nonfinite memory episode loss")
                optimizer.zero_grad(set_to_none=True)
                (chunk_loss * (len(losses) / label_count)).backward()
                torch.nn.utils.clip_grad_norm_(parameters, gradient_clip, error_if_nonfinite=True)
                optimizer.step()
                if any(not bool(torch.isfinite(parameter).all()) for parameter in parameters):
                    raise ValueError("nonfinite model parameter after update")
                total_loss += float(chunk_loss.detach()) * len(losses)
        memory = memory.detach()
    return total_loss / label_count
