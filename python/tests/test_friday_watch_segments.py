"""Normal operational facts, not gameplay or GPU qualification."""

from __future__ import annotations

import json
import time
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_friday_watch import watch as original_watch_fixture


@pytest.fixture
def watch():
    return original_watch_fixture.__wrapped__()


@pytest.fixture
def owner(watch, tmp_path):
    config = {
        "run": "ops-watch-test",
        "budget_id": "ops-budget",
        "peer": {"kind": "local", "directory": str(tmp_path / "store")},
        "deadline_at": time.time() + 10800,
        "training_seconds": 72000,
        "actors": 1,
        "stall_seconds": 60,
    }
    return watch.SegmentWatcher(config, tmp_path / "watch")


def event(owner, kind, **fields):
    return owner.exchange.publish_event(kind, budget_id=owner.config["budget_id"], **fields)


def attempt(owner, *, budget_start=0):
    value = {
        "root": str(owner.work / "attempt"),
        "generation": "g-one",
        "checkpoint": {"updates": 362},
        "job_id": "learner",
        "actor_plans": ["actor-plan"],
        "budget_start_seconds": budget_start,
        "active_seconds": 0,
        "replaced": [],
    }
    owner.state["attempts"].append(value)
    owner.state["active"] = 0
    return value


def test_close_before_offer_is_one_world_and_never_reopens(owner):
    attempt(owner)
    event(
        owner,
        "actor_games_closed",
        actor="actor",
        games=[
            {
                "intent_id": "intent",
                "seed": "seed",
                "status": "finished",
                "world_mutation_offered": True,
                "training_generation": "g-one",
            }
        ],
    )
    owner.collect_events()
    event(
        owner,
        "actor_world_offered",
        actor="actor",
        intent_id="intent",
        seed="seed",
        training_generation="g-one",
    )
    owner.collect_events()
    assert owner.state["games_total"] == 1
    assert not owner.state["open_worlds"] and not owner.state["lost_world_offers"]
    owner.collect_events()
    assert owner.state["games_total"] == 1


def test_terminal_actor_before_delayed_offer_counts_lost_delivery_without_inventing_close(owner):
    attempt(owner)
    event(
        owner,
        "actor_retired",
        actor="actor",
        unresolved_episodes=["intent"],
        training_generation="g-one",
    )
    owner.collect_events()
    assert owner.state["games_total"] == 0
    assert owner.state["unresolved_admissions"] == ["intent"]
    event(
        owner,
        "actor_world_offered",
        actor="actor",
        intent_id="intent",
        seed="seed",
        training_generation="g-one",
    )
    owner.collect_events()
    assert owner.state["games_total"] == 1
    assert owner.state["lost_world_offers"] == ["intent"]
    assert owner.state["unknown_intents"] == []  # No fabricated original ledger result.
    assert owner.state["health"]["lost_offers"] == 1
    assert not owner.state["unresolved_admissions"] and not owner.state["open_worlds"]


def test_original_unknown_close_cannot_be_upgraded_by_later_close(owner):
    event(
        owner,
        "actor_games_closed",
        actor="actor",
        games=[
            {
                "intent_id": "intent",
                "seed": "seed",
                "status": "unknown",
                "world_mutation_offered": True,
            }
        ],
    )
    owner.collect_events()
    event(
        owner,
        "actor_games_closed",
        actor="actor",
        games=[
            {
                "intent_id": "intent",
                "seed": "seed",
                "status": "finished",
                "world_mutation_offered": True,
            }
        ],
    )
    with pytest.raises(ValueError, match="immutable_actor_close_changed"):
        owner.collect_events()


def test_fresh_heartbeat_does_not_hide_stalled_updates(owner, watch, monkeypatch):
    value = attempt(owner)
    event(owner, "training_started", training_generation="g-one", active_seconds=0, updates=362)
    owner.collect_events()
    began = value["last_progress_at"]
    monkeypatch.setattr(watch.time, "time", lambda: began + 3600)
    event(owner, "heartbeat", training_generation="g-one", active_seconds=3600, updates=362)
    owner.collect_segment_evaluation = lambda: None
    owner.schedule_segment_evaluation = lambda: None
    owner.retain_segment_checkpoints = lambda: None
    owner.offer_initial_actors = lambda _: None
    owner.replace_actors = lambda _: None
    stops = []
    owner.stop_attempt = lambda _, reason: stops.append(reason)
    monkeypatch.setattr(watch, "job_accounting", lambda _: {"terminal": False})
    owner.tick_segment()
    assert owner.state["stop"] == "learner_progress_stalled"
    assert stops == ["learner_progress_stalled"]


def test_recovered_budget_anchor_adds_new_window_once(owner):
    attempt(owner, budget_start=3600)
    owner.state["training_seconds"] = 3600
    event(owner, "training_window", training_generation="g-one", active_seconds=100, updates=363)
    owner.collect_events()
    assert owner.state["training_seconds"] == 3700
    owner.collect_events()
    assert owner.state["training_seconds"] == 3700


@pytest.mark.parametrize("terminal", [False, True])
def test_failed_event_stops_before_live_or_terminal_allocation_can_recover(
        owner, watch, monkeypatch, terminal):
    value = attempt(owner)
    value["actor_plans"] = []
    event(owner, "failed", training_generation="g-one", error_type="KeyError", updates=375)
    owner.collect_segment_evaluation = lambda: None
    owner.schedule_segment_evaluation = lambda: None
    owner.retain_segment_checkpoints = lambda: None
    stops = []
    owner.stop_attempt = lambda _, reason, **kwargs: stops.append(reason)
    owner.start_attempt = lambda: pytest.fail("a typed learner failure requires diagnosis")
    monkeypatch.setitem(watch.sys.modules, "rl", SimpleNamespace())  # No actor allocations here.
    monkeypatch.setattr(watch, "job_accounting", lambda _: {"terminal": terminal})
    owner.tick_segment()
    assert owner.state["stop"] == "learner_failed_requires_diagnosis"
    assert value["failed"]["updates"] == 375 and stops
    if terminal:
        assert owner.state["active"] is None
        owner.tick_segment()
        assert len(owner.state["attempts"]) == 1


def test_learner_failure_classes_are_a_closed_table(watch):
    """OPS design §2 (a): only process-level SGD deaths and CUDA OOM are recoverable, and
    only when the event's own terminal error is the SGD fence/abort outcome."""
    cls = watch.learner_failure_class
    fence = {'error_type': 'BoundaryError', 'error_code': watch.SGD_FENCE_CODE}
    exited = {'error_type': 'SgdProcessFailed',
              'sgd_failure': {'code': 'sgd_process_exited', 'signal': 'SIGKILL', 'exitcode': -9}}
    assert cls(exited)['recoverable'] is True and cls(exited)['signal'] == 'SIGKILL'
    assert cls({**fence, 'sgd_failure': {'code': 'sgd_process_stalled'}})['recoverable'] is True
    oom = {'error_type': 'SgdProcessFailed', 'sgd_failure': {
        'code': 'sgd_process_failed', 'child': {'error_type': 'OutOfMemoryError'}}}
    assert cls(oom)['rule'] == 'out_of_memory'
    assert cls({'error_type': 'OutOfMemoryError'})['recoverable'] is True
    for bad in ({'error_type': 'KeyError'}, {'error_type': 'IntegrityError'},
                {**fence, 'sgd_failure': {'code': 'sgd_process_failed',
                                          'child': {'error_type': 'ValueError'}}},
                {**fence, 'sgd_failure': {'code': 'sgd_process_exited',
                                          'protocol_violation': True}},
                # exitcode 1 without a signal: an unexplained child error, not a death
                {**fence, 'sgd_failure': {'code': 'sgd_process_exited', 'signal': None,
                                          'exitcode': 1}},
                # sticky sgd_failure behind a different terminal error (e.g. in close)
                {**exited, 'error_type': 'ValueError', 'error_code': None},
                {**exited, 'finalization_failure': {'error_type': 'OSError'}},
                {'error_type': 'OutOfMemoryError', 'finalization_failure': {'stage': 'cp'}},
                {'sgd_failure': {'code': 'sgd_process_stalled'}},
                {'failure': {'fatal': True}, 'error_type': 'OutOfMemoryError'},
                {'sgd_failure': 'garbled'}, {}):
        assert cls(bad)['recoverable'] is False, bad
    assert cls({**exited, 'error_type': 'ValueError'})['rule'] == 'terminal_error_not_sgd_fence'


def test_recoverable_learner_crash_starts_a_new_attempt_after_terminal(owner, watch,
                                                                     monkeypatch):
    """CPU-only fault injection of a SIGKILLed SGD child: no global stop, one typed
    recovery record and alarm, and once learner and actors are terminal the normal
    boundary starts a new attempt (select_checkpoint picks the newest complete CP, or the
    window's pinned parent before its first CP: #388)."""
    value = attempt(owner)
    value["actor_plans"] = []
    event(owner, "failed", training_generation="g-one", error_type="SgdProcessFailed",
          updates=380, sgd_failure={"code": "sgd_process_exited", "signal": "SIGKILL",
                                    "exitcode": -9})
    owner.collect_segment_evaluation = lambda: None
    owner.schedule_segment_evaluation = lambda: None
    owner.retain_segment_checkpoints = lambda: None
    stops, starts, alarms = [], [], []
    owner.stop_attempt = lambda _, reason, **kwargs: stops.append(reason)
    owner.start_attempt = lambda: starts.append(1)
    owner.telemetry = lambda kind, **f: alarms.append((kind, f.get('reason')))
    monkeypatch.setitem(watch.sys.modules, "rl", SimpleNamespace())
    terminal = {"value": False}
    monkeypatch.setattr(watch, "job_accounting", lambda _: {"terminal": terminal["value"]})
    owner.tick_segment()
    owner.tick_segment()  # still draining: one record, one alarm
    assert owner.state["stop"] is None
    assert stops == ["learner_crash_recoverable"] * 2
    [record] = owner.state["recoveries"]
    assert record["updates"] == 380 and record["class"]["rule"] == "sgd_process_level"
    assert ("attention", "learner_crash_recoverable_new_attempt") in alarms
    assert alarms.count(("attention", "learner_crash_recoverable_new_attempt")) == 1
    terminal["value"] = True
    owner.tick_segment()  # learner terminal, no actors: attempt closes
    assert owner.state["active"] is None and starts == []
    owner.tick_segment()
    assert starts == [1]

    # Allocation already terminal when the failure is first seen: same single record.
    second = attempt(owner)
    second.update(actor_plans=[], generation="g-two")
    owner.state["active"] = 1
    event(owner, "failed", training_generation="g-two", error_type="OutOfMemoryError",
          updates=390)
    owner.tick_segment()
    assert owner.state["active"] is None and len(owner.state["recoveries"]) == 2
    assert owner.state["recoveries"][1]["class"]["rule"] == "out_of_memory"
    assert owner.state["stop"] is None


