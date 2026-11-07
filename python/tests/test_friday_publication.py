"""Original Friday publication seams: no game, scheduler offers or recovery replay."""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from test_friday_rl import rl as original_rl
from test_friday_watch import watch as original_watch


@pytest.fixture
def rl():
    return original_rl.__wrapped__()


@pytest.mark.parametrize('raw', [b'', b'{"actor":', b'{"actor":"\xe4'])
def test_live_owner_partial_bytes_wait_and_complete_foreign_owner_still_rejects(
    rl, tmp_path, monkeypatch, raw
):
    plan = tmp_path/'plan.json'
    plan.write_text('{}')
    (tmp_path/'submission.json').write_text('{"job_id":"79"}')
    owner = tmp_path/'actors/a/owner.json'
    owner.parent.mkdir(parents=True)
    owner.write_bytes(raw)
    monkeypatch.setattr(rl, 'load_plan', lambda _: {'actors': [{'actor': 'a'}]})
    monkeypatch.setattr(rl.subprocess, 'check_output',
                        lambda *a, **k: '790|79_0|RUNNING|0:0\n')
    assert rl.actor_accounting(plan)[0]['state'] == 'RUNNING'
    owner.write_text('{"actor":"foreign","job_id":"790","plan_sha256":"bad"}')
    with pytest.raises(ValueError, match='binding_changed'):
        rl.actor_accounting(plan)


def test_exclusive_publication_never_exposes_half_owner_and_never_adopts_existing(
    rl, tmp_path, monkeypatch
):
    from spireagent.storage import local
    path = tmp_path/'owner.json'
    reached, release = threading.Event(), threading.Event()
    original_link = os.link
    def link(source, target):
        assert json.loads(source.read_bytes()) == {'actor': 'a'}
        assert not path.exists()
        reached.set()
        assert release.wait(5)
        return original_link(source, target)
    monkeypatch.setattr(local.os, 'link', link)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rl.publish_exclusive, path, {'actor': 'a'})
        assert reached.wait(5)
        assert rl.read_published(path) is None
        release.set()
        future.result(timeout=5)
    assert rl.read_published(path) == {'actor': 'a'}
    with pytest.raises(FileExistsError):
        rl.publish_exclusive(path, {'actor': 'a'})


def test_two_distinct_workers_cannot_both_win_same_owner(rl, tmp_path):
    path = tmp_path/'owner.json'
    barrier = threading.Barrier(8)
    def publish(number):
        barrier.wait()
        try:
            rl.publish_exclusive(path, {'actor': str(number)})
            return True
        except FileExistsError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(publish, range(8))) == 1
    assert rl.read_published(path)['actor'] in {str(i) for i in range(8)}


@pytest.mark.parametrize('raw', [b'', b'{"ledger_report":', b'{"x":"\xe4'])
def test_terminal_evaluation_waits_for_result_and_snapshot_publication(tmp_path, monkeypatch, raw):
    watch = original_watch.__wrapped__()
    config = {'run': 'publication', 'peer': {'kind': 'local', 'directory': str(tmp_path/'store')},
              'budget_id': 'publication-budget', 'deadline_at': 9999999999,
              'training_seconds': 3600, 'actors': 1}
    owner = watch.SegmentWatcher(config, tmp_path/'watch')
    root = tmp_path/'eval'
    root.mkdir()
    (root/'result.json').write_bytes(raw)
    (root/'snapshot.json').write_text('{"full_readback":true}')
    owner.state['pending'] = {'root': str(root), 'plan': str(root/'plan.json'),
                              'stage': 'running', 'job_id': '80'}
    monkeypatch.setattr(watch, 'job_accounting', lambda _: {'terminal': True})
    owner.evaluation_cohort = lambda *a: pytest.fail('unpublished result must wait')
    owner.collect_segment_evaluation()
    assert owner.state['stop'] is None and owner.state['pending']['stage'] == 'running'


