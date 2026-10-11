"""Shared Workbench admission races using actual app/model services and loopback owners."""

from __future__ import annotations

import copy
import threading

import pytest
from metadata_import_guard import no_torch_imports as no_torch_imports
from test_local_models import runtime_http as runtime_http
from test_native_recording_application import body
from test_native_recording_application import browser_app as browser_app
from test_native_tasks import bridge as bridge
from test_native_tasks import source_status

from spireagent.json_boundary import BoundaryError


@pytest.fixture
def shared(browser_app, runtime_http, bridge, monkeypatch):
    app, _, _, _, _, _, _ = browser_app
    client, runtime, runtime_calls = runtime_http
    native, _, native_calls, behavior = bridge
    app.models.client = client
    app.models.native_tasks = native
    app.models.state.update(
        status="loaded",
        loaded=True,
        runtime=copy.deepcopy(runtime),
        connector_endpoint=behavior["connector_endpoint"],
    )
    monkeypatch.setattr(app, "_recording_endpoint", lambda: behavior["connector_endpoint"])
    monkeypatch.setattr(app.models, "_evaluation_handoff", lambda: None)
    yield app, client, runtime, runtime_calls, native, native_calls, behavior


def worker(operation):
    values, errors = [], []

    def run():
        try:
            values.append(operation())
        except BoundaryError as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, values, errors


def finish(thread):
    thread.join(timeout=3)
    assert not thread.is_alive()


