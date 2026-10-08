from __future__ import annotations

import copy
import hashlib
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.workbench import local_models
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_models import (
    LocalModelService,
    RuntimeClient,
    RuntimeControlBinding,
)
from stpd.canonical import canonical_json
from stpd.policy import installation


@pytest.fixture
def service(tmp_path, monkeypatch):
    config = ProjectConfig(tmp_path, "", "", None, combination())
    result = LocalModelService(config)
    result.state["connector_endpoint"] = "http://127.0.0.1:19191"
    monkeypatch.setattr(result.native_tasks, "connector_instance", lambda _: "game-1")
    # Synthetic native acceptance isolates the Runtime transport tests. The actual
    # bridge and fail-closed handoff have their own wire-level regression suite.
    monkeypatch.setattr(
        result.native_tasks, "prepare_model",
        lambda observed, endpoint: {"runtime_instance_id": "game-1"},
    )
    return result


def finished(service):
    assert service.thread is not None
    service.thread.join(timeout=3)
    assert not service.thread.is_alive()
    return service.status()


def startup():
    return {
        "run_id": "run-00000000-0000-0000-0000-000000000000",
        "manifest_id": "fixture-policy",
        "policy_artifact_sha256": "a" * 64,
        "runtime_version": "0.1.0-rc.1",
        "runtime_code_sha256": "b" * 64,
    }


def status():
    start = startup()
    return {
        "schema": "sts2.policy-runtime/status-1",
        "run_id": start["run_id"],
        "runtime": {
            "version": start["runtime_version"],
            "code_sha256": start["runtime_code_sha256"],
        },
        "policy": {
            "manifest_id": start["manifest_id"],
            "artifact_sha256": start["policy_artifact_sha256"],
        },
        "mode": "human",
        "lifecycle": "running",
        "controller": "released",
        "tainted": False,
        "environment": {"host_kind": "test", "loaded_mod_ids": ["STS2_PLATFORM"],
                        "runtime_instance_id": "game-1"},
    }


def environment(epoch=0, instance="game-1"):
    return {
        "schema": "sts2.policy-runtime/environment-1",
        "run_id": startup()["run_id"],
        "runtime_instance_id": instance,
        "recovery_epoch": epoch,
    }


