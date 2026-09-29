"""Durable research reservations for a transactional SQLite project ledger.

Run/duplicate membership is monotonic. Hiding a dataset, retrying a job, or evicting a
projection cache cannot release a Gold reservation. The host supplies one writer transaction
for each operation; Gold inventory qualification, when requested, runs inside that transaction.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from spireagent.json_boundary import BoundaryError, digest
from stpd.fullrun.contracts import ResearchTransitionV1, ResearchTransitionV2, SourceProjection
from stpd.fullrun.curated_dataset import PURPOSES
from stpd.fullrun.decision_dataset import _identity
from stpd.fullrun.representation import decision_fingerprint


class CurationTransactions(Protocol):
    """A host's SQLite writer transaction, including commit and rollback."""

    def transaction(self) -> AbstractContextManager[sqlite3.Connection]: ...


InventoryPending = Callable[[sqlite3.Connection], bool]
ClaimGuard = Callable[[sqlite3.Connection, str, set[str]], None]


class CurationLedger:
    def __init__(
        self,
        operations: CurationTransactions,
        *,
        inventory_pending: InventoryPending | None = None,
        claim_guard: ClaimGuard | None = None,
    ) -> None:
        self.operations = operations
        self._inventory_pending = inventory_pending
        self._claim_guard = claim_guard
        with operations.transaction() as db:
            for statement in (
                "CREATE TABLE IF NOT EXISTS curation_sources("
                "id TEXT PRIMARY KEY,archive TEXT NOT NULL,complete INTEGER NOT NULL)",
                "CREATE TABLE IF NOT EXISTS curation_source_decisions("
                "source TEXT NOT NULL,occurrence TEXT NOT NULL,transition_id TEXT NOT NULL,"
                "PRIMARY KEY(source,occurrence))",
                "CREATE INDEX IF NOT EXISTS curation_decision_sources ON "
                "curation_source_decisions(occurrence,source)",
                "CREATE TABLE IF NOT EXISTS curation_exact_source_index(source TEXT PRIMARY KEY)",
                "CREATE TABLE IF NOT EXISTS curation_source_runs("
                "source TEXT NOT NULL,run TEXT NOT NULL,PRIMARY KEY(source,run))",
                "CREATE TABLE IF NOT EXISTS curation_fingerprints("
                "fingerprint TEXT NOT NULL,run TEXT NOT NULL,PRIMARY KEY(fingerprint,run))",
                "CREATE INDEX IF NOT EXISTS curation_run_fingerprints ON "
                "curation_fingerprints(run,fingerprint)",
                "CREATE TABLE IF NOT EXISTS curation_occurrences("
                "id TEXT PRIMARY KEY,run TEXT NOT NULL,decision TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS curation_occurrence_details("
                "id TEXT PRIMARY KEY,sequence INTEGER NOT NULL,family TEXT NOT NULL,"
                "surface TEXT NOT NULL,action TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS curation_claims("
                "id TEXT PRIMARY KEY,purpose TEXT NOT NULL,artifact TEXT,created REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS curation_claim_runs("
                "claim TEXT NOT NULL,run TEXT NOT NULL,PRIMARY KEY(claim,run))",
                "CREATE INDEX IF NOT EXISTS curation_run_claims ON curation_claim_runs(run,claim)",
                "CREATE TABLE IF NOT EXISTS curation_uses("
                "run TEXT NOT NULL,kind TEXT NOT NULL,reference TEXT NOT NULL,at REAL NOT NULL,"
                "PRIMARY KEY(run,kind,reference))",
                "CREATE TABLE IF NOT EXISTS curation_source_uses("
                "source TEXT NOT NULL,kind TEXT NOT NULL,reference TEXT NOT NULL,"
                "PRIMARY KEY(source,kind,reference))",
                "CREATE TABLE IF NOT EXISTS curation_annotations("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT,occurrence TEXT NOT NULL,"
                "actor TEXT NOT NULL,action TEXT NOT NULL,reason TEXT NOT NULL,at REAL NOT NULL)",
                "CREATE INDEX IF NOT EXISTS curation_latest_annotation ON "
                "curation_annotations(occurrence,sequence)",
            ):
                db.execute(statement)

    def index_source(self, source: str, projection: SourceProjection) -> None:
        """Called only on the installed verifier's typed output, never HTTP JSON."""
        digest(source, "curation.source")
        if projection.scope != "platform_verified":
            raise BoundaryError("curation", "verified_source_required")
        # Write small batches; a crash leaves complete=0 and cannot open a raw export.
        with self.operations.transaction() as db:
            previous = db.execute(
                "SELECT archive,complete FROM curation_sources WHERE id=?", (source,)
            ).fetchone()
            if previous and previous[0] != projection.source_sha256:
                raise BoundaryError("curation", "source_identity_conflict")
            if previous and previous[1] and db.execute(
                "SELECT 1 FROM curation_exact_source_index WHERE source=?", (source,)
            ).fetchone():
                return
            db.execute(
                "INSERT OR IGNORE INTO curation_sources VALUES(?,?,0)",
                (source, projection.source_sha256),
            )
        for start in range(0, len(projection.transitions), 64):
            items = []
            details = []
            locations = []
            for record in projection.transitions[start : start + 64]:
                if not isinstance(record, ResearchTransitionV2):
                    raise BoundaryError("curation", "typed_occurrence_required")
                items.append(
                    (
                        record.run_id,
                        decision_fingerprint(record),
                        _identity(record),
                        record.occurrence.value()["decision_id"],
                    )
                )
                locations.append((source, _identity(record), record.transition_id))
                chosen = next(a for a in record.actions if a.key == record.chosen_key)
                details.append(
                    (
                        _identity(record),
                        record.source_evidence.value()["action_sequence"],
                        record.family,
                        record.surface,
                        json.dumps(chosen.semantic_dict()),
                    )
                )
            with self.operations.transaction() as db:
                db.executemany(
                    "INSERT OR IGNORE INTO curation_source_decisions VALUES(?,?,?)", locations
                )
                db.executemany(
                    "INSERT OR IGNORE INTO curation_occurrence_details VALUES(?,?,?,?,?)", details
                )
                for run, fingerprint, occurrence, decision in items:
                    db.execute(
                        "INSERT OR IGNORE INTO curation_source_runs VALUES(?,?)", (source, run)
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO curation_fingerprints VALUES(?,?)",
                        (fingerprint, run),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO curation_occurrences VALUES(?,?,?)",
                        (occurrence, run, decision),
                    )
        with self.operations.transaction() as db:
            db.execute("UPDATE curation_sources SET complete=1 WHERE id=?", (source,))
            db.execute("INSERT OR IGNORE INTO curation_exact_source_index VALUES(?)", (source,))

    @staticmethod
    def _groups(db: sqlite3.Connection, runs: Iterable[str]) -> set[str]:
        # Temp seeds avoid SQLite's parameter limit on a large dataset. UNION terminates
        # cycles; duplicate inputs connect whole runs rather than just selected rows.
        db.execute("CREATE TEMP TABLE IF NOT EXISTS curation_seeds(run TEXT PRIMARY KEY)")
        db.execute("DELETE FROM curation_seeds")
        db.executemany("INSERT OR IGNORE INTO curation_seeds VALUES(?)", ((r,) for r in runs))
        return {
            row[0]
            for row in db.execute(
                "WITH RECURSIVE connected(run) AS (SELECT run FROM curation_seeds UNION "
                "SELECT b.run FROM connected c JOIN curation_fingerprints a ON a.run=c.run "
                "JOIN curation_fingerprints b ON b.fingerprint=a.fingerprint) "
                "SELECT run FROM connected"
            )
        }

    @staticmethod
    def _claims(db: sqlite3.Connection, runs: set[str]) -> dict[str, tuple[str, str | None]]:
        found = {}
        for run in runs:
            for row in db.execute(
                "SELECT c.id,c.purpose,c.artifact FROM curation_claims c "
                "JOIN curation_claim_runs r ON r.claim=c.id WHERE r.run=?",
                (run,),
            ):
                found[row[0]] = (row[1], row[2])
        return found

    def overlap(self, left: Iterable[str], right: Iterable[str]) -> dict[str, Any]:
        with self.operations.transaction() as db:
            common = self._groups(db, left) & self._groups(db, right)
        return {
            "overlap": bool(common),
            "related_runs": len(common),
            "basis": "whole_run_and_transitive_duplicate_decisions",
        }

    def claim(
        self,
        identity: str,
        purpose: str,
        runs: Iterable[str],
        *,
        gold_parents: tuple[str, ...] = (),
        require_inventory: bool = False,
        annotation_revision: int | None = None,
    ) -> None:
        """Reserve before immutable publication; a failed job retains its own reservation."""
        if not isinstance(purpose, str) or purpose not in PURPOSES:
            raise BoundaryError("curation", "invalid_dataset_purpose")
        selected = set(runs)
        if not selected:
            raise BoundaryError("curation", "empty_selection")
        with self.operations.transaction() as db:
            if annotation_revision is not None and db.execute(
                "SELECT coalesce(max(sequence),0) FROM curation_annotations"
            ).fetchone()[0] != annotation_revision:
                raise BoundaryError("curation", "quality_annotations_changed")
            # Without a host's complete, current inventory there is no basis to seal Gold.
            # The callback must inspect that inventory using this same writer transaction.
            if (
                purpose == "gold"
                and require_inventory
                and (self._inventory_pending is None or self._inventory_pending(db))
            ):
                raise BoundaryError("curation", "gold_source_inventory_pending")
            old = db.execute(
                "SELECT purpose FROM curation_claims WHERE id=?", (identity,)
            ).fetchone()
            if old:
                old_runs = {
                    r[0]
                    for r in db.execute(
                        "SELECT run FROM curation_claim_runs WHERE claim=?", (identity,)
                    )
                }
                if old[0] != purpose or old_runs != selected:
                    raise BoundaryError("curation", "reservation_identity_conflict")
            related = self._groups(db, selected)
            if self._claim_guard is not None:
                self._claim_guard(db, purpose, related)
            claims = self._claims(db, related)
            for claim, (kind, artifact) in claims.items():
                if claim == identity:
                    continue
                if purpose == "gold":
                    if kind != "gold":
                        raise BoundaryError("curation", "gold_already_in_other_dataset")
                    if artifact not in gold_parents:
                        raise BoundaryError("curation", "gold_requires_gold_merge")
                elif kind == "gold":
                    raise BoundaryError("curation", "gold_reserved_data")
            if gold_parents and purpose != "gold":
                raise BoundaryError("curation", "gold_merge_must_remain_gold")
            for parent in gold_parents:
                row = db.execute(
                    "SELECT purpose FROM curation_claims WHERE artifact=?", (parent,)
                ).fetchone()
                if row is None or row[0] != "gold":
                    raise BoundaryError("curation", "gold_merge_requires_only_gold")
            if purpose == "gold" and any(
                db.execute(
                    "SELECT 1 FROM curation_uses WHERE run=? AND kind='training' UNION "
                    "SELECT 1 FROM curation_source_uses u JOIN curation_source_runs r "
                    "ON r.source=u.source WHERE r.run=? AND u.kind='training'",
                    (run, run),
                ).fetchone()
                for run in related
            ):
                raise BoundaryError("curation", "gold_previously_used_for_training")
            db.execute(
                "INSERT OR IGNORE INTO curation_claims VALUES(?,?,NULL,?)",
                (identity, purpose, time.time()),
            )
            db.executemany(
                "INSERT OR IGNORE INTO curation_claim_runs VALUES(?,?)",
                ((identity, run) for run in sorted(selected)),
            )

    def bind(self, identity: str, artifact: str) -> None:
        digest(artifact, "curation.artifact")
        with self.operations.transaction() as db:
            old = db.execute(
                "SELECT artifact FROM curation_claims WHERE id=?", (identity,)
            ).fetchone()
            if old is None or old[0] not in {None, artifact}:
                raise BoundaryError("curation", "reservation_identity_conflict")
            db.execute("UPDATE curation_claims SET artifact=? WHERE id=?", (artifact, identity))

    def dataset(self, artifact: str) -> tuple[str, set[str]] | None:
        with self.operations.transaction() as db:
            row = db.execute(
                "SELECT id,purpose FROM curation_claims WHERE artifact=?", (artifact,)
            ).fetchone()
            if row is None:
                return None
            runs = {
                r[0]
                for r in db.execute("SELECT run FROM curation_claim_runs WHERE claim=?", (row[0],))
            }
        return row[1], runs

    def require_training_use(self, artifact: str, sources: Iterable[str],
                             runs: Iterable[str], reference: str) -> None:
        """Read exact local training claim, complete indexes and prior exposure.

        A caller independently verifies the source artifact and its run IDs. This
        method owns the ledger relationship and creates no new use records.
        """
        digest(artifact, "curation.training_artifact")
        digest(reference, "curation.training_reference", length=32)
        source_ids, run_ids = set(sources), set(runs)
        if not source_ids or not run_ids:
            raise BoundaryError("curation", "training_use_identity_missing")
        for source in source_ids:
            digest(source, "curation.training_source")
        with self.operations.transaction() as db:
            claims = db.execute(
                "SELECT id,purpose FROM curation_claims WHERE artifact=?", (artifact,)
            ).fetchall()
            if len(claims) != 1 or claims[0][1] != "training":
                raise BoundaryError("curation", "training_claim_mismatch")
            claimed = {row[0] for row in db.execute(
                "SELECT run FROM curation_claim_runs WHERE claim=?", (claims[0][0],)
            )}
            if claimed != run_ids:
                raise BoundaryError("curation", "training_claim_mismatch")
            indexed: set[str] = set()
            for source in source_ids:
                ready = db.execute("SELECT complete FROM curation_sources WHERE id=?",
                                   (source,)).fetchone()
                if ready is None or ready[0] != 1 or not db.execute(
                    "SELECT 1 FROM curation_exact_source_index WHERE source=?", (source,)
                ).fetchone():
                    raise BoundaryError("curation", "source_index_incomplete")
                indexed.update(row[0] for row in db.execute(
                    "SELECT run FROM curation_source_runs WHERE source=?", (source,)
                ))
                if not db.execute(
                    "SELECT 1 FROM curation_source_uses "
                    "WHERE source=? AND kind='training' AND reference=?",
                    (source, reference),
                ).fetchone():
                    raise BoundaryError("curation", "training_source_use_missing")
            if not run_ids <= indexed:
                raise BoundaryError("curation", "source_run_identity_mismatch")
            for run in run_ids:
                if not db.execute(
                    "SELECT 1 FROM curation_uses WHERE run=? AND kind='training' "
                    "AND reference=?", (run, reference)
                ).fetchone():
                    raise BoundaryError("curation", "training_run_use_missing")

    def use(self, runs: Iterable[str], kind: str, reference: str) -> None:
        if kind not in {"training", "download"}:
            raise BoundaryError("curation", "invalid_use")
        with self.operations.transaction() as db:
            related = self._groups(db, runs)
            if any(purpose == "gold" for purpose, _ in self._claims(db, related).values()):
                raise BoundaryError("curation", "gold_reserved_data")
            db.executemany(
                "INSERT OR IGNORE INTO curation_uses VALUES(?,?,?,?)",
                ((r, kind, reference, time.time()) for r in related),
            )

    def use_source(self, source: str, kind: str, reference: str) -> None:
        """Retain exposure/use even if this old source has not been projected yet."""
        if kind not in {"training", "download"}:
            raise BoundaryError("curation", "invalid_use")
        with self.operations.transaction() as db:
            runs = {
                r[0]
                for r in db.execute(
                    "SELECT run FROM curation_source_runs WHERE source=?", (source,)
                )
            }
            related = self._groups(db, runs)
            if any(purpose == "gold" for purpose, _ in self._claims(db, related).values()):
                raise BoundaryError("curation", "gold_reserved_data")
            if db.execute("SELECT 1 FROM curation_claims WHERE purpose='gold' LIMIT 1").fetchone():
                indexed = db.execute(
                    "SELECT complete FROM curation_sources WHERE id=?", (source,)
                ).fetchone()
                if indexed is None or not indexed[0]:
                    raise BoundaryError("curation", "source_isolation_index_pending")
            db.execute(
                "INSERT OR IGNORE INTO curation_source_uses VALUES(?,?,?)",
                (source, kind, reference),
            )

    def source_runs(self, source: str) -> set[str] | None:
        with self.operations.transaction() as db:
            row = db.execute(
                "SELECT complete FROM curation_sources WHERE id=?", (source,)
            ).fetchone()
            if row is None or not row[0]:
                return None
            return {
                r[0]
                for r in db.execute(
                    "SELECT run FROM curation_source_runs WHERE source=?", (source,)
                )
            }

    def exact_source_ready(self, source: str) -> bool:
        """Whole-run membership alone cannot locate a decision in a package."""
        with self.operations.transaction() as db:
            return db.execute(
                "SELECT 1 FROM curation_exact_source_index e JOIN curation_sources s "
                "ON s.id=e.source WHERE e.source=? AND s.complete=1", (source,)
            ).fetchone() is not None

    def has_gold(self) -> bool:
        with self.operations.transaction() as db:
            return (
                db.execute("SELECT 1 FROM curation_claims WHERE purpose='gold' LIMIT 1").fetchone()
                is not None
            )

    def annotate(self, occurrence: str, actor: str, action: str, reason: str) -> int:
        digest(occurrence, "curation.occurrence")
        if (
            not isinstance(action, str)
            or action not in {"flag", "exclude", "restore"}
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 500
        ):
            raise BoundaryError("curation", "invalid_annotation")
        with self.operations.transaction() as db:
            if (
                db.execute(
                    "SELECT 1 FROM curation_occurrences WHERE id=?", (occurrence,)
                ).fetchone()
                is None
            ):
                raise BoundaryError("curation", "occurrence_not_found")
            cursor = db.execute(
                "INSERT INTO curation_annotations(occurrence,actor,action,reason,at) "
                "VALUES(?,?,?,?,?)",
                (occurrence, actor, action, reason.strip(), time.time()),
            )
            return int(cursor.lastrowid or 0)

    def annotations(
        self, records: Iterable[ResearchTransitionV1], *, runs: Iterable[str] | None = None
    ) -> dict[str, Any]:
        with self.operations.transaction() as db:
            revision = db.execute(
                "SELECT coalesce(max(sequence),0) FROM curation_annotations"
            ).fetchone()[0]
            items = {}
            for run in set(runs) if runs is not None else {record.run_id for record in records}:
                for row in db.execute(
                    "SELECT a.occurrence,a.sequence,a.action,a.reason FROM curation_annotations a "
                    "JOIN curation_occurrences o ON o.id=a.occurrence WHERE o.run=? "
                    "AND a.sequence=(SELECT max(sequence) FROM curation_annotations b "
                    "WHERE b.occurrence=a.occurrence)",
                    (run,),
                ):
                    items[row[0]] = {"sequence": row[1], "action": row[2], "reason": row[3]}
        return {"revision": revision, "items": items}

    def history(self, occurrence: str) -> list[dict[str, Any]]:
        digest(occurrence, "curation.occurrence")
        with self.operations.transaction() as db:
            rows = db.execute(
                "SELECT sequence,action,reason,at FROM curation_annotations "
                "WHERE occurrence=? ORDER BY sequence",
                (occurrence,),
            ).fetchall()
        return [dict(row) for row in rows]
