"""Persistent input reads through the existing verified Store cache.

Writes and mutable slots stay on the original store. This never selects results,
grants data use, evicts files or turns existence into an integrity check.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

from spireagent.artifact_contracts import Manifest, Payload
from spireagent.json_boundary import BoundaryError

from .cache import configure_cache, read_policy
from .local import LocalBlobStore
from .s3 import S3BlobStore
from .store import BinarySource, ManifestArtifactStore


class InputCacheStore:
    def __init__(self, writer: ManifestArtifactStore, directory: Path) -> None:
        blobs = writer.blobs
        if isinstance(blobs, LocalBlobStore):
            peer = {"kind": "local", "directory": str(blobs.root.resolve())}
        elif isinstance(blobs, S3BlobStore):
            peer = {"kind": "s3", **asdict(blobs.config)}
        else:
            raise BoundaryError("input_cache", "supported_store_required")
        self.writer = writer
        self.blobs = writer.blobs  # Mutable completion/control/index slots stay remote.
        self.reader = ManifestArtifactStore(LocalBlobStore(directory), cache_manifests=True)
        policy = directory / "cache-policy.json"
        if policy.exists():
            if read_policy(policy)["peers"] != [peer]:
                raise BoundaryError("input_cache", "peer_binding_mismatch")
        else:
            configure_cache(self.reader, peer, max_bytes=8 * 1024**3)

    def get_manifest(self, identity: str) -> Manifest:
        return self.reader.get_manifest(identity)

    def read_payload(self, payload: Payload) -> Iterator[bytes]:
        yield from self.reader.read_payload(payload)

    def put_payload(self, role: str, source: BinarySource,
                    media_type: str = "application/octet-stream") -> Payload:
        return self.writer.put_payload(role, source, media_type)

    def publish(self, manifest: Manifest) -> str:
        return self.writer.publish(manifest)

    def manifest_ids(self) -> tuple[str, ...]:
        return self.writer.manifest_ids()
