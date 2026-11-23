"""#632: the common action-score offset (blank run A71-A74) and its two switches.

`performance.score_head_fp32` runs the action score head in FP32 with autocast off, so a
large common offset is not rounded to BF16 before the softmax; `loss.logit_mean_penalty_coef`
adds c * mean over policy rows of (row mean raw score)^2, which moves only the offset.
Both default off and are then the code before them exactly. CPU only: BF16 is the CPU
autocast here; GPU autocast and the inductor backend are not exercised.
"""

from __future__ import annotations

import copy
import dataclasses as dc
import math
from dataclasses import asdict

import pytest
import torch
from test_rl41_w1 import model as model  # noqa: F401  (fixture)
from test_rl_explore_ratio_floor import OPTIONS, _before_knob, _data
from test_rl_rows_forward import prepared

from spireagent.json_boundary import BoundaryError
from stpd.p1.model import MStd
from stpd.p1.rows_forward import forward_rows
from stpd.rl import recipe_changes
from stpd.rl.batch_preparation import row_layout
from stpd.rl.loss import SCORE_READOUT, LossConfig, batched_segment_loss, prepared_update, rl_update
from stpd.rl.performance import PerformanceConfig, prepared_precision
from stpd.rl.segment_learner import _resume_config

OFFSET = 130.0  # the blank run's row mean at the stall (RESULT.md: v500 offset 127)


def _rows_group(model, policy=True):
    group = next(g for mb in prepared(model).minibatches for g in mb.groups if g.policy)
    group = dc.replace(group, policy=policy)
    return dc.replace(group, layout=row_layout(group, bucket_rows=256, width_multiple=64))


def _probabilities(scores):
    return scores.float().log_softmax(-1).exp().nan_to_num(0)


def _old_head(self, features):
    return self.head(features)  # the call before #632, verbatim


# -- defaults: the code before the switches ----------------------------------------------

def test_default_loss_and_gradient_are_bit_identical_to_before():
    for extra in ({}, {"logit_mean_penalty_coef": 0.0}, {"score_rows": []}):
        scores, logits, hp, t = _data()
        reference = _before_knob(scores, logits, hp, t, **OPTIONS)
        components = batched_segment_loss(scores, logits, hp, t, **OPTIONS, **extra)[2]
        assert torch.equal(components[0], reference)
        assert torch.equal(torch.autograd.grad(components[0], scores)[0],
                           torch.autograd.grad(reference, scores, retain_graph=True)[0])


@pytest.mark.parametrize("bf16", [False, True])
def test_default_forward_is_bit_identical_to_the_head_call_before(model, monkeypatch, bf16):
    """Learner and inference both score through `forward_rows`; off, `score_actions` is the
    old `self.head` call (same scores, so the same sampling probabilities)."""
    group = _rows_group(model)
    padded_group = prepared(model).minibatches[0].groups[0]
    autocast = torch.autocast("cpu", dtype=torch.bfloat16, enabled=bf16)
    with torch.no_grad(), autocast:
        now = forward_rows(model, group, packed=False)
        padded = model.forward_prepared_rl4(padded_group)
    monkeypatch.setattr(MStd, "score_actions", _old_head)
    with torch.no_grad(), autocast:
        before = forward_rows(model, group, packed=False)
        padded_before = model.forward_prepared_rl4(padded_group)
    for left, right in zip((*now, *padded), (*before, *padded_before), strict=True):
        assert torch.equal(left, right)


def test_default_prepared_update_is_bit_identical_to_before(model, monkeypatch):
    batch = prepared(model, "rows-v2")
    config = LossConfig(mode="ppo", value_coef=.2, entropy_coef=.01)
    weights, results = [], []
    for patch in (False, True):
        if patch:
            monkeypatch.setattr(MStd, "score_actions", _old_head)
        trained = copy.deepcopy(model)
        result = prepared_update(trained, torch.optim.AdamW(trained.parameters(), lr=1e-3),
                                 batch.minibatches[0], config=config, target_kl=1e9,
                                 performance=PerformanceConfig(prepared_forward="rows-v2"))
        weights.append(trained.state_dict())
        results.append(result)
    for key in ("policy_loss", "td_loss", "approx_kl_mu_theta", "gradient_norm"):
        assert results[0][key] == results[1][key], key
    for name, value in weights[0].items():
        assert torch.equal(value, weights[1][name]), name


