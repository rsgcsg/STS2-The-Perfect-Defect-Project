"""The recipe connects original owners; scheduler and R2 are replaced, no job/game starts."""
from __future__ import annotations

import json
import subprocess
import sys
from contextlib import nullcontext
from unittest.mock import patch

import pytest
from test_friday_watch import FRIDAY, module, watch  # noqa: F401 -- shared original Friday fixtures


@pytest.fixture
def recipe(watch):  # noqa: F811 -- pytest resolves the imported original fixture
    with patch.dict(sys.modules, {'evaluate': watch.evaluate, 'submit': watch.scheduler,
                                 'batch': module('recipe_batch', FRIDAY / 'batch.py')}):
        rl = module('recipe_rl', FRIDAY / 'rl.py')
        with patch.dict(sys.modules, {'rl': rl, 'watch': watch}):
            return module('recipe', FRIDAY / 'recipe.py')


@pytest.fixture
def inputs(recipe, tmp_path, monkeypatch):
    from stpd.evaluation.suites import rl3_learning_curve_manifest

    source = tmp_path / 'release/source'
    source.mkdir(parents=True)
    for name in recipe.SOURCE_FILES:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# frozen source fixture\n')
    environment = tmp_path / 'independent-cuda'
    (environment / 'bin').mkdir(parents=True)
    (environment / 'bin/python').write_text('# interpreter fixture, never executed\n')
    peer = {'kind': 's3', 'bucket': 'test', 'endpoint': 'https://example.invalid',
            'prefix': 'test', 'region': 'auto'}
    base = {'schema': 'spireagent/managed-evaluation-execution-v1', 'store': 's3', 'peer': peer,
            'candidate_directory': str(tmp_path), 'retention': recipe.rl.RETENTION}
    execution = tmp_path / 'execution.json'
    execution.write_text(json.dumps(base))
    evaluation = tmp_path / 'evaluation.json'
    evaluation.write_text(json.dumps({**base, 'retention': recipe.evaluate.RETENTION}))
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(rl3_learning_curve_manifest('v0.111.0')))
    value = {'schema': recipe.SCHEMA, 'approved': False, 'wall_seconds': 3600,
        'learner': {'schema': 'stpd/rl-learner-config-1', 'run': 'new-recipe', 'peer': peer,
                    'device': 'cuda', 'start_artifact_id': 'a' * 64,
                    'start_model_sha256': 'b' * 64},
        'execution': str(execution), 'runtime_source': str(source),
        'cuda_environment': str(environment), 'gpu': 'a30', 'actors': 12,
        'actor_budget': {'episode_seconds': 900, 'games_per_ledger': 2, 'max_games': 10000},
        'seed_namespace': 'recipe-training-only',
        'evaluation_execution': str(evaluation), 'evaluation_manifest': str(manifest),
        'evaluation_tiers': [{'name': 'quick', 'seconds': 14400, 'seed_prefix': 20},
                             {'name': 'milestone', 'seconds': 28800, 'seed_prefix': 100}]}
    configuration = tmp_path / 'recipe.json'
    configuration.write_text(json.dumps(value))

    def git(arguments, **options):
        assert arguments[0] == 'git'
        if arguments[1] == 'status':
            return b''
        if arguments[1] == 'rev-parse':
            return 'a' * 40 + '\n'
        assert arguments[1] == 'show'
        return (source / arguments[2].split(':', 1)[1]).read_bytes()

    monkeypatch.setattr(recipe.subprocess, 'check_output', git)
    return configuration, value, tmp_path / 'prepared'


def freeze(recipe, inputs, *, approved=True):
    configuration, value, root = inputs
    configuration.write_text(json.dumps({**value, 'approved': approved}))
    plan = recipe.prepare(configuration, root)
    return plan, root


