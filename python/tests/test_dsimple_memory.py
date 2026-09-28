"""Small synthetic graph checks for experimental D-Simple M2, not policy quality."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

import pytest
import torch

from stpd.models.dsimple_memory import ExperimentalDSimpleM2
from stpd.models.losses import listwise_rank_loss
from stpd.models.token_core import ScratchShape, ScratchTokenCore


@pytest.fixture(autouse=True)
def single_cpu_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def model(*, slots: int = 1, reset: bool = False, gated: bool = False,
          frozen: bool = False, max_tokens: int = 64) -> ExperimentalDSimpleM2:
    torch.manual_seed(47)
    core = ScratchTokenCore(ScratchShape(
        vocab_size=32, width=8, layers=1, heads=2, feedforward=16,
        dropout=0, max_tokens=max_tokens,
    ))
    if frozen:
        core.frozen = True
        core.requires_grad_(False).eval()
        core.supports_bidirectional = False
    return ExperimentalDSimpleM2(
        core, slots=slots, reset_each_step=reset, gated=gated,
    ).eval()


def tokens(*values: int) -> torch.Tensor:
    return torch.tensor(values, dtype=torch.long)


@pytest.mark.parametrize("slots", [1, 8])
@pytest.mark.parametrize("gated", [False, True])
def test_two_observations_preserve_gradient_through_old_memory(slots: int, gated: bool):
    scorer = model(slots=slots, gated=gated)
    initial = scorer.initial_memory()
    assert initial.shape == (slots, 8) and torch.count_nonzero(initial) == 0
    first = scorer.advance(tokens(1, 2, 3), initial)
    first.retain_grad()
    scores, second = scorer.step(
        tokens(4, 5), (tokens(6, 7), tokens(8, 9)), first,
        previous_actual_action=tokens(10, 11), feedback=tokens(12),
    )
    assert scores.shape == (2,) and second.shape == first.shape
    assert torch.isfinite(scores).all() and torch.isfinite(second).all()
    listwise_rank_loss(scores, 0).backward()
    assert first.grad is not None and first.grad.abs().sum() > 0
    assert scorer.write_queries.grad is not None
    assert scorer.write_queries.grad.abs().sum() > 0
    assert scorer.action_encoder.conv.weight.grad is not None
    assert scorer.action_encoder.conv.weight.grad.abs().sum() > 0


def test_reset_control_has_same_parameters_but_discards_previous_memory():
    persistent = model(slots=8, gated=True)
    reset = model(slots=8, gated=True, reset=True)
    reset.load_state_dict(deepcopy(persistent.state_dict()), strict=True)
    assert persistent.state_dict().keys() == reset.state_dict().keys()
    assert all(torch.equal(p, q) and p.data_ptr() != q.data_ptr()
               for p, q in zip(persistent.parameters(), reset.parameters(), strict=True))
    old_a = torch.ones(8, 8)
    old_b = -torch.ones(8, 8)
    page = tokens(1, 2)
    torch.testing.assert_close(reset.advance(page, old_a), reset.advance(page, old_b),
                               rtol=0, atol=0)
    assert not torch.allclose(persistent.advance(page, old_a),
                              persistent.advance(page, old_b))
    torch.testing.assert_close(persistent.advance(page, old_a, reset_before=True),
                               reset.advance(page, old_a), rtol=0, atol=0)


def test_confirmed_previous_action_and_optional_public_feedback_are_inputs():
    scorer = model(slots=8)
    old = scorer.initial_memory()
    page = tokens(1, 2)
    base = scorer.advance(page, old)
    with_action = scorer.advance(page, old, previous_actual_action=tokens(3))
    with_feedback = scorer.advance(page, old, feedback=tokens(4))
    assert not torch.allclose(base, with_action)
    assert not torch.allclose(base, with_feedback)
    changed_action = scorer.advance(page, old, previous_actual_action=tokens(5))
    changed_feedback = scorer.advance(page, old, feedback=tokens(6))
    assert not torch.allclose(with_action, changed_action)
    assert not torch.allclose(with_feedback, changed_feedback)
    # The API has no argument for a merely selected/current candidate in advance().


def test_frozen_core_preserves_gradient_to_memory_and_trainable_inputs():
    scorer = model(slots=8, frozen=True)
    old = scorer.initial_memory().requires_grad_()
    updated = scorer.advance(tokens(1, 2), old, previous_actual_action=tokens(3))
    scores = scorer.score(updated, (tokens(4), tokens(5)))
    listwise_rank_loss(scores, 1).backward()
    assert old.grad is not None and old.grad.abs().sum() > 0
    assert scorer.write_queries.grad is not None
    assert scorer.write_queries.grad.abs().sum() > 0
    assert scorer.action_encoder.embedding.weight.grad is not None
    assert scorer.action_encoder.embedding.weight.grad.abs().sum() > 0
    assert not scorer.core.training
    assert all(parameter.grad is None for parameter in scorer.core.parameters())


def test_scoring_is_read_only_permutation_equivariant_and_candidate_isolated():
    scorer = model(slots=8)
    memory = scorer.advance(tokens(1, 2, 3), scorer.initial_memory())
    before = memory.detach().clone()
    actions = (tokens(4, 5), tokens(6), tokens(7, 8, 9))
    scores = scorer.score(memory, actions)
    torch.testing.assert_close(scorer.score(memory, actions[::-1]), scores.flip(0))
    torch.testing.assert_close(scorer.score(memory, (actions[0], tokens(10)))[0],
                               scores[0])
    assert scores.shape == (3,) and torch.isfinite(scores).all()
    assert torch.equal(memory, before)


def test_zero_transition_keeps_memory_read_as_residual_anchor():
    scorer = model(slots=1)
    with torch.no_grad():
        for parameter in scorer.transition.parameters():
            parameter.zero_()
        scorer.memory_value.weight.copy_(torch.eye(8))
        scorer.memory_value.bias.zero_()
        scorer.score_head[0].weight.copy_(torch.eye(8))
        scorer.score_head[0].bias.zero_()
        scorer.score_head[2].weight.zero_()
        scorer.score_head[2].weight[0, 0] = 1
        scorer.score_head[2].bias.zero_()
    actions = (tokens(1), tokens(2, 3))
    memory_a = torch.eye(8)[0:1]
    memory_b = torch.eye(8)[1:2]
    scores_a = scorer.score(memory_a, actions)
    scores_b = scorer.score(memory_b, actions)
    torch.testing.assert_close(scores_a[0], scores_a[1], rtol=0, atol=0)
    torch.testing.assert_close(scores_b[0], scores_b[1], rtol=0, atol=0)
    assert not torch.allclose(scores_a, scores_b)


def test_candidate_count_does_not_add_page_calls_or_memory_writes():
    scorer = model(slots=8)
    page = tokens(1, 2)
    old = scorer.initial_memory()
    actions = (tokens(3), tokens(4), tokens(5), tokens(6))
    with (
        patch.object(scorer.core, "contextualize", wraps=scorer.core.contextualize) as page_call,
        patch.object(scorer.write_norm, "forward", wraps=scorer.write_norm.forward) as write,
    ):
        scorer.step(page, actions[:2], old)
        assert (page_call.call_count, write.call_count) == (1, 1)
        scorer.step(page, actions, old)
        assert (page_call.call_count, write.call_count) == (2, 2)
        scorer.score(old, actions)
        assert (page_call.call_count, write.call_count) == (2, 2)


def test_full_preflight_rejects_bad_last_candidate_before_any_write():
    scorer = model(slots=8, max_tokens=20)
    page = tokens(1, 2)
    old = scorer.initial_memory()
    with (
        patch.object(scorer.core, "contextualize", wraps=scorer.core.contextualize) as page_call,
        patch.object(scorer.action_encoder, "forward",
                     wraps=scorer.action_encoder.forward) as action_call,
    ):
        with pytest.raises(ValueError, match="vocabulary"):
            scorer.step(page, (tokens(3), tokens(32)), old)
        with pytest.raises(ValueError, match="token limit"):
            scorer.step(tokens(1, 2, 3, 4, 5), (tokens(3), tokens(4)), old)
        with pytest.raises(ValueError, match="values"):
            scorer.step(page, (tokens(3), tokens(4)),
                        torch.full((8, 8), float("nan")))
        with pytest.raises(ValueError, match="shape"):
            scorer.step(page, (tokens(3), tokens(4)), torch.zeros(7, 8))
        with pytest.raises(ValueError, match="dtype"):
            scorer.step(page, (tokens(3), tokens(4)), old.double())
        with pytest.raises(ValueError, match="int64"):
            scorer.step(page, (tokens(3), tokens(4)), old,
                        previous_actual_action=tokens(5).float())
        with pytest.raises(ValueError, match="vocabulary"):
            scorer.step(page, (tokens(3), tokens(4)), old, feedback=tokens(32))
        page_call.assert_not_called()
        action_call.assert_not_called()
    assert torch.count_nonzero(old) == 0


def test_state_dict_roundtrip_and_reset_parameter_parity():
    original = model(slots=8, gated=True)
    restored = model(slots=8, gated=True, reset=True)
    restored.load_state_dict(original.state_dict(), strict=True)
    memory = original.initial_memory()
    page = tokens(1, 2)
    actions = (tokens(3), tokens(4))
    before = original.step(page, actions, memory)
    after = restored.step(page, actions, memory)
    torch.testing.assert_close(after[0], before[0], rtol=0, atol=0)
    torch.testing.assert_close(after[1], before[1], rtol=0, atol=0)
