"""P1 request-local manifest reuse saves GETs; never caches mutable slots."""
from collections import Counter

import pytest
from test_artifact_store_v1 import PRODUCER

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import FrozenObject
from spireagent.storage.blobs import StoreError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore


class CountReads(LocalBlobStore):
    def __init__(self, root):
        super().__init__(root)
        self.reads = Counter()

    def get(self, key):
        self.reads[key] += 1
        return super().get(key)


def test_cached_reporter_keeps_exact_manifests_and_synchronous_publication(tmp_path):
    blobs = CountReads(tmp_path)
    original = ManifestArtifactStore(blobs)
    run = Manifest('run', PRODUCER, parameters=FrozenObject.of({'event_index': 'run-events-v1'}))
    original.publish(run)
    cached = ManifestArtifactStore(blobs, cache_manifests=True)
    reporter = ObjectStoreRunReporter(cached, blobs)
    for step in (1, 2):
        event = Manifest('run_event', PRODUCER, (Parent('run', run.artifact_id),),
                         parameters=FrozenObject.of({'schema': 'stpd/run-event-v1',
                                                     'step': step, 'kind': 'progress'}))
        reporter.emit(event)
        # emit returns only after both original index and manifest exist.
        assert original.get_manifest(event.artifact_id) == event
        indexed = blobs.get(f'run-events/{run.artifact_id}/{event.artifact_id}.json')
        assert indexed == event.to_bytes()
    assert blobs.reads[f'manifests/{run.artifact_id}.json'] == 1
    assert [e.parameters.value()['step'] for e in reporter.events(run.artifact_id)] == [1, 2]
    assert reporter.completed(run.artifact_id) is None
    assert reporter.completed(run.artifact_id) is None
    assert blobs.reads[f'run-completions/{run.artifact_id}.json'] == 2
    assert cached.get_manifest(run.artifact_id) == run
    # Deliberate in-memory damage is still caught by Manifest.from_bytes on hit.
    cached._manifest_cache[run.artifact_id] = b'{}'
    with pytest.raises((ValueError, StoreError)):
        cached.get_manifest(run.artifact_id)


def test_persistent_inputs_are_hashed_and_writes_slots_remain_original(tmp_path):
    import io

    from spireagent.storage.input_cache import InputCacheStore

    remote_blobs = CountReads(tmp_path / 'remote')
    remote = ManifestArtifactStore(remote_blobs)
    payload = remote.put_payload('data', io.BytesIO(b'input bytes'))
    cache_path = tmp_path / 'cache'
    one = InputCacheStore(remote, cache_path)
    assert b''.join(one.read_payload(payload)) == b'input bytes'
    calls = remote_blobs.reads.copy()
    two = InputCacheStore(remote, cache_path)
    assert b''.join(two.read_payload(payload)) == b'input bytes'
    assert remote_blobs.reads == calls
    new = two.put_payload('output', io.BytesIO(b'output bytes'))
    assert b''.join(remote.read_payload(new)) == b'output bytes'
    assert two.blobs is remote_blobs
    chunk = next((cache_path / 'objects/sha256').glob('*'))
    chunk.write_bytes(b'bad bytes')
    with pytest.raises(StoreError, match='payload_chunk_integrity_failure'):
        b''.join(InputCacheStore(remote, cache_path).read_payload(payload))
    assert remote_blobs.reads == calls + Counter({
        f'payload-indexes/v1/{new.sha256}.json': 1,
        f'objects/sha256/{new.sha256}': 1})
