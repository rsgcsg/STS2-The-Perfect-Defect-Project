"""Friday RL resource boundary tests; no RL1 private source, game or learner execution."""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip('fcntl', reason='Friday RL actors target POSIX Slurm')
FRIDAY = Path(__file__).resolve().parents[2] / 'tools/friday'


def file_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def rl():
    batch = file_module('friday_rl_batch', FRIDAY / 'batch.py')
    with patch.dict(sys.modules, {'batch': batch}):
        scheduler = file_module('friday_rl_scheduler', FRIDAY / 'submit.py')
        with patch.dict(sys.modules, {'submit': scheduler}):
            evaluation = file_module('friday_rl_evaluate', FRIDAY / 'evaluate.py')
            with patch.dict(sys.modules, {'evaluate': evaluation}):
                return file_module('friday_rl_resource', FRIDAY / 'rl.py')


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path.resolve()
    candidate = root / 'candidate'
    candidate.mkdir()
    peer = {'kind': 's3', 'bucket': 'test', 'endpoint': 'https://example.invalid',
            'prefix': 'stpd', 'region': 'auto'}
    execution = root / 'execution.json'
    execution.write_text(json.dumps({'schema': 'spireagent/managed-evaluation-execution-v1',
        'candidate_directory': str(candidate), 'peer': peer, 'store': 's3',
        'retention': {'tier': 'trajectory', 'purpose': 'training', 'audit_every': 50}}),
        encoding='utf-8')
    learner = root / 'learner.json'
    learner.write_text(json.dumps({'schema': 'stpd/rl-run-1', 'run': 'rl1-real-1',
        'peer': peer, 'seed_namespace': 'rl1-friday-20261029',
        'actor_defaults': {'behavior': {'mode': 'temperature_uniform', 'temperature': 1.0,
                                        'uniform_mix': 0.03}}}), encoding='utf-8')
    return execution, learner, root / 'plan'


def prepared(rl, inputs, **options):
    execution, learner, output = inputs
    return rl.prepare(execution, learner, output, **options), output / 'plan.json'


def test_prepare_without_RL1_import_freezes_same_peer_and_eight_unique_actors(rl, inputs):
    plan, file = prepared(rl, inputs)
    configs = [json.loads(Path(ref['path']).read_bytes()) for ref in plan['actors']]
    assert len(configs) == len({config['actor'] for config in configs}) == 8
    assert all(config['run'] == plan['run'] and config['peer'] == plan['peer']
               for config in configs)
    assert all(config['games_per_ledger'] == 2 for config in configs)
    assert all(config['wall_seconds'] == 3300 and config['episode_seconds'] == 900
               and config['max_games'] == 1000 for config in configs)
    assert all('unknown_stop' not in config for config in configs)
    assert all(config['behavior'] == {'mode': 'temperature_uniform', 'temperature': 1.0,
                                     'uniform_mix': 0.03} for config in configs)
    assert plan['cross_actor_immediate_unknown_stop'] is False
    assert plan['automatic_mac_fallback'] is False
    assert rl.load_plan(file)['run'] == 'rl1-real-1'


def test_peer_drift_and_candidate_symlink_are_rejected(rl, inputs, tmp_path):
    execution, learner, output = inputs
    value = json.loads(execution.read_bytes())
    value['peer']['prefix'] = 'different-run-location'
    execution.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='same_frozen_R2'):
        rl.prepare(execution, learner, output)
    value['peer'] = json.loads(learner.read_bytes())['peer']
    alias = tmp_path / 'candidate-alias'
    alias.symlink_to(value['candidate_directory'], target_is_directory=True)
    value['candidate_directory'] = str(alias)
    execution.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='canonical_physical'):
        rl.prepare(execution, learner, output)


def test_explicit_learner_driven_scale_can_prepare_forty_actors(rl, inputs):
    plan, _ = prepared(rl, inputs, actors=40)
    assert len({ref['actor'] for ref in plan['actors']}) == 40


