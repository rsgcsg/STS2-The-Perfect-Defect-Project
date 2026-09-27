"""Synthetic verified recording through the exact local B v2 training owner."""

from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import test_local_recording_preview as recording_fixture

from spireagent.json_boundary import BoundaryError
from spireagent.source import source_identity
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench import local_dataset as dataset_module
from spireagent.workbench import local_training as training_module
from spireagent.workbench.developer import (
    ROOT,
    LocalResearchWorkspaceConfig,
    ProjectConfig,
    combination,
)
from spireagent.workbench.developer_server import (
    Application,
    configuration_id,
    create_server,
)
from spireagent.workbench.inplace_curation import InplaceCurationPreparation, configured_owner
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_training import OPERATION_FILE, LocalTrainingService


def _ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, runs: int = 3,
           public_bindings: bool = True):
    original_bundle = recording_fixture.bundle3
    monkeypatch.setattr(recording_fixture, "bundle3", lambda path, **_kw:
                        original_bundle(path, runs=runs, public_bindings=public_bindings))
    _, source, store = recording_fixture._fixture(tmp_path / "library", canonical=True)
    state = tmp_path / "profile"
    state.mkdir()
    config = ProjectConfig(state, "", "", None, combination(),
                           LocalResearchWorkspaceConfig(tmp_path / "library/store",
                                                        tmp_path / "library/registry.sqlite"))
    preparation = InplaceCurationPreparation(config)
    preparation.start()
    assert preparation.thread is not None
    preparation.thread.join(timeout=30)
    assert preparation.status()["status"] == "ready", preparation.status()
    producer = store.get_manifest(source).producer
    monkeypatch.setattr(dataset_module, "source_identity", lambda _: producer)
    datasets = LocalDatasetService(config)
    datasets.start_preview(source, "training", None)
    assert datasets.thread is not None
    datasets.thread.join(timeout=30)
    ready = datasets.status()["operation"]
    assert ready["status"] == "preview_ready", ready
    datasets.start_publish(ready["preview_id"])
    assert datasets.thread is not None
    datasets.thread.join(timeout=30)
    published = datasets.status()["operation"]
    assert published["status"] == "completed", published
    return config, published["result_artifact_id"], source, store


def _settle(service: LocalTrainingService, timeout: float = 180) -> dict:
    assert service._thread is not None
    service._thread.join(timeout=timeout)
    assert not service._thread.is_alive()
    return service.status()["operation"]


def test_exact_synthetic_public_bc_small_b_subprocess(tmp_path: Path, monkeypatch) -> None:
    config, dataset_id, source, store = _ready(tmp_path, monkeypatch)
    owner = configured_owner(config)
    service = LocalTrainingService(config)
    before = tuple(store.manifest_ids())
    assert service.status()["operation"] == {"status": "idle"}
    assert not (owner.path.parent / OPERATION_FILE).exists()
    assert tuple(store.manifest_ids()) == before
    started = service.start(dataset_id)["operation"]
    assert started["status"] == "pending"
    completed = _settle(service)
    assert completed["status"] == "completed", completed
    assert set(("allocation_id", "view_id", "input_id", "run_id", "checkpoint_id",
                "result_id", "model_id", "evaluation_id")) <= set(completed)
    result = store.get_manifest(completed["result_id"])
    model = store.get_manifest(completed["model_id"])
    report = store.get_manifest(completed["evaluation_id"])
    assert result.parent("run") == completed["run_id"]
    assert result.parent("model") == model.artifact_id
    assert result.parent("offline_evaluation") == report.artifact_id
    assert report.parameters.value()["partition"] == "dev"
    assert model.parameters.value()["serializer"] == {
        "profile": "public_compact", "status": "provisional",
        "version": "stpd-public-snapshot-compact-v2",
    }
    assert model.parameters.value()["config"]["recipe"] == "stage1a.b.s.v2"
    assert model.parameters.value()["config"]["steps"] == 3
    assert model.parameters.value()["config"]["dropout"] == 0.0
    assert model.parameters.value()["config"]["max_tokens"] == 16384
    assert {key: model.parameters.value()["config"][key] for key in
            ("width", "layers", "heads", "feedforward", "device")} == {
                "width": 48, "layers": 1, "heads": 2, "feedforward": 96, "device": "cpu",
            }
    assert store.get_manifest(completed["run_id"]).parameters.value()["cpu_threads"] == 2
    durable = json.loads((owner.path.parent / OPERATION_FILE).read_bytes())
    assert durable["_exit_code"] == 0
    registry = SQLiteRegistry(config.research_workspace.registry_path, readonly=True)
    assert registry.get(model.artifact_id).to_bytes() == model.to_bytes()
    assert registry.get(report.artifact_id).to_bytes() == report.to_bytes()
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_uses WHERE reference=?",
                          (completed["operation_id"],)).fetchone()[0] >= 3
        assert db.execute("SELECT source FROM curation_source_uses WHERE reference=?",
                          (completed["operation_id"],)).fetchone() == (source,)
    assert service.start(dataset_id)["operation"]["operation_id"] == completed["operation_id"]
    assert LocalTrainingService(config).status()["operation"] == completed


