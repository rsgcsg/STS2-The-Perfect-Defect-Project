import json
import shutil
from pathlib import Path

import pytest
from test_decision_training import prepared

from spireagent.storage.blobs import StoreError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from stpd.fullrun.decision_training import AllocationSpec, publish_allocation, publish_decision_view
from stpd.fullrun.features import load_model_view
from stpd.fullrun.representation import FullRunSerializer
from stpd.fullrun.view_session import verified_model_views


def test_reuse_avoids_semantic_reprojection_but_detects_source_corruption(tmp_path, monkeypatch):
    owner, dataset = prepared(tmp_path)
    allocation = publish_allocation(owner.store, dataset, AllocationSpec(), owner.producer)
    view = publish_decision_view(
        owner.store, allocation.artifact_id, FullRunSerializer(), owner.producer
    )
    with verified_model_views(owner.store) as session:
        expected = load_model_view(owner.store, view.artifact_id)
        monkeypatch.setattr(
            "stpd.fullrun.features._load_model_view",
            lambda *args: pytest.fail("repeated semantic reprojection"),
        )
        assert load_model_view(owner.store, view.artifact_id) == expected
        assert (session.misses, session.hits) == (1, 1)
        # Corrupt an original archive, not just the cached view's own bytes.
        pending = [view]
        source = None
        while pending:
            item = pending.pop()
            if item.kind == "evidence":
                source = item
                break
            pending.extend(owner.store.get_manifest(p.artifact_id) for p in item.parents)
        assert source is not None
        payload = source.payload("archive")
        index = json.loads(owner.store.blobs.get(f"payload-indexes/v1/{payload.sha256}.json"))
        chunk = index["chunks"][0]["sha256"]
        path = Path(owner.store.blobs.root) / "objects" / "sha256" / chunk
        path.write_bytes(b"corrupt")
        with pytest.raises(StoreError, match="integrity"):
            load_model_view(owner.store, view.artifact_id)


def test_command_scope_restores_after_exception(tmp_path):
    from stpd.fullrun.view_session import _CURRENT

    owner, _ = prepared(tmp_path)
    assert _CURRENT.get() is None
    with verified_model_views(owner.store) as outer:
        with pytest.raises(RuntimeError), verified_model_views(owner.store) as inner:
            assert inner is not outer
            raise RuntimeError("interrupted command")
        assert _CURRENT.get() is outer
    assert _CURRENT.get() is None


def test_view_reuse_across_same_root_wrappers_isolated_from_copy_and_replacement(
    tmp_path, monkeypatch,
):
    import stpd.fullrun.features as features

    owner, dataset = prepared(tmp_path)
    allocation = publish_allocation(owner.store, dataset, AllocationSpec(), owner.producer)
    view = publish_decision_view(
        owner.store, allocation.artifact_id, FullRunSerializer(), owner.producer,
    )
    original = features._load_model_view
    loads = []

    def counted(store, identity):
        loads.append(store)
        return original(store, identity)

    monkeypatch.setattr(features, "_load_model_view", counted)
    with verified_model_views(owner.store) as session:
        expected = load_model_view(owner.store, view.artifact_id)
        wrapper = ManifestArtifactStore(LocalBlobStore(owner.store.blobs.root, create=False))
        assert load_model_view(wrapper, view.artifact_id) == expected
        assert loads == [owner.store]
        assert session.hits == 1
        other = ManifestArtifactStore(LocalBlobStore(tmp_path / "other-store"))
        copy_artifact(owner.store, other, view.artifact_id)
        assert load_model_view(other, view.artifact_id) == expected
        assert loads == [owner.store, other]
        root = owner.store.blobs.root
        saved = root.with_name("saved-store")
        root.rename(saved)
        shutil.copytree(saved, root)
        assert load_model_view(wrapper, view.artifact_id) == expected
        assert loads == [owner.store, other, wrapper]
        assert session.hits == 1
    assert session.value is None