def test_prepare_freezes_original_actor_plan_and_shared_environment_prefix_without_submitting(
        recipe, inputs):
    with (patch.object(recipe, 'Exchange', side_effect=AssertionError('prepare must be local')),
          patch.object(recipe.scheduler, 'output', side_effect=AssertionError('no scheduler'))):
        plan, root = freeze(recipe, inputs, approved=False)
    learner = recipe.read_pinned(plan['learner'])
    assert learner['performance'] is None and learner['background_io'] is False
    actors = recipe.read_pinned(plan['actors'])
    assert len(actors['actors']) == 12 and actors['games_per_ledger'] == 2
    first = recipe.read_pinned(actors['actors'][0])
    assert first['behavior'] == {
        'mode': 'temperature_uniform', 'temperature': 1, 'uniform_mix': 0.03}
    assert plan['source_revision'] == 'a' * 40
    for name in ('start.sh', 'learner.sh', 'watch.sh'):
        subprocess.run(['/bin/bash', '-n', str(root / name)], check=True)
        assert 'source "$credentials"' in (root / name).read_text()
    learner_script = (root / 'learner.sh').read_text()
    assert inputs[1]['cuda_environment'] + '/bin/python' in learner_script
    assert 'export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4' in learner_script
    assert 'OMP_NUM_THREADS=1' in (root / 'watch.sh').read_text()
    assert 'OMP_NUM_THREADS=4' not in (root / 'watch.sh').read_text()
    assert (root / 'watch.sh').read_text().splitlines()[-1].startswith(
        'exec ' + inputs[1]['cuda_environment'] + '/bin/python ')
    with pytest.raises(ValueError, match='explicit_approved_recipe'):
        recipe.start(root / 'plan.json')


def test_missing_wall_and_CPU_or_linked_environment_are_rejected(recipe, inputs, tmp_path):
    configuration, value, root = inputs
    configuration.write_text(json.dumps({**value, 'wall_seconds': None}))
    with pytest.raises(ValueError, match='explicit_finite_wall'):
        recipe.prepare(configuration, root)
    assert not root.exists()
    linked = tmp_path / 'linked-cuda'
    linked.symlink_to(value['cuda_environment'], target_is_directory=True)
    configuration.write_text(json.dumps({**value, 'cuda_environment': str(linked)}))
    with pytest.raises(ValueError, match='canonical_physical_path'):
        recipe.load_recipe(configuration)


class ExchangeFacts:
    def __init__(self, submitted, *, ready=True):
        self.submitted, self.ready, self.stops = submitted, ready, []

    def stop_request(self):
        return None

    def latest_model(self):
        return ({'version': 0, 'published_at': 12345, 'model_artifact_id': 'a' * 64}
                if self.submitted and self.ready else None)

    def latest_checkpoint(self):
        return None

    def shard_keys(self):
        return []

    def request_stop(self, reason):
        self.stops.append(reason)


def start_fixtures(recipe, monkeypatch, *, unknown=False, busy=False, ready=True):
    submitted, calls = [], []
    exchange = ExchangeFacts(submitted, ready=ready)
    monkeypatch.setattr(recipe, 'Exchange', lambda *_: exchange)
    monkeypatch.setattr(recipe.scheduler, 'user_submission_lock', nullcontext)
    monkeypatch.setattr(recipe.scheduler, 'username', lambda: 'fixture-user')

    def output(command):
        calls.append(command)
        if command[0] == 'squeue':
            return 'existing-gpu' if busy else ''
        assert command[0] == 'sbatch'
        if '--partition=smp_gpu' in command:
            submitted.append('learner')
            if unknown:
                raise OSError('lost sbatch response')
            return '101'
        assert submitted == ['learner', 'actors']
        return '303'

    def actors(path):
        assert submitted == ['learner'] and exchange.latest_model()['version'] == 0
        submitted.append('actors')
        recipe.save(path.parent / 'submission.json', {'job_id': '202'})
        return {'job_id': '202'}

    monkeypatch.setattr(recipe.scheduler, 'output', output)
    monkeypatch.setattr(recipe.rl, 'submit', actors)
    return exchange, submitted, calls