def test_insufficient_independent_components_never_reserves_use(
    tmp_path: Path, monkeypatch,
) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch, runs=1)
    service = LocalTrainingService(config)
    service.start(dataset_id)
    result = _settle(service)
    assert result["status"] == "failed"
    assert result["error_code"] == "insufficient_independent_components"
    assert "run_id" not in result
    owner = configured_owner(config)
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_uses").fetchone() == (0,)


def test_missing_public_h_binding_never_falls_back_to_native_input(
    tmp_path: Path, monkeypatch,
) -> None:
    config, dataset_id, source, _ = _ready(tmp_path, monkeypatch, public_bindings=False)
    service = LocalTrainingService(config)
    service.start(dataset_id)
    result = _settle(service)
    assert result["status"] == "failed", result
    assert result["error_code"] == "nonempty_train_dev_required"
    assert "run_id" not in result
    owner = configured_owner(config)
    with owner.transaction() as db:
        assert db.execute("SELECT source FROM curation_source_uses WHERE reference=?",
                          (result["operation_id"],)).fetchone() == (source,)


def test_one_slot_across_profiles_and_unknown_restart(tmp_path: Path, monkeypatch) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    second_state = tmp_path / "other-profile"
    second_state.mkdir()
    other = ProjectConfig(second_state, "", "", None, combination(), config.research_workspace)
    service = LocalTrainingService(config)
    entered, release = threading.Event(), threading.Event()

    def held(_lock, _path, _identity, _owner, _store):
        entered.set()
        assert release.wait(10)
        _lock.__exit__(None, None, None)

    monkeypatch.setattr(service, "_run", held)
    service.start(dataset_id)
    assert entered.wait(5)
    contender = LocalTrainingService(other)
    assert contender.status()["operation"]["status"] == "pending"
    same = contender.start(dataset_id)["operation"]
    assert same["operation_id"] == service.status()["operation"]["operation_id"]
    with pytest.raises(BoundaryError, match="operation_in_progress"):
        contender.start("a" * 64)
    release.set()
    assert service._thread is not None
    service._thread.join(timeout=10)
    assert contender.status()["operation"]["status"] == "interrupted_unknown"
    with pytest.raises(BoundaryError, match="previous_training_outcome_unknown"):
        contender.start("a" * 64)


def test_nonzero_child_keeps_run_and_private_exit_for_diagnosis(
    tmp_path: Path, monkeypatch,
) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    producer = source_identity(ROOT)

    class FailedChild:
        stdout = io.BytesIO(b"synthetic private failure\n")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def wait(self):
            return 7

    launches = []

    def fail_child(command, **_kwargs):
        launches.append(command)
        return FailedChild()

    monkeypatch.setattr(training_module.subprocess, "Popen", fail_child)
    monkeypatch.setattr(training_module, "source_identity", lambda _root: producer)
    service = LocalTrainingService(config)
    service.start(dataset_id)
    result = _settle(service)
    assert len(launches) == 1
    assert result["status"] == "interrupted_unknown", result
    assert result["error_code"] == "training_process_failed"
    assert "run_id" in result and "result_id" not in result
    owner = configured_owner(config)
    operation = json.loads((owner.path.parent / OPERATION_FILE).read_bytes())
    assert operation["_exit_code"] == 7
    log = owner.path.parent / ("local-training-" + result["operation_id"] + ".log")
    assert log.read_bytes() == b"synthetic private failure\n"
    assert "synthetic private failure" not in json.dumps(service.status())
    with pytest.raises(BoundaryError, match="previous_training_outcome_unknown"):
        LocalTrainingService(config).start(dataset_id)


def test_invalid_producer_operation_or_owner_never_starts_child(
    tmp_path: Path, monkeypatch,
) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    launched = []
    monkeypatch.setattr(training_module.subprocess, "Popen",
                        lambda *_args, **_kwargs: launched.append(1))
    monkeypatch.setattr(training_module, "source_identity",
                        lambda _root: (_ for _ in ()).throw(
                            BoundaryError("source", "clean_checkout_required")))
    service = LocalTrainingService(config)
    service.start(dataset_id)
    result = _settle(service)
    assert result["status"] == "failed"
    assert result["error_code"] == "clean_checkout_required"
    assert launched == []
    owner = configured_owner(config)
    operation_path = owner.path.parent / OPERATION_FILE
    operation_path.write_bytes(b"{broken")
    assert service.status()["availability"] == "recovery_required"
    with pytest.raises(BoundaryError, match="operation_recovery_required"):
        service.start(dataset_id)
    assert launched == []
    operation_path.unlink()
    owner.path.unlink()
    assert service.status()["availability"] == "recovery_required"
    with pytest.raises(BoundaryError, match="ledger_recovery_required"):
        service.start(dataset_id)
    assert launched == []


