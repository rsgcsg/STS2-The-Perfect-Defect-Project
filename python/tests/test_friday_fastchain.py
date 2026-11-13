"""Fast-chain launcher: layout -> placement, run profile, preflight decisions, readout."""
from __future__ import annotations

import copy
import importlib
import json
import math
import sys
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
    names = ('run_profile', 'layout', 'preflight', 'launch', 'readout')
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


run_profile, layout, preflight, launch, readout = load_fastchain()


def probe_row(node='gpu-a', gpus=3, free=3, cores=128, mem_g=500):
    return {'node': node, 'state': 'mixed', 'gpus': gpus, 'gpus_free': free,
            'cores_idle': cores, 'mem_free_g': mem_g}


def knobs(**changes):
    values = run_profile.values(run_profile.load_profile(PROFILE))
    values.update(changes)
    return values


def plan_for(cards=3, layout_spec=None, smoke=False, probe=None, **changes):
    return launch.build_plan('t1', knobs(**changes), run_profile.load_site(SITE), cards=cards,
                             card_type='A100-40', layout_spec=layout_spec, env_cores=None,
                             smoke=smoke, probe_rows=probe or [probe_row()])


def good_facts(plan):
    k = plan['knobs']
    return {'src_rev': f'int {k["env_pkg_build"]}', 'src_features': {'shards': True},
            'env_pkg_rev': k['env_pkg_build'] + 'cdbd192d (int tip)',
            'prep_test_start_seconds': 0.0,
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


def test_build_plan_caps_memory_guard_below_job_limit_and_falls_back():
    two = plan_for(cards=2)
    limit = two['job_mem_g'] * preflight.GIB
    assert two['mem_guard_bytes'] < two['mem_hard_bytes'] <= limit  # 220 GB guard > 214.7 GB
    assert two['placement']['learner_device'] == 'cuda'
    assert (two['env_nodes_max'] * two['knobs']['env_aggregators']) % two['knobs']['services'] == 0
    none = plan_for(cards=3, probe=[probe_row(free=1)])
    assert none['gpu_node'] is None
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
    assert set(cmds) >= {'gpu', 'env', 'prep'}
    assert '--gres=gpu:a100:3' in cmds['gpu']
    assert any(c == f'--export=ALL,FC_RUN={plan["run"]}' for c in cmds['gpu'])
    prep = ' '.join(cmds['prep'])
    assert ' : ' in prep and 'c8220' not in prep  # heterogeneous, no slow prep host types
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
    ({'remote_prepare_connections': 35}, {}, 'shards'),
    ({}, {'prep_test_start_seconds': None}, 'prep_schedulable'),
    ({}, {'prep_test_start_seconds': 99_999.0}, 'prep_schedulable'),
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
    assert not preflight.check_gpu_node(nonode).ok


def test_time_limits_refuse_a_prep_job_longer_than_its_partition_allows():
    plan = plan_for(cards=3)
    assert plan['partition_max_minutes'] == {'gpu': None, 'env': None, 'prep': 60}
    assert not preflight.check_time_limits(plan).ok  # 50 min wall + margins > 60
    short = plan_for(cards=3, wall_seconds=2400)
    assert preflight.check_time_limits(short).ok


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
    assert preflight.network_fs('nfs4') and preflight.network_fs('lustre')
    assert not preflight.network_fs('tmpfs') and not preflight.network_fs('xfs')


# ---------------- readout ----------------

def write_run(run, cards=3):
    plan = plan_for(cards=cards)
    run.mkdir()
    (run / 'plan.json').write_text(json.dumps(plan))
    t0 = 1000.0
    events = [{'t': t0, 'kind': 'training_started'}]
    events += [{'t': t0 + i, 'kind': 'progress', 'stepped': True, 'rows': 8000, 'seconds': 1.0}
               for i in range(1, 1300)]
    (run / 'learner-events.jsonl').write_text('\n'.join(map(json.dumps, events)))
    status = [{'t': t0 + i, 'queued_rows': 50_000, 'timing': {
        'preparation_wait_seconds': 0.1 * i, 'sgd_stage_wait_seconds': 0.05 * i,
        'optimizer_attempt_seconds': 0.8 * i},
        'fast_chain': {'learner': {'admitted_rows': 8100 * i, 'capacity_dropped_rows': 10 * i}}}
        for i in range(0, 1300, 10)]
    (run / 'learner-status.jsonl').write_text('\n'.join(map(json.dumps, status)))
    service = [{'t': t0 + i, 'handoff_q': 3, 'service': {'rows': 2000 * i,
                'device_seconds': 0.7 * i}, 'handoff': {'written': i, 'dropped': 0}}
               for i in range(0, 1300, 5)]
    (run / 'service-status-0.jsonl').write_text('\n'.join(map(json.dumps, service)))
    (run / 'gpu.csv').write_text('\n'.join(
        f'{int((t0 + i) * 1000)}, {c}, {90 if c else 70}, 30000'
        for i in range(0, 1300, 2) for c in range(cards)))
    return plan


def test_readout_edge_table_target_and_loss_table(tmp_path):
    run = tmp_path / 'run-t1'
    write_run(run)
    result = readout.build(run, 1200)
    assert result['name'] == '3卡 · 1推理+2训练 · A100-40'
    assert result['training']['rows_h'] == pytest.approx(8000 * 3600, rel=0.01)
    assert result['verdict'] == 'below target'
    assert result['training']['prepare_wait_pct'] == pytest.approx(10, abs=0.5)
    assert result['services'][0]['rows_s'] == pytest.approx(2000)
    assert result['gpu']['1']['busy_pct'] == 90
    table = result['loss_table']
    assert table[0]['cause'].startswith('ideal') and table[1]['cause'] == 'measured'
    text = readout.render(result)
    assert 'target: 3,000万' in text and 'loss table' in text and 'infer 0' in text


def test_readout_single_card_has_no_loss_table(tmp_path):
    run = tmp_path / 'run-t1'
    plan = write_run(run, cards=1)
    assert plan['placement']['cards'] == 1
    result = readout.build(run, 1200)
    assert result['loss_table'] is None
    assert result['target']['target_rows_h'] == 14_000_000
