"""`stpd/rows-v2-row-1`: one compiled decision row as compact little-endian bytes.

A row carries exactly one `TensorFrame` (the Input-v2 tensors, reductions,
catalog choice ids and entity count) that training preparation already
consumes (`prepare_group(..., layout="rows-v2")`). Decoding returns a frame
element-wise equal (same dtypes, shapes and values; float bits preserved) to the
frame it was encoded from. It is derived public input only: no theta, no
world/seed identity, no hidden state, no legality.

Byte layout:

    0   8   magic  b"STPDRW1\\0"
    8   4   u32 little-endian header length H
    12  H   header: compact UTF-8 JSON with sorted keys
                {"arrays": [[name, dtype, count], ...],   # body order
                 "choices": [choice_id, ...], "entity_count": int,
                 "format": "stpd/rows-v2-row-1",
                 "reductions": {name: [[width, owners], ...]},
                 "text_width": int}
    ..      zero padding to a multiple of 8
    ..      each array (little-endian, zero padded to a multiple of 8) in order:
                text_tokens, text_lengths, text_number_index, text_number_values,
                identities, identity_fields, kinds, positions, number_fields,
                numbers, missing_fields, text_fields, text_indices, actions,
                then per reduction (ids, numbers, text, refs, source, target)
                its buckets' indices `<name>.<i>` and `<name>.inverse`.

Integer arrays use the smallest of u1/u2/i4/i8 that holds their values and
decode to int64; `numbers` and `text_number_values` are f4. Text is ragged:
`text_tokens` concatenates each text's real token ids, and only the token
numbers whose float32 bits are nonzero are stored (`text_number_index` into the
ragged stream). Padding to `text_width` is rebuilt as zeros, exactly as
`compile_frame` pads. The sha256 of these bytes is the payload binding in
`stpd/rl-inference-3` rows; encoding is deterministic.

Canonical form (what `encode_row` writes): every integer array uses the smallest
of u1/u2/i4/i8 holding its values; the header is `json.dumps(sort_keys=True,
ensure_ascii=False, separators=(",", ":"))` UTF-8 with no duplicate keys; all
padding is zero; `text_width` equals the longest text and every text has at
least one token, as `compile_frame` produces; only nonzero token numbers are
stored. There is one encoder (this Python module). `decode_row` accepts some
non-canonical spellings of the same frame (wider integers, other header
spacing), so a row sha256 identifies bytes produced by this encoder only; it is
not a content identity across encoders and decode does not re-encode to check.
A second encoder would need byte-equality tests against this one first.
"""
from __future__ import annotations

import hashlib
import json
import struct
from typing import Any, NoReturn

import numpy as np
import torch

from spireagent.json_boundary import BoundaryError

from .tensor_input import Reduction, TensorFrame

FORMAT = "stpd/rows-v2-row-1"
MAGIC = b"STPDRW1\0"
MAX_ROW_BYTES = 64 * 1024 * 1024
MAX_HEADER_BYTES = 1024 * 1024
TEXT_ARRAYS = ("text_tokens", "text_lengths", "text_number_index", "text_number_values")
NODE_ARRAYS = ("identities", "identity_fields", "kinds", "positions", "number_fields",
               "numbers", "missing_fields", "text_fields", "text_indices", "actions")
FRAME_TENSORS = ("text_ids", "text_numbers", "text_lengths", *NODE_ARRAYS)
REDUCTION_ORDER = ("ids", "numbers", "text", "refs", "source", "target")
FLOAT_ARRAYS = frozenset({"numbers", "text_number_values"})
_DTYPES: dict[str, np.dtype[Any]] = {
    code: np.dtype("<" + code) for code in ("u1", "u2", "i4", "i8", "f4")}
_HEADER_KEYS = {"arrays", "choices", "entity_count", "format", "reductions", "text_width"}


def _reject(code: str) -> NoReturn:
    raise BoundaryError("p1_rows_v2_row", code)


def _pad(length: int) -> int:
    return -length % 8


def row_sha256(row: bytes | bytearray | memoryview) -> str:
    return hashlib.sha256(row).hexdigest()


def _int_code(array: np.ndarray) -> str:
    if array.size == 0:
        return "u1"
    low, high = int(array.min()), int(array.max())
    if low >= 0:
        return "u1" if high < 1 << 8 else "u2" if high < 1 << 16 else \
            "i4" if high < 1 << 31 else "i8"
    return "i4" if low >= -(1 << 31) and high < 1 << 31 else "i8"


