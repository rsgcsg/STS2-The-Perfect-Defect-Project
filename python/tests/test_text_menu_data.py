"""Synthetic U trace roundtrip and fail-closed admission; no Human proof."""

from __future__ import annotations

import copy
import io
from dataclasses import replace

import pytest

from test_artifact_store_v1 import PRODUCER, store

from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.fullrun.text_menu_data import (
    SOURCE_SCHEMA, load_text_menu_bc_view, load_text_menu_source,
    publish_text_menu_bc_view, publish_text_menu_source,
)


def snapshot(name: str):
    return {
        "protocol_version": "1.0.0", "schema": "sts2.player-environment/text-menu-snapshot-1",
        "input_profile": "text-menu-v1", "snapshot_id": f"opaque-snapshot-{name}",
        "sequence": 1, "observed_at": "2026-09-26T00:00:00Z", "status": "interactive",
        "persistent": {"content": {"run": {"floor": 3 + sum(map(ord, name)) % 10},
                                  "player": {"hp": 42}}},
        "interaction": {"kind": "combat_turn", "interaction_id": f"opaque-interaction-{name}",
                        "content": {"phase": "player", "cards": [
                            {"entity_id": "opaque-card-1", "name": "Defend",
                             "description": "Gain 5 Block."}]}},
        "referents": [{"referent_id": "opaque-card-1", "kind": "entity",
                       "label": "Defend", "role": "hand_card", "state": {"visible": True},
                       "properties": {"name": "Defend", "description": "Gain 5 Block."}}],
        "completeness": {"status": "complete"},
        "information_policy": {"includes_hidden_information": False}, "session": {},
        "menu": {"cursor": "root", "revision": 1,
                 "native_snapshot_id": f"opaque-native-{name}"},
        "menu_actions": {"status": "complete", "materialized_count": 2,
                         "total_count": 2, "ordering_semantics": "native_order", "actions": [
            {"action_id": "opaque-nav", "kind": "system_navigation",
             "verb": "open_information", "label": "Information", "subject_referent_id": None,
             "arguments": [], "effect_domain": "text_menu"},
            {"action_id": "opaque-play", "kind": "native_input", "verb": "play",
             "label": "Play Defend", "subject_referent_id": "opaque-card-1",
             "arguments": [], "effect_domain": "native_input"},
        ]},
    }


def row(run: str, *, origin: str = "synthetic", native: bool = False):
    before, after = snapshot(run + "-before"), snapshot(run + "-after")
    if not native:
        after["menu"]["cursor"] = "information"
    action = before["menu_actions"]["actions"][int(native)]
    return {"schema": SOURCE_SCHEMA, "record_id": f"record-{run}", "run_id": run,
            "step_index": 0, "origin": origin, "source_ref": f"fixture/{run}/0",
            "snapshot": before, "selected_action_id": action["action_id"],
            "request": {"request_id": f"request-{run}",
                        "expected_snapshot_id": before["snapshot_id"],
                        "bound_action_id": action["action_id"], "input_profile": "text-menu-v1"},
            "result": {"schema": "sts2.player-environment/text-menu-action-result-1",
                       "input_profile": "text-menu-v1", "request_id": f"request-{run}",
                       "status": "applied", "effect_domain": action["effect_domain"],
                       "native_delivery": "delivered" if native else None,
                       "action": action, "successor": after, "retry": "never"}}


def test_source_view_loader_reprojects_and_retains_u_lineage(tmp_path):
    target = store(tmp_path)
    source = publish_text_menu_source(target, (row("a"), row("b", native=True)), PRODUCER)
    view = publish_text_menu_bc_view(target, source.artifact_id, PRODUCER)
    loaded, samples = load_text_menu_bc_view(target, view)
    assert loaded.artifact_id == view.artifact_id
    assert {sample.split for sample in samples} == {"train", "dev"}
    assert samples[0].action_keys == ("opaque-nav", "opaque-play")
    assert samples[1].chosen_index == 1
    report = target.bytes(view.payload("lineage"))
    assert b'"native_delivery":"delivered"' in report
    assert b'"native_successor_supervision":false' in report
    assert b'"human_origin_verified":false' in report
    forged_payload = target.put_payload("samples", io.BytesIO(json_bytes(samples[0].to_dict())))
    with pytest.raises(BoundaryError, match="view_projection_mismatch"):
        load_text_menu_bc_view(target, replace(view, payloads=(forged_payload, view.payload("lineage"))))


def test_agent_requires_opt_in_and_human_has_no_label_mapping(tmp_path):
    target = store(tmp_path)
    with pytest.raises(BoundaryError, match="agent_admission_opt_in_required"):
        publish_text_menu_source(target, (row("a", origin="agent"), row("b")), PRODUCER)
    source = publish_text_menu_source(
        target, (row("a", origin="agent"), row("b")), PRODUCER, admit_agent=True)
    assert len(load_text_menu_source(target, source.artifact_id)[1]) == 2
    with pytest.raises(BoundaryError, match="human_observation_mapping_unsupported"):
        publish_text_menu_source(target, (row("a", origin="human"), row("b")), PRODUCER)


