"""Browser transport to the real training owner; immutable synthetic CPU fixtures only."""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from test_training_service_contracts import ready, settle

from spireagent.workbench.developer_server import Application, configuration_id, create_server
from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.recipes import structured as adapter_module


@contextmanager
def browser(config, tmp_path):
    config_path = tmp_path / "project.json"
    config_path.write_text(json.dumps(config.to_dict()))
    app = Application(config, config_path=config_path)
    server = create_server(app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    runtime = config.state_dir / "runtime.json"
    runtime.write_text(json.dumps({"instance_id": app.instance_id,
                                  "configuration_id": configuration_id(config)}))
    headers = {"Cookie": f"{app.account.cookie_name}={app.account.cookie}",
               "Content-Type": "application/json", "Origin": url,
               "X-CSRF-Token": app.account.csrf}

    def call(route, body=None, *, selected_headers=None):
        request = Request(url + "/api/local-training/" + route,
                          data=None if body is None else json.dumps(body).encode(),
                          headers=headers if selected_headers is None else selected_headers)
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    try:
        yield app, call, headers, runtime
    finally:
        server.shutdown()
        server.server_close()
        app.close()


def rejected(call, route, body, status, error, **kwargs):
    with pytest.raises(HTTPError) as caught:
        call(route, body, **kwargs)
    assert caught.value.code == status
    assert json.load(caught.value)["error"] == error


def test_http_capabilities_exact_queries_body_and_configuration_guards(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    before = set(store.manifest_ids())
    with browser(service.config, tmp_path) as (app, call, headers, runtime):
        app.local_training = service
        capabilities = call("capabilities")
        assert capabilities["schema"] == "spireagent/training-capabilities-v1"
        assert capabilities["automatic_retry"] is False
        assert call("status")["operation"]["status"] == "idle"
        for route in ["capabilities?extra=1", "status?extra=1",
                      "status?operation_id=" + "a" * 32 + "&operation_id=" + "b" * 32]:
            rejected(call, route, None, 400, "invalid_local_training_request")
        rejected(call, "status?operation_id=" + "a" * 32, None, 409, "operation_not_current")
        rejected(call, "capabilities", None, 401, "browser_session_required", selected_headers={})
        body = {"operation_id": "a" * 32, "expected_attempt_id": "b" * 32}
        for action in ["pause", "cancel", "reconcile", "resume"]:
            denied = {key: value for key, value in headers.items() if key != "X-CSRF-Token"}
            rejected(call, action, body, 403, "browser_action_denied", selected_headers=denied)
            rejected(call, action, {**body, "extra": True}, 400, "invalid_local_training_request")
        rejected(call, "start", {**request.to_dict(), "executable": "untrusted"},
                 409, "invalid_training_request")
        remote = {**request.to_dict(), "placement_id": "remote-gpu"}
        rejected(call, "start", remote, 409, "unsupported_placement")
        runtime.write_text(json.dumps({"instance_id": "different",
                                       "configuration_id": configuration_id(service.config)}))
        for action in ["pause", "cancel", "reconcile"]:
            rejected(call, action, body, 409, "running_configuration_mismatch")
        rejected(call, "resume", {**body, "checkpoint_id": "c" * 64,
                                 "intent_id": "d" * 32, "limits": request.limits},
                 409, "running_configuration_mismatch")
        rejected(call, "start", request.to_dict(), 409, "running_configuration_mismatch")
    assert set(store.manifest_ids()) == before
    assert not (owner.path.parent / "local-training-operation.json").exists()


def test_http_real_service_pending_controls_reload_exact_resume_and_limits(tmp_path, monkeypatch):
    service, request, _owner, _store = ready(tmp_path, monkeypatch)
    original = adapter_module._private_child
    entered, released = threading.Event(), threading.Event()

    def delayed(command, *args, **kwargs):
        on_line = kwargs["on_stdout_line"]

        def message(raw):
            if json.loads(raw)["kind"] == "prepared":
                entered.set()
                assert released.wait(10)
            on_line(raw)

        return original(command, *args, **{**kwargs, "on_stdout_line": message})

    monkeypatch.setattr(adapter_module, "_private_child", delayed)
    with browser(service.config, tmp_path) as (app, call, _headers, _runtime):
        app.local_training = service
        try:
            started = call("start", request.to_dict())["operation"]
            assert entered.wait(15)
            operation_id, attempt_id = started["operation_id"], started["attempt_id"]
            body = {"operation_id": operation_id, "expected_attempt_id": attempt_id}
            rejected(call, "pause", {**body, "expected_attempt_id": "0" * 32},
                     409, "stale_operation_attempt")
            ack = call("pause", body)["operation"]
            assert ack["status"] == "pending" and ack["requested_action"] == "pause"
            ack = call("cancel", body)["operation"]
            assert ack["status"] == "pending" and ack["worker_state"] == "running"
            assert ack["requested_action"] == "cancel" and ack["selected_result"] is False
            rejected(call, "reconcile", body, 409, "writer_still_running")
        finally:
            released.set()
        stopped = settle(service)
        assert stopped["status"] == "cancelled" and stopped["worker_state"] == "terminal"
        assert stopped["checkpoint_id"]
        app.local_training = LocalTrainingService(service.config)
        reloaded = call("status?operation_id=" + operation_id)["operation"]
        assert reloaded["limits"] == stopped["limits"] and reloaded["config"] == stopped["config"]
        reloaded["limits"]["wall_seconds"] = 1
        assert call("status")["operation"]["limits"]["wall_seconds"] == 600
        resume = {**body, "checkpoint_id": stopped["checkpoint_id"], "intent_id": "2" * 32,
                  "limits": stopped["limits"]}
        rejected(call, "resume", {**resume, "limits": {"wall_seconds": 601}},
                 409, "cumulative_limits_must_be_preserved")
        resumed = call("resume", resume)["operation"]
        assert resumed["attempt_id"] != attempt_id and resumed["limits"] == stopped["limits"]
        completed = settle(app.local_training)
        assert completed["status"] == "completed", completed
        assert completed["elapsed_seconds"] >= stopped["elapsed_seconds"]
        rejected(call, "cancel", body, 409, "stale_operation_attempt")


def test_snapshot_config_and_limits_are_public_copies(tmp_path, monkeypatch):
    service, request, owner, _store = ready(tmp_path, monkeypatch)
    canonical = replace(request, limits={"wall_seconds": 600, "scratch_bytes": 512 * 1024 * 1024})
    journal = service._new_operation(canonical, owner, "a" * 32, previous={"status": "idle"})
    first = service._snapshot(journal)["operation"]
    first["config"]["epochs"] = 100
    first["limits"]["wall_seconds"] = 1
    again = service._snapshot(journal)["operation"]
    assert again["config"] == request.config
    assert again["limits"] == canonical.limits
