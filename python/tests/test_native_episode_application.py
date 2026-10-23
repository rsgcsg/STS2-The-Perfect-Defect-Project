"""Application ownership and real public Node pipe seam over a synthetic World.

No test in this file discovers/starts STS2 or qualifies native mode behavior.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import threading
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.package_identity import directory_sha256
from spireagent.workbench import agent_run_request
from spireagent.workbench.developer import ProjectConfig, atomic_json, combination
from spireagent.workbench.developer_server import Application, configuration_id
from spireagent.workbench.local_environment import HOST_PACKAGE
from spireagent.workbench.native_episode_binding import (
    PROFILE_FILE,
    PROFILE_SCHEMA,
    native_exit_disposition,
)

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "components/host-runtime"
spec = importlib.util.spec_from_file_location(
    "native_public_client_fixture", HOST / "consumers/python/sts2_headless/client.py"
)
assert spec and spec.loader
public_client = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = public_client
spec.loader.exec_module(public_client)


def capabilities():
    return {
        "protocol_version": "1.0.0",
        "execution_available": True,
        "host": {
            "host_kind": "headless",
            "runtime_instance_id": "runtime-original",
            "implementation": {
                "artifact_sha256": "b" * 64,
                "source_revision": "c" * 40,
                "module_version_id": "01234567-89ab-cdef-0123-456789abcdef",
            },
        },
        "game": {
            "version": "fixture-game",
            "commit": "fixture-commit",
            "modset": {"status": "exact_platform_modset", "fingerprint": "d" * 64},
        },
    }


def prepare_body(intent="a" * 32, **changes):
    return {
        "ownership": "owned",
        "intent_id": intent,
        "seed": "ABC123",
        "display_mode": "headless",
        "speed": "instant",
        **changes,
    }


@pytest.fixture
def native_endpoint():
    return "http://127.0.0.1:15526"


@pytest.fixture
def native_caps():
    return capabilities()


@pytest.fixture
def native_app(tmp_path, monkeypatch, native_endpoint, native_caps):
    state = tmp_path / "state"
    state.mkdir()
    (state / "native-host").mkdir()
    config = ProjectConfig(state, "", native_endpoint, None, combination())
    path = tmp_path / "project.json"
    atomic_json(path, config.to_dict())
    app = Application(config, config_path=path)
    atomic_json(
        state / "runtime.json",
        {"instance_id": app.instance_id, "configuration_id": configuration_id(config)},
    )
    package = tmp_path / "host-package"
    (package / "tools").mkdir(parents=True)
    (package / "consumers/python/sts2_headless").mkdir(parents=True)
    for name in ("__init__.py", "client.py"):
        shutil.copyfile(
            HOST / "consumers/python/sts2_headless" / name,
            package / "consumers/python/sts2_headless" / name,
        )
    log, fixture = state / "pipe.jsonl", state / "world.json"
    caps = native_caps
    identity = {
        "protocol": "1.0.0",
        "host": caps["host"],
        "game": caps["game"],
        "disk": {"sts2_assembly": {"sha256": "e" * 64}},
        "episode_provenance": {"verdict": "provenance_pass", "actual_seed": "ABC123",
            "requested_seed": "ABC123",
            "runtime_instance_id": native_caps["host"]["runtime_instance_id"]},
        "profile": {
            "template_id": "defect-a0-s0",
            "template_payload_sha256": "f" * 64,
            "generation_id": "profile-generation",
        },
        "native_speed": {
            "ready": True,
            "requested": "instant",
            "effective": "instant",
            "before": "normal",
        },
    }
    atomic_json(fixture, {"identity": identity, "log": str(log)})
    fixture_module = (HOST / "tools/test-fixtures/reference-episode.mjs").as_uri()
    (package / "tools/reference-pe-driver.mjs").write_text(
        f"process.argv.push('--fixture', {json.dumps(str(fixture))});\n"
        "await import(" + json.dumps(fixture_module) + ");\n"
    )
    (package / "package.json").write_text(
        json.dumps({"name": HOST_PACKAGE, "version": "1.1.0-rc.27"})
    )
    pin = {
        "schema": "stpd/platform-host-runtime-pin-v1",
        "package": HOST_PACKAGE,
        "version": "1.1.0-rc.27",
        "source_revision": "a" * 40,
        "component_tree_revision": "b" * 40,
        "release_asset_sha256": "c" * 64,
        "package_content_sha256": directory_sha256(package),
    }
    from spireagent.workbench.native_episode_binding import installed_tuple

    game = tmp_path / "game"
    game.mkdir()
    profile = {
        "schema": PROFILE_SCHEMA,
        "host_package_directory": str(package),
        "host_package_pin": pin,
        "game_directory": str(game),
        "template_id": "defect-a0-s0",
        "endpoint": config.platform_url,
        "installed_tuple": installed_tuple(caps, "e" * 64),
        "preparation_timeout_seconds": 5,
        "cleanup_timeout_seconds": 5,
        "candidate_admission": {"game": "exact_canary", "connector": "exact_canary"},
        "default_speed": None,
    }
    atomic_json(state / PROFILE_FILE, profile)
    service = app.local_environment
    monkeypatch.setattr(service, "seed_normalizer", lambda *_args: _args[-1])
    monkeypatch.setattr(service, "native_capabilities", lambda _endpoint: native_caps)

    def client(command, _profile, **callbacks):
        assert (
            not app.models.lock._is_owned()
        )  # Network/process await lane is outside the admission lock.
        return public_client.ReferencePlayerEnvironment(
            command, response_timeout_seconds=5, **callbacks
        )

    monkeypatch.setattr(service, "native_client_factory", client)
    yield app, profile, fixture, log
    try:
        current = service.native_episode_status()
        if current.get("environment_ref") and not current.get("finish_complete"):
            app.finish_native_episode(current["environment_ref"])
    finally:
        app.close()


def prepared(app, body=None):
    app.prepare_native_episode(body or prepare_body())
    worker = app.local_environment.native_worker
    worker.join(timeout=8)
    assert not worker.is_alive()
    state = app.native_episode_status()
    assert state["phase"] == "ready", state
    return state


def test_owned_public_pipe_release_and_same_intent_do_not_reset_again(native_app):
    app, _profile, _fixture, log = native_app
    ready = prepared(app)
    assert app.prepare_native_episode(prepare_body())["environment_ref"] == ready["environment_ref"]
    with pytest.raises(BoundaryError, match="preparation_intent_conflict"):
        app.prepare_native_episode(prepare_body(seed="OTHER"))
    assert [json.loads(line)["event"] for line in log.read_text().splitlines()].count(
        "reset_started"
    ) == 1
    binding = ready["admission_context"]["binding"]
    assert binding["ownership"] == "owned" and binding["handoff"]["controller"] is None
    assert "profile_root" not in json.dumps(binding) and "host_control_token" not in json.dumps(
        binding
    )


@pytest.mark.parametrize("requested", ["default", "normal", "fast", "instant"])
def test_owned_default_resolves_instant_and_explicit_modes_keep_native_readback(
    native_app, monkeypatch, requested
):
    app, _profile, fixture, _log = native_app
    effective = "instant" if requested == "default" else requested
    value = json.loads(fixture.read_text())
    value["identity"]["native_speed"].update(requested=effective, effective=effective)
    atomic_json(fixture, value)
    original_client = app.local_environment.native_client_factory

    def client(command, *args, **kwargs):
        assert command.count("--native-speed") == 1
        assert command[command.index("--native-speed") + 1] == effective
        return original_client(command, *args, **kwargs)

    monkeypatch.setattr(app.local_environment, "native_client_factory", client)
    binding = prepared(app, prepare_body(speed=requested))["admission_context"]["binding"]
    assert binding["speed"]["requested"] == effective
    assert binding["speed"]["effective"] == effective
    assert binding["speed"]["basis"] == "native_typed_readback"
    assert binding["speed"]["configured_default"] == (
        {"mode": "instant", "basis": "project_default"} if requested == "default" else None
    )
    assert "qualification_ref" not in json.dumps(binding["speed"])


@pytest.mark.parametrize(
    "changes",
    [
        {"speed": "None"},
        {"display_mode": "editor"},
        {"endpoint": "http://foreign"},
        {"PID": 17},
    ],
)
def test_closed_configuration_rejects_before_client_construction(native_app, changes):
    app, _profile, _fixture, log = native_app
    with pytest.raises(BoundaryError):
        app.prepare_native_episode(prepare_body(**changes))
    assert not log.exists()
    assert app.local_environment.native_client is None


def test_missing_capability_and_unknown_handoff_never_publish_ready(native_app):
    app, _profile, fixture, log = native_app
    value = json.loads(fixture.read_text())
    value["missingCapability"] = True
    atomic_json(fixture, value)
    app.prepare_native_episode(prepare_body())
    app.local_environment.native_worker.join(timeout=8)
    assert app.native_episode_status()["phase"] == "failed"
    assert "reset_started" not in log.read_text()


def test_immutable_context_and_changed_profile_fence_without_host_rpc(native_app):
    app, profile, _fixture, log = native_app
    ready = prepared(app)
    context = ready["admission_context"]
    before = log.read_bytes()
    app.local_environment.authorize_native_episode(context)
    assert log.read_bytes() == before
    profile["cleanup_timeout_seconds"] = 6
    atomic_json(app.config.state_dir / PROFILE_FILE, profile)
    with pytest.raises(BoundaryError, match="configuration_changed"):
        app.local_environment.authorize_native_episode(context)
    assert log.read_bytes() == before


def test_finish_stops_actor_before_owned_close_and_reuses_original_cleanup(native_app, monkeypatch):
    app, _profile, _fixture, _log = native_app
    ready = prepared(app)
    events = []
    client = app.local_environment.native_client
    original = client.close

    def close(*args, **kwargs):
        events.append("host_close")
        return original(*args, **kwargs)

    monkeypatch.setattr(client, "close", close)

    def stop(context, **_kwargs):
        assert not app.models.lock._is_owned()
        events.append("actor_stop_and_seal")
        return {
            "status": "stopped",
            "run_id": "fixture-actor",
            "seal": "unknown",
            "seal_error": "fixture",
        }

    monkeypatch.setattr(app.models, "stop_native_episode_actor", stop)
    closed = app.finish_native_episode(ready["environment_ref"])
    assert events == ["actor_stop_and_seal", "host_close"]
    assert closed["phase"] == "closed"
    assert closed["cleanup"]["host_close"] == "observed_graceful"
    assert closed["cleanup"]["native_exit_disposition"] == "graceful"
    assert closed["actor_finish"]["seal"] == "unknown"
    assert app.finish_native_episode(ready["environment_ref"]) == closed
    assert len(events) == 2


def test_shutdown_shared_fence_rejects_inflight_prepare_before_resource_reservation(
    native_app, monkeypatch
):
    app, _profile, _fixture, log = native_app
    entered, release = threading.Event(), threading.Event()
    original = app.local_environment._read_native_profile

    def delayed():
        entered.set()
        release.wait(timeout=2)
        return original()

    monkeypatch.setattr(app.local_environment, "_read_native_profile", delayed)
    errors = []

    def prepare():
        try:
            app.prepare_native_episode(prepare_body())
        except BoundaryError as error:
            errors.append(error.code)

    worker = threading.Thread(target=prepare)
    worker.start()
    assert entered.wait(timeout=2)
    app.close()
    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert errors and not log.exists()
    assert app.native_episode_status()["phase"] == "idle"


def test_attached_resource_never_bootstraps_writes_speed_or_closes_host(native_app, monkeypatch):
    app, _profile, _fixture, log = native_app
    monkeypatch.setattr(app.local_environment, "_native_unheld", lambda *_args: None)
    ready = prepared(app, {"ownership": "attached", "intent_id": "a" * 32})
    assert app.local_environment.native_client is None and not log.exists()
    binding = ready["admission_context"]["binding"]
    assert not {"profile", "speed", "presentation", "handoff", "host_package_pin"} & set(binding)
    assert "game_assembly_sha256" not in binding["installed_tuple"]
    closed = app.finish_native_episode(ready["environment_ref"])
    assert closed["cleanup"]["host_close"] == "not_applicable" and not log.exists()


def test_current_display_prepares_real_attached_resource_without_reset(native_app, monkeypatch):
    app, _profile, _fixture, log = native_app
    monkeypatch.setattr(app.local_environment, "_native_unheld", lambda *_args: None)
    ref = app.prepare_current_native_episode("runtime-original")
    record = app.native_episode_status()
    assert record["environment_ref"] == ref and record["phase"] == "ready"
    assert record["preparation"]["ownership"] == "attached"
    assert record["admission_context"]["binding"]["initialization"] == "cold_current_segment"
    assert app.prepare_current_native_episode("runtime-original", ref) == ref
    assert not log.exists() and app.local_environment.native_client is None
    with pytest.raises(BoundaryError, match="native_model_context_changed"):
        app.prepare_current_native_episode("foreign-runtime", ref)
    with pytest.raises(BoundaryError, match="resource_binding_changed"):
        app.prepare_current_native_episode("runtime-original", "foreign-ref")
    assert app.native_episode_status()["environment_ref"] == ref


def test_current_display_never_replaces_owned_seeded_episode(native_app):
    app, _profile, _fixture, log = native_app
    ready = prepared(app)
    before = log.read_bytes()
    assert app.prepare_current_native_episode("runtime-original") == ready["environment_ref"]
    assert app.native_episode_status() == ready and log.read_bytes() == before


@pytest.mark.parametrize("next_runtime", ["runtime-original", "runtime-next"])
def test_current_display_new_segment_requires_original_actor_seal(
    native_app, monkeypatch, next_runtime,
):
    app, _profile, _fixture, log = native_app
    monkeypatch.setattr(app.local_environment, "_native_unheld", lambda *_args: None)
    ref = app.prepare_current_native_episode("runtime-original")
    context = app.local_environment.native_episode_context(ref)
    request = {"path": "requests/original.json", "sha256": "1" * 64}
    app.local_environment.associate_native_actor(context, "run-original", request)
    original = app.native_episode_status()
    # The real Model owner has no stopped/sealed Actor here: it must refuse
    # before the Environment can detach or create a replacement segment.
    with pytest.raises(BoundaryError, match="stopped_actor_required"):
        app.prepare_current_native_episode("runtime-original", ref)
    assert app.native_episode_status() == original
    # Isolate only the already-tested typed Model seal boundary. The real
    # Environment must archive/detach and prepare a different attached resource;
    # it must never issue Host reset/close, speed or native gameplay writes.
    monkeypatch.setattr(app.models, "native_actor_retirement_proof", lambda *_: {})
    monkeypatch.setattr(app.models, "stop_native_episode_actor", lambda *_args, **_kw: {
        "status": "stopped", "run_id": "run-original", "seal": "confirmed"})
    current = capabilities()
    current["host"]["runtime_instance_id"] = next_runtime
    monkeypatch.setattr(app.local_environment, "native_capabilities", lambda _: current)
    successor = app.prepare_current_native_episode(next_runtime, ref)
    assert successor != ref
    assert app.native_episode_status()["admission_context"]["binding"]["initialization"] == (
        "cold_current_segment")
    assert not log.exists()


def test_legacy_request_shape_stays_exact_and_optional_native_binding_is_by_value():
    composition = {
        "selection_id": "fixture",
        "composition_id": "a" * 64,
        "semantic_definition": {"task_binding": None, "program": None},
    }
    request = agent_run_request.create(composition, "start", "short", None, "native")
    assert request["admission_context"] == {
        "kind": "existing_application_precondition",
        "native_intent": None,
    }
    assert "native_episode" not in json_bytes(request).decode()


@pytest.mark.parametrize(
    "exit_value,disposition",
    [
        (None, "unknown"),
        ({"code": 0}, "unknown"),
        ({"code": 0, "signal": None, "forced": True, "host_shutdown": {}}, "forced"),
        ({"code": 1, "signal": None, "forced": False, "host_shutdown": {}}, "failed"),
    ],
)
def test_driver_zero_cannot_relabel_native_exit(exit_value, disposition):
    assert (
        native_exit_disposition(exit_value, "runtime-original")["native_exit_disposition"]
        == disposition
    )


def test_replacement_binding_is_refused_before_recorder_prepare_or_actor_control(
    native_app, monkeypatch
):
    from test_local_models import environment, status

    app, _profile, _fixture, _log = native_app
    ready = prepared(app)
    app.models.state["native_episode"] = ready["admission_context"]
    native_posts, actor_posts = [], []

    class RuntimeFixture:
        def request(self, route, _body=None, **_kwargs):
            if route == "/status":
                return {"status": status()}
            if route == "/environment":
                return environment()
            actor_posts.append(route)
            raise AssertionError("No mutation may be offered")

    monkeypatch.setattr(app.models.native_tasks, "connector_instance", lambda _endpoint: "game-1")
    monkeypatch.setattr(
        app.models.native_tasks,
        "prepare_model",
        lambda *_args, **_kwargs: native_posts.append("POST"),
    )
    with pytest.raises(BoundaryError, match="runtime_or_installed_tuple_changed"):
        app.models._execute_command(
            "auto", app.models.intent_generation, RuntimeFixture(), app.config.platform_url, None
        )
    assert not native_posts and not actor_posts


def test_recording_reservation_and_unbound_actor_creation_are_fenced(native_app):
    app, _profile, _fixture, _log = native_app
    prepared(app)
    with pytest.raises(BoundaryError, match="native_resource_reserved"):
        app.models.start("unregistered")
    with (
        pytest.raises(BoundaryError, match="native_resource_reserved"),
        app.models.reserve_recording_source_mutation(require_human=False),
    ):
        raise AssertionError("Source mutation cannot enter")


def test_unknown_handoff_quarantines_original_and_never_admits_actor(native_app):
    app, _profile, fixture, _log = native_app
    value = json.loads(fixture.read_text())
    value["held"] = True
    atomic_json(fixture, value)
    app.prepare_native_episode(prepare_body())
    app.local_environment.native_worker.join(timeout=8)
    record = app.native_episode_status()
    assert record["phase"] == "unknown" and record["admission_context"] is None
    with pytest.raises(BoundaryError, match="resource_not_ready"):
        app.local_environment.native_episode_context(record["environment_ref"])


def test_external_native_state_absence_or_symlink_rejects_before_driver(native_app, monkeypatch):
    import subprocess

    app, _profile, _fixture, log = native_app
    root = app.config.state_dir / "native-host"
    root.rmdir()
    calls = []
    monkeypatch.setattr(
        app.local_environment,
        "native_client_factory",
        lambda *_a, **_kw: calls.append("constructed"),
    )
    with pytest.raises(BoundaryError, match="native_profile_required_or_invalid"):
        app.prepare_native_episode(prepare_body())
    # The existing Node directory-junction convention needs no Windows
    # symlink privilege; Node is already required by this public pipe fixture.
    subprocess.run(
        [
            "node",
            "--eval",
            "require('node:fs').symlinkSync(process.argv[1],process.argv[2],"
            "process.platform==='win32'?'junction':'dir')",
            str(app.config.state_dir),
            str(root),
        ],
        check=True,
        capture_output=True,
        timeout=5,
    )
    try:
        with pytest.raises(BoundaryError, match="native_profile_required_or_invalid"):
            app.prepare_native_episode(prepare_body())
    finally:
        if sys.platform == "win32":
            root.rmdir()
        else:
            root.unlink()
        root.mkdir()
    assert calls == [] and not log.exists()


@pytest.mark.parametrize("fault", ["missing", "wrong-root"])
def test_external_state_ready_proof_is_required_before_reset(native_app, fault):
    app, _profile, fixture, log = native_app
    value = json.loads(fixture.read_bytes())
    if fault == "missing":
        value["missingStateCapability"] = True
    else:
        value["stateRootReadback"] = {
            "schema": "sts2.host-runtime/reference-state-root-1",
            "mode": "external",
            "local_root": str(app.config.state_dir),
            "evidence_root": str(app.config.state_dir / "evidence"),
        }
    atomic_json(fixture, value)
    app.prepare_native_episode(prepare_body())
    app.local_environment.native_worker.join(timeout=8)
    assert not app.local_environment.native_worker.is_alive()
    state = app.native_episode_status()
    assert state["phase"] == "failed", state
    assert state["error_code"] == "native_external_state_unproven"
    assert state["world_mutation_offered"] is False
    assert "reset_started" not in [
        json.loads(line)["event"] for line in log.read_bytes().splitlines()
    ]


def test_two_serial_owned_preparations_use_real_external_template_and_preserve_package(native_app):
    import subprocess

    from spireagent.package_identity import validate_installed_package

    app, profile, fixture, log = native_app
    root = app.config.state_dir / "native-host"
    game_identity = {
        "platform": "win32",
        "architecture": "x64",
        "release": {
            "version": "fixture-game",
            "commit": "fixture-commit",
            "main_assembly_hash": 123,
        },
        "executable": {"sha256": "a" * 64},
        "runtime_main_assembly_hash": 123,
        "sts2_assembly": {"sha256": "e" * 64},
        "godotsharp_assembly": {"sha256": "f" * 64},
    }
    # The real existing template producer creates its exact directory/manifest;
    # only native World execution is synthetic in the public pipe fixture.
    script = """
