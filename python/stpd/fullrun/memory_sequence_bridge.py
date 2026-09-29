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
    MemorySequenceEpisode,
    MemorySequenceStep,
    MemorySequenceWindow,
    _validate_step,
    validate_memory_window,
)
from .observed_input_sequence import ObservedInput, ObservedInputView
from .text_menu_inputs import INPUT_PROFILE, SNAPSHOT_SCHEMA, project_text_menu_snapshot
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


@dataclass(frozen=True)
class MemoryEpisodeBridgeResult:
    episodes: tuple[MemorySequenceEpisode, ...]
    sources: tuple[MemoryEpisodeSource, ...]
    diagnostics: tuple[MemoryBridgeDiagnostic, ...]


@dataclass(frozen=True)
class MemoryEpisodeSource:
    episode_id: str
    source_id: str
    stream_id: str
    first_reset_reason: str | None
    event_ids: tuple[str, ...]
    step_event_ids: tuple[str, ...]
    settling_event_ids: tuple[str, ...]


def _validate_bridge_input(
    view: ObservedInputView, tokenizer: object, model: ExperimentalDSimpleM2,
) -> None:
    if (
        not isinstance(view, ObservedInputView)
        or not view.source_id
        or not isinstance(view.inputs, tuple)
        or not isinstance(model, ExperimentalDSimpleM2)
        or getattr(tokenizer, "truncation", None) is not None
        or getattr(tokenizer, "padding", None) is not None
    ):
        raise ValueError("invalid memory bridge input")


def _segments(
    view: ObservedInputView,
) -> tuple[tuple[tuple[ObservedInput, ...], ...], list[MemoryBridgeDiagnostic]]:
    """Share exact event-order, reset and missing-observation boundaries."""
    segments: list[tuple[ObservedInput, ...]] = []
    diagnostics: list[MemoryBridgeDiagnostic] = []
    segment: list[ObservedInput] = []
    broken = False
    seen: set[str] = set()
    last_sequence: int | None = None
    last_stream: str | None = None

    def diagnose(item: ObservedInput, reason: str) -> None:
        diagnostics.append(MemoryBridgeDiagnostic(item.stream_id, item.event_id, reason))

    for item in view.inputs:
        if not isinstance(item, ObservedInput) or not item.stream_id or not item.event_id:
            raise ValueError("invalid observed input")
        if item.event_id in seen:
            raise ValueError("duplicate observed event")
        seen.add(item.event_id)
        if type(item.source_sequence) is not int or item.source_sequence < 1:
            raise ValueError("invalid observed event order")
        if item.stream_id != last_stream or item.reset_before:
            if segment:
                segments.append(tuple(segment))
                segment = []
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
            if segment:
                segments.append(tuple(segment))
                segment = []
            diagnose(item, "missing_observation")
            broken = True
            continue
        segment.append(item)
    if segment:
        segments.append(tuple(segment))
    return tuple(segments), diagnostics


def _project_segment(
    view: ObservedInputView, tokenizer: object, model: ExperimentalDSimpleM2,
    segment: tuple[ObservedInput, ...], *, max_input_tokens: int, limit_code: str,
) -> tuple[str, tuple[MemorySequenceStep, ...]]:
    first = segment[0]
    episode_id = semantic_hash([view.source_id, first.stream_id, first.event_id])
    steps: list[MemorySequenceStep] = []
    input_tokens = 0
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
        step = MemorySequenceStep(
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
        )
        input_tokens += _validate_step(model, step, episode_id, position)
        if input_tokens > max_input_tokens:
            raise BoundaryError("memory_bridge", limit_code)
        steps.append(step)
    return episode_id, tuple(steps)


def _source(
    view: ObservedInputView, segment: tuple[ObservedInput, ...], episode_id: str,
) -> MemoryWindowSource:
    first = segment[0]
    return MemoryWindowSource(
        episode_id, view.source_id, first.stream_id,
        first.reset_reason, tuple(item.event_id for item in segment),
    )


