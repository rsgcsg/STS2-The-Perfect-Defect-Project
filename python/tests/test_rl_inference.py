"""Actual persistent TCP scoring, pinned mu adoption and normal Agent Close."""
from __future__ import annotations

import copy
import io
import json
import threading
from dataclasses import replace

import torch
from test_p1_input_v2 import public_state

from stpd.p1.agent import SCHEMA, serve
from stpd.p1.data import json_bytes
from stpd.p1.export import export_model, register
from stpd.p1.input import V2_INPUT_SPEC, project
from stpd.p1.local_inference import AUTH_ENV, SegmentInferenceClient, SegmentInferencePool
from stpd.p1.model import MStd
from stpd.p1.vocabulary import public_vocabulary


def model_and_pool(control):
    torch.set_num_threads(2)
    torch.manual_seed(11)
    model = MStd(public_vocabulary()).eval()
    model.enable_rl4_heads(17)
    model.requires_grad_(False)
    model.execution = replace(model.execution, implementation="experimental-packed-v1",
        microbatch_size=16, scene_attention_area=67108864, text_attention_area=67108864)
    identity = {"snapshot_sha256": "a" * 64, "version": 0, "precision": "fp32"}
    pool = SegmentInferencePool(model, identity, control=control,
        gpu_lock=threading.RLock(), max_batch=40, window_ms=2, address=("127.0.0.1", 0))
    return model, pool, identity


def test_real_scores_pin_mu_and_forced_skip_network(tmp_path, monkeypatch):
    begins = []

    def control(request):
        begins.append(request)
        key = request.get("pinned_snapshot_sha256") or pool.latest
        return {"mu": pool.snapshots[key], "lease": f"actor/{len(begins)-1:08d}"}

    inherited = "35" * 32
    monkeypatch.setenv(AUTH_ENV, inherited)
    model, pool, first = model_and_pool(control)
    assert pool.environment()[AUTH_ENV] == inherited
    for key, token in pool.environment().items():
        monkeypatch.setenv(key, token)
    trace = tmp_path / "decisions.jsonl"
    client = SegmentInferenceClient({**pool.attachment(), "actor": "actor", "episode": "one"},
                                   V2_INPUT_SPEC, trace)

    def current(index, single=False):
        value = public_state()
        if single:
            value["catalog"] = value["catalog"][:1]
        return {"acquisition_id": f"acq-{index}", "consumption_unit": {"id": f"unit-{index}"},
                "observation": value, "catalog": value["catalog"]}

    try:
        actual = client.consume(current(0))
        with torch.no_grad():
            expected = model.forward_batch([
                project(public_state(), input_projection=V2_INPUT_SPEC)])[0]
        torch.testing.assert_close(torch.tensor(actual), expected, atol=1e-5, rtol=1e-5)
        updated = copy.deepcopy(model)
        with torch.no_grad():
            updated.head[2].weight.mul_(1.5)
        second = {**first, "snapshot_sha256": "b" * 64, "version": 1}
        pool.publish(updated, second)
        for index in range(1, 64):
            assert client.consume(current(index, single=True)) == [0.0]
        assert client.mu == first and len(begins) == 1
        repeated = current(63, single=True)
        repeated["acquisition_id"] = "acq-repeated"
        assert client.consume(repeated) == [0.0]
        assert client.mu == first and len(begins) == 1 and client.in_segment == 64
        actual = client.consume(current(64))
        assert client.mu == second and len(begins) == 2
        with torch.no_grad():
            expected = updated.forward_batch([
                project(public_state(), input_projection=V2_INPUT_SPEC)])[0]
        torch.testing.assert_close(torch.tensor(actual), expected, atol=1e-5, rtol=1e-5)
    finally:
        client.close()
        report = pool.close()
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(records) == 66
    assert all(record["mu"] == first for record in records[:65])
    assert records[64]["lease"] == records[63]["lease"]
    assert records[65]["mu"] == second
    assert sum(record["network_request"] for record in records) == report["requests"] == 2
    assert all(record["outcome_probabilities"] is None for record in records[1:65])
    assert report["errors"] == 0


def test_normal_consume_ack_next_closes_at_budget_without_a_synthetic_action(tmp_path, monkeypatch):
    begins = []

    def control(request):
        begins.append(request)
        return {"stopped": True}

    model, pool, _ = model_and_pool(control)
    checkpoint = tmp_path / "checkpoint"
    checkpoint.write_bytes(b"unit fixture; no trained artifact")
    package, owner = tmp_path / "package", tmp_path / "private"
    export_model(model, checkpoint, package, {})
    register(package, owner, {"schema": "sts2.policy-runtime/agent-manifest-1",
        "input": {"profile": "p1-global-r1", "global": {"revision": "r1.5"}}})
    attachment = owner / "inference.json"
    attachment.write_bytes(json_bytes({**pool.attachment(), "actor": "actor", "episode": "one"}))
    for key, token in pool.environment().items():
        monkeypatch.setenv(key, token)
    manifest = json.loads((owner / "manifest.json").read_bytes())
    output = io.StringIO()
    state = public_state()
    envelope = {"schema": SCHEMA, "session_id": "one", "recovery_epoch": 0}
    value = {"acquisition_id": "a", "input_spec": manifest["input"]["input_spec"],
        "continuity_token": "one", "previous_consumption_id": None,
        "observation": state, "catalog": state["catalog"], "consumption_unit": {"id": "one"}}

    def messages():
        yield json.dumps({**envelope, "request_id": "a", "message_type": "consume", "input": value})
        completion = json.loads(output.getvalue().splitlines()[-1])["completion"]
        yield json.dumps({**envelope, "request_id": "a", "message_type": "consume_ack",
                          "completion": completion})
        yield json.dumps({**envelope, "request_id": "b", "message_type": "next",
                          "input": {"continuity_token": "one"}})

    try:
        serve(owner / "config.json", owner / "manifest.json", owner, messages(), output,
              local_inference=attachment)
    finally:
        report = pool.close()
    result = json.loads(output.getvalue().splitlines()[-1])
    assert result["output"]["directive"] == {
        "type": "close", "reason": "training_segment_budget_complete"}
    assert len(begins) == 1 and report["requests"] == 0
