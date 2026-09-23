"""Opt-in, provisional B3 input for B2 ordinary reward logical pages.

``project_reward_page_snapshot`` accepts only the complete B2 profile's current
``reward_claim`` or ``card_reward_selection`` page, current public persistent
facts, and full finite action catalog. It has no Read or remembered/unvisited
page. Current-page option order is native page order; short action text points
to a kind and local ordinal. Opaque snapshot/referent/bound-action IDs and the
Host's candidate ordering stay outside model text. The execution sidecar keeps
every catalog occurrence, including equivalent choices, and inverts scores
only after exact count and finite-value checks.

The Connector owns action authority and delivery. This module defines neither
native legality, a causal successor, learned memory, training data, nor an
installed Runtime adapter. Incomplete/other menus fail as a unit; no action is
silently filtered. Its version is separate from the legacy public input format.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from spireagent.json_boundary import BoundaryError

from ..canonical import canonical_json, semantic_hash
from .platform_bundle3 import _SemanticProjection
from .public_compaction import compact_public_state
from .representation import reject_leakage

INPUT_PROFILE = "ordinary-reward-page-v1"
SNAPSHOT_SCHEMA = "sts2.player-environment/ordinary-reward-page-snapshot-1"
VERSION = "stpd-ordinary-reward-current-page-compact-v1"
IDENTITY = {
    "version": VERSION,
    "profile": "ordinary_reward_current_page_compact",
    "source_schema": SNAPSHOT_SCHEMA,
    "input_profile": INPUT_PROFILE,
    "status": "provisional",
}
_BOUND_ACTION_SCHEMA = "sts2.player-environment/bound-actions-1"


@dataclass(frozen=True)
class RewardActionBinding:
    model_index: int
    catalog_index: int
    bound_action_id: str


@dataclass(frozen=True)
class RewardPageInput:
    state_text: str
    action_texts: tuple[str, ...]
    bindings: tuple[RewardActionBinding, ...]
    candidate_digest: str  # Original Host catalog order; never model text.

    def scores_in_catalog_order(self, scores: Sequence[float]) -> tuple[float, ...]:
        """Invert the exact occurrence permutation, including synonymous actions."""
        if len(scores) != len(self.bindings):
            raise BoundaryError("reward_page_input", "score_count_mismatch")
        result: list[float | None] = [None] * len(self.bindings)
        for binding, score in zip(self.bindings, scores, strict=True):
            if (type(score) not in {int, float} or not math.isfinite(score)
                    or not 0 <= binding.catalog_index < len(result)
                    or result[binding.catalog_index] is not None):
                raise BoundaryError("reward_page_input", "invalid_score_or_binding")
            result[binding.catalog_index] = float(score)
        if any(value is None for value in result):
            raise BoundaryError("reward_page_input", "incomplete_score_binding")
        return tuple(value for value in result if value is not None)


def _object(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BoundaryError("reward_page_input", code)
    return value


def _target_ids(rows: list[Any], code: str) -> list[str]:
    ids: list[str] = []
    for row in rows:
        identifier = _object(row, code).get("entity_id")
        if not isinstance(identifier, str) or not identifier:
            raise BoundaryError("reward_page_input", "current_page_target_binding")
        ids.append(identifier)
    return ids


def _options(
    surface: dict[str, Any], kind: str,
) -> tuple[dict[str, tuple[int, str, int]], set[str]]:
    """Return current-page target positions and exact enabled target IDs."""
    if kind == "card_reward_selection":
        if "rewards" in surface:
            raise BoundaryError("reward_page_input", "mixed_reward_page")
        cards = surface["cards"]
        alternatives = surface["alternatives"]
        effects = surface["alternative_effects"]
        selectable = surface["selectable_card_entity_ids"]
        if (not isinstance(cards, list) or not isinstance(alternatives, list)
                or not isinstance(effects, list) or not isinstance(selectable, list)
                or len(effects) != len(alternatives)):
            raise BoundaryError("reward_page_input", "incomplete_current_page")
        card_ids = _target_ids(cards, "malformed_card")
        alt_ids = _target_ids(alternatives, "malformed_alternative")
        ids = [*card_ids, *alt_ids]
        selectable_ids: set[str] = set()
        for value in selectable:
            if not isinstance(value, str) or not value or value in selectable_ids:
                raise BoundaryError("reward_page_input", "current_page_target_binding")
            selectable_ids.add(value)
        if (len(set(ids)) != len(ids)
                or not selectable_ids.issubset(card_ids)):
            raise BoundaryError("reward_page_input", "current_page_target_binding")
        for index, (alternative, effect) in enumerate(zip(alternatives, effects, strict=True)):
            item = _object(alternative, "malformed_alternative")
            declaration = _object(effect, "malformed_alternative_effect")
            if (type(item.get("index")) is not int or item["index"] != index
                    or type(item.get("enabled")) is not bool
                    or declaration.get("entity_id") != item["entity_id"]
                    or declaration.get("effect") != "return_to_rewards_without_claim"):
                raise BoundaryError("reward_page_input", "alternative_effect_binding")
        positions = {value: (0, "card", index) for index, value in enumerate(card_ids)}
        positions.update({value: (1, "alternative", index)
                          for index, value in enumerate(alt_ids)})
        enabled = selectable_ids | {
            alt_ids[index] for index, alt in enumerate(alternatives) if alt["enabled"]
        }
        return positions, enabled

    if "cards" in surface or "alternatives" in surface or "alternative_effects" in surface:
        raise BoundaryError("reward_page_input", "unopened_reward_contents")
    rewards = surface["rewards"]
    if (not isinstance(rewards, list) or surface.get("discardable_potions") != []
            or type(surface.get("can_proceed")) is not bool
            or type(surface.get("proceed_skips_remaining_rewards")) is not bool):
        raise BoundaryError("reward_page_input", "incomplete_current_page")
    ids = _target_ids(rewards, "malformed_reward")
    if (len(set(ids)) != len(ids)
            or any(type(reward.get("enabled")) is not bool for reward in rewards)):
        raise BoundaryError("reward_page_input", "current_page_target_binding")
    positions = {value: (0, "reward", index) for index, value in enumerate(ids)}
    enabled = {ids[index] for index, reward in enumerate(rewards) if reward["enabled"]}
    if surface["can_proceed"]:
        enabled.add("<current-page-proceed>")
    return positions, enabled


def project_reward_page_snapshot(snapshot: dict[str, Any]) -> RewardPageInput:
    """Project a complete B2 page without sorting away the native option order.

    Target ordinals refer to the *current page* list, not the Host candidate
    array. The returned binding sidecar is the only route back to opaque IDs.
    """
    try:
        if (snapshot.get("input_profile") != INPUT_PROFILE
                or snapshot.get("schema") != SNAPSHOT_SCHEMA
                or snapshot.get("status") != "interactive"
                or snapshot["information_policy"]["includes_hidden_information"] is not False
                or snapshot["completeness"]["status"] != "complete"
                or snapshot.get("reads") != []):
            raise BoundaryError("reward_page_input", "complete_public_reward_page_required")
        interaction = _object(snapshot["interaction"], "interaction_required")
        kind = interaction["kind"]
        if (kind not in {"reward_claim", "card_reward_selection"}
                or interaction["content_schema"]
                != f"sts2.player-environment/surface/{kind}-2"
                or not isinstance(interaction["interaction_id"], str)
                or not interaction["interaction_id"]
                or not isinstance(interaction["capabilities"], list)):
            raise BoundaryError("reward_page_input", "current_page_contract_required")
        content = _object(interaction["content"], "current_page_content_required")
        surface = _object(content["surface"], "current_page_surface_required")
        if surface.get("kind") != kind:
            raise BoundaryError("reward_page_input", "current_page_kind_mismatch")
        positions, enabled = _options(surface, kind)
        catalog = _object(snapshot["bound_actions"], "complete_catalog_required")
        values = catalog["actions"]
        if (catalog.get("schema") != _BOUND_ACTION_SCHEMA
                or catalog.get("status") != "complete"
                or not isinstance(values, list) or not values
                or type(catalog.get("total_count")) is not int
                or type(catalog.get("materialized_count")) is not int
                or catalog["total_count"] != len(values)
                or catalog["materialized_count"] != len(values)):
            raise BoundaryError("reward_page_input", "complete_catalog_required")
        referents = snapshot["referents"]
        persistent = _object(snapshot["persistent"], "persistent_current_state_required")
        current_facts = _object(persistent["content"], "persistent_current_state_required")
        if not isinstance(referents, list):
            raise BoundaryError("reward_page_input", "referents_required")
        ref_ids = [_object(ref, "malformed_referent")["referent_id"] for ref in referents]
        keys = [_object(value, "malformed_action")["bound_action_id"] for value in values]
        if (any(not isinstance(value, str) or not value for value in [*ref_ids, *keys])
                or len(set(ref_ids)) != len(ref_ids) or len(set(keys)) != len(keys)):
            raise BoundaryError("reward_page_input", "unique_bindings_required")
        refs_by_id = {ref["referent_id"]: ref for ref in referents}
        expected_roles = {"card": "card", "alternative": "option", "reward": "reward"}
        for target_id, (_, target_kind, _) in positions.items():
            ref = refs_by_id.get(target_id)
            if (ref is None or ref.get("role") != expected_roles[target_kind]
                    or ref.get("kind") != "entity"
                    or _object(ref.get("properties"), "current_page_referent_required")
                    .get("entity_id") != target_id):
                raise BoundaryError("reward_page_input", "current_page_referent_binding")
        projector = _SemanticProjection([current_facts, content])
        state = {
            "CURRENT_PERSISTENT": projector.clean(current_facts),
            "CURRENT_PAGE": {"kind": kind, "content": projector.clean(content)},
            "READS": [],
        }
        reject_leakage(state)
        state_text = (f"[STPD_STATE version={VERSION} profile=ordinary_reward_current_page]\n"
                      + canonical_json(compact_public_state(state)) + "\n[/STPD_STATE]")
        ordered: list[tuple[int, int, int, str, str]] = []
        covered: set[str] = set()
        for original_index, value in enumerate(values):
            subject = value.get("subject_referent_id")
            arguments = value["arguments"]
            # B2 projects select_card_reward as public "select"; claim,
            # alternative and proceed are public "activate".
            if (value["interaction_id"] != interaction["interaction_id"]
                    or not isinstance(arguments, list)
                    or (subject is not None and subject not in ref_ids)):
                raise BoundaryError("reward_page_input", "action_binding_mismatch")
            roles = [_object(arg, "malformed_argument")["role"] for arg in arguments]
            if (any(not isinstance(role, str) or not role for role in roles)
                    or len(set(roles)) != len(roles)
                    or any(arg["referent_id"] not in ref_ids for arg in arguments)):
                raise BoundaryError("reward_page_input", "argument_binding_mismatch")
            # B2 ordinary reward actions bind the current option as subject;
            # no separate operand domain is in this first declared profile.
            if arguments:
                raise BoundaryError("reward_page_input", "unsupported_reward_operands")
            if subject is None and kind == "reward_claim" and surface["can_proceed"]:
                group, target_kind, ordinal = 1, "proceed", 0
                covered.add("<current-page-proceed>")
            elif subject in positions and subject in enabled:
                group, target_kind, ordinal = positions[subject]
                covered.add(subject)
            else:
                raise BoundaryError("reward_page_input", "unmatched_current_page_action")
            expected_verb = "select" if target_kind == "card" else "activate"
            if value["verb"] != expected_verb:
                raise BoundaryError("reward_page_input", "unsupported_reward_verb")
            # The current page already contains the full card/reward text. A
            # local ordinal points to that fact without repeating it per action.
            action_fact = {"verb": value["verb"],
                           "current_page_target": {"kind": target_kind, "ordinal": ordinal}}
            reject_leakage(action_fact)
            ordered.append((group, ordinal, original_index,
                            canonical_json(action_fact), value["bound_action_id"]))
        if enabled != covered:
            raise BoundaryError("reward_page_input", "incomplete_current_page_menu")
        ordered.sort(key=lambda row: (row[0], row[1], row[2]))
        action_texts = tuple(f"[STPD_ACTION version={VERSION}]\n{row[3]}\n[/STPD_ACTION]"
                             for row in ordered)
        bindings = tuple(RewardActionBinding(index, row[2], row[4])
                         for index, row in enumerate(ordered))
        return RewardPageInput(state_text, action_texts, bindings, semantic_hash(keys))
    except (KeyError, TypeError, AttributeError) as error:
        raise BoundaryError("reward_page_input", "malformed_snapshot") from error
