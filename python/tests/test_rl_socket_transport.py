"""Real authenticated sockets and bounded local bytes; synthetic protocol inputs."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import asdict
from multiprocessing.connection import Client

import pytest
from spireagent.json_boundary import json_bytes
from spireagent.rl_dataplane_ports import (
    CONTROL_SCHEMA,
    MAX_PACKET_BYTES,
    TENSOR_SEGMENT_FORMAT,
    BinaryRef,
    EncodedSegment,
)
from spireagent.rl_exchange import control_input, input_key
from spireagent.rl_local_wal import LocalControlWAL
from spireagent.rl_socket_transport import (
    AUTH_ENV,
    BinarySources,
    DataplaneError,
    SocketDataplane,
    upload,
)


def encoded(body=b"immutable packet", **changes):
    return EncodedSegment(
        {
            "format": TENSOR_SEGMENT_FORMAT,
            "run": "r",
            "training_generation": "g",
            "actor": "a",
            "episode": "e",
            "run_id": "world",
            "segment_id": "a/0",
            "rows": 2,
            "producer": {"source": "fixture"},
            "recipe_digest": "recipe",
            "use_and_seed_digest": "use",
            "row_bindings_sha256": "rows",
            "mu": {"snapshot_sha256": "mu"},
            "compiler_identity": {"compiler": "fixture"},
            "input_spec": {"input": "fixture"},
            "original_commit": {"original": "fixture"},
            "packet_size": len(body),
            "packet_sha256": hashlib.sha256(body).hexdigest(),
            **changes,
        },
        body,
    )


def test_borrow_is_readonly_and_pins_until_copy_finishes(tmp_path):
    sources = BinarySources(tmp_path, run="r", generation="g", max_bytes=100)
    value = encoded()
    credit = sources.begin(value.envelope)
    sources.chunk(credit, value.packet)
    ref = sources.finish(credit)
    sources.retain(ref, "prepare")
    sources.release(ref, "upload")
    with sources.borrow(ref) as view:
        assert view.readonly and view.tobytes() == value.packet
        sources.release(ref, "prepare")
        assert sources.status()["spool_bytes"] == len(value.packet)
    assert sources.status()["spool_bytes"] == 0
    with pytest.raises(DataplaneError, match="SOURCE_DISCARDED"):
        sources.envelope(ref)


def test_partial_upload_hash_and_size_failures_never_expose_a_ref(tmp_path):
    sources = BinarySources(tmp_path, run="r", generation="g", max_bytes=100)
    credit = sources.begin(encoded().envelope)
    sources.chunk(credit, b"wrong")
    with pytest.raises(DataplaneError, match="INVALID_PACKET"):
        sources.finish(credit)
    sources.cancel(credit)
    value = encoded(b"correct")
    credit = sources.begin(value.envelope)
    sources.chunk(credit, b"corrupt")
    with pytest.raises(DataplaneError, match="HASH_MISMATCH"):
        sources.finish(credit)
    sources.cancel(credit)
    assert sources.status()["spool_bytes"] == 0
    assert not sources.entries
    for bad in (
        encoded(format="auto"),
        encoded(training_generation="old"),
        encoded(rows=0),
        encoded(packet_size=MAX_PACKET_BYTES + 1),
    ):
        with pytest.raises(DataplaneError):
            sources.begin(bad.envelope)


def test_byte_credit_wait_is_not_row_credit_and_stop_wakes_it(tmp_path):
    sources = BinarySources(tmp_path, run="r", generation="g", max_bytes=16)
    value = encoded(b"0123456789abcdef")
    credit = sources.begin(value.envelope)
    result = []
    entered = threading.Event()

    def waiting():
        entered.set()
        try:
            sources.begin({**value.envelope, "segment_id": "a/1"})
        except DataplaneError as error:
            result.append(error.code)

    worker = threading.Thread(target=waiting)
    worker.start()
    assert entered.wait(1)
    sources.stop()
    worker.join(2)
    assert not worker.is_alive() and result == ["STOPPED"]
    sources.cancel(credit)
    assert sources.status()["spool_bytes"] == 0


def test_control_canonical_preserves_binary_hash_and_separates_historical_keys():
    ref = {
        "format": TENSOR_SEGMENT_FORMAT,
        "slot": "r/g/" + "a" * 32,
        "sha256": "a" * 64,
        "size": 128,
    }
    request = {
        "operation": "commit",
        "control_schema": CONTROL_SCHEMA,
        "run": "r",
        "training_generation": "g",
        "segment_id": "a/0",
        "run_id": "world",
        "rows": 2,
        "mu": {"mu": "exact"},
        "payload": ref,
        "envelope": encoded().envelope,
    }
    original = control_input(request)
    assert original["payload"] == ref
    assert original["segment_artifact_id"] == "tensor:" + ref["slot"]
    assert input_key(original) != input_key(
        control_input({**request, "payload": {**ref, "sha256": "b" * 64}})
    )
    with pytest.raises((ValueError, TypeError)):
        control_input({k: v for k, v in request.items() if k != "control_schema"})
    from spireagent.rl_exchange import _hot_ref

    with pytest.raises(ValueError):
        _hot_ref({"slot": "r/g/source", "sha256": "a" * 64, "size": 65 * 1024**2})


def test_real_socket_upload_commit_receipt_and_stop_bypass_blocked_begin(tmp_path, monkeypatch):
    # The real learner's semantic admission is covered by the integration test;
    # this fixture isolates independent socket channels and durable ACK ordering.
    from stpd.p1.export import adapter_identity
    from stpd.p1.local_inference import SEGMENT_SCHEMA, SegmentConnection

    wal = LocalControlWAL(tmp_path / "wal", run="r", generation="g")
    sources = BinarySources(tmp_path / "sources", run="r", generation="g", max_bytes=1024)
    stopped = threading.Event()
    begin_entered = threading.Event()
    applied = []

    def control(request):
        operation = request["operation"]
        if operation == "begin":
            begin_entered.set()
            assert stopped.wait(5)
            return {"stopped": True}
        if operation == "status":
            return {"ready": True}
        if operation == "stop":
            stopped.set()
            return {"stopped": True}
        if operation == "receipt":
            original = control_input(request["input"])
            return wal.receipt(input_key(original), original)
        original = control_input(request)
        wal.append(input_key(original), original, {"segment_id": original["segment_id"]})
        assert wal.receipt(input_key(original), original)["status"] == "FOUND"
        applied.append(original)
        return {"segment_id": original["segment_id"]}

    auth = b"x" * 32
    monkeypatch.setenv(AUTH_ENV, auth.hex())
    server = SocketDataplane(
        attachment={"schema": SEGMENT_SCHEMA, "pool_id": "pool", "adapter": adapter_identity()},
        auth=auth,
        control=control,
        sources=sources,
        address=("127.0.0.1", 0),
    )
    attachment = {**server.attachment(), "run": "r", "training_generation": "g"}
    results = []

    def begin():
        with SegmentConnection(attachment) as connection:
            results.append(connection.request("begin", actor="b", episode="e", previous_lease=None))

    waiter = threading.Thread(target=begin)
    waiter.start()
    assert begin_entered.wait(2)
    try:
        value = encoded(b"abc" * 100)
        ref = upload(attachment, value)
        assert sources.envelope(ref) == value.envelope
        with SegmentConnection(attachment) as connection:
            assert connection.request("status")["ready"]
            request = {
                "run": "r",
                "training_generation": "g",
                "segment_id": "a/0",
                "run_id": "world",
                "rows": 2,
                "mu": value.envelope["mu"],
                "payload": asdict(ref),
                "envelope": value.envelope,
            }
            assert connection.request("commit", **request) == {"segment_id": "a/0"}
            receipt = connection.request(
                "receipt",
                input={**request, "operation": "commit", "control_schema": CONTROL_SCHEMA},
            )
            assert receipt["status"] == "FOUND" and len(applied) == 1
            assert connection.request(
                "stop", run="r", training_generation="g", epoch=1, reason="fixture"
            )["stopped"]
        waiter.join(2)
        assert results == [{"stopped": True}]
    finally:
        stopped.set()
        waiter.join(2)
        server.close()
        wal.close()


def test_disconnected_half_body_cancels_credit(tmp_path, monkeypatch):
    auth = b"y" * 32
    sources = BinarySources(tmp_path, run="r", generation="g", max_bytes=1024)
    binding = {"schema": "fixture", "pool_id": "pool", "adapter": {"exact": True}}
    server = SocketDataplane(
        attachment=binding,
        auth=auth,
        control=lambda _: {},
        sources=sources,
        address=("127.0.0.1", 0),
    )
    try:
        with Client(server.address, authkey=auth) as connection:
            connection.send_bytes(
                json_bytes(
                    {
                        **binding,
                        "request_id": 1,
                        "operation": "upload_begin",
                        "envelope": encoded().envelope,
                    }
                )
            )
            assert json_bytes  # response framing is real, no pickle
            connection.recv_bytes()
            connection.send_bytes(b"half")
        until = time.monotonic() + 2
        while sources.status()["upload_credits"] and time.monotonic() < until:
            threading.Event().wait(0.01)
        assert sources.status()["spool_bytes"] == 0
    finally:
        server.close()


def test_uncommitted_uploads_are_owned_until_original_close_or_retirement(tmp_path):
    sources = BinarySources(tmp_path, run="r", generation="g", max_bytes=100)
    value = encoded()
    credit = sources.begin(value.envelope)
    sources.chunk(credit, value.packet)
    orphan = sources.finish(credit)
    with pytest.raises(DataplaneError, match="IDENTITY_MISMATCH"):
        sources.begin(value.envelope)  # no repeated orphan per original lease
    sources.release_uploads("other")
    assert sources.envelope(orphan) == value.envelope
    sources.release_uploads("a", "e")
    assert sources.status()["spool_bytes"] == 0
    value = encoded(episode="e2", segment_id="a/1")
    credit = sources.begin(value.envelope)
    sources.chunk(credit, value.packet)
    accepted = sources.finish(credit)
    sources.retain(accepted, "ppo:a/0")
    sources.release_uploads("a")
    assert sources.envelope(accepted) == value.envelope
    sources.release(accepted, "ppo:a/0")
    assert sources.status()["spool_bytes"] == 0


@pytest.mark.parametrize("trim", [False, True])
def test_mu_due_refresh_failure_never_advances_cadence_or_grants_old_version(
    tmp_path, monkeypatch, trim
):
    from spireagent.rl_socket_transport import NodeMuCache

    cache = NodeMuCache({}, tmp_path, interval=0)
    original = {"identity": {"snapshot_sha256": "a" * 64}, "checked_at": 1.0}
    (tmp_path / "CURRENT.json").write_bytes(json_bytes(original))
    monkeypatch.setattr(cache, "_trim", lambda *_args: trim)

    def failed():
        raise DataplaneError("CAPACITY_WAIT")

    monkeypatch.setattr(cache, "_download", failed)
    with pytest.raises(DataplaneError, match="CAPACITY_WAIT"), cache.latest():
        pytest.fail("stale mu must not be granted after a failed due refresh")
    assert (tmp_path / "CURRENT.json").read_bytes() == json_bytes(original)


def test_completed_connections_do_not_accumulate_thread_owners(tmp_path):
    auth = b"z" * 32
    sources = BinarySources(tmp_path, run="r", generation="g", max_bytes=1024)
    binding = {"schema": "fixture", "pool_id": "pool", "adapter": {"exact": True}}
    server = SocketDataplane(
        attachment=binding,
        auth=auth,
        control=lambda _: {"ready": True},
        sources=sources,
        address=("127.0.0.1", 0),
    )
    try:
        for _index in range(64):
            with Client(server.address, authkey=auth) as channel:
                channel.send_bytes(json_bytes({**binding, "operation": "status", "request_id": 1}))
                channel.recv_bytes()
        until = time.monotonic() + 2
        while server.workers and time.monotonic() < until:
            threading.Event().wait(0.01)
        assert not server.connections and not server.workers
    finally:
        server.close()


def test_node_mu_cache_is_single_flight_and_actor_load_never_reads_shared_store(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from spireagent.rl_exchange import Exchange
    from spireagent.rl_socket_transport import NodeMuCache
    from stpd.p1.local_inference import SEGMENT_SCHEMA
    from stpd.p1.local_mu import LocalMuScorer
    from stpd.rl.segment_learner import SegmentLearner
    from test_rl41_w2_actor import publish_mu

    peer = {"kind": "local", "directory": str(tmp_path / "store")}
    exchange = Exchange(peer, "r")
    identity, _package = publish_mu(exchange, tmp_path, 1)
    pool = SimpleNamespace(
        snapshots={identity["snapshot_sha256"]: identity},
        latest=identity["snapshot_sha256"],
        node_caches={},
    )
    owner = SimpleNamespace(
        pool=pool,
        generation="initial",
        live_work=tmp_path / "learner",
        exchange=exchange,
        condition=threading.Condition(),
    )
    calls = []

    def model_files(key, node, cached):
        calls.append((node, list(cached)))
        return SegmentLearner._model_files(owner, key, node, cached)

    auth = b"m" * 32
    monkeypatch.setenv(AUTH_ENV, auth.hex())
    sources = BinarySources(tmp_path / "sources", run="r", generation="initial")
    binding = {"schema": SEGMENT_SCHEMA, "pool_id": "pool", "adapter": identity["adapter"]}
    server = SocketDataplane(
        attachment=binding,
        auth=auth,
        control=lambda _: {},
        sources=sources,
        model_files=model_files,
        address=("127.0.0.1", 0),
    )
    attachment = {**server.attachment(), "run": "r", "training_generation": "initial"}
    node = NodeMuCache(attachment, tmp_path / "node", interval=15)

    def adoption(_index):
        with node.latest() as current:
            return current

    try:
        with ThreadPoolExecutor(max_workers=8) as actors:
            identities = list(actors.map(adoption, range(16)))
        assert identities == [identity] * 16 and len(calls) == 1
        # Initial registration starts with an empty actor Store. The node cache
        # must install the original immutable manifest and exact payload roles.
        empty_exchange = Exchange({"kind": "local", "directory": str(tmp_path / "empty")}, "r")
        empty_exchange.model_provider = node
        original = empty_exchange.latest_model(training_generation="initial")
        package = empty_exchange.materialize_model(original, tmp_path / "bootstrap")
        assert (package / "model.tensor-tree").read_bytes() == (
            tmp_path / "node" / identity["snapshot_sha256"] / "model.tensor-tree"
        ).read_bytes()
        assert (
            empty_exchange.store.get_manifest(identity["model_artifact_id"]).artifact_id
            == (identity["model_artifact_id"])
        )
        assert empty_exchange.materialize_model(original, tmp_path / "bootstrap") == package
        actor_exchange = Exchange(peer, "r")

        def forbidden(*_args, **_kwargs):
            pytest.fail("actor model adoption touched shared Store")

        monkeypatch.setattr(actor_exchange, "materialize_model", forbidden)
        monkeypatch.setattr(actor_exchange.store, "get_manifest", forbidden)
        monkeypatch.setattr(actor_exchange.store, "read_payload", forbidden)
        scorer = LocalMuScorer(actor_exchange, tmp_path / "actor")
        scorer.node_cache = node
        with node.latest() as current:
            scorer.adopt(current)
        assert scorer.latest == identity["snapshot_sha256"]
        scorer.close()
        # A due metadata refresh to the same version transfers no model body.
        current = tmp_path / "node/CURRENT.json"
        record = __import__("json").loads(current.read_bytes())
        record["checked_at"] = 0
        current.write_bytes(json_bytes(record))
        with node.latest() as again:
            assert again == identity
        assert len(calls) == 2 and calls[1][1] == [identity["snapshot_sha256"]]
    finally:
        server.close()


@pytest.mark.parametrize("failure_errno", [24, 23, 9])
def test_accept_errors_are_counted_and_typed_without_killing_acceptor(
    tmp_path, monkeypatch, failure_errno
):
    import errno

    import spireagent.rl_socket_transport as module

    actual_listener = module.Listener
    failures = []

    class RecoveringListener:
        def __init__(self, *args, **kwargs):
            self.listener = actual_listener(*args, **kwargs)
            self.address = self.listener.address
            self.failed = False

        def accept(self):
            if not self.failed:
                self.failed = True
                raise OSError(failure_errno, "injected accept error")
            return self.listener.accept()

        def close(self):
            self.listener.close()

    monkeypatch.setattr(module, "Listener", RecoveringListener)
    binding = {"schema": "fixture", "pool_id": "pool", "adapter": {}}
    auth = b"e" * 32
    server = SocketDataplane(
        attachment=binding,
        auth=auth,
        control=lambda _: {"stopped": True},
        sources=BinarySources(tmp_path, run="r", generation="g"),
        address=("127.0.0.1", 0),
        on_failure=failures.append,
    )
    try:
        with Client(server.address, authkey=auth) as channel:
            channel.send_bytes(json_bytes({**binding, "operation": "stop", "request_id": 1}))
            assert __import__("json").loads(channel.recv_bytes())["result"]["stopped"]
        assert server.acceptor.is_alive()
        health = server.health()
        assert health["accept_errors"] == 1
        exhausted = failure_errno in {errno.EMFILE, errno.ENFILE}
        assert health["fd_exhaustions"] == int(exhausted)
        assert failures[0]["code"] == ("FD_EXHAUSTED" if exhausted else "SOCKET_ACCEPT_FAILED")
        assert failures[0]["fatal"] is not exhausted
    finally:
        server.close()


def test_fd_capacity_is_checked_against_actual_raised_limit(monkeypatch):
    import resource

    from spireagent.rl_socket_transport import ensure_fd_capacity

    limits = [1024, 4096]
    monkeypatch.setattr(resource, "getrlimit", lambda _: tuple(limits))

    def set_limit(_resource, value):
        limits[:] = value

    monkeypatch.setattr(resource, "setrlimit", set_limit)
    result = ensure_fd_capacity(500, source_slots=100)
    assert result["nofile_soft"] == 4096 and result["required_fds"] == 3356
    with pytest.raises(DataplaneError, match="FD_CAPACITY_EXCEEDED"):
        ensure_fd_capacity(1000, source_slots=100)


def test_500_authenticated_idle_connections_leave_stop_serviceable(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import ExitStack

    from spireagent.rl_socket_transport import ensure_fd_capacity

    limits = ensure_fd_capacity(500)
    auth = b"c" * 32
    binding = {"schema": "fixture", "pool_id": "pool", "adapter": {}}
    server = SocketDataplane(
        attachment=binding,
        auth=auth,
        control=lambda r: {"stopped": r["operation"] == "stop"},
        sources=BinarySources(tmp_path, run="r", generation="g"),
        address=("127.0.0.1", 0),
    )
    try:
        with ExitStack() as cleanup, ThreadPoolExecutor(max_workers=32) as clients:

            def connect(_index):
                return Client(server.address, authkey=auth)

            connections = list(clients.map(connect, range(500)))
            for connection in connections:
                cleanup.callback(connection.close)
            with Client(server.address, authkey=auth) as priority:
                priority.send_bytes(json_bytes({**binding, "operation": "stop", "request_id": 1}))
                assert priority.poll(2)
                assert __import__("json").loads(priority.recv_bytes())["result"]["stopped"]
            health = server.health()
            assert health["acceptor_alive"] and health["socket_connections"] >= 500
            print(
                __import__("json").dumps(
                    {"socket_idle_connections": 500, "fd_limits": limits, "health": health}
                )
            )
    finally:
        server.close()


def test_waiting_upload_owner_is_unique_and_close_revokes_before_capacity_arrives(tmp_path):
    sources = BinarySources(tmp_path, run="r", generation="g", max_bytes=16)
    blocker = encoded(b"x" * 16, actor="b", episode="other", segment_id="b/0")
    occupied = sources.begin(blocker.envelope)
    value = encoded(b"a" * 8)
    responses, errors = [], []

    def waiting():
        try:
            responses.append(sources.begin(value.envelope))
        except DataplaneError as error:
            errors.append(error.code)

    one = threading.Thread(target=waiting)
    two = threading.Thread(target=waiting)
    one.start()
    until = time.monotonic() + 2
    while len(sources.upload_owners) != 2 and time.monotonic() < until:
        threading.Event().wait(0.01)
    two.start()
    two.join(2)
    assert errors == ["IDENTITY_MISMATCH"] and not responses
    sources.release_uploads("a", "e")
    one.join(2)
    assert not one.is_alive() and errors == ["IDENTITY_MISMATCH", "STOPPED"]
    sources.cancel(occupied)
    assert not responses and not sources.credits and sources.status()["spool_bytes"] == 0
    with pytest.raises(DataplaneError, match="STOPPED"):
        sources.begin(value.envelope)


def capacity_owner(tmp_path, lanes):
    """Real production control/queue/WAL; synthetic conditions, no World or tensors."""
    from types import SimpleNamespace

    from stpd.rl.segment_learner import SegmentLearner
    from stpd.rl.segments import SegmentConfig, SegmentQueue

    owner = SegmentLearner.__new__(SegmentLearner)
    owner.settings = SegmentConfig(
        steps=1,
        dataset_rows=1,
        minibatches=1,
        episode_rows=1,
        capacity_rows=2,
        preparation_workers=1,
    )
    owner.config = SimpleNamespace(run="r", operating_budget=None)
    owner.generation = "g"
    owner.wal = LocalControlWAL(tmp_path / "control.sqlite", run="r", generation="g")
    owner.binary_sources = BinarySources(
        tmp_path / "bodies", run="r", generation="g", max_bytes=1024
    )
    owner.archive = None
    owner.queue = SegmentQueue(owner.settings)
    owner.control_lock = threading.RLock()
    owner.condition = owner.queue.condition = threading.Condition(owner.control_lock)
    owner.capacity_waiters, owner.episode_load, owner.lease_owners = {}, {}, {}
    owner.capacity_tickets = 0
    owner.source_prefetch_changed = threading.Event()
    owner.actor_episodes = {f"a{i}:e": "e" for i in range(lanes)}
    owner.retired_actors = set()
    owner.input_fenced = owner.newlease_stopped = False
    owner.input_failure = owner.input_head = owner.input_anchor = owner.stop_fact = None
    owner.input_sequence = owner.stop_epoch = owner.training_discarded_rows = 0
    owner.hp_binary_members, owner.hot_references, owner.sources = {}, {}, {}
    mu = {"run": "r", "training_generation": "g", "snapshot_sha256": "a" * 64}
    owner.pool = SimpleNamespace(
        snapshots={"a" * 64: mu}, latest="a" * 64, node_caches={"node": {"a" * 64}}
    )
    for i in range(2):
        actor = f"blocker{i}"
        owner.queue.reserve(actor + "/0")
        record = {
            "segment_id": actor + "/0",
            "run_id": "synthetic",
            "actor": actor,
            "episode": "blocked",
            "rows": 1,
            "mu": mu,
            "payload": {
                "format": TENSOR_SEGMENT_FORMAT,
                "slot": "r/g/" + str(i) * 32,
                "sha256": "a" * 64,
                "size": 1,
            },
        }
        owner.queue.commit(actor + "/0", record)
        owner.episode_load[actor, "blocked"] = {"queued": 1, "inflight": 0}
    return owner


def begin_request(actor):
    return {
        "operation": "begin",
        "control_schema": CONTROL_SCHEMA,
        "run": "r",
        "training_generation": "g",
        "actor": actor,
        "episode": "e",
        "previous_lease": None,
        "node_id": "node",
        "node_cached_mu_snapshot": "a" * 64,
    }


def test_500_capacity_waiters_do_not_poll_receipts_and_one_released_row_grants_one(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    owner = capacity_owner(tmp_path, 500)
    calls = []
    receipt = owner.wal.receipt

    def counted(key, request):
        if request.get("operation") == "begin":
            calls.append(key)
        return receipt(key, request)

    owner.wal.receipt = counted
    workers = ThreadPoolExecutor(max_workers=500)
    futures = [workers.submit(owner.control, begin_request(f"a{i}")) for i in range(500)]
    try:
        until = time.monotonic() + 15
        while len(owner.capacity_waiters) < 500 and time.monotonic() < until:
            threading.Event().wait(0.01)
        assert len(owner.capacity_waiters) == 500
        wall, cpu = time.monotonic(), time.process_time()
        threading.Event().wait(1.25)
        wall, cpu = time.monotonic() - wall, time.process_time() - cpu
        assert cpu / wall < 0.2
        assert len(calls) == 500
        with owner.condition:
            selected = owner.queue.dataset()
            owner._dataset_released(selected)
        until = time.monotonic() + 3
        while not any(f.done() for f in futures) and time.monotonic() < until:
            threading.Event().wait(0.01)
        assert sum(f.done() for f in futures) == 1
        assert len(calls) == 500 and owner.queue.occupied == 2 and owner.wal.sequence == 1
        owner.control(
            {
                "operation": "stop",
                "run": "r",
                "training_generation": "g",
                "epoch": 1,
                "reason": "protocol fixture",
            }
        )
        results = [future.result(timeout=5) for future in futures]
        assert sum("lease" in result for result in results) == 1
        assert sum(result.get("stopped") is True for result in results) == 499
        assert not owner.capacity_waiters and owner.capacity_tickets == 0
        print(
            __import__("json").dumps(
                {
                    "capacity_waiters": 500,
                    "idle_cpu_per_wall": cpu / wall,
                    "begin_receipt_queries": len(calls),
                    "grants_after_one_row_release": 1,
                }
            )
        )
    finally:
        with owner.condition:
            owner.newlease_stopped = True
            owner._notify_capacity(force=True)
        workers.shutdown(wait=True)
        owner.wal.close()


def test_duplicate_waiting_begin_has_one_durable_grant_and_same_response(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    owner = capacity_owner(tmp_path, 1)
    workers = ThreadPoolExecutor(max_workers=2)
    futures = [workers.submit(owner.control, begin_request("a0")) for _ in range(2)]
    try:
        until = time.monotonic() + 3
        while (
            not owner.capacity_waiters or next(iter(owner.capacity_waiters.values())).followers != 1
        ) and time.monotonic() < until:
            threading.Event().wait(0.01)
        with owner.condition:
            selected = owner.queue.dataset()
            owner._dataset_released(selected)
        replies = [future.result(timeout=3) for future in futures]
        assert replies[0] == replies[1] and owner.wal.sequence == 1
        assert owner.queue.occupied == 2
    finally:
        with owner.condition:
            owner.newlease_stopped = True
            owner._notify_capacity(force=True)
        workers.shutdown(wait=True)
        owner.wal.close()


def semantic_batch_hash(batch):
    """Whole numerical/metadata hash; omit only transport and measured wall/PID fields."""
    import dataclasses

    import torch

    digest = hashlib.sha256()

    def visit(value):
        if isinstance(value, torch.Tensor):
            digest.update(str((str(value.dtype), tuple(value.shape))).encode())
            digest.update(value.detach().cpu().contiguous().numpy().tobytes())
        elif dataclasses.is_dataclass(value):
            digest.update(type(value).__name__.encode())
            for field in dataclasses.fields(value):
                if field.name in {
                    "request",
                    "prepared_at",
                    "timings",
                    "worker_pids",
                    "pid",
                    "seconds",
                    "host",
                    "metadata_cache",
                }:
                    continue
                digest.update(field.name.encode())
                visit(getattr(value, field.name))
        elif isinstance(value, dict):
            for key in sorted(value):
                digest.update(key.encode())
                visit(value[key])
        elif isinstance(value, (tuple, list)):
            digest.update(str((type(value).__name__, len(value))).encode())
            for item in value:
                visit(item)
        else:
            digest.update(json_bytes(value))

    visit(batch)
    return digest.hexdigest()


def test_actual_binary_producer_socket_wal_prepared_batch_matches_old_source(tmp_path, monkeypatch):
    """Production application/codec/transport/consumer, synthetic public input; zero World/SGD."""
    import dataclasses
    import uuid

    from test_rl41_w1 import PRODUCER, configuration, segment

    pytest.importorskip("stpd.rl.binary_preparation")
    from spireagent.rl_exchange import Exchange
    from spireagent.workbench.rl_segment_producer import SegmentManagedExecutor
    from stpd.p1.export import input_spec, runtime_projection
    from stpd.p1.frame_compiler import FrameCompiler
    from stpd.p1.local_inference import SegmentConnection
    from stpd.p1.local_mu import ActorControlConnection, ActorInferencePool, LocalMuScorer
    from stpd.p1.tensor_input import TensorFrame
    from stpd.rl import batch_preparation as bp
    from stpd.rl.segment_learner import SegmentLearner
    from stpd.rl.tensor_segment import TensorSegmentEncoder

    config, io = configuration(tmp_path, monkeypatch, generation="g-wire-" + uuid.uuid4().hex[:12])
    io = dataclasses.replace(
        io,
        dataplane={
            "lanes": 2,
            "root": str(tmp_path / "live"),
            "source_bytes": 64 * 1024**2,
            "source_slots": 16,
            "prepare_slots": 2,
            "assembly_workers": 1,
        },
    )
    owner = SegmentLearner(config, tmp_path / "owner", io)
    for key, value in owner.pool.environment().items():
        monkeypatch.setenv(key, value)
    actor_pools, connections = [], []
    values, deliveries = {}, []
    tensor_init = TensorFrame.__init__
    prefetch_entered, prefetch_release = threading.Event(), threading.Event()
    original_prefetch = owner.input_preparer.prefetch

    def delayed_prefetch(ref, envelope):
        if not prefetch_entered.is_set():
            prefetch_entered.set()
            assert prefetch_release.wait(5)
        return original_prefetch(ref, envelope)

    monkeypatch.setattr(owner.input_preparer, "prefetch", delayed_prefetch)

    def no_control_tensor(self, *args, **kwargs):
        if threading.current_thread().name == "rl-channel":
            pytest.fail("learner socket/control constructed TensorFrame")
        tensor_init(self, *args, **kwargs)

    monkeypatch.setattr(TensorFrame, "__init__", no_control_tensor)
    try:
        for actor in ("a", "b"):
            actor_exchange = Exchange(config.peer, config.run)
            scorer = LocalMuScorer(actor_exchange, tmp_path / ("mu-" + actor))
            actor_pool = ActorInferencePool(SegmentConnection(owner.pool.attachment()), scorer)
            actor_pools.append(actor_pool)
            admission = ActorControlConnection(actor_pool)
            connection = SegmentConnection(actor_pool.attachment())
            connections += [admission, connection]
            episode = "episode-" + actor
            assert admission.request("episode_open", actor=actor, episode=episode)["admitted"]
            grant = admission.request("begin", actor=actor, episode=episode, previous_lease=None)
            frozen = scorer.models[grant["mu"]["snapshot_sha256"]]
            value = segment(
                frozen,
                name=grant["lease"],
                run_id=episode,
                rows=2,
                generation=owner.generation,
                mu=grant["mu"],
            )
            value["original_commit"]["cost_seconds"] = {"original_small_file_fsync": 0.0}
            values[value["segment_id"]] = value
            producer = SegmentManagedExecutor.__new__(SegmentManagedExecutor)
            producer.actor, producer.attachment, producer.admission = (
                actor,
                actor_pool.attachment(),
                admission,
            )
            producer.exchange, producer.producer = actor_exchange, PRODUCER
            producer.binary_encoders = {
                episode: TensorSegmentEncoder(
                    vocabulary=frozen.vocabulary,
                    compiler_identity=value["compiler_identity"],
                    recipe={**owner.pool.binary_profile["binary_recipe"], "actor": actor},
                    spool=tmp_path / ("spool-" + actor),
                )
            }
            producer.binary_spools = {}
            row = {"intent_id": episode}
            selection_rng = owner.rng.getstate()
            delivery = producer._commit_binary_segment(row, connection, value)
            deliveries.append(delivery)
            assert delivery["segment_artifact_id"].startswith("tensor:")
            assert owner.queue.rows == len(deliveries) * 2
            if actor == "a":
                assert prefetch_entered.wait(2)  # commit starts before a dataset exists
                # A blocked source copy must hold neither the control handler
                # nor learner lock; the original receipt remains serviceable.
                status = admission.request("status")
                assert status["run"] == config.run and not status["input_fenced"]
                prefetch_release.set()
            ref = BinaryRef(**delivery["payload"])
            until = time.monotonic() + 5
            while time.monotonic() < until:
                with owner.input_preparer.condition:
                    source_job = owner.input_preparer.source_jobs.get(ref)
                if source_job is not None:
                    source_job.result(5)
                    break
                threading.Event().wait(0.01)
            else:
                pytest.fail("committed FIFO head was not prefetched before selection")
            assert owner.rng.getstate() == selection_rng
            assert (
                producer._end_episode(
                    connection,
                    actor=actor,
                    episode=episode,
                    run_id=episode,
                    terminal=None,
                    outcome_states=[],
                    hp_battles=[],
                    typed_original=None,
                    unqualified=True,
                    segments=[{"segment_id": value["segment_id"], **delivery}],
                )["run_id"]
                == episode
            )
        active = owner._select()
        assert active is not None
        compiler = FrameCompiler(owner.model.vocabulary)
        sources = tuple(
            bp.prepare_source(
                values[member.segment_id],
                compiler,
                input_spec(owner.model.vocabulary, runtime_projection(owner.model.vocabulary)),
            )
            for member in active["request"].members
        )
        expected = bp.assemble(
            active["request"],
            sources,
            microbatch_size=owner.performance.microbatch_size,
            deduplicate_text=owner.performance.deduplicate_text,
            scene_attention_area=owner.performance.scene_attention_area,
            text_attention_area=owner.performance.text_attention_area,
        )
        owner.pending_batch = active, owner._submit_batch(active)
        assert owner._prepare()
        actual = owner.active_batch
        assert semantic_batch_hash(actual) == semantic_batch_hash(expected)
        commits = [
            record
            for record in owner.wal.records_since(0)
            if record["request"].get("operation") == "commit"
        ]
        assert len(commits) == 2
        for fact in commits:
            assert owner.wal.receipt(fact["key"], fact["request"])["response"] == fact["response"]
        assert owner.updates == owner.first_use == 0
        print(
            __import__("json").dumps(
                {
                    "integration": "producer/socket/WAL/PreparedBatch",
                    "rows": len(actual.metadata),
                    "paired_hash": semantic_batch_hash(actual),
                    "wal_commits": len(commits),
                    "World": 0,
                    "SGD": 0,
                    "preparer": owner.input_preparer.summary(),
                }
            )
        )
    finally:
        prefetch_release.set()
        for connection in connections:
            connection.close()
        for pool in actor_pools:
            pool.close()
        owner.close()


def test_closed_episode_returns_unused_wakeup_ticket_to_next_waiter(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    owner = capacity_owner(tmp_path, 2)
    workers = ThreadPoolExecutor(max_workers=2)
    a = workers.submit(owner.control, begin_request("a0"))
    until = time.monotonic() + 3
    while len(owner.capacity_waiters) != 1 and time.monotonic() < until:
        threading.Event().wait(0.01)
    b = workers.submit(owner.control, begin_request("a1"))
    try:
        until = time.monotonic() + 3
        while len(owner.capacity_waiters) != 2 and time.monotonic() < until:
            threading.Event().wait(0.01)
        with owner.condition:
            selected = owner.queue.dataset()
            owner._dataset_released(selected)
            assert owner.capacity_tickets == 1
            owner.actor_episodes.pop("a0:e")
            owner._notify_capacity()
        assert a.result(timeout=3) == {"stopped": True}
        assert b.result(timeout=3)["lease"] == "a1/0"
        assert owner.queue.occupied == 2 and owner.capacity_tickets == 0
    finally:
        with owner.condition:
            owner.newlease_stopped = True
            owner._notify_capacity(force=True)
        workers.shutdown(wait=True)
        owner.wal.close()


def test_late_terminal_witness_only_aux_survives_ppo_retirement_and_closes_once(
    tmp_path, monkeypatch
):
    import dataclasses
    import socket
    import uuid

    from test_rl41_w1 import configuration, segment
    pytest.importorskip('stpd.rl.binary_preparation')
    from spireagent.rl_socket_transport import NodeMuCache
    from spireagent.workbench.rl_segment_producer import SegmentManagedExecutor
    from stpd.p1.local_inference import SegmentConnection
    from stpd.p1.local_mu import LocalMuScorer
    from stpd.rl.segment_learner import SegmentLearner
    from stpd.rl.tensor_segment import TensorSegmentEncoder

    config, io = configuration(tmp_path, monkeypatch, generation="g-aux-" + uuid.uuid4().hex[:12])
    config = dataclasses.replace(
        config, segments=dataclasses.replace(config.segments, episode_rows=8)
    )
    io = dataclasses.replace(
        io,
        dataplane={
            "lanes": 1,
            "root": str(tmp_path / "live"),
            "source_bytes": 64 * 1024**2,
            "source_slots": 16,
            "prepare_slots": 2,
            "assembly_workers": 1,
        },
    )
    owner = SegmentLearner(config, tmp_path / "owner", io)
    for key, value in owner.pool.environment().items():
        monkeypatch.setenv(key, value)
    endpoint = owner.pool.attachment()
    scorer = LocalMuScorer(owner.exchange, tmp_path / "mu")
    connection = SegmentConnection(endpoint)
    cache = NodeMuCache(endpoint, tmp_path / "node")
    values, records = [], []
    try:
        connection.request("episode_open", actor="p", episode="episode")
        encoder = TensorSegmentEncoder(
            vocabulary=owner.model.vocabulary,
            compiler_identity=owner.compiler_identity,
            recipe={**owner.pool.binary_profile["binary_recipe"], "actor": "p"},
            spool=tmp_path / "spool",
        )
        for i in range(2):
            with cache.latest() as identity:
                grant = connection.request(
                    "begin",
                    actor="p",
                    episode="episode",
                    previous_lease=records[-1]["segment_id"] if records else None,
                    node_id=socket.gethostname(),
                    node_cached_mu_snapshot=identity["snapshot_sha256"],
                )
            scorer.adopt(grant["mu"])
            value = segment(
                scorer.models[grant["mu"]["snapshot_sha256"]],
                name=grant["lease"],
                run_id="episode",
                rows=2,
                start=2 * i,
                generation=owner.generation,
                mu=grant["mu"],
                terminal=i == 1,
            )
            encoded_value = encoder.encode(value)
            ref = upload(endpoint, encoded_value)
            record = {
                "segment_id": grant["lease"],
                "payload": asdict(ref),
                "rows": 2,
                "training_generation": owner.generation,
                "segment_artifact_id": "tensor:" + ref.slot,
            }
            connection.request(
                "commit",
                **record,
                run=config.run,
                run_id="episode",
                mu=grant["mu"],
                envelope=encoded_value.envelope,
            )
            values.append(value)
            records.append(record)
        assert owner._prepare()
        assert owner.binary_sources.status()["source_slots"] == 0
        # Valid selected old rows plus a terminal-only final source. No original
        # PPO body remains, so only the exact aux alias can verify the final witness.
        labels = {
            "run": config.run,
            "training_generation": owner.generation,
            "actor": "p",
            "episode": "episode",
            "run_id": "episode",
            "terminal": values[-1]["terminal"],
            "typed_original": values[-1]["terminal"]["typed_original"],
            "unqualified": False,
            "outcome_states": [
                {
                    "source": {
                        "run_id": "episode",
                        "segment_id": records[0]["segment_id"],
                        "training_generation": owner.generation,
                        "row": i,
                        "key": f"episode:{i}",
                    },
                    "outcome": 3,
                }
                for i in range(2)
            ],
            "hp_battles": [],
            "segments": records,
        }
        producer = SegmentManagedExecutor.__new__(SegmentManagedExecutor)
        producer.attachment = endpoint
        producer.binary_encoders = {"episode": encoder}
        auxiliary = producer._upload_auxiliary(labels)
        assert len(auxiliary) == 2
        result = connection.request("episode_end", **labels, binary_aux=auxiliary)
        assert result == {"run_id": "episode"}
        assert sum(len(rows) for rows in owner.terminal.groups.values()) == 2
        assert owner.binary_sources.status()["source_slots"] == 1
        facts = [
            r for r in owner.wal.records_since(0) if r["request"].get("operation") == "episode_end"
        ]
        assert (
            len(facts) == 1
            and owner.wal.receipt(facts[0]["key"], facts[0]["request"])["response"] == result
        )
    finally:
        connection.close()
        scorer.close()
        owner.close()


def test_binary_listener_preserves_safe_remote_exception_diagnostic(tmp_path, monkeypatch):
    from spireagent.local_diagnostics import exception_facts
    from stpd.p1.export import adapter_identity
    from stpd.p1.local_inference import SEGMENT_SCHEMA, SegmentConnection

    def failing_control(request):
        raise RuntimeError('diagnostic message must remain private')
    auth = b'y' * 32
    monkeypatch.setenv(AUTH_ENV, auth.hex())
    sources = BinarySources(tmp_path / 'sources', run='r', generation='g', max_bytes=1024)
    server = SocketDataplane(
        attachment={'schema': SEGMENT_SCHEMA, 'pool_id': 'p', 'adapter': adapter_identity()},
        auth=auth, control=failing_control, sources=sources, address=('127.0.0.1', 0))
    try:
        with (SegmentConnection({**server.attachment(), 'run': 'r', 'training_generation': 'g'}) as channel,
              pytest.raises(ValueError) as caught):
            channel.request('status')
        facts = exception_facts(caught.value)['remote']
        assert facts['exception_type'] == 'RuntimeError'
        assert facts['stack'][-1]['function'] == 'failing_control'
        assert 'diagnostic message must remain private' not in str(facts)
    finally:
        server.close()


def test_early_source_prefetch_is_bounded_to_two_fifo_datasets_and_pins_bytes(tmp_path):
    from types import SimpleNamespace

    from stpd.rl.segment_learner import SegmentLearner
    from stpd.rl.segments import SegmentConfig, SegmentQueue

    owner = SegmentLearner.__new__(SegmentLearner)
    owner.settings = SegmentConfig(
        steps=1, dataset_rows=1, minibatches=1, episode_rows=1, capacity_rows=4
    )
    owner.queue = SegmentQueue(owner.settings)
    owner.condition = owner.queue.condition
    owner.binary_sources = BinarySources(tmp_path / "bodies", run="r", generation="g")
    owner.hot_references = {}
    owner.wal = object()
    owner.source_prefetch_changed = threading.Event()
    owner.source_prefetch_stopping = threading.Event()
    owner.source_prefetch_gate = threading.Lock()
    owner.newlease_stopped = owner.input_fenced = False
    calls = []

    def prefetch(ref, _envelope):
        with owner.condition:
            assert (ref.slot, "source-prefetch") in owner.hot_references
        with owner.binary_sources.borrow(ref) as raw:
            calls.append((ref, bytes(raw)))

    owner.input_preparer = SimpleNamespace(prefetch=prefetch)
    refs = []
    for index in range(4):
        value = encoded(
            bytes([index]),
            actor=f"a{index}",
            episode=f"e{index}",
            segment_id=f"a{index}/0",
            run_id=f"world{index}",
            rows=1,
        )
        credit = owner.binary_sources.begin(value.envelope)
        owner.binary_sources.chunk(credit, value.packet)
        ref = owner.binary_sources.finish(credit)
        refs.append(ref)
        owner._retain(ref, "ppo:" + value.envelope["segment_id"])
        owner.binary_sources.release(ref, "upload")
        record = {**value.envelope, "payload": asdict(ref)}
        assert owner.queue.reserve(record["segment_id"])
        owner.queue.commit(record["segment_id"], record)
    before = owner.queue.state()
    worker = threading.Thread(target=owner._source_prefetch_loop)
    worker.start()
    try:
        owner.source_prefetch_changed.set()
        until = time.monotonic() + 2
        while time.monotonic() < until:
            with owner.condition:
                if len(calls) == 2 and not any(
                    key[1] == "source-prefetch" for key in owner.hot_references
                ):
                    break
            threading.Event().wait(0.01)
        assert calls == [(refs[0], b"\x00"), (refs[1], b"\x01")]
        assert owner.queue.state() == before
        assert [owner.queue.dataset()[0]["segment_id"] for _ in range(2)] == ["a0/0", "a1/0"]
    finally:
        owner.source_prefetch_stopping.set()
        owner.source_prefetch_changed.set()
        worker.join(2)
        assert not worker.is_alive()
        for ref, index in zip(refs, range(4), strict=True):
            owner._release(ref, f"ppo:a{index}/0")
    assert owner.binary_sources.status()["spool_bytes"] == 0


def test_checkpoint_barrier_pauses_background_source_copy_without_fencing(tmp_path, monkeypatch):
    """Real CP entry/barrier; CP serialization is outside this source-lifetime check."""
    import random
    from concurrent.futures import Future, ThreadPoolExecutor
    from types import SimpleNamespace

    pytest.importorskip("stpd.rl.binary_preparation")
    import stpd.rl.segment_learner as module
    from stpd.rl.binary_preparation import BinaryBatchPreparer
    from stpd.rl.segments import SegmentConfig, SegmentQueue

    owner = module.SegmentLearner.__new__(module.SegmentLearner)
    owner.settings = SegmentConfig(
        steps=1, dataset_rows=1, minibatches=1, episode_rows=1, capacity_rows=4
    )
    owner.queue = SegmentQueue(owner.settings)
    owner.condition = owner.queue.condition
    owner.binary_sources = BinarySources(tmp_path / "bodies", run="r", generation="g")
    owner.hot_references, owner.capacity_waiters = {}, {}
    owner.wal = object()
    owner.generation = "g"
    owner.source_prefetch_changed = threading.Event()
    owner.source_prefetch_stopping = threading.Event()
    owner.source_prefetch_gate = threading.Lock()
    owner.newlease_stopped = owner.input_fenced = False
    preparer = BinaryBatchPreparer(
        provider=owner.binary_sources,
        vocabulary={},
        workers=1,
        threads=1,
        scratch=tmp_path / "scratch",
        source_slots=2,
        max_source_bytes=1024,
        assembly_workers=1,
    )
    owner.input_preparer = preparer
    value = encoded(b"queued-packet", rows=1)
    credit = owner.binary_sources.begin(value.envelope)
    owner.binary_sources.chunk(credit, value.packet)
    ref = owner.binary_sources.finish(credit)
    owner._retain(ref, "ppo:a/0")
    owner.binary_sources.release(ref, "upload")
    owner.queue.reserve("a/0")
    owner.queue.commit("a/0", {**value.envelope, "payload": asdict(ref)})
    owner.active = owner.pending_batch = owner.checkpoint_encoding = None
    owner.checkpoints = SimpleNamespace(pending=False)
    owner._collect_checkpoint = lambda **_: None
    owner._collect_labels = lambda **_: None
    owner.rng = random.Random(7)
    owner.committed_selection_rng = owner.rng.getstate()
    owner.checkpoint_sequence = 0
    owner._learning_state = owner._memory = lambda: {}
    owner._event = lambda *_, **__: None
    owner.checkpoint_encoder = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(module, "_encode_snapshot", lambda state: state)
    pending = Future()
    preparer.pending.add(pending)
    copied = threading.Event()

    def prefetch(_ref, _envelope):
        assert not preparer.fenced
        copied.set()

    monkeypatch.setattr(preparer, "prefetch", prefetch)
    with ThreadPoolExecutor(max_workers=1) as executor:
        checkpoint = executor.submit(owner.checkpoint)
        until = time.monotonic() + 2
        while not preparer.fenced and time.monotonic() < until:
            threading.Event().wait(0.001)
        assert preparer.fenced
        worker = threading.Thread(target=owner._source_prefetch_loop)
        worker.start()
        try:
            owner.source_prefetch_changed.set()
            until = time.monotonic() + 2
            while (ref.slot, "source-prefetch") not in owner.hot_references:
                assert time.monotonic() < until
                threading.Event().wait(0.001)
            assert not copied.wait(0.05) and not owner.input_fenced
            assert owner.condition.acquire(timeout=1)
            owner.condition.release()
            pending.set_result(None)
            checkpoint.result(2)
            assert copied.wait(2)
        finally:
            if not pending.done():
                pending.set_result(None)
            checkpoint.result(2)
            owner.source_prefetch_stopping.set()
            owner.source_prefetch_changed.set()
            worker.join(2)
            assert not worker.is_alive()
            preparer.shutdown()
            owner.checkpoint_encoder.shutdown()
            owner._release(ref, "ppo:a/0")
    assert not owner.input_fenced and not owner.newlease_stopped
    assert not owner.hot_references and owner.binary_sources.status()["spool_bytes"] == 0
