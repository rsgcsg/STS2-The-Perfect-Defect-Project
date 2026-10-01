"""Apply research reservations at managed byte/compute boundaries, including old exports."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from typing import Any

from spireagent.artifact_contracts import Manifest
from spireagent.hub.access import project_member, require_artifact_access
from spireagent.hub.collections import CollectionAccess
from spireagent.hub.console_auth import ConsolePrincipal
from spireagent.hub.curation import CurationLedger, _hub_inventory_pending
from spireagent.hub.database import Operations
from spireagent.hub.uploads import UploadService
from spireagent.json_boundary import BoundaryError, digest, json_bytes
from spireagent.storage.store import ArtifactStore

CURATED_DATASET_SCHEMA = "stpd/curated-decision-dataset-v1"
RECEIVED_SOURCE_SCHEMA = "stpd/received-bundle-v1"
LEGACY_DECISION_DATASET_SCHEMA = "stpd/decision-dataset-v1"
LEGACY_DECISION_UNION_SCHEMA = "stpd/decision-union-v1"
LEGACY_SELECTION_RULE_SCHEMA = "stpd/decision-selection-v1"
MAX_LINEAGE_NODES = 512
MAX_SOURCE_INDEXES = 512
MAX_OVERLAP_RUNS = 50_000
MAX_RUN_GROUP_ROWS = 100_000
MAX_OVERLAP_SQLITE_VM_STEPS = 5_000_000
MAX_RUN_GROUP_SQLITE_VM_STEPS = 5_000_000
MAX_LEGACY_DATASETS = 256
MAX_LEGACY_DEPTH = 8
MAX_LEGACY_SOURCES = 100


def _bounded_rows(
    db: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...] = (),
    *,
    vm_steps: int = MAX_OVERLAP_SQLITE_VM_STEPS,
) -> list[sqlite3.Row] | None:
    """Materialize only capped query results and interrupt excessive SQLite work."""
    remaining = [max(1, vm_steps // 1_000)]

    def abort_on_budget() -> int:
        remaining[0] -= 1
        return int(remaining[0] <= 0)

    db.set_progress_handler(abort_on_budget, 1_000)
    try:
        return db.execute(query, parameters).fetchall()
    except sqlite3.OperationalError as error:
        if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT:
            return None
        raise
    finally:
        db.set_progress_handler(None, 0)


def _legacy_source_superset(
    root: Manifest, nodes: tuple[Manifest, ...]
) -> set[str] | None:
    """Return only a validated legacy decision artifact's complete source closure."""
    node_by_id = {item.artifact_id: item for item in nodes}
    source_ids: set[str] = set()
    visited: dict[str, int] = {}
    visiting: set[str] = set()

    def visit(item: Manifest, depth: int) -> int | None:
        if depth > MAX_LEGACY_DEPTH or item.artifact_id in visiting:
            return None
        prior_height = visited.get(item.artifact_id)
        if prior_height is not None:
            return prior_height if depth + prior_height <= MAX_LEGACY_DEPTH else None
        if len(visited) + len(visiting) >= MAX_LEGACY_DATASETS:
            return None
        if item.kind != "dataset":
            return None
        info = item.parameters.value()
        schema = info.get("schema")
        if schema not in {LEGACY_DECISION_DATASET_SCHEMA, LEGACY_DECISION_UNION_SCHEMA}:
            return None
        try:
            logical_id = digest(info.get("logical_id"), "decision_dataset.logical_id")
            records = info.get("records")
            if (
                not logical_id
                or type(records) is not int
                or records <= 0
                or info.get("scope") != "platform_verified"
            ):
                return None
        except BoundaryError:
            return None
        rules = info.get("rules")
        if not isinstance(rules, dict) or rules.get("schema") != LEGACY_SELECTION_RULE_SCHEMA:
            return None
        if not 1 <= len(item.parents) <= 100:
            return None

        visiting.add(item.artifact_id)
        height = 0
        for parent in item.parents:
            parent_node = node_by_id.get(parent.artifact_id)
            if parent_node is None:
                visiting.remove(item.artifact_id)
                return None
            if schema == LEGACY_DECISION_DATASET_SCHEMA:
                if parent.role != "source_" + parent.artifact_id:
                    visiting.remove(item.artifact_id)
                    return None
                source_info = parent_node.parameters.value()
                if (
                    parent_node.kind != "evidence"
                    or source_info.get("schema") != RECEIVED_SOURCE_SCHEMA
                    or source_info.get("disposition") != "verified"
                ):
                    visiting.remove(item.artifact_id)
                    return None
                source_ids.add(parent_node.artifact_id)
            else:
                if parent.role != "dataset_" + parent.artifact_id:
                    visiting.remove(item.artifact_id)
                    return None
                child_height = visit(parent_node, depth + 1)
                if child_height is None:
                    visiting.remove(item.artifact_id)
                    return None
                height = max(height, child_height + 1)
        visiting.remove(item.artifact_id)
        if depth + height > MAX_LEGACY_DEPTH:
            return None
        visited[item.artifact_id] = height
        return height

    if visit(root, 0) is None or not source_ids or len(source_ids) > MAX_LEGACY_SOURCES:
        return None
    if set(node_by_id) != set(visited) | source_ids:
        return None
    return source_ids


