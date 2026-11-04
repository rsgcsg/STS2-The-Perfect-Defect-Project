"""Actor CPU/control semantics using real codecs, Store, sockets and Agent port.

Public states are fixtures: these tests do not establish World throughput or
learning quality and deliberately impose no numerical equivalence gate.
"""
from __future__ import annotations

import copy
import math
import threading
import time
from dataclasses import asdict

import pytest
import torch
from test_p1_input_v2 import public_state
from test_rl_loop import PRODUCER

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import FrozenObject
from spireagent.rl_exchange import Exchange, control_input
from spireagent.workbench.global_evidence_store import publish_detached_p1_model
from stpd.p1.data import digest, json_bytes
from stpd.p1.execution import MStdExecution
from stpd.p1.export import adapter_identity, export_model, runtime_projection
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.local_inference import (
    AUTH_ENV,
    SegmentConnection,
    SegmentInferenceClient,
    SegmentInferencePool,
)
from stpd.p1.local_mu import ActorInferencePool, LocalMuScorer, single_cpu_thread
from stpd.p1.model import MStd
from stpd.p1.vocabulary import public_vocabulary
from stpd.workers.checkpoint_codec import encode_checkpoint


def publish_mu(exchange, root, version, *, model=None):
    single_cpu_thread()
    if model is None:
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(100 + version)
            model = MStd(public_vocabulary(), MStdExecution(
                implementation="experimental-packed-v1", cpu_threads=1,
                scene_attention_area=67108864, text_attention_area=67108864)).eval()
            model.enable_rl4_heads(7 + version)
    model.deduplicate_text = True
    checkpoint = root / f"source-{version}"
    checkpoint.write_bytes(b"engineering fixture, no training")
    package = root / f"package-{version}"
    export_model(model, checkpoint, package, {})
    manifest = publish_detached_p1_model(exchange.store, package, PRODUCER)
    heads = exchange.store.put_bytes("checkpoint", encode_checkpoint(
        {"heads": model.rl4_heads.state_dict()}))
    head = Manifest("checkpoint", PRODUCER,
        parents=(Parent("policy", manifest.artifact_id),), payloads=(heads,),
        parameters=FrozenObject.of({"schema": "stpd/rl-behavior-heads-1"}))
    exchange.store.publish(head)
    numerical = {"precision": "fp32", "tf32": False,
        "projection": runtime_projection(model.vocabulary), "execution": asdict(model.execution),
        "adapter": adapter_identity(), "deduplicate_text": True}
    identity = {"run": exchange.run, "training_generation": "initial", "version": version,
        "updates": version, "model_artifact_id": manifest.artifact_id,
        "model_sha256": manifest.payload("weights").sha256,
        "heads_artifact_id": head.artifact_id, "heads_sha256": heads.sha256,
        "snapshot_sha256": digest(json_bytes({"weights": manifest.payload("weights").sha256,
                                               "heads": heads.sha256, "numerical": numerical})),
        "text_digest": digest(encode_checkpoint({"weights": {
            k: v for k, v in model.state_dict().items()
            if k.startswith(("text_ids.", "text_numbers.", "text_blocks.", "text_projection."))}})),
        "vocabulary_sha256": digest(json_bytes(model.vocabulary)),
        "input_spec": copy.deepcopy(model.vocabulary["input_spec"]),
        "compiler_identity": FrameCompiler(model.vocabulary).identity, **numerical}
    exchange.publish_model({**identity, "published_at": time.time(),
                            "trajectory_encoding": "short-segment-1"})
    return identity, package


