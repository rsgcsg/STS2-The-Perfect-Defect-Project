"""Durable curation authority for one explicitly created local store.

The registry is a disposable index. This database and the store owner marker are
not: opening an existing workspace never creates or repairs either one.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from sts2_platform_evidence.human_session_bundle_v3 import HumanSessionBundleV3

from spireagent.artifact_contracts import Manifest
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

    @staticmethod
    def _human_runs(store: ManifestArtifactStore, source: str) -> set[str]:
        """Read native run membership from the exact typed local archive, not labels."""
        from spireagent.local_verified_bundle import EVIDENCE_SCHEMA, verified_local_bundle

        manifest = store.get_manifest(source)
        if (manifest.kind != "evidence"
                or manifest.parameters.value().get("schema") != EVIDENCE_SCHEMA):
            raise BoundaryError("local_curation", "local_verified_source_required")
        with verified_local_bundle(store, manifest) as verified:
            bundle = verified.bundle
            if not isinstance(bundle, HumanSessionBundleV3):
                raise BoundaryError("local_curation", "human_run_identity_mismatch")
            if any(row["session_id"] != bundle.session_id or
                   row["run_id"] not in bundle.run_ids for row in bundle.human_text_inputs):
                raise BoundaryError("local_curation", "human_run_identity_mismatch")
            result = {bundle.session_id + "/" + run for run in bundle.run_ids}
            verified.assert_directory_identity()
            return result

    def index_human_runs(self, store: ManifestArtifactStore, source: str, *,
                         historical: bool = False) -> set[str]:
        """Add typed run membership without claiming a canonical transition index.

        A pre-owner source has unknown prior use. A new import retains its pending
        candidate until this membership and the canonical index are both present.
        """
        runs = self._human_runs(store, source)
        with self.transaction() as db:
            indexed = db.execute("SELECT 1 FROM curation_sources WHERE id=? AND complete=1",
                                 (source,)).fetchone()
            if indexed is None:
                raise BoundaryError("local_curation", "source_index_incomplete")
            previous = {row[0] for row in db.execute(
                "SELECT run FROM curation_source_runs WHERE source=?", (source,))}
            pending = db.execute("SELECT 1 FROM local_source_pending WHERE artifact=?",
                                 (source,)).fetchone() is not None
            if self.legacy_guard and not pending and historical:
                db.executemany("INSERT OR IGNORE INTO local_legacy_unknown_runs VALUES(?)",
                               ((run,) for run in runs - previous))
            db.executemany("INSERT OR IGNORE INTO curation_source_runs VALUES(?,?)",
                           ((source, run) for run in runs))
        return runs

    def _inventory_pending(self, db: sqlite3.Connection) -> bool:
        if db.execute("SELECT 1 FROM local_source_pending LIMIT 1").fetchone():
            return True
        # A store writer outside this owner cannot silently add an unindexed source.
        store = ManifestArtifactStore(LocalBlobStore(self.store_dir, create=False, readonly=True))
        indexed = {row[0] for row in db.execute(
            "SELECT id FROM curation_sources WHERE complete=1")}
        for artifact in store.manifest_ids():
            manifest = store.get_manifest(artifact)
            if manifest.kind != "evidence":
                continue
            if artifact not in indexed:
                return True
            if manifest.parameters.value().get("schema") == "stpd/local-verified-bundle-v1":
                try:
                    runs = self._human_runs(store, artifact)
                except (BoundaryError, OSError, ValueError):
                    return True
                recorded = {row[0] for row in db.execute(
                    "SELECT run FROM curation_source_runs WHERE source=?", (artifact,))}
                if not runs <= recorded:
                    return True
        return False

    def gold_history_unknown(self, db: sqlite3.Connection, runs: Iterable[str]) -> bool:
        if not self.legacy_guard:
            return False
        if db.execute("SELECT 1 FROM local_legacy_unknown_runs WHERE run='*'").fetchone():
            return True
        return any(db.execute("SELECT 1 FROM local_legacy_unknown_runs WHERE run=?",
                              (run,)).fetchone() for run in runs)

    def _historical_claim_guard(self, db: sqlite3.Connection, purpose: str,
                                related: set[str]) -> None:
        if purpose == "gold" and any(
            db.execute("SELECT 1 FROM curation_uses WHERE run=? AND kind='evaluation'",
                       (run,)).fetchone() for run in related
        ):
            raise BoundaryError("local_curation", "gold_previously_used_for_evaluation")
        if purpose == "gold" and self.gold_history_unknown(db, related):
            raise BoundaryError("local_curation", "legacy_gold_history_unknown")

    def reserve_memory_dev(self, store: ManifestArtifactStore, train_source_id: str,
                           dev_source_id: str, model_operation_id: str,
                           evaluation_operation_id: str) -> bool:
        """Reserve one model-specific dev use in the existing local curation ledger."""
        from stpd.fullrun.text_menu_human_import import load_human_text_source

        digest(model_operation_id, "local_curation.model_operation", length=32)
        digest(evaluation_operation_id, "local_curation.evaluation_operation", length=32)
        train_manifest, _ = load_human_text_source(store, train_source_id)
        dev_manifest, _ = load_human_text_source(store, dev_source_id)
        if train_manifest.artifact_id == dev_manifest.artifact_id:
            raise BoundaryError("local_curation", "train_dev_source_overlap")

        def evidence_runs(manifest: Manifest) -> tuple[set[str], tuple[str, ...]]:
            parents = tuple(parent.artifact_id for parent in manifest.parents)
            if not parents:
                raise BoundaryError("local_curation", "source_evidence_required")
            runs: set[str] = set()
            for source in parents:
                native = self._human_runs(store, source)
                indexed = self.ledger.source_runs(source)
                if indexed is None or not native <= indexed:
                    raise BoundaryError("local_curation", "source_index_incomplete")
                runs.update(native)
            return runs, parents

        train_runs, train_evidence = evidence_runs(train_manifest)
        dev_runs, dev_evidence = evidence_runs(dev_manifest)
        if not train_runs or not dev_runs:
            raise BoundaryError("local_curation", "source_run_identity_missing")
        if set(train_evidence) & set(dev_evidence) or train_runs & dev_runs:
            raise BoundaryError("local_curation", "train_dev_exact_origin_overlap")
        with self.transaction() as db:
            def claim(source_id: str) -> tuple[str, set[str]] | None:
                selected = db.execute(
                    "SELECT id,purpose FROM curation_claims WHERE artifact=?", (source_id,)
                ).fetchone()
                if selected is None:
                    return None
                return selected[1], {row[0] for row in db.execute(
                    "SELECT run FROM curation_claim_runs WHERE claim=?", (selected[0],)
                )}

            if (claim(train_source_id) != ("training", train_runs)
                    or claim(dev_source_id) != ("training", dev_runs)):
                raise BoundaryError("local_curation", "source_claim_mismatch")
            train_related = self.ledger._groups(db, train_runs)
            dev_related = self.ledger._groups(db, dev_runs)
            semantic_overlap = bool(train_related & dev_related)
            if self.gold_history_unknown(db, dev_related):
                raise BoundaryError("local_curation", "legacy_exposure_unknown")
            if any(purpose in {"gold", "test"} for purpose, _ in
                   self.ledger._claims(db, dev_related).values()):
                raise BoundaryError("local_curation", "sealed_dev_source_forbidden")
            if any(db.execute(
                "SELECT 1 FROM curation_uses WHERE run=? AND kind='training' "
                "AND reference=?", (run, model_operation_id),
            ).fetchone() is None for run in train_runs):
                raise BoundaryError("local_curation", "model_training_use_unproven")
            if any(db.execute(
                "SELECT 1 FROM curation_source_uses WHERE source=? AND kind='training' "
                "AND reference=?", (source, model_operation_id),
            ).fetchone() is None for source in train_evidence):
                raise BoundaryError("local_curation", "model_training_use_unproven")
            db.executemany("INSERT OR IGNORE INTO curation_uses VALUES(?,?,?,?)",
                           ((run, "evaluation", evaluation_operation_id, time.time())
                            for run in sorted(dev_related)))
            db.executemany("INSERT OR IGNORE INTO curation_source_uses VALUES(?,?,?)",
                           ((source, "evaluation", evaluation_operation_id)
                            for source in sorted(dev_evidence)))
            return semantic_overlap
