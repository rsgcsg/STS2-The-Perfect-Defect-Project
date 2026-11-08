"""`stpd/rows-v2-row-1` round-trips the real compiled frame and rejects tampering."""
from __future__ import annotations

import json
import struct

import numpy as np
import pytest
import torch
from test_p1_frame_compiler import assert_frame_equal
from test_p1_input_r18 import claws, trial
from test_p1_input_r19 import sphere
from test_p1_input_v2 import public_state

from spireagent.json_boundary import BoundaryError
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.input import V2_INPUT_SPEC, V2_R18_INPUT_SPEC, V2_R19_INPUT_SPEC
from stpd.p1.rows_encoder_wire import FORMAT, MAGIC, decode_row, encode_row, row_sha256
from stpd.p1.tensor_input import Reduction, TensorFrame
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl.batch_preparation import prepare_group

CASES = [(public_state, V2_INPUT_SPEC), (trial, V2_R18_INPUT_SPEC),
         (claws, V2_R18_INPUT_SPEC), (sphere, V2_R19_INPUT_SPEC)]


@pytest.mark.parametrize("source,spec", CASES)
def test_row_round_trips_reference_frame_and_rows_v2_preparation(source, spec):
    compiler = FrameCompiler(public_vocabulary(spec))
    frame = compiler.compile(source())
    row = encode_row(frame)
    assert row == encode_row(frame)  # deterministic bytes, so row_sha256 binds content
    decoded = decode_row(row, validator=compiler, expected_sha256=row_sha256(row))
    assert_frame_equal(frame, decoded)
    floors = torch.tensor([0, 0])
    left = prepare_group([frame, frame], (0, 1), True, floors, deduplicate_text=True,
                         layout="rows-v2")
    right = prepare_group([decoded, decoded], (0, 1), True, floors, deduplicate_text=True,
                          layout="rows-v2")
    for key, tensor in left.tensors.items():
        assert torch.equal(tensor, right.tensors[key]), key
    assert left.layout.scene_buckets == right.layout.scene_buckets


def test_row_rejects_digest_length_padding_header_and_index_tampering():
    compiler = FrameCompiler(public_vocabulary(V2_INPUT_SPEC))
    row = encode_row(compiler.compile(public_state()))
    with pytest.raises(BoundaryError, match="row_digest_mismatch"):
        decode_row(row, validator=compiler, expected_sha256="0" * 64)
    with pytest.raises(BoundaryError, match="row_magic"):
        decode_row(b"X" + row[1:], validator=None, expected_sha256=None)
    with pytest.raises(BoundaryError, match="row_length_mismatch"):
        decode_row(row + b"\0" * 8, validator=None, expected_sha256=None)
    with pytest.raises(BoundaryError, match="row_length_mismatch"):
        decode_row(row[:-8], validator=None, expected_sha256=None)
    (length,) = struct.unpack_from("<I", row, 8)
    header = row[12:12 + length]
    assert FORMAT.encode() in header
    with pytest.raises(BoundaryError, match="row_header_fields"):
        decode_row(row.replace(FORMAT.encode(), b"stpd/rows-v2-row-0"), validator=None,
                   expected_sha256=None)
    if (12 + length) % 8:
        with pytest.raises(BoundaryError, match="row_padding_nonzero"):
            decode_row(row[:12 + length] + b"\1" + row[13 + length:], validator=None,
                       expected_sha256=None)
    # A kinds index past the vocabulary only fails with the structural validator.
    frame = compiler.compile(public_state())
    frame.tensors["kinds"][0] = 10**9
    tampered = encode_row(frame)
    decode_row(tampered, validator=None, expected_sha256=None)
    with pytest.raises(BoundaryError, match="invalid_frame_index"):
        decode_row(tampered, validator=compiler, expected_sha256=row_sha256(tampered))


def _frame(**changes):
    frame = FrameCompiler(public_vocabulary(V2_INPUT_SPEC)).compile(public_state())
    tensors = {**frame.tensors, **changes.pop("tensors", {})}
    return TensorFrame(tensors, {**frame.reductions, **changes.pop("reductions", {})},
                       changes.pop("choices", frame.choices),
                       changes.pop("entity_count", frame.entity_count))


def _rewrite_header(row, change):
    """Re-serialize the header after `change(header)`; the body stays byte-identical."""
    (length,) = struct.unpack_from("<I", row, 8)
    body = row[12 + length + (-(12 + length) % 8):]
    header = json.loads(row[12:12 + length])
    change(header)
    raw = json.dumps(header, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":")).encode()
    return MAGIC + struct.pack("<I", len(raw)) + raw + b"\0" * (-(12 + len(raw)) % 8) + body


def _bits(tensor):
    return tensor.numpy().view(np.uint32 if tensor.dtype == torch.float32 else np.int64)


