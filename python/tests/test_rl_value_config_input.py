"""Value heads read the sampling config (`segments.value_config_input`, owner 2026-11-19).

The outcome and HP heads get four numbers per row (ln T, mix / 0.1 of the combat and
noncombat classes; control = zeros) through zero-initialised weights: an older model
gives the same head outputs, a trained input changes only exploration rows, the policy
logits never read it, and with the knob off nothing differs from before.
"""
from __future__ import annotations

import copy
import dataclasses as dc
import math

import pytest

pytest.importorskip("torch")

import torch  # noqa: E402
from test_rl41_w1 import model as model  # noqa: E402,F401  (fixture)
from test_rl_rows_forward import prepared  # noqa: E402

from stpd.p1.rows_forward import forward_rows  # noqa: E402
from stpd.p1.rows_scorer import RowsScorer  # noqa: E402
from stpd.rl import recipe_changes  # noqa: E402
from stpd.rl.batch_preparation import row_layout  # noqa: E402
from stpd.rl.heads import (  # noqa: E402
    CONTROL_VALUE_CONFIG,
    VALUE_CONFIG_HEAD,
    VALUE_CONFIG_KEYS,
    load_heads_state,
    row_value_configs,
    value_config_vector,
)
from stpd.rl.loss import LossConfig, prepared_update, with_value_config  # noqa: E402
from stpd.rl.performance import PerformanceConfig  # noqa: E402
from stpd.rl.segment_learner import _resume_config  # noqa: E402
from stpd.rl.segments import SegmentConfig  # noqa: E402

HIGH = {"combat": {"temperature": 1.4, "uniform_mix": 0.005},
        "noncombat": {"temperature": 2.5, "uniform_mix": 0.01}}
CONTROL = {"combat": {"temperature": 1, "uniform_mix": 0},
           "noncombat": {"temperature": 1, "uniform_mix": 0}}


def trained(model):
    """Non-trivial heads (the final layers start at zero in a fresh model)."""
    generator = torch.Generator().manual_seed(11)
    with torch.no_grad():
        for module in (model.rl4_heads.outcome, model.rl4_heads.hp):
            weight = module.mlp[-1].weight
            weight.copy_(torch.randn(weight.shape, generator=generator) * 0.05)
    return model


def rows_groups(model):
    batch = prepared(model)
    return [dc.replace(group, layout=row_layout(group, bucket_rows=3, width_multiple=8))
            for minibatch in batch.minibatches for group in minibatch.groups]


def configs_for(rows, pattern):
    return torch.tensor([pattern[index % len(pattern)] for index in range(rows)])


def test_encoding_is_log_temperature_and_mix_over_its_ceiling_control_zero():
    assert value_config_vector(CONTROL) == CONTROL_VALUE_CONFIG == (0.0, 0.0, 0.0, 0.0)
    assert value_config_vector(HIGH) == pytest.approx(
        (math.log(1.4), 0.05, math.log(2.5), 0.1))
    with pytest.raises(ValueError, match="invalid_value_config_sampling"):
        value_config_vector({**HIGH, "combat": {"temperature": 1, "uniform_mix": 0.2}})


@pytest.mark.parametrize("packed,kernels", [(False, ""), (True, ""), (False, "heads"),
                                            (True, "heads")])
def test_an_older_model_with_the_input_added_gives_identical_outputs(model, packed, kernels):
    """(a) An old checkpoint upgraded (zero weights): same logits/HP/policy, any config."""
    old = trained(model)
    new = copy.deepcopy(old)
    new.enable_value_config()
    assert new.architecture["auxiliary_heads"][-1] == VALUE_CONFIG_HEAD
    assert [name for name, _ in new.named_parameters()][-2:] == [
        "rl4_heads." + key for key in VALUE_CONFIG_KEYS]
    with torch.no_grad():
        for group in rows_groups(old):
            reference = forward_rows(old, group, packed=packed, kernels=kernels)
            for pattern in ([CONTROL_VALUE_CONFIG], [value_config_vector(HIGH)]):
                got = forward_rows(new, group, packed=packed, kernels=kernels,
                                   value_config=configs_for(len(group.rows), pattern))
                for left, right in zip(reference, got, strict=True):
                    assert torch.equal(left.isneginf(), right.isneginf())
                    finite = left.isfinite()
                    torch.testing.assert_close(left[finite], right[finite],
                                               rtol=0, atol=1e-6)


