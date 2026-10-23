"""Application/API/group tests with explicit synthetic single-episode transport.

The production suite request serializer/report/seal checks and Model admission lock
are real. Runtime/Host transport and peer transfer are covered by their owner tests.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from test_reference_evaluation import GroupOwner

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import ProjectConfig, atomic_json, combination
from spireagent.workbench.developer_server import Application, configuration_id, create_server
from spireagent.workbench.reference_evaluation import (
    ApplicationReferenceClient as BoundedGroupClient,
)
from spireagent.workbench.reference_evaluation import ReferenceEvaluationService
from tools.evaluate_reference_group import command


@pytest.fixture
def group_app(tmp_path, monkeypatch):
    from spireagent.workbench import reference_evaluation

    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    group = app.reference_evaluation
    monkeypatch.setattr(group, "_validate_selection", lambda *_: None)
    clients = []

    def client(_app):
        result = GroupOwner(Path(group.operation["report_path"]).parent)
        clients.append(result)
        return result

    monkeypatch.setattr(reference_evaluation, "ApplicationReferenceClient", client)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    atomic_json(config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
        "port": server.server_port, "control_token": app.control_token})
    body = {"selection_id": "existing-agent", "seed_count": 2, "game_version": "v0.111.0",
            "run_profile": "short", "display_mode": "headless", "speed": "instant",
            "limits": {"preparation_seconds": 10, "episode_seconds": 10,
                       "cleanup_seconds": 10, "suite_seconds": 30},
            "evidence_peer": {"kind": "local", "directory": str(tmp_path / "peer")}}
    try:
        yield app, clients, body, server.server_port
    finally:
        group.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def await_status(app, expected):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = command(app.config, "status")
        if status["status"] == expected:
            return status
        time.sleep(0.01)
    pytest.fail(f"group did not reach {expected}: {status}")


@pytest.mark.parametrize("action", ["resume", "stop"])
def test_group_http_cli_pause_and_exact_operation_control(group_app, monkeypatch, action):
    app, clients, body, port = group_app
    entered, release = threading.Event(), threading.Event()
    original = GroupOwner.preserve

    def preserve(self, run_id, peer, timeout):
        if self.number == 1:
            entered.set()
            assert release.wait(timeout=5)
        return original(self, run_id, peer, timeout)

    monkeypatch.setattr(GroupOwner, "preserve", preserve)
    started = command(app.config, "start", body)
    control = {"operation_id": started["operation_id"]}
    try:
        assert entered.wait(timeout=5)
        assert command(app.config, "pause", control)["status"] == "pause_requested"
        with pytest.raises(HTTPError) as wrong:
            command(app.config, "stop", {"operation_id": "0" * 32})
        assert wrong.value.code == 409
        for mode in ("auto", "human", "stop"):
            with pytest.raises(BoundaryError, match="reference_group_owns_execution"):
                app.models.command(mode)
        # Native creation and close use the same reservation fence as Model.
        with pytest.raises(BoundaryError, match="reference_group_owns_execution"):
            app.prepare_native_episode({})
        with pytest.raises(BoundaryError, match="reference_group_owns_execution"):
            app.finish_native_episode("a" * 32)
        # The normal Model HTTP endpoint is fenced too, without hiding reads.
        request = Request(f"http://127.0.0.1:{port}/api/local-models/command",
                          data=b'{"action":"stop"}', headers={"Authorization":
                          "Bearer " + app.control_token, "Content-Type": "application/json"})
        with pytest.raises(HTTPError) as blocked:
            urlopen(request, timeout=2)
        assert blocked.value.code == 409
        assert app.models.status()["status"] == "idle"
        release.set()
        paused = await_status(app, "paused")
        assert paused["report"]["episodes"][0]["stored_evidence_id"] == "e" * 64
        assert paused["report"]["episodes"][1]["status"] == "not_started"
        for require_human in (True, False):
            with (pytest.raises(BoundaryError, match="reference_group_owns_execution"),
                  app.models.reserve_recording_source_mutation(require_human=require_human)):
                pytest.fail("foreign recording source reservation was admitted")
        assert app.models._recording_source_reservation is None
        for mutation in (lambda: app.local_recording_import.start("a" * 64),
                         lambda: app.local_recording_import.start_global_evidence(
                             {}, body["evidence_peer"]),
                         lambda: app.local_recording_import.start_cache_trim(())):
            with pytest.raises(BoundaryError, match="reference_group_owns_execution"):
                mutation()
        command(app.config, action, control)
        app.reference_evaluation.thread.join(timeout=5)
        final = command(app.config, "status")
        assert final["status"] == ("completed" if action == "resume" else "stopped"), final
        count = 2 if action == "resume" else 1
        assert final["report"]["planned"] == 2
        assert final["report"]["actor_started"] == count
        assert clients[0].calls.count("submit") == count
        assert final["report"]["summary"]["preserved_evidence"] == count
        if action == "stop":
            assert final["report"]["episodes"][1]["reason"] == "stop_requested"
    finally:
        release.set()


def test_lost_preservation_ack_fences_new_group_and_keeps_original_report(group_app, monkeypatch):
    app, clients, body, _port = group_app

    def lost(*_args):
        raise TimeoutError("synthetic peer acknowledgement lost")

    monkeypatch.setattr(GroupOwner, "preserve", lost)
    command(app.config, "start", body)
    app.reference_evaluation.thread.join(timeout=5)
    final = command(app.config, "status")
    assert final["status"] == "recovery_required", final
    assert final["report"]["summary"]["preservation_failed_or_unknown"] == 1
    assert clients[0].calls.count("submit") == 1
    assert final["report"]["episodes"][1]["status"] == "not_started"
    path = Path(final["report_path"])
    original = path.read_bytes()
    with pytest.raises(HTTPError) as blocked:
        command(app.config, "start", body)
    assert blocked.value.code == 409
    assert ReferenceEvaluationService(app).status()["status"] == "recovery_required"
    assert path.read_bytes() == original


def test_process_loss_recovery_never_replays_or_rewrites_original(group_app):
    app, _clients, _body, _port = group_app
    group = app.reference_evaluation
    group.operation = {"schema": "spireagent/reference-evaluation-operation-v1",
                       "operation_id": "a" * 32, "status": "running"}
    group._save()
    original = group.path.read_bytes()
    reopened = ReferenceEvaluationService(app)
    assert reopened.status()["status"] == "recovery_required"
    assert group.path.read_bytes() == original
    assert reopened.thread is None
    # Keep the fixture's owning application recoverable for close.
    group.operation = {"schema": "spireagent/reference-evaluation-operation-v1", "status": "idle"}


def recovery_facts(app, clients, monkeypatch):
    """Original synthetic owner outputs, retaining the real suite serializers."""
    import copy
    import json

    from spireagent.workbench import reference_evaluation

    group = app.reference_evaluation
    current = group.status()
    row = current["report"]["episodes"][0]
    directory = Path(current["report_path"]).parent
    native = json.loads((directory / row["finish"]["path"]).read_bytes())
    state = {**copy.deepcopy(clients[0].status(1)), "status": "stopped", "loaded": False}
    readback = {"artifact_id": "e" * 64, "content_id": row["evidence_content_id"],
                "run_id": row["run_id"], "full_readback": True, "typed_verification": "pass"}
    preservation = {"status": "completed", "operation_id": "f" * 32, "run_id": row["run_id"],
                    "peer": group.operation["evidence_peer"], "artifact_id": "e" * 64,
                    "local_readback": readback, "peer_readback": copy.deepcopy(readback)}
    original_factory = reference_evaluation.ApplicationReferenceClient
    monkeypatch.setattr(reference_evaluation, "ApplicationReferenceClient", lambda _app: clients[0])
    monkeypatch.setattr(app, "native_episode_status", lambda: native)
    monkeypatch.setattr(app.models, "status", lambda: state)
    monkeypatch.setattr(app.local_recording_import, "status", lambda: preservation)
    control = {"operation_id": current["operation_id"], "report_sha256": current["report_sha256"]}
    return control, native, preservation, original_factory


@pytest.mark.parametrize("fault", [None, "active", "different_ref", "hash", "ack_only",
                                 "activation_write"])
def test_explicit_reconciliation_retires_only_proved_original_and_keeps_unknown(
    group_app, monkeypatch, fault,
):
    from spireagent.workbench import reference_evaluation

    app, clients, body, _port = group_app

    def lost(*_args):
        raise TimeoutError("original preservation reply lost")

    monkeypatch.setattr(GroupOwner, "preserve", lost)
    command(app.config, "start", body)
    app.reference_evaluation.thread.join(timeout=5)
    current = command(app.config, "status")
    assert current["status"] == "recovery_required"
    original = Path(current["report_path"]).read_bytes()
    control, native, preservation, original_factory = recovery_facts(app, clients, monkeypatch)
    if fault == "active":
        app.models.state["loaded"] = True
    elif fault == "different_ref":
        native["environment_ref"] = "0" * 32
    elif fault == "hash":
        control["report_sha256"] = "0" * 64
    elif fault == "ack_only":
        preservation["peer_readback"] = {"acknowledged": True}
    elif fault == "activation_write":
        def failed_save():
            raise OSError("synthetic operation activation write failure")

        monkeypatch.setattr(app.reference_evaluation, "_save", failed_save)
    try:
        if fault:
            with pytest.raises(HTTPError) as refused:
                command(app.config, "reconcile", control)
            assert refused.value.code == (400 if fault == "activation_write" else 409)
            assert app.reference_evaluation.status()["status"] == "recovery_required"
        else:
            reconciled = command(app.config, "reconcile", control)
            assert reconciled["status"] == "stopped"
            observation = reconciled["recovery_observation"]
            assert Path(observation["path"]).is_file()
            assert reconciled["report"]["stop_reason"] == "preservation_unknown_or_failed"
            assert reconciled["report"]["summary"]["preservation_failed_or_unknown"] == 1
            # Only a new explicit request can start a different group.
            monkeypatch.setattr(reference_evaluation, "ApplicationReferenceClient",
                                original_factory)
            monkeypatch.setattr(app, "native_episode_status", lambda: {"phase": "closed"})
            monkeypatch.setattr(app.models, "status", lambda: {"status": "idle"})
            new_group = command(app.config, "start", body)
            assert new_group["operation_id"] != current["operation_id"]
            app.reference_evaluation.thread.join(timeout=5)
        assert Path(current["report_path"]).read_bytes() == original
        assert clients[0].calls.count("submit") == 1
    finally:
        app.models.state["loaded"] = False


def test_group_client_keeps_original_http_observation_deadline(group_app, monkeypatch):
    """A slow synchronous owner cannot turn a 100 ms allowance into its own 3 s wait."""
    app, _clients, _body, _port = group_app
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def slow_owner():
        calls.append("status")
        entered.set()
        try:
            assert release.wait(timeout=3)
            return {"status": "idle"}
        finally:
            done.set()

    monkeypatch.setattr(app.models, "status", slow_owner)
    client = BoundedGroupClient(app)
    started = time.monotonic()
    try:
        with pytest.raises(BoundaryError,
                           match="workbench_request_failed_check_status_before_retry"):
            client.status(0.1)
        assert time.monotonic() - started < 1
        assert entered.is_set() and not done.is_set()
        assert calls == ["status"]  # Observation failure never repeats the request.
    finally:
        release.set()
        assert done.wait(timeout=1)


def test_group_scope_nonce_requires_original_auth_and_does_not_escape(group_app, monkeypatch):
    import json

    app, _clients, body, port = group_app
    entered, release = threading.Event(), threading.Event()
    original = GroupOwner.preserve

    def preserve(self, run_id, peer, timeout):
        entered.set()
        assert release.wait(timeout=5)
        return original(self, run_id, peer, timeout)

    monkeypatch.setattr(GroupOwner, "preserve", preserve)
    started = command(app.config, "start", body)
    try:
        assert entered.wait(timeout=5)
        nonce = app.reference_evaluation._http_nonce
        before = command(app.config, "status")
        assert nonce not in json.dumps(before)
        for provided, authorized, expected in ((None, True, 409), ("0" * 64, True, 403),
                                                (nonce, False, 403)):
            headers = {"Content-Type": "application/json"}
            if provided is not None:
                headers["X-Reference-Group-Scope"] = provided
            if authorized:
                headers["Authorization"] = "Bearer " + app.control_token
            request = Request(f"http://127.0.0.1:{port}/api/local-models/command",
                              data=b'{"action":"stop"}', headers=headers)
            assert nonce not in request.full_url
            with pytest.raises(HTTPError) as refused:
                urlopen(request, timeout=2)
            assert refused.value.code == expected
            error = refused.value.read().decode()
            assert nonce not in error and "0" * 64 not in error
        assert nonce not in Path(before["report_path"]).read_text()
    finally:
        command(app.config, "stop", {"operation_id": started["operation_id"]})
        release.set()
        app.reference_evaluation.thread.join(timeout=5)


def test_group_client_rejects_replaced_application_before_owner_request(group_app, monkeypatch):
    from spireagent.workbench import reference_suite

    app, _clients, _body, _port = group_app
    monkeypatch.setattr(reference_suite, "running", lambda _config: {
        "instance_id": "0" * 32, "configuration_id": "b" * 64})
    with pytest.raises(BoundaryError, match="application_instance_mismatch"):
        BoundedGroupClient(app)
