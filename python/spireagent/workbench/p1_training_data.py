"""P1 engineering allocation/use on the existing local CurationLedger.

The eligibility index is keyed by the immutable named projection, not by an
invented canonical conversion of its raw original. All native sources remain
parents in the same Store. No new database, permission system or research
admission is created here.
"""

from __future__ import annotations

from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.json_boundary import digest as checked_digest
from spireagent.storage.local import LocalBlobStore
from stpd.p1.data import digest
from stpd.p1.dataset import inspect_projections, load_snapshot


def _occurrence(decision: str) -> str:
    return digest(("p1-N-native:" + decision).encode())


def _scope(
    store: Any, owner: Any, snapshot_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(store.blobs, LocalBlobStore) or store.blobs.root != owner.store_dir.resolve():
        raise BoundaryError("p1_training_data", "store_owner_mismatch")
    manifest, value = load_snapshot(store, snapshot_id)
    if manifest.parameters.value().get("purpose") != "engineering":
        raise BoundaryError("p1_training_data", "engineering_snapshot_required")
    if not value["partition_N"].get("train") or not value["partition_N"].get("dev"):
        raise BoundaryError("p1_training_data", "nonempty_train_dev_allocation_required")
    if set(value["partition_N"]) - {"train", "dev", "test"}:
        raise BoundaryError("p1_training_data", "unassigned_source_cannot_start_training")
    scopes = {}
    for split in ("train", "dev", "test"):
        refs = [p for p in value["points"] if p["split"] == split]
        if not refs:
            continue
        references = [ref for point in refs for ref in (point, *point["aliases"])]
        scopes[split] = {
            "claim_id": snapshot_id if split == "train" else f"p1-N-{split}:{snapshot_id}",
            "roots": {p["root_run_id"] for p in references},
            "projections": {p["projection_id"] for p in references},
            "points": refs,
        }
    return value, scopes


def _require_purposes(owner: Any, snapshot_id: str, scopes: dict[str, Any]) -> None:
    ledger = owner.ledger
    with owner.transaction() as db:
        for split, scope in scopes.items():
            expected = "training" if split == "train" else "test"
            claim = db.execute("SELECT purpose FROM curation_claims WHERE id=?",
                               (scope["claim_id"],)).fetchone()
            claimed = {row[0] for row in db.execute(
                "SELECT run FROM curation_claim_runs WHERE claim=?", (scope["claim_id"],)
            )}
            if claim != (expected,) or claimed != scope["roots"]:
                raise BoundaryError("p1_training_data", "snapshot_purpose_reservation_required")
            related = ledger._groups(db, scope["roots"])
            for identity, (purpose, _) in ledger._claims(db, related).items():
                if identity == scope["claim_id"]:
                    continue
                if purpose == "gold":
                    raise BoundaryError("p1_training_data", "gold_reserved_data")
                if purpose != expected or (
                    split != "train" and not identity.startswith(f"p1-N-{split}:")
                ):
                    raise BoundaryError("p1_training_data", "related_run_split_purpose_overlap")
            if split != "train" and any(db.execute(
                "SELECT 1 FROM curation_uses WHERE run=? AND kind='training' UNION "
                "SELECT 1 FROM curation_source_uses u JOIN curation_source_runs r "
                "ON r.source=u.source WHERE r.run=? AND u.kind='training'", (run, run)
            ).fetchone() for run in related):
                raise BoundaryError("p1_training_data", "held_out_run_has_training_exposure")
            for point in scope["points"]:
                annotation = db.execute(
                    "SELECT action FROM curation_annotations WHERE occurrence=? "
                    "ORDER BY sequence DESC LIMIT 1", (_occurrence(point["source_decision_id"]),)
                ).fetchone()
                if annotation == ("exclude",):
                    raise BoundaryError("p1_training_data", "snapshot_selected_excluded_N")
    train = scopes["train"]
    if ledger.dataset(snapshot_id) != ("training", train["roots"]):
        raise BoundaryError("p1_training_data", "training_claim_binding_mismatch")


def reserve_p1_snapshot(owner: Any, store: Any, snapshot_id: str) -> dict[str, Any]:
    """Explicit engineering purpose reservation; no model updates or formal admission.

    Existing claims retain failed preparation intent. Held-out role reservations
    use the ledger's existing unbound-claim facility, with a self-describing
    immutable snapshot reference. Only the actual TRAIN roots bind the training
    artifact; dev/test are not falsely marked as gradient training.
    """
    value, scopes = _scope(store, owner, snapshot_id)
    ledger = owner.ledger
    for source in value["sources"]:
        projection_id = source["projection_id"]
        index = inspect_projections(store, [projection_id])
        fingerprint_rows = [(key, root) for group in index["groups"]
                            for root in group["root_run_ids"] for key in group["keys"]]
        with owner.transaction() as db:
            previous = db.execute("SELECT archive FROM curation_sources WHERE id=?",
                                  (projection_id,)).fetchone()
            if previous is not None and previous != (source["segments_sha256"],):
                raise BoundaryError("p1_training_data", "projection_index_identity_conflict")
            db.execute("INSERT OR IGNORE INTO curation_sources VALUES(?,?,0)",
                       (projection_id, source["segments_sha256"]))
            db.executemany("INSERT OR IGNORE INTO curation_fingerprints VALUES(?,?)",
                           fingerprint_rows)
            for point in index["points"]:
                occurrence = _occurrence(point["source_decision_id"])
                db.execute("INSERT OR IGNORE INTO curation_source_runs VALUES(?,?)",
                           (projection_id, point["root_run_id"]))
                db.execute("INSERT OR IGNORE INTO curation_source_decisions VALUES(?,?,?)",
                           (projection_id, occurrence, point["N_fact_sha256"]))
                db.execute("INSERT OR IGNORE INTO curation_occurrences VALUES(?,?,?)",
                           (occurrence, point["root_run_id"], point["source_decision_id"]))
            db.execute("UPDATE curation_sources SET complete=1 WHERE id=?", (projection_id,))
            db.execute("INSERT OR IGNORE INTO curation_exact_source_index VALUES(?)",
                       (projection_id,))
    for split, scope in scopes.items():
        ledger.claim(scope["claim_id"], "training" if split == "train" else "test", scope["roots"])
    ledger.bind(snapshot_id, snapshot_id)
    _require_purposes(owner, snapshot_id, scopes)
    return {
        "snapshot_id": snapshot_id, "research_admission": "not_evaluated",
        "scope": "engineering_N_only", "training_use_recorded": False,
        "partitions": {s: {"claim_id": x["claim_id"], "root_run_ids": sorted(x["roots"]),
                            "N": len(x["points"])} for s, x in scopes.items()},
        "historical_external_exposure": "unknown",
        "index_scope": "immutable_named_projection_eligible_N_not_raw_capture_inventory",
    }


def require_p1_snapshot(
    owner: Any, store: Any, snapshot_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value, scopes = _scope(store, owner, snapshot_id)
    _require_purposes(owner, snapshot_id, scopes)
    return value, scopes


def record_p1_training_use(owner: Any, store: Any, snapshot_id: str, operation_id: str) -> None:
    checked_digest(operation_id, "p1_training_data.operation_id", length=32)
    _, scopes = require_p1_snapshot(owner, store, snapshot_id)
    train = scopes["train"]
    for source in sorted(train["projections"]):
        owner.ledger.use_source(source, "training", operation_id)
    owner.ledger.use(train["roots"], "training", operation_id)
    require_p1_training_use(owner, store, snapshot_id, operation_id)


def require_p1_training_use(owner: Any, store: Any, snapshot_id: str, operation_id: str) -> None:
    _, scopes = require_p1_snapshot(owner, store, snapshot_id)
    train = scopes["train"]
    owner.ledger.require_training_use(
        snapshot_id, train["projections"], train["roots"], operation_id
    )

