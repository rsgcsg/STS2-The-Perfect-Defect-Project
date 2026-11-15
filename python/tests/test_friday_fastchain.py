"""Fast-chain launcher: layout -> placement, run profile, preflight decisions, readout."""
from __future__ import annotations

import copy
import importlib
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip('fcntl', reason='Friday tools target POSIX Slurm')
FASTCHAIN = Path(__file__).resolve().parents[2] / 'tools/friday/fastchain'
PROFILE = FASTCHAIN / 'rl5-fastchain.toml'
SITE = FASTCHAIN / 'site.example.toml'


def load_fastchain():
    """The launcher's modules import each other by plain name (as launch.py does from its
    own directory). Load them as one set, then take the plain names back out of
    sys.modules and sys.path so they cannot shadow same-named modules of other tests."""
    names = ('run_profile', 'learner_config', 'layout', 'preflight', 'launch', 'readout',
             'cp_retention', 'supervise')
    saved_path = list(sys.path)
    saved = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    sys.path.insert(0, str(FASTCHAIN))
    try:
        loaded = [importlib.import_module(name) for name in names]
    finally:
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
        sys.path[:] = saved_path
    return loaded


run_profile, learner_config, layout, preflight, launch, readout, cp_retention, supervise = \
    load_fastchain()


def probe_row(node='gpu-a', gpus=3, free=3, cores=128, mem_g=500):
    return {'node': node, 'state': 'mixed', 'gpus': gpus, 'gpus_free': free,
            'cores_idle': cores, 'mem_free_g': mem_g}


def knobs(**changes):
    values = run_profile.values(run_profile.load_profile(PROFILE))
    values.update(changes)
    return values


def plan_for(cards=3, layout_spec=None, smoke=False, probe=None, env_cores=None,
             probe_only=True, long_run=False, **changes):
    return launch.build_plan('t1', knobs(**changes), run_profile.load_site(SITE), cards=cards,
                             card_type='A100-40', layout_spec=layout_spec, env_cores=env_cores,
                             smoke=smoke, probe_rows=probe or [probe_row()],
                             probe_only=probe_only, long_run=long_run)


FROZEN = json.loads((Path(__file__).resolve().parents[1] / 'configs/rl5.json').read_text())


def gate_base():
    """A gate learner-base.json as the cluster has it: the frozen recipe, levers off."""
    base = copy.deepcopy(FROZEN)
    for key in ('prepared_compile', 'prepared_kernels', 'prepared_bucket_rows'):
        base['performance'].pop(key)
    base.update({'max_versions': 100000, 'peer': {'kind': 'archive'}})
    base['segments']['warmup_artifact_id'] = 'f' * 64
    return base


def with_hp(config, coef, scope='combat_ticket'):
    """A learner config (gate base or frozen recipe) with c_hp = `coef` and its scope."""
    config = copy.deepcopy(config)
    config['loss']['hp_advantage_coef'] = coef
    config['loss']['hp_advantage_scope'] = scope
    return config


def gate_without_hp(*keys, config=None):
    """A gate learner-base.json (or `config`) from before the HP score (without c_hp and
    its scope)."""
    base = copy.deepcopy(config) if config is not None else gate_base()
    for key in keys or ('hp_advantage_coef', 'hp_advantage_scope'):
        del base['loss'][key]
    return base


def good_facts(plan):
    k = plan['knobs']
    return {'src_rev': f'{k["env_pkg_build"]}{"0" * 32}',
            'src_features': {name: True for name in launch.FEATURES},
            'src_checkout': {'git': True, 'head': 'c' * 40, 'dirty': False},
            'frozen_recipe': FROZEN, 'gate_learner_base': gate_base(),
            'compile_warmup_dumps': {p: True for p in plan['site']['compile_warmup_dumps']},
            'warmup_artifact': {'id': 'f' * 64, 'present': True, 'package_sha256': 'e' * 64},
            'start_model': {'package_sha256': 'e' * 64, 'training_identity': None},
            'env_pkg_rev': k['env_pkg_build'] + 'cdbd192d (int tip)',
            'gpu_test_start_seconds': 0.0,
            'queue': {'fairshare': 0.5, 'priority_estimate': 15000, 'partitions': {
                'gpu': {'name': 'GPU-PARTITION', 'pending': 3, 'above': 0},
                'prep': {'name': 'CPU-PARTITION', 'pending': 2, 'above': 0}}},
            'env_candidate_sha256': k['env_candidate_artifact_sha256'] + '0' * 48,
            'secret_files': {'/x/auth.hex': 0o600, '/x/prep-auth.hex': 0o600}}


def by_name(results):
    return {r.name: r for r in results}


# ---------------- layout ----------------

def test_three_current_layouts_place_roles_on_the_planned_cards():
    one = layout.place('shared', 1, 'A100-40', service_cores=40, rank_cores=8)
    assert (one.service_card, one.train_cards, one.learner_visible, one.learner_device) == \
        (0, (0,), (0,), 'cuda')
    assert one.shared_inference_card and one.name == '1卡 · 推理训练共用 · A100-40'
    two = layout.place('1i+1t', 2, 'A100-40', service_cores=40, rank_cores=8)
    # single rank sees only its own card (c2b refused "cuda:1"; c2a landed on card 0)
    assert (two.service_card, two.learner_visible, two.learner_device, two.ranks) == \
        (0, (1,), 'cuda', 1)
    assert not two.shared_inference_card and two.gpu_cores == 48
    three = layout.place('1i+2t', 3, 'A100-80', service_cores=40, rank_cores=8)
    assert (three.train_cards, three.learner_visible, three.learner_device, three.ranks) == \
        ((1, 2), (0, 1, 2), 'cuda:1', 2)
    assert three.name == '3卡 · 1推理+2训练 · A100-80' and three.gpu_cores == 56
    explicit = layout.place('inference,train,train', 3, 'A100-40', service_cores=40,
                            rank_cores=8)
    assert explicit.learner_device == 'cuda:1'


@pytest.mark.parametrize('spec,cards,message', [
    ('shared', 2, "is one card"),
    ('1i+1t', 3, 'names 2 cards'),
    ('2i+1t', 3, 'exactly one inference'),
    ('inference', 1, 'no training card'),
    ('train,inference,train', 3, 'contiguous'),
    ('inference+train,train', 2, 'data-parallel rank on the inference card'),
    ('gpu,train', 2, 'each card is a'),
])
def test_invalid_layouts_are_refused(spec, cards, message):
    with pytest.raises(layout.LayoutError, match=message):
        layout.place(spec, cards, 'A100-40', service_cores=40, rank_cores=8)


def test_targets_attach_to_card_count_and_perfect_packing():
    assert layout.target(1, {'1': 14_000_000, '3': 30_000_000}, {})['target_rows_h'] == 14_000_000
    two = layout.target(2, {'1': 14_000_000, '3': 30_000_000}, {})
    assert two['target_rows_h'] is None and 'loss table' in two['note']
    assert round(layout.perfect_packing(3, 42_500_000, 18_500_000)) == 38_668_033


# ---------------- run profile ----------------

def test_repository_profile_and_example_site_validate():
    profile = run_profile.load_profile(PROFILE)
    assert set(profile) == set(run_profile.KNOBS)
    assert all(k['why'].strip() and k['measured'].strip() for k in profile.values())
    site = run_profile.load_site(SITE)
    assert set(site['gpu_types']) == {'A100-40', 'A100-80'}


def test_site_refuses_every_top_level_key_outside_site(tmp_path):
    """A bare [eval_baselines] (or any other top-level key) was silently ignored."""
    baselines = (f'a0 = {{ plan = "/b/a0/plan.json", sha256 = "{"a" * 64}" }}\n'
                 f'a10 = {{ plan = "/b/a10/plan.json", sha256 = "{"b" * 64}" }}\n')
    site = tmp_path / 'site.toml'
    site.write_text(SITE.read_text() + '\n[site.eval_baselines]\n' + baselines)
    assert set(run_profile.load_site(site)['eval_baselines']) == {'a0', 'a10'}
    site.write_text(SITE.read_text() + '\n[eval_baselines]\n' + baselines)
    with pytest.raises(run_profile.ProfileError,
                       match=r'eval_baselines: top-level .*write it as \[site.eval_baselines\]'):
        run_profile.load_site(site)
    site.write_text('note = "x"\n' + SITE.read_text())
    with pytest.raises(run_profile.ProfileError, match='note: top-level key outside'):
        run_profile.load_site(site)


def test_gc_refreeze_threshold_is_a_knob_that_needs_gc_freeze():
    """F: the profile lowers the re-freeze threshold (online recompiles); the code
    default 200000 is the earlier constant and needs no source feature."""
    profile = run_profile.load_profile(PROFILE)
    assert profile['sgd_gc_refreeze_objects']['default'] == 200_000
    assert profile['sgd_gc_refreeze_objects']['value'] == 40_000
    assert preflight.required_features(plan_for(cards=3))['gc_refreeze']
    assert 'gc_refreeze' not in preflight.required_features(
        plan_for(cards=3, sgd_gc_refreeze_objects=200_000))
    with pytest.raises(run_profile.ProfileError, match='needs sgd_gc_freeze'):
        run_profile.load_profile(PROFILE, ['sgd_gc_freeze=false'])
    assert run_profile.values(run_profile.load_profile(PROFILE, [
        'sgd_gc_freeze=false', 'sgd_gc_refreeze_objects=200000']))['sgd_gc_freeze'] is False
    with pytest.raises(run_profile.ProfileError, match='sgd_gc_refreeze_objects'):
        run_profile.load_profile(PROFILE, ['sgd_gc_refreeze_objects=0'])


def test_overrides_are_validated_and_recorded():
    profile = run_profile.load_profile(PROFILE, ['services=4'])
    assert profile['services']['value'] == 4 and profile['services']['overridden_from'] == 6
    with pytest.raises(run_profile.ProfileError, match='unknown knob'):
        run_profile.load_profile(PROFILE, ['no_such=1'])
    with pytest.raises(run_profile.ProfileError, match='--set services'):
        run_profile.load_profile(PROFILE, ['services=many'])


def test_profile_refuses_unknown_missing_and_undocumented_knobs(tmp_path):
    text = PROFILE.read_text()
    bad = tmp_path / 'p.toml'
    bad.write_text(text + '\n[knobs.extra]\nvalue = 1\ndefault = 1\nwhy = "x"\nmeasured = "x"\n')
    with pytest.raises(run_profile.ProfileError, match='extra: unknown knob'):
        run_profile.load_profile(bad)
    line = next(x for x in text.splitlines() if x.startswith('services = '))
    bad.write_text(text.replace(line + '\n', ''))
    with pytest.raises(run_profile.ProfileError, match='services: missing'):
        run_profile.load_profile(bad)
    why = line[line.index('why = '):line.index(', measured')]
    bad.write_text(text.replace(line, line.replace(why, 'why = ""')))
    with pytest.raises(run_profile.ProfileError, match='services: why'):
        run_profile.load_profile(bad)
    bad.write_text(text.replace(line, line.replace('value = 6', 'value = "six"')))
    with pytest.raises(run_profile.ProfileError, match='services.value'):
        run_profile.load_profile(bad)


def test_env_lines_quote_and_encode():
    lines = run_profile.env_lines({'levers': True, 'service_kwargs': {'a': 1}, 'run': 'x y'})
    assert "export FC_LEVERS=1" in lines
    assert "export FC_SERVICE_KWARGS='{\"a\":1}'" in lines
    assert "export FC_RUN='x y'" in lines


# ---------------- launcher plan ----------------

def test_parse_probe_and_test_only():
    text = ('gpu-a mixed gpu:a100:3(S:0-1) gpu:a100:1(IDX:0) 20/108/0/128 515000 115000\n'
            'gpu-a mixed gpu:a100:3(S:0-1) gpu:a100:1(IDX:0) 20/108/0/128 515000 115000\n'
            'gpu-b drained gpu:a100:2 gpu:a100:0 0/64/0/64 250000 0\n')
    rows = launch.parse_probe(text, 'a100')
    assert rows[0] == {'node': 'gpu-a', 'state': 'mixed', 'gpus': 3, 'gpus_free': 2,
                       'cores_idle': 108, 'mem_free_g': 390}
    assert len(rows) == 2 and rows[1]['gpus_free'] == 0
    import time
    now = time.mktime(time.strptime('2026-11-13T10:00:00', '%Y-%m-%dT%H:%M:%S'))
    msg = 'sbatch: Job 1 to start at 2026-11-13T10:05:00 using 8 processors'
    assert launch.parse_test_only(msg, now) == 300
    assert launch.parse_test_only('sbatch: error: no', now) is None


def test_env_nodes_spread_evenly_and_slow_nodes_count_half():
    assert launch.balanced_env_nodes(19, 2, 6) == 18
    assert launch.balanced_env_nodes(19, 3, 6) == 18
    assert launch.balanced_env_nodes(5, 4, 6) == 3
    assert launch.split_slow(10, 0) == (10, 0)
    assert launch.split_slow(10, 4) == (8, 4)


def test_build_plan_derives_memory_guard_from_job_limit_and_falls_back():
    for cards in (1, 2, 3):
        plan = plan_for(cards=cards)
        limit = plan['job_mem_g'] * preflight.GIB
        assert plan['mem_guard_bytes'] == int(0.85 * limit)
        assert plan['mem_hard_bytes'] == int(0.95 * limit)
        assert plan['mem_guard_bytes'] < plan['mem_hard_bytes'] < limit
        # Non-reclaimable bound (learner budget) leaves >= 20% of the job below the guard.
        assert preflight.memory_estimate(plan)['counted'] <= 0.8 * plan['mem_guard_bytes']
    two = plan_for(cards=2)
    smaller = plan_for(cards=2, job_mem_g={'1': 160, '2': 100, '3': 240})
    assert smaller['mem_guard_bytes'] == int(0.85 * 100 * preflight.GIB)
    assert not preflight.check_memory(plan_for(cards=2, mem_guard_fraction=0.96)).ok
    assert two['placement']['learner_device'] == 'cuda'
    assert (two['env_nodes_max'] * two['knobs']['env_aggregators']) % two['knobs']['services'] == 0
    none = plan_for(cards=3, probe=[probe_row(free=1)])
    assert none['gpu_nodes_free_now'] == [] and none['placement']['cards'] == 3
    fell = launch.build_plan('t1', knobs(), run_profile.load_site(SITE), cards=3,
                             card_type='A100-40', layout_spec=None, env_cores=None, smoke=False,
                             probe_rows=[probe_row(free=1)], fallback=True)
    assert fell['placement']['cards'] == 1 and fell['name'].startswith('1卡')
    smoke = plan_for(cards=3, smoke=True)
    assert smoke['wall_seconds'] == smoke['knobs']['smoke_wall_seconds']
    k = smoke['knobs']
    assert smoke['env_nodes_max'] == k['services'] // math.gcd(k['env_aggregators'],
                                                                k['services'])


def test_commands_and_job_env_carry_layout_without_secrets():
    plan = plan_for(cards=3)
    cmds = launch.commands(plan)
    assert '--gres=gpu:a100:3' in cmds['gpu']
    assert any(c == f'--export=ALL,FC_RUN={plan["run"]}' for c in cmds['gpu'])
    # The card type is partition + gres + constraint, never a node pinned from sinfo.
    assert '--constraint=A100-40-NODE-FEATURE' in cmds['gpu']
    assert not any(c.startswith('--nodelist') for c in cmds['gpu'])
    # One plain job per prep group (never heterogeneous), GPU first, env last.
    assert list(cmds) == ['gpu', 'prep-0', 'prep-1', 'env']
    for i, group in enumerate(plan['knobs']['prep_groups']):
        prep = cmds[f'prep-{i}']
        assert ':' not in prep and f'--constraint={group["type"]}' in prep
        assert prep[-2] == f'--export=ALL,FC_RUN={plan["run"]},FC_PREP_GROUP={i}'
        assert prep[-1].endswith('/prep.sh') and f'--nodes={group["nodes"]}' in prep
    env = launch.job_env(plan)
    assert 'export FC_LEARNER_DEVICE=cuda:1' in env and 'export FC_RANKS=2' in env
    assert 'STPD_' not in env


# ---------------- preflight (login side) ----------------

def test_preflight_passes_a_consistent_plan():
    plan = plan_for(cards=3, wall_seconds=2400)
    results = preflight.checks(plan, good_facts(plan))
    assert all(r.ok for r in results), preflight.report(results)
    assert preflight.report(results).endswith('preflight: ok')


@pytest.mark.parametrize('change,facts_change,failing', [
    ({'levers': False}, {}, 'speed_switches'),
    ({'remote_prepare_codec': 'none'}, {}, 'speed_switches'),
    ({'env_machine_types': ['fc430', 'c6220']}, {}, 'machine_types'),
    ({'env_memo': {}}, {}, 'env_package'),
    ({}, {'env_pkg_rev': 'deadbeef built'}, 'env_package'),
    ({}, {'env_candidate_sha256': 'ffff'}, 'env_package'),
    ({}, {'src_features': {'shards': False}}, 'shards'),
    ({'env_candidate_artifact_sha256': ''}, {}, 'env_package'),
    ({'env_candidate_artifact_sha256': 'b77f5139'}, {}, 'env_package'),
    ({}, {'src_checkout': {'git': False}}, 'producer_identity'),
    ({}, {'src_checkout': {'git': True, 'head': 'c' * 40, 'dirty': True}}, 'producer_identity'),
    ({'max_versions': 100000}, {}, 'frozen_recipe'),
    ({}, {'gate_learner_base': None}, 'frozen_recipe'),
    # a key the gate has stays the gate's: c_hp other than the frozen value is drift
    ({}, {'gate_learner_base': with_hp(gate_base(), 0.003)}, 'frozen_recipe'),
    # neither the gate nor the frozen config (a source before #538) has c_hp and its scope
    ({}, {'gate_learner_base': gate_without_hp(),
          'frozen_recipe': gate_without_hp(config=FROZEN)}, 'hp_score'),
    # c_hp > 0 agreed by gate and frozen recipe, but segment format 3
    ({}, {'gate_learner_base': with_hp(gate_base(), 0.003),
          'frozen_recipe': with_hp(FROZEN, 0.003)}, 'hp_score'),
    ({}, {'frozen_recipe': {
        **FROZEN, 'lr': 0.0002}}, 'frozen_recipe'),
    ({}, {'compile_warmup_dumps': {'/x/missing.pt': False}}, 'compile_warmup'),
    ({}, {'compile_warmup_dumps': {}}, 'compile_warmup'),
    ({}, {'warmup_artifact': {'id': 'f' * 64, 'present': False}}, 'warmup_artifact'),
    ({'prepared_batches': 40}, {}, 'prepared_batch_cap'),  # V4 smoke-h1c
    ({'service_cores': 4}, {}, 'service_cpus'),
    ({'remote_prepare_connections': 35}, {}, 'shards'),
    ({}, {'src_rev': 'feedface' * 5}, 'env_package'),
    ({}, {'secret_files': {'/x/auth.hex': 0o644}}, 'secret_files'),
    ({}, {'secret_files': {'/x/auth.hex': None}}, 'secret_files'),
])
def test_preflight_refuses_each_failure(change, facts_change, failing):
    plan = plan_for(cards=3, wall_seconds=2400, **change)
    facts = {**good_facts(plan), **facts_change}
    results = by_name(preflight.checks(plan, facts))
    assert not results[failing].ok
    assert 'REFUSED' in preflight.report(list(results.values()))


def test_preflight_refuses_bad_site_and_plan_edits():
    plan = plan_for(cards=3)
    shared = copy.deepcopy(plan)
    shared['site']['gpu_local_tmp'] = '/scratch/x'
    assert not preflight.check_hot_path(shared).ok
    prep = copy.deepcopy(plan)
    prep['site']['prep_scratch'] = {'r640': '/data/x'}
    assert not preflight.check_prep_scratch(prep).ok  # fc430 missing, r640 shared
    overlap = copy.deepcopy(plan)
    overlap['env_node_index_ranges'] = {'fast': [0, 10], 'c8220': [5, 4]}
    assert not preflight.check_identity(overlap).ok
    guard = copy.deepcopy(plan)
    guard['mem_guard_bytes'] = guard['job_mem_g'] * preflight.GIB + 1
    assert not preflight.check_memory(guard).ok
    cores = copy.deepcopy(plan)
    cores['env_cores_max'] = cores['core_cap']
    assert not preflight.check_cores(cores).ok
    uneven = copy.deepcopy(plan)
    uneven['env_nodes_max'] += 1
    assert not preflight.check_shards(uneven, good_facts(plan)).ok
    nonode = plan_for(cards=3, probe=[probe_row(free=0)])
    request = preflight.check_gpu_request(nonode, good_facts(nonode))
    assert request.ok and request.warn and 'will pend' in request.detail  # printed only


def test_prepared_batch_cap_is_printed_and_keeps_its_headroom():
    plan = plan_for(cards=1)
    result = preflight.check_prepared_batch_cap(plan)
    assert result.ok and 'per-batch cap 1.249 GB' in result.detail  # 38.4 GiB / 33
    tight = preflight.check_prepared_batch_cap(plan_for(cards=1, prepared_batches=40))
    assert not tight.ok and 'per-batch cap 1.006 GB' in tight.detail
    assert not preflight.check_prepared_batch_cap(
        plan_for(cards=1, prepared_batch_expected_max_gb=1.1)).ok


def test_service_cpus_never_short_and_sharing_is_a_warning():
    for workers in range(1, 17):
        for cores in range(workers, 65):
            cpus = list(range(cores))
            for index in range(8):
                assert len(layout.service_cpus(cpus, workers, index)) == workers
    distinct = preflight.check_service_cpus(plan_for(cards=1, service_cores=48))
    assert distinct.ok and not distinct.warn
    for cores in (14, 16, 30):  # h1b, h1c, the profile value
        shared = preflight.check_service_cpus(plan_for(cards=1, service_cores=cores))
        assert shared.ok and shared.warn and 'of 6 services share CPUs' in shared.detail
    assert not preflight.check_service_cpus(plan_for(cards=1, service_cores=7)).ok


def test_input_cause_names_the_innermost_error():
    h1c = {'operation': 'dataplane_shard', 'shard': 0, 'failure': {
        'operation': 'prepare', 'code': 'INVALID_PACKET', 'producer_error_type': 'RuntimeError',
        'error': 'binary_remote_assembly_failed: ValueError: '
                 'prepared_batch_exceeds_current_plus_next_byte_budget'}}
    assert learner_config.input_cause(h1c) == (
        'dataplane_shard 0 / prepare INVALID_PACKET: binary_remote_assembly_failed: '
        'ValueError: prepared_batch_exceeds_current_plus_next_byte_budget')
    assert learner_config.input_cause({'operation': 'source_prefetch', 'code': 'INVALID_PACKET',
                                       'error_type': 'OSError'}) == \
        'source_prefetch INVALID_PACKET: OSError'
    assert learner_config.input_cause(None) is None


def test_old_build_keeps_the_two_worker_workaround_only():
    """Without #523 a437612d the SGD budget counts idle local pools: gate workers refuse,
    the 2/2 workaround (and no scratch flag) passes; with the commit the gate values pass."""
    plan = plan_for(cards=3, wall_seconds=2400)
    old = {**good_facts(plan), 'src_features': {
        name: name != 'budget_counts_remote' for name in launch.FEATURES}}
    assert plan['knobs']['preparation_workers'] == 12
    assert preflight.check_features(plan, old).warn  # owner 11-14: a warning only
    workaround = plan_for(cards=3, wall_seconds=2400, preparation_workers=2,
                          assembly_workers=2, prep_scratch_min_free=False)
    assert not preflight.check_features(workaround, old).warn
    assert preflight.check_features(plan, good_facts(plan)).ok


def test_zero_or_too_few_env_nodes_refuse():
    plan = plan_for(cards=3, wall_seconds=2400, env_cores=20)  # < one balanced step
    assert plan['env_nodes_max'] == 0
    shards = preflight.check_shards(plan, good_facts(plan))
    assert not shards.ok and 'env nodes' in shards.detail
    assert 'env' not in launch.commands(plan)


def test_placeholder_producer_needs_smoke_or_probe_only():
    fake = {'producer_identity': 'placeholder'}
    plan = plan_for(cards=3, wall_seconds=2400, probe_only=False, **fake)
    assert plan['checkpoints_void'] and not plan['probe_only']
    assert not preflight.check_producer(plan, good_facts(plan)).ok
    assert preflight.check_producer(plan_for(cards=3, smoke=True, probe_only=False, **fake),
                                    good_facts(plan)).ok
    assert preflight.check_producer(plan_for(cards=3, probe_only=True, **fake),
                                    good_facts(plan)).ok
    real = plan_for(cards=3, probe_only=False)  # the profile default: checkout
    assert not real['checkpoints_void'] and real['producer_identity'] == 'checkout'
    assert preflight.check_producer(real, good_facts(real)).ok


