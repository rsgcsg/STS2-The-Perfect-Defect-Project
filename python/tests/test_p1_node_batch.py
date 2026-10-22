"""Packed node terms preserve complete inputs, RNG and training updates."""

from __future__ import annotations

import copy
import os
from types import MethodType

import pytest
import torch
from p1_node_oracle import original_node_vectors
from torch.nn import functional as F

from stpd.p1.input import Entity, Frame, fit_vocabulary
from stpd.p1.model import MStd, _mean_terms
from stpd.p1.profiling import synthetic_frame

DEVICES = ["cpu", pytest.param("cuda:0", id="cuda", marks=pytest.mark.skipif(
    not torch.cuda.is_available(), reason="real CUDA device required; skip is unverified",
))]


@pytest.fixture(autouse=True)
def deterministic_cpu(request):
    threads = torch.get_num_threads()
    deterministic = torch.are_deterministic_algorithms_enabled()
    warn_only = torch.is_deterministic_algorithms_warn_only_enabled()
    settings = (torch.backends.cuda.matmul.allow_tf32, torch.backends.cudnn.allow_tf32,
                torch.backends.cudnn.benchmark)
    torch.set_num_threads(2)
    torch.use_deterministic_algorithms(True)
    try:
        device = getattr(request.node, "callspec", None)
        if device is not None and device.params.get("device", "cpu").startswith("cuda"):
            assert os.environ.get("CUBLAS_WORKSPACE_CONFIG") in {":4096:8", ":16:8"}
            torch.backends.cuda.matmul.allow_tf32 = False
            torch.backends.cudnn.allow_tf32 = False
            torch.backends.cudnn.benchmark = False
        yield
    finally:
        torch.set_num_threads(threads)
        torch.use_deterministic_algorithms(deterministic, warn_only=warn_only)
        (torch.backends.cuda.matmul.allow_tf32, torch.backends.cudnn.allow_tf32,
         torch.backends.cudnn.benchmark) = settings


def rng_state(device):
    return torch.cuda.get_rng_state(device) if device.startswith("cuda") else torch.get_rng_state()


def restore_rng(state, device):
    if device.startswith("cuda"):
        torch.cuda.set_rng_state(state, device)
    else:
        torch.set_rng_state(state)


def edge_frame():
    return Frame([
        Entity("empty", 0),
        Entity("mixed", 1, [("first_id", "A"), ("second_id", "B"), ("first_id", "A")],
               [("description", "Gain 5 Block."), ("label", "Other"), ("label", "Other")],
               [("amount", 0.0), ("optional", None), ("amount", -123.0)], [1, 0, 1]),
        Entity("missing", 2, numeric=[("optional", None)], refs=[1]),
        Entity("no_numbers", 3, text=[("label", "Other")], refs=[0, 1, 2, 3, 1]),
    ], [], [])


def compare_gradients(wanted, actual):
    for (name, old), (other, new) in zip(wanted.named_parameters(), actual.named_parameters(),
                                       strict=True):
        assert name == other
        assert (old.grad is None) == (new.grad is None), name
        if old.grad is not None:
            torch.testing.assert_close(new.grad, old.grad, rtol=3e-4, atol=2e-6, msg=name)


@pytest.mark.parametrize("training", [False, True])
@pytest.mark.parametrize("case", ["mixed", "empty", "missing", "present", "star"])
@pytest.mark.parametrize("device", DEVICES)
def test_original_nodes_outputs_gradients_and_rng(training, case, device):
    frame = edge_frame()
    if case == "empty":
        frame = Frame([Entity("empty", 0), Entity("empty", 1)], [], [])
    elif case == "missing":
        frame = Frame([Entity("missing", 0, numeric=[("optional", None)] * 3)], [], [])
    elif case == "present":
        frame = Frame([Entity("present", 0, numeric=[("amount", 0.0), ("amount", 42.0)])], [], [])
    elif case == "star":
        frame = synthetic_frame(64)
        frame.entities[0].refs += [1, 1, 2]  # Repeated references retain multiplicity.
    torch.manual_seed(17)
    actual = MStd(fit_vocabulary([frame])).to(device).train(training)
    wanted = copy.deepcopy(actual)
    texts = sorted({text for e in frame.entities for _, text in e.text})
    vectors = torch.randn(len(texts), 192, device=device, requires_grad=True)
    reference_vectors = vectors.detach().clone().requires_grad_()
    before = rng_state(device)
    expected = original_node_vectors(wanted, frame, reference_vectors)
    after = rng_state(device)
    restore_rng(before, device)
    result = actual.node_vectors(frame, vectors)
    assert torch.equal(after, rng_state(device))
    torch.testing.assert_close(result, expected, rtol=2e-5, atol=2e-6)
    # A nonuniform upstream gradient exercises every node and embedding row.
    upstream = torch.randn_like(expected)
    expected.backward(upstream)
    result.backward(upstream)
    compare_gradients(wanted, actual)
    assert (vectors.grad is None) == (reference_vectors.grad is None)
    if vectors.grad is not None:
        torch.testing.assert_close(vectors.grad, reference_vectors.grad, rtol=3e-4, atol=2e-6)


