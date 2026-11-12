"""Every-2-h Managed evaluation of fast-chain CPs: schedule, skips, no retry after intent."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip('fcntl', reason='Friday tools target POSIX Slurm')
FRIDAY = Path(__file__).resolve().parents[2] / 'tools/friday'


def module_file(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fast_eval():
    batch = module_file('friday_fe_batch', FRIDAY / 'batch.py')
    with patch.dict(sys.modules, {'batch': batch}):
        scheduler = module_file('friday_fe_scheduler', FRIDAY / 'submit.py')
        with patch.dict(sys.modules, {'submit': scheduler}):
            evaluate = module_file('friday_fe_evaluate', FRIDAY / 'evaluate.py')
            with patch.dict(sys.modules, {'evaluate': evaluate}):
                return module_file('friday_fast_eval', FRIDAY / 'fast_eval.py')


SETTINGS = {'learner_config': '/w/learner.json', 'peer': {'kind': 'local', 'directory': '/p'},
            'run': 'run-1', 'training_generation': None, 'execution': '/w/exec.json',
            'manifest': '/w/manifest.json', 'runtime_source': '/src/repo', 'workers': 4,
            'seeds': 100, 'wall_seconds': 10800, 'episode_seconds': 1800, 'interval': 7200,
            'max_live': 2, 'partition': 'general', 'prepare_partition': 'debug',
            'exclude_nodes': [], 'deadline_at': None}


class World:
    def __init__(self):
        self.now = 1000.0
        self.records = []
        self.live = set()
        self.commands = []
        self.submits = []
        self.submit_error = None

    def record(self, cp, at):
        self.records.append({'checkpoint_artifact_id': cp, 'published_at': at,
                             'checkpoint_sequence': len(self.records) + 1})

    def newest(self):
        return max(self.records, key=lambda r: r['published_at']) if self.records else None

    def run(self, command):
        self.commands.append(command)
        job = str(100 + len(self.commands))
        self.live.add(job)
        return job + ';cluster'

    def submit(self, plan, **options):
        if self.submit_error:
            raise ValueError(self.submit_error)
        self.submits.append((plan, options))
        job = str(900 + len(self.submits))
        self.live.add(job)
        return {'job_id': job}


def loop(fast_eval, world, tmp_path, **settings):
    return fast_eval.Loop({**SETTINGS, **settings}, tmp_path, dry_run=False,
                          clock=lambda: world.now, newest=world.newest, run_command=world.run,
                          jobs=lambda ids: {i for i in ids if i in world.live},
                          submit=world.submit)


def finish_prepare(world, item, plan=True):
    world.live.discard(item['prepare_job'])
    if plan:
        path = Path(item['root']) / 'round/evaluation/plan.json'
        path.parent.mkdir(parents=True)
        path.write_text('{}')


def test_newest_cp_is_prepared_then_its_plan_submitted_every_interval(fast_eval, tmp_path):
    world = World()
    world.record('cp-a', 10)
    world.record('cp-b', 20)
    runner = loop(fast_eval, world, tmp_path)
    runner.tick()
    [item] = runner.state['evaluations']
    assert item['checkpoint'] == 'cp-b' and item['stage'] == 'preparing'
    assert item['prepare_job'] == '101'
    script = (tmp_path / 'evaluation-0001/prepare.sh').read_text()
    assert 'prepare-checkpoint' in script and '--checkpoint cp-b' in script
    assert '--seeds 100' in script and '--wall-seconds 10800' in script
    assert '--episode-seconds 1800' in script and '--deadline-at' not in script
    assert (tmp_path / 'evaluation-0001/submission-intent.json').is_file()
    runner.tick()  # still preparing: nothing new
    assert len(world.commands) == 1 and not world.submits
    finish_prepare(world, item)
    runner.tick()
    assert item['stage'] == 'submitted' and item['evaluation_job'] == '901'
    assert world.submits[0][1]['partition'] == 'general'
    world.now += 7200  # due again, no new CP: skipped, never the same CP twice
    runner.tick()
    assert len(world.commands) == 1
    assert runner.state['log'][-1]['reason'] == 'no_new_checkpoint'
    world.record('cp-c', 30)
    world.now += 7200
    runner.tick()
    assert runner.state['evaluations'][-1]['checkpoint'] == 'cp-c'
    saved = json.loads((tmp_path / 'state.json').read_text())
    assert [e['checkpoint'] for e in saved['evaluations']] == ['cp-b', 'cp-c']
    # A restart continues the same schedule; other settings need a new output.
    assert loop(fast_eval, world, tmp_path).state == saved
    with pytest.raises(ValueError, match='settings_changed'):
        loop(fast_eval, world, tmp_path, seeds=50)


def test_busy_failed_and_deadline_skip_without_stopping(fast_eval, tmp_path):
    world = World()
    world.record('cp-a', 10)
    runner = loop(fast_eval, world, tmp_path, max_live=1)
    runner.tick()
    item = runner.state['evaluations'][0]
    finish_prepare(world, item, plan=False)  # job died without a plan
    runner.tick()
    assert item['stage'] == 'prepare_failed'
    world.record('cp-b', 20)
    world.now += 7200
    runner.tick()
    second = runner.state['evaluations'][1]
    finish_prepare(world, second)
    world.submit_error = 'requested_evaluation_workers_exceed_current_fair_share'
    runner.tick()  # refused before any delivery: tried again next tick
    assert second['stage'] == 'preparing'
    world.submit_error = None
    runner.tick()
    assert second['stage'] == 'submitted'
    world.record('cp-c', 30)
    world.now += 7200
    runner.tick()  # the one allowed evaluation still runs
    assert runner.state['log'][-1]['reason'] == 'evaluations_still_running'
    late = loop(fast_eval, world, tmp_path / 'late', deadline_at=world.now + 3600)
    late.tick()
    assert late.state['log'][-1]['reason'] == 'absolute_deadline_headroom'


def test_an_unknown_submission_is_never_repeated(fast_eval, tmp_path):
    world = World()
    world.record('cp-a', 10)

    def broken(command):
        raise OSError('sbatch connection lost')

    runner = loop(fast_eval, world, tmp_path)
    runner.run_command = broken
    runner.tick()
    item = runner.state['evaluations'][0]
    assert item['stage'] == 'prepare_submit_unknown'
    world.now += 7200
    runner.run_command = world.run
    runner.tick()
    assert world.commands == []  # same CP: not offered again
    world.record('cp-b', 20)
    world.submit_error = 'existing_evaluation_plan_must_drain_before_new_submission'
    world.now += 7200
    runner.tick()
    item = runner.state['evaluations'][1]
    finish_prepare(world, item)
    runner.tick()
    assert item['stage'] == 'submit_failed'


def test_dry_run_writes_the_script_and_submits_nothing(fast_eval, tmp_path):
    world = World()
    world.record('cp-a', 10)
    runner = fast_eval.Loop(SETTINGS, tmp_path, dry_run=True, clock=lambda: world.now,
                            newest=world.newest, run_command=world.run,
                            jobs=lambda ids: set(), submit=world.submit)
    runner.tick()
    assert world.commands == [] and not (tmp_path / 'state.json').exists()
    item = runner.state['evaluations'][0]
    assert item['stage'] == 'dry_run' and item['command'][0] == 'sbatch'
    assert (tmp_path / 'evaluation-0001/prepare.sh').is_file()
