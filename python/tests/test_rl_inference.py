"""Normal Agent Close from the control listener without a synthetic action."""
from __future__ import annotations

import io
import json
import time
from dataclasses import replace

import torch
from test_p1_input_v2 import public_state

from stpd.p1.agent import SCHEMA, serve
from stpd.p1.data import json_bytes
from stpd.p1.export import export_model, register
from stpd.p1.local_inference import SegmentInferencePool
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
    pool = SegmentInferencePool(model.vocabulary, identity, control=control,
        address=("127.0.0.1", 0))
    return model, pool, identity


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
        # A real Runtime may next/await before the single stopped begin reply
        # arrives. Keep that normal protocol responsive; it must never act.
        deadline = time.monotonic() + 10
        request = 0
        while True:
            request += 1
            yield json.dumps({**envelope, "request_id": f"next-{request}",
                "message_type": "next",
                "input": {"continuity_token": "one", "received_cursor": "known-cursor"}})
            directive = json.loads(output.getvalue().splitlines()[-1])["output"]["directive"]
            if directive["type"] == "close":
                break
            assert directive["type"] == "await"  # Never a fabricated act.
            assert time.monotonic() < deadline, "normal stopped begin reply did not arrive"
            time.sleep(.01)

    try:
        serve(owner / "config.json", owner / "manifest.json", owner, messages(), output,
              local_inference=attachment)
    finally:
        report = pool.close()
    result = json.loads(output.getvalue().splitlines()[-1])
    assert result["output"]["directive"] == {
        "type": "close", "reason": "training_segment_budget_complete"}
    assert len(begins) == 1 and report["requests"] == 0
