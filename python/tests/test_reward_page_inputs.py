"""Synthetic ordinary reward input checks; no Human or game qualification."""

import copy
import json

import pytest
from test_public_inputs import snapshot as legacy_snapshot
from tokenizers import Tokenizer

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.features import ModelSample
from stpd.fullrun.public_compaction import expand_public_state
from stpd.fullrun.public_inputs import project_public_snapshot
from stpd.fullrun.reward_page_inputs import (
    IDENTITY,
    INPUT_PROFILE,
    SNAPSHOT_SCHEMA,
    VERSION,
    project_reward_page_snapshot,
)
from stpd.fullrun.token_inputs import encode_texts, fit_scratch


def _card(identifier, name, description):
    return {
        "entity_id": identifier, "definition_id": name.upper(), "name": name,
        "type": "Skill", "cost": "1", "star_cost": None,
        "description": description, "rarity": "Common", "is_upgraded": False,
        "is_selected": False, "existing_enchantment": None,
    }


def _action(identifier, subject, interaction="screen-inner", verb="activate"):
    return {
        "bound_action_id": identifier, "verb": verb,
        "interaction_id": interaction, "subject_referent_id": subject,
        "arguments": [], "label": "Use current option",
    }


def _referent(row, role):
    return {
        "referent_id": row["entity_id"], "role": role, "kind": "entity",
        "label": row.get("name") or row.get("label"),
        "state": {"visible": True, "enabled": row.get("enabled"),
                  "selected": row.get("is_selected"),
                  "focused": None, "observation_basis": "native_visible_fact"},
        "properties_schema": f"sts2.player-environment/referent/{role}-1",
        "properties": copy.deepcopy(row),
    }


def inner_snapshot():
    cards = [
        _card("card-a", "Anchor", "Gain block."),
        _card("card-b", "Balance", "Draw a card."),
        _card("card-c", "Catalyst", "Gain energy."),
    ]
    # Host catalog order follows opaque CandidateId, not the native card row.
    actions = [
        _action("bound-b", "card-b", verb="select"),
        _action("bound-return", "alt-return"),
        _action("bound-a", "card-a", verb="select"),
        _action("bound-c", "card-c", verb="select"),
    ]
    alternatives = [{"entity_id": "alt-return", "index": 0,
                     "label": "Return to rewards", "enabled": True}]
    return {
        "schema": SNAPSHOT_SCHEMA, "input_profile": INPUT_PROFILE,
        "protocol_version": "1.0.0", "snapshot_id": "snapshot-inner", "sequence": 1,
        "status": "interactive", "reads": [],
        "information_policy": {"includes_hidden_information": False},
        "completeness": {"status": "complete"},
        "persistent": {"content": {
            "scope": "run", "run": {"act": 1, "floor": 3, "bosses": ["Boss A"],
                                   "modifiers": ["Visible modifier"]},
            "player": {"character_definition_id": "DEFECT", "hp": 44, "max_hp": 70,
                       "gold": 92, "relics": [{"name": "Visible relic"}],
                       "potions": [{"name": "Visible potion"}]},
            "completeness": {"status": "complete"},
        }},
        "interaction": {
            "interaction_id": "screen-inner", "kind": "card_reward_selection",
            "content_schema": "sts2.player-environment/surface/card_reward_selection-2",
            "capabilities": [{"verb": "select"}, {"verb": "activate"}],
            "content": {"surface": {
                "kind": "card_reward_selection", "cards": cards,
                "alternatives": alternatives,
                "selectable_card_entity_ids": ["card-a", "card-b", "card-c"],
                "alternative_effects": [{"entity_id": "alt-return",
                                         "effect": "return_to_rewards_without_claim"}],
            }, "context": {"kind": "reward_flow", "reward_kind": "card_reward"}},
        },
        "referents": [*(_referent(card, "card") for card in cards),
                      _referent(alternatives[0], "option")],
        "bound_actions": {
            "schema": "sts2.player-environment/bound-actions-1",
            "status": "complete", "total_count": 4, "materialized_count": 4,
            "limit": 512, "actions": actions,
        },
    }


