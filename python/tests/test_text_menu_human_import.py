"""Synthetic public fixtures exercise Human input projection boundaries."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from platform_bundle3_fixture import bundle3, load, seal, write
from test_artifact_store_v1 import PRODUCER, store
from test_text_menu_data import snapshot

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, json_bytes
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.fullrun.features import _load_model_view, load_model_view
from stpd.fullrun.observed_input_sequence import load_observed_input_view
from stpd.fullrun.text_menu_human_import import (
    LEGACY_VIEW_SCHEMA,
    SOURCE_SCHEMA,
    VIEW_SCHEMA,
    _project,
    load_human_text_source,
    load_verified_human_text_bundle,
    publish_human_text_bc_view,
    publish_human_text_source,
    publish_verified_human_text_bundle,
)
from stpd.fullrun.text_menu_inputs import IDENTITY, project_text_menu_snapshot
from stpd.fullrun.token_comparison import compare_token_results
from stpd.fullrun.token_inputs import fit_scratch, load_token_inputs, publish_token_inputs
from stpd.policy.token_decision import TokenDecisionScorer, export_token_model
from stpd.workers.token_ranking import TokenConfig
from stpd.workers.token_worker import execute_tokens, prepare_token_run


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


def test_same_local_run_id_does_not_claim_cross_session_native_independence() -> None:
    rows = (observation("session-a", "different-a"),
            observation("session-b", "different-b"))
    assert rows[0]["run_id"] == rows[1]["run_id"] == "run-1"
    assert (project_text_menu_snapshot(rows[0]["snapshot"]).state_text
            != project_text_menu_snapshot(rows[1]["snapshot"]).state_text)

    samples, report = _project(rows)

    assert {sample.split for sample in samples} == {"train", "dev"}
    assert len({sample.run_id for sample in samples}) == 2  # scoped by session
    assert report["split_basis"] == (
        "recording_session_group_with_duplicate_visible_current_input_collapse")
    assert report["native_run_independence"] == "unknown_across_sessions"
    assert [row["run_id"] for row in report["rows"]] == ["run-1", "run-1"]

    legacy_samples, legacy_report = _project(rows, schema=LEGACY_VIEW_SCHEMA)
    assert legacy_samples == samples
    assert legacy_report["schema"] == LEGACY_VIEW_SCHEMA
    assert legacy_report["split_basis"] == (
        "whole_session_run_and_duplicate_visible_current_input")
    assert "native_run_independence" not in legacy_report


@pytest.mark.parametrize("schema", [LEGACY_VIEW_SCHEMA, "stpd/human-text-input-bc-view-v2"])
def test_human_view_dispatch_keeps_v1_and_v2_read_paths(schema, monkeypatch) -> None:
    manifest = SimpleNamespace(
        kind="model_view", parameters=SimpleNamespace(value=lambda: {"schema": schema}))
    target = SimpleNamespace(get_manifest=lambda identity: manifest)
    expected = (manifest, ())
    monkeypatch.setattr(
        "stpd.fullrun.text_menu_human_import.load_human_text_bc_view",
        lambda store, value: expected)

    assert _load_model_view(target, "view-id") == expected


def test_v1_and_v2_payloads_revalidate_for_token_admission(
    tmp_path: Path, monkeypatch,
) -> None:
    rows = (observation("session-a", "archive-a"),
            observation("session-b", "archive-b"))
    target = store(tmp_path)
    source = Manifest("dataset", PRODUCER, parameters=FrozenObject.of({
        "schema": SOURCE_SCHEMA, "scope": "engineering", "purpose": "bc_input_observation",
        "source_digest": "a" * 64,
    }))
    target.publish(source)
    monkeypatch.setattr(
        "stpd.fullrun.text_menu_human_import.load_human_text_source",
        lambda _store, identity: (source, rows) if identity == source.artifact_id else None)

    view_v2 = publish_human_text_bc_view(target, source.artifact_id, PRODUCER)
    assert view_v2.parameters.value()["schema"] == VIEW_SCHEMA
    _, samples_v2 = load_model_view(target, view_v2.artifact_id)

    samples_v1, lineage_v1 = _project(rows, schema=LEGACY_VIEW_SCHEMA)
    sample_payload = target.put_bytes(
        "samples", b"".join(json_bytes(sample.to_dict()) for sample in samples_v1),
        "application/x-ndjson")
    lineage_payload = target.put_bytes("lineage", json_bytes(lineage_v1), "application/json")
    view_v1 = Manifest(
        "model_view", PRODUCER, (Parent("dataset", source.artifact_id),),
        (sample_payload, lineage_payload), FrozenObject.of({
            "schema": LEGACY_VIEW_SCHEMA, "serializer": lineage_v1["serializer"],
            "scope": "engineering", "samples": len(samples_v1),
            "source_digest": "a" * 64, "label_boundary": lineage_v1["label_boundary"],
        }))
    target.publish(view_v1)
    _, samples_loaded_v1 = load_model_view(target, view_v1.artifact_id)
    assert samples_loaded_v1 == samples_v1 == samples_v2

    forged_lineage = decode_json(b"".join(target.read_payload(view_v2.payload("lineage"))))
    forged_lineage["native_run_independence"] = "proven_independent"
    forged_payload = target.put_bytes("lineage", json_bytes(forged_lineage), "application/json")
    forged_view = Manifest(
        "model_view", PRODUCER, view_v2.parents,
        (view_v2.payload("samples"), forged_payload), view_v2.parameters,
    )
    target.publish(forged_view)
    with pytest.raises(BoundaryError, match="view_projection_mismatch"):
        load_model_view(target, forged_view.artifact_id)

    from stpd.fullrun.token_inputs import _source as token_input_source

    for view in (view_v1, view_v2):
        assert token_input_source(target, view.artifact_id) == samples_v2
    with pytest.raises(BoundaryError, match="view_projection_mismatch"):
        token_input_source(target, forged_view.artifact_id)


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


def _declared_bundle(
    tmp_path: Path, mechanism: str, verb: str, *, session_id: str | None = None,
    page_name: str | None = None,
) -> tuple[Path, dict, Path]:
    bundle = bundle3(tmp_path / "fixture", session_id=session_id)
    raw = bundle / "raw"
    recording = load(raw / "recording-manifest.json")
    recording.update(text_input_schema_version=1, close_schema_version=1)
    write(raw / "recording-manifest.json", recording)
    current = snapshot(page_name or session_id or "archive")
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
        "sequence": 1, "record_id": f"text-{session_id or '1'}",
        "session_id": recording["session_id"],
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
    return bundle, row, stream


@pytest.mark.parametrize(("mechanism", "verb"), [
    ("begin_card_play_exact_factory_return", "begin_card_play"),
    ("controller_confirmed_input_signal", "confirm_card"),
    ("controller_canceled_input_signal", "cancel_card_play"),
    ("controller_target_finish_input", "confirm_target"),
    ("controller_target_canceled_input", "cancel_card_play"),
])
def test_declared_bundle_archive_is_reverified_when_source_loads(tmp_path, mechanism, verb) -> None:
    bundle, row, stream = _declared_bundle(tmp_path, mechanism, verb)
    target = store(tmp_path / "artifacts")
    evidence = publish_verified_human_text_bundle(target, bundle, PRODUCER)
    stream.write_bytes(b"corrupted after archive\n")
    _, verified, rows = load_verified_human_text_bundle(target, evidence.artifact_id)
    assert verified.text_input_schema_version == 1
    assert rows[0]["disposition"] == "accepted_input"
    source = publish_human_text_source(target, (evidence.artifact_id,), PRODUCER)
    _, loaded = load_human_text_source(target, source.artifact_id)
    assert loaded == rows
    observed = load_observed_input_view(target, source.artifact_id)
    assert observed.stream_scope == "partial_human_input_stream"
    assert observed.trajectory_complete is False
    assert len(observed.inputs) == 1
    assert observed.inputs[0].choice_mask is True
    assert observed.inputs[0].delivery_status == "human_witness_only"
    assert observed.inputs[0].delivery_mask is False
    assert observed.inputs[0].successor_snapshot is None
    assert observed.inputs[0].causal_successor_mask is False
    samples, _ = _project((*loaded, observation("other-session", "other-page")))
    archived = next(sample for sample in samples if sample.chosen_index == 1
                    and verb in sample.action_texts[1])
    assert archived.action_keys[archived.chosen_index] == row["chosen_action"]["action_id"]
    assert mechanism not in archived.state_text
    assert all(mechanism not in action for action in archived.action_texts)
    with pytest.raises(BoundaryError, match="independent_groups_required"):
        _project(loaded)


def test_typed_human_text_view_trains_tiny_b_and_exports_standalone_text_menu(
    tmp_path: Path,
) -> None:
    original_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        target = store(tmp_path / "artifacts")
        evidence_ids = []
        observed = []
        for name, page in (("session-a", "same-page"), ("session-b", "same-page"),
                           ("session-z", "distinct-page")):
            bundle, row, _ = _declared_bundle(
                tmp_path / name, "begin_card_play_exact_factory_return",
                "begin_card_play", session_id=name, page_name=page,
            )
            evidence = publish_verified_human_text_bundle(target, bundle, PRODUCER)
            evidence_ids.append(evidence.artifact_id)
            observed.append(row)
        source = publish_human_text_source(target, tuple(evidence_ids), PRODUCER)
        view = publish_human_text_bc_view(target, source.artifact_id, PRODUCER)
        assert view.parameters.value()["serializer"] == IDENTITY
        tokens = publish_token_inputs(target, view.artifact_id, "s", PRODUCER, max_tokens=4096)
        inputs = load_token_inputs(target, tokens.artifact_id)
        assert {sample.split for sample in inputs.samples} == {"train", "dev"}
        assert len(inputs.samples) == 3
        dev = [sample for sample in inputs.samples if sample.split == "dev"]
        assert len(dev) == 2 and len({sample.run_id for sample in dev}) == 2
        assert dev[0].state_text == dev[1].state_text
        assert {row["run_id"] for row in observed} == {"run-0001"}
        assert b"".join(target.read_payload(tokens.payload("tokenizer"))) == fit_scratch(
            inputs.samples)
        config = TokenConfig.text_menu_small_b(
            steps=2, width=16, layers=1, heads=2, feedforward=32, max_tokens=4096)
        run = prepare_token_run(target, inputs, config, PRODUCER)
        result = execute_tokens(
            target, ObjectStoreRunReporter(target, target.blobs), run.artifact_id, PRODUCER)
        assert result.state == "completed" and result.result_id is not None
        completed = target.get_manifest(result.result_id)
        model_id = completed.parent("model")
        report = target.get_manifest(completed.parent("offline_evaluation"))
        metrics = decode_json(b"".join(target.read_payload(report.payload("metrics"))))
        assert len(metrics["rows"]) == 2
        for item in (metrics["summary"], *metrics["baselines"].values()):
            assert item["overall"]["count"] == 2
            assert item["bootstrap"]["status"] == "unknown"
            assert item["bootstrap"]["reason"] == (
                "native_run_independence_unknown_across_sessions")
            assert item["bootstrap"]["reported_run_groups"] == 2
            assert "percentile_95" not in item["bootstrap"]
        assert target.get_manifest(model_id).parameters.value()["serializer"] == IDENTITY
        export = tmp_path / "export"
        export_token_model(target, model_id, export)
        scores = TokenDecisionScorer(export).score_snapshot(observed[0]["snapshot"])
        assert tuple(scores) == tuple(action["action_id"] for action in
                                      observed[0]["snapshot"]["menu_actions"]["actions"])
        assert all(isinstance(score, float) for score in scores.values())

        other_config = replace(config, seed=config.seed + 1, steps=1)
        other_run = prepare_token_run(target, inputs, other_config, PRODUCER)
        other_result = execute_tokens(
            target, ObjectStoreRunReporter(target, target.blobs),
            other_run.artifact_id, PRODUCER)
        assert other_result.result_id is not None
        comparison = compare_token_results(
            target, [result.result_id, other_result.result_id])
        for item in comparison["models"]:
            assert "independent_runs" not in item
            assert "run_weighted" not in item
            assert item["reported_run_groups"] == 2
            assert item["reported_run_group_weighted"] == item["decision_weighted"]
            assert item["native_run_independence"] == "unknown_across_sessions"

        # One typed session cannot provide the independent train/dev split.
        one = publish_human_text_source(target, (evidence_ids[0],), PRODUCER)
        with pytest.raises(BoundaryError, match="independent_groups_required"):
            publish_human_text_bc_view(target, one.artifact_id, PRODUCER)

        lineage = decode_json(b"".join(target.read_payload(view.payload("lineage"))))
        lineage["native_run_independence"] = "proved"
        forged = Manifest(
            "model_view", PRODUCER, view.parents,
            (view.payload("samples"), target.put_bytes(
                "lineage", json_bytes(lineage), "application/json")), view.parameters)
        target.publish(forged)
        with pytest.raises(BoundaryError, match="view_projection_mismatch"):
            publish_token_inputs(target, forged.artifact_id, "s", PRODUCER)

        held_out = Manifest("dataset", PRODUCER, parameters=FrozenObject.of({
            "schema": SOURCE_SCHEMA, "purpose": "gold"}))
        target.publish(held_out)
        forged_parent = Manifest(
            "model_view", PRODUCER, (Parent("dataset", held_out.artifact_id),),
            view.payloads, view.parameters)
        target.publish(forged_parent)
        with pytest.raises(BoundaryError, match="held_out_data_cannot_train"):
            publish_token_inputs(target, forged_parent.artifact_id, "s", PRODUCER)
    finally:
        torch.set_num_threads(original_threads)
