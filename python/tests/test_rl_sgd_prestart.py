"""V3 `sgd_prestart`: the controller authorizes the next staged dataset while it
still accounts the current one, so the SGD child runs datasets back to back.

Same learning as the lockstep start, bit for bit on one CPU rank and to the
data-parallel tolerance on two gloo ranks; CP, STOP and failures stay at a
dataset boundary or fail loudly.
"""
from __future__ import annotations

import dataclasses as dc
import os
import signal
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
from test_rl41_w1 import assert_tree
from test_rl_segment_learner import read_cp
from test_rl_sgd_data_parallel import PARALLEL, close_tree
from test_rl_sgd_process import aux_uses, terminal_owner

import stpd.rl.segment_learner as segment_learner
from stpd.rl.segment_learner import SegmentLearner
from stpd.rl.sgd_process import SgdProcessFailed

pytestmark = pytest.mark.skipif(os.name != "posix", reason="real spawned SGD process")
EPISODES = 4


def train_to(owner, datasets, *, checkpoint_at=None):
    """Controller call order of `_run`; never authorizes a dataset beyond `datasets`.

    `checkpoint_at=k`: request a CP while dataset k is current (before a prestart
    of k+1 can happen); it is captured at the next quiescent boundary, i.e. after
    dataset k in both modes. Returns (step events, datasets counted in the CP)."""
    events, captured, requested = [], None, False
    original = owner._sgd_prestart

    def bounded():
        upcoming = owner.datasets + (owner.active is not None) + (owner.next_active is not None)
        if upcoming < datasets:
            original()

    owner._sgd_prestart = bounded
    while owner.datasets < datasets:
        if requested and captured is None and owner.active is None:
            owner._collect_publication(wait=True)
            owner.checkpoint()
            owner._collect_checkpoint(wait=True)
            captured = owner.datasets
        if owner.active is None:
            assert owner._prepare()
        if (checkpoint_at is not None and not requested
                and owner.datasets == checkpoint_at - 1):
            owner.checkpoint()  # active: only requested
            assert owner.checkpoint_requested
            requested = True
        owner._prepare_ahead()
        events.append(owner._step())
        owner._prepare_ahead()
        owner._collect_publication(wait=False)
    assert owner.active is None and owner.next_active is None
    owner._collect_publication(wait=True)
    if requested and captured is None:
        owner.checkpoint()
        owner._collect_checkpoint(wait=True)
        captured = owner.datasets
    return events, captured


def progress(events):
    return [(event["first_use_floors"], {key: event["first_use_policy_bias"][key]
             for key in ("expected_n", "coverage_complete", "all")},
             event["first_use_version_lag"], event["first_use_pre_update_SGD_lag"],
             event["first_use_age_seconds"]["n"])
            for kind, event in events if kind == "progress" and event["stepped"]]


def run(tmp_path, monkeypatch, *, prestart, dataplane=None, checkpoint_at=None, datasets=3):
    owner, config, io = terminal_owner(tmp_path, monkeypatch, episodes=EPISODES, dataplane={
        "sgd_stage_thread": True, "sgd_prestart": prestart, **(dataplane or {})})
    published, original = [], owner.exchange.publish_event

    def record(kind, **fields):
        published.append((kind, fields))
        return original(kind, **fields)

    owner.exchange.publish_event = record
    try:
        events, captured = train_to(owner, datasets, checkpoint_at=checkpoint_at)
        record, middle = read_cp(owner) if checkpoint_at is not None else (None, None)
        owner.checkpoint(final=True)
        _, final = read_cp(owner)
        return {"events": events, "middle": middle, "final": final, "captured": captured,
                "aux": aux_uses(owner), "prestarted": owner.timing["sgd_prestarted_batches"],
                "exit": None, "owner": owner, "published": published,
                "record": record, "config": config, "io": io}
    finally:
        owner.close()


