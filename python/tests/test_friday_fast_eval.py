"""Every-2-h Managed evaluation of fast-chain CPs: schedule, skips, no retry after intent."""
from __future__ import annotations

import importlib.util
import json
import subprocess
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
            'seeds': 100, 'pending_limit': 16, 'save_workers': 4, 'memory_gb': 112,
            'expected_steps': 1200, 'wall_seconds': 10800, 'episode_seconds': 1800,
            'interval': 7200, 'max_live': 2, 'partition': 'general',
            'prepare_partition': 'debug', 'exclude_nodes': [], 'deadline_at': None}


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
    assert '--pending-limit 16 --save-workers 4 --memory-gb 112' in script
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


def args_for(tmp_path, **overrides):
    learner, execution = tmp_path / 'learner.json', tmp_path / 'execution.json'
    learner.write_text(json.dumps({'peer': {'kind': 'local', 'directory': '/p'}, 'run': 'r'}))
    execution.write_text(json.dumps({'storage_mode': 'shared_local'}))
    values = dict(learner_config=learner, execution=execution, manifest=tmp_path / 'm.json',
                  suite=None, training_generation=None, interval=7200, workers=16, seeds=100,
                  pending_limit=16, save_workers=4, memory_gb=None, expected_steps=1200,
                  wall_seconds=None, episode_seconds=1800, max_live=2, partition='general',
                  prepare_partition='debug', exclude_node=None, deadline_at=None,
                  checkpoint=None, baseline_plan=None, no_baseline=True)
    return __import__('argparse').Namespace(**{**values, **overrides})


def test_job_size_and_wall_are_settings_from_games_and_expected_steps(fast_eval, tmp_path):
    """Lead 11-14 (a)(e): 16 live games, 4 save threads, 7 GB each; wall from games x steps."""
    settings = fast_eval.settings_from(args_for(tmp_path))
    assert (settings['pending_limit'], settings['save_workers']) == (16, 4)
    assert settings['memory_gb'] == 112 and settings['expected_steps'] == 1200
    assert settings['wall_seconds'] == 100 * 1200 * 0.9 / 16 + 1800 + 900 == 9450
    a10 = fast_eval.settings_from(args_for(tmp_path, seeds=50))
    assert a10['wall_seconds'] == 6075
    longer = fast_eval.settings_from(args_for(tmp_path, expected_steps=2400))
    assert longer['wall_seconds'] == 16200 > settings['wall_seconds']
    assert fast_eval.planned_wall_seconds(100, 5000, 16, 1800) == fast_eval.MAX_WALL_SECONDS
    given = fast_eval.settings_from(args_for(tmp_path, wall_seconds=7000, memory_gb=90,
                                             pending_limit=8))
    assert given['wall_seconds'] == 7000 and given['memory_gb'] == 90
    assert fast_eval.settings_from(args_for(tmp_path, pending_limit=8))['memory_gb'] == 56


def test_registered_A0_baseline_is_the_default_and_refused_when_changed(fast_eval, tmp_path):
    import getpass

    plan = tmp_path / getpass.getuser() / fast_eval.A0_BASELINE['plan']
    plan.parent.mkdir(parents=True)
    plan.write_text('{"plan": "a46afd12 round"}')
    with (patch.object(fast_eval, 'BASELINE_ROOT', tmp_path),
          patch.dict(fast_eval.A0_BASELINE, sha256=fast_eval.hashed(plan))):
        default = fast_eval.settings_from(args_for(tmp_path, no_baseline=False))
        assert default['baseline_plan'] == str(plan)
        given = fast_eval.settings_from(args_for(tmp_path, no_baseline=False,
                                                 baseline_plan=tmp_path / 'other.json'))
        assert given['baseline_plan'] == str((tmp_path / 'other.json').resolve())
        assert fast_eval.settings_from(args_for(tmp_path))['baseline_plan'] is None
        plan.write_text('{"plan": "changed"}')
        with pytest.raises(ValueError, match='registered_A0_baseline_plan_missing_or_changed'):
            fast_eval.settings_from(args_for(tmp_path, no_baseline=False))


