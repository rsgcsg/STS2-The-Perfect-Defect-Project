"""Real TCP/normal M-std scoring from Actor-compiled public inputs, without World."""
from __future__ import annotations

import copy
import io
import json
import threading
import time

import pytest
import torch
from test_p1_input_v2 import public_state

from spireagent.json_boundary import BoundaryError
from stpd.p1 import local_inference
from stpd.p1.agent_driver import SCHEMA as DRIVER_SCHEMA
from stpd.p1.agent_driver import serve_session
from stpd.p1.data import digest, json_bytes
from stpd.p1.execution import MStdExecution
from stpd.p1.export import input_spec
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.input import V2_INPUT_SPEC, project
from stpd.p1.local_inference import (
    SEGMENT_SCHEMA,
    SegmentConnection,
    SegmentInferenceClient,
    SegmentInferencePool,
)
from stpd.p1.model import MStd
from stpd.p1.tensor_input import TensorFrame
from stpd.p1.vocabulary import public_vocabulary


def member():
    torch.set_num_threads(2)
    torch.manual_seed(31)
    model = MStd(public_vocabulary(), MStdExecution.for_plan("mstd-training-v1")).eval()
    model.enable_rl4_heads(7)
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    return model, {"snapshot_sha256": "a" * 64, "version": 0}


def owned_pool(model, identity, monkeypatch, *, compiler_identity=None):
    controls = []

    def control(request):
        controls.append(request)
        assert request["operation"] == "begin"
        return {"mu": identity, "lease": f"actor/{len(controls)-1}", "server_time": time.time()}

    pool = SegmentInferencePool(model, identity, control=control, gpu_lock=threading.RLock(),
        max_batch=2, window_ms=2, address=("127.0.0.1", 0), segment_steps=8,
        compiler_identity=compiler_identity)
    for key, value in pool.environment().items():
        monkeypatch.setenv(key, value)
    return pool, controls


def current_input(observation, unit="unit-1"):
    return {"acquisition_id": f"acq-{unit}", "consumption_unit": {"id": unit},
            "observation": observation, "catalog": observation["catalog"]}


def consume(client, value):
    scores = client.consume(value)
    if scores is None:
        client.begin.result(timeout=5)
        scores = client.consume(value)
    assert scores is not None
    return scores


def test_actor_compiles_once_including_forced_and_server_only_decodes(tmp_path, monkeypatch):
    model, identity = member()
    pool, controls = owned_pool(model, identity, monkeypatch)
    attachment = {**pool.attachment(), "actor": "actor", "episode": "episode"}
    assert attachment["schema"] == SEGMENT_SCHEMA == "stpd/rl-inference-2"
    assert attachment["vocabulary"] == model.vocabulary
    client = SegmentInferenceClient(attachment, V2_INPUT_SPEC, tmp_path / "behavior.jsonl")
    encodes, requests = [], []
    encode, request = client.compiler.encode, client.request

    def encoded(observation):
        encodes.append(observation)
        return encode(observation)

    def requested(operation, **fields):
        requests.append((operation, fields))
        return request(operation, **fields)

    monkeypatch.setattr(client.compiler, "encode", encoded)
    monkeypatch.setattr(client, "request", requested)

    def no_compile(*args, **kwargs):
        raise AssertionError("server projected/tokenized/recompiled Actor input")

    monkeypatch.setattr(local_inference, "project", no_compile)
    monkeypatch.setattr(pool.compiler, "compile", no_compile)
    monkeypatch.setattr(pool.compiler._encoding, "tokenize", no_compile)
    forced = public_state()
    forced["catalog"] = forced["catalog"][:1]
    observation = public_state()
    observation["run"] = {"content": {"floor": 3}}
    with torch.inference_mode():
        expected = model.forward_rl4([project(observation, input_projection=V2_INPUT_SPEC)], [3])
    before = torch.get_rng_state().clone()
    try:
        assert consume(client, current_input(forced, "forced")) == [0.]
        forced_record = copy.deepcopy(client.cached)
        assert not forced_record["network_request"]
        frame = client.compiler.decode(forced_record["compiled_input"], observation=forced)
        assert len(frame.choices) == 1
        scores = consume(client, current_input(observation))
        assert torch.equal(torch.tensor(scores), expected[0][0])
        record = copy.deepcopy(client.cached)
        assert record["mu"] == identity
        assert record["outcome_probabilities"] == expected[1].softmax(-1)[0].tolist()
        assert record["observation_sha256"] == digest(json_bytes(observation))
        assert record["catalog_sha256"] == digest(json_bytes(observation["catalog"]))
        assert record["compile_seconds"] >= 0
        assert record["compiled_wire_bytes"] == len(json_bytes(record["compiled_input"]))
        repeated = {**current_input(observation), "acquisition_id": "same-unit-new-acq"}
        assert client.consume(repeated) == scores
        assert len(encodes) == 2 and len(controls) == 1
        scored = [fields for operation, fields in requests if operation == "score"]
        assert len(scored) == 1 and "observation" not in scored[0]
        assert scored[0]["compiled_input"] == record["compiled_input"]
        assert torch.equal(before, torch.get_rng_state())
    finally:
        client.close()
        summary = pool.close()
    assert summary["requests"] == 1
    journal = [json.loads(line) for line in (tmp_path / "behavior.jsonl").read_text().splitlines()]
    assert len(journal) == 3 and journal[-1]["score_source"] == "reused_consumption"
    assert journal[-1]["compile_seconds"] == journal[-1]["request_seconds"] == 0
    assert journal[-1]["compiled_input"] == journal[-2]["compiled_input"]


