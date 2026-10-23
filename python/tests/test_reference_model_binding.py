"""Same P1 Model, original registration/negotiate and native typed resource.

SDK/Runtime package identity, Model bytes, Application HTTP/CLI request generation,
public Reference negotiation and Host Python pipe are real. The physical World is
synthetic. Load-dispatch tests replace _start to avoid the shared Runtime port;
these checks do not qualify runtime attestation or actual native gameplay.
"""
# ruff: noqa: F811 -- imported production-shaped fixtures are resolved by pytest.
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from test_local_models import public_native_preflight as public_native_preflight
from test_native_episode_application import native_app as native_app
from test_native_episode_application import prepare_body, prepared
from test_native_episode_process import (
    installed_native_runtime as installed_native_runtime,
)
from test_native_episode_process import native_caps as native_caps
from test_native_episode_process import native_endpoint as native_endpoint
from test_native_episode_process import native_runtime as native_runtime
from test_native_episode_process import world as world
from test_p1_model_registration import p1_registration as p1_registration

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import atomic_json
from spireagent.workbench.developer_server import configuration_id, create_server
from spireagent.workbench.local_models import LocalModelService
from spireagent.workbench.reference_suite import _same_model_binding


@pytest.fixture
def reference_p1(native_runtime, native_app, p1_registration, world, monkeypatch):
    app = native_runtime[0]
    service = app.local_model_registration
    _, _, store, model, _, _ = p1_registration
    monkeypatch.setattr(app.local_model_export, "_source", lambda _: store)
    monkeypatch.setattr(app.local_environment, "native_capabilities",
                        app.local_environment._native_capabilities)
    original_route = world.route

    def route(path, body):
        if path.endswith("/public-basis/capabilities"):
            return {"schema": "sts2.player-environment/public-basis-capabilities-1",
                "read_profile": "public-basis-sealed-1", "input_profile": "public-basis-1",
                "capture_schema": "sts2.player-environment/public-basis-handle-1",
                "chunk_schema": "sts2.player-environment/public-basis-chunk-1",
                "release_schema": "sts2.player-environment/public-basis-release-1",
                "current_route": "/api/player-environment/public-basis/current",
                "read_route": "/api/player-environment/public-basis/read",
                "release_route": "/api/player-environment/public-basis/release",
                "max_capsule_bytes": 8388608, "max_retained_bytes": 16777216,
                "max_capsules": 8, "ttl_ms": 30000, "default_chunk_bytes": 65536,
                "min_chunk_bytes": 256, "max_chunk_bytes": 1048576,
                "creates_mutation_authority": False,
                "scope": "current_public_snapshot_and_advertised_read_instances"}
        return original_route(path, body)

    monkeypatch.setattr(world, "route", route)
    selection = service.register(model.artifact_id)["selection_id"]
    entry = app.models.selection(selection)
    original_manifest = app.models.entry_path(entry, "manifest")
    original_config = app.models.entry_path(entry, "config")
    original = (original_manifest.read_bytes(), original_config.read_bytes(),
                model.artifact_id)

    def prepare(mode, intent):
        kind = "live_ui" if mode == "native-ui" else "headless"
        world.caps["host"]["host_kind"] = kind
        fixture = json.loads(native_app[2].read_bytes())
        fixture["identity"]["host"]["host_kind"] = kind
        atomic_json(native_app[2], fixture)
        return prepared(app, prepare_body(intent, display_mode=mode))

    return app, world, selection, prepare, original, original_manifest, original_config


