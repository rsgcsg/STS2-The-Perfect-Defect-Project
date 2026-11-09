"""WP8: SegmentLearner consumes fast_chain segments (admission, exclusion, capacity, switch)."""
from __future__ import annotations

import dataclasses
import multiprocessing
import time
from typing import Any

import pytest
from test_rl41_w1 import configuration
from test_rl_fast_source import Game, _sender, closed_for, message, train_seed

from spireagent.json_boundary import BoundaryError
from stpd.p1.frame_compiler import FrameCompiler
from stpd.rl.fast_source import FAST_FORMAT
from stpd.rl.segment_learner import SegmentLearner


def fast_learner(tmp_path, monkeypatch, *, fast: Any = True) -> SegmentLearner:
    from stpd.p1.local_inference import AUTH_ENV

    config, io = configuration(tmp_path, monkeypatch)
    monkeypatch.setenv(AUTH_ENV, "ab" * 32)
    config = dataclasses.replace(config, segments=dataclasses.replace(
        config.segments, steps=64, dataset_rows=64, episode_rows=128, capacity_rows=128))
    dataplane = {"lanes": 1, "root": str(tmp_path / "live"), "source_slots": 8,
                 "prepare_slots": 2, "source_bytes": 16 * 1024**2,
                 "preparation_workers": 1, "assembly_workers": 1}
    if fast is not None:
        dataplane["fast_chain"] = fast
    return SegmentLearner(config, tmp_path / "owner", dataclasses.replace(io, dataplane=dataplane))


def game(owner: SegmentLearner, name: str, *, terminal: bool = True) -> list[Any]:
    """One real assembler game bound to this learner's generation and compiler."""
    g = Game(owner.generation, compiler=FrameCompiler(owner.model.vocabulary),
             assembler_id="node-" + name)
    g.open(name)
    for _ in range(64 + 5 if terminal else 65):
        g.step(name)
    if terminal:
        g.step(name, terminal=True)
        g.close(name, "terminal")
    return g.sealed


def admission(owner: SegmentLearner, name: str, index: int) -> dict[str, Any]:
    return owner.control({
        "operation": "fast_instance_admission", "actor": "actor-1", "intent_id": "i-" + name,
        "seed": train_seed(index), "game_instance_id": name, "training_split": "TRAIN",
        "run": owner.config.run, "training_generation": owner.generation})


def eventually(check: Any, seconds: float = 10.0) -> bool:
    """The source prefetch thread holds a short `source-prefetch` retain of its own."""
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def offer(owner: SegmentLearner, segment: Any) -> None:
    assert owner.fast_intake is not None
    owner._fast_apply(owner.fast_intake.offer(message(segment), segment.data))


def queued(owner: SegmentLearner) -> list[str]:
    return [record["segment_id"] for record in owner.queue.pending]


def test_handoff_connection_admits_attested_segments_and_prepares_a_batch(
        tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        sealed = game(owner, "g1")
        assert admission(owner, "g1", 1) == {"admitted": True, "game_instance_id": "g1"}
        ours, theirs = multiprocessing.Pipe()
        process = multiprocessing.Process(target=_sender,
                                          args=(theirs, sealed, closed_for(sealed)))
        process.start()
        theirs.close()  # only the sender holds that end, so its exit is EOF here
        reader = owner.attach_fast_handoff(ours)
        process.join(30)
        assert process.exitcode == 0
        reader.join(30)  # EOF after the sender closes its end
        assert not reader.is_alive() and not owner.input_fenced
        assert queued(owner) == [s.segment_id for s in sealed]
        status = owner.fast_status()["fast_chain"]
        assert status["learner"]["admitted"] == 2
        assert status["learner"]["admitted_rows"] == 70
        assert status["intake"]["open_instances"] == 0
        assert status["fast_source_segments"] == 2
        # The fast record goes through the same binary preparer as the old chain.
        assert owner._prepare()
        assert owner.active is not None
        assert [r["segment_id"] for r in owner.active["segments"]] == [sealed[0].segment_id]
        assert owner.active_batch is not None
        assert len(owner.active_batch.metadata) == 64
        # Forward/loss/backward run on the fast rows; the fixture's behaviour q
        # (0.25/0.75) is far from the fresh policy, so the method's own KL stop fires.
        result = owner._step()
        assert result["rows"] == 16 and result["kl_stopped"] and not result["stepped"]
        assert result["approx_kl_mu_theta"] > 0 and owner.updates == 0
    finally:
        owner.close()


def test_env_crash_close_drops_unused_segments_and_counts_used_rows(tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        sealed = game(owner, "g1")
        admission(owner, "g1", 1)
        for segment in sealed:
            offer(owner, segment)
        assert owner.fast_intake is not None
        owner._fast_apply(owner.fast_intake.closed(closed_for(
            sealed, close_reason="env_crash")))
        assert queued(owner) == []
        counters = owner.fast_status()["fast_chain"]["learner"]
        assert counters["excluded_instances"] == 1
        assert counters["excluded_dropped_rows"] == 70 and counters["excluded_used_rows"] == 0
        assert eventually(lambda: owner.fast_status()["fast_chain"]["fast_source_segments"] == 0)
        # The actor's later evidence report for the same instance is answered, not re-applied.
        reply = owner.control({"operation": "fast_instance_excluded", "actor": "actor-1",
                               "game_instance_id": "g1", "reason": "evidence_failed",
                               "run": owner.config.run, "training_generation": owner.generation})
        assert reply == {"excluded": True, "game_instance_id": "g1"}
        assert owner.fast_status()["fast_chain"]["learner"]["excluded_instances"] == 1
    finally:
        owner.close()


def test_full_queue_drops_the_oldest_unprepared_segment_counted(tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        capacity = owner.settings.capacity_rows  # 1 lane x 64 + 4 x dataset_rows
        assert capacity % 64 == 0
        first = []
        for index in range(1, capacity // 64 + 2):
            name = f"g{index}"
            bootstrap = game(owner, name, terminal=False)[0]
            admission(owner, name, index)
            offer(owner, bootstrap)
            first.append(bootstrap.segment_id)
        assert queued(owner) == first[1:]
        counters = owner.fast_status()["fast_chain"]["learner"]
        assert counters["capacity_dropped"] == 1 and counters["capacity_dropped_rows"] == 64
        assert owner.queue.occupied == capacity
        assert eventually(lambda: owner.fast_status()["fast_chain"]["fast_source_segments"]
                          == capacity // 64)
    finally:
        owner.close()


def test_fast_chain_off_keeps_the_old_provider_and_refuses_fast_controls(tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch, fast=None)
    try:
        assert owner.input_preparer.provider is owner.binary_sources
        assert owner.fast_status() == {}
        with pytest.raises(BoundaryError, match="fast_chain_not_enabled"):
            admission(owner, "g1", 1)
    finally:
        owner.close()
    with pytest.raises(BoundaryError, match="explicit_bool"):
        (tmp_path / "bad").mkdir()
        fast_learner(tmp_path / "bad", monkeypatch, fast="yes")


def test_fast_ref_format_is_routed_to_fast_sources(tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        sealed = game(owner, "g1", terminal=False)
        admission(owner, "g1", 1)
        offer(owner, sealed[0])
        record = owner.queue.pending[0]
        member = owner._member(record)
        assert member.payload.format == FAST_FORMAT
        assert owner._binary_owner(member.payload) is owner.fast_sources
    finally:
        owner.close()
