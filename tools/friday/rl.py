#!/usr/bin/env python3
"""Friday resource adapter for the original RL1 CPU actor; no gameplay loop."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import evaluate
import submit as scheduler
from batch import capacity, hashed, private_root, read_pinned, save, snapshot

SCHEMA = 'spireagent/friday-rl-actors-v1'
NAME = re.compile(r'^[a-z0-9][a-z0-9-]{0,62}$')
RETENTION = {'tier': 'trajectory', 'purpose': 'training', 'audit_every': 50}


def canonical_path(path: Path, *, existing: bool = True) -> Path:
    if (not path.is_absolute() or path.is_symlink() or path.resolve() != path
            or existing and not path.exists()):
        raise ValueError('canonical_physical_path_required')
    return path


def prepare(execution: Path, learner: Path, output: Path, *, actors: int = 8,
            wall_seconds: int = 3600, episode_seconds: int = 900, max_games: int = 20,
            behavior: dict[str, Any] | None = None, seed_namespace: str | None = None
            ) -> dict[str, Any]:
    execution, learner = canonical_path(execution), canonical_path(learner)
    output = canonical_path(output, existing=False)
    base, learning = json.loads(execution.read_bytes()), json.loads(learner.read_bytes())
    if (learning.get('schema') not in {'stpd/rl-run-1', 'stpd/rl-learner-config-1'}
            or not NAME.fullmatch(learning.get('run', ''))):
        raise ValueError('original_learner_run_and_peer_configuration_required')
    peer = learning['peer']
    if set(peer) != {'kind', 'bucket', 'endpoint', 'prefix', 'region'}:
        raise ValueError('original_nonsecret_R2_peer_binding_required')
    from spireagent.storage.s3 import S3Config

    S3Config(**{key: value for key, value in peer.items() if key != 'kind'})
    if (peer.get('kind') != 's3' or base.get('peer') != peer
            or base.get('store') != 's3' or base.get('retention') != RETENTION
            or base.get('schema') != 'spireagent/managed-evaluation-execution-v1'):
        raise ValueError('same_frozen_R2_execution_and_learner_peer_required')
    canonical_path(Path(base['candidate_directory']))
    if (type(actors) is not int or not 1 <= actors <= 200
            or type(wall_seconds) is not int or not 300 <= wall_seconds <= 21600
            or type(episode_seconds) is not int or not 10 <= episode_seconds <= 1800
            or wall_seconds <= episode_seconds + 180
            or type(max_games) is not int or not 1 <= max_games <= 200):
        raise ValueError('bounded_one_to_two_hundred_CPU_actor_budget_required')
    defaults = learning.get('actor_defaults', {})
    behavior = behavior if behavior is not None else defaults.get('behavior')
    seed_namespace = seed_namespace or learning.get('seed_namespace')
    if (not isinstance(behavior, dict)
            or set(behavior) != {'mode', 'temperature', 'uniform_mix'}
            or not isinstance(seed_namespace, str) or not seed_namespace):
        raise ValueError('explicit_original_behavior_and_seed_namespace_required')
    output.mkdir(parents=True, mode=0o700)
    private_root(output)
    frozen = output / 'execution.json'
    frozen.write_bytes(execution.read_bytes())
    key = hashlib.sha256((str(output) + '\0' + learning['run']).encode()).hexdigest()[:12]
    actor_refs = []
    for index in range(actors):
        name = f'friday-{key}-{index:02d}'
        actor = {'schema': 'spireagent/rl-actor-config-1', 'actor': name,
            'execution': str(frozen), 'run': learning['run'], 'peer': peer,
            'seed_namespace': seed_namespace, 'wall_seconds': wall_seconds,
            'episode_seconds': episode_seconds, 'max_games': max_games,
            'games_per_ledger': 1, 'behavior': behavior,
            'unknown_stop': {'consecutive': 1, 'fraction': 0, 'after': 1},
            'pipeline': defaults.get('pipeline', {'save_workers': 1, 'pending_limit': 2})}
        path = output / f'{name}.json'
        save(path, actor)
        actor_refs.append({'actor': name, 'path': str(path), 'sha256': hashed(path)})
    plan = {'schema': SCHEMA, 'target': 'friday', 'run': learning['run'], 'peer': peer,
        'source_root': str(Path(__file__).resolve().parents[2]), 'actors': actor_refs,
        'execution': {'path': str(frozen), 'sha256': hashed(frozen)},
        'learner_origin': {'path': str(learner), 'sha256': hashed(learner)},
        'wall_seconds': wall_seconds, 'episode_seconds': episode_seconds,
        'constraint': 'fc430', 'retry_unknown': False, 'automatic_mac_fallback': False,
        'cross_actor_immediate_unknown_stop': False}
    save(output / 'plan.json', plan)
    return plan


def load_plan(path: Path) -> dict[str, Any]:
    canonical_path(path)
    value = json.loads(path.read_bytes())
    if (value.get('schema') != SCHEMA or value.get('target') != 'friday'
            or value.get('retry_unknown') is not False
            or value.get('automatic_mac_fallback') is not False):
        raise ValueError('frozen_Friday_RL_actor_plan_required')
    base = read_pinned(value['execution'])
    canonical_path(Path(value['source_root']))
    canonical_path(Path(base['candidate_directory']))
    for ref in value['actors']:
        actor = read_pinned(ref)
        if (actor['actor'] != ref['actor'] or actor['run'] != value['run']
                or actor['peer'] != value['peer'] or base['peer'] != value['peer']
                or actor['execution'] != value['execution']['path']):
            raise ValueError('actor_learner_execution_binding_changed')
    return value


def require_modules(source: Path) -> None:
    """Never import/pull RL1's private checkout; only the executing frozen release."""
    for module, relative in (
            ('spireagent.workbench.rl_actor', 'workbench/rl_actor.py'),
            ('spireagent.rl_exchange', 'rl_exchange.py')):
        expected = source / 'python/spireagent' / relative
        spec = importlib.util.find_spec(module)
        if (spec is None or spec.origin is None or not expected.is_file()
                or expected.is_symlink() or Path(spec.origin).resolve() != expected):
            raise ValueError('RL1_modules_not_merged_or_not_in_frozen_release')


