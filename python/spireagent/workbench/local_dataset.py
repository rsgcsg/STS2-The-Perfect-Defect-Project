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
from spireagent.json_boundary import BoundaryError, decode_json, digest
from spireagent.local_verified_bundle import EVIDENCE_SCHEMA
from spireagent.source import source_identity
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ROOT, ProjectConfig, atomic_json
from spireagent.workbench.inplace_curation import configured_owner
from spireagent.workbench.local_curation import LocalCurationOwner
from spireagent.workbench.managed_local_workspace import ROOT_NAME, inspect_managed_workspace
from stpd.canonical import semantic_hash
from stpd.fullrun.contracts import SourceProjection
from stpd.fullrun.curated_dataset import SCHEMA as CURATED_SCHEMA
from stpd.fullrun.curated_dataset import curate, load_selection, publish_selection
from stpd.fullrun.decision_dataset import DecisionDataset, SelectionRules
from stpd.fullrun.decision_spool import SpoolSelection
from stpd.fullrun.decision_store import preview
from stpd.fullrun.text_menu_human_import import (
    SOURCE_SCHEMA as HUMAN_SOURCE_SCHEMA,
)
from stpd.fullrun.text_menu_human_import import (
    load_human_text_source,
    load_verified_human_text_bundle,
    publish_human_text_source,
)

SCHEMA = "stpd/local-dataset-operation-v1"
OPERATION_FILE = "local-dataset-operation.json"
PURPOSES = frozenset({"training", "test", "gold"})


def _ordered_recipe(view: str) -> str:
    from stpd.ordered_source_spec import RECIPES

    for recipe, (slots, reset, selected_view) in RECIPES.items():
        if (slots, reset, selected_view) == (1, "carry", view):
            return recipe
    raise BoundaryError("local_dataset", "source3_training_recipe_unavailable")


