"""Explicitly owned, empty local research workspace for the Workbench.

This manager never adopts the legacy ``research_workspace`` path. A workspace is
created only by an explicit user action under the configured Workbench state
directory, and its marker plus the state-directory registration must agree before
it is opened.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import atomic_json
from spireagent.workbench.local_curation import (
    LEDGER_NAME,
    OWNER_NAME,
    OWNER_SCHEMA,
    LocalCurationOwner,
)
from spireagent.workbench.local_workspace import LocalWorkspace

LEGACY_WORKSPACE_SCHEMA = "stpd/managed-local-workspace-v1"
WORKSPACE_SCHEMA = "stpd/managed-local-workspace-v2"
REGISTRATION_SCHEMA = "stpd/managed-local-workspace-registration-v1"
ROOT_NAME = "managed-research-workspaces"
REGISTRATION_NAME = "managed-research-workspace.json"
_ID = re.compile(r"[a-f0-9]{32}\Z")


def _state_root(state_dir: Path) -> Path:
    root = state_dir.expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise BoundaryError("managed_workspace", "state_directory_unavailable")
    return root


def _paths(state_dir: Path) -> tuple[Path, Path]:
    root = _state_root(state_dir)
    workspace_root = root / ROOT_NAME
    if workspace_root.is_symlink() or (workspace_root.exists() and not workspace_root.is_dir()):
        raise BoundaryError("managed_workspace", "workspace_root_invalid")
    registration = root / REGISTRATION_NAME
    if registration.is_symlink():
        raise BoundaryError("managed_workspace", "registration_invalid")
    return workspace_root, registration


def _read_json(path: Path, code: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BoundaryError("managed_workspace", code)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BoundaryError("managed_workspace", code) from error
    if not isinstance(value, dict):
        raise BoundaryError("managed_workspace", code)
    return value


def _orphaned_count(root: Path, registered_id: str | None) -> int:
    if not root.exists():
        return 0
    try:
        return sum(
            1
            for child in root.iterdir()
            if child.name != registered_id
        )
    except OSError as error:
        raise BoundaryError("managed_workspace", "workspace_root_unavailable") from error


def inspect_managed_workspace(state_dir: Path) -> dict[str, Any]:
    """Return registration state without creating or repairing any path."""
    root, registration_path = _paths(state_dir)
    if not registration_path.exists():
        return {
            "schema": REGISTRATION_SCHEMA,
            "status": "not_created",
            "orphaned_initializations": _orphaned_count(root, None),
            "requires_cloud_account": False,
        }
    registration = _read_json(registration_path, "registration_invalid")
    if set(registration) != {"schema", "workspace_id", "created_at"}:
        raise BoundaryError("managed_workspace", "registration_invalid")
    identity = registration.get("workspace_id")
    created_at = registration.get("created_at")
    if (registration.get("schema") != REGISTRATION_SCHEMA
            or not isinstance(identity, str) or _ID.fullmatch(identity) is None
            or not isinstance(created_at, str) or not created_at):
        raise BoundaryError("managed_workspace", "registration_invalid")
    directory = root / identity
    if directory.is_symlink() or not directory.is_dir():
        raise BoundaryError("managed_workspace", "workspace_marker_invalid")
    marker = _read_json(directory / "workspace.json", "workspace_marker_invalid")
    expected = {
        "schema": LEGACY_WORKSPACE_SCHEMA,
        "workspace_id": identity,
        "created_at": created_at,
        "store": "store",
        "registry": "registry.sqlite",
    }
    legacy = marker == expected
    new_fields = {"curation_ledger": LEDGER_NAME, "ledger_id": marker.get("ledger_id"),
                  "store_id": marker.get("store_id")}
    current = (set(marker) == set(expected) | set(new_fields)
               and marker.get("schema") == WORKSPACE_SCHEMA
               and all(marker.get(key) == value for key, value in expected.items()
                       if key != "schema")
               and marker.get("curation_ledger") == LEDGER_NAME
               and all(isinstance(marker.get(key), str) and _ID.fullmatch(marker[key])
                       for key in ("ledger_id", "store_id")))
    if not legacy and not current:
        raise BoundaryError("managed_workspace", "workspace_marker_invalid")
    store_dir = directory / "store"
    registry_path = directory / "registry.sqlite"
    if (store_dir.is_symlink() or not store_dir.is_dir()
            or registry_path.is_symlink() or not registry_path.is_file()):
        raise BoundaryError("managed_workspace", "workspace_storage_invalid")
    try:
        registry = SQLiteRegistry(registry_path, readonly=True)
    except BoundaryError as error:
        raise BoundaryError("managed_workspace", "workspace_registry_invalid") from error
    except sqlite3.DatabaseError as error:
        raise BoundaryError("managed_workspace", "workspace_registry_invalid") from error
    store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False, readonly=True))
    owner = None
    curation_status = "recovery_required"
    if current:
        owner_marker = _read_json(store_dir / OWNER_NAME, "curation_recovery_required")
        if owner_marker != {"schema": OWNER_SCHEMA, "workspace_id": identity,
                            "store_id": marker["store_id"],
                            "ledger_id": marker["ledger_id"],
                            "ledger_path": str((directory / LEDGER_NAME).resolve())}:
            raise BoundaryError("managed_workspace", "curation_recovery_required")
        try:
            owner = LocalCurationOwner(directory / LEDGER_NAME, store_dir, identity,
                                      marker["ledger_id"], marker["store_id"])
        except BoundaryError as error:
            raise BoundaryError("managed_workspace", "curation_recovery_required") from error
        curation_status = "ready"
    return {
        "schema": REGISTRATION_SCHEMA,
        "status": "ready",
        "workspace_id": identity,
        "created_at": created_at,
        "orphaned_initializations": _orphaned_count(root, identity),
        "requires_cloud_account": False,
        "workspace": LocalWorkspace(registry, store),
        "curation_status": curation_status,
        "curation_recovery": (
            "legacy_history_requires_explicit_migration" if legacy else None
        ),
        "curation_owner": owner,
    }


def create_managed_workspace(state_dir: Path) -> dict[str, Any]:
    """Create once under state_dir; never adopt, replace, or clean old directories."""
    root, registration_path = _paths(state_dir)
    current = inspect_managed_workspace(state_dir)
    if current["status"] == "ready":
        return current
    if registration_path.exists():
        # A broken existing registration is not a blank slate and must not be replaced.
        raise BoundaryError("managed_workspace", "registration_invalid")

    if not root.parent.is_dir():
        raise BoundaryError("managed_workspace", "state_directory_unavailable")
    root.mkdir(mode=0o700, parents=False, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise BoundaryError("managed_workspace", "workspace_root_invalid")
    identity = uuid.uuid4().hex
    directory = root / identity
    directory.mkdir(mode=0o700, exist_ok=False)
    store_dir = directory / "store"
    store_dir.mkdir(mode=0o700)
    registry_path = directory / "registry.sqlite"
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    ledger_id, store_id = uuid.uuid4().hex, uuid.uuid4().hex

    # The durable workspace marker and empty store/index must exist before the
    # one state-directory pointer is published. Failure leaves an inspectable
    # orphan and never reports a half-created workspace as registered.
    try:
        ManifestArtifactStore(LocalBlobStore(store_dir, create=False, readonly=False))
        SQLiteRegistry(registry_path, readonly=False)
        atomic_json(store_dir / OWNER_NAME,
                    {"schema": OWNER_SCHEMA, "workspace_id": identity,
                     "store_id": store_id, "ledger_id": ledger_id,
                     "ledger_path": str((directory / LEDGER_NAME).resolve())})
        LocalCurationOwner(directory / LEDGER_NAME, store_dir, identity, ledger_id,
                           store_id, create=True)
    except (OSError, sqlite3.DatabaseError, BoundaryError) as error:
        raise BoundaryError("managed_workspace", "workspace_initialization_failed") from error
    marker = {
        "schema": WORKSPACE_SCHEMA,
        "workspace_id": identity,
        "created_at": created_at,
        "store": "store",
        "registry": "registry.sqlite",
        "curation_ledger": LEDGER_NAME,
        "ledger_id": ledger_id,
        "store_id": store_id,
    }
    try:
        atomic_json(directory / "workspace.json", marker)
        atomic_json(
            registration_path,
            {"schema": REGISTRATION_SCHEMA, "workspace_id": identity, "created_at": created_at},
        )
    except OSError as error:
        raise BoundaryError("managed_workspace", "workspace_registration_failed") from error
    result = inspect_managed_workspace(state_dir)
    if result["status"] != "ready":
        raise BoundaryError("managed_workspace", "workspace_registration_failed")
    return result
