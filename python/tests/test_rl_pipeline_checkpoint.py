"""Real socket/WAL/preparation/SGD seams; synthetic public input, zero World."""
from __future__ import annotations

import dataclasses as dc
import json
import socket
import time

import pytest
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
from test_rl41_w1 import configuration, segment
from test_rl_segment_learner import read_cp

from spireagent.rl_socket_transport import AUTH_ENV, upload
from spireagent.storage.config import open_peer
from spireagent.storage.store import copy_artifact
from stpd.p1.local_inference import SegmentConnection
from stpd.p1.local_mu import LocalMuScorer, NodeMuCache
from stpd.rl.checkpoint_state import SCHEMA, decode_state, encode_state
from stpd.rl.segment_learner import SegmentLearner
from stpd.rl.tensor_segment import TensorSegmentEncoder
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint

# Explicit S3 SGD-process alternative for the same seams. The declared budget
# items are this CPU fixture's own bounds; production uses the module defaults.
SGD_PROCESS = {"sgd_process": True, "memory_limit_bytes": 64 * 1024**3, "memory_budget": {
    "controller_base": 1024**3, "worker_process": 256 * 1024**2,
    "publisher_process": 512 * 1024**2, "sgd_base_cpu": 1024**3}}


def binary_owner(tmp_path, monkeypatch, datasets=3, dataplane=None):
    config, io = configuration(tmp_path, monkeypatch)
    config = dc.replace(config, segments=dc.replace(config.segments, minibatches=2,
        archive_min_free_bytes=0, archive_min_free_inodes=0))
    io = dc.replace(io, dataplane={
        "lanes": 1, "root": str(tmp_path / "live"), "source_slots": 32,
        "prepare_slots": 4, "source_bytes": 64 * 1024**2,
        "preparation_workers": 2, "assembly_workers": 2, "prepared_batches": 3,
        "lock_timing": True, **(dataplane or {}),
    })
    owner = SegmentLearner(config, tmp_path / "owner", io)
    monkeypatch.setenv(AUTH_ENV, owner.pool.environment()[AUTH_ENV])
    endpoint = owner.pool.attachment()
    connection = SegmentConnection(endpoint)
    cache = NodeMuCache(endpoint, tmp_path / "node")
    scorer = LocalMuScorer(owner.exchange, tmp_path / "mu")
    try:
        for i in range(2 * datasets):
            actor, episode = f"p{i}", f"episode-{i}"
            connection.request("episode_open", actor=actor, episode=episode)
            with cache.latest() as identity:
                grant = connection.request("begin", actor=actor, episode=episode,
                    previous_lease=None, node_id=socket.gethostname(),
                    node_cached_mu_snapshot=identity["snapshot_sha256"])
            scorer.adopt(grant["mu"])
            frozen = scorer.models[grant["mu"]["snapshot_sha256"]]
            value = segment(frozen, name=grant["lease"], run_id=episode, rows=2,
                generation=owner.generation, mu=grant["mu"])
            encoder = TensorSegmentEncoder(vocabulary=owner.input_preparer.vocabulary,
                compiler_identity=owner.compiler_identity,
                recipe={**owner.pool.binary_profile["binary_recipe"], "actor": actor},
                spool=tmp_path / f"spool-{i}")
            encoded = encoder.encode(value)
            ref = upload(endpoint, encoded)
            record = {"segment_id": grant["lease"], "payload": dc.asdict(ref), "rows": 2,
                "training_generation": owner.generation,
                "segment_artifact_id": "tensor:" + ref.slot}
            connection.request("commit", **record, run=config.run, run_id=episode,
                mu=grant["mu"], envelope=encoded.envelope)
            connection.request("episode_end", actor=actor, episode=episode, run_id=episode,
                terminal=None, outcome_states=[], hp_battles=[], typed_original=None,
                unqualified=True, segments=[record])
    except BaseException:
        owner.close()
        raise
    finally:
        connection.close()
        scorer.close()
    return owner, config, io


