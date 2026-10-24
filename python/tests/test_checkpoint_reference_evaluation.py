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
