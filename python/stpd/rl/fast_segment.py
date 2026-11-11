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

`stpd/rl-fast-segment-2` (T3, opt-in at the assembler, `segment_format`): the
same logical segment, columnar. The per-row numbers leave the JSON header, so
sealing costs a few microseconds per row instead of ~90 (two JSON encodes and
one decode of ~80 KB of floats per 64 rows):

    0   8   magic b"STPDFS02"
    8   8   u64 little-endian header length H
    16  H   header: compact UTF-8 JSON, sorted keys (see `HEADER_KEYS_2`): the
            segment fields of format 1 without `rows`, plus `row_count`,
            `text` (consumption_id, catalog_sha256, observation_sha256,
            row_sha256: one string per row), `outcomes` (one per row, the
            environment's outcome fields unchanged, or null) and `columns`
            (the layout below, as [name, dtype, count])
    ..      zero padding to a multiple of 8
    ..      the `COLUMNS_2` arrays in order, little-endian, each zero padded
            to a multiple of 8 (`behavior_q` is ragged: catalog_len values per
            row, in row order; `outcome_probabilities` is row_count x 51)
    ..      each row's bytes, zero padded to a multiple of 8, in row order
            (offsets follow from `payload_length`)

Every row's `applied` is not stored: the encoder requires it to equal what
`sampling` gives for the row's decision class (the assembler already refuses
any other), and the reader rebuilds it. q values and value distributions are
float64 (exact for every finite JSON number in [0, 1]). `decode_segment` reads
both formats into the same logical header (format 1 shape, `format` naming the
byte format, `payload` offsets of that layout), checked by the same
`validate_header`, so nothing downstream of the reader changes. Old format-1
segments are read as before and never converted (U4).
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .heads import CLASS_FLOORS, OUTCOME_CLASSES
from .segment_validation import _probabilities as learner_distribution_check

FORMAT = "stpd/rl-fast-segment-1"
FORMAT_2 = "stpd/rl-fast-segment-2"
FORMATS = (FORMAT, FORMAT_2)
ROW_FORMAT = "stpd/rows-v2-row-1"
MAGIC = b"STPDFS01"
MAGIC_2 = b"STPDFS02"
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
HEADER_KEYS_2 = (HEADER_KEYS - {"rows"}) | {"row_count", "text", "outcomes", "columns"}
TEXT_COLUMNS_2 = ("consumption_id", "catalog_sha256", "observation_sha256", "row_sha256")
INT_COLUMNS_2 = ("request_id", "step_index", "catalog_len", "floor", "selected_index",
                 "payload_length")
COLUMNS_2 = (*((name, "<i8") for name in INT_COLUMNS_2), ("in_combat", "|u1"),
             ("selected_q", "<f8"), ("behavior_q", "<f8"), ("outcome_probabilities", "<f8"))
_INT64 = (-(1 << 63), (1 << 63) - 1)
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


_FLOAT = {float}
# The fast support check below reads this layout (heads: classes 0..48 by floor,
# 49 = win, 50 = the Act 2 class at floor 35); any other layout takes the slow path.
_PREFIX_SUPPORT = (len(CLASS_FLOORS) == OUTCOME_CLASSES == 51
                   and CLASS_FLOORS[:50] == tuple(range(50)) and CLASS_FLOORS[50] <= 48)


def unit_numbers(values: Any) -> bool:
    """Every entry an int or float, finite, in [0, 1] (one C pass when all floats)."""
    if set(map(type, values)) <= _FLOAT:
        if not values:
            return True
        return math.isfinite(sum(values)) and min(values) >= 0 and max(values) <= 1
    return not any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                   for v in values)


def supported(outcome_class: int, floor: int) -> bool:
    """`heads.outcome_mask` for one (class, floor): only classes at or above the floor."""
    return CLASS_FLOORS[outcome_class] >= min(floor, 48)


def check_value_distribution(values: Any, floor: int) -> None:
    """The learner's own checks, before a segment is called trainable.

    `segment_validation._probabilities` (51 classes, finite, >= 0, mass within
    1e-5) and the floor support of `segment_targets` / `heads.outcome_mask`
    (no mass below the observed floor).
    """
    if (_PREFIX_SUPPORT and type(values) is list and len(values) == OUTCOME_CLASSES
            and type(floor) is int and floor >= 0 and set(map(type, values)) == _FLOAT):
        # Same verdict, one C pass each: all floats, so a finite sum >= 0 entries
        # means every entry is finite; unsupported classes are a prefix plus 50.
        total = sum(values)
        if (math.isfinite(total) and min(values) >= 0
                and math.isclose(total, 1, rel_tol=0, abs_tol=1e-5)):
            limit = min(floor, 48)
            if any(values[:limit]) or (limit > CLASS_FLOORS[50] and values[50]):
                _reject("segment_mu_value_floor_support")
            return
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
    if (not isinstance(q, list) or len(q) != row["catalog_len"] or not unit_numbers(q)
            or not math.isclose(sum(q), 1.0, abs_tol=1e-6)
            or type(index) is not int or not 0 <= index < len(q)
            or q[index] != row["selected_q"] or not q[index] > 0):
        _reject("segment_behavior_q_invalid")
    check_value_distribution(row["outcome_probabilities"], row["floor"])
    forced = (row["outcome"] or {}).get("forced_steps", 0)
    if type(forced) is not int or forced < 0:
        _reject("segment_forced_steps")


