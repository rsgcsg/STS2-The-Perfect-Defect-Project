"""Real Application HTTP + public Host pipe + common Runtime Auto; synthetic World only."""
# ruff: noqa: F811 -- pytest resolves the imported production-shaped fixtures.
from __future__ import annotations

import copy
import hashlib
import json
import threading

import pytest
from reference_suite_diagnostics import retain_report_diagnostic
from test_local_models import public_native_preflight as public_native_preflight
from test_native_episode_application import native_app as native_app
from test_native_episode_process import (
    installed_native_runtime as installed_native_runtime,
)
from test_native_episode_process import native_caps as process_native_caps  # noqa: F401
from test_native_episode_process import (
    native_endpoint as native_endpoint,
)
from test_native_episode_process import (
    native_runtime as native_runtime,
)
from test_native_episode_process import register_entry
from test_native_episode_process import world as world
from test_reference_suite import suite_manifest

from spireagent.encoding import canonical_json
from spireagent.json_boundary import json_bytes
from spireagent.workbench.developer import atomic_json
from spireagent.workbench.developer_server import configuration_id, create_server
from spireagent.workbench.reference_suite import (
    SuiteLimits,
    WorkbenchReferenceClient,
    run_suite,
)
from stpd.fullrun.native_structured_inputs import native_catalog_digest


@pytest.fixture
def native_caps(process_native_caps):
    value = copy.deepcopy(process_native_caps)
    value["game"]["version"] = "v0.111.0"
    return value


@pytest.fixture
def suite_app(native_runtime, native_app, world, monkeypatch):
    from spireagent.workbench.native_teacher_support import ADAPTER, installation_config
    from stpd.policy.native_teacher_agent import descriptor, write_artifact
    from stpd.policy.native_teacher_manifest import teacher_manifest

    app = native_runtime[0]
    folder = app.models.private_root / "fixture-reference-teacher"
    folder.mkdir(parents=True)
    artifact = write_artifact(folder / "program.json")
    manifest = teacher_manifest(descriptor(), artifact, world.caps, world.seams,
                                manifest_id="fixture-reference-teacher")
    config_path, manifest_path = folder / "config.json", folder / "manifest.json"
    config_path.write_bytes(json_bytes(
        installation_config(folder / "program.json", artifact["sha256"])))
    manifest_path.write_bytes(json_bytes(manifest))
    register_entry(app.models, "fixture-reference-teacher", ADAPTER, "native-logical-v1",
                   config_path, manifest_path)
    app.models.native_tasks.address = world.endpoint
    initial = copy.deepcopy((world.observation, world.capture, world.actions, world.bytes))
    actual_prepare, actual_route = app.prepare_native_episode, world.route
    offers = []

    def prepare(body):
        # Synthetic World reset; no replacement of the owning public pipe reset,
        # generation/template/seed checks, controller handoff or Actor admission.
        world.observation, world.capture, world.actions, world.bytes = copy.deepcopy(initial)
        offers.append(copy.deepcopy(body))
        settings = json.loads(native_app[2].read_bytes())
        settings["identity"]["episode_provenance"]["actual_seed"] = body["seed"]
        atomic_json(native_app[2], settings)
        return actual_prepare(body)

    def route(path, body):
        response = actual_route(path, body)
        if path.endswith("/actions") and body.get("input_profile") == "native-logical-v1":
            # One real Runtime Act advances this synthetic World to a typed
            # Summary. The actual program must Consume/ACK it before Close.
            world.actions = [{**world.actions[0], "action_id": "return",
                              "verb": "return_to_timeline"}]
            observation = world.observation
            observation.update(snapshot_id="native-summary", revision=2)
            observation["interaction"].update(
                kind="game_over", stage="summary",
                content_schema="sts2.player-environment/surface/game_over-1",
                content={"context": {"kind": "game_over", "result": "loss", "game_mode": "standard",
                                     "score": 10, "floor_reached": 1, "ascension": 0},
                         "surface": {"kind": "game_over", "stage": "summary", "can_return": True,
                                     "can_advance_summary": False,
                                     "return_destination": "timeline"}})
            observation["catalog"].update(snapshot_id="native-summary", total_count=1,
                                           digest=native_catalog_digest(world.actions))
            world.bytes = json.dumps(observation, separators=(",", ":")).encode()
            world.capture.update(capture_id="summary-capture", snapshot_id="native-summary",
                                 sha256=hashlib.sha256(world.bytes).hexdigest(),
                                 byte_count=len(world.bytes))
        return response

    monkeypatch.setattr(app, "prepare_native_episode", prepare)
    monkeypatch.setattr(world, "route", route)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    atomic_json(app.config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(app.config),
        "port": server.server_port, "control_token": app.control_token})
    try:
        yield app, offers, native_app[3], world
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_two_serial_http_episodes_use_original_request_auto_ack_seal_and_exact_finish(
    tmp_path, suite_app, monkeypatch, request,
):
    app, offers, host_log, world = suite_app
    manifest, sha = suite_manifest(tmp_path)
    client = WorkbenchReferenceClient(app.config)
    original_submit = client.submit
    request_wires, host_at_submit = [], []
    first_ref = None

    def submit(request, ref, timeout):
        nonlocal first_ref
        if first_ref is not None:
            # A is already sealed; B owns this live public Host. An old HTTP
            # finish must return A's retained result without touching B.
            before = host_log.read_bytes()
            b_client = app.local_environment.native_client
            assert b_client._process.poll() is None
            historical = client.finish(first_ref, timeout)
            assert historical["environment_ref"] == first_ref
            assert host_log.read_bytes() == before
            assert client.native_status(timeout)["environment_ref"] == ref
            assert app.local_environment.native_client is b_client
            assert b_client._process.poll() is None
        first_ref = ref
        request_wires.append(canonical_json(request).encode())
        host_at_submit.append(host_log.read_bytes())
        return original_submit(request, ref, timeout)

    monkeypatch.setattr(client, "submit", submit)
    report = run_suite(manifest, sha, client=client, run_dir=tmp_path / "reference-attempt",
                       selection="fixture-reference-teacher", run_profile="short", max_episodes=2,
                       limits=SuiteLimits(15, 20, 15, 90, 0.05, 10))
    diagnostic = retain_report_diagnostic(
        report, tmp_path / "reference-attempt", request.node.user_properties)
    assert report["status"] == "completed", diagnostic
    assert len(report["episodes"]) == 20 and len(offers) == len(request_wires) == 2
    assert report["actor_started"] == 2 and world.submitted == 2
    assert len({row["generation_id"] for row in report["episodes"][:2]}) == 2
    assert len({row["run_id"] for row in report["episodes"][:2]}) == 2
    for index, row in enumerate(report["episodes"][:2]):
        directory = tmp_path / "reference-attempt"
        assert (directory / row["request"]["path"]).read_bytes() == request_wires[index]
        original = (directory / row["original_request"]["path"]).read_bytes()
        request = json.loads(original)
        assert request["admission_context"]["native_episode"] == row["admission_context"]
        events = [json.loads(line) for line in
                  (app.models.directory / "agent-runs" / row["run_id"] / "events.jsonl")
                  .read_bytes().splitlines()]
        assert any(event["kind"] == "agent_sample_consume_ack_offered" for event in events)
        assert any(event["kind"] == "native_submission_requested" for event in events)
        assert any(event["kind"] == "stopped" for event in events)
        first = next(event["payload"] for event in events if event["kind"] == "agent_consumed")
        assert first["report"]["state_version"] == 1
        assert row["agent_task_outcome"]["terminal_observation"]["result"] == "loss"
        assert row["cleanup_accepted"] is True
    # Actor requests use the HTTP World, never the bootstrap driver's step.
    host_events = [json.loads(line)["event"] for line in host_log.read_bytes().splitlines()]
    assert host_events.count("reset_started") == host_events.count("close") == 2
    assert "step" not in host_events and not world.held
    assert all(row["status"] == "not_started" for row in report["episodes"][2:])