class DurableControl:
    """Test capacity owner over the existing production input receipt log."""
    def __init__(self, exchange):
        self.exchange = exchange
        self.pool = None
        self.sequence = 0
        self.head = None
        self.next_lease = 0
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, value):
        with self.lock:
            operation = value["operation"]
            self.calls.append(operation)
            if operation == "status":
                return {"accepting_new_segments": True}
            original = control_input(value["input"] if operation == "receipt" else value)
            old = self.exchange.input_receipt(original)
            if operation == "receipt":
                return {"found": old is not None,
                        "response": old["response"] if old is not None else None}
            if old is not None:
                return old["response"]
            generation = self.pool.snapshots[self.pool.latest]["training_generation"]
            if value.get("training_generation") != generation:
                return {"reason": "generation_changed", "training_generation": generation}
            if operation == "begin":
                response = {"mu": self.pool.snapshots[self.pool.latest],
                            "lease": f"actor/{self.next_lease}", "server_time": time.time()}
                self.next_lease += 1
            elif operation == "episode_open":
                response = {"admitted": True, "run": self.exchange.run,
                            "training_generation": self.pool.snapshots[
                                self.pool.latest]["training_generation"]}
            elif operation == "commit":
                response = {"segment_id": value["segment_id"]}
            else:
                response = {"run_id": value.get("run_id")}
            self.sequence += 1
            record = self.exchange.append_input(original, response, sequence=self.sequence,
                previous=self.head, producer=PRODUCER)
            self.head = record["input_artifact_id"]
            return response


@pytest.fixture
def running(tmp_path, monkeypatch):
    single_cpu_thread()
    monkeypatch.setenv(AUTH_ENV, "35" * 32)
    exchange = Exchange({"kind": "local", "directory": str(tmp_path / "store")}, "w2-test")
    first, _ = publish_mu(exchange, tmp_path, 1)
    control = DurableControl(exchange)
    pool = SegmentInferencePool(public_vocabulary(), first, control=control, segment_steps=2,
                                address=("127.0.0.1", 0))
    control.pool = pool
    path = tmp_path / "rendezvous.json"
    path.write_bytes(json_bytes(pool.attachment()))
    remote = SegmentConnection({**pool.attachment(), "rendezvous": str(path)})
    scorer = LocalMuScorer(exchange, tmp_path / "mu", cache_bytes=2 * 1024**2)
    actor = ActorInferencePool(remote, scorer)
    try:
        yield actor, pool, control, exchange, first, path
    finally:
        actor.close()
        control.pool.close()


def current(index, *, forced=False):
    observation = public_state()
    observation["run"] = {"content": {"floor": 3}}
    if forced:
        observation["catalog"] = observation["catalog"][:1]
    return {"acquisition_id": f"acq-{index}", "consumption_unit": {"id": f"unit-{index}"},
            "observation": observation, "catalog": observation["catalog"]}


def consume(client, value):
    result = client.consume(value)
    if result is None:
        client.begin.result(timeout=10)
        result = client.consume(value)
    assert result is not None
    return result


def client_for(actor, tmp_path):
    return SegmentInferenceClient({**actor.attachment(), "actor": "actor", "episode": "episode"},
                                  public_vocabulary()["input_spec"], tmp_path / "trace.jsonl")


def reasonable(result):
    assert len(result.outcome_probabilities) == 51
    assert all(math.isfinite(p) and 0 <= p <= 1 for p in result.outcome_probabilities)
    assert abs(sum(result.outcome_probabilities) - 1) < 1e-5
    assert result.scores is not None and len(result.scores) == 2
    assert all(math.isfinite(p) for p in result.scores)


def test_full_mu_loading_distribution_identity_and_one_cpu_thread(running, tmp_path):
    actor, _, _, _, first, _ = running
    client = client_for(actor, tmp_path)
    try:
        assert len(consume(client, current(0))) == 2
        key = first["snapshot_sha256"]
        reasonable(actor.scorer.score_compiled(
            key, client.cached["compiled_input"], 3, policy=True))
        assert actor.scorer.identities[key] == client.mu == first
        assert actor.scorer.identities[key]["version"] == 1
        assert actor.scorer.models[key].text_cache_namespace == key
        assert next(actor.scorer.models[key].parameters()).device.type == "cpu"
        assert not any(p.requires_grad for p in actor.scorer.models[key].parameters())
        assert torch.get_num_threads() == torch.get_num_interop_threads() == 1
        assert actor.scorer.report()["threads"] == 1
        report = actor.scorer.report()
        assert report["scoring_threads"] == [(1, 1)]
        assert report["score_latency_seconds"]["population"] == report["requests"] == 2
        assert report["score_latency_seconds"]["sampled"] == 2
        assert report["score_latency_seconds"]["median"] > 0
        assert sum(report["score_latency_sample_seconds"]) == pytest.approx(report["score_seconds"])
        assert actor.scorer.text_cache.report()["accounted_bytes"] <= 2 * 1024**2
    finally:
        client.close()