def test_table_knobs_and_nan_are_validated():
    with pytest.raises(run_profile.ProfileError, match='prepared_gib'):
        run_profile.load_profile(PROFILE, ['prepared_gib=nan'])
    with pytest.raises(run_profile.ProfileError, match='service_kwargs'):
        run_profile.load_profile(PROFILE, ['service_kwargs={ no_such = 1 }'])
    with pytest.raises(run_profile.ProfileError, match='env_memo'):
        run_profile.load_profile(PROFILE, ['env_memo={ projection = "x" }'])
    with pytest.raises(run_profile.ProfileError, match='sgd_switch_interval_seconds'):
        run_profile.load_profile(PROFILE, ['sgd_switch_interval_seconds=1.0'])
    check = run_profile.SITE_KEYS['partition_max_minutes']
    assert check({'debug': 60}) is None and check({'debug': '60'}) is not None
    assert run_profile.SITE_KEYS['prep_scratch']({'r640': 1}) is not None
    twice = run_profile.load_profile(PROFILE, ['services=4', 'services=3'])
    assert twice['services']['value'] == 3 and twice['services']['overridden_from'] == 6


def scancel_log(failing=()):
    """A run_scancel for submit/cancel_all: records each call; ids in `failing` fail."""
    calls = []

    def run(ids):
        calls.append(list(ids))
        bad = bool(set(ids) & set(failing))
        return subprocess.CompletedProcess(ids, 1 if bad else 0, '',
                                           'scancel: error: Socket timed out' if bad else '')
    return calls, run


def test_submit_puts_the_gpu_job_first_and_cancels_everything_on_a_later_failure(tmp_path):
    plan = plan_for(cards=3, wall_seconds=2400)
    cmds = launch.commands(plan)
    sent, waited = [], []
    cancelled, run_scancel = scancel_log()
    ids = iter(['11', '12', '13', '14'])

    def run_cmd(cmd):
        sent.append(cmd)
        return next(ids)

    def wait(job, limit, run):
        beat = run / 'launcher-alive'  # touched before the GPU submit, kept fresh meanwhile
        waited.append((job, limit, beat.exists() and time.time() - beat.stat().st_mtime < 60))

    jobs = launch.submit(tmp_path, plan, cmds, run_cmd=run_cmd, wait=wait,
                         run_scancel=run_scancel)
    assert jobs == {'gpu': '11', 'prep-0': '12', 'prep-1': '13', 'env': '14'}
    assert sent[0] == cmds['gpu'] and (tmp_path / 'gpu-job.txt').read_text() == '11\n'
    for cmd, name in zip(sent[1:], ['prep-0', 'prep-1', 'env'], strict=True):  # after GPU
        assert cmd == [*cmds[name][:-1], '--dependency=after:11', cmds[name][-1]]
    assert waited == [('11', plan['gpu_pending_max_seconds'], True)] and not cancelled
    assert json.loads((tmp_path / 'jobs.json').read_text()) == jobs

    ids = iter(['21', '22', '23'])

    def env_fails(cmd):
        if cmd[-1].endswith('env.sh'):
            raise launch.subprocess.CalledProcessError(1, cmd)
        return next(ids)

    with pytest.raises(launch.subprocess.CalledProcessError):
        launch.submit(tmp_path, plan, cmds, run_cmd=env_fails, wait=lambda *a: None,
                      run_scancel=run_scancel)
    # Dependents first, the GPU job last (after: holds once the GPU job is cancelled).
    assert cancelled == [['22', '23'], ['21']]
    assert "cancelled ['22', '23', '21']" in (tmp_path / 'submit-failed.txt').read_text()

    cancelled.clear()

    def interrupted(*args):
        raise KeyboardInterrupt

    ids = iter(['31', '32', '33', '34'])
    with pytest.raises(KeyboardInterrupt):
        launch.submit(tmp_path, plan, cmds, run_cmd=lambda cmd: next(ids), wait=interrupted,
                      run_scancel=run_scancel)
    assert cancelled == [['32', '33', '34'], ['31']]


@pytest.mark.parametrize('sig', ['SIGTERM', 'SIGHUP'])
def test_submit_cancels_on_sigterm_and_sighup_and_restores_the_handlers(tmp_path, sig):
    plan = plan_for(cards=3, wall_seconds=2400)
    cmds = launch.commands(plan)
    cancelled, run_scancel = scancel_log()
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    ids = iter(['51', '52', '53', '54'])

    def killed_while_pending(job, limit, run):
        os.kill(os.getpid(), getattr(signal, sig))
        time.sleep(5)  # the handler raises before this returns

    with pytest.raises(SystemExit, match=f'launcher got {sig}'):
        launch.submit(tmp_path, plan, cmds, run_cmd=lambda cmd: next(ids),
                      wait=killed_while_pending, run_scancel=run_scancel)
    assert cancelled == [['52', '53', '54'], ['51']]
    assert {s: signal.getsignal(s) for s in before} == before


def test_a_failed_scancel_says_which_jobs_may_still_be_alive(tmp_path):
    plan = plan_for(cards=3, wall_seconds=2400)
    cancelled, run_scancel = scancel_log(failing={'61'})
    assert launch.cancel_all({'gpu': '61', 'env': '62'}, run_scancel=run_scancel) == (
        "SCANCEL FAILED (scancel 61: rc 1 scancel: error: Socket timed out), jobs may "
        "still be alive: ['62', '61']")
    assert cancelled == [['62'], ['61']]
    ids = iter(['61', '62', '63', '64'])
    with pytest.raises(SystemExit):
        launch.submit(tmp_path, plan, launch.commands(plan), run_cmd=lambda cmd: next(ids),
                      wait=lambda *a: (_ for _ in ()).throw(SystemExit('bound')),
                      run_scancel=run_scancel)
    text = (tmp_path / 'submit-failed.txt').read_text()
    assert 'SCANCEL FAILED' in text and 'cancelled' not in text


PENDING = ('x|20000|None|GPU-PARTITION\n11|10167|Priority|GPU-PARTITION\n'
           'y|10300|Priority|GPU-PARTITION\nz|9000|Priority|GPU-PARTITION\n'
           'w|50000|Priority|CPU-PARTITION\n')


def test_gpu_start_waits_for_gpu_sh_and_never_reads_a_dead_job_as_started(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(launch, 'pending_jobs', lambda: launch.parse_pending(PENDING))
    # None = squeue failed once (read again); RUNNING without gpu.sh's confirmation waits.
    states = iter(['PENDING', None, 'PENDING', 'CONFIGURING', 'RUNNING', 'confirm'])

    def state(job):
        s = next(states)
        if s == 'confirm':
            (tmp_path / 'gpu-started').write_text('11 now\n')
            return 'RUNNING'
        return s
    monkeypatch.setattr(launch, 'gpu_state', state)
    launch.wait_gpu_start('11', 3600, tmp_path, poll=0)
    assert next(states, 'all read') == 'all read'
    assert ('Reason=Priority, priority 10167, 2 of 4 pending jobs in GPU-PARTITION rank '
            'above') in capsys.readouterr().out
    (tmp_path / 'gpu-started').unlink()
    for dead in ('', 'CANCELLED', 'FAILED', 'TIMEOUT', 'NODE_FAIL', 'BOOT_FAIL',
                 'OUT_OF_MEMORY', 'COMPLETED', 'COMPLETING'):
        monkeypatch.setattr(launch, 'gpu_state', lambda job, s=dead: s)
        with pytest.raises(SystemExit, match='ended before gpu.sh started the run'):
            launch.wait_gpu_start('11', 3600, tmp_path, poll=0)
    (tmp_path / 'STOP').write_text('launcher gone before GPU start: heartbeat 400 s old\n')
    monkeypatch.setattr(launch, 'gpu_state', lambda job: 'COMPLETED')
    with pytest.raises(SystemExit, match='COMPLETED; STOP: launcher gone'):
        launch.wait_gpu_start('11', 3600, tmp_path, poll=0)
    (tmp_path / 'gpu-started').write_text('11 now\n')  # it started, then ended: started
    launch.wait_gpu_start('11', 3600, tmp_path, poll=0)


def test_gpu_pending_bound_reports_the_queue_and_never_resubmits(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, 'pending_jobs', lambda: launch.parse_pending(PENDING))
    monkeypatch.setattr(launch, 'gpu_state', lambda job: 'PENDING')
    with pytest.raises(SystemExit, match='still PENDING after 0 s: job 11 PENDING, '
                       'Reason=Priority, priority 10167, 2 of 4 .*not resubmitted'):
        launch.wait_gpu_start('11', 0, tmp_path, poll=0)
    monkeypatch.setattr(launch, 'gpu_state', lambda job: None)
    with pytest.raises(SystemExit, match='squeue unreadable at the deadline'):
        launch.wait_gpu_start('11', 0, tmp_path, poll=0)
    # Through submit: the bound cancels every job of the run and nothing is sent again.
    plan = plan_for(cards=3, wall_seconds=2400, smoke=True)
    assert plan['gpu_pending_max_seconds'] == plan['knobs']['smoke_gpu_pending_max_seconds']
    assert plan_for(cards=3)['gpu_pending_max_seconds'] == \
        plan['knobs']['gpu_pending_max_seconds'] > plan['gpu_pending_max_seconds']
    sent = []
    cancelled, run_scancel = scancel_log()
    monkeypatch.setattr(launch, 'gpu_state', lambda job: 'PENDING')
    cmds = launch.commands(plan)
    ids = iter(str(n) for n in range(41, 50))
    with pytest.raises(SystemExit, match='still PENDING'):
        launch.submit(tmp_path, plan, cmds, run_cmd=lambda cmd: sent.append(cmd) or next(ids),
                      wait=lambda job, limit, run: launch.wait_gpu_start(job, 0, run, poll=0),
                      run_scancel=run_scancel)
    assert len(sent) == len(cmds)
    assert cancelled == [[str(42 + i) for i in range(len(cmds) - 1)], ['41']]
    assert 'still PENDING' in (tmp_path / 'submit-failed.txt').read_text()


def test_gpu_running_without_gpu_sh_confirmation_is_bounded(tmp_path, monkeypatch):
    """Review #540 S2: RUNNING for ack_limit s without gpu-started cancels the run loudly;
    a requeue starts that bound again; a started job is never read as still PENDING."""
    now = [0.0]

    def clock():
        return now[0]

    def answers(*seq):
        it = iter(seq)

        def state(job):
            now[0] += 100
            s = next(it)
            if s == 'confirm':
                (tmp_path / 'gpu-started').write_text('11 now\n')
                return 'RUNNING'
            return s
        return state, it
    monkeypatch.setattr(launch.time, 'monotonic', clock)
    monkeypatch.setattr(launch, 'pending_jobs', lambda: launch.parse_pending(PENDING))
    state, it = answers(*['RUNNING'] * 4, 'never read')
    monkeypatch.setattr(launch, 'gpu_state', state)
    (tmp_path / 'STOP').write_text('learner exited\n')
    with pytest.raises(SystemExit, match='RUNNING for 250 s but gpu.sh never confirmed its '
                       r'start \(gpu-started missing.*; STOP: learner exited\); see '):
        launch.wait_gpu_start('11', 10_000, tmp_path, poll=0, ack_limit=250)
    assert next(it) == 'never read'
    (tmp_path / 'STOP').unlink()
    # requeued after 200 s RUNNING: the bound counts from the next RUNNING (7 reads, not 4)
    state, it = answers('RUNNING', 'RUNNING', 'PENDING', *['RUNNING'] * 4, 'never read')
    monkeypatch.setattr(launch, 'gpu_state', state)
    with pytest.raises(SystemExit, match='RUNNING for 250 s'):
        launch.wait_gpu_start('11', 10_000, tmp_path, poll=0, ack_limit=250)
    assert next(it) == 'never read'
    # the pending bound has passed, then the job runs and squeue fails: still waiting
    state, it = answers('CONFIGURING', None, 'RUNNING', None, None, 'confirm')
    monkeypatch.setattr(launch, 'gpu_state', state)
    launch.wait_gpu_start('11', 0, tmp_path, poll=0, ack_limit=10_000)
    assert next(it, 'all read') == 'all read'
    (tmp_path / 'gpu-started').unlink()
    # through submit: every job of the run is cancelled, dependents first
    plan = plan_for(cards=3, wall_seconds=2400, smoke=True)
    cancelled, run_scancel = scancel_log()
    state, _ = answers(*['RUNNING'] * 4)  # the fake clock moves on each read
    monkeypatch.setattr(launch, 'gpu_state', state)
    cmds = launch.commands(plan)
    ids = iter(str(n) for n in range(41, 50))
    with pytest.raises(SystemExit, match='never confirmed its start'):
        launch.submit(tmp_path, plan, cmds, run_cmd=lambda cmd: next(ids),
                      wait=lambda job, limit, run: launch.wait_gpu_start(
                          job, limit, run, poll=0, ack_limit=250),
                      run_scancel=run_scancel)
    assert cancelled == [[str(42 + i) for i in range(len(cmds) - 1)], ['41']]
    assert 'never confirmed' in (tmp_path / 'submit-failed.txt').read_text()


def test_gpu_state_decides_on_stdout_only(monkeypatch):
    answers = {'noise': (0, 'RUNNING\n', 'slurm_receive_msg: Socket timed out, retrying\n'),
               'purged': (1, '', 'slurm_load_jobs error: Invalid job id specified\n'),
               'down': (1, '', 'slurm_load_jobs error: Unable to contact slurm controller\n'),
               'gone': (0, '', '')}
    monkeypatch.setattr(launch.subprocess, 'run', lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, *answers[cmd[3]]))
    assert [launch.gpu_state(k) for k in answers] == ['RUNNING', '', None, '']


def test_queue_facts_parse_slurm_output(monkeypatch):
    answers = {'sshare': '0.016700\n', 'sprio': 'Weights|10000|10000\n',
               'squeue': '1|20000|Priority|gpu\n2|10300|Priority|gpu\n3|10100|Resources|cpu\n'
                         '4|10200|Priority|cpu,gpu\n'}
    monkeypatch.setattr(launch, '_stdout', lambda cmd: answers[cmd[0]])
    assert launch.queue_facts({'gpu': 'gpu', 'prep': 'cpu'}) == {
        'fairshare': 0.0167, 'priority_estimate': 10167, 'partitions': {
            'gpu': {'name': 'gpu', 'pending': 3, 'above': 3},
            'prep': {'name': 'cpu', 'pending': 2, 'above': 1}}}
    answers['sprio'] = 'garbage\n'
    assert launch.queue_facts({'gpu': 'gpu'})['partitions']['gpu']['above'] is None
    monkeypatch.setattr(launch, '_stdout', lambda cmd: None)  # squeue/sshare failed
    assert launch.queue_facts({'gpu': 'gpu'}) == {
        'fairshare': None, 'priority_estimate': None,
        'partitions': {'gpu': {'name': 'gpu', 'pending': None, 'above': None}}}
    assert launch.queue_position('9', None) == 'job 9: squeue unreadable'
    assert launch.queue_position('9', []) == 'job 9 is not pending'
    assert launch.parse_fairshare('x\n') is None


def test_site_card_type_is_a_constraint_and_a_pin_only_when_set(tmp_path):
    text = SITE.read_text()
    site = run_profile.load_site(SITE)
    assert site['gpu_types']['A100-40']['constraint'] == 'A100-40-NODE-FEATURE'
    assert 'nodelist' not in site['gpu_types']['A100-40']
    pinned = tmp_path / 'site.toml'
    pinned.write_text(text.replace('# nodelist = "GPU-NODE-2"', 'nodelist = "GPU-NODE-2"'))
    plan = launch.build_plan('t1', knobs(), run_profile.load_site(pinned), cards=3,
                             card_type='A100-80', layout_spec=None, env_cores=None,
                             smoke=False, probe_rows=[probe_row()])
    assert '--nodelist=GPU-NODE-2' in launch.commands(plan)['gpu']
    old = tmp_path / 'old.toml'
    old.write_text(text.replace('constraint = "A100-40-NODE-FEATURE"\n', ''))
    with pytest.raises(run_profile.ProfileError, match='gpu_types.A100-40: missing constraint'):
        run_profile.load_site(old)
    # An empty constraint while A100-80 shares partition + gres: either card could come.
    twin = tmp_path / 'twin.toml'
    twin.write_text(text.replace('"A100-40-NODE-FEATURE"', '""'))
    with pytest.raises(run_profile.ProfileError, match='A100-40: empty constraint, but '
                       'A100-80 has the same partition and gres'):
        run_profile.load_site(twin)
    none = tmp_path / 'none.toml'
    none.write_text(text.replace('"A100-40-NODE-FEATURE"', '""').replace(
        'partition = "GPU-PARTITION"\nconstraint = "A100-80', 'partition = "GPU-80"\n'
        'constraint = "A100-80'))
    plan = launch.build_plan('t1', knobs(), run_profile.load_site(none), cards=3,
                             card_type='A100-40', layout_spec=None, env_cores=None,
                             smoke=False, probe_rows=[probe_row()])
    assert not any(c.startswith('--constraint') for c in launch.commands(plan)['gpu'])


def test_start_order_adds_the_prep_wait_to_the_gpu_prep_and_env_jobs():
    """GPU first, prep and env after it started: the learner's prep wait comes before its
    wall, the services and env nodes wait for the learner."""
    plan = plan_for(cards=3, wall_seconds=2400)
    k = plan['knobs']
    wait = k['remote_prepare_wait_seconds']
    assert plan['prep_wait_seconds'] == wait
    assert plan['gpu_minutes'] == (2400 + 240 + wait) // 60 + 1 == plan['prep_minutes']
    assert plan['env_wait_seconds'] == k['env_wait_seconds'] + wait
    assert plan['env_minutes'] == (k['env_wait_seconds'] + wait + 2400 + 600) // 60
    assert f'export FC_WAIT_N={(k["env_wait_seconds"] + wait) // 5}' in launch.job_env(plan)
    local = plan_for(cards=3, wall_seconds=2400, remote_prepare=False, preparation_workers=24,
                     assembly_workers=24)
    assert local['prep_wait_seconds'] == 0 and local['gpu_minutes'] == (2400 + 240) // 60 + 1
    assert not any(name.startswith('prep') for name in launch.commands(local))


FAKE_SQUEUE = """#!/bin/bash
n=$(( $(cat "$SQ_COUNT" 2>/dev/null || echo 0) + 1 )); echo $n > "$SQ_COUNT"
answer=$(sed -n "${n}p" "$SQ_SCRIPT"); [ -n "$answer" ] || answer=$(tail -1 "$SQ_SCRIPT")
case $answer in
  fail) echo "slurm_load_jobs error: Socket timed out on send/recv operation" >&2; exit 1 ;;
  stopfail) touch "$RUN/STOP"; exit 1 ;;
  purged) echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1 ;;
  noisy) echo "slurm_receive_msg: Socket timed out, retrying" >&2; echo RUNNING ;;
  empty) exit 0 ;;
  *) echo "$answer" ;;
esac
"""


def run_block(tmp_path, script, start, end, answers, env=(), prefix=''):
    """One marked block of a job script as it runs, with a scripted squeue and no sleep."""
    text = (FASTCHAIN / script).read_text()
    block = text[text.index(start):text.index(end)]
    run = tmp_path / 'run'
    run.mkdir(parents=True, exist_ok=True)
    (run / 'gpu-job.txt').write_text('77\n')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / 'squeue').write_text(FAKE_SQUEUE)
    (bin_dir / 'squeue').chmod(0o755)
    (tmp_path / 'answers').write_text('\n'.join(answers) + '\n')
    out = subprocess.run(
        ['bash', '-c', prefix + block.replace('sleep 15', 'true').replace('sleep 5', 'true')
         + 'echo "block done RC=${RC:-}"'],
        env={'PATH': f'{bin_dir}:/usr/bin:/bin', 'RUN': str(run), 'H': str(FASTCHAIN),
             'SQ_SCRIPT': str(tmp_path / 'answers'), 'SQ_COUNT': str(tmp_path / 'count'),
             **dict(env)},
        capture_output=True, text=True, timeout=60)
    count = tmp_path / 'count'
    return out, int(count.read_text()) if count.exists() else 0


def run_prep_serve(tmp_path, answers, beat_age=0):
    """The serve loop of prep.sh; load.txt (gpu.sh's heartbeat) is `beat_age` s old."""
    run = tmp_path / 'run'
    run.mkdir(parents=True)
    (run / 'load.txt').write_text('1 load\n')
    os.utime(run / 'load.txt', (time.time() - beat_age,) * 2)
    out, calls = run_block(tmp_path, 'prep.sh', '# --- serve:', '# --- end serve ---', answers,
                           env={'FC_PREP_GROUP': '0'},
                           prefix=f'source {FASTCHAIN}/gpu_job.sh; GPU=77\n')
    assert out.returncode == 0, out.stderr
    return out.stdout, calls


def test_prep_serve_loop_reads_squeue_stdout_only_and_falls_back_to_the_gpu_heartbeat(
        tmp_path):
    out, calls = run_prep_serve(tmp_path / 'a', ['RUNNING', 'fail', 'fail', 'RUNNING',
                                                 'COMPLETED'])
    assert calls == 5 and out.count('squeue cannot answer') == 2 and 'gone' in out
    assert out.strip().endswith('RC=0')
    out, calls = run_prep_serve(tmp_path / 'b', ['RUNNING', 'purged'])
    assert calls == 2 and 'Invalid job id' in out and out.strip().endswith('RC=0')
    out, calls = run_prep_serve(tmp_path / 'c', ['empty'])
    assert calls == 1 and 'gone: rc=0 out=[]' in out
    # stderr noise at rc 0 and PENDING/REQUEUED/SUSPENDED are not gone
    out, calls = run_prep_serve(tmp_path / 'd', ['noisy', 'PENDING', 'REQUEUED', 'SUSPENDED',
                                                 'noisy', 'empty'])
    assert calls == 6 and out.count('gone') == 1
    # a long slurmctld outage with a live GPU heartbeat keeps serving (until STOP)
    out, calls = run_prep_serve(tmp_path / 'e', ['fail'] * 60 + ['stopfail'])
    assert calls == 61 and out.strip().endswith('RC=0')
    assert not (tmp_path / 'e/run/prep-stopped-0').exists()
    # squeue cannot answer AND the heartbeat is stale: stop loudly, with a marker
    out, calls = run_prep_serve(tmp_path / 'f', ['RUNNING', 'fail'], beat_age=700)
    assert calls == 2 and out.strip().endswith('RC=4')
    assert 'prep group 0 stopped' in (tmp_path / 'f/run/prep-stopped-0').read_text()


def test_env_node_leaves_within_a_minute_when_the_gpu_job_is_gone(tmp_path):
    def join_wait(root, answers):
        out, calls = run_block(root, 'env.sh', '# --- join wait:', '# --- end join wait ---',
                               answers, env={'FC_WAIT_N': '30'}, prefix='have() { false; }\n')
        assert out.returncode == 0, out.stderr
        return out.stdout, calls

    out, calls = join_wait(tmp_path / 'a', ['CANCELLED'])  # a cancelled run's stray
    assert calls == 1 and 'GPU job gone before this node joined' in out
    assert 'block done' not in out
    out, calls = join_wait(tmp_path / 'b', ['RUNNING', 'noisy', 'empty'])  # every 12 x 5 s
    assert calls == 3 and 'gone before this node joined: rc=0 out=[]' in out
    out, calls = join_wait(tmp_path / 'c', ['fail'])  # unknown is not gone: the wait runs out
    assert calls == 3 and 'block done' in out


