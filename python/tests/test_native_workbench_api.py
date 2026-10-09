"""Real HTTP/Application/owner seams with synthetic private pairing fixtures."""

from __future__ import annotations

import io
import json
import sqlite3
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from test_local_models import runtime_http as runtime_http
from test_native_workbench_access import paired_app

from spireagent.json_boundary import BoundaryError
from spireagent.workbench import local_models
from spireagent.workbench.developer import atomic_json
from spireagent.workbench.developer_server import create_server
from spireagent.workbench.native_workbench_access import NativePair
from spireagent.workbench.native_workbench_api import (
    COMMAND_SCHEMA,
    MAX_COMMAND_BYTES,
    MAX_RESPONSE_BYTES,
    PREFIX,
    VIEW_SCHEMA,
    bounded_response,
    public,
)


@pytest.fixture
def native_http(tmp_path, monkeypatch):
    app, old_pair, root, secret, peer = paired_app(tmp_path, monkeypatch)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    pair = NativePair(
        "game-1",
        old_pair.workbench_instance_id,
        old_pair.configuration_id,
        url + "/",
        old_pair.pair_id,
        old_pair.expires_at,
    )

    # Authenticated peer fixture returns only this exact process-pair proof.
    def current_peer(method, route, *, headers):
        peer.calls.append((method, route))
        assert headers == pair.headers(secret)
        return {
            "schema": "sts2.platform/native-workbench-current-1",
            **pair.to_dict(),
            "signature": pair.sign(secret, "native-current-v1"),
        }

    monkeypatch.setattr(peer, "_request", current_peer)
    app.native_access.install(pair, peer)

    def call(route, body=None, *, headers=None, raw=None):
        selected = pair.headers(secret) if headers is None else headers
        if body is not None or raw is not None:
            selected = {**selected, "Content-Type": "application/json"}
        request = Request(
            url + route,
            data=raw if raw is not None else None if body is None else json.dumps(body).encode(),
            headers=selected,
        )
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    try:
        yield app, call, pair, root, secret, peer
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=3)


def command(action, payload=None, request_id="c" * 32):
    return PREFIX + "/actions/" + action, {
        "schema": COMMAND_SCHEMA,
        "request_id": request_id,
        "payload": payload or {},
    }


def rejected(call, route, status, body=None, **kwargs):
    with pytest.raises(HTTPError) as caught:
        call(route, body, **kwargs)
    assert caught.value.code == status
    return json.load(caught.value)


def test_actual_view_five_pages_and_public_health_never_expose_credentials(native_http):
    app, call, pair, _root, secret, _peer = native_http
    before = app.local_training.status()
    for page in ["play", "data", "training", "models", "settings"]:
        view = call(PREFIX + "/view?page=" + page)
        assert view["schema"] == VIEW_SCHEMA and view["page"] == page
        assert view["binding"] == pair.to_dict()
        assert view["pagination"]["limit"] <= 50
        raw = json.dumps(view)
        for credential in [secret, app.account.cookie, app.account.csrf, app.control_token]:
            assert credential not in raw
        assert view["capabilities"]["remote_execution"]["enabled"] is False
        source_support = view["capabilities"]["source_aware_prepare"]
        assert source_support["scope"] == "saved_source3_to_ordered_training_partition"
        assert source_support["enabled"] is False
        assert source_support["reason"] == (
            "workspace_required" if page == "data" else "source3_data_view_required")
    assert call("/health", headers={}) == {"instance_id": app.instance_id}
    assert app.local_training.status() == before
    assert not (app.config.state_dir / "local-training-operation.json").exists()


def test_native_envelope_auth_origin_fields_bounds_and_query_are_fail_closed(native_http):
    app, call, pair, _root, secret, peer = native_http
    for query in [
        "page=unknown",
        "page=play&page=data",
        "page=data&limit=51",
        "offset=10001",
        "extra=1",
    ]:
        assert rejected(call, PREFIX + "/view?" + query, 409)["error"] == "invalid_native_query"
    path, body = command("training.start")
    for headers in [
        {},
        {**pair.headers(secret), "Origin": pair.workbench_url[:-1]},
        {**pair.headers(secret), "Cookie": "browser-cookie"},
    ]:
        rejected(call, path, 409, body, headers=headers)
    rejected(call, PREFIX + "/actions/execute-shell", 404, body)
    rejected(call, path, 400, raw=b"{" + b" " * MAX_COMMAND_BYTES)
    rejected(call, path, 400, raw=b'{"schema":"a","schema":"b","request_id":"c","payload":{}}')
    rejected(call, path, 409, {**body, "executable": "not-an-application-action"})
    result = call(*command("training.start", {"dataset_id": "d" * 64}))
    assert result["status"] == "rejected" and result["error"]["automatic_retry"] is False
    assert app.local_training.status()["operation"]["status"] == "idle"
    assert all(route == "/v1/workbench/native-status" for _method, route in peer.calls)