def test_failed_u_step_kept_in_source_but_excluded_from_bc(tmp_path):
    target = store(tmp_path)
    failed = row("a")
    failed["result"].update(status="not_applied", retry="reobserve",
                            native_delivery=None, successor=failed["snapshot"])
    source = publish_text_menu_source(target, (failed, row("b"), row("c")), PRODUCER)
    assert len(load_text_menu_source(target, source.artifact_id)[1]) == 3
    view = publish_text_menu_bc_view(target, source.artifact_id, PRODUCER)
    _, samples = load_text_menu_bc_view(target, view)
    assert len(samples) == 2
    assert b'"reason":"not_applied"' in target.bytes(view.payload("lineage"))


def test_duplicate_public_input_cannot_cross_train_dev(tmp_path):
    target = store(tmp_path)
    first, second = row("a"), row("b")
    second["snapshot"]["persistent"] = copy.deepcopy(first["snapshot"]["persistent"])
    # Both inputs have the same visible state and catalog; opaque IDs differ.
    source = publish_text_menu_source(target, (first, second), PRODUCER)
    with pytest.raises(BoundaryError, match="independent_components_required"):
        publish_text_menu_bc_view(target, source.artifact_id, PRODUCER)


def test_adjacent_steps_preserve_order_without_inventing_causality(tmp_path):
    first, second = row("a"), row("a-next")
    second.update(run_id="a", step_index=1)
    # Async game progress can occur between two recorded U observations.
    target = store(tmp_path)
    source = publish_text_menu_source(target, (first, second), PRODUCER)
    assert len(load_text_menu_source(target, source.artifact_id)[1]) == 2
    second["step_index"] = 0
    with pytest.raises(BoundaryError, match="duplicate_record_or_step"):
        publish_text_menu_source(target, (first, second), PRODUCER)


def test_native_delivery_may_leave_same_page_or_have_no_observation(tmp_path):
    target = store(tmp_path)
    same = row("a", native=True)
    same["result"]["successor"] = same["snapshot"]
    unavailable = row("b", native=True)
    unavailable["result"].update(successor=None,
                                 reason_code="successor_observation_unavailable")
    terminal = row("c", native=True)
    terminal["result"]["successor"]["status"] = "terminal"
    terminal["result"]["successor"]["menu_actions"] = {
        "status": "unavailable", "materialized_count": 0,
        "total_count": 0, "ordering_semantics": "native_order", "actions": []}
    source = publish_text_menu_source(target, (same, unavailable, terminal), PRODUCER)
    view = publish_text_menu_bc_view(target, source.artifact_id, PRODUCER)
    _, samples = load_text_menu_bc_view(target, view)
    assert len(samples) == 3
    lineage = target.bytes(view.payload("lineage"))
    assert b'"successor_snapshot_id":null' in lineage


def test_unknown_native_delivery_is_logged_but_never_bc_label(tmp_path):
    target = store(tmp_path)
    unknown = row("a", native=True)
    unknown["result"].update(status="unknown", native_delivery="unknown",
                             successor=None, reason_code="input_delivery_unknown")
    source = publish_text_menu_source(target, (unknown, row("b"), row("c")), PRODUCER)
    view = publish_text_menu_bc_view(target, source.artifact_id, PRODUCER)
    _, samples = load_text_menu_bc_view(target, view)
    assert len(samples) == 2
    assert b'"reason":"unknown"' in target.bytes(view.payload("lineage"))
    bad = copy.deepcopy(unknown)
    bad["result"]["native_delivery"] = None
    with pytest.raises(BoundaryError, match="unknown_native_delivery_mismatch"):
        publish_text_menu_source(target, (bad, row("b")), PRODUCER)


def test_rejected_native_keeps_current_observation_without_training_label(tmp_path):
    target = store(tmp_path)
    rejected = row("a", native=True)
    rejected["result"].update(status="not_applied", retry="reobserve",
                              native_delivery="not_delivered", successor=rejected["snapshot"])
    source = publish_text_menu_source(target, (rejected, row("b"), row("c")), PRODUCER)
    view = publish_text_menu_bc_view(target, source.artifact_id, PRODUCER)
    assert len(load_text_menu_bc_view(target, view)[1]) == 2


def test_navigation_is_u_only_even_if_native_page_changes_concurrently(tmp_path):
    target = store(tmp_path)
    navigation = row("a")
    assert (navigation["snapshot"]["menu"]["native_snapshot_id"]
            != navigation["result"]["successor"]["menu"]["native_snapshot_id"])
    source = publish_text_menu_source(target, (navigation, row("b")), PRODUCER)
    view = publish_text_menu_bc_view(target, source.artifact_id, PRODUCER)
    assert len(load_text_menu_bc_view(target, view)[1]) == 2


@pytest.mark.parametrize("change,code", [
    (lambda r: r["request"].update(expected_snapshot_id="wrong"), "request_frame_binding_mismatch"),
    (lambda r: r["result"].update(native_delivery="delivered"), "applied_effect_mismatch"),
    (lambda r: r["result"].update(successor=r["snapshot"]), "unchanged_navigation_identity"),
    (lambda r: r["result"].update(action={**r["result"]["action"], "label": "Other"}), "result_choice_mismatch"),
])
def test_trace_fails_on_binding_or_effect_drift(tmp_path, change, code):
    first = row("a")
    change(first)
    with pytest.raises(BoundaryError, match=code):
        publish_text_menu_source(store(tmp_path), (first, row("b")), PRODUCER)