def test_missing_or_foreign_RL1_modules_are_explicitly_refused_before_allocation(rl, inputs):
    _plan, file = prepared(rl, inputs)
    with (patch.object(rl.importlib.util, 'find_spec', return_value=None),
          patch.object(rl.scheduler, 'output') as scheduler,
          pytest.raises(ValueError, match='RL1_modules_not_merged')):
        rl.submit(file)
    scheduler.assert_not_called()
    with (patch.object(rl.importlib.util, 'find_spec',
                       return_value=SimpleNamespace(origin='/foreign/rl_actor.py')),
          pytest.raises(ValueError, match='RL1_modules_not_merged')):
        rl.run(file)
    assert not (file.parent / 'actors').exists()


def test_module_binding_uses_the_current_project_exchange_owner(rl, tmp_path):
    source = tmp_path.resolve() / 'release/source'
    modules = {'spireagent.workbench.rl_actor': 'workbench/rl_actor.py',
               'spireagent.rl_exchange': 'rl_exchange.py'}
    paths = {name: source / 'python/spireagent' / path for name, path in modules.items()}
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# module location boundary; no RL implementation\n')
    with patch.object(rl.importlib.util, 'find_spec',
                      side_effect=lambda name: SimpleNamespace(origin=str(paths[name]))):
        rl.require_modules(source)


def test_submit_reuses_F2_gate_and_fc430_single_core_array(rl, inputs, tmp_path):
    _plan, file = prepared(rl, inputs)
    lock = tmp_path.resolve() / 'settings/submit.lock'
    with (patch.object(rl, 'require_modules'),
          patch.object(rl.scheduler, 'submission_lock_path', return_value=lock),
          patch.object(rl.scheduler, 'output', side_effect=['', '0/400/0/400', ''])):
        result = rl.submit(file, dry_run=True)
    assert '--array=0-7%8' in result['command']
    assert '--signal=B:USR1@1980' in result['command']
    assert '--cpus-per-task=1' in result['command']
    assert '--constraint=fc430' in result['command']
    assert '--no-requeue' in result['command'] and '--job-name=spireagent-f2' in result['command']
    script = (file.parent / 'launch.sh').read_text(encoding='utf-8')
    assert '$source_root/tools/friday/rl.py" run --plan' in script
    subprocess.run(['bash', '-n', str(file.parent / 'launch.sh')], check=True)
    with (patch.object(rl, 'require_modules'),
          patch.object(rl.scheduler, 'submission_lock_path', return_value=lock),
          patch.object(rl.scheduler, 'output', return_value='1394000_[0-7]') as scheduler,
          pytest.raises(ValueError, match='must_drain')):
        rl.submit(file)
    assert scheduler.call_count == 1


def test_changed_actor_configuration_is_rejected(rl, inputs):
    plan, file = prepared(rl, inputs)
    Path(plan['actors'][0]['path']).write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='pinned_input_changed'):
        rl.load_plan(file)


@pytest.mark.parametrize('disposition', ['unknown', 'save_failed', 'censored'])
def test_run_calls_original_module_and_abnormal_results_fence_pending_workers(
        rl, inputs, tmp_path, monkeypatch, disposition):
    plan, file = prepared(rl, inputs, actors=2)
    monkeypatch.setattr(rl, 'require_modules', lambda _: None)
    monkeypatch.setattr(rl.sys, 'platform', 'linux')
    monkeypatch.setenv('SLURM_JOB_ID', 'owned-test')
    monkeypatch.setenv('SLURM_CPUS_PER_TASK', '1')
    monkeypatch.setenv('SLURM_ARRAY_TASK_ID', '0')
    real_mkdtemp = tempfile.mkdtemp
    monkeypatch.setattr(rl.tempfile, 'mkdtemp',
                        lambda **kw: real_mkdtemp(dir=tmp_path, **kw))
    commands = []

    def actor(command, **_keywords):
        commands.append(command)
        assert command[1:3] == ['-m', 'spireagent.workbench.rl_actor']
        assert command[3:5] == ['--config', plan['actors'][0]['path']]
        work = Path(command[-1])
        (work / 'actor-summary.json').write_text(json.dumps({
            'schema': 'spireagent/rl-actor-summary-1', 'actor': plan['actors'][0]['actor'],
            'run': plan['run'], 'status': 'completed',
            'games': [{'status': disposition, 'seed': 'test-no-game', 'shard': None}]}),
            encoding='utf-8')
        return SimpleNamespace(wait=lambda: 0)

    original_snapshot = rl.snapshot
    original_popen = subprocess.Popen

    def checked_snapshot(source, target):
        assert (file.parent / 'STOP').is_file()
        assert callable(signal.getsignal(signal.SIGUSR1))
        with patch.object(rl.subprocess, 'Popen', original_popen):
            return original_snapshot(source, target)

    monkeypatch.setattr(rl.subprocess, 'Popen', actor)
    monkeypatch.setattr(rl, 'snapshot', checked_snapshot)
    result = rl.run(file)
    assert result['original_summary']['games'][0]['status'] == disposition
    assert result['cross_actor_immediate_unknown_stop'] is False
    assert len(commands) == 1
    monkeypatch.setenv('SLURM_ARRAY_TASK_ID', '1')
    stopped = rl.run(file)
    assert stopped['new_games'] == 0
    # Only test-owned fixtures are changed here to exercise the permanent per-actor lease.
    (file.parent / 'STOP').unlink()
    monkeypatch.setenv('SLURM_ARRAY_TASK_ID', '0')
    with pytest.raises(FileExistsError):
        rl.run(file)
    assert len(commands) == 1


