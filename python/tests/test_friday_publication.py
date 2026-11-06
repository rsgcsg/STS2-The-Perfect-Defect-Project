"""Original Friday publication seams: no game, scheduler offers or recovery replay."""
import json
import os
import threading
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