def test_gold_or_test_claim_never_admitted(tmp_path: Path, monkeypatch) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    owner = configured_owner(config)
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    original = store.get_manifest(dataset_id)
    # A forged manifest ID cannot borrow the real training claim.
    from spireagent.artifact_contracts import Manifest
    from spireagent.json_boundary import FrozenObject

    forged = Manifest("dataset", original.producer, original.parents, original.payloads,
                      FrozenObject.of({**original.parameters.value(), "purpose": "gold"}))
    store.publish(forged)
    service = LocalTrainingService(config)
    service.start(forged.artifact_id)
    result = _settle(service)
    assert result["status"] == "failed"
    assert result["error_code"] == "curated_training_dataset_required"
    assert "run_id" not in result


def test_http_readonly_status_and_exact_browser_write(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    app = Application(ProjectConfig(state, "", "", None, combination()))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    cookie = f"{app.account.cookie_name}={app.account.cookie}"
    try:
        with pytest.raises(HTTPError) as absent:
            urlopen(url + "/api/local-training/status", timeout=3)
        assert absent.value.code == 401
        before = tuple(state.iterdir())
        with urlopen(Request(url + "/api/local-training/status",
                             headers={"Cookie": cookie}), timeout=3) as response:
            status = json.load(response)
        assert status["schema"] == "stpd/local-training-operation-v1"
        assert status["availability"] == "workspace_required"
        assert status["csrf_token"] == app.account.csrf
        assert tuple(state.iterdir()) == before
        body = json.dumps({"dataset_id": "a" * 64}).encode()
        headers = {"Cookie": cookie, "Content-Type": "application/json"}
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(url + "/api/local-training/start", data=body,
                            headers=headers), timeout=3)
        assert denied.value.code == 403
        headers.update({"Origin": url, "X-CSRF-Token": app.account.csrf})
        with pytest.raises(HTTPError) as unavailable:
            urlopen(Request(url + "/api/local-training/start", data=body,
                            headers=headers), timeout=3)
        assert unavailable.value.code == 409
        assert json.load(unavailable.value)["error"] == "running_instance_unavailable"
        with pytest.raises(HTTPError) as unknown:
            urlopen(Request(url + "/api/local-training/start",
                            data=json.dumps({"dataset_id": "a" * 64, "extra": 1}).encode(),
                            headers=headers), timeout=3)
        assert unknown.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        app.close()


def test_http_explicit_start_tracks_exact_completed_run_without_get_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, dataset_id, _, store = _ready(tmp_path, monkeypatch)
    config_path = tmp_path / "project.json"
    config_path.write_text(json.dumps(config.to_dict()))
    app = Application(config, config_path=config_path)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    cookie = f"{app.account.cookie_name}={app.account.cookie}"
    (config.state_dir / "runtime.json").write_text(json.dumps({
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
        "port": server.server_port,
    }))

    def status() -> dict:
        with urlopen(Request(url + "/api/local-training/status",
                             headers={"Cookie": cookie}), timeout=5) as response:
            return json.load(response)

    try:
        owner = configured_owner(config)
        operation_path = owner.path.parent / OPERATION_FILE
        before = tuple(store.manifest_ids())
        initial = status()
        assert initial["schema"] == "stpd/local-training-operation-v1"
        assert initial["operation"]["status"] == "idle"
        assert not operation_path.exists()
        assert tuple(store.manifest_ids()) == before
        body = json.dumps({"dataset_id": dataset_id}).encode()
        headers = {"Cookie": cookie, "Content-Type": "application/json",
                   "Origin": url, "X-CSRF-Token": initial["csrf_token"]}
        with urlopen(Request(url + "/api/local-training/start", data=body,
                             headers=headers), timeout=5) as response:
            started = json.load(response)
        assert started["availability"] == "ready"
        assert started["operation"]["dataset_id"] == dataset_id
        operation_id = started["operation"]["operation_id"]
        deadline = time.monotonic() + 45
        observed = []
        while time.monotonic() < deadline:
            current = status()["operation"]
            observed.append(current["status"])
            if current["status"] != "pending":
                break
            time.sleep(0.05)
        assert current["status"] == "completed", current
        assert current["operation_id"] == operation_id
        result = store.get_manifest(current["result_id"])
        assert result.parent("run") == current["run_id"]
        assert result.parent("model") == current["model_id"]
        assert result.parent("offline_evaluation") == current["evaluation_id"]
        assert "pending" in observed or observed == ["completed"]
        manifest_ids = tuple(store.manifest_ids())
        second = status()["operation"]
        assert second == current
        assert tuple(store.manifest_ids()) == manifest_ids
        public = json.dumps({"start": started, "completed": second})
        assert app.control_token not in public and app.account.cookie not in public
        assert str(owner.store_dir) not in public
        assert "_owner" not in public and "_exit_code" not in public
    finally:
        server.shutdown()
        server.server_close()
        app.close()
