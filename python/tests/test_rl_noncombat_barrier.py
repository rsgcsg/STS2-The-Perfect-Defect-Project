"""`loss.noncombat_log_barrier_coef` beta (user 2026-11-24): -beta * the mean over a learning
noncombat policy row's legal catalog of log pi(a|s), with the policy term's row weight.

Default 0 is pinned to the loss before the knob (same value and gradient, no readout keys,
same recipe digest); beta > 0 adds beta * w * (pi - 1/N) to a noncombat row's score
gradient and nothing to combat or non-policy rows, which lifts the smallest probability.
"""

from __future__ import annotations

import copy
import math
from dataclasses import asdict

import pytest
import torch
from test_rl41_w1 import model as model  # noqa: F401  (fixture)
from test_rl_explore_ratio_floor import OPTIONS, _before_knob, _data
from test_rl_rows_forward import prepared

from spireagent.json_boundary import BoundaryError
from stpd.rl import recipe_changes
from stpd.rl.loss import (
    BARRIER_LOG_FLOOR,
    BARRIER_READOUT,
    LossConfig,
    barrier_log_probabilities,
    batch_noncombat_rows,
    batched_segment_loss,
    prepared_update,
    rl_update,
    with_noncombat,
)
from stpd.rl.performance import PerformanceConfig
from stpd.rl.segment_learner import _resume_config

NONCOMBAT = [1., 0., 1., 1., 0., 1., 1., 0.]  # row 5 is forced (not a policy row)


def _barrier_data():
    scores, logits, hp, t = _data()
    # three legal entries; row 3 has two and one padded entry (-inf, as a policy group pads)
    third = torch.full((len(scores), 1), -3.0)
    third[3] = -math.inf
    scores = torch.cat((scores.detach(), third), 1).requires_grad_(True)
    old = torch.cat((t["old_probabilities"] * .9, torch.full((len(scores), 1), .1)), 1)
    old[3] = torch.cat((t["old_probabilities"][3], torch.zeros(1)))
    t["old_probabilities"] = old
    t["catalog_valid"] = torch.ones((len(scores), 3), dtype=torch.bool)
    t["catalog_valid"][3, 2] = False
    t["noncombat"] = torch.tensor(NONCOMBAT)
    return scores, logits, hp, t


def test_default_is_bit_identical_to_the_loss_before_the_knob():
    scores, logits, hp, t = _data()
    reference = _before_knob(scores, logits, hp, t, **OPTIONS)
    for extra in ({}, {"noncombat_log_barrier_coef": 0.0}, {"barrier_rows": []},
                  {"noncombat_log_barrier_coef": 0.0, "barrier_rows": []}):
        loss, totals, components, _ = batched_segment_loss(scores, logits, hp, t, **OPTIONS,
                                                           **extra)
        assert torch.equal(components[0], reference)
        assert torch.equal(torch.autograd.grad(components[0], scores)[0],
                           torch.autograd.grad(reference, scores, retain_graph=True)[0])
        assert extra.get("barrier_rows", []) == []  # nothing collected at 0


