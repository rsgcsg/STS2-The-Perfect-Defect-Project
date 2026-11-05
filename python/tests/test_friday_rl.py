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
from test_runtime_install import bundled_release as bundled_release

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
        'retention': {'tier': 'trajectory', 'purpose': 'training', 'audit_every': 20}}),
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


@pytest.mark.parametrize("actors,audit_every", [(56, 20), (128, 100)])
def test_explicit_scale_preserves_declared_training_audit_interval(rl, inputs, actors, audit_every):
    execution, _, _ = inputs
    configuration = json.loads(execution.read_bytes())
    configuration['retention']['audit_every'] = audit_every
    execution.write_text(json.dumps(configuration))
    plan, _ = prepared(rl, inputs, actors=actors)
    assert len({ref['actor'] for ref in plan['actors']}) == actors
    actor = json.loads(Path(plan['actors'][0]['path']).read_bytes())
    frozen = json.loads(Path(actor['execution']).read_bytes())
    assert frozen['retention']['audit_every'] == audit_every


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


def test_actor_runtime_materializes_full_pinned_bundle_and_rechecks_cache(
        rl, tmp_path, bundled_release):
    from spireagent.package_identity import PackageIdentityError

    _, package, pin = bundled_release
    execution = tmp_path.resolve()/'execution-runtime.json'
    original = {'runtime_directory': str(package/'dist'), 'runtime_pin': pin,
                'peer': {'kind': 'local', 'directory': str(tmp_path/'store')}}
    execution.write_text(json.dumps(original))
    actor = tmp_path.resolve()/'runtime-actor-source.json'
    configuration = {'actor': 'a', 'execution': str(execution), 'run': 'bounded-test'}
    actor.write_text(json.dumps(configuration))
    reference = {'path': str(actor), 'sha256': rl.hashed(actor)}
    source_bytes = actor.read_bytes(), execution.read_bytes()
    cache = tmp_path.resolve()/'node-cache'
    for number in range(2):
        scratch = tmp_path.resolve()/f'worker-{number}'
        scratch.mkdir()
        derived, receipt = rl.materialize_actor_runtime(reference, scratch, cache)
        actual = json.loads(derived.read_bytes())
        actual_execution = json.loads(Path(actual['execution']).read_bytes())
        assert {k: v for k, v in actual.items() if k != 'execution'} == {
            k: v for k, v in configuration.items() if k != 'execution'}
        assert {k: v for k, v in actual_execution.items() if k != 'runtime_directory'} == {
            k: v for k, v in original.items() if k != 'runtime_directory'}
        assert receipt['cache_hit'] is bool(number)
        assert (receipt['verified_package']['package_content_sha256']
                == pin['package_content_sha256'])
        assert receipt['derived_actor_sha256'] == rl.hashed(derived)
        assert receipt['World_calls_during_materialization'] == 0
    assert (actor.read_bytes(), execution.read_bytes()) == source_bytes
    target = Path(actual_execution['runtime_directory']).parent
    assert (target/'node_modules/zod/package.json').is_file()
    (target/'dist/cli.js').write_text('modified cache')
    scratch = tmp_path.resolve()/'worker-bad'
    scratch.mkdir()
    with pytest.raises(PackageIdentityError, match='content differs'):
        rl.materialize_actor_runtime(reference, scratch, cache)
    assert not (scratch/'runtime-actor.json').exists()


@pytest.mark.parametrize('disposition', [None, 'unknown', 'save_failed'])
def test_normal_worker_passes_materialized_configuration_before_any_actor_offer(
        rl, inputs, tmp_path, bundled_release, host_startup_release, monkeypatch, disposition):
    _, package, pin = bundled_release
    execution, _, _ = inputs
    base = json.loads(execution.read_bytes())
    base.update(runtime_directory=str(package/'dist'), runtime_pin=pin,
                **host_startup_release)
    execution.write_text(json.dumps(base))
    plan, path = prepared(rl, inputs, actors=1)
    inference = {'schema': 'synthetic-inference'}
    attachment = path.parent/'inference.json'
    attachment.write_text(json.dumps(inference))
    plan.update(trajectory_encoding='short-segment-1',
        inference={'path': str(attachment), 'sha256': rl.hashed(attachment)})
    ref = plan['actors'][0]
    actor_path = Path(ref['path'])
    actor = json.loads(actor_path.read_bytes())
    actor['segments'] = {'inference': inference}
    actor_path.write_text(json.dumps(actor))
    ref['sha256'] = rl.hashed(actor_path)
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(rl, 'require_modules', lambda _: None)
    monkeypatch.setattr(rl.sys, 'platform', 'linux')
    monkeypatch.setenv('SLURM_JOB_ID', 'synthetic')
    monkeypatch.setenv('SLURM_CPUS_PER_TASK', '1')
    monkeypatch.setenv('SLURM_ARRAY_TASK_ID', '0')
    materialize = rl.materialize_actor_runtime
    monkeypatch.setattr(rl, 'materialize_actor_runtime',
        lambda reference, scratch, _cache: materialize(
            reference, scratch, tmp_path.resolve()/'isolated-cache'))
    materialize_host = rl.materialize_actor_host
    monkeypatch.setattr(rl, 'materialize_actor_host',
        lambda reference, scratch, _cache: materialize_host(
            reference, scratch, tmp_path.resolve()/'isolated-host-cache'))
    observed = []
    real_popen = rl.subprocess.Popen

    def start(command, **options):
        if 'spireagent.workbench.rl_actor' not in command:
            return real_popen(command, **options)
        actual = Path(command[command.index('--config')+1])
        assert actual != actor_path
        private = json.loads(actual.read_bytes())
        local = json.loads(Path(private['execution']).read_bytes())
        assert local['runtime_directory'] != str(package/'dist')
        assert local['host_directory'] != base['host_directory']
        assert local['candidate_directory'] != base['candidate_directory']
        assert options['env']['DOTNET'].startswith(str(tmp_path/'isolated-host-cache'))
        assert options['env']['STS2_GAME_DIR'] != os.environ['STS2_GAME_DIR']
        assert options['env']['DOTNET_NOLOGO'] == '1'
        observed.append(actual)
        work = Path(command[command.index('--work')+1])
        (work/'actor-summary.json').write_text(json.dumps({
            'schema': 'spireagent/rl-actor-summary-1', 'actor': ref['actor'],
            'run': plan['run'], 'status': 'stopped_before_offer',
            'games': [] if disposition is None else [{'status': disposition, 'shard': None}]}))
        return SimpleNamespace(wait=lambda: 0, poll=lambda: 0)

    monkeypatch.setattr(rl.subprocess, 'Popen', start)
    result = rl.run(path)
    assert len(observed) == 1
    assert not (path.parent/'STOP').exists()
    assert result['runtime_materialization']['verified_package']['package_content_sha256'] == pin[
        'package_content_sha256']
    assert result['actor_config_sha256'] == ref['sha256']
    assert result['host_materialization']['candidate_audit_cached'] is False