def test_boundaries_skip_versions_and_forced_tail_use_old_mu(running, tmp_path, monkeypatch):
    actor, pool, _, exchange, first, _ = running
    client = client_for(actor, tmp_path)
    try:
        consume(client, current(0))
        second, _ = publish_mu(exchange, tmp_path, 2)
        latest, _ = publish_mu(exchange, tmp_path, 3)
        pool.publish(second)
        pool.publish(latest)
        assert consume(client, current(1, forced=True)) == [0.0]
        assert client.mu == first
        assert len(client.cached["outcome_probabilities"]) == 51
        old_wire = copy.deepcopy(client.cached["compiled_input"])
        consume(client, current(2))
        assert client.mu == latest and client.mu["version"] == 3
        assert second["snapshot_sha256"] not in actor.scorer.models
        # The real scorer receives the explicitly named old snapshot after
        # adopting the new one. A distinct head makes wrong-mu substitution visible.
        key = first["snapshot_sha256"]
        observed = []
        original = actor.scorer.models[key].forward_rl4
        def old_value(*args, **kwargs):
            observed.append(key)
            return original(*args, **kwargs)
        monkeypatch.setattr(actor.scorer.models[key], "forward_rl4", old_value)
        tail = actor.scorer.score_compiled(key, old_wire, 3, policy=False)
        assert observed == [key] and tail.snapshot_sha256 == key and tail.scores is None
        assert len(tail.outcome_probabilities) == 51
        # Cache rows from old mu were invalidated at adoption; tails may only
        # repopulate their own namespace while the old segment remains retained.
        assert set(namespace for namespace, _ in actor.scorer.text_cache.rows) <= {
            key, latest["snapshot_sha256"]}
        assert len(actor.scorer.load_seconds) == 2
    finally:
        client.close()


def test_cache_cold_on_adoption_and_retained_tail_released(running, tmp_path):
    actor, pool, _, exchange, first, _ = running
    client = client_for(actor, tmp_path)
    try:
        consume(client, current(0))
        assert actor.scorer.text_cache.report()["rows"] > 0
        newer, _ = publish_mu(exchange, tmp_path, 2)
        pool.publish(newer)
        consume(client, current(1))
        # Isolate adopt from the next forward to directly witness invalidation.
        actor.scorer.adopt(newer)
        assert actor.scorer.text_cache.report()["rows"] == 0
        key = first["snapshot_sha256"]
        assert key in actor.scorer.models
        with SegmentConnection(actor.attachment()) as connection:
            connection.request("commit", segment_id=client.lease, run_id="fixture", rows=2,
                               mu=first)
        assert key not in actor.scorer.models
        with pytest.raises(KeyError):
            actor.scorer.score_compiled(key, client.cached["compiled_input"], 3, policy=False)
    finally:
        client.close()


def test_default_learner_has_no_models_worker_or_remote_score_path(running):
    _, pool, _, _, first, _ = running
    assert not hasattr(pool, "models") and not hasattr(pool, "worker")
    with (SegmentConnection(pool.attachment()) as client,
          pytest.raises(ValueError, match="disabled")):
        client.request("score", snapshot_sha256=first["snapshot_sha256"])


def test_duplicate_control_original_receipt_and_changed_body_rejected(running):
    actor, _, control, _, _, _ = running
    with SegmentConnection(actor.attachment()) as client:
        first = client.request("begin", actor="actor", episode="episode", previous_lease=None)
        duplicate = client.request("begin", actor="actor", episode="episode", previous_lease=None)
        assert duplicate == first
        assert control.sequence == 1
        assert actor.scorer.references == {first["mu"]["snapshot_sha256"]: 1}
        request = {"operation": "begin", "actor": "actor", "episode": "episode",
                   "previous_lease": None}
        receipt = client.request("receipt", input=request)
        assert receipt == {"found": True, "response": first}
        commit = {"segment_id": "actor/99", "run_id": "fixture", "rows": 1, "mu": first["mu"]}
        original = client.request("commit", **commit)
        assert client.request("commit", **commit) == original
        with pytest.raises(ValueError, match="input_key_reused_with_different_request"):
            client.request("commit", **{**commit, "rows": 2})


