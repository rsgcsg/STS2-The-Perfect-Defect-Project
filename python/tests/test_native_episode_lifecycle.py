"""Additional native lifecycle fences; synthetic World/process ownership only."""
# ruff: noqa: F811 -- pytest fixture parameters shadow imported fixture functions.

from __future__ import annotations

import copy
import io
import json
import threading
import time
from types import SimpleNamespace

import pytest
from test_native_episode_application import (  # noqa: F401 -- imported pytest fixtures
    capabilities,
    native_app,
    native_caps,
    native_endpoint,
    prepare_body,
    prepared,
)

from spireagent.json_boundary import BoundaryError
from spireagent.storage.blobs import StoreError
from spireagent.storage.local import LocalBlobStore
from spireagent.workbench.developer import atomic_json
from spireagent.workbench.local_environment import LocalEnvironmentService
from spireagent.workbench.native_episode_binding import OPERATION_DIRECTORY, PROFILE_FILE


def test_configuration_drift_during_public_read_rejects_before_new_actor_authority(
    native_app, monkeypatch
):
    app, profile, _fixture, log = native_app
    ready = prepared(app)
    before = log.read_bytes()

    def drift(_endpoint):
        profile["cleanup_timeout_seconds"] = 6
        atomic_json(app.config.state_dir / PROFILE_FILE, profile)
        return capabilities()

    monkeypatch.setattr(app.local_environment, "native_capabilities", drift)
    with pytest.raises(BoundaryError, match="configuration_changed"):
        app.local_environment.authorize_native_episode(ready["admission_context"])
    assert log.read_bytes() == before


def test_driver_loss_cannot_stop_replacement_actor_or_rebound_resource(native_app, monkeypatch):
    app, _profile, _fixture, _log = native_app
    ready = prepared(app)
    context = ready["admission_context"]
    calls = []
    with monkeypatch.context() as patch:
        patch.setattr(app.models, "command", lambda *_args, **_kwargs: calls.append("stop"))
        patch.setattr(app.models, "client", SimpleNamespace(startup={"run_id": "replacement"}))
        app.models.state["native_episode"] = context
        app.models.state["startup_native_episode"] = {
            "run_id": "original",
            "admission_context": context,
        }
        app.models.native_episode_driver_lost(context)
        replacement = copy.deepcopy(context)
        replacement["binding"]["generation_id"] = "f" * 32
        app.models.state["native_episode"] = replacement
        app.models.native_episode_driver_lost(context)
    assert calls == []


def test_persisted_ready_resource_reopens_as_recovery_required_without_adoption(native_app):
    app, _profile, _fixture, log = native_app
    ready = prepared(app)
    before = log.read_bytes()
    reopened = LocalEnvironmentService(app.config)
    assert reopened.native_episode_status()["phase"] == "recovery_required"
    assert reopened.native_client is None and reopened.native_worker is None
    with pytest.raises(BoundaryError, match="resource_binding_changed"):
        reopened.native_episode_context(ready["environment_ref"])
    assert log.read_bytes() == before


def test_finish_during_real_pipe_reset_aborts_and_cannot_publish_late_ready(native_app):
    app, _profile, fixture, log = native_app
    settings = json.loads(fixture.read_bytes())
    settings["blockReset"] = True
    atomic_json(fixture, settings)
    preparing = app.prepare_native_episode(prepare_body())
    deadline = time.monotonic() + 3
    while (
        not log.exists() or "reset_started" not in log.read_text()
    ) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert log.exists() and "reset_started" in log.read_text()
    result = app.finish_native_episode(preparing["environment_ref"])
    app.local_environment.native_worker.join(timeout=3)
    assert not app.local_environment.native_worker.is_alive()
    assert result["phase"] == "unknown" and result["finish_complete"] is True
    assert result["cleanup"]["host_close"] == "unconfirmed"
    assert result["cleanup"]["close_result"] is None
    assert app.native_episode_status()["admission_context"] is None
    assert app.native_episode_status()["phase"] != "ready"
    assert "reset_aborted" in log.read_text()


def test_profile_drift_during_real_constructor_ready_wait_never_offers_reset(
    native_app, monkeypatch
):
    app, profile, _fixture, log = native_app
    entered, release = threading.Event(), threading.Event()
    factory = app.local_environment.native_client_factory

    def delayed(command, original_profile, **callbacks):
        started = callbacks["on_started"]

        def on_started(client):
            started(client)  # Actual process handle is owned before readiness wait.
            entered.set()
            assert release.wait(timeout=3)

        return factory(command, original_profile, **{**callbacks, "on_started": on_started})

    monkeypatch.setattr(app.local_environment, "native_client_factory", delayed)
    app.prepare_native_episode(prepare_body())
    assert entered.wait(timeout=3)
    profile["cleanup_timeout_seconds"] = 6
    atomic_json(app.config.state_dir / PROFILE_FILE, profile)
    release.set()
    app.local_environment.native_worker.join(timeout=3)
    assert not app.local_environment.native_worker.is_alive()
    result = app.native_episode_status()
    assert result["phase"] == "failed" and result["world_mutation_offered"] is False
    assert not log.exists() or "reset_started" not in log.read_text()