def guarded_runs(
    operations: Operations,
    store: ArtifactStore,
    manifest: Manifest,
    *,
    training: bool = False,
    nodes: tuple[Manifest, ...] = (),
    sources: set[str] | None = None,
) -> set[str]:
    ledger = CurationLedger(operations)
    pending = [manifest]
    seen = set()
    runs: set[str] = set()
    has_gold = ledger.has_gold()
    memo = {item.artifact_id: item for item in nodes}

    def parent(identity: str) -> Manifest:
        if identity not in memo:
            memo[identity] = store.get_manifest(identity)
        return memo[identity]

    while pending:
        item = pending.pop()
        if item.artifact_id in seen:
            continue
        seen.add(item.artifact_id)
        if len(seen) > 512:
            raise BoundaryError("curation", "lineage_limit")
        info = item.parameters.value()
        if info.get("purpose") == "gold" or (training and info.get("purpose") == "test"):
            raise BoundaryError(
                "curation", "gold_reserved_data" if not training else "held_out_data_cannot_train"
            )
        selection = ledger.dataset(item.artifact_id)
        if selection is not None:
            purpose, selected_runs = selection
            if purpose == "gold" or (training and purpose == "test"):
                raise BoundaryError(
                    "curation", "held_out_data_cannot_train" if training else "gold_reserved_data"
                )
            runs.update(selected_runs)
            # Only the compact current receipt omits unselected source context.
            # Legacy reports can contain complete source accounting.
            if info.get("schema") == "stpd/curated-decision-dataset-v1":
                continue
        if info.get("schema") == "stpd/received-bundle-v1":
            if sources is not None:
                sources.add(item.artifact_id)
            source_runs = ledger.source_runs(item.artifact_id)
            if source_runs is None:
                if has_gold:
                    raise BoundaryError("curation", "source_isolation_index_pending")
            else:
                runs.update(source_runs)
            continue
        pending.extend(parent(p.artifact_id) for p in item.parents)
    with operations.transaction() as db:
        related = ledger._groups(db, runs)
        if any(purpose == "gold" for purpose, _ in ledger._claims(db, related).values()):
            raise BoundaryError("curation", "gold_reserved_data")
    return runs


def record_use(
    operations: Operations,
    store: ArtifactStore,
    manifest: Manifest,
    kind: str,
    *,
    nodes: tuple[Manifest, ...] = (),
) -> None:
    sources: set[str] = set()
    runs = guarded_runs(
        operations, store, manifest, training=kind == "training", nodes=nodes, sources=sources
    )
    ledger = CurationLedger(operations)
    ledger.use(runs, kind, manifest.artifact_id)
    for source in sources:
        ledger.use_source(source, kind, manifest.artifact_id)


