"""S0 observation-only public tree projection; opaque IDs are binding data only.

Arrays are bags unless their public contract declares order or they are native
hand/orb/potion slots. Referents are always a bag. Trees retain typed null,
boolean, integer and real values; missing fields do not become numeric zero.
References add typed graph edges, never ID embeddings. C never enters this tree.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes

from ..canonical import semantic_hash
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

    def public_scope(value: Any, depth: int = 0) -> Any:
        """Exclude whole program-metadata subtrees before declaration discovery.

        Declaration keys survive only as binding data in an authorized public
        object. Dropped receipts/history/control can neither introduce anchors
        nor change which public description wins an existing ID collision.
        """
        if depth > MAX_DEPTH:
            raise BoundaryError("structured_input", "depth_limit")
        if isinstance(value, dict):
            output: dict[str, Any] = {}
            for key, item in value.items():
                if key in IDENTIFIERS:
                    if item is not None and (not isinstance(item, str) or not item):
                        raise BoundaryError("structured_input", "invalid_public_declaration")
                    # Never discover declarations inside a declaration value.
                    output[key] = item
                elif key not in METADATA:
                    output[key] = public_scope(item, depth + 1)
            return output
        if isinstance(value, list):
            return [public_scope(item, depth + 1) for item in value]
        return value

    roots = public_scope(roots)
    visible = public_scope(visible)
    registry: dict[str, dict[str, Any]] = {}

    def collect(value: Any, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            raise BoundaryError("structured_input", "depth_limit")
        if isinstance(value, dict):
            for key in IDENTIFIERS:
                identifier = value.get(key)
                if isinstance(identifier, str):
                    old = registry.get(identifier)
                    # Rich descriptions win deterministically; binding is not a feature.
                    if old is None or (len(json_bytes(value)), json_bytes(value)) > (
                        len(json_bytes(old)),
                        json_bytes(old),
                    ):
                        registry[identifier] = value
            for item in value.values():
                collect(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                collect(item, depth + 1)

    collect(roots)
    collect(visible)
    visible_ids = {ref["referent_id"] for ref in visible}
    aliases = {identifier: {identifier} for identifier in registry}

    def alias(identifiers: tuple[str, ...]) -> None:
        if identifiers:
            joined = set().union(*(aliases[identifier] for identifier in identifiers))
            for identifier in joined:
                aliases[identifier] = joined

    def collect_aliases(value: Any) -> None:
        if isinstance(value, dict):
            alias(
                tuple(value[key] for key in sorted(IDENTIFIERS) if isinstance(value.get(key), str))
            )
            for item in value.values():
                collect_aliases(item)
        elif isinstance(value, list):
            for item in value:
                collect_aliases(item)

    collect_aliases(roots)
    collect_aliases(visible)
    for ref in visible:
        properties = ref["properties"]
        if isinstance(properties, dict):
            alias(
                (
                    ref["referent_id"],
                    *(
                        properties[key]
                        for key in sorted(IDENTIFIERS)
                        if isinstance(properties.get(key), str)
                    ),
                )
            )
    # Python identities are ephemeral occurrence bindings, never node features.
    declarations: dict[int, tuple[str, ...]] = {}

    def clean(
        value: Any, field: str = "", owner: dict[str, Any] | None = None, depth: int = 0
    ) -> Any:
        if depth > MAX_DEPTH:
            raise BoundaryError("structured_input", "depth_limit")
        if isinstance(value, dict):
            output: dict[str, Any] = {}
            for key in sorted(value):
                if key in METADATA:
                    continue
                item = value[key]
                if key.endswith(("_entity_id", "_referent_id")):
                    if item is not None and (not isinstance(item, str) or item not in registry):
                        raise BoundaryError("structured_input", "unresolved_public_reference")
                    output[key.removesuffix("_id") + "_ref"] = (
                        None if item is None else _Reference(item)
                    )
                elif key.endswith(("_entity_ids", "_referent_ids")):
                    if not isinstance(item, list) or any(
                        not isinstance(x, str) or x not in registry for x in item
                    ):
                        raise BoundaryError("structured_input", "unresolved_public_reference")
                    output[key.removesuffix("_ids") + "_refs"] = (
                        "ordered" if _ordered(key, value) else "bag",
                        tuple(_Reference(x) for x in item),
                    )
                else:
                    output[key] = clean(item, key, value, depth + 1)
            declarations[id(output)] = tuple(
                value[key] for key in sorted(IDENTIFIERS) if isinstance(value.get(key), str)
            )
            return output
        if isinstance(value, list):
            return (
                "ordered" if _ordered(field, owner) else "bag",
                tuple(clean(item, field, owner, depth + 1) for item in value),
            )
        if isinstance(value, str):
            if len(value.encode("utf-8")) > MAX_FIELD_BYTES:
                raise BoundaryError("structured_input", "field_text_limit")
            return _Reference(value) if value in registry else value
        if value is None or type(value) is bool:
            return value
        if type(value) in {int, float}:
            try:
                finite = math.isfinite(value)
            except OverflowError as error:
                raise BoundaryError("structured_input", "numeric_range") from error
            if not finite:
                raise BoundaryError("structured_input", "nonfinite_number")
            return value
        raise BoundaryError("structured_input", "unsupported_public_value")

    # Declaration IDs are omitted; selection is a public role-bearing relation.
    roots["CURRENT_MENU"]["selection"] = [
        {"role": item["role"], "selected_referent_ref": _Reference(item["referent_id"])}
        for item in snapshot["menu"]["selection"]
    ]

    def clean_selection(value: Any) -> Any:
        if isinstance(value, _Reference):
            return value
        if isinstance(value, dict):
            return {key: clean_selection(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            return ("bag", tuple(clean_selection(item) for item in value))
        return clean(value)

    selected = clean_selection(roots.pop("CURRENT_MENU"))
    state = clean(roots)
    state["CURRENT_MENU"] = selected
    descriptions = {identifier: clean(value) for identifier, value in registry.items()}

    def canonical(value: Any, visited: frozenset[str] = frozenset()) -> Any:
        if isinstance(value, _Reference):
            target = descriptions[value.identifier]
            if value.identifier in visited:
                # A cycle is a typed relation, not a learned semantic hash token.
                return {"$cycle": local(target)}
            return {"$reference": canonical(target, visited | {value.identifier})}
        if isinstance(value, dict):
            return {key: canonical(item, visited) for key, item in sorted(value.items())}
        if isinstance(value, tuple):
            kind, items = value
            entries = [canonical(item, visited) for item in items]
            if kind == "bag":
                entries.sort(key=json_bytes)
            return {"$" + kind: entries}
        return {"$" + ("null" if value is None else type(value).__name__): value}

    def local(value: Any) -> Any:
        if isinstance(value, _Reference):
            return {"$reference": True}
        if isinstance(value, dict):
            return {key: local(item) for key, item in sorted(value.items())}
        if isinstance(value, tuple):
            kind, items = value
            entries = [local(item) for item in items]
            if kind == "bag":
                entries.sort(key=json_bytes)
            return {"$" + kind: entries}
        return {"$" + ("null" if value is None else type(value).__name__): value}

    # Every visible instance is retained, including equal-name/equal-value multiplicity.
    semantic_refs = [canonical(descriptions[ref["referent_id"]]) for ref in visible]
    semantic_refs.sort(key=json_bytes)
    state_digest = semantic_hash(
        {
            "projection": INPUT_ID,
            "version": PROJECTION_VERSION,
            "state": canonical(state),
            "referents": semantic_refs,
        }
    )
    nodes: list[StructuredNode] = []
    edges: list[tuple[int, int, int]] = []
    anchors: dict[str, int] = {}
    entity_members: list[tuple[int, int]] = []
    text_bytes = 0

    def node(field: str, kind: str, text: str = "", number: float | int | None = None) -> int:
        nonlocal text_bytes
        for part in (field, text):
            size = len(part.encode("utf-8"))
            if size > MAX_FIELD_BYTES:
                raise BoundaryError("structured_input", "field_text_limit")
            text_bytes += size
        if text_bytes > MAX_TEXT_BYTES or len(nodes) >= MAX_NODES:
            raise BoundaryError("structured_input", "frame_budget")
        numeric = (0.0, 0.0, 0.0)
        if number is not None:
            v = float(number)
            numeric = (1.0, v / (1.0 + abs(v)), math.copysign(math.log1p(abs(v)), v))
        nodes.append(StructuredNode(field, kind, text, numeric))
        return len(nodes) - 1

    def edge(source: int, target: int, relation: int) -> None:
        if len(edges) + 2 > MAX_EDGES:
            raise BoundaryError("structured_input", "edge_budget")
        edges.extend(((source, target, relation), (target, source, relation + 1)))

    def tree(
        value: Any,
        field: str,
        parent: int | None = None,
        anchor: int | None = None,
        owner: int | None = None,
    ) -> int:
        if isinstance(value, _Reference):
            index = node(field, "ref")
            edge(index, anchors[value.identifier], 2)
            if owner is not None:
                edge(owner, anchors[value.identifier], 2)
        elif isinstance(value, dict):
            index = node(field, "object") if anchor is None else anchor
            for key, item in sorted(value.items()):
                tree(item, field + "/" + key, index, owner=owner)
            for identifier in declarations.get(id(value), ()):
                if anchors[identifier] != index:
                    edge(index, anchors[identifier], 2)
        elif isinstance(value, tuple):
            kind, items = value
            index = node(field, kind)
            # Bag iteration order is computation layout only, never a feature.
            for ordinal, item in enumerate(items):
                child = tree(item, field + "/$item", index, owner=owner)
                if kind == "ordered":
                    position = tree(ordinal, field + "/$public_order", child, owner=owner)
                    identifiers = (
                        (item.identifier,)
                        if isinstance(item, _Reference)
                        else declarations.get(id(item), ())
                        if isinstance(item, dict)
                        else ()
                    )
                    position_targets = set().union(
                        *(aliases[identifier] for identifier in identifiers)
                    )
                    for identifier in sorted(position_targets):
                        target = anchors[identifier]
                        edge(target, position, 0)
                        if (target, position) not in entity_members:
                            entity_members.append((target, position))
        else:
            kind = (
                "null"
                if value is None
                else "bool"
                if type(value) is bool
                else "int"
                if type(value) is int
                else "real"
                if type(value) is float
                else "text"
            )
            index = node(
                field,
                kind,
                value if kind == "text" else "",
                int(value) if kind == "bool" else value if kind in {"int", "real"} else None,
            )
        if parent is not None:
            edge(parent, index, 0)
        if owner is not None and index != owner:
            entity_members.append((owner, index))
        return index

    root = node("$global", "object")
    for identifier in registry:
        anchors[identifier] = node("$entity", "object")
    for identifier, description in descriptions.items():
        tree(description, "$entity", anchor=anchors[identifier], owner=anchors[identifier])
    for ref in visible:
        edge(root, anchors[ref["referent_id"]], 0)
    tree(state, "$state", root)
    actions = snapshot["menu_actions"]["actions"]
    if not 0 <= len(actions) <= MAX_CANDIDATES:
        raise BoundaryError("structured_input", "candidate_budget")
    candidates = []
    for action in actions:
        roles = []
        subject = action["subject_referent_id"]
        if subject is not None:
            roles.append(("subject", anchors[subject]))
        for argument in action["arguments"]:
            if argument["referent_id"] not in visible_ids:
                raise BoundaryError("structured_input", "candidate_reference_not_visible")
            roles.append(("argument:" + argument["role"], anchors[argument["referent_id"]]))
        for part in (action["kind"], action["verb"], action["label"], *(r for r, _ in roles)):
            size = len(part.encode("utf-8"))
            if size > MAX_FIELD_BYTES:
                raise BoundaryError("structured_input", "field_text_limit")
            text_bytes += size
        if text_bytes > MAX_TEXT_BYTES:
            raise BoundaryError("structured_input", "frame_budget")
        candidates.append(
            StructuredCandidate(
                action["action_id"], action["kind"], action["verb"], action["label"], tuple(roles)
            )
        )
    return StructuredFrame(
        tuple(nodes),
        tuple(edges),
        tuple(candidates),
        state_digest,
        semantic_hash([item.action_id for item in candidates]),
        tuple((identifier, anchors[identifier]) for identifier in registry),
        tuple(entity_members),
    )
