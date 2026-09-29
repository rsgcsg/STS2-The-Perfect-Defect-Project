"""Synthetic regressions for read-only, partial observed-input windows."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_artifact_store_v1 import PRODUCER, store
from test_text_menu_data import snapshot
from test_text_menu_human_import import observation

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.observed_input_sequence import (
    ObservedInput,
    ObservedInputView,
    SourceEventRef,
    build_fixed_windows,
    load_observed_input_view,
)
from stpd.fullrun.text_menu_runtime_import import publish_verified_text_menu_run

sys.path.insert(0, str(Path(__file__).parents[2] / "components/evidence/tests"))


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


def _input(
    event_id: str, sequence: int, *, reset: bool = False, choice: bool = True,
) -> ObservedInput:
    return ObservedInput(
        stream_id="agent:content:run", source_kind="agent_decision_inputs",
        event_id=event_id, source_sequence=sequence, snapshot=snapshot("same-page"),
        observation_mask=True,
        selected_action_id="opaque-play" if choice else None, choice_mask=choice,
        delivery_status="unknown", delivery_mask=False, successor_snapshot=None,
        successor_relation="unknown", successor_observation_mask=False,
        causal_successor_mask=False, reset_before=reset,
        reset_reason="stream_start" if reset else None,
        source_events=(SourceEventRef(sequence, "text_decision_input", event_id),),
    )


def test_fixed_windows_preserve_real_events_and_ignore_numeric_sequence_gaps():
    first, returned = _input("event-a", 4, reset=True), _input("event-b", 29)
    view = ObservedInputView("evidence", "verified_agent_observed_inputs", False,
                             (first, returned))

    (window,) = build_fixed_windows(view, learn_steps=2, burn_in_steps=1)

    assert len(window.inputs) == 3
    assert window.inputs[0] is None
    assert window.inputs[1:] == (first, returned)
    assert window.valid_mask == (False, True, True)
    assert window.observation_mask == (False, True, True)
    assert window.burn_in_mask == (False, False, False)
    assert window.choice_mask == (False, True, True)
    assert window.delivery_mask == (False, False, False)
    assert window.causal_successor_mask == (False, False, False)
    # A real return to the same page remains a second event because identity differs.
    assert first.snapshot == returned.snapshot and first.event_id != returned.event_id


def test_fixed_windows_split_only_at_explicit_reset_and_reject_duplicate_event_ids():
    first, reset = _input("event-a", 10, reset=True), _input("event-b", 11, reset=True)
    view = ObservedInputView("evidence", "verified_agent_observed_inputs", False,
                             (first, reset))
    windows = build_fixed_windows(view, learn_steps=1, burn_in_steps=1)
    assert len(windows) == 2
    assert all(len(window.inputs) == 2 for window in windows)
    assert all(window.burn_in_mask == (False, False) for window in windows)
    with pytest.raises(BoundaryError, match="duplicate_observed_event"):
        build_fixed_windows(
            ObservedInputView("evidence", "verified_agent_observed_inputs", False,
                              (first, first)),
            learn_steps=1, burn_in_steps=0,
        )
    with pytest.raises(BoundaryError, match="event_order_mismatch"):
        build_fixed_windows(
            ObservedInputView("evidence", "verified_agent_observed_inputs", False,
                              (_input("late", 29, reset=True), _input("early", 4))),
            learn_steps=1, burn_in_steps=0,
        )


def test_nonempty_burn_in_context_is_masked_from_learning_labels():
    items = tuple(_input(f"event-{index}", index, reset=index == 1)
                  for index in range(1, 6))
    view = ObservedInputView("evidence", "verified_agent_observed_inputs", False, items)

    windows = build_fixed_windows(view, learn_steps=2, burn_in_steps=1)

    assert len(windows) == 3
    assert windows[0].inputs == (None, items[0], items[1])
    assert windows[0].burn_in_mask == (False, False, False)
    assert windows[0].choice_mask == (False, True, True)
    assert windows[1].inputs == (items[1], items[2], items[3])
    assert windows[1].burn_in_mask == (True, False, False)
    assert windows[1].choice_mask == (False, True, True)
    assert windows[2].inputs == (items[3], items[4], None)
    assert windows[2].burn_in_mask == (True, False, False)
    assert windows[2].valid_mask == (True, True, False)
    assert windows[2].choice_mask == (False, True, False)


def test_human_inputs_are_partial_and_keep_choice_separate_from_delivery(monkeypatch):
    import stpd.fullrun.observed_input_sequence as module

    accepted = observation("session-a", "accepted")
    accepted.update(timeline_id="timeline-a")
    rejected = observation("session-a", "rejected", "rejected_or_cancelled")
    rejected.update(timeline_id="timeline-a", sequence=6)
    rejected["chosen_action"] = None
    missing = observation("session-a", "capture-failed", "capture_failed")
    missing.update(timeline_id="timeline-a", sequence=2)
    missing.pop("snapshot")
    missing.pop("chosen_action")
    after_missing = observation("session-a", "after-missing")
    after_missing.update(timeline_id="timeline-a", sequence=3)
    another_run = observation("session-a", "another-run")
    another_run.update(timeline_id="timeline-a", run_id="run-2", sequence=1)
    returned_identity = observation("session-a", "returned-identity")
    returned_identity.update(timeline_id="timeline-a", sequence=5)
    manifest = SimpleNamespace(parameters=SimpleNamespace(value=lambda: {
        "schema": "stpd/human-text-input-source-v1"}))
    monkeypatch.setattr(module, "load_human_text_source", lambda _store, _identity: (
        manifest, (accepted, missing, after_missing, another_run, returned_identity, rejected)))

    view = load_observed_input_view(SimpleNamespace(get_manifest=lambda _id: manifest), "source")

    assert view.stream_scope == "partial_human_input_stream"
    assert view.trajectory_complete is False
    assert len(view.inputs) == 6
    assert view.inputs[0].choice_mask is True
    assert view.inputs[0].delivery_status == "human_witness_only"
    assert view.inputs[0].delivery_mask is False
    assert view.inputs[0].causal_successor_mask is False
    assert view.inputs[1].observation_mask is False
    assert view.inputs[1].choice_mask is False
    assert view.inputs[2].reset_before is True
    assert view.inputs[2].reset_reason == "after_missing_observation"
    assert view.inputs[3].observation_mask is True
    assert view.inputs[3].choice_mask is True
    assert view.inputs[3].reset_before is True
    assert view.inputs[3].reset_reason == "identity_change"
    assert view.inputs[4].reset_before is True
    assert view.inputs[4].reset_reason == "identity_change"
    windows = build_fixed_windows(view, learn_steps=1, burn_in_steps=0)
    assert len(windows) == 5
    assert all(len(window.inputs) == 1 for window in windows)


def test_unknown_agent_delivery_does_not_become_not_delivered():
    item = _input("unknown-action", 1, reset=True)
    assert item.delivery_status == "unknown"
    assert item.delivery_mask is False
    assert item.snapshot["snapshot_id"] == "opaque-snapshot-same-page"


def _ordered_human_row(append: int, capture: int | None, *,
                       runtime: str | None = "runtime-a", run: str = "run-1",
                       missing: bool = False) -> dict:
    row = observation("session-a", f"append-{append}")
    row.update(schema_version=2, schema="sts2.human-annotator/human-text-input-2",
               sequence=append, timeline_id="timeline-a", run_id=run,
               observation_order={"capture_ordinal": capture, "completed_append_watermark": 0})
    if runtime is not None:
        row["environment"] = {"runtime_instance_id": runtime,
                              "environment_fingerprint": "environment-a"}
    if missing:
        row.update(snapshot=None, chosen_action=None, disposition="capture_failed")
    return row


def _human_rows_view(monkeypatch, rows):
    import stpd.fullrun.observed_input_sequence as module

    manifest = SimpleNamespace(parameters=SimpleNamespace(value=lambda: {
        "schema": "stpd/human-text-input-source-v1"}))
    monkeypatch.setattr(module, "load_human_text_source", lambda _store, _identity: (
        manifest, tuple(rows)))
    return load_observed_input_view(SimpleNamespace(get_manifest=lambda _id: manifest), "source")


def test_human_row2_nested_append_order_uses_capture_and_retains_physical_refs(monkeypatch):
    rows = [_ordered_human_row(1, 2), _ordered_human_row(2, 1), _ordered_human_row(3, 19)]
    # Same page and a large capture gap are not an inferred missing Human input.
    for row in rows:
        row["snapshot"] = copy.deepcopy(rows[0]["snapshot"])
        row["chosen_action"] = copy.deepcopy(rows[0]["chosen_action"])
    view = _human_rows_view(monkeypatch, rows)

    assert [item.source_sequence for item in view.inputs] == [1, 2, 19]
    assert [item.source_events[0].sequence for item in view.inputs] == [2, 1, 3]
    assert [item.reset_before for item in view.inputs] == [True, False, False]
    assert len(build_fixed_windows(view, learn_steps=8, burn_in_steps=0)) == 1
    assert all(not item.delivery_mask and not item.causal_successor_mask for item in view.inputs)


def test_human_row2_known_capture_without_observation_splits_at_capture_not_append(monkeypatch):
    view = _human_rows_view(monkeypatch, [
        _ordered_human_row(1, 2, missing=True), _ordered_human_row(2, 1),
        _ordered_human_row(3, 3), _ordered_human_row(4, 4),
    ])

    assert [item.source_sequence for item in view.inputs] == [1, 2, 3, 4]
    assert [item.source_events[0].sequence for item in view.inputs] == [2, 1, 3, 4]
    assert [item.reset_reason for item in view.inputs] == [
        "stream_start", "missing_observation", "after_missing_observation", None]


@pytest.mark.parametrize("failed_runtime", ["runtime-a", None])
def test_human_row2_unlocated_failure_never_bridges_outer_capture_to_later(
    monkeypatch, failed_runtime,
):
    view = _human_rows_view(monkeypatch, [
        _ordered_human_row(1, 2),
        _ordered_human_row(2, None, runtime=failed_runtime, missing=True),
        _ordered_human_row(3, 1), _ordered_human_row(4, 3),
        _ordered_human_row(5, 1, runtime="runtime-b"),
        _ordered_human_row(6, 2, runtime="runtime-b"),
        _ordered_human_row(7, 4, run="run-2"), _ordered_human_row(8, 5, run="run-2"),
    ])

    by_append = {item.source_events[0].sequence: item for item in view.inputs}
    assert all(by_append[index].reset_before for index in (1, 2, 3, 4))
    assert len({by_append[index].stream_id for index in (1, 2, 3, 4)}) == 4
    assert by_append[6].reset_before is (failed_runtime is None)
    assert by_append[8].reset_before is False  # An unrelated run remains usable.
    assert all(by_append[index].choice_mask for index in (1, 3, 4, 5, 6, 7, 8))


def test_human_row2_capture_order_splits_runs_and_does_not_rejoin_returned_run(monkeypatch):
    view = _human_rows_view(monkeypatch, [
        _ordered_human_row(1, 2, run="run-2"), _ordered_human_row(2, 1),
        _ordered_human_row(3, 3), _ordered_human_row(4, 4),
    ])

    assert [item.source_events[0].sequence for item in view.inputs] == [2, 1, 3, 4]
    assert [item.reset_before for item in view.inputs] == [True, True, True, False]
    assert view.inputs[0].stream_id != view.inputs[2].stream_id


@pytest.mark.parametrize(
    ("native", "expected_delivery", "expected_successor"),
    [(False, "not_applicable", "ui_navigation"),
     (True, "delivered", "post_native_observation")],
)
def test_agent_view_uses_verified_full_archive_and_keeps_successor_kinds_distinct(
    tmp_path, verified_fixture, native, expected_delivery, expected_successor,
):
    from stpd.fullrun.observed_input_sequence import load_observed_input_view

    directory = verified_fixture._text_evidence(f"sequence-{native}", native=native)
    target = store(tmp_path)
    evidence, _, _ = publish_verified_text_menu_run(
        target, directory, PRODUCER, admit_agent=True)

    view = load_observed_input_view(target, evidence.artifact_id)

    assert view.stream_scope == "verified_agent_observed_inputs"
    assert view.trajectory_complete is False
    assert len(view.inputs) == 1
    item = view.inputs[0]
    assert item.observation_mask is True
    assert item.choice_mask is True
    assert item.delivery_status == expected_delivery
    assert item.successor_relation == expected_successor
    assert item.successor_observation_mask is True
    assert item.causal_successor_mask is False
    assert item.source_events[0].kind == "text_decision_input"
    assert item.source_events[0].event_id == ":".join(item.event_id.split(":")[:2])


def test_verified_two_decision_archive_splits_on_owner_reset_event(
    tmp_path, verified_fixture,
):
    directory = verified_fixture._text_evidence("sequence-two-decisions")
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    input_event = next(event for event in events if event["kind"] == "text_decision_input")
    decision_event = next(event for event in events if event["kind"] == "decision")
    second_input = copy.deepcopy(input_event)
    second_input["payload"]["decision_id"] = "decision-2"
    second_input["payload"]["snapshot"]["snapshot_id"] = "text-3"
    second_input["payload"]["snapshot"]["sequence"] = 3
    second_decision = copy.deepcopy(decision_event)
    second_decision["payload"]["decision"]["decision_id"] = "decision-2"
    second_decision["payload"]["decision"]["snapshot_id"] = "text-3"
    boundary = {
        "schema": input_event["schema"], "sequence": 0,
        "recorded_at": input_event["recorded_at"], "kind": "mode_changed",
        "payload": {"mode": "one_step"},
    }
    release_index = next(index for index, event in enumerate(events)
                         if event["kind"] == "controller_released")
    events[release_index + 1:release_index + 1] = [boundary, second_input, second_decision]
    for sequence, event in enumerate(events, 1):
        event["sequence"] = sequence
    verified_fixture._rewrite_events(directory, events)
    target = store(tmp_path)
    evidence, _, _ = publish_verified_text_menu_run(
        target, directory, PRODUCER, admit_agent=True)

    view = load_observed_input_view(target, evidence.artifact_id)

    assert len(view.inputs) == 2
    assert view.inputs[0].reset_reason == "environment_admitted"
    assert view.inputs[1].reset_before is True
    assert view.inputs[1].reset_reason == "mode_changed"
    assert view.inputs[1].choice_mask is True
    assert view.inputs[1].delivery_status == "not_attempted"
    windows = build_fixed_windows(view, learn_steps=2, burn_in_steps=0)
    assert len(windows) == 2
    assert [window.inputs[0].event_id for window in windows] == [
        item.event_id for item in view.inputs]


def test_agent_unknown_delivery_keeps_observation_and_unknown_masks(
    tmp_path, verified_fixture,
):
    directory = verified_fixture._text_evidence("sequence-unknown", native=True)
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    outcome = next(event for event in events if event["kind"] == "text_native_delivery")
    outcome["kind"] = "text_native_unknown"
    outcome["payload"]["result"].update(
        status="unknown", native_delivery="unknown", successor=None, retry="never")
    events = [event for event in events if event["kind"] != "text_observed_successor"]
    for sequence, event in enumerate(events, 1):
        event["sequence"] = sequence
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(status="tainted", tainted=True)
    from test_agent_run_evidence import canonical
    manifest_path.write_bytes(canonical(manifest))
    verified_fixture._rewrite_events(directory, events)
    target = store(tmp_path)
    evidence, _, _ = publish_verified_text_menu_run(
        target, directory, PRODUCER, admit_agent=True)

    item = load_observed_input_view(target, evidence.artifact_id).inputs[0]

    assert item.observation_mask is True
    assert item.choice_mask is True
    assert item.delivery_status == "unknown"
    assert item.delivery_mask is False
    assert item.successor_observation_mask is False
    assert item.causal_successor_mask is False


@pytest.mark.parametrize(
    ("native", "delivery", "effect_domain", "expected"),
    [(False, None, "text_menu", "not_applicable"),
     (True, "not_delivered", "native_input", "not_delivered")],
)
def test_verified_not_applied_outcomes_preserve_delivery_knowledge(
    tmp_path, verified_fixture, native, delivery, effect_domain, expected,
):
    directory = verified_fixture._text_evidence(f"sequence-not-applied-{native}", native=native)
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    outcome = next(event for event in events if event["kind"] in {
        "menu_navigation", "text_native_delivery",
    })
    outcome["kind"] = "text_menu_not_applied"
    outcome["payload"].pop("action_id", None)
    result = outcome["payload"]["result"]
    result.update(status="not_applied", native_delivery=delivery,
                  successor=None, retry="reobserve")
    result["effect_domain"] = effect_domain
    events = [event for event in events if event["kind"] != "text_observed_successor"]
    for sequence, event in enumerate(events, 1):
        event["sequence"] = sequence
    verified_fixture._rewrite_events(directory, events)
    target = store(tmp_path)
    evidence, _, _ = publish_verified_text_menu_run(
        target, directory, PRODUCER, admit_agent=True)

    item = load_observed_input_view(target, evidence.artifact_id).inputs[0]

    assert item.delivery_status == expected
    assert item.delivery_mask is (expected == "not_delivered")
    assert item.observation_mask is True
    assert item.successor_snapshot is None
    assert item.causal_successor_mask is False


def test_missing_outcome_after_dispatch_is_unknown_not_not_delivered(
    tmp_path, verified_fixture,
):
    directory = verified_fixture._text_evidence("sequence-missing-outcome", native=True)
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    events = [event for event in events if event["kind"] not in {
        "text_native_delivery", "text_observed_successor",
    }]
    for sequence, event in enumerate(events, 1):
        event["sequence"] = sequence
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(status="tainted", tainted=True)
    from test_agent_run_evidence import canonical
    manifest_path.write_bytes(canonical(manifest))
    verified_fixture._rewrite_events(directory, events)
    target = store(tmp_path)
    evidence, _, _ = publish_verified_text_menu_run(
        target, directory, PRODUCER, admit_agent=True)

    item = load_observed_input_view(target, evidence.artifact_id).inputs[0]

    assert item.delivery_status == "unknown"
    assert item.delivery_mask is False
    assert item.observation_mask is True
    assert item.successor_snapshot is None
    assert item.causal_successor_mask is False


def test_unpaired_tainted_agent_input_keeps_only_observation(
    tmp_path, verified_fixture,
):
    directory = verified_fixture._text_evidence("sequence-unpaired", native=True)
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    input_index = next(index for index, event in enumerate(events)
                       if event["kind"] == "text_decision_input")
    events = events[:input_index + 1]
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(status="tainted", tainted=True)
    from test_agent_run_evidence import canonical
    manifest_path.write_bytes(canonical(manifest))
    verified_fixture._rewrite_events(directory, events)
    target = store(tmp_path)
    evidence, _, _ = publish_verified_text_menu_run(
        target, directory, PRODUCER, admit_agent=True)

    (item,) = load_observed_input_view(target, evidence.artifact_id).inputs

    assert item.observation_mask is True
    assert item.choice_mask is False
    assert item.selected_action_id is None
    assert item.delivery_status == "not_attempted"
    assert item.delivery_mask is False
    assert item.successor_snapshot is None
    assert item.causal_successor_mask is False
