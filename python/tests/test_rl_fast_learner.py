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
from stpd.rl.fast_assembly import MuHandoff
from stpd.rl.fast_source import FAST_FORMAT
from stpd.rl.segment_learner import SegmentLearner


def fast_learner(tmp_path, monkeypatch, *, fast: Any = True,
                 aux_labels: list[str] | None = None, **loss: float) -> SegmentLearner:
    from stpd.p1.local_inference import AUTH_ENV

    tmp_path.mkdir(parents=True, exist_ok=True)
    config, io = configuration(tmp_path, monkeypatch)
    if loss:
        config = dataclasses.replace(config, loss=dataclasses.replace(config.loss, **loss))
    monkeypatch.setenv(AUTH_ENV, "ab" * 32)
    config = dataclasses.replace(config, segments=dataclasses.replace(
        config.segments, steps=64, dataset_rows=64, episode_rows=128, capacity_rows=128))
    dataplane = {"lanes": 1, "root": str(tmp_path / "live"), "source_slots": 8,
                 "prepare_slots": 2, "source_bytes": 16 * 1024**2,
                 "preparation_workers": 1, "assembly_workers": 1}
    if fast is not None:
        dataplane["fast_chain"] = fast
    if aux_labels is not None:
        dataplane["fast_aux_labels"] = aux_labels
    return SegmentLearner(config, tmp_path / "owner", dataclasses.replace(io, dataplane=dataplane))


def game(owner: SegmentLearner, name: str, *, terminal: bool = True) -> list[Any]:
    """One real assembler game bound to this learner's generation and compiler."""
    g = Game(owner.generation, compiler=FrameCompiler(owner.model.vocabulary),
             assembler_id="node-" + name, handoff=learner_mu(owner))
    g.open(name)
    for _ in range(64 + 5 if terminal else 65):
        g.step(name)
    if terminal:
        g.step(name, terminal=True)
        g.close(name, "terminal")
    return g.sealed


def learner_mu(owner: SegmentLearner) -> MuHandoff:
    """The mu this learner itself published (its start snapshot)."""
    identity = owner.pool.snapshots[owner.pool.latest]
    return MuHandoff(identity["snapshot_sha256"], identity["training_generation"],
                     identity["version"], identity["updates"])


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
        # Forward/loss/backward run on the fast rows. The fixture's behaviour q
        # (0.25/0.75) is far from the fresh policy, so the method's own KL stop
        # usually fires; a 16-row random minibatch can fall under it (then it steps).
        result = owner._step()
        assert result["rows"] == 16 and result["approx_kl_mu_theta"] > 0
        assert result["kl_stopped"] is not result["stepped"]
        assert owner.updates == (1 if result["stepped"] else 0)
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


def test_learner_trains_only_segments_under_its_own_published_mu(tmp_path, monkeypatch):
    from test_rl_fast_source import mu

    from stpd.rl.fast_source import FastIntakeError

    owner = fast_learner(tmp_path, monkeypatch)
    try:
        assert owner.fast_intake is not None
        start = learner_mu(owner)
        assert owner.fast_intake.published[start.snapshot_sha256]["version"] == start.version
        g = Game(owner.generation, compiler=FrameCompiler(owner.model.vocabulary),
                 assembler_id="node-x", handoff=mu(5, owner.generation))
        g.open("gx")
        for _ in range(65):
            g.step("gx")
        admission(owner, "gx", 1)
        with pytest.raises(FastIntakeError, match="fast_segment_mu_not_published"):
            offer(owner, g.sealed[0])
        assert queued(owner) == []
    finally:
        owner.close()


def test_labels_over_the_handoff_fill_the_aux_pools_and_reach_a_batch(tmp_path, monkeypatch):
    from test_rl_fast_source import combat_game

    owner = fast_learner(tmp_path, monkeypatch, aux_labels=["terminal", "hp"])
    try:
        sealed = combat_game(handoff=learner_mu(owner),
                             compiler=FrameCompiler(owner.model.vocabulary),
                             assembler_id="node-g1")
        admission(owner, "g1", 1)
        ours, theirs = multiprocessing.Pipe()
        process = multiprocessing.Process(target=_sender,
                                          args=(theirs, sealed, closed_for(sealed), True))
        process.start()
        theirs.close()
        reader = owner.attach_fast_handoff(ours)
        process.join(30)
        assert process.exitcode == 0
        reader.join(30)
        assert not reader.is_alive() and not owner.input_fenced
        assert list(owner.terminal.groups) == ["g1"] and len(owner.terminal.groups["g1"]) == 64
        assert list(owner.hp.groups) == ["g1:0", "g1:20"]
        assert {row["hp"] for row in owner.hp.groups["g1:0"]} == {0.4}
        intake = owner.fast_status()["fast_chain"]["intake"]
        assert intake["labels_terminal_rows"] == 64 and intake["labels_hp_rows"] == 20
        # The aux rows go through the same binary preparer as the old chain's.
        assert owner._prepare()
        assert owner.active is not None and owner.active["terminal"] and owner.active["hp"]
        assert owner.active_batch is not None
        batch = owner.active_batch
        assert batch.kinds.count("terminal") == len(owner.active["terminal"]) > 0
        assert batch.kinds.count("hp") == len(owner.active["hp"]) > 0
        result = owner._step()
        assert result["rows"] > 0
        # A later exclusion of the instance also removes its label rows.
        owner.control({"operation": "fast_instance_excluded", "actor": "actor-1",
                       "game_instance_id": "g1", "reason": "evidence_failed",
                       "run": owner.config.run, "training_generation": owner.generation})
        assert not owner.terminal.groups and not owner.hp.groups
        assert owner.fast_status()["fast_chain"]["learner"]["excluded_aux_rows"] > 0
    finally:
        owner.close()


