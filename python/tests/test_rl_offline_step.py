"""C-SCALE: the default-off SGD-process switch that dumps real online minibatches.

Real socket/WAL/preparation workers and a real spawned SGD child on CPU; zero
World. A dumped attempt replayed offline with the same `prepared_update` gives,
bit for bit on single-threaded CPU, the theta/Adam the next dumped attempt saw
online. GPU step times on such files are Friday facts, not claims of this test.
"""
from __future__ import annotations

import dataclasses as dc
import json
import os
import stat

import pytest
import torch
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
from test_rl41_w1 import assert_tree
from test_rl41_w1 import model as model  # noqa: F401  (fixture)

from stpd.rl import offline_step
from stpd.rl.offline_step import OfflineDumpOptions, dump_options
from stpd.rl.sgd_process import sgd_options

pytestmark = pytest.mark.skipif(os.name != "posix", reason="real spawned SGD process")


def test_switch_is_off_by_default_and_needs_the_sgd_process():
    assert sgd_options({"sgd_process": True}).offline_dump is None
    assert dump_options({}) is None
    with pytest.raises(ValueError, match="without_explicit_selection"):
        sgd_options({"sgd_offline_dump_every": 10})
    with pytest.raises(ValueError, match="without_every"):
        sgd_options({"sgd_process": True, "sgd_offline_dump_limit": 3})
    for bad in ({"sgd_offline_dump_every": 0}, {"sgd_offline_dump_every": True},
                {"sgd_offline_dump_every": 1, "sgd_offline_dump_limit": 0},
                {"sgd_offline_dump_every": 1, "sgd_offline_dump_directory": ""}):
        with pytest.raises(ValueError, match="invalid_sgd_offline_dump"):
            sgd_options({"sgd_process": True, **bad})
    assert sgd_options({"sgd_process": True, "sgd_offline_dump_every": 200}).offline_dump == (
        OfflineDumpOptions(200, 20, None))


def _owner(tmp_path, monkeypatch, layout, dataplane):
    import test_rl_sgd_process as split

    original = split.configuration

    def configured(path, patch, **kwargs):
        config, io = original(path, patch, **kwargs)
        return dc.replace(config, performance=dc.replace(
            config.performance, prepared_forward=layout)), io

    monkeypatch.setattr(split, "configuration", configured)
    return split, split.terminal_owner(tmp_path, monkeypatch, dataplane=dataplane)[0]


@pytest.mark.parametrize("layout", ["padded-v1", "rows-v2"])
def test_dumped_attempt_replays_offline_to_the_next_online_state(tmp_path, monkeypatch, layout):
    dumps = tmp_path / "dumps"
    split, owner = _owner(tmp_path / "run", monkeypatch, layout, {
        "sgd_offline_dump_every": 1, "sgd_offline_dump_limit": 3,
        "sgd_offline_dump_directory": str(dumps)})
    try:
        events = split.train(owner, 3)
        assert owner.updates == 6
    finally:
        owner.close()
    reported = [event["sgd_timing"].get("offline_dump") for event in events]
    # Update 0 is a diagnostics step (diagnostics_every=8) and is never dumped;
    # then one per attempt until the limit.
    assert [fact["update"] if fact else None for fact in reported] == [
        None, 2, 3, 4, None, None]
    assert all("error_type" not in fact for fact in reported if fact)
    roots = sorted(path for path in dumps.iterdir())
    assert [str(path) for path in roots] == [fact["path"] for fact in reported if fact]
    loaded = [offline_step.load(root) for root in roots]
    facts = [fact for fact in reported if fact]
    for root, (value, minibatch, receipt), fact in zip(roots, loaded, facts, strict=True):
        assert value["schema"] == receipt["schema"] == offline_step.SCHEMA
        assert receipt["sha256"] == fact["sha256"] and receipt["online_update"] == fact["update"]
        assert receipt["layout"] == layout and value["performance"]["prepared_forward"] == layout
        assert all((group.layout is not None) is (layout == "rows-v2")
                   for group in minibatch.groups)
        assert receipt["ppo_rows"] == minibatch.td_rows > 0
        assert receipt["dataset_rows"] > 0 and receipt["dataset_payload_bytes"] > 0
        assert receipt["minibatch_bytes_per_ppo_row"] > 0
        assert stat.S_IMODE((root / offline_step.TREE).stat().st_mode) == 0o600
    # Replay attempt k offline: theta/Adam equal what attempt k+1 saw online.
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        for (value, minibatch, _), (after, _, _) in zip(loaded, loaded[1:], strict=False):
            model, optimizer, result = offline_step.replay(value, minibatch)
            assert result["stepped"]
            assert_tree(model.state_dict(), after["weights"])
            assert_tree(optimizer.state_dict()["state"], after["optimizer"]["state"])
    finally:
        torch.set_num_threads(threads)


