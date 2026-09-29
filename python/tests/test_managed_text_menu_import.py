"""Synthetic closed Managed lineage and conservative research admission."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from test_artifact_store_v1 import PRODUCER, store
from test_shared_research_curation import PrivateHost
from test_text_menu_v2_inputs import action, fixture, with_actions

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.research_curation import CurationLedger
from stpd.fullrun.managed_text_menu_import import (
    EVENT_SCHEMA,
    REPORT_SCHEMA,
    SESSION_SCHEMA,
    ManagedImportExpectation,
    import_managed_text_menu_report,
    load_managed_text_menu_source,
)
from stpd.fullrun.observed_input_sequence import load_observed_input_view
from stpd.fullrun.text_menu_inputs import project_text_menu_v2_snapshot


def _page(value, number):
    page = copy.deepcopy(value)
    page["snapshot_id"] = f"page-{number}"
    page["menu"]["revision"] = number
    page["sequence"] = number + 1
    page["session"] = {"runtime_instance_id": "runtime-a",
                       "environment_fingerprint": "4" * 64}
    project_text_menu_v2_snapshot(page)
    return page


def _sequence():
    root = _page(fixture("targeted-root"), 0)
    selected = _page(fixture("targeted-select")["successor"], 1)
    cancelled = _page(root, 2)
    reselected = _page(selected, 3)
    confirm = _page(with_actions(
        reselected, "card_confirmation",
        [{"role": "card", "referent_id": "card-C"},
         {"role": "target", "referent_id": "enemy-E"}],
        [action("opaque-confirm", "native_input", "play", "card-C",
                [{"role": "target", "referent_id": "enemy-E"}]),
         action("opaque-cancel", "system_selection", "cancel_selection")],
    ), 4)
    after = _page(root, 5)
    return [root, selected, cancelled, reselected, confirm, after], [
        "v2-select-card-C", "v2-cancel-card-C", "v2-select-card-C",
        "v2-select-target-E", "opaque-confirm",
    ]


def _archive(tmp_path: Path, *, mutate=None, session="session-a",
             scenario="fixed-a0", terminal_status=None):
    archive = store(tmp_path / "report")
    pages, choices = _sequence()
    if terminal_status is not None:
        pages[-1]["status"] = terminal_status
        pages[-1]["interaction"]["capabilities"] = []
        pages[-1]["menu_actions"].update(
            status="complete" if terminal_status == "observed" else "unavailable",
            materialized_count=0, total_count=0, actions=[],
        )
    pin = {"schema": "stpd/platform-host-runtime-pin-v1",
           "package": "@rsgcsg/sts2-host-runtime", "version": "1.1.0-rc.20",
           "source_revision": "a" * 40, "component_tree_revision": "b" * 40,
           "release_asset_sha256": "c" * 64, "package_content_sha256": "d" * 64}
    events = []
    parents = []
    for index, chosen in enumerate(choices):
        before = {"schema": "sts2.player-environment/text-menu-observation-context-2",
                  "snapshot": pages[index], "game_continuity_id": "continuity-a"}
        selected = next(a for a in pages[index]["menu_actions"]["actions"]
                        if a["action_id"] == chosen)
        request = f"request-{index}"
        result = {
            "protocol_version": "1.0.0",
            "schema": "sts2.player-environment/text-menu-action-result-2",
            "input_profile": "text-menu-v2", "request_id": request, "status": "applied",
            "effect_domain": selected["effect_domain"],
            "native_delivery": "delivered" if selected["effect_domain"] == "native_input" else None,
            "action": selected,
            "reason_code": None, "detail": "", "retry": "never",
            "successor": pages[index + 1], "attribution": None,
        }
        event = {"request_id": request, "action_id": chosen, "before_context": before,
                 "expected_snapshot_id": pages[index]["snapshot_id"],
                 "result": result, "error_code": None}
        if mutate is not None:
            mutate(index, event)
        payload = archive.put_bytes("event", json_bytes(event))
        manifest = Manifest("run_event", PRODUCER, payloads=(payload,),
                            parameters=FrozenObject.of({
            "schema": EVENT_SCHEMA, "scenario_id": scenario, "session_id": session,
            "request_id": request,
        }))
        archive.publish(manifest)
        parents.append(Parent(f"event-{index}", manifest.artifact_id))
        events.append({"request_id": request, "action_id": chosen,
                       "expected_snapshot_id": pages[index]["snapshot_id"],
                       "error_code": None, "result_status": "applied",
                       "native_delivery": result["native_delivery"],
                       "event_artifact_id": manifest.artifact_id})
    report = {"schema": SESSION_SCHEMA, "status": "stopped", "session_id": session,
              "scenario_id": scenario, "seed": "seed-a", "input_profile": "text-menu-v2",
              "host_package_pin": pin,
              "episode_identity": {
                  "candidate_build": {"upstream_revision": "e" * 40,
                      "source_patch_sha256": "1" * 64, "artifact_sha256": "2" * 64,
                      "artifact_mvid": "synthetic-mvid", "original_sts2_sha256": "3" * 64,
                      "runtime_sts2_sha256": "3" * 64},
                  "environment_fingerprint": "4" * 64,
                  "episode_provenance": {"verdict": "provenance_pass",
                      "requested_seed": "seed-a", "actual_seed": "seed-a",
                      "runtime_instance_id": "runtime-a"}},
              "events": events,
              "context": {"schema": "sts2.player-environment/text-menu-observation-context-2",
                          "snapshot": pages[-1], "game_continuity_id": "continuity-a"}}
    payload = archive.put_bytes("report", json_bytes(report))
    manifest = Manifest("analysis", PRODUCER, parents=tuple(parents), payloads=(payload,),
                        parameters=FrozenObject.of({
        "schema": REPORT_SCHEMA, "session_id": session, "scenario_id": scenario,
        "status": "stopped", "scope": "managed_text_menu_engineering_only",
    }))
    archive.publish(manifest)
    expectation = ManagedImportExpectation(
        session, scenario, "seed-a", FrozenObject.of(pin),
        FrozenObject.of(report["episode_identity"]["candidate_build"]),
        "4" * 64, "runtime-a",
    )
    return archive, manifest.artifact_id, expectation


def test_import_keeps_event_closure_and_only_observed_input_masks(tmp_path: Path):
    archive, report_id, expected = _archive(tmp_path)
    research = store(tmp_path / "research")
    source = import_managed_text_menu_report(
        archive, research, report_id, PRODUCER, expected=expected)
    assert source.manifest.parent("managed_report") == report_id
    assert len(source.inputs) == 5
    assert all(research.get_manifest(item.event_artifact_id).kind == "run_event"
               for item in source.inputs)
    view = load_observed_input_view(research, source.manifest.artifact_id)
    assert view.stream_scope == "managed_engineering_control_inputs"
    assert [item.selected_action_id for item in view.inputs] == _sequence()[1]
    assert [item.delivery_status for item in view.inputs] == [
        "not_applicable", "not_applicable", "not_applicable", "not_applicable", "delivered"]
    assert all(item.choice_mask and item.successor_observation_mask and
               not item.causal_successor_mask for item in view.inputs)
    assert [item.reset_before for item in view.inputs] == [True, False, False, False, False]
    assert load_managed_text_menu_source(research, source.manifest.artifact_id) == source


@pytest.mark.parametrize("mutation", [
    lambda i, e: e.update(error_code="native_delivery_unknown") if i == 4 else None,
    lambda i, e: e["result"].update(native_delivery="unknown") if i == 4 else None,
    lambda i, e: e["before_context"].update(game_continuity_id="other") if i == 2 else None,
    lambda i, e: e.update(expected_snapshot_id="wrong") if i == 3 else None,
    lambda i, e: e["before_context"]["snapshot"]["menu_actions"].update(
        total_count=999) if i == 0 else None,
    lambda i, e: e["result"].update(successor=_sequence()[0][0]) if i == 2 else None,
])
def test_tampered_event_fails_before_research_publication(tmp_path: Path, mutation):
    archive, report_id, expected = _archive(tmp_path, mutate=mutation)
    research = store(tmp_path / "research")
    with pytest.raises(BoundaryError):
        import_managed_text_menu_report(archive, research, report_id, PRODUCER,
                                        expected=expected)
    assert research.manifest_ids() == ()


@pytest.mark.parametrize("parents", [
    lambda rows: rows[:-1],
    lambda rows: (Parent("event-0", rows[1].artifact_id),
                  Parent("event-1", rows[0].artifact_id), *rows[2:]),
    lambda rows: (*rows, Parent("event-5", rows[0].artifact_id)),
])
def test_missing_reordered_or_extra_report_event_parent_fails(tmp_path: Path, parents):
    archive, report_id, expected = _archive(tmp_path)
    original = archive.get_manifest(report_id)
    altered = Manifest("analysis", original.producer, parents=tuple(parents(original.parents)),
                       payloads=original.payloads, parameters=original.parameters)
    archive.publish(altered)
    research = store(tmp_path / "research")
    with pytest.raises(BoundaryError, match="event_parent_mismatch|report_identity_mismatch"):
        import_managed_text_menu_report(archive, research, altered.artifact_id, PRODUCER,
                                        expected=expected)
    assert research.manifest_ids() == ()


def test_existing_ledger_groups_repeated_scenario_and_never_creates_use(tmp_path: Path):
    research = store(tmp_path / "research")
    host = PrivateHost(tmp_path / "curation.sqlite")
    ledger = CurationLedger(host)
    first_archive, first_report, first_expected = _archive(tmp_path / "first")
    second_archive, second_report, second_expected = _archive(
        tmp_path / "second", session="session-b", scenario="renamed-fixed-a0")
    first = import_managed_text_menu_report(
        first_archive, research, first_report, PRODUCER, expected=first_expected)
    second = import_managed_text_menu_report(
        second_archive, research, second_report, PRODUCER, expected=second_expected)
    assert first.split_run_id == second.split_run_id
    assert ledger.reserve_managed_observed_source(
        research, first.manifest.artifact_id, "training",
        expected=first_expected) == first.split_run_id
    assert ledger.source_runs(first.manifest.artifact_id) == {first.split_run_id}
    assert ledger.overlap({first.split_run_id}, {second.split_run_id})["overlap"]
    with pytest.raises(BoundaryError, match="managed_purpose_invalid"):
        ledger.reserve_managed_observed_source(research, second.manifest.artifact_id, "gold",
                                               expected=second_expected)
    with pytest.raises(BoundaryError, match="managed_split_purpose_overlap"):
        ledger.reserve_managed_observed_source(research, second.manifest.artifact_id, "test",
                                               expected=second_expected)
    ledger.reserve_managed_observed_source(research, second.manifest.artifact_id, "training",
                                           expected=second_expected)
    with host.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_uses").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM curation_source_uses").fetchone()[0] == 0


@pytest.mark.parametrize("status", ["observed", "settling"])
def test_final_noninteractive_public_successor_is_observation_only(tmp_path: Path,
                                                                   status: str):
    archive, report_id, expected = _archive(tmp_path, terminal_status=status)
    research = store(tmp_path / "research")
    source = import_managed_text_menu_report(
        archive, research, report_id, PRODUCER, expected=expected)
    view = load_observed_input_view(research, source.manifest.artifact_id)
    assert view.inputs[-1].successor_snapshot["status"] == status
    assert view.inputs[-1].successor_observation_mask
    assert not view.inputs[-1].causal_successor_mask


@pytest.mark.parametrize("mutation", [
    lambda i, e: e["result"]["successor"].update(sequence=e["before_context"][
        "snapshot"]["sequence"]) if i == 4 else None,
    lambda i, e: e["result"]["successor"]["session"].update(
        runtime_instance_id="different") if i == 4 else None,
    lambda i, e: e["before_context"]["snapshot"]["session"].update(
        environment_fingerprint="different") if i == 0 else None,
])
def test_sequence_and_session_drift_are_rejected(tmp_path: Path, mutation):
    archive, report_id, expected = _archive(tmp_path, mutate=mutation)
    with pytest.raises(BoundaryError):
        import_managed_text_menu_report(
            archive, store(tmp_path / "research"), report_id, PRODUCER, expected=expected)


def test_import_and_reservation_require_external_exact_expectation(tmp_path: Path):
    archive, report_id, expected = _archive(tmp_path)
    research = store(tmp_path / "research")
    wrong = ManagedImportExpectation(
        expected.session_id, expected.scenario_id, "different-seed",
        expected.host_package_pin, expected.candidate_build,
        expected.environment_fingerprint, expected.runtime_instance_id)
    with pytest.raises(BoundaryError, match="import_expectation_mismatch"):
        import_managed_text_menu_report(
            archive, research, report_id, PRODUCER, expected=wrong)
    assert research.manifest_ids() == ()
    source = import_managed_text_menu_report(
        archive, research, report_id, PRODUCER, expected=expected)
    ledger = CurationLedger(PrivateHost(tmp_path / "curation.sqlite"))
    with pytest.raises(BoundaryError, match="import_expectation_mismatch"):
        ledger.reserve_managed_observed_source(research, source.manifest.artifact_id,
                                               "training", expected=wrong)


def test_test_purpose_rejects_prior_training_use_even_without_claim(tmp_path: Path):
    archive, report_id, expected = _archive(tmp_path)
    research = store(tmp_path / "research")
    source = import_managed_text_menu_report(
        archive, research, report_id, PRODUCER, expected=expected)
    ledger = CurationLedger(PrivateHost(tmp_path / "curation.sqlite"))
    ledger.use({source.split_run_id}, "training", "earlier-model")
    with pytest.raises(BoundaryError, match="managed_test_previously_used_for_training"):
        ledger.reserve_managed_observed_source(research, source.manifest.artifact_id,
                                               "test", expected=expected)
    assert ledger.source_runs(source.manifest.artifact_id) is None


def test_test_purpose_rejects_prior_source_training_use_before_index(tmp_path: Path):
    archive, report_id, expected = _archive(tmp_path)
    research = store(tmp_path / "research")
    source = import_managed_text_menu_report(
        archive, research, report_id, PRODUCER, expected=expected)
    ledger = CurationLedger(PrivateHost(tmp_path / "curation.sqlite"))
    ledger.use_source(source.manifest.artifact_id, "training", "earlier-model")
    with pytest.raises(BoundaryError, match="managed_test_previously_used_for_training"):
        ledger.reserve_managed_observed_source(research, source.manifest.artifact_id,
                                               "test", expected=expected)
