"""P1 public identity paths for input v2 (owner: SDK publicIdentity.ts).

Unknown extensions are literal; suffixes and alias-like spellings do not grant
identity semantics. Only declared public DTO paths contribute reference edges.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


def identity_paths(observation: dict[str, Any]) -> Callable[[str], str | None]:
    declared = {"player.content.entity_id", "player.content.relics[].entity_id",
                "player.content.potions[].entity_id", "commitments.reward_dispositions[].group",
                "map.content.nodes[].entity_id", "combat.content.player.player_entity_id",
                "combat.content.enemies[].entity_id"}
    declared.update(f"combat.content.player.{kind}[].entity_id"
                    for kind in ("companions", "potion_states", "orbs"))
    references = {"commitments.declined_reward_groups[]", "catalog[].subject", "catalog[].target"}
    cards = {"deck.content.cards[]", "piles.content.zones[].cards[]",
             "combat.content.player.hand[]", "player.content.relics[].card_previews[]",
             "player.content.potions[].card_previews[]", "run.content.modifiers[].card_previews[]"}
    native = observation.get("scene", {}).get("native_kind")
    surface = "scene.content.surface."
    def add(fields):
        declared.update(surface + field for field in fields)
    def refs(fields):
        references.update(surface + field for field in fields)
    def card(root):
        cards.add(surface + root)
    if native in {"event_option", "event_dialogue", "map_navigation", "rest_site", "shop_room",
                  "shop_inventory", "treasure_room", "reward_claim", "card_reward_selection"}:
        refs(["non_combat_discardable_potions[].entity_id"])
    if native in {"card_selection", "combat_hand_card_selection", "native_combat_pile_selection",
                  "native_deck_card_selection", "native_generated_card_choice",
                  "native_simple_card_selection", "deck_transform_selection", "deck_card_selection",
                  "deck_upgrade_selection", "deck_enchant_selection"}:
        card("cards[]")
        add(["screen_entity_id", "hand_entity_id", "request_entity_id",
             "current_controls[].entity_id"])
        refs(["selected_card_entity_ids[]", "selectable_card_entity_ids[]",
              "deselectable_card_entity_ids[]", "selector_request.entity_id",
              "selector_request.owner_entity_id", "selector_request.selected_entity_ids[]",
              "preview_options[].subject_entity_id",
              "selection_progress.selected_card_entity_ids[]"])
        card("preview_options[].before")
        card("preview_options[].after")
        if native == "deck_upgrade_selection":
            card("preview_cards[]")
    elif native == "combat_turn":
        add(["room_entity_id"])
        for key in ("playable_cards", "usable_potions", "discardable_potions"):
            add([f"{key}[].entity_id"])
            refs([f"{key}[].target_entity_ids[]"])
    elif native == "map_navigation":
        add(["next_options[].entity_id"])
    elif native == "reward_claim":
        add(["reward_set_id", "rewards[].entity_id", "rewards[].group_id",
             "discardable_potions[].entity_id"])
        card("rewards[].card_options[]")
    elif native == "card_reward_selection":
        card("cards[]")
        add(["alternatives[].entity_id", "screen_entity_id", "reward_set_id", "parent_group_id"])
        refs(["selectable_card_entity_ids[]"])
    elif native == "card_bundle_selection":
        add(["bundles[].entity_id", "screen_entity_id"])
        card("bundles[].cards[]")
        refs(["selected_bundle_entity_id", "selectable_bundle_entity_ids[]"])
    elif native in {"event_option", "event_dialogue", "rest_site"}:
        add(["options[].entity_id", "screen_entity_id", "room_entity_id"])
    elif native in {"treasure_room", "treasure_chest", "treasure_relic_selection",
                    "treasure_completion", "relic_choice", "native_boss_relic_selection"}:
        add(["room_entity_id", "screen_entity_id", "relics[].entity_id"])
        refs(["selectable_relic_entity_ids[]"])
    elif native == "shop_inventory":
        add(["screen_entity_id", "shop_session_entity_id", "cards[].slot_entity_id",
             "relics[].entity_id", "relics[].slot_entity_id", "relics[].relic.entity_id",
             "potions[].entity_id", "potions[].slot_entity_id", "card_removal.entity_id",
             "card_removal.slot_entity_id"])
        card("cards[]")
        card("cards[].card")
        card("relics[].relic.card_previews[]")
    elif native == "shop_room":
        add(["room_entity_id", "shop_session_entity_id"])
    elif native == "game_over":
        add(["other_controls[].entity_id"])
    elif native == "potion_popup":
        # Legacy r1.5 captures retain this page. The SDK's rewardPotionPage.ts
        # owns these typed potion/target identities; publicIdentity.ts omits them.
        # Repair the new model representation without changing P1 or its comparer.
        add(["potion_entity_id"])
        refs(["use_target_entity_ids[]"])
    # Index-specific typed public card and shop Reads. An unrelated extension
    # named entity_id remains literal, just as in the SDK policy.
    exact_cards = set()
    exact_declared: set[str] = set()
    for index, read in enumerate(observation.get("extra_reads", [])):
        root = f"extra_reads[{index}].content"
        if read.get("kind") == "surface_card" and isinstance(read.get("content"), dict):
            exact_cards.add(root)
            references.add("extra_reads[].content.display_owner_entity_id")
        if read.get("kind") == "shop_catalog":
            exact_declared.update(root + "." + field for field in (
                "cards[].entity_id", "cards[].slot_entity_id", "relics[].entity_id",
                "relics[].slot_entity_id", "relics[].relic.entity_id", "potions[].entity_id",
                "potions[].slot_entity_id", "card_removal.entity_id",
                "card_removal.slot_entity_id"))
            exact_cards.update({root + ".cards[].card", root + ".relics[].relic.card_previews[]"})
    if native in {"event_option", "event_dialogue"}:
        body = observation.get("scene", {}).get("content", {}).get("surface", {})
        for i, option in enumerate(body.get("options", [])):
            for j, tip in enumerate(option.get("tooltips", [])):
                if tip.get("kind") == "card" and isinstance(tip.get("card"), dict):
                    exact_cards.add(f"scene.content.surface.options[{i}].tooltips[{j}].card")
    def card_id(path, root):
        return path.startswith(root) and re.fullmatch(r"(?:\.card_previews\[\])*\.entity_id",
                                                     path[len(root):]) is not None
    def relation(path):
        literal = path.removeprefix("$.")
        normalized = re.sub(r"\[\d+\]", "[]", literal)
        if normalized in references:
            return "reference"
        if normalized in declared:
            return "declared"
        # All remaining declarations are typed card/shop entity fields. Names,
        # descriptions and definition IDs cannot acquire identity semantics.
        # Avoid scanning every card root for these much more frequent literals.
        if not literal.endswith((".entity_id", ".slot_entity_id")):
            return None
        detail = re.sub(r"^(extra_reads\[\d+\])(.*)$",
                        lambda m: m[1] + re.sub(r"\[\d+\]", "[]", m[2]), literal)
        if (any(card_id(normalized, root) for root in cards)
                or detail in exact_declared
                or any(card_id(detail, root) for root in exact_cards)
                or any(card_id(literal, root) for root in exact_cards)):
            return "declared"
        return None
    return relation
