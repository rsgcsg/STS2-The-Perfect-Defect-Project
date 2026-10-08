"""Native occurrence replay; labels are sparse targets, never synthetic Wait actions.

The initial source is a synthetic conformance format. A future native recording
bridge must verify actual publication/actor/input witnesses before Agent admission.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from spireagent.json_boundary import BoundaryError, decode_json, object_fields

from ..canonical import semantic_hash
from .native_structured_inputs import INPUT_SPEC, project_native_structured, qualified_occurrence
from .structured_inputs import StructuredFrame

SOURCE_SCHEMA = "stpd/native-structured-sequence-source-v1"
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_STEPS = 4096


@dataclass(frozen=True)
class NativeUnit:
    occurrence: tuple[str, str, str, str, str | None]
    revision: int
    coherence: str


def qualify_native(
    observation: dict[str, Any], actions: list[dict[str, Any]]
) -> tuple[
    StructuredFrame,
    NativeUnit,
]:
    frame = project_native_structured(observation, actions)
    descriptor = {
        key: value
        for key, value in observation["catalog"].items()
        if key not in {"catalog_ref", "scope_id"}
    }
    facts = {
        key: value for key, value in observation.items() if key not in {"observed_at", "catalog"}
    }
    unit = NativeUnit(
        qualified_occurrence(observation),
        observation["revision"],
        semantic_hash({"observation": facts, "catalog": descriptor}),
    )
    return frame, unit


def native_advance(previous: NativeUnit | None, current: NativeUnit) -> bool:
    if previous is None:
        return True
    if previous.occurrence[0] != current.occurrence[0]:
        raise BoundaryError("native_structured_sequence", "generation_requires_explicit_reset")
    if current.revision < previous.revision:
        raise BoundaryError("native_structured_sequence", "native_revision_regressed")
    if current.occurrence == previous.occurrence:
        if current.coherence != previous.coherence or current.revision != previous.revision:
            raise BoundaryError("native_structured_sequence", "same_occurrence_coherence_drift")
        return False
    if current.revision <= previous.revision:
        raise BoundaryError("native_structured_sequence", "new_occurrence_revision_required")
    return True


@dataclass(frozen=True)
class NativeStructuredStep:
    frame: StructuredFrame
    unit: NativeUnit
    continuity_token: str
    reset_before: bool
    advance: bool
    chosen_action_id: str | None


@dataclass(frozen=True)
class NativeStructuredSequence:
    steps: tuple[NativeStructuredStep, ...]
    source_sha256: str
    source_bytes: bytes


def parse_native_sequence(raw: bytes) -> NativeStructuredSequence:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES:
        raise BoundaryError("native_structured_sequence", "source_byte_limit")
    source = object_fields(
        decode_json(raw),
        {"schema", "source_kind", "input_spec", "steps"},
        "native_structured_sequence",
    )
    if (
        source["schema"] != SOURCE_SCHEMA
        or source["source_kind"] != "synthetic"
        or source["input_spec"] != INPUT_SPEC
        or not isinstance(source["steps"], list)
        or not 0 < len(source["steps"]) <= MAX_STEPS
    ):
        raise BoundaryError("native_structured_sequence", "source_identity_or_scope")
    steps = []
    previous: NativeUnit | None = None
    continuity: str | None = None
    retired: set[str] = set()
    for raw_step in source["steps"]:
        value = object_fields(
            raw_step,
            {"observation", "catalog", "continuity_token", "reset_before", "chosen_action_id"},
            "native_structured_sequence.step",
        )
        token = value["continuity_token"]
        reset = value["reset_before"]
        if (
            not isinstance(token, str)
            or not token
            or type(reset) is not bool
            or reset != (token != continuity)
            or token in retired
        ):
            raise BoundaryError("native_structured_sequence", "explicit_continuity_reset_required")
        if reset:
            if continuity is not None:
                retired.add(continuity)
            previous = None
        frame, unit = qualify_native(value["observation"], value["catalog"])
        advance = native_advance(previous, unit)
        chosen = value["chosen_action_id"]
        if chosen is not None and (not isinstance(chosen, str) or chosen not in frame.action_ids):
            raise BoundaryError("native_structured_sequence", "label_catalog_binding")
        steps.append(NativeStructuredStep(frame, unit, token, reset, advance, chosen))
        previous, continuity = unit, token
    # Canonical identity is source bytes, not inferred Human/Agent origin.
    return NativeStructuredSequence(tuple(steps), hashlib.sha256(raw).hexdigest(), raw)
