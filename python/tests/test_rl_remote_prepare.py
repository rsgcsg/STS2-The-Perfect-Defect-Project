"""Remote assembly host: same functions, same hand-off bytes, loud failures."""

from __future__ import annotations

import dataclasses as dc
import hashlib
import mmap
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
        return hashlib.sha256(memoryview(mapping)[: handoff.used_bytes]).hexdigest()
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
        connection.send({"protocol": remote_prepare.PROTOCOL})
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
