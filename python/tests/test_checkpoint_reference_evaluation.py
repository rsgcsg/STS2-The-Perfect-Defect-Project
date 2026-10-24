from __future__ import annotations

import copy
import hashlib
import json

import pytest
from test_native_episode_application import native_app as native_app
from test_native_episode_application import native_caps as native_caps
from test_native_episode_application import native_endpoint as native_endpoint
from test_native_episode_application import prepare_body, prepared

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench.native_episode_binding import checked_context, preparation_body
from spireagent.workbench.reference_suite import load_manifest
from stpd.evaluation.suites import CHARACTERS, checkpoint_reference_manifest, seed_membership


@pytest.mark.parametrize('character,ascension', [(c, 0) for c in CHARACTERS] + [('Defect', 10)])
def test_same_owned_application_carries_scenario_to_real_host_pipe(
    native_app, character, ascension
):
    app, _profile, fixture, _log = native_app
    value = json.loads(fixture.read_bytes())
    value['identity'].update(
        requested_character_id=character.upper(), requested_ascension=ascension)
    fixture.write_bytes(json_bytes(value))
    state = prepared(app, prepare_body(character=character, ascension=ascension))
    binding = state['admission_context']['binding']
    assert binding['scenario'] == {'character': character, 'ascension': ascension}
    assert binding['initialization'] == 'fresh_seeded_reference_scenario'
    assert checked_context(state['admission_context']) == state['admission_context']
    changed = copy.deepcopy(state['admission_context'])
    changed['binding']['scenario']['ascension'] = True
    with pytest.raises(BoundaryError):
        checked_context(changed)


def test_foreign_scenario_identity_is_rejected_before_actor(native_app):
    app, _profile, fixture, _log = native_app
    value = json.loads(fixture.read_bytes())
    value['identity'].update(requested_character_id='DEFECT', requested_ascension=0)
    fixture.write_bytes(json_bytes(value))
    app.prepare_native_episode(prepare_body(character='Silent', ascension=0))
    app.local_environment.native_worker.join(8)
    state = app.native_episode_status()
    assert state['phase'] == 'unknown'
    assert state['error_code'] == 'owned_scenario_unproven'
    assert state['actor'] is None


@pytest.mark.parametrize('changes', [{'character': 'Bad', 'ascension': 0},
    {'character': 'Defect', 'ascension': True}, {'character': 'Defect', 'ascension': 11},
    {'character': 'Defect'}])
def test_invalid_scenario_rejected(changes):
    with pytest.raises(BoundaryError):
        preparation_body(prepare_body(**changes))