def test_an_older_head_snapshot_loads_into_heads_with_the_input(model):
    """A fitted head snapshot without the input (warm-up start, published mu)."""
    old = trained(model)
    new = copy.deepcopy(old)
    del new.rl4_heads
    new.enable_rl4_heads(7, value_config=True)
    load_heads_state(new.rl4_heads, old.rl4_heads.state_dict())
    assert all(not bool(new.rl4_heads.get_parameter(key).any()) for key in VALUE_CONFIG_KEYS)
    with torch.no_grad():
        for group in rows_groups(old)[:2]:
            reference = forward_rows(old, group, packed=False)
            got = forward_rows(new, group, packed=False, value_config=configs_for(
                len(group.rows), [value_config_vector(HIGH)]))
            for left, right in zip(reference, got, strict=True):
                finite = left.isfinite()
                torch.testing.assert_close(left[finite], right[finite], rtol=0, atol=1e-6)
    # Anything else differing still fails loudly.
    state = {**old.rl4_heads.state_dict(), "unexpected": torch.zeros(1)}
    with pytest.raises(RuntimeError, match="unexpected"):
        load_heads_state(new.rl4_heads, state)


def test_frame_api_reads_control_and_padded_path_agrees(model):
    old = trained(model)
    new = copy.deepcopy(old)
    new.enable_value_config()
    group = prepared(old).minibatches[0].groups[0]  # padded-v1
    with torch.no_grad():
        reference = old.forward_prepared_rl4(group)
        got = new.forward_prepared_rl4(group, value_config=torch.zeros((len(group.rows), 4)))
    for left, right in zip(reference, got, strict=True):
        finite = left.isfinite()
        torch.testing.assert_close(left[finite], right[finite], rtol=0, atol=1e-6)
    with pytest.raises(ValueError, match="value_config_required"):
        new.forward_prepared_rl4(group)


def test_a_trained_input_changes_only_rows_with_a_non_control_config(model):
    """(b) Non-zero weights: exploration rows move, control rows and policy do not."""
    old = trained(model)
    new = copy.deepcopy(old)
    new.enable_value_config()
    generator = torch.Generator().manual_seed(3)
    with torch.no_grad():
        for key in VALUE_CONFIG_KEYS:
            weight = new.rl4_heads.get_parameter(key)
            weight.copy_(torch.randn(weight.shape, generator=generator))
        group = rows_groups(old)[0]
        rows = len(group.rows)
        pattern = [CONTROL_VALUE_CONFIG, value_config_vector(HIGH)]
        config = configs_for(rows, pattern)
        scores0, logits0, hp0 = forward_rows(old, group, packed=False)
        scores1, logits1, hp1 = forward_rows(new, group, packed=False, value_config=config)
    torch.testing.assert_close(scores0, scores1, rtol=0, atol=0)  # policy never reads it
    control = config.abs().sum(1) == 0
    assert bool(control.any()) and bool((~control).any())
    finite = logits0.isfinite()
    torch.testing.assert_close(logits0[control][finite[control]],
                               logits1[control][finite[control]], rtol=0, atol=1e-6)
    torch.testing.assert_close(hp0[control], hp1[control], rtol=0, atol=1e-6)
    moved = (logits0 - logits1).masked_fill(~finite, 0).abs().amax(1)
    assert bool((moved[~control] > 1e-4).all())
    assert bool(((hp0 - hp1).abs()[~control] > 1e-4).all())