# -- score_head_fp32 ------------------------------------------------------------------------

def _offset_model(model):
    shifted = copy.deepcopy(model)
    with torch.no_grad():
        shifted.head[-1].bias += OFFSET
    return shifted


def _gaps(model, compile=False):
    """Max |p - p_fp32| over catalog entries: BF16 head, FP32 head (CPU autocast)."""
    group = _rows_group(model)
    with torch.no_grad():
        reference = _probabilities(forward_rows(model, group, packed=False)[0])
        gaps = []
        for fp32 in (False, True):
            model.score_head_fp32 = fp32
            with torch.autocast("cpu", dtype=torch.bfloat16):
                scores = forward_rows(model, group, packed=False, compile=compile)[0]
            gaps.append(float((_probabilities(scores) - reference).abs().max()))
    model.score_head_fp32 = False
    return gaps


def test_fp32_head_removes_the_offset_rounding_of_the_head(model):
    """Head alone, same FP32 inputs, row mean ~130: BF16 rounds scores to steps of 1."""
    shifted = _offset_model(model)
    generator = torch.Generator().manual_seed(3)
    features = torch.randn((16, 12, 384), generator=generator)
    with torch.no_grad():
        reference = shifted.head(features).squeeze(-1)
        assert 120 < float(reference.mean()) < 140
        gaps = []
        for fp32 in (False, True):
            shifted.score_head_fp32 = fp32
            with torch.autocast("cpu", dtype=torch.bfloat16):
                scores = shifted.score_actions(features).squeeze(-1)
            assert scores.dtype == (torch.float32 if fp32 else torch.bfloat16)
            gaps.append(float((_probabilities(scores) - _probabilities(reference)).abs().max()))
    print(f"head only, row mean ~{OFFSET:.0f}: max |p - p_fp32| bf16 head {gaps[0]:.3e}, "
          f"fp32 head {gaps[1]:.3e}")
    assert gaps[1] < 1e-5 < 1e-2 < gaps[0]


def test_fp32_head_in_the_bf16_rows_forward(model):
    """Whole rows forward (the learner's and the services' function) under CPU BF16."""
    shifted = _offset_model(model)
    off, on = _gaps(shifted)
    plain_off, plain_on = _gaps(model)
    print(f"rows forward, row mean ~{OFFSET:.0f}: max |p - p_fp32| bf16 head {off:.3e}, "
          f"fp32 head {on:.3e}; offset 0: {plain_off:.3e} / {plain_on:.3e}")
    # With the head in FP32 the offset adds nothing: what is left is the trunk's BF16.
    assert on < off / 100
    assert on < 2 * plain_on + 1e-6


def test_fp32_head_in_the_compiled_rows_forward(model, monkeypatch):
    """torch.compile (aot_eager on CPU) traces the autocast-off block like eager."""
    compile_ = torch.compile
    monkeypatch.setattr(torch, "compile", lambda function, **keywords: compile_(
        function, **{**keywords, "backend": "aot_eager"}))
    shifted = _offset_model(model)
    eager, compiled = _gaps(shifted), _gaps(shifted, compile=True)
    print(f"compiled rows forward, row mean ~{OFFSET:.0f}: bf16 head {compiled[0]:.3e}, "
          f"fp32 head {compiled[1]:.3e} (eager {eager[0]:.3e} / {eager[1]:.3e})")
    assert compiled[1] == pytest.approx(eager[1], abs=1e-6)
    assert compiled[1] < compiled[0] / 100


def test_prepared_precision_sets_and_restores_the_choice(model):
    assert model.score_head_fp32 is False  # class default: an old pickled module too
    with prepared_precision(model, PerformanceConfig(score_head_fp32=True)):
        assert model.score_head_fp32 is True
    assert model.score_head_fp32 is False
    with pytest.raises(BoundaryError):
        PerformanceConfig(score_head_fp32=1)