def test_learner_restart_changes_port_pool_preserves_local_segment_and_no_redraw(
        running, tmp_path):
    actor, old, control, _, first, path = running
    client = client_for(actor, tmp_path)
    control_client = SegmentConnection({**old.attachment(), "rendezvous": str(path)})
    try:
        assert control_client.request("status")["accepting_new_segments"]
        scores = consume(client, current(0))
        requests_before = actor.scorer.requests
        old.close()  # Only the learner listener ends. Actor/client/World owner remains.
        # Local inference carries on inside the same segment during the outage.
        assert consume(client, current(1, forced=True)) == [0.]
        assert client.mu == first
        new = SegmentInferencePool(public_vocabulary(),
            first, control=control, address=("127.0.0.1", 0), segment_steps=2)
        assert new.pool_id != old.pool_id and new.address != old.address
        control.pool = new
        path.write_bytes(json_bytes(new.attachment()))
        assert control_client.request("status")["accepting_new_segments"]
        # Same acknowledged occurrence is not scored/drawn again after reconnect.
        assert client.consume(current(1, forced=True)) == [0.]
        assert actor.scorer.requests == requests_before + 1
        assert client.in_segment == 2
        assert len(scores) == 2
        consume(client, current(2))
        assert client.lease == "actor/1"
        assert control.sequence == 2
    finally:
        control_client.close()
        client.close()


def test_lost_control_reply_queries_original_receipt_and_score_unknown_not_replayed(
        running, monkeypatch):
    actor, _, control, _, _, _ = running
    with SegmentConnection(actor.attachment()) as client:
        original = client._request_once
        lost = False
        def lose(operation, fields):
            nonlocal lost
            result = original(operation, fields)
            if operation == "begin" and not lost:
                lost = True
                raise EOFError("accepted control reply lost")
            return result
        monkeypatch.setattr(client, "_request_once", lose)
        result = client.request("begin", actor="actor", episode="episode", previous_lease=None)
        assert result["lease"] == "actor/0" and control.sequence == 1
        assert control.calls.count("begin") == 1
        calls = []
        def unknown(operation, fields):
            calls.append(operation)
            raise EOFError("unknown score response")
        monkeypatch.setattr(client, "_request_once", unknown)
        with pytest.raises(EOFError):
            client.request("score", snapshot_sha256=result["mu"]["snapshot_sha256"])
        assert calls == ["score"]


def test_bad_frame_and_foreign_compiler_rejected_without_recompile(running, tmp_path, monkeypatch):
    actor, _, _, _, first, _ = running
    client = client_for(actor, tmp_path)
    try:
        consume(client, current(0))
        key = first["snapshot_sha256"]
        wire = copy.deepcopy(client.cached["compiled_input"])
        bad = copy.deepcopy(wire)
        bad["compiler"]["compiler_sha256"] = "0" * 64
        with pytest.raises(ValueError, match="compiler_identity_mismatch"):
            actor.scorer.score_compiled(key, bad, 3, policy=True)
        # Already compiled public tensors are decoded, never projected/tokenized
        # again on the resident actor's numerical side.
        def no_compile(*args, **kwargs):
            raise AssertionError("local scorer compiled an already compiled frame")
        monkeypatch.setattr(actor.scorer.compilers[key], "compile", no_compile)
        reasonable(actor.scorer.score_compiled(key, wire, 3, policy=True))
        foreign = {**actor.attachment(), "actor": "actor", "episode": "episode"}
        foreign["compiler_identity"] = {**foreign["compiler_identity"],
                                         "compiler_sha256": "0" * 64}
        with pytest.raises(ValueError, match="producer_compiler_identity_differs"):
            SegmentInferenceClient(foreign, public_vocabulary()["input_spec"], tmp_path / "bad")
    finally:
        client.close()


@pytest.mark.parametrize("changed", ["model_sha256", "heads_sha256", "snapshot_sha256",
                                      "vocabulary_sha256", "input_spec", "adapter"])
def test_wrong_weight_or_contract_identity_is_not_loaded(running, changed):
    actor, _, _, _, first, _ = running
    foreign = copy.deepcopy(first)
    foreign[changed] = "0" * 64 if isinstance(foreign[changed], str) else {"wrong": True}
    with pytest.raises(ValueError):
        actor.scorer.adopt(foreign)
    assert not actor.scorer.models