def test_current_configuration_and_selected_launcher_fence_before_owner(native_http):
    app, call, _pair, root, _secret, _peer = native_http
    original = json.loads((app.config.state_dir / "runtime.json").read_text())
    atomic_json(app.config.state_dir / "runtime.json", {**original, "instance_id": "wrong"})
    value = rejected(call, PREFIX + "/actions/models.stop", 409, command("models.stop")[1])
    assert value["error"] == "native_configuration_changed"
    atomic_json(app.config.state_dir / "runtime.json", original)
    app.identity["workbench_sha256"] = "0" * 64
    value = rejected(call, PREFIX + "/actions/models.stop", 409, command("models.stop")[1])
    assert value["error"] == "native_selected_workbench_changed"
    assert app.models.state["status"] == "idle"
    (root / "native-access.json").unlink()
    value = rejected(call, PREFIX + "/view", 409)
    assert value["error"] == "native_access_not_configured"


def test_owner_control_ack_remains_pending_and_resume_copies_exact_body(native_http, monkeypatch):
    app, call, _pair, _root, _secret, _peer = native_http
    received = []

    def control(action, body):
        received.append((action, body))
        return {
            "schema": "spireagent/training-operation-snapshot-v1",
            "availability": "ready",
            "operation": {
                "status": "pending",
                "requested_action": action,
                "worker_state": "running",
                "domain_completion_state": "not_completed",
            },
        }

    monkeypatch.setattr(app, "control_local_training", control)
    body = {"operation_id": "d" * 32, "expected_attempt_id": "e" * 32}
    ack = call(*command("training.pause", body))
    assert ack["status"] == "accepted"
    assert ack["owner_response"]["operation"]["status"] == "pending"
    assert ack["owner_response"]["operation"]["worker_state"] == "running"
    limits = {"wall_seconds": 91, "scratch_bytes": 16777216}
    resume = {**body, "checkpoint_id": "f" * 64, "intent_id": "1" * 32, "limits": limits}
    call(*command("training.resume", resume, request_id="2" * 32))
    assert received == [("pause", body), ("resume", resume)]


def test_native_projection_strips_nested_secrets_and_enforces_response_bytes():
    assert public(
        {
            "csrf_token": "no",
            "safe": {"token": "no", "value": 3},
            "config_path": "/private",
            "payloads": [{"raw": "no"}],
        }
    ) == {"safe": {"value": 3}}
    with pytest.raises(ValueError, match="native_response_limit"):
        bounded_response({"large": "a" * MAX_RESPONSE_BYTES})


def native_load(app, monkeypatch, runtime):
    client, state, requests = runtime
    app.models.state["connector_endpoint"] = "http://127.0.0.1:19191"
    monkeypatch.setattr(app.models.native_tasks, "connector_instance", lambda _: "game-1")
    monkeypatch.setattr(
        app.models.native_tasks,
        "prepare_model",
        lambda *_, **kwargs: {"runtime_instance_id": "game-1"},
    )
    monkeypatch.setattr(
        app.models,
        "readiness",
        lambda _: {
            "checks": {"runtime_package": {"status": "pass"}, "backend": {"status": "pass"}}
        },
    )
    entered, release = threading.Event(), threading.Event()

    def held(identity, intent, *_args):
        entered.set()
        assert release.wait(3)
        app.models._require_intent(intent)
        app.models.client = client
        app.models.state.update(loaded=True, status="loaded", selection_id=identity)

    monkeypatch.setattr(app.models, "_start", held)
    return entered, release, state, requests


def finish_model(app):
    for worker in list(app.models.threads):
        worker.join(timeout=3)
        assert not worker.is_alive()


def test_lost_load_reply_expired_pair_cancels_only_original_pre_runtime_intent(
    native_http,
    runtime_http,
    monkeypatch,
):
    from spireagent.workbench import native_workbench_access as access_module

    app, _call, pair, _root, secret, _peer = native_http
    entered, release, _state, requests = native_load(app, monkeypatch, runtime_http)
    original_id = "9" * 32
    path, body = command(
        "models.takeover",
        {"selection_id": "s1-human-combat-v4", "run_profile": "short"},
        original_id,
    )
    # Deliberately discard the owner ACK. The client already knows request_id.
    headers = {**pair.headers(secret), "Content-Type": "application/json"}
    response = urlopen(
        Request(
            pair.workbench_url.rstrip("/") + path, data=json.dumps(body).encode(), headers=headers
        ),
        timeout=3,
    )
    response.close()
    assert entered.wait(3)
    monkeypatch.setattr(access_module.time, "time", lambda: pair.expires_at + 1)
    rejected(_call, PREFIX + "/view", 409)
    try:
        recovered = _call(*command("models.human", {"native_request_id": original_id}, "8" * 32))
        assert recovered["status"] == "accepted"
        assert recovered["binding"] == pair.to_dict()
    finally:
        release.set()
    finish_model(app)
    assert app.models.state["status"] == "stopped" and app.models.state["loaded"] is False
    assert not any(path == "/v2/mode" and body == {"mode": "auto"} for path, body in requests)
    replay = rejected(
        _call,
        PREFIX + "/actions/models.stop",
        409,
        command("models.stop", {"native_request_id": original_id})[1],
    )
    assert replay["error"] == "native_model_intent_superseded"


