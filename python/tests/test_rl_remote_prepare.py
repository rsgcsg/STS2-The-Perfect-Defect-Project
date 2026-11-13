"""Remote assembly host: same functions, same hand-off bytes, loud failures."""

from __future__ import annotations

import dataclasses as dc
import mmap
import pickletools
import threading
import time
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener

import pytest
from test_rl41_w1 import model as model
from test_rl41_w1 import request, segment
from test_rl_binary_preparation import BytesProvider, admit, labels_for, prepare
from test_rl_tensor_segment import encoder

from stpd.rl import remote_prepare
from stpd.rl.binary_preparation import prepared_digest
from stpd.rl.prepared_batch import AuxiliarySelection


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv(remote_prepare.AUTH_ENV, "ab" * 16)
    state = remote_prepare._Host(tmp_path / "host", 1, 1, 1024**3)
    (tmp_path / "host").mkdir()
    listener = Listener(("127.0.0.1", 0))
    key = remote_prepare.authkey()

    def accept():
        while True:
            try:
                connection = listener.accept()
            except OSError:
                return
            threading.Thread(target=remote_prepare._serve, args=(state, connection, key),
                             daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    yield state, f"127.0.0.1:{listener.address[1]}"
    listener.close()
    for pool in (state.decode, state.assembly):
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)


def _digest(handoff):
    with open(handoff.path, "rb") as stream:
        mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
    try:
        with memoryview(mapping) as whole, whole[: handoff.used_bytes] as view:
            return prepared_digest(view)
    finally:
        mapping.close()


def _handoffs(model, tmp_path, name, **options):
    """PPO member plus aux rows of an aux-only source, prepared twice."""
    provider = BytesProvider()
    preparer = prepare(model, tmp_path / name, provider, handoff=True, **options)
    enc = encoder(model, tmp_path / f"{name}-spool")
    late = segment(model, name="late/0", run_id="late-episode", terminal=True)
    current = segment(model, name="current/0", run_id="current-episode")
    try:
        old_member, encoded = admit(enc, preparer, provider, late, "late")
        labels = labels_for(late, encoded)
        labels["segments"] = [
            {"segment_id": old_member.segment_id, "payload": dc.asdict(old_member.payload)}
        ]
        groups = preparer.verify_labels(labels, (old_member,)).result(timeout=60)
        items = tuple(row["prepared_input"] for _, _, rows in groups for row in rows)
        member, _ = admit(enc, preparer, provider, current, "current")
        rows = list({item.source["row"]: item for item in items}.values())[:2]
        req = dc.replace(
            request([current]), members=(member,),
            auxiliary=tuple(AuxiliarySelection(i.source, "terminal", 3, 0, 2) for i in rows),
            auxiliary_minibatches=(0,) * len(rows))
        out = []
        for _ in range(2):
            handoff = preparer.submit(req, items).result(timeout=120)
            out.append((handoff.sha256, handoff.used_bytes, _digest(handoff),
                        handoff.metadata, handoff.label_updates))
            preparer.release_handoff(handoff)
        return out, preparer.summary(), preparer.remote and dict(preparer.remote.stats)
    finally:
        preparer.shutdown(wait=True, cancel_futures=True)


def test_remote_assembly_hands_off_the_local_bytes_and_reuses_sources(model, tmp_path, host):
    _state, endpoint = host
    local, _summary, _ = _handoffs(model, tmp_path, "local")
    remote, summary, stats = _handoffs(
        model, tmp_path, "remote", remote_prepare={"endpoints": [endpoint], "connections": 1})
    assert [r[:2] for r in remote] == [r[:2] for r in local]
    assert all(sha == digest for sha, _, digest, *_ in remote)  # file == worker digest
    assert [r[3:] for r in remote] == [r[3:] for r in local]
    # Two sources cross once; the second batch finds both in the host cache.
    assert stats["tasks"] == 2 and stats["sources_sent"] == 2
    assert any(row["pool"] == "remote_assembly" and row["tasks"] == 2
               for row in summary["workers"])


