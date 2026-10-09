"""Static trusted recipe registry. Discovery never imports a numerical backend."""

from __future__ import annotations

from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.local_model_dependencies import recipe_dependencies_available
from spireagent.workbench.memory_recipe import MEMORY_RECIPES
from stpd.native_training_source_spec import RECIPE as NATIVE_SAMPLED_RECIPE
from stpd.ordered_source_spec import RECIPES as ORDERED_RECIPE_CONFIGS

DEFAULT_RECIPE = "stage1a.dsimple.s.v1"
STRUCTURED_RECIPE = "structured-m2-cpu-v2"
STRUCTURED_SCOPED_RECIPE = "structured-m2-cpu-v3"
ORDERED_RECIPES = frozenset(ORDERED_RECIPE_CONFIGS)
STRUCTURED_RECIPES = frozenset({
    STRUCTURED_RECIPE, STRUCTURED_SCOPED_RECIPE, NATIVE_SAMPLED_RECIPE, *ORDERED_RECIPES,
})
MAX_TOTAL_ATTEMPTS = 32
DEFAULT_CHECKPOINT_CADENCE = 100
TRUSTED_RECIPES = frozenset({DEFAULT_RECIPE, *STRUCTURED_RECIPES, *MEMORY_RECIPES})


def checked_recipe_execution_policy(recipe_id: str, value: object) -> dict[str, Any]:
    from stpd.native_sampled_carry_spec import RECIPE as SAMPLED_RECIPE
    from stpd.policy.native_operational_outcome import checked_execution_policy

    if not isinstance(recipe_id, str) or recipe_id not in {NATIVE_SAMPLED_RECIPE, SAMPLED_RECIPE}:
        raise BoundaryError("local_training", "invalid_training_request")
    try:
        return checked_execution_policy(value)
    except BoundaryError as error:
        # Preserve the existing HTTP/native admission error. A rejected request
        # is not an unconfirmed worker operation; its detailed parser cause stays.
        raise BoundaryError("local_training", "invalid_training_request") from error


def structured_recipe_scope(recipe_id: str) -> str:
    from stpd.structured_code_scope import LEGACY_SCOPE, TRAINING_SCOPE

    if recipe_id == STRUCTURED_RECIPE:
        return LEGACY_SCOPE
    if recipe_id == STRUCTURED_SCOPED_RECIPE:
        return TRAINING_SCOPE
    if recipe_id in ORDERED_RECIPES:
        from stpd.ordered_source_spec import SCOPE

        return SCOPE
    if recipe_id == NATIVE_SAMPLED_RECIPE:
        from stpd.native_graph_spec import TRAINING_SCOPE

        return TRAINING_SCOPE
    raise BoundaryError("local_training", "unsupported_structured_recipe")


def structured_recipe_run_schema(recipe_id: str) -> str:
    from stpd.structured_code_scope import SCOPED_RUN_SCHEMA

    structured_recipe_scope(recipe_id)
    if recipe_id == NATIVE_SAMPLED_RECIPE:
        from stpd.native_graph_spec import RUN_SCHEMA

        return RUN_SCHEMA
    if recipe_id in ORDERED_RECIPES:
        from stpd.ordered_source_spec import RUN_SCHEMA

        return RUN_SCHEMA
    return (
        SCOPED_RUN_SCHEMA if recipe_id == STRUCTURED_SCOPED_RECIPE else "stpd/structured-m2-run-v2"
    )


def structured_recipe_is_scoped(recipe_id: str) -> bool:
    structured_recipe_scope(recipe_id)
    return (recipe_id in {STRUCTURED_SCOPED_RECIPE, NATIVE_SAMPLED_RECIPE}
            or recipe_id in ORDERED_RECIPES)


