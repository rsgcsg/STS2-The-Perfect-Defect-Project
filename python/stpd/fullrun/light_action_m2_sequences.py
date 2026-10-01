"""Canonical, source-ordered sequence projection for light-action M2.

This is a research input projection, not an event owner. Callers must pass the
canonical transition rows after the existing decision-dataset loader verified
their immutable source, allocation and transition proofs. Only an immediately
preceding proved commit in the same source/session/run occurrence is projected
as prior actual action; a gap starts a fresh episode.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

from spireagent.json_boundary import BoundaryError, json_bytes

from ..canonical import semantic_hash
from ..light_action_codec import BOS_ACT, EOS_ACT
from ..models.light_action_m2 import LIGHT_ACTION_M2_GRAPH
from .features import ModelSample
from .light_action_inputs import (
    CANONICAL_SCHEMA as CANONICAL_M0_INPUT_SCHEMA,
)
from .light_action_inputs import (
    TRAINING_BINDING_SCHEMA,
    LightActionTokenRow,
    LoadedLightActionInputs,
)

LIGHT_ACTION_M2_SEQUENCE_SCHEMA = "stpd/stage1a-light-action-m2-sequence-input-v1"
LIGHT_ACTION_M2_SEQUENCE_PROFILE = "canonical-source-adjacent-commit-v1"
LIGHT_ACTION_M2_PRIOR_ACTION_PROFILE = "previous-transition-proved-commit-v1"
LIGHT_ACTION_M2_FEEDBACK_PROFILE = "none-v1"
LIGHT_ACTION_M2_TRUNCATION_PROFILE = "fixed-chunk-detach-after-backward-v1"
MAX_SEQUENCE_ROWS = 100_000
MAX_EPISODE_STEPS = 64
MAX_BPTT_STEPS = 32
MAX_EPISODE_INPUT_TOKENS = 65_536
MAX_CHUNK_INPUT_TOKENS = 65_536
_RECIPE_IDS = {
    f"stage1a.dsimple.light-action.m2.{backbone}.v1"
    for backbone in ("s", "pf", "pl")
}


@dataclass(frozen=True)
class LightActionM2SequenceConfig:
    """Identity-bearing limits and matched memory variant for one sequence input."""

    recipe_id: str
    slots: int
    reset_each_step: bool
    max_episode_steps: int = MAX_EPISODE_STEPS
    max_bptt_steps: int = MAX_BPTT_STEPS
    max_episode_input_tokens: int = MAX_EPISODE_INPUT_TOKENS
    max_chunk_input_tokens: int = MAX_CHUNK_INPUT_TOKENS

    def __post_init__(self) -> None:
        if (self.recipe_id not in _RECIPE_IDS or type(self.slots) is not int
                or self.slots not in (1, 8) or type(self.reset_each_step) is not bool):
            raise BoundaryError("light_action_m2_sequence", "invalid_graph_variant")
        if (type(self.max_episode_steps) is not int
                or not 1 <= self.max_episode_steps <= MAX_EPISODE_STEPS
                or type(self.max_bptt_steps) is not int
                or not 1 <= self.max_bptt_steps <= MAX_BPTT_STEPS
                or type(self.max_episode_input_tokens) is not int
                or not 1 <= self.max_episode_input_tokens <= MAX_EPISODE_INPUT_TOKENS
                or type(self.max_chunk_input_tokens) is not int
                or not 1 <= self.max_chunk_input_tokens <= MAX_CHUNK_INPUT_TOKENS):
            raise BoundaryError("light_action_m2_sequence", "invalid_sequence_limits")


@dataclass(frozen=True)
class LightActionM2SourceOccurrence:
    """Minimal provenance copied from one verified canonical transition and allocation."""

    source_archive_sha256: str
    session_id: str
    native_run_id: str
    run_id: str
    source_sequence: int
    transition_id: str
    split: Literal["train", "dev"]
    source_disposition: str
    transition_disposition: str
    commit_ref: str
    successor_ref: str
    proof_ref: str

    def __post_init__(self) -> None:
        for name in ("source_archive_sha256", "transition_id", "commit_ref",
                     "successor_ref", "proof_ref"):
            value = getattr(self, name)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise BoundaryError("light_action_m2_sequence", "invalid_source_digest")
        if (not isinstance(self.session_id, str) or not self.session_id
                or not isinstance(self.native_run_id, str) or not self.native_run_id
                or self.run_id != f"{self.session_id}/{self.native_run_id}"
                or type(self.source_sequence) is not int or self.source_sequence < 1
                or self.split not in {"train", "dev"}
                or self.source_disposition != "transition_proved"
                or self.transition_disposition != "committed"):
            raise BoundaryError("light_action_m2_sequence", "unproved_or_unbound_transition")


@dataclass(frozen=True)
class LightActionM2SequenceStep:
    episode_id: str
    position: int
    run_occurrence_id: str
    source_sequence: int
    transition_id: str
    commit_ref: str
    successor_ref: str
    proof_ref: str
    page: tuple[int, ...]
    action_ids: tuple[str, ...]
    actions: tuple[tuple[int, ...], ...]
    target_action_id: str
    previous_actual_action: tuple[int, ...] | None
    previous_transition_id: str | None
    reset_before: bool
    split: Literal["train", "dev"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id, "position": self.position,
            "run_occurrence_id": self.run_occurrence_id,
            "source_sequence": self.source_sequence, "transition_id": self.transition_id,
            "commit_ref": self.commit_ref, "successor_ref": self.successor_ref,
            "proof_ref": self.proof_ref, "page": list(self.page),
            "action_ids": list(self.action_ids),
            "actions": [list(action) for action in self.actions],
            "target_action_id": self.target_action_id,
            "previous_actual_action": (None if self.previous_actual_action is None
                                        else list(self.previous_actual_action)),
            "previous_transition_id": self.previous_transition_id,
            "reset_before": self.reset_before, "split": self.split,
        }


@dataclass(frozen=True)
class LightActionM2SequenceEpisode:
    episode_id: str
    source_archive_sha256: str
    session_id: str
    native_run_id: str
    split: Literal["train", "dev"]
    config: LightActionM2SequenceConfig
    steps: tuple[LightActionM2SequenceStep, ...]

    @property
    def run_occurrence_id(self) -> str:
        return semantic_hash([self.source_archive_sha256, self.session_id, self.native_run_id])

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "source_archive_sha256": self.source_archive_sha256,
            "session_id": self.session_id, "native_run_id": self.native_run_id,
            "run_occurrence_id": self.run_occurrence_id, "split": self.split,
            "config": asdict(self.config),
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class LightActionM2SequenceInput:
    schema: str
    m0_input_id: str
    allocation_id: str
    model_view_id: str
    training_operation_id: str
    dataset_ids: tuple[str, ...]
    source_archive_ids: tuple[str, ...]
    graph: str
    sequence_profile: str
    prior_action_profile: str
    feedback_profile: str
    truncation_profile: str
    config: LightActionM2SequenceConfig
    episodes: tuple[LightActionM2SequenceEpisode, ...]
    identity: str

    def _content(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "m0_input_id": self.m0_input_id,
            "allocation_id": self.allocation_id, "model_view_id": self.model_view_id,
            "training_operation_id": self.training_operation_id,
            "dataset_ids": list(self.dataset_ids),
            "source_archive_ids": list(self.source_archive_ids),
            "graph": self.graph, "sequence_profile": self.sequence_profile,
            "prior_action_profile": self.prior_action_profile,
            "feedback_profile": self.feedback_profile,
            "truncation_profile": self.truncation_profile,
            "config": asdict(self.config),
            "physical_game_independence": "unresolved",
            "interpretation": "within-purpose-diagnostic-no-independent-game-claim",
            "episodes": [episode.to_dict() for episode in self.episodes],
        }

    def to_dict(self) -> dict[str, Any]:
        value = self._content()
        value["identity"] = self.identity
        return value

    def payload_bytes(self) -> bytes:
        """Canonical payload bytes suitable for a later immutable training-input artifact."""
        return json_bytes(self.to_dict())


def _validate_input_binding(
    inputs: LoadedLightActionInputs,
) -> tuple[str, str, str, tuple[str, ...]]:
    manifest = inputs.manifest
    info = manifest.parameters.value()
    if (manifest.kind != "training_input"
            or info.get("schema") != CANONICAL_M0_INPUT_SCHEMA
            or info.get("graph") != "dsimple.light-action.m0.v1"
            or len(manifest.parents) != 1 or manifest.parents[0].role != "model_view"):
        raise BoundaryError("light_action_m2_sequence", "canonical_m0_input_required")
    binding = info.get("training_binding")
    if (not isinstance(binding, dict)
            or set(binding) != {"schema", "dataset_ids", "training_operation_id",
                                "allocation_id", "model_view_id"}
            or binding.get("schema") != TRAINING_BINDING_SCHEMA
            or binding.get("model_view_id") != manifest.parent("model_view")
            or not isinstance(binding.get("dataset_ids"), list)
            or binding["dataset_ids"] != sorted(set(binding["dataset_ids"]))
            or any(not isinstance(value, str) or not value for value in binding["dataset_ids"])
            or not isinstance(binding.get("allocation_id"), str)
            or not isinstance(binding.get("training_operation_id"), str)):
        raise BoundaryError("light_action_m2_sequence", "canonical_m0_lineage_required")
    return (manifest.artifact_id, binding["allocation_id"], binding["model_view_id"],
            tuple(binding["dataset_ids"]))


def _input_records(
    inputs: LoadedLightActionInputs,
    source_occurrences: tuple[LightActionM2SourceOccurrence, ...],
) -> tuple[tuple[ModelSample, LightActionTokenRow, LightActionM2SourceOccurrence], ...]:
    if (not isinstance(source_occurrences, tuple) or not source_occurrences
            or len(source_occurrences) > MAX_SEQUENCE_ROWS
            or len(inputs.samples) > MAX_SEQUENCE_ROWS or len(inputs.rows) != len(inputs.samples)):
        raise BoundaryError("light_action_m2_sequence", "source_row_limit_or_shape")
    sample_by_id: dict[str, tuple[ModelSample, LightActionTokenRow]] = {}
    for sample, row in zip(inputs.samples, inputs.rows, strict=True):
        if sample.transition_id in sample_by_id:
            raise BoundaryError("light_action_m2_sequence", "duplicate_input_transition")
        vocab_size = inputs.state_tokenizer.get_vocab_size()
        if (sample.split not in {"train", "dev"}
                or row.action_ids != sample.action_keys
                or len(row.actions) != len(sample.action_keys)
                or not row.actions or len(set(row.action_ids)) != len(row.action_ids)
                or not 0 <= sample.chosen_index < len(row.actions)
                or not row.state or any(type(token) is not int or not 0 <= token < vocab_size
                                        for token in row.state)):
            raise BoundaryError("light_action_m2_sequence", "invalid_m0_row_binding")
        sample_by_id[sample.transition_id] = (sample, row)
    source_by_id: dict[str, LightActionM2SourceOccurrence] = {}
    for occurrence in source_occurrences:
        if occurrence.transition_id in source_by_id:
            raise BoundaryError("light_action_m2_sequence", "duplicate_source_transition")
        source_by_id[occurrence.transition_id] = occurrence
    if set(sample_by_id) != set(source_by_id):
        raise BoundaryError("light_action_m2_sequence", "selected_membership_mismatch")
    records = []
    for transition_id, (sample, row) in sample_by_id.items():
        occurrence = source_by_id[transition_id]
        if (sample.run_id != occurrence.run_id or sample.split != occurrence.split
                or row.action_ids[sample.chosen_index] != sample.action_keys[sample.chosen_index]):
            raise BoundaryError("light_action_m2_sequence", "source_sample_binding_mismatch")
        target = row.actions[sample.chosen_index]
        if (len(target) < 3 or target[0] != BOS_ACT or target[-1] != EOS_ACT
                or any(type(value) is not int for value in target)
                or any(value < 0 or value > 255 for value in target[1:-1])):
            raise BoundaryError("light_action_m2_sequence", "invalid_target_action_codec")
        for action in row.actions:
            if (len(action) < 3 or action[0] != BOS_ACT or action[-1] != EOS_ACT
                    or any(type(value) is not int for value in action)
                    or any(value < 0 or value > 255 for value in action[1:-1])):
                raise BoundaryError("light_action_m2_sequence", "invalid_action_codec")
        records.append((sample, row, occurrence))
    return tuple(records)


def _step_tokens(row: LightActionTokenRow,
                 previous_action: tuple[int, ...] | None) -> int:
    return (len(row.state) + sum(len(action) for action in row.actions)
            + (0 if previous_action is None else len(previous_action)))


def project_light_action_m2_sequences(
    inputs: LoadedLightActionInputs,
    source_occurrences: tuple[LightActionM2SourceOccurrence, ...],
    config: LightActionM2SequenceConfig,
) -> LightActionM2SequenceInput:
    """Bind exact M0 membership to source-ordered, target-separated episodes.

    Each source tuple must contain only rows that the canonical allocation selected;
    omitted decisions are not inferred or bridged. Gaps and source/session/run changes
    begin a reset segment. Current labels remain targets; only the adjacent prior proved
    committed row contributes previous-actual-action bytes.
    """
    if not isinstance(inputs, LoadedLightActionInputs) or not isinstance(
        config, LightActionM2SequenceConfig,
    ):
        raise BoundaryError("light_action_m2_sequence", "typed_inputs_required")
    m0_input_id, allocation_id, model_view_id, dataset_ids = _validate_input_binding(inputs)
    records = _input_records(inputs, source_occurrences)
    grouped: dict[tuple[str, str, str], list[tuple[ModelSample, LightActionTokenRow,
                                                  LightActionM2SourceOccurrence]]] = {}
    for item in records:
        occurrence = item[2]
        key = (occurrence.source_archive_sha256, occurrence.session_id,
               occurrence.native_run_id)
        grouped.setdefault(key, []).append(item)
    episodes: list[LightActionM2SequenceEpisode] = []
    for (source_id, session_id, native_run_id), run_rows in sorted(grouped.items()):
        splits = {row[0].split for row in run_rows}
        if len(splits) != 1:
            raise BoundaryError("light_action_m2_sequence", "mixed_split_run_occurrence")
        split = cast(Literal["train", "dev"], next(iter(splits)))
        run_rows.sort(key=lambda row: row[2].source_sequence)
        sequence_values = [row[2].source_sequence for row in run_rows]
        if len(sequence_values) != len(set(sequence_values)):
            raise BoundaryError("light_action_m2_sequence", "duplicate_source_sequence")
        run_occurrence_id = semantic_hash([source_id, session_id, native_run_id])
        segment: list[tuple[ModelSample, LightActionTokenRow,
                            LightActionM2SourceOccurrence, tuple[int, ...] | None,
                            str | None]] = []
        segment_tokens = 0

        def emit(
            source_id: str = source_id, session_id: str = session_id,
            native_run_id: str = native_run_id, split: Literal["train", "dev"] = split,
            run_occurrence_id: str = run_occurrence_id,
        ) -> None:
            nonlocal segment, segment_tokens
            if not segment:
                return
            start_sequence = segment[0][2].source_sequence
            episode_id = semantic_hash([
                LIGHT_ACTION_M2_SEQUENCE_SCHEMA, m0_input_id, run_occurrence_id,
                split, start_sequence, [item[2].transition_id for item in segment],
            ])
            steps = tuple(
                LightActionM2SequenceStep(
                    episode_id=episode_id, position=position,
                    run_occurrence_id=run_occurrence_id,
                    source_sequence=sample_source.source_sequence,
                    transition_id=sample_source.transition_id,
                    commit_ref=sample_source.commit_ref,
                    successor_ref=sample_source.successor_ref,
                    proof_ref=sample_source.proof_ref,
                    page=row.state, action_ids=row.action_ids, actions=row.actions,
                    target_action_id=sample.action_keys[sample.chosen_index],
                    previous_actual_action=previous_action,
                    previous_transition_id=previous_transition_id,
                    reset_before=position == 0, split=split,
                )
                for position, (sample, row, sample_source, previous_action,
                               previous_transition_id) in enumerate(segment)
            )
            episodes.append(LightActionM2SequenceEpisode(
                episode_id, source_id, session_id, native_run_id, split, config, steps,
            ))
            segment, segment_tokens = [], 0

        for sample, row, occurrence in run_rows:
            previous = segment[-1] if segment else None
            adjacent = (previous is not None
                        and previous[2].source_sequence + 1 == occurrence.source_sequence)
            if adjacent and previous is not None:
                prior_action = previous[1].actions[previous[0].chosen_index]
                prior_transition = previous[2].transition_id
            else:
                prior_action, prior_transition = None, None
            step_tokens = _step_tokens(row, prior_action)
            if step_tokens > config.max_chunk_input_tokens:
                raise BoundaryError("light_action_m2_sequence", "single_step_chunk_budget_exceeded")
            if step_tokens > config.max_episode_input_tokens:
                raise BoundaryError(
                    "light_action_m2_sequence", "single_step_episode_budget_exceeded",
                )
            if segment and not adjacent:
                emit()
                prior_action, prior_transition = None, None
                step_tokens = _step_tokens(row, None)
                if step_tokens > config.max_episode_input_tokens:
                    raise BoundaryError(
                        "light_action_m2_sequence", "single_step_episode_budget_exceeded",
                    )
            if segment and (len(segment) >= config.max_episode_steps
                            or segment_tokens + step_tokens > config.max_episode_input_tokens):
                emit()
                # The old state resets at a segment boundary, but an adjacent exact
                # committed action is still an available input on this first page.
            segment.append((sample, row, occurrence, prior_action, prior_transition))
            segment_tokens += step_tokens
        emit()

    if not episodes or not any(step.split == "train" for episode in episodes
                               for step in episode.steps):
        raise BoundaryError("light_action_m2_sequence", "train_sequences_required")
    if not any(step.split == "dev" for episode in episodes for step in episode.steps):
        raise BoundaryError("light_action_m2_sequence", "dev_sequences_required")
    source_ids = tuple(sorted({item.source_archive_sha256 for item in source_occurrences}))
    content = {
        "schema": LIGHT_ACTION_M2_SEQUENCE_SCHEMA, "m0_input_id": m0_input_id,
        "allocation_id": allocation_id, "model_view_id": model_view_id,
        "training_operation_id": inputs.manifest.parameters.value()["training_binding"][
            "training_operation_id"],
        "dataset_ids": list(dataset_ids), "source_archive_ids": list(source_ids),
        "graph": LIGHT_ACTION_M2_GRAPH,
        "sequence_profile": LIGHT_ACTION_M2_SEQUENCE_PROFILE,
        "prior_action_profile": LIGHT_ACTION_M2_PRIOR_ACTION_PROFILE,
        "feedback_profile": LIGHT_ACTION_M2_FEEDBACK_PROFILE,
        "truncation_profile": LIGHT_ACTION_M2_TRUNCATION_PROFILE,
        "config": asdict(config),
        "physical_game_independence": "unresolved",
        "interpretation": "within-purpose-diagnostic-no-independent-game-claim",
        "episodes": [episode.to_dict() for episode in episodes],
    }
    identity = hashlib.sha256(json_bytes(content)).hexdigest()
    return LightActionM2SequenceInput(
        LIGHT_ACTION_M2_SEQUENCE_SCHEMA, m0_input_id, allocation_id, model_view_id,
        inputs.manifest.parameters.value()["training_binding"]["training_operation_id"],
        dataset_ids, source_ids, LIGHT_ACTION_M2_GRAPH,
        LIGHT_ACTION_M2_SEQUENCE_PROFILE, LIGHT_ACTION_M2_PRIOR_ACTION_PROFILE,
        LIGHT_ACTION_M2_FEEDBACK_PROFILE, LIGHT_ACTION_M2_TRUNCATION_PROFILE,
        config, tuple(episodes), identity,
    )


def sequence_input_bytes(value: LightActionM2SequenceInput) -> bytes:
    if not isinstance(value, LightActionM2SequenceInput):
        raise BoundaryError("light_action_m2_sequence", "typed_sequence_input_required")
    content = value._content()
    if hashlib.sha256(json_bytes(content)).hexdigest() != value.identity:
        raise BoundaryError("light_action_m2_sequence", "sequence_identity_mismatch")
    return json_bytes(value.to_dict())
