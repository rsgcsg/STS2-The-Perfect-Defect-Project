"""RL5 V3: the fast data plane in shard processes (`dataplane.shards`)."""
from __future__ import annotations

import dataclasses
import multiprocessing
import os
import signal
import time
from pathlib import Path
from typing import Any

import pytest
import torch
from test_rl41_w1 import assert_tree, configuration
from test_rl_fast_learner import admission, eventually, exclude, learner_mu
from test_rl_fast_source import Game, _sender, closed_for
from test_rl_pipeline_checkpoint import SGD_PROCESS
from test_rl_segment_learner import read_cp

from spireagent.json_boundary import BoundaryError
from stpd.p1.frame_compiler import FrameCompiler
from stpd.rl import dataplane_shards
from stpd.rl.dataplane_shards import (
    adopted_rng_state,
    shard_budget,
    shard_dataplane,
    shard_settings,
)
from stpd.rl.fast_handoff import ShmSegmentSender
from stpd.rl.segment_learner import SegmentLearner
from stpd.rl.segments import SegmentConfig

STEP_FACTS = ("update", "dataset", "minibatch", "epoch", "stepped", "kl_stopped",
              "first_use", "first_use_multi", "td_loss", "policy_loss", "entropy",
              "approx_kl_mu_theta", "terminal_loss", "hp_loss", "gradient_norm")


def parts(tmp_path, monkeypatch, shards: int, *, wall_seconds: float | None = None,
          prestart: bool = False, epochs: int = 1,
          **dataplane_extra: Any) -> tuple[Any, Any]:
    """`prestart` False (not the SGD default): the learner then takes a dataset only
    after the previous one finished, so the order of selections against aux
    charges does not depend on timing and two arms can be compared value by
    value. The two-shard training test runs with the default (True)."""
    from stpd.p1.local_inference import AUTH_ENV

    tmp_path.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(20261113)  # the start package draws from the global torch RNG
    # One fixed generation: both arms' segments, envelopes and requests are then
    # comparable value by value.
    config, io = configuration(tmp_path, monkeypatch, generation="g-shards")
    monkeypatch.setenv(AUTH_ENV, "ab" * 32)
    config = dataclasses.replace(config, segments=dataclasses.replace(
        config.segments, steps=64, dataset_rows=64, minibatches=2, episode_rows=128,
        capacity_rows=1024, epochs=epochs,
        target_kl=0.9))  # the fixture's behaviour q is far from theta: let it step
    if wall_seconds is not None:
        config = dataclasses.replace(config, wall_seconds=wall_seconds)
    dataplane = {"lanes": 1, "root": str(tmp_path / "live"), "source_slots": 16,
                 "prepare_slots": 8, "source_bytes": 64 * 1024**2,
                 "preparation_workers": 1, "assembly_workers": 1, "prepared_batches": 2,
                 "fast_chain": True, **SGD_PROCESS, "sgd_prestart": prestart,
                 **dataplane_extra}
    if shards:
        dataplane["shards"] = shards
    return config, dataclasses.replace(io, dataplane=dataplane)


def learner(tmp_path, monkeypatch, shards: int, *, prestart: bool = False, epochs: int = 1,
            **dataplane_extra: Any) -> SegmentLearner:
    config, io = parts(tmp_path, monkeypatch, shards, prestart=prestart, epochs=epochs,
                       **dataplane_extra)
    return SegmentLearner(config, tmp_path / "owner", io)


def _send_open(connection: Any, segments: list[Any]) -> None:
    """Segments of a game that stays open: no close, then the sender's orderly end."""
    sender = ShmSegmentSender(connection, assembler_id=segments[0].header["assembler_instance"])
    for segment in segments:
        sender.send(segment, Path(__file__))
    sender.finish()


def game(owner: SegmentLearner, index: int, *, terminal: bool = True) -> list[Any]:
    """One game with a fixed instance id (the same bytes in every arm)."""
    name = f"g{index}"
    g = Game(owner.generation, compiler=FrameCompiler(owner.input_preparer.vocabulary),
             assembler_id="node-" + name, handoff=learner_mu(owner),
             instance_id=f"instance-{index}")
    g.open(name)
    for _ in range(64 + 5):
        g.step(name)
    if terminal:
        g.step(name, terminal=True)
        g.close(name, "terminal")
    assert admission(owner, name, index) == {"admitted": True, "game_instance_id": name}
    return g.sealed


def hand_off(owner: SegmentLearner, sealed: list[Any], *, labels: bool = True) -> None:
    """One assembler connection carrying one whole game, read to its orderly end."""
    ours, theirs = multiprocessing.Pipe()
    process = multiprocessing.Process(target=_sender,
                                      args=(theirs, sealed, closed_for(sealed), labels))
    process.start()
    theirs.close()
    reader = owner.attach_fast_handoff(ours)
    process.join(60)
    assert process.exitcode == 0
    reader.join(60)
    assert not reader.is_alive()


