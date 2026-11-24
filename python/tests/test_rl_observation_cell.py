"""Observation cells (`segments.observe_arms`, user 2026-11-24): an arm's rows keep their value
and HP targets but give no policy, entropy or barrier gradient and no KL-stop or clip weight.

Empty (default) is the update before it; observed rows' advantages and recorded q (a greedy
one-hot included) cannot change the step, their value targets still do.
"""

from __future__ import annotations

import copy
import math
from dataclasses import asdict, replace

import pytest
import test_rl41_w1 as w1
import torch
from test_rl41_w1 import model as model  # noqa: F401  (fixture)
from test_rl_explore_ratio_floor import OPTIONS, _data
from test_rl_rows_forward import prepared

from stpd.rl import batch_preparation as bp
from stpd.rl import recipe_changes
from stpd.rl.loss import (
    LossConfig,
    batched_segment_loss,
    learning_policy_mask,
    minibatch_learn_rows,
    prepared_update,
)
from stpd.rl.performance import PerformanceConfig
from stpd.rl.segment_learner import _resume_config
from stpd.rl.segments import SegmentConfig

PERFORMANCE = PerformanceConfig(prepared_forward="rows-v2")
CONFIG = LossConfig(mode="ppo", value_coef=.2, entropy_coef=.01)


def test_learning_policy_mask_drops_the_observed_arms_and_their_save_starts():
    arms = ["control", "greedy", "greedy+save", "control+save", None]
    policy = [True, True, True, True, False, False]  # row 5: auxiliary
    assert learning_policy_mask(arms, policy, {"greedy"}) == \
        [True, False, False, True, False, False]
    with pytest.raises(ValueError, match="observe_arms_need_row_arms"):
        learning_policy_mask(arms, [False, False, False, False, True, False], {"greedy"})
    with pytest.raises(ValueError, match="observe_arms_need_row_arms"):
        learning_policy_mask(None, policy, {"greedy"})


def test_observed_rows_give_no_policy_gradient_and_a_one_hot_q_stays_finite():
    scores, logits, hp, t = _data()
    observed = torch.tensor([0., 1., 0., 0., 1., 0., 0., 1.])
    t["policy_mask"] = t["policy_mask"] * (1 - observed)
    one_hot = t["old_probabilities"].clone()
    one_hot[observed > 0] = torch.tensor([1.0, 0.0])  # greedy rows: q one-hot at action 0
    t["old_probabilities"] = one_hot
    options = {**OPTIONS, "multi": int(t["policy_mask"].sum())}
    _, totals, components, _ = batched_segment_loss(scores, logits, hp, t, observed_rows=True,
                                                    **options)
    gradient = torch.autograd.grad(components[0], scores)[0]
    assert torch.equal(gradient[observed > 0], torch.zeros_like(gradient[observed > 0]))
    assert gradient[t["policy_mask"] > 0].abs().sum() > 0
    assert torch.isfinite(totals).all()  # the acting KL too
    # without the flag the one-hot rows' 0 * inf would make the acting-KL total NaN
    assert math.isnan(float(batched_segment_loss(scores, logits, hp, t, **options)[1][5].detach()))


def _observed(minibatch):
    """Every second policy row of the minibatch (tensor order) is an observation row."""
    policy = minibatch.tensors["policy_mask"].tolist()
    positions = [i for i, value in enumerate(policy) if value > 0]
    observed = set(positions[1::2])
    return observed, [policy[i] > 0 and i not in observed for i in range(len(policy))]


def _step(model, minibatch, **kwargs):
    trained = copy.deepcopy(model)
    result = prepared_update(trained, torch.optim.AdamW(trained.parameters(), lr=1e-3),
                             minibatch, config=CONFIG, performance=PERFORMANCE, target_kl=1e9,
                             **kwargs)
    return trained.state_dict(), result