def test_gpu_sh_refuses_to_start_when_the_launcher_heartbeat_is_stale(tmp_path):
    """A stale (or missing) heartbeat is read once more after `sleep 30` (S1, NFS attribute
    cache) and refused only if still stale; `sleep` here records the call instead."""
    def check(root, age, during_sleep=''):
        run = root / 'run'
        run.mkdir(parents=True)
        if age is not None:
            (run / 'launcher-alive').touch()
            os.utime(run / 'launcher-alive', (time.time() - age,) * 2)
        out, _ = run_block(root, 'gpu.sh', 'stop_once() {', '# --- end launcher check ---',
                           [], env={'FC_LAUNCHER_STALE_SECONDS': '120'},
                           prefix=f'sleep() {{ echo "slept $1"; {during_sleep} :; }}\n')
        return out, run

    out, run = check(tmp_path / 'fresh', 5)
    assert out.returncode == 0 and 'block done' in out.stdout and 'slept' not in out.stdout
    assert 'launcher heartbeat 5 s old' in (run / 'gpu-started').read_text()
    assert not (run / 'STOP').exists()
    for name, age, said in (('stale', 300, 'heartbeat 30'), ('missing', None,
                                                              'heartbeat missing')):
        out, run = check(tmp_path / name, age)
        assert out.returncode == 3 and not (run / 'gpu-started').exists(), name
        assert 'reading it again in 30 s' in out.stdout and 'slept 30' in out.stdout, name
        assert (run / 'STOP').read_text().startswith(
            f'launcher gone before GPU start: {said}'), name
    # the second reading sees the launcher's latest touch (a cached mtime refreshed)
    out, run = check(tmp_path / 'cached', 300, 'touch "$RUN/launcher-alive";')
    assert out.returncode == 0 and 'slept 30' in out.stdout, out.stdout + out.stderr
    assert 'launcher heartbeat 0 s old' in (run / 'gpu-started').read_text()
    assert not (run / 'STOP').exists()


def test_prep_min_free_bytes_and_new_switches_reach_the_jobs():
    plan = plan_for(cards=3, wall_seconds=2400)
    env = launch.job_env(plan)
    assert 'export FC_PREP_MIN_FREE_BYTES=15000000000' in env
    assert plan['knobs']['prepared_kernels'].endswith('+residual')
    assert plan['knobs']['diagnostics_every'] == 512
    off = launch.job_env(plan_for(cards=3, prep_scratch_min_free=False))
    assert 'export FC_PREP_MIN_FREE_BYTES=0' in off


def test_time_limits_refuse_a_prep_job_longer_than_its_partition_allows():
    plan = plan_for(cards=3)
    assert plan['partition_max_minutes'] == {'gpu': 0, 'env': 0, 'prep': 0}
    assert preflight.check_time_limits(plan).ok
    capped = copy.deepcopy(plan)
    capped['partition_max_minutes']['prep'] = 60
    assert not preflight.check_time_limits(capped).ok  # 50 min wall + margins > 60
    unknown = copy.deepcopy(plan)
    unknown['partition_max_minutes']['env'] = None
    assert 'no entry' in preflight.check_time_limits(unknown).detail
    long = plan_for(cards=2, long_run=True)
    assert long['wall_seconds'] == 72000 and long['gpu_minutes'] > 1200
    assert preflight.check_time_limits(long).ok
    long['partition_max_minutes']['gpu'] = 48 * 60
    assert preflight.check_time_limits(long).ok  # 20 h fits a 48 h partition
    nojoin = plan_for(cards=2, env_min_join_seconds=3000)
    assert 'join window' in preflight.check_time_limits(nojoin).detail


def test_memory_budget_does_not_count_idle_local_pools():
    remote = plan_for(cards=3)
    local = plan_for(cards=3, remote_prepare=False, preparation_workers=24,
                     assembly_workers=24)
    est_remote, est_local = preflight.memory_estimate(remote), preflight.memory_estimate(local)
    assert est_remote['workers'] == 4 * remote['knobs']['shards']
    assert est_local['workers'] > est_remote['workers']
    assert preflight.check_memory(remote).ok


# ---------------- preflight (job side) ----------------

def test_job_side_pure_helpers():
    assert preflight.scratch_ok(31 * 10**9, 15)[0]
    assert not preflight.scratch_ok(29 * 10**9, 15)[0]
    assert preflight.local_fs('tmpfs') and preflight.local_fs('xfs')
    assert preflight.local_fs('ext2/ext3') and preflight.local_fs('btrfs')
    for other in ('nfs4', 'lustre', 'unknown', 'UNKNOWN (0x19830326)', 'beegfs', ''):
        assert not preflight.local_fs(other)


# ---------------- readout ----------------

def jsonl_text(records):
    """JSON lines as the roles write them: every line ends with a newline."""
    return ''.join(json.dumps(record) + '\n' for record in records)


def write_run(run, cards=3):
    plan = plan_for(cards=cards)
    run.mkdir()
    (run / 'plan.json').write_text(json.dumps(plan))
    t0 = 1000.0
    events = [{'t': t0, 'kind': 'training_started'}]
    events += [{'t': t0 + i, 'kind': 'progress', 'stepped': True, 'rows': 8000,
                'terminal_rows': 600, 'hp_rows': 400, 'seconds': 1.0} for i in range(1, 1300)]
    (run / 'learner-events.jsonl').write_text(jsonl_text(events))
    status = [{'t': t0 + i, 'queued_rows': 50_000, 'timing': {
        'preparation_wait_seconds': 0.1 * i, 'sgd_stage_wait_seconds': 0.05 * i,
        'optimizer_attempt_seconds': 0.8 * i},
        'fast_chain': {'learner': {'admitted_rows': 8100 * i, 'capacity_dropped_rows': 10 * i}}}
        for i in range(0, 1300, 10)]
    (run / 'learner-status.jsonl').write_text(jsonl_text(status))
    service = [{'t': t0 + i, 'handoff_q': 3, 'service': {'rows': 2000 * i,
                'device_seconds': 0.7 * i}, 'handoff': {'written': i, 'dropped': 0}}
               for i in range(0, 1300, 5)]
    (run / 'service-status-0.jsonl').write_text(jsonl_text(service))
    (run / 'gpu.csv').write_text('\n'.join(
        f'{int((t0 + i) * 1000)}, {c}, {90 if c else 70}, 30000'
        for i in range(0, 1300, 2) for c in range(cards)))
    return plan


def test_readout_edge_table_target_and_loss_table(tmp_path):
    run = tmp_path / 'run-t1'
    write_run(run)
    result = readout.build(run, 1200)
    assert result['name'] == '3卡 · 1推理+2训练 · A100-40'
    # incl. aux = td + terminal + hp (9,000/s); td only shown beside it
    assert result['training']['rows_h'] == pytest.approx(9000 * 3600, rel=0.01)
    assert result['training']['td_rows_h'] == pytest.approx(8000 * 3600, rel=0.01)
    assert result['training']['aux_share'] == pytest.approx(1000 / 9000, rel=0.01)
    assert result['verdict'] == 'meets target'  # 3,240万 > 3,000万 (td only would fail)
    assert result['training']['prepare_wait_pct'] == pytest.approx(10, abs=0.5)
    assert result['services'][0]['rows_s'] == pytest.approx(2000)
    assert result['gpu']['1']['busy_pct'] == 90
    table = result['loss_table']
    assert table[0]['cause'].startswith('ideal') and table[1]['cause'] == 'measured'
    text = readout.render(result)
    assert 'target: 3,000万' in text and 'loss table' in text and 'infer 0' in text
    assert 'incl. aux' in text and 'td only' in text


# Value sums (learning_diagnostics.FIELDS) of two rows: targets 0.2/0.4, predictions
# 0.25/0.35 -> EV 0.75, MSE 0.0025; HP: one row, error 0.02 -> MAE 2 HP points.
TD_SUMS = [2.0, 0.6, 0.2, 0.0, 0.005, 0.1]
HP_SUMS = [1.0, 0.5, 0.25, 0.02, 0.0004, 0.02]
NONE_SUMS = [0.0] * 6


def write_record_inputs(run):
    """Adds what the run record reads to a write_run directory: learner diagnostics,
    boundary sections, checkpoints, driver lines and the identity files."""
    t0 = 1000.0
    events = [json.loads(line) for line in
              (run / 'learner-events.jsonl').read_text().splitlines()]
    heads = {'td': TD_SUMS, 'terminal': NONE_SUMS, 'hp': HP_SUMS}
    for event in events:
        if event['kind'] != 'progress':
            continue
        i = round(event['t'] - t0)
        event.update(first_use=7000 * i, first_use_multi=3000 * i, entropy=1.5,
                     approx_kl_mu_theta=0.01, clip_fraction=0.1, value_statistics={
                         'all': heads, 'act1': heads, 'act2': {h: NONE_SUMS for h in heads},
                         'act3': {h: NONE_SUMS for h in heads},
                         'boss_floor': {h: NONE_SUMS for h in heads}})
        if i % 100 == 1:
            event['advantage_raw'] = {'multi_rows': 100, 'mean': 0.01, 'std': 0.2}
            event['sgd_timing'] = {'boundary_sections': {'gather': 0.5, 'assemble': 1.5},
                                   'boundary_sections_cpu': {'gather': 0.4},
                                   'boundary_seconds': 2.0}
    events += [{'t': t0 + 1300, 'kind': 'progress', 'stepped': False, 'kl_stopped': True},
               {'t': t0 + 1301, 'kind': 'checkpoint_published',
                'checkpoint_artifact_id': 'cp-1', 'sequence': 1},
               {'t': t0 + 1302, 'kind': 'checkpoint_published',
                'checkpoint_artifact_id': 'cp-2', 'sequence': 2},
               {'t': t0 + 1303, 'kind': 'fast_handoff_lost'}]
    (run / 'learner-events.jsonl').write_text(jsonl_text(events))
    (run / 'effective-profile.json').write_text(json.dumps({'knobs': {
        'replay_k': {'value': 4}, 'segment_format': {'value': 'stpd/rl-fast-segment-4',
                           'overridden_from': 'stpd/rl-fast-segment-3'},
        'ascension_mix': {'value': {'0': 0.5, '10': 0.5}},
        'exploration_ladder': {'value': [0.0, 0.1]},
        'hp_combat_weights': {'value': {'normal': 1.0, 'elite': 1.0, 'boss': 0.3}}}}))
    (run / 'facts.json').write_text(json.dumps({
        'src_rev': 'abc123def4567890', 'src_checkout': {'git': True, 'dirty': False}}))
    (run / 'learner').mkdir()
    (run / 'learner' / 'learner.json').write_text(json.dumps({'loss': {
        'hp_coef': 0.1, 'hp_advantage_coef': 0.005,
        'hp_advantage_scope': 'combat_ticket'}}))
    driver = run / 'env-n0' / 'p0'
    driver.mkdir(parents=True)

    def closed(ref, t, floor, ascension=0, terminal='defeat', **changes):
        return {'event': 'closed', 't': t, 'game_ref': ref, 'reason': 'terminal',
                'arm': 'control', 'start': 'new_run', 'floor': floor, 'replay_k': 1,
                'group': ref, 'ascension': ascension, 'terminal': terminal, **changes}
    lines = [{'event': 'admission', 'game_ref': ref, 'ok': True, 'code': None}
             for ref in ('a', 'b', 'c', 'd', 'e', 'f')]
    lines += [{'event': 'admission', 'game_ref': 'g', 'ok': False, 'code': 'protocol'},
              closed('a', 1_000, 17), closed('b', 2_000, 34),
              closed('c', 3_000, 48, terminal='victory'), closed('d', 4_000, 49, ascension=10),
              closed('e', 5_000, 40, arm='explore'), closed('f', 6_000, 40, start='replay'),
              closed('g', 7_000, 40), closed('h', 8_000, 12, reason='timeout',
                                             detail='episode_seconds', terminal=None),
              {'event': 'open_refused', 'reason': 'no_slot'}, {'event': 'slot_failed'}]
    (driver / 'driver.jsonl').write_text(jsonl_text(lines))


def test_readout_run_record_carries_identity_learning_games_and_incidents(tmp_path, capsys):
    run = tmp_path / 'run-t1'
    write_run(run)
    write_record_inputs(run)
    r = readout.build(run, 1200)
    assert r['schema'] == readout.SCHEMA == 'spireagent/friday-fastchain-run-record-1'
    i = r['identity']
    assert i['name'] == '3卡 · 1推理+2训练 · A100-40' and i['source_revision'] == 'abc123def4567890'
    assert i['source_dirty'] is False and len(i['profile_sha256']) == 64
    assert i['overridden_knobs'] == {'segment_format': 'stpd/rl-fast-segment-4'}
    assert i['recipe'] == {'exploration_ladder': [0.0, 0.1], 'ascension_mix': {
        '0': 0.5, '10': 0.5}, 'replay_k': 4, 'save_starts': None,
        'segment_format': 'stpd/rl-fast-segment-4',
        'hp_coef': 0.1, 'c_hp': 0.005, 'c_hp_scope': 'combat_ticket',
        'hp_combat_weights': {'normal': 1.0, 'elite': 1.0, 'boss': 0.3},
        'hp_potion_mode': None,  # a plan without the knob (ticket H)
        'kl_stop_scope': None, 'lr_warmup_steps': None}  # nor the ticket K knobs
    p = r['throughput']
    assert p['run_training_rows_per_h_incl_aux'] == pytest.approx(9000 * 3600, rel=1e-3)
    assert p['run_training_rows_per_h_td_only'] == pytest.approx(8000 * 3600, rel=1e-3)
    assert p['latest_training_rows_per_h_incl_aux'] == pytest.approx(9000 * 3600, rel=0.01)
    assert p['first_use_rows_per_h'] == pytest.approx(7000 * 3600, rel=1e-3)
    assert p['first_use_ratio_of_admitted'] == pytest.approx(7000 / 8100, rel=0.01)
    assert p['admitted_rows_total'] == 8100 * 1290 and p['kl_stopped_attempts'] == 1
    for part in ('latest', 'run_mean'):
        x = r['learning'][part]
        assert x['value']['all']['td']['explained_variance'] == pytest.approx(0.75)
        assert x['value']['all']['td']['mse'] == pytest.approx(0.0025)
        assert x['value']['all']['hp']['mae'] == pytest.approx(2.0)  # HP points
        assert x['value']['all']['terminal']['explained_variance'] is None
        assert x['value']['act3']['td']['rows'] == 0
        assert x['entropy'] == pytest.approx(1.5) and x['clip_fraction'] == pytest.approx(0.1)
    assert r['learning']['run_mean']['advantage_raw']['datasets'] == 13
    assert r['learning']['run_mean']['advantage_raw']['std'] == pytest.approx(0.2)
    games = r['training_games']
    every = games['rolling']['all']
    assert every['games'] == 4 and every['mean_floor'] == 37 and every['win_rate'] == 0.25
    assert every['reward_undefined_games'] == 1  # A10 floor-49 loss has no reward class
    a0 = games['run']['A0']
    assert a0['mean_reward_v21'] == pytest.approx(
        (0.5 * (17 / 48) ** 2 + 0.5 * (34 / 48) ** 2 + 0.2 + 1.0) / 3)
    assert {k: (b['reached'], b['cleared']) for k, b in a0['bosses'].items()} == {
        'act1_boss': (3, 2), 'act2_boss': (2, 2), 'act3_boss': (1, 1)}
    assert games['run']['A10']['bosses']['act3_boss2'] == {
        'games': 1, 'reached': 1, 'cleared': 0, 'clear_rate': 0.0,
        'clear_rate_given_reached': 0.0}
    assert every['act3_reached_rate'] == 0.75  # floors 34, 48 (won) and 49 passed the act-2 Boss
    # A3: every arm's full games; control's are the top-level numbers
    assert sorted(games['arms']) == ['control', 'explore']
    assert games['arms']['control']['run'] == games['run']
    assert games['arms']['control']['rolling'] == games['rolling']
    explore = games['arms']['explore']['rolling']['all']
    assert (explore['games'], explore['mean_floor'], explore['act3_reached_rate']) == (1, 40, 1)
    inc = r['incidents']
    assert inc['env_closed_by_reason'] == {'terminal': 7, 'timeout': 1}
    assert inc['env_close_details'] == {'timeout': {'episode_seconds': 1}}
    assert inc['admission_refused_by_code'] == {'protocol': 1}
    assert inc['open_refused_by_reason'] == {'no_slot': 1}
    assert inc['driver_failures_by_kind'] == {'slot_failed': 1} and inc['handoff_lost'] == 1
    boundary = r['boundary_sections']['run']
    assert boundary['datasets'] == 13
    assert boundary['boundary_sections_seconds_mean'] == {'gather': 0.5, 'assemble': 1.5}
    assert [c['checkpoint_artifact_id'] for c in r['checkpoints']] == ['cp-1', 'cp-2']
    text = readout.render(r)
    for line in ('identity: src abc123def456', 'incl. aux', 'first use:',
                 'EV vs lambda-return 0.750', 'HP MAE 2.00 HP', 'training games A0',
                 'Boss cleared act1 67%', 'admission refused', 'boundary per dataset (13)',
                 'checkpoints: 2, last cp-2', 'training games arm explore A0 (full, last 100): '
                 '1 games, floor 40.0, win 0.0%, act 3 reached 100%'):
        assert line in text, line
    assert readout.main([str(run), '--json']) == 0
    assert json.loads(capsys.readouterr().out)['learning'] == json.loads(
        json.dumps(r['learning']))
    events = (run / 'learner-events.jsonl').read_text().replace('[2.0, 0.6, 0.2, 0.0, 0.005, 0.1]',
                                                              '[2.0, 0.6]', 1)
    (run / 'learner-events.jsonl').write_text(events)
    with pytest.raises(ValueError, match='2 fields, expected 6'):
        readout.build(run, 1200)  # fails loudly, never a guessed EV


def test_readout_run_record_carries_the_hp_share_and_the_hp_score_by_encounter(tmp_path):
    # RL5-METHODS A2: c_hp * A_hp's share of the advantage variance (learner) and the
    # format-4 per-encounter counters (each service's latest cumulative assembler report).
    run = tmp_path / 'run-t1'
    write_run(run)
    write_record_inputs(run)
    r = readout.build(run, 1200)  # c_hp 0 / format 3: neither section has a number
    assert r['hp_score'] == {}
    assert r['learning']['run_mean']['advantage_raw']['hp_share'] is None
    text = readout.render(r)
    assert 'HP term' not in text and 'HP score by encounter' not in text
    events = [json.loads(line) for line in
              (run / 'learner-events.jsonl').read_text().splitlines()]
    for event in events:
        if 'advantage_raw' in event:  # 13 datasets: 6 at a share of 0.1, then 7 at 0.3
            event['advantage_raw'].update(hp_std=0.05,
                                          hp_share=0.1 if event['t'] < 1600 else 0.3)
    (run / 'learner-events.jsonl').write_text(jsonl_text(events))
    zero = {'battles': 0, 'deaths': 0, 'hp_change': 0}

    def status(t, normal, boss):
        return {'t': t, 'service': {'rows': 1}, 'assembler': {'hp_score': {'by_encounter': {
            'normal': normal, 'elite': zero, 'boss': boss, 'unknown': zero}}}}
    with open(run / 'service-status-0.jsonl', 'a') as handle:
        handle.write(jsonl_text([status(2300, {'battles': 10, 'deaths': 1,
                                               'hp_change': -100},
                                        {'battles': 2, 'deaths': 0, 'hp_change': -40})]))
    (run / 'service-status-1.jsonl').write_text(jsonl_text([
        status(2200, zero, zero),
        status(2300, {'battles': 20, 'deaths': 2, 'hp_change': -260},
               {'battles': 1, 'deaths': 1, 'hp_change': -50})]) + '{"t": 2305, "assem')
    r = readout.build(run, 1200)
    assert r['learning']['run_mean']['advantage_raw']['hp_share'] == pytest.approx(2.7 / 13)
    assert r['learning']['run_mean']['advantage_raw']['hp_std'] == pytest.approx(0.05)
    assert r['hp_score'] == {
        'normal': {'combats': 30, 'deaths': 3, 'death_rate': 0.1, 'mean_net_hp_change': -12.0},
        'elite': {'combats': 0, 'deaths': 0, 'death_rate': None, 'mean_net_hp_change': None},
        'boss': {'combats': 3, 'deaths': 1, 'death_rate': pytest.approx(1 / 3),
                 'mean_net_hp_change': -30.0},
        'unknown': {'combats': 0, 'deaths': 0, 'death_rate': None,
                    'mean_net_hp_change': None}}
    text = readout.render(r)
    assert 'HP term (c_hp A_hp) run mean: 20.8% of advantage variance' in text
    assert ('HP score by encounter (combats, deaths, mean net HP change): normal 30 / 3 / '
            '-12.0, elite 0 / 0 / ') in text


def test_readout_reads_only_appended_bytes_and_waits_for_a_torn_line(tmp_path):
    run = tmp_path / 'run-t1'
    write_run(run)
    write_record_inputs(run)
    events, driver = run / 'learner-events.jsonl', run / 'env-n0' / 'p0' / 'driver.jsonl'
    folded = [events, run / 'learner-status.jsonl', driver]
    reader = readout.Readout(run, 1200)
    before = reader.update().record()
    assert reader.read_bytes == sum(path.stat().st_size for path in folded)
    assert before == readout.build(run, 1200)
    read = reader.read_bytes
    reader.update()
    assert reader.read_bytes == read  # nothing new: nothing read
    heads = {'td': TD_SUMS, 'terminal': NONE_SUMS, 'hp': HP_SUMS}
    new_events = jsonl_text({'t': 2310.0 + i, 'kind': 'progress', 'stepped': True,
                             'rows': 8000, 'terminal_rows': 600, 'hp_rows': 400,
                             'seconds': 1.0, 'value_statistics': {'all': heads}}
                            for i in range(3))
    new_games = jsonl_text([{'event': 'admission', 'game_ref': 'z', 'ok': True},
                            {'event': 'closed', 't': 9_000, 'game_ref': 'z',
                             'reason': 'terminal', 'arm': 'control', 'start': 'new_run',
                             'floor': 20, 'replay_k': 1, 'group': 'z', 'ascension': 0,
                             'terminal': 'defeat'}])
    torn = '{"t": 2320.0, "kind": "checkpoint_published", "checkpoint_artifact_id"'
    with events.open('a') as handle:
        handle.write(new_events + torn)
    with driver.open('a') as handle:
        handle.write(new_games)
    after = reader.update().record()
    # Only the appended whole lines were read; the torn line waits, nothing earlier is lost.
    assert reader.read_bytes - read == len(new_events) + len(new_games)
    assert after['throughput']['updates'] == before['throughput']['updates'] + 3
    assert after['learning']['run_mean']['attempts'] == \
        before['learning']['run_mean']['attempts'] + 3
    assert after['training_games']['run']['all']['games'] == 5
    assert [c['checkpoint_artifact_id'] for c in after['checkpoints']] == ['cp-1', 'cp-2']
    assert after == readout.build(run, 1200)  # incremental == one fresh read
    with events.open('a') as handle:
        handle.write(': "cp-3"}\n')
    assert [c['checkpoint_artifact_id'] for c in reader.update().record()['checkpoints']] == [
        'cp-1', 'cp-2', 'cp-3']
    with events.open('a') as handle:
        handle.write('{"t": 2330.0, "kind": \n')
    with pytest.raises(ValueError, match='not JSON'):
        readout.Readout(run, 1200).update()  # a broken complete line fails loudly


def test_readout_counts_closes_without_a_natural_end_apart(tmp_path):
    run = tmp_path / 'run-t1'
    write_run(run)
    write_record_inputs(run)
    driver = run / 'env-n0' / 'p0' / 'driver.jsonl'
    base = {'event': 'closed', 'reason': 'terminal', 'arm': 'control', 'start': 'new_run',
            'replay_k': 1, 'ascension': 0}
    with driver.open('a') as handle:
        handle.write(jsonl_text([
            {'event': 'admission', 'game_ref': 'n1', 'ok': True},
            {'event': 'admission', 'game_ref': 'n2', 'ok': True},
            {**base, 't': 9_000, 'game_ref': 'n1', 'group': 'n1', 'floor': None,
             'terminal': None},  # the kernel closed it 'terminal' with no public end
            {**base, 't': 9_500, 'game_ref': 'n2', 'group': 'n2', 'floor': 30,
             'terminal': None}]))
    r = readout.build(run, 1200)
    games = r['training_games']
    assert games['unscored_closes'] == 2
    assert games['run']['all']['games'] == 4 and games['run']['all']['win_rate'] == 0.25
    assert 'closes without a natural end' in readout.render(r)


def _many_control_games(run, n, start_t=10_000):
    """n more complete A0 control games (floor 10 + i % 30, every 7th a win) on driver p0."""
    lines = []
    for i in range(n):
        ref = f'm{i}'
        lines += [{'event': 'admission', 'game_ref': ref, 'ok': True},
                  {'event': 'closed', 't': start_t + i, 'game_ref': ref, 'reason': 'terminal',
                   'arm': 'control', 'start': 'new_run', 'floor': 10 + i % 30,
                   'replay_k': 1, 'group': ref, 'ascension': 0,
                   'terminal': 'victory' if i % 7 == 0 else 'defeat'}]
    with (run / 'env-n0' / 'p0' / 'driver.jsonl').open('a') as handle:
        handle.write(jsonl_text(lines))


