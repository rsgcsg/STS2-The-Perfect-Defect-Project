"""Provisional v2 current reward/potion page input; no remembered page or target.

The Connector owns the complete native-profile menu and delivery. This projector
keeps current reward/card order, current potion slot and popup-control order.
Opaque IDs remain only in the exact catalog-position execution sidecar. A popup
Use is one current button action; any later target page is a new observation.
This is neither a Runtime adapter nor training-data admission.
"""

from __future__ import annotations

from typing import Any

from spireagent.json_boundary import BoundaryError

from ..canonical import canonical_json, semantic_hash
from .platform_bundle3 import _SemanticProjection
from .public_compaction import compact_public_state
from .representation import reject_leakage
from .reward_page_inputs import RewardActionBinding, RewardPageInput, _object

INPUT_PROFILE = "ordinary-reward-potion-page-v2"
SNAPSHOT_SCHEMA = "sts2.player-environment/ordinary-reward-potion-page-snapshot-2"
VERSION = "stpd-ordinary-reward-potion-current-page-compact-v2"
IDENTITY = {
    "version": VERSION,
    "profile": "ordinary_reward_potion_current_page_compact",
    "source_schema": SNAPSHOT_SCHEMA,
    "input_profile": INPUT_PROFILE,
    "status": "provisional",
}
_BOUND_ACTION_SCHEMA = "sts2.player-environment/bound-actions-1"


