#!/usr/bin/env python3
"""Freeze one RL recipe and connect the existing Friday learner/actor/watcher CLIs."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

import evaluate
import rl
import submit as scheduler
import watch
from batch import hashed, private_root, read_pinned, save
from spireagent.json_boundary import json_bytes
from spireagent.rl_exchange import Exchange

SCHEMA = 'spireagent/friday-rl-recipe-1'
SOURCE_FILES = ('python/stpd/rl/learner.py', 'python/spireagent/workbench/rl_learner.py',
                'tools/friday/rl.py', 'tools/friday/watch.py', 'tools/friday/evaluate.py',
                'tools/friday/recipe.py')


def reference(path: Path) -> dict[str, str]:
    return {'path': str(path), 'sha256': hashed(path)}


def load_recipe(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    wall = value.get('wall_seconds')
    if (value.get('schema') != SCHEMA or type(value.get('approved')) is not bool
            or type(wall) is not int or not 300 <= wall <= 72000
            or value.get('gpu', 'a30') not in ('a30', 'a100')
            or type(value.get('actors', 12)) is not int or not 1 <= value.get('actors', 12) <= 200):
        raise ValueError('explicit_finite_wall_and_bounded_single_GPU_recipe_required')
    if (type(value.get('watch_poll_seconds', 900)) is not int
            or not 60 <= value.get('watch_poll_seconds', 900) <= 1800):
        raise ValueError('bounded_watcher_poll_seconds_required')
    learning = value['learner']
    if (learning.get('device') != 'cuda' or learning.get('resume_checkpoint') is not None
            or learning.get('wall_seconds') not in (None, wall)):
        raise ValueError('new_run_CUDA_learner_with_same_wall_required')
    environment = rl.canonical_path(Path(value['cuda_environment']))
    if not environment.is_dir() or not (environment / 'bin/python').is_file():
        raise ValueError('independent_physical_CUDA_environment_required')
    source = rl.canonical_path(Path(value['runtime_source']))
    if environment == (source.parent / 'env').resolve():
        raise ValueError('CUDA_environment_must_be_independent_of_CPU_actor_environment')
    watch.tiers(value)
    return value


def environment_prefix(source: Path, plan: Path) -> str:
    # Reuse the exact ordinary user-owned private environment loader, never read it here.
    script = evaluate.launcher(plan, {'source_root': str(source)})
    if script.count('\nexec ') != 1:
        raise ValueError('shared_Friday_launcher_prefix_changed')
    return script.split('\nexec ', 1)[0] + '\n'


def script(path: Path, prefix: str, command: list[str]) -> None:
    path.write_text(prefix + 'exec ' + shlex.join(command) + '\n')
    path.chmod(0o700)


def prepare(configuration: Path, output: Path) -> dict[str, Any]:
    from spireagent.workbench.reference_suite import load_manifest
    from stpd.evaluation.suites import RL3_LEARNING_CURVE_SUITE_ID

    config = load_recipe(configuration)
    source = Path(config['runtime_source'])
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=source):
        raise ValueError('committed_frozen_runtime_source_required')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    source_files = {name: hashed(source / name) for name in SOURCE_FILES}
    for name, digest in source_files.items():
        committed = subprocess.check_output(['git', 'show', f'{revision}:{name}'], cwd=source)
        if hashlib.sha256(committed).hexdigest() != digest:
            raise ValueError('committed_runtime_file_identity_changed')
    manifest = Path(config['evaluation_manifest'])
    _, suite = load_manifest(manifest, hashed(manifest))
    if suite['suite_id'] != RL3_LEARNING_CURVE_SUITE_ID:
        raise ValueError('registered_RL3_evaluation100_manifest_required')
    execution = json.loads(Path(config['evaluation_execution']).read_bytes())
    evaluate.validate_execution({**execution, 'models': {'latest': {}}}, {'latest': 'latest'})
    output = rl.canonical_path(output, existing=False)
    output.mkdir(parents=True, mode=0o700)
    private_root(output)
    save(output / 'configuration.json', config)
    learning = dict(config['learner'], wall_seconds=config['wall_seconds'])
    learning.setdefault('performance', None)
    learning.setdefault('background_io', False)
    save(output / 'learner.json', learning)
    from stpd.rl.learner import LearnerConfig

    LearnerConfig.read(output / 'learner.json')  # Parse on CPU before requesting any GPU.
    for name in ('evaluation_execution', 'evaluation_manifest'):
        (output / f'{name}.json').write_bytes(Path(config[name]).read_bytes())
    actors = output / 'actors' / 'plan.json'
    rl.prepare(Path(config['execution']), output / 'learner.json', actors.parent,
        actors=config.get('actors', 12), wall_seconds=config['wall_seconds'],
        runtime_source=source, behavior=config.get('behavior', {
            'mode': 'temperature_uniform', 'temperature': 1.0, 'uniform_mix': 0.03}),
        seed_namespace=config['seed_namespace'], **config['actor_budget'])
    plan = {'schema': SCHEMA, 'configuration': reference(output / 'configuration.json'),
            'learner': reference(output / 'learner.json'), 'actors': reference(actors),
            'runtime_source': str(source), 'source_revision': revision, 'source_files': source_files,
            'evaluation_execution': reference(output / 'evaluation_execution.json'),
            'evaluation_manifest': reference(output / 'evaluation_manifest.json')}
    save(output / 'plan.json', plan)
    prefix = environment_prefix(source, output / 'plan.json')
    cpu = str(source.parent / 'env/bin/python')
    script(output / 'start.sh', prefix, [cpu, str(source / 'tools/friday/recipe.py'),
                                      'start', '--plan', str(output / 'plan.json')])
    threads = shlex.quote(str(learning.get('threads', 4)))
    learner_prefix = prefix + (f'export OMP_NUM_THREADS={threads} MKL_NUM_THREADS={threads} '
                               f'OPENBLAS_NUM_THREADS={threads}\n')
    script(output / 'learner.sh', learner_prefix, [str(Path(config['cuda_environment']) / 'bin/python'),
        '-m', 'spireagent.workbench.rl_learner', '--config', str(output / 'learner.json'),
        '--work', str(output / 'learner')])
    script(output / 'watch.sh', prefix, [str(Path(config['cuda_environment']) / 'bin/python'),
        str(source / 'tools/friday/watch.py'),
        '--config', str(output / 'watch.json'), '--work', str(output / 'watch')])
    plan['launchers'] = {name: reference(output / name)
                         for name in ('start.sh', 'learner.sh', 'watch.sh')}
    save(output / 'plan.json', plan)
    return plan


def dispatch(root: Path, name: str, request: dict[str, Any], operation) -> dict[str, Any]:
    save(root / f'{name}-intent.json', {'at': time.time(), 'request': request})
    try:
        result = operation()
    except ValueError as error:
        save(root / f'{name}-receipt.json', {'state': 'known_failure', 'error_type': type(error).__name__})
        raise
    except Exception as error:  # retain uncertain scheduler effect, never resubmit
        save(root / f'{name}-outcome-unknown.json', {'error_type': type(error).__name__})
        raise
    save(root / f'{name}-receipt.json', {'at': time.time(), 'state': 'accepted', **result})
    return result


def start(path: Path) -> dict[str, Any]:
    root = private_root(rl.canonical_path(path).parent)
    if list(root.glob('*intent.json')) or list(root.glob('*receipt.json')):
        raise ValueError('existing_recipe_attempt_requires_original_owner_reconciliation')
    plan = json.loads(path.read_bytes())
    if plan.get('schema') != SCHEMA:
        raise ValueError('frozen_RL_recipe_plan_required')
    config, learning = read_pinned(plan['configuration']), read_pinned(plan['learner'])
    if config['approved'] is not True:
        raise ValueError('explicit_approved_recipe_required')
    load_recipe(Path(plan['configuration']['path']))
    for name in ('actors', 'evaluation_execution', 'evaluation_manifest'):
        read_pinned(plan[name])
    for launcher in plan['launchers'].values():
        if Path(launcher['path']).is_symlink() or hashed(Path(launcher['path'])) != launcher['sha256']:
            raise ValueError('frozen_recipe_launcher_changed')
    source = Path(plan['runtime_source'])
    if (subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
            != plan['source_revision']
            or subprocess.check_output(['git', 'status', '--porcelain'], cwd=source)
            or any(hashed(source / name) != digest for name, digest in plan['source_files'].items())):
        raise ValueError('frozen_runtime_source_changed')
    exchange = Exchange(learning['peer'], learning['run'])
    if (exchange.stop_request() or exchange.latest_model() or exchange.latest_checkpoint()
            or exchange.shard_keys()):
        raise ValueError('new_empty_RL_run_required')
    with (root / 'start-intent.json').open('xb') as claim:
        claim.write(json_bytes({'plan_sha256': hashed(path), 'at': time.time()}))
        claim.flush()
        os.fsync(claim.fileno())
    logs = private_root(root / 'slurm')
    wall = config['wall_seconds']
    command = ['sbatch', '--parsable', '--partition=smp_gpu', '--nodes=1', '--ntasks=1',
        '--cpus-per-task=4', '--mem=32G', '--gres=gpu:' + config.get('gpu', 'a30') + ':1',
        f'--time={math.ceil((wall + 600) / 60)}', '--no-requeue', '--signal=B:USR1@300',
        '--job-name=spireagent-rl-learner', f'--output={logs}/learner-%j.out',
        f'--error={logs}/learner-%j.err', str(root / 'learner.sh')]
    try:
        with scheduler.user_submission_lock():
            if scheduler.output(['squeue', '-h', '-u', scheduler.username(), '-p', 'smp_gpu', '-o', '%i']):
                raise ValueError('another_Friday_GPU_job_requires_owner_scheduling')
            gpu = dispatch(root, 'learner-submit', {'command': command},
                           lambda: {'job_id': scheduler.output(command)})
        deadline = time.monotonic() + 120
        model = exchange.latest_model()
        while model is None and time.monotonic() < deadline:
            time.sleep(min(5, max(0, deadline - time.monotonic())))
            model = exchange.latest_model()
        if model is None or model['version'] != 0:
            raise ValueError('learner_version_zero_not_observed_within120seconds')
        save(root / 'learner-ready-receipt.json', {'model': model, 'gpu_job': gpu['job_id']})
        actors = dispatch(root, 'actors-submit', {'plan': plan['actors']},
                          lambda: rl.submit(Path(plan['actors']['path'])))
        watcher = {'schema': watch.SCHEMA, 'run': learning['run'], 'peer': learning['peer'],
            'runtime_source': str(source), 'actor_plan': plan['actors']['path'],
            'actor_job_id': str(actors['job_id']).split(';')[0], 'started_at': model['published_at'],
            'wall_seconds': wall, 'poll_seconds': config.get('watch_poll_seconds', 900),
            'evaluation_mode': 'submit',
            'evaluation_execution': plan['evaluation_execution']['path'],
            'evaluation_manifest': plan['evaluation_manifest']['path'],
            'evaluation_tiers': config['evaluation_tiers']}
        save(root / 'watch.json', watcher)
        watch.read_config(root / 'watch.json')
        command = ['sbatch', '--parsable', '--partition=smp', '--nodes=1', '--ntasks=1',
            '--cpus-per-task=1', '--mem=4G', f'--time={math.ceil((wall + 3000) / 60)}',
            '--no-requeue', '--signal=B:USR1@300', '--job-name=spireagent-rl-watch',
            f'--output={logs}/watch-%j.out', f'--error={logs}/watch-%j.err', str(root / 'watch.sh')]
        with scheduler.user_submission_lock():
            observer = dispatch(root, 'watch-submit', {'command': command},
                                lambda: {'job_id': scheduler.output(command)})
        result = {'learner': gpu, 'actors': actors, 'watcher': observer,
                  'started_at': model['published_at'], 'plan_sha256': hashed(path)}
        save(root / 'start-receipt.json', result)
        return result
    except Exception:
        save(root / 'learner/STOP', {'reason': 'recipe_start_failed_new_run'})
        save(Path(plan['actors']['path']).parent / 'STOP', {'reason': 'recipe_start_failed_new_run'})
        try:
            exchange.request_stop('recipe_start_failed_new_run')
        except Exception as error:  # noqa: BLE001 -- local STOP survives a remote control outage
            save(root / 'stop-outcome-unknown.json', {'error_type': type(error).__name__})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    freeze = commands.add_parser('prepare')
    freeze.add_argument('--configuration', type=Path, required=True)
    freeze.add_argument('--output', type=Path, required=True)
    launch = commands.add_parser('start')
    launch.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    value = (prepare(args.configuration, args.output) if args.command == 'prepare' else start(args.plan))
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
