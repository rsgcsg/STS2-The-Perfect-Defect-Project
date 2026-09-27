"""Local evidence remains local while the canonical selector reads its verified bytes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from test_local_recording_preview import PRODUCER, _fixture

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.local_verified_bundle import verified_local_bundle
from spireagent.workbench.local_recording_preview import preview_artifact
from stpd.fullrun.decision_cache import VerifiedSourceCache
from stpd.fullrun.decision_dataset import SelectionRules
from stpd.fullrun.decision_store import (
    load,
    preview,
    preview_union,
    publish,
    publish_union,
)
from stpd.fullrun.platform_bundle3 import PlatformBundle3SourceAdapter


def _changed(store, source: Manifest, **parameters: object) -> Manifest:
    changed = Manifest(
        "evidence", PRODUCER, payloads=source.payloads,
        parameters=FrozenObject.of({**source.parameters.value(), **parameters}),
    )
    store.publish(changed)
    return changed


def test_local_canonical_preview_publish_reload_keeps_source_identity(tmp_path: Path) -> None:
    _, artifact_id, store = _fixture(tmp_path, canonical=True)
    source = store.get_manifest(artifact_id)
    rules = SelectionRules()
    cache = VerifiedSourceCache(tmp_path / "private.sqlite", "test")
    selected = preview(store, (source,), rules, cache=cache)
    assert len(selected.records) == 2
    assert cache.misses == 1
    assert preview(store, (source,), rules, cache=cache) == selected
    assert cache.hits == 1
    published = publish(store, (source,), rules, PRODUCER, selected.logical_id, cache=cache)
    assert published.parents[0].artifact_id == artifact_id
    assert store.get_manifest(artifact_id) == source
    assert load(store, published.artifact_id, cache=cache)[1] == selected


def test_labels_only_do_not_substitute_for_canonical_decisions(tmp_path: Path) -> None:
    _, artifact_id, store = _fixture(tmp_path)
    source = store.get_manifest(artifact_id)
    rules = SelectionRules()
    selected = preview(store, (source,), rules)
    assert len(selected.records) == 0
    with pytest.raises(BoundaryError, match="empty_selection"):
        publish(store, (source,), rules, PRODUCER, selected.logical_id)


@pytest.mark.parametrize("change", [
    {"schema": "stpd/unknown-source-v1"},
    {"disposition": "verified"},
    {"hub_receipt": {"fake": True}},
    {"human_origin_attested": False},
    {"manifest_sha256": "0" * 64},
    {"transfer_manifest_sha256": "0" * 64},
])
def test_local_metadata_cannot_be_promoted_or_forged(tmp_path: Path, change: dict) -> None:
    _, artifact_id, store = _fixture(tmp_path, canonical=True)
    source = _changed(store, store.get_manifest(artifact_id), **change)
    with pytest.raises(BoundaryError):
        preview(store, (source,), SelectionRules())


def test_cache_hit_rechecks_transfer_before_publish(tmp_path: Path, monkeypatch) -> None:
    _, artifact_id, store = _fixture(tmp_path, canonical=True)
    source = store.get_manifest(artifact_id)
    rules = SelectionRules()
    cache = VerifiedSourceCache(tmp_path / "private.sqlite", "test")
    selected = preview(store, (source,), rules, cache=cache)
    published = publish(store, (source,), rules, PRODUCER, selected.logical_id, cache=cache)
    original = store.read_payload

    def damaged(payload):
        if payload.role == "transfer":
            raise BoundaryError("test", "transfer_unavailable")
        return original(payload)

    monkeypatch.setattr(store, "read_payload", damaged)
    with pytest.raises(BoundaryError, match="transfer_unavailable"):
        publish(store, (source,), rules, PRODUCER, selected.logical_id, cache=cache)
    with pytest.raises(BoundaryError, match="transfer_unavailable"):
        load(store, published.artifact_id, cache=cache)
    assert set(store.manifest_ids()) == {artifact_id, published.artifact_id}


def test_union_cache_hit_rechecks_local_ancestor(tmp_path: Path, monkeypatch) -> None:
    _, artifact_id, store = _fixture(tmp_path, canonical=True)
    source = store.get_manifest(artifact_id)
    rules = SelectionRules()
    cache = VerifiedSourceCache(tmp_path / "private.sqlite", "test")
    selected = preview(store, (source,), rules, cache=cache)
    parent = publish(store, (source,), rules, PRODUCER, selected.logical_id, cache=cache)
    union = preview_union(store, (parent,), rules, cache=cache)
    original = store.read_payload

    def damaged(payload):
        if payload.role == "transfer":
            raise BoundaryError("test", "transfer_unavailable")
        return original(payload)

    monkeypatch.setattr(store, "read_payload", damaged)
    with pytest.raises(BoundaryError, match="transfer_unavailable"):
        publish_union(store, (parent,), rules, PRODUCER, union.logical_id, cache=cache)


def test_workbench_preview_and_warm_source_do_not_buffer_archive(
    tmp_path: Path, monkeypatch,
) -> None:
    workspace, artifact_id, store = _fixture(tmp_path, canonical=True)
    source = store.get_manifest(artifact_id)
    rules = SelectionRules()
    cache = VerifiedSourceCache(tmp_path / "private.sqlite", "test")
    selected = preview(store, (source,), rules, cache=cache)
    original = Path.read_bytes

    def no_archive_read(path: Path) -> bytes:
        if path.name == "bundle.tar.gz":
            pytest.fail("warm verification must stream the archive")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", no_archive_read)
    assert preview_artifact(workspace, artifact_id)["canonical_decisions"] == 2
    assert preview(store, (source,), rules, cache=cache) == selected
    publish(store, (source,), rules, PRODUCER, selected.logical_id, cache=cache)


def test_cold_projection_reuses_verified_directory_and_rejects_rebinding(
    tmp_path: Path, monkeypatch,
) -> None:
    _, artifact_id, store = _fixture(tmp_path, canonical=True)
    source = store.get_manifest(artifact_id)
    import spireagent.local_verified_bundle as local_bundle
    import stpd.fullrun.platform_bundle3 as adapter

    unpack = local_bundle.unpack
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return unpack(*args, **kwargs)

    monkeypatch.setattr(local_bundle, "unpack", counted)
    monkeypatch.setattr(adapter, "_extract", lambda *_: pytest.fail("duplicate extraction"))
    assert len(preview(store, (source,), SelectionRules()).records) == 2
    assert calls == 1
    with verified_local_bundle(store, source) as verified:
        projection = PlatformBundle3SourceAdapter()._project_verified_local(verified)
        assert len(projection.transitions) == 2
        raw_file = verified.directory / "raw/recording-manifest.json"
        raw_file.write_bytes(raw_file.read_bytes() + b" ")
        with pytest.raises(BoundaryError, match="bundle_identity_mismatch"):
            PlatformBundle3SourceAdapter()._project_verified_local(verified)
        with pytest.raises(BoundaryError, match="bundle_identity_mismatch"):
            PlatformBundle3SourceAdapter()._project_verified_local(
                replace(verified, bundle=object())
            )
