"""The real service admits old M0 preflight without imposing Structured plans."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from spireagent.workbench import local_training as module
from spireagent.workbench.developer import ProjectConfig
from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.recipes.historical_m0 import (
    HistoricalM0RecipeAdapter,
    HistoricalPreflight,
)
from spireagent.workbench.recipes.reward_m0 import RewardM0Preflight, RewardM0RecipeAdapter
from spireagent.workbench.trusted_recipes import HISTORICAL_M0_RECIPE, REWARD_M0_RECIPE
from stpd.historical_m0_spec import HistoricalM0Config
from stpd.reward_m0_spec import RewardM0Config


@pytest.mark.parametrize("recipe", [HISTORICAL_M0_RECIPE, REWARD_M0_RECIPE])
def test_existing_m0_service_start_keeps_its_own_preflight_and_never_calls_structured_plan(
    tmp_path, monkeypatch, recipe
):
    # The domain preflight itself has independent real-source regressions. This
    # fixture supplies that exact owned result type at the ordinary service boundary.
    owner = SimpleNamespace(path=tmp_path / "curation.sqlite", identity=("fixture",))
    if recipe == HISTORICAL_M0_RECIPE:
        adapter = HistoricalM0RecipeAdapter(recipe)
        prepared = HistoricalPreflight(())
        config = HistoricalM0Config(warm_start_model_id="3" * 64, steps=1).to_dict()
    else:
        adapter = RewardM0RecipeAdapter(recipe)
        prepared = RewardM0Preflight((), {})
        config = RewardM0Config(steps=1).to_dict()
    service = LocalTrainingService(ProjectConfig(tmp_path, "", "", None, {}))
    monkeypatch.setattr(service, "_selected", lambda: (owner, object(), None))
    monkeypatch.setattr(adapter, "preflight_request", lambda *args: prepared)
    monkeypatch.setattr(module, "require_recipe_dependencies", lambda *args: None)
    import spireagent.workbench.trusted_recipes as registry

    monkeypatch.setattr(registry, "recipe_adapter", lambda _: adapter)
    launched = []

    def launch(held, path, operation, owner, store):
        launched.append(operation)
        held.__exit__(None, None, None)

    monkeypatch.setattr(service, "_launch_attempt", launch)
    request = TrainingRequest("1" * 32, recipe, "2" * 64, config,
                              limits={"wall_seconds": 30})
    result = service.start(request)["operation"]
    assert result["status"] == "pending" and len(launched) == 1
    assert "training_plan" not in launched[0]
    assert result["training_plan"] is None
