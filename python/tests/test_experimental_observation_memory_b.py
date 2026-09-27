"""CPU graph checks for an unregistered synthetic observation-only prototype."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

import pytest
import torch
from torch import nn

from stpd.models.experimental_observation_memory_b import ExperimentalObservationMemoryB
from stpd.models.losses import listwise_rank_loss
from stpd.models.stage1a import BTokenScorer
from stpd.models.synthetic_memory_window import make_reward_episodes
from stpd.models.token_core import ScratchShape, ScratchTokenCore, TokenCore


@pytest.fixture(autouse=True)
def single_cpu_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def tiny_core(*, dropout: float = 0, max_tokens: int = 32) -> ScratchTokenCore:
    torch.manual_seed(27)
    return ScratchTokenCore(ScratchShape(
        vocab_size=16, width=8, layers=1, heads=2, feedforward=16,
        dropout=dropout, max_tokens=max_tokens,
    ))


def test_one_summary_one_update_candidate_readonly_and_linear_branches():
    model = ExperimentalObservationMemoryB(tiny_core()).eval()
    observation = torch.tensor([8])
    actions = tuple(torch.tensor([token]) for token in (9, 10, 11, 12))
    with (patch.object(model.core, "read_last_query", wraps=model.core.read_last_query) as summary,
          patch.object(model.gru, "forward", wraps=model.gru.forward) as update,
          patch.object(model.core, "_project_attention",
                       wraps=model.core._project_attention) as project,
          patch.object(model.core, "_branch_layer",
                       wraps=model.core._branch_layer) as branch):
        scores, hidden = model.step(observation, actions[:2], model.initial_hidden())
        assert scores.shape == (2,)
        assert summary.call_count == update.call_count == 1
        # One shared projection plus one projection per candidate, per layer.
        assert project.call_count == 3 and branch.call_count == 2
        model.score(observation, actions, hidden)
        assert summary.call_count == update.call_count == 1
        assert project.call_count == 8 and branch.call_count == 6
    original = model.score(observation, actions[:2], hidden)
    torch.testing.assert_close(model.score(observation, actions[:2][::-1], hidden),
                               original.flip(0))
    torch.testing.assert_close(model.score(observation, (actions[0], actions[2]), hidden)[0],
                               original[0])
    assert sum(isinstance(module, nn.TransformerEncoder) for module in model.modules()) == 1


def test_history_can_affect_only_memory_reset_matches_current_and_gradients_flow():
    left, right = make_reward_episodes()[:2]  # Same B, different A and opposite labels.
    assert left[-1].label_key != right[-1].label_key
    assert torch.equal(left[-1].observation, right[-1].observation)
    assert all(torch.equal(a, b) for a, b in zip(
        left[-1].actions, right[-1].actions, strict=True,
    ))
    model = ExperimentalObservationMemoryB(tiny_core())
    reset = ExperimentalObservationMemoryB(tiny_core(), reset_each_step=True)
    reset.load_state_dict(deepcopy(model.state_dict()), strict=True)
    assert all(torch.equal(a, b) for a, b in zip(
        model.parameters(), reset.parameters(), strict=True,
    ))
    assert all(a.data_ptr() != b.data_ptr() for a, b in zip(
        model.parameters(), reset.parameters(), strict=True,
    ))

    def run(episode, instance):
        hidden = instance.initial_hidden()
        for step in episode[:2]:
            hidden = instance.advance(step.observation, hidden,
                                      reset_before=step.reset_before)
        return instance.step(episode[-1].observation, episode[-1].actions, hidden)

    left_scores, left_hidden = run(left, model)
    right_scores, right_hidden = run(right, model)
    assert not torch.allclose(left_hidden, right_hidden)
    assert not torch.allclose(left_scores, right_scores)
    left_reset, _ = run(left, reset)
    right_reset, _ = run(right, reset)
    torch.testing.assert_close(left_reset, right_reset, atol=0, rtol=0)

    prior = model.advance(left[0].observation, model.initial_hidden(), reset_before=True)
    prior.retain_grad()
    final, _ = model.step(left[-1].observation, left[-1].actions, prior)
    listwise_rank_loss(final, 0).backward()
    assert prior.grad is not None and prior.grad.abs().sum() > 0
    assert model.gru.weight_hh.grad is not None and model.gru.weight_hh.grad.abs().sum() > 0
    assert (model.memory_projection.weight.grad is not None
            and model.memory_projection.weight.grad.abs().sum() > 0)


def test_positive_dropout_and_prefix_capacity_fail_closed_without_changing_m0():
    core = tiny_core(dropout=0.1)
    old = BTokenScorer(core, packed=True).eval()
    state, actions = torch.tensor([1, 2]), (torch.tensor([9]), torch.tensor([10]))
    before_keys = tuple(old.state_dict())
    before = old(state, actions)
    with pytest.raises(ValueError, match="zero-dropout"):
        ExperimentalObservationMemoryB(core)
    with pytest.raises(ValueError, match="zero dropout"):
        core.read_action_queries_from_shared(core.embed_tokens(state), actions, old.readout)
    torch.testing.assert_close(old(state, actions), before, atol=0, rtol=0)
    assert tuple(old.state_dict()) == before_keys

    zero_core = tiny_core().eval()
    query = torch.randn(zero_core.width)
    reference = TokenCore.read_action_queries(zero_core, state, actions, query)
    torch.testing.assert_close(zero_core.read_action_queries(state, actions, query),
                               reference, atol=1e-6, rtol=1e-6)

    memory = ExperimentalObservationMemoryB(tiny_core(max_tokens=5))
    with patch.object(memory.core, "read_last_query", wraps=memory.core.read_last_query) as call:
        with pytest.raises(ValueError, match="token limit"):
            memory.score(state, (torch.tensor([9]), torch.tensor([10, 11])),
                         memory.initial_hidden())
        call.assert_not_called()
