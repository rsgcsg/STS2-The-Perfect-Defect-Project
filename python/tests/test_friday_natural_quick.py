"""Real seed/ledger/report seams; only World execution and its receipts are scripted."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from test_friday_evaluation import FRIDAY, module_file

ADAPTER = {'id': 'fixture-action-adapter', 'version': '1', 'code_sha256': 'a' * 64}
PROTOCOL = {'host_pin': {'version': 'fixture-host'}, 'runtime_pin': {'version': 'fixture-runtime'},
            'input_spec': {'id': 'fixture-input'}, 'initial_progress_sha256': 'b' * 64,
            'behavior_policy': {'mode': 'argmax', 'temperature': None, 'uniform_mix': 0}}


@pytest.fixture
def quick():
    batch = module_file('natural_quick_batch', FRIDAY / 'batch.py')
    with patch.dict(sys.modules, {'batch': batch}):
        scheduler = module_file('natural_quick_scheduler', FRIDAY / 'submit.py')
        with patch.dict(sys.modules, {'submit': scheduler}):
            evaluate = module_file('natural_quick_evaluate', FRIDAY / 'evaluate.py')
            with patch.dict(sys.modules, {'evaluate': evaluate}):
                return module_file('natural_quick', FRIDAY / 'quick.py')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + '\n')


def normal_result(quick, plan_path, scenario):
    """Use the real frozen ledger selection and normal cohort projection."""
    from spireagent.json_boundary import json_bytes
    from stpd.evaluation.rl_observation import load_cohort

    ev = quick.evaluate
    plan = ev.load_plan(plan_path)
    ledger = ev.create_ledger(plan, plan_path.parent / 'ledger')
    slots = copy.deepcopy(ledger.plan['slots'])
    role = next(iter(plan['selections']))
    unknown, admitted, unsettled = scenario(role, slots)
    execution = ev.read_pinned(plan['execution'])
    model = execution['models'][role]
    model_sha = json.loads((Path(model['package_directory']) / 'model.json').read_bytes())[
        'package_sha256']
    episodes = []
    for slot in slots:
        n = slot['seed_ordinal']
        status = 'unknown' if n in unknown else 'finished'
        if unsettled and n != slots[0]['seed_ordinal']:
            slot.update(status='not_started', world_mutation_offered=False)
            continue
        floor = slot.pop('fixture_floor',
                         None if status == 'unknown' else 12 if role == 'current' else 10)
        run_id = role + '-' + str(n)
        slot.update(status=status, run_id=run_id,
                    natural_result=None if status == 'unknown' else 'loss',
                    max_observed_floor=floor, cleanup_accepted=not unsettled,
                    world_mutation_offered=True)
        episodes.append({'seed': slot['seed'], 'run_id': run_id, 'observations': [],
            'terminal_public': {}, 'trajectory': {'run_id': run_id,
                'identity': {**PROTOCOL, 'seed': slot['seed'],
                    'model_artifact_id': model['artifact_id'],
                    'model_sha256': model_sha, 'producer': {'repository': 'fixture',
                    'source_revision': 'c' * 40, 'uv_lock_sha256': 'd' * 64}},
                'outcome': {'max_observed_floor': floor,
                            'boss_clearance': {'cleared_acts': [], 'count': None}}}})
    report = {'planned': len(slots), 'host_kind': 'managed', 'slots': slots,
              'score_admitted': admitted,
              'formal_audit': {'planned': 1, 'qualified': int(admitted), 'satisfied': admitted}}
    source = {'plan_sha256': ev.hashed(plan_path), 'ledger_report': report,
              'error_type': None}
    evidence = plan_path.parent / 'evidence'
    save(evidence / 'report-source.json', source)
    save(evidence / 'report-details.json', {'episodes': episodes})
    cohort = load_cohort(evidence / 'report-source.json', details=evidence / 'report-details.json')
    save(evidence / 'report-cohort.json', cohort)
    files = {name: {'path': f'report-{name}.json',
                   'sha256': ev.hashed(evidence / f'report-{name}.json')}
             for name in ('source', 'details', 'cohort')}
    save(plan_path.parent / 'result.json', {**source, 'observation_report': {
        'schema': 'spireagent/evaluation-observation-files-1', 'files': files,
        'ledger_report_sha256': hashlib.sha256(json_bytes(report)).hexdigest()}})
    save(plan_path.parent / 'snapshot.json', {'full_readback': True})


def configured(quick, tmp_path, monkeypatch):
    from stpd.evaluation.suites import rl3_learning_curve_manifest

    models = {role: {'model_artifact_id': digit * 64, 'model_sha256': sha * 64}
              for role, digit, sha in (('current', '1', 'a'), ('baseline', '2', 'b'))}
    packages = {}
    for role, record in models.items():
        package = tmp_path / role
        save(package / 'model.json', {'package_sha256': record['model_sha256']})
        packages[role] = {'artifact_id': record['model_artifact_id'],
                          'package_directory': str(package)}
    execution = {'schema': 'spireagent/managed-evaluation-execution-v1', 'store': 's3',
                 'retention': {'tier': 'trajectory', 'purpose': 'evaluation', 'audit_every': 50},
                 'pipeline': {'pending_limit': 8, 'save_workers': 2}, 'agent_adapter': ADAPTER,
                 'models': packages, 'peer': {'kind': 's3', 'bucket': 'fixture',
                  'prefix': 'fixture',
                  'endpoint': 'https://example.invalid', 'region': 'auto'}}
    value = rl3_learning_curve_manifest('v0.111.0')
    manifest = tmp_path / 'original100.json'
    save(manifest, value)
    base_execution = tmp_path / 'baseline-execution.json'
    save(base_execution, {**execution, 'models': {'baseline': packages['baseline']}})
    base = tmp_path / 'baseline-cache'
    quick.evaluate.prepare(base_execution, base, selections={'baseline': 'baseline'},
                           manifest_path=manifest, seed_prefix=20)
    normal_result(quick, base / 'plan.json', lambda _r, _s: (set(), True, False))
    input_path = tmp_path / 'execution.json'
    save(input_path, execution)
    plan = quick.prepare(input_path, tmp_path / 'round', manifest_path=manifest,
        runtime_source=FRIDAY.parent.parent, models=models,
        baseline_cache=[quick.batch_reference(base / 'plan.json')],
        baseline_unknown_seeds=[value['seeds'][55]], version=9, updates=120)
    monkeypatch.setenv('SLURM_JOB_ID', 'fixture')
    monkeypatch.setenv('SLURM_CPUS_PER_TASK', '4')
    monkeypatch.setattr('stpd.p1.export.adapter_identity', lambda: ADAPTER)
    return tmp_path / 'round/plan.json', plan


def test_unknown_on_either_side_refills_new_ordinals_without_replay(quick, tmp_path, monkeypatch):
    path, plan = configured(quick, tmp_path, monkeypatch)
    offers = []

    def execute(batch_path, *, on_stop=None):
        batch = quick.evaluate.load_plan(batch_path)
        role = next(iter(batch['selections']))
        ordinals = batch['seed_ordinals']
        offers.append((role, ordinals))

        def scenario(_role, slots):
            if role == 'current' and ordinals[0] == 1:
                n = next(s['seed_ordinal'] for s in slots if not s['formal_audit_selected'])
                return {n}, True, False
            if role == 'baseline' and ordinals == [21]:
                return {21}, False, False  # sole selected audit is unknown
            return set(), True, False
        normal_result(quick, batch_path, scenario)

    monkeypatch.setattr(quick.evaluate, 'run', execute)
    result = quick.run(path)
    assert result['complete'] and result['paired_n'] == 20
    assert result['comparison']['mean_delta'] == 2
    assert offers == [('current', list(range(1, 21))), ('current', [21]),
                      ('baseline', [21]), ('current', [22]), ('baseline', [22])]
    assert result['new_current_world_offers'] == 22
    assert result['new_baseline_world_offers'] == 2
    assert len(result['baseline_cache']) == len(plan['baseline_cache']) + 2
    assert result['raw_ledger_flags_unchanged']


def test_unknown_audit_never_qualifies_other_natural_rows(quick, tmp_path, monkeypatch):
    path, _ = configured(quick, tmp_path, monkeypatch)
    offers = []

    def execute(batch_path, *, on_stop=None):
        plan = quick.evaluate.load_plan(batch_path)
        role = next(iter(plan['selections']))
        offers.append((role, plan['seed_ordinals']))

        def scenario(_role, slots):
            if role == 'current' and plan['seed_ordinals'][0] == 1:
                return {next(s['seed_ordinal'] for s in slots if s['formal_audit_selected'])}, \
                    False, False
            return set(), True, False
        normal_result(quick, batch_path, scenario)

    monkeypatch.setattr(quick.evaluate, 'run', execute)
    result = quick.run(path)
    assert result['complete'] and result['paired_n'] == 20
    assert offers == [('current', list(range(1, 21))), ('current', list(range(21, 41))),
                      ('baseline', list(range(21, 41)))]
    assert result['batches'][1]['raw_score_admitted'] is False
    assert not quick.qualified(result['batches'][1])


def test_original100_exhaustion_is_incomplete_and_skips_known_baseline_unknown(
        quick, tmp_path, monkeypatch):
    path, _ = configured(quick, tmp_path, monkeypatch)
    offered = []

    def execute(batch_path, *, on_stop=None):
        plan = quick.evaluate.load_plan(batch_path)
        assert next(iter(plan['selections'])) == 'current'
        offered.extend(plan['seed_ordinals'])
        normal_result(quick, batch_path,
                      lambda _r, slots: ({s['seed_ordinal'] for s in slots}, False, False))

    monkeypatch.setattr(quick.evaluate, 'run', execute)
    result = quick.run(path)
    assert not result['complete'] and result['paired_n'] == 0
    assert result['reason'] == 'original100_pool_exhausted'
    assert offered == [i for i in range(1, 101) if i != 56]
    with pytest.raises(FileExistsError):
        quick.run(path)  # unknown controller delivery cannot be re-offered


def test_unsettled_save_stops_admission_and_preserves_planned_vs_offered(
        quick, tmp_path, monkeypatch):
    path, _ = configured(quick, tmp_path, monkeypatch)
    offers = []

    def execute(batch_path, *, on_stop=None):
        offers.append(batch_path)
        normal_result(quick, batch_path, lambda _r, _s: (set(), False, True))

    monkeypatch.setattr(quick.evaluate, 'run', execute)
    result = quick.run(path)
    assert len(offers) == 1 and not result['complete']
    assert result['reason'] == 'non_unknown_or_unsettled_batch_requires_owner_read'
    assert result['new_current_planned_slots'] == 20
    assert result['new_current_world_offers'] == 1


def test_launcher_keeps_original_allocation_owner(quick, tmp_path):
    script = quick.launcher(tmp_path / 'plan.json', {'source_root': str(FRIDAY.parent.parent)})
    assert 'tools/friday/allocation.py' in script and '--mode evaluation' in script
    assert 'tools/friday/quick.py" run --plan' in script
    assert 'source "$credentials"' in script


def test_natural_missing_floor_is_attention_not_unknown_refill(quick, tmp_path, monkeypatch):
    path, _ = configured(quick, tmp_path, monkeypatch)
    offers = []

    def execute(batch_path, *, on_stop=None):
        offers.append(batch_path)

        def scenario(_role, slots):
            slots[0]['fixture_floor'] = None
            return set(), True, False
        normal_result(quick, batch_path, scenario)

    monkeypatch.setattr(quick.evaluate, 'run', execute)
    result = quick.run(path)
    assert len(offers) == 1 and not result['complete']
    assert result['reason'] == 'non_unknown_or_unsettled_batch_requires_owner_read'
    assert result['paired_n'] == result['comparison']['paired_n'] == 0
    # The original admitted natural rows remain original facts, not quick pairs.
    assert result['batches'][-1]['raw_score_admitted'] is True
    assert result['batches'][-1]['cohort']['floor_n'] == 19
    batch = module_file('quick_watch_batch', FRIDAY / 'batch.py')
    with patch.dict(sys.modules, {'batch': batch, 'evaluate': quick.evaluate, 'quick': quick}):
        watch = module_file('quick_watch', FRIDAY / 'watch.py')
        watcher = object.__new__(watch.Watcher)
        pending = {'plan_sha256': quick.hashed(path), 'model_artifact_id': '1' * 64,
                   'version': 9, 'updates': 120, 'due_at': 0}
        watcher.state = {'evaluations': [], 'pending': pending}
        watcher.persist = lambda: None
        watcher.append_reference = lambda *a: None
        watcher.publish = lambda *a, **kw: None
        watcher.finish_natural_quick(path.parent / 'result.json', pending)
    assert watcher.state['pending']['state'] == 'attention_required'


def test_late_batch_stop_prevents_refill_even_after_clean_preservation(
        quick, tmp_path, monkeypatch):
    path, _ = configured(quick, tmp_path, monkeypatch)
    offers = []

    def execute(batch_path, *, on_stop=None):
        offers.append(batch_path)

        def scenario(_role, slots):
            return {next(s['seed_ordinal'] for s in slots if not s['formal_audit_selected'])}, \
                True, False
        normal_result(quick, batch_path, scenario)
        on_stop()  # Evaluate's real late-USR1 callback is tested at its snapshot seam.

    monkeypatch.setattr(quick.evaluate, 'run', execute)
    result = quick.run(path)
    assert len(offers) == 1 and result['paired_n'] == 19 and not result['complete']
    assert result['reason'] == 'round_stop_or_wall_limit'


def test_unclosed_baseline_cache_never_opens_a_new_current_world(quick, tmp_path, monkeypatch):
    path, plan = configured(quick, tmp_path, monkeypatch)
    baseline = tmp_path / 'unclosed-cache'
    quick.evaluate.prepare(tmp_path / 'baseline-execution.json', baseline,
                           selections={'baseline': 'baseline'}, seed_prefix=20,
                           manifest_path=Path(plan['manifest']['path']))
    normal_result(quick, baseline / 'plan.json', lambda _r, _s: (set(), False, True))
    plan['baseline_cache'] = [quick.batch_reference(baseline / 'plan.json')]
    save(path, plan)
    monkeypatch.setattr(quick.evaluate, 'run', lambda *_a, **_k: pytest.fail('no new World'))
    result = quick.run(path)
    assert not result['complete'] and result['new_current_world_offers'] == 0
    assert result['reason'] == 'non_unknown_or_unsettled_batch_requires_owner_read'
