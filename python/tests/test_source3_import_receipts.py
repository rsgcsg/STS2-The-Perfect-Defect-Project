"""Immutable operational provenance, including explicit partial-import recovery."""

from __future__ import annotations

from dataclasses import replace

import pytest
from source3_product_fixture import DERIVED, GOLDEN, ORIGINAL, settled, setup

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import FrozenObject
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench import local_recording_import
from spireagent.workbench.local_recording_import import (
    SOURCE3_RECEIPT_SCHEMA,
    LocalRecordingImporter,
)
from stpd.fullrun.ordered_source import publish_ordered_source_raw
from stpd.ordered_source_spec import RAW_SCHEMA


def receipts(store):
    return [store.get_manifest(identity) for identity in store.manifest_ids()
            if store.get_manifest(identity).parameters.value().get("schema")
            == SOURCE3_RECEIPT_SCHEMA]


def test_fresh_then_changed_registration_reuse_distinguishes_actual_packer(tmp_path, monkeypatch):
    importer, _, catalog, candidate, store, _, _, tool = setup(tmp_path, monkeypatch)
    importer.start(candidate)
    fresh = settled(importer)
    assert fresh["status"] == "completed", fresh
    raw_id, receipt_id = fresh["artifact_id"], fresh["import_receipt_id"]
    raw_bytes = store.get_manifest(raw_id).to_bytes()
    original_release = tool.release_id
    receipt = store.get_manifest(receipt_id)
    assert receipt.kind == "analysis" and not receipt.payloads
    assert receipt.parents == (Parent("raw", raw_id),) and receipt.producer == DERIVED
    info = receipt.parameters.value()
    assert info["candidate_id"] == candidate and info["raw_artifact_id"] == raw_id
    assert info["tool_invocation"] == fresh["tool_invocation"] == "packed"
    assert info["registered_tool_release_id"] == info["packing_tool_release_id"] == (
        original_release)
    assert info["original_packing_receipt_id"] is None
    assert info["human_origin_verified"] is False

    # The source revision stays identical across registrations. It cannot prove
    # which release performed the original packaging operation.
    tool.release_id = "9" * 64
    changed_candidate = catalog.read()["candidates"][0]["candidate_id"]
    assert changed_candidate != candidate
    reused = importer.start(changed_candidate)
    assert reused["status"] == "completed", reused
    assert reused["artifact_id"] == raw_id and len(tool.calls) == 1
    assert reused["tool_invocation"] == "reused_no_tool_invocation"
    assert reused["registered_tool_release_id"] == tool.release_id
    assert reused["packing_tool_release_id"] == reused["tool_release_id"] == original_release
    reused_receipt = store.get_manifest(reused["import_receipt_id"])
    assert reused_receipt.parameters.value()["original_packing_receipt_id"] == receipt_id
    assert reused_receipt.parameters.value()["candidate_id"] == changed_candidate
    assert store.get_manifest(raw_id).to_bytes() == raw_bytes
    frozen = store.manifest_ids()
    reopened = LocalRecordingImporter(importer.config, catalog)
    assert reopened.start(changed_candidate)["import_receipt_id"] == reused_receipt.artifact_id
    assert store.manifest_ids() == frozen and len(tool.calls) == 1


def test_old_raw_without_operational_receipt_reports_original_packer_unknown(tmp_path, monkeypatch):
    importer, _, _, candidate, store, _, _, tool = setup(tmp_path, monkeypatch)
    raw = publish_ordered_source_raw(store, GOLDEN, ORIGINAL)
    before = raw.to_bytes()
    result = importer.start(candidate)
    assert result["status"] == "completed", result
    assert result["artifact_id"] == raw.artifact_id and not tool.calls
    assert result["tool_invocation"] == "reused_no_tool_invocation"
    assert result["registered_tool_release_id"] == tool.release_id
    assert result["packing_tool_release_id"] is None and result["tool_release_id"] is None
    receipt = store.get_manifest(result["import_receipt_id"])
    assert receipt.parameters.value()["original_packing_receipt_id"] is None
    assert receipt.parents == (Parent("raw", raw.artifact_id),)
    assert store.get_manifest(raw.artifact_id).to_bytes() == before
    assert len(receipts(store)) == 1


