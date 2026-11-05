#!/usr/bin/env python3
"""One bounded Friday Slurm watcher; actors/evaluations retain frozen runtime owners."""
from __future__ import annotations

import argparse
import collections
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import evaluate
from batch import hashed, private_root, read_published, save
from spireagent.json_boundary import json_bytes
from spireagent.rl_exchange import Exchange

scheduler = evaluate.scheduler
SCHEMA = 'spireagent/friday-rl-watch-config-1'


def read_config(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_bytes())
    if (value.get('schema') != SCHEMA or value.get('evaluation_mode') not in {'submit', 'direct'}
            or not 1 <= value.get('wall_seconds', 0) <= 86400
            or not 60 <= value.get('poll_seconds', 900) <= 1800
            or not 14400 <= value.get('evaluation_seconds', 18000) <= 18000
            or type(value.get('started_at')) not in {int, float}
            or not math.isfinite(value['started_at'])):
        raise ValueError('bounded_RL_watch_configuration_required')
    limit = value.get('unknown_stop_fraction', 0.05)
    if (type(limit) not in (int, float) or not math.isfinite(limit) or not 0 < limit <= 1
            or type(value.get('floor_stop_enabled', True)) is not bool):
        raise ValueError('invalid_explicit_health_threshold')
    plan = json.loads(Path(value['actor_plan']).read_bytes())
    if plan['run'] != value['run'] or plan['peer'] != value['peer']:
        raise ValueError('watch_actor_run_peer_binding_mismatch')
    # Pin both the original runtime and actual submitted actor allocation.
    if plan['source_root'] != value['runtime_source']:
        raise ValueError('watch_frozen_runtime_binding_mismatch')
    evaluation_source = value.get('evaluation_runtime_source')
    if evaluation_source is not None:
        source = Path(evaluation_source)
        if (not source.is_absolute() or source.resolve() != source or source.is_symlink()
                or not (source / 'tools/friday/evaluate.py').is_file()):
            raise ValueError('canonical_frozen_evaluation_runtime_required')
    if value.get('natural_quick') is not None:
        natural = value['natural_quick']
        if (value['evaluation_mode'] != 'direct' or not isinstance(natural, dict)
                or not isinstance(natural.get('baseline_cache'), list)
                or not natural['baseline_cache']
                or set(natural.get('baseline_model', {})) !=
                    {'model_artifact_id', 'model_sha256'}
                or not isinstance(natural.get('baseline_unknown_seeds', []), list)):
            raise ValueError('explicit_direct_natural_quick_baseline_required')
        if {tier['name'] for tier in tiers(value)} != {'quick'}:
            raise ValueError('natural_quick_requires_explicit_quick_only_schedule')
    submission = json.loads((Path(value['actor_plan']).parent / 'submission.json').read_bytes())
    if str(submission['job_id']).split(';')[0] != str(value['actor_job_id']):
        raise ValueError('watch_actor_job_binding_mismatch')
    if value.get('evaluation_tiers') is not None:
        tiers(value)
        from spireagent.workbench.reference_suite import load_manifest
        from stpd.evaluation.suites import RL3_LEARNING_CURVE_SUITE_ID

        manifest = Path(value['evaluation_manifest'])
        _, frozen = load_manifest(manifest, hashed(manifest))
        if frozen['suite_id'] != RL3_LEARNING_CURVE_SUITE_ID or len(frozen['seeds']) != 100:
            raise ValueError('RL3_registered100_evaluation_manifest_required')
    if value.get('additional_actor_plans') is not None:
        extras = value['additional_actor_plans']
        if (not isinstance(extras, list) or not extras
                or any(not isinstance(ref, dict) or set(ref) != {'plan', 'job_id'}
                       for ref in extras)):
            raise ValueError('explicit_additional_actor_plan_bindings_required')
        names: set[str] = set()
        jobs: set[str] = set()
        count = 0
        for allocation in actor_allocations(value):
            frozen = (evaluate.read_pinned(allocation['reference'])
                      if allocation.get('reference') else
                      json.loads(Path(allocation['plan']).read_bytes()))
            submitted = json.loads((Path(allocation['plan']).parent / 'submission.json').read_bytes())
            current_names = {ref['actor'] for ref in frozen['actors']}
            if (frozen['run'] != value['run'] or frozen['peer'] != value['peer']
                    or frozen['source_root'] != value['runtime_source']
                    or str(submitted['job_id']).split(';')[0] != allocation['job_id']
                    or allocation['job_id'] in jobs or names & current_names):
                raise ValueError('additional_actor_plan_or_submission_binding_mismatch')
            names.update(current_names)
            jobs.add(allocation['job_id'])
            count += len(frozen['actors'])
        if count > 36:
            raise ValueError('concurrent_RL_actor_limit_36_exceeded')
    return value


def actor_allocations(config: dict[str, Any]) -> list[dict[str, Any]]:
    # Existing plans and submission receipts remain each allocation's owners.
    return [{'plan': config.get('actor_plan'), 'job_id': str(config['actor_job_id'])}] + [
        {'plan': ref['plan']['path'], 'job_id': str(ref['job_id']), 'reference': ref['plan']}
        for ref in config.get('additional_actor_plans', [])]


def tiers(config: dict[str, Any]) -> list[dict[str, Any]]:
    values = config.get('evaluation_tiers', [])
    expected = {'quick': (14400, 20), 'milestone': (28800, 100)}
    if (not isinstance(values, list) or not 1 <= len(values) <= 2
            or {v.get('name') for v in values if isinstance(v, dict)}
                not in ({'quick'}, set(expected))
            or len({v.get('name') for v in values if isinstance(v, dict)}) != len(values)):
        raise ValueError('explicit_quick20_optional_milestone100_tiers_required')
    for value in values:
        if ((value.get('seconds'), value.get('seed_prefix')) != expected[value['name']]
                or type(value.get('seconds')) is not int
                or type(value.get('seed_prefix')) is not int
                or type(value.get('wall_seconds', 7200)) is not int
                or not 1200 <= value.get('wall_seconds', 7200) <= 21600):
            raise ValueError('bounded_RL3_evaluation_tier_required')
    return values


def evaluate_result(path: Path, version: int, updates: int, *,
                    confirmation: bool = False, kind: str | None = None,
                    expected_planned: int | None = None) -> dict[str, Any]:
    result = json.loads(path.read_bytes())
    report = result['ledger_report']
    groups = report['groups']
    floors = [row['max_observed_floor'] for row in report['slots']
              if row['status'] == 'finished' and row.get('max_observed_floor') is not None]
    planned = expected_planned or (40 if confirmation else 20)
    complete = (report['score_admitted'] is True and report['planned'] == planned
                and report['not_started'] == report['reserved'] == report['save_pending'] == 0
                and report['save_failed'] == report['saved_unscored'] == 0
                and report['trajectory_pending'] == report['censored'] == 0
                and report['win'] + report['loss'] + report['unknown'] == planned)
    return {'version': version, 'updates': updates, 'wins': report['win'],
            'evaluation_kind': kind or ('confirmation' if confirmation else 'fixed20'),
            'losses': report['loss'], 'unknown': report['unknown'],
            'mean_max_floor': sum(floors) / len(floors) if floors else None,
            'evaluated_at': time.time(), 'complete': complete,
            'score_admitted': report['score_admitted'], 'planned': report['planned'],
            'scored': sum(group['scored'] for group in groups),
            'result_path': str(path), 'result_sha256': hashed(path)}


def stop_reason(now: float, config: dict[str, Any], games: list[dict[str, Any]],
                event: dict[str, Any] | None, evaluations: list[dict[str, Any]]) -> str | None:
    if now >= config['started_at'] + config['wall_seconds']:
        return 'first_round_wall_limit'
    limit = config.get('unknown_stop_fraction', 0.05)
    if games and sum(g['status'] == 'unknown' for g in games) / len(games) > limit:
        return f'training_unknown_fraction_above_{100 * limit:g}_percent'
    if event:
        if event['kind'] == 'failed':
            return 'learner_failed'
        for name in ('loss', 'policy_loss', 'kl', 'gradient_norm'):
            if name in event and not math.isfinite(event[name]):
                return 'learner_nonfinite_' + name
        threshold = config.get('kl_stop_threshold')
        if threshold is not None and event.get('kl', 0) > threshold:
            return 'learner_KL_above_configured_threshold'
    completed = [row for row in evaluations if row['complete']
                 and row.get('evaluation_kind', 'fixed20') == 'fixed20']
    if (config.get('floor_stop_enabled', True) and len(completed) >= 2
            and all(row['mean_max_floor'] is not None
            and row['mean_max_floor'] < 11.75 for row in completed[-2:])):
        return 'two_evaluations_below_baseline_by_more_than_two_floors'
    return None


TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'NODE_FAIL',
            'OUT_OF_MEMORY', 'PREEMPTED', 'BOOT_FAIL', 'DEADLINE', 'REVOKED'}


def job_accounting(job_id: str) -> dict[str, Any]:
    raw = subprocess.check_output(['sacct', '-n', '-P', '-j', job_id,
                                   '--format=JobIDRaw,State,ExitCode,Submit,Eligible,Start,End,ElapsedRaw'], text=True)
    rows = []
    for line in raw.splitlines():
        fields = line.split('|')
        if len(fields) >= 3 and (fields[0] == job_id if '.' in job_id
                                 else '.' not in fields[0]):
            rows.append({'job_id': fields[0], 'state': fields[1].split()[0].rstrip('+'),
                         'exit_code': fields[2], **({'timing': dict(zip(
                             ('submit', 'eligible', 'start', 'end', 'elapsed_seconds'), fields[3:8], strict=True))}
                             if len(fields) >= 8 else {})})
    return {'job_id': job_id, 'rows': rows,
            'terminal': bool(rows) and all(row['state'] in TERMINAL for row in rows),
            'all_completed': bool(rows) and all(row['state'] == 'COMPLETED' for row in rows)}


