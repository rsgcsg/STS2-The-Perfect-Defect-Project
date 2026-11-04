"""Compiled public metadata and unchanged consumption semantics, without World."""
from __future__ import annotations

import copy
import io
import json

import pytest
import torch
from test_p1_input_v2 import public_state
from test_rl41_w2_actor import current, running  # noqa: F401

from spireagent.json_boundary import BoundaryError
from stpd.p1.agent_driver import SCHEMA as DRIVER_SCHEMA
from stpd.p1.agent_driver import serve_session
from stpd.p1.export import input_spec
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.input import V2_INPUT_SPEC
from stpd.p1.local_inference import SegmentInferenceClient
from stpd.p1.tensor_input import TensorFrame
from stpd.p1.vocabulary import public_vocabulary


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


def test_normal_driver_reconsume_and_repeated_next_do_not_compile_or_draw_again(
        running, tmp_path, monkeypatch):  # noqa: F811
    pool, _, _, _, _, _ = running
    attachment = {**pool.attachment(), "actor": "actor", "episode": "episode"}
    client = SegmentInferenceClient(attachment, V2_INPUT_SPEC, tmp_path / "behavior.jsonl")
    output, draws, compiled = io.StringIO(), [], []
    original = client.compiler.encode

    def encode(observation):
        compiled.append(observation)
        return original(observation)

    monkeypatch.setattr(client.compiler, "encode", encode)
    specification = input_spec(pool.vocabulary)
    manifest = {"adapter": attachment["adapter"], "artifact": {"sha256": "a" * 64},
                "input": {"input_spec": specification}}
    value = {**current(0), "input_spec": specification,
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
        summary = pool.scorer.report()
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    directives = [row["output"]["directive"] for row in replies
                  if row["message_type"] == "directive"]
    assert len(directives) == 4 and all(row["type"] == "act" for row in directives)
    assert len({row["selection"]["action_id"] for row in directives}) == 1
    assert len(draws) == len(compiled) == summary["requests"] == 1
