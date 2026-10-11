"""Real common Runtime CLI/Teacher/Decision4 children; synthetic World/backend.

Reuses the existing exact-archive or current-source public preflight owner.
The PublicM2 fixture counts tensor writes and loads no real weights or NN.
"""
# ruff: noqa: F811 -- pytest fixture parameters shadow imported fixture functions.

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import pytest
from native_episode_world import NativeEpisodeWorld
from test_local_models import public_native_preflight  # noqa: F401 -- shared real package owner
from test_native_episode_application import (
    ROOT,
    capabilities,
    native_app,  # noqa: F401 -- pytest fixture imported for this module's World injection
    prepared,
)

from spireagent.json_boundary import json_bytes
from spireagent.package_identity import directory_sha256
from spireagent.workbench.developer import atomic_json
from spireagent.workbench.local_model_registration import REGISTRY
from spireagent.workbench.runtime_install import validate_runtime_install


@pytest.fixture
def native_caps():
    value = capabilities()
    value.update(
        snapshot_schema="sts2.player-environment/snapshot-1",
        action_schema="sts2.player-environment/action-1",
        receipt_schema="sts2.player-environment/receipt-1",
        control_schema="sts2.player-environment/control-1",
        status="implemented",
        environment_fingerprint="environment-fixture",
        verbs=["end_turn"],
        snapshot_bound=True,
        single_controller=True,
        control={"recommended_renewal_ms": 1000},
        evidence_profiles=[],
        non_claims=["synthetic_world_only"],
    )
    value["host"].update(id="fixture-host", name="fixture", version="fixture")
    value["game"].update(
        branch=None,
        main_assembly_hash=None,
        compatibility={"status": "supported", "observation_allowed": True, "detail": "synthetic"},
    )
    value["game"]["modset"].update(
        scope="synthetic", loaded_mod_ids=["fixture-mod"], detail="synthetic"
    )
    return value


@pytest.fixture
def world(native_caps):
    instance = NativeEpisodeWorld(ROOT, native_caps)
    yield instance
    instance.close()


@pytest.fixture
def native_endpoint(world):
    return world.endpoint


@pytest.fixture(scope="module")
def installed_native_runtime(public_native_preflight):
    # The existing owner accepts an exact reviewed archive for bounded local
    # replay, or prepares the current real source package for ordinary CI. It
    # installs once before either Actor uses15527; never reinstall between cases.
    service, _manifests, temporary = public_native_preflight
    supplied = os.environ.get("STPD_TEST_NATIVE_RUNTIME_ARCHIVE")
    if supplied:
        archive = Path(supplied).resolve()
    else:
        report = json.loads((temporary / "package/policy-runtime-package.json").read_bytes())
        archive = temporary / "package" / report["filename"]
    pin = service.registry()["runtime_package"]
    before = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert before == pin["release_asset_sha256"]
    source = service.directory / "runtime/node_modules"
    validate_runtime_install(source, pin, service._connector_pin())
    content = directory_sha256(source)

    # Same source-test root/catalog and exact adapter closure pattern as the
    # existing common Runtime tests. Only immutable code/metadata/dependencies
    # are reused; every Application keeps its own mutable operation/Actor state.
    from stpd.policy.native_teacher_agent import CODE_FILES
    from stpd.public_m2_policy_installation import ADAPTER_SOURCE_CLOSURE

    closure = set(CODE_FILES) | set(ADAPTER_SOURCE_CLOSURE)
    expected = {}
    for name in sorted(closure):
        original, target = ROOT / "python" / name, service.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        expected[name] = hashlib.sha256(original.read_bytes()).hexdigest()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == expected[name]
    yield source, archive, before, pin, service.root
    assert directory_sha256(source) == content
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == before
    assert {
        name: hashlib.sha256((service.root / name).read_bytes()).hexdigest()
        for name in sorted(closure)
    } == expected


@pytest.fixture
def native_runtime(native_app, installed_native_runtime):
    app = native_app[0]
    source, archive, before, pin, source_root = installed_native_runtime
    # Use the original source-test configuration, never alter the production
    # catalog or mock package verification/readiness/launch ownership.
    app.models.root = source_root
    assert app.models.registry()["runtime_package"] == pin
    target = app.models.directory / "runtime/node_modules"
    shutil.copytree(source, target)
    assert directory_sha256(target) == directory_sha256(source)
    actual = app.models._runtime_package()
    assert actual["version"] == pin["version"]
    yield app, archive, before
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == before


def register_entry(models, identity, adapter, profile, config_path, manifest_path):
    atomic_json(
        models.private_root / REGISTRY,
        {
            "schema": "stpd/local-token-policies-v1",
            "policies": [
                {
                    "id": identity,
                    "label": identity,
                    "adapter": adapter,
                    "runtime_profile": profile,
                    "config": config_path.relative_to(models.private_root).as_posix(),
                    "manifest": manifest_path.relative_to(models.private_root).as_posix(),
                }
            ],
        },
    )


def finish_operation(models):
    assert models.thread is not None
    models.thread.join(timeout=15)
    assert not models.thread.is_alive(), "Common owner operation did not reach its terminal clock"
    state = models.status()
    assert state["operation"]["status"] == "completed", state
    return state