def next_dataset(owner: SegmentLearner, seconds: float = 300.0) -> None:
    """Wait for the next prepared dataset as the main loop does (on the learner's
    condition, not a sleep). A learner that stopped taking input fails at once
    with its failure; a timeout reports what the data plane holds."""

    def facts() -> str:
        shards = (owner.fast_status().get("fast_chain", {}) if owner.shards is None
                  else owner.fast_status()["fast_chain"]["shards"]["per_shard"])
        return (f"input_failure={owner.input_failure} fenced={owner.input_fenced} "
                f"stopped={owner.newlease_stopped} queue_rows={owner.queue.rows} "
                f"pending={len(owner._pending_batches())} datasets={owner.datasets} "
                f"sgd_failure={owner.sgd_failure} data_plane={shards}")

    deadline = time.monotonic() + seconds
    while not owner._prepare():
        if owner.input_fenced or owner.newlease_stopped:
            pytest.fail("the learner stopped taking input: " + facts())
        if time.monotonic() > deadline:
            pytest.fail(f"no prepared dataset within {seconds:.0f} s: " + facts())
        with owner.condition:
            owner.condition.wait(0.05)


def train(owner: SegmentLearner, datasets: int) -> tuple[list[Any], list[dict[str, Any]]]:
    """The main loop's own call order until that many more datasets are finished.

    With `sgd_prestart` the learner may take the next dataset while the current
    one still trains and promote it without a new `_prepare`; every dataset is
    recorded when it becomes the current one, however it got there."""
    requests: list[Any] = []
    steps: list[dict[str, Any]] = []
    seen: set[int] = set()
    target = owner.datasets + datasets
    while owner.datasets < target:
        owner._prepare_ahead()
        if owner.active is None:
            next_dataset(owner)
        assert owner.active is not None and owner.active_batch is not None
        request = owner.active_batch.request
        if request.identity.sequence not in seen:
            seen.add(request.identity.sequence)
            # The whole frozen request: members, aux selection, minibatch partition,
            # aux owners, selection RNG state, byte budget and dataset number.
            requests.append((request, owner.active_batch.metadata))
            if owner.shards is not None:
                owner.seen_shards = [*getattr(owner, "seen_shards", []),
                                     (owner.active["shard"], owner.active["shard_sequence"])]
        owner._prepare_ahead()
        result = owner._step()
        steps.append({key: result.get(key) for key in STEP_FACTS})
        if owner.active is None:
            owner._collect_publication(wait=True)
    return requests, steps


def run_arm(tmp_path, monkeypatch, shards: int, *, prestart: bool = False,
            epochs: int = 1) -> dict[str, Any]:
    owner = learner(tmp_path, monkeypatch, shards, prestart=prestart, epochs=epochs)
    try:
        for index in (1, 2, 3):
            hand_off(owner, game(owner, index))
        assert not owner.input_fenced
        requests, steps = train(owner, 3)
        status = owner.fast_status()["fast_chain"]
        owner.checkpoint(final=True)  # a CP is written with shards too
        return {"requests": requests, "steps": steps, "learner": status["learner"],
                "intake": status["intake"], "updates": owner.updates,
                "datasets": owner.datasets, "first_use": owner.first_use,
                "version": owner.version, "fenced": owner.input_fenced,
                "prestarted": owner.timing["sgd_prestarted_batches"]}
    finally:
        owner.close()


def test_one_shard_selects_and_trains_exactly_as_the_in_process_data_plane(
        tmp_path, monkeypatch):
    inline = run_arm(tmp_path / "inline", monkeypatch, 0)
    shard = run_arm(tmp_path / "shard", monkeypatch, 1)
    assert len(inline["requests"]) == 3 and inline["updates"] > 0
    assert any(request.auxiliary for request, _ in inline["requests"])
    assert inline["prestarted"] == 0
    assert shard == inline


def test_with_prestart_one_shard_still_trains_exactly_as_the_in_process_data_plane(
        tmp_path, monkeypatch):
    """`sgd_prestart` (the chain default) on in both arms: a next dataset is taken
    while its predecessor trains and promoted without a new `_prepare`. Four
    epochs (8 steps a dataset) leave the staged answer time to arrive, so the
    prestart fires. All three datasets are selected before the first step
    (two ahead), so when it fires cannot change a selection: the shard path
    trains the same datasets with the same members, aux selections, RNG
    states, losses and counters. The prestart count itself depends on timing."""
    inline = run_arm(tmp_path / "inline", monkeypatch, 0, prestart=True, epochs=4)
    shard = run_arm(tmp_path / "shard", monkeypatch, 1, prestart=True, epochs=4)
    assert inline["datasets"] == 3 and inline["prestarted"] >= 1 and shard["prestarted"] >= 1
    assert [r.identity.sequence for r, _ in inline["requests"]] == [1, 2, 3]
    inline.pop("prestarted"), shard.pop("prestarted")
    assert shard == inline


