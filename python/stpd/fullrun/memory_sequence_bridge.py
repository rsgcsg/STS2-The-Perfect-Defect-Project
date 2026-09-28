"""Project a caller-verified observed stream into bounded M2 computation windows.

This is a pure research projection. The caller must establish source identity,
training purpose, use-ledger authority, and split eligibility before using a
window for training. A Human choice is never treated as an executed action.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch

from spireagent.json_boundary import BoundaryError

from ..canonical import semantic_hash
from ..models.dsimple_memory import ExperimentalDSimpleM2
from ..models.dsimple_sequence_training import (
    MAX_LEARN_STEPS,
    MAX_WINDOW_INPUT_TOKENS,
    MAX_WINDOW_STEPS,
    MemorySequenceStep,
    MemorySequenceWindow,
    validate_memory_window,
)
from .observed_input_sequence import ObservedInput, ObservedInputView
from .text_menu_inputs import project_text_menu_snapshot
from .token_inputs import encode_texts


@dataclass(frozen=True)
class MemoryBridgeDiagnostic:
    stream_id: str
    first_event_id: str
    reason: str


@dataclass(frozen=True)
class MemoryWindowSource:
    episode_id: str
    source_id: str
    stream_id: str
    first_reset_reason: str | None
    event_ids: tuple[str, ...]


@dataclass(frozen=True)
class MemoryBridgeResult:
    windows: tuple[MemorySequenceWindow, ...]
    sources: tuple[MemoryWindowSource, ...]
    diagnostics: tuple[MemoryBridgeDiagnostic, ...]


def project_memory_windows(
    view: ObservedInputView,
    tokenizer: object,
    model: ExperimentalDSimpleM2,
    *,
    burn_in_steps: int = 0,
) -> MemoryBridgeResult:
    """Keep only complete reset-origin prefixes; never reconstruct missing history.

    The caller supplies a verified view and a fixed tokenizer. This function
    neither loads artifacts nor makes a training admission decision. A segment
    may start at an explicit observation reset, not necessarily native run start.
    """
    if (
        not isinstance(view, ObservedInputView)
        or not view.source_id
        or not isinstance(view.inputs, tuple)
        or type(burn_in_steps) is not int
        or burn_in_steps < 0
        or not isinstance(model, ExperimentalDSimpleM2)
        or getattr(tokenizer, "truncation", None) is not None
        or getattr(tokenizer, "padding", None) is not None
    ):
        raise ValueError("invalid memory bridge input")

    windows: list[MemorySequenceWindow] = []
    sources: list[MemoryWindowSource] = []
    diagnostics: list[MemoryBridgeDiagnostic] = []
    segment: list[ObservedInput] = []
    broken = False
    seen: set[str] = set()
    last_sequence: int | None = None
    last_stream: str | None = None

    def diagnose(item: ObservedInput, reason: str) -> None:
        diagnostics.append(MemoryBridgeDiagnostic(item.stream_id, item.event_id, reason))

    def finish() -> None:
        nonlocal segment
        if not segment:
            return
        first = segment[0]
        if len(segment) > MAX_WINDOW_STEPS or len(segment) - burn_in_steps > MAX_LEARN_STEPS:
            diagnose(first, "window_limit_no_truncation")
            segment = []
            return
        episode_id = semantic_hash([view.source_id, first.stream_id, first.event_id])
        steps: list[MemorySequenceStep] = []
        loss: list[bool] = []
        input_tokens = 0
        try:
            for position, item in enumerate(segment):
                if not isinstance(item.snapshot, dict):
                    raise BoundaryError("memory_bridge", "missing_observation")
                public = project_text_menu_snapshot(item.snapshot)
                selected = item.selected_action_id
                if item.choice_mask != (selected is not None):
                    raise BoundaryError("memory_bridge", "choice_mask_mismatch")
                if selected is not None and public.action_ids.count(selected) != 1:
                    raise BoundaryError("memory_bridge", "choice_binding_mismatch")
                row = encode_texts(tokenizer, public.state_text, public.action_texts,
                                   max_tokens=model.core.max_tokens)
                input_tokens += len(row.state) + sum(len(action) for action in row.actions)
                if input_tokens > MAX_WINDOW_INPUT_TOKENS:
                    raise BoundaryError("memory_bridge", "window_input_token_limit")
                steps.append(MemorySequenceStep(
                    episode_id=episode_id,
                    position=position,
                    page=torch.tensor(row.state, dtype=torch.long,
                                      device=model.write_queries.device),
                    action_keys=public.action_ids,
                    actions=tuple(torch.tensor(action, dtype=torch.long,
                                               device=model.write_queries.device)
                                  for action in row.actions),
                    label_key=selected,
                    reset_before=position == 0,
                    # Neither a Human witness nor a chosen Agent label proves Commit.
                    previous_actual_action=None,
                    public_feedback=None,
                ))
                loss.append(position >= burn_in_steps and selected is not None)
            if not any(loss):
                diagnose(first, "no_learn_span_label")
            else:
                window = MemorySequenceWindow(
                    episode_id, tuple(steps), (True,) * len(steps),
                    tuple(index < burn_in_steps for index in range(len(steps))),
                    tuple(loss),
                )
                validate_memory_window(model, window)
                windows.append(window)
                sources.append(MemoryWindowSource(
                    episode_id, view.source_id, first.stream_id,
                    first.reset_reason, tuple(item.event_id for item in segment),
                ))
        except (BoundaryError, ValueError, TypeError, AttributeError) as error:
            diagnose(first, error.code if isinstance(error, BoundaryError)
                     else "window_contract_rejected")
        segment = []

    for item in view.inputs:
        if not isinstance(item, ObservedInput) or not item.stream_id or not item.event_id:
            raise ValueError("invalid observed input")
        if item.event_id in seen:
            raise ValueError("duplicate observed event")
        seen.add(item.event_id)
        if type(item.source_sequence) is not int or item.source_sequence < 1:
            raise ValueError("invalid observed event order")
        if item.stream_id != last_stream or item.reset_before:
            finish()
            broken = not item.reset_before
            last_sequence = None
        elif last_sequence is not None and item.source_sequence <= last_sequence:
            raise ValueError("invalid observed event order")
        last_stream = item.stream_id
        last_sequence = item.source_sequence
        if broken:
            diagnose(item, "reset_required")
            continue
        if not item.observation_mask or item.snapshot is None:
            finish()
            diagnose(item, "missing_observation")
            broken = True
            continue
        segment.append(item)
    finish()
    return MemoryBridgeResult(tuple(windows), tuple(sources), tuple(diagnostics))
