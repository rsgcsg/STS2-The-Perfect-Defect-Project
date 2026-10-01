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
from spireagent.hub.console_auth import ConsolePrincipal
from spireagent.hub.curation import CurationLedger, _hub_inventory_pending
from spireagent.hub.database import Operations
from spireagent.hub.uploads import UploadService
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.store import ArtifactStore

CURATED_DATASET_SCHEMA = "stpd/curated-decision-dataset-v1"
RECEIVED_SOURCE_SCHEMA = "stpd/received-bundle-v1"
MAX_LINEAGE_NODES = 512
MAX_SOURCE_INDEXES = 512
MAX_OVERLAP_RUNS = 50_000


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
    from spireagent.json_boundary import digest

    candidate_id = digest(candidate_id, "curation_overlap.candidate")
    gold_id = digest(gold_id, "curation_overlap.gold")
    if not project_member(principal):
        raise BoundaryError("hub", "unauthorized")

    unknown: dict[str, Any] = {
        "schema": "stpd/curation-overlap-v1",
        "observed_at": timestamp(),
        "scope": "candidate_against_owner_gold_reservations",
        "status": "unknown",
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
        result["reasons"] = [reason]
        return result

    try:
        candidate = service.store.get_manifest(candidate_id)
        if candidate.kind != "dataset":
            return unknown_for("candidate_not_dataset")
        candidate_info = candidate.parameters.value()
        if (
            candidate_info.get("schema") != CURATED_DATASET_SCHEMA
            or candidate_info.get("purpose") != "training"
        ):
            return unknown_for("candidate_not_supported_training_dataset")
        candidate_nodes = require_artifact_access(
            candidate, project_member=True, store=service.store
        )
    except (BoundaryError, OSError, ValueError):
        return unknown_for("candidate_metadata_unavailable")

    # Follow only the versioned source/dataset parent edges emitted by the owner.
    # This never opens a selection payload or infers relationships from filenames.
    node_by_id = {item.artifact_id: item for item in candidate_nodes}
    pending = [candidate]
    visited: set[str] = set()
    source_ids: set[str] = set()
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

    # Preserve the existing project-member artifact catalog and withdrawal check.
    # The target Gold is authorized by the owner claim below; its catalog projection
    # deliberately contains no parent IDs or payload descriptors.
    try:
        with closing(service.console_index.read()) as db:
            for identity in (candidate_id, gold_id):
                row = db.execute(
                    "SELECT kind,summary FROM console_artifacts WHERE artifact_id=?",
                    (identity,),
                ).fetchone()
                if row is None or row["kind"] != "dataset":
                    raise BoundaryError("curation_overlap", "resource_not_found")
                summary = json.loads(row["summary"])
                if identity == gold_id and summary.get("metadata", {}).get("purpose") != "gold":
                    raise BoundaryError("curation_overlap", "resource_not_found")
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

            # A project member may compare current candidates backed by sources that
            # remain shared. This reads existing withdrawal state; no owner is created.
            for source_id in source_ids:
                withdrawn = db.execute(
                    "SELECT 1 FROM uploads u JOIN collection_sharing s ON s.upload_id=u.id "
                    "WHERE json_extract(u.receipt,'$.evidence_id')=? AND s.approved=0 LIMIT 1",
                    (source_id,),
                ).fetchone()
                if withdrawn:
                    raise BoundaryError("hub", "unauthorized")

            claims = db.execute(
                "SELECT id,purpose,artifact FROM curation_claims "
                "WHERE purpose='gold' ORDER BY id"
            ).fetchall()
            candidate_claims = db.execute(
                "SELECT id,purpose,artifact FROM curation_claims WHERE artifact=?",
                (candidate_id,),
            ).fetchall()
            target_claims = [row for row in claims if row["artifact"] == gold_id]
            if len(candidate_claims) != 1 or candidate_claims[0]["purpose"] != "training":
                return unknown_for("candidate_owner_claim_missing")
            if len(target_claims) != 1:
                return unknown_for("gold_owner_claim_missing")

            candidate_claim_id = candidate_claims[0]["id"]
            candidate_runs = {
                row[0]
                for row in db.execute(
                    "SELECT run FROM curation_claim_runs WHERE claim=?",
                    (candidate_claim_id,),
                )
            }
            gold_claim_runs: dict[str, set[str]] = {}
            for claim in claims:
                gold_claim_runs[claim["id"]] = {
                    row[0]
                    for row in db.execute(
                        "SELECT run FROM curation_claim_runs WHERE claim=?", (claim["id"],)
                    )
                }
            gold_runs = set().union(*gold_claim_runs.values()) if gold_claim_runs else set()
            if not candidate_runs or not gold_runs:
                return unknown_for("owner_claim_membership_missing")
            if len(candidate_runs) > MAX_OVERLAP_RUNS or len(gold_runs) > MAX_OVERLAP_RUNS:
                return unknown_for("overlap_run_limit")

            source_states: dict[str, tuple[bool, set[str]]] = {}
            for source_id in source_ids:
                source_row = db.execute(
                    "SELECT complete FROM curation_sources WHERE id=?", (source_id,)
                ).fetchone()
                exact = db.execute(
                    "SELECT 1 FROM curation_exact_source_index WHERE source=?", (source_id,)
                ).fetchone()
                runs = {
                    row[0]
                    for row in db.execute(
                        "SELECT run FROM curation_source_runs WHERE source=?", (source_id,)
                    )
                }
                source_states[source_id] = (
                    source_row is not None and source_row[0] == 1 and exact is not None,
                    runs,
                )
            indexed_candidate_runs = set().union(
                *(runs for _, runs in source_states.values())
            )
            if not candidate_runs <= indexed_candidate_runs:
                return unknown_for("candidate_claim_source_mismatch")

            gold_sources = {
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT s.source FROM curation_source_runs s "
                    "JOIN curation_claim_runs r ON r.run=s.run "
                    "JOIN curation_claims c ON c.id=r.claim WHERE c.purpose='gold'"
                )
            }
            indexed_gold_runs = {
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT s.run FROM curation_source_runs s "
                    "JOIN curation_sources c ON c.id=s.source WHERE c.complete=1"
                )
            }
            if not gold_runs <= indexed_gold_runs:
                return unknown_for("gold_claim_source_mismatch")

            source_complete = all(complete for complete, _ in source_states.values())
            all_source_indexes_complete = db.execute(
                "SELECT 1 FROM curation_sources s LEFT JOIN curation_exact_source_index e "
                "ON e.source=s.id WHERE s.complete!=1 OR e.source IS NULL LIMIT 1"
            ).fetchone() is None
            inventory_pending = _hub_inventory_pending(db)
            missing_fingerprints = db.execute(
                "SELECT 1 FROM curation_source_runs s JOIN curation_sources c ON c.id=s.source "
                "LEFT JOIN curation_fingerprints f ON f.run=s.run "
                "WHERE c.complete=1 AND f.run IS NULL LIMIT 1"
            ).fetchone() is not None

            exact_sources = source_ids & gold_sources
            exact_runs = candidate_runs & gold_runs
            grouped = db.execute(
                "WITH RECURSIVE seeds(side,run) AS ("
                "SELECT 'candidate',CAST(value AS TEXT) FROM json_each(?) UNION ALL "
                "SELECT 'gold',CAST(value AS TEXT) FROM json_each(?)), "
                "connected(side,run) AS (SELECT side,run FROM seeds UNION "
                "SELECT c.side,b.run FROM connected c "
                "JOIN curation_fingerprints a ON a.run=c.run "
                "JOIN curation_fingerprints b ON b.fingerprint=a.fingerprint) "
                "SELECT side,run FROM connected",
                (json.dumps(sorted(candidate_runs)), json.dumps(sorted(gold_runs))),
            ).fetchall()
            candidate_group_runs = {row["run"] for row in grouped if row["side"] == "candidate"}
            gold_group_runs = {row["run"] for row in grouped if row["side"] == "gold"}
            group_overlap = candidate_group_runs & gold_group_runs
            coverage_complete = (
                source_complete
                and all_source_indexes_complete
                and not inventory_pending
                and not missing_fingerprints
            )

            if exact_sources or exact_runs or group_overlap:
                status = "overlap"
            elif coverage_complete:
                status = "no_indexed_overlap"
            else:
                status = "unknown"

            candidate_membership = {
                "artifact": candidate_id,
                "claim": candidate_claim_id,
                "sources": sorted(source_ids),
                "runs": sorted(candidate_runs),
            }
            gold_membership = [
                {
                    "claim": claim["id"],
                    "artifact": claim["artifact"],
                    "runs": sorted(gold_claim_runs[claim["id"]]),
                }
                for claim in claims
            ]
            candidate_commitment = hashlib.sha256(json_bytes(candidate_membership)).hexdigest()
            gold_commitment = hashlib.sha256(json_bytes(gold_membership)).hexdigest()
            comparison_commitment = hashlib.sha256(
                json_bytes(
                    {
                        "candidate": candidate_commitment,
                        "requested_gold": gold_id,
                        "gold_reservations": gold_commitment,
                    }
                )
            ).hexdigest()

            reasons: list[str] = []
            if not coverage_complete:
                reasons.extend(
                    reason
                    for active, reason in (
                        (not source_complete or not all_source_indexes_complete,
                         "source_index_incomplete"),
                        (inventory_pending, "verified_source_inventory_pending"),
                        (missing_fingerprints, "duplicate_group_index_incomplete"),
                    )
                    if active
                )
            return {
                **unknown,
                "observed_at": timestamp(),
                "status": status,
                "findings": {
                    "source": {
                        "status": (
                            "overlap" if exact_sources else
                            "none" if coverage_complete else "unknown"
                        ),
                        "count": len(exact_sources) if exact_sources or coverage_complete else None,
                    },
                    "run": {
                        "status": (
                            "overlap" if exact_runs else
                            "none" if coverage_complete else "unknown"
                        ),
                        "count": len(exact_runs) if exact_runs or coverage_complete else None,
                    },
                    "run_group": {
                        "status": (
                            "overlap" if group_overlap else
                            "none" if coverage_complete else "unknown"
                        ),
                        "related_runs": (
                            len(group_overlap) if group_overlap or coverage_complete else None
                        ),
                    },
                },
                "coverage": {
                    "candidate_source": "complete" if source_complete else "incomplete",
                    "gold_source": "complete" if all_source_indexes_complete else "incomplete",
                    "verified_source_inventory": (
                        "incomplete" if inventory_pending else "complete"
                    ),
                    "run_group": "complete" if not missing_fingerprints else "incomplete",
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
    except BoundaryError:
        raise
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError, KeyError):
        return unknown_for("owner_metadata_unavailable")
