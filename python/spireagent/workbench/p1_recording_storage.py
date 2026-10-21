"""P1 preservation/restore over the existing Store; locations contain no credentials."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sts2_platform_evidence import DirectoryTransferManifest

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, digest, json_bytes
from spireagent.storage.blobs import StoreError
from spireagent.storage.config import open_store
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.s3 import S3BlobStore, S3Config
from spireagent.storage.store import ManifestArtifactStore, copy_artifact

from .p1_recording_store import BUNDLE_SCHEMA, _transfer, _verify_archive


def peer_binding(location: str) -> dict[str, Any]:
    """Resolve once; reconciliation never silently targets a different environment bucket."""
    store = open_store(location)
    if isinstance(store.blobs, S3BlobStore):
        return {"kind": "s3", **asdict(store.blobs.config)}
    return {"kind": "local", "directory": str(Path(location).resolve())}


def open_peer(binding: dict[str, Any]) -> ManifestArtifactStore:
    if set(binding) == {"kind", "directory"} and binding["kind"] == "local":
        return ManifestArtifactStore(LocalBlobStore(Path(binding["directory"])))
    if (
        set(binding) == {"kind", "bucket", "endpoint", "prefix", "region"}
        and binding["kind"] == "s3"
    ):
        return ManifestArtifactStore(
            S3BlobStore(S3Config(**{key: value for key, value in binding.items() if key != "kind"}))
        )
    raise BoundaryError("p1_storage", "invalid_peer_binding")


def _inventory(
    store: ManifestArtifactStore, manifest: Manifest, scratch: Path
) -> DirectoryTransferManifest:
    info = manifest.parameters.value()
    if info.get("schema") == BUNDLE_SCHEMA:
        return _transfer(store, manifest)
    if info.get("format") != "p1-native-session-bundle-v1":
        raise BoundaryError("p1_storage", "p1_original_or_preservation_required")
    payload = manifest.payload("source-index")
    if payload.size > 16 * 1024**2:
        raise BoundaryError("p1_storage", "inventory_size_limit")
    with tempfile.TemporaryDirectory(prefix="p1-index-", dir=scratch) as name:
        path = store.materialize(payload, Path(name) / "index.json")
        inventory = DirectoryTransferManifest.read(path)
    if (
        inventory.content_id != info.get("bundle_content_id")
        or inventory.artifact_type != "p1-native-session-bundle-v1"
    ):
        raise BoundaryError("p1_storage", "inventory_identity_mismatch")
    return inventory


def require_workspace_space(
    scratch: Path,
    temporary_bytes: int,
    *,
    persistent_bytes: int = 0,
    destination: Path | None = None,
    reserve_bytes: int = 512 * 1024**2,
) -> dict[str, Any]:
    """Conservative operation estimate; actual write failures remain visible."""
    if min(temporary_bytes, persistent_bytes, reserve_bytes) < 0:
        raise BoundaryError("p1_storage", "invalid_reserve")
    scratch.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(scratch).free
    needed = temporary_bytes + persistent_bytes + reserve_bytes
    if destination is not None:
        destination.mkdir(parents=True, exist_ok=True)
        if destination.stat().st_dev != scratch.stat().st_dev:
            if shutil.disk_usage(destination).free < persistent_bytes + reserve_bytes:
                raise BoundaryError("p1_storage", "destination_space_insufficient")
            needed = temporary_bytes + reserve_bytes
    if free < needed:
        raise BoundaryError("p1_storage", "space_insufficient")
    return {
        "available_bytes": free,
        "scratch_budget_bytes": temporary_bytes,
        "persistent_budget_bytes": persistent_bytes,
        "reserve_bytes": reserve_bytes,
        "required_available_bytes": needed,
    }


def storage_budget(
    source: ManifestArtifactStore,
    artifact_id: str,
    *,
    scratch: Path,
    destination: Path | None,
    reserve_bytes: int = 512 * 1024**2,
) -> dict[str, Any]:
    """Budget the actual lineage closure, one copy temporary and compressed readback."""
    if reserve_bytes < 0:
        raise BoundaryError("p1_storage", "invalid_reserve")
    scratch.mkdir(parents=True, exist_ok=True)
    seen, payloads = set(), {}
    pending = [artifact_id]
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        seen.add(identity)
        manifest = source.get_manifest(identity)
        pending.extend(p.artifact_id for p in manifest.parents)
        for payload in manifest.payloads:
            payloads[payload.sha256] = payload.size
    scratch_bytes = max(payloads.values(), default=0)
    persistent_bytes = sum(payloads.values()) if destination is not None else 0
    return {
        **require_workspace_space(
            scratch,
            scratch_bytes,
            persistent_bytes=persistent_bytes,
            destination=destination,
            reserve_bytes=reserve_bytes,
        ),
        "lineage_manifests": len(seen),
    }


def verify_p1_storage(
    store: ManifestArtifactStore, artifact_id: str, *, scratch: Path
) -> dict[str, Any]:
    """Full payload and streamed archive-member readback, without expanding the raw tree."""
    scratch.mkdir(parents=True, exist_ok=True)
    manifest = store.get_manifest(artifact_id)
    inventory = _inventory(store, manifest, scratch)
    with tempfile.TemporaryDirectory(prefix="p1-readback-", dir=scratch) as name:
        archive = store.materialize(manifest.payload("archive"), Path(name) / "archive.tar.gz")
        _verify_archive(archive, inventory)
        scratch_actual = archive.stat().st_size
    for payload in manifest.payloads:
        if payload.role != "archive":
            for _ in store.read_payload(payload):
                pass
    return {
        "artifact_id": artifact_id,
        "content_id": inventory.content_id,
        "archive_sha256": manifest.payload("archive").sha256,
        "archive_bytes": manifest.payload("archive").size,
        "source_files": len(inventory.files),
        "source_bytes": sum(f.bytes for f in inventory.files),
        "full_readback": True,
        "scratch_actual_bytes": scratch_actual,
        "verified_at": datetime.now(UTC).isoformat(),
    }


def storage_intent(action: str, artifact_id: str, peer: dict[str, Any]) -> dict[str, Any]:
    if action not in {"preserve", "restore", "verify"}:
        raise BoundaryError("p1_storage", "invalid_action")
    digest(artifact_id, "p1_storage.artifact_id")
    # Validate before persisting arbitrary caller data (and never accept credential fields).
    open_peer(peer)
    return {
        "recording_type": "p1_native",
        "action": "storage",
        "storage_action": action,
        "artifact_id": artifact_id,
        "peer": peer,
        "status": "pending",
        "operation_id": str(uuid4()),
        "started_at": datetime.now(UTC).isoformat(),
    }


def _remote_locator(
    local: ManifestArtifactStore, peer: ManifestArtifactStore, identity: str
) -> tuple[str, Manifest | None]:
    try:
        peer.get_manifest(identity)
        return identity, None
    except StoreError as error:
        if error.code != "object_not_found":
            raise
    # Semantic manifests stay in the local catalog when expanded copies cool.
    # A's original preservation artifact can already locate the exact archive.
    original = local.get_manifest(identity)
    if original.parameters.value().get("schema") != BUNDLE_SCHEMA:
        raise BoundaryError("p1_storage", "p1_original_required")
    locator = original.parent("preservation")
    preserved = peer.get_manifest(locator)
    if preserved.payload("archive") != original.payload(
        "archive"
    ) or preserved.parameters.value().get("bundle_content_id") != original.parameters.value().get(
        "content_id"
    ):
        raise BoundaryError("p1_storage", "preservation_locator_mismatch")
    return locator, original


def storage_error_status(error: Exception) -> str:
    # These checks happen before the first copy/publication. Other failures need readback.
    return (
        "failed"
        if getattr(error, "code", None)
        in {
            "space_insufficient",
            "destination_space_insufficient",
            "invalid_reserve",
            "p1_original_or_preservation_required",
            "inventory_identity_mismatch",
            "preservation_locator_mismatch",
            "p1_original_required",
        }
        else "publication_unknown"
    )


def run_storage(
    local: ManifestArtifactStore,
    intent: dict[str, Any],
    *,
    scratch: Path,
    reserve_bytes: int = 512 * 1024**2,
) -> dict[str, Any]:
    scratch.mkdir(parents=True, exist_ok=True)
    peer = open_peer(intent["peer"])
    action, identity = intent["storage_action"], intent["artifact_id"]
    source, target = (peer, local) if action == "restore" else (local, peer)
    locator, original = (identity, None)
    if action in {"restore", "verify"}:
        locator, original = _remote_locator(local, peer, identity)
    if action == "verify":
        source = target = peer
    inventory = _inventory(source, source.get_manifest(locator), scratch)
    destination = (
        target.blobs.root
        if isinstance(target.blobs, LocalBlobStore) and action != "verify"
        else None
    )
    budget = storage_budget(
        source, locator, scratch=scratch, destination=destination, reserve_bytes=reserve_bytes
    )
    if action != "verify":
        copy_artifact(source, target, locator, scratch=scratch)
        if original is not None:
            transfer = local.put_bytes("transfer", json_bytes(inventory.to_dict()))
            if transfer != original.payload("transfer"):
                raise BoundaryError("p1_storage", "restored_transfer_mismatch")
            local.publish(original)
    checked_id = locator if action == "verify" else identity
    receipt = verify_p1_storage(target, checked_id, scratch=scratch)
    return {
        **intent,
        "status": "completed",
        "budget": budget,
        "locator_artifact_id": locator,
        "storage_receipt": receipt,
    }


def reconcile_storage(
    local: ManifestArtifactStore, intent: dict[str, Any], *, scratch: Path
) -> dict[str, Any]:
    """Explicit readback of the original destination; never repeats copy/upload."""
    peer = open_peer(intent["peer"])
    target = local if intent["storage_action"] == "restore" else peer
    identity = intent["artifact_id"]
    if intent["storage_action"] == "verify":
        identity, _ = _remote_locator(local, peer, identity)
    try:
        storage_budget(target, identity, scratch=scratch, destination=None)
        receipt = verify_p1_storage(target, identity, scratch=scratch)
    except StoreError as error:
        if error.code != "object_not_found":
            raise
        return {
            **intent,
            "status": "failed",
            "publication": "absent_or_incomplete",
            "error_code": error.code,
        }
    return {**intent, "status": "completed", "storage_receipt": receipt}