def test_terminal_evaluation_without_result_is_skipped_after_grace(tmp_path, monkeypatch):
    """c13b 11-07: eval job 1444165 was cancelled on a bad node and never published a
    result; the pending evaluation stayed 'running', so the stopped watcher never exited
    and a same-window continuation would never evaluate again."""
    import time
    watch = original_watch.__wrapped__()
    config = {'run': 'publication', 'peer': {'kind': 'local', 'directory': str(tmp_path/'store')},
              'budget_id': 'publication-budget', 'deadline_at': 9999999999,
              'training_seconds': 3600, 'actors': 1}
    owner = watch.SegmentWatcher(config, tmp_path/'watch')
    root = tmp_path/'eval'
    root.mkdir()
    (root/'partial-original.json').write_text('{}')
    owner.state['pending'] = {'root': str(root), 'plan': str(root/'plan.json'),
                              'stage': 'running', 'job_id': '81', 'due_seconds': 14400}
    alarms = []
    owner.telemetry = lambda kind, **fields: alarms.append((kind, fields))
    monkeypatch.setattr(watch, 'job_accounting', lambda _: {'terminal': True})
    owner.evaluation_cohort = lambda *a: pytest.fail('no result to read')
    owner.collect_segment_evaluation()
    seen = owner.state['pending']['terminal_seen_at']
    owner.collect_segment_evaluation()  # still inside the grace window: keeps waiting
    assert owner.state['pending']['terminal_seen_at'] == seen and not alarms
    owner.state['pending']['terminal_seen_at'] = time.time() - watch.EVALUATION_RESULT_GRACE_SECONDS
    owner.collect_segment_evaluation()
    assert owner.state['pending'] is None and owner.state['stop'] is None
    record = owner.state['evaluations'][-1]
    assert record['skipped'] == 'evaluation_job_terminal_without_result'
    assert record['job_id'] == '81' and record['at_training_seconds'] == 14400
    assert alarms == [('attention', {'reason': 'evaluation_job_terminal_without_result',
                                     'evaluation': str(root), 'job_id': '81'})]
    assert (root/'partial-original.json').exists()  # originals kept


def test_exact_game_version_is_taken_from_shipped_release(rl, tmp_path):
    game = tmp_path/'game'
    game.mkdir()
    (game/'release_info.json').write_text('{"version":"v0.111.0"}')
    manifest = rl.evaluate.prepare_learning_curve_manifest(game, tmp_path/'manifest.json')
    assert manifest['game_version'] == 'v0.111.0' and len(manifest['seeds']) == 100
    (game/'release_info.json').write_text('{"version":"0.111.0"}')
    with pytest.raises(ValueError, match='explicit exact game version'):
        rl.evaluate.prepare_learning_curve_manifest(game, tmp_path/'bad.json')


def completed_window(tmp_path, watch, **state):
    import hashlib
    prior_cfg = {'run': 'continuation',
                 'peer': {'kind': 'local', 'directory': str(tmp_path/'store')},
                 'budget_id': 'h1-good', 'training_seconds': 3600, 'deadline_at': 9999990000}
    tmp_path.mkdir(parents=True, exist_ok=True)
    config_file = tmp_path/'prior-config.json'
    config_file.write_text(json.dumps(prior_cfg))
    previous = {'active': None, 'stop': 'run_budget_complete', 'pending': None,
                'configuration': {'path': str(config_file),
                                  'sha256': hashlib.sha256(config_file.read_bytes()).hexdigest()},
                'attempts': [{'generation': 'g-good', 'terminal_accounting': {'terminal': True}}],
                **state}
    state_file = tmp_path/'prior-state.json'
    state_file.write_text(json.dumps(previous))
    config = {**prior_cfg, 'budget_id': 'long', 'training_seconds': 72000,
              'deadline_at': 9999999999, 'actors': 1, 'cpu_only': True,
              'learner_config': str(tmp_path/'learner.json'),
              'previous_window_state': {'path': str(state_file),
                'sha256': hashlib.sha256(state_file.read_bytes()).hexdigest()}}
    return prior_cfg, previous, watch.SegmentWatcher(config, tmp_path/'watch')


def previous_window_CP(prior_cfg, generation='g-good', budget_id='h1-good'):
    return lambda: {
        'checkpoint_artifact_id': 'b'*64, 'deadline_at': prior_cfg['deadline_at'],
        'training_generation': generation, 'producer': 'learner',
        'operating_budget': {'id': budget_id, 'total_seconds': 3600, 'consumed_seconds': 3600}}


