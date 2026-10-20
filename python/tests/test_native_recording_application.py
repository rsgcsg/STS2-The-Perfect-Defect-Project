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
from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.developer_server import Application, create_server
from test_native_tasks import bridge as bridge
from test_native_workbench_api import PREFIX, command
from test_native_workbench_api import native_http as native_http


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

    def execute(endpoint, observed, kind, *, source_declaration, command_id,
                capture_profile_id=None, p1_native_origin=None):
        if capture_profile_id is not None:
            behavior["selected_profile"] = capture_profile_id
            current["capture_profile_id"] = capture_profile_id
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
        if p1_native_origin is not None:
            behavior["p1_native_origin"] = p1_native_origin
            current["source"] = None
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
    closed = call("/api/native-recording/status")
    assert not closed["recovery_required"]
    assert closed["unconfirmed"]["command_id"] == original["command_id"]
    # The distinct known Close releases sequencing, never proves the original request.
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


def test_source4_browser_and_fixed_form_use_same_owner_and_close_controls(native_http, monkeypatch):
    app, call, pair, _, _, _ = native_http
    current, calls, behavior = owner(app, monkeypatch)
    current.update(recording_lifecycle="closed", closeout_status="closed")
    selected = {**body(current, "start_new_session", "declared_human"),
                "capture_profile_id": "native-logical-source-v4"}
    route, value = command("recording.start", selected)
    assert call(route, value)["status"] == "accepted"
    assert behavior["selected_profile"] == "native-logical-source-v4"
    assert current["capture_profile_id"] == "native-logical-source-v4"
    view = call(PREFIX + "/view?page=data")
    descriptors = {item["action_id"]: item for item in view["capabilities"]["actions"]}
    assert descriptors["recording.close"]["enabled"]
    assert descriptors["recording.pause"]["enabled"]
    assert descriptors["recording.start"]["fields"][-1]["default"] == "native-logical-source-v3"
    route, value = command("recording.close", body(current, "close"), "e" * 32)
    assert call(route, value)["status"] == "accepted"
    assert current["recording_lifecycle"] == "closed"
    with pytest.raises(BoundaryError, match="invalid_native_recording_command"):
        app.control_native_recording({**body(current, "close"),
                                      "capture_profile_id": "native-logical-source-v4"})


@pytest.mark.parametrize("profile", [None, [], {}, "native-logical-source-v5"])
def test_source_format_rejects_bad_values_before_owner_dispatch(tmp_path, monkeypatch, profile):
    app = Application(ProjectConfig(tmp_path / "state", "", "", None, combination()))
    try:
        current, calls, _ = owner(app, monkeypatch)
        with pytest.raises(BoundaryError, match="invalid_native_recording_command"):
            app.control_native_recording({**body(current, "start_new_session", "declared_human"),
                                          "capture_profile_id": profile})
        assert calls == []
    finally:
        app.close()


def test_p1_controls_use_same_application_and_unknown_survives_restart(tmp_path, monkeypatch):
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    app = Application(config)
    current, calls, behavior = owner(app, monkeypatch)
    current.update(recording_lifecycle="closed", closeout_status="closed", source=None)
    start = {**body(current, "start_new_session", "declared_human"), "actor_id": None,
             "capture_profile_id": "human-p1-native-decisions-1"}
    app.control_native_recording(start)
    assert behavior["p1_native_origin"] == "declared_human"
    assert current["capture_profile_id"] == "human-p1-native-decisions-1"
    app.control_native_recording(body(current, "export_p1_native_prefix"))
    behavior["unknown"] = True
    pause = body(current, "pause")
    with pytest.raises(BoundaryError, match="native_recording_command_unknown"):
        app.control_native_recording(pause)
    resumed = Application(config)
    assert resumed._recording_unknown_notice["command_id"] == pause["command_id"]
    assert resumed._recording_unknown_blocks is True
    assert len(calls) == 3  # restart did not resend the unknown request