@pytest.mark.parametrize("beta", [.02, .5])
def test_barrier_gradient_lifts_the_rare_options_of_noncombat_rows_only(beta):
    scores, logits, hp, t = _barrier_data()
    off = batched_segment_loss(scores, logits, hp, t, **OPTIONS)
    rows: list[torch.Tensor] = []
    on = batched_segment_loss(scores, logits, hp, t, noncombat_log_barrier_coef=beta,
                              barrier_rows=rows, **OPTIONS)
    assert torch.equal(on[1], off[1])  # policy, KL, entropy, clip ... unchanged
    delta = (torch.autograd.grad(on[2][0], scores)[0]
             - torch.autograd.grad(off[2][0], scores)[0])
    valid = t["catalog_valid"]
    probability = scores.detach().masked_fill(~valid, -math.inf).softmax(-1)
    weight = t["policy_mask"] * t["noncombat"] / OPTIONS["multi"]
    count = valid.sum(-1)
    for row in range(len(scores)):
        want = beta * weight[row] * (probability[row] - 1 / count[row])
        assert torch.allclose(delta[row][valid[row]], want[valid[row]], atol=1e-7)
        assert torch.equal(delta[row][~valid[row]], torch.zeros_like(delta[row][~valid[row]]))
    assert weight.gt(0).sum() == 4 and (delta[weight == 0] == 0).all()
    # a descent step raises the smallest legal probability of every barrier row
    stepped = (scores.detach() - 5 * delta).masked_fill(~valid, -math.inf).softmax(-1)
    smallest = probability.masked_fill(~valid, math.inf).amin(-1)
    after = stepped.masked_fill(~valid, math.inf).amin(-1)
    uneven = (weight > 0) & (smallest < 1 / count - 1e-6)  # row 3 is uniform: nothing to lift
    assert uneven.sum() == 3 and (after[uneven] > smallest[uneven]).all()
    assert torch.equal(after[weight == 0], smallest[weight == 0])
    # readout rows: the term per barrier row and its smallest legal probability
    [table] = rows
    term = -beta * (probability.log().masked_fill(~valid, 0).sum(-1) / count) * weight
    assert torch.allclose(table[weight > 0, 0], term[weight > 0], atol=1e-7)
    assert torch.allclose(table[weight > 0, 1], smallest[weight > 0])
    assert torch.isnan(table[weight == 0]).all()
    assert float((on[2][0] - off[2][0]).detach()) == pytest.approx(float(term.sum()), rel=1e-5)


def test_noncombat_rows_come_from_the_metadata_and_never_default():
    class Request:
        members = [type("M", (), {"rows": 2})()]
        auxiliary = (None, None)

    assert batch_noncombat_rows([{"in_combat": True}, {"in_combat": False}], Request) == \
        [False, True, False, False]
    with pytest.raises(ValueError, match="needs_row_in_combat"):
        batch_noncombat_rows([{"in_combat": True}, {}], Request)


def test_barrier_config_is_validated_resume_locked_and_declarable():
    assert LossConfig().noncombat_log_barrier_coef == 0.0
    for bad in (-.1, math.nan, math.inf, True, "0.1"):
        with pytest.raises(BoundaryError):
            LossConfig(noncombat_log_barrier_coef=bad)

    def recipe(**loss):
        return _resume_config({"loss": asdict(LossConfig(**loss)), "segments": {}})

    old_cp = {"loss": {k: v for k, v in asdict(LossConfig()).items()
                       if k != "noncombat_log_barrier_coef"}, "segments": {}}
    assert recipe() == _resume_config(old_cp)  # same recipe digest at 0
    assert recipe(noncombat_log_barrier_coef=.02) != recipe()  # undeclared: refused
    assert recipe_changes.check(
        {"checkpoint_artifact_id": "cp", "changes": {
            "loss.noncombat_log_barrier_coef": {"old": 0.0, "new": .02}}},
        _resume_config(old_cp), recipe(noncombat_log_barrier_coef=.02)) == {
            "loss.noncombat_log_barrier_coef": {"old": 0.0, "new": .02}}


def test_rl_update_refuses_the_barrier(model):
    with pytest.raises(BoundaryError, match="noncombat_log_barrier_needs_prepared_update"):
        rl_update(model, model, torch.optim.AdamW(model.parameters()), [], [], 1e-5,
                  LossConfig(mode="ppo", value_coef=.2, noncombat_log_barrier_coef=.1))


def test_prepared_update_needs_the_rows_exactly_when_the_barrier_is_on(model):
    batch = prepared(model, "rows-v2")
    minibatch = batch.minibatches[0]
    rows = [True] * (1 + max(r for g in minibatch.groups for r in g.rows))
    performance = PerformanceConfig(prepared_forward="rows-v2")
    for config, staged in ((LossConfig(mode="ppo", noncombat_log_barrier_coef=.02), minibatch),
                           (LossConfig(mode="ppo"), with_noncombat(minibatch, rows, "cpu"))):
        trained = copy.deepcopy(model)
        with pytest.raises(ValueError, match="noncombat_rows_must_match"):
            prepared_update(trained, torch.optim.AdamW(trained.parameters()), staged,
                            config=config, performance=performance)