@pytest.fixture
def host_startup_release(tmp_path, monkeypatch):
    from test_local_environment import fixture

    host_fixture = tmp_path/'host-fixture'
    host_fixture.mkdir()
    _config, host, candidate, pin, _audit, _checked = fixture(host_fixture)
    (candidate/'source.txt').write_text('synthetic stopped candidate; no Native claim')
    subprocess.run(['git', 'init', '--quiet', str(candidate)], check=True)
    subprocess.run(['git', 'add', 'source.txt'], cwd=candidate, check=True)
    subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '--quiet', '-m', 'synthetic stopped fixture'],
                   cwd=candidate, check=True)
    native = tmp_path/'native-libraries'
    native.mkdir()
    (native/'sts2.dll').write_bytes(b'synthetic game input')
    (candidate/'lib').symlink_to(native, target_is_directory=True)
    game = tmp_path/'game'
    game.mkdir()
    (game/'release_info.json').write_text('{}')
    sdk = tmp_path/'dotnet-sdk'
    sdk.mkdir()
    (sdk/'dotnet').write_text('#!/bin/sh\nexit 0\n')
    (sdk/'dotnet').chmod(0o755)
    monkeypatch.setenv('STS2_GAME_DIR', str(game))
    monkeypatch.setenv('DOTNET', str(sdk/'dotnet'))
    return {'host_directory': str(host), 'host_pin': pin,
            'candidate_directory': str(candidate), 'game_assembly_sha256': 'a'*64}


def test_Host_startup_copy_is_complete_concurrent_and_immutable(
        rl, host_startup_release, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    execution = tmp_path/'execution-host.json'
    execution.write_text(json.dumps(host_startup_release))
    actor = tmp_path/'actor-host.json'
    actor.write_text(json.dumps({'execution': str(execution), 'actor': 'test'}))
    ref = {'path': str(actor), 'sha256': rl.hashed(actor)}
    cache = tmp_path/'host-cache'

    def materialize(number):
        scratch = tmp_path/f'host-worker-{number}'
        scratch.mkdir()
        return rl.materialize_actor_host(ref, scratch, cache)

    before = execution.read_bytes(), actor.read_bytes()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(materialize, (0, 1)))
    assert sorted(item[1]['cache_hit'] for item in results) == [False, True]
    derived, receipt, environment = results[-1]
    configuration = json.loads(Path(json.loads(derived.read_bytes())['execution']).read_bytes())
    candidate = Path(configuration['candidate_directory'])
    assert not (candidate/'lib').is_symlink()
    assert (candidate/'lib/sts2.dll').read_bytes() == b'synthetic game input'
    assert (candidate/'.git/HEAD').read_bytes() == (
        Path(host_startup_release['candidate_directory'])/'.git/HEAD').read_bytes()
    assert receipt['World_calls_during_materialization'] == 0
    assert receipt['candidate_audit_cached'] is False
    assert Path(environment['DOTNET']).is_file()
    assert (execution.read_bytes(), actor.read_bytes()) == before
    index_before = (candidate/'.git/index').read_bytes()
    subprocess.run(['git', 'update-index', '--refresh'], cwd=candidate, check=True)
    assert (candidate/'.git/index').read_bytes() != index_before
    assert materialize(2)[1]['cache_hit']  # Legitimate Git stat-cache refresh is allowed.
    subprocess.run(['git', 'update-index', '--assume-unchanged', 'source.txt'],
                   cwd=candidate, check=True)
    with pytest.raises(ValueError, match='Host_startup_cache_changed'):
        materialize(3)
    subprocess.run(['git', 'update-index', '--no-assume-unchanged', 'source.txt'],
                   cwd=candidate, check=True)
    (candidate/'lib/sts2.dll').write_bytes(b'changed cached input')
    with pytest.raises(ValueError, match='Host_startup_cache_changed'):
        materialize(4)


