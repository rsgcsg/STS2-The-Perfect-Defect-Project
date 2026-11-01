"""Counterexamples for policy drift, game-length weighting and within-game EV."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from stpd.evaluation.value_quality import distribution, summarize, temporal_quality


def test_temporal_perfect_and_rescaled_paths_keep_correlation_and_jitter_distinct():
    target = [1., 2., 4., 8.]
    perfect = temporal_quality(target, target)
    scaled = temporal_quality([2 * x for x in target], target)
    assert perfect["correlation"] == pytest.approx(1)
    assert perfect["jitter_ratio"] == pytest.approx(1)
    assert scaled["correlation"] == pytest.approx(1)
    assert scaled["jitter_ratio"] == pytest.approx(2)


def test_zero_jitter_is_preserved_rather_than_called_perfect():
    result = temporal_quality([1., 2., 3., 4.], [1., 2., 4., 8.])
    assert result["correlation"] > 0
    assert result["jitter_ratio"] == 0
    assert result["target_difference_std"] > 0


@pytest.mark.parametrize("p,t,corr_reason,jitter_reason", [
    ([1.], [2.], "fewer_than_two_rows", "fewer_than_three_rows"),
    ([1., 2.], [2., 4.], None, "fewer_than_three_rows"),
    ([1., 1., 1.], [2., 2., 2.], "constant_prediction_or_target", "constant_target_differences"),
    ([1., 2., 3.], [3., 2., 1.], None, "constant_target_differences"),
])
def test_undefined_temporal_denominators_remain_explicit(p, t, corr_reason, jitter_reason):
    result = temporal_quality(p, t)
    assert result["correlation_undefined_reason"] == corr_reason
    assert result["jitter_undefined_reason"] == jitter_reason
    if corr_reason:
        assert result["correlation"] is None
    if jitter_reason:
        assert result["jitter_ratio"] is None


def test_near_constant_target_is_flagged_without_epsilon_or_imputation():
    target = [1., 1. + 1e-10, 1. + 3e-10]
    result = temporal_quality(target, target)
    assert result["target_differences_near_constant"]
    assert result["jitter_ratio"] == pytest.approx(1)
    assert result["correlation"] == pytest.approx(1)


def test_equal_game_distribution_preserves_missing_games_and_interpolates():
    result = distribution([None, 1., 3., None])
    assert result["total_games"] == 4 and result["valid_games"] == 2
    assert result["undefined_games"] == 2 and result["median"] == 2
    assert result["p10"] == pytest.approx(1.2)


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


def test_offline_gates_keep_flat_and_undefined_temporal_candidates_failed_and_serializable():
    path = Path(__file__).resolve().parents[2] / "tools/friday/value_head_offline.py"
    spec = importlib.util.spec_from_file_location("value_head_offline_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = [1., 2., 4., 8.]
    baseline = summarize([game("a",target,target)])
    gates,logs = module.quality_gates(baseline,baseline)
    assert gates['correlation_valid_count'] and gates['correlation_median']
    assert gates['jitter_valid_count'] and gates['jitter_no_flat_collapse']
    flat = summarize([game("a",[1.,1.,1.,1.],target)])
    gates,logs = module.quality_gates(baseline,flat)
    assert not gates['correlation_valid_count'] and not gates['correlation_median']
    assert not gates['jitter_no_flat_collapse'] and not gates['jitter_log_distance_25percent']
    assert logs['candidate'] is None
    json.dumps({'gates':gates,'logs':logs},allow_nan=False)
    insufficient = summarize([game('a',[1.],[1.])])
    # EV is undefined for this cohort; no runtime comparison should produce a pass.
    assert insufficient['per_decision']['explained_variance'] is None
    gates,logs = module.quality_gates(insufficient,insufficient)
    assert not gates['per_decision_EV'] and not gates['equal_game_weighted_EV']
    assert not gates['correlation_median'] and not gates['jitter_log_distance_25percent']
    json.dumps({'gates':gates,'logs':logs},allow_nan=False)