import fs from 'node:fs'; import path from 'node:path';
const {isolatedProfileLaunch}=await import(process.argv[1]);
const {captureProfileTemplate}=await import(process.argv[2]);
const root=process.argv[3], game=JSON.parse(process.argv[4]);
const original=isolatedProfileLaunch(root,'template-source','win32');
const file=path.join(original.expected_user_data_root,'default/1/settings.save');
fs.mkdirSync(path.dirname(file),{recursive:true});
fs.writeFileSync(file,JSON.stringify({schema_version:8,synthetic:true}));
console.log(JSON.stringify(captureProfileTemplate({localRoot:root,profileId:'template-source',
  templateId:'defect-a0-s0',gameIdentity:game})));
"""
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "--eval",
            script,
            (HOST / "src/profile-isolation.mjs").as_uri(),
            (HOST / "src/profile-template.mjs").as_uri(),
            str(root),
            json.dumps(game_identity),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    captured = json.loads(result.stdout)
    value = json.loads(fixture.read_bytes())
    value["templateIdentity"] = game_identity
    atomic_json(fixture, value)
    package = Path(profile["host_package_directory"])
    before = directory_sha256(package)
    generations = []
    for intent in ("a" * 32, "b" * 32):
        ready = prepared(app, prepare_body(intent=intent))
        binding = ready["admission_context"]["binding"]
        generations.append(binding["profile"]["generation_id"])
        assert binding["profile"]["template_payload_sha256"] == captured["payload_sha256"]
        assert ready["host_state_root"]["local_root"] == str(root)
        result = app.finish_native_episode(ready["environment_ref"])
        assert result["phase"] == "closed", result
        validate_installed_package(
            package, profile["host_package_pin"], required_paths=("tools/reference-pe-driver.mjs",)
        )
        assert directory_sha256(package) == before
    assert generations[0] != generations[1]
    states = [
        json.loads(line)
        for line in log.read_bytes().splitlines()
        if json.loads(line)["event"] == "external_state"
    ]
    assert len(states) == 2
    for state in states:
        assert state["localRoot"] == str(root)
        assert state["evidenceRoot"] == str(root / "evidence")
        assert Path(state["profile"]["profile_root"]).is_relative_to(root / "profiles")
    assert (root / "profile-templates/defect-a0-s0/template.json").is_file()
    assert (root / "evidence/fixture-episodes.jsonl").is_file()
    assert not (package / ".local").exists()


def test_retiring_actor_preserves_original_resource_and_fences_failed_publication(
    native_app, monkeypatch,
):
    app = native_app[0]
    ready = prepared(app)
    context = ready["admission_context"]
    request = {"path": "requests/original.json", "sha256": "1" * 64}
    # This isolates the Environment's internal consumer boundary. Actual typed
    # Stop/seal proof is covered by the common-Teacher process test.
    proof = {"run_id": "run-original", "request": request,
             "admission_context": context, "evaluation": {"source": "typed-owner-fixture"}}
    app.local_environment.associate_native_actor(context, "run-original", request)
    before = app.local_environment.native_episode_status()
    with pytest.raises(BoundaryError, match="actor_retirement_binding_changed"):
        app.local_environment.retire_native_actor(context, {**proof, "run_id": "run-foreign"})
    assert app.local_environment.native_episode_status() == before
    with monkeypatch.context() as patch:
        def failed_save():
            raise OSError("fixture publication unavailable")
        patch.setattr(app.local_environment, "_save_native", failed_save)
        with pytest.raises(BoundaryError, match="actor_retirement_persistence_unconfirmed"):
            app.local_environment.retire_native_actor(context, proof)
        assert app.local_environment.native_record["phase"] == "actor-associated"
        assert app.local_environment.native_record["actor"] == before["actor"]
        with pytest.raises(BoundaryError, match="resource_already_associated"):
            app.local_environment.native_admission_guard(context, "creation")


@pytest.mark.parametrize("field", ["actual_seed", "requested_seed", "runtime_instance_id",
                                   "generation_id", "missing"])
def test_reuse_rejects_changed_host_identity_before_retiring_original_actor(
    native_app, monkeypatch, field,
):
    app = native_app[0]
    ready = prepared(app)
    context = ready["admission_context"]
    request = {"path": "requests/original.json", "sha256": "1" * 64}
    app.local_environment.associate_native_actor(context, "run-original", request)
    original = app.local_environment.native_episode_status()
    client = app.local_environment.native_client
    # Real public Host client/JSONL producer supplies the complete identity. Only
    # the synthetic World's subsequent readback is changed at this wire seam.
    identity = client.episode_identity()
    app.local_environment.verify_native_actor_reuse(context)
    changed = json.loads(json.dumps(identity))
    if field == "generation_id":
        changed["profile"][field] = "another-generation"
    elif field == "missing":
        del changed["episode_provenance"]["actual_seed"]
    else:
        changed["episode_provenance"][field] = "another-world"
    monkeypatch.setattr(client, "episode_identity", lambda: changed)
    with pytest.raises(BoundaryError, match="native_world_identity_(changed|unavailable)"):
        app.local_environment.verify_native_actor_reuse(context)
    assert app.local_environment.native_episode_status() == original
    with pytest.raises(BoundaryError, match="resource_already_associated"):
        app.local_environment.native_admission_guard(context, "creation")


def test_closed_original_resource_does_not_block_replacement_game_pair(native_app, monkeypatch):
    app = native_app[0]
    ready = prepared(app)
    original = ready["admission_context"]
    replacement = "new-selected-game"
    # Only the Connector read is synthetic; closure below is the real public
    # Host pipe/Environment terminal producer and keeps the original context.
    monkeypatch.setattr(app.models.native_tasks, "connector_instance", lambda _: replacement)
    assert not app._workbench_runtime_identity(replacement)
    closed = app.finish_native_episode(ready["environment_ref"])
    assert closed["phase"] == "closed" and closed["finish_complete"]
    assert closed["admission_context"] == original
    assert app._workbench_runtime_identity(replacement)
    assert not app._workbench_runtime_identity("another-unselected-game")
