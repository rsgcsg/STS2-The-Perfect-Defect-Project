"""Synthetic local Dataset preview and publication through the real shared ledger."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from test_local_recording_preview import _fixture

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from spireagent.workbench import local_dataset as dataset_module
from spireagent.workbench import managed_local_workspace as managed
from spireagent.workbench.developer import LocalResearchWorkspaceConfig, ProjectConfig, combination
from spireagent.workbench.local_curation import LocalLedger
from spireagent.workbench.local_dataset import LocalDatasetService
from stpd.fullrun.curated_dataset import load_selection

PRODUCER = Producer("local/synthetic-fixture", "a" * 40, "b" * 64)


def setup(tmp_path: Path, monkeypatch) -> tuple[LocalDatasetService, str]:
    _, artifact, source_store = _fixture(tmp_path / "source", canonical=True)
    state = tmp_path / "state"
    state.mkdir()
    selected = managed.create_managed_workspace(state)
    destination = state / managed.ROOT_NAME / selected["workspace_id"] / "store"
    store = ManifestArtifactStore(LocalBlobStore(destination, create=False))
    copy_artifact(source_store, store, artifact)
    owner = selected["curation_owner"]
    candidate = store.get_manifest(artifact).parameters.value()["candidate_id"]
    owner.begin_source(candidate)
    owner.published_source(candidate, artifact)
    monkeypatch.setattr("spireagent.workbench.local_dataset.source_identity", lambda _: PRODUCER)
    return LocalDatasetService(ProjectConfig(state, "", "", None, combination())), artifact


def settled(service: LocalDatasetService) -> dict:
    assert service.thread is not None
    service.thread.join(timeout=15)
    assert not service.thread.is_alive()
    return service.status()["operation"]


def test_preview_publish_reload_and_repreview(tmp_path: Path, monkeypatch) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    before = service.status()
    assert before["availability"] == "ready"
    assert before["operation"]["status"] == "idle"
    assert not service.path.exists()
    owner_before = managed.inspect_managed_workspace(service.config.state_dir)["curation_owner"]
    ledger_bytes = owner_before.path.read_bytes()
    assert service.status()["operation"]["status"] == "idle"
    assert owner_before.path.read_bytes() == ledger_bytes
    started = service.start_preview(artifact, "training", None)
    assert started["operation"]["status"] in {"pending", "preview_ready"}
    preview = settled(service)
    assert preview["status"] == "preview_ready", preview
    assert preview["selected"] == 2
    assert preview["can_publish"] is True
    assert service.start_publish(preview["preview_id"])["operation"]["status"] in {
        "pending", "completed",
    }
    published = settled(service)
    assert published["status"] == "completed", published
    state = service.config.state_dir
    owner = managed.inspect_managed_workspace(state)["curation_owner"]
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False, readonly=True))
    manifest = store.get_manifest(published["result_artifact_id"])
    selected = load_selection(store, manifest, cache=None)
    assert len(selected.records) == 2
    assert owner.ledger.dataset(manifest.artifact_id) == ("training", selected.run_ids)
    repeat = service.start_publish(preview["preview_id"])["operation"]
    assert repeat["result_artifact_id"] == manifest.artifact_id
    refreshed = service.start_preview(artifact, "training", None)["operation"]
    assert refreshed["id"] != preview["id"]
    assert settled(service)["preview_id"] != preview["preview_id"]
    with pytest.raises(BoundaryError, match="preview_changed"):
        service.start_publish(preview["preview_id"])


def test_gold_pending_and_existing_training_claim(tmp_path: Path, monkeypatch) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    service.start_preview(artifact, "gold", None)
    gold = settled(service)
    assert gold["status"] == "preview_ready", gold
    assert gold["can_publish"] is True
    service.start_preview(artifact, "training", None)
    training = settled(service)
    service.start_publish(training["preview_id"])
    assert settled(service)["status"] == "completed"
    service.start_preview(artifact, "gold", None)
    later = settled(service)
    assert later["status"] == "preview_ready"
    assert later["can_publish"] is False
    assert later["error_code"] == "gold_already_in_other_dataset"
    with pytest.raises(BoundaryError, match="preview_cannot_publish"):
        service.start_publish(later["preview_id"])


def test_gold_preview_names_other_unindexed_source(tmp_path: Path, monkeypatch) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    owner = managed.inspect_managed_workspace(service.config.state_dir)["curation_owner"]
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    source = store.get_manifest(artifact)
    other = Manifest("evidence", Producer("local/other", "c" * 40, "b" * 64),
                     payloads=source.payloads, parameters=source.parameters)
    store.publish(other)
    service.start_preview(artifact, "gold", None)
    result = settled(service)
    assert result["status"] == "preview_ready"
    assert result["can_publish"] is False
    assert result["error_code"] == "gold_source_inventory_pending"
    with pytest.raises(BoundaryError, match="preview_cannot_publish"):
        service.start_publish(result["preview_id"])


def test_paired_training_rechecks_actual_overlap(tmp_path: Path, monkeypatch) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    service.start_preview(artifact, "training", None)
    training = settled(service)
    service.start_publish(training["preview_id"])
    parent = settled(service)["result_artifact_id"]
    service.start_preview(artifact, "test", parent)
    paired = settled(service)
    assert paired["status"] == "failed"
    assert paired["error_code"] == "training_test_overlap"


def test_invalid_local_evidence_never_gets_dataset_preview(tmp_path: Path, monkeypatch) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    owner = managed.inspect_managed_workspace(service.config.state_dir)["curation_owner"]
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    original = store.get_manifest(artifact)
    bad = Manifest("evidence", original.producer, payloads=original.payloads,
                   parameters=FrozenObject.of({**original.parameters.value(),
                                               "content_id": "0" * 64}))
    store.publish(bad)
    service.start_preview(bad.artifact_id, "training", None)
    result = settled(service)
    assert result["status"] == "failed"
    assert result["error_code"] == "transfer_identity_mismatch"
    assert owner.ledger.source_runs(bad.artifact_id) is None
    with owner.transaction() as db:
        assert db.execute("SELECT status FROM local_source_pending").fetchone() == ("published",)
    foreign = Manifest("evidence", original.producer, payloads=original.payloads,
                       parameters=FrozenObject.of({**original.parameters.value(),
                                                   "schema": "stpd/received-bundle-v1"}))
    store.publish(foreign)
    service.start_preview(foreign.artifact_id, "training", None)
    assert settled(service)["error_code"] == "local_verified_source_required"


def test_annotation_change_invalidates_publish_token(tmp_path: Path, monkeypatch) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    service.start_preview(artifact, "training", None)
    ready = settled(service)
    owner = managed.inspect_managed_workspace(service.config.state_dir)["curation_owner"]
    with owner.transaction() as db:
        occurrence = db.execute("SELECT id FROM curation_occurrences LIMIT 1").fetchone()[0]
    owner.ledger.annotate(occurrence, "synthetic-reviewer", "exclude", "synthetic exclusion")
    service.start_publish(ready["preview_id"])
    failed = settled(service)
    assert failed["error_code"] == "preview_changed"
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_claims").fetchone() == (0,)


def test_restart_pending_does_not_automatically_run(tmp_path: Path, monkeypatch) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    service.start_preview(artifact, "training", None)
    settled(service)
    old = json.loads(service.path.read_text())
    old["status"] = "pending"
    old["_phase"] = "publish"
    service.path.write_text(json.dumps(old))
    reopened = LocalDatasetService(service.config)
    assert reopened.status()["operation"]["status"] == "interrupted"
    assert reopened.thread is None
    with pytest.raises(BoundaryError, match="publication_recovery_required"):
        reopened.start_publish(old["preview_id"])


def test_explicit_recovery_of_bound_publish_after_status_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    service.start_preview(artifact, "training", None)
    ready = settled(service)

    def fail_registry(*_args, **_kwargs):
        raise OSError("synthetic registry failure after binding")

    original = dataset_module.sync_registry
    monkeypatch.setattr(dataset_module, "sync_registry", fail_registry)
    service.start_publish(ready["preview_id"])
    failed = settled(service)
    assert failed["status"] == "failed"
    assert failed["recovery_available"] is True
    assert failed["error_code"] == "publish_failed"
    monkeypatch.setattr(dataset_module, "sync_registry", original)
    reopened = LocalDatasetService(service.config)
    reopened.start_publish(ready["preview_id"])
    recovered = settled(reopened)
    assert recovered["status"] == "completed", recovered
    assert recovered["result_artifact_id"]


def test_bind_fault_recovers_only_exact_typed_published_artifact(
    tmp_path: Path, monkeypatch,
) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    service.start_preview(artifact, "training", None)
    ready = settled(service)
    original = LocalLedger.bind

    def fail_bind(*_args, **_kwargs):
        raise OSError("synthetic bind failure")

    monkeypatch.setattr(LocalLedger, "bind", fail_bind)
    service.start_publish(ready["preview_id"])
    failed = settled(service)
    assert failed["status"] == "failed"
    owner = managed.inspect_managed_workspace(service.config.state_dir)["curation_owner"]
    with owner.transaction() as db:
        assert db.execute("SELECT artifact FROM curation_claims WHERE id=?",
                          (failed["id"],)).fetchone() == (None,)
    monkeypatch.setattr(LocalLedger, "bind", original)
    reopened = LocalDatasetService(service.config)
    reopened.start_publish(ready["preview_id"])
    recovered = settled(reopened)
    assert recovered["status"] == "completed", recovered
    assert owner.ledger.dataset(recovered["result_artifact_id"])[0] == "training"
    assert recovered["recovery_available"] is False


def test_unproved_publication_keeps_claim_and_never_republishes(
    tmp_path: Path, monkeypatch,
) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    service.start_preview(artifact, "gold", None)
    ready = settled(service)
    assert ready["can_publish"]
    original = dataset_module.publish_selection
    calls = []

    def fail_before_publish(*_args, **_kwargs):
        calls.append("attempt")
        raise OSError("synthetic pre-publication failure")

    monkeypatch.setattr(dataset_module, "publish_selection", fail_before_publish)
    service.start_publish(ready["preview_id"])
    failed = settled(service)
    assert failed["status"] == "failed"
    assert failed["recovery_available"] is True
    assert calls == ["attempt"]
    monkeypatch.setattr(dataset_module, "publish_selection", original)
    reopened = LocalDatasetService(service.config)
    reopened.start_publish(ready["preview_id"])
    unknown = settled(reopened)
    assert unknown["error_code"] == "publication_recovery_required"
    assert unknown["recovery_available"] is False
    assert calls == ["attempt"]
    owner = managed.inspect_managed_workspace(service.config.state_dir)["curation_owner"]
    with owner.transaction() as db:
        assert db.execute("SELECT purpose,artifact FROM curation_claims WHERE id=?",
                          (failed["id"],)).fetchone() == ("gold", None)
    with pytest.raises(BoundaryError, match="publication_recovery_required"):
        reopened.start_preview(artifact, "training", None)


def test_late_preview_result_cannot_replace_newer_operation(tmp_path: Path, monkeypatch) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    original = service._select

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_select", delayed)
    service.start_preview(artifact, "training", None)
    assert entered.wait(10)
    replacement = LocalDatasetService(service.config)
    assert replacement.status()["operation"]["status"] == "interrupted"
    replacement.start_preview(artifact, "test", None)
    current = settled(replacement)
    assert current["status"] == "preview_ready"
    release.set()
    settled(service)
    durable = json.loads(service.path.read_text())
    assert durable["id"] == current["id"]
    assert durable["purpose"] == "test"


def test_terminal_status_write_failure_never_reports_durable_completion(
    tmp_path: Path, monkeypatch,
) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    original = dataset_module.atomic_json

    def fail_terminal(path, value):
        if value.get("status") == "preview_ready":
            raise OSError("synthetic terminal write failure")
        original(path, value)

    monkeypatch.setattr(dataset_module, "atomic_json", fail_terminal)
    service.start_preview(artifact, "training", None)
    result = settled(service)
    assert result["status"] == "interrupted"
    assert result["error_code"] == "operation_state_unavailable"
    assert json.loads(service.path.read_text())["status"] == "pending"
    reopened = LocalDatasetService(service.config)
    assert reopened.status()["operation"]["status"] == "interrupted"


def test_publish_terminal_write_failure_preserves_recovery_token(
    tmp_path: Path, monkeypatch,
) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    service.start_preview(artifact, "training", None)
    ready = settled(service)
    original = dataset_module.atomic_json

    def fail_completed(path, value):
        if value.get("status") == "completed":
            raise OSError("synthetic completed status failure")
        original(path, value)

    monkeypatch.setattr(dataset_module, "atomic_json", fail_completed)
    service.start_publish(ready["preview_id"])
    interrupted = settled(service)
    assert interrupted["status"] == "interrupted"
    assert interrupted["recovery_available"] is True
    with pytest.raises(BoundaryError, match="publication_recovery_required"):
        service.start_preview(artifact, "test", None)
    monkeypatch.setattr(dataset_module, "atomic_json", original)
    reopened = LocalDatasetService(service.config)
    assert reopened.status()["operation"]["status"] == "interrupted"
    reopened.start_publish(ready["preview_id"])
    assert settled(reopened)["status"] == "completed"


def test_gold_claim_precedes_publication_and_blocks_training_race(
    tmp_path: Path, monkeypatch,
) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    service.start_preview(artifact, "gold", None)
    ready = settled(service)
    assert ready["can_publish"]
    owner = managed.inspect_managed_workspace(service.config.state_dir)["curation_owner"]
    runs = owner.ledger.source_runs(artifact)
    assert runs
    entered = threading.Event()
    release = threading.Event()
    original = dataset_module.publish_selection

    def delayed_publish(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(dataset_module, "publish_selection", delayed_publish)
    service.start_publish(ready["preview_id"])
    assert entered.wait(10)
    with pytest.raises(BoundaryError, match="gold_reserved_data"):
        owner.ledger.claim("other-training", "training", runs)
    release.set()
    assert settled(service)["status"] == "completed"


def test_existing_store_needs_preparation_without_claiming_corruption(tmp_path: Path) -> None:
    _, artifact, source_store = _fixture(tmp_path / "source", canonical=True)
    store = tmp_path / "source/store"
    original = source_store.get_manifest(artifact).to_bytes()
    service = LocalDatasetService(ProjectConfig(
        tmp_path / "state", "", "", None, combination(),
        LocalResearchWorkspaceConfig(store, tmp_path / "source/registry.sqlite"),
    ))
    status = service.status()
    assert status["availability"] == "preparation_required"
    assert status["reason"] == "curation_preparation_required"
    assert not (store / ".curation-owner.json").exists()
    assert source_store.get_manifest(artifact).to_bytes() == original


def test_configured_second_profile_resolves_existing_managed_owner(
    tmp_path: Path, monkeypatch,
) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    owner = managed.inspect_managed_workspace(service.config.state_dir)["curation_owner"]
    legacy = LocalDatasetService(ProjectConfig(
        tmp_path / "other", "", "", None, combination(),
        LocalResearchWorkspaceConfig(owner.store_dir,
                                     owner.path.parent / "registry.sqlite"),
    ))
    assert legacy.status()["availability"] == "ready"
    assert legacy._selected()[0].identity == owner.identity
    assert legacy._selected()[0].path == owner.path


def test_malformed_durable_operation_cannot_be_overwritten(tmp_path: Path, monkeypatch) -> None:
    service, artifact = setup(tmp_path, monkeypatch)
    service.path.write_text('{"schema":"stpd/local-dataset-operation-v1","status":"pending"}')
    reopened = LocalDatasetService(service.config)
    assert reopened.status()["availability"] == "recovery_required"
    with pytest.raises(BoundaryError, match="operation_file_invalid"):
        reopened.start_preview(artifact, "training", None)