def test_same_cause_recoveries_from_one_checkpoint_stop_for_diagnosis(owner, watch,
                                                                      monkeypatch):
    """A deterministic OOM after the newest CP must not loop on GPU-hours: two recoveries
    with one rule that restored one CP stop the run (max_recoveries is only the outer
    bound). The real start_attempt runs up to the launch, which a sentinel replaces."""
    owner.config.update(max_recoveries=14, cpu_only=True)
    for name in ("collect_segment_evaluation", "schedule_segment_evaluation",
                 "retain_segment_checkpoints"):
        setattr(owner, name, lambda: None)
    owner.stop_attempt = lambda *a, **k: None
    alarms = []
    owner.telemetry = lambda kind, **f: alarms.append(f.get('reason'))
    monkeypatch.setitem(watch.sys.modules, "rl", SimpleNamespace())
    monkeypatch.setattr(watch, "job_accounting", lambda _: {"terminal": True})
    restores = iter([("cp-a", 362, 100.0), ("cp-b", 380, 200.0), ("cp-b", 380, 200.0)])

    def select():
        identity, updates, published = next(restores)
        owner.selected_checkpoint_published_at = published
        return {"checkpoint_artifact_id": identity, "updates": updates}
    owner.select_checkpoint = select

    class Launched(Exception):
        pass

    def no_launch(cp, *_, **__):
        raise Launched(cp["checkpoint_artifact_id"])
    monkeypatch.setattr(watch, "checkpoint_link", no_launch)
    real, launched = owner.start_attempt, []

    def start():  # the real cap checks; stop at the launch and record a new attempt
        try:
            real()
        except Launched as cp:
            new = attempt(owner)
            new.update(actor_plans=[], generation=f"g-{len(launched)}",
                       checkpoint={"checkpoint_artifact_id": str(cp), "updates": 0})
            owner.state["active"] = len(owner.state["attempts"]) - 1
            launched.append(str(cp))
    owner.start_attempt = start
    first = attempt(owner)
    first.update(actor_plans=[], checkpoint={"checkpoint_artifact_id": "cp-a", "updates": 362})
    for generation, updates in (("g-one", 390), ("g-0", 395), ("g-1", 396)):
        event(owner, "failed", training_generation=generation, error_type="OutOfMemoryError",
              updates=updates)
        owner.tick_segment()  # terminal: one recovery row, attempt closes
        owner.tick_segment()  # next attempt, or the same-cause stop
    assert launched == ["cp-a", "cp-b"]
    assert owner.state["stop"] == "learner_recovery_limit_requires_diagnosis"
    assert len(owner.state["attempts"]) == 3
    rows = owner.state["recoveries"]
    assert [r["restored_checkpoint_artifact_id"] for r in rows] == ["cp-a", "cp-b", "cp-b"]
    assert rows[0]["updates_lost"] == 28 and rows[0]["error_type"] == "OutOfMemoryError"
    assert rows[0]["from_checkpoint_artifact_id"] == "cp-a"
    assert rows[0]["checkpoint_age_seconds"] == rows[0]["failed_at"] - 100.0
    assert alarms.count("learner_crash_recoverable_new_attempt") == 3
    assert alarms[-1] == "learner_recovery_limit_requires_diagnosis"


def test_named_diagnosed_finding_allows_new_owner_without_deleting_history(owner, watch, tmp_path):
    failed = event(owner, "integrity_failed", reason="sampled_original_audit_failed")
    owner.collect_events()
    assert owner.state["stop"] == "integrity_failed_requires_diagnosis"
    key = next(
        k
        for k in owner.exchange.blobs.keys(owner.exchange.prefix + "learner-events/")
        if owner.exchange.event_record(k)["kind"] == "integrity_failed"
    )
    config = {**owner.config, "acknowledged_integrity_events": {key: failed["record_sha256"]}}
    replacement = watch.SegmentWatcher(config, tmp_path / "replacement")
    replacement.collect_events()
    assert replacement.state["stop"] is None
    assert replacement.exchange.event_record(key) == failed
    config["acknowledged_integrity_events"][key] = "0" * 64
    rejected = watch.SegmentWatcher(config, tmp_path / "wrong-ack")
    rejected.collect_events()
    assert rejected.state["stop"] == "integrity_failed_requires_diagnosis"


def test_uncertain_sbatch_is_never_reoffered(owner, watch, tmp_path, monkeypatch):
    script = tmp_path / "worker.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    calls = []

    def unknown(command):
        calls.append(command)
        raise OSError("submission reply lost")

    monkeypatch.setattr(watch.scheduler, "user_submission_lock", nullcontext)
    monkeypatch.setattr(watch.scheduler, "output", unknown)
    with pytest.raises(OSError):
        owner.submit_cpu_step(script, tmp_path / "job", name="test", cpus=1, minutes=1, memory_gb=1)
    with pytest.raises(
        ValueError, match="original_submission_intent_requires_reconciliation_no_retry"
    ):
        owner.submit_cpu_step(script, tmp_path / "job", name="test", cpus=1, minutes=1, memory_gb=1)
    assert len(calls) == 1


def test_expired_absolute_deadline_prevents_even_evaluation_preparation(owner):
    owner.config.update(deadline_at=time.time() - 1, evaluation={"seconds": 7200})
    owner.state["training_seconds"] = 7200
    owner.state["stop"] = "run_budget_complete"
    owner.select_checkpoint = lambda: pytest.fail("no CP selection or new offer after deadline")
    owner.schedule_segment_evaluation()
    assert owner.state["pending"] is None


def test_previous_watch_state_must_bind_the_same_run_peer_and_budget(owner, watch, tmp_path):
    foreign = watch.SegmentWatcher({**owner.config, "run": "other-run"}, tmp_path / "foreign")
    foreign.state["attempts"] = [{"job_id": "foreign-job", "generation": "foreign-generation"}]
    foreign.state["active"] = 0
    foreign.persist()
    reference = {"path": str(foreign.state_path), "sha256": watch.hashed(foreign.state_path)}
    with pytest.raises(ValueError, match="previous_watch_run_peer_and_budget_identity_changed"):
        watch.SegmentWatcher(
            {**owner.config, "previous_watch_state": reference}, tmp_path / "rejected"
        )
    owner.persist()
    reference = {"path": str(owner.state_path), "sha256": watch.hashed(owner.state_path)}
    resumed = watch.SegmentWatcher(
        {**owner.config, "previous_watch_state": reference}, tmp_path / "new-work"
    )
    assert resumed.state["previous_config_sha256"] == owner.state["config_sha256"]
    assert resumed.state["configuration"]["path"] == str(resumed.work / "watch-config.json")


def _tick_with_closes(owner, watch, monkeypatch, closed, unknown, *, age=0.0):
    value = attempt(owner)
    value["actor_plans"] = []
    games = [{"intent_id": f"done-{i}", "seed": f"seed-{i}",
              "status": "unknown" if i < unknown else "finished",
              "reason": "runtime_execution_or_release_unknown" if i < unknown else None,
              "world_mutation_offered": True, "training_generation": "g-one"}
             for i in range(closed)]
    event(owner, "actor_games_closed", actor="actor", games=games)
    for i in range(5):
        event(owner, "actor_world_offered", actor="actor", intent_id=f"inflight-{i}",
              seed=f"other-{i}", training_generation="g-one")
    owner.collect_segment_evaluation = lambda: None
    owner.schedule_segment_evaluation = lambda: None
    owner.retain_segment_checkpoints = lambda: None
    owner.offer_initial_actors = lambda _: None
    stops, alarms = [], []
    owner.stop_attempt = lambda _, reason: stops.append(reason)
    owner.telemetry = lambda kind, **f: alarms.append((kind, f))
    monkeypatch.setattr(watch, "job_accounting", lambda _: {"terminal": False})
    if age:
        owner.collect_events()
        for fact in owner.state["worlds"].values():
            if "closed_at" in fact:
                fact["closed_at"] -= age
    owner.tick_segment()
    return stops, alarms


# Card U4 C: rolling 30-minute window of global closes, denominator >= 100 closes.
# >= 10% is a typed attention alarm (training continues); >= 20% stops.
@pytest.mark.parametrize("closed,unknown,level", [
    (20, 6, None), (100, 9, None), (100, 10, "attention"), (100, 19, "attention"),
    (100, 20, "stop"), (500, 60, "attention"), (500, 99, "attention"), (500, 100, "stop")])
def test_rolling_unknown_window_attention_and_stop(owner, watch, monkeypatch,
                                                   closed, unknown, level):
    stops, alarms = _tick_with_closes(owner, watch, monkeypatch, closed, unknown)
    window = owner.state["unknown_window"]
    assert (window["closes"], window["unknown"], window["level"]) == (closed, unknown, level)
    assert owner.state["stop"] == ("training_unknown_fraction_above_limit"
                                   if level == "stop" else None)
    assert stops == (["training_unknown_fraction_above_limit"] if level == "stop" else [])
    reasons = [f["reason"] for kind, f in alarms if kind == "attention"]
    if level is None:
        assert reasons == []
    else:
        assert reasons == ["training_unknown_window_at_or_above_stop_fraction" if level == "stop"
                           else "training_unknown_window_at_or_above_attention_fraction_"
                                "lead_investigates"]
        assert owner.state["unknown_alarms"][-1]["unknown"] == unknown
    if level == "stop":
        assert owner.state["stop_detail"]["rule"] == "rolling_unknown_window"
        assert owner.state["stop_detail"]["window"]["fraction"] >= .2
    # Every unknown was typed at close; none of these has a classifying fact.
    assert window["by_category"] == ({"unclassified": unknown} if unknown else {})
    assert owner.state["unknown_overview"]["unknown"] == unknown


def test_unknowns_older_than_the_window_never_stop(owner, watch, monkeypatch):
    stops, alarms = _tick_with_closes(owner, watch, monkeypatch, 100, 50, age=1801)
    assert owner.state["unknown_window"]["closes"] == 0 and owner.state["stop"] is None
    assert stops == [] and alarms == []
    assert owner.state["unknown_overview"]["unknown"] == 50  # still counted in totals


def test_attention_alarm_repeats_at_most_once_per_window(owner, watch, monkeypatch):
    _, alarms = _tick_with_closes(owner, watch, monkeypatch, 100, 12)
    owner.tick_segment()
    owner.tick_segment()
    assert [kind for kind, _ in alarms].count("attention") == 1
    assert owner.state["stop"] is None and len(owner.state["unknown_alarms"]) == 1


def test_unknown_window_settings_are_explicit_and_bounded(watch):
    assert watch.unknown_window_settings({}) == watch.UNKNOWN_WINDOW_DEFAULTS
    for bad in ({"stop_fraction": .05}, {"window_seconds": 60}, {"other": 1},
                {"attention_fraction": True}):
        with pytest.raises(ValueError, match="explicit_unknown_window_settings_required"):
            watch.unknown_window_settings({"unknown_window": bad})


