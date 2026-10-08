"""Actual loopback transport tests; Runtime gameplay semantics are owned upstream."""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_models import (
    AGENT_STARTUP,
    AGENT_STATUS,
    LocalModelService,
    RuntimeClient,
    RuntimeControlBinding,
)


def source():
    adapter = {
        "id": "synthetic",
        "version": "1",
        "protocol": "sts2.policy-runtime/agent-session-ndjson-1",
        "code_sha256": "d" * 64,
    }
    startup = {
        "schema": AGENT_STARTUP,
        "run_id": "run-original",
        "agent_manifest_id": "manifest-original",
        "agent_manifest_sha256": "a" * 64,
        "agent_artifact_sha256": "b" * 64,
        "runtime_version": "synthetic",
        "runtime_code_sha256": "c" * 64,
        "adapter": adapter,
        "autonomy_budget": {"maxSubmissions": 16, "maxPolicyCalls": 32, "deadlineMs": 60000},
    }
    pending = {
        "request_id": "request-original",
        "run_id": "run-original",
        "runtime_instance_id": "game-original",
        "session_id": "session-original",
        "submission_epoch": 1,
        "basis_acquisition_id": "basis-original",
        "snapshot_id": "snapshot-original",
        "action_id": "action-original",
        "status": "pending",
        "reason": None,
    }
    status = {
        "schema": AGENT_STATUS,
        "run_id": startup["run_id"],
        "agent_manifest_sha256": startup["agent_manifest_sha256"],
        "runtime": {
            "version": startup["runtime_version"],
            "code_sha256": startup["runtime_code_sha256"],
        },
        "agent": {
            "manifest_id": startup["agent_manifest_id"],
            "agent_id": "agent",
            "agent_version": "1",
            "provider": "synthetic",
            "architecture": "synthetic",
            "artifact_id": "package-original",
            "artifact_sha256": startup["agent_artifact_sha256"],
            "adapter": adapter,
        },
        "lifecycle": "running",
        "mode": "human",
        "controller": "released",
        "tainted": False,
        "taint_reason": None,
        "refreshing": False,
        "errors": [],
        "invalidations": [],
        "environment": None,
        "session": None,
        "last_observation": None,
        "last_directive": None,
        "last_result": None,
        "pending_request": pending,
        "autonomy_budget": {
            "max_submissions": 16,
            "submissions_used": 1,
            "max_policy_calls": 32,
            "policy_calls_used": 1,
            "deadline_ms": 60000,
            "elapsed_ms": 20,
            "remaining_ms": 59980,
            "state": "inactive",
            "exhausted_reason": None,
            "ended_reason": "mode_changed",
        },
    }
    return startup, status


