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


def test_auxiliary_counts_are_successful_steps_and_survive_checkpoint():
    pool = AuxiliaryPool(groups=2, uses=4)
    pool.add("game-a", [{"key": "a"}, {"key": "b"}])
    rows = pool.sample(256, random.Random(1))
    assert all(row["uses"] == 0 for row in rows)  # selection or KL stop is not use.
    pool.used(rows, 0)
    pool.used(rows, 0)
    assert all(row["uses"] == 2 for row in rows)
    next_dataset = pool.sample(256, random.Random(1))
    pool.used(next_dataset, 1)
    pool.used(next_dataset, 1)
    assert pool.sample(256, random.Random(1)) == []
    restored = AuxiliaryPool(groups=2, uses=4)
    restored.restore(pool.state())
    assert restored.state() == pool.state()
    pool.add("game-b", [{"key": "c"}])
    pool.add("game-c", [{"key": "d"}])
    assert list(pool.groups) == ["game-b", "game-c"]


def test_hp_is_used_at_most_twice_without_replacing_original_labels():
    pool = AuxiliaryPool(groups=128, uses=2)
    pool.add("battle-a", [{"key": "one", "label": 0.37}])
    rows = pool.sample(256, random.Random(1))
    pool.used(rows, 0)
    pool.used(rows, 0)
    assert pool.sample(256, random.Random(1)) == []
    assert pool.groups["battle-a"][0]["label"] == 0.37


def test_kl_partial_epochs_do_not_extend_terminal_or_hp_dataset_lifetimes():
    terminal = AuxiliaryPool(groups=256, uses=4)
    terminal.add("one", [{"key": "state"}])
    rows = terminal.sample(1, random.Random(1))
    terminal.used(rows, 0)  # only epoch one succeeded; the rest hit the KL stop.
    terminal.used(terminal.sample(1, random.Random(1)), 1)
    assert rows[0]["uses"] == 2 and rows[0]["datasets"] == [0, 1]
    assert terminal.sample(1, random.Random(1)) == []
    hp = AuxiliaryPool(groups=128, uses=2)
    hp.add("battle", [{"key": "state"}])
    hp.used(hp.sample(1, random.Random(1)), 0)
    assert hp.sample(1, random.Random(1)) == []
