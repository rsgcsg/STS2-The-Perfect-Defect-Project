"""Pure public teacher vectors; every selected ID is an original complete-C member."""

from __future__ import annotations

import copy

import pytest

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.native_structured_inputs import native_catalog_digest
from stpd.policy.native_public_teacher import NativePublicTeacher


def action(identity, verb, subject=None):
    return {
        "action_id": identity,
        "kind": "input",
        "verb": verb,
        "label": identity,
        "subject_referent_id": subject,
        "arguments": [],
        "effect_domain": "native",
    }


def observation(actions, kind="native_map", stage="ready", surface=None, refs=(), schema=None):
    return {
        "schema": "sts2.player-environment/native-logical-observation-1",
        "input_profile": "native-logical-v1",
        "status": "interactive",
        "snapshot_id": "snapshot",
        "session": {"runtime_instance_id": "runtime"},
        "catalog": {
            "snapshot_id": "snapshot",
            "status": "complete",
            "total_count": len(actions),
            "digest": native_catalog_digest(actions),
            "stream_generation": "generation",
        },
        "information_policy": {"includes_hidden_information": False},
        "completeness": {
            "status": "complete",
            "included": ["persistent", "interaction", "referents", "catalog"],
            "missing": [],
            "full_reference_complete": True,
        },
        "owner_occurrence": {"focus_referent_id": None},
        "interaction": {
            "kind": kind,
            "stage": stage,
            "content_schema": schema,
            "content": {"surface": surface or {"kind": kind}},
        },
        "referents": [
            {
                "referent_id": identity,
                "kind": "entity",
                "role": role,
                "state": {"visible": True, "enabled": True},
            }
            for identity, role in refs
        ],
    }


def test_entire_catalog_kept_and_low_ranked_member_chosen_from_typed_map():
    actions = [action(f"other-{i}", "inspect_card", "card") for i in range(40)]
    actions += [action("map-original", "activate", "monster")]
    before = copy.deepcopy(actions)
    view = observation(
        actions,
        surface={
            "kind": "map_navigation",
            "next_options": [{"entity_id": "monster", "point_type": "monster"}],
        },
        refs=[("monster", "map_point")],
    )
    choice = NativePublicTeacher(browse=False).decide(view, actions)
    assert choice.action_id == "map-original" and actions == before
    assert choice.state["learned"] is False


def test_card_entry_focus_confirm_and_no_unobserved_focus_repeat():
    teacher = NativePublicTeacher(browse=False)
    actions = [action("entry", "begin_card_play", "card"), action("end", "end_turn")]
    assert (
        teacher.decide(
            observation(actions, "combat_turn", refs=[("card", "card")]), actions
        ).action_id
        == "entry"
    )
    actions = [
        action("focus", "focus_target", "enemy"),
        action("confirm", "confirm_target", "enemy"),
    ]
    view = observation(
        actions, "combat_card_operation", "card_targeting", refs=[("enemy", "creature")]
    )
    assert teacher.decide(view, actions).action_id == "focus"
    assert teacher.decide(view, actions).reason == "native_target_focus_not_observed"
    view["owner_occurrence"]["focus_referent_id"] = "enemy"
    assert teacher.decide(view, actions).action_id == "confirm"
    actions = [action("end", "end_turn")]
    assert teacher.decide(observation(actions, "combat_turn"), actions).action_id == "end"
    assert (
        NativePublicTeacher(browse=False)
        .decide(observation(actions, "combat_turn"), actions)
        .action_id
        is None
    )


def test_bounded_inspection_then_progress_without_hidden_strategy_state():
    teacher = NativePublicTeacher()
    for kind, verb, role in [
        ("native_map", "open_run_deck", None),
        ("run_deck", "inspect_card", "card"),
        ("inspect_card", "toggle_card_upgrade_preview", None),
        ("inspect_card", "return_card_inspect", None),
        ("run_deck", "return_native_information", None),
    ]:
        actions = [action(verb, verb, "card" if role else None)]
        view = observation(actions, kind, refs=[("card", "card")] if role else [])
        assert teacher.decide(view, actions).action_id == verb
    actions = [action("progress", "activate", "monster")]
    view = observation(
        actions,
        surface={
            "kind": "map_navigation",
            "next_options": [{"entity_id": "monster", "point_type": "monster"}],
        },
        refs=[("monster", "map_point")],
    )
    assert teacher.decide(view, actions).action_id == "progress"
    assert teacher.state()["browse_choices"] == 5
    teacher.browse_choices = 12
    actions = [action("back", "return_card_inspect")]
    assert teacher.decide(observation(actions, "inspect_card"), actions).action_id is None


def test_linked_reward_typed_children_and_native_proceed():
    teacher = NativePublicTeacher(browse=False)
    actions = [
        action("linked", "claim_linked_reward", "choice"),
        action("proceed", "proceed_rewards", "screen"),
    ]
    surface = {
        "kind": "reward_claim",
        "entries": [
            {"kind": "linked_reward_set", "choices": [{"referent_id": "choice", "enabled": True}]}
        ],
        "proceed_enabled": True,
        "proceed_is_skip": False,
    }
    view = observation(
        actions,
        "reward_claim",
        surface=surface,
        refs=[("choice", "reward"), ("screen", "screen")],
        schema="sts2.player-environment/surface/linked_rewards_text_menu-1",
    )
    assert teacher.decide(view, actions).action_id == "linked"
    surface["entries"][0]["choices"][0]["enabled"] = False
    assert teacher.decide(view, actions).action_id == "proceed"
    del surface["proceed_is_skip"]
    assert teacher.decide(view, actions).action_id is None


def test_card_reward_typed_selection_and_missing_alternative_semantics_stop():
    actions = [action("select", "select", "card")]
    surface = {"cards": [{"entity_id": "card"}], "selectable_card_entity_ids": ["card"]}
    view = observation(
        actions, "card_reward_selection", surface=surface, refs=[("card", "current_option")]
    )
    teacher = NativePublicTeacher(browse=False)
    assert teacher.decide(view, actions).action_id == "select"
    surface["selectable_card_entity_ids"] = []
    assert teacher.decide(view, actions).reason == "card_reward_alternative_effect_not_public"


@pytest.mark.parametrize("mutation", ["partial", "digest", "hidden"])
def test_missing_complete_or_public_basis_rejected(mutation):
    actions = [action("end", "end_turn")]
    view = observation(actions, "combat_turn")
    if mutation == "partial":
        view["completeness"]["status"] = "partial"
    elif mutation == "digest":
        view["catalog"]["digest"] = "0" * 64
    else:
        view["information_policy"]["includes_hidden_information"] = True
    with pytest.raises(BoundaryError, match="complete_current_catalog_required"):
        NativePublicTeacher().decide(view, actions)


def test_unsupported_owner_has_no_generic_label_or_end_turn_fallback():
    actions = [action("tempting", "select", "option"), action("end", "end_turn")]
    view = observation(actions, "event_option", refs=[("option", "option")])
    assert (
        NativePublicTeacher(browse=False).decide(view, actions).reason == "unsupported_public_owner"
    )