def test_reserved_paired_sets_are_frozen_and_cannot_be_used_for_training(tmp_path):
    all_seeds = set()
    for char, asc in [(c, 0) for c in CHARACTERS] + [('Defect', 10)]:
        value = checkpoint_reference_manifest('v0.111.0', char, asc)
        assert not all_seeds.intersection(value['seeds'])
        all_seeds.update(value['seeds'])
        path = tmp_path / (value['suite_id']+'.json')
        path.write_bytes(json_bytes(value))
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert load_manifest(path, sha)[1] == value
        assert all(value['suite_id'] in seed_membership(seed) for seed in value['seeds'])
        changed = copy.deepcopy(value)
        changed['ascension'] = 1
        path.write_bytes(json_bytes(changed))
        with pytest.raises(ValueError):
            load_manifest(path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert len(all_seeds) == 20


def test_terminal_and_metrics_use_original_public_context_not_missing_victory(tmp_path):
    from types import SimpleNamespace

    from spireagent.workbench.agent_task_outcome import _global_terminal
    from stpd.evaluation.reference_metrics import read_reference_metrics

    snapshot = {'schema': 'sts2.player-environment/snapshot-1', 'snapshot_id': 'original',
                'session': {'runtime_instance_id': 'runtime'},
                'persistent': {'content': {'run': {'floor': 12, 'act': 1, 'ascension': 0},
                                          'player': {'hp': 0,
                                                     'character_definition_id': 'DEFECT'}}},
                'interaction': {'kind': 'game_over',
                    'content_schema': 'sts2.player-environment/surface/game_over-1',
                    'content': {'surface': {'kind': 'game_over', 'stage': 'intro'},
                                'context': {'kind': 'game_over', 'result': 'loss'}}}}
    raw = json_bytes({'capture': {'session': {'runtime_instance_id': 'runtime'}},
                      'serialized_observation': json.dumps(
                          {'capture_status': 'complete', 'snapshot': snapshot})})
    (tmp_path / 'source.bin').write_bytes(raw)
    ref = {'path': 'source.bin', 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    events = [{'sequence': 1, 'kind': 'global_source',
               'payload': {'ordinal': 1, 'artifact': ref}},
              {'sequence': 2, 'kind': 'global_observation',
               'payload': {'status': 'terminal', 'complete': True, 'source_ordinal': 1}}]
    events_raw = b''.join(json_bytes(e) for e in events)
    (tmp_path / 'events.jsonl').write_bytes(events_raw)
    events_ref = {'path': 'events.jsonl', 'sha256': hashlib.sha256(events_raw).hexdigest(),
                  'bytes': len(events_raw)}
    value = SimpleNamespace(directory=tmp_path, evidence_manifest={'files': [ref, events_ref]})
    outcome = {'terminal_observation': {}}
    _global_terminal(outcome, value, events)
    assert outcome['terminal_observation']['result'] == 'loss'
    evaluation = {'evidence_verification': 'pass', 'run_id': 'run', 'evidence_content_id': 'a'*64,
                  'agent_task_outcome': {'evidence': {'events': events_ref}}}
    metrics = read_reference_metrics(tmp_path, evaluation,
                                     {'binding': {'runtime_instance_id': 'runtime'}})
    assert metrics['terminal_result'] == 'loss' and metrics['max_observed_floor'] == 12
    # Original source changed after the accepted events: never trust HP or a stale PASS.
    snapshot['interaction']['content']['context']['result'] = 'win'
    (tmp_path / 'source.bin').write_bytes(json_bytes(snapshot))
    with pytest.raises(BoundaryError, match='original_changed'):
        read_reference_metrics(tmp_path, evaluation,
                               {'binding': {'runtime_instance_id': 'runtime'}})


@pytest.mark.parametrize('speed', ['instant', 'fast'])
def test_checkpoint_multi_selection_reaches_original_prepare(tmp_path, speed):
    from test_reference_suite import Clock, OwnerClient

    from spireagent.workbench.reference_suite import SuiteLimits, run_suite
    manifest = checkpoint_reference_manifest('v0.111.0')
    path = tmp_path / 'manifest.json'
    path.write_bytes(json_bytes(manifest))
    directory = tmp_path / 'matrix'
    client = OwnerClient(directory, 'lost_prepare')
    clock = Clock()
    result = run_suite(path, hashlib.sha256(path.read_bytes()).hexdigest(), client=client,
        run_dir=directory, selection='', selections={'first': 'model-a', 'last': 'model-b'},
        run_profile='short', speed=speed, max_episodes=1, limits=SuiteLimits(2, 2, 2, 20, .01),
        clock=clock, sleep=clock.sleep)
    assert client.resource['preparation']['speed'] == speed
    assert result['speed'] == speed
    assert client.calls.count('readiness') == 2
    assert 'prepare' in client.calls
    assert result['world_offers'] == 1
    assert result['planned'] == 20
    assert result['episodes'][0]['seed'] == result['episodes'][1]['seed']
    assert [r['selection_id'] for r in result['episodes'][:2]] == ['model-a', 'model-b']


def test_completed_original_is_retained_without_reoffer_and_unknown_rejected(tmp_path):
    from spireagent.workbench.reference_suite import _Artifacts, _completed_rows
    def ref(name, value):
        raw = json_bytes(value)
        (tmp_path / name).write_bytes(raw)
        return {'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    row = {'seed': 'seed', 'selection_id': 'model', 'status': 'finished',
           'world_mutation_offered': True, 'cleanup_accepted': True, 'stored_evidence_id': 'a'*64,
           'run_id': 'original-run', 'finish': ref('finish.json', {'phase': 'closed'}),
           'preservation': ref('preserve.json', {'status': 'completed', 'run_id': 'original-run',
                'artifact_id': 'a'*64, 'peer_readback': {'typed_verification': 'pass'}})}
    report = {'schema': 'spireagent/reference-suite-report-v1', 'manifest': {'sha256': 'b'*64},
              'run_profile': 'continuous', 'speed': 'instant', 'display_mode': 'headless',
              'evidence_peer': None, 'episodes': [row]}
    original = tmp_path / 'report.json'
    original.write_bytes(json_bytes(report))
    def load(name):
        return _completed_rows((original,), 'b'*64, profile='continuous', display='headless',
            speed='instant', peer=None, selected={'last': 'model'},
            artifacts=_Artifacts(tmp_path/name))
    assert load('one')[('seed', 'model')]['original_row']['run_id'] == 'original-run'
    with pytest.raises(BoundaryError, match='previous_attempt_configuration_changed'):
        _completed_rows((original,), 'b'*64, profile='continuous', display='headless',
            speed='fast', peer=None, selected={'last': 'model'},
            artifacts=_Artifacts(tmp_path/'different-speed'))
    report['episodes'][0]['status'] = 'unknown'
    original.write_bytes(json_bytes(report))
    with pytest.raises(BoundaryError, match='requires_reconciliation'):
        load('two')


def test_fixed_ref20_keeps_original_instant_precondition(tmp_path):
    from test_reference_suite import OwnerClient, suite_manifest

    from spireagent.workbench.reference_suite import SuiteLimits, run_suite
    path, sha = suite_manifest(tmp_path)
    client = OwnerClient(tmp_path/'attempt')
    with pytest.raises(BoundaryError, match='fixed_suite_requires_instant_speed'):
        run_suite(path, sha, client=client, run_dir=tmp_path/'attempt',
            selection='model', run_profile='continuous', speed='fast',
            limits=SuiteLimits(2, 2, 2, 20))
    assert not client.calls
