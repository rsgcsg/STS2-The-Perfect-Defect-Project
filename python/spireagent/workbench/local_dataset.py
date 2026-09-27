"""Explicit local verified-source preview and purpose-bound Dataset publication."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections import Counter
from contextlib import closing, suppress
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, digest
from spireagent.local_verified_bundle import EVIDENCE_SCHEMA
from spireagent.source import source_identity
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ROOT, ProjectConfig, atomic_json
from spireagent.workbench.inplace_curation import configured_owner
from spireagent.workbench.local_curation import LocalCurationOwner
from spireagent.workbench.managed_local_workspace import ROOT_NAME, inspect_managed_workspace
from stpd.fullrun.contracts import SourceProjection
from stpd.fullrun.curated_dataset import SCHEMA as CURATED_SCHEMA
from stpd.fullrun.curated_dataset import curate, load_selection, publish_selection
from stpd.fullrun.decision_dataset import DecisionDataset, SelectionRules
from stpd.fullrun.decision_spool import SpoolSelection
from stpd.fullrun.decision_store import preview

SCHEMA = "stpd/local-dataset-operation-v1"
OPERATION_FILE = "local-dataset-operation.json"
PURPOSES = frozenset({"training", "test", "gold"})


def _close(*datasets: DecisionDataset | None) -> None:
    seen: set[int] = set()
    failure: Exception | None = None
    for dataset in datasets:
        if dataset is not None and isinstance(dataset.records, SpoolSelection):
            owner = dataset.records.owner
            if id(owner) not in seen:
                seen.add(id(owner))
                try:
                    owner.close()
                except Exception as error:
                    failure = error
    if failure is not None:
        raise failure


class LocalDatasetService:
    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.path = config.state_dir / OPERATION_FILE
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.operation: dict[str, Any] = {"status": "idle"}
        self.operation_invalid = False
        if self.path.exists():
            if self.path.is_symlink():
                self.operation_invalid = True
                self.operation = {"status": "failed", "error_code": "operation_file_invalid"}
            else:
                try:
                    value = json.loads(self.path.read_bytes())
                    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
                        raise ValueError
                    self._validate_operation(value)
                    self.operation = value
                    if value.get("status") == "pending":
                        self.operation = {**value, "status": "interrupted",
                                          "error_code": "previous_operation_interrupted"}
                except (OSError, ValueError, BoundaryError, KeyError, TypeError):
                    self.operation_invalid = True
                    self.operation = {"status": "failed", "error_code": "operation_file_invalid"}

    @staticmethod
    def _validate_operation(value: dict[str, Any]) -> None:
        status = value.get("status")
        if status not in {"idle", "pending", "preview_ready", "completed", "failed",
                          "interrupted"}:
            raise ValueError
        if status == "idle":
            return
        digest(value["id"], "local_dataset.id", length=32)
        digest(value["artifact_id"], "local_dataset.artifact_id")
        if value["purpose"] not in PURPOSES or value["paired_training"] is not None \
                and value["purpose"] == "training":
            raise ValueError
        if value["paired_training"] is not None:
            digest(value["paired_training"], "local_dataset.paired_training")
        if not isinstance(value["_owner"], list) or len(value["_owner"]) != 4:
            raise ValueError
        SelectionRules.decode(value["_rules"])
        if value["_phase"] not in {"preview", "publish"}:
            raise ValueError
        if value["_phase"] == "publish" or status in {"preview_ready", "completed"}:
            digest(value["preview_id"], "local_dataset.preview_id", length=32)
            digest(value["_logical_id"], "local_dataset.logical_id")
            if type(value["_annotation_revision"]) is not int:
                raise ValueError
        if "_producer" in value:
            Producer.decode(value["_producer"])
        if status == "completed":
            digest(value["result_artifact_id"], "local_dataset.result")

    def _selected(self) -> tuple[LocalCurationOwner, ManifestArtifactStore, Path]:
        if self.config.research_workspace is not None:
            owner = configured_owner(self.config)
            configured = self.config.research_workspace
            store = ManifestArtifactStore(LocalBlobStore(configured.store_dir, create=False))
            return owner, store, configured.registry_path
        selected = inspect_managed_workspace(self.config.state_dir)
        if selected["status"] != "ready":
            raise BoundaryError("local_dataset", "workspace_required")
        owner = selected["curation_owner"]
        if owner is None:
            raise BoundaryError("local_dataset", "curation_owner_recovery_required")
        directory = self.config.state_dir.resolve() / ROOT_NAME / selected["workspace_id"]
        store = ManifestArtifactStore(LocalBlobStore(directory / "store", create=False))
        return owner, store, directory / "registry.sqlite"

    def _availability(self) -> tuple[str, str | None]:
        if self.operation_invalid:
            return "recovery_required", "operation_file_invalid"
        try:
            self._selected()
        except BoundaryError as error:
            if error.code == "workspace_required":
                return "workspace_required", error.code
            if error.code == "curation_preparation_required":
                return "preparation_required", error.code
            return "recovery_required", error.code
        return "ready", None

    def _paired_catalog(self, store: ManifestArtifactStore,
                        owner: LocalCurationOwner) -> list[dict[str, Any]]:
        result = []
        try:
            with closing(sqlite3.connect(
                owner.path.resolve().as_uri() + "?mode=ro", uri=True,
            )) as db:
                bound = [row[0] for row in db.execute(
                    "SELECT artifact FROM curation_claims WHERE purpose='training' "
                    "AND artifact IS NOT NULL ORDER BY artifact LIMIT 100")]
        except sqlite3.DatabaseError as error:
            raise BoundaryError("local_dataset", "curation_owner_recovery_required") from error
        for identity in bound:
            item = store.get_manifest(identity)
            info = item.parameters.value()
            if item.kind == "dataset" \
                    and info.get("schema") == CURATED_SCHEMA \
                    and info.get("purpose") == "training":
                result.append({"artifact_id": identity, "records": info.get("records", 0)})
        return result

    def status(self) -> dict[str, Any]:
        availability, reason = self._availability()
        paired: list[dict[str, Any]] = []
        owner: LocalCurationOwner | None = None
        if availability == "ready":
            owner, store, _ = self._selected()
            paired = self._paired_catalog(store, owner)
        with self.lock:
            operation = {key: value for key, value in self.operation.items()
                         if not key.startswith("_") and key != "schema"}
            possible_recovery = bool(
                availability == "ready"
                and self.operation.get("status") in {"failed", "interrupted"}
                and self.operation.get("_phase") == "publish"
                and self.operation.get("preview_id")
                and self.operation.get("_producer")
                and self.operation.get("error_code") != "publication_recovery_required"
            )
        recovery = False
        if possible_recovery and owner is not None:
            try:
                with closing(sqlite3.connect(
                    owner.path.resolve().as_uri() + "?mode=ro", uri=True,
                )) as db:
                    recovery = db.execute("SELECT 1 FROM curation_claims WHERE id=?",
                                          (operation["id"],)).fetchone() is not None
            except sqlite3.DatabaseError as error:
                raise BoundaryError("local_dataset", "curation_owner_recovery_required") from error
        operation["recovery_available"] = recovery
        result = {"schema": SCHEMA, "availability": availability,
                  "paired_training": paired, "operation": operation}
        if reason is not None:
            result["reason"] = reason
        return result

    def _save(self) -> None:
        atomic_json(self.path, {"schema": SCHEMA, **self.operation})

    def _finish(self, identity: str, update: dict[str, Any]) -> None:
        with self.lock:
            if self.operation.get("id") != identity or self.operation.get("status") != "pending":
                return
            try:
                durable = json.loads(self.path.read_bytes())
            except (OSError, ValueError):
                self.operation.update(status="interrupted",
                                      error_code="operation_state_unavailable")
                return
            if durable.get("id") != identity or durable.get("status") != "pending":
                self.operation.update(status="interrupted", error_code="operation_superseded")
                return
            following = {**self.operation, **update}
            try:
                atomic_json(self.path, {"schema": SCHEMA, **following})
            except OSError:
                self.operation.update(status="interrupted",
                                      error_code="operation_state_unavailable")
                return
            self.operation = following

    def _record_producer(self, identity: str, producer: Producer) -> None:
        with self.lock:
            durable = json.loads(self.path.read_bytes())
            if (self.operation.get("id") != identity or durable.get("id") != identity
                    or durable.get("status") != "pending"):
                raise BoundaryError("local_dataset", "operation_superseded")
            following = {**self.operation, "_producer": producer.to_dict()}
            atomic_json(self.path, {"schema": SCHEMA, **following})
            self.operation = following

    def _same(self, artifact: str, purpose: str, paired: str | None) -> bool:
        return (self.operation.get("artifact_id") == artifact
                and self.operation.get("purpose") == purpose
                and self.operation.get("paired_training") == paired)

    def start_preview(self, artifact_id: object, purpose: object,
                      paired_training: object) -> dict[str, Any]:
        source = digest(artifact_id, "local_dataset.artifact_id")
        if not isinstance(purpose, str) or purpose not in PURPOSES:
            raise BoundaryError("local_dataset", "invalid_dataset_purpose")
        paired = (None if paired_training is None else
                  digest(paired_training, "local_dataset.paired_training"))
        if purpose == "training" and paired is not None:
            raise BoundaryError("local_dataset", "training_cannot_pair_itself")
        if self.operation_invalid:
            raise BoundaryError("local_dataset", "operation_file_invalid")
        owner, _, _ = self._selected()
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                if self._same(source, purpose, paired):
                    return self.status()
                raise BoundaryError("local_dataset", "operation_in_progress")
            if (self.operation.get("_phase") == "publish"
                    and self.operation.get("status") in {"failed", "interrupted"}
                    and self.operation.get("_producer")):
                with owner.transaction() as db:
                    held = db.execute("SELECT 1 FROM curation_claims WHERE id=?",
                                      (self.operation["id"],)).fetchone()
                if held:
                    raise BoundaryError("local_dataset", "publication_recovery_required")
            identity = uuid.uuid4().hex
            rules = SelectionRules()
            self.operation = {"status": "pending", "id": identity, "_phase": "preview",
                              "artifact_id": source, "purpose": purpose,
                              "paired_training": paired, "_owner": owner.identity,
                              "_rules": rules.to_dict()}
            self._save()
            self.thread = threading.Thread(target=self._run_preview, args=(identity,), daemon=True)
            self.thread.start()
            return self.status()

    def _select(self, owner: LocalCurationOwner, store: ManifestArtifactStore,
                source_id: str, purpose: str, paired: str | None,
                rules: SelectionRules, *,
                index_source: bool) -> tuple[DecisionDataset, DecisionDataset, int]:
        source = store.get_manifest(source_id)
        if source.kind != "evidence" or source.parameters.value().get("schema") != EVIDENCE_SCHEMA:
            raise BoundaryError("local_dataset", "local_verified_source_required")
        base: DecisionDataset | None = None
        selected: DecisionDataset | None = None
        training: DecisionDataset | None = None
        try:
            def projected(value: SourceProjection) -> None:
                if index_source:
                    owner.ledger.index_source(source_id, value)

            base = preview(store, (source,), rules, on_projection=projected)
            if index_source:
                candidate = source.parameters.value().get("candidate_id")
                if isinstance(candidate, str):
                    with owner.transaction() as db:
                        pending = db.execute("SELECT 1 FROM local_source_pending WHERE "
                                             "candidate=?", (candidate,)).fetchone()
                    if pending:
                        owner.complete_index(candidate, source_id)
            annotations = owner.ledger.annotations(
                base.records, runs=(r["run_id"] for r in base.report.value()["runs"])
            )
            selected = curate(base, purpose, annotations)
            if paired is not None:
                parent = store.get_manifest(paired)
                bound = owner.ledger.dataset(paired)
                info = parent.parameters.value()
                if (parent.kind != "dataset" or info.get("schema") != CURATED_SCHEMA
                        or info.get("purpose") != "training"
                        or bound is None or bound[0] != "training"):
                    raise BoundaryError("local_dataset", "paired_training_required")
                training = load_selection(store, parent, cache=None)
                facts = set(training.fingerprints())
                if any(fingerprint in facts for fingerprint in selected.fingerprints()) \
                        or owner.ledger.overlap(selected.run_ids, training.run_ids)["overlap"]:
                    raise BoundaryError("local_dataset", "training_test_overlap")
            _close(training)
            return base, selected, annotations["revision"]
        except BaseException:
            with suppress(Exception):
                _close(base, selected, training)
            raise

    def _result(self, selected: DecisionDataset, *, owner: LocalCurationOwner,
                purpose: str) -> dict[str, Any]:
        report = selected.report.value()
        conflict: str | None = None
        with owner.transaction() as db:
            related = owner.ledger._groups(db, selected.run_ids)
            claims = owner.ledger._claims(db, related)
            if purpose == "gold":
                if owner.gold_history_unknown(db, related):
                    conflict = "legacy_gold_history_unknown"
                elif owner._inventory_pending(db):
                    conflict = "gold_source_inventory_pending"
                elif any(kind != "gold" for kind, _ in claims.values()):
                    conflict = "gold_already_in_other_dataset"
                elif claims:
                    conflict = "gold_requires_gold_merge"
                elif any(db.execute(
                    "SELECT 1 FROM curation_uses WHERE run=? AND kind='training' UNION "
                    "SELECT 1 FROM curation_source_uses u JOIN curation_source_runs r "
                    "ON r.source=u.source WHERE r.run=? AND u.kind='training'",
                    (run, run),
                ).fetchone() for run in related):
                    conflict = "gold_previously_used_for_training"
            elif any(kind == "gold" for kind, _ in claims.values()):
                conflict = "gold_reserved_data"
        count = len(selected.records)
        return {"selected": count, "split_status": report["split_status"],
                "exclusions": dict(Counter(item["reason"] for item in report["excluded"])),
                "can_publish": count > 0 and conflict is None,
                **({"error_code": conflict} if conflict else
                   {"error_code": "empty_selection"} if not count else {})}

    def _run_preview(self, identity: str) -> None:
        base = selected = None
        try:
            with self.lock:
                request = dict(self.operation)
            owner, store, _ = self._selected()
            if tuple(request["_owner"]) != owner.identity:
                raise BoundaryError("local_dataset", "workspace_owner_changed")
            base, selected, revision = self._select(
                owner, store, request["artifact_id"], request["purpose"],
                request["paired_training"], SelectionRules.decode(request["_rules"]),
                index_source=True,
            )
            result = self._result(selected, owner=owner, purpose=request["purpose"])
            update = {**result, "status": "preview_ready", "preview_id": uuid.uuid4().hex,
                      "_logical_id": selected.logical_id, "_annotation_revision": revision}
        except Exception as error:
            code = error.code if isinstance(error, BoundaryError) else "preview_failed"
            update = {"status": "failed", "error_code": code}
        finally:
            try:
                _close(base, selected)
            except Exception:
                if update["status"] != "failed":
                    update = {"status": "failed", "error_code": "preview_cleanup_failed"}
        self._finish(identity, update)

    def start_publish(self, preview_id: object) -> dict[str, Any]:
        token = digest(preview_id, "local_dataset.preview_id", length=32)
        if self.operation_invalid:
            raise BoundaryError("local_dataset", "operation_file_invalid")
        self._selected()
        with self.lock:
            if self.operation.get("preview_id") != token:
                raise BoundaryError("local_dataset", "preview_changed")
            if self.thread is not None and self.thread.is_alive():
                if self.operation.get("_phase") == "publish":
                    return self.status()
                raise BoundaryError("local_dataset", "operation_in_progress")
            if self.operation.get("status") == "completed":
                return self.status()
            state = self.operation.get("status")
            if state not in {"preview_ready", "interrupted", "failed"}:
                raise BoundaryError("local_dataset", "preview_not_ready")
            if state != "preview_ready":
                if self.operation.get("error_code") == "publication_recovery_required":
                    raise BoundaryError("local_dataset", "publication_recovery_required")
                if self.operation.get("_phase") != "publish":
                    raise BoundaryError("local_dataset", "publication_recovery_required")
                owner, _, _ = self._selected()
                with owner.transaction() as db:
                    row = db.execute("SELECT artifact FROM curation_claims WHERE id=?",
                                     (self.operation["id"],)).fetchone()
                if row is None or (row[0] is None and "_producer" not in self.operation):
                    raise BoundaryError("local_dataset", "publication_recovery_required")
            elif not self.operation.get("can_publish"):
                raise BoundaryError("local_dataset", "preview_cannot_publish")
            self.operation.update(status="pending", _phase="publish", can_publish=False)
            self._save()
            identity = self.operation["id"]
            self.thread = threading.Thread(target=self._run_publish, args=(identity,), daemon=True)
            self.thread.start()
            return self.status()

    def _recover_published(self, owner: LocalCurationOwner, store: ManifestArtifactStore,
                           request: dict[str, Any], identity: str) -> str:
        try:
            producer = Producer.decode(request["_producer"])
        except (KeyError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_dataset", "publication_recovery_required") from error
        with owner.transaction() as db:
            held = {row[0] for row in db.execute(
                "SELECT run FROM curation_claim_runs WHERE claim=?", (identity,))}
        matches = []
        for candidate in store.manifest_ids():
            manifest = store.get_manifest(candidate)
            info = manifest.parameters.value()
            if (manifest.kind != "dataset" or manifest.producer != producer
                    or info.get("schema") != CURATED_SCHEMA
                    or info.get("logical_id") != request["_logical_id"]
                    or info.get("purpose") != request["purpose"]
                    or info.get("rules") != request["_rules"]
                    or info.get("paired_training") != request["paired_training"]
                    or (request["purpose"] == "gold"
                        and info.get("reservation") != identity)
                    or len(manifest.parents) != 1
                    or manifest.parents[0].role != "source_" + request["artifact_id"]
                    or manifest.parents[0].artifact_id != request["artifact_id"]):
                continue
            try:
                loaded = load_selection(store, manifest, cache=None)
                try:
                    if loaded.logical_id == request["_logical_id"] and loaded.run_ids == held:
                        matches.append(candidate)
                finally:
                    _close(loaded)
            except (BoundaryError, OSError, ValueError):
                continue
        if len(matches) != 1:
            raise BoundaryError("local_dataset", "publication_recovery_required")
        owner.ledger.bind(identity, matches[0])
        return matches[0]

    def _run_publish(self, identity: str) -> None:
        base = selected = None
        try:
            with self.lock:
                request = dict(self.operation)
            owner, store, registry_path = self._selected()
            if tuple(request["_owner"]) != owner.identity:
                raise BoundaryError("local_dataset", "workspace_owner_changed")
            # An interrupted claim may already be bound; never guess from a cache.
            with owner.transaction() as db:
                row = db.execute("SELECT artifact FROM curation_claims WHERE id=?",
                                 (identity,)).fetchone()
            if row is not None:
                result_id = (row[0] if row[0] is not None else
                             self._recover_published(owner, store, request, identity))
                manifest = store.get_manifest(result_id)
                if manifest.parameters.value().get("reservation") != identity \
                        and request["purpose"] == "gold":
                    raise BoundaryError("local_dataset", "publication_recovery_required")
                recovered = load_selection(store, manifest, cache=None)
                try:
                    if recovered.logical_id != request["_logical_id"] \
                            or owner.ledger.dataset(result_id) != (
                                request["purpose"], recovered.run_ids,
                            ):
                        raise BoundaryError("local_dataset", "publication_recovery_required")
                finally:
                    _close(recovered)
            else:
                base, selected, revision = self._select(
                    owner, store, request["artifact_id"], request["purpose"],
                    request["paired_training"], SelectionRules.decode(request["_rules"]),
                    index_source=False,
                )
                if selected.logical_id != request["_logical_id"] \
                        or revision != request["_annotation_revision"]:
                    raise BoundaryError("local_dataset", "preview_changed")
                result = self._result(selected, owner=owner, purpose=request["purpose"])
                if not result["can_publish"]:
                    raise BoundaryError(
                        "local_dataset", result.get("error_code", "preview_changed")
                    )
                producer = source_identity(ROOT)
                self._record_producer(identity, producer)
                owner.ledger.claim(identity, request["purpose"], selected.run_ids,
                                   annotation_revision=revision)
                manifest = publish_selection(
                    store, (store.get_manifest(request["artifact_id"]),),
                    SelectionRules.decode(request["_rules"]),
                    producer, selected, merging=False,
                    expected=request["_logical_id"],
                    paired_training=request["paired_training"], reservation=identity,
                )
                result_id = manifest.artifact_id
                owner.ledger.bind(identity, result_id)
            registry = SQLiteRegistry(registry_path)
            cached = frozenset(item.artifact_id for item in registry.manifests()
                               if registry.is_cached(item.artifact_id))
            sync_registry(store, registry, cached)
            update = {"status": "completed", "result_artifact_id": result_id,
                      "can_publish": False, "error_code": None}
        except Exception as error:
            code = error.code if isinstance(error, BoundaryError) else "publish_failed"
            update = {"status": "failed", "error_code": code, "can_publish": False}
        finally:
            try:
                _close(base, selected)
            except Exception:
                if update["status"] != "failed":
                    update = {"status": "failed", "error_code": "publish_cleanup_failed",
                              "can_publish": False}
        self._finish(identity, update)
