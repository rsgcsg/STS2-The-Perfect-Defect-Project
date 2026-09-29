"""Project a caller-verified observed stream into bounded M2 computation windows.

This is a pure research projection. The caller must establish source identity,
training purpose, use-ledger authority, and split eligibility before using a
window for training. A Human choice is never treated as an executed action.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from tokenizers import Tokenizer

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
from .confirmed_interaction import (
    HISTORY_INPUT_PROFILE,
    HISTORY_PROFILES,
    V2_HISTORY_INPUT_PROFILE,
    confirmed_action_text,
)
from .memory_projection_config import (
    MemoryEpisodeProjectionConfig as MemoryEpisodeProjectionConfig,
)
from .memory_projection_config import (
    MemoryEpisodeProjectionConfigV2 as MemoryEpisodeProjectionConfigV2,
)
from .memory_projection_config import (
    MemoryEpisodeProjectionConfigV3 as MemoryEpisodeProjectionConfigV3,
)
from .memory_projection_config import (
    history_episode_projection_config as history_episode_projection_config,
)
from .memory_projection_config import (
    parse_episode_projection_config as parse_episode_projection_config,
)
from .memory_projection_config import (
    projection_input_profile as projection_input_profile,
)
from .memory_projection_config import (
    v2_episode_projection_config as v2_episode_projection_config,
)
from .memory_token_inputs import (
    encode_memory_texts,
    project_memory_profile_snapshot,
)
from .observed_input_sequence import ObservedInput, ObservedInputView
from .text_menu_inputs import INPUT_PROFILE, SNAPSHOT_SCHEMA, V2_INPUT_PROFILE


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
    event_mapping: tuple[MemoryEventMapping, ...]


@dataclass(frozen=True)
class MemoryEventMapping:
    """Auditable disposition of one verified observed event in an M2 projection."""

    source_id: str
    stream_id: str
    event_id: str
    source_sequence: int
    disposition: str
    episode_id: str | None
    position: int | None
    reason: str | None
    reset_reason: str | None = None


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
    view: ObservedInputView, tokenizer: Tokenizer, model: ExperimentalDSimpleM2,
) -> None:
    if (
        not isinstance(view, ObservedInputView)
        or not view.source_id
        or not isinstance(view.inputs, tuple)
        or not isinstance(model, ExperimentalDSimpleM2)
        or not isinstance(tokenizer, Tokenizer)
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
    stream_sequences: dict[str, int] = {}
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
        # A memory reset changes the model episode, never the source clock.
        # Remember each stream even if another stream was observed in between.
        if item.source_sequence <= stream_sequences.get(item.stream_id, 0):
            raise ValueError("invalid observed event order")
        stream_sequences[item.stream_id] = item.source_sequence
        if item.stream_id != last_stream or item.reset_before:
            if segment:
                segments.append(tuple(segment))
                segment = []
            broken = not item.reset_before
        last_stream = item.stream_id
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
    view: ObservedInputView, tokenizer: Tokenizer, model: ExperimentalDSimpleM2,
    segment: tuple[ObservedInput, ...], *, max_input_tokens: int, limit_code: str,
    input_profile: str = INPUT_PROFILE,
) -> tuple[str, tuple[MemorySequenceStep, ...]]:
    first = segment[0]
    episode_id = semantic_hash([view.source_id, first.stream_id, first.event_id])
    steps: list[MemorySequenceStep] = []
    input_tokens = 0
    consumed: set[str] = set()
    for position, item in enumerate(segment):
        if not isinstance(item.snapshot, dict):
            raise BoundaryError("memory_bridge", "missing_observation")
        public = project_memory_profile_snapshot(item.snapshot, input_profile)
        selected = item.selected_action_id
        if item.choice_mask != (selected is not None):
            raise BoundaryError("memory_bridge", "choice_mask_mismatch")
        if selected is not None and public.action_ids.count(selected) != 1:
            raise BoundaryError("memory_bridge", "choice_binding_mismatch")
        previous_text: str | None = None
        if input_profile in HISTORY_PROFILES:
            eligible = []
            for prior in segment[:position]:
                if (prior.event_id in consumed or not prior.choice_mask
                        or prior.selected_action_id is None
                        or prior.confirmed_at_sequence is None
                        or prior.confirmed_effect_domain is None):
                    continue
                if item.source_kind == "human_input_stream":
                    if (prior.capture_ordinal is None or item.capture_ordinal is None
                            or prior.capture_ordinal >= item.capture_ordinal
                            or item.completed_append_watermark is None
                            or prior.physical_sequence is None
                            or prior.physical_sequence > item.completed_append_watermark):
                        continue
                    order = prior.physical_sequence
                else:
                    if prior.confirmed_at_sequence >= item.source_sequence:
                        continue
                    order = prior.confirmed_at_sequence
                eligible.append((order, prior))
            if eligible:
                # All already visible older witnesses retire together. Only the
                # latest completed known interaction occupies the single slot.
                _, previous = max(eligible, key=lambda candidate: candidate[0])
                consumed.update(prior.event_id for _, prior in eligible)
                if not isinstance(previous.snapshot, dict):
                    raise BoundaryError("memory_bridge", "history_snapshot_missing")
                assert previous.selected_action_id is not None
                assert previous.confirmed_effect_domain is not None
                previous_text = confirmed_action_text(
                    previous.snapshot, previous.selected_action_id,
                    effect_domain=previous.confirmed_effect_domain,
                    basis=("last_known_human_input_witness" if item.source_kind ==
                           "human_input_stream" else "confirmed_connector_result"),
                    profile=input_profile,
                )
        row = encode_memory_texts(
            tokenizer, public.state_text, public.action_texts,
            max_tokens=model.core.max_tokens, slots=model.slots,
            previous_actual_action=previous_text is not None)
        previous_tokens = (tuple(tokenizer.encode(previous_text, add_special_tokens=False).ids)
                           if previous_text is not None else None)
        if (previous_tokens is not None and
                (not previous_tokens or len(previous_tokens) > model.core.max_tokens)):
            raise BoundaryError("memory_bridge", "history_token_limit")
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
            previous_actual_action=(torch.tensor(previous_tokens, dtype=torch.long,
                                                 device=model.write_queries.device)
                                    if previous_tokens is not None else None),
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
    tokenizer: Tokenizer,
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
    tokenizer: Tokenizer,
    model: ExperimentalDSimpleM2,
    *,
    max_observations: int,
    max_input_tokens: int,
    max_settling_events: int = 0,
    projection_config: (
        MemoryEpisodeProjectionConfig | MemoryEpisodeProjectionConfigV2 |
        MemoryEpisodeProjectionConfigV3 | None
    ) = None,
) -> MemoryEpisodeBridgeResult:
    """Project bounded episodes; opted-in verified settling writes no model state."""
    _validate_bridge_input(view, tokenizer, model)
    if projection_config is not None and type(projection_config) not in {
        MemoryEpisodeProjectionConfig, MemoryEpisodeProjectionConfigV2,
        MemoryEpisodeProjectionConfigV3,
    }:
        raise BoundaryError("memory_bridge", "projection_config_mismatch")
    input_profile = (INPUT_PROFILE if projection_config is None else
                     projection_input_profile(projection_config))
    if input_profile in {V2_INPUT_PROFILE, V2_HISTORY_INPUT_PROFILE}:
        allowed = ({"managed_engineering_control_inputs"} if input_profile ==
                   V2_INPUT_PROFILE else {"managed_engineering_control_inputs",
                                          "verified_agent_observed_inputs"})
        if (view.stream_scope not in allowed
                or any(not isinstance(item, ObservedInput)
                       or item.source_kind != ("managed_control_input_stream" if
                          view.stream_scope == "managed_engineering_control_inputs" else
                          "agent_decision_inputs") for item in view.inputs)
                or max_settling_events != 0):
            raise BoundaryError("memory_bridge", "v2_source_or_settling_mismatch")
    elif (input_profile == HISTORY_INPUT_PROFILE
          and view.stream_scope not in {"partial_human_input_stream",
                                        "verified_agent_observed_inputs"}):
        raise BoundaryError("memory_bridge", "history_source_profile_mismatch")
    if (projection_config is not None
            and max_settling_events != projection_config.max_settling_events):
        raise BoundaryError("memory_bridge", "projection_config_mismatch")
    if (type(max_observations) is not int or max_observations <= 0
            or type(max_input_tokens) is not int or max_input_tokens <= 0
            or type(max_settling_events) is not int or max_settling_events < 0):
        raise ValueError("invalid memory episode bridge limits")
    episodes: list[MemorySequenceEpisode] = []
    sources: list[MemoryEpisodeSource] = []
    segments, diagnostics = _segments(view)
    event_mapping = {
        item.event_id: MemoryEventMapping(
            view.source_id, item.stream_id, item.event_id, item.source_sequence,
            "excluded", None, None, "not_projected", item.reset_reason,
        ) for item in view.inputs
    }
    for diagnostic in diagnostics:
        mapped_event = event_mapping.get(diagnostic.first_event_id)
        if mapped_event is not None:
            event_mapping[mapped_event.event_id] = MemoryEventMapping(
                mapped_event.source_id, mapped_event.stream_id, mapped_event.event_id,
                mapped_event.source_sequence, "excluded", None, None, diagnostic.reason,
                mapped_event.reset_reason,
            )
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
                input_profile=input_profile,
            )
            if not any(step.label_key is not None for step in steps):
                diagnostics.append(MemoryBridgeDiagnostic(first.stream_id, first.event_id,
                                                          "no_learn_span_label"))
                for item in segment:
                    event_mapping[item.event_id] = MemoryEventMapping(
                        view.source_id, item.stream_id, item.event_id, item.source_sequence,
                        "excluded", None, None, "no_learn_span_label", item.reset_reason,
                    )
                continue
            episodes.append(MemorySequenceEpisode(episode_id, steps))
            sources.append(_episode_source(view, segment, projected, episode_id))
            positions = {item.event_id: position for position, item in enumerate(projected)}
            for item in segment:
                if item.event_id in positions:
                    event_mapping[item.event_id] = MemoryEventMapping(
                        view.source_id, item.stream_id, item.event_id, item.source_sequence,
                        "step", episode_id, positions[item.event_id], None, item.reset_reason,
                    )
                else:
                    event_mapping[item.event_id] = MemoryEventMapping(
                        view.source_id, item.stream_id, item.event_id, item.source_sequence,
                        "settling", episode_id, None, "verified_settling", item.reset_reason,
                    )
        except (BoundaryError, ValueError, TypeError, AttributeError) as error:
            reason = (error.code if isinstance(error, BoundaryError)
                      else "episode_contract_rejected")
            diagnostics.append(MemoryBridgeDiagnostic(
                first.stream_id, first.event_id, reason,
            ))
            for item in segment:
                event_mapping[item.event_id] = MemoryEventMapping(
                    view.source_id, item.stream_id, item.event_id, item.source_sequence,
                    "excluded", None, None, reason, item.reset_reason,
                )
    return MemoryEpisodeBridgeResult(tuple(episodes), tuple(sources),
                                     _ordered_diagnostics(view, diagnostics),
                                     tuple(event_mapping[item.event_id] for item in view.inputs))
