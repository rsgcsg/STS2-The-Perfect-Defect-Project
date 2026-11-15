"""RL5-METHODS K: KL stop scope ("all" | "control") and the lr warm-up.

Defaults are pinned: scope "all" (control_rows None) and lr_warmup_steps 0 leave the
update, its result and the optimizer exactly as before K.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, replace

import pytest
import torch
from test_rl41_w1 import model as model
from test_rl41_w1 import ready, segment

from stpd.rl.loss import (
    LossConfig,
    minibatch_control_rows,
    prepared_update,
    warmup_learning_rates,
)
from stpd.rl.performance import PerformanceConfig
from stpd.rl.segment_learner import _resume_config
from stpd.rl.segments import SegmentConfig

OPTIONS = dict(config=LossConfig(mode="ppo", value_coef=.2), performance=PerformanceConfig())
ARMS = ["control"] * 3 + ["hot"] * 3  # rows 0-2 from the control arm's source


def _batch(model):
    values = [segment(model, rows=3, name="a", run_id="a", forced=False),
              segment(model, rows=3, name="b", run_id="b", forced=False)]
    return ready(model, values, parts=((0, 3, 1, 4, 2, 5),))


def _skewed(batch, rows):
    """The minibatch with the recorded q of the given logical rows moved away from theta
    (chosen action's q x 0.1, renormalised): their KL is large, the others' about 0."""
    minibatch = batch.minibatches[0]
    order = [row for group in minibatch.groups for row in group.rows]
    old = minibatch.tensors["old_probabilities"].clone()
    actions = minibatch.tensors["actions"]
    for position, row in enumerate(order):
        if row in rows:
            old[position, actions[position]] *= 0.1
            old[position] /= old[position].sum()
    return replace(minibatch, tensors={**minibatch.tensors, "old_probabilities": old})


def _run(model, minibatch, **kwargs):
    model = copy.deepcopy(model)
    optimizer = torch.optim.AdamW(model.parameters(), lr=4e-5)
    result = prepared_update(model, optimizer, minibatch, **OPTIONS, **kwargs)
    return result, model, optimizer


def test_control_rows_follow_tensor_order_and_only_multi_choice_control_rows(model):
    batch = _batch(model)
    minibatch = batch.minibatches[0]
    order = [row for group in minibatch.groups for row in group.rows]
    assert minibatch_control_rows(batch, minibatch, ARMS) == [row < 3 for row in order]
    assert not any(minibatch_control_rows(batch, minibatch, None))
    forced = ready(model, [segment(model, rows=4, forced=True)])
    flags = minibatch_control_rows(forced, forced.minibatches[0], ["control"] * 4)
    policy = forced.policy_mask.tolist()
    assert flags == [policy[row] for group in forced.minibatches[0].groups
                     for row in group.rows] and not all(flags)  # forced rows excluded


def test_default_scope_is_unchanged_and_all_control_rows_reproduce_it(model):
    """control_rows None = the rule before K (no new fields). Every row a control row
    measures the same KL, takes the same decision and the same Adam step."""
    batch = _batch(model)
    minibatch = _skewed(batch, {3, 4, 5})
    default, theta, adam = _run(model, minibatch, target_kl=100)
    assert default["stepped"] and not {"kl_stop_basis", "approx_kl_control"} & set(default)
    every = [True] * len(minibatch.tensors["targets"])
    scoped, theta2, adam2 = _run(model, minibatch, target_kl=100, control_rows=every)
    assert scoped["kl_stop_basis"] == "control" and scoped["kl_control_rows"] == 6
    assert scoped["approx_kl_control"] == pytest.approx(default["approx_kl_mu_theta"], rel=1e-5)
    assert scoped["approx_kl_mu_theta"] == default["approx_kl_mu_theta"]
    assert scoped["value_statistics"] == default["value_statistics"]
    for name, tensor in theta.state_dict().items():
        assert torch.equal(tensor, theta2.state_dict()[name])
    for left, right in zip(adam.state.values(), adam2.state.values(), strict=True):
        assert all(torch.equal(left[key], right[key]) for key in left)


def test_control_scope_ignores_exploration_rows_and_records_both_kls(model):
    batch = _batch(model)
    minibatch = _skewed(batch, {3, 4, 5})  # the "hot" arm deviates on purpose
    control = minibatch_control_rows(batch, minibatch, ARMS)
    measured, _, _ = _run(model, minibatch, target_kl=100, control_rows=control)
    all_rows, control_kl = measured["approx_kl_mu_theta"], measured["approx_kl_control"]
    assert measured["kl_control_rows"] == 3 and 0 <= control_kl < all_rows / 10
    target = (all_rows + control_kl) / 2
    default, _, _ = _run(model, minibatch, target_kl=target)
    assert default["kl_stopped"] and not default["stepped"]
    scoped, _, _ = _run(model, minibatch, target_kl=target, control_rows=control)
    assert scoped["stepped"] and not scoped["kl_stopped"]
    assert scoped["approx_kl_mu_theta"] == pytest.approx(all_rows)
    # the control arm itself deviating still stops the step
    hot_control = _skewed(batch, {0, 1, 2})
    stopped, _, _ = _run(model, hot_control, target_kl=target, control_rows=control)
    assert stopped["kl_stopped"] and stopped["kl_stop_basis"] == "control"


def test_minibatch_without_control_rows_uses_all_rows_and_says_so(model):
    batch = _batch(model)
    minibatch = _skewed(batch, {3, 4, 5})
    none = [False] * len(minibatch.tensors["targets"])
    measured, _, _ = _run(model, minibatch, target_kl=100)
    target = measured["approx_kl_mu_theta"] / 2
    result, _, _ = _run(model, minibatch, target_kl=target, control_rows=none)
    assert result["kl_stop_basis"] == "all_rows_no_control" and result["kl_stopped"]
    assert result["kl_control_rows"] == 0 and result["approx_kl_control"] is None
    with pytest.raises(ValueError, match="control_rows_must_cover"):
        _run(model, minibatch, control_rows=none[:-1])


def test_warmup_is_linear_proportional_per_group_and_resumes_from_the_update_count():
    weights = [torch.nn.Parameter(torch.zeros(1)), torch.nn.Parameter(torch.zeros(1))]
    optimizer = torch.optim.AdamW([{"params": [weights[0]], "lr": 7.0},
                                   {"params": [weights[1]], "lr": 3.0}])
    warmup_learning_rates(optimizer, (1.13e-4, 1e-4), 0, 0)  # default: untouched
    assert [g["lr"] for g in optimizer.param_groups] == [7.0, 3.0]
    seen = []
    for updates in (0, 1, 3, 4, 2000):  # a resume restores `updates`: no restart
        warmup_learning_rates(optimizer, (1.13e-4, 1e-4), updates, 4)
        seen.append([g["lr"] for g in optimizer.param_groups])
    assert seen[0] == pytest.approx([1.13e-4 / 4, 1e-4 / 4])
    assert seen[1] == pytest.approx([1.13e-4 / 2, 1e-4 / 2])
    assert seen[2] == seen[3] == seen[4] == [1.13e-4, 1e-4]


def test_settings_validate_and_defaults_stay_out_of_the_resume_recipe():
    assert SegmentConfig().kl_stop_scope == "all" and SegmentConfig().lr_warmup_steps == 0
    for bad in ({"kl_stop_scope": "explore"}, {"lr_warmup_steps": -1},
                {"lr_warmup_steps": 2.5}):
        with pytest.raises(ValueError, match="invalid_segment_configuration"):
            SegmentConfig(**bad)

    def recipe(**segments):
        return _resume_config({"loss": {}, "segments": asdict(SegmentConfig(**segments))})

    before_k = recipe()
    assert not {"kl_stop_scope", "lr_warmup_steps"} & set(before_k["segments"])
    old_cp = copy.deepcopy(before_k)  # a CP config written before K has neither key
    assert recipe() == _resume_config(old_cp)
    assert recipe(kl_stop_scope="control")["segments"]["kl_stop_scope"] == "control"
    assert recipe(lr_warmup_steps=2000) != before_k  # resume-locked off the default


def test_control_kl_rides_the_data_parallel_reduction(model):
    """Under "control" the control-row KL joins the one all-reduce payload after the value
    sums (so every rank decides alike); the default payload is unchanged."""
    from stpd.rl.learning_diagnostics import SIZE

    batch = _batch(model)
    minibatch = _skewed(batch, {3, 4, 5})
    control = minibatch_control_rows(batch, minibatch, ARMS)
    seen = []

    def halve(_model, totals, valid):  # stands in for a reduce that rescales everything
        seen.append(totals.numel())
        return totals / 2, valid

    plain, _, _ = _run(model, minibatch, target_kl=100, control_rows=control)
    synced, _, _ = _run(model, minibatch, target_kl=100, control_rows=control,
                        gradient_sync=halve)
    default, _, _ = _run(model, minibatch, target_kl=100, gradient_sync=halve)
    assert seen == [8 + SIZE + 1, 8 + SIZE]
    assert synced["approx_kl_control"] == pytest.approx(plain["approx_kl_control"] / 2)
    assert synced["approx_kl_mu_theta"] == pytest.approx(plain["approx_kl_mu_theta"] / 2)
    assert synced["value_statistics"] == default["value_statistics"]
