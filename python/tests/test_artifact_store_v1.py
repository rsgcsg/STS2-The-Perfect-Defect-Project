from __future__ import annotations

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from spireagent.artifact_contracts import Manifest, Parent, Payload, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, json_bytes
from spireagent.storage.blobs import StoreError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import CHUNK_BYTES, ManifestArtifactStore, copy_artifact

PRODUCER = Producer("rsgcsg/STS2-The-Perfect-Defect", "a" * 40, "b" * 64)


def store(root: Path) -> ManifestArtifactStore:
    return ManifestArtifactStore(LocalBlobStore(root))


def test_frozen_object_copies_and_manifest_roundtrip() -> None:
    mutable = {"nested": [1, 2]}
    frozen = FrozenObject.of(mutable)
    mutable["nested"].append(3)
    exposed = frozen.value()
    exposed["nested"].append(9)
    assert frozen.value() == {"nested": [1, 2]}
    manifest = Manifest("dataset", PRODUCER, parameters=frozen)
    assert Manifest.from_bytes(manifest.to_bytes()) == manifest
    assert Manifest.from_bytes(manifest.to_bytes(), manifest.artifact_id) == manifest
    altered = decode_json(manifest.to_bytes())
    altered["parameters"]["nested"].append(4)
    with pytest.raises(BoundaryError, match="identity_mismatch"):
        Manifest.from_bytes(json_bytes(altered))


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b"\xff"])
def test_bad_json_is_rejected(raw: bytes) -> None:
    with pytest.raises(BoundaryError):
        decode_json(raw)


def test_manifest_rejects_missing_future_schema_noncanonical_and_bad_types() -> None:
    manifest = Manifest("dataset", PRODUCER)
    value = decode_json(manifest.to_bytes())
    value["schema"] = "future"
    with pytest.raises(BoundaryError, match="unsupported_schema"):
        Manifest.from_bytes(json_bytes(value))
    with pytest.raises(BoundaryError, match="non_canonical_bytes"):
        Manifest.from_bytes(manifest.to_bytes().rstrip(b"\n"))
    with pytest.raises(BoundaryError):
        Payload("data", "a" * 64, True)
    with pytest.raises(BoundaryError):
        Producer("repo", "main", "a" * 64)
    with pytest.raises(BoundaryError):
        Manifest("future_kind", PRODUCER)


@pytest.mark.parametrize(
    "parameters",
    [
        {"api_key": "must-not-persist"},
        {"nested": [{"authorization": "Bearer secret"}]},
        {"endpoint": "https://user:password@example.invalid/store"},
        {"endpoint": "https://example.invalid/store?access_token=secret"},
    ],
)
def test_manifest_rejects_credential_bearing_metadata(parameters: dict[str, object]) -> None:
    with pytest.raises(BoundaryError, match="secret_metadata|credentialed_url"):
        Manifest("dataset", PRODUCER, parameters=FrozenObject.of(parameters))


def test_manifest_rejects_duplicate_parent_roles() -> None:
    with pytest.raises(BoundaryError, match="duplicate_parent_role"):
        Manifest(
            "model",
            PRODUCER,
            parents=(Parent("dataset", "a" * 64), Parent("dataset", "b" * 64)),
        )


@pytest.mark.parametrize("key", ["../escape", "/absolute", "a\\b", "a/../b", "a//b", "con.json"])
def test_backend_rejects_unsafe_keys(tmp_path: Path, key: str) -> None:
    backend = LocalBlobStore(tmp_path)
    with pytest.raises(StoreError):
        backend.put_if_absent(key, b"x")


def test_atomic_idempotent_publish_and_concurrent_collision(tmp_path: Path) -> None:
    backend = LocalBlobStore(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(lambda _: backend.put_if_absent("objects/a", b"same"), range(16)))
    assert sum(created) == 1
    assert backend.get("objects/a") == b"same"
    with pytest.raises(StoreError, match="collision"):
        backend.put_if_absent("objects/a", b"different")
    assert not list(tmp_path.rglob(".pending-*"))


def test_manifest_last_and_corrupt_payload_rejection(tmp_path: Path) -> None:
    target = store(tmp_path)
    missing = Payload("data", "a" * 64, 7)
    manifest = Manifest("dataset", PRODUCER, payloads=(missing,))
    with pytest.raises(StoreError):
        target.publish(manifest)
    assert target.manifest_ids() == ()
    payload = target.put_bytes("data", b"payload")
    (tmp_path / "objects/sha256" / payload.sha256).write_bytes(b"corrupt")
    with pytest.raises(StoreError, match="integrity"):
        target.publish(Manifest("dataset", PRODUCER, payloads=(payload,)))
    assert target.manifest_ids() == ()


