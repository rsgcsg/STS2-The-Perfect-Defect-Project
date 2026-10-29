#!/usr/bin/env python3
"""One bounded Friday Slurm watcher; actors/evaluations retain frozen runtime owners."""
from __future__ import annotations

import argparse
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
import submit as scheduler
from batch import hashed, private_root, save
from spireagent.json_boundary import json_bytes
from spireagent.rl_exchange import Exchange

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
    plan = json.loads(Path(value['actor_plan']).read_bytes())
    if plan['run'] != value['run'] or plan['peer'] != value['peer']:
        raise ValueError('watch_actor_run_peer_binding_mismatch')
    # Pin both the original runtime and actual submitted actor allocation.
    if plan['source_root'] != value['runtime_source']:
        raise ValueError('watch_frozen_runtime_binding_mismatch')
    submission = json.loads((Path(value['actor_plan']).parent / 'submission.json').read_bytes())
    if str(submission['job_id']).split(';')[0] != str(value['actor_job_id']):
        raise ValueError('watch_actor_job_binding_mismatch')
    return value


def evaluate_result(path: Path, version: int, updates: int, *,
                    confirmation: bool = False) -> dict[str, Any]:
    result = json.loads(path.read_bytes())
    report = result['ledger_report']
    groups = report['groups']
    floors = [row['max_observed_floor'] for row in report['slots']
              if row['status'] == 'finished' and row.get('max_observed_floor') is not None]
    planned = 40 if confirmation else 20
    complete = (report['score_admitted'] is True and report['planned'] == planned
                and report['not_started'] == report['reserved'] == report['save_pending'] == 0
                and report['save_failed'] == report['saved_unscored'] == 0
                and report['trajectory_pending'] == report['censored'] == 0
                and report['win'] + report['loss'] + report['unknown'] == planned)
    return {'version': version, 'updates': updates, 'wins': report['win'],
            'evaluation_kind': 'confirmation' if confirmation else 'fixed20',
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
    if games and sum(g['status'] == 'unknown' for g in games) / len(games) > 0.05:
        return 'training_unknown_fraction_above_5_percent'
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
    if (len(completed) >= 2 and all(row['mean_max_floor'] is not None
            and row['mean_max_floor'] < 11.75 for row in completed[-2:])):
        return 'two_evaluations_below_baseline_by_more_than_two_floors'
    return None


TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'NODE_FAIL',
            'OUT_OF_MEMORY', 'PREEMPTED', 'BOOT_FAIL', 'DEADLINE', 'REVOKED'}