def _set_knob(run, name, value):
    path = run / 'effective-profile.json'
    profile = json.loads(path.read_text())
    profile['knobs'][name] = {'value': value}
    path.write_text(json.dumps(profile))


def test_readout_rolling_window_defaults_to_the_last_100_and_takes_the_profile_windows(tmp_path):
    """#556: `readout_rolling_games` [100] (the default) and a run frozen without the knob give
    the same rolling section as before (last 100, no extra key); [30, 1000] puts the last 30
    in `rolling` and the last 1000 beside it, per arm too, incrementally."""
    run = tmp_path / 'run-t1'
    write_run(run)
    write_record_inputs(run)
    _many_control_games(run, 150)
    old = readout.build(run, 1200)  # no knob in the profile: a run frozen before #556
    games = old['training_games']
    assert games['rolling_games'] == 100 and 'rolling_windows' not in games
    assert 'rolling_windows' not in games['arms']['control']
    last = [10 + i % 30 for i in range(50, 150)]  # the newest 100 by close time
    assert games['rolling']['all']['games'] == 100
    assert games['rolling']['all']['mean_floor'] == pytest.approx(sum(last) / 100)
    assert games['rolling']['all']['win_rate'] == pytest.approx(
        sum(i % 7 == 0 for i in range(50, 150)) / 100)
    assert games['run']['all']['games'] == 154
    _set_knob(run, 'readout_rolling_games', [100])
    new = readout.build(run, 1200)  # the default: the same record (bar the profile digest)

    def lines(record):
        return [line for line in readout.render(record).splitlines()
                if not line.startswith('identity:')]
    assert {**new, 'identity': None} == {**old, 'identity': None}
    assert lines(new) == lines(old)
    assert 'training games A0 (control, full, last 100): 100 games' in readout.render(old)

    _set_knob(run, 'readout_rolling_games', [30, 1000])
    reader = readout.Readout(run, 1200)
    reader.update()
    _many_control_games(run, 20, start_t=20_000)  # appended after the first read
    r = reader.update().record()
    assert r == readout.build(run, 1200)  # incremental == one fresh read
    games = r['training_games']
    assert games['rolling_games'] == 30 and games['rolling']['all']['games'] == 30
    newest = [10 + i % 30 for i in range(130, 150)] + [10 + i % 30 for i in range(20)]
    assert games['rolling']['all']['mean_floor'] == pytest.approx(sum(newest[-30:]) / 30)
    wide = games['rolling_windows']['1000']['all']
    assert wide['games'] == games['run']['all']['games'] == 174
    # per ascension the deque holds the largest window too, not the first
    assert games['rolling_windows']['1000']['A0']['games'] == games['run']['A0']['games'] > 30
    assert games['arms']['control']['rolling_windows'] == games['rolling_windows']
    assert games['arms']['explore']['rolling_windows']['1000']['all']['games'] == 1
    text = readout.render(r)
    assert 'training games A0 (control, full, last 30): 30 games' in text
    assert 'training games all (control, full, last 1000): 174 games' in text
    assert 'training games arm explore A0 (full, last 1000): 1 games' in text
    with pytest.raises(ValueError, match='positive ints'):
        readout.Readout(run, 1200, rolling=[0])


def test_readout_rolling_windows_run_on_across_a_resumed_generation(tmp_path):
    """A resumed chain generation starts its rolling windows from the earlier generations'
    games (bounded by the largest window); its whole-run section stays its own."""
    first, second = tmp_path / 'run-t1', tmp_path / 'run-t1-r1'
    for run in (first, second):
        write_run(run)
        write_record_inputs(run)
        _set_knob(run, 'readout_rolling_games', [30, 1000])
    _many_control_games(first, 150)
    _many_control_games(second, 20, start_t=20_000)
    alone = readout.build(second, 1200)['training_games']
    assert alone['rolling_windows']['1000']['all']['games'] == 24
    assert 'rolling_seed_runs' not in alone
    reader = readout.Readout(second, 1200, seed=[first])
    games = reader.update().record()['training_games']
    assert games['rolling_seed_runs'] == ['run-t1']
    assert games['run'] == alone['run']  # this generation's own
    assert games['rolling_windows']['1000']['all']['games'] == 154 + 24
    first_a0 = readout.build(first, 1200)['training_games']['run']['A0']['games']
    assert games['rolling_windows']['1000']['A0']['games'] == (
        first_a0 + alone['run']['A0']['games'])
    assert games['rolling']['all']['games'] == 30
    assert games['arms']['explore']['rolling_windows']['1000']['all']['games'] == 2
    bounded = readout.Readout(second, 1200, rolling=[30, 100], seed=[first]).update()
    assert len(bounded.control.rolling) == 100
    # A seeded generation with no game closed yet already shows the windows.
    empty = tmp_path / 'run-t1-r2'
    write_run(empty)
    games = readout.Readout(empty, 1200, rolling=[100], seed=[first, second]).update(
        ).record()['training_games']
    assert games['rolling']['all']['games'] == 100 and games['run']['all']['games'] == 0


def test_readout_reports_hp_score_and_ppo_update_per_arm(tmp_path):
    """RL5-METHODS R: per exploration arm the assemblers' per-encounter HP counters
    (`<arm>+save` apart) and the learner's per-arm clip fraction and approx KL."""
    run = tmp_path / 'run-t1'
    write_run(run)
    write_record_inputs(run)
    r = readout.build(run, 1200)
    assert r['hp_score_arms'] == {} and r['learning']['run_mean']['policy_by_arm'] is None
    text = readout.render(r)
    assert 'PPO update per arm' not in text and '  arm control:' not in text
    zero = {'battles': 0, 'deaths': 0, 'hp_change': 0}

    def table(normal, boss):
        return {'normal': normal, 'elite': zero, 'boss': boss, 'unknown': zero}
    hot = table({'battles': 10, 'deaths': 1, 'hp_change': -100}, zero)
    control = table({'battles': 4, 'deaths': 0, 'hp_change': -20},
                    {'battles': 2, 'deaths': 1, 'hp_change': -90})
    saved = table(zero, {'battles': 1, 'deaths': 1, 'hp_change': -60})
    statuses = [({'control': control, 'hot': hot}, 0), ({'control': control,
                                                          'control+save': saved}, 1)]
    for by_arm, index in statuses:
        by_encounter = {kind: {key: sum(t[kind][key] for t in by_arm.values())
                               for key in zero} for kind in control}
        (run / f'service-status-{index}.jsonl').write_text(jsonl_text([
            {'t': 2300, 'service': {'rows': 1}, 'assembler': {'hp_score': {
                'by_encounter': by_encounter, 'by_arm': by_arm}}}]))
    events = [json.loads(line) for line in
              (run / 'learner-events.jsonl').read_text().splitlines()]
    for event in events:
        if event.get('kind') == 'progress':  # 4 rows of control (1 clipped), 2 of hot
            event['policy_by_arm'] = {'control': [4, 0.004, 1, 0], 'hot': [2, 0.01, 2, 1],
                                      'unattributed': [3, 0.0, 0, 0]}
    (run / 'learner-events.jsonl').write_text(jsonl_text(events))
    r = readout.build(run, 1200)
    arms = r['hp_score_arms']
    assert sorted(arms) == ['control', 'control+save', 'hot']
    assert arms['control']['normal'] == {'combats': 8, 'deaths': 0, 'death_rate': 0.0,
                                         'mean_net_hp_change': -5.0}
    assert arms['control']['boss'] == {'combats': 4, 'deaths': 2, 'death_rate': 0.5,
                                       'mean_net_hp_change': -45.0}
    assert arms['hot']['normal']['death_rate'] == 0.1
    assert arms['hot']['elite']['death_rate'] is None
    assert arms['control+save']['boss']['mean_net_hp_change'] == -60.0
    assert r['hp_score']['boss']['combats'] == 5  # all arms together, as before
    attempts = r['learning']['run_mean']['attempts']
    ppo = r['learning']['run_mean']['policy_by_arm']
    assert ppo['control'] == {'rows': 4 * attempts, 'clip_fraction': 0.25,
                              'approx_kl': pytest.approx(0.001), 'ratio_nonfinite': 0}
    assert ppo['hot'] == {'rows': 2 * attempts, 'clip_fraction': 1.0,
                          'approx_kl': pytest.approx(0.01), 'ratio_nonfinite': attempts}
    assert r['learning']['latest']['policy_by_arm']['hot']['clip_fraction'] == 1.0
    text = readout.render(r)
    assert 'PPO update per arm (rows, clip, approx KL): control ' in text
    assert '/ 0.250 / 0.0010, hot ' in text
    assert 'unattributed' not in text.split('PPO update per arm')[1].splitlines()[0]
    lost = r['learning']['latest']['policy_by_arm']['unattributed']['rows']
    assert lost % 3 == 0 and f'  WARNING (latest): {lost:,} PPO rows unattributed (a source ' \
        'without an exploration table), left out of the per-arm line' in text
    assert '  arm control+save: normal 0 / 0 / -, elite 0 / 0 / -, boss 1 / 1 / -60.0' in text
    events[-5]['policy_by_arm'] = {'hot': [2, 0.01]}
    (run / 'learner-events.jsonl').write_text(jsonl_text(events))
    with pytest.raises(ValueError, match='2 fields, expected 4'):
        readout.build(run, 1200)


def test_readout_reports_the_learning_settings_diagnostics(tmp_path):
    """RL5-METHODS R: clip bind rate and pre-clip norm, value-target entropy and outcome
    weight, value calibration, policy entropy by decision and act, row age (versions,
    updates, model seconds) and the game-target intake fields (#564)."""
    run = tmp_path / 'run-t1'
    write_run(run)
    write_record_inputs(run)
    r = readout.build(run, 1200)
    run_mean = r['learning']['run_mean']
    assert run_mean['gradient_clip'] is None and run_mean['value_calibration'] is None
    assert run_mean['policy_entropy'] is None and run_mean['row_age']['mu_age_seconds'] is None
    assert r['value_target_intake'] == {}
    assert 'settings run mean' not in readout.render(r)
    events = [json.loads(line) for line in
              (run / 'learner-events.jsonl').read_text().splitlines()]
    progress = [e for e in events if e.get('kind') == 'progress' and e.get('stepped')]
    for n, event in enumerate(progress):
        norm = 2.0 if n % 4 == 0 else 0.5  # a quarter of the steps above max_grad_norm 1
        event.update(gradient_norm=norm, gradient_clipped=norm > 1.0,
                     value_calibration=[[2, 0.1, 0.0]] + [[0, 0.0, 0.0]] * 8 + [[1, 0.95, 1.0]],
                     policy_entropy={'control|act1|combat': [3, 1.5, 0.75],
                                     'hot|act2|rest': [1, 0.2, 0.1]},
                     first_use_version_lag=[1, 1, 2, 9], first_use_pre_update_SGD_lag=[0, 3],
                     first_use_mu_age_seconds={'n': 2, 'missing': 1, 'sum': 70.0,
                                               'histogram': [0, 0, 0, 1, 0, 0, 1] + [0] * 12})
        if 'advantage_raw' in event:
            event['advantage_raw']['variance'] = 0.04
            event['value_target'] = {'rows': 10, 'outcome_weight_mean': 0.3,
                                     'noncombat_rows': 4, 'noncombat_outcome_weight_mean': 0.5,
                                     'target_entropy_mean': 2.0}
    (run / 'learner-events.jsonl').write_text(jsonl_text(events))
    status = [json.loads(line) for line in
              (run / 'learner-status.jsonl').read_text().splitlines()]
    status[-1]['fast_chain']['intake'] = {
        'value_target': 'game', 'held_rows': 500, 'held_instances': 3, 'spill_bytes': 4096,
        'spill_files': 2, 'spill_peak_bytes': 8192, 'game_target_stop_dropped_rows': 7,
        'game_target_terminal_end_rows': 90, 'closed_terminal': 4}
    status[-1]['fast_chain']['learner']['game_end_lt128_selected_rows'] = 11
    (run / 'learner-status.jsonl').write_text(jsonl_text(status))
    r = readout.build(run, 1200)
    x = r['learning']['run_mean']
    clip = x['gradient_clip']
    assert clip['steps'] == len(progress) and clip['bound'] == len(range(0, len(progress), 4))
    assert clip['bind_rate'] == pytest.approx(clip['bound'] / clip['steps'])
    assert clip['pre_clip_norm_mean'] == pytest.approx(
        (2.0 * clip['bound'] + 0.5 * (clip['steps'] - clip['bound'])) / clip['steps'])
    assert clip['pre_clip_norm_p95_at_most'] == pytest.approx(2 ** (1 / 8) * 2.0)
    assert x['value_target']['target_entropy_mean'] == 2.0
    assert x['value_target']['noncombat_outcome_weight_mean'] == 0.5
    assert x['advantage_raw']['variance'] == pytest.approx(0.04)
    calibration = x['value_calibration']
    assert len(calibration) == 10 and calibration[0]['rows'] == 2 * len(progress)
    assert calibration[0]['mean_predicted'] == pytest.approx(0.05)
    assert calibration[0]['mean_realised'] == 0.0 and calibration[5]['rows'] == 0
    assert calibration[9] == {'bin': [0.9, 1.0], 'rows': len(progress),
                              'mean_predicted': pytest.approx(0.95), 'mean_realised': 1.0}
    entropy = x['policy_entropy']
    assert entropy['by_decision']['combat']['entropy_mean'] == pytest.approx(0.5)
    assert entropy['by_act']['act2']['normalized_entropy_mean'] == pytest.approx(0.1)
    assert entropy['all']['entropy_mean'] == pytest.approx(1.7 / 4)
    age = x['row_age']
    assert age['version_lag'] == {'n': 4 * len(progress), 'mean': 3.25, 'p95': 9}
    assert age['pre_update_SGD_lag']['mean'] == 1.5
    seconds = age['mu_age_seconds']
    assert seconds['n'] == 2 * len(progress) and seconds['missing'] == len(progress)
    assert seconds['mean'] == 35.0 and seconds['p95_at_most'] == 60
    latest = r['learning']['latest']
    assert latest['row_age']['version_lag']['p95'] == 9  # kept as counts in the window
    assert latest['gradient_clip']['steps'] < clip['steps']
    assert r['value_target_intake'] == {
        'intake': {'game_target_stop_dropped_rows': 7, 'game_target_terminal_end_rows': 90,
                   'held_instances': 3, 'held_rows': 500, 'spill_bytes': 4096,
                   'spill_files': 2, 'spill_peak_bytes': 8192},
        'learner': {'game_end_lt128_selected_rows': 11}}
    text = readout.render(r)
    assert '  settings run mean: grad clip bound 25.' in text
    assert ('value target entropy 2.000 nats (mean of dataset means), outcome weight 0.300 '
            '(noncombat 0.500)') in text
    assert ('row age 3.25 versions (p95 9), 1.50 updates (p95 3), behaviour-model age since '
            'publication (upper bound on row age) 35 s (p95 <= 60 s)') in text
    assert ('  value calibration (terminal training rows, in-sample, row visits incl. KL-stopped '
            'attempts, all arms; predicted -> realised, visits): 0.05 -> 0.00') in text
    assert ('  behaviour-model entropy, T/mix removed (nats / of ln N) by decision: combat '
            '0.50/0.25, rest 0.20/0.10') in text
    assert 'game value targets: held 500 rows, spill 4,096 bytes in 2 files (peak 8,192), ' \
        "dropped rows {'stop': 7}" in text
    progress[3]['value_calibration'] = [[1, 0.5]]
    (run / 'learner-events.jsonl').write_text(jsonl_text(events))
    with pytest.raises(ValueError, match='value_calibration bin 0: 2 fields, expected 3'):
        readout.build(run, 1200)


def test_readout_refuses_to_guess_missing_aux(tmp_path):
    run = tmp_path / 'run-t1'
    write_run(run)
    events = [json.loads(line) for line in (run / 'learner-events.jsonl').read_text().splitlines()]
    for event in events[5:]:
        event.pop('hp_rows', None)
    (run / 'learner-events.jsonl').write_text(jsonl_text(events))
    result = readout.build(run, 1200)
    assert result['training']['rows_h'] is None and result['training']['aux_missing']
    assert result['verdict'] is None
    assert 'aux fields missing' in readout.render(result)


def test_verdict_strict_for_three_cards_and_ready():
    three = {'cards': 3, 'target_rows_h': 30_000_000, 'ready_rows_h': 35_000_000}
    assert readout.verdict(30_000_000, three) == 'below target'
    assert readout.verdict(30_000_001, three) == 'meets target'
    assert readout.verdict(35_000_000, three) == 'ready'
    assert readout.verdict(14_000_000, {'cards': 1, 'target_rows_h': 14_000_000}) == \
        'meets target'


def test_readout_single_card_has_no_loss_table(tmp_path):
    run = tmp_path / 'run-t1'
    plan = write_run(run, cards=1)
    assert plan['placement']['cards'] == 1
    result = readout.build(run, 1200)
    assert result['loss_table'] is None
    assert result['target']['target_rows_h'] == 14_000_000
    assert 'perfect_packing_rows_h' not in plan['target']  # shared card: not an upper bound


def test_readout_alarms_on_recompiles_after_warmup_and_sums_gc():
    events = [{'kind': 'sgd_process_started', 'compile': {'warmup': ['a.pt', 'b.pt']}},
              {'kind': 'progress', 'stepped': True, 't': 10.0,
               'rows_forward': {'dynamo_unique_graphs': 6}, 'step': {'gc_seconds': 0.5}},
              {'kind': 'progress', 'stepped': True, 't': 70.0,
               'rows_forward': {'dynamo_unique_graphs': 6}, 'step': {'gc_seconds': 0.25}}]
    calm = readout.compile_edge(events)
    assert calm['warmup_dumps'] == 2 and calm['graphs_after_warmup'] == 6
    assert calm['alarm'] is None and calm['gc_seconds'] == 0.75
    events.append({'kind': 'progress', 'stepped': True, 't': 130.0,
                   'rows_forward': {'dynamo_unique_graphs': 7}})
    assert 'rose 6 -> 7' in readout.compile_edge(events)['alarm']
    assert readout.compile_edge([])['gc_seconds'] is None


def test_learner_config_matches_the_frozen_recipe_except_allowed_levers():
    knobs = run_profile.values(run_profile.load_profile(PROFILE))
    run = {'run_name': 'r', 'peer_dir': '/p', 'start_package': '/s', 'wall_seconds': 60,
           'device': 'cuda', 'generation': 'g', 'warmup_artifact_id': 'w',
           'rendezvous_path': '/r', 'ranks': 1, 'resume_checkpoint': None}
    built = learner_config.build(gate_base(), knobs, run)
    assert 'archive' not in json.dumps(built['peer']) and built['max_versions'] == 900000
    assert learner_config.recipe_drift(built, FROZEN) == []
    assert learner_config.recipe_drift({**built, 'max_versions': 100000}, FROZEN) == [
        'max_versions: frozen 900000, effective 100000']
    resumed = learner_config.build(gate_base(), knobs, {**run, 'resume_checkpoint': 'cp-1'})
    assert resumed['resume_checkpoint'] == 'cp-1'


def test_hp_score_coefficient_comes_from_the_gate_and_needs_segment_format_4():
    assert knobs()['segment_format'] == 'stpd/rl-fast-segment-3'  # default: format 3, c_hp 0
    plan = plan_for(cards=3, wall_seconds=2400, segment_format='stpd/rl-fast-segment-4')
    facts = {**good_facts(plan), 'gate_learner_base': with_hp(gate_base(), 0.003),
             'frozen_recipe': with_hp(FROZEN, 0.003)}
    # scope combat_ticket: no source sends single-combat tickets, so no row gets the term
    results = by_name(preflight.checks(plan, facts))
    assert [r.name for r in results.values() if not r.ok] == ['hp_score']
    assert 'scope combat_ticket adds nothing' in results['hp_score'].detail
    # the "all_combats" experiment arm: the same path, gate and frozen recipe agreeing
    arm = {**facts, 'gate_learner_base': with_hp(gate_base(), 0.003, 'all_combats'),
           'frozen_recipe': with_hp(FROZEN, 0.003, 'all_combats')}
    results = preflight.checks(plan, arm)
    assert all(r.ok for r in results), preflight.report(results)
    assert by_name(results)['hp_score'].detail == 'c_hp 0.003, scope all_combats, ' \
        'segment format stpd/rl-fast-segment-4'
    # build passes the gate's c_hp through; A9 compares it with the frozen value
    run = {'run_name': 'r', 'peer_dir': '/p', 'start_package': '/s', 'wall_seconds': 60,
           'device': 'cuda', 'generation': 'g', 'warmup_artifact_id': 'w',
           'rendezvous_path': '/r', 'ranks': 1, 'resume_checkpoint': None}
    built = learner_config.build(with_hp(gate_base(), 0.003, 'all_combats'), plan['knobs'], run)
    assert built['loss']['hp_advantage_coef'] == 0.003
    assert built['loss']['hp_advantage_scope'] == 'all_combats'
    assert learner_config.recipe_drift(built, FROZEN) == [
        'loss.hp_advantage_coef: frozen 0.0, effective 0.003',
        "loss.hp_advantage_scope: frozen 'combat_ticket', effective 'all_combats'"]
    # build alone never invents c_hp: a gate without it is `<missing>` drift
    old = learner_config.build(gate_without_hp(), plan['knobs'], run)
    assert learner_config.recipe_drift(old, FROZEN) == [
        "loss.hp_advantage_coef: frozen 0.0, effective '<missing>'",
        "loss.hp_advantage_scope: frozen 'combat_ticket', effective '<missing>'"]


def test_hp_score_refuses_a_coefficient_no_row_would_receive():
    """G: c_hp > 0 with scope combat_ticket adds nothing (no source sends single-combat
    tickets), and every c_hp > 0 needs format 4; c_hp 0 (today's default) passes as before."""
    def hp(fmt, coef, scope):
        plan = plan_for(cards=3, wall_seconds=2400, segment_format=fmt)
        facts = {**good_facts(plan), 'gate_learner_base': with_hp(gate_base(), coef, scope),
                 'frozen_recipe': with_hp(FROZEN, coef, scope)}
        return preflight.check_hp_score(plan, facts)

    three, four = 'stpd/rl-fast-segment-3', 'stpd/rl-fast-segment-4'
    assert not hp(three, 0.003, 'all_combats').ok  # all_combats still needs format 4
    assert 'needs segment_format' in hp(three, 0.003, 'all_combats').detail
    assert not hp(three, 0.003, 'combat_ticket').ok
    refused = hp(four, 0.003, 'combat_ticket')
    assert not refused.ok and 'sends no single-combat tickets' in refused.detail
    assert hp(four, 0.003, 'all_combats').ok
    for fmt in (three, four):  # c_hp 0: the scope does not matter, as today
        assert hp(fmt, 0.0, 'combat_ticket').ok and hp(fmt, 0.0, 'all_combats').ok
    # the fact the refusal rests on: the fast env's episode_open names no ticket, so the
    # assembler ends every game as {"end": "game"}; a ticket source must lift the refusal
    kernel = (FASTCHAIN.parents[2] / 'components/policy-runtime/src/fast-env/kernel.ts')
    opened = kernel.read_text().split('type: "episode_open"', 1)[1].split('});', 1)[0]
    assert 'ticket' not in opened


