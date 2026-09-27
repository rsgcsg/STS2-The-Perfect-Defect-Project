"""Read-only catalog of Game Mod-owned, closed recording sessions.

The projection exposes only bounded session metadata. It does not verify or
pack evidence, infer Human origin, or write to the source or research store.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sts2_platform_evidence.collection_tool import CollectionTool

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.collection_tool_registration import (
    REGISTRATION_FILE,
    current_collection_tool,
)
from spireagent.workbench.developer import ProjectConfig

SCHEMA = "stpd/local-recording-catalog-v1"
RECORDING_SCHEMA = "sts2.human-annotator/recording-manifest-2"
CLOSE_SCHEMA = "sts2.human-annotator/session-close-1"
MAX_ROOT_ENTRIES = 512
MAX_METADATA_BYTES = 64 * 1024
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_existing_directory(value: object) -> Path | None:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        return None
    path = Path(value)
    try:
        for current in (path, *path.parents):
            if stat.S_ISLNK(current.lstat().st_mode):
                return None
        info = path.stat()
    except OSError:
        return None
    if not stat.S_ISDIR(info.st_mode) or path == Path(path.anchor):
        return None
    if (path / "recording-manifest.json").exists():
        return None
    return path


def _read_regular_metadata(path: Path) -> bytes | None:
    """Read one small, non-symlink metadata file without following links."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            return None
        if before.st_size > MAX_METADATA_BYTES:
            return None
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode)
                or info.st_size > MAX_METADATA_BYTES
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
            return None
        chunks: list[bytes] = []
        remaining = MAX_METADATA_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        return data if len(data) <= MAX_METADATA_BYTES else None
    finally:
        os.close(descriptor)


def _json_object(data: bytes | None) -> dict[str, Any] | None:
    if data is None:
        return None
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _closed_session(directory: Path) -> dict[str, Any] | None:
    try:
        if not stat.S_ISDIR(directory.lstat().st_mode):
            return None
    except OSError:
        return None
    manifest_bytes = _read_regular_metadata(directory / "recording-manifest.json")
    close_bytes = _read_regular_metadata(directory / "session-close-receipt.json")
    manifest = _json_object(manifest_bytes)
    close = _json_object(close_bytes)
    if manifest is None or close is None:
        return None
    session_id, timeline_id = manifest.get("session_id"), manifest.get("timeline_id")
    closed_at = close.get("closed_at")
    if (
        manifest.get("schema") != RECORDING_SCHEMA
        or close.get("schema") != CLOSE_SCHEMA
        or close.get("status") != "closed"
        or not isinstance(closed_at, str)
        or not closed_at
        or not isinstance(session_id, str)
        or not _ID.fullmatch(session_id)
        or not isinstance(timeline_id, str)
        or not _ID.fullmatch(timeline_id)
        or close.get("session_id") != session_id
        or close.get("timeline_id") != timeline_id
    ):
        return None
    return {
        "session_id": session_id,
        "timeline_id": timeline_id,
        "closed_at": closed_at[:64],
        "manifest_sha256": _sha256(manifest_bytes or b""),
        "close_sha256": _sha256(close_bytes or b""),
    }


