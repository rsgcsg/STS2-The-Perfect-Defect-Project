"""WP8: same-update data parallel inside the SGD process (two real gloo ranks on CPU).

Both ranks split the physical groups of the same minibatch and sum their partial
gradients, so the result equals one SGD process up to float summation order;
counters, first use, KL decisions and auxiliary uses are the same ledger.
"""
from __future__ import annotations

import os
from typing import Any

import pytest
import torch
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
from test_rl_segment_learner import read_cp
from test_rl_sgd_process import aux_uses, terminal_owner, train

from stpd.rl.data_parallel import shard_groups
from stpd.rl.sgd_process import sgd_options

pytestmark = pytest.mark.skipif(os.name != "posix", reason="real spawned SGD ranks")
PARALLEL = {"sgd_data_parallel": {"world": 2, "backend": "gloo", "timeout_seconds": 120}}


def close_tree(left, right):
    if isinstance(left, torch.Tensor):
        if left.is_floating_point():
            # Same update, different float summation order (two partial sums).
            torch.testing.assert_close(left.cpu(), right.cpu(), rtol=1e-4, atol=1e-5)
        else:
            assert torch.equal(left.cpu(), right.cpu())
    elif isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            close_tree(left[key], right[key])
    elif isinstance(left, (tuple, list)):
        assert len(left) == len(right)
        for one, two in zip(left, right, strict=True):
            close_tree(one, two)
    else:
        assert left == right


def test_two_ranks_split_each_minibatch_and_match_one_SGD_process(tmp_path, monkeypatch):
    results = {}
    for world in (1, 2):
        owner, _, _ = terminal_owner(tmp_path / f"w{world}", monkeypatch,
                                     dataplane=PARALLEL if world == 2 else None)
        try:
            events = train(owner, 3)
            owner.checkpoint(final=True)
            _, cp = read_cp(owner)
            results[world] = cp, events, aux_uses(owner)
            if world == 2:
                parallel = [e["data_parallel"] for e in events if "data_parallel" in e]
                assert len(parallel) == len(events)
                for facts in parallel:
                    assert facts["world"] == 2 and facts["mode"] == "split_rows"
                    ranks = facts["ranks"]
                    assert [r["rank"] for r in ranks] == [0, 1]
                    assert sum(r["rows"] for r in ranks) > 0
                    assert all(r["groups"] >= 1 for r in ranks)
                timing = [e["sgd_timing"] for e in events]
                assert any("replica_check_seconds" in t for t in timing)
        finally:
            owner.close()
    (one, one_events, one_aux), (two, two_events, two_aux) = results[1], results[2]
    assert one.counters == two.counters and one_aux == two_aux
    assert [e["kl_stopped"] for e in one_events] == [e["kl_stopped"] for e in two_events]
    for name in ("weights", "optimizer", "ema"):
        close_tree(getattr(one, name), getattr(two, name))
    for left, right in zip(one_events, two_events, strict=True):
        for key in ("update", "first_use", "first_use_multi", "stepped"):
            assert left[key] == right[key], key
        for key in ("td_loss", "policy_loss", "approx_kl_mu_theta", "gradient_norm"):
            assert left[key] == pytest.approx(right[key], rel=1e-4, abs=1e-6), key


def test_shard_groups_is_a_balanced_disjoint_cover():
    rows = [3, 1, 4, 1, 5, 9, 2, 6]
    parts = [shard_groups(rows, r, 2) for r in range(2)]
    assert sorted(parts[0] + parts[1]) == list(range(len(rows)))
    assert parts[0] == tuple(range(len(parts[0])))  # contiguous
    assert shard_groups([2, 2], 0, 2) == (0,) and shard_groups([2, 2], 1, 2) == (1,)
    # A rank may get nothing; it then all-reduces zeros.
    assert shard_groups([5], 0, 2) == () and shard_groups([5], 1, 2) == (0,)
    with pytest.raises(ValueError):
        shard_groups(rows, 2, 2)


def test_data_parallel_options_are_explicit():
    base = {"sgd_process": True}
    assert sgd_options(base).data_parallel is None
    assert sgd_options({**base, **PARALLEL}).data_parallel == {
        "world": 2, "backend": "gloo", "timeout_seconds": 120.0}
    for bad in ({"world": 1}, {"world": 2, "backend": "mpi"}, {"world": 2, "mode": "x"},
                {"world": 2, "timeout_seconds": 1}):
        with pytest.raises(ValueError, match="invalid_sgd_data_parallel"):
            sgd_options({**base, "sgd_data_parallel": bad})
    with pytest.raises(ValueError, match="without_explicit_selection"):
        sgd_options({"sgd_process": False, **PARALLEL})


def _replica_rank(rank: int, store: str, perturb: bool, results: Any) -> None:
    import torch
    import torch.distributed as dist

    from stpd.rl.data_parallel import replica_check

    dist.init_process_group("gloo", init_method="file://" + store, rank=rank, world_size=2)
    try:
        torch.manual_seed(7)
        model = torch.nn.Linear(3, 2)
        if perturb and rank == 1:
            with torch.no_grad():
                model.weight[0, 0] += 1e-3
        try:
            replica_check(model)
            results.put((rank, "ok"))
        except ValueError as error:
            results.put((rank, str(error)))
    finally:
        dist.destroy_process_group()


@pytest.mark.parametrize("perturb", [False, True])
def test_replica_check_fences_a_diverged_follower(tmp_path, perturb):
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    store = str(tmp_path / "store")
    ranks = [context.Process(target=_replica_rank, args=(rank, store, perturb, results))
             for rank in (0, 1)]
    for process in ranks:
        process.start()
    for process in ranks:
        process.join(120)
        assert process.exitcode == 0
    outcome = dict(results.get(timeout=5) for _ in ranks)
    expected = "data_parallel_replicas_diverged" if perturb else "ok"
    assert outcome == {0: expected, 1: expected}