def test_bootstrap_revocation_after_native_load_admission_fences_auto(
    native_http,
    runtime_http,
    monkeypatch,
):
    app, call, _pair, root, _secret, _peer = native_http
    entered, release, state, requests = native_load(app, monkeypatch, runtime_http)
    call(
        *command("models.takeover", {"selection_id": "s1-human-combat-v4", "run_profile": "short"})
    )
    assert entered.wait(3)
    bootstrap = json.loads((root / "native-access.json").read_text())
    atomic_json(root / "native-access.json", {**bootstrap, "enabled": False})
    release.set()
    finish_model(app)
    assert state["mode"] == "human"
    assert not any(path == "/v2/mode" and body == {"mode": "auto"} for path, body in requests)
    assert app.models.state["error_code"] == "native_access_not_configured"
    rejected(
        call,
        PREFIX + "/actions/models.human",
        409,
        command("models.human", {"native_request_id": "c" * 32})[1],
    )


def test_expired_recovery_rejects_newer_intent_wrong_pair_and_over_grace(
    native_http,
    runtime_http,
    monkeypatch,
):
    from spireagent.workbench import native_workbench_access as access_module

    app, call, pair, _root, secret, _peer = native_http
    entered, release, _state, requests = native_load(app, monkeypatch, runtime_http)
    call(*command("models.load", {"selection_id": "s1-human-combat-v4", "run_profile": "short"}))
    assert entered.wait(3)
    try:
        monkeypatch.setattr(access_module.time, "time", lambda: pair.expires_at + 601)
        over = rejected(
            call,
            PREFIX + "/actions/models.stop",
            409,
            command("models.stop", {"native_request_id": "c" * 32})[1],
        )
        assert over["error"] == "native_recovery_grace_expired"
        monkeypatch.setattr(access_module.time, "time", lambda: pair.expires_at + 1)
        wrong = rejected(
            call,
            PREFIX + "/actions/models.stop",
            409,
            command("models.stop", {"native_request_id": "c" * 32})[1],
            headers={**pair.headers(secret), "X-SpireAgent-Workbench-Instance-ID": "wrong"},
        )
        assert wrong["error"] == "native_pair_mismatch"
        with app.models.lock:
            app.models.intent_generation += 1
        stale = rejected(
            call,
            PREFIX + "/actions/models.stop",
            409,
            command("models.stop", {"native_request_id": "c" * 32})[1],
        )
        assert stale["error"] == "native_model_intent_superseded"
    finally:
        release.set()
    finish_model(app)
    assert not any(path == "/v2/mode" and body == {"mode": "auto"} for path, body in requests)


@pytest.mark.parametrize("recovery", ["models.human", "models.stop"])
def test_expired_native_load_proof_cannot_cancel_new_ordinary_start(
    native_http, runtime_http, monkeypatch, recovery
):
    from spireagent.workbench import native_workbench_access as access_module

    app, call, pair, _root, _secret, _peer = native_http
    monkeypatch.setattr(
        app.models, "readiness", lambda _: {"checks": {"backend": {"status": "blocked"}}}
    )
    original_request = "9" * 32
    call(*command("models.load", {
        "selection_id": "s1-human-combat-v4", "run_profile": "short",
    }, original_request))
    finish_model(app)
    assert app.models.state["error_code"] == "model_readiness_blocked"
    original_generation = app.models.intent_generation
    assert app.models.native_intent_context(original_request)["binding"] == pair.to_dict()

    entered, release, state, requests = native_load(app, monkeypatch, runtime_http)
    admitted = app.models.start("s1-human-combat-v4")
    assert admitted["operation"]["action"] == "start"
    assert entered.wait(3)
    assert app.models.intent_generation == original_generation + 1
    assert "_native_intent" not in app.models.state
    assert app.models._native_authorizer is None
    monkeypatch.setattr(access_module.time, "time", lambda: pair.expires_at + 1)
    try:
        refused = rejected(call, PREFIX + "/actions/" + recovery, 409,
                           command(recovery, {"native_request_id": original_request})[1])
        assert refused["error"] == "native_model_intent_superseded"
        assert app.models.intent_generation == original_generation + 1
        assert app.models.state["operation"]["status"] == "pending"
    finally:
        release.set()
    finish_model(app)
    assert app.models.state["operation"]["status"] == "completed"
    assert app.models.state["loaded"] is True and state["mode"] == "human"
    assert not any(path in {"/v2/mode", "/v2/stop"} for path, _ in requests)