def test_a_trained_start_without_fitted_heads_needs_the_site_to_say_so():
    """G / REVIEW-550 O1: a trained start with warmup_file = "" starts its RL4 heads from the
    constructor init; refused unless site trained_start_without_warmup = true. A random-init
    start and a resume (heads from the CP) pass as before."""
    plan = plan_for(cards=3, wall_seconds=2400)
    none = {**good_facts(plan), 'warmup_artifact': {'id': None, 'present': False, 'none': True}}
    refused = preflight.check_warmup_artifact(plan, none)
    assert not refused.ok and 'trained_start_without_warmup' in refused.detail
    assert preflight.check_warmup_artifact({**plan, 'resume_checkpoint': 'cp-1'}, none).ok
    random_init = {**none, 'start_model': {'package_sha256': 'e' * 64, 'training_identity': {
        'schema': preflight.RANDOM_START, 'seed': 5}}}
    assert preflight.check_warmup_artifact(plan, random_init).ok
    meant = copy.deepcopy(plan)
    meant['site']['trained_start_without_warmup'] = True
    passed = preflight.check_warmup_artifact(meant, none)
    assert passed.ok and passed.detail.endswith('site trained_start_without_warmup = true')
    both = preflight.check_warmup_artifact(meant, good_facts(plan))  # opt-out + a warm-up
    assert not both.ok and 'set one of them' in both.detail
    assert preflight.check_warmup_artifact({**meant, 'resume_checkpoint': 'cp-1'},
                                           good_facts(plan)).ok  # never refuses a resume
    # fitted heads: compared with the start package on a fresh start
    other = {**good_facts(plan), 'warmup_artifact': {'id': 'f' * 64, 'present': True,
                                                     'package_sha256': 'd' * 64}}
    mismatch = preflight.check_warmup_artifact(plan, other)
    assert not mismatch.ok and 'd' * 64 in mismatch.detail and 'e' * 64 in mismatch.detail
    assert preflight.check_warmup_artifact({**plan, 'resume_checkpoint': 'cp-1'}, other).ok


def test_site_takes_trained_start_without_warmup_as_an_optional_bool(tmp_path):
    assert 'trained_start_without_warmup' not in run_profile.load_site(SITE)  # default: off
    site = tmp_path / 'site.toml'
    text = SITE.read_text().replace('\n[site]\n', '\n[site]\ntrained_start_without_warmup = @\n')
    site.write_text(text.replace('@', 'true'))
    assert run_profile.load_site(site)['trained_start_without_warmup'] is True
    site.write_text(text.replace('@', '"yes"'))
    with pytest.raises(run_profile.ProfileError,
                       match='trained_start_without_warmup: expected bool'):
        run_profile.load_site(site)


def test_a_gate_from_before_the_hp_score_takes_both_keys_from_the_frozen_config():
    """F: the shared gate predates #538 and must stay so while old-code chains run (their
    LossConfig refuses unknown keys); the run's own frozen config supplies the missing
    loss keys, reported, and A9 still compares every key the gate has."""
    filled_base, filled = learner_config.gate_recipe(gate_without_hp(), with_hp(FROZEN, 0.003))
    assert filled == {'hp_advantage_coef': 0.003, 'hp_advantage_scope': 'combat_ticket'}
    assert filled_base['loss'] == with_hp(gate_base(), 0.003)['loss']
    assert 'hp_advantage_coef' not in gate_without_hp()['loss']  # the input is not changed
    same, none = learner_config.gate_recipe(with_hp(gate_base(), 0.5), FROZEN)
    assert none == {} and same['loss']['hp_advantage_coef'] == 0.5  # the gate's value wins
    plan = plan_for(cards=3, wall_seconds=2400)
    facts = {**good_facts(plan), 'gate_learner_base': gate_without_hp()}
    results = by_name(preflight.checks(plan, facts))
    assert all(r.ok for r in results.values()), preflight.report(list(results.values()))
    assert results['frozen_recipe'].detail.endswith(
        'not in the gate, from the frozen config: loss.hp_advantage_coef, '
        'loss.hp_advantage_scope')
    assert results['hp_score'].detail == ('c_hp 0.0, scope combat_ticket, segment format '
                                          'stpd/rl-fast-segment-3 (from the frozen config; '
                                          'not in the gate)')
    # the c_hp experiment: only the source rl5.json changes, the shared gate does not
    four = plan_for(cards=3, wall_seconds=2400, segment_format='stpd/rl-fast-segment-4')
    arm = {**good_facts(four), 'gate_learner_base': gate_without_hp(),
           'frozen_recipe': with_hp(FROZEN, 0.003, 'all_combats')}
    results = by_name(preflight.checks(four, arm))
    assert all(r.ok for r in results.values()), preflight.report(list(results.values()))
    assert results['hp_score'].detail.startswith('c_hp 0.003, scope all_combats')
    # a gate made after #538 (or a patched copy) reports nothing filled
    assert 'not in the gate' not in by_name(preflight.checks(plan, good_facts(plan)))[
        'frozen_recipe'].detail


def test_a_partly_filled_gate_keeps_its_own_loss_keys_for_a9():
    """#551 review S1: filling one key never replaces the loss keys the gate has; its
    stale c_hp is still compared with the frozen value (a gate_recipe that merged the
    whole frozen loss block over the gate would compare frozen with frozen)."""
    gate = gate_without_hp('hp_advantage_scope', config=with_hp(gate_base(), 0.003))
    filled_base, filled = learner_config.gate_recipe(gate, FROZEN)
    assert filled == {'hp_advantage_scope': 'combat_ticket'}
    assert filled_base['loss']['hp_advantage_coef'] == 0.003  # the gate's, not frozen 0.0
    plan = plan_for(cards=3, wall_seconds=2400)
    results = by_name(preflight.checks(plan, {**good_facts(plan), 'gate_learner_base': gate}))
    assert not results['frozen_recipe'].ok
    assert results['frozen_recipe'].detail == (
        'drift from the frozen parameters: loss.hp_advantage_coef: frozen 0.0, effective 0.003')


def test_a_gate_loss_key_the_frozen_config_lacks_is_refused_before_the_queue():
    """#551 review S2: A9 walks the frozen keys only; a misspelt key in a patched gate copy
    would pass (its correct name filled from the frozen config) and stop the learner's
    LossConfig after the GPU queue. Preflight names it."""
    gate = gate_without_hp('hp_advantage_coef')
    gate['loss']['hp_advantage_coeff'] = 0.003
    plan = plan_for(cards=3, wall_seconds=2400)
    results = by_name(preflight.checks(plan, {**good_facts(plan), 'gate_learner_base': gate}))
    assert not results['frozen_recipe'].ok
    assert results['frozen_recipe'].detail.startswith(
        'gate learner-base.json has loss keys the frozen config lacks: hp_advantage_coeff')


def test_roles_writes_the_learner_config_and_gc_keys_preflight_checked():
    """#551 review S4: roles.py (a job script, not importable here) takes its learner.json
    from `learner_base`, the one function preflight checks, and its GC keys from
    `sgd_gc_dataplane`; the configured threshold reaches the SGD child's init."""
    from stpd.rl.sgd_process import sgd_options

    plan = plan_for(cards=3, wall_seconds=2400, segment_format='stpd/rl-fast-segment-4')
    run = {'run_name': 'r', 'peer_dir': '/p', 'start_package': '/s', 'wall_seconds': 60,
           'device': 'cuda', 'generation': 'g', 'warmup_artifact_id': 'w',
           'rendezvous_path': '/r', 'ranks': 1, 'resume_checkpoint': None}
    frozen = with_hp(FROZEN, 0.003, 'all_combats')
    run['start_model_sha256'] = 'a' * 64  # A1 start_fields pass through to build
    written, filled = learner_config.learner_base(gate_without_hp(), frozen, plan['knobs'], run)
    assert written['loss']['hp_advantage_coef'] == 0.003 and set(filled) == {
        'hp_advantage_coef', 'hp_advantage_scope'}
    assert (written['start_package'], written['start_model_sha256']) == ('/s', 'a' * 64)
    assert learner_config.recipe_drift(written, frozen) == []
    results = by_name(preflight.checks(plan, {**good_facts(plan), 'frozen_recipe': frozen,
                                              'gate_learner_base': gate_without_hp()}))
    assert all(r.ok for r in results.values()), preflight.report(list(results.values()))
    assert results['hp_score'].detail.startswith('c_hp 0.003')
    gc_keys = learner_config.sgd_gc_dataplane(plan['knobs'])
    assert gc_keys == {'sgd_gc_freeze': True, 'sgd_gc_refreeze_objects': 40_000}
    assert sgd_options({'sgd_process': True, **gc_keys}).stage_init()[
        'gc_refreeze_objects'] == 40_000
    assert learner_config.sgd_gc_dataplane(knobs(sgd_gc_freeze=False,
                                                 sgd_gc_refreeze_objects=200_000)) == {}
    assert learner_config.sgd_gc_dataplane(knobs(sgd_gc_refreeze_objects=200_000)) == {
        'sgd_gc_freeze': True}
    roles = (FASTCHAIN / 'roles.py').read_text()
    assert 'base, filled = learner_config.learner_base(' in roles
    assert 'dataplane.update(learner_config.sgd_gc_dataplane(K))' in roles
    assert 'gate_recipe' not in roles and 'learner_config.build(' not in roles
    assert 'dataplane["sgd_gc_' not in roles


def test_long_run_and_smoke_are_exclusive():
    with pytest.raises(SystemExit):
        plan_for(cards=2, smoke=True, long_run=True)


def test_learner_window_follows_the_learner_restore_rules():
    fresh = learner_config.window('c-x', 72000, 1e9, None, None)
    assert fresh['operating_budget'] == {'id': 'c-x', 'total_seconds': 72000,
                                         'consumed_seconds': 0}
    assert fresh['deadline_at'] == 1e9 and fresh['operating_budget_parent'] is None
    own = {'operating_budget': {'id': 'c-x', 'total_seconds': 72000, 'consumed_seconds': 9},
           'deadline_at': 1e9}
    assert learner_config.window('c-x', 72000, 1e9, 'cp1', own) == {
        'operating_budget': None, 'operating_budget_parent': None, 'deadline_at': None}
    other = {'operating_budget': {'id': 'old', 'total_seconds': 3600, 'consumed_seconds': 3600},
             'deadline_at': 5.0}
    parent = learner_config.window('c-x', 72000, 1e9, 'cp0', other)['operating_budget_parent']
    assert parent == {'checkpoint_artifact_id': 'cp0', 'operating_budget': other[
        'operating_budget'], 'deadline_at': 5.0}
    assert learner_config.window('c-x', 72000, 1e9, 'cp0', {'operating_budget': None,
                                 'deadline_at': None})['deadline_at'] == 1e9
    with pytest.raises(ValueError, match='no operating budget'):
        learner_config.window('c-x', 72000, 1e9, 'cp0', {'operating_budget': None,
                                                         'deadline_at': 5.0})
    with pytest.raises(ValueError):
        learner_config.window('c-x', 80000, 1e9, None, None)


def test_chain_generation_plan_and_resume_preflight():
    chain = {'id': 'c-t1', 'deadline_at': 10**10, 'total_seconds': 72000, 'generation': 1}
    plan = launch.build_plan('t1-r1', knobs(), run_profile.load_site(SITE), cards=2,
                             card_type='A100-40', layout_spec=None, env_cores=None,
                             smoke=False, probe_rows=[probe_row()], long_run=True,
                             resume_checkpoint='cp-0123456789', chain=chain)
    assert plan['wall_seconds'] == 72000 and plan['resume_checkpoint'] == 'cp-0123456789'
    env = launch.job_env(plan)
    assert 'FC_RESUME_CHECKPOINT=cp-0123456789' in env and 'FC_CHAIN_ID=c-t1' in env
    assert preflight.check_resume(plan, {'start_model': None}).ok  # a resume reads no package
    late = launch.build_plan('t1-r2', knobs(), run_profile.load_site(SITE), cards=2,
                             card_type='A100-40', layout_spec=None, env_cores=None,
                             smoke=False, probe_rows=[probe_row()], long_run=True,
                             chain={**chain, 'deadline_at': __import__('time').time() + 600})
    assert late['wall_seconds'] <= 600
    assert 'min_generation_seconds' in preflight.check_resume(late, good_facts(late)).detail
    # a fresh start (generation 1, no CP) loads the start package: missing = refused at login
    missing = preflight.check_resume(late, {**good_facts(late), 'start_model': None})
    assert not missing.ok and 'fresh start without a start package' in missing.detail
    with pytest.raises(SystemExit):
        launch.build_plan('t1', knobs(), run_profile.load_site(SITE), cards=2,
                          card_type='A100-40', layout_spec=None, env_cores=None, smoke=True,
                          probe_rows=[probe_row()], chain=chain)


def write_outcome(run, failure=None, report=True, disk=False, window_end='deadline',
                  stop=None, rc='auto'):
    run.mkdir(parents=True, exist_ok=True)
    if rc == 'auto':  # gpu.sh: 0 clean, 6 learner failure, none when the node died
        rc = None if not report else 6 if failure else 0
    if rc is not None:
        (run / 'gpu-rc').write_text(f'{rc}\n')
    if report:
        (run / 'learner-report.json').write_text(json.dumps(
            {'failure': failure, 'summary': None if failure else {'window_end': window_end}}))
    (run / 'STOP').write_text(stop or ('learner failure x' if failure else 'learner ended'))
    if disk:
        (run / 'disk-stop').write_text('DISK STOP /x free 9%')


def test_outcome_resumes_only_the_learner_fence_and_dead_jobs(tmp_path):
    fence = "BoundaryError: input_owner_fenced_restore_last_checkpoint ..."
    cases = [({}, 'normal'), ({'failure': fence}, 'resumable'),
             ({'failure': 'BoundaryError: unresolved_worlds_after_drain'}, 'failure'),
             # review D2: a "service failure" STOP alone would repeat (gpu preflight, MPS...)
             ({'failure': 'BoundaryError: unresolved_worlds_after_drain',
               'stop': 'service failure 2 exited'}, 'failure'),
             ({'report': False}, 'resumable'), ({'report': False, 'rc': 9}, 'failure'),
             # review D1: the learner stopped cleanly on a service STOP, the job exits 5
             ({'stop': 'service failure handoff EOF', 'rc': 5}, 'failure'),
             ({'stop': 'service failure handoff EOF', 'rc': 0}, 'failure'),
             ({'rc': 5}, 'failure'), ({'window_end': None}, 'failure'),
             ({'disk': True}, 'disk_stop'), ({'failure': fence, 'disk': True}, 'disk_stop')]
    for i, (kw, kind) in enumerate(cases):
        write_outcome(tmp_path / str(i), **kw)
        assert supervise.outcome(tmp_path / str(i))['kind'] == kind, kw


class FakeCluster:
    """launch.main / squeue / scancel / CP records for one chain, all in memory."""

    def __init__(self, root, endings):
        self.root, self.endings, self.argvs, self.alive = root, list(endings), [], set()
        self.now = 1000.0
        self.records = []

    def launch_main(self, argv):
        self.argvs.append(argv)
        tag = argv[1]
        run = self.root / f'run-{tag}'
        run.mkdir(parents=True)
        number = len(self.argvs)
        jobs = {'gpu': f'{number}00', 'env': f'{number}01'}
        (run / 'jobs.json').write_text(json.dumps(jobs))
        self.alive = set(jobs.values())
        ending = self.endings.pop(0)
        write_outcome(run, **ending)
        self.records.append({'training_generation': f'g-fc-{tag}', 'published_at': self.now,
                             'checkpoint_artifact_id': f'cp-{tag}'})
        return 0

    def squeue(self, ids):
        self.alive.discard(next(i for i in ids if i.endswith('00')))  # GPU job ends
        return {i for i in self.alive if i in ids}

    def scancel(self, ids):
        self.alive -= set(ids)

    def sleep(self, seconds):
        self.now += seconds


def chain_for(tmp_path, endings, **sets):
    tmp_path.mkdir(parents=True, exist_ok=True)
    site = tmp_path / 'site.toml'
    text = SITE.read_text().replace('/scratch/USER/fastchain"', f'{tmp_path}"', 1)
    text = text.replace('/scratch/USER/fastchain/peer"', f'{tmp_path / "peer"}"', 1)
    site.write_text(text)
    cluster = FakeCluster(tmp_path, endings)
    args = __import__('argparse').Namespace(
        chain='lr1', profile=PROFILE, site=site, cards=2, card_type='A100-40', layout=None,
        set=[f'{k}={v}' for k, v in sets.items()], resume_checkpoint=None,
        eval_execution=None, eval_manifest=None, eval_baseline_plan=None,
        eval_no_baseline=False, eval_a10_manifest=None, eval_slot_registered=False,
        dry_run=False)
    chain = supervise.Chain(args, clock=lambda: cluster.now, sleep=cluster.sleep,
                            launch_main=cluster.launch_main, squeue=cluster.squeue,
                            scancel=cluster.scancel, checkpoints=lambda g: cluster.records)
    return chain, cluster


FENCE = {'failure': 'BoundaryError: input_owner_fenced_restore_last_checkpoint'}


def test_chain_resumes_a_fence_from_the_newest_cp_in_a_new_run_and_generation(tmp_path):
    chain, cluster = chain_for(tmp_path, [FENCE, {}])
    assert chain.run() == 0
    first, second = cluster.argvs
    assert first[1] == 'lr1' and '--resume-checkpoint' not in first
    assert second[1] == 'lr1-r1'
    assert second[second.index('--resume-checkpoint') + 1] == 'cp-lr1'
    assert '--long-run' in second and '--chain-json' in second
    facts = json.loads((tmp_path / 'chain-lr1' / 'chain-1.json').read_text())
    assert facts['id'] == 'c-lr1' and facts['generation'] == 1
    final = json.loads((tmp_path / 'chain-lr1' / 'FINAL.json').read_text())
    assert final['ok'] and final['resumes'] == 1
    assert 'restore_last_checkpoint' in final['first_cause']['failure']


def test_chain_writes_the_run_record_at_checkpoints_resume_and_end(tmp_path):
    chain, cluster = chain_for(tmp_path, [FENCE, {}])
    launch_main = cluster.launch_main

    def launch_with_run(argv):
        code = launch_main(argv)
        run = tmp_path / f'run-{argv[1]}'
        (run / 'plan.json').write_text(json.dumps(plan_for(cards=2)))
        (run / 'learner-events.jsonl').write_text(json.dumps(
            {'t': 1.0, 'kind': 'training_started'}) + '\n')
        return code
    chain.launch_main = launch_with_run
    assert chain.run() == 0
    chain_dir = tmp_path / 'chain-lr1'
    lines = [json.loads(x) for x in (chain_dir / 'run-records.jsonl').read_text().splitlines()]
    assert [(x['chain']['why'], x['chain']['generation']) for x in lines] == [
        ('generation_end', 0), ('final', 1)]
    latest = json.loads((chain_dir / 'run-record.json').read_text())
    assert latest['schema'] == readout.SCHEMA and latest['chain']['resumes'] == 1
    assert latest['identity']['name'] == '2卡 · 1推理+1训练 · A100-40'
    assert json.loads((chain_dir / 'FINAL.json').read_text())['run_record'] == str(
        chain_dir / 'run-record.json')
    # A new CP is seen once, and only from whole lines.
    run = tmp_path / 'run-lr1-r1'
    events = run / 'learner-events.jsonl'
    assert not chain.new_checkpoint(run)
    with events.open('a') as handle:
        handle.write('{"t": 2.0, "kind": "checkpoint_published", "checkpoint_artifact_id"')
    assert not chain.new_checkpoint(run)
    with events.open('a') as handle:
        handle.write(': "cp-x"}\n')
    assert chain.new_checkpoint(run) and not chain.new_checkpoint(run)
    chain.tick(run)  # nothing new: no record
    assert len((chain_dir / 'run-records.jsonl').read_text().splitlines()) == 2
    with events.open('a') as handle:
        handle.write('{"t": 3.0, "kind": "checkpoint_published", "checkpoint_artifact_id": '
                     '"cp-y"}\n')
    chain.tick(run)
    last = json.loads((chain_dir / 'run-records.jsonl').read_text().splitlines()[-1])
    assert last['chain']['why'] == 'checkpoint'
    assert [c['checkpoint_artifact_id'] for c in last['checkpoints']] == ['cp-x', 'cp-y']
    # One readout per generation: each record read only what was appended since the last.
    assert chain.reader.run == run and chain.reader.read_bytes == events.stat().st_size
    assert chain.reader.seed_runs == [tmp_path / 'run-lr1']  # windows run on (#556)
    (run / 'plan.json').write_text('{')  # a broken record is noted, never stops the run
    assert not chain.record(run, 'checkpoint')
    assert chain.state['log'][-1]['kind'] == 'run_record_failed'
    assert chain.reader is None  # the next record starts a fresh readout
    # A read error on the events file (NFS) is noted; the tick goes on, no CP this time.
    events.rename(run / 'events.bak')
    events.mkdir()  # opening it raises IsADirectoryError, an OSError
    chain.state['record_offsets'][run.name] = 0
    chain.tick(run)
    assert chain.state['log'][-1]['kind'] == 'checkpoint_scan_failed'
    assert chain.state['record_offsets'][run.name] == 0  # the next tick tries again


def test_chain_stops_loudly_when_resumes_run_out_and_keeps_the_first_cause(tmp_path):
    chain, cluster = chain_for(tmp_path, [FENCE, FENCE], resume_max_total=1)
    assert chain.run() == 1
    final = json.loads((tmp_path / 'chain-lr1' / 'FINAL.json').read_text())
    assert 'resume budget exhausted' in final['cause'] and len(cluster.argvs) == 2
    assert final['first_cause']['kind'] == 'resumable'


def test_chain_never_resumes_other_failures_and_allows_three_resumes(tmp_path):
    chain, cluster = chain_for(tmp_path / 'a', [{'failure': 'ValueError: boom'}])
    assert chain.run() == 1 and len(cluster.argvs) == 1
    chain, cluster = chain_for(tmp_path / 'b', [FENCE, {'report': False},
                                                FENCE, FENCE])
    assert chain.run() == 1 and len(cluster.argvs) == 4
    assert 'resume budget exhausted (3 of 3)' in chain.state['final']['cause']
    assert 'restore_last_checkpoint' in chain.state['final']['first_cause']['failure']


def test_chain_never_resumes_across_a_code_or_profile_change(tmp_path):
    chain, cluster = chain_for(tmp_path, [FENCE, {}])
    original = supervise.identity
    calls = []

    def changing(*a):
        calls.append(1)
        value = original(*a)
        return {**value, 'profile': 'changed'} if len(calls) > 1 else value
    supervise.identity = changing
    try:
        assert chain.run() == 1
    finally:
        supervise.identity = original
    assert 'never resumed across a change' in chain.state['final']['cause']
    assert len(cluster.argvs) == 1


def test_chain_ends_on_a_submit_failure_without_repeating_it(tmp_path):
    chain, cluster = chain_for(tmp_path, [])

    def broken(argv):
        cluster.argvs.append(argv)
        raise SystemExit('refused after prep submission')
    chain.launch_main = broken
    assert chain.run() == 1 and len(cluster.argvs) == 1
    assert 'not repeated' in chain.state['final']['cause']


def test_chain_records_the_window_lost_to_gpu_queue_time(tmp_path, capsys):
    """The window is fixed at chain start; queue time is printed and summed, not given back."""
    chain, cluster = chain_for(tmp_path, [FENCE, {}])
    launch_main = cluster.launch_main

    def queued(argv):
        cluster.now += 3600 if len(cluster.argvs) == 0 else 600  # launch returns at GPU start
        return launch_main(argv)
    chain.launch_main = queued
    assert chain.run() == 0
    first, second = chain.state['generations']
    assert (first['queue_seconds'], second['queue_seconds']) == (3600, 600)
    assert first['window_left_seconds'] == round(chain.state['deadline_at'] - 1000 - 3600)
    final = json.loads((tmp_path / 'chain-lr1' / 'FINAL.json').read_text())
    assert final['ok'] and final['queue_seconds_total'] == 4200
    assert 'generation 0 started after 3600 s in the queue' in capsys.readouterr().err


def chain_with_recipe(tmp_path, text, endings):
    """A chain whose env-recipe.txt appears while generation 0 runs (the chain dir exists
    only once the chain started)."""
    chain, cluster = chain_for(tmp_path, endings)
    launch_main = cluster.launch_main

    def launch_then_edit(argv):
        (tmp_path / 'chain-lr1' / 'env-recipe.txt').write_text(text)
        return launch_main(argv)
    chain.launch_main = launch_then_edit
    return chain, cluster


def test_chain_takes_the_env_recipe_at_a_generation_boundary(tmp_path):
    chain, cluster = chain_with_recipe(
        tmp_path, '# I8\nascension_mix={ 0 = 0.5, 10 = 0.5 }\n\n replay_k = 4\n', [FENCE, {}])
    assert chain.run() == 0
    first, second = cluster.argvs
    assert [a for a in first if a.startswith(('ascension_mix', 'replay_k'))] == []
    assert [second[i + 1] for i, a in enumerate(second) if a == '--set'][-2:] == \
        ['ascension_mix={ 0 = 0.5, 10 = 0.5 }', 'replay_k=4']
    generations = chain.state['generations']
    assert generations[0]['env_recipe'] == [] and generations[1]['env_recipe'][-1] == \
        'replay_k=4'
    assert json.loads((tmp_path / 'chain-lr1' / 'FINAL.json').read_text())['ok']


