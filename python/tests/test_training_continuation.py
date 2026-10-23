"""Same Ops/Scheduler/real P1 CPU worker across a process-owner replacement."""
import json
import time
from dataclasses import replace

import pytest
from test_p1_remote_scheduler import composition

from spireagent.hub import training_continuation as continuation
from spireagent.hub.scheduler import Scheduler
from spireagent.workbench.developer import RemoteComputeConfig
from spireagent.workbench.remote_training import RemoteTrainingService


def setup(tmp_path):
    store, owner, snapshot, run, ops, provider, record_use = composition(tmp_path)
    job = ops.enqueue("training", run.artifact_id, "first", max_seconds=300,
                      reserved_units=1, budget_limit=4, options={"stop_after": 1})
    provider.ready = True
    scheduler = Scheduler(ops, store, provider, budget_limit=4, record_training_use=record_use)
    assert scheduler.tick(time.time())["state"] == "submitted"
    row = ops.jobs()[0]
    policy = continuation.arm(ops, row, target_id=provider.target.target_id,
                              producer=provider.target.producer.to_dict(),
                              deadline=time.time() + 3600, max_new_segments=2, max_seconds=300)
    return store, ops, provider, scheduler, policy, job, record_use


def service(store, ops, provider, scheduler):
    # Existing test composition's real ledger/use callback remains on the Scheduler.
    # Only the Application's already constructed context is supplied here.
    from types import SimpleNamespace
    app = object.__new__(RemoteTrainingService)
    app.config = SimpleNamespace(remote_compute=RemoteComputeConfig(
        ops.path, ops.path.with_name("target.json"), 4, 1,
        {"kind": "local", "directory": "unused"}))
    app._ready = lambda: (None, store, provider, ops, scheduler)
    return app


def test_real_pause_atomic_next_intent_restart_and_original_result(tmp_path):
    store, ops, provider, scheduler, policy, job, record_use = setup(tmp_path)
    try:
        assert scheduler.tick(time.time())["state"] == "paused"
        paused = ops.jobs()[0]
        cp = json.loads(paused["result"])["checkpoint_id"]
        app = service(store, ops, provider, scheduler)
        app._continue_training(time.time())
        new = continuation.read(ops)
        assert new["new_segments"] == 1
        assert len(ops.jobs()) == 2
        assert json.loads(ops.jobs()[1]["options"]) == {
            "resume": cp, "stop_after": None, "execution_deadline_unix": policy["deadline_unix"]}
        app._continue_training(time.time())
        assert len(ops.jobs()) == 2
    finally:
        scheduler.close()
    # Recreate the owner after enqueue but before submit, using the same durable DB.
    with Scheduler(ops, store, provider, budget_limit=4,
                   record_training_use=record_use) as replacement:
        app = service(store, ops, provider, replacement)
        app._continue_training(time.time())
        assert replacement.tick(time.time())["state"] == "submitted"
        assert replacement.tick(time.time())["state"] == "completed"
        app._continue_training(time.time())
        policy = continuation.read(ops)
        assert policy["state"] == "completed" and not policy["enabled"]
    assert len(provider.calls) == 2
    assert provider.calls[1].resume == cp
    assert {request.input_id for request in provider.calls} == {policy["run_id"]}
    assert len(ops.jobs()) == 2
    assert sum(row["reserved_units"] for row in ops.jobs()) == 2


@pytest.mark.parametrize("case", ["unknown", "cancelled", "deadline", "segments", "budget",
                                  "target", "paused_by_operator"])
def test_no_successor_when_original_or_authorization_does_not_allow_it(tmp_path, case):
    store, ops, provider, scheduler, policy, job, _ = setup(tmp_path)
    try:
        app = service(store, ops, provider, scheduler)
        if case == "unknown":
            provider.poll_error = True
            assert scheduler.tick(time.time())["state"] == "result_uncertain"
        else:
            assert scheduler.tick(time.time())["state"] == "paused"
            if case == "cancelled":
                continuation.stop(ops, job, "stopped_by_operator")
            elif case in {"deadline", "segments"}:
                policy["deadline_unix"] = (time.time() - 1 if case == "deadline"
                                           else time.time() + 3600)
                policy["new_segments"] = 2 if case == "segments" else 0
                with ops.transaction() as db:
                    db.execute("UPDATE settings SET value=? WHERE key=?",
                               (json.dumps(policy), continuation.KEY))
            elif case == "budget":
                app.config.remote_compute = replace(app.config.remote_compute, budget_limit=1)
            elif case == "target":
                provider.target = replace(provider.target, timeout_seconds=301)
            elif case == "paused_by_operator":
                ops.pause(True)
        app._continue_training(time.time())
        assert len(ops.jobs()) == 1
        assert len(provider.calls) == 1
    finally:
        scheduler.close()