def _plan(reductions: dict[str, list[list[int]]]) -> list[str]:
    names = [*TEXT_ARRAYS, *NODE_ARRAYS]
    for name in REDUCTION_ORDER:
        names.extend(f"{name}.{i}" for i in range(len(reductions[name])))
        names.append(f"{name}.inverse")
    return names


def encode_row(frame: TensorFrame) -> bytes:
    """Serialize one CPU TensorFrame; the exact inverse of `decode_row`."""
    tensors = frame.tensors
    if set(tensors) != set(FRAME_TENSORS) or set(frame.reductions) != set(REDUCTION_ORDER):
        _reject("frame_fields_differ")
    if type(frame.entity_count) is not int:
        _reject("frame_entity_count")
    for name, tensor in tensors.items():
        dtype = torch.float32 if name in {"numbers", "text_numbers"} else torch.long
        rank = 2 if name == "text_ids" else 3 if name == "text_numbers" else 1
        if tensor.dtype != dtype or tensor.device.type != "cpu" or tensor.ndim != rank:
            _reject("frame_tensor_dtype")
    ids, numbers = tensors["text_ids"].numpy(), tensors["text_numbers"].numpy()
    lengths = tensors["text_lengths"].numpy()
    if (numbers.shape != (*ids.shape, 1) or lengths.shape != ids.shape[:1]
            or (lengths.size and int(lengths.min()) < 1)
            or ids.shape[1] != (int(lengths.max()) if lengths.size else 0)):
        _reject("frame_text_shape")  # compile_frame: width = longest text, no empty text
    real = np.arange(ids.shape[1])[None, :] < lengths[:, None]
    if ids[~real].any() or numbers[..., 0][~real].view(np.uint32).any():
        _reject("frame_text_padding_nonzero")  # compile_frame pads with zeros only
    stream = numbers[..., 0][real]
    nonzero = np.flatnonzero(stream.view(np.uint32))
    arrays: dict[str, np.ndarray] = {
        "text_tokens": ids[real], "text_lengths": lengths,
        "text_number_index": nonzero, "text_number_values": stream[nonzero]}
    arrays.update({name: tensors[name].numpy() for name in NODE_ARRAYS})
    buckets: dict[str, list[list[int]]] = {}
    for name in REDUCTION_ORDER:
        reduction = frame.reductions[name]
        buckets[name] = []
        for i, (width, owners, indices) in enumerate(reduction.buckets):
            if indices.dtype != torch.long or indices.ndim != 1 \
                    or indices.numel() != width * owners:
                _reject("frame_reduction_structure")
            buckets[name].append([int(width), int(owners)])
            arrays[f"{name}.{i}"] = indices.numpy()
        if reduction.inverse.dtype != torch.long or reduction.inverse.ndim != 1:
            _reject("frame_reduction_structure")
        arrays[f"{name}.inverse"] = reduction.inverse.numpy()
    body, table = [], []
    for name in _plan(buckets):
        array = arrays[name].reshape(-1)
        code = "f4" if name in FLOAT_ARRAYS else _int_code(array)
        raw = array.astype(_DTYPES[code], copy=False).tobytes()
        table.append([name, code, int(array.size)])
        body.append(raw + b"\0" * _pad(len(raw)))
    header = json.dumps({"arrays": table, "choices": list(frame.choices),
                         "entity_count": frame.entity_count, "format": FORMAT,
                         "reductions": buckets, "text_width": int(ids.shape[1])},
                        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    row = b"".join([MAGIC, struct.pack("<I", len(header)), header,
                    b"\0" * _pad(12 + len(header)), *body])
    if len(row) > MAX_ROW_BYTES:
        _reject("row_size_limit")
    return row


def _header(raw: bytes) -> tuple[dict[str, Any], int]:
    if len(raw) < 12 or raw[:8] != MAGIC:
        _reject("row_magic")
    (length,) = struct.unpack_from("<I", raw, 8)
    if length > MAX_HEADER_BYTES or 12 + length > len(raw):
        _reject("row_header_length")
    try:
        header = json.loads(raw[12:12 + length])
    except (UnicodeDecodeError, ValueError):
        _reject("row_header_json")
    if not isinstance(header, dict) or set(header) != _HEADER_KEYS or header["format"] != FORMAT:
        _reject("row_header_fields")
    choices, count, width = header["choices"], header["entity_count"], header["text_width"]
    reductions, table = header["reductions"], header["arrays"]
    if (not isinstance(choices, list) or any(type(c) is not str for c in choices)
            or type(count) is not int or count < 1 or type(width) is not int or width < 0
            or not isinstance(reductions, dict) or set(reductions) != set(REDUCTION_ORDER)
            or not isinstance(table, list)):
        _reject("row_header_fields")
    for buckets in reductions.values():
        if not isinstance(buckets, list) or any(
                not isinstance(b, list) or len(b) != 2
                or any(type(v) is not int or v < 0 for v in b) for b in buckets):
            _reject("row_header_fields")
    names = _plan(reductions)
    if len(table) != len(names) or any(
            not isinstance(entry, list) or len(entry) != 3 or entry[0] != name
            or type(entry[1]) is not str or entry[1] not in _DTYPES
            or (entry[1] == "f4") != (name in FLOAT_ARRAYS)
            or type(entry[2]) is not int or entry[2] < 0
            for entry, name in zip(table, names, strict=False)):
        _reject("row_header_arrays")
    end = 12 + length
    if any(raw[end:end + _pad(end)]):
        _reject("row_padding_nonzero")
    return header, end + _pad(end)


def decode_row(row: bytes | bytearray | memoryview, *, validator: Any,
               expected_sha256: str | None) -> TensorFrame:
    """Rebuild the TensorFrame; validate structure/indices when `validator` is given.

    Both checks are required arguments so a consumer cannot omit them by accident:
    `validator` is a `FrameCompiler` for the bound vocabulary (the same bounds
    `FrameCompiler.decode` checks before M-std reads any index) and
    `expected_sha256` binds these exact bytes (`payload.row_sha256`). Only tests
    of the byte layout itself pass None.
    """
    raw = bytes(row)
    if len(raw) > MAX_ROW_BYTES:
        _reject("row_size_limit")
    if expected_sha256 is not None and row_sha256(raw) != expected_sha256:
        _reject("row_digest_mismatch")
    header, cursor = _header(raw)
    expected = cursor
    for _, code, count in header["arrays"]:
        nbytes = count * _DTYPES[code].itemsize
        expected += nbytes + _pad(nbytes)
    if expected != len(raw):
        _reject("row_length_mismatch")
    arrays: dict[str, np.ndarray] = {}
    for name, code, count in header["arrays"]:
        nbytes = count * _DTYPES[code].itemsize
        if any(raw[cursor + nbytes:cursor + nbytes + _pad(nbytes)]):
            _reject("row_padding_nonzero")
        array = np.frombuffer(raw, dtype=_DTYPES[code], count=count, offset=cursor)
        arrays[name] = array.astype(np.float32 if code == "f4" else np.int64)  # owned copy
        cursor += nbytes + _pad(nbytes)
    lengths, width = arrays["text_lengths"], header["text_width"]
    tokens = arrays["text_tokens"]
    # Check the canonical text shape before any dense allocation: width is the
    # longest text and no text is empty (compile_frame), and the dense text is
    # bounded like the old 64 MiB frame wire, so a header integer cannot inflate it.
    if ((lengths.size and int(lengths.min()) < 1)
            or width != (int(lengths.max()) if lengths.size else 0)
            or lengths.size * width * 12 > MAX_ROW_BYTES
            or int(lengths.sum()) != tokens.size):
        _reject("row_text_shape")
    index, values = arrays["text_number_index"], arrays["text_number_values"]
    if index.size != values.size or (index.size and (
            int(index.min()) < 0 or int(index.max()) >= tokens.size
            or bool((np.diff(index) <= 0).any()))):
        _reject("row_text_numbers")
    real = np.arange(width)[None, :] < lengths[:, None]
    ids = np.zeros((lengths.size, width), dtype=np.int64)
    ids[real] = tokens
    stream = np.zeros(tokens.size, dtype=np.float32)
    stream[index] = values
    numbers = np.zeros((lengths.size, width, 1), dtype=np.float32)
    numbers[..., 0][real] = stream
    tensors = {"text_ids": torch.from_numpy(ids), "text_numbers": torch.from_numpy(numbers),
               "text_lengths": torch.from_numpy(lengths)}
    tensors.update({name: torch.from_numpy(arrays[name]) for name in NODE_ARRAYS})
    reductions = {}
    for name in REDUCTION_ORDER:
        buckets = tuple((width_, owners, torch.from_numpy(arrays[f"{name}.{i}"]))
                        for i, (width_, owners) in enumerate(header["reductions"][name]))
        reductions[name] = Reduction(buckets, torch.from_numpy(arrays[f"{name}.inverse"]))
    frame = TensorFrame(tensors, reductions, tuple(header["choices"]), header["entity_count"])
    if validator is not None:
        validator._validate_state(frame.state())
    return frame
