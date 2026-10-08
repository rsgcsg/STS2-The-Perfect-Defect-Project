"""Direct native-logical full-reference projection; no legacy snapshot fabrication."""

from __future__ import annotations

import hashlib
import struct
from typing import Any

from spireagent.json_boundary import BoundaryError, digest, json_bytes, object_fields

from ..canonical import semantic_hash
from .structured_inputs import (
    IDENTIFIERS,
    MAX_CANDIDATES,
    MAX_EDGES,
    MAX_FIELD_BYTES,
    MAX_NODES,
    MAX_SNAPSHOT_BYTES,
    MAX_TEXT_BYTES,
    METADATA,
    StructuredFrame,
)
from .structured_tree import build_structured_frame

PROFILE = "native-logical-v1"
OBSERVATION_SCHEMA = "sts2.player-environment/native-logical-observation-1"
INPUT_ID = "stpd-native-structured-full-reference-v1"
PROJECTION_VERSION = "1.0.0"
SCOPE = ("persistent", "interaction", "referents", "catalog")
# Genuine currently public terminal/summary facts are observation, not a label.
NATIVE_METADATA = (METADATA - {"run_outcome"}) | frozenset(
    {
        "owner_id",
        "occurrence_id",
        "binding_revision",
        "focus_occurrence",
        "revision",
        "catalog_ref",
        "scope_id",
        "stream_generation",
        "publication_index",
        "source_index",
        "capture_id",
        "capture_sha256",
        "consumption_id",
        "state_version",
        "continuity_token",
        "previous_consumption_id",
        "acquisition_id",
        "previous_action",
        "feedback",
        "native_operand",
        "native_object_id",
        "hidden_rng",
        "hidden_draw_order",
        "future_state",
        "teacher_label",
        "teacher_confidence",
        "chosen_action_id",
        "chosen_action",
        "outcome_label",
    }
)
INPUT_SPEC_BODY = {
    "schema": "stpd/native-structured-input-spec-v1",
    "id": INPUT_ID,
    "version": PROJECTION_VERSION,
    "source_schema": OBSERVATION_SCHEMA,
    "profile": PROFILE,
    "eager_scope": list(SCOPE),
    "history_mode": "full_reference",
    "consumption_mode": "once_per_occurrence",
    "I": False,
    "F": False,
    "public_roots": [
        "persistent.content",
        "interaction.kind/stage/prompt/content",
        "status",
        "provided_public_referents",
        "owner_occurrence.focus_referent_id",
    ],
    "arrays": "explicit_public_order_else_bag-v1",
    "referents": "all_authorized_bag",
    "candidate_arguments": "native_ordered_role_positions",
    "candidate_encoder": False,
    "metadata_exclusions": sorted(NATIVE_METADATA | IDENTIFIERS),
    "reset": "explicit_new_continuity_segment",
    "empty_catalog": "consume_without_label_or_score",
    "limits": {
        "max_input_bytes": MAX_SNAPSHOT_BYTES,
        "max_nodes": MAX_NODES,
        "max_edges": MAX_EDGES,
        "max_field_bytes": MAX_FIELD_BYTES,
        "max_feature_text_bytes": MAX_TEXT_BYTES,
        "max_candidates": MAX_CANDIDATES,
    },
}
INPUT_SPEC = {
    "id": INPUT_ID,
    "version": PROJECTION_VERSION,
    "sha256": semantic_hash(INPUT_SPEC_BODY),
}
PROJECTION = {
    "id": INPUT_ID,
    "version": PROJECTION_VERSION,
    "source_schema": OBSERVATION_SCHEMA,
    "profile": PROFILE,
    "I": False,
    "F": False,
}
ACTION_FIELDS = {
    "action_id",
    "kind",
    "verb",
    "label",
    "subject_referent_id",
    "arguments",
    "effect_domain",
}
OBSERVATION_FIELDS = {
    "protocol_version",
    "schema",
    "input_profile",
    "snapshot_id",
    "revision",
    "observed_at",
    "status",
    "persistent",
    "interaction",
    "referents",
    "completeness",
    "session",
    "information_policy",
    "owner_occurrence",
    "catalog",
}


