"""W3 semantics on the real compiler, Exchange hot slots and original save owner.

Public observations and Runtime transitions are synthetic; no World or training.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import pytest
from test_p1_input_v2 import public_state
from test_rl_loop import PRODUCER
from test_rl_segment_store import fixture

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.rl_contracts import MuScore
from spireagent.rl_exchange import Exchange, _input_artifacts, control_input, input_key
from spireagent.storage.blobs import StoreError
from spireagent.storage.store import copy_artifact
from spireagent.workbench.managed_retention import preservation_route, training_audit_selected
from spireagent.workbench.managed_segment_store import read_segment, validate_hot_segment
from spireagent.workbench.rl_segment_producer import OriginalBarrier, SegmentAssembler
from stpd.p1.data import json_bytes as input_bytes
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl.segments import AuxiliaryPool


class Scorer:
    """Only numeric inference is replaced; records bind its exact requested mu."""
    def __init__(self):
        self.retained = {}
        self.calls = []

    def retain_mu(self, key):
        self.retained[key] = self.retained.get(key, 0) + 1

    def release_mu(self, key):
        self.retained[key] -= 1

    def score_compiled(self, key, frame_wire, floor, *, policy):
        assert self.retained[key] > 0
        assert policy is False
        self.calls.append((key, floor))
        return MuScore(key, None, (0.0, 1.0, *([0.0] * 49)))


def hot_fixture(tmp_path, count=4, *, terminal=False, hp=False, delayed_reward=False):
    exchange, mu, run, trace, rows = fixture(tmp_path, count, terminal=terminal)
    mu.update(run=exchange.run, training_generation="generation-a")
    compiler = FrameCompiler(public_vocabulary())
    inputs = [rows[0]["input"], *(row["successor"] for row in rows)]
    for index, value in enumerate(inputs):
        state = public_state()
        state["run"] = {"content": {"floor": 3 if index < count else 4}}
        state["player"] = {"content": {"hp": 20}}
        if hp:
            state["combat"] = {
                "availability": "available"
                if index < count - int(delayed_reward)
                else "unavailable"
            }
            state["scene"] = {"native_kind": "combat"}
            if index == count:
                state["combat"]["availability"] = "unavailable"
                state["scene"] = {"native_kind": "reward_card"}
                state["player"] = {"content": {"hp": 57}}
        if terminal and index == count:
            state["status"] = "terminal"
        value.update(observation=state, catalog=state["catalog"])
    with trace.open("wb") as stream:
        for i, row in enumerate(rows):
            value = row["input"]
            scores = [0.0] * len(value["catalog"])
            behavior = {"mu": mu, "lease": f"actor/{i // 2}", "scores": scores,
                "floor": 3, "acquisition_id": value["acquisition_id"],
                "input_sha256": hashlib.sha256(input_bytes(value)).hexdigest(),
                "catalog_sha256": hashlib.sha256(input_bytes(value["catalog"])).hexdigest(),
                "compiled_input": compiler.encode(value["observation"]),
                "outcome_probabilities": [1.0, *([0.0] * 50)]}
            stream.write(json_bytes(behavior))
            row["directive"]["directive"].update(
                selection={"action_id": value["catalog"][0]["choice_id"]}, scores={"values": scores}
            )
    scorer = Scorer()
    delivered = []

    def commit(segment, originals):
        assert originals == b""  # No archive is produced on the new edge.
        ref = exchange.put_hot_segment(segment, owner="episode:episode-a")
        exchange.retain_hot_payload(ref, "ppo:" + segment["segment_id"])
        delivered.append((copy.deepcopy(segment), ref))
        return {"segment_artifact_id": "hot:" + ref.slot, "payload": asdict(ref),
                "training_generation": segment["training_generation"]}

    assembler = SegmentAssembler({"intent_id": "episode-a",
        "behavior_policy": {"temperature": 1.0, "uniform_mix": 0.0}},
        OriginalBarrier(run, trace), commit, compiler=compiler, scorer=scorer, step_limit=2,
        generation_discarded=exchange.hot_generation_discarded)
    return exchange, assembler, scorer, rows, delivered


def test_sampling_is_per_open_identity_not_result_depth_or_small_ledger():
    selected = []
    for seed in range(2000):
        row = {"seed": str(seed), "selection_id": "policy", "manifest_sha256": "b" * 64}
        decision = training_audit_selected(row)
        selected.append(decision)
        for result in ("win", "loss", "unknown"):
            assert (
                training_audit_selected(
                    {
                        **row,
                        "agent_task_outcome": {
                            "terminal_observation": {"status": "observed", "result": result}
                        },
                        "execution_status": "unknown" if result == "unknown" else "finished",
                        "game_metrics": {"max_observed_floor": 999},
                    }
                )
                is decision
            )
    # Not top-k, and a single ordinary slot is allowed to have zero audits.
    assert 65 <= sum(selected) <= 135
    assert False in selected[:10]


@pytest.mark.parametrize("result", ["win", "loss"])
@pytest.mark.parametrize("audit_every", [20, 100])
def test_new_natural_training_routes_do_not_store_unsampled_originals(result, audit_every):
    retention = {"tier": "trajectory", "purpose": "training", "audit_every": audit_every}
    row = {"trajectory_encoding": "short-segment-1", "execution_status": "finished",
        "cleanup_accepted": True, "training_audit_selected": False,
        "agent_task_outcome": {"terminal_observation": {"status": "observed", "result": result}},
        "seed": "", "selection_id": "policy", "manifest_sha256": "b" * 64}
    row["seed"] = next(
        str(i) for i in range(100) if not training_audit_selected(
            {**row, "seed": str(i)}, audit_every=audit_every)
    )
    assert preservation_route(retention, row) == "ledger_only"
    sampled = {**row, "training_audit_selected": True}
    sampled["seed"] = next(
        str(i) for i in range(1000) if training_audit_selected(
            {**row, "seed": str(i)}, audit_every=audit_every)
    )
    assert preservation_route(retention, sampled) == "full_peer"
    with pytest.raises(BoundaryError, match="selection_changed"):
        preservation_route(retention, {**sampled, "training_audit_selected": False})
    for status in ("unknown", "censored", "failed", "verification_pending"):
        assert preservation_route(retention, {**row, "execution_status": status}) == "full_peer"
    assert preservation_route(retention, {**row, "cleanup_accepted": False}) == "full_peer"
    assert preservation_route(retention, {**row, "agent_task_outcome": {}}) == "full_peer"
    assert preservation_route({**retention, "purpose": "evaluation"}, row) == (
        "trajectory_and_full_peer")


def test_hot_prefix_bootstrap_uses_confirmed_successor_and_old_mu(tmp_path):
    exchange, assembler, scorer, rows, delivered = hot_fixture(tmp_path)
    try:
        for row in rows:
            assembler.transition(row)
        assert len(delivered) == 2
        segment, ref = delivered[-1]
        assert segment["bootstrap"]["floor"] == 4
        assert (
            segment["bootstrap"]["successor_input_sha256"]
            == hashlib.sha256(input_bytes(rows[-1]["successor"])).hexdigest()
        )
        assert segment["bootstrap"]["mu_snapshot_sha256"] == segment["mu"]["snapshot_sha256"]
        assert all(v == 0 for v in scorer.retained.values())
        assert exchange.read_hot_segment(ref)["rows"] == 2
        assert read_segment(exchange.store, "hot:" + ref.slot, scratch=tmp_path)["rows"] == 2
        assert not any(p.parameters.value().get("run_id") == segment["run_id"]
                       for p in (exchange.store.get_manifest(Path(k).stem)
                                 for k in exchange.blobs.keys("manifests/")))
        bad = copy.deepcopy(segment)
        bad["bootstrap"]["floor"] = 99
        with pytest.raises(BoundaryError, match="bootstrap_binding_changed"):
            validate_hot_segment(bad)
        bad = copy.deepcopy(segment)
        bad["steps"][0]["behavior"]["outcome_probabilities"] = None
        with pytest.raises(BoundaryError, match="outcome_distribution"):
            validate_hot_segment(bad)
    finally:
        assembler.close()


def test_pending_terminal_does_not_become_death_and_requires_typed_label(tmp_path):
    _, assembler, scorer, rows, delivered = hot_fixture(tmp_path, count=2, terminal=True)
    try:
        for row in rows:
            assembler.transition(row)
        assembler.finish(None)
        assert delivered == []
        label = {"class": 4, "reward": 0.5 * (4 / 48) ** 2, "typed_original": "a" * 64}
        assembler.finish(label)
        assert delivered[0][0]["terminal"] == label
        assert delivered[0][0]["bootstrap"] is None
        assert all(v == 0 for v in scorer.retained.values())
    finally:
        assembler.close()


@pytest.mark.parametrize("delayed", [False, True])
def test_hp_crosses_segments_and_uses_first_stable_reward_after_healing(tmp_path, delayed):
    _, assembler, _, rows, delivered = hot_fixture(tmp_path, hp=True, delayed_reward=delayed)
    try:
        for row in rows:
            assembler.transition(row)
        battle = delivered[-1][0]["hp_battles"][0]
        assert battle["post_hp"] == 57
        assert battle["end_step"] == 4 - int(delayed)
        assert battle["boundary_step_index"] == 3
        assert [r["source"]["key"] for r in battle["states"]] == [
            f"native-run:{i}" for i in range(4 - int(delayed))
        ]
        assert battle["states"][0]["source"]["segment_id"] == delivered[0][0]["segment_id"]
        assert (
            battle["boundary_input_sha256"]
            == hashlib.sha256(input_bytes(rows[-1]["successor"])).hexdigest()
        )
    finally:
        assembler.close()


def test_aux_replacement_eviction_and_prepare_hold_references_after_ppo_and_episode(tmp_path):
    exchange = Exchange({"kind": "local", "directory": str(tmp_path)}, "aux-test")
    pool = AuxiliaryPool(2, 2)
    live = {}
    for dataset in range(10):
        ref = exchange.put_hot_payload(
            json_bytes({"frame": dataset}), training_generation="g", owner="episode"
        )
        key = f"state-{dataset}"
        exchange.retain_hot_payload(ref, "ppo")
        exchange.retain_hot_payload(ref, "aux:" + key)
        pool.add(key, [{"key": key, "payload": asdict(ref)}])
        live[key] = ref
        # Eviction retires only that membership's reference, never a selected preparation.
        selected = pool.sample(1, random.Random(dataset), epochs=1)
        chosen = selected[0]["payload"] if selected else None
        if chosen:
            exchange.retain_hot_payload(chosen, "prepare")
        for old in set(live) - set(pool.groups):
            exchange.release_hot_payload(live.pop(old), "aux:" + old)
        exchange.release_hot_payload(ref, "ppo")
        exchange.release_hot_payload(ref, "episode")
        if chosen:
            assert json.loads(exchange.read_hot_payload(chosen))["frame"] <= dataset
            exchange.release_hot_payload(chosen, "prepare")
        assert exchange.hot_status("g")["payloads"] <= 2
    for key, ref in live.items():
        exchange.release_hot_payload(ref, "aux:" + key)
    assert exchange.hot_status("g") == {"payloads": 0, "bytes": 0, "references": 0}
    assert not list((tmp_path / ".rl-hot").rglob("*.json"))


def test_hot_read_verification_runs_outside_shared_owner_lock(tmp_path, monkeypatch):
    exchange = Exchange({"kind": "local", "directory": str(tmp_path)}, "locks")
    ref = exchange.put_hot_payload(b"hot", training_generation="g", owner="ppo")
    entered, proceed = threading.Event(), threading.Event()
    original = exchange._hot_store().get

    def blocked(store, key):
        if key == ref.slot + ".json":
            entered.set()
            assert proceed.wait(5)
        return original(key)

    from spireagent.storage.local import LocalBlobStore
    original_type = LocalBlobStore.get

    def get(store, key):
        return blocked(store, key) if store.root.name == ".rl-hot" else original_type(store, key)

    monkeypatch.setattr(LocalBlobStore, "get", get)
    with ThreadPoolExecutor(1) as worker:
        reading = worker.submit(exchange.read_hot_payload, ref)
        assert entered.wait(5)
        # This would deadlock if the slow read held the owner lock.
        exchange.retain_hot_payload(ref, "aux")
        exchange.release_hot_payload(ref, "ppo")
        proceed.set()
        assert reading.result(5) == b"hot"
    exchange.release_hot_payload(ref, "aux")
    with pytest.raises(StoreError, match="object_not_found"):
        exchange.read_hot_payload(ref)


def test_hot_mutation_of_other_payload_proceeds_and_generation_restore_still_excludes(
        tmp_path, monkeypatch):
    from types import SimpleNamespace

    import spireagent.rl_exchange as module
    from spireagent.instance_lock import instance_lock

    exchange = Exchange({"kind": "local", "directory": str(tmp_path)}, "locks")
    with monkeypatch.context() as patch:
        identifiers = iter(["1" * 32, "2" * 32])  # different fixed owner stripes
        patch.setattr(module.uuid, "uuid4", lambda: SimpleNamespace(hex=next(identifiers)))
        first = exchange.put_hot_payload(b"first", training_generation="g", owner="ppo")
        second = exchange.put_hot_payload(b"second", training_generation="g", owner="ppo")
    entered, proceed = threading.Event(), threading.Event()
    original = exchange._write_hot_state

    def blocked(store, key, state):
        if key == first.slot + ".owners.json":
            entered.set()
            assert proceed.wait(5)
        return original(store, key, state)

    monkeypatch.setattr(exchange, "_write_hot_state", blocked)
    with ThreadPoolExecutor(2) as workers:
        mutation = workers.submit(exchange.retain_hot_payload, first, "aux")
        assert entered.wait(5)
        try:
            # A slow fsync on one payload must not serialize another payload.
            workers.submit(exchange.retain_hot_payload, second, "aux").result(5)
            generation_lock = tmp_path / ".rl-hot/locks/g/owners.lock"
            with (pytest.raises(BoundaryError, match="already_running"),
                  instance_lock(generation_lock, create=False)):
                pytest.fail("restore acquired during an active payload mutation")
        finally:
            proceed.set()
        mutation.result(5)
    monkeypatch.setattr(exchange, "_write_hot_state", original)
    for ref in (first, second):
        exchange.release_hot_payload(ref, "ppo")
        exchange.release_hot_payload(ref, "aux")
    assert exchange.hot_status("g") == {"payloads": 0, "bytes": 0, "references": 0}
    assert len(list((tmp_path / ".rl-hot/locks/g").glob("owners-*.lock"))) == 2
    assert exchange.discard_hot_generation("g") == {"payloads": 0, "bytes": 0}


def test_cp_and_archived_input_log_never_pull_hot_payloads_back_into_store(tmp_path):
    peer = {"kind": "local", "directory": str(tmp_path / "archive")}
    binding = {"kind": "local", "directory": str(tmp_path / "shared"), "archive": peer}
    exchange = Exchange(binding, "cp-hot")
    ref = exchange.put_hot_payload(b"ephemeral compiled rows", training_generation="g", owner="ppo")
    request = {"operation": "commit", "run": exchange.run, "training_generation": "g",
        "segment_id": "actor/0", "run_id": "native", "rows": 1, "mu": {},
        "payload": asdict(ref), "segment_artifact_id": "hot:" + ref.slot}
    assert not _input_artifacts(request)
    record = exchange.append_input(request, {"segment_id": "actor/0"}, sequence=1,
                                   previous=None, producer=PRODUCER)
    assert not exchange.store.get_manifest(record["input_artifact_id"]).parents
    cp = Manifest(
        "checkpoint",
        PRODUCER,
        payloads=(exchange.store.put_bytes("checkpoint", b"self contained state"),),
    )
    exchange.store.publish(cp)
    exchange.publish_checkpoint(0, cp.artifact_id, sequence=1)
    assert exchange.drain_archive(10)["full_readback"]
    exchange.release_hot_payload(ref, "ppo")
    archive = Exchange(peer, exchange.run)
    assert not (tmp_path / "archive" / ".rl-hot").exists()
    cold = Exchange({"kind": "local", "directory": str(tmp_path / "empty")}, exchange.run)
    copy_artifact(archive.store, cold.store, cp.artifact_id, scratch=tmp_path)
    assert (
        cold.store.bytes(cold.store.get_manifest(cp.artifact_id).payload("checkpoint"))
        == b"self contained state"
    )
    assert not (tmp_path / "empty" / ".rl-hot").exists()
    # Original acceptance remains readable after its transient bytes are released.
    assert exchange.input_receipt(request)["response"] == {"segment_id": "actor/0"}
    new_ref = exchange.put_hot_payload(
        b"ephemeral compiled rows", training_generation="new-g", owner="ppo"
    )
    other = {**request, "training_generation": "new-g", "payload": asdict(new_ref),
             "segment_artifact_id": "hot:" + new_ref.slot}
    assert input_key(control_input(other)) != input_key(control_input(request))
    assert exchange.input_receipt(other) is None
    exchange.release_hot_payload(new_ref, "ppo")


def test_lost_hot_commit_response_never_reoffers_the_segment(tmp_path):
    _, assembler, _, rows, _ = hot_fixture(tmp_path, count=2)
    calls = []

    def lost(segment, originals):
        calls.append(segment["segment_id"])
        raise TimeoutError("accepted response was lost")

    assembler.commit_segment = lost
    try:
        with pytest.raises(TimeoutError):
            for row in rows:
                assembler.transition(row)
        with pytest.raises(BoundaryError, match="requires_reconciliation"):
            assembler.finish(None)
        assert calls == ["actor/0"]
    finally:
        assembler.close()


def test_forced_value_is_computed_with_the_retained_mu_and_multi_missing_is_rejected(tmp_path):
    _, assembler, scorer, rows, delivered = hot_fixture(tmp_path, count=2)
    value = rows[0]["input"]
    value["catalog"] = value["observation"]["catalog"] = value["catalog"][:1]
    trace = assembler.barrier.trace
    records = [json.loads(raw) for raw in trace.read_bytes().splitlines()]
    records[0].update(scores=[0.0], outcome_probabilities=None,
        input_sha256=hashlib.sha256(input_bytes(value)).hexdigest(),
        catalog_sha256=hashlib.sha256(input_bytes(value["catalog"])).hexdigest(),
        compiled_input=assembler.compiler.encode(value["observation"]))
    rows[0]["directive"]["directive"]["scores"]["values"] = [0.0]
    trace.write_bytes(b"".join(json_bytes(record) for record in records))
    try:
        for row in rows:
            assembler.transition(row)
        assert delivered[0][0]["steps"][0]["behavior"]["outcome_probabilities"][1] == 1.0
        assert scorer.calls[0] == (records[0]["mu"]["snapshot_sha256"], 3)
    finally:
        assembler.close()
    separate = tmp_path / "multi"
    separate.mkdir()
    _, assembler, _, rows, _ = hot_fixture(separate, count=2)
    trace = assembler.barrier.trace
    records = [json.loads(raw) for raw in trace.read_bytes().splitlines()]
    records[0]["outcome_probabilities"] = None
    trace.write_bytes(b"".join(json_bytes(record) for record in records))
    try:
        with pytest.raises(BoundaryError, match="decision_outcome_distribution_missing"):
            assembler.transition(rows[0])
    finally:
        assembler.close()


def test_restore_discards_only_its_new_hot_generation_and_preserves_original_ack(tmp_path):
    exchange = Exchange({"kind": "local", "directory": str(tmp_path)}, "restore-hot")
    old = exchange.put_hot_payload(b"old generation", training_generation="old-g", owner="episode")
    new = exchange.put_hot_payload(b"new generation", training_generation="new-g", owner="ppo")
    legacy = tmp_path / "existing-asset"
    legacy.write_bytes(b"never touched")
    request = {"operation": "commit", "run": exchange.run, "training_generation": "old-g",
        "segment_id": "actor/0", "run_id": "native", "rows": 1, "mu": {},
        "payload": asdict(old), "segment_artifact_id": "hot:" + old.slot}
    exchange.append_input(request, {"segment_id": "actor/0"}, sequence=1,
                          previous=None, producer=PRODUCER)
    before = exchange.input_receipt(request)
    # A process died after writing new slot bytes but before publishing ownership.
    store = exchange._hot_store()
    store.put_if_absent(exchange.run + "/old-g/" + "f" * 32 + ".json", b"orphan")
    discarded = exchange.discard_hot_generation("old-g")
    assert discarded["payloads"] == 2
    assert exchange.hot_status("old-g")["payloads"] == 0
    assert exchange.read_hot_payload(new) == b"new generation"
    assert exchange.input_receipt(request) == before
    assert legacy.read_bytes() == b"never touched"
    exchange.release_hot_payload(old, "episode")  # Explicitly revoked by restore.
    with pytest.raises(BoundaryError, match="discarded_on_restore"):
        exchange.put_hot_payload(b"late old segment", training_generation="old-g", owner="episode")
    assert any(e.get("stage") == "discarded_on_restore" for e in exchange.event_records())


def test_native_and_behavior_originals_are_both_preserved_before_new_local_release(tmp_path):
    from spireagent.workbench.reference_suite import _Artifacts
    from spireagent.workbench.rl_segment_producer import SegmentManagedExecutor

    exchange = Exchange({"kind": "local", "directory": str(tmp_path / "store")}, "full-original")
    peer = Exchange({"kind": "local", "directory": str(tmp_path / "peer")}, exchange.run)
    original = Manifest("analysis", PRODUCER,
        payloads=(exchange.store.put_bytes("archive", b"synthetic failed native prefix"),))
    exchange.store.publish(original)
    copy_artifact(exchange.store, peer.store, original.artifact_id, scratch=tmp_path)
    artifacts = _Artifacts(tmp_path / "attempt")
    run = artifacts.root / "run"
    run.mkdir()
    (run / "original.bin").write_bytes(b"original")
    registration = artifacts.root / "registration"
    registration.mkdir()
    trace = registration / "behavior-decisions.jsonl"
    raw = b'{"mu":"actual per-step mu"}\n'
    trace.write_bytes(raw)
    (registration / "model.tensor-tree").write_bytes(b"new registration copy")
    old = tmp_path / "old-run"
    old.mkdir()
    (old / "original.bin").write_bytes(b"old asset")
    executor = SegmentManagedExecutor.__new__(SegmentManagedExecutor)
    executor.exchange, executor.store, executor.peer, executor.producer = (
        exchange,
        exchange.store,
        peer.store,
        PRODUCER,
    )
    executor.new_originals = {"native": (run, trace)}
    row = {
        "run_id": "native",
        "stored_failure_id": original.artifact_id,
        "segment_stream_close": {
            "segments": [
                {
                    "original_commit": {
                        "behavior_trace_bytes": len(raw),
                        "behavior_trace_prefix_sha256": hashlib.sha256(raw).hexdigest(),
                    }
                }
            ]
        },
    }
    executor._preserve_behavior(row, artifacts)
    receipt = json.loads((artifacts.root / row["behavior_preservation"]["path"]).read_bytes())
    observed = peer.store.get_manifest(receipt["artifact_id"])
    assert peer.store.bytes(observed.payload("behavior")) == raw
    assert observed.parents[0].artifact_id == original.artifact_id
    executor._release_new_originals(row, run)
    assert not run.exists() and not trace.exists()
    assert not (registration / "model.tensor-tree").exists()
    assert (old / "original.bin").read_bytes() == b"old asset"


def test_hot_records_and_lineage_do_not_join_two_restore_generations(tmp_path):
    exchange, assembler, _, rows, delivered = hot_fixture(tmp_path, count=4)
    trace = assembler.barrier.trace
    records = [json.loads(raw) for raw in trace.read_bytes().splitlines()]
    for record in records[2:]:
        record["mu"] = {
            **record["mu"],
            "training_generation": "generation-b",
            "snapshot_sha256": "b" * 64,
        }
        record["lease"] = "actor/0"
    trace.write_bytes(b"".join(json_bytes(record) for record in records))
    try:
        for row in rows:
            assembler.transition(row)
        old, new = (value[0] for value in delivered)
        assert old["training_generation"] == "generation-a"
        assert new["training_generation"] == "generation-b"
        assert new["previous_segment_artifact_id"] is None
        assert old["bootstrap"]["mu_snapshot_sha256"] == old["mu"]["snapshot_sha256"]
        for segment, payload in delivered:
            record = {
                k: segment[k]
                for k in ("run", "training_generation", "segment_id", "run_id", "rows", "mu")
            }
            record.update(payload=asdict(payload), segment_artifact_id="hot:" + payload.slot)
            exchange.publish_segment("actor", 0, record)
            keys = exchange.shard_keys(training_generation=segment["training_generation"])
            assert len(keys) == 1
            assert exchange.segment_record(keys[0])["payload"] == asdict(payload)
    finally:
        assembler.close()


def test_label_delivery_failure_keeps_full_original_without_replaying_world(tmp_path, monkeypatch):
    import shutil
    from types import SimpleNamespace

    from test_global_evidence_store import _setup

    from spireagent.storage.config import open_store
    from spireagent.workbench import managed_pipeline
    from spireagent.workbench.managed_pipeline import PipelinedManagedExecutor
    from spireagent.workbench.reference_suite import _Artifacts

    store, producer, original, expected, model, _, _ = _setup(tmp_path, monkeypatch)
    executor = PipelinedManagedExecutor.__new__(PipelinedManagedExecutor)
    executor.store, executor.producer = store, producer
    executor.peer = open_store(str(tmp_path / "peer"))
    executor.runtime = Path(expected["global_runtime_directory"])
    executor.preparation = {"store": str(tmp_path / "store")}
    executor.configuration = {"models": {"m": {"artifact_id": model.artifact_id,
        "package_directory": str(tmp_path / "model")}},
        "peer": {"kind": "local", "directory": str(tmp_path / "peer")},
        "host_pin": {}, "runtime_pin": {}, "initial_progress": {"progress_sha256": "c" * 64},
        "retention": {"tier": "formal", "audit_every": 100}}
    artifacts = _Artifacts(tmp_path / "attempt")
    (artifacts.root / "private").mkdir()
    run = artifacts.root / "runs/run-fixture"
    run.parent.mkdir()
    shutil.move(original, run)
    expected["run_id"] = "run-fixture"
    row = {"selection_id": "m", "seed": "fresh", "manifest_sha256": "d" * 64,
        "scenario": {"character": "Defect", "ascension": 0}, "run_id": "run-fixture",
        "status": "save_pending", "execution_status": "verification_pending",
        "cleanup_accepted": True, "verification_runtime_instance_id": "runtime",
        "retention": managed_pipeline.retention_configuration(executor.configuration)}
    row["finish"] = artifacts.put("finish.json", json_bytes({"phase": "closed"}))
    report = {"episodes": [row]}
    executor._write_job(row, artifacts, lambda: artifacts.save(report), expected, mode="sealed")
    monkeypatch.setattr(managed_pipeline, "validate_installed_package", lambda *a, **kw: None)
    # Only typed/native execution is a fixture seam. Full failure transfer is real.
    monkeypatch.setattr(
        executor, "_verify_original", lambda *a: SimpleNamespace(passed=True, value=object())
    )
    calls = []

    def lost(*a, **kw):
        calls.append(1)
        raise TimeoutError("original terminal-label ACK lost")

    monkeypatch.setattr(executor, "_record_verified_original", lost)
    result = executor.preserve_report(artifacts.root / "report.json")["episodes"][0]
    assert result["status"] == "unknown"
    assert result["reason"] == "post_verification_processing_failed"
    assert calls == [1]
    assert result["stored_failure_id"]
    receipt = json.loads((artifacts.root / result["failure_preservation"]["path"]).read_bytes())
    assert receipt["full_readback"] is True and receipt["typed_recording_qualified"] is False


def test_model_and_cp_numbers_are_scoped_by_generation_without_a_guessed_latest(tmp_path):
    exchange, mu, *_ = fixture(tmp_path, 1)
    for generation in ("first-g", "restore-g"):
        model = {**mu, "run": exchange.run, "training_generation": generation, "version": 58}
        exchange.publish_model(model)
        cp = Manifest("checkpoint", PRODUCER,
            payloads=(exchange.store.put_bytes("checkpoint", generation.encode()),))
        exchange.store.publish(cp)
        exchange.publish_checkpoint(58, cp.artifact_id, sequence=17, training_generation=generation)
        assert (
            exchange.latest_model(training_generation=generation)["training_generation"]
            == generation
        )
        assert (
            exchange.latest_checkpoint(training_generation=generation)["checkpoint_sequence"] == 17
        )
    assert exchange.latest_model() is None
    assert exchange.latest_checkpoint() is None
    assert (
        exchange.latest_checkpoint(training_generation="first-g")["checkpoint_artifact_id"]
        != exchange.latest_checkpoint(training_generation="restore-g")["checkpoint_artifact_id"]
    )


def test_failed_sample_audit_appends_finding_stops_admission_and_keeps_originals(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from sts2_platform_evidence import VerificationFinding, VerificationResult
    from sts2_platform_evidence import global_agent_session_run_evidence as evidence_owner
    from sts2_platform_evidence.agent_session_run_evidence import AgentSessionRunEvidenceVerifier

    from spireagent.workbench.reference_suite import _Artifacts
    from spireagent.workbench.rl_segment_producer import SegmentManagedExecutor

    exchange = Exchange({"kind": "local", "directory": str(tmp_path / "peer")}, "audit-stop")
    executor = SegmentManagedExecutor.__new__(SegmentManagedExecutor)
    executor.exchange = exchange
    executor.budget_id = None
    executor.admission = SimpleNamespace(request=lambda operation: {
        "run": exchange.run, "training_generation": "audit-g"})
    artifacts = _Artifacts(tmp_path / "attempt")
    run = artifacts.root / "run"
    run.mkdir()
    (run / "original.bin").write_bytes(b"must retain")
    trace = artifacts.root / "trace.jsonl"
    trace.write_bytes(b"actual journal\n")
    executor.new_originals = {"native": (run, trace)}
    row = {"run_id": "native", "training_audit_selected": True, "status": "saved"}
    row["typed_verification"] = artifacts.put("typed.json", json_bytes({"content_id": "a" * 64}))
    monkeypatch.setattr(executor, "_preserve_behavior", lambda *a: None)
    failed = VerificationResult(AgentSessionRunEvidenceVerifier.descriptor, "fail", run,
        findings=(VerificationFinding("audit_counterexample", "injected semantic finding"),))
    monkeypatch.setattr(evidence_owner, "audit_global_session", lambda *a: failed)
    with pytest.raises(BoundaryError, match="sampled_original_audit_failed"):
        executor._after_preservation(row, artifacts, run, {}, "full_peer")
    finding = json.loads((artifacts.root / row["sampling_audit"]["path"]).read_bytes())
    assert finding["findings"][0]["code"] == "audit_counterexample"
    assert exchange.stop_request(training_generation="audit-g")["reason"] == (
        "sampled_original_audit_failed")
    assert exchange.stop_request(training_generation="after-diagnosis") is None
    assert (run / "original.bin").read_bytes() == b"must retain"
    assert trace.exists()


def test_explicit_restore_discards_old_tail_before_any_bootstrap_score(tmp_path, monkeypatch):
    exchange, assembler, scorer, rows, delivered = hot_fixture(tmp_path, count=8)
    try:
        for row in rows[:2]:
            assembler.transition(row)
        generation = delivered[0][0]["training_generation"]
        exchange.discard_hot_generation(generation)

        def forbidden_score(*_args, **_kwargs):
            raise AssertionError("a revoked old tail must not ask central/local mu for bootstrap")

        def discarded(segment, originals):
            assert originals == b"" and segment["bootstrap"] is None
            assert exchange.hot_generation_discarded(segment["training_generation"])
            return {"training_generation": generation, "discarded_on_restore": True}

        monkeypatch.setattr(scorer, "score_compiled", forbidden_score)
        assembler.commit_segment = discarded
        for row in rows[2:]:
            assembler.transition(row)
        assert assembler.committed[-1]["discarded_on_restore"]
        assert assembler.committed[-1]["rows"] == 2
        assert len(assembler.committed) == 4
        assert all(segment.get("discarded_on_restore") for segment in assembler.committed[1:])
        assert len(delivered) == 1
    finally:
        assembler.close()


def test_original_observer_error_survives_finish_reconciliation_failure(tmp_path):
    from types import SimpleNamespace

    from spireagent.workbench.rl_segment_producer import LiveSegmentProducer

    producer = LiveSegmentProducer.__new__(LiveSegmentProducer)
    producer.process = SimpleNamespace(stdout=object())
    producer.error = producer.error_record = None
    producer.error_path = tmp_path / "observer-error.json"
    producer.interruption = None
    first = BoundaryError("original_commit", "test_original_io_failed", "private URL must not copy")

    def transition(_value):
        raise first

    def finish(_value):
        raise BoundaryError("rl_segment", "original_segment_commit_requires_reconciliation")

    producer.assembler = SimpleNamespace(transition=transition, finish=finish)
    producer.verifier = SimpleNamespace(events=lambda: iter([{"verified": "transition"}]))
    producer._read()
    assert producer.error is first
    record = json.loads(producer.error_path.read_bytes())
    assert record["operation"] == "transition"
    assert (record["stage"], record["code"]) == ("original_commit", "test_original_io_failed")
    assert "private URL" not in producer.error_path.read_text()
    producer.thread = SimpleNamespace(join=lambda **_: None, is_alive=lambda: False)
    with pytest.raises(BoundaryError, match="original_segment_commit_requires_reconciliation"):
        producer.wait()
    assert producer.error is first and json.loads(producer.error_path.read_bytes()) == record


def test_tail_finish_records_first_error_before_reconciliation(tmp_path):
    from types import SimpleNamespace

    from spireagent.workbench.rl_segment_producer import LiveSegmentProducer

    producer = LiveSegmentProducer.__new__(LiveSegmentProducer)
    producer.error = producer.error_record = None
    producer.error_path = tmp_path / "tail-error.json"
    producer.thread = SimpleNamespace(join=lambda **_: None, is_alive=lambda: False)
    calls = []

    def finish(_terminal):
        calls.append(True)
        if len(calls) == 1:
            raise OSError(28, "private filesystem text")
        raise BoundaryError("rl_segment", "original_segment_commit_requires_reconciliation")

    producer.assembler = SimpleNamespace(finish=finish)
    with pytest.raises(OSError):
        producer.finish(None)
    record = json.loads(producer.error_path.read_bytes())
    assert record["operation"] == "tail_finish" and record["errno"] == 28
    assert "private filesystem" not in producer.error_path.read_text()
    with pytest.raises(BoundaryError, match="original_segment_commit_requires_reconciliation"):
        producer.finish(None)
    assert json.loads(producer.error_path.read_bytes()) == record


@pytest.mark.parametrize("admitted", [False, True])
def test_pre_world_cleanup_only_ends_actually_admitted_episode(monkeypatch, admitted):
    from types import SimpleNamespace

    from spireagent.workbench.rl_segment_producer import (
        PipelinedManagedExecutor,
        SegmentManagedExecutor,
    )

    executor = SegmentManagedExecutor.__new__(SegmentManagedExecutor)
    executor.actor = "actor"
    executor.admission = SimpleNamespace()
    monkeypatch.setattr(PipelinedManagedExecutor, "__call__", lambda *a: None)
    calls = []
    executor._end_episode = lambda *_args, **fields: calls.append(fields)
    row = {"cleanup_accepted": True, "intent_id": "no-world"}
    if admitted:
        row["segment_episode_admitted"] = True
    executor(row, {}, None, lambda: None)
    assert len(calls) == int(admitted)


def test_one_percent_unsampled_original_never_enters_full_archive_path(tmp_path, monkeypatch):
    import shutil
    from types import SimpleNamespace

    from test_global_evidence_store import _setup

    from spireagent.workbench import managed_pipeline
    from spireagent.workbench.managed_pipeline import PipelinedManagedExecutor
    from spireagent.workbench.reference_suite import _Artifacts

    store, producer, original, expected, model, _, _ = _setup(tmp_path, monkeypatch)
    executor = PipelinedManagedExecutor.__new__(PipelinedManagedExecutor)
    executor.store, executor.producer = store, producer
    executor.runtime = Path(expected["global_runtime_directory"])
    executor.preparation = {"store": str(tmp_path / "store")}
    executor.configuration = {"models": {"m": {"artifact_id": model.artifact_id,
        "package_directory": str(tmp_path / "model")}},
        "peer": {"kind": "local", "directory": str(tmp_path / "peer")},
        "host_pin": {}, "runtime_pin": {}, "initial_progress": {"progress_sha256": "c" * 64},
        "retention": {"tier": "trajectory", "purpose": "training", "audit_every": 100}}
    artifacts = _Artifacts(tmp_path / "attempt")
    (artifacts.root / "private").mkdir()
    run = artifacts.root / "runs/run-fixture"
    run.parent.mkdir()
    shutil.move(original, run)
    expected["run_id"] = "run-fixture"
    row = {"selection_id": "m", "manifest_sha256": "d" * 64,
        "scenario": {"character": "Defect", "ascension": 0}, "run_id": "run-fixture",
        "status": "save_pending", "execution_status": "verification_pending",
        "cleanup_accepted": True, "verification_runtime_instance_id": "runtime",
        "trajectory_encoding": "short-segment-1",
        "retention": managed_pipeline.retention_configuration(executor.configuration)}
    row["seed"] = next(str(i) for i in range(100) if not training_audit_selected(
        {**row, "seed": str(i)}, audit_every=100))
    row["training_audit_selected"] = False
    row["finish"] = artifacts.put("finish.json", json_bytes({"phase": "closed"}))
    report = {"episodes": [row]}
    executor._write_job(row, artifacts, lambda: artifacts.save(report), expected, mode="sealed")
    monkeypatch.setattr(managed_pipeline, "validate_installed_package", lambda *a, **kw: None)
    # Replace native verification only; execute the normal preservation router.
    monkeypatch.setattr(executor, "_verify_original", lambda *a: SimpleNamespace(
        passed=True, value=SimpleNamespace(content_id="a" * 64)))

    def recorded(value, *_args):
        value.update(status="finished", reason="natural_loss", agent_task_outcome={
            "terminal_observation": {"status": "observed", "result": "loss"}})

    def forbidden(*_args, **_kwargs):
        raise AssertionError("unsampled raw must not enter native archive publication/readback")

    monkeypatch.setattr(executor, "_record_verified_original", recorded)
    monkeypatch.setattr(PipelinedManagedExecutor.__mro__[1], "_preserve", forbidden)
    result = executor.preserve_report(artifacts.root / "report.json")["episodes"][0]
    assert result["status"] == "saved" and result["preservation_route"] == "ledger_only"
    receipt = json.loads((artifacts.root / result["ledger_preservation"]["path"]).read_bytes())
    assert receipt["sampling"] == "open_identity_sha256_mod100"
    assert receipt["original_retained"] is False
    assert not result.get("stored_evidence_id") and not result.get("stored_failure_id")


def test_hot_transport_honors_64mib_reference_without_widening_artifact_blobs(tmp_path):
    from spireagent.storage.local import LocalBlobStore

    # The actual shared LocalBlobStore used by HotRef must carry its declared
    # range. Ordinary artifact chunks retain the original 16 MiB bound.
    raw = b'x' * (17 * 1024**2)
    ordinary = LocalBlobStore(tmp_path / 'ordinary')
    with pytest.raises(StoreError, match='blob_exceeds_bounded_transport'):
        ordinary.put_if_absent('too-large.json', raw)
    exchange = Exchange({'kind': 'local', 'directory': str(tmp_path / 'exchange')}, 'hot-bound')
    payload = exchange.put_hot_payload(raw, training_generation='g-test', owner='episode:test')
    assert payload.size == len(raw) and payload.sha256 == hashlib.sha256(raw).hexdigest()
    assert exchange.read_hot_payload(payload) == raw
    exchange.retain_hot_payload(payload, 'ppo:test')
    exchange.release_hot_payload(payload, 'episode:test')
    assert exchange.read_hot_payload(payload) == raw
    exchange.release_hot_payload(payload, 'ppo:test')
    assert exchange.hot_status('g-test')['payloads'] == 0


def test_integrity_finding_survives_unavailable_control_status(tmp_path):
    from types import SimpleNamespace

    from spireagent.workbench.rl_segment_producer import SegmentManagedExecutor

    exchange = Exchange(
        {"kind": "local", "directory": str(tmp_path / "peer")}, "audit-control-down"
    )

    def unavailable(*args, **kwargs):
        raise OSError("control unavailable")

    owner = SimpleNamespace(
        exchange=exchange, budget_id="ops-test", admission=SimpleNamespace(request=unavailable)
    )
    SegmentManagedExecutor._request_training_stop(owner, "sampled_original_audit_failed")
    events = exchange.event_records()
    assert [e["kind"] for e in events] == ["integrity_failed", "integrity_stop_pending"]
    assert events[0]["reason"] == "sampled_original_audit_failed"
    assert events[0]["budget_id"] == "ops-test"
