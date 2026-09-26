"""Synthetic public fixtures exercise Human input projection boundaries."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

import pytest
from test_artifact_store_v1 import PRODUCER, store
from test_text_menu_data import snapshot

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.text_menu_human_import import (
    _project,
    load_human_text_source,
    load_verified_human_text_bundle,
    publish_human_text_source,
    publish_verified_human_text_bundle,
)

sys.path.insert(0, os.environ.get(
    "STPD_EVIDENCE_TEST_ROOT",
    str(Path(__file__).parents[2] / "components/evidence"),
))


def observation(session: str, name: str, disposition: str = "accepted_input") -> dict:
    current = snapshot(name)
    current["menu_actions"]["actions"][1]["verb"] = "begin_card_play"
    chosen = copy.deepcopy(current["menu_actions"]["actions"][1])
    return {
        "schema_version": 1, "schema": "sts2.human-annotator/human-text-input-1",
        "sequence": 1, "record_id": f"{session}-{name}", "session_id": session,
        "run_id": "run-1", "snapshot": current, "chosen_action": chosen,
        "mapping_status": "exact_unique", "match_count": 1,
        "mapping_basis": "text_menu_native_reference_equality",
        "native_mechanism": "begin_card_play_exact_factory_return",
        "external_controller_active": False, "disposition": disposition,
        "reason_code": None if disposition == "accepted_input" else "rejected",
    }


def test_exact_begin_is_only_label_and_negative_row_remains_accounted() -> None:
    rejected = observation("session-a", "a-rejected", "rejected_or_cancelled")
    rejected["chosen_action"] = None
    rows = (observation("session-a", "a"), rejected,
            observation("session-b", "b"))
    samples, report = _project(rows)
    assert len(samples) == 2
    assert {sample.split for sample in samples} == {"train", "dev"}
    assert all(sample.action_keys[sample.chosen_index] == "opaque-play" for sample in samples)
    assert [item["status"] for item in report["rows"]] == ["included", "excluded", "included"]
    assert report["native_successor_supervision"] is False
    assert report["human_origin"] == "explicit_owner_attestation_not_machine_verifiable"


def test_duplicate_visible_input_collapses_sessions_and_blocks_leakage() -> None:
    rows = (observation("session-a", "same"), observation("session-b", "same"))
    with pytest.raises(BoundaryError, match="independent_groups_required"):
        _project(rows)


def test_forged_choice_cannot_become_human_label() -> None:
    rows = (observation("session-a", "a"), observation("session-b", "b"))
    rows[0]["chosen_action"]["label"] = "Unwitnessed"
    with pytest.raises(BoundaryError, match="chosen_catalog_binding_mismatch"):
        _project(rows)


def test_source_requires_verified_archived_evidence(tmp_path) -> None:
    target = store(tmp_path)
    with pytest.raises((BoundaryError, KeyError)):
        publish_human_text_source(target, (), PRODUCER)
    with pytest.raises((BoundaryError, KeyError)):
        publish_human_text_source(target, ("unverified-id",), PRODUCER)
    with pytest.raises((BoundaryError, KeyError)):
        load_human_text_source(target, "unverified-id")


def test_declared_bundle_archive_is_reverified_when_source_loads(tmp_path) -> None:
    from tests.test_human_session_bundle_v2 import canonical, sha_bytes
    from tests.test_human_session_bundle_v3 import HumanSessionBundleV3Tests

    helper = HumanSessionBundleV3Tests()
    helper.setUp()
    try:
        bundle = helper._bundle()
        row = helper._text_row(bundle)
        row["snapshot"]["interaction"]["content"] = {}
        row["snapshot"]["referents"][0].update(
            kind="entity", role="hand_card", label="Strike", properties={})
        row["snapshot_sha256"] = sha_bytes(canonical(row["snapshot"]).encode())
        helper._declare_text(bundle, [row])
        target = store(tmp_path / "artifacts")
        evidence = publish_verified_human_text_bundle(target, bundle, PRODUCER)
        (bundle / "raw/human-text-inputs.jsonl").write_bytes(b"corrupted after archive\n")
        _, verified, rows = load_verified_human_text_bundle(target, evidence.artifact_id)
        assert verified.text_input_schema_version == 1
        assert rows[0]["disposition"] == "accepted_input"
        source = publish_human_text_source(target, (evidence.artifact_id,), PRODUCER)
        _, loaded = load_human_text_source(target, source.artifact_id)
        assert loaded == rows
        # One session is transportable, but cannot furnish independent BC splits.
        with pytest.raises(BoundaryError, match="independent_groups_required"):
            _project(loaded)
    finally:
        helper.tearDown()