def test_a_dead_shard_fences_the_learner_and_nothing_is_rerouted(tmp_path, monkeypatch):
    owner = learner(tmp_path, monkeypatch, 2)
    try:
        sealed = game(owner, 1)
        hand_off(owner, sealed)  # first connection: shard 0
        before = owner.fast_status()["fast_chain"]
        assert before["shards"]["count"] == 2
        log = (owner.work / "dataplane-shard-0.log").read_text()
        assert "dataplane shard 0 of 2 started" in log
        assert [s["connections"] for s in before["shards"]["per_shard"]] == [1, 0]
        assert before["learner"]["admitted"] == len(sealed)
        victim, other = owner.shards.shards
        # A connection still open on the shard that will die (token 2 -> shard 0).
        owner.shards.attached = 2
        ours, theirs = multiprocessing.Pipe()
        receiver = owner.attach_fast_handoff(ours)
        os.kill(victim.process.pid, signal.SIGKILL)
        assert eventually(lambda: owner.input_fenced, 30)
        # The thread `attach` returned ends with its shard (no receiver_done comes).
        receiver.join(30)
        assert not receiver.is_alive()
        theirs.close()
        assert owner.newlease_stopped
        assert owner.input_failure["operation"] == "dataplane_shard"
        assert owner.input_failure["shard"] == 0
        assert owner.input_failure["failure"]["operation"] == "dataplane_shard_died"
        # The surviving shard was told: it admits nothing more, and no call
        # re-routes the dead shard's work to it.
        assert eventually(lambda: owner.shards._call(other, "status")["input_fenced"], 30)
        # The status of the failed run can still be read.
        after = owner.fast_status()["fast_chain"]["shards"]
        assert "unavailable" in after["per_shard"][0] and "learner" in after["per_shard"][1]
        assert after["balance"] == {"ok": False, "checked": False,
                                    "problems": ["shard_unavailable"]}
        with pytest.raises(BoundaryError, match="dataplane_shard_died"):
            admission(owner, "g2", 2)
        ours, _theirs = multiprocessing.Pipe()
        owner.shards.attached = 0  # the next attach would go to the dead shard
        with pytest.raises(BoundaryError, match="dataplane_shard_died"):
            owner.attach_fast_handoff(ours)
        assert not owner._prepare()
    finally:
        with pytest.raises(BoundaryError, match="dataplane_shard_died"):
            owner.close()


def test_two_shards_split_connections_and_count_a_broadcast_exclusion_once(
        tmp_path, monkeypatch):
    owner = learner(tmp_path, monkeypatch, 2, prestart=True)  # the chain's default
    try:
        first, second = game(owner, 1), game(owner, 2)
        hand_off(owner, first)   # shard 0
        hand_off(owner, second)  # shard 1
        status = owner.fast_status()["fast_chain"]
        per_shard = status["shards"]["per_shard"]
        assert [s["connections"] for s in per_shard] == [1, 1]
        assert [s["learner"]["admitted"] for s in per_shard] == [len(first), len(second)]
        assert status["learner"]["admitted"] == len(first) + len(second)
        assert status["intake"]["attested"] == 2  # broadcast: counted once, not per shard
        # Both closed on their own shard; the other shard's inert record is gone.
        assert eventually(lambda: owner.fast_status()["fast_chain"]["intake"][
            "open_instances"] == 0)
        # An instance no shard has seen yet: excluded everywhere, counted once.
        reply = owner.control({
            "operation": "fast_instance_excluded", "actor": "actor-1",
            "game_instance_id": "g9", "reason": "env_crash", "run": owner.config.run,
            "training_generation": owner.generation})
        assert reply == {"excluded": True, "game_instance_id": "g9"}
        status = owner.fast_status()["fast_chain"]
        assert status["learner"]["excluded_instances"] == 1
        assert status["intake"]["excluded_by_reason"] == {"actor:env_crash": 1}
        assert not owner.input_fenced
        requests, steps = train(owner, 2)
        assert len(requests) == 2 and owner.updates > 0 and steps
        assert [r.identity.sequence for r, _ in requests] == [1, 2]
        # One dataset from each shard: the second is its shard's first, so its
        # file carries number 1 while the learner (and the SGD plan) call it 2.
        assert sorted(owner.seen_shards) == [(0, 1), (1, 1)]
        # A CP records the shard count and every shard's own selection RNG.
        recorded = owner._learning_state().input_pipeline["dataplane_shards"]
        assert recorded["count"] == 2 and [s["index"] for s in recorded["shards"]] == [0, 1]
        assert all(len(s["committed_selection_rng"]) == 3 for s in recorded["shards"])
        assert (recorded["shards"][0]["committed_selection_rng"]
                != recorded["shards"][1]["committed_selection_rng"])
    finally:
        owner.close()


