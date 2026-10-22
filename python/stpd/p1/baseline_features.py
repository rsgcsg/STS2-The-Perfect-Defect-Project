"""The original G3 hand features, extracted only from the current public graph.

P1 producer fields are player.content, combat.content.player/enemies, piles.content
and public entity_id relations from catalog.subject/target. Repeated views with
conflicting values are missing for that feature; opaque aliases never become features.
Null/absent/non-numeric/X costs have a zero value AND an explicit missing indicator.
Fixed signed-log transforms need no heldout-fitted scale. Categorical inventories
are fitted from TRAIN candidates only; new values use the explicit unknown column.
"""

from __future__ import annotations

import math
from typing import Any

from .input import INPUT_SPEC, NUMBER, signed_log

CATEGORIES = ("scene", "action", "card_type")
NUMERICS = (
    "card_cost",
    "description_number_1",
    "description_number_2",
    "target_hp_ratio",
    "target_is_attack",
    "player_energy",
    "player_hp_ratio",
    "player_block",
    "hand_size",
)
FEATURE_SPEC = {
    "id": "stpd-p1-g3-hand-features",
    "version": "1",
    "revision": INPUT_SPEC["revision"],
    "categories": list(CATEGORIES),
    "numerics": list(NUMERICS),
    "missing": "zero plus per-numeric missing indicator; unknown category column",
    "numeric_transform": "signed_log1p except hp ratios and attack indicator",
    "references": "exact public entity_id equality only; ambiguous field values are missing",
    "input": "current public observation only; complete unfiltered catalog",
}


def validate_observation(observation: dict[str, Any]) -> None:
    if (observation.get("profile"), observation.get("revision")) != (
        INPUT_SPEC["profile"],
        INPUT_SPEC["revision"],
    ):
        raise ValueError("baseline requires its current P1 observation revision")
    catalog = observation.get("catalog")
    if not isinstance(catalog, list) or any(not isinstance(c, dict) for c in catalog):
        raise ValueError("baseline needs the full current catalog")
    ids = [c.get("choice_id") for c in catalog]
    if any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("baseline catalog IDs must be unique")
    if observation.get("status") not in {"decision", "terminal"} or (
        observation["status"] == "decision" and not catalog
    ):
        raise ValueError("baseline needs a decision or terminal observation")


def _number(value: Any) -> float | None:
    if type(value) in {int, float}:
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value)
        except ValueError:
            return None
    else:
        return None
    return result if math.isfinite(result) else None


def _content(observation: dict[str, Any], key: str) -> dict[str, Any]:
    part = observation.get(key)
    if not isinstance(part, dict) or part.get("availability") != "available":
        return {}
    content = part.get("content")
    return content if isinstance(content, dict) else {}


def _ratio(body: dict[str, Any]) -> float | None:
    hp, maximum = _number(body.get("hp")), _number(body.get("max_hp"))
    return hp / maximum if hp is not None and maximum is not None and maximum > 0 else None


def candidate_features(observation: dict[str, Any]) -> list[dict[str, Any]]:
    validate_observation(observation)
    aliases: dict[str, list[dict[str, Any]]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("entity_id"), str):
                aliases.setdefault(value["entity_id"], []).append(value)
            for key, item in value.items():
                if key not in {"envelope", "catalog"}:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(observation)

    def entity(alias: Any) -> dict[str, Any]:
        views = aliases.get(alias, []) if isinstance(alias, str) else []
        result = {}
        for key in {key for body in views for key in body}:
            values = [body[key] for body in views if key in body and body[key] is not None]
            if values and all(value == values[0] for value in values):
                result[key] = values[0]
        return result

    combat = _content(observation, "combat")
    public_player = _content(observation, "player")
    combat_player = combat.get("player")
    player = {**public_player, **(combat_player if isinstance(combat_player, dict) else {})}
    piles = _content(observation, "piles")
    hand = player.get("hand")
    if not isinstance(hand, list):
        zones = piles.get("zones", [])
        hands = [z.get("cards") for z in zones if isinstance(z, dict) and z.get("zone") == "hand"]
        hand = hands[0] if len(hands) == 1 else None
    hand_size = float(len(hand)) if isinstance(hand, list) else None
    scene = observation.get("scene", {}).get("family")
    rows = []
    for choice in observation["catalog"]:
        card, target = entity(choice.get("subject")), entity(choice.get("target"))
        description = card.get("description")
        numbers = (
            [float(x.group()) for x in NUMBER.finditer(description)]
            if isinstance(description, str)
            else []
        )
        intents = target.get("intents")
        attack = None
        if isinstance(intents, list) and all(isinstance(x, dict) for x in intents):
            # Producer intent.type is a public native name, not an inferred card effect.
            kinds = [x.get("type") for x in intents]
            if all(isinstance(x, str) for x in kinds):
                attack = float(any("attack" in x.lower() for x in kinds))
        row: dict[str, Any] = dict(
            zip(
                NUMERICS,
                (
                    _number(card.get("cost")),
                    numbers[0] if numbers else None,
                    numbers[1] if len(numbers) > 1 else None,
                    _ratio(target),
                    attack,
                    _number(player.get("energy")),
                    _ratio(player),
                    _number(player.get("block")),
                    hand_size,
                ),
                strict=True,
            )
        )
        row.update(
            scene=scene if isinstance(scene, str) else None,
            action=choice.get("kind"),
            card_type=card.get("type"),
        )
        rows.append(row)
    return rows


def fit_categories(observations: list[dict[str, Any]]) -> dict[str, list[str]]:
    rows = [row for observation in observations for row in candidate_features(observation)]
    return {
        key: [
            "[UNKNOWN]",
            *sorted(
                {row[key] for row in rows if isinstance(row[key], str) and row[key] != "[UNKNOWN]"}
            ),
        ]
        for key in CATEGORIES
    }


def vectorize(observation: dict[str, Any], categories: dict[str, list[str]]) -> list[list[float]]:
    vectors = []
    for row in candidate_features(observation):
        vector: list[float] = []
        for key in CATEGORIES:
            values = categories[key]
            index = values.index(row[key]) if row[key] in values else 0
            vector.extend(float(i == index) for i in range(len(values)))
        for key in NUMERICS:
            value = _number(row[key])
            transformed = (
                (
                    value
                    if key.endswith("hp_ratio") or key == "target_is_attack"
                    else signed_log(value)
                )
                if value is not None
                else 0.0
            )
            vector.extend((transformed, float(value is None)))
        vectors.append(vector)
    return vectors
