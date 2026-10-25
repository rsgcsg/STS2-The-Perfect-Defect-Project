"""Real Environment/store and public Host pipe, synthetic World; OS census isolated."""
# ruff: noqa: F811 -- imported shared pytest fixtures.
from __future__ import annotations

import subprocess
import sys
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from test_native_episode_application import (  # noqa: F401
    native_app,
    native_caps,
    native_endpoint,
    prepare_body,
    prepared,
)

from spireagent.json_boundary import BoundaryError
from spireagent.storage.blobs import StoreError
from spireagent.storage.local import LocalBlobStore
from spireagent.workbench import native_unknown
from spireagent.workbench.developer_server import create_server
from spireagent.workbench.local_model_cli import native_episode_command
from spireagent.workbench.native_episode_binding import RESOURCE_FILE


def interrupted(app, monkeypatch):
    ready = prepared(app)
    service = app.local_environment
    service.native_record["actor"] = {"run_id": "run-" + "a" * 36}
    service._save_native()
    record, client, first = service.begin_native_finish(ready["environment_ref"])
    assert first
    put = LocalBlobStore.put_if_absent

    def enospc(store, key, raw):
        if key.startswith("results/"):
            raise StoreError("test_ENOSPC")
        return put(store, key, raw)

    with monkeypatch.context() as patch:
        patch.setattr(LocalBlobStore, "put_if_absent", enospc)
        final = service.close_native_after_actor(
            record, client, {"status": "stopped", "seal": "unknown"})
    assert final["phase"] == "recovery_required"
    assert final["cleanup"]["driver_exit_code"] == 0
    return service.native_unknown_preview()["expected"]


@pytest.fixture
def absent_os(monkeypatch):
    monkeypatch.setattr(native_unknown, "no_handles", lambda _path: None)
    monkeypatch.setattr(native_unknown, "unoccupied", lambda *_args: {
        "game_process_census": "absent", "host_state_handles": "absent",
        "endpoint_observation": "connection_refused"})


def test_release_is_append_only_and_new_world_preserves_original_unknown(
    native_app, monkeypatch, absent_os,
):
    app, _profile, _fixture, log = native_app
    expected = interrupted(app, monkeypatch)
    service = app.local_environment
    original = (app.config.state_dir / RESOURCE_FILE).read_bytes()
    status_path = (app.config.state_dir / "native-episode-operations-v1" / "operations"
                   / expected["expected_environment_ref"] / "status.json")
    old_status = status_path.read_bytes()
    with pytest.raises(BoundaryError, match="native_resource_reserved"):
        app.prepare_native_episode(prepare_body("b" * 32))
    receipt = app.seal_native_unknown(expected)
    assert receipt["result"] == "unknown" and receipt["released"] is True
    assert (app.config.state_dir / RESOURCE_FILE).read_bytes() == original
    assert service.native_episode_status()["phase"] == "recovery_required"
    assert app.seal_native_unknown(expected) == receipt
    replacement = prepared(app, prepare_body("b" * 32))
    assert replacement["environment_ref"] != expected["expected_environment_ref"]
    assert status_path.read_bytes() == old_status
    history, _, first = service.begin_native_finish(expected["expected_environment_ref"])
    assert not first and history["phase"] == "recovery_required"
    assert '"reset_started"' in log.read_text()


@pytest.mark.parametrize("field", ["expected_generation_id", "expected_run_id",
                                   "expected_resource_sha256", "expected_application_instance_id"])
def test_changed_expected_identity_rejects(native_app, monkeypatch, absent_os, field):
    app, *_ = native_app
    expected = interrupted(app, monkeypatch)
    expected[field] = "different"
    with pytest.raises(BoundaryError, match="identity_mismatch"):
        app.seal_native_unknown(expected)
    assert app.local_environment.native_unknown_release() is None


def test_live_actor_process_rejects_even_with_a_known_native_close(
    native_app, monkeypatch, absent_os,
):
    app, *_ = native_app
    expected = interrupted(app, monkeypatch)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    app.models.process = process
    try:
        with pytest.raises(BoundaryError, match="stop_runtime_before_install"):
            app.seal_native_unknown(expected)
        assert app.local_environment.native_unknown_release() is None
    finally:
        process.terminate()
        process.wait(timeout=5)
        app.models.process = None


def test_seal_write_failure_keeps_admission_blocked(native_app, monkeypatch, absent_os):
    app, *_ = native_app
    expected = interrupted(app, monkeypatch)
    put = LocalBlobStore.put_if_absent

    def fail(store, key, raw):
        if key.endswith("unknown-seal.json"):
            raise StoreError("test_seal_write_failed")
        return put(store, key, raw)

    monkeypatch.setattr(LocalBlobStore, "put_if_absent", fail)
    with pytest.raises(StoreError, match="test_seal_write_failed"):
        app.seal_native_unknown(expected)
    assert app.local_environment.native_unknown_release() is None
    with pytest.raises(BoundaryError, match="native_resource_reserved"):
        app.prepare_native_episode(prepare_body("b" * 32))