@pytest.mark.parametrize("tf32", [False, True])
def test_prepared_precision_keeps_tf32_off_for_the_fp32_head(tf32):
    """Strict FP32 like the inference service: on, the learner's matmul TF32 flag stays off;
    off, it is `config.tf32` as before. No GPU here: a stand-in whose weights claim a CUDA
    device drives only the flag logic; the flag's effect on an A100 matmul is not run."""
    import types
    import warnings

    stand_in = types.SimpleNamespace(
        ids=types.SimpleNamespace(weight=types.SimpleNamespace(device=torch.device("cuda"))),
        score_head_fp32=False)
    matmul = torch.backends.cuda.matmul
    before = matmul.allow_tf32
    try:
        for head_fp32 in (False, True):
            matmul.allow_tf32 = not tf32  # a different value going in: it is set, not kept
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # CPU-only autocast("cuda") warns, disabled
                with prepared_precision(stand_in, PerformanceConfig(
                        tf32=tf32, score_head_fp32=head_fp32)):
                    assert matmul.allow_tf32 is (tf32 and not head_fp32)
                    assert stand_in.score_head_fp32 is head_fp32
            assert matmul.allow_tf32 is (not tf32)  # restored
    finally:
        matmul.allow_tf32 = before


def test_old_checkpoint_weights_load_with_both_switches_on(model):
    """No new parameter or buffer: an existing state dict loads strictly."""
    fresh = copy.deepcopy(model)
    fresh.score_head_fp32 = True
    assert set(fresh.state_dict()) == set(model.state_dict())
    fresh.load_state_dict(model.state_dict(), strict=True)
    LossConfig(mode="ppo", logit_mean_penalty_coef=.01)


def test_effective_config_records_score_head_fp32_only_when_on_and_resume_ignores_it():
    from stpd.rl.learner import LearnerConfig
    from stpd.rl.segments import SegmentConfig

    base = LearnerConfig(run="u", peer={"kind": "local", "directory": "/nonexistent"},
                         performance=PerformanceConfig(), segments=SegmentConfig())
    on = dc.replace(base, performance=PerformanceConfig(score_head_fp32=True))
    assert "score_head_fp32" not in base.frozen()["performance"]
    assert on.frozen()["performance"]["score_head_fp32"] is True
    assert _resume_config(dc.asdict(base)) == _resume_config(dc.asdict(on))


# -- logit_mean_penalty_coef -------------------------------------------------------------

def _offset_data():
    scores, logits, hp, t = _data()
    with torch.no_grad():
        scores += torch.linspace(-3, 5, len(scores))[:, None]  # row means of both signs
    t["catalog_valid"] = t["catalog_valid"].clone()
    t["catalog_valid"][2, 1] = False  # one row with a single legal entry ... and padding
    return scores, logits, hp, t


@pytest.mark.parametrize("coef", [.01, .3])
def test_penalty_gradient_is_one_value_per_row_and_pulls_the_mean_to_zero(coef):
    scores, logits, hp, t = _offset_data()
    off = batched_segment_loss(scores, logits, hp, t, **OPTIONS)
    on = batched_segment_loss(scores, logits, hp, t, logit_mean_penalty_coef=coef, **OPTIONS)
    assert torch.equal(on[1], off[1])  # every total (policy, KL, entropy, ...) unchanged
    delta = (torch.autograd.grad(on[2][0], scores)[0]
             - torch.autograd.grad(off[2][0], scores)[0])
    valid = t["catalog_valid"]
    count = valid.sum(-1)
    mean = scores.detach().masked_fill(~valid, 0).sum(-1) / count
    weight = t["policy_mask"] / OPTIONS["multi"]
    for row in range(len(scores)):
        legal = delta[row][valid[row]]
        assert torch.allclose(legal, legal[0].expand_as(legal))  # softmax unchanged
        assert torch.equal(delta[row][~valid[row]], torch.zeros_like(delta[row][~valid[row]]))
        want = coef * 2 * mean[row] / count[row] * weight[row]
        assert float(legal[0]) == pytest.approx(float(want), rel=1e-5, abs=1e-9)
        if weight[row]:  # descent (-grad) moves the row mean towards 0
            assert math.copysign(1, float(legal[0])) == math.copysign(1, float(mean[row]))
    # a step along -delta keeps every row's softmax and shrinks every policy row's mean
    stepped = scores.detach() - 0.5 * delta
    assert torch.allclose(stepped.masked_fill(~valid, -math.inf).log_softmax(-1).exp(),
                          scores.detach().masked_fill(~valid, -math.inf).log_softmax(-1).exp(),
                          atol=1e-6)
    after = stepped.masked_fill(~valid, 0).sum(-1) / count
    policy = weight > 0
    assert (after[policy].abs() < mean[policy].abs()).all()
    assert torch.equal(after[~policy], mean[~policy])