def _text(value: Any, *, empty: bool = False, maximum: int = 65536) -> str:
    if not isinstance(value, str) or not empty and not value:
        raise BoundaryError("native_structured_input", "invalid_text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise BoundaryError("native_structured_input", "invalid_unicode") from error
    if len(encoded) > maximum:
        raise BoundaryError("native_structured_input", "field_text_limit")
    return value


def native_catalog_digest(actions: list[dict[str, Any]]) -> str:
    """Mechanical consumer validation of the Connector-owned binary digest grammar."""
    if not isinstance(actions, list) or len(actions) > 65536:
        raise BoundaryError("native_structured_input", "catalog_size")
    checksum = hashlib.sha256(b"sts2.native-logical.catalog.v1\x00")

    def number(value: int) -> None:
        checksum.update(struct.pack(">I", value))

    def text(value: Any) -> None:
        raw = _text(value, empty=True).encode("utf-8")
        number(len(raw))
        checksum.update(raw)

    number(len(actions))
    identifiers: set[str] = set()
    for raw in actions:
        action = object_fields(raw, ACTION_FIELDS, "native_structured_input.action")
        identifier = _text(action["action_id"], empty=True)
        if identifier in identifiers:
            raise BoundaryError("native_structured_input", "duplicate_action_id")
        identifiers.add(identifier)
        for key in ("action_id", "kind", "verb", "label"):
            text(action[key])
        subject = action["subject_referent_id"]
        checksum.update(b"\x00" if subject is None else b"\x01")
        if subject is not None:
            text(subject)
        arguments = action["arguments"]
        if not isinstance(arguments, list) or len(arguments) > 65536:
            raise BoundaryError("native_structured_input", "arguments_required")
        number(len(arguments))
        roles: set[str] = set()
        for raw_argument in arguments:
            argument = object_fields(
                raw_argument, {"role", "referent_id"}, "native_structured_input.argument"
            )
            role = _text(argument["role"], empty=True)
            if role in roles:
                raise BoundaryError("native_structured_input", "duplicate_argument_role")
            roles.add(role)
            text(role)
            text(argument["referent_id"])
        text(action["effect_domain"])
    return checksum.hexdigest()


def qualified_occurrence(observation: dict[str, Any]) -> tuple[str, str, str, str, str | None]:
    owner = object_fields(
        observation["owner_occurrence"],
        {"owner_id", "occurrence_id", "binding_revision", "focus_referent_id", "focus_occurrence"},
        "native_structured_input.owner",
    )
    for key in ("owner_id", "occurrence_id", "binding_revision"):
        _text(owner[key])
    for key in ("focus_referent_id", "focus_occurrence"):
        if owner[key] is not None:
            _text(owner[key])
    return (
        _text(observation["catalog"]["stream_generation"]),
        _text(observation["snapshot_id"]),
        owner["occurrence_id"],
        owner["binding_revision"],
        owner["focus_occurrence"],
    )


def project_native_structured(
    observation: dict[str, Any], actions: list[dict[str, Any]]
) -> StructuredFrame:
    """Direct public fields; complete C is binding/scoring data, never writer input."""
    observation = object_fields(
        observation, OBSERVATION_FIELDS, "native_structured_input.observation"
    )
    if len(json_bytes({"observation": observation, "catalog": actions})) > MAX_SNAPSHOT_BYTES:
        raise BoundaryError("native_structured_input", "input_byte_limit")
    policy = object_fields(
        observation["information_policy"],
        {"id", "scope", "includes_hidden_information", "unknown_field_behavior"},
        "native_structured_input.information_policy",
    )
    session = object_fields(
        observation["session"],
        {"runtime_instance_id", "environment_fingerprint"},
        "native_structured_input.session",
    )
    for value in (
        *session.values(),
        policy["id"],
        policy["scope"],
        policy["unknown_field_behavior"],
    ):
        _text(value)
    completeness = object_fields(
        observation["completeness"],
        {"status", "included", "missing", "full_reference_complete"},
        "native_structured_input.scope",
    )
    if (
        observation["protocol_version"] != "1.0.0"
        or observation["schema"] != OBSERVATION_SCHEMA
        or observation["input_profile"] != PROFILE
        or observation["status"] not in {"interactive", "settling", "terminal"}
        or completeness
        != {
            "status": "complete",
            "included": list(SCOPE),
            "missing": [],
            "full_reference_complete": True,
        }
        or policy["includes_hidden_information"] is not False
        or completeness["full_reference_complete"] is not True
        or type(observation["revision"]) is not int
        or observation["revision"] < 1
    ):
        raise BoundaryError("native_structured_input", "complete_native_reference_required")
    descriptor = object_fields(
        observation["catalog"],
        {
            "catalog_ref",
            "snapshot_id",
            "status",
            "total_count",
            "digest",
            "ordering_semantics",
            "access_methods",
            "scope_id",
            "stream_generation",
        },
        "native_structured_input.catalog",
    )
    checksum = native_catalog_digest(actions)
    if (
        descriptor["status"] != "complete"
        or descriptor["snapshot_id"] != observation["snapshot_id"]
        or type(descriptor["total_count"]) is not int
        or descriptor["total_count"] != len(actions)
        or descriptor["digest"] != checksum
        or len(actions) > MAX_CANDIDATES
    ):
        raise BoundaryError("native_structured_input", "complete_catalog_binding_required")
    digest(descriptor["digest"], "native_structured_input.catalog_digest")
    qualified_occurrence(observation)
    refs = observation["referents"]
    if not isinstance(refs, list):
        raise BoundaryError("native_structured_input", "referents_required")
    identifiers: set[str] = set()
    referents = []
    for raw in refs:
        ref = object_fields(
            raw,
            {"referent_id", "role", "kind", "label", "state", "properties_schema", "properties"},
            "native_structured_input.referent",
        )
        identifier = _text(ref["referent_id"])
        if identifier in identifiers:
            raise BoundaryError("native_structured_input", "duplicate_referent")
        identifiers.add(identifier)
        _text(ref["role"])
        _text(ref["kind"])
        if ref["label"] is not None:
            _text(ref["label"], empty=True)
        state = object_fields(
            ref["state"],
            {"visible", "enabled", "selected", "focused", "observation_basis"},
            "native_structured_input.ref_state",
        )
        if type(state["visible"]) is not bool or any(
            state[key] is not None and type(state[key]) is not bool
            for key in ("enabled", "selected", "focused")
        ):
            raise BoundaryError("native_structured_input", "public_referent_state_required")
        _text(state["observation_basis"])
        referents.append(
            {
                key: ref[key]
                for key in ("referent_id", "role", "kind", "label", "state", "properties")
            }
        )
    focus = observation["owner_occurrence"]["focus_referent_id"]
    if focus is not None and focus not in identifiers:
        raise BoundaryError("native_structured_input", "public_focus_binding_required")
    for action in actions:
        if (
            action["kind"] != "native_input"
            or action["subject_referent_id"] is not None
            and action["subject_referent_id"] not in identifiers
            or any(arg["referent_id"] not in identifiers for arg in action["arguments"])
        ):
            raise BoundaryError("native_structured_input", "public_action_binding_required")
    persistent = observation["persistent"]
    if persistent is not None:
        persistent = object_fields(
            persistent, {"content_schema", "content"}, "native_structured_input.persistent"
        )["content"]
    page = observation["interaction"]
    if page is not None:
        page = object_fields(
            page,
            {
                "interaction_id",
                "kind",
                "stage",
                "prompt",
                "content_schema",
                "content",
                "capabilities",
            },
            "native_structured_input.page",
        )
        page = {key: page[key] for key in ("kind", "stage", "prompt", "content")}
    roots = {
        "CURRENT_PERSISTENT": persistent,
        "CURRENT_PAGE": page,
        "CURRENT_STATUS": observation["status"],
        "CURRENT_NATIVE_FOCUS": {"focus_referent_id": focus},
    }
    return build_structured_frame(
        roots,
        referents,
        actions,
        projection_id=INPUT_ID,
        projection_version=PROJECTION_VERSION,
        metadata=NATIVE_METADATA,
        candidate_digest=checksum,
        ordered_arguments=True,
    )