def test_c11_replay_rolling_window_alarms_before_it_stops(watch):
    """c11 (no node exclusion) crossed 10% about 27 min before its 03:42 5% stop and 20%
    about 19 min before; without the two bad nodes the window stays below 3%."""
    import json
    from pathlib import Path

    fixture = json.loads((Path(__file__).parent
                          / "fixtures/friday_c11_node_unknown_replay.json").read_bytes())
    stop_at = fixture["global_stop"]["epoch"] - fixture["t0_epoch"]
    settings = watch.unknown_window_settings({})
    first: dict[str, float] = {}
    healthy_peak = 0.0
    batches = fixture["batches"]
    for at, *_ in batches:
        window = [b for b in batches if at - 1800 < b[0] <= at]
        closes = sum(b[2] + b[3] for b in window)
        _, level = watch.unknown_window_level(sum(b[3] for b in window), closes, settings)
        if level:
            first.setdefault(level, at)
            if level == "stop":
                first.setdefault("attention", at)
        healthy = [b for b in window if b[1] not in {"smp-9-20", "smp-9-7"}]
        fraction, _ = watch.unknown_window_level(sum(b[3] for b in healthy),
                                                 sum(b[2] + b[3] for b in healthy), settings)
        healthy_peak = max(healthy_peak, fraction)
    assert first["attention"] < first["stop"] < stop_at
    assert stop_at - first["attention"] >= 20 * 60
    assert healthy_peak < .03


def test_unknown_close_keeps_reason_and_last_receipt_in_watch_state(owner):
    attempt(owner)
    failure = {"execution_reason": "runtime_execution_or_release_unknown",
               "last_unknown_receipt": {"reason_code": "managed_execution_unknown"}}
    event(owner, 'actor_games_closed', actor='actor', games=[
        {'intent_id': 'u', 'seed': 's1', 'status': 'unknown', 'failure': failure,
         'reason': 'runtime_execution_or_release_unknown',
         'world_mutation_offered': True, 'training_generation': 'g-one'},
        {'intent_id': 'n', 'seed': 's2', 'status': 'finished', 'reason': 'natural_terminal',
         'world_mutation_offered': True, 'training_generation': 'g-one'}])
    owner.collect_events()
    worlds = owner.state['worlds']
    assert worlds['u']['failure'] == failure
    assert worlds['u']['reason'] == 'runtime_execution_or_release_unknown'
    assert 'failure' not in worlds['n'] and 'reason' not in worlds['n']
    assert owner.state['unknown_intents'] == ['u']


def test_step_accounting_and_signal_never_select_holder(watch, owner, monkeypatch):
    commands = []
    monkeypatch.setattr(watch.subprocess, 'check_output', lambda *a, **kw:
        '79|RUNNING|0:0|s|e|s|e|99\n79.batch|RUNNING|0:0|s|e|s|e|99\n'
        '79.17|COMPLETED|0:0|s|e|s|e|20\n')
    account = watch.job_accounting('79.17')
    assert account['terminal'] and account['all_completed']
    assert [r['job_id'] for r in account['rows']] == ['79.17']
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(watch.subprocess, 'run', run)
    owner.signal_once('79.17')
    assert commands == [['scancel', '--signal=USR1', '79.17']]


def test_learner_only_restore_preserves_unknown_denominator_and_charged_time(owner):
    value = attempt(owner)
    event(owner, 'heartbeat', training_generation='g-one', active_seconds=100, updates=370)
    event(owner, 'actor_world_offered', actor='actor', intent_id='old',
          training_generation='g-one')
    event(owner, 'actor_games_closed', actor='actor', games=[{
        'intent_id': 'old', 'seed': 'seed', 'status': 'unknown',
        'world_mutation_offered': True, 'training_generation': 'g-one'}])
    event(owner, 'learner_replacement_started', previous_generation='g-one',
          training_generation='g-two', charged_seconds=110)
    event(owner, 'heartbeat', training_generation='g-two', active_seconds=20, updates=372)
    event(owner, 'training_window', training_generation='g-one', active_seconds=105, updates=370)
    owner.collect_events()
    assert value['generations'] == ['g-one', 'g-two']
    assert value['active_seconds'] == 20 and not value.get('window_closed')
    assert 130 <= owner.state['training_seconds'] < 140
    assert owner.state['health']['completed_for_stop'] == 1
    assert owner.state['health']['known_unknown'] == 1


@pytest.mark.parametrize('error', [ConnectionRefusedError(111, 'Connection refused'),
                                   EOFError(), ConnectionResetError(), BrokenPipeError()])
def test_stop_request_to_closed_learner_socket_is_recorded_not_a_watcher_crash(
        owner, monkeypatch, error):
    """c13d 11-08: the learner closed its socket while draining; the watcher's repeated
    stop request got ConnectionRefusedError and the watcher died without its final drain."""
    from stpd.p1.export import adapter_identity
    from stpd.p1.local_inference import SEGMENT_SCHEMA, SegmentConnection

    current = attempt(owner)
    current['actor_plans'] = []
    owner.signal_once = lambda _job: None
    owner.config['dataplane'] = {}
    monkeypatch.setattr(owner, '_socket_attachment', lambda _attempt: {
        'run': owner.config['run'], 'training_generation': 'g-one', 'pool_id': 'p-one',
        'schema': SEGMENT_SCHEMA, 'adapter': adapter_identity(),
    })
    def refused(*args, **kwargs):
        raise error
    monkeypatch.setattr(SegmentConnection, 'request', refused)
    alarms = []
    owner.telemetry = lambda kind, **fields: alarms.append((kind, fields))
    owner.stop_attempt(current, 'evaluation_CP_preparation_failed')
    owner.stop_attempt(current, 'evaluation_CP_preparation_failed')
    assert current['stopping'] == 'evaluation_CP_preparation_failed'
    name = type(error).__name__
    assert current['stop_channel_unavailable']['error_type'] == name
    assert current['stop_channel_unavailable']['epoch'] == 1 and current['stop_epoch'] == 2
    assert alarms == [('attention', {'reason': 'stop_channel_unavailable',
                                     'error_type': name})]


@pytest.mark.parametrize('corrupt', [False, True])
def test_final_wal_snapshot_closes_event_cursor_without_reconnecting(
        owner, monkeypatch, corrupt):
    import json

    from spireagent.rl_local_wal import LocalControlWAL
    from stpd.p1.export import adapter_identity
    from stpd.p1.local_inference import SEGMENT_SCHEMA, SegmentConnection

    current = attempt(owner)
    owner.config['dataplane'] = {}
    monkeypatch.setattr(owner, '_socket_attachment', lambda _attempt: {
        'run': owner.config['run'], 'training_generation': 'g-one', 'pool_id': 'p-one',
        'schema': SEGMENT_SCHEMA, 'adapter': adapter_identity(),
    })
    def no_network(*args, **kwargs):
        raise AssertionError('closed listener must not be reconnected')
    monkeypatch.setattr(SegmentConnection, 'request', no_network)
    journal = LocalControlWAL(owner.work / 'terminal.sqlite',
                              run=owner.config['run'], generation='g-one')
    try:
        for kind in ('training_window', 'finished'):
            record = event(owner, kind, training_generation='g-one', active_seconds=5, updates=1)
            journal.append('event:' + record['record_sha256'], record, {}, kind='event')
        facts = journal.events_since(0)
    finally:
        journal.close()
    if corrupt:
        facts[-1]['hash'] = '0' * 64
    path = owner.work / 'attempt/learner/control-wal-final.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'run': owner.config['run'],
        'training_generation': 'g-one', 'events': facts}))
    owner.collect_events()
    if corrupt:
        assert owner.state['stop'] == 'dataplane_events_unavailable'
        assert not current.get('finished')
        assert not current.get('socket_events_closed')
    else:
        assert current['finished'] and current['window_closed']
        assert current['socket_events_closed']
        assert current['event_sequence'] == facts[-1]['sequence']
        assert owner.state['stop'] is None
        assert len(owner.seen_events) == 2
        owner.collect_events()
        assert len(owner.seen_events) == 2


def test_inconclusive_audit_window_pauses_only_new_admission(owner):
    attempt(owner)
    for n in range(4):
        event(owner,'sampling_audit',run_id=f'i{n}',status='audit_inconclusive')
    for n in range(20):
        event(owner,'sampling_audit',run_id=f'p{n}',status='pass')
    event(owner,'sampling_audit',run_id='i4',status='audit_inconclusive')
    owner.collect_events()
    assert owner.state['health']['sampling_audit']['window_fraction']==.2
    assert not owner.state.get('audit_admission_paused')
    event(owner,'sampling_audit',run_id='i5',status='audit_inconclusive')
    owner.collect_events()
    assert owner.state['health']['sampling_audit']['inconclusive']==6
    assert owner.exchange.admission_pause(owner.config['budget_id']) is not None
    assert owner.exchange.admission_pause('another-budget') is None
    assert owner.state['stop'] is None
    assert owner.exchange.stop_request(training_generation='g-one') is None


def test_held_step_stop_keeps_worker_processes_alive_and_uses_existing_markers(
        owner, watch, monkeypatch):
    from pathlib import Path

    value = attempt(owner)
    value.update(job_id="1424613.98", actor_plans=[])
    root = Path(value["root"])
    (root / "learner").mkdir(parents=True)
    signals = []
    owner.signal_once = signals.append
    owner.stop_attempt(value, "normal-stop")
    assert signals == []
    assert owner.exchange.stop_request(training_generation="g-one")["reason"] == "normal-stop"
    assert (root / "login-launch/STOP").is_file()
    assert (root / "learner/STOP").is_file()
    assert value["stopping"] == "normal-stop"
    # The typed reason and budget travel in learner/STOP itself, and the first one stays.
    written = json.loads((root / "learner/STOP").read_bytes())
    assert written == {"reason": "normal-stop", "budget_id": owner.config.get("budget_id")}
    owner.stop_attempt(value, "absolute_deadline")
    assert json.loads((root / "learner/STOP").read_bytes())["reason"] == "normal-stop"
    cancellations = []
    monkeypatch.setattr(watch.subprocess, "run", lambda command, **kwargs:
        (cancellations.append(command) or SimpleNamespace(returncode=0)))
    owner.cancel_at_deadline()
    assert cancellations == [["scancel", "1424613.98"]]


def test_terminal_learner_stop_preserves_actor_drain_without_reconnecting(owner, monkeypatch):
    from pathlib import Path

    value = attempt(owner)
    value.update(job_id='1427035.12', actor_plans=[])
    root = Path(value['root'])
    (root / 'learner').mkdir(parents=True)
    owner.config['dataplane'] = {}
    def no_dead_socket(_attempt):
        raise AssertionError('Slurm-terminal learner must not receive another RPC Stop')
    monkeypatch.setattr(owner, '_socket_attachment', no_dead_socket)
    owner.stop_attempt(value, 'learner_allocation_terminal', learner_terminal=True)
    assert (root / 'login-launch/STOP').is_file()
    assert (root / 'learner/STOP').is_file()
    assert value['stopping'] == 'learner_allocation_terminal'


