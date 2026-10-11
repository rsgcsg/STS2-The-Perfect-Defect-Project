"""Fixed paired model controls use the shared owner and original Runtime context."""

from __future__ import annotations

import copy
import threading

import pytest
from test_local_models import runtime_http as runtime_http
from test_native_workbench_api import PREFIX, command, finish_model
from test_native_workbench_api import native_http as native_http

from spireagent.json_boundary import BoundaryError


@pytest.fixture
def owned(native_http, runtime_http, monkeypatch, request):
    app, call, pair, _, _, _ = native_http
    client, runtime, requests = runtime_http
    if getattr(request, "param", "policy") == "agent":
        from test_native_runtime_transport import source

        native_startup, native_status = source()
        native_startup["run_id"] = client.startup["run_id"]
        native_status.update(
            run_id=native_startup["run_id"],
            pending_request=None,
            environment={"runtime_instance_id": "game-1"},
            session={"profile": "native-logical-v1", "recovery_epoch": 0},
        )
        client.startup = native_startup
        runtime.clear()
        runtime.update(native_status)
    app.models.client = client
    app.models.state.update(
        loaded=True,
        status="loaded",
        startup=client.startup,
        runtime=copy.deepcopy(runtime),
        connector_endpoint="http://127.0.0.1:19191",
    )
    monkeypatch.setattr(app.models.native_tasks, "connector_instance", lambda _: "game-1")
    preparations = []
    monkeypatch.setattr(
        app.models.native_tasks,
        "prepare_model",
        lambda *args, **kwargs: (
            preparations.append((args, kwargs)) or {"runtime_instance_id": "game-1"}
        ),
    )
    monkeypatch.setattr(app.models, "_evaluation_handoff", lambda: None)
    return app, call, pair, client, runtime, requests, preparations


def context(app):
    return {key: value for key, value in app.models.control_context().items() if key != "schema"}


@pytest.mark.parametrize("owned", ["policy", "agent"], indirect=True)
@pytest.mark.parametrize(
    "action,mutations",
    [
        ("auto", ["/v2/mode"]),
        ("shadow", ["/v2/mode"]),
        ("one_step", ["/v2/mode", "/v2/tick"]),
        ("tick", ["/v2/tick"]),
    ],
)
def test_actual_paired_advanced_command_has_exact_owned_context_and_tick_has_no_mode_alias(
    owned, action, mutations
):
    app, call, pair, client, _, requests, preparations = owned
    original = context(app)
    view = call(PREFIX + "/view?page=play")
    actions = {item["action_id"]: item for item in view["capabilities"]["actions"]}
    assert actions["models." + action]["enabled"]
    route, body = command("models." + action, original)
    native_request_id = body["request_id"]
    result = call(route, body)
    assert result["status"] == "accepted"
    finish_model(app)
    assert app.models.state["operation"]["status"] == "completed"
    assert [route for route, value in requests if value is not None] == mutations
    assert len(preparations) == 1
    for _, body, headers in client.fixture_headers:
        if body is not None:
            observed = {key.lower(): value for key, value in headers.items()}
            assert observed["x-sts2-game-instance-id"] == original["runtime_instance_id"]
            assert observed["x-sts2-recovery-epoch"] == str(original["recovery_epoch"])
    assert (
        app.models.native_intent_context(native_request_id)["binding"]["runtime_instance_id"]
        == pair.runtime_instance_id
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("runtime_run_id", "replacement-run"),
        ("runtime_instance_id", "game-2"),
        ("recovery_epoch", 1),
        ("recovery_epoch", True),
        ("recovery_epoch", -1),
        ("extra", True),
    ],
)
def test_stale_or_malformed_context_rejects_before_intent_worker_and_native_preparation(
    owned, field, value
):
    app, call, _, _, _, requests, preparations = owned
    original = context(app)
    generation = app.models.intent_generation
    route, body = command("models.tick", {**original, field: value})
    result = call(route, body)
    assert result["status"] == "rejected"
    assert app.models.intent_generation == generation and app.models.thread is None
    assert preparations == [] and not any(body is not None for _, body in requests)


@pytest.mark.parametrize("action", ["auto", "shadow", "one_step", "tick"])
def test_source_reservation_blocks_all_new_paired_advanced_admissions_before_thread(owned, action):
    app, call, _, _, _, requests, preparations = owned
    original = context(app)
    generation = app.models.intent_generation
    with app.models.reserve_recording_source_mutation(require_human=False):
        route, body = command("models." + action, original)
        result = call(route, body)
    assert (
        result["status"] == "rejected"
        and result["error"]["code"] == "native_recording_command_pending"
    )
    assert app.models.intent_generation == generation and app.models.thread is None
    assert preparations == [] and not any(body is not None for _, body in requests)


def test_recovery_during_native_preparation_never_refreshes_original_epoch_or_sends_late_tick(
    owned, monkeypatch
):
    app, call, _, client, _, requests, preparations = owned
    original = context(app)
    entered, release = threading.Event(), threading.Event()

    def prepare(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return {"runtime_instance_id": "game-1"}

    monkeypatch.setattr(app.models.native_tasks, "prepare_model", prepare)
    route, body = command("models.one_step", original)
    assert call(route, body)["status"] == "accepted"
    assert entered.wait(timeout=2)
    client.request("/mode", {"mode": "human"})  # independent direct recovery advances owner epoch
    release.set()
    finish_model(app)
    assert app.models.state["error_code"] == "runtime_recovery_epoch_mismatch"
    assert not any(route == "/v2/tick" for route, body in requests if body is not None)
    attempted = [
        headers for route, body, headers in client.fixture_headers if body == {"mode": "one_step"}
    ]
    assert len(attempted) == 1
    assert {key.lower(): value for key, value in attempted[0].items()}[
        "x-sts2-recovery-epoch"
    ] == str(original["recovery_epoch"])


def test_explicit_tick_unknown_response_is_one_post_and_requires_owner_recovery(owned, monkeypatch):
    app, call, _, client, _, requests, _ = owned
    original = context(app)
    request = client.request
    posts = []

    def lost(route, body=None, *, binding=None):
        if route == "/tick":
            posts.append((route, body, binding))
            raise BoundaryError("local_model", "runtime_command_unknown")
        return request(route, body, binding=binding)

    monkeypatch.setattr(client, "request", lost)
    route, body = command("models.tick", original)
    assert call(route, body)["status"] == "accepted"
    finish_model(app)
    assert app.models.state["status"] == "command_unknown" and len(posts) == 1
    with pytest.raises(BoundaryError, match="previous_operation_requires_recovery"):
        app.models.command("tick")
    assert len(posts) == 1


def test_rejected_unknown_model_does_not_replace_original_native_recovery_intent(
    owned, monkeypatch
):
    app, call, _, client, _, _, _ = owned
    original = context(app)
    request = client.request

    def lost(route, body=None, *, binding=None):
        if route == "/tick":
            raise BoundaryError("local_model", "runtime_command_unknown")
        return request(route, body, binding=binding)

    monkeypatch.setattr(client, "request", lost)
    route, body = command("models.tick", original, request_id="f" * 32)
    assert call(route, body)["status"] == "accepted"
    finish_model(app)
    generation = app.models.intent_generation
    retained = copy.deepcopy(app.models.state["_native_intent"])
    route, body = command("models.auto", original, request_id="e" * 32)
    assert call(route, body)["status"] == "rejected"
    assert (
        app.models.intent_generation == generation
        and app.models.state["_native_intent"] == retained
    )
    assert app.models.native_intent_context("f" * 32)["request_id"] == "f" * 32