@pytest.fixture
def runtime_http(request):
    legacy = getattr(request, "param", "current") == "legacy"
    state = status()
    requests = []
    headers = []
    control = {"epoch": 0, "instance": "game-1", "environment_available": True, "ticks": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests.append((self.path, None))
            if self.path == "/v2/environment":
                if legacy or not control["environment_available"]:
                    self.reject(404, "not_found")
                    return
                self.respond({**environment(control["epoch"], control["instance"]),
                              "run_id": state["run_id"]})
                return
            self.respond()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            headers.append((self.path, body, dict(self.headers)))
            prefix = "" if legacy else "/v2"
            if self.path not in {prefix + route for route in ("/mode", "/tick", "/stop")}:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not legacy and self.headers.get("X-STS2-Policy-Run-ID") != state["run_id"]:
                self.send_response(409)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            route = self.path.removeprefix(prefix) if prefix else self.path
            recovering = route == "/stop" or (route == "/mode" and body.get("mode") == "human")
            if recovering:
                control["epoch"] += 1
            elif not legacy:
                if self.headers.get("X-STS2-Game-Instance-ID") != control["instance"]:
                    self.reject(409, "runtime_game_mismatch")
                    return
                if self.headers.get("X-STS2-Recovery-Epoch") != str(control["epoch"]):
                    self.reject(409, "runtime_recovery_epoch_mismatch")
                    return
            if route == "/mode":
                state["mode"] = body["mode"]
            if route == "/tick":
                control["ticks"] += 1
                state["mode"] = "human"
            if route == "/stop":
                state["lifecycle"] = "stopped"
            if route == "/mode" and body.get("mode") == "one_step" and control.get("mode_entered"):
                control["mode_entered"].set()
                assert control["release_mode_response"].wait(timeout=3)
            self.respond()

        def reject(self, code, error):
            raw = json.dumps({"schema": "sts2.policy-runtime/http-2", "error": error}).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def respond(self, override=None):
            value = {
                "schema": "sts2.policy-runtime/http-1" if legacy else "sts2.policy-runtime/http-2",
                "status": state,
            }
            if self.path in {"/tick", "/v2/tick"}:
                value["schema"] += "/tick-1"
                value["results"] = []
            raw = json.dumps(override if override is not None else value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = RuntimeClient(f"http://127.0.0.1:{server.server_port}", startup())
        client.fixture_control = control
        client.fixture_headers = headers
        yield client, state, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_registry_is_trusted_code_selection_not_downloaded_command(service, tmp_path):
    report = service.catalog()
    assert report["policies"][0]["selection_id"] == "s1-human-combat-v4"
    with pytest.raises(BoundaryError, match="unregistered_policy"):
        service.start("../../downloaded/evil.json")
    root = tmp_path / "untrusted"
    registry = root / "configs/developer/local-policies-v1.json"
    registry.parent.mkdir(parents=True)
    value = service.registry()
    value["policies"][0]["command"] = "sh -c anything"
    registry.write_text(json.dumps(value))
    service.root = root
    with pytest.raises(BoundaryError):
        service.registry()


def test_readiness_reports_real_missing_prerequisites_without_loading(service, monkeypatch):
    monkeypatch.setattr(
        installation, "_backend_check", lambda: {"status": "blocked", "code": "no_cuda"}
    )
    result = service.readiness("s1-human-combat-v4")
    assert result["status"] == "blocked" and result["loaded"] is False
    assert result["checks"]["policy_identity"]["status"] == "pass"
    assert result["checks"]["backend"]["code"] == "no_cuda"
    assert service.process is None
    service.start("s1-human-combat-v4")
    result = finished(service)
    assert result["error_code"] == "model_readiness_blocked"
    assert service.process is None


def test_runtime_client_accepts_current_environment_and_binds_exact_identity(runtime_http):
    client, state, requests = runtime_http
    assert client.request("/status")["status"]["environment"]["loaded_mod_ids"] == ["STS2_PLATFORM"]
    state["run_id"] = "replacement-runtime"
    with pytest.raises(BoundaryError, match="identity_drift"):
        client.request("/status")


def test_runtime_status_budget_identity_checked_when_startup_records_it(runtime_http):
    client, state, requests = runtime_http
    client.startup["autonomy_budget"] = {
        "maxSubmissions": 2000, "maxPolicyCalls": 4000, "deadlineMs": 1800000,
    }
    state["autonomy_budget"] = {
        "max_submissions": 2000, "max_policy_calls": 4000, "deadline_ms": 1800000,
    }
    client.request("/status")
    state["autonomy_budget"]["deadline_ms"] = 60000
    with pytest.raises(BoundaryError, match="runtime_status_unavailable_or_identity_drift"):
        client.request("/status")
    assert all(body is None for _, body in requests)


@pytest.mark.parametrize(
    "route,body", [("/mode", {"mode": "human"}), ("/tick", {"max_ticks": 1}), ("/stop", {})]
)
def test_runtime_command_fences_replaced_process_before_effect(runtime_http, route, body):
    client, runtime, requests = runtime_http
    assert client.request("/status")["status"]["run_id"] == startup()["run_id"]
    # The same listening address now belongs to another process, after the GET.
    runtime.update(run_id="replacement-runtime", mode="auto")
    before = copy.deepcopy(runtime)
    with pytest.raises(BoundaryError, match="runtime_command_unknown"):
        client.request(route, body)
    assert runtime == before
    assert [request for request in requests if request[1] is not None] == [("/v2" + route, body)]


@pytest.mark.parametrize("runtime_http", ["legacy"], indirect=True)
@pytest.mark.parametrize(
    "route,body", [("/mode", {"mode": "human"}), ("/tick", {"max_ticks": 1}), ("/stop", {})]
)
def test_runtime_command_cannot_mutate_legacy_server_at_reused_address(runtime_http, route, body):
    client, runtime, requests = runtime_http
    runtime["mode"] = "auto"
    before = copy.deepcopy(runtime)
    with pytest.raises(BoundaryError, match="runtime_command_unknown"):
        client.request(route, body)
    assert runtime == before
    assert [request for request in requests if request[1] is not None] == [("/v2" + route, body)]


def test_closing_old_workbench_cannot_stop_replacement_runtime(service, runtime_http, monkeypatch):
    client, runtime, requests = runtime_http
    service.client = client
    service.state.update(status="loaded", loaded=True, startup=startup())
    # This HTTP fixture has no Agent evidence; the real verifier is covered separately.
    monkeypatch.setattr(service, "_evaluation_handoff", lambda: None)
    runtime["run_id"] = "replacement-runtime"
    service.close()
    assert runtime["lifecycle"] == "running"
    assert [request for request in requests if request[1] is not None] == [("/v2/stop", {})]
    restarted = LocalModelService(service.config)
    assert restarted.state["status"] == "recovery_required"
    assert restarted.state["previous_session"]["startup"] == startup()


@pytest.mark.parametrize("lost_stop", [False, True])
@pytest.mark.parametrize("connector_endpoint", [None, "http://127.0.0.1:19191", "invalid"])
def test_recovered_runtime_shutdown_requires_confirmation(
    service, runtime_http, monkeypatch, lost_stop, connector_endpoint
):
    from urllib.error import URLError

    client, runtime, _ = runtime_http
    manifest = {"manifest_id": "fixture-policy", "artifact": {"sha256": "a" * 64}}
    exact = {
        **startup(),
        "address": "http://127.0.0.1:15527",
        "policy_manifest_sha256": hashlib.sha256(canonical_json(manifest).encode()).hexdigest(),
    }
    service.state["previous_session"] = {"startup": exact, "selection_id": "fixture"}
    if connector_endpoint is not None:
        service.state["previous_session"]["connector_endpoint"] = connector_endpoint
    with monkeypatch.context() as recovery:
        recovery.setattr(service, "selection", lambda _: {"id": "fixture", "manifest": "fixture"})
        recovery.setattr(local_models, "_object_file", lambda _: manifest)
        recovery.setattr(
            service,
            "_runtime_package",
            lambda _identity=None: {
                "version": exact["runtime_version"],
                "code_sha256": exact["runtime_code_sha256"],
            },
        )
        recovery.setattr(local_models, "RuntimeClient", lambda *_: client)
        service._recover("human", service.intent_generation)
    assert service.client is client and service.process is None
    if connector_endpoint == "http://127.0.0.1:19191":
        assert service.state["connector_endpoint"] == connector_endpoint
        assert service.state["error_code"] is None
    else:
        assert service.state["connector_endpoint"] is None
        assert service.state["error_code"] == "runtime_connector_binding_required"
        monkeypatch.setattr(
            service.native_tasks, "prepare_model",
            local_models.NativeTasks.prepare_model.__get__(service.native_tasks),
        )
        service.command("auto")
        assert finished(service)["error_code"] == "runtime_connector_binding_required"
        assert runtime["mode"] == "human"  # safety recovery is retained, model entry is blocked
    if lost_stop:
        # A real recovered HTTP client loses its Stop response; no local Popen
        # exists to supply alternative proof that the process terminated.
        def lost(*args, **kwargs):
            raise URLError("lost response")

        monkeypatch.setattr(client.opener, "open", lost)
    monkeypatch.setattr(service, "_evaluation_handoff", lambda: None)
    service.close()
    assert service.state["status"] == ("command_unknown" if lost_stop else "stopped")
    assert runtime["lifecycle"] == ("running" if lost_stop else "stopped")
    restarted = LocalModelService(service.config)
    assert restarted.state["status"] == ("recovery_required" if lost_stop else "idle")
    if lost_stop:
        assert restarted.state["previous_session"]["startup"] == exact
        restarted.close()
        again = LocalModelService(service.config)
        assert again.state["previous_session"]["startup"] == exact
        with monkeypatch.context() as recovery:
            recovery.setattr(again, "selection", lambda _: {"id": "fixture", "manifest": "fixture"})
            recovery.setattr(local_models, "_object_file", lambda _: manifest)
            recovery.setattr(
                again,
                "_runtime_package",
                lambda _identity=None: {
                    "version": exact["runtime_version"],
                    "code_sha256": exact["runtime_code_sha256"],
                },
            )
            recovery.setattr(local_models, "RuntimeClient", lambda *_: client)
            again.command("human")
            assert finished(again)["status"] == "recovery_required"
        final = LocalModelService(service.config)
        assert final.state["previous_session"]["startup"] == exact
        with pytest.raises(BoundaryError, match="requires_recovery"):
            final.start("s1-human-combat-v4")


@pytest.mark.parametrize("session_status", ["loaded", "command_unknown"])
def test_recovered_exact_observation_clears_only_observation_error(
    service, runtime_http, session_status
):
    client, runtime, requests = runtime_http
    service.client = client
    service.state.update(status=session_status, loaded=True, error_code="runtime_command_unknown")
    runtime["run_id"] = "replacement-runtime"
    assert service.status()["observation_error"] == "runtime_status_unavailable_or_identity_drift"
    runtime["run_id"] = startup()["run_id"]
    runtime["tainted"] = True
    observed = service.status()
    assert "observation_error" not in observed
    assert observed["status"] == session_status
    assert observed["error_code"] == "runtime_command_unknown"
    assert observed["runtime"]["tainted"] is True
    assert all(body is None for _, body in requests)


def test_confirmed_stop_retires_only_transient_status_failure(service, monkeypatch):
    handoff_entered, finish_handoff = threading.Event(), threading.Event()
    running = status()
    terminal = {**running, "lifecycle": "stopped"}
    runtime_errors = [{"code": "historical_runtime_diagnostic"}]
    terminal["errors"] = runtime_errors
    closed = False

    class Runtime:
        def request(self, route, body=None):
            nonlocal closed
            if route == "/stop":
                closed = True
                return {"status": terminal}
            if route == "/status" and not closed:
                return {"status": running}
            raise BoundaryError("local_model", "runtime_status_unavailable_or_identity_drift")

    def handoff():
        handoff_entered.set()
        assert finish_handoff.wait(timeout=3)

    monkeypatch.setattr(service, "_evaluation_handoff", handoff)
    service.client = Runtime()
    service.state.update(status="loaded", loaded=True, runtime=running)
    service.command("stop")
    try:
        assert handoff_entered.wait(timeout=2)
        assert "observation_error" not in service.status()
        # An earlier live GET may already have recorded this transient diagnostic.
        with service.lock:
            service.state["observation_error"] = "runtime_status_unavailable_or_identity_drift"
    finally:
        finish_handoff.set()
    ended = finished(service)
    assert ended["status"] == "stopped" and ended["loaded"] is False
    assert ended["runtime"]["lifecycle"] == "stopped"
    assert ended["runtime"]["errors"] == runtime_errors
    assert "observation_error" not in ended


def test_late_status_reply_cannot_replace_confirmed_stop_runtime(service, monkeypatch):
    read_entered, finish_read = threading.Event(), threading.Event()
    handoff_entered, finish_handoff = threading.Event(), threading.Event()
    running = status()
    terminal = {**running, "lifecycle": "stopped"}
    report: list[dict] = []

    class Runtime:
        def request(self, route, body=None):
            if route == "/stop":
                return {"status": terminal}
            if route == "/status" and threading.current_thread().name == "late-status":
                read_entered.set()
                assert finish_read.wait(timeout=3)
                return {"status": running}
            return {"status": running}

    def handoff():
        handoff_entered.set()
        assert finish_handoff.wait(timeout=3)

    monkeypatch.setattr(service, "_evaluation_handoff", handoff)
    service.client = Runtime()
    service.state.update(status="loaded", loaded=True, runtime=running)
    reader = threading.Thread(target=lambda: report.append(service.status()), name="late-status")
    reader.start()
    try:
        assert read_entered.wait(timeout=2)
        service.command("stop")
        assert handoff_entered.wait(timeout=2)
        finish_read.set()
        reader.join(timeout=2)
        assert not reader.is_alive()
    finally:
        finish_read.set()
        finish_handoff.set()
        reader.join(timeout=2)
    ended = finished(service)
    assert len(report) == 1
    assert ended["status"] == "stopped" and ended["loaded"] is False
    assert ended["runtime"]["lifecycle"] == "stopped"


@pytest.mark.parametrize(
    "address",
    ["http://public.example:15527", "https://127.0.0.1:15527", "http://127.0.0.1:15527/path"],
)
def test_runtime_requires_loopback_and_no_extra_path(address):
    with pytest.raises(BoundaryError):
        RuntimeClient(address, startup())


def test_one_step_is_exact_owner_mode_and_one_tick(service, runtime_http):
    client, _, requests = runtime_http
    service.client = client
    service.state.update(status="loaded", loaded=True)
    initial = service.command("one_step")
    assert initial["operation"]["action"] == "one_step"
    finished(service)
    assert [r for r in requests if r[1] is not None] == [
        ("/v2/mode", {"mode": "one_step"}),
        ("/v2/tick", {"max_ticks": 1}),
    ]
    assert service.state["runtime"]["mode"] == "human"


def test_lost_tick_response_is_unknown_and_never_retried(service):
    class LostResponse:
        calls = []

        def request(self, route, body=None, *, binding=None):
            self.calls.append((route, body))
            if route == "/tick":
                raise BoundaryError("local_model", "runtime_command_unknown")
            return environment() if route == "/environment" else {"status": status()}

    client = LostResponse()
    service.client = client
    service.state.update(status="loaded", loaded=True)
    service.command("one_step")
    assert finished(service)["status"] == "command_unknown"
    with pytest.raises(BoundaryError, match="requires_recovery"):
        service.command("one_step")
    service.command("human")
    finished(service)
    assert sum(route == "/tick" for route, _ in client.calls) == 1


def test_human_handoff_can_be_requested_while_tick_pending(service):
    started, release = threading.Event(), threading.Event()
    calls = []

    class BlockingRuntime:
        def request(self, route, body=None, *, binding=None):
            calls.append((route, body))
            if route == "/tick":
                started.set()
                assert release.wait(timeout=3)
            return environment() if route == "/environment" else {"status": status()}

    service.client = BlockingRuntime()
    service.state.update(status="loaded", loaded=True)
    service.command("one_step")
    assert started.wait(timeout=2)
    service.command("human")
    # Recovery reaches the owner without waiting for the held tick response.
    assert finished(service)["operation"]["status"] == "completed"
    assert ("/mode", {"mode": "human"}) in calls
    assert service.control_send_lock.locked()
    release.set()
    for thread in service.threads:
        thread.join(timeout=2)
    assert [body for route, body in calls if route == "/mode"][-1] == {"mode": "human"}


@pytest.mark.parametrize("blocked_route", ["/mode", "/tick"])
@pytest.mark.parametrize("late_reply", ["success", "unknown"])
@pytest.mark.parametrize("recovery", ["human", "stop"])
def test_recovery_bypasses_stalled_model_reply_and_old_result_cannot_retake_control(
    service, monkeypatch, blocked_route, late_reply, recovery,
):
    entered, release = threading.Event(), threading.Event()
    runtime, calls = status(), []

    class Runtime:
        def request(self, route, body=None, *, binding=None):
            calls.append((route, body))
            if route == "/environment":
                return environment()
            if route == "/mode":
                runtime.update(mode=body["mode"], controller=(
                    "released" if body["mode"] == "human" else "held"
                ))
            if route == "/stop":
                runtime.update(mode="human", controller="released", lifecycle="stopped")
            old_observation = copy.deepcopy(runtime)
            if route == blocked_route and body != {"mode": "human"}:
                entered.set()
                assert release.wait(timeout=3)
                if late_reply == "unknown":
                    raise BoundaryError("local_model", "runtime_command_unknown")
            return {"status": old_observation}

    monkeypatch.setattr(service, "_evaluation_handoff", lambda: None)
    service.client = Runtime()
    service.state.update(status="loaded", loaded=True)
    service.command("one_step")
    old_thread, old_operation = service.thread, service.state["operation"]
    assert entered.wait(timeout=2)
    try:
        service.command(recovery)
        recovered = finished(service)
        assert old_thread.is_alive()
        assert service.control_send_lock.locked()  # model-model serialization remains
        assert recovered["operation"]["action"] == recovery
        assert recovered["operation"]["status"] == "completed"
        assert recovered["runtime"]["controller"] == "released"
        assert runtime["mode"] == "human" and runtime["controller"] == "released"
    finally:
        release.set()
        old_thread.join(timeout=2)
    final = service.status()
    assert final["operation"] == recovered["operation"]
    assert final["error_code"] is None
    assert final["runtime"]["mode"] == "human"
    assert final["runtime"]["controller"] == "released"
    assert final["status"] == ("stopped" if recovery == "stop" else "loaded")
    assert old_operation["status"] == ("unknown" if late_reply == "unknown" else "failed")
    assert sum(route == "/tick" for route, _ in calls) == (blocked_route == "/tick")


def test_restarted_service_does_not_guess_pid_or_activate(service):
    service.state.update(status="loaded", loaded=True, startup=startup())
    service._save()
    replacement = LocalModelService(service.config)
    assert replacement.status()["status"] == "recovery_required"
    assert replacement.client is None and replacement.process is None
    with pytest.raises(BoundaryError, match="requires_recovery"):
        replacement.start("s1-human-combat-v4")


def test_shutdown_during_readiness_cannot_launch_a_late_runtime(service, monkeypatch):
    checking, release = threading.Event(), threading.Event()
    calls = []

    def readiness(_):
        checking.set()
        assert release.wait(timeout=3)
        return {"status": "ready_to_load"}

    monkeypatch.setattr(service, "readiness", readiness)
    monkeypatch.setattr(service, "_runtime_package", lambda _identity=None: {"version": "fixture"})
    monkeypatch.setattr(local_models.subprocess, "Popen", lambda *a, **k: calls.append(a))
    service.start("s1-human-combat-v4")
    assert checking.wait(timeout=2)
    service.close()
    release.set()
    finished(service)
    assert calls == []
    assert service.state["loaded"] is False


def test_start_uses_fixed_command_human_and_rejects_foreign_attestation(service, monkeypatch):
    monkeypatch.setattr(local_models, "_check_runtime_port", lambda port: None)
    monkeypatch.setattr(service, "readiness", lambda _: {"status": "ready_to_load"})
    monkeypatch.setattr(
        service, "_runtime_package",
        lambda _identity=None: {"version": "0.1.0-rc.1", "code_sha256": "b" * 64}
    )
    calls = []

    class Process:
        stdout = io.BytesIO(json.dumps({"schema": "foreign"}).encode() + b"\n")
        stopped = False

        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))

        def terminate(self):
            self.stopped = True

        def wait(self, timeout):
            return 0

        def poll(self):
            return 0 if self.stopped else None

    monkeypatch.setattr(local_models.subprocess, "Popen", Process)
    monkeypatch.setenv("STPD_HUB_TOKEN", "must-not-reach-inference-child")
    service.state.update(startup=startup(), evaluation={"old": True}, status="stopped")
    service.start("s1-human-combat-v4")
    result = finished(service)
    assert result["error_code"] == "runtime_load_or_attestation_failed"
    assert result["status"] == "failed"
    assert "startup" not in result and "evaluation" not in result
    assert LocalModelService(service.config).state["status"] == "idle"
    command, options = calls[0]
    assert command[-2:] == ["--mode", "human"]
    assert "--adapter-arg=tools/policy_adapter.py" in command
    assert "STPD_HUB_TOKEN" not in options["env"]
    assert options["env"]["HF_HUB_OFFLINE"] == "1"
    assert options["env"]["TRANSFORMERS_OFFLINE"] == "1"
    connector = command[command.index("--connector-endpoint") + 1]
    assert service.state["connector_endpoint"] == connector
    assert json.loads((service.directory / "session.json").read_text())["connector_endpoint"] == (
        connector
    )
    assert service.process.stopped


