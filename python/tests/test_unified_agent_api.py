"""Existing Application instance/browser authority guards the new closed routes."""

from __future__ import annotations

import json
import threading
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest
from auth_fixture_diagnostics import AuthFixtureDiagnostics

from spireagent.workbench.developer import ProjectConfig, atomic_json, combination
from spireagent.workbench.developer_server import Application, configuration_id, create_server


def test_common_routes_use_same_browser_configuration_and_registration_owner(tmp_path, monkeypatch):
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    atomic_json(
        config.state_dir / "runtime.json",
        {
            "instance_id": app.instance_id,
            "configuration_id": configuration_id(config),
        },
    )
    calls = []
    monkeypatch.setattr(
        app.local_model_registration,
        "register_teacher",
        lambda **kw: calls.append(("teacher", kw)) or {"registered": True},
    )
    monkeypatch.setattr(
        app.local_model_registration,
        "register_composition",
        lambda identity, **kw: calls.append((identity, kw)) or {"registered": True},
    )
    monkeypatch.setattr(
        app.models, "run_agent", lambda body: calls.append(("run", body)) or {"admitted": True}
    )
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def post(route, body, *, csrf=None):
        return client.open(
            Request(
                root + route,
                data=json.dumps(body).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Origin": root,
                    "X-CSRF-Token": app.account.csrf if csrf is None else csrf,
                },
            )
        )

    try:
        client.open(root + "/").close()
        teacher = "/api/local-model-registrations/register-teacher"
        composition = "/api/local-model-registrations/register-composition"
        run = "/api/local-models/run-agent"
        for route in (teacher, composition, run):
            with pytest.raises(HTTPError) as denied:
                post(route, {}, csrf="wrong")
            assert denied.value.code == 403
        assert calls == []
        with post(teacher, {"variant": "continuous"}) as response:
            assert json.load(response) == {"registered": True}
        assert calls[-1] == ("teacher", {"variant": "continuous"})
        options = {"model_id": "a" * 64, "task_option": "none", "program_option": "none"}
        with post(composition, options) as response:
            assert json.load(response) == {"registered": True}
        assert calls[-1] == (
            "a" * 64,
            {"environment_kind": "native", "task_option": "none", "program_option": "none"},
        )
        with post(run, {"already_effective": "pure-owner-tested"}) as response:
            assert json.load(response) == {"admitted": True}
        assert calls[-1] == ("run", {"already_effective": "pure-owner-tested"})
        before = len(calls)
        for route, body in (
            (teacher, {"command": "shell"}),
            (composition, {**options, "command": "shell"}),
            (composition, {"model_id": "a" * 64}),
        ):
            with pytest.raises(HTTPError) as invalid:
                post(route, body)
            assert invalid.value.code == 400
        assert len(calls) == before
        atomic_json(
            config.state_dir / "runtime.json",
            {
                "instance_id": "wrong",
                "configuration_id": configuration_id(config),
            },
        )
        for route, body in ((teacher, {}), (composition, options)):
            with pytest.raises(HTTPError) as changed:
                post(route, body)
            assert changed.value.code == 409
            assert json.load(changed.value)["error"] == "running_configuration_mismatch"
        assert len(calls) == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