def test_restore_generation_absent_old_control_is_not_resent(running, tmp_path):
    _, old, control, _, first, path = running
    connection = SegmentConnection({**old.attachment(), "rendezvous": str(path)})
    try:
        connection.request("status")
        old.close()
        calls = []
        def restored(request):
            calls.append(request["operation"])
            if request["operation"] == "receipt":
                return {"found": False, "response": None}
            raise AssertionError("old-generation control was sent to new learner")
        identity = {**first, "training_generation": "restored"}
        new = SegmentInferencePool(public_vocabulary(),
            identity, control=restored, address=("127.0.0.1", 0), segment_steps=2)
        control.pool = new
        path.write_bytes(json_bytes(new.attachment()))
        result = connection.request("begin", actor="actor", episode="live-world",
                                    previous_lease="actor/8")
        assert result == {"reason": "generation_changed"}
        assert calls == ["receipt"]
    finally:
        connection.close()


@pytest.mark.parametrize("admitted", [True, False])
def test_restore_begin_readmits_same_episode_without_a_new_world(running, monkeypatch, admitted):
    actor, _, _, _, first, _ = running
    requests = []
    def restored_request(client, operation, **fields):
        requests.append((operation, fields))
        client.attachment.update(run=first["run"], training_generation="restored")
        if len(requests) == 1:
            return {"reason": "generation_changed"}
        if operation == "episode_open":
            return {"admitted": admitted, "run": first["run"],
                    "training_generation": "restored"}
        assert fields["episode"] == "same-live-world" and fields["previous_lease"] is None
        return {"mu": first, "lease": "actor/0"}
    monkeypatch.setattr(SegmentConnection, "request", restored_request)
    result = actor._dispatch({"operation": "begin", "actor": "actor",
                              "episode": "same-live-world", "previous_lease": "actor/8"})
    if admitted:
        assert result["lease"] == "actor/0"
        assert [operation for operation, _ in requests] == ["begin", "episode_open", "begin"]
    else:
        assert result == {"stopped": True}
        assert [operation for operation, _ in requests] == ["begin", "episode_open"]
    assert all(fields["episode"] == "same-live-world" for _, fields in requests)


def test_retained_mu_capacity_wait_releases_control_lock_for_old_commit(running, tmp_path):
    actor, pool, _, exchange, first, _ = running
    second, _ = publish_mu(exchange, tmp_path, 2)
    third, _ = publish_mu(exchange, tmp_path, 3)
    with SegmentConnection(actor.attachment()) as connection:
        connection.request("begin", actor="actor", episode="episode", previous_lease=None)
        pool.publish(second)
        connection.request("begin", actor="actor", episode="episode", previous_lease="actor/0")
        pool.publish(third)
        waiting = threading.Event()
        original_wait = actor.scorer.capacity.wait
        def witness(*args, **kwargs):
            waiting.set()
            return original_wait(*args, **kwargs)
        actor.scorer.capacity.wait = witness
        results = []
        def begin():
            with SegmentConnection(actor.attachment()) as another:
                results.append(another.request("begin", actor="actor", episode="episode",
                                               previous_lease="actor/1"))
        worker = threading.Thread(target=begin)
        worker.start()
        assert waiting.wait(5)
        with actor.scorer.lock:
            assert len(actor.scorer.models) == 2
            assert third["snapshot_sha256"] not in actor.scorer.models
        # This actual control path must progress while begin waits for capacity.
        connection.request("commit", segment_id="actor/0", run_id="fixture", rows=2, mu=first)
        worker.join(5)
        assert not worker.is_alive()
        assert results[0]["mu"] == third
        assert len(actor.scorer.models) == 2
        assert first["snapshot_sha256"] not in actor.scorer.models