def test_evaluation_catalog_verifies_content_identity(service):
    directory = service.directory / "evaluations"
    directory.mkdir(parents=True)
    report = {"schema": "stpd/local-runtime-evaluation-handoff-v1", "game_outcome": "not_measured"}
    identity = hashlib.sha256(canonical_json(report).encode()).hexdigest()
    good = {**report, "evaluation_id": identity}
    (directory / (identity + ".json")).write_text(json.dumps(good))
    corrupt = copy.deepcopy(good)
    corrupt["game_outcome"] = "invented win"
    (directory / "corrupt.json").write_text(json.dumps(corrupt))
    assert service.evaluations() == [good]


def test_downloaded_fullrun_model_is_not_treated_as_a_loadable_policy(service):
    model = Manifest(
        "model",
        Producer("test/repository", "a" * 40, "b" * 64),
        parameters=FrozenObject.of({"schema": "stpd/scheme1-model-v1", "command": "malicious"}),
    )
    directory = service.config.state_dir / "downloads" / model.artifact_id
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(model.to_bytes())
    (directory / "download.json").write_text("{}")
    result = service.catalog()["downloaded_models"]
    assert result[0]["artifact_id"] == model.artifact_id
    assert result[0]["support_status"] == "unsupported" and result[0]["loaded"] is False
    with pytest.raises(BoundaryError, match="unregistered_policy"):
        service.start(model.artifact_id)
    assert service.process is None


@pytest.mark.parametrize("version", ["v1", "v2", "v3", "v999"])
def test_structured_catalog_requires_explicit_export_and_registration_for_closed_versions(
    service, version
):
    model = Manifest(
        "model", Producer("test/repository", "a" * 40, "b" * 64),
        parameters=FrozenObject.of({"schema": "stpd/structured-m2-model-" + version}),
    )
    directory = service.config.state_dir / "downloads" / model.artifact_id
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(model.to_bytes())
    (directory / "download.json").write_text("{}")
    result = service.catalog()["downloaded_models"][0]
    expected = "unsupported" if version == "v999" else "export_and_registration_required"
    assert result["support_status"] == expected
    assert result["loaded"] is False
    with pytest.raises(BoundaryError, match="unregistered_policy"):
        service.start(model.artifact_id)
    assert service.process is None


def test_prepare_preserves_verified_download_receipt_and_never_starts_runtime(service):
    receipt = {
        "schema": "stpd/result-download-v1",
        "artifact_id": "a" * 64,
        "model_load_validated": False,
        "parents_downloaded": False,
    }

    class Downloader:
        def download(self, identity, destination):
            assert identity == receipt["artifact_id"]
            assert destination == service.config.state_dir / "downloads"
            return receipt

    service.hub = Downloader()
    service.prepare("a" * 64)
    result = finished(service)
    assert result["last_download"] == receipt and result["loaded"] is False
    assert service.process is None


def test_identity_replacement_between_ui_poll_and_command_never_gets_post(service, runtime_http):
    client, state, requests = runtime_http
    service.client = client
    service.state.update(status="loaded", loaded=True)
    assert service.status()["runtime"]["run_id"] == startup()["run_id"]
    state["run_id"] = "some-other-runtime"
    service.command("auto")
    finished(service)
    assert all(body is None for _, body in requests)


def test_prepare_and_load_does_not_install_when_backend_or_weights_blocked(service, monkeypatch):
    calls = []
    monkeypatch.setattr(
        service,
        "readiness",
        lambda _: {
            "status": "blocked",
            "checks": {
                "runtime_package": {"status": "blocked"},
                "backend": {"status": "blocked", "code": "no_cuda"},
            },
        },
    )
    monkeypatch.setattr(local_models, "install_runtime", lambda *args: calls.append(args))
    service.prepare_and_load("s1-human-combat-v4")
    assert finished(service)["error_code"] == "model_readiness_blocked"
    assert calls == [] and service.process is None


def test_prepare_and_load_installs_only_pinned_runtime_before_existing_human_start(
    service, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        service,
        "readiness",
        lambda _: {
            "status": "blocked",
            "checks": {
                "runtime_package": {"status": "blocked"},
                "public_contract": {"status": "blocked"},
                "backend": {"status": "pass"},
            },
        },
    )
    monkeypatch.setattr(
        local_models, "install_runtime", lambda *args: calls.append(("install", args))
    )
    monkeypatch.setattr(
        service, "_start", lambda identity, intent: calls.append(("start", identity))
    )
    service.prepare_and_load("s1-human-combat-v4")
    assert finished(service)["operation"]["status"] == "completed"
    assert [kind for kind, _ in calls] == ["install", "start"]
    assert calls[0][1][1] == service.registry()["runtime_package"]


@pytest.mark.parametrize(
    "error", ["recording_close_pending_or_failed", "native_task_command_unknown"]
)
def test_native_close_failure_never_requests_model_mode(service, runtime_http, monkeypatch, error):
    client, _, requests = runtime_http
    service.client = client
    service.state.update(status="loaded", loaded=True)

    def failed(observed, connector_endpoint):
        raise BoundaryError("local_model", error)

    monkeypatch.setattr(service.native_tasks, "prepare_model", failed)
    service.command("auto")
    result = finished(service)
    assert result["operation"]["status"] == (
        "unknown" if error == "native_task_command_unknown" else "failed"
    )
    assert all(body is None for _, body in requests)
    service.command("human")  # manual recovery is independent of recording bridge
    assert finished(service)["operation"]["status"] == "completed"
    assert [body for _, body in requests if body] == [{"mode": "human"}]


def test_runtime_environment_observed_during_native_close_must_match(
    service, runtime_http, monkeypatch,
):
    client, runtime, requests = runtime_http
    service.client = client
    service.state.update(status="loaded", loaded=True)
    runtime["environment"] = None

    def closed(observed, endpoint):
        assert observed["environment"] is None
        # Another Runtime caller populated its environment while this handoff
        # was closing the Recorder. Do not authorize a different cached game.
        runtime["environment"] = {"runtime_instance_id": "other-game"}
        return {"runtime_instance_id": "game-1"}

    monkeypatch.setattr(service.native_tasks, "prepare_model", closed)
    service.command("auto")
    assert finished(service)["error_code"] == "native_task_game_identity_mismatch"
    assert all(body is None for _, body in requests)


def test_finalization_is_background_and_verifies_exact_bytes_not_page_read(service, monkeypatch):
    from agent_evaluation_fixture import evidence

    directory, expected = evidence(service.directory / "agent-runs")
    service.state.update(
        startup=expected, selection_id="s1-human-combat-v4", loaded=True, status="loaded"
    )
    calls = []

    class Finalized:
        def request(self, route, body=None, *, binding=None):
            calls.append((route, body))
            return {"status": {**status(), "lifecycle": "stopped"}}

    service.client = Finalized()
    service.status()
    assert service.evaluations() == []
    service.observe_once()
    (report,) = service.evaluations()
    assert report["evidence_verification"] == "pass"
    assert report["run_id"] == directory.name and report["game_outcome"] == "not_measured"
    assert service.client is None and service.state["status"] == "stopped"
    service.observe_once()
    assert len(service.evaluations()) == 1
    assert all(route == "/status" and body is None for route, body in calls)