class Watcher:
    def __init__(self, config: dict[str, Any], work: Path):
        self.config, self.work = config, private_root(work)
        self.exchange = Exchange(config['peer'], config['run'])
        self.state_path = work / 'watch-state.json'
        self.state: dict[str, Any] = (json.loads(self.state_path.read_bytes()) if self.state_path.exists() else
                      {'config_sha256': hashlib.sha256(json_bytes(config)).hexdigest(),
                       'shards': {}, 'evaluations': [], 'pending': None, 'stop': None,
                       'last_evaluation_at': config['started_at']})
        if self.state['config_sha256'] != hashlib.sha256(json_bytes(config)).hexdigest():
            raise ValueError('watch_resume_configuration_changed')
        self.direct: subprocess.Popen | None = None
        self.signalled = False
        self.maintenance_requested = False

    def persist(self) -> None:
        save(self.state_path, self.state)

    def publish(self, kind: str, **fields: Any) -> dict[str, Any]:
        value = {'schema': 'spireagent/rl-watch-status-1', 'run': self.config['run'],
                 'at': time.time(), 'kind': kind, **fields}
        key = f'{self.exchange.prefix}status/{time.time_ns():020d}.json'
        self.exchange.blobs.put_if_absent(key, json_bytes(value))
        return value

    def telemetry(self, kind: str, **fields: Any) -> dict[str, Any] | None:
        try:
            return self.publish(kind, **fields)
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            print(json.dumps({'kind': 'watcher_telemetry_failed', 'event': kind,
                              'error_type': type(error).__name__}), file=sys.stderr, flush=True)
            return None

    def signal_once(self, job_id: str) -> dict[str, Any]:
        """Durable at-most-once signal intent; uncertain scheduler effects need reconciliation."""
        signals = self.state.setdefault('stop_signals', {})
        if job_id in signals:
            return cast(dict[str, Any], signals[job_id])
        signals[job_id] = {'state': 'intent', 'at': time.time()}
        self.persist()
        try:
            command = ['scancel', '--signal=USR1']
            if not re.fullmatch(r'[1-9][0-9]*\.[0-9]+', job_id):
                command.append('--batch')
            result = subprocess.run([*command, job_id],
                                    check=False, capture_output=True)
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            signals[job_id].update(state='outcome_unknown', error_type=type(error).__name__)
        else:
            signals[job_id].update(state='response_received', exit_code=result.returncode)
        self.persist()
        return cast(dict[str, Any], signals[job_id])

    def stop(self, reason: str) -> None:
        if self.state['stop'] is None:
            self.state['stop'] = reason
            self.persist()
        actor_signals = []
        for allocation in actor_allocations(self.config):
            save(Path(allocation['plan']).parent / 'STOP',
                 {'reason': self.state['stop'], 'run': self.config['run']})
            actor_signals.append(self.signal_once(allocation['job_id']))
        actor_signal = actor_signals[0]
        if 'drain_started_at' not in self.state:
            self.state['drain_started_at'] = time.time()
            self.persist()
        try:
            self.exchange.request_stop(self.state['stop'])
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            self.state['learner_stop_pending'] = True
            self.state['learner_stop_error_type'] = type(error).__name__
        else:
            self.state['learner_stop_pending'] = False
        self.persist()
        if self.state['pending']:
            pending = self.state['pending']
            save(Path(pending['plan']).parent / 'STOP', {'reason': self.state['stop']})
            if pending.get('job_id'):
                self.signal_once(str(pending['job_id']))
        self.telemetry('stop_requested', reason=self.state['stop'],
                     actor_signal_exit_code=actor_signal.get('exit_code'),
                     learner_stop_pending=self.state['learner_stop_pending'])

    def prepare_evaluation(self, model: dict[str, Any], now: float, *,
                           confirmation: bool = False, tier: dict[str, Any] | None = None,
                           due_at: float | None = None) -> None:
        if tier is not None and tier['name'] == 'quick' and self.config.get('natural_quick'):
            return self.prepare_natural_quick(model, tier, due_at)
        kind = tier['name'] if tier else ('confirmation' if confirmation else 'fixed20')
        if (tier is not None and self.config['evaluation_mode'] == 'direct'
                and int(os.environ.get('SLURM_CPUS_PER_TASK', '0')) < 4):
            raise ValueError('direct_RL3_evaluation_requires_four_allocated_CPUs')
        root = self.work / 'evaluations' / f"{kind}-v{model['version']:06d}-{time.time_ns()}"
        root.mkdir(parents=True, mode=0o700)
        package = self.exchange.materialize_model(model, self.work / 'models')
        execution = json.loads(Path(self.config['evaluation_execution']).read_bytes())
        if tier is not None:
            execution['pipeline'] = {'pending_limit': 8, 'save_workers': 2}
        execution['models'] = {'latest': {'artifact_id': model['model_artifact_id'],
                                           'package_directory': str(package)}}
        execution_path = root / 'input-execution.json'
        save(execution_path, execution)
        plan_path = root / 'plan' / 'plan.json'
        evaluate.prepare(execution_path, plan_path.parent, selections={'latest': 'latest'},
            workers=4 if tier else (1 if self.config['evaluation_mode'] == 'direct'
                else self.config.get('evaluation_workers', 4)),
            wall_seconds=tier.get('wall_seconds', 7200) if tier else (10800 if confirmation else 7200),
            manifest_path=Path(self.config['evaluation_manifest']),
            seed_prefix=tier['seed_prefix'] if tier else (40 if confirmation else 20),
            runtime_source=Path(self.config.get('evaluation_runtime_source',
                                                self.config['runtime_source'])),
            observation_report=tier is not None)
        pending = {'plan': str(plan_path), 'version': model['version'],
                   'updates': model.get('training', {}).get('updates', 0),
                   'state': 'submission_intent', 'job_id': None, 'evaluation_kind': kind,
                   'model_artifact_id': model['model_artifact_id'],
                   'planned': tier['seed_prefix'] if tier else (40 if confirmation else 20),
                   'due_at': due_at, 'prepared_at': time.time(),
                   'plan_sha256': hashed(plan_path)}
        self.state['pending'] = pending
        if not confirmation and tier is None:
            self.state['last_evaluation_at'] = now
        self.persist()
        if self.config['evaluation_mode'] == 'submit':
            try:
                result = evaluate.submit(plan_path, constraint='fc430')
            except ValueError as error:
                # This explicit pre-sbatch capacity refusal owns no allocation or world offer.
                if str(error) != 'requested_evaluation_workers_exceed_current_fair_share':
                    raise
                self.state['pending'] = None
                if not confirmation and tier is None:
                    self.state['last_evaluation_at'] = (now - self.config.get('evaluation_seconds', 18000)
                                                        + self.config.get('poll_seconds', 900))
                self.persist()
                self.publish('attention', reason='evaluation_capacity_unavailable_no_submission')
                return
            pending.update(state='submitted', job_id=str(result['job_id']).split(';')[0])
        else:
            plan = evaluate.load_plan(plan_path)
            launcher = root / 'launch.sh'
            launcher.write_text(evaluate.launcher(plan_path, plan))
            launcher.chmod(0o700)
            with (root / 'direct.out').open('wb') as stdout, (root / 'direct.err').open('wb') as stderr:
                self.direct = subprocess.Popen(['bash', str(launcher)], stdout=stdout, stderr=stderr)
            pending.update(state='direct_running', pid=self.direct.pid)
        self.persist()
        self.publish('evaluation_started', **pending)

    def prepare_natural_quick(self, model: dict[str, Any], tier: dict[str, Any],
                              due_at: float | None) -> None:
        import quick

        if int(os.environ.get('SLURM_CPUS_PER_TASK', '0')) < 4:
            raise ValueError('direct_RL3_evaluation_requires_four_allocated_CPUs')
        config = self.config['natural_quick']
        models = {'current': {k: model[k] for k in ('model_artifact_id', 'model_sha256')},
                  'baseline': config['baseline_model']}
        execution = json.loads(Path(self.config['evaluation_execution']).read_bytes())
        execution['pipeline'] = {'pending_limit': 8, 'save_workers': 2}
        execution['models'] = {role: {'artifact_id': record['model_artifact_id'],
            'package_directory': str(self.exchange.materialize_model(record, self.work / 'models'))}
            for role, record in models.items()}
        root = self.work / 'evaluations' / f"natural20-v{model['version']:06d}-{time.time_ns()}"
        input_path = self.work / 'natural-quick-execution.json'
        save(input_path, execution)
        quick.prepare(input_path, root, manifest_path=Path(self.config['evaluation_manifest']),
            runtime_source=Path(self.config.get('evaluation_runtime_source',
                                                self.config['runtime_source'])),
            models=models, baseline_cache=self.state.get('natural_quick_baseline_cache',
                                                         config['baseline_cache']),
            baseline_unknown_seeds=config.get('baseline_unknown_seeds', []),
            version=model['version'], updates=model.get('training', {}).get('updates', 0),
            wall_seconds=tier.get('wall_seconds', 7200))
        plan_path = root / 'plan.json'
        pending = {'plan': str(plan_path), 'version': model['version'],
                   'updates': model.get('training', {}).get('updates', 0),
                   'state': 'submission_intent', 'job_id': None,
                   'evaluation_kind': 'quick_natural20', 'planned': 20, 'due_at': due_at,
                   'model_artifact_id': model['model_artifact_id'], 'prepared_at': time.time(),
                   'plan_sha256': hashed(plan_path)}
        self.state['pending'] = pending
        self.persist()
        launcher = root / 'launch.sh'
        launcher.write_text(quick.launcher(plan_path, quick.load_plan(plan_path)))
        with (root / 'direct.out').open('wb') as stdout, (root / 'direct.err').open('wb') as stderr:
            self.direct = subprocess.Popen(['bash', str(launcher)], stdout=stdout, stderr=stderr)
        pending.update(state='direct_running', pid=self.direct.pid)
        self.persist()
        self.publish('evaluation_started', **pending)

    def finish_natural_quick(self, result_path: Path, pending: dict[str, Any]) -> None:
        import quick

        original = json.loads(result_path.read_bytes())
        count = original.get('paired_n')
        if (original.get('schema') != quick.SCHEMA
                or original.get('plan_sha256') != pending['plan_sha256']
                or original.get('model_artifact_id') != pending['model_artifact_id']
                or original.get('version') != pending['version']
                or original.get('updates') != pending['updates']
                or type(count) is not int or not 0 <= count <= 20
                or len(original.get('paired_seeds', [])) != count
                or len(set(original.get('paired_seeds', []))) != count
                or original.get('complete') is not (count == 20)
                or original['comparison']['paired_n'] != count):
            raise ValueError('natural_quick_original_result_binding_mismatch')
        result = {'version': pending['version'], 'updates': pending['updates'],
                  'model_artifact_id': pending['model_artifact_id'],
                  'evaluation_kind': 'quick_natural20', 'complete': original['complete'],
                  'paired_n': count, 'planned': 20, 'mean_max_floor': original['current_mean'],
                  'baseline_paired_mean_floor': original['baseline_mean'],
                  'comparison': original['comparison'], 'due_at': pending['due_at'],
                  'evaluated_at': original['finished_at'], 'result_path': str(result_path),
                  'result_sha256': hashed(result_path), 'report': original['report'],
                  'reason': original['reason'], 'raw_ledger_flags_unchanged': True}
        if not any(e.get('result_sha256') == result['result_sha256']
                   for e in self.state['evaluations']):
            self.state['evaluations'].append(result)
        self.state['natural_quick_baseline_cache'] = original['baseline_cache']
        if original['reason'] == 'non_unknown_or_unsettled_batch_requires_owner_read':
            pending['state'] = 'attention_required'
        else:
            self.state['pending'] = None
        self.persist()
        self.append_reference('quick_natural20', result['result_sha256'], result)
        self.publish('evaluation_finished', evaluation=result)

    def collect_evaluation(self):
        pending = self.state['pending']
        if pending is None:
            return
        if pending.get('state') == 'attention_required':
            return
        result_path = Path(pending['plan']).parent / 'result.json'
        if result_path.exists() and read_published(result_path) is None:
            return
        if read_published(result_path) is not None:
            if pending.get('evaluation_kind') == 'quick_natural20':
                self.finish_natural_quick(result_path, pending)
                return
            result = evaluate_result(result_path, pending['version'], pending['updates'],
                confirmation=pending.get('evaluation_kind') == 'confirmation',
                kind=pending.get('evaluation_kind'), expected_planned=pending.get('planned'))
            if pending.get('plan_sha256') is not None:
                original = json.loads(result_path.read_bytes())
                if original['plan_sha256'] != pending['plan_sha256']:
                    raise ValueError('evaluation_result_plan_binding_mismatch')
            result['model_artifact_id'] = pending.get('model_artifact_id')
            result['due_at'] = pending.get('due_at')
            if pending.get('job_id'):
                result['scheduler_accounting'] = job_accounting(pending['job_id'])
            if self.config.get('evaluation_tiers') is not None:
                result.update(self.evaluation_report(result_path, pending))
            existing = next((r for r in self.state['evaluations']
                             if r['result_sha256'] == result['result_sha256']), None)
            if existing is None:
                self.state['evaluations'].append(result)
            else:
                result = existing
            self.persist()
            if self.config.get('evaluation_tiers') is not None and result['complete']:
                self.candidate_reference(result)
            self.state['pending'] = None
            self.persist()
            self.publish('evaluation_finished', evaluation=result)
            if result['complete']:
                evaluation_file = result_path.parent / 'tensorboard-evaluation.json'
                save(evaluation_file, {k: result[k] for k in ('version', 'updates', 'wins',
                        'mean_max_floor', 'unknown', 'evaluated_at', 'evaluation_kind', 'planned')})
                peer_file = self.work / 'peer.json'
                save(peer_file, self.config['peer'])
                subprocess.run([sys.executable, '-m', 'spireagent.rl_tensorboard',
                    '--peer-config', str(peer_file), '--run', self.config['run'],
                    '--evaluation', str(evaluation_file), '--work', str(self.work / 'tb')], check=True)
        elif pending['state'] == 'submission_intent':
            self.publish('attention', reason='evaluation_submission_outcome_unknown_no_retry', pending=pending)
        elif pending.get('job_id'):
            accounting = job_accounting(pending['job_id'])
            if accounting['terminal']:
                pending['state'] = 'terminal_without_result'
                self.persist()
                self.publish('attention', reason='evaluation_job_terminal_without_result_no_retry',
                             pending=pending, accounting=accounting)
        elif self.direct is not None and self.direct.poll() is not None:
            self.publish('attention', reason='evaluation_process_exited_without_result', pending=pending)

    def schedule_tiers(self, model: dict[str, Any] | None, now: float) -> None:
        schedules = self.state.setdefault('tier_next_due', {})
        due = []
        for tier in tiers(self.config):
            at = schedules.setdefault(tier['name'], self.config['started_at'] + tier['seconds'])
            while at <= now:
                due.append((at, tier))
                at += tier['seconds']
            schedules[tier['name']] = at
        if not due:
            return
        due.sort(key=lambda item: (item[0], item[1]['seed_prefix']), reverse=True)
        selected = due[0]
        records = self.state.setdefault('evaluation_schedule', [])
        intent = None
        for at, tier in reversed(due):
            reason = ('pending_evaluation' if self.state['pending'] is not None else
                      'maintenance' if self.maintenance_requested else
                      'model_unavailable' if model is None else
                      'covered_by_milestone' if at == selected[0] and tier != selected[1] else
                      'missed_scheduled_slot' if (at, tier) != selected else None)
            record = {'due_at': at, 'tier': tier['name'],
                      'version_observed': (model or {}).get('version'),
                      'model_artifact_id': (model or {}).get('model_artifact_id'),
                      'state': 'skipped' if reason else 'intent', 'reason': reason}
            records.append(record)
            if reason is None:
                intent = record
        self.persist()  # consume the wall-clock slot before preparation/submission
        if intent is not None:
            assert model is not None  # An intent is selected only for an available model.
            previous = self.state.get('last_evaluated_version_observed')
            if previous is not None and model['version'] > previous + 1:
                intent['unevaluated_version_range'] = [previous + 1, model['version'] - 1]
            self.state['last_evaluated_version_observed'] = model['version']
            self.persist()
            try:
                self.prepare_evaluation(model, now, tier=selected[1], due_at=selected[0])
            except Exception as error:
                intent.update(state='preparation_or_submission_failed', error_type=type(error).__name__)
                self.persist()
                raise
            intent['state'] = 'pending' if self.state['pending'] else 'skipped_capacity'
            self.persist()

    def evaluation_report(self, result_path: Path, pending: dict[str, Any]) -> dict[str, Any]:
        """One saved ledger -> existing validated trajectory/report implementation."""
        from stpd.evaluation.rl_observation import write_report

        plan, original, files = evaluate.read_observation_report(result_path, Path(pending['plan']))
        metadata = original['observation_report']
        cohort = files['cohort']
        protocol = {'manifest_sha256': plan['manifest']['sha256'],
                    'seed_prefix': plan.get('seed_prefix'),
                    'seeds': [row['seed'] for row in cohort['rows']],
                    'host': cohort['host_kind'], 'character': cohort['character'],
                    'ascension': cohort['ascension'], 'observed': cohort['protocol_observed']}
        key = hashlib.sha256(json_bytes(protocol)).hexdigest()
        cohorts = {'current': cohort}
        comparable = [e for e in self.state['evaluations']
                      if e.get('comparison_key') == key and e.get('cohort') is not None]
        if comparable:
            cohorts['baseline'] = comparable[0]['cohort']
            if len(comparable) > 1:
                cohorts['previous'] = comparable[-1]['cohort']
        output = result_path.parent / 'report'
        if not output.exists():
            write_report(cohorts, output)
        return {'cohort': cohort, 'comparison_key': key, 'report': str(output),
                'details_sha256': metadata['files']['details']['sha256']}

    def append_reference(self, kind: str, identity: str, fields: dict[str, Any]) -> None:
        reference = {'schema': 'spireagent/rl-retained-reference-1', 'run': self.config['run'],
                     'kind': kind, 'identity': identity, **fields}
        raw = json_bytes(reference)
        digest = hashlib.sha256(raw).hexdigest()
        key = f'{self.exchange.prefix}references/{kind}/{identity}-{digest}.json'
        self.exchange.blobs.put_if_absent(key, raw)
        save(self.work / 'references' / kind / f'{identity}-{digest}.json', reference)

    def checkpoint_binding(self, checkpoint: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
        import torch
        from stpd.p1.export import action_weights, load_model
        from stpd.rl.checkpoint import learner_state
        from stpd.workers.checkpoint_codec import decode_checkpoint

        manifest = self.exchange.store.get_manifest(checkpoint['checkpoint_artifact_id'])
        info = manifest.parameters.value()
        raw = b''.join(self.exchange.store.read_payload(manifest.payload('checkpoint')))
        state = decode_checkpoint(raw)
        learner = learner_state(state)
        updates = model['training']['updates']
        model_manifest = self.exchange.store.get_manifest(model['model_artifact_id'])
        if (manifest.kind != 'checkpoint' or info['run'] != self.config['run']
                or info['version'] != model['version'] or checkpoint['version'] != model['version']
                or info['updates'] != updates or learner['updates'] != updates
                or learner['version'] != model['version'] or learner['run'] != self.config['run']
                or not {'weights', 'optimizer', 'torch_rng', 'schema'} <= state.keys()
                or not {'random', 'buffer', 'counts', 'seen', 'returns'} <= learner.keys()
                or not isinstance(info.get('config'), dict)
                or state.get('schema') != info.get('schema')
                or info.get('producer') != manifest.producer.to_dict()
                or model_manifest.producer != manifest.producer):
            raise ValueError('milestone_full_checkpoint_model_binding_mismatch')
        package = self.exchange.materialize_model(model, self.work / 'models')
        weights, _ = load_model(package / 'model.tensor-tree', model['model_sha256'])
        current = weights.state_dict()
        action_state = action_weights(state['weights'], value_heads=info['config'].get('critic') is not None)
        if (current.keys() != action_state.keys()
                or any(not torch.equal(value, action_state[name])
                       for name, value in current.items())):
            raise ValueError('milestone_checkpoint_model_weights_mismatch')
        return {'checkpoint_artifact_id': manifest.artifact_id,
                'checkpoint_payload_sha256': manifest.payload('checkpoint').sha256,
                'checkpoint_parameters': info, 'checkpoint_producer': manifest.producer.to_dict(),
                'version': model['version'], 'updates': updates,
                'model_artifact_id': model['model_artifact_id'], 'model_sha256': model['model_sha256']}

    def milestone_reference(self, now: float) -> None:
        next_at = self.state.setdefault('next_milestone_at', self.config['started_at'] + 14400)
        if now < next_at:
            return
        intents = self.state.setdefault('milestone_intents', {})
        identity = f'wall-{int(next_at):012d}'
        if identity not in intents:
            checkpoint = self.exchange.latest_checkpoint()
            if checkpoint is None:
                self.state['milestone_pending'] = {'due_at': next_at, 'reason': 'no_complete_checkpoint'}
                self.persist()
                return
            model = self.exchange.model(checkpoint['version'])
            binding = self.checkpoint_binding(checkpoint, model)
            intents[identity] = {'due_at': next_at, 'captured_at': checkpoint['published_at'],
                **binding, 'watch_configuration_sha256': self.state['config_sha256']}
            self.persist()  # freeze the exact pair before an uncertain external write
        self.append_reference('milestone', identity, intents[identity])
        self.state['next_milestone_at'] = next_at + 14400
        self.state.pop('milestone_pending', None)
        self.persist()

    @staticmethod
    def candidate_comparison(before: dict[str, Any], after: dict[str, Any]) -> tuple[bool, str]:
        """Predeclared priority; missing/conflicting witnesses do not win."""
        from stpd.evaluation.rl_observation import compare

        if before['comparison_key'] != after['comparison_key']:
            return False, 'incomparable_protocol'
        a, b = before['cohort'], after['cohort']
        if b['wins'] != a['wins']:
            return b['wins'] > a['wins'], 'wins'
        if not a['boss_count_missing'] and not b['boss_count_missing']:
            for field in ('boss1_cleared_witnessed', 'boss2_reached_witnessed',
                          'boss1_reached_witnessed'):
                if a[field] != b[field]:
                    return b[field] > a[field], field
        floors_a = [r['floor'] for r in a['rows'] if r['scored'] and r['floor'] is not None]
        floors_b = [r['floor'] for r in b['rows'] if r['scored'] and r['floor'] is not None]
        if len(floors_a) != a['planned'] or len(floors_b) != b['planned']:
            return False, 'incomparable_incomplete_scored_floors'
        differences = [sum(f >= t for f in floors_b) - sum(f >= t for f in floors_a)
                       for t in sorted(set(floors_a + floors_b))]
        if differences and all(d >= 0 for d in differences) and any(d > 0 for d in differences):
            return True, 'arrival_distribution_dominance'
        if differences and all(d <= 0 for d in differences) and any(d < 0 for d in differences):
            return False, 'arrival_distribution_decline'
        if not any(differences):
            return False, 'arrival_distribution_equal'
        delta = compare(a, b)
        interval = delta['bootstrap95_fixed_seed_sensitivity']
        return bool(delta['paired_n'] == a['planned'] and interval is not None
                    and interval[0] > 0), 'crossing_CDF_paired95_lower_bound'

    def candidate_reference(self, result: dict[str, Any]) -> None:
        processed = self.state.setdefault('candidate_references', [])
        if result['result_sha256'] in processed:
            return
        candidates = self.state.setdefault('candidate_best', {})
        key = result['comparison_key']
        before = next((r for r in self.state['evaluations']
                       if r['result_sha256'] == candidates.get(key)), None)
        if result['unknown'] or result['cohort']['floor_n'] != result['planned']:
            better, reason = False, 'incomplete_scored_denominator'
        else:
            better, reason = ((True, 'first_complete_comparable') if before is None else
                              self.candidate_comparison(before, result))
        self.append_reference('candidate', result['result_sha256'], {
            'result_sha256': result['result_sha256'], 'comparison_key': key,
            'evaluation_kind': result['evaluation_kind'], 'version': result['version'],
            'updates': result['updates'], 'model_artifact_id': result['model_artifact_id'],
            'rule': 'wins-Boss-CDF-paired-v1', 'selected': better, 'reason': reason,
            'previous_result_sha256': candidates.get(key)})
        if better:
            candidates[key] = result['result_sha256']
        processed.append(result['result_sha256'])
        self.persist()

    def plateau(self) -> bool:
        groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        for result in self.state['evaluations']:
            if (result['complete'] and result.get('comparison_key') is not None
                    and result['unknown'] == 0 and result['cohort']['floor_n'] == result['planned']):
                groups[result['comparison_key']].append(result)
        for results in groups.values():
            if len(results) < 4:
                continue
            best = results[0]
            stagnant = 0
            for result in results[1:]:
                from stpd.evaluation.rl_observation import compare

                delta = compare(best['cohort'], result['cohort'])
                improved = delta['mean_delta'] is not None and delta['mean_delta'] > 0
                stagnant = 0 if improved else stagnant + 1
                if improved:
                    best = result
            if stagnant >= 3:
                return True
        return False

    def _actor_allocation_health(self, allocation: dict[str, Any]) -> dict[str, Any]:
        """Scheduler facts distinguish a healthy learner from lost actor capacity."""
        accounting = job_accounting(allocation['job_id'])
        plan = (evaluate.read_pinned(allocation['reference']) if allocation.get('reference')
                else json.loads(Path(allocation['plan']).read_bytes()))
        expected = len(plan['actors'])
        rows = accounting['rows']
        running_jobs = {row['job_id'] for row in rows if row['state'] == 'RUNNING'}
        failed = [row for row in rows if row['state'] in TERMINAL
                  and (row['state'] != 'COMPLETED' or row['exit_code'] != '0:0')]
        stopped, results, active = [], [], set()
        actor_root = Path(allocation['plan']).parent / 'actors'
        for ref in plan['actors']:
            root = actor_root / ref['actor']
            owner_path, result_path = root / 'owner.json', root / 'result.json'
            owner = read_published(owner_path)
            preserved = result_path.exists()
            if not preserved and owner is not None:
                # rl.run seals the exit result here BEFORE its potentially long snapshot.
                result_path = Path(owner['scratch']) / 'result.json'
            if not result_path.exists():
                if owner is not None and str(owner['job_id']) in running_jobs:
                    active.add(str(owner['job_id']))
                continue
            value = read_published(result_path)
            if value is None:
                if owner is not None and str(owner['job_id']) in running_jobs:
                    active.add(str(owner['job_id']))
                continue
            if value['actor'] != ref['actor'] or value['run'] != self.config['run']:
                raise ValueError('actor_health_result_binding_changed')
            summary = value.get('original_summary') or {}
            results.append({'actor': value['actor'], 'exit_code': value['exit_code'],
                            'status': summary.get('status'),
                            'preservation_complete': preserved})
            if str(summary.get('status', '')).startswith('stopped_ledger:'):
                stopped.append({'actor': value['actor'], 'status': summary['status']})
        health = {**accounting, 'expected': expected, 'running': len(running_jobs),
                  'active_actors': len(active), 'actor_results': results,
                  'failed': failed, 'stopped_ledgers': stopped}
        return health

    def actor_health(self) -> dict[str, Any]:
        parts = [self._actor_allocation_health(a) for a in actor_allocations(self.config)]
        health = parts[0] if len(parts) == 1 else {
            'job_id': str(self.config['actor_job_id']), 'allocations': parts,
            'terminal': all(p['terminal'] for p in parts),
            'all_completed': all(p['all_completed'] for p in parts),
            **{name: sum(p[name] for p in parts)
               for name in ('expected', 'running', 'active_actors')},
            **{name: [row for p in parts for row in p[name]]
               for name in ('rows', 'failed', 'actor_results', 'stopped_ledgers')}}
        failed, results = health['failed'], health['actor_results']
        missing = any(not p['rows'] for p in parts)
        signature = json.dumps({'failed': failed, 'actor_results': results,
                                'missing': missing}, sort_keys=True)
        if (failed or results or missing) and self.state.get('actor_attention') != signature:
            self.publish('attention', reason='actor_capacity_requires_owner_read', actors=health)
            self.state['actor_attention'] = signature
        return health

    def tick(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        for key in self.exchange.shard_keys():
            if key not in self.state['shards']:
                self.state['shards'][key] = self.exchange.shard_record(key)
        games = [game for shard in self.state['shards'].values() for game in shard['games']]
        event = self.exchange.latest_event()
        model = self.exchange.latest_model()
        self.collect_evaluation()
        try:
            actors = self.actor_health()
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure  # noqa: BLE001 -- telemetry failure must not suppress stop rules
            actors = {'observation': 'unavailable', 'error_type': type(error).__name__}
            self.telemetry('attention', reason='actor_accounting_unavailable', **actors)
        reason = ('watcher_signal' if self.signalled else
                  stop_reason(now, self.config, games, event, self.state['evaluations']))
        stopping = self.state['stop'] or reason
        if stopping:
            self.stop(stopping)
        elif self.config.get('evaluation_tiers') is not None:
            self.schedule_tiers(model, now)
            self.milestone_reference(now)
        elif (not self.maintenance_requested and self.state['pending'] is None
              and model is not None):
            winner = next((row for row in self.state['evaluations'] if row['complete']
                           and row.get('evaluation_kind', 'fixed20') == 'fixed20'
                           and row['wins'] > 0), None)
            if winner is not None and not self.state.get('confirmation_requested'):
                # One independent original forty-seed ledger: first twenty repeat, last twenty new.
                # Persist this one-shot intent before preparation/submission; never reoffer uncertainty.
                self.state['confirmation_requested'] = True
                self.persist()
                self.prepare_evaluation(self.exchange.model(winner['version']), now, confirmation=True)
            elif now - self.state['last_evaluation_at'] >= self.config.get('evaluation_seconds', 18000):
                self.prepare_evaluation(model, now)
        hour_games = sum(len(shard['games']) for shard in self.state['shards'].values()
                         if shard['finished_at'] >= now - 3600)
        unknown = sum(g['status'] == 'unknown' for g in games)
        seen = set((event or {}).get('seen_shards', []))
        evaluations = self.state['evaluations']
        complete = [row for row in evaluations if row['complete']
                    and row.get('evaluation_kind', 'fixed20') == 'fixed20']
        no_improvement = (len(complete) >= 3 and max(row['mean_max_floor'] or 0 for row in complete[-3:])
                          <= max([13.75] + [row['mean_max_floor'] or 0 for row in complete[:-3]]))
        if self.config.get('evaluation_tiers') is not None:
            no_improvement = self.plateau()
        event_age = now - event['at'] if event else None
        if event_age is None or event_age > 900:
            self.publish('attention', reason='learner_event_missing_or_older_than_15_minutes',
                         learner_event_age_seconds=event_age)
        counts = (event or {}).get('counts', {})
        stale = counts.get('samples_dropped_stale', 0)
        samples = counts.get('rl_samples', 0)
        status = self.publish('status', version=(model or {}).get('version'), actors=actors,
            learner_event_age_seconds=event_age,
            learner_wait_data_fraction=(event or {}).get('timing', {}).get('fractions', {}).get('wait_for_data'),
            stale_sample_discard_fraction=stale / samples if samples else None,
            games_total=len(games), games_last_hour=hour_games, unknown=unknown,
            unknown_fraction=unknown / len(games) if games else None,
            shard_backlog=len(set(self.state['shards']) - seen), learner=event,
            latest_evaluation=evaluations[-1] if evaluations else None,
            three_evaluations_without_improvement=no_improvement,
            win_confirmation_required=(any(row['wins'] > 0 for row in complete)
                                       and not any(row.get('evaluation_kind') == 'confirmation'
                                                   and row['complete'] for row in evaluations)),
            confirmation_requested=self.state.get('confirmation_requested', False),
            latest_confirmation=next((row for row in reversed(evaluations)
                if row.get('evaluation_kind') == 'confirmation'), None),
            stopped=self.state['stop'], pending_evaluation=self.state['pending'])
        self.persist()
        return status

    def evaluation_drain(self) -> dict[str, Any]:
        pending = self.state['pending']
        if self.direct is not None:
            return {'terminal': self.direct.poll() is not None,
                    'exit_code': self.direct.poll(), 'mode': 'direct',
                    'result_present': bool(pending and
                        (Path(pending['plan']).parent / 'result.json').exists())}
        if pending is None:
            return {'terminal': True, 'mode': self.config['evaluation_mode']}
        if pending.get('job_id'):
            return {'mode': 'submit', **job_accounting(pending['job_id'])}
        return {'terminal': False, 'mode': self.config['evaluation_mode'],
                'reason': 'evaluation_owner_requires_reconciliation'}

    def drain(self) -> bool:
        learner_finished = False
        checkpoint = None
        try:
            event = self.exchange.latest_event()
            model = self.exchange.latest_model()
            checkpoint = self.exchange.latest_checkpoint()
            learner_finished = bool(event and event['kind'] == 'finished'
                and event['at'] >= self.config['started_at']
                and model and checkpoint and checkpoint['version'] == model['version'])
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            self.telemetry('attention', reason='learner_drain_observation_unavailable',
                           error_type=type(error).__name__)
        try:
            accounts = [job_accounting(a['job_id']) for a in actor_allocations(self.config)]
            accounting = accounts[0] if len(accounts) == 1 else {
                'terminal': all(a['terminal'] for a in accounts), 'allocations': accounts}
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            accounting = {'terminal': False, 'error_type': type(error).__name__}
        try:
            evaluation = self.evaluation_drain()
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            evaluation = {'terminal': False, 'error_type': type(error).__name__}
        complete = bool(learner_finished and accounting['terminal'] and evaluation['terminal'])
        expired = time.time() - self.state['drain_started_at'] >= 2400
        self.telemetry('drain_status', learner_finished=learner_finished,
            latest_checkpoint=checkpoint, actors=accounting, evaluation=evaluation,
            drain_complete=complete, drain_timeout=expired,
            learner_stop_pending=self.state.get('learner_stop_pending', True))
        save(self.work / 'drain-status.json', {'at': time.time(),
            'learner_finished': learner_finished, 'latest_checkpoint': checkpoint,
            'actors': accounting, 'evaluation': evaluation, 'drain_complete': complete, 'drain_timeout': expired,
            'learner_stop_pending': self.state.get('learner_stop_pending', True)})
        return complete or expired

    def next_delay(self, now: float, *, tick_started_at: float | None = None) -> float:
        """Next bounded health tick or exact evaluation/deadline boundary.

        An overdue submission that could not obtain capacity waits the ordinary
        health interval instead of spinning. This changes only future deployments.
        """
        interval = float(self.config.get('poll_seconds', 900))
        if self.state['stop']:
            return min(interval, max(0., float(2400 - (now - self.state['drain_started_at']))))
        interval = min(interval, max(0., float(self.config['started_at']
            + self.config['wall_seconds'] - now)))
        if self.config.get('evaluation_tiers') is not None and not self.maintenance_requested:
            schedules = self.state.get('tier_next_due', {})
            due_at = min(schedules.get(t['name'], self.config['started_at'] + t['seconds'])
                         for t in tiers(self.config))
            due_in = float(due_at - now)
            if due_in > 0:
                return min(interval, due_in)
            if tick_started_at is not None and tick_started_at < due_at:
                return 0
            return interval
        if self.state['pending'] is None and not self.maintenance_requested:
            due_at = (self.state['last_evaluation_at']
                      + self.config.get('evaluation_seconds', 18000))
            due_in = float(due_at - now)
            if due_in > 0:
                interval = min(interval, due_in)
            elif tick_started_at is not None and tick_started_at < due_at:
                # The last tick started before the deadline: it did not yet get
                # a due evaluation attempt. Check once now; failed overdue ticks
                # still back off for the ordinary health interval.
                interval = 0
        return interval

    def run(self):
        if sys.platform != 'linux' or not os.environ.get('SLURM_JOB_ID'):
            raise ValueError('Friday_Slurm_watcher_required')
        for number in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT):
            signal.signal(number, lambda *_: setattr(self, 'signalled', True))
        signal.signal(signal.SIGUSR2, lambda *_: setattr(self, 'maintenance_requested', True))
        self.telemetry('watcher_started', config_sha256=self.state['config_sha256'],
                       job_id=os.environ['SLURM_JOB_ID'])
        while True:
            tick_started_at = time.time()
            try:
                self.tick(tick_started_at)
            except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
                self.telemetry('attention', reason='watcher_tick_failed',
                    error_type=type(error).__name__, error_code=getattr(error, 'code', None))
                # The actor signal is local/Slurm and precedes all remote learner control.
                if self.state['stop'] or self.signalled or time.time() >= (
                        self.config['started_at'] + self.config['wall_seconds']):
                    self.stop(self.state['stop'] or ('watcher_signal' if self.signalled
                                                    else 'first_round_wall_limit'))
            if self.state['stop'] and self.drain():
                return
            if self.maintenance_requested and not self.state['stop'] and self.state['pending'] is None:
                self.persist()
                self.telemetry('watcher_maintenance_exit', actors_and_learner_stopped=False)
                return
            time.sleep(self.next_delay(time.time(), tick_started_at=tick_started_at))


def read_segment_config(path: Path) -> dict[str, Any]:
    """The existing watcher owns this mode; it consumes the original launch/ledger."""
    value: dict[str, Any] = json.loads(path.read_bytes())
    if (value.get('schema') != SCHEMA or value.get('mode') != 'segments'
            or type(value.get('deadline_at')) not in (int, float)
            or not math.isfinite(value['deadline_at'])
            or type(value.get('training_seconds')) not in (int, float)
            or not 1 <= value['training_seconds'] <= 72000
            or type(value.get('actors')) is not int or not 1 <= value['actors'] <= 500
            or not isinstance(value.get('budget_id'), str)
            or not 1 <= value.get('poll_seconds', 30) <= 300
            or value.get('actor_partition', 'smp') not in {'smp', 'general', 'debug'}
            or not 0 < value.get('unknown_stop_fraction', .05) <= 1):
        raise ValueError('bounded_segment_run_watch_configuration_required')
    from stpd.rl.learner import LearnerConfig
    source = Path(value['runtime_source'])
    base = LearnerConfig.read(Path(value['learner_config']))
    if (source.resolve() != source or source.is_symlink() or base.segments is None
            or not base.resume_checkpoint or base.run != value['run'] or base.peer != value['peer']
            or value.get('cpu_only', False) != (base.device == 'cpu')):
        raise ValueError('exact_segment_learner_source_CP_and_compute_binding_required')
    for key in ('learner_config', 'execution', 'learner_python'):
        target = Path(value[key])
        if not target.is_absolute() or not target.is_file():
            raise ValueError('existing_absolute_segment_watch_input_required')
    exchange = Exchange(value['peer'], value['run'])
    acknowledgements = value.get('acknowledged_integrity_events', {})
    if not isinstance(acknowledgements, dict):
        raise TypeError('pinned_diagnosed_integrity_findings_required')
    for key, digest in acknowledgements.items():
        event = exchange.event_record(key)
        if (not key.startswith(exchange.prefix + 'learner-events/')
                or event['kind'] != 'integrity_failed' or event.get('budget_id') != value['budget_id']
                or event['record_sha256'] != digest):
            raise ValueError('pinned_diagnosed_integrity_findings_required')
    if value.get('previous_window_state') is not None:
        previous = evaluate.read_pinned(value['previous_window_state'])
        prior = evaluate.read_pinned(previous['configuration'])
        if (previous['active'] is not None or previous['stop'] != 'run_budget_complete'
                or prior['run'] != value['run'] or prior['peer'] != value['peer']
                or prior['budget_id'] == value['budget_id']):
            raise ValueError('completed_distinct_previous_operating_window_required')
    if value.get('evaluation') is not None:
        evaluation = value['evaluation']
        if (not 50 <= evaluation.get('seeds', 50) <= 100
                or evaluation.get('seconds', 7200) != 7200
                or evaluation.get('partition', 'general') not in {'general', 'debug', 'smp'}):
            raise ValueError('fixed_two_hour_paired_evaluation_required')
        if (evaluation.get('initial_checkpoint') is not None
                and evaluation['initial_checkpoint'] != base.resume_checkpoint):
            raise ValueError('initial_evaluation_must_bind_continuation_checkpoint')
        binding = evaluate.read_pinned(evaluation['baseline_binding'])
        if binding['checkpoint_artifact_id'] != evaluation['baseline_checkpoint']:
            raise ValueError('baseline_checkpoint_model_and_result_binding_required')
        projection = evaluate.fixed_baseline(evaluation['baseline_batches'], binding,
            evaluation['manifest'], seeds=evaluation.get('seeds', 50))
        if not projection['offers_accounted']:
            raise ValueError('frozen_baseline_attempts_must_be_accounted_before_training')
    return value


class SegmentWatcher(Watcher):
    """RL4.1 adaptation of the original Friday owner, not a gameplay scheduler.

    All offers go through rl.launch/rl.submit/evaluate and their sticky intents.
    Lost allocations replace capacity, never an old World. No secret is persisted.
    """

    def __init__(self, config: dict[str, Any], work: Path):
        self.config, self.work = config, private_root(work)
        self.exchange = Exchange(config['peer'], config['run'])
        self.exchange.run_deadline(config['deadline_at'], budget_id=config['budget_id'])
        self.state_path = self.work / 'watch-state.json'
        resumed = config.get('previous_watch_state')
        previous = evaluate.read_pinned(resumed) if resumed and not self.state_path.exists() else None
        if previous is not None:
            prior_config = evaluate.read_pinned(previous['configuration'])
            if (previous['config_sha256'] != hashlib.sha256(json_bytes(prior_config)).hexdigest()
                    or any(prior_config[k] != config[k] for k in (
                        'run', 'peer', 'budget_id', 'deadline_at', 'training_seconds'))):
                raise ValueError('previous_watch_run_peer_and_budget_identity_changed')
        frozen_config = self.work / 'watch-config.json'
        if not frozen_config.exists():
            save(frozen_config, config)
        if hashed(frozen_config) != hashlib.sha256(json_bytes(config)).hexdigest():
            raise ValueError('watch_resume_configuration_changed')
        config_reference = {'path': str(frozen_config), 'sha256': hashed(frozen_config)}
        self.state: dict[str, Any] = (json.loads(self.state_path.read_bytes()) if self.state_path.exists() else previous or
            {'config_sha256': hashlib.sha256(json_bytes(config)).hexdigest(), 'attempts': [],
             'active': None, 'stop': None, 'training_seconds': 0., 'games_total': 0,
             'unknown_intents': [], 'unresolved_admissions': [], 'last_event_key': '',
             'open_worlds': {}, 'seen_event_keys': [], 'worlds': {}, 'terminal_actors': [],
             'generation_seconds': {}, 'lost_world_offers': [], 'health': {},
             'unresolved_admission_generations': {},
             'blocking_integrity_findings': {},
             'configuration': config_reference,
             'pending': None, 'evaluations': [], 'next_evaluation_seconds': (
                 0 if (config.get('evaluation') or {}).get('initial_checkpoint') else 7200),
             'protected_checkpoints': [], 'incarnation': None})
        if previous is not None:
            if any(config.get('acknowledged_integrity_events', {}).get(key) != digest
                   for key, digest in previous.get('blocking_integrity_findings', {}).items()):
                raise ValueError('diagnosed_integrity_findings_required_for_continuation')
            save(self.work / 'previous-watch-state.json', previous)
            self.state['previous_config_sha256'] = self.state['config_sha256']
            self.state['config_sha256'] = hashlib.sha256(json_bytes(config)).hexdigest()
            self.state['configuration'] = config_reference
            self.state['stop'] = None  # Explicit continuation; original state remains pinned above.
        if self.state['config_sha256'] != hashlib.sha256(json_bytes(config)).hexdigest():
            raise ValueError('watch_resume_configuration_changed')
        self.signalled = self.maintenance_requested = False
        self.incarnation = f'{os.getpid()}-{time.time_ns()}'
        self.capability_lost = self.state['active'] is not None
        self.state['incarnation'] = self.incarnation
        self.seen_events = set(self.state.get('seen_event_keys', []))

    def collect_events(self):
        worlds = self.state['worlds']
        unresolved = set(self.state['unresolved_admissions'])
        terminal = set(self.state['terminal_actors'])
        attempts = {g: a for a in self.state['attempts']
                    for g in a.get('generations', [a['generation']])}
        keys = sorted(k for k in self.exchange.blobs.keys(self.exchange.prefix + 'learner-events/')
                      if k.endswith('.json') and k not in self.seen_events)
        for key in keys:
            event = self.exchange.event_record(key)
            self.seen_events.add(key)
            if event.get('budget_id') != self.config['budget_id']:
                continue
            if event['kind'] == 'learner_replacement_started':
                previous = attempts.get(event['previous_generation'])
                if previous is None:
                    raise ValueError('learner_replacement_requires_owned_previous_generation')
                generation = event['training_generation']
                previous.setdefault('generations', [previous['generation']]).append(generation)
                previous.setdefault('replacement_elapsed_seconds', 0.)
                previous['replacement_elapsed_seconds'] += event['charged_seconds']
                previous['active_seconds'] = 0.
                previous.pop('active_measured_at', None)
                previous.pop('window_closed', None)
                attempts[generation] = previous
            attempt = attempts.get(event.get('training_generation'))
            if event['kind'] in {'heartbeat', 'training_window'}:
                generation = event['training_generation']
                self.state['generation_seconds'][generation] = max(
                    self.state['generation_seconds'].get(generation, 0), event['active_seconds'])
            if attempt is not None:
                attempt['last_event'] = event
                updates = event.get('updates', event.get('update'))
                if updates is not None and updates > attempt.get(
                        'last_progress_updates', attempt['checkpoint']['updates']):
                    attempt['last_progress_updates'] = updates
                    attempt['last_progress_at'] = event['at']
                if event['kind'] == 'training_started':
                    attempt.setdefault('last_progress_at', event['at'])
                if (event['kind'] in {'heartbeat', 'training_window'} and
                        event['training_generation'] == attempt.get(
                            'generations', [attempt['generation']])[-1]):
                    attempt['active_seconds'] = max(attempt.get('active_seconds', 0),
                                                     event['active_seconds'])
                    attempt['active_measured_at'] = event['at']
                    if event['kind'] == 'training_window':
                        attempt['window_closed'] = True
                if event['kind'] == 'failed':
                    attempt['failed'] = event
                    self.state['stop'] = self.state['stop'] or 'learner_failed_requires_diagnosis'
                if event['kind'] == 'finished':
                    attempt['finished'] = True
            if event['kind'] == 'actor_retired':
                unresolved.update(event['unresolved_episodes'])
                self.state['unresolved_admission_generations'].update({
                    intent: event['training_generation'] for intent in event['unresolved_episodes']})
                terminal.add(event['actor'])
            if event['kind'] in {'sampling_audit_scheduled', 'sampling_audit'}:
                audits = self.state.setdefault('sampling_audits', {})
                status = event.get('status', 'pending')
                previous = audits.get(event['run_id'])
                if previous and previous['status'] != 'pending' and previous['status'] != status:
                    raise ValueError('immutable_sampling_audit_result_changed')
                audits[event['run_id']] = {'status': status, 'at': event['at']}
            if event['kind'] == 'integrity_failed':
                self.state['blocking_integrity_findings'][key] = event['record_sha256']
                acknowledged = self.config.get('acknowledged_integrity_events', {}).get(key)
                if acknowledged != event['record_sha256']:
                    self.state['stop'] = 'integrity_failed_requires_diagnosis'
            if event['kind'] == 'actor_world_offered':
                intent = event['intent_id']
                fact = worlds.setdefault(intent, {})
                if fact.get('actor') not in {None, event['actor']}:
                    raise ValueError('world_intent_actor_changed')
                fact.update(actor=event['actor'], generation=event['training_generation'], offered=True)
            if event['kind'] == 'actor_games_closed':
                for game in event['games']:
                    intent = game.get('intent_id') or game.get('run_id') or game['seed']
                    fact = worlds.setdefault(intent, {})
                    if fact.get('actor') not in {None, event['actor']}:
                        raise ValueError('world_intent_actor_changed')
                    if fact.get('closed') and fact['status'] != game['status']:
                        raise ValueError('immutable_actor_close_changed')
                    fact.update(actor=event['actor'], closed=True, status=game['status'])
                    if not fact.get('generation') and game.get('training_generation'):
                        fact['generation'] = game['training_generation']
                    if game.get('world_mutation_offered'):
                        fact['offered'] = True
                    # A late original close reconciles a derived lost-offer
                    # observation; the original ledger's unknown is never upgraded.
                    unresolved.discard(intent)
        self.state['seen_event_keys'] = sorted(self.seen_events)
        self.state['unresolved_admissions'] = sorted(intent for intent in unresolved
            if not worlds.get(intent, {}).get('offered') and not worlds.get(intent, {}).get('closed'))
        self.state['terminal_actors'] = sorted(terminal)
        self.refresh_world_health()
        total = 0.
        for attempt in self.state['attempts']:
            measured = attempt.get('active_seconds', 0.)
            if not attempt.get('window_closed') and attempt.get('active_measured_at'):
                end = attempt.get('terminal_observed_at', time.time())
                measured += max(0, end - attempt['active_measured_at'])
            measured += attempt.get('replacement_elapsed_seconds', 0.)
            attempt['charged_seconds'] = max(attempt.get('charged_seconds', 0), measured)
            total = max(total, attempt.get('budget_start_seconds', 0) + attempt['charged_seconds'])
        self.state['training_seconds'] = max(self.state['training_seconds'], total,
                                             sum(self.state['generation_seconds'].values()))

    def refresh_world_health(self):
        terminal = set(self.state['terminal_actors'])
        worlds = self.state['worlds']
        offered = {k for k, v in worlds.items() if v.get('offered')}
        unknown = {k for k in offered if worlds[k].get('status') in {'unknown', 'save_failed'}}
        lost = {k for k in offered if not worlds[k].get('closed') and worlds[k]['actor'] in terminal}
        self.state['games_total'] = len(offered)
        self.state['unknown_intents'] = sorted(unknown)
        self.state['lost_world_offers'] = sorted(lost)
        self.state['open_worlds'] = {k: v for k, v in worlds.items()
                                     if k in offered and not v.get('closed') and k not in lost}
        index = self.state['active']
        generation = self.state['attempts'][index]['generation'] if index is not None else None
        generations = set(self.state['attempts'][index].get('generations', [generation])) \
            if index is not None else set()
        active = {k for k in offered if worlds[k].get('generation') in generations}
        self.state['health'] = {'generation': generation, 'offered': len(active),
            'completed_for_stop': sum(worlds[k].get('closed') is True
                                      or worlds[k]['actor'] in terminal for k in active),
            'known_unknown': len(active & unknown), 'lost_offers': len(active & lost),
            'unresolved_admissions': sum(self.state['unresolved_admission_generations'].get(k)
                in generations for k in self.state['unresolved_admissions']) if generation else 0}
        self.refresh_audit_health()

    def refresh_audit_health(self):
        audits = list(self.state.get('sampling_audits', {}).values())
        completed = sorted((a for a in audits if a['status'] != 'pending'),
                           key=lambda a: a['at'])
        window = completed[-50:]
        inconclusive = sum(a['status'] == 'audit_inconclusive' for a in window)
        fraction = inconclusive / len(window) if window else 0.
        paused = self.state.get('audit_admission_paused', False)
        if inconclusive >= 5 and fraction > .2 and not paused:
            self.exchange.pause_admission(self.config['budget_id'],
                                           'sampling_audit_inconclusive_window')
            self.state['audit_admission_paused'] = paused = True
            self.telemetry('attention', reason='sampling_audit_inconclusive_admission_paused',
                           audit_window=len(window), inconclusive=inconclusive)
        self.state['health']['sampling_audit'] = {
            'pending': sum(a['status'] == 'pending' for a in audits),
            'completed': len(completed), 'inconclusive': sum(
                a['status'] == 'audit_inconclusive' for a in completed),
            'window_size': 50, 'window_completed': len(window),
            'window_inconclusive': inconclusive, 'window_fraction': fraction,
            'admission_paused': paused}

    def cold_checkpoint(self, artifact_id: str) -> dict[str, Any]:
        import tempfile

        from spireagent.storage.config import open_peer
        from spireagent.storage.store import copy_artifact
        from stpd.rl.checkpoint_state import decode_state
        archive = self.config['peer'].get('archive')
        if archive is None:
            raise ValueError('independent_checkpoint_archive_required')
        source = open_peer(archive)
        with tempfile.TemporaryDirectory(prefix='cp-cold-', dir=self.work) as temporary:
            target = open_peer({'kind': 'local', 'directory': temporary})
            copy_artifact(source, target, artifact_id, scratch=Path(temporary))
            manifest = target.get_manifest(artifact_id)
            payload = manifest.payload('checkpoint')
            cp = decode_state(target.bytes(payload, maximum=payload.size))
            if cp.identity.run != self.config['run']:
                raise ValueError('restored_checkpoint_run_changed')
            receipt = {'checkpoint_artifact_id': artifact_id, 'payload_sha256': payload.sha256,
                'payload_bytes': payload.size, 'sequence': cp.identity.sequence,
                'updates': cp.counters.updates, 'training_generation': cp.identity.training_generation,
                'producer': manifest.producer.to_dict(), 'empty_directory_readback': True,
                'operating_budget': cp.config.get('operating_budget'),
                'deadline_at': cp.config.get('deadline_at'),
                'normal_reader': True, 'at': time.time()}
        save(self.work / 'CP-readbacks' / (artifact_id + '.json'), receipt)
        return receipt

    def select_checkpoint(self) -> dict[str, Any]:
        from spireagent.storage.blobs import StoreError
        from spireagent.storage.config import open_peer
        from spireagent.storage.store import copy_artifact
        from stpd.rl.learner import LearnerConfig
        base = LearnerConfig.read(Path(self.config['learner_config']))
        if self.config.get('previous_window_state') and not self.state['attempts']:
            assert base.resume_checkpoint is not None
            return self.cold_checkpoint(base.resume_checkpoint)
        records = self.exchange.checkpoint_records()
        identities = list(dict.fromkeys(r['checkpoint_artifact_id'] for r in reversed(records)))
        fallback = LearnerConfig.read(Path(self.config['learner_config'])).resume_checkpoint
        if fallback not in identities:
            identities.append(fallback)
        for identity in identities:
            try:
                return self.cold_checkpoint(identity)
            except StoreError as error:
                if error.code != 'object_not_found':
                    raise
                self.telemetry('checkpoint_archive_pending', checkpoint_artifact_id=identity)
                # A node can die after local CP publication but before async R2
                # preservation. Finish that same immutable upload before restore.
                copy_artifact(self.exchange.store, open_peer(self.config['peer']['archive']),
                              identity, scratch=self.work)
                return self.cold_checkpoint(identity)
        raise ValueError('no_independently_recoverable_checkpoint')

    def submit_cpu_step(self, script: Path, root: Path, *, name: str, cpus: int,
                        minutes: int, memory_gb: int) -> str:
        """Engineering/CPU allocation only. GPU submission stays in I1's launcher."""
        intent, receipt = root / 'submission-intent.json', root / 'submission.json'
        if intent.exists() or receipt.exists():
            raise ValueError('original_submission_intent_requires_reconciliation_no_retry')
        command = ['sbatch', '--parsable', '--partition=' + self.config.get('cpu_partition', 'debug'),
            '--nodes=1', '--ntasks=1', f'--cpus-per-task={cpus}', f'--mem={memory_gb}G',
            f'--time={minutes}', '--no-requeue', '--signal=B:USR1@300', '--export=ALL',
            '--job-name=' + name, '--output=' + str(root / '%j.out'),
            '--error=' + str(root / '%j.err')]
        if self.config.get('cpu_constraint'):
            command.append('--constraint=' + self.config['cpu_constraint'])
        if self.config.get('exclude_nodes'):
            command.append('--exclude=' + ','.join(self.config['exclude_nodes']))
        command.append(str(script))
        with scheduler.user_submission_lock():
            save(intent, {'at': time.time(), 'command': command, 'script_sha256': hashed(script)})
            job = scheduler.output(command)
            save(receipt, {'at': time.time(), 'job_id': job})
        return job.split(';')[0]

    def start_attempt(self):
        import dataclasses
        import inspect

        import rl
        from stpd.rl.learner import LearnerConfig
        if self.state['active'] is not None:
            raise ValueError('previous_learner_and_actor_allocations_must_be_terminal')
        if len(self.state['attempts']) > self.config.get('max_recoveries', 3):
            self.state['stop'] = 'learner_recovery_limit_requires_diagnosis'
            self.persist()
            return
        if not self.config.get('cpu_only') and 'retain_capability' not in inspect.signature(
                rl.launch).parameters:
            raise ValueError('I1_launch_retain_capability_integration_required_before_GPU_offer')
        cp = self.select_checkpoint()
        new_window = bool(self.config.get('previous_window_state') and not self.state['attempts'])
        if new_window:
            previous = evaluate.read_pinned(self.config['previous_window_state'])
            prior = evaluate.read_pinned(previous['configuration'])
            generations = {g for a in previous['attempts']
                           for g in a.get('generations', [a['generation']])}
            budget = cp.get('operating_budget') or {}
            if (budget.get('id') != prior['budget_id']
                    or budget.get('total_seconds') != prior['training_seconds']
                    or cp.get('deadline_at') != prior['deadline_at']
                    or cp.get('training_generation') not in generations):
                raise ValueError('checkpoint_does_not_belong_to_completed_previous_window')
        if (not new_window and cp.get('deadline_at') is not None
                and cp['deadline_at'] != self.config['deadline_at']):
            raise ValueError('checkpoint_deadline_cannot_be_reset')
        if cp.get('operating_budget') is not None and not new_window:
            budget = cp['operating_budget']
            if not self.state['attempts'] and not self.config.get('previous_watch_state'):
                raise ValueError('original_watch_state_required_to_reconcile_deliveries')
            if (budget['id'] != self.config['budget_id']
                    or budget['total_seconds'] != self.config['training_seconds']):
                raise ValueError('checkpoint_operating_budget_cannot_be_reset')
            self.state['training_seconds'] = max(self.state['training_seconds'],
                                                 budget['consumed_seconds'])
        number = len(self.state['attempts']) + 1
        root = private_root(self.work / f'attempt-{number:04d}')
        generation = f'g-ops-{number:04d}-{time.time_ns()}'
        base = LearnerConfig.read(Path(self.config['learner_config']))
        if base.segments is None:
            raise ValueError('short_segment_learner_configuration_required')
        remaining = min(self.config['training_seconds'] - self.state['training_seconds'],
                        self.config['deadline_at'] - time.time() - 600)
        if remaining <= 0:
            self.state['stop'] = 'run_budget_complete'
            self.persist()
            return
        config = dataclasses.replace(base, resume_checkpoint=cp['checkpoint_artifact_id'],
            resume_source=cp['producer'], start_package=None, start_artifact_id=None,
            start_model_sha256=None, deadline_at=self.config['deadline_at'], wall_seconds=remaining,
            operating_budget_parent=({
                'checkpoint_artifact_id': cp['checkpoint_artifact_id'],
                'operating_budget': cp['operating_budget'], 'deadline_at': cp['deadline_at']}
                if new_window else base.operating_budget_parent),
            operating_budget={'id': self.config['budget_id'],
                'total_seconds': self.config['training_seconds'],
                'consumed_seconds': self.state['training_seconds']},
            segments=dataclasses.replace(base.segments, training_generation=generation,
                rendezvous_path=str(self.work / 'inference.json'))).bind_actors(self.config['actors'])
        configuration = root / 'learner.json'
        save(configuration, config.frozen())
        script = root / 'learner.sh'
        script.write_text(rl.learner_launcher(configuration, root / 'learner',
            Path(self.config['execution']), root / 'actors', source=Path(self.config['runtime_source']),
            actors=self.config['actors'], seed_namespace=f'ops-{self.work.name}-{number}',
            python=Path(self.config['learner_python']),
            episode_seconds=self.config.get('episode_seconds', 14400),
            supervisor=self.config.get('learner_supervisor')))
        script.chmod(0o700)
        attempt = {'root': str(root), 'generation': generation, 'config': str(configuration),
                   'checkpoint': cp, 'actor_plans': [], 'replaced': [], 'active_seconds': 0,
                   'budget_start_seconds': self.state['training_seconds']}
        self.state['attempts'].append(attempt)
        self.state['active'] = number - 1
        self.persist()  # Freeze the attempt before any potentially uncertain sbatch.
        if self.config.get('cpu_only'):
            old_stop = os.environ.get(rl.LAUNCH_STOP_ENV)
            os.environ[rl.LAUNCH_STOP_ENV] = str(root / 'login-launch/STOP')
            try:
                attempt['job_id'] = self.submit_cpu_step(script, root / 'login-launch',
                    name='rl4-learner-cpu', cpus=self.config.get('cpus', 2),
                    minutes=min(60, math.ceil((remaining + 900) / 60)),
                    memory_gb=self.config.get('memory_gb', 8))
            finally:
                if old_stop is None:
                    os.environ.pop(rl.LAUNCH_STOP_ENV, None)
                else:
                    os.environ[rl.LAUNCH_STOP_ENV] = old_stop
        else:
            receipt = rl.launch(script, configuration, root / 'learner', root / 'actors',
                root / 'login-launch', gpu=self.config.get('gpu', 'a100'),
                minutes=self.config.get('minutes', 2880), cpus=self.config.get('cpus', 16),
                memory_gb=self.config.get('memory_gb', 64),
                ready_seconds=self.config.get('ready_seconds', 3600), retain_capability=True,
                held_job_id=self.config.get('held_job_id'))
            attempt['job_id'] = receipt['GPU_job_id']
            attempt['actor_plans'] = [str(root / 'actors/plan.json')]
        self.capability_lost = False
        self.persist()

    def offer_initial_actors(self, attempt: dict[str, Any]) -> None:
        import rl
        path = Path(attempt['root']) / 'actors/plan.json'
        if attempt['actor_plans'] or not path.exists():
            return
        submitted = path.parent / 'submission.json'
        if submitted.exists():
            # A receipt is not permission to offer again; recover only ownership.
            attempt['actor_plans'] = [str(path)]
        else:
            rl.submit(path, partition=self.config.get('actor_partition', 'smp'),
                constraint=self.config.get('actor_constraint'),
                memory_gb=self.config.get('actor_memory_gb', 4),
                exclude_nodes=self.config.get('exclude_nodes'))
            attempt['actor_plans'] = [str(path)]
        self.persist()

    def replace_actors(self, attempt: dict[str, Any]) -> None:
        import rl

        if self.state.get('audit_admission_paused'):
            return
        terminal = [row for plan in attempt['actor_plans']
                    for row in rl.actor_accounting(Path(plan)) if row['terminal']
                    and row['actor'] not in attempt['replaced']]
        if not terminal:
            return
        self.state['terminal_actors'] = sorted(set(self.state['terminal_actors'])
                                               | {row['actor'] for row in terminal})
        self.refresh_world_health()
        health = self.state['health']
        if (health['completed_for_stop'] and (health['known_unknown'] + health['lost_offers'])
                / health['completed_for_stop']
                > self.config.get('unknown_stop_fraction', .05)):
            self.state['stop'] = 'training_unknown_fraction_above_limit'
            self.stop_attempt(attempt, self.state['stop'])
            return
        if time.time() + 900 >= self.config['deadline_at']:
            return
        number = len(attempt.get('replacements', [])) + 1
        root = Path(attempt['root']) / f'replacement-{number:04d}'
        config = Path(attempt['root']) / 'learner/learner-config.json'
        rl.prepare(Path(self.config['execution']), config, root, actors=len(terminal),
            wall_seconds=max(300, min(79200, int(self.config['deadline_at'] - time.time() - 600))),
            episode_seconds=self.config.get('episode_seconds', 14400), max_games=100000,
            games_per_ledger=10, runtime_source=Path(self.config['runtime_source']),
            seed_namespace=f'ops-{self.work.name}-{attempt["generation"]}-replacement-{number}',
            segment_inference=self.work / 'inference.json')
        replacement = {'plan': str(root / 'plan.json'), 'replaces': terminal}
        attempt.setdefault('replacements', []).append(replacement)
        attempt['replaced'].extend(row['actor'] for row in terminal)
        self.persist()  # Never synthesize a second plan after an uncertain submit.
        rl.submit(root / 'plan.json', additional_to=[Path(p) for p in attempt['actor_plans']],
            partition=self.config.get('actor_partition', 'smp'),
            constraint=self.config.get('actor_constraint'),
            memory_gb=self.config.get('actor_memory_gb', 4),
            exclude_nodes=self.config.get('exclude_nodes'))
        attempt['actor_plans'].append(str(root / 'plan.json'))
        refs = [{'path': p, 'sha256': hashed(Path(p))} for p in attempt['actor_plans'][1:]]
        save(Path(attempt['root']) / 'actor-allocations.json', {'plans': refs})
        self.persist()

    def stop_attempt(self, attempt: dict[str, Any], reason: str) -> None:
        for generation in attempt.get('generations', [attempt['generation']]):
            self.exchange.request_stop(reason, training_generation=generation)
        root = Path(attempt['root'])
        save(root / 'login-launch/STOP', {'reason': reason})
        if (root / 'learner').exists():
            (root / 'learner/STOP').touch()
        self.signal_once(attempt['job_id'])
        for plan in attempt['actor_plans']:
            path = Path(plan)
            save(path.parent / 'STOP', {'reason': reason})
            job = json.loads((path.parent / 'submission.json').read_bytes())['job_id']
            self.signal_once(str(job).split(';')[0])
        attempt['stopping'] = reason
        self.persist()

    def cancel_at_deadline(self):
        """The frozen absolute boundary authorizes cancellation of owned allocations."""
        jobs = set()
        if self.state['active'] is not None:
            attempt = self.state['attempts'][self.state['active']]
            if attempt.get('job_id'):
                jobs.add(attempt['job_id'])
            for plan in attempt['actor_plans']:
                receipt = Path(plan).parent / 'submission.json'
                if receipt.exists():
                    jobs.add(str(json.loads(receipt.read_bytes())['job_id']).split(';')[0])
        if self.state['pending'] and self.state['pending'].get('job_id'):
            jobs.add(self.state['pending']['job_id'])
        intents = self.state.setdefault('deadline_cancel_intents', {})
        for job in jobs - intents.keys():
            intents[job] = {'at': time.time(), 'outcome': 'unknown'}
            self.persist()
            result = subprocess.run(['scancel', job], check=False, capture_output=True)
            intents[job].update(outcome='response_received', exit_code=result.returncode)
            self.persist()

    def evaluation_cohort(self, plan_path: Path, result_path: Path
                          ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bool]:
        snapshot = read_published(plan_path.parent / 'snapshot.json')
        if snapshot is None:
            raise FileNotFoundError('evaluation_snapshot_not_published')
        if snapshot.get('full_readback') is not True:
            raise ValueError('evaluation_full_snapshot_readback_required')
        plan, result, files = evaluate.read_observation_report(result_path, plan_path)
        ledger = result['ledger_report']
        closed = (not result.get('error_type') and ledger['score_admitted'] is True
            and not any(row.get('execution_reason') == 'typed_original_verification_failed'
                        for row in ledger['slots'])
            and all(row['status'] in {'finished', 'unknown'} and row.get('cleanup_accepted') is True
                    for row in ledger['slots']))
        return plan, result, files['cohort'], closed

    def collect_segment_evaluation(self):
        pending = self.state['pending']
        if pending is None:
            return
        root = Path(pending['root'])
        if pending['stage'] == 'preparation':
            if time.time() >= self.config['deadline_at'] - 900:
                self.signal_once(pending['job_id'])
                pending['stage'] = 'failed'
                pending['reason'] = 'absolute_deadline_no_World_offer'
                self.state['stop'] = 'absolute_deadline'
                self.persist()
                return
            if not job_accounting(pending['job_id'])['terminal']:
                return
            plan_path = root / 'round/evaluation/plan.json'
            if not plan_path.exists():
                self.state['stop'] = 'evaluation_CP_preparation_failed'
                pending['stage'] = 'failed'
                self.persist()
                return
            pending['stage'] = 'submission_intent'
            pending['plan'] = str(plan_path)
            self.persist()
            cfg = self.config['evaluation']
            receipt = evaluate.submit(plan_path, partition=cfg.get('partition', 'general'),
                constraint=cfg.get('constraint'), exclude_nodes=self.config.get('exclude_nodes'))
            pending.update(stage='running', job_id=str(receipt['job_id']).split(';')[0])
            self.persist()
            return
        if pending['stage'] in {'failed', 'submission_intent'}:
            return  # Sticky uncertain/failed original; no replacement games.
        if not job_accounting(pending['job_id'])['terminal']:
            return
        path = Path(pending['plan'])
        result_path = path.parent / 'result.json'
        if (read_published(result_path) is None
                or read_published(path.parent / 'snapshot.json') is None):
            return
        plan, result, cohort, closed = self.evaluation_cohort(path, result_path)
        binding = evaluate.read_pinned(plan['checkpoint_binding'])
        report = {'root': pending['root'], 'plan': str(path), 'result': str(result_path),
            'checkpoint': binding, 'closed': closed, 'cohort': cohort,
            'at_training_seconds': pending['due_seconds'], 'job_id': pending['job_id'],
            'unknown': sum(r['status'] == 'unknown' for r in result['ledger_report']['slots'])}
        if closed:
            from stpd.evaluation.rl_observation import compare, write_report
            cfg = self.config['evaluation']
            baseline_projection = evaluate.fixed_baseline(cfg['baseline_batches'],
                evaluate.read_pinned(cfg['baseline_binding']), cfg['manifest'],
                seeds=cfg.get('seeds', 50))
            baseline = baseline_projection['cohort']
            if (not baseline_projection['offers_accounted']
                    or cfg['manifest']['sha256'] != plan['manifest']['sha256']):
                raise ValueError('exact_frozen_baseline_protocol_and_denominator_required')
            required = {'host_pin', 'runtime_pin', 'input_spec', 'initial_progress_sha256',
                        'behavior_policy'}
            for group in (baseline, cohort):
                protocol = group['protocol_observed']
                if (not required <= protocol.keys()
                        or protocol['behavior_policy'].get('mode') != 'argmax'):
                    raise ValueError('complete_argmax_evaluation_protocol_required')
            if {r['seed'] for r in baseline['rows']} != {r['seed'] for r in cohort['rows']}:
                raise ValueError('exact_same_frozen_paired_seed_set_required')
            previous = (self.state['evaluations'][-1]['cohort']
                        if self.state['evaluations'] else baseline)
            report['against_baseline'] = compare(baseline, cohort)
            report['against_previous'] = compare(previous, cohort)
            report['report_directory'] = str(root / 'comparison')
            write_report({'baseline': baseline, 'previous': previous, 'current': cohort},
                         root / 'comparison', scope_note='Managed fixed pool; unknown pairs excluded, '
                         'no retries. Selection uses raw CP theta, never training returns.')
            best = self.state.get('best_checkpoint')
            best_cohort = self.state.get('best_cohort', baseline)
            comparison = compare(best_cohort, cohort)
            if comparison['mean_delta'] is not None and comparison['mean_delta'] > 0:
                self.state['best_checkpoint'] = binding['checkpoint_artifact_id']
                self.state['best_cohort'] = cohort
            elif best is None:
                from stpd.rl.learner import LearnerConfig
                self.state['best_checkpoint'] = self.config['evaluation'].get(
                    'baseline_resume_checkpoint', LearnerConfig.read(
                        Path(self.config['learner_config'])).resume_checkpoint)
                self.state['best_cohort'] = baseline
            interval = report['against_previous']['bootstrap95_fixed_seed_sensitivity']
            regression = bool(interval and interval[1] < 0)
            prior = self.state['evaluations'][-1] if self.state['evaluations'] else {}
            report['paired_regression'] = regression
            if regression and prior.get('paired_regression'):
                self.state['stop'] = 'two_paired_regressions_restore_best_after_diagnosis'
                save(self.work / 'rollback-checkpoint.json', {
                    'checkpoint_artifact_id': self.state['best_checkpoint'],
                    'selection': 'highest_fixed_pool_paired_point_estimate',
                    'automatic_training_resume': False, 'reason': self.state['stop']})
        else:
            self.state['stop'] = 'evaluation_audit_or_originals_incomplete'
        # Full original result is retained by evaluate. This record never upgrades it.
        self.state['evaluations'].append(report)
        self.state['pending'] = None
        self.persist()

    def schedule_segment_evaluation(self):
        import shlex
        cfg = self.config.get('evaluation')
        due = self.state['next_evaluation_seconds']
        if (cfg is None or self.state['pending'] is not None
                or self.state['training_seconds'] < due
                or time.time() >= self.config['deadline_at'] - 900
                or self.state['stop'] not in {None, 'run_budget_complete'}):
            return
        required_window = cfg.get('episode_seconds', 900) + 180 + 600
        if self.config['deadline_at'] - time.time() <= required_window:
            self.telemetry('evaluation_not_started', reason='absolute_deadline_headroom',
                           due_training_seconds=due)
            return
        cp = (self.cold_checkpoint(cfg['initial_checkpoint'])
              if due == 0 and cfg.get('initial_checkpoint') else self.select_checkpoint())
        if cp['checkpoint_artifact_id'] not in self.state['protected_checkpoints']:
            self.state['protected_checkpoints'].append(cp['checkpoint_artifact_id'])
        number = len(self.state['evaluations']) + 1
        root = private_root(self.work / f'evaluation-{number:04d}')
        script = root / 'prepare.sh'
        prefix = evaluate.launcher(root / 'not-offered-plan.json',
            {'source_root': self.config['runtime_source']}).split(
                'exec "$actor_root/env/bin/python"', 1)[0]
        command = ['"$actor_root/env/bin/python"', '"$source_root/tools/friday/evaluate.py"',
            'prepare-checkpoint', '--learner-config', shlex.quote(self.config['learner_config']),
            '--checkpoint', cp['checkpoint_artifact_id'], '--execution',
            shlex.quote(cfg['execution']), '--manifest', shlex.quote(cfg['manifest']['path']),
            '--output', shlex.quote(str(root / 'round')), '--workers', str(cfg.get('workers', 4)),
            '--seeds', str(cfg.get('seeds', 50)), '--wall-seconds', str(cfg.get('wall_seconds', 7200)),
            '--episode-seconds', str(cfg.get('episode_seconds', 900))]
        command.extend(['--deadline-at', str(self.config['deadline_at'])])
        script.write_text(prefix + 'exec ' + ' '.join(command) + '\n')
        script.chmod(0o700)
        pending = {'stage': 'preparation_intent', 'root': str(root),
                   'checkpoint': cp, 'due_seconds': due}
        self.state['pending'] = pending
        self.state['next_evaluation_seconds'] += 7200
        self.persist()
        job = self.submit_cpu_step(script, root / 'preparation', name='rl4-eval-prepare',
                                  cpus=1, minutes=10, memory_gb=6)
        pending.update(stage='preparation', job_id=job)
        self.persist()

    def retain_segment_checkpoints(self):
        records = self.exchange.checkpoint_records()
        current = records[-1]['checkpoint_artifact_id'] if records else None
        if current is None or current == self.state.get('last_CP_retention'):
            return
        protected = set(self.state['protected_checkpoints'])
        if self.state.get('best_checkpoint'):
            protected.add(self.state['best_checkpoint'])
        result = self.exchange.retain_checkpoints(protected=tuple(sorted(protected)))
        save(self.work / 'CP-retention' / (current + '.json'), result)
        if not result['unverified_chunks']:
            self.state['last_CP_retention'] = current
        self.persist()

    def tick_segment(self):
        now = time.time()
        self.collect_events()
        if now >= self.config['deadline_at']:
            self.state['stop'] = 'absolute_deadline'
            self.cancel_at_deadline()
        self.collect_segment_evaluation()
        self.schedule_segment_evaluation()
        self.retain_segment_checkpoints()
        if self.signalled:
            self.state['stop'] = self.state['stop'] or 'watcher_signal'
        if (now >= self.config['deadline_at'] - 600
                or self.state['training_seconds'] >= self.config['training_seconds']):
            self.state['stop'] = self.state['stop'] or 'run_budget_complete'
        health = self.state['health']
        unknown = health.get('known_unknown', 0) + health.get('lost_offers', 0)
        total = health.get('completed_for_stop', 0)
        if total and unknown / total > self.config.get('unknown_stop_fraction', .05):
            self.state['stop'] = self.state['stop'] or 'training_unknown_fraction_above_limit'
        uncertain = health.get('unresolved_admissions', 0)
        if uncertain and uncertain / (total + uncertain) > self.config.get(
                'unknown_stop_fraction', .05):
            self.state['stop'] = self.state['stop'] or 'unresolved_admission_fraction_above_limit'
        if now >= self.config['deadline_at'] - 900 and self.state['pending'] is not None:
            pending = self.state['pending']
            if pending.get('plan'):
                save(Path(pending['plan']).parent / 'STOP', {'reason': 'absolute_deadline'})
            if pending.get('job_id'):
                self.signal_once(pending['job_id'])
        index = self.state['active']
        if index is None:
            if not self.state['stop']:
                self.start_attempt()
            self.persist()
            return
        attempt = self.state['attempts'][index]
        if not attempt.get('job_id'):
            raise ValueError('original_learner_submission_requires_reconciliation_no_retry')
        account = job_accounting(attempt['job_id'])
        if account['terminal']:
            attempt.setdefault('terminal_observed_at', now)
            self.stop_attempt(attempt, 'learner_allocation_terminal')
            import rl

            actors_terminal = True
            retired_names: set[str] = set()
            for plan_path in attempt['actor_plans']:
                plan = rl.load_plan(Path(plan_path))
                rows = rl.actor_accounting(Path(plan_path))
                if len(rows) != len(plan['actors']) or not all(r['terminal'] for r in rows):
                    actors_terminal = False
                retired_names.update(r['actor'] for r in rows if r['terminal'])
            self.state['terminal_actors'] = sorted(set(self.state['terminal_actors']) | retired_names)
            self.refresh_world_health()
            if actors_terminal:
                attempt['terminal_accounting'] = account
                self.state['active'] = None
                self.persist()
            return
        if self.state['stop'] or self.capability_lost or attempt.get('failed'):
            reason = self.state['stop'] or ('watcher_capability_lost' if self.capability_lost
                                           else 'learner_failed')
            self.stop_attempt(attempt, reason)
            if attempt.get('failed'):
                self.state['stop'] = 'learner_failed_requires_diagnosis'
            return
        self.offer_initial_actors(attempt)
        if attempt['actor_plans']:
            progress_at = attempt.get('last_progress_at')
            if progress_at and now - progress_at > self.config.get('stall_seconds', 900):
                self.state['stop'] = 'learner_progress_stalled'
                self.stop_attempt(attempt, self.state['stop'])
            else:
                self.replace_actors(attempt)
        self.persist()

    def run(self):
        import secrets

        from stpd.p1.local_inference import AUTH_ENV
        if sys.platform != 'linux':
            raise ValueError('Friday_login_owner_required')
        prior = os.environ.get(AUTH_ENV)
        os.environ[AUTH_ENV] = secrets.token_hex(32)
        for number in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT):
            signal.signal(number, lambda *_: setattr(self, 'signalled', True))
        try:
            while True:
                self.tick_segment()
                if self.state['stop'] and self.state['active'] is None:
                    pending = self.state['pending']
                    if pending is None or pending['stage'] in {'failed', 'submission_intent'}:
                        from spireagent.storage.async_archive import ArchivedStore

                        if isinstance(self.exchange.store, ArchivedStore):
                            status = self.exchange.store.archive.drain(timeout=120, owned=False)
                            save(self.work / 'archive-final.json', status)
                            self.retain_segment_checkpoints()
                        return self.state
                time.sleep(self.config.get('poll_seconds', 30))
        except BaseException as error:
            self.telemetry('owner_failed', error_type=type(error).__name__,
                           error_code=getattr(error, 'code', None))
            self.state['stop'] = self.state['stop'] or 'watcher_failure_requires_reconciliation'
            if self.state['active'] is not None:
                attempt = self.state['attempts'][self.state['active']]
                if attempt.get('job_id'):
                    self.stop_attempt(attempt, self.state['stop'])
            self.persist()
            raise
        finally:
            if prior is None:
                os.environ.pop(AUTH_ENV, None)
            else:
                os.environ[AUTH_ENV] = prior


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    work = private_root(args.work.resolve())
    with (work / 'watch.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mode = json.loads(args.config.read_bytes()).get('mode')
        if mode == 'segments':
            SegmentWatcher(read_segment_config(args.config), work).run()
        else:
            Watcher(read_config(args.config), work).run()


if __name__ == '__main__':
    main()
