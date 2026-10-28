"""Friday RL resource boundary tests; no RL1 private source, game or learner execution."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
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
    learner.write_text(json.dumps({'schema': 'stpd/rl-run-1', 'run': 'rl1-friday-smoke',
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
    assert all(config['games_per_ledger'] == 1 for config in configs)
    assert all(config['unknown_stop'] == {'consecutive': 1, 'fraction': 0, 'after': 1}
               for config in configs)
    assert plan['cross_actor_immediate_unknown_stop'] is False
    assert plan['automatic_mac_fallback'] is False
    assert rl.load_plan(file)['run'] == 'rl1-friday-smoke'


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


def test_submit_reuses_F2_gate_and_fc430_single_core_array(rl, inputs, tmp_path):
    _plan, file = prepared(rl, inputs)
    lock = tmp_path.resolve() / 'settings/submit.lock'
    with (patch.object(rl, 'require_modules'),
          patch.object(rl.scheduler, 'submission_lock_path', return_value=lock),
          patch.object(rl.scheduler, 'output', side_effect=['', '0/400/0/400', ''])):
        result = rl.submit(file, dry_run=True)
    assert '--array=0-7%8' in result['command']
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
        return SimpleNamespace(returncode=0)

    original_snapshot = rl.snapshot
    original_run = subprocess.run

    def checked_snapshot(source, target):
        assert (file.parent / 'STOP').is_file()
        with patch.object(rl.subprocess, 'run', original_run):
            return original_snapshot(source, target)

    monkeypatch.setattr(rl.subprocess, 'run', actor)
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
