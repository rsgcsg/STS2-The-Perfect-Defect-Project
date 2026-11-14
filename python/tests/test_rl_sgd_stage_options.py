"""V3 酉 staging options: lean tree walks, split verification, stream reuse,
child switch interval. Every option is opt-in; defaults keep the earlier path."""

from __future__ import annotations

import contextlib
import dataclasses
import mmap
import os
import pickle
import threading
import time
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
from stpd.rl.sgd_process import (
    _DataParallel,
    _SgdWorker,
    _StageConfig,
    mapped_arrays,
    sgd_options,
)

FAST = {"sgd_stage_stream_reuse": True, "sgd_stage_lean_tree": True,
        "sgd_verify_split": True, "sgd_step_probe": True, "sgd_gc_freeze": True}


def test_stage_options_are_explicit_and_default_to_the_earlier_path():
    base = {"sgd_process": True}
    options = sgd_options(base)
    assert options.stage_init() == {"stream_reuse": False, "lean_tree": False,
                                    "verify_split": False, "step_probe": False,
                                    "gc_freeze": False}
    assert _StageConfig.from_init(options.stage_init()) == _StageConfig()
    chosen = sgd_options({**base, **FAST})
    assert chosen.stage_init() == {"stream_reuse": True, "lean_tree": True,
                                   "verify_split": True, "step_probe": True,
                                   "gc_freeze": True}
    for key, bad in (("sgd_stage_stream_reuse", 1), ("sgd_stage_lean_tree", "yes"),
                     ("sgd_verify_split", None),
                     ("sgd_step_probe", "on"), ("sgd_gc_freeze", 1)):
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
                            ({1: {"length": length}}, "sgd_verify_split_chunks_missing"),
                            ({1: {"length": length, "parts": own["parts"]}},
                             "sgd_verify_split_chunks_missing")):
        sent.clear()
        with pytest.raises(SystemExit):
            _combiner(sent)._verify_split(1, expected, own, followers)
        assert [kind for kind, _ in sent] == ["failed"]
        assert sent[0][1]["error_code"] == code


def test_a_changed_follower_chunk_fails_through_stage_work_and_flush(tmp_path, monkeypatch):
    """End to end on a multi-chunk file: rank 1 hashes chunk 1 of the file it
    mapped through `_stage_work`; rank 0's `_flush` refuses before `staged`."""
    from tools.bench_sgd_stage import build_batch, stage_worker, write_handoff

    def exit_(code):
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", exit_)
    plan = write_handoff(build_batch(40, 1031, 2, 2), tmp_path)
    assert prepared_chunk_count(plan["used_bytes"]) >= 3
    with open(plan["path"], "r+b") as stream:  # a byte of chunk 1: rank 1's share
        stream.seek(PREPARED_DIGEST_CHUNK + 11)
        byte = stream.read(1)
        stream.seek(PREPARED_DIGEST_CHUNK + 11)
        stream.write(bytes([byte[0] ^ 1]))
    config = {"lean_tree": True, "verify_split": True}
    staged = [_SgdWorker._stage_work(stage_worker(rank, 2, config), plan)[1]
              for rank in range(2)]
    sent: list = []
    leader = _DataParallel.__new__(_DataParallel)
    leader.world, leader.send = 2, lambda kind, **fields: sent.append((kind, fields))
    leader.staged_lock, leader.deferred, leader.follower_ready = threading.Lock(), {}, {}
    leader.follower_staged(1, plan["dataset"], tuple(staged[1]["file_identity"]),
                           staged[1]["verify_parts"])
    with pytest.raises(SystemExit):
        leader.staged(plan["dataset"], staged[0])
    assert [kind for kind, _ in sent] == ["failed"]
    assert sent[0][1]["error_code"] == "sgd_prepared_file_integrity_changed"


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


def _tensor_paths(node, path=()):
    """Every tensor (or packed view) by path, walking every field regardless of hints."""
    if isinstance(node, (torch.Tensor, bp._TensorView)):
        yield path, node
    elif dataclasses.is_dataclass(node) and not isinstance(node, type):
        for field in dataclasses.fields(node):
            yield from _tensor_paths(getattr(node, field.name), (*path, field.name))
    elif isinstance(node, dict):
        for key, item in node.items():
            yield from _tensor_paths(item, (*path, key))
    elif isinstance(node, (tuple, list)):
        for index, item in enumerate(node):
            yield from _tensor_paths(item, (*path, index))


def _assert_lean_reaches_every_tensor(tree):
    expected = {id(node): path for path, node in _tensor_paths(tree)}
    assert expected
    reached: set[int] = set()

    def record(node):
        if isinstance(node, (torch.Tensor, bp._TensorView)):
            reached.add(id(node))
        return node

    bp._map_structure(tree, record, lean=True)
    missing = sorted(map(str, (path for key, path in expected.items() if key not in reached)))
    assert not missing, f"lean walk skips tensors (a field hint lies): {missing[:5]}"


