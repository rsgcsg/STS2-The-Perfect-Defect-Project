from __future__ import annotations

from pathlib import Path

import pytest
from test_artifact_store_v1 import PRODUCER

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.blobs import StoreError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore


def test_immutable_completion_and_manifest_event_recovery(tmp_path: Path) -> None:
    class FailIndexOnce(LocalBlobStore):
        failed = False

        def put_if_absent(self, key: str, data: bytes) -> bool:
            if key.startswith("run-events/") and not self.failed:
                self.failed = True
                raise StoreError("injected_index_write_failure")
            return super().put_if_absent(key, data)

    blobs = FailIndexOnce(tmp_path)
    store = ManifestArtifactStore(blobs)
    reporter = ObjectStoreRunReporter(store, blobs)
    run = Manifest("run", PRODUCER)
    store.publish(run)
    event = Manifest(
        "run_event",
        PRODUCER,
        (Parent("run", run.artifact_id),),
        parameters=FrozenObject.of({"schema": "stpd/run-event-v1", "step": 1}),
    )
    with pytest.raises(StoreError):
        reporter.emit(event)
    assert reporter.events(run.artifact_id) == (event,)
    assert reporter.emit(event) == event.artifact_id
    assert reporter.completed(run.artifact_id) is None
    result = Manifest(
        "run_result",
        PRODUCER,
        (Parent("run", run.artifact_id),),
        parameters=FrozenObject.of({"schema": "stpd/run-result-v1"}),
    )
    assert reporter.complete(result) == result.artifact_id
    assert reporter.complete(result) == result.artifact_id
    assert reporter.completed(run.artifact_id) == result
    conflicting = Manifest(
        "run_result",
        PRODUCER,
        result.parents,
        parameters=FrozenObject.of({"schema": "stpd/run-result-v1", "different": True}),
    )
    with pytest.raises(StoreError, match="collision"):
        reporter.complete(conflicting)
    assert reporter.completed(run.artifact_id) == result