def test_failed_dump_is_reported_and_never_fences_training(tmp_path, monkeypatch):
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    split, owner = _owner(tmp_path / "run", monkeypatch, "padded-v1", {
        "sgd_offline_dump_every": 1, "sgd_offline_dump_directory": str(dumps)})
    dumps.chmod(0o500)  # the child cannot create its attempt directory
    try:
        events = split.train(owner, 2)
        assert owner.updates == 4 and owner.sgd_failure is None
    finally:
        dumps.chmod(0o700)
        owner.close()
    reported = [event["sgd_timing"].get("offline_dump") for event in events]
    assert reported[0] is None and reported[1]["error_type"] == "PermissionError"
    assert reported[2:] == [None, None]  # one failure ends further dumps
    assert not list(dumps.iterdir())


def test_historical_P5_snapshot_shape_still_loads(tmp_path, model):
    """`layout` absent (R42-P5 LEARNER-ENTRY files) reads as padded-v1."""
    import test_rl41_w1 as w1

    from stpd.rl.batch_preparation import assemble

    values = [w1.segment(model, name=f"actor/{i}", run_id=f"episode-{i}", rows=3)
              for i in range(2)]
    batch = assemble(w1.request(values), tuple(w1.source(model, v) for v in values))
    tree = offline_step.cpu_tree(batch.minibatches[0])
    for group in tree["groups"]:
        del group["layout"]
    offline_step.write(tmp_path / "p5", {"schema": offline_step.SCHEMA, "minibatch": tree},
                       {"online_update": 1})
    _, minibatch, receipt = offline_step.load(tmp_path / "p5")
    assert json.loads((tmp_path / "p5" / offline_step.RECEIPT).read_bytes()) == receipt
    assert minibatch.td_rows == batch.minibatches[0].td_rows
    assert all(group.layout is None for group in minibatch.groups)
    with pytest.raises(FileExistsError):  # one directory per attempt, never overwritten
        offline_step.write(tmp_path / "p5", {"schema": offline_step.SCHEMA}, {})


@pytest.mark.parametrize("kernels", ["", "cross+embed+heads+nodes+residual"])
def test_compile_warmup_on_a_dumped_minibatch_leaves_theta_and_trains(tmp_path, monkeypatch,
                                                                     kernels):
    """WP8: bz2 preflight, recompile limit, cache dir and warm-up on real dumps.

    The warm-up compiles with the run's own `performance`, levers included (e.g. the
    `residual` BF16 trunk), on the live storage layout (shared slabs): warmed on the
    dumps of the same minibatches, the run's compiled update adds no dynamo graph."""
    dumps = tmp_path / "dumps"
    split, owner = _owner(tmp_path / "dump", monkeypatch, "rows-v2", {
        "sgd_offline_dump_every": 1, "sgd_offline_dump_limit": 2,
        "sgd_offline_dump_directory": str(dumps)})
    try:
        split.train(owner, 2)
    finally:
        owner.close()
    roots = sorted(dumps.iterdir())
    assert len(roots) == 2
    original = split.configuration

    def compiled(path, patch, **kwargs):
        config, io = original(path, patch, **kwargs)
        return dc.replace(config, performance=dc.replace(
            config.performance, prepared_compile=True, prepared_kernels=kernels)), io

    monkeypatch.setattr(split, "configuration", compiled)
    cache = tmp_path / "inductor"
    warmup = [str(root) for root in roots]
    owner = split.terminal_owner(tmp_path / "warm", monkeypatch, dataplane={"sgd_compile": {
        "cache_dir": str(cache), "recompile_limit": 16, "warmup": warmup}})[0]
    try:
        # The controller already refused a ready whose theta differs from init.
        facts = owner.sgd_compile
        assert facts["bz2"] and facts["recompile_limit"] == 16 and facts["size_guard"] is False
        assert facts["cache_dir"] == str(cache) and cache.exists()
        assert [warm["path"] for warm in facts["warmup"]] == warmup
        warm = facts["warmup"][-1]
        assert warm["rows"] > 0
        assert warm["rows_forward"]["compiled"] and warm["rows_forward"]["dynamo_frames_ok"] > 0
        assert warm["rows_forward"].get("kernels", "") == kernels
        events = split.train(owner, 1)
        assert owner.updates == 2
        # Diagnostics attempts (update 0 here) run eager by design; the rest compile.
        assert [event["rows_forward"]["compiled"] for event in events] == [False, True]
        assert events[1]["rows_forward"].get("kernels", "") == kernels
        graphs = warm["rows_forward"]["dynamo_unique_graphs"]
        assert events[1]["rows_forward"]["dynamo_unique_graphs"] == graphs > 0
    finally:
        owner.close()


def test_compile_options_need_prepared_compile_and_are_explicit():
    base = {"sgd_process": True}
    assert sgd_options(base).compile is None
    for bad in ({"recompile_limit": 4}, {"warmup": "x"}, {"cache_dir": ""}, {"other": 1},
                {"size_guard": 1}):
        with pytest.raises(ValueError, match="invalid_sgd_compile"):
            sgd_options({**base, "sgd_compile": bad})
