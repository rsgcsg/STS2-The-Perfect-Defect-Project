"""The saved ledger is authoritative; missing evidence stays missing."""
import json
import subprocess
import sys

import pytest
from stpd.evaluation.rl_observation import compare, load_cohort, write_report


def saved(tmp_path, *, admitted=True, status='finished', floor=8, result='loss', seed='a'):
    slot = {'step': 'v1', 'seed': seed, 'run_id': 'run-a', 'character': 'Defect',
            'ascension': 0, 'status': status, 'max_observed_floor': floor,
            'natural_result': result}
    path = tmp_path / f'{seed}-{status}-{floor}-{admitted}.json'
    path.write_text(json.dumps({'ledger_report': {'slots': [slot], 'planned': 1,
        'host_kind': 'managed', 'score_admitted': admitted}}))
    return path


def test_missing_boss_and_death_floor_are_not_filled_from_max_floor(tmp_path):
    cohort = load_cohort(saved(tmp_path, floor=17))
    assert cohort['max_floor'] == 17
    assert cohort['boss1_reached_witnessed'] == cohort['boss1_cleared_witnessed'] == 0
    assert cohort['boss_count_missing'] == 1
    assert cohort['rows'][0]['death_floor'] is None
    assert cohort['loss_floor_counts'] == {}


def test_detail_join_is_by_run_and_preserves_explicit_terminal_and_boss(tmp_path):
    details = tmp_path / 'details.json'
    details.write_text(json.dumps({'rows': [{'run_id': 'run-a', 'seed': 'a',
        'terminal_public': {'floor': 8}, 'trajectory': {'outcome': {
            'max_observed_floor': 8, 'boss_clearance': {'cleared_acts': [1], 'count': 1}}}}]}))
    cohort = load_cohort(saved(tmp_path), details=details)
    assert cohort['loss_floor_counts'] == {8: 1}
    assert cohort['boss1_cleared_witnessed'] == 1
    assert cohort['boss_count_missing'] == 0
    details.write_text(details.read_text().replace('"seed": "a"', '"seed": "other"'))
    with pytest.raises(ValueError, match='seed_mismatch'):
        load_cohort(saved(tmp_path), details=details)


@pytest.mark.parametrize('status', ['unknown', 'censored', 'not_started', 'save_failed'])
def test_unresolved_slots_never_pair_as_a_loss(tmp_path, status):
    before = load_cohort(saved(tmp_path))
    after = load_cohort(saved(tmp_path, status=status))
    assert after['planned'] == 1 and after['floor_n'] == after['losses'] == 0
    assert after['status_counts'] == {status: 1}
    assert compare(before, after)['counts'] == {'unpaired': 1}


def test_unqualified_audit_does_not_become_score(tmp_path):
    cohort = load_cohort(saved(tmp_path, admitted=False))
    assert cohort['floor_n'] == cohort['losses'] == 0
    assert cohort['rows'][0]['floor'] == 8


def test_pairing_and_power_uses_distinct_seeds_and_exposes_variance(tmp_path):
    before = load_cohort(saved(tmp_path))
    after = load_cohort(saved(tmp_path, floor=10))
    result = compare(before, after)
    assert result['mean_delta'] == 2 and result['paired_n'] == 1
    assert result['bootstrap95_fixed_seed_sensitivity'] is None
    assert result['paired_delta_sample_variance'] is None
    after['host_kind'] = 'reference'
    with pytest.raises(ValueError, match='different_host'):
        compare(before, after)


def test_denominator_and_duplicate_seed_guard(tmp_path):
    path = saved(tmp_path)
    obj = json.loads(path.read_text())
    obj['ledger_report']['planned'] = 2
    path.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match='denominator'):
        load_cohort(path)
    obj['ledger_report']['slots'] *= 2
    path.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match='duplicate_seed'):
        load_cohort(path)


def test_real_cli_writes_parseable_outputs_and_refuses_overwrite(tmp_path):
    source = saved(tmp_path)
    output = tmp_path / 'report'
    command = [sys.executable, '-m', 'stpd.evaluation.rl_observation',
               '--current', str(source), '--baseline', str(source), '--output', str(output)]
    subprocess.run(command, check=True, timeout=15)
    assert json.loads((output / 'report.json').read_text())['cohorts']['current']['planned'] == 1
    import xml.etree.ElementTree as ET
    ET.fromstring((output / 'distribution.svg').read_text())
    assert 'unpaired' not in (output / 'paired.csv').read_text()
    assert subprocess.run(command, capture_output=True, timeout=15).returncode != 0
    with pytest.raises(FileExistsError):
        write_report({'current': load_cohort(source)}, output)


