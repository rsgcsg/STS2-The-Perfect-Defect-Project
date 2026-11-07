"""S3 rows-v2: the padding-free RL training forward against the padded original.

CPU fp32: outputs and every parameter gradient equal padded-v1 within float
accumulation tolerance, for multi-bucket layouts, varied scene/text lengths,
policy and non-policy groups. GPU BF16 step time and tolerance are Friday facts.
"""
from __future__ import annotations

import copy
import dataclasses as dc
import itertools
import os

import pytest
import test_rl41_w1 as w1
import torch
from test_rl41_w1 import model as model  # noqa: F401  (fixture)

from spireagent.json_boundary import BoundaryError
from stpd.rl.batch_preparation import assemble, pack_transport, row_layout, unpack_transport
from stpd.rl.loss import LossConfig, prepared_update
from stpd.rl.performance import PerformanceConfig

EXTRA = ("STRIKE_SILENT", "STRIKE_REGENT", "STRIKE_NECROBINDER", "BASH")


@pytest.fixture
def varied(monkeypatch):
    """Rows with 0-4 extra deck cards: different scene lengths and text sets."""
    counter = itertools.count()
    original = w1.public_state

    def state():
        value = original()
        extra = next(counter) % 5
        value["deck"]["content"]["cards"].extend(
            {"entity_id": f"x{i}", "definition_id": EXTRA[i % 4], "cost": i}
            for i in range(extra))
        return value

    monkeypatch.setattr(w1, "public_state", state)


def prepared(model, *layouts, microbatch_size=8):
    """The same segments assembled once per layout (default: padded-v1 only)."""
    values = [w1.segment(model, name=f"actor/{i}", run_id=f"episode-{i}", rows=5)
              for i in range(2)]
    batches = [assemble(w1.request(values), tuple(w1.source(model, v) for v in values),
                        microbatch_size=microbatch_size, deduplicate_text=True,
                        prepared_layout=layout) for layout in layouts or ("padded-v1",)]
    return batches if len(batches) > 1 else batches[0]


def forward_and_gradients(model, group):
    model.zero_grad(set_to_none=True)
    scores, logits, hp = model.forward_prepared_rl4(group)
    generator = torch.Generator().manual_seed(5)
    total = hp.mul(torch.randn(hp.shape, generator=generator)).sum()
    for value in (scores, logits):
        weights = torch.randn(value.shape, generator=generator)
        total = total + value.masked_fill(~value.isfinite(), 0).mul(weights).sum()
    total.backward()
    gradients = {name: p.grad.clone() for name, p in model.named_parameters()
                 if p.grad is not None}
    return (scores.detach(), logits.detach(), hp.detach()), gradients


def assert_same_function(model, padded, rows):
    (s0, l0, h0), g0 = forward_and_gradients(model, padded)
    (s1, l1, h1), g1 = forward_and_gradients(model, rows)
    for left, right in ((s0, s1), (l0, l1), (h0, h1)):
        assert left.shape == right.shape
        assert torch.equal(left.isneginf(), right.isneginf())
        finite = left.isfinite()
        torch.testing.assert_close(left[finite], right[finite], rtol=1e-5, atol=2e-6)
    assert set(g0) == set(g1)
    for name in g0:
        torch.testing.assert_close(g0[name], g1[name], rtol=1e-4, atol=2e-6, msg=name)


@pytest.mark.parametrize("bucket_rows,width_multiple", [(256, 64), (3, 8), (1, 1)])
def test_rows_forward_equals_padded_forward_on_cpu(model, varied, bucket_rows, width_multiple):
    batch = prepared(model)
    groups = [g for mb in batch.minibatches for g in mb.groups]
    assert groups and all(g.layout is None for g in groups)
    lengths = {int(n) for g in groups for n in (~g.tensors["scene_mask"][:len(g.rows)]).sum(1)}
    assert len(lengths) > 1  # the fixture really varies scene length
    for group in groups:
        layout = row_layout(group, bucket_rows=bucket_rows, width_multiple=width_multiple)
        if bucket_rows == 1:
            assert len(layout.scene_buckets) == len(group.rows)
        assert sum(b[3] for b in layout.scene_buckets) == int((~group.tensors["scene_mask"][
            :len(group.rows)]).sum())
        assert_same_function(model, group, dc.replace(group, layout=layout))
        plain = dc.replace(group, policy=False)
        assert_same_function(model, plain, dc.replace(plain, layout=row_layout(
            plain, bucket_rows=bucket_rows, width_multiple=width_multiple)))


