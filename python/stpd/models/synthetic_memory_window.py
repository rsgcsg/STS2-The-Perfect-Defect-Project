"""In-memory synthetic observation-only windows, never real trajectory evidence.

The episode hides two independent reward facts in earlier observations. Every
terminal comparison has identical current observation and complete action menu.
Labels are separate metadata and are never serialized into model tokens.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import Tensor

from .experimental_observation_memory_b import ExperimentalObservationMemoryB
from .losses import listwise_rank_loss


@dataclass(frozen=True)
class SyntheticMemoryStep:
    episode_id: str
    position: int
    observation: Tensor
    action_keys: tuple[str, ...]
    actions: tuple[Tensor, ...]
    label_key: str | None
    reset_before: bool

    def __post_init__(self) -> None:
        if (not self.episode_id or type(self.position) is not int or self.position < 0
                or not self.action_keys or len(self.action_keys) != len(self.actions)
                or len(set(self.action_keys)) != len(self.action_keys)
                or (self.label_key is not None and self.label_key not in self.action_keys)):
            raise ValueError("invalid synthetic action binding")


@dataclass(frozen=True)
class SyntheticMemoryWindow:
    episode_id: str
    steps: tuple[SyntheticMemoryStep | None, ...]
    valid_mask: tuple[bool, ...]
    burn_in_mask: tuple[bool, ...]
    loss_mask: tuple[bool, ...]


def make_reward_episodes() -> tuple[tuple[SyntheticMemoryStep, ...], ...]:
    """Pairs require both A and B; equal rewards are excluded."""
    pairs = ((1, 2), (3, 2), (2, 1), (2, 3), (1, 3), (3, 1))
    menu = (torch.tensor([9]), torch.tensor([10]))
    episodes = []
    for index, (a_value, b_value) in enumerate(pairs):
        episode_id = f"synthetic-reward-{index}"
        values = ((1, a_value), (2, b_value), (8, None))
        steps = tuple(SyntheticMemoryStep(
            episode_id, position,
            torch.tensor([kind, 3 + value] if value is not None else [kind]),
            ("A", "B"), menu,
            ("A" if a_value > b_value else "B") if position == 2 else None,
            reset_before=position == 0,
        ) for position, (kind, value) in enumerate(values))
        episodes.append(steps)
    return tuple(episodes)


def build_synthetic_windows(
    episodes: tuple[tuple[SyntheticMemoryStep, ...], ...], *,
    burn_in_steps: int, learn_steps: int,
) -> tuple[SyntheticMemoryWindow, ...]:
    if (type(burn_in_steps) is not int or burn_in_steps < 0
            or type(learn_steps) is not int or learn_steps < 1):
        raise ValueError("invalid synthetic window shape")
    windows = []
    for episode in episodes:
        if (not episode or any(step.episode_id != episode[0].episode_id
                               or step.position != position
                               or step.reset_before != (position == 0)
                               for position, step in enumerate(episode))):
            raise ValueError("invalid synthetic episode order or reset")
        for target_start in range(0, len(episode), learn_steps):
            targets = episode[target_start:target_start + learn_steps]
            if not any(step.label_key is not None for step in targets):
                continue
            context = episode[max(0, target_start - burn_in_steps):target_start]
            values: list[SyntheticMemoryStep | None] = (
                [None] * (burn_in_steps - len(context)) + list(context) + list(targets)
            )
            values.extend([None] * (burn_in_steps + learn_steps - len(values)))
            burns = tuple(i < burn_in_steps and step is not None
                          for i, step in enumerate(values))
            windows.append(SyntheticMemoryWindow(
                episode[0].episode_id, tuple(values),
                tuple(step is not None for step in values), burns,
                tuple(step is not None and step.label_key is not None and not burns[i]
                      for i, step in enumerate(values)),
            ))
    return tuple(windows)


def synthetic_window_loss(
    model: ExperimentalObservationMemoryB, window: SyntheticMemoryWindow,
) -> tuple[Tensor, Tensor]:
    """Burn in without loss or backprop across the burn/learn boundary."""
    if not (len(window.steps) == len(window.valid_mask) == len(window.burn_in_mask)
            == len(window.loss_mask)):
        raise ValueError("synthetic window mask lengths differ")
    hidden = model.initial_hidden()
    losses = []
    passed_burn = False
    for step, valid, burn, learn in zip(
        window.steps, window.valid_mask, window.burn_in_mask, window.loss_mask, strict=True,
    ):
        if valid != (step is not None) or (step is None and (burn or learn)) or (burn and learn):
            raise ValueError("synthetic window masks disagree")
        if step is None:
            continue
        if step.episode_id != window.episode_id:
            raise ValueError("synthetic window crosses episode")
        if burn:
            if passed_burn:
                raise ValueError("burn-in after learn boundary")
            with torch.no_grad():
                hidden = model.advance(step.observation, hidden, reset_before=step.reset_before)
            continue
        if not passed_burn:
            hidden = hidden.detach()
            passed_burn = True
        scores, hidden = model.step(
            step.observation, step.actions, hidden, reset_before=step.reset_before,
        )
        if learn:
            if step.label_key is None:
                raise ValueError("loss mask has no bound label")
            losses.append(listwise_rank_loss(scores, step.action_keys.index(step.label_key)))
        elif step.label_key is not None:
            raise ValueError("label omitted from loss mask")
    if not losses:
        raise ValueError("synthetic window has no target")
    return torch.stack(losses).mean(), hidden
