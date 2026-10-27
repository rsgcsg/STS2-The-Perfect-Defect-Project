"""P1 engineering allocation/use on the existing shared CurationLedger.

The eligibility index is keyed by the immutable named projection, not by an
invented canonical conversion of its raw original. All native sources remain
parents in the same Store. No new database, permission system or research
admission is created here.
"""

from __future__ import annotations

import json
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.json_boundary import digest as checked_digest
from spireagent.research_curation import CurationLedger
from stpd.p1.data import digest, json_bytes
from stpd.p1.dataset import inspect_projections, load_snapshot, projection_payload_role
from stpd.p1.spec import SNAPSHOT_SCHEMA


def _occurrence(decision: str) -> str:
    return digest(("p1-N-native:" + decision).encode())


def _scope(
    store: Any, snapshot_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest, value = load_snapshot(store, snapshot_id)
    if manifest.parameters.value().get("purpose") != "engineering":
        raise BoundaryError("p1_training_data", "engineering_snapshot_required")
    return value, _selection_scopes(value, snapshot_id)


def _selection_scopes(value: dict[str, Any], snapshot_id: str) -> dict[str, Any]:
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
    return scopes


def _require_purposes(
    ledger: CurationLedger, snapshot_id: str, scopes: dict[str, Any],
) -> None:
    with ledger.operations.transaction() as db:
        experiment = db.execute("SELECT model,parent FROM curation_p1_experiments WHERE snapshot=?",
                                (snapshot_id,)).fetchone()
        if experiment is not None:
            _check_model_holdouts(db, ledger, experiment[0], experiment[1], scopes)
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
                other_snapshot = identity.removeprefix("p1-N-dev:").removeprefix("p1-N-test:")
                if identity == scope["claim_id"] or (purpose != "gold" and (
                    experiment is not None or db.execute(
                        "SELECT 1 FROM curation_p1_experiments WHERE snapshot=?",
                        (other_snapshot,)).fetchone())):
                    continue
                if purpose == "gold":
                    raise BoundaryError("p1_training_data", "gold_reserved_data")
                if purpose != expected or (
                    split != "train" and not identity.startswith(f"p1-N-{split}:")
                ):
                    raise BoundaryError("p1_training_data", "related_run_split_purpose_overlap")
            if experiment is None and split != "train" and any(db.execute(
                "SELECT 1 FROM curation_uses WHERE run=? AND kind='training' AND reference NOT IN "
                "(SELECT reference FROM curation_p1_model_uses) UNION "
                "SELECT 1 FROM curation_source_uses u JOIN curation_source_runs r "
                "ON r.source=u.source WHERE r.run=? AND u.kind='training' AND u.reference NOT IN "
                "(SELECT reference FROM curation_p1_model_uses)", (run, run)
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


def reserve_p1_snapshot(ledger: CurationLedger, store: Any, snapshot_id: str, *,
                        experiment: dict[str, Any] | None = None) -> dict[str, Any]:
    """Explicit engineering purpose reservation; no model updates or formal admission.

    Existing claims retain failed preparation intent. Held-out role reservations
    use the ledger's existing unbound-claim facility, with a self-describing
    immutable snapshot reference. Only the actual TRAIN roots bind the training
    artifact; dev/test are not falsely marked as gradient training.
    """
    value, scopes = _scope(store, snapshot_id)
    if experiment is None:
        with ledger.operations.transaction() as db:
            if not db.execute("SELECT 1 FROM curation_p1_legacy_snapshots WHERE snapshot=?",
                              (snapshot_id,)).fetchone():
                raise BoundaryError("p1_training_data", "new_snapshot_requires_model_experiment")
    for source in value["sources"]:
        index_p1_projection(ledger, store, source["projection_id"])
    if experiment is not None:
        _require_formula_weights(store, value, scopes, experiment)
        _reserve_experiment(ledger, snapshot_id, scopes, experiment)
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
    ledger: CurationLedger, store: Any, snapshot_id: str, operation_id: str, *,
    loss_weighting: dict[str, Any] | None = None, initial_checkpoint: str | None = None,
) -> None:
    require_p1_experiment_input(ledger, store, snapshot_id, loss_weighting, initial_checkpoint)
    checked_digest(operation_id, "p1_training_data.operation_id", length=32)
    _, scopes = require_p1_snapshot(ledger, store, snapshot_id)
    train = scopes["train"]
    with ledger.operations.transaction() as db:
        model = db.execute("SELECT model,parent FROM curation_p1_experiments WHERE snapshot=?",
                           (snapshot_id,)).fetchone()
        if model is not None:
            _check_model_holdouts(db, ledger, model[0], model[1], scopes)
            _check_used_descendants(db, ledger, model[0], train["roots"])
            old = db.execute("SELECT snapshot FROM curation_p1_model_uses WHERE reference=?",
                             (operation_id,)).fetchone()
            if old is not None and old[0] != snapshot_id:
                raise BoundaryError("p1_training_data", "operation_experiment_conflict")
            db.execute("INSERT OR IGNORE INTO curation_p1_model_uses VALUES(?,?)",
                       (operation_id, snapshot_id))
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


def require_recorded_p1_training_use(
    ledger: CurationLedger, store: Any, snapshot_id: str, operation_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check an existing use without reprojecting its immutable source locally.

    Only the original, already admitted operation can be reused. This creates no
    use or permission and does not verify the source data for computation. The
    remote calibration must still run the ordinary full Snapshot/Run reader
    before any forward/update; no SnapshotReader accepts this metadata as proof.
    Current purpose, related exposure and exclusion checks remain local.
    """
    checked_digest(operation_id, "p1_training_data.operation_id", length=32)
    manifest = store.get_manifest(snapshot_id)
    info = manifest.parameters.value()
    if (manifest.kind != "dataset" or info.get("schema") != SNAPSHOT_SCHEMA
            or info.get("purpose") != "engineering"):
        raise BoundaryError("p1_training_data", "engineering_snapshot_required")
    value = json.loads(b"".join(store.read_payload(manifest.payload("selection"))))
    if (digest(json_bytes(value)) != info.get("selection_sha256")
            or value.get("schema") != SNAPSHOT_SCHEMA):
        raise BoundaryError("p1_training_data", "snapshot_selection_binding_mismatch")
    scopes = _selection_scopes(value, snapshot_id)
    _require_purposes(ledger, snapshot_id, scopes)
    train = scopes["train"]
    ledger.require_training_use(snapshot_id, train["projections"], train["roots"], operation_id)
    return value, scopes


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
    if allocation_policy == "pool":
        return _pool_allocation(ledger, index)
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



def _pool_allocation(ledger: CurationLedger, index: dict[str, Any]) -> dict[str, Any]:
    excluded = []
    with ledger.operations.transaction() as db:
        for p in index["points"]:
            row = db.execute("SELECT action,reason FROM curation_annotations WHERE occurrence=? "
                             "ORDER BY sequence DESC LIMIT 1",
                             (_occurrence(p["source_decision_id"]),)).fetchone()
            if row and row[0] == "exclude":
                excluded.append({"source_decision_id": p["source_decision_id"], "reason": row[1]})
    return {"split_by_group": {g["group_id"]: "pool" for g in index["groups"] if g["N"]},
            "partition_N": {"pool": index["unique_N"]}, "groups": [], "conflicts": [],
            "quality_exclusions": excluded, "blockers": [], "ready_to_freeze": False,
            "policy": "pool", "purpose_reserved": False, "training_use_recorded": False}


def _model_chain(db: Any, model: str, parent: str | None) -> set[str]:
    chain = {model}
    while parent is not None:
        if parent in chain:
            raise BoundaryError("p1_training_data", "model_ancestry_cycle")
        chain.add(parent)
        row = db.execute("SELECT parent FROM curation_p1_experiments WHERE model=?",
                         (parent,)).fetchone()
        if row is None:
            raise BoundaryError("p1_training_data", "parent_model_usage_history_required")
        parent = row[0]
    return chain


def _check_model_holdouts(db: Any, ledger: CurationLedger, model: str,
                          parent: str | None, scopes: dict[str, Any]) -> None:
    chain = _model_chain(db, model, parent)
    exposed: set[str] = set()
    for ancestor in chain:
        exposed.update(r[0] for r in db.execute(
            "SELECT r.run FROM curation_p1_experiment_runs r "
            "JOIN curation_p1_experiments e ON e.snapshot=r.snapshot "
            "JOIN curation_p1_model_uses u ON u.snapshot=e.snapshot "
            "WHERE e.model=? AND r.role='train'", (ancestor,)))
    related_train = ledger._groups(db, scopes["train"]["roots"])
    for role in ("dev", "test"):
        if role in scopes:
            related = ledger._groups(db, scopes[role]["roots"])
            if related & (exposed | related_train):
                raise BoundaryError("p1_training_data", "model_ancestor_trained_exam_run")


def _reserve_experiment(ledger: CurationLedger, snapshot: str, scopes: dict[str, Any],
                        experiment: dict[str, Any], *, historical: bool = False) -> None:
    import time

    if (not isinstance(experiment, dict) or set(experiment) != {
            "model_id", "parent_model_id", "configuration_id",
            "weight_definition", "weights_by_root"}
            or not isinstance(experiment["model_id"], str) or not experiment["model_id"].strip()
            or len(experiment["model_id"]) > 200
            or (experiment["parent_model_id"] is not None and
                not isinstance(experiment["parent_model_id"], str))):
        raise BoundaryError("p1_training_data", "explicit_model_experiment_required")
    checked_digest(experiment["configuration_id"], "p1_experiment.configuration")
    from stpd.p1.weighting import SCHEMA, checked_weights
    checked_weights({"schema": SCHEMA, "rule_id": experiment["configuration_id"],
                     "definition": experiment["weight_definition"],
                     "weights_by_root": experiment["weights_by_root"]},
                    {"points": [{"root_run_id": r, "split": "train"}
                                for r in scopes["train"]["roots"]]})
    raw = json.dumps(experiment, sort_keys=True)
    with ledger.operations.transaction() as db:
        if not historical:
            _require_weight_approval(db, experiment["weight_definition"])
        _check_scope_quality(db, scopes)
        _check_model_holdouts(db, ledger, experiment["model_id"],
                              experiment["parent_model_id"], scopes)
        old = db.execute("SELECT snapshot,configuration FROM curation_p1_experiments WHERE model=?",
                         (experiment["model_id"],)).fetchone()
        if old is not None and tuple(old) != (snapshot, raw):
            raise BoundaryError("p1_training_data",
                                "model_experiment_is_immutable_use_new_model_id")
        db.execute("INSERT OR IGNORE INTO curation_p1_experiments VALUES(?,?,?,?,?)",
                   (snapshot, experiment["model_id"], experiment["parent_model_id"],
                    raw, time.time()))
        for role, scope in scopes.items():
            db.executemany("INSERT OR IGNORE INTO curation_p1_experiment_runs VALUES(?,?,?,?)",
                           [(snapshot, root, role, experiment["weights_by_root"].get(root))
                            for root in sorted(scope["roots"])])


def draft_model_allocation(ledger: CurationLedger, index: dict[str, Any], *, model_id: str,
                           parent_model_id: str | None, split_salt: str,
                           dev_fraction: float = 0.1, test_fraction: float = 0.1,
                           split_by_group: dict[str, str] | None = None) -> dict[str, Any]:
    """Select this experiment's roles; old reservations never label the pool."""
    import math
    from collections import Counter

    if (not model_id or not split_salt or any(type(x) not in (int, float) or
            not math.isfinite(x) or not 0 <= x < 1 for x in (dev_fraction, test_fraction))
            or dev_fraction + test_fraction >= 1):
        raise BoundaryError("p1_training_data", "valid_experiment_split_required")
    groups = [g for g in index["groups"] if g["N"]]
    with ledger.operations.transaction() as db:
        _check_scope_quality(db, {"pool": {"points": index["points"]}})
        chain = _model_chain(db, model_id, parent_model_id)
        exposed: set[str] = set()
        for ancestor in chain:
            exposed.update(r[0] for r in db.execute(
                "SELECT r.run FROM curation_p1_experiment_runs r "
                "JOIN curation_p1_experiments e ON e.snapshot=r.snapshot "
                "JOIN curation_p1_model_uses u ON u.snapshot=e.snapshot "
                "WHERE e.model=? AND r.role='train'", (ancestor,)))
        eligible = []
        for g in groups:
            related = ledger._groups(db, g["root_run_ids"])
            for key in g["keys"]:
                related.update(r[0] for r in db.execute(
                    "SELECT run FROM curation_fingerprints WHERE fingerprint=?", (key,)))
            related = ledger._groups(db, related)
            if any(p == "gold" for p, _ in ledger._claims(db, related).values()):
                raise BoundaryError("p1_training_data", "gold_reserved_data")
            if not related & exposed:
                eligible.append(g["group_id"])
        if split_by_group is None:
            ordered = sorted(eligible, key=lambda g: digest(json_bytes([split_salt, g])))
            dev_n, test_n = (round(len(groups) * x) for x in (dev_fraction, test_fraction))
            if len(ordered) < dev_n + test_n or dev_n + test_n >= len(groups):
                raise BoundaryError("p1_training_data", "insufficient_untrained_exam_groups")
            splits = {g["group_id"]: "train" for g in groups}
            for gid in ordered[:dev_n]:
                splits[gid] = "dev"
            for gid in ordered[dev_n:dev_n + test_n]:
                splits[gid] = "test"
        else:
            splits = dict(split_by_group)
            if set(splits) != {g["group_id"] for g in groups} or any(
                    s not in {"train", "dev", "test"} for s in splits.values()):
                raise BoundaryError("p1_training_data", "exact_group_partition_required")
            if any(s != "train" and g not in eligible for g, s in splits.items()):
                raise BoundaryError("p1_training_data", "model_ancestor_trained_exam_run")
    counts: Counter[str] = Counter()
    for g in groups:
        counts[splits[g["group_id"]]] += g["N"]
    blockers = [] if counts["train"] and counts["dev"] else ["nonempty_train_dev_required"]
    return {"split_by_group": splits, "partition_N": dict(counts), "groups": groups,
            "blockers": blockers, "conflicts": [], "ready_to_freeze": not blockers,
            "isolation_claim": "model_ancestry_no_recorded_training_exposure; observed_groups_only",
            "policy": "per_training"}


def adopt_p1_model_history(ledger: CurationLedger, store: Any, snapshot_id: str,
                           operation_id: str, experiment: dict[str, Any]) -> None:
    """Bind a pre-ledger model to its verified existing training use, never guess it."""
    _, scopes = require_recorded_p1_training_use(ledger, store, snapshot_id, operation_id)
    _reserve_experiment(ledger, snapshot_id, scopes, experiment, historical=True)
    with ledger.operations.transaction() as db:
        old = db.execute("SELECT snapshot FROM curation_p1_model_uses WHERE reference=?",
                         (operation_id,)).fetchone()
        if old is not None and old[0] != snapshot_id:
            raise BoundaryError("p1_training_data", "operation_experiment_conflict")
        db.execute("INSERT OR IGNORE INTO curation_p1_model_uses VALUES(?,?)",
                   (operation_id, snapshot_id))



def _require_weight_approval(db: Any, definition: dict[str, Any]) -> None:
    raw = json_bytes(definition)
    row = db.execute("SELECT definition FROM curation_p1_weight_approvals WHERE id=?",
                     (digest(raw),)).fetchone()
    if row is None or row[0] != raw.decode():
        raise BoundaryError("p1_training_data", "weight_rule_approval_required")


def _check_scope_quality(db: Any, scopes: dict[str, Any]) -> None:
    for scope in scopes.values():
        for point in scope["points"]:
            row = db.execute("SELECT action FROM curation_annotations WHERE occurrence=? "
                             "ORDER BY sequence DESC LIMIT 1",
                             (_occurrence(point["source_decision_id"]),)).fetchone()
            if row and row[0] == "exclude":
                raise BoundaryError("p1_training_data", "snapshot_selected_excluded_N")


def require_p1_experiment_input(ledger: CurationLedger, store: Any, snapshot: str,
                                 loss_weighting: dict[str, Any] | None,
                                 initial_checkpoint: str | None) -> None:
    """Bind the actual training request to its approved weights and model parent."""
    from stpd.p1.weighting import SCHEMA

    with ledger.operations.transaction() as db:
        row = db.execute("SELECT configuration FROM curation_p1_experiments WHERE snapshot=?",
                         (snapshot,)).fetchone()
        legacy = db.execute("SELECT 1 FROM curation_p1_legacy_snapshots WHERE snapshot=?",
                            (snapshot,)).fetchone()
    if row is None:
        if legacy is None:
            raise BoundaryError("p1_training_data", "new_snapshot_requires_model_experiment")
        if loss_weighting is not None:
            _require_legacy_weights(store, snapshot, loss_weighting)
        return
    experiment = json.loads(row[0])
    with ledger.operations.transaction() as db:
        _require_weight_approval(db, experiment["weight_definition"])
    expected = {"schema": SCHEMA, "rule_id": experiment["configuration_id"],
                "definition": experiment["weight_definition"],
                "weights_by_root": experiment["weights_by_root"]}
    if loss_weighting != expected:
        raise BoundaryError("p1_training_data", "experiment_training_weights_mismatch")
    parent = experiment["parent_model_id"]
    if parent is None:
        if initial_checkpoint is not None:
            raise BoundaryError("p1_training_data", "fresh_model_requires_fresh_initialization")
    else:
        if initial_checkpoint is None:
            raise BoundaryError("p1_training_data", "parent_model_checkpoint_required")
        checkpoint = store.get_manifest(initial_checkpoint)
        if checkpoint.kind != "checkpoint":
            raise BoundaryError("p1_training_data", "parent_model_checkpoint_required")
        run = store.get_manifest(checkpoint.parent("run"))
        operation = run.parameters.value().get("operation_id")
        with ledger.operations.transaction() as db:
            actual_parent = db.execute(
                "SELECT e.model FROM curation_p1_model_uses u JOIN curation_p1_experiments e "
                "ON e.snapshot=u.snapshot WHERE u.reference=?", (operation,)).fetchone()
        if actual_parent is None or actual_parent[0] != parent:
            raise BoundaryError("p1_training_data", "parent_checkpoint_usage_history_mismatch")


def _require_legacy_weights(store: Any, snapshot: str, weighting: dict[str, Any]) -> None:
    """Legacy unit control or the original progress rule; changed rules need an experiment."""
    from stpd.p1.cohort import (
        RULE_ID,
        WEIGHT_PARAMETERS,
        bind_finish_floors,
        projection_facts,
        run_weight,
    )
    from stpd.p1.dataset import load_projection

    weights = weighting.get("weights_by_root", {})
    if weights and all(w == 1 for w in weights.values()) and "definition" not in weighting:
        return
    definition = weighting.get("definition") or {}
    if definition.get("rule") != RULE_ID or definition.get("parameters") != WEIGHT_PARAMETERS:
        raise BoundaryError("p1_training_data", "new_weight_rule_requires_model_experiment")
    manifest = store.get_manifest(snapshot)
    facts = {}
    for parent in manifest.parents:
        p = load_projection(store, parent.artifact_id)
        facts[p.root] = projection_facts(p)
    bound = bind_finish_floors(facts)
    if any(root not in bound or weight != run_weight(bound[root])
           for root, weight in weights.items()):
        raise BoundaryError("p1_training_data", "legacy_weights_do_not_match_original_rule")



def _require_formula_weights(store: Any, value: dict[str, Any], scopes: dict[str, Any],
                             experiment: dict[str, Any]) -> None:
    from stpd.p1.cohort import (
        A10_PROGRESS_RULE,
        a10_progress_weight,
        bind_finish_floors,
        projection_facts,
        run_weight,
        weight_definition,
    )
    from stpd.p1.dataset import load_projection

    definition = experiment.get("weight_definition")
    if (not isinstance(definition, dict) or not isinstance(definition.get("rule"), str)
            or definition != weight_definition(definition["rule"])):
        raise BoundaryError("p1_training_data", "supported_weight_definition_required")
    facts: dict[str, dict[str, Any]] = {}
    for source in value["sources"]:
        projection = load_projection(store, source["projection_id"])
        fact = projection_facts(projection)
        if projection.root in facts and facts[projection.root] != fact:
            raise BoundaryError("p1_training_data", "conflicting_run_facts_select_one_projection")
        facts[projection.root] = fact
    bound = bind_finish_floors(facts)
    function = a10_progress_weight if definition["rule"] == A10_PROGRESS_RULE else run_weight
    expected = {root: function(bound[root]) for root in scopes["train"]["roots"]}
    if experiment.get("weights_by_root") != expected:
        raise BoundaryError("p1_training_data", "experiment_weights_do_not_match_approved_formula")



def _check_used_descendants(db: Any, ledger: CurationLedger, model: str,
                             training_roots: set[str]) -> None:
    """A deferred ancestor use cannot contaminate an already-used child's exam."""
    descendants = {model}
    rows = db.execute("SELECT model,parent,snapshot FROM curation_p1_experiments").fetchall()
    while True:
        found = {r[0] for r in rows if r[1] in descendants}
        if found <= descendants:
            break
        descendants.update(found)
    train = ledger._groups(db, training_roots)
    for child, _, snapshot in rows:
        if child == model or child not in descendants or not db.execute(
                "SELECT 1 FROM curation_p1_model_uses WHERE snapshot=?", (snapshot,)).fetchone():
            continue
        exams = {r[0] for r in db.execute("SELECT run FROM curation_p1_experiment_runs "
                                        "WHERE snapshot=? AND role!='train'", (snapshot,))}
        if train & ledger._groups(db, exams):
            raise BoundaryError("p1_training_data",
                                "ancestor_use_would_contaminate_descendant_exam")