@pytest.fixture
def native_http():
    startup, status = source()
    calls = []
    behavior = {"resolution": "resolved", "epoch": 3, "runtime_instance_id": "game-original"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, value, code=200):
            raw = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            calls.append(("GET", self.path, None, dict(self.headers)))
            if self.path == "/v2/environment":
                self.reply(
                    {
                        "schema": "sts2.policy-runtime/environment-1",
                        "run_id": startup["run_id"],
                        "runtime_instance_id": behavior["runtime_instance_id"],
                        "recovery_epoch": behavior["epoch"],
                    }
                )
            elif self.path == "/status":
                self.reply({"schema": "sts2.policy-runtime/http-2", "status": status})
            else:
                self.reply({"error": "unknown"}, 404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(("POST", self.path, body, dict(self.headers)))
            if self.path != "/v2/reconcile":
                self.reply({"error": "unexpected mutation"}, 404)
                return
            if behavior["resolution"] == "rejected":
                self.reply(
                    {
                        "schema": "sts2.policy-runtime/http-2",
                        "error": "runtime_pending_request_mismatch",
                    },
                    409,
                )
                return
            if behavior["resolution"] == "resolved":
                status["pending_request"] = None
            self.reply(
                {
                    "schema": "sts2.policy-runtime/http-2",
                    "request_id": body["request_id"],
                    "resolution": behavior["resolution"],
                    "status": status,
                }
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    client = RuntimeClient(f"http://127.0.0.1:{server.server_port}", startup)
    try:
        yield client, status, calls, behavior
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_native_namespace_rejects_legacy_and_foreign_fields(native_http):
    client, status, _, _ = native_http
    client.validate_status(status)
    for change in ("legacy", "foreign", "identity"):
        value = copy.deepcopy(status)
        if change == "legacy":
            value["schema"] = "sts2.policy-runtime/status-1"
        elif change == "foreign":
            value["policy"] = {"manifest_id": "fake"}
        else:
            value["agent"]["artifact_sha256"] = "f" * 64
        with pytest.raises(ValueError):
            client.validate_status(value)


def test_same_service_explicit_original_reconcile_sends_one_bound_post_and_stays_human(
    tmp_path, native_http
):
    client, status, calls, _ = native_http
    models = LocalModelService(ProjectConfig(tmp_path, "", "", None, combination()))
    models.client = client
    models.state.update(loaded=True, status="loaded", runtime=copy.deepcopy(status))
    models.command("reconcile", request_id="request-original")
    assert models.thread is not None
    models.thread.join(timeout=5)
    assert not models.thread.is_alive()
    assert models.state["operation"]["status"] == "completed", models.state
    posts = [call for call in calls if call[0] == "POST"]
    assert len(posts) == 1 and posts[0][1:3] == (
        "/v2/reconcile",
        {"request_id": "request-original"},
    )
    assert posts[0][3]["X-Sts2-Policy-Run-Id"] == "run-original"
    assert posts[0][3]["X-Sts2-Game-Instance-Id"] == "game-original"
    assert posts[0][3]["X-Sts2-Recovery-Epoch"] == "3"
    assert models.state["runtime"]["mode"] == "human"
    assert models.state["runtime"]["pending_request"] is None
    assert models.state["last_reconciliation"] == {
        "request_id": "request-original",
        "resolution": "resolved",
    }
    assert all("current" not in call[1] and "actions" not in call[1] for call in calls)


def test_changed_original_or_game_binding_sends_no_reconcile_post(tmp_path, native_http):
    client, status, calls, behavior = native_http
    models = LocalModelService(ProjectConfig(tmp_path, "", "", None, combination()))
    models.client = client
    models.state.update(loaded=True, status="loaded", runtime=copy.deepcopy(status))
    with pytest.raises(BoundaryError, match="native_pending_request_required"):
        models.command("reconcile", request_id="replacement-request")
    assert calls == []
    original = copy.deepcopy(status["pending_request"])
    status["pending_request"]["basis_acquisition_id"] = "replacement-current"
    with pytest.raises(BoundaryError, match="native_pending_request_changed"):
        models._execute_reconcile(models.intent_generation, client, original)
    assert not any(call[0] == "POST" for call in calls)
    status["pending_request"] = original
    behavior["runtime_instance_id"] = "replacement-game"
    with pytest.raises(BoundaryError, match="runtime_game_mismatch"):
        models._execute_reconcile(models.intent_generation, client, original)
    assert not any(call[0] == "POST" for call in calls)


def test_pending_reply_is_not_automatically_polled_or_resubmitted(native_http):
    client, status, calls, behavior = native_http
    behavior["resolution"] = "pending"
    reply = client.request(
        "/reconcile",
        {"request_id": "request-original"},
        binding=RuntimeControlBinding("game-original", 3),
    )
    assert (
        reply["resolution"] == "pending"
        and reply["status"]["pending_request"] == status["pending_request"]
    )
    assert len(calls) == 1


def test_known_original_rejection_keeps_native_unknown_scope_closed(native_http):
    client, _, calls, behavior = native_http
    behavior["resolution"] = "rejected"
    with pytest.raises(BoundaryError, match="runtime_pending_request_mismatch"):
        client.request(
            "/reconcile",
            {"request_id": "request-original"},
            binding=RuntimeControlBinding("game-original", 3),
        )
    assert len(calls) == 1