def test_run_to_a_normal_stop_gives_the_same_CP_and_discard_with_one_shard(
        tmp_path, monkeypatch):
    """Through `run()`: training, the window end, the stop discard and the final CP."""
    results = {}
    for shards in (0, 1):
        config, io = parts(tmp_path / str(shards), monkeypatch, shards, wall_seconds=45)
        owner = SegmentLearner(config, tmp_path / str(shards) / "owner", io)
        for index in (1, 2, 3):
            hand_off(owner, game(owner, index))
        summary = owner.run()  # closes the learner
        _, cp = read_cp(owner)
        status = owner.fast_status().get("fast_chain", {})
        discarded = (owner.training_discarded_rows if not shards
                     else sum(s["training_discarded_rows"] for s in status["shards"]["per_shard"]))
        queued = (owner.queue.rows if not shards
                  else sum(s["queue_rows"] for s in status["shards"]["per_shard"]))
        committed = (cp.rng.learner_random if not shards else tuple(
            cp.input_pipeline["dataplane_shards"]["shards"][0]["committed_selection_rng"]))
        results[shards] = cp, {
            "updates": summary["updates"], "datasets": summary["datasets"],
            "first_use": summary["first_use"], "versions": summary["versions"],
            "window_end": summary["window_end"], "fenced": summary["input_fenced"],
            "counters": cp.counters, "discarded": discarded, "queued": queued,
            "committed_rng": (committed[0], list(committed[1]), committed[2])}
    (base, base_facts), (shard, shard_facts) = results[0], results[1]
    assert base_facts["updates"] > 0 and base_facts["window_end"] == "deadline"
    assert base_facts["discarded"] > 0 and base_facts["queued"] == 0
    assert shard_facts == base_facts
    for name in ("weights", "optimizer", "ema"):
        assert_tree(getattr(base, name), getattr(shard, name))


def test_an_instance_is_one_shards_for_life_as_on_one_ledger(tmp_path, monkeypatch):
    owner = learner(tmp_path, monkeypatch, 2)
    try:
        sealed = game(owner, 1)
        hand_off(owner, sealed)  # shard 0 receives the game and its close
        other = owner.shards.shards[1]
        assert eventually(lambda: owner.shards._call(other, "status")["intake"].get(
            "open_instances") == 0)
        # The same instance again, on a connection of the other shard: the same
        # fence as on one ledger, raised there and passed on to every shard.
        ours, theirs = multiprocessing.Pipe()
        process = multiprocessing.Process(target=_send_open, args=(theirs, sealed[:1]))
        process.start()
        theirs.close()
        owner.attach_fast_handoff(ours)
        assert eventually(lambda: owner.input_fenced, 30)
        assert owner.input_failure["shard"] == 1
        assert "fast_segment_after_instance_closed" in owner.input_failure["failure"]["error"]
        first = owner.shards.shards[0]
        assert eventually(lambda: owner.shards._call(first, "status")["input_fenced"], 30)
        status = owner.fast_status()["fast_chain"]
        assert status["learner"]["admitted"] == len(sealed)  # nothing admitted twice
        assert "input fenced" in (owner.work / "dataplane-shard-1.log").read_text()
        process.kill()
        process.join(30)
    finally:
        owner.close()


def test_an_open_instance_on_a_second_shard_fences(tmp_path, monkeypatch):
    owner = learner(tmp_path, monkeypatch, 2)
    try:
        sealed = game(owner, 1, terminal=False)  # stays open on shard 0
        senders = []
        for _ in range(2):  # the same segment on a connection of each shard
            ours, theirs = multiprocessing.Pipe()
            process = multiprocessing.Process(target=_send_open, args=(theirs, sealed))
            process.start()
            theirs.close()
            owner.attach_fast_handoff(ours)
            senders.append(process)
        assert eventually(lambda: owner.input_fenced, 30)
        assert "fast_instance_on_two_shards" in owner.input_failure["failure"]["error"]
        for process in senders:
            process.kill()
            process.join(30)
    finally:
        owner.close()


