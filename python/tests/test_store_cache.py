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