def test_chain_ends_on_an_env_recipe_outside_the_env_knobs_or_refused(tmp_path):
    for name, text, message in (('learner', 'env_slots=8\n', 'only'),
                                ('value', 'replay_k=40\n', 'replay_k'),
                                ('bare', 'replay_k\n', 'only')):
        chain, cluster = chain_with_recipe(tmp_path / name, text, [FENCE, {}])
        assert chain.run() == 1 and len(cluster.argvs) == 1, name
        cause = chain.state['final']['cause']
        assert cause.startswith('generation 1 not submitted') and message in cause, cause


def test_readout_reports_disk_watermarks(tmp_path):
    (tmp_path / 'disk.txt').write_text('1 /run free_pct=15 avail=15 size=100\n'
                                       '2 /tmp free_pct=40 avail=40 size=100\n')
    (tmp_path / 'disk-watermark.txt').write_text('DISK WARN /run free 15% < 20% now\n')
    disk = readout.disk_edge(tmp_path)
    assert disk['free_pct'] == {'/run': 15.0, '/tmp': 40.0} and len(disk['events']) == 1


def test_snapshot_test_only_and_queue_are_only_printed():
    plan = plan_for(cards=3)
    for start in (None, 99_999.0, 0.0):
        result = preflight.check_gpu_request(plan, {**good_facts(plan),
                                                    'gpu_test_start_seconds': start})
        assert result.ok and not result.warn and 'test-only' in result.detail
    assert 'PASS gpu_request' in preflight.report([result])
    busy = {'fairshare': 0.0167, 'priority_estimate': 10167, 'partitions': {
        'gpu': {'name': 'GPU-PARTITION', 'pending': 30, 'above': 0},
        'prep': {'name': 'CPU-PARTITION', 'pending': 1000, 'above': 829}}}
    for queue, warn in ((busy, True), ({}, True), (good_facts(plan)['queue'], False)):
        result = preflight.check_queue({'queue': queue})
        assert result.ok and result.warn == warn
    assert ('gpu partition GPU-PARTITION: 0 of 30 pending jobs rank above it; prep partition '
            'CPU-PARTITION: 829 of 1000') in preflight.check_queue({'queue': busy}).detail
    prep = preflight.check_prep_schedulable(plan, good_facts(plan))
    assert prep.ok and '8 prep hosts in 2 plain jobs' in prep.detail


def test_missing_source_features_only_warn():
    plan = plan_for(cards=3, shards_adopt_unsharded_checkpoint=True)
    facts = {**good_facts(plan), 'src_features': {
        name: name != 'adopt_unsharded' for name in launch.FEATURES}}
    result = preflight.check_features(plan, facts)
    assert result.ok and result.warn and 'adopt_unsharded' in result.detail


def test_new_knobs_name_their_source_features():
    need = preflight.required_features(plan_for(cards=3))
    assert {'retention', 'mu_archive', 'r7_max_games', 'r7_audit_mu',
            'segment_format_3'} <= set(need)
    assert 'segment_format_4' not in need
    off = preflight.required_features(plan_for(
        cards=3, release_trained_segments=False, r7_games_per_hour=0,
        segment_format='stpd/rl-fast-segment-4'))
    assert not {'retention', 'mu_archive', 'r7_audit_mu', 'segment_format_3'} & set(off)
    assert 'segment_format_4' in off
    a10 = preflight.required_features(plan_for(cards=3, ascension_mix={'0': 0.5, '10': 0.5}))
    assert 'ascension' in a10
    # A2: only a weight other than 1 / 1 / 1 needs a source that knows it
    assert 'hp_combat_weights' not in need and 'hp_combat_weights' not in off
    weighted = preflight.required_features(plan_for(
        cards=3, segment_format='stpd/rl-fast-segment-4',
        hp_combat_weights={'normal': 1.0, 'elite': 1.0, 'boss': 0.3}))
    assert 'hp_combat_weights' in weighted


def test_profile_relations_are_refused_at_load():
    with pytest.raises(run_profile.ProfileError, match='disk_stop_free_pct'):
        run_profile.load_profile(PROFILE, ['disk_stop_free_pct=30'])
    # the segment format is its own knob: 3 (default) or 4, both carry the level
    with pytest.raises(run_profile.ProfileError, match='segment_format'):
        run_profile.load_profile(PROFILE, ['segment_format=stpd/rl-fast-segment-2'])
    with pytest.raises(run_profile.ProfileError, match='unknown'):
        run_profile.load_profile(PROFILE, [
            'service_kwargs={ segment_format = "stpd/rl-fast-segment-4" }'])
    assert run_profile.values(run_profile.load_profile(PROFILE, [
        'ascension_mix={ 0 = 0.5, 10 = 0.5 }', 'segment_format=stpd/rl-fast-segment-4'
    ]))['segment_format'] == 'stpd/rl-fast-segment-4'
    with pytest.raises(run_profile.ProfileError, match='summing to 1'):
        run_profile.load_profile(PROFILE, ['ascension_mix={ 0 = 0.5, 10 = 0.4 }'])
    # A2: the HP-change weight per combat type, default 1 / 1 / 1; others need format 4
    profile = run_profile.load_profile(PROFILE)
    assert profile['hp_combat_weights']['value'] == profile['hp_combat_weights']['default'] \
        == {'normal': 1.0, 'elite': 1.0, 'boss': 1.0}
    weighted = 'hp_combat_weights={ normal = 1.0, elite = 1.0, boss = 0.3 }'
    with pytest.raises(run_profile.ProfileError, match='hp_combat_weights.*segment_format'):
        run_profile.load_profile(PROFILE, [weighted])
    assert run_profile.values(run_profile.load_profile(PROFILE, [
        weighted, 'segment_format=stpd/rl-fast-segment-4']))['hp_combat_weights'] == {
        'normal': 1.0, 'elite': 1.0, 'boss': 0.3}
    for bad in ('{ normal = 1.0, elite = 1.0 }', '{ normal = 1, elite = 1, boss = -0.3 }',
                '{ normal = 1, elite = 1, boss = 1, unknown = 1 }'):
        with pytest.raises(run_profile.ProfileError, match='hp_combat_weights'):
            run_profile.load_profile(PROFILE, ['hp_combat_weights=' + bad,
                                               'segment_format=stpd/rl-fast-segment-4'])
    with pytest.raises(run_profile.ProfileError, match='eval_a10_suite'):
        run_profile.load_profile(PROFILE, ['eval_a10_enabled=true', 'eval_a10_suite=""'])
    a10 = run_profile.values(run_profile.load_profile(PROFILE))
    assert a10['eval_a10_enabled'] is True
    assert a10['eval_a10_suite'] == 'managed-rl5-learning-curve-50-20261114-Defect-a10'
    assert run_profile.values(run_profile.load_profile(PROFILE))['ascension_mix'] == {'0': 1.0}


def test_ascension_plan_is_largest_remainder():
    assert run_profile.ascension_plan({'0': 1.0}, 10) == [0] * 10
    assert run_profile.ascension_plan({'0': 0.5, '10': 0.5}, 10) == [0] * 5 + [10] * 5
    assert run_profile.ascension_plan({'0': 0.9, '10': 0.1}, 12) == [0] * 11 + [10]
    plan = run_profile.ascension_plan({'0': 0.34, '5': 0.33, '10': 0.33}, 10)
    assert len(plan) == 10 and plan.count(0) == 4 and plan.count(5) == 3


def test_job_env_carries_r7_and_ascension_knobs():
    env = launch.job_env(plan_for(cards=3))
    assert 'FC_ENV_NODES=' in env and 'FC_R7_GAMES_PER_HOUR=10' in env
    assert """FC_ASCENSION_MIX='{"0":1.0}'""" in env and 'FC_RELEASE_TRAINED_SEGMENTS=1' in env
    # default env recipe = the RL4.2 behaviour: one control arm at T 1 / mix 0, K = 1
    assert 'FC_REPLAY_K=1' in env and 'FC_EXPLORATION_LADDER=\'{"control":{"combat":{' \
        '"temperature":1.0,"uniform_mix":0.0},"noncombat":{"temperature":1.0,' \
        '"uniform_mix":0.0},"share":1.0}}\'' in env
    assert 'FC_SAMPLING_TEMPERATURE_MIN=1.0' in env  # A3: today's [1, 3] validation


# ---------------- environment-side recipe (exploration ladder, K replay) ----------------

THREE_ARMS = ('exploration_ladder={ control = { share = 0.34, combat = { temperature = 1.0, '
              'uniform_mix = 0.0 }, noncombat = { temperature = 1.0, uniform_mix = 0.0 } }, '
              'mid = { share = 0.33, combat = { temperature = 1.15, uniform_mix = 0.005 }, '
              'noncombat = { temperature = 1.6, uniform_mix = 0.01 } }, '
              'high = { share = 0.33, combat = { temperature = 1.4, uniform_mix = 0.005 }, '
              'noncombat = { temperature = 2.5, uniform_mix = 0.01 } } }')


def test_exploration_ladder_and_replay_k_are_validated():
    values = run_profile.values(run_profile.load_profile(PROFILE, [THREE_ARMS, 'replay_k=8']))
    assert sorted(values['exploration_ladder']) == ['control', 'high', 'mid']
    assert values['replay_k'] == 8
    arm = '{ share = 1.0, combat = %s, noncombat = { temperature = 1.0, uniform_mix = 0.0 } }'
    for bad, match in (
            ('{ "bad arm" = ' + arm % '{ temperature = 1.0, uniform_mix = 0.0 }' + ' }',
             'bad arm'),
            ('{ a = ' + arm % '{ temperature = 0.5, uniform_mix = 0.0 }' + ' }',
             'a.combat: temperature 0.5 below sampling_temperature_min 1.0'),
            ('{ a = ' + arm % '{ temperature = 0.2, uniform_mix = 0.0 }' + ' }',
             r'temperature in \[0.25, 3\]'),
            ('{ a = ' + arm % '{ temperature = 1.0, uniform_mix = 0.2 }' + ' }',
             r'uniform_mix in \[0, 0.1\]'),
            ('{ a = ' + arm % '{ temperature = 1.0 }' + ' }', 'exactly'),
            ('{ a = ' + (arm % '{ temperature = 1.0, uniform_mix = 0.0 }').replace(
                '1.0, combat', '0.5, combat') + ' }', 'summing to 1')):
        with pytest.raises(run_profile.ProfileError, match=match):
            run_profile.load_profile(PROFILE, [f'exploration_ladder={bad}'])
    with pytest.raises(run_profile.ProfileError, match='replay_k 13 must not exceed env_slots'):
        run_profile.load_profile(PROFILE, ['replay_k=13'])
    # A3: an arm below 1 only with the run's lower minimum, itself in [0.25, 1]
    bold = arm % '{ temperature = 0.7, uniform_mix = 0.0 }'
    with pytest.raises(run_profile.ProfileError, match='temperature 0.7 below'):
        run_profile.load_profile(PROFILE, [f'exploration_ladder={{ bold = {bold} }}'])
    values = run_profile.values(run_profile.load_profile(PROFILE, [
        f'exploration_ladder={{ bold = {bold} }}', 'sampling_temperature_min=0.7']))
    assert values['exploration_ladder']['bold']['combat']['temperature'] == 0.7
    for low in ('0.2', '1.5'):
        with pytest.raises(run_profile.ProfileError, match='sampling_temperature_min'):
            run_profile.load_profile(PROFILE, [f'sampling_temperature_min={low}'])
    with pytest.raises(run_profile.ProfileError, match='replay_k'):
        run_profile.load_profile(PROFILE, ['replay_k=0'])


def test_save_starts_knob_defaults_off_and_is_validated():
    """RL5-METHODS B: share 0 by default (no save, no save start); the env exports it as JSON."""
    values = run_profile.values(run_profile.load_profile(PROFILE, []))
    assert values['save_starts'] == {'share': 0.0, 'random_saves': 2,
                                     'act_weights': [1.0, 1.0, 1.0], 'died_later_weight': 0.0,
                                     'uses_per_save': 16, 'pool_gb': 10.0}
    assert "export FC_SAVE_STARTS='{\"act_weights\":[1.0,1.0,1.0]," in '\n'.join(
        run_profile.env_lines(values))
    on = ('save_starts={ share = 0.3, random_saves = 2, act_weights = [1.0, 1.0, 0.5], '
          'died_later_weight = 0.0, uses_per_save = 16, pool_gb = 10.0 }')
    on_values = run_profile.values(run_profile.load_profile(PROFILE, [on]))
    assert on_values['save_starts']['share'] == 0.3
    for old, new, match in (('share = 0.3', 'share = 1.5', 'share'),
                            ('[1.0, 1.0, 0.5]', '[0, 0, 0]', 'act_weights'),
                            ('[1.0, 1.0, 0.5]', '[1.0, 1.0]', 'act_weights'),
                            ('uses_per_save = 16', 'uses_per_save = 0', 'uses_per_save'),
                            (', pool_gb = 10.0', '', 'exactly')):
        with pytest.raises(run_profile.ProfileError, match=match):
            run_profile.load_profile(PROFILE, [on.replace(old, new)])
    assert 'save_starts' in supervise.ENV_RECIPE_KNOBS


def test_readout_rolling_games_knob_defaults_to_100_and_is_validated():
    """#556: [100] = the readout before the knob; a list of 1-4 distinct ints in [10, 1e5]."""
    assert run_profile.values(run_profile.load_profile(PROFILE, []))[
        'readout_rolling_games'] == [100]
    on = run_profile.values(run_profile.load_profile(
        PROFILE, ['readout_rolling_games=[100, 1000]']))
    assert on['readout_rolling_games'] == [100, 1000]
    for bad in ('[]', '[5]', '[100, 100]', '[100, 200, 300, 400, 500]', '100', '[1e3]'):
        with pytest.raises(run_profile.ProfileError, match='readout_rolling_games'):
            run_profile.load_profile(PROFILE, [f'readout_rolling_games={bad}'])


def test_exploration_plan_interleaves_arms_over_the_run_drivers():
    ladder = run_profile.values(run_profile.load_profile(PROFILE, [THREE_ARMS]))[
        'exploration_ladder']
    run = run_profile.exploration_plan(ladder, 0, 300)
    assert [run.count(a) for a in ('control', 'mid', 'high')] == [102, 99, 99]
    for n in range(1, 301):  # any prefix (nodes that never join) keeps each share +-1
        assert all(abs(run[:n].count(a) - ladder[a]['share'] * n) <= 1 for a in ladder)
    # node slices (env.sh: NODEIDX x P, P drivers) put together are the run's plan
    assert sum((run_profile.exploration_plan(ladder, i * 10, 10) for i in range(30)), []) == run
    assert run_profile.exploration_plan(knobs()['exploration_ladder'], 40, 10) == \
        ['control'] * 10


def test_env_sh_gives_each_driver_its_level_and_arm(tmp_path):
    """The ASC= / ARM= lines of env.sh as they run (bash quoting of the JSON knobs)."""
    import subprocess
    lines = [line for line in (FASTCHAIN / 'env.sh').read_text().splitlines()
             if line.startswith(('ASC=', 'ARM='))]
    assert len(lines) == 2
    ladder = run_profile.values(run_profile.load_profile(PROFILE, [THREE_ARMS]))[
        'exploration_ladder']
    env = {'PY': sys.executable, 'H': str(FASTCHAIN), 'P': '10', 'NODEIDX': '1',
           'FC_ASCENSION_MIX': '{"0":0.4,"10":0.6}',
           'FC_EXPLORATION_LADDER': json.dumps(ladder, separators=(',', ':'), sort_keys=True),
           'PATH': '/usr/bin:/bin'}
    out = subprocess.run(['bash', '-c', '\n'.join(lines) + '\necho "${ASC[*]}|${ARM[*]}"'],
                         env=env, capture_output=True, text=True, check=True).stdout.strip()
    asc, arms = out.split('|')
    assert asc.split() == ['0'] * 4 + ['10'] * 6
    assert arms.split() == run_profile.exploration_plan(ladder, 10, 10)
    # A3: every driver gets the run's lowest arm temperature (the fast env checks its arm)
    assert '--temperature-min $FC_SAMPLING_TEMPERATURE_MIN' in (FASTCHAIN / 'env.sh').read_text()


FAKE_FAST_ENV = '''
import { createHash } from "node:crypto";
import { appendFileSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline";
const path = process.argv[process.argv.indexOf("--config") + 1];
const config = JSON.parse(readFileSync(path, "utf8"));
const emit = (event) => process.stdout.write(JSON.stringify(event) + "\\n");
let n = 0;
emit({ event: "ready" });
createInterface({ input: process.stdin }).on("line", line => {
  const c = JSON.parse(line);
  if (c.cmd === "open") {
    n += 1;
    // a save start ends 22 floors after its save; a game with save points reaches floor 30-34
    const fromSave = c.start.kind === "from_save";
    const id = `inst${n}`;
    const floor = fromSave ? c.start.save.floor + 22 + 17
      : c.save_points ? 30 + (n % 5) : 3 + (n % 5);
    appendFileSync(join(dirname(path), "opens.jsonl"), JSON.stringify(c) + "\\n");
    // a start the native cannot mount: a bad seed, or (save starts) a save made on floor 16
    if (c.seed.startsWith("bad") || (fromSave && c.start.save.floor === 16 - 17)) {
      emit({ event: "open_refused", slot: c.slot, game_ref: c.game_ref, reason: "reset_failed:x" });
      return;
    }
    const game = { slot: c.slot, game_ref: c.game_ref, game_instance_id: id };
    const act = (f) => f <= 17 ? 1 : f <= 33 ? 2 : 3;
    emit({ event: "opened", ...game, start: c.start });
    for (const f of c.save_points?.floors ?? []) if (f <= floor) {
      const sha256 = createHash("sha256").update(`${id}.${f}`).digest("hex");
      emit({ event: "saved", ...game, floor: f, ascension: config.host.ascension,
        character: config.host.character, decisions: f,
        public_facts: { act: act(f), floor: f, hp: 10, max_hp: 80 }, save: {
          schema: "sts2.host-runtime/managed-map-continue-1",
          path: join(c.save_points.directory, `${sha256}.json`), bytes: 80000, sha256,
          candidate: { artifact_sha256: "1".repeat(64) }, seed: c.seed, act: act(f),
          floor: f - 17, parent_game_continuity_id: `fam-${id}` } });
    }
    setTimeout(() => emit({ event: "closed", ...game, reason: "terminal", decisions: 10,
      public_facts: { act: act(floor), floor },
      terminal: { result: floor === 7 ? "victory" : "defeat" },
      evidence_failed: false }), 30);
  } else if (c.cmd === "status") {
    emit({ event: "status", counters: { decisions: 10 * n, forced_steps: 0, native_steps: 0,
      node_cpu_ms: 0, link_wait_ms: 0 }, slots: [] });
  } else if (c.cmd === "stop") { emit({ event: "stopped" }); process.exit(0); }
});
'''


def fake_driver_world(root):
    """A package whose fast env answers open/status/stop at once, plus the driver inputs."""
    dist = root / 'pkg/components/policy-runtime/dist'
    (dist / 'fast-env').mkdir(parents=True)
    (dist / 'global-program.js').write_text(
        'export function createGlobalProgramManifest() { return { input: {} }; }\n')
    (dist / 'fast-env/process.js').write_text(FAKE_FAST_ENV)
    (root / 'seeds.txt').write_text('\n'.join(f'S{i}' for i in range(2000)) + '\n')
    (root / 'progress.json').write_text('{}')
    (root / 'spec.json').write_text('{}')
    return root


def run_driver(world, out, admit, *flags, steady=1):
    import subprocess
    node = __import__('shutil').which('node')
    assert node, 'development Node is required for the env driver regression'
    argv = [node, str(FASTCHAIN / 'env_driver.mjs'), '--pkg', str(world / 'pkg'),
            '--out', str(out), '--tag', 'n0-p1', '--slots', '4', '--ascension', '0',
            '--seeds', str(world / 'seeds.txt'), '--seed-offset', '0',
            '--initial-progress', str(world / 'progress.json'),
            '--input-spec', str(world / 'spec.json'), '--link', '/nonexistent.sock',
            '--admit', str(admit), '--candidate', str(world), '--single-choice', 'omit',
            '--warm-s', '0', '--steady-s', str(steady), '--status-s', '1', *flags]
    return subprocess.run(argv, capture_output=True, text=True, timeout=60,
                          env={'PATH': '/usr/bin:/bin', 'REL': str(world)})


class AdmitAll:
    """The node admission sidecar's socket: every admit/exclude answered ok."""

    def __init__(self):
        import socket
        import tempfile
        import threading
        self.dir = Path(tempfile.mkdtemp(prefix='fc', dir='/tmp'))
        self.path = self.dir / 'admit.sock'
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.path))
        self.server.listen(4)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        import threading
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.answer, args=(client,), daemon=True).start()

    @staticmethod
    def answer(client):
        for _line in client.makefile('rb'):
            client.sendall(b'{"ok": true}\n')

    def close(self):
        import shutil
        self.server.close()
        shutil.rmtree(self.dir, ignore_errors=True)


LADDER_JSON = json.dumps({'hot': {'share': 1.0,
                                  'combat': {'temperature': 1.4, 'uniform_mix': 0.005},
                                  'noncombat': {'temperature': 2.5, 'uniform_mix': 0.01}}})


def test_env_driver_arm_sampling_and_k_replay_groups_feed_the_readout(tmp_path):
    world, admit = fake_driver_world(tmp_path), AdmitAll()
    run = tmp_path / 'run-t1'
    write_run(run, cards=1)
    out = run / 'env-n0-1' / 'p1'
    try:
        done = run_driver(world, out, admit.path, '--exploration-ladder', LADDER_JSON,
                          '--arm', 'hot', '--replay-k', '3')
    finally:
        admit.close()
    assert done.returncode == 0, done.stderr
    sampling = json.loads((out / 'config.json').read_text())['inference']['sampling']
    assert sampling == {'mode': 'temperature_uniform', 'seed': 'wp8-n0-p1', 'arm_id': 'hot',
                        'classes': {'combat': {'temperature': 1.4, 'uniform_mix': 0.005},
                                    'noncombat': {'temperature': 2.5, 'uniform_mix': 0.01}}}
    opens = [json.loads(line) for line in (out / 'opens.jsonl').read_text().splitlines()]
    assert len(opens) > 12
    # K = 3: every seed of the list opens three times in a row, then the next seed
    assert [o['seed'] for o in opens] == [f'S{i // 3}' for i in range(len(opens))]
    assert all(o['start'] == {'kind': 'new_run'} for o in opens)
    closed = [r for r in readout.jsonl(out / 'driver.jsonl') if r['event'] == 'closed']
    assert closed and all(r['arm'] == 'hot' and r['ascension'] == 0 and r['replay_k'] == 3
                          for r in closed)
    for r in closed:
        i = int(r['game_ref'][1:]) - 1
        assert (r['group'], r['member'], r['seed']) == (f'n0-p1-k{i // 3 + 1}', i % 3,
                                                         f'S{i // 3}')
    recipe = readout.recipe_edge(run)
    admitted = {r['game_ref'] for r in readout.jsonl(out / 'driver.jsonl')
                if r['event'] == 'admission' and r['ok']}
    complete = [r for r in closed if r['game_ref'] in admitted]
    hot = recipe['arm']['hot']
    assert hot['games'] == len(complete) and hot['rows'] == 10 * len(complete)
    assert hot['wins'] == sum(r['terminal'] == 'victory' for r in complete)
    assert hot['mean_floor'] == pytest.approx(sum(r['floor'] for r in complete) / len(complete))
    assert recipe['ascension']['0']['games'] == len(complete)
    k3 = recipe['k']['3']
    assert k3['groups'] == len({r['group'] for r in closed}) and k3['full_groups'] >= 3
    assert k3['mean_floor_range'] is not None
    # A group is held only until all K of its games closed (its share stays in k_rows).
    closes = {}
    for r in closed:
        closes[r['group']] = closes.get(r['group'], 0) + 1
    reader = readout.Readout(run).update()
    assert {name.rsplit('/', 1)[1] for name in reader.groups} == {
        g for g, n in closes.items() if n < 3}
    assert len(reader.groups) < k3['groups'] and reader.recipe() == recipe
    built = readout.build(run, 1200)
    assert built['recipe'] == recipe
    text = readout.render(built)
    assert 'arm hot' in text and 'ascension A0' in text and 'k 3' in text