def test_late_ready_and_accepted_http_reply_loss_never_reoffer_actor(
    tmp_path, suite_app, monkeypatch, request,
):
    app, offers, host_log, _world = suite_app
    manifest, sha = suite_manifest(tmp_path)
    client = WorkbenchReferenceClient(app.config)
    actual_prepare, actual_status, actual_submit = (
        client.prepare, client.native_status, client.submit,
    )
    status_reads, submits = 0, 0

    def prepare(body, timeout):
        resource = actual_prepare(body, timeout)
        # Only delay the client's observation; real prepare/pipe are unchanged.
        return {**resource, "phase": "preparing"}

    def native_status(timeout):
        nonlocal status_reads
        status_reads += 1
        resource = actual_status(timeout)
        return {**resource, "phase": "preparing"} if status_reads < 3 else resource

    def submit(request, ref, timeout):
        nonlocal submits
        submits += 1
        actual_submit(request, ref, timeout)
        raise TimeoutError("injected loss after HTTP owner accepted original request")

    monkeypatch.setattr(client, "prepare", prepare)
    monkeypatch.setattr(client, "native_status", native_status)
    monkeypatch.setattr(client, "submit", submit)
    report = run_suite(manifest, sha, client=client, run_dir=tmp_path / "lost-attempt",
                       selection="fixture-reference-teacher", run_profile="short", max_episodes=2,
                       limits=SuiteLimits(15, 20, 15, 90, 0.05, 10))
    diagnostic = retain_report_diagnostic(
        report, tmp_path / "lost-attempt", request.node.user_properties)
    assert report["status"] == "stopped", diagnostic
    assert len(offers) == submits == 1
    assert status_reads >= 3
    assert report["stop_reason"] == "TimeoutError"
    assert report["episodes"][0]["finish"]
    assert all(row["status"] == "not_started" for row in report["episodes"][1:])
    events = [json.loads(line)["event"] for line in host_log.read_bytes().splitlines()]
    assert events.count("reset_started") == events.count("close") == 1
    assert "step" not in events
