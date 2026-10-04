"""Bounded M0 request wire framing; canonical logical bytes retain their identity."""

from __future__ import annotations

import hashlib
import struct
import zlib

from spireagent.json_boundary import BoundaryError

MAX_M0_REQUEST_BYTES = 256 * 1024 * 1024
MAX_M0_LOGICAL_REQUEST_BYTES = 1024 * 1024 * 1024
REQUEST_WIRE_MAGIC = b"STPD-M0-REQUEST\x00\x01"
_HEADER = struct.Struct(">Q32s")


def encode_m0_request(raw: bytes, *, compress: bool = False) -> bytes:
    """Keep legacy bytes when they fit; otherwise emit the version-1 zlib frame."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_M0_LOGICAL_REQUEST_BYTES:
        raise BoundaryError("m0_request_wire", "logical_request_size_limit")
    if not compress and len(raw) <= MAX_M0_REQUEST_BYTES:
        return raw
    framed = (REQUEST_WIRE_MAGIC
              + _HEADER.pack(len(raw), hashlib.sha256(raw).digest())
              + zlib.compress(raw, level=6))
    if len(framed) > MAX_M0_REQUEST_BYTES:
        raise BoundaryError("m0_request_wire", "request_size_limit")
    return framed


def decode_m0_request(raw: bytes) -> bytes:
    """Decode a frame or legacy logical bytes, without unbounded decompression.

    The caller checks the wire bound before accepting transport bytes. Unframed
    canonical bytes may also come from the owner's logical request store.
    """
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_M0_LOGICAL_REQUEST_BYTES:
        raise BoundaryError("m0_request_wire", "logical_request_size_limit")
    if raw.startswith(REQUEST_WIRE_MAGIC[:-1]) and not raw.startswith(REQUEST_WIRE_MAGIC):
        raise BoundaryError("m0_request_wire", "unsupported_request_wire_version")
    if not raw.startswith(REQUEST_WIRE_MAGIC):
        return raw
    header_end = len(REQUEST_WIRE_MAGIC) + _HEADER.size
    if len(raw) > MAX_M0_REQUEST_BYTES:
        raise BoundaryError("m0_request_wire", "request_size_limit")
    if len(raw) <= header_end:
        raise BoundaryError("m0_request_wire", "truncated_request_frame")
    size, sha256 = _HEADER.unpack(raw[len(REQUEST_WIRE_MAGIC):header_end])
    if not 1 <= size <= MAX_M0_LOGICAL_REQUEST_BYTES:
        raise BoundaryError("m0_request_wire", "logical_request_size_limit")
    inflater = zlib.decompressobj()
    try:
        # One extra byte proves an understated length/bomb without inflating the
        # rest. Never call unbounded decompress() or flush() on untrusted bytes.
        logical = inflater.decompress(raw[header_end:], size + 1)
    except zlib.error as error:
        raise BoundaryError("m0_request_wire", "invalid_request_compression") from error
    if len(logical) != size:
        raise BoundaryError("m0_request_wire", "request_length_mismatch")
    if not inflater.eof:
        raise BoundaryError("m0_request_wire", "truncated_request_frame")
    if inflater.unused_data or inflater.unconsumed_tail:
        raise BoundaryError("m0_request_wire", "trailing_request_data")
    if hashlib.sha256(logical).digest() != sha256:
        raise BoundaryError("m0_request_wire", "request_digest_mismatch")
    return logical