@pytest.mark.parametrize('reference', ['initial_checkpoint', 'previous'])
def test_recipe_rollback_compares_two_post_switch_points_to_the_initial_CP(
        owner, watch, tmp_path, monkeypatch, reference):
    """A partial rebound can still be below the switch point twice (CARD §5c)."""
    import json

    from stpd.evaluation import rl_observation

    protocol = {"host_pin": {"source": "fixed"}, "runtime_pin": {"source": "fixed"},
                "input_spec": "fixed", "initial_progress_sha256": "fixed",
                "behavior_policy": {"mode": "argmax"}}

    def cohort(floor):
        return {"host_kind": "managed", "character": "Defect", "ascension": 0,
                "planned": 2, "protocol_observed": protocol,
                "rows": [{"seed": seed, "floor": floor, "scored": True, "status": "finished",
                          "result": "loss"} for seed in ("seed-a", "seed-b")]}

    recipe = {"performance": {"microbatch_size": 640},
              "segments": {"minibatches": 2, "auxiliary_rows": 128}}
    owner.config['evaluation'] = {
        'baseline_binding': {'id': 'baseline'}, 'baseline_batches': [],
        'baseline_checkpoint': 'baseline', 'manifest': {'sha256': 'fixed'},
        'initial_checkpoint': 'anchor', 'baseline_resume_checkpoint': 'anchor',
        'rollback_reference': reference, 'rollback_recipe': recipe,
    }
    monkeypatch.setattr(watch, 'job_accounting', lambda _: {'terminal': True})
    monkeypatch.setattr(watch.evaluate, 'fixed_baseline',
                        lambda *a, **kw: {'offers_accounted': True, 'cohort': cohort(20)})
    monkeypatch.setattr(watch.evaluate, 'read_pinned', lambda ref: ref)
    monkeypatch.setattr(rl_observation, 'write_report', lambda *a, **kw: None)
    for index, (due, checkpoint, floor) in enumerate(
            ((0, 'anchor', 20), (7200, 'lower', 17), (14400, 'partial-rebound', 18))):
        root = tmp_path / f'eval-{index}'
        root.mkdir()
        (root / 'result.json').write_text('{}')
        (root / 'snapshot.json').write_text('{}')
        path = root / 'plan.json'
        plan = {'checkpoint_binding': {'checkpoint_artifact_id': checkpoint},
                'manifest': {'sha256': 'fixed'}}
        result = {'ledger_report': {'slots': [{'status': 'finished'}] * 2}}
        owner.evaluation_cohort = (
            lambda *a, plan=plan, result=result, floor=floor: (plan, result, cohort(floor), True))
        owner.state['pending'] = {'root': str(root), 'plan': str(path), 'stage': 'running',
                                  'job_id': '80', 'due_seconds': due}
        owner.collect_segment_evaluation()
        if due < 14400:
            assert owner.state['stop'] is None
    assert owner.state['evaluations'][0]['paired_regression'] is False
    assert owner.state['evaluations'][-1]['against_previous']['mean_delta'] == 1
    assert owner.state['evaluations'][-1]['reward']['planned'] == 2
    assert 'mean_difference_bound' in owner.state['evaluations'][-1]['reward_against_previous']
    if reference == 'previous':
        assert owner.state['stop'] is None
        assert owner.state['evaluations'][-1]['paired_regression'] is False
        assert not (owner.work / 'rollback-checkpoint.json').exists()
        return
    assert all(row['paired_regression'] for row in owner.state['evaluations'][1:])
    assert owner.state['stop'] == 'two_paired_regressions_restore_best_after_diagnosis'
    rollback = json.loads((owner.work / 'rollback-checkpoint.json').read_bytes())
    assert rollback['checkpoint_artifact_id'] == 'anchor'
    assert rollback['recipe_after_diagnosis'] == recipe
    assert rollback['automatic_training_resume'] is False


def _protocol(host):
    return {"host_pin": {"version": host}, "runtime_pin": {"version": "0.1.0-rc.64"},
            "input_spec": "fixed", "initial_progress_sha256": "fixed",
            "behavior_policy": {"mode": "argmax"}}


def _cohort(host, floors, character="Defect"):
    return {"host_kind": "managed", "character": character, "ascension": 0,
            "planned": 2, "protocol_observed": _protocol(host),
            "rows": [{"seed": seed, "floor": floor, "scored": True, "status": "finished",
                      "result": "loss"}
                     for seed, floor in zip(("seed-a", "seed-b"), floors, strict=True)]}


def _run_evaluations(owner, watch, tmp_path, monkeypatch, steps, *, reference='previous'):
    """steps: (due_seconds, checkpoint, Host version, floors); baseline is Host rc.63."""
    from stpd.evaluation import rl_observation

    owner.config['evaluation'] = {
        'baseline_binding': {'id': 'baseline'}, 'baseline_batches': [],
        'baseline_checkpoint': 'baseline', 'manifest': {'sha256': 'fixed'},
        'initial_checkpoint': steps[0][1], 'baseline_resume_checkpoint': 'window-1-start',
        'rollback_reference': reference,
    }
    monkeypatch.setattr(watch, 'job_accounting', lambda _: {'terminal': True})
    monkeypatch.setattr(watch.evaluate, 'fixed_baseline', lambda *a, **kw: {
        'offers_accounted': True, 'cohort': _cohort('1.1.0-rc.63', (20, 20))})
    monkeypatch.setattr(watch.evaluate, 'read_pinned', lambda ref: ref)
    reports, alarms = [], []
    monkeypatch.setattr(rl_observation, 'write_report',
                        lambda cohorts, *a, **kw: reports.append(sorted(cohorts)))
    monkeypatch.setattr(owner, 'telemetry', lambda kind, **f: alarms.append((kind, f)))
    for index, (due, checkpoint, host, floors, *character) in enumerate(steps):
        root = tmp_path / f'eval-{index}'
        root.mkdir(parents=True)
        (root / 'result.json').write_text('{}')
        (root / 'snapshot.json').write_text('{}')
        plan = {'checkpoint_binding': {'checkpoint_artifact_id': checkpoint},
                'manifest': {'sha256': 'fixed'}}
        result = {'ledger_report': {'slots': [{'status': 'finished'}] * 2}}
        group = _cohort(host, floors, *character)
        owner.evaluation_cohort = lambda *a, plan=plan, result=result, group=group: (
            plan, result, group, True)
        owner.state['pending'] = {'root': str(root), 'plan': str(root / 'plan.json'),
                                  'stage': 'running', 'job_id': '80', 'due_seconds': due}
        owner.collect_segment_evaluation()
    return reports, alarms


@pytest.mark.parametrize('reference', ['previous', 'initial_checkpoint'])
def test_new_host_pin_evaluation_is_protocol_skipped_not_a_watcher_crash(
        owner, watch, tmp_path, monkeypatch, reference):
    """R14: window-2 eval-0001 (Host rc.64) vs the frozen rc.63 baseline crashed the watcher
    with different_observed_protocol and stopped training."""
    reports, alarms = _run_evaluations(owner, watch, tmp_path, monkeypatch, [
        (0, 'cp-6787', '1.1.0-rc.64', (24, 30)),
        (7200, 'cp-lower', '1.1.0-rc.64', (20, 26)),
        (14400, 'cp-higher', '1.1.0-rc.64', (30, 34))], reference=reference)
    first, second, third = owner.state['evaluations']
    assert owner.state['stop'] is None
    skip = {'skipped': 'different_observed_protocol', 'differing_keys': ['host_pin'],
            'reference': 'baseline', 'values': {'host_pin': {
                'reference': {'version': '1.1.0-rc.63'}, 'current': {'version': '1.1.0-rc.64'}}}}
    assert first['against_baseline'] == first['reward_against_baseline'] == skip
    assert first['against_previous'] == skip and first['previous_reference'] == 'baseline'
    # Not compared is not "no regression"; the anchor (initial_checkpoint) is not a comparison.
    assert first['paired_regression'] is (None if reference == 'previous' else False)
    assert first['protocol_skipped'] == {'baseline': ['host_pin'], 'previous': ['host_pin'],
                                         'best': ['host_pin']}
    assert first['reward']['planned'] == 2 and 'lower_bound' in first['reward']
    assert first['best_selection']['reason'] == 'first_cohort_of_new_observed_protocol'
    assert owner.state['superseded_best'][0]['best_cohort_source'] == 'frozen_baseline'
    assert 'skipped' not in first  # A closed cohort: the new protocol's reference.
    # Later rc.64 cohorts compare with the most recent rc.64 cohort, never the rc.63 baseline.
    assert second['previous_reference'] == first['root']
    assert second['against_previous']['mean_delta'] == -4
    assert second['against_baseline']['skipped'] == 'different_observed_protocol'
    assert 'mean_difference_bound' in second['reward_against_previous']
    assert third['against_previous']['mean_delta'] == 9
    assert owner.state['best_checkpoint'] == 'cp-higher'
    assert len(owner.state['superseded_best']) == 1
    assert reports == [['current'], ['current', 'previous'], ['current', 'previous']]
    assert [f['reason'] for kind, f in alarms if kind == 'attention'] == [
        'different_observed_protocol'] * 3
    if reference == 'initial_checkpoint':
        assert third['against_recipe_switch']['mean_delta'] == 5


def test_protocol_skip_prefers_latest_same_protocol_reference(
        owner, watch, tmp_path, monkeypatch):
    _run_evaluations(owner, watch, tmp_path, monkeypatch, [
        (0, 'cp-a', '1.1.0-rc.63', (20, 20)),
        (7200, 'cp-b', '1.1.0-rc.64', (21, 21)),
        (14400, 'cp-c', '1.1.0-rc.63', (23, 25))])
    first, second, third = owner.state['evaluations']
    assert 'protocol_skipped' not in first and first['against_baseline']['mean_delta'] == 0
    assert second['against_previous']['skipped'] == 'different_observed_protocol'
    assert second['against_previous']['reference'] == first['root']
    assert third['previous_reference'] == first['root']
    assert third['against_previous']['mean_delta'] == 4
    assert third['against_baseline']['mean_delta'] == 4
    assert third['protocol_skipped'] == {'latest_closed': ['host_pin'], 'best': ['host_pin']}
    assert third['best_selection']['reason'] == 'restored_best_of_returning_observed_protocol'
    assert owner.state['best_checkpoint'] == 'cp-c'  # +4 over the restored rc.63 reference
    assert owner.state['stop'] is None