def test_explicit_cpu_producer_identity_decodes_on_a_different_torch_build(monkeypatch):
    vocabulary = public_vocabulary()
    producer = FrameCompiler(vocabulary)
    observation = public_state()
    wire = producer.encode(observation)
    with monkeypatch.context() as runtime:
        runtime.setattr(torch, "__version__", str(torch.__version__) + "+different-consumer-build")
        consumer = FrameCompiler(vocabulary)
    assert producer.identity != consumer.identity
    with pytest.raises(BoundaryError, match="compiler_identity_mismatch"):
        consumer.decode(wire, observation=observation)
    decoded = consumer.decode(wire, observation=observation, expected_identity=producer.identity)
    assert isinstance(decoded, TensorFrame)
    assert decoded.choices == tuple(choice["choice_id"] for choice in observation["catalog"])
    assert consumer.encode(observation)["compiler"] == consumer.identity
    changed = copy.deepcopy(producer.identity)
    changed["vocabulary_sha256"] = "0" * 64
    with pytest.raises(BoundaryError, match="compiler_vocabulary_mismatch"):
        consumer.decode(wire, expected_identity=changed)
    changed = copy.deepcopy(producer.identity)
    changed["input_spec"]["revision"] = "other-input"
    with pytest.raises(BoundaryError, match="compiler_vocabulary_mismatch"):
        consumer.decode(wire, expected_identity=changed)


def test_pool_freezes_cpu_identity_and_actor_rejects_foreign_actual_compiler(tmp_path, monkeypatch):
    model, identity = member()
    producer_identity = FrameCompiler(model.vocabulary).identity
    with monkeypatch.context() as runtime:
        runtime.setattr(torch, "__version__", str(torch.__version__) + "+consumer-build")
        pool, _ = owned_pool(model, identity, monkeypatch, compiler_identity=producer_identity)
    attachment = {**pool.attachment(), "actor": "actor", "episode": "episode"}
    client = None
    try:
        assert pool.compiler.identity != attachment["compiler_identity"] == producer_identity
        client = SegmentInferenceClient(attachment, V2_INPUT_SPEC, tmp_path / "behavior.jsonl")
        assert len(consume(client, current_input(public_state()))) == 2
        foreign = copy.deepcopy(attachment)
        foreign["compiler_identity"]["compiler_sha256"] = "0" * 64
        with pytest.raises(ValueError, match="producer_compiler_identity_differs"):
            SegmentInferenceClient(foreign, V2_INPUT_SPEC, tmp_path / "foreign.jsonl")
        legacy = {**attachment, "schema": "stpd/rl-inference-1"}
        with pytest.raises(ValueError, match="attachment differs"):
            SegmentInferenceClient(legacy, V2_INPUT_SPEC, tmp_path / "legacy.jsonl")
    finally:
        if client is not None:
            client.close()
        pool.close()