def test_lean_walk_reaches_every_tensor_path_of_production_shaped_batches(tmp_path,
                                                                          monkeypatch):
    """Guard for hint-based skipping: a field declared tensor-free that holds a
    tensor (incl. 0-dim CPU tensors on the stage_batch path) fails here."""
    from tools.bench_sgd_stage import build_batch

    sources = [build_batch(1, 1031, 4, 2)]
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=3)
    try:
        handoff = _first_handoff(owner)
        with open(handoff.path, "rb") as stream:
            mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        arrays = mapped_arrays(mapping, [(a.offset, a.count, a.dtype) for a in handoff.arrays])
        packed = pickle.loads(handoff.tree)
        _assert_lean_reaches_every_tensor(packed)  # the stage_pinned_slabs/shard path
        sources.append(bp.unpack_transport((packed, tuple(a.copy() for a in arrays))))
        del arrays
    finally:
        owner.close()
    for batch in sources:
        _assert_lean_reaches_every_tensor(batch)  # the stage_batch path
        _assert_lean_reaches_every_tensor(bp.pack_transport(batch)[0])
    with pytest.raises(AssertionError, match="hint lies"):
        _assert_lean_reaches_every_tensor(_lying_batch(sources[0]))


def _lying_batch(batch):
    """A 0-dim tensor in a field declared tensor-free (here `prepared_at`)."""
    return dataclasses.replace(batch, prepared_at=torch.tensor(1.0))


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
    assert all("probe" not in e["sgd_timing"] for e in one_events)
    for event in two_events:  # `sgd_step_probe`: GC and allocator facts of every step
        probe = event["sgd_timing"]["probe"]
        assert probe["gc_seconds"] >= 0 and len(probe["gc_collections"]) == 3
        assert probe["gc_collections"][2] == len(probe["gc_full"])
        assert probe["allocator"] == {}  # CPU: no CUDA allocator
    # `sgd_gc_freeze`: one full collection at every dataset
    # boundary, reported with the next dataset's first step; the first one also
    # freezes. Never on the earlier path.
    assert all("gc_boundary" not in e["sgd_timing"] for e in one_events)
    reports = [e["sgd_timing"]["gc_boundary"] for e in two_events
               if "gc_boundary" in e["sgd_timing"]]
    firsts = [e for e in two_events if e["sgd_timing"]["boundary_seconds"] is not None]
    assert len(reports) == len(firsts) >= 2  # every dataset after the first
    assert reports[0]["froze"] and reports[0]["freezes"] == 1
    assert reports[0]["frozen"] > 10_000 and reports[0]["objects"] > 10_000
    for report in reports[1:]:  # what came after the freeze stays small; no frozen walk
        assert not report["froze"] and report["objects"] < 10_000, report
        assert "frozen" not in report, report
    # Automatic full collections are off; the CPU staging path's explicit
    # per-batch collect (the CUDA direct path has none) scans only post-freeze objects.
    frozen_at = next(i for i, e in enumerate(two_events) if "gc_boundary" in e["sgd_timing"])
    for event in two_events[frozen_at:]:
        for full in event["sgd_timing"]["probe"]["gc_full"]:
            assert full["objects"] < 10_000 and full["seconds"] >= 0, full
    for event in firsts:  # rank 0's boundary, every section in order
        timing = event["sgd_timing"]
        assert list(timing["boundary_sections"]) == [
            "last_step", "replica_check", "snapshot_capture", "snapshot_write",
            "snapshot_send", "gc_collect", "gc_freeze", "gc_other", "idle"]
        assert sum(timing["boundary_sections"].values()) == pytest.approx(
            timing["boundary_seconds"], abs=1e-9)


def test_dataset_boundary_sections_sum_to_the_boundary(tmp_path, monkeypatch):
    """Always on: contiguous stepping-thread sections, wall and thread CPU, from a
    dataset's last step end to the next dataset's start (its first step reports)."""
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=3)
    try:
        events = train(owner, 3)
    finally:
        owner.close()
    firsts = [e["sgd_timing"] for e in events if e["sgd_timing"]["boundary_seconds"] is not None]
    assert len(firsts) >= 2
    for timing in firsts:
        wall, cpu = timing["boundary_sections"], timing["boundary_sections_cpu"]
        assert list(wall) == list(cpu) == [
            "last_step", "snapshot_capture", "snapshot_write", "snapshot_send", "idle"]
        assert min(wall.values()) >= 0 and min(cpu.values()) >= 0
        assert sum(wall.values()) == pytest.approx(timing["boundary_seconds"], abs=1e-9)
    assert not any("boundary_sections" in e["sgd_timing"] for e in events
                   if e["sgd_timing"]["boundary_seconds"] is None)


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