def test_preparation_builds_the_same_layout_and_it_survives_transport(model, varied):
    padded, rows = prepared(model, "padded-v1", "rows-v2")
    for mb0, mb1 in zip(padded.minibatches, rows.minibatches, strict=True):
        for g0, g1 in zip(mb0.groups, mb1.groups, strict=True):
            assert g1.layout is not None
            expected = row_layout(g0)
            moved = unpack_transport(pack_transport(g1))
            for layout in (g1.layout, moved.layout):
                for field in dc.fields(expected):
                    left, right = getattr(expected, field.name), getattr(layout, field.name)
                    assert repr(left) == repr(right), field.name
            assert_same_function(model, g0, moved)


def test_prepared_update_refuses_a_layout_that_does_not_match_the_selection(model, varied):
    batch, rows = prepared(model, "padded-v1", "rows-v2")
    optimizer = torch.optim.AdamW(model.parameters())
    config = LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01)
    for selected, minibatch in (("rows-v2", batch.minibatches[0]),
                                ("padded-v1", rows.minibatches[0])):
        with pytest.raises(BoundaryError, match="prepared_layout_does_not_match"):
            prepared_update(model, optimizer, minibatch, config=config,
                            performance=PerformanceConfig(prepared_forward=selected))


def test_one_rows_v2_optimizer_step_matches_padded_v1_on_cpu(model, varied):
    padded, rows = prepared(model, "padded-v1", "rows-v2")
    config = LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01)
    results, weights = [], []
    for selected, batch in (("padded-v1", padded), ("rows-v2", rows)):
        trained = copy.deepcopy(model)
        optimizer = torch.optim.AdamW(trained.parameters(), lr=1e-3)
        result = prepared_update(trained, optimizer, batch.minibatches[0], config=config,
                                 performance=PerformanceConfig(prepared_forward=selected),
                                 target_kl=1e9)
        assert result["stepped"]
        results.append(result)
        weights.append(trained.state_dict())
    for key in ("td_loss", "policy_loss", "terminal_loss", "hp_loss", "approx_kl_mu_theta",
                "gradient_norm"):
        assert results[0][key] == pytest.approx(results[1][key], rel=1e-5, abs=1e-7), key
    for name, value in weights[0].items():
        torch.testing.assert_close(value, weights[1][name], rtol=1e-4, atol=1e-6, msg=name)


def test_selection_is_explicit_and_compile_needs_rows_v2():
    assert PerformanceConfig().prepared_forward == "padded-v1"
    with pytest.raises(BoundaryError, match="invalid_prepared_forward"):
        PerformanceConfig(prepared_forward="rows")
    with pytest.raises(BoundaryError, match="prepared_compile_requires_rows_v2"):
        PerformanceConfig(prepared_compile=True)
    with pytest.raises(ValueError, match="unsupported_prepared_layout"):
        assemble(None, (), prepared_layout="rows")  # type: ignore[arg-type]


def test_rows_forward_refuses_training_mode(model, varied):
    group = prepared(model, "rows-v2").minibatches[0].groups[0]
    model.train()
    try:
        with pytest.raises(ValueError, match="rows_forward_requires_eval_mode"):
            model.forward_prepared_rl4(group)
    finally:
        model.eval()


@pytest.mark.skipif(os.name != "posix", reason="real spawned SGD process")
def test_rows_v2_trains_in_the_sgd_process_close_to_padded_in_process(tmp_path, monkeypatch):
    """Real socket/WAL/preparation workers and SGD child: the selection reaches the
    workers, the layout survives the binary hand-off into the child, and 2 datasets
    train like padded-v1 in-process. Each update reports the attention path."""
    import test_rl_sgd_process as split

    original = split.configuration
    trained = {}
    for layout, sgd in (("padded-v1", False), ("rows-v2", True)):
        def configured(path, patch, _layout=layout, **kwargs):
            config, io = original(path, patch, **kwargs)
            return dc.replace(config, performance=dc.replace(
                config.performance, prepared_forward=_layout)), io

        monkeypatch.setattr(split, "configuration", configured)
        owner, _, _ = split.terminal_owner(tmp_path / layout, monkeypatch, sgd=sgd)
        try:
            events = split.train(owner, 2)
            assert owner.updates == 4
            facts = [event.get("rows_forward") for event in events]
            assert facts == ([{"path": "masked", "compiled": False}] * 4 if sgd
                             else [None] * 4)  # CPU: masked; varlen is CUDA sm80+ BF16
            owner.checkpoint(final=True)
            trained[layout] = split.read_cp(owner)[1].weights
        finally:
            owner.close()
    for name, value in trained["padded-v1"].items():
        torch.testing.assert_close(value, trained["rows-v2"][name], rtol=1e-4, atol=1e-5,
                                   msg=name)