@pytest.mark.parametrize("stage", ["before_receipt", "after_receipt", "after_raw"])
def test_reopen_partial_publication_recovers_saved_packing_fact_and_producer_without_repack(
    tmp_path, monkeypatch, stage,
):
    importer, _, catalog, candidate, store, _, _, tool = setup(tmp_path, monkeypatch)
    publish = ManifestArtifactStore.publish
    def interrupted(self, manifest):
        schema = manifest.parameters.value().get("schema")
        if schema == SOURCE3_RECEIPT_SCHEMA and stage == "before_receipt":
            raise OSError("synthetic receipt write failed before publication")
        identity = publish(self, manifest)
        if ((schema == SOURCE3_RECEIPT_SCHEMA and stage == "after_receipt")
                or (schema == RAW_SCHEMA and stage == "after_raw")):
            raise OSError("synthetic response lost after immutable publication")
        return identity
    monkeypatch.setattr(ManifestArtifactStore, "publish", interrupted)
    importer.start(candidate)
    failed = settled(importer)
    assert failed["status"] == "published_index_unavailable", failed
    raw_id = failed["artifact_id"]
    assert len(tool.calls) == 1
    assert importer.operation["_receipt_producer"] == DERIVED.to_dict()
    assert importer.operation["_packing_fact"]["tool_release_id"] == tool.release_id
    assert importer.operation["_packing_fact"]["closed_metadata"]["bundle_content_id"] == (
        store.get_manifest(raw_id).parameters.value()["bundle_content_id"])
    original_release = tool.release_id
    tool.release_id = "8" * 64
    changed_candidate = catalog.read()["candidates"][0]["candidate_id"]
    monkeypatch.setattr(ManifestArtifactStore, "publish", publish)
    monkeypatch.setattr(local_recording_import, "source_identity", lambda *_args:
                        pytest.fail("partial recovery must retain its captured producer"))
    reopened = LocalRecordingImporter(importer.config, catalog)
    recovered = reopened.start(changed_candidate)
    assert recovered["status"] == "completed", recovered
    assert recovered["artifact_id"] == raw_id and len(tool.calls) == 1
    assert recovered["packing_tool_release_id"] == original_release
    assert recovered["registered_tool_release_id"] == tool.release_id
    assert recovered["tool_invocation"] == "reused_no_tool_invocation"
    original = [item for item in receipts(store)
                if item.parameters.value()["tool_invocation"] == "packed"]
    assert len(original) == 1 and original[0].producer == DERIVED
    assert original[0].parameters.value()["candidate_id"] == candidate
    assert original[0].parameters.value()["registered_tool_release_id"] == original_release
    assert store.get_manifest(recovered["import_receipt_id"]).parameters.value()[
        "original_packing_receipt_id"] == original[0].artifact_id


def test_multiple_original_packing_receipts_fail_closed_without_new_tool_invocation(
    tmp_path, monkeypatch,
):
    importer, _, _, candidate, store, _, _, tool = setup(tmp_path, monkeypatch)
    importer.start(candidate)
    fresh = settled(importer)
    receipt = store.get_manifest(fresh["import_receipt_id"])
    other = Producer("fixture://ambiguous-operational-producer", "1" * 40, "2" * 64)
    store.publish(replace(receipt, producer=other))
    before = store.manifest_ids()
    failed = importer.start(candidate)
    assert failed["status"] == "published_index_unavailable", failed
    assert failed["error_code"] == "source3_original_packing_receipt_ambiguous"
    assert store.manifest_ids() == before and len(tool.calls) == 1
    assert "import_receipt_id" not in failed


def test_reused_raw_receipt_failure_is_explicit_and_recoverable_without_packing(
    tmp_path, monkeypatch,
):
    importer, _, catalog, candidate, store, _, _, tool = setup(tmp_path, monkeypatch)
    raw = publish_ordered_source_raw(store, GOLDEN, ORIGINAL)
    publish = ManifestArtifactStore.publish
    def refuse_receipt(self, manifest):
        if manifest.parameters.value().get("schema") == SOURCE3_RECEIPT_SCHEMA:
            raise OSError("synthetic reuse receipt publication failed")
        return publish(self, manifest)
    monkeypatch.setattr(ManifestArtifactStore, "publish", refuse_receipt)
    failed = importer.start(candidate)
    assert failed["status"] == "published_index_unavailable" and not tool.calls
    assert failed["artifact_id"] == raw.artifact_id
    assert not receipts(store)
    monkeypatch.setattr(ManifestArtifactStore, "publish", publish)
    monkeypatch.setattr(local_recording_import, "source_identity", lambda *_args:
                        pytest.fail("reuse recovery must retain its captured producer"))
    recovered = LocalRecordingImporter(importer.config, catalog).start(candidate)
    assert recovered["status"] == "completed" and not tool.calls
    assert recovered["packing_tool_release_id"] is None


