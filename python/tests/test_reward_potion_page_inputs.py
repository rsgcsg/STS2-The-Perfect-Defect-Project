"""Synthetic B4 current-page projector checks; no Human or game data."""

import copy
import json

import pytest
from test_reward_page_inputs import inner_snapshot, outer_snapshot

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.public_compaction import expand_public_state
from stpd.fullrun.reward_page_inputs import project_reward_page_snapshot
from stpd.fullrun.reward_potion_page_inputs import (
    INPUT_PROFILE,
    SNAPSHOT_SCHEMA,
    VERSION,
    project_reward_potion_snapshot,
)


def _v2(snapshot):
    snapshot["schema"] = SNAPSHOT_SCHEMA
    snapshot["input_profile"] = INPUT_PROFILE
    kind = snapshot["interaction"]["kind"]
    snapshot["interaction"]["content_schema"] = f"sts2.player-environment/surface/{kind}-3"
    snapshot["interaction"]["content"]["surface"]["openable_potions"] = [
        {"potion_entity_id": "potion-a", "slot": 0, "name": "Test potion"}]
    snapshot["referents"].append({
        "referent_id": "potion-a", "role": "potion", "kind": "entity",
        "label": "Test potion", "state": {"visible": True, "enabled": True},
        "properties_schema": "sts2.player-environment/referent/potion-1",
        "properties": {"potion_entity_id": "potion-a", "slot": 0, "name": "Test potion"},
    })
    snapshot["bound_actions"]["actions"].insert(1, {
        "bound_action_id": "bound-open", "verb": "open",
        "interaction_id": snapshot["interaction"]["interaction_id"],
        "subject_referent_id": "potion-a", "arguments": [], "label": "Open potion popup",
    })
    snapshot["bound_actions"]["total_count"] += 1
    snapshot["bound_actions"]["materialized_count"] += 1
    return snapshot


def outer_v2():
    value = _v2(outer_snapshot())
    del value["interaction"]["content"]["surface"]["discardable_potions"]
    return value


def inner_v2():
    return _v2(inner_snapshot())


def popup_v2(*, use=True, discard=True):
    value = outer_v2()
    value["interaction"] = {
        "interaction_id": "popup-screen", "kind": "potion_popup", "stage": "ready",
        "content_schema": "sts2.player-environment/surface/potion_popup-3",
        "capabilities": [{"verb": "activate"}, {"verb": "cancel"}],
        "content": {"surface": {
            "kind": "potion_popup", "potion_entity_id": "potion-a",
            "definition_id": "TEST_POTION", "name": "Test potion", "slot": 0,
            "can_use": use, "can_discard": discard,
            "controls": [
                {"entity_id": "control-use", "kind": "use", "enabled": use,
                 "label": "Use potion"},
                {"entity_id": "control-discard", "kind": "discard", "enabled": discard,
                 "label": "Discard potion"},
                {"entity_id": "popup-screen:close", "kind": "close", "enabled": True,
                 "label": "Close potion popup"},
            ],
        }, "context": {"kind": "reward_flow", "reward_kind": "room_rewards"}},
    }
    value["referents"] = [value["referents"][-1]] + [{
        "referent_id": control["entity_id"], "role": "control", "kind": "entity",
        "label": control["label"], "state": {"visible": True,
            "enabled": control["enabled"]},
        "properties_schema": "sts2.player-environment/referent/control-1",
        "properties": copy.deepcopy(control),
    } for control in value["interaction"]["content"]["surface"]["controls"]]
    actions = []
    for kind, verb in (("use", "activate"), ("discard", "activate"),
                       ("close", "cancel")):
        if kind == "use" and not use or kind == "discard" and not discard:
            continue
        control = next(item for item in value["interaction"]["content"]["surface"]["controls"]
                       if item["kind"] == kind)
        actions.append({
            "bound_action_id": f"bound-{kind}", "verb": verb,
            "interaction_id": "popup-screen", "subject_referent_id": control["entity_id"],
            "arguments": [] if kind == "close" else [
                {"role": "potion", "referent_id": "potion-a"}], "label": control["label"],
        })
    value["bound_actions"]["actions"] = list(reversed(actions))
    value["bound_actions"]["total_count"] = len(actions)
    value["bound_actions"]["materialized_count"] = len(actions)
    return value


def _state(text):
    return expand_public_state(json.loads(text.split("\n", 1)[1].rsplit("\n", 1)[0]))


def _actions(projected):
    return [json.loads(text.split("\n", 1)[1].rsplit("\n", 1)[0])
            for text in projected.action_texts]


def test_outer_and_inner_keep_whole_current_menu_and_native_option_order():
    outer = project_reward_potion_snapshot(outer_v2())
    assert VERSION in outer.state_text
    assert [(action["current_page_target"]["kind"], action["current_page_target"]["ordinal"])
            for action in _actions(outer)] == [
                ("reward", 0), ("reward", 1), ("potion_open", 0), ("proceed", 0)]
    assert [binding.catalog_index for binding in outer.bindings] == [3, 2, 1, 0]
    assert outer.scores_in_catalog_order((1, 2, 3, 4)) == (4.0, 3.0, 2.0, 1.0)
    assert "cards" not in _state(outer.state_text)["CURRENT_PAGE"]["content"]["surface"]
    inner = project_reward_potion_snapshot(inner_v2())
    assert [action["current_page_target"]["kind"] for action in _actions(inner)] == [
        "card", "card", "card", "potion_open", "alternative"]
    assert [card["name"] for card in
            _state(inner.state_text)["CURRENT_PAGE"]["content"]["surface"]["cards"]] == [
                "Anchor", "Balance", "Catalyst"]
    assert "potion-a" not in inner.state_text + "".join(inner.action_texts)


