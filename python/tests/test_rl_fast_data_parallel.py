"""RL5 fast chain on two SGD ranks (WP8 same-update data parallel, real gloo ranks on CPU).

The fast-chain learner (attested fast segments -> binary preparer -> prepared-batch
file -> SGD process) with `sgd_data_parallel` world 2 must learn the same update as
one SGD process, publish mu and write the CP from rank 0 only, and stop loudly when
any rank dies.
"""
from __future__ import annotations

import dataclasses
import os
import time
from typing import Any

import pytest
import torch
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
from test_rl41_w1 import configuration
from test_rl_fast_learner import admission, learner_mu, offer
from test_rl_fast_source import Game
from test_rl_pipeline_checkpoint import SGD_PROCESS
from test_rl_segment_learner import read_cp
from test_rl_sgd_data_parallel import close_tree, first_moment
from test_rl_sgd_process import train

from stpd.p1.frame_compiler import FrameCompiler
from stpd.rl.segment_learner import SegmentLearner
from stpd.rl.sgd_process import SgdProcessFailed

pytestmark = pytest.mark.skipif(os.name != "posix", reason="real spawned SGD ranks")
PARALLEL = {"sgd_data_parallel": {"world": 2, "backend": "gloo", "timeout_seconds": 120}}
GAMES = 3


def fast_sgd_learner(tmp_path, monkeypatch, *, world: int) -> SegmentLearner:
    from stpd.p1.local_inference import AUTH_ENV

    tmp_path.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(20261112)  # the start package draws from the global torch RNG
    config, io = configuration(tmp_path, monkeypatch, generation="g-fast-dp")
    monkeypatch.setenv(AUTH_ENV, "ab" * 32)
    config = dataclasses.replace(config, segments=dataclasses.replace(
        config.segments, steps=64, dataset_rows=64, minibatches=2, episode_rows=128,
        capacity_rows=512,
        target_kl=0.9))  # the fixture's behaviour q is far from theta: let it step
    dataplane = {"lanes": 1, "root": str(tmp_path / "live"), "source_slots": 16,
                 "prepare_slots": 4, "source_bytes": 32 * 1024**2,
                 "preparation_workers": 1, "assembly_workers": 1, "prepared_batches": 2,
                 "fast_chain": True, **SGD_PROCESS, **(PARALLEL if world == 2 else {})}
    return SegmentLearner(config, tmp_path / "owner", dataclasses.replace(io, dataplane=dataplane))


def feed(owner: SegmentLearner) -> None:
    """The same attested fast segments (fixed instance ids) for every world size."""
    for index in range(1, GAMES + 1):
        name = f"g{index}"
        game = Game(owner.generation, compiler=FrameCompiler(owner.input_preparer.vocabulary),
                    assembler_id="node-" + name, handoff=learner_mu(owner),
                    instance_id=f"instance-{index}")
        game.open(name)
        for _ in range(64 + 5):
            game.step(name)
        game.step(name, terminal=True)
        game.close(name, "terminal")
        admission(owner, name, index)
        for segment in game.sealed:
            offer(owner, segment)


def test_fast_chain_two_ranks_match_one_SGD_process_and_rank0_alone_publishes(
        tmp_path, monkeypatch):
    results: dict[int, Any] = {}
    lr = 0.0
    for world in (1, 2):
        owner = fast_sgd_learner(tmp_path / f"w{world}", monkeypatch, world=world)
        try:
            assert owner.sgd is not None
            feed(owner)
            events = train(owner, 2)
            owner.checkpoint(final=True)
            _, cp = read_cp(owner)
            # mu is published from rank 0's snapshots only (the controller's one child).
            published = owner.published_updates
            results[world] = cp, events, published
            lr = owner.config.lr
            if world == 2:
                parallel = [e["data_parallel"] for e in events]
                assert parallel and all(f["world"] == 2 and f["mode"] == "split_rows"
                                        for f in parallel)
                assert all([r["rank"] for r in f["ranks"]] == [0, 1] for f in parallel)
                # The fast batch really is split: both ranks ran rows of each minibatch.
                assert all(all(r["rows"] > 0 for r in f["ranks"]) for f in parallel)
                assert any("replica_check_seconds" in e["sgd_timing"] for e in events)
        finally:
            owner.close()
    (one, one_events, one_published), (two, two_events, two_published) = results[1], results[2]
    assert one.counters == two.counters
    assert one_published == two_published == one.counters.updates >= 1
    assert [e["kl_stopped"] for e in one_events] == [e["kl_stopped"] for e in two_events]
    null = {"head.2.bias"}  # exact-zero gradient: see test_rl_sgd_data_parallel
    assert all(first_moment(cp, name) < 1e-7 for cp in (one, two) for name in null)
    bound = one.counters.updates * lr  # Adam moves it at most lr per update
    for name, left in one.weights.items():
        if name in null:
            assert float((left - two.weights[name]).abs().max()) <= bound, name
        else:
            close_tree(left, two.weights[name])
    for name in ("optimizer", "ema"):
        close_tree(getattr(one, name), getattr(two, name))
    for left, right in zip(one_events, two_events, strict=True):
        for key in ("update", "first_use", "stepped"):
            assert left[key] == right[key], key
        for key in ("td_loss", "policy_loss", "approx_kl_mu_theta", "gradient_norm"):
            assert left[key] == pytest.approx(right[key], rel=1e-4, abs=1e-6), key


def test_a_dead_follower_rank_stops_the_fast_learner_loudly(tmp_path, monkeypatch):
    import psutil

    owner = fast_sgd_learner(tmp_path, monkeypatch, world=2)
    try:
        assert owner.sgd is not None
        feed(owner)
        train(owner, 1)
        sgd = psutil.Process(owner.sgd.process.pid)
        followers = [child for child in sgd.children()
                     if "resource_tracker" not in " ".join(child.cmdline())]
        assert len(followers) == 1, [child.cmdline() for child in sgd.children()]
        followers[0].kill()
        with pytest.raises(SgdProcessFailed) as raised:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                train(owner, 1)
                time.sleep(0.2)
        facts = raised.value.facts
        assert "data_parallel_rank" in repr(facts), facts
        assert sgd.wait(30) is not None or not sgd.is_running()
    finally:
        owner.close()