def test_submit_reuses_F2_gate_and_fc430_single_core_array(rl, inputs, tmp_path):
    _plan, file = prepared(rl, inputs)
    lock = tmp_path.resolve() / 'settings/submit.lock'
    with (patch.object(rl, 'require_modules'),
          patch.object(rl.scheduler, 'submission_lock_path', return_value=lock),
          patch.object(rl.scheduler, 'output', side_effect=['', '0/400/0/400', '', 'fc430|0/400/0/400'])):
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
        return SimpleNamespace(wait=lambda: 0, poll=lambda: 0)

    original_snapshot = rl.snapshot
    original_popen = subprocess.Popen
    original_run = subprocess.run

    def signal_after_copy(command, *args, **kwargs):
        result = original_run(command, *args, **kwargs)
        if command[0] == 'rsync':
            # This is the actual late-signal failure: a new STOP after copying
            # must not mutate the closed source before its byte readback.
            signal.raise_signal(signal.SIGUSR1)
        return result

    def checked_snapshot(source, target):
        assert (file.parent / 'STOP').is_file()
        assert callable(signal.getsignal(signal.SIGUSR1))
        with (patch.object(rl.subprocess, 'Popen', original_popen),
              patch.object(rl.subprocess, 'run', signal_after_copy)):
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
    snapshot_ready = tmp_path / 'snapshot-ready'
    snapshot_release = tmp_path / 'snapshot-release'
    (file.parent / 'submission.json').write_text(json.dumps({'job_id': '123'}))
    execution, learner, _ = inputs
    next_peer = {'kind': 'local', 'directory': str(tmp_path / 'hot-store'),
                 'archive': plan['peer']}
    frozen_execution = json.loads(execution.read_bytes())
    frozen_execution.update(peer=next_peer, store=next_peer['directory'])
    next_execution, next_learner = tmp_path / 'next-execution.json', tmp_path / 'next-learner.json'
    next_execution.write_text(json.dumps(frozen_execution))
    next_learner.write_text(json.dumps({**json.loads(learner.read_bytes()), 'peer': next_peer}))
    next_plan = tmp_path / 'next-actors'
    next_source = tmp_path / 'next-source'
    next_source.mkdir()
    rl.prepare(next_execution, next_learner, next_plan, actors=32, runtime_source=next_source)
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
        import json, os, sys, time
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        import rl
        rl.require_modules = lambda _: None  # This test replaces only the Actor process boundary.
        rl.sys.platform = 'linux'
        original_snapshot = rl.snapshot
        def paused_snapshot(source, target):
            Path(os.environ['TEST_SNAPSHOT_READY']).write_text('actor wait() completed')
            deadline = time.monotonic() + 20
            while not Path(os.environ['TEST_SNAPSHOT_RELEASE']).exists():
                assert time.monotonic() < deadline, 'test did not release evidence snapshot'
                time.sleep(.02)
            return original_snapshot(source, target)
        rl.snapshot = paused_snapshot
        print(json.dumps(rl.run(Path(sys.argv[2]))), flush=True)
        ''')
    command = [sys.executable, '-c', driver, str(FRIDAY), str(file)]
    environment = {'PATH': os.defpath, 'PYTHONPATH': str(stubs), 'TMPDIR': str(tmp_path),
                   'SLURM_JOB_ID': 'signal-stub', 'SLURM_CPUS_PER_TASK': '1',
                   'SLURM_ARRAY_TASK_ID': '0', 'TEST_READY': str(ready),
                   'TEST_SNAPSHOT_READY': str(snapshot_ready),
                   'TEST_SNAPSHOT_RELEASE': str(snapshot_release)}
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
            with (patch.object(rl, 'require_modules'),
                  pytest.raises(ValueError, match='additional_actor_run_peer_source')):
                rl.submit(next_plan / 'plan.json', additional_to=[file], dry_run=True)
            process.send_signal(signal.SIGUSR1)
            deadline = time.monotonic() + 15
            while (not snapshot_ready.exists() and process.poll() is None
                   and time.monotonic() < deadline):
                time.sleep(.02)
            assert snapshot_ready.is_file() and process.poll() is None
            with pytest.raises(ProcessLookupError):
                os.kill(origin['pid'], 0)  # Original child is reaped, parent copy still pending.
            with (patch.object(rl, 'require_modules'),
                  patch.object(rl.scheduler, 'submission_lock_path',
                               return_value=tmp_path / 'submit.lock'),
                  patch.object(rl.scheduler, 'output',
                               side_effect=['123_[0-0]', '0/400/0/400', '', 'fc430|0/400/0/400'])):
                submitted = rl.submit(next_plan / 'plan.json', additional_to=[file], dry_run=True)
            assert submitted['cores'] == 32
            previous = submitted['additional_to'][0]
            assert previous['preservation_only'] is True
            assert previous['finished_actor_results'][0]['actor'] == plan['actors'][0]['actor']
            prior_snapshot = file.parent / 'actors' / plan['actors'][0]['actor'] / 'snapshot.json'
            assert not prior_snapshot.exists()
            snapshot_release.touch()
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


@pytest.mark.parametrize('problem', ['ledger_cleanup_unconfirmed', 'wrong_configuration'])
def test_preservation_handoff_does_not_waive_unknown_cleanup_or_foreign_result(
        rl, inputs, tmp_path, problem):
    plan, path = prepared(rl, inputs, actors=1)
    ref = plan['actors'][0]
    persistent = path.parent / 'actors' / ref['actor']
    persistent.mkdir(parents=True)
    scratch = tmp_path.resolve() / 'closed-result'
    scratch.mkdir()
    (persistent / 'owner.json').write_text(json.dumps({'actor': ref['actor'], 'job_id': '42',
        'scratch': str(scratch), 'plan_sha256': rl.hashed(path)}))
    result = {'schema': 'spireagent/friday-RL-result-v1', 'actor': ref['actor'],
        'run': plan['run'], 'actor_config_sha256': ref['sha256'],
        'scratch_retained': str(scratch), 'exit_code': 0, 'error_type': None,
        'original_summary': {'schema': 'spireagent/rl-actor-summary-1',
            'actor': ref['actor'], 'run': plan['run'], 'status': 'stopped_signal'}}
    if problem == 'ledger_cleanup_unconfirmed':
        result['original_summary']['status'] = 'stopped_ledger:host_cleanup_unconfirmed'
    else:
        result['actor_config_sha256'] = 'f' * 64
    (scratch / 'result.json').write_text(json.dumps(result))
    if problem == 'ledger_cleanup_unconfirmed':
        assert rl.finished_actor_results(path, plan) is None
    else:
        with pytest.raises(ValueError, match='result_binding_changed'):
            rl.finished_actor_results(path, plan)


def test_same_run_additional_array_keeps_original_plan_and_single_core_workers(
        rl, inputs, tmp_path):
    _plan, parent = prepared(rl, inputs, actors=32)
    (parent.parent / 'submission.json').write_text(json.dumps({'job_id': '123'}))
    original = parent.read_bytes()
    execution, learner, _ = inputs
    extra = tmp_path.resolve() / 'extra'
    plan = rl.prepare(execution, learner, extra, actors=24)
    with (patch.object(rl, 'require_modules'),
          patch.object(rl.scheduler, 'submission_lock_path', return_value=tmp_path / 'submit.lock'),
          patch.object(rl.scheduler, 'output', side_effect=['123_[0-31]', '0/400/0/400', '', 'fc430|0/400/0/400'])):
        result = rl.submit(extra / 'plan.json', additional_to=[parent], dry_run=True)
    assert '--array=0-23%24' in result['command'] and '--cpus-per-task=1' in result['command']
    assert result['additional_to'][0]['job_id'] == '123'
    assert parent.read_bytes() == original
    assert not {a['actor'] for a in _plan['actors']} & {a['actor'] for a in plan['actors']}
    assert not (extra / 'submission-intent.json').exists()


@pytest.mark.parametrize('existing,count,error', [
    ('999_[0-11]', 12, 'must_drain')])
def test_additional_array_rejects_unlisted_owner(
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
            return 'fc430|0/400/0/400' if '%f' in str(command) else '0/400/0/400'
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
          patch.object(rl.scheduler, 'output', side_effect=['', '0/400/0/400', '', 'fc430|0/400/0/400'])):
        result = rl.submit(file, dry_run=True, memory_gb=6, exclude_nodes=['smp-9-0'])
    assert '--mem=6G' in result['command'] and '--mem=4G' not in result['command']
    assert '--exclude=smp-9-0' in result['command']
    assert result['excluded_nodes'] == ['smp-9-0']
    assert result['memory_gb_per_actor'] == 6
    with pytest.raises(ValueError, match='positive_memory'):
        rl.submit(file, dry_run=True, memory_gb=True)


@pytest.mark.parametrize('count', [40, 256, 500])
def test_segment_prepare_binds_scorer_and_avoids_per_game_900_second_cut(
        rl, inputs, tmp_path, count):
    from stpd.p1.export import adapter_identity
    from stpd.p1.local_inference import SEGMENT_SCHEMA

    execution, learner, output = inputs
    learning = json.loads(learner.read_bytes())
    learning['segments'] = {'steps': 64, 'excluded_seeds': ['frozen-evaluation-seed']}
    learning['actor_defaults']['behavior']['uniform_mix'] = 0
    learner.write_text(json.dumps(learning))
    attachment = learner.parent/'inference.json'
    attachment.write_text(json.dumps({'schema': SEGMENT_SCHEMA, 'adapter': adapter_identity(),
        'pool_id': 'fixture', 'address': ['127.0.0.1', 1234], 'segment_steps': 64}))
    plan = rl.prepare(execution, learner, output, actors=count, wall_seconds=4500,
        episode_seconds=14400, games_per_ledger=10, segment_inference=attachment)
    assert plan['trajectory_encoding'] == 'short-segment-1'
    actor = json.loads(Path(plan['actors'][0]['path']).read_bytes())
    assert actor['segments']['inference'] == json.loads(attachment.read_bytes())
    assert actor['segments']['excluded_seeds'] == ['frozen-evaluation-seed']
    assert actor['segments']['rendezvous'] == str(attachment)
    assert actor['episode_seconds'] == 14400 and actor['behavior']['uniform_mix'] == 0
    assert rl.load_plan(output/'plan.json')['inference']['sha256'] == rl.hashed(attachment)
    with patch.object(rl, 'read_pinned', wraps=rl.read_pinned) as pinned:
        rl.load_plan(output/'plan.json', actor_index=count - 1)
    assert pinned.call_count == 3  # execution, shared inference, selected actor; independent of N
    assert [call.args[0]['path'] for call in pinned.call_args_list
            if call.args[0]['path'].startswith(str(output/'friday-'))] == [
                plan['actors'][-1]['path']]
    first_actor = Path(plan['actors'][0]['path'])
    original = first_actor.read_bytes()
    first_actor.write_bytes(b'{}')
    rl.load_plan(output/'plan.json', actor_index=count - 1)
    with pytest.raises(ValueError, match='pinned_input_changed'):
        rl.load_plan(output/'plan.json', actor_index=0)
    with pytest.raises(ValueError, match='pinned_input_changed'):
        rl.load_plan(output/'plan.json')  # supervisor still checks the entire plan
    first_actor.write_bytes(original)
    with pytest.raises(ValueError, match='frozen_actor_array_index'):
        rl.load_plan(output/'plan.json', actor_index=count)
    def scheduler(command):
        if command[0] == 'sinfo':
            return 'fc430|0/654/0/654' if '%f|%C' in command else '0/654/0/654'
        return ''

    with (patch.object(rl, 'require_modules'),
          patch.object(rl.scheduler, 'submission_lock_path', return_value=tmp_path/'submit.lock'),
          patch.object(rl.scheduler, 'output', side_effect=scheduler)):
        submitted = rl.submit(output/'plan.json', dry_run=True)
    assert submitted['cores'] == count
    assert f'--array=0-{count - 1}%{count}' in submitted['command']
    (output/'STOP').touch()
    with (patch.object(rl.sys, 'argv', ['rl.py', 'run', '--plan', str(output/'plan.json')]),
          patch.object(rl.sys, 'platform', 'linux'),
          patch.dict(rl.os.environ, {'SLURM_JOB_ID': 'synthetic', 'SLURM_CPUS_PER_TASK': '1',
                                    'SLURM_ARRAY_TASK_ID': str(count - 1)}),
          patch.object(rl, 'require_modules'),
          patch.object(rl, 'read_pinned', wraps=rl.read_pinned) as pinned):
        rl.main()  # actual CLI gate -> actual run -> original before-offer Stop
    assert pinned.call_count == 6  # both CLI gate and child verify only this actor
    (output/'STOP').unlink()
    original_attachment = actor['segments']['inference']
    attachment.write_text(json.dumps({**original_attachment, 'pool_id': 'replacement',
                                     'address': ['127.0.0.1', 5678]}))
    replacement = output.with_name('replacement-plan')
    new_plan = rl.prepare(execution, learner, replacement, actors=1, wall_seconds=4500,
        episode_seconds=14400, games_per_ledger=10, segment_inference=attachment)
    assert rl.read_pinned(rl.load_plan(output/'plan.json')['inference']) == original_attachment
    assert rl.read_pinned(rl.load_plan(replacement/'plan.json')['inference']) != original_attachment
    assert plan['inference']['path'] != new_plan['inference']['path']
    Path(plan['inference']['path']).write_text('{}')
    with pytest.raises(ValueError, match='pinned_input_changed'):
        rl.load_plan(output/'plan.json')


def test_initial_array_submit_unknown_keeps_intent_without_a_second_offer(rl, inputs, tmp_path):
    _, file = prepared(rl, inputs)
    commands = []

    def scheduler(command):
        if command[0] == 'sbatch':
            commands.append(command)
            raise RuntimeError('injected_unknown_scheduler_response')
        return ('fc430|0/400/0/400' if '%f' in str(command) else '0/400/0/400') if command[0] == 'sinfo' else ''

    with (patch.object(rl, 'require_modules'),
          patch.object(rl.scheduler, 'submission_lock_path', return_value=tmp_path/'submit.lock'),
          patch.object(rl.scheduler, 'output', side_effect=scheduler)):
        with pytest.raises(RuntimeError, match='unknown_scheduler'):
            rl.submit(file)
        assert (file.parent/'submission-intent.json').exists()
        with pytest.raises(ValueError, match='reconciliation_no_retry'):
            rl.submit(file)
    assert len(commands) == 1


@pytest.mark.parametrize(
    "external, stop_at_creation, actor_count, steps",
    [(False, False, 56, 64), (True, False, 48, 64), (True, False, 8, 64),
     (True, False, 7, 64), (True, True, 32, 64), (True, False, 8, 48),
     (True, False, 11, 48), (True, False, 256, 64), (True, False, 500, 64)]
)
def test_gpu_owner_hands_transient_auth_to_original_cpu_submission_only(
    rl, inputs, tmp_path, monkeypatch, external, stop_at_creation, actor_count, steps
):
    """No Slurm/GPU: real prepare serializer, stubbed numerical/scheduler owners."""
    from spireagent import resource_slot
    from stpd.p1.local_inference import AUTH_ENV, SEGMENT_SCHEMA
    from stpd.rl import segment_learner
    from stpd.rl.learner import LearnerConfig

    execution, configuration, actors = inputs
    learning = json.loads(configuration.read_bytes())
    learning['segments'] = {'steps': steps, 'inference_batch': 32}
    learning['actor_defaults']['behavior']['uniform_mix'] = 0
    configuration.write_text(json.dumps(learning))
    from stpd.rl.segments import SegmentConfig

    config = LearnerConfig(run=learning['run'], peer=learning['peer'], device='cuda',
        segments=SegmentConfig(steps=steps), wall_seconds=3600)
    monkeypatch.setattr(LearnerConfig, 'read', lambda _: config)
    monkeypatch.setattr(resource_slot, 'require_managed_slot', lambda: None)
    monkeypatch.setattr(rl, 'require_modules', lambda _: None)
    monkeypatch.setattr(rl.sys, 'platform', 'linux')
    monkeypatch.setenv('SLURM_JOB_ID', 'synthetic-gpu-owner')
    monkeypatch.setenv(AUTH_ENV, 'prior-capability')
    monkeypatch.setenv(rl.LAUNCH_STOP_ENV, str(tmp_path/'login-STOP'))
    work = tmp_path.resolve()/'learner-work'
    capability = 'synthetic-new-capability'
    calls = []

    class NumericalOwner:
        def __init__(self, config, work, io):
            self.pool = SimpleNamespace(environment=lambda: {AUTH_ENV: capability})
            (work/'inference.json').write_text(json.dumps({'schema': SEGMENT_SCHEMA,
                'segment_steps': steps, 'pool_id': 'synthetic', 'address': ['127.0.0.1', 1234]}))

        def run(self, *, on_tick=None):
            calls.append('run')
            return {'updates': 0}

    def submitted(path, **options):
        assert os.environ[AUTH_ENV] == capability
        plan = rl.load_plan(path)
        assert len(plan['actors']) == actor_count and options == {'memory_gb': 4}
        assert all(capability not in file.read_text() for file in actors.glob('*.json'))
        calls.append('submit')
        return {'job_id': 'synthetic-unoffered'}

    monkeypatch.setattr(segment_learner, 'SegmentLearner', NumericalOwner)
    monkeypatch.setattr(rl, 'submit', submitted)
    episode_capacity = 128 // steps * steps
    if actor_count * episode_capacity < 1024:
        with pytest.raises(ValueError, match='bounded_segment_actor_count'):
            rl.learn(configuration, work, execution, actors, actors=actor_count,
                     seed_namespace='synthetic-new-seeds', external_submit=external)
        assert calls == [] and not work.exists()
        return
    if stop_at_creation:
        original_mkdir = Path.mkdir

        def race(path, *args, **kwargs):
            result = original_mkdir(path, *args, **kwargs)
            if path == work:
                (tmp_path/'login-STOP').touch()
            return result

        monkeypatch.setattr(Path, 'mkdir', race)
        with pytest.raises(ValueError, match='already_stopped'):
            rl.learn(configuration, work, execution, actors, actors=actor_count,
                     seed_namespace='synthetic-new-seeds', external_submit=True)
        assert calls == []
        return
    assert rl.learn(configuration, work, execution, actors, actors=actor_count,
                    seed_namespace='synthetic-new-seeds',
                    external_submit=external) == {'updates': 0}
    effective = json.loads((work/'learner-config.json').read_bytes())
    assert effective['segments']['capacity_rows'] == 1024 + actor_count * steps
    assert not set(effective['performance'] or {}) & {'packed', 'tensor_cache', 'joint_nodes'}
    plan = rl.load_plan(actors/'plan.json')
    assert plan['learner_origin'] == {'path': str(work/'learner-config.json'),
                                      'sha256': rl.hashed(work/'learner-config.json')}
    assert plan['learner_request'] == {'path': str(configuration),
                                       'sha256': rl.hashed(configuration)}
    assert calls == (['run'] if external else ['submit', 'run'])
    assert os.environ[AUTH_ENV] == 'prior-capability'


@pytest.mark.parametrize('retain_capability', [False, True])
@pytest.mark.parametrize('unknown_cpu', [False, True])
def test_login_launch_submits_original_plan_with_same_environment(
    rl, inputs, tmp_path, monkeypatch, unknown_cpu, retain_capability
):
    from stpd.p1.local_inference import AUTH_ENV

    execution, config, actors = inputs
    script = tmp_path/'gpu.sh'
    script.write_text('#!/bin/sh\n')
    work, output = tmp_path/'work', tmp_path/'login'
    capabilities, commands = [], []
    monkeypatch.setenv(AUTH_ENV, 'prior')
    monkeypatch.setattr(rl.scheduler, 'submission_lock_path', lambda: tmp_path/'submit.lock')

    def schedule(command):
        commands.append(command)
        if command[0] == 'sbatch':
            capabilities.append(os.environ[AUTH_ENV])
            rl.prepare(execution, config, actors)
            return 'synthetic-GPU'
        if command[0] == 'squeue' and '-j' in command:
            return 'RUNNING'
        return ''

    def submit(plan, **options):
        capabilities.append(os.environ[AUTH_ENV])
        assert rl.load_plan(plan)['learner_origin']['sha256'] == rl.hashed(config)
        assert options == {'memory_gb': 4}
        if unknown_cpu:
            raise RuntimeError('unknown CPU response')
        return {'job_id': 'synthetic-CPU'}

    monkeypatch.setattr(rl.scheduler, 'output', schedule)
    monkeypatch.setattr(rl, 'submit', submit)
    if unknown_cpu:
        with pytest.raises(RuntimeError, match='unknown CPU'):
            rl.launch(script, config, work, actors, output, retain_capability=retain_capability)
        assert (actors/'STOP').exists()
        assert (output/'STOP').exists()
    else:
        receipt = rl.launch(script, config, work, actors, output,
                            retain_capability=retain_capability)
        assert receipt['GPU_job_id'] == 'synthetic-GPU'
        assert (output/'launch-receipt.json').exists()
    assert len(capabilities) == 2 and capabilities[0] == capabilities[1]
    assert len(bytes.fromhex(capabilities[0])) == 32
    assert os.environ[AUTH_ENV] == (capabilities[0]
        if retain_capability and not unknown_cpu else 'prior')
    assert all(capabilities[0] not in f.read_text() for f in tmp_path.rglob('*.json'))
    with pytest.raises(ValueError, match='fresh_login'):
        rl.launch(script, config, work, actors, output, retain_capability=retain_capability)
    assert sum(command[0] == 'sbatch' for command in commands) == 1


def test_running_gpu_plan_timeout_retains_stop_before_work_exists(rl, tmp_path, monkeypatch):
    from stpd.p1.local_inference import AUTH_ENV
    script, config = tmp_path/'gpu.sh', tmp_path/'config.json'
    script.write_text('#!/bin/sh\n')
    config.write_text('{}')
    monkeypatch.delenv(AUTH_ENV, raising=False)
    monkeypatch.setattr(rl.scheduler, 'submission_lock_path', lambda: tmp_path/'submit.lock')
    inherited = {}

    def schedule(command):
        if command[0] == 'sbatch':
            inherited['stop'] = os.environ[rl.LAUNCH_STOP_ENV]
            return 'queued-original'
        if command[0] == 'squeue' and '-j' in command:
            return 'RUNNING'
        return ''

    monkeypatch.setattr(rl.scheduler, 'output', schedule)
    clock = iter([0, 2])
    monkeypatch.setattr(rl.time, 'monotonic', lambda: next(clock))
    with pytest.raises(TimeoutError):
        rl.launch(script, config, tmp_path/'work', tmp_path/'actors',
                  tmp_path/'login', ready_seconds=1)
    assert Path(inherited['stop']).exists()
    assert not (tmp_path/'work').exists() and not (tmp_path/'actors').exists()
    assert AUTH_ENV not in os.environ and rl.LAUNCH_STOP_ENV not in os.environ


def test_additional_active_parent_accepts_invisible_node_local_scratch(rl, inputs, tmp_path):
    plan, path = prepared(rl, inputs, actors=1)
    ref = plan['actors'][0]
    persistent = path.parent/'actors'/ref['actor']
    persistent.mkdir(parents=True)
    scratch = tmp_path/'other-compute-node-only'
    (persistent/'owner.json').write_text(json.dumps({'actor': ref['actor'], 'job_id': '42',
        'scratch': str(scratch), 'plan_sha256': rl.hashed(path)}))
    assert not scratch.exists()
    assert rl.finished_actor_results(path, plan) is None
    result = {'schema': 'spireagent/friday-RL-result-v1', 'actor': ref['actor'],
        'run': plan['run'], 'actor_config_sha256': ref['sha256'],
        'scratch_retained': str(scratch), 'exit_code': 0, 'error_type': None,
        'original_summary': {'schema': 'spireagent/rl-actor-summary-1',
            'actor': ref['actor'], 'run': plan['run'], 'status': 'completed'}}
    (persistent/'result.json').write_text(json.dumps(result))
    finished = rl.finished_actor_results(path, plan)
    assert finished[0]['result_path'] == str(persistent/'result.json')
    assert finished[0]['result_sha256'] == rl.hashed(persistent/'result.json')


def test_gpu_queue_time_does_not_consume_running_readiness_or_48h_limit(
    rl, inputs, tmp_path, monkeypatch
):
    execution, config, actors = inputs
    script = tmp_path/'gpu.sh'
    script.write_text('#!/bin/sh\n')
    clock, observations, running = [0.0], [], [False]
    monkeypatch.setattr(rl.scheduler, 'submission_lock_path', lambda: tmp_path/'submit.lock')
    monkeypatch.setattr(rl.time, 'monotonic', lambda: clock[0])

    def schedule(command):
        if command[0] == 'sbatch':
            assert '--time=2880' in command
            return '81'
        if command[0] == 'squeue' and '-j' in command:
            observations.append(True)
            if len(observations) < 3:
                clock[0] += 1200  # Twenty queue minutes exceed the ready window.
                return 'PENDING'
            running[0] = True
            return 'RUNNING'
        return ''

    def sleep(seconds):
        clock[0] += seconds
        if running[0]:
            rl.prepare(execution, config, actors)

    monkeypatch.setattr(rl.scheduler, 'output', schedule)
    monkeypatch.setattr(rl.time, 'sleep', sleep)
    monkeypatch.setattr(rl, 'submit', lambda *a, **kw: {'job_id': 'CPU'})
    result = rl.launch(script, config, tmp_path/'work', actors, tmp_path/'login',
                       minutes=2880, ready_seconds=120)
    assert result['GPU_job_id'] == '81' and len(observations) == 4
    assert (tmp_path/'login/GPU-started.json').exists()
    assert not (tmp_path/'login/STOP').exists()


@pytest.mark.parametrize('existing,detail,allowed', [
    ('79', 'PENDING|gres/gpu:a100:1|rl4-learner', True),
    ('79', 'RUNNING|gres/gpu:a100:1|rl4-learner', True),
    ('79', 'RUNNING|gres/gpu:a30:1|rl4-learner', False),
    ('79', 'PENDING|gres/gpu:a100:2|rl4-learner', False),
    ('79', 'PENDING|gres/gpu:a100:1|foreign-job', False),
    ('79\n80', 'PENDING|gres/gpu:a100:1|rl4-learner', False),
])
def test_explicit_opposite_gpu_queue_keeps_one_job_per_card(
    rl, inputs, tmp_path, monkeypatch, existing, detail, allowed
):
    execution, config, actors = inputs
    script = tmp_path/'gpu.sh'
    script.write_text('#!/bin/sh\n')
    offers = []
    monkeypatch.setattr(rl.scheduler, 'submission_lock_path', lambda: tmp_path/'submit.lock')

    def schedule(command):
        if command[0] == 'sbatch':
            offers.append(command)
            rl.prepare(execution, config, actors)
            return '80'
        if command[0] == 'squeue':
            if command[-1] == '%T':
                return 'RUNNING'
            return detail if '-j' in command else existing
        return ''

    monkeypatch.setattr(rl.scheduler, 'output', schedule)
    monkeypatch.setattr(rl, 'submit', lambda *a, **kw: {'job_id': 'CPU'})
    if allowed:
        result = rl.launch(script, config, tmp_path/'work', actors, tmp_path/'login',
                           gpu='a30', alongside_job='79')
        assert result['GPU_job_id'] == '80' and len(offers) == 1
    else:
        with pytest.raises(ValueError, match='GPU.*owner|GPU.*scheduling'):
            rl.launch(script, config, tmp_path/'work', actors, tmp_path/'login',
                      gpu='a30', alongside_job='79')
        assert not offers


@pytest.mark.parametrize('state', ['PENDING', 'FAILED'])
def test_existing_plan_cannot_offer_actors_before_live_gpu(
    rl, inputs, tmp_path, monkeypatch, state
):
    execution, config, actors = inputs
    script = tmp_path/'gpu.sh'
    script.write_text('#!/bin/sh\n')
    observed = []
    monkeypatch.setattr(rl.scheduler, 'submission_lock_path', lambda: tmp_path/'submit.lock')

    def schedule(command):
        if command[0] == 'sbatch':
            rl.prepare(execution, config, actors)
            return '81'
        if command[0] == 'squeue' and '-j' in command:
            observed.append(state)
            return state
        return ''

    monkeypatch.setattr(rl.scheduler, 'output', schedule)
    monkeypatch.setattr(rl, 'submit', lambda *a, **kw: pytest.fail('GPU not RUNNING'))
    if state == 'PENDING':
        monkeypatch.setattr(rl.time, 'sleep', lambda *_: (_ for _ in ()).throw(
            InterruptedError('operator ends read-only queue wait')))
        error = InterruptedError
    else:
        error = RuntimeError
    with pytest.raises(error):
        rl.launch(script, config, tmp_path/'work', actors, tmp_path/'login')
    assert observed == [state] and (tmp_path/'login/STOP').exists()


def test_reserve_only_never_offers_cpu_or_needs_actor_readiness(rl, tmp_path, monkeypatch):
    script, config = tmp_path/'gpu.sh', tmp_path/'config.json'
    script.write_text('#!/bin/sh\n')
    config.write_text('{}')
    monkeypatch.setattr(rl.scheduler, 'submission_lock_path', lambda: tmp_path/'submit.lock')
    monkeypatch.setattr(rl.scheduler, 'output', lambda cmd: '91' if cmd[0] == 'sbatch' else '')
    monkeypatch.setattr(rl, 'submit', lambda *a, **kw: pytest.fail('reservation has no actors'))
    receipt = rl.launch(script, config, tmp_path/'work', tmp_path/'actors', tmp_path/'login',
                        reserve_only=True, minutes=2880)
    assert receipt == {'GPU_job_id': '91', 'reservation_only': True, 'CPU_actors_offered': 0}
    assert not (tmp_path/'work').exists() and not (tmp_path/'actors').exists()


def test_allocation_holder_exits_at_explicit_execution_budget(tmp_path, monkeypatch):
    # Process/clock behavior only; this fixture does not qualify GPU allocation.
    monkeypatch.setenv('SLURM_JOB_ID', '123456')
    budget = tmp_path/'budget.json'
    budget.write_text(json.dumps({'seconds': 1}))
    root = tmp_path/'held'
    subprocess.run([sys.executable, str(FRIDAY/'gpu_session.py'), '--root', str(root),
                    '--budget-file', str(budget)], check=True, timeout=5)
    assert json.loads((root/'allocation.json').read_bytes())['initial_budget_seconds'] == 1
    assert json.loads((root/'released.json').read_bytes())['signals'] == []


def test_long_actor_entry_accepts_500_and_keeps_fair_share(rl, inputs):
    from stpd.rl.learner import LearnerConfig
    from stpd.rl.segments import SegmentConfig

    plan, _ = prepared(rl, inputs, actors=500, wall_seconds=72000, max_games=100000)
    assert len({a["actor"] for a in plan["actors"]}) == 500
    assert json.loads(Path(plan["actors"][-1]["path"]).read_bytes())["max_games"] == 100000
    config = LearnerConfig(run="capacity", peer={}, segments=SegmentConfig()).bind_actors(500)
    assert config.segments.capacity_rows == 1024 + 500 * 64
    with pytest.raises(ValueError, match="segment_actor_count_required"):
        config.bind_actors(501)
    assert rl.capacity(1999, 0, 500, maximum=500) == 499
    assert rl.capacity(2000, 301, 500, maximum=500) == 32


def test_general_segment_submission_keeps_300_second_advance(rl, tmp_path, monkeypatch):
    from contextlib import nullcontext

    path = tmp_path / "plan.json"
    path.write_text("{}")
    plan = {
        "source_root": str(tmp_path),
        "actors": [{"actor": "one"}],
        "wall_seconds": 3600,
        "episode_seconds": 14400,
        "games_per_ledger": 10,
        "trajectory_encoding": "short-segment-1",
    }
    monkeypatch.setattr(rl, "load_plan", lambda _: plan)
    monkeypatch.setattr(rl, "require_modules", lambda _: None)
    monkeypatch.setattr(rl, "launcher", lambda *args: "#!/bin/bash\nexit 0\n")
    monkeypatch.setattr(rl.scheduler, "user_submission_lock", nullcontext)
    monkeypatch.setattr(
        rl.scheduler, "output", lambda command: ("fc430|0/100/0/100" if "%f|%C" in command
                         else "0/100/0/100") if command[0] == "sinfo" else ""
    )
    result = rl.submit(path, dry_run=True, partition="general")
    assert "--signal=B:USR1@300" in result["command"]
    assert "--partition=general" in result["command"]


@pytest.mark.parametrize("partition", ["general", "debug"])
def test_other_partition_RL_array_still_blocks_submission(rl, inputs, tmp_path, monkeypatch,
                                                         partition):
    _, plan = prepared(rl, inputs)
    monkeypatch.setattr(rl, "require_modules", lambda _: None)
    monkeypatch.setattr(rl.scheduler, "submission_lock_path", lambda: tmp_path / "submit.lock")

    def scheduler(command):
        if command[0] == "squeue":
            # The existing allocation is in smp; a partition filter would hide
            # it from this general/debug request and wrongly reach sbatch.
            if "-p" in command and command[command.index("-p") + 1] != "smp":
                return ""
            return "other-array_0"
        if command[0] == "sinfo":
            return "0/1000/0/1000"
        pytest.fail("must reject the foreign RL array before any sbatch")

    monkeypatch.setattr(rl.scheduler, "output", scheduler)
    with pytest.raises(ValueError, match="existing_friday_array_must_drain"):
        rl.submit(plan, partition=partition)
    assert not (plan.parent / "submission-intent.json").exists()


def test_actor_terminal_facts_bind_raw_and_array_scheduler_ids(rl, tmp_path, monkeypatch):
    path = tmp_path / "plan.json"
    path.write_text("{}")
    plan = {"actors": [{"actor": "a"}, {"actor": "b"}]}
    (tmp_path / "submission.json").write_text(json.dumps({"job_id": "77"}))
    owner = tmp_path / "actors/a/owner.json"
    owner.parent.mkdir(parents=True)
    owner.write_text(json.dumps({"actor": "a", "job_id": "101", "plan_sha256": rl.hashed(path)}))
    monkeypatch.setattr(rl, "load_plan", lambda _: plan)
    monkeypatch.setattr(
        rl.subprocess,
        "check_output",
        lambda *args, **kwargs: (
            "101|77_0|FAILED|1:0\n101.batch|77_0.batch|FAILED|1:0\n102|77_1|RUNNING|0:0\n"
        ),
    )
    rows = rl.actor_accounting(path)
    assert rows[0]["actor"] == "a" and rows[0]["terminal"] is True
    assert rows[1]["actor"] == "b" and rows[1]["terminal"] is False
    owner.write_text(json.dumps({"actor": "a", "job_id": "77_0", "plan_sha256": rl.hashed(path)}))
    with pytest.raises(ValueError, match="terminal_actor_owner_or_submission_binding_changed"):
        rl.actor_accounting(path)


def test_explicit_capacity_limit_survives_repeated_actor_binding():
    from stpd.rl.learner import LearnerConfig
    from stpd.rl.segments import SegmentConfig
    config = LearnerConfig(run='capacity', peer={},
        segments=SegmentConfig(capacity_limit_rows=6400))
    assert config.bind_actors(256).bind_actors(256).segments.capacity_rows == 6400
    assert config.bind_actors(32).segments.capacity_rows == 3072
    for invalid in (True, 0, 3071, 6400.0):
        with pytest.raises(ValueError, match='invalid_segment'):
            SegmentConfig(capacity_limit_rows=invalid)


@pytest.mark.parametrize('other_jobs', ['', '\n80'])
def test_held_gpu_launch_offers_one_step_and_never_sbatch(
    rl, inputs, tmp_path, monkeypatch, other_jobs
):
    execution, config, actors = inputs
    script = tmp_path/'gpu.sh'
    script.write_text('#!/bin/sh\n')
    root = tmp_path/'held-login'
    commands = []
    monkeypatch.setattr(rl.scheduler, 'submission_lock_path', lambda: tmp_path/'submit.lock')
    def schedule(command):
        commands.append(command)
        if '--steps' in command:
            return '79.batch\n79.extern'
        if '-j' in command:
            return 'RUNNING|gres/gpu:a100:1|rl4-learner'
        return '79' + other_jobs
    monkeypatch.setattr(rl.scheduler, 'output', schedule)
    def process(command, **kwargs):
        commands.append(command)
        (root/'GPU-step-id').write_text('79.17\n')
        rl.prepare(execution, config, actors)
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(rl.subprocess, 'Popen', process)
    submitted_options = []
    def submit(*args, **options):
        submitted_options.append(options)
        return {'job_id': '80'}
    monkeypatch.setattr(rl, 'submit', submit)
    result = rl.launch(script, config, tmp_path/'learner', actors, root,
                       gpu='a100', held_job_id='79', actor_node_types=['fc430'],
                       actor_exclude_nodes=['smp-9-3'])
    assert result['GPU_job_id'] == '79.17'
    assert sum(c[0] == 'srun' for c in commands) == 1
    assert all(c[0] != 'sbatch' for c in commands)

    assert submitted_options == [{'memory_gb': 4, 'node_types': ['fc430'],
                                  'exclude_nodes': ['smp-9-3']}]


def test_held_gpu_active_step_rejected_without_submission(rl, tmp_path, monkeypatch):
    script, config = tmp_path/'gpu.sh', tmp_path/'config.json'
    script.write_text('#!/bin/sh\n')
    config.write_text('{}')
    monkeypatch.setattr(rl.scheduler, 'submission_lock_path', lambda: tmp_path/'submit.lock')
    def schedule(command):
        if '--steps' in command:
            return '79.9'
        if '-j' in command:
            return 'RUNNING|gres/gpu:a100:1|rl4-learner'
        return '79'
    monkeypatch.setattr(rl.scheduler, 'output', schedule)
    monkeypatch.setattr(rl.subprocess, 'Popen', lambda *a, **kw: pytest.fail('must not offer'))
    with pytest.raises(ValueError, match='active_step'):
        rl.launch(script, config, tmp_path/'learner', tmp_path/'actors', tmp_path/'login',
                  gpu='a100', held_job_id='79')


@pytest.mark.parametrize('node_types,allowed', [(None, False), (['fc430', 'c6220'], True)])
def test_explicit_node_types_never_silently_mix_slow_nodes(rl, inputs, tmp_path,
                                                          monkeypatch, node_types, allowed):
    _, path = prepared(rl, inputs)
    from contextlib import nullcontext

    def scheduler(command):
        if command[0] == 'sinfo':
            return ('fc430|0/0/0/16\nc6220|0/400/0/400'
                    if '%f|%C' in command else '0/400/0/416')
        return ''

    monkeypatch.setattr(rl, 'require_modules', lambda _: None)
    monkeypatch.setattr(rl.scheduler, 'user_submission_lock', nullcontext)
    monkeypatch.setattr(rl.scheduler, 'output', scheduler)
    if allowed:
        result = rl.submit(path, dry_run=True, node_types=node_types)
        assert '--constraint=fc430|c6220' in result['command']
    else:
        with pytest.raises(ValueError, match='explicit_node_type_capacity'):
            rl.submit(path, dry_run=True)
        assert not (path.parent / 'submission-intent.json').exists()


def test_actor_monitor_preserves_expected_pending_until_terminal_observation(rl, tmp_path, monkeypatch):
    monkeypatch.setattr(rl, 'load_plan', lambda path: {'actors': [{'actor': 'a'}, {'actor': 'b'}]})
    monitor = rl.ActorAllocationMonitor(tmp_path / 'plan.json')
    retired = set()
    owner = SimpleNamespace(retired_actors=retired,
        retire_actor=lambda actor, row: retired.add(actor))
    monitor.poll(owner)
    assert owner.actor_allocation_pending == {'a', 'b'}
    monitor.observations.put({'actor': 'a', 'terminal': True})
    monitor.poll(owner)
    assert owner.actor_allocation_pending == {'b'}
    monitor.observations.put({'actor': 'b', 'terminal': True})
    monitor.poll(owner)
    assert not owner.actor_allocation_pending
