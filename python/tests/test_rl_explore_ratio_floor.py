"""`loss.explore_ratio_floor` c: a policy row with r < c takes the gradient c A grad log p.

Default 0 is pinned to the loss before the knob (same value, same gradient, no new row
facts, same recipe digest); c > 0 changes only the floored rows' gradient.
"""

from __future__ import annotations

import copy
import math
from dataclasses import asdict, replace

import pytest
import torch
from test_rl41_w1 import model as model
from test_rl41_w1 import ready, segment

from spireagent.json_boundary import BoundaryError
from stpd.rl import recipe_changes
from stpd.rl.learning_diagnostics import explore_floor_by_arm
from stpd.rl.loss import LossConfig, batched_segment_loss, prepared_update
from stpd.rl.performance import PerformanceConfig
from stpd.rl.segment_learner import _resume_config, _with_policy_by_arm

OPTIONS = dict(clip=.2, entropy_coef=.01, value_coef=.2, terminal_coef=.1, hp_coef=.1,
               multi=5, td_rows=6, terminal_rows=0, hp_rows=0)
# Each row: action 0 with theta p(a) and behaviour q(a); r = p / q is
# .4, ~.02, ~.02, 1.25, .1 and 1.25 (the last row is forced: not a policy row).
P = [.2, .02, .02, .5, .05, .5]
Q = [.5, .998, .998, .4, .5, .4]
A = [1., 2., -2., 1., -1.5, 999.]


def _data():
    scores = torch.tensor([[math.log(p), math.log(1 - p)] for p in P], requires_grad=True)
    targets = torch.zeros((6, 51))
    targets[:, 18] = 1
    t = {"actions": torch.zeros(6, dtype=torch.long),
         "old_probabilities": torch.tensor([[q, 1 - q] for q in Q]),
         "advantages": torch.tensor(A), "targets": targets, "hp_targets": torch.zeros(6),
         "policy_mask": torch.tensor([1., 1., 1., 1., 1., 0.]), "td_mask": torch.ones(6),
         "terminal_mask": torch.zeros(6), "hp_mask": torch.zeros(6),
         "catalog_valid": torch.ones((6, 2), dtype=torch.bool),
         "outcome_valid": torch.ones((6, 51), dtype=torch.bool)}
    return scores, torch.zeros((6, 51), requires_grad=True), torch.zeros(6, requires_grad=True), t


def _before_knob(scores, logits, hp, t, clip, multi, entropy_coef, **_):
    """The policy part of the loss as written before the knob (reference copy)."""
    logp = scores.float().log_softmax(-1)
    probability = logp.exp()
    safe_logp = logp.masked_fill(~t["catalog_valid"], 0)
    action = t["actions"][:, None]
    log_ratio = logp.gather(1, action)[:, 0] - t["old_probabilities"].gather(1, action)[:, 0].log()
    ratio = log_ratio.exp()
    advantage = t["advantages"]
    policy_mask = t["policy_mask"] / max(multi, 1)
    policy = -(
        torch.minimum(ratio * advantage, ratio.clamp(1 - clip, 1 + clip) * advantage) * policy_mask
    ).sum()
    entropy = (-(probability * safe_logp).sum(-1) * policy_mask).sum()
    return policy - entropy_coef * entropy


def test_default_is_bit_identical_to_the_loss_before_the_knob():
    scores, logits, hp, t = _data()
    components = batched_segment_loss(scores, logits, hp, t, **OPTIONS)[2]
    reference = _before_knob(scores, logits, hp, t, **OPTIONS)
    assert torch.equal(components[0], reference)
    assert torch.equal(torch.autograd.grad(components[0], scores)[0],
                       torch.autograd.grad(reference, scores)[0])
    zero = batched_segment_loss(scores, logits, hp, t, explore_ratio_floor=0.0, **OPTIONS)
    default = batched_segment_loss(scores, logits, hp, t, **OPTIONS)
    assert torch.equal(zero[0], default[0]) and torch.equal(zero[1], default[1])
    observed: list[torch.Tensor] = []
    batched_segment_loss(scores, logits, hp, t, row_diagnostics=observed, **OPTIONS)
    assert observed[0].shape == (6, 6)  # no new row columns at c = 0


