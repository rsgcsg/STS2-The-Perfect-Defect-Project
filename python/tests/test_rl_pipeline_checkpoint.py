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


def test_CP_with_64_production_sized_prepares_in_flight_encodes_and_restores(tmp_path,
                                                                            monkeypatch):
    """c3c died at its first CP with ~40 prepared batches in flight: each full
    request (members, every row index, the selection RNG) cost ~10k codec nodes.
    The CP now keeps a bounded receipt per request; restore never read them."""
    import hashlib
    from concurrent.futures import Future

    from spireagent.json_boundary import BoundaryError, json_bytes
    from stpd.rl import checkpoint_codec
    from stpd.rl.prepared_batch import AuxiliarySelection

    owner, config, io = binary_owner(tmp_path, monkeypatch, datasets=1)
    restored = None
    try:
        assert owner._prepare()
        template = owner.active_batch.request
        rows, members, aux = 4113, 120, 270  # c3a/c3b production dataset shape
        cut = [round(rows * i / 4) for i in range(5)]
        member = template.members[0]
        done: Future = Future()
        done.set_result(None)
        requests = [dc.replace(
            template,
            identity=dc.replace(template.identity, sequence=100 + n),
            members=tuple(dc.replace(member, segment_id=f"s{n}-{m}", rows=rows // members)
                          for m in range(members)),
            minibatch_rows=tuple(tuple(range(cut[i], cut[i + 1])) for i in range(4)),
            auxiliary=tuple(AuxiliarySelection(
                {"run_id": member.run_id, "segment_id": f"s{n}-{a}",
                 "training_generation": owner.generation, "row": a, "key": f"k{a}"},
                "terminal", 1.0, 0, 4) for a in range(aux)),
            auxiliary_minibatches=tuple(a % 4 for a in range(aux)))
            for n in range(64)]
        while owner.active is not None:
            owner._step()
        with pytest.raises(BoundaryError, match="structure_limit"):  # the c3c failure
            checkpoint_codec.encode_checkpoint(
                {"pending_requests": [dc.asdict(r) for r in requests]})
        for depth in (32, 64):
            monkeypatch.setattr(owner, "_pending_batches", lambda depth=depth: tuple(
                ({"request": r}, done) for r in requests[:depth]))
            started = time.perf_counter()
            raw = encode_state(owner._learning_state())
            seconds = time.perf_counter() - started
            cp = decode_state(raw)
            receipts = cp.input_pipeline["pending_requests"]
            assert [r["identity"]["sequence"] for r in receipts] == list(range(100, 100 + depth))
            for receipt, request in zip(receipts, requests, strict=False):
                assert receipt["sha256"] == hashlib.sha256(
                    json_bytes(dc.asdict(request))).hexdigest()
                assert (receipt["members"], receipt["rows"], receipt["auxiliary"],
                        receipt["minibatches"]) == (members, members * (rows // members), aux, 4)
            print(json.dumps({"prepared_in_flight": depth, "cp_bytes": len(raw),
                              "encode_seconds": round(seconds, 3)}))
        # Restore is unchanged: committed state and RNG only, no hot input replayed.
        owner.checkpoint()
        owner._collect_checkpoint(wait=True)
        record, cp = read_cp(owner)
        assert len(cp.input_pipeline["pending_requests"]) == 64
        cold = open_peer({"kind": "local", "directory": str(tmp_path / "new-live/store")})
        copy_artifact(owner.exchange.store, cold, record["checkpoint_artifact_id"],
                      scratch=tmp_path)
        restored = SegmentLearner(dc.replace(config, peer=owner.config.peer,
            start_package=None, resume_checkpoint=record["checkpoint_artifact_id"],
            segments=dc.replace(config.segments, training_generation="g-new")),
            tmp_path / "restored", dc.replace(io, dataplane={**io.dataplane,
                "root": str(tmp_path / "new-live")}))
        assert restored.rng.getstate() == cp.rng.learner_random
        assert restored._pending_batches() == () and restored.updates == cp.counters.updates
    finally:
        monkeypatch.undo()
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


def _dispatcher(workers):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    return SimpleNamespace(
        preparation_dispatch=ThreadPoolExecutor(max_workers=workers),
        dispatch_order=threading.Condition(), dispatch_issued=0, dispatch_turn=0)


def test_parallel_dispatch_submits_in_issue_order_and_isolates_failures():
    import threading
    from concurrent.futures import Future

    fake = _dispatcher(4)
    submitted, gates = [], [threading.Event() for _ in range(6)]

    def prefetch(i):
        def run():
            assert gates[i].wait(10)
            if i == 2:
                raise ValueError("prefetch_failed_2")
        return run

    def submit(i):
        def run():
            submitted.append(i)
            if i == 4:
                raise RuntimeError("submit_failed_4")
            done = Future()
            done.set_result(i)
            return done
        return run

    try:
        results = [SegmentLearner._dispatch_preparation(fake, prefetch(i), submit(i))
                   for i in range(6)]
        # Later copies finish first; their submissions still wait for earlier turns.
        for i in (3, 1, 5, 2, 0, 4):
            gates[i].set()
        assert [r.result(timeout=10) if i not in (2, 4) else None
                for i, r in enumerate(results)] == [0, 1, None, 3, None, 5]
        with pytest.raises(ValueError, match="prefetch_failed_2"):
            results[2].result()
        with pytest.raises(RuntimeError, match="submit_failed_4"):
            results[4].result()
        assert submitted == [0, 1, 3, 4, 5] and fake.dispatch_turn == 6
    finally:
        for gate in gates:
            gate.set()
        fake.preparation_dispatch.shutdown(wait=True)


def test_parallel_dispatch_overlaps_prefetches():
    import threading
    from concurrent.futures import Future

    fake = _dispatcher(3)
    barrier = threading.Barrier(3, timeout=10)  # breaks unless 3 copies run at once

    def submit():
        done = Future()
        done.set_result(None)
        return done

    try:
        results = [SegmentLearner._dispatch_preparation(fake, barrier.wait, submit)
                   for _ in range(3)]
        assert [r.result(timeout=15) for r in results] == [None] * 3
    finally:
        fake.preparation_dispatch.shutdown(wait=True)


def test_parallel_dispatch_keeps_real_batch_order_and_timings(tmp_path, monkeypatch):
    import threading

    owner, _, _ = binary_owner(tmp_path, monkeypatch, datasets=3,
                               dataplane={"prepare_dispatch_workers": 3})
    assert owner.preparation_dispatch._max_workers == 3
    release_first = threading.Event()
    order: list[int] = []
    try:
        original_prefetch = owner._prefetch_members
        first = []

        def delayed(members, **kwargs):
            if not first:
                first.append(True)
                assert release_first.wait(15)
            return original_prefetch(members, **kwargs)

        original_submit = owner.input_preparer.submit

        def recorded(request, *args, **kwargs):
            order.append(request.identity.sequence)
            return original_submit(request, *args, **kwargs)

        monkeypatch.setattr(owner, "_prefetch_members", delayed)
        monkeypatch.setattr(owner.input_preparer, "submit", recorded)
        owner._prepare_ahead()
        pending = owner._pending_batches()
        assert len(pending) >= 2
        time.sleep(.5)
        assert order == []  # later copies done; their submissions wait for the first
        release_first.set()
        batches = [future.result(timeout=60) for _, future in pending]
        assert order == sorted(order) and len(order) == len(pending)
        for (active, _), batch in zip(pending, batches, strict=True):
            assert batch.request == active["request"]
            for key in ("frontend_prefetch_credit_wait_seconds",
                        "frontend_prefetch_copy_seconds", "frontend_order_wait_seconds"):
                assert batch.timings[key] >= 0
        assert batches[1].timings["frontend_order_wait_seconds"] > .3
    finally:
        release_first.set()
        owner.close()


def test_invalid_prepare_dispatch_workers_fail_loudly(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="invalid_prepare_dispatch_workers"):
        binary_owner(tmp_path, monkeypatch, datasets=1,
                     dataplane={"prepare_dispatch_workers": 0})