def common_start(app, ready, selection, world, log):
    # Synthetic native Recorder owner remains on a real local HTTP seam. The
    # Application's production NativeTasks preparation checks are unmodified.
    app.models.native_tasks.address = world.endpoint
    request = app.models.agent_request(
        selection, "start", native_episode_ref=ready["environment_ref"]
    )
    assert request["admission_context"]["native_episode"] == ready["admission_context"]
    original = copy.deepcopy(request)
    before = log.read_bytes()
    app.models.run_agent(request)
    state = finish_operation(app.models)
    assert state["loaded"] is True
    assert app.models.process is not None and app.models.process.poll() is None
    assert app.local_environment.native_episode_status()["phase"] == "actor-associated"
    reference = app.models.state["agent_run_request"]
    raw = (app.models.directory / reference["path"]).read_bytes()
    stored = json.loads(raw)
    assert (
        stored["admission_context"]["native_episode"]
        == original["admission_context"]["native_episode"]
    )
    assert "native_episode" not in json.dumps(
        original["composition"]["semantic_definition"]["input"]
    )
    assert log.read_bytes() == before  # No Host-driver RPC during Actor life.
    return state["startup"], reference, raw, before


def common_finish(app, ready, startup, reference, raw, host_before, log, world):
    result = app.finish_native_episode(ready["environment_ref"])
    assert result["phase"] == "closed", result
    assert result["actor_finish"]["status"] == "stopped"
    assert result["actor_finish"]["run_id"] == startup["run_id"]
    assert result["actor_finish"]["seal"] == "available"
    assert result["cleanup"]["native_exit_disposition"] == "graceful"
    assert app.models.process.poll() == 0
    assert not world.held
    assert (app.models.directory / reference["path"]).read_bytes() == raw
    host_after = log.read_bytes()
    assert host_after.startswith(host_before)
    tail = [json.loads(line)["event"] for line in host_after[len(host_before) :].splitlines()]
    assert tail == ["close", "driver_finished"]
    evaluation = app.models.state["evaluation"]
    assert evaluation["evidence_verification"] == "pass", evaluation
    assert evaluation["agent_execution"]["status"] == "bound"
    assert evaluation["agent_execution"]["native_episode"]["status"] == "bound"
    events = [
        json.loads(line)
        for line in (app.models.directory / "agent-runs" / startup["run_id"] / "events.jsonl")
        .read_bytes()
        .splitlines()
    ]
    assert "stopped" in [event["kind"] for event in events]
    assert all(
        "native_episode" not in json.dumps(event["payload"])
        for event in events
        if event["kind"]
        in {"agent_sample_next_requested", "agent_sample_input_stored", "public_decision_input"}
    )
    return events


def test_owned_episode_common_teacher_process_ack_stop_seal_then_host_close(
    native_runtime, world, native_app
):
    from spireagent.workbench.native_teacher_support import ADAPTER, installation_config
    from stpd.policy.native_teacher_agent import descriptor, write_artifact
    from stpd.policy.native_teacher_manifest import teacher_manifest

    app = native_runtime[0]
    folder = app.models.private_root / "fixture-teacher"
    folder.mkdir(parents=True)
    artifact = write_artifact(folder / "program.json")
    manifest = teacher_manifest(
        descriptor(), artifact, world.caps, world.seams, manifest_id="fixture-teacher"
    )
    config_path, manifest_path = folder / "config.json", folder / "manifest.json"
    config_path.write_bytes(
        json_bytes(installation_config(folder / "program.json", artifact["sha256"]))
    )
    manifest_path.write_bytes(json_bytes(manifest))
    register_entry(
        app.models, "fixture-teacher", ADAPTER, "native-logical-v1", config_path, manifest_path
    )
    assert app.models.readiness("fixture-teacher")["status"] == "ready_to_load"
    log = native_app[3]
    ready = prepared(app)
    startup, reference, raw, host_before = common_start(app, ready, "fixture-teacher", world, log)
    app.models.command("one_step")
    finish_operation(app.models)
    assert world.submitted == 1
    assert log.read_bytes() == host_before
    events = common_finish(app, ready, startup, reference, raw, host_before, log, world)
    kinds = [event["kind"] for event in events]
    assert "agent_consumed" in kinds and "agent_sample_consume_ack_offered" in kinds
    assert kinds.count("native_submission_requested") == 1
    consumed = next(event["payload"] for event in events if event["kind"] == "agent_consumed")
    assert consumed["report"]["state_version"] == 1
    assert consumed["acknowledgement"]["state_version"] == 1


