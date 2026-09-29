"""Closed Workbench identities for the fixed M2-K1 training pair."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from spireagent.workbench.memory_recipe import (
    M2_K1_RECIPE,
    RESET_K1_RECIPE,
    recipe_for_memory_config,
)
from stpd.workers.memory_ranking import MemoryConfig


@pytest.mark.parametrize(("reset", "expected"), [
    (False, M2_K1_RECIPE),
    (True, RESET_K1_RECIPE),
])
def test_exact_k1_config_selects_the_closed_recipe(reset: bool, expected: str) -> None:
    config = asdict(MemoryConfig(vocab_size=32, episode_count=2,
                                 width=48, layers=1, heads=2,
                                 feedforward=96, max_tokens=16384,
                                 max_total_input_tokens=4_194_304,
                                 max_episode_observations=768,
                                 max_episode_input_tokens=4_194_304,
                                 max_chunk_steps=2,
                                 max_chunk_input_tokens=24_576,
                                 max_actions_per_step=256,
                                 reset_each_step=reset))
    assert recipe_for_memory_config(config) == expected


@pytest.mark.parametrize(("key", "value"), [
    ("slots", 8),
    ("gated", True),
    ("width", 64),
    ("reset_each_step", 1),
    ("other", False),
])
def test_unsupported_memory_config_cannot_be_mislabeled(key: str, value: object) -> None:
    config = asdict(MemoryConfig(vocab_size=32, episode_count=2,
                                 width=48, feedforward=96, max_tokens=16384,
                                 max_total_input_tokens=4_194_304,
                                 max_episode_observations=768,
                                 max_episode_input_tokens=4_194_304,
                                 max_chunk_steps=2,
                                 max_chunk_input_tokens=24_576,
                                 max_actions_per_step=256))
    config[key] = value
    with pytest.raises(ValueError, match="unsupported_workbench_memory_config"):
        recipe_for_memory_config(config)
