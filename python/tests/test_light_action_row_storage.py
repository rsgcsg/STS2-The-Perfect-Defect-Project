"""Bounded row storage and exact source recompilation, using synthetic bytes only."""

from __future__ import annotations

import hashlib
import io
import struct
import zlib
from dataclasses import replace

import pytest

from spireagent.json_boundary import BoundaryError, FrozenObject
from stpd.fullrun import light_action_row_storage as codec


def _frame(raw: bytes, *, size: int | None = None, sha: bytes | None = None) -> bytes:
    return (codec.MAGIC + struct.pack(">Q32s", len(raw) if size is None else size,
                                      hashlib.sha256(raw).digest() if sha is None else sha)
            + zlib.compress(raw))


def _identity(raw: bytes) -> dict:
    return {"schema": codec.STORAGE_SCHEMA, "encoding": "zlib",
            "logical_size": len(raw), "logical_sha256": hashlib.sha256(raw).hexdigest()}


def _decode(frame: bytes, identity: dict, *, stride: int = 7) -> list[bytes]:
    return list(codec.decoded_rows(
        (frame[start:start + stride] for start in range(0, len(frame), stride)),
        stored_size=len(frame), media_type=codec.COMPRESSED_MEDIA_TYPE, identity=identity,
    ))


def test_legacy_bytes_and_compressed_logical_identity_with_scaled_caps(monkeypatch) -> None:
    raw = b'{"actions":[[1,2]],"state":[1]}\n'
    with codec.prepare_rows((raw,)) as (stream, identity, media_type):
        assert identity is None and media_type == codec.LEGACY_MEDIA_TYPE
        assert stream.read() == raw
    monkeypatch.setattr(codec, "MAX_STORED_BYTES", 128)
    monkeypatch.setattr(codec, "MAX_LOGICAL_BYTES", 4096)
    monkeypatch.setattr(codec, "STREAM_BYTES", 64)
    logical = b"x" * 4096
    with codec.prepare_rows((logical[:1234], logical[1234:])) as (stream, identity, media):
        frame = stream.read()
        assert len(frame) <= 128 < len(logical)
        assert media == codec.COMPRESSED_MEDIA_TYPE
        assert identity == _identity(logical)
        chunks = _decode(frame, identity)
        assert all(len(chunk) <= 64 for chunk in chunks)
        assert b"".join(chunks) == logical
    with (pytest.raises(BoundaryError, match="logical_size_limit"),
          codec.prepare_rows((logical, b"x"))):
        pass
    with (pytest.raises(BoundaryError, match="stored_size_limit"),
          codec.prepare_rows((bytes(range(256)),))):
        pass


@pytest.mark.parametrize("stride", [1, 7, 100_000])
def test_streamed_inflation_matches_across_input_and_output_boundaries(monkeypatch, stride) -> None:
    monkeypatch.setattr(codec, "STREAM_BYTES", 1024)
    for size in (1023, 1024, 1025, 4096, 4097):
        raw = b"x" * size
        codec.require_equal_rows(_decode(_frame(raw), _identity(raw), stride=stride), (raw,))


@pytest.mark.parametrize("damage,code", [
    (lambda frame: frame[:-1], "truncated_rows_frame"),
    (lambda frame: frame + b"tail", "trailing_rows_data"),
    (lambda frame: frame + zlib.compress(b"second stream"), "trailing_rows_data"),
    (lambda frame: frame[:len(codec.MAGIC) + 40], "truncated_rows_frame"),
    (lambda frame: frame[:len(codec.MAGIC) + 40] + b"bad", "invalid_rows_compression"),
    (lambda frame: b"unknown" + frame[7:], "unsupported_rows_frame"),
])
def test_corrupt_incomplete_and_trailing_frames_fail_closed(damage, code) -> None:
    raw = b"logical rows" * 100
    with pytest.raises(BoundaryError, match=code):
        _decode(damage(_frame(raw)), _identity(raw), stride=100_000)