def assert_same(left, right, *, exact=True):
    assert left["aux"] == right["aux"]
    for name in ("middle", "final"):
        one, two = left[name], right[name]
        if one is None:
            assert two is None
            continue
        assert one.counters == two.counters
        for part in ("weights", "optimizer", "ema"):
            (assert_tree if exact else close_tree)(getattr(one, part), getattr(two, part))
    assert len(left["events"]) == len(right["events"])
    for one, two in zip(left["events"], right["events"], strict=True):
        for key in ("dataset", "minibatch", "epoch", "update", "first_use", "stepped",
                    "kl_stopped"):
            assert one[key] == two[key], key
        if exact:
            for key in ("td_loss", "policy_loss"):
                assert one[key] == two[key], key


def test_prestart_keeps_learning_bitwise_equal_to_lockstep_on_CPU(tmp_path, monkeypatch):
    lockstep = run(tmp_path / "lockstep", monkeypatch, prestart=False)
    prestart = run(tmp_path / "prestart", monkeypatch, prestart=True)
    assert lockstep["prestarted"] == 0
    assert prestart["prestarted"] >= 1  # datasets 2 and/or 3 started back to back
    assert_same(lockstep, prestart)
    for result in (lockstep, prestart):
        assert result["owner"].sgd.exit["exitcode"] == 0
    # First-use summaries are now built in the telemetry thread: same content.
    built = [progress(result["published"]) for result in (lockstep, prestart)]
    assert built[0] == built[1] and len(built[0]) == len(lockstep["events"])
    assert all(row[1]["coverage_complete"] for row in built[0])


def test_prestart_CP_mid_run_equals_the_lockstep_CP(tmp_path, monkeypatch):
    """A CP request stops further prestart; the CP is captured at the next
    quiescent boundary, with theta/Adam at exactly the controller's counters."""
    lockstep = run(tmp_path / "lockstep", monkeypatch, prestart=False, checkpoint_at=2)
    prestart = run(tmp_path / "prestart", monkeypatch, prestart=True, checkpoint_at=2)
    assert lockstep["captured"] == prestart["captured"]
    assert prestart["middle"].counters.datasets == prestart["captured"]
    assert_same(lockstep, prestart)
    # A new learner restores the mid-run prestart CP to exactly that state.
    config, io = prestart["config"], prestart["io"]
    request = dc.replace(
        config, start_package=None,
        resume_checkpoint=prestart["record"]["checkpoint_artifact_id"],
        segments=dc.replace(config.segments, training_generation="g-prestart-restore"))
    restored = SegmentLearner(request, tmp_path / "restored", dc.replace(
        io, dataplane={**io.dataplane, "root": str(tmp_path / "live-restored")}))
    try:
        state, cp = restored._learning_state(), prestart["middle"]
        assert restored.updates == cp.counters.updates
        assert restored.datasets == cp.counters.datasets == lockstep["captured"]
        for part in ("weights", "optimizer", "ema"):
            assert_tree(getattr(cp, part), getattr(state, part))
            assert_tree(getattr(lockstep["middle"], part), getattr(state, part))
    finally:
        restored.close()


def test_prestart_two_gloo_ranks_stay_in_lockstep_and_match(tmp_path, monkeypatch):
    lockstep = run(tmp_path / "lockstep", monkeypatch, prestart=False, dataplane=PARALLEL)
    prestart = run(tmp_path / "prestart", monkeypatch, prestart=True, dataplane=PARALLEL)
    assert prestart["prestarted"] >= 1
    # Both ranks ran the same step sequence (a mismatch hangs or fails a collective);
    # theta matches the lockstep run to the data-parallel tolerance.
    assert_same(lockstep, prestart, exact=False)
    assert prestart["owner"].sgd.exit["exitcode"] == 0


def test_STOP_is_honoured_at_the_next_dataset_boundary(tmp_path, monkeypatch):
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=EPISODES, dataplane={
        "sgd_stage_thread": True, "sgd_prestart": True})
    try:
        assert owner._prepare()
        (owner.work / "STOP").write_text("stop")
        while owner.active is not None:
            owner._prepare_ahead()
            owner._step()
        assert owner.next_active is None and owner.datasets == 1
        assert owner.timing["sgd_prestarted_batches"] == 0
    finally:
        owner.close()