def overlap_metadata(
    service: UploadService,
    principal: ConsolePrincipal,
    candidate_id: str,
    gold_id: str,
) -> dict[str, Any]:
    """Read current indexed overlap without opening payloads or recording a use.

    The caller has already re-authorized the member identity. The protected Gold
    scope comes from every current owner claim; only the candidate's immutable
    manifest closure is read, and only for its source identities.
    """
    from spireagent.hub.console_index import timestamp
    candidate_id = digest(candidate_id, "curation_overlap.candidate")
    gold_id = digest(gold_id, "curation_overlap.gold")
    if not project_member(principal):
        raise BoundaryError("hub", "unauthorized")
    candidate_scope = "unknown"

    unknown: dict[str, Any] = {
        "schema": "stpd/curation-overlap-v1",
        "observed_at": timestamp(),
        "scope": "candidate_against_owner_gold_reservations",
        "status": "unknown",
        "candidate_scope": "unknown",
        "findings": {
            "source": {"status": "unknown", "count": None},
            "run": {"status": "unknown", "count": None},
            "run_group": {"status": "unknown", "count": None},
        },
        "coverage": {
            "candidate_source": "unknown",
            "gold_source": "unknown",
            "verified_source_inventory": "unknown",
            "run_group": "unknown",
            "physical_game": "unknown",
        },
        "commitments": None,
        "reasons": [],
        "limits": [
            "This metadata is not training authorization or a download/use grant.",
            "The owner index has no physical-game identity; independence remains unknown.",
        ],
    }

    def unknown_for(reason: str) -> dict[str, Any]:
        result = deepcopy(unknown)
        result["candidate_scope"] = candidate_scope
        result["reasons"] = [reason]
        return result

    def candidate_unavailable() -> dict[str, Any]:
        result = deepcopy(unknown)
        result["reasons"] = ["candidate_unavailable"]
        return result

    # The member catalog is the existing discoverability gate. Do not retrieve or
    # classify an artifact until its ID is present in that authorized catalog scope.
    try:
        with closing(service.console_index.read()) as scope_db:
            candidate_visible = scope_db.execute(
                "SELECT 1 FROM console_artifacts WHERE artifact_id=?", (candidate_id,)
            ).fetchone()
        if candidate_visible is None:
            return candidate_unavailable()
        candidate = service.store.get_manifest(candidate_id)
        candidate_nodes = require_artifact_access(
            candidate, project_member=True, store=service.store
        )
    except (BoundaryError, OSError, ValueError, KeyError, TypeError, sqlite3.DatabaseError):
        # Keep absent, sealed, and otherwise inaccessible candidate IDs indistinguishable.
        return candidate_unavailable()
    if candidate.kind != "dataset":
        return unknown_for("candidate_not_supported_dataset")

    candidate_info = candidate.parameters.value()
    candidate_schema = candidate_info.get("schema")
    legacy_candidate = False
    # Follow only the versioned source/dataset parent edges emitted by the owner.
    # This never opens a selection payload or infers relationships from filenames.
    node_by_id = {item.artifact_id: item for item in candidate_nodes}
    source_ids: set[str] = set()
    if candidate_schema == CURATED_DATASET_SCHEMA:
        candidate_scope = "owner_claim_runs"
        if candidate_info.get("purpose") != "training":
            return unknown_for("candidate_not_supported_training_dataset")
        pending = [candidate]
        visited: set[str] = set()
        lineage_complete = True
        while pending:
            item = pending.pop()
            if item.artifact_id in visited:
                continue
            visited.add(item.artifact_id)
            if len(visited) > MAX_LINEAGE_NODES:
                lineage_complete = False
                break
            info = item.parameters.value()
            if item.kind != "dataset" or info.get("schema") != CURATED_DATASET_SCHEMA:
                lineage_complete = False
                break
            if not item.parents or len(item.parents) > 100:
                lineage_complete = False
                break
            for parent in item.parents:
                if parent.role == "source_" + parent.artifact_id:
                    source = node_by_id.get(parent.artifact_id)
                    if source is None:
                        lineage_complete = False
                        break
                    if (
                        source.kind != "evidence"
                        or source.parameters.value().get("schema") != RECEIVED_SOURCE_SCHEMA
                        or source.parameters.value().get("disposition") != "verified"
                    ):
                        lineage_complete = False
                        break
                    source_ids.add(source.artifact_id)
                elif parent.role == "dataset_" + parent.artifact_id:
                    parent_manifest = node_by_id.get(parent.artifact_id)
                    if parent_manifest is None:
                        lineage_complete = False
                        break
                    pending.append(parent_manifest)
                else:
                    lineage_complete = False
                    break
            if not lineage_complete:
                break
            if len(source_ids) > MAX_SOURCE_INDEXES:
                lineage_complete = False
                break
        if not lineage_complete or not source_ids:
            result = unknown_for("candidate_source_lineage_incomplete")
            result["coverage"]["candidate_source"] = "incomplete"
            return result
    elif candidate_schema in {
        LEGACY_DECISION_DATASET_SCHEMA, LEGACY_DECISION_UNION_SCHEMA
    }:
        legacy_candidate = True
        candidate_scope = "source_superset"
        legacy_sources = _legacy_source_superset(candidate, candidate_nodes)
        if legacy_sources is None:
            result = unknown_for("legacy_parent_contract_unproven")
            result["coverage"]["candidate_source"] = "incomplete"
            return result
        source_ids = legacy_sources
    else:
        return unknown_for("candidate_not_supported_dataset")

    # The target Gold is resolved only from its redacted catalog row and owner claim;
    # no Gold manifest, parent list, payload descriptor, or payload is opened.
    try:
        with closing(service.console_index.read()) as db:
            candidate_row = db.execute(
                "SELECT 1 FROM console_artifacts WHERE artifact_id=? AND kind='dataset'",
                (candidate_id,),
            ).fetchone()
            if candidate_row is None:
                return candidate_unavailable()
            gold_row = db.execute(
                "SELECT kind,summary FROM console_artifacts WHERE artifact_id=?",
                (gold_id,),
            ).fetchone()
            if gold_row is None or gold_row["kind"] != "dataset":
                return unknown_for("gold_unavailable")
            gold_summary = json.loads(gold_row["summary"])
            if gold_summary.get("metadata", {}).get("purpose") != "gold":
                return unknown_for("gold_unavailable")
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            required = {
                "curation_sources", "curation_source_runs", "curation_exact_source_index",
                "curation_fingerprints", "curation_claims", "curation_claim_runs", "uploads",
                "collection_sharing",
            }
            if not required <= tables:
                return unknown_for("owner_index_unavailable")

            # Reuse CollectionAccess's current upload/receipt/share checks against this
            # same read snapshot. Missing upload rows and explicit withdrawals are not
            # interpreted as public/shared, and this path never calls its use-recording API.
            for source_id in source_ids:
                try:
                    CollectionAccess.manifests_for_evidence(db, service.store, source_id)
                except (
                    BoundaryError, OSError, ValueError, KeyError, TypeError,
                    sqlite3.DatabaseError,
                ):
                    return candidate_unavailable()

            claim_rows = _bounded_rows(
                db,
                "SELECT id,purpose,artifact FROM curation_claims WHERE purpose='gold' LIMIT ?",
                (MAX_OVERLAP_RUNS + 1,),
            )
            candidate_claims = _bounded_rows(
                db,
                "SELECT id,purpose,artifact FROM curation_claims WHERE artifact=? LIMIT 2",
                (candidate_id,),
            )
            target_claims = _bounded_rows(
                db,
                "SELECT id FROM curation_claims "
                "WHERE purpose='gold' AND artifact=? LIMIT 2",
                (gold_id,),
            )
            if claim_rows is None or candidate_claims is None or target_claims is None:
                return unknown_for("owner_index_query_limit")
            gold_claims_complete = len(claim_rows) <= MAX_OVERLAP_RUNS
            claims = sorted(claim_rows[:MAX_OVERLAP_RUNS], key=lambda row: row["id"])
            if len(target_claims) != 1:
                return unknown_for("gold_unavailable")

            candidate_claim_id: str | None = None
            if legacy_candidate:
                # A legacy selection has no owner claim. Do not invent one: its full
                # source closure will supply a conservative run superset below.
                if candidate_claims:
                    return unknown_for("legacy_candidate_claim_conflict")
            else:
                if len(candidate_claims) != 1 or candidate_claims[0]["purpose"] != "training":
                    return unknown_for("candidate_owner_claim_missing")
                candidate_claim_id = candidate_claims[0]["id"]

            source_states: dict[str, bool] = {}
            for source_id in source_ids:
                source_row = db.execute(
                    "SELECT complete FROM curation_sources WHERE id=?", (source_id,)
                ).fetchone()
                exact = db.execute(
                    "SELECT 1 FROM curation_exact_source_index WHERE source=?", (source_id,)
                ).fetchone()
                source_states[source_id] = (
                    source_row is not None and source_row[0] == 1 and exact is not None
                )

            source_ids_json = json.dumps(sorted(source_ids))
            exact_source_rows = _bounded_rows(
                db,
                "WITH candidate_sources(source) AS ("
                "SELECT CAST(value AS TEXT) FROM json_each(?)) "
                "SELECT DISTINCT s.source FROM candidate_sources x "
                "JOIN curation_source_runs s ON s.source=x.source "
                "WHERE EXISTS (SELECT 1 FROM curation_claim_runs r "
                "JOIN curation_claims c ON c.id=r.claim "
                "WHERE c.purpose='gold' AND r.run=s.run) LIMIT ?",
                (source_ids_json, MAX_SOURCE_INDEXES + 1),
            )
            exact_sources = (
                {row[0] for row in exact_source_rows}
                if exact_source_rows is not None else set()
            )
            source_match_complete = (
                exact_source_rows is not None and len(exact_source_rows) <= MAX_SOURCE_INDEXES
            )

            gold_pair_rows = _bounded_rows(
                db,
                "SELECT r.claim,r.run FROM curation_claim_runs r "
                "JOIN curation_claims c ON c.id=r.claim WHERE c.purpose='gold' LIMIT ?",
                (MAX_OVERLAP_RUNS + 1,),
            )
            gold_run_query_limited = gold_pair_rows is None
            gold_run_pairs_complete = (
                gold_pair_rows is not None and len(gold_pair_rows) <= MAX_OVERLAP_RUNS
            )
            gold_runs = {row["run"] for row in gold_pair_rows or []}
            if not gold_runs and gold_run_pairs_complete:
                return unknown_for("gold_owner_membership_missing")

            if legacy_candidate:
                candidate_run_rows = _bounded_rows(
                    db,
                    "WITH candidate_sources(source) AS ("
                    "SELECT CAST(value AS TEXT) FROM json_each(?)) "
                    "SELECT DISTINCT s.run FROM candidate_sources x "
                    "JOIN curation_source_runs s ON s.source=x.source LIMIT ?",
                    (source_ids_json, MAX_OVERLAP_RUNS + 1),
                )
                candidate_run_query_limited = candidate_run_rows is None
                candidate_runs = {row[0] for row in candidate_run_rows or []}
                candidate_runs_complete = (
                    candidate_run_rows is not None
                    and len(candidate_run_rows) <= MAX_OVERLAP_RUNS
                )
                if not candidate_runs and candidate_runs_complete:
                    return unknown_for("candidate_source_run_index_missing")
            else:
                candidate_run_rows = _bounded_rows(
                    db,
                    "SELECT run FROM curation_claim_runs WHERE claim=? LIMIT ?",
                    (candidate_claim_id, MAX_OVERLAP_RUNS + 1),
                )
                candidate_run_query_limited = candidate_run_rows is None
                candidate_runs = {row[0] for row in candidate_run_rows or []}
                candidate_runs_complete = (
                    candidate_run_rows is not None
                    and len(candidate_run_rows) <= MAX_OVERLAP_RUNS
                )
                if not candidate_runs and candidate_runs_complete:
                    return unknown_for("candidate_owner_membership_missing")
            source_complete = all(source_states.values())
            all_source_rows = _bounded_rows(
                db,
                "SELECT 1 FROM curation_sources s LEFT JOIN curation_exact_source_index e "
                "ON e.source=s.id WHERE s.complete!=1 OR e.source IS NULL LIMIT 1",
            )
            all_source_indexes_complete = all_source_rows is not None and not all_source_rows
            inventory_pending = _hub_inventory_pending(db)
            fingerprint_rows = _bounded_rows(
                db,
                "SELECT 1 FROM curation_source_runs s "
                "JOIN curation_sources c ON c.id=s.source "
                "LEFT JOIN curation_fingerprints f ON f.run=s.run "
                "WHERE c.complete=1 AND f.run IS NULL LIMIT 1",
            )
            missing_fingerprints = fingerprint_rows is None or bool(fingerprint_rows)
            gold_source_rows = _bounded_rows(
                db,
                "SELECT r.run FROM curation_claim_runs r "
                "JOIN curation_claims c ON c.id=r.claim WHERE c.purpose='gold' "
                "AND NOT EXISTS (SELECT 1 FROM curation_source_runs s "
                "JOIN curation_sources i ON i.id=s.source "
                "WHERE s.run=r.run AND i.complete=1) LIMIT 1",
            )
            gold_claim_sources_complete = gold_source_rows is not None and not gold_source_rows
            gold_claim_source_mismatch = bool(gold_source_rows)

            candidate_claim_source_rows: list[sqlite3.Row] | None = []
            if not legacy_candidate:
                candidate_claim_source_rows = _bounded_rows(
                    db,
                    "SELECT r.run FROM curation_claim_runs r WHERE r.claim=? "
                    "AND NOT EXISTS (SELECT 1 FROM json_each(?) x "
                    "JOIN curation_source_runs s ON s.source=CAST(x.value AS TEXT) "
                    "AND s.run=r.run) LIMIT 1",
                    (candidate_claim_id, source_ids_json),
                )
            candidate_claim_sources_complete = (
                candidate_claim_source_rows is not None
                and not candidate_claim_source_rows
            )
            candidate_claim_source_mismatch = bool(candidate_claim_source_rows)
            gold_claim_runs: dict[str, set[str]] = {
                claim["id"]: set() for claim in claims
            }
            for row in gold_pair_rows or []:
                if row["claim"] in gold_claim_runs:
                    gold_claim_runs[row["claim"]].add(row["run"])
            exact_runs = candidate_runs & gold_runs
            grouped_rows = _bounded_rows(
                db,
                "WITH RECURSIVE seeds(side,run) AS ("
                "SELECT 'candidate',CAST(value AS TEXT) FROM json_each(?) UNION ALL "
                "SELECT 'gold',CAST(value AS TEXT) FROM json_each(?)), "
                "connected(side,run) AS (SELECT side,run FROM seeds UNION "
                "SELECT c.side,b.run FROM connected c "
                "JOIN curation_fingerprints a ON a.run=c.run "
                "JOIN curation_fingerprints b ON b.fingerprint=a.fingerprint "
                "LIMIT ?) SELECT side,run FROM connected",
                (
                    json.dumps(sorted(candidate_runs)), json.dumps(sorted(gold_runs)),
                    MAX_RUN_GROUP_ROWS + 1,
                ),
                vm_steps=MAX_RUN_GROUP_SQLITE_VM_STEPS,
            )
            grouped = grouped_rows or []
            run_group_query_complete = (
                grouped_rows is not None and len(grouped_rows) <= MAX_RUN_GROUP_ROWS
            )
            candidate_group_runs = {row["run"] for row in grouped if row["side"] == "candidate"}
            gold_group_runs = {row["run"] for row in grouped if row["side"] == "gold"}
            group_overlap = candidate_group_runs & gold_group_runs
            source_coverage_complete = (
                source_complete and all_source_indexes_complete and not inventory_pending
                and source_match_complete and gold_claim_sources_complete
            )
            run_coverage_complete = (
                source_coverage_complete and candidate_runs_complete and gold_run_pairs_complete
                and candidate_claim_sources_complete
            )
            run_group_coverage_complete = (
                run_coverage_complete and not missing_fingerprints
                and run_group_query_complete
            )
            coverage_complete = (
                source_coverage_complete and run_coverage_complete
                and run_group_coverage_complete
            )

            if exact_sources or exact_runs or group_overlap:
                status = "possible_overlap" if legacy_candidate else "overlap"
            elif coverage_complete:
                status = "no_indexed_overlap"
            else:
                status = "unknown"

            reasons: list[str] = []
            if not coverage_complete:
                reasons.extend(
                    reason
                    for active, reason in (
                        (not source_complete or not all_source_indexes_complete,
                         "source_index_incomplete"),
                        (inventory_pending, "verified_source_inventory_pending"),
                        (missing_fingerprints, "duplicate_group_index_incomplete"),
                        (not source_match_complete, "source_overlap_query_incomplete"),
                        (not gold_claim_sources_complete,
                         "gold_claim_source_mismatch" if gold_claim_source_mismatch
                         else "owner_index_query_limit"),
                        (not candidate_claim_sources_complete,
                         "candidate_claim_source_mismatch" if candidate_claim_source_mismatch
                         else "owner_index_query_limit"),
                        (candidate_run_query_limited or gold_run_query_limited,
                         "owner_index_query_limit"),
                        (not candidate_runs_complete or not gold_run_pairs_complete,
                         "overlap_run_limit"),
                        (not gold_claims_complete, "overlap_claim_limit"),
                        (not run_group_query_complete,
                         "run_group_query_limit" if grouped_rows is None
                         else "run_group_limit"),
                    )
                    if active
                )
            if legacy_candidate and (exact_sources or exact_runs or group_overlap):
                reasons.append("candidate_source_superset_requires_refinement")
            source_finding = {
                "status": (
                    "overlap" if exact_sources else
                    "none" if source_coverage_complete else "unknown"
                ),
                "count": (
                    len(exact_sources) if exact_sources or source_coverage_complete else None
                ),
            }
            run_finding: dict[str, Any]
            if legacy_candidate and exact_runs:
                run_finding = {
                    "status": "possible_overlap",
                    "count": None,
                }
                if candidate_runs_complete and gold_run_pairs_complete:
                    run_finding["observed_superset_count"] = len(exact_runs)
            else:
                run_finding = {
                    "status": (
                        "overlap" if exact_runs else
                        "none" if run_coverage_complete else "unknown"
                    ),
                    "count": (
                        len(exact_runs)
                        if run_coverage_complete and (exact_runs or run_coverage_complete)
                        else None
                    ),
                }
            if legacy_candidate and group_overlap:
                run_group_finding: dict[str, Any] = {
                    "status": "possible_overlap",
                    "related_runs": None,
                }
                if run_group_coverage_complete:
                    run_group_finding["observed_superset_runs"] = len(group_overlap)
            else:
                run_group_finding = {
                    "status": (
                        "overlap" if group_overlap else
                        "none" if run_group_coverage_complete else "unknown"
                    ),
                    "related_runs": (
                        len(group_overlap)
                        if run_group_coverage_complete
                        and (group_overlap or run_group_coverage_complete) else None
                    ),
                }
            gold_membership = [
                {
                    "claim": claim["id"],
                    "artifact": claim["artifact"],
                    "runs": sorted(gold_claim_runs[claim["id"]]),
                }
                for claim in claims
            ]
            candidate_commitment = (
                hashlib.sha256(
                    json_bytes(
                        {
                            "artifact": candidate_id,
                            "claim": candidate_claim_id,
                            "scope": candidate_scope,
                            "sources": sorted(source_ids),
                            "runs": sorted(candidate_runs),
                        }
                    )
                ).hexdigest()
                if candidate_runs_complete else None
            )
            gold_commitment = (
                hashlib.sha256(json_bytes(gold_membership)).hexdigest()
                if gold_claims_complete and gold_run_pairs_complete else None
            )
            comparison_commitment = (
                hashlib.sha256(
                    json_bytes(
                        {
                            "candidate": candidate_commitment,
                            "requested_gold": gold_id,
                            "gold_reservations": gold_commitment,
                        }
                    )
                ).hexdigest()
                if candidate_commitment is not None and gold_commitment is not None else None
            )
            return {
                **unknown,
                "observed_at": timestamp(),
                "status": status,
                "candidate_scope": candidate_scope,
                "findings": {
                    "source": source_finding,
                    "run": run_finding,
                    "run_group": run_group_finding,
                },
                "coverage": {
                    "candidate_source": "complete" if source_complete else "incomplete",
                    "gold_source": (
                        "complete"
                        if all_source_indexes_complete and not inventory_pending
                        and source_match_complete and gold_claim_sources_complete
                        else "incomplete"
                    ),
                    "verified_source_inventory": (
                        "incomplete" if inventory_pending else "complete"
                    ),
                    "run": "complete" if run_coverage_complete else "incomplete",
                    "run_group": (
                        "complete" if run_group_coverage_complete else "incomplete"
                    ),
                    "physical_game": "unknown",
                },
                "commitments": {
                    "candidate_membership_sha256": candidate_commitment,
                    "gold_reservations_sha256": gold_commitment,
                    "comparison_sha256": comparison_commitment,
                },
                "reasons": reasons or (["no_overlap_in_indexed_scope"]
                                        if status == "no_indexed_overlap" else []),
            }
    except BoundaryError as error:
        if error.stage in {"sharing", "hub"}:
            return candidate_unavailable()
        raise
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError, KeyError):
        return unknown_for("owner_metadata_unavailable")
