"""`stpd/rows-v2-row-1` round-trips the real compiled frame and rejects tampering."""
from __future__ import annotations

import struct

import pytest
import torch
from test_p1_frame_compiler import assert_frame_equal
from test_p1_input_r18 import claws, trial
from test_p1_input_r19 import sphere
from test_p1_input_v2 import public_state

from spireagent.json_boundary import BoundaryError
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.input import V2_INPUT_SPEC, V2_R18_INPUT_SPEC, V2_R19_INPUT_SPEC
from stpd.p1.rows_encoder_wire import FORMAT, decode_row, encode_row, row_sha256
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
        decode_row(row, expected_sha256="0" * 64)
    with pytest.raises(BoundaryError, match="row_magic"):
        decode_row(b"X" + row[1:])
    with pytest.raises(BoundaryError, match="row_length_mismatch"):
        decode_row(row + b"\0" * 8)
    with pytest.raises(BoundaryError, match="row_length_mismatch"):
        decode_row(row[:-8])
    (length,) = struct.unpack_from("<I", row, 8)
    header = row[12:12 + length]
    assert FORMAT.encode() in header
    with pytest.raises(BoundaryError, match="row_header_fields"):
        decode_row(row.replace(FORMAT.encode(), b"stpd/rows-v2-row-0"))
    if (12 + length) % 8:
        with pytest.raises(BoundaryError, match="row_padding_nonzero"):
            decode_row(row[:12 + length] + b"\1" + row[13 + length:])
    # A kinds index past the vocabulary only fails with the structural validator.
    frame = compiler.compile(public_state())
    frame.tensors["kinds"][0] = 10**9
    tampered = encode_row(frame)
    decode_row(tampered)
    with pytest.raises(BoundaryError, match="invalid_frame_index"):
        decode_row(tampered, validator=compiler)
