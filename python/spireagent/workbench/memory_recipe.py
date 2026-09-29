"""The two explicitly supported train-only Workbench M2-K1 recipes."""

from __future__ import annotations

M2_K1_RECIPE = "stage1a.dsimple.m2.k1.experimental.v1"
RESET_K1_RECIPE = "stage1a.dsimple.reset.k1.experimental.v1"
V2_M2_K1_RECIPE = "stage1a.dsimple.m2.k1.experimental.v2"
V2_RESET_K1_RECIPE = "stage1a.dsimple.reset.k1.experimental.v2"
MEMORY_RECIPES = frozenset({M2_K1_RECIPE, RESET_K1_RECIPE,
                            V2_M2_K1_RECIPE, V2_RESET_K1_RECIPE})
V2_MEMORY_RECIPES = frozenset({V2_M2_K1_RECIPE, V2_RESET_K1_RECIPE})

_CONFIG_KEYS = frozenset({
    "vocab_size", "episode_count", "slots", "reset_each_step", "gated", "seed",
    "width", "layers", "heads", "feedforward", "dropout", "max_tokens",
    "learning_rate", "weight_decay", "gradient_clip", "cpu_threads",
    "max_total_input_tokens", "max_episode_observations", "max_episode_input_tokens",
    "max_chunk_steps", "max_chunk_input_tokens", "max_actions_per_step",
})
_FIXED_CONFIG = {
    "slots": 1, "gated": False, "seed": 1701, "width": 48, "layers": 1,
    "heads": 2, "feedforward": 96, "dropout": 0.0, "max_tokens": 16384,
    "learning_rate": 0.001, "weight_decay": 0.0, "gradient_clip": 1.0,
    "cpu_threads": 2, "max_total_input_tokens": 4_194_304,
    "max_episode_observations": 768, "max_episode_input_tokens": 4_194_304,
    "max_chunk_steps": 2, "max_chunk_input_tokens": 24_576,
    "max_actions_per_step": 256,
}


def reset_each_step_for_recipe(recipe: object) -> bool:
    """Resolve only the closed Workbench recipe names; no checkpoint toggling."""
    if recipe in {M2_K1_RECIPE, V2_M2_K1_RECIPE}:
        return False
    if recipe in {RESET_K1_RECIPE, V2_RESET_K1_RECIPE}:
        return True
    raise ValueError("unsupported_workbench_memory_recipe")


def input_profile_for_recipe(recipe: object) -> str:
    if recipe in V2_MEMORY_RECIPES:
        return "text-menu-v2"
    if recipe in {M2_K1_RECIPE, RESET_K1_RECIPE}:
        return "text-menu-v1"
    raise ValueError("unsupported_workbench_memory_recipe")


def recipe_for_memory_config(config: object, *,
                             projection_config: object = None) -> str:
    """Bind identical K1 weights to the verified projection profile."""
    if (not isinstance(config, dict) or set(config) != _CONFIG_KEYS
            or type(config.get("vocab_size")) is not int
            or not 1 <= config["vocab_size"] <= 65_536
            or type(config.get("episode_count")) is not int
            or not 1 <= config["episode_count"] <= 8
            or type(config.get("reset_each_step")) is not bool
            or any(type(config.get(key)) is not type(value) or config.get(key) != value
                   for key, value in _FIXED_CONFIG.items())):
        raise ValueError("unsupported_workbench_memory_config")
    from stpd.fullrun.memory_sequence_bridge import (
        parse_episode_projection_config,
        projection_input_profile,
    )

    try:
        profile = ("text-menu-v1" if projection_config is None else
                   projection_input_profile(parse_episode_projection_config(projection_config)))
    except (TypeError, ValueError) as error:
        raise ValueError("unsupported_workbench_memory_projection") from error
    if profile == "text-menu-v2":
        return V2_RESET_K1_RECIPE if config["reset_each_step"] else V2_M2_K1_RECIPE
    return RESET_K1_RECIPE if config["reset_each_step"] else M2_K1_RECIPE