def outer_snapshot():
    value = inner_snapshot()
    value["interaction"] = {
        "interaction_id": "screen-outer", "kind": "reward_claim",
        "content_schema": "sts2.player-environment/surface/reward_claim-2",
        "capabilities": [{"verb": "activate"}],
        "content": {"surface": {
            "kind": "reward_claim",
            "rewards": [
                {"entity_id": "reward-a", "kind": "card", "label": "Choose cards",
                 "description": None, "enabled": True},
                {"entity_id": "reward-b", "kind": "gold", "label": "Take gold",
                 "description": None, "enabled": True},
            ],
            "potion_slots_full": False, "discardable_potions": [],
            "can_proceed": True, "proceed_skips_remaining_rewards": True,
        }, "context": {"kind": "reward_flow", "reward_kind": "room_rewards"}},
    }
    value["referents"] = [_referent(reward, "reward") for reward in
                          value["interaction"]["content"]["surface"]["rewards"]]
    value["bound_actions"]["actions"] = [
        _action("bound-proceed", None, "screen-outer"),
        _action("bound-reward-b", "reward-b", "screen-outer"),
        _action("bound-reward-a", "reward-a", "screen-outer"),
    ]
    value["bound_actions"]["total_count"] = 3
    value["bound_actions"]["materialized_count"] = 3
    return value


def _decoded_state(text):
    value = json.loads(text.split("\n", 1)[1].rsplit("\n", 1)[0])
    return expand_public_state(value)


def _decoded_action(text):
    return json.loads(text.split("\n", 1)[1].rsplit("\n", 1)[0])


def test_inner_page_order_and_complete_execution_bijection():
    value = inner_snapshot()
    projected = project_reward_page_snapshot(value)
    assert IDENTITY["version"] == VERSION
    assert len(projected.action_texts) == len(projected.bindings) == 4
    assert [(binding.model_index, binding.catalog_index, binding.bound_action_id)
            for binding in projected.bindings] == [
                (0, 2, "bound-a"), (1, 0, "bound-b"),
                (2, 3, "bound-c"), (3, 1, "bound-return"),
            ]
    assert [_decoded_action(text)["current_page_target"] for text in projected.action_texts] == [
                {"kind": "card", "ordinal": 0}, {"kind": "card", "ordinal": 1},
                {"kind": "card", "ordinal": 2}, {"kind": "alternative", "ordinal": 0},
            ]
    assert [_decoded_action(text)["verb"] for text in projected.action_texts] == [
        "select", "select", "select", "activate",
    ]
    assert projected.scores_in_catalog_order((10.0, 20.0, 30.0, 40.0)) \
        == (20.0, 40.0, 10.0, 30.0)
    state = _decoded_state(projected.state_text)
    assert [card["name"] for card in state["CURRENT_PAGE"]["content"]["surface"]["cards"]] \
        == ["Anchor", "Balance", "Catalyst"]
    assert state["CURRENT_PERSISTENT"]["player"]["relics"][0]["name"] == "Visible relic"
    assert state["CURRENT_PERSISTENT"]["player"]["potions"][0]["name"] == "Visible potion"
    assert "bosses" in state["CURRENT_PERSISTENT"]["run"]
    assert "VISIBLE_ENTITIES" not in state  # The page already owns native option order.
    text = projected.state_text + "".join(projected.action_texts)
    for opaque in ("snapshot-inner", "screen-inner", "card-a", "bound-a", "alt-return"):
        assert opaque not in text


def test_outer_current_page_does_not_invent_unopened_card_contents():
    projected = project_reward_page_snapshot(outer_snapshot())
    assert [binding.catalog_index for binding in projected.bindings] == [2, 1, 0]
    assert [_decoded_action(text)["current_page_target"]["kind"]
            for text in projected.action_texts] == ["reward", "reward", "proceed"]
    assert "cards" not in _decoded_state(projected.state_text)["CURRENT_PAGE"]["content"]["surface"]


