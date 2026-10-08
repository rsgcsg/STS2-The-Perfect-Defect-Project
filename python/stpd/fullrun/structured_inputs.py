"""S0 observation-only public tree projection; opaque IDs are binding data only.

Arrays are bags unless their public contract declares order or they are native
hand/orb/potion slots. Referents are always a bag. Trees retain typed null,
boolean, integer and real values; missing fields do not become numeric zero.
References add typed graph edges, never ID embeddings. C never enters this tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes

from .text_menu_inputs import V2_SNAPSHOT_SCHEMA, validate_text_menu_v2_snapshot

INPUT_ID = "stpd-structured-observation-only-v1"
PROJECTION_VERSION = "1.0.0"
MAX_FIELD_BYTES = 4096
MAX_TEXT_BYTES = 1024 * 1024
MAX_NODES = 65536
MAX_EDGES = 262144
MAX_CANDIDATES = 16384
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_DEPTH = 48
NODE_TYPES = ("object", "bag", "ordered", "text", "null", "bool", "int", "real", "ref")
# Relationship directions are distinct; there is exactly one message layer.
RELATIONS = ("child", "parent", "reference", "referenced_by")
IDENTIFIERS = frozenset({"entity_id", "referent_id", "slot_entity_id", "interaction_id"})
METADATA = IDENTIFIERS | frozenset(
    {
        "combat_id",
        "action_keys",
        "evidence",
        "non_claims",
        "sources",
        "observation_basis",
        "action_support",
        "blocked_reason",
        "native_decision_status",
        "completeness",
        "inventory_index",
        "ordinal",
        "sequence",
        "observed_at",
        "snapshot_id",
        "runtime_id",
        "runtime_instance_id",
        "environment_fingerprint",
        "generation",
        "seed",
        "control",
        "controller",
        "receipt",
        "request_id",
        "history",
        "previous_actual_action",
        "public_feedback",
        "last_action",
        "last_action_text",
        "reason_code",
        "request_result",
        "candidate_digest",
        "candidate_count",
        "capture_time",
        "captured_at",
        "session_id",
        "run_id",
    }
)
ORDERED_FIELDS = frozenset({"hand", "orbs", "orb_slots", "potions", "potion_slots"})
ORDERED_SEMANTICS = frozenset(
    {
        "ordered",
        "ordered_sequence",
        "slot_order",
        "source_order",
        "public_display_order",
        "native_display_order",
        "native_displayed_order",
        "native_slot_order",
        "revealed_native_order",
        "current_hand_order",
        "current_menu_source_order",
    }
)


@dataclass(frozen=True)
class StructuredNode:
    field: str
    kind: str
    text: str = ""
    # known, bounded signed value, signed-log1p; type is separately embedded.
    numeric: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class StructuredCandidate:
    action_id: str
    kind: str
    verb: str
    label: str
    roles: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class StructuredFrame:
    nodes: tuple[StructuredNode, ...]
    edges: tuple[tuple[int, int, int], ...]
    candidates: tuple[StructuredCandidate, ...]
    state_digest: str
    candidate_digest: str
    # Binding only; never encoded, hashed into state, or treated as native operands.
    ref_rows: tuple[tuple[str, int], ...]
    # Entity-local field aggregation precedes the single relation layer.
    entity_members: tuple[tuple[int, int], ...]

    @property
    def action_ids(self) -> tuple[str, ...]:
        return tuple(item.action_id for item in self.candidates)


@dataclass(frozen=True)
class _Reference:
    identifier: str


def _ordered(field: str, owner: dict[str, Any] | None) -> bool:
    semantics = None if owner is None else owner.get("ordering_semantics")
    if semantics == "unordered_multiset" or (owner and owner.get("kind") == "run_deck"):
        return False
    return field in ORDERED_FIELDS or semantics in ORDERED_SEMANTICS


def project_structured_snapshot(snapshot: dict[str, Any]) -> StructuredFrame:
    """Validate and directly tensorize current public source structure.

    Returned indices bind this capture only. The state digest has no C, raw ID,
    receipt or transport identity. New captures must call this again even when
    their state digest equals the most recently consumed observation.
    """
    if not isinstance(snapshot, dict) or len(json_bytes(snapshot)) > MAX_SNAPSHOT_BYTES:
        raise BoundaryError("structured_input", "snapshot_size_or_type")
    if (
        isinstance(snapshot.get("menu_actions"), dict)
        and isinstance(snapshot["menu_actions"].get("actions"), list)
        and len(snapshot["menu_actions"]["actions"]) > MAX_CANDIDATES
    ):
        raise BoundaryError("structured_input", "candidate_budget")
    validate_text_menu_v2_snapshot(snapshot, allow_observation_only=True)
    if snapshot["schema"] != V2_SNAPSHOT_SCHEMA:
        raise BoundaryError("structured_input", "source_schema_mismatch")
    if snapshot["completeness"].get("missing") != []:
        raise BoundaryError("structured_input", "required_public_scope_missing")
    visible = [
        {
            key: ref.get(key)
            for key in ("referent_id", "role", "kind", "label", "state", "properties")
        }
        for ref in snapshot["referents"]
        if ref["state"]["visible"]
    ]
    roots: dict[str, Any] = {
        "CURRENT_PERSISTENT": (
            None if snapshot["persistent"] is None else snapshot["persistent"]["content"]
        ),
        "CURRENT_PAGE": {
            key: snapshot["interaction"].get(key) for key in ("kind", "stage", "prompt", "content")
        },
        "CURRENT_MENU": {
            "cursor": snapshot["menu"]["cursor"],
            "selection": snapshot["menu"]["selection"],
        },
    }

    from .structured_tree import build_structured_frame

    selection = {"CURRENT_MENU": {"cursor": snapshot["menu"]["cursor"], "selection": [
        {"role": item["role"], "selected_referent_ref": _Reference(item["referent_id"])}
        for item in snapshot["menu"]["selection"]]}}
    return build_structured_frame(roots, visible, snapshot["menu_actions"]["actions"],
                                  projection_id=INPUT_ID, projection_version=PROJECTION_VERSION,
                                  reference_roots=selection)
