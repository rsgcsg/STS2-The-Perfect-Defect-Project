"""Fast-chain side of `segments.combat_end_kappa` (knob, learner.json, preflight, readout)."""
from __future__ import annotations

import copy
import time

import pytest
from test_friday_fastchain import (
    FASTCHAIN,
    RECIPE_A,
    RECIPE_A_FROZEN,
    TWO_CELL,
    by_name,
    good_facts,
    launch,
    learner_config,
    long_run_site,
    preflight,
    probe_row,
    readout,
    recipe_a_gate,
    run_profile,
)

RUN = {'run_name': 'r', 'peer_dir': '-', 'start_package': '-', 'wall_seconds': 1,
       'device': 'cuda', 'generation': '-', 'warmup_artifact_id': None,
       'rendezvous_path': '-', 'ranks': 1, 'resume_checkpoint': None}


def second_training():
    """Recipe A + combat_end_kappa 0.5 (knob) + c_hp 0.001 (frozen recipe): the trade-off
    experiment, built here only; its profile and frozen JSON are the lead's to write."""
    k = {**run_profile.values(run_profile.load_profile(RECIPE_A)), 'combat_end_kappa': 0.5}
    frozen = copy.deepcopy(RECIPE_A_FROZEN)
    frozen['segments']['combat_end_kappa'] = 0.5
    frozen['loss']['hp_advantage_coef'] = 0.001
    return k, frozen


def test_the_knob_is_off_in_every_profile_and_bounded():
    for profile in FASTCHAIN.glob('rl5-*.toml'):
        assert run_profile.values(run_profile.load_profile(profile))['combat_end_kappa'] == 1.0
    check = run_profile.KNOBS['combat_end_kappa']
    assert check(0) is None and check(0.5) is None and check(1.0) is None
    assert check(1.5) is not None and check(-0.1) is not None and check('0.5') is not None
    # off: learner.json without the key, no source feature asked for
    k = run_profile.values(run_profile.load_profile(TWO_CELL))
    assert 'combat_end_kappa' not in preflight.required_features({'knobs': k})


def test_second_training_passes_preflight_with_c_hp_from_the_frozen_recipe(tmp_path):
    k, frozen = second_training()
    assert run_profile.cross_problems(k) == []
    site = long_run_site(tmp_path)
    chain = {'id': 'c-b', 'deadline_at': time.time() + k['long_wall_seconds'], 'generation': 0,
             'total_seconds': 28800, 'train_target_seconds': 28800,
             'train_left_seconds': 28800, 'seed_start': 0}
    plan = launch.build_plan('rb', k, site, cards=1, card_type='A100-40', layout_spec=None,
                             env_cores=None, smoke=False, probe_rows=[probe_row()],
                             long_run=True, chain=chain)
    gate = recipe_a_gate()
    # the shared gate carries c_hp 0.003: A9 refuses the 0.001 recipe against it
    facts = {**good_facts(plan), 'frozen_recipe': frozen, 'gate_learner_base': gate}
    result = by_name(preflight.checks(plan, facts))['frozen_recipe']
    assert not result.ok and 'loss.hp_advantage_coef' in result.detail, result.detail
    # a private gate copy without the key takes it from the frozen recipe: everything passes
    del gate['loss']['hp_advantage_coef']
    facts['gate_learner_base'] = gate
    results = by_name(preflight.checks(plan, facts))
    assert all(r.ok for r in results.values()), preflight.report(list(results.values()))
    assert 'c_hp 0.001' in results['hp_score'].detail
    assert 'combat_end_kappa' in preflight.required_features(plan, frozen)
    assert 'combat_end_kappa' in launch.FEATURES
    learner, filled = learner_config.learner_base(gate, frozen, k, RUN)
    assert filled['hp_advantage_coef'] == 0.001
    assert learner['segments']['combat_end_kappa'] == 0.5
    assert learner_config.recipe_drift(learner, frozen) == []
    # recipe A's own frozen recipe (k = 1) differs from this learner.json in exactly the key
    assert learner_config.recipe_drift(learner, RECIPE_A_FROZEN) == [
        "loss.hp_advantage_coef: frozen 0.003, effective 0.001"]


def test_kappa_needs_game_value_targets_and_the_gate_cannot_hold_it():
    k, frozen = second_training()
    gate = recipe_a_gate()
    gate['segments']['value_target'] = 'segment'
    with pytest.raises(ValueError, match='value_target'):
        learner_config.learner_base(gate, frozen, k, RUN)
    gate = recipe_a_gate()
    gate['segments']['combat_end_kappa'] = 0.5
    with pytest.raises(ValueError, match='knob owns it'):
        learner_config.learner_base(gate, frozen, {**k, 'combat_end_kappa': 1.0}, RUN)


def test_readout_combat_settle_lines():
    learning = readout._Learning()
    # two datasets: [combat rows, settled, lost, sum G, sq G, sum G', sq G']
    for values in ([100, 60, 10, 1.0, 9.0, 0.5, 4.0], [100, 40, 0, -1.0, 7.0, 0.5, 2.0]):
        learning.add(readout._slim({'t': 1, 'kind': 'progress', 'stepped': True,
                                    'combat_settle': values}))
    got = learning.result()['combat_settle']
    assert got['combat_rows'] == 200 and got['settled_rows'] == 100
    assert got['settled_share'] == 0.5 and got['lost_rows'] == 10
    assert got['std_game'] == pytest.approx((16.0 / 200) ** 0.5)
    assert got['std_settled'] == pytest.approx((6.0 / 200 - (1.0 / 200) ** 2) ** 0.5)
    lines = readout.render_record({'learning': {'latest': learning.result(),
                                                'run_mean': None}})
    assert any('combat-end settlement' in line and '100 of 200 combat rows settled (50.0%)'
               in line for line in lines), lines
    assert readout._Learning().result()['combat_settle'] is None


def test_readout_counts_battles_by_how_they_ended():
    intake = {'held_rows': 0, 'spill_bytes': 0, 'spill_files': 0, 'spill_peak_bytes': 0,
              'game_target_settle_end_noncombat_battles': 90,
              'game_target_settle_end_floor_battles': 7,
              'game_target_settle_lost_battles': 3, 'game_target_settle_lost_floor_battles': 0}
    lines = readout.render_record({'value_target_intake': {'intake': intake}})
    assert any('settled at a noncombat row 90 / at a floor change 7, fell back to G 3 '
               '(end not reached) / 0 (floor missing)' in line for line in lines), lines