def test_busy_failed_and_deadline_skip_without_stopping(fast_eval, tmp_path):
    world = World()
    world.record('cp-a', 10)
    runner = loop(fast_eval, world, tmp_path, max_live=1)
    runner.tick()
    item = runner.state['evaluations'][0]
    finish_prepare(world, item, plan=False)  # job died without a plan
    runner.tick()  # shared-disk grace: the plan may still appear (#403)
    assert item['stage'] == 'preparing'
    world.now += fast_eval.PLAN_GRACE_SECONDS
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


def test_restart_after_crash_between_sbatch_and_state(fast_eval, tmp_path):
    """Review A: `preparing` without its job id never crashes a restart."""
    world = World()
    world.record('cp-a', 10)
    world.record('cp-b', 20)
    runner = loop(fast_eval, world, tmp_path)
    runner.tick()
    state = json.loads((tmp_path / 'state.json').read_text())
    assert state['evaluations'][0]['prepare_job'] == '101'  # persisted at once
    # The crash left no job id in the state: the receipt names it.
    del state['evaluations'][0]['prepare_job']
    (tmp_path / 'state.json').write_text(json.dumps(state))
    again = loop(fast_eval, world, tmp_path)
    assert again.state['evaluations'][0]['prepare_job'] == '101'
    again.tick()
    # No receipt either: sbatch may have run, so it is never submitted again.
    (tmp_path / 'evaluation-0001/submission.json').unlink()
    (tmp_path / 'state.json').write_text(json.dumps(state))
    third = loop(fast_eval, world, tmp_path)
    assert third.state['evaluations'][0]['stage'] == 'prepare_submit_unknown'
    third.tick()
    assert len(world.commands) == 1


def test_an_intent_without_state_is_never_resubmitted(fast_eval, tmp_path):
    """Review D: a crash after the intent, before the state, leaves the directory alone."""
    world = World()
    world.record('cp-a', 10)
    root = tmp_path / 'evaluation-0001'
    root.mkdir(mode=0o700)
    (root / 'submission-intent.json').write_text('{}')
    runner = loop(fast_eval, world, tmp_path)
    runner.tick()
    assert world.commands == [] and runner.state['evaluations'][0]['stage'] == 'orphan_intent'
    world.now += 7200
    runner.tick()  # the next number, the same CP
    assert runner.state['evaluations'][1]['root'].endswith('evaluation-0002')
    assert len(world.commands) == 1


def test_liveness_uses_the_user_live_list_and_a_failing_squeue_skips(fast_eval, tmp_path):
    """Review B: purged ids are absent, never an error; squeue failure decides nothing."""
    calls = []

    class Scheduler:
        @staticmethod
        def username():
            return 'someone'

        @staticmethod
        def output(command):
            calls.append(command)
            return '101\n555_3\n'

    with patch.dict(sys.modules, {'submit': Scheduler}):
        assert fast_eval.live_jobs(['101', '555', '999']) == {'101', '555'}
        assert fast_eval.live_jobs([]) == set() and len(calls) == 1
    assert calls[0][:4] == ['squeue', '-h', '-u', 'someone'] and '-j' not in calls[0]
    world = World()
    world.record('cp-a', 10)
    runner = loop(fast_eval, world, tmp_path)
    runner.tick()
    item = runner.state['evaluations'][0]
    finish_prepare(world, item)

    def down(ids):
        raise subprocess.CalledProcessError(1, ['squeue'])

    runner.jobs = down
    runner.tick()
    assert item['stage'] == 'preparing' and runner.state['log'][-1]['kind'] == (
        'scheduler_unreachable')
    runner.jobs = lambda ids: {i for i in ids if i in world.live}
    runner.tick()
    assert item['stage'] == 'submitted'