@pytest.fixture
def native_entry_api(tmp_path, monkeypatch):
    """Real HTTP/CLI/request owners; synthetic Environment/Actor effect leaves.

    No Host, Runtime, model, game or provider is constructed. The Environment
    leaf uses the production body/DTO readers; the common Model produces and
    checks the effective request before its launch leaf records original bytes.
    """
    import copy
    from types import SimpleNamespace

    from test_native_episode_application import capabilities, prepare_body
    from test_unified_agent_execution import m2

    from spireagent.json_boundary import json_bytes
    from spireagent.workbench import local_model_cli
    from spireagent.workbench.native_episode_binding import (
        ADMISSION_SCHEMA,
        BINDING_SCHEMA,
        PROFILE_FILE,
        PROFILE_SCHEMA,
        checked_context,
        installed_tuple,
        preparation_body,
    )

    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    atomic_json(
        config.state_dir / "runtime.json",
        {
            "instance_id": app.instance_id,
            "configuration_id": configuration_id(config),
        },
    )
    reference = "e" * 32
    # Synthetic Environment identity is typed by the production DTO reader.
    # This does not claim actual Host readback, bootstrap or native ownership.
    context = checked_context(
        {
            "schema": ADMISSION_SCHEMA,
            "environment_ref": reference,
            "binding": {
                "schema": BINDING_SCHEMA,
                "ownership": "owned",
                "application_instance_id": app.instance_id,
                "configuration_id": configuration_id(config),
                "generation_id": "f" * 32,
                "runtime_instance_id": "runtime-original",
                "endpoint_binding_sha256": "a" * 64,
                "installed_tuple": installed_tuple(capabilities(), "b" * 64),
                "initialization": "fresh_seeded_defect_a0",
                "host_package_pin": {
                    "schema": "stpd/platform-host-runtime-pin-v1",
                    "package": "@rsgcsg/sts2-host-runtime",
                    "version": "1.1.0-rc.27",
                    "source_revision": "a" * 40,
                    "component_tree_revision": "b" * 40,
                    "release_asset_sha256": "c" * 64,
                    "package_content_sha256": "d" * 64,
                },
                "profile": {
                    "template_id": "defect-a0-s0",
                    "template_payload_sha256": "f" * 64,
                    "generation_id": "profile-fixture",
                    "isolation": "isolated_local_profile",
                },
                "presentation": {"requested": "headless", "observed_host_kind": "headless"},
                "speed": {
                    "requested": "instant",
                    "effective": "instant",
                    "before": "normal",
                    "basis": "native_typed_readback",
                    "configured_default": None,
                },
                "handoff": {
                    "schema": "sts2.host-runtime/reference-controller-handoff-1",
                    "runtime_instance_id": "runtime-original",
                    "observed_at": "2026-10-12T00:00:00Z",
                    "controller": None,
                    "basis": "fresh_control_observation_after_close",
                },
            },
        }
    )
    # Owned request admission validates its staged profile even though this
    # fixture never spawns or verifies a Host package or game.
    package, game = tmp_path / "host-package", tmp_path / "game"
    for directory in (package, game, config.state_dir / "native-host"):
        directory.mkdir()
    atomic_json(config.state_dir / PROFILE_FILE, {
        "schema": PROFILE_SCHEMA,
        "host_package_directory": str(package),
        "host_package_pin": context["binding"]["host_package_pin"],
        "game_directory": str(game),
        "template_id": "defect-a0-s0",
        "endpoint": "http://127.0.0.1:15526",
        "installed_tuple": context["binding"]["installed_tuple"],
        "preparation_timeout_seconds": 5,
        "cleanup_timeout_seconds": 5,
        "candidate_admission": {"game": "exact_canary", "connector": "exact_canary"},
        "default_speed": None,
    })

    effects, requests = [], []
    # Preparation is a synthetic process leaf, but all request/context admission
    # uses the same Environment record and immutable request as the real owner.
    record = app.local_environment.native_record

    def prepare(value):
        body = preparation_body(value)
        effects.append(("prepare", copy.deepcopy(body)))
        record.update(
            phase="ready",
            environment_ref=reference,
            generation_id=context["binding"]["generation_id"],
            intent_id=body["intent_id"],
            preparation=body,
            configuration_id=configuration_id(config),
            profile_sha256=app.local_environment._read_native_profile()[1],
            application_instance_id=app.instance_id,
            actor=None,
            admission_context=context,
        )
        app.local_environment._accept_native_request(record)
        return copy.deepcopy(record)

    def begin_finish(value):
        app.local_environment.native_episode_context(value)
        effects.append(("finish", value))
        return {**record, "admission_context": None}, None, True

    def close_after_actor(original, _client, _actor):
        record.update(phase="closed")
        return copy.deepcopy(record)

    monkeypatch.setattr(app.local_environment, "prepare_native_episode", prepare)
    monkeypatch.setattr(
        app.local_environment, "native_episode_status", lambda: copy.deepcopy(record)
    )
    monkeypatch.setattr(app.local_environment, "begin_native_finish", begin_finish)
    monkeypatch.setattr(app.local_environment, "close_native_after_actor", close_after_actor)
    monkeypatch.setattr(app.models, "_run_profile", lambda *_args: True)
    monkeypatch.setattr(app.models, "composition", lambda _identity: m2())
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(json_bytes({"schema": "sts2.policy-runtime/policy-manifest-1"}))
    monkeypatch.setattr(app.models, "selection", lambda _identity: {
        "id": "public-m2", "adapter": "stpd-public-m2-decision-adapter",
        "runtime_profile": "public-snapshot-m2-v1",
    })
    monkeypatch.setattr(app.models, "entry_path", lambda *_args: manifest)

    def launch(identity, profile, **kwargs):
        original = copy.deepcopy(kwargs["agent_request"])
        requests.append(original)
        effects.append(("run", identity, profile))
        return {"status": "accepted_synthetic_launch", "original_request": original}

    monkeypatch.setattr(app.models, "_prepare_and_load", launch)
    server = create_server(app)
    diagnostic = AuthFixtureDiagnostics()
    diagnostic.observe_server(server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    browser = build_opener(HTTPCookieProcessor(CookieJar()))
    browser.open(root + "/", timeout=3).close()
    plain = build_opener()
    monkeypatch.setattr(
        local_model_cli,
        "running",
        lambda _config: {
            "port": server.server_port,
            "control_token": app.control_token,
        },
    )

    def exchange(path, value=None, *, auth="browser", raw=None, extra=None):
        headers = {"Content-Type": "application/json"}
        if auth == "browser":
            headers.update(Origin=root, **{"X-CSRF-Token": app.account.csrf})
        elif auth in {"bearer", "wrong-bearer"}:
            headers["Authorization"] = "Bearer " + (
                app.control_token if auth == "bearer" else "wrong"
            )
        if extra:
            headers.update(extra)
        wire = raw if raw is not None else (json_bytes(value) if value is not None else None)
        opener = browser if auth == "browser" else plain
        return diagnostic.exchange(
            opener, Request(root + path, data=wire, headers=headers), auth=auth,
            body_length=len(wire) if wire is not None else 0,
            effects=effects, requests=requests,
        )

    yield SimpleNamespace(
        app=app,
        config=config,
        server=server,
        exchange=exchange,
        effects=effects,
        requests=requests,
        context=context,
        reference=reference,
        body=prepare_body(),
    )
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    # Fixture Environment owns no resource to clean up. Prevent a fake ready
    # record from asking Application shutdown to wait for a nonexistent worker.
    record.update(phase="closed")
    app.close()


@pytest.mark.parametrize("caller", ["browser", "bearer-cli"])
def test_native_entry_real_http_and_cli_preserve_common_original_request(native_entry_api, caller):
    from spireagent.json_boundary import json_bytes
    from spireagent.workbench.local_model_cli import native_episode_command

    api = native_entry_api
    if caller == "browser":
        prepared = api.exchange("/api/local-environment/native/prepare", api.body)
        observed = api.exchange("/api/local-environment/native")
        request = api.exchange(
            "/api/local-models/request",
            {
                "selection_id": "public-m2",
                "operation": "prepare-and-load",
                "run_profile": "short",
                "native_episode_ref": api.reference,
            },
        )
        result = api.exchange("/api/local-models/run-agent", request)
        finished = api.exchange(
            "/api/local-environment/native/finish",
            {
                "environment_ref": api.reference,
            },
        )
    else:
        prepared = native_episode_command(
            api.config,
            "prepare-owned",
            intent_id=api.body["intent_id"],
            seed=api.body["seed"],
            speed="instant",
        )
        observed = native_episode_command(api.config, "native-status")
        request = native_episode_command(
            api.config,
            "agent-request",
            environment_ref=api.reference,
            selection="public-m2",
            operation="prepare-and-load",
        )
        result = native_episode_command(
            api.config,
            "run-agent",
            environment_ref=api.reference,
            selection="public-m2",
            operation="prepare-and-load",
        )
        finished = native_episode_command(
            api.config, "native-finish", environment_ref=api.reference
        )
    assert prepared == observed
    assert [effect[0] for effect in api.effects] == ["prepare", "run", "finish"]
    assert api.effects[0][1] == api.body
    assert finished["phase"] == "closed"
    assert request["admission_context"]["native_episode"] == api.context
    assert "native_episode" not in json.dumps(request["composition"]["semantic_definition"])
    assert json_bytes(result["original_request"]) == json_bytes(request)
    assert [json_bytes(value) for value in api.requests] == [json_bytes(request)]


@pytest.mark.parametrize("auth", ["none", "wrong-bearer", "bad-browser"])
def test_native_entry_auth_rejects_before_environment_or_model_effect(native_entry_api, auth):
    api = native_entry_api
    routes = [
        ("/api/local-environment/native/prepare", api.body),
        ("/api/local-environment/native/finish", {"environment_ref": api.reference}),
        (
            "/api/local-models/request",
            {
                "selection_id": "public-m2",
                "operation": "start",
                "run_profile": "short",
                "native_episode_ref": api.reference,
            },
        ),
        ("/api/local-models/run-agent", {}),
    ]
    extra = {"Origin": "http://foreign", "X-CSRF-Token": "wrong"} if auth == "bad-browser" else None
    actual_auth = "browser" if auth == "bad-browser" else auth
    for path, body in routes:
        with pytest.raises(HTTPError) as denied:
            api.exchange(path, body, auth=actual_auth, extra=extra)
        assert denied.value.code == 403
    if auth != "bad-browser":
        with pytest.raises(HTTPError) as denied:
            api.exchange("/api/local-environment/native", auth=actual_auth)
        assert denied.value.code == 401
    assert api.effects == [] and api.requests == []


def test_native_entry_invalid_fields_duplicate_keys_and_limits_have_no_effect(native_entry_api):
    api = native_entry_api
    request_body = {
        "selection_id": "public-m2",
        "operation": "start",
        "run_profile": "short",
        "native_episode_ref": api.reference,
    }
    invalid = [
        ("/api/local-environment/native/prepare", {**api.body, "command": "shell"}, None),
        ("/api/local-environment/native/prepare", {**api.body, "intent_id": True}, None),
        (
            "/api/local-environment/native/prepare",
            None,
            b'{"ownership":"attached","intent_id":"a","intent_id":"b"}',
        ),
        ("/api/local-environment/native/prepare", None, b" " * 1025),
        (
            "/api/local-environment/native/finish",
            {"environment_ref": api.reference, "command": "shell"},
            None,
        ),
        ("/api/local-models/request", {**request_body, "command": "shell"}, None),
        ("/api/local-models/request", {**request_body, "native_episode_ref": "bad"}, None),
    ]
    for path, body, raw in invalid:
        with pytest.raises(HTTPError) as rejected:
            api.exchange(path, body, auth="bearer", raw=raw)
        assert rejected.value.code in {400, 409}
    assert api.effects == [] and api.requests == []
    api.exchange("/api/local-environment/native/prepare", api.body, auth="bearer")
    original_effects = list(api.effects)
    request = api.exchange("/api/local-models/request", request_body, auth="bearer")
    with pytest.raises(HTTPError) as rejected:
        api.exchange("/api/local-models/run-agent", {**request, "command": "shell"}, auth="bearer")
    assert rejected.value.code == 409
    with pytest.raises(HTTPError) as rejected:
        api.exchange("/api/local-environment/native?foreign=1", auth="bearer")
    assert rejected.value.code == 400
    assert api.effects == original_effects and api.requests == []


def test_native_entry_cli_unknown_transport_never_replays_prepare(native_entry_api, monkeypatch):
    import socket

    from spireagent.json_boundary import BoundaryError
    from spireagent.workbench.local_model_cli import native_episode_command

    api = native_entry_api
    original = api.server.RequestHandlerClass.respond

    def lose_original_reply(handler, *args, **kwargs):
        if handler.path == "/api/local-environment/native/prepare":
            # Original owner already accepted the offer. Lose only its reply;
            # the real CLI transport must preserve unknown without resending it.
            handler.close_connection = True
            handler.connection.shutdown(socket.SHUT_RDWR)
            handler.connection.close()
            return
        return original(handler, *args, **kwargs)

    monkeypatch.setattr(api.server.RequestHandlerClass, "respond", lose_original_reply)
    with pytest.raises(
        BoundaryError, match="application_request_failed_check_original_status_no_retry"
    ):
        native_episode_command(
            api.config,
            "prepare-owned",
            intent_id=api.body["intent_id"],
            seed=api.body["seed"],
            speed="instant",
        )
    assert api.effects == [("prepare", api.body)]
    assert api.requests == []