def pause_at_owner_status(native, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = native.recording_status

    def query(endpoint):
        entered.set()
        assert release.wait(timeout=3)
        return original(endpoint)

    monkeypatch.setattr(native, "recording_status", query)
    return entered, release


@pytest.mark.parametrize("action", ["auto", "one_step", "shadow"])
@pytest.mark.parametrize("source_action", ["start_new_session", "change_source"])
def test_source_first_rejects_model_before_intent_or_thread_and_never_queues_late_auto(
    shared,
    monkeypatch,
    action,
    source_action,
):
    app, _, _, runtime_calls, native, native_calls, behavior = shared
    if source_action == "change_source":
        source_status(behavior, "paused")
    before = copy.deepcopy(behavior["recording"])
    entered, release = pause_at_owner_status(native, monkeypatch)
    thread, values, errors = worker(
        lambda: app.control_native_recording(body(before, source_action, "declared_human"))
    )
    try:
        assert entered.wait(timeout=2)
        generation = app.models.intent_generation
        assert app.models.thread is None
        with pytest.raises(BoundaryError, match="native_recording_command_pending"):
            app.models.command(action)
        assert app.models.intent_generation == generation and app.models.thread is None
        assert all(value is None for _, value in runtime_calls)
    finally:
        release.set()
        finish(thread)
    assert not errors and values[0]["accepted"]
    posts = [value for route, value in native_calls if route == "/v1/tasks/recording/command"]
    assert (
        len(posts) == 1
        and posts[0]["command"]["source_declaration"]["source_kind"] == "declared_human"
    )
    assert posts[0]["command"]["source_declaration"]["machine_verifiable"] is False
    assert all(
        value is None for _, value in runtime_calls
    )  # releasing does not queue the rejected action


@pytest.mark.parametrize("source_action", ["pause", "resume", "close", "start_new_session"])
def test_every_source_first_intent_blocks_new_model_admission(shared, monkeypatch, source_action):
    app, _, _, runtime_calls, native, native_calls, behavior = shared
    if source_action != "start_new_session":
        source_status(behavior, "paused" if source_action == "resume" else "recording")
    source_kind = "agent_protocol" if source_action == "start_new_session" else None
    entered, release = pause_at_owner_status(native, monkeypatch)
    thread, _, errors = worker(
        lambda: app.control_native_recording(
            body(behavior["recording"], source_action, source_kind)
        )
    )
    try:
        assert entered.wait(timeout=2)
        generation = app.models.intent_generation
        with pytest.raises(BoundaryError, match="native_recording_command_pending"):
            app.models.command("auto")
        assert app.models.intent_generation == generation and app.models.thread is None
    finally:
        release.set()
        finish(thread)
    assert not errors
    assert sum(route == "/v1/tasks/recording/command" for route, _ in native_calls) == 1
    assert not any(value is not None for _, value in runtime_calls)


@pytest.mark.parametrize("source_action", ["pause", "resume", "close", "start_new_session"])
def test_preexisting_model_intent_is_not_cancelled_by_later_source_control(
    shared, monkeypatch, source_action
):
    app, _, _, _, native, _, behavior = shared
    entered, release = threading.Event(), threading.Event()
    original = native.prepare_model

    def prepare(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return original(*args, **kwargs)

    monkeypatch.setattr(native, "prepare_model", prepare)
    app.models.command("auto")
    try:
        assert entered.wait(timeout=2)
        generation = app.models.intent_generation
        behavior["recording_apply_lifecycle"] = True
        if source_action != "start_new_session":
            source_status(behavior, "paused" if source_action == "resume" else "recording")
            behavior["recording"]["source"]["declaration"]["source_kind"] = "agent_protocol"
        result = app.control_native_recording(
            body(
                behavior["recording"],
                source_action,
                "agent_protocol" if source_action == "start_new_session" else None,
            )
        )
        assert result["accepted"] and app.models.intent_generation == generation
    finally:
        release.set()
        finish(app.models.thread)
    if source_action == "pause":
        assert app.models.state["operation"]["status"] == "failed"
        assert app.models.state["error_code"] == "recording_close_pending_or_failed"
        assert behavior["recording"]["recording_lifecycle"] == "paused"
    else:
        assert app.models.state["operation"]["status"] == "completed"


@pytest.mark.parametrize("source_action", ["pause", "resume", "close"])
def test_existing_auto_can_explicitly_control_agent_recording_without_gameplay_veto(
    shared, source_action
):
    app, _, runtime, runtime_calls, _, _, behavior = shared
    runtime["mode"] = "auto"
    app.models.state["runtime"] = copy.deepcopy(runtime)
    source_status(behavior, "paused" if source_action == "resume" else "recording")
    behavior["recording"]["source"]["declaration"]["source_kind"] = "agent_protocol"
    behavior["recording_apply_lifecycle"] = True
    generation = app.models.intent_generation
    result = app.control_native_recording(body(behavior["recording"], source_action))
    assert result["accepted"] and app.models.intent_generation == generation
    assert (
        behavior["recording"]["recording_lifecycle"]
        == {"pause": "paused", "resume": "recording", "close": "closed"}[source_action]
    )
    assert runtime["mode"] == "auto" and not any(value is not None for _, value in runtime_calls)


@pytest.mark.parametrize("entry", ["start", "prepare_and_load", "prepare_and_takeover"])
def test_source_first_rejects_every_model_start_admission_before_worker_creation(
    shared, monkeypatch, entry
):
    app, _, _, _, native, _, behavior = shared
    app.models.client = None
    app.models.state.update(status="idle", loaded=False, runtime=None)
    monkeypatch.setattr(app.models, "_run_profile", lambda *args: True)
    starts = []
    monkeypatch.setattr(app.models, "_start", lambda *args: starts.append(args))
    monkeypatch.setattr(
        app.models, "readiness", lambda _: pytest.fail("rejected load must not begin readiness")
    )
    entered, release = pause_at_owner_status(native, monkeypatch)
    thread, _, errors = worker(
        lambda: app.control_native_recording(
            body(behavior["recording"], "start_new_session", "unknown")
        )
    )
    try:
        assert entered.wait(timeout=2)
        generation = app.models.intent_generation
        with pytest.raises(BoundaryError, match="native_recording_command_pending"):
            getattr(app.models, entry)("fixture-model")
        assert app.models.intent_generation == generation and app.models.thread is None
    finally:
        release.set()
        finish(thread)
    assert not errors and starts == []


@pytest.mark.parametrize("action", ["auto", "one_step", "shadow"])
def test_model_first_pending_admission_blocks_source_before_any_native_recording_query(
    shared, monkeypatch, action
):
    app, _, _, _, native, native_calls, behavior = shared
    entered, release = threading.Event(), threading.Event()
    original = native.prepare_model

    def prepare(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return original(*args, **kwargs)

    monkeypatch.setattr(native, "prepare_model", prepare)
    app.models.command(action)
    try:
        assert entered.wait(timeout=2)
        before = list(native_calls)
        with pytest.raises(BoundaryError, match="model_recovery_required"):
            app.control_native_recording(
                body(behavior["recording"], "start_new_session", "declared_human")
            )
        assert native_calls == before
    finally:
        release.set()
        finish(app.models.thread)
    assert app.models.state["operation"]["status"] == "completed"
    assert not any(route == "/v1/tasks/recording/command" for route, _ in native_calls)


@pytest.mark.parametrize("entry", ["start", "prepare_and_load", "prepare_and_takeover"])
def test_pending_real_model_start_or_load_intent_blocks_source_without_waiting_for_model_completion(
    shared,
    monkeypatch,
    entry,
):
    app, _, _, _, _, native_calls, behavior = shared
    app.models.client = None
    app.models.state.update(status="idle", loaded=False, runtime=None)
    monkeypatch.setattr(app.models, "_run_profile", lambda *args: True)
    entered, release = threading.Event(), threading.Event()

    def blocked(*args):
        entered.set()
        assert release.wait(timeout=3)
        return {"checks": {}}

    if entry == "start":
        monkeypatch.setattr(app.models, "_start", blocked)
    else:
        monkeypatch.setattr(app.models, "readiness", blocked)
        monkeypatch.setattr(app.models, "_start", lambda *args: None)
    getattr(app.models, entry)("fixture-model")
    try:
        assert entered.wait(timeout=2)
        assert app.models.state["operation"]["status"] == "pending"
        with pytest.raises(BoundaryError, match="model_recovery_required"):
            app.control_native_recording(
                body(behavior["recording"], "start_new_session", "agent_native_ui")
            )
        assert not native_calls
    finally:
        release.set()
        finish(app.models.thread)


@pytest.mark.parametrize("recovery", ["human", "stop"])
@pytest.mark.parametrize("blocked_phase", ["status", "post"])
def test_source_network_wait_never_holds_model_lock_or_blocks_actual_human_stop(
    shared, monkeypatch, recovery, blocked_phase
):
    app, _, _, runtime_calls, native, _, behavior = shared
    entered, release = threading.Event(), threading.Event()
    if blocked_phase == "status":
        entered, release = pause_at_owner_status(native, monkeypatch)
    else:
        original = native._recording_request

        def request(route, value=None):
            if value is not None:
                entered.set()
                assert release.wait(timeout=3)
            return original(route, value)

        monkeypatch.setattr(native, "_recording_request", request)
    thread, _, errors = worker(
        lambda: app.control_native_recording(
            body(behavior["recording"], "start_new_session", "declared_human")
        )
    )
    try:
        assert entered.wait(timeout=2)
        app.models.command(recovery)
        finish(app.models.thread)
        route = "/v2/mode" if recovery == "human" else "/v2/stop"
        assert any(item[0] == route and item[1] is not None for item in runtime_calls)
        assert app.models.state["operation"]["status"] == "completed"
        assert thread.is_alive()  # the real recovery returned while Source I/O is still held
    finally:
        release.set()
        finish(thread)
    assert not errors


def start_unknown_source(shared):
    app, _, _, _, _, native_calls, behavior = shared
    entered, release = threading.Event(), threading.Event()
    behavior.update(
        recording_outcome="lost_pending", recording_entered=entered, recording_release=release
    )
    request = body(behavior["recording"], "start_new_session", "declared_human")
    thread, _, errors = worker(lambda: app.control_native_recording(request))
    assert entered.wait(timeout=2)
    finish(thread)  # response is lost while the server's Source POST remains held
    assert len(errors) == 1 and errors[0].code == "native_recording_command_unknown"
    assert sum(route == "/v1/tasks/recording/command" for route, _ in native_calls) == 1
    assert app.models._recording_source_reservation is None
    return request, release


def test_unknown_source_model_admission_stays_fenced_until_exact_durable_close(
    shared,
):
    app, _, _, _, _, native_calls, behavior = shared
    request, release = start_unknown_source(shared)
    try:
        generation = app.models.intent_generation
        for _ in range(2):
            assert app.native_recording_status()["recovery_required"]
            with pytest.raises(BoundaryError, match="native_recording_command_pending"):
                app.models.command("auto")
        assert app.models.intent_generation == generation and app.models.thread is None
        assert sum(route == "/v1/tasks/recording/command" for route, _ in native_calls) == 1
    finally:
        release.set()
    behavior["recording_outcome"] = "success"
    source_status(behavior, "closing")
    behavior["recording"].update(closeout_status="closing")
    app.control_native_recording(body(behavior["recording"], "close"))
    with pytest.raises(BoundaryError, match="native_recording_command_pending"):
        app.models.command("auto")  # accepted/pending Close is insufficient
    behavior["recording_outcome"] = "rejected"
    with pytest.raises(BoundaryError):
        app.control_native_recording(body(behavior["recording"], "close"))
    with pytest.raises(BoundaryError, match="native_recording_command_pending"):
        app.models.command("auto")
    behavior["recording_outcome"] = "success"
    behavior["recording"].update(recording_lifecycle="closed", closeout_status="closed")
    app.control_native_recording(body(behavior["recording"], "close"))
    assert app._recording_unknown_notice["command_id"] == request["command_id"]
    assert not app._recording_model_admission_blocked()
    app.models.command("auto")
    finish(app.models.thread)
    assert app.models.state["operation"]["status"] == "completed"
    assert app._recording_unknown_notice["command_id"] == request["command_id"]


def test_known_new_runtime_makes_old_unknown_inapplicable_without_source_start_or_notice_rewrite(
    shared, monkeypatch
):
    app, _, _, _, _, native_calls, behavior = shared
    request, release = start_unknown_source(shared)
    release.set()
    app.models.command("stop")
    finish(app.models.thread)
    monkeypatch.setattr(app.models, "_run_profile", lambda *args: True)
    starts = []
    monkeypatch.setattr(app.models, "_start", lambda *args: starts.append(args))
    app.native_recording_status()
    with pytest.raises(BoundaryError, match="native_recording_command_pending"):
        app.models.start("fixture-model")
    original_notice = copy.deepcopy(app._recording_unknown_notice)
    behavior["capabilities"]["host"]["runtime_instance_id"] = "game-2"
    behavior["recording"]["runtime_instance_id"] = "game-2"
    view = (
        app.native_recording_status()
    )  # real pre/post Connector identity checked, same typed owner
    assert not view["recovery_required"] and app._recording_unknown_blocks
    assert app._recording_unknown_notice == original_notice
    app.models.start("fixture-model")
    finish(app.models.thread)
    assert len(starts) == 1 and app.models.state["operation"]["status"] == "completed"
    assert app._recording_unknown_notice["command_id"] == request["command_id"]
    assert sum(route == "/v1/tasks/recording/command" for route, _ in native_calls) == 1
