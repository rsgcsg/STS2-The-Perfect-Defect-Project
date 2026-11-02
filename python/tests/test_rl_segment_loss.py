"""Real MStd gradients use one minibatch denominator across microbatches."""
from __future__ import annotations

import copy

import torch
from test_p1_t0 import observation

from stpd.p1.input import fit_vocabulary, project
from stpd.p1.model import MStd
from stpd.rl.heads import OUTCOME_CLASSES
from stpd.rl.loss import AuxiliarySample, LossConfig, SegmentSample, segment_update
from stpd.rl.performance import PerformanceConfig


def prepared():
    torch.set_num_threads(2)
    torch.manual_seed(19)
    original = observation()
    forced = copy.deepcopy(original)
    forced["catalog"] = forced["catalog"][:1]
    frames = [project(original), project(forced)]
    model = MStd(fit_vocabulary(frames)).eval()
    model.enable_rl4_heads(13)
    target = torch.nn.functional.one_hot(torch.tensor(18), OUTCOME_CLASSES).float()
    with torch.no_grad():
        probability = model.forward_batch(frames)[0].softmax(-1).tolist()
    rows = [SegmentSample(frames[0], 3, f"multi-{i}", i % 2, probability[i % 2],
                probability, (-1.0, 0.5, 1.5)[i], target) for i in range(3)]
    rows.append(SegmentSample(frames[1], 3, "forced", 0, 1.0, [1.0], 1000.0, target))
    terminal = [AuxiliarySample(frames[0], 3, "terminal", outcome=18)]
    hp = [AuxiliarySample(frames[1], 3, "hp", hp=0.42)]
    return model, rows, terminal, hp


def test_microbatch_gradient_matches_whole_minibatch_including_forced_and_auxiliary():
    first, rows, terminal, hp = prepared()
    second = copy.deepcopy(first)
    config = LossConfig(mode="ppo", value_coef=.2, entropy_coef=.01)
    a = segment_update(first, torch.optim.AdamW(first.parameters(), lr=0), rows,
        terminal, hp, config=config, performance=PerformanceConfig(microbatch_size=1))
    b = segment_update(second, torch.optim.AdamW(second.parameters(), lr=0), rows,
        terminal, hp, config=config, performance=PerformanceConfig(microbatch_size=16))
    assert a["stepped"] and b["stepped"] and a["multi_rows"] == b["multi_rows"] == 3
    for (name, one), (other, two) in zip(
            first.named_parameters(), second.named_parameters(), strict=True):
        assert name == other
        assert (one.grad is None) == (two.grad is None), name
        if one.grad is not None:
            assert torch.allclose(one.grad, two.grad, atol=2e-6, rtol=2e-4), name
    for field in ("policy_loss", "td_loss", "terminal_loss", "hp_loss", "entropy"):
        assert abs(a[field]-b[field]) < 2e-6, field


def test_kl_stops_policy_and_auxiliary_before_any_optimizer_state_changes():
    model, rows, terminal, hp = prepared()
    row = rows[0]
    rows[0] = SegmentSample(row.frame, row.floor, row.key, row.action,
        .01, [.01, .99], row.advantage, row.outcome_target)
    before = copy.deepcopy(model.state_dict())
    optimizer = torch.optim.AdamW(model.parameters(), lr=4e-5, weight_decay=0)
    result = segment_update(model, optimizer, rows, terminal, hp,
        config=LossConfig(mode="ppo", value_coef=.2, entropy_coef=.01),
        performance=PerformanceConfig(microbatch_size=2), target_kl=.03)
    assert result["kl_stopped"] and not result["stepped"]
    assert result["approx_kl_mu_theta"] > .03
    assert not optimizer.state
    assert all(torch.equal(value, before[name]) for name, value in model.state_dict().items())
    assert all(parameter.grad is None for parameter in model.parameters())


def test_successful_update_reports_weighted_shared_trunk_gradients():
    model, rows, terminal, hp = prepared()
    result = segment_update(model,
        torch.optim.AdamW(model.parameters(), lr=4e-5, weight_decay=0), rows, terminal, hp,
        config=LossConfig(mode="ppo", value_coef=.2, entropy_coef=.01),
        performance=PerformanceConfig(microbatch_size=2), diagnostics=True)
    assert result["stepped"]
    assert result["trunk_gradients"]["policy_norm"] > 0
    assert set(result["trunk_gradients"]["weighted_head_norms"]) == {
        "policy", "td", "terminal", "hp"}