def test_penalty_config_is_validated_resume_locked_and_declarable():
    assert LossConfig().logit_mean_penalty_coef == 0.0
    for bad in (-.1, math.nan, math.inf, True, "0.1"):
        with pytest.raises(BoundaryError):
            LossConfig(logit_mean_penalty_coef=bad)

    def recipe(**loss):
        return _resume_config({"loss": asdict(LossConfig(**loss)), "segments": {}})

    old_cp = {"loss": {k: v for k, v in asdict(LossConfig()).items()
                       if k != "logit_mean_penalty_coef"}, "segments": {}}
    assert recipe() == _resume_config(old_cp)  # same recipe digest at 0
    assert recipe(logit_mean_penalty_coef=.01)["loss"]["logit_mean_penalty_coef"] == .01
    assert recipe_changes.check(
        {"checkpoint_artifact_id": "cp", "changes": {
            "loss.logit_mean_penalty_coef": {"old": 0.0, "new": .01}}},
        _resume_config(old_cp), recipe(logit_mean_penalty_coef=.01)) == {
            "loss.logit_mean_penalty_coef": {"old": 0.0, "new": .01}}
    assert recipe(logit_mean_penalty_coef=.01) != recipe()  # undeclared: a resume refuses it


def test_rl_update_refuses_the_penalty(model):
    with pytest.raises(BoundaryError, match="logit_mean_penalty_needs_prepared_update"):
        rl_update(model, model, torch.optim.AdamW(model.parameters()), [], [], 1e-5,
                  LossConfig(mode="ppo", value_coef=.2, logit_mean_penalty_coef=.1))


# -- readout -------------------------------------------------------------------------------

@pytest.mark.parametrize("diagnostics", [False, True])
def test_one_step_reports_the_score_readout(model, diagnostics):
    shifted = _offset_model(model)
    batch = prepared(shifted, "rows-v2")
    result = prepared_update(shifted, torch.optim.AdamW(shifted.parameters(), lr=1e-4),
                             batch.minibatches[0], target_kl=1e9,
                             config=LossConfig(mode="ppo", value_coef=.2),
                             performance=PerformanceConfig(prepared_forward="rows-v2"),
                             diagnostics=diagnostics)
    readout = {name: result[name] for name in SCORE_READOUT}
    print(f"diagnostics={diagnostics}: {readout}")
    assert OFFSET - 10 < readout["score_row_mean_p50"] <= readout["score_row_mean_p90"]
    assert readout["score_max_abs_p50"] <= readout["score_max_abs_p90"]
    assert readout["score_max_abs_p50"] >= readout["score_row_mean_p50"]
    assert 0 <= readout["score_range_p50"] <= readout["score_range_p90"] < 10
    if diagnostics:
        assert 0 < readout["score_read_norm_p50"] <= readout["score_read_norm_p90"]
    else:
        assert readout["score_read_norm_p50"] is None is readout["score_read_norm_p90"]


def test_inference_scorer_takes_the_choice(model):
    from stpd.p1.rows_scorer import RowsScorer

    snapshot = copy.deepcopy(model).requires_grad_(False)
    RowsScorer(snapshot, bf16=False)
    assert snapshot.score_head_fp32 is False
    RowsScorer(snapshot, bf16=False, score_head_fp32=True)
    assert snapshot.score_head_fp32 is True
    with pytest.raises(ValueError, match="invalid_score_head_fp32"):
        RowsScorer(snapshot, score_head_fp32="yes")
