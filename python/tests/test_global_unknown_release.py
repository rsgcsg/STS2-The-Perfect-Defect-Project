"""The real publication-unknown fence joins explicit Native release, without cloud writes."""
# ruff: noqa: F811 -- shared imported fixtures.
from __future__ import annotations

import json

import pytest
from test_native_unknown_release import (  # noqa: F401
    absent_os,
    native_app,
    native_caps,
    native_endpoint,
    prepare_body,
    prepared,
)

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.blobs import StoreError
from spireagent.storage.local import LocalBlobStore
from spireagent.workbench import local_recording_import


def closed_with_unknown_publication(app):
    ready = prepared(app)
    service = app.local_environment
    run_id = "run-" + "a" * 36
    service.native_record["actor"] = {"run_id": run_id}
    service._save_native()
    record, client, first = service.begin_native_finish(ready["environment_ref"])
    assert first
    assert service.close_native_after_actor(record, client, {"status": "stopped"})["phase"] == (
        "closed")
    importer = app.local_recording_import
    importer.operation = {
        "schema": local_recording_import.SCHEMA, "recording_type": "p1_global",
        "action": "global-evidence", "operation_id": "e" * 32,
        "status": "publication_unknown", "run_id": run_id,
        "error_code": "typed_global_verification_failed", "artifact_id": None,
        "_global_context": {"expected": {"run_id": run_id}}}
    importer._save()  # Actual owner serializer, no forged release/confirmation.
    return app.native_unknown_preview()["expected"]


def test_explicit_seal_preserves_publisher_bytes_and_only_allows_a_different_run(
    native_app, absent_os, monkeypatch,
):
    app, *_ = native_app
    expected = closed_with_unknown_publication(app)
    importer = app.local_recording_import
    original = importer.path.read_bytes()
    with pytest.raises(BoundaryError, match="original_intent_reconciliation_required"):
        app.prepare_native_episode(prepare_body("b" * 32))
    result = app.seal_native_unknown(expected)
    assert result["expected"] == expected and result["result"] == "unknown"
    assert importer.operation["status"] == "publication_unknown"
    assert importer.path.read_bytes() == original
    store, _generation = app.local_environment._native_operation_store()
    archive_raw = store.get(result["publication"]["key"])
    archived = json.loads(archive_raw)
    assert archived["original_utf8"].encode() == original
    assert archived["result"] == "unknown"
    assert importer.global_unknown_released() is True
    with pytest.raises(BoundaryError, match="retired_unknown_publication_no_retry"):
        importer.start_global_evidence({"expected": {"run_id": expected["expected_run_id"]}})
    offered = []
    def complete_new_publication():
        with importer.lock:
            offered.append(importer.operation["run_id"])
            importer.operation["status"] = "completed"
            importer._save()

    monkeypatch.setattr(importer, "_run_global_evidence", complete_new_publication)
    monkeypatch.setattr(local_recording_import, "source_identity",
                        lambda _root: Producer("fixture", "a" * 40, "b" * 64))
    new_run = "run-" + "b" * 36
    importer.start_global_evidence({"expected": {"run_id": new_run},
                                    "model_artifact_id": "c" * 64})
    importer.thread.join(timeout=3)
    assert offered == [new_run]
    assert store.get(result["publication"]["key"]) == archive_raw
    with pytest.raises(BoundaryError, match="retired_unknown_publication_no_retry"):
        importer.start_global_evidence({"expected": {"run_id": expected["expected_run_id"]}})
    # Application wiring on a fresh owner uses the persisted retirement lookup,
    # even though the live pointer now names a successful different publication.
    from spireagent.workbench.local_environment import LocalEnvironmentService

    reopened = local_recording_import.LocalRecordingImporter(app.config, app.local_recordings)
    reopened.global_run_retired = LocalEnvironmentService(app.config).global_run_retired
    assert reopened.operation["status"] == "completed"
    with pytest.raises(BoundaryError, match="retired_unknown_publication_no_retry"):
        reopened.start_global_evidence({"expected": {"run_id": expected["expected_run_id"]}})
    assert offered == [new_run]


@pytest.mark.parametrize("stage", ["/publication/", "/confirmations/",
                                   "retired-global-publications/"])
def test_publisher_or_final_confirmation_write_after_publication_keeps_closed_world_blocked(
    native_app, absent_os, monkeypatch, stage,
):
    app, *_ = native_app
    expected = closed_with_unknown_publication(app)
    importer = app.local_recording_import
    original = importer.path.read_bytes()
    put = LocalBlobStore.put_if_absent

    def failed_after_write(store, key, raw):
        result = put(store, key, raw)
        if stage in key:
            raise StoreError("test_failure_after_publication")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(LocalBlobStore, "put_if_absent", failed_after_write)
        with pytest.raises(StoreError, match="failure_after_publication"):
            app.seal_native_unknown(expected)
    assert app.native_episode_status()["phase"] == "closed"
    assert app.local_environment.native_unknown_release() is None
    assert importer.global_unknown_released() is False
    assert importer.path.read_bytes() == original
    with pytest.raises(BoundaryError, match="original_intent_reconciliation_required"):
        app.prepare_native_episode(prepare_body("b" * 32))
    assert app.seal_native_unknown(expected)["released"] is True


def test_publisher_restart_does_not_adopt_archive_without_explicit_confirmation(
    native_app, absent_os,
):
    app, *_ = native_app
    expected = closed_with_unknown_publication(app)
    app.seal_native_unknown(expected)
    reopened = local_recording_import.LocalRecordingImporter(app.config, app.local_recordings)
    assert reopened.operation["status"] == "publication_unknown"
    assert reopened.global_unknown_released() is False
    with pytest.raises(BoundaryError, match="original_intent_reconciliation_required"):
        reopened.require_publication_admission()


def test_another_native_actors_close_cannot_release_this_publication(
    native_app, absent_os,
):
    app, *_ = native_app
    closed_with_unknown_publication(app)
    importer = app.local_recording_import
    other = "run-" + "b" * 36
    importer.operation["run_id"] = other
    importer.operation["_global_context"]["expected"]["run_id"] = other
    importer._save()
    original = importer.path.read_bytes()
    expected = app.native_unknown_preview()["expected"]
    with pytest.raises(BoundaryError, match="publication_native_run_mismatch"):
        app.seal_native_unknown(expected)
    assert app.local_environment.native_unknown_release() is None
    assert importer.global_unknown_released() is False
    assert importer.path.read_bytes() == original
