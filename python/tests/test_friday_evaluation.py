"""Real evaluation ledger/seed/audit planning, with gameplay and scheduler IO replaced."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip('fcntl', reason='Friday evaluation targets POSIX Slurm')
FRIDAY = Path(__file__).resolve().parents[2] / 'tools/friday'


def module_file(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def evaluate():
    batch = module_file('friday_eval_batch', FRIDAY / 'batch.py')
    with patch.dict(sys.modules, {'batch': batch}):
        scheduler = module_file('friday_eval_scheduler', FRIDAY / 'submit.py')
        with patch.dict(sys.modules, {'submit': scheduler}):
            return module_file('friday_evaluate', FRIDAY / 'evaluate.py')


@pytest.fixture
def execution(tmp_path):
    # Only plan/queue semantics are exercised: these model entries do not claim typed evidence.
    value = {'schema': 'spireagent/managed-evaluation-execution-v1', 'store': 's3',
             'retention': {'tier': 'trajectory', 'purpose': 'evaluation', 'audit_every': 50},
             'pipeline': {'save_workers': 2, 'pending_limit': 8},
             'models': {f'model-{index}': {'artifact_id': str(index) * 64}
                        for index in range(1, 4)},
             'peer': {'kind': 's3', 'bucket': 'test', 'prefix': 'test',
                      'endpoint': 'https://example.invalid', 'region': 'auto'}}
    file = tmp_path / 'execution.json'
    file.write_text(json.dumps(value), encoding='utf-8')
    return file


def prepared(evaluate, execution, tmp_path, **keywords):
    selections = {f'checkpoint-{index}': f'model-{index}' for index in range(1, 4)}
    directory = (tmp_path / 'evaluation').resolve()
    plan = evaluate.prepare(execution, directory, selections=selections, **keywords)
    return plan, directory


def test_real_canonical20_by_three_models_freezes60_and_two_audits(evaluate, execution, tmp_path):
    from spireagent.workbench.reference_suite import load_manifest
    from stpd.evaluation.suites import load_suite

    plan, directory = prepared(evaluate, execution, tmp_path)
    ledger = evaluate.create_ledger(plan, (tmp_path / 'ledger').resolve())
    _, manifest = load_manifest(Path(plan['manifest']['path']), plan['manifest']['sha256'])
    assert manifest['seeds'] == load_suite('ref-20')['seeds']
    assert manifest['host'] == 'Managed'
    assert plan['planned'] == len(ledger.slots()) == 60
    assert len({slot['seed'] for slot in ledger.slots()}) == 20
    assert len({slot['selection_id'] for slot in ledger.slots()}) == 3
    assert ledger.plan['audit_selection']['count'] == 2
    assert sum(slot['formal_audit_selected'] for slot in ledger.slots()) == 2
    assert all(slot['status'] == 'not_started' for slot in ledger.slots())
    assert ledger.report()['score_admitted'] is False
    assert (directory / 'plan.json').is_file()


def test_bounded_selection_keeps_full_ledger_denominator_and_owner_audits(
        evaluate, execution, tmp_path):
    plan, _ = prepared(evaluate, execution, tmp_path, limit=2)
    ledger = evaluate.create_ledger(plan, (tmp_path / 'ledger').resolve())
    selected = ledger.plan['slots'][:plan['selected_limit']]
    assert plan['planned'] == ledger.report()['planned'] == 60
    assert len(selected) == 2 and all(row['formal_audit_selected'] for row in selected)
    assert ledger.report()['not_started'] == 60


def test_d1_prefix_preserves_full_t2_manifest_and_passes_owner_selection(
        evaluate, execution, tmp_path, monkeypatch):
    from spireagent.workbench.evaluation_ledger import EvaluationLedger
    from stpd.evaluation.suites import t2_managed_manifest

    manifest = tmp_path / 't2-original.json'
    manifest.write_text(json.dumps(t2_managed_manifest('v0.111.0')))
    original = manifest.read_bytes()
    plan, _ = prepared(evaluate, execution, tmp_path,
                       manifest_path=manifest, seed_prefix=20)
    assert plan['planned'] == 60
    assert Path(plan['manifest']['path']).read_bytes() == original
    received = []

    def owner_create(*args, **kwargs):
        received.append(kwargs)
        return 'owner-ledger'

    monkeypatch.setattr(EvaluationLedger, 'create', owner_create)
    assert evaluate.create_ledger(plan, (tmp_path / 'ledger').resolve()) == 'owner-ledger'
    assert received[0]['seed_prefix'] == 20


@pytest.mark.parametrize('prefix', [0, 41, True])
def test_invalid_canonical_prefix_is_rejected(evaluate, execution, tmp_path, prefix):
    with pytest.raises(ValueError, match='bounded_canonical_seed_prefix'):
        prepared(evaluate, execution, tmp_path, seed_prefix=prefix)


def test_seed_subset_preserves_full_registered_manifest_and_original_ordinals(
        evaluate, execution, tmp_path):
    from stpd.evaluation.suites import rl3_learning_curve_manifest

    manifest = tmp_path / 'original100.json'
    value = rl3_learning_curve_manifest('v0.111.0')
    manifest.write_text(json.dumps(value))
    original = manifest.read_bytes()
    plan, _ = prepared(evaluate, execution, tmp_path, manifest_path=manifest,
                       seed_ordinals=[21, 56, 100])
    assert plan['planned'] == 9 and plan['seed_ordinals'] == [21, 56, 100]
    assert Path(plan['manifest']['path']).read_bytes() == original
    ledger = evaluate.create_ledger(plan, (tmp_path / 'ledger').resolve())
    assert {slot['seed_ordinal'] for slot in ledger.plan['slots']} == {21, 56, 100}
    assert all(slot['seed'] == value['seeds'][slot['seed_ordinal'] - 1]
               for slot in ledger.plan['slots'])
    assert ledger.plan['originals'][0]['sha256'] == plan['manifest']['sha256']
    assert ledger.plan['audit_selection']['count'] == 1


@pytest.mark.parametrize('ordinals', [[], [0], [21], [True], [2, 1], [1, 1], ['1']])
def test_seed_subset_rejects_invalid_or_reordered_original_ordinals(
        evaluate, execution, tmp_path, ordinals):
    with pytest.raises(ValueError, match='bounded_canonical_seed_ordinals'):
        prepared(evaluate, execution, tmp_path, seed_ordinals=ordinals)


def test_seed_prefix_and_subset_cannot_define_two_denominators(evaluate, execution, tmp_path):
    with pytest.raises(ValueError, match='bounded_canonical_seed_ordinals'):
        prepared(evaluate, execution, tmp_path, seed_prefix=10, seed_ordinals=[1])


@pytest.mark.parametrize('changed', [
    {'store': '/tmp/local'},
    {'retention': {'tier': 'trajectory', 'purpose': 'training', 'audit_every': 50}},
    {'retention': {'tier': 'trajectory', 'purpose': 'evaluation', 'audit_every': 100}},
])
def test_wrong_preservation_or_peer_is_rejected_before_ledger(
        evaluate, execution, tmp_path, changed):
    value = json.loads(execution.read_bytes())
    value.update(changed)
    execution.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='fixed_remote_evaluation'):
        prepared(evaluate, execution, tmp_path)


def test_shared_local_mode_is_named_and_exact(evaluate, execution, tmp_path):
    """Lead 11-12: fast-chain eval without R2 = a named mode, never a fallback."""
    shared = str((tmp_path / 'shared-peer').resolve())
    value = json.loads(execution.read_bytes())
    value.update(peer={'kind': 'local', 'directory': shared}, store=shared)
    selections = {'model-1': 'model-1', 'model-2': 'model-2', 'model-3': 'model-3'}
    with pytest.raises(ValueError, match='fixed_remote_evaluation'):  # not named: refused
        evaluate.validate_execution(value, selections)
    value['storage_mode'] = 'shared_local'
    evaluate.validate_execution(value, selections)
    for changed in ({'store': str(tmp_path / 'elsewhere')},
                    {'peer': {'kind': 'local', 'directory': 'relative'}, 'store': 'relative'},
                    {'peer': {'kind': 'local', 'directory': shared,
                              'archive': {'kind': 'local', 'directory': '/a'}}},
                    {'peer': json.loads(execution.read_bytes())['peer'], 'store': 's3'},
                    {'storage_mode': 'local'}):
        with pytest.raises(ValueError, match='fixed_remote_evaluation'):
            evaluate.validate_execution({**value, **changed}, selections)
    # The remote mode is unchanged, spelled or not.
    original = json.loads(execution.read_bytes())
    evaluate.validate_execution({**original, 'storage_mode': 'remote'}, selections)


def test_shared_local_launcher_reads_no_R2_environment(evaluate, tmp_path):
    """A7: the fast chain never opens R2; every other mode keeps the R2 environment."""
    plan = {'source_root': str(tmp_path / 'actor/source')}
    remote = evaluate.launcher(tmp_path / 'plan.json', plan)
    assert remote == evaluate.launcher(tmp_path / 'plan.json', plan, credentials=True)
    assert 'friday-r2.env' in remote and 'source "$credentials"' in remote
    local = evaluate.launcher(tmp_path / 'plan.json', plan, credentials=False)
    assert 'friday-r2.env' not in local and '$credentials' not in local
    assert local.replace('# shared_local storage: no R2 environment is read\n', '') == \
        remote.replace(evaluate.R2_ENVIRONMENT, '')
    assert evaluate.needs_r2({}) and evaluate.needs_r2({'storage_mode': 'remote'})
    assert not evaluate.needs_r2({'storage_mode': 'shared_local'})


def test_fixed20_seed_tampering_stays_rejected_by_original_reader(evaluate, execution, tmp_path):
    from spireagent.json_boundary import BoundaryError
    from stpd.evaluation.suites import load_suite

    manifest = load_suite('ref-20')
    manifest['host'] = 'Managed'
    manifest['seeds'][0] = manifest['seeds'][1]
    file = tmp_path / 'tampered.json'
    file.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(BoundaryError, match='manifest_configuration_changed'):
        prepared(evaluate, execution, tmp_path, manifest_path=file)


def test_changed_frozen_execution_is_rejected(evaluate, execution, tmp_path):
    plan, _ = prepared(evaluate, execution, tmp_path)
    Path(plan['execution']['path']).write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='pinned_input_changed'):
        evaluate.create_ledger(plan, (tmp_path / 'ledger').resolve())


def test_real_ledger_unknown_stops_new_admission_and_never_reoffers(
        evaluate, execution, tmp_path, monkeypatch):
    from spireagent.workbench import evaluation_ledger as owner
    from spireagent.workbench.reference_suite import SuiteLimits

    plan, _ = prepared(evaluate, execution, tmp_path)
    ledger = evaluate.create_ledger(plan, (tmp_path / 'ledger').resolve())
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(owner.shutil, 'disk_usage', lambda _: usage._replace(free=100 * 1024**3))
    offered = []

    def unknown(row, manifest, artifacts, checkpoint):
        # Replace only Host execution: no fake typed success or R2 readback is manufactured.
        assert next(slot for slot in ledger.slots()
                    if slot['slot_id'] == row['ledger_slot_id'])['status'] == 'reserved'
        offered.append(row['ledger_slot_id'])
        row.update(status='unknown', reason='injected_delivery_uncertainty',
                   execution_status='unknown', cleanup_accepted=True, world_mutation_offered=True)
        checkpoint()

    executor = evaluate.StopOnUncertain(unknown, ledger.directory / 'STOP')
    result = ledger.run_managed(workers=1, max_episodes=3, executor=executor,
                                limits=SuiteLimits(10, 10, 10, 60))
    assert result['status'] == 'stopped'
    assert len(offered) == ledger.report()['unknown'] == 1
    assert ledger.report()['not_started'] == 59
    ledger.run_managed(workers=1, max_episodes=3, executor=executor,
                       limits=SuiteLimits(10, 10, 10, 60))
    assert len(offered) == 1
    assert ledger.report()['score_admitted'] is False


def test_explicit_clean_unknown_collection_keeps_full_ledger_and_never_reoffers(
        evaluate, execution, tmp_path, monkeypatch):
    from spireagent.workbench import evaluation_ledger as owner
    from spireagent.workbench.reference_suite import SuiteLimits

    plan, directory = prepared(evaluate, execution, tmp_path, unknown_policy='continue_cleaned')
    assert evaluate.load_plan(directory / 'plan.json')['unknown_policy'] == 'continue_cleaned'
    ledger = evaluate.create_ledger(plan, (tmp_path / 'ledger').resolve())
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(owner.shutil, 'disk_usage', lambda _: usage._replace(free=100 * 1024**3))
    offered = []

    def unknown(row, manifest, artifacts, checkpoint):
        offered.append(row['ledger_slot_id'])
        row.update(status='unknown', execution_status='unknown',
                   reason='runtime_execution_or_release_unknown', cleanup_accepted=True,
                   world_mutation_offered=True)
        checkpoint()

    stop = ledger.directory / 'STOP'
    executor = evaluate.StopOnUncertain(unknown, stop, unknown_policy='continue_cleaned')
    for _ in range(2):
        ledger.run_managed(workers=1, max_episodes=3, executor=executor,
                           limits=SuiteLimits(10, 10, 10, 60))
    assert len(offered) == len(set(offered)) == 6
    assert not stop.exists()
    assert ledger.report()['unknown'] == 6
    assert ledger.report()['not_started'] == 54
    assert ledger.report()['score_admitted'] is False


@pytest.mark.parametrize('row', [
    {'status': 'unknown', 'cleanup_accepted': False, 'world_mutation_offered': True},
    {'status': 'unknown', 'cleanup_accepted': False,
     'execution_reason': 'typed_original_verification_failed'},
])
def test_clean_unknown_option_preserves_cleanup_and_integrity_stops(evaluate, tmp_path, row):
    stop = tmp_path / 'STOP'
    evaluate.StopOnUncertain(None, stop, unknown_policy='continue_cleaned').inspect(row)
    assert stop.exists()


@pytest.mark.parametrize('policy', ['stop', 'continue_cleaned'])
def test_closed_save_failure_is_an_excluded_pair_not_a_stopped_batch(evaluate, tmp_path, policy):
    stop = tmp_path / 'STOP'
    row = {'status': 'save_failed', 'cleanup_accepted': True,
           'execution_status': 'finished', 'reason': 'preservation_failed'}
    evaluate.StopOnUncertain(None, stop, unknown_policy=policy).inspect(row)
    assert not stop.exists()
    # The strict policy still stops when the game itself was uncertain.
    uncertain = {**row, 'execution_status': 'unknown'}
    evaluate.StopOnUncertain(None, stop, unknown_policy=policy).inspect(uncertain)
    assert stop.exists() is (policy == 'stop')


def test_cleaned_typed_verification_failure_is_one_excluded_pair(evaluate, tmp_path):
    row = {'status': 'unknown', 'cleanup_accepted': True,
           'execution_reason': 'typed_original_verification_failed'}
    stop = tmp_path / 'STOP'
    evaluate.StopOnUncertain(None, stop, unknown_policy='continue_cleaned').inspect(row)
    assert not stop.exists()
    evaluate.StopOnUncertain(None, stop).inspect(row)
    assert stop.exists()


@pytest.mark.parametrize('policy', [None, 'retry', True])
def test_unknown_collection_policy_requires_explicit_supported_value(
        evaluate, execution, tmp_path, policy):
    with pytest.raises(ValueError, match='explicit_evaluation_unknown_policy'):
        prepared(evaluate, execution, tmp_path, unknown_policy=policy)


def test_evaluation_shares_aggregate_gate_and_uses_one_four_core_allocation(
        evaluate, execution, tmp_path):
    _plan, directory = prepared(evaluate, execution, tmp_path)
    lock = tmp_path / 'configuration/friday-submit.lock'
    with (patch.object(evaluate.scheduler, 'submission_lock_path', return_value=lock.resolve()),
          patch.object(evaluate.scheduler, 'output', side_effect=['', '0/100/0/100', ''])):
        result = evaluate.submit(directory / 'plan.json', dry_run=True)
    assert any(arg.startswith('--job-name=spireagent-eval-') for arg in result['command'])
    assert '--cpus-per-task=4' in result['command']
    assert '--nodes=1' in result['command'] and '--ntasks=1' in result['command']
    assert '--no-requeue' in result['command']
    assert not any(item.startswith('--array') for item in result['command'])
    subprocess.run(['bash', '-n', str(directory / 'launch.sh')], check=True)
    with (patch.object(evaluate.scheduler, 'submission_lock_path', return_value=lock.resolve()),
          patch.object(evaluate.scheduler, 'output', return_value='1394000_[0-31]') as output,
          pytest.raises(ValueError, match='must_drain')):
        evaluate.submit(directory / 'plan.json', dry_run=False)
    assert output.call_count == 1


def test_run_has_no_automatic_mac_fallback(evaluate, execution, tmp_path, monkeypatch):
    _plan, directory = prepared(evaluate, execution, tmp_path)
    monkeypatch.setattr(evaluate.sys, 'platform', 'darwin')
    with pytest.raises(ValueError, match='no_mac_fallback'):
        evaluate.run(directory / 'plan.json')
    assert not (directory / 'owner.json').exists()


def test_allocation_run_uses_original_ledger_and_retains_unknown_without_reoffer(
        evaluate, execution, tmp_path, monkeypatch):
    from spireagent.workbench import evaluation_ledger as owner
    from spireagent.workbench import managed_pipeline

    _plan, directory = prepared(evaluate, execution, tmp_path, workers=1, limit=3)
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(owner.shutil, 'disk_usage', lambda _: usage._replace(free=100 * 1024**3))
    offers = []

    class HostReplaced:
        preparation = {'test_boundary': 'Host and remote IO replaced, no gameplay proof'}

        def __init__(self, configuration, **_keywords):
            assert configuration['store'] == 's3'
            assert configuration['retention'] == evaluate.RETENTION

        def __call__(self, row, manifest, artifacts, checkpoint):
            # Durable admission exists before even the first fake native offer.
            admission = json.loads((directory / 'admission.json').read_bytes())
            assert len(admission['original_ledger_plan']['slots']) == 60
            assert len(admission['selected_slot_ids']) == 3
            offers.append(row['ledger_slot_id'])
            row.update(status='unknown', reason='injected_delivery_uncertainty',
                       execution_status='unknown', cleanup_accepted=True,
                       world_mutation_offered=True)
            checkpoint()

        def close(self):
            pass

    monkeypatch.setattr(managed_pipeline, 'PipelinedManagedExecutor', HostReplaced)
    monkeypatch.setattr(evaluate.sys, 'platform', 'linux')
    monkeypatch.setenv('SLURM_JOB_ID', 'test-owned-allocation')
    monkeypatch.setenv('SLURM_CPUS_PER_TASK', '1')
    # A late signal must stay handled during the complete snapshot/readback.
    import signal

    original_snapshot = evaluate.keep_evidence
    initial_handler = signal.getsignal(signal.SIGUSR1)

    def signal_during_snapshot(scratch, root, summary, execution):
        assert signal.getsignal(signal.SIGUSR1) != initial_handler
        controller_stop.unlink(missing_ok=True)
        signal.raise_signal(signal.SIGUSR1)
        assert controller_stop.exists()
        return original_snapshot(scratch, root, summary, execution)

    monkeypatch.setattr(evaluate, 'keep_evidence', signal_during_snapshot)
    controller_stop = tmp_path / 'controller-STOP'
    result = evaluate.run(directory / 'plan.json', on_stop=controller_stop.touch)
    assert controller_stop.exists()
    assert signal.getsignal(signal.SIGUSR1) == initial_handler
    assert result['timing']['process_wall_seconds'] >= result['timing']['phases']['snapshot']
    assert result['timing']['process_finished_at'] >= result['timing']['process_started_at']
    assert result['status'] == 'stopped'
    assert result['ledger_report']['unknown'] == 1
    assert result['ledger_report']['not_started'] == 59
    assert result['ledger_report']['score_admitted'] is False
    assert len(offers) == 1
    receipt = json.loads((directory / 'snapshot.json').read_bytes())
    assert receipt['full_readback'] is True and receipt['peer_held'] == []
    assert evaluate.originals_retained(receipt)
    with tarfile.open(directory / 'evidence/evidence.tar') as archive:
        names = archive.getnames()
    assert 'result.json' in names and 'ledger/plan.json' in names  # the unknown slot's original
    with pytest.raises(FileExistsError):
        evaluate.run(directory / 'plan.json')
    assert len(offers) == 1


def test_end_signal_during_play_keeps_evidence_at_once_and_the_close_replaces_it(
        evaluate, execution, tmp_path, monkeypatch):
    """S1: USR1 (wall end; Slurm kills 300 s later) while a game still runs: the ledger and
    scratch are kept at once (interrupted), the in-job report waits for project-report,
    and the normal close, if it gets there, replaces the interim copy."""
    import signal
    import time

    from spireagent.workbench import evaluation_ledger as owner
    from spireagent.workbench import managed_pipeline

    _plan, directory = prepared(evaluate, execution, tmp_path, workers=1, limit=1)
    planned = evaluate.load_plan  # the report needs one model; here only its skip matters
    monkeypatch.setattr(evaluate, 'load_plan',
                        lambda path: {**planned(path), 'observation_report': True})
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(owner.shutil, 'disk_usage', lambda _: usage._replace(free=100 * 1024**3))
    seen = {}

    class HostReplaced:
        preparation = {'test_boundary': 'Host and remote IO replaced, no gameplay proof'}

        def __init__(self, configuration, **_keywords):
            pass

        def __call__(self, row, manifest, artifacts, checkpoint):
            os.kill(os.getpid(), signal.SIGUSR1)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not (directory / 'result.json').exists():
                time.sleep(0.05)
            seen['result'] = json.loads((directory / 'result.json').read_bytes())
            seen['snapshot'] = json.loads((directory / 'snapshot.json').read_bytes())
            row.update(status='unknown', reason='injected_delivery_uncertainty',
                       execution_status='unknown', cleanup_accepted=True,
                       world_mutation_offered=True)
            checkpoint()

        def close(self):
            pass

    monkeypatch.setattr(managed_pipeline, 'PipelinedManagedExecutor', HostReplaced)
    monkeypatch.setattr(evaluate.sys, 'platform', 'linux')
    monkeypatch.setenv('SLURM_JOB_ID', 'test-owned-allocation')
    monkeypatch.setenv('SLURM_CPUS_PER_TASK', '1')
    monkeypatch.setattr(evaluate, 'prepare_observation_report',
                        lambda *_: pytest.fail('after the end signal the report waits'))
    result = evaluate.run(directory / 'plan.json')
    assert seen['result']['status'] == 'interrupted'
    assert seen['result']['ledger_report']['slots'][0]['status'] == 'reserved'
    assert seen['snapshot']['interrupted'] is True and seen['snapshot']['archive']['files'] > 0
    assert not evaluate.originals_retained(seen['snapshot'])
    assert result['observation_report_skipped']['reason'] == 'end_signal'
    final = json.loads((directory / 'snapshot.json').read_bytes())
    assert 'interrupted' not in final and evaluate.originals_retained(final)
    assert json.loads((directory / 'result.json').read_bytes())['status'] == result['status']
    assert result['status'] != 'interrupted' and result['stop_requested'] is True
    with tarfile.open(directory / 'evidence/evidence.tar') as archive:
        assert 'ledger/plan.json' in archive.getnames()


def test_prepare_cli_accepts_runtime_source_and_freezes_plan(execution, tmp_path):
    runtime = tmp_path / 'frozen-source'
    (runtime / 'tools/friday').mkdir(parents=True)
    (runtime / 'tools/friday/evaluate.py').write_text('# exact runtime path fixture\n')
    output = tmp_path / 'cli-plan'
    command = [sys.executable, str(FRIDAY / 'evaluate.py'), 'prepare',
        '--execution', str(execution), '--output', str(output),
        '--runtime-source', str(runtime)]
    for index in range(1, 4):
        command.extend(['--selection', f'checkpoint-{index}=model-{index}'])
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=15,
        env={'PATH': os.defpath,
             'PYTHONPATH': str(FRIDAY.parents[1] / 'python'),
             **{key: os.environ[key] for key in ('LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH')
                if key in os.environ}})
    plan = json.loads(result.stdout)
    assert plan['source_root'] == str(runtime)
    assert plan['planned'] == 60 and (output / 'plan.json').is_file()


def test_scheduled_submit_preserves_allocation_and_uses_slurm_timer(
        evaluate, execution, tmp_path):
    _plan, directory = prepared(evaluate, execution, tmp_path, workers=8)
    lock = tmp_path / 'configuration/friday-submit.lock'
    with (patch.object(evaluate.scheduler, 'submission_lock_path', return_value=lock.resolve()),
          patch.object(evaluate.scheduler, 'output', side_effect=['', '0/100/0/100', ''])):
        result = evaluate.submit(directory / 'plan.json', dry_run=True,
                                 begin='2099-01-01T12:00:00+10:00')
    assert '--cpus-per-task=8' in result['command']
    assert len([arg for arg in result['command'] if arg.startswith('--begin=')]) == 1
    assert result['scheduler_start_guaranteed'] is False
    assert not (directory / 'submission.json').exists()
    assert not (directory / 'owner.json').exists()


@pytest.mark.parametrize('begin', ['2020-01-01T12:00:00+00:00', '2099-01-01T12:00:00'])
def test_scheduled_submit_rejects_ambiguous_or_past_time(evaluate, tmp_path, begin):
    with pytest.raises(ValueError, match='future_begin'):
        evaluate.submit(tmp_path / 'not-opened.json', begin=begin)


def test_plan_exposes_pending_limit_that_can_serialize_requested_workers(
        evaluate, execution, tmp_path):
    value = json.loads(execution.read_bytes())
    value['pipeline'] = {'save_workers': 1, 'pending_limit': 1}
    execution.write_text(json.dumps(value))
    plan, _ = prepared(evaluate, execution, tmp_path, workers=8)
    assert plan['parallelism'] == {'requested_workers': 8, 'pending_limit': 1,
                                   'save_workers': 1, 'execution_upper_bound': 1}


def test_job_memory_is_a_plan_setting_with_the_old_default(evaluate, execution, tmp_path):
    lock = tmp_path / 'configuration/friday-submit.lock'
    for name, keywords, expected in (('default', {'workers': 4}, '--mem=16G'),
                                     ('sixteen', {'workers': 16, 'memory_gb': 112}, '--mem=112G')):
        plan = evaluate.prepare(execution, (tmp_path / name).resolve(),
                                selections={f'checkpoint-{i}': f'model-{i}' for i in range(1, 4)},
                                **keywords)
        assert plan['memory_gb'] == int(expected[6:-1])
        with (patch.object(evaluate.scheduler, 'submission_lock_path', return_value=lock.resolve()),
              patch.object(evaluate.scheduler, 'output', side_effect=['', '0/100/0/100', ''])):
            result = evaluate.submit(tmp_path / name / 'plan.json', dry_run=True)
        assert expected in result['command']
    with pytest.raises(ValueError, match='bounded_allocation'):
        prepared(evaluate, execution, tmp_path / 'bad', memory_gb=0)


def write_attempt(evaluate, scratch, number, *, finished=True):
    """One attempt original as the pipeline leaves it (receipts in the ledger's shape)."""
    attempt = scratch / 'ledger/attempts' / f'{number:06d}'
    run_id = f'run-{number}'
    (attempt / 'runs' / run_id).mkdir(parents=True)
    (attempt / 'runs' / run_id / 'events.jsonl').write_text('{"event": 1}\n')
    (attempt / 'runs' / run_id / f'global-{number}.bin').write_bytes(b'blob')
    (attempt / 'private/registration').mkdir(parents=True)
    (attempt / 'private/registration/model.tensor-tree').write_bytes(b'model-bytes')
    (attempt / 'private/host.log').write_text('log')

    def put(name, value):
        path = attempt / name
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return {'path': name, 'sha256': evaluate.hashed(path), 'bytes': path.stat().st_size}
    stored, shard = f'{number}' * 64, f't{number}' * 32
    trajectory = put('0014-trajectory.json', '{"steps": []}')
    row = {'status': 'finished' if finished else 'unknown', 'run_id': run_id,
           'stored_evidence_id': stored, 'trajectory_shard_id': shard, 'trajectory': trajectory,
           'preservation': put('0013-preservation.json', {
               'status': 'completed', 'run_id': run_id, 'artifact_id': stored,
               'peer_readback': {'full_readback': True, 'typed_verification': 'pass',
                                 'content_id': 'c' * 64}}),
           'trajectory_preservation': put('0015-trajectory-preservation.json', {
               'status': 'completed', 'run_id': run_id, 'artifact_id': shard,
               'full_readback': True, 'trajectory_verification': 'pass',
               'episode_sha256': trajectory['sha256'], 'original_content_id': 'c' * 64})}
    report = put('report.json', {'episodes': [row]})
    return {'status': row['status'], 'run_id': run_id, 'stored_evidence_id': stored,
            'trajectory_shard_id': shard,
            'report': {**report, 'path': str(attempt / 'report.json')},
            'preservation_route': 'trajectory_and_full_peer' if finished else 'full_peer'}


def test_evidence_keeps_peer_held_games_by_receipt_and_the_rest_in_one_tar(evaluate, tmp_path):
    """d1 (lead 11-14): no second copy of originals the peer already read back in full."""
    scratch, root = tmp_path / 'scratch', (tmp_path / 'round').resolve()
    root.mkdir(mode=0o700)
    package = tmp_path / 'package'
    package.mkdir()
    (package / 'model.tensor-tree').write_bytes(b'model-bytes')
    held = write_attempt(evaluate, scratch, 1)
    unknown = write_attempt(evaluate, scratch, 2, finished=False)
    changed = write_attempt(evaluate, scratch, 3)
    (scratch / 'ledger/attempts/000003/0015-trajectory-preservation.json').write_text('{}')
    for name in evaluate.REPORT_FILES:
        (scratch / name).write_text(name)
    (scratch / 'result.json').write_text('{}')
    changed['slot_id'] = 'slot-3'
    summary = {'ledger_report': {'slots': [held, unknown, changed, {'status': 'not_started'}]}}
    peer = {'kind': 'local', 'directory': '/peer'}
    receipt = evaluate.keep_evidence(scratch, root, summary,
                                     {'models': {'m': {'package_directory': str(package)}},
                                      'peer': peer})
    with tarfile.open(root / 'evidence/evidence.tar') as archive:
        names = set(archive.getnames())
    assert {'ledger/attempts/000001/report.json', 'ledger/attempts/000001/0013-preservation.json',
            'ledger/attempts/000001/private/host.log', 'result.json'} <= names
    assert not any(n.startswith('ledger/attempts/000001/runs/') for n in names)
    assert 'ledger/attempts/000001/0014-trajectory.json' not in names
    for number in ('000002', '000003'):  # not peer-held (unknown; changed receipt): all kept
        assert {f'ledger/attempts/{number}/runs/run-{int(number)}/events.jsonl',
                f'ledger/attempts/{number}/0014-trajectory.json'} <= names
    assert not any(n.endswith('model.tensor-tree') for n in names)
    model = evaluate.hashed(package / 'model.tensor-tree')
    assert receipt['model_copies'] == {'package_sha256': [model], 'not_copied': 3}
    assert not set(evaluate.REPORT_FILES) & names
    assert all((root / 'evidence' / name).read_text() == name for name in evaluate.REPORT_FILES)
    assert receipt['archive']['sha256'] == evaluate.hashed(root / 'evidence/evidence.tar')
    assert receipt['archive']['files'] == len(names)
    [held_entry] = receipt['peer_held']
    assert held_entry['run_id'] == 'run-1' and held_entry['stored_evidence_id'] == '1' * 64
    assert held_entry['attempt'] == 'ledger/attempts/000001'
    assert held_entry['content_id'] == 'c' * 64
    # S2: the peer-held files live only in the named peer; S4: counted when kept whole.
    assert receipt['peer'] == peer and receipt['self_contained'] is False
    assert receipt['kept_in_full'] == ['slot-3'] and receipt['not_regular'] == []
    assert evaluate.originals_retained(receipt)
    assert evaluate.originals_retained({'schema': 'spireagent/friday-local-snapshot-v1',
                                        'full_readback': True})
    for other in ({'full_readback': False}, {'full_readback': True},  # N1: only v1 or v2
                  {'schema': 'spireagent/other', 'full_readback': True},
                  {**receipt, 'interrupted': True}):
        assert not evaluate.originals_retained(other)
    shutil.rmtree(scratch)  # the node scratch is gone: an attempt comes back from the tar
    path = evaluate.retained_original(root, str(scratch), Path(unknown['report']['path']),
                                      tmp_path / 'work')
    assert evaluate.hashed(path) == unknown['report']['sha256']
    assert (path.parent / 'runs/run-2/events.jsonl').is_file()


def test_one_malformed_attempt_is_kept_whole_and_the_rest_proceed(evaluate, tmp_path):
    """S4: a finished game whose receipts cannot be read never aborts the evidence step."""
    scratch, root = tmp_path / 'scratch', (tmp_path / 'round').resolve()
    root.mkdir(mode=0o700)
    held = write_attempt(evaluate, scratch, 1)
    empty = write_attempt(evaluate, scratch, 2)
    (scratch / 'ledger/attempts/000002/report.json').write_text('{"episodes": []}')
    empty['report']['sha256'] = evaluate.hashed(scratch / 'ledger/attempts/000002/report.json')
    pathless = write_attempt(evaluate, scratch, 3)
    del pathless['report']['path']
    empty['slot_id'], pathless['slot_id'] = 'slot-2', 'slot-3'
    os.mkfifo(scratch / 'ledger/pipe')  # N2: not a regular file, named, never counted
    receipt = evaluate.keep_evidence(scratch, root, {'ledger_report': {
        'slots': [held, empty, pathless]}}, {'models': {}})
    with tarfile.open(root / 'evidence/evidence.tar') as archive:
        names = set(archive.getnames())
    assert [h['run_id'] for h in receipt['peer_held']] == ['run-1']
    assert receipt['kept_in_full'] == ['slot-2', 'slot-3']
    assert {'ledger/attempts/000002/runs/run-2/events.jsonl',
            'ledger/attempts/000003/runs/run-3/events.jsonl'} <= names
    assert receipt['not_regular'] == ['ledger/pipe'] and 'ledger/pipe' not in names
    assert receipt['archive']['files'] == len(names)


def test_interrupted_evidence_skips_unreadable_files_and_is_never_retained(evaluate, tmp_path):
    """S1: at the end signal the live scratch is kept as far as it can be read."""
    scratch, root = tmp_path / 'scratch', (tmp_path / 'round').resolve()
    root.mkdir(mode=0o700)
    write_attempt(evaluate, scratch, 1, finished=False)
    locked = scratch / 'ledger/attempts/000001/private/live.log'
    locked.write_text('being written')
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip('running as a user that reads mode-0 files')
        with pytest.raises(PermissionError):
            evaluate.keep_evidence(scratch, root, {'ledger_report': {'slots': []}},
                                   {'models': {}})
        receipt = evaluate.keep_evidence(scratch, root, {'ledger_report': {'slots': []}},
                                         {'models': {}}, interrupted=True)
    finally:
        locked.chmod(0o600)
    with tarfile.open(root / 'evidence/evidence.tar') as archive:
        names = set(archive.getnames())
        assert archive.extractfile('ledger/attempts/000001/private/host.log').read() == b'log'
    assert receipt['interrupted'] is True
    assert receipt['unreadable'] == ['ledger/attempts/000001/private/live.log']
    assert 'ledger/attempts/000001/runs/run-1/events.jsonl' in names
    assert not evaluate.originals_retained(receipt)


def test_unknown_submission_outcome_keeps_intent_and_is_never_automatically_resubmitted(
        evaluate, execution, tmp_path):
    _plan, directory = prepared(evaluate, execution, tmp_path)
    lock = tmp_path / 'configuration/friday-submit.lock'
    with (patch.object(evaluate.scheduler, 'submission_lock_path', return_value=lock.resolve()),
          patch.object(evaluate.scheduler, 'output', side_effect=[
              '', '0/100/0/100', '', OSError('lost sbatch response')]),
          pytest.raises(OSError)):
        evaluate.submit(directory / 'plan.json')
    intent = json.loads((directory / 'submission-intent.json').read_bytes())
    assert 'submission_requested_at' in intent and 'job_id' not in intent
    with (patch.object(evaluate.scheduler, 'submission_lock_path', return_value=lock.resolve()),
          patch.object(evaluate.scheduler, 'output', return_value='') as output,
          pytest.raises(ValueError, match='submission_requires_owner_reconciliation')):
        evaluate.submit(directory / 'plan.json')
    assert output.call_count == 1  # read-only original-name reconciliation, no second sbatch


def test_observation_report_defaults_off_and_rejects_multiple_models(
        evaluate, execution, tmp_path):
    plan, _directory = prepared(evaluate, execution, tmp_path)
    assert plan['observation_report'] is False
    with pytest.raises(ValueError, match='single_model_observation_report'):
        evaluate.prepare(execution, tmp_path / 'not-created',
                         selections={f'checkpoint-{i}': f'model-{i}' for i in range(1, 4)},
                         observation_report=True)


def test_evaluation_allocation_owns_trajectory_detail_preparation(evaluate, tmp_path):
    execution = tmp_path / 'execution.json'
    execution.write_text('{}')
    plan = {'execution': {'path': str(execution), 'sha256': evaluate.hashed(execution)}}
    summary = {'plan_sha256': 'a' * 64, 'ledger_report': {'slots': [
        {'run_id': 'one', 'seed': 'seed-one', 'trajectory_shard_id': 'shard-one'}]}}
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    opened = []

    def open_peer(peer):
        opened.append(peer)
        return 'test-store'

    def read_shard(store, shard, *, scratch):
        assert store == 'test-store' and shard == 'shard-one'
        assert scratch.is_dir()
        yield {'run_id': 'one', 'identity': {'seed': 'seed-one'}}

    def load_cohort(path, *, details):
        assert json.loads(path.read_bytes()) == summary
        assert json.loads(details.read_bytes()) == {'episodes': [{'run_id': 'one'}]}
        return {'source_sha256': evaluate.hashed(path), 'details_sha256': evaluate.hashed(details)}

    execution.write_text(json.dumps({'peer': {'kind': 's3'}}))
    plan['execution']['sha256'] = evaluate.hashed(execution)
    with (patch('spireagent.storage.config.open_peer', open_peer),
          patch('spireagent.workbench.managed_trajectory_store.read_shard', read_shard),
          patch('stpd.evaluation.rl_observation.trajectory_details',
                side_effect=lambda e: {'run_id': e['run_id']}),
          patch('stpd.evaluation.rl_observation.load_cohort', load_cohort)):
        metadata = evaluate.prepare_observation_report(plan, summary, scratch)
    assert opened == [{'kind': 's3'}]
    assert metadata['files']['cohort']['sha256'] == evaluate.hashed(scratch / 'report-cohort.json')
    assert metadata['files']['source']['sha256'] == evaluate.hashed(scratch / 'report-source.json')


def test_evaluation_general_partition_preserves_fair_share_and_excludes_G1(
        evaluate, execution, tmp_path):
    _, directory = prepared(evaluate, execution, tmp_path)
    lock = tmp_path / 'submit.lock'
    with (patch.object(evaluate.scheduler, 'submission_lock_path', return_value=lock.resolve()),
          patch.object(evaluate.scheduler, 'output', side_effect=['', '0/100/0/100', '']) as calls):
        result = evaluate.submit(directory / 'plan.json', dry_run=True,
                                 partition='general', exclude_nodes=['gpu-1', 'actor-1'])
    assert '--partition=general' in result['command']
    assert '--exclude=gpu-1,actor-1' in result['command']
    assert all('general' in call.args[0] for call in calls.call_args_list[1:])


def test_node_probe_submission_names_exactly_one_node(evaluate, execution, tmp_path):
    """OPS node probe: one real game pinned to one excluded node; a range or set is refused."""
    _, directory = prepared(evaluate, execution, tmp_path)
    lock = tmp_path / 'submit.lock'
    with (patch.object(evaluate.scheduler, 'submission_lock_path', return_value=lock.resolve()),
          patch.object(evaluate.scheduler, 'output', side_effect=['', '0/100/0/100', ''])):
        result = evaluate.submit(directory / 'plan.json', dry_run=True, partition='general',
                                 nodelist='smp-9-7')
    assert '--nodelist=smp-9-7' in result['command']
    for bad in ('smp-9-[7-8]', 'smp-9-7,smp-9-8', ''):
        with pytest.raises(ValueError, match='single_node_name_required'):
            evaluate.submit(directory / 'plan.json', dry_run=True, nodelist=bad)