def test_env_driver_puts_the_temperature_minimum_in_the_config_only_below_1(tmp_path):
    """A3: --temperature-min 1 (the default env.sh passes) leaves config.json as it was
    without the flag; a lower one reaches the fast env as inference.temperature_min."""
    world, admit = fake_driver_world(tmp_path), AdmitAll()
    bold = json.dumps({'bold': {'share': 1.0,
                                'combat': {'temperature': 0.7, 'uniform_mix': 0.0},
                                'noncombat': {'temperature': 2.0, 'uniform_mix': 0.02}}})
    configs = {}
    try:
        for name, flags in (('before', ()), ('one', ('--temperature-min', '1')),
                            ('bold', ('--temperature-min', '0.7'))):
            ladder, arm = (bold, 'bold') if name == 'bold' else (LADDER_JSON, 'hot')
            done = run_driver(world, tmp_path / name, admit.path, '--exploration-ladder',
                              ladder, '--arm', arm, '--replay-k', '1', *flags)
            assert done.returncode == 0, done.stderr
            configs[name] = (tmp_path / name / 'config.json').read_text().replace(
                str(tmp_path / name), 'OUT')
    finally:
        admit.close()
    assert configs['one'] == configs['before']
    assert 'temperature_min' not in json.loads(configs['before'])['inference']
    inference = json.loads(configs['bold'])['inference']
    assert inference['temperature_min'] == 0.7
    assert inference['sampling']['classes'] == {
        'combat': {'temperature': 0.7, 'uniform_mix': 0.0},
        'noncombat': {'temperature': 2.0, 'uniform_mix': 0.02}}
    flags = [r for r in readout.jsonl(tmp_path / 'bold' / 'driver.jsonl')
             if r['event'] == 'driver_flags']
    assert flags[0]['temperature_min'] == 0.7


SAVE_STARTS_OFF = json.dumps(run_profile.values(run_profile.load_profile(PROFILE, []))[
    'save_starts'], separators=(',', ':'), sort_keys=True)


def test_env_driver_with_save_starts_off_opens_exactly_as_before_and_refuses_bad_flags(tmp_path):
    """The default knob (share 0): the same open commands as a driver without the flag."""
    assert json.loads(SAVE_STARTS_OFF)['share'] == 0
    world, admit = fake_driver_world(tmp_path), AdmitAll()
    runs = {}
    try:
        for name, extra in (('none', ()), ('off', ('--save-starts', SAVE_STARTS_OFF,
                                                    '--save-pool', str(tmp_path / 'pool')))):
            out = tmp_path / name / 'env-n0' / 'p0'
            write_run(tmp_path / name, cards=1)
            done = run_driver(world, out, admit.path, '--exploration-ladder', LADDER_JSON,
                              '--arm', 'hot', '--replay-k', '2', *extra)
            assert done.returncode == 0, done.stderr
            runs[name] = [json.loads(line)
                          for line in (out / 'opens.jsonl').read_text().splitlines()]
    finally:
        admit.close()

    def strip(command):
        return {k: v for k, v in command.items() if k not in ('command_id', 'slot')}
    n = min(len(runs['none']), len(runs['off']))
    assert n > 8 and [strip(o) for o in runs['off'][:n]] == [strip(o) for o in runs['none'][:n]]
    assert set(runs['off'][0]) == {'schema', 'cmd', 'command_id', 'slot', 'game_ref', 'seed',
                                   'initial_progress', 'start', 'r7', 'retain_originals'}
    assert [o['seed'] for o in runs['off'][:n]] == [f'S{i // 2}' for i in range(n)]
    assert all(o['start'] == {'kind': 'new_run'} for o in runs['off'])
    assert not (tmp_path / 'pool').exists()
    # #553 should-fix 1: nor does the readout change: no save counters, no save-start table,
    # every close in the arm tables.
    off = readout.jsonl(tmp_path / 'off' / 'env-n0' / 'p0' / 'driver.jsonl')
    assert not [r for r in off if r['event'] in ('save_counters', 'save_pooled')]
    record = readout.build(tmp_path / 'off', 1200)
    assert record['save_starts'] == {}
    assert set(record['recipe']['start']) == {'new_run'}
    assert record['recipe']['arm']['hot'] == record['recipe']['start']['new_run']
    assert 'save starts:' not in readout.render(record)
    for flags, message in (
            (('--exploration-ladder', LADDER_JSON, '--arm', 'cold', '--replay-k', '1'), '--arm'),
            (('--exploration-ladder', LADDER_JSON, '--arm', 'hot'), '--replay-k'),
            (('--exploration-ladder', LADDER_JSON, '--arm', 'hot', '--replay-k', '5'),
             'at most --slots 4'),
            (('--arm', 'hot', '--replay-k', '1'), '--exploration-ladder')):
        refused = run_driver(world, tmp_path / 'refused', tmp_path / 'none.sock', *flags)
        assert refused.returncode == 2 and message in refused.stderr, flags


def test_env_driver_save_starts_pool_draw_refusals_and_readout(tmp_path):
    """Share 0.5: new runs save into the node pool, save starts claim one use per game (a K
    group shares one save), a refused load is sealed and its family paused (never reopened),
    and the readout reports save starts apart from the control arm's rolling results."""
    world, admit = fake_driver_world(tmp_path), AdmitAll()
    run = tmp_path / 'run-s'
    write_run(run, cards=1)
    out, pool = run / 'env-n0-1' / 'p1', tmp_path / 'pool'
    knob = dict(json.loads(SAVE_STARTS_OFF), share=0.5, uses_per_save=4)
    try:
        done = run_driver(world, out, admit.path, '--exploration-ladder', LADDER_JSON,
                          '--arm', 'hot', '--replay-k', '2', '--save-starts', json.dumps(knob),
                          '--save-pool', str(pool), steady=3)
    finally:
        admit.close()
    assert done.returncode == 0, done.stderr
    opens = [json.loads(line) for line in (out / 'opens.jsonl').read_text().splitlines()]
    new = [o for o in opens if o['start']['kind'] == 'new_run']
    starts = [o for o in opens if o['start']['kind'] == 'from_save']
    assert new and starts and all({16, 32, 47} <= set(o['save_points']['floors'])
                                  and len(o['save_points']['floors']) == 5 for o in new)
    assert all('save_points' not in o for o in starts)
    log = readout.jsonl(out / 'driver.jsonl')
    refused = [r for r in log if r['event'] == 'open_refused']
    started = [r for r in log if r['event'] == 'opened' and r['start'] == 'from_save']
    # one use per game: no (save, use) twice; a K group = one save
    uses = [(r['save'], r['use']) for r in started + refused]
    assert started and len(uses) == len(set(uses))
    groups = {}
    for r in started + refused:
        groups.setdefault(r['group'], set()).add(r['save'])
    assert all(len(saves) == 1 for saves in groups.values())
    assert all(r['seed'].startswith('S') and r['save_floor'] != 16 for r in started)
    ledger = [json.loads(line) for line in (pool / 'ledger.jsonl').read_text().splitlines()]
    by_save = {line['id'][:16]: line for line in ledger if line['kind'] == 'save'}
    pause_at = {line['family']: i for i, line in enumerate(ledger) if line['kind'] == 'pause'}
    assert refused and all(r['reason'] == 'reset_failed:x' for r in refused)
    assert {by_save[r['save']]['family'] for r in refused} == set(pause_at)
    # a paused family's save is never claimed after its pause line
    assert all(i < pause_at.get(line['family'], 1 << 30) for i, line in enumerate(ledger)
               if line['kind'] == 'claim')
    summary = json.loads((out / 'summary.json').read_text())['save_starts']
    assert summary['load_failed'] == len(refused) and summary['saves_pooled'] > 0
    reader = readout.Readout(run).update()
    saves = reader.save_starts()
    pooled = sum(1 for r in log if r['event'] == 'save_pooled')
    assert sum(saves['pooled_by_act'].values()) == pooled == summary['saves_pooled']
    admitted = {r['game_ref'] for r in log if r['event'] == 'admission' and r['ok']}
    complete = [r for r in log if r['event'] == 'closed' and r['start'] == 'from_save'
                and r['game_ref'] in admitted]
    assert sum(row['games'] for row in saves['by_start_act'].values()) == len(complete) > 0
    assert all(row['mean_floors_gained'] == 22 for row in saves['by_start_act'].values())
    recipe = reader.recipe()
    assert recipe['start']['from_save']['games'] == len(complete)
    # Per-arm and K tables hold the new runs only (save starts: the start table).
    assert recipe['arm'] == {'hot': recipe['start']['new_run']}
    assert sum(row['games'] for row in recipe['k'].values()) == recipe['arm']['hot']['games']
    # A3's per-arm results hold the arm's new runs only, too; no control-arm game to roll.
    played = reader.training_games()
    assert played['run']['all']['games'] == 0 and set(played['arms']) == {'hot'}
    assert played['arms']['hot']['run']['all']['games'] == recipe['arm']['hot']['games']
    text = readout.render(readout.build(run, 1200))
    assert 'save starts: pooled per act' in text and 'effective share' in text
    # #553 review C: cumulative counters on every status, the last one at exit = summary;
    # the readout shows the effective share and the counters while the driver still runs.
    lines = [r for r in log if r['event'] == 'save_counters']
    assert len(lines) >= 3
    assert {k: v for k, v in lines[-1].items() if k not in ('t', 'event')} == summary
    assert all(a['tickets_claimed'] <= b['tickets_claimed']
               and a['from_save_opened'] <= b['from_save_opened']
               for a, b in zip(lines, lines[1:], strict=False))
    opened = {}
    for r in log:
        if r['event'] == 'opened':
            opened[r['start']] = opened.get(r['start'], 0) + 1
    assert saves['opened_by_start'] == opened and opened['from_save'] == len(started)
    assert saves['effective_share'] == pytest.approx(len(started) / sum(opened.values()))
    assert saves['drivers_reporting'] == 1
    assert saves['driver_counters']['load_failed'] == summary['load_failed'] == len(refused)
    assert saves['driver_counters']['new_run_without_ticket'] == summary[
        'new_run_without_ticket']
    assert not any(key.startswith('pool_') for key in saves['driver_counters'])
    running = tmp_path / 'run-running'
    write_run(running, cards=1)
    cut = next(i for i, r in enumerate(log) if r['event'] == 'save_counters'
               and r['from_save_opened'] > 0)
    (running / 'env-n0-1' / 'p1').mkdir(parents=True)
    (running / 'env-n0-1' / 'p1' / 'driver.jsonl').write_text(jsonl_text(log[:cut + 1]))
    early = readout.build(running, 1200)['save_starts']
    assert early['drivers_reporting'] == 1 and early['effective_share'] > 0
    assert early['driver_counters']['from_save_opened'] == log[cut]['from_save_opened']


def test_readout_recipe_counts_only_admitted_games_with_evidence(tmp_path):
    driver = tmp_path / 'env-n0-1' / 'p1'
    driver.mkdir(parents=True)

    def closed(ref, group, floor, reason='terminal', result='defeat', failed=False, arm='mid',
               start='new_run'):
        return {'event': 'closed', 't': 1000 + int(ref[1:]), 'game_ref': ref,
                'reason': reason, 'arm': arm,
                'ascension': 10, 'replay_k': 2, 'group': group, 'member': 0, 'seed': 's',
                'start': start, 'decisions': 100, 'floor': floor, 'terminal': result,
                'evidence_failed': failed}
    records = [{'event': 'admission', 'game_ref': f'g{i}', 'ok': i != 4} for i in range(1, 9)]
    records += [closed('g1', 'k1', 10), closed('g2', 'k1', 16, result='victory'),
                closed('g3', 'k2', 12), closed('g4', 'k2', 30),  # g4 refused admission
                closed('g5', 'k3', 5, reason='unknown'), closed('g6', 'k3', 9, failed=True),
                # a save-start K group: the start table only (#553 review H)
                closed('g7', 'k4', 40, start='from_save'),
                closed('g8', 'k4', 25, start='from_save')]
    (driver / 'driver.jsonl').write_text(jsonl_text(records))
    recipe = readout.recipe_edge(tmp_path)
    mid = recipe['arm']['mid']
    assert (mid['games'], mid['other'], mid['wins'], mid['rows']) == (3, 3, 1, 400)
    assert mid['mean_floor'] == pytest.approx((10 + 16 + 12) / 3)
    k2 = recipe['k']['2']
    assert (k2['groups'], k2['full_groups'], k2['mean_floor_range']) == (3, 1, 6)
    assert recipe['ascension']['10']['games'] == 3 and recipe['start']['new_run']['rows'] == 400
    assert recipe['start']['from_save']['games'] == 2
    assert recipe['start']['from_save']['mean_floor'] == pytest.approx(32.5)
    assert readout.recipe_edge(tmp_path / 'nothing') == {}


def test_chain_name_must_not_look_like_a_generation(tmp_path):
    site = tmp_path / 'site.toml'
    site.write_text(SITE.read_text())
    with pytest.raises(SystemExit):
        supervise.main(['start', 'a-r1', '--site', str(site), '--cards', '2',
                        '--card-type', 'A100-40'])


def test_standing_eval_watchers_use_shared_local_storage(tmp_path):
    chain, _ = chain_for(tmp_path, [], eval_suite='rl3-a0-100', eval_a10_enabled='true',
                         eval_a10_suite='a10-50')
    template = tmp_path / 'execution.json'
    template.write_text(json.dumps({'schema': 'x', 'models': {}, 'storage_mode': 'remote',
                                    'store': 's3', 'peer': {'kind': 's3'}}))
    chain.args.eval_execution, chain.args.eval_manifest = template, tmp_path / 'm.json'
    chain.args.eval_a10_manifest = tmp_path / 'm10.json'
    chain.args.eval_baseline_plan = tmp_path / 'base' / 'plan.json'
    chain.dir.mkdir(parents=True)
    chain.state = {'deadline_at': 1e10, 'log': []}
    started = []
    chain.spawn = lambda cmd, log: started.append(cmd) or type(
        'P', (), {'pid': 7, 'poll': lambda self: None})()
    chain.persist = lambda: None
    chain.start_eval()
    a0, a10 = started
    for cmd in started:
        assert '--once' not in cmd and '--checkpoint' not in cmd and '--deadline-at' not in cmd
    assert a0[a0.index('--suite') + 1] == 'rl3-a0-100' and '--baseline-plan' in a0
    assert a10[a10.index('--suite') + 1] == 'a10-50' and a10[a10.index('--seeds') + 1] == '50'
    assert '--baseline-plan' not in a10 and '--dry-run' in a0
    assert a0[a0.index('--baseline-source') + 1] == 'explicit' and '--baseline-sha256' not in a0
    execution = json.loads((chain.dir / 'eval-execution.json').read_text())
    assert execution['storage_mode'] == 'shared_local' and execution['peer'] == {
        'kind': 'local', 'directory': chain.site['peer_dir']}
    assert execution['store'] == chain.site['peer_dir'] and execution['models'] == {}


def eval_chain(tmp_path, baselines='', **changes):
    """A chain with both standing watchers; `baselines` is appended to the site's [site]."""
    chain, cluster = chain_for(tmp_path, [{}], eval_a10_enabled='true', eval_a10_suite='a10-50',
                               **changes)
    if baselines:
        text = chain.args.site.read_text().replace(  # on its own line, never in a comment
            '\n[site.gpu_types', '\n' + baselines + '\n[site.gpu_types', 1)
        chain.args.site.write_text(text)
        chain.site = supervise.run_profile.load_site(chain.args.site)
    template = tmp_path / 'execution.json'
    template.write_text(json.dumps({'schema': 'x', 'models': {}}))
    chain.args.eval_execution, chain.args.eval_manifest = template, tmp_path / 'm.json'
    chain.args.eval_a10_manifest = tmp_path / 'm10.json'
    chain.spawn = lambda cmd, log: type('P', (), {'pid': 7, 'poll': lambda self: None,
                                                  'returncode': None})()
    return chain, cluster


SITE_BASELINES = (f'eval_baselines.a0 = {{ plan = "/b/a0/plan.json", sha256 = "{"a" * 64}" }}\n'
                  f'eval_baselines.a10 = {{ plan = "/b/a10/plan.json", sha256 = "{"b" * 64}" }}\n')


def test_watcher_start_checks_run_before_generation_zero_and_stop_the_chain(tmp_path):
    """M1: a watcher that would refuse to start stops the chain before any training job."""
    chain, cluster = eval_chain(tmp_path, SITE_BASELINES)
    checked = []

    def check(cmd):
        checked.append(cmd)
        refused = '--suite' in cmd and cmd[cmd.index('--suite') + 1] == 'a10-50'
        return subprocess.CompletedProcess(cmd, 2 if refused else 0, '', 'fast_eval check '
                                           'refused: ValueError: named_frozen_evaluation_suite'
                                           '_manifest_required' if refused else '')

    chain.check = check
    assert chain.run() == 1
    assert cluster.argvs == []  # no generation was submitted
    final = json.loads((chain.dir / 'FINAL.json').read_text())
    assert 'watcher a10 refused at start' in final['cause'] and 'manifest' in final['cause']
    a0, a10 = checked
    assert all(cmd[-1] == '--check' for cmd in checked)
    assert a0[a0.index('--baseline-plan') + 1] == '/b/a0/plan.json'
    assert a0[a0.index('--baseline-sha256') + 1] == 'a' * 64
    assert a10[a10.index('--baseline-source') + 1] == 'site'
    # --dry-run runs the same checks.
    dry, cluster = eval_chain(tmp_path / 'dry', SITE_BASELINES)
    dry.args.dry_run, dry.check = True, check
    assert dry.run() == 1 and cluster.argvs == []


def test_no_baseline_needs_the_flag_and_the_flag_overrides_the_site(tmp_path):
    chain, cluster = eval_chain(tmp_path)  # no eval_baselines in the site
    chain.check = lambda cmd: subprocess.CompletedProcess(cmd, 0, '{}', '')
    assert chain.run() == 1 and cluster.argvs == []
    final = json.loads((chain.dir / 'FINAL.json').read_text())
    assert 'no baseline' in final['cause'] and '--eval-no-baseline' in final['cause']
    flagged, cluster = eval_chain(tmp_path / 'flag', SITE_BASELINES)
    flagged.args.eval_no_baseline = True
    checked = []
    flagged.check = lambda cmd: checked.append(cmd) or subprocess.CompletedProcess(cmd, 0, '', '')
    assert flagged.run() == 0 and len(cluster.argvs) == 1
    assert all('--no-baseline' in cmd and '--baseline-plan' not in cmd for cmd in checked)
    with pytest.raises(supervise.run_profile.ProfileError, match='eval_baselines'):
        eval_chain(tmp_path / 'bad', 'eval_baselines.a0 = { plan = "/b", sha256 = "short" }')


def test_a_dead_watcher_is_dead_in_the_readout(tmp_path, capsys):
    chain, _ = eval_chain(tmp_path, SITE_BASELINES)
    chain.dir.mkdir(parents=True)
    chain.state = {'log': []}
    dead = type('P', (), {'pid': 7, 'poll': lambda self: 1, 'returncode': 1})()
    chain.eval_loops = {'a0': dead}
    chain.tick(tmp_path / 'no-run-yet')
    assert 'DEAD (exit 1)' in capsys.readouterr().err
    status = chain.eval_status()
    assert 'a0 DEAD (exit 1) no state yet' in status and 'a10 no state yet' in status


# ---------------- checkpoint retention (#555) ----------------

def test_cp_retention_default_keeps_every_checkpoint_and_one_is_refused():
    values = knobs()
    assert values['checkpoint_keep_newest'] == 0 and values['checkpoint_milestone_hours'] == 0
    records = [{'checkpoint_artifact_id': f'cp{i}', 'published_at': 300.0 * i}
               for i in range(1, 9)]
    assert cp_retention.select(records, keep_newest=0, milestone_hours=0, started_at=0,
                               protected=()) == ([f'cp{i}' for i in range(1, 9)], [])
    with pytest.raises(run_profile.ProfileError, match='checkpoint_keep_newest'):
        run_profile.load_profile(PROFILE, ['checkpoint_keep_newest=1'])
    run_profile.load_profile(PROFILE, ['checkpoint_keep_newest=2'])


def test_cp_retention_keeps_newest_claims_and_one_per_milestone_window():
    # One CP every 30 min for 5 h; milestones every 2 h; newest 2; cp4 claimed.
    records = [{'checkpoint_artifact_id': f'cp{i}', 'published_at': 1000 + 1800.0 * i}
               for i in range(10)]
    kept, drop = cp_retention.select(records, keep_newest=2, milestone_hours=2,
                                     started_at=1000, protected={'cp4', 'elsewhere'})
    assert kept == ['cp0', 'cp4', 'cp8', 'cp9']  # windows 0-2 h, 2-4 h, 4-6 h: cp0, cp4, cp8
    assert drop == ['cp1', 'cp2', 'cp3', 'cp5', 'cp6', 'cp7']


def retention_chain(tmp_path, monkeypatch, **sets):
    """A chain whose peer holds 6 real CPs (16-byte chunks): each payload = one chunk all
    CPs share (an all-zero block) + one unique chunk. Generation lr1-r1 started from cp1,
    the A0 watcher claims cp2, a model shares cp4's unique chunk."""
    from spireagent.artifact_contracts import Manifest, Producer
    from spireagent.json_boundary import FrozenObject
    from spireagent.rl_exchange import Exchange
    from spireagent.storage import store as store_module

    monkeypatch.setattr(store_module, 'CHUNK_BYTES', 16)
    chain, cluster = chain_for(tmp_path, [], **sets)
    chain.dir.mkdir(parents=True)
    exchange = Exchange({'kind': 'local', 'directory': chain.site['peer_dir']},
                        chain.site['run_name'])
    producer = Producer('rsgcsg/STS2-The-Perfect-Defect', 'a' * 40, 'b' * 64)
    zero, cps = bytes(16), []
    for i in range(1, 7):
        generation = 'g-fc-lr1' if i <= 3 else 'g-fc-lr1-r1'
        payload = exchange.store.put_bytes('checkpoint', zero + f'unique-cp-{i:06d}'.encode())
        manifest = Manifest('checkpoint', producer, payloads=(payload,),
                            parameters=FrozenObject.of({'sequence': i}))
        exchange.store.publish(manifest)
        exchange.publish_checkpoint(i, manifest.artifact_id, sequence=i,
                                    training_generation=generation)
        cps.append(manifest)
    exchange.store.publish(Manifest('model', producer, payloads=(exchange.store.put_bytes(
        'weights', b'unique-cp-000004' + b'model-weights-xx'),)))
    # One model publication (model + heads + record) per CP version, as the learner's.
    from spireagent.artifact_contracts import Parent
    for i in range(1, 7):
        generation = 'g-fc-lr1' if i <= 3 else 'g-fc-lr1-r1'
        weights = exchange.store.put_bytes('weights', f'model-weights-{i:02d}'.encode())
        model = Manifest('model', producer, payloads=(weights, exchange.store.put_bytes(
            'package_manifest', f'package-json-{i:03d}'.encode())))
        exchange.store.publish(model)
        heads = Manifest('checkpoint', producer, parents=(Parent('policy', model.artifact_id),),
                         payloads=(exchange.store.put_bytes(
                             'checkpoint', f'heads-bytes-{i:04d}'.encode()),),
                         parameters=FrozenObject.of({'schema': 'stpd/rl-behavior-heads-1'}))
        exchange.store.publish(heads)
        exchange.publish_model({'run': chain.site['run_name'], 'version': i,
                                'training_generation': generation, 'published_at': 100.0 + i,
                                'model_artifact_id': model.artifact_id,
                                'model_sha256': weights.sha256,
                                'heads_artifact_id': heads.artifact_id})
    chain.checkpoints = chain.peer_checkpoints
    chain.state = {'started_at': 0.0, 'deadline_at': 10**12, 'start_checkpoint': None,
                   'identity': {}, 'resumes_at': [], 'log': [],
                   'generations': [{'number': 0, 'runtag': 'lr1', 'checkpoint': None,
                                    'run': str(tmp_path / 'run-lr1')},
                                   {'number': 1, 'runtag': 'lr1-r1',
                                    'checkpoint': cps[0].artifact_id,
                                    'run': str(tmp_path / 'run-lr1-r1')}]}
    claim = chain.dir / 'eval-a0' / 'protected-checkpoints.json'
    claim.parent.mkdir()
    claim.write_text(json.dumps({'schema': 'x/protected', 'checkpoints': [cps[1].artifact_id]}))
    return chain, exchange, cps


def chunk(exchange, data):
    import hashlib
    return exchange.blobs.root / 'objects' / 'sha256' / hashlib.sha256(data).hexdigest()


