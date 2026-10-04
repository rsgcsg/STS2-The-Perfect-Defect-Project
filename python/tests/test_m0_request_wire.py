"""Portable malformed-frame checks with small stand-ins for the fixed byte caps."""

import hashlib
import json
import struct
import zlib

import pytest

from spireagent.json_boundary import BoundaryError
from stpd.cloud_jobs import m0_request_wire as wire


def _frame(raw: bytes, *, size: int | None = None, sha256: bytes | None = None) -> bytes:
    return (wire.REQUEST_WIRE_MAGIC
            + struct.pack(">Q32s", len(raw) if size is None else size,
                          hashlib.sha256(raw).digest() if sha256 is None else sha256)
            + zlib.compress(raw))


def test_legacy_bytes_and_semantic_hash_are_unchanged() -> None:
    raw = b'{"schema":"legacy-canonical-request"}'
    assert wire.encode_m0_request(raw) is raw
    assert wire.decode_m0_request(raw) is raw
    framed = wire.encode_m0_request(raw, compress=True)
    assert framed == wire.encode_m0_request(raw, compress=True)
    assert wire.decode_m0_request(framed) == raw
    assert hashlib.sha256(framed).digest() != hashlib.sha256(raw).digest()
    assert wire.MAX_M0_REQUEST_BYTES == 256 * 1024 * 1024
    assert wire.MAX_M0_LOGICAL_REQUEST_BYTES == 1024 * 1024 * 1024


def test_oversized_logical_request_uses_frame_without_raising_wire_cap(monkeypatch) -> None:
    monkeypatch.setattr(wire, "MAX_M0_REQUEST_BYTES", 128)
    monkeypatch.setattr(wire, "MAX_M0_LOGICAL_REQUEST_BYTES", 1024)
    raw = b"x" * 1024
    framed = wire.encode_m0_request(raw)
    assert len(raw) > 128 >= len(framed)
    assert wire.decode_m0_request(framed) == raw
    with pytest.raises(BoundaryError, match="logical_request_size_limit"):
        wire.encode_m0_request(raw + b"x")
    with pytest.raises(BoundaryError, match="request_size_limit"):
        wire.encode_m0_request(bytes(range(256)))


@pytest.mark.parametrize("size", [0, 1025, 2**64 - 1])
def test_declared_size_bound_is_checked_before_inflation(monkeypatch, size) -> None:
    monkeypatch.setattr(wire, "MAX_M0_LOGICAL_REQUEST_BYTES", 1024)
    with pytest.raises(BoundaryError, match="logical_request_size_limit"):
        wire.decode_m0_request(_frame(b"x", size=size))


def test_understated_length_bomb_is_not_fully_inflated(monkeypatch) -> None:
    real_inflater = zlib.decompressobj
    limits = []

    class ObservedInflater:
        def __init__(self):
            self.inner = real_inflater()

        def decompress(self, raw, maximum):
            limits.append(maximum)
            return self.inner.decompress(raw, maximum)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(wire.zlib, "decompressobj", ObservedInflater)
    with pytest.raises(BoundaryError, match="request_length_mismatch"):
        wire.decode_m0_request(_frame(b"x" * 1_000_000, size=4))
    assert limits == [5]


@pytest.mark.parametrize("change,code", [
    (lambda raw: raw[:-1], "truncated_request_frame"),
    (lambda raw: raw + b"tail", "trailing_request_data"),
    (lambda raw: raw + zlib.compress(b"second stream"), "trailing_request_data"),
    (lambda raw: raw[:len(wire.REQUEST_WIRE_MAGIC) + 40], "truncated_request_frame"),
    (lambda raw: raw[:len(wire.REQUEST_WIRE_MAGIC) + 40] + b"bad", "invalid_request_compression"),
])
def test_incomplete_corrupt_and_trailing_streams_fail_closed(change, code) -> None:
    with pytest.raises(BoundaryError, match=code):
        wire.decode_m0_request(change(_frame(b"logical bytes")))


def test_length_digest_and_wire_bounds_are_independent(monkeypatch) -> None:
    with pytest.raises(BoundaryError, match="request_length_mismatch"):
        wire.decode_m0_request(_frame(b"bytes", size=6))
    with pytest.raises(BoundaryError, match="request_digest_mismatch"):
        wire.decode_m0_request(_frame(b"bytes", sha256=b"\x00" * 32))
    monkeypatch.setattr(wire, "MAX_M0_REQUEST_BYTES", 64)
    with pytest.raises(BoundaryError, match="request_size_limit"):
        wire.decode_m0_request(_frame(bytes(range(256))))


def test_unsupported_frame_version_fails_closed() -> None:
    framed = _frame(b"bytes")
    prefix = wire.REQUEST_WIRE_MAGIC[:-1]
    with pytest.raises(BoundaryError, match="unsupported_request_wire_version"):
        wire.decode_m0_request(prefix + b"\x02" + framed[len(wire.REQUEST_WIRE_MAGIC):])