def validate_header(header: dict[str, Any], byte_format: str = FORMAT, *,
                    rows_checked: bool = False) -> None:
    """Structural and seal rules shared by the encoders and the reader (logical header).

    `rows_checked`: the caller already ran the per-row q / value / forced-steps
    checks on these exact values (the assembler, at finish_row and step_outcome);
    every other rule still runs.
    """
    if not isinstance(header, dict) or set(header) != HEADER_KEYS:
        _reject("segment_header_keys")
    if header["format"] != byte_format or header["row_format"] != ROW_FORMAT:
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
        if not rows_checked:
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


def applied_of(sampling: Any, in_combat: Any) -> dict[str, Any]:
    """The `applied` record `sampling` gives one row (format 2 stores none)."""
    decision = "combat" if in_combat else "noncombat"
    policy = sampling["classes"][decision]
    return {"arm_id": sampling["arm_id"], "decision_class": decision,
            "temperature": policy["temperature"], "uniform_mix": policy["uniform_mix"]}


def _same_json(left: Any, right: Any) -> bool:
    """Equal and of the same JSON types all the way down (1 != 1.0 != True here)."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(v, right[k])
                                                   for k, v in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(map(_same_json, left, right))
    return bool(left == right)


def _int_column(name: str, values: list[Any]) -> bytes:
    if any(type(v) is not int or not _INT64[0] <= v <= _INT64[1] for v in values):
        _reject(f"segment_column_{name}")
    return np.asarray(values, dtype="<i8").tobytes()


def encode_segment_columns(header: dict[str, Any], rows: list[bytes], *,
                           bound: bool = False) -> SealedSegment:
    """Seal `header` (format-1 shape, rows without payload offsets) as format 2.

    Checks what `encode_segment` checks (the logical header goes through
    `validate_header`, every row is bound by its sha256), plus the column types:
    ints are int64 ints, `in_combat` a bool, the text fields strings and every
    row's `applied` what `sampling` gives it (same JSON types). Returns the
    logical header (q and values as floats, as decoded), owned except for the
    row outcomes when `bound`.

    `bound` (the assembler only): every row's `payload.row_sha256` was computed
    from these exact immutable bytes when the row was accepted, and the per-row
    q / value / forced-steps checks already ran on these values (and outcomes are
    already the assembler's own copies), so they are not repeated here. The
    assembler decodes and compares every `verify_every`-th segment in full.
    """
    if not isinstance(header, dict) or not isinstance(header.get("rows"), list):
        _reject("segment_header_keys")
    metas = header["rows"]
    if len(rows) != len(metas):
        _reject("segment_row_bytes_count")
    try:
        logical = {key: _json_tree(value) for key, value in header.items() if key != "rows"}
    except TypeError as error:
        raise SegmentFormatError("segment_header_json_types") from error
    owned_rows: list[dict[str, Any]] = []
    offset = 0
    for meta, raw in zip(metas, rows, strict=True):
        if not isinstance(meta, dict) or set(meta) != ROW_KEYS:
            _reject("segment_row_keys")
        payload = meta["payload"]
        if bound:
            digest = payload.get("row_sha256") if isinstance(payload, dict) else None
            if type(digest) is not str:
                _reject("segment_row_digest_mismatch")
        else:
            digest = sha256(raw)
            if not isinstance(payload, dict) or payload.get("row_sha256", digest) != digest:
                _reject("segment_row_digest_mismatch")
        row = dict(meta)
        row["payload"] = {"offset": offset, "length": len(raw), "row_sha256": digest}
        if not bound:
            try:
                row["outcome"] = _json_tree(meta["outcome"])
            except TypeError as error:
                raise SegmentFormatError("segment_header_json_types") from error
        owned_rows.append(row)
        offset += len(raw) + _pad(len(raw))
    logical["rows"] = owned_rows
    validate_header(logical, FORMAT_2, rows_checked=bound)
    try:
        expected = {flag: applied_of(logical["sampling"], flag) for flag in (False, True)}
    except (KeyError, TypeError) as error:
        raise SegmentFormatError("segment_column_applied") from error
    for row in owned_rows:
        applied = row["applied"]
        if type(row["in_combat"]) is not bool or not _same_json(applied,
                                                                expected[row["in_combat"]]):
            _reject("segment_column_applied")
        row["applied"] = dict(expected[row["in_combat"]])
        # Checked numbers in [0, 1] (no bools); stored as f8, so the logical header
        # holds the floats the decoder gives back (an int q 1 becomes 1.0).
        row["behavior_q"] = list(map(float, row["behavior_q"]))
        row["selected_q"] = float(row["selected_q"])
        row["outcome_probabilities"] = list(map(float, row["outcome_probabilities"]))
    text = {name: [row["payload"]["row_sha256"] if name == "row_sha256" else row[name]
                   for row in owned_rows] for name in TEXT_COLUMNS_2}
    if any(type(v) is not str for values in text.values() for v in values):
        _reject("segment_column_text")
    q = [v for row in owned_rows for v in row["behavior_q"]]
    columns = [
        *(_int_column(name, [row["payload"]["length"] if name == "payload_length"
                                         else row[name] for row in owned_rows])
          for name in INT_COLUMNS_2),
        bytes(row["in_combat"] for row in owned_rows),
        np.asarray([row["selected_q"] for row in owned_rows], dtype="<f8").tobytes(),
        np.asarray(q, dtype="<f8").tobytes(),
        np.asarray([row["outcome_probabilities"] for row in owned_rows],
                   dtype="<f8").tobytes()]
    counts = [len(owned_rows)] * (len(INT_COLUMNS_2) + 2) + [
        len(q), len(owned_rows) * OUTCOME_CLASSES]
    stored = {key: value for key, value in logical.items() if key != "rows"}
    stored.update(row_count=len(owned_rows), text=text,
                  outcomes=[row["outcome"] for row in owned_rows],
                  columns=[[name, dtype, count]
                           for (name, dtype), count in zip(COLUMNS_2, counts, strict=True)])
    encoded = canonical_json(stored)
    if len(encoded) > MAX_HEADER_BYTES:
        _reject("segment_header_byte_limit")
    parts = [MAGIC_2, len(encoded).to_bytes(8, "little"), encoded, b"\0" * _pad(len(encoded))]
    for column in columns:
        parts.extend((column, b"\0" * _pad(len(column))))
    for raw in rows:
        parts.extend((bytes(raw), b"\0" * _pad(len(raw))))
    data = b"".join(parts)
    if len(data) > MAX_SEGMENT_BYTES:
        _reject("segment_byte_limit")
    return SealedSegment(logical, data)


def _json_tree(value: Any) -> Any:
    """An owned copy of a JSON tree (dict/list/str/int/float/bool/None); TypeError otherwise."""
    kind = type(value)
    if kind is str or kind is int or kind is bool or value is None:
        return value
    if kind is float:
        if not math.isfinite(value):
            raise TypeError("nonfinite")
        return value
    if kind is dict:
        if any(type(key) is not str for key in value):
            raise TypeError("key")
        return {key: _json_tree(item) for key, item in value.items()}
    if kind is list or kind is tuple:
        return [_json_tree(item) for item in value]
    raise TypeError(kind.__name__)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            _reject("segment_duplicate_field")
        result[key] = value
    return result


def _constant(_: str) -> Any:
    _reject("segment_nonfinite_json")


def _header_json(raw: memoryview, length: int) -> Any:
    try:
        return json.loads(bytes(raw[16:16 + length]), object_pairs_hook=_pairs,
                          parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SegmentFormatError("segment_header_json") from error


def _decode_columns(raw: memoryview, length: int, header_only: bool
                    ) -> tuple[dict[str, Any], list[bytes]]:
    stored = _header_json(raw, length)
    if not isinstance(stored, dict) or set(stored) != HEADER_KEYS_2:
        _reject("segment_header_keys")
    if stored["format"] != FORMAT_2:
        _reject("segment_format")
    count = stored["row_count"]
    text, outcomes = stored["text"], stored["outcomes"]
    if (type(count) is not int or not 1 <= count <= MAX_ROWS
            or not isinstance(text, dict) or set(text) != set(TEXT_COLUMNS_2)
            or any(not isinstance(text[name], list) or len(text[name]) != count
                   or any(type(v) is not str for v in text[name]) for name in TEXT_COLUMNS_2)
            or not isinstance(outcomes, list) or len(outcomes) != count
            or not isinstance(stored["columns"], list)
            or len(stored["columns"]) != len(COLUMNS_2)):
        _reject("segment_columns")
    cursor = 16 + length + _pad(length)
    if any(raw[16 + length:cursor]):
        _reject("segment_padding_nonzero")
    arrays: dict[str, Any] = {}
    for (name, dtype), described in zip(COLUMNS_2, stored["columns"], strict=True):
        if name == "behavior_q":
            widths = arrays["catalog_len"].tolist()
            expected = sum(widths) if min(widths) >= 1 else -1
        elif name == "outcome_probabilities":
            expected = count * OUTCOME_CLASSES
        else:
            expected = count
        if described != [name, dtype, expected]:
            _reject("segment_columns")
        size = expected * np.dtype(dtype).itemsize
        end = cursor + size
        if end + _pad(size) > len(raw):
            _reject("segment_column_out_of_bounds")
        arrays[name] = np.frombuffer(raw, dtype=dtype, count=expected, offset=cursor)
        if any(raw[end:end + _pad(size)]):
            _reject("segment_padding_nonzero")
        cursor = end + _pad(size)
    if arrays["in_combat"].max() > 1:
        _reject("segment_column_in_combat")
    ints = {name: arrays[name].tolist() for name in INT_COLUMNS_2}
    combat = [bool(v) for v in arrays["in_combat"].tolist()]
    selected = arrays["selected_q"].tolist()
    flat_q = arrays["behavior_q"].tolist()
    values = arrays["outcome_probabilities"].reshape(count, OUTCOME_CLASSES).tolist()
    sampling = stored["sampling"]
    header = {key: value for key, value in stored.items() if key not in (
        "row_count", "text", "outcomes", "columns")}
    rows_meta: list[dict[str, Any]] = []
    base, start, offset = cursor, 0, 0
    for i in range(count):
        width, size = ints["catalog_len"][i], ints["payload_length"][i]
        if size < 1:
            _reject("segment_row_offsets")
        try:
            applied = applied_of(sampling, combat[i])
        except (KeyError, TypeError) as error:
            raise SegmentFormatError("segment_column_applied") from error
        rows_meta.append({
            "request_id": ints["request_id"][i], "step_index": ints["step_index"][i],
            "consumption_id": text["consumption_id"][i], "catalog_len": width,
            "catalog_sha256": text["catalog_sha256"][i],
            "observation_sha256": text["observation_sha256"][i], "in_combat": combat[i],
            "floor": ints["floor"][i],
            "payload": {"offset": offset, "length": size, "row_sha256": text["row_sha256"][i]},
            "selected_index": ints["selected_index"][i], "behavior_q": flat_q[start:start + width],
            "selected_q": selected[i], "applied": applied,
            "outcome_probabilities": values[i], "outcome": outcomes[i]})
        start += width
        offset += size + _pad(size)
    header["rows"] = rows_meta
    validate_header(header, FORMAT_2)
    return header, _rows_section(raw, base, header["rows"], header_only)


def _rows_section(raw: memoryview, base: int, metas: list[dict[str, Any]],
                  header_only: bool) -> list[bytes]:
    rows: list[bytes] = []
    cursor = 0
    for meta in metas:
        payload = meta["payload"]
        if payload.get("offset") != cursor or type(payload.get("length")) is not int \
                or payload["length"] < 1:
            _reject("segment_row_offsets")
        start, end = base + cursor, base + cursor + payload["length"]
        if end + _pad(payload["length"]) > len(raw):
            _reject("segment_row_out_of_bounds")
        if not header_only:
            row = bytes(raw[start:end])
            if sha256(row) != payload["row_sha256"]:
                _reject("segment_row_digest_mismatch")
            rows.append(row)
        if any(raw[end:end + _pad(payload["length"])]):
            _reject("segment_padding_nonzero")
        cursor += payload["length"] + _pad(payload["length"])
    if base + cursor != len(raw):
        _reject("segment_trailing_bytes")
    return rows


def decode_segment(data: bytes | bytearray | memoryview, *,
                   expected_sha256: str | None = None,
                   header_only: bool = False) -> tuple[dict[str, Any], list[bytes]]:
    """Return (logical header, row bytes) of either format; every row is bound by its sha256.

    `header_only` (bytes the caller has already bound to the sealed segment's
    sha256): the same header, layout, padding and length checks, but no row
    copies and no per-row sha256; returns no rows. Whoever decodes the rows
    later (the decode worker) still binds every row to its digest.
    """
    raw = memoryview(data)
    if (len(raw) > MAX_SEGMENT_BYTES or len(raw) < 16
            or bytes(raw[:8]) not in (MAGIC, MAGIC_2)):
        _reject("segment_magic_or_size")
    if expected_sha256 is not None and sha256(raw) != expected_sha256:
        _reject("segment_digest_mismatch")
    length = int.from_bytes(raw[8:16], "little")
    if not 0 < length <= MAX_HEADER_BYTES or 16 + length > len(raw):
        _reject("segment_header_length")
    if bytes(raw[:8]) == MAGIC_2:
        return _decode_columns(raw, length, header_only)
    header = _header_json(raw, length)
    validate_header(header)
    base = 16 + length + _pad(length)
    if any(raw[16 + length:base]):
        _reject("segment_padding_nonzero")
    return header, _rows_section(raw, base, header["rows"], header_only)