@pytest.mark.parametrize("floor", [.05, .3])
def test_floored_rows_take_c_A_grad_log_p_both_signs_others_unchanged(floor):
    scores, logits, hp, t = _data()
    logp = scores.log_softmax(-1)
    chosen = logp.gather(1, t["actions"][:, None])[:, 0]
    ratio = (chosen - t["old_probabilities"].gather(1, t["actions"][:, None])[:, 0].log()).exp()
    floored = (ratio.detach() < floor) & t["policy_mask"].bool()
    assert floored.any() and (~floored & t["policy_mask"].bool()).any()
    base = batched_segment_loss(scores, logits, hp, t, **OPTIONS)
    on = batched_segment_loss(scores, logits, hp, t, explore_ratio_floor=floor, **OPTIONS)
    # the loss value and every total (KL, clip fraction, ...) are unchanged
    assert torch.equal(on[1], base[1]) and torch.allclose(on[0], base[0])
    per_row_on = torch.autograd.grad(on[2][0], scores)[0]
    per_row_base = torch.autograd.grad(base[2][0], scores)[0]
    entropy_only = torch.autograd.grad(-OPTIONS["entropy_coef"] * (
        -(logp.exp() * logp).sum(-1) * t["policy_mask"] / OPTIONS["multi"]).sum(), scores,
        retain_graph=True)[0]
    for row in range(6):
        if floored[row]:
            # -(c A / multi) grad log p(a) plus the unchanged entropy part
            want = -floor * t["advantages"][row] / OPTIONS["multi"] * torch.autograd.grad(
                chosen[row], scores, retain_graph=True)[0][row]
            assert torch.allclose(per_row_on[row], want + entropy_only[row], atol=1e-6)
            assert not torch.allclose(per_row_on[row], per_row_base[row])
        else:
            assert torch.equal(per_row_on[row], per_row_base[row])
    signs = {float(t["advantages"][row].sign()) for row in range(6) if floored[row]}
    assert signs == {1.0, -1.0}  # symmetric: A < 0 rows are floored too


def test_gradients_agree_at_the_floor_for_an_unclipped_row():
    """r just below and just above c, A > 0: c A grad log p vs r A grad log p (r ~ c)."""
    floor = .3
    grads = []
    for r in (floor - 1e-4, floor + 1e-4):
        scores, logits, hp, t = _data()
        p = P[0]  # row 0 (A = 1): theta p(a) = .2 and q(a) = p / r
        t["old_probabilities"][0] = torch.tensor([p / r, 1 - p / r])
        loss = batched_segment_loss(scores, logits, hp, t, explore_ratio_floor=floor,
                                    **OPTIONS)[2][0]
        grads.append(torch.autograd.grad(loss, scores)[0][0])
    assert torch.allclose(grads[0], grads[1], rtol=1e-3, atol=1e-6)


def test_config_validates_the_range():
    assert LossConfig().explore_ratio_floor == 0.0
    LossConfig(explore_ratio_floor=.5)
    for bad in (-.01, .51, math.nan, True, "0.1"):
        with pytest.raises(BoundaryError):
            LossConfig(explore_ratio_floor=bad)