def _with(minibatch, observed, **tensors):
    index = torch.tensor(sorted(observed))
    out = dict(minibatch.tensors)
    for name, change in tensors.items():
        value = out[name].clone()
        value[index] = change(value[index])
        out[name] = value
    return replace(minibatch, tensors=out)


def test_observed_rows_cannot_change_the_policy_update_but_still_train_the_values(model):
    minibatch = prepared(model, "rows-v2").minibatches[0]
    observed, learn = _observed(minibatch)
    assert observed and any(learn)
    weights, result = _step(model, minibatch, learn_rows=learn)
    assert result["policy_observed_rows"] == len(observed)
    assert result["policy_learning_rows"] == sum(learn) == minibatch.policy_rows - len(observed)

    def one_hot(old):
        hot = torch.zeros_like(old)
        hot[:, 0] = 1
        return hot

    # observed rows with other advantages and a greedy one-hot q: the same step, bit for bit
    changed = _with(minibatch, observed, advantages=lambda a: -50 * a + 3,
                    old_probabilities=one_hot,
                    actions=lambda a: torch.zeros_like(a))
    weights_changed, result_changed = _step(model, changed, learn_rows=learn)
    for name, value in weights.items():
        assert torch.equal(value, weights_changed[name]), name
    for key in ("policy_loss", "entropy", "approx_kl_mu_theta", "clip_fraction",
                "acting_kl_theta_mu", "td_loss"):
        assert result_changed[key] == result[key], key
    # their value targets still train the outcome head
    def last_valid(targets):
        valid = minibatch.tensors["outcome_valid"][sorted(observed)]
        hot = torch.zeros_like(targets)
        hot[torch.arange(len(hot)), valid.float().cumsum(1).argmax(1)] = 1
        return hot

    retargeted = _with(minibatch, observed, targets=last_valid)
    weights_retargeted, _ = _step(model, retargeted, learn_rows=learn)
    assert any(not torch.equal(value, weights_retargeted[name])
               for name, value in weights.items() if name.startswith("rl4_heads.outcome"))
    # and without the observation the same changes do move the policy
    weights_learning, _ = _step(model, changed)
    assert any(not torch.equal(value, weights_learning[name])
               for name, value in weights.items() if not name.startswith("rl4_heads."))


def test_all_rows_learning_is_the_update_before_it(model):
    minibatch = prepared(model, "rows-v2").minibatches[0]
    policy = minibatch.tensors["policy_mask"].tolist()
    weights, result = _step(model, minibatch)
    dataset = [False] * (1 + max(r for g in minibatch.groups for r in g.rows))
    for position, row in enumerate(r for g in minibatch.groups for r in g.rows):
        dataset[row] = policy[position] > 0
    learn = minibatch_learn_rows(minibatch, dataset)  # dataset order -> tensor order
    assert learn == [p > 0 for p in policy]
    weights_all, result_all = _step(model, minibatch, learn_rows=learn)
    for name, value in weights.items():
        assert torch.equal(value, weights_all[name]), name
    for key in ("policy_loss", "entropy", "approx_kl_mu_theta", "clip_fraction", "td_loss"):
        assert result_all[key] == result[key], key
    assert "policy_observed_rows" not in result and result_all["policy_observed_rows"] == 0


def test_learn_rows_must_cover_the_minibatch(model):
    minibatch = prepared(model, "rows-v2").minibatches[0]
    with pytest.raises(ValueError, match="learn_rows_must_cover"):
        _step(model, minibatch, learn_rows=[True])


