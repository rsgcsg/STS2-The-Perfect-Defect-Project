"""Synthetic graph tests; no corpus, recipes, runtime or efficacy claims."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import torch

from stpd.light_action_codec import BOS_ACT, EOS_ACT
from stpd.models.light_action_m2 import LIGHT_ACTION_M2_GRAPH, LightActionM2Scorer
from stpd.models.token_core import ScratchShape, ScratchTokenCore


def core(*, frozen: bool = False, width: int = 16):
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(71)
        value = ScratchTokenCore(ScratchShape(
            vocab_size=64, width=width, layers=1, heads=2, feedforward=32,
            dropout=0.0, max_tokens=64,
        ))
    value.frozen = frozen
    if frozen:
        value.requires_grad_(False).eval()
        value.supports_bidirectional = False
    return value


def action(*values: int) -> torch.Tensor:
    return torch.tensor([BOS_ACT, *values, EOS_ACT], dtype=torch.long)


def make_model(*, slots: int = 1, reset: bool = False, width: int = 16,
               frozen: bool = False, seed: int = 43) -> LightActionM2Scorer:
    return LightActionM2Scorer(
        core(frozen=frozen, width=width), max_action_bytes=32,
        initialization_seed=seed, slots=slots, reset_each_step=reset,
    )


def sample():
    return torch.tensor([1, 2, 3, 4]), (action(10, 11), action(12), action(13, 14))


@pytest.mark.parametrize("slots", [1, 8])
def test_complete_catalog_scores_permute_without_candidate_writes(slots):
    model = make_model(slots=slots).eval()
    page, actions = sample()
    memory = model.initial_memory()
    scores, updated = model.step(page, actions, memory)
    reversed_scores, reversed_memory = model.step(page, actions[::-1], memory)
    torch.testing.assert_close(reversed_scores, scores.flip(0))
    torch.testing.assert_close(reversed_memory, updated)
    assert updated.grad_fn is not None
    assert scores.shape == (len(actions),) and torch.isfinite(scores).all()
    assert model.slots == slots and model.score(model.initial_memory(), actions).shape == (3,)


def test_whole_catalog_is_preflighted_before_memory_write():
    model = make_model()
    page, actions = sample()
    invalid_catalog = (actions[0], torch.tensor([BOS_ACT, 999, EOS_ACT]))
    with patch.object(model, "advance", wraps=model.advance) as advance:
        with pytest.raises(ValueError, match="byte action"):
            model.step(page, invalid_catalog, model.initial_memory())
        advance.assert_not_called()
    with pytest.raises(ValueError, match="complete candidate catalog"):
        model.step(page, (), model.initial_memory())


def test_writer_uses_only_page_memory_and_qualified_past_inputs():
    model = make_model()
    page, actions = sample()
    memory = model.initial_memory()
    chosen, _ = model.step(page, actions, memory, previous_actual_action=actions[1])
    another_catalog = (action(55), action(56, 57))
    other_scores, _ = model.step(page, another_catalog, memory,
                                 previous_actual_action=actions[1])
    # The common write cannot inspect current candidate catalog contents/count.
    torch.testing.assert_close(
        model.step(page, actions, memory, previous_actual_action=actions[1])[1],
        model.step(page, another_catalog, memory,
                   previous_actual_action=actions[1])[1],
    )
    assert chosen.shape == (3,) and other_scores.shape == (2,)
    assert "label" not in model.advance.__annotations__


def test_reset_zeros_old_memory_but_can_consume_same_previous_action():
    model = make_model().eval()
    page, actions = sample()
    old = torch.randn_like(model.initial_memory())
    with_action = model.advance(page, old, previous_actual_action=actions[0], reset_before=True)
    zero_old = model.advance(page, model.initial_memory(),
                             previous_actual_action=actions[0], reset_before=True)
    torch.testing.assert_close(with_action, zero_old)
    no_action = model.advance(page, model.initial_memory(), reset_before=True)
    assert not torch.equal(with_action, no_action)


def test_online_steps_match_explicit_offline_unroll_with_shared_gradient_graph():
    model = make_model().train()
    pages = (torch.tensor([1, 2]), torch.tensor([3, 4, 5]), torch.tensor([6, 7]))
    catalogs = ((action(10), action(11)), (action(12, 13), action(14)),
                (action(15), action(16, 17)))
    previous = (None, catalogs[0][1], catalogs[1][0])
    online_memory = model.initial_memory()
    online_outputs = []
    for index in range(3):
        scores, online_memory = model.step(
            pages[index], catalogs[index], online_memory,
            previous_actual_action=previous[index], reset_before=index == 0,
        )
        online_outputs.append(scores)

    offline_memory = model.initial_memory()
    offline_outputs = []
    for index in range(3):
        offline_memory = model.advance(
            pages[index], offline_memory, previous_actual_action=previous[index],
            reset_before=index == 0,
        )
        offline_outputs.append(model.score(offline_memory, catalogs[index]))
    for online, offline in zip(online_outputs, offline_outputs, strict=True):
        torch.testing.assert_close(online, offline)
    torch.testing.assert_close(online_memory, offline_memory)
    assert online_memory.grad_fn is not None  # Caller, not graph, owns BPTT detachment.


def test_branch_initialization_is_rng_safe_and_shared_across_k_and_core_width():
    torch.manual_seed(991)
    before = torch.random.get_rng_state().clone()
    one = make_model(slots=1, width=16, seed=123)
    after = torch.random.get_rng_state().clone()
    assert torch.equal(before, after)
    eight_wider = make_model(slots=8, width=24, seed=123)
    assert LIGHT_ACTION_M2_GRAPH == "dsimple.light-action.m2.v1"
    for name in ("action_encoder", "write_key", "write_value", "write_norm",
                 "memory_gate", "action_query", "memory_key", "memory_value",
                 "transition", "score_head"):
        left = dict(getattr(one, name).named_parameters())
        right = dict(getattr(eight_wider, name).named_parameters())
        assert left.keys() == right.keys()
        for key in left:
            torch.testing.assert_close(left[key], right[key], rtol=0, atol=0)
    torch.testing.assert_close(one.write_queries[:1], eight_wider.write_queries[:1], rtol=0, atol=0)


@pytest.mark.parametrize("frozen", [False, True])
def test_frozen_pf_boundary_and_trainable_m2_gradients(frozen):
    model = make_model(slots=8, frozen=frozen).train()
    page, actions = sample()
    scores, memory = model.step(
        page, actions, model.initial_memory(), previous_actual_action=actions[0],
    )
    (scores.square().mean() + memory.square().mean()).backward()
    if frozen:
        assert not model.core.training
        assert all(parameter.grad is None for parameter in model.core.parameters())
    else:
        assert model.core.embedding.weight.grad is not None
        assert model.core.embedding.weight.grad.abs().sum() > 0
    for name in ("state_projection", "action_encoder", "write_key", "write_value",
                 "memory_key", "memory_value", "transition", "score_head"):
        gradients = [parameter.grad for parameter in getattr(model, name).parameters()]
        assert any(gradient is not None and gradient.abs().sum() > 0
                   for gradient in gradients), name
    assert model.write_queries.grad is not None and model.write_queries.grad.abs().sum() > 0


def test_no_silent_action_or_feedback_truncation():
    model = make_model()
    page, actions = sample()
    too_long = action(*range(33))
    with pytest.raises(ValueError, match="truncation is forbidden"):
        model.step(page, (too_long,), model.initial_memory())
    feedback = torch.ones(61, dtype=torch.long)
    with pytest.raises(ValueError, match="page and feedback token limit"):
        model.step(page, actions, model.initial_memory(), public_feedback=feedback)
