"""Synthetic existing-store preparation; no real recordings or stores are opened."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_local_recording_preview import _fixture

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.workbench import inplace_curation as inplace
from spireagent.workbench import local_dataset as dataset_module
from spireagent.workbench.developer import LocalResearchWorkspaceConfig, ProjectConfig, combination
from spireagent.workbench.inplace_curation import (
    LEDGER_NAME,
    PLAN_NAME,
    InplaceCurationPreparation,
    configured_owner,
    preparation_status,
)
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_recording_import import _selected_curation_owner
from stpd.fullrun.curated_dataset import curate, publish_selection
from stpd.fullrun.decision_dataset import SelectionRules
from stpd.fullrun.decision_store import preview, publish


def _config(tmp_path: Path) -> tuple[ProjectConfig, str, object]:
    _, source, store = _fixture(tmp_path / "old", canonical=True)
    state = tmp_path / "profile"
    state.mkdir()
    return (ProjectConfig(state, "", "", None, combination(),
                          LocalResearchWorkspaceConfig(tmp_path / "old/store",
                                                       tmp_path / "old/registry.sqlite")),
            source, store)


def _settle(service: InplaceCurationPreparation) -> dict:
    assert service.thread is not None
    service.thread.join(timeout=20)
    assert not service.thread.is_alive()
    return service.status()


def test_explicit_prepare_reuses_one_ledger_across_profiles(tmp_path: Path) -> None:
    config, source, store = _config(tmp_path)
    original = store.get_manifest(source).to_bytes()
    registry_before = config.research_workspace.registry_path.read_bytes()
    ids_before = store.manifest_ids()
    assert preparation_status(config)["status"] == "preparation_required"
    assert not (config.research_workspace.store_dir / PLAN_NAME).exists()
    with pytest.raises(BoundaryError, match="curation_preparation_required"):
        configured_owner(config)

    service = InplaceCurationPreparation(config)
    assert service.start()["status"] == "preparing"
    assert _settle(service)["status"] == "ready"
    owner = configured_owner(config)
    assert owner.path == config.research_workspace.store_dir / LEDGER_NAME
    runs = owner.ledger.source_runs(source)
    assert runs
    with pytest.raises(BoundaryError, match="legacy_gold_history_unknown"):
        owner.ledger.claim("c" * 64, "gold", runs)
    owner.ledger.claim("d" * 64, "training", runs)
    assert store.get_manifest(source).to_bytes() == original
    assert store.manifest_ids() == ids_before
    assert config.research_workspace.registry_path.read_bytes() == registry_before
    assert service.start()["status"] == "ready"

    second = tmp_path / "other-profile"
    second.mkdir()
    other_config = ProjectConfig(second, "", "", None, combination(),
                                 config.research_workspace)
    assert configured_owner(other_config).identity == owner.identity
    assert _selected_curation_owner(other_config).path == owner.path


def test_old_training_claim_and_purposeless_dataset_keep_gold_unknown(tmp_path: Path) -> None:
    config, source, store = _config(tmp_path)
    evidence = store.get_manifest(source)
    rules = SelectionRules()
    base = preview(store, (evidence,), rules)
    old_plain = publish(store, (evidence,), rules, evidence.producer, base.logical_id)
    selected = curate(base, "training", {"revision": 0, "items": {}})
    old_training = publish_selection(store, (evidence,), rules, evidence.producer, selected,
                                     merging=False, expected=selected.logical_id,
                                     paired_training=None)
    run_ids = selected.run_ids
    service = InplaceCurationPreparation(config)
    service.start()
    status = _settle(service)
    assert status["status"] == "ready", status
    assert status["known_dataset_count"] == 1
    assert status["unknown_dataset_count"] == 1
    owner = configured_owner(config)
    assert owner.ledger.dataset(old_training.artifact_id) == ("training", run_ids)
    assert owner.ledger.dataset(old_plain.artifact_id) is None
    owner.ledger.claim("a" * 64, "test", run_ids)
    with pytest.raises(BoundaryError, match="legacy_gold_history_unknown"):
        owner.ledger.claim("b" * 64, "gold", run_ids)


def test_original_store_dataset_flow_uses_prepared_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, source, store = _config(tmp_path)
    preparation = InplaceCurationPreparation(config)
    preparation.start()
    assert _settle(preparation)["status"] == "ready"
    producer = store.get_manifest(source).producer
    monkeypatch.setattr(dataset_module, "source_identity", lambda _: producer)
    service = LocalDatasetService(config)
    service.start_preview(source, "gold", None)
    assert service.thread is not None
    service.thread.join(timeout=15)
    gold = service.status()["operation"]
    assert gold["status"] == "preview_ready"
    assert gold["can_publish"] is False
    assert gold["error_code"] == "legacy_gold_history_unknown"
    service.start_preview(source, "training", None)
    assert service.thread is not None
    service.thread.join(timeout=15)
    ready = service.status()["operation"]
    assert ready["status"] == "preview_ready", ready
    assert ready["can_publish"] is True
    service.start_publish(ready["preview_id"])
    assert service.thread is not None
    service.thread.join(timeout=15)
    done = service.status()["operation"]
    assert done["status"] == "completed", done
    assert done["result_artifact_id"] in store.manifest_ids()
    assert configured_owner(config).ledger.dataset(done["result_artifact_id"])[0] == "training"


def test_old_gold_manifest_never_gets_fresh_authority(tmp_path: Path) -> None:
    config, source, store = _config(tmp_path)
    source_manifest = store.get_manifest(source)
    gold = Manifest("dataset", source_manifest.producer,
                    parameters=FrozenObject.of({"schema": "stpd/curated-decision-dataset-v1",
                                                "purpose": "gold"}))
    store.publish(gold)
    service = InplaceCurationPreparation(config)
    service.start()
    status = _settle(service)
    assert status["status"] == "recovery_required"
    assert status["reason"] == "legacy_gold_recovery_required"
    assert status["retry_available"] is False
    with pytest.raises(BoundaryError, match="curation_preparation_incomplete"):
        configured_owner(config)


def test_terminal_write_failure_resumes_same_owner_and_missing_ledger_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _, _ = _config(tmp_path)
    actual = inplace.atomic_json
    calls = 0

    def fail_once(path: Path, value: object) -> None:
        nonlocal calls
        if path.name == PLAN_NAME and isinstance(value, dict) and value.get("status") == "ready":
            calls += 1
            if calls == 1:
                raise OSError("synthetic terminal write failure")
        actual(path, value)

    monkeypatch.setattr(inplace, "atomic_json", fail_once)
    service = InplaceCurationPreparation(config)
    service.start()
    failed = _settle(service)
    assert failed["status"] == "recovery_required"
    assert failed["retry_available"] is True
    first = (config.research_workspace.store_dir / PLAN_NAME).read_bytes()
    service.start()
    assert _settle(service)["status"] == "ready"
    assert first != (config.research_workspace.store_dir / PLAN_NAME).read_bytes()
    owner = configured_owner(config)
    (config.research_workspace.store_dir / LEDGER_NAME).unlink()
    assert preparation_status(config)["status"] == "recovery_required"
    assert preparation_status(config)["retry_available"] is False
    with pytest.raises(BoundaryError, match="ledger_recovery_required"):
        configured_owner(config)
    assert owner.path == config.research_workspace.store_dir / LEDGER_NAME


def test_missing_preparation_plan_cannot_disable_legacy_gold_guard(tmp_path: Path) -> None:
    config, source, _ = _config(tmp_path)
    service = InplaceCurationPreparation(config)
    service.start()
    assert _settle(service)["status"] == "ready"
    owner = configured_owner(config)
    runs = owner.ledger.source_runs(source)
    assert runs
    (config.research_workspace.store_dir / PLAN_NAME).unlink()
    status = preparation_status(config)
    assert status["status"] == "recovery_required"
    assert status["retry_available"] is False
    with pytest.raises(BoundaryError, match="preparation_recovery_required"):
        configured_owner(config)
    with pytest.raises(BoundaryError, match="preparation_recovery_required"):
        InplaceCurationPreparation(config).start()
    with pytest.raises(BoundaryError, match="legacy_gold_history_unknown"):
        owner.ledger.claim("e" * 64, "gold", runs)


def test_pending_source_bridge_and_gold_claim_share_one_writer(tmp_path: Path) -> None:
    config, source, _ = _config(tmp_path)
    service = InplaceCurationPreparation(config)
    service.start()
    assert _settle(service)["status"] == "ready"
    owner = configured_owner(config)
    old_run = next(iter(owner.ledger.source_runs(source) or ()))
    with owner.transaction() as db:
        fingerprint = db.execute(
            "SELECT fingerprint FROM curation_fingerprints WHERE run=? LIMIT 1",
            (old_run,),
        ).fetchone()[0]
    candidate = "f" * 64
    future_run = "future-run"
    owner.begin_source(candidate)
    ready = threading.Barrier(2)

    def bridge() -> None:
        ready.wait(timeout=5)
        with owner.transaction() as db:
            db.execute("INSERT INTO curation_fingerprints VALUES(?,?)",
                       (fingerprint, future_run))
            db.execute("DELETE FROM local_source_pending WHERE candidate=?", (candidate,))

    def claim() -> str:
        ready.wait(timeout=5)
        try:
            owner.ledger.claim("g" * 64, "gold", {future_run})
        except BoundaryError as error:
            return error.code
        return "incorrectly_reserved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempted = pool.submit(claim)
        indexed = pool.submit(bridge)
        assert attempted.result(timeout=10) in {
            "gold_source_inventory_pending", "legacy_gold_history_unknown"
        }
        indexed.result(timeout=10)
    with pytest.raises(BoundaryError, match="legacy_gold_history_unknown"):
        owner.ledger.claim("h" * 64, "gold", {future_run})
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_claims WHERE purpose='gold'").fetchone() \
            == (0,)
