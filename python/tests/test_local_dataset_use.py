"""Verified synthetic canonical data through the existing local exposure owner."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from test_dataset_curation import build
from test_decision_store import setup
from test_local_curation import create

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from stpd.fullrun.curated_dataset import curate, publish_selection
from stpd.fullrun.decision_dataset import SelectionRules
from stpd.fullrun.decision_spool import SpoolSelection
from stpd.fullrun.decision_store import load, preview
from stpd.fullrun.decision_training import AllocationSpec, load_allocation, publish_allocation
from stpd.fullrun.decision_union import union_decisions

OPERATION = "a" * 32
EVALUATION = "b" * 32


@pytest.fixture
def owned(tmp_path: Path):
    hub, upload, source, jobs = setup(tmp_path / "hub")
    dataset_id = build(jobs, upload)["result"]["artifact_id"]
    local = tmp_path / "local"
    local.mkdir()
    _, _, owner = create(local)
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    copy_artifact(hub.store, store, dataset_id)
    projected = preview(store, (store.get_manifest(source.artifact_id),), SelectionRules(),
                        on_projection=lambda projection:
                        owner.ledger.index_source(source.artifact_id, projection))
    try:
        owner.ledger.claim(dataset_id, "training", projected.run_ids)
        owner.ledger.bind(dataset_id, dataset_id)
    finally:
        if isinstance(projected.records, SpoolSelection):
            projected.records.owner.close()
    return owner, store, dataset_id, source, hub.producer


def use_rows(owner):
    with owner.transaction() as db:
        return (db.execute("SELECT run,kind,reference FROM curation_uses ORDER BY run").fetchall(),
                db.execute("SELECT * FROM curation_source_uses ORDER BY source").fetchall())


def model_allocation(store, dataset_id, producer, *, isolation="run"):
    allocation = publish_allocation(store, dataset_id,
                                    AllocationSpec(isolation=isolation), producer)
    model = Manifest("model", producer, (Parent("allocation", allocation.artifact_id),),
                     parameters=FrozenObject.of({"qualification": "synthetic_no_weights"}))
    store.publish(model)
    return model, allocation


def test_reserve_before_downstream_consumption_and_idempotent_require(owned):
    owner, store, dataset_id, source, _ = owned
    original_manifests = store.manifest_ids()
    with pytest.raises(BoundaryError, match="training_source_use_missing"):
        owner.require_training_datasets(store, (dataset_id,), OPERATION)
    receipt = owner.reserve_training_datasets(store, (dataset_id,), OPERATION)
    assert receipt["operation_id"] == OPERATION
    assert receipt["datasets"][0]["artifact_id"] == dataset_id
    assert receipt["datasets"][0]["sources"] == [{
        "artifact_id": source.artifact_id, "archive_sha256": source.payload("archive").sha256,
    }]
    assert receipt["historical_external_exposure"] == "unknown"
    assert receipt["ledger_scope"] == "source_and_run_exposure_not_dataset_operation_binding"
    before = use_rows(owner)
    assert owner.require_training_datasets(store, (dataset_id,), OPERATION) == receipt
    assert owner.reserve_training_datasets(store, (dataset_id,), OPERATION) == receipt
    assert use_rows(owner) == before
    assert store.manifest_ids() == original_manifests
    with pytest.raises(BoundaryError, match="training_source_use_missing"):
        owner.require_training_datasets(store, (dataset_id,), "c" * 32)


@pytest.mark.parametrize("operation", ["a" * 31, "x" * 32])
def test_invalid_operation_cannot_reserve(owned, operation):
    owner, store, dataset_id, _, _ = owned
    with pytest.raises(BoundaryError):
        owner.reserve_training_datasets(store, (dataset_id,), operation)
    assert use_rows(owner) == ([], [])


@pytest.mark.parametrize(("change", "error"), [
    ("claim", "training_claim_mismatch"),
    ("index", "source_index_incomplete"),
    ("archive", "source_identity_conflict"),
    ("runs", "source_run_identity_mismatch"),
])
def test_current_owner_binding_is_required_before_reservation(owned, change, error):
    owner, store, dataset_id, source, _ = owned
    with owner.transaction() as db:
        if change == "claim":
            db.execute("DELETE FROM curation_claims WHERE artifact=?", (dataset_id,))
        elif change == "index":
            db.execute("DELETE FROM curation_exact_source_index WHERE source=?",
                       (source.artifact_id,))
        elif change == "archive":
            db.execute("UPDATE curation_sources SET archive=? WHERE id=?",
                       ("f" * 64, source.artifact_id))
        else:
            db.execute("DELETE FROM curation_source_runs WHERE source=?", (source.artifact_id,))
    with pytest.raises(BoundaryError, match=error):
        owner.reserve_training_datasets(store, (dataset_id,), OPERATION)
    assert use_rows(owner) == ([], [])


def test_store_copy_cannot_borrow_another_store_owner(owned, tmp_path):
    owner, store, dataset_id, _, _ = owned
    copied = ManifestArtifactStore(LocalBlobStore(tmp_path / "copy"))
    copy_artifact(store, copied, dataset_id)
    with pytest.raises(BoundaryError, match="store_identity_mismatch"):
        owner.reserve_training_datasets(copied, (dataset_id,), OPERATION)
    assert use_rows(owner) == ([], [])


@pytest.mark.parametrize("purpose", ["test", "gold"])
def test_wrapper_cannot_hide_held_out_ancestor(owned, purpose):
    owner, store, dataset_id, _, producer = owned
    original = store.get_manifest(dataset_id)
    held_out = replace(original, parameters=FrozenObject.of({
        **original.parameters.value(), "purpose": purpose,
    }))
    store.publish(held_out)
    wrapper = Manifest("dataset", producer, (Parent("dataset_" + held_out.artifact_id,
                                                   held_out.artifact_id),),
                       parameters=original.parameters)
    store.publish(wrapper)
    with pytest.raises(BoundaryError, match="held_out_data_cannot_train"):
        owner.reserve_training_datasets(store, (wrapper.artifact_id,), OPERATION)
    assert use_rows(owner) == ([], [])


@pytest.mark.parametrize("purpose", ["test", "gold"])
def test_current_held_out_duplicate_group_blocks_before_use(owned, purpose):
    owner, store, dataset_id, source, _ = owned
    runs = owner.ledger.source_runs(source.artifact_id)
    owner.ledger.claim("held-out-neighbor", purpose, {"neighbor/run"})
    with owner.transaction() as db:
        db.executemany("INSERT INTO curation_fingerprints VALUES(?,?)",
                       [("duplicate-visible-input", next(iter(runs))),
                        ("duplicate-visible-input", "neighbor/run")])
    with pytest.raises(BoundaryError, match="held_out_data_cannot_train"):
        owner.reserve_training_datasets(store, (dataset_id,), OPERATION)
    assert use_rows(owner) == ([], [])


def test_nested_curated_merge_uses_actual_evidence_sources(owned):
    owner, store, dataset_id, source, producer = owned
    _, parent = load(store, dataset_id)
    rules = SelectionRules(seed=23)
    base = union_decisions(((dataset_id, parent),), rules)
    selected = curate(base, "training", {"revision": 0, "items": {}})
    try:
        wrapper = publish_selection(store, (store.get_manifest(dataset_id),), rules, producer,
                                    selected, merging=True, expected=selected.logical_id,
                                    paired_training=None)
        owner.ledger.claim(wrapper.artifact_id, "training", selected.run_ids)
        owner.ledger.bind(wrapper.artifact_id, wrapper.artifact_id)
    finally:
        for dataset in (parent, base, selected):
            if isinstance(dataset.records, SpoolSelection):
                dataset.records.owner.close()
    receipt = owner.reserve_training_datasets(store, (wrapper.artifact_id,), OPERATION)
    assert receipt["datasets"][0]["sources"][0]["artifact_id"] == source.artifact_id
    assert receipt["datasets"][0]["artifact_id"] == wrapper.artifact_id


def test_failed_preparation_keeps_conservative_source_reservation(owned, monkeypatch):
    owner, store, dataset_id, source, _ = owned
    monkeypatch.setattr(owner.ledger, "use", lambda *args: (_ for _ in ()).throw(
        RuntimeError("synthetic-preparation-failure")))
    with pytest.raises(RuntimeError, match="synthetic-preparation-failure"):
        owner.reserve_training_datasets(store, (dataset_id,), OPERATION)
    assert use_rows(owner) == ([], [(source.artifact_id, "training", OPERATION)])
    with pytest.raises(BoundaryError, match="training_run_use_missing"):
        owner.require_training_datasets(store, (dataset_id,), OPERATION)


def test_legacy_unknown_allows_training_and_diagnostic_dev_preserves_unknown(owned):
    owner, store, dataset_id, source, producer = owned
    runs = owner.ledger.source_runs(source.artifact_id)
    with owner.transaction() as db:
        db.execute("CREATE TABLE local_legacy_unknown_runs(run TEXT PRIMARY KEY)")
        db.executemany("INSERT INTO local_legacy_unknown_runs VALUES(?)", ((run,) for run in runs))
    owner.legacy_guard = True
    model, allocation = model_allocation(store, dataset_id, producer)
    with pytest.raises(BoundaryError, match="training_source_use_missing"):
        owner.reserve_allocation_dev(store, model.artifact_id, allocation.artifact_id,
                                     OPERATION, EVALUATION)
    assert use_rows(owner) == ([], [])
    owner.reserve_training_datasets(store, (dataset_id,), OPERATION)
    receipt = owner.reserve_allocation_dev(store, model.artifact_id, allocation.artifact_id,
                                           OPERATION, EVALUATION)
    assert receipt["clean_held_out_claim"] is False
    assert receipt["physical_game_independence"] == "unresolved"
    assert receipt["historical_external_exposure"] == "unknown"
    assert receipt["evaluation_scope"] == "within_training_purpose_allocation"
    with owner.transaction() as db:
        assert {row[0] for row in db.execute("SELECT run FROM local_legacy_unknown_runs")} == runs
        assert {row[0] for row in db.execute(
            "SELECT run FROM curation_uses WHERE kind='evaluation' AND reference=?",
            (EVALUATION,))} == set(receipt["qualified_run_ids"])
        assert db.execute("SELECT * FROM curation_source_uses WHERE kind='evaluation'").fetchall()
    before = use_rows(owner)
    assert owner.reserve_allocation_dev(store, model.artifact_id, allocation.artifact_id,
                                        OPERATION, EVALUATION) == receipt
    assert use_rows(owner) == before
    with pytest.raises(BoundaryError, match="gold_previously_used_for_evaluation"):
        owner.ledger.claim("cannot-seal", "gold", receipt["qualified_run_ids"])


def test_evaluation_rejects_different_allocation_or_training_operation(owned):
    owner, store, dataset_id, _, producer = owned
    model, allocation = model_allocation(store, dataset_id, producer)
    other = publish_allocation(store, dataset_id, AllocationSpec(seed=99), producer)
    owner.reserve_training_datasets(store, (dataset_id,), OPERATION)
    before = use_rows(owner)
    with pytest.raises(BoundaryError, match="model_allocation_mismatch"):
        owner.reserve_allocation_dev(store, model.artifact_id, other.artifact_id,
                                     OPERATION, EVALUATION)
    with pytest.raises(BoundaryError, match="training_source_use_missing"):
        owner.reserve_allocation_dev(store, model.artifact_id, allocation.artifact_id,
                                     "c" * 32, EVALUATION)
    assert use_rows(owner) == before


def test_forged_allocation_cannot_supply_dev_membership(owned):
    owner, store, dataset_id, _, producer = owned
    _, allocation = model_allocation(store, dataset_id, producer)
    info = allocation.parameters.value()
    forged = replace(allocation, parameters=FrozenObject.of({
        **info, "spec": {**info["spec"], "max_dev": 1}, "counts": {"train": 1, "dev": 10},
    }))
    store.publish(forged)
    model = Manifest("model", producer, (Parent("allocation", forged.artifact_id),))
    store.publish(model)
    owner.reserve_training_datasets(store, (dataset_id,), OPERATION)
    before = use_rows(owner)
    with pytest.raises(BoundaryError, match="membership_or_identity_mismatch"):
        owner.reserve_allocation_dev(store, model.artifact_id, forged.artifact_id,
                                     OPERATION, EVALUATION)
    assert use_rows(owner) == before


def test_explicit_same_run_decision_diagnostic_is_not_clean(owned):
    owner, store, dataset_id, _, producer = owned
    model, allocation = model_allocation(store, dataset_id, producer, isolation="decision")
    owner.reserve_training_datasets(store, (dataset_id,), OPERATION)
    _, data, members = load_allocation(store, allocation.artifact_id)
    try:
        train = {m["run_id"] for m in members["members"] if m["split"] == "train"}
        dev = {m["run_id"] for m in members["members"] if m["split"] == "dev"}
        assert train & dev
    finally:
        if isinstance(data.records, SpoolSelection):
            data.records.owner.close()
    receipt = owner.reserve_allocation_dev(store, model.artifact_id, allocation.artifact_id,
                                           OPERATION, EVALUATION)
    assert receipt["semantic_overlap"] is True
    assert receipt["clean_held_out_claim"] is False