def job_accounting(job_id: str) -> dict[str, Any]:
    raw = subprocess.check_output(['sacct', '-n', '-P', '-j', job_id,
                                   '--format=JobIDRaw,State,ExitCode'], text=True)
    rows = []
    for line in raw.splitlines():
        fields = line.split('|')
        if len(fields) >= 3 and '.' not in fields[0]:
            rows.append({'job_id': fields[0], 'state': fields[1].split()[0].rstrip('+'),
                         'exit_code': fields[2]})
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
        except Exception as error:
            print(json.dumps({'kind': 'watcher_telemetry_failed', 'event': kind,
                              'error_type': type(error).__name__}), file=sys.stderr, flush=True)
            return None

    def stop(self, reason: str):
        # Persist intent first. Re-sending USR1 and an identical control request is safe;
        # no game or allocation is retried after an uncertain scheduler response.
        if self.state['stop'] is None:
            self.state['stop'] = reason
            self.persist()
        save(Path(self.config['actor_plan']).parent / 'STOP',
             {'reason': self.state['stop'], 'run': self.config['run']})
        actor_signal = subprocess.run(
            ['scancel', '--signal=USR1', '--batch', str(self.config['actor_job_id'])],
            check=False, capture_output=True)
        if 'drain_started_at' not in self.state:
            self.state['drain_started_at'] = time.time()
            self.persist()
        try:
            self.exchange.request_stop(self.state['stop'])
        except Exception as error:
            self.state['learner_stop_pending'] = True
            self.state['learner_stop_error_type'] = type(error).__name__
        else:
            self.state['learner_stop_pending'] = False
        self.persist()
        if self.state['pending']:
            pending = self.state['pending']
            save(Path(pending['plan']).parent / 'STOP', {'reason': self.state['stop']})
            if pending.get('job_id'):
                subprocess.run(['scancel', '--signal=USR1', '--batch', pending['job_id']],
                               check=False, capture_output=True)
        self.telemetry('stop_requested', reason=self.state['stop'],
                     actor_signal_exit_code=actor_signal.returncode,
                     learner_stop_pending=self.state['learner_stop_pending'])

    def prepare_evaluation(self, model: dict[str, Any], now: float, *,
                           confirmation: bool = False):
        kind = 'confirmation' if confirmation else 'fixed20'
        root = self.work / 'evaluations' / f"{kind}-v{model['version']:06d}-{time.time_ns()}"
        root.mkdir(parents=True, mode=0o700)
        package = self.exchange.materialize_model(model, self.work / 'models')
        execution = json.loads(Path(self.config['evaluation_execution']).read_bytes())
        execution['models'] = {'latest': {'artifact_id': model['model_artifact_id'],
                                           'package_directory': str(package)}}
        execution_path = root / 'input-execution.json'
        save(execution_path, execution)
        plan_path = root / 'plan' / 'plan.json'
        evaluate.prepare(execution_path, plan_path.parent, selections={'latest': 'latest'},
            workers=1 if self.config['evaluation_mode'] == 'direct' else self.config.get('evaluation_workers', 4),
            wall_seconds=10800 if confirmation else 7200,
            manifest_path=Path(self.config['evaluation_manifest']),
            seed_prefix=40 if confirmation else 20,
            runtime_source=Path(self.config['runtime_source']))
        pending = {'plan': str(plan_path), 'version': model['version'],
                   'updates': model.get('training', {}).get('updates', 0),
                   'state': 'submission_intent', 'job_id': None, 'evaluation_kind': kind}
        self.state['pending'] = pending
        if not confirmation:
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
                if not confirmation:
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
                confirmation=pending.get('evaluation_kind') == 'confirmation')
            self.state['evaluations'].append(result)
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
        except Exception as error:  # noqa: BLE001 -- telemetry failure must not suppress stop rules
            actors = {'observation': 'unavailable', 'error_type': type(error).__name__}
            self.telemetry('attention', reason='actor_accounting_unavailable', **actors)
        reason = ('watcher_signal' if self.signalled else
                  stop_reason(now, self.config, games, event, self.state['evaluations']))
        if self.state['stop'] or reason:
            self.stop(self.state['stop'] or reason)
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
        except Exception as error:
            self.telemetry('attention', reason='learner_drain_observation_unavailable',
                           error_type=type(error).__name__)
        try:
            accounting = job_accounting(str(self.config['actor_job_id']))
        except Exception as error:
            accounting = {'terminal': False, 'error_type': type(error).__name__}
        try:
            evaluation = self.evaluation_drain()
        except Exception as error:
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

    def run(self):
        if sys.platform != 'linux' or not os.environ.get('SLURM_JOB_ID'):
            raise ValueError('Friday_Slurm_watcher_required')
        for number in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT):
            signal.signal(number, lambda *_: setattr(self, 'signalled', True))
        signal.signal(signal.SIGUSR2, lambda *_: setattr(self, 'maintenance_requested', True))
        self.telemetry('watcher_started', config_sha256=self.state['config_sha256'],
                       job_id=os.environ['SLURM_JOB_ID'])
        while True:
            try:
                self.tick()
            except Exception as error:
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
            interval = self.config.get('poll_seconds', 900)
            if self.state['stop']:
                interval = min(interval, max(0, 2400 - (time.time() - self.state['drain_started_at'])))
            else:
                interval = min(interval, max(0, self.config['started_at']
                    + self.config['wall_seconds'] - time.time()))
            time.sleep(interval)


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
