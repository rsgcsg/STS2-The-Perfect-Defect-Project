"""Watcher stop arithmetic and frozen-runtime dispatch; no game/GPU qualification."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip('fcntl', reason='Friday targets POSIX Slurm')
FRIDAY = Path(__file__).resolve().parents[2] / 'tools/friday'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def watch():
    batch = module('watch_batch', FRIDAY / 'batch.py')
    with patch.dict(sys.modules, {'batch': batch}):
        submit = module('watch_submit', FRIDAY / 'submit.py')
        with patch.dict(sys.modules, {'submit': submit}):
            evaluate = module('watch_evaluate', FRIDAY / 'evaluate.py')
            with patch.dict(sys.modules, {'evaluate': evaluate}):
                return module('watch', FRIDAY / 'watch.py')


def test_stops_keep_unknown_denominator_strict_and_baseline_strict(watch):
    config = {'started_at': 100, 'wall_seconds': 72000}
    games = [{'status': 'unknown'}] + [{'status': 'finished'}] * 19
    assert watch.stop_reason(1000, config, games, None, []) is None
    assert watch.stop_reason(1000, config, games[:-1], None, []) == 'training_unknown_fraction_above_5_percent'
    assert watch.stop_reason(72100, config, [], None, []) == 'first_round_wall_limit'
    rows = [{'complete': True, 'mean_max_floor': 11.75}] * 2
    assert watch.stop_reason(1000, config, [], None, rows) is None
    rows = [{'complete': True, 'mean_max_floor': 11.74}] * 2
    assert watch.stop_reason(1000, config, [], None, rows) == 'two_evaluations_below_baseline_by_more_than_two_floors'
    assert watch.stop_reason(1000, config, [], None, rows[:1]) is None
    assert watch.stop_reason(1000, config, [], {'kind': 'progress', 'kl': 0.5}, []) is None
    assert watch.stop_reason(1000, config, [], {'kind': 'failed'}, []) == 'learner_failed'
    assert watch.stop_reason(1000, config, [], {'kind': 'progress', 'policy_loss': float('nan')}, []) == 'learner_nonfinite_policy_loss'


def test_incomplete_audit_evaluation_never_becomes_regression_or_TB_score(watch, tmp_path):
    report = {'score_admitted': False, 'planned': 20, 'not_started': 19, 'reserved': 0,
              'save_pending': 0, 'save_failed': 0, 'saved_unscored': 0,
              'trajectory_pending': 0, 'censored': 0, 'win': 0, 'loss': 0, 'unknown': 1,
              'groups': [{'scored': 0}], 'slots': [{'status': 'unknown'}]}
    path = tmp_path / 'result.json'
    path.write_text(json.dumps({'ledger_report': report}))
    result = watch.evaluate_result(path, 2, 16)
    assert result['complete'] is False and result['unknown'] == 1
    assert result['mean_max_floor'] is None and result['scored'] == 0


def test_20_hour_actor_prepare_pins_old_runtime_and_single_core(watch, tmp_path):
    with patch.dict(sys.modules, {'evaluate': watch.evaluate, 'submit': watch.scheduler,
                                 'batch': module('long_batch', FRIDAY / 'batch.py')}):
        rl = module('long_rl', FRIDAY / 'rl.py')
    peer = {'kind': 's3', 'bucket': 'test', 'endpoint': 'https://example.invalid',
            'prefix': 'test', 'region': 'auto'}
    runtime = tmp_path / 'frozen-source'
    runtime.mkdir()
    execution = tmp_path / 'execution.json'
    execution.write_text(json.dumps({'schema': 'spireagent/managed-evaluation-execution-v1',
       'candidate_directory': str(tmp_path), 'peer': peer, 'store': 's3', 'retention': rl.RETENTION}))
    learner = tmp_path / 'learner.json'
    learner.write_text(json.dumps({'schema': 'stpd/rl-learner-config-1', 'run': 'rl2', 'peer': peer}))
    plan = rl.prepare(execution, learner, tmp_path / 'plan', actors=12, wall_seconds=75600,
                      seed_namespace='rl2-train', runtime_source=runtime)
    assert plan['source_root'] == str(runtime)
    assert len(plan['actors']) == 12
    assert all(json.loads(Path(ref['path']).read_bytes())['wall_seconds'] == 75600
               for ref in plan['actors'])


def test_slurm_terminal_state_requires_all_array_members_and_retains_failures(watch):
    with patch.object(watch.subprocess, 'check_output', return_value=(
            '123_0|COMPLETED|0:0\n123_0.batch|COMPLETED|0:0\n123_1|RUNNING|0:0\n')):
        assert watch.job_accounting('123')['terminal'] is False
    with patch.object(watch.subprocess, 'check_output', return_value=(
            '123_0|COMPLETED|0:0\n123_1|FAILED|1:0\n')):
        value = watch.job_accounting('123')
        assert value['terminal'] is True and value['all_completed'] is False
        assert value['rows'][1]['exit_code'] == '1:0'
    with patch.object(watch.subprocess, 'check_output', return_value=''):
        assert watch.job_accounting('123')['terminal'] is False


def test_terminal_evaluation_without_result_is_attention_and_never_reoffered(watch, tmp_path):
    instance = object.__new__(watch.Watcher)
    plan = tmp_path / 'plan.json'
    instance.state = {'pending': {'plan': str(plan), 'version': 1, 'updates': 8,
                                  'job_id': '123', 'state': 'submitted'}}
    events = []
    instance.persist = lambda: None
    instance.publish = lambda kind, **fields: events.append({'kind': kind, **fields})
    instance.direct = None
    with patch.object(watch, 'job_accounting', return_value={'terminal': True, 'rows': []}):
        instance.collect_evaluation()
    assert instance.state['pending']['state'] == 'terminal_without_result'
    assert events[0]['reason'] == 'evaluation_job_terminal_without_result_no_retry'
    assert instance.state['pending']['job_id'] == '123'


def test_split_runtime_submit_cli_keeps_strict_module_origin(watch, tmp_path):
    import os
    import subprocess
    import textwrap

    batch = module('split_batch', FRIDAY / 'batch.py')
    with patch.dict(sys.modules, {'evaluate': watch.evaluate, 'submit': watch.scheduler, 'batch': batch}):
        rl = module('split_rl', FRIDAY / 'rl.py')
    frozen = tmp_path / 'frozen-source'
    package = frozen / 'python/spireagent'
    (package / 'workbench').mkdir(parents=True)
    for file in ('__init__.py', 'rl_exchange.py', 'workbench/__init__.py', 'workbench/rl_actor.py'):
        (package / file).write_text('# frozen import-origin fixture; no actor executes\n')
    peer = {'kind': 's3', 'bucket': 'test', 'endpoint': 'https://example.invalid',
            'prefix': 'test', 'region': 'auto'}
    execution = tmp_path / 'execution.json'
    execution.write_text(json.dumps({'schema': 'spireagent/managed-evaluation-execution-v1',
        'candidate_directory': str(tmp_path), 'peer': peer, 'store': 's3', 'retention': rl.RETENTION}))
    learner = tmp_path / 'learner.json'
    learner.write_text(json.dumps({'schema': 'stpd/rl-learner-config-1', 'run': 'rl2', 'peer': peer}))
    output = tmp_path / 'plan'
    rl.prepare(execution, learner, output, actors=12, wall_seconds=75600,
               seed_namespace='rl2-training', runtime_source=frozen)
    driver = textwrap.dedent('''\
        import sys
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        import rl
        # Only site-specific scheduler IO/lock path are replaced, as in other Friday tests.
        lock_path = Path(sys.argv[2])
        rl.scheduler.submission_lock_path = lambda: lock_path
        rl.scheduler.output = lambda args: '0/400/0/400' if args[0] == 'sinfo' else ''
        sys.argv = ['rl.py', 'submit', '--plan', sys.argv[3], '--dry-run']
        rl.main()
        ''')
    result = subprocess.run([sys.executable, '-c', driver, str(FRIDAY),
          str(tmp_path / 'private/submit.lock'), str(output / 'plan.json')],
          capture_output=True, text=True, check=True, timeout=15,
          env={'PATH': os.defpath, 'PYTHONPATH': str(frozen / 'python')})
    submission = json.loads(result.stdout)
    assert submission['dry_run'] is True and submission['cores'] == 12
    assert '--time=1265' in submission['command']
    assert '--array=0-11%12' in submission['command']
    launcher = (output / 'launch.sh').read_text()
    assert f'source_root={frozen}' in launcher
    assert 'export PYTHONPATH="$source_root/python"' in launcher
    assert not (output / 'submission.json').exists()


def test_R2_outage_cannot_bypass_actor_wall_stop_or_40_minute_drain(watch, tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    def unavailable(*_args, **_kwargs):
        raise OSError('injected R2 outage')

    exchange = SimpleNamespace(prefix='exchange/rl/rl2/',
        blobs=SimpleNamespace(put_if_absent=unavailable), shard_keys=unavailable,
        request_stop=unavailable, latest_event=unavailable)
    monkeypatch.setattr(watch, 'Exchange', lambda *_: exchange)
    monkeypatch.setattr(watch.sys, 'platform', 'linux')
    monkeypatch.setenv('SLURM_JOB_ID', 'watch-test')
    monkeypatch.setattr(watch.signal, 'signal', lambda *_: None)
    now = [100.0]
    monkeypatch.setattr(watch.time, 'time', lambda: now[0])
    monkeypatch.setattr(watch.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    actor_signals = []

    def signal_actor(command, **_kwargs):
        actor_signals.append((now[0], command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(watch.subprocess, 'run', signal_actor)
    monkeypatch.setattr(watch, 'job_accounting', lambda _: {'terminal': True, 'all_completed': True})
    config = {'peer': {}, 'run': 'rl2', 'started_at': 100, 'wall_seconds': 10,
              'poll_seconds': 900, 'actor_plan': str(tmp_path / 'actors/plan.json'),
              'actor_job_id': '123'}
    instance = watch.Watcher(config, tmp_path / 'watch')
    instance.run()
    assert actor_signals[0][0] == 110.0
    assert actor_signals[0][1] == ['scancel', '--signal=USR1', '--batch', '123']
    assert (tmp_path / 'actors/STOP').is_file()
    assert now[0] == 2510.0  # original wall deadline + forty minutes, despite every R2 call failing
    assert instance.state['learner_stop_pending'] is True
    final = json.loads((tmp_path / 'watch/drain-status.json').read_bytes())
    assert final['drain_timeout'] is True and final['drain_complete'] is False
    assert final['learner_stop_pending'] is True
    assert 'watcher_telemetry_failed' in capsys.readouterr().err
