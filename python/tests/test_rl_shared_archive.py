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


def test_input_manifest_and_both_exchange_indexes_share_one_full_dependency_readback(
        tmp_path, monkeypatch):
    import spireagent.storage.async_archive as module

    offer = Archive.offer

    def queued(archive, key, raw, *, start=True):
        return offer(archive, key, raw, start=False)

    # Control scheduling only; producers, CAS, durable outbox and target reads
    # remain the actual implementations below.
    monkeypatch.setattr(Archive, "offer", queued)
    binding, hot, remote = peers(tmp_path)
    exchange = Exchange(binding, "input-copy")
    record = exchange.append_input(
        {"operation": "episode_open", "actor": "actor", "episode": "episode"},
        {"admitted": True}, sequence=1, previous=None, producer=PRODUCER)
    copies = []
    original = module.copy_artifact

    def counted(source, target, artifact, **kwargs):
        copies.append(artifact)
        return original(source, target, artifact, **kwargs)

    monkeypatch.setattr(module, "copy_artifact", counted)
    assert exchange.drain_archive(10)["full_readback"]
    assert copies == [record["input_artifact_id"]]
    restored = Exchange(binding["archive"], exchange.run)
    receipt = restored.input_receipt(
        {"operation": "episode_open", "actor": "actor", "episode": "episode"})
    assert receipt["response"] == {"admitted": True}
    assert remote.get_manifest(record["input_artifact_id"]) == hot.get_manifest(
        record["input_artifact_id"])


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


@pytest.mark.parametrize("run", ["../escape", "UPPER", "a" * 64])
def test_exchange_and_archive_seed_reject_invalid_run_before_peer_access(
    tmp_path, monkeypatch, run,
):
    binding, hot, archive = peers(tmp_path)
    monkeypatch.setattr(archive.blobs, "keys", lambda _: pytest.fail("read invalid namespace"))
    for open_namespace in (lambda: Exchange(binding, run), lambda: hot.archive.seed_exchange(run)):
        with pytest.raises(BoundaryError, match="stage=rl_exchange; code=invalid_run_name"):
            open_namespace()


def test_normal_decision_segments_and_tensorboard_binary_archive_without_JSON_parsing(tmp_path):
    import collections
    import json

    from spireagent.rl_tensorboard import TensorBoardMetrics
    from stpd.rl.ledger import DecisionLedger

    binding, hot, archive = peers(tmp_path)
    exchange = Exchange(binding, "normal-diagnostics")
    ledger = DecisionLedger(tmp_path / "decision-ledger.jsonl")
    ledger.write("first", updates=1)
    ledger.write("second", updates=2)
    learner = Learner.__new__(Learner)
    learner.ledger, learner.exchange = ledger, exchange
    learner.config = SimpleNamespace(background_io=False)
    learner.telemetry_pending = collections.deque()
    learner._publish_ledger()  # The real normal publisher creates a multi-record JSONL blob.
    ledger.close()
    metrics = TensorBoardMetrics(exchange, tmp_path / "metrics")
    metrics.scalars({"train/policy_loss": .25}, 2)
    metrics.publish()
    keys = hot.blobs.keys(exchange.prefix)
    decision_key = next(k for k in keys if "/decisions/" in k)
    assert [json.loads(line)["kind"] for line in hot.blobs.get(decision_key).splitlines()] == [
        "first", "second"]
    assert any("/tb/" in k for k in keys)
    assert exchange.drain_archive(10)["full_readback"]
    for key in keys:
        assert archive.blobs.get(key) == hot.blobs.get(key)
    assert hot.archive.status()["pending"] == 0


def test_archive_drain_checks_deadline_between_tasks_and_logs_safe_IO_failure(
        tmp_path, monkeypatch, capsys):
    from spireagent.storage import async_archive as module

    _, hot, _ = peers(tmp_path)
    archive = hot.archive
    for i in range(6):
        archive.offer(f"exchange/test/{i}.json", b"{}", start=False)
    clock, copied = [0.0], []
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    def bounded_one(identity):
        copied.append(identity)
        clock[0] += .02  # A task is not interrupted; its successor must respect the total deadline.

    monkeypatch.setattr(archive, "_copy", bounded_one)
    assert archive.step(deadline=.03) == 2 and len(copied) == 2

    def quota_failure(_):
        raise OSError("secret SDK/environment content must never reach diagnostics")

    monkeypatch.setattr(archive, "_copy", quota_failure)
    assert archive.step() == 0
    assert archive.last_error["error_type"] == "OSError"
    assert archive.last_error["stage"] == "shared_archive"
    diagnostic = capsys.readouterr().err
    assert '"error_type": "OSError"' in diagnostic and "secret SDK" not in diagnostic
    assert archive.status()["pending"] == 6


