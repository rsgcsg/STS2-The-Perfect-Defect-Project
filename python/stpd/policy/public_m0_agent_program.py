"""One declared optional final chooser over complete original Public M0 scores.

Connector's current typed selectable-card facts define candidate membership.
This program creates neither legality nor native operands, and retains full C.
"""

from __future__ import annotations

from typing import Any, NoReturn

from spireagent.json_boundary import BoundaryError

SNAPSHOT_SCHEMA = "sts2.player-environment/snapshot-1"
CARD_SURFACE_SCHEMA = "sts2.player-environment/surface/card_reward_selection-1"
CARD_REFERENT_SCHEMA = "sts2.player-environment/referent/card-1"


def card_reward_take_program_spec() -> dict[str, str]:
    return {
        "schema": "stpd/public-m0-agent-program-v1",
        "id": "card-reward-take-v1",
        "input_schema": SNAPSHOT_SCHEMA,
        "scores": "nonterminal-complete-raw-neural-v1",
        "selection": "NN-best-current-selectable-card-v1",
        "zero_eligible": "neural-argmax-v1",
    }


def validate_program_spec(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value != card_reward_take_program_spec():
        raise BoundaryError("public_m0_agent_program", "unsupported_program_spec")
    return card_reward_take_program_spec()


def current_card_candidates(snapshot: dict[str, Any]) -> tuple[int, ...]:
    """Called only after complete public projection/C/support admission.

    Empty means the declared rule has no eligible current card. Malformed claimed
    ordinary pages reject whole instead of becoming a neural fallback.
    """
    interaction = snapshot["interaction"]
    if interaction["kind"] != "card_reward_selection":
        return ()

    def invalid() -> NoReturn:
        raise BoundaryError("public_m0_agent_program", "unqualified_card_reward_program_input")

    content = interaction["content"]
    surface, context = content.get("surface"), content.get("context")
    if (snapshot["schema"] != SNAPSHOT_SCHEMA
            or interaction.get("stage") != "ready"
            or interaction.get("content_schema") != CARD_SURFACE_SCHEMA
            or not isinstance(surface, dict) or not isinstance(context, dict)
            or surface.get("kind") != "card_reward_selection"
            or context.get("kind") != "reward_flow"
            or context.get("reward_kind") != "card_reward"):
        invalid()
    cards = surface.get("cards")
    selectable = surface.get("selectable_card_entity_ids")
    alternatives = surface.get("alternatives")
    if (not isinstance(cards, list) or not isinstance(selectable, list)
            or not isinstance(alternatives, list)):
        invalid()
    if any(not isinstance(card, dict) or not isinstance(card.get("entity_id"), str)
           or not card["entity_id"] for card in cards):
        invalid()
    card_ids = [card["entity_id"] for card in cards]
    if (len(card_ids) != len(set(card_ids))
            or any(not isinstance(identity, str) or not identity for identity in selectable)
            or len(selectable) != len(set(selectable))
            or not set(selectable) <= set(card_ids)):
        invalid()
    if any(not isinstance(option, dict)
           or not isinstance(option.get("entity_id"), str) or not option["entity_id"]
           or type(option.get("index")) is not int or option["index"] < 0
           or not isinstance(option.get("label"), str) or not option["label"]
           or type(option.get("enabled")) is not bool for option in alternatives):
        invalid()
    option_ids = [option["entity_id"] for option in alternatives]
    option_positions = [option["index"] for option in alternatives]
    if (len(option_ids) != len(set(option_ids))
            or len(option_positions) != len(set(option_positions))
            or set(option_ids) & set(card_ids)):
        invalid()
    referents = {ref["referent_id"]: ref for ref in snapshot["referents"]}
    for card in cards:
        identity = card["entity_id"]
        ref = referents.get(identity)
        if (not isinstance(ref, dict) or ref.get("role") != "card"
                or ref.get("kind") != "entity"
                or ref.get("properties_schema") != CARD_REFERENT_SCHEMA
                or not isinstance(ref.get("properties"), dict)
                or ref["properties"] != card
                or not isinstance(ref.get("state"), dict)
                or ref["state"].get("visible") is not True):
            invalid()
    matches: dict[str, int] = {}
    for index, action in enumerate(snapshot["bound_actions"]["actions"]):
        subject = action.get("subject_referent_id")
        if action["verb"] != "select":
            continue
        if subject not in selectable or action.get("arguments") != [] or subject in matches:
            invalid()
        matches[subject] = index
    if set(matches) != set(selectable):
        invalid()
    return tuple(sorted(matches.values()))  # Original C order, never native UI index.