def test_recipe_digest_unchanged_at_default_and_declarable():
    def recipe(**loss):
        return _resume_config({"loss": asdict(LossConfig(**loss)), "segments": {}})

    old_cp = {"loss": {k: v for k, v in asdict(LossConfig()).items()
                       if k != "explore_ratio_floor"}, "segments": {}}
    assert recipe() == _resume_config(old_cp)
    assert "explore_ratio_floor" not in recipe()["loss"]
    assert recipe(explore_ratio_floor=.2)["loss"]["explore_ratio_floor"] == .2
    assert "loss.explore_ratio_floor" in recipe_changes.DECLARABLE
    changes = recipe_changes.check(
        {"checkpoint_artifact_id": "cp", "changes": {
            "loss.explore_ratio_floor": {"old": 0.0, "new": .2}}},
        _resume_config(old_cp), recipe(explore_ratio_floor=.2))
    assert changes == {"loss.explore_ratio_floor": {"old": 0.0, "new": .2}}
    with pytest.raises(ValueError, match="undeclared_recipe_change"):
        recipe_changes.check({"checkpoint_artifact_id": "cp", "changes": {
            "loss.entropy_coef": {"old": 0.0, "new": .01}}},
            _resume_config(old_cp), recipe(entropy_coef=.01, explore_ratio_floor=.2))


def _update(model, floor):
    values = [segment(model, rows=3, name="a", run_id="a", forced=False),
              segment(model, rows=3, name="b", run_id="b", forced=False)]
    batch = ready(model, values, parts=((0, 3, 1, 4, 2, 5),))
    minibatch = batch.minibatches[0]
    old = minibatch.tensors["old_probabilities"].clone()
    actions = minibatch.tensors["actions"].clone()
    for position in range(0, len(old), 2):  # every other row: action 1, q(1) = .999
        actions[position] = 1
        old[position, :2] = torch.tensor([.001, .999])
    minibatch = replace(minibatch, tensors={
        **minibatch.tensors, "old_probabilities": old, "actions": actions,
        "advantages": torch.tensor([1., -1., -2., 2., .5, -.5])})
    theta = copy.deepcopy(model)
    optimizer = torch.optim.AdamW(theta.parameters(), lr=4e-5)
    result = prepared_update(theta, optimizer, minibatch, config=LossConfig(
        mode="ppo", value_coef=.2, explore_ratio_floor=floor),
        performance=PerformanceConfig(), target_kl=100, collect_policy_rows=True)
    return result, theta


def test_update_rows_carry_floor_facts_only_when_on_and_summarise_per_arm(model):
    off, theta_off = _update(model, 0.0)
    assert off["_policy_rows"] and "explore_floored" not in off["_policy_rows"][0]
    assert explore_floor_by_arm(["x"] * 6, off["_policy_rows"], 0.0) is None
    assert "explore_floor_by_arm" not in _with_policy_by_arm(None, ["x"] * 6,
                                                              off["_policy_rows"])
    on, theta_on = _update(model, .5)
    rows = on["_policy_rows"]
    assert {"advantage", "explore_floored"} <= set(rows[0])
    assert all(row["explore_floored"] == (row["ratio"] < .5) for row in rows)
    floored = [row for row in rows if row["explore_floored"]]
    assert floored
    arms = ["control"] * 3 + ["hot"] * 3
    summary = explore_floor_by_arm(arms, rows, .5)
    assert sum(v[0] for v in summary.values()) == len(rows)
    assert sum(v[1] for v in summary.values()) == len(floored)
    assert sum(v[2] for v in summary.values()) == sum(r["advantage"] > 0 for r in floored)
    total = sum(abs(r["advantage"]) for r in floored)
    assert sum(v[3] for v in summary.values()) == pytest.approx(total)
    assert sum(v[4] for v in summary.values()) == pytest.approx(.5 * total)
    assert sum(v[5] for v in summary.values()) == pytest.approx(
        sum(r["ratio"] * abs(r["advantage"]) for r in floored))
    assert _with_policy_by_arm(None, arms, rows, .5)["explore_floor_by_arm"] == summary
    # the same pre-step facts, but a different step
    assert on["approx_kl_mu_theta"] == off["approx_kl_mu_theta"]
    assert any(not torch.equal(a, b) for a, b in zip(
        theta_on.state_dict().values(), theta_off.state_dict().values(), strict=True))
