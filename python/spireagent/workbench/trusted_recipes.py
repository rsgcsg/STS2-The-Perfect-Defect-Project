"""Static trusted recipe registry. Discovery never imports a numerical backend."""

from __future__ import annotations

from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.local_model_dependencies import recipe_dependencies_available
from spireagent.workbench.memory_recipe import MEMORY_RECIPES

DEFAULT_RECIPE = "stage1a.dsimple.s.v1"
STRUCTURED_RECIPE = "structured-m2-cpu-v2"
TRUSTED_RECIPES = frozenset({DEFAULT_RECIPE, STRUCTURED_RECIPE, *MEMORY_RECIPES})


def describe_recipe(recipe_id: str) -> dict[str, Any]:
    if recipe_id not in TRUSTED_RECIPES:
        raise BoundaryError("local_training", "unsupported_training_recipe")
    structured = recipe_id == STRUCTURED_RECIPE
    descriptor: dict[str, Any] = {
        "recipe_id": recipe_id, "placement_ids": ["local-cpu"],
        "dependencies_available": recipe_dependencies_available(recipe_id),
        "automatic_retry": False,
        "supported_actions": ["cancel", "pause", "resume", "reconcile"] if structured else [],
        "result_type": "evaluated" if recipe_id == DEFAULT_RECIPE else "train_only",
        "config_defaults": {}, "config_fields": {},
        "limits": {"wall_seconds": {"minimum": 1, "maximum": 3600}},
    }
    if structured:
        from stpd.structured_workload_contracts import structured_workload_capabilities

        domain = structured_workload_capabilities()
        descriptor.update(result_type="evaluated", config_defaults=domain["config_defaults"],
                          config_fields=domain["config_bounds"],
                          fixed_config_fields=domain["fixed_config_fields"],
                          control_boundary=domain["control_boundary"],
                          source_profile="s0-admitted-policy-offers-v1",
                          source_admission={"synthetic_fixture": "supported_engineering_only",
                                            "agent": "structured_source_verifier_required"},
                          legacy_v1="final_only_not_resumable")
    else:
        descriptor["limits"] = {}
    return descriptor


def validate_recipe_config(recipe_id: str, config: object) -> dict[str, Any]:
    if recipe_id not in TRUSTED_RECIPES:
        raise BoundaryError("local_training", "unsupported_training_recipe")
    if not isinstance(config, dict):
        raise BoundaryError("local_training", "invalid_recipe_config")
    if recipe_id == STRUCTURED_RECIPE:
        from dataclasses import asdict

        from stpd.structured_workload_contracts import StructuredTrainingConfig

        if set(config) - set(asdict(StructuredTrainingConfig())):
            raise BoundaryError("local_training", "invalid_recipe_config")
        selected = StructuredTrainingConfig(**config)
        selected.validate()
        return asdict(selected)
    if config:
        raise BoundaryError("local_training", "fixed_recipe_config_required")
    return {}


def recipe_adapter(recipe_id: str) -> Any:
    # Explicit imports only: a manifest or HTTP request cannot select an import,
    # executable, path, URL, plugin or arbitrary worker.
    if recipe_id == STRUCTURED_RECIPE:
        from spireagent.workbench.recipes.structured import StructuredRecipeAdapter

        return StructuredRecipeAdapter()
    if recipe_id == DEFAULT_RECIPE or recipe_id in MEMORY_RECIPES:
        from spireagent.workbench.recipes.legacy import LegacyRecipeAdapter

        return LegacyRecipeAdapter()
    raise BoundaryError("local_training", "unsupported_training_recipe")