def test_completion_publication_race_selects_one_immutable_result(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    store = ManifestArtifactStore(LocalBlobStore(tmp_path))
    reporter = ObjectStoreRunReporter(store, store.blobs)
    run = Manifest("run", PRODUCER)
    store.publish(run)
    results = [
        Manifest(
            "run_result",
            PRODUCER,
            (Parent("run", run.artifact_id),),
            parameters=FrozenObject.of({"schema": "stpd/run-result-v1", "attempt": i}),
        )
        for i in range(8)
    ]

    def publish(result):
        try:
            return reporter.complete(result)
        except StoreError as error:
            assert error.code == "immutable_key_collision"
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = [value for value in pool.map(publish, results) if value is not None]
    assert len(winners) == 1
    assert reporter.completed(run.artifact_id).artifact_id == winners[0]


def indexed_fixture(tmp_path: Path, blobs_type=LocalBlobStore):
    blobs = blobs_type(tmp_path)
    store = ManifestArtifactStore(blobs)
    run = Manifest("run", PRODUCER,
                   parameters=FrozenObject.of({"event_index": "run-events-v1"}))
    store.publish(run)
    event = Manifest("run_event", PRODUCER, (Parent("run", run.artifact_id),),
                     parameters=FrozenObject.of({"schema": "stpd/run-event-v1", "step": 1}))
    return store, ObjectStoreRunReporter(store, blobs), run, event


def test_declared_index_never_scans_unrelated_manifests(tmp_path: Path) -> None:
    store, reporter, run, event = indexed_fixture(tmp_path)
    reporter.emit(event)
    # This unrelated unreadable object made the old full scan fail. It must not
    # affect the declared Run, whose only lookup directory is its own index.
    store.blobs.put_if_absent("manifests/" + "a" * 64 + ".json", b"corrupt unrelated manifest")
    assert reporter.events(run.artifact_id) == (event,)


def test_index_publication_barrier_reports_pending_then_visible(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    indexed, release = Event(), Event()

    class PublicationBarrier(LocalBlobStore):
        def put_if_absent(self, key: str, data: bytes) -> bool:
            created = super().put_if_absent(key, data)
            if key.startswith("run-events/"):
                indexed.set()
                assert release.wait(5), "test publication barrier timed out"
            return created

    store, reporter, run, event = indexed_fixture(tmp_path, PublicationBarrier)
    with ThreadPoolExecutor(max_workers=1) as pool:
        publication = pool.submit(reporter.emit, event)
        try:
            assert indexed.wait(5)
            with pytest.raises(BoundaryError, match="event_publication_pending"):
                reporter.events(run.artifact_id)
            with pytest.raises(StoreError, match="object_not_found"):
                store.get_manifest(event.artifact_id)
            assert not publication.done()  # The observer did not end/restart the active writer.
        finally:
            release.set()
        assert publication.result(timeout=5) == event.artifact_id
    assert reporter.events(run.artifact_id) == (event,)


def test_crash_after_index_is_explicit_pending_without_losing_the_event(tmp_path: Path) -> None:
    class CrashAfterIndex(LocalBlobStore):
        def put_if_absent(self, key: str, data: bytes) -> bool:
            created = super().put_if_absent(key, data)
            if key.startswith("run-events/"):
                raise StoreError("injected_process_crash_after_durable_index")
            return created

    store, reporter, run, event = indexed_fixture(tmp_path, CrashAfterIndex)
    with pytest.raises(StoreError, match="injected_process_crash"):
        reporter.emit(event)
    reopened = ManifestArtifactStore(LocalBlobStore(tmp_path, create=False))
    observer = ObjectStoreRunReporter(reopened, reopened.blobs)
    with pytest.raises(BoundaryError, match="event_publication_pending"):
        observer.events(run.artifact_id)
    assert reopened.blobs.get(f"run-events/{run.artifact_id}/{event.artifact_id}.json") == (
        event.to_bytes())
    # No implicit retry or manifest invented by the read side. An explicit
    # identical publication can finish the originally indexed immutable event.
    assert observer.emit(event) == event.artifact_id
    assert observer.events(run.artifact_id) == (event,)


@pytest.mark.parametrize("fault", ["canonical", "identity", "foreign_run", "kind", "schema"])
def test_declared_index_rejects_invalid_bytes_or_binding(tmp_path: Path, fault: str) -> None:
    from dataclasses import replace

    store, reporter, run, event = indexed_fixture(tmp_path)
    bad = event
    if fault == "foreign_run":
        other = Manifest("run", PRODUCER)
        store.publish(other)
        bad = replace(event, parents=(Parent("run", other.artifact_id),))
    elif fault == "kind":
        bad = replace(event, kind="checkpoint")
    elif fault == "schema":
        bad = replace(event, parameters=FrozenObject.of({"schema": "future", "step": 1}))
    raw = bad.to_bytes() + (b" " if fault == "canonical" else b"")
    identity = "b" * 64 if fault == "identity" else bad.artifact_id
    store.blobs.put_if_absent(f"run-events/{run.artifact_id}/{identity}.json", raw)
    with pytest.raises(BoundaryError) as caught:
        reporter.events(run.artifact_id)
    assert caught.value.code != "event_publication_pending"


def test_index_failure_before_manifest_leaves_no_unindexed_event(tmp_path: Path) -> None:
    class IndexWriteFailure(LocalBlobStore):
        def put_if_absent(self, key: str, data: bytes) -> bool:
            if key.startswith("run-events/"):
                raise StoreError("injected_index_write_failure")
            return super().put_if_absent(key, data)

    store, reporter, run, event = indexed_fixture(tmp_path, IndexWriteFailure)
    with pytest.raises(StoreError, match="injected_index_write_failure"):
        reporter.emit(event)
    assert reporter.events(run.artifact_id) == ()
    with pytest.raises(StoreError, match="object_not_found"):
        store.get_manifest(event.artifact_id)