def test_reentry_keeps_same_input_rules_without_requiring_byte_or_catalog_order_identity():
    before = project_reward_page_snapshot(inner_snapshot())
    after_source = inner_snapshot()
    after_source["snapshot_id"] = "another-snapshot"
    after_source["interaction"]["interaction_id"] = "another-screen"
    after_source["bound_actions"]["actions"].reverse()
    for index, action in enumerate(after_source["bound_actions"]["actions"]):
        action["bound_action_id"] = f"new-bound-{index}"
        action["interaction_id"] = "another-screen"
    after = project_reward_page_snapshot(after_source)
    assert before.candidate_digest != after.candidate_digest
    assert [_decoded_action(text)["current_page_target"] for text in before.action_texts] \
        == [_decoded_action(text)["current_page_target"] for text in after.action_texts]
    assert sorted(binding.catalog_index for binding in after.bindings) == list(range(4))
    assert "new-bound" not in after.state_text + "".join(after.action_texts)


def test_same_content_options_remain_distinct_by_current_page_position_and_occurrence():
    value = inner_snapshot()
    value["interaction"]["content"]["surface"]["cards"][1] = {
        **value["interaction"]["content"]["surface"]["cards"][0], "entity_id": "card-b",
    }
    duplicate = copy.deepcopy(value["bound_actions"]["actions"][2])
    duplicate["bound_action_id"] = "bound-a-second"
    value["bound_actions"]["actions"].append(duplicate)
    value["bound_actions"]["total_count"] = 5
    value["bound_actions"]["materialized_count"] = 5
    projected = project_reward_page_snapshot(value)
    targets = [_decoded_action(text)["current_page_target"] for text in projected.action_texts]
    assert targets[:3] == [
        {"kind": "card", "ordinal": 0}, {"kind": "card", "ordinal": 0},
        {"kind": "card", "ordinal": 1},
    ]
    assert len({binding.bound_action_id for binding in projected.bindings}) == 5
    assert len(projected.scores_in_catalog_order((1, 2, 3, 4, 5))) == 5


def test_current_page_order_cost_and_rebound_ids_change_only_current_facts_and_bindings():
    before = project_reward_page_snapshot(inner_snapshot())
    changed = inner_snapshot()
    page = changed["interaction"]["content"]["surface"]
    page["cards"][0], page["cards"][2] = page["cards"][2], page["cards"][0]
    page["cards"][0]["cost"] = "2"
    for ref in changed["referents"]:
        if ref["referent_id"] == page["cards"][0]["entity_id"]:
            ref["properties"]["cost"] = "2"
    changed["snapshot_id"] = "rebound-snapshot"
    changed["bound_actions"]["actions"][0]["bound_action_id"] = "rebound-choice"
    after = project_reward_page_snapshot(changed)
    cards = _decoded_state(after.state_text)["CURRENT_PAGE"]["content"]["surface"]["cards"]
    assert [(card["name"], card["cost"]) for card in cards] == [
        ("Catalyst", "2"), ("Balance", "1"), ("Anchor", "1"),
    ]
    assert before.state_text != after.state_text
    assert [binding.catalog_index for binding in after.bindings] == [3, 0, 2, 1]
    assert after.bindings[1].bound_action_id == "rebound-choice"
    assert "rebound-choice" not in after.state_text + "".join(after.action_texts)


def test_existing_token_encoder_keeps_whole_catalog_or_rejects_whole_budget():
    projected = project_reward_page_snapshot(inner_snapshot())
    sample = ModelSample("transition", "synthetic-run", "train", "reward", "select",
                         projected.state_text, projected.action_texts,
                         tuple(f"key-{index}" for index in range(4)), 0)
    tokenizer = Tokenizer.from_str(fit_scratch((sample,), vocab_size=256).decode())
    row = encode_texts(tokenizer, projected.state_text, projected.action_texts,
                       max_tokens=32768)
    assert len(row.actions) == len(projected.bindings) == 4
    with pytest.raises(BoundaryError, match="joint_limit_exceeded_no_truncation"):
        encode_texts(tokenizer, projected.state_text, projected.action_texts, max_tokens=1)