def test_training_step_reads_the_rows_configs_and_learns_the_input(model):
    """The learner path: `with_value_config` per row, the config weights get gradient."""
    new = trained(model)
    new.enable_value_config()
    batch = prepared(new, "rows-v2")
    minibatch = batch.minibatches[0]
    rows = 1 + max(row for group in minibatch.groups for row in group.rows)
    configs = [value_config_vector(HIGH)] * rows
    config = LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01)
    performance = PerformanceConfig(prepared_forward="rows-v2")
    with pytest.raises(ValueError, match="value_config_required"):
        prepared_update(copy.deepcopy(new), torch.optim.AdamW(new.parameters()), minibatch,
                        config=config, performance=performance, target_kl=1e9)
    optimizer = torch.optim.AdamW(new.parameters(), lr=1e-3)
    staged = with_value_config(minibatch, configs, torch.device("cpu"))
    assert staged.tensors["value_config"].shape == (len(minibatch.tensors["targets"]), 4)
    result = prepared_update(new, optimizer, staged, config=config,
                             performance=performance, target_kl=1e9)
    assert result["stepped"]
    outcome, hp = (new.rl4_heads.get_parameter(key) for key in VALUE_CONFIG_KEYS)
    assert bool(outcome.ne(0).any())
    # The HP weights move only when the minibatch carries HP targets.
    assert bool(hp.ne(0).any()) == (float(result["hp_loss"]) > 0)


def test_row_configs_come_from_each_sources_first_row_control_without_one():
    """(c) Dataset rows take their source's recorded config; old sources are control."""
    high = list(value_config_vector(HIGH))
    metadata = [{"value_config": high}, {}, {}, {}, {"value_config": [0.0] * 4}]
    assert row_value_configs(metadata, [2, 2, 1]) == [tuple(high)] * 2 + [
        CONTROL_VALUE_CONFIG] * 2 + [CONTROL_VALUE_CONFIG]
    with pytest.raises(ValueError, match="sources cover"):
        row_value_configs(metadata, [2, 2])


def test_rows_scorer_ignores_configs_for_heads_without_the_input(model):
    """(d) A mu without the input scores exactly as before, configs or not."""
    old = trained(model).requires_grad_(False)
    scorer = RowsScorer(old, bf16=False)
    assert scorer.reads_value_config is False and scorer.value_config([(1, 1, 1, 1)]) is None
    group = rows_groups(old)[0]
    plain = scorer.forward_tensors(group)
    given = scorer.forward_tensors(group, [value_config_vector(HIGH)] * len(group.rows))
    for left, right in zip(plain, given, strict=True):
        assert torch.equal(left, right)
    with pytest.raises(ValueError, match="value_config_given_to_heads_without_it"):
        forward_rows(old, group, value_config=torch.zeros((len(group.rows), 4)))


def test_knob_off_keeps_the_architecture_parameters_and_recipe(model):
    """(d) Default off: no new parameter, architecture or recipe key."""
    assert SegmentConfig().value_config_input is False
    assert not model.rl4_heads.has_value_config
    assert VALUE_CONFIG_HEAD not in model.architecture["auxiliary_heads"]
    assert not any(key.split(".")[-1] in VALUE_CONFIG_KEYS for key in model.state_dict())
    recipe = {"segments": dc.asdict(SegmentConfig()), "loss": {}}
    assert "value_config_input" not in _resume_config(recipe)["segments"]
    on = {"segments": dc.asdict(SegmentConfig(value_config_input=True)), "loss": {}}
    assert _resume_config(on)["segments"]["value_config_input"] is True
    with pytest.raises(ValueError):
        SegmentConfig(value_config_input=1)  # type: ignore[arg-type]


def test_a_continuation_may_declare_only_off_to_on():
    old = {"segments": {}}
    on = {"segments": {"value_config_input": True}}
    declared = {"checkpoint_artifact_id": "cp", "changes": {
        "segments.value_config_input": {"old": False, "new": True}}}
    assert recipe_changes.check(declared, old, on) == declared["changes"]
    back = {"checkpoint_artifact_id": "cp", "changes": {
        "segments.value_config_input": {"old": True, "new": False}}}
    with pytest.raises(ValueError, match="only_off_to_on"):
        recipe_changes.check(back, on, old)
