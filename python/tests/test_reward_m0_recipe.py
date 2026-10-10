"""Trusted recipe admission and ordinary v3 lifecycle over sealed synthetic data."""

from __future__ import annotations

import uuid

import pytest
from test_artifact_store_v1 import PRODUCER
from test_reward_m0_pair import limits
from test_reward_m0_pair import pair_originals as pair_originals

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.trusted_recipes import (
    STRUCTURED_RECIPE,
    STRUCTURED_RECIPES,
    TYPED_RECIPES,
    describe_recipe,
    recipe_adapter,
    typed_recipe_checkpoint_schema,
    typed_recipe_run_schema,
)
from stpd.reward_m0_spec import RECIPE, RewardM0Config


def publish_pair(fixture, monkeypatch):
    _, _, config, historical, ids = fixture
    monkeypatch.setattr(
        "spireagent.workbench.local_dataset.source_identity", lambda _root: PRODUCER
    )
    service = LocalDatasetService(config)
    selected = RewardM0Config(steps=2, state_vocab_size=256)
    service.start_reward_m0_preview(
        historical["cached_view_id"], ids, historical, selected.to_dict(), limits()
    )
    service.thread.join(timeout=10)
    preview = service.status()["operation"]
    assert preview["status"] == "preview_ready", preview
    service.start_publish(preview["preview_id"])
    service.thread.join(timeout=10)
    result = service.status()["operation"]
    assert result["status"] == "completed", result
    return result["training_source_id"], selected


def test_explicit_trusted_dispatch_preserves_structured_domain_and_unknown_rejection():
    assert RECIPE in TYPED_RECIPES and RECIPE not in STRUCTURED_RECIPES
    assert type(recipe_adapter(RECIPE)).__name__ == "RewardM0RecipeAdapter"
    assert type(recipe_adapter(STRUCTURED_RECIPE)).__name__ == "StructuredRecipeAdapter"
    assert typed_recipe_run_schema(RECIPE) != typed_recipe_run_schema(STRUCTURED_RECIPE)
    assert typed_recipe_checkpoint_schema(RECIPE) != typed_recipe_checkpoint_schema(
        STRUCTURED_RECIPE
    )
    description = describe_recipe(RECIPE)
    assert description["remote_execution"] is False
    assert description["numerical_devices"] == ["cpu", "mps"]
    assert description["evaluation_scope"] == "TRAIN_internal_diagnostic"
    for method in (recipe_adapter, typed_recipe_run_schema, typed_recipe_checkpoint_schema):
        with pytest.raises(BoundaryError):
            method("unregistered-arbitrary-module")


@pytest.mark.parametrize("change", [None, "seed", "steps", "tokens", "wall", "scratch"])
def test_source_frozen_pair_config_and_limits_gate_before_pending(
    pair_originals,
    monkeypatch,
    change,
):
    store, owner, config, _, _ = pair_originals
    source_id, frozen = publish_pair(pair_originals, monkeypatch)
    selected = frozen.to_dict()
    selected["arm"] = "B"
    selected_limits = limits()
    if change in {"seed", "steps"}:
        selected[change] += 1
    elif change == "tokens":
        selected["max_state_tokens"] -= 1
    elif change == "wall":
        selected_limits["wall_seconds"] += 1
    elif change == "scratch":
        selected_limits["scratch_bytes"] -= 1
    request = TrainingRequest(uuid.uuid4().hex, RECIPE, source_id, selected, limits=selected_limits)
    before = store.manifest_ids()
    if change is not None:
        from spireagent.workbench.local_training import LocalTrainingService

        service = LocalTrainingService(config)
        with pytest.raises(BoundaryError, match="frozen_pair_config_or_limits_mismatch"):
            service.start(request)
        assert service._thread is None
    else:
        result = recipe_adapter(RECIPE).preflight_request(store, owner, request)
        assert len(result.runs) == 2
        assert set(result.paired_lengths) == {"A", "B"}
    assert store.manifest_ids() == before