def test_explicit_central_scoring_switch_cpu_smoke(monkeypatch):
    from stpd.p1.local_inference import CentralInferencePool, create_segment_inference_pool

    single_cpu_thread()
    vocabulary = public_vocabulary()
    identity = {"snapshot_sha256": "a" * 64, "version": 1}
    default = create_segment_inference_pool(vocabulary, identity, control=lambda _: {},
                                           address=("127.0.0.1", 0))
    try:
        assert type(default) is SegmentInferencePool and not hasattr(default, "models")
    finally:
        default.close()
    with torch.random.fork_rng(devices=[]):
        model = MStd(vocabulary, MStdExecution.for_plan("mstd-training-v1")).eval()
        model.enable_rl4_heads(7)
    model.requires_grad_(False)
    pool = create_segment_inference_pool(vocabulary, identity, control=lambda _: {},
        central_scoring=True, model=model, max_batch=2, window_ms=0,
        address=("127.0.0.1", 0))
    assert isinstance(pool, CentralInferencePool)
    for key, value in pool.environment().items():
        monkeypatch.setenv(key, value)
    try:
        assert pool.attachment()["role"] == "learner_central"
        assert pool.worker.is_alive()
        with SegmentConnection(pool.attachment()) as client:
            scored = client.request("score", snapshot_sha256=identity["snapshot_sha256"],
                compiled_input=FrameCompiler(vocabulary).encode(public_state()), floor=0,
                input_projection=vocabulary["input_spec"])
        assert scored["mu"] == identity and len(scored["scores"]) == 2
        assert len(scored["outcome_probabilities"]) == 51
        assert all(math.isfinite(value) for value in scored["scores"])
    finally:
        summary = pool.close()
    assert summary["requests"] == 1


@pytest.mark.parametrize("prior_begin", [False, True])
def test_restore_zero_segment_close_uses_actual_episode_admission(
    running, tmp_path, prior_begin
):
    from spireagent.workbench.rl_segment_producer import SegmentManagedExecutor
    from stpd.p1.local_mu import ActorControlConnection

    actor, old, control, exchange, first, path = running
    admission = ActorControlConnection(actor)
    episode = "zero-segment-world"
    old_input = None
    if prior_begin:
        admission.request("episode_open", actor="actor", episode=episode)
        admission.request("begin", actor="actor", episode=episode, previous_lease=None)
        old_input = {"operation": "begin", "run": exchange.run,
            "training_generation": first["training_generation"],
            "actor": "actor", "episode": episode, "previous_lease": None}
    old.close()
    second, _ = publish_mu(exchange, tmp_path, 2)
    restored = {**second, "training_generation": "restored"}
    restored_control = DurableControl(exchange)
    new = SegmentInferencePool(public_vocabulary(), restored, control=restored_control,
        address=("127.0.0.1", 0), segment_steps=2)
    restored_control.pool = control.pool = new
    path.write_bytes(json_bytes(new.attachment()))
    try:
        if prior_begin:
            admission.request("begin", actor="actor", episode=episode, previous_lease="actor/0")
        else:
            admission.request("episode_open", actor="actor", episode=episode)
        assert admission.episode_owner("actor", episode) == {
            "run": exchange.run, "training_generation": "restored"}
        if old_input is not None:
            # Historical receipt reads must not roll back the current owner.
            assert admission.request("receipt", input=old_input)["found"]
            assert admission.episode_owner("actor", episode)["training_generation"] == "restored"
            admission.request("episode_end", actor="actor", episode=episode,
                run=exchange.run, training_generation=first["training_generation"],
                run_id="historical-close", segments=[], terminal=None,
                outcome_states=[], hp_battles=[])
            assert admission.episode_owner("actor", episode)["training_generation"] == "restored"
            assert any(key[0] == "restored" for key in actor.leases)
        executor = SegmentManagedExecutor.__new__(SegmentManagedExecutor)
        executor.exchange, executor.producer, executor.admission = exchange, PRODUCER, admission
        # The observer socket and model registration still name the startup A.
        with SegmentConnection(actor.attachment()) as stale:
            executor._end_episode(stale, actor="actor", episode=episode,
                run_id="real-zero-segment-run",
                segments=([{"training_generation": first["training_generation"]}]
                          if prior_begin else []),
                terminal={"class": 2, "reward": 1.0},
                outcome_states=[{"training_generation": first["training_generation"]}],
                hp_battles=[{"states": [{"training_generation": first["training_generation"]}]}])
        original = exchange.episode_end_input("actor", episode, training_generation="restored")
        assert original["segments"] == [] and original["run_id"] == "real-zero-segment-run"
        assert original["terminal"] is None
        assert original["outcome_states"] == original["hp_battles"] == []
        assert exchange.episode_end_input("actor", episode,
            training_generation=first["training_generation"]) is None
        assert ("actor", episode) not in actor.episode_owners
        assert not actor.leases
        assert not any(actor.scorer.references.values())
    finally:
        admission.close()
