"""Shared browser/native application lifecycle and uncertainty fences; synthetic Recorder owner."""

from __future__ import annotations

import copy
import json
import threading
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

import pytest
from test_native_tasks import bridge as bridge
from test_native_workbench_api import PREFIX, command
from test_native_workbench_api import native_http as native_http

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.developer_server import Application, create_server


def body(status, kind, source_kind=None):
    return {
        "kind": kind,
        "runtime_instance_id": status["runtime_instance_id"],
        "recording_session_id": status["recording_session_id"],
        "source_segment_id": status["source"]["segment_id"] if status["source"] else None,
        "source_kind": source_kind,
        "actor_id": "explicit-operator" if source_kind else None,
        "command_id": str(uuid4()),
    }


def owner(app, monkeypatch):
    current = {
        "schema": "sts2.platform/recording-status-1",
        "runtime_instance_id": "game-1",
        "recording_session_id": "source-session",
        "recording_lifecycle": "recording",
        "capture_profile_id": "native-logical-source-v3",
        "closeout_status": "recording",
        "source": {
            "epoch_id": "epoch-1",
            "segment_id": "segment-1",
            "declaration": {
                "source_kind": "agent_protocol",
                "actor_id": "operator",
                "declaration_id": "decl-1",
                "machine_verifiable": False,
            },
            "observations": 7,
            "inputs": 3,
            "pending_inputs": 1,
            "epochs": 1,
            "gaps": 4,
            "accounting_complete": True,
            "error": None,
        },
        "health": {"append_health": "healthy", "disk_health": "healthy", "error": None},
        "non_claims": [],
    }
    calls, behavior = [], {"unknown": False}
    monkeypatch.setattr(
        app.models.native_tasks, "recording_status", lambda _: copy.deepcopy(current)
    )

    def execute(endpoint, observed, kind, *, source_declaration, command_id):
        calls.append((endpoint, copy.deepcopy(observed), kind, source_declaration, command_id))
        if behavior["unknown"]:
            raise BoundaryError("recording", "native_recording_command_unknown")
        if kind == "close":
            current.update(recording_lifecycle="closed", closeout_status="closed")
        if kind == "pause":
            current["recording_lifecycle"] = "paused"
        if kind == "resume":
            current["recording_lifecycle"] = "recording"
        if kind == "start_new_session":
            current.update(recording_lifecycle="recording", recording_session_id="fresh-session")
        if source_declaration is not None:
            current["source"]["declaration"] = source_declaration
        return {
            "accepted": True,
            "status": copy.deepcopy(current),
            "command_id": command_id,
            "pending": False,
        }

    monkeypatch.setattr(app.models.native_tasks, "recording_command", execute)
    return current, calls, behavior


@pytest.fixture
def browser_app(tmp_path, monkeypatch):
    app = Application(ProjectConfig(tmp_path / "state", "", "", None, combination()))
    current, calls, behavior = owner(app, monkeypatch)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def call(route, value=None, *, csrf=True, headers=None, raw=None):
        selected = {"Content-Type": "application/json", "Origin": root, **(headers or {})}
        if csrf:
            selected["X-CSRF-Token"] = app.account.csrf
        request = Request(
            root + route,
            data=raw
            if raw is not None
            else (json.dumps(value).encode() if value is not None else None),
            headers=selected,
        )
        with client.open(request, timeout=5) as response:
            return json.load(response)

    try:
        yield app, client, root, call, current, calls, behavior
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_browser_status_and_command_use_exact_shared_owner_no_cloud_or_actor_default(browser_app):
    app, client, root, call, current, calls, behavior = browser_app
    with pytest.raises(HTTPError) as denied:
        call("/api/native-recording/status")
    assert denied.value.code == 401 and not calls
    client.open(root + "/").close()
    observed = call("/api/native-recording/status")
    assert observed["status"]["source"]["gaps"] == 4
    assert observed["csrf_token"] == app.account.csrf and app.hub is None
    result = call("/api/native-recording/command", body(current, "pause"))
    assert result["status"]["recording_lifecycle"] == "paused"
    assert calls[-1][2] == "pause" and calls[-1][3] is None
    result = call("/api/native-recording/command", body(current, "change_source", "declared_human"))
    declaration = calls[-1][3]
    assert (
        declaration["source_kind"] == "declared_human"
        and declaration["actor_id"] == "explicit-operator"
    )
    assert declaration["machine_verifiable"] is False and declaration["declaration_id"]
    assert calls[-1][0] == "http://127.0.0.1:15526"


