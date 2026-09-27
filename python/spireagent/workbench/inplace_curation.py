"""Explicit, fail-closed curation preparation for one configured local store."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ProjectConfig, atomic_json
from spireagent.workbench.local_curation import (
    OWNER_NAME,
    OWNER_SCHEMA,
    LocalCurationOwner,
)
from stpd.fullrun.curated_dataset import SCHEMA as CURATED_SCHEMA
from stpd.fullrun.data import DATASET_SCHEMA, load_dataset
from stpd.fullrun.decision_dataset import SCHEMA as DECISION_SCHEMA
from stpd.fullrun.decision_dataset import SelectionRules
from stpd.fullrun.decision_spool import SpoolSelection
from stpd.fullrun.decision_store import load, preview
from stpd.fullrun.decision_union import UNION_SCHEMA

SCHEMA = "stpd/local-curation-preparation-v1"
INPLACE_OWNER_SCHEMA = "stpd/local-curation-inplace-owner-v1"
PLAN_NAME = ".curation-preparation.json"
LOCK_NAME = ".curation-preparation.lock"
LEDGER_NAME = ".curation.sqlite"
MAX_MANIFESTS = 10000


def _store_dir(config: ProjectConfig) -> Path:
    selected = config.research_workspace
    if selected is None:
        raise BoundaryError("local_curation", "configured_workspace_required")
    store_dir = selected.store_dir
    if store_dir.is_symlink() or not store_dir.is_dir() or not selected.registry_path.is_file():
        raise BoundaryError("local_curation", "configured_workspace_unavailable")
    return store_dir.resolve()


def _plan(store_dir: Path) -> dict[str, Any] | None:
    path = store_dir / PLAN_NAME
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise BoundaryError("local_curation", "preparation_recovery_required")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError) as error:
        raise BoundaryError("local_curation", "preparation_recovery_required") from error
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or value.get("status") not in {"creating", "indexing", "failed", "ready"}
            or any(not isinstance(value.get(key), str) or len(value[key]) != 32
                   for key in ("workspace_id", "store_id", "ledger_id"))
            or value.get("store_path") != str(store_dir)
            or type(value.get("schema_initialized")) is not bool
            or not isinstance(value.get("inventory_sha256"), str)):
        raise BoundaryError("local_curation", "preparation_recovery_required")
    return value


def _marker(store_dir: Path) -> dict[str, Any] | None:
    path = store_dir / OWNER_NAME
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise BoundaryError("local_curation", "store_identity_mismatch")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError) as error:
        raise BoundaryError("local_curation", "store_identity_mismatch") from error
    if not isinstance(value, dict) or set(value) != {
        "schema", "workspace_id", "store_id", "ledger_id", "ledger_path"
    } or value.get("schema") not in {OWNER_SCHEMA, INPLACE_OWNER_SCHEMA}:
        raise BoundaryError("local_curation", "store_identity_mismatch")
    return value


def _owner(store_dir: Path, marker: dict[str, Any], *, legacy: bool) -> LocalCurationOwner:
    path = Path(marker["ledger_path"])
    if legacy and path != store_dir / LEDGER_NAME:
        raise BoundaryError("local_curation", "store_identity_mismatch")
    owner = LocalCurationOwner(path, store_dir, marker["workspace_id"],
                              marker["ledger_id"], marker["store_id"],
                              legacy_guard=legacy,
                              owner_schema=INPLACE_OWNER_SCHEMA if legacy else OWNER_SCHEMA)
    if legacy:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                          "AND name='local_legacy_unknown_runs'").fetchone() is None:
                raise BoundaryError("local_curation", "ledger_recovery_required")
    return owner


def configured_owner(config: ProjectConfig) -> LocalCurationOwner:
    """Resolve the same store owner across profiles; never initialize on read/open."""
    store_dir = _store_dir(config)
    plan, marker = _plan(store_dir), _marker(store_dir)
    if marker is None:
        if plan is not None or (store_dir / LEDGER_NAME).exists():
            raise BoundaryError("local_curation", "preparation_recovery_required")
        raise BoundaryError("local_curation", "curation_preparation_required")
    if (plan is None and marker["schema"] == INPLACE_OWNER_SCHEMA
            or plan is not None and marker["schema"] != INPLACE_OWNER_SCHEMA):
        raise BoundaryError("local_curation", "preparation_recovery_required")
    if plan is not None:
        if plan["status"] != "ready":
            raise BoundaryError("local_curation", "curation_preparation_incomplete")
        if any(marker[key] != plan[key] for key in ("workspace_id", "store_id", "ledger_id")):
            raise BoundaryError("local_curation", "store_identity_mismatch")
    return _owner(store_dir, marker, legacy=plan is not None)


def preparation_status(config: ProjectConfig, *, active: bool = False) -> dict[str, Any]:
    if config.research_workspace is None:
        return {"schema": SCHEMA, "status": "not_applicable"}
    try:
        store_dir = _store_dir(config)
        plan, marker = _plan(store_dir), _marker(store_dir)
        if marker is None and plan is None and not (store_dir / LEDGER_NAME).exists():
            return {"schema": SCHEMA, "status": "preparation_required",
                    "retry_available": False}
        if plan is not None and plan["status"] != "ready":
            preparing = active and plan["status"] in {"creating", "indexing"}
            reason = plan.get("error_code", "previous_preparation_interrupted")
            fatal = {"legacy_gold_recovery_required", "preparation_inventory_changed",
                     "store_identity_mismatch", "ledger_recovery_required",
                     "preparation_recovery_required"}
            marker_matches = (marker is not None and all(
                marker[key] == plan[key] for key in
                ("workspace_id", "store_id", "ledger_id")))
            retry = (not preparing and reason not in fatal
                     and (marker_matches or marker is None and not plan["schema_initialized"])
                     and (not plan["schema_initialized"] or
                          (store_dir / LEDGER_NAME).is_file()))
            return {"schema": SCHEMA,
                    "status": "preparing" if preparing else "recovery_required",
                    "reason": reason, "retry_available": retry,
                    "phase": plan["status"], "processed": plan.get("processed", 0),
                    "total": plan.get("total", 0)}
        configured_owner(config)
        return {"schema": SCHEMA, "status": "ready",
                "retry_available": False,
                "historical_use_history": "unknown" if plan else "owned_ledger",
                "legacy_dataset_count": (plan.get("known_dataset_count", 0) +
                                         plan.get("unknown_dataset_count", 0)) if plan else 0,
                "known_dataset_count": plan.get("known_dataset_count", 0) if plan else 0,
                "unknown_dataset_count": plan.get("unknown_dataset_count", 0) if plan else 0,
                "unknown_source_count": plan.get("unknown_source_count", 0) if plan else 0}
    except (BoundaryError, OSError, sqlite3.DatabaseError) as error:
        return {"schema": SCHEMA, "status": "recovery_required",
                "retry_available": False,
                "reason": error.code if isinstance(error, BoundaryError) else
                "curation_owner_recovery_required"}


def _inventory(store: ManifestArtifactStore) -> tuple[str, ...]:
    identities = tuple(store.manifest_ids())
    if len(identities) > MAX_MANIFESTS:
        raise BoundaryError("local_curation", "preparation_inventory_limit")
    return identities


def _inventory_hash(identities: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(sorted(identities)).encode()).hexdigest()


def _close(dataset: Any) -> None:
    if isinstance(dataset.records, SpoolSelection):
        dataset.records.owner.close()


class InplaceCurationPreparation:
    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        return preparation_status(self.config,
                                  active=self.thread is not None and self.thread.is_alive())

    def start(self) -> dict[str, Any]:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return self.status()
            current = self.status()
            if current["status"] == "ready":
                return current
            if current["status"] not in {"preparation_required", "preparing",
                                         "recovery_required"}:
                raise BoundaryError("local_curation", "configured_workspace_required")
            if current["status"] == "recovery_required" and not current["retry_available"]:
                raise BoundaryError("local_curation", current["reason"])
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            return {"schema": SCHEMA, "status": "preparing"}

    def _run(self) -> None:
        try:
            self._prepare()
        except Exception as error:
            if isinstance(error, FileExistsError) or (isinstance(error, BoundaryError)
                    and error.code == "preparation_in_progress"):
                return
            # Keep the exact resumable plan; no partial owner authorizes writes.
            try:
                store_dir = _store_dir(self.config)
                plan = _plan(store_dir)
                if plan is not None and plan["status"] != "ready":
                    code = (error.code if isinstance(error, BoundaryError) else
                            "ledger_recovery_required" if isinstance(
                                error, sqlite3.DatabaseError) else
                            "curation_preparation_failed")
                    plan.update(status="failed", error_code=code)
                    atomic_json(store_dir / PLAN_NAME, plan)
            except (BoundaryError, OSError):
                pass

    def _prepare(self) -> None:
        store_dir = _store_dir(self.config)
        store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False, readonly=True))
        path = store_dir / PLAN_NAME
        if not path.exists() and _marker(store_dir) is not None:
            configured_owner(self.config)
            return
        if not path.exists():
            identities = _inventory(store)
            plan = {"schema": SCHEMA, "status": "creating", "workspace_id": uuid.uuid4().hex,
                    "store_id": uuid.uuid4().hex, "ledger_id": uuid.uuid4().hex,
                    "store_path": str(store_dir),
                    "schema_initialized": False,
                    "processed": 0, "total": len(identities),
                    "inventory_sha256": _inventory_hash(identities)}
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(plan, handle)
                handle.flush()
                os.fsync(handle.fileno())
        lock_path = store_dir / LOCK_NAME
        if lock_path.is_symlink():
            raise BoundaryError("local_curation", "preparation_recovery_required")
        from spireagent.workbench.developer_server import instance_lock

        try:
            with instance_lock(lock_path):
                self._prepare_locked(store_dir, store)
        except BoundaryError as error:
            if error.code == "already_running":
                raise BoundaryError("local_curation", "preparation_in_progress") from error
            raise

    def _prepare_locked(self, store_dir: Path, store: ManifestArtifactStore) -> None:
        plan = _plan(store_dir)
        if plan is None:
            raise BoundaryError("local_curation", "preparation_recovery_required")
        if plan["status"] == "ready":
            configured_owner(self.config)
            return
        identities = _inventory(store)
        if _inventory_hash(identities) != plan["inventory_sha256"]:
            raise BoundaryError("local_curation", "preparation_inventory_changed")
        plan["total"] = sum(store.get_manifest(identity).kind in {"evidence", "dataset"}
                            for identity in identities)
        ledger_path = store_dir / LEDGER_NAME
        expected = {"schema": INPLACE_OWNER_SCHEMA, "workspace_id": plan["workspace_id"],
                    "store_id": plan["store_id"], "ledger_id": plan["ledger_id"],
                    "ledger_path": str(ledger_path)}
        marker = _marker(store_dir)
        if marker is None and not plan["schema_initialized"] and not ledger_path.exists():
            atomic_json(store_dir / OWNER_NAME, expected)
        elif marker != expected:
            raise BoundaryError("local_curation", "store_identity_mismatch")
        if not ledger_path.exists() and not plan["schema_initialized"]:
            owner = LocalCurationOwner(ledger_path, store_dir, plan["workspace_id"],
                                      plan["ledger_id"], plan["store_id"], create=True,
                                      allow_existing=True, legacy_guard=True,
                                      owner_schema=INPLACE_OWNER_SCHEMA)
        else:
            owner = _owner(store_dir, expected, legacy=True)
        plan["schema_initialized"] = True
        plan["status"] = "indexing"
        plan.pop("error_code", None)
        plan["processed"] = 0
        atomic_json(store_dir / PLAN_NAME, plan)
        known = unknown = unknown_sources = 0
        processed = 0
        for identity in identities:
            item = store.get_manifest(identity)
            if item.kind != "evidence":
                continue
            projections: list[Any] = []
            selected = None
            try:
                selected = preview(store, (item,), SelectionRules(),
                                   on_projection=projections.append)
                if len(projections) != 1:
                    raise BoundaryError("local_curation", "source_projection_incomplete")
                owner.ledger.index_source(identity, projections[0])
                if item.parameters.value().get("schema") == "stpd/local-verified-bundle-v1":
                    owner.index_human_runs(store, identity, historical=True)
                # Even a verified old source has no complete local download/use
                # receipt history. Its runs cannot be sealed as new Gold.
                runs = owner.ledger.source_runs(identity)
                if runs is None:
                    raise BoundaryError("local_curation", "source_index_incomplete")
                with owner.transaction() as db:
                    db.executemany("INSERT OR IGNORE INTO local_legacy_unknown_runs VALUES(?)",
                                   ((run,) for run in runs))
            except (BoundaryError, OSError, ValueError):
                unknown_sources += 1
                with owner.transaction() as db:
                    db.execute("INSERT OR IGNORE INTO local_legacy_unknown_runs VALUES('*')")
            finally:
                if selected is not None:
                    _close(selected)
            processed += 1
            if processed % 16 == 0:
                plan["processed"] = processed
                atomic_json(store_dir / PLAN_NAME, plan)
        for identity in identities:
            item = store.get_manifest(identity)
            if item.kind != "dataset":
                continue
            processed += 1
            if processed % 16 == 0:
                plan["processed"] = processed
                atomic_json(store_dir / PLAN_NAME, plan)
            info = item.parameters.value()
            schema = info.get("schema")
            purpose = info.get("purpose")
            if purpose == "gold":
                raise BoundaryError("local_curation", "legacy_gold_recovery_required")
            try:
                if schema in {CURATED_SCHEMA, DECISION_SCHEMA, UNION_SCHEMA}:
                    _, selected = load(store, identity)
                    try:
                        runs = selected.run_ids
                    finally:
                        _close(selected)
                elif schema == DATASET_SCHEMA:
                    _, selected_full = load_dataset(store, identity)
                    runs = {record.run_id for record in selected_full.records}
                else:
                    raise BoundaryError("local_curation", "legacy_dataset_unverifiable")
            except (BoundaryError, OSError, ValueError):
                unknown += 1
                with owner.transaction() as db:
                    db.execute("INSERT OR IGNORE INTO local_legacy_unknown_runs VALUES('*')")
                continue
            if purpose in {"training", "test"}:
                owner.ledger.claim(identity, purpose, runs)
                owner.ledger.bind(identity, identity)
                known += 1
            else:
                unknown += 1
                with owner.transaction() as db:
                    db.executemany("INSERT OR IGNORE INTO local_legacy_unknown_runs VALUES(?)",
                                   ((run,) for run in runs))
        plan.update(status="ready", known_dataset_count=known,
                    unknown_dataset_count=unknown, unknown_source_count=unknown_sources,
                    processed=plan["total"])
        atomic_json(store_dir / PLAN_NAME, plan)
