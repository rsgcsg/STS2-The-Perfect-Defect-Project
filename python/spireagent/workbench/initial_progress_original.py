"""Owned native launch provenance; save bytes never enter P1 or research input."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.policy_files import bound_path

SCHEMA = "spireagent/native-initial-progress-original-v1"
MAX_BYTES = 16 * 1024**2


def capture_owned_progress(
    identity: dict[str, Any], state_root: Path, *, generation: str, seed: str
) -> dict[str, Any]:
    """Snapshot the actual isolated progress.save after reset, before handoff.

    An unavailable file remains explicitly unknown without changing the launch.
    The copy is one immutable resource-generation original, never a backfill.
    """
    from .native_episode_binding import identifier

    identifier(generation)
    baseline = identity.get("baseline") or {}
    profile = identity.get("profile") or {}
    metadata: dict[str, Any] = {
        "schema": SCHEMA,
        "source": "native_progress_file",
        "status": "unknown",
        "runtime_instance_id": identity["host"]["runtime_instance_id"],
        "profile_generation_id": profile.get("generation_id"),
        "native_profile_id": baseline.get("native_profile_id"),
        "seed": seed,
        "captured_at": datetime.now(UTC).isoformat(),
        "bytes": None,
        "sha256": None,
        "reason": "native_progress_binding_unavailable",
    }
    destination = state_root / "initial-progress-originals" / generation
    destination.mkdir(parents=True, exist_ok=False)
    try:
        owner = Path(profile["profile_root"])
        marker = json.loads(
            bound_path(
                state_root, owner / "headless-profile.json", stage="initial_progress_original"
            ).read_bytes()
        )
        if (
            owner.parent != state_root / "profiles"
            or owner.resolve() != owner
            or marker.get("generation_id") != profile.get("generation_id")
            or marker.get("profile_id") != profile.get("profile_id")
            or baseline.get("ready") is not True
            or baseline.get("runtime_instance_id") != metadata["runtime_instance_id"]
            or baseline.get("profile_generation_id") != profile.get("generation_id")
            or type(baseline.get("native_profile_id")) is not int
            or baseline["native_profile_id"] < 1
        ):
            raise BoundaryError("initial_progress_original", "owned_profile_unproven")
        # Exact pinned ProgressSaveManager.GetProgressPathForProfile uses the
        # returned native profile directory, SavesDir='saves', then progress.save.
        source = bound_path(
            owner,
            Path(baseline["native_profile_path"]) / "saves" / "progress.save",
            stage="initial_progress_original",
        )
        if not source.is_file() or not 0 < source.stat().st_size <= MAX_BYTES:
            raise BoundaryError("initial_progress_original", "native_progress_file_unavailable")
        with source.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if not 0 < len(raw) <= MAX_BYTES:
            raise BoundaryError("initial_progress_original", "native_progress_capacity")
        # Copy bytes literally; parsing and rewriting would lose original identity.
        with (destination / "initial-progress.bin").open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        metadata.update(
            status="captured", bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), reason=None
        )
    except (OSError, ValueError, KeyError, TypeError, BoundaryError):
        # No fallback to normalized baseline.progress_json or a personal profile.
        metadata["reason"] = "native_progress_file_or_owned_binding_unavailable"
    path = destination / "original.json"
    raw_metadata = json_bytes(metadata)
    with path.open("xb") as stream:
        stream.write(raw_metadata)
        stream.flush()
        os.fsync(stream.fileno())
    return {
        "path": str(path),
        "bytes": len(raw_metadata),
        "sha256": hashlib.sha256(raw_metadata).hexdigest(),
        "status": metadata["status"],
    }