def test_chunked_streaming_transfer_lineage_and_materialize(tmp_path: Path) -> None:
    source, target = store(tmp_path / "source"), store(tmp_path / "target")
    raw = b"x" * CHUNK_BYTES + b"tail"
    payload = source.put_payload("data", io.BytesIO(raw))
    assert payload.sha256 == hashlib.sha256(raw).hexdigest()
    root = Manifest("dataset", PRODUCER, payloads=(payload,))
    source.publish(root)
    model = Manifest("model", PRODUCER, parents=(Parent("dataset", root.artifact_id),))
    source.publish(model)
    assert copy_artifact(source, target, model.artifact_id) == model.artifact_id
    assert copy_artifact(source, target, model.artifact_id) == model.artifact_id
    assert set(target.manifest_ids()) == {root.artifact_id, model.artifact_id}
    destination = tmp_path / "output.bin"
    target.materialize(payload, destination)
    assert destination.read_bytes() == raw
    target.materialize(payload, destination)
    destination.write_bytes(b"changed")
    with pytest.raises(StoreError, match="collision"):
        target.materialize(payload, destination)


def test_parent_must_exist_and_zero_length_payload_is_supported(tmp_path: Path) -> None:
    target = store(tmp_path)
    with pytest.raises(StoreError):
        target.publish(Manifest("model", PRODUCER, parents=(Parent("run", "a" * 64),)))
    empty = target.put_bytes("empty", b"")
    assert target.bytes(empty) == b""
    target.publish(Manifest("dataset", PRODUCER, payloads=(empty,)))


def test_tampered_index_cannot_claim_complete_bytes(tmp_path: Path) -> None:
    target = store(tmp_path)
    payload = target.put_bytes("data", b"abcdef")
    path = tmp_path / f"payload-indexes/v1/{payload.sha256}.json"
    value = decode_json(path.read_bytes())
    value["chunks"] = []
    path.write_bytes(json_bytes(value))
    with pytest.raises(StoreError, match="integrity"):
        target.bytes(payload)


def test_short_reads_cannot_change_the_chunk_index(tmp_path: Path) -> None:
    class ShortStream(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            return super().read(min(size, 17))

    target = store(tmp_path)
    value = b"content" * 100
    first = target.put_payload("data", ShortStream(value))
    second = target.put_payload("data", io.BytesIO(value))
    assert first == second
    assert target.bytes(second) == value


def test_mutable_manifest_collections_are_rejected() -> None:
    with pytest.raises(BoundaryError, match="mutable_or_untyped"):
        Manifest("dataset", PRODUCER, parents=[])


@pytest.mark.parametrize(
    "field",
    [
        "AWS_SECRET_ACCESS_KEY",
        "aws_access_key_id",
        "session_token",
        "apiKey",
        "X-Amz-Credential",
        "X-Amz-Signature",
    ],
)
def test_durable_credential_aliases_are_rejected(field: str) -> None:
    with pytest.raises(BoundaryError, match="secret_metadata"):
        Manifest("analysis", PRODUCER, parameters=FrozenObject.of({field: "fixture-secret"}))


def test_published_parent_reuse_keeps_new_original_and_empty_restore(tmp_path, monkeypatch):
    source, target = store(tmp_path / "source"), store(tmp_path / "peer")
    training = source.put_bytes("training", b"original training checkpoint")
    parent = Manifest("model", PRODUCER, payloads=(training,))
    source.publish(parent)
    copy_artifact(source, target, parent.artifact_id)
    original = source.put_bytes("archive", b"new sealed original")
    child = Manifest("dataset", PRODUCER, payloads=(original,),
                     parents=(Parent("model", parent.artifact_id),))
    source.publish(child)
    read = target.read_payload
    def bounded(payload):
        assert payload != training, "published parent must not re-stream training ancestry"
        yield from read(payload)
    monkeypatch.setattr(target, "read_payload", bounded)
    assert copy_artifact(source, target, child.artifact_id,
                         reuse_published_parents=True) == child.artifact_id
    assert target.bytes(original) == b"new sealed original"
    monkeypatch.setattr(target, "read_payload", read)
    assert target.bytes(training) == b"original training checkpoint"
    empty = store(tmp_path / "empty")
    copy_artifact(source, empty, child.artifact_id, reuse_published_parents=True)
    assert empty.bytes(training) == b"original training checkpoint"
