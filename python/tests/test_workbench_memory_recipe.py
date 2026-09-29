"""Closed Workbench identities for the fixed M2-K1 training pair."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from spireagent.workbench.memory_recipe import (
    M2_K1_RECIPE,
    RESET_K1_RECIPE,
    V2_M2_K1_RECIPE,
    V2_RESET_K1_RECIPE,
    recipe_for_memory_config,
)
from stpd.fullrun.memory_sequence_bridge import v2_episode_projection_config
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
    v2 = asdict(v2_episode_projection_config())
    assert recipe_for_memory_config(config, projection_config=v2) == (
        V2_RESET_K1_RECIPE if reset else V2_M2_K1_RECIPE)
    for changed in ({**v2, "input_profile": "text-menu-v1"},
                    {**v2, "renderer_wrapper": "counterfeit"},
                    {**v2, "max_settling_events": 64}):
        with pytest.raises(ValueError, match="unsupported_workbench_memory_projection"):
            recipe_for_memory_config(config, projection_config=changed)


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
