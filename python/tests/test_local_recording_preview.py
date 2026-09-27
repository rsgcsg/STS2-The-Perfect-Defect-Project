"""Synthetic archives pass the installed typed Evidence verifier before preview."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from platform_bundle3_fixture import bundle3, load, seal, write
from sts2_platform_evidence import DirectoryTransferManifest, verify_human_session_bundle
from test_text_menu_data import snapshot

from spireagent.artifact_contracts import Manifest, Payload, Producer
from spireagent.hub.uploads import MAX_ARCHIVE
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.local_recording_import import _archive_verified_bundle
from spireagent.workbench.local_recording_preview import LocalRecordingPreview, preview_artifact
from spireagent.workbench.local_workspace import LocalWorkspace

PRODUCER = Producer("local/synthetic-fixture", "a" * 40, "b" * 64)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _labels(bundle: Path) -> None:
    raw = bundle / "raw"
    recording = load(raw / "recording-manifest.json")
    recording.update(text_input_schema_version=1, close_schema_version=1)
    write(raw / "recording-manifest.json", recording)
    current = snapshot("preview")
    current["session"] = {"runtime_instance_id": "runtime-1",
                          "environment_fingerprint": "environment-1"}
    current["interaction"]["content_schema"] = "combat_turn-1"
    current["interaction"]["content"] = {"surface": {"kind": "combat_turn"}, "context": {}}
    current["information_policy"]["scope"] = "current_page"
    current["menu_actions"]["ordering_semantics"] = "native_order_with_fixed_information_groups"
    current["menu_actions"]["actions"][1]["verb"] = "begin_card_play"
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
        "native_mechanism": "begin_card_play_exact_factory_return",
        "disposition": "accepted_input", "external_controller_active": False,
    }
    rejected = {**row, "sequence": 2, "record_id": "text-2",
                "disposition": "rejected_or_cancelled", "reason_code": "cancelled"}
    path = raw / "human-text-inputs.jsonl"
    path.write_bytes(json_bytes(row) + json_bytes(rejected))
    write(raw / "session-close-receipt.json", {
        "schema": "sts2.human-annotator/session-close-1",
        "session_id": recording["session_id"], "timeline_id": recording["timeline_id"],
        "status": "closed", "closed_at": "2026-09-26T00:00:02Z",
        "human_text_input_count": 2, "human_text_inputs_sha256": _sha(path),
    })
    seal(bundle)
    verified = verify_human_session_bundle(bundle)
    assert verified.passed, verified.findings


def _fixture(
    tmp_path: Path, *, canonical: bool = False,
) -> tuple[LocalWorkspace, str, ManifestArtifactStore]:
    bundle = bundle3(tmp_path / "fixture", runs=1)
    if not canonical:
        (bundle / "raw/semantic-boundary-trace.jsonl").write_bytes(b"")
        (bundle / "raw/canonical-transitions.jsonl").write_bytes(b"")
        for path in (bundle / "session-bundle-manifest.json", bundle / "audit/audit-report.json"):
            value = load(path)
            value["canonical_count"] = 0
            write(path, value)
        seal(bundle)
    _labels(bundle)
    verified = verify_human_session_bundle(bundle).require_value()
    transfer = DirectoryTransferManifest.from_directory(
        bundle, content_id=verified.bundle_content_id, artifact_type="human-session-bundle",
    )
    archive = tmp_path / "archive.tar.gz"
    _archive_verified_bundle(bundle, archive, transfer)
    store_dir = tmp_path / "store"
    store = ManifestArtifactStore(LocalBlobStore(store_dir))
    registry_path = tmp_path / "registry.sqlite"
    SQLiteRegistry(registry_path)
    archive_payload = store.put_bytes("archive", archive.read_bytes(), "application/gzip")
    transfer_payload = store.put_bytes("transfer", json_bytes(transfer.to_dict()))
    params = {
        "schema": "stpd/local-verified-bundle-v1", "candidate_id": "a" * 64,
        "session_id": verified.session_id, "timeline_id": verified.timeline_id,
        "manifest_sha256": _sha(bundle / "raw/recording-manifest.json"),
        "close_sha256": _sha(bundle / "raw/session-close-receipt.json"),
        "content_id": transfer.content_id, "transfer_manifest_sha256": transfer.manifest_sha256,
        "tool_release_id": "d" * 64, "worker_id": verified.worker_id,
        "campaign_id": verified.campaign_id, "human_origin_attested": True,
        "disposition": "locally_verified", "research_admission": "not_evaluated",
        "hub_receipt": None,
    }
    manifest = Manifest("evidence", PRODUCER, payloads=(archive_payload, transfer_payload),
                        parameters=FrozenObject.of(params))
    artifact_id = store.publish(manifest)
    workspace = LocalWorkspace(
        SQLiteRegistry(registry_path, readonly=True),
        ManifestArtifactStore(LocalBlobStore(store_dir, create=False, readonly=True)),
    )
    return workspace, artifact_id, store


def test_zero_canonical_keeps_accepted_label_distinct_without_writes(tmp_path: Path) -> None:
    workspace, artifact_id, store = _fixture(tmp_path)
    before = (store.manifest_ids(), tuple(store.blobs.keys("objects/")),
              tuple(store.blobs.keys("payload-indexes/")))
    result = preview_artifact(workspace, artifact_id)
    assert result["available_types"] == ["human_input_label"]
    assert result["human_input_labels"] == 1
    assert result["human_input_total"] == 2
    assert result["human_input_exclusions"] == {"rejected_or_cancelled:cancelled": 1}
    assert result["canonical_decisions"] == 0
    assert result["independent_run_qualification"] == "insufficient_canonical_decisions"
    assert result["research_admission"] == "not_evaluated"
    assert (store.manifest_ids(), tuple(store.blobs.keys("objects/")),
            tuple(store.blobs.keys("payload-indexes/"))) == before
    assert LocalRecordingPreview(lambda: workspace).status()["status"] == "idle"


def test_canonical_record_is_separate_and_run_independence_unknown(tmp_path: Path) -> None:
    workspace, artifact_id, _ = _fixture(tmp_path, canonical=True)
    result = preview_artifact(workspace, artifact_id)
    assert result["available_types"] == ["human_input_label", "canonical_decision"]
    assert result["canonical_decisions"] == 2
    assert result["independent_run_qualification"] == "unknown"


@pytest.mark.parametrize(("change", "code"), [
    ({"schema": "stpd/received-bundle-v1"}, "not_local_verified_bundle"),
    ({"manifest_sha256": "0" * 64}, "recording_source_hash_mismatch"),
    ({"close_sha256": "0" * 64}, "recording_source_hash_mismatch"),
    ({"transfer_manifest_sha256": "0" * 64}, "transfer_identity_mismatch"),
])
def test_metadata_tamper_is_rejected(tmp_path: Path, change: dict, code: str) -> None:
    workspace, artifact_id, store = _fixture(tmp_path)
    original = store.get_manifest(artifact_id)
    changed = Manifest("evidence", PRODUCER, payloads=original.payloads,
                       parameters=FrozenObject.of({**original.parameters.value(), **change}))
    tampered_id = store.publish(changed)
    with pytest.raises(BoundaryError, match=code):
        preview_artifact(workspace, tampered_id)


def test_transfer_and_archive_payload_tamper_are_rejected(tmp_path: Path) -> None:
    workspace, artifact_id, store = _fixture(tmp_path)
    original = store.get_manifest(artifact_id)
    for role, data, media in (("transfer", b"{}", "application/json"),
                              ("archive", b"not-a-gzip", "application/gzip")):
        replacement = store.put_bytes(role, data, media)
        changed = Manifest("evidence", PRODUCER,
                           payloads=tuple(replacement if item.role == role else item
                                          for item in original.payloads),
                           parameters=original.parameters)
        with pytest.raises((BoundaryError, OSError, ValueError)):
            preview_artifact(workspace, store.publish(changed))


def test_preview_uses_only_selected_store_and_never_auto_restarts(tmp_path: Path) -> None:
    workspace, artifact_id, _ = _fixture(tmp_path)
    other_dir = tmp_path / "other-store"
    ManifestArtifactStore(LocalBlobStore(other_dir))
    other_registry = tmp_path / "other-registry.sqlite"
    SQLiteRegistry(other_registry)
    other = LocalWorkspace(
        SQLiteRegistry(other_registry, readonly=True),
        ManifestArtifactStore(LocalBlobStore(other_dir, create=False, readonly=True)),
    )
    preview = LocalRecordingPreview(lambda: other)
    assert preview.status()["status"] == "idle"
    assert preview.start(artifact_id)["status"] in {"pending", "failed"}
    assert preview.thread is not None
    preview.thread.join(timeout=5)
    assert preview.status()["status"] == "failed"
    assert not other.store.manifest_ids()
    selected = LocalRecordingPreview(lambda: workspace)
    assert selected.status()["status"] == "idle"
    selected.start(artifact_id)
    assert selected.thread is not None
    selected.thread.join(timeout=5)
    assert selected.status()["status"] == "completed"
    assert selected.status()["artifact_id"] == artifact_id
    assert LocalRecordingPreview(lambda: workspace).status()["status"] == "idle"


def test_oversized_archive_is_rejected_before_payload_read(tmp_path: Path) -> None:
    workspace, artifact_id, store = _fixture(tmp_path)
    original = store.get_manifest(artifact_id)
    archive = original.payload("archive")
    oversized = Manifest(
        "evidence", PRODUCER,
        payloads=(Payload("archive", archive.sha256, MAX_ARCHIVE + 1, archive.media_type),
                  original.payload("transfer")),
        parameters=original.parameters,
    )

    class ManifestOnlyStore:
        def get_manifest(self, identity: str) -> Manifest:
            assert identity == oversized.artifact_id
            return oversized

        def read_payload(self, _payload: Payload) -> None:
            pytest.fail("size gate must run before payload read")

    preview_workspace = LocalWorkspace(workspace.registry, ManifestOnlyStore())  # type: ignore[arg-type]
    with pytest.raises(BoundaryError, match="payload_size_or_type_invalid"):
        preview_artifact(preview_workspace, oversized.artifact_id)