def test_a_shard_that_does_not_answer_fences_and_is_not_waited_for_at_close(
        tmp_path, monkeypatch):
    monkeypatch.setattr(dataplane_shards, "FAILED_CLOSE_SECONDS", 2.0)
    owner = learner(tmp_path, monkeypatch, 2)
    hung = owner.shards.shards[1]
    try:
        os.kill(hung.process.pid, signal.SIGSTOP)
        with pytest.raises(BoundaryError, match="dataplane_shard_reply_timeout"):
            owner.shards._call(hung, "status", seconds=1.0)
        assert eventually(lambda: owner.input_fenced, 30)
        assert owner.input_failure["failure"]["operation"] == "dataplane_shard_reply_timeout"
    finally:
        began = time.monotonic()
        with pytest.raises(BoundaryError, match="dataplane_shard"):
            owner.close()
        assert time.monotonic() - began < 60
        assert not hung.process.is_alive()


def test_a_message_without_a_known_kind_fences_at_once(tmp_path, monkeypatch):
    owner = learner(tmp_path, monkeypatch, 1)
    try:
        monkeypatch.setattr(dataplane_shards.ShardSet, "_decode",
                            staticmethod(lambda data: {"kind": "surprise"}))
        # The next message of the shard (its status reply) is not understood.
        status = owner.fast_status()["fast_chain"]["shards"]
        assert "unavailable" in status["per_shard"][0]
        assert eventually(lambda: owner.input_fenced, 30)
        failure = owner.input_failure["failure"]
        assert failure["operation"] == "dataplane_shard_message_unreadable"
        assert "unknown_shard_message_kind" in failure["error"]
    finally:
        with pytest.raises(BoundaryError, match="dataplane_shard"):
            owner.close()


def _send_then_vanish(connection: Any, segments: list[Any], go: Any) -> None:
    """Hand a still-open game off, then (on `go`) end without the orderly end:
    the service's EOF, as when it stops right after the learner's stop reply."""
    sender = ShmSegmentSender(connection, assembler_id=segments[0].header["assembler_instance"])
    for segment in segments:
        sender.send(segment, Path(__file__))
    sender._drain(block=True)
    go.wait(120)
    sender.close()  # release only: never the orderly end
    connection.close()


def test_a_normal_stop_is_not_a_fence_when_services_end_before_the_shards_hear_of_it(
        tmp_path, monkeypatch):
    """S2: the stop flag reaches a shard by message. A service that ends right
    after the stop reply may reach its shard first; the shard reports the lost
    hand-off, the stopping learner records it, and nothing admitted is lost."""
    owner = learner(tmp_path, monkeypatch, 2)
    go = multiprocessing.Event()
    senders: list[Any] = []
    events: list[tuple[str, dict[str, Any]]] = []
    try:
        admitted = 0
        for index in (1, 2):  # one open game on a connection of each shard
            sealed = game(owner, index, terminal=False)
            admitted += sum(len(segment.header["rows"]) for segment in sealed)
            ours, theirs = multiprocessing.Pipe()
            process = multiprocessing.Process(target=_send_then_vanish,
                                              args=(theirs, sealed, go))
            process.start()
            theirs.close()
            owner.attach_fast_handoff(ours)
            senders.append(process)
        assert eventually(lambda: owner.fast_status()["fast_chain"]["learner"][
            "admitted_rows"] == admitted, 60)
        # The stop flags are held back: the shards hear of the stop only after
        # the services ended (the widest form of the race).
        held = owner.shards.flags
        owner.shards.flags = lambda **_: None
        record = owner._event
        owner._event = lambda kind, **fields: (events.append((kind, fields)),
                                               record(kind, **fields))[1]
        reply = owner.control({"operation": "stop", "run": owner.config.run,
                               "training_generation": owner.generation, "epoch": 1,
                               "reason": "window_end"})
        assert reply["stopped"] is True
        go.set()
        for process in senders:
            process.join(60)
            assert process.exitcode == 0
        # Each shard saw an EOF before the stop and fenced itself ...
        for shard in owner.shards.shards:
            assert eventually(lambda shard=shard: owner.shards._call(
                shard, "status")["input_fenced"], 30)
        assert eventually(lambda: sorted(fields["shard"] for kind, fields in events
                                         if kind == "fast_handoff_lost"
                                         and fields.get("learner_stopping")) == [0, 1], 30)
        # ... and the learner, already stopping, only recorded it.
        assert not owner.input_fenced and owner.input_failure is None
        assert not any(shard.failed for shard in owner.shards.shards)
        owner.shards.flags = held
        with owner.input_gate, owner.condition:
            owner._discard_stopped_shards()  # as `_run` does at the stop
        assert not owner.input_fenced
        # Everything admitted is accounted: discarded at the stop, nothing dropped.
        status = owner.fast_status()["fast_chain"]["shards"]["per_shard"]
        assert sum(s["training_discarded_rows"] for s in status) == admitted
        assert sum(s["queue_rows"] for s in status) == 0
    finally:
        go.set()
        for process in senders:
            if process.is_alive():
                process.kill()
        owner.close()  # both shards end cleanly: no fence was raised
    assert [shard.exit["exitcode"] for shard in owner.shards.shards] == [0, 0]