def test_an_unknown_evaluation_submit_is_recorded_and_the_loop_lives(fast_eval, tmp_path):
    """Review C: OSError / CalledProcessError from the owner's submit."""
    world = World()
    world.record('cp-a', 10)
    runner = loop(fast_eval, world, tmp_path)
    runner.tick()
    item = runner.state['evaluations'][0]
    finish_prepare(world, item)

    def broken(plan, **options):
        raise subprocess.CalledProcessError(1, ['sbatch'])

    runner.submit = broken
    runner.tick()
    assert item['stage'] == 'submit_unknown'
    runner.submit = world.submit
    runner.tick()
    assert world.submits == []  # never retried
    world.record('cp-b', 20)
    world.now += 7200
    runner.tick()
    assert runner.state['evaluations'][-1]['checkpoint'] == 'cp-b'


def test_cp_under_preparation_is_published_as_protected(fast_eval, tmp_path):
    world = World()
    world.record('cp-a', 10)
    runner = loop(fast_eval, world, tmp_path)
    runner.tick()
    protected = tmp_path / 'protected-checkpoints.json'
    assert json.loads(protected.read_text())['checkpoints'] == ['cp-a']
    finish_prepare(world, runner.state['evaluations'][0])
    runner.tick()
    assert json.loads(protected.read_text())['checkpoints'] == []


