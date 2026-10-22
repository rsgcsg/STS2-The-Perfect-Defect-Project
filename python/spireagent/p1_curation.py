"""P1 engineering allocation/use on the existing shared CurationLedger.

The eligibility index is keyed by the immutable named projection, not by an
invented canonical conversion of its raw original. All native sources remain
parents in the same Store. No new database, permission system or research
admission is created here.
"""

from __future__ import annotations

from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.json_boundary import digest as checked_digest
from spireagent.research_curation import CurationLedger
from stpd.p1.data import digest, json_bytes
from stpd.p1.dataset import inspect_projections, load_snapshot, projection_payload_role


def _occurrence(decision: str) -> str:
    return digest(("p1-N-native:" + decision).encode())


def _scope(
    store: Any, snapshot_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
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


def _require_purposes(
    ledger: CurationLedger, snapshot_id: str, scopes: dict[str, Any],
) -> None:
    with ledger.operations.transaction() as db:
        for split, scope in scopes.items():
            expected = "training" if split == "train" else "test"
            claim = db.execute("SELECT purpose FROM curation_claims WHERE id=?",
                               (scope["claim_id"],)).fetchone()
            claimed = {row[0] for row in db.execute(
                "SELECT run FROM curation_claim_runs WHERE claim=?", (scope["claim_id"],)
            )}
            if claim is None or claim[0] != expected or claimed != scope["roots"]:
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
                if annotation is not None and annotation[0] == "exclude":
                    raise BoundaryError("p1_training_data", "snapshot_selected_excluded_N")
    train = scopes["train"]
    if ledger.dataset(snapshot_id) != ("training", train["roots"]):
        raise BoundaryError("p1_training_data", "training_claim_binding_mismatch")


def index_p1_projection(
    ledger: CurationLedger, store: Any, projection_id: str,
) -> dict[str, Any]:
    """Index verified eligible N and observed lineage without allocating data use."""
    index = inspect_projections(store, [projection_id])
    source = index["sources"][0]
    role = projection_payload_role(store.get_manifest(projection_id))
    payload_sha256 = source[role + "_sha256"]
    fingerprint_rows = [(key, root) for group in index["groups"]
                        for root in group["root_run_ids"] for key in group["keys"]]
    with ledger.operations.transaction() as db:
        previous = db.execute("SELECT archive FROM curation_sources WHERE id=?",
                              (projection_id,)).fetchone()
        if previous is not None and previous[0] != payload_sha256:
            raise BoundaryError("p1_training_data", "projection_index_identity_conflict")
        db.execute("INSERT OR IGNORE INTO curation_sources VALUES(?,?,0)",
                   (projection_id, payload_sha256))
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
    return {"projection_id": projection_id, "eligible_N": index["unique_N"],
            "root_run_ids": sorted({p["root_run_id"] for p in index["points"]}),
            "index_scope": "immutable_named_projection_eligible_N_not_raw_capture_inventory",
            "purpose_reserved": False, "training_use_recorded": False}


def reserve_p1_snapshot(ledger: CurationLedger, store: Any, snapshot_id: str) -> dict[str, Any]:
    """Explicit engineering purpose reservation; no model updates or formal admission.

    Existing claims retain failed preparation intent. Held-out role reservations
    use the ledger's existing unbound-claim facility, with a self-describing
    immutable snapshot reference. Only the actual TRAIN roots bind the training
    artifact; dev/test are not falsely marked as gradient training.
    """
    value, scopes = _scope(store, snapshot_id)
    for source in value["sources"]:
        index_p1_projection(ledger, store, source["projection_id"])
    for split, scope in scopes.items():
        ledger.claim(scope["claim_id"], "training" if split == "train" else "test", scope["roots"])
    ledger.bind(snapshot_id, snapshot_id)
    _require_purposes(ledger, snapshot_id, scopes)
    return {
        "snapshot_id": snapshot_id, "research_admission": "not_evaluated",
        "scope": "engineering_N_only", "training_use_recorded": False,
        "partitions": {s: {"claim_id": x["claim_id"], "root_run_ids": sorted(x["roots"]),
                            "N": len(x["points"])} for s, x in scopes.items()},
        "historical_external_exposure": "unknown",
        "index_scope": "immutable_named_projection_eligible_N_not_raw_capture_inventory",
    }


def require_p1_snapshot(
    ledger: CurationLedger, store: Any, snapshot_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value, scopes = _scope(store, snapshot_id)
    _require_purposes(ledger, snapshot_id, scopes)
    return value, scopes


def record_p1_training_use(
    ledger: CurationLedger, store: Any, snapshot_id: str, operation_id: str,
) -> None:
    checked_digest(operation_id, "p1_training_data.operation_id", length=32)
    _, scopes = require_p1_snapshot(ledger, store, snapshot_id)
    train = scopes["train"]
    for source in sorted(train["projections"]):
        ledger.use_source(source, "training", operation_id)
    ledger.use(train["roots"], "training", operation_id)
    require_p1_training_use(ledger, store, snapshot_id, operation_id)


def require_p1_training_use(
    ledger: CurationLedger, store: Any, snapshot_id: str, operation_id: str,
) -> None:
    _, scopes = require_p1_snapshot(ledger, store, snapshot_id)
    train = scopes["train"]
    ledger.require_training_use(snapshot_id, train["projections"], train["roots"], operation_id)


def draft_p1_allocation(
    ledger: CurationLedger,
    index: dict[str, Any],
    *,
    split_salt: str,
    allocation_policy: str = "preserve_existing",
) -> dict[str, Any]:
    """Explain a reusable engineering allocation without changing claims or uses.

    Prior roles and actual exposure constrain new versions. Unknown fresh groups
    may be TRAIN, but never acquire held-out independence by an ID/hash alone.
    """
    from collections import Counter

    from stpd.fullrun.data import split_run_fingerprints

    if not isinstance(split_salt, str) or not split_salt:
        raise BoundaryError("p1_training_data", "explicit_split_identity_required")
    if allocation_policy not in {"preserve_existing", "observed_groups_engineering"}:
        raise BoundaryError("p1_training_data", "unsupported_allocation_policy")
    groups = [g for g in index["groups"] if g["N"]]
    splits: dict[str, str] = {}
    details, conflicts = [], []
    fresh = []
    excluded = []
    with ledger.operations.transaction() as db:
        for group in groups:
            related = set(group["root_run_ids"])
            for key in group["keys"]:
                related.update(
                    r[0]
                    for r in db.execute(
                        "SELECT run FROM curation_fingerprints WHERE fingerprint=?", (key,)
                    )
                )
            related = ledger._groups(db, related)
            claims = ledger._claims(db, related)
            roles: set[str] = set()
            reasons = []
            if any(purpose == "gold" for purpose, _ in claims.values()):
                reasons.append("gold_reserved_data")
            for identity, (purpose, _) in claims.items():
                if purpose == "training":
                    roles.add("train")
                elif purpose == "test":
                    if identity.startswith("p1-N-dev:"):
                        roles.add("dev")
                    elif identity.startswith("p1-N-test:"):
                        roles.add("test")
                    else:
                        reasons.append("heldout_claim_role_unknown")
            exposed = any(
                db.execute(
                    "SELECT 1 FROM curation_uses WHERE run=? AND kind='training' UNION "
                    "SELECT 1 FROM curation_source_uses u JOIN curation_source_runs r "
                    "ON r.source=u.source WHERE r.run=? AND u.kind='training'",
                    (run, run),
                ).fetchone()
                for run in related
            )
            if exposed:
                roles.add("train")
            if len(roles) > 1:
                reasons.append("related_run_split_purpose_overlap")
            if reasons:
                conflicts.append(
                    {
                        "group_id": group["group_id"],
                        "codes": sorted(set(reasons)),
                        "claims": [
                            {"claim_id": k, "purpose": v[0]} for k, v in sorted(claims.items())
                        ],
                        "training_exposure": exposed,
                    }
                )
                splits[group["group_id"]] = "unassigned"
            elif roles:
                splits[group["group_id"]] = next(iter(roles))
            else:
                fresh.append(group)
            details.append(
                {
                    "group_id": group["group_id"],
                    "root_run_ids": group["root_run_ids"],
                    "related_run_ids": sorted(related),
                    "training_exposure": exposed,
                    "independence_metadata_complete": group["independence_metadata_complete"],
                    "basis": "prior_claim_or_permanent_exposure" if roles else "new_group",
                }
            )
        for point in index["points"]:
            row = db.execute(
                "SELECT action,reason FROM curation_annotations WHERE occurrence=? "
                "ORDER BY sequence DESC LIMIT 1",
                (_occurrence(point["source_decision_id"]),),
            ).fetchone()
            if row is not None and row[0] == "exclude":
                excluded.append(
                    {"source_decision_id": point["source_decision_id"], "reason": row[1]}
                )
    known = [g for g in fresh if g["independence_metadata_complete"]]
    allow_fresh_heldout = (
        len(known) == len(fresh) or allocation_policy == "observed_groups_engineering"
    )
    if not splits and allow_fresh_heldout and len(fresh) >= 3:
        splits.update(
            split_run_fingerprints(
                ((g["group_id"], g["group_id"]) for g in fresh),
                int(digest(split_salt.encode())[:16], 16),
            ).value()
        )
    else:
        for group in fresh:
            splits[group["group_id"]] = "train"
        if allow_fresh_heldout and fresh:
            ordered = sorted(fresh, key=lambda g: digest(json_bytes([split_salt, g["group_id"]])))
            # Fill absent held-out roles only from unclaimed, never-exposed groups.
            # Previously reserved roles never move to meet a ratio.
            prior_train = any(splits[g["group_id"]] == "train" for g in groups if g not in fresh)
            minimum_remaining = 0 if prior_train else 1
            for role in ("dev", "test"):
                if role not in splits.values() and len(ordered) > minimum_remaining:
                    splits[ordered.pop(0)["group_id"]] = role
    counts: Counter[str] = Counter()
    for point in index["points"]:
        counts[splits[point["group_id"]]] += 1
    blockers = []
    if conflicts:
        blockers.append("curation_allocation_conflicts")
    if not counts["train"] or not counts["dev"] or not counts["test"]:
        blockers.append("nonempty_train_dev_test_required_for_first_batch")
    if excluded:
        blockers.append("quality_excluded_N_requires_filtered_selection")
    return {
        "split_by_group": splits,
        "partition_N": dict(counts),
        "groups": details,
        "conflicts": conflicts,
        "quality_exclusions": excluded,
        "blockers": blockers,
        "ready_to_freeze": not blockers,
        "policy": allocation_policy,
        "isolation_claim": "observed_groups_only_not_statistical_independence",
        "historical_external_exposure": "unknown",
        "research_admission": "not_evaluated",
        "purpose_reserved": False,
        "training_use_recorded": False,
    }
