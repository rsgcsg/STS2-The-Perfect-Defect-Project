"""Batched PPO/TD/terminal/HP semantics, rather than speedup equivalence gates."""

from __future__ import annotations

import copy
import math

import pytest
import torch
from test_rl41_w1 import model as model
from test_rl41_w1 import ready, segment

from stpd.rl.loss import LossConfig, batched_segment_loss, prepared_update, segment_update
from stpd.rl.performance import PerformanceConfig
from stpd.rl.prepared_batch import AuxiliarySelection


@pytest.mark.parametrize("coefficients", [(1., .2, .1, .1), (.4, .5, .2, .7)])
def test_four_head_means_and_coefficients_exclude_forced_policy_and_padding(coefficients):
    policy_coef, value_coef, terminal_coef, hp_coef = coefficients
    scores = torch.tensor([[math.log(0.6), math.log(0.4)], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]])
    logits = torch.zeros((4, 51), requires_grad=True)
    hp = torch.zeros(4, requires_grad=True)
    targets = torch.zeros((4, 51))
    targets[:3, 18] = 1
    t = {
        "actions": torch.tensor([0, 0, 0, 0]),
        "old_probabilities": torch.tensor([[0.6, 0.4], [0.5, 0.5], [0.5, 0.5], [0.5, 0.5]]),
        "advantages": torch.tensor([1.0, 999.0, 0.0, 0.0]),
        "targets": targets,
        "hp_targets": torch.tensor([0.0, 0.0, 0.0, 0.42]),
        "policy_mask": torch.tensor([1.0, 0.0, 0.0, 0.0]),
        "td_mask": torch.tensor([1.0, 1.0, 0.0, 0.0]),
        "terminal_mask": torch.tensor([0.0, 0.0, 1.0, 0.0]),
        "hp_mask": torch.tensor([0.0, 0.0, 0.0, 1.0]),
        "catalog_valid": torch.ones((4, 2), dtype=torch.bool),
        "outcome_valid": torch.ones((4, 51), dtype=torch.bool),
    }
    loss, totals, _, valid = batched_segment_loss(
        scores,
        logits,
        hp,
        t,
        clip=0.2,
        entropy_coef=0.01,
        policy_coef=policy_coef,
        value_coef=value_coef,
        terminal_coef=terminal_coef,
        hp_coef=hp_coef,
        multi=1,
        td_rows=2,
        terminal_rows=1,
        hp_rows=1,
    )
    entropy = -0.6 * math.log(0.6) - 0.4 * math.log(0.4)
    assert valid
    assert float(totals[0]) == pytest.approx(math.log(51))
    assert float(totals[1]) == pytest.approx(-1.0)
    assert float(totals[2]) == pytest.approx(entropy)
    assert float(totals[7]) == pytest.approx(0.037)
    assert float(loss) == pytest.approx(-policy_coef - 0.01 * entropy
        + (value_coef + terminal_coef) * math.log(51) + hp_coef * 0.037)


def test_impossible_classes_and_padded_candidates_have_no_probability_mass(model):
    batch = ready(model, [segment(model)])
    for group in batch.minibatches[0].groups:
        scores, logits, _ = model.forward_prepared_rl4(group)
        assert logits[:, :3].isneginf().all()
        assert logits.softmax(-1)[:, :3].eq(0).all()
        if group.policy:
            assert scores.shape[1] == 8
            assert scores[:, 2:].isneginf().all()
            assert scores.softmax(-1)[:, 2:].eq(0).all()


def test_successful_step_reports_shared_trunk_gradients_for_all_four_heads(model):
    with torch.no_grad():
        model.rl4_heads.outcome.mlp[-1].weight.normal_(0, 0.01)
        model.rl4_heads.hp.mlp[-1].weight.normal_(0, 0.01)
    value = segment(model, forced=False)
    value["bootstrap"]["outcome_probabilities"] = [0.0] * 49 + [1.0, 0.0]
    ref = {
        "run_id": value["run_id"],
        "segment_id": value["segment_id"],
        "training_generation": value["training_generation"],
        "row": 0,
        "key": "episode:0",
    }
    aux = (
        AuxiliarySelection(ref, "terminal", 49.0, 0, 2),
        AuxiliarySelection(ref, "hp", 0.42, 0, 1),
    )
    batch = ready(model, [value], auxiliary=aux, owners=(0, 0))
    result = prepared_update(
        model,
        torch.optim.AdamW(model.parameters(), lr=4e-5, weight_decay=0),
        batch.minibatches[0],
        config=LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01),
        performance=PerformanceConfig(),
        diagnostics=True,
    )
    assert result["stepped"] and result["terminal_rows"] == result["hp_rows"] == 1
    norms = result["trunk_gradients"]["weighted_head_norms"]
    assert set(norms) == {"policy", "td", "terminal", "hp"}
    assert all(norms[name] > 0 for name in norms)


def test_dynamic_nan_prevents_Adam_and_does_not_leave_gradients(model, monkeypatch):
    batch = ready(model, [segment(model)])
    original = model.forward_prepared_rl4

    def bad(group):
        score, logits, hp = original(group)
        return score, logits + torch.nan, hp

    monkeypatch.setattr(model, "forward_prepared_rl4", bad)
    optimizer = torch.optim.AdamW(model.parameters(), lr=4e-5)
    before = copy.deepcopy(model.state_dict())
    with pytest.raises(ValueError, match="non_finite_prepared"):
        prepared_update(
            model,
            optimizer,
            batch.minibatches[0],
            config=LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01),
            performance=PerformanceConfig(),
        )
    assert not optimizer.state
    assert all(torch.equal(tensor, before[name]) for name, tensor in model.state_dict().items())
    assert all(parameter.grad is None for parameter in model.parameters())