@pytest.mark.parametrize("p1_registration", ["trained"], indirect=True)
def test_headless_model_to_native_ui_and_back_reuses_bytes_and_exact_bindings(reference_p1):
    app, world, selection, prepare, original, manifest_path, config_path = reference_p1
    selected = app.models.composition(selection)
    first = prepare("native-ui", "1" * 32)
    request = app.model_agent_request(selection, "prepare-and-load", "short",
                                      native_episode_ref=first["environment_ref"])
    assert request["selection_id"] != selection
    assert request["operation"] == "prepare-and-load"
    assert request["admission_context"]["native_episode"] == first["admission_context"]
    assert _same_model_binding(selected, request)
    bound_entry = app.models.selection(request["selection_id"])
    bound_manifest = json.loads(app.models.entry_path(bound_entry, "manifest").read_bytes())
    bound_config = json.loads(app.models.entry_path(bound_entry, "config").read_bytes())
    producer = {key: value for key, value in world.caps["host"].items()
                if key != "runtime_instance_id"}
    assert bound_manifest["requirements"]["environment"]["producer_sha256"] == hashlib.sha256(
        canonical_json(producer).encode()).hexdigest()
    assert bound_manifest["requirements"]["environment"]["host_kind"] == "reference"
    assert bound_config["model_artifact_id"] == original[2]
    assert bound_config["package_path"] == json.loads(original[1])["package_path"]
    assert (manifest_path.read_bytes(), config_path.read_bytes()) == original[:2]
    assert app.local_model_registration.status(original[2])["selection_id"] == selection
    catalog = LocalModelService(app.config).catalog()
    assert request["selection_id"] not in {row["selection_id"] for row in catalog["policies"]}
    changed = copy.deepcopy(request)
    changed["composition"]["semantic_definition"]["models"][0]["weights_sha256"] = "0" * 64
    changed["composition"].pop("composition_id")
    changed["composition_id"] = hashlib.sha256(
        canonical_json(changed["composition"]).encode()).hexdigest()
    changed["composition"]["composition_id"] = changed["composition_id"]
    assert not _same_model_binding(selected, changed)
    assert app.finish_native_episode(first["environment_ref"])["finish_complete"]
    second = prepare("headless", "2" * 32)
    returning = app.model_agent_request(selection, "prepare-and-load", "short",
                                         native_episode_ref=second["environment_ref"])
    assert returning["selection_id"] == selection
    assert returning["admission_context"]["native_episode"] == second["admission_context"]
    assert len(app.local_model_registration._entries()) == 2
    assert not any("controller/acquire" in row["path"] or row["path"].endswith("/actions")
                   for row in world.requests), "binding does not take control or act"


