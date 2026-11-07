"""Bounded automatic GPU successor holder: at most two GPUs, one pending, no retry."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest
import test_friday_watch_segments as segments
from test_friday_rl import rl  # noqa: F401 -- the rl.py module fixture
from test_friday_watch import FRIDAY, module

HOUR = 3600.0
NOW = 1794150000.0
END = NOW + 3 * HOUR  # holder within the 4 h lead
TEMPLATE = ('#!/bin/bash\nexec python gpu_session.py --root @HOLDER_ROOT@ '
            '--budget-file @BUDGET_FILE@\n')


@pytest.fixture
def gs(monkeypatch):
    batch = module('gs_batch', FRIDAY / 'batch.py')
    with patch.dict(sys.modules, {'batch': batch}):
        submit = module('gs_submit', FRIDAY / 'submit.py')
        with patch.dict(sys.modules, {'submit': submit}):
            value = module('gpu_successor', FRIDAY / 'gpu_successor.py')
    monkeypatch.setattr(value.scheduler, 'user_submission_lock', nullcontext)
    monkeypatch.setattr(value.scheduler, 'username', lambda: 'me')
    return value


@pytest.fixture
def cfg(gs, tmp_path):
    template = tmp_path / 'template.sh'
    template.write_text(TEMPLATE)
    root = tmp_path.resolve() / 'successors'
    return gs.settings({'template': str(template), 'root': str(root),
                        'authority': 'user 11-07 23:5x: bounded auto successor, <= 2 GPUs'},
                       'a100')


def stamp(at: float) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(at))


class Slurm:
    """squeue rows for our jobs; sbatch appends a pending successor as Slurm shows it."""

    def __init__(self, rows, fail=False):
        self.rows, self.fail, self.submitted = list(rows), fail, []
        self.on_squeue = None

    def __call__(self, command):
        if command[0] == 'squeue':
            if self.on_squeue:
                self.on_squeue(self)
            return '\n'.join(self.rows)
        assert command[0] == 'sbatch'
        if self.fail:
            raise subprocess.CalledProcessError(1, 'sbatch')
        self.submitted.append(command)
        gres = next(c for c in command if c.startswith('--gres=')).split('=', 1)[1]
        self.rows.append(f'9{len(self.submitted)}|PENDING|gres/{gres}|Unknown')
        return f'9{len(self.submitted)};friday'


def test_settings_are_bounded_and_explicit(gs, cfg):
    assert cfg['max_gpus'] == 2 and cfg['lead_seconds'] == 14400
    base = {k: cfg[k] for k in ('template', 'root', 'authority')}
    for bad in ({**base, 'max_gpus': 3}, {**base, 'authority': ' '}, {**base, 'extra': 1},
                {**base, 'root': 'relative'}, {**base, 'time_seconds': 172801},
                {**base, 'gres': 'gpu:a100:2'}, {**base, 'job_name': 'x'},
                {k: v for k, v in base.items() if k != 'authority'}):
        with pytest.raises(ValueError, match='explicit_gpu_successor_settings_required'):
            gs.settings(bad)
    with pytest.raises(ValueError, match='explicit_gpu_successor_settings_required'):
        gs.settings(base, 'h100')
    assert gs.settings(base, 'a30')['gres'] == 'gpu:a30:1'  # from the watch's gpu, one card


def test_template_and_root_are_checked_when_the_config_is_read(gs, cfg, tmp_path):
    base = {k: cfg[k] for k in ('template', 'root', 'authority')}
    Path(cfg['template']).write_text('#!/bin/bash\nexec python gpu_session.py\n')
    with pytest.raises(ValueError, match='gpu_successor_template_tokens_required'):
        gs.settings(base)
    with pytest.raises(ValueError, match='gpu_successor_template_unreadable'):
        gs.settings({**base, 'template': str(tmp_path / 'missing.sh')})
    Path(cfg['template']).write_text(TEMPLATE)
    link = tmp_path.resolve() / 'link'
    link.symlink_to(tmp_path.resolve())
    with pytest.raises(ValueError, match='gpu_successor_root_must_be_canonical'):
        gs.settings({**base, 'root': str(link / 'successors')})
    loose = tmp_path.resolve() / 'loose'
    loose.mkdir(mode=0o755)
    loose.chmod(0o755)
    with pytest.raises(ValueError, match='gpu_successor_root_must_be_owned_private'):
        gs.settings({**base, 'root': str(loose)})
    assert gs.settings({**base, 'template': '/nonexistent'}, check_paths=False)


def test_successor_matches_the_held_launch_check(gs, cfg, rl):  # noqa: F811
    """Review H1: the successor must be what rl.launch(held_job_id=...) accepts, or the
    handover is refused and the card sits idle."""
    slurm = Slurm([f'1441467|RUNNING|gres/gpu:a100:1|{stamp(END)}'])
    gs.tick(cfg, '1441467', now=NOW, output=slurm)
    [command] = slurm.submitted
    option = {c.split('=', 1)[0]: c.split('=', 1)[1] for c in command if '=' in c}
    # squeue '%T|%b|%j' of the successor once it runs, as Slurm prints it.
    shown = ['RUNNING', 'gres/' + option['--gres'], option['--job-name']]
    assert shown == rl.held_allocation_detail('a100')
    assert option['--partition'] == 'smp_gpu' and gs.JOB_NAME == rl.HELD_JOB_NAME


def test_one_successor_near_expiry_with_typed_record_then_nothing_more(gs, cfg):
    slurm = Slurm([f'1441467|RUNNING|gres/gpu:a100:1|{stamp(NOW + 6 * HOUR)}',
                   '1444472_7|RUNNING|(null)|Unknown'])  # CPU actors do not count
    assert gs.tick(cfg, '1441467', now=NOW, output=slurm)['reason'] == 'holder_not_near_expiry'
    slurm.rows[0] = f'1441467|RUNNING|gres/gpu:a100:1|{stamp(END)}'
    result = gs.tick(cfg, '1441467', now=NOW, output=slurm)
    assert result['action'] == 'submitted' and result['job_id'] == '91'
    [command] = slurm.submitted
    assert '--begin=' + stamp(END - 1800) in command and '--gres=gpu:a100:1' in command
    assert '--time=2880' in command and '--no-requeue' in command
    assert '--job-name=rl4-learner' in command
    record = json.loads(Path(result['record']).read_bytes())
    assert record['outcome'] == 'submitted' and record['predecessor'] == '1441467'
    assert record['authority'].startswith('user 11-07') and record['max_gpus'] == 2
    work = Path(cfg['root']) / 'successor-of-1441467'
    script = (work / 'allocation.sh').read_text()
    assert str(work / 'runtime') in script and str(work / 'budget.json') in script
    assert json.loads((work / 'budget.json').read_bytes())['successor_of'] == '1441467'
    # Every later check: no second successor for this holder, pending or running.
    assert gs.tick(cfg, '1441467', now=NOW + 60, output=slurm)['reason'] == \
        'successor_already_submitted_for_this_holder'
    slurm.rows[-1] = slurm.rows[-1].replace('PENDING', 'RUNNING')
    assert gs.tick(cfg, '1441467', now=NOW + 120, output=slurm)['action'] == 'none'
    assert len(slurm.submitted) == 1


def test_cap_and_pending_rule_refuse_with_typed_alarms(gs, cfg):
    near = f'1441467|RUNNING|gres/gpu:a100:1|{stamp(END)}'
    other = Slurm([near, '1450000|RUNNING|gres/gpu:a100:1|Unknown'])  # e.g. a GPU test
    assert gs.tick(cfg, '1441467', now=NOW, output=other)['reason'] == \
        'gpu_cap_reached_no_successor'
    # Cards, not jobs (review M1): one two-card holder is already at the cap; an unreadable
    # count and a non-whitelisted live state (review L2) count too.
    for rows in ([f'1441467|RUNNING|gres/gpu:a100:2|{stamp(END)}'],
                 [near, '1450002|RUNNING|gres/gpu|Unknown'],
                 [near, '1450003|STOPPED|gres/gpu:a30:1|Unknown']):
        slurm = Slurm(rows)
        result = gs.tick(cfg, '1441467', now=NOW, output=slurm)
        assert result['reason'] in {'gpu_cap_reached_no_successor',
                                    'gpu_job_already_pending_no_second_successor'}
        assert slurm.submitted == []
    assert gs.our_gpu_jobs(Slurm(['1|COMPLETED|gres/gpu:a100:1|Unknown'])) == []
    pending = Slurm([near, '1450001|PENDING|gres/gpu:a30:1|Unknown'])
    assert gs.tick(cfg, '1441467', now=NOW, output=pending)['reason'] == \
        'gpu_job_already_pending_no_second_successor'
    assert other.submitted == pending.submitted == []
    gone = Slurm([])  # review M2: a holder gone before any successor alarms, no submission
    assert gs.tick(cfg, '1441467', now=NOW, output=gone)['reason'] == \
        'gpu_holder_gone_without_successor'
    queued = Slurm([f'1441467|PENDING|gres/gpu:a100:1|{stamp(END)}'])
    assert gs.tick(cfg, '1441467', now=NOW, output=queued)['reason'] == 'holder_not_running_yet'
    unknown_end = Slurm(['1441467|RUNNING|gres/gpu:a100:1|Unknown'])
    assert gs.tick(cfg, '1441467', now=NOW, output=unknown_end)['reason'] == \
        'gpu_holder_end_unknown'


def test_uncertain_submission_is_never_retried(gs, cfg):
    slurm = Slurm([f'1441467|RUNNING|gres/gpu:a100:1|{stamp(END)}'], fail=True)
    result = gs.tick(cfg, '1441467', now=NOW, output=slurm)
    assert result['reason'] == 'gpu_successor_submission_outcome_unknown_no_retry'
    record = json.loads((Path(cfg['root']) / 'records/1441467.json').read_bytes())
    assert record['outcome'] == 'outcome_unknown'
    slurm.fail = False
    again = gs.tick(cfg, '1441467', now=NOW + 300, output=slurm)
    assert again['reason'] == 'gpu_successor_submission_outcome_unknown_no_retry'
    assert again['holder'] == '1441467' and slurm.submitted == []


def test_receipt_write_failure_stays_unknown_and_unreadable_records_block(gs, cfg,
                                                                         monkeypatch):
    slurm = Slurm([f'1441467|RUNNING|gres/gpu:a100:1|{stamp(END)}'])
    original = gs.save
    writes = []

    def save(path, value):
        writes.append(value.get('outcome'))
        if value.get('outcome') == 'submitted':
            raise OSError('disk full')
        original(path, value)
    monkeypatch.setattr(gs, 'save', save)
    with pytest.raises(OSError):
        gs.tick(cfg, '1441467', now=NOW, output=slurm)
    monkeypatch.setattr(gs, 'save', original)
    assert len(slurm.submitted) == 1 and writes[-1] == 'submitted'
    assert gs.tick(cfg, '1441467', now=NOW + 60, output=slurm)['reason'] == \
        'gpu_successor_submission_outcome_unknown_no_retry'
    (Path(cfg['root']) / 'records/1441467.json').write_text('{not json')
    assert gs.tick(cfg, '1441467', now=NOW + 120, output=slurm)['reason'] == \
        'gpu_successor_record_unreadable_no_submission'
    assert len(slurm.submitted) == 1


def test_lock_reread_sees_a_pending_job_that_appeared_meanwhile(gs, cfg):
    slurm = Slurm([f'1441467|RUNNING|gres/gpu:a100:1|{stamp(END)}'])
    seen = []

    def appear(fake):  # second squeue (under the lock) sees another writer's pending job
        seen.append(1)
        if len(seen) == 2:
            fake.rows.append('1450009|PENDING|gres/gpu:a100:1|Unknown')
    slurm.on_squeue = appear
    result = gs.tick(cfg, '1441467', now=NOW, output=slurm)
    assert result['reason'] == 'gpu_job_already_pending_no_second_successor'
    assert slurm.submitted == [] and not (Path(cfg['root']) / 'records').exists()


def test_successor_still_queued_after_begin_alarms_without_resubmitting(gs, cfg):
    slurm = Slurm([f'1441467|RUNNING|gres/gpu:a100:1|{stamp(END)}'])
    gs.tick(cfg, '1441467', now=NOW, output=slurm)
    begin = END - 1800
    assert gs.tick(cfg, '1441467', now=begin + 300, output=slurm)['action'] == 'none'
    late = gs.tick(cfg, '1441467', now=begin + 900, output=slurm)
    assert late['reason'] == 'gpu_successor_pending_past_begin' and late['job_id'] == '91'
    assert len(slurm.submitted) == 1


def test_template_changed_after_the_config_read_still_refuses_at_submission(gs, cfg):
    Path(cfg['template']).write_text('#!/bin/bash\nexec python gpu_session.py\n')
    slurm = Slurm([f'1441467|RUNNING|gres/gpu:a100:1|{stamp(END)}'])
    with pytest.raises(ValueError, match='gpu_successor_template_tokens_required'):
        gs.tick(cfg, '1441467', now=NOW, output=slurm)
    assert slurm.submitted == []


def test_watcher_checks_at_most_every_five_minutes_and_alarms_once(watch, tmp_path,
                                                                    monkeypatch):
    owner = segments.owner.__wrapped__(watch, tmp_path)
    owner.config.update(gpu_successor={'any': 'settings'}, held_job_id='1441467')
    results = iter([{'action': 'submitted', 'reason': 'gpu_successor_submitted',
                     'holder': '1441467', 'job_id': '91'},
                    {'action': 'none', 'reason': 'successor_already_submitted_for_this_holder'}])
    calls = []
    fake = type(sys)('gpu_successor')
    fake.settings = lambda raw, gpu: raw
    fake.tick = lambda cfg, held, now: calls.append(held) or next(results)
    monkeypatch.setitem(sys.modules, 'gpu_successor', fake)
    alarms = []
    owner.telemetry = lambda kind, **f: alarms.append((kind, f))
    owner.check_gpu_successor()
    owner.check_gpu_successor()  # within 5 min: not checked again
    assert calls == ['1441467']
    assert alarms == [('attention', {'gpu_successor': 'submitted',
                                     'reason': 'gpu_successor_submitted',
                                     'holder': '1441467', 'job_id': '91'})]
    owner.state['gpu_successor_checked_at'] -= 301
    owner.check_gpu_successor()
    assert len(calls) == 2 and len(alarms) == 1  # 'none' is not an alarm

    def broken(cfg, held, now):
        raise OSError('squeue unavailable')
    fake.tick = broken
    owner.state['gpu_successor_checked_at'] -= 301
    owner.check_gpu_successor()
    assert alarms[-1][1]['reason'] == 'gpu_successor_check_failed'


@pytest.fixture
def watch():
    return segments.original_watch_fixture.__wrapped__()


def test_read_segment_config_checks_the_successor_settings(watch, gs, tmp_path):
    """Absent key: the read goes on as before; present: settings and held_job_id now."""
    base = {'schema': watch.SCHEMA, 'mode': 'segments', 'deadline_at': time.time() + 3600,
            'training_seconds': 3600, 'actors': 1, 'budget_id': 'b'}
    path = tmp_path / 'watch.json'
    path.write_text(json.dumps(base))
    with pytest.raises(KeyError, match='runtime_source'):  # past the new block
        watch.read_segment_config(path)
    template = tmp_path / 't.sh'
    template.write_text(TEMPLATE)
    successor = {'template': str(template), 'root': str(tmp_path.resolve() / 's'),
                 'authority': 'user 11-07'}
    path.write_text(json.dumps({**base, 'gpu_successor': successor}))
    with patch.dict(sys.modules, {'gpu_successor': gs}):
        with pytest.raises(ValueError, match='gpu_successor_requires_held_job_id'):
            watch.read_segment_config(path)
        path.write_text(json.dumps({**base, 'gpu_successor': {**successor, 'max_gpus': 3},
                                    'held_job_id': '1'}))
        with pytest.raises(ValueError, match='explicit_gpu_successor_settings_required'):
            watch.read_segment_config(path)