def test_ordinary_start_after_failed_native_load_ignores_revoked_native_authorizer(
    native_http, monkeypatch
):
    app, call, _pair, root, _secret, _peer = native_http
    monkeypatch.setattr(
        app.models, "readiness", lambda _: {"checks": {"backend": {"status": "blocked"}}}
    )
    call(*command("models.load", {
        "selection_id": "s1-human-combat-v4", "run_profile": "short",
    }))
    finish_model(app)
    assert app.models.state["error_code"] == "model_readiness_blocked"
    generation = app.models.intent_generation
    bootstrap = json.loads((root / "native-access.json").read_bytes())
    atomic_json(root / "native-access.json", {**bootstrap, "enabled": False})
    spawned = []

    class Process:
        stdout = io.BytesIO(b'{"schema":"foreign"}\n')
        stopped = False

        def __init__(self, command, **kwargs):
            spawned.append(command)

        def terminate(self):
            self.stopped = True

        def wait(self, timeout):
            return 0

        def poll(self):
            return 0 if self.stopped else None

    monkeypatch.setattr(app.models, "readiness", lambda _: {"status": "ready_to_load"})
    monkeypatch.setattr(app.models, "_runtime_package", lambda _: {
        "version": "fixture", "code_sha256": "b" * 64,
    })
    monkeypatch.setattr(local_models, "_check_runtime_port", lambda _: None)
    monkeypatch.setattr(local_models.subprocess, "Popen", Process)
    app.models.start("s1-human-combat-v4")
    finish_model(app)
    assert app.models.intent_generation == generation + 1
    assert "_native_intent" not in app.models.state and app.models._native_authorizer is None
    assert len(spawned) == 1 and spawned[0][-2:] == ["--mode", "human"]
    # The real ordinary load path reached its existing startup validation, not
    # the revoked native authorizer. Its deliberately foreign fixture still fails.
    assert app.models.state["error_code"] == "runtime_load_or_attestation_failed"
    assert app.models.process.stopped


def test_rejected_ordinary_start_preserves_pending_native_intent(native_http, runtime_http,
                                                               monkeypatch):
    app, call, _pair, _root, _secret, _peer = native_http
    entered, release, _state, _requests = native_load(app, monkeypatch, runtime_http)
    call(*command("models.load", {
        "selection_id": "s1-human-combat-v4", "run_profile": "short",
    }))
    assert entered.wait(3)
    context = app.models.native_intent_context("c" * 32)
    authorizer = app.models._native_authorizer
    generation = app.models.intent_generation
    try:
        with pytest.raises(BoundaryError, match="operation_in_progress"):
            app.models.start("s1-human-combat-v4")
        with pytest.raises(BoundaryError, match="unregistered_policy"):
            app.models.start("unknown-selection")
        assert app.models.intent_generation == generation
        assert app.models.native_intent_context("c" * 32) == context
        assert app.models._native_authorizer is authorizer
    finally:
        release.set()
    finish_model(app)


def test_registration_sqlite_failure_is_safe_unconfirmed_without_replay(
    native_http, tmp_path, monkeypatch, caplog,
):
    app, call, pair, _root, _secret, _peer = native_http
    storage_path = tmp_path / "private-rows.sqlite"
    with sqlite3.connect(storage_path) as database:
        database.execute("CREATE TABLE rows(body TEXT)")
    attempts = []

    def fail_verification(model_id, *, environment_kind):
        attempts.append((model_id, environment_kind))
        with sqlite3.connect(storage_path.as_uri() + "?mode=ro", uri=True) as database:
            database.execute("INSERT INTO rows VALUES ('synthetic')")

    monkeypatch.setattr(app.local_model_registration, "_register", fail_verification)
    result = call(*command("models.register", {"model_id": "d" * 64}))
    assert result["binding"] == pair.to_dict()
    assert result["request_id"] == "c" * 32
    assert result["status"] == "unconfirmed"
    assert result["error"] == {
        "code": "registration_verification_storage_failed", "automatic_retry": False,
    }
    assert result["owner_response"] is None
    assert attempts == [("d" * 64, "native")]
    assert str(storage_path) not in json.dumps(result)
    assert "Traceback" not in json.dumps(result)
    assert "sqlite_errorname=SQLITE_READONLY" in caplog.text
    assert call("/health", headers={}) == {"instance_id": app.instance_id}
    assert len(attempts) == 1
