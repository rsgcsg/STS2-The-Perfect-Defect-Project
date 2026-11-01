"""Counterexamples for policy drift, game-length weighting and within-game EV."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from stpd.evaluation.value_quality import summarize


def game(name, predicted, targets, floor=10):
    return {"run_id": name, "seed": "seed-" + name, "floor": floor, "result": "loss",
            "predictions": predicted, "targets": targets}


def test_within_game_quality_is_separate_from_between_game_bias():
    result = summarize([game("a", [1., 2.], [0., 1.]),
                        game("b", [-1., 0.], [0., 1.], 20)])
    assert result["per_decision"]["explained_variance"] == -3
    assert result["within_game"]["explained_variance"] == 1
    assert result["mean_game_bias"] == 0
    assert [g["bias_prediction_minus_target"] for g in result["per_game"]] == [1, -1]
    assert result["floor_distribution"]["mean"] == 15


def test_long_high_bias_game_does_not_dominate_macro_game_bias():
    result = summarize([game("long", [1.] * 10, [0.] * 10), game("short", [-1.], [0.])])
    assert result["per_decision"]["bias_prediction_minus_target"] == pytest.approx(9 / 11)
    assert result["mean_game_bias"] == 0
    assert result["equal_game_weighted"]["bias_prediction_minus_target"] == pytest.approx(0)
    assert result["per_decision"]["explained_variance"] is None
    assert sum(b["rows"] for b in result["calibration_fixed_prediction_bins"]) == 11
    assert result["thirds"]["middle"]["games"] == 1


def test_perfect_predictions_preserve_all_thirds_calibration_and_floor_denominator():
    result = summarize([game("a", [.01, .02, .03], [.01, .02, .03]),
                        game("b", [.1, .2, .3, .4, .5, .6], [.1, .2, .3, .4, .5, .6])])
    assert result["games"] == 2 and result["rows"] == 9
    assert result["within_game"]["explained_variance"] == 1
    assert all(result["thirds"][part]["mse"] == 0 for part in ("early", "middle", "late"))
    assert sum(b["rows"] for b in result["calibration_fixed_prediction_bins"]) == 9
    assert result["mean_game_bias_bootstrap95_fixed_group_sensitivity"] == [0, 0]


@pytest.mark.parametrize("change", [
    {"predictions": [float("nan")]}, {"targets": []}, {"result": "unknown"}, {"floor": None},
])
def test_bad_or_non_natural_data_is_not_imputed(change):
    value = game("a", [.1], [.2])
    value.update(change)
    with pytest.raises(ValueError, match="natural_outcome"):
        summarize([value])


def cli():
    path = Path(__file__).resolve().parents[2] / "tools/friday/value_validation.py"
    spec = importlib.util.spec_from_file_location("current_value_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixed_seed_plan_is_permanent_holdout_and_not_reserved_evaluation():
    from stpd.evaluation.suites import seed_membership
    from stpd.rl.critic import held_out

    module = cli()
    plan = module.fixed_seeds(48)
    assert plan == module.fixed_seeds(48)
    assert len(set(plan["seeds"])) == 48
    assert all(held_out(seed) and not seed_membership(seed) for seed in plan["seeds"])
    assert all(len(group["seeds"]) == 1 for group in plan["groups"])


def test_frozen_reference_damage_fails_before_any_dispatch(tmp_path):
    module = cli()
    path = tmp_path / "reference.json"
    path.write_text(json.dumps({"model": "original"}))
    ref = {"path": str(path), "sha256": module.hashed(path)}
    assert module.bound(ref) == {"model": "original"}
    original = copy.deepcopy(ref)
    path.write_text(json.dumps({"model": "other"}))
    assert ref == original
    with pytest.raises(ValueError, match="reference_changed"):
        module.bound(ref)