def test_one_loop_per_output(tmp_path):
    from spireagent.instance_lock import instance_lock
    from spireagent.json_boundary import BoundaryError

    code = ('import sys,time; sys.path[:0]=sys.argv[2:]; '
            'from spireagent.instance_lock import instance_lock; from pathlib import Path\n'
            'with instance_lock(Path(sys.argv[1])):\n print("held", flush=True); time.sleep(30)')
    lock = tmp_path / 'loop.lock'
    other = subprocess.Popen([sys.executable, '-c', code, str(lock), *sys.path],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert other.stdout.readline().strip() == 'held'
        with pytest.raises(BoundaryError, match='already_running'), instance_lock(lock):
            pass
    finally:
        other.kill()
        other.wait()
    with instance_lock(lock):  # released on process death
        pass


def test_one_given_cp_without_R2_is_prepared_once(fast_eval, tmp_path):
    """--checkpoint: exactly that CP, once; shared_local preparation reads no R2 environment."""
    world = World()
    runner = fast_eval.Loop({**SETTINGS, 'checkpoint': 'cp-given', 'r2': False}, tmp_path,
                            dry_run=False, clock=lambda: world.now, run_command=world.run,
                            jobs=lambda ids: {i for i in ids if i in world.live},
                            submit=world.submit)
    runner.tick()
    [item] = runner.state['evaluations']
    script = (tmp_path / 'evaluation-0001/prepare.sh').read_text()
    assert item['checkpoint'] == 'cp-given' and '--checkpoint cp-given' in script
    assert 'friday-r2.env' not in script and '--workers 4' in script
    finish_prepare(world, item)
    runner.tick()
    world.now += 7200
    runner.tick()
    assert runner.state['log'][-1]['reason'] == 'no_new_checkpoint'
    assert fast_eval.check_storage({'storage_mode': 'shared_local'},
                                   {'kind': 'local', 'directory': '/p'}) is False
    assert fast_eval.check_storage({}, {'kind': 's3'}) is True
    with pytest.raises(ValueError, match='local_peer_without_archive'):
        fast_eval.check_storage({'storage_mode': 'shared_local'},
                                {'kind': 'local', 'directory': '/p', 'archive': {'kind': 's3'}})


def test_finished_evaluation_is_reported_once_and_a_lost_one_recorded(fast_eval, tmp_path):
    world = World()
    world.record('cp-a', 10)
    runner = loop(fast_eval, world, tmp_path, baseline_plan='/base/plan.json')
    calls = []

    def report(root, baseline, previous):
        calls.append((root, baseline, previous))
        return {'report_directory': str(root / 'report'), 'cohorts': {'current': {
            'scored': 99, 'wins': 1, 'mean_floor': 37.5, 'reward_mean': 0.48,
            'conservative': 0.48}}}

    runner_report = patch.object(fast_eval, 'write_evaluation_report', report)
    with runner_report:
        runner.tick()
        first = runner.state['evaluations'][0]
        finish_prepare(world, first)
        runner.tick()
        assert first['stage'] == 'submitted'
        world.live.discard(first['evaluation_job'])
        runner.tick()  # ended, result not visible yet: grace
        assert first['stage'] == 'submitted' and not calls
        result = Path(first['root']) / 'round/evaluation/result.json'
        result.write_text('{}')
        runner.tick()
        assert first['stage'] == 'reported' and calls == [
            (Path(first['root']), '/base/plan.json', None)]
        assert runner.state['log'][-1]['kind'] == 'evaluation_reported'
        world.record('cp-b', 20)
        world.now += 7200
        runner.tick()
        second = runner.state['evaluations'][1]
        finish_prepare(world, second)
        runner.tick()
        world.live.discard(second['evaluation_job'])
        runner.tick()
        world.now += fast_eval.RESULT_GRACE_SECONDS
        runner.tick()
        assert second['stage'] == 'evaluation_ended_without_result' and len(calls) == 1


def cohort(tmp_path, name, rows, protocol='rc.64'):
    from stpd.evaluation.rl_observation import load_cohort

    slots = [{'step': 'checkpoint', 'seed': seed, 'run_id': f'{name}-{seed}',
              'character': 'Defect', 'ascension': 0, 'status': status,
              'max_observed_floor': floor, 'natural_result': result}
             for seed, status, floor, result in rows]
    path = tmp_path / f'{name}.json'
    path.write_text(json.dumps({'ledger_report': {'slots': slots, 'planned': len(slots),
                                                  'host_kind': 'managed', 'score_admitted': True}}))
    value = load_cohort(path)
    value['protocol_observed'] = {'host_pin': protocol}
    return value


def test_report_pairs_matching_references_and_names_skipped_ones(fast_eval, tmp_path):
    base = cohort(tmp_path, 'base', [('s1', 'finished', 17, 'loss'),
                                     ('s2', 'finished', 33, 'loss'),
                                     ('s3', 'finished', 20, 'loss')])
    current = cohort(tmp_path, 'cur', [('s1', 'finished', 33, 'loss'),
                                       ('s2', 'finished', 48, 'win'),
                                       ('s3', 'unknown', 44, None)])
    other = cohort(tmp_path, 'old', [('s1', 'finished', 9, 'loss')], protocol='rc.63')
    plans = {str(tmp_path / 'eval/round/evaluation/plan.json'): current,
             '/base/plan.json': base, '/old/plan.json': other}

    def read(result, plan):
        value = plans[str(plan)]
        return {'manifest': {'sha256': 'm'}}, {}, {'cohort': value}

    with patch.object(fast_eval.evaluate, 'read_observation_report', read):
        summary = fast_eval.write_evaluation_report(tmp_path / 'eval', '/base/plan.json',
                                                    '/old/plan.json')
    assert summary['skipped_references'] == {
        'previous': {'plan': '/old/plan.json', 'differing': ['host_pin']}}
    now = summary['cohorts']['current']
    assert now['planned'] == 3 and now['scored'] == 2 and now['wins'] == 1
    assert [row['seed'] for row in now['excluded']] == ['s3']  # unknown: excluded, not void
    paired = summary['paired']['baseline']
    assert paired['floor_pairs'] == 2 and paired['counts']['farther'] == 2
    readme = (tmp_path / 'eval/report/README.md').read_text()
    assert 'Act1 Boss cleared' in readme and 'Farther / same / nearer' in readme
    assert json.loads((tmp_path / 'eval/report/summary.json').read_text()) == json.loads(
        json.dumps(summary))
