"""Pure projection of qualified native-v1 Human observations for LightActionM2.

This research projection consumes an already verified ``ObservedInputView``;
it is not source admission. It keeps page text in the state tokenizer and uses
the lossless byte codec for the complete ordered action catalog and prior
confirmed interaction.
"""

from __future__ import annotations

import hashlib
import heapq
from dataclasses import dataclass, replace

from tokenizers import Tokenizer

from spireagent.json_boundary import BoundaryError, json_bytes

from ..canonical import semantic_hash
from ..light_action_codec import SPEC_SHA256, encode_action
from ..models.light_action_m2 import LIGHT_ACTION_M2_GRAPH
from ..models.light_action_m2_training_data import (
    LightActionM2TrainingEpisode,
    LightActionM2TrainingStep,
)
from .confirmed_interaction import HISTORY_INPUT_PROFILE, confirmed_action_text
from .memory_sequence_bridge import _projectable_episode_items, _segments
from .memory_token_inputs import project_memory_profile_snapshot
from .observed_input_sequence import ObservedInput, ObservedInputView
from .token_inputs import input_texts

TEXT_M2_INPUT_SCHEMA = "stpd/stage1a-light-action-m2-text-menu-v1-input-v1"
TEXT_M2_SEQUENCE_PROFILE = "native-v1-qualified-observed-carry-v1"
TEXT_M2_PRIOR_ACTION_PROFILE = "previous-qualified-confirmed-input-through-append-watermark-v1"
MAX_TEXT_M2_SETTLING_EVENTS = 64


@dataclass(frozen=True)
class LightActionM2TextProjectionConfig:
    input_profile: str
    slots: int
    reset_each_step: bool
    max_action_bytes: int
    max_actions_per_step: int
    max_page_tokens: int
    max_settling_events: int = MAX_TEXT_M2_SETTLING_EVENTS
    max_episode_steps: int = 64
    max_chunk_steps: int = 32
    max_episode_tokens: int = 65_536
    max_chunk_tokens: int = 65_536


@dataclass(frozen=True)
class LightActionM2TextDisposition:
    event_id: str
    disposition: str
    reason: str | None = None
    episode_id: str | None = None
    position: int | None = None


@dataclass(frozen=True)
class LightActionM2TextProjection:
    schema: str
    source_id: str
    graph: str
    input_profile: str
    sequence_profile: str
    prior_action_profile: str
    action_codec_sha256: str
    state_tokenizer_sha256: str
    config: LightActionM2TextProjectionConfig
    episodes: tuple[LightActionM2TrainingEpisode, ...]
    dispositions: tuple[LightActionM2TextDisposition, ...]
    identity: str

    def _content(self) -> dict:
        return {
            "schema": self.schema,
            "source_id": self.source_id,
            "graph": self.graph,
            "input_profile": self.input_profile,
            "sequence_profile": self.sequence_profile,
            "prior_action_profile": self.prior_action_profile,
            "action_codec_sha256": self.action_codec_sha256,
            "state_tokenizer_sha256": self.state_tokenizer_sha256,
            "config": {
                "slots": self.config.slots,
                "reset_each_step": self.config.reset_each_step,
                "max_action_bytes": self.config.max_action_bytes,
                "max_actions_per_step": self.config.max_actions_per_step,
                "max_page_tokens": self.config.max_page_tokens,
                "max_settling_events": self.config.max_settling_events,
                "max_episode_steps": self.config.max_episode_steps,
                "max_chunk_steps": self.config.max_chunk_steps,
                "max_episode_tokens": self.config.max_episode_tokens,
                "max_chunk_tokens": self.config.max_chunk_tokens,
            },
            "episodes": [
                {
                    "episode_id": episode.episode_id,
                    "slots": episode.slots,
                    "reset_each_step": episode.reset_each_step,
                    "max_episode_steps": episode.max_episode_steps,
                    "max_chunk_steps": episode.max_chunk_steps,
                    "max_episode_tokens": episode.max_episode_tokens,
                    "max_chunk_tokens": episode.max_chunk_tokens,
                    "steps": [
                        {
                            "position": step.position,
                            "page": list(step.page),
                            "action_ids": list(step.action_ids),
                            "byte_actions": [list(action) for action in step.byte_actions],
                            "target_action_id": step.target_action_id,
                            "previous_actual_action": (
                                None
                                if step.previous_actual_action is None
                                else list(step.previous_actual_action)
                            ),
                            "reset_before": step.reset_before,
                        }
                        for step in episode.steps
                    ],
                }
                for episode in self.episodes
            ],
            "dispositions": [
                {
                    "event_id": item.event_id,
                    "disposition": item.disposition,
                    "reason": item.reason,
                    "episode_id": item.episode_id,
                    "position": item.position,
                }
                for item in self.dispositions
            ],
        }

    def to_dict(self) -> dict:
        value = self._content()
        value["identity"] = self.identity
        return value


