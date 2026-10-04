"""Synthetic shared LightActionM2 training kernel tests; no source admission."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest
import torch

from spireagent.json_boundary import BoundaryError
from stpd.light_action_codec import BOS_ACT, EOS_ACT
from stpd.models.light_action_m2 import LightActionM2Scorer
from stpd.models.light_action_m2_training_data import (
    LightActionM2TrainingEpisode,
    LightActionM2TrainingStep,
)
from stpd.models.light_action_m2_training_kernel import (
    train_light_action_m2_training_episode,
)
from stpd.models.token_core import ScratchShape, ScratchTokenCore


@pytest.fixture(autouse=True)
def one_cpu_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def model(*, reset: bool = False) -> LightActionM2Scorer:
    core = ScratchTokenCore(ScratchShape(32, 8, 1, 2, 16, 0.0, 64))
    return LightActionM2Scorer(
        core, max_action_bytes=16, initialization_seed=37, slots=1, reset_each_step=reset
    )


def action(byte: int) -> tuple[int, ...]:
    return (BOS_ACT, byte, EOS_ACT)


def step(
    position: int, *, target: str | None, reset: bool | None = None
) -> LightActionM2TrainingStep:
    return LightActionM2TrainingStep(
        position=position,
        page=(1 + position, 3),
        action_ids=("a", "b"),
        byte_actions=(action(65), action(66)),
        target_action_id=target,
        previous_actual_action=None if position == 0 else action(64 + position),
        reset_before=(position == 0) if reset is None else reset,
    )


def episode(
    *targets: str | None, max_chunk_steps: int = 2, reset: bool = False
) -> LightActionM2TrainingEpisode:
    return LightActionM2TrainingEpisode(
        episode_id="fixture-episode",
        slots=1,
        reset_each_step=reset,
        steps=tuple(step(index, target=target) for index, target in enumerate(targets)),
        max_episode_steps=8,
        max_chunk_steps=max_chunk_steps,
        max_episode_tokens=4096,
        max_chunk_tokens=2048,
    )


def test_unlabeled_interactive_page_advances_memory_and_trains_labeled_page():
    subject = model()
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    with (
        patch.object(subject, "step", wraps=subject.step) as model_step,
        patch.object(optimizer, "step", wraps=optimizer.step) as optimizer_step,
    ):
        loss = train_light_action_m2_training_episode(
            subject,
            optimizer,
            episode(None, "b"),
        )
    assert torch.isfinite(torch.tensor(loss))
    assert model_step.call_count == 2
    assert optimizer_step.call_count == 1
    assert model_step.call_args_list[0].kwargs["previous_actual_action"] is None


def test_unlabeled_only_episode_fails_before_model_or_optimizer_mutation():
    subject = model()
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    before = deepcopy(subject.state_dict())
    with patch.object(subject, "step", wraps=subject.step) as model_step:
        with pytest.raises(BoundaryError, match="episode_has_no_supervised_label"):
            train_light_action_m2_training_episode(
                subject,
                optimizer,
                episode(None, None),
            )
        model_step.assert_not_called()
    assert optimizer.state == {}
    assert all(torch.equal(before[key], value) for key, value in subject.state_dict().items())


def test_unlabeled_only_chunk_skips_optimizer_and_memory_detaches_at_boundary():
    subject = model()
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    # The first chunk reconstructs memory but has no objective; the second trains.
    with (
        patch.object(subject, "step", wraps=subject.step) as model_step,
        patch.object(optimizer, "step", wraps=optimizer.step) as optimizer_step,
    ):
        train_light_action_m2_training_episode(
            subject,
            optimizer,
            episode(None, "b", max_chunk_steps=1),
        )
    assert model_step.call_count == 2
    assert optimizer_step.call_count == 1
    assert model_step.call_args_list[0].args[2].grad_fn is None
    assert model_step.call_args_list[1].args[2].grad_fn is None


def test_mixed_label_chunk_carries_graph_then_detaches_before_next_chunk():
    subject = model()
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    with patch.object(subject, "step", wraps=subject.step) as model_step:
        train_light_action_m2_training_episode(
            subject,
            optimizer,
            episode("a", None, "b", max_chunk_steps=2),
        )
    memories = [call.args[2] for call in model_step.call_args_list]
    assert memories[0].grad_fn is None
    assert memories[1].grad_fn is not None
    assert memories[2].grad_fn is None


def test_late_invalid_row_is_preflighted_before_any_compute_or_update():
    subject = model()
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    valid_first, invalid_last = episode("a", "b").steps
    invalid = replace(invalid_last, byte_actions=((BOS_ACT, 66.0, EOS_ACT), action(67)))
    malformed = replace(episode("a", "b"), steps=(valid_first, invalid))
    before = deepcopy(subject.state_dict())
    with patch.object(subject, "step", wraps=subject.step) as model_step:
        with pytest.raises(BoundaryError, match="invalid_step_binding"):
            train_light_action_m2_training_episode(subject, optimizer, malformed)
        model_step.assert_not_called()
    assert optimizer.state == {}
    assert all(torch.equal(before[key], value) for key, value in subject.state_dict().items())


def test_reset_must_match_explicit_episode_identity():
    subject = model(reset=True)
    with pytest.raises(BoundaryError, match="invalid_episode"):
        train_light_action_m2_training_episode(
            subject,
            torch.optim.AdamW(subject.parameters(), lr=0.001),
            episode("a", reset=False),
        )
