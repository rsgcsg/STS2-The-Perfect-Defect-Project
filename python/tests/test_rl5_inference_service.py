"""RL5 S2-1 standalone inference service: CPU semantics with real codecs and sockets.

These are engineering fixtures. They do not establish GPU throughput, World
throughput or learning quality; the GPU microbenchmark is a separate run.
"""
from __future__ import annotations

import os
import socket
from multiprocessing.connection import Client, Listener

import pytest
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
from test_p1_input_v2 import public_state
from test_rl41_w2_actor import publish_mu, running  # noqa: F401

from spireagent.json_boundary import BoundaryError
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.inference_service import InferenceService
from stpd.p1.local_inference import SegmentConnection, tcp_nodelay
from stpd.p1.local_mu import LocalMuScorer, RemoteMuScorer, load_behavior_mu
from stpd.p1.text_cache import TextVectorCache
from stpd.p1.vocabulary import public_vocabulary


class StoreSource:
    """Test source: the same load checks over the Store instead of get_mu."""

    def __init__(self, exchange, root, first):
        self.exchange, self.root, self.items, self.loads = exchange, root, [first], 0

    def latest(self):
        return dict(self.items[-1])

    def load(self, identity):
        self.loads += 1
        return load_behavior_mu(identity, node_cache=None, exchange=self.exchange,
                                cache_root=self.root, text_cache=TextVectorCache())


@pytest.fixture
def service(running, tmp_path):  # noqa: F811
    _, _, _, exchange, first, _ = running
    (tmp_path / "svc").mkdir()
    source = StoreSource(exchange, tmp_path / "svc", first)
    rendezvous = tmp_path / "service.json"
    pool = InferenceService(source, max_batch=8, window_ms=1, address=("127.0.0.1", 0),
                            rendezvous=rendezvous)
    try:
        yield pool, source, exchange, first, rendezvous
    finally:
        pool.close()


def remote_for(rendezvous, exchange, tmp_path):
    import json

    attachment = json.loads(rendezvous.read_bytes())
    assert attachment["role"] == "inference_service"
    return RemoteMuScorer({**attachment, "rendezvous": str(rendezvous)}, exchange=exchange,
                          cache_root=tmp_path / "mu")


def test_service_scores_match_actor_local_mu(service, tmp_path):
    pool, source, exchange, first, rendezvous = service
    frame = FrameCompiler(public_vocabulary()).encode(public_state())
    local = LocalMuScorer(exchange, tmp_path / "local", cache_bytes=2 * 1024**2)
    remote = remote_for(rendezvous, exchange, tmp_path)
    try:
        local.adopt(first)
        remote.adopt(first)
        key = first["snapshot_sha256"]
        expected = local.score_compiled(key, frame, 3, policy=True)
        scored = remote.score_compiled(key, frame, 3, policy=True)
        assert remote.service and remote.report()["backend"] == "service"
        assert len(scored.scores) == len(expected.scores)
        assert max(abs(a - b) for a, b in zip(scored.scores, expected.scores, strict=True)) < 1e-5
        assert max(abs(a - b) for a, b in zip(scored.outcome_probabilities,
                                               expected.outcome_probabilities, strict=True)) < 1e-5
        # The first mu was loaded once at startup; adopt did not reload it.
        assert source.loads == 1 and pool.service_stats["adopt"] == 1
    finally:
        local.close()
        remote.close()


def test_new_grant_is_pulled_once_and_old_tail_still_scores(service, tmp_path):
    pool, source, exchange, first, rendezvous = service
    second, _ = publish_mu(exchange, tmp_path, 2)
    source.items.append(second)
    frame = FrameCompiler(public_vocabulary()).encode(public_state())
    remote = remote_for(rendezvous, exchange, tmp_path)
    try:
        remote.adopt(first, retain=True)
        remote.adopt(second)
        remote.adopt(second)  # already adopted: no second request
        assert source.loads == 2 and pool.service_stats["adopt"] == 2
        tail = remote.score_compiled(first["snapshot_sha256"], frame, 3, policy=False)
        assert tail.scores is None and len(tail.outcome_probabilities) == 51
        assert remote.score_compiled(second["snapshot_sha256"], frame, 3, policy=True).scores
    finally:
        remote.close()


def test_generation_change_refuses_old_tail(service, tmp_path):
    pool, source, exchange, first, rendezvous = service
    frame = FrameCompiler(public_vocabulary()).encode(public_state())
    remote = remote_for(rendezvous, exchange, tmp_path)
    try:
        remote.adopt(first, retain=True)
        source.items.append({**first, "training_generation": "restored-1"})
        pool.refresh()
        assert pool.attachment()["training_generation"] == "restored-1"
        with pytest.raises(BoundaryError, match="generation_changed"):
            remote.score_compiled(first["snapshot_sha256"], frame, 3, policy=False)
    finally:
        remote.close()


def test_lost_adopt_and_score_replies_are_not_resent(service, tmp_path, monkeypatch):
    pool, source, exchange, first, rendezvous = service
    remote = remote_for(rendezvous, exchange, tmp_path)
    calls = []

    def lost(operation, fields):
        calls.append(operation)
        if operation == "status":
            return {"training_generation": first["training_generation"]}
        raise EOFError("reply unknown")

    monkeypatch.setattr(remote.remote, "_request_once", lost)
    try:
        with pytest.raises(EOFError):
            remote.adopt(first)
        assert calls == ["adopt"] and not remote.adopted
        remote.adopted.add(first["snapshot_sha256"])
        remote.identities[first["snapshot_sha256"]] = first
        with pytest.raises(EOFError):
            remote.score_compiled(first["snapshot_sha256"], {}, 3, policy=True)
        assert calls == ["adopt", "score", "status"] and remote.requests == 0
    finally:
        remote.close()


def test_retired_mu_is_a_typed_error_and_control_is_refused(service, tmp_path):
    pool, source, exchange, first, rendezvous = service
    second, _ = publish_mu(exchange, tmp_path, 2)
    source.items.append(second)
    assert pool.refresh() is True
    pool.retire(set())  # keeps only the latest
    frame = FrameCompiler(public_vocabulary()).encode(public_state())
    with (SegmentConnection(pool.attachment()) as client,
          pytest.raises(BoundaryError, match="MU_NOT_RESIDENT")):
        client.request("score", snapshot_sha256=first["snapshot_sha256"],
                       compiled_input=frame, floor=3, input_projection=first["projection"])
    with (SegmentConnection(pool.attachment()) as client,
          pytest.raises(BoundaryError, match="INFERENCE_SERVICE_SCORES_ONLY")):
        client.request("begin", actor="a", episode="e", previous_lease=None)


def test_tcp_nodelay_set_on_tcp_connection(monkeypatch):
    key = os.urandom(32)
    with Listener(("127.0.0.1", 0), authkey=key) as listener:
        import threading

        accepted = []
        thread = threading.Thread(target=lambda: accepted.append(listener.accept()))
        thread.start()
        client = Client(listener.address, authkey=key)
        thread.join(5)
        try:
            assert tcp_nodelay(client) is True
            probe = socket.socket(fileno=os.dup(client.fileno()))
            try:
                assert probe.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
            finally:
                probe.close()
            client.send_bytes(b"still open")
            assert accepted[0].recv_bytes() == b"still open"
        finally:
            client.close()
            accepted[0].close()
