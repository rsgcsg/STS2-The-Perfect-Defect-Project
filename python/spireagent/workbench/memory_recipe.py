"""Closed train-only Workbench memory recipes; old identities stay unchanged."""

from __future__ import annotations

from dataclasses import dataclass

M2_K1_RECIPE = "stage1a.dsimple.m2.k1.experimental.v1"
RESET_K1_RECIPE = "stage1a.dsimple.reset.k1.experimental.v1"
M2_K8_RECIPE = "stage1a.dsimple.m2.k8.experimental.v1"
RESET_K8_RECIPE = "stage1a.dsimple.reset.k8.experimental.v1"
V2_M2_K1_RECIPE = "stage1a.dsimple.m2.k1.experimental.v2"
V2_RESET_K1_RECIPE = "stage1a.dsimple.reset.k1.experimental.v2"
V2_M2_K8_RECIPE = "stage1a.dsimple.m2.k8.experimental.v2"
V2_RESET_K8_RECIPE = "stage1a.dsimple.reset.k8.experimental.v2"
HISTORY_M2_K1_RECIPE = "stage1a.dsimple.m2.k1.confirmed-interaction.v1"
HISTORY_RESET_K1_RECIPE = "stage1a.dsimple.reset.k1.confirmed-interaction.v1"
HISTORY_M2_K8_RECIPE = "stage1a.dsimple.m2.k8.confirmed-interaction.v1"
HISTORY_RESET_K8_RECIPE = "stage1a.dsimple.reset.k8.confirmed-interaction.v1"
V2_HISTORY_M2_K1_RECIPE = "stage1a.dsimple.m2.k1.confirmed-interaction.v2"
V2_HISTORY_RESET_K1_RECIPE = "stage1a.dsimple.reset.k1.confirmed-interaction.v2"
V2_HISTORY_M2_K8_RECIPE = "stage1a.dsimple.m2.k8.confirmed-interaction.v2"
V2_HISTORY_RESET_K8_RECIPE = "stage1a.dsimple.reset.k8.confirmed-interaction.v2"


@dataclass(frozen=True)
class MemoryRecipeSettings:
    slots: int
    reset_each_step: bool
    input_profile: str


_RECIPES = {
    M2_K1_RECIPE: MemoryRecipeSettings(1, False, "text-menu-v1"),
    RESET_K1_RECIPE: MemoryRecipeSettings(1, True, "text-menu-v1"),
    M2_K8_RECIPE: MemoryRecipeSettings(8, False, "text-menu-v1"),
    RESET_K8_RECIPE: MemoryRecipeSettings(8, True, "text-menu-v1"),
    V2_M2_K1_RECIPE: MemoryRecipeSettings(1, False, "text-menu-v2"),
    V2_RESET_K1_RECIPE: MemoryRecipeSettings(1, True, "text-menu-v2"),
    V2_M2_K8_RECIPE: MemoryRecipeSettings(8, False, "text-menu-v2"),
    V2_RESET_K8_RECIPE: MemoryRecipeSettings(8, True, "text-menu-v2"),
    HISTORY_M2_K1_RECIPE: MemoryRecipeSettings(1, False, "text-menu-v1-confirmed-interaction"),
    HISTORY_RESET_K1_RECIPE: MemoryRecipeSettings(1, True, "text-menu-v1-confirmed-interaction"),
    HISTORY_M2_K8_RECIPE: MemoryRecipeSettings(8, False, "text-menu-v1-confirmed-interaction"),
    HISTORY_RESET_K8_RECIPE: MemoryRecipeSettings(8, True, "text-menu-v1-confirmed-interaction"),
    V2_HISTORY_M2_K1_RECIPE: MemoryRecipeSettings(1, False, "text-menu-v2-confirmed-interaction"),
    V2_HISTORY_RESET_K1_RECIPE: MemoryRecipeSettings(1, True, "text-menu-v2-confirmed-interaction"),
    V2_HISTORY_M2_K8_RECIPE: MemoryRecipeSettings(8, False, "text-menu-v2-confirmed-interaction"),
    V2_HISTORY_RESET_K8_RECIPE: MemoryRecipeSettings(8, True, "text-menu-v2-confirmed-interaction"),
}
MEMORY_RECIPES = frozenset(_RECIPES)
V2_MEMORY_RECIPES = frozenset(recipe for recipe, settings in _RECIPES.items()
                              if settings.input_profile.startswith("text-menu-v2"))

_CONFIG_KEYS = frozenset({
    "vocab_size", "episode_count", "slots", "reset_each_step", "gated", "seed",
    "width", "layers", "heads", "feedforward", "dropout", "max_tokens",
    "learning_rate", "weight_decay", "gradient_clip", "cpu_threads",
    "max_total_input_tokens", "max_episode_observations", "max_episode_input_tokens",
    "max_chunk_steps", "max_chunk_input_tokens", "max_actions_per_step",
})
_FIXED_CONFIG = {
    "gated": False, "seed": 1701, "width": 48, "layers": 1,
    "heads": 2, "feedforward": 96, "dropout": 0.0, "max_tokens": 16384,
    "learning_rate": 0.001, "weight_decay": 0.0, "gradient_clip": 1.0,
    "cpu_threads": 2, "max_total_input_tokens": 4_194_304,
    "max_episode_observations": 768, "max_episode_input_tokens": 4_194_304,
    "max_chunk_steps": 2, "max_chunk_input_tokens": 24_576,
    "max_actions_per_step": 256,
}


def memory_settings_for_recipe(recipe: object) -> MemoryRecipeSettings:
    """Resolve slot count, reset and input together; never reinterpret a checkpoint."""
    if not isinstance(recipe, str) or recipe not in _RECIPES:
        raise ValueError("unsupported_workbench_memory_recipe")
    return _RECIPES[recipe]


def reset_each_step_for_recipe(recipe: object) -> bool:
    return memory_settings_for_recipe(recipe).reset_each_step


def input_profile_for_recipe(recipe: object) -> str:
    return memory_settings_for_recipe(recipe).input_profile


def recipe_for_memory_config(config: object, *,
                             projection_config: object = None) -> str:
    """Bind the exact memory configuration to its verified projection profile."""
    if (not isinstance(config, dict) or set(config) != _CONFIG_KEYS
            or type(config.get("vocab_size")) is not int
            or not 1 <= config["vocab_size"] <= 65_536
            or type(config.get("episode_count")) is not int
            or not 1 <= config["episode_count"] <= 8
            or type(config.get("slots")) is not int or config["slots"] not in (1, 8)
            or type(config.get("reset_each_step")) is not bool
            or any(type(config.get(key)) is not type(value) or config.get(key) != value
                   for key, value in _FIXED_CONFIG.items())):
        raise ValueError("unsupported_workbench_memory_config")
    from stpd.fullrun.memory_projection_config import (
        parse_episode_projection_config,
        projection_input_profile,
    )

    try:
        profile = ("text-menu-v1" if projection_config is None else
                   projection_input_profile(parse_episode_projection_config(projection_config)))
    except (TypeError, ValueError) as error:
        raise ValueError("unsupported_workbench_memory_projection") from error
    settings = MemoryRecipeSettings(config["slots"], config["reset_each_step"], profile)
    return next(recipe for recipe, candidate in _RECIPES.items() if candidate == settings)
