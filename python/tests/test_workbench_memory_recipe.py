"""Closed Workbench identities for matched K1/K8 memory and reset pairs."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from spireagent.workbench.memory_recipe import (
    M2_K1_RECIPE,
    M2_K8_RECIPE,
    RESET_K1_RECIPE,
    RESET_K8_RECIPE,
    V2_M2_K1_RECIPE,
    V2_M2_K8_RECIPE,
    V2_RESET_K1_RECIPE,
    V2_RESET_K8_RECIPE,
    input_profile_for_recipe,
    memory_settings_for_recipe,
    recipe_for_memory_config,
    reset_each_step_for_recipe,
)
from stpd.fullrun.memory_sequence_bridge import v2_episode_projection_config
from stpd.workers.memory_ranking import MemoryConfig


@pytest.mark.parametrize(("slots", "reset", "expected", "expected_v2"), [
    (1, False, M2_K1_RECIPE, V2_M2_K1_RECIPE),
    (1, True, RESET_K1_RECIPE, V2_RESET_K1_RECIPE),
    (8, False, M2_K8_RECIPE, V2_M2_K8_RECIPE),
    (8, True, RESET_K8_RECIPE, V2_RESET_K8_RECIPE),
])
def test_exact_config_selects_the_closed_recipe(
    slots: int, reset: bool, expected: str, expected_v2: str,
) -> None:
    config = asdict(MemoryConfig(vocab_size=32, episode_count=2,
                                 width=48, layers=1, heads=2,
                                 feedforward=96, max_tokens=16384,
                                 max_total_input_tokens=4_194_304,
                                 max_episode_observations=768,
                                 max_episode_input_tokens=4_194_304,
                                 max_chunk_steps=2,
                                 max_chunk_input_tokens=24_576,
                                 max_actions_per_step=256,
                                 slots=slots, reset_each_step=reset))
    assert recipe_for_memory_config(config) == expected
    v2 = asdict(v2_episode_projection_config())
    assert recipe_for_memory_config(config, projection_config=v2) == expected_v2
    for recipe, profile in ((expected, "text-menu-v1"), (expected_v2, "text-menu-v2")):
        settings = memory_settings_for_recipe(recipe)
        assert (settings.slots, settings.reset_each_step, settings.input_profile) == (
            slots, reset, profile)
        assert reset_each_step_for_recipe(recipe) is reset
        assert input_profile_for_recipe(recipe) == profile
    for changed in ({**v2, "input_profile": "text-menu-v1"},
                    {**v2, "renderer_wrapper": "counterfeit"},
                    {**v2, "max_settling_events": 64}):
        with pytest.raises(ValueError, match="unsupported_workbench_memory_projection"):
            recipe_for_memory_config(config, projection_config=changed)


@pytest.mark.parametrize(("key", "value"), [
    ("slots", 2),
    ("slots", True),
    ("slots", 8.0),
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


@pytest.mark.parametrize("recipe", [None, {}, [], "stage1a.dsimple.m2.k2.experimental.v1",
                                    "stage1a.dsimple.m2.k8.experimental.v3"])
def test_unknown_recipe_has_no_inferred_settings(recipe: object) -> None:
    for resolver in (memory_settings_for_recipe, input_profile_for_recipe,
                     reset_each_step_for_recipe):
        with pytest.raises(ValueError, match="unsupported_workbench_memory_recipe"):
            resolver(recipe)
