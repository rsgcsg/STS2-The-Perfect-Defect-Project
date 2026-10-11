"""Original Native intent/result retention across serial resources and restart."""
# ruff: noqa: F811 -- pytest fixture parameters shadow imported fixture functions.

from __future__ import annotations

import json

import pytest
from test_native_episode_application import (  # noqa: F401 -- imported pytest fixtures
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
from spireagent.workbench.native_episode_binding import OPERATION_DIRECTORY


def reset_count(log):
    return [json.loads(line)["event"] for line in log.read_bytes().splitlines()].count(
        "reset_started"
    )


def two_closed(app):
    a = prepared(app)
    closed_a = app.finish_native_episode(a["environment_ref"])
    b = prepared(app, prepare_body(intent="b" * 32))
    app.finish_native_episode(b["environment_ref"])
    return a, closed_a, b


def test_a_close_b_close_replay_a_returns_original_and_never_resets_again(native_app):
    app, _profile, _fixture, log = native_app
    a, closed_a, b = two_closed(app)
    before = log.read_bytes()
    assert a["environment_ref"] != b["environment_ref"] and reset_count(log) == 2
    assert app.prepare_native_episode(prepare_body()) == closed_a
    assert app.finish_native_episode(a["environment_ref"]) == closed_a
    with pytest.raises(BoundaryError, match="preparation_intent_conflict"):
        app.prepare_native_episode(prepare_body(seed="DIFFERENT"))
    assert log.read_bytes() == before


def test_old_finish_a_and_replay_a_while_b_active_cannot_stop_or_close_b(native_app, monkeypatch):
    app, _profile, _fixture, log = native_app
    a = prepared(app)
    closed_a = app.finish_native_episode(a["environment_ref"])
    b = prepared(app, prepare_body(intent="b" * 32))
    b_client = app.local_environment.native_client
    before = log.read_bytes()
    stops = []
    with monkeypatch.context() as patch:
        patch.setattr(
            app.models, "stop_native_episode_actor", lambda *_a, **_kw: stops.append("stop")
        )
        assert app.finish_native_episode(a["environment_ref"]) == closed_a
        assert app.prepare_native_episode(prepare_body()) == closed_a
    assert stops == [] and log.read_bytes() == before
    assert app.native_episode_status()["environment_ref"] == b["environment_ref"]
    assert app.native_episode_status()["phase"] == "ready"
    assert app.local_environment.native_client is b_client and b_client._process.poll() is None


def test_restart_reads_original_closed_a_without_constructing_or_adopting_a_host(native_app):
    app, _profile, _fixture, log = native_app
    a, closed_a, _b = two_closed(app)
    before = log.read_bytes()
    reopened = LocalEnvironmentService(app.config)
    assert reopened.prepare_native_episode(prepare_body()) == closed_a
    historical, client, first = reopened.begin_native_finish(a["environment_ref"])
    assert historical == closed_a and client is None and first is False
    assert reopened.native_client is None and reopened.native_worker is None
    assert log.read_bytes() == before


def test_known_failed_a_stays_original_failed_after_b_without_retrying_world_offer(native_app):
    app, _profile, fixture, log = native_app
    settings = json.loads(fixture.read_bytes())
    settings["missingCapability"] = True
    atomic_json(fixture, settings)
    app.prepare_native_episode(prepare_body())
    app.local_environment.native_worker.join(timeout=3)
    failed = app.native_episode_status()
    assert failed["phase"] == "failed" and failed["world_mutation_offered"] is False
    settings["missingCapability"] = False
    atomic_json(fixture, settings)
    prepared(app, prepare_body(intent="b" * 32))
    before = log.read_bytes()
    assert app.prepare_native_episode(prepare_body()) == failed
    assert app.finish_native_episode(failed["environment_ref"]) == failed
    assert log.read_bytes() == before and reset_count(log) == 1


def test_immutable_request_write_failure_fences_before_client_and_survives_restart(
    native_app, monkeypatch
):
    app, _profile, _fixture, log = native_app
    actual = LocalBlobStore.put_if_absent

    def failed(store, key, raw):
        if key.startswith("requests/"):
            raise StoreError("injected_request_write_failure")
        return actual(store, key, raw)

    with monkeypatch.context() as patch:
        patch.setattr(LocalBlobStore, "put_if_absent", failed)
        with pytest.raises(BoundaryError, match="native_operation_persistence_failed"):
            app.prepare_native_episode(prepare_body())
    assert not log.exists() and app.local_environment.native_client is None
    assert app.local_environment.native_worker is None
    assert app.native_episode_status()["phase"] == "recovery_required"
    reopened = LocalEnvironmentService(app.config)
    assert reopened.native_episode_status()["phase"] == "recovery_required"
    with pytest.raises(BoundaryError):
        app.prepare_native_episode(prepare_body())
    assert not log.exists()


def test_missing_retained_request_cannot_turn_historical_a_into_a_new_reset(native_app):
    app, _profile, _fixture, log = native_app
    _a, _closed_a, _b = two_closed(app)
    before = log.read_bytes()
    path = app.config.state_dir / OPERATION_DIRECTORY / "requests" / (("a" * 32) + ".json")
    path.unlink()  # Explicit fault injection in the isolated temporary operation archive.
    with pytest.raises(BoundaryError, match="native_original_request_missing"):
        app.prepare_native_episode(prepare_body())
    assert log.read_bytes() == before and reset_count(log) == 2