def test_a_checkpoint_request_is_never_crossed_by_a_prestart(tmp_path, monkeypatch):
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=EPISODES, dataplane={
        "sgd_stage_thread": True, "sgd_prestart": True})
    try:
        assert owner._prepare()
        owner.checkpoint()
        assert owner.checkpoint_requested
        while owner.active is not None:
            owner._prepare_ahead()
            owner._step()
        assert owner.next_active is None and owner.datasets == 1
        owner._collect_publication(wait=True)
        owner.checkpoint()
        owner._collect_checkpoint(wait=True)
        assert read_cp(owner)[1].counters.datasets == 1
    finally:
        owner.close()


def test_child_death_with_a_prestarted_dataset_fails_loudly(tmp_path, monkeypatch):
    """The child dies right after the controller authorized the next dataset
    (its `start` is sent; the current dataset's answer is still awaited)."""
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=EPISODES, dataplane={
        "sgd_stage_thread": True, "sgd_prestart": True})
    original, killed = owner._sgd_prestart, []

    def prestart_then_kill():
        original()
        if owner.next_active is not None and not killed:
            os.kill(owner.sgd.pid, signal.SIGKILL)
            killed.append(owner.next_active[1].request.identity.sequence)

    owner._sgd_prestart = prestart_then_kill
    try:
        with pytest.raises(SgdProcessFailed):
            for _ in range(50):
                if owner.active is None:
                    # Read-ahead datasets prepared before this one starts, so the
                    # next one is planned and staged while this one trains.
                    owner._prepare_ahead()
                    for _, future in owner._pending_batches():
                        future.result(timeout=60)
                    assert owner._prepare()
                owner._prepare_ahead()
                owner._step()
        assert killed  # the failure came with a prestarted dataset outstanding
        assert owner.sgd.failure is not None
    finally:
        owner.close()


def test_invalid_prestart_option_is_refused(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="invalid_sgd_prestart"):
        terminal_owner(tmp_path, monkeypatch, dataplane={"sgd_prestart": 1})


def telemetry_owner(publish):
    owner = SegmentLearner.__new__(SegmentLearner)
    owner.telemetry = ThreadPoolExecutor(max_workers=1)
    owner.telemetry_futures = deque()
    owner.generation, owner.config = "g", SimpleNamespace(operating_budget=None)
    owner.exchange = SimpleNamespace(publish_event=publish)
    return owner


def test_a_full_telemetry_queue_fails_loudly_and_drops_nothing(monkeypatch):
    monkeypatch.setattr(segment_learner, "TELEMETRY_QUEUE_LIMIT", 3)
    release, published = threading.Event(), []

    def publish(kind, **fields):
        release.wait(30)
        published.append(fields["n"])

    owner = telemetry_owner(publish)
    try:
        for n in range(3):
            owner._event("progress", n=n)  # never waits on the blocked thread
        with pytest.raises(RuntimeError, match="telemetry_queue_full"):
            owner._event("progress", n=3)
    finally:
        release.set()
        owner.telemetry.shutdown(wait=True)
    assert published == [0, 1, 2]


def test_a_telemetry_error_surfaces_on_the_next_event():
    def publish(kind, **fields):
        raise OSError("event store down")

    owner = telemetry_owner(publish)
    try:
        owner._event("progress", _build=lambda: {"built": 1})
        owner.telemetry_futures[0].exception(timeout=30)
        with pytest.raises(OSError, match="event store down"):
            owner._event("progress")
    finally:
        owner.telemetry.shutdown(wait=True)


def test_memory_is_sampled_at_most_every_few_seconds(monkeypatch):
    owner = SegmentLearner.__new__(SegmentLearner)
    owner._init_progress_telemetry()
    owner.device, owner.sgd, owner.sgd_memory = SimpleNamespace(type="cpu"), object(), {"a": 1}
    calls, clock = [], [100.0]
    monkeypatch.setattr(segment_learner, "_process_memory", lambda: calls.append(1) or {"x": 1})
    monkeypatch.setattr(segment_learner.time, "monotonic", lambda: clock[0])
    assert owner._memory() == {"x": 1, "sgd_process": {"a": 1}}
    clock[0] += segment_learner.MEMORY_SAMPLE_SECONDS - 0.1
    owner.sgd_memory = {"a": 2}  # the child's latest report is always current
    assert owner._memory() == {"x": 1, "sgd_process": {"a": 2}} and len(calls) == 1
    clock[0] += 0.2
    owner._memory()
    assert len(calls) == 2
