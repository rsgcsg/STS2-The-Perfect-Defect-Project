"""Exact text decision/result binding before Agent source admission."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from test_artifact_store_v1 import PRODUCER, store
from test_text_menu_data import row

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.text_menu_data import load_text_menu_source, publish_text_menu_source
from stpd.fullrun.text_menu_inputs import project_text_menu_snapshot
from stpd.fullrun.text_menu_runtime_import import (
    _trace_rows,
    load_verified_agent_run_artifact,
    publish_verified_text_menu_run,
)

sys.path.insert(0, str(Path(__file__).parents[2] / "components/evidence/tests"))


def _events(*, outcome: bool = True, dispatch: bool = True):
    source = row("agent", origin="agent", native=True)
    snapshot = source["snapshot"]
    selected = source["selected_action_id"]
    decision_id = "decision-1"
    run_id = "agent-run"
    source["result"]["request_id"] = f"request-{run_id}-{decision_id}"
    events = [
        {"sequence": 1, "kind": "text_decision_input", "payload": {
            "decision_id": decision_id, "snapshot": snapshot}},
        {"sequence": 2, "kind": "decision", "payload": {
            "decision": {"decision_id": decision_id, "snapshot_id": snapshot["snapshot_id"],
                         "candidate_digest": project_text_menu_snapshot(snapshot).candidate_digest,
                         "candidate_count": 2, "selected_index": 1},
            "resolved_bound_action_id": selected}},
    ]
    if dispatch:
        events.append({"sequence": 3, "kind": "text_menu_dispatch_attempt", "payload": {
            "decision_id": decision_id, "action_id": selected, "effect_domain": "native_input"}})
    if outcome:
        events.append({"sequence": 4, "kind": "text_native_delivery", "payload": {
            "decision_id": decision_id, "result": source["result"]}})
    return events


def test_correlated_native_delivery_keeps_current_input_and_exact_request():
    rows, report = _trace_rows(_events(), "agent-run", "evidence-content")
    assert len(rows) == 1
    assert rows[0]["origin"] == "agent"
    assert rows[0]["source_ref"].endswith("#input=1&outcome=4")
    assert rows[0]["selected_action_id"] == "opaque-play"
    assert report["outcomes"] == {"applied": 1}


def test_dispatch_without_result_is_diagnostic_and_never_a_label():
    rows, report = _trace_rows(_events(outcome=False), "agent-run", "evidence-content")
    assert rows == ()
    assert report["diagnostics"][0]["reason"] == "dispatch_without_correlated_result"


def test_shadow_decision_without_dispatch_is_not_a_label():
    rows, report = _trace_rows(_events(outcome=False, dispatch=False),
                               "agent-run", "evidence-content")
    assert rows == ()
    assert report["diagnostics"][0]["reason"] == "abstained_or_not_dispatched"


def test_result_request_or_action_drift_is_rejected():
    events = _events()
    events[3]["payload"]["result"]["request_id"] = "other-request"
    with pytest.raises(BoundaryError, match="request_id_mismatch"):
        _trace_rows(events, "agent-run", "evidence-content")
    events = _events()
    events[3]["payload"]["result"]["action"] = copy.deepcopy(
        events[3]["payload"]["result"]["action"])
    events[3]["payload"]["result"]["action"]["label"] = "Wrong"
    with pytest.raises(BoundaryError, match="outcome_binding_mismatch"):
        _trace_rows(events, "agent-run", "evidence-content")


@pytest.fixture
def verified_fixture():
    from test_agent_run_evidence import TextMenuAgentRunEvidenceTests

    fixture = TextMenuAgentRunEvidenceTests(
        "test_text_navigation_is_verified_without_native_receipt")
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.tearDown()


@pytest.mark.parametrize("native", [False, True])
def test_finalized_evidence_archive_is_exact_parent_and_reloaded(
    tmp_path, verified_fixture, native,
):
    directory = verified_fixture._text_evidence(
        "text-native" if native else "text-nav", native=native)
    target = store(tmp_path)
    evidence, source, report = publish_verified_text_menu_run(
        target, directory, PRODUCER, admit_agent=True)
    assert source is not None
    assert source.parent("verified_agent_run") == evidence.artifact_id
    assert report["rows"] == 1
    verified, expected = load_verified_agent_run_artifact(target, evidence.artifact_id)
    assert verified.content_id == evidence.parameters.value()["content_id"]
    assert load_text_menu_source(target, source.artifact_id)[1] == expected
    assert expected[0]["source_ref"].startswith(f"agent-run://{verified.content_id}/")
    if native:
        assert expected[0]["result"]["successor"] is None
    else:
        assert expected[0]["result"]["successor"] is not None
    with pytest.raises(BoundaryError, match="verified_agent_evidence_required"):
        publish_text_menu_source(target, expected, PRODUCER, admit_agent=True)


def test_failed_native_result_kept_in_source_but_not_turned_into_delivery(
    tmp_path, verified_fixture,
):
    directory = verified_fixture._text_evidence("text-unknown", native=True)
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    events[5]["kind"] = "text_native_unknown"
    events[5]["payload"]["result"].update(
        status="unknown", native_delivery="unknown", successor=None, retry="never")
    events.pop(6)
    for sequence, event in enumerate(events, 1):
        event["sequence"] = sequence
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(status="tainted", tainted=True)
    from test_agent_run_evidence import canonical  # noqa: E402
    manifest_path.write_bytes(canonical(manifest))
    verified_fixture._rewrite_events(directory, events)
    evidence, source, report = publish_verified_text_menu_run(
        store(tmp_path), directory, PRODUCER, admit_agent=True)
    assert evidence.kind == "evidence"
    assert source is not None
    assert report["outcomes"] == {"unknown": 1}


def test_undispatched_decision_archived_without_agent_label(tmp_path, verified_fixture):
    directory = verified_fixture._text_evidence("text-shadow")
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    verified_fixture._rewrite_events(directory, events[:3])
    target = store(tmp_path)
    evidence, source, report = publish_verified_text_menu_run(
        target, directory, PRODUCER, admit_agent=True)
    assert source is None
    assert report["diagnostics"][0]["reason"] == "abstained_or_not_dispatched"
    assert load_verified_agent_run_artifact(target, evidence.artifact_id)[1] == ()