def test_returning_protocol_keeps_its_better_earlier_best(owner, watch, tmp_path, monkeypatch):
    _run_evaluations(owner, watch, tmp_path, monkeypatch, [
        (0, 'cp-a', '1.1.0-rc.63', (30, 30)),
        (7200, 'cp-b', '1.1.0-rc.64', (21, 21)),
        (14400, 'cp-c', '1.1.0-rc.63', (21, 21))])
    assert owner.state['evaluations'][-1]['best_selection']['restored_checkpoint'] == 'cp-a'
    assert owner.state['best_checkpoint'] == 'cp-a'
    assert [row['best_checkpoint'] for row in owner.state['superseded_best']] == ['cp-a', 'cp-b']


def test_protocol_skipped_regression_reference_never_reads_as_no_regression(
        owner, watch, tmp_path, monkeypatch):
    _run_evaluations(owner, watch, tmp_path, monkeypatch, [
        (0, 'anchor', '1.1.0-rc.63', (30, 30)),
        (7200, 'cp-b', '1.1.0-rc.64', (20, 20)),
        (14400, 'cp-c', '1.1.0-rc.64', (10, 10))], reference='initial_checkpoint')
    later = owner.state['evaluations'][1:]
    assert [row['paired_regression'] for row in later] == [None, None]
    assert all(row['paired_regression_skipped'] == 'different_observed_protocol' for row in later)
    assert all(row['protocol_skipped']['recipe_switch'] == ['host_pin'] for row in later)
    assert later[1]['against_previous']['mean_delta'] == -10  # same-protocol delta kept
    assert owner.state['stop'] is None


def test_resumed_window_with_old_protocol_evaluations_is_the_c13_incident_shape(
        owner, watch, tmp_path, monkeypatch):
    """A resumed state carrying rc.63 evaluations and an rc.63 best, then an rc.64 cohort."""
    old = _cohort('1.1.0-rc.63', (25, 25))
    owner.state['evaluations'].append({'root': 'window-1/evaluation-0007', 'cohort': old,
                                       'at_training_seconds': 50400, 'paired_regression': True,
                                       'regression_reference': 'previous'})
    owner.state.update(best_checkpoint='w1-best', best_cohort=old)
    _run_evaluations(owner, watch, tmp_path, monkeypatch, [
        (57600, 'cp-6787', '1.1.0-rc.64', (24, 30)),
        (64800, 'cp-next', '1.1.0-rc.64', (10, 10))])
    first, second = owner.state['evaluations'][1:]
    assert first['against_previous']['reference'] == 'window-1/evaluation-0007'
    assert first['paired_regression'] is None and second['paired_regression'] is True
    assert owner.state['superseded_best'][0]['best_checkpoint'] == 'w1-best'
    assert owner.state['superseded_best'][0]['best_cohort_source'] == 'evaluation'
    assert owner.state['best_checkpoint'] == 'cp-6787'
    assert owner.state['stop'] is None  # rc.63's regression never chains into rc.64's


def test_report_failure_leaves_best_and_superseded_state_untouched(
        owner, watch, tmp_path, monkeypatch):
    """Re-review R2: write_report runs before any state change."""
    from stpd.evaluation import rl_observation

    _run_evaluations(owner, watch, tmp_path / 'one', monkeypatch, [
        (0, 'cp-a', '1.1.0-rc.63', (30, 30))])
    before = {k: owner.state.get(k) for k in ('best_checkpoint', 'best_cohort', 'superseded_best')}

    def failing(*a, **kw):
        raise OSError('disk')
    original = watch.SegmentWatcher.collect_segment_evaluation
    def collect(self):
        monkeypatch.setattr(rl_observation, 'write_report', failing)
        return original(self)
    monkeypatch.setattr(watch.SegmentWatcher, 'collect_segment_evaluation', collect)
    with pytest.raises(OSError):
        _run_evaluations(owner, watch, tmp_path / 'two', monkeypatch, [
            (0, 'cp-b', '1.1.0-rc.64', (10, 10))])
    assert {k: owner.state.get(k) for k in before} == before
    assert owner.state['pending'] is not None and len(owner.state['evaluations']) == 1


def test_superseded_best_without_cohort_is_never_restored(owner, watch, tmp_path, monkeypatch):
    """Re-review R1: an entry written without its cohort is skipped, not a KeyError."""
    owner.state['superseded_best'] = [{'best_checkpoint': 'old', 'differing_keys': ['host_pin']}]
    _run_evaluations(owner, watch, tmp_path, monkeypatch, [
        (0, 'cp-a', '1.1.0-rc.64', (20, 20))])
    assert owner.state['best_checkpoint'] == 'cp-a'
    assert owner.state['superseded_best'][0] == {'best_checkpoint': 'old',
                                                 'differing_keys': ['host_pin']}


@pytest.mark.parametrize('host', ['1.1.0-rc.63', '1.1.0-rc.64'])
def test_host_character_or_ascension_change_is_still_refused(
        owner, watch, tmp_path, monkeypatch, host):
    """Only the observed-protocol pin check is skipped; a different character still raises,
    also when the Host changes at the same time (no reference then reaches compare)."""
    with pytest.raises(ValueError, match='different_host_character_or_ascension'):
        _run_evaluations(owner, watch, tmp_path, monkeypatch, [
            (0, 'cp-a', host, (20, 20), 'Ironclad')])
    assert owner.state['evaluations'] == [] and owner.state.get('best_checkpoint') is None


@pytest.mark.parametrize('status, closed', [('save_failed', True), ('save_pending', False)])
def test_save_failed_after_retry_is_one_excluded_pair_in_the_segment_evaluation(
        owner, watch, tmp_path, monkeypatch, status, closed):
    (tmp_path / 'snapshot.json').write_text('{"full_readback": true}')
    slots = ([{'status': 'finished', 'cleanup_accepted': True}] * 49
             + [{'status': status, 'cleanup_accepted': True}])
    result = {'ledger_report': {'slots': slots, 'score_admitted': True}}
    monkeypatch.setattr(watch.evaluate, 'read_observation_report',
                        lambda *a: ({}, result, {'cohort': {'rows': []}}))
    assert owner.evaluation_cohort(tmp_path / 'plan.json', tmp_path / 'result.json')[3] is closed


def test_closed_training_window_stops_replacement_while_learner_still_drains(
        owner, watch, monkeypatch):
    value = attempt(owner)
    event(owner, "training_window", training_generation="g-one", active_seconds=10, updates=363)
    owner.collect_segment_evaluation = lambda: None
    owner.schedule_segment_evaluation = lambda: None
    owner.retain_segment_checkpoints = lambda: None
    def unexpected(_):
        raise AssertionError("draining learner must not receive new actors")
    owner.offer_initial_actors = owner.replace_actors = unexpected
    stops = []
    owner.stop_attempt = lambda _, reason: stops.append(reason)
    monkeypatch.setattr(watch, "job_accounting", lambda _: {"terminal": False})
    owner.tick_segment()
    assert value["window_closed"] and stops == ["learner_draining"]


def test_restart_resumes_newest_archived_checkpoint_across_attempts(
        owner, watch, monkeypatch, tmp_path):
    # Segment CP records live only in the archive; sequences restart per attempt.
    archive = {"kind": "local", "directory": str(tmp_path / "archive")}
    owner.config.update(learner_config=str(tmp_path / "learner.json"))
    owner.config["peer"] = {**owner.config["peer"], "archive": archive}
    records = {"local": [], "archive": [
        {"checkpoint_artifact_id": "old-attempt-seq40", "checkpoint_sequence": 40,
         "published_at": 100.0},
        {"checkpoint_artifact_id": "new-attempt-seq39", "checkpoint_sequence": 39,
         "published_at": 200.0},
    ]}
    monkeypatch.setattr(watch, "Exchange", lambda peer, run: SimpleNamespace(
        checkpoint_records=lambda: records["archive"] if peer == archive else records["local"]))
    owner.exchange = SimpleNamespace(checkpoint_records=lambda: records["local"])
    from stpd.rl import learner
    monkeypatch.setattr(learner.LearnerConfig, "read",
                        staticmethod(lambda path: SimpleNamespace(resume_checkpoint="initial")))
    tried = []
    owner.cold_checkpoint = lambda identity: tried.append(identity) or {"id": identity}
    assert owner.select_checkpoint() == {"id": "new-attempt-seq39"}
    assert tried == ["new-attempt-seq39"]


def test_one_actor_batch_of_cut_games_is_measured_against_offered_worlds(watch):
    # 43 min in: no closes reported yet, then one actor reports 7 cut games of 10.
    health = {"completed_for_stop": 10, "offered": 1500, "known_unknown": 7, "lost_offers": 0}
    config = {"unknown_stop_fraction": 0.05}
    assert not watch.unknown_over_limit(7, watch.stop_denominator(health), config)
    assert watch.unknown_over_limit(80, watch.stop_denominator(health), config)


# ---- Card U4 A/B/D: typed unknowns, both headline scores, compatible fixes ----

def _u4_rows(floors, statuses):
    return [{"seed": f"s{i:03d}", "run_id": f"run-{i}", "status": status,
             "scored": status == "finished", "result": "loss" if status == "finished" else None,
             "floor": floor if status == "finished" else None,
             "observed_floor_lower_bound": floor, "boss_cleared_acts": []}
            for i, (floor, status) in enumerate(zip(floors, statuses, strict=True))]


def _u4_cohort(host, floors, statuses):
    return {"host_kind": "managed", "character": "Defect", "ascension": 0,
            "planned": len(floors), "protocol_observed": _protocol(host),
            "rows": _u4_rows(floors, statuses)}


def _u4_attempt(tmp_path, seed, **row):
    """A retained attempt original: report + Runtime final status, as evaluate keeps it."""
    import json

    root = tmp_path / "attempts" / seed
    (root / "runs" / row["run_id"]).mkdir(parents=True)
    (root / "runtime-final-status.json").write_text(json.dumps(
        {"last_result": None, "taint_reason": None}))
    (root / "runs" / row["run_id"] / "events.jsonl").write_text("")
    path = root / "attempt-report.json"
    path.write_text(json.dumps({"episodes": [{
        "runtime_final_status": {"path": "runtime-final-status.json"}, **row}]}))
    import hashlib
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _u4_evaluate(owner, watch, tmp_path, monkeypatch, steps, **evaluation):
    """steps: (due, checkpoint, host, floors, statuses, slot extras by index)."""
    from stpd.evaluation import rl_observation

    n = len(steps[0][3])
    owner.config['evaluation'] = {
        'baseline_binding': {'id': 'baseline'}, 'baseline_batches': [],
        'baseline_checkpoint': 'baseline', 'manifest': {'sha256': 'fixed'},
        'baseline_resume_checkpoint': 'window-1-start', **evaluation}
    monkeypatch.setattr(watch, 'job_accounting', lambda _: {'terminal': True})
    monkeypatch.setattr(watch, 'job_node', lambda _: 'smp-9-1')
    monkeypatch.setattr(watch.evaluate, 'fixed_baseline', lambda *a, **kw: {
        'offers_accounted': True,
        'cohort': _u4_cohort('1.1.0-rc.64', [20] * n, ['finished'] * n)})
    monkeypatch.setattr(watch.evaluate, 'read_pinned', lambda ref: ref)
    monkeypatch.setattr(rl_observation, 'write_report', lambda *a, **kw: None)
    alarms = []
    monkeypatch.setattr(owner, 'telemetry', lambda kind, **f: alarms.append((kind, f)))
    for index, (due, checkpoint, host, floors, statuses, extras) in enumerate(steps):
        root = tmp_path / f'eval-{index}'
        root.mkdir(parents=True)
        (root / 'result.json').write_text('{}')
        (root / 'snapshot.json').write_text('{}')
        plan = {'checkpoint_binding': {'checkpoint_artifact_id': checkpoint},
                'manifest': {'sha256': 'fixed'}}
        slots = [{'seed': f's{i:03d}', 'run_id': f'run-{i}', 'status': status,
                  'cleanup_accepted': status != 'not_started',
                  'reason': None if status == 'finished' else
                  'runtime_execution_or_release_unknown',
                  'public_max_observed_floor': floor, **extras.get(i, {})}
                 for i, (floor, status) in enumerate(zip(floors, statuses, strict=True))]
        result = {'ledger_report': {'slots': slots, 'score_admitted': True}}
        group = _u4_cohort(host, floors, statuses)
        owner.evaluation_cohort = lambda *a, plan=plan, result=result, group=group: (
            plan, result, group, not watch.evaluation_incomplete(result))
        owner.state['pending'] = {'root': str(root), 'plan': str(root / 'plan.json'),
                                  'stage': 'running', 'job_id': '80', 'due_seconds': due}
        owner.collect_segment_evaluation()
    return alarms