@pytest.mark.parametrize(
    "defect",
    ["csrf", "query", "duplicate", "oversize", "origin", "extra", "source_missing", "context"],
)
def test_browser_command_transport_and_exact_context_fail_before_owner(browser_app, defect):
    app, client, root, call, current, calls, behavior = browser_app
    client.open(root + "/").close()
    value = body(current, "pause")
    route = "/api/native-recording/command"
    kwargs = {}
    if defect == "csrf":
        kwargs["csrf"] = False
    if defect == "query":
        route += "?retry=1"
    if defect == "duplicate":
        kwargs["raw"] = (
            json.dumps(value).replace('"kind": "pause"', '"kind":"resume","kind":"pause"').encode()
        )
    if defect == "oversize":
        kwargs["raw"] = b" " * 4097
    if defect == "origin":
        kwargs["headers"] = {"Origin": "http://evil.example"}
    if defect == "extra":
        value["actor"] = "not authority"
    if defect == "source_missing":
        value = body(current, "start_new_session")
    if defect == "context":
        value["source_segment_id"] = "old-segment"
    with pytest.raises(HTTPError):
        call(route, value, **kwargs)
    assert calls == []


def test_application_unknown_survives_refresh_and_explicit_new_isolation_preserves_notice(
    browser_app,
):
    app, client, root, call, current, calls, behavior = browser_app
    client.open(root + "/").close()
    behavior["unknown"] = True
    original = body(current, "pause")
    with pytest.raises(HTTPError):
        call("/api/native-recording/command", original)
    assert len(calls) == 1
    for _ in range(2):
        view = call("/api/native-recording/status")
        assert (
            view["recovery_required"]
            and view["unconfirmed"]["command_id"] == original["command_id"]
        )
    with pytest.raises(HTTPError):
        call("/api/native-recording/command", body(current, "pause"))
    assert len(calls) == 1
    behavior["unknown"] = False
    call("/api/native-recording/command", body(current, "close"))
    assert call("/api/native-recording/status")[
        "recovery_required"
    ]  # Closed is not proof of original request
    call("/api/native-recording/command", body(current, "start_new_session", "agent_protocol"))
    view = call("/api/native-recording/status")
    assert (
        not view["recovery_required"]
        and view["unconfirmed"]["command_id"] == original["command_id"]
    )
    call("/api/native-recording/command", body(current, "pause"))
    current["runtime_instance_id"] = "fresh-process"
    assert not call("/api/native-recording/status")["recovery_required"]


def test_shared_application_model_recovery_guard_blocks_source_start_and_change_away_only(
    browser_app, monkeypatch
):
    app, _, _, _, current, calls, _ = browser_app
    from types import SimpleNamespace

    app.models.client = SimpleNamespace(request=lambda *args: {"status": {"lifecycle": "stopped"}})
    fresh = {"loaded": True, "runtime": {"mode": "auto", "controller": "held", "tainted": False}}
    monkeypatch.setattr(app.models, "status", lambda: fresh)
    for kind in ("start_new_session", "change_source"):
        with pytest.raises(BoundaryError, match="model_recovery_required"):
            app.control_native_recording(body(current, kind, "declared_human"))
    assert calls == []
    app.control_native_recording(body(current, "close"))  # recovery lane is independent
    app.control_native_recording(body(current, "start_new_session", "agent_protocol"))
    assert calls[-1][3]["source_kind"] == "agent_protocol"
    app.models.client = None  # synthetic status-only client has no owned Runtime to close


def test_native_fixed_forms_and_browser_use_same_application_method_and_explicit_context(
    native_http, monkeypatch
):
    app, call, pair, _, _, _ = native_http
    current, calls, behavior = owner(app, monkeypatch)
    view = call(PREFIX + "/view?page=data")
    descriptors = {item["action_id"]: item for item in view["capabilities"]["actions"]}
    assert {
        "recording.start",
        "recording.pause",
        "recording.resume",
        "recording.change_source",
        "recording.close",
    } <= descriptors.keys()
    assert descriptors["recording.start"]["fields"][0]["default"] == ""
    assert descriptors["recording.start"]["fields"][1]["default"] == ""
    route, value = command("recording.pause", body(current, "pause"))
    assert call(route, value)["status"] == "accepted" and len(calls) == 1
    assert calls[-1][2] == "pause"
    route, value = command(
        "recording.change_source", body(current, "change_source", "agent_native_ui"), "d" * 32
    )
    assert call(route, value)["status"] == "accepted"
    assert (
        calls[-1][3]["source_kind"] == "agent_native_ui" and not calls[-1][3]["machine_verifiable"]
    )
    route, value = command(
        "recording.resume",
        {**body(current, "resume"), "runtime_instance_id": "other-game"},
        "e" * 32,
    )
    result = call(route, value)
    assert result["status"] == "rejected" and len(calls) == 2