def _episode_source(
    view: ObservedInputView, segment: tuple[ObservedInput, ...],
    projected: tuple[ObservedInput, ...], episode_id: str,
) -> MemoryEpisodeSource:
    step_ids = tuple(item.event_id for item in projected)
    step_set = set(step_ids)
    first = segment[0]
    return MemoryEpisodeSource(
        episode_id, view.source_id, first.stream_id, first.reset_reason,
        tuple(item.event_id for item in segment), step_ids,
        tuple(item.event_id for item in segment if item.event_id not in step_set),
    )


def _session_identity(item: ObservedInput) -> tuple[str, str]:
    snapshot = item.snapshot
    session = snapshot.get("session") if isinstance(snapshot, dict) else None
    if not isinstance(session, dict):
        raise BoundaryError("memory_bridge", "settling_identity_unverified")
    runtime_id = session.get("runtime_instance_id")
    fingerprint = session.get("environment_fingerprint")
    if (not isinstance(runtime_id, str) or not runtime_id
            or not isinstance(fingerprint, str) or not fingerprint):
        raise BoundaryError("memory_bridge", "settling_identity_unverified")
    return runtime_id, fingerprint


def _projectable_episode_items(
    segment: tuple[ObservedInput, ...], max_settling_events: int,
) -> tuple[ObservedInput, ...]:
    settling = tuple(item for item in segment if isinstance(item.snapshot, dict)
                     and item.snapshot.get("status") == "settling")
    if not settling:
        return segment
    settling_ids = {item.event_id for item in settling}
    if max_settling_events == 0:
        raise BoundaryError("memory_bridge", "settling_requires_opt_in")
    if len(settling) > max_settling_events:
        raise BoundaryError("memory_bridge", "episode_settling_limit")
    if segment[0].event_id in settling_ids or segment[-1].event_id in settling_ids:
        raise BoundaryError("memory_bridge", "settling_needs_interactive_neighbors")
    identity = _session_identity(segment[0])
    for item in segment:
        if _session_identity(item) != identity:
            raise BoundaryError("memory_bridge", "settling_identity_drift")
        if item.event_id in settling_ids:
            snapshot = item.snapshot
            assert isinstance(snapshot, dict)
            completeness = snapshot.get("completeness")
            catalog = snapshot.get("menu_actions")
            policy = snapshot.get("information_policy")
            interaction = snapshot.get("interaction")
            if (
                snapshot.get("schema") != SNAPSHOT_SCHEMA
                or snapshot.get("input_profile") != INPUT_PROFILE
                or snapshot.get("status") != "settling"
                or not isinstance(policy, dict)
                or policy.get("includes_hidden_information") is not False
                or not isinstance(completeness, dict)
                or completeness.get("status") != "complete"
                or not isinstance(interaction, dict)
                or interaction.get("capabilities") != []
                or not isinstance(catalog, dict)
                or catalog.get("status") != "unavailable"
                or catalog.get("actions") != []
                or type(catalog.get("materialized_count")) is not int
                or catalog["materialized_count"] != 0
                or type(catalog.get("total_count")) is not int
                or catalog["total_count"] < 0
                or not isinstance(catalog.get("ordering_semantics"), str)
                or not catalog["ordering_semantics"]
                or item.choice_mask is not False
                or item.selected_action_id is not None
                or item.delivery_mask is not False
                or item.causal_successor_mask is not False
            ):
                raise BoundaryError("memory_bridge", "invalid_settling_observation")
    return tuple(item for item in segment if item.event_id not in settling_ids)


def _ordered_diagnostics(
    view: ObservedInputView, diagnostics: list[MemoryBridgeDiagnostic],
) -> tuple[MemoryBridgeDiagnostic, ...]:
    event_order = {item.event_id: index for index, item in enumerate(view.inputs)}
    return tuple(sorted(diagnostics, key=lambda item: event_order[item.first_event_id]))


