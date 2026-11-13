"""RL5 V3: the fast data plane in shard processes (`dataplane.shards`)."""
from __future__ import annotations

import dataclasses
import multiprocessing
import os
import signal
from typing import Any

import pytest
import torch
from test_rl41_w1 import configuration
from test_rl_fast_learner import admission, eventually, learner_mu
from test_rl_fast_source import Game, _sender, closed_for
from test_rl_pipeline_checkpoint import SGD_PROCESS
from test_rl_segment_learner import read_cp

from spireagent.json_boundary import BoundaryError
from stpd.p1.frame_compiler import FrameCompiler
from stpd.rl.dataplane_shards import shard_dataplane, shard_settings
from stpd.rl.segment_learner import SegmentLearner
from stpd.rl.segments import SegmentConfig

STEP_FACTS = ("update", "dataset", "minibatch", "epoch", "stepped", "kl_stopped",
              "first_use", "first_use_multi", "td_loss", "policy_loss", "entropy",
              "approx_kl_mu_theta", "terminal_loss", "hp_loss", "gradient_norm")


def parts(tmp_path, monkeypatch, shards: int, **dataplane_extra: Any) -> tuple[Any, Any]:
    from stpd.p1.local_inference import AUTH_ENV

    tmp_path.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(20261113)  # the start package draws from the global torch RNG
    # One fixed generation: both arms' segments, envelopes and requests are then
    # comparable value by value.
    config, io = configuration(tmp_path, monkeypatch, generation="g-shards")
    monkeypatch.setenv(AUTH_ENV, "ab" * 32)
    config = dataclasses.replace(config, segments=dataclasses.replace(
        config.segments, steps=64, dataset_rows=64, minibatches=2, episode_rows=128,
        capacity_rows=1024,
        target_kl=0.9))  # the fixture's behaviour q is far from theta: let it step
    dataplane = {"lanes": 1, "root": str(tmp_path / "live"), "source_slots": 16,
                 "prepare_slots": 8, "source_bytes": 64 * 1024**2,
                 "preparation_workers": 1, "assembly_workers": 1, "prepared_batches": 2,
                 "fast_chain": True, **SGD_PROCESS, **dataplane_extra}
    if shards:
        dataplane["shards"] = shards
    return config, dataclasses.replace(io, dataplane=dataplane)


def learner(tmp_path, monkeypatch, shards: int, **dataplane_extra: Any) -> SegmentLearner:
    config, io = parts(tmp_path, monkeypatch, shards, **dataplane_extra)
    return SegmentLearner(config, tmp_path / "owner", io)


def game(owner: SegmentLearner, index: int) -> list[Any]:
    """One terminal game with a fixed instance id (the same bytes in every arm)."""
    name = f"g{index}"
    g = Game(owner.generation, compiler=FrameCompiler(owner.input_preparer.vocabulary),
             assembler_id="node-" + name, handoff=learner_mu(owner),
             instance_id=f"instance-{index}")
    g.open(name)
    for _ in range(64 + 5):
        g.step(name)
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


def train(owner: SegmentLearner, datasets: int) -> tuple[list[Any], list[dict[str, Any]]]:
    """The main loop's own call order for that many datasets."""
    requests: list[Any] = []
    steps: list[dict[str, Any]] = []
    for _ in range(datasets):
        owner._prepare_ahead()
        assert eventually(owner._prepare, 120)
        assert owner.active is not None and owner.active_batch is not None
        # The whole frozen request: members, aux selection, minibatch partition,
        # aux owners, selection RNG state, byte budget and dataset number.
        requests.append((owner.active_batch.request, owner.active_batch.metadata))
        owner._prepare_ahead()
        while owner.active is not None:
            result = owner._step()
            steps.append({key: result.get(key) for key in STEP_FACTS})
            owner._prepare_ahead()
        owner._collect_publication(wait=True)
    return requests, steps


def run_arm(tmp_path, monkeypatch, shards: int) -> dict[str, Any]:
    owner = learner(tmp_path, monkeypatch, shards)
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
                "version": owner.version, "fenced": owner.input_fenced}
    finally:
        owner.close()


def test_one_shard_selects_and_trains_exactly_as_the_in_process_data_plane(
        tmp_path, monkeypatch):
    inline = run_arm(tmp_path / "inline", monkeypatch, 0)
    shard = run_arm(tmp_path / "shard", monkeypatch, 1)
    assert len(inline["requests"]) == 3 and inline["updates"] > 0
    assert any(request.auxiliary for request, _ in inline["requests"])
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
        os.kill(victim.process.pid, signal.SIGKILL)
        assert eventually(lambda: owner.input_fenced, 30)
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
    owner = learner(tmp_path, monkeypatch, 2)
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
        # A CP records the shard count and every shard's own selection RNG.
        recorded = owner._learning_state().input_pipeline["dataplane_shards"]
        assert recorded["count"] == 2 and [s["index"] for s in recorded["shards"]] == [0, 1]
        assert all(len(s["committed_selection_rng"]) == 3 for s in recorded["shards"])
        assert (recorded["shards"][0]["committed_selection_rng"]
                != recorded["shards"][1]["committed_selection_rng"])
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
        "source_bytes": 10, "prepare_slots": 4, "lanes": 4,
        "remote_prepare": {"endpoints": ["h:1"], "connections": 12, "client_processes": 1}}
    assert remote["connections"] == 36  # the learner's own configuration is not changed


@pytest.mark.parametrize("extra", [{"shards": True}, {"shards": 9}, {"shards": -1},
                                   {"shards": 1, "fast_verify_workers": 1},
                                   {"shards": 1, "fast_chain": False}])
def test_invalid_shard_configuration_is_refused_before_anything_starts(
        tmp_path, monkeypatch, extra):
    with pytest.raises(BoundaryError, match="dataplane_shards"):
        learner(tmp_path, monkeypatch, extra["shards"],
                **{key: value for key, value in extra.items() if key != "shards"})
