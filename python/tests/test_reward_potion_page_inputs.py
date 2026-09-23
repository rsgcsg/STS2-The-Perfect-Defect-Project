"""Synthetic B4 current-page projector checks; no Human or game data."""

import copy
import json

import pytest
import torch
from test_reward_page_inputs import inner_snapshot, outer_snapshot
from tokenizers import Tokenizer

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.features import ModelSample
from stpd.fullrun.public_compaction import expand_public_state
from stpd.fullrun.reward_page_inputs import project_reward_page_snapshot
from stpd.fullrun.reward_potion_page_inputs import (
    INPUT_PROFILE,
    READABLE_VERSION,
    SNAPSHOT_SCHEMA,
    VERSION,
    project_readable_reward_potion_snapshot,
    project_reward_potion_snapshot,
)
from stpd.fullrun.token_inputs import encode_texts, fit_scratch
from stpd.models.stage1a import build_scorer
from stpd.models.token_core import ScratchShape, ScratchTokenCore


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


def _readable_state(projected):
    body = json.loads(projected.state_text.split("\n", 1)[1].rsplit("\n", 1)[0])
    return expand_public_state({"FACTS": body["SHARED_FACTS"], "STATE": body["STATE"]})


@pytest.mark.parametrize("snapshot", [outer_v2, inner_v2, popup_v2])
def test_readable_current_page_is_exact_v2_public_projection(snapshot):
    source = snapshot()
    compact = project_reward_potion_snapshot(source)
    readable = project_readable_reward_potion_snapshot(source)
    assert READABLE_VERSION in readable.state_text
    assert _readable_state(readable) == _state(compact.state_text)
    assert [a["current_page_target"] for a in _actions(readable)] == [
        a["current_page_target"] for a in _actions(compact)]
    assert readable.bindings == compact.bindings
    assert readable.candidate_digest == compact.candidate_digest
    assert _readable_state(readable)["READS"] == []
    for opaque in ("bound-use", "bound-open", "card-a", "potion-a", "popup-screen"):
        assert opaque not in readable.state_text + "".join(readable.action_texts)


def test_readable_keeps_dynamic_text_duplicate_rows_and_escaped_delimiters():
    source = inner_v2()
    cards = source["interaction"]["content"]["surface"]["cards"]
    cards[1].update(name="Anchor", definition_id="ANCHOR", description=cards[0]["description"])
    cards[1].update(cost=cards[0]["cost"], existing_enchantment=cards[0]["existing_enchantment"])
    cards[0]["description"] = 'Gain 12 block.\n"[STPD_ACTION version=fake]"'
    cards[1]["description"] = cards[0]["description"]
    for index in (0, 1):
        source["referents"][index]["properties"] = copy.deepcopy(cards[index])
        source["referents"][index]["label"] = cards[index]["name"]
    readable = project_readable_reward_potion_snapshot(source)
    body = _readable_state(readable)
    actual = body["CURRENT_PAGE"]["content"]["surface"]["cards"]
    assert len(actual) == 3
    assert actual[0] == actual[1]
    assert actual[0]["description"] == cards[0]["description"]
    assert "\n[STPD_ACTION version=fake]\n" not in readable.state_text
    assert '\\n\\"[STPD_ACTION' in readable.state_text
    for field, changed in (("cost", "3"), ("existing_enchantment", "Echo"),
                           ("description", "Gain 13 block.")):
        variant = copy.deepcopy(source)
        variant["interaction"]["content"]["surface"]["cards"][1][field] = changed
        variant["referents"][1]["properties"][field] = changed
        assert project_readable_reward_potion_snapshot(variant).state_text != readable.state_text