def test_completed_window_reference_cannot_authorize_other_window_CP(tmp_path, monkeypatch):
    import sys
    watch = original_watch.__wrapped__()
    for stop in ('run_budget_complete', 'absolute_deadline'):
        prior_cfg, _, owner = completed_window(tmp_path/stop, watch, stop=stop)
        owner.select_checkpoint = previous_window_CP(prior_cfg, 'g-failed', 'failed-window')
        monkeypatch.setitem(sys.modules, 'rl', SimpleNamespace())
        with pytest.raises(ValueError, match='does_not_belong'):
            owner.start_attempt()
        assert not owner.state['attempts']


@pytest.mark.parametrize('stop', ['run_budget_complete', 'absolute_deadline'])
def test_window_ended_by_budget_or_absolute_deadline_links_its_own_CP(tmp_path, monkeypatch,
                                                                      stop):
    import sys

    from stpd.rl.learner import LearnerConfig
    watch = original_watch.__wrapped__()
    prior_cfg, previous, owner = completed_window(tmp_path, watch, stop=stop)
    watch.require_completed_previous_window(previous, prior_cfg, owner.config)
    owner.select_checkpoint = previous_window_CP(prior_cfg)
    monkeypatch.setitem(sys.modules, 'rl', SimpleNamespace())
    def linked(_):
        raise LookupError('previous_window_link_accepted')
    monkeypatch.setattr(LearnerConfig, 'read', staticmethod(linked))
    with pytest.raises(LookupError, match='previous_window_link_accepted'):
        owner.start_attempt()


@pytest.mark.parametrize('state', [
    {'stop': 'training_unknown_fraction_above_limit'},
    {'stop': 'watcher_failure_requires_reconciliation'},
    {'stop': 'watcher_signal'},
    {'stop': None},
    {'stop': 'absolute_deadline', 'active': 0},
    {'stop': 'absolute_deadline', 'pending': {'stage': 'running', 'job_id': '81'}},
    {'stop': 'absolute_deadline', 'attempts': [{'generation': 'g-good'}]},
])
def test_only_completed_previous_window_can_be_linked(tmp_path, monkeypatch, state):
    import sys
    watch = original_watch.__wrapped__()
    prior_cfg, previous, owner = completed_window(tmp_path, watch, **state)
    with pytest.raises(ValueError, match='completed_distinct_previous_operating_window'):
        watch.require_completed_previous_window(previous, prior_cfg, owner.config)
    owner.select_checkpoint = previous_window_CP(prior_cfg)
    monkeypatch.setitem(sys.modules, 'rl', SimpleNamespace())
    with pytest.raises(ValueError, match='completed_distinct_previous_operating_window'):
        owner.start_attempt()
    assert not owner.state['attempts']
    for changed in ({'run': 'other'}, {'budget_id': prior_cfg['budget_id']}):
        with pytest.raises(ValueError, match='completed_distinct_previous_operating_window'):
            watch.require_completed_previous_window(
                {**previous, 'stop': 'absolute_deadline', 'active': None, 'pending': None,
                 'attempts': [{'terminal_accounting': {'terminal': True}}]},
                prior_cfg, {**owner.config, **changed})


@pytest.mark.parametrize('earlier,final', [
    (None, 'absolute_deadline'),
    ('training_unknown_fraction_above_limit', 'training_unknown_fraction_above_limit'),
])
def test_watcher_started_after_deadline_ends_with_typed_stop_never_relabelled(
    tmp_path, earlier, final
):
    import time
    watch = original_watch.__wrapped__()
    owner = watch.SegmentWatcher({
        'run': 'late', 'peer': {'kind': 'local', 'directory': str(tmp_path/'store')},
        'budget_id': 'late-budget', 'deadline_at': time.time() + 3600,
        'training_seconds': 72000, 'actors': 1}, tmp_path/'watch')
    owner.config['deadline_at'] = time.time() - 1
    owner.state['stop'] = earlier
    owner.start_attempt = lambda: pytest.fail('no offer after the absolute deadline')
    owner.tick_segment()
    assert owner.state['stop'] == final and owner.state['active'] is None


