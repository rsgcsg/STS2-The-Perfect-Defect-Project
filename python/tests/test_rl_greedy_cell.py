"""Greedy class (user 2026-11-24): temperature exactly 0 = the first highest score.

The sampler records a one-hot q (ties: the lowest catalog position), the value heads read
it as ln T = ln(floor / 2), the profile admits it only in an observation cell, and the
learner never trains a policy on such a row. The service round trip is in
test_p1_fast_inference.test_a_greedy_class_takes_the_highest_score_with_a_one_hot_q, the
profile rules in test_friday_fastchain (two-cell profile).
"""

from __future__ import annotations

import math

import pytest

from stpd.p1.inference_service import check_sampling
from stpd.p1.sampling import GREEDY_TEMPERATURE, distribution, draw, is_greedy
from stpd.rl.heads import GREEDY_VALUE_LOG_TEMPERATURE, value_config_vector
from stpd.rl.learning_diagnostics import exploration_row, row_entropy
from stpd.rl.loss import NORMAL_SAMPLING, _checked_sampling, refuse_greedy_learning_rows

SAMPLING = {"mode": "temperature_uniform", "seed": "s", "arm_id": "greedy",
            "classes": {"combat": {"temperature": 0, "uniform_mix": 0.0},
                        "noncombat": {"temperature": 0.0, "uniform_mix": 0.0}}}


def test_greedy_takes_the_highest_score_and_records_a_one_hot_q():
    logits = [0.3, 2.5, -1.0, 2.4]
    model, q = distribution(logits, GREEDY_TEMPERATURE, 0.0)
    assert q == [0.0, 1.0, 0.0, 0.0] and sum(q) == 1.0
    assert model == distribution(logits, 1.0, 0.0)[1]  # p itself is unchanged
    # every draw is that entry, whatever the consumption id
    assert {draw(q, "seed", f"c{i}") for i in range(200)} == {1}


def test_greedy_ties_take_the_lowest_catalog_position():
    assert distribution([1.0, 3.0, 3.0, 3.0], 0, 0.0)[1] == [0.0, 1.0, 0.0, 0.0]
    assert distribution([5.0, 5.0], 0.0, 0.0)[1] == [1.0, 0.0]


def test_greedy_with_a_uniform_mix_is_the_mixed_one_hot():
    _, q = distribution([0.0, 1.0, 0.5, -2.0], 0, 0.08)
    assert q == pytest.approx([0.02, 0.94, 0.02, 0.02])


def test_only_an_exact_zero_is_greedy():
    assert is_greedy(0) and is_greedy(0.0)
    assert not any(is_greedy(v) for v in (False, 1e-9, -1e-300, "0", None))
    # T just above 0 is still a temperature (and refused by the envelope)
    assert check_sampling({**SAMPLING, "classes": {
        "combat": {"temperature": 0.1, "uniform_mix": 0.0},
        "noncombat": {"temperature": 1.0, "uniform_mix": 0.0}}}) == "sampling_temperature"
    assert check_sampling({**SAMPLING, "classes": {
        "combat": {"temperature": False, "uniform_mix": 0.0},
        "noncombat": {"temperature": 1.0, "uniform_mix": 0.0}}}) == "sampling_temperature"


def test_the_service_opens_greedy_at_any_run_minimum():
    for minimum in (0.25, 0.5, 1.0):
        assert check_sampling(SAMPLING, minimum) is None


def test_value_config_reads_greedy_as_half_the_floor():
    assert math.log(0.125) == pytest.approx(GREEDY_VALUE_LOG_TEMPERATURE)
    assert value_config_vector(SAMPLING["classes"]) == pytest.approx(
        (math.log(0.125), 0.0, math.log(0.125), 0.0))
    # distinct from every sampled cell, sampled cells unchanged
    assert value_config_vector({"combat": {"temperature": 0.25, "uniform_mix": 0.0},
                                "noncombat": {"temperature": 1, "uniform_mix": 0.1}}) == \
        pytest.approx((math.log(0.25), 0.0, 0.0, 1.0))


def test_learner_greedy_rows_take_the_dummy_cell_and_never_learn():
    assert _checked_sampling([0, 0.0]) == NORMAL_SAMPLING
    assert _checked_sampling([0.5, 0.0]) == (0.5, 0.0)
    with pytest.raises(ValueError, match="arm_sampling_missing_or_invalid"):
        _checked_sampling([0, 0.5])
    metadata = [{"arm_sampling": [1.0, 0.0]}, {"arm_sampling": [0, 0.0]}, {}]
    refuse_greedy_learning_rows(metadata, [True, False, True])  # greedy row observed: fine
    with pytest.raises(ValueError, match="greedy_row_needs_an_observation_cell"):
        refuse_greedy_learning_rows(metadata, [True, True, False])


def test_greedy_rows_in_the_exploration_dose():
    assert exploration_row([0.0, 1.0, 0.0], 1, 0, 0.0) == (0, False)
    with pytest.raises(ValueError, match="greedy"):
        row_entropy([0.0, 1.0, 0.0], 0, 0.0)