def _deterministic_prestart(monkeypatch):
    """Prestart fires only if N+1 is already staged when N's next answer is awaited:
    a race on a slow runner. Before each prestart check, wait for N+1's preparation
    and its `staged` answer (earlier answers are kept, in order, for `expect`)."""
    from collections import deque

    from stpd.rl.segment_learner import SegmentLearner

    original = SegmentLearner._sgd_prestart

    def prestart(self):
        sgd = self.sgd
        if (sgd is not None and sgd.options.prestart and self.active_batch is not None
                and self.next_active is None and self.pending_batch is not None
                and not self.checkpoint_requested):
            self.pending_batch[1].result(timeout=120)
            self._sgd_plan_ahead()
            sequence = self.pending_batch[1].result().request.identity.sequence
            if sequence in sgd.planned and sequence not in sgd.staged:
                held = sgd.__dict__.setdefault("_held", deque())
                get = type(sgd)._get.__get__(sgd)
                if "_get" not in sgd.__dict__:
                    sgd._get = lambda *a, **k: held.popleft() if held else get(*a, **k)
                deadline = time.monotonic() + 120
                while sequence not in sgd.staged:
                    message = get(deadline, "staged", 120)
                    if message["kind"] == "staged":
                        sgd._staged(message)
                    else:
                        held.append(message)
        return original(self)

    monkeypatch.setattr(SegmentLearner, "_sgd_prestart", prestart)


def test_prestart_with_every_stage_option_still_equals_lockstep(tmp_path, monkeypatch):
    """Single rank: the options change only staging, so prestart stays bitwise equal
    to lockstep, including a mid-run CP."""
    from test_rl_sgd_prestart import assert_same, run

    _deterministic_prestart(monkeypatch)
    lockstep = run(tmp_path / "lockstep", monkeypatch, prestart=False, dataplane=FAST,
                   checkpoint_at=2)
    prestart = run(tmp_path / "prestart", monkeypatch, prestart=True, dataplane=FAST,
                   checkpoint_at=2)
    assert prestart["prestarted"] >= 1 and lockstep["prestarted"] == 0
    assert lockstep["captured"] == prestart["captured"]
    assert_same(lockstep, prestart)
    assert prestart["owner"].sgd.exit["exitcode"] == 0


def test_prestart_two_ranks_with_every_stage_option_match_lockstep(tmp_path, monkeypatch):
    from test_rl_sgd_prestart import assert_same, run

    _deterministic_prestart(monkeypatch)
    lockstep = run(tmp_path / "lockstep", monkeypatch, prestart=False,
                   dataplane={**PARALLEL, **FAST})
    prestart = run(tmp_path / "prestart", monkeypatch, prestart=True,
                   dataplane={**PARALLEL, **FAST})
    assert prestart["prestarted"] >= 1
    assert_same(lockstep, prestart, exact=False)
    assert prestart["owner"].sgd.exit["exitcode"] == 0


def test_a_stall_records_every_SGD_process_thread_stacks(tmp_path, monkeypatch):
    """A follower that stops answering hangs rank 0 in the gradient all-reduce; the
    controller's stall record carries rank 0's live thread stacks (the stopped
    follower dumps once it is continued), then the child is killed as before."""
    import signal

    from stpd.rl.sgd_process import STACKS_PREFIX, SgdProcessFailed

    if not hasattr(signal, "SIGUSR1"):
        pytest.skip("no SIGUSR1")
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=2, dataplane={
        **PARALLEL, "sgd_stall_seconds": 3})
    follower = None
    try:
        assert owner._prepare()
        owner._step()
        files = {p.stem.split("-")[1]: int(p.stem.rsplit("-", 1)[1])
                 for p in owner.sgd.root.glob(STACKS_PREFIX + "*.txt")}
        assert set(files) == {"rank0", "rank1"} and files["rank0"] == owner.sgd.pid
        follower = files["rank1"]
        os.kill(follower, signal.SIGSTOP)
        with pytest.raises(SgdProcessFailed) as raised:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:  # whichever step needs rank 1 first
                if owner.active is None:
                    assert owner._prepare()
                owner._step()
        facts = raised.value.facts
        assert facts["code"] == "sgd_process_stalled"
        rank0 = facts["stacks"][f"{STACKS_PREFIX}rank0-{files['rank0']}"]
        assert "run_dataset" in rank0 and "Thread" in rank0, rank0
    finally:
        if follower is not None:
            with contextlib.suppress(OSError):
                os.kill(follower, signal.SIGKILL)
        owner.close()