def test_partial_reuse_receipt_cannot_substitute_the_captured_producer(tmp_path, monkeypatch):
    importer, _, catalog, candidate, store, _, _, tool = setup(tmp_path, monkeypatch)
    publish_ordered_source_raw(store, GOLDEN, ORIGINAL)
    publish = ManifestArtifactStore.publish
    other = Producer("fixture://unexpected-receipt-producer", "1" * 40, "2" * 64)
    def mismatched_side_effect(self, manifest):
        if manifest.parameters.value().get("schema") == SOURCE3_RECEIPT_SCHEMA:
            publish(self, replace(manifest, producer=other))
            raise OSError("synthetic mismatched receipt persisted before lost reply")
        return publish(self, manifest)
    monkeypatch.setattr(ManifestArtifactStore, "publish", mismatched_side_effect)
    assert importer.start(candidate)["status"] == "published_index_unavailable"
    monkeypatch.setattr(ManifestArtifactStore, "publish", publish)
    monkeypatch.setattr(local_recording_import, "source_identity", lambda *_args:
                        pytest.fail("reconciliation must retain its captured producer"))
    before = store.manifest_ids()
    failed = LocalRecordingImporter(importer.config, catalog).start(candidate)
    assert failed["status"] == "published_index_unavailable", failed
    assert failed["error_code"] == "source3_receipt_producer_mismatch"
    assert store.manifest_ids() == before and not tool.calls


@pytest.mark.parametrize("stage", ["before_receipt", "after_receipt", "after_raw"])
@pytest.mark.parametrize("conflict", ["producer", "release", "candidate"])
def test_saved_successful_pack_checks_existing_original_receipt_before_partial_reuse(
    tmp_path, monkeypatch, stage, conflict,
):
    importer, _, catalog, candidate, store, _, _, tool = setup(tmp_path, monkeypatch)
    publish = ManifestArtifactStore.publish
    other = Producer("fixture://conflicting-packed-receipt-producer", "1" * 40, "2" * 64)
    def contradicted(receipt):
        if conflict == "producer":
            return replace(receipt, producer=other)
        params = receipt.parameters.value()
        if conflict == "release":
            params["registered_tool_release_id"] = params["packing_tool_release_id"] = "9" * 64
        else:
            params["candidate_id"] = "8" * 64
        return replace(receipt, parameters=FrozenObject.of(params))
    def interrupted(self, manifest):
        schema = manifest.parameters.value().get("schema")
        if schema == SOURCE3_RECEIPT_SCHEMA and stage == "before_receipt":
            raise OSError("synthetic receipt not published")
        if schema == SOURCE3_RECEIPT_SCHEMA and stage == "after_receipt":
            publish(self, contradicted(manifest))
            raise OSError("synthetic contradictory original receipt persisted before lost reply")
        identity = publish(self, manifest)
        if schema == RAW_SCHEMA and stage == "after_raw":
            raise OSError("synthetic raw publication completed before lost reply")
        return identity
    monkeypatch.setattr(ManifestArtifactStore, "publish", interrupted)
    importer.start(candidate)
    failed = settled(importer)
    assert failed["status"] == "published_index_unavailable", failed
    assert importer.operation["_receipt_producer"] == DERIVED.to_dict()
    raw_id = failed["artifact_id"]
    saved = importer.operation["_packing_fact"]
    if stage != "after_receipt":
        # Model an existing contradictory immutable original receipt at recovery,
        # without rewriting the operation's previously captured successful fact.
        expected = {**saved["closed_metadata"], "raw_artifact_id": raw_id}
        original = Manifest("analysis", DERIVED, parents=(Parent("raw", raw_id),),
                            parameters=FrozenObject.of(expected))
        publish(store, contradicted(original))
    monkeypatch.setattr(ManifestArtifactStore, "publish", publish)
    monkeypatch.setattr(local_recording_import, "source_identity", lambda *_args:
                        pytest.fail("recovery must retain saved successful-pack Producer"))
    # The current registered prerequisite can legitimately change. The saved
    # successful pack still binds the original release and original candidate.
    tool.release_id = "7" * 64
    new_candidate = catalog.read()["candidates"][0]["candidate_id"]
    assert new_candidate != candidate
    before = store.manifest_ids()
    recovered = LocalRecordingImporter(importer.config, catalog).start(new_candidate)
    assert recovered["status"] == "published_index_unavailable", recovered
    assert recovered["error_code"] == (
        "source3_receipt_producer_mismatch" if conflict == "producer"
        else "source3_packing_fact_mismatch")
    assert store.manifest_ids() == before and len(tool.calls) == 1
    assert "import_receipt_id" not in recovered