@pytest.mark.parametrize('games', [0, 11])
def test_games_per_ledger_rejects_values_outside_original_one_to_ten(rl, inputs, games):
    with pytest.raises(ValueError, match='actor_budget'):
        prepared(rl, inputs, games_per_ledger=games)


def test_original_ten_game_ledger_and_thousand_game_budget_are_supported(rl, inputs):
    plan, _ = prepared(rl, inputs, games_per_ledger=10, wall_seconds=10000)
    actor = json.loads(Path(plan['actors'][0]['path']).read_bytes())
    assert actor['games_per_ledger'] == 10 and actor['max_games'] == 1000
    assert 'unknown_stop' not in actor


def test_prepare_CLI_uses_section8_handoff_defaults_without_submitting(inputs):
    execution, learner, output = inputs
    value = json.loads(learner.read_bytes())
    value['schema'] = 'stpd/rl-learner-config-1'
    value.pop('actor_defaults')
    value.pop('seed_namespace')
    learner.write_text(json.dumps(value), encoding='utf-8')
    command = [sys.executable, str(FRIDAY / 'rl.py'), 'prepare', '--execution', str(execution),
               '--learner-config', str(learner), '--output', str(output), '--actors', '6',
               '--seed-namespace', 'rl1-real-1', '--max-games', '10000']
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=15,
        env={'PATH': os.defpath, 'PYTHONPATH': str(FRIDAY.parents[1] / 'python')})
    plan = json.loads(result.stdout)
    actor = json.loads(Path(plan['actors'][0]['path']).read_bytes())
    assert plan['run'] == 'rl1-real-1' and len(plan['actors']) == 6
    assert actor['games_per_ledger'] == 2 and actor['episode_seconds'] == 900
    assert actor['wall_seconds'] == 3300 and actor['max_games'] == 10000
    assert not (output / 'submission.json').exists()
    assert not (output / 'actors').exists()


