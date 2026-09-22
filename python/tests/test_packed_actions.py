"""Packed B matches isolated branches without repeating the observation computation."""
import copy
from unittest.mock import patch

import pytest
import torch
from test_stage1a_models import core, sample, tiny_qwen_core

from stpd.models.stage1a import BTokenScorer
from stpd.models.token_core import TokenCore


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


def test_scratch_entire_catalog_is_one_transformer_call():
    model = BTokenScorer(core(), packed=True).eval()
    state, actions = sample()
    with patch.object(model.core.encoder, "forward", wraps=model.core.encoder.forward) as forward:
        model(state, actions)
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