@pytest.mark.parametrize("p1_registration", ["trained"], indirect=True)
@pytest.mark.parametrize("transport", ["gui-prepare", "developer-cli"])
def test_public_prepare_uses_exact_ref_and_loads_human_without_takeover(
    reference_p1, monkeypatch, transport, capsys,
):
    app, _, selection, prepare, _, _, _ = reference_p1
    resource = prepare("native-ui", "3" * 32)
    loads = []
    monkeypatch.setattr(app.models, "_start", lambda identity, *_:
        loads.append((identity, copy.deepcopy(app.models.state["native_episode"]))))
    monkeypatch.setattr(app.models, "_execute_command", lambda *_:
        pytest.fail("prepare-and-load must never request Auto"))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    atomic_json(app.config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(app.config),
        "port": server.server_port, "control_token": app.control_token})
    headers = {"Authorization": "Bearer " + app.control_token, "Content-Type": "application/json"}
    url = f"http://127.0.0.1:{server.server_port}/api/local-models/prepare"
    try:
        with pytest.raises(HTTPError) as rejected:
            urlopen(Request(url, data=json.dumps({"selection_id": selection}).encode(),
                            headers=headers), timeout=10)
        assert rejected.value.code == 409, "unbound load cannot use a reserved resource"
        for value in (None, True):
            with pytest.raises(HTTPError) as invalid:
                urlopen(Request(url, data=json.dumps({"selection_id": selection,
                    "native_episode_ref": value}).encode(), headers=headers), timeout=10)
            assert invalid.value.code == 409
        assert not loads
        if transport == "gui-prepare":
            with urlopen(Request(url, data=json.dumps({"selection_id": selection,
                    "run_profile": "short", "native_episode_ref": resource["environment_ref"]}
                    ).encode(), headers=headers), timeout=30) as reply:
                offered = json.load(reply)
        else:
            from spireagent.workbench.developer_cli import main

            assert main(["native-episode", "--config", str(app.config_path),
                "--action", "run-agent", "--selection", selection,
                "--native-episode-ref", resource["environment_ref"],
                "--run-operation", "prepare-and-load", "--run-profile", "short"]) == 0
            offered = json.loads(capsys.readouterr().out)
        assert offered["operation"]["action"] == "prepare-and-load"
        app.models.thread.join(timeout=15)
        assert not app.models.thread.is_alive()
        assert app.models.status()["operation"]["status"] == "completed"
        assert len(loads) == 1 and loads[0][0] != selection
        assert loads[0][1] == resource["admission_context"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("p1_registration", ["trained"], indirect=True)
def test_wrong_ref_and_changed_runtime_reject_before_execution(reference_p1, monkeypatch):
    app, world, selection, prepare, _, _, _ = reference_p1
    resource = prepare("native-ui", "4" * 32)
    before = len(app.local_model_registration._entries())
    with pytest.raises(BoundaryError, match="foreign_resource"):
        app.model_agent_request(selection, "prepare-and-load", "short",
                                native_episode_ref="f" * 32)
    real_negotiate = app.local_model_registration._global_environment_manifest

    def replaced(*args, **kwargs):
        result = real_negotiate(*args, **kwargs)
        world.caps["host"]["runtime_instance_id"] = "replacement-runtime"
        return result

    monkeypatch.setattr(app.local_model_registration, "_global_environment_manifest", replaced)
    with pytest.raises(BoundaryError, match="runtime_or_installed_tuple_changed"):
        app.model_agent_request(selection, "prepare-and-load", "short",
                                native_episode_ref=resource["environment_ref"])
    assert len(app.local_model_registration._entries()) == before
    assert app.models.thread is None and app.models.process is None


@pytest.mark.parametrize("p1_registration", ["trained"], indirect=True)
def test_negotiate_timeout_is_not_retried_or_followed_by_runtime(reference_p1, monkeypatch):
    app, _, selection, prepare, _, _, _ = reference_p1
    resource = prepare("native-ui", "5" * 32)
    attempts = []

    def unknown(*args, **kwargs):
        attempts.append(args)
        raise subprocess.TimeoutExpired("synthetic returned-lost negotiation", 1)

    monkeypatch.setattr(app.local_model_registration, "_global_environment_manifest", unknown)
    before = len(app.local_model_registration._entries())
    with pytest.raises(subprocess.TimeoutExpired):
        app.model_agent_request(selection, "prepare-and-load", "short",
                                native_episode_ref=resource["environment_ref"])
    assert len(attempts) == 1 and len(app.local_model_registration._entries()) == before
    assert app.models.thread is None and app.models.process is None


@pytest.mark.parametrize("p1_registration", ["trained"], indirect=True)
def test_native_start_passes_ready_reference_through_original_application_and_request(
    reference_p1, monkeypatch,
):
    from spireagent.workbench.native_workbench_access import NativePair
    from spireagent.workbench.native_workbench_api import NativeWorkbenchApi

    app, world, selection, prepare, original, manifest_path, config_path = reference_p1
    ready = prepare("native-ui", "d" * 32)
    pair = NativePair(world.caps["host"]["runtime_instance_id"], app.instance_id,
                      configuration_id(app.config), "http://127.0.0.1:12345/", "a" * 32, 9999999999)
    calls = []
    monkeypatch.setattr(app.native_access, "intent_authorizer", lambda context, expected:
                        lambda: calls.append(("pair", context, expected)))
    monkeypatch.setattr(app.models, "readiness",
        lambda _: {"checks": {"runtime_package": {"status": "pass"}}})

    def start(identity, intent, profile="short"):
        calls.append(("start", identity, profile))
        from types import SimpleNamespace

        app.models.client = SimpleNamespace(request=lambda _: {"status": {
            "lifecycle": "running", "mode": "human"}})
        app.models.state.update(status="loaded", loaded=True)
    monkeypatch.setattr(app.models, "_start", start)
    monkeypatch.setattr(app.models, "_execute_command", lambda mode, *args: calls.append((mode,)))
    api = NativeWorkbenchApi(app)
    api._dispatch("models.takeover", {"selection_id": selection, "run_profile": "continuous",
        "native_episode_ref": ready["environment_ref"]}, pair=pair, request_id="b" * 32)
    app.models.thread.join(5)
    assert app.models.state["operation"]["status"] == "completed"
    request_ref = app.models.state["agent_run_request"]
    request = json.loads((app.models.directory / request_ref["path"]).read_bytes())
    assert request["budget"]["limits"] == {"mode": "continuous"}
    assert request["admission_context"]["native_episode"] == ready["admission_context"]
    assert request["admission_context"]["native_intent"] is None
    assert app.models.state["_native_intent"]["binding"] == pair.to_dict()
    assert request["selection_id"] != selection
    assert [entry[0] for entry in calls if entry[0] in {"start", "auto"}] == ["start", "auto"]
    assert (manifest_path.read_bytes(), config_path.read_bytes()) == original[:2]
    app.models.client = None
    app.models.state.update(status="stopped", loaded=False)
