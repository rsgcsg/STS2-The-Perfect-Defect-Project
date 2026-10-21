"""P1 preservation/restore over the existing Store; locations contain no credentials."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sts2_platform_evidence import DirectoryTransferManifest
from sts2_platform_evidence.collection_tool import CollectionTool

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, digest, json_bytes
from spireagent.storage.blobs import StoreError
from spireagent.storage.config import open_store
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.s3 import S3BlobStore, S3Config
from spireagent.storage.store import ManifestArtifactStore, copy_artifact

from .p1_recording_store import BUNDLE_SCHEMA, _transfer, _verify_archive

HOT_METADATA = frozenset({
    "recording-manifest.json", "session-close-receipt.json", "p1-native-close.json",
    "recording-owner.lock",
})


def _raw_hashes(directory: Path) -> dict[str, str]:
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise BoundaryError("p1_storage", "expanded_source_symlink")
        if path.is_file():
            with path.open("rb") as source:
                result[path.relative_to(directory).as_posix()] = hashlib.file_digest(
                    source, "sha256",
                ).hexdigest()
    return result


@contextmanager
def expanded_consumer(directory: Path) -> Iterator[None]:
    """Share the existing owner-lock primitive, outside immutable raw bytes."""
    from .instance_lock import instance_lock

    directory = directory.resolve()
    key = hashlib.sha256(str(directory).encode()).hexdigest()
    # A raw directory can itself be inside a sealed bundle. Keep control files
    # outside both the raw tree and its enclosing bundle/recordings root.
    with instance_lock(directory.parent.parent / ".p1-consumers" / (key + ".lock")):
        native = directory / "recording-owner.lock"
        if native.is_symlink():
            raise BoundaryError("p1_storage", "expanded_source_symlink")
        if native.exists():
            with instance_lock(native, create=False):
                yield
        else:
            yield


def expanded_storage(
    local: ManifestArtifactStore, intent: dict[str, Any], *, scratch: Path, tool: CollectionTool,
) -> dict[str, Any]:
    """Cool or restore a closed raw copy; the Store and immutable archives remain.

    The existing recording lease excludes the native owner and managed raw
    import consumers. All writes use the importer's original durable operation.
    """
    from .local_recordings import _closed_session
    from .p1_recording_store import materialize_p1_original

    identity = intent["artifact_id"]
    source = local.get_manifest(identity)
    info = source.parameters.value()
    directory = Path(intent["_expanded_directory"])
    if (source.kind != "evidence" or info.get("schema") != BUNDLE_SCHEMA
            or not directory.is_absolute() or directory.resolve() != directory
            or any(p.is_symlink() for p in (directory, *directory.parents))):
        raise BoundaryError("p1_storage", "expanded_source_required")
    observed = _closed_session(directory)
    if (observed is None or observed.get("recording_type") != "p1_native"
            or observed["session_id"] != info["session_id"]
            or observed["timeline_id"] != info["timeline_id"]):
        raise BoundaryError("p1_storage", "original_closed_source_required")
    hashes = info["raw_file_sha256"]
    for name, key in (("recording-manifest.json", "manifest_sha256"),
                      ("session-close-receipt.json", "close_sha256"),
                      ("p1-native-close.json", "p1_close_sha256")):
        if hashes.get(name) != observed[key]:
            raise BoundaryError("p1_storage", "original_closed_source_mismatch")
    action = intent["storage_action"]
    if action not in {"cool", "restore-expanded"}:
        raise BoundaryError("p1_storage", "expanded_action_required")
    peer = open_peer(intent["peer"])
    if (isinstance(local.blobs, LocalBlobStore) and isinstance(peer.blobs, LocalBlobStore)
            and local.blobs.root == peer.blobs.root):
        raise BoundaryError("p1_storage", "independent_preservation_peer_required")
    with expanded_consumer(directory):
        before = _raw_hashes(directory)
        if (not set(before) <= set(hashes)
                or any(hashes[name] != sha for name, sha in before.items())
                or action == "cool" and before != hashes):
            raise BoundaryError("p1_storage", "expanded_original_inventory_mismatch")
        # Both durable compressed local archive and independent peer are required.
        local_receipt = verify_p1_storage(local, identity, scratch=scratch)
        peer_receipt = verify_p1_storage(peer, identity, scratch=scratch)
        free_before = shutil.disk_usage(directory).free
        removed_bytes = removed_files = restored_files = 0
        with materialize_p1_original(peer, identity, scratch=scratch, tool=tool) as restored:
            raw = restored / "raw"
            if _raw_hashes(raw) != hashes:
                raise BoundaryError("p1_storage", "restored_raw_inventory_mismatch")
            if action == "cool":
                # Recheck the exact source after remote full readback and typed restore.
                if _raw_hashes(directory) != before:
                    raise BoundaryError("p1_storage", "expanded_source_changed")
                for name in sorted(hashes):
                    if name in HOT_METADATA:
                        continue
                    path = directory / name
                    removed_bytes += path.stat().st_size
                    path.unlink()
                    removed_files += 1
                for path in sorted(directory.rglob("*"), reverse=True):
                    if path.is_dir() and not any(path.iterdir()):
                        path.rmdir()
            else:
                missing = [name for name in hashes if name not in before]
                require_workspace_space(
                    directory, 0, persistent_bytes=sum((raw / n).stat().st_size for n in missing),
                )
                # Stage outside the immutable tree, on the same filesystem. A
                # failed write must not leave a partial original blocking recovery.
                with tempfile.TemporaryDirectory(
                    prefix="expanded-restore-", dir=directory.parent.parent / ".p1-consumers",
                ) as staging:
                    pending = Path(staging) / "payload"
                    for name in missing:
                        target = directory / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with pending.open("wb") as output, (raw / name).open("rb") as original:
                            shutil.copyfileobj(original, output, 1024**2)
                        with pending.open("rb") as content:
                            if hashlib.file_digest(content, "sha256").hexdigest() != hashes[name]:
                                raise BoundaryError("p1_storage", "expanded_restore_mismatch")
                        # link publishes a complete file atomically and refuses an
                        # existing destination, including a concurrently created one.
                        os.link(pending, target)
                        pending.unlink()
                        restored_files += 1
                if _raw_hashes(directory) != hashes:
                    raise BoundaryError("p1_storage", "expanded_restore_mismatch")
        receipt = {
            "artifact_id": identity, "content_id": info["content_id"],
            "full_readback": True, "typed_restore": True,
            "expanded_state": "cold" if action == "cool" else "hot",
            "removed_files": removed_files, "removed_bytes": removed_bytes,
            "restored_files": restored_files,
            "retained_metadata": sorted(HOT_METADATA & set(hashes)),
            "free_bytes_before": free_before,
            "free_bytes_after": shutil.disk_usage(directory).free,
            "local_archive": local_receipt, "peer_archive": peer_receipt,
        }
    return {**intent, "status": "completed", "storage_receipt": receipt}


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
    if action not in {"preserve", "restore", "verify", "cool", "restore-expanded"}:
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
    if intent["storage_action"] in {"cool", "restore-expanded"}:
        raise BoundaryError("p1_storage", "explicit_expanded_restore_required")
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