def test_c13_eval_0001_shape_wall_limited_deep_games_are_scored_conservatively(
        owner, watch, tmp_path, monkeypatch):
    """Window-2 eval-0001: 45 finished + 5 unknown at floors 44-47, all stopped by the
    900 s per-game wall limit. Typed wall_limit, both scores reported, cohort closed."""
    floors = [20] * 45 + [44, 45, 46, 47, 47]
    statuses = ['finished'] * 45 + ['unknown'] * 5
    extras = {i: {'report': _u4_attempt(tmp_path, f's{i:03d}', run_id=f'run-{i}',
                                        wall_limit_reached=True,
                                        execution_reason='runtime_execution_or_release_unknown',
                                        game_metrics={'max_observed_floor': floors[i]})}
              for i in range(45, 50)}
    alarms = _u4_evaluate(owner, watch, tmp_path, monkeypatch,
                          [(0, 'cp-a', '1.1.0-rc.64', floors, statuses, extras)])
    record, = owner.state['evaluations']
    assert record['closed'] is True and 'skipped' not in record
    assert record['unknown_by_category'] == {'wall_limit': 5}
    assert {d['facts_source'] for d in record['unknown_details']} == {'attempt_original'}
    assert record['unknown_details'][0]['detail']['node'] == 'smp-9-1'
    assert record['unknown_details'][0]['detail']['floor'] == 44
    scores = record['scores']
    assert scores['finished_n'] == 45 and scores['conservative_n'] == 50
    assert scores['conservative'] > scores['finished_mean']  # deep games no longer dropped
    assert scores['conservative'] == pytest.approx(record['reward']['lower_bound']['L'])
    assert scores['selection_score'] == 'conservative' and scores['complete'] is True
    assert record['best_comparison']['score'] == 'conservative'
    assert record['regression_score'] == 'conservative'
    assert owner.state['best_selection_score'] == 'conservative'
    assert ('evaluation_scores', {'evaluation': record['root'], **scores}) in alarms


def test_many_unknowns_no_longer_void_an_evaluation(owner, watch, tmp_path, monkeypatch):
    """Window 1 eval-0008: 9 unknowns of 50 were skipped as incomplete; now reported."""
    floors = [30] * 41 + [12] * 9
    statuses = ['finished'] * 41 + ['unknown'] * 9
    _u4_evaluate(owner, watch, tmp_path, monkeypatch,
                 [(0, 'cp-a', '1.1.0-rc.64', floors, statuses, {})])
    record, = owner.state['evaluations']
    assert record['closed'] is True and 'skipped' not in record
    # No attempt original and no classifying code: unclassified, with the reason.
    assert record['unknown_by_category'] == {'unclassified': 9}
    assert record['unknown_details'][0]['rule'] == 'no_classifying_fact'
    assert record['unknown_details'][0]['facts_source'] == 'slot_only:no_attempt_report'


def test_missing_originals_label_incomplete_but_still_report_scores(
        owner, watch, tmp_path, monkeypatch):
    """eval-0007 shape: the batch stopped, 11 games never played. Labelled, scored, and
    not used as a comparison reference; an infrastructure unknown leaves the conservative
    score."""
    floors = [29] * 38 + [5] + [0] * 11
    statuses = ['finished'] * 38 + ['unknown'] + ['not_started'] * 11
    extras = {38: {'reason': 'runtime_exited_before_startup'}}
    alarms = _u4_evaluate(owner, watch, tmp_path, monkeypatch,
                          [(0, 'cp-a', '1.1.0-rc.64', floors, statuses, extras)])
    record, = owner.state['evaluations']
    assert record['closed'] is False
    assert record['skipped'] == 'evaluation_audit_or_originals_incomplete'
    assert record['incomplete'] == [{'reason': 'originals_missing',
                                     'statuses': {'not_started': 11}}]
    assert record['scores']['incomplete'] == ['originals_missing']
    assert record['unknown_by_category'] == {'infrastructure': 1}
    assert record['scores']['conservative_n'] == 38
    assert record['scores']['conservative'] == pytest.approx(record['scores']['finished_mean'])
    assert owner.state.get('best_checkpoint') is None  # never a decision reference
    assert [f['reason'] for kind, f in alarms if kind == 'attention'] == [
        'evaluation_audit_or_originals_incomplete']


def test_audit_mismatch_is_the_other_incomplete_reason(watch):
    result = {'ledger_report': {'score_admitted': False, 'slots': [
        {'seed': 'a', 'status': 'finished', 'cleanup_accepted': True,
         'execution_reason': 'typed_original_verification_failed'}]}}
    assert watch.evaluation_incomplete(result) == [{
        'reason': 'audit_mismatch', 'score_admitted': False,
        'typed_verification_failed_on_scored_status': ['a']}]
    clean = {'ledger_report': {'score_admitted': True, 'slots': [
        {'seed': s, 'status': s, 'cleanup_accepted': True}
        for s in ('finished', 'unknown', 'save_failed', 'censored')]}}
    assert watch.evaluation_incomplete(clean) == []


def test_training_closes_are_typed_and_bad_node_closes_count_as_infrastructure(
        owner, watch):
    attempt(owner)
    owner.config['node_unknown_exclusion'] = {}
    games = [
        {'intent_id': 'gap', 'seed': 's1', 'status': 'unknown',
         'reason': 'runtime_execution_or_release_unknown', 'world_mutation_offered': True,
         'failure': {'last_unknown_receipt': {'reason_code': 'managed_execution_unknown',
                                              'label': 'Play Tesla Coil -> Soul Nexus'}}},
        {'intent_id': 'timeout', 'seed': 's2', 'status': 'unknown',
         'reason': 'runtime_execution_or_release_unknown', 'world_mutation_offered': True,
         'failure': {'handoff_reason': 'agent_call_timeout'}},
        {'intent_id': 'save', 'seed': 's3', 'status': 'save_failed',
         'reason': 'segment_delivery_incomplete', 'world_mutation_offered': True},
        {'intent_id': 'bare', 'seed': 's4', 'status': 'unknown',
         'reason': 'runtime_execution_or_release_unknown', 'world_mutation_offered': True}]
    event(owner, 'actor_games_closed', actor='bad-actor', games=games[3:])
    event(owner, 'actor_games_closed', actor='actor', games=games[:3])
    owner.collect_events()
    worlds = owner.state['worlds']
    assert worlds['gap']['unknown_class']['category'] == 'environment_gap'
    assert worlds['timeout']['unknown_class']['category'] == 'infrastructure'
    assert worlds['save']['unknown_class']['category'] == 'save_ledger'
    assert worlds['bare']['unknown_class']['category'] == 'unclassified'
    assert all('closed_at' in worlds[k] for k in ('gap', 'timeout', 'save', 'bare'))
    owner.state['node_exclusion'] = {'actor_nodes': {'bad-actor': 'smp-9-20'},
                                     'excluded': {'smp-9-20': {}}}
    owner.unknown_window_gate()
    effective = owner.unknown_class('bare', worlds['bare'])
    assert (effective['category'], effective['rule']) == ('infrastructure', 'node_auto_excluded')
    assert effective['close_category'] == 'unclassified'
    assert worlds['bare']['unknown_class']['category'] == 'unclassified'  # original kept
    overview = owner.state['unknown_overview']
    assert overview['by_category'] == {'environment_gap': 1, 'infrastructure': 2,
                                       'save_ledger': 1}
    # Model statistics leave infrastructure out: 2 of 2 remaining closes.
    assert overview['model_unknown_fraction'] == 1.0


def test_resume_from_a_c13b_shaped_state_keeps_every_persisted_key(
        owner, watch, tmp_path, monkeypatch):
    """A state written before U4 (no closed_at/unknown_class, floor-rule best and
    regression) resumes through previous_watch_state; old keys keep their meaning."""
    attempt(owner)
    old = _u4_cohort('1.1.0-rc.64', [24] * 50, ['finished'] * 50)
    owner.state['worlds'] = {f'w{i}': {'actor': 'actor', 'generation': 'g-one',
                                       'offered': True, 'closed': True,
                                       'status': 'unknown' if i < 30 else 'finished',
                                       **({'reason': 'runtime_execution_or_release_unknown',
                                           'failure': {'handoff_reason': 'agent_call_timeout'}}
                                          if i < 30 else {})}
                             for i in range(100)}
    # Review H1: c13 crashed with its actors gone, so its state holds lost offers.
    owner.state['worlds'].update({f'lost{i}': {'actor': 'gone', 'generation': 'g-one',
                                               'offered': True} for i in range(25)})
    owner.state['terminal_actors'] = ['gone']
    owner.state['lost_world_offers'] = sorted(f'lost{i}' for i in range(25))
    owner.state['evaluations'] = [{'root': 'c13/evaluation-0001', 'cohort': old,
                                   'closed': True, 'at_training_seconds': 0,
                                   'paired_regression': True,
                                   'regression_reference': 'previous',
                                   'checkpoint': {'checkpoint_artifact_id': 'cp-6787'}}]
    owner.state.update(best_checkpoint='cp-6787', best_cohort=old, active=None)
    owner.persist()
    before = __import__('json').loads(owner.state_path.read_bytes())
    reference = {'path': str(owner.state_path), 'sha256': watch.hashed(owner.state_path)}
    resumed = watch.SegmentWatcher({**owner.config, 'previous_watch_state': reference},
                                   tmp_path / 'watch-c13c')
    for key, value in before.items():
        if key not in {'config_sha256', 'configuration', 'incarnation', 'stop'}:
            assert resumed.state[key] == value, key
    # Old closes and old lost offers carry no time: they never enter the rolling window.
    resumed.refresh_world_health()
    assert resumed.state['worlds']['lost0']['lost_at'] is None
    assert resumed.unknown_window_gate() is None
    assert resumed.state['unknown_window']['closes'] == 0
    assert resumed.state['unknown_overview']['by_category'] == {'infrastructure': 55}
    # The next evaluation names its score; the floor-rule regression never chains into it.
    floors = [10] * 50
    _u4_evaluate(resumed, watch, tmp_path / 'next', monkeypatch,
                 [(7200, 'cp-next', '1.1.0-rc.64', floors, ['finished'] * 50, {})])
    record = resumed.state['evaluations'][-1]
    assert record['previous_reference'] == 'c13/evaluation-0001'
    assert record['paired_regression'] is True and record['regression_score'] == 'conservative'
    assert resumed.state['stop'] is None
    assert resumed.state['best_checkpoint'] == 'cp-6787'


