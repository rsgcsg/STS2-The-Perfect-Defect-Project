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
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import evaluate
from batch import hashed, private_root, save
from spireagent.json_boundary import json_bytes
from spireagent.rl_exchange import Exchange

scheduler = evaluate.scheduler
SCHEMA = 'spireagent/friday-rl-watch-config-1'


def read_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if (value.get('schema') != SCHEMA or value.get('evaluation_mode') not in {'submit', 'direct'}
            or not 1 <= value.get('wall_seconds', 0) <= 72000
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
    return value


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
        if len(fields) >= 3 and '.' not in fields[0]:
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
        self.state = (json.loads(self.state_path.read_bytes()) if self.state_path.exists() else
                      {'config_sha256': hashlib.sha256(json_bytes(config)).hexdigest(),
                       'shards': {}, 'evaluations': [], 'pending': None, 'stop': None,
                       'last_evaluation_at': config['started_at']})
        if self.state['config_sha256'] != hashlib.sha256(json_bytes(config)).hexdigest():
            raise ValueError('watch_resume_configuration_changed')
        self.direct: subprocess.Popen | None = None
        self.signalled = False
        self.maintenance_requested = False

    def persist(self):
        save(self.state_path, self.state)

    def publish(self, kind: str, **fields: Any):
        value = {'schema': 'spireagent/rl-watch-status-1', 'run': self.config['run'],
                 'at': time.time(), 'kind': kind, **fields}
        key = f'{self.exchange.prefix}status/{time.time_ns():020d}.json'
        self.exchange.blobs.put_if_absent(key, json_bytes(value))
        return value

    def telemetry(self, kind: str, **fields: Any):
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
            return signals[job_id]
        signals[job_id] = {'state': 'intent', 'at': time.time()}
        self.persist()
        try:
            result = subprocess.run(['scancel', '--signal=USR1', '--batch', job_id],
                                    check=False, capture_output=True)
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            signals[job_id].update(state='outcome_unknown', error_type=type(error).__name__)
        else:
            signals[job_id].update(state='response_received', exit_code=result.returncode)
        self.persist()
        return signals[job_id]

    def stop(self, reason: str):
        if self.state['stop'] is None:
            self.state['stop'] = reason
            self.persist()
        save(Path(self.config['actor_plan']).parent / 'STOP',
             {'reason': self.state['stop'], 'run': self.config['run']})
        actor_signal = self.signal_once(str(self.config['actor_job_id']))
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
                           due_at: float | None = None):
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
            runtime_source=Path(self.config['runtime_source']), observation_report=tier is not None)
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

    def collect_evaluation(self):
        pending = self.state['pending']
        if pending is None:
            return
        result_path = Path(pending['plan']).parent / 'result.json'
        if result_path.exists():
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

        plan = evaluate.load_plan(Path(pending['plan']))
        original = json.loads(result_path.read_bytes())
        metadata = original.get('observation_report')
        if (metadata is None or metadata.get('schema') != 'spireagent/evaluation-observation-files-1'
                or metadata.get('ledger_report_sha256') != hashlib.sha256(
                    json_bytes(original['ledger_report'])).hexdigest()):
            raise ValueError('evaluation_job_observation_report_required')
        files = {}
        for name in ('source', 'details', 'cohort'):
            reference = metadata['files'][name]
            if Path(reference['path']).name != reference['path']:
                raise ValueError('evaluation_report_file_name_required')
            files[name] = evaluate.read_pinned({
                'path': str(result_path.parent / 'evidence' / reference['path']),
                'sha256': reference['sha256']})
        if (files['source']['ledger_report'] != original['ledger_report']
                or files['source']['plan_sha256'] != original['plan_sha256']
                or files['cohort']['source_sha256'] != metadata['files']['source']['sha256']
                or files['cohort']['details_sha256'] != metadata['files']['details']['sha256']):
            raise ValueError('evaluation_report_original_binding_mismatch')
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

    def actor_health(self) -> dict[str, Any]:
        """Scheduler facts distinguish a healthy learner from lost actor capacity."""
        accounting = job_accounting(str(self.config['actor_job_id']))
        plan = json.loads(Path(self.config['actor_plan']).read_bytes())
        expected = len(plan['actors'])
        rows = accounting['rows']
        running_jobs = {row['job_id'] for row in rows if row['state'] == 'RUNNING'}
        failed = [row for row in rows if row['state'] in TERMINAL
                  and (row['state'] != 'COMPLETED' or row['exit_code'] != '0:0')]
        stopped, results, active = [], [], set()
        actor_root = Path(self.config['actor_plan']).parent / 'actors'
        for ref in plan['actors']:
            root = actor_root / ref['actor']
            owner_path, result_path = root / 'owner.json', root / 'result.json'
            owner = json.loads(owner_path.read_bytes()) if owner_path.exists() else None
            preserved = result_path.exists()
            if not preserved and owner is not None:
                # rl.run seals the exit result here BEFORE its potentially long snapshot.
                result_path = Path(owner['scratch']) / 'result.json'
            if not result_path.exists():
                if owner is not None and str(owner['job_id']) in running_jobs:
                    active.add(str(owner['job_id']))
                continue
            value = json.loads(result_path.read_bytes())
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
        # Preserve the historical Slurm-running field; active_actors is the game-loop capacity.
        signature = json.dumps({'failed': failed, 'actor_results': results,
                                'missing': not rows}, sort_keys=True)
        if (failed or results or not rows) and self.state.get('actor_attention') != signature:
            self.publish('attention', reason='actor_capacity_requires_owner_read', actors=health)
            self.state['actor_attention'] = signature
        return health

    def tick(self, now: float | None = None):
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
        if self.state['stop'] or reason:
            self.stop(self.state['stop'] or reason)
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
            accounting = job_accounting(str(self.config['actor_job_id']))
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            accounting = {'terminal': False, 'error_type': type(error).__name__}
        try:
            evaluation = self.evaluation_drain()
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            evaluation = {'terminal': False, 'error_type': type(error).__name__}
        complete = learner_finished and accounting['terminal'] and evaluation['terminal']
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
        interval = self.config.get('poll_seconds', 900)
        if self.state['stop']:
            return min(interval, max(0, 2400 - (now - self.state['drain_started_at'])))
        interval = min(interval, max(0, self.config['started_at']
            + self.config['wall_seconds'] - now))
        if self.config.get('evaluation_tiers') is not None and not self.maintenance_requested:
            schedules = self.state.get('tier_next_due', {})
            due_at = min(schedules.get(t['name'], self.config['started_at'] + t['seconds'])
                         for t in tiers(self.config))
            due_in = due_at - now
            if due_in > 0:
                return min(interval, due_in)
            if tick_started_at is not None and tick_started_at < due_at:
                return 0
            return interval
        if self.state['pending'] is None and not self.maintenance_requested:
            due_at = (self.state['last_evaluation_at']
                      + self.config.get('evaluation_seconds', 18000))
            due_in = due_at - now
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    work = private_root(args.work.resolve())
    with (work / 'watch.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        Watcher(read_config(args.config), work).run()


if __name__ == '__main__':
    main()