def text_projection_bytes(value: LightActionM2TextProjection) -> bytes:
    if not isinstance(value, LightActionM2TextProjection):
        raise BoundaryError("light_action_m2_text_projection", "typed_projection_required")
    if hashlib.sha256(json_bytes(value._content())).hexdigest() != value.identity:
        raise BoundaryError("light_action_m2_text_projection", "projection_identity_mismatch")
    return json_bytes(value.to_dict())


def _validate_config(config: LightActionM2TextProjectionConfig) -> None:
    if (
        not isinstance(config, LightActionM2TextProjectionConfig)
        or config.input_profile != HISTORY_INPUT_PROFILE
        or type(config.slots) is not int
        or config.slots not in (1, 8)
        or type(config.reset_each_step) is not bool
        or type(config.max_action_bytes) is not int
        or not 1 <= config.max_action_bytes <= 8192
        or type(config.max_actions_per_step) is not int
        or not 1 <= config.max_actions_per_step <= 16_384
        or type(config.max_page_tokens) is not int
        or not 1 <= config.max_page_tokens <= 65_536
        or type(config.max_settling_events) is not int
        or not 0 <= config.max_settling_events <= MAX_TEXT_M2_SETTLING_EVENTS
        or type(config.max_episode_steps) is not int
        or not 1 <= config.max_episode_steps <= 64
        or type(config.max_chunk_steps) is not int
        or not 1 <= config.max_chunk_steps <= 32
        or type(config.max_episode_tokens) is not int
        or not 1 <= config.max_episode_tokens <= 65_536
        or type(config.max_chunk_tokens) is not int
        or not 1 <= config.max_chunk_tokens <= 65_536
    ):
        raise BoundaryError("light_action_m2_text_projection", "invalid_projection_config")


def _project_step(
    item: ObservedInput,
    tokenizer: Tokenizer,
    config: LightActionM2TextProjectionConfig,
    position: int,
    previous: ObservedInput | None,
) -> LightActionM2TrainingStep:
    if (
        not isinstance(item.snapshot, dict)
        or item.source_kind != "human_input_stream"
        or item.choice_mask != (item.selected_action_id is not None)
    ):
        raise BoundaryError("light_action_m2_text_projection", "invalid_human_observation")
    public = project_memory_profile_snapshot(item.snapshot, config.input_profile)
    if (
        len(public.action_ids) > config.max_actions_per_step
        or len(public.action_ids) != len(public.action_texts)
        or len(set(public.action_ids)) != len(public.action_ids)
    ):
        raise BoundaryError("light_action_m2_text_projection", "complete_catalog_required")
    page_text, _ = input_texts(public.state_text, ())
    page = tuple(tokenizer.encode(page_text, add_special_tokens=False).ids)
    if not page or len(page) > config.max_page_tokens:
        raise BoundaryError("light_action_m2_text_projection", "page_token_limit")
    byte_actions = tuple(
        encode_action(text, max_bytes=config.max_action_bytes) for text in public.action_texts
    )
    target = item.selected_action_id
    if target is not None and public.action_ids.count(target) != 1:
        raise BoundaryError("light_action_m2_text_projection", "target_binding_mismatch")
    previous_bytes: tuple[int, ...] | None = None
    if previous is not None:
        if (
            previous.source_kind != "human_input_stream"
            or not previous.choice_mask
            or previous.selected_action_id is None
            or previous.confirmed_effect_domain is None
            or not isinstance(previous.snapshot, dict)
        ):
            raise BoundaryError("light_action_m2_text_projection", "history_binding_mismatch")
        text = confirmed_action_text(
            previous.snapshot,
            previous.selected_action_id,
            effect_domain=previous.confirmed_effect_domain,
            basis="last_known_human_input_witness",
            profile=config.input_profile,
        )
        previous_bytes = encode_action(text, max_bytes=config.max_action_bytes)
    return LightActionM2TrainingStep(
        position=position,
        page=page,
        action_ids=public.action_ids,
        byte_actions=byte_actions,
        target_action_id=target,
        previous_actual_action=previous_bytes,
        reset_before=position == 0,
    )


