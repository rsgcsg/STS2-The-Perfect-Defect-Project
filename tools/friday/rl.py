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
            wall_seconds: int = 3300, episode_seconds: int = 900, max_games: int = 1000,
            games_per_ledger: int = 2, runtime_source: Path | None = None,
            behavior: dict[str, Any] | None = None, seed_namespace: str | None = None,
            segment_inference: Path | None = None
            ) -> dict[str, Any]:
    execution, learner = canonical_path(execution), canonical_path(learner)
    output = canonical_path(output, existing=False)
    base, learning = json.loads(execution.read_bytes()), json.loads(learner.read_bytes())
    segment_settings = learning.get('segments')
    inference = None
    if segment_settings is not None:
        from stpd.p1.local_inference import SEGMENT_SCHEMA
        from stpd.rl.segments import SegmentConfig

        settings = SegmentConfig(**segment_settings)
        if segment_inference is None:
            raise ValueError('prepared_segment_inference_attachment_required')
        canonical_path(segment_inference)
        inference = json.loads(segment_inference.read_bytes())
        if (inference.get('schema') != SEGMENT_SCHEMA
                or inference.get('segment_steps') != settings.steps):
            raise ValueError('segment_inference_and_learner_steps_differ')
    if (learning.get('schema') not in {'stpd/rl-run-1', 'stpd/rl-learner-config-1'}
            or not NAME.fullmatch(learning.get('run', ''))):
        raise ValueError('original_learner_run_and_peer_configuration_required')
    peer = learning['peer']
    from spireagent.storage.config import validate_peer

    shared = set(peer) == {'kind', 'directory', 'archive'} and peer.get('kind') == 'local'
    if peer.get('kind') != 's3' and not shared:
        raise ValueError('original_nonsecret_R2_or_shared_archive_peer_required')
    if shared and peer['archive'].get('kind') != 's3':
        raise ValueError('Friday_shared_store_requires_R2_archive')
    validate_peer(peer)  # No credentials, client construction or network access in preparation.
    if (base.get('peer') != peer or base.get('store') != (
            peer['directory'] if shared else 's3') or base.get('retention') != RETENTION
            or base.get('schema') != 'spireagent/managed-evaluation-execution-v1'):
        raise ValueError('same_frozen_R2_execution_and_learner_peer_required')
    canonical_path(Path(base['candidate_directory']))
    if (type(actors) is not int or not 1 <= actors <= 200
            or type(wall_seconds) is not int or not 300 <= wall_seconds <= 79200
            or type(episode_seconds) is not int or not 10 <= episode_seconds <= (
                79200 if inference is not None else 1800)
            or type(games_per_ledger) is not int or not 1 <= games_per_ledger <= 10
            or wall_seconds <= (180 if inference is not None
                                else games_per_ledger * episode_seconds + 180)
            or type(max_games) is not int or not 1 <= max_games <= 10000):
        raise ValueError('bounded_one_to_two_hundred_CPU_actor_budget_required')
    defaults = learning.get('actor_defaults', {})
    behavior = behavior if behavior is not None else defaults.get('behavior', {
        'mode': 'temperature_uniform', 'temperature': 1.0,
        'uniform_mix': 0.0 if inference is not None else 0.03})
    seed_namespace = seed_namespace or learning.get('seed_namespace')
    if (not isinstance(behavior, dict)
            or set(behavior) != {'mode', 'temperature', 'uniform_mix'}
            or not isinstance(seed_namespace, str) or not seed_namespace):
        raise ValueError('explicit_original_behavior_and_seed_namespace_required')
    if inference is not None and behavior != {
            'mode': 'temperature_uniform', 'temperature': 1.0, 'uniform_mix': 0.0}:
        raise ValueError('short_segments_require_untempered_mu_without_random_mix')
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
            'games_per_ledger': games_per_ledger, 'behavior': behavior,
            'pipeline': defaults.get('pipeline', {'save_workers': 1, 'pending_limit': 2})}
        if inference is not None:
            actor['segments'] = {'inference': inference,
                                 'excluded_seeds': settings.excluded_seeds}
        path = output / f'{name}.json'
        save(path, actor)
        actor_refs.append({'actor': name, 'path': str(path), 'sha256': hashed(path)})
    plan = {'schema': SCHEMA, 'target': 'friday', 'run': learning['run'], 'peer': peer,
        'source_root': str(canonical_path(runtime_source) if runtime_source else
                           Path(__file__).resolve().parents[2]), 'actors': actor_refs,
        'execution': {'path': str(frozen), 'sha256': hashed(frozen)},
        'learner_origin': {'path': str(learner), 'sha256': hashed(learner)},
        'wall_seconds': wall_seconds, 'episode_seconds': episode_seconds,
        'games_per_ledger': games_per_ledger,
        'constraint': 'fc430', 'retry_unknown': False, 'automatic_mac_fallback': False,
        'cross_actor_immediate_unknown_stop': False}
    if inference is not None:
        plan['trajectory_encoding'] = 'short-segment-1'
        plan['inference'] = {'path': str(segment_inference), 'sha256': hashed(segment_inference)}
    save(output / 'plan.json', plan)
    return plan