def test_declared_bomb_bound_is_enforced_before_inflation(monkeypatch) -> None:
    monkeypatch.setattr(codec, "MAX_LOGICAL_BYTES", 1024)
    identity = {**_identity(b"x"), "logical_size": 1025}
    monkeypatch.setattr(codec.zlib, "decompressobj", lambda: pytest.fail("must reject first"))
    with pytest.raises(BoundaryError, match="logical_size_limit"):
        _decode(_frame(b"x", size=1025), identity)


def test_understated_bomb_and_digest_are_detected_with_bounded_output(monkeypatch) -> None:
    monkeypatch.setattr(codec, "STREAM_BYTES", 64)
    raw = b"x" * 1_000_000
    identity = {**_identity(raw), "logical_size": 4}
    with pytest.raises(BoundaryError, match="logical_length_mismatch"):
        _decode(_frame(raw, size=4), identity, stride=100_000)
    raw = b"logical"
    identity = {**_identity(raw), "logical_sha256": "0" * 64}
    with pytest.raises(BoundaryError, match="logical_digest_mismatch"):
        _decode(_frame(raw, sha=b"\x00" * 32), identity)
    with pytest.raises(BoundaryError, match="rows_header_identity_mismatch"):
        _decode(_frame(raw), identity)


def test_storage_metadata_and_stored_bounds_are_independent(monkeypatch) -> None:
    raw = b"logical"
    frame = _frame(raw)
    with pytest.raises(BoundaryError, match="rows_storage_metadata_required"):
        list(codec.decoded_rows((frame,), stored_size=len(frame),
                                media_type=codec.COMPRESSED_MEDIA_TYPE, identity=None))
    with pytest.raises(BoundaryError, match="stored_length_mismatch"):
        list(codec.decoded_rows((raw,), stored_size=len(raw) + 1,
                                media_type=codec.LEGACY_MEDIA_TYPE, identity=None))
    monkeypatch.setattr(codec, "MAX_STORED_BYTES", 32)
    with pytest.raises(BoundaryError, match="stored_size_limit"):
        _decode(frame, _identity(raw))


def _compressed_inputs(tmp_path, monkeypatch):
    from test_light_action_m0 import PRODUCER, _inputs

    from stpd.fullrun.light_action_inputs import (
        load_light_action_inputs,
        publish_light_action_inputs,
    )

    archive, view, legacy = _inputs(tmp_path)
    raw = b"".join(archive.read_payload(legacy.manifest.payload("rows")))
    # Faithful capacity regression without allocating 350 MiB synthetic rows.
    threshold = (len(raw) + len(_frame(raw))) // 2
    assert len(_frame(raw)) < threshold < len(raw)
    with monkeypatch.context() as context:
        context.setattr(codec, "MAX_STORED_BYTES", threshold)
        compressed = publish_light_action_inputs(archive, view.artifact_id, "s", PRODUCER)
    loaded = load_light_action_inputs(archive, compressed.artifact_id)
    return archive, legacy, loaded, raw


def test_new_compressed_input_and_legacy_input_recompile_identically(tmp_path, monkeypatch) -> None:
    from stpd.fullrun.light_action_inputs import load_light_action_inputs

    archive, legacy, compressed, raw = _compressed_inputs(tmp_path, monkeypatch)
    info = compressed.manifest.parameters.value()
    assert info["rows_storage"] == _identity(raw)
    assert info["schema"] == legacy.manifest.parameters.value()["schema"]
    assert compressed.samples == legacy.samples and compressed.rows == legacy.rows
    assert len(compressed.rows) == len(legacy.rows)
    assert compressed.manifest.payload("rows").size < len(raw)
    again = load_light_action_inputs(archive, legacy.manifest.artifact_id)
    assert again.manifest == legacy.manifest and again.rows == legacy.rows
    assert again.state_tokenizer.to_str() == legacy.state_tokenizer.to_str()