def test_registered_compatible_host_fix_keeps_cohorts_comparable_with_a_note(
        owner, watch, tmp_path, monkeypatch):
    floors = [20] * 4
    statuses = ['finished'] * 4
    owner.compatible_fixes = [{'id': 'u4-1-rc64-rc65', 'key': 'host_pin',
                               'from': {'version': '1.1.0-rc.64'},
                               'to': {'version': '1.1.0-rc.65'},
                               'proof': {'artifact': 'r2://proof/u4-1.json', 'sha256': 'a' * 64}}]
    _u4_evaluate(owner, watch, tmp_path, monkeypatch, [
        (0, 'cp-a', '1.1.0-rc.64', floors, statuses, {}),
        (7200, 'cp-b', '1.1.0-rc.65', [22] * 4, statuses, {}),
        (14400, 'cp-c', '1.1.0-rc.63', [25] * 4, statuses, {})])
    first, second, third = owner.state['evaluations']
    assert 'protocol_skipped' not in second
    assert second['against_previous']['mean_delta'] == 2
    assert second['against_previous']['compatible_protocol_keys'] == ['host_pin']
    assert second['compatible_fixes']['previous'][0]['compatible_fix'] == 'u4-1-rc64-rc65'
    assert second['compatible_fixes']['baseline'][0]['direction'] == 'forward'
    # rc.63 is not registered against rc.64/rc.65: still skipped as before, and the
    # rc.65 best (chosen across the registered fix, never superseded by it) is kept.
    assert third['against_previous']['skipped'] == 'different_observed_protocol'
    assert third['protocol_skipped']['best'] == ['host_pin']
    assert [row['best_checkpoint'] for row in owner.state['superseded_best']] == ['cp-b']


def test_compatible_fix_registry_file_and_validation(watch, tmp_path):
    import json

    registered = watch.read_compatible_fixes()  # the repo file is valid
    assert [(e['id'], e['from']['version'], e['to']['version']) for e in registered] == [
        ('u4-1-host-rc63-to-rc64-soul-nexus', '1.1.0-rc.63', '1.1.0-rc.64')]
    # The c13 crash pair (window-1 rc.63 baseline vs window-2 rc.64) is now comparable.
    before = {'protocol_observed': {'host_pin': registered[0]['from'], 'input_spec': 's'}}
    after = {'protocol_observed': {'host_pin': registered[0]['to'], 'input_spec': 's'}}
    blocking, notes = watch.protocol_blocking(before, after, registered)
    assert blocking == [] and notes[0]['compatible_fix'] == registered[0]['id']
    changed = {'protocol_observed': {**after['protocol_observed'], 'input_spec': 't'}}
    assert watch.protocol_blocking(before, changed, registered)[0] == ['input_spec']
    entry = {'id': 'x', 'key': 'host_pin', 'from': {'version': 'a'}, 'to': {'version': 'b'},
             'proof': {'artifact': 'proof.json', 'sha256': 'b' * 64}}
    path = tmp_path / 'fixes.json'

    def check(entries):
        path.write_text(json.dumps({'schema': watch.COMPATIBLE_FIXES_SCHEMA,
                                    'entries': entries}))
        return watch.read_compatible_fixes(path)

    assert check([entry]) == [entry]
    for bad in ({**entry, 'key': 'runtime_pin'}, {**entry, 'to': entry['from']},
                {**entry, 'proof': {'artifact': 'p'}}, {**entry, 'extra': 1}):
        with pytest.raises(ValueError, match='compatible_fix_registry_invalid'):
            check([bad])
    with pytest.raises(ValueError, match='compatible_fix_registry_duplicate'):
        check([entry, {**entry, 'id': 'y', 'from': entry['to'], 'to': entry['from']}])


def test_evaluation_defaults_to_1800s_games_and_can_avoid_actor_nodes(owner, watch):
    assert watch.EVALUATION_EPISODE_SECONDS == 1800
    owner.config['exclude_nodes'] = ['smp-9-8']
    owner.state['node_exclusion'] = {'actor_nodes': {'a': 'smp-9-1', 'b': 'smp-9-2',
                                                     'done': 'smp-9-3'}}
    owner.state['terminal_actors'] = ['done']
    owner.config['evaluation'] = {}
    assert owner.evaluation_exclude_nodes() == ['smp-9-8']
    owner.config['evaluation'] = {'avoid_actor_nodes': True}
    assert owner.evaluation_exclude_nodes() == ['smp-9-8', 'smp-9-1', 'smp-9-2']


def test_evaluation_and_cpu_steps_avoid_auto_excluded_nodes(owner, watch, tmp_path,
                                                                    monkeypatch):
    """U4 7b: the 11-07 18:22 evaluation landed on smp-9-10, excluded at 18:07."""
    owner.config['exclude_nodes'] = ['smp-9-8']
    owner.config['evaluation'] = {}
    owner.state['node_exclusion'] = {'actor_nodes': {'a': 'smp-9-1'},
                                     'excluded': {'smp-9-10': {'at': 1.0}}}
    assert owner.evaluation_exclude_nodes() == ['smp-9-8', 'smp-9-10']
    owner.config['evaluation'] = {'avoid_actor_nodes': True}
    assert owner.evaluation_exclude_nodes() == ['smp-9-8', 'smp-9-10', 'smp-9-1']
    commands = []
    monkeypatch.setattr(watch.scheduler, 'user_submission_lock', nullcontext)
    monkeypatch.setattr(watch.scheduler, 'output', lambda c: commands.append(c) or '77')
    script = tmp_path / 'prepare.sh'
    script.write_text('#!/bin/sh\n')
    assert owner.submit_cpu_step(script, tmp_path / 'prep', name='rl4-eval-prepare', cpus=1,
                                 minutes=10, memory_gb=6) == '77'
    assert '--exclude=smp-9-8,smp-9-10' in commands[0]


def test_evaluation_pool_list_replaces_the_actor_list_for_evaluations(owner, watch, tmp_path,
                                                                      monkeypatch):
    """OPS 11-07: eval-prepare waited 52 and 57 min because every node outside the actor
    exclude list was full of our own actors. ``evaluation.exclude_nodes`` lets evaluations
    and their CPU preparation use nodes the actors are kept off; auto-excluded nodes and
    (opt-in) running actor nodes are still added; actors keep the configured list."""
    owner.config['exclude_nodes'] = ['smp-9-8', 'smp-c03', 'smp-c04']
    owner.config['evaluation'] = {'exclude_nodes': ['smp-9-8']}
    owner.state['node_exclusion'] = {'actor_nodes': {'a': 'smp-9-1'},
                                     'excluded': {'smp-9-10': {'at': 1.0}}}
    assert owner.evaluation_exclude_nodes() == ['smp-9-8', 'smp-9-10']
    assert owner.actor_exclude_nodes() == ['smp-9-8', 'smp-c03', 'smp-c04', 'smp-9-10']
    owner.config['evaluation'] = {'exclude_nodes': [], 'avoid_actor_nodes': True}
    assert owner.evaluation_exclude_nodes() == ['smp-9-10', 'smp-9-1']
    commands = []
    monkeypatch.setattr(watch.scheduler, 'user_submission_lock', nullcontext)
    monkeypatch.setattr(watch.scheduler, 'output', lambda c: commands.append(c) or '78')
    script = tmp_path / 'prepare.sh'
    script.write_text('#!/bin/sh\n')
    owner.submit_cpu_step(script, tmp_path / 'prep', name='rl4-eval-prepare', cpus=1,
                          minutes=10, memory_gb=6, exclude=owner.evaluation_exclude_nodes())
    assert '--exclude=smp-9-10,smp-9-1' in commands[0]
    owner.submit_cpu_step(script, tmp_path / 'other', name='x', cpus=1, minutes=1,
                          memory_gb=1)  # default: the actor list
    assert '--exclude=smp-9-8,smp-c03,smp-c04,smp-9-10' in commands[1]
    for good in ([], ['smp-c04', 'r01-c01']):
        assert watch.node_list_valid(good)
    for bad in ('smp-9-8', ['smp-9-8', 'smp-9-8'], ['smp-9-[1-3]'], [3], ['a,b']):
        assert not watch.node_list_valid(bad)


def test_unknowns_of_an_auto_excluded_node_only_alarm(owner, watch):
    """U4 7a, user 11-07 "不算，只报警": after its exclusion time a node's closes leave the
    global gate's numerator and denominator; its closes before that still count."""
    attempt(owner)
    now = time.time()
    owner.state['node_exclusion'] = {
        'actor_nodes': {'good': 'smp-9-1', 'bad': 'smp-9-10'},
        'excluded': {'smp-9-10': {'kind': 'node_unknown_exclusion', 'at': now - 600}}}

    def close(intent, actor, status, at):
        owner.state['worlds'][intent] = {'actor': actor, 'generation': 'g-one',
                                         'offered': True, 'closed': True,
                                         'status': status, 'closed_at': at}

    for i in range(100):
        close(f'good-{i}', 'good', 'finished', now - 60)
    for i in range(5):  # before the exclusion: counted as before
        close(f'early-{i}', 'bad', 'unknown', now - 900)
    for i in range(60):  # the c13b wave: 50 unknown + 10 finished after the exclusion
        close(f'late-{i}', 'bad', 'unknown' if i < 50 else 'finished', now - 300)
    alarms = []
    owner.telemetry = lambda kind, **f: alarms.append((kind, f))
    assert owner.unknown_window_gate() is None  # counted, 55/165 would be a stop
    window = owner.state['unknown_window']
    assert (window['closes'], window['unknown']) == (105, 5)
    assert window['excluded_node_closes'] == 60
    assert window['excluded_node_unknown'] == {'smp-9-10': 50}
    overview = owner.state['unknown_overview']
    assert overview['excluded_node_unknown'] == 50 and overview['unknown'] == 55
    assert alarms == [('attention', {
        'reason': 'excluded_node_unknown', 'node': 'smp-9-10', 'unknown': 50,
        'window_seconds': 1800, 'action': 'training_continues_node_already_excluded'})]
    owner.unknown_window_gate()
    assert len(alarms) == 1  # once per node per window
    assert owner.state['worlds']['late-0']['status'] == 'unknown'  # original kept


