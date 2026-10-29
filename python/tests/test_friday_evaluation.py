"""Real evaluation ledger/seed/audit planning, with gameplay and scheduler IO replaced."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
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
    result = evaluate.run(directory / 'plan.json')
    assert result['status'] == 'stopped'
    assert result['ledger_report']['unknown'] == 1
    assert result['ledger_report']['not_started'] == 59
    assert result['ledger_report']['score_admitted'] is False
    assert len(offers) == 1
    assert json.loads((directory / 'snapshot.json').read_bytes())['full_readback'] is True
    with pytest.raises(FileExistsError):
        evaluate.run(directory / 'plan.json')
    assert len(offers) == 1


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
        env={'PATH': __import__('os').defpath,
             'PYTHONPATH': str(FRIDAY.parents[1] / 'python')})
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