def source3_capabilities() -> dict[str, Any]:
    """Closed choices are projected from the research owner, without tensor imports."""
    from stpd.ordered_source_spec import (
        COHORTS,
        DEFAULT_VIEW,
        PRETRAIN_VIEW,
        SAMPLED_VIEW,
        VIEW_SPECS,
        view_qualification,
    )

    # Presentation of owner-defined views; historical function/recipe defaults
    # retain their meaning. The product explicitly selects its new default.
    presentation = {
        DEFAULT_VIEW: ("公开发布观察历史", "original_admitted_attachment_epoch_prefix"),
        PRETRAIN_VIEW: ("录制公开观察预训练", "original_admitted_attachment_epoch_prefix"),
        SAMPLED_VIEW: ("决策取样与连续记忆", "declared_original_input_basis_sampled_segments"),
    }
    return {
        "source_profile": "native-logical-source-v3",
        "cohorts": sorted(COHORTS), "default_cohort": "declared_human",
        "source_labels": {
            "declared_human": "人操作·原生键鼠/UI（本人声明）",
            "agent_native_ui": "机器操作·原生键鼠/UI",
            "agent_protocol": "机器操作·Agent 程序协议",
            "unknown": "未知来源",
        },
        "default_view": SAMPLED_VIEW,
        "views": [{"view": view, "qualification": view_qualification(view),
                   "recommended_recipe_id": _ordered_recipe(view),
                   "label": presentation.get(view, ("显式研究数据视图", ""))[0],
                   "history_scope": presentation.get(view, ("", "declared_by_projection_spec"))[1]}
                  for view in VIEW_SPECS],
        "human_origin_verified": False, "automatic_training": False,
    }


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
        kind = value.get("kind", "canonical")
        if kind not in {"canonical", "human_input", "ordered_source3"}:
            raise ValueError
        if kind == "ordered_source3":
            from stpd.ordered_source_spec import COHORTS, view_specs

            ids = value.get("artifact_ids")
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 256
                    or ids != sorted(set(ids)) or ids[0] != value["artifact_id"]
                    or value.get("cohort") not in COHORTS
                    or value["purpose"] != "training" or value["paired_training"] is not None
                    or not isinstance(value.get("_owner"), list) or len(value["_owner"]) != 4
                    or value["_phase"] not in {"preview", "publish"}):
                raise ValueError
            view_specs(value["view"])
            for source in ids:
                digest(source, "local_dataset.source3_raw")
            if "_producer" in value:
                Producer.decode(value["_producer"])
            if value["_phase"] == "publish" or status in {"preview_ready", "completed"}:
                digest(value["preview_id"], "local_dataset.preview_id", length=32)
                digest(value["_logical_id"], "local_dataset.logical_id")
                if not isinstance(value.get("_admission_refs"), list):
                    raise ValueError
                refs = value["_admission_refs"]
                if [ref["raw_id"] for ref in refs] != ids:
                    raise ValueError
                for ref in refs:
                    if set(ref) != {"raw_id", "admission_id"}:
                        raise ValueError
                    digest(ref["admission_id"], "local_dataset.source3_admission")
                Producer.decode(value["_producer"])
            if "_partition_id" in value:
                digest(value["_partition_id"], "local_dataset.source3_partition")
            if status == "completed":
                digest(value["result_artifact_id"], "local_dataset.result")
            return
        if kind == "human_input":
            ids = value.get("artifact_ids")
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 256
                    or len(set(ids)) != len(ids) or ids[0] != value["artifact_id"]):
                raise ValueError
            for identity in ids:
                digest(identity, "local_dataset.human_source")
            if value["purpose"] != "training" or value["paired_training"] is not None:
                raise ValueError
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
        reservation = operation.get("use_reservation")
        if (operation.get("kind") == "ordered_source3"
                and operation.get("status") == "completed"
                and isinstance(reservation, dict)
                and reservation.get("split") == "train"
                and reservation.get("artifact_id") == operation.get("training_source_id")
                == operation.get("result_artifact_id")
                and operation.get("result_artifact_id") is not None):
            # Publication retained its successful reservation. Preview status is
            # older; projecting it here also fixes reopened historical operations.
            operation["split_status"] = "reserved"
        recovery = False
        if possible_recovery and operation.get("kind") == "ordered_source3":
            # Exact immutable refs/producer permit explicit publication reconciliation.
            recovery = True
            possible_recovery = False
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
                  "source3_support": source3_capabilities(),
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
                and self.operation.get("paired_training") == paired
                and self.operation.get("kind", "canonical") == "canonical")

    def binding(self, artifact_id: object) -> dict[str, Any]:
        """An exact-ID, metadata/ledger-only purpose lookup; no readiness guess."""
        identity = digest(artifact_id, "local_dataset.artifact_id")
        owner, store, _ = self._selected()
        item = store.get_manifest(identity)
        if item.kind != "dataset" or item.parameters.value().get("schema") != HUMAN_SOURCE_SCHEMA:
            raise BoundaryError("local_dataset", "human_input_source_required")
        with closing(sqlite3.connect(owner.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT purpose FROM curation_claims WHERE artifact=?",
                             (identity,)).fetchone()
        return {"schema": "stpd/local-dataset-binding-v1", "artifact_id": identity,
                "sample_type": "human_input", "curation_purpose": row[0] if row else None}

    def start_human_preview(self, artifact_ids: object) -> dict[str, Any]:
        if (not isinstance(artifact_ids, list) or not 1 <= len(artifact_ids) <= 256
                or len(set(artifact_ids)) != len(artifact_ids)):
            raise BoundaryError("local_dataset", "human_source_selection_invalid")
        ids = [digest(value, "local_dataset.human_source") for value in artifact_ids]
        if self.operation_invalid:
            raise BoundaryError("local_dataset", "operation_file_invalid")
        owner, _, _ = self._selected()
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                if (self.operation.get("kind") == "human_input"
                        and self.operation.get("artifact_ids") == ids):
                    return self.status()
                raise BoundaryError("local_dataset", "operation_in_progress")
            if (self.operation.get("_phase") == "publish"
                    and self.operation.get("status") in {"failed", "interrupted"}
                    and self.operation.get("_producer")):
                if self.operation.get("kind") == "ordered_source3":
                    raise BoundaryError("local_dataset", "publication_recovery_required")
                with owner.transaction() as db:
                    held = db.execute("SELECT 1 FROM curation_claims WHERE id=?",
                                      (self.operation["id"],)).fetchone()
                if held:
                    raise BoundaryError("local_dataset", "publication_recovery_required")
            identity = uuid.uuid4().hex
            self.operation = {"status": "pending", "id": identity, "_phase": "preview",
                              "kind": "human_input", "artifact_id": ids[0],
                              "artifact_ids": ids, "purpose": "training",
                              "paired_training": None, "_owner": owner.identity,
                              "_rules": SelectionRules().to_dict()}
            self._save()
            self.thread = threading.Thread(target=self._run_preview, args=(identity,), daemon=True)
            self.thread.start()
            return self.status()

    def start_source3_preview(self, artifact_ids: object, cohort: object,
                              view: object) -> dict[str, Any]:
        from stpd.ordered_source_spec import COHORTS, view_specs

        if (not isinstance(artifact_ids, list) or not 1 <= len(artifact_ids) <= 256
                or any(not isinstance(value, str) for value in artifact_ids)
                or len(set(artifact_ids)) != len(artifact_ids)):
            raise BoundaryError("local_dataset", "source3_source_selection_invalid")
        ids = sorted(digest(value, "local_dataset.source3_raw") for value in artifact_ids)
        if not isinstance(cohort, str) or cohort not in COHORTS:
            raise BoundaryError("local_dataset", "source3_cohort_not_supported")
        if not isinstance(view, str):
            raise BoundaryError("local_dataset", "source3_view_not_supported")
        view_specs(view)
        _ordered_recipe(view)
        if self.operation_invalid:
            raise BoundaryError("local_dataset", "operation_file_invalid")
        owner, _, _ = self._selected()
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                if (self.operation.get("kind") == "ordered_source3"
                        and self.operation.get("artifact_ids") == ids
                        and self.operation.get("cohort") == cohort
                        and self.operation.get("view") == view):
                    return self.status()
                raise BoundaryError("local_dataset", "operation_in_progress")
            if (self.operation.get("_phase") == "publish"
                    and self.operation.get("status") in {"failed", "interrupted"}):
                if self.operation.get("kind") == "ordered_source3":
                    raise BoundaryError("local_dataset", "publication_recovery_required")
                with owner.transaction() as db:
                    if db.execute("SELECT 1 FROM curation_claims WHERE id=?",
                                  (self.operation["id"],)).fetchone():
                        raise BoundaryError("local_dataset", "publication_recovery_required")
            identity = uuid.uuid4().hex
            self.operation = {
                "status": "pending", "id": identity, "_phase": "preview",
                "kind": "ordered_source3", "artifact_id": ids[0], "artifact_ids": ids,
                "purpose": "training", "paired_training": None, "_owner": owner.identity,
                "cohort": cohort, "view": view,
            }
            self._save()
            self.thread = threading.Thread(target=self._run_preview, args=(identity,), daemon=True)
            self.thread.start()
            return self.status()

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
                if self.operation.get("kind") == "ordered_source3":
                    raise BoundaryError("local_dataset", "publication_recovery_required")
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
                owner.index_human_runs(store, source_id, historical=True)
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

    def _human_source(self, owner: LocalCurationOwner, store: ManifestArtifactStore,
                      ids: list[str], *, index_source: bool) -> tuple[set[str], str, int]:
        rows: list[dict[str, Any]] = []
        runs: set[str] = set()
        sessions: set[str] = set()
        for source_id in ids:
            source = store.get_manifest(source_id)
            if (source.kind != "evidence"
                    or source.parameters.value().get("schema") != EVIDENCE_SCHEMA):
                raise BoundaryError("local_dataset", "local_verified_source_required")
            _, bundle, batch = load_verified_human_text_bundle(store, source_id)
            if bundle.session_id in sessions:
                raise BoundaryError("local_dataset", "duplicate_session")
            sessions.add(bundle.session_id)
            rows.extend(batch)
            runs.update(bundle.session_id + "/" + run for run in bundle.run_ids)
            if len(rows) > 100000:
                raise BoundaryError("local_dataset", "human_text_row_limit")
            if index_source:
                projected: list[SourceProjection] = []
                base = None
                try:
                    base = preview(store, (source,), SelectionRules(),
                                   on_projection=projected.append)
                    if len(projected) != 1:
                        raise BoundaryError("local_dataset", "source_projection_incomplete")
                    owner.ledger.index_source(source_id, projected[0])
                    indexed = owner.index_human_runs(store, source_id, historical=True)
                    if indexed != {bundle.session_id + "/" + run for run in bundle.run_ids}:
                        raise BoundaryError("local_dataset", "human_run_identity_mismatch")
                    candidate = source.parameters.value().get("candidate_id")
                    if isinstance(candidate, str):
                        with owner.transaction() as db:
                            pending = db.execute("SELECT 1 FROM local_source_pending WHERE "
                                                 "candidate=?", (candidate,)).fetchone()
                        if pending:
                            owner.complete_index(candidate, source_id)
                finally:
                    _close(base)
        accepted = sum(row["disposition"] == "accepted_input" for row in rows)
        return runs, semantic_hash(rows), accepted

    @staticmethod
    def _human_result(owner: LocalCurationOwner, runs: set[str], accepted: int) -> dict[str, Any]:
        conflict: str | None = None
        with owner.transaction() as db:
            related = owner.ledger._groups(db, runs)
            if any(purpose == "gold" for purpose, _ in owner.ledger._claims(
                db, related).values()
            ):
                conflict = "gold_reserved_data"
        return {"selected": accepted, "accepted_labels": accepted,
                "sample_type": "human_input", "split_status": "not_checked_for_training",
                "exclusions": {}, "can_publish": accepted > 0 and conflict is None,
                **({"error_code": conflict} if conflict else
                   {"error_code": "no_accepted_input"} if not accepted else {})}

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

    @staticmethod
    def _ordered_preview(store: ManifestArtifactStore, request: dict[str, Any],
                         producer: Producer) -> tuple[list[dict[str, str]], dict[str, Any], str]:
        from stpd.fullrun.ordered_source import publish_ordered_source_admission
        from stpd.ordered_source_spec import view_qualification, view_specs

        refs: list[dict[str, str]] = []
        counts: Counter[str] = Counter()
        exclusions: Counter[str] = Counter()
        original_reports: list[str] = []
        content_ids: set[str] = set()
        for raw_id in request["artifact_ids"]:
            ref = publish_ordered_source_admission(
                store, raw_id, producer, cohort=request["cohort"], view=request["view"],
            )
            admission = store.get_manifest(ref.admission_id)
            report = decode_json(store.bytes(admission.payload("report")))
            if report["bundle_content_id"] in content_ids:
                raise BoundaryError("local_dataset", "duplicate_original_source3_bundle")
            content_ids.add(report["bundle_content_id"])
            refs.append({"raw_id": ref.raw_id, "admission_id": ref.admission_id})
            counts.update(report["counts"])
            exclusions.update(row["reason"] for row in report["exclusions"])
            original_reports.append(admission.payload("report").sha256)
        eligible = counts["eligible_unique_N"]
        denominator = counts["original_exact_delivered_cohort_choices"]
        projection, target = view_specs(request["view"])
        summary = {
            "selected": eligible, "accepted_labels": eligible, "sample_type": "ordered_source3",
            "source_kind": request["cohort"], "source_view": request["view"],
            "counts": dict(counts), "exclusions": dict(sorted(exclusions.items())),
            "N_coverage": {"cohort": request["cohort"], "eligible": eligible,
                           "denominator": denominator,
                           "fraction": eligible / denominator if denominator else None},
            "excluded_labels": denominator - eligible,
            "history_scope": next(value["history_scope"]
                                  for value in source3_capabilities()["views"]
                                  if value["view"] == request["view"]),
            "qualification": view_qualification(request["view"]),
            "projection_spec": projection, "target_spec": target,
            "human_origin_verified": False, "split_status": "not_reserved",
            "recommended_recipe_id": _ordered_recipe(request["view"]),
            "can_publish": eligible > 0,
            **({"error_code": "no_eligible_source3_N"} if eligible == 0 else {}),
        }
        logical_id = semantic_hash({"refs": refs, "report_sha256": original_reports,
                                    "summary": summary})
        return refs, summary, logical_id

    def _run_preview(self, identity: str) -> None:
        base = selected = None
        try:
            with self.lock:
                request = dict(self.operation)
            owner, store, _ = self._selected()
            if tuple(request["_owner"]) != owner.identity:
                raise BoundaryError("local_dataset", "workspace_owner_changed")
            if request.get("kind") == "ordered_source3":
                producer = source_identity(ROOT)
                self._record_producer(identity, producer)
                refs, result, logical_id = self._ordered_preview(store, request, producer)
                update = {**result, "status": "preview_ready", "preview_id": uuid.uuid4().hex,
                          "_logical_id": logical_id, "_admission_refs": refs}
            elif request.get("kind") == "human_input":
                runs, logical_id, accepted = self._human_source(
                    owner, store, request["artifact_ids"], index_source=True)
                result = self._human_result(owner, runs, accepted)
                update = {**result, "status": "preview_ready", "preview_id": uuid.uuid4().hex,
                          "_logical_id": logical_id, "_annotation_revision": 0}
            else:
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
                if self.operation.get("kind") == "ordered_source3":
                    if (not self.operation.get("_admission_refs")
                            or not self.operation.get("_producer")):
                        raise BoundaryError("local_dataset", "publication_recovery_required")
                else:
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
            if request.get("kind") == "human_input":
                if (manifest.kind != "dataset" or manifest.producer != producer
                        or info.get("schema") != HUMAN_SOURCE_SCHEMA
                        or info.get("source_digest") != request["_logical_id"]
                        or [parent.artifact_id for parent in manifest.parents]
                        != request["artifact_ids"]):
                    continue
                try:
                    _, rows = load_human_text_source(store, candidate)
                    runs = self._human_source(owner, store, request["artifact_ids"],
                                              index_source=False)[0]
                    if semantic_hash(list(rows)) == request["_logical_id"] and runs == held:
                        matches.append(candidate)
                except (BoundaryError, OSError, ValueError):
                    continue
                continue
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

    def _publish_human(self, owner: LocalCurationOwner, store: ManifestArtifactStore,
                       request: dict[str, Any], identity: str, row: tuple | None) -> str:
        if row is not None:
            result_id = row[0] if row[0] is not None else self._recover_published(
                owner, store, request, identity)
            manifest, rows = load_human_text_source(store, result_id)
            if (manifest.parameters.value()["source_digest"] != request["_logical_id"]
                    or [p.artifact_id for p in manifest.parents] != request["artifact_ids"]
                    or semantic_hash(list(rows)) != request["_logical_id"]):
                raise BoundaryError("local_dataset", "publication_recovery_required")
            runs = self._human_source(owner, store, request["artifact_ids"],
                                      index_source=False)[0]
            if owner.ledger.dataset(result_id) != ("training", runs):
                raise BoundaryError("local_dataset", "publication_recovery_required")
            return result_id
        runs, logical_id, accepted = self._human_source(
            owner, store, request["artifact_ids"], index_source=False)
        if logical_id != request["_logical_id"]:
            raise BoundaryError("local_dataset", "preview_changed")
        indexed_runs: set[str] = set()
        for source in request["artifact_ids"]:
            source_runs = owner.ledger.source_runs(source)
            if source_runs is None:
                raise BoundaryError("local_dataset", "source_index_incomplete")
            indexed_runs.update(source_runs)
        if not runs <= indexed_runs:
            raise BoundaryError("local_dataset", "source_run_identity_mismatch")
        result = self._human_result(owner, runs, accepted)
        if not result["can_publish"]:
            raise BoundaryError("local_dataset", result.get("error_code", "preview_changed"))
        producer = source_identity(ROOT)
        self._record_producer(identity, producer)
        owner.ledger.claim(identity, "training", runs)
        manifest = publish_human_text_source(store, tuple(request["artifact_ids"]), producer)
        if manifest.parameters.value()["source_digest"] != logical_id:
            raise BoundaryError("local_dataset", "preview_changed")
        owner.ledger.bind(identity, manifest.artifact_id)
        return manifest.artifact_id

    def _record_ordered_partition(self, identity: str, artifact_id: str) -> None:
        with self.lock:
            durable = json.loads(self.path.read_bytes())
            if (self.operation.get("id") != identity or durable.get("id") != identity
                    or durable.get("status") != "pending"
                    or self.operation.get("_partition_id") not in (None, artifact_id)):
                raise BoundaryError("local_dataset", "operation_superseded")
            following = {**self.operation, "_partition_id": artifact_id}
            atomic_json(self.path, {"schema": SCHEMA, **following})
            self.operation = following

    def _publish_ordered(self, owner: LocalCurationOwner, store: ManifestArtifactStore,
                         request: dict[str, Any], identity: str) -> tuple[str, dict[str, Any]]:
        from stpd.fullrun.ordered_source import (
            OrderedSourceRef,
            publish_ordered_source_partition,
            verify_ordered_source_partition,
        )
        from stpd.ordered_source_spec import PARTITION_SCHEMA

        producer = Producer.decode(request["_producer"])
        refs, result, logical_id = self._ordered_preview(store, request, producer)
        if refs != request["_admission_refs"] or logical_id != request["_logical_id"]:
            raise BoundaryError("local_dataset", "preview_changed")
        if not result["can_publish"]:
            raise BoundaryError("local_dataset", "no_eligible_source3_N")
        saved_partition = request.get("_partition_id")
        matches: list[str] = []
        for candidate in ((saved_partition,) if saved_partition else store.manifest_ids()):
            manifest = store.get_manifest(candidate)
            info = manifest.parameters.value()
            if (manifest.kind == "dataset" and manifest.producer == producer
                    and info.get("partition_schema") == PARTITION_SCHEMA
                    and info.get("split") == "train"
                    and info.get("source_kind") == request["cohort"]
                    and info.get("raw_refs") == refs
                    and info.get("projection_spec") == result["projection_spec"]
                    and info.get("target_spec") == result["target_spec"]):
                matches.append(candidate)
            elif saved_partition:
                raise BoundaryError("local_dataset", "publication_recovery_required")
        if len(matches) > 1:
            raise BoundaryError("local_dataset", "publication_recovery_required")
        if matches:
            partition = verify_ordered_source_partition(store, matches[0])
        else:
            partition = publish_ordered_source_partition(
                store, tuple(OrderedSourceRef(**ref) for ref in refs), "train", producer,
            )
        result_id = partition.manifest.artifact_id
        self._record_ordered_partition(identity, result_id)
        reservation = owner.reserve_verified_ordered_source(store, result_id)
        for raw_id in partition.source_ids:
            with owner.transaction() as db:
                pending = [row[0] for row in db.execute(
                    "SELECT candidate FROM local_source_pending "
                    "WHERE artifact=? AND status='published'",
                    (raw_id,),
                )]
            for candidate in pending:
                owner.complete_index(candidate, raw_id)
        return result_id, {
            "training_source_id": result_id, "source_view": request["view"],
            "source_kind": request["cohort"],
            "recommended_recipe_id": _ordered_recipe(request["view"]),
            "use_reservation": reservation, "actual_training_use": False,
            "next_action": "training.start", "human_origin_verified": False,
        }

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
            result_support: dict[str, Any] = {}
            if request.get("kind") == "ordered_source3":
                result_id, result_support = self._publish_ordered(owner, store, request, identity)
            elif request.get("kind") == "human_input":
                result_id = self._publish_human(owner, store, request, identity, row)
            elif row is not None:
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
                      "can_publish": False, "error_code": None, **result_support}
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