def load_plan(path: Path) -> dict[str, Any]:
    canonical_path(path)
    value: dict[str, Any] = json.loads(path.read_bytes())
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
        if (value.get('trajectory_encoding') == 'short-segment-1'
                and actor.get('segments', {}).get('inference') != read_pinned(value['inference'])):
            raise ValueError('segment_inference_attachment_changed')
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


def finished_actor_results(path: Path, plan: dict[str, Any]) -> list[dict[str, str]] | None:
    """The normal parent writes result only after wait(), before its evidence copy.

    A live Slurm allocation may therefore preserve bytes without a live Actor.
    Missing results mean still active; malformed/foreign results never waive the
    original binding. The exclusive owner files prohibit restarting these actors.
    """
    results = []
    plan_sha256 = hashed(path)
    for ref in plan['actors']:
        owner_path = path.parent / 'actors' / ref['actor'] / 'owner.json'
        if not owner_path.is_file():
            return None
        owner = json.loads(owner_path.read_bytes())
        if owner.get('actor') != ref['actor'] or owner.get('plan_sha256') != plan_sha256:
            raise ValueError('finished_actor_owner_binding_changed')
        scratch = canonical_path(Path(owner['scratch']))
        result_path = scratch / 'result.json'
        if not result_path.is_file():
            return None
        result = json.loads(result_path.read_bytes())
        summary = result.get('original_summary') or {}
        if (result.get('schema') != 'spireagent/friday-RL-result-v1'
                or result.get('actor') != ref['actor'] or result.get('run') != plan['run']
                or result.get('actor_config_sha256') != ref['sha256']
                or result.get('scratch_retained') != str(scratch)
                or summary.get('schema') != 'spireagent/rl-actor-summary-1'
                or summary.get('actor') != ref['actor'] or summary.get('run') != plan['run']):
            raise ValueError('finished_actor_result_binding_changed')
        if (result.get('exit_code') != 0 or result.get('error_type') is not None
                or summary.get('status') not in {'completed', 'stopped_signal', 'stopped_before_offer'}):
            return None
        results.append({'actor': ref['actor'], 'owner_job_id': str(owner['job_id']),
                        'result_path': str(result_path), 'result_sha256': hashed(result_path)})
    return results