@pytest.mark.parametrize("compile_", [False, True])
def test_rows_v2_without_the_sgd_process_is_a_typed_config_error(tmp_path, monkeypatch,
                                                                  compile_):
    import test_rl_sgd_process as split

    original = split.configuration

    def configured(path, patch, **kwargs):
        config, io = original(path, patch, **kwargs)
        return dc.replace(config, performance=dc.replace(
            config.performance, prepared_forward="rows-v2", prepared_compile=compile_)), io

    monkeypatch.setattr(split, "configuration", configured)
    with pytest.raises(BoundaryError, match="rows_v2_requires_sgd_process"):
        split.terminal_owner(tmp_path, monkeypatch, sgd=False, episodes=0)


@pytest.fixture
def bare(monkeypatch):
    """No deck, no playable cards (so no role terms) and an empty catalog label."""
    original = w1.public_state

    def state():
        value = original()
        value["deck"]["content"]["cards"] = []
        value["scene"]["content"]["surface"] = {"playable_cards": []}
        value["catalog"][0]["label"] = ""
        return value

    monkeypatch.setattr(w1, "public_state", state)


def test_rows_forward_handles_empty_text_and_absent_roles(model, bare):
    """Card edge cases: an empty text string and candidates with no role terms.
    (A group with no text at all is not producible: kind/field texts are always
    present in a public frame.)"""
    batch = prepared(model)
    groups = [g for mb in batch.minibatches for g in mb.groups]
    for group in groups:
        layout = row_layout(group)
        assert len(layout.roles["target"][0]) == 0  # no target terms anywhere
        assert not bool(layout.role_present["target"].any())
        assert not bool(layout.role_present["source"].all())
        assert_same_function(model, group, dc.replace(group, layout=layout))


def test_effective_config_records_rows_v2_only_when_selected_and_resume_ignores_it():
    from stpd.rl.learner import LearnerConfig
    from stpd.rl.segment_learner import _resume_config
    from stpd.rl.segments import SegmentConfig

    base = LearnerConfig(run="rows-unit", peer={"kind": "local", "directory": "/nonexistent"},
                         performance=PerformanceConfig(), segments=SegmentConfig())
    selected = dc.replace(base, performance=PerformanceConfig(prepared_forward="rows-v2"))
    assert "prepared_forward" not in base.frozen()["performance"]
    assert selected.frozen()["performance"]["prepared_forward"] == "rows-v2"
    assert "prepared_compile" not in selected.frozen()["performance"]
    # The binary recipe digest and the CP resume comparison see the same objectives.
    assert _resume_config(dc.asdict(base)) == _resume_config(dc.asdict(selected))


def test_compiled_rows_forward_trains_and_diagnostic_steps_run_eager(model, varied):
    """A compiled backward refuses retain_graph (per-head diagnostics); those steps
    run the same block eager, every other step compiled."""
    padded, rows = prepared(model, "padded-v1", "rows-v2")
    config = LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01)
    reference = copy.deepcopy(model)
    eager_optimizer = torch.optim.AdamW(reference.parameters(), lr=1e-3)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    for diagnostics in (True, False):
        result = prepared_update(
            model, optimizer, rows.minibatches[0], config=config, target_kl=1e9,
            performance=PerformanceConfig(prepared_forward="rows-v2", prepared_compile=True),
            diagnostics=diagnostics)
        expected = prepared_update(reference, eager_optimizer, padded.minibatches[0],
                                   config=config, target_kl=1e9,
                                   performance=PerformanceConfig(), diagnostics=diagnostics)
        assert result["stepped"] and ("trunk_gradients" in result) == diagnostics
        facts = result["rows_forward"]
        assert facts["path"] == "masked" and facts["compiled"] is not diagnostics
        assert facts["dynamo_frames_ok"] >= (0 if diagnostics else 1)
        assert result["policy_loss"] == pytest.approx(expected["policy_loss"], rel=1e-4,
                                                      abs=1e-6)
    for name, value in reference.state_dict().items():
        torch.testing.assert_close(model.state_dict()[name], value, rtol=1e-4, atol=1e-5,
                                   msg=name)
