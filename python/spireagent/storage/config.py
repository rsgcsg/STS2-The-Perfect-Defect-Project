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


def open_peer(binding: dict[str, Any]) -> ManifestArtifactStore:
    if set(binding) == {"kind", "directory"} and binding["kind"] == "local":
        return ManifestArtifactStore(LocalBlobStore(Path(binding["directory"])))
    if (set(binding) == {"kind", "bucket", "endpoint", "prefix", "region"}
            and binding["kind"] == "s3"):
        return ManifestArtifactStore(S3BlobStore(S3Config(**{
            key: value for key, value in binding.items() if key != "kind"})))
    raise BoundaryError("p1_storage", "invalid_peer_binding")
