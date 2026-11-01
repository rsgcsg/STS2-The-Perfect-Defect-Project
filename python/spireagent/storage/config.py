"""Application storage selection; no UI, model, or vendor-specific callers."""
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..json_boundary import BoundaryError
from .local import LocalBlobStore
from .s3 import S3BlobStore, S3Config
from .store import ManifestArtifactStore


def open_store(location: str) -> ManifestArtifactStore:
    """'s3' selects environment configuration; every other value is a local directory."""
    if location == "s3":
        bucket = os.environ.get("STPD_S3_BUCKET")
        if not bucket:
            raise BoundaryError("configuration", "missing_s3_bucket")
        config = S3Config(
            bucket=bucket,
            endpoint=os.environ.get("STPD_S3_ENDPOINT") or None,
            prefix=os.environ.get("STPD_S3_PREFIX", "stpd"),
            region=os.environ.get("STPD_S3_REGION", "us-east-1"),
        )
        return ManifestArtifactStore(S3BlobStore(config))
    return ManifestArtifactStore(LocalBlobStore(Path(location)))


def peer_binding(location: str) -> dict[str, Any]:
    """Pin a non-secret location once; recovery must not follow environment drift."""
    store = open_store(location)
    if isinstance(store.blobs, S3BlobStore):
        return {"kind": "s3", **asdict(store.blobs.config)}
    return {"kind": "local", "directory": str(Path(location).resolve())}


def validate_peer(binding: dict[str, Any]) -> None:
    """Validate nonsecret transport configuration without constructing a client."""
    if set(binding) == {"kind", "directory", "archive"} and binding["kind"] == "local":
        root = Path(binding["directory"])
        archive = binding["archive"]
        if (not root.is_absolute() or root.is_symlink() or root.resolve() != root
                or not isinstance(archive, dict) or "archive" in archive
                or archive.get("kind") == "local" and Path(archive["directory"]).resolve() == root):
            raise BoundaryError("p1_storage", "distinct_frozen_shared_archive_required")
        validate_peer(archive)
        return
    if set(binding) == {"kind", "directory"} and binding["kind"] == "local":
        Path(binding["directory"])
        return
    if (set(binding) == {"kind", "bucket", "endpoint", "prefix", "region"}
            and binding["kind"] == "s3"):
        S3Config(**{key: value for key, value in binding.items() if key != "kind"})
        return
    raise BoundaryError("p1_storage", "invalid_peer_binding")


def open_peer(binding: dict[str, Any]) -> ManifestArtifactStore:
    validate_peer(binding)
    if binding["kind"] == "local":
        root = Path(binding["directory"])
        if "archive" in binding:
            from .async_archive import ArchivedStore

            return ArchivedStore(root, binding["archive"])
        return ManifestArtifactStore(LocalBlobStore(root))
    return ManifestArtifactStore(S3BlobStore(S3Config(**{
        key: value for key, value in binding.items() if key != "kind"})))