@pytest.mark.parametrize("controller", ["omitted", None, "held"])
def test_attached_unheld_uses_real_optional_null_control_snapshot_grammar(
    native_app, monkeypatch, controller
):
    from spireagent.workbench.native_tasks import NativeTasks

    app, _profile, _fixture, log = native_app
    response = {
        "protocol_version": "1.0.0",
        "schema": "sts2.player-environment/control-1",
        "runtime_instance_id": "runtime-original",
        "clients": [
            {
                "client_session_id": "fixture-client",
                "client_instance_id": "fixture-instance",
                "product_id": "fixture-extra-client-field",
            }
        ],
    }
    if controller != "omitted":
        response["controller"] = (
            None
            if controller is None
            else {
                "controller_lease_id": "lease",
                "controller_generation": 1,
                "client_session_id": "fixture-client",
                "expires_at": "2030-01-01T00:00:00Z",
            }
        )

    class Opener:
        def open(self, request, **_kwargs):
            assert (
                request.full_url == app.config.platform_url + "/api/player-environment/controller"
            )
            return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(NativeTasks, "__init__", lambda self: setattr(self, "opener", Opener()))
    if controller == "held":
        with pytest.raises(BoundaryError, match="attached_control_unavailable"):
            app.local_environment._native_unheld(app.config.platform_url, "runtime-original")
    else:
        app.local_environment._native_unheld(app.config.platform_url, "runtime-original")
    assert not log.exists() and app.local_environment.native_client is None


def test_pre_spawn_b_finish_cannot_inherit_prior_a_evaluation(native_app):
    app, _profile, _fixture, _log = native_app
    a = prepared(app)
    app.finish_native_episode(a["environment_ref"])
    app.models.state["evaluation"] = {
        "schema": "stpd/local-native-agent-operation-handoff-v1",
        "run_id": "prior-A",
        "evidence_verification": "pass",
    }
    b = prepared(app, prepare_body(intent="b" * 32))
    app.models._bind_native_episode_intent(b["admission_context"])
    # This is the actual pre-Popen state: B intent bound, no startup B run ID,
    # and A's last original report intentionally remains readable in Model.
    result = app.models.stop_native_episode_actor(b["admission_context"], timeout=1)
    assert result["run_id"] is None and result["seal"] == "not_applicable"
    assert result["evaluation_reference"] is None
    assert app.models.state["evaluation"]["run_id"] == "prior-A"


def test_final_archive_failure_reloads_as_recovery_preserving_cleanup_without_reclose(
    native_app, monkeypatch
):
    app, _profile, _fixture, log = native_app
    ready = prepared(app)
    actual = LocalBlobStore.put_if_absent

    def fail_result(store, key, raw):
        if key.startswith("results/"):
            raise StoreError("injected_terminal_publication_failure")
        return actual(store, key, raw)

    with monkeypatch.context() as patch:
        patch.setattr(LocalBlobStore, "put_if_absent", fail_result)
        result = app.finish_native_episode(ready["environment_ref"])
    before = log.read_bytes()
    assert result["phase"] == "recovery_required" and result["recorded_phase"] == "closed"
    assert result["cleanup"]["native_exit_disposition"] == "graceful"
    assert result["persistence_error"] == "native_operation_persistence_unconfirmed"
    assert not (
        app.config.state_dir / OPERATION_DIRECTORY / "results" / (("a" * 32) + ".json")
    ).exists()
    assert app.finish_native_episode(ready["environment_ref"])["phase"] == "recovery_required"
    reopened = LocalEnvironmentService(app.config)
    original = reopened.native_episode_status()
    assert original["phase"] == "recovery_required" and original["cleanup"] == result["cleanup"]
    historical, client, first = reopened.begin_native_finish(ready["environment_ref"])
    assert historical["phase"] == "recovery_required" and client is None and first is False
    assert log.read_bytes() == before


def test_late_worker_and_eof_cannot_rewrite_sealed_unknown_finish(native_app, monkeypatch):
    app, _profile, fixture, log = native_app
    settings = json.loads(fixture.read_bytes())
    settings["blockReset"] = True
    atomic_json(fixture, settings)
    preparing = app.prepare_native_episode(prepare_body())
    deadline = time.monotonic() + 3
    while (
        not log.exists() or "reset_started" not in log.read_text()
    ) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert log.exists() and "reset_started" in log.read_text()
    client = app.local_environment.native_client
    actual_close = client.close
    worker_waiting, release_worker = threading.Event(), threading.Event()

    def close(*args, **kwargs):
        result = actual_close(*args, **kwargs)
        if threading.current_thread() is app.local_environment.native_worker:
            # Hold the original reset/quarantine exception after its actual EOF
            # cleanup so the finisher can publish immutable unknown first.
            worker_waiting.set()
            assert release_worker.wait(timeout=3)
        return result

    monkeypatch.setattr(client, "close", close)
    finished = app.finish_native_episode(preparing["environment_ref"])
    assert worker_waiting.wait(timeout=3)
    assert finished["phase"] == "unknown" and finished["finish_complete"] is True
    result_path = app.config.state_dir / OPERATION_DIRECTORY / "results" / (("a" * 32) + ".json")
    original = result_path.read_bytes()
    release_worker.set()
    app.local_environment.native_worker.join(timeout=3)
    assert not app.local_environment.native_worker.is_alive()
    app.local_environment._native_driver_exited(preparing["environment_ref"])
    assert app.native_episode_status() == finished
    assert app.finish_native_episode(preparing["environment_ref"]) == finished
    assert result_path.read_bytes() == original
