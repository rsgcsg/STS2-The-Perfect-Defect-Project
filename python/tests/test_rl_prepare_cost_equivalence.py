"""Vectorized joint reductions, text deduplication and role metadata equal the originals.

The joint reference is the live `tensor_input.joint_nodes` (unchanged; it is a
rows-encoder reference source); the text reference is the pre-change per-text
key loop. Every output is
compared by dtype, shape, stride, storage offset and bytes, on fixture frames
(alone, repeated, mixed, decoded from rows-v2) and on edge inputs that must take
the per-text fallback.
"""
from __future__ import annotations

import pytest
import torch
from test_p1_input_r18 import claws, trial
from test_p1_input_r19 import sphere
from test_p1_input_v2 import public_state

import stpd.rl.batch_preparation as batch_preparation
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.input import V2_INPUT_SPEC, V2_R18_INPUT_SPEC, V2_R19_INPUT_SPEC
from stpd.p1.rows_encoder_wire import decode_row, encode_row
from stpd.p1.tensor_input import Reduction, TensorFrame, joint_nodes, text_batch
from stpd.p1.vocabulary import public_vocabulary


def reference_unique(frames: list[TensorFrame]) -> tuple[list[int], list[int]]:
    keys = tuple(key for frame in frames for key in frame.text_keys)
    unique: dict[bytes, int] = {}
    selection: list[int] = []
    inverse: list[int] = []
    for i, text_key in enumerate(keys):
        if text_key not in unique:
            unique[text_key] = len(selection)
            selection.append(i)
        inverse.append(unique[text_key])
    return selection, inverse


def assert_same_tensor(left: torch.Tensor, right: torch.Tensor) -> None:
    assert (left.dtype, left.shape, left.stride(), left.storage_offset()) == (
        right.dtype, right.shape, right.stride(), right.storage_offset())
    assert left.untyped_storage().nbytes() == right.untyped_storage().nbytes()
    assert torch.equal(left, right)


def assert_same(left, right) -> None:  # noqa: ANN001 - nested tensors and containers
    if isinstance(left, torch.Tensor):
        assert_same_tensor(left, right)
    elif isinstance(left, dict):
        assert list(left) == list(right)
        for key in left:
            assert_same(left[key], right[key])
    elif isinstance(left, (list, tuple)):
        assert type(left) is type(right) and len(left) == len(right)
        for a, b in zip(left, right, strict=True):
            assert_same(a, b)
    elif isinstance(left, Reduction):
        assert_same(left.buckets, right.buckets)
        assert_same(left.inverse, right.inverse)
    else:
        assert left == right


CASES = [(public_state, V2_INPUT_SPEC), (trial, V2_R18_INPUT_SPEC),
         (claws, V2_R18_INPUT_SPEC), (sphere, V2_R19_INPUT_SPEC)]


def fixture_frames() -> list[list[TensorFrame]]:
    batches = []
    for source, spec in CASES:
        frame = FrameCompiler(public_vocabulary(spec)).compile(source())
        decoded = decode_row(encode_row(frame), validator=None, expected_sha256=None)
        batches += [[frame], [frame, decoded, frame], [decoded] * 5]
    trial_frame = FrameCompiler(public_vocabulary(V2_R18_INPUT_SPEC)).compile(trial())
    claws_frame = FrameCompiler(public_vocabulary(V2_R18_INPUT_SPEC)).compile(claws())
    batches.append([trial_frame, claws_frame, trial_frame, claws_frame])  # mixed widths
    return batches


@pytest.mark.parametrize("index", range(13))
def test_joint_reductions_and_prepared_groups_equal_the_reference(index, monkeypatch):
    frames = fixture_frames()[index]
    fast, oracle = batch_preparation._joint_nodes(frames), joint_nodes(frames)
    assert_same(fast.tensors, oracle.tensors)
    assert_same(fast.reductions, oracle.reductions)
    batch = text_batch(frames)
    assert batch_preparation._unique_texts(frames, *batch) == reference_unique(frames)
    floors = torch.zeros(len(frames), dtype=torch.long)
    for layout in ("padded-v1", "rows-v2"):
        new = batch_preparation.prepare_group(frames, tuple(range(len(frames))), True, floors,
                                              deduplicate_text=True, layout=layout)
        with monkeypatch.context() as patch:
            patch.setattr(batch_preparation, "_unique_texts",
                          lambda frames, *_: reference_unique(frames))
            old = batch_preparation.prepare_group(frames, tuple(range(len(frames))), True,
                                                  floors, deduplicate_text=True, layout=layout)
        assert_same(new.tensors, old.tensors)


def reference_role_metadata(frames, key, scene_width, catalog_width, physical_rows):  # noqa: ANN001
    """The pre-change row-by-row `_role_metadata`."""
    rows: list[list[int]] = []
    for batch, frame in enumerate(frames):
        reduction = frame.reductions[key]
        unpacked: list[list[int]] = []
        for width, count, indices in reduction.buckets:
            unpacked.extend((indices.reshape(count, width) + batch * scene_width).tolist())
        inverse = reduction.inverse.tolist()
        rows.extend([unpacked[index] for index in inverse]
                    + [[] for _ in range(catalog_width - len(frame.choices))])
    rows.extend([[] for _ in range((physical_rows - len(frames)) * catalog_width)])
    width = batch_preparation._bucket(max(map(len, rows), default=0), (1, 2, 4, 8, 16, 32, 64))
    indices = torch.zeros((len(rows), width), dtype=torch.long)
    weights = torch.zeros((len(rows), width), dtype=torch.float32)
    for row, values in enumerate(rows):
        if values:
            indices[row, : len(values)] = torch.tensor(values)
            weights[row, : len(values)] = 1.0 / len(values)
    return indices, weights