def test_owned_archive_drain_does_not_scan_or_copy_another_producers_intent(
        tmp_path, monkeypatch):
    root, target = tmp_path / "hot", {"kind": "local", "directory": str(tmp_path / "cold")}
    first, second = Archive(root, target), Archive(root, target)
    for owner, key in ((first, "exchange/test/first.json"),
                       (second, "exchange/test/second.json")):
        owner.source.blobs.put_if_absent(key, b"{}")
        owner.offer(key, b"{}", start=False)

    def no_global_scan(_):
        raise AssertionError("owned drain must not list the shared outbox")

    monkeypatch.setattr(first.queue, "keys", no_global_scan)
    assert first.drain(timeout=2, owned=True)["full_readback"]
    assert first.target.blobs.get("exchange/test/first.json") == b"{}"
    with pytest.raises(StoreError, match="object_not_found"):
        first.target.blobs.get("exchange/test/second.json")
    assert second.status(owned=True)["pending"] == 1
    assert second.drain(timeout=2, owned=True)["full_readback"]


@pytest.mark.parametrize("owned", [False, True])
def test_input_chain_is_archived_in_sequence_before_random_manifest_roots(
        tmp_path, monkeypatch, owned):
    from spireagent.storage import async_archive as module

    offer = Archive.offer
    monkeypatch.setattr(Archive, "offer",
                        lambda owner, key, raw, **_: offer(owner, key, raw, start=False))
    binding, hot, remote = peers(tmp_path)
    exchange = Exchange(binding, "archive-order")
    records, previous = [], None
    for sequence in range(1, 4):
        record = exchange.append_input(
            {"operation": "episode_open", "actor": f"actor{sequence}", "episode": "episode"},
            {"admitted": True}, sequence=sequence, previous=previous, producer=PRODUCER)
        records.append(record)
        previous = record["input_artifact_id"]
    archive, original_keys = hot.archive, hot.archive.queue.keys

    def reversed_listing(prefix):
        if prefix != "pending/":
            return original_keys(prefix)
        assert not owned, "owned service cannot list the global queue"
        return tuple(reversed(original_keys(prefix)))

    monkeypatch.setattr(archive.queue, "keys", reversed_listing)
    roots = []
    original_copy = module.copy_artifact

    def checked_copy(source, target, identity, **options):
        if identity in {r["input_artifact_id"] for r in records}:
            number = next(i for i, r in enumerate(records) if r["input_artifact_id"] == identity)
            if number:
                assert target.get_manifest(records[number - 1]["input_artifact_id"])
            roots.append(identity)
        return original_copy(source, target, identity, **options)

    monkeypatch.setattr(module, "copy_artifact", checked_copy)
    assert archive.step(owned=owned) > 0
    assert roots == [record["input_artifact_id"] for record in records]
    restored = Exchange(binding["archive"], exchange.run)
    assert restored.input_records(0) == records
    assert [restored.read_input(record) for record in records] == [
        exchange.read_input(record) for record in records]


def test_missing_pending_metadata_requires_a_completed_receipt(tmp_path, monkeypatch):
    _, hot, _ = peers(tmp_path)
    archive = hot.archive
    key = "exchange/test/original.json"
    archive.source.blobs.put_if_absent(key, b"{}")
    identity = archive.offer(key, b"{}", start=False)
    get = archive.queue.get
    raced = False

    def complete_during_metadata_read(path):
        nonlocal raced
        if path == f"pending/{identity}.json" and not raced:
            raced = True
            archive._copy(identity)
            raise StoreError("object_not_found")
        return get(path)

    monkeypatch.setattr(archive.queue, "get", complete_during_metadata_read)
    assert archive.step(owned=True) == 0 and archive.last_error is None
    assert archive.target.blobs.get(key) == b"{}"

    other = "exchange/test/missing.json"
    archive.source.blobs.put_if_absent(other, b"{}")
    missing = archive.offer(other, b"{}", start=False)
    (archive.queue.root / "pending" / f"{missing}.json").unlink()
    assert archive.step(owned=True) == 0
    assert archive.last_error["code"] == "object_not_found"
    assert archive.last_error["pending"] == missing