def test_late_restart_does_not_launch_queued_successor(tmp_path):
    store, ops, provider, scheduler, policy, job, _ = setup(tmp_path)
    try:
        assert scheduler.tick(time.time())["state"] == "paused"
        app = service(store, ops, provider, scheduler)
        app._continue_training(time.time())
        app._continue_training(policy["deadline_unix"])
        assert ops.jobs()[1]["status"] == "cancelled"
        assert continuation.read(ops)["state"] == "deadline_exhausted"
        assert scheduler.tick(time.time())["state"] == "idle"
        assert len(provider.calls) == 1
    finally:
        scheduler.close()


def test_http_arm_recreated_application_owner_continues_without_client_resume(tmp_path):
    from test_remote_training_http import client, settled

    from stpd.p1.spec import MStdConfig

    with client(tmp_path) as (app, call, provider, store, owner, snapshot, _):
        first = call("start", body={"intent_id": "d" * 32,
            "source_id": snapshot.artifact_id, "config": MStdConfig(max_epochs=2).to_dict(),
            "max_seconds": 300, "stop_after": 1})["jobs"][0]
        running = settled(call, first["id"], "running")
        body = {"job_id": first["id"], "expected_attempt_id": running["attempt_id"],
                "deadline_unix": time.time() + 3600, "max_new_segments": 2,
                "max_seconds": 300}
        armed = call("auto-continue", body=body)["continuation"]
        assert armed["enabled"] and armed["new_segments"] == 0
        assert call("auto-continue", body=body)["continuation"] == armed
        original = app.remote_training
        original.close()
        replacement = RemoteTrainingService(app.config, original.select, provider=provider)
        app.remote_training = replacement
        provider.ready = True
        replacement.start_loop()
        deadline = time.monotonic() + 60
        while True:
            status = call("status")
            policy = status["continuation"]
            assert status["scheduler_error"] is None
            if policy["state"] == "completed":
                break
            assert time.monotonic() < deadline, status
            time.sleep(0.05)
        assert len(status["jobs"]) == 2 and len(provider.calls) == 2
        assert {row["input_id"] for row in status["jobs"]} == {first["input_id"]}
        assert status["jobs"][0]["status"] == "paused"
        assert status["jobs"][1]["status"] == "completed"
        assert not policy["enabled"]


def test_explicit_execution_producer_is_separate_from_control_application(tmp_path, monkeypatch):
    from test_remote_training_http import client

    from spireagent.json_boundary import BoundaryError
    from spireagent.workbench import remote_training
    from spireagent.workbench.developer import ProjectConfig

    with client(tmp_path) as (app, _, provider, _, _, _, config_path):
        old = provider.target.producer
        changed = replace(old, source_revision="0" * 40)
        monkeypatch.setattr(remote_training, "source_identity", lambda _: changed)
        with pytest.raises(BoundaryError, match="target_source_lock_mismatch"):
            app.remote_training._context()
        config = replace(app.config, remote_compute=replace(
            app.config.remote_compute, execution_producer=old))
        config_path.write_text(json.dumps(config.to_dict()))
        assert ProjectConfig.load(config_path) == config
        service = RemoteTrainingService(config, app.remote_training.select, provider=provider)
        assert service._context()[2].target.producer == old
        service.config = replace(config, remote_compute=replace(
            config.remote_compute, execution_producer=changed))
        with pytest.raises(BoundaryError, match="target_source_lock_mismatch"):
            service._context()


def test_delayed_preflight_cannot_submit_after_continuation_deadline(tmp_path, monkeypatch):
    store, ops, provider, scheduler, policy, job, _ = setup(tmp_path)
    try:
        assert scheduler.tick(time.time())["state"] == "paused"
        app = service(store, ops, provider, scheduler)
        app._continue_training(time.time())
        original_preflight = scheduler._preflight
        def slow_preflight(row):
            request = original_preflight(row)
            monkeypatch.setattr("spireagent.hub.scheduler.time.time",
                                lambda: policy["deadline_unix"] + 1)
            return request
        monkeypatch.setattr(scheduler, "_preflight", slow_preflight)
        result = scheduler.tick(time.time())
        assert result["state"] == "preflight_failed"
        assert result["code"] == "continuation_launch_deadline_exhausted"
        assert len(provider.calls) == 1
        assert ops.jobs()[1]["status"] == "failed"
        app._continue_training(time.time())
        assert not continuation.read(ops)["enabled"]
        assert sum(row["reserved_units"] for row in ops.jobs()) == 2
    finally:
        scheduler.close()
