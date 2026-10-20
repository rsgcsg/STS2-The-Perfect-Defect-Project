"""Private Curation owner snapshots in the existing immutable ArtifactStore."""

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
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.source import source_identity
from spireagent.storage.config import open_store
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ROOT, ProjectConfig
from spireagent.workbench.inplace_curation import PLAN_NAME, configured_owner
from spireagent.workbench.local_curation import OWNER_NAME, LocalCurationOwner

SCHEMA = "spireagent/private-curation-backup-v1"


def publish_backup(
    owner: LocalCurationOwner, store: ManifestArtifactStore, producer: Producer, *, scratch: Path,
) -> Manifest:
    """Preserve claims/use/index without copying account state or operation credentials."""
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="curation-backup-", dir=scratch) as name:
        snapshot = Path(name) / "curation.sqlite"
        owner.backup(snapshot)
        with snapshot.open("rb") as handle:
            payload = store.put_payload("curation_database", handle, "application/vnd.sqlite3")
    marker = store.put_bytes("curation_owner", (owner.store_dir / OWNER_NAME).read_bytes())
    payloads = [payload, marker]
    plan = owner.store_dir / PLAN_NAME
    if plan.exists():
        payloads.append(store.put_bytes("curation_preparation", plan.read_bytes()))
    manifest = Manifest("analysis", producer, payloads=tuple(payloads), parameters=FrozenObject.of({
        "schema": SCHEMA, "owner_identity": list(owner.identity),
        "created_at": datetime.now(UTC).isoformat(), "restore_live": False,
        "scope": "private_curation_claims_uses_indexes_not_application_account_or_job_state",
    }))
    store.publish(manifest)
    return manifest


def restore_backup(
    store: ManifestArtifactStore, artifact_id: str, destination: Path,
) -> dict[str, Any]:
    """Stage verified bytes in a new directory; never replace a live owner."""
    manifest = store.get_manifest(artifact_id)
    info = manifest.parameters.value()
    identity = info.get("owner_identity")
    if (manifest.kind != "analysis" or info.get("schema") != SCHEMA
            or not isinstance(identity, list) or len(identity) != 4
            or any(not isinstance(x, str) or not x for x in identity)):
        raise BoundaryError("local_curation", "curation_backup_required")
    if destination.exists() or destination.is_symlink():
        raise BoundaryError("local_curation", "new_restore_directory_required")
    roles = {p.role for p in manifest.payloads}
    if not {"curation_database", "curation_owner"} <= roles <= {
        "curation_database", "curation_owner", "curation_preparation",
    }:
        raise BoundaryError("local_curation", "curation_backup_payloads_invalid")
    destination.mkdir(mode=0o700, parents=True)
    snapshot = store.materialize(manifest.payload("curation_database"),
                                 destination / "curation.sqlite")
    snapshot.chmod(0o600)
    with closing(sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        LocalCurationOwner._validate_backup(db, tuple(identity))
        counts = {name: db.execute("SELECT count(*) FROM " + name).fetchone()[0]
                  for name in ("curation_claims", "curation_uses", "curation_source_uses",
                               "curation_sources", "curation_annotations")}
    marker_path = store.materialize(manifest.payload("curation_owner"), destination / OWNER_NAME)
    marker_path.chmod(0o600)
    marker = json.loads(marker_path.read_bytes())
    if any(marker.get(key) != expected for key, expected in zip(
        ("workspace_id", "ledger_id", "store_id"), identity[:3], strict=True
    )):
        raise BoundaryError("local_curation", "backup_owner_marker_mismatch")
    if "curation_preparation" in roles:
        store.materialize(manifest.payload("curation_preparation"), destination / PLAN_NAME)
    return {"artifact_id": artifact_id, "status": "verified_staged", "live_replaced": False,
            "destination": str(destination), "original_owner_identity": identity, "counts": counts,
            "next": "Stop the owning Application; review exact original paths before replacement."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--store", required=True)
    parser.add_argument("--artifact")
    parser.add_argument("--output", type=Path, required=True,
                        help="backup scratch or a new restore directory")
    args = parser.parse_args(argv)
    try:
        store = open_store(args.store)
        if args.action == "backup":
            if args.config is None:
                raise BoundaryError("local_curation", "configured_owner_required")
            owner = configured_owner(ProjectConfig.load(args.config))
            manifest = publish_backup(owner, store, source_identity(ROOT), scratch=args.output)
            result = {"artifact_id": manifest.artifact_id, "status": "published",
                      "live_replaced": False}
        else:
            if args.artifact is None:
                raise BoundaryError("local_curation", "backup_artifact_required")
            result = restore_backup(store, args.artifact, args.output)
    except (BoundaryError, OSError, ValueError, sqlite3.DatabaseError) as error:
        code = error.code if isinstance(error, BoundaryError) else type(error).__name__
        print(json.dumps({"status": "failed", "error_code": code}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0