@pytest.mark.parametrize("sgd", [False, True], ids=["in_process", "sgd_process"])
def test_CP_keeps_real_pending_requests_workers_and_selection_rng(tmp_path, monkeypatch, sgd):
    owner, config, io = binary_owner(tmp_path, monkeypatch,
                                     dataplane=SGD_PROCESS if sgd else None)
    restored = None
    try:
        assert owner._prepare()
        owner.selection_deadline = time.monotonic() - 1
        owner._prepare_ahead()
        assert not owner._pending_batches()
        owner.selection_deadline = None
        owner._prepare_ahead()
        pending = owner._pending_batches()
        assert [a["request"].identity.sequence for a, _ in pending] == [2, 3]
        ids = [{r["segment_id"] for r in a["segments"]} for a, _ in pending]
        assert not ids[0] & ids[1]
        for _, future in pending:
            future.result(timeout=30)
        while owner.active is not None:
            owner._step()
        rng, committed = owner.rng.getstate(), owner.committed_selection_rng
        pids = (set(owner.input_preparer.pool._processes),
                set(owner.input_preparer.assembly_pool._processes))
        started = time.perf_counter()
        owner.checkpoint()
        pause = time.perf_counter() - started
        assert owner._pending_batches() == pending
        assert all(not f.cancelled() and f.done() for _, f in pending)
        assert pids == (set(owner.input_preparer.pool._processes),
                        set(owner.input_preparer.assembly_pool._processes))
        assert owner.rng.getstate() == rng
        owner._collect_checkpoint(wait=True)
        record, cp = read_cp(owner)
        assert cp.rng.learner_random == committed
        assert cp.input_pipeline["speculative_selection_rng"] == rng
        assert cp.input_pipeline["committed_selection_rng"] == committed
        assert [r["identity"]["sequence"] for r in cp.input_pipeline["pending_requests"]] == [2, 3]
        print(json.dumps({"checkpoint_pause_seconds": pause, "pending_preserved": 2,
                          "pids_preserved": True, "World": 0, "SGD": owner.updates}))
        legacy = decode_checkpoint(encode_state(cp))
        assert legacy["schema"] == SCHEMA
        legacy["schema"] = "stpd/rl-segment-checkpoint-2"
        legacy.pop("input_pipeline")
        assert decode_state(encode_checkpoint(legacy)).input_pipeline is None
        with pytest.raises(ValueError, match="schema_binding_changed"):
            decode_state(encode_state(cp), expected_schema=legacy["schema"])
        # H1/v2 checkpoints remain exportable by the normal evaluation owner.
        from spireagent.artifact_contracts import Manifest
        from spireagent.json_boundary import FrozenObject
        from stpd.evaluation.rl_checkpoint import export_checkpoint

        old = Manifest("checkpoint", owner.producer,
            payloads=(owner.exchange.store.put_bytes("checkpoint", encode_checkpoint(legacy)),),
            parameters=FrozenObject.of({"schema": legacy["schema"]}))
        owner.exchange.store.publish(old)
        binding = export_checkpoint(owner.exchange.store, old.artifact_id, tmp_path / "eval-v2")
        assert binding["updates"] == cp.counters.updates
        wrong = Manifest("checkpoint", owner.producer,
            payloads=(owner.exchange.store.put_bytes("checkpoint", encode_state(cp)),),
            parameters=FrozenObject.of({"schema": legacy["schema"]}))
        owner.exchange.store.publish(wrong)
        with pytest.raises(ValueError, match="schema_binding_changed"):
            owner._read_checkpoint(wrong.artifact_id)
        with pytest.raises(ValueError, match="schema_binding_changed"):
            export_checkpoint(owner.exchange.store, wrong.artifact_id, tmp_path / "wrong-v2")
        assert owner._prepare()
        assert owner.active_batch.request.identity.sequence == 2
        cold = open_peer({"kind": "local", "directory": str(tmp_path / "new-live/store")})
        copy_artifact(owner.exchange.store, cold, record["checkpoint_artifact_id"],
                      scratch=tmp_path)
        restored = SegmentLearner(dc.replace(config, peer=owner.config.peer,
            start_package=None, resume_checkpoint=record["checkpoint_artifact_id"],
            segments=dc.replace(config.segments, training_generation="g-new")),
            tmp_path / "restored", dc.replace(io, dataplane={**io.dataplane,
                "root": str(tmp_path / "new-live")}))
        assert restored.rng.getstate() == committed
        assert restored._pending_batches() == () and not restored.queue.pending
        assert restored.updates == cp.counters.updates
    finally:
        if restored is not None:
            restored.close()
        owner.close()


@pytest.mark.parametrize("sgd", [False, True], ids=["in_process", "sgd_process"])
@pytest.mark.parametrize("cutoff", ["soft", "absolute"])
def test_cutoff_finishes_frozen_two_minibatch_dataset_and_final_CP(tmp_path, monkeypatch, cutoff,
                                                                    sgd):
    owner, _, _ = binary_owner(tmp_path, monkeypatch, datasets=1,
                               dataplane=SGD_PROCESS if sgd else None)
    assert owner._prepare()
    original_step = owner._step
    steps = []

    def step():
        result = original_step()
        steps.append(result)
        if len(steps) == 1:
            if cutoff == "soft":
                time.sleep(.15)
            else:
                owner.deadline_at = time.time() + 599
        return result

    monkeypatch.setattr(owner, "_step", step)
    if cutoff == "soft":
        owner.config = dc.replace(owner.config, wall_seconds=.1)
    else:
        owner.deadline_at = time.time() + 660
    summary = owner.run()
    assert len(steps) == 2 and summary["datasets"] == 1
    _, cp = read_cp(owner)
    assert cp.counters.updates == 2 and cp.counters.datasets == 1
    assert owner.active is None
    assert not cp.input_pipeline["pending_requests"]


@pytest.mark.parametrize("sgd", [False, True], ids=["in_process", "sgd_process"])
def test_source_frontend_wait_overlaps_real_optimizer_step(tmp_path, monkeypatch, sgd):
    import threading

    owner, _, _ = binary_owner(tmp_path, monkeypatch, datasets=2,
                               dataplane=SGD_PROCESS if sgd else None)
    entered, resume = threading.Event(), threading.Event()
    try:
        assert owner._prepare()
        original = owner._prefetch_members

        def delayed(members, **kwargs):
            entered.set()
            assert resume.wait(15)
            return original(members, **kwargs)

        monkeypatch.setattr(owner, "_prefetch_members", delayed)
        owner._prepare_ahead()
        assert entered.wait(5)
        pending = owner._pending_batches()
        assert len(pending) == 1 and not pending[0][1].done()
        before = owner.updates
        owner._step()
        assert owner.updates == before + 1 and not resume.is_set()
        resume.set()
        assert pending[0][1].result(timeout=30).request == pending[0][0]["request"]
    finally:
        resume.set()
        owner.close()