def test_parent_forwards_real_USR1_to_actor_process_and_retains_STOP(rl, inputs, tmp_path):
    """Real Popen/signal transport, with only the RL Actor module replaced by a no-game stub."""
    plan, file = prepared(rl, inputs, actors=1)
    stubs = tmp_path / 'stub-modules'
    package = stubs / 'spireagent/workbench'
    package.mkdir(parents=True)
    (stubs / 'spireagent/__init__.py').write_text('', encoding='utf-8')
    (package / '__init__.py').write_text('', encoding='utf-8')
    ready = tmp_path / 'actor-ready'
    (package / 'rl_actor.py').write_text(textwrap.dedent('''\
        import argparse, json, os, signal, threading
        from pathlib import Path
        parser = argparse.ArgumentParser()
        parser.add_argument('--config', type=Path)
        parser.add_argument('--work', type=Path)
        args = parser.parse_args()
        config = json.loads(args.config.read_bytes())
        received = threading.Event()
        signal.signal(signal.SIGUSR1, lambda *_: received.set())
        Path(os.environ['TEST_READY']).write_text(json.dumps({
            'pid': os.getpid(), 'module': __file__}))
        assert received.wait(15), 'USR1 was not forwarded by the parent Popen owner'
        assert (args.work / 'STOP').is_file()
        (args.work / 'actor-summary.json').write_text(json.dumps({
            'schema': 'spireagent/rl-actor-summary-1', 'actor': config['actor'],
            'run': config['run'], 'signal': signal.SIGUSR1,
            'status': 'stopped_signal', 'games': []}))
        '''), encoding='utf-8')
    driver = textwrap.dedent('''\
        import json, sys
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        import rl
        rl.require_modules = lambda _: None  # This test replaces only the Actor process boundary.
        rl.sys.platform = 'linux'
        print(json.dumps(rl.run(Path(sys.argv[2]))), flush=True)
        ''')
    command = [sys.executable, '-c', driver, str(FRIDAY), str(file)]
    environment = {'PATH': os.defpath, 'PYTHONPATH': str(stubs), 'TMPDIR': str(tmp_path),
                   'SLURM_JOB_ID': 'signal-stub', 'SLURM_CPUS_PER_TASK': '1',
                   'SLURM_ARRAY_TASK_ID': '0', 'TEST_READY': str(ready)}
    with subprocess.Popen(command, env=environment, cwd=tmp_path, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) as process:
        try:
            deadline = time.monotonic() + 15
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.is_file(), (
                f'no-game Actor did not install its signal handler; driver_exit={process.poll()}')
            origin = json.loads(ready.read_bytes())
            assert origin['pid'] != process.pid
            assert Path(origin['module']).resolve() == (package / 'rl_actor.py').resolve()
            process.send_signal(signal.SIGUSR1)
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0 and stderr == ''
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)
    result = json.loads(stdout)
    assert result['exit_code'] == 0
    assert result['forwarded_actor_signals'] == [signal.SIGUSR1]
    assert result['original_summary']['signal'] == signal.SIGUSR1
    assert result['original_summary']['status'] == 'stopped_signal'
    assert len(result['original_summary']['games']) == 0  # No game or training executed.
    assert (Path(result['scratch_retained']) / 'actor/STOP').is_file()
    assert result['actor'] == plan['actors'][0]['actor']


def test_resume_copy_reuses_terminal_owner_without_reopening_games(
        rl, inputs, monkeypatch, tmp_path):
    plan, path = prepared(rl, inputs, actors=1)
    actor = plan['actors'][0]['actor']
    persistent = path.parent / 'actors' / actor
    persistent.mkdir(parents=True, mode=0o700)
    scratch = tmp_path / 'closed-scratch'
    scratch.mkdir()
    (scratch / 'result.json').write_text(json.dumps({'actor': actor, 'run': plan['run'],
                                                    'exit_code': 0}))
    (scratch / 'payload').write_text('retained original bytes')
    (persistent / 'owner.json').write_text(json.dumps({'actor': actor, 'job_id': '42',
        'scratch': str(scratch), 'plan_sha256': rl.hashed(path)}))
    partial = persistent / 'evidence'
    partial.mkdir()
    (partial / 'payload').write_text('interrupted partial copy')
    monkeypatch.setattr(rl.subprocess, 'check_output', lambda *a, **k: '42|RUNNING|\n')
    with pytest.raises(ValueError, match='allocation_must_be_terminal'):
        rl.resume_snapshot(path, actor)
    monkeypatch.setattr(rl.subprocess, 'check_output', lambda *a, **k: '42|FAILED|\n')
    result = rl.resume_snapshot(path, actor)
    assert result['new_games'] == 0 and result['copy_resumed']
    assert (partial / 'payload').read_text() == 'retained original bytes'
    assert json.loads((persistent / 'snapshot.json').read_bytes())['full_readback']