@pytest.mark.parametrize("executable", ["SlayTheSpire2", "Slay the Spire 2",
    "/Applications/Slay the Spire 2.app/Contents/MacOS/Slay the Spire 2"])
def test_game_census_and_failed_handle_observation_reject(tmp_path, monkeypatch, executable):
    import json

    monkeypatch.setattr(native_unknown.subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        returncode=0, stdout=json.dumps(["123 " + executable]), stderr=""))
    with pytest.raises(BoundaryError, match="game_still_present"):
        native_unknown.unoccupied({"host_package_directory": "/pinned-host"}, tmp_path)
    monkeypatch.setattr(native_unknown.subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        returncode=1, stdout=b"", stderr=b"permission denied"))
    with pytest.raises(BoundaryError, match="occupancy_unproved"):
        native_unknown.no_handles(tmp_path)


def test_release_preview_and_command_use_the_authenticated_original_application(
    native_app, monkeypatch, absent_os,
):
    app, *_ = native_app
    expected = interrupted(app, monkeypatch)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr("spireagent.workbench.local_model_cli.running", lambda _config: {
        "instance_id": app.instance_id, "port": server.server_port,
        "control_token": app.control_token})
    try:
        with pytest.raises(HTTPError) as unauthorized:
            urlopen(address + "/api/local-environment/native/unknown-release", timeout=3)
        assert unauthorized.value.code == 401
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(address + "/api/local-environment/native/seal-unknown", data=b"{}"),
                    timeout=3)
        assert denied.value.code == 403
        preview = native_episode_command(app.config, "unknown-status", timeout_seconds=5)
        assert preview["expected"] == expected and preview["release"] is None
        receipt = native_episode_command(
            app.config, "seal-unknown", expected_release=expected,
            expected_instance_id=app.instance_id, timeout_seconds=5)
        assert receipt["released"] is True and receipt["result"] == "unknown"
        assert app.native_episode_status()["phase"] == "recovery_required"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("stage", ["unknown-seal.json", "confirmations/"])
def test_publication_then_error_never_activates_release(
    native_app, monkeypatch, absent_os, stage,
):
    app, *_ = native_app
    expected = interrupted(app, monkeypatch)
    put = LocalBlobStore.put_if_absent

    def publish_then_fail(store, key, raw):
        result = put(store, key, raw)
        if stage in key:
            raise StoreError("test_error_after_publication")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(LocalBlobStore, "put_if_absent", publish_then_fail)
        with pytest.raises(StoreError, match="after_publication"):
            app.seal_native_unknown(expected)
    assert app.local_environment.native_unknown_release() is None
    with pytest.raises(BoundaryError, match="native_resource_reserved"):
        app.prepare_native_episode(prepare_body("b" * 32))
    # Explicit confirmation may preserve/read the failed candidate, then prove
    # absence again. No game, Stop or publication is retried by a status read.
    assert app.seal_native_unknown(expected)["released"] is True


def test_new_application_requires_explicit_confirmation_of_preserved_seal(
    native_app, monkeypatch, absent_os,
):
    from spireagent.workbench.local_environment import LocalEnvironmentService

    app, *_ = native_app
    expected = interrupted(app, monkeypatch)
    app.seal_native_unknown(expected)
    reopened = LocalEnvironmentService(app.config)
    reopened.seed_normalizer = app.local_environment.seed_normalizer
    reopened.bind_native_application(
        lock=app.models.lock, application_instance_id="c" * 32,
        configuration_id=app.native_episode_configuration,
        admit_prepare=app._admit_native_preparation, actor_lost=lambda _context: None)
    assert reopened.native_unknown_preview()["preserved_unknown"] is not None
    assert reopened.native_unknown_release() is None
    with pytest.raises(BoundaryError, match="native_resource_reserved"):
        reopened.prepare_native_episode(prepare_body("b" * 32))
    preview = reopened.native_unknown_preview()
    assert reopened.seal_native_unknown(preview["expected"])["released"] is True


def test_evidence_phase_unknown_can_release_without_rewriting_a_closed_environment(
    native_app, monkeypatch, absent_os,
):
    app, *_ = native_app
    ready = prepared(app)
    service = app.local_environment
    service.native_record["actor"] = {"run_id": "run-" + "b" * 36}
    service._save_native()
    record, client, first = service.begin_native_finish(ready["environment_ref"])
    assert first
    closed = service.close_native_after_actor(record, client, {"status": "stopped"})
    assert closed["phase"] == "closed"
    original = (app.config.state_dir / RESOURCE_FILE).read_bytes()
    expected = service.native_unknown_preview()["expected"]
    assert app.seal_native_unknown(expected)["result"] == "unknown"
    assert app.native_episode_status()["phase"] == "closed"
    assert (app.config.state_dir / RESOURCE_FILE).read_bytes() == original