def submit(path: Path, *, dry_run: bool = False,
           additional_to: list[Path] | None = None, memory_gb: int = 4,
           exclude_nodes: list[str] | None = None) -> dict[str, Any]:
    if (type(memory_gb) is not int or memory_gb < 1
            or exclude_nodes is not None and (not exclude_nodes
                or any(not isinstance(node, str) or not NAME.fullmatch(node)
                       for node in exclude_nodes))):
        raise ValueError('positive_memory_and_explicit_scheduler_node_names_required')
    plan = load_plan(path)
    require_modules(Path(plan['source_root']))
    root = private_root(path.parent)
    parents: list[dict[str, Any]] = []
    if additional_to is not None:
        if not additional_to or len(set(additional_to)) != len(additional_to):
            raise ValueError('distinct_existing_same_run_parent_plans_required')
        seen_names = {a['actor'] for a in plan['actors']}
        for original in additional_to:
            parent = load_plan(original)
            finished = finished_actor_results(original, parent)
            if (original == path or parent['run'] != plan['run']
                    or not finished and (parent['peer'] != plan['peer']
                                         or parent['source_root'] != plan['source_root'])
                    or {a['actor'] for a in parent['actors']} & seen_names):
                raise ValueError('additional_actor_run_peer_source_or_names_mismatch')
            seen_names.update(a['actor'] for a in parent['actors'])
            submitted = json.loads((original.parent / 'submission.json').read_bytes())
            parents.append({'plan': {'path': str(original), 'sha256': hashed(original)},
                            'job_id': str(submitted['job_id']).split(';')[0],
                            'actors': len(parent['actors']),
                            **({'preservation_only': True, 'finished_actor_results': finished}
                               if finished else {})})
        if len({p['job_id'] for p in parents}) != len(parents):
            raise ValueError('distinct_existing_same_run_parent_jobs_required')
    with scheduler.user_submission_lock():
        if (root / 'submission-intent.json').exists():
            raise ValueError('previous_actor_submission_requires_reconciliation_no_retry')
        existing = scheduler.output(['squeue', '-h', '-u', scheduler.username(), '-n',
                                     'spireagent-f2', '-o', '%i'])
        if parents:
            active = {line.split('_')[0].strip() for line in existing.splitlines() if line.strip()}
            known = {p['job_id'] for p in parents}
            if not active <= known:
                raise ValueError('existing_friday_array_must_drain_before_new_submission')
            actor_limit = 40 if plan.get('trajectory_encoding') == 'short-segment-1' else 36
            if len(plan['actors']) + sum(p['actors'] for p in parents
                                        if p['job_id'] in active
                                        and not p.get('preservation_only')) > actor_limit:
                raise ValueError(f'concurrent_RL_actor_limit_{actor_limit}_exceeded')
        elif existing:
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
        advance = plan.get('games_per_ledger', 1) * plan['episode_seconds'] + 180
        constraint = 'fc430'
        if plan.get('trajectory_encoding') == 'short-segment-1':
            advance = 300
            features = scheduler.output(['sinfo', '-h', '-p', 'smp', '-o', '%f|%C'])
            idle_fast = sum(int(row.split('|')[1].split('/')[1]) for row in features.splitlines()
                            if 'fc430' in row.split('|')[0].split(','))
            constraint = 'fc430' if idle_fast >= count else 'fc430|c6220'
        command = ['sbatch', '--parsable', '--partition=smp', '--nodes=1', '--ntasks=1',
            '--cpus-per-task=1', f'--mem={memory_gb}G', f'--time={minutes}', '--no-requeue',
            f'--signal=B:USR1@{advance}', '--job-name=spireagent-f2',
            f'--array=0-{count - 1}%{count}', f'--constraint={constraint}',
            f'--output={logs}/%A_%a.out', f'--error={logs}/%A_%a.err',
            *(['--exclude=' + ','.join(exclude_nodes)] if exclude_nodes else []), str(script)]
        result = {'schema': 'spireagent/friday-RL-submit-v1', 'command': command,
            'cores': count, 'idle_smp_cores': idle, 'queued_smp_jobs': queued,
            'dry_run': dry_run, 'memory_gb_per_actor': memory_gb,
            'excluded_nodes': exclude_nodes or [], 'cross_actor_immediate_unknown_stop': False,
            **({'additional_to': parents} if parents else {})}
        if not dry_run:
            save(root / 'submission-intent.json', {**result, 'at': time.time()})
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

    actor_process = None
    forwarded_actor_signals: list[int] = []

    def stop(number, _frame):
        # RL1 checks STOP/signalled between complete small ledgers, not within a ledger.
        (work / 'STOP').touch()
        if (number == signal.SIGUSR1 and actor_process is not None
                and actor_process.poll() is None and not forwarded_actor_signals):
            try:
                actor_process.send_signal(number)
                forwarded_actor_signals.append(number)
            except ProcessLookupError:
                pass

    previous = {number: signal.signal(number, stop)
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)}
    began = time.monotonic()
    code = None
    error_type = error_code = None
    summary = None
    try:
        with ((scratch / 'actor.out').open('wb') as stdout,
              (scratch / 'actor.err').open('wb') as stderr):
            actor_process = subprocess.Popen(
                [sys.executable, '-m', 'spireagent.workbench.rl_actor',
                 '--config', ref['path'], '--work', str(work)], stdout=stdout, stderr=stderr)
            code = actor_process.wait()
        if (work / 'actor-summary.json').is_file():
            summary = json.loads((work / 'actor-summary.json').read_bytes())
        uncertain = any(game.get('status') in {'unknown', 'save_failed'}
                        or (game.get('status') not in {'saved', 'finished'} and not game.get('shard')
                            and not (plan.get('trajectory_encoding') == 'short-segment-1'
                                and game.get('status') == 'censored'
                                and game.get('cleanup_accepted') is True
                                and game.get('segment_prefix_close') is True))
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
        result = {'schema': 'spireagent/friday-RL-result-v1', 'actor': ref['actor'],
            'run': plan['run'], 'exit_code': code, 'error_type': error_type,
            'error_code': error_code, 'wall_seconds': time.monotonic() - began,
            'original_summary': summary, 'scratch_retained': str(scratch),
            'actor_config_sha256': ref['sha256'], 'retry_unknown': False,
            'cross_actor_immediate_unknown_stop': False,
            'forwarded_actor_signals': forwarded_actor_signals}
        save(scratch / 'result.json', result)
        try:
            receipt = snapshot(scratch, persistent / 'evidence')
            save(persistent / 'snapshot.json', receipt)
            save(persistent / 'result.json', result)
        finally:
            # Keep stop handlers installed through copy, byte verification and receipt.
            for number, handler in previous.items():
                signal.signal(number, handler)
    return result


def resume_snapshot(path: Path, actor: str) -> dict[str, Any]:
    """Resume only evidence copying after the original allocation is terminal."""
    plan = json.loads(path.read_bytes())
    reference = next((row for row in plan['actors'] if row['actor'] == actor), None)
    if reference is None:
        raise ValueError('actor_not_in_frozen_plan')
    read_pinned(reference)
    persistent = private_root(path.parent / 'actors' / actor)
    owner = json.loads((persistent / 'owner.json').read_bytes())
    if owner['plan_sha256'] != hashed(path) or owner['actor'] != actor:
        raise ValueError('saved_actor_owner_binding_changed')
    status = subprocess.check_output(['sacct', '-n', '-P', '-j', owner['job_id'],
        '--format=JobIDRaw,State'], text=True)
    states = [line.split('|')[1].split()[0].rstrip('+')
              for line in status.splitlines() if len(line.split('|')) >= 2]
    terminal = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'OUT_OF_MEMORY',
                'NODE_FAIL', 'PREEMPTED', 'BOOT_FAIL', 'DEADLINE', 'REVOKED'}
    if not states or any(state not in terminal for state in states):
        raise ValueError('original_actor_allocation_must_be_terminal')
    scratch = Path(owner['scratch'])
    result = json.loads((scratch / 'result.json').read_bytes())
    if result['actor'] != actor or result['run'] != plan['run']:
        raise ValueError('saved_actor_result_binding_changed')
    receipt = snapshot(scratch, persistent / 'evidence')
    save(persistent / 'snapshot.json', receipt)
    save(persistent / 'result.json', result)
    return {'actor': actor, 'copy_resumed': True, 'new_games': 0, 'snapshot': receipt}