def test_archive_metadata_ordering_respects_deadline_before_copy(tmp_path, monkeypatch):
    from spireagent.storage import async_archive as module

    _, hot, _ = peers(tmp_path)
    archive = hot.archive
    for i in range(6):
        archive.offer(f"exchange/test/{i}.json", b"{}", start=False)
    clock, copied, get = [0.], [], archive.queue.get
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    def slow_metadata(key):
        clock[0] += .02
        return get(key)

    monkeypatch.setattr(archive.queue, "get", slow_metadata)
    monkeypatch.setattr(archive, "_copy", lambda identity: copied.append(identity))
    assert archive.step(deadline=.03) == 0 and not copied
    assert clock[0] == pytest.approx(.04)


def test_order_cache_avoids_rescanning_but_does_not_cache_source_or_completion(
        tmp_path, monkeypatch):
    _, hot, _ = peers(tmp_path)
    archive = hot.archive
    key, raw = "exchange/test/original.json", b'{"immutable":"original"}'
    archive.source.blobs.put_if_absent(key, raw)
    identity = archive.offer(key, raw, start=False)
    assert archive._ordered_pending([identity], None) == [identity]
    get, reads = archive.queue.get, []

    def counted(key):
        reads.append(key)
        return get(key)

    monkeypatch.setattr(archive.queue, "get", counted)
    assert archive._ordered_pending([identity], None) == [identity]
    assert reads == []  # Ordering reuses immutable metadata, never R2 receipts.
    path = archive.source.blobs._path(key)
    path.write_bytes(b'{"changed":true}')
    assert archive.step(owned=True) == 0
    assert archive.last_error["code"] == "archive_source_changed"
    assert reads == [f"pending/{identity}.json"]  # _copy reopens the real intent.
    assert archive.status(owned=False)["pending"] == 1
    path.write_bytes(raw)
    assert archive.step(owned=True) == 1
    assert archive.target.blobs.get(key) == raw
    assert archive.step(owned=True) == 0
    assert archive.status(owned=False)["full_readback"]


def test_capacity_reuses_immutable_intent_cost_but_observes_new_completion(
        tmp_path, monkeypatch):
    _, hot, _ = peers(tmp_path)
    archive = hot.archive
    key, raw = "exchange/test/capacity.json", b'{"size":"original"}'
    archive.source.blobs.put_if_absent(key, raw)
    identity = archive.offer(key, raw, start=False)
    get, reads = archive.source.blobs.get, []

    def counted(key):
        reads.append(key)
        return get(key)

    monkeypatch.setattr(archive.source.blobs, "get", counted)
    archive._refresh_capacity()
    assert archive.capacity_status()["pending_bytes_estimate"] == len(raw)
    assert reads == [key]
    archive._refresh_capacity()
    assert archive.capacity_status()["pending_bytes_estimate"] == len(raw)
    assert reads == [key]  # No second NFS read of unchanged immutable bytes.
    archive._copy(identity)
    archive._refresh_capacity()
    assert archive.capacity_status()["pending"] == 0
    assert archive.capacity_status()["pending_bytes_estimate"] == 0
    other, second = "exchange/test/new-capacity.json", b'{"new":"intent"}'
    archive.source.blobs.put_if_absent(other, second)
    archive.offer(other, second, start=False)
    archive._refresh_capacity()
    assert archive.capacity_status()["pending_bytes_estimate"] == len(second)
    assert reads[-1] == other


@pytest.mark.parametrize("key,code", [
    (None, "invalid_object_key"), (7, None),
    ("", "invalid_object_key"), ("a/../escape", "nonportable_object_key")])
def test_archive_ordering_retains_original_object_key_boundary(tmp_path, key, code):
    import json

    _, hot, _ = peers(tmp_path)
    archive = hot.archive
    identity = archive.offer("exchange/test/original.json", b"{}", start=False)
    path = archive.queue.root / "pending" / f"{identity}.json"
    task = json.loads(path.read_bytes())
    task["key"] = key
    path.write_text(json.dumps(task))
    assert archive.step(owned=True) == 0
    if code is None:
        assert archive.last_error["error_type"] == "TypeError"
    else:
        assert archive.last_error["code"] == code
    assert archive.last_error["pending"] == identity and path.exists()


