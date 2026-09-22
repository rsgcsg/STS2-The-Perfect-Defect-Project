from __future__ import annotations

import copy
import hashlib
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
            lambda: {
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
                lambda: {
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
    monkeypatch.setattr(service, "_runtime_package", lambda: {"version": "fixture"})
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
        service, "_runtime_package", lambda: {"version": "0.1.0-rc.1", "code_sha256": "b" * 64}
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
    private = root / ".local/token-policies-v1.json"
    private.parent.mkdir()
    entry = {"id": "stage1a-b-s", "label": "B-S", "adapter": "token-v1",
             "config": ".local/b-s/config.json", "manifest": ".local/b-s/manifest.json"}
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