def project_light_action_m2_human_text(
    view: ObservedInputView,
    tokenizer: Tokenizer,
    config: LightActionM2TextProjectionConfig,
) -> LightActionM2TextProjection:
    """Project verified Human input rows without admission or event ownership."""
    _validate_config(config)
    if (
        not isinstance(view, ObservedInputView)
        or not isinstance(view.source_id, str)
        or not view.source_id
        or view.stream_scope != "partial_human_input_stream"
        or not isinstance(view.inputs, tuple)
        or not isinstance(tokenizer, Tokenizer)
        or tokenizer.truncation is not None
        or tokenizer.padding is not None
    ):
        raise BoundaryError("light_action_m2_text_projection", "verified_human_view_required")
    if any(not isinstance(item, ObservedInput) for item in view.inputs):
        raise BoundaryError("light_action_m2_text_projection", "invalid_observed_input")
    if any(item.source_kind != "human_input_stream" for item in view.inputs):
        raise BoundaryError("light_action_m2_text_projection", "human_input_stream_required")

    segments, diagnostics = _segments(view, HISTORY_INPUT_PROFILE)
    disposition_by_event = {
        item.event_id: LightActionM2TextDisposition(item.event_id, "excluded", "not_projected")
        for item in view.inputs
    }
    for diagnostic in diagnostics:
        if diagnostic.first_event_id in disposition_by_event:
            disposition_by_event[diagnostic.first_event_id] = LightActionM2TextDisposition(
                diagnostic.first_event_id,
                "excluded",
                diagnostic.reason,
            )

    episodes: list[LightActionM2TrainingEpisode] = []
    for segment in segments:
        projected = _projectable_episode_items(segment, config.max_settling_events)
        projected_ids = {item.event_id for item in projected}
        for item in segment:
            if item.event_id not in projected_ids:
                disposition_by_event[item.event_id] = LightActionM2TextDisposition(
                    item.event_id,
                    "no_memory_write",
                    "verified_settling_observation",
                )
        if not projected:
            continue

        pending: list[tuple[int, int, ObservedInput]] = []
        steps: list[LightActionM2TrainingStep] = []
        step_event_ids: list[str] = []
        segment_tokens = 0
        episode_part = 0
        segment_identity = semantic_hash(
            [view.source_id, segment[0].stream_id, segment[0].event_id]
        )

        def emit(identity: str = segment_identity) -> None:
            nonlocal steps, step_event_ids, segment_tokens, episode_part
            if not steps:
                return
            episode_id = semantic_hash(
                [TEXT_M2_INPUT_SCHEMA, identity, episode_part, step_event_ids]
            )
            episodes.append(
                LightActionM2TrainingEpisode(
                    episode_id=episode_id,
                    slots=config.slots,
                    reset_each_step=config.reset_each_step,
                    steps=tuple(steps),
                    max_episode_steps=config.max_episode_steps,
                    max_chunk_steps=config.max_chunk_steps,
                    max_episode_tokens=config.max_episode_tokens,
                    max_chunk_tokens=config.max_chunk_tokens,
                )
            )
            for position, event_id in enumerate(step_event_ids):
                disposition_by_event[event_id] = LightActionM2TextDisposition(
                    event_id,
                    "projected",
                    None,
                    episode_id,
                    position,
                )
            episode_part += 1
            steps, step_event_ids, segment_tokens = [], [], 0

        for item in projected:
            watermark = (
                item.completed_append_watermark if item.capture_ordinal is not None else None
            )
            latest: tuple[int, int, ObservedInput] | None = None
            while pending and watermark is not None and pending[0][0] <= watermark:
                candidate = heapq.heappop(pending)
                if latest is None or candidate[:2] > latest[:2]:
                    latest = candidate
            previous = None if latest is None else latest[2]
            step = _project_step(item, tokenizer, config, len(steps), previous)
            step_tokens = (
                len(step.page)
                + sum(map(len, step.byte_actions))
                + (0 if step.previous_actual_action is None else len(step.previous_actual_action))
            )
            if step_tokens > config.max_chunk_tokens:
                raise BoundaryError("light_action_m2_text_projection", "step_chunk_budget_exceeded")
            if step_tokens > config.max_episode_tokens:
                raise BoundaryError(
                    "light_action_m2_text_projection",
                    "step_episode_budget_exceeded",
                )
            if steps and (
                len(steps) >= config.max_episode_steps
                or segment_tokens + step_tokens > config.max_episode_tokens
            ):
                emit()
                # Episode limits reset memory; the historical previous interaction
                # remains separately available if its append watermark qualifies.
                step = _project_step(item, tokenizer, config, 0, previous)
                step_tokens = (
                    len(step.page)
                    + sum(map(len, step.byte_actions))
                    + (
                        0
                        if step.previous_actual_action is None
                        else len(step.previous_actual_action)
                    )
                )
            if step.position != len(steps):
                step = LightActionM2TrainingStep(
                    position=len(steps),
                    page=step.page,
                    action_ids=step.action_ids,
                    byte_actions=step.byte_actions,
                    target_action_id=step.target_action_id,
                    previous_actual_action=step.previous_actual_action,
                    reset_before=not steps,
                )
            steps.append(step)
            step_event_ids.append(item.event_id)
            segment_tokens += step_tokens
            disposition_by_event[item.event_id] = LightActionM2TextDisposition(
                item.event_id,
                "projected",
                None,
            )
            if (
                item.choice_mask
                and item.selected_action_id is not None
                and item.capture_ordinal is not None
                and item.physical_sequence is not None
                and item.confirmed_at_sequence is not None
                and item.confirmed_effect_domain is not None
            ):
                heapq.heappush(pending, (item.physical_sequence, len(steps) - 1, item))
            elif (
                item.choice_mask
                and item.selected_action_id is not None
                and item.confirmed_at_sequence is not None
            ):
                # A row without exact capture/physical order remains a valid current
                # label, but it cannot be a recurrent history witness.
                pass
        emit()

    tokenizer_sha256 = hashlib.sha256(tokenizer.to_str().encode("utf-8")).hexdigest()
    result = LightActionM2TextProjection(
        schema=TEXT_M2_INPUT_SCHEMA,
        source_id=view.source_id,
        graph=LIGHT_ACTION_M2_GRAPH,
        input_profile=HISTORY_INPUT_PROFILE,
        sequence_profile=TEXT_M2_SEQUENCE_PROFILE,
        prior_action_profile=TEXT_M2_PRIOR_ACTION_PROFILE,
        action_codec_sha256=SPEC_SHA256,
        state_tokenizer_sha256=tokenizer_sha256,
        config=config,
        episodes=tuple(episodes),
        dispositions=tuple(disposition_by_event.values()),
        identity="",
    )
    identity = hashlib.sha256(json_bytes(result._content())).hexdigest()
    return replace(result, identity=identity)