@pytest.mark.parametrize("index", range(13))
def test_role_metadata_equals_the_row_by_row_reference(index):
    frames = fixture_frames()[index]
    catalog = max(len(frame.choices) for frame in frames)
    scene = 1 + max(frame.entity_count for frame in frames)
    used = False
    for key in ("source", "target"):
        for width, rows in ((catalog, len(frames)), (catalog + 3, len(frames) + 2)):
            args = (frames, key, scene, width, rows)
            expected = reference_role_metadata(*args)
            assert_same(batch_preparation._role_metadata(*args), expected)
            used |= bool(expected[1].any())
    assert used  # the fixture binds real scene indices, not only empty rows
    # No frames at all: only empty physical rows.
    args = ([], "source", scene, catalog, 2)
    assert_same(batch_preparation._role_metadata(*args), reference_role_metadata(*args))


def with_text(frame: TensorFrame, ids: torch.Tensor, numbers: torch.Tensor,
              lengths: torch.Tensor) -> TensorFrame:
    tensors = dict(frame.tensors, text_ids=ids, text_numbers=numbers, text_lengths=lengths)
    return TensorFrame(tensors, frame.reductions, frame.choices, frame.entity_count)


def test_text_dedup_edges_keep_reference_selection_and_fallback():
    base = FrameCompiler(public_vocabulary(V2_INPUT_SPEC)).compile(public_state())
    ids, numbers = base.tensors["text_ids"].clone(), base.tensors["text_numbers"].clone()
    lengths = base.tensors["text_lengths"].clone()
    # Negative zero is a different number than zero; dirty padding must not merge.
    signed = numbers.clone()
    signed[0, 0, 0] = -0.0
    dirty = ids.clone()
    short = int(lengths.argmin())
    assert int(lengths[short]) < ids.shape[1]
    dirty[short, int(lengths[short])] = 7
    cases = [[base, with_text(base, ids, signed, lengths)],
             [base, with_text(base, dirty, numbers, lengths)],
             [with_text(base, ids, numbers, lengths), base, base]]
    # -0.0 in the padding of a short text: the key ignores padding, so the texts still
    # merge; only an integer-view padding check sees -0.0 and takes the exact path.
    padded_negative = numbers.clone()
    padded_negative[short, int(lengths[short]), 0] = -0.0
    cases.append([base, with_text(base, ids, padded_negative, lengths)])
    for frames in cases:
        assert batch_preparation._unique_texts(frames, *text_batch(frames)) \
            == reference_unique(frames)


def test_length_beyond_own_width_keeps_distinct_keys_on_the_exact_path():
    base = FrameCompiler(public_vocabulary(V2_INPUT_SPEC)).compile(public_state())
    numbers = torch.zeros((1, 2, 1), dtype=torch.float32)
    # A: width 2, length 3 (its key clamps to 2 ids); B: width 3, length 3 (3 ids).
    # Zero-padded to width 3 the two rows are equal, but their keys differ.
    narrow = with_text(base, torch.tensor([[5, 6]]), numbers, torch.tensor([3]))
    wide = with_text(base, torch.tensor([[5, 6, 0]]), torch.zeros((1, 3, 1)),
                     torch.tensor([3]))
    expected = reference_unique([narrow, wide])
    assert expected == ([0, 1], [0, 1])
    narrow, wide = (with_text(base, frame.tensors["text_ids"], frame.tensors["text_numbers"],
                              frame.tensors["text_lengths"]) for frame in (narrow, wide))
    frames = [narrow, wide]
    assert batch_preparation._unique_texts(frames, *text_batch(frames)) == expected
    assert all("text_keys" in frame.metadata_cache for frame in frames)  # exact path


def test_host_cached_frames_take_the_exact_path():
    base = FrameCompiler(public_vocabulary(V2_INPUT_SPEC)).compile(public_state())
    expected = reference_unique([base, base])
    host = TensorFrame(base.tensors, base.reductions, base.choices, base.entity_count)
    cached = TensorFrame(base.tensors, base.reductions, base.choices, base.entity_count,
                         host=host)
    frames = [cached, cached]
    assert batch_preparation._unique_texts(frames, *text_batch(frames)) == expected
    assert "text_keys" in host.metadata_cache  # keys came from the host metadata


def test_hash_collision_takes_the_exact_per_text_path(monkeypatch):
    expected = reference_unique(fixture_frames()[1])
    frames = fixture_frames()[1]  # fresh frames: no cached text keys yet
    zeros = batch_preparation._text_hash_multipliers(1) * 0  # every row hashes to 0
    monkeypatch.setattr(batch_preparation, "_text_hash_multipliers", lambda _words: zeros)
    assert len(set(expected[1])) > 1  # distinct texts really collide
    assert batch_preparation._unique_texts(frames, *text_batch(frames)) == expected
    assert all("text_keys" in frame.metadata_cache for frame in frames)  # fell back


def test_joint_reduction_rejects_bucket_counts_that_do_not_cover_the_inverse():
    frame = FrameCompiler(public_vocabulary(V2_INPUT_SPEC)).compile(public_state())
    reduction = frame.reductions["ids"]
    width, count, indices = reduction.buckets[0]
    short = Reduction(((width, count - 1, indices[:-width or None]),
                       *reduction.buckets[1:]), reduction.inverse)
    broken = TensorFrame(frame.tensors, dict(frame.reductions, ids=short), frame.choices,
                         frame.entity_count)
    with pytest.raises(ValueError, match="joint_reduction_inverse_length"):
        batch_preparation._joint_nodes([broken])