@pytest.mark.parametrize("failure", ["dead", "hung"])
def test_a_shard_lost_at_the_stop_fences_and_everything_is_still_closed(
        tmp_path, monkeypatch, failure):
    """S1: the stop discard asks every shard. A dead or a hung one fences the
    learner (no disposition for what it held, no final CP: restore the last
    one), and the close still ends the shards, the pool and the SGD process."""
    monkeypatch.setattr(dataplane_shards, "REPLY_SECONDS", 10.0)
    monkeypatch.setattr(dataplane_shards, "FAILED_CLOSE_SECONDS", 2.0)
    owner = learner(tmp_path, monkeypatch, 2)
    shards = owner.shards
    victim = shards.shards[0 if failure == "dead" else 1]
    try:
        hand_off(owner, game(owner, 1))  # queued on shard 0
        hand_off(owner, game(owner, 2))  # queued on shard 1
        discard = shards.discard_stopped

        def at_the_stop() -> list[dict[str, Any]]:
            if failure == "dead":
                os.kill(victim.process.pid, signal.SIGKILL)
                assert eventually(lambda: victim.dead is not None, 60)
            else:
                os.kill(victim.process.pid, signal.SIGSTOP)
            return discard()

        shards.discard_stopped = at_the_stop
        sequence = owner.checkpoint_sequence
        (owner.work / "STOP").touch()  # stop at once: the loop never starts
        began = time.monotonic()
        with pytest.raises(BoundaryError, match="dataplane_shard"):
            owner.run()
        assert time.monotonic() - began < 120
    finally:
        if victim.process.is_alive():  # only if the close did not run
            victim.process.kill()
    assert owner.input_fenced
    assert owner.input_failure["shard"] == victim.index
    assert owner.input_failure["failure"]["operation"] == "dataplane_shard_discard_at_stop"
    assert owner.checkpoint_sequence == sequence  # no final CP from a fenced stop
    # The close ran to its end: every shard reaped, the SGD process shut down,
    # no child process of this learner left.
    assert all(shard.exit is not None and not shard.process.is_alive()
               for shard in shards.shards)
    assert owner.sgd.exit is not None and not owner.sgd.process.is_alive()
    assert eventually(lambda: not multiprocessing.active_children(), 30), (
        multiprocessing.active_children())


def test_a_prepared_dataset_keeps_an_instance_excluded_after_it_with_two_shards(
        tmp_path, monkeypatch):
    """D1 = A per shard: segments a read-ahead dataset already holds count as
    used and train; the owning shard counts the exclusion, the other only marks
    the instance foreign, and the learner reports it once."""
    owner = learner(tmp_path, monkeypatch, 2)
    senders: list[Any] = []
    try:
        sealed = {}
        for index in (1, 2):  # open games, so an exclusion is still allowed
            sealed[index] = game(owner, index, terminal=False)
            ours, theirs = multiprocessing.Pipe()
            process = multiprocessing.Process(target=_send_open, args=(theirs, sealed[index]))
            process.start()
            theirs.close()
            owner.attach_fast_handoff(ours).join(60)
            senders.append(process)
        rows = sum(len(segment.header["rows"]) for segment in sealed[1])
        # Both shards prepare one dataset ahead (not taken: still queued there).
        assert eventually(lambda: (owner._prepare_ahead(),
                                   len(owner._pending_batches()))[1] == 2, 120)
        assert sorted(active["shard"] for active, _ in owner._pending_batches()) == [0, 1]
        assert exclude(owner, "g1") == {"excluded": True, "game_instance_id": "g1"}
        status = owner.fast_status()["fast_chain"]
        assert status["learner"]["excluded_instances"] == 1
        assert status["learner"]["excluded_prefetched_rows"] == rows
        assert status["learner"]["excluded_dropped_rows"] == 0
        assert status["intake"]["excluded_by_reason"] == {"actor:evidence_failed": 1}
        per_shard = status["shards"]["per_shard"]
        assert [s["learner"]["excluded_prefetched_rows"] for s in per_shard] == [rows, 0]
        assert [s["foreign_exclusions"] for s in per_shard] == [0, 1]
        # The prepared dataset is trained as it was frozen, the excluded game in it.
        requests, _ = train(owner, 2)
        members = {member.segment_id for member in requests[0][0].members}
        assert {segment.segment_id for segment in sealed[1]} <= members
        assert sorted(owner.seen_shards) == [(0, 1), (1, 1)]
        assert not owner.input_fenced
    finally:
        for process in senders:
            process.join(30)
        owner.close()