def test_remote_failure_fails_that_batch_without_local_fallback(model, tmp_path, host):
    _state, endpoint = host
    provider = BytesProvider()
    preparer = prepare(model, tmp_path / "p", provider, handoff=True,
                       remote_prepare={"endpoints": [endpoint], "connections": 1})
    enc = encoder(model, tmp_path / "spool")
    value = segment(model)
    try:
        member, _ = admit(enc, preparer, provider, value, "current")
        req = dc.replace(request([value]), members=(member,))
        with pytest.raises(RuntimeError, match="binary_remote_assembly_failed.*byte"):
            preparer.submit(dc.replace(req, max_output_bytes=64)).result(timeout=120)
        assert preparer.summary()["failed_tasks_without_timing"]["remote_assembly"] == 1
        # A later batch opens a new connection and succeeds.
        handoff = preparer.submit(req).result(timeout=120)
        assert handoff.sha256 == _digest(handoff)
        preparer.release_handoff(handoff)
    finally:
        preparer.shutdown(wait=True, cancel_futures=True)


def test_remote_prepare_requires_handoff_and_an_authkey(model, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="requires_handoff"):
        prepare(model, tmp_path / "a", BytesProvider(),
                remote_prepare={"endpoints": ["127.0.0.1:1"]})
    monkeypatch.delenv(remote_prepare.AUTH_ENV, raising=False)
    with pytest.raises(ValueError, match="authkey_required"):
        prepare(model, tmp_path / "b", BytesProvider(), handoff=True,
                remote_prepare={"endpoints": ["127.0.0.1:1"]})


def test_a_host_that_never_replies_fails_the_batch_within_the_timeout(model, tmp_path,
                                                                       monkeypatch):
    monkeypatch.setenv(remote_prepare.AUTH_ENV, "cd" * 16)
    listener = Listener(("127.0.0.1", 0), authkey=remote_prepare.authkey())
    held = []

    def silent():  # handshake and hello, then never answers a task
        connection = listener.accept()
        held.append(connection)
        connection.recv()
        connection.send({"protocol": remote_prepare.PROTOCOL, "codec": "none"})
        connection.recv()

    threading.Thread(target=silent, daemon=True).start()
    provider = BytesProvider()
    preparer = prepare(model, tmp_path / "p", provider, handoff=True, remote_prepare={
        "endpoints": [f"127.0.0.1:{listener.address[1]}"], "connections": 1,
        "timeout_seconds": 2})
    enc = encoder(model, tmp_path / "spool")
    value = segment(model)
    try:
        member, _ = admit(enc, preparer, provider, value, "current")
        began = time.monotonic()
        with pytest.raises(TimeoutError, match="remote_prepare_timeout"):
            preparer.submit(dc.replace(request([value]), members=(member,))).result(timeout=60)
        assert time.monotonic() - began < 30
        assert preparer.summary()["failed_tasks_without_timing"]["remote_assembly"] == 1
    finally:
        preparer.shutdown(wait=True, cancel_futures=True)
        listener.close()


def test_cache_key_binds_the_envelope_and_hello_binds_code_and_threads(model, host):
    state, endpoint = host
    envelope = {"packet_sha256": "a" * 64, "gae_lambda": 0.99, "training_split": "TRAIN"}
    assert remote_prepare.source_key(envelope) != remote_prepare.source_key(
        dict(envelope, gae_lambda=0.95))
    assert remote_prepare.source_key(envelope) == remote_prepare.source_key(dict(envelope))
    first = remote_prepare.RemoteAssembly(endpoints=[endpoint], vocabulary=model.vocabulary,
                                          threads=1)
    first._connect(endpoint).close()
    other = remote_prepare.RemoteAssembly(endpoints=[endpoint], vocabulary=model.vocabulary,
                                          threads=2)
    with pytest.raises(RuntimeError, match="worker_settings_changed"):
        other._connect(endpoint)
    with pytest.raises(ValueError, match="code_changed"):
        state.start(model.vocabulary, 1, "0" * 64)


def test_serve_survives_a_wrong_key_and_a_silent_client(tmp_path, monkeypatch, model):
    monkeypatch.setenv(remote_prepare.AUTH_ENV, "ef" * 16)
    ready = tmp_path / "ready"
    threading.Thread(target=remote_prepare.serve, daemon=True, args=([
        "--listen", "127.0.0.1:0", "--scratch", str(tmp_path / "h"), "--decode-workers", "1",
        "--assembly-workers", "1", "--ready-file", str(ready)],)).start()
    deadline = time.monotonic() + 30
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    endpoint = "127.0.0.1:" + ready.read_text().strip().rpartition(":")[2]
    silent = Client(remote_prepare._address(endpoint))  # connects, never answers the challenge
    with pytest.raises(AuthenticationError):
        Client(remote_prepare._address(endpoint), authkey=b"x" * 16)
    learner = remote_prepare.RemoteAssembly(endpoints=[endpoint], vocabulary=model.vocabulary,
                                            threads=1)
    learner._connect(endpoint).close()
    silent.close()


