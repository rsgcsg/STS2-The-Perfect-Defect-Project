"""Real local HTTP/Scheduler with the existing synthetic CPU provider, no live worker."""
from __future__ import annotations

import json
import time

import pytest
from test_remote_training_http import client, settled

from spireagent.json_boundary import BoundaryError
from spireagent.local_diagnostics import LocalDiagnostics
from stpd.p1.spec import MStdConfig


def records(app, name):
    return [json.loads(line) for line in
            (app.config.state_dir / "logs" / name).read_text().splitlines()]


@pytest.mark.parametrize("continuation", ["manual", "automatic"])
def test_start_and_continuation_log_local_phases_without_changing_dispatch(
    tmp_path, continuation,
):
    with client(tmp_path) as (app, call, provider, _, _, snapshot, _):
        provider.ready = True
        first = call("start", body={"intent_id": "a" * 32,
            "source_id": snapshot.artifact_id, "config": MStdConfig(max_epochs=2).to_dict(),
            "max_seconds": 300, "stop_after": 1})["jobs"][0]
        paused = settled(call, first["id"], "paused")
        if continuation == "manual":
            second = call("resume", body={"job_id": paused["id"],
                "expected_attempt_id": paused["attempt_id"], "intent_id": "b" * 32,
                "checkpoint_id": paused["result"]["checkpoint_id"],
                "max_seconds": 300, "stop_after": None})["jobs"][0]
            settled(call, second["id"], "completed")
        else:
            call("auto-continue", body={"job_id": paused["id"],
                "expected_attempt_id": paused["attempt_id"],
                "deadline_unix": time.time() + 3600, "max_new_segments": 1,
                "max_seconds": 300})
            deadline = time.monotonic() + 30
            while True:
                status = call("status")
                assert status["scheduler_error"] is None
                if status["continuation"]["state"] == "completed":
                    break
                assert time.monotonic() < deadline, status
                time.sleep(0.05)
            second = status["jobs"][1]
        app.remote_training.close()
        assert len(provider.calls) == 2
        assert provider.calls[1].resume == paused["result"]["checkpoint_id"]
        assert provider.calls[0].input_id == provider.calls[1].input_id == first["input_id"]
        phases = records(app, "phases.jsonl")
        started = [p["stage"] for p in phases if p["event"] == "phase_started"]
        expected = ["remote.prepare.request_validate", "remote.transfer.budget",
                    "remote.transfer.copy", "remote.transfer.readback", "remote.transfer.verify",
                    "remote.transfer.consume", "remote.start.enqueue", "scheduler.preflight",
                    "scheduler.provider_submit_to_handle", "scheduler.bind_handle",
                    "scheduler.receipt_verify", "scheduler.receipt_accept",
                    "scheduler.result_index"]
        positions = [started.index(name) for name in expected]
        assert positions == sorted(positions)
        extra = (["remote.resume.request_validate", "remote.resume.enqueue"]
                 if continuation == "manual" else
                 ["continuation.paused_accepted", "continuation.new_intent"])
        assert all(name in started for name in extra)
        finished = [p for p in phases if p["event"] == "phase_finished"]
        assert len(finished) * 2 == len(phases)
        assert all(p["duration_ms"] >= 0 and p["elapsed_ms"] >= 0 for p in finished)
        assert all(p["outcome"] == "completed" for p in finished)
        assert {p.get("job_id") for p in finished if p["stage"] == "scheduler.bind_handle"} == {
            first["id"], second["id"]}
        if continuation == "automatic":
            intent = next(p for p in finished if p["stage"] == "continuation.new_intent")
            assert intent["job_id"] == second["id"] and intent["parent_job_id"] == first["id"]


def test_middle_transfer_failure_names_stage_and_never_submits(tmp_path, monkeypatch):
    from spireagent.workbench import remote_training_storage

    secret = "DO_NOT_LOG_PROVIDER_CREDENTIAL_19d3"

    def fail(*args, **kwargs):
        raise ValueError(secret)

    with client(tmp_path) as (app, call, provider, _, _, snapshot, _):
        monkeypatch.setattr(remote_training_storage, "copy_artifact", fail)
        with pytest.raises(BoundaryError, match="invalid_remote_training_request"):
            call("start", body={"intent_id": "a" * 32, "source_id": snapshot.artifact_id,
                "config": MStdConfig(max_epochs=2).to_dict(), "max_seconds": 300, "stop_after": 1})
        assert provider.calls == [] and call("status")["jobs"] == []
        errors = records(app, "errors.jsonl")
        assert len(errors) == 1 and errors[0]["stage"] == "remote.transfer.copy"
        assert errors[0]["action"] == "start" and errors[0]["exception_type"] == "ValueError"
        assert secret not in json.dumps(errors)
        failed = [p for p in records(app, "phases.jsonl") if p.get("outcome") == "failed"]
        assert failed and {p["error_id"] for p in failed} == {errors[0]["error_id"]}


def test_paused_operations_do_not_claim_a_new_intent_or_self_parent(tmp_path):
    from test_training_continuation import service, setup

    store, ops, provider, scheduler, _, first, _ = setup(tmp_path)
    try:
        assert scheduler.tick(time.time())["state"] == "paused"
        ops.pause(True)
        app = service(store, ops, provider, scheduler)
        with LocalDiagnostics(tmp_path).operation("remote-training", "auto-continue"):
            app._continue_training(time.time())
        phases = [json.loads(line) for line in
                  (tmp_path / "logs/phases.jsonl").read_text().splitlines()]
        assert "continuation.advance" in {p["stage"] for p in phases}
        assert "continuation.new_intent" not in {p["stage"] for p in phases}
        assert all(p.get("parent_job_id") != first for p in phases)
        assert len(ops.jobs()) == 1 and len(provider.calls) == 1
    finally:
        scheduler.close()
