"""The two explicitly supported train-only Workbench M2-K1 recipes."""

from __future__ import annotations

M2_K1_RECIPE = "stage1a.dsimple.m2.k1.experimental.v1"
RESET_K1_RECIPE = "stage1a.dsimple.reset.k1.experimental.v1"
MEMORY_RECIPES = frozenset({M2_K1_RECIPE, RESET_K1_RECIPE})


def reset_each_step_for_recipe(recipe: object) -> bool:
    """Resolve only the closed Workbench recipe names; no checkpoint toggling."""
    if recipe == M2_K1_RECIPE:
        return False
    if recipe == RESET_K1_RECIPE:
        return True
    raise ValueError("unsupported_workbench_memory_recipe")
