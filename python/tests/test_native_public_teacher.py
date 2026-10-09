"""Pure public teacher vectors; every selected ID is an original complete-C member."""

from __future__ import annotations

import copy

import pytest

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.native_structured_inputs import native_catalog_digest
from stpd.policy.native_public_teacher import NativePublicTeacher
from stpd.policy.native_task import (
    map_timed_ready_summary_task_spec,
    public_map_travel_pending,
    public_map_travel_timing_spec,
    ready_summary_task_spec,
)


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
        actions, "combat_card_operation", "card_targeting", refs=[("enemy", "creature")],
        surface={"kind": "combat_card_operation", "stage": "card_targeting",
                 "focused_target_referent_id": None},
        schema="sts2.player-environment/surface/combat_card_operation_text_menu-1",
    )
    assert teacher.decide(view, actions).action_id == "focus"
    assert teacher.decide(view, actions).directive == "await"
    assert teacher.decide(view, actions).reason == "await_public_target_focus"
    view["interaction"]["content"]["surface"]["focused_target_referent_id"] = "enemy"
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
        ("run_deck", "inspect_deck_card", "card"),
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


def test_run_deck_uses_its_native_inspect_verb_without_grid_alias_fallback():
    teacher = NativePublicTeacher(phase="deck")
    actions = [action("grid", "inspect_card", "card")]
    view = observation(actions, "run_deck", refs=[("card", "card")])
    assert teacher.decide(view, actions).reason == "required_native_action_unavailable"
    actions.append(action("deck", "inspect_deck_card", "card"))
    view = observation(actions, "run_deck", refs=[("card", "card")])
    assert teacher.decide(view, actions).action_id == "deck"
    assert teacher.state()["browse_choices"] == 1


@pytest.mark.parametrize("role", ["card", "playable_card", "hand"])
def test_combat_entry_accepts_the_native_card_referent_roles(role):
    # Native combat projection preserves playable_card/hand; presentation may add card.
    actions = [action("begin", "begin_card_play", "card"), action("end", "end_turn")]
    view = observation(actions, "combat_turn", refs=[("card", role)])
    assert NativePublicTeacher(browse=False).decide(view, actions).action_id == "begin"


@pytest.mark.parametrize(("kind", "role"), [("control", "card"), ("entity", "relic")])
def test_combat_entry_does_not_promote_other_referents_or_pad_with_end_turn(kind, role):
    actions = [action("begin", "begin_card_play", "card"), action("end", "end_turn")]
    view = observation(actions, "combat_turn", refs=[("card", role)])
    view["referents"][0]["kind"] = kind
    choice = NativePublicTeacher(browse=False).decide(view, actions)
    assert choice.action_id is None and choice.reason == "native_card_entry_unavailable_or_unproven"


def targeting_observation(*, focused="enemy", owner_focus="enemy", confirms="enemy"):
    actions = [action("focus-current", "focus_target", "enemy"),
               action("confirm-current", "confirm_target", confirms)]
    for member in actions:
        member["kind"] = "native_input"
    view = observation(
        actions, "combat_card_operation", "card_targeting",
        surface={"kind": "combat_card_operation", "stage": "card_targeting",
                 "held_card_referent_id": "held-card", "displayed_title": "Strike",
                 "displayed_cost": "1", "displayed_description": "Deal damage.",
                 "focused_target_referent_id": focused},
        refs=[("enemy", "creature"), ("other-enemy", "creature")],
        schema="sts2.player-environment/surface/combat_card_operation_text_menu-1",
    )
    view["owner_occurrence"]["focus_referent_id"] = owner_focus
    return view, actions


def test_already_focused_current_target_confirms_without_private_focus_history():
    # Direct6 sample268 already provided both equal focus fields and Confirm.
    # Repeating Focus on that same target made no new unit for the next decision.
    teacher = NativePublicTeacher(browse=False)
    view, actions = targeting_observation()
    original = copy.deepcopy((view, actions))
    assert teacher.focus_target_id is None
    choice = teacher.decide(view, actions)
    assert choice.directive == "act" and choice.action_id == "confirm-current"
    assert (view, actions) == original


