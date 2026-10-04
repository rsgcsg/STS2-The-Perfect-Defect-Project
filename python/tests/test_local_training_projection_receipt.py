"""Owner-held projection proofs retain current authority without reprojecting rows."""

import pytest
from test_local_dataset_use import OPERATION, use_rows

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.local_curation import LocalCurationOwner

pytest_plugins = ["test_local_dataset_use"]


def test_receipt_requires_existing_admission_and_never_adds_training_use(owned):
    owner, store, dataset_id, _, _ = owned
    with pytest.raises(BoundaryError, match="training_source_use_missing"):
        owner.verified_training_receipt(store, (dataset_id,), OPERATION)
    assert use_rows(owner) == ([], [])


def test_receipt_survives_restart_and_only_rechecks_current_small_bindings(owned, monkeypatch):
    owner, store, dataset_id, _, _ = owned
    summary = owner.reserve_training_datasets(store, (dataset_id,), OPERATION)
    before = use_rows(owner)
    identity = owner.verified_training_receipt(store, (dataset_id,), OPERATION)
    reopened = LocalCurationOwner(owner.path, owner.store_dir, *owner.identity[:3])
    monkeypatch.setattr(reopened, "_training_dataset_bindings",
                        lambda *_args: pytest.fail("cached proof reprojected rows"))
    monkeypatch.setattr(store, "read_payload", lambda *_args: pytest.fail("read raw corpus"))
    assert reopened.require_training_receipt(store, (dataset_id,), OPERATION, identity) == summary
    assert reopened.verified_training_receipt(store, (dataset_id,), OPERATION) == identity
    assert use_rows(owner) == before
    with pytest.raises(BoundaryError, match="verified_training_receipt_required"):
        reopened.require_training_receipt(store, (dataset_id,), OPERATION, "f" * 64)
    with pytest.raises(BoundaryError, match="training_receipt_binding_mismatch"):
        reopened.require_training_receipt(store, (dataset_id,), "b" * 32, identity)
    with pytest.raises(BoundaryError, match="training_receipt_binding_mismatch"):
        reopened.require_training_receipt(store, ("f" * 64,), OPERATION, identity)


@pytest.mark.parametrize(("mutation", "reason"), [
    ("claim", "training_claim_mismatch"), ("index", "source_index_incomplete"),
    ("archive", "source_identity_conflict"), ("runs", "source_run_identity_mismatch"),
    ("use", "training_source_use_missing"), ("gold", "training_claim_mismatch"),
    ("receipt", "verified_training_receipt_required"),
])
def test_cached_proof_does_not_override_revocation_or_current_identity(owned, mutation, reason):
    owner, store, dataset_id, source, _ = owned
    owner.reserve_training_datasets(store, (dataset_id,), OPERATION)
    identity = owner.verified_training_receipt(store, (dataset_id,), OPERATION)
    with owner.transaction() as db:
        if mutation == "claim":
            db.execute("DELETE FROM curation_claims WHERE artifact=?", (dataset_id,))
        elif mutation == "index":
            db.execute("DELETE FROM curation_exact_source_index WHERE source=?",
                       (source.artifact_id,))
        elif mutation == "archive":
            db.execute("UPDATE curation_sources SET archive=? WHERE id=?",
                       ("f" * 64, source.artifact_id))
        elif mutation == "runs":
            db.execute("DELETE FROM curation_source_runs WHERE source=?", (source.artifact_id,))
        elif mutation == "use":
            db.execute("DELETE FROM curation_source_uses WHERE reference=?", (OPERATION,))
        elif mutation == "gold":
            db.execute("UPDATE curation_claims SET purpose='gold' WHERE artifact=?", (dataset_id,))
        else:
            db.execute("UPDATE local_training_projection_receipts SET receipt='{}'")
    with pytest.raises(BoundaryError, match=reason):
        owner.require_training_receipt(store, (dataset_id,), OPERATION, identity)
