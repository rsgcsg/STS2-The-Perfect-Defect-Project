"""Versioned lossless UTF-8 byte codec for public text-menu action text."""

from __future__ import annotations

import hashlib
import json

from spireagent.json_boundary import BoundaryError

SCHEMA = "stpd/light-action-utf8-bytes-v1"
BOS_ACT = 256
EOS_ACT = 257
VOCAB_SIZE = 258

SPEC = {
    "schema": SCHEMA,
    "mapping": "each UTF-8 byte maps to the identical token ID 0..255",
    "bos_act": BOS_ACT,
    "eos_act": EOS_ACT,
    "vocab_size": VOCAB_SIZE,
    "normalization": "none",
    "truncation": "forbidden",
    "action_id_input": False,
}
SPEC_BYTES = json.dumps(SPEC, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")).encode("utf-8")
SPEC_SHA256 = hashlib.sha256(SPEC_BYTES).hexdigest()


def encode_action(text: str, *, max_bytes: int) -> tuple[int, ...]:
    if not isinstance(text, str) or type(max_bytes) is not int or max_bytes < 1:
        raise BoundaryError("light_action_codec", "invalid_action_or_limit")
    try:
        encoded = text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise BoundaryError("light_action_codec", "invalid_unicode") from error
    if not encoded:
        raise BoundaryError("light_action_codec", "empty_action")
    if len(encoded) > max_bytes:
        raise BoundaryError("light_action_codec", "action_byte_limit_exceeded_no_truncation")
    return (BOS_ACT, *encoded, EOS_ACT)


def decode_action(ids: tuple[int, ...] | list[int]) -> str:
    if (not isinstance(ids, (tuple, list)) or len(ids) < 3
            or ids[0] != BOS_ACT or ids[-1] != EOS_ACT
            or any(type(value) is not int or not 0 <= value <= 255 for value in ids[1:-1])):
        raise BoundaryError("light_action_codec", "invalid_encoded_action")
    try:
        return bytes(ids[1:-1]).decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise BoundaryError("light_action_codec", "invalid_utf8_action") from error