def test_observe_arms_config_is_validated_resume_locked_and_declarable():
    assert SegmentConfig().observe_arms == []
    assert SegmentConfig(observe_arms=["greedy"]).observe_arms == ["greedy"]
    for bad in (["b", "a"], ["a", "a"], ["a+save"], ["a b"], "greedy", [1]):
        with pytest.raises(ValueError):
            SegmentConfig(observe_arms=bad)
    with pytest.raises(ValueError):
        SegmentConfig(kl_stop_scope="control", observe_arms=["control"])

    def recipe(**segments):
        return _resume_config({"loss": asdict(LossConfig()),
                               "segments": asdict(SegmentConfig(**segments))})

    old = {k: v for k, v in asdict(SegmentConfig()).items() if k != "observe_arms"}
    assert recipe() == _resume_config({"loss": asdict(LossConfig()), "segments": old})
    assert recipe(observe_arms=["greedy"]) != recipe()
    assert recipe_changes.check(
        {"checkpoint_artifact_id": "cp", "changes": {
            "segments.observe_arms": {"old": [], "new": ["greedy"]}}},
        recipe(), recipe(observe_arms=["greedy"])) == {
            "segments.observe_arms": {"old": [], "new": ["greedy"]}}


def _arm_sources(model, arms):
    """One w1 segment per arm; its first row names the arm (fast_source's count table)."""
    values = [w1.segment(model, name=f"actor/{i}", run_id=f"episode-{i}", rows=5)
              for i in range(len(arms))]
    sources = []
    for i, (value, arm) in enumerate(zip(values, arms, strict=True)):
        source = w1.source(model, value)
        metadata = list(source.metadata)
        metadata[0] = {**metadata[0], "exploration": {f"{arm}|act1|combat": [1, 0, 0, 0, 0]}}
        # distinct raw advantages per source, so the observed rows move the statistics
        raw = source.raw_advantages * (1 + 3 * i) + 2 * i
        sources.append(replace(source, metadata=tuple(metadata), raw_advantages=raw))
    return values, sources


def test_observed_rows_leave_the_advantage_normalisation(model):
    values, sources = _arm_sources(model, ["control", "greedy"])
    request = w1.request(values)
    plain = bp.assemble(request, tuple(sources))
    observed = bp.assemble(request, tuple(sources), observe_arms=("greedy",))
    n = sum(v["rows"] for v in values)
    policy = plain.policy_mask[:n]
    learn = policy.clone()
    learn[values[0]["rows"]:] = False
    assert learn.any() and (policy & ~learn).any()
    raw = torch.cat([s.raw_advantages for s in sources])
    multi = raw[learn]
    mean, scale = float(multi.mean()), max(float(multi.std(unbiased=False)), 1e-8)
    assert observed.normalization.multi_rows == int(learn.sum())
    assert observed.normalization.mean == mean and observed.normalization.scale == scale
    assert torch.equal(observed.advantages[:n][learn], (multi - mean) / scale)
    assert not observed.advantages[:n][~learn].any()  # observation rows: 0, no policy weight
    # the plain normalisation (every policy row) differs: the bug this guards against
    assert plain.normalization.multi_rows == int(policy.sum())
    assert not torch.equal(plain.advantages[:n][learn], observed.advantages[:n][learn])
    # whatever the observed rows' raw advantages, the learning rows' values are the same
    moved = list(sources)
    moved[1] = replace(moved[1], raw_advantages=moved[1].raw_advantages * -40 + 7)
    again = bp.assemble(request, tuple(moved), observe_arms=("greedy",))
    assert torch.equal(again.advantages, observed.advantages)
    for a, b in zip(again.minibatches, observed.minibatches, strict=True):
        assert torch.equal(a.tensors["advantages"], b.tensors["advantages"])


def test_no_observed_arm_is_the_normalisation_before_it(model):
    values, sources = _arm_sources(model, ["control", "bold"])
    request = w1.request(values)
    plain = bp.assemble(request, tuple(sources))
    unobserved = bp.assemble(request, tuple(sources), observe_arms=("greedy",))
    assert plain.normalization == unobserved.normalization
    assert torch.equal(plain.advantages, unobserved.advantages)
    with pytest.raises(ValueError, match="observe_arms_need_row_arms"):
        bp.assemble(request, tuple(replace(s, metadata=tuple(
            {k: v for k, v in m.items() if k != "exploration"} for m in s.metadata))
            for s in sources), observe_arms=("greedy",))
