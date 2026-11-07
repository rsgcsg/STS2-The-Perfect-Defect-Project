"""S3 rows-v2: the padding-free RL training forward against the padded original.

CPU fp32: outputs and every parameter gradient equal padded-v1 within float
accumulation tolerance, for multi-bucket layouts, varied scene/text lengths,
policy and non-policy groups. GPU BF16 step time and tolerance are Friday facts.
"""
from __future__ import annotations

import copy
import dataclasses as dc
import itertools

import pytest
import torch
import test_rl41_w1 as w1
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


def test_binary_learner_trains_rows_v2_end_to_end_close_to_padded(tmp_path, monkeypatch):
    """Real socket/WAL/preparation workers: the selection reaches the workers, the
    layout survives the binary hand-off, and 2 datasets train like padded-v1."""
    import test_rl_pipeline_checkpoint as pipeline

    original = pipeline.configuration
    trained = {}
    for layout in ("padded-v1", "rows-v2"):
        def configured(path, patch, _layout=layout, **kwargs):
            config, io = original(path, patch, **kwargs)
            return dc.replace(config, performance=dc.replace(
                config.performance, prepared_forward=_layout)), io

        monkeypatch.setattr(pipeline, "configuration", configured)
        (tmp_path / layout).mkdir()
        torch.manual_seed(20261108)
        owner, _, _ = pipeline.binary_owner(tmp_path / layout, monkeypatch, datasets=2)
        try:
            for _ in range(2):
                assert owner._prepare()
                groups = [g for mb in owner.active_batch.minibatches for g in mb.groups]
                assert all((g.layout is not None) == (layout == "rows-v2") for g in groups)
                owner._prepare_ahead()
                while owner.active is not None:
                    owner._step()
                    owner._prepare_ahead()
            assert owner.updates == 4
            trained[layout] = {k: v.detach().clone() for k, v in owner.model.state_dict().items()}
        finally:
            owner.close()
    for name, value in trained["padded-v1"].items():
        torch.testing.assert_close(value, trained["rows-v2"][name], rtol=1e-4, atol=1e-5,
                                   msg=name)