def test_inner_selectable_set_cannot_alias_a_potion_opener():
    value = inner_v2()
    value["interaction"]["content"]["surface"]["selectable_card_entity_ids"].append(
        "potion-a")
    with pytest.raises(BoundaryError, match="selectable_cards_unbound"):
        project_reward_potion_snapshot(value)


def test_opener_referent_must_keep_the_current_slot():
    value = outer_v2()
    value["referents"][-1]["properties"]["slot"] = 1
    with pytest.raises(BoundaryError, match="current_potion_slot_binding"):
        project_reward_potion_snapshot(value)


def test_popup_use_and_discard_are_distinct_current_controls_even_with_same_public_verb():
    source = popup_v2()
    projected = project_reward_potion_snapshot(source)
    assert [(action["verb"], action["current_page_target"]) for action in _actions(projected)] == [
        ("activate", {"kind": "use", "ordinal": 0}),
        ("activate", {"kind": "discard", "ordinal": 1}),
        ("cancel", {"kind": "close", "ordinal": 2}),
    ]
    assert [binding.bound_action_id for binding in projected.bindings] == [
        "bound-use", "bound-discard", "bound-close"]
    assert [binding.catalog_index for binding in projected.bindings] == [2, 1, 0]
    assert projected.scores_in_catalog_order((9, 8, 7)) == (7.0, 8.0, 9.0)
    for opaque in ("control-use", "control-discard", "bound-use", "popup-screen"):
        assert opaque not in projected.state_text + "".join(projected.action_texts)
    rebound = popup_v2()
    rebound["interaction"]["content"]["surface"]["controls"][0]["entity_id"] = "use-2"
    rebound["referents"][1]["referent_id"] = "use-2"
    rebound["referents"][1]["properties"]["entity_id"] = "use-2"
    rebound["bound_actions"]["actions"][2]["subject_referent_id"] = "use-2"
    rebound["bound_actions"]["actions"].reverse()
    second = project_reward_potion_snapshot(rebound)
    assert _actions(second) == _actions(projected)
    assert sorted(binding.catalog_index for binding in second.bindings) == [0, 1, 2]


def test_disabled_popup_use_is_visible_fact_but_not_an_action():
    projected = project_reward_potion_snapshot(popup_v2(use=False))
    assert [action["current_page_target"]["kind"] for action in _actions(projected)] == [
        "discard", "close"]
    controls = _state(projected.state_text)["CURRENT_PAGE"]["content"]["surface"]["controls"]
    assert controls[0]["enabled"] is False


def test_duplicate_current_control_is_not_a_second_model_candidate():
    value = popup_v2()
    duplicated = copy.deepcopy(value["bound_actions"]["actions"][0])
    duplicated["bound_action_id"] = "bound-close-duplicate"
    value["bound_actions"]["actions"].append(duplicated)
    value["bound_actions"]["total_count"] += 1
    value["bound_actions"]["materialized_count"] += 1
    with pytest.raises(BoundaryError, match="duplicate_current_page_action"):
        project_reward_potion_snapshot(value)


@pytest.mark.parametrize("change,code", [
    (lambda s: s.update(input_profile="ordinary-reward-page-v1"), "complete_public"),
    (lambda s: s.update(schema="sts2.player-environment/ordinary-reward-page-snapshot-1"),
     "complete_public"),
    (lambda s: s.update(status="visible_unsupported"), "complete_public"),
    (lambda s: s["bound_actions"].update(status="truncated"), "complete_catalog"),
    (lambda s: s["bound_actions"]["actions"][0].update(subject_referent_id="potion-a"),
     "unmatched_current_page_action"),
    (lambda s: s["interaction"]["content"]["surface"].update(
        use_target_entity_ids=["future-enemy"]), "noncurrent_popup_fields"),
    (lambda s: s["interaction"]["content"]["surface"].update(
        cards=[{"name": "Unopened card"}]), "noncurrent_popup_fields"),
    (lambda s: s["interaction"]["content"]["surface"]["controls"][0]
     .update(enabled=False), "popup_enablement"),
    (lambda s: s["referents"][1]["properties"].update(entity_id="control-discard"),
     "current_referent_binding"),
    (lambda s: s["bound_actions"]["actions"][1].update(verb="select"),
     "unsupported_current_page_verb"),
    (lambda s: s["bound_actions"]["actions"][1].update(arguments=[]),
     "popup_potion_argument"),
    (lambda s: s["referents"].pop(0), "current_popup_potion_binding"),
    (lambda s: s["referents"][0].update(role="card"), "current_popup_potion_binding"),
    (lambda s: s["referents"][0].update(kind="control"), "current_popup_potion_binding"),
    (lambda s: s["referents"][0]["properties"].update(potion_entity_id="other-potion"),
     "current_popup_potion_binding"),
    (lambda s: s["bound_actions"]["actions"][1]["arguments"][0]
     .update(referent_id="other-potion"), "popup_potion_argument"),
])
def test_popup_incomplete_or_misbound_menu_fails_whole(change, code):
    value = popup_v2()
    change(value)
    with pytest.raises(BoundaryError, match=code):
        project_reward_potion_snapshot(value)


def test_v1_projector_keeps_rejecting_v2_and_v2_rejects_v1():
    with pytest.raises(BoundaryError):
        project_reward_page_snapshot(outer_v2())
    with pytest.raises(BoundaryError):
        project_reward_potion_snapshot(outer_snapshot())
