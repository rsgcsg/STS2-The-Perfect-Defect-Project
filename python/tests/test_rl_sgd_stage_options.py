"""V3 酉 staging options: lean tree walks, split verification, stream reuse,
child switch interval. Every option is opt-in; defaults keep the earlier path."""

from __future__ import annotations

import mmap
import os
import pickle
from pathlib import Path

import pytest
import torch
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
from test_rl_sgd_data_parallel import PARALLEL, close_tree, equal_tree
from test_rl_sgd_process import _first_handoff, terminal_owner, train

from stpd.rl import batch_preparation as bp
from stpd.rl.binary_preparation import (
    PREPARED_DIGEST_CHUNK,
    prepared_chunk_count,
    prepared_chunk_digests,
    prepared_digest,
    prepared_digest_combine,
)
from stpd.rl.data_parallel import shard_transport
from stpd.rl.sgd_process import _DataParallel, _StageConfig, mapped_arrays, sgd_options

FAST = {"sgd_stage_stream_reuse": True, "sgd_stage_lean_tree": True,
        "sgd_verify_split": True, "sgd_verify_threads": 2,
        "sgd_switch_interval_seconds": 0.0005}


def test_stage_options_are_explicit_and_default_to_the_earlier_path():
    base = {"sgd_process": True}
    options = sgd_options(base)
    assert options.stage_init() == {"stream_reuse": False, "lean_tree": False,
                                    "verify_split": False, "verify_threads": 4,
                                    "switch_interval": None}
    assert _StageConfig.from_init(options.stage_init()) == _StageConfig()
    chosen = sgd_options({**base, **FAST})
    assert chosen.stage_init() == {"stream_reuse": True, "lean_tree": True,
                                   "verify_split": True, "verify_threads": 2,
                                   "switch_interval": 0.0005}
    for key, bad in (("sgd_stage_stream_reuse", 1), ("sgd_stage_lean_tree", "yes"),
                     ("sgd_verify_split", None), ("sgd_verify_threads", 0),
                     ("sgd_verify_threads", 2.0), ("sgd_verify_threads", True),
                     ("sgd_switch_interval_seconds", 0), ("sgd_switch_interval_seconds", "1"),
                     ("sgd_switch_interval_seconds", float("nan"))):
        with pytest.raises(ValueError, match="invalid_" + key):
            sgd_options({**base, key: bad})
    with pytest.raises(ValueError, match="without_explicit_selection"):
        sgd_options({"sgd_verify_split": True})


@pytest.mark.parametrize("length", [0, 1, PREPARED_DIGEST_CHUNK, 3 * PREPARED_DIGEST_CHUNK + 7])
def test_chunk_digests_from_any_split_combine_to_the_whole_file_digest(length):
    data = memoryview(os.urandom(length))
    whole = prepared_digest(data, 3)
    count = prepared_chunk_count(length)
    for world in (1, 2, 3):
        parts: dict[int, bytes] = {}
        for rank in range(world):
            parts.update(prepared_chunk_digests(data, range(rank, count, world), 2))
        assert prepared_digest_combine(length, [parts[i] for i in range(count)]) == whole
    if count:
        with pytest.raises(ValueError, match="incomplete"):
            prepared_digest_combine(length, [parts[i] for i in range(count - 1)])
        with pytest.raises(ValueError, match="out_of_range"):
            prepared_chunk_digests(data, [count])


def _combiner(sent):
    parallel = _DataParallel.__new__(_DataParallel)
    parallel.send = lambda kind, **fields: sent.append((kind, fields))
    return parallel


def test_split_verification_refuses_a_changed_or_missing_chunk(monkeypatch):
    def exit_(code):
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", exit_)
    length = 2 * PREPARED_DIGEST_CHUNK + 5
    data = bytearray(os.urandom(length))
    expected = prepared_digest(memoryview(data))
    own = {"length": length, "parts": prepared_chunk_digests(memoryview(data), [0, 2])}
    other = {"length": length, "parts": prepared_chunk_digests(memoryview(data), [1])}
    sent: list = []
    _combiner(sent)._verify_split(1, expected, own, {1: other})
    assert sent == []  # verified: the caller answers `staged`
    data[PREPARED_DIGEST_CHUNK + 3] ^= 1  # the follower's chunk changed on disk
    changed = {"length": length, "parts": prepared_chunk_digests(memoryview(data), [1])}
    for followers, code in (({1: changed}, "sgd_prepared_file_integrity_changed"),
                            ({1: {"length": length, "parts": {}}},
                             "sgd_verify_split_chunks_missing"),
                            ({1: None}, "sgd_verify_split_chunks_missing"),
                            ({1: {"length": length, "parts": own["parts"]}},
                             "sgd_verify_split_chunks_missing")):
        sent.clear()
        with pytest.raises(SystemExit):
            _combiner(sent)._verify_split(1, expected, own, followers)
        assert [kind for kind, _ in sent] == ["failed"]
        assert sent[0][1]["error_code"] == code


