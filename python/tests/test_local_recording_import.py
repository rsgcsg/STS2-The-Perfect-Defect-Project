"""Synthetic local import exercises the real typed Evidence verifier."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from platform_bundle3_fixture import bundle3, load, seal, write

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench import local_recording_import as importing
from spireagent.workbench import managed_local_workspace as managed
from spireagent.workbench.developer import LocalResearchWorkspaceConfig, ProjectConfig, combination
from spireagent.workbench.inplace_curation import InplaceCurationPreparation


class Catalog:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.identity = "a" * 64
        self.changed = False
        self.reads = 0

    def read(self) -> dict:
        self.reads += 1
        return {"status": "ready"}

    def candidate(self, identity: str) -> dict | None:
        if identity != self.identity or self.changed:
            return None
        return {"source_directory": self.source, "session_id": "session-1",
                "timeline_id": "timeline-v2-test", "manifest_sha256": "b" * 64,
                "close_sha256": "c" * 64}


def setup(tmp_path: Path, monkeypatch) -> tuple[importing.LocalRecordingImporter, Catalog,
                                               ManifestArtifactStore, Path]:
    state = tmp_path / "state"
    state.mkdir()
    source = tmp_path / "recordings" / "closed"
    source.mkdir(parents=True)
    (source / "recording-manifest.json").write_text("synthetic raw marker")
    store_dir = tmp_path / "research-store"
    store = ManifestArtifactStore(LocalBlobStore(store_dir))
    registry_path = tmp_path / "registry.sqlite"
    SQLiteRegistry(registry_path)
    config = ProjectConfig(
        state, "", "", None, combination(),
        LocalResearchWorkspaceConfig(store_dir, registry_path),
    )
    preparation = InplaceCurationPreparation(config)
    preparation.start()
    assert preparation.thread is not None
    preparation.thread.join(timeout=15)
    assert preparation.status()["status"] == "ready"
    template = bundle3(tmp_path / "fixture")

    class Tool:
        calls = 0

        def __init__(self, directory, release_id):
            assert release_id == "d" * 64

        def pack(self, session, output, worker, campaign):
            Tool.calls += 1
            assert session == source
            shutil.copytree(template, output)
            path = output / "session-bundle-manifest.json"
            manifest = load(path)
            manifest["worker_id"] = worker
            manifest["campaign_id"] = campaign
            manifest["human_origin_attestation"]["worker_id"] = worker
            write(path, manifest)
            seal(output)

    monkeypatch.setattr(importing, "CollectionTool", Tool)
    monkeypatch.setattr(importing, "current_collection_tool",
                        lambda _config: (tmp_path / "synthetic-tool", "d" * 64))
    monkeypatch.setattr(importing, "source_identity", lambda _root:
                        Producer("local/synthetic-fixture", "a" * 40, "b" * 64))
    catalog = Catalog(source)
    return importing.LocalRecordingImporter(config, catalog), catalog, store, source


def finished(importer: importing.LocalRecordingImporter) -> dict:
    assert importer.thread is not None
    importer.thread.join(timeout=15)
    assert not importer.thread.is_alive()
    return importer.status()


def test_explicit_verified_import_publishes_once_and_preserves_source(tmp_path, monkeypatch):
    importer, catalog, store, source = setup(tmp_path, monkeypatch)
    before = (source / "recording-manifest.json").read_bytes()
    with pytest.raises(BoundaryError, match="attestation"):
        importer.start(catalog.identity, False)
    with pytest.raises(BoundaryError, match="attestation"):
        importer.start(catalog.identity, None)
    assert not store.manifest_ids() and importer.status()["status"] == "idle"

    started = importer.start(catalog.identity, True)
    assert started["status"] == "pending"
    done = finished(importer)
    assert done["status"] == "completed", done
    artifact_id = done["artifact_id"]
    assert store.manifest_ids() == (artifact_id,)
    manifest = store.get_manifest(artifact_id)
    assert manifest.kind == "evidence"
    assert manifest.parameters.value()["schema"] == importing.EVIDENCE_SCHEMA
    assert manifest.parameters.value()["research_admission"] == "not_evaluated"
    assert manifest.parameters.value()["hub_receipt"] is None
    assert manifest.payload("archive").size > 0
    assert (source / "recording-manifest.json").read_bytes() == before
    assert importer.start(catalog.identity, True)["artifact_id"] == artifact_id
    assert store.manifest_ids() == (artifact_id,)
    reopened = importing.LocalRecordingImporter(importer.config, catalog)
    assert reopened.status()["artifact_id"] == artifact_id
    importing.atomic_json(importer.path, {
        "schema": importing.SCHEMA, "status": "pending", "candidate_id": catalog.identity,
    })
    interrupted = importing.LocalRecordingImporter(importer.config, catalog)
    assert interrupted.status()["status"] == "interrupted_unknown"
    class NoSecondPack:
        def __init__(self, *_args):
            pytest.fail("already published candidate must not pack again")
    monkeypatch.setattr(importing, "CollectionTool", NoSecondPack)
    assert interrupted.start(catalog.identity, True)["artifact_id"] == artifact_id

    from stpd.fullrun.data import publish_received_source
    with pytest.raises(BoundaryError, match="unsupported_received_bundle"):
        publish_received_source(store, artifact_id, Producer("fixture", "a" * 40, "b" * 64))


def test_managed_import_keeps_source_inventory_pending_after_publish(tmp_path, monkeypatch):
    importer, catalog, _, _ = setup(tmp_path, monkeypatch)
    state = importer.config.state_dir
    managed.create_managed_workspace(state)
    importer.config = ProjectConfig(state, "", "", None, combination())
    importer.start(catalog.identity, True)
    done = finished(importer)
    assert done["status"] == "completed", done
    selected = managed.inspect_managed_workspace(state)
    with selected["curation_owner"].transaction() as db:
        assert db.execute("SELECT candidate,artifact,status FROM local_source_pending"
                          ).fetchone() == (catalog.identity, done["artifact_id"], "published")
    with pytest.raises(BoundaryError, match="gold_source_inventory_pending"):
        selected["curation_owner"].ledger.claim("gold", "gold", {"run"})


def test_changed_candidate_and_pack_failure_do_not_publish(tmp_path, monkeypatch):
    importer, catalog, store, _ = setup(tmp_path, monkeypatch)
    catalog.changed = True
    with pytest.raises(BoundaryError, match="candidate_changed"):
        importer.start(catalog.identity, True)
    assert not store.manifest_ids()

    catalog.changed = False
    class BrokenTool:
        def __init__(self, *_args):
            pass

        def pack(self, *_args):
            raise ValueError("private pack diagnostic")

    monkeypatch.setattr(importing, "CollectionTool", BrokenTool)
    importer.start(catalog.identity, True)
    result = finished(importer)
    assert result["status"] == "failed"
    assert result["error_code"] == "local_import_failed"
    assert "private pack diagnostic" not in json.dumps(result)
    assert not store.manifest_ids()


def test_real_typed_verifier_rejects_corrupt_packed_bundle(tmp_path, monkeypatch):
    importer, catalog, store, _ = setup(tmp_path, monkeypatch)
    template = bundle3(tmp_path / "bad-fixture")
    (template / "checksums.sha256").write_text("invalid")

    class BadTool:
        def __init__(self, *_args):
            pass

        def pack(self, _source, output, _worker, _campaign):
            shutil.copytree(template, output)

    monkeypatch.setattr(importing, "CollectionTool", BadTool)
    importer.start(catalog.identity, True)
    assert finished(importer)["status"] == "failed"
    assert not store.manifest_ids()


def test_owner_seal_change_during_pack_fails_before_publication(tmp_path, monkeypatch):
    importer, catalog, store, _ = setup(tmp_path, monkeypatch)

    class DriftingTool:
        def __init__(self, *_args):
            pass

        def pack(self, *_args):
            catalog.changed = True

    monkeypatch.setattr(importing, "CollectionTool", DriftingTool)
    importer.start(catalog.identity, True)
    result = finished(importer)
    assert result["status"] == "failed"
    assert result["error_code"] == "candidate_changed_or_unavailable"
    assert not store.manifest_ids()


def test_pending_restart_is_unknown_and_never_runs_without_post(tmp_path, monkeypatch):
    importer, catalog, store, _ = setup(tmp_path, monkeypatch)
    importing.atomic_json(importer.path, {
        "schema": importing.SCHEMA, "status": "pending", "candidate_id": catalog.identity,
    })
    restarted = importing.LocalRecordingImporter(importer.config, catalog)
    assert restarted.status()["status"] == "interrupted_unknown"
    assert restarted.thread is None
    assert not store.manifest_ids()


def test_publication_read_failure_reports_unknown_after_possible_commit(tmp_path, monkeypatch):
    importer, catalog, store, _ = setup(tmp_path, monkeypatch)
    importer.operation = {"schema": importing.SCHEMA, "status": "pending",
                          "candidate_id": catalog.identity}

    def publish_then_fail(*_args):
        store.publish(Manifest(
            "evidence", Producer("fixture", "a" * 40, "b" * 64),
            parameters=FrozenObject.of({
                "schema": importing.EVIDENCE_SCHEMA, "candidate_id": catalog.identity,
            }),
        ))
        raise OSError("status write interrupted after publication")

    monkeypatch.setattr(importer, "_import", publish_then_fail)
    monkeypatch.setattr(importing, "_selected_store", lambda _config: (_ for _ in ()).throw(
        OSError("synthetic store unavailable for reconciliation")))
    importer._run(catalog.identity, catalog.candidate(catalog.identity))
    assert importer.status()["status"] == "publication_unknown"
    assert importer.status()["error_code"] == "publication_state_unavailable"
    assert len(store.manifest_ids()) == 1