def test_owned_process_exit_can_finalize_without_http_but_unowned_disconnect_cannot(service):
    from agent_evaluation_fixture import evidence

    _, expected = evidence(service.directory / "agent-runs")
    service.state.update(
        startup=expected, selection_id="s1-human-combat-v4", loaded=True, status="loaded"
    )

    class Unavailable:
        def request(self, *args):
            raise BoundaryError("local_model", "runtime_unavailable")

    class Exited:
        def poll(self):
            return 0

    service.client = Unavailable()
    service.observe_once()
    assert service.evaluations() == [] and service.client is not None
    service.process = Exited()
    service.observe_once()
    assert service.state["status"] == "stopped"
    assert service.evaluations()[0]["evidence_verification"] == "pass"


@pytest.mark.parametrize("action", ["auto", "one_step", "shadow"])
def test_human_cancels_old_intent_while_native_close_is_pending(service, monkeypatch, action):
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Runtime:
        def request(self, route, body=None, *, binding=None):
            calls.append((route, body))
            return environment() if route == "/environment" else {"status": status()}

    def native_close(_, connector_endpoint):
        entered.set()
        assert release.wait(timeout=3)
        return {"ready_for_model": True, "runtime_instance_id": "game-1"}

    monkeypatch.setattr(service.native_tasks, "prepare_model", native_close)
    service.client = Runtime()
    service.state.update(status="loaded", loaded=True)
    service.command(action)
    assert entered.wait(timeout=2)
    service.command("human")
    assert finished(service)["operation"]["status"] == "completed"
    release.set()
    for thread in service.threads:
        thread.join(timeout=2)
    assert [(route, body) for route, body in calls if body is not None] == [
        ("/mode", {"mode": "human"})]
    assert service.state["runtime"]["mode"] == "human"


def test_human_during_step_mode_response_prevents_late_tick(service):
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Runtime:
        def request(self, route, body=None, *, binding=None):
            calls.append((route, body))
            if body == {"mode": "one_step"}:
                entered.set()
                assert release.wait(timeout=3)
            return environment() if route == "/environment" else {"status": status()}

    service.client = Runtime()
    service.state.update(status="loaded", loaded=True)
    service.command("one_step")
    assert entered.wait(timeout=2)
    service.command("human")
    release.set()
    assert finished(service)["operation"]["status"] == "completed"
    for thread in service.threads:
        thread.join(timeout=2)
    assert not any(route == "/tick" for route, _ in calls)
    assert [body for route, body in calls if route == "/mode"] == [
        {"mode": "one_step"}, {"mode": "human"}]


