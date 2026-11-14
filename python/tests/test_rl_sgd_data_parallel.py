"""WP8: same-update data parallel inside the SGD process (two real gloo ranks on CPU).

Both ranks split the physical groups of the same minibatch and sum their partial
gradients, so the result equals one SGD process up to float summation order;
counters, first use, KL decisions and auxiliary uses are the same ledger.
"""
from __future__ import annotations

import copy
import mmap
import os
import pickle
import threading
from pathlib import Path
from typing import Any

import pytest
import torch
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
from test_rl_segment_learner import read_cp
from test_rl_sgd_process import _first_handoff, aux_uses, terminal_owner, train

from stpd.rl import batch_preparation as bp
from stpd.rl.checkpoint_state import construct_model
from stpd.rl.data_parallel import RemoteGroup, shard_groups, shard_transport
from stpd.rl.loss import prepared_update
from stpd.rl.sgd_process import SgdProcessFailed, _DataParallel, mapped_arrays, sgd_options

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


def first_moment(cp: Any, parameter: str) -> float:
    names = [name for group in cp.optimizer_parameter_names for name in group]
    return float(cp.optimizer["state"][names.index(parameter)]["exp_avg"].abs().max())


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
            results["lr"] = owner.config.lr
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
    # The action-score bias `head.2.bias` adds one constant to every score of a
    # catalog, and scores only enter the loss through log_softmax: its exact
    # gradient is 0, so both runs only see float32 rounding noise (~1e-9) there.
    # Adam divides that noise by sqrt(v)+eps with sqrt(v) below eps, turning it
    # into a step of up to ~lr whose value depends on summation order (seen on
    # Linux CI: 1.55e-5 on a value of 0.025). The bias cannot change any policy
    # output, so it is bounded by the Adam step (updates x lr), not by rounding;
    # every other tensor keeps the rounding tolerance. The exemption is this one
    # named parameter, and its gradient must still be noise in both runs.
    null = {"head.2.bias"}
    assert all(first_moment(cp, name) < 1e-7 for cp in (one, two) for name in null)
    bound = one.counters.updates * results["lr"]
    for name, left in one.weights.items():
        right = two.weights[name]
        if name in null:
            assert float((left - right).abs().max()) <= bound, name
        else:
            close_tree(left, right)
    for name in ("optimizer", "ema"):
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


def equal_tree(left, right):
    if isinstance(left, torch.Tensor):
        assert left.dtype == right.dtype and left.shape == right.shape
        assert torch.equal(left, right)
    elif isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            equal_tree(left[key], right[key])
    elif isinstance(left, (tuple, list)):
        assert len(left) == len(right)
        for one, two in zip(left, right, strict=True):
            equal_tree(one, two)
    elif hasattr(left, "__dataclass_fields__"):
        assert type(left) is type(right)
        for name in left.__dataclass_fields__:
            equal_tree(getattr(left, name), getattr(right, name))
    else:
        assert left == right


class _Capture:
    """`gradient_sync` stand-in: records this rank's partial gradient and totals."""

    def __call__(self, model, totals, valid):
        self.grads = {name: p.grad.clone() for name, p in model.named_parameters()
                      if p.grad is not None}
        self.totals = totals.clone()
        return totals, valid


def test_each_rank_stages_only_its_groups_and_trains_on_the_same_values(tmp_path,
                                                                         monkeypatch):
    """Host path: a rank pins/copies only its own groups of each minibatch. What it
    trains on (its groups, the row tensors, denominators, order) is the same as
    from the whole file, so its partial gradient and loss totals are bitwise equal."""
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=3, dataplane=PARALLEL)
    try:
        handoff = _first_handoff(owner)
        with open(handoff.path, "rb") as stream:
            mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        specs = [(a.offset, a.count, a.dtype) for a in handoff.arrays]
        arrays = mapped_arrays(mapping, specs)
        tree = pickle.loads(handoff.tree)
        full = bp.unpack_transport((tree, tuple(a.copy() for a in arrays)))
        total = sum(a.nbytes for a in arrays)
        torch.manual_seed(11)
        base = construct_model(owner.construction_template).eval()
        staged = []
        for rank in range(2):
            part_tree, part_arrays = shard_transport(tree, arrays, rank, 2)
            staged.append(sum(a.nbytes for a in part_arrays))
            part = bp.unpack_transport((part_tree, tuple(a.copy() for a in part_arrays)))
            assert len(part.minibatches) == len(full.minibatches) >= 1
            for whole, mine in zip(full.minibatches, part.minibatches, strict=True):
                local = shard_groups([len(g.rows) for g in whole.groups], rank, 2)
                assert len(whole.groups) >= 2 and local
                for index, (group, kept) in enumerate(zip(whole.groups, mine.groups,
                                                          strict=True)):
                    if index in local:
                        equal_tree(group, kept)
                    else:
                        assert kept == RemoteGroup(tuple(group.rows), group.policy)
                for name in ("tensors", "epoch_auxiliary", "epoch_auxiliary_counts",
                             "policy_rows", "td_rows", "terminal_rows", "hp_rows"):
                    equal_tree(getattr(whole, name), getattr(mine, name))
                # Same partial gradient and totals as this rank's share of the
                # whole minibatch (the all-reduce input), bit for bit.
                captured = []
                for minibatch in (whole, mine):
                    model = copy.deepcopy(base)
                    optimizer = torch.optim.SGD(model.parameters(), lr=0.0)
                    capture = _Capture()
                    torch.manual_seed(7)
                    prepared_update(model, optimizer, minibatch, config=owner.config.loss,
                                    performance=owner.performance, target_kl=1e9,
                                    gradient_sync=capture, shard=(rank, 2))
                    captured.append(capture)
                assert captured[0].grads and torch.equal(captured[0].totals,
                                                          captured[1].totals)
                assert captured[0].grads.keys() == captured[1].grads.keys()
                for name, grad in captured[0].grads.items():
                    assert torch.equal(grad, captured[1].grads[name]), name
            for whole_field in ("request", "metadata", "policy_mask"):
                equal_tree(getattr(full, whole_field), getattr(part, whole_field))
        # Each rank copies about its share, not the whole file; together about once.
        assert all(size < total for size in staged), (staged, total)
        del arrays, full, part, part_arrays
    finally:
        owner.close()


