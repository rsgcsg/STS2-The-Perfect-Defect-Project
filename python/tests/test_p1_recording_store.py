"""Real original-reader and CAS tests; synthetic release does not qualify deployment."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
from pathlib import Path

import pytest
from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.local_recordings import _closed_session
from spireagent.workbench.p1_recording_store import (
    BUNDLE_SCHEMA,
    derive_p1_recording,
    materialize_p1_original,
    publish_p1_original,
)
from sts2_platform_evidence.collection_tool import (
    P1_NATIVE_SUPPORT,
    CollectionFailure,
    CollectionTool,
    digest,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def original(tmp_path):
    # Reuse the production C# store/packer fixture already frozen by its owning JS test.
    text = (ROOT / "tools/test/p1-native-bundle.test.mjs").read_text()
    match = re.search(r"const packedFixture = `([^`]+)`", text)
    assert match is not None
    files = json.loads(gzip.decompress(base64.b64decode(match[1])))
    destination = tmp_path / "original"
    destination.mkdir()
    for name, encoded in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(encoded))
    # Owning fixture is rooted at the original bundle itself.
    candidates = list(destination.rglob("session-bundle-manifest.json"))
    assert len(candidates) == 1
    return candidates[0].parent


@pytest.fixture
def tool(tmp_path):
    directory = tmp_path / "release"
    content = {
        "platform-bom.json": b"{}",
        "sts2-human-annotator.dll": b"not executed in this portable reader test",
        "p1/tools/p1-native-bundle.mjs": (ROOT / "tools/p1-native-bundle.mjs").read_bytes(),
        "p1/tools/p1-native-to-p1.mjs": b"""
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
if (process.argv[1] === fileURLToPath(import.meta.url)) throw Error('CLI executed on import');
export async function convertP1NativeBundle(options) {
 if (options.runId !== 'run-0001' || !options.includeSegments) throw Error('options lost');
 const source=JSON.parse(readFileSync(options.bundle+'/session-bundle-manifest.json'));
 return {segment_output:{schema:'sts2.p1/native-segments-1',
 source_content_id:source.bundle_content_id,
 branch:{root:null},profile:'test',revision:'test',projection_claims:[],whole_run_complete:false,
 source_conversion_complete:false,segments:[{points:[{mask:{N:true,next:false,end:false}}]}],
 gaps:[{reason:'synthetic-gap'}],discarded_choices:[]},blocking_issues:[]};
}
""",
        "p1/components/connector/sdk/typescript/dist/p1Global/index.js": b"export {};",
        "setup/tools/p1-native-recording.mjs": b"throw Error('no gameplay in this fixture');",
    }
    for name, raw in content.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    identity = {
        "worktree": "clean",
        "source_revision": "a" * 40,
        "entrypoint": "sts2-human-annotator.dll",
        "supported_recording_schema": P1_NATIVE_SUPPORT["recording_schema"],
        "p1_native_support": P1_NATIVE_SUPPORT,
        "files": [
            {"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            for path, raw in sorted(content.items())
        ],
    }
    release = digest(identity)
    (directory / "collection-tool.json").write_text(
        json.dumps(
            {
                "schema": "sts2.evidence/collection-tool-1",
                "release_id": release,
                "identity": identity,
            }
        )
    )
    return CollectionTool(directory, release)


def test_pinned_original_reader_and_store_roundtrip_reuse(tmp_path, original, tool):
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    producer = Producer("synthetic/import-test", "a" * 40, "b" * 64)
    before = {
        str(p.relative_to(original)): p.read_bytes() for p in original.rglob("*") if p.is_file()
    }
    raw = publish_p1_original(store, original, tool, producer, scratch=tmp_path / "scratch")
    assert raw.kind == "evidence" and raw.parameters.value()["schema"] == BUNDLE_SCHEMA
    assert raw.parameters.value()["research_admission"] == "not_evaluated"
    assert raw.parameters.value()["human_origin_verified"] is False
    assert (
        publish_p1_original(
            store, original, tool, producer, scratch=tmp_path / "scratch"
        ).artifact_id
        == raw.artifact_id
    )
    assert store.manifest_ids() == (raw.artifact_id,)
    with materialize_p1_original(
        store, raw.artifact_id, scratch=tmp_path / "scratch", tool=tool
    ) as restored:
        assert {
            str(p.relative_to(restored)): p.read_bytes() for p in restored.rglob("*") if p.is_file()
        } == before
    assert list((tmp_path / "scratch").iterdir()) == []
    assert {
        str(p.relative_to(original)): p.read_bytes() for p in original.rglob("*") if p.is_file()
    } == before


def test_original_tampering_and_mutated_fixed_release_are_rejected(tmp_path, original, tool):
    (original / "raw/p1-native-events.jsonl").write_bytes(b"{}\n")
    with pytest.raises(CollectionFailure, match="reader/converter failed"):
        tool.verify_p1_native(original)
    (tool.directory / "p1/tools/p1-native-bundle.mjs").write_bytes(b"export {};")
    with pytest.raises(ValueError, match="bytes differ"):
        tool.verify_p1_native(original)


def test_p1_is_explicitly_catalogued_and_invalid_origin_never_falls_into_legacy(original):
    raw = original / "raw"
    metadata = _closed_session(raw)
    assert metadata is not None and metadata["recording_type"] == "p1_native"
    manifest = raw / "recording-manifest.json"
    data = json.loads(manifest.read_text())
    data["p1_native_origin"] = "agent_protocol"
    manifest.write_text(json.dumps(data))
    assert _closed_session(raw) is None


def test_unknown_projection_rejected_before_materialization(tmp_path, tool):
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    with pytest.raises(BoundaryError, match="unsupported_projection"):
        derive_p1_recording(
            store,
            "f" * 64,
            tool,
            Producer("test", "a" * 40, "b" * 64),
            scratch=tmp_path,
            projection_id="unknown-projection",
        )
    assert not store.manifest_ids()


def test_import_reconciliation_uses_exact_raw_fingerprints(tmp_path, original, tool):
    from spireagent.workbench.local_recording_import import LocalRecordingImporter
    from spireagent.workbench.p1_recordings_cli import _reconcile

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    producer = Producer("test", "a" * 40, "b" * 64)
    operation = {
        "recording_type": "p1_native",
        "action": "import-bundle",
        "content_id": tool.verify_p1_native(original)["manifest"]["bundle_content_id"],
    }
    assert _reconcile(store, operation)["publication"] == "absent"
    raw = publish_p1_original(store, original, tool, producer, scratch=tmp_path / "scratch")
    assert _reconcile(store, operation)["artifact_id"] == raw.artifact_id
    candidate = _closed_session(original / "raw")
    importer = object.__new__(LocalRecordingImporter)
    assert importer._existing_candidate(store, "unused", candidate) == raw.artifact_id
    for field in ("manifest_sha256", "close_sha256", "p1_close_sha256"):
        changed = {**candidate, field: "0" * 64}
        assert importer._existing_candidate(store, "unused", changed) is None


def test_converter_adapter_import_does_not_execute_cli(tmp_path, original, tool):
    summary = tool.derive_p1_native(original, tmp_path / "segments.json", run_id="run-0001")
    assert summary["candidate_N"] == 1
    assert summary["gaps"] == [{"reason": "synthetic-gap"}]
    assert json.loads((tmp_path / "segments.json").read_bytes())["whole_run_complete"] is False


def test_background_derivation_reconciles_published_result_without_reexecution(
    tmp_path,
    original,
    tool,
    monkeypatch,
):
    from spireagent.storage.registry import SQLiteRegistry
    from spireagent.workbench import local_recording_import as importing
    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.local_workspace import LocalWorkspace

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    registry = SQLiteRegistry(tmp_path / "registry.sqlite")
    producer = Producer("synthetic/test", "a" * 40, "b" * 64)
    source = publish_p1_original(store, original, tool, producer, scratch=tmp_path / "scratch")
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    monkeypatch.setattr(importing, "_selected_store", lambda _, **_kw: (store, registry))
    monkeypatch.setattr(
        importing, "current_collection_tool", lambda _: (tool.directory, tool.release_id)
    )
    monkeypatch.setattr(importing, "source_identity", lambda _: producer)
    importer = importing.LocalRecordingImporter(config, None)
    publish = store.publish
    calls = []

    def lost_after_publish(manifest):
        calls.append(manifest.artifact_id)
        publish(manifest)
        raise OSError("synthetic lost publication acknowledgement")

    monkeypatch.setattr(store, "publish", lost_after_publish)
    importer.start_p1_derivation(source.artifact_id, "native-selector-finalized-d-1")
    importer.thread.join(timeout=10)
    assert not importer.thread.is_alive()
    result = importer.status()
    assert result["status"] == "completed" and len(calls) == 1
    assert result["artifact_id"] == calls[0]
    assert store.get_manifest(calls[0]).parent("source") == source.artifact_id
    assert LocalWorkspace(registry, store).inventory(category="recordings")["total"] == 1
    # A readback outage must remain unknown and must fence new publication.
    with monkeypatch.context() as scoped:
        scoped.setattr(store, "manifest_ids", lambda: (_ for _ in ()).throw(OSError("offline")))
        assert importer.reconcile_p1_derivation()["status"] == "publication_unknown"
        with pytest.raises(BoundaryError, match="original_intent_reconciliation_required"):
            importer.start_p1_derivation(source.artifact_id, "native-input-v1")
    assert importer.reconcile_p1_derivation()["status"] == "completed"
    assert len(calls) == 1


def test_unknown_p1_import_retains_original_intent_and_can_reconcile_without_raw(
    tmp_path,
    original,
    tool,
    monkeypatch,
):
    from spireagent.storage.registry import SQLiteRegistry
    from spireagent.workbench import local_recording_import as importing
    from spireagent.workbench.developer import ProjectConfig, combination

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    raw = publish_p1_original(
        store, original, tool, Producer("test", "a" * 40, "b" * 64), scratch=tmp_path / "scratch"
    )
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    importer = importing.LocalRecordingImporter(config, None)
    importer.operation = {
        "schema": importing.SCHEMA,
        "recording_type": "p1_native",
        "action": "import",
        "candidate_id": "a" * 64,
        "status": "publication_unknown",
        "_p1_candidate": _closed_session(original / "raw"),
    }
    importer._save()
    original.rename(tmp_path / "offline-original")
    restarted = importing.LocalRecordingImporter(config, None)
    before = dict(restarted.operation)
    with pytest.raises(BoundaryError, match="original_intent_reconciliation_required"):
        restarted.start("b" * 64, False)
    assert restarted.operation == before
    registry = SQLiteRegistry(tmp_path / "registry.sqlite")
    monkeypatch.setattr(importing, "_selected_store", lambda _, **_kw: (store, registry))
    resolved = restarted.reconcile_p1_import()
    assert resolved["status"] == "completed" and resolved["artifact_id"] == raw.artifact_id
    assert resolved["candidate_id"] == "a" * 64


def test_storage_roundtrip_full_archive_readback_and_reconcile(tmp_path, original, tool):
    from spireagent.workbench.p1_recording_storage import (
        peer_binding,
        reconcile_storage,
        run_storage,
        storage_intent,
    )

    local = ManifestArtifactStore(LocalBlobStore(tmp_path / "local"))
    raw = publish_p1_original(
        local, original, tool, Producer("test", "a" * 40, "b" * 64), scratch=tmp_path / "scratch"
    )
    peer = peer_binding(str(tmp_path / "cold"))
    intent = storage_intent("preserve", raw.artifact_id, peer)
    saved = run_storage(local, intent, scratch=tmp_path / "scratch", reserve_bytes=0)
    assert saved["storage_receipt"]["full_readback"] is True
    assert saved["storage_receipt"]["source_files"] > 1
    assert saved["budget"]["scratch_budget_bytes"] >= raw.payload("archive").size
    assert list((tmp_path / "scratch").iterdir()) == []
    # Cold restore needs only the ID and location, with no raw directory or old local Store.
    empty = ManifestArtifactStore(LocalBlobStore(tmp_path / "restored"))
    restored = run_storage(
        empty,
        storage_intent("restore", raw.artifact_id, peer),
        scratch=tmp_path / "scratch",
        reserve_bytes=0,
    )
    assert restored["storage_receipt"]["archive_sha256"] == raw.payload("archive").sha256
    assert empty.get_manifest(raw.artifact_id) == raw
    assert reconcile_storage(empty, intent, scratch=tmp_path / "scratch")["status"] == "completed"
    # Corruption never becomes a successful stored receipt.
    cold = ManifestArtifactStore(LocalBlobStore(tmp_path / "cold"))
    index = json.loads(cold.blobs.get(f"payload-indexes/v1/{raw.payload('archive').sha256}.json"))
    (cold.blobs.root / "objects/sha256" / index["chunks"][0]["sha256"]).write_bytes(b"bad")
    with pytest.raises(ValueError, match="payload_chunk_integrity_failure"):
        reconcile_storage(local, intent, scratch=tmp_path / "scratch")


def test_storage_space_preflight_and_secret_binding_rejected(tmp_path, original, tool, monkeypatch):
    from collections import namedtuple

    from spireagent.workbench import p1_recording_storage as storage

    local = ManifestArtifactStore(LocalBlobStore(tmp_path / "local"))
    raw = publish_p1_original(
        local, original, tool, Producer("test", "a" * 40, "b" * 64), scratch=tmp_path / "scratch"
    )
    peer = storage.peer_binding(str(tmp_path / "cold"))
    with pytest.raises(BoundaryError, match="invalid_peer_binding"):
        storage.storage_intent("preserve", raw.artifact_id, {**peer, "aws_secret_access_key": "x"})
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: usage(100, 99, 1))
    with pytest.raises(BoundaryError, match="space_insufficient"):
        storage.run_storage(
            local,
            storage.storage_intent("preserve", raw.artifact_id, peer),
            scratch=tmp_path / "scratch",
            reserve_bytes=0,
        )
    assert not ManifestArtifactStore(LocalBlobStore(tmp_path / "cold")).manifest_ids()


def test_target_publication_keeps_schema_role_receipt_and_partial(tmp_path, original, tool):
    from spireagent.workbench.p1_recording_store import (
        p1_projection,
        publish_p1_projection,
        query_p1_recordings,
    )
    from sts2_platform_evidence.collection_tool import P1_TARGET_PROJECTION, P1_TARGET_SUPPORT

    manifest_path = tool.directory / "collection-tool.json"
    manifest = json.loads(manifest_path.read_bytes())
    helper = tool.directory / "p1/tools/p1-native-targets.mjs"
    helper.write_text("export {};\n")
    identity = manifest["identity"]
    identity["p1_native_support"] = P1_TARGET_SUPPORT
    identity["files"].append(
        {
            "path": "p1/tools/p1-native-targets.mjs",
            "bytes": helper.stat().st_size,
            "sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
        }
    )
    identity["files"].sort(key=lambda row: row["path"])
    release = digest(identity)
    manifest_path.write_text(json.dumps({**manifest, "release_id": release}))
    target_tool = CollectionTool(tool.directory, release)
    local = ManifestArtifactStore(LocalBlobStore(tmp_path / "local"))
    producer = Producer("test", "a" * 40, "b" * 64)
    raw = publish_p1_original(local, original, target_tool, producer, scratch=tmp_path / "scratch")
    info = raw.parameters.value()
    output = tmp_path / "targets.json"
    output.write_text(
        json.dumps(
            {
                "schema": "sts2.p1/native-targets-1",
                "projection_id": P1_TARGET_PROJECTION,
                "source_content_id": info["content_id"],
                "source_manifest_sha256": info["bundle_manifest_sha256"],
                "branch": {
                    "run_id": "run-0001",
                    "session_id": info["session_id"],
                    "timeline_id": info["timeline_id"],
                },
                "profile": "test",
                "revision": "test",
                "source_integrity_verified": True,
                "projection_claims": {"lossless_p1_trace": False},
                "timeline": {"events": [{}], "spans": [{"kind": "non_p1_native_effect"}]},
                "points": [{"mask": {"N": True, "next": False, "end": True}}],
                "p1_action_chain_complete": False,
                "memory_continuity": "not_approved_or_validated",
            }
        )
    )
    sha = hashlib.sha256(output.read_bytes()).hexdigest()
    receipt = {
        "source_content_id": info["content_id"],
        "tool_release_id": release,
        "payload_sha256": sha,
        "full_readback": True,
        "original_receipt": "synthetic",
        "owner_diagnostics": {"whole_run_recording_complete": False},
    }

    def publish(evidence):
        return publish_p1_projection(
            local,
            raw.artifact_id,
            target_tool,
            producer,
            output=output,
            expected_sha256=sha,
            projection_id=P1_TARGET_PROJECTION,
            derivation_receipt=evidence,
        )

    with pytest.raises(BoundaryError, match="derivation_receipt_mismatch"):
        publish({**receipt, "tool_release_id": "f" * 64})
    result = publish(receipt)
    assert result.payload("targets").sha256 == sha
    assert result.parameters.value()["summary"]["whole_run_complete"] is False
    assert result.parameters.value()["summary"]["candidate_N"] == 1
    assert publish(receipt).artifact_id == result.artifact_id
    assert p1_projection(local, result.artifact_id).artifact_id == result.artifact_id
    assert query_p1_recordings(local)[0]["training_admitted"] is False
    output.write_text(output.read_text() + " ")
    with pytest.raises(BoundaryError, match="projection_payload_hash_mismatch"):
        publish(receipt)


def test_closed_recording_maintenance_uses_import_owner_and_survives_reopen(
    tmp_path,
    original,
    tool,
    monkeypatch,
):
    import shutil

    from spireagent.storage.registry import SQLiteRegistry
    from spireagent.workbench import local_recording_import as importing
    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.p1_recording_maintenance import P1RecordingMaintenance
    from spireagent.workbench.p1_recording_storage import peer_binding

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    registry = SQLiteRegistry(tmp_path / "registry.sqlite")
    candidate = {
        **_closed_session(original / "raw"),
        "source_directory": original / "raw",
        "p1_tool_supported": True,
        "tool_release_id": tool.release_id,
    }
    identity = "a" * 64

    class Catalog:
        def read(self):
            return {
                "status": "ready",
                "candidates": [{"candidate_id": identity, "recording_type": "p1_native"}],
            }

        def candidate(self, value):
            return candidate if value == identity else None

    calls = []

    def pack(_self, _source, output, _worker, _campaign, **kwargs):
        calls.append(kwargs)
        shutil.copytree(original, output)

    monkeypatch.setattr(CollectionTool, "pack_p1_native", pack)
    monkeypatch.setattr(importing, "_selected_store", lambda _, **_kw: (store, registry))
    monkeypatch.setattr(
        importing, "current_collection_tool", lambda _: (tool.directory, tool.release_id)
    )
    monkeypatch.setattr(
        importing, "source_identity", lambda _: Producer("test", "a" * 40, "b" * 64)
    )
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config.state_dir.mkdir()
    importer = importing.LocalRecordingImporter(config, Catalog())
    maintenance = P1RecordingMaintenance(importer)
    maintenance.configure(True, peer_binding(str(tmp_path / "cold")), projection_id=None)
    maintenance.tick()
    importer.thread.join(timeout=10)
    assert importer.status()["status"] == "completed"
    assert calls == [{"human_origin_attested": False}], (
        "preservation must not invent Human attestation"
    )
    maintenance.tick()
    importer.thread.join(timeout=10)
    assert importer.status()["storage_receipt"]["full_readback"] is True
    assert maintenance.tick()["status"] == "completed"
    reopened = importing.LocalRecordingImporter(config, Catalog())
    assert reopened._maintenance["completed_candidates"] == [identity]
    assert P1RecordingMaintenance(reopened).tick()["status"] == "up_to_date"
    assert len(calls) == 1
    reopened.operation.update(status="interrupted_unknown")
    assert P1RecordingMaintenance(reopened).tick()["status"] == "blocked"
    assert len(calls) == 1


def test_background_storage_lost_ack_requires_explicit_readback(
    tmp_path, original, tool, monkeypatch
):
    from spireagent.storage.registry import SQLiteRegistry
    from spireagent.workbench import local_recording_import as importing
    from spireagent.workbench import p1_recording_storage as storage
    from spireagent.workbench.developer import ProjectConfig, combination

    local = ManifestArtifactStore(LocalBlobStore(tmp_path / "local"))
    raw = publish_p1_original(
        local, original, tool, Producer("test", "a" * 40, "b" * 64), scratch=tmp_path / "scratch"
    )
    registry = SQLiteRegistry(tmp_path / "registry.sqlite")
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config.state_dir.mkdir()
    importer = importing.LocalRecordingImporter(config, None)
    monkeypatch.setattr(importing, "_selected_store", lambda _, **_kw: (local, registry))
    copy = storage.copy_artifact
    calls = []

    def lost_ack(source, target, identity, **kwargs):
        calls.append(identity)
        copy(source, target, identity, **kwargs)
        raise OSError("lost publication reply")

    monkeypatch.setattr(storage, "copy_artifact", lost_ack)
    importer.start_p1_storage(
        "preserve", raw.artifact_id, storage.peer_binding(str(tmp_path / "cold"))
    )
    importer.thread.join(timeout=10)
    assert importer.status()["status"] == "publication_unknown"
    reopened = importing.LocalRecordingImporter(config, None)
    with pytest.raises(BoundaryError, match="original_intent_reconciliation_required"):
        reopened.start_p1_storage(
            "preserve", raw.artifact_id, storage.peer_binding(str(tmp_path / "cold"))
        )
    assert reopened.reconcile_p1_storage()["storage_receipt"]["full_readback"] is True
    assert len(calls) == 1


def test_query_sources_keeps_unknown_metadata_and_does_not_double_count_views(
    tmp_path,
    original,
    tool,
):
    from spireagent.workbench.p1_recording_store import query_p1_sources

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    producer = Producer("test", "a" * 40, "b" * 64)
    raw = publish_p1_original(store, original, tool, producer, scratch=tmp_path / "scratch")
    derive_p1_recording(store, raw.artifact_id, tool, producer, scratch=tmp_path / "scratch")
    result = query_p1_sources(store, filters={"character": None, "has_projection": True})
    assert len(result) == 1 and len(result[0]["projections"]) == 1
    assert result[0]["character"] is None and result[0]["game_version"] is None
    assert result[0]["preservation_verified"] is False
    assert query_p1_sources(store, filters={"character": "invented"}) == []
    with pytest.raises(BoundaryError, match="unsupported_query_filter"):
        query_p1_sources(store, filters={"hidden_state": "bad"})


def test_changed_maintenance_policy_rechecks_previously_completed_sources(
    tmp_path,
    original,
    tool,
    monkeypatch,
):
    from spireagent.storage.registry import SQLiteRegistry
    from spireagent.workbench import local_recording_import as importing
    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.p1_recording_maintenance import P1RecordingMaintenance
    from spireagent.workbench.p1_recording_storage import peer_binding

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    registry = SQLiteRegistry(tmp_path / "registry.sqlite")
    candidate = {**_closed_session(original / "raw"), "source_directory": original / "raw"}
    identity = "a" * 64

    class Catalog:
        def read(self):
            return {
                "status": "ready",
                "candidates": [{"candidate_id": identity, "recording_type": "p1_native"}],
            }

        def candidate(self, value):
            return candidate

    source = publish_p1_original(
        store, original, tool, Producer("test", "a" * 40, "b" * 64), scratch=tmp_path / "scratch"
    )
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config.state_dir.mkdir()
    importer = importing.LocalRecordingImporter(config, Catalog())
    monkeypatch.setattr(importing, "_selected_store", lambda _, **_kw: (store, registry))
    maintenance = P1RecordingMaintenance(importer)
    maintenance.configure(True, None, projection_id=None)
    assert maintenance.tick()["status"] == "completed"
    assert not importer._storage_receipts
    maintenance.configure(True, peer_binding(str(tmp_path / "cold")), projection_id=None)
    maintenance.tick()
    importer.thread.join(timeout=10)
    assert importer.status()["storage_receipt"]["artifact_id"] == source.artifact_id
    assert maintenance.tick()["status"] == "completed"


def test_cli_state_carries_all_owners_and_native_unknown_fences_new_work():
    from spireagent.workbench.local_recording_import import SCHEMA
    from spireagent.workbench.p1_recordings_cli import _carry_operation_state, _operation_guard

    previous = {
        "schema": SCHEMA,
        "status": "completed",
        "_maintenance": {"policy": {"enabled": True}},
        "_storage_receipts": {"old": {}},
        "_native_agent_intents": {"a": {"status": "completed"}},
    }
    _operation_guard(previous)
    next_operation = _carry_operation_state(previous, {"schema": SCHEMA, "status": "pending"})
    for key in ("_maintenance", "_storage_receipts", "_native_agent_intents"):
        assert next_operation[key] == previous[key]
    previous["_native_agent_intents"]["a"]["status"] = "publication_unknown"
    with pytest.raises(BoundaryError, match="explicit_native_intent_reconciliation_required"):
        _operation_guard(previous)


def test_direct_publication_space_precheck_precedes_payload_writes(
    tmp_path,
    original,
    tool,
    monkeypatch,
):
    from collections import namedtuple

    from spireagent.workbench import p1_recording_storage as storage

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: usage(100, 99, 1))
    with pytest.raises(BoundaryError, match="space_insufficient"):
        publish_p1_original(
            store,
            original,
            tool,
            Producer("test", "a" * 40, "b" * 64),
            scratch=tmp_path / "scratch",
        )
    assert not store.manifest_ids()
    assert not list((tmp_path / "store").rglob("objects/sha256/*"))


def test_explicit_storage_reconcile_can_establish_absence_without_copy(tmp_path):
    from spireagent.workbench.p1_recording_storage import (
        peer_binding,
        reconcile_storage,
        storage_intent,
    )

    local = ManifestArtifactStore(LocalBlobStore(tmp_path / "local"))
    intent = storage_intent("restore", "a" * 64, peer_binding(str(tmp_path / "cold")))
    result = reconcile_storage(local, intent, scratch=tmp_path / "scratch")
    assert result["status"] == "failed" and result["publication"] == "absent_or_incomplete"