def test_every_shard_records_a_new_mu_before_any_service_can_hold_it(tmp_path, monkeypatch):
    owner = learner(tmp_path, monkeypatch, 2)
    order: list[str] = []
    try:
        hand_off(owner, game(owner, 1))
        hand_off(owner, game(owner, 2))
        before = owner.pool.latest
        record_shards, record_pool = owner.shards.publish_mu, owner.pool.publish
        owner.shards.publish_mu = lambda identity: (record_shards(identity),
                                                    order.append("shards"))[0]
        owner.pool.publish = lambda *args: (order.append("pool"), record_pool(*args))[1]
        train(owner, 2)
        owner._collect_publication(wait=True)
        assert owner.pool.latest != before and order and order[0] == "shards"
        assert order == ["shards", "pool"] * (len(order) // 2)
        # A segment under the new mu is admitted on either shard (each recorded it).
        admitted = owner.fast_status()["fast_chain"]["learner"]["admitted"]
        third, fourth = game(owner, 3), game(owner, 4)
        hand_off(owner, third)   # shard 0
        hand_off(owner, fourth)  # shard 1
        status = owner.fast_status()["fast_chain"]
        assert status["learner"]["admitted"] == admitted + len(third) + len(fourth)
        assert not owner.input_fenced
    finally:
        owner.close()


def test_a_restore_keeps_every_shards_selection_rng_and_refuses_another_count(
        tmp_path, monkeypatch):
    config, io = parts(tmp_path / "a", monkeypatch, 2)
    owner = SegmentLearner(config, tmp_path / "a" / "owner", io)
    try:
        hand_off(owner, game(owner, 1))
        hand_off(owner, game(owner, 2))
        train(owner, 2)
        owner.checkpoint(final=True)
        record, cp = read_cp(owner)
    finally:
        owner.close()
    recorded = cp.input_pipeline["dataplane_shards"]
    assert recorded["count"] == 2

    def restore(name: str, shards: int) -> SegmentLearner:
        request = dataclasses.replace(
            config, start_package=None, resume_checkpoint=record["checkpoint_artifact_id"],
            segments=dataclasses.replace(config.segments, training_generation="g-" + name))
        dataplane = {**io.dataplane, "root": str(tmp_path / ("live-" + name))}
        dataplane.pop("shards")
        if shards:
            dataplane["shards"] = shards
        return SegmentLearner(request, tmp_path / name, dataclasses.replace(
            io, dataplane=dataplane))

    for other in (0, 1, 3):
        with pytest.raises(BoundaryError, match="checkpoint_dataplane_shard_count_changed"):
            restore(f"other-{other}", other)
    restored = restore("same", 2)
    try:
        assert restored.updates == cp.counters.updates
        now = restored.shards.snapshot()
        for before, after in zip(recorded["shards"], now["shards"], strict=True):
            for name in ("committed_selection_rng", "speculative_selection_rng"):
                assert after[name][0] == before["committed_selection_rng"][0]
                assert list(after[name][1]) == list(before["committed_selection_rng"][1])
        assert not restored.input_fenced
    finally:
        restored.close()


def test_an_unsharded_CP_is_continued_with_shards_only_by_the_explicit_switch(
        tmp_path, monkeypatch):
    config, io = parts(tmp_path / "a", monkeypatch, 0)
    owner = SegmentLearner(config, tmp_path / "a" / "owner", io)
    try:
        hand_off(owner, game(owner, 1))
        train(owner, 1)
        owner.checkpoint(final=True)
        record, cp = read_cp(owner)
    finally:
        owner.close()
    assert "dataplane_shards" not in cp.input_pipeline

    def restore(name: str, **extra: Any) -> SegmentLearner:
        request = dataclasses.replace(
            config, start_package=None, resume_checkpoint=record["checkpoint_artifact_id"],
            segments=dataclasses.replace(config.segments, training_generation="g-" + name))
        dataplane = {**io.dataplane, "root": str(tmp_path / ("live-" + name)), "shards": 2,
                     **extra}
        return SegmentLearner(request, tmp_path / name, dataclasses.replace(
            io, dataplane=dataplane))

    with pytest.raises(BoundaryError, match="checkpoint_dataplane_shard_count_changed"):
        restore("refused")
    with pytest.raises(BoundaryError, match="dataplane_shards_adopt_switch"):
        restore("typed", shards_adopt_unsharded_checkpoint="yes")
    adopted = restore("adopted", shards_adopt_unsharded_checkpoint=True)
    try:
        committed = cp.rng.learner_random
        now = adopted.shards.snapshot()
        assert now["count"] == 2
        for index, shard in enumerate(now["shards"]):
            expected = adopted_rng_state(committed, index)
            assert shard["committed_selection_rng"][0] == expected[0]
            assert list(shard["committed_selection_rng"][1]) == list(expected[1])
        assert list(now["shards"][0]["committed_selection_rng"][1]) == list(committed[1])
        assert (now["shards"][1]["committed_selection_rng"]
                != now["shards"][0]["committed_selection_rng"])
        # Its next CP names the shards, so a later restore needs no switch.
        assert adopted._learning_state().input_pipeline["dataplane_shards"]["count"] == 2
    finally:
        adopted.close()


def test_the_memory_budget_counts_only_processes_that_will_exist():
    settings = SegmentConfig(preparation_workers=12)
    local = {"assembly_workers": 8, "label_workers": 1, "source_bytes": 3 * 1024**3}
    _, _, processes = shard_budget(local, settings, 6, 3)
    assert processes == 3 * (1 + 4 + 3 + 1)
    # Remote prepare never starts a local pool worker: the shard and its clients.
    remote = {**local, "remote_prepare": {"endpoints": ["h:1"], "connections": 36,
                                          "client_processes": 2}}
    _, _, processes = shard_budget(remote, settings, 6, 3)
    assert processes == 3 * (1 + 1)
    remote["remote_prepare"]["client_processes"] = 0
    assert shard_budget(remote, settings, 6, 3)[2] == 3


def test_switch_is_explicit_and_shares_are_even():
    settings = SegmentConfig(capacity_rows=6144, terminal_games=256, hp_battles=128,
                             prepared_bytes=7 * 1024)
    same, ahead = shard_settings(settings, 6, 1)
    assert (same, ahead) == (settings, 6)
    third, ahead = shard_settings(settings, 6, 3)
    assert (third.capacity_rows, third.terminal_games, third.hp_battles, ahead) == (
        2048, 85, 42, 2)
    # The per-dataset byte budget is unchanged: 7 KiB // (6 + 1) per dataset.
    assert third.prepared_bytes // (ahead + 1) == settings.prepared_bytes // 7
    remote = {"endpoints": ["h:1"], "connections": 36, "client_processes": 2}
    assert shard_dataplane({"source_bytes": 30, "prepare_slots": 9, "lanes": 4,
                            "remote_prepare": remote}, 3) == {
        "source_bytes": 10, "prepare_slots": 4, "lanes": 4, "assembly_workers": 1,
        "label_workers": 1,
        "remote_prepare": {"endpoints": ["h:1"], "connections": 12, "client_processes": 1}}
    # Local preparation workers are split too: N shards, the configured total.
    split = shard_dataplane({"preparation_workers": 12, "assembly_workers": 8}, 3, settings)
    assert (split["preparation_workers"], split["assembly_workers"]) == (4, 3)
    assert remote["connections"] == 36  # the learner's own configuration is not changed


@pytest.mark.parametrize("extra", [{"shards": True}, {"shards": 9}, {"shards": -1},
                                   {"shards": 1, "fast_verify_workers": 1},
                                   {"shards": 1, "fast_chain": False}])
def test_invalid_shard_configuration_is_refused_before_anything_starts(
        tmp_path, monkeypatch, extra):
    with pytest.raises(BoundaryError, match="dataplane_shards"):
        learner(tmp_path, monkeypatch, extra["shards"],
                **{key: value for key, value in extra.items() if key != "shards"})


class _NoScan(dict):
    """An instance ledger that fails any walk over it (lookups stay allowed)."""

    def _walk(self, *args: Any) -> Any:
        raise AssertionError("the open instances were walked during an offer")

    __iter__ = items = keys = values = _walk  # type: ignore[assignment]


def test_a_shard_offer_claims_only_its_own_instance_without_walking_the_open_ones():
    """With several shards every shard holds every attested open game (control is
    broadcast). An offer reports only the instance it bound: O(1), not O(open)."""
    from test_rl_fast_source import attest, bootstrap_and_terminal, message, mu, published

    from stpd.rl.dataplane_shards import _ShardIntake
    from stpd.rl.fast_source import _Instance

    intake = _ShardIntake(run="run-1", generation="gen-1")
    intake.publish_mu(published(mu(1)))
    sent: list[dict[str, Any]] = []
    intake.notify = sent.append
    for index in range(3000):  # open games attested here, bound on other shards
        intake.attest(attest(f"other-{index}"))
    first, second = bootstrap_and_terminal("g1")
    intake.instances = _NoScan(intake.instances)
    assert not intake.offer(message(first), first.data).admit  # buffered, bound, claimed
    intake.attest(attest("g1"))
    intake.offer(message(second, 2), second.data)
    assert sent == [{"kind": "claims", "instances": ["g1"]}]  # once, only its own
    assert isinstance(intake.instances.get("other-1"), _Instance)
