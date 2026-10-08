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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stpd.fullrun.protocol_source import VerifiedProtocolSource

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

    def begin_managed_source(self, candidate: str, run: str, purpose: str, *,
                             source: str | None = None) -> None:
        """Purpose preflight and pending marker share one local writer transaction."""
        digest(candidate, "local_curation.candidate")
        if source is not None:
            digest(source, "local_curation.managed_source")
        ledger = self.ledger
        with self.transaction() as db:
            ledger.check_managed_purpose(db, run, purpose, source=source)
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

    def _verified_protocol_source(
        self, store: ManifestArtifactStore, source_id: str
    ) -> VerifiedProtocolSource:
        from stpd.fullrun.protocol_source import verify_protocol_source_partition

        if (
            not isinstance(store.blobs, LocalBlobStore)
            or store.blobs.root != self.store_dir.resolve()
        ):
            raise BoundaryError("local_curation", "store_identity_mismatch")
        _ = self.ledger
        return verify_protocol_source_partition(store, source_id)

    def _check_protocol_purpose(
        self,
        db: sqlite3.Connection,
        store: ManifestArtifactStore,
        source: VerifiedProtocolSource,
        *,
        require_claim: bool = False,
    ) -> set[str]:
        from stpd.fullrun.protocol_source import PARTITION_SCHEMA

        ledger = self.ledger
        related = ledger._groups(db, source.runs)
        purpose = "training" if source.split == "train" else "test"
        self._historical_claim_guard(db, purpose, related)
        for kind, artifact in ledger._claims(db, related).values():
            if kind == "gold":
                raise BoundaryError("local_curation", "gold_reserved_data")
            if kind != purpose or artifact is None:
                raise BoundaryError("local_curation", "protocol_split_purpose_overlap")
            if artifact != source.manifest.artifact_id:
                # DEV is kept distinct from TEST without extending legacy purposes.
                # A legacy claim cannot be relabelled as a protocol partition.
                info = store.get_manifest(artifact).parameters.value()
                if (
                    info.get("partition_schema") != PARTITION_SCHEMA
                    or info.get("split") != source.split
                ):
                    raise BoundaryError("local_curation", "protocol_split_purpose_overlap")
        if source.split != "train" and any(
            db.execute(
                "SELECT 1 FROM curation_uses WHERE run=? AND kind='training' UNION "
                "SELECT 1 FROM curation_source_uses u JOIN curation_source_runs r "
                "ON r.source=u.source WHERE r.run=? AND u.kind='training'",
                (run, run),
            ).fetchone()
            for run in related
        ):
            raise BoundaryError("local_curation", "held_out_data_cannot_train")
        if require_claim:
            claim = db.execute(
                "SELECT id,purpose FROM curation_claims WHERE artifact=?",
                (source.manifest.artifact_id,),
            ).fetchall()
            if len(claim) != 1 or claim[0][1] != purpose:
                raise BoundaryError("local_curation", "protocol_source_claim_mismatch")
            claimed = {
                row[0]
                for row in db.execute(
                    "SELECT run FROM curation_claim_runs WHERE claim=?", (claim[0][0],)
                )
            }
            if claimed != set(source.runs):
                raise BoundaryError("local_curation", "protocol_source_claim_mismatch")
            for raw_id in source.source_ids:
                ready = db.execute(
                    "SELECT s.complete FROM curation_sources s JOIN curation_exact_source_index i "
                    "ON i.source=s.id WHERE s.id=?",
                    (raw_id,),
                ).fetchone()
                expected_runs = {row["run_id"] for row in source.index if row["raw_id"] == raw_id}
                recorded = {
                    row[0]
                    for row in db.execute(
                        "SELECT run FROM curation_source_runs WHERE source=?", (raw_id,)
                    )
                }
                recorded_rows = {
                    tuple(row)
                    for row in db.execute(
                        "SELECT occurrence,transition_id FROM curation_source_decisions "
                        "WHERE source=?",
                        (raw_id,),
                    )
                }
                expected_rows = {
                    (row["occurrence_id"], "protocol-offer:" + row["capture_id"])
                    for row in source.index
                    if row["raw_id"] == raw_id
                }
                if ready != (1,) or recorded != expected_runs or recorded_rows != expected_rows:
                    raise BoundaryError("local_curation", "source_index_incomplete")
        return related

    def reserve_verified_protocol_source(
        self, store: ManifestArtifactStore, source_id: str
    ) -> dict:
        """Reserve exact typed Agent/Synthetic offers using the existing local ledger.

        Exact indexes locate raw offers/capsules and make no Human-transition claim.
        Original raw sources remain the exposure authority; a derivative is not an origin.
        """
        source = self._verified_protocol_source(store, source_id)
        purpose = "training" if source.split == "train" else "test"
        with self.transaction() as db:
            # Join related runs before checking purposes in the same transaction.
            for run in source.dataset.runs:
                identity = run.identity.value()
                episode = identity["episode"]
                for fingerprint in (
                    "protocol-group:" + run.source_group,
                    "protocol-runtime:" + episode["host"]["runtime_instance_id"],
                    "protocol-profile:" + episode["profile"]["generation_id"],
                ):
                    db.execute(
                        "INSERT OR IGNORE INTO curation_fingerprints VALUES(?,?)",
                        (fingerprint, run.run_id),
                    )
            self._check_protocol_purpose(db, store, source)
            existing = db.execute(
                "SELECT purpose,artifact FROM curation_claims WHERE id=?", (source_id,)
            ).fetchone()
            if existing is not None and existing != (purpose, source_id):
                raise BoundaryError("local_curation", "reservation_identity_conflict")
            for raw_id in source.source_ids:
                archive = store.get_manifest(raw_id).payload("archive").sha256
                old = db.execute(
                    "SELECT archive FROM curation_sources WHERE id=?", (raw_id,)
                ).fetchone()
                if old is not None and old != (archive,):
                    raise BoundaryError("local_curation", "source_identity_conflict")
                db.execute(
                    "INSERT OR IGNORE INTO curation_sources VALUES(?,?,0)", (raw_id, archive)
                )
                rows = [row for row in source.index if row["raw_id"] == raw_id]
                db.executemany(
                    "INSERT OR IGNORE INTO curation_source_runs VALUES(?,?)",
                    ((raw_id, row["run_id"]) for row in rows),
                )
                db.executemany(
                    "INSERT OR IGNORE INTO curation_source_decisions VALUES(?,?,?)",
                    (
                        (raw_id, row["occurrence_id"], "protocol-offer:" + row["capture_id"])
                        for row in rows
                    ),
                )
                db.execute("UPDATE curation_sources SET complete=1 WHERE id=?", (raw_id,))
                db.execute("INSERT OR IGNORE INTO curation_exact_source_index VALUES(?)", (raw_id,))
            db.execute(
                "INSERT OR IGNORE INTO curation_claims VALUES(?,?,?,?)",
                (source_id, purpose, source_id, time.time()),
            )
            db.executemany(
                "INSERT OR IGNORE INTO curation_claim_runs VALUES(?,?)",
                ((source_id, run) for run in sorted(source.runs)),
            )
            self._check_protocol_purpose(db, store, source, require_claim=True)
        return self._protocol_use_summary(source, None)

    @staticmethod
    def _protocol_use_summary(source: VerifiedProtocolSource, operation_id: str | None) -> dict:
        return {
            "artifact_id": source.manifest.artifact_id,
            "operation_id": operation_id,
            "source_ids": list(source.source_ids),
            "qualified_run_ids": sorted(source.runs),
            "source_groups": sorted(source.source_groups),
            "split": source.split,
            "input_spec": "s0-admitted-policy-offers-v1",
            "qualification": source.manifest.parameters.value()["qualification"],
            "ledger_scope": "original_raw_source_and_related_run_exposure",
            "historical_external_exposure": "unknown",
        }

    def record_verified_protocol_training_use(
        self, store: ManifestArtifactStore, source_id: str, operation_id: str
    ) -> dict:
        source = self._verified_protocol_source(store, source_id)
        digest(operation_id, "local_curation.protocol_training_operation", length=32)
        if source.split != "train":
            raise BoundaryError("local_curation", "train_only_source_required")
        with self.transaction() as db:
            related = self._check_protocol_purpose(db, store, source, require_claim=True)
            db.executemany(
                "INSERT OR IGNORE INTO curation_uses VALUES(?,?,?,?)",
                ((run, "training", operation_id, time.time()) for run in sorted(related)),
            )
            db.executemany(
                "INSERT OR IGNORE INTO curation_source_uses VALUES(?,?,?)",
                ((raw_id, "training", operation_id) for raw_id in source.source_ids),
            )
        return self.require_verified_protocol_training_use(store, source_id, operation_id)

    def require_verified_protocol_training_use(
        self, store: ManifestArtifactStore, source_id: str, operation_id: str
    ) -> dict:
        source = self._verified_protocol_source(store, source_id)
        digest(operation_id, "local_curation.protocol_training_operation", length=32)
        if source.split != "train":
            raise BoundaryError("local_curation", "train_only_source_required")
        with self.transaction() as db:
            self._check_protocol_purpose(db, store, source, require_claim=True)
            if any(
                db.execute(
                    "SELECT 1 FROM curation_source_uses WHERE source=? "
                    "AND kind='training' AND reference=?",
                    (raw_id, operation_id),
                ).fetchone()
                is None
                for raw_id in source.source_ids
            ) or any(
                db.execute(
                    "SELECT 1 FROM curation_uses WHERE run=? AND kind='training' AND reference=?",
                    (run, operation_id),
                ).fetchone()
                is None
                for run in source.runs
            ):
                raise BoundaryError("local_curation", "protocol_training_use_missing")
        return self._protocol_use_summary(source, operation_id)

    def record_verified_protocol_evaluation_use(
        self, store: ManifestArtifactStore, source_id: str, model_id: str, operation_id: str
    ) -> dict:
        """Record fixed-model DEV/TEST exposure separately from any training use."""
        source = self._verified_protocol_source(store, source_id)
        digest(operation_id, "local_curation.protocol_evaluation_operation", length=32)
        if source.split not in {"dev", "test"}:
            raise BoundaryError("local_curation", "held_out_source_required")
        model = store.get_manifest(digest(model_id, "local_curation.protocol_evaluation_model"))
        if model.kind != "model":
            raise BoundaryError("local_curation", "fixed_model_required")
        # Reject exact origins and source groups, including imported model ancestry
        # whose training exposure was recorded on a different host's ledger.
        from stpd.fullrun.protocol_source import (
            PARTITION_SCHEMA,
            verify_protocol_source_partition,
        )

        pending, seen = [model], set()
        while pending:
            current = pending.pop()
            if current.artifact_id in seen:
                continue
            seen.add(current.artifact_id)
            if current.parameters.value().get("partition_schema") == PARTITION_SCHEMA:
                ancestor = verify_protocol_source_partition(store, current.artifact_id)
                if ancestor.source_groups & source.source_groups:
                    raise BoundaryError("local_curation", "model_held_out_source_group_overlap")
            if len(seen) > 512:
                raise BoundaryError("local_curation", "lineage_limit")
            pending.extend(store.get_manifest(parent.artifact_id) for parent in current.parents)
        if seen & set(source.source_ids):
            raise BoundaryError("local_curation", "model_held_out_origin_overlap")
        with self.transaction() as db:
            related = self._check_protocol_purpose(db, store, source, require_claim=True)
            db.executemany(
                "INSERT OR IGNORE INTO curation_uses VALUES(?,?,?,?)",
                ((run, "evaluation", operation_id, time.time()) for run in sorted(related)),
            )
            db.executemany(
                "INSERT OR IGNORE INTO curation_source_uses VALUES(?,?,?)",
                ((raw_id, "evaluation", operation_id) for raw_id in source.source_ids),
            )
        return {
            **self._protocol_use_summary(source, operation_id),
            "model_id": model_id,
            "use": "evaluation",
            "clean_held_out_claim": False,
        }

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

    def _training_dataset_bindings(self, store: ManifestArtifactStore,
                                   dataset_ids: tuple[str, ...], operation_id: str
                                   ) -> list[dict]:
        """Reproject canonical selections; callers cannot supply source/run identities."""
        from stpd.fullrun.curated_dataset import SCHEMA, load_selection
        from stpd.fullrun.dataset_policy import training_sources
        from stpd.fullrun.decision_dataset import DecisionDataset
        from stpd.fullrun.decision_spool import SpoolSelection

        digest(operation_id, "local_curation.training_operation", length=32)
        if (not isinstance(store.blobs, LocalBlobStore)
                or store.blobs.root != self.store_dir.resolve()):
            raise BoundaryError("local_curation", "store_identity_mismatch")
        if not isinstance(dataset_ids, tuple) or not 1 <= len(dataset_ids) <= 100:
            raise BoundaryError("local_curation", "training_dataset_selection_invalid")
        dataset_ids = tuple(digest(identity, "local_curation.training_dataset")
                            for identity in dataset_ids)
        if len(set(dataset_ids)) != len(dataset_ids):
            raise BoundaryError("local_curation", "training_dataset_selection_invalid")
        bindings = []
        for identity in sorted(dataset_ids):
            manifest = store.get_manifest(digest(identity, "local_curation.training_dataset"))
            info = manifest.parameters.value()
            training_sources(store, identity)
            if (manifest.kind != "dataset" or info.get("schema") != SCHEMA
                    or info.get("purpose") != "training"):
                raise BoundaryError("local_curation", "curated_training_dataset_required")
            pending = [manifest]
            seen: set[str] = set()
            sources: dict[str, Manifest] = {}
            while pending:
                item = pending.pop()
                if item.artifact_id in seen:
                    continue
                seen.add(item.artifact_id)
                if len(seen) > 512:
                    raise BoundaryError("curation", "lineage_limit")
                if item.kind == "evidence":
                    if item.parameters.value().get("schema") not in {
                        "stpd/received-bundle-v1", "stpd/local-verified-bundle-v1",
                    }:
                        raise BoundaryError("local_curation", "source_evidence_required")
                    sources[item.artifact_id] = item
                pending.extend(store.get_manifest(parent.artifact_id) for parent in item.parents)
            if not sources:
                raise BoundaryError("local_curation", "source_evidence_required")
            memo: dict[str, DecisionDataset] = {}
            try:
                dataset = load_selection(store, manifest, cache=None, memo=memo)
                runs = dataset.run_ids
                if not runs:
                    raise BoundaryError("local_curation", "source_run_identity_missing")
                bindings.append({
                    "artifact_id": identity, "logical_id": dataset.logical_id,
                    "qualified_run_ids": sorted(runs),
                    "sources": [{"artifact_id": source.artifact_id,
                                 "archive_sha256": source.payload("archive").sha256}
                                for source in sorted(sources.values(),
                                                     key=lambda item: item.artifact_id)],
                })
            finally:
                for selected in memo.values():
                    if isinstance(selected.records, SpoolSelection):
                        selected.records.owner.close()
        self._check_training_bindings(bindings)
        return bindings

    def _check_training_bindings(self, bindings: list[dict]) -> None:
        ledger = self.ledger
        with self.transaction() as db:
            for binding in bindings:
                runs = set(binding["qualified_run_ids"])
                claims = db.execute("SELECT id,purpose FROM curation_claims WHERE artifact=?",
                                    (binding["artifact_id"],)).fetchall()
                if len(claims) != 1 or claims[0][1] != "training":
                    raise BoundaryError("curation", "training_claim_mismatch")
                claimed = {row[0] for row in db.execute(
                    "SELECT run FROM curation_claim_runs WHERE claim=?", (claims[0][0],))}
                if claimed != runs:
                    raise BoundaryError("curation", "training_claim_mismatch")
                indexed: set[str] = set()
                for source in binding["sources"]:
                    ready = db.execute(
                        "SELECT s.archive FROM curation_sources s "
                        "JOIN curation_exact_source_index e ON e.source=s.id "
                        "WHERE s.id=? AND s.complete=1", (source["artifact_id"],)).fetchone()
                    if ready is None:
                        raise BoundaryError("curation", "source_index_incomplete")
                    if ready[0] != source["archive_sha256"]:
                        raise BoundaryError("curation", "source_identity_conflict")
                    indexed.update(row[0] for row in db.execute(
                        "SELECT run FROM curation_source_runs WHERE source=?",
                        (source["artifact_id"],)))
                if not runs <= indexed:
                    raise BoundaryError("curation", "source_run_identity_mismatch")
                related = ledger._groups(db, indexed)
                if any(purpose in {"test", "gold"} for purpose, _ in
                       ledger._claims(db, related).values()):
                    raise BoundaryError("curation", "held_out_data_cannot_train")

    @staticmethod
    def _training_use_summary(bindings: list[dict], operation_id: str) -> dict:
        return {"operation_id": operation_id, "datasets": bindings,
                "input_family": "canonical_curated_decisions",
                "ledger_scope": "source_and_run_exposure_not_dataset_operation_binding",
                "historical_external_exposure": "unknown"}

    def reserve_training_datasets(self, store: ManifestArtifactStore,
                                  dataset_ids: tuple[str, ...], operation_id: str) -> dict:
        """Reserve before derivatives/compute; history unknown does not ban training.

        Existing rows record source/run exposure, not an immutable dataset-to-operation
        binding. Consumers must also bind dataset IDs and operation in their run inputs.
        A failed preparation retains its conservative reservation, never a completion.
        """
        bindings = self._training_dataset_bindings(store, dataset_ids, operation_id)
        for binding in bindings:
            for source in binding["sources"]:
                self.ledger.use_source(source["artifact_id"], "training", operation_id)
            self.ledger.use(binding["qualified_run_ids"], "training", operation_id)
        return self.require_training_datasets(store, dataset_ids, operation_id)

    def require_training_datasets(self, store: ManifestArtifactStore,
                                  dataset_ids: tuple[str, ...], operation_id: str) -> dict:
        """Independently verify an admitted downstream operation without adding use rows."""
        bindings = self._training_dataset_bindings(store, dataset_ids, operation_id)
        for binding in bindings:
            self.ledger.require_training_use(
                binding["artifact_id"],
                (source["artifact_id"] for source in binding["sources"]),
                binding["qualified_run_ids"], operation_id,
            )
        return self._training_use_summary(bindings, operation_id)

    def reserve_allocation_dev(self, store: ManifestArtifactStore, model_id: str,
                               allocation_id: str, training_operation_id: str,
                               evaluation_operation_id: str) -> dict:
        """Reserve diagnostic dev from a training-purpose allocation, never clean test.

        The caller verifies its model's immutable training-operation binding separately;
        the existing ledger alone cannot establish that dataset/operation relationship.
        """
        from stpd.fullrun.dataset_policy import training_sources
        from stpd.fullrun.decision_spool import SpoolSelection
        from stpd.fullrun.decision_training import load_allocation

        digest(evaluation_operation_id, "local_curation.evaluation_operation", length=32)
        model = store.get_manifest(digest(model_id, "local_curation.evaluation_model"))
        if model.kind != "model":
            raise BoundaryError("local_curation", "model_training_lineage_mismatch")
        training_sources(store, model_id)
        pending = [model]
        seen: set[str] = set()
        while pending:
            item = pending.pop()
            if item.artifact_id in seen:
                continue
            seen.add(item.artifact_id)
            if len(seen) > 512:
                raise BoundaryError("curation", "lineage_limit")
            pending.extend(store.get_manifest(parent.artifact_id) for parent in item.parents)
        if allocation_id not in seen:
            raise BoundaryError("local_curation", "model_allocation_mismatch")
        manifest, dataset, allocation = load_allocation(store, allocation_id)
        try:
            admission = self.require_training_datasets(
                store, (manifest.parent("dataset"),), training_operation_id)
            dev_members = [member for member in allocation["members"] if member["split"] == "dev"]
            runs = {member["run_id"] for member in dev_members}
            archives = {member["source_archive_sha256"] for member in dev_members}
            sources = admission["datasets"][0]["sources"]
            if not archives <= {source["archive_sha256"] for source in sources}:
                raise BoundaryError("local_curation", "allocation_source_mismatch")
            dev_sources = {source["artifact_id"] for source in sources
                           if source["archive_sha256"] in archives}
            train_runs = {member["run_id"] for member in allocation["members"]
                          if member["split"] == "train"}
            if not runs:
                raise BoundaryError("local_curation", "empty_dev_allocation")
            with self.transaction() as db:
                related = self.ledger._groups(db, runs)
                if any(purpose in {"test", "gold"} for purpose, _ in
                       self.ledger._claims(db, related).values()):
                    raise BoundaryError("local_curation", "sealed_dev_source_forbidden")
                overlap = bool(related & self.ledger._groups(db, train_runs))
                db.executemany("INSERT OR IGNORE INTO curation_uses VALUES(?,?,?,?)",
                               ((run, "evaluation", evaluation_operation_id, time.time())
                                for run in sorted(related)))
                db.executemany("INSERT OR IGNORE INTO curation_source_uses VALUES(?,?,?)",
                               ((source, "evaluation", evaluation_operation_id)
                                for source in sorted(dev_sources)))
            return {"model_id": model_id, "allocation_id": allocation_id,
                    "training_operation_id": training_operation_id,
                    "evaluation_operation_id": evaluation_operation_id,
                    "qualified_run_ids": sorted(runs), "semantic_overlap": overlap,
                    "evaluation_scope": "within_training_purpose_allocation",
                    "historical_external_exposure": "unknown",
                    "physical_game_independence": "unresolved", "clean_held_out_claim": False,
                    "ledger_scope": admission["ledger_scope"]}
        finally:
            if isinstance(dataset.records, SpoolSelection):
                dataset.records.owner.close()

    def reserve_memory_dev(self, store: ManifestArtifactStore, train_source_id: str,
                           dev_source_id: str, model_operation_id: str,
                           evaluation_operation_id: str) -> bool:
        """Reserve one model-specific dev use in the existing local curation ledger."""
        from stpd.fullrun.managed_text_menu_import import (
            SOURCE_SCHEMA as MANAGED_SOURCE_SCHEMA,
        )
        from stpd.fullrun.managed_text_menu_import import load_managed_text_menu_source
        from stpd.fullrun.text_menu_human_import import load_human_text_source

        digest(model_operation_id, "local_curation.model_operation", length=32)
        digest(evaluation_operation_id, "local_curation.evaluation_operation", length=32)
        train_schema = store.get_manifest(train_source_id).parameters.value().get("schema")
        dev_schema = store.get_manifest(dev_source_id).parameters.value().get("schema")
        if train_schema != dev_schema:
            raise BoundaryError("local_curation", "train_dev_source_profile_mismatch")
        managed = train_schema == MANAGED_SOURCE_SCHEMA
        if managed:
            train_source = load_managed_text_menu_source(store, train_source_id)
            dev_source = load_managed_text_menu_source(store, dev_source_id)
            train_manifest, dev_manifest = train_source.manifest, dev_source.manifest
        else:
            train_manifest, _ = load_human_text_source(store, train_source_id)
            dev_manifest, _ = load_human_text_source(store, dev_source_id)
        if train_manifest.artifact_id == dev_manifest.artifact_id:
            raise BoundaryError("local_curation", "train_dev_source_overlap")

        def evidence_runs(manifest: Manifest, split_run: str | None
                          ) -> tuple[set[str], tuple[str, ...]]:
            if split_run is not None:
                # Managed training exposes the typed source itself, whose parent
                # is the reverified immutable report. Its split is not session ID.
                indexed = self.ledger.source_runs(manifest.artifact_id)
                if indexed != {split_run} or not self.ledger.exact_source_ready(
                    manifest.artifact_id
                ):
                    raise BoundaryError("local_curation", "source_index_incomplete")
                return {split_run}, (manifest.artifact_id,)
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

        train_runs, train_evidence = evidence_runs(
            train_manifest, train_source.split_run_id if managed else None)
        dev_runs, dev_evidence = evidence_runs(
            dev_manifest, dev_source.split_run_id if managed else None)
        if not train_runs or not dev_runs:
            raise BoundaryError("local_curation", "source_run_identity_missing")
        managed_origins_overlap = False
        if managed:
            train_origins = {train_source.report_id} | {
                item.event_artifact_id for item in train_source.inputs}
            dev_origins = {dev_source.report_id} | {
                item.event_artifact_id for item in dev_source.inputs}
            managed_origins_overlap = bool(train_origins & dev_origins)
        if (set(train_evidence) & set(dev_evidence) or train_runs & dev_runs
                or managed_origins_overlap):
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