@pytest.mark.parametrize("threaded", [False, True])
def test_two_ranks_never_start_on_bytes_rank0_did_not_verify(tmp_path, monkeypatch, threaded):
    """Only rank 0 hashes the prepared file (once per batch); a changed byte still
    fails the child loudly before any `staged`, so no rank trains on it."""
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=1,
                                 dataplane={**PARALLEL, "sgd_stage_thread": threaded})
    try:
        handoff = _first_handoff(owner)
        path = Path(handoff.path)
        with path.open("r+b") as stream:
            first = stream.read(1)
            stream.seek(0)
            stream.write(bytes([first[0] ^ 1]))
        with pytest.raises(SgdProcessFailed) as caught:
            owner._prepare()
        child = caught.value.facts["child"]
        assert child["error_code"] == "sgd_prepared_file_integrity_changed"
        assert owner.updates == 0
    finally:
        owner.close()


def test_a_raising_verify_thread_fails_the_stage_and_yields_no_staged(tmp_path, monkeypatch):
    """The hash runs on a helper thread; if it raises, staging re-raises it (the child
    then fails loudly, as for any stage error) and never returns a staged batch.
    In-process: a spawned child would not see the monkeypatch."""
    from types import SimpleNamespace

    from stpd.rl import binary_preparation
    from stpd.rl.sgd_process import _SgdWorker, _StageConfig

    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=1, dataplane=PARALLEL)
    try:
        handoff = _first_handoff(owner)
        plan = {"dataset": handoff.request.identity.sequence, "path": handoff.path,
                "tree": handoff.tree,
                "arrays": [(a.offset, a.count, a.dtype) for a in handoff.arrays],
                "used_bytes": handoff.used_bytes, "sha256": handoff.sha256,
                "minibatch_count": handoff.minibatch_count}
        worker = SimpleNamespace(device=torch.device("cpu"), rank=0, shard=None,
                                 stage_executor=None, parallel=None,
                                 staging_config=_StageConfig())
        worker._staged_fields = _SgdWorker._staged_fields.__get__(worker)
        _, fields = _SgdWorker._stage_work(worker, plan)  # control: verifies
        assert fields["verify_seconds"] > 0

        def broken(view, threads=1):
            raise RuntimeError("verify thread broke")

        monkeypatch.setattr(binary_preparation, "prepared_digest", broken)
        with pytest.raises(RuntimeError, match="verify thread broke"):
            _SgdWorker._stage_work(worker, plan)
        assert owner.updates == 0
    finally:
        owner.close()


def test_rank0_refuses_staged_when_a_follower_mapped_another_file(monkeypatch):
    parallel = _DataParallel.__new__(_DataParallel)
    parallel.world, parallel.staged_lock = 2, threading.Lock()
    parallel.deferred, parallel.follower_ready = {}, {}
    sent = []
    parallel.send = lambda kind, **fields: sent.append((kind, fields))

    def exit_(code):
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", exit_)
    verified = (1, 2, 3, 4)
    parallel.staged(1, {"file_identity": verified, "sha256": "x"})
    assert sent == []  # waits for the follower
    parallel.follower_staged(1, 1, verified)
    assert [kind for kind, _ in sent] == ["staged"]
    parallel.follower_staged(1, 2, (1, 9, 3, 4))  # follower first, then rank 0
    with pytest.raises(SystemExit):
        parallel.staged(2, {"file_identity": verified, "sha256": "y"})
    kind, fields = sent[-1]
    assert kind == "failed"
    assert fields["error_code"] == "sgd_data_parallel_file_identity_changed"