def test_one_step_reports_the_barrier_readout_and_default_reports_none(model):
    batch = prepared(model, "rows-v2")
    minibatch = batch.minibatches[0]
    rows = [True] * (1 + max(r for g in minibatch.groups for r in g.rows))
    performance = PerformanceConfig(prepared_forward="rows-v2")
    trained = copy.deepcopy(model)
    off = prepared_update(trained, torch.optim.AdamW(trained.parameters(), lr=1e-4), minibatch,
                          config=LossConfig(mode="ppo", value_coef=.2), target_kl=1e9,
                          performance=performance)
    assert not set(BARRIER_READOUT) & set(off)
    trained = copy.deepcopy(model)
    on = prepared_update(trained, torch.optim.AdamW(trained.parameters(), lr=1e-4),
                         with_noncombat(minibatch, rows, "cpu"),
                         config=LossConfig(mode="ppo", value_coef=.2,
                                           noncombat_log_barrier_coef=.02),
                         target_kl=1e9, performance=performance)
    print({name: on[name] for name in BARRIER_READOUT})
    assert on["noncombat_barrier_rows"] == minibatch.policy_rows > 0
    assert on["noncombat_barrier"] > 0  # -mean log pi > 0
    assert 0 < on["noncombat_min_probability_p10"] <= on["noncombat_min_probability_p50"] <= .5
    # the same rows' policy loss and KL as without the barrier (it changes no other total)
    for key in ("policy_loss", "approx_kl_mu_theta", "entropy", "clip_fraction", "td_loss"):
        assert on[key] == pytest.approx(off[key], rel=1e-6, abs=1e-9), key


def test_barrier_log_probability_has_a_floor_with_the_exact_gradient():
    """FP32 log_softmax is -inf on a legal entry when the finite score spread overflows;
    the barrier's log pi is floored at BARRIER_LOG_FLOOR there (and for any entry below
    it), with the log_softmax gradient kept (e_i - pi), so the term stays finite and a
    rare option is still lifted. Rows above the floor are the plain log_softmax."""
    scores = torch.tensor([[3e38, -3e38, 0.0],      # overflow: -inf without the floor
                           [0.0, -120.0, -math.inf],     # far below the floor; padded entry
                           [0.5, -0.5, 1.0]]).requires_grad_(True)
    valid = torch.tensor([[True, True, True], [True, True, False], [True, True, True]])
    logp = scores.float().log_softmax(-1)
    assert math.isinf(float(logp[0, 1].detach()))  # the case the floor is for
    probability = logp.exp()
    barrier = barrier_log_probabilities(scores, logp, probability, valid)
    assert torch.isfinite(barrier).all()
    value = barrier.detach()
    assert float(value[0, 1]) == float(value[1, 1]) == BARRIER_LOG_FLOOR
    assert float(value[1, 2]) == 0.0  # outside the catalog
    assert torch.equal(barrier[2], logp[2])  # above the floor: unchanged, bit for bit
    weights = torch.tensor([[1.0, 2.0, 3.0], [1.0, 2.0, 0.0], [1.0, 2.0, 3.0]])
    gradient = torch.autograd.grad((barrier * weights).sum(), scores)[0]
    # the exact log_softmax Jacobian on the catalog: g_j - pi_j * sum(g)
    p = probability.detach().masked_fill(~valid, 0)
    expected = (weights - p * weights.sum(-1, keepdim=True)).masked_fill(~valid, 0)
    assert torch.isfinite(gradient).all()
    assert torch.allclose(gradient, expected, atol=1e-6)
    # the rare entry is lifted: its own gradient is not cut off by the floor
    assert float(gradient[1, 1]) > 1.9
