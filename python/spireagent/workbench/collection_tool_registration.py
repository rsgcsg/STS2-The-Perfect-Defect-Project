"""Private registration of an operator-pinned public CollectionTool directory."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sts2_platform_evidence.collection_tool import CollectionTool

from spireagent.json_boundary import BoundaryError, decode_json, digest, object_fields
from spireagent.workbench.developer import ProjectConfig, atomic_json

SCHEMA = "stpd/collection-tool-registration-v1"
REGISTRATION_FILE = "collection-tool-registration.json"
REGISTRATION_LIMIT = 64 * 1024


def _tool(directory: Path, release_id: str) -> Path:
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
        raise BoundaryError("collection_tool", "absolute_non_symlink_tool_directory_required")
    try:
        # The public owner validates the clean-source identity and complete byte inventory.
        CollectionTool(directory, release_id)
    except (OSError, ValueError, TypeError):
        raise BoundaryError(
            "collection_tool", "collection_tool_identity_verification_failed"
        ) from None
    return directory.resolve()


def _registration(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise BoundaryError(
            "collection_tool", "collection_tool_registration_missing_or_unsafe"
        ) from None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size > REGISTRATION_LIMIT
        or (os.name != "nt" and info.st_mode & 0o077)
    ):
        raise BoundaryError("collection_tool", "collection_tool_registration_missing_or_unsafe")
    raw = decode_json(path.read_bytes())
    fields = {"schema", "path", "release_id"}
    if not isinstance(raw, dict) or set(raw) not in (
        fields,
        fields | {"host_lifecycle", "native_log_file"},
    ):
        raise BoundaryError("collection_tool", "collection_tool_registration_invalid")
    value = object_fields(raw, set(raw), "collection_tool.registration")
    if value["schema"] != SCHEMA or not isinstance(value["path"], str):
        raise BoundaryError("collection_tool", "collection_tool_registration_invalid")
    digest(value["release_id"], "collection_tool.release_id")
    return value


def register_collection_tool(
    config: ProjectConfig,
    directory: Path,
    release_id: str,
    *,
    replace: bool = False,
    host_lifecycle: Path | None = None,
    native_log_file: Path | None = None,
) -> dict[str, Any]:
    """Register verified bytes without executing, packaging, binding or uploading anything."""
    from spireagent.workbench.developer_server import instance_lock

    identity = digest(release_id, "collection_tool.release_id")
    # The same OS-held lock as the workbench guarantees registration while it is stopped.
    with instance_lock(config.state_dir / "instance.lock"):
        tool = _tool(directory, identity)
        target = config.state_dir / REGISTRATION_FILE
        registration = {"schema": SCHEMA, "path": str(tool), "release_id": identity}
        if (host_lifecycle is None) != (native_log_file is None):
            raise BoundaryError("collection_tool", "host_lifecycle_and_log_required")
        if host_lifecycle is not None:
            assert native_log_file is not None
            if (
                not host_lifecycle.is_absolute()
                or host_lifecycle.is_symlink()
                or not native_log_file.is_absolute()
                or native_log_file.is_symlink()
            ):
                raise BoundaryError("collection_tool", "absolute_host_lifecycle_required")
            registration["host_lifecycle"] = str(host_lifecycle)
            registration["native_log_file"] = str(native_log_file)
            _host_context(config, host_lifecycle, native_log_file)

        if target.is_symlink():
            raise BoundaryError("collection_tool", "collection_tool_registration_missing_or_unsafe")
        if target.exists():
            try:
                previous = _registration(target)
            except (OSError, ValueError, BoundaryError):
                if not replace:
                    raise BoundaryError(
                        "collection_tool", "collection_tool_registration_invalid"
                    ) from None
                previous = None
            if previous == registration:
                return {
                    "schema": SCHEMA,
                    "status": "registered",
                    "release_id": identity,
                    "changed": False,
                }
            if not replace:
                raise BoundaryError(
                    "collection_tool", "collection_tool_registration_conflict_use_replace_tool"
                )
        atomic_json(target, registration)
        return {"schema": SCHEMA, "status": "registered", "release_id": identity, "changed": True}


def registered_collection_tool(config: ProjectConfig, expected_release_id: str) -> Path:
    """Resolve only the requested release after re-verifying its full current byte identity."""
    expected = digest(expected_release_id, "collection_tool.expected_release_id")
    registration = _registration(config.state_dir / REGISTRATION_FILE)
    if registration["release_id"] != expected:
        raise BoundaryError("collection_tool", "collection_tool_registered_release_mismatch")
    return _tool(Path(registration["path"]), expected)


def current_collection_tool(config: ProjectConfig) -> tuple[Path, str]:
    """The installed release owns software identity; daily consent does not pin it."""
    registration = _registration(config.state_dir / REGISTRATION_FILE)
    identity = registration["release_id"]
    return _tool(Path(registration["path"]), identity), identity


def _host_context(config: ProjectConfig, path: Path, native_log_file: Path) -> dict[str, Any]:
    """Read only the selected Host lifecycle; Host profile supplies the isolated data root."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024**2:
            raise ValueError
        record = decode_json(path.read_bytes())
        profile = record["profile"]
        runtime = record["loaded_identity"]["host"]["runtime_instance_id"]
        address = urlsplit(record["endpoint"])
        root = Path(profile["expected_user_data_root"])
        if (
            record.get("schema_version") != 1
            or record.get("status") != "ready"
            or type(record["pid"]) is not int
            or record["pid"] <= 0
            or not isinstance(runtime, str)
            or not runtime
            or address.scheme != "http"
            or address.hostname not in {"127.0.0.1", "localhost", "::1"}
            or address.port is None
            or not root.is_absolute()
            or not native_log_file.is_file()
            or native_log_file.is_symlink()
            or root not in native_log_file.parents
            or (config.platform_url and record["endpoint"] != config.platform_url)
        ):
            raise ValueError
        return {
            "process_id": record["pid"],
            "runtime_instance_id": runtime,
            "endpoint": record["endpoint"],
            "connector_port": address.port,
            "native_log_file": str(native_log_file),
            "profile_generation_id": profile["generation_id"],
        }
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise BoundaryError("collection_tool", "selected_host_context_unavailable") from error


