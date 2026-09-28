"""Synthetic M2 sequence computation checks; no real source admission."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest
import torch

from stpd.models.dsimple_memory import ExperimentalDSimpleM2
from stpd.models.dsimple_sequence_training import (
    MemorySequenceStep,
    MemorySequenceWindow,
    memory_sequence_loss,
    train_memory_window,
)
from stpd.models.token_core import ScratchShape, ScratchTokenCore


@pytest.fixture(autouse=True)
def single_cpu_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def model(
    *, reset: bool = False, width: int = 8, seed: int = 47, max_tokens: int = 16
) -> ExperimentalDSimpleM2:
    torch.manual_seed(seed)
    return ExperimentalDSimpleM2(
        ScratchTokenCore(
            ScratchShape(
                vocab_size=32,
                width=width,
                layers=1,
                heads=2,
                feedforward=2 * width,
                dropout=0,
                max_tokens=max_tokens,
            )
        ),
        slots=1,
        reset_each_step=reset,
    )


def tokens(*ids: int) -> torch.Tensor:
    return torch.tensor(ids, dtype=torch.long)


def window(
    *, cue: int = 3, burn: bool = False, previous: torch.Tensor | None = None, label: str = "B"
) -> MemorySequenceWindow:
    steps = (
        MemorySequenceStep(
            "episode", 0, tokens(1, cue), ("A", "B"), (tokens(8), tokens(9)), reset_before=True
        ),
        MemorySequenceStep(
            "episode",
            1,
            tokens(2, 4),
            ("A", "B"),
            (tokens(8), tokens(9)),
            label,
            previous_actual_action=previous,
        ),
    )
    return MemorySequenceWindow("episode", steps, (True, True), (burn, False), (False, True))


def test_unlabeled_observation_carries_gradient_into_later_label():
    subject = model()
    loss = memory_sequence_loss(subject, window())
    loss.backward()
    assert subject.core.embedding.weight.grad is not None
    assert subject.core.embedding.weight.grad[3].abs().sum() > 0
    assert subject.write_queries.grad is not None
    assert subject.write_queries.grad.abs().sum() > 0


def test_burn_in_reconstructs_memory_but_detaches_its_gradient():
    subject = model()
    loss = memory_sequence_loss(subject, window(burn=True))
    loss.backward()
    grad = subject.core.embedding.weight.grad
    assert grad is not None
    assert torch.count_nonzero(grad[3]) == 0
    assert grad[4].abs().sum() > 0
    assert memory_sequence_loss(subject, window(cue=3, burn=True)) != memory_sequence_loss(
        subject, window(cue=5, burn=True)
    )


def test_current_label_is_not_previous_executed_action_and_unknown_can_still_write():
    subject = model()
    with patch.object(subject, "advance", wraps=subject.advance) as advance:
        memory_sequence_loss(subject, window(previous=None))
    assert advance.call_count == 2  # Including the labeled step's internal write.
    assert all(call.kwargs["previous_actual_action"] is None for call in advance.call_args_list)
    # The labeled step is written by step(), with the optional actual action still absent.
    with patch.object(subject, "step", wraps=subject.step) as step:
        memory_sequence_loss(subject, window(previous=None))
    assert step.call_args.kwargs["previous_actual_action"] is None


def test_complete_catalog_binding_permutation_and_read_only_scoring():
    subject = model()
    base = window()
    final = base.steps[1]
    assert final is not None
    permuted = replace(final, action_keys=final.action_keys[::-1], actions=final.actions[::-1])
    other = replace(base, steps=(base.steps[0], permuted))
    torch.testing.assert_close(
        memory_sequence_loss(subject, other), memory_sequence_loss(subject, base)
    )
    with patch.object(subject.write_norm, "forward", wraps=subject.write_norm.forward) as write:
        memory_sequence_loss(subject, base)
        assert write.call_count == 2  # One write per observation, not per candidate.


@pytest.mark.parametrize(
    "invalid",
    [
        "duplicate_key",
        "wrong_label",
        "missing_label",
        "bad_final_token",
        "nonfinite_page",
        "bad_loss_mask",
        "burn_after_learn",
        "mid_episode",
        "second_reset",
        "wrong_episode",
    ],
)
def test_whole_window_preflight_rejects_bad_late_input_before_compute_or_update(invalid: str):
    subject = model()
    base = window()
    first, final = base.steps
    assert first is not None and final is not None
    if invalid == "duplicate_key":
        base = replace(base, steps=(first, replace(final, action_keys=("A", "A"))))
    elif invalid == "wrong_label":
        base = replace(base, steps=(first, replace(final, label_key="absent")))
    elif invalid == "missing_label":
        base = replace(base, loss_mask=(False, False))
    elif invalid == "bad_final_token":
        base = replace(base, steps=(first, replace(final, actions=(tokens(8), tokens(32)))))
    elif invalid == "nonfinite_page":
        base = replace(base, steps=(first, replace(final, page=torch.tensor([float("nan")]))))
    elif invalid == "bad_loss_mask":
        base = replace(base, loss_mask=(True, False))
    elif invalid == "burn_after_learn":
        base = replace(base, burn_in_mask=(False, True))
    elif invalid == "mid_episode":
        base = replace(base, steps=(replace(first, position=1, reset_before=False), final))
    elif invalid == "second_reset":
        base = replace(base, steps=(first, replace(final, reset_before=True)))
    else:
        base = replace(base, steps=(first, replace(final, episode_id="other")))
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    before = deepcopy(subject.state_dict())
    with patch.object(subject.core, "contextualize", wraps=subject.core.contextualize) as compute:
        with pytest.raises(ValueError):
            train_memory_window(subject, optimizer, base)
        compute.assert_not_called()
    assert all(torch.equal(before[key], after) for key, after in subject.state_dict().items())
    assert optimizer.state == {}


@pytest.mark.parametrize("invalid", ["unlabeled_action", "burn_in_action", "missing_page"])
def test_mandatory_page_and_actions_fail_before_any_compute_or_update(invalid: str):
    subject = model()
    base = window(burn=invalid == "burn_in_action")
    first, final = base.steps
    assert first is not None and final is not None
    changed = (
        replace(first, page=None)
        if invalid == "missing_page"
        else replace(first, actions=(None, tokens(9)))
    )
    broken = replace(base, steps=(changed, final))
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    before = deepcopy(subject.state_dict())
    with patch.object(subject.core, "contextualize", wraps=subject.core.contextualize) as compute:
        with pytest.raises(ValueError, match="token"):
            train_memory_window(subject, optimizer, broken)
        compute.assert_not_called()
    assert all(torch.equal(before[key], value) for key, value in subject.state_dict().items())
    assert optimizer.state == {}


def test_learn_span_limit_rejects_valid_full_catalog_before_compute():
    subject = model()
    first, final = window().steps
    assert first is not None and final is not None
    steps = (
        first,
        *(replace(first, position=index, reset_before=False) for index in range(1, 32)),
        replace(final, position=32),
    )
    oversized = MemorySequenceWindow(
        "episode", steps, (True,) * 33, (False,) * 33, (False,) * 32 + (True,)
    )
    with patch.object(subject.core, "contextualize", wraps=subject.core.contextualize) as compute:
        with pytest.raises(ValueError, match="learn span exceeds limit"):
            memory_sequence_loss(subject, oversized)
        compute.assert_not_called()


def test_aggregate_input_token_limit_rejects_complete_catalog_before_compute():
    subject = model(max_tokens=8192)
    long_action = tokens(*(6 for _ in range(8192)))
    first = MemorySequenceStep(
        "episode",
        0,
        tokens(1, 3),
        tuple(f"A{index}" for index in range(9)),
        (long_action,) * 9,
        "A0",
        reset_before=True,
    )
    oversized = MemorySequenceWindow("episode", (first,), (True,), (False,), (True,))
    with patch.object(subject.core, "contextualize", wraps=subject.core.contextualize) as compute:
        with pytest.raises(ValueError, match="input token limit exceeded"):
            memory_sequence_loss(subject, oversized)
        compute.assert_not_called()


def test_padding_label_masks_and_complete_prefix_rules():
    subject = model()
    base = window()
    padded = replace(
        base,
        steps=(None, *base.steps, None),
        valid_mask=(False, True, True, False),
        burn_in_mask=(False, True, False, False),
        loss_mask=(False, False, True, False),
    )
    assert torch.isfinite(memory_sequence_loss(subject, padded))
    final = base.steps[1]
    assert final is not None
    masked = replace(
        base, steps=(base.steps[0], replace(final, label_key="A")), loss_mask=(False, False)
    )
    with pytest.raises(ValueError, match="labels"):
        memory_sequence_loss(subject, masked)
    gap = replace(
        padded,
        steps=(base.steps[0], None, base.steps[1]),
        valid_mask=(True, False, True),
        burn_in_mask=(False, False, False),
        loss_mask=(False, False, True),
    )
    with pytest.raises(ValueError, match="contiguous"):
        memory_sequence_loss(subject, gap)


def test_present_but_masked_label_does_not_contribute_loss():
    subject = model()
    first, final = window().steps
    assert first is not None and final is not None
    middle = replace(final, position=1, label_key="A")
    last = replace(final, position=2)
    base = MemorySequenceWindow(
        "episode",
        (first, middle, last),
        (True, True, True),
        (False, False, False),
        (False, False, True),
    )
    changed = replace(base, steps=(first, replace(middle, label_key="B"), last))
    torch.testing.assert_close(
        memory_sequence_loss(subject, base), memory_sequence_loss(subject, changed), atol=0, rtol=0
    )


def test_window_length_guard_rejects_before_model_computation():
    subject = model()
    first, final = window().steps
    assert first is not None and final is not None
    steps = (
        first,
        *(replace(first, position=index, reset_before=False) for index in range(1, 64)),
        replace(final, position=64),
    )
    oversized = MemorySequenceWindow(
        "episode", steps, (True,) * 65, (True,) * 64 + (False,), (False,) * 64 + (True,)
    )
    with patch.object(subject.core, "contextualize", wraps=subject.core.contextualize) as compute:
        with pytest.raises(ValueError, match="invalid memory window"):
            memory_sequence_loss(subject, oversized)
        compute.assert_not_called()


def test_reset_at_episode_start_isolated_and_controls_train_independently():
    persistent, reset = model(), model(reset=True)
    assert all(
        torch.equal(a, b) and a.data_ptr() != b.data_ptr()
        for a, b in zip(persistent.parameters(), reset.parameters(), strict=True)
    )
    a = torch.optim.AdamW(persistent.parameters(), lr=0.001)
    b = torch.optim.AdamW(reset.parameters(), lr=0.001)
    before = deepcopy(persistent.state_dict())
    assert torch.isfinite(torch.tensor(train_memory_window(persistent, a, window())))
    assert torch.isfinite(torch.tensor(train_memory_window(reset, b, window())))
    assert any(
        not torch.equal(before[key], value) for key, value in persistent.state_dict().items()
    )
    assert any(not torch.equal(before[key], value) for key, value in reset.state_dict().items())
    assert a is not b and a.state and b.state
    with pytest.raises(ValueError, match="optimizer"):
        train_memory_window(persistent, b, window())
    # Each full-prefix call begins from M0; no previous episode state is carried in.
    torch.testing.assert_close(
        memory_sequence_loss(persistent, window()), memory_sequence_loss(persistent, window())
    )


def test_nonfinite_parameters_and_invalid_optimizer_settings_fail_before_update():
    subject = model()
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    with pytest.raises(ValueError, match="gradient clip"):
        train_memory_window(subject, optimizer, window(), gradient_clip=float("nan"))
    with torch.no_grad():
        subject.write_queries[0, 0] = float("nan")
    with pytest.raises(ValueError, match="nonfinite model parameter"):
        train_memory_window(subject, optimizer, window())
    assert optimizer.state == {}


def test_balanced_two_cue_task_only_persistent_memory_can_fit_history():
    """Small train-set mechanism check, not a policy or generalization claim."""

    def episode(cue: int, label: str) -> MemorySequenceWindow:
        first = MemorySequenceStep(
            "cue", 0, tokens(1, cue), ("A", "B"), (tokens(6), tokens(7)), reset_before=True
        )
        second = MemorySequenceStep(
            "cue",
            1,
            tokens(1, 5),
            ("A", "B"),
            (tokens(6), tokens(7)),
            label,
            previous_actual_action=tokens(8),
        )
        return MemorySequenceWindow(
            "cue", (first, second), (True, True), (False, False), (False, True)
        )

    examples = (episode(3, "A"), episode(4, "B"))
    persistent = model(width=16, seed=20260929)
    reset = model(width=16, reset=True, seed=20260929)
    assert all(
        torch.equal(a, b) and a.data_ptr() != b.data_ptr()
        for a, b in zip(persistent.parameters(), reset.parameters(), strict=True)
    )
    for subject in (persistent, reset):
        optimizer = torch.optim.Adam(subject.parameters(), lr=0.01)
        for _ in range(60):
            optimizer.zero_grad(set_to_none=True)
            loss = torch.stack([memory_sequence_loss(subject, item) for item in examples]).mean()
            loss.backward()
            optimizer.step()
    assert sum(memory_sequence_loss(persistent, item) < 0.1 for item in examples) == 2
    reset_losses = [memory_sequence_loss(reset, item) for item in examples]
    assert float(torch.stack(reset_losses).mean().detach()) >= 0.69
    with torch.no_grad():
        first_a = reset.advance(tokens(1, 3), reset.initial_memory(), reset_before=True)
        first_b = reset.advance(tokens(1, 4), reset.initial_memory(), reset_before=True)
        scores_a, _ = reset.step(
            tokens(1, 5), (tokens(6), tokens(7)), first_a, previous_actual_action=tokens(8)
        )
        scores_b, _ = reset.step(
            tokens(1, 5), (tokens(6), tokens(7)), first_b, previous_actual_action=tokens(8)
        )
    torch.testing.assert_close(scores_a, scores_b, atol=0, rtol=0)
