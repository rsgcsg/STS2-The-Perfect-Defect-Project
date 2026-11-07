#!/usr/bin/env python3
"""Bounded automatic GPU successor holder (OPS, user decision 11-07).

When the current held allocation (``held_job_id``) is near its Slurm end, submit one
successor holder (``gpu_session.py``-style script from an operator template) that begins
shortly before the current one ends, so the next window can take over its GPU.

Bounds, all enforced here and never relaxed:
- at most ``max_gpus`` (<= 2) GPU jobs of ours, running or pending, at any moment,
  the successor included;
- at most one pending GPU job of ours: a pending one blocks any new submission;
- submitted only when the current holder is within ``lead_seconds`` of its end;
- one successor per holder, with a typed record written before ``sbatch``;
- an uncertain submission (intent without receipt, or ``sbatch`` failing) is never
  retried: the record stays ``outcome_unknown`` and every later check alarms.

The handover itself (``release_swap prepare --held-job <successor>``) is not done here.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import submit as scheduler
from batch import private_root, read_published, save

SCHEMA = 'spireagent/friday-gpu-successor-1'
ACTIVE = {'RUNNING', 'PENDING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED', 'REQUEUED',
          'RESIZING', 'REQUEUE_HOLD', 'REQUEUE_FED'}
TOKENS = ('@HOLDER_ROOT@', '@BUDGET_FILE@')
DEFAULTS = {'partition': 'smp_gpu', 'gres': 'gpu:a100:1', 'cpus': 16, 'mem_gb': 64,
            'time_seconds': 172800, 'lead_seconds': 14400, 'overlap_seconds': 1800,
            'max_gpus': 2, 'job_name': 'rl4-gpu-successor'}
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$')


def settings(raw: Any) -> dict[str, Any]:
    """Explicit opt-in ``gpu_successor`` config; every bound checked."""
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS) - {'template', 'root', 'authority'}:
        raise ValueError('explicit_gpu_successor_settings_required')
    value = {**DEFAULTS, **raw}
    ints = ('cpus', 'mem_gb', 'time_seconds', 'lead_seconds', 'overlap_seconds', 'max_gpus')
    if (any(type(value[k]) is not int for k in ints)
            or not 1 <= value['max_gpus'] <= 2
            or not 3600 <= value['time_seconds'] <= 172800
            or not 600 <= value['lead_seconds'] <= 86400
            or not 0 <= value['overlap_seconds'] <= min(7200, value['lead_seconds'])
            or not 1 <= value['cpus'] <= 64 or not 1 <= value['mem_gb'] <= 512
            or not all(isinstance(value.get(k), str) and value[k].startswith('/')
                       for k in ('template', 'root'))
            or not isinstance(value.get('authority'), str) or not value['authority'].strip()
            or not all(NAME.fullmatch(str(value[k])) for k in ('partition', 'gres', 'job_name'))):
        raise ValueError('explicit_gpu_successor_settings_required')
    return value


def slurm_time(text: str) -> float | None:
    try:
        return datetime.strptime(text, '%Y-%m-%dT%H:%M:%S').timestamp()
    except ValueError:
        return None


def our_gpu_jobs(output: Callable[[list[str]], str] = scheduler.output) -> list[dict[str, Any]]:
    """Every GPU job of ours that holds or may soon hold a card."""
    raw = output(['squeue', '-h', '-u', scheduler.username(), '-o', '%i|%T|%b|%e'])
    jobs = []
    for line in raw.splitlines():
        fields = line.split('|')
        if len(fields) >= 4 and 'gpu' in fields[2] and fields[1] in ACTIVE:
            jobs.append({'job_id': fields[0], 'state': fields[1], 'end': slurm_time(fields[3])})
    return jobs


def records(root: Path) -> list[dict[str, Any]]:
    folder = root / 'records'
    if not folder.is_dir():
        return []
    return [r for r in (read_published(p) for p in sorted(folder.glob('*.json'))) if r]


def decide(cfg: dict[str, Any], held_job: str, jobs: list[dict[str, Any]],
           history: list[dict[str, Any]], now: float) -> dict[str, Any]:
    """Pure decision: ``none`` (nothing to do), ``alarm`` (typed, nothing submitted) or
    ``submit`` (with the begin time)."""
    holder = next((j for j in jobs if j['job_id'] == held_job), None)
    unknown = [r for r in history if r.get('outcome') == 'outcome_unknown']
    if unknown:
        return {'action': 'alarm', 'reason': 'gpu_successor_submission_outcome_unknown_no_retry',
                'records': [r['predecessor'] for r in unknown]}
    if any(r.get('predecessor') == held_job for r in history):
        return {'action': 'none', 'reason': 'successor_already_submitted_for_this_holder'}
    if holder is None or holder['state'] != 'RUNNING':
        return {'action': 'none', 'reason': 'holder_not_running'}
    if holder['end'] is None:
        return {'action': 'alarm', 'reason': 'gpu_holder_end_unknown', 'holder': held_job}
    if holder['end'] - now > cfg['lead_seconds']:
        return {'action': 'none', 'reason': 'holder_not_near_expiry'}
    pending = [j['job_id'] for j in jobs if j['state'] != 'RUNNING']
    if pending:
        return {'action': 'alarm', 'reason': 'gpu_job_already_pending_no_second_successor',
                'pending': pending, 'holder': held_job}
    if len(jobs) + 1 > cfg['max_gpus']:
        return {'action': 'alarm', 'reason': 'gpu_cap_reached_no_successor',
                'gpu_jobs': [j['job_id'] for j in jobs], 'max_gpus': cfg['max_gpus'],
                'holder': held_job}
    begin = max(now, holder['end'] - cfg['overlap_seconds'])
    return {'action': 'submit', 'reason': 'gpu_holder_near_expiry', 'holder': held_job,
            'holder_end': holder['end'], 'begin': begin}


def submit_successor(cfg: dict[str, Any], decision: dict[str, Any], now: float,
                     output: Callable[[list[str]], str] = scheduler.output) -> dict[str, Any]:
    root = Path(cfg['root'])
    template = Path(cfg['template']).read_text()
    if not all(token in template for token in TOKENS):
        raise ValueError('gpu_successor_template_tokens_required')
    holder = decision['holder']
    with scheduler.user_submission_lock():
        # Re-observe under the lock: another writer may have submitted meanwhile.
        again = decide(cfg, holder, our_gpu_jobs(output), records(root), now)
        if again['action'] != 'submit':
            return again
        record_path = private_root(root / 'records') / f'{holder}.json'
        work = root / f'successor-of-{holder}'
        work.mkdir(mode=0o700, parents=True, exist_ok=False)
        budget = work / 'budget.json'
        save(budget, {'seconds': cfg['time_seconds'], 'authority': cfg['authority'],
                      'automatic_training': False, 'successor_of': holder})
        script = work / 'allocation.sh'
        script.write_text(template.replace('@HOLDER_ROOT@', str(work / 'runtime'))
                          .replace('@BUDGET_FILE@', str(budget)))
        script.chmod(0o700)
        begin = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(again['begin']))
        command = ['sbatch', '--parsable', '--partition=' + cfg['partition'], '--nodes=1',
                   '--ntasks=1', '--gres=' + cfg['gres'], f"--cpus-per-task={cfg['cpus']}",
                   f"--mem={cfg['mem_gb']}G", f"--time={cfg['time_seconds'] // 60}",
                   '--no-requeue', '--export=ALL', '--begin=' + begin,
                   '--job-name=' + cfg['job_name'], '--output=' + str(work / 'GPU-%j.out'),
                   '--error=' + str(work / 'GPU-%j.err'), str(script)]
        record = {'schema': SCHEMA, 'predecessor': holder, 'holder_end': again['holder_end'],
                  'begin': begin, 'command': command, 'authority': cfg['authority'],
                  'max_gpus': cfg['max_gpus'], 'intent_at': now, 'outcome': 'outcome_unknown'}
        save(record_path, record)
        try:
            job = output(command).split(';')[0]
        except (OSError, subprocess.SubprocessError) as error:
            # sbatch may have reached the controller; never retried automatically.
            return {'action': 'alarm',
                    'reason': 'gpu_successor_submission_outcome_unknown_no_retry',
                    'holder': holder, 'error_type': type(error).__name__}
        save(record_path, {**record, 'outcome': 'submitted', 'job_id': job,
                           'submitted_at': time.time()})
    return {'action': 'submitted', 'reason': 'gpu_successor_submitted', 'holder': holder,
            'job_id': job, 'begin': begin, 'record': str(record_path)}


def tick(cfg: dict[str, Any], held_job: str, *, now: float | None = None,
         output: Callable[[list[str]], str] = scheduler.output) -> dict[str, Any]:
    now = time.time() if now is None else now
    decision = decide(cfg, held_job, our_gpu_jobs(output), records(Path(cfg['root'])), now)
    if decision['action'] != 'submit':
        return decision
    return submit_successor(cfg, decision, now, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, required=True,
                        help='watch.json with gpu_successor and held_job_id')
    parser.add_argument('--check-only', action='store_true',
                        help='print the decision, submit nothing')
    arguments = parser.parse_args()
    watch_config = json.loads(arguments.config.read_bytes())
    chosen = settings(watch_config['gpu_successor'])
    held = watch_config['held_job_id']
    if arguments.check_only:
        print(json.dumps(decide(chosen, held, our_gpu_jobs(), records(Path(chosen['root'])),
                                time.time())))
    else:
        print(json.dumps(tick(chosen, held)))