def describe_recipe(recipe_id: str) -> dict[str, Any]:
    if recipe_id not in TRUSTED_RECIPES:
        raise BoundaryError("local_training", "unsupported_training_recipe")
    structured = recipe_id in STRUCTURED_RECIPES
    descriptor: dict[str, Any] = {
        "recipe_id": recipe_id,
        "placement_ids": ["local-cpu"],
        "dependencies_available": recipe_dependencies_available(recipe_id),
        "automatic_retry": False,
        "supported_actions": ["cancel", "pause", "resume", "reconcile"] if structured else [],
        "result_type": "evaluated" if recipe_id == DEFAULT_RECIPE else "train_only",
        "config_defaults": {},
        "config_fields": {},
        "limits": {
            "wall_seconds": {"minimum": 1, "maximum": 3600},
            "scratch_bytes": {
                "minimum": 16 * 1024 * 1024,
                "maximum": 1024 * 1024 * 1024,
                "default": 512 * 1024 * 1024,
            },
        },
    }
    if structured:
        from stpd.structured_workload_contracts import structured_workload_capabilities

        domain = structured_workload_capabilities()
        descriptor.update(
            result_type="train_only",
            config_defaults=domain["config_defaults"],
            config_fields=domain["config_bounds"],
            fixed_config_fields=domain["fixed_config_fields"],
            control_boundary=domain["control_boundary"],
            worker_isolation="private_child-v1",
            cancel_grace_seconds=1,
            training_partition="train_only",
            fixed_model_evaluation="domain_seam",
            max_total_attempts=MAX_TOTAL_ATTEMPTS,
            cumulative_limits=True,
            scratch_monitor_seconds=0.25,
            artifact_budget="conservative_parent_reservation_before_every_write",
            source_profile="s0-admitted-policy-offers-v1",
            source_admission={
                "synthetic_fixture": "supported_engineering_only",
                "typed_protocol_partition": "replay_verified_sampled_s0",
                "ordinary_agent_json": "structured_source_verifier_required",
            },
            legacy_v1="final_only_not_resumable",
        )
        descriptor["config_defaults"]["checkpoint_every_boundaries"] = DEFAULT_CHECKPOINT_CADENCE
        descriptor["config_fields"]["checkpoint_every_boundaries"] = [1, 100]
        descriptor["code_scope"] = structured_recipe_scope(recipe_id)
        descriptor["run_schema"] = structured_recipe_run_schema(recipe_id)
        descriptor["scoped_attempt_producer"] = structured_recipe_is_scoped(recipe_id)
        from stpd.structured_code_scope import (
            SCOPED_MODEL_SCHEMA,
            checkpoint_schema,
            structured_model_package_schema,
        )

        descriptor["checkpoint_schema"] = checkpoint_schema(descriptor["code_scope"])
        descriptor["model_schema"] = (
            SCOPED_MODEL_SCHEMA
            if recipe_id == STRUCTURED_SCOPED_RECIPE
            else "stpd/structured-m2-model-v2"
        )
        if recipe_id == NATIVE_SAMPLED_RECIPE:
            from stpd.native_graph_spec import NativeGraphControl
            from stpd.native_sampled_carry_spec import INPUT_SPEC
            from stpd.native_training_source_spec import (
                MODEL_SCHEMA,
                PACKAGE_SCHEMA,
                SOURCE_PROFILES,
            )

            descriptor.update(model_schema=MODEL_SCHEMA, package_schema=PACKAGE_SCHEMA,
                              source_profiles=list(SOURCE_PROFILES), input_spec=INPUT_SPEC,
                              source_profile="native_sampled_recorded_source_union_v2",
                              source_admission={
                                  "typed_sampled_partition": "exact_originals_reverified",
                                  "origin": "declaration_separate_from_integrity"},
                              model_control=NativeGraphControl().to_dict())
        elif recipe_id in ORDERED_RECIPES:
            from stpd.ordered_source_spec import (
                MODEL_SCHEMA,
                PACKAGE_SCHEMA,
                recipe_control,
                recipe_view,
                view_specs,
            )

            view = recipe_view(recipe_id)
            projection, target = view_specs(view)
            descriptor.update(
                model_schema=MODEL_SCHEMA,
                package_schema=PACKAGE_SCHEMA,
                source_profile="native-logical-source-v3",
                source_admission={
                    "typed_ordered_source3_partition": "originals_reverified",
                    "source_kind": "declaration_not_permission_or_Human_proof",
                },
                model_control=recipe_control(recipe_id).to_dict(),
                projection_spec=projection,
                target_spec=target,
                source_view=view,
            )
        else:
            descriptor["package_schema"] = structured_model_package_schema(
                descriptor["model_schema"]
            )
    else:
        descriptor["limits"] = {}
    from stpd.native_sampled_carry_spec import RECIPE as SAMPLED_RECIPE

    if recipe_id in {NATIVE_SAMPLED_RECIPE, SAMPLED_RECIPE}:
        from stpd.policy.native_operational_outcome import owned_current_known_stale_policy

        descriptor["execution_policy"] = {
            "optional": True,
            "default": None,
            "example": owned_current_known_stale_policy(),
            "max_known_stale_rejections": [1, 16],
            "max_consecutive_known_stale_rejections": "1..min(total,4)",
            "binding": "immutable_run_then_first_model_package",
        }
    return descriptor


def validate_recipe_config(recipe_id: str, config: object) -> dict[str, Any]:
    if recipe_id not in TRUSTED_RECIPES:
        raise BoundaryError("local_training", "unsupported_training_recipe")
    if not isinstance(config, dict):
        raise BoundaryError("local_training", "invalid_recipe_config")
    if recipe_id in STRUCTURED_RECIPES:
        from dataclasses import asdict

        from stpd.structured_workload_contracts import StructuredTrainingConfig

        fields = set(asdict(StructuredTrainingConfig())) | {"checkpoint_every_boundaries"}
        if set(config) - fields:
            raise BoundaryError("local_training", "invalid_recipe_config")
        cadence = config.get("checkpoint_every_boundaries", DEFAULT_CHECKPOINT_CADENCE)
        if type(cadence) is not int or not 1 <= cadence <= 100:
            raise BoundaryError("local_training", "invalid_checkpoint_cadence")
        selected = StructuredTrainingConfig(**{key: value for key, value in config.items()
                                              if key != "checkpoint_every_boundaries"})
        selected.validate()
        return {**asdict(selected), "checkpoint_every_boundaries": cadence}
    if config:
        raise BoundaryError("local_training", "fixed_recipe_config_required")
    return {}


def recipe_adapter(recipe_id: str) -> Any:
    # Explicit imports only: a manifest or HTTP request cannot select an import,
    # executable, path, URL, plugin or arbitrary worker.
    if recipe_id in STRUCTURED_RECIPES:
        from spireagent.workbench.recipes.structured import StructuredRecipeAdapter

        return StructuredRecipeAdapter(recipe_id)
    if recipe_id == DEFAULT_RECIPE or recipe_id in MEMORY_RECIPES:
        from spireagent.workbench.recipes.legacy import LegacyRecipeAdapter

        return LegacyRecipeAdapter()
    raise BoundaryError("local_training", "unsupported_training_recipe")
