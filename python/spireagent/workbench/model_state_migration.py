"""Explicit, stopped Workbench import of checkout-era private model metadata.

Only exact Runtime profile pins become active. Old selection bindings are
archived as history; their source and environment identities are never rebased.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError
from spireagent.package_identity import PackageIdentityError
from spireagent.policy_files import _inside, _object_file
from spireagent.workbench.developer import ProjectConfig, atomic_json
from spireagent.workbench.developer_server import instance_lock, running
from spireagent.workbench.local_models import (
    TEXT_PROFILES,
    LocalModelService,
    _check_runtime_port,
)
from spireagent.workbench.runtime_install import validate_runtime_install


def _ordinary_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise BoundaryError("local_model", "legacy_metadata_unsafe")
    return path.read_bytes()


def _legacy_entry_file(old: Path, relative: object) -> tuple[str, bytes]:
    if (not isinstance(relative, str) or not relative.startswith(".local/")
            or Path(relative).is_absolute() or ".." in Path(relative).parts):
        raise BoundaryError("local_model", "legacy_metadata_unsafe")
    inner = Path(relative).relative_to(".local")
    path = old / inner
    for parent in (path, *path.parents):
        if parent == old.parent:
            break
        if parent.is_symlink():
            raise BoundaryError("local_model", "legacy_metadata_unsafe")
    return inner.as_posix(), _ordinary_file(path)


def _write_new_file(path: Path, raw: bytes) -> None:
    """Write a staging file completely before it can become an owner record."""
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        handle = os.open(path, os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)


def _publish_profile(destination: Path, raw: bytes) -> None:
    """Publish a complete pin only when no different pin owns the final name."""
    temporary = destination.with_name(destination.name + ".stage-" + uuid4().hex)
    try:
        _write_new_file(temporary, raw)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if _ordinary_file(destination) != raw:
                raise BoundaryError("local_model", "private_profile_collision") from None
    finally:
        temporary.unlink(missing_ok=True)
    _sync_directory(destination.parent)


def migrate_legacy_model_state(config: ProjectConfig, legacy_python_root: Path) -> dict[str, Any]:
    """Archive old facts and import only matching installed private Runtime pins."""
    if (not legacy_python_root.is_absolute() or legacy_python_root.is_symlink()
            or not legacy_python_root.is_dir()):
        raise BoundaryError("local_model", "legacy_root_unsafe")
    old = legacy_python_root / ".local"
    if old.is_symlink() or not old.is_dir():
        raise BoundaryError("local_model", "legacy_metadata_unsafe")
    with instance_lock(config.state_dir / "instance.lock"):
        if running(config) is not None:
            raise BoundaryError("local_model", "close_workbench_before_model_state_migration")
        models = LocalModelService(config)
        if models.state["status"] == "recovery_required":
            raise BoundaryError("local_model", "previous_operation_requires_recovery")
        _check_runtime_port(15527)
        if models.private_root.is_symlink():
            raise BoundaryError("local_model", "private_model_state_unsafe")

        files: dict[str, bytes] = {}
        pins: dict[str, bytes] = {}
        legacy_selection_count = 0
        for _profile_id, (_, legacy_name, schema, slot) in TEXT_PROFILES.items():
            path = legacy_python_root / legacy_name
            if not path.exists() and not path.is_symlink():
                continue
            raw = _ordinary_file(path)
            value = _object_file(path)
            pin = value.get("runtime_package")
            if (set(value) != {"schema", "runtime_package"} or value["schema"] != schema
                    or not isinstance(pin, dict)):
                raise BoundaryError("local_model", "legacy_profile_invalid")
            directory = models.directory / slot
            if directory.is_symlink():
                raise BoundaryError("local_model", "runtime_install_path_unsafe")
            try:
                validate_runtime_install(directory / "runtime/node_modules", pin,
                                         models._connector_pin())
            except (OSError, ValueError, PackageIdentityError) as error:
                raise BoundaryError("local_model", "text_runtime_local_install_required") from error
            name = Path(legacy_name).name
            pins[name] = raw
            files[name] = raw
        roster = old / "token-policies-v1.json"
        if roster.exists() or roster.is_symlink():
            raw = _ordinary_file(roster)
            value = _object_file(roster)
            entries = value.get("policies")
            if (value.get("schema") != "stpd/local-token-policies-v1"
                    or not isinstance(entries, list)):
                raise BoundaryError("local_model", "legacy_metadata_unsafe")
            legacy_selection_count = len(entries)
            files["token-policies-v1.json"] = raw
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                    raise BoundaryError("local_model", "legacy_metadata_unsafe")
                for key in ("config", "manifest"):
                    name, payload = _legacy_entry_file(old, entry.get(key))
                    files[name] = payload
        if not files:
            raise BoundaryError("local_model", "legacy_metadata_missing")
        for name, raw in pins.items():
            destination = models.private_root / name
            if (destination.is_symlink() or destination.exists()
                    and _ordinary_file(destination) != raw):
                raise BoundaryError("local_model", "private_profile_collision")
        inventory = {name: hashlib.sha256(raw).hexdigest()
                     for name, raw in sorted(files.items())}
        archive_id = hashlib.sha256(canonical_json({
            "legacy_python_root": str(legacy_python_root.resolve()), "files": inventory,
        }).encode()).hexdigest()
        archive = models.private_root / "legacy-archive" / archive_id
        if archive.parent.is_symlink() or archive.is_symlink():
            raise BoundaryError("local_model", "legacy_archive_unsafe")
        if archive.exists():
            recorded = _object_file(archive / "inventory.json")
            if recorded != {"schema": "stpd/legacy-model-state-archive-v1",
                            "legacy_python_root": str(legacy_python_root.resolve()),
                            "files": inventory}:
                raise BoundaryError("local_model", "legacy_archive_collision")
            for name, raw in files.items():
                if _ordinary_file(_inside(archive, name)) != raw:
                    raise BoundaryError("local_model", "legacy_archive_collision")
        else:
            archive.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            stage = archive.with_name(".stage-" + archive_id + "-" + uuid4().hex)
            stage.mkdir(mode=0o700)
            try:
                for name, raw in files.items():
                    target = _inside(stage, name)
                    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                    _write_new_file(target, raw)
                atomic_json(stage / "inventory.json", {
                    "schema": "stpd/legacy-model-state-archive-v1",
                    "legacy_python_root": str(legacy_python_root.resolve()),
                    "files": inventory,
                })
                for name, raw in files.items():
                    if _ordinary_file(_inside(stage, name)) != raw:
                        raise BoundaryError("local_model", "legacy_archive_staging_invalid")
                for directory in sorted((stage, *stage.rglob("*")),
                                        key=lambda path: len(path.parts), reverse=True):
                    if directory.is_dir():
                        _sync_directory(directory)
                stage.rename(archive)
                _sync_directory(archive.parent)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        models.private_root.mkdir(parents=True, exist_ok=True)
        for name, raw in pins.items():
            destination = models.private_root / name
            if not destination.exists():
                _publish_profile(destination, raw)
        return {"schema": "stpd/model-state-migration-v1", "status": "archived",
                "archive_id": archive_id, "imported_profiles": sorted(pins),
                "legacy_selections": legacy_selection_count,
                "legacy_selections_loadable": False}