@pytest.mark.parametrize("owner_focus", [None, "other-enemy", "foreign", 1])
@pytest.mark.parametrize("pending", [None, "enemy"])
def test_current_typed_focus_ignores_occurrence_with_no_or_matching_pending(owner_focus, pending):
    teacher = NativePublicTeacher(browse=False, focus_target_id=pending)
    view, actions = targeting_observation(owner_focus=owner_focus)
    assert teacher.decide(view, actions).action_id == "confirm-current"


def test_pending_different_focus_waits_for_requested_arrival_before_confirm():
    teacher = NativePublicTeacher(browse=False, focus_target_id="other-enemy")
    view, actions = targeting_observation()
    choice = teacher.decide(view, actions)
    assert choice.directive == "await" and choice.reason == "await_public_target_focus"
    assert choice.action_id is None and teacher.focus_target_id == "other-enemy"
    view["interaction"]["content"]["surface"]["focused_target_referent_id"] = "other-enemy"
    actions[1]["subject_referent_id"] = "other-enemy"
    view["catalog"]["digest"] = native_catalog_digest(actions)
    assert teacher.decide(view, actions).action_id == "confirm-current"


def test_unfocused_target_uses_original_focus_then_current_arrival_confirms():
    teacher = NativePublicTeacher(browse=False)
    view, actions = targeting_observation(focused=None, owner_focus="enemy")
    assert teacher.decide(view, actions).action_id == "focus-current"
    assert teacher.focus_target_id == "enemy"
    view["interaction"]["content"]["surface"]["focused_target_referent_id"] = "enemy"
    assert teacher.decide(view, actions).action_id == "confirm-current"


@pytest.mark.parametrize("change", [
    lambda o: o["interaction"]["content"]["surface"].pop("focused_target_referent_id"),
    lambda o: o["interaction"]["content"]["surface"].update(focused_target_referent_id="foreign"),
    lambda o: o["interaction"]["content"]["surface"].update(focused_target_referent_id=1),
    lambda o: o["interaction"].update(content_schema="noncanonical"),
    lambda o: o["interaction"]["content"]["surface"].update(kind="other"),
    lambda o: o["interaction"]["content"]["surface"].update(stage="card_confirm"),
    lambda o: o["referents"][0]["state"].update(visible=False),
    lambda o: o["referents"][0]["state"].update(enabled=False),
    lambda o: o.update(status="settling"),
])
def test_unknown_unready_foreign_or_noncanonical_focus_never_confirms(change):
    teacher = NativePublicTeacher(browse=False)
    view, actions = targeting_observation()
    change(view)
    assert teacher.decide(view, actions).action_id != "confirm-current"


def test_current_focus_needs_matching_original_confirm_and_complete_input():
    teacher = NativePublicTeacher(browse=False)
    view, actions = targeting_observation(focused="other-enemy", confirms="enemy")
    assert teacher.decide(view, actions).action_id != "confirm-current"
    view["completeness"]["full_reference_complete"] = False
    with pytest.raises(BoundaryError, match="complete_current_catalog_required"):
        teacher.decide(view, actions)


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
    view["referents"][1]["kind"] = "control"
    assert teacher.decide(view, actions).action_id == "linked"
    surface["entries"][0]["choices"][0]["enabled"] = False
    actions = [actions[1]]
    view = observation(
        actions,
        "reward_claim",
        surface=surface,
        refs=[("screen", "screen")],
        schema="sts2.player-environment/surface/linked_rewards_text_menu-1",
    )
    # The actual reward-page producer emits this as a control, not an entity.
    assert teacher.decide(view, actions).action_id is None
    view["referents"][0]["kind"] = "control"
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