def project_memory_windows(
    view: ObservedInputView,
    tokenizer: object,
    model: ExperimentalDSimpleM2,
    *,
    burn_in_steps: int = 0,
) -> MemoryBridgeResult:
    """Keep only complete reset-origin prefixes; never reconstruct missing history."""
    _validate_bridge_input(view, tokenizer, model)
    if type(burn_in_steps) is not int or burn_in_steps < 0:
        raise ValueError("invalid memory bridge input")
    windows: list[MemorySequenceWindow] = []
    sources: list[MemoryWindowSource] = []
    segments, diagnostics = _segments(view)
    for segment in segments:
        first = segment[0]
        if len(segment) > MAX_WINDOW_STEPS or len(segment) - burn_in_steps > MAX_LEARN_STEPS:
            diagnostics.append(MemoryBridgeDiagnostic(first.stream_id, first.event_id,
                                                      "window_limit_no_truncation"))
            continue
        try:
            episode_id, steps = _project_segment(
                view, tokenizer, model, segment,
                max_input_tokens=MAX_WINDOW_INPUT_TOKENS,
                limit_code="window_input_token_limit",
            )
            loss = tuple(index >= burn_in_steps and step.label_key is not None
                         for index, step in enumerate(steps))
            if not any(loss):
                diagnostics.append(MemoryBridgeDiagnostic(first.stream_id, first.event_id,
                                                          "no_learn_span_label"))
                continue
            window = MemorySequenceWindow(
                episode_id, steps, (True,) * len(steps),
                tuple(index < burn_in_steps for index in range(len(steps))), loss,
            )
            validate_memory_window(model, window)
            windows.append(window)
            sources.append(_source(view, segment, episode_id))
        except (BoundaryError, ValueError, TypeError, AttributeError) as error:
            diagnostics.append(MemoryBridgeDiagnostic(
                first.stream_id, first.event_id,
                error.code if isinstance(error, BoundaryError) else "window_contract_rejected",
            ))
    return MemoryBridgeResult(tuple(windows), tuple(sources),
                              _ordered_diagnostics(view, diagnostics))


def project_memory_episodes(
    view: ObservedInputView,
    tokenizer: object,
    model: ExperimentalDSimpleM2,
    *,
    max_observations: int,
    max_input_tokens: int,
    max_settling_events: int = 0,
) -> MemoryEpisodeBridgeResult:
    """Project bounded episodes; opted-in verified settling writes no model state."""
    _validate_bridge_input(view, tokenizer, model)
    if (type(max_observations) is not int or max_observations <= 0
            or type(max_input_tokens) is not int or max_input_tokens <= 0
            or type(max_settling_events) is not int or max_settling_events < 0):
        raise ValueError("invalid memory episode bridge limits")
    episodes: list[MemorySequenceEpisode] = []
    sources: list[MemoryEpisodeSource] = []
    segments, diagnostics = _segments(view)
    for segment in segments:
        first = segment[0]
        try:
            projected = _projectable_episode_items(segment, max_settling_events)
            if len(projected) > max_observations:
                raise BoundaryError("memory_bridge", "episode_observation_limit")
            episode_id, steps = _project_segment(
                view, tokenizer, model, projected,
                max_input_tokens=max_input_tokens,
                limit_code="episode_input_token_limit",
            )
            if not any(step.label_key is not None for step in steps):
                diagnostics.append(MemoryBridgeDiagnostic(first.stream_id, first.event_id,
                                                          "no_learn_span_label"))
                continue
            episodes.append(MemorySequenceEpisode(episode_id, steps))
            sources.append(_episode_source(view, segment, projected, episode_id))
        except (BoundaryError, ValueError, TypeError, AttributeError) as error:
            diagnostics.append(MemoryBridgeDiagnostic(
                first.stream_id, first.event_id,
                error.code if isinstance(error, BoundaryError) else "episode_contract_rejected",
            ))
    return MemoryEpisodeBridgeResult(tuple(episodes), tuple(sources),
                                     _ordered_diagnostics(view, diagnostics))
