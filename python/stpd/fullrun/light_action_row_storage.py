"""Bounded, lossless storage for canonical light-action input row bytes.

The payload is storage only. Its decoded bytes must still equal a complete
recompilation from the admitted source; it does not replace that validation.
"""

from __future__ import annotations

import hashlib
import itertools
import struct
import tempfile
import zlib
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any, BinaryIO

from spireagent.json_boundary import BoundaryError, digest, object_fields

MAX_STORED_BYTES = 256 * 1024 * 1024
MAX_LOGICAL_BYTES = 1024 * 1024 * 1024
STREAM_BYTES = 1024 * 1024
STORAGE_SCHEMA = "stpd/light-action-input-rows-storage-v1"
LEGACY_MEDIA_TYPE = "application/x-ndjson"
COMPRESSED_MEDIA_TYPE = "application/vnd.stpd.light-action-input-rows+zlib"
MAGIC = b"STPD-M0-INPUT-ROWS\x00\x01"
_HEADER = struct.Struct(">Q32s")


def storage_identity(value: object) -> dict[str, Any]:
    item = object_fields(value, {"schema", "encoding", "logical_size", "logical_sha256"},
                         "light_action_row_storage")
    if item["schema"] != STORAGE_SCHEMA or item["encoding"] != "zlib":
        raise BoundaryError("light_action_row_storage", "unsupported_storage_codec")
    if type(item["logical_size"]) is not int or not 0 < item["logical_size"] <= MAX_LOGICAL_BYTES:
        raise BoundaryError("light_action_row_storage", "logical_size_limit")
    digest(item["logical_sha256"], "light_action_row_storage.logical_sha256")
    return dict(item)


@contextmanager
def prepare_rows(
    chunks: Iterable[bytes],
) -> Iterator[tuple[BinaryIO, dict[str, Any] | None, str]]:
    """Spool canonical bytes, preserving legacy storage unless compression is needed."""
    with tempfile.SpooledTemporaryFile(max_size=8 * STREAM_BYTES, mode="w+b") as logical:
        whole = hashlib.sha256()
        size = 0
        for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise BoundaryError("light_action_row_storage", "binary_rows_required")
            size += len(chunk)
            if size > MAX_LOGICAL_BYTES:
                raise BoundaryError("light_action_row_storage", "logical_size_limit")
            whole.update(chunk)
            logical.write(chunk)
        if not size:
            raise BoundaryError("light_action_row_storage", "empty_rows")
        logical.seek(0)
        if size <= MAX_STORED_BYTES:
            yield logical, None, LEGACY_MEDIA_TYPE
            return
        identity = {
            "schema": STORAGE_SCHEMA, "encoding": "zlib",
            "logical_size": size, "logical_sha256": whole.hexdigest(),
        }
        with tempfile.SpooledTemporaryFile(max_size=8 * STREAM_BYTES, mode="w+b") as encoded:
            encoded.write(MAGIC + _HEADER.pack(size, whole.digest()))
            compressor = zlib.compressobj(level=6)
            while chunk := logical.read(STREAM_BYTES):
                encoded.write(compressor.compress(chunk))
                if encoded.tell() > MAX_STORED_BYTES:
                    raise BoundaryError("light_action_row_storage", "stored_size_limit")
            encoded.write(compressor.flush())
            if encoded.tell() > MAX_STORED_BYTES:
                raise BoundaryError("light_action_row_storage", "stored_size_limit")
            encoded.seek(0)
            yield encoded, identity, COMPRESSED_MEDIA_TYPE


def _bounded_chunks(chunks: Iterable[bytes], stored_size: int) -> Iterator[bytes]:
    total = 0
    for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise BoundaryError("light_action_row_storage", "binary_rows_required")
        total += len(chunk)
        if total > stored_size:
            raise BoundaryError("light_action_row_storage", "stored_length_mismatch")
        if chunk:
            yield chunk
    if total != stored_size:
        raise BoundaryError("light_action_row_storage", "stored_length_mismatch")