def test_old_row_loss_is_retired():
    with pytest.raises(ValueError, match="retired_use_prepared_update"):
        segment_update()


def test_row_bias_is_full_forward_KL_and_distinguishes_objective_clipping():
    scores = torch.tensor([[math.log(.8), math.log(.2), -torch.inf]] * 4,
                          requires_grad=True)
    logits, hp = torch.zeros((4, 51), requires_grad=True), torch.zeros(4, requires_grad=True)
    targets = torch.zeros((4, 51))
    targets[:, 18] = 1
    data = {"actions": torch.tensor([0, 0, 1, 0]),
        "old_probabilities": torch.tensor([[.5, .5, 0.]] * 4),
        "advantages": torch.tensor([1., -1., -1., 999.]), "targets": targets,
        "hp_targets": torch.zeros(4), "policy_mask": torch.tensor([1., 1., 1., 0.]),
        "td_mask": torch.ones(4), "terminal_mask": torch.zeros(4),
        "hp_mask": torch.zeros(4), "catalog_valid": torch.tensor([[True, True, False]] * 4),
        "outcome_valid": torch.ones((4, 51), dtype=torch.bool)}
    options = dict(clip=.2, entropy_coef=.01, value_coef=.2, terminal_coef=.1,
                   hp_coef=.1, multi=3, td_rows=4, terminal_rows=0, hp_rows=0)
    baseline = batched_segment_loss(scores, logits, hp, data, **options)[0]
    expected_grad = torch.autograd.grad(baseline, scores)[0]
    observed = []
    loss = batched_segment_loss(scores, logits, hp, data,
                               row_diagnostics=observed, **options)[0]
    assert torch.equal(loss, baseline)
    assert torch.equal(torch.autograd.grad(loss, scores)[0], expected_grad)
    values = observed[0]
    assert not values.requires_grad and values.shape == (4, 6)
    expected_KL = .5 * math.log(.5 / .8) + .5 * math.log(.5 / .2)
    assert values[:, 0].tolist() == pytest.approx([expected_KL] * 4)
    assert values[:, 1].tolist() == pytest.approx([1.6, 1.6, .4, 1.6])
    assert values[:, 2].bool().all()  # All outside the ratio band.
    assert values[:, 3].tolist()[:3] == [1., 0., 1.]  # Advantage direction matters.
    assert values[:, 4].tolist() == [1., 1., 0., 1.]
    assert values[:, 5].tolist() == [1., 1., 1., 0.]  # Forced/aux are not policy rows.


def test_row_diagnostics_keep_same_Adam_and_map_physical_groups_to_logical_rows(model):
    values = [segment(model, rows=3, name="multi", forced=False),
              segment(model, rows=2, name="forced", run_id="forced", forced=True)]
    batch = ready(model, values, parts=((2, 0, 4, 1, 3),))
    plain, observed = copy.deepcopy(model), copy.deepcopy(model)
    first = torch.optim.AdamW(plain.parameters(), lr=4e-5)
    second = torch.optim.AdamW(observed.parameters(), lr=4e-5)
    options = dict(config=LossConfig(mode="ppo", value_coef=.2),
                   performance=PerformanceConfig(), target_kl=100)
    baseline = prepared_update(plain, first, batch.minibatches[0], **options)
    result = prepared_update(observed, second, batch.minibatches[0],
                             collect_policy_rows=True, **options)
    assert baseline["stepped"] and result["stepped"]
    assert [row["row"] for row in result["_policy_rows"]] == [2, 0, 1, 3]
    for name, tensor in plain.state_dict().items():
        assert torch.equal(tensor, observed.state_dict()[name])
    for left, right in zip(first.state.values(), second.state.values(), strict=True):
        assert all(torch.equal(left[key], right[key]) for key in left)


def test_encoder_groups_accumulate_once_and_release_before_next_forward(model, monkeypatch):
    """Group size bounds saved activations without an extra recomputation pass."""
    batch = ready(model, [segment(model, forced=False)])
    minibatch = batch.minibatches[0]
    assert len(minibatch.groups) > 1
    static = {p.untyped_storage().data_ptr() for p in model.parameters()}
    for group in minibatch.groups:
        static.update(t.untyped_storage().data_ptr() for t in group.tensors.values())
    live = 0

    class Saved:
        def __init__(self, tensor):
            nonlocal live
            self.tensor = tensor.detach()
            self.counted = tensor.untyped_storage().data_ptr() not in static
            live += int(self.counted)

        def __del__(self):
            nonlocal live
            live -= int(self.counted)

    original = model.forward_prepared_rl4
    observed = []

    def forward(group):
        observed.append(live)
        return original(group)

    monkeypatch.setattr(model, "forward_prepared_rl4", forward)
    with torch.autograd.graph.saved_tensors_hooks(Saved, lambda value: value.tensor):
        result = prepared_update(model, torch.optim.AdamW(model.parameters(), lr=4e-5),
            minibatch, config=LossConfig(mode="ppo", value_coef=.2, entropy_coef=.01),
            performance=PerformanceConfig())
    assert result["stepped"]
    assert observed == [0] * len(minibatch.groups)
