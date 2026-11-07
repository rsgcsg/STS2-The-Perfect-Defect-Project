"""Normal operational facts, not gameplay or GPU qualification."""

from __future__ import annotations

import time
from contextlib import nullcontext
from types import SimpleNamespace

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


# Closes arrive in actor batches long after offers, so the gate counts offered
# worlds (5 in flight here) rather than only the closes reported so far.
@pytest.mark.parametrize("closed,unknown,stopped",
                         [(20, 5, False), (20, 6, True), (220, 11, False), (218, 12, True)])
def test_global_five_percent_gate_counts_offered_worlds(owner, watch, monkeypatch,
                                                          closed, unknown, stopped):
    value = attempt(owner)
    value["actor_plans"] = []
    games = [{"intent_id": f"done-{i}", "seed": f"seed-{i}",
              "status": "unknown" if i < unknown else "finished",
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
    stops = []
    owner.stop_attempt = lambda _, reason: stops.append(reason)
    monkeypatch.setattr(watch, "job_accounting", lambda _: {"terminal": False})
    owner.tick_segment()
    assert owner.state["health"]["completed_for_stop"] == closed
    assert owner.state["health"]["known_unknown"] == unknown
    assert owner.state["stop"] == ("training_unknown_fraction_above_limit" if stopped else None)
    assert stops == (["training_unknown_fraction_above_limit"] if stopped else [])


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
                "rows": [{"seed": seed, "floor": floor, "scored": True, "status": "finished"}
                         for seed in ("seed-a", "seed-b")]}

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
            "rows": [{"seed": seed, "floor": floor, "scored": True, "status": "finished"}
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