def test_explicit_map_travel_waits_with_complete_information_catalog_and_no_route():
    # Native information leaves remain independently actionable during travel.
    actions = [
        action("deck", "open_run_deck"),
        action("relic", "inspect_relic", "public-relic"),
        action("relic-tips", "show_relic_tips", "public-relic"),
        *[action(f"info-{i}", "show_topbar_tips", f"public-topbar-{i}") for i in range(9)],
    ]
    for member in actions:
        member["kind"] = "native_input"
    original = copy.deepcopy(actions)
    view = observation(
        actions,
        stage="native_information_page",
        schema="sts2.player-environment/surface/map_navigation-1",
        surface={
            "kind": "map_navigation",
            "traveling": True,
            "travel_enabled": False,
            "next_options": [],
            "drawing_mode": "none",
        },
    )
    teacher = NativePublicTeacher(browse=False)
    before = teacher.state()
    decision = teacher.decide(view, actions)
    assert decision.directive == "await" and decision.reason == "await_public_map_travel"
    assert decision.action_id is None and teacher.state() == before
    assert actions == original and len(actions) == 12
    view["interaction"]["content"]["surface"]["traveling"] = False
    decision = teacher.decide(view, actions)
    assert decision.directive == "close" and decision.reason == "required_native_action_unavailable"
    view["interaction"]["content"]["surface"].update(traveling=True, next_options=None)
    decision = teacher.decide(view, actions)
    assert decision.directive == "await" and decision.reason == "await_public_map_travel"


@pytest.mark.parametrize("change", [
    lambda o: o.update(schema="wrong"),
    lambda o: o.update(input_profile="text-menu-v2"),
    lambda o: o["interaction"].update(content_schema="wrong"),
    lambda o: o["interaction"].update(stage="selecting"),
    lambda o: o["interaction"].update(kind="map_navigation"),
    lambda o: o["interaction"].update(kind="native_card_selection", stage="preview"),
    lambda o: o["interaction"].update(kind="native_card_selection", stage="peek"),
    lambda o: o["interaction"].update(kind="native_held_card", stage="targeting"),
    lambda o: o["interaction"]["content"]["surface"].update(kind="other"),
    lambda o: o["interaction"]["content"]["surface"].update(traveling=False),
    lambda o: o["interaction"]["content"]["surface"].pop("traveling"),
    lambda o: o["interaction"]["content"]["surface"].update(traveling=1),
    lambda o: o["interaction"]["content"]["surface"].update(traveling="true"),
])
def test_shared_map_timing_does_not_infer_other_owners_flags_or_parent_pending(change):
    view = observation(
        [action("info", "open_run_deck")], stage="native_information_page",
        schema="sts2.player-environment/surface/map_navigation-1",
        surface={"kind": "map_navigation", "traveling": True,
                 "travel_enabled": False, "next_options": []},
    )
    view["interaction"]["content"]["context"] = {"kind": "combat", "is_play_phase": False}
    assert public_map_travel_pending(view)
    change(view)
    assert not public_map_travel_pending(view)


def test_shared_timing_versions_only_agent_navigation_not_input_or_original_task():
    from stpd.native_graph_spec import NativeGraphControl
    from stpd.native_sampled_carry_spec import INPUT_SPEC, sampled_agent_spec
    from stpd.policy.native_teacher_agent import AGENT_SPEC
    from stpd.policy.native_teacher_agent import INPUT_SPEC as TEACHER_INPUT

    assert INPUT_SPEC["sha256"] == (
        "99996f16dc233ecb52f76f43435a1b064bcd89b61566f1e0b197e86161b6fd28")
    assert TEACHER_INPUT["sha256"] == (
        "d31163fbfe29bfec0be0c8c92ae5b13e4266fbe8915ff4c1762686e8cf7a6eb4")
    student = sampled_agent_spec(NativeGraphControl())
    assert student["version"] == AGENT_SPEC["version"] == "1.1.0"
    assert student["timing_policy"] == AGENT_SPEC["timing_policy"] == (
        public_map_travel_timing_spec())
    old, timed = ready_summary_task_spec(), map_timed_ready_summary_task_spec()
    assert old["version"] == "1.0.0" and timed["version"] == "1.1.0"
    assert {k: v for k, v in old.items() if k not in {"version", "navigation"}} == {
        k: v for k, v in timed.items() if k not in {"version", "navigation"}}
    assert student["task_spec"] == AGENT_SPEC["task_spec"] == timed


def test_disabled_map_routes_without_explicit_travel_fact_are_not_blind_pending():
    actions = [action("info", "open_run_deck")]
    view = observation(
        actions, surface={"kind": "map_navigation", "travel_enabled": False, "next_options": []}
    )
    assert NativePublicTeacher(browse=False).decide(view, actions).directive == "close"
