"""Opt-in execution preserves complete terms and rejects invalid updates before AdamW."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
import torch
from test_p1_node_batch import compare_gradients, edge_frame
from test_p1_t0 import observation

from spireagent.json_boundary import BoundaryError
from stpd.p1.execution import MStdExecution, microbatches
from stpd.p1.input import fit_vocabulary, project
from stpd.p1.model import MStd
from stpd.p1.numerics import TrainingNumerics
from stpd.p1.optimization import optimizer_update
from stpd.p1.profiling import synthetic_frame


@pytest.mark.parametrize("training", [False, True])
@pytest.mark.parametrize("implementation", ["row-fused-v1", "experimental-packed-v1"])
def test_fused_rows_complete_node_terms_gradients_and_rng(training, implementation):
    torch.manual_seed(73)
    torch.set_num_threads(2)
    frame = edge_frame()
    reference = MStd(fit_vocabulary([frame])).train(training)
    packed = copy.deepcopy(reference)
    packed.execution = MStdExecution(implementation=implementation)
    texts = sorted({text for entity in frame.entities for _, text in entity.text})
    values = torch.randn(len(texts), 192, requires_grad=True)
    other = values.detach().clone().requires_grad_()
    before = torch.get_rng_state()
    wanted = reference.node_vectors(frame, values)
    after = torch.get_rng_state()
    torch.set_rng_state(before)
    result = packed.node_vectors(frame, other)
    assert torch.equal(after, torch.get_rng_state())
    torch.testing.assert_close(result, wanted, rtol=2e-5, atol=2e-6)
    upstream = torch.randn_like(wanted)
    wanted.backward(upstream)
    result.backward(upstream)
    compare_gradients(reference, packed)
    torch.testing.assert_close(values.grad, other.grad, rtol=3e-4, atol=2e-6)


def test_fused_rows_full_catalog_and_three_updates_keep_absent_gradients_and_are_finite():
    frames = [synthetic_frame(3), synthetic_frame(5)]
    torch.manual_seed(29)
    reference = MStd(fit_vocabulary(frames))
    packed = copy.deepcopy(reference)
    packed.execution = MStdExecution(implementation="row-fused-v1", defer_score_sync=True)
    models = [reference, packed]
    optimizers = [torch.optim.AdamW(m.parameters(), lr=3e-4) for m in models]
    scopes = [TrainingNumerics(5, 2, torch.device("cpu")) for _ in models]
    for _ in range(3):
        rows = []
        for model, optimizer, scope in zip(models, optimizers, scopes, strict=True):
            with scope.scope():
                rows.append(optimizer_update(model, optimizer, frames, [0, 1], 3e-4))
        assert abs(rows[0]["mean_N_loss"] - rows[1]["mean_N_loss"]) < 2e-5
        for old, new in zip(reference.parameters(), packed.parameters(), strict=True):
            assert (old.grad is None) == (new.grad is None)
            assert torch.isfinite(new).all()
        assert torch.equal(scopes[0].rng["torch_cpu"], scopes[1].rng["torch_cpu"])
    reference.eval()
    packed.eval()
    with torch.inference_mode():
        for old, new in zip(reference.forward_batch(frames), packed.forward_batch(frames),
                            strict=True):
            torch.testing.assert_close(new, old, rtol=3e-4, atol=2e-5)


def test_execution_packing_keeps_sample_order_tail_and_oversized_frame():
    small, large = synthetic_frame(3), synthetic_frame(1024)
    model = MStd(fit_vocabulary([small, large]), MStdExecution(
        implementation="row-fused-v1", microbatch_size=2,
        scene_attention_area=100000, text_attention_area=100000))
    frames = [small, large, small, small, small]
    groups = list(microbatches(frames, list(range(5)), model))
    assert groups == [[0], [1], [2, 3], [4]]
    assert [index for group in groups for index in group] == list(range(5))
    assert len(frames[1].entities) == len(large.entities)


def test_deferred_sync_still_rejects_nonfinite_before_any_parameter_update(monkeypatch):
    frame = project(observation())
    model = MStd(fit_vocabulary([frame]), MStdExecution(defer_score_sync=True))
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
    before = {key: value.clone() for key, value in model.state_dict().items()}
    parameter = next(model.parameters())
    monkeypatch.setattr(model, "forward_batch", lambda frames, **kw: [
        parameter.reshape(-1)[:2] * 0 + torch.tensor([-3e38, 3e38]) for _ in frames])
    with pytest.raises(BoundaryError, match="non_finite_loss"):
        optimizer_update(model, optimizer, [frame], [0], 3e-4)
    assert not optimizer.state
    assert all(torch.equal(value, before[key]) for key, value in model.state_dict().items())


def test_deferred_sync_rejects_nonfinite_unselected_score_with_finite_loss(monkeypatch):
    """A loss-only guard misses -inf on an unselected legal candidate."""
    frame = project(observation())
    model = MStd(fit_vocabulary([frame]), MStdExecution.for_plan("mstd-training-v1"))
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
    parameter = next(model.parameters())
    scores = parameter.reshape(-1)[:2] * 0 + torch.tensor([0.0, -torch.inf])
    assert torch.isfinite(torch.nn.functional.cross_entropy(scores[None], torch.tensor([0])))
    monkeypatch.setattr(model, "forward_batch", lambda frames, **kw: [scores for _ in frames])
    with pytest.raises(BoundaryError, match="non_finite_scores"):
        optimizer_update(model, optimizer, [frame], [0], 3e-4)
    assert not optimizer.state


def test_named_training_plan_keeps_legacy_constructor_and_full_microbatch_policy():
    plan = MStdExecution.for_plan("mstd-training-v1")
    assert MStdExecution().implementation == "reference"
    assert MStdExecution.for_plan("reference-v1") == MStdExecution()
    assert plan.implementation == "experimental-packed-v1" and plan.defer_score_sync
    assert (plan.microbatch_size, plan.scene_attention_area, plan.text_attention_area) == (
        8, 262144, 262144)
    assert MStdExecution.decode(plan.to_dict()) == plan
    with pytest.raises(BoundaryError, match="unsupported_execution_plan"):
        MStdExecution.for_plan("latest")


def test_legacy_checkpoint_package_keeps_identical_inference_after_new_plan(tmp_path):
    from stpd.p1.export import export_model, load_model
    from stpd.workers.checkpoint_codec import encode_checkpoint

    frame = project(observation())
    torch.manual_seed(413)
    legacy = MStd(fit_vocabulary([frame])).eval()
    checkpoint = tmp_path / "legacy.tensor-tree"
    checkpoint.write_bytes(encode_checkpoint({"model": legacy.state_dict()}))
    with torch.inference_mode():
        before = legacy(frame).clone()
    descriptor = export_model(legacy, checkpoint, tmp_path / "package", {"fixture": "legacy"})
    assert descriptor["schema"] == "stpd/p1-mstd-model-1"
    # Resolving the new training default must never mutate inference defaults.
    assert MStdExecution.for_plan("mstd-training-v1").implementation == "experimental-packed-v1"
    loaded, package = load_model(tmp_path / "package/model.tensor-tree",
                                 descriptor["package_sha256"])
    assert "inference_execution" not in package
    assert loaded.execution == MStdExecution()
    with torch.inference_mode():
        assert torch.equal(before, loaded(frame))


@pytest.mark.parametrize("change", [
    {"microbatch_size": True}, {"cpu_threads": 0}, {"scene_attention_area": 0},
    {"text_attention_area": 2**29}, {"defer_score_sync": 1}, {"implementation": "automatic"},
])
def test_execution_rejects_invalid_configuration(change):
    with pytest.raises(BoundaryError):
        replace(MStdExecution(), **change)


def test_future_packed_complete_catalog_gradients_absence_and_draws():
    """Initial mathematics is checked; multi-update exact-trajectory remains unproved."""
    frames = [edge_frame(), project(observation())]
    torch.manual_seed(97)
    reference = MStd(fit_vocabulary(frames))
    packed = copy.deepcopy(reference)
    packed.execution = MStdExecution(implementation="experimental-packed-v1")
    scopes = [TrainingNumerics(11, 2, torch.device("cpu")) for _ in range(2)]
    outputs = []
    for model, scope in zip([reference, packed], scopes, strict=True):
        with scope.scope():
            scores = model.forward_batch(frames)
            outputs.append([value.detach() for value in scores])
            sum(value.square().sum() for value in scores).backward()
    for frame, wanted, actual in zip(frames, *outputs, strict=True):
        assert actual.shape == (len(frame.choices),)
        torch.testing.assert_close(actual, wanted, rtol=3e-4, atol=2e-5)
    compare_gradients(reference, packed)
    assert torch.equal(scopes[0].rng["torch_cpu"], scopes[1].rng["torch_cpu"])
