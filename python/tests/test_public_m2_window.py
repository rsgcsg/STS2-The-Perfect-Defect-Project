"""Synthetic public M2 numerical protocol; no source admission or model quality claim."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest
import torch

from spireagent.json_boundary import BoundaryError
from stpd.light_action_codec import BOS_ACT, EOS_ACT
from stpd.models.light_action_m2 import LightActionM2Scorer
from stpd.models.light_action_m2_training_data import LightActionM2TrainingStep
from stpd.models.public_m2_window import preflight_public_m2_window, train_public_m2_window
from stpd.models.token_core import ScratchShape, ScratchTokenCore


@pytest.fixture(autouse=True)
def one_cpu_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def model(*, slots: int = 1, reset_each_step: bool = False) -> LightActionM2Scorer:
    core = ScratchTokenCore(ScratchShape(32, 8, 1, 2, 16, 0.0, 64))
    return LightActionM2Scorer(
        core, max_action_bytes=16, initialization_seed=37,
        slots=slots, reset_each_step=reset_each_step,
    )


def action(byte: int) -> tuple[int, ...]:
    return (BOS_ACT, byte, EOS_ACT)


def steps(start: int, count: int) -> tuple[LightActionM2TrainingStep, ...]:
    return tuple(
        LightActionM2TrainingStep(
            position=position,
            page=(1 + position % 16, 3),
            action_ids=("a", "b"),
            byte_actions=(action(65), action(66)),
            target_action_id="a" if position % 2 else "b",
            previous_actual_action=None,
            reset_before=position == 0,
        )
        for position in range(start, start + count)
    )


def train_chain(
    subject: LightActionM2Scorer,
    optimizer: torch.optim.Optimizer,
    *,
    start: int,
    end: int,
    memory: torch.Tensor,
) -> torch.Tensor:
    for position in range(start, end, 8):
        result = train_public_m2_window(
            subject, optimizer, steps(position, min(8, end - position)), memory,
            chain_start=position == 0,
        )
        assert result.label_count == min(8, end - position)
        assert result.optimizer_updates == 1
        assert result.loss_sum == pytest.approx(result.loss_mean * result.label_count)
        assert result.memory.grad_fn is None
        memory = result.memory
    return memory


def test_65_decisions_continue_through_eight_step_windows_without_implicit_reset():
    subject = model()
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    initial = subject.initial_memory()
    with patch.object(subject, "step", wraps=subject.step) as called:
        final = train_chain(subject, optimizer, start=0, end=65, memory=initial)
    assert called.call_count == 65
    assert [call.kwargs["reset_before"] for call in called.call_args_list] == [True] + [False] * 64
    assert all(call.kwargs["previous_actual_action"] is None for call in called.call_args_list)
    assert all(call.kwargs["public_feedback"] is None for call in called.call_args_list)
    assert called.call_args_list[8].args[2].grad_fn is None
    assert not torch.equal(called.call_args_list[8].args[2], initial)
    assert torch.isfinite(final).all()


def test_later_loss_backpropagates_through_earlier_write_and_result_detaches():
    subject = model()
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    memories: list[torch.Tensor] = []
    real_step = subject.step
    real_loss = __import__(
        "stpd.models.public_m2_window", fromlist=["listwise_rank_loss"]
    ).listwise_rank_loss
    loss_calls = 0

    def capture_step(*args, **kwargs):
        scores, memory = real_step(*args, **kwargs)
        memory.retain_grad()
        memories.append(memory)
        return scores, memory

    def later_only_loss(scores, target):
        nonlocal loss_calls
        loss_calls += 1
        loss = real_loss(scores, target)
        return loss * 0 if loss_calls == 1 else loss

    with (
        patch.object(subject, "step", side_effect=capture_step),
        patch("stpd.models.public_m2_window.listwise_rank_loss", side_effect=later_only_loss),
    ):
        result = train_public_m2_window(
            subject, optimizer, steps(0, 2), subject.initial_memory(), chain_start=True
        )
    assert memories[0].grad is not None
    assert torch.count_nonzero(memories[0].grad) > 0
    assert result.memory.grad_fn is None
    assert result.memory.requires_grad is False


@pytest.mark.parametrize("slots,reset_each_step", [(1, False), (8, False), (8, True)])
def test_supported_slot_and_reset_variants(slots: int, reset_each_step: bool):
    subject = model(slots=slots, reset_each_step=reset_each_step)
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    memory = train_chain(subject, optimizer, start=0, end=9, memory=subject.initial_memory())
    assert memory.shape == (slots, 384)


def test_entire_window_rejected_before_mode_parameters_optimizer_or_memory_mutation():
    subject = model()
    subject.eval()
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    memory = subject.initial_memory()
    before = deepcopy(subject.state_dict())
    bad = replace(steps(0, 2)[1], previous_actual_action=action(67))
    with (
        patch.object(subject, "step", wraps=subject.step) as called,
        pytest.raises(BoundaryError, match="invalid_step_binding"),
    ):
        train_public_m2_window(
            subject, optimizer, (steps(0, 1)[0], bad), memory, chain_start=True
        )
    called.assert_not_called()
    assert subject.training is False
    assert optimizer.state == {}
    assert all(torch.equal(before[key], value) for key, value in subject.state_dict().items())
    assert torch.equal(memory, subject.initial_memory())


def test_no_silent_window_split_or_label_omission():
    subject = model()
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    memory = subject.initial_memory()
    with pytest.raises(BoundaryError, match="invalid_window"):
        train_public_m2_window(subject, optimizer, steps(0, 9), memory, chain_start=True)
    with pytest.raises(BoundaryError, match="window_input_budget_exceeded"):
        train_public_m2_window(
            subject, optimizer, steps(0, 2), memory, chain_start=True,
            max_window_tokens=15,
        )
    with pytest.raises(BoundaryError, match="missing_target"):
        train_public_m2_window(
            subject, optimizer, (replace(steps(0, 1)[0], target_action_id=None),),
            memory, chain_start=True,
        )
    assert optimizer.state == {}


def test_98304_budget_counts_every_candidate_without_truncation():
    subject = model()
    full = (BOS_ACT, *(65,) * 14, EOS_ACT)
    short = (BOS_ACT, *(65,) * 12, EOS_ACT)
    row = LightActionM2TrainingStep(
        position=0, page=(1, 2),
        action_ids=tuple(f"a{index}" for index in range(6144)),
        byte_actions=(full,) * 6143 + (short,),
        target_action_id="a0", previous_actual_action=None, reset_before=True,
    )
    assert len(row.page) + sum(map(len, row.byte_actions)) == 98_304
    with patch.object(subject, "_validate_action", wraps=subject._validate_action) as checked:
        assert preflight_public_m2_window(
            subject, (row,), subject.initial_memory(), chain_start=True,
            max_window_steps=4, max_window_tokens=98_304,
        ) == (0,)
    assert checked.call_count == len(row.action_ids)
    with pytest.raises(BoundaryError, match="window_input_budget_exceeded"):
        preflight_public_m2_window(
            subject, (row,), subject.initial_memory(), chain_start=True,
            max_window_steps=4, max_window_tokens=98_303,
        )
    with pytest.raises(BoundaryError, match="invalid_window"):
        preflight_public_m2_window(
            subject, (row,), subject.initial_memory(), chain_start=True,
            max_window_steps=4, max_window_tokens=98_305,
        )


def test_eval_preflight_accepts_unlabeled_row_but_preserves_binding_checks():
    subject = model()
    row = replace(steps(0, 1)[0], target_action_id=None)
    assert preflight_public_m2_window(
        subject, (row,), subject.initial_memory(), chain_start=True,
        require_targets=False,
    ) == (None,)
    with pytest.raises(BoundaryError, match="target_binding_mismatch"):
        preflight_public_m2_window(
            subject, (replace(row, target_action_id="missing"),),
            subject.initial_memory(), chain_start=True, require_targets=False,
        )


def test_target_changes_loss_but_cannot_change_window_memory_write():
    first = model()
    second = model()
    second.load_state_dict(deepcopy(first.state_dict()))
    first_optimizer = torch.optim.AdamW(first.parameters(), lr=0.001)
    second_optimizer = torch.optim.AdamW(second.parameters(), lr=0.001)
    row = steps(0, 1)[0]
    first_result = train_public_m2_window(
        first, first_optimizer, (row,), first.initial_memory(), chain_start=True
    )
    second_result = train_public_m2_window(
        second, second_optimizer, (replace(row, target_action_id="a"),),
        second.initial_memory(), chain_start=True,
    )
    assert torch.equal(first_result.memory, second_result.memory)
    assert first_result.loss_mean != second_result.loss_mean


def test_scratch_parameter_and_optimizer_coverage_are_preflighted():
    subject = model()
    optimizer = torch.optim.AdamW(subject.parameters(), lr=0.001)
    first_parameter = next(subject.parameters())
    first_parameter.requires_grad_(False)
    with pytest.raises(BoundaryError, match="frozen_model_parameter"):
        train_public_m2_window(
            subject, optimizer, steps(0, 1), subject.initial_memory(), chain_start=True
        )
    first_parameter.requires_grad_(True)
    partial_optimizer = torch.optim.AdamW(list(subject.parameters())[1:], lr=0.001)
    with pytest.raises(BoundaryError, match="optimizer_model_mismatch"):
        train_public_m2_window(
            subject, partial_optimizer, steps(0, 1), subject.initial_memory(), chain_start=True
        )
    assert optimizer.state == {}
    assert partial_optimizer.state == {}


def test_continuation_matches_cloned_checkpoint_at_window_boundary():
    uninterrupted = model()
    optimizer = torch.optim.AdamW(uninterrupted.parameters(), lr=0.001)
    boundary_memory = train_chain(
        uninterrupted, optimizer, start=0, end=8, memory=uninterrupted.initial_memory()
    )
    resumed = model()
    resumed.load_state_dict(deepcopy(uninterrupted.state_dict()))
    resumed_optimizer = torch.optim.AdamW(resumed.parameters(), lr=0.001)
    resumed_optimizer.load_state_dict(deepcopy(optimizer.state_dict()))
    saved_memory = boundary_memory.detach().clone()
    final_a = train_chain(uninterrupted, optimizer, start=8, end=17, memory=boundary_memory)
    final_b = train_chain(resumed, resumed_optimizer, start=8, end=17, memory=saved_memory)
    assert torch.equal(final_a, final_b)
    assert all(
        torch.equal(value, resumed.state_dict()[key])
        for key, value in uninterrupted.state_dict().items()
    )