def test_same_run_additional_array_keeps_original_plan_and_single_core_workers(
        rl, inputs, tmp_path):
    _plan, parent = prepared(rl, inputs, actors=12)
    (parent.parent / 'submission.json').write_text(json.dumps({'job_id': '123'}))
    original = parent.read_bytes()
    execution, learner, _ = inputs
    extra = tmp_path.resolve() / 'extra'
    plan = rl.prepare(execution, learner, extra, actors=12)
    with (patch.object(rl, 'require_modules'),
          patch.object(rl.scheduler, 'submission_lock_path', return_value=tmp_path / 'submit.lock'),
          patch.object(rl.scheduler, 'output', side_effect=['123_[0-11]', '0/400/0/400', ''])):
        result = rl.submit(extra / 'plan.json', additional_to=[parent], dry_run=True)
    assert '--array=0-11%12' in result['command'] and '--cpus-per-task=1' in result['command']
    assert result['additional_to'][0]['job_id'] == '123'
    assert parent.read_bytes() == original
    assert not {a['actor'] for a in _plan['actors']} & {a['actor'] for a in plan['actors']}
    assert not (extra / 'submission-intent.json').exists()


@pytest.mark.parametrize('existing,count,error', [
    ('999_[0-11]', 12, 'must_drain'), ('123_[0-23]', 16, 'limit_36')])
def test_additional_array_rejects_unlisted_owner_or_total_over36(
        rl, inputs, tmp_path, existing, count, error):
    _plan, parent = prepared(rl, inputs, actors=24)
    (parent.parent / 'submission.json').write_text(json.dumps({'job_id': '123'}))
    execution, learner, _ = inputs
    extra = tmp_path.resolve() / 'extra'
    rl.prepare(execution, learner, extra, actors=count)
    with (patch.object(rl, 'require_modules'),
          patch.object(rl.scheduler, 'submission_lock_path', return_value=tmp_path / 'submit.lock'),
          patch.object(rl.scheduler, 'output', return_value=existing) as scheduler,
          pytest.raises(ValueError, match=error)):
        rl.submit(extra / 'plan.json', additional_to=[parent], dry_run=True)
    assert scheduler.call_count == 1
    assert not (extra / 'submission-intent.json').exists()


def test_additional_submission_uncertain_outcome_is_not_offered_again(rl, inputs, tmp_path):
    _plan, parent = prepared(rl, inputs, actors=12)
    (parent.parent / 'submission.json').write_text(json.dumps({'job_id': '123'}))
    execution, learner, _ = inputs
    extra = tmp_path.resolve() / 'extra'
    rl.prepare(execution, learner, extra, actors=12)
    commands = []

    def scheduler(command):
        if command[0] == 'sbatch':
            commands.append(command)
            raise RuntimeError('injected_unknown_scheduler_response')
        if command[0] == 'sinfo':
            return '0/400/0/400'
        return '123_[0-11]' if '-n' in command else ''

    with (patch.object(rl, 'require_modules'),
          patch.object(rl.scheduler, 'submission_lock_path', return_value=tmp_path / 'submit.lock'),
          patch.object(rl.scheduler, 'output', side_effect=scheduler)):
        with pytest.raises(RuntimeError, match='unknown_scheduler'):
            rl.submit(extra / 'plan.json', additional_to=[parent])
        assert (extra / 'submission-intent.json').is_file()
        with pytest.raises(ValueError, match='reconciliation_no_retry'):
            rl.submit(extra / 'plan.json', additional_to=[parent])
        with pytest.raises(ValueError, match='reconciliation_no_retry'):
            rl.submit(extra / 'plan.json')  # changing entry mode cannot re-offer an unknown submit
    assert len(commands) == 1


def test_submit_explicit_memory_and_excluded_node_use_normal_scheduler_argv(rl, inputs, tmp_path):
    _plan, file = prepared(rl, inputs)
    lock = tmp_path.resolve() / 'settings/submit.lock'
    with (patch.object(rl, 'require_modules'),
          patch.object(rl.scheduler, 'submission_lock_path', return_value=lock),
          patch.object(rl.scheduler, 'output', side_effect=['', '0/400/0/400', ''])):
        result = rl.submit(file, dry_run=True, memory_gb=6, exclude_nodes=['smp-9-0'])
    assert '--mem=6G' in result['command'] and '--mem=4G' not in result['command']
    assert '--exclude=smp-9-0' in result['command']
    assert result['excluded_nodes'] == ['smp-9-0']
    assert result['memory_gb_per_actor'] == 6
    with pytest.raises(ValueError, match='positive_memory'):
        rl.submit(file, dry_run=True, memory_gb=True)
