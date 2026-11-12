"""Whole-segment admission, global batch membership and actual auxiliary uses."""
from __future__ import annotations

import random

import pytest

from stpd.rl.segments import AuxiliaryPool, SegmentConfig, SegmentQueue, minibatch_members


def committed(queue, actor, sequence, run, rows=64):
    identity = f"{actor}/{sequence:08d}"
    assert queue.reserve(identity)
    value = {"segment_id": identity, "run_id": run, "rows": rows,
             "artifact_id": f"immutable-{identity}"}
    queue.commit(identity, value)
    return value


def test_whole_segments_overshoot_is_balanced_and_no_row_disappears():
    queue = SegmentQueue(SegmentConfig())
    for index in range(15):
        committed(queue, f"actor-{index}", 0, f"run-{index}")
    committed(queue, "tail", 0, "tail", 63)
    assert queue.dataset() is None  # 1023, not an elastic change to the target.
    committed(queue, "last", 0, "last", 64)
    dataset = queue.dataset()
    assert sum(row["rows"] for row in dataset) == 1087
    groups = minibatch_members(1087)
    assert [len(group) for group in groups] == [272, 272, 272, 271]
    assert [row for group in groups for row in group] == list(range(1087))
    assert queue.rows == 0


def test_one_capacity_count_and_full_queue_still_accepts_existing_segment():
    config = SegmentConfig(dataset_rows=64, capacity_rows=128)
    queue = SegmentQueue(config)
    assert queue.reserve("a/00000000") and queue.reserve("b/00000000")
    assert queue.occupied == 128
    assert not queue.reserve("c/00000000")
    queue.commit("a/00000000", {"segment_id": "a/00000000", "run_id": "one", "rows": 64})
    assert queue.occupied == 128
    assert queue.dataset() is not None
    assert queue.reserve("c/00000000")
    queue.release("b/00000000")
    with pytest.raises(ValueError, match="already_offered"):
        queue.reserve("b/00000000")


def test_single_game_share_and_resume_keep_undelivered_reservations():
    config = SegmentConfig(dataset_rows=256, capacity_rows=1024)
    queue = SegmentQueue(config)
    for sequence in range(3):
        committed(queue, "a", sequence, "one")
    committed(queue, "b", 0, "two")
    assert queue.reserve("c/00000000")
    assert queue.dataset() is None  # one game contributes at most128.
    committed(queue, "d", 0, "three")
    restored = SegmentQueue(config)
    restored.restore(queue.state())
    dataset = restored.dataset()
    assert sum(row["rows"] for row in dataset) == 256
    assert sum(row["rows"] for row in dataset if row["run_id"] == "one") == 128
    assert restored.leases == {"c/00000000": 64}
    assert restored.rows == 64


def test_terminal_two_uses_two_datasets_and_HP_one_use():
    terminal = AuxiliaryPool(groups=2, uses=2)
    terminal.add("game-a", [{"key": "a", "label": 49}])
    rows = terminal.sample(128, random.Random(1))
    assert rows[0]["uses"] == 0  # reserving or a KL stop does not consume it
    terminal.used(rows, 1)
    assert rows[0]["uses"] == 1 and rows[0]["datasets"] == [1]
    terminal.used(terminal.sample(128, random.Random(2)), 2)
    assert rows[0]["uses"] == 2 and rows[0]["datasets"] == [1, 2]
    assert terminal.sample(128, random.Random(3)) == []
    assert terminal.retire_exhausted()["game-a"][0]["label"] == 49
    assert not terminal.groups
    hp = AuxiliaryPool(groups=128, uses=1)
    hp.add("battle", [{"key": "hp", "label": .37}])
    selected = hp.sample(128, random.Random(1))
    hp.used(selected, 1)
    assert hp.sample(128, random.Random(2)) == []
    assert selected[0]["label"] == .37 and selected[0]["uses"] == 1


def test_aux_reservation_excludes_current_dataset_without_charging_it():
    pool = AuxiliaryPool(groups=2, uses=1)
    pool.add("battle", [{"key": "a"}, {"key": "b"}])
    assert [row["key"] for row in pool.sample(128, random.Random(1), exclude={"a"})] == ["b"]
    assert all(row["uses"] == 0 for row in pool.groups["battle"])


def test_archival_pool_reader_preserves_recorded_uses_and_labels():
    pool = AuxiliaryPool(groups=2, uses=4)
    pool.add("old-game", [{"key": "a", "outcome": 18}])
    rows = pool.sample(128, random.Random(1), epochs=2)
    pool.used(rows, 1, epochs=2)
    historical = pool.state()
    restored = AuxiliaryPool(groups=2, uses=4)
    restored.restore(historical)
    assert restored.groups["old-game"][0]["uses"] == 1
    assert restored.groups["old-game"][0]["datasets"] == [1]
    assert restored.groups["old-game"][0]["outcome"] == 18


@pytest.mark.parametrize("uses", [1, 2])
def test_incremental_retire_equals_the_full_scan(uses):
    """`retire_exhausted(rows)` (the rows just charged) retires exactly what the full
    scan retires, in pool order, through evictions, restores and external pops."""
    rng = random.Random(20261113 + uses)
    fast, full = AuxiliaryPool(groups=24, uses=uses), AuxiliaryPool(groups=24, uses=uses)
    added = 0
    for dataset in range(200):
        for _ in range(rng.randrange(4)):
            rows = [{"key": f"g{added}:{i}"} for i in range(rng.randrange(1, 6))]
            fast.add(f"g{added}", rows)
            full.add(f"g{added}", rows)
            added += 1
        if dataset % 37 == 36:  # restore rebuilds the index from a CP state
            fast.restore(fast.state())
        if dataset % 53 == 52 and fast.groups:  # an owner pops a group directly
            identity = next(iter(fast.groups))
            fast.groups.pop(identity)
            full.groups.pop(identity)
        fast_rows = fast.sample(rng.randrange(1, 12), random.Random(dataset))
        full_rows = full.sample(len(fast_rows), random.Random(dataset))
        assert [r["key"] for r in fast_rows] == [r["key"] for r in full_rows]
        fast.used(fast_rows, dataset)
        full.used(full_rows, dataset)
        assert fast.retire_exhausted(fast_rows) == full.retire_exhausted()
        assert fast.state() == full.state()
        assert all(r["uses"] < uses for rows in fast.groups.values() for r in rows)
