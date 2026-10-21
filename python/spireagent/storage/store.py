"""Manifest-last publication and verified streaming over bounded immutable chunks."""

from __future__ import annotations

import builtins
import hashlib
import io
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

from spireagent.artifact_contracts import Manifest, Payload
from spireagent.instance_lock import instance_lock
from spireagent.json_boundary import array, decode_json, digest, json_bytes, object_fields, unsigned
from spireagent.storage.blobs import BlobStore, StoreError
from spireagent.storage.local import LocalBlobStore

CHUNK_BYTES = 8 * 1024 * 1024
PAYLOAD_SCHEMA = "stpd/payload-index-v1"


class BinarySource(Protocol):
    """The only stream operation required; includes wrapped Windows temporary files."""

    def read(self, size: int = -1, /) -> bytes: ...


class ArtifactStore(Protocol):
    def put_payload(
        self, role: str, source: BinarySource, media_type: str = "application/octet-stream"
    ) -> Payload: ...

    def read_payload(self, payload: Payload) -> Iterator[bytes]: ...

    def publish(self, manifest: Manifest) -> str: ...

    def get_manifest(self, artifact_id: str) -> Manifest: ...

    def manifest_ids(self) -> tuple[str, ...]: ...


class ManifestArtifactStore:
    def __init__(self, blobs: BlobStore) -> None:
        self.blobs = blobs
        self._peer: ManifestArtifactStore | None = None
        self._cache_policy: dict[str, Any] | None = None
        if isinstance(blobs, LocalBlobStore):
            policy = blobs.root / "cache-policy.json"
            if policy.is_file():
                from spireagent.storage.cache import read_policy

                self._cache_policy = read_policy(policy)

    def cache_peer(self) -> ManifestArtifactStore | None:
        """The explicitly configured durable peer; no implicit provider fallback."""
        if self._cache_policy is not None and self._peer is None:
            from spireagent.storage.config import open_peer

            self._peer = open_peer(self._cache_policy["peer"])
        return self._peer

    def _cache(self, key: str, data: bytes) -> None:
        if isinstance(self.blobs, LocalBlobStore) and not self.blobs.readonly:
            self.blobs.put_if_absent(key, data)

    def _get(self, key: str) -> tuple[bytes, bool]:
        try:
            return self.blobs.get(key), False
        except StoreError as error:
            if error.code != "object_not_found":
                raise
            peer = self.cache_peer()
            if peer is None:
                raise
            return peer.blobs.get(key), True

    def put_payload(
        self, role: str, source: BinarySource, media_type: str = "application/octet-stream"
    ) -> Payload:
        whole = hashlib.sha256()
        chunks = []
        size = 0
        while True:
            buffered = bytearray()
            while len(buffered) < CHUNK_BYTES:
                part = source.read(CHUNK_BYTES - len(buffered))
                if not isinstance(part, bytes):
                    raise StoreError("payload_stream_must_be_binary")
                if len(part) > CHUNK_BYTES - len(buffered):
                    raise StoreError("payload_stream_violated_read_bound")
                if not part:
                    break
                buffered.extend(part)
            if not buffered:
                break
            data = bytes(buffered)
            whole.update(data)
            chunk_id = hashlib.sha256(data).hexdigest()
            self.blobs.put_if_absent(f"objects/sha256/{chunk_id}", data)
            chunks.append({"sha256": chunk_id, "size": len(data)})
            size += len(data)
        payload = Payload(role, whole.hexdigest(), size, media_type)
        index = {"schema": PAYLOAD_SCHEMA, "sha256": payload.sha256, "size": size, "chunks": chunks}
        self.blobs.put_if_absent(f"payload-indexes/v1/{payload.sha256}.json", json_bytes(index))
        return payload

    def read_payload(self, payload: Payload) -> Iterator[bytes]:
        # A lease covers the generator lifetime, including a caller paused at yield.
        # Only this payload is fenced; a remote miss never locks the whole Store.
        if isinstance(self.blobs, LocalBlobStore) and self._cache_policy is not None:
            lock = self.blobs.root / ".cache-leases" / (payload.sha256 + ".lock")
            if not lock.exists():
                if self.blobs.readonly:
                    raise StoreError("cache_read_lease_missing",
                                     "prepare cache leases with its writable Store owner")
                lock.parent.mkdir(exist_ok=True)
                lock.touch(exist_ok=True)
            with instance_lock(lock, create=False, shared=True):
                yield from self._read_payload(payload)
        else:
            yield from self._read_payload(payload)

    def _read_payload(self, payload: Payload) -> Iterator[bytes]:
        index_key = f"payload-indexes/v1/{payload.sha256}.json"
        raw, recovered_index = self._get(index_key)
        index = object_fields(decode_json(raw), {"schema", "sha256", "size", "chunks"}, "payload")
        if (
            index["schema"] != PAYLOAD_SCHEMA
            or index["sha256"] != payload.sha256
            or index["size"] != payload.size
            or raw != json_bytes(index)
        ):
            raise StoreError("payload_index_mismatch")
        whole = hashlib.sha256()
        size = 0
        unsigned(index["size"], "payload.index_size")
        chunks = array(index["chunks"], "payload.chunks")
        if len(chunks) != (payload.size + CHUNK_BYTES - 1) // CHUNK_BYTES:
            raise StoreError("payload_integrity_failure")
        if recovered_index:
            self._cache(index_key, raw)
        for position, item in enumerate(chunks):
            chunk = object_fields(item, {"sha256", "size"}, "payload.chunk")
            key = digest(chunk["sha256"], "payload.chunk")
            expected_size = unsigned(chunk["size"], "payload.chunk")
            required_size = min(CHUNK_BYTES, payload.size - position * CHUNK_BYTES)
            if expected_size != required_size:
                raise StoreError("invalid_chunk_size")
            chunk_key = f"objects/sha256/{key}"
            data, recovered = self._get(chunk_key)
            if len(data) != expected_size or hashlib.sha256(data).hexdigest() != key:
                raise StoreError("payload_chunk_integrity_failure")
            if recovered:
                self._cache(chunk_key, data)
            whole.update(data)
            size += len(data)
            if size > payload.size:
                raise StoreError("payload_size_overflow")
            yield data
        if size != payload.size or whole.hexdigest() != payload.sha256:
            raise StoreError("payload_integrity_failure")

    def bytes(self, payload: Payload, *, maximum: int = 16 * 1024 * 1024) -> bytes:
        if payload.size > maximum:
            raise StoreError("use_streaming_for_large_payload")
        return b"".join(self.read_payload(payload))

    def put_bytes(
        self, role: str, value: builtins.bytes, media_type: str = "application/json"
    ) -> Payload:
        return self.put_payload(role, io.BytesIO(value), media_type)

    def publish(self, manifest: Manifest) -> str:
        for parent in manifest.parents:
            self.get_manifest(parent.artifact_id)
        for payload in manifest.payloads:
            for _ in self.read_payload(payload):
                pass
        self.blobs.put_if_absent(f"manifests/{manifest.artifact_id}.json", manifest.to_bytes())
        return manifest.artifact_id

    def get_manifest(self, artifact_id: str) -> Manifest:
        digest(artifact_id, "artifact_id")
        key = f"manifests/{artifact_id}.json"
        raw, recovered = self._get(key)
        manifest = Manifest.from_bytes(raw, artifact_id)
        if recovered:
            self._cache(key, raw)
        return manifest

    def manifest_ids(self) -> tuple[str, ...]:
        identities = []
        for key in self.blobs.keys("manifests/"):
            if not key.endswith(".json"):
                raise StoreError("unexpected_manifest_key")
            identities.append(
                digest(key.removeprefix("manifests/").removesuffix(".json"), "manifest_key")
            )
        return tuple(sorted(identities))

    def materialize(self, payload: Payload, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".stpd-pending-", dir=destination.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                for chunk in self.read_payload(payload):
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.is_symlink():
                    raise StoreError("materialization_symlink") from None
                with destination.open("rb") as handle:
                    actual = hashlib.file_digest(handle, "sha256").hexdigest()
                if actual != payload.sha256 or destination.stat().st_size != payload.size:
                    raise StoreError("materialization_collision") from None
            return destination
        finally:
            temporary.unlink(missing_ok=True)


def copy_artifact(source: ArtifactStore, target: ArtifactStore, artifact_id: str, *,
                  scratch: Path | None = None) -> str:
    """Copy an exact lineage closure, publishing every manifest after its parents/bytes."""
    visited: set[str] = set()
    visiting: set[str] = set()
    pending: list[tuple[str, Manifest | None]] = [(artifact_id, None)]
    while pending:
        identity, manifest = pending.pop()
        if identity in visited:
            continue
        if manifest is None:
            if identity in visiting:
                raise StoreError("lineage_cycle")
            visiting.add(identity)
            manifest = source.get_manifest(identity)
            pending.append((identity, manifest))
            pending.extend((p.artifact_id, None) for p in reversed(manifest.parents))
            continue
        for payload in manifest.payloads:
            try:
                for _ in target.read_payload(payload):
                    pass
                continue
            except StoreError as error:
                if error.code != "object_not_found":
                    raise
            with tempfile.TemporaryFile("w+b", dir=scratch) as handle:
                for chunk in source.read_payload(payload):
                    handle.write(chunk)
                handle.seek(0)
                received = target.put_payload(payload.role, handle, payload.media_type)
            if received != payload:
                raise StoreError("transfer_identity_mismatch")
        if target.publish(manifest) != identity:
            raise StoreError("transfer_manifest_mismatch")
        visiting.remove(identity)
        visited.add(identity)
    return artifact_id