def test_p1_derivation_http_uses_existing_browser_admission_and_exact_dto(browser_app, monkeypatch):
    app, client, root, call, current, calls, behavior = browser_app
    invocations = []
    monkeypatch.setattr(app.local_recording_import, "start_p1_derivation",
                        lambda **body: invocations.append(body) or {"status": "pending"})
    request = {"source_artifact_id": "a" * 64,
               "projection_id": "native-selector-finalized-d-1", "run_id": "run-0001"}
    with pytest.raises(HTTPError) as denied:
        call("/api/local-recordings/derive", request)
    assert denied.value.code == 403 and not invocations
    client.open(root + "/").close()
    with pytest.raises(HTTPError) as invalid:
        call("/api/local-recordings/derive", {**request, "private_path": "/source"})
    assert invalid.value.code == 400 and not invocations
    assert call("/api/local-recordings/derive", request)["status"] == "pending"
    assert invocations == [request]


def test_p1_terminal_control_calls_same_application_api(browser_app, monkeypatch, tmp_path, capsys):
    from spireagent.workbench import developer_server
    from spireagent.workbench.developer import atomic_json
    from spireagent.workbench.p1_recordings_cli import main

    app, client, root, call, current, calls, behavior = browser_app
    current.update(capture_profile_id="human-p1-native-decisions-1", source=None)
    config = tmp_path / "project.json"
    atomic_json(config, app.config.to_dict())
    monkeypatch.setattr(developer_server, "running", lambda _: {
        "port": int(root.rsplit(":", 1)[1]), "control_token": app.control_token})
    assert main(["control", "status", "--config", str(config)]) == 0
    assert json.loads(capsys.readouterr().out)["status"]["recording_session_id"] == "source-session"
    assert not calls
    assert main(["control", "prefix", "--config", str(config)]) == 0
    assert json.loads(capsys.readouterr().out)["command_id"] == calls[0][-1]
    assert calls[0][2] == "export_p1_native_prefix" and len(calls) == 1


def test_p1_storage_http_uses_existing_operation_and_exact_body(browser_app, monkeypatch):
    app, client, root, call, current, calls, behavior = browser_app
    invocations = []
    monkeypatch.setattr(app.local_recording_import, "start_p1_storage",
        lambda **body: invocations.append(body) or {"status": "pending"})
    request = {"action": "restore", "artifact_id": "a" * 64,
               "peer": {"kind": "local", "directory": "/explicit/cold-store"}}
    with pytest.raises(HTTPError) as denied:
        call("/api/local-recordings/storage", request)
    assert denied.value.code == 403 and not invocations
    client.open(root + "/").close()
    with pytest.raises(HTTPError) as malformed:
        call("/api/local-recordings/storage", {**request, "secret": "not accepted"})
    assert malformed.value.code == 400 and not invocations
    assert call("/api/local-recordings/storage", request)["status"] == "pending"
    assert invocations == [request] and not calls


def test_p1_maintenance_terminal_and_browser_share_policy_api(
    browser_app, monkeypatch, tmp_path, capsys,
):
    from spireagent.workbench import developer_server
    from spireagent.workbench.developer import atomic_json
    from spireagent.workbench.p1_recordings_cli import main

    app, client, root, call, current, calls, behavior = browser_app
    path = tmp_path / "project.json"
    atomic_json(path, app.config.to_dict())
    monkeypatch.setattr(developer_server, "running", lambda _: {
        "port": int(root.rsplit(":", 1)[1]), "control_token": app.control_token})
    assert main(["maintenance", "enable", "--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["policy"]["enabled"] is True
    assert app.local_recording_import._maintenance["policy"]["peer"] is None
    client.open(root + "/").close()
    assert call("/api/local-recordings/maintenance", {
        "enabled": False, "peer": None, "projection_id": "native-timeline-targets-1"
    })["policy"]["enabled"] is False
    assert not calls, "maintenance policy never sends Recorder commands"
