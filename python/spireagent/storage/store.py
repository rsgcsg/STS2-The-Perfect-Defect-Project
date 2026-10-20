"""Manifest-last publication and verified streaming over bounded immutable chunks."""

from __future__ import annotations

import builtins
import hashlib
import io
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from spireagent.artifact_contracts import Manifest, Payload
from spireagent.json_boundary import array, decode_json, digest, json_bytes, object_fields, unsigned
from spireagent.storage.blobs import BlobStore, StoreError

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
        raw = self.blobs.get(f"payload-indexes/v1/{payload.sha256}.json")
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
        for position, item in enumerate(chunks):
            chunk = object_fields(item, {"sha256", "size"}, "payload.chunk")
            key = digest(chunk["sha256"], "payload.chunk")
            expected_size = unsigned(chunk["size"], "payload.chunk")
            required_size = min(CHUNK_BYTES, payload.size - position * CHUNK_BYTES)
            if expected_size != required_size:
                raise StoreError("invalid_chunk_size")
            data = self.blobs.get(f"objects/sha256/{key}")
            if len(data) != expected_size or hashlib.sha256(data).hexdigest() != key:
                raise StoreError("payload_chunk_integrity_failure")
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
        return Manifest.from_bytes(self.blobs.get(f"manifests/{artifact_id}.json"), artifact_id)

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
