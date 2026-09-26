"""Packed B matches isolated branches without repeating the observation computation."""
import copy
from unittest.mock import patch

import pytest
import torch
from test_stage1a_models import core, sample, tiny_qwen_core
from torch.nn import functional as F

from stpd.models.stage1a import BTokenScorer
from stpd.models.token_core import ScratchShape, ScratchTokenCore, TokenCore


@pytest.mark.parametrize("qwen", [False, True])
def test_packed_outputs_and_all_trainable_gradients_match_isolated_reference(qwen):
    backbone = tiny_qwen_core() if qwen else core()
    initial = backbone.readout_initial() if qwen else None
    reference = BTokenScorer(backbone, readout_initial=initial).eval()
    packed = copy.deepcopy(reference)
    packed.packed = True
    state, actions = sample()
    # Eval disables dropout, but gradients remain enabled. Scratch shared dropout in
    # training is intentionally a new execution identity, not bitwise v1 reproduction.
    left, right = reference(state, actions), packed(state, actions)
    torch.testing.assert_close(left, right, atol=2e-5, rtol=2e-5)
    left.square().sum().backward()
    right.square().sum().backward()
    for (name, p), (other, q) in zip(
        reference.named_parameters(), packed.named_parameters(), strict=True,
    ):
        assert name == other
        if p.requires_grad:
            torch.testing.assert_close(p.grad, q.grad, atol=2e-5, rtol=2e-4)
        else:
            assert p.grad is q.grad is None
    with torch.no_grad():
        torch.testing.assert_close(packed(state, actions[::-1]), right.flip(0))
        torch.testing.assert_close(packed(state, actions[:1]), right[:1])
        changed = (actions[0], torch.tensor([20, 21, 22, 23]))
        torch.testing.assert_close(packed(state, changed)[0], right[0])


@pytest.mark.parametrize("training", [False, True])
def test_scratch_branch_execution_matches_dense_packed_graph_and_gradients(training):
    torch.manual_seed(121)
    shape = ScratchShape(vocab_size=32, width=24, layers=3, heads=4,
                         feedforward=48, dropout=0.0, max_tokens=64)
    optimized = BTokenScorer(ScratchTokenCore(shape), packed=True).train(training)
    reference = copy.deepcopy(optimized)
    state = torch.tensor([1, 2, 3, 4, 5, 6, 7])
    actions = (torch.tensor([8, 9]), torch.tensor([10, 11, 12]), torch.tensor([13]))
    actual = optimized(state, actions)
    dense_hidden = TokenCore.read_action_queries(
        reference.core, state, actions, reference.readout,
    )
    expected = reference.head(dense_hidden).flatten()
    torch.testing.assert_close(actual, expected, atol=2e-5, rtol=2e-5)
    weights = torch.tensor([0.5, -0.7, 1.1])
    (actual * weights).sum().backward()
    (expected * weights).sum().backward()
    for (name, parameter), (other, dense_parameter) in zip(
        optimized.named_parameters(), reference.named_parameters(), strict=True,
    ):
        assert name == other
        torch.testing.assert_close(parameter.grad, dense_parameter.grad,
                                   atol=2e-5, rtol=2e-4)


def test_scratch_shared_prefix_and_attention_shapes_scale_with_branches():
    backbone = core(max_tokens=256)
    model = BTokenScorer(backbone, packed=True).eval()
    state = torch.arange(1, 121) % 32
    actions = tuple(torch.tensor([i % 32, (i + 1) % 32]) for i in range(24))
    with (patch.object(backbone, "_project_attention",
                       wraps=backbone._project_attention) as project,
          patch("stpd.models.token_core.F.scaled_dot_product_attention",
                wraps=F.scaled_dot_product_attention) as attention):
        scores = model(state, actions)
    assert scores.shape == (len(actions),)
    state_projections = [call for call in project.call_args_list
                         if call.args[1].shape[0] == len(state)]
    assert len(state_projections) == backbone.shape.layers
    assert attention.call_count == backbone.shape.layers - 1 + len(actions) * backbone.shape.layers
    total = len(state) + sum(len(action) + 1 for action in actions)
    for call in attention.call_args_list:
        query, key, _ = call.args
        assert query.shape[-2] <= len(state)
        assert key.shape[-2] <= len(state) + max(len(action) + 1 for action in actions)
        mask = call.kwargs["attn_mask"]
        if mask is not None:
            assert mask.shape == (query.shape[-2], key.shape[-2])
            assert mask.numel() < total * total


def test_scratch_dropout_training_keeps_historical_dense_call_order():
    model = BTokenScorer(core(), packed=True).train()
    state, actions = sample()
    with patch.object(model.core.encoder, "forward",
                      wraps=model.core.encoder.forward) as forward:
        model(state, actions).sum().backward()
    assert forward.call_count == 1
    assert forward.call_args.args[0].shape[1] == len(state) + sum(len(a) + 1 for a in actions)


def test_frozen_all_readouts_share_one_fixed_prefix_and_one_query_pass():
    backbone = tiny_qwen_core()
    model = BTokenScorer(backbone, readout_initial=backbone.readout_initial(), packed=True).eval()
    state, actions = sample()
    with patch.object(backbone.model, "forward", wraps=backbone.model.forward) as forward:
        actual = model(state, actions)
    assert forward.call_count == 2
    assert forward.call_args_list[0].kwargs["inputs_embeds"].shape[1] == (
        len(state) + sum(len(a) for a in actions))
    assert forward.call_args_list[1].kwargs["inputs_embeds"].shape[1] == len(actions)
    # One literal packed forward is the independent reference for the PF optimization.
    hidden = TokenCore.read_action_queries(backbone, state, actions, model.readout)
    expected = model.head(hidden).flatten()
    torch.testing.assert_close(actual, expected, atol=2e-5, rtol=2e-5)
    g1 = torch.autograd.grad(actual.sum(), model.readout)[0]
    g2 = torch.autograd.grad(expected.sum(), model.readout)[0]
    torch.testing.assert_close(g1, g2, atol=2e-5, rtol=2e-4)
