"""Durable curation authority for one explicitly created local store.

The registry is a disposable index. This database and the store owner marker are
not: opening an existing workspace never creates or repairs either one.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from spireagent.hub.database import create_private_database
from spireagent.json_boundary import BoundaryError, digest
from spireagent.research_curation import CurationLedger, InventoryPending
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore

LEDGER_NAME = "curation.sqlite"
OWNER_NAME = ".curation-owner.json"
OWNER_SCHEMA = "stpd/local-curation-owner-v1"
REQUIRED_TABLES = {
    "local_curation_identity", "local_source_pending", "curation_sources",
    "curation_source_decisions", "curation_exact_source_index", "curation_source_runs",
    "curation_fingerprints", "curation_occurrences", "curation_occurrence_details",
    "curation_claims", "curation_claim_runs", "curation_uses",
    "curation_source_uses", "curation_annotations",
}
REQUIRED_INDEXES = {
    "curation_decision_sources", "curation_run_fingerprints",
    "curation_run_claims", "curation_latest_annotation",
}


class LocalLedger(CurationLedger):
    def __init__(self, owner: LocalCurationOwner, *,
                 inventory_pending: InventoryPending | None = None) -> None:
        super().__init__(owner, inventory_pending=inventory_pending,
                         claim_guard=owner._historical_claim_guard)

    def claim(self, identity: str, purpose: str, runs: Iterable[str], *,
              gold_parents: tuple[str, ...] = (),
              require_inventory: bool = False, annotation_revision: int | None = None) -> None:
        # A local caller cannot accidentally omit the host's Gold inventory gate.
        super().claim(identity, purpose, runs, gold_parents=gold_parents,
                      require_inventory=require_inventory or purpose == "gold",
                      annotation_revision=annotation_revision)


class LocalCurationOwner:
    def __init__(self, path: Path, store_dir: Path, workspace_id: str,
                 ledger_id: str, store_id: str, *, create: bool = False,
                 allow_existing: bool = False, legacy_guard: bool = False,
                 owner_schema: str = OWNER_SCHEMA) -> None:
        self.path = path
        self.store_dir = store_dir
        self.identity = (workspace_id, ledger_id, store_id, str(store_dir.resolve()))
        self._ledger: LocalLedger | None = None
        self._schema_ready = False
        self.legacy_guard = legacy_guard
        if path.is_symlink() or store_dir.is_symlink() or not store_dir.is_dir():
            raise BoundaryError("local_curation", "owner_storage_invalid")
        owner_path = store_dir / OWNER_NAME
        try:
            if owner_path.is_symlink() or json.loads(owner_path.read_text()) != {
                "schema": owner_schema, "workspace_id": workspace_id, "store_id": store_id,
                "ledger_id": ledger_id, "ledger_path": str(path.resolve()),
            }:
                raise BoundaryError("local_curation", "store_identity_mismatch")
        except (OSError, ValueError) as error:
            raise BoundaryError("local_curation", "store_identity_mismatch") from error
        if create:
            if path.exists():
                raise BoundaryError("local_curation", "ledger_already_exists")
            if not allow_existing and tuple(ManifestArtifactStore(LocalBlobStore(
                store_dir, create=False, readonly=True)).manifest_ids()):
                raise BoundaryError("local_curation", "existing_store_requires_recovery")
            create_private_database(path)
            with self.transaction(initializing=True) as db:
                db.execute("CREATE TABLE local_curation_identity("
                           "workspace TEXT NOT NULL,ledger TEXT NOT NULL,"
                           "store TEXT NOT NULL,store_path TEXT NOT NULL)")
                db.execute("INSERT INTO local_curation_identity VALUES(?,?,?,?)", self.identity)
                db.execute("CREATE TABLE local_source_pending("
                           "candidate TEXT PRIMARY KEY,artifact TEXT,status TEXT NOT NULL "
                           "CHECK(status IN ('publishing','published')))")
                if allow_existing:
                    db.execute("CREATE TABLE local_legacy_unknown_runs(run TEXT PRIMARY KEY)")
            self._ledger = LocalLedger(self, inventory_pending=self._inventory_pending)
            self._schema_ready = True
        else:
            if not path.is_file():
                raise BoundaryError("local_curation", "ledger_recovery_required")
            try:
                db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
                try:
                    row = db.execute("SELECT workspace,ledger,store,store_path "
                                     "FROM local_curation_identity").fetchone()
                    if row != self.identity or db.execute(
                        "SELECT count(*) FROM local_curation_identity").fetchone()[0] != 1:
                        raise BoundaryError("local_curation", "ledger_identity_mismatch")
                    self._validate_existing(db)
                    self._schema_ready = True
                finally:
                    db.close()
            except sqlite3.DatabaseError as error:
                raise BoundaryError("local_curation", "ledger_recovery_required") from error

    @staticmethod
    def _validate_existing(db: sqlite3.Connection) -> None:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables >= REQUIRED_TABLES:
            raise BoundaryError("local_curation", "ledger_recovery_required")
        indexes = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        if not indexes >= REQUIRED_INDEXES:
            raise BoundaryError("local_curation", "ledger_recovery_required")

    @property
    def ledger(self) -> LocalLedger:
        if self._ledger is None:
            self._ledger = LocalLedger(self, inventory_pending=self._inventory_pending)
        return self._ledger

    @contextmanager
    def transaction(self, *, initializing: bool = False) -> Iterator[sqlite3.Connection]:
        if self.path.is_symlink() or not self.path.is_file():
            raise BoundaryError("local_curation", "ledger_recovery_required")
        try:
            db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True,
                                 timeout=30, isolation_level=None)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                if not initializing:
                    row = db.execute("SELECT workspace,ledger,store,store_path "
                                     "FROM local_curation_identity").fetchone()
                    if row != self.identity or db.execute(
                        "SELECT count(*) FROM local_curation_identity").fetchone()[0] != 1:
                        raise BoundaryError("local_curation", "ledger_identity_mismatch")
                    if self._schema_ready:
                        self._validate_existing(db)
                yield db
                if db.in_transaction:
                    db.commit()
            except BaseException:
                if db.in_transaction:
                    db.rollback()
                raise
            finally:
                db.close()
        except sqlite3.DatabaseError as error:
            raise BoundaryError("local_curation", "ledger_recovery_required") from error

    def begin_source(self, candidate: str) -> None:
        digest(candidate, "local_curation.candidate")
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO local_source_pending VALUES(?,NULL,'publishing')",
                       (candidate,))

    def published_source(self, candidate: str, artifact: str) -> None:
        digest(artifact, "local_curation.artifact")
        with self.transaction() as db:
            row = db.execute("SELECT artifact FROM local_source_pending WHERE candidate=?",
                             (candidate,)).fetchone()
            if row is None or row[0] not in (None, artifact):
                raise BoundaryError("local_curation", "source_pending_identity_conflict")
            db.execute("UPDATE local_source_pending SET artifact=?,status='published' "
                       "WHERE candidate=?", (artifact, candidate))

    def complete_index(self, candidate: str, source: str) -> None:
        """Clear a pending import only after a typed source has a complete exact index."""
        with self.transaction() as db:
            row = db.execute("SELECT artifact,status FROM local_source_pending "
                             "WHERE candidate=?", (candidate,)).fetchone()
            if row is None:
                raise BoundaryError("local_curation", "source_pending_required")
            if row != (source, "published"):
                raise BoundaryError("local_curation", "source_pending_identity_conflict")
            if db.execute("SELECT 1 FROM curation_sources s JOIN curation_exact_source_index i "
                          "ON i.source=s.id WHERE s.id=? AND s.complete=1", (source,)
                          ).fetchone() is None:
                raise BoundaryError("local_curation", "source_index_incomplete")
            db.execute("DELETE FROM local_source_pending WHERE candidate=?", (candidate,))

    def _inventory_pending(self, db: sqlite3.Connection) -> bool:
        if db.execute("SELECT 1 FROM local_source_pending LIMIT 1").fetchone():
            return True
        # A store writer outside this owner cannot silently add an unindexed source.
        store = ManifestArtifactStore(LocalBlobStore(self.store_dir, create=False, readonly=True))
        indexed = {row[0] for row in db.execute(
            "SELECT id FROM curation_sources WHERE complete=1")}
        return any(artifact not in indexed for artifact in store.manifest_ids()
                   if store.get_manifest(artifact).kind == "evidence")

    def gold_history_unknown(self, db: sqlite3.Connection, runs: Iterable[str]) -> bool:
        if not self.legacy_guard:
            return False
        if db.execute("SELECT 1 FROM local_legacy_unknown_runs WHERE run='*'").fetchone():
            return True
        return any(db.execute("SELECT 1 FROM local_legacy_unknown_runs WHERE run=?",
                              (run,)).fetchone() for run in runs)

    def _historical_claim_guard(self, db: sqlite3.Connection, purpose: str,
                                related: set[str]) -> None:
        if purpose == "gold" and self.gold_history_unknown(db, related):
            raise BoundaryError("local_curation", "legacy_gold_history_unknown")
