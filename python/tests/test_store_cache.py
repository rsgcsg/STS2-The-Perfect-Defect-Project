"""Cold bytes must reach the original domain reader without changing artifact identity."""
from __future__ import annotations

import hashlib

import pytest
from test_p1_snapshot import projection_fixture, store_at

from spireagent.storage.blobs import StoreError
from spireagent.storage.cache import configure_cache
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from stpd.p1.dataset import load_projection


def test_normal_reader_restores_same_id_missing_manifest_index_and_chunk(tmp_path):
    local, peer = store_at(tmp_path / "local"), store_at(tmp_path / "peer")
    projection, _, _ = projection_fixture(local, "cold")
    copy_artifact(local, peer, projection.artifact_id)
    configure_cache(local, str(tmp_path / "peer"), max_bytes=1024)
    for prefix in ("objects/", "payload-indexes/", "manifests/"):
        for key in local.blobs.keys(prefix):
            (local.blobs.root / key).unlink()
    reopened = store_at(tmp_path / "local")
    assert load_projection(reopened, projection.artifact_id).manifest == projection
    assert load_projection(reopened, projection.artifact_id).value["segments"][0]["points"]
    for key in reopened.blobs.keys("objects/"):
        assert hashlib.sha256(reopened.blobs.get(key)).hexdigest() == key.rsplit("/", 1)[1]


def test_local_or_remote_corruption_never_falls_back_or_caches_bad_chunk(tmp_path):
    local, peer = store_at(tmp_path / "local"), store_at(tmp_path / "peer")
    projection, _, _ = projection_fixture(local, "bad")
    copy_artifact(local, peer, projection.artifact_id)
    configure_cache(local, str(tmp_path / "peer"), max_bytes=1024)
    key = "objects/sha256/" + projection.payload("segments").sha256
    local_path = local.blobs.root / key
    local_path.write_bytes(b"corrupt local")
    with pytest.raises(StoreError, match="payload_chunk_integrity_failure"):
        load_projection(local, projection.artifact_id)
    local_path.unlink()
    (peer.blobs.root / key).write_bytes(b"corrupt peer")
    with pytest.raises(StoreError, match="payload_chunk_integrity_failure"):
        load_projection(local, projection.artifact_id)
    assert not local_path.exists()


def test_missing_peer_bytes_keep_typed_failure_and_readonly_does_not_write(tmp_path):
    local, peer = store_at(tmp_path / "local"), store_at(tmp_path / "peer")
    projection, _, _ = projection_fixture(local, "readonly")
    copy_artifact(local, peer, projection.artifact_id)
    configure_cache(local, str(tmp_path / "peer"), max_bytes=1024)
    key = "objects/sha256/" + projection.payload("segments").sha256
    (local.blobs.root / key).unlink()
    readonly = ManifestArtifactStore(LocalBlobStore(tmp_path / "local", readonly=True))
    assert load_projection(readonly, projection.artifact_id).value["segments"][0]["points"]
    assert not (local.blobs.root / key).exists()
    (peer.blobs.root / key).unlink()
    with pytest.raises(StoreError, match="object_not_found"):
        load_projection(local, projection.artifact_id)


def test_trim_keeps_shared_recent_and_active_reader_then_recovers(tmp_path):
    import io
    import os

    from test_artifact_store_v1 import PRODUCER

    from spireagent.artifact_contracts import Manifest
    from spireagent.storage.cache import trim_cache
    from spireagent.storage.store import CHUNK_BYTES

    local, peer = store_at(tmp_path / "local"), store_at(tmp_path / "peer")
    common = b"s" * CHUNK_BYTES
    values = [common + b"old", b"active", common + b"recent", b"newest"]
    manifests = []
    for index, value in enumerate(values):
        payload = local.put_payload("data", io.BytesIO(value))
        manifest = Manifest("analysis", PRODUCER, payloads=(payload,))
        local.publish(manifest)
        copy_artifact(local, peer, manifest.artifact_id)
        os.utime(local.blobs.root / "manifests" / (manifest.artifact_id + ".json"),
                 ns=(100 + index, 100 + index))
        manifests.append(manifest)
    configure_cache(local, str(tmp_path / "peer"), max_bytes=0)
    reading = local.read_payload(manifests[1].payload("data"))
    assert next(reading) == b"active"
    first = trim_cache(local)
    assert first["active_chunks"] and not first["within_budget"]
    shared_key = "objects/sha256/" + hashlib.sha256(common).hexdigest()
    assert local.blobs.get(shared_key) == common
    reading.close()
    second = trim_cache(local)
    assert not second["active_chunks"]
    assert b"".join(local.read_payload(manifests[0].payload("data"))) == values[0]
    assert b"".join(local.read_payload(manifests[1].payload("data"))) == values[1]
    assert set(first["retained_artifact_ids"]) == {m.artifact_id for m in manifests[-2:]}