def test_owned_episode_common_public_m2_process_preserves_ordinal_and_w_then_stop(
    native_runtime, world, native_app, tmp_path, monkeypatch
):
    # Only this test imports Torch. Existing numerical fixture uses additions
    # over a synthetic memory tensor; no trained weights, forward, fit or game.
    from test_public_m2_runtime import metadata_export

    import stpd.public_m2_policy_installation as installation
    from spireagent.workbench.local_model_registration import _requirements

    app = native_runtime[0]
    export, _model = metadata_export(tmp_path)
    folder = app.models.private_root / "fixture-public-m2"
    folder.mkdir(parents=True)
    config_path, manifest_path = folder / "config.json", folder / "manifest.json"
    requirements, support = _requirements(world.caps, input_profile=installation.PROFILE)
    installation.bind_public_m2_export(
        app.models.root,
        export,
        config_path,
        manifest_path,
        manifest_id="fixture-public-m2",
        policy={
            "id": "fixture-m2",
            "version": "1.0.0",
            "provider": "synthetic",
            "architecture": "counting_backend",
        },
        requirements=requirements,
        support=support,
        binding_root=app.models.private_root,
    )
    register_entry(
        app.models,
        "fixture-public-m2",
        installation.ADAPTER_ID,
        installation.PROFILE,
        config_path,
        manifest_path,
    )
    actual_package = installation.validate_package

    def numerical_fixture_package(path, **kwargs):
        # Preserve real metadata/lineage/payload hashing. The sole replaced leaf
        # is actual weight materialization, matching the existing counting child.
        return actual_package(path, **{**kwargs, "load_weights": False})

    monkeypatch.setattr(installation, "validate_package", numerical_fixture_package)
    trace = folder / "counting-trace.jsonl"
    original_arguments = app.models.adapter_arguments
    monkeypatch.setattr(
        app.models,
        "adapter_arguments",
        lambda entry: (
            [
                str(ROOT / "python/tests/public_m2_child.py"),
                str(manifest_path),
                str(trace),
                "registered",
            ]
            if entry["id"] == "fixture-public-m2"
            else original_arguments(entry)
        ),
    )
    assert app.models.readiness("fixture-public-m2")["status"] == "ready_to_load"
    ready, log = prepared(app), native_app[3]
    startup, reference, raw, host_before = common_start(app, ready, "fixture-public-m2", world, log)
    # Shadow uses the common CLI's autonomous worker. A concurrent manual Tick
    # may be refused as tick_in_progress; completing that HTTP operation does
    # not prove an input was consumed. Wait for the original producer records.
    replies, commands, observations = [], [], []
    client = app.models.client
    assert client is not None
    request = client.request

    def capture_reply(route, *args, **kwargs):
        result = request(route, *args, **kwargs)
        if route in {"/mode", "/tick"}:
            replies.append({"route": route, "response": copy.deepcopy(result)})
        return result

    monkeypatch.setattr(client, "request", capture_reply)
    event_path = app.models.directory / "agent-runs" / startup["run_id"] / "events.jsonl"

    def completed_public_input(sequence):
        deadline = time.monotonic() + 5
        snapshot_id = f"public-{sequence}"
        while time.monotonic() < deadline:
            state = app.models.status()
            try:
                rows = [json.loads(line) for line in trace.read_bytes().splitlines()]
                events = [json.loads(line) for line in event_path.read_bytes().splitlines()]
            except (FileNotFoundError, json.JSONDecodeError):
                rows, events = [], []
            inputs = [
                event["payload"] for event in events if event["kind"] == "public_decision_input"
            ]
            runtime = state["runtime"]
            if (
                runtime["last_snapshot_id"] == snapshot_id
                and len(rows) == sequence
                and len(inputs) == sequence
                and rows[-1]["completion"]["snapshot_id"] == snapshot_id
                and rows[-1]["control"]["observation_ordinal"] == sequence
                and inputs[-1]["completion"]["snapshot_id"] == snapshot_id
                and inputs[-1]["control"]["observation_ordinal"] == sequence
            ):
                observations.append(copy.deepcopy(state))
                return
            assert not runtime["tainted"] and not runtime["errors"], state
            time.sleep(0.02)
        observations.append(copy.deepcopy(state))
        raise AssertionError(f"Original public-{sequence} completion did not arrive: {state}")

    try:
        commands.append(app.models.command("shadow"))
        commands.append(finish_operation(app.models))
        completed_public_input(1)
        # Advance an actual complete public observation and its finite catalog;
        # the Runtime, never this fixture, assigns the new qualified ordinal.
        world.public_sequence += 1
        completed_public_input(2)
        assert log.read_bytes() == host_before
        events = common_finish(app, ready, startup, reference, raw, host_before, log, world)
    finally:
        (folder / "process-diagnostics.json").write_bytes(
            json_bytes(
                {
                    "original_control_replies": replies,
                    "command_states": commands,
                    "completed_observation_states": observations,
                    "sent_snapshots": world.sent_snapshots,
                    "world_requests": world.requests,
                    "final_state": app.models.status(),
                }
            )
        )
    rows = [json.loads(line) for line in trace.read_bytes().splitlines()]
    assert [row["control"]["observation_ordinal"] for row in rows] == [1, 2]
    assert [row["reset"] for row in rows] == [True, False]
    assert rows[0]["memory_sha256"] != rows[1]["memory_sha256"]
    assert rows[1]["step_calls"] == 2 and world.submitted == 0
    assert len([event for event in events if event["kind"] == "public_decision_input"]) == 2