def launcher(path: Path, plan: dict[str, Any]) -> str:
    script = evaluate.launcher(path, {'source_root': plan['source_root']})
    command = '$source_root/tools/friday/evaluate.py'
    if script.count(command) != 1:
        raise ValueError('shared_Friday_launcher_entry_changed')
    return script.replace(command, '$source_root/tools/friday/rl.py', 1).replace(
        '--mode evaluation', '--mode rl', 1)


def submit(path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    plan = load_plan(path)
    require_modules(Path(plan['source_root']))
    root = private_root(path.parent)
    with scheduler.user_submission_lock():
        existing = scheduler.output(['squeue', '-h', '-u', scheduler.username(), '-n',
                                     'spireagent-f2', '-o', '%i'])
        if existing:
            raise ValueError('existing_friday_array_must_drain_before_new_submission')
        if (root / 'STOP').exists() or any((root / 'actors').glob('*/owner.json')):
            raise ValueError('previous_actor_owner_requires_reconciliation_no_reoffer')
        idle = sum(int(line.split('/')[1]) for line in scheduler.output(
            ['sinfo', '-h', '-p', 'smp', '-o', '%C']).splitlines())
        queued = len(scheduler.output(
            ['squeue', '-h', '-p', 'smp', '-t', 'PD', '-o', '%i']).splitlines())
        count = len(plan['actors'])
        if capacity(idle, queued, count) < count:
            raise ValueError('requested_RL_actors_exceed_current_fair_share')
        script = root / 'launch.sh'
        script.write_text(launcher(path, plan), encoding='utf-8')
        script.chmod(0o700)
        logs = private_root(root / 'slurm')
        minutes = (plan['wall_seconds'] + 300 + 59) // 60
        command = ['sbatch', '--parsable', '--partition=smp', '--nodes=1', '--ntasks=1',
            '--cpus-per-task=1', '--mem=4G', f'--time={minutes}', '--no-requeue',
            f"--signal=B:USR1@{plan['episode_seconds'] + 180}", '--job-name=spireagent-f2',
            f'--array=0-{count - 1}%{count}', '--constraint=fc430',
            f'--output={logs}/%A_%a.out', f'--error={logs}/%A_%a.err', str(script)]
        result = {'schema': 'spireagent/friday-RL-submit-v1', 'command': command,
            'cores': count, 'idle_smp_cores': idle, 'queued_smp_jobs': queued,
            'dry_run': dry_run, 'cross_actor_immediate_unknown_stop': False}
        if not dry_run:
            result['job_id'] = scheduler.output(command)
            save(root / 'submission.json', result)
        return result


def run(path: Path) -> dict[str, Any]:
    plan = load_plan(path)
    require_modules(Path(plan['source_root']))
    if (sys.platform != 'linux' or not os.environ.get('SLURM_JOB_ID')
            or int(os.environ.get('SLURM_CPUS_PER_TASK', '0')) < 1):
        raise ValueError('Friday_CPU_allocation_required_no_Mac_fallback')
    index = int(os.environ.get('SLURM_ARRAY_TASK_ID', '-1'))
    if not 0 <= index < len(plan['actors']):
        raise ValueError('frozen_actor_array_index_required')
    root = private_root(path.parent)
    if (root / 'STOP').exists():
        return {'status': 'stopped', 'reason': 'batch_stop', 'new_games': 0}
    ref = plan['actors'][index]
    read_pinned(ref)
    persistent = private_root(root / 'actors' / ref['actor'])
    descriptor = os.open(persistent / 'owner.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    scratch = Path(tempfile.mkdtemp(prefix='spireagent-friday-rl-')).resolve()
    scratch.chmod(0o700)
    with os.fdopen(descriptor, 'wb') as owner:
        owner.write(json.dumps({'actor': ref['actor'], 'job_id': os.environ['SLURM_JOB_ID'],
                                'scratch': str(scratch), 'plan_sha256': hashed(path)}).encode())
        owner.flush()
        os.fsync(owner.fileno())
    work = scratch / 'actor'
    work.mkdir(mode=0o700)

    def stop(_number, _frame):
        # Original RL1 STOP is checked between one-game ledgers; no native command is retried.
        (work / 'STOP').touch()

    previous = {number: signal.signal(number, stop)
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)}
    began = time.monotonic()
    code = None
    error_type = error_code = None
    summary = None
    try:
        with ((scratch / 'actor.out').open('wb') as stdout,
              (scratch / 'actor.err').open('wb') as stderr):
            code = subprocess.run([sys.executable, '-m', 'spireagent.workbench.rl_actor',
                '--config', ref['path'], '--work', str(work)], stdout=stdout, stderr=stderr,
                check=False).returncode
        if (work / 'actor-summary.json').is_file():
            summary = json.loads((work / 'actor-summary.json').read_bytes())
        uncertain = any(game.get('status') in {'unknown', 'save_failed'}
                        or game.get('status') not in {'saved', 'finished'} and not game.get('shard')
                        for game in (summary or {}).get('games', []))
        if code != 0 or summary is None or uncertain:
            # Fences pending workers before the potentially slow final NFS snapshot.
            save(root / 'STOP', {'actor': ref['actor'], 'reason': 'actor_failed_or_unknown',
                                'exit_code': code})
    except Exception as error:  # noqa: BLE001 -- preserve this exact owned attempt, never retry.
        error_type, error_code = type(error).__name__, getattr(error, 'code', None)
        save(root / 'STOP', {'actor': ref['actor'], 'reason': 'actor_failed',
                            'error_type': error_type, 'error_code': error_code})
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        result = {'schema': 'spireagent/friday-RL-result-v1', 'actor': ref['actor'],
            'run': plan['run'], 'exit_code': code, 'error_type': error_type,
            'error_code': error_code, 'wall_seconds': time.monotonic() - began,
            'original_summary': summary, 'scratch_retained': str(scratch),
            'actor_config_sha256': ref['sha256'], 'retry_unknown': False,
            'cross_actor_immediate_unknown_stop': False}
        save(scratch / 'result.json', result)
        receipt = snapshot(scratch, persistent / 'evidence')
        save(persistent / 'snapshot.json', receipt)
        save(persistent / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_parser = commands.add_parser('prepare')
    prepare_parser.add_argument('--execution', type=Path, required=True)
    prepare_parser.add_argument('--learner-config', type=Path, required=True)
    prepare_parser.add_argument('--output', type=Path, required=True)
    prepare_parser.add_argument('--actors', type=int, default=8)
    prepare_parser.add_argument('--wall-seconds', type=int, default=3600)
    prepare_parser.add_argument('--episode-seconds', type=int, default=900)
    prepare_parser.add_argument('--max-games', type=int, default=20)
    prepare_parser.add_argument('--seed-namespace')
    prepare_parser.add_argument('--behavior', type=Path)
    for name in ('submit', 'run'):
        sub = commands.add_parser(name)
        sub.add_argument('--plan', type=Path, required=True)
        if name == 'submit':
            sub.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == 'prepare':
        result = prepare(args.execution, args.learner_config, args.output,
            actors=args.actors, wall_seconds=args.wall_seconds,
            episode_seconds=args.episode_seconds, max_games=args.max_games,
            behavior=json.loads(args.behavior.read_bytes()) if args.behavior else None,
            seed_namespace=args.seed_namespace)
    elif args.command == 'submit':
        result = submit(args.plan, dry_run=args.dry_run)
    else:
        result = run(args.plan)
    print(json.dumps(result, sort_keys=True))
    if (args.command == 'run'
            and (result.get('error_type') or result.get('exit_code') not in {0, None})):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
