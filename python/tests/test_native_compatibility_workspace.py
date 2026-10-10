"""Real managed reader/publication boundary without a numerical backend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ProjectConfig
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_model_export import LocalModelExport, _DownloadedStructuredModel
from spireagent.workbench.managed_local_workspace import create_managed_workspace
from stpd.native_code_scope import COMMON_MODEL_SCHEMA


@pytest.fixture
def workspace(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    create_managed_workspace(state)
    config = ProjectConfig(state, "", "", None, {})
    curation, writable, _ = LocalDatasetService(config)._selected()
    original = Manifest(
        "model",
        Producer("fixture://workspace", "a" * 40, "b" * 64),
        parameters=FrozenObject.of({"schema": COMMON_MODEL_SCHEMA}),
    )
    writable.publish(original)
    return LocalModelExport(config), curation, writable, original


def test_actual_inspection_stays_readonly_and_publication_opens_same_marker_bound_store(workspace):
    service, curation, _, original = workspace
    reader = service._source(original.artifact_id)
    assert isinstance(reader, ManifestArtifactStore) and reader.blobs.readonly
    assert hasattr(reader, "publish") and hasattr(reader, "put_payload")
    publisher = service._native_publication_store(reader, original)
    assert not publisher.blobs.readonly
    assert publisher.blobs.root == reader.blobs.root == curation.store_dir
    assert publisher.get_manifest(original.artifact_id).to_bytes() == original.to_bytes()
    # Opening publication never turns the inspection handle into a writer.
    assert reader.blobs.readonly


def test_wrong_reader_root_with_identical_original_is_rejected(workspace, tmp_path):
    service, _, _, original = workspace
    alternate = ManifestArtifactStore(LocalBlobStore(tmp_path / "alternate"))
    alternate.publish(original)
    reader = ManifestArtifactStore(
        LocalBlobStore(alternate.blobs.root, create=False, readonly=True)
    )
    with pytest.raises(BoundaryError, match="compatibility_publication_workspace_mismatch"):
        service._native_publication_store(reader, original)
    assert not service._path().exists()


def test_missing_curation_marker_keeps_read_access_but_denies_publication(workspace):
    service, curation, _, original = workspace
    reader = service._source(original.artifact_id)
    (curation.store_dir / ".curation-owner.json").unlink()
    assert service._source(original.artifact_id).get_manifest(original.artifact_id) == original
    with pytest.raises(BoundaryError, match="curation_owner_recovery_required"):
        service._native_publication_store(reader, original)
    assert reader.blobs.readonly
    assert not service._path().exists()


def test_a_readonly_publication_selection_is_not_a_capability(workspace, monkeypatch):
    service, curation, _, original = workspace
    reader = service._source(original.artifact_id)
    monkeypatch.setattr(
        LocalDatasetService, "_selected", lambda self: (curation, reader, Path("unused"))
    )
    with pytest.raises(BoundaryError, match="compatibility_publication_workspace_mismatch"):
        service._native_publication_store(reader, original)


def test_download_cache_cannot_acquire_publication_from_a_different_workspace(workspace):
    service, _, _, original = workspace
    directory = service.config.state_dir / "downloads" / original.artifact_id
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(original.to_bytes())
    (directory / "download.json").write_text(
        json.dumps({"schema": "stpd/result-download-v1", "artifact_id": original.artifact_id})
    )
    reader = service._source(original.artifact_id)
    assert isinstance(reader, _DownloadedStructuredModel)
    with pytest.raises(BoundaryError, match="local_sampled_model_required_for_compatibility"):
        service._native_publication_store(reader, original)
    assert not service._path().exists()
