"""Synthetic local Human-input source uses the existing store and curation owner."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import test_local_recording_preview as recording_fixture
from platform_bundle3_fixture import bundle3 as original_bundle3
from test_local_recording_preview import _fixture
from test_text_menu_data import snapshot

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import LocalResearchWorkspaceConfig, ProjectConfig, combination
from spireagent.workbench.developer_server import Application, configuration_id, create_server
from spireagent.workbench.inplace_curation import InplaceCurationPreparation, configured_owner
from spireagent.workbench.local_curation import LocalLedger
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_training import LocalTrainingService
from stpd.fullrun.text_menu_human_import import SOURCE_SCHEMA, load_human_text_source


def _prepared(tmp_path: Path) -> tuple[LocalDatasetService, str]:
    _, source, _ = _fixture(tmp_path / "library")
    state = tmp_path / "state"
    state.mkdir()
    config = ProjectConfig(state, "", "", None, combination(),
                           LocalResearchWorkspaceConfig(tmp_path / "library/store",
                                                        tmp_path / "library/registry.sqlite"))
    preparation = InplaceCurationPreparation(config)
    preparation.start()
    assert preparation.thread is not None
    preparation.thread.join(timeout=30)
    assert preparation.status()["status"] == "ready", preparation.status()
    return LocalDatasetService(config), source


def _settle(service: LocalDatasetService) -> dict:
    assert service.thread is not None
    service.thread.join(timeout=30)
    assert not service.thread.is_alive()
    return service.status()["operation"]


def _fresh_source(tmp_path: Path, monkeypatch, store: ManifestArtifactStore,
                  owner, name: str) -> str:
    monkeypatch.setattr(recording_fixture, "bundle3", lambda path, **kwargs:
                        original_bundle3(path, **{**kwargs, "session_id": "session-" + name}))
    monkeypatch.setattr(recording_fixture, "snapshot", lambda _: snapshot("page-" + name))
    _, candidate, other = _fixture(tmp_path / name)
    copied = other.get_manifest(candidate)
    payloads = tuple(store.put_bytes(payload.role,
                                     b"".join(other.read_payload(payload)),
                                     payload.media_type)
                     for payload in copied.payloads)
    candidate_id = (name[0] * 64)[:64]
    parameters = {**copied.parameters.value(), "candidate_id": candidate_id}
    source = store.publish(Manifest("evidence", copied.producer, payloads=payloads,
                                    parameters=FrozenObject.of(parameters)))
    owner.begin_source(candidate_id)
    owner.published_source(candidate_id, source)
    return source


def test_old_zero_canonical_human_source_saves_but_does_not_train(tmp_path: Path,
                                                                   monkeypatch) -> None:
    service, source = _prepared(tmp_path)
    owner = configured_owner(service.config)
    # The exact value comes from the typed bundle; no canonical occurrence was invented.
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    monkeypatch.setattr("spireagent.workbench.local_dataset.source_identity",
                        lambda _: store.get_manifest(source).producer)
    typed_runs = owner._human_runs(store, source)
    assert len(typed_runs) == 1
    assert owner.ledger.source_runs(source) == typed_runs
    with owner.transaction() as db:
        assert db.execute("SELECT 1 FROM local_legacy_unknown_runs WHERE run=?",
                          (next(iter(typed_runs)),)).fetchone()
        assert db.execute("SELECT 1 FROM curation_source_decisions WHERE source=?",
                          (source,)).fetchone() is None
    before = service.status()
    assert before["operation"]["status"] == "idle"
    assert not service.path.exists()
    service.start_human_preview([source])
    preview = _settle(service)
    assert preview["status"] == "preview_ready", preview
    assert preview["can_publish"] and preview["accepted_labels"] == 1
    assert preview["split_status"] == "not_checked_for_training"
    service.start_publish(preview["preview_id"])
    result = _settle(service)
    assert result["status"] == "completed", result.get("error_code")
    manifest, rows = load_human_text_source(store, result["result_artifact_id"])
    assert manifest.parameters.value()["schema"] == SOURCE_SCHEMA
    assert len(rows) == 2
    assert owner.ledger.dataset(manifest.artifact_id) == ("training", typed_runs)
    assert service.binding(manifest.artifact_id)["curation_purpose"] == "training"
    monkeypatch.setattr("spireagent.workbench.local_training.source_identity",
                        lambda _: manifest.producer)
    training = LocalTrainingService(service.config)
    training.start(manifest.artifact_id)
    assert training._thread is not None
    training._thread.join(timeout=30)
    failed = training.status()["operation"]
    assert failed["status"] == "failed", failed
    assert failed["error_code"] == "independent_groups_required"
    assert "run_id" not in failed


def test_gold_cannot_ignore_unindexed_human_run(tmp_path: Path) -> None:
    service, source = _prepared(tmp_path)
    owner = configured_owner(service.config)
    with owner.transaction() as db:
        db.execute("DELETE FROM curation_source_runs WHERE source=?", (source,))
        assert owner._inventory_pending(db)
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    with pytest.raises(BoundaryError, match="gold_source_inventory_pending"):
        owner.ledger.claim("probe-gold", "gold", owner._human_runs(store, source),
                           require_inventory=True)


def test_new_pending_human_run_is_indexed_without_legacy_unknown(tmp_path: Path,
                                                                  monkeypatch) -> None:
    service, old = _prepared(tmp_path)
    owner = configured_owner(service.config)
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    source = _fresh_source(tmp_path, monkeypatch, store, owner, "b")
    new_candidate = "b" * 64
    assert source != old
    service.start_human_preview([source])
    preview = _settle(service)
    assert preview["status"] == "preview_ready", preview
    typed_runs = owner._human_runs(store, source)
    assert owner.ledger.source_runs(source) == typed_runs
    with owner.transaction() as db:
        assert db.execute("SELECT 1 FROM local_source_pending WHERE candidate=?",
                          (new_candidate,)).fetchone() is None
        assert all(db.execute("SELECT 1 FROM local_legacy_unknown_runs WHERE run=?",
                              (run,)).fetchone() is None for run in typed_runs)


def test_canonical_and_human_projections_share_native_run_key(tmp_path: Path) -> None:
    _, source, _ = _fixture(tmp_path / "library", canonical=True)
    state = tmp_path / "state"
    state.mkdir()
    config = ProjectConfig(state, "", "", None, combination(),
                           LocalResearchWorkspaceConfig(tmp_path / "library/store",
                                                        tmp_path / "library/registry.sqlite"))
    preparation = InplaceCurationPreparation(config)
    preparation.start()
    assert preparation.thread is not None
    preparation.thread.join(timeout=30)
    assert preparation.status()["status"] == "ready"
    owner = configured_owner(config)
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    typed_runs = owner._human_runs(store, source)
    assert owner.ledger.source_runs(source) == typed_runs
    with owner.transaction() as db:
        canonical = {row[0] for row in db.execute(
            "SELECT DISTINCT run FROM curation_occurrences WHERE id IN "
            "(SELECT occurrence FROM curation_source_decisions WHERE source=?)", (source,))}
        assert canonical == typed_runs


def test_multiple_human_sources_publish_training_bound_source(tmp_path: Path,
                                                              monkeypatch) -> None:
    service, _ = _prepared(tmp_path)
    owner = configured_owner(service.config)
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    left = _fresh_source(tmp_path, monkeypatch, store, owner, "b")
    right = _fresh_source(tmp_path, monkeypatch, store, owner, "c")
    monkeypatch.setattr("spireagent.workbench.local_dataset.source_identity",
                        lambda _: store.get_manifest(left).producer)
    service.start_human_preview([left, right])
    preview = _settle(service)
    assert preview["status"] == "preview_ready", preview
    assert preview["selected"] == 2 and preview["can_publish"]
    service.start_publish(preview["preview_id"])
    published = _settle(service)
    assert published["status"] == "completed", published.get("error_code")
    source_id = published["result_artifact_id"]
    source, rows = load_human_text_source(store, source_id)
    assert [parent.artifact_id for parent in source.parents] == [left, right]
    assert len(rows) == 4
    runs = owner._human_runs(store, left) | owner._human_runs(store, right)
    assert owner.ledger.dataset(source_id) == ("training", runs)
    assert service.binding(source_id) == {
        "schema": "stpd/local-dataset-binding-v1", "artifact_id": source_id,
        "sample_type": "human_input", "curation_purpose": "training",
    }


def test_human_source_trains_real_three_step_worker(tmp_path: Path, monkeypatch) -> None:
    service, _ = _prepared(tmp_path)
    owner = configured_owner(service.config)
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    sources = [_fresh_source(tmp_path, monkeypatch, store, owner, name) for name in ("b", "c")]
    monkeypatch.setattr("spireagent.workbench.local_dataset.source_identity",
                        lambda _: store.get_manifest(sources[0]).producer)
    service.start_human_preview(sources)
    preview = _settle(service)
    assert preview["can_publish"]
    service.start_publish(preview["preview_id"])
    published = _settle(service)
    assert published["status"] == "completed", published.get("error_code")
    dataset_id = published["result_artifact_id"]
    training = LocalTrainingService(service.config)
    assert training.status()["operation"]["status"] == "idle"
    training.start(dataset_id)
    assert training._thread is not None
    training._thread.join(timeout=120)
    assert not training._thread.is_alive()
    result = training.status()["operation"]
    assert result["status"] == "completed", result.get("error_code")
    assert "allocation_id" not in result
    assert store.get_manifest(result["view_id"]).parameters.value()["schema"] == (
        "stpd/human-text-input-bc-view-v2")
    assert store.get_manifest(result["evaluation_id"]).parameters.value()["partition"] == "dev"
    runs = owner._human_runs(store, sources[0]) | owner._human_runs(store, sources[1])
    with owner.transaction() as db:
        assert {row[0] for row in db.execute(
            "SELECT run FROM curation_uses WHERE kind='training' AND reference=?",
            (result["operation_id"],))} == runs
        assert {row[0] for row in db.execute(
            "SELECT source FROM curation_source_uses WHERE kind='training' AND reference=?",
            (result["operation_id"],))} == set(sources)


def test_human_http_preview_publish_binding_are_explicit_and_cookie_bound(
    tmp_path: Path, monkeypatch,
) -> None:
    service, source = _prepared(tmp_path)
    owner = configured_owner(service.config)
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    monkeypatch.setattr("spireagent.workbench.local_dataset.source_identity",
                        lambda _: store.get_manifest(source).producer)
    config_path = tmp_path / "project.json"
    config_path.write_text(json.dumps(service.config.to_dict()))
    app = Application(service.config, config_path=config_path)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    cookie = f"{app.account.cookie_name}={app.account.cookie}"
    (service.config.state_dir / "runtime.json").write_text(json.dumps({
        "instance_id": app.instance_id, "configuration_id": configuration_id(service.config),
        "port": server.server_port,
    }))

    def get(route: str, *, authenticated: bool = True) -> dict:
        headers = {"Cookie": cookie} if authenticated else {}
        with urlopen(Request(url + route, headers=headers), timeout=5) as response:
            return json.load(response)

    def post(route: str, body: dict, token: str) -> dict:
        headers = {"Cookie": cookie, "Content-Type": "application/json",
                   "Origin": url, "X-CSRF-Token": token}
        with urlopen(Request(url + route, data=json.dumps(body).encode(),
                             headers=headers), timeout=5) as response:
            return json.load(response)

    try:
        before = tuple(store.manifest_ids())
        status = get("/api/local-datasets/status")
        assert status["operation"]["status"] == "idle"
        assert not app.local_datasets.path.exists()
        assert tuple(store.manifest_ids()) == before
        with pytest.raises(HTTPError) as unauthorized:
            get("/api/local-datasets/binding/" + source, authenticated=False)
        assert unauthorized.value.code == 401
        with pytest.raises(HTTPError) as denied:
            post("/api/local-datasets/human-preview", {"artifact_ids": [source]}, "bad")
        assert denied.value.code == 403
        with pytest.raises(HTTPError) as malformed:
            post("/api/local-datasets/human-preview",
                 {"artifact_ids": [source], "purpose": "gold"}, status["csrf_token"])
        assert malformed.value.code == 400
        post("/api/local-datasets/human-preview", {"artifact_ids": [source]},
             status["csrf_token"])
        assert app.local_datasets.thread is not None
        app.local_datasets.thread.join(timeout=30)
        operation = get("/api/local-datasets/status")["operation"]
        assert operation["status"] == "preview_ready", operation
        assert operation["kind"] == "human_input"
        post("/api/local-datasets/publish", {"preview_id": operation["preview_id"]},
             status["csrf_token"])
        assert app.local_datasets.thread is not None
        app.local_datasets.thread.join(timeout=30)
        completed = get("/api/local-datasets/status")["operation"]
        assert completed["status"] == "completed", completed.get("error_code")
        binding = get("/api/local-datasets/binding/" + completed["result_artifact_id"])
        assert binding["curation_purpose"] == "training"
        assert "training_ready" not in binding
        assert get("/api/local-datasets/status")["operation"]["id"] == operation["id"]
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=5)


def test_human_publish_recovers_exact_unbound_source_without_republishing(
    tmp_path: Path, monkeypatch,
) -> None:
    service, source = _prepared(tmp_path)
    owner = configured_owner(service.config)
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    monkeypatch.setattr("spireagent.workbench.local_dataset.source_identity",
                        lambda _: store.get_manifest(source).producer)
    service.start_human_preview([source])
    preview = _settle(service)
    original_bind = LocalLedger.bind
    calls = 0

    def fault_once(self, identity, artifact):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic bind interruption")
        return original_bind(self, identity, artifact)

    monkeypatch.setattr(LocalLedger, "bind", fault_once)
    service.start_publish(preview["preview_id"])
    failed = _settle(service)
    assert failed["status"] == "failed" and failed["error_code"] == "publish_failed"
    published = [item for item in store.manifest_ids()
                 if store.get_manifest(item).parameters.value().get("schema") == SOURCE_SCHEMA]
    assert len(published) == 1
    with pytest.raises(BoundaryError, match="publication_recovery_required"):
        service.start_human_preview([source])
    service.start_publish(preview["preview_id"])
    completed = _settle(service)
    assert completed["status"] == "completed", completed.get("error_code")
    assert completed["result_artifact_id"] == published[0]
    assert owner.ledger.dataset(published[0]) == ("training", owner._human_runs(store, source))