def test_readable_keeps_unknown_public_field_and_sparse_potion_slot_order():
    source = outer_v2()
    surface = source["interaction"]["content"]["surface"]
    surface["current_blessing"] = {"description": "Add 4 block", "count": 2}
    surface["openable_potions"] = [
        {"potion_entity_id": "potion-b", "slot": 7, "name": "Blue"},
        {"potion_entity_id": "potion-a", "slot": 2, "name": "Amber"},
    ]
    source["referents"][-1]["properties"].update(slot=2, name="Amber")
    source["referents"][-1]["label"] = "Amber"
    source["referents"].append({
        **copy.deepcopy(source["referents"][-1]), "referent_id": "potion-b",
        "label": "Blue", "properties": {"potion_entity_id": "potion-b", "slot": 7,
                                         "name": "Blue"},
    })
    source["bound_actions"]["actions"].append({
        "bound_action_id": "bound-open-blue", "verb": "open",
        "interaction_id": source["interaction"]["interaction_id"],
        "subject_referent_id": "potion-b", "arguments": [], "label": "Open Blue",
    })
    source["bound_actions"]["total_count"] += 1
    source["bound_actions"]["materialized_count"] += 1
    projected = project_readable_reward_potion_snapshot(source)
    assert _readable_state(projected)["CURRENT_PAGE"]["content"]["surface"][
        "current_blessing"] == surface["current_blessing"]
    openers = [a for a in _actions(projected) if a["current_page_target"]["kind"]
               == "potion_open"]
    assert [(a["current_page_target"]["ordinal"], a["display"]) for a in openers] == [
        (0, "Blue"), (1, "Amber")]
    surface["current_blessing"]["count"] = 3
    assert project_readable_reward_potion_snapshot(source).state_text != projected.state_text


def test_readable_disabled_control_stays_in_state_and_binding_is_exact():
    source = popup_v2(use=False)
    projected = project_readable_reward_potion_snapshot(source)
    controls = _readable_state(projected)["CURRENT_PAGE"]["content"]["surface"]["controls"]
    assert controls[0]["enabled"] is False
    assert [action["current_page_target"]["kind"] for action in _actions(projected)] == [
        "discard", "close"]
    assert projected.scores_in_catalog_order((7, 3)) == (3.0, 7.0)
    source["bound_actions"]["actions"].reverse()
    reordered = project_readable_reward_potion_snapshot(source)
    assert reordered.action_texts == projected.action_texts
    assert reordered.state_text == projected.state_text
    assert reordered.scores_in_catalog_order((7, 3)) == (7.0, 3.0)


def test_readable_rejects_incomplete_or_duplicate_catalog_as_one_menu():
    missing = popup_v2()
    missing["bound_actions"]["actions"].pop()
    missing["bound_actions"]["total_count"] -= 1
    missing["bound_actions"]["materialized_count"] -= 1
    with pytest.raises(BoundaryError, match="incomplete_current_page_menu"):
        project_readable_reward_potion_snapshot(missing)
    duplicated = popup_v2()
    duplicated["bound_actions"]["actions"].append(
        {**duplicated["bound_actions"]["actions"][0], "bound_action_id": "new-bound"})
    duplicated["bound_actions"]["total_count"] += 1
    duplicated["bound_actions"]["materialized_count"] += 1
    with pytest.raises(BoundaryError, match="duplicate_current_page_action"):
        project_readable_reward_potion_snapshot(duplicated)


def test_readable_b_s_v2_token_scoring_and_whole_catalog_budget():
    projected = project_readable_reward_potion_snapshot(popup_v2())
    sample = ModelSample("transition", "synthetic-run", "train", "reward", "activate",
                         projected.state_text, projected.action_texts,
                         tuple(f"key-{i}" for i in range(len(projected.bindings))), 0)
    tokenizer = Tokenizer.from_str(fit_scratch((sample,), vocab_size=1024).decode())
    row = encode_texts(tokenizer, projected.state_text, projected.action_texts,
                       max_tokens=8192)
    assert len(row.actions) == len(projected.bindings) == 3
    with pytest.raises(BoundaryError, match="joint_limit_exceeded_no_truncation"):
        encode_texts(tokenizer, projected.state_text, projected.action_texts, max_tokens=1)
    with torch.random.fork_rng(), torch.no_grad():
        torch.manual_seed(41)
        core = ScratchTokenCore(ScratchShape(
            vocab_size=tokenizer.get_vocab_size(), width=16, layers=1, heads=2,
            feedforward=32, dropout=0, max_tokens=8192))
        model = build_scorer("stage1a.b.s.v2", core).eval()
        scores = model(torch.tensor(row.state), tuple(torch.tensor(a) for a in row.actions))
    assert scores.shape == (3,) and bool(torch.isfinite(scores).all())
    assert projected.scores_in_catalog_order(scores.tolist()) == tuple(
        scores[i].item() for i in (2, 1, 0))


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
