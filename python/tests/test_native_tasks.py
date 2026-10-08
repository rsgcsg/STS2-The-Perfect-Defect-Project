"""Loopback task bridge must close the exact recording before model control."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.native_tasks import NativeTasks


@pytest.fixture
def bridge():
    observed = {
        "schema": "sts2.platform/task-status-1",
        "runtime_instance_id": "game-1",
        "recording_session_id": "recording-1",
        "recording_lifecycle": "recording",
        "closeout_status": "recording",
        "ready_for_model": False,
    }
    calls, behavior = [], {"close": "success"}
    capabilities = {
        "protocol_version": "1.0.0",
        **{name + "_schema": f"sts2.player-environment/{name}-1"
           for name in ("snapshot", "action", "receipt", "control")},
        "host": {"runtime_instance_id": "game-1"},
    }
    behavior["capabilities"] = capabilities
    behavior["recording"] = {
        "schema": "sts2.platform/recording-status-1",
        "runtime_instance_id": "game-1",
        "recording_session_id": None,
        "recording_lifecycle": "ready",
        "capture_profile_id": None,
        "closeout_status": "idle",
        "source": None,
        "health": {"append_health": "healthy", "disk_health": "healthy", "error": None},
        "non_claims": [
            "not_machine_proof_of_human_origin",
            "not_native_coverage_qualified",
            "not_causal_transition_proof",
            "not_research_admission",
            "not_g2_v1_approved",
        ],
    }

    class ConnectorHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            calls.append((self.path, None))
            assert self.path == "/api/player-environment/capabilities"
            ordinal = sum(route == self.path for route, _ in calls)
            fault_at, fault = behavior.get("connector_fault_at", (None, None))
            if fault_at == ordinal and fault == "unavailable":
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            value = capabilities
            if fault_at == ordinal and fault == "drift":
                value = {**capabilities, "host": {"runtime_instance_id": "replacement-game"}}
            raw = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            calls.append((self.path, None))
            if self.path == "/v2/tasks/status":
                self.respond(task_v2_status(behavior))
                return
            if self.path == "/v1/tasks/recording/status":
                self.respond(behavior["recording"])
                return
            assert self.path == "/v1/tasks/status"
            self.respond()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            if self.path == "/v2/tasks/prepare-model":
                source = behavior["recording"]
                context = body["model_context"]
                status = task_v2_status(behavior)
                compatible = (
                    status["can_preserve_for_native_agent"]
                    and context["runtime_status_schema"]
                    == "sts2.policy-runtime/agent-session-status-1"
                )
                disposition = (
                    "retained_agent_protocol"
                    if compatible
                    else (
                        "no_active_recording"
                        if status["recording_lifecycle"] in {"ready", "closed"}
                        else "closed_for_model"
                    )
                )
                ready = True
                if (
                    source["capture_profile_id"] == "native-logical-source-v3"
                    and source["source"] is not None
                ):
                    if not source["source"]["accounting_complete"]:
                        disposition, ready = "blocked", False
                    elif (
                        source["source"]["declaration"]["source_kind"] == "agent_protocol"
                        and source["recording_lifecycle"] == "paused"
                    ):
                        disposition, ready = "paused", False
                if ready and disposition == "closed_for_model":
                    observed.update(
                        recording_lifecycle="closed", ready_for_model=True, closeout_status="closed"
                    )
                    source.update(recording_lifecycle="closed", closeout_status="closed")
                    status = task_v2_status(behavior)
                value = {
                    "schema": "sts2.platform/task-model-result-2",
                    "command_id": body["command_id"],
                    "runtime_instance_id": "game-1",
                    "model_context": context,
                    "ready_for_model": ready,
                    "recording_disposition": disposition,
                    "status": status,
                }
                if behavior.get("replace_connector"):
                    capabilities["host"]["runtime_instance_id"] = "replacement-game"
                if behavior.get("v2_mutate_result"):
                    behavior["v2_mutate_result"](value)
                if behavior.get("v2_lost"):
                    self.close_connection = True
                    return
                self.respond(value)
                return
            if self.path == "/v1/tasks/recording/command":
                outcome = behavior.get("recording_outcome", "success")
                if outcome == "lost":
                    self.close_connection = True
                    return
                if outcome == "not_dispatched":
                    self.respond({"error": "native_task_not_dispatched"}, 503)
                    return
                if outcome == "rejected":
                    self.respond(
                        {
                            "error": "task_handoff_rejected",
                            "detail": "recording_game_instance_changed",
                        },
                        409,
                    )
                    return
                if outcome == "unknown_error":
                    self.respond({"error": "unrecognized"}, 503)
                    return
                if outcome == "lost_pending":
                    import socket

                    behavior["recording_entered"].set()
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    assert behavior["recording_release"].wait(timeout=5)
                    return
                if behavior.get("recording_apply_lifecycle"):
                    kind = body["command"]["kind"]
                    if kind == "start_new_session":
                        source_status(behavior, "recording")
                        behavior["recording"]["source"]["declaration"] = body["command"][
                            "source_declaration"
                        ]
                    elif kind in {"pause", "resume", "close"}:
                        lifecycle = {"pause": "paused", "resume": "recording", "close": "closed"}[
                            kind
                        ]
                        behavior["recording"].update(
                            recording_lifecycle=lifecycle, closeout_status=lifecycle
                        )
                result = {
                    "schema": "sts2.platform/recording-result-1",
                    "command_id": body["command"]["command_id"],
                    "accepted": True,
                    "pending": behavior.get("recording_result_pending", False),
                    "code": "recording",
                    "status": behavior["recording"],
                }
                if outcome == "malformed":
                    result["extra"] = "not allowed"
                if outcome == "wrong_id":
                    result["command_id"] = "00000000-0000-0000-0000-000000000000"
                self.respond(result)
                return
            assert body["runtime_instance_id"] == "game-1"
            assert body["recording_session_id"] == "recording-1"
            if behavior["close"] == "lost":
                self.close_connection = True
                return
            if behavior["close"] == "success":
                observed.update(recording_lifecycle="closed", ready_for_model=True)
            else:
                observed.update(recording_lifecycle="closing")
            if behavior.get("replace_connector"):
                capabilities["host"]["runtime_instance_id"] = "replacement-game"
            self.respond()

        def respond(self, value=None, code=200):
            raw = json.dumps(observed if value is None else value).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connector = ThreadingHTTPServer(("127.0.0.1", 0), ConnectorHandler)
    connector_thread = threading.Thread(target=connector.serve_forever, daemon=True)
    connector_thread.start()
    behavior["connector_endpoint"] = f"http://127.0.0.1:{connector.server_port}"
    client = NativeTasks()
    client.address = f"http://127.0.0.1:{server.server_port}"
    try:
        yield client, observed, calls, behavior
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        connector.shutdown()
        connector.server_close()
        connector_thread.join(timeout=2)


def test_fresh_instance_and_recording_binding_one_close_only(bridge):
    client, _, calls, behavior = bridge
    assert client.prepare_model(
        {"environment": {"runtime_instance_id": "game-1"}}, behavior["connector_endpoint"]
    )[
        "ready_for_model"
    ]
    assert len(calls) == 4
    assert len(calls[2][1]["command_id"]) == 36
    client.prepare_model(
        {"environment": {"runtime_instance_id": "game-1"}}, behavior["connector_endpoint"]
    )
    assert len(calls) == 7 and calls[-1][1] is None
    assert sum(body is not None for _, body in calls) == 1


@pytest.mark.parametrize(
    "outcome,error",
    [("pending", "recording_close_pending_or_failed"), ("lost", "native_task_command_unknown")],
)
def test_close_pending_or_lost_response_is_not_model_permission(bridge, outcome, error):
    client, _, calls, behavior = bridge
    behavior["close"] = outcome
    with pytest.raises(BoundaryError, match=error):
        client.prepare_model(
            {"environment": {"runtime_instance_id": "game-1"}}, behavior["connector_endpoint"]
        )
    assert sum(body is not None for _, body in calls) == 1  # no automatic POST retry


def test_wrong_game_missing_identity_and_inconsistent_ready_never_send_close(bridge):
    client, observed, calls, behavior = bridge
    with pytest.raises(BoundaryError, match="runtime_game_identity_required"):
        client.prepare_model({"environment": {}}, behavior["connector_endpoint"])
    assert calls == []
    with pytest.raises(BoundaryError, match="native_task_game_identity_mismatch"):
        client.prepare_model(
            {"environment": {"runtime_instance_id": "different-game"}},
            behavior["connector_endpoint"],
        )
    observed["ready_for_model"] = True
    with pytest.raises(BoundaryError, match="native_task_unavailable"):
        client.prepare_model(
            {"environment": {"runtime_instance_id": "game-1"}}, behavior["connector_endpoint"]
        )
    assert all(body is None for _, body in calls)


def test_first_human_environment_null_uses_bound_connector_then_exact_bridge(bridge):
    client, _, calls, behavior = bridge
    result = client.prepare_model({"environment": None}, behavior["connector_endpoint"])
    assert result["runtime_instance_id"] == "game-1" and result["ready_for_model"]
    assert [route for route, _ in calls] == [
        "/api/player-environment/capabilities", "/v1/tasks/status",
        "/v1/tasks/prepare-model", "/api/player-environment/capabilities",
    ]


@pytest.mark.parametrize("endpoint", [None, "", "http://example.org:15526",
                                     "http://127.0.0.1:15526/path"])
def test_unbound_or_invalid_connector_never_guesses_default(bridge, endpoint):
    client, _, calls, _ = bridge
    with pytest.raises(BoundaryError, match="runtime_connector_binding_required"):
        client.prepare_model({"environment": None}, endpoint)
    assert calls == []


@pytest.mark.parametrize("drift", ["instance", "protocol", "missing-host"])
def test_connector_identity_must_agree_before_close(bridge, drift):
    client, _, calls, behavior = bridge
    capabilities = behavior["capabilities"]
    if drift == "instance":
        capabilities["host"]["runtime_instance_id"] = "different-game"
    elif drift == "protocol":
        capabilities["protocol_version"] = "2.0.0"
    else:
        del capabilities["host"]
    error = "game_identity_mismatch" if drift == "instance" else "connector_identity_unavailable"
    with pytest.raises(BoundaryError, match=error):
        client.prepare_model({"environment": None}, behavior["connector_endpoint"])
    assert all(body is None for _, body in calls)


def test_connector_replacement_while_closing_cannot_authorize_model_control(bridge):
    client, _, calls, behavior = bridge
    behavior["replace_connector"] = True
    with pytest.raises(BoundaryError, match="native_task_command_unknown"):
        client.prepare_model({"environment": None}, behavior["connector_endpoint"])
    assert sum(body is not None for _, body in calls) == 1


@pytest.mark.parametrize("fault,preflight_code", [("unavailable", "connector_identity_unavailable"),
                                               ("drift", "native_task_game_identity_mismatch")])
@pytest.mark.parametrize("phase", ["before_close", "after_close", "ready_no_close"])
def test_model_identity_confirmation_keeps_submission_stage(bridge, fault, preflight_code, phase):
    client, observed, calls, behavior = bridge
    if phase == "ready_no_close":
        observed.update(recording_lifecycle="closed", ready_for_model=True)
    behavior["connector_fault_at"] = (1 if phase == "before_close" else 2, fault)
    expected = "native_task_command_unknown" if phase == "after_close" else preflight_code
    with pytest.raises(BoundaryError) as result:
        client.prepare_model(
            {"environment": {"runtime_instance_id": "game-1"}}, behavior["connector_endpoint"]
        )
    assert result.value.code == expected
    assert sum(body is not None for _, body in calls) == (1 if phase == "after_close" else 0)
    assert not any(route in {"/mode", "/tick"} for route, _ in calls)


@pytest.mark.parametrize("fault", ["unavailable", "drift"])
def test_workbench_post_close_confirmation_failure_requires_recovery_without_model_dispatch(
    bridge, tmp_path, fault
):
    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.local_models import LocalModelService

    native, _, native_calls, behavior = bridge
    # Workbench first validates its persisted Connector, then NativeTasks reads
    # before Close, and the third read confirms after the actual Close POST.
    behavior["connector_fault_at"] = (3, fault)
    service = LocalModelService(ProjectConfig(tmp_path, "", "", None, combination()))
    service.native_tasks = native
    runtime_calls = []

    class Runtime:
        def request(self, route, body=None, *, binding=None):
            runtime_calls.append((route, body))
            assert body is None
            if route == "/status":
                return {
                    "status": {
                        "schema": "sts2.policy-runtime/status-1",
                        "environment": None,
                        "run_id": "fixture-run",
                        "mode": "human",
                    }
                }
            assert route == "/environment"
            return {"schema": "sts2.policy-runtime/environment-1", "run_id": "fixture-run",
                    "runtime_instance_id": "game-1", "recovery_epoch": 19}

    service.client = Runtime()
    service.state.update(
        status="loaded", loaded=True, connector_endpoint=behavior["connector_endpoint"]
    )
    service.command("auto")
    service.thread.join(timeout=3)
    assert not service.thread.is_alive()
    assert service.state["status"] == "command_unknown"
    assert service.state["operation"]["status"] == "unknown"
    assert service.state["error_code"] == "native_task_command_unknown"
    assert runtime_calls == [("/status", None), ("/environment", None)]
    assert sum(body is not None for _, body in native_calls) == 1
    with pytest.raises(BoundaryError, match="previous_operation_requires_recovery"):
        service.command("auto")
    assert sum(body is not None for _, body in native_calls) == 1


@pytest.mark.parametrize("action", ["auto", "one_step", "shadow"])
def test_workbench_first_model_command_uses_persisted_endpoint_not_current_config(
    bridge, tmp_path, action,
):
    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.local_models import LocalModelService

    native, _, native_calls, behavior = bridge
    config = ProjectConfig(tmp_path, "", "", None, combination())
    service = LocalModelService(config)
    service.native_tasks = native
    calls = []

    class Runtime:
        def request(self, route, body=None, *, binding=None):
            calls.append((route, body))
            if route == "/environment":
                assert not native_calls  # capture the owner epoch before any native preparation
                return {"schema": "sts2.policy-runtime/environment-1", "run_id": "fixture-run",
                        "runtime_instance_id": "game-1", "recovery_epoch": 19}
            if body is not None:
                assert native_calls[-1] == ("/api/player-environment/capabilities", None)
                assert binding.runtime_instance_id == "game-1" and binding.recovery_epoch == 19
            return {
                "status": {
                    "schema": "sts2.policy-runtime/status-1",
                    "environment": None,
                    "run_id": "fixture-run",
                    "mode": (body or {}).get("mode", "human"),
                }
            }

    service.client = Runtime()
    service.state.update(status="loaded", loaded=True,
                         connector_endpoint=behavior["connector_endpoint"])
    service.command(action)
    service.thread.join(timeout=3)
    assert not service.thread.is_alive()
    assert service.state["operation"]["status"] == "completed"
    assert [route for route, body in calls if body is not None] == (
        ["/mode", "/tick"] if action == "one_step" else ["/mode"]
    )
    assert sum(body is not None for _, body in native_calls) == 1


def source_status(behavior, state="paused"):
    behavior["recording"].update(
        recording_session_id="source-session", recording_lifecycle=state,
        capture_profile_id="native-logical-source-v3",
        source={"epoch_id": "epoch-1", "segment_id": "segment-1",
                "declaration": {"source_kind": "agent_native_ui", "actor_id": "operator-agent",
                                "declaration_id": "declaration-1", "machine_verifiable": False},
                "observations": 7, "inputs": 3, "pending_inputs": 1, "epochs": 1,
                "gaps": 0, "accounting_complete": True, "error": None},
    )


def task_v2_status(behavior):
    recording = behavior["recording"]
    source = recording["source"]
    return {
        "schema": "sts2.platform/task-status-2",
        "runtime_instance_id": recording["runtime_instance_id"],
        "recording_session_id": recording["recording_session_id"],
        "recording_lifecycle": recording["recording_lifecycle"],
        "closeout_status": recording["closeout_status"],
        "capture_profile_id": recording["capture_profile_id"],
        "source_segment_id": source["segment_id"] if source else None,
        "source_declaration": source["declaration"] if source else None,
        "can_preserve_for_native_agent": bool(
            source
            and source["declaration"]["source_kind"] == "agent_protocol"
            and recording["capture_profile_id"] == "native-logical-source-v3"
            and recording["recording_lifecycle"] == "recording"
            and source["accounting_complete"]
        ),
        "non_claims": recording["non_claims"],
    }


def test_source_start_and_paused_change_keep_explicit_declaration_session_segment(bridge):
    client, _, calls, behavior = bridge
    observed = client.recording_status(behavior["connector_endpoint"])
    declaration = {"source_kind": "agent_protocol", "actor_id": "canary-operator",
                   "declaration_id": "explicit-declaration", "machine_verifiable": False}
    result = client.recording_command(behavior["connector_endpoint"], observed, "start_new_session",
                                      source_declaration=declaration)
    assert result["accepted"]
    posted = next(body for route, body in calls if route == "/v1/tasks/recording/command")
    assert posted["recording_session_id"] is None
    assert posted["command"] == {
        "schema": "sts2.ai-platform/recording-command-3",
        "command_id": posted["command"]["command_id"],
        "kind": "start_new_session",
        "capture_profile_id": "native-logical-source-v3",
        "source_declaration": declaration,
        "expected_source_segment_id": None,
    }
    source_status(behavior)
    observed = client.recording_status(behavior["connector_endpoint"])
    declaration = {
        **declaration,
        "source_kind": "declared_human",
        "declaration_id": "human-declaration",
    }
    client.recording_command(
        behavior["connector_endpoint"], observed, "change_source", source_declaration=declaration
    )
    posted = [body for route, body in calls if route == "/v1/tasks/recording/command"][-1]
    assert posted["recording_session_id"] == "source-session"
    assert posted["command"]["expected_source_segment_id"] == "segment-1"
    assert posted["command"]["capture_profile_id"] is None
    assert posted["command"]["source_declaration"]["machine_verifiable"] is False


@pytest.mark.parametrize("kind", ["pause", "resume", "close"])
def test_source_lifecycle_commands_have_no_declaration_or_profile_data(bridge, kind):
    client, _, calls, behavior = bridge
    source_status(behavior)
    observed = client.recording_status(behavior["connector_endpoint"])
    client.recording_command(behavior["connector_endpoint"], observed, kind)
    posted = next(body for route, body in calls if route == "/v1/tasks/recording/command")[
        "command"
    ]
    assert posted["schema"] == "sts2.ai-platform/recording-command-3"
    assert all(
        posted[key] is None
        for key in ("capture_profile_id", "source_declaration", "expected_source_segment_id")
    )


@pytest.mark.parametrize(
    "outcome,code",
    [
        ("lost", "native_recording_command_unknown"),
        ("malformed", "native_recording_command_unknown"),
        ("wrong_id", "native_recording_command_unknown"),
        ("not_dispatched", "native_recording_not_dispatched"),
        ("rejected", "native_recording_rejected"),
        ("unknown_error", "native_recording_command_unknown"),
    ],
)
def test_source_response_loss_or_unrecognized_error_never_reposts(bridge, outcome, code):
    client, _, calls, behavior = bridge
    source_status(behavior)
    observed = client.recording_status(behavior["connector_endpoint"])
    behavior["recording_outcome"] = outcome
    with pytest.raises(BoundaryError, match=code):
        client.recording_command(behavior["connector_endpoint"], observed, "close")
    assert sum(route == "/v1/tasks/recording/command" for route, _ in calls) == 1
    # A separate read observes current owner state; it does not repeat the mutation.
    client.recording_status(behavior["connector_endpoint"])
    assert sum(route == "/v1/tasks/recording/command" for route, _ in calls) == 1


def test_source_stale_runtime_bad_declaration_or_live_switch_stays_before_post(bridge):
    client, _, calls, behavior = bridge
    source_status(behavior, "recording")
    observed = client.recording_status(behavior["connector_endpoint"])
    declaration = {"source_kind": "declared_human", "actor_id": "operator",
                   "declaration_id": "declaration", "machine_verifiable": False}
    for bad in ({**declaration, "machine_verifiable": True}, {**declaration, "source_kind": {}},
                {**declaration, "actor_id": "/private/path"}, {**declaration, "extra": True}):
        with pytest.raises(BoundaryError, match="invalid_native_recording_command"):
            client.recording_command(
                behavior["connector_endpoint"],
                observed,
                "start_new_session",
                source_declaration=bad,
            )
    with pytest.raises(BoundaryError, match="invalid_native_recording_command"):
        client.recording_command(
            behavior["connector_endpoint"],
            observed,
            "change_source",
            source_declaration=declaration,
        )
    observed["runtime_instance_id"] = "previous-game"
    with pytest.raises(BoundaryError, match="native_recording_game_identity_mismatch"):
        client.recording_command(behavior["connector_endpoint"], observed, "close")
    assert all(route != "/v1/tasks/recording/command" for route, _ in calls)


@pytest.mark.parametrize("field,value", [("extra", True), ("recording_lifecycle", []),
                                        ("capture_profile_id", "native-logical-source-v3")])
def test_source_status_requires_exact_safe_shape_and_profile_claim(bridge, field, value):
    client, _, _, behavior = bridge
    behavior["recording"][field] = value
    with pytest.raises(BoundaryError, match="native_recording_unavailable"):
        client.recording_status(behavior["connector_endpoint"])


@pytest.mark.parametrize(
    "fault,preflight_code",
    [
        ("unavailable", "connector_identity_unavailable"),
        ("drift", "native_recording_game_identity_mismatch"),
    ],
)
@pytest.mark.parametrize("phase", ["before_post", "after_post"])
def test_source_confirmation_failure_is_unknown_only_after_submission(
    bridge, fault, preflight_code, phase
):
    client, _, calls, behavior = bridge
    source_status(behavior)
    observed = client.recording_status(behavior["connector_endpoint"])
    reads = sum(route == "/api/player-environment/capabilities" for route, _ in calls)
    behavior["connector_fault_at"] = (reads + (1 if phase == "before_post" else 2), fault)
    behavior["recording_result_pending"] = True
    behavior["recording"].update(recording_lifecycle="closing", closeout_status="closing")
    expected = "native_recording_command_unknown" if phase == "after_post" else preflight_code
    with pytest.raises(BoundaryError) as result:
        client.recording_command(behavior["connector_endpoint"], observed, "close")
    assert result.value.code == expected
    posts = sum(route == "/v1/tasks/recording/command" for route, _ in calls)
    assert posts == (1 if phase == "after_post" else 0)
    # A recovered read observes lifecycle only; it never replays the uncertain command.
    client.recording_status(behavior["connector_endpoint"])
    assert sum(route == "/v1/tasks/recording/command" for route, _ in calls) == posts


def native_context(epoch=19):
    return {
        "runtime_status_schema": "sts2.policy-runtime/agent-session-status-1",
        "runtime_run_id": "fixture-run",
        "input_profile": "native-logical-v1",
        "recovery_epoch": epoch,
    }


def runtime_metadata():
    return {
        "schema": "sts2.policy-runtime/agent-session-status-1",
        "run_id": "fixture-run",
        "environment": {"runtime_instance_id": "game-1"},
        "session": {"profile": "native-logical-v1", "recovery_epoch": 19},
    }


def test_v2_native_agent_retains_explicit_healthy_protocol_and_exact_original_context(bridge):
    client, _, calls, behavior = bridge
    source_status(behavior, "recording")
    source = behavior["recording"]["source"]
    source["declaration"]["source_kind"] = "agent_protocol"
    source["gaps"] = 4  # accounting health does not assert zero missing coverage
    context = NativeTasks.model_context(runtime_metadata(), "fixture-run", 19)
    result = client.prepare_model(
        runtime_metadata(), behavior["connector_endpoint"], model_context=context
    )
    assert result["recording_disposition"] == "retained_agent_protocol"
    assert result["status"]["recording_lifecycle"] == "recording"
    body = next(body for route, body in calls if route == "/v2/tasks/prepare-model")
    assert (
        body["recording_session_id"] == "source-session"
        and body["source_segment_id"] == "segment-1"
    )
    assert body["model_context"] == native_context()
    assert sum(body is not None for _, body in calls) == 1
    assert all(not route.startswith("/v1") for route, _ in calls)


@pytest.mark.parametrize("kind", ["declared_human", "agent_native_ui", "unknown", "agent_protocol"])
def test_v2_incompatible_or_nonprotocol_recording_closes_without_relabel(bridge, kind):
    client, _, calls, behavior = bridge
    source_status(behavior, "recording")
    declaration = behavior["recording"]["source"]["declaration"]
    declaration["source_kind"] = kind
    context = (
        native_context()
        if kind != "agent_protocol"
        else {
            **native_context(),
            "runtime_status_schema": "sts2.policy-runtime/status-1",
            "input_profile": None,
        }
    )
    result = client.prepare_model(
        {
            "schema": context["runtime_status_schema"],
            "environment": None,
            "run_id": "fixture-run",
            "session": {"profile": "native-logical-v1", "recovery_epoch": 19},
        },
        behavior["connector_endpoint"],
        model_context=context,
    )
    assert result["recording_disposition"] == "closed_for_model"
    assert result["status"]["recording_lifecycle"] == "closed"
    assert result["status"]["source_declaration"] == declaration
    assert sum(body is not None for _, body in calls) == 1


@pytest.mark.parametrize("failure", ["paused", "accounting"])
def test_v2_paused_protocol_and_accounting_failure_never_resume_or_grant_ready(bridge, failure):
    client, _, calls, behavior = bridge
    source_status(behavior, "paused" if failure == "paused" else "recording")
    source = behavior["recording"]["source"]
    source["declaration"]["source_kind"] = "agent_protocol"
    if failure == "accounting":
        source["accounting_complete"] = False
    with pytest.raises(BoundaryError, match="recording_close_pending_or_failed"):
        client.prepare_model(
            runtime_metadata(), behavior["connector_endpoint"], model_context=native_context()
        )
    assert behavior["recording"]["recording_lifecycle"] == (
        "paused" if failure == "paused" else "recording"
    )
    assert sum(body is not None for _, body in calls) == 1


@pytest.mark.parametrize(
    "field,bad",
    [
        ("input_profile", None),
        ("input_profile", "text-menu-v1"),
        ("runtime_status_schema", "unknown"),
        ("runtime_run_id", "/private"),
        ("recovery_epoch", True),
        ("recovery_epoch", -1),
        ("recovery_epoch", 9007199254740992),
    ],
)
def test_v2_bad_context_is_rejected_before_any_owner_mutation(bridge, field, bad):
    client, _, calls, behavior = bridge
    with pytest.raises(BoundaryError, match="native_task_unavailable"):
        client.prepare_model(
            runtime_metadata(),
            behavior["connector_endpoint"],
            model_context={**native_context(), field: bad},
        )
    assert not calls


@pytest.mark.parametrize("mutation", ["bool_epoch", "segment", "declaration", "ready_disposition"])
def test_v2_invalid_after_submission_is_unknown_and_one_post(bridge, mutation):
    client, _, calls, behavior = bridge
    source_status(behavior, "recording")
    behavior["recording"]["source"]["declaration"]["source_kind"] = "agent_protocol"

    def mutate(value):
        if mutation == "bool_epoch":
            value["model_context"] = {**value["model_context"], "recovery_epoch": True}
        if mutation == "segment":
            value["status"]["source_segment_id"] = "replacement-segment"
        if mutation == "declaration":
            value["status"]["source_declaration"] = {
                **value["status"]["source_declaration"],
                "actor_id": "replacement-actor",
            }
        if mutation == "ready_disposition":
            value["recording_disposition"] = "no_active_recording"

    behavior["v2_mutate_result"] = mutate
    metadata = runtime_metadata()
    metadata["session"]["recovery_epoch"] = 1
    with pytest.raises(BoundaryError, match="native_task_command_unknown"):
        client.prepare_model(
            metadata, behavior["connector_endpoint"], model_context=native_context(1)
        )
    assert sum(body is not None for _, body in calls) == 1


@pytest.mark.parametrize("fault", ["unavailable", "drift", "lost"])
def test_v2_retained_or_close_result_identity_loss_stays_unknown_after_post(bridge, fault):
    client, _, calls, behavior = bridge
    source_status(behavior, "recording")
    behavior["recording"]["source"]["declaration"]["source_kind"] = "agent_protocol"
    if fault == "lost":
        behavior["v2_lost"] = True
    else:
        behavior["connector_fault_at"] = (2, fault)
    with pytest.raises(BoundaryError, match="native_task_command_unknown"):
        client.prepare_model(
            runtime_metadata(), behavior["connector_endpoint"], model_context=native_context()
        )
    assert sum(body is not None for _, body in calls) == 1


@pytest.mark.parametrize(
    "profile,epoch",
    [(None, 19), ("unknown", 19), ("native-logical-v1", True), ("native-logical-v1", 20)],
)
def test_fresh_native_runtime_context_requires_profile_and_original_recovery_epoch(profile, epoch):
    metadata = runtime_metadata()
    metadata["session"] = {"profile": profile, "recovery_epoch": epoch}
    with pytest.raises(BoundaryError):
        NativeTasks.model_context(metadata, "fixture-run", 19)
    assert (
        NativeTasks.model_context(
            {"schema": "sts2.policy-runtime/status-1", "run_id": "fixture-run"}, "fixture-run", 19
        )["input_profile"]
        is None
    )


@pytest.mark.parametrize(
    "lifecycle,error", [("recording", None), ("paused", "recording_close_pending_or_failed")]
)
def test_actual_local_model_handoff_retains_protocol_with_original_runtime_binding(
    bridge, tmp_path, lifecycle, error
):
    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.local_models import LocalModelService

    native, _, native_calls, behavior = bridge
    source_status(behavior, lifecycle)
    behavior["recording"]["source"]["declaration"]["source_kind"] = "agent_protocol"
    service = LocalModelService(ProjectConfig(tmp_path, "", "", None, combination()))
    service.native_tasks = native
    runtime_calls = []

    class Runtime:
        def request(self, route, value=None, *, binding=None):
            runtime_calls.append((route, value, binding))
            if route == "/environment":
                assert not native_calls
                return {
                    "schema": "sts2.policy-runtime/environment-1",
                    "run_id": "fixture-run",
                    "runtime_instance_id": "game-1",
                    "recovery_epoch": 19,
                }
            if value is not None:
                assert binding.runtime_instance_id == "game-1" and binding.recovery_epoch == 19
            return {
                "status": {
                    **runtime_metadata(),
                    "mode": (value or {}).get("mode", "human"),
                    "lifecycle": "running",
                }
            }

    service.client = Runtime()
    service.state.update(
        status="loaded", loaded=True, connector_endpoint=behavior["connector_endpoint"]
    )
    service.command("one_step")
    service.thread.join(timeout=3)
    assert not service.thread.is_alive()
    result = service.state["operation"]
    assert result["status"] == ("failed" if error else "completed")
    assert behavior["recording"]["recording_lifecycle"] == lifecycle
    assert sum(value is not None for _, value in native_calls) == 1
    submitted = next(value for route, value in native_calls if route == "/v2/tasks/prepare-model")
    assert submitted["model_context"] == native_context()
    assert [route for route, value, _ in runtime_calls if value is not None] == (
        [] if error else ["/mode", "/tick"]
    )
    if error:
        assert service.state["error_code"] == error


@pytest.mark.parametrize("defect", ["schema", "profile", "epoch"])
def test_v2_client_cannot_retain_using_context_foreign_to_fresh_native_status(bridge, defect):
    client, _, calls, behavior = bridge
    metadata = runtime_metadata()
    if defect == "schema":
        metadata["schema"] = "sts2.policy-runtime/status-1"
    if defect == "profile":
        metadata["session"]["profile"] = None
    if defect == "epoch":
        metadata["session"]["recovery_epoch"] = 20
    with pytest.raises(BoundaryError):
        client.prepare_model(
            metadata, behavior["connector_endpoint"], model_context=native_context()
        )
    assert calls == []


def test_v2_legacy_close_only_context_never_destroys_explicit_paused_protocol(bridge):
    client, _, calls, behavior = bridge
    source_status(behavior, "paused")
    behavior["recording"]["source"]["declaration"]["source_kind"] = "agent_protocol"
    context = {
        **native_context(),
        "runtime_status_schema": "sts2.policy-runtime/status-1",
        "input_profile": None,
    }
    with pytest.raises(BoundaryError, match="recording_close_pending_or_failed"):
        client.prepare_model(
            {
                "schema": "sts2.policy-runtime/status-1",
                "environment": None,
                "run_id": "fixture-run",
            },
            behavior["connector_endpoint"],
            model_context=context,
        )
    assert behavior["recording"]["recording_lifecycle"] == "paused"
    assert sum(value is not None for _, value in calls) == 1
