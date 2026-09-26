"""Synthetic public fixtures exercise Human input projection boundaries."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
from platform_bundle3_fixture import bundle3, load, seal, write
from test_artifact_store_v1 import PRODUCER, store
from test_text_menu_data import snapshot

from spireagent.json_boundary import BoundaryError, decode_json, json_bytes
from stpd.fullrun.text_menu_human_import import (
    _project,
    load_human_text_source,
    load_verified_human_text_bundle,
    publish_human_text_source,
    publish_verified_human_text_bundle,
)
from stpd.fullrun.text_menu_inputs import project_text_menu_snapshot


def observation(session: str, name: str, disposition: str = "accepted_input") -> dict:
    current = snapshot(name)
    current["menu_actions"]["actions"][1]["verb"] = "begin_card_play"
    chosen = copy.deepcopy(current["menu_actions"]["actions"][1])
    row = {
        "schema_version": 1, "schema": "sts2.human-annotator/human-text-input-1",
        "sequence": 1, "record_id": f"{session}-{name}", "session_id": session,
        "run_id": "run-1", "snapshot": current, "chosen_action": chosen,
        "mapping_status": "exact_unique", "match_count": 1,
        "mapping_basis": "text_menu_native_reference_equality",
        "native_mechanism": "begin_card_play_exact_factory_return",
        "external_controller_active": False, "disposition": disposition,
    }
    if disposition != "accepted_input":
        row["reason_code"] = "rejected"
    return row


def test_exact_begin_is_only_label_and_negative_row_remains_accounted() -> None:
    rejected = observation("session-a", "a-rejected", "rejected_or_cancelled")
    rejected["chosen_action"] = None
    failed = observation("session-a", "a-failed", "capture_failed")
    failed.pop("snapshot")
    failed.pop("chosen_action")
    rows = (observation("session-a", "a"), rejected, failed,
            observation("session-b", "b"))
    samples, report = _project(rows)
    assert len(samples) == 2
    assert {sample.split for sample in samples} == {"train", "dev"}
    assert all(sample.action_keys[sample.chosen_index] == "opaque-play" for sample in samples)
    assert [item["status"] for item in report["rows"]] == [
        "included", "excluded", "excluded", "included"]
    assert report["rows"][2]["snapshot_id"] is None
    assert report["native_successor_supervision"] is False
    assert report["human_origin"] == "explicit_owner_attestation_not_machine_verifiable"
    assert report["label_boundary"] == "owner_attested_human_exact_native_begin_input"


def test_verified_device_provenance_does_not_change_model_input() -> None:
    rows = (observation("session-a", "a"), observation("session-b", "b"))
    for row in rows:
        row["chosen_action"]["verb"] = "cancel_card_play"
        row["snapshot"]["menu_actions"]["actions"][1]["verb"] = "cancel_card_play"
        row["native_mechanism"] = "controller_canceled_input_signal"
    samples, report = _project(rows)
    targeted = copy.deepcopy(rows)
    for row in targeted:
        row["native_mechanism"] = "controller_target_canceled_input"
    other, other_report = _project(targeted)
    assert samples == other
    assert report == other_report
    assert report["label_boundary"] == "owner_attested_human_exact_native_input"
    assert all("controller_" not in sample.state_text
               and all("controller_" not in action for action in sample.action_texts)
               for sample in samples)


def test_duplicate_visible_input_collapses_sessions_and_blocks_leakage() -> None:
    rows = (observation("session-a", "same"), observation("session-b", "same"))
    with pytest.raises(BoundaryError, match="independent_groups_required"):
        _project(rows)


def test_forged_choice_cannot_become_human_label() -> None:
    rows = (observation("session-a", "a"), observation("session-b", "b"))
    rows[0]["chosen_action"]["label"] = "Unwitnessed"
    with pytest.raises(BoundaryError, match="chosen_catalog_binding_mismatch"):
        _project(rows)


def test_actual_core_serialized_row_projects_exact_current_menu() -> None:
    fixture = (Path(__file__).parents[2] / "components/evidence/tests/fixtures/human_text"
               / "core_accepted.jsonl")
    row = decode_json(fixture.read_bytes())
    assert "reason_code" not in row
    assert row["snapshot"]["referents"][0]["properties"] is None
    public = project_text_menu_snapshot(row["snapshot"])
    assert public.action_ids.count(row["chosen_action"]["action_id"]) == 1
    samples, report = _project((row, observation("synthetic-independent", "other")))
    assert len(samples) == 2
    assert {sample.split for sample in samples} == {"train", "dev"}
    assert report["rows"][0]["selected_action_id"] == row["chosen_action"]["action_id"]


def test_source_requires_verified_archived_evidence(tmp_path) -> None:
    target = store(tmp_path)
    with pytest.raises((BoundaryError, KeyError)):
        publish_human_text_source(target, (), PRODUCER)
    with pytest.raises((BoundaryError, KeyError)):
        publish_human_text_source(target, ("unverified-id",), PRODUCER)
    with pytest.raises((BoundaryError, KeyError)):
        load_human_text_source(target, "unverified-id")


@pytest.mark.parametrize(("mechanism", "verb"), [
    ("begin_card_play_exact_factory_return", "begin_card_play"),
    ("controller_confirmed_input_signal", "confirm_card"),
    ("controller_canceled_input_signal", "cancel_card_play"),
    ("controller_target_finish_input", "confirm_target"),
    ("controller_target_canceled_input", "cancel_card_play"),
])
def test_declared_bundle_archive_is_reverified_when_source_loads(tmp_path, mechanism, verb) -> None:
    bundle = bundle3(tmp_path / "fixture")
    raw = bundle / "raw"
    recording = load(raw / "recording-manifest.json")
    recording.update(text_input_schema_version=1, close_schema_version=1)
    write(raw / "recording-manifest.json", recording)
    current = snapshot("archive")
    current["session"] = {"runtime_instance_id": "runtime-1",
                          "environment_fingerprint": "environment-1"}
    current["interaction"]["content_schema"] = "combat_turn-1"
    current["interaction"]["content"] = {
        "surface": {"kind": "combat_turn"}, "context": {}}
    current["information_policy"]["scope"] = "current_page"
    current["menu_actions"]["ordering_semantics"] = "native_order_with_fixed_information_groups"
    current["menu_actions"]["actions"][1]["verb"] = verb
    artifact = {"product": "fixture", "version": "1", "source_revision": "b" * 40,
                "source_digest_sha256": "a" * 64, "sha256": "a" * 64,
                "module_version_id": "11111111-1111-1111-1111-111111111111"}
    row = {
        "schema_version": 1, "schema": "sts2.human-annotator/human-text-input-1",
        "sequence": 1, "record_id": "text-1", "session_id": recording["session_id"],
        "timeline_id": recording["timeline_id"], "run_id": "run-0001",
        "observed_at": "2026-09-26T00:00:00Z", "recorded_at": "2026-09-26T00:00:01Z",
        "environment": {"game": {"main_assembly_sha256": "a" * 64,
                                 "main_assembly_module_version_id":
                                 "11111111-1111-1111-1111-111111111111"},
                        "connector": artifact, "annotator": artifact,
                        "player_environment_protocol": "1.0.0",
                        "runtime_instance_id": "runtime-1",
                        "environment_fingerprint": "environment-1",
                        "modset_status": "exact", "modset_fingerprint": "a" * 64},
        "snapshot": current,
        "snapshot_sha256": hashlib.sha256(json_bytes(current).rstrip(b"\n")).hexdigest(),
        "chosen_action": current["menu_actions"]["actions"][1],
        "mapping_status": "exact_unique", "match_count": 1,
        "mapping_basis": "text_menu_native_reference_equality",
        "native_owner_witness_id": "owner-1", "native_subject_witness_id": "subject-1",
        "native_carrier_witness_id": "carrier-1",
        "native_mechanism": mechanism,
        "disposition": "accepted_input", "external_controller_active": False,
    }
    if verb != "begin_card_play":
        row["native_owner_witness_id"] = row["native_carrier_witness_id"]
    stream = raw / "human-text-inputs.jsonl"
    stream.write_bytes(json_bytes(row))
    write(raw / "session-close-receipt.json", {
        "schema": "sts2.human-annotator/session-close-1",
        "session_id": recording["session_id"], "timeline_id": recording["timeline_id"],
        "status": "closed", "closed_at": "2026-09-26T00:00:02Z",
        "human_text_input_count": 1,
        "human_text_inputs_sha256": hashlib.sha256(stream.read_bytes()).hexdigest(),
    })
    seal(bundle)
    target = store(tmp_path / "artifacts")
    evidence = publish_verified_human_text_bundle(target, bundle, PRODUCER)
    stream.write_bytes(b"corrupted after archive\n")
    _, verified, rows = load_verified_human_text_bundle(target, evidence.artifact_id)
    assert verified.text_input_schema_version == 1
    assert rows[0]["disposition"] == "accepted_input"
    source = publish_human_text_source(target, (evidence.artifact_id,), PRODUCER)
    _, loaded = load_human_text_source(target, source.artifact_id)
    assert loaded == rows
    samples, _ = _project((*loaded, observation("other-session", "other-page")))
    archived = next(sample for sample in samples if sample.chosen_index == 1
                    and verb in sample.action_texts[1])
    assert archived.action_keys[archived.chosen_index] == row["chosen_action"]["action_id"]
    assert mechanism not in archived.state_text
    assert all(mechanism not in action for action in archived.action_texts)
    with pytest.raises(BoundaryError, match="independent_groups_required"):
        _project(loaded)