def test_remote_labels_equal_local_labels_and_the_learner_never_decodes(model, tmp_path, host):
    _state, endpoint = host
    late = segment(model, name="late/0", run_id="late-episode", terminal=True)
    out = {}
    for name, options in (("local", {}), ("remote", {"remote_prepare": {
            "endpoints": [endpoint], "connections": 1}})):
        provider = BytesProvider()
        preparer = prepare(model, tmp_path / name, provider, handoff=True, **options)
        enc = encoder(model, tmp_path / f"{name}-spool")
        try:
            member, encoded = admit(enc, preparer, provider, late, "late")
            labels = labels_for(late, encoded)
            labels["segments"] = [
                {"segment_id": member.segment_id, "payload": dc.asdict(member.payload)}]
            groups = preparer.verify_labels(labels, (member,)).result(timeout=60)
            items = [row["prepared_input"] for _, _, rows in groups for row in rows]
            assert items and all(i.body and i.envelope for i in items)
            deadline = time.monotonic() + 10  # worker records land via the callback thread
            while not preparer.summary()["workers"] and time.monotonic() < deadline:
                time.sleep(0.01)
            out[name] = groups, [i.envelope for i in items], preparer.summary()
        finally:
            preparer.shutdown(wait=True, cancel_futures=True)
    assert out["remote"][:2] == out["local"][:2]  # AuxiliaryInput equality includes body
    pools = {row["pool"] for row in out["remote"][2]["workers"]}
    assert "remote_labels" in pools and not pools & {"source", "labels", "assembly"}


def test_a_host_that_drops_mid_stream_fails_the_batch_with_the_real_error(model, tmp_path,
                                                                           monkeypatch):
    monkeypatch.setenv(remote_prepare.AUTH_ENV, "12" * 16)
    listener = Listener(("127.0.0.1", 0), authkey=remote_prepare.authkey())

    def dropping():  # replies, streams a few bytes of a large batch, then hangs up
        connection = listener.accept()
        connection.recv()
        connection.send({"protocol": remote_prepare.PROTOCOL, "codec": "none"})
        task = connection.recv()
        assert task["sources"]
        connection.send({"need": []})
        connection.send({"tree": b"", "summary": {"used_bytes": 1 << 20}, "arrays": [],
                         "pid": 1, "wall": 0.0, "cpu": 0.0, "host_seconds": 0.0,
                         "decode_seconds": 0.0})
        connection.send_bytes(b"x" * 1000)
        connection.close()

    threading.Thread(target=dropping, daemon=True).start()
    provider = BytesProvider()
    preparer = prepare(model, tmp_path / "p", provider, handoff=True, remote_prepare={
        "endpoints": [f"127.0.0.1:{listener.address[1]}"], "connections": 1,
        "timeout_seconds": 10})
    enc = encoder(model, tmp_path / "spool")
    value = segment(model)
    try:
        member, _ = admit(enc, preparer, provider, value, "current")
        with pytest.raises(RuntimeError, match="short_transfer"):  # not BufferError
            preparer.submit(dc.replace(request([value]), members=(member,))).result(timeout=60)
        assert preparer.summary()["failed_tasks_without_timing"]["remote_assembly"] == 1
    finally:
        preparer.shutdown(wait=True, cancel_futures=True)
        listener.close()


def test_label_tokens_must_name_a_ready_source():
    import io
    import pickle

    class Pickler(pickle.Pickler):
        def persistent_id(self, item):
            return (-1, None, None) if item == "body" else None

    stream = io.BytesIO()
    Pickler(stream, protocol=5).dump(["body"])
    with pytest.raises(ValueError, match="index_out_of_range"):
        remote_prepare._unpack_labels(stream.getvalue(), ())


