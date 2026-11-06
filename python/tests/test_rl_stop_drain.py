"""Original H1 end input, real CPU worker and two RPC hops; zero World/inference."""
from __future__ import annotations

import json
import os
import signal
import threading
from collections import deque
from types import SimpleNamespace

import pytest

from spireagent.artifact_contracts import Producer
from spireagent.local_diagnostics import exception_facts
from spireagent.rl_exchange import Exchange, control_input, input_key
from stpd.p1.local_inference import AUTH_ENV, SegmentConnection, SegmentInferencePool
from stpd.p1.local_mu import ActorControlConnection, ActorInferencePool
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl.batch_preparation import BatchPreparer, verify_auxiliary
from stpd.rl.segment_learner import SegmentLearner
from stpd.rl.segments import SegmentConfig, SegmentQueue


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="POSIX graceful-stop signal")
@pytest.mark.parametrize("kill", [False, True])
def test_original_empty_end_after_stop_and_real_auxiliary_worker_failure(
    tmp_path, monkeypatch, kill
):
    # Retained R3 episode-end control input, no game payload or model construction.
    request = {
        "actor": "friday-61089d6a998c-145", "episode": "f7f7434da60c49a08654bab1c7a3f82b",
        "hp_battles": [], "operation": "episode_end", "outcome_states": [],
        "run": "rl4-outcome-20261103-1", "run_id": "run-b1b8999c-9162-47e9-a093-9e5c833bd390",
        "segments": [], "terminal": None,
        "training_generation": "g-ops-0001-1793853513381702752",
        "typed_original": "d03acb18ad664c1ad895309c2106e17fc1ed76a236ef21266caf1d03a3e1f23c",
    }
    assert input_key(control_input(request)) == (
        "d680191f9ea56758ecdbebf330d4136fdddf20c6bfcdb0c8e4e0a7a6e059c7d1")
    monkeypatch.setenv(AUTH_ENV, "53" * 32)
    vocab = public_vocabulary()
    owner = object.__new__(SegmentLearner)
    owner.config = SimpleNamespace(run=request["run"], operating_budget=None)
    owner.generation = request["training_generation"]
    owner.input_gate = threading.RLock()
    owner.source_prefetch_gate = threading.RLock()
    owner.episode_ends = {}
    owner.ahead_batches = deque()
    owner.condition = threading.Condition(threading.RLock())
    owner.exchange = Exchange(
        {"kind": "local", "directory": str(tmp_path / "peer")}, request["run"])
    owner.producer = Producer("engineering/offline-H1-FIX2", "0" * 40, "0" * 64)
    owner.input_sequence, owner.input_head, owner.input_anchor = 0, None, None
    owner.input_fenced, owner.newlease_stopped = False, False
    owner.wal = None
    owner.actor_episodes, owner.retired_actors, owner.sources = {}, set(), {}
    owner.queue, owner.lease_owners, owner.hot_references = SegmentQueue(SegmentConfig()), {}, {}
    owner.active, owner.pending_batch = None, None
    owner.terminal, owner.hp = SimpleNamespace(groups={}), SimpleNamespace(groups={})
    preparer = BatchPreparer(vocab, workers=1, threads=1, peer=None, run=request["run"],
                             scratch=tmp_path)
    owner.input_preparer = preparer
    pool = SegmentInferencePool(vocab, {"snapshot_sha256": "1" * 64, "run": request["run"],
        "training_generation": request["training_generation"]}, control=owner.control,
        address=("127.0.0.1", 0))
    # No scores are called. This is the actual control path through both listeners.
    actor = ActorInferencePool(SegmentConnection(pool.attachment()),
                               SimpleNamespace(close=lambda: None))
    connection = ActorControlConnection(actor)
    try:
        assert connection.request("episode_open", actor=request["actor"],
                                  episode=request["episode"])["admitted"]
        owner.newlease_stopped = True
        owner.exchange.persist_episode_end(request, owner.producer)
        assert preparer.pool.submit(verify_auxiliary, request, ()).result(timeout=20) == []
        # A genuine worker validation exception retains its original worker frame
        # after ProcessPool pickling, without relying on textual RemoteTraceback.
        with pytest.raises(ValueError) as invalid:
            preparer.pool.submit(verify_auxiliary,
                {**request, "outcome_states": [{}]}, ()).result(timeout=10)
        worker = exception_facts(invalid.value)["remote"]
        assert worker["exception_type"] == "ValueError"
        assert worker["stack"][-1]["function"] == "verify_auxiliary_sources"
        pid = preparer.pool.submit(os.getpid).result(timeout=5)
        process = preparer.pool._processes[pid]
        assert pid != os.getpid()
        fields = {key: value for key, value in request.items() if key != "operation"}
        if kill:
            manager = preparer.pool._executor_manager_thread
            assert manager is not None
            os.kill(pid, signal.SIGUSR1)
            # The executor manager owns waitpid/reaping. A competing Process.join
            # can return before that thread publishes the actual exit status.
            manager.join(timeout=5)
            assert not manager.is_alive()
            assert not process.is_alive() and process.exitcode == -signal.SIGUSR1
            with pytest.raises(ValueError) as caught:
                connection.request("episode_end", **fields)
            facts = exception_facts(caught.value)
            assert facts["remote"]["remote"]["exception_type"] == "BrokenProcessPool"
            assert {"_end_episode", "submit"} <= {
                frame["function"] for frame in facts["remote"]["remote"]["stack"]}
            raw = json.dumps(facts)
            assert "RL service episode_end failed" not in raw
            assert "A child process terminated abruptly" not in raw
            assert request["typed_original"] not in raw
        else:
            assert connection.request("episode_end", **fields)["run_id"] == request["run_id"]
        assert owner.exchange.episode_end_input(request["actor"], request["episode"],
            training_generation=request["training_generation"]) is not None
        assert (owner.exchange.input_receipt(request) is not None) is (not kill)
        assert bool(owner.actor_episodes) is kill
    finally:
        connection.close()
        actor.close()
        pool.close()
        preparer.shutdown(wait=True, cancel_futures=True)