def selected_host_context(config: ProjectConfig) -> dict[str, Any] | None:
    path = config.state_dir / REGISTRATION_FILE
    if not path.exists() and not path.is_symlink():
        return None
    value = _registration(path)
    selected = value.get("host_lifecycle")
    return (
        _host_context(config, Path(selected), Path(value["native_log_file"]))
        if selected is not None
        else None
    )


def inspect_collection_destination(
    config: ProjectConfig,
    tool: CollectionTool,
    *,
    recordings_root: Path,
    game_directory: Path | None = None,
    allow_stopped_inventory: bool = False,
) -> dict[str, Any]:
    """One setup consumer for Catalog and collection preparation; no native rule duplication."""
    context = selected_host_context(config)
    options: dict[str, Any] = (
        {"native_log_file": context["native_log_file"], "connector_port": context["connector_port"]}
        if context is not None
        else {}
    )
    if game_directory is not None:
        options["game_directory"] = game_directory
    result = tool.setup_status(recordings_root=recordings_root, **options)
    if context is not None:
        if selected_host_context(config) != context:
            raise BoundaryError("collection_tool", "selected_host_context_changed_or_unconfirmed")
        # Catalog may read the owner's configured directory while no game is
        # running. It still validates that root before scanning. This does not
        # assert an active runtime or relax the preparation/mutation callers.
        stopped_inventory = (
            allow_stopped_inventory
            and result.get("game_running") is False
            and result.get("loaded_identity") is None
            and isinstance(result.get("configured_recordings_root"), str)
            and (
                (result.get("reason") == "configured_game_stopped"
                 and result.get("configured") is True)
                or (result.get("reason") == "recording_root_not_configured"
                    and result.get("configured") is False)
            )
        )
        if stopped_inventory:
            return result
        loaded = result.get("loaded_identity") or {}
        if (
            any(loaded.get(key) != context[key] for key in ("process_id", "runtime_instance_id"))
            or loaded.get("connector_endpoint") != context["endpoint"]
        ):
            raise BoundaryError("collection_tool", "selected_host_context_changed_or_unconfirmed")
    return result