def test_trim_refuses_unpreserved_bytes_and_keeps_models(tmp_path):
    import io
    import os

    from test_artifact_store_v1 import PRODUCER

    from spireagent.artifact_contracts import Manifest
    from spireagent.storage.cache import trim_cache

    local = store_at(tmp_path / "local")
    store_at(tmp_path / "peer")
    manifests = []
    for index, kind in enumerate(("analysis", "model", "analysis", "analysis")):
        manifest = Manifest(kind, PRODUCER, payloads=(
            local.put_payload("data", io.BytesIO(str(index).encode())),))
        local.publish(manifest)
        os.utime(local.blobs.root / "manifests" / (manifest.artifact_id + ".json"),
                 ns=(100 + index, 100 + index))
        manifests.append(manifest)
    configure_cache(local, str(tmp_path / "peer"), max_bytes=0)
    receipt = trim_cache(local)
    assert receipt["unverified_chunks"]
    assert not receipt["removed_chunks"]
    assert manifests[1].artifact_id in receipt["retained_artifact_ids"]


def test_readonly_cache_never_initializes_missing_lease(tmp_path, monkeypatch):
    from pathlib import Path

    local, peer = store_at(tmp_path / "local"), store_at(tmp_path / "peer")
    projection, _, _ = projection_fixture(local, "readonly-leases")
    copy_artifact(local, peer, projection.artifact_id)
    configure_cache(local, str(tmp_path / "peer"), max_bytes=1024)
    readonly = ManifestArtifactStore(LocalBlobStore(tmp_path / "local", readonly=True))
    original_mkdir, original_touch = Path.mkdir, Path.touch

    def refuse_mkdir(path, *args, **kwargs):
        if path.is_relative_to(local.blobs.root):
            raise AssertionError("readonly reader wrote a directory")
        return original_mkdir(path, *args, **kwargs)

    def refuse_touch(path, *args, **kwargs):
        if path.is_relative_to(local.blobs.root):
            raise AssertionError("readonly reader wrote a lock")
        return original_touch(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", refuse_mkdir)
    monkeypatch.setattr(Path, "touch", refuse_touch)
    assert load_projection(readonly, projection.artifact_id).value["segments"][0]["points"]
    lease = local.blobs.root / ".cache-leases" / (projection.payload("segments").sha256 + ".lock")
    lease.unlink()
    with pytest.raises(StoreError, match="cache_read_lease_missing"):
        load_projection(readonly, projection.artifact_id)
    assert not lease.exists()


def test_existing_maintenance_owner_trims_and_persists_receipt(tmp_path, monkeypatch):
    import io
    import os

    from test_artifact_store_v1 import PRODUCER

    from spireagent.artifact_contracts import Manifest
    from spireagent.workbench import local_recording_import as importing
    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.p1_recording_maintenance import P1RecordingMaintenance

    local, peer = store_at(tmp_path / "local"), store_at(tmp_path / "peer")
    for index in range(4):
        manifest = Manifest("analysis", PRODUCER, payloads=(
            local.put_payload("data", io.BytesIO(str(index).encode())),))
        local.publish(manifest)
        copy_artifact(local, peer, manifest.artifact_id)
        os.utime(local.blobs.root / "manifests" / (manifest.artifact_id + ".json"),
                 ns=(100 + index, 100 + index))
    configure_cache(local, str(tmp_path / "peer"), max_bytes=0)

    class Catalog:
        def read(self):
            return {"status": "ready", "game_running": False, "candidates": []}

    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config.state_dir.mkdir()
    importer = importing.LocalRecordingImporter(config, Catalog())
    monkeypatch.setattr(importing, "_selected_store", lambda *a, **k: (local, None))
    maintenance = P1RecordingMaintenance(importer)
    maintenance.configure(True, {"kind": "local", "directory": str(tmp_path / "peer")},
                          projection_id=None)
    assert maintenance.tick()["status"] == "cache_trimming"
    importer.thread.join(timeout=3)
    assert not importer.thread.is_alive()
    assert importer.operation["status"] == "completed"
    assert len(importer.operation["storage_receipt"]["removed_chunks"]) == 2
    reopened = importing.LocalRecordingImporter(config, Catalog())
    assert reopened._maintenance["cache"] == importer.operation["storage_receipt"]
    assert maintenance.tick()["status"] == "cache_over_budget"


def test_new_publication_prepares_lease_for_future_readonly_consumer(tmp_path):
    import io

    from test_artifact_store_v1 import PRODUCER

    from spireagent.artifact_contracts import Manifest

    local = store_at(tmp_path / "local")
    store_at(tmp_path / "peer")
    configure_cache(local, str(tmp_path / "peer"), max_bytes=1024)
    payload = local.put_payload("data", io.BytesIO(b"published after cache setup"))
    manifest = Manifest("analysis", PRODUCER, payloads=(payload,))
    local.publish(manifest)
    readonly = ManifestArtifactStore(LocalBlobStore(tmp_path / "local", readonly=True))
    assert b"".join(readonly.read_payload(payload)) == b"published after cache setup"


def test_maintenance_rechecks_active_reader_after_backoff(tmp_path, monkeypatch):
    import io
    import os

    from test_artifact_store_v1 import PRODUCER

    from spireagent.artifact_contracts import Manifest
    from spireagent.workbench import local_recording_import as importing
    from spireagent.workbench import p1_recording_maintenance as maintaining
    from spireagent.workbench.developer import ProjectConfig, combination

    local, peer = store_at(tmp_path / "local"), store_at(tmp_path / "peer")
    manifests = []
    for index in range(3):
        manifest = Manifest("analysis", PRODUCER, payloads=(
            local.put_payload("data", io.BytesIO(str(index).encode())),))
        local.publish(manifest)
        copy_artifact(local, peer, manifest.artifact_id)
        os.utime(local.blobs.root / "manifests" / (manifest.artifact_id + ".json"),
                 ns=(100 + index, 100 + index))
        manifests.append(manifest)
    configure_cache(local, str(tmp_path / "peer"), max_bytes=2)

    class Catalog:
        def read(self):
            return {"status": "ready", "game_running": False, "candidates": []}

    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config.state_dir.mkdir()
    importer = importing.LocalRecordingImporter(config, Catalog())
    monkeypatch.setattr(importing, "_selected_store", lambda *a, **k: (local, None))
    maintenance = maintaining.P1RecordingMaintenance(importer)
    maintenance.configure(True, {"kind": "local", "directory": str(tmp_path / "peer")},
                          projection_id=None)
    reader = local.read_payload(manifests[0].payload("data"))
    next(reader)
    assert maintenance.tick()["status"] == "cache_trimming"
    importer.thread.join(timeout=3)
    assert importer._maintenance["cache"]["active_chunks"]
    assert maintenance.tick()["status"] == "cache_over_budget"
    reader.close()
    # Closed reader alone is not success. The scheduled retry must actually trim.
    monkeypatch.setattr(maintaining.time, "time",
                        lambda: importer._maintenance["cache_retry_after"] + 1)
    assert maintenance.tick()["status"] == "cache_trimming"
    importer.thread.join(timeout=3)
    assert importer._maintenance["cache"]["within_budget"]
    assert maintenance.tick()["status"] == "up_to_date"


def test_two_pinned_peers_recover_and_trim_only_on_exact_absence(tmp_path):
    import io
    import os

    from test_artifact_store_v1 import PRODUCER

    from spireagent.artifact_contracts import Manifest
    from spireagent.storage.cache import trim_cache

    local, primary, secondary = [store_at(tmp_path / name)
                                 for name in ("local", "primary", "secondary")]
    projection, _, _ = projection_fixture(local, "second-peer")
    copy_artifact(local, secondary, projection.artifact_id)
    configure_cache(local, str(tmp_path / "primary"), max_bytes=0,
                    additional_peer=str(tmp_path / "secondary"))
    key = "objects/sha256/" + projection.payload("segments").sha256
    (local.blobs.root / key).unlink()
    assert load_projection(local, projection.artifact_id).manifest == projection
    for index in range(2):
        manifest = Manifest("analysis", PRODUCER, payloads=(
            local.put_payload("data", io.BytesIO(str(index).encode())),))
        local.publish(manifest)
        os.utime(local.blobs.root / "manifests" / (manifest.artifact_id + ".json"),
                 ns=(10**18 + index, 10**18 + index))
    # Make the two recent markers newer than the original read-through artifacts.
    for identity in (projection.artifact_id, projection.parent("source")):
        os.utime(local.blobs.root / "manifests" / (identity + ".json"), ns=(1, 1))
    # The source's payload was not materialized in this read-only projection path;
    # retain its local index metadata for trim inventory without fetching raw bytes.
    source = secondary.get_manifest(projection.parent("source"))
    for payload in source.payloads:
        index_key = f"payload-indexes/v1/{payload.sha256}.json"
        local.blobs.put_if_absent(index_key, secondary.blobs.get(index_key))
    trimmed = trim_cache(local)
    assert key.rsplit("/", 1)[1] in trimmed["removed_chunks"]
    assert load_projection(local, projection.artifact_id).manifest == projection
    copy_artifact(secondary, primary, projection.artifact_id)
    (primary.blobs.root / key).write_bytes(b"corrupt first declared peer")
    (local.blobs.root / key).unlink()
    with pytest.raises(StoreError, match="payload_chunk_integrity_failure"):
        load_projection(local, projection.artifact_id)
    assert not (local.blobs.root / key).exists()