def test_stop_while_loading_cancels_late_start_without_waiting_for_readiness(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def readiness(_):
        entered.set()
        assert release.wait(timeout=3)
        return {"status": "ready_to_load"}

    monkeypatch.setattr(service, "readiness", readiness)
    monkeypatch.setattr(local_models.subprocess, "Popen", lambda *a, **k: calls.append(a))
    service.start("s1-human-combat-v4")
    assert entered.wait(timeout=2)
    service.command("stop")
    assert finished(service)["status"] == "stopped"
    release.set()
    for thread in service.threads:
        thread.join(timeout=2)
    assert calls == [] and service.state["status"] == "stopped"


def test_environment_observation_and_control_headers_bind_one_runtime_owner(service, runtime_http):
    client, _, requests = runtime_http
    client.fixture_control["epoch"] = 17
    observed = client.request("/environment")
    assert observed == environment(17)
    service.client = client
    service.state.update(status="loaded", loaded=True)
    service.command("one_step")
    assert finished(service)["operation"]["status"] == "completed"
    writes = client.fixture_headers
    assert [path for path, _, _ in writes] == ["/v2/mode", "/v2/tick"]
    for _, _, headers in writes:
        normalized = {key.lower(): value for key, value in headers.items()}
        assert normalized["x-sts2-policy-run-id"] == startup()["run_id"]
        assert normalized["x-sts2-game-instance-id"] == "game-1"
        assert normalized["x-sts2-recovery-epoch"] == "17"
    assert all(path != "/environment" for path, _ in requests)


@pytest.mark.parametrize("action", ["auto", "one_step", "shadow"])
def test_managed_commands_use_selected_episode_and_never_native_recorder(
    service, runtime_http, monkeypatch, tmp_path, action,
):
    from test_managed_model_target import target

    client, _, requests = runtime_http
    selected = {**target(tmp_path), "runtime_instance_id": "game-1"}
    service.managed_target = lambda: selected
    service.client = client
    service.state.update(status="loaded", loaded=True,
                         managed_environment=local_models.public_target(selected),
                         connector_endpoint=None)

    def unexpected(*_args):
        pytest.fail("Managed command reached native Mod/Recorder preparation")

    monkeypatch.setattr(service.native_tasks, "connector_instance", unexpected)
    monkeypatch.setattr(service.native_tasks, "prepare_model", unexpected)
    service.command(action)
    assert finished(service)["operation"]["status"] == "completed"
    writes = [(route, body) for route, body in requests if body is not None]
    assert writes == [("/v2/mode", {"mode": action})] + (
        [("/v2/tick", {"max_ticks": 1})] if action == "one_step" else []
    )


@pytest.mark.parametrize("changed", ["service_instance_id", "runtime_instance_id",
                                    "game_continuity_id", "unavailable"])
@pytest.mark.parametrize("recovery", ["human", "stop"])
def test_managed_target_drift_blocks_decisions_but_preserves_explicit_recovery(
    service, runtime_http, tmp_path, monkeypatch, changed, recovery,
):
    from test_managed_model_target import target

    client, runtime, requests = runtime_http
    selected = {**target(tmp_path), "runtime_instance_id": "game-1"}
    service.client = client
    service.state.update(status="loaded", loaded=True,
                         managed_environment=local_models.public_target(selected),
                         connector_endpoint=None)

    def current():
        if changed == "unavailable":
            raise BoundaryError("local_environment", "managed_control_held")
        return {**selected, changed: "replacement"}

    service.managed_target = current
    monkeypatch.setattr(service, "_evaluation_handoff", lambda: None)
    service.command("one_step")
    failed = finished(service)
    assert failed["operation"]["status"] == "failed"
    assert failed["status"] == "loaded" and failed["loaded"] is True
    assert not any(body is not None for _, body in requests)
    assert runtime["mode"] == "human"
    service.command(recovery)
    assert finished(service)["operation"]["status"] == "completed"
    assert [(route, body) for route, body in requests if body is not None] == [
        ("/v2/stop", {}) if recovery == "stop" else ("/v2/mode", {"mode": "human"})]


@pytest.mark.parametrize("drift", [None, "binding_sha256", "service_instance_id",
                                  "runtime_instance_id", "game_continuity_id"])
def test_managed_load_binds_cli_and_checks_actual_startup_target(
    service, monkeypatch, tmp_path, drift,
):
    from test_managed_model_target import target

    selected = target(tmp_path)
    service.managed_target = lambda: selected
    expected_target = local_models.public_target(selected)
    manifest = {"manifest_id": "managed-fixture", "artifact": {"sha256": "a" * 64},
                "requirements": {"environment": {"kind": "managed_text_v2"}}}
    manifest_path = tmp_path / "managed-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    entry = {"id": "managed-fixture", "adapter": "stpd-m2-decision-adapter"}
    monkeypatch.setattr(service, "selection", lambda _: entry)
    monkeypatch.setattr(service, "entry_path", lambda _entry, _key: manifest_path)
    monkeypatch.setattr(service, "entry_root", lambda _: tmp_path)
    monkeypatch.setattr(service, "_run_profile", lambda *_: True)
    monkeypatch.setattr(service, "readiness", lambda _: {"status": "ready_to_load"})
    monkeypatch.setattr(service, "_runtime_package", lambda _: {
        "version": "managed-candidate", "code_sha256": "b" * 64})
    monkeypatch.setattr(service, "_node_modules", lambda _: tmp_path / "node_modules")
    monkeypatch.setattr(local_models, "_check_runtime_port", lambda _: None)
    monkeypatch.setattr(service, "start_observer", lambda: None)
    limits = local_models.RUN_PROFILES["short"]
    claimed_target = {**expected_target, **({drift: "foreign"} if drift else {})}
    observed_startup = {
        "schema": "sts2.policy-runtime/startup-1", "mode": "human",
        "manifest_id": manifest["manifest_id"],
        "policy_artifact_sha256": manifest["artifact"]["sha256"],
        "policy_manifest_sha256": hashlib.sha256(canonical_json(manifest).encode()).hexdigest(),
        "runtime_version": "managed-candidate", "runtime_code_sha256": "b" * 64,
        "address": "http://127.0.0.1:15527", "run_id": startup()["run_id"],
        "managed_environment": claimed_target,
        "autonomy_budget": {"maxSubmissions": limits["max_submissions"],
                            "maxPolicyCalls": limits["max_policy_calls"],
                            "deadlineMs": limits["deadline_ms"]},
    }
    commands = []

    class Process:
        stopped = False

        def __init__(self, command, **_kwargs):
            commands.append(command)
            self.stdout = io.BytesIO(json.dumps(observed_startup).encode() + b"\n")

        def poll(self):
            return 0 if self.stopped else None

        def terminate(self):
            self.stopped = True

        def wait(self, timeout):
            return 0

    class Client:
        def __init__(self, *_args):
            pass

        def request(self, route):
            assert route == "/status"
            return {"status": {"autonomy_budget": limits}}

    monkeypatch.setattr(local_models.subprocess, "Popen", Process)
    monkeypatch.setattr(local_models, "RuntimeClient", Client)
    service.start("managed-fixture")
    result = finished(service)
    assert len(commands) == 1
    command = commands[0]
    assert "--connector-endpoint" not in command
    assert command[command.index("--managed-attachment") + 1] == str(selected["client_attachment"])
    assert command[command.index("--managed-expected-game-continuity-id") + 1] == "episode-one"
    assert "stpd.policy.memory_port" in " ".join(command)
    if drift:
        assert result["error_code"] == "runtime_load_or_attestation_failed"
        assert service.process.stopped
    else:
        assert result["loaded"] is True
        assert result["managed_environment"] == expected_target
        assert result["connector_endpoint"] is None
        assert "client_attachment" not in result["managed_environment"]
    service.client = None
    service.process.terminate()


@pytest.mark.parametrize("epoch", [-1, True, 1.5, "1", None, 9007199254740992])
def test_control_binding_rejects_non_decimal_or_unsafe_epochs(epoch):
    with pytest.raises(BoundaryError, match="invalid_runtime_control_binding"):
        RuntimeControlBinding.from_environment(environment(epoch), startup()["run_id"])


@pytest.mark.parametrize("identity", ["", "game\r\nInjected: yes", "has space", "x" * 257, None])
def test_control_binding_cannot_inject_arbitrary_http_headers(identity):
    with pytest.raises(BoundaryError, match="invalid_runtime_control_binding"):
        RuntimeControlBinding(identity, 0)


def test_environment_binding_is_exact_shape_and_run():
    for value in [
        {**environment(), "run_id": "another-runtime"},
        {**environment(), "unexpected": True},
        {**environment(), "schema": "future-unrecognized"},
        {key: value for key, value in environment().items() if key != "recovery_epoch"},
    ]:
        with pytest.raises(BoundaryError, match="identity_drift"):
            RuntimeControlBinding.from_environment(value, startup()["run_id"])


@pytest.mark.parametrize("action", ["shadow", "one_step", "auto"])
def test_old_runtime_requires_upgrade_before_native_close_but_recovery_still_works(
    service, runtime_http, monkeypatch, action,
):
    client, runtime, requests = runtime_http
    client.fixture_control["environment_available"] = False
    closed = []
    monkeypatch.setattr(service.native_tasks, "prepare_model", lambda *args: closed.append(args))
    service.client = client
    service.state.update(status="loaded", loaded=True)
    service.command(action)
    report = finished(service)
    assert report["error_code"] == "runtime_upgrade_required_for_model_control"
    assert report["operation"]["status"] == "failed" and report["loaded"] is True
    assert closed == [] and not any(body is not None for _, body in requests)
    service.command("human")
    assert finished(service)["operation"]["status"] == "completed"
    monkeypatch.setattr(service, "_evaluation_handoff", lambda: None)
    service.command("stop")
    assert finished(service)["status"] == "stopped"
    assert runtime["lifecycle"] == "stopped"
    for _, _, headers in client.fixture_headers:
        assert not any(key.lower() == "x-sts2-recovery-epoch" for key in headers)


def test_runtime_actual_connector_must_match_saved_endpoint_before_native_close(
    service, runtime_http, monkeypatch,
):
    client, _, requests = runtime_http
    client.fixture_control["instance"] = "other-game"
    closed = []
    monkeypatch.setattr(service.native_tasks, "prepare_model", lambda *args: closed.append(args))
    service.client = client
    service.state.update(status="loaded", loaded=True)
    service.command("auto")
    assert finished(service)["error_code"] == "runtime_game_mismatch"
    assert closed == [] and not any(body is not None for _, body in requests)


@pytest.mark.parametrize("action", ["auto", "shadow", "one_step"])
@pytest.mark.parametrize("recovery", ["human", "stop"])
def test_other_ui_recovery_during_native_prepare_rejects_late_model_mode(
    service, runtime_http, monkeypatch, action, recovery,
):
    client, runtime, requests = runtime_http
    entered, release = threading.Event(), threading.Event()

    def prepare(_status, endpoint):
        assert endpoint == "http://127.0.0.1:19191"
        entered.set()
        assert release.wait(timeout=3)
        return {"runtime_instance_id": "game-1", "ready_for_model": True}

    monkeypatch.setattr(service.native_tasks, "prepare_model", prepare)
    service.client = client
    service.state.update(status="loaded", loaded=True)
    service.command(action)
    assert entered.wait(timeout=2)
    # Independent in-game UI client: no call into service.command or its local
    # generation. The shared Runtime owner alone invalidates the stale intent.
    other_ui = RuntimeClient(client.address, startup())
    if recovery == "human":
        other_ui.request("/mode", {"mode": "human"})
    else:
        other_ui.request("/stop", {})
    release.set()
    result = finished(service)
    assert result["error_code"] == "runtime_recovery_epoch_mismatch"
    assert result["operation"]["status"] == "failed"
    assert runtime["mode"] == "human" and client.fixture_control["ticks"] == 0
    assert sum(body == {"mode": action} for _, body in requests) == 1
    attempted = next(
        headers for _, body, headers in client.fixture_headers if body == {"mode": action}
    )
    assert {key.lower(): value for key, value in attempted.items()}["x-sts2-recovery-epoch"] == "0"
    assert client.fixture_control["epoch"] == 1  # the old intent did not refresh and retry


@pytest.mark.parametrize("recovery", ["human", "stop"])
def test_workbench_recovery_reaches_http_owner_before_pending_mode_reply(
    service, runtime_http, monkeypatch, recovery,
):
    client, runtime, requests = runtime_http
    entered, release = threading.Event(), threading.Event()
    client.fixture_control.update(mode_entered=entered, release_mode_response=release)
    monkeypatch.setattr(service, "_evaluation_handoff", lambda: None)
    service.client = client
    service.state.update(status="loaded", loaded=True)
    service.command("one_step")
    old_thread = service.thread
    assert entered.wait(timeout=2)
    try:
        service.command(recovery)
        result = finished(service)
        assert old_thread.is_alive()
        assert result["operation"]["action"] == recovery
        assert result["operation"]["status"] == "completed"
        assert client.fixture_control["epoch"] == 1
        assert runtime["lifecycle"] == ("stopped" if recovery == "stop" else "running")
        if recovery == "human":
            assert runtime["mode"] == "human"
    finally:
        release.set()
        old_thread.join(timeout=2)
    assert not any(path == "/v2/tick" for path, _ in requests)
    assert client.fixture_control["ticks"] == 0
    assert service.state["operation"] == result["operation"]


@pytest.mark.parametrize("recovery", ["human", "stop"])
def test_other_ui_recovery_after_one_step_mode_rejects_late_tick(
    service, runtime_http, recovery,
):
    client, runtime, requests = runtime_http
    entered, release = threading.Event(), threading.Event()
    client.fixture_control.update(mode_entered=entered, release_mode_response=release)
    service.client = client
    service.state.update(status="loaded", loaded=True)
    service.command("one_step")
    assert entered.wait(timeout=2)
    other_ui = RuntimeClient(client.address, startup())
    if recovery == "human":
        other_ui.request("/mode", {"mode": "human"})
    else:
        other_ui.request("/stop", {})
    release.set()
    result = finished(service)
    assert result["error_code"] == "runtime_recovery_epoch_mismatch"
    assert result["operation"]["status"] == "failed"
    assert client.fixture_control["ticks"] == 0
    assert sum(path == "/v2/tick" for path, _ in requests) == 1
    assert runtime["lifecycle"] == ("stopped" if recovery == "stop" else "running")
    if recovery == "human":
        assert runtime["mode"] == "human"


def test_mutation_dispatch_never_omits_shared_binding_for_model_commands(service, runtime_http):
    client, _, requests = runtime_http
    for route, body in [("/mode", {"mode": "auto"}), ("/tick", {"max_ticks": 1})]:
        with pytest.raises(BoundaryError, match="runtime_recovery_precondition_required"):
            service._send_control(client, route, body, service.intent_generation)
    assert requests == []


@pytest.mark.parametrize(
    "http_code,payload,expected", [
        (409, {"schema": "sts2.policy-runtime/http-2", "error": "runtime_game_mismatch"},
         "runtime_game_mismatch"),
        (409, {"schema": "sts2.policy-runtime/http-2", "error": "runtime_recovery_epoch_mismatch"},
         "runtime_recovery_epoch_mismatch"),
        (503, {"schema": "sts2.policy-runtime/http-2", "error": "runtime_environment_unavailable"},
         "runtime_environment_unavailable"),
        (409, {"schema": "other", "error": "runtime_recovery_epoch_mismatch"},
         "runtime_command_unknown"),
        (409, {"schema": "sts2.policy-runtime/http-2", "error": "runtime_recovery_epoch_mismatch",
               "unrecognized": True}, "runtime_command_unknown"),
        (409, {"schema": "sts2.policy-runtime/http-2", "error": "runtime_run_mismatch"},
         "runtime_command_unknown"),
    ],
)
def test_only_recognized_owner_precondition_rejection_is_known_not_dispatched(
    monkeypatch, http_code, payload, expected,
):
    from urllib.error import HTTPError

    client = RuntimeClient("http://127.0.0.1:15527", startup())
    calls = []

    def reject(request, **_):
        calls.append(request)
        raise HTTPError(request.full_url, http_code, "rejected", {},
                        io.BytesIO(json.dumps(payload).encode()))

    monkeypatch.setattr(client.opener, "open", reject)
    with pytest.raises(BoundaryError, match=expected):
        client.request("/mode", {"mode": "auto"}, binding=RuntimeControlBinding("game-1", 0))
    assert len(calls) == 1


def test_local_token_registry_adds_models_without_commands_or_runtime_override(service, tmp_path):
    root = tmp_path / "registered"
    registry = root / "configs/developer/local-policies-v1.json"
    registry.parent.mkdir(parents=True)
    shipped = json.loads((service.root / "configs/developer/local-policies-v1.json").read_text())
    registry.write_text(json.dumps(shipped))
    private = service.private_root / "token-policies-v1.json"
    private.parent.mkdir()
    entry = {"id": "stage1a-b-s", "label": "B-S", "adapter": "token-v1",
             "config": "b-s/config.json", "manifest": "b-s/manifest.json"}
    local = {"schema": "stpd/local-token-policies-v1", "policies": [entry]}
    private.write_text(json.dumps(local))
    service.root = root
    assert service.selection("stage1a-b-s") == entry
    assert service.registry()["runtime_package"] == shipped["runtime_package"]
    local["policies"][0]["command"] = "sh"
    private.write_text(json.dumps(local))
    with pytest.raises(BoundaryError):
        service.registry()
    del entry["command"]
    entry["id"] = shipped["policies"][0]["id"]
    private.write_text(json.dumps(local))
    with pytest.raises(BoundaryError, match="duplicate"):
        service.registry()
    entry["id"] = "stage1a-b-s"
    entry["adapter"] = "downloaded-code"
    private.write_text(json.dumps(local))
    with pytest.raises(BoundaryError, match="invalid_local_token_registry"):
        service.registry()


@pytest.fixture
def text_runtime_profile(service, tmp_path):
    root = tmp_path / "text-owner"
    registry = root / "configs/developer/local-policies-v1.json"
    registry.parent.mkdir(parents=True)
    registry.write_bytes((service.root / "configs/developer/local-policies-v1.json").read_bytes())
    private = service.private_root
    private.mkdir(parents=True, exist_ok=True)
    entry = {"id": "text-b", "label": "Text B", "adapter": "token-v1",
             "config": "config.json", "manifest": "manifest.json",
             "runtime_profile": "text-menu-v1"}
    (private / "token-policies-v1.json").write_text(json.dumps({
        "schema": "stpd/local-token-policies-v1", "policies": [entry],
    }), encoding="utf-8")
    (private / "manifest.json").write_text(json.dumps({
        "representation": {"input_schema": "sts2.player-environment/text-menu-snapshot-1"},
        "manifest_id": "text-b", "artifact": {"sha256": "a" * 64},
    }), encoding="utf-8")
    pin = {"package": local_models.RUNTIME_PACKAGE,
           "dependency_layout": "bundled_source_candidate"}
    (private / "text-menu-runtime-v1.json").write_text(json.dumps({
        "schema": "stpd/local-text-runtime-v1", "runtime_package": pin,
    }), encoding="utf-8")
    service.root = root
    return entry, pin


def test_text_runtime_profile_is_explicit_and_never_falls_back_to_legacy(
    service, text_runtime_profile, monkeypatch,
):
    entry, pin = text_runtime_profile
    shipped = service.registry()["runtime_package"]
    assert service.runtime_profile() == (service.directory, shipped)
    assert service._node_modules() == service.root / "node_modules"
    directory = service.directory / "text-menu-v1"
    assert service.runtime_profile(entry["id"]) == (directory, pin)
    assert service._node_modules(entry["id"]) == directory / "runtime/node_modules"
    seen = []

    def validate(path, actual_pin, legacy_pin):
        seen.append((path, actual_pin, legacy_pin))
        return {"version": "checked"}

    monkeypatch.setattr(local_models, "validate_runtime_install", validate)
    assert service._runtime_package(entry["id"]) == {"version": "checked"}
    assert seen == [(directory / "runtime/node_modules", pin, service._connector_pin())]
    assert service.registry()["runtime_package"] == shipped


def test_run_profiles_are_fixed_per_selection_and_legacy_extended_is_rejected(
    service, text_runtime_profile,
):
    entry, _ = text_runtime_profile
    for shipped in service.registry()["policies"]:
        if shipped["id"] == entry["id"]:
            continue
        source = local_models.ROOT / shipped["manifest"]
        destination = service.root / shipped["manifest"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    policies = {item["selection_id"]: item for item in service.catalog()["policies"]}
    text = policies[entry["id"]]
    assert text["default_run_profile"] == "short"
    assert [profile["id"] for profile in text["run_profiles"]] == ["short", "extended"]
    assert [profile["label"] for profile in text["run_profiles"]] == [
        "短时检查", "较长尝试（每次自主授权最多 30 分钟）",
    ]
    assert text["run_profiles"][1]["limits"] == {
        "max_submissions": 2000, "max_policy_calls": 4000, "deadline_ms": 1800000,
    }
    legacy = policies["s1-human-combat-v4"]
    assert legacy["run_profiles"] == [{"id": "short", "label": "默认运行", "limits": None}]
    assert legacy["run_profile_unavailable_reason"] == "extended_requires_text_menu_runtime"
    for bad in ("extended",):
        with pytest.raises(BoundaryError, match="extended_requires_text_menu_runtime"):
            service.start("s1-human-combat-v4", bad)
    for bad in (None, "forever", 2000):
        with pytest.raises(BoundaryError, match="unsupported_run_profile"):
            service.start(entry["id"], bad)
    assert service.process is None


def test_terminal_screen_projection_requires_exact_text_game_over_context():
    snapshot = {
        "schema": "sts2.player-environment/text-menu-snapshot-1",
        "input_profile": "text-menu-v1",
        "interaction": {
            "kind": "game_over",
            "content": {
                "surface": {"kind": "game_over"},
                "context": {"kind": "game_over", "result": "win"},
            },
        },
    }
    assert local_models._terminal_screen(snapshot) == "win"
    v2 = copy.deepcopy(snapshot)
    v2.update(schema="sts2.player-environment/text-menu-snapshot-2",
              input_profile="text-menu-v2")
    assert local_models._terminal_screen(v2) == "win"
    v2["input_profile"] = "text-menu-v1"
    assert local_models._terminal_screen(v2) is None
    for path, value in (
        (("schema",), "other"),
        (("input_profile",), "compact-v2"),
        (("interaction", "kind"), "combat_turn"),
        (("interaction", "content", "surface", "kind"), "combat_turn"),
        (("interaction", "content", "context", "kind"), "combat_turn"),
        (("interaction", "content", "context", "result"), "maybe"),
        (("interaction", "content", "context", "result"), ["win"]),
    ):
        invalid = copy.deepcopy(snapshot)
        cursor = invalid
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        assert local_models._terminal_screen(invalid) is None
    assert local_models._terminal_screen({}) is None


def test_recorded_budget_projection_is_narrow_and_unknown_on_old_payloads():
    value = {
        "state": "exhausted", "max_submissions": 16, "submissions_used": 16,
        "max_policy_calls": 32, "policy_calls_used": 16, "deadline_ms": 60000,
        "elapsed_ms": 20000, "remaining_ms": 40000,
        "exhausted_reason": "submission_attempt_limit", "ended_reason": None,
        "private_data": "excluded",
    }
    projected = local_models._recorded_budget(value)
    assert projected is not None
    assert projected["exhausted_reason"] == "submission_attempt_limit"
    assert "private_data" not in projected and "remaining_ms" not in projected
    for invalid in ({}, {**value, "state": ["active"]},
                    {**value, "deadline_ms": True},
                    {**value, "exhausted_reason": ["deadline"]}):
        assert local_models._recorded_budget(invalid) is None


def test_verified_finalized_budget_is_projected_without_game_outcome(service):
    from agent_evaluation_fixture import evidence, sha

    from spireagent.live_evaluation import FILES

    directory, expected = evidence(service.directory / "agent-runs")
    event_path = directory / "events.jsonl"
    event = json.loads(event_path.read_text())
    event["payload"] = {"autonomy_budget": {
        "state": "exhausted", "max_submissions": 16, "submissions_used": 16,
        "max_policy_calls": 32, "policy_calls_used": 16, "deadline_ms": 60000,
        "elapsed_ms": 20000, "remaining_ms": 40000,
        "exhausted_reason": "submission_attempt_limit", "ended_reason": None,
    }, "controller": "released"}
    event_path.write_text(canonical_json(event) + "\n")
    evidence_path = directory / "evidence-manifest.json"
    manifest = json.loads(evidence_path.read_text())
    for item in manifest["files"]:
        if item["path"] == "events.jsonl":
            item.update(bytes=len(event_path.read_bytes()), sha256=sha(event_path.read_bytes()))
    manifest["manifest_sha256"] = sha(canonical_json({
        "run_id": manifest["run_id"], "files": manifest["files"],
    }).encode())
    evidence_path.write_text(canonical_json(manifest) + "\n")
    (directory / "checksums.sha256").write_text("".join(
        sha((directory / name).read_bytes()) + "  " + name + "\n"
        for name in FILES if name != "checksums.sha256"
    ))
    service.state.update(startup=expected, selection_id="s1-human-combat-v4")
    service._evaluation_handoff()
    report = service.evaluations()[0]
    assert report["evidence_verification"] == "pass"
    assert report["budget_summary"]["submissions_used"] == 16
    assert report["budget_end_reason"] == "submission_attempt_limit"
    assert report["recorded_action_verbs"] is None
    assert report["native_submission_attempts"] is None
    assert report["terminal_screen_observation"] is None
    assert report["game_outcome"] == "not_measured"


def test_verified_event_projection_counts_attempts_and_marks_conflicting_terminal_pages(
    service, monkeypatch,
):
    """The event projector is exercised behind a stubbed verifier; its gate is tested above."""
    import sts2_platform_evidence
    from agent_evaluation_fixture import evidence

    directory, expected = evidence(service.directory / "agent-runs")
    page = {
        "schema": "sts2.player-environment/text-menu-snapshot-1",
        "input_profile": "text-menu-v1",
        "interaction": {"kind": "game_over", "content": {
            "surface": {"kind": "game_over"},
            "context": {"kind": "game_over", "result": "win"},
        }},
    }
    loss = copy.deepcopy(page)
    loss["interaction"]["content"]["context"]["result"] = "loss"
    budget = {
        "state": "exhausted", "max_submissions": 16, "submissions_used": 1,
        "max_policy_calls": 32, "policy_calls_used": 1, "deadline_ms": 60000,
        "elapsed_ms": 1000, "exhausted_reason": "policy_call_limit", "ended_reason": None,
    }
    items = [
        ("text_decision_input", {"snapshot": page}),
        ("text_menu_dispatch_attempt", {"effect_domain": "text_menu"}),
        ("menu_navigation", {"result": {
            "effect_domain": "text_menu", "native_delivery": None,
            "action": {"verb": "open_information"}, "successor": None,
        }}),
        ("text_menu_dispatch_attempt", {"effect_domain": "native_input"}),
        ("text_native_delivery", {"result": {
            "effect_domain": "native_input", "native_delivery": "delivered",
            "action": {"verb": "open_combat_draw_pile"},
        }}),
        ("text_observed_successor", {"successor": loss}),
        ("stopped", {"autonomy_budget": budget}),
    ]
    (directory / "events.jsonl").write_text("".join(
        canonical_json({"kind": kind, "payload": payload, "sequence": sequence}) + "\n"
        for sequence, (kind, payload) in enumerate(items, 1)
    ))
    monkeypatch.setattr(sts2_platform_evidence, "verify_agent_run_evidence", lambda *_: (
        SimpleNamespace(status="pass", findings=[], value=SimpleNamespace(
            content_id="a" * 64, event_count=len(items)
        ))
    ))
    service.state.update(startup=expected, selection_id="s1-human-combat-v4")
    service._evaluation_handoff()
    report = service.evaluations()[0]
    assert report["native_submission_attempts"] == 1
    assert report["recorded_action_verbs"] == {
        "open_information": 1, "open_combat_draw_pile": 1,
    }
    assert report["delivery_counts"] == {"delivered": 1}
    assert report["budget_end_reason"] == "policy_call_limit"
    assert report["budget_summary"]["submissions_used"] == 1
    assert report["terminal_screen_observation"] == {
        "status": "ambiguous", "result": None, "observation_count": 2,
        "first_event_sequence": 1, "last_event_sequence": 6,
    }
    assert report["game_outcome"] == "not_measured"


def test_sealed_nonadmitted_game_over_is_reported_as_observation_only(service):
    import sys
    from pathlib import Path

    from spireagent.live_evaluation import EXPECTED

    sys.path.insert(0, str(Path(__file__).parents[2] / "components/evidence/tests"))
    from test_agent_run_evidence import (  # type: ignore[import-not-found]
        TextMenuAgentRunEvidenceTests,
        canonical,
    )

    fixture = TextMenuAgentRunEvidenceTests(
        "test_text_navigation_is_verified_without_native_receipt")
    fixture.setUp()
    try:
        fixture.root = service.directory / "agent-runs"
        fixture.root.mkdir(parents=True, exist_ok=True)
        run_id = "run-00000000-0000-0000-0000-000000000001"
        directory = fixture._text_evidence(run_id)
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(status="stopped", mode="human")
        manifest_path.write_bytes(canonical(manifest))
        events = [json.loads(line)
                  for line in (directory / "events.jsonl").read_text().splitlines()]
        page = fixture._game_over_intro(events[1]["payload"]["snapshot"], "loss")
        observation = {"schema": events[0]["schema"], "sequence": 2,
                       "recorded_at": events[0]["recorded_at"],
                       "kind": "text_observation_not_admitted",
                       "payload": {"reason": "unsupported_interaction_kind", "snapshot": page}}
        handoff = {**observation, "sequence": 3, "kind": "handoff_to_human",
                   "payload": {"reason": "auto_surface_not_admitted"}}
        stopped = {**observation, "sequence": 4, "kind": "stopped", "payload": {}}
        fixture._rewrite_events(directory, [events[0], observation, handoff, stopped])
        service.state.update(startup={key: manifest[key] for key in EXPECTED},
                             selection_id="fixture")
        service._evaluation_handoff()
        report = service.evaluations()[0]
        assert report["evidence_verification"] == "pass"
        assert report["event_counts"]["text_observation_not_admitted"] == 1
        assert report["terminal_screen_observation"] == {
            "status": "observed", "result": "loss", "observation_count": 1,
            "first_event_sequence": 2, "last_event_sequence": 2,
        }
        assert report["native_submission_attempts"] is None
        assert report["game_outcome"] == "not_measured"
        assert report["scope"] == "bounded_runtime_operation"
    finally:
        fixture.tearDown()


@pytest.mark.parametrize("profile,attestation", [
    ("short", "correct"), ("extended", "correct"),
    ("extended", "startup_ignored"), ("extended", "status_ignored"),
])
def test_text_run_profile_cli_and_both_budget_attestations(
    service, text_runtime_profile, monkeypatch, profile, attestation,
):
    entry, _ = text_runtime_profile
    monkeypatch.setattr(local_models, "_check_runtime_port", lambda _: None)
    monkeypatch.setattr(service, "readiness", lambda _: {"status": "ready_to_load"})
    monkeypatch.setattr(service, "_runtime_package", lambda _: {
        "version": "0.1.0-rc.10", "code_sha256": "b" * 64,
    })
    monkeypatch.setattr(service, "start_observer", lambda: None)
    manifest = json.loads((service.private_root / entry["manifest"]).read_text())
    limits = local_models.RUN_PROFILES[profile]
    startup_budget = {
        "maxSubmissions": limits["max_submissions"],
        "maxPolicyCalls": limits["max_policy_calls"],
        "deadlineMs": limits["deadline_ms"],
    }
    if attestation == "startup_ignored":
        startup_budget = {"maxSubmissions": 16, "maxPolicyCalls": 32, "deadlineMs": 60000}
    observed_startup = {
        "schema": "sts2.policy-runtime/startup-1", "mode": "human",
        "manifest_id": manifest["manifest_id"],
        "policy_artifact_sha256": manifest["artifact"]["sha256"],
        "policy_manifest_sha256": hashlib.sha256(canonical_json(manifest).encode()).hexdigest(),
        "runtime_version": "0.1.0-rc.10", "runtime_code_sha256": "b" * 64,
        "address": "http://127.0.0.1:15527",
        "run_id": "run-00000000-0000-0000-0000-000000000000",
        "autonomy_budget": startup_budget,
    }
    commands = []

    class Process:
        stopped = False

        def __init__(self, command, **_):
            commands.append(command)
            self.stdout = io.BytesIO(json.dumps(observed_startup).encode() + b"\n")

        def terminate(self):
            self.stopped = True

        def wait(self, timeout):
            return 0

        def poll(self):
            return 0 if self.stopped else None

    class Client:
        def __init__(self, *_):
            pass

        def request(self, route):
            assert route == "/status"
            actual = dict(limits)
            if attestation == "status_ignored":
                actual["deadline_ms"] = 60000
            return {"status": {"autonomy_budget": actual}}

    monkeypatch.setattr(local_models.subprocess, "Popen", Process)
    monkeypatch.setattr(local_models, "RuntimeClient", Client)
    service.start(entry["id"], profile)
    result = finished(service)
    command = commands[0]
    for flag, value in (("--max-auto-submissions", limits["max_submissions"]),
                        ("--max-policy-calls", limits["max_policy_calls"]),
                        ("--auto-deadline-ms", limits["deadline_ms"])):
        assert command[command.index(flag) + 1] == str(value)
    if attestation == "correct":
        assert result["status"] == "loaded" and result["run_profile"] == profile
    else:
        assert result["error_code"] == "runtime_load_or_attestation_failed"
        assert service.process.stopped
    # The fake child has no service endpoint; prevent fixture teardown from issuing Stop.
    service.client = None
    service.process.terminate()


@pytest.mark.parametrize("profile", ["unreviewed", "../runtime", None])
def test_local_registry_rejects_unrecognized_runtime_profile(
    service, text_runtime_profile, profile,
):
    entry, _ = text_runtime_profile
    entry["runtime_profile"] = profile
    (service.private_root / "token-policies-v1.json").write_text(json.dumps({
        "schema": "stpd/local-token-policies-v1", "policies": [entry],
    }), encoding="utf-8")
    with pytest.raises(BoundaryError, match="unsupported_runtime_profile"):
        service.registry()


@pytest.mark.parametrize("representation", [
    {"input_schema": "sts2.player-environment/snapshot-1"}, None, [], "text-menu-v1",
])
def test_text_runtime_cannot_be_selected_by_legacy_or_malformed_model(
    service, text_runtime_profile, representation,
):
    entry, _ = text_runtime_profile
    path = service.private_root / entry["manifest"]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["representation"] = representation
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BoundaryError, match="text_runtime_requires_text_model"):
        service.runtime_profile(entry["id"])


def test_prepare_text_runtime_requires_explicit_local_install_without_download(
    service, text_runtime_profile, monkeypatch,
):
    entry, _ = text_runtime_profile
    monkeypatch.setattr(service, "readiness", lambda _: {
        "checks": {"runtime_package": {"status": "blocked"}}, "status": "blocked",
    })
    monkeypatch.setattr(local_models, "install_runtime", lambda *a, **k: pytest.fail("no download"))
    monkeypatch.setattr(service, "_start", lambda *a: pytest.fail("no model start"))
    service.prepare_and_load(entry["id"])
    state = finished(service)
    assert state["error_code"] == "text_runtime_local_install_required"
    assert state["loaded"] is False


def test_offline_runtime_install_binds_explicit_selection_to_separate_slot(
    service, text_runtime_profile, monkeypatch, tmp_path,
):
    from spireagent.workbench import local_model_cli, runtime_install

    entry, pin = text_runtime_profile
    calls = []
    monkeypatch.setattr(local_model_cli, "running", lambda _: None)
    monkeypatch.setattr(local_models, "LocalModelService", lambda _: service)
    monkeypatch.setattr(runtime_install, "install_runtime", lambda *a, **k: calls.append((a, k)))
    archive = tmp_path / "candidate.tgz"
    local_model_cli.model_command(service.config, "install-runtime",
                                  selection=entry["id"], runtime_archive=archive)
    assert calls == [((service.directory / "text-menu-v1", pin, service._connector_pin()),
                      {"archive": archive})]
    # Without a selection, the offline CLI uses the shipped primary Runtime pin.
    # It cannot infer a new Runtime profile from archive contents.
    calls.clear()
    local_model_cli.model_command(service.config, "install-runtime", runtime_archive=archive)
    assert calls == [((service.directory, service.registry()["runtime_package"],
                       service._connector_pin()), {"archive": archive})]


def test_v2_offline_install_requires_private_exact_pin_and_uses_distinct_slot(
    service, monkeypatch, tmp_path,
):
    from spireagent.workbench import local_model_cli, runtime_install
    from spireagent.workbench.developer import atomic_json

    archive = tmp_path / "v2-candidate.tgz"
    archive.write_bytes(b"synthetic candidate archive")
    monkeypatch.setattr(local_model_cli, "running", lambda _: None)
    monkeypatch.setattr(local_models, "LocalModelService", lambda _: service)
    installed = []
    monkeypatch.setattr(runtime_install, "install_runtime",
                        lambda *args, **kwargs: installed.append((args, kwargs)) or
                        {"status": "runtime_installed"})
    with pytest.raises(BoundaryError, match="trusted_text_runtime_kit_unavailable"):
        local_model_cli.model_command(service.config, "install-runtime",
                                      runtime_profile="text-menu-m2-v2",
                                      runtime_archive=archive)
    assert installed == []

    service.private_root.mkdir(parents=True, exist_ok=True)
    profile = service.private_root / "text-menu-m2-runtime-v2.json"
    pin = {"package": "@rsgcsg/sts2-policy-runtime", "version": "candidate-v2",
           "source_revision": "a" * 40, "component_tree_revision": "b" * 40,
           "release_asset_sha256": "c" * 64,
           "package_content_sha256": "d" * 64,
           "dependency_layout": "bundled_source_candidate",
           "bundled_connector_pin": {"candidate": "strictly validated by install"}}
    atomic_json(profile, {"schema": "stpd/local-text-m2-runtime-v1",
                          "runtime_package": pin})
    with pytest.raises(BoundaryError, match="text_runtime_profile_invalid"):
        local_model_cli.model_command(service.config, "install-runtime",
                                      runtime_profile="text-menu-m2-v2",
                                      runtime_archive=archive)
    assert installed == []
    atomic_json(profile, {"schema": "stpd/local-text-m2-runtime-v2",
                          "runtime_package": pin})
    result = local_model_cli.model_command(service.config, "install-runtime",
                                           runtime_profile="text-menu-m2-v2",
                                           runtime_archive=archive)
    assert result["status"] == "runtime_installed"
    assert installed == [((service.directory / "text-menu-m2-v2", pin,
                           service._connector_pin()),
                          {"archive": archive, "required_profile": "text-menu-m2-v2"})]


def test_v2_prepare_reports_unbundled_asset_and_reuses_exact_install(
    service, monkeypatch,
):
    from spireagent.workbench.developer import atomic_json

    monkeypatch.setattr(local_models, "install_runtime",
                        lambda *args, **kwargs: pytest.fail("no install on prepare"))
    service.prepare_text_runtime("text-menu-m2-v2")
    state = finished(service)
    assert state["error_code"] == "trusted_text_runtime_kit_unavailable"
    assert state["loaded"] is False

    service.private_root.mkdir(parents=True, exist_ok=True)
    pin = {"package": "@rsgcsg/sts2-policy-runtime", "version": "candidate-v2",
           "source_revision": "a" * 40, "component_tree_revision": "b" * 40,
           "release_asset_sha256": "c" * 64,
           "package_content_sha256": "d" * 64,
           "dependency_layout": "bundled_source_candidate",
           "bundled_connector_pin": {"candidate": "strictly validated on reuse"}}
    atomic_json(service.private_root / "text-menu-m2-runtime-v2.json",
                {"schema": "stpd/local-text-m2-runtime-v2", "runtime_package": pin})
    observed = []
    monkeypatch.setattr(local_models, "validate_runtime_install",
                        lambda *args: observed.append(args) or {"status": "installed"})
    monkeypatch.setattr(local_models, "v2_sdk_available", lambda _: True)
    monkeypatch.setattr(service, "_selected_kit_text_runtime",
                        lambda _: pytest.fail("installed v2 must not request kit assets"))
    service.prepare_text_runtime("text-menu-m2-v2")
    state = finished(service)
    assert state["last_text_runtime_preparation"] == {
        "runtime_profile": "text-menu-m2-v2", "status": "ready", "reused": True,
    }
    assert observed == [((service.directory / "text-menu-m2-v2" / "runtime" /
                          "node_modules"), pin, service._connector_pin())]
    monkeypatch.setattr(local_models, "v2_sdk_available", lambda _: False)
    service.prepare_text_runtime("text-menu-m2-v2")
    state = finished(service)
    assert state["error_code"] == "v2_runtime_contract_unavailable"


def test_v2_prepare_from_selected_kit_uses_contract_checked_transaction(
    service, monkeypatch, tmp_path,
):
    from spireagent.workbench import kit_runtime

    archive = tmp_path / "v2.tgz"
    archive.write_bytes(b"synthetic inventoried archive")
    pin = {"package": "@rsgcsg/sts2-policy-runtime", "version": "candidate-v2",
           "source_revision": "a" * 40, "component_tree_revision": "b" * 40,
           "release_asset_sha256": kit_runtime.hashlib.sha256(archive.read_bytes()).hexdigest(),
           "package_content_sha256": "d" * 64,
           "dependency_layout": "bundled_source_candidate",
           "bundled_connector_pin": {}}
    profile = json.dumps({"schema": "stpd/local-text-m2-runtime-v2",
                          "runtime_package": pin}).encode()
    monkeypatch.setattr(service, "_selected_kit_text_runtime",
                        lambda profile_id: (profile, archive, pin))
    installed = []
    monkeypatch.setattr(local_models, "install_runtime",
                        lambda *a, **k: installed.append((a, k)) or
                        {"status": "runtime_installed"})
    service.prepare_text_runtime("text-menu-m2-v2")
    state = finished(service)
    assert state["last_text_runtime_preparation"] == {
        "runtime_profile": "text-menu-m2-v2", "status": "ready", "reused": False,
    }
    assert installed == [((service.directory / "text-menu-m2-v2", pin,
                           service._connector_pin()),
                          {"archive": archive, "required_profile": "text-menu-m2-v2"})]


def test_v2_prepare_rejects_colliding_private_pin_before_install(
    service, monkeypatch, tmp_path,
):
    from spireagent.workbench.developer import atomic_json

    archive = tmp_path / "v2.tgz"
    archive.write_bytes(b"inventoried")
    pin = {"package": "@rsgcsg/sts2-policy-runtime", "version": "candidate-v2",
           "source_revision": "a" * 40, "component_tree_revision": "b" * 40,
           "release_asset_sha256": "c" * 64,
           "package_content_sha256": "d" * 64,
           "dependency_layout": "bundled_source_candidate", "bundled_connector_pin": {}}
    service.private_root.mkdir(parents=True)
    atomic_json(service.private_root / "text-menu-m2-runtime-v2.json",
                {"schema": "stpd/local-text-m2-runtime-v2", "runtime_package": pin})
    different = dict(pin, version="other-v2")
    monkeypatch.setattr(service, "_selected_kit_text_runtime",
                        lambda _: (b"profile", archive, different))
    monkeypatch.setattr(local_models, "validate_runtime_install", lambda *a: None)
    monkeypatch.setattr(local_models, "install_runtime",
                        lambda *a, **k: pytest.fail("collision must not install"))
    service.prepare_text_runtime("text-menu-m2-v2")
    assert finished(service)["error_code"] == "private_profile_collision"


def test_v2_sdk_probe_requires_installed_methods(tmp_path):
    from spireagent.workbench.runtime_install import v2_sdk_available

    sdk = tmp_path / "index.mjs"
    sdk.write_text("export class PlayerEnvironmentRestClient {}", encoding="utf-8")
    assert v2_sdk_available(sdk) is False
    sdk.write_text("export class PlayerEnvironmentRestClient {"
                   "textMenuV2Capabilities() {} observeTextMenuV2Context() {} }",
                   encoding="utf-8")
    assert v2_sdk_available(sdk) is False
    sdk.write_text("export class PlayerEnvironmentRestClient {"
                   "textMenuV2Capabilities() {} observeTextMenuV2() {} "
                   "observeTextMenuV2Context() {} submitTextMenuV2() {} "
                   "textMenuV2Result() {} }", encoding="utf-8")
    assert v2_sdk_available(sdk) is True


def test_runtime_port_check_rejects_listener_but_accepts_closed_connections():
    import os
    import socket

    with socket.socket() as listener:
        if os.name != "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen()
        with pytest.raises(BoundaryError, match="runtime_port_already_in_use"):
            local_models._check_runtime_port(port)
        if os.name != "nt":
            with socket.create_connection(("127.0.0.1", port)) as client:
                connection, _ = listener.accept()
                connection.close()  # POSIX server TIME_WAIT is reusable by Node
                assert client.recv(1) == b""
    local_models._check_runtime_port(port)


@pytest.mark.parametrize("tainted", [False, True])
def test_explicit_stop_recovers_sealed_previous_version_without_replaying(
    service, monkeypatch, tainted,
):
    from agent_evaluation_fixture import evidence

    directory, expected = evidence(service.directory / "agent-runs", tainted=tainted)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    previous = {"startup": {**expected, "address": "http://127.0.0.1:15527"},
                "selection_id": "removed-old-model", "status": "runtime_exited"}
    service.state.update(status="recovery_required", previous_session=previous)
    probes = []
    monkeypatch.setattr(local_models, "_check_runtime_port", lambda port: probes.append(port))
    monkeypatch.setattr(service, "selection", lambda _: pytest.fail("must use sealed identity"))
    monkeypatch.setattr(service, "_runtime_package", lambda: pytest.fail("old package not needed"))
    service.command("stop")
    result = finished(service)
    assert result["status"] == "stopped" and not result["loaded"]
    assert result["previous_session"] is None
    assert result["recovery_evidence"]["tainted"] is tainted
    assert result["evaluation"]["evidence_verification"] == "pass"
    assert probes == [15527] and service.client is None
    archive = service.directory / "session-archives" / (
        result["recovery_evidence"]["session_archive_id"] + ".json"
    )
    assert json.loads(archive.read_bytes()) == previous
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    assert LocalModelService(service.config).state["status"] == "idle"


@pytest.mark.parametrize("fault", ["missing", "tampered", "identity", "no_stop", "occupied"])
def test_finalized_stop_recovery_requires_exact_evidence_and_free_port(
    service, monkeypatch, fault,
):
    from agent_evaluation_fixture import evidence

    directory, expected = evidence(
        service.directory / "agent-runs",
        terminal_event="mode_changed" if fault == "no_stop" else "stopped",
    )
    previous = {"startup": {**expected, "address": "http://127.0.0.1:15527"},
                "selection_id": "old-model", "status": "command_unknown"}
    if fault == "missing":
        (directory / "checksums.sha256").unlink()
    elif fault == "tampered":
        (directory / "events.jsonl").write_text("{}\n")
    elif fault == "identity":
        previous["startup"]["runtime_code_sha256"] = "f" * 64
    service.state.update(status="recovery_required", previous_session=previous)

    def probe(_):
        if fault == "occupied":
            raise BoundaryError("local_model", "runtime_port_already_in_use")
        pytest.fail("unverified evidence must not reach port probe")

    monkeypatch.setattr(local_models, "_check_runtime_port", probe)
    if fault == "occupied":
        with pytest.raises(BoundaryError, match="runtime_port_already_in_use"):
            service._recover_finalized_stop(previous, service.intent_generation)
    else:
        assert not service._recover_finalized_stop(previous, service.intent_generation)
    assert service.state["previous_session"] == previous
    assert service.state["status"] == "recovery_required"
    assert not (service.directory / "session-archives").exists()