def test_a_failed_decode_submit_cleans_up_and_fails_the_waiter(model, host, monkeypatch):
    state, _endpoint = host
    state.start(model.vocabulary, 1, remote_prepare.code_digest())
    digest = "d" * 64
    assert state.reserve([digest]) == [digest]
    placeholder = state.sources[digest][0]

    def refuse(*_args, **_kwargs):
        raise RuntimeError("pool broken")

    monkeypatch.setattr(state.decode, "submit", refuse)
    with pytest.raises(RuntimeError, match="pool broken"):
        state.add(digest, {"packet_size": 3}, b"abc")
    assert digest not in state.sources and state.held == 0
    assert not list(state.scratch.glob("src-*"))
    with pytest.raises(RuntimeError, match="pool broken"):
        placeholder.result(timeout=1)


@pytest.fixture
def lz4_frame(monkeypatch):
    """Real lz4 when installed; otherwise a stand-in with the same frame API."""
    import importlib

    try:
        frame = importlib.import_module("lz4.frame")
    except ImportError:
        import sys
        import types
        import zlib

        frame = types.ModuleType("lz4.frame")
        frame.compress = lambda data: zlib.compress(bytes(data), 1)  # type: ignore[attr-defined]
        frame.decompress = lambda data: zlib.decompress(bytes(data))  # type: ignore[attr-defined]
        package = types.ModuleType("lz4")
        package.frame = frame  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "lz4", package)
        monkeypatch.setitem(sys.modules, "lz4.frame", frame)
    return frame


def test_lz4_codec_hands_off_the_same_bytes_and_hello_binds_the_codec(model, tmp_path, host,
                                                                       lz4_frame, monkeypatch):
    state, endpoint = host
    monkeypatch.setattr(remote_prepare, "LZ4_CHUNK_BYTES", 64 * 1024)  # many frames per batch
    local, _summary, _ = _handoffs(model, tmp_path, "local")
    state.codec = "lz4"
    remote, _summary, stats = _handoffs(model, tmp_path, "remote", remote_prepare={
        "endpoints": [endpoint], "connections": 1, "codec": "lz4"})
    assert [r[:2] for r in remote] == [r[:2] for r in local]
    assert all(sha == digest for sha, _, digest, *_ in remote)  # sha256 of decompressed file
    assert [r[3:] for r in remote] == [r[3:] for r in local]
    assert 0 < stats["wire_received_bytes"] < stats["received_bytes"]
    plain = remote_prepare.RemoteAssembly(endpoints=[endpoint], vocabulary=model.vocabulary,
                                          threads=1)
    with pytest.raises(RuntimeError, match="codec_changed"):
        plain._connect(endpoint)
    with pytest.raises(ValueError, match="codec_unknown"):
        remote_prepare.RemoteAssembly(endpoints=[endpoint], vocabulary=model.vocabulary,
                                      threads=1, codec="zstd")


# ------------------------------------------------- client processes (client_processes: N)


def _tree_shape(tree):
    """The pickled tree minus its float values (wall-clock timings differ per run)."""
    return [(op.name, arg) for op, arg, _ in pickletools.genops(tree)
            if not isinstance(arg, float)]


def _batch_view(handoff):
    return (handoff.sha256, handoff.used_bytes, _digest(handoff), _tree_shape(handoff.tree),
            [(a.offset, a.count, a.dtype) for a in handoff.arrays], handoff.metadata,
            handoff.label_updates, handoff.payload_bytes, handoff.minibatch_count)


def test_client_processes_hand_off_the_same_batches_and_labels(model, tmp_path, host):
    _state, endpoint = host
    late = segment(model, name="late/0", run_id="late-episode", terminal=True)
    out = {}
    for clients in (0, 2):
        name = f"c{clients}"
        provider = BytesProvider()
        preparer = prepare(model, tmp_path / name, provider, handoff=True, remote_prepare={
            "endpoints": [endpoint], "connections": 2, "client_processes": clients})
        enc = encoder(model, tmp_path / f"{name}-spool")
        try:
            assert isinstance(preparer.remote, remote_prepare.RemoteClients) == bool(clients)
            member, encoded = admit(enc, preparer, provider, late, "late")
            labels = labels_for(late, encoded)
            labels["segments"] = [
                {"segment_id": member.segment_id, "payload": dc.asdict(member.payload)}]
            groups = preparer.verify_labels(labels, (member,)).result(timeout=120)
            req = dc.replace(request([late]), members=(member,))
            batches = []
            for _ in range(2):
                handoff = preparer.submit(req).result(timeout=120)
                batches.append(_batch_view(handoff))
                if clients:
                    assert preparer.remote.served_by(handoff.path)  # host fingerprint
                preparer.release_handoff(handoff)
            stats = dict(preparer.remote.stats)
            stats.pop("client_restarts", None)
            out[clients] = groups, batches, stats
        finally:
            preparer.shutdown(wait=True, cancel_futures=True)
    assert out[2][0] == out[0][0]  # AuxiliaryInput equality includes body
    assert out[2][1] == out[0][1]
    assert all(view[0] == view[2] for view in out[2][1])  # file == worker digest
    # Stats come back from the client processes and are summed here. The second
    # run finds its source in the host cache (the first run sent it).
    assert out[2][2]["tasks"] == 2 and out[2][2]["label_tasks"] == 1
    assert out[2][2]["received_bytes"] == out[0][2]["received_bytes"]