def test_cp_retention_default_deletes_nothing_and_records_bytes(tmp_path, monkeypatch):
    chain, exchange, cps = retention_chain(tmp_path, monkeypatch)
    chain.retain()
    assert chain.retention == {'keep_newest': 0, 'milestone_hours': 0, 'kept': 6,
                               'deleted': 0, 'delete_failed': 0, 'deleted_bytes': 0,
                               'payload_bytes_kept': 6 * 32}
    for cp in cps:
        exchange.store.bytes(cp.payload('checkpoint'))
    assert chain.model_retention == {'keep_newest': 0, 'kept': 6, 'deleted': 0,
                                     'deleted_bytes': 0, 'delete_failed': 0,
                                     'payload_bytes_kept': 6 * 48}


def test_model_retention_keeps_newest_and_versions_of_retained_cps(tmp_path, monkeypatch):
    from spireagent.storage.blobs import StoreError

    chain, exchange, cps = retention_chain(tmp_path, monkeypatch, checkpoint_keep_newest=2,
                                           model_keep_newest=1)
    chain.retain()
    # Retained CPs name versions (lr1, 1), (lr1, 2), (lr1-r1, 5), (lr1-r1, 6); newest = 6.
    facts = chain.model_retention
    assert facts['kept'] == 4 and facts['deleted'] == 2 and facts['delete_failed'] == 0
    assert facts['deleted_bytes'] == 2 * 48 and facts['payload_bytes_kept'] == 4 * 48
    for generation, version, gone in (('g-fc-lr1', 1, False), ('g-fc-lr1', 3, True),
                                      ('g-fc-lr1-r1', 4, True), ('g-fc-lr1-r1', 6, False)):
        record = exchange.model(version, training_generation=generation)
        model = exchange.store.get_manifest(record['model_artifact_id'])  # manifest stays
        heads = exchange.store.get_manifest(record['heads_artifact_id'])
        for payload in (*model.payloads, heads.payload('checkpoint')):
            if gone:
                with pytest.raises(StoreError, match='object_not_found'):
                    exchange.store.bytes(payload)
            else:
                exchange.store.bytes(payload)
    run = Path(chain.state['generations'][1]['run'])
    run.mkdir()
    (run / 'plan.json').write_text(json.dumps(plan_for(cards=2)))
    (run / 'learner-events.jsonl').write_text('')
    assert chain.record(run, 'checkpoint')
    line = json.loads((chain.dir / 'run-records.jsonl').read_text().splitlines()[-1])
    assert line['chain']['model_retention']['deleted'] == 2
    chain.retain()  # nothing more, never re-counted
    assert chain.model_retention['deleted'] == 2
    assert chain.model_retention['deleted_bytes'] == 2 * 48


def test_model_retention_selection_and_heads_binding():
    records = [{'training_generation': 'g', 'version': v, 'published_at': float(v)}
               for v in range(1, 11)]
    kept, drop = cp_retention.select_models(records, keep_newest=3, tied={('g', 2)})
    assert [r['version'] for r in kept] == [2, 8, 9, 10] and len(drop) == 6
    assert cp_retention.select_models(records, keep_newest=0, tied=set()) == (records, [])


def test_cp_retention_deletes_only_unclaimed_unshared_cp_bytes(tmp_path, monkeypatch):
    from spireagent.storage.blobs import StoreError

    chain, exchange, cps = retention_chain(tmp_path, monkeypatch, checkpoint_keep_newest=2)
    chain.retain()
    # kept: cp1 (generation lr1-r1 started from it), cp2 (claimed), cp5 + cp6 (newest)
    assert chain.retention['kept'] == 4 and chain.retention['deleted'] == 2
    assert chain.retention['payload_bytes_kept'] == 4 * 32
    assert chain.retention['deleted_bytes'] == 16  # cp3's unique chunk; cp4's is shared
    assert chain.state['checkpoint_retention']['deleted'] == [cps[2].artifact_id,
                                                              cps[3].artifact_id]
    for index in (0, 1, 4, 5):
        exchange.store.bytes(cps[index].payload('checkpoint'))
    with pytest.raises(StoreError, match='object_not_found'):
        exchange.store.bytes(cps[2].payload('checkpoint'))
    assert chunk(exchange, bytes(16)).exists()  # every CP names the zero block
    assert chunk(exchange, b'unique-cp-000004').exists()  # the model names it
    assert not chunk(exchange, b'unique-cp-000003').exists()
    # Lineage still resolves the deleted id (manifest and record stay).
    assert exchange.store.get_manifest(cps[2].artifact_id).kind == 'checkpoint'
    # The resume CP is the newest and still restorable.
    assert chain.newest_checkpoint() == cps[5].artifact_id
    # The run record line carries the facts.
    run = Path(chain.state['generations'][1]['run'])
    run.mkdir()
    (run / 'plan.json').write_text(json.dumps(plan_for(cards=2)))
    (run / 'learner-events.jsonl').write_text('')
    assert chain.record(run, 'checkpoint')
    line = json.loads((chain.dir / 'run-records.jsonl').read_text().splitlines()[-1])
    assert line['chain']['checkpoint_retention']['deleted'] == 2
    # A second pass deletes nothing more and never re-counts.
    chain.retain()
    assert chain.retention['deleted'] == 2 and chain.retention['deleted_bytes'] == 16


def test_cp_retention_failed_delete_is_loud_counted_and_retried(tmp_path, monkeypatch,
                                                               capsys):
    chain, exchange, cps = retention_chain(tmp_path, monkeypatch, checkpoint_keep_newest=2)
    real = os.unlink

    def refuse(path):
        raise PermissionError(13, 'quota', str(path))
    monkeypatch.setattr(cp_retention.os, 'unlink', refuse)
    chain.retain()  # never raises: training goes on
    # cp3's unique chunk failed (cp3 stays pending); cp4 has no unshared chunk to remove
    assert chain.retention['delete_failed'] == 1 and chain.retention['deleted'] == 1
    assert 'not deleted' in capsys.readouterr().err
    assert any(e['kind'] == 'checkpoint_delete_failed' for e in chain.state['log'])
    monkeypatch.setattr(cp_retention.os, 'unlink', real)
    chain.retain()
    assert chain.retention['deleted'] == 2 and chain.retention['delete_failed'] == 1


def test_cp_retention_never_deletes_on_an_unreadable_claim(tmp_path, monkeypatch, capsys):
    chain, exchange, cps = retention_chain(tmp_path, monkeypatch, checkpoint_keep_newest=2,
                                           model_keep_newest=1)
    (chain.dir / 'eval-a0' / 'protected-checkpoints.json').write_text('{')
    chain.retain()
    assert 'error' in chain.retention and chain.retention['deleted'] == 0
    assert chain.state['log'][-1]['kind'] == 'checkpoint_retention_failed'
    assert 'CP/model retention skipped' in capsys.readouterr().err
    for cp in cps:
        exchange.store.bytes(cp.payload('checkpoint'))
    assert chain.model_retention['deleted'] == 0  # no model deleted that round either
    for version in (1, 2, 3):
        record = exchange.model(version, training_generation='g-fc-lr1')
        exchange.store.bytes(exchange.store.get_manifest(record['model_artifact_id'])
                             .payload('weights'))


def test_cp_retention_keeps_a_payload_another_artifact_names(tmp_path, monkeypatch):
    """Review M1: two artifacts with byte-identical payloads share one payload index;
    deleting one must leave the other readable."""
    from spireagent.artifact_contracts import Manifest, Producer
    from spireagent.json_boundary import FrozenObject

    chain, exchange, cps = retention_chain(tmp_path, monkeypatch)
    producer = Producer('rsgcsg/STS2-The-Perfect-Defect', 'a' * 40, 'b' * 64)
    same = [Manifest('checkpoint', producer, payloads=(exchange.store.put_bytes(
        'checkpoint', b'identical-bytes-' * 2),), parameters=FrozenObject.of({'n': n}))
        for n in (1, 2)]
    for manifest in same:
        exchange.store.publish(manifest)
    deleter = cp_retention.Deleter(exchange.store)
    done = deleter.delete(same[0].artifact_id)
    assert done['freed_bytes'] == 0 and done['shared'] == 1
    exchange.store.bytes(same[1].payload('checkpoint'))


def test_cp_retention_claims_evaluated_cps_and_other_chain_starts(tmp_path):
    """Review S1/S2: every CP an evaluation names (any stage) and the start CPs of every
    chain under the same work root are protected; a malformed state skips the pass."""
    chain_dir = tmp_path / 'chain-a'
    (chain_dir / 'eval-a0').mkdir(parents=True)
    (chain_dir / 'eval-a0' / 'state.json').write_text(json.dumps({'evaluations': [
        {'checkpoint': 'cp-done', 'stage': 'done'},
        {'checkpoint': 'cp-unknown', 'stage': 'prepare_submit_unknown'},
        {'checkpoint': None, 'stage': 'orphan_intent'}]}))  # review M2: must not block
    (tmp_path / 'chain-b').mkdir()
    (tmp_path / 'chain-b' / 'state.json').write_text(json.dumps({
        'start_checkpoint': 'cp-branch', 'generations': [{'checkpoint': None},
                                                         {'checkpoint': 'cp-gen'}]}))
    assert cp_retention.claims(chain_dir) == {'cp-done', 'cp-unknown', 'cp-branch', 'cp-gen'}
    (chain_dir / 'eval-a0' / 'state.json').write_text('{"evaluations": [{}]}')
    with pytest.raises(ValueError, match='no evaluations list'):
        cp_retention.claims(chain_dir)


def test_hp_potion_mode_defaults_to_points_and_is_refused_where_it_does_nothing():
    """Ticket H: "value_head" needs format 4 (profile and preflight) and c_hp > 0
    (preflight); off the default it needs a source that knows it, roles passes it on."""
    profile = run_profile.load_profile(PROFILE)
    assert profile['hp_potion_mode']['value'] == profile['hp_potion_mode']['default'] \
        == 'points'
    with pytest.raises(run_profile.ProfileError, match='hp_potion_mode.*segment_format'):
        run_profile.load_profile(PROFILE, ['hp_potion_mode="value_head"'])
    with pytest.raises(run_profile.ProfileError, match='hp_potion_mode'):
        run_profile.load_profile(PROFILE, ['hp_potion_mode="zero"'])
    four = 'stpd/rl-fast-segment-4'
    assert run_profile.values(run_profile.load_profile(PROFILE, [
        'hp_potion_mode="value_head"', f'segment_format={four}']))['hp_potion_mode'] == \
        'value_head'

    def hp(fmt, coef, mode):
        plan = plan_for(cards=3, wall_seconds=2400, segment_format=fmt)
        plan['knobs']['hp_potion_mode'] = mode  # past the profile relation
        facts = {**good_facts(plan),
                 'gate_learner_base': with_hp(gate_base(), coef, 'all_combats'),
                 'frozen_recipe': with_hp(FROZEN, coef, 'all_combats')}
        return preflight.check_hp_score(plan, facts)

    zero = hp(four, 0.0, 'value_head')
    assert not zero.ok and "hp_potion_mode 'value_head' needs c_hp > 0" in zero.detail
    three = hp('stpd/rl-fast-segment-3', 0.003, 'value_head')
    assert not three.ok and 'needs segment_format' in three.detail
    on = hp(four, 0.003, 'value_head')
    assert on.ok and on.detail.endswith('potions value_head')
    assert hp(four, 0.003, 'points').detail == \
        'c_hp 0.003, scope all_combats, segment format stpd/rl-fast-segment-4'
    assert hp('stpd/rl-fast-segment-3', 0.0, 'points').ok
    assert 'hp_potion_mode' not in preflight.required_features(plan_for(cards=3))
    assert 'hp_potion_mode' in preflight.required_features(plan_for(
        cards=3, segment_format=four, hp_potion_mode='value_head'))
    roles = (FASTCHAIN / 'roles.py').read_text()
    assert 'hp["hp_potion_mode"] = K["hp_potion_mode"]' in roles


def test_kl_stop_scope_and_lr_warmup_default_off_and_reach_the_learner_only_when_set():
    """Ticket K: both knobs default to today's behaviour and then leave learner.json as it
    was; set, they land in segments (resume-locked there); "control" needs a control arm."""
    profile = run_profile.load_profile(PROFILE)
    for name, default in (('kl_stop_scope', 'all'), ('lr_warmup_steps', 0)):
        assert profile[name]['value'] == profile[name]['default'] == default
    run = {'run_name': 'r', 'peer_dir': '/p', 'start_package': '/s', 'wall_seconds': 60,
           'device': 'cuda', 'generation': 'g', 'warmup_artifact_id': 'w',
           'rendezvous_path': '/r', 'ranks': 1, 'resume_checkpoint': None}
    built = learner_config.build(gate_base(), run_profile.values(profile), run)
    assert not {'kl_stop_scope', 'lr_warmup_steps'} & set(built['segments'])
    assert learner_config.recipe_drift(built, FROZEN) == []
    set_ = run_profile.values(run_profile.load_profile(
        PROFILE, ['kl_stop_scope="control"', 'lr_warmup_steps=2000']))
    on = learner_config.build(gate_base(), set_, run)
    assert on['segments']['kl_stop_scope'] == 'control'
    assert on['segments']['lr_warmup_steps'] == 2000
    assert learner_config.recipe_drift(on, FROZEN) == []  # not frozen keys: no drift
    with pytest.raises(run_profile.ProfileError, match='control arm|named control'):
        run_profile.load_profile(PROFILE, [
            'kl_stop_scope="control"', 'exploration_ladder={ hot = { share = 1.0, combat = '
            '{ temperature = 1.0, uniform_mix = 0.0 }, noncombat = { temperature = 1.0, '
            'uniform_mix = 0.0 } } }'])
    for altered in ('{ temperature = 1.2, uniform_mix = 0.0 }',
                    '{ temperature = 1.0, uniform_mix = 0.01 }'):
        with pytest.raises(run_profile.ProfileError, match='control arm unaltered'):
            run_profile.load_profile(PROFILE, [
                'kl_stop_scope="control"', 'exploration_ladder={ control = { share = 1.0, '
                f'combat = {altered}, noncombat = '
                '{ temperature = 1.0, uniform_mix = 0.0 } } }'])
    with pytest.raises(run_profile.ProfileError, match='lr_warmup_steps'):
        run_profile.load_profile(PROFILE, ['lr_warmup_steps=-1'])
    need = preflight.required_features(plan_for(cards=3))
    assert not {'kl_stop_scope', 'lr_warmup_steps'} & set(need)
    need = preflight.required_features(plan_for(cards=3, kl_stop_scope='control',
                                                lr_warmup_steps=2000))
    assert {'kl_stop_scope', 'lr_warmup_steps'} <= set(need)


# ---------------- RL5-METHODS C: declared recipe changes at a continuation ----------------

LR_DOWN = {'checkpoint': 'cp-src', 'changes': {
    'lr': {'old': 0.000113, 'new': 0.00005},
    'segments.value_target': {'old': 'segment', 'new': 'game'}}}
RUN = {'run_name': 'r', 'peer_dir': '/p', 'start_package': '/s', 'wall_seconds': 60,
       'device': 'cuda', 'generation': 'g', 'warmup_artifact_id': 'w',
       'rendezvous_path': '/r', 'ranks': 1, 'resume_checkpoint': 'cp-src'}


def test_declarable_keys_match_the_learner_allow_list():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from stpd.rl import recipe_changes
    assert tuple(recipe_changes.DECLARABLE) == learner_config.DECLARABLE
    assert run_profile.DECLARABLE == learner_config.DECLARABLE
    for key, value in recipe_changes.RECIPE_DEFAULTS.items():
        assert learner_config.CONFIG_DEFAULTS[key] == value


def test_declared_changes_default_off_and_build_writes_the_declaration_only_when_set():
    profile = run_profile.load_profile(PROFILE)
    assert profile['declared_changes']['value'] == profile['declared_changes']['default'] == {}
    plain = learner_config.build(gate_base(), knobs(), RUN)
    assert 'resume_recipe_changes' not in plain  # an older learner refuses unknown keys
    assert learner_config.recipe_drift(plain, FROZEN) == []
    built = learner_config.build(gate_base(), knobs(declared_changes=LR_DOWN), RUN)
    assert built['lr'] == 0.00005 and built['segments']['value_target'] == 'game'
    assert built['resume_recipe_changes'] == {'checkpoint_artifact_id': 'cp-src',
                                              'changes': LR_DOWN['changes']}
    assert learner_config.recipe_drift(built, FROZEN) == ['lr: frozen 0.000113, effective 5e-05']
    assert learner_config.recipe_drift(built, FROZEN, declared=frozenset(LR_DOWN['changes'])) == []
    assert learner_config.apply_declared(plain, LR_DOWN) == built
    assert learner_config.apply_declared(plain, {}) is plain


def test_declared_changes_knob_is_validated_at_load():
    good = ('declared_changes={ checkpoint = "cp-src", changes = { "lr" = { old = 0.000113, '
            'new = 0.00005 } } }')
    assert run_profile.values(run_profile.load_profile(PROFILE, [good]))[
        'declared_changes']['changes']['lr']['new'] == 0.00005
    for bad, message in (
            ('declared_changes={ checkpoint = "cp", changes = { "segments.steps" = '
             '{ old = 2, new = 4 } } }', 'not declarable'),
            ('declared_changes={ checkpoint = "cp", changes = { "lr" = '
             '{ old = 0.1, new = 0.1 } } }', 'nothing changes'),
            ('declared_changes={ checkpoint = "cp", changes = {} }', 'at least one change'),
            ('declared_changes={ changes = { "lr" = { old = 0.1, new = 0.2 } } }',
             'at least one change'),
            ('declared_changes={ checkpoint = "cp", changes = { "lr" = { new = 0.2 } } }',
             'exactly old and new'),
            ('declared_changes={ checkpoint = "cp", changes = { "segments.kl_stop_scope" = '
             '{ old = "all", new = "control" } } }', 'kl_stop_scope knob')):
        with pytest.raises(run_profile.ProfileError, match=message):
            run_profile.load_profile(PROFILE, [bad])


def declared_plan(declared, resume='cp-src', generation=None):
    chain = None if generation is None else {'id': 'c-t1', 'deadline_at': 10**10,
                                             'total_seconds': 72000, 'generation': generation}
    return launch.build_plan('t1-r1', knobs(declared_changes=declared),
                             run_profile.load_site(SITE), cards=2, card_type='A100-40',
                             layout_spec=None, env_cores=None, smoke=False,
                             probe_rows=[probe_row()], long_run=True,
                             resume_checkpoint=resume, chain=chain)


def cp_config(**changes):
    """A CP's config as the learner stores it: the built learner config (no declaration)."""
    config = learner_config.build(gate_base(), knobs(), RUN)
    for key, value in changes.items():
        *parents, last = key.split('.')
        node = config
        for part in parents:
            node = node[part]
        node[last] = value
    return config


@pytest.mark.parametrize('declared,resume,generation,old,ok,detail', [
    ({}, 'cp-src', None, None, True, 'none: the recipe must equal'),
    (LR_DOWN, 'cp-src', None, cp_config(), True, "lr 0.000113 -> 5e-05, "
     "segments.value_target 'segment' -> 'game'"),
    (LR_DOWN, None, None, cp_config(), False, 'need --resume-checkpoint'),
    (LR_DOWN, 'cp-other', None, cp_config(), False, 'but this run starts from cp-other'),
    (LR_DOWN, 'cp-newer', 2, cp_config(), True, 'inactive'),
    (LR_DOWN, 'cp-src', None, None, False, 'cannot read CP cp-src'),
    (LR_DOWN, 'cp-src', None, cp_config(lr=0.0002), False,
     'lr: declared old 0.000113, CP has 0.0002'),
    (LR_DOWN, 'cp-src', None, cp_config(**{'loss.entropy_coef': 0.02}), False,
     'loss.entropy_coef changes 0.02 -> 0.01 undeclared'),
    ({'checkpoint': 'cp-src', 'changes': {'lr': {'old': 0.000113, 'new': 0.00005}}},
     'cp-src', None, cp_config(**{'segments.value_target': 'game'}), False,
     "segments.value_target changes 'game' -> 'segment' undeclared"),
    (LR_DOWN, 'cp-src', None, cp_config(lr=0.00005), False,
     'lr: declared old 0.000113, CP has 5e-05; lr: 5e-05 in both'),
])
def test_preflight_checks_a_declaration_against_the_cp_it_names(
        declared, resume, generation, old, ok, detail):
    plan = declared_plan(declared, resume, generation)
    facts = {**good_facts(plan), **({} if old is None else {'resume_checkpoint_config': old})}
    result = preflight.check_declared_changes(plan, facts)
    assert result.ok is ok and detail in result.detail, result.detail
    results = by_name(preflight.checks(plan, facts))
    assert results['declared_changes'].ok is ok
    if declared and ok:
        assert results['frozen_recipe'].ok, results['frozen_recipe'].detail
        assert 'declared_changes' in preflight.required_features(plan)


def test_readout_shows_the_restored_cp_and_the_declared_changes(tmp_path):
    run = tmp_path / 'run-t1'
    plan = write_run(run)
    write_record_inputs(run)
    plan['resume_checkpoint'] = 'cp-src'
    (run / 'plan.json').write_text(json.dumps(plan))
    learner = json.loads((run / 'learner' / 'learner.json').read_text())
    learner['resume_recipe_changes'] = {'checkpoint_artifact_id': 'cp-src',
                                        'changes': LR_DOWN['changes']}
    (run / 'learner' / 'learner.json').write_text(json.dumps(learner))
    restored = {'t': 1000.5, 'kind': 'checkpoint_restored', 'checkpoint_artifact_id': 'cp-src',
                'run': 'r', 'training_generation': 'g-old', 'sequence': 7,
                'mu_version': 40, 'updates': 900, 'producer': {}, 'recipe_changes':
                LR_DOWN['changes'], 'optimizer_lr': {'restored': [0.000113, 0.0001],
                                                     'base': [0.00005, 0.0001],
                                                     'applied': [0.00005, 0.0001]}}
    with (run / 'learner-events.jsonl').open('a') as out:
        out.write(json.dumps(restored) + '\n')
    r = readout.build(run, 1200)
    assert r['identity']['declared_changes']['checkpoint_artifact_id'] == 'cp-src'
    edge = r['restored_checkpoint']
    assert edge['matches_plan'] is True and edge['updates'] == 900
    text = readout.render(r)
    for line in ('restored CP: cp-src (generation g-old, CP 7, mu 40, 900 updates)',
                 "declared recipe changes: lr 0.000113 -> 5e-05, segments.value_target "
                 "'segment' -> 'game'; optimizer lr restored [0.000113, 0.0001], base "
                 "[5e-05, 0.0001], next step [5e-05, 0.0001]"):
        assert line in text, text
    assert r['incidents']['restored_checkpoint_mismatch'] is None
    plan['resume_checkpoint'] = 'cp-expected'  # the learner restored another CP: an incident
    (run / 'plan.json').write_text(json.dumps(plan))
    r = readout.build(run, 1200)
    assert r['incidents']['restored_checkpoint_mismatch'] == {'expected': 'cp-expected',
                                                              'restored': 'cp-src'}
    text = readout.render(r)
    assert "NOT the plan's resume CP cp-expected" in text
    assert 'INCIDENT: the learner restored CP cp-src, the plan\'s resume CP is cp-expected' \
        in text


def test_a_failed_cp_read_names_the_cp_not_the_store_path(tmp_path):
    peer = tmp_path / 'secret-site-peer'
    plan = declared_plan(LR_DOWN)
    facts = launch.resume_checkpoint_facts(plan, peer)
    error = facts['resume_checkpoint_config_error']
    assert 'cp-src' in error and 'FileNotFoundError' in error and str(peer) not in error
    assert 'secret-site-peer' not in error
    result = preflight.check_declared_changes(plan, {**good_facts(plan), **facts})
    assert not result.ok and 'secret-site-peer' not in result.detail
    assert launch.resume_checkpoint_facts(declared_plan({}), peer) == {}  # nothing read


def test_preflight_compares_declared_values_type_strict_like_the_learner():
    declared = {'checkpoint': 'cp-src', 'changes': {
        'loss.hp_advantage_coef': {'old': False, 'new': 0.003}}}
    plan = declared_plan(declared)
    facts = {**good_facts(plan), 'resume_checkpoint_config': cp_config()}  # c_hp 0.0
    result = preflight.check_declared_changes(plan, facts)
    assert not result.ok and 'declared old False, CP has 0.0' in result.detail