def test_evaluation_pool_is_the_only_evaluation_placement_and_long_waits_alarm(
        owner, watch):
    """Lead decision S1 (review of #400): with ``evaluation.pool_nodes`` evaluations run only
    on the pool (release_swap writes the complement); a pool the configs do not enforce is
    refused; a queued prepare or evaluation job past 20 min raises one typed attention per
    stage and is never moved to other nodes."""
    good = {'exclude_nodes': ['smp-9-8', 'smp-c03', 'smp-c04'],
            'evaluation': {'pool_nodes': ['smp-c03', 'smp-c04'],
                           'exclude_nodes': ['smp-9-1', 'smp-9-8']}}
    watch.require_evaluation_pool(good)
    watch.require_evaluation_pool({'evaluation': {}})
    for bad in ({**good, 'exclude_nodes': ['smp-9-8', 'smp-c03']},  # actors may use c04
                {**good, 'evaluation': {'pool_nodes': ['smp-c03', 'smp-c04']}},
                {**good, 'evaluation': {'pool_nodes': ['smp-c03'],
                                        'exclude_nodes': ['smp-c03']}},
                {**good, 'evaluation': {'pool_nodes': [], 'exclude_nodes': []}}):
        with pytest.raises(ValueError, match='evaluation_pool_must_be_actor_excluded'):
            watch.require_evaluation_pool(bad)
    owner.config.update(good)
    owner.state['node_exclusion'] = {'excluded': {'smp-9-10': {'at': 1.0}}}
    assert owner.evaluation_exclude_nodes() == ['smp-9-1', 'smp-9-8', 'smp-9-10']
    alarms = []
    owner.telemetry = lambda kind, **f: alarms.append((kind, f))
    now = time.time()
    pending = {'root': 'evaluation-0005', 'stage': 'preparation', 'job_id': '91',
               'stage_submitted_at': now - 1300}
    queued = {'rows': [{'state': 'PENDING'}], 'terminal': False}
    owner.evaluation_wait_alarm(pending, {'rows': [{'state': 'RUNNING'}], 'terminal': False})
    assert alarms == []  # running, not waiting
    owner.evaluation_wait_alarm({**pending, 'stage_submitted_at': now - 60}, queued)
    assert alarms == []  # under 20 min
    owner.evaluation_wait_alarm(pending, queued)
    owner.evaluation_wait_alarm(pending, queued)
    assert [(k, f['reason'], f['stage']) for k, f in alarms] == [
        ('attention', 'evaluation_job_queued_over_limit_no_fallback', 'preparation')]
    assert alarms[0][1]['pool_nodes'] == ['smp-c03', 'smp-c04']
    pending.update(stage='running', stage_submitted_at=now - 1300)
    owner.evaluation_wait_alarm(pending, queued)
    assert len(alarms) == 2 and alarms[1][1]['stage'] == 'running'


def test_readmitted_node_counts_again_and_keeps_its_bad_burst_isolated(owner, watch):
    """OPS re-admission at a natural restart: release_swap probed smp-9-10 healthy and lists
    it in ``readmit_nodes``. The inherited auto exclusion ends; the burst while it was
    excluded stays out of the gate, its closes after re-admission count, and a fresh #386
    window can exclude it again."""
    attempt(owner)
    now = time.time()
    owner.state['node_exclusion'] = {
        'actor_nodes': {'good': 'smp-9-1', 'bad': 'smp-9-10', 'new': 'smp-9-10'},
        'excluded': {'smp-9-10': {'kind': 'node_unknown_exclusion', 'at': now - 900}},
        'windows': {'smp-9-10': [1] * 30}, 'logged': {'smp-9-10': 'node_unknown_exclusion'}}
    owner.config['readmit_nodes'] = [{'node': 'smp-9-10', 'evidence': {'job_id': '9'}},
                                     {'node': 'smp-9-0', 'evidence': {'job_id': '8'}}]
    owner.apply_readmissions()
    nodes = owner.state['node_exclusion']
    assert nodes['excluded'] == {} and 'smp-9-10' not in nodes['windows']
    assert 'smp-9-10' not in nodes['logged'] and 'smp-9-0' not in nodes.get('readmitted', {})
    period = nodes['readmitted']['smp-9-10'][0]
    assert period['excluded_at'] == now - 900 and period['readmitted_at'] >= now
    assert [e['kind'] for e in nodes['events']] == ['node_readmitted',
                                                  'node_readmission_skipped']
    assert owner.actor_exclude_nodes() is None

    def close(intent, actor, status, at):
        owner.state['worlds'][intent] = {'actor': actor, 'generation': 'g-one',
                                         'offered': True, 'closed': True,
                                         'status': status, 'closed_at': at}

    for i in range(100):
        close(f'good-{i}', 'good', 'finished', now - 60)
    for i in range(50):  # the burst while excluded: stays isolated
        close(f'burst-{i}', 'bad', 'unknown', now - 300)
    for i in range(4):  # after re-admission: counted again
        close(f'after-{i}', 'new', 'unknown', time.time() + 1)
    owner.telemetry = lambda kind, **f: None
    owner.state['unknown_window'] = None
    with patch.object(watch.time, 'time', return_value=now + 5):
        assert owner.unknown_window_gate() is None
    window = owner.state['unknown_window']
    assert (window['closes'], window['unknown']) == (104, 4)
    assert window['excluded_node_closes'] == 50
    assert owner.unknown_class('burst-0', owner.state['worlds']['burst-0'])['category'] \
        == 'infrastructure'


def test_newly_lost_offer_enters_the_window_and_unoffered_closes_never_do(owner):
    attempt(owner)
    event(owner, 'actor_world_offered', actor='dead', intent_id='lost', seed='s',
          training_generation='g-one')
    event(owner, 'actor_games_closed', actor='a', games=[
        {'intent_id': 'never', 'seed': 's2', 'status': 'unknown',
         'reason': 'runtime_exited_before_startup', 'world_mutation_offered': False}])
    owner.collect_events()
    owner.state['terminal_actors'] = ['dead']
    owner.refresh_world_health()
    assert owner.state['worlds']['lost']['lost_at'] is not None
    owner.unknown_window_gate()
    window = owner.state['unknown_window']
    assert (window['closes'], window['unknown']) == (1, 1)
    assert window['by_category'] == {'infrastructure': 1}


def test_stop_alarm_is_announced_even_after_an_attention_alarm(owner, watch, monkeypatch):
    stops, alarms = _tick_with_closes(owner, watch, monkeypatch, 100, 12)
    event(owner, 'actor_games_closed', actor='actor', games=[
        {'intent_id': f'late-{i}', 'seed': f'late-{i}', 'status': 'unknown',
         'world_mutation_offered': True, 'training_generation': 'g-one'} for i in range(10)])
    owner.tick_segment()
    assert [f['reason'] for kind, f in alarms if kind == 'attention'] == [
        'training_unknown_window_at_or_above_attention_fraction_lead_investigates',
        'training_unknown_window_at_or_above_stop_fraction']
    assert owner.state['stop'] == 'training_unknown_fraction_above_limit'
    assert [a['level'] for a in owner.state['unknown_alarms']] == ['attention', 'stop']


def test_unscored_evaluation_is_neither_a_regression_verdict_nor_a_reference(
        owner, watch, tmp_path, monkeypatch):
    """Review M1: 50/50 infrastructure unknowns count no game conservatively."""
    n = 50
    infra = {i: {'reason': 'runtime_exited_before_startup'} for i in range(n)}
    _u4_evaluate(owner, watch, tmp_path, monkeypatch, [
        (0, 'cp-a', '1.1.0-rc.64', [30] * n, ['finished'] * n, {}),
        (7200, 'cp-b', '1.1.0-rc.64', [0] * n, ['unknown'] * n, infra),
        (14400, 'cp-c', '1.1.0-rc.64', [10] * n, ['finished'] * n, {})])
    first, second, third = owner.state['evaluations']
    assert second['scores']['conservative_n'] == 0
    assert second['paired_regression'] is None
    assert second['paired_regression_skipped'] == 'no_paired_interval'
    assert third['previous_reference'] == first['root']  # the unscored one is passed over
    assert third['previous_passed_over'] == {'root': second['root'],
                                             'reason': 'no_counted_games'}
    assert 'protocol_skipped' not in third
    assert third['paired_regression'] is True and owner.state['stop'] is None


def test_archiver_lag_is_visible_and_a_slow_final_drain_reports_not_fails(owner, monkeypatch):
    """Review M4: watch-state carries the archiver's backlog, age, rate and error, with
    one attention per episode; the final drain is sized from the backlog and a slow tail
    is reported in archive-final.json (originals kept), not a watcher failure."""
    import json as json_module

    from spireagent.storage.async_archive import ArchivedStore
    from spireagent.storage.blobs import StoreError

    view = {"pending": 3, "oldest_pending_seconds": 10.0, "copied_records": 0,
            "copied_bytes": 0, "last_error": None, "publisher_alive": True}
    waits = []

    class Archive:
        def backlog(self):
            return dict(view)

        def wait_for_publisher(self, *, timeout):
            waits.append(timeout)
            raise StoreError("shared_archive_incomplete", "retain")

    store = ArchivedStore.__new__(ArchivedStore)
    store.archive = Archive()
    monkeypatch.setattr(owner.exchange, "store", store, raising=False)
    alarms = []
    monkeypatch.setattr(owner, "telemetry", lambda kind, **fields: alarms.append(
        fields.get("reason", kind)))
    first = owner.archiver_check()
    assert first["pending"] == 3 and first["alarm"] is None and alarms == []
    assert owner.archiver_check() == first  # 300 s cadence: no second listing
    view.update(oldest_pending_seconds=4000.0, copied_records=3600, copied_bytes=7200)
    owner.state["archiver"]["at"] -= 3600
    record = owner.archiver_check()
    assert record["alarm"] == "archive_backlog_too_old"
    assert record["records_per_hour"] == pytest.approx(3600)
    assert record["bytes_per_hour"] == pytest.approx(7200) and owner.state["archiver"] == record
    owner.state["archiver"]["at"] -= 3600
    owner.archiver_check()
    assert alarms == ["archive_backlog_too_old"]  # one attention per episode
    view.update(pending=1000, copied_records=3600)
    owner.final_archive_drain()
    assert waits == [500.0]  # no copies since the last check: 0.5 s per pending intent
    final = json_module.loads((owner.work / "archive-final.json").read_bytes())
    assert final["full_readback"] is False and final["error"] == "shared_archive_incomplete"
    assert alarms[-1] == "archive_final_drain_incomplete"
