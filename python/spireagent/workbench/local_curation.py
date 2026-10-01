"""Durable curation authority for one explicitly created local store.

The registry is a disposable index. This database and the store owner marker are
not: opening an existing workspace never creates or repairs either one.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sts2_platform_evidence.human_session_bundle_v3 import HumanSessionBundleV3

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.hub.database import create_private_database
from spireagent.json_boundary import BoundaryError, FrozenObject, digest
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
USER_DECLARATION_SCHEMA = "stpd/local-user-declaration-v1"
USER_DECLARATION_TABLE = "local_user_declarations"
DECLARATION_DATASET_SCHEMAS = {
    "stpd/decision-dataset-v1",
    "stpd/curated-decision-dataset-v1",
}
DECLARATION_SOURCE_SCHEMAS = {
    "stpd/received-bundle-v1",
    "stpd/local-verified-bundle-v1",
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

    @staticmethod
    def _timestamp(value: object, stage: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 64:
            raise BoundaryError(stage, "invalid_timestamp")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise BoundaryError(stage, "invalid_timestamp") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise BoundaryError(stage, "invalid_timestamp")
        return value

    def _declaration_store(self, store: ManifestArtifactStore) -> None:
        if (not isinstance(store, ManifestArtifactStore)
                or not isinstance(store.blobs, LocalBlobStore)
                or store.blobs.root != self.store_dir.resolve()
                or self.path.is_symlink() or self.store_dir.is_symlink()):
            raise BoundaryError("local_curation", "store_identity_mismatch")

    @staticmethod
    def _declaration_dataset(store: ManifestArtifactStore, identity: str,
                             visited: set[str], sources: dict[str, Manifest],
                             datasets: dict[str, Manifest]) -> None:
        """Validate exact supported manifest recipes without opening any payload."""
        from stpd.fullrun.curated_dataset import SCHEMA as CURATED_SCHEMA
        from stpd.fullrun.decision_dataset import SCHEMA as DECISION_SCHEMA
        from stpd.fullrun.decision_dataset import SelectionRules

        if identity in visited:
            return
        if len(visited) >= 512:
            raise BoundaryError("local_curation", "declaration_lineage_limit")
        visited.add(identity)
        manifest = store.get_manifest(identity)
        info = manifest.parameters.value()
        if manifest.kind == "evidence":
            schema = info.get("schema")
            if not isinstance(schema, str) or schema not in DECLARATION_SOURCE_SCHEMAS:
                raise BoundaryError("local_curation", "declaration_source_unsupported")
            if ((schema == "stpd/received-bundle-v1" and info.get("disposition") != "verified")
                    or (schema == "stpd/local-verified-bundle-v1"
                        and info.get("disposition") != "locally_verified")):
                raise BoundaryError("local_curation", "declaration_source_unsupported")
            if [payload.role for payload in manifest.payloads].count("archive") != 1:
                raise BoundaryError("local_curation", "declaration_source_unsupported")
            sources[identity] = manifest
            return
        schema = info.get("schema")
        if (manifest.kind != "dataset" or not isinstance(schema, str)
                or schema not in DECLARATION_DATASET_SCHEMAS):
            raise BoundaryError("local_curation", "declaration_dataset_unsupported")
        if schema == DECISION_SCHEMA:
            if set(info) != {"schema", "logical_id", "rules", "records", "scope", "split_status"}:
                raise BoundaryError("local_curation", "declaration_dataset_unsupported")
            if ([payload.role for payload in manifest.payloads] != ["records", "selection"]
                    or info["scope"] != "platform_verified"
                    or type(info["records"]) is not int or info["records"] < 1):
                raise BoundaryError("local_curation", "declaration_dataset_unsupported")
            SelectionRules.decode(info["rules"])
            digest(info["logical_id"], "local_curation.declaration_logical_id")
            expected_prefix = "source_"
        else:
            expected = {
                "schema", "logical_id", "purpose", "rules", "merging", "paired_training",
                "records", "runs", "scope", "split_status", "materialization",
                "sealed_test", "reservation", "isolation", "historical_external_exposure",
            }
            if set(info) != expected or info["purpose"] != "training":
                raise BoundaryError("local_curation", "declaration_dataset_unsupported")
            if ([payload.role for payload in manifest.payloads] != ["selection"]
                    or type(info["merging"]) is not bool
                    or info["scope"] != "platform_verified"
                    or type(info["records"]) is not int or info["records"] < 1
                    or type(info["runs"]) is not int or info["runs"] < 1
                    or info["paired_training"] is not None
                    or info["sealed_test"] is not False
                    or info["reservation"] is not None
                    or info["materialization"] != "on_demand"
                    or info["isolation"] != "ordinary"
                    or info["historical_external_exposure"] != "unknown"):
                raise BoundaryError("local_curation", "declaration_dataset_unsupported")
            SelectionRules.decode(info["rules"])
            digest(info["logical_id"], "local_curation.declaration_logical_id")
            expected_prefix = "dataset_" if info["merging"] else "source_"
        if not manifest.parents or len(manifest.parents) > 100:
            raise BoundaryError("local_curation", "declaration_dataset_unsupported")
        parent_ids = [parent.artifact_id for parent in manifest.parents]
        if (len(set(parent_ids)) != len(parent_ids)
                or {parent.role for parent in manifest.parents}
                != {expected_prefix + parent_id for parent_id in parent_ids}):
            raise BoundaryError("local_curation", "declaration_dataset_unsupported")
        datasets[identity] = manifest
        for parent in manifest.parents:
            parent_manifest = store.get_manifest(parent.artifact_id)
            if schema == DECISION_SCHEMA or (schema == CURATED_SCHEMA and not info["merging"]):
                if parent_manifest.kind != "evidence":
                    raise BoundaryError("local_curation", "declaration_dataset_unsupported")
            elif parent_manifest.kind != "dataset":
                raise BoundaryError("local_curation", "declaration_dataset_unsupported")
            LocalCurationOwner._declaration_dataset(
                store, parent.artifact_id, visited, sources, datasets,
            )

    @staticmethod
    def _declaration_rules_contain(candidate: dict, declared: dict) -> bool:
        from stpd.fullrun.decision_dataset import SelectionRules

        candidate_rules = SelectionRules.decode(candidate)
        declared_rules = SelectionRules.decode(declared)
        if ((declared_rules.complete_only and not candidate_rules.complete_only)
                or (declared_rules.wins_only and not candidate_rules.wins_only)
                or (declared_rules.no_failures_only and not candidate_rules.no_failures_only)):
            return False
        candidate_filters = candidate_rules.filters.value()
        declared_filters = declared_rules.filters.value()
        return all(key in candidate_filters
                   and set(candidate_filters[key]) <= set(values)
                   for key, values in declared_filters.items())

    def _declaration_preproof(
        self, store: ManifestArtifactStore, candidate_id: str, scope: dict,
        declared_manifests: dict[str, Manifest],
    ) -> None:
        """Prove recipe containment from manifests before any source payload is read."""
        from stpd.fullrun.curated_dataset import SCHEMA as CURATED_SCHEMA
        from stpd.fullrun.decision_dataset import SCHEMA as DECISION_SCHEMA

        declared_ids = set(scope["dataset_ids"])
        declared_sources = {item["artifact_id"]: item["archive_sha256"]
                            for item in scope["sources"]}
        visited: set[str] = set()

        def covered(identity: str) -> None:
            if identity in declared_ids:
                return
            if identity in visited or len(visited) >= 512:
                if identity in visited:
                    return
                raise BoundaryError("local_curation", "declaration_lineage_limit")
            visited.add(identity)
            manifest = store.get_manifest(identity)
            # Keep the existing manifest-only held-out ancestry gate ahead of
            # any selection or source payload reads.
            from stpd.fullrun.dataset_policy import training_sources

            training_sources(store, identity)
            info = manifest.parameters.value()
            schema = info.get("schema")
            if (manifest.kind != "dataset" or not isinstance(schema, str)
                    or schema not in DECLARATION_DATASET_SCHEMAS):
                raise BoundaryError("local_curation", "declaration_coverage_unproven")
            self._declaration_dataset(store, identity, set(), {}, {})
            if schema == CURATED_SCHEMA and info["merging"]:
                # A merge can only remove from already covered immutable parents.
                for parent in manifest.parents:
                    covered(parent.artifact_id)
                return
            if schema in (DECISION_SCHEMA, CURATED_SCHEMA):
                leaf_sources: dict[str, Manifest] = {}
                leaf_datasets: dict[str, Manifest] = {}
                self._declaration_dataset(
                    store, identity, set(), leaf_sources, leaf_datasets,
                )
                inventory = {source_id: source.payload("archive").sha256
                             for source_id, source in leaf_sources.items()}
                candidates = []
                for declared_id, declared_manifest in declared_manifests.items():
                    declared_info = declared_manifest.parameters.value()
                    if declared_info.get("schema") != DECISION_SCHEMA:
                        continue
                    if set(inventory) <= set(declared_sources) and all(
                        declared_sources[source_id] == archive
                        for source_id, archive in inventory.items()
                    ):
                        # Direct source recipes must be bounded by one declared canonical
                        # dataset's complete archive inventory, not a union of siblings.
                        canonical_scope = self._declaration_scope(store, (declared_id,))
                        canonical_sources = {
                            item["artifact_id"]: item["archive_sha256"]
                            for item in canonical_scope["sources"]
                        }
                        if (not set(inventory) <= set(canonical_sources)
                                or any(canonical_sources[key] != value
                                       for key, value in inventory.items())):
                            continue
                        unrestricted = (
                            not declared_info["rules"]["complete_only"]
                            and not declared_info["rules"]["wins_only"]
                            and not declared_info["rules"]["no_failures_only"]
                            and not declared_info["rules"]["filters"]
                        )
                        if not unrestricted and inventory != canonical_sources:
                            continue
                        if not self._declaration_rules_contain(
                            info["rules"], declared_info["rules"],
                        ):
                            continue
                        candidates.append(declared_id)
                if not candidates:
                    raise BoundaryError("local_curation", "declaration_coverage_unproven")
                return
            raise BoundaryError("local_curation", "declaration_coverage_unproven")

        covered(digest(candidate_id, "local_curation.training_dataset"))

    def _declaration_scope(self, store: ManifestArtifactStore,
                           dataset_ids: tuple[str, ...]) -> dict:
        self._declaration_store(store)
        if not isinstance(dataset_ids, tuple) or not 1 <= len(dataset_ids) <= 100:
            raise BoundaryError("local_curation", "declaration_scope_invalid")
        identities = tuple(digest(item, "local_curation.declaration_dataset")
                           for item in dataset_ids)
        if len(set(identities)) != len(identities):
            raise BoundaryError("local_curation", "declaration_scope_invalid")
        from stpd.fullrun.curated_dataset import SCHEMA as CURATED_SCHEMA
        from stpd.fullrun.decision_dataset import SCHEMA as DECISION_SCHEMA

        sources: dict[str, Manifest] = {}
        datasets: dict[str, Manifest] = {}
        visited: set[str] = set()
        for identity in sorted(identities):
            root = store.get_manifest(identity)
            # Detect unsupported held-out ancestors without opening any payload.
            from stpd.fullrun.dataset_policy import training_sources

            training_sources(store, identity)
            root_schema = root.parameters.value().get("schema")
            if (root.kind != "dataset" or not isinstance(root_schema, str)
                    or root_schema not in {DECISION_SCHEMA, CURATED_SCHEMA}):
                raise BoundaryError("local_curation", "declaration_dataset_unsupported")
            self._declaration_dataset(store, identity, visited, sources, datasets)
        if not sources:
            raise BoundaryError("local_curation", "declaration_source_missing")
        source_rows = []
        for identity, source in sorted(sources.items()):
            source_rows.append({"artifact_id": identity,
                                "archive_sha256": source.payload("archive").sha256})
        try:
            with closing(sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro", uri=True,
            )) as db:
                row = db.execute("SELECT workspace,ledger,store,store_path "
                                 "FROM local_curation_identity").fetchone()
                if row != self.identity or db.execute(
                    "SELECT count(*) FROM local_curation_identity").fetchone()[0] != 1:
                    raise BoundaryError("local_curation", "ledger_identity_mismatch")
                self._validate_existing(db)
                run_ids: set[str] = set()
                for source_row in source_rows:
                    indexed = db.execute(
                        "SELECT archive,complete FROM curation_sources WHERE id=?",
                        (source_row["artifact_id"],),
                    ).fetchone()
                    if (indexed != (source_row["archive_sha256"], 1)
                            or not db.execute(
                                "SELECT 1 FROM curation_exact_source_index WHERE source=?",
                                (source_row["artifact_id"],),
                            ).fetchone()):
                        raise BoundaryError("local_curation", "source_index_incomplete")
                    run_ids.update(row[0] for row in db.execute(
                        "SELECT run FROM curation_source_runs WHERE source=?",
                        (source_row["artifact_id"],),
                    ))
        except sqlite3.DatabaseError as error:
            raise BoundaryError("local_curation", "ledger_recovery_required") from error
        if not run_ids:
            raise BoundaryError("local_curation", "declaration_source_run_missing")
        return {
            "dataset_ids": sorted(identities), "sources": source_rows,
            "qualified_run_ids": sorted(run_ids),
            "scope_boundary": "exact_frozen_dataset_artifacts",
        }

    def _declaration_artifact(self, store: ManifestArtifactStore, identity: str,
                              scope: dict | None = None) -> tuple[Manifest, dict]:
        manifest = store.get_manifest(digest(identity, "local_curation.declaration_id"))
        info = manifest.parameters.value()
        required = {
            "schema", "request_id", "recorded_at", "owner", "scope", "statement",
            "self_recorded", "project_training_authorized_now", "historical_authorization",
            "historical_manual_exposure", "human_origin_evidence", "sharing_permission",
        }
        if (manifest.kind != "analysis" or manifest.payloads or set(info) != required
                or info["schema"] != USER_DECLARATION_SCHEMA):
            raise BoundaryError("local_curation", "declaration_artifact_unavailable")
        if info["owner"] != {"workspace_id": self.identity[0],
                             "ledger_id": self.identity[1], "store_id": self.identity[2]}:
            raise BoundaryError("local_curation", "declaration_owner_mismatch")
        digest(info["request_id"], "local_curation.declaration_request", length=32)
        self._timestamp(info["recorded_at"], "local_curation.declaration")
        if (info["self_recorded"] is not True
                or info["project_training_authorized_now"] is not True
                or info["historical_authorization"] != "unknown"
                or info["historical_manual_exposure"] != "unknown"
                or info["human_origin_evidence"] != "user_declaration_not_machine_proof"
                or info["sharing_permission"] != "not_granted_by_this_record"):
            raise BoundaryError("local_curation", "declaration_statement_invalid")
        statement = info["statement"]
        if (not isinstance(statement, dict)
                or set(statement) != {"text", "text_basis", "source"}
                or not isinstance(statement["text"], str)
                or not statement["text"] or len(statement["text"]) > 4096
                or not isinstance(statement["text_basis"], str)
                or statement["text_basis"] not in {"verbatim", "normalized"}):
            raise BoundaryError("local_curation", "declaration_statement_invalid")
        source = statement["source"]
        if (not isinstance(source, dict)
                or set(source) != {"kind", "reference", "written_at"}
                or source["kind"] != "direct_user_instruction"
                or not isinstance(source["reference"], str) or not source["reference"]
                or len(source["reference"]) > 256):
            raise BoundaryError("local_curation", "declaration_statement_invalid")
        self._timestamp(source["written_at"], "local_curation.declaration")
        actual_scope = info["scope"]
        if (not isinstance(actual_scope, dict)
                or set(actual_scope) != {"dataset_ids", "sources", "qualified_run_ids",
                                         "scope_boundary"}
                or actual_scope["scope_boundary"] != "exact_frozen_dataset_artifacts"
                or not isinstance(actual_scope["dataset_ids"], list)
                or not 1 <= len(actual_scope["dataset_ids"]) <= 100
                or any(not isinstance(item, str) for item in actual_scope["dataset_ids"])
                or actual_scope["dataset_ids"] != sorted(set(actual_scope["dataset_ids"]))
                or not isinstance(actual_scope["sources"], list)
                or not isinstance(actual_scope["qualified_run_ids"], list)
                or any(not isinstance(item, str) for item in actual_scope["qualified_run_ids"])
                or actual_scope["qualified_run_ids"] != sorted(
                    set(actual_scope["qualified_run_ids"]),
                )):
            raise BoundaryError("local_curation", "declaration_scope_invalid")
        for dataset in actual_scope["dataset_ids"]:
            digest(dataset, "local_curation.declaration_dataset")
        source_ids = []
        for item in actual_scope["sources"]:
            if (not isinstance(item, dict) or set(item) != {"artifact_id", "archive_sha256"}):
                raise BoundaryError("local_curation", "declaration_scope_invalid")
            source_ids.append(digest(item["artifact_id"], "local_curation.declaration_source"))
            digest(item["archive_sha256"], "local_curation.declaration_archive")
        if source_ids != sorted(set(source_ids)) or not source_ids:
            raise BoundaryError("local_curation", "declaration_scope_invalid")
        for run in actual_scope["qualified_run_ids"]:
            if not isinstance(run, str) or not run or len(run) > 512:
                raise BoundaryError("local_curation", "declaration_scope_invalid")
        expected_parents = tuple(sorted(
            [Parent("dataset_" + item, item) for item in actual_scope["dataset_ids"]]
            + [Parent("source_" + item["artifact_id"], item["artifact_id"])
               for item in actual_scope["sources"]]
        ))
        if manifest.parents != expected_parents or (scope is not None and actual_scope != scope):
            raise BoundaryError("local_curation", "declaration_binding_mismatch")
        return manifest, info

    def _check_declaration_guards(self, db: sqlite3.Connection, scope: dict) -> None:
        runs = set(scope["qualified_run_ids"])
        related = LocalLedger._groups(db, runs)
        if any(purpose in {"test", "gold"}
               for purpose, _ in LocalLedger._claims(db, related).values()):
            raise BoundaryError("local_curation", "held_out_data_cannot_train")
        for source in scope["sources"]:
            if db.execute(
                "SELECT 1 FROM curation_source_uses WHERE source=? AND kind IN ('test','gold')",
                (source["artifact_id"],),
            ).fetchone():
                raise BoundaryError("local_curation", "held_out_data_cannot_train")

    def register_user_declaration(
        self, store: ManifestArtifactStore, dataset_ids: tuple[str, ...], producer: Producer, *,
        request_id: str, statement: str, statement_source: dict,
        self_recorded: bool, project_training_authorized_now: bool,
    ) -> dict:
        """Publish an immutable current declaration and append its owner registration."""
        request = digest(request_id, "local_curation.declaration_request", length=32)
        if not isinstance(producer, Producer):
            raise BoundaryError("local_curation", "declaration_producer_invalid")
        if (not isinstance(statement, str) or not statement or len(statement) > 4096
                or type(self_recorded) is not bool
                or type(project_training_authorized_now) is not bool
                or self_recorded is not True or project_training_authorized_now is not True):
            raise BoundaryError("local_curation", "declaration_statement_invalid")
        if (not isinstance(statement_source, dict)
                or set(statement_source) != {"kind", "reference", "written_at", "text_basis"}
                or statement_source["kind"] != "direct_user_instruction"
                or not isinstance(statement_source["reference"], str)
                or not statement_source["reference"] or len(statement_source["reference"]) > 256
                or not isinstance(statement_source["text_basis"], str)
                or statement_source["text_basis"] not in {"verbatim", "normalized"}):
            raise BoundaryError("local_curation", "declaration_statement_invalid")
        self._timestamp(statement_source["written_at"], "local_curation.declaration")
        scope = self._declaration_scope(store, dataset_ids)
        parent_refs = tuple(sorted(
            [Parent("dataset_" + item, item) for item in scope["dataset_ids"]]
            + [Parent("source_" + item["artifact_id"], item["artifact_id"])
               for item in scope["sources"]]
        ))
        source = {key: statement_source[key] for key in ("kind", "reference", "written_at")}
        try:
            with closing(sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro", uri=True,
            )) as db:
                tables = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if USER_DECLARATION_TABLE in tables:
                    rows = db.execute(
                        "SELECT dataset,artifact,recorded_at FROM local_user_declarations "
                        "WHERE request_id=? ORDER BY dataset", (request,),
                    ).fetchall()
                else:
                    rows = []
        except sqlite3.DatabaseError as error:
            raise BoundaryError("local_curation", "ledger_recovery_required") from error
        if rows:
            if ([row[0] for row in rows] != scope["dataset_ids"]
                    or len({(row[1], row[2]) for row in rows}) != 1):
                raise BoundaryError("local_curation", "declaration_request_conflict")
            artifact, recorded_at = rows[0][1], rows[0][2]
            _, info = self._declaration_artifact(store, artifact, scope)
            if (info["request_id"] != request or info["recorded_at"] != recorded_at
                    or info["statement"] != {"text": statement,
                                              "text_basis": statement_source["text_basis"],
                                              "source": source}):
                raise BoundaryError("local_curation", "declaration_request_conflict")
            with self.transaction() as db:
                self._check_declaration_guards(db, scope)
            return {"status": "registered", "declaration_id": artifact,
                    "recorded_at": recorded_at, "dataset_ids": scope["dataset_ids"],
                    "scope": scope}

        with self.transaction() as db:
            self._check_declaration_guards(db, scope)
        recorded_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z",
        )
        manifest = Manifest("analysis", producer, parent_refs, (), FrozenObject.of({
            "schema": USER_DECLARATION_SCHEMA, "request_id": request,
            "recorded_at": recorded_at,
            "owner": {"workspace_id": self.identity[0], "ledger_id": self.identity[1],
                      "store_id": self.identity[2]},
            "scope": scope,
            "statement": {"text": statement, "text_basis": statement_source["text_basis"],
                          "source": source},
            "self_recorded": self_recorded,
            "project_training_authorized_now": project_training_authorized_now,
            "historical_authorization": "unknown",
            "historical_manual_exposure": "unknown",
            "human_origin_evidence": "user_declaration_not_machine_proof",
            "sharing_permission": "not_granted_by_this_record",
        }))
        artifact = store.publish(manifest)
        if artifact != manifest.artifact_id:
            raise BoundaryError("local_curation", "declaration_publication_mismatch")
        with self.transaction() as db:
            current = self._declaration_scope(store, tuple(scope["dataset_ids"]))
            if current != scope:
                raise BoundaryError("local_curation", "declaration_scope_changed")
            self._check_declaration_guards(db, scope)
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_user_declarations ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT NOT NULL,"
                "dataset TEXT NOT NULL,artifact TEXT NOT NULL,recorded_at TEXT NOT NULL,"
                "UNIQUE(request_id,dataset))"
            )
            db.execute("CREATE INDEX IF NOT EXISTS local_user_declarations_latest "
                       "ON local_user_declarations(dataset,sequence)")
            existing = db.execute(
                "SELECT dataset,artifact,recorded_at FROM local_user_declarations "
                "WHERE request_id=? ORDER BY dataset", (request,),
            ).fetchall()
            if existing:
                if ([row[0] for row in existing] != scope["dataset_ids"]
                        or len({(row[1], row[2]) for row in existing}) != 1):
                    raise BoundaryError("local_curation", "declaration_request_conflict")
                artifact, recorded_at = existing[0][1], existing[0][2]
                _, current_info = self._declaration_artifact(store, artifact, scope)
                if (current_info["request_id"] != request
                        or current_info["recorded_at"] != recorded_at
                        or current_info["statement"] != {
                            "text": statement, "text_basis": statement_source["text_basis"],
                            "source": source,
                        }):
                    raise BoundaryError("local_curation", "declaration_request_conflict")
            else:
                db.executemany(
                    "INSERT INTO local_user_declarations(request_id,dataset,artifact,recorded_at) "
                    "VALUES(?,?,?,?)",
                    ((request, item, artifact, recorded_at) for item in scope["dataset_ids"]),
                )
        return {"status": "registered", "declaration_id": artifact,
                "recorded_at": recorded_at, "dataset_ids": scope["dataset_ids"],
                "scope": scope}

    def read_user_declaration(self, store: ManifestArtifactStore, dataset_id: str) -> dict:
        """Read only the exact latest owner registration; never initialize the ledger."""
        identity = digest(dataset_id, "local_curation.declaration_dataset")
        try:
            self._declaration_store(store)
            with closing(sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro", uri=True,
            )) as db:
                owner = db.execute(
                    "SELECT workspace,ledger,store,store_path FROM local_curation_identity",
                ).fetchone()
                if (owner != self.identity or db.execute(
                    "SELECT count(*) FROM local_curation_identity",
                ).fetchone()[0] != 1):
                    raise BoundaryError("local_curation", "ledger_identity_mismatch")
                self._validate_existing(db)
                row = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                                 (USER_DECLARATION_TABLE,)).fetchone()
                if row is None:
                    return {"status": "not_durably_registered", "dataset_id": identity}
                latest = db.execute(
                    "SELECT request_id,artifact,recorded_at FROM local_user_declarations "
                    "WHERE dataset=? ORDER BY sequence DESC LIMIT 1", (identity,),
                ).fetchone()
                if latest is None:
                    return {"status": "not_durably_registered", "dataset_id": identity}
                request, artifact, recorded_at = latest
                rows = db.execute(
                    "SELECT dataset,artifact,recorded_at FROM local_user_declarations "
                    "WHERE request_id=? ORDER BY dataset", (request,),
                ).fetchall()
            manifest, info = self._declaration_artifact(store, artifact)
            scope_ids = info["scope"]["dataset_ids"]
            if ([item[0] for item in rows] != scope_ids
                    or any(item[1:] != (artifact, recorded_at) for item in rows)
                    or identity not in scope_ids or info["request_id"] != request
                    or info["recorded_at"] != recorded_at):
                raise BoundaryError("local_curation", "declaration_binding_mismatch")
            current = self._declaration_scope(store, tuple(scope_ids))
            if current != info["scope"]:
                raise BoundaryError("local_curation", "declaration_scope_mismatch")
            return {"status": "registered", "dataset_id": identity,
                    "declaration_id": manifest.artifact_id, "recorded_at": recorded_at,
                    "dataset_ids": scope_ids,
                    "self_recorded": info["self_recorded"],
                    "project_training_authorized_now": info["project_training_authorized_now"],
                    "historical_authorization": info["historical_authorization"],
                    "historical_manual_exposure": info["historical_manual_exposure"]}
        except (OSError, sqlite3.DatabaseError, BoundaryError, ValueError, TypeError) as error:
            code = (error.code if isinstance(error, BoundaryError)
                    else "declaration_storage_unavailable")
            return {"status": "unavailable", "dataset_id": identity, "reason": code}

    def _verify_declaration_membership(
        self, store: ManifestArtifactStore, dataset_ids: tuple[str, ...],
        declared_ids: tuple[str, ...],
    ) -> None:
        from stpd.fullrun.curated_dataset import SCHEMA as CURATED_SCHEMA
        from stpd.fullrun.curated_dataset import load_selection
        from stpd.fullrun.decision_dataset import DecisionDataset, _fact, _identity
        from stpd.fullrun.decision_spool import SpoolSelection
        from stpd.fullrun.decision_store import load as load_decision_dataset

        def selected(manifest: Manifest) -> DecisionDataset:
            if manifest.parameters.value().get("schema") == CURATED_SCHEMA:
                return load_selection(store, manifest, cache=None)
            return load_decision_dataset(store, manifest.artifact_id, cache=None)[1]

        frozen: dict[str, str] = {}
        opened: list[DecisionDataset] = []
        try:
            for identity in declared_ids:
                source = selected(store.get_manifest(identity))
                opened.append(source)
                for record in source.records:
                    key = _identity(record)
                    fact = _fact(record)
                    if key in frozen and frozen[key] != fact:
                        raise BoundaryError("local_curation", "declaration_membership_conflict")
                    frozen[key] = fact
            if not frozen:
                raise BoundaryError("local_curation", "declaration_membership_empty")
            for identity in dataset_ids:
                candidate = selected(store.get_manifest(identity))
                opened.append(candidate)
                for record in candidate.records:
                    key = _identity(record)
                    if frozen.get(key) != _fact(record):
                        raise BoundaryError("local_curation", "declaration_membership_mismatch")
        finally:
            for dataset in opened:
                if isinstance(dataset.records, SpoolSelection):
                    dataset.records.owner.close()

    def require_user_training_declaration(
        self, store: ManifestArtifactStore, dataset_ids: tuple[str, ...],
        declaration_id: str, *, training_operation_id: str,
    ) -> dict:
        """Require an exact registered declaration and typed covered subset."""
        self._declaration_store(store)
        if not isinstance(dataset_ids, tuple) or not 1 <= len(dataset_ids) <= 100:
            raise BoundaryError("local_curation", "training_dataset_selection_invalid")
        identities = tuple(digest(item, "local_curation.training_dataset")
                           for item in dataset_ids)
        if len(set(identities)) != len(identities):
            raise BoundaryError("local_curation", "training_dataset_selection_invalid")
        declaration = digest(declaration_id, "local_curation.declaration_id")
        operation = digest(training_operation_id, "local_curation.training_operation", length=32)
        manifest, info = self._declaration_artifact(store, declaration)
        scope = self._declaration_scope(store, tuple(info["scope"]["dataset_ids"]))
        if scope != info["scope"]:
            raise BoundaryError("local_curation", "declaration_scope_mismatch")
        if (info["self_recorded"] is not True
                or info["project_training_authorized_now"] is not True):
            raise BoundaryError("local_curation", "declaration_not_authorized_now")
        for declared_id in scope["dataset_ids"]:
            current = self.read_user_declaration(store, declared_id)
            if (current.get("status") != "registered"
                    or current.get("declaration_id") != declaration):
                raise BoundaryError("local_curation", "declaration_not_latest_registration")
        declared_manifests = {
            identity: store.get_manifest(identity) for identity in scope["dataset_ids"]
        }
        for identity in identities:
            self._declaration_preproof(store, identity, scope, declared_manifests)
        with self.transaction() as db:
            self._check_declaration_guards(db, scope)
        # This approved helper performs typed source reprojection, exact claims/index
        # validation and existing no-write exposure checks after metadata coverage passes.
        admitted = self.require_training_datasets(store, identities, operation)
        self._verify_declaration_membership(store, identities, tuple(scope["dataset_ids"]))
        for declared_id in scope["dataset_ids"]:
            current = self.read_user_declaration(store, declared_id)
            if (current.get("status") != "registered"
                    or current.get("declaration_id") != declaration):
                raise BoundaryError("local_curation", "declaration_registration_changed")
        return {"declaration_id": manifest.artifact_id,
                "training_operation_id": operation, "dataset_ids": list(identities),
                "datasets": admitted["datasets"],
                "historical_external_exposure": "unknown"}

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