@pytest.mark.parametrize('own_cp', [False, True])
def test_new_window_link_stays_in_force_until_its_own_first_CP(tmp_path, monkeypatch, own_cp):
    """Review #388 M1: attempt-0001 of a linked window died before its first CP."""
    import sys

    from stpd.rl.learner import LearnerConfig
    watch = original_watch.__wrapped__()
    prior_cfg, _, owner = completed_window(tmp_path, watch)
    parent = previous_window_CP(prior_cfg)()
    newest_prior = {**parent, 'checkpoint_artifact_id': 'c'*64}
    own = {**parent, 'checkpoint_artifact_id': 'd'*64, 'training_generation': 'g-ops-0001',
           'deadline_at': owner.config['deadline_at'],
           'operating_budget': {'id': 'long', 'total_seconds': 72000, 'consumed_seconds': 600}}
    stored = {cp['checkpoint_artifact_id']: cp for cp in (parent, newest_prior, own)}
    reads = []
    def cold(identity):
        reads.append(identity)
        return dict(stored[identity])
    owner.cold_checkpoint = cold
    records = [{'checkpoint_artifact_id': 'c'*64, 'published_at': 1.}]
    if own_cp:
        records.append({'checkpoint_artifact_id': 'd'*64, 'published_at': 2.})
    owner.exchange.checkpoint_records = lambda: list(records)
    monkeypatch.setattr(LearnerConfig, 'read', staticmethod(
        lambda _: SimpleNamespace(resume_checkpoint='b'*64, segments=None)))
    monkeypatch.setitem(sys.modules, 'rl', SimpleNamespace())
    owner.state['attempts'] = [{'generation': 'g-ops-0001',
                                'terminal_accounting': {'terminal': True}}]
    owner.state['training_seconds'] = 600.
    # Both paths pass every link/deadline/budget check and reach the learner config;
    # before the fix the first raised checkpoint_deadline_cannot_be_reset.
    with pytest.raises(ValueError, match='short_segment_learner_configuration_required'):
        owner.start_attempt()
    assert reads == (['d'*64] if own_cp else ['c'*64, 'b'*64])
    assert owner.state['training_seconds'] == 600. and len(owner.state['attempts']) == 1


@pytest.mark.parametrize('earlier,final', [
    (None, 'absolute_deadline'),
    ('training_unknown_fraction_above_limit', 'training_unknown_fraction_above_limit'),
])
@pytest.mark.parametrize('stage', ['preparation', 'legacy_failed'])
def test_preparation_abandoned_at_deadline_keeps_stop_and_releases_window_link(
    tmp_path, monkeypatch, earlier, final, stage
):
    """Review #388 M2: same guard as tick_segment; no sticky pending without a World."""
    import time
    watch = original_watch.__wrapped__()
    owner = watch.SegmentWatcher({
        'run': 'prep', 'peer': {'kind': 'local', 'directory': str(tmp_path/'store')},
        'budget_id': 'prep-budget', 'deadline_at': time.time() + 600,
        'training_seconds': 72000, 'actors': 1}, tmp_path/'watch')
    owner.state['stop'] = earlier
    pending = {'stage': 'preparation', 'root': str(tmp_path/'evaluation-0001'),
               'checkpoint': {'checkpoint_artifact_id': 'e'*64}, 'due_seconds': 7200,
               'job_id': '91'}
    if stage == 'legacy_failed':  # persisted by the code before this fix
        pending.update(stage='failed', reason='absolute_deadline_no_World_offer')
    owner.state['pending'] = pending
    signalled = []
    owner.signal_once = signalled.append
    accounting = {'terminal': False}
    monkeypatch.setattr(watch, 'job_accounting', lambda _: dict(accounting))
    owner.collect_segment_evaluation()
    if stage == 'preparation':
        assert owner.state['stop'] == final and signalled == ['91']
        assert owner.state['pending']['stage'] == 'preparation_abandoned'
        owner.collect_segment_evaluation()
    assert owner.state['pending'] is not None  # its job is not terminal yet
    accounting['terminal'] = True
    owner.collect_segment_evaluation()
    assert owner.state['pending'] is None
    record = owner.state['evaluations'][-1]
    assert record['skipped'] == 'absolute_deadline_no_World_offer'
    assert record['job_id'] == '91' and record['at_training_seconds'] == 7200
    owner.state.update(stop=owner.state['stop'] or earlier,
                       attempts=[{'generation': 'g', 'terminal_accounting': {'terminal': True}}])
    following = {**owner.config, 'budget_id': 'next-budget'}
    if owner.state['stop'] == 'absolute_deadline':
        watch.require_completed_previous_window(owner.state, owner.config, following)
    else:
        with pytest.raises(ValueError, match='completed_distinct_previous_operating_window'):
            watch.require_completed_previous_window(owner.state, owner.config, following)