@pytest.mark.parametrize('changes', [
    {'terminal_public': {'floor': 999}},
    {'terminal_public': {'floor': True}},
    {'terminal_public': {'result': 'win'}},
    {'trajectory': {'outcome': {'terminal': {'result': 'win'}}}},
    {'trajectory': {'outcome': {'status': 'unknown', 'unknown': True}}},
])
def test_conflicting_terminal_details_are_rejected(tmp_path, changes):
    details = tmp_path / 'details.json'
    details.write_text(json.dumps({'rows': [{'run_id': 'run-a', **changes}]}))
    with pytest.raises(ValueError, match='detail_terminal'):
        load_cohort(saved(tmp_path), details=details)


def test_raw_trajectory_public_combat_reaches_csv(tmp_path):
    from test_p1_trajectory import episode

    raw = episode('a' * 64, 'run-a')
    raw['identity']['seed'] = 'a'
    raw['observations'][0]['observation'].update({
        'run': {'content': {'act': 1, 'floor': 8}},
        'scene': {'native_kind': 'combat_turn', 'family': 'combat'},
        'combat': {'content': {'encounter_type': 'boss',
                               'enemies': [{'definition_id': 'SYNTHETIC_ENEMY'}]}}})
    raw['observations'][1]['observation'].update({
        'run': {'content': {'act': 1, 'floor': 8}},
        'scene': {'native_kind': 'game_over', 'family': 'terminal'}})
    raw['outcome']['max_observed_floor'] = 8
    details = tmp_path / 'raw.json'
    details.write_text(json.dumps({'episodes': [raw]}))
    cohort = load_cohort(saved(tmp_path), details=details)
    assert cohort['boss1_reached_witnessed'] == 1
    assert cohort['boss1_cleared_witnessed'] == 0
    assert cohort['loss_floor_counts'] == {8: 1}
    output = tmp_path / 'full-report'
    write_report({'current': cohort}, output)
    text = (output / 'seeds.csv').read_text()
    assert 'SYNTHETIC_ENEMY' in text and 'boss' in text


def test_known_protocol_mismatch_is_rejected(tmp_path):
    before = load_cohort(saved(tmp_path))
    after = load_cohort(saved(tmp_path, floor=10))
    before['protocol_observed']['behavior_policy'] = {'mode': 'argmax'}
    after['protocol_observed']['behavior_policy'] = {'mode': 'temperature_uniform'}
    with pytest.raises(ValueError, match='different_observed_protocol'):
        compare(before, after)


def test_zero_pilot_variance_does_not_suggest_zero_seeds(tmp_path):
    cohort = load_cohort(saved(tmp_path))
    cohort['rows'].append({**cohort['rows'][0], 'seed': 'b'})
    result = compare(cohort, cohort)
    assert result['paired_delta_sample_variance'] == 0
    assert result['approx_paired_seeds_for_one_floor_80pct_power'] is None
    assert result['approx_paired_seeds_for_ci95_halfwidth_one_floor'] is None


def test_planning_and_bootstrap_keep_known_paired_difference(tmp_path):
    cohort = load_cohort(saved(tmp_path))
    before = {**cohort, 'rows': [{**cohort['rows'][0], 'seed': str(i), 'floor': 10}
                                for i in range(3)]}
    after = {**cohort, 'rows': [{**cohort['rows'][0], 'seed': str(i), 'floor': 10+i}
                               for i in range(3)]}
    result = compare(before, after)
    assert result['mean_delta'] == result['paired_delta_sample_variance'] == 1
    assert result['approx_paired_seeds_for_ci95_halfwidth_one_floor'] == 4
    assert result['approx_paired_seeds_for_one_floor_80pct_power'] == 8
    assert result['counts'] == {'same': 1, 'farther': 2}


def test_terminal_witness_does_not_upgrade_unresolved_ledger(tmp_path):
    details = tmp_path / 'details.json'
    details.write_text(json.dumps({'rows': [{'run_id': 'run-a',
        'terminal_public': {'floor': 8, 'result': 'loss'},
        'trajectory': {'outcome': {'status': 'finished', 'terminal': {'result': 'loss'}}}}]}))
    cohort = load_cohort(saved(tmp_path, status='save_failed', result=None), details=details)
    assert cohort['planned'] == 1 and cohort['status_counts'] == {'save_failed': 1}
    assert cohort['floor_n'] == cohort['losses'] == 0
    assert cohort['loss_floor_counts'] == {}