def test_client_process_errors_pass_through_and_a_dead_client_fails_its_batch(
        model, tmp_path, host, monkeypatch):
    _state, endpoint = host
    provider = BytesProvider()
    preparer = prepare(model, tmp_path / "p", provider, handoff=True, remote_prepare={
        "endpoints": [endpoint], "connections": 1, "client_processes": 1})
    enc = encoder(model, tmp_path / "spool")
    value = segment(model)
    try:
        member, _ = admit(enc, preparer, provider, value, "current")
        req = dc.replace(request([value]), members=(member,))
        with pytest.raises(RuntimeError, match="binary_remote_assembly_failed.*byte"):
            preparer.submit(dc.replace(req, max_output_bytes=64)).result(timeout=120)
        handoff = preparer.submit(req).result(timeout=120)
        assert handoff.sha256 == _digest(handoff)
        preparer.release_handoff(handoff)
        # Kill the client process while its task is in flight: that batch fails
        # (no local fallback, no re-send); a later batch starts a new client.
        client = preparer.remote.clients[0]
        original = client.submit

        def submit_then_kill(task_id, task):
            future = original(task_id, task)
            client.process.kill()
            return future

        monkeypatch.setattr(client, "submit", submit_then_kill)
        with pytest.raises(RuntimeError, match=remote_prepare.DIED):
            preparer.submit(req).result(timeout=120)
        assert preparer.summary()["failed_tasks_without_timing"]["remote_assembly"] == 2
        handoff = preparer.submit(req).result(timeout=120)
        assert handoff.sha256 == _digest(handoff)
        assert preparer.remote.stats["client_restarts"] == 1
        preparer.release_handoff(handoff)
    finally:
        preparer.shutdown(wait=True, cancel_futures=True)


def test_client_processes_bound():
    with pytest.raises(ValueError, match="client_processes_bound"):
        remote_prepare.RemoteClients(processes=0, endpoints=["h:1"], vocabulary={}, threads=1)


def test_a_reader_failure_stops_the_live_client_before_failing_its_tasks(monkeypatch):
    from concurrent.futures import Future
    from multiprocessing.connection import Connection

    monkeypatch.setenv(remote_prepare.AUTH_ENV, "ab" * 16)
    client = remote_prepare._Client({
        "endpoints": ["127.0.0.1:1"], "vocabulary": {}, "threads": 1, "connections": 1,
        "auth_env": remote_prepare.AUTH_ENV, "timeout_seconds": 5.0, "codec": "none",
        "slots": ["127.0.0.1:1"]})
    try:
        seen = []
        waiting: Future = Future()
        waiting.add_done_callback(lambda _: seen.append(client.process.is_alive()))
        client.pending[999] = waiting  # an in-flight task the child never answers

        def unreadable(self):
            raise MemoryError("reply")

        monkeypatch.setattr(Connection, "recv", unreadable)
        # The reader's current recv takes this reply; its next recv fails.
        first = client.submit(1, ("labels", (), (), None))
        with pytest.raises(Exception):  # noqa: B017 - refused connect, as raised
            first.result(timeout=120)
        with pytest.raises(RuntimeError, match=remote_prepare.DIED + ": exitcode=-9"):
            waiting.result(timeout=30)
        assert seen == [False] and client.dead and not client.process.is_alive()
        with pytest.raises(RuntimeError, match=remote_prepare.DIED):
            client.submit(2, ("labels", (), (), None))
    finally:
        monkeypatch.undo()
        client.close()
