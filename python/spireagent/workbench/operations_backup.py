"""Private Scheduler snapshots, including original handles, in the existing Store."""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.hub.database import CURRENT_SCHEMA, Operations
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.source import source_identity
from spireagent.storage.config import open_store
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ROOT, ProjectConfig

SCHEMA = "spireagent/private-operations-backup-1"
PRIVATE_TABLES = frozenset({"settings", "jobs", "compute_attempts", "events", "sqlite_sequence"})


def _validate(db: sqlite3.Connection) -> dict[str, int]:
    if (db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA user_version").fetchone()[0] != CURRENT_SCHEMA):
        raise BoundaryError("operations_backup", "unsupported_or_corrupt_snapshot")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # This is a local Scheduler, never a Hub/account/collection database export.
    for table in tables - PRIVATE_TABLES:
        quoted = '"' + table.replace('"', '""') + '"'
        if db.execute("SELECT count(*) FROM " + quoted).fetchone()[0]:
            raise BoundaryError("operations_backup", "private_scheduler_database_required")
    if db.execute("SELECT value FROM settings WHERE key='paused'").fetchone() != ("1",):
        raise BoundaryError("operations_backup", "paused_recovery_required")
    if db.execute("SELECT 1 FROM jobs WHERE lease_hash IS NOT NULL LIMIT 1").fetchone():
        raise BoundaryError("operations_backup", "credential_free_snapshot_required")
    return {name: db.execute("SELECT count(*) FROM " + name).fetchone()[0]
            for name in ("jobs", "compute_attempts", "events")}


def publish_backup(config: ProjectConfig, store: ManifestArtifactStore, producer: Producer,
                   *, scratch: Path) -> Manifest:
    deployment = config.remote_compute
    if deployment is None or not deployment.operations_path.is_file():
        raise BoundaryError("operations_backup", "existing_remote_operations_required")
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="operations-backup-", dir=scratch) as name:
        snapshot = Path(name) / "operations.sqlite"
        # Existing SQLite WAL-aware owner backup; only this copy is paused/sanitized.
        Operations(deployment.operations_path).backup(snapshot)
        with closing(sqlite3.connect(snapshot)) as db:
            # ConsoleIndex.artifact_closure rebuilds these projections from Store
            # manifests. Drop their rows only in the copy; access/owner state is
            # still rejected by the unchanged private-snapshot validation below.
            for table in ("console_lineage", "console_artifact_policy", "console_artifacts"):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                              (table,)).fetchone():
                    db.execute("DELETE FROM " + table)
            db.execute("UPDATE jobs SET lease_hash=NULL")
            db.commit()
            _validate(db)
            db.execute("VACUUM")  # No deleted account/credential bytes in free pages.
        with snapshot.open("rb") as handle:
            payload = store.put_payload("operations_database", handle, "application/vnd.sqlite3")
    manifest = Manifest("analysis", producer, payloads=(payload,), parameters=FrozenObject.of({
        "schema": SCHEMA, "created_at": datetime.now(UTC).isoformat(),
        "compute_store": dict(deployment.compute_store), "restore_live": False,
        "scope": "private_scheduler_jobs_attempts_original_provider_handles_events",
        "authentication_exported": False, "target_configuration": "operator_owned_not_exported",
        "restored_dispatch": "paused_reconcile_exact_original_handles_before_enable",
    }))
    store.publish(manifest)
    return manifest


def restore_backup(store: ManifestArtifactStore, artifact_id: str,
                   destination: Path) -> dict[str, Any]:
    manifest = store.get_manifest(artifact_id)
    info = manifest.parameters.value()
    if (manifest.kind != "analysis" or info.get("schema") != SCHEMA
            or [payload.role for payload in manifest.payloads] != ["operations_database"]
            or manifest.parents):
        raise BoundaryError("operations_backup", "operations_backup_required")
    if destination.exists() or destination.is_symlink():
        raise BoundaryError("operations_backup", "new_restore_directory_required")
    destination.mkdir(parents=True, mode=0o700)
    path = store.materialize(manifest.payload("operations_database"),
                             destination / "operations.sqlite")
    path.chmod(0o600)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        counts = _validate(db)
    return {"schema": SCHEMA, "artifact_id": manifest.artifact_id, "status": "verified_staged",
            "live_replaced": False, "dispatch": "paused", "counts": counts,
            "compute_store": info["compute_store"], "automatic_retry": False,
            "next": "Review original writer and target; reconcile handles before enable."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--store", required=True)
    parser.add_argument("--artifact")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        store = open_store(args.store)
        if args.action == "backup":
            if args.config is None:
                raise BoundaryError("operations_backup", "configured_operations_required")
            manifest = publish_backup(ProjectConfig.load(args.config), store, source_identity(ROOT),
                                      scratch=args.output)
            result = {"artifact_id": manifest.artifact_id, "status": "published"}
        else:
            if args.artifact is None:
                raise BoundaryError("operations_backup", "backup_artifact_required")
            result = restore_backup(store, args.artifact, args.output)
    except (BoundaryError, OSError, ValueError, sqlite3.DatabaseError) as error:
        print(json.dumps({"status": "failed", "error_code": getattr(
            error, "code", type(error).__name__)}))
        return 1
    print(json.dumps(result))
    return 0
