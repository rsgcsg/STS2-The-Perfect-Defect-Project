"""Normal exported model -> authenticated local queue -> unchanged Agent driver."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import torch
from test_p1_t0 import observation

from stpd.p1.export import export_model, load_model
from stpd.p1.input import INPUT_SPEC, fit_vocabulary, project
from stpd.p1.local_inference import LocalInferenceClient, LocalInferencePool
from stpd.p1.model import MStd


def test_batched_model_preserves_actor_scores_and_bad_input_is_local(tmp_path, monkeypatch):
    value = observation()
    checkpoint = tmp_path / "checkpoint"
    checkpoint.write_bytes(b"local queue engineering fixture; no training")
    package = tmp_path / "package"
    model = MStd(fit_vocabulary([project(value)])).eval()
    descriptor = export_model(model, checkpoint, package, {"scope": "synthetic"})
    sha = descriptor["package_sha256"]
    pool = LocalInferencePool({sha: package / "model.tensor-tree"}, max_batch=4, window_ms=20)
    for key, token in pool.environment().items():
        monkeypatch.setenv(key, token)
    public_attachment = pool.attachment()
    barrier = Barrier(4)

    def actor(index):
        current = copy.deepcopy(value)
        current["catalog"][0]["label"] += str(index)
        client = LocalInferenceClient(pool.attachment(), sha, INPUT_SPEC)
        try:
            barrier.wait(timeout=10)
            return current, client(current)
        finally:
            client.close()

    try:
        with ThreadPoolExecutor(max_workers=4) as workers:
            actual = list(workers.map(actor, range(4)))
        original, _ = load_model(package / "model.tensor-tree", sha)
        with torch.inference_mode():
            for current, scores in actual:
                expected = original(project(current))
                torch.testing.assert_close(torch.tensor(scores), expected, atol=1e-5, rtol=1e-5)
                assert max(range(len(scores)), key=scores.__getitem__) == int(expected.argmax())
        bad = LocalInferenceClient(pool.attachment(), sha, INPUT_SPEC)
        good = LocalInferenceClient(pool.attachment(), sha, INPUT_SPEC)
        invalid = {**value, "revision": "foreign"}
        with ThreadPoolExecutor(max_workers=2) as workers:
            failed = workers.submit(bad, invalid)
            succeeded = workers.submit(good, value)
            with pytest.raises(ValueError, match="score failed"):
                failed.result(timeout=10)
            assert len(succeeded.result(timeout=10)) == len(value["catalog"])
        bad.close()
        good.close()
    finally:
        summary = pool.close()
    assert summary["closed"] is True
    assert summary["requests"] == 5 and summary["errors"] == 1
    assert max(summary["batch_sizes"]) > 1
    assert pool.close() == summary
    assert not pool.worker.is_alive() and not pool.acceptor.is_alive()
    # The nonsecret attachment cannot restart or authenticate a scorer.
    assert all("auth" not in key for key in public_attachment)


def test_foreign_model_or_pool_never_receives_a_score(tmp_path, monkeypatch):
    value = observation()
    checkpoint = tmp_path / "checkpoint"
    checkpoint.write_bytes(b"bound local queue fixture")
    package = tmp_path / "package"
    descriptor = export_model(MStd(fit_vocabulary([project(value)])), checkpoint, package, {})
    sha = descriptor["package_sha256"]
    pool = LocalInferencePool({sha: package / "model.tensor-tree"}, max_batch=1, window_ms=0)
    for key, token in pool.environment().items():
        monkeypatch.setenv(key, token)
    try:
        for attachment, model in [(pool.attachment(), "f" * 64),
                                  ({**pool.attachment(), "pool_id": "foreign"}, sha)]:
            client = LocalInferenceClient(attachment, model, INPUT_SPEC)
            try:
                with pytest.raises(EOFError):
                    client(value)
            finally:
                client.close()
    finally:
        summary = pool.close()
    assert summary["requests"] == 0


def test_existing_agent_consume_ack_next_is_identical_with_local_scoring(tmp_path, monkeypatch):
    import io
    import json

    from stpd.p1.agent import SCHEMA, serve
    from stpd.p1.data import json_bytes
    from stpd.p1.export import register

    value = observation()
    checkpoint = tmp_path / "checkpoint"
    checkpoint.write_bytes(b"unchanged Agent protocol fixture")
    package = tmp_path / "package"
    descriptor = export_model(MStd(fit_vocabulary([project(value)])), checkpoint, package, {})
    root = tmp_path / "private"
    register(package, root, {"schema": "sts2.policy-runtime/agent-manifest-1",
        "input": {"profile": "p1-global-r1", "global": {"revision": "r1.5"}}})
    manifest = json.loads((root / "manifest.json").read_bytes())
    sha = descriptor["package_sha256"]
    pool = LocalInferencePool({sha: package / "model.tensor-tree"}, max_batch=4, window_ms=2)
    attachment = root / "inference.json"
    attachment.write_bytes(json_bytes(pool.attachment()))
    for key, token in pool.environment().items():
        monkeypatch.setenv(key, token)

    def run(local):
        output = io.StringIO()
        envelope = {"schema": SCHEMA, "session_id": "isolated-actor", "recovery_epoch": 0}
        current = {"acquisition_id": "acq", "input_spec": manifest["input"]["input_spec"],
                   "continuity_token": "current", "previous_consumption_id": None,
                   "observation": value, "catalog": value["catalog"],
                   "consumption_unit": {"id": "unit"}}

        def messages():
            yield json.dumps({**envelope, "request_id": "consume", "message_type": "consume",
                              "input": current})
            completion = json.loads(output.getvalue().splitlines()[-1])["completion"]
            yield json.dumps({**envelope, "request_id": "consume", "message_type": "consume_ack",
                              "completion": completion})
            for index in range(2):
                yield json.dumps({**envelope, "request_id": f"next-{index}", "message_type": "next",
                                  "input": {"continuity_token": "current"}})

        serve(root / "config.json", root / "manifest.json", root, messages(), output,
              local_inference=attachment if local else None)
        return [json.loads(line) for line in output.getvalue().splitlines()]

    try:
        assert run(False) == run(True)
    finally:
        summary = pool.close()
    assert summary["requests"] == 1  # Repeated next never scores or consumes again.
