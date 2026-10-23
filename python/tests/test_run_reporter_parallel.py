"""Concurrent event reads preserve the exhaustive publication barrier and byte checks."""

from __future__ import annotations

from threading import Barrier, Lock

import pytest
from test_artifact_store_v1 import PRODUCER
from test_run_reporter import indexed_fixture

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.blobs import StoreError
from spireagent.storage.run_reporter import ObjectStoreRunReporter


def test_parallel_reads_bound_actual_overlap_and_keep_identical_events(tmp_path):
    store, writer, run, first = indexed_fixture(tmp_path)
    events = [first, *[Manifest("run_event", PRODUCER, (Parent("run", run.artifact_id),),
                               parameters=FrozenObject.of(
                                   {"schema": "stpd/run-event-v1", "step": i}))
                      for i in range(2, 9)]]
    for event in reversed(events):
        writer.emit(event)
    lock, barrier = Lock(), Barrier(3)
    active = peak = admitted = 0
    get = store.blobs.get

    def observed_get(key):
        nonlocal active, peak, admitted
        if not key.startswith("run-events/"):
            return get(key)
        with lock:
            active += 1
            peak = max(peak, active)
            admitted += 1
            first_group = admitted <= 3
        try:
            if first_group:
                barrier.wait(timeout=5)
            return get(key)
        finally:
            with lock:
                active -= 1

    store.blobs.get = observed_get
    reader = ObjectStoreRunReporter(store, store.blobs, cache_events=True, read_concurrency=3)
    assert reader.events(run.artifact_id) == tuple(events)
    assert peak == 3 and admitted == 8
    assert reader.events(run.artifact_id) == tuple(events)
    assert admitted == 8  # Only fully checked immutable events are retained.


def test_parallel_reader_retains_pending_publication_instead_of_empty_history(tmp_path):
    store, writer, run, first = indexed_fixture(tmp_path)
    writer.emit(first)
    second = Manifest("run_event", PRODUCER, first.parents,
                      parameters=FrozenObject.of({"schema": "stpd/run-event-v1", "step": 2}))
    store.blobs.put_if_absent(f"run-events/{run.artifact_id}/{second.artifact_id}.json",
                             second.to_bytes())
    reader = ObjectStoreRunReporter(store, store.blobs, read_concurrency=3)
    with pytest.raises(BoundaryError, match="event_publication_pending"):
        reader.events(run.artifact_id)
    store.publish(second)
    assert reader.events(run.artifact_id) == (first, second)


def test_parallel_reader_still_rejects_different_published_bytes(tmp_path):
    store, writer, run, first = indexed_fixture(tmp_path)
    writer.emit(first)
    second = Manifest("run_event", PRODUCER, first.parents,
                      parameters=FrozenObject.of({"schema": "stpd/run-event-v1", "step": 2}))
    writer.emit(second)
    get = store.blobs.get

    def corrupt(key):
        if key == f"manifests/{second.artifact_id}.json":
            return first.to_bytes()
        return get(key)

    store.blobs.get = corrupt
    with pytest.raises((BoundaryError, StoreError)):
        ObjectStoreRunReporter(store, store.blobs, read_concurrency=3).events(run.artifact_id)


@pytest.mark.parametrize("limit", [0, 17, True, 1.5])
def test_event_read_concurrency_is_explicit_and_bounded(tmp_path, limit):
    store, _, _, _ = indexed_fixture(tmp_path)
    with pytest.raises(BoundaryError, match="invalid_read_concurrency"):
        ObjectStoreRunReporter(store, store.blobs, read_concurrency=limit)