def test_unrelated_historical_original_receipt_does_not_require_todays_producer(
    tmp_path, monkeypatch,
):
    importer, _, catalog, candidate, store, _, _, tool = setup(tmp_path, monkeypatch)
    importer.start(candidate)
    historical = settled(importer)
    assert historical["status"] == "completed", historical
    old_receipt = store.get_manifest(historical["import_receipt_id"])
    before = old_receipt.to_bytes()
    # The single operation journal may have moved on to a different import.
    # Historical immutable receipts remain reusable without that saved pack fact.
    importer.operation = {"schema": local_recording_import.SCHEMA, "status": "idle"}
    importer._save()
    today = Producer("fixture://later-reuse-application", "3" * 40, "4" * 64)
    monkeypatch.setattr(local_recording_import, "source_identity", lambda *_args: today)
    original_release = tool.release_id
    tool.release_id = "6" * 64
    current_candidate = catalog.read()["candidates"][0]["candidate_id"]
    reused = LocalRecordingImporter(importer.config, catalog).start(current_candidate)
    assert reused["status"] == "completed", reused
    assert reused["artifact_id"] == historical["artifact_id"] and len(tool.calls) == 1
    assert reused["tool_invocation"] == "reused_no_tool_invocation"
    assert reused["packing_tool_release_id"] == original_release
    assert reused["registered_tool_release_id"] == tool.release_id
    current = store.get_manifest(reused["import_receipt_id"])
    assert current.producer == today and old_receipt.producer == DERIVED
    assert current.parameters.value()["original_packing_receipt_id"] == old_receipt.artifact_id
    assert store.get_manifest(old_receipt.artifact_id).to_bytes() == before


@pytest.mark.parametrize("field", ["bundle_content_id", "registered_tool_release_id"])
def test_related_saved_packing_metadata_mismatch_cannot_fall_back_to_unknown_packer(
    tmp_path, monkeypatch, field,
):
    importer, _, catalog, candidate, store, _, _, tool = setup(tmp_path, monkeypatch)
    publish = ManifestArtifactStore.publish
    def after_raw(self, manifest):
        identity = publish(self, manifest)
        if manifest.parameters.value().get("schema") == RAW_SCHEMA:
            raise OSError("synthetic raw published before receipt")
        return identity
    monkeypatch.setattr(ManifestArtifactStore, "publish", after_raw)
    importer.start(candidate)
    failed = settled(importer)
    assert failed["status"] == "published_index_unavailable", failed
    assert not receipts(store)
    importer.operation["_packing_fact"]["closed_metadata"][field] = "5" * 64
    importer._save()
    monkeypatch.setattr(ManifestArtifactStore, "publish", publish)
    monkeypatch.setattr(local_recording_import, "source_identity", lambda *_args:
                        pytest.fail("related packing fact must retain captured Producer"))
    before = store.manifest_ids()
    refused = LocalRecordingImporter(importer.config, catalog).start(candidate)
    assert refused["status"] == "published_index_unavailable", refused
    assert refused["error_code"] == "source3_packing_fact_mismatch"
    assert "packing_tool_release_id" not in refused and "import_receipt_id" not in refused
    assert store.manifest_ids() == before and len(tool.calls) == 1
