"""Pure public-semantic projection shared by live inputs and verified evidence.

Opaque references resolve only against captured public values. This module does
not load evidence archives, application services, or research training recipes.
"""

from __future__ import annotations

from typing import Any

from spireagent.json_boundary import BoundaryError

from ..canonical import canonical_json, semantic_hash

# These are capture/identity metadata, not player semantics. The originals remain
# in the immutable evidence. Unknown hidden/native fields are rejected downstream.
_METADATA = frozenset(
    {
        "entity_id",
        "referent_id",
        "interaction_id",
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
        "slot_entity_id",
        "inventory_index",
        "focused",
        "hovered",
    }
)



class _SemanticProjection:
    """Project public values; resolve opaque references only from captured entities."""

    def __init__(self, values: list[Any]) -> None:
        self.entities: dict[str, dict[str, Any]] = {}
        for value in values:
            self._collect(value)

    def _collect(self, value: Any) -> None:
        if isinstance(value, dict):
            identifier = value.get("entity_id")
            if isinstance(value.get("interaction_id"), str) and "kind" in value:
                self.entities[value["interaction_id"]] = {
                    "kind": value["kind"],
                    **value.get("content", {}),
                }
            if isinstance(value.get("referent_id"), str):
                # Connector explicitly serializes absent public properties as
                # null. This adds no inferred facts; the visible referent still
                # keeps its role and state.
                properties = value.get("properties")
                self.entities[value["referent_id"]] = {
                    "role": value.get("role"),
                    **({} if properties is None else properties),
                    "state": value.get("state", {}),
                }
            if isinstance(value.get("slot_entity_id"), str):
                self.entities[value["slot_entity_id"]] = value
            if isinstance(identifier, str):
                # Prefer richer normal inspectable descriptions over compact fields.
                previous = self.entities.get(identifier, {})
                self.entities[identifier] = {**previous, **value}
            for item in value.values():
                self._collect(item)
        elif isinstance(value, list):
            for item in value:
                self._collect(item)

    def clean(self, value: Any, *, resolving: bool = False) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if key in _METADATA:
                    continue
                if key.endswith("_entity_ids") or key.endswith("_referent_ids"):
                    result[key.removesuffix("_ids") + "_values"] = [
                        self.reference(x, resolving) for x in item
                    ]
                elif key.endswith("_entity_id") or key.endswith("_referent_id"):
                    result[key.removesuffix("_id") + "_value"] = (
                        self.reference(item, resolving) if item is not None else None
                    )
                else:
                    result["visible_label" if key == "label" else key] = self.clean(
                        item, resolving=resolving
                    )
            if (
                value.get("ordering_semantics") == "unordered_multiset"
                or value.get("kind") == "run_deck"
            ) and isinstance(result.get("cards"), list):
                result["cards"] = sorted(result["cards"], key=canonical_json)
            return result
        if isinstance(value, list):
            return [self.clean(item, resolving=resolving) for item in value]
        if isinstance(value, str) and value in self.entities and not resolving:
            return self.entity(value)
        return value

    def reference(self, identifier: str, resolving: bool) -> dict[str, Any]:
        if not resolving:
            return self.entity(identifier)
        if identifier not in self.entities:
            raise BoundaryError("platform_projection", "missing_referent_semantics")

        # An entity can refer to itself/its owner. Keep a semantic reference rather
        # than recursively expand a cycle or leak the runtime handle into features.
        def leaf(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: leaf(item)
                    for key, item in value.items()
                    if key not in _METADATA
                    and not key.endswith(
                        ("_entity_id", "_entity_ids", "_referent_id", "_referent_ids")
                    )
                }
            if isinstance(value, list):
                return [leaf(item) for item in value]
            return value

        return {"semantic_ref": semantic_hash(leaf(self.entities[identifier]))}

    def entity(self, identifier: str | None) -> dict[str, Any]:
        if identifier is None:
            return {}
        if identifier not in self.entities:
            raise BoundaryError("platform_projection", "missing_referent_semantics")
        result: dict[str, Any] = self.clean(self.entities[identifier], resolving=True)
        return result