def test_bad_or_legacy_score_payload_is_local_and_never_recompiled(monkeypatch):
    model, identity = member()
    pool, _ = owned_pool(model, identity, monkeypatch)
    client = SegmentConnection(pool.attachment())
    compiler = FrameCompiler(model.vocabulary)
    observation = public_state()
    wire = compiler.encode(observation)
    changed = copy.deepcopy(wire)
    changed["compiler"]["compiler_sha256"] = "0" * 64
    fields = {"snapshot_sha256": identity["snapshot_sha256"], "floor": 0,
              "input_projection": V2_INPUT_SPEC}
    try:
        with pytest.raises(ValueError, match="compiler_identity_mismatch"):
            client.request("score", compiled_input=changed, **fields)
        with pytest.raises(ValueError, match="KeyError"):
            client.request("score", observation=observation, **fields)
        result = client.request("score", compiled_input=wire, **fields)
        assert len(result["scores"]) == len(observation["catalog"])
        assert result["mu"] == identity
    finally:
        client.close()
        summary = pool.close()
    assert summary["requests"] == 1


def test_normal_driver_reconsume_and_repeated_next_do_not_compile_or_draw_again(
        tmp_path, monkeypatch):
    model, identity = member()
    pool, _ = owned_pool(model, identity, monkeypatch)
    attachment = {**pool.attachment(), "actor": "actor", "episode": "episode"}
    client = SegmentInferenceClient(attachment, V2_INPUT_SPEC, tmp_path / "behavior.jsonl")
    output, draws, compiled = io.StringIO(), [], []
    original = client.compiler.encode

    def encode(observation):
        compiled.append(observation)
        return original(observation)

    monkeypatch.setattr(client.compiler, "encode", encode)
    specification = input_spec(model.vocabulary)
    manifest = {"adapter": attachment["adapter"], "artifact": {"sha256": "a" * 64},
                "input": {"input_spec": specification}}
    value = {**current_input(public_state()), "input_spec": specification,
             "continuity_token": "current", "previous_consumption_id": None}
    base = {"schema": DRIVER_SCHEMA, "session_id": "session", "recovery_epoch": 0}

    def messages():
        for index in range(2):
            yield json.dumps({**base, "request_id": f"consume-{index}",
                              "message_type": "consume", "input": value})
            completion = json.loads(output.getvalue().splitlines()[-1])["completion"]
            yield json.dumps({**base, "request_id": f"consume-{index}",
                              "message_type": "consume_ack", "completion": completion})
            if client.begin is not None:
                client.begin.result(timeout=5)
            for number in range(2):
                yield json.dumps({**base, "request_id": f"next-{index}-{number}",
                    "message_type": "next", "input": {"continuity_token": "current",
                                                          "received_cursor": "cursor"}})
            value.update(previous_consumption_id=completion["consumption_id"],
                         acquisition_id="new-acquisition")

    def select(scores, key):
        draws.append(key)
        return max(range(len(scores)), key=scores.__getitem__)

    try:
        serve_session(manifest, lambda _: [], select, messages(), output,
            reuse_selection=True, keyed_selection=True, score_input=client.consume)
    finally:
        client.close()
        summary = pool.close()
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    directives = [row["output"]["directive"] for row in replies
                  if row["message_type"] == "directive"]
    assert len(directives) == 4 and all(row["type"] == "act" for row in directives)
    assert len({row["selection"]["action_id"] for row in directives}) == 1
    assert len(draws) == len(compiled) == summary["requests"] == 1