def test_preparation_before_deadline_still_follows_its_original_path(tmp_path, monkeypatch):
    watch = original_watch.__wrapped__()
    owner = watch.SegmentWatcher({
        'run': 'prep-normal', 'peer': {'kind': 'local', 'directory': str(tmp_path/'store')},
        'budget_id': 'prep-normal-budget', 'deadline_at': 9999999999,
        'training_seconds': 72000, 'actors': 1}, tmp_path/'watch')
    owner.state['pending'] = {'stage': 'preparation', 'root': str(tmp_path/'evaluation-0001'),
                              'due_seconds': 7200, 'job_id': '92'}
    accounting = {'terminal': False}
    monkeypatch.setattr(watch, 'job_accounting', lambda _: dict(accounting))
    owner.collect_segment_evaluation()
    assert owner.state['pending']['stage'] == 'preparation' and owner.state['stop'] is None
    accounting.update(terminal=True, all_completed=True)
    owner.collect_segment_evaluation()  # completed, plan not yet visible: wait for it
    assert owner.state['pending']['stage'] == 'preparation' and owner.state['stop'] is None
    seen = owner.state['pending']['plan_terminal_seen_at']
    restarted = watch.SegmentWatcher(owner.config, tmp_path/'watch')  # grace survives a restart
    restarted.collect_segment_evaluation()
    assert restarted.state['pending']['plan_terminal_seen_at'] == seen
    assert restarted.state['stop'] is None
    restarted.state['pending']['plan_terminal_seen_at'] -= watch.EVALUATION_RESULT_GRACE_SECONDS
    restarted.collect_segment_evaluation()  # still no plan after the grace: the typed stop
    assert restarted.state['stop'] == 'evaluation_CP_preparation_failed'
    assert restarted.state['pending']['stage'] == 'failed' and not restarted.state['evaluations']


@pytest.mark.parametrize('earlier', [None, 'learner_failed_requires_diagnosis'])
def test_failed_preparation_without_plan_stops_at_once_and_keeps_an_earlier_stop(
        tmp_path, monkeypatch, earlier):
    watch = original_watch.__wrapped__()
    owner = watch.SegmentWatcher({
        'run': 'prep-failed', 'peer': {'kind': 'local', 'directory': str(tmp_path/'store')},
        'budget_id': 'prep-failed-budget', 'deadline_at': 9999999999,
        'training_seconds': 72000, 'actors': 1}, tmp_path/'watch')
    owner.state.update(stop=earlier, pending={'stage': 'preparation', 'due_seconds': 7200,
                       'root': str(tmp_path/'evaluation-0001'), 'job_id': '95'})
    monkeypatch.setattr(watch, 'job_accounting',
                        lambda _: {'terminal': True, 'all_completed': False})
    owner.collect_segment_evaluation()  # FAILED/CANCELLED: no late plan to wait for
    assert owner.state['stop'] == (earlier or 'evaluation_CP_preparation_failed')
    assert owner.state['pending']['stage'] == 'failed'
    assert 'plan_terminal_seen_at' not in owner.state['pending']


def test_plan_grace_at_deadline_takes_the_deadline_path(tmp_path, monkeypatch):
    watch = original_watch.__wrapped__()
    owner = watch.SegmentWatcher({
        'run': 'prep-deadline', 'peer': {'kind': 'local', 'directory': str(tmp_path/'store')},
        'budget_id': 'prep-deadline-budget', 'deadline_at': 9999999999,
        'training_seconds': 72000, 'actors': 1}, tmp_path/'watch')
    owner.state['pending'] = {'stage': 'preparation', 'due_seconds': 7200, 'job_id': '96',
                              'root': str(tmp_path/'evaluation-0001')}
    monkeypatch.setattr(watch, 'job_accounting',
                        lambda _: {'terminal': True, 'all_completed': True})
    owner.signal_once = lambda _job: None
    owner.collect_segment_evaluation()
    assert owner.state['stop'] is None and 'plan_terminal_seen_at' in owner.state['pending']
    owner.config['deadline_at'] = time.time() + 60  # the deadline arrives inside the grace
    owner.collect_segment_evaluation()
    assert owner.state['pending']['stage'] == 'preparation_abandoned'
    assert owner.state['stop'] == 'absolute_deadline'


