"""Candidate Connector v2 fixtures exercise the opt-in STPD input seam."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError, decode_json, json_bytes
from stpd.canonical import semantic_hash
from stpd.fullrun.memory_token_inputs import (
    RENDERER_IDENTITY,
    V2_RENDERER_IDENTITY,
    project_memory_snapshot,
    project_memory_v2_snapshot,
    renderer_identity_for_profile,
)
from stpd.fullrun.text_menu_inputs import (
    IDENTITY,
    V2_IDENTITY,
    project_text_menu_snapshot,
    project_text_menu_v2_snapshot,
)

FIXTURES = Path(__file__).parent / "fixtures" / "text_menu_v2"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def page(snapshot):
    return json.loads(project_text_menu_v2_snapshot(snapshot).state_text.split("\n", 1)[1]
                      .rsplit("\n", 1)[0])


def with_actions(source, cursor, selection, actions):
    result = copy.deepcopy(source)
    result["menu"]["cursor"] = cursor
    result["menu"]["selection"] = selection
    result["menu_actions"]["actions"] = actions
    result["menu_actions"]["total_count"] = len(actions)
    result["menu_actions"]["materialized_count"] = len(actions)
    return result


def action(identifier, kind, verb, subject=None, arguments=None):
    return {"action_id": identifier, "kind": kind, "verb": verb, "label": verb,
            "subject_referent_id": subject, "arguments": arguments or [],
            "effect_domain": "native_input" if kind == "native_input" else "text_menu"}


def test_published_candidate_root_select_cancel_target_confirm_sequence():
    root = fixture("targeted-root")
    selected = fixture("targeted-select")["successor"]
    first = project_text_menu_v2_snapshot(root)
    second = project_text_menu_v2_snapshot(selected)
    assert first.action_kinds == ("system_selection",)
    assert second.action_kinds == ("system_selection", "system_selection")
    assert page(root)["CURRENT_MENU"] == {"cursor": "root", "selection": []}
    staged = page(selected)["CURRENT_MENU"]
    assert staged["cursor"] == "card_targets"
    assert staged["selection"][0]["role"] == "card"
    assert staged["selection"][0]["referent"]["display_text"] == "Strike"
    assert staged["selection"][0]["referent"]["ordinal"] == 0

    cancelled = copy.deepcopy(root)
    cancelled["menu"]["revision"] = 2
    assert project_text_menu_v2_snapshot(cancelled).action_kinds == ("system_selection",)
    assert project_text_menu_v2_snapshot(copy.deepcopy(root)).state_text == first.state_text
    target = with_actions(selected, "card_confirmation", [
        {"role": "card", "referent_id": "card-C"},
        {"role": "target", "referent_id": "enemy-E"},
    ], [action("opaque-confirm", "native_input", "play", "card-C",
               [{"role": "target", "referent_id": "enemy-E"}]),
        action("opaque-cancel", "system_selection", "cancel_selection")])
    final = project_text_menu_v2_snapshot(target)
    assert final.action_kinds == ("native_input", "system_selection")
    assert page(target)["CURRENT_MENU"]["selection"][1]["referent"]["display_text"] == "Jaw Worm"
    assert all("card-C" not in text and "enemy-E" not in text and "opaque-" not in text
               for text in (final.state_text, *final.action_texts))
    assert final.action_ids == ("opaque-confirm", "opaque-cancel")
    assert final.candidate_digest == semantic_hash(list(final.action_ids))
    assert final.scores_in_catalog_order((0.7, 0.2)) == (0.7, 0.2)


def test_card_only_published_candidate_confirmation_is_a_native_leaf():
    root = fixture("card-only-root")
    selected = fixture("card-only-select")["successor"]
    assert project_text_menu_v2_snapshot(root).action_kinds == ("system_selection",)
    result = project_text_menu_v2_snapshot(selected)
    assert result.action_kinds == ("native_input", "system_selection")
    assert page(selected)["CURRENT_MENU"]["selection"][0]["role"] == "card"


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(protocol_version="2.0.0"),
    lambda s: s["referents"][0]["state"].update(visible=False),
    lambda s: s["referents"][0]["state"].update(enabled=False),
    lambda s: s["referents"][0].update(role="enemy"),
    lambda s: s["menu_actions"]["actions"][0].update(subject_referent_id="missing"),
    lambda s: s["menu_actions"].update(status="truncated"),
    lambda s: s["menu_actions"]["actions"].append(copy.deepcopy(
        fixture("targeted-root")["menu_actions"]["actions"][0])),
    lambda s: s["menu_actions"]["actions"][0].update(kind="native_input"),
    lambda s: s["menu_actions"]["actions"][0].update(effect_domain="native_input"),
    lambda s: s["interaction"].update(stage="not_ready"),
    lambda s: s["menu"].update(extra=True),
])
def test_invalid_v2_root_is_rejected(mutation):
    source = fixture("targeted-root")
    mutation(source)
    source["menu_actions"].update(total_count=len(source["menu_actions"]["actions"]),
                                  materialized_count=len(source["menu_actions"]["actions"]))
    with pytest.raises(BoundaryError):
        project_text_menu_v2_snapshot(source)


@pytest.mark.parametrize("mutation", [
    lambda s: s["menu"].update(selection=[]),
    lambda s: s["menu"]["selection"][0].update(role="target"),
    lambda s: s["menu"]["selection"][0].update(referent_id="enemy-E"),
    lambda s: s["menu_actions"]["actions"][0].update(subject_referent_id="card-C"),
    lambda s: s["menu_actions"]["actions"][1].update(subject_referent_id="card-C"),
    lambda s: s["menu_actions"]["actions"][1].update(kind="native_input"),
    lambda s: s["referents"][1]["state"].update(enabled=False),
])
def test_invalid_staged_binding_is_rejected(mutation):
    source = fixture("targeted-select")["successor"]
    mutation(source)
    with pytest.raises(BoundaryError):
        project_text_menu_v2_snapshot(source)


def test_confirmation_requires_exact_card_and_target_binding():
    selected = fixture("targeted-select")["successor"]
    confirm = with_actions(selected, "card_confirmation", [
        {"role": "card", "referent_id": "card-C"},
        {"role": "target", "referent_id": "enemy-E"},
    ], [action("confirm", "native_input", "play", "card-C",
               [{"role": "target", "referent_id": "enemy-E"}]),
        action("cancel", "system_selection", "cancel_selection")])
    assert len(project_text_menu_v2_snapshot(confirm).action_ids) == 2
    confirm["menu_actions"]["actions"][0]["arguments"] = []
    with pytest.raises(BoundaryError, match="confirmation_binding_mismatch"):
        project_text_menu_v2_snapshot(confirm)


def test_semantic_occurrences_catalog_order_and_opaque_id_invariance():
    source = fixture("targeted-root")
    other = copy.deepcopy(source["referents"][0])
    other["referent_id"] = "card-D"
    source["referents"].insert(1, other)
    source["menu_actions"]["actions"].append(
        action("select-D", "system_selection", "select_card", "card-D"))
    source["menu_actions"].update(total_count=2, materialized_count=2)
    before = project_text_menu_v2_snapshot(source)
    assert before.action_texts[0] != before.action_texts[1]
    assert '"ordinal":0' in before.action_texts[0]
    assert '"ordinal":1' in before.action_texts[1]
    assert before.state_text.count("Strike") >= 2
    stale_ids = copy.deepcopy(source)
    stale_ids["menu_actions"]["actions"][0]["action_id"] = "changed-A"
    stale_ids["menu_actions"]["actions"][1]["action_id"] = "changed-B"
    stale_ids["referents"][0]["referent_id"] = "changed-card-A"
    stale_ids["referents"][1]["referent_id"] = "changed-card-B"
    stale_ids["menu_actions"]["actions"][0]["subject_referent_id"] = "changed-card-A"
    stale_ids["menu_actions"]["actions"][1]["subject_referent_id"] = "changed-card-B"
    after = project_text_menu_v2_snapshot(stale_ids)
    assert (after.state_text, after.action_texts) == (before.state_text, before.action_texts)
    assert after.candidate_digest != before.candidate_digest
    reordered = copy.deepcopy(source)
    reordered["menu_actions"]["actions"].reverse()
    reversed_result = project_text_menu_v2_snapshot(reordered)
    assert reversed_result.action_ids == before.action_ids[::-1]
    assert reversed_result.candidate_digest != before.candidate_digest


def test_v1_output_and_renderer_identity_remain_frozen():
    from test_text_menu_inputs import snapshot

    current = project_text_menu_snapshot(snapshot())
    assert [hashlib.sha256(text.encode()).hexdigest() for text in
            (current.state_text, *current.action_texts, current.candidate_digest)] == [
        "70459c91b673ca3a3c69242815c3d8db680bb7eb11eda8ed30cd8d47d91b6b53",
        "69de53b09a54ac1c1d9bbb8f9ed6403d720bedbb75507347481a76485b97987f",
        "6a43a259796176b6160e66b000d9a3c8ba2ee7243154861c52b3e9f053ff856b",
        "06ea41aac7aa949dbfc45a00468379e05abc8680c0944dba0cbd4c2ae32ef713",
        "5d692a6ed27e18581c4cf9a7d64e63f2af4cb5f5ebfc41217b03ce64ab74c86b",
    ]
    with pytest.raises(BoundaryError):
        project_text_menu_snapshot(fixture("targeted-root"))
    with pytest.raises(BoundaryError):
        project_memory_snapshot(fixture("targeted-root"))
    assert project_memory_v2_snapshot(fixture("targeted-root")) == project_text_menu_v2_snapshot(
        decode_json(json_bytes(fixture("targeted-root"))))
    assert IDENTITY["input_profile"] == "text-menu-v1"
    assert V2_IDENTITY["input_profile"] == "text-menu-v2"
    assert RENDERER_IDENTITY["id"] == "stpd/m2-canonical-current-page-v1"
    assert V2_RENDERER_IDENTITY["id"] == "stpd/m2-canonical-current-page-v2"
    assert renderer_identity_for_profile("text-menu-v1") == RENDERER_IDENTITY
    assert renderer_identity_for_profile("text-menu-v2") == V2_RENDERER_IDENTITY
    with pytest.raises(BoundaryError, match="unknown_text_menu_profile"):
        renderer_identity_for_profile("text-menu-v3")


def test_complete_large_current_catalog_is_never_trimmed():
    source = fixture("targeted-root")
    card = source["referents"][0]
    actions = source["menu_actions"]["actions"]
    for ordinal in range(1, 80):
        clone = copy.deepcopy(card)
        clone["referent_id"] = f"card-{ordinal}"
        source["referents"].append(clone)
        actions.append(action(f"select-{ordinal}", "system_selection", "select_card",
                              clone["referent_id"]))
    source["menu_actions"].update(total_count=len(actions), materialized_count=len(actions))
    projected = project_text_menu_v2_snapshot(source)
    assert len(projected.action_ids) == len(projected.action_texts) == 80
    assert projected.action_ids[-1] == "select-79"
    assert '"ordinal":79' in projected.action_texts[-1]
