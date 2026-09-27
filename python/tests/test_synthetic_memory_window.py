"""Synthetic windows test bookkeeping only, not training or game evidence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
import torch

from stpd.models.experimental_observation_memory_b import ExperimentalObservationMemoryB
from stpd.models.synthetic_memory_window import (
    SyntheticMemoryStep,
    build_synthetic_windows,
    make_reward_episodes,
    synthetic_window_loss,
)
from stpd.models.token_core import ScratchShape, ScratchTokenCore


@pytest.fixture(autouse=True)
def single_cpu_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def model(*, reset: bool = False) -> ExperimentalObservationMemoryB:
    torch.manual_seed(47)
    return ExperimentalObservationMemoryB(ScratchTokenCore(ScratchShape(
        vocab_size=16, width=8, layers=1, heads=2, feedforward=16,
        dropout=0, max_tokens=16,
    )), hidden_width=8, reset_each_step=reset)


def test_two_fact_episodes_identical_final_input_and_exact_bound_labels():
    episodes = make_reward_episodes()
    assert len(episodes) >= 4
    final = episodes[0][-1]
    assert all(torch.equal(episode[-1].observation, final.observation)
               for episode in episodes)
    assert all(episode[-1].action_keys == final.action_keys == ("A", "B")
               for episode in episodes)
    assert all(all(torch.equal(a, b) for a, b in zip(
        episode[-1].actions, final.actions, strict=True,
    )) for episode in episodes)
    assert episodes[0][1].observation.tolist() == episodes[1][1].observation.tolist()
    assert episodes[0][-1].label_key != episodes[1][-1].label_key
    assert episodes[2][0].observation.tolist() == episodes[3][0].observation.tolist()
    assert episodes[2][-1].label_key != episodes[3][-1].label_key
    assert all(step.label_key is None for episode in episodes for step in episode[:2])
    with pytest.raises(ValueError, match="binding"):
        replace(final, action_keys=("A", "A"))
    with pytest.raises(ValueError, match="binding"):
        replace(final, label_key="missing")


def test_burn_padding_detach_masks_and_label_metadata_do_not_enter_graph():
    episode = make_reward_episodes()[0]
    windows = build_synthetic_windows((episode,), burn_in_steps=3, learn_steps=2)
    assert len(windows) == 1
    window = windows[0]
    assert window.valid_mask == (False, True, True, True, False)
    assert window.burn_in_mask == (False, True, True, False, False)
    assert window.loss_mask == (False, False, False, True, False)
    subject = model()
    loss, hidden = synthetic_window_loss(subject, window)
    assert torch.isfinite(loss) and hidden.requires_grad
    loss.backward()
    assert subject.memory_projection.weight.grad is not None

    flipped = replace(episode[-1], label_key="A" if episode[-1].label_key == "B" else "B")
    changed = replace(window, steps=(*window.steps[:3], flipped, None))
    other_loss, other_hidden = synthetic_window_loss(subject, changed)
    torch.testing.assert_close(other_hidden, hidden, atol=0, rtol=0)
    assert not torch.equal(other_loss, loss)

    with pytest.raises(ValueError, match="masks disagree"):
        synthetic_window_loss(subject, replace(window, loss_mask=(True, *window.loss_mask[1:])))
    with pytest.raises(ValueError, match="episode"):
        build_synthetic_windows(((episode[0], replace(episode[1], episode_id="wrong"),
                                  episode[2]),), burn_in_steps=2, learn_steps=1)


def test_persistent_and_reset_controls_train_as_separate_equal_start_instances():
    window = build_synthetic_windows(
        (make_reward_episodes()[0],), burn_in_steps=2, learn_steps=1,
    )[0]
    persistent, reset = model(), model(reset=True)
    reset.load_state_dict(deepcopy(persistent.state_dict()), strict=True)
    assert all(torch.equal(a, b) and a.data_ptr() != b.data_ptr()
               for a, b in zip(persistent.parameters(), reset.parameters(), strict=True))
    before = deepcopy(persistent.state_dict())
    for subject in (persistent, reset):
        optimizer = torch.optim.SGD(subject.parameters(), lr=0.01)
        optimizer.zero_grad(set_to_none=True)
        loss, _ = synthetic_window_loss(subject, window)
        assert torch.isfinite(loss)
        loss.backward()
        optimizer.step()
    assert any(not torch.equal(before[key], persistent.state_dict()[key]) for key in before)
    assert any(not torch.equal(before[key], reset.state_dict()[key]) for key in before)
    assert all(a.data_ptr() != b.data_ptr()
               for a, b in zip(persistent.parameters(), reset.parameters(), strict=True))


def test_window_step_binding_is_not_inferred_from_action_position():
    base = make_reward_episodes()[0][-1]
    reversed_step = SyntheticMemoryStep(
        base.episode_id, base.position, base.observation,
        base.action_keys[::-1], base.actions[::-1], base.label_key, base.reset_before,
    )
    assert reversed_step.action_keys.index(base.label_key) == 1 - base.action_keys.index(
        base.label_key,
    )
