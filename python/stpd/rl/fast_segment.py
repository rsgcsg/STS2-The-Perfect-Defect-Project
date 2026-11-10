"""`stpd/rl-fast-segment-1`: one sealed fast_chain segment as bytes (WP5).

The inference-side assembler (`fast_assembly`) seals at most 64 scored rows of
one game instance under one mu. The segment keeps every row's exact
`stpd/rows-v2-row-1` bytes (the learner runs `decode_row` -> TensorFrame ->
`prepare_group(layout="rows-v2")`), the actual behavior the service returned
(q vector, selected position, applied arm/class/T/eps), the segment mu's value
output on that row, the environment's step outcome copied unchanged, and the
seal. It holds no theta, no world or seed identity and no hidden state; the
learner never fills a missing value from a newer mu.

Seal kinds:
- `bootstrap`: 64 rows, every outcome `completed`, tail value of the *segment*
  mu on the successor row (the next row of the same game; W2 rule unchanged);
- `terminal`: the last row's outcome carries the natural terminal and a
  labeler supplied the outcome class and reward;
- `truncated`: anything else (game closed `not_started`/`unknown`/`stop`/
  `budget`/`env_crash`, terminal without a label, generation discarded). Kept as
  original, counted separately, never handed to SGD.

Byte layout:

    0   8   magic b"STPDFS01"
    8   8   u64 little-endian header length H
    16  H   header: compact UTF-8 JSON, sorted keys (see `HEADER_KEYS`)
    ..      zero padding to a multiple of 8
    ..      each row's bytes, zero padded to a multiple of 8, at the header's
            `rows[i].payload.offset` (relative to the start of this section)
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from .heads import CLASS_FLOORS, OUTCOME_CLASSES
from .segment_validation import _probabilities as learner_distribution_check

FORMAT = "stpd/rl-fast-segment-1"
ROW_FORMAT = "stpd/rows-v2-row-1"
MAGIC = b"STPDFS01"
MAX_ROWS = 64
MAX_HEADER_BYTES = 16 * 1024 * 1024
MAX_SEGMENT_BYTES = 256 * 1024 * 1024
SEAL_KINDS = ("bootstrap", "terminal", "truncated")
HEADER_KEYS = frozenset({
    "format", "row_format", "segment_id", "assembler_id", "game_instance_id", "actor_id",
    "assembler_instance", "segment_index", "start", "single_choice", "sampling", "mu",
    "compiler_identity", "rows", "seal", "trainable", "forced_steps", "audit_retain_mu"})
ROW_KEYS = frozenset({
    "request_id", "step_index", "consumption_id", "catalog_len", "catalog_sha256",
    "observation_sha256", "in_combat", "floor", "payload", "selected_index", "behavior_q",
    "selected_q", "applied", "outcome_probabilities", "outcome"})
MU_KEYS = frozenset({"snapshot_sha256", "training_generation", "version", "updates"})
BOOTSTRAP_KEYS = frozenset({"mu_snapshot_sha256", "successor_request_id", "successor_step_index",
                            "successor_row_sha256", "floor", "outcome_probabilities"})


class SegmentFormatError(ValueError):
    """A malformed or tampered segment; never repaired, never partially read."""


def _reject(code: str) -> None:
    raise SegmentFormatError(code)


def _pad(length: int) -> int:
    return -length % 8


def sha256(raw: bytes | bytearray | memoryview) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


@dataclass(frozen=True)
class SealedSegment:
    """Encoded bytes plus the header they were encoded from (owned, immutable)."""

    header: dict[str, Any]
    data: bytes

    @property
    def segment_id(self) -> str:
        return str(self.header["segment_id"])

    @property
    def kind(self) -> str:
        return str(self.header["seal"]["kind"])

    @property
    def trainable(self) -> bool:
        return bool(self.header["trainable"])

    @property
    def sha256(self) -> str:
        return sha256(self.data)


def supported(outcome_class: int, floor: int) -> bool:
    """`heads.outcome_mask` for one (class, floor): only classes at or above the floor."""
    return CLASS_FLOORS[outcome_class] >= min(floor, 48)


def check_value_distribution(values: Any, floor: int) -> None:
    """The learner's own checks, before a segment is called trainable.

    `segment_validation._probabilities` (51 classes, finite, >= 0, mass within
    1e-5) and the floor support of `segment_targets` / `heads.outcome_mask`
    (no mass below the observed floor).
    """
    try:
        learner_distribution_check(values)
    except ValueError as error:
        raise SegmentFormatError("segment_mu_value_distribution") from error
    if type(floor) is not int or floor < 0 or any(
            value != 0 and not supported(index, floor) for index, value in enumerate(values)):
        _reject("segment_mu_value_floor_support")


def _check_q(row: dict[str, Any]) -> None:
    q = row["behavior_q"]
    index = row["selected_index"]
    if (not isinstance(q, list) or len(q) != row["catalog_len"]
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                   for v in q)
            or not math.isclose(sum(q), 1.0, abs_tol=1e-6)
            or type(index) is not int or not 0 <= index < len(q)
            or q[index] != row["selected_q"] or not q[index] > 0):
        _reject("segment_behavior_q_invalid")
    check_value_distribution(row["outcome_probabilities"], row["floor"])
    forced = (row["outcome"] or {}).get("forced_steps", 0)
    if type(forced) is not int or forced < 0:
        _reject("segment_forced_steps")


def validate_header(header: dict[str, Any]) -> None:
    """Structural and seal rules shared by the encoder and the reader."""
    if not isinstance(header, dict) or set(header) != HEADER_KEYS:
        _reject("segment_header_keys")
    if header["format"] != FORMAT or header["row_format"] != ROW_FORMAT:
        _reject("segment_format")
    if not isinstance(header["mu"], dict) or set(header["mu"]) != MU_KEYS:
        _reject("segment_mu_identity")
    if type(header["audit_retain_mu"]) is not bool:
        _reject("segment_audit_flag")
    rows = header["rows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        _reject("segment_row_count")
    previous_step = -1
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            _reject("segment_row_keys")
        if type(row["step_index"]) is not int or row["step_index"] <= previous_step:
            _reject("segment_step_order")
        previous_step = row["step_index"]
        _check_q(row)
        if row["applied"].get("decision_class") != ("combat" if row["in_combat"]
                                                     else "noncombat"):
            _reject("segment_decision_class")
    seal = header["seal"]
    kind = seal.get("kind") if isinstance(seal, dict) else None
    if kind not in SEAL_KINDS or set(seal) != {"kind", "bootstrap", "terminal", "truncation"}:
        _reject("segment_seal")
    completed = all((row["outcome"] or {}).get("status") == "completed" for row in rows)
    if kind == "bootstrap":
        tail = seal["bootstrap"]
        if (len(rows) != MAX_ROWS or not completed or seal["terminal"] is not None
                or seal["truncation"] is not None or not isinstance(tail, dict)
                or set(tail) != BOOTSTRAP_KEYS
                or type(tail["successor_request_id"]) is not int
                or tail["successor_step_index"] <= rows[-1]["step_index"]
                or tail.get("mu_snapshot_sha256") != header["mu"]["snapshot_sha256"]
                or type(tail.get("floor")) is not int
                or tail["floor"] < max(row["floor"] for row in rows)
                or (rows[-1]["outcome"] or {}).get("terminal") is not None):
            _reject("segment_bootstrap_seal")
        check_value_distribution(tail["outcome_probabilities"], tail["floor"])
    elif kind == "terminal":
        terminal = seal["terminal"]
        if (not completed or seal["bootstrap"] is not None or seal["truncation"] is not None
                or not isinstance(terminal, dict) or set(terminal) != {"result", "class", "reward"}
                or (rows[-1]["outcome"] or {}).get("terminal") != {"result": terminal["result"]}):
            _reject("segment_terminal_seal")
        label = terminal["class"]
        if type(label) is not int or not 0 <= label < OUTCOME_CLASSES:
            _reject("segment_terminal_class")
        if not all(supported(label, row["floor"]) for row in rows):
            _reject("terminal_before_observed_floor")
    else:
        truncation = seal["truncation"]
        if (seal["bootstrap"] is not None or seal["terminal"] is not None
                or not isinstance(truncation, dict) or set(truncation) != {"reason", "detail"}):
            _reject("segment_truncated_seal")
    if header["trainable"] is not (kind != "truncated"):
        _reject("segment_truncated_is_never_trainable")
    forced = sum((row["outcome"] or {}).get("forced_steps", 0) for row in rows)
    if header["forced_steps"] != forced:
        _reject("segment_forced_steps")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return repr(value)


@dataclass(frozen=True)
class RejectedSegment:
    """A segment the encoder refused, kept as original (never trained, never sent).

    `data` is `stpd/rl-fast-segment-rejected-1`: canonical JSON with the
    header as the assembler held it (non-JSON values by repr), the error code
    and every row's exact bytes (base64).
    """

    segment_id: str
    header: dict[str, Any]
    rows: tuple[bytes, ...]
    error: str
    kind: str = "rejected"
    trainable: bool = False

    @property
    def data(self) -> bytes:
        return canonical_json({
            "format": "stpd/rl-fast-segment-rejected-1", "error": self.error,
            "header": _json_safe(self.header),
            "rows_base64": [base64.b64encode(row).decode() for row in self.rows]})


@dataclass(frozen=True)
class InstanceClosed:
    """One game instance closed at the assembler (WP8): the learner's exclusion signal.

    Emitted after every segment of the instance, on the same stream, so a
    receiver sees it after the last one. `close_reason` is the environment's
    `episode_close.reason`; `failure` the first non-completed outcome of the game
    (`"<status>:<reason>"`, for example `"unknown:compact_state_divergence"`, or
    `"refused:<code>"` for a refused row), or None. `segments` counts every
    segment of the instance by sealed kind (`bootstrap` + `terminal` = trainable).
    """

    game_instance_id: str
    close_reason: str
    failure: str | None
    segments: dict[str, int]


def encode_segment(header: dict[str, Any], rows: list[bytes]) -> SealedSegment:
    """Seal `header` (whose rows carry no payload yet) with the exact row bytes."""
    header = json.loads(canonical_json(header))  # owned copy, JSON-only values
    if len(rows) != len(header.get("rows", ())):
        _reject("segment_row_bytes_count")
    offset = 0
    for meta, raw in zip(header["rows"], rows, strict=True):
        digest = sha256(raw)
        if meta.get("payload", {}).get("row_sha256", digest) != digest:
            _reject("segment_row_digest_mismatch")
        meta["payload"] = {"offset": offset, "length": len(raw), "row_sha256": digest}
        offset += len(raw) + _pad(len(raw))
    validate_header(header)
    encoded = canonical_json(header)
    if len(encoded) > MAX_HEADER_BYTES:
        _reject("segment_header_byte_limit")
    parts = [MAGIC, len(encoded).to_bytes(8, "little"), encoded, b"\0" * _pad(len(encoded))]
    for raw in rows:
        parts.extend((bytes(raw), b"\0" * _pad(len(raw))))
    data = b"".join(parts)
    if len(data) > MAX_SEGMENT_BYTES:
        _reject("segment_byte_limit")
    return SealedSegment(header, data)


def decode_segment(data: bytes | bytearray | memoryview, *,
                   expected_sha256: str | None = None) -> tuple[dict[str, Any], list[bytes]]:
    """Return (header, row bytes); every row is bound by its sha256."""
    raw = memoryview(data)
    if len(raw) > MAX_SEGMENT_BYTES or len(raw) < 16 or bytes(raw[:8]) != MAGIC:
        _reject("segment_magic_or_size")
    if expected_sha256 is not None and sha256(raw) != expected_sha256:
        _reject("segment_digest_mismatch")
    length = int.from_bytes(raw[8:16], "little")
    if not 0 < length <= MAX_HEADER_BYTES or 16 + length > len(raw):
        _reject("segment_header_length")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _reject("segment_duplicate_field")
            result[key] = value
        return result

    def constant(_: str) -> Any:
        _reject("segment_nonfinite_json")

    try:
        header = json.loads(bytes(raw[16:16 + length]), object_pairs_hook=pairs,
                            parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SegmentFormatError("segment_header_json") from error
    validate_header(header)
    base = 16 + length + _pad(length)
    if any(raw[16 + length:base]):
        _reject("segment_padding_nonzero")
    rows: list[bytes] = []
    cursor = 0
    for meta in header["rows"]:
        payload = meta["payload"]
        if payload.get("offset") != cursor or type(payload.get("length")) is not int \
                or payload["length"] < 1:
            _reject("segment_row_offsets")
        start, end = base + cursor, base + cursor + payload["length"]
        if end + _pad(payload["length"]) > len(raw):
            _reject("segment_row_out_of_bounds")
        row = bytes(raw[start:end])
        if sha256(row) != payload["row_sha256"]:
            _reject("segment_row_digest_mismatch")
        if any(raw[end:end + _pad(payload["length"])]):
            _reject("segment_padding_nonzero")
        rows.append(row)
        cursor += payload["length"] + _pad(payload["length"])
    if base + cursor != len(raw):
        _reject("segment_trailing_bytes")
    return header, rows
