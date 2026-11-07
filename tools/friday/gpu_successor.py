#!/usr/bin/env python3
"""Bounded automatic GPU successor holder (OPS, user decision 11-07).

When the current held allocation (``held_job_id``) is near its Slurm end, submit one
successor holder (``gpu_session.py``-style script from an operator template) that begins
shortly before the current one ends, so the next window can take over its GPU.

The successor is exactly what ``rl.launch(held_job_id=...)`` accepts: one card of the
watch's ``gpu`` type (``gres/gpu:<gpu>:1``) named ``rl4-learner``
(``rl.held_allocation_detail``).

Bounds, all enforced here and never relaxed:
- at most ``max_gpus`` (<= 2) GPU cards of ours in every non-terminal job, the successor
  included; a GPU job whose card count cannot be read counts as over the cap;
- at most one pending GPU job of ours: a pending one blocks any new submission;
- submitted only when the current holder is within ``lead_seconds`` of its end;
- one successor per holder, with a typed record written before ``sbatch``;
- an uncertain submission (intent without receipt, ``sbatch`` failing or timing out)
  is never retried: the record stays ``outcome_unknown`` and every later check alarms
  until an operator resolves it (python/docs/RL_LONG_RUN.zh-CN.md).

Typed alarms, no submission: a successor still queued after its begin time, and a holder
that is gone before any successor was submitted. The handover itself
(``release_swap prepare --held-job <successor>``) is not done here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import submit as scheduler
from batch import private_root, read_published, save

SCHEMA = 'spireagent/friday-gpu-successor-1'
JOB_NAME = 'rl4-learner'  # rl.HELD_JOB_NAME; the held launch path accepts only this
TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'PREEMPTED', 'NODE_FAIL',
            'BOOT_FAIL', 'DEADLINE', 'OUT_OF_MEMORY', 'SPECIAL_EXIT', 'REVOKED'}
NOT_PENDING = {'RUNNING', 'COMPLETING'}
GPUS = {'a100', 'a30'}
TOKENS = ('@HOLDER_ROOT@', '@BUDGET_FILE@')
DEFAULTS = {'partition': 'smp_gpu', 'cpus': 16, 'mem_gb': 64, 'time_seconds': 172800,
            'lead_seconds': 14400, 'overlap_seconds': 1800, 'max_gpus': 2,
            'pending_alarm_seconds': 600}
CARDS = re.compile(r'gpu(?::[A-Za-z0-9_.-]+)?:(\d+)')
SQUEUE_SECONDS, SBATCH_SECONDS = 60, 120


def settings(raw: Any, gpu: str = 'a100', *, check_paths: bool = True) -> dict[str, Any]:
    """Explicit opt-in ``gpu_successor`` config; every bound checked. The card type comes
    from the watch's ``gpu``. ``check_paths`` also checks the template and the record root
    now, not in the last hours before expiry."""
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS) - {'template', 'root', 'authority'}:
        raise ValueError('explicit_gpu_successor_settings_required')
    value = {**DEFAULTS, **raw}
    ints = ('cpus', 'mem_gb', 'time_seconds', 'lead_seconds', 'overlap_seconds', 'max_gpus',
            'pending_alarm_seconds')
    if (gpu not in GPUS or any(type(value[k]) is not int for k in ints)
            or not 1 <= value['max_gpus'] <= 2
            or not 3600 <= value['time_seconds'] <= 172800
            or not 600 <= value['lead_seconds'] <= 86400
            or not 0 <= value['overlap_seconds'] <= min(7200, value['lead_seconds'])
            or not 60 <= value['pending_alarm_seconds'] <= 7200
            or not 1 <= value['cpus'] <= 64 or not 1 <= value['mem_gb'] <= 512
            or not all(isinstance(value.get(k), str) and value[k].startswith('/')
                       for k in ('template', 'root'))
            or not isinstance(value.get('authority'), str) or not value['authority'].strip()
            or value['partition'] != 'smp_gpu'):  # rl.launch looks for holders there
        raise ValueError('explicit_gpu_successor_settings_required')
    value['gpu'], value['gres'] = gpu, f'gpu:{gpu}:1'
    if check_paths:
        check_template(Path(value['template']))
        check_root(Path(value['root']))
    return value


def check_template(path: Path) -> str:
    try:
        text = path.read_text()
    except OSError as error:
        raise ValueError('gpu_successor_template_unreadable') from error
    if not all(token in text for token in TOKENS):
        raise ValueError('gpu_successor_template_tokens_required')
    return text


def check_root(root: Path) -> None:
    """``private_root``'s rules without creating anything: canonical, no symlink, and an
    existing root (or its parent) owned by us; an existing root must be 0700."""
    if root.resolve() != root or root.is_symlink():
        raise ValueError('gpu_successor_root_must_be_canonical')
    existing = root if root.exists() else root.parent
    info = existing.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or (
            existing == root and stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('gpu_successor_root_must_be_owned_private_directory')


def slurm(command: list[str]) -> str:
    timeout = SBATCH_SECONDS if command[0] == 'sbatch' else SQUEUE_SECONDS
    return subprocess.check_output(command, text=True, timeout=timeout).strip()


def slurm_time(text: str) -> float | None:
    try:
        return datetime.strptime(text, '%Y-%m-%dT%H:%M:%S').timestamp()
    except ValueError:
        return None


def our_gpu_jobs(output: Callable[[list[str]], str] = slurm) -> list[dict[str, Any]]:
    """Every non-terminal GPU job of ours, with its card count (None when unreadable)."""
    raw = output(['squeue', '-h', '-u', scheduler.username(), '-o', '%i|%T|%b|%e'])
    jobs = []
    for line in raw.splitlines():
        fields = line.split('|')
        if len(fields) < 4 or 'gpu' not in fields[2] or fields[1] in TERMINAL:
            continue
        counts = [int(n) for n in CARDS.findall(fields[2])]
        jobs.append({'job_id': fields[0], 'state': fields[1], 'end': slurm_time(fields[3]),
                     'cards': sum(counts) if counts else None})
    return jobs


def records(root: Path) -> list[dict[str, Any]]:
    """Every record; an unreadable or foreign file is kept as ``unreadable`` (fail closed)."""
    folder = root / 'records'
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob('*.json')):
        value = read_published(path)
        if not isinstance(value, dict) or value.get('schema') != SCHEMA:
            value = {'outcome': 'unreadable', 'predecessor': path.stem, 'path': str(path)}
        found.append(value)
    return found


def decide(cfg: dict[str, Any], held_job: str, jobs: list[dict[str, Any]],
           history: list[dict[str, Any]], now: float) -> dict[str, Any]:
    """Pure decision: ``none`` (nothing to do), ``alarm`` (typed, nothing submitted) or
    ``submit`` (with the begin time)."""
    holder = next((j for j in jobs if j['job_id'] == held_job), None)
    blocked = [r for r in history if r.get('outcome') in {'outcome_unknown', 'unreadable'}]
    if blocked:
        return {'action': 'alarm', 'holder': held_job,
                'reason': ('gpu_successor_submission_outcome_unknown_no_retry'
                           if any(r['outcome'] == 'outcome_unknown' for r in blocked)
                           else 'gpu_successor_record_unreadable_no_submission'),
                'records': [r['predecessor'] for r in blocked]}
    mine = next((r for r in history if r.get('predecessor') == held_job), None)
    if mine is not None:
        successor = next((j for j in jobs if j['job_id'] == mine.get('job_id')), None)
        begin = mine.get('begin_at')
        if (successor is not None and successor['state'] not in NOT_PENDING
                and isinstance(begin, (int, float))
                and now - begin > cfg['pending_alarm_seconds']):
            return {'action': 'alarm', 'reason': 'gpu_successor_pending_past_begin',
                    'holder': held_job, 'job_id': mine.get('job_id'),
                    'waited_seconds': round(now - begin)}
        return {'action': 'none', 'reason': 'successor_already_submitted_for_this_holder'}
    if holder is None:
        return {'action': 'alarm', 'reason': 'gpu_holder_gone_without_successor',
                'holder': held_job}
    if holder['state'] != 'RUNNING':
        return {'action': 'none', 'reason': 'holder_not_running_yet'}
    if holder['end'] is None:
        return {'action': 'alarm', 'reason': 'gpu_holder_end_unknown', 'holder': held_job}
    if holder['end'] - now > cfg['lead_seconds']:
        return {'action': 'none', 'reason': 'holder_not_near_expiry'}
    pending = [j['job_id'] for j in jobs if j['state'] not in NOT_PENDING]
    if pending:
        return {'action': 'alarm', 'reason': 'gpu_job_already_pending_no_second_successor',
                'pending': pending, 'holder': held_job}
    cards = [j['cards'] for j in jobs]
    if None in cards or sum(cards) + 1 > cfg['max_gpus']:
        return {'action': 'alarm', 'reason': 'gpu_cap_reached_no_successor',
                'gpu_jobs': {j['job_id']: j['cards'] for j in jobs},
                'max_gpus': cfg['max_gpus'], 'holder': held_job}
    begin = max(now, holder['end'] - cfg['overlap_seconds'])
    return {'action': 'submit', 'reason': 'gpu_holder_near_expiry', 'holder': held_job,
            'holder_end': holder['end'], 'begin': begin}


def submit_successor(cfg: dict[str, Any], decision: dict[str, Any], now: float,
                     output: Callable[[list[str]], str] = slurm) -> dict[str, Any]:
    root = Path(cfg['root'])
    template = check_template(Path(cfg['template']))
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
                   '--job-name=' + JOB_NAME, '--output=' + str(work / 'GPU-%j.out'),
                   '--error=' + str(work / 'GPU-%j.err'), str(script)]
        record = {'schema': SCHEMA, 'predecessor': holder, 'holder_end': again['holder_end'],
                  'begin': begin, 'begin_at': again['begin'], 'command': command,
                  'authority': cfg['authority'], 'max_gpus': cfg['max_gpus'],
                  'intent_at': now, 'outcome': 'outcome_unknown'}
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
         output: Callable[[list[str]], str] = slurm) -> dict[str, Any]:
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
                        help='check settings, template and root; print the decision; '
                             'submit nothing')
    arguments = parser.parse_args()
    watch_config = json.loads(arguments.config.read_bytes())
    chosen = settings(watch_config['gpu_successor'], watch_config.get('gpu', 'a100'))
    held = watch_config['held_job_id']
    if arguments.check_only:
        print(json.dumps({'settings': 'ok', 'gres': chosen['gres'], 'job_name': JOB_NAME,
                          'decision': decide(chosen, held, our_gpu_jobs(),
                                             records(Path(chosen['root'])), time.time())}))
    else:
        print(json.dumps(tick(chosen, held)))