@pytest.mark.parametrize("mutation", ["missing_metadata", "digest", "size", "extra", "projection"])
def test_compressed_inputs_cannot_replace_exact_source_recompilation(
    tmp_path, monkeypatch, mutation,
) -> None:
    from stpd.fullrun.light_action_inputs import load_light_action_inputs

    archive, _, compressed, raw = _compressed_inputs(tmp_path, monkeypatch)
    info = compressed.manifest.parameters.value()
    payloads = compressed.manifest.payloads
    if mutation == "missing_metadata":
        info.pop("rows_storage")
    elif mutation == "digest":
        info["rows_storage"]["logical_sha256"] = "0" * 64
    elif mutation == "size":
        info["rows_storage"]["logical_size"] += 1
    elif mutation == "extra":
        info["rows_storage"]["unknown"] = True
    else:
        # Valid compressed bytes + matching hashes still cannot invent a row.
        altered = raw.replace(b'"state":[', b'"state":[0,', 1)
        assert altered != raw
        info["rows_storage"] = _identity(altered)
        payload = archive.put_payload("rows", io.BytesIO(_frame(altered)),
                                      codec.COMPRESSED_MEDIA_TYPE)
        payloads = tuple(payload if item.role == "rows" else item for item in payloads)
    forged = replace(compressed.manifest, payloads=payloads, parameters=FrozenObject.of(info))
    archive.publish(forged)
    with pytest.raises(BoundaryError):
        load_light_action_inputs(archive, forged.artifact_id)


def test_stream_comparison_retains_exact_canonical_bytes() -> None:
    codec.require_equal_rows((b"abc", b"def"), (b"a", b"bcde", b"f"))
    for altered in ((b"abcd",), (b"abcdef ",), (b"abcdeg",)):
        with pytest.raises(BoundaryError, match="source_projection_mismatch"):
            codec.require_equal_rows(altered, (b"abcdef",))
    assert codec.MAX_STORED_BYTES == 256 * 1024 * 1024
    assert codec.MAX_LOGICAL_BYTES == 1024 * 1024 * 1024


@pytest.mark.parametrize("family", ["public", "canonical"])
def test_compressed_rows_preserve_owner_bound_public_and_canonical_inputs(
    tmp_path, monkeypatch, family,
) -> None:
    from contextlib import contextmanager

    from stpd.fullrun import light_action_inputs

    prepare = codec.prepare_rows

    @contextmanager
    def compressed_rows(chunks):
        raw = b"".join(chunks)  # Small synthetic fixture only.
        threshold = (len(raw) + len(_frame(raw))) // 2
        with monkeypatch.context() as context:
            context.setattr(codec, "MAX_STORED_BYTES", threshold)
            with prepare((raw,)) as result:
                yield result

    monkeypatch.setattr(light_action_inputs, "prepare_rows", compressed_rows)
    if family == "public":
        from test_local_m0_remote import _case

        case = _case(tmp_path, monkeypatch, steps=1)
        torch, threads, _service, store, _owner, _producer, run, *_rest = case
        identity = run.parent("training_input")
    else:
        from test_token_remote_update import _canonical_run

        case = _canonical_run(tmp_path, monkeypatch)
        torch, threads, store, _owner, _operation, _producer, inputs, *_rest = case
        identity = inputs.manifest.artifact_id
    try:
        loaded = light_action_inputs.load_light_action_inputs(store, identity)
        info = loaded.manifest.parameters.value()
        assert info["rows_storage"]["schema"] == codec.STORAGE_SCHEMA
        assert info["schema"] == (light_action_inputs.PUBLIC_SCHEMA if family == "public"
                                  else light_action_inputs.CANONICAL_SCHEMA)
        assert info["training_binding"]["training_operation_id"]
        assert {sample.split for sample in loaded.samples} == {"train", "dev"}
        assert len(loaded.rows) == len(loaded.samples)
    finally:
        torch.set_num_threads(threads)