def test_preparation_plan_visible_late_is_submitted_not_a_stop(tmp_path, monkeypatch):
    """c13d 11-08: the preparation job wrote plan.json 00:16:30.8 and was terminal at
    00:16:31; the login node did not see the plan yet and the window stopped."""
    watch = original_watch.__wrapped__()
    owner = watch.SegmentWatcher({
        'run': 'prep-late', 'peer': {'kind': 'local', 'directory': str(tmp_path/'store')},
        'budget_id': 'prep-late-budget', 'deadline_at': 9999999999,
        'training_seconds': 72000, 'actors': 1, 'evaluation': {}}, tmp_path/'watch')
    root = tmp_path/'evaluation-0005'
    owner.state['pending'] = {'stage': 'preparation', 'root': str(root),
                              'due_seconds': 28800, 'job_id': '93'}
    accounting = {'terminal': True, 'all_completed': True}
    monkeypatch.setattr(watch, 'job_accounting', lambda _: dict(accounting))
    submitted = []
    monkeypatch.setattr(watch.evaluate, 'submit',
                        lambda plan, **kw: submitted.append(plan) or {'job_id': '94'})
    owner.collect_segment_evaluation()
    assert owner.state['stop'] is None and not submitted
    plan = root/'round/evaluation/plan.json'
    plan.parent.mkdir(parents=True)
    plan.write_text('{}')
    owner.collect_segment_evaluation()
    assert owner.state['stop'] is None and submitted == [plan]
    assert owner.state['pending']['stage'] == 'running' and owner.state['pending']['job_id'] == '94'
    # The evaluation job's result also arrives late: its own grace starts now, not at
    # the preparation's terminal time (review H1).
    owner.state['pending']['plan_terminal_seen_at'] -= 2 * watch.EVALUATION_RESULT_GRACE_SECONDS
    owner.evaluation_cohort = lambda *a: pytest.fail('no result to read')
    owner.collect_segment_evaluation()
    assert owner.state['pending'] is not None and owner.state['pending']['stage'] == 'running'
    assert not owner.state['evaluations'] and owner.state['stop'] is None


def test_H1_anchor_is_first_cycle_point_without_moving_two_hour_cadence(tmp_path):
    watch = original_watch.__wrapped__()
    config = {'run': 'anchor', 'peer': {'kind': 'local', 'directory': str(tmp_path/'store')},
              'budget_id': 'anchor-budget', 'deadline_at': 9999999999,
              'training_seconds': 72000, 'actors': 1,
              'evaluation': {'initial_checkpoint': 'a'*64}}
    owner = watch.SegmentWatcher(config, tmp_path/'watch')
    assert owner.state['next_evaluation_seconds'] == 0
    called = []
    def cold(identity):
        called.append(identity)
        raise LookupError('anchor_selected_before_offer')
    owner.cold_checkpoint = cold
    with pytest.raises(LookupError, match='anchor_selected'):
        owner.schedule_segment_evaluation()
    assert called == ['a'*64]


def test_no_evaluation_is_still_a_valid_optional_value(tmp_path):
    watch = original_watch.__wrapped__()
    config = {'run': 'no-evaluation',
              'peer': {'kind': 'local', 'directory': str(tmp_path/'store')},
              'budget_id': 'no-eval-budget', 'deadline_at': 9999999999,
              'training_seconds': 3600, 'actors': 1, 'evaluation': None}
    owner = watch.SegmentWatcher(config, tmp_path/'watch')
    assert owner.state['next_evaluation_seconds'] == 7200


def test_holder_waits_for_first_complete_budget_without_declaring_unlimited_hold(tmp_path):
    import subprocess
    import sys
    import time
    from pathlib import Path
    budget = tmp_path/'budget.json'
    budget.write_bytes(b'')
    root = tmp_path/'holder'
    script = Path(__file__).resolve().parents[2]/'tools/friday/gpu_session.py'
    env = {**os.environ, 'SLURM_JOB_ID': '123'}
    child = subprocess.Popen([sys.executable, str(script), '--root', str(root),
                              '--budget-file', str(budget)], env=env)
    try:
        time.sleep(.25)
        assert child.poll() is None and not (root/'allocation.json').exists()
        budget.write_text('{"seconds":1}')
        assert child.wait(timeout=5) == 0
        assert json.loads((root/'allocation.json').read_bytes())['initial_budget_seconds'] == 1
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