@pytest.mark.parametrize("device", DEVICES)
def test_original_full_training_rng_gradients_and_next_optimizer_state(device):
    frames = [synthetic_frame(3), synthetic_frame(5)]
    frames[0].entities[0].numeric += [("amount", 0.0), ("optional", None)]
    frames[0].entities[0].refs += [1, 1]
    torch.manual_seed(29)
    actual = MStd(fit_vocabulary(frames)).to(device).train()
    wanted = copy.deepcopy(actual)
    wanted.node_vectors = MethodType(original_node_vectors, wanted)
    optimizers = [torch.optim.AdamW(m.parameters(), lr=2e-4) for m in (wanted, actual)]
    before = rng_state(device)
    scores = wanted.forward_batch(frames)
    after = rng_state(device)
    restore_rng(before, device)
    results = actual.forward_batch(frames)
    assert torch.equal(after, rng_state(device))
    for result, expected in zip(results, scores, strict=True):
        torch.testing.assert_close(result, expected, rtol=2e-5, atol=2e-6)
    score_difference = max(float((a - b).abs().max().detach())
                           for a, b in zip(scores, results, strict=True))
    for output in (scores, results):
        sum(F.cross_entropy(value[None], torch.tensor([i], device=device))
            for i, value in enumerate(output)).backward()
    compare_gradients(wanted, actual)
    gradient_difference = max(float((a.grad - b.grad).abs().max())
                              for a, b in zip(wanted.parameters(), actual.parameters(), strict=True)
                              if a.grad is not None)
    for optimizer in optimizers:
        optimizer.step()
    parameter_difference = state_difference = 0.0
    for (name, old), (_, new) in zip(wanted.named_parameters(), actual.named_parameters(),
                                   strict=True):
        torch.testing.assert_close(new, old, rtol=3e-4, atol=2e-6, msg=name)
        parameter_difference = max(parameter_difference, float((new - old).abs().max().detach()))
        for key, value in optimizers[0].state[old].items():
            torch.testing.assert_close(optimizers[1].state[new][key], value, rtol=3e-4, atol=2e-6,
                                       msg=f"{name}.{key}")
            state_difference = max(state_difference,
                                   float((optimizers[1].state[new][key] - value).abs().max()))
    print({"device": device, "deterministic_algorithms": True,
           "training_scores_max_abs": score_difference,
           "gradients_max_abs": gradient_difference,
           "next_parameters_max_abs": parameter_difference,
           "optimizer_state_max_abs": state_difference})


def test_ragged_padding_is_linear_and_does_not_share_missing_nan(monkeypatch):
    values = torch.randn(1025, 192)
    values[-1] = torch.nan
    rows = [list(range(1024)), *[[i] for i in range(1024)], [], [1024]]
    # Observe the actual gathers for a star plus singletons, including the final
    # row reorder; an N*max_degree allocation would exceed this bound.
    sizes = []
    original = torch.Tensor.index_select

    def record(value, dim, index):
        sizes.append(index.numel())
        return original(value, dim, index)

    monkeypatch.setattr(torch.Tensor, "index_select", record)
    result = _mean_terms(values, rows, values.new_zeros(192))
    assert sum(sizes[:-1]) < 2 * sum(len(row) for row in rows)
    assert sizes[-1] == len(rows)
    assert torch.isfinite(result[:-1]).all()
    assert torch.isnan(result[-1]).all()
    assert torch.equal(result[-2], torch.zeros(192))