def _archive_background_process(connection, root, target, label):
    """Actual immutable Store and OS locks; report metadata scans across processes."""
    archive = Archive(root, target)
    channel_lock = threading.Lock()

    def emit(value):
        with channel_lock:
            connection.send(value)

    original_keys, original_copy, original_step = (
        archive.queue.keys, archive._copy, archive.step)

    def keys(prefix):
        if prefix == "pending/":
            emit({"event": "global_scan"})
        return original_keys(prefix)

    def copy(identity):
        original_copy(identity)
        emit({"event": "copied", "identity": identity})

    def step(**options):
        result = original_step(**options)
        emit({"event": "step_complete", "owned": options.get("owned", False)})
        return result

    archive.queue.keys, archive._copy, archive.step = keys, copy, step
    key = f"exchange/test/{label}.json"
    archive.source.blobs.put_if_absent(key, b"{}")
    identity = archive.offer(key, b"{}")
    emit({"event": "ready", "identity": identity})
    while connection.recv() == "wake":
        archive.wake.set()


def test_background_global_scan_has_one_owner_and_process_death_preserves_orphan_service(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    root, target = tmp_path / "hot", {"kind": "local", "directory": str(tmp_path / "cold")}
    processes, channels = [], []
    observed = [[], []]

    def await_event(index, predicate):
        deadline = time.monotonic() + 15
        channel = channels[index]
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0 and channel.poll(remaining), observed[index]
            value = channel.recv()
            observed[index].append(value)
            if predicate(value):
                return value

    def start(label):
        parent, child = context.Pipe()
        process = context.Process(target=_archive_background_process,
            args=(child, root, target, label))
        processes.append(process)
        channels.append(parent)
        process.start()
        child.close()

    try:
        start("leader")
        await_event(0, lambda x: x["event"] == "global_scan")
        await_event(0, lambda x: x["event"] == "copied")
        start("follower")
        await_event(1, lambda x: x["event"] == "step_complete" and x["owned"])
        assert not any(x["event"] == "global_scan" for x in observed[1])

        # A producer can die after an offer: no live process claims these IDs.
        orphan = Archive(root, target)

        def publish_orphan(name):
            key, raw = f"exchange/test/{name}.json", b'{"original":"unchanged"}'
            orphan.source.blobs.put_if_absent(key, raw)
            return key, raw, orphan.offer(key, raw, start=False)

        key, raw, identity = publish_orphan("before-death")
        channels[0].send("wake")
        await_event(0, lambda x: x["event"] == "copied" and x["identity"] == identity)
        assert orphan.target.blobs.get(key) == raw

        processes[0].terminate()
        processes[0].join(5)
        assert not processes[0].is_alive()
        key, raw, identity = publish_orphan("after-death")
        channels[1].send("wake")
        await_event(1, lambda x: x["event"] == "global_scan")
        await_event(1, lambda x: x["event"] == "copied" and x["identity"] == identity)
        assert orphan.target.blobs.get(key) == raw
        assert orphan.drain(timeout=2, owned=False)["full_readback"]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(5)
        for channel in channels:
            channel.close()


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


@pytest.mark.parametrize('kind', ['segment', 'heads'])
def test_RL4_record_is_not_preserved_before_its_exact_artifact_dependencies(
        tmp_path, kind):
    from test_rl_segment_store import (
        fixture,
        historical_segment,
        publish_historical_record,
        publish_segment,
    )

    from spireagent.workbench.managed_segment_store import read_segment

    exchange, mu, run, trace, rows = fixture(tmp_path, 1)
    segment, originals = historical_segment(run, trace, rows)
    manifest = publish_segment(exchange.store, segment, PRODUCER, originals=originals)
    record = {key: segment[key] for key in ('segment_id', 'run_id', 'rows', 'mu')}
    record['segment_artifact_id'] = manifest.artifact_id
    publish_historical_record(exchange, 'actor', 0, record)
    segments = [(segment, manifest)]
    if kind == 'segment':
        key = exchange.shard_keys()[0]
    else:
        exchange.publish_model({'run': exchange.run, **mu})
        key = f'{exchange.prefix}models/000000.json'
    target = {'kind': 'local', 'directory': str(tmp_path / 'R2-transport-fixture')}
    archive = Archive(Path(exchange.binding['directory']), target)
    # No manifest outbox worker exists. Only the normal record copy may copy
    # dependencies; independently archived manifests cannot hide a missing edge.
    original_put = archive.target.blobs.put_if_absent

    def verify_dependencies_before_record(record_key, raw):
        if record_key == key:
            if kind == 'segment':
                assert read_segment(archive.target, segments[0][1].artifact_id,
                                    scratch=tmp_path) == segments[0][0]
            else:
                head = archive.target.get_manifest(mu['heads_artifact_id'])
                assert (archive.target.bytes(head.payload('checkpoint'))
                        == b'synthetic heads fixture')
        return original_put(record_key, raw)

    archive.target.blobs.put_if_absent = verify_dependencies_before_record
    raw = exchange.blobs.get(key)
    identity = archive.offer(key, raw, start=False)
    archive._copy(identity)
    assert archive.target.blobs.get(key) == raw
    assert archive.status()["full_readback"]


def test_CP_retention_preserves_recent_pins_shared_chunks_and_remote_history(tmp_path):
    from test_rl41_w1 import PRODUCER

    from spireagent.artifact_contracts import Manifest
    from spireagent.json_boundary import FrozenObject

    peer = {
        "kind": "local",
        "directory": str(tmp_path / "local"),
        "archive": {"kind": "local", "directory": str(tmp_path / "remote")},
    }
    e = Exchange(peer, "cp-retention")
    cp = []
    for i in range(1, 7):
        m = Manifest(
            "checkpoint",
            PRODUCER,
            payloads=(e.store.put_bytes("checkpoint", f"CP{i}".encode()),),
            parameters=FrozenObject.of({"schema": "stpd/rl-segment-checkpoint-2", "sequence": i}),
        )
        e.store.publish(m)
        e.publish_checkpoint(i, m.artifact_id, sequence=i, training_generation="g-one")
        cp.append(m)
    model = Manifest("model", PRODUCER, payloads=(cp[1].payload("checkpoint"),))
    e.store.publish(model)
    e.drain_archive(10)
    result = e.retain_checkpoints(protected=(cp[0].artifact_id,))
    assert set(result["candidate_artifact_ids"]) == {cp[1].artifact_id, cp[2].artifact_id}
    for index, m in enumerate(cp):
        exists = (e.blobs.root / "objects/sha256" / m.payload("checkpoint").sha256).exists()
        assert exists == (index != 2), (index, exists)


def test_quota_inode_backlog_and_stale_capacity_reject_new_work(tmp_path, monkeypatch):
    from pathlib import Path

    from spireagent.storage import async_archive as module
    from stpd.rl.segments import SegmentConfig

    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/bin/usage")
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: SimpleNamespace(free=1000 * 1024**3))
    monkeypatch.setattr(module.os, "statvfs", lambda _: SimpleNamespace(f_favail=12345))
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda *args, **kwargs: "/data 90G 100G\n/data/scratch 190G 200G\n",
    )
    observed = module.filesystem_capacity(Path("/data/scratch/example-user/store"))
    assert observed["free_bytes"] == 10 * 1024**3 and observed["quota_status"] == "measured"
    assert observed["free_inodes"] == 12345
    state = {
        "fresh": True,
        "error": None,
        "quota_status": "measured",
        "free_bytes": 100 * 1024**3,
        "free_inodes": 100000,
        "pending_bytes_estimate": 0,
    }
    from stpd.rl.segment_learner import SegmentLearner

    owner = SimpleNamespace(
        settings=SegmentConfig(), archive=SimpleNamespace(capacity_status=lambda: state)
    )
    assert SegmentLearner.archive_capacity(owner)["ready"]
    for changes in (
        {"free_bytes": 1},
        {"free_inodes": 1},
        {"pending_bytes_estimate": 100 * 1024**3},
        {"quota_status": "observation_failed"},
        {"fresh": False},
        {"error": "OSError"},
    ):
        saved = state.copy()
        state.update(changes)
        assert not SegmentLearner.archive_capacity(owner)["ready"], changes
        state.clear()
        state.update(saved)


def test_same_budget_deadline_cold_peer_archives_identical_control_bytes(tmp_path):
    remote = {'kind': 'local', 'directory': str(tmp_path / 'archive')}
    first = Exchange({'kind': 'local', 'directory': str(tmp_path / 'first'), 'archive': remote},
                     'deadline-cold')
    second = Exchange({'kind': 'local', 'directory': str(tmp_path / 'second'), 'archive': remote},
                      'deadline-cold')
    assert first.run_deadline(10000, budget_id='one') == 10000
    first.drain_archive(10)
    assert second.run_deadline(10000.0, budget_id='one') == 10000
    assert second.drain_archive(10)['full_readback']
    assert second.run_deadline(20000, budget_id='two') == 20000
    with pytest.raises(BoundaryError, match='run_deadline_cannot_be_reset'):
        second.run_deadline(20001, budget_id='two')
