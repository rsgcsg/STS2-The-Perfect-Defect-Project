"""Application takeover intent uses the existing real Runtime HTTP fixture."""

from __future__ import annotations

import threading

import pytest
from test_local_models import finished
from test_local_models import runtime_http as runtime_http
from test_local_models import service as service

from spireagent.json_boundary import BoundaryError
from spireagent.workbench import local_models


def ready(service, monkeypatch, client):
    monkeypatch.setattr(
        service,
        "readiness",
        lambda _: {
            "checks": {
                "runtime_package": {"status": "pass"},
                "public_contract": {"status": "pass"},
                "backend": {"status": "pass"},
            }
        },
    )

    def loaded(identity, intent, *args):
        service._require_intent(intent)
        service.client = client
        service.state.update(loaded=True, status="loaded", selection_id=identity)

    monkeypatch.setattr(service, "_start", loaded)


def test_one_takeover_intent_loads_human_then_same_epoch_auto_once(
    service, runtime_http, monkeypatch
):
    client, state, requests = runtime_http
    ready(service, monkeypatch, client)
    service.prepare_and_takeover("s1-human-combat-v4")
    result = finished(service)
    assert result["operation"]["action"] == "prepare-and-takeover"
    assert result["operation"]["status"] == "completed"
    assert state["mode"] == "auto"
    assert [body for path, body in requests if path == "/v2/mode"] == [{"mode": "auto"}]
    headers = {key.lower(): value for key, value in client.fixture_headers[-1][2].items()}
    assert headers["x-sts2-game-instance-id"] == "game-1"
    assert headers["x-sts2-recovery-epoch"] == "0"


def test_manual_prepare_load_keeps_existing_human_only_semantics(
    service, runtime_http, monkeypatch
):
    client, state, requests = runtime_http
    ready(service, monkeypatch, client)
    service.prepare_and_load("s1-human-combat-v4")
    assert finished(service)["operation"]["status"] == "completed"
    assert state["mode"] == "human"
    assert not any(path == "/v2/mode" for path, _body in requests)


def test_human_interrupt_during_takeover_load_fences_every_later_auto(
    service, runtime_http, monkeypatch
):
    client, _state, requests = runtime_http
    ready(service, monkeypatch, client)
    original = service._start
    entered, release = threading.Event(), threading.Event()

    def held(identity, intent, *args):
        entered.set()
        assert release.wait(3)
        original(identity, intent, *args)

    monkeypatch.setattr(service, "_start", held)
    service.prepare_and_takeover("s1-human-combat-v4")
    assert entered.wait(3)
    worker = service.thread
    service.command("human")
    release.set()
    result = finished(service)
    assert worker is not None
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert result["loaded"] is False and result["status"] == "stopped"
    assert not any(path == "/v2/mode" and body == {"mode": "auto"} for path, body in requests)


@pytest.mark.parametrize("failure", ["revoked", "replacement"])
def test_native_load_revalidates_auth_and_original_game_before_spawning(
    service, monkeypatch, failure
):
    monkeypatch.setattr(service, "readiness", lambda _: {"status": "ready_to_load"})
    monkeypatch.setattr(service, "_runtime_package", lambda _: {"version": "fixture"})
    monkeypatch.setattr(local_models, "_check_runtime_port", lambda _: None)
    spawned = []
    monkeypatch.setattr(local_models.subprocess, "Popen", lambda *a, **kw: spawned.append(a))
    service.state["_native_intent"] = {
        "intent_generation": service.intent_generation,
        "binding": {"runtime_instance_id": "game-1"},
    }

    def authorize():
        if failure == "revoked":
            raise BoundaryError("native_workbench", "native_access_not_configured")

    service._native_authorizer = authorize
    monkeypatch.setattr(service.native_tasks, "connector_instance", lambda _: "replacement")
    expected = "native_access_not_configured" if failure == "revoked" else (
        "native_model_context_changed"
    )
    with pytest.raises(BoundaryError) as rejected:
        service._start("s1-human-combat-v4")
    assert rejected.value.code == expected
    assert spawned == []
