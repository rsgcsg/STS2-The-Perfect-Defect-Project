"""release_swap.py decision logic and its dry-run orchestration; no Friday, game or GPU.

Fixture states are shaped like the 11-07 RL4.2 continuations: c12c (unknown stop just
after the window-1 deadline), c12d (window 1 closed by absolute_deadline), c13 (window 2
stopped by a watcher failure) and c13b (window 2 running).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_friday_watch import FRIDAY, module
from test_friday_watch import watch as original_watch_fixture

W1_DEADLINE = 1793998135.2920434  # window 1 deadline_at (11-07 06:48:55 AEST)
W2_DEADLINE = 1794159609.0  # holder 1441467 end - 1800
NOW_C13 = 1794024000.0  # 11-07 ~14:00, while c13 was stopped
COMPLETED = frozenset({'run_budget_complete', 'absolute_deadline'})


@pytest.fixture
def swap():
    return module('release_swap', FRIDAY / 'release_swap.py')


@pytest.fixture
def watch():
    return original_watch_fixture.__wrapped__()


def state(stop, *, active=None, seconds=0.0, attempts=1, **extra):
    return {'stop': stop, 'active': active, 'pending': None, 'training_seconds': seconds,
            'attempts': [{'generation': f'g-ops-{n:04d}', 'root': f'/nowhere/attempt-{n:04d}',
                          'terminal_accounting': {'terminal': True}}
                         for n in range(1, attempts + 1)], **extra}


def config(budget, deadline, **extra):
    return {'run': 'rl42-p5-long-20261106-20261105', 'peer': {'kind': 'local', 'directory': 's'},
            'budget_id': budget, 'deadline_at': deadline, 'training_seconds': 72000, **extra}


C12D = (state('absolute_deadline', seconds=55364.0, attempts=14),
        config('rl42-p5-long-20261106-20261105', W1_DEADLINE))
C13 = (state('watcher_failure_requires_reconciliation', seconds=7003.0),
       config('rl42-p5-long-20261107-window2', W2_DEADLINE))
C13B_RUNNING = (state(None, active=0, seconds=7509.0),
                config('rl42-p5-long-20261107-window2', W2_DEADLINE))
C12C = (state('training_unknown_fraction_above_limit', seconds=55364.0, attempts=13),
        config('rl42-p5-long-20261106-20261105', W1_DEADLINE))


def decide(swap, fixture, now, margin=1800, **kwargs):
    return swap.decide(*fixture, now=now, margin=margin, completed_stops=COMPLETED, **kwargs)


def test_decision_follows_the_c12c_c12d_c13_c13b_shapes(swap, watch):
    assert watch.COMPLETED_WINDOW_STOPS == COMPLETED  # the rule the tool is handed
    assert decide(swap, C12D, W1_DEADLINE + 3600)['mode'] == 'new_window'
    same = decide(swap, C13, NOW_C13)
    assert same['mode'] == 'same_window'
    assert same['launch_by'] == W2_DEADLINE - 600 - 1800
    # c12c just after the deadline: a same-window watch only closes the window (c12d).
    closing = decide(swap, C12C, W1_DEADLINE + 60)
    assert closing['mode'] == 'close_window' and 'deadline_at - 600' in closing['why']
    # ... and inside the 600 s + margin before it, too (the 06:38-06:48 trap).
    assert decide(swap, C12C, W1_DEADLINE - 1200)['mode'] == 'close_window'
    used = (state('learner_failed', seconds=71000.0), C13[1])
    assert decide(swap, used, NOW_C13)['mode'] == 'close_window'
    with pytest.raises(swap.SwapError, match='predecessor_watch_not_stopped'):
        decide(swap, C13B_RUNNING, NOW_C13)
    # The real c13 state still names attempt-0001 as active (the watcher crashed).
    crashed = ({**C13[0], 'active': 0}, C13[1])
    with pytest.raises(swap.SwapError, match='predecessor_watch_still_active'):
        decide(swap, crashed, NOW_C13, active_terminal=False)
    assert swap.decide(*crashed, now=NOW_C13, margin=1800, completed_stops=COMPLETED,
                       active_terminal=True)['mode'] == 'same_window'


def test_new_window_fields_apply_the_deadline_and_total_training_rule(swap):
    prior = C12D[1]
    fields = swap.new_window_fields(prior, [55364.0], now=NOW_C13, margin=1800,
                                    deadline_at=W2_DEADLINE, budget_id=None,
                                    training_seconds=None, total_training_seconds=129600)
    assert fields['training_seconds'] == 72000  # min(72000, 36 h - 55364 s), as c13
    assert fields['budget_id'] == 'rl42-p5-long-20261106-20261105-window2'
    assert swap.next_budget_id('rl42-p5-long-20261107-window2') == (
        'rl42-p5-long-20261107-window3')
    third = swap.new_window_fields(C13[1], [55364.0, 70000.0], now=NOW_C13, margin=1800,
                                   deadline_at=W2_DEADLINE, budget_id='w3',
                                   training_seconds=None, total_training_seconds=129600)
    assert third['training_seconds'] == 4236 and third['budget_id'] == 'w3'
    for kwargs, code in (
            ({'deadline_at': NOW_C13 + 2000}, 'new_window_deadline_too_close'),
            ({'deadline_at': None}, 'new_window_deadline_required'),
            ({'total_training_seconds': None}, 'new_window_training_seconds_required'),
            ({'total_training_seconds': 55500}, 'total_training_target_reached'),
            ({'budget_id': prior['budget_id']}, 'new_window_budget_id_must_differ')):
        arguments = {'deadline_at': W2_DEADLINE, 'budget_id': None, 'training_seconds': None,
                     'total_training_seconds': 129600, **kwargs}
        with pytest.raises(swap.SwapError, match=code):
            swap.new_window_fields(prior, [55364.0], now=NOW_C13, margin=1800, **arguments)


def pin(path, value):
    path.write_text(json.dumps(value))
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def test_chain_seconds_walk_every_linked_window(swap, tmp_path):
    read = original_watch_fixture.__wrapped__().evaluate.read_pinned
    w1_config = pin(tmp_path / 'c12d-config.json', C12D[1])
    w1 = pin(tmp_path / 'c12d-state.json', {**C12D[0], 'configuration': w1_config})
    w2_config = pin(tmp_path / 'c13b-config.json', {**C13[1], 'previous_window_state': w1})
    w2 = {**C13[0], 'training_seconds': 70000.0, 'configuration': w2_config}
    assert swap.chain_training_seconds(w2, read) == [55364.0, 70000.0]


def test_checkpoint_pick_is_the_predecessor_watchers_own_rule(swap, watch, tmp_path,
                                                             monkeypatch):
    archive = {'kind': 'local', 'directory': str(tmp_path / 'archive')}
    prior = {**C13[1], 'peer': {**C13[1]['peer'], 'archive': archive},
             'learner_config': str(tmp_path / 'learner.json')}
    records = [{'checkpoint_artifact_id': 'cp-6787', 'published_at': 100.0},
               {'checkpoint_artifact_id': 'cp-7755', 'published_at': 300.0},
               {'checkpoint_artifact_id': 'cp-7700', 'published_at': 200.0}]
    monkeypatch.setattr(watch, 'Exchange', lambda peer, run: SimpleNamespace(
        checkpoint_records=lambda: records if peer == archive else []))
    from stpd.rl import learner
    monkeypatch.setattr(learner.LearnerConfig, 'read',
                        staticmethod(lambda path: SimpleNamespace(resume_checkpoint='cp-6787')))
    budget = {'id': prior['budget_id'], 'total_seconds': 72000, 'consumed_seconds': 7003}
    monkeypatch.setattr(watch.SegmentWatcher, 'cold_checkpoint', lambda self, identity: {
        'checkpoint_artifact_id': identity, 'operating_budget': budget, 'at': 12345.6})
    cp, notes = swap.predecessor_checkpoint(watch, prior, C13[0], tmp_path / 'work')
    assert cp == {'checkpoint_artifact_id': 'cp-7755', 'operating_budget': budget}
    assert notes == []
    # A linked window with no CP of its own yet re-enters from its pinned parent.
    other = {'id': 'window-1', 'total_seconds': 72000, 'consumed_seconds': 55364}
    monkeypatch.setattr(watch.SegmentWatcher, 'cold_checkpoint', lambda self, identity: {
        'checkpoint_artifact_id': identity, 'operating_budget': other})
    linked = {**prior, 'previous_window_state': {'path': 'p', 'sha256': 'x'}}
    cp, notes = swap.predecessor_checkpoint(watch, linked, C13[0], tmp_path / 'work2')
    assert cp['checkpoint_artifact_id'] == 'cp-6787'
    assert notes[0]['kind'] == 'window_first_checkpoint_pending'


def test_checkpoint_link_and_continuation_rules_are_the_watchers(swap, watch, tmp_path):
    cp = {'checkpoint_artifact_id': 'cp', 'deadline_at': W2_DEADLINE,
          'operating_budget': {'id': C13[1]['budget_id'], 'total_seconds': 72000,
                               'consumed_seconds': 7003}}
    assert watch.checkpoint_link(cp, C13[1], has_history=True) is False
    with pytest.raises(ValueError, match='checkpoint_deadline_cannot_be_reset'):
        watch.checkpoint_link({**cp, 'deadline_at': W1_DEADLINE}, C13[1], has_history=True)
    with pytest.raises(ValueError, match='original_watch_state_required'):
        watch.checkpoint_link(cp, C13[1], has_history=False)
    # New window: the c12d -> c13 link.
    w1_config = pin(tmp_path / 'c12d-config.json', C12D[1])
    w1 = pin(tmp_path / 'c12d-state.json', {**C12D[0], 'configuration': w1_config})
    window = {**C13[1], 'previous_window_state': w1}
    parent = {'checkpoint_artifact_id': 'cdfdff3b', 'deadline_at': W1_DEADLINE,
              'training_generation': 'g-ops-0014',
              'operating_budget': {'id': C12D[1]['budget_id'], 'total_seconds': 72000,
                                   'consumed_seconds': 55364}}
    assert watch.checkpoint_link(parent, window, has_history=True) is True
    with pytest.raises(ValueError, match='does_not_belong'):
        watch.checkpoint_link({**parent, 'training_generation': 'g-other'}, window,
                              has_history=True)
    with pytest.raises(ValueError, match='previous_watch_run_peer_and_budget'):
        watch.require_watch_continuation({'config_sha256': 'other'}, C12D[1], C12D[1])
    watch.require_watch_continuation(
        {'config_sha256': hashlib.sha256(watch.json_bytes(C13[1])).hexdigest()},
        C13[1], {**C13[1], 'execution': 'new'})


def identities(adapter='aab8860d', revision='0752ea89', compiler='4f8b8fbe', spec='v2'):
    return {'agent_adapter': {'id': 'stpd-p1-mstd-agent', 'code_sha256': adapter},
            'producer': {'source_revision': revision, 'uv_lock_sha256': 'eb79'},
            'compiler': {'compiler_sha256': compiler, 'input_spec': {'id': spec},
                         'vocabulary_sha256': 'vocab'}}


def old_files(**kwargs):
    new = identities(**kwargs)
    return ({'agent_adapter': new['agent_adapter'], 'producer': new['producer'],
             'python_executable': '/actor/python', 'host_pin': {'version': '1.1.0-rc.64'}},
            {'segments': {'input_compiler_identity': new['compiler']}, 'resume_checkpoint': 'a'})


def test_identity_table_names_each_change_and_refuses_a_model_input_change(swap):
    # c12/c12b: 4acc0dea changed only workers/checkpoint_codec.py bytes vs af57b882.
    old_execution, old_learner = old_files(adapter='0ld', revision='af57b882',
                                           compiler='f13e9b8d')
    rows = swap.identity_rows(old_execution, old_learner, identities(revision='4acc0dea'),
                              ['python/stpd/workers/checkpoint_codec.py'])
    changed = {r['identity']: r['reason'] for r in rows if r['changed']}
    assert set(changed) == {'execution.agent_adapter', 'execution.producer',
                            'learner.segments.input_compiler_identity.compiler_sha256'}
    assert 'checkpoint_codec.py' in changed['execution.agent_adapter']
    assert 'af57b882 -> 4acc0dea' in changed['execution.producer']
    # c13b: only the producer revision moved.
    old_execution, old_learner = old_files(revision='795622dc')
    rows = swap.identity_rows(old_execution, old_learner, identities(), [])
    assert [r['identity'] for r in rows if r['changed']] == ['execution.producer']
    assert 'unchanged' in swap.render_identities(rows)
    with pytest.raises(swap.SwapError, match='model_input_changed'):
        swap.identity_rows(old_execution, old_learner, identities(spec='v3'), [])


def test_watch_configs_for_same_new_and_close_window(swap, tmp_path):
    prior = {**C13[1], 'execution': 'old-exe', 'runtime_source': '/r/old/source',
             'learner_config': 'old-learner', 'learner_python': '/r/old/env-gpu/bin/python',
             'held_job_id': '1441467', 'exclude_nodes': ['smp-9-8'],
             'previous_window_state': {'path': 'c12d', 'sha256': 'x'},
             'acknowledged_integrity_events': {'k': 'v'},
             'evaluation': {'execution': 'old-exe', 'initial_checkpoint': 'cdfdff3b'}}
    ref = {'path': 'c13/watch-state.json', 'sha256': 'y'}
    common = {'previous_ref': ref, 'release': Path('/r/new'), 'execution': Path('/o/exe.json'),
              'learner': Path('/o/learner.json'), 'cp_id': '8f88caeb', 'held_job': '1441467',
              'exclude': ['smp-9-12'], 'auto_excluded': ['smp-9-2'], 'acknowledgements': {}}
    same = swap.build_watch('same_window', prior, window=None, **common)
    assert same['previous_watch_state'] == ref
    assert same['previous_window_state'] == prior['previous_window_state']  # #391
    assert same['exclude_nodes'] == ['smp-9-12', 'smp-9-8']  # state keeps auto-excluded
    assert same['evaluation'] == {'execution': '/o/exe.json', 'initial_checkpoint': '8f88caeb'}
    assert (same['runtime_source'], same['learner_python']) == (
        '/r/new/source', '/r/new/env-gpu/bin/python')
    assert {k: same[k] for k in ('budget_id', 'deadline_at', 'training_seconds')} == {
        k: prior[k] for k in ('budget_id', 'deadline_at', 'training_seconds')}
    window = {'budget_id': 'w3', 'deadline_at': 1.0e10, 'training_seconds': 4236}
    new = swap.build_watch('new_window', {**prior, 'previous_watch_state': {'p': 1}},
                           window=window, **common)
    assert 'previous_watch_state' not in new and new['previous_window_state'] == ref
    assert 'acknowledged_integrity_events' not in new  # they name the old budget's events
    assert new['exclude_nodes'] == ['smp-9-12', 'smp-9-2', 'smp-9-8']
    assert (new['budget_id'], new['training_seconds']) == ('w3', 4236)
    close = swap.build_watch('close_window', prior, window=None, **common)
    assert close == {**prior, 'previous_watch_state': ref}


def test_dead_attempt_without_final_wal_needs_reconciliation(swap):
    previous = state('learner_failed', attempts=2)
    previous['attempts'][0].update(actor_plans=['p'], socket_events_closed=True)
    previous['attempts'][1].update(actor_plans=['p'])
    assert swap.attempts_needing_reconciliation(previous, True, lambda path: False) == [2]
    assert swap.attempts_needing_reconciliation(previous, True, lambda path: True) == []
    assert swap.attempts_needing_reconciliation(previous, False, lambda path: False) == []


# ------------------------------------------------------------- dry-run orchestration

@pytest.fixture
def friday(swap, watch, tmp_path, monkeypatch):
    """A c13-shaped predecessor and fakes for every Friday side effect."""
    long_run = tmp_path / 'long'
    old = tmp_path / 'releases/rl42-p5-795622dc'
    release = tmp_path / 'releases/rl42-p5-0752ea89'
    old_execution, old_learner = old_files(revision='795622dc')
    (long_run / 'watch-c13').mkdir(parents=True)
    pin(long_run / 'execution-795622dc.json', old_execution)
    pin(long_run / 'learner-c13.json', old_learner)
    prior = {**C13[1], 'mode': 'segments', 'execution': str(long_run / 'execution-795622dc.json'),
             'learner_config': str(long_run / 'learner-c13.json'),
             'runtime_source': str(old / 'source'), 'held_job_id': '1441467',
             'learner_python': str(old / 'env-gpu/bin/python'), 'exclude_nodes': ['smp-9-8']}
    configuration = pin(long_run / 'watch-c13/watch-config.json', prior)
    pin(long_run / 'watch-c13/watch-state.json', {
        **C13[0], 'configuration': configuration,
        'config_sha256': hashlib.sha256(watch.json_bytes(prior)).hexdigest()})
    calls = {'children': 0}

    def child(root, registration, python, rel, adapter, compiler):
        calls['children'] += 1
        root.mkdir(parents=True, exist_ok=True)
        bad = compiler['compiler_sha256'] != '4f8b8fbe'
        return {'rc': 1 if bad else calls.get('good_rc', 0), 'stderr_bytes': 9 if bad else 0,
                'code': 'RL_producer_compiler_identity_differs' if bad else None}

    monkeypatch.setattr(swap, 'holder_end', lambda job: ('RUNNING', W2_DEADLINE + 1800))
    monkeypatch.setattr(swap, 'find_registration', lambda *a: tmp_path / 'registration')
    monkeypatch.setattr(swap, 'freeze_identities', lambda *a: identities())
    monkeypatch.setattr(swap, 'git', lambda *a, **k: '' if 'diff' in a else 'f' * 40)
    monkeypatch.setattr(swap, 'child_check', child)
    budget = {'id': C13[1]['budget_id'], 'total_seconds': 72000, 'consumed_seconds': 7003}
    monkeypatch.setattr(swap, 'predecessor_checkpoint', lambda *a: (
        {'checkpoint_artifact_id': '8f88caeb', 'updates': 7755, 'deadline_at': W2_DEADLINE,
         'training_generation': 'g-ops-0001', 'operating_budget': budget,
         'producer': {'source_revision': '795622dc'}}, []))
    monkeypatch.setattr(watch, 'read_segment_config', lambda path: json.loads(path.read_bytes()))
    monkeypatch.setattr(watch, 'read_compatible_fixes', lambda: [])
    monkeypatch.setattr(swap.time, 'time', lambda: NOW_C13)
    monkeypatch.setitem(sys.modules, 'watch', watch)
    monkeypatch.setitem(sys.modules, 'evaluate', watch.evaluate)
    monkeypatch.setattr(sys, 'path', list(sys.path))

    def prepare(*extra):
        args = swap.parser().parse_args(
            ['prepare', '--dry-run', '--predecessor', str(long_run / 'watch-c13'),
             '--name', 'c13b', '--release-dir', str(release), *extra, '--phase-b',
             '--resolved-release', str(release), '--resolved-sha', '0752ea89' * 5,
             '--resolved-env', str(tmp_path / 'ENV-0752ea89.sh')])
        swap.phase_b(args, swap.Steps())
    return SimpleNamespace(prepare=prepare, long_run=long_run, calls=calls, release=release)


def test_dry_run_prepares_the_c13b_continuation_and_is_idempotent(friday, capsys):
    friday.prepare('--expect-mode', 'same_window')
    final = friday.long_run / 'swap-c13b-dry-run'
    ready = json.loads((final / 'READY.json').read_bytes())
    assert set(ready['files']) == set(['decision.json', 'identities.json', 'checkpoint.json',
                                       'execution.json', 'learner.json', 'watch.json'])
    cfg = json.loads((final / 'watch.json').read_bytes())
    assert cfg['execution'] == str(final / 'execution.json')
    assert cfg['previous_watch_state']['path'].endswith('watch-c13/watch-state.json')
    assert cfg['runtime_source'] == str(friday.release / 'source')
    learner = json.loads((final / 'learner.json').read_bytes())
    assert learner['resume_checkpoint'] == '8f88caeb'
    execution = json.loads((final / 'execution.json').read_bytes())
    assert execution['producer']['source_revision'] == '0752ea89'
    out = capsys.readouterr().out
    assert 'DRY RUN' in out and 'real_game: skipped (dry run)' in out
    assert 'nohup python tools/friday/watch.py' in out and '<url>' not in out
    assert friday.calls['children'] == 2  # new identity + negative control
    assert not list(friday.long_run.glob('.swap-*'))  # no staging or FAILED left behind
    before = {p.name: p.read_bytes() for p in final.iterdir()}
    friday.prepare()
    assert {p.name: p.read_bytes() for p in final.iterdir()} == before
    assert 'unchanged: same files and hashes' in capsys.readouterr().out
    assert friday.calls['children'] == 2  # nothing re-verified
    with pytest.raises(Exception, match='prepared_output_differs'):
        friday.prepare('--exclude-node', 'smp-9-99')


def test_a_failing_verification_leaves_no_valid_looking_output(friday, swap):
    friday.calls['good_rc'] = 1
    with pytest.raises(swap.SwapError, match='agent_child_refused_new_identity'):
        friday.prepare()
    assert not (friday.long_run / 'swap-c13b-dry-run').exists()
    failed = list(friday.long_run.glob('.swap-c13b-dry-run.FAILED-*'))
    assert len(failed) == 1 and not (failed[0] / 'READY.json').exists()


def test_typed_failure_line_never_prints_an_endpoint(swap, capsys, monkeypatch):
    def boom(args, steps):
        steps('2 freeze', 'x')
        raise ValueError('bad peer https://secret.r2.example/bucket')
    monkeypatch.setattr(swap, 'phase_a', boom)
    assert swap.main(['prepare', '--predecessor', '.', '--name', 'c']) == 3
    err = capsys.readouterr().err
    assert 'FAILED at 2 freeze: ValueError' in err and 'secret' not in err


def test_registration_search_skips_stripped_models_and_walks_linked_windows(swap, tmp_path):
    read = original_watch_fixture.__wrapped__().evaluate.read_pinned

    def registration(attempt, *, model):
        reg = (tmp_path / attempt / 'actors/actors/a-00/evidence/actor/ledgers/000001/ledger'
               / 'attempts/000001/private/registration')
        reg.mkdir(parents=True)
        (reg / 'config.json').write_text(json.dumps({'package_path': '/tmp/x/model.tensor-tree'}))
        if model:
            (reg / 'model.tensor-tree').write_bytes(b'm')
        return reg

    registration('w2-attempt', model=False)  # c13: newer retention dropped the model
    kept = registration('w1-attempt', model=True)  # c12b attempt-0013 kept it
    w1_config = pin(tmp_path / 'w1-config.json', C12D[1])
    w1 = pin(tmp_path / 'w1-state.json', {'attempts': [{'root': str(tmp_path / 'w1-attempt')}],
                                          'configuration': w1_config})
    w2_config = pin(tmp_path / 'w2-config.json', {**C13[1], 'previous_window_state': w1})
    w2 = {'attempts': [{'root': str(tmp_path / 'w2-attempt')}], 'configuration': w2_config}
    assert swap.find_registration(w2, read) == kept
    with pytest.raises(swap.SwapError, match='no_actor_registration_with_model_found'):
        swap.find_registration({**w2, 'configuration': pin(tmp_path / 'c.json', C13[1])}, read)