def learn(configuration: Path, work: Path, execution: Path, actors_output: Path, *,
          actors: int, seed_namespace: str, episode_seconds: int = 14400,
          memory_gb: int = 4) -> dict[str, Any]:
    """The GPU owner starts its scorer, then submits the original CPU actor adapter.

    The transient scorer capability reaches sbatch through its inherited environment;
    attachment/plan/log files contain only public endpoint and identity metadata.
    Resume uses the same explicit learner CP and fresh, never-offered actor names.
    """
    from spireagent.resource_slot import require_managed_slot
    from spireagent.workbench.global_evidence_store import publish_detached_p1_model
    from spireagent.workbench.managed_segment_store import read_segment
    from stpd.p1.local_inference import AUTH_ENV
    from stpd.rl.learner import LearnerConfig
    from stpd.rl.segment_learner import SegmentLearner, SegmentLearnerIO

    configuration = canonical_path(configuration)
    config = LearnerConfig.read(configuration)
    source = Path(__file__).resolve().parents[2]
    require_modules(source)
    require_managed_slot()
    if (sys.platform != 'linux' or not os.environ.get('SLURM_JOB_ID')
            or config.device != 'cuda' or config.segments is None
            or actors != config.segments.inference_batch):
        raise ValueError('allocated_Friday_GPU_and_matching_segment_actor_count_required')
    work = canonical_path(work, existing=False)
    canonical_path(actors_output, existing=False)
    if work.exists() or actors_output.exists():
        raise ValueError('fresh_learner_work_and_never_offered_actor_output_required')
    work.mkdir(mode=0o700, parents=True)

    def stop(_number, _frame):
        (work / 'STOP').touch()

    previous = {number: signal.signal(number, stop)
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)}
    prior_auth = os.environ.get(AUTH_ENV)
    try:
        learner = SegmentLearner(config, work,
            SegmentLearnerIO(read_segment, publish_detached_p1_model))
        try:
            prepare(execution, configuration, actors_output, actors=actors,
                wall_seconds=int(config.wall_seconds)+600, episode_seconds=episode_seconds,
                games_per_ledger=10, runtime_source=source, seed_namespace=seed_namespace,
                segment_inference=work/'inference.json')
            os.environ.update(learner.pool.environment())
            submit(actors_output/'plan.json', memory_gb=memory_gb)
        except BaseException:
            # Preserve the initialized learner/CP even if the scheduler offer has
            # no reply. submit's sticky intent requires original-job reconciliation.
            (work/'STOP').touch()
            learner.run()
            raise
        return learner.run()
    finally:
        if prior_auth is None:
            os.environ.pop(AUTH_ENV, None)
        else:
            os.environ[AUTH_ENV] = prior_auth
        for number, handler in previous.items():
            signal.signal(number, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_parser = commands.add_parser('prepare')
    prepare_parser.add_argument('--execution', type=Path, required=True)
    prepare_parser.add_argument('--learner-config', type=Path, required=True)
    prepare_parser.add_argument('--output', type=Path, required=True)
    prepare_parser.add_argument('--actors', type=int, default=8)
    prepare_parser.add_argument('--runtime-source', type=Path,
                                help='canonical frozen release source; tooling may be newer')
    prepare_parser.add_argument('--wall-seconds', type=int, default=3300)
    prepare_parser.add_argument('--episode-seconds', type=int, default=900)
    prepare_parser.add_argument('--max-games', type=int, default=1000)
    prepare_parser.add_argument('--games-per-ledger', type=int, default=2)
    prepare_parser.add_argument('--seed-namespace')
    prepare_parser.add_argument('--behavior', type=Path)
    prepare_parser.add_argument('--segment-inference', type=Path,
                                help='normal central scorer attachment; no credential values')
    for name in ('submit', 'run'):
        sub = commands.add_parser(name)
        sub.add_argument('--plan', type=Path, required=True)
        if name == 'submit':
            sub.add_argument('--dry-run', action='store_true')
            sub.add_argument('--additional-to', type=Path, nargs='+')
            sub.add_argument('--memory-gb', type=int, default=4)
            sub.add_argument('--exclude-node', action='append', dest='exclude_nodes')
    save_parser = commands.add_parser('save')
    save_parser.add_argument('--plan', type=Path, required=True)
    save_parser.add_argument('--actor', required=True)
    learn_parser = commands.add_parser('learn')
    learn_parser.add_argument('--config', type=Path, required=True)
    learn_parser.add_argument('--work', type=Path, required=True)
    learn_parser.add_argument('--execution', type=Path, required=True)
    learn_parser.add_argument('--actors-output', type=Path, required=True)
    learn_parser.add_argument('--actors', type=int, default=32)
    learn_parser.add_argument('--seed-namespace', required=True)
    learn_parser.add_argument('--episode-seconds', type=int, default=14400)
    learn_parser.add_argument('--memory-gb', type=int, default=4)
    args = parser.parse_args()
    os.umask(0o077)
    if (args.command in {'run', 'submit'} and not getattr(args, 'dry_run', False)
            and load_plan(args.plan).get('trajectory_encoding') != 'short-segment-1'):
        raise ValueError('whole_episode_live_training_retired_use_segments')
    if args.command == 'learn':
        result = learn(args.config, args.work, args.execution, args.actors_output,
            actors=args.actors, seed_namespace=args.seed_namespace,
            episode_seconds=args.episode_seconds, memory_gb=args.memory_gb)
    elif args.command == 'prepare':
        result = prepare(args.execution, args.learner_config, args.output,
            actors=args.actors, wall_seconds=args.wall_seconds,
            episode_seconds=args.episode_seconds, max_games=args.max_games,
            games_per_ledger=args.games_per_ledger, runtime_source=args.runtime_source,
            behavior=json.loads(args.behavior.read_bytes()) if args.behavior else None,
            seed_namespace=args.seed_namespace, segment_inference=args.segment_inference)
    elif args.command == 'submit':
        result = submit(args.plan, dry_run=args.dry_run, additional_to=args.additional_to,
                        memory_gb=args.memory_gb, exclude_nodes=args.exclude_nodes)
    elif args.command == 'save':
        result = resume_snapshot(args.plan, args.actor)
    else:
        result = run(args.plan)
    print(json.dumps(result, sort_keys=True))
    if (args.command == 'run'
            and (result.get('error_type') or result.get('exit_code') not in {0, None})):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
