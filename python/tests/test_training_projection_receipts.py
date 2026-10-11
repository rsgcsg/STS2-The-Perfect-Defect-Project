"""Owner-produced cached proof, with real canonical projection and live use checks."""

from __future__ import annotations

import json

import pytest
from test_light_action_m0_canonical_cli import _synthetic_workspace

from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.local_curation import LocalCurationOwner


@pytest.fixture
def admitted(tmp_path):
    _, store_dir, dataset_id, owner = _synthetic_workspace(tmp_path)
    archive = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
    operation = "d" * 32
    owner.reserve_training_datasets(archive, (dataset_id,), operation)
    return archive, dataset_id, owner, operation


def test_full_projection_runs_once_and_reopened_owner_reuses_proof_without_payloads(
        admitted, monkeypatch):
    archive, dataset_id, owner, operation = admitted
    calls = []
    full = owner.require_training_datasets

    def counted(*args):
        calls.append(args)
        return full(*args)

    monkeypatch.setattr(owner, "require_training_datasets", counted)
    receipt_id = owner.verified_training_receipt(archive, (dataset_id,), operation)
    assert len(calls) == 1
    monkeypatch.setattr(archive, "read_payload",
                        lambda *_: pytest.fail("cached proof read payload"))
    assert owner.verified_training_receipt(archive, (dataset_id,), operation) == receipt_id
    assert len(calls) == 1
    reopened = LocalCurationOwner(owner.path, owner.store_dir, *owner.identity[:3])
    monkeypatch.setattr(reopened, "require_training_datasets",
                        lambda *_: pytest.fail("cached proof reprojected dataset"))
    assert reopened.verified_training_receipt(archive, (dataset_id,), operation) == receipt_id
    summary = reopened.require_training_receipt(archive, (dataset_id,), operation, receipt_id)
    assert summary["operation_id"] == operation
    assert summary["datasets"][0]["artifact_id"] == dataset_id


@pytest.mark.parametrize("mutation", ["source_use", "run_use", "index", "claim", "gold"])
def test_cached_proof_does_not_override_current_authority(admitted, monkeypatch, mutation):
    archive, dataset_id, owner, operation = admitted
    receipt_id = owner.verified_training_receipt(archive, (dataset_id,), operation)
    summary = owner.require_training_receipt(archive, (dataset_id,), operation, receipt_id)
    run = summary["datasets"][0]["qualified_run_ids"][0]
    source = summary["datasets"][0]["sources"][0]["artifact_id"]
    with owner.transaction() as db:
        if mutation == "source_use":
            db.execute("DELETE FROM curation_source_uses WHERE source=? AND reference=?",
                       (source, operation))
        elif mutation == "run_use":
            db.execute("DELETE FROM curation_uses WHERE run=? AND reference=?", (run, operation))
        elif mutation == "index":
            db.execute("DELETE FROM curation_exact_source_index WHERE source=?", (source,))
        elif mutation == "claim":
            db.execute("DELETE FROM curation_claim_runs WHERE run=?", (run,))
        else:
            # A second owner can change the live claims between model operations.
            db.execute("INSERT INTO curation_claims VALUES(?,?,?,?)",
                       ("test-gold", "gold", "a" * 64, 0))
            db.execute("INSERT INTO curation_claim_runs VALUES(?,?)", ("test-gold", run))
    monkeypatch.setattr(archive, "read_payload", lambda *_: pytest.fail("cache read payload"))
    with pytest.raises(BoundaryError):
        owner.require_training_receipt(archive, (dataset_id,), operation, receipt_id)


def test_compatibility_change_requires_new_full_proof_and_preserves_old_receipt(
        admitted, monkeypatch):
    import spireagent.workbench.local_curation as module

    archive, dataset_id, owner, operation = admitted
    previous = owner.verified_training_receipt(archive, (dataset_id,), operation)
    with owner.transaction() as db:
        original = db.execute("SELECT receipt FROM local_training_projection_receipts "
                              "WHERE identity=?", (previous,)).fetchone()[0]
    monkeypatch.setattr(module, "training_projection_compatibility", lambda: "a" * 64)
    with pytest.raises(BoundaryError, match="verified_training_receipt_required"):
        owner.require_training_receipt(archive, (dataset_id,), operation, previous)
    current = owner.verified_training_receipt(archive, (dataset_id,), operation)
    assert current != previous
    with owner.transaction() as db:
        assert db.execute("SELECT receipt FROM local_training_projection_receipts "
                          "WHERE identity=?", (previous,)).fetchone()[0] == original


def test_caller_receipt_and_different_operation_cannot_supply_a_proof(admitted):
    archive, dataset_id, owner, operation = admitted
    receipt_id = owner.verified_training_receipt(archive, (dataset_id,), operation)
    with pytest.raises(BoundaryError, match="verified_training_receipt_required"):
        owner.require_training_receipt(archive, (dataset_id,), operation, "a" * 64)
    with pytest.raises(BoundaryError, match="verified_training_receipt_required"):
        owner.require_training_receipt(archive, (dataset_id,), "e" * 32, receipt_id)


def test_corrupted_owner_record_is_rejected_without_silent_reprojection(admitted, monkeypatch):
    archive, dataset_id, owner, operation = admitted
    receipt_id = owner.verified_training_receipt(archive, (dataset_id,), operation)
    with owner.transaction() as db:
        raw = db.execute("SELECT receipt FROM local_training_projection_receipts WHERE identity=?",
                         (receipt_id,)).fetchone()[0]
        value = json.loads(raw)
        value["summary"]["datasets"] = []
        db.execute("UPDATE local_training_projection_receipts SET receipt=? WHERE identity=?",
                   (json.dumps(value), receipt_id))
    monkeypatch.setattr(owner, "require_training_datasets", lambda *_: pytest.fail("reprojection"))
    with pytest.raises(BoundaryError, match="verified_training_receipt_required"):
        owner.verified_training_receipt(archive, (dataset_id,), operation)
