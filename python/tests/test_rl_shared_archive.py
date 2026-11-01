"""Normal immutable producers/readers with local hot transport and archive readback.

Synthetic trajectory source bytes and an independent local archive isolate the
transport contract; these tests do not claim gameplay or cloud availability.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_p1_trajectory import episode

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError
from spireagent.rl_exchange import Exchange
from spireagent.storage.async_archive import Archive
from spireagent.storage.blobs import StoreError
from spireagent.storage.config import open_peer
from spireagent.workbench.managed_trajectory_store import publish_shard, read_shard
from stpd.rl.learner import Learner


def peers(tmp_path):
    archive = {"kind": "local", "directory": str(tmp_path / "archive")}
    binding = {"kind": "local", "directory": str(tmp_path / "shared"), "archive": archive}
    return binding, open_peer(binding), open_peer(archive)


def publish_model(store):
    payload = store.put_bytes("weights", b"synthetic immutable model")
    model = Manifest("model", PRODUCER, payloads=(payload,))
    store.publish(model)
    return model


def test_hot_producer_typed_reader_and_background_archive_keep_original_bytes(
        tmp_path, monkeypatch):
    binding, hot, archive = peers(tmp_path)
    blocked, entered = threading.Event(), threading.Event()
    original_put = hot.archive.target.blobs.put_if_absent

    def wait_on_archive(key, raw):
        entered.set()
        assert blocked.wait(10)
        return original_put(key, raw)

    monkeypatch.setattr(hot.archive.target.blobs, "put_if_absent", wait_on_archive)
    model = publish_model(hot)
    value = episode(model.artifact_id)
    value["identity"]["model_sha256"] = model.payload("weights").sha256
    shard = publish_shard(hot, [value], PRODUCER, scratch=tmp_path)
    exchange = Exchange(binding, "shared-contract")
    exchange.publish_model({"run": exchange.run, "version": 0,
        "model_artifact_id": model.artifact_id, "model_sha256": model.payload("weights").sha256})
    record = exchange.publish_shard("actor", 0, {"shard_artifact_id": shard.artifact_id,
        "run_ids": [value["run_id"]], "model_artifact_id": model.artifact_id, "model_version": 0})
    assert entered.wait(2)
    assert list(read_shard(exchange.store, shard.artifact_id, scratch=tmp_path)) == [value]
    assert exchange.shard_record(exchange.shard_keys()[0]) == record
    assert exchange.archive_status()["pending"] > 0
    assert not archive.blobs.keys(f"{exchange.prefix}shards/")
    blocked.set()
    assert exchange.drain_archive(10)["full_readback"]
    assert list(read_shard(archive, shard.artifact_id, scratch=tmp_path)) == [value]
    remote = Exchange(binding["archive"], exchange.run)
    assert remote.shard_record(remote.shard_keys()[0]) == record
    assert hot.archive.status()["pending"] == 0


def test_archive_failure_preserves_hot_unknown_and_resumes_without_new_offer(tmp_path, monkeypatch):
    binding, hot, archive = peers(tmp_path)
    exchange = Exchange(binding, "unknown-contract")
    original_put = hot.archive.target.blobs.put_if_absent

    def unavailable(*_):
        raise StoreError("s3_put_failed")

    monkeypatch.setattr(hot.archive.target.blobs, "put_if_absent", unavailable)
    record = exchange.publish_shard("actor", 0, {"shard_artifact_id": None,
        "run_ids": [], "model_version": 0, "games": [{"status": "unknown", "run_id": "original"}]})
    with pytest.raises(StoreError, match="shared_archive_incomplete"):
        exchange.drain_archive(.02)
    assert exchange.shard_record(exchange.shard_keys()[0]) == record
    assert exchange.archive_status()["last_error"]["code"] == "s3_put_failed"
    monkeypatch.setattr(hot.archive.target.blobs, "put_if_absent", original_put)
    assert exchange.drain_archive(5)["full_readback"]
    assert len(archive.blobs.keys(f"{exchange.prefix}shards/")) == 1
    assert Exchange(binding["archive"], exchange.run).shard_keys() == exchange.shard_keys()


def test_reconcile_recovers_unqueued_publication_and_keeps_failure_original(tmp_path):
    binding, hot, archive = peers(tmp_path)
    # A process can die after durable local publication but before its outbox offer.
    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import ManifestArtifactStore

    plain = ManifestArtifactStore(LocalBlobStore(Path(binding["directory"])))
    payload = plain.put_bytes("failure", b"original failed prefix, never a new game")
    failure = Manifest("analysis", PRODUCER, payloads=(payload,))
    plain.publish(failure)
    resumed = Archive(Path(binding["directory"]), binding["archive"])
    resumed.reconcile("unknown-contract")
    assert resumed.drain(timeout=5, owned=False)["full_readback"]
    assert archive.get_manifest(failure.artifact_id) == failure
    assert archive.bytes(payload) == b"original failed prefix, never a new game"


def test_hot_store_cold_reads_use_only_the_explicit_archive(tmp_path):
    binding, hot, archive = peers(tmp_path)
    model = publish_model(archive)
    assert hot.get_manifest(model.artifact_id) == model
    assert hot.bytes(model.payload("weights")) == b"synthetic immutable model"
    assert hot.archive.status(owned=True)["planned"] == 0  # Reading starts no archive worker.


def test_existing_exchange_seed_is_exact_and_archive_binding_cannot_drift(tmp_path):
    binding, hot, archive = peers(tmp_path)
    old = Exchange(binding["archive"], "old-run")
    event = old.publish_event("original", updates=17)
    original_key = archive.blobs.keys(f"{old.prefix}learner-events/")[0]
    seeded = hot.archive.seed_exchange(old.run)
    assert seeded["records"] == 1 and seeded["full_readback"]
    assert Exchange(binding, old.run).latest_event() == event
    assert hot.blobs.get(original_key) == archive.blobs.get(original_key)
    with pytest.raises(StoreError, match="immutable_key_collision"):
        open_peer({**binding, "archive": {"kind": "local", "directory": str(tmp_path / "wrong")}})


@pytest.mark.parametrize("change", [
    {"archive": {"kind": "local", "directory": "SAME"}},
    {"archive": {"kind": "local", "directory": "other", "archive": {}}},
])
def test_shared_peer_cannot_archive_into_itself_or_chain_archives(tmp_path, change):
    binding = {"kind": "local", "directory": str(tmp_path / "shared"), **change}
    if binding["archive"].get("directory") == "SAME":
        binding["archive"]["directory"] = binding["directory"]
    with pytest.raises(BoundaryError, match="distinct_frozen_shared_archive"):
        open_peer(binding)


def test_completed_read_collection_runs_off_owner_and_keeps_FIFO(monkeypatch):
    from stpd.rl import learner as module

    learner = Learner.__new__(Learner)
    learner.config = SimpleNamespace(reader_backend="process", background_io=True)
    learner.reader = ThreadPoolExecutor(max_workers=2)
    learner.read_completion = ThreadPoolExecutor(max_workers=1)
    gate = threading.Event()
    owner, decoded = threading.get_ident(), []

    def read(key):
        if key == "first":
            assert gate.wait(5)
        return [{"key": key}]

    def decode(rows):
        decoded.append((rows[0]["key"], threading.get_ident()))
        return rows

    monkeypatch.setattr(module, "process_read", read)
    learner._read_request = lambda key: key
    learner._read_result = decode
    first, second = learner._submit_read("first"), learner._submit_read("second")
    time.sleep(.02)
    assert not first.done() and not second.done()
    gate.set()
    assert first.result(timeout=5) == [{"key": "first"}]
    assert second.result(timeout=5) == [{"key": "second"}]
    assert [key for key, _ in decoded] == ["first", "second"]
    assert all(thread != owner for _, thread in decoded)
    learner.reader.shutdown()
    learner.read_completion.shutdown()