def test_fast_sources_cross_ipc_as_offsets_into_a_derived_file(tmp_path, monkeypatch):
    """Decoded fast rows live in the source's derived file, removed with the slot."""
    owner = fast_learner(tmp_path, monkeypatch)
    preparer: Any = owner.input_preparer
    scratch = preparer.scratch
    closed = False
    try:
        sealed = game(owner, "g1")
        admission(owner, "g1", 1)
        for segment in sealed:
            offer(owner, segment)
        assert owner._prepare() and owner.active_batch is not None
        ready = [job.result().value for job in preparer.source_jobs.values() if job.done()]
        assert ready and all(type(a).__name__ == "_SharedArray" for r in ready for a in r.arrays)
        assert {a.name for r in ready for a in r.arrays} <= {
            str(p) for p in scratch.glob("rl-source-*.d*")}
        owner.close()
        closed = True
        assert not list(scratch.glob("rl-source-*"))
    finally:
        if not closed:
            owner.close()


def exclude(owner: SegmentLearner, name: str) -> dict[str, Any]:
    return owner.control({"operation": "fast_instance_excluded", "actor": "actor-1",
                          "game_instance_id": name, "reason": "evidence_failed",
                          "run": owner.config.run, "training_generation": owner.generation})


def test_exclusion_counts_trained_and_prefetched_rows_apart(tmp_path, monkeypatch):
    """Lead ruling 11-10: used = selected into a prepared batch, never voided or
    rebuilt; rows handed to SGD and rows only read ahead are counted apart."""
    from concurrent.futures import Future

    owner = fast_learner(tmp_path, monkeypatch)
    try:
        for index, name in enumerate(("g1", "g2"), start=1):
            sealed = game(owner, name)
            admission(owner, name, index)
            for segment in sealed:
                offer(owner, segment)
            if name == "g1":
                # The next dataset is taken for SGD: it leaves the queue.
                active = owner._select()
            else:
                # A read-ahead batch (take=False): still queued, held by the batch.
                active = owner._select(take=False)
                owner.ahead_batches.append((active, Future()))
            assert active is not None
            assert [r["segment_id"] for r in active["segments"]] == [sealed[0].segment_id]
            assert exclude(owner, name) == {"excluded": True, "game_instance_id": name}
            assert queued(owner) == ([] if name == "g1" else [sealed[0].segment_id])
        counters = owner.fast_status()["fast_chain"]["learner"]
        assert counters["excluded_instances"] == 2
        assert counters["excluded_dropped_rows"] == 12  # each game's 6-row terminal tail
        assert counters["excluded_trained_rows"] == 64  # g1's dataset, handed to SGD
        assert counters["excluded_prefetched_rows"] == 64  # g2's, read ahead, kept
        assert counters["excluded_used_rows"] == 128
    finally:
        owner.ahead_batches.clear()
        owner.close()


def test_an_exclusion_between_the_admit_decision_and_its_use_drops_the_segment(
        tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        sealed = game(owner, "g1")
        admission(owner, "g1", 1)
        assert owner.fast_intake is not None
        # The receive thread has the intake's decision (admit) ...
        decision = owner.fast_intake.offer(message(sealed[0]), sealed[0].data)
        assert decision.admit
        # ... the control thread applies the actor's exclusion first ...
        assert exclude(owner, "g1") == {"excluded": True, "game_instance_id": "g1"}
        # ... then the receive thread applies its decision: dropped, counted.
        owner._fast_apply(decision)
        assert queued(owner) == []
        counters = owner.fast_status()["fast_chain"]["learner"]
        assert counters["excluded_admit_raced"] == 1
        assert counters["excluded_admit_raced_rows"] == 64
        assert counters["excluded_dropped_rows"] == 64 and counters["admitted"] == 0
        assert eventually(lambda: owner.fast_status()["fast_chain"]["fast_source_segments"] == 0)
    finally:
        owner.close()


def test_fast_aux_pools_follow_the_loss_coefficients(tmp_path, monkeypatch):
    """Lead ruling 11-10: every pool with coef > 0 takes fast labels by default;
    an explicit list leaving one out is refused at start, never a silent 0."""
    owner = fast_learner(tmp_path / "a", monkeypatch)
    try:
        assert owner.config.loss.terminal_coef > 0 and owner.config.loss.hp_coef > 0
        assert owner.fast_intake is not None
        assert owner.fast_intake.aux_labels == {"terminal", "hp"}
    finally:
        owner.close()
    owner = fast_learner(tmp_path / "b", monkeypatch, hp_coef=0.0)
    try:
        assert owner.fast_intake is not None and owner.fast_intake.aux_labels == {"terminal"}
    finally:
        owner.close()
    with pytest.raises(BoundaryError, match="omit_a_pool_with_positive_coef"):
        fast_learner(tmp_path / "c", monkeypatch, aux_labels=["terminal"])
    with pytest.raises(BoundaryError, match="omit_a_pool_with_positive_coef"):
        fast_learner(tmp_path / "d", monkeypatch, aux_labels=[], terminal_coef=0.1)
    # A pool the loss does not weight may still be listed (harmless).
    owner = fast_learner(tmp_path / "e", monkeypatch, aux_labels=["terminal", "hp"],
                         hp_coef=0.0)
    owner.close()