def test_owner_stores_logical_bytes_under_legacy_hash_with_separate_bound(
    tmp_path, monkeypatch,
) -> None:
    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import ManifestArtifactStore
    from spireagent.workbench import local_training as owner

    monkeypatch.setattr(wire, "MAX_M0_REQUEST_BYTES", 128)
    monkeypatch.setattr(wire, "MAX_M0_LOGICAL_REQUEST_BYTES", 1024)
    monkeypatch.setattr(owner, "REMOTE_MAX_REQUEST_BYTES", 128)
    monkeypatch.setattr(owner, "REMOTE_MAX_LOGICAL_REQUEST_BYTES", 1024)
    monkeypatch.setattr(owner, "REMOTE_CHUNK_BYTES", 32)
    logical = b"x" * 1024
    framed = wire.encode_m0_request(logical)
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    reference, sha256 = owner._persist_remote_request(store, framed)
    assert sha256 == hashlib.sha256(logical).hexdigest()
    assert reference == owner.REMOTE_REQUEST_PREFIX + sha256
    index = json.loads(store.blobs.get(reference))
    assert index["schema"] == owner.REMOTE_REQUEST_WIRE_INDEX_SCHEMA
    assert index["wire_size"] == len(framed)
    assert index["size"] == len(logical)
    assert index["wire_sha256"] == hashlib.sha256(framed).hexdigest()
    assert all(item["size"] <= 32 for item in index["chunks"])
    assert b"".join(
        store.blobs.get(owner.REMOTE_REQUEST_CHUNK_PREFIX + item["sha256"])
        for item in index["chunks"]
    ) == framed
    assert sum(item["size"] for item in index["chunks"]) < len(logical)
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store", create=False))
    assert owner._read_remote_blob(
        store, reference, sha256, object_prefix=owner.REMOTE_REQUEST_PREFIX,
        chunk_prefix=owner.REMOTE_REQUEST_CHUNK_PREFIX,
        maximum=owner.REMOTE_MAX_LOGICAL_REQUEST_BYTES, label="remote_request",
    ) == logical
    with pytest.raises(BoundaryError, match="remote_request_size_limit"):
        owner._persist_remote_request(store, logical)


@pytest.mark.parametrize("legacy_first", [True, False])
def test_request_storage_reuses_original_representation_without_rewriting_refs(
    tmp_path, legacy_first,
) -> None:
    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import ManifestArtifactStore
    from spireagent.workbench import local_training as owner

    logical = b"legacy exact bytes"
    framed = wire.encode_m0_request(logical, compress=True)
    first, second = (logical, framed) if legacy_first else (framed, logical)
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    reference, sha256 = owner._persist_remote_request(store, first)
    original = store.blobs.get(reference)
    assert owner._persist_remote_request(store, second) == (reference, sha256)
    assert store.blobs.get(reference) == original
    index = json.loads(original)
    assert (index["schema"] == "stpd/local-training-remote-evidence-index-v1") == legacy_first


@pytest.mark.parametrize("damage", [
    "wire_digest", "logical_digest", "logical_length", "wire_length",
    "unknown_field", "chunk_bytes", "frame_digest", "frame_trailing",
])
def test_compressed_request_storage_rechecks_all_bytes_after_restart(tmp_path, damage) -> None:
    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import ManifestArtifactStore
    from spireagent.workbench import local_training as owner

    logical = b"compressible logical request " * 100
    framed = wire.encode_m0_request(logical, compress=True)
    store_dir = tmp_path / "store"
    store = ManifestArtifactStore(LocalBlobStore(store_dir))
    reference, sha256 = owner._persist_remote_request(store, framed)
    index = json.loads(store.blobs.get(reference))
    if damage == "wire_digest":
        index["wire_sha256"] = "0" * 64
    elif damage == "logical_digest":
        index["sha256"] = "0" * 64
    elif damage == "logical_length":
        index["size"] += 1
    elif damage == "wire_length":
        index["wire_size"] += 1
    elif damage == "unknown_field":
        index["unknown"] = True
    else:
        if damage == "frame_digest":
            offset = len(wire.REQUEST_WIRE_MAGIC) + 8
            framed = framed[:offset] + b"\x00" * 32 + framed[offset + 32:]
        elif damage == "frame_trailing":
            framed += b"trailing"
        elif damage == "chunk_bytes":
            key = owner.REMOTE_REQUEST_CHUNK_PREFIX + index["chunks"][0]["sha256"]
            (store_dir / key).write_bytes(b"corrupt")
        if damage.startswith("frame_"):
            chunk_sha = hashlib.sha256(framed).hexdigest()
            store.blobs.put_if_absent(owner.REMOTE_REQUEST_CHUNK_PREFIX + chunk_sha, framed)
            index["wire_sha256"] = chunk_sha
            index["wire_size"] = len(framed)
            index["chunks"] = [{"sha256": chunk_sha, "size": len(framed)}]
    # Deliberate test-only disk corruption; production writes remain immutable.
    (store_dir / reference).write_bytes(json.dumps(index).encode())
    replacement = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
    with pytest.raises(BoundaryError):
        owner._read_remote_blob(
            replacement, reference, sha256, object_prefix=owner.REMOTE_REQUEST_PREFIX,
            chunk_prefix=owner.REMOTE_REQUEST_CHUNK_PREFIX,
            maximum=owner.REMOTE_MAX_LOGICAL_REQUEST_BYTES, label="remote_request",
        )


def test_compressed_storage_index_is_never_accepted_as_generic_result(tmp_path) -> None:
    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import ManifestArtifactStore
    from spireagent.workbench import local_training as owner

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    reference, sha256 = owner._persist_remote_request(
        store, wire.encode_m0_request(b"logical", compress=True),
    )
    result_reference = owner.REMOTE_RESULT_PREFIX + sha256
    store.blobs.put_if_absent(result_reference, store.blobs.get(reference))
    with pytest.raises(BoundaryError, match="remote_result_unavailable"):
        owner._read_remote_blob(
            store, result_reference, sha256, object_prefix=owner.REMOTE_RESULT_PREFIX,
            chunk_prefix=owner.REMOTE_RESULT_CHUNK_PREFIX,
            maximum=owner.REMOTE_MAX_RESULT_BYTES, label="remote_result",
        )
