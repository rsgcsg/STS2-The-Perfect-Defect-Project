"""Concrete model-ready inputs shared by light-action M2 sequence owners.

This module contains no source admission or research-source authority. Callers
must qualify their rows before constructing these immutable values.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LightActionM2TrainingStep:
    position: int
    page: tuple[int, ...]
    action_ids: tuple[str, ...]
    byte_actions: tuple[tuple[int, ...], ...]
    target_action_id: str | None
    previous_actual_action: tuple[int, ...] | None
    reset_before: bool


@dataclass(frozen=True)
class LightActionM2TrainingEpisode:
    episode_id: str
    slots: int
    reset_each_step: bool
    steps: tuple[LightActionM2TrainingStep, ...]
    max_episode_steps: int
    max_chunk_steps: int
    max_episode_tokens: int
    max_chunk_tokens: int