def _assert_bitwise(left, right):
    assert (left.choices, left.entity_count) == (right.choices, right.entity_count)
    assert left.tensors.keys() == right.tensors.keys()
    for key, tensor in left.tensors.items():
        assert tensor.dtype == right.tensors[key].dtype, key
        assert tensor.shape == right.tensors[key].shape, key
        assert np.array_equal(_bits(tensor), _bits(right.tensors[key])), key
    for key, reduction in left.reductions.items():
        other = right.reductions[key]
        assert torch.equal(reduction.inverse, other.inverse), key
        for before, after in zip(reduction.buckets, other.buckets, strict=True):
            assert before[:2] == after[:2] and torch.equal(before[2], after[2]), key


@pytest.mark.parametrize("width", [2**26, 2**40])
def test_review_m1_header_text_width_cannot_inflate_decode(width):
    compiler = FrameCompiler(public_vocabulary(V2_INPUT_SPEC))
    row = encode_row(compiler.compile(public_state()))
    bad = _rewrite_header(row, lambda header: header.update(text_width=width))
    with pytest.raises(BoundaryError, match="row_text_shape"):
        decode_row(bad, validator=compiler, expected_sha256=row_sha256(bad))


def test_review_m2_non_string_dtype_is_a_boundary_error():
    row = encode_row(_frame())

    def change(header):
        header["arrays"][0][1] = ["u1"]

    bad = _rewrite_header(row, change)
    with pytest.raises(BoundaryError, match="row_header_arrays"):
        decode_row(bad, validator=None, expected_sha256=None)


def test_float_specials_round_trip_bit_for_bit():
    specials = np.array([0x80000000, 0x7FC01234, 0x7F800001, 0x7F800000, 0xFF800000],
                        dtype=np.uint32).view(np.float32)  # -0.0, NaN payload, sNaN, +-inf
    frame = _frame()
    numbers = torch.from_numpy(specials.copy())
    fields = frame.tensors["number_fields"][:1].repeat(len(specials))
    text_numbers = frame.tensors["text_numbers"].clone()
    first = int(frame.tensors["text_lengths"][0])
    text_numbers.numpy()[0, :first, 0] = specials[:first]
    special = _frame(tensors={"numbers": numbers, "number_fields": fields,
                              "text_numbers": text_numbers})
    decoded = decode_row(encode_row(special), validator=None, expected_sha256=None)
    _assert_bitwise(special, decoded)


def test_integer_width_boundaries_round_trip():
    values = [255, 256, 65535, 65536, 2**31 - 1, 2**31, -1, -2**31, -2**31 - 1,
              -2**63, 2**63 - 1]
    frame = _frame()
    for value in values:
        kinds = frame.tensors["kinds"].clone()
        kinds[0] = value
        changed = _frame(tensors={"kinds": kinds})
        decoded = decode_row(encode_row(changed), validator=None, expected_sha256=None)
        _assert_bitwise(changed, decoded)


def test_zero_texts_and_empty_catalog_round_trip():
    empty = torch.zeros(0, dtype=torch.long)
    no_text = _frame(tensors={
        "text_ids": torch.zeros((0, 0), dtype=torch.long),
        "text_numbers": torch.zeros((0, 0, 1), dtype=torch.float32),
        "text_lengths": empty, "text_fields": empty, "text_indices": empty})
    _assert_bitwise(no_text, decode_row(encode_row(no_text), validator=None,
                                        expected_sha256=None))
    no_choice = _frame(tensors={"actions": empty}, choices=(), reductions={
        "source": Reduction((), empty), "target": Reduction((), empty)})
    decoded = decode_row(encode_row(no_choice), validator=None, expected_sha256=None)
    _assert_bitwise(no_choice, decoded)
    assert decoded.choices == () and decoded.reductions["source"].buckets == ()


def test_encode_rejects_frames_compile_frame_never_produces():
    frame = _frame()
    with pytest.raises(BoundaryError, match="frame_tensor_dtype"):
        encode_row(_frame(tensors={"kinds": frame.tensors["kinds"].reshape(1, -1)}))
    ids, numbers = frame.tensors["text_ids"], frame.tensors["text_numbers"]
    wider = {"text_ids": torch.nn.functional.pad(ids, (0, 1)),
             "text_numbers": torch.nn.functional.pad(numbers, (0, 0, 0, 1))}
    with pytest.raises(BoundaryError, match="frame_text_shape"):
        encode_row(_frame(tensors=wider))
    lengths = frame.tensors["text_lengths"].clone()
    lengths[0] = 0
    with pytest.raises(BoundaryError, match="frame_text_shape"):
        encode_row(_frame(tensors={"text_lengths": lengths}))
    with pytest.raises(BoundaryError, match="frame_entity_count"):
        encode_row(_frame(entity_count=float(frame.entity_count)))