def _lean_free(node):
    assert not isinstance(node, bp._TensorView)
    return node


def test_lean_tree_walks_give_the_same_staged_values_on_a_real_handoff(tmp_path, monkeypatch):
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=3, dataplane=PARALLEL)
    try:
        handoff = _first_handoff(owner)
        with open(handoff.path, "rb") as stream:
            mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        arrays = mapped_arrays(mapping, [(a.offset, a.count, a.dtype) for a in handoff.arrays])
        tree = pickle.loads(handoff.tree)
        copies = tuple(a.copy() for a in arrays)
        full = bp.unpack_transport((tree, copies))
        lean = bp.unpack_transport((pickle.loads(handoff.tree), copies), lean=True)
        equal_tree(full, lean)
        bp._map_structure(lean, _lean_free)  # no packed view is left unmapped
        for rank in range(2):
            parts = [shard_transport(pickle.loads(handoff.tree), arrays, rank, 2, lean=flag)
                     for flag in (False, True)]
            assert [p.nbytes for p in parts[0][1]] == [p.nbytes for p in parts[1][1]]
            unpacked = [bp.unpack_transport((t, tuple(a.copy() for a in a_)), lean=flag)
                        for (t, a_), flag in zip(parts, (False, True), strict=True)]
            equal_tree(*unpacked)
            bp._map_structure(unpacked[1], _lean_free)
        del arrays, full, lean, parts, unpacked
    finally:
        owner.close()


def test_lean_tree_on_a_chain_sized_batch_tree():
    from tools.bench_sgd_stage import build_batch

    batch = build_batch(2, 4126, 4, 2)
    tree, arrays = bp.pack_transport(batch)
    raw = pickle.dumps(tree, protocol=5)
    for rank in range(2):
        full = shard_transport(pickle.loads(raw), arrays, rank, 2)
        lean = shard_transport(pickle.loads(raw), arrays, rank, 2, lean=True)
        equal_tree(bp.unpack_transport((full[0], tuple(a.copy() for a in full[1]))),
                   bp.unpack_transport((lean[0], tuple(a.copy() for a in lean[1])), lean=True))


def test_two_ranks_with_every_stage_option_match_the_earlier_path(tmp_path, monkeypatch):
    """Same batches, order and updates: only the staging path differs."""
    results = {}
    for name, extra in (("earlier", {}), ("fast", FAST)):
        owner, _, _ = terminal_owner(tmp_path / name, monkeypatch,
                                     dataplane={**PARALLEL, "sgd_stage_thread": True, **extra})
        try:
            events = train(owner, 3)
            owner.checkpoint(final=True)
            from test_rl_segment_learner import read_cp

            _, cp = read_cp(owner)
            results[name] = cp, events, dict(owner.timing)
        finally:
            owner.close()
    (one, one_events, _), (two, two_events, timing) = results["earlier"], results["fast"]
    assert one.counters == two.counters
    for left, right in zip(one_events, two_events, strict=True):
        for key in ("update", "first_use", "first_use_multi", "stepped", "kl_stopped"):
            assert left[key] == right[key], key
    for name, left in one.weights.items():
        close_tree(left, two.weights[name])
    assert timing["sgd_stage_verify_seconds"] > 0 and timing["sgd_stage_pin_seconds"] > 0


@pytest.mark.parametrize("split", [False, True])
def test_a_changed_byte_fails_loudly_before_any_update_with_split_verify(tmp_path, monkeypatch,
                                                                        split):
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=1, dataplane={
        **PARALLEL, "sgd_stage_thread": True, **FAST, "sgd_verify_split": split})
    try:
        handoff = _first_handoff(owner)
        path = Path(handoff.path)
        with path.open("r+b") as stream:
            first = stream.read(1)
            stream.seek(0)
            stream.write(bytes([first[0] ^ 1]))
        with pytest.raises(Exception) as caught:
            owner._prepare()
        child = caught.value.facts["child"]
        assert child["error_code"] == "sgd_prepared_file_integrity_changed"
        assert owner.updates == 0
    finally:
        owner.close()


def test_stage_batch_and_pinned_slabs_accept_a_fixed_stream_on_cpu():
    batch = bp.unpack_transport(bp.pack_transport({"x": torch.arange(4)}))
    assert bp.stage_batch(batch, torch.device("cpu"), stream=None, lean=True) == (batch, 0)
