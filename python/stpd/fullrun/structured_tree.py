"""Shared pure typed public tree and reference graph; no source-schema conversion."""

from __future__ import annotations

import math
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes

from ..canonical import semantic_hash
from .structured_inputs import (
    IDENTIFIERS,
    MAX_CANDIDATES,
    MAX_DEPTH,
    MAX_EDGES,
    MAX_FIELD_BYTES,
    MAX_NODES,
    MAX_TEXT_BYTES,
    METADATA,
    StructuredCandidate,
    StructuredFrame,
    StructuredNode,
    _ordered,
    _Reference,
)


def build_structured_frame(
    roots: dict[str, Any],
    visible: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    *,
    projection_id: str,
    projection_version: str,
    metadata: frozenset[str] = METADATA,
    reference_roots: dict[str, Any] | None = None,
    candidate_digest: str | None = None,
    ordered_arguments: bool = False,
) -> StructuredFrame:
    """Caller supplies qualified public fields and complete source-owned actions.

    ``visible`` is historical naming; it is exactly the caller's authorized
    referent set, without a second visibility or gameplay legality engine.
    """

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
                elif key not in metadata:
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
                if key in metadata:
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

    def clean_selection(value: Any) -> Any:
        if isinstance(value, _Reference):
            return value
        if isinstance(value, dict):
            return {key: clean_selection(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            return ("bag", tuple(clean_selection(item) for item in value))
        return clean(value)

    relation_values = {
        key: clean_selection(value) for key, value in (reference_roots or {}).items()
    }
    state = clean({key: value for key, value in roots.items() if key not in relation_values})
    state.update(relation_values)
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
            "projection": projection_id,
            "version": projection_version,
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
    if not 0 <= len(actions) <= MAX_CANDIDATES:
        raise BoundaryError("structured_input", "candidate_budget")
    candidates = []
    for action in actions:
        roles = []
        subject = action["subject_referent_id"]
        if subject is not None:
            roles.append(("subject", anchors[subject]))
        for ordinal, argument in enumerate(action["arguments"]):
            if argument["referent_id"] not in visible_ids:
                raise BoundaryError("structured_input", "candidate_reference_not_visible")
            name = (
                "argument:" + (str(ordinal) + ":" if ordered_arguments else "") + argument["role"]
            )
            roles.append((name, anchors[argument["referent_id"]]))
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
        candidate_digest or semantic_hash([item.action_id for item in candidates]),
        tuple((identifier, anchors[identifier]) for identifier in registry),
        tuple(entity_members),
    )