@pytest.mark.parametrize("change,code", [
    (lambda s: s.pop("input_profile"), "complete_public"),
    (lambda s: s.update(input_profile="ordinary-reward-page-v2"), "complete_public"),
    (lambda s: s.update(schema="sts2.player-environment/snapshot-1"), "complete_public"),
    (lambda s: s.update(status="visible_unsupported"), "complete_public"),
    (lambda s: s.update(status="settling"), "complete_public"),
    (lambda s: s["completeness"].update(status="partial"), "complete_public"),
    (lambda s: s["information_policy"].update(includes_hidden_information=True),
     "complete_public"),
    (lambda s: s.update(reads=[{"read_id": "other-page"}]), "complete_public"),
    (lambda s: s["bound_actions"].update(status="partial"), "complete_catalog"),
    (lambda s: s["bound_actions"].update(total_count=512), "complete_catalog"),
    (lambda s: s["bound_actions"]["actions"][0].update(bound_action_id="bound-a"),
     "unique_bindings"),
    (lambda s: s["bound_actions"]["actions"][0].update(subject_referent_id="unknown"),
     "action_binding"),
    (lambda s: s["bound_actions"]["actions"][0].update(verb="play"),
     "unsupported_reward_verb"),
    (lambda s: s["bound_actions"]["actions"][0].update(verb="activate"),
     "unsupported_reward_verb"),
    (lambda s: s["bound_actions"]["actions"][1].update(verb="select"),
     "unsupported_reward_verb"),
    (lambda s: s["interaction"]["content"]["surface"]["alternative_effects"][0]
     .update(effect="complete_reward"), "alternative_effect"),
    (lambda s: s["interaction"]["content"]["surface"].update(rewards=[]), "mixed_reward"),
    (lambda s: s["interaction"]["content"].update(hidden_rng=7), "forbidden"),
    (lambda s: s["bound_actions"]["actions"][0].update(arguments=[
        {"role": "target", "referent_id": "card-a"}]), "unsupported_reward_operands"),
])
def test_incomplete_or_cross_profile_page_fails_whole(change, code):
    value = inner_snapshot()
    change(value)
    with pytest.raises(BoundaryError, match=code):
        project_reward_page_snapshot(value)


def test_outer_unopened_contents_and_unsupported_kind_fail():
    value = outer_snapshot()
    value["interaction"]["content"]["surface"]["cards"] = [_card("leak", "Unopened", "No")]
    with pytest.raises(BoundaryError, match="unopened_reward_contents"):
        project_reward_page_snapshot(value)
    value = outer_snapshot()
    value["interaction"]["kind"] = "combat_turn"
    with pytest.raises(BoundaryError, match="current_page_contract"):
        project_reward_page_snapshot(value)


def test_page_to_referent_must_bind_exact_role_and_current_object():
    for mutation in (
        lambda s: s["referents"].pop(0),
        lambda s: s["referents"][0].update(role="option"),
        lambda s: s["referents"][0]["properties"].update(entity_id="card-b"),
    ):
        value = inner_snapshot()
        mutation(value)
        with pytest.raises(BoundaryError, match="current_page_referent_binding"):
            project_reward_page_snapshot(value)


def test_scores_reject_length_nonfinite_and_bool_without_partial_mapping():
    projected = project_reward_page_snapshot(inner_snapshot())
    for bad in ((1.0,), (1.0, 2.0, float("nan"), 4.0),
                (1.0, 2.0, float("inf"), 4.0), (1.0, 2.0, True, 4.0)):
        with pytest.raises(BoundaryError, match="score_count|invalid_score"):
            projected.scores_in_catalog_order(bad)


def test_old_public_projection_is_still_strict_and_retains_its_order():
    old = legacy_snapshot()
    first = project_public_snapshot(old)
    old["bound_actions"]["actions"].reverse()
    second = project_public_snapshot(old)
    assert second.action_texts == first.action_texts[::-1]
    with pytest.raises(BoundaryError, match="interactive_complete"):
        project_public_snapshot(inner_snapshot())
    with pytest.raises(BoundaryError, match="complete_public"):
        project_reward_page_snapshot(old)