def decoded_rows(
    chunks: Iterable[bytes], *, stored_size: int, media_type: str,
    identity: dict[str, Any] | None,
) -> Iterator[bytes]:
    """Decode incrementally with independent stored, logical and output-chunk bounds."""
    if type(stored_size) is not int or not 0 < stored_size <= MAX_STORED_BYTES:
        raise BoundaryError("light_action_row_storage", "stored_size_limit")
    source = iter(_bounded_chunks(chunks, stored_size))
    if identity is None:
        if media_type != LEGACY_MEDIA_TYPE:
            raise BoundaryError("light_action_row_storage", "rows_storage_metadata_required")
        yield from source
        return
    expected = storage_identity(identity)
    if media_type != COMPRESSED_MEDIA_TYPE:
        raise BoundaryError("light_action_row_storage", "rows_media_type_mismatch")
    header_size = len(MAGIC) + _HEADER.size
    header = bytearray()
    remainder = b""
    for chunk in source:
        needed = header_size - len(header)
        header.extend(chunk[:needed])
        remainder = chunk[needed:]
        if len(header) == header_size:
            break
    if len(header) != header_size:
        raise BoundaryError("light_action_row_storage", "truncated_rows_frame")
    if not header.startswith(MAGIC):
        raise BoundaryError("light_action_row_storage", "unsupported_rows_frame")
    size, sha256 = _HEADER.unpack(header[len(MAGIC):])
    if not 0 < size <= MAX_LOGICAL_BYTES:
        raise BoundaryError("light_action_row_storage", "logical_size_limit")
    if size != expected["logical_size"] or sha256.hex() != expected["logical_sha256"]:
        raise BoundaryError("light_action_row_storage", "rows_header_identity_mismatch")
    inflater = zlib.decompressobj()
    whole = hashlib.sha256()
    total = 0
    for data in itertools.chain((remainder,), source):
        if inflater.eof and data:
            raise BoundaryError("light_action_row_storage", "trailing_rows_data")
        while data:
            maximum = min(STREAM_BYTES, size - total + 1)
            try:
                output = inflater.decompress(data, maximum)
            except zlib.error as error:
                raise BoundaryError(
                    "light_action_row_storage", "invalid_rows_compression",
                ) from error
            total += len(output)
            if total > size:
                raise BoundaryError("light_action_row_storage", "logical_length_mismatch")
            if inflater.unused_data:
                raise BoundaryError("light_action_row_storage", "trailing_rows_data")
            whole.update(output)
            if output:
                yield output
            data = inflater.unconsumed_tail
    if not inflater.eof:
        raise BoundaryError("light_action_row_storage", "truncated_rows_frame")
    if total != size:
        raise BoundaryError("light_action_row_storage", "logical_length_mismatch")
    if whole.digest() != sha256:
        raise BoundaryError("light_action_row_storage", "logical_digest_mismatch")


def require_equal_rows(actual: Iterable[bytes], expected: Iterable[bytes]) -> None:
    """Compare complete byte streams without joining the full logical row payload."""
    left, right = iter(actual), iter(expected)
    left_bytes = right_bytes = b""
    left_offset = right_offset = 0
    while True:
        if left_offset == len(left_bytes):
            left_bytes = next(left, b"")
            left_offset = 0
        if right_offset == len(right_bytes):
            right_bytes = next(right, b"")
            right_offset = 0
        if not left_bytes or not right_bytes:
            if left_bytes != right_bytes:
                raise BoundaryError("light_action_inputs", "source_projection_mismatch")
            return
        length = min(len(left_bytes) - left_offset, len(right_bytes) - right_offset)
        if (left_bytes[left_offset:left_offset + length]
                != right_bytes[right_offset:right_offset + length]):
            raise BoundaryError("light_action_inputs", "source_projection_mismatch")
        left_offset += length
        right_offset += length