def _rows(value: Any, code: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise BoundaryError("reward_potion_input", code)
    return [_object(row, code) for row in value]


def _options(
    surface: dict[str, Any], kind: str,
) -> tuple[dict[str, tuple[int, str, int, str]], set[str]]:
    """Map an exact current referent to (group, kind, ordinal, public verb)."""
    positions: dict[str, tuple[int, str, int, str]] = {}
    enabled: set[str] = set()

    def add(identifier: Any, group: int, option_kind: str, ordinal: int,
            verb: str, available: bool = True) -> None:
        if not isinstance(identifier, str) or not identifier or identifier in positions:
            raise BoundaryError("reward_potion_input", "duplicate_or_missing_current_target")
        positions[identifier] = (group, option_kind, ordinal, verb)
        if available:
            enabled.add(identifier)

    if kind == "potion_popup":
        if any(key in surface for key in (
                "direct_combat_use", "use_target_entity_ids", "rewards", "cards",
                "alternatives", "alternative_effects", "openable_potions",
                "discardable_potions", "can_proceed", "proceed_skips_remaining_rewards")):
            raise BoundaryError("reward_potion_input", "noncurrent_popup_fields")
        controls = _rows(surface["controls"], "current_popup_controls_required")
        seen_kinds: set[str] = set()
        for ordinal, control in enumerate(controls):
            control_kind = control.get("kind")
            available = control.get("enabled")
            if (control_kind not in {"use", "discard", "close"}
                    or control_kind in seen_kinds or type(available) is not bool):
                raise BoundaryError("reward_potion_input", "ambiguous_current_popup_control")
            seen_kinds.add(control_kind)
            add(control.get("entity_id"), 0, control_kind, ordinal,
                "cancel" if control_kind == "close" else "activate", available)
        if ("close" not in seen_kinds
                or not any(row.get("kind") == "close" and row.get("enabled") is True
                           for row in controls)
                or surface.get("can_use") is not any(
                    row.get("kind") == "use" and row.get("enabled") is True for row in controls)
                or surface.get("can_discard") is not any(
                    row.get("kind") == "discard" and row.get("enabled") is True
                    for row in controls)):
            raise BoundaryError("reward_potion_input", "popup_enablement_mismatch")
        return positions, enabled

    openers = _rows(surface["openable_potions"], "current_potion_openers_required")
    slots: set[int] = set()
    for ordinal, opener in enumerate(openers):
        slot = opener.get("slot")
        if type(slot) is not int or slot < 0 or slot in slots:
            raise BoundaryError("reward_potion_input", "current_potion_slot_binding")
        slots.add(slot)
        add(opener.get("potion_entity_id"), 1, "potion_open", ordinal, "open")

    if kind == "reward_claim":
        if "discardable_potions" in surface or any(key in surface for key in
                ("cards", "alternatives", "alternative_effects", "controls")):
            raise BoundaryError("reward_potion_input", "legacy_or_unopened_reward_contents")
        rewards = _rows(surface["rewards"], "current_rewards_required")
        for ordinal, reward in enumerate(rewards):
            if type(reward.get("enabled")) is not bool:
                raise BoundaryError("reward_potion_input", "reward_enablement_unknown")
            add(reward.get("entity_id"), 0, "reward", ordinal, "activate", reward["enabled"])
        if type(surface.get("can_proceed")) is not bool or type(
                surface.get("proceed_skips_remaining_rewards")) is not bool:
            raise BoundaryError("reward_potion_input", "proceed_state_unknown")
        if surface["can_proceed"]:
            add("<current-page-proceed>", 2, "proceed", 0, "activate")
    elif kind == "card_reward_selection":
        if any(key in surface for key in ("rewards", "controls", "discardable_potions",
                                           "can_proceed", "proceed_skips_remaining_rewards")):
            raise BoundaryError("reward_potion_input", "unopened_reward_contents")
        cards = _rows(surface["cards"], "current_cards_required")
        selectable = surface["selectable_card_entity_ids"]
        if (not isinstance(selectable, list)
                or any(not isinstance(value, str) for value in selectable)
                or len(set(selectable)) != len(selectable)):
            raise BoundaryError("reward_potion_input", "selectable_cards_unbound")
        card_ids: set[str] = set()
        for ordinal, card in enumerate(cards):
            card_id = card.get("entity_id")
            if not isinstance(card_id, str) or not card_id:
                raise BoundaryError("reward_potion_input", "duplicate_or_missing_current_target")
            card_ids.add(card_id)
            add(card_id, 0, "card", ordinal, "select", card_id in selectable)
        if not set(selectable).issubset(card_ids):
            raise BoundaryError("reward_potion_input", "selectable_cards_unbound")
        alternatives = _rows(surface["alternatives"], "current_alternatives_required")
        effects = _rows(surface["alternative_effects"], "current_alternative_effects_required")
        if len(alternatives) != len(effects):
            raise BoundaryError("reward_potion_input", "alternative_effect_binding")
        for ordinal, (alternative, effect) in enumerate(zip(alternatives, effects, strict=True)):
            if (alternative.get("index") != ordinal or type(alternative.get("enabled")) is not bool
                    or effect.get("entity_id") != alternative.get("entity_id")
                    or effect.get("effect") != "return_to_rewards_without_claim"):
                raise BoundaryError("reward_potion_input", "alternative_effect_binding")
            add(alternative.get("entity_id"), 2, "alternative", ordinal,
                "activate", alternative["enabled"])
    else:
        raise BoundaryError("reward_potion_input", "unsupported_current_page")
    return positions, enabled


def project_reward_potion_snapshot(snapshot: dict[str, Any]) -> RewardPageInput:
    """Project one complete opted-in page; preserve exact catalog occurrences."""
    try:
        if (snapshot.get("input_profile") != INPUT_PROFILE
                or snapshot.get("schema") != SNAPSHOT_SCHEMA
                or snapshot.get("status") != "interactive"
                or snapshot["information_policy"]["includes_hidden_information"] is not False
                or snapshot["completeness"]["status"] != "complete"
                or snapshot.get("reads") != []):
            raise BoundaryError("reward_potion_input", "complete_public_v2_page_required")
        interaction = _object(snapshot["interaction"], "current_interaction_required")
        kind = interaction["kind"]
        if (kind not in {"reward_claim", "card_reward_selection", "potion_popup"}
                or interaction["content_schema"]
                != f"sts2.player-environment/surface/{kind}-3"
                or not isinstance(interaction["interaction_id"], str)
                or not interaction["interaction_id"]):
            raise BoundaryError("reward_potion_input", "current_page_contract_required")
        content = _object(interaction["content"], "current_content_required")
        surface = _object(content["surface"], "current_surface_required")
        if surface.get("kind") != kind:
            raise BoundaryError("reward_potion_input", "current_surface_kind_mismatch")
        positions, enabled = _options(surface, kind)
        catalog = _object(snapshot["bound_actions"], "complete_catalog_required")
        values = catalog["actions"]
        if (catalog.get("schema") != _BOUND_ACTION_SCHEMA
                or catalog.get("status") != "complete"
                or not isinstance(values, list) or not values
                or type(catalog.get("total_count")) is not int
                or type(catalog.get("materialized_count")) is not int
                or catalog["total_count"] != len(values)
                or catalog.get("materialized_count") != len(values)):
            raise BoundaryError("reward_potion_input", "complete_catalog_required")
        referents = _rows(snapshot["referents"], "current_referents_required")
        refs = {ref["referent_id"]: ref for ref in referents}
        if len(refs) != len(referents):
            raise BoundaryError("reward_potion_input", "unique_referents_required")
        expected_roles = {"reward": "reward", "card": "card", "alternative": "option",
                          "potion_open": "potion", "use": "control", "discard": "control",
                          "close": "control"}
        for target, (_, option_kind, _, _) in positions.items():
            if option_kind == "proceed":
                continue
            ref = refs.get(target)
            properties = ref.get("properties") if isinstance(ref, dict) else None
            property_key = "potion_entity_id" if option_kind == "potion_open" else "entity_id"
            if (not isinstance(ref, dict) or ref.get("role") != expected_roles[option_kind]
                    or ref.get("kind") != "entity" or not isinstance(properties, dict)
                    or properties.get(property_key) != target):
                raise BoundaryError("reward_potion_input", "current_referent_binding")
            if option_kind == "potion_open" and properties.get("slot") != next(
                    row["slot"] for row in surface["openable_potions"]
                    if row["potion_entity_id"] == target):
                raise BoundaryError("reward_potion_input", "current_potion_slot_binding")
        if kind == "potion_popup":
            potion_id = surface.get("potion_entity_id")
            potion_ref = refs.get(potion_id)
            potion_properties = (potion_ref.get("properties")
                                 if isinstance(potion_ref, dict) else None)
            if (not isinstance(potion_id, str) or not isinstance(potion_ref, dict)
                    or potion_ref.get("role") != "potion"
                    or potion_ref.get("kind") != "entity"
                    or not isinstance(potion_properties, dict)
                    or potion_properties.get("potion_entity_id") != potion_id):
                raise BoundaryError("reward_potion_input", "current_popup_potion_binding")
        persistent = _object(snapshot["persistent"], "current_persistent_required")
        current_facts = _object(persistent["content"], "current_persistent_required")
        descriptions = [{**ref, "properties": ref.get("properties") or {}}
                        for ref in referents]
        projector = _SemanticProjection([current_facts, content, descriptions])
        state = {"CURRENT_PERSISTENT": projector.clean(current_facts),
                 "CURRENT_PAGE": {"kind": kind, "content": projector.clean(content)},
                 "READS": []}
        reject_leakage(state)
        state_text = (f"[STPD_STATE version={VERSION} profile=ordinary_reward_potion_page]\n"
                      + canonical_json(compact_public_state(state)) + "\n[/STPD_STATE]")
        ordered: list[tuple[int, int, int, str, str]] = []
        covered: set[str] = set()
        keys: list[str] = []
        for catalog_index, raw in enumerate(values):
            action = _object(raw, "malformed_action")
            bound_id = action["bound_action_id"]
            subject = action["subject_referent_id"]
            arguments = _rows(action["arguments"], "malformed_arguments")
            if (not isinstance(bound_id, str) or not bound_id or bound_id in keys
                    or action["interaction_id"] != interaction["interaction_id"]
                    or subject is not None and subject not in refs):
                raise BoundaryError("reward_potion_input", "action_binding_mismatch")
            keys.append(bound_id)
            target = subject if subject is not None else "<current-page-proceed>"
            if target not in enabled:
                raise BoundaryError("reward_potion_input", "unmatched_current_page_action")
            if target in covered:
                raise BoundaryError("reward_potion_input", "duplicate_current_page_action")
            group, option_kind, ordinal, verb = positions[target]
            if action["verb"] != verb:
                raise BoundaryError("reward_potion_input", "unsupported_current_page_verb")
            if kind == "potion_popup" and option_kind in {"use", "discard"}:
                if (len(arguments) != 1 or arguments[0].get("role") != "potion"
                        or arguments[0].get("referent_id") != surface.get("potion_entity_id")):
                    raise BoundaryError("reward_potion_input", "popup_potion_argument_mismatch")
            elif arguments:
                raise BoundaryError("reward_potion_input", "unsupported_current_page_operands")
            covered.add(target)
            action_fact = {"verb": verb,
                           "current_page_target": {"kind": option_kind, "ordinal": ordinal}}
            reject_leakage(action_fact)
            ordered.append((group, ordinal, catalog_index,
                            canonical_json(action_fact), bound_id))
        if covered != enabled:
            raise BoundaryError("reward_potion_input", "incomplete_current_page_menu")
        ordered.sort(key=lambda row: (row[0], row[1], row[2]))
        action_texts = tuple(f"[STPD_ACTION version={VERSION}]\n{row[3]}\n[/STPD_ACTION]"
                             for row in ordered)
        bindings = tuple(RewardActionBinding(index, row[2], row[4])
                         for index, row in enumerate(ordered))
        return RewardPageInput(state_text, action_texts, bindings, semantic_hash(keys))
    except (KeyError, TypeError, AttributeError) as error:
        raise BoundaryError("reward_potion_input", "malformed_snapshot") from error