@pytest.mark.parametrize('poll_seconds', [60, 900])
def test_start_submits_one_GPU_then_original_actor_and_watcher_and_never_restarts(
        recipe, inputs, monkeypatch, poll_seconds):
    inputs[1]['watch_poll_seconds'] = poll_seconds
    inputs[0].write_text(json.dumps(inputs[1]))
    _plan, root = freeze(recipe, inputs)
    exchange, submitted, calls = start_fixtures(recipe, monkeypatch)
    result = recipe.start(root / 'plan.json')
    assert result['learner']['job_id'] == '101' and result['actors']['job_id'] == '202'
    assert result['watcher']['job_id'] == '303' and result['started_at'] == 12345
    gpu = next(call for call in calls if '--partition=smp_gpu' in call)
    assert '--gres=gpu:a30:1' in gpu and '--time=70' in gpu
    watcher = json.loads((root / 'watch.json').read_bytes())
    assert watcher['started_at'] == 12345 and watcher['actor_job_id'] == '202'
    assert watcher['evaluation_tiers'] == inputs[1]['evaluation_tiers']
    assert watcher['poll_seconds'] == poll_seconds
    assert not exchange.stops and submitted == ['learner', 'actors']
    with pytest.raises(ValueError, match='original_owner_reconciliation'):
        recipe.start(root / 'plan.json')
    assert len([call for call in calls if call[0] == 'sbatch']) == 2


def test_unknown_GPU_submission_preserves_intent_and_does_not_reoffer_or_claim_failure(
        recipe, inputs, monkeypatch):
    _plan, root = freeze(recipe, inputs)
    exchange, submitted, calls = start_fixtures(recipe, monkeypatch, unknown=True)
    with pytest.raises(OSError):
        recipe.start(root / 'plan.json')
    assert (root / 'learner-submit-intent.json').is_file()
    assert (root / 'learner-submit-outcome-unknown.json').is_file()
    assert not (root / 'learner-submit-receipt.json').exists()
    assert exchange.stops == ['recipe_start_failed_new_run']
    assert (root / 'learner/STOP').is_file() and (root / 'actors/STOP').is_file()
    assert submitted == ['learner'] and len(calls) == 2
    with pytest.raises(ValueError, match='original_owner_reconciliation'):
        recipe.start(root / 'plan.json')


def test_existing_GPU_job_blocks_submission_without_touching_that_job(recipe, inputs, monkeypatch):
    _plan, root = freeze(recipe, inputs)
    exchange, submitted, calls = start_fixtures(recipe, monkeypatch, busy=True)
    with pytest.raises(ValueError, match='another_Friday_GPU_job'):
        recipe.start(root / 'plan.json')
    assert not submitted and len(calls) == 1 and calls[0][0] == 'squeue'
    assert exchange.stops == ['recipe_start_failed_new_run']
    assert (root / 'actors/STOP').exists() and (root / 'learner/STOP').exists()


def test_version_zero_wait_is_bounded_to120_seconds_and_does_not_submit_actors(
        recipe, inputs, monkeypatch):
    _plan, root = freeze(recipe, inputs)
    exchange, submitted, _calls = start_fixtures(recipe, monkeypatch, ready=False)
    now = [0.0]
    monkeypatch.setattr(recipe.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(recipe.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    with pytest.raises(ValueError, match='not_observed_within120seconds'):
        recipe.start(root / 'plan.json')
    assert now[0] == 120 and submitted == ['learner']
    assert exchange.stops == ['recipe_start_failed_new_run']


def test_existing_run_is_never_stopped_by_new_recipe_failure(recipe, inputs, monkeypatch):
    _plan, root = freeze(recipe, inputs)
    exchange = ExchangeFacts(['previous-run'])
    monkeypatch.setattr(recipe, 'Exchange', lambda *_: exchange)
    with pytest.raises(ValueError, match='new_empty_RL_run'):
        recipe.start(root / 'plan.json')
    assert not exchange.stops and not (root / 'start-intent.json').exists()
    assert not (root / 'actors/STOP').exists()


def test_modified_launcher_is_rejected_before_any_exchange_or_scheduler_call(recipe, inputs):
    _plan, root = freeze(recipe, inputs)
    (root / 'learner.sh').write_text('# altered launcher\n')
    with (patch.object(recipe, 'Exchange', side_effect=AssertionError('no network')),
          pytest.raises(ValueError, match='frozen_recipe_launcher_changed')):
        recipe.start(root / 'plan.json')
