"""Synthetic verified recording through the exact local B v2 training owner."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import test_local_recording_preview as recording_fixture

from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench import local_dataset as dataset_module
from spireagent.workbench.developer import LocalResearchWorkspaceConfig, ProjectConfig, combination
from spireagent.workbench.developer_server import Application, create_server
from spireagent.workbench.inplace_curation import InplaceCurationPreparation, configured_owner
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_training import OPERATION_FILE, LocalTrainingService


def _ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, runs: int = 3):
    original_bundle = recording_fixture.bundle3
    monkeypatch.setattr(recording_fixture, "bundle3", lambda path, **_kw:
                        original_bundle(path, runs=runs, public_bindings=True))
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
    assert model.parameters.value()["serializer"] == "compact-v2"
    assert model.parameters.value()["config"]["recipe"] == "stage1a.b.s.v2"
    assert model.parameters.value()["config"]["steps"] == 3
    assert model.parameters.value()["config"]["dropout"] == 0.0
    assert model.parameters.value()["config"]["max_tokens"] == 16384
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
    with pytest.raises(BoundaryError, match="operation_in_progress"):
        contender.start("a" * 64)
    release.set()
    assert service._thread is not None
    service._thread.join(timeout=10)
    assert contender.status()["operation"]["status"] == "interrupted_unknown"
    with pytest.raises(BoundaryError, match="previous_training_outcome_unknown"):
        contender.start("a" * 64)


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