class LocalRecordingCatalog:
    """Observe the fixed native owner and keep candidate paths server-side."""

    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self._candidates: dict[str, dict[str, Any]] = {}

    def candidate(self, candidate_id: str) -> dict[str, Any] | None:
        """Resolve only a candidate from the latest catalog observation."""
        if not isinstance(candidate_id, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
            return None
        value = self._candidates.get(candidate_id)
        return dict(value) if value is not None else None

    def read(self) -> dict[str, Any]:
        observed_at = datetime.now(UTC).isoformat()
        self._candidates = {}
        try:
            tool_directory, release_id = current_collection_tool(self.config)
        except BoundaryError:
            try:
                (self.config.state_dir / REGISTRATION_FILE).lstat()
                missing = False
            except FileNotFoundError:
                missing = True
            except OSError:
                missing = False
            return self._result(
                status="tool_registration_missing" if missing else "tool_unavailable",
                observed_at=observed_at,
                error_code=("collection_tool_registration_missing" if missing
                            else "collection_tool_registration_unavailable"),
            )
        except (OSError, ValueError, TypeError, RuntimeError):
            return self._result(
                status="tool_unavailable", observed_at=observed_at,
                error_code="collection_tool_registration_unavailable",
            )
        try:
            tool = CollectionTool(tool_directory, release_id)
            # This inert profile-owned path is an expected-root probe, not a
            # guessed recordings location. The native owner reports its own root.
            expected_root = self.config.state_dir / "local-recordings-root-probe"
            owner = tool.setup_status(recordings_root=expected_root)
        except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError):
            return self._result(
                status="owner_unavailable", observed_at=observed_at,
                error_code="collection_setup_unavailable",
            )

        root, basis = self._owner_root(owner, expected_root)
        if root is None:
            return self._result(
                status="owner_unavailable", observed_at=observed_at,
                game_running=owner.get("game_running") if isinstance(owner, dict) else None,
                error_code="native_recording_root_unavailable",
            )
        if not root.exists():
            return self._result(
                status="recordings_unavailable", observed_at=observed_at,
                root_basis=basis,
                game_running=owner.get("game_running"),
                error_code="recordings_root_not_found",
            )

        try:
            candidates, unsealed, truncated = self._scan(root)
        except OSError:
            return self._result(
                status="recordings_unavailable", observed_at=observed_at,
                root_basis=basis, game_running=owner.get("game_running"),
                error_code="recordings_root_unreadable",
            )
        self._candidates = {item["candidate_id"]: item["_server"] for item in candidates}
        public = [{key: value for key, value in item.items() if key != "_server"}
                  for item in candidates]
        return {
            "schema": SCHEMA,
            "status": "ready",
            "observed_at": observed_at,
            "root_basis": basis,
            "game_running": owner.get("game_running"),
            "candidate_count": len(public),
            "unsealed_count": unsealed,
            "truncated": truncated,
            "candidates": public,
            "requires_cloud_account": False,
            "claim": "close_metadata_present_bundle_and_human_origin_not_verified",
        }

    def _owner_root(self, owner: object, expected_root: Path) -> tuple[Path | None, str | None]:
        if (not isinstance(owner, dict)
                or owner.get("schema") != "sts2.platform/collection-setup-1"
                or owner.get("recordings_root") != os.path.normpath(str(expected_root))):
            return None, None
        errors = owner.get("errors")
        if not isinstance(errors, list) or errors:
            return None, None
        installed = owner.get("installed_artifact")
        if (not isinstance(installed, dict)
                or not re.fullmatch(r"[0-9a-f]{64}", str(installed.get("sha256", "")))
                or not isinstance(installed.get("module_version_id"), str)
                or not installed["module_version_id"]):
            return None, None
        game_directory = _safe_existing_directory(owner.get("game_directory"))
        if game_directory is None:
            return None, None

        def outside_game(root: Path | None) -> Path | None:
            if root is None:
                return None
            if game_directory is not None and (
                root == game_directory
                or root in game_directory.parents
                or game_directory in root.parents
            ):
                return None
            return root

        if owner.get("game_running") is False:
            reason = owner.get("reason")
            raw = owner.get("configured_recordings_root")
            if ((reason == "configured_game_stopped" and owner.get("configured") is True)
                    or (reason == "recording_root_not_configured"
                        and owner.get("configured") is False)):
                basis = "configured_only"
            else:
                return None, None
            root = outside_game(_safe_existing_directory(raw))
            return (root, basis) if root is not None else (None, None)
        if owner.get("game_running") is True:
            if (owner.get("reason") not in {
                    "current_runtime_bound", "current_runtime_root_mismatch"
                }
                    or owner.get("connected") is not True
                    or not isinstance(owner.get("loaded_identity"), dict)
                    or type(owner["loaded_identity"].get("process_id")) is not int
                    or not isinstance(owner["loaded_identity"].get("game"), dict)):
                return None, None
            root = outside_game(_safe_existing_directory(owner.get("actual_recordings_root")))
            return (root, "current_runtime") if root is not None else (None, None)
        return None, None

    def _scan(self, root: Path) -> tuple[list[dict[str, Any]], int, bool]:
        candidates: list[dict[str, Any]] = []
        unsealed = 0
        truncated = False
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_ROOT_ENTRIES:
                    truncated = True
                    break
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISDIR(info.st_mode):
                    continue
                directory = Path(entry.path)
                metadata = _closed_session(directory)
                if metadata is None:
                    manifest = directory / "recording-manifest.json"
                    close = directory / "session-close-receipt.json"
                    if manifest.exists() and not close.exists():
                        unsealed += 1
                    continue
                # The path stays in this process. Only the opaque candidate ID
                # and matching seal metadata cross the browser boundary.
                material = "\0".join((str(root), entry.name,
                                       metadata["manifest_sha256"],
                                       metadata["close_sha256"]))
                candidate_id = _sha256(material.encode("utf-8"))
                server_value = {
                    "recordings_root": root,
                    "source_directory": directory,
                    **metadata,
                }
                candidates.append({
                    "candidate_id": candidate_id,
                    "session_id": metadata["session_id"],
                    "timeline_id": metadata["timeline_id"],
                    "closed_at": metadata["closed_at"],
                    "state": "close_metadata_present_pending_bundle_verification",
                    "_server": server_value,
                })
        candidates.sort(key=lambda item: (item["closed_at"], item["session_id"]))
        return candidates, unsealed, truncated

    @staticmethod
    def _result(*, status: str, observed_at: str, error_code: str | None = None,
                root_basis: str | None = None, game_running: bool | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": SCHEMA,
            "status": status,
            "observed_at": observed_at,
            "requires_cloud_account": False,
        }
        if error_code:
            result["error_code"] = error_code
        if root_basis:
            result["root_basis"] = root_basis
        if game_running is not None:
            result["game_running"] = game_running
        return result
