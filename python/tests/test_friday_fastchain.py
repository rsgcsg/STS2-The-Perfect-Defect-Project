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
    names = ('run_profile', 'learner_config', 'layout', 'preflight', 'launch', 'readout',
             'supervise')
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


run_profile, learner_config, layout, preflight, launch, readout, supervise = \
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


def gate_without_hp(*keys):
    """A gate learner-base.json from before the HP score (without c_hp and its scope)."""
    base = gate_base()
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
    ({'env_candidate_artifact_sha256': ''}, {}, 'env_package'),
    ({'env_candidate_artifact_sha256': 'b77f5139'}, {}, 'env_package'),
    ({}, {'src_checkout': {'git': False}}, 'producer_identity'),
    ({}, {'src_checkout': {'git': True, 'head': 'c' * 40, 'dirty': True}}, 'producer_identity'),
    ({'max_versions': 100000}, {}, 'frozen_recipe'),
    ({}, {'gate_learner_base': None}, 'frozen_recipe'),
    ({}, {'gate_learner_base': gate_without_hp()}, 'frozen_recipe'),
    ({}, {'gate_learner_base': gate_without_hp()}, 'hp_score'),
    ({}, {'gate_learner_base': gate_without_hp('hp_advantage_scope')}, 'hp_score'),
    # c_hp > 0 agreed by gate and frozen recipe, but segment format 3
    ({}, {'gate_learner_base': with_hp(gate_base(), 0.003),
          'frozen_recipe': with_hp(FROZEN, 0.003)}, 'hp_score'),
    ({}, {'frozen_recipe': {
        **FROZEN, 'lr': 0.0002}}, 'frozen_recipe'),
    ({}, {'compile_warmup_dumps': {'/x/missing.pt': False}}, 'compile_warmup'),
    ({}, {'compile_warmup_dumps': {}}, 'compile_warmup'),
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
    assert not preflight.check_gpu_node(nonode).ok


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


def test_submit_cancels_every_submitted_job_on_a_later_failure(tmp_path, monkeypatch):
    plan = plan_for(cards=3, wall_seconds=2400)
    cmds = launch.commands(plan)
    cancelled = []
    monkeypatch.setattr(launch.subprocess, 'run',
                        lambda cmd, **kw: cancelled.append(cmd[1:]) if cmd[0] == 'scancel'
                        else None)
    ids = iter(['11', '12'])

    def run_cmd(cmd):
        if cmd[-1].endswith('env.sh'):
            raise launch.subprocess.CalledProcessError(1, cmd)
        return next(ids)

    with pytest.raises(launch.subprocess.CalledProcessError):
        launch.submit(tmp_path, plan, cmds, run_cmd=run_cmd, wait=lambda *a: None)
    assert cancelled == [['11', '12']]  # prep and GPU, no retry
    assert 'cancelled' in (tmp_path / 'submit-failed.txt').read_text()

    cancelled.clear()

    def interrupted(*args):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        launch.submit(tmp_path, plan, cmds, run_cmd=lambda cmd: '21', wait=interrupted)
    assert cancelled == [['21']]


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

def write_run(run, cards=3):
    plan = plan_for(cards=cards)
    run.mkdir()
    (run / 'plan.json').write_text(json.dumps(plan))
    t0 = 1000.0
    events = [{'t': t0, 'kind': 'training_started'}]
    events += [{'t': t0 + i, 'kind': 'progress', 'stepped': True, 'rows': 8000,
                'terminal_rows': 600, 'hp_rows': 400, 'seconds': 1.0} for i in range(1, 1300)]
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


def test_readout_refuses_to_guess_missing_aux(tmp_path):
    run = tmp_path / 'run-t1'
    write_run(run)
    events = [json.loads(line) for line in (run / 'learner-events.jsonl').read_text().split('\n')]
    for event in events[5:]:
        event.pop('hp_rows', None)
    (run / 'learner-events.jsonl').write_text('\n'.join(map(json.dumps, events)))
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
    results = preflight.checks(plan, facts)
    assert all(r.ok for r in results), preflight.report(results)
    assert by_name(results)['hp_score'].detail == 'c_hp 0.003, scope combat_ticket, ' \
        'segment format stpd/rl-fast-segment-4'
    # the "all_combats" experiment arm: the same path, gate and frozen recipe agreeing
    arm = {**facts, 'gate_learner_base': with_hp(gate_base(), 0.003, 'all_combats'),
           'frozen_recipe': with_hp(FROZEN, 0.003, 'all_combats')}
    results = preflight.checks(plan, arm)
    assert all(r.ok for r in results), preflight.report(results)
    assert 'scope all_combats' in by_name(results)['hp_score'].detail
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
    # a gate from before the HP score is refused, never read as c_hp = 0
    old = learner_config.build(gate_without_hp(), plan['knobs'], run)
    assert learner_config.recipe_drift(old, FROZEN) == [
        "loss.hp_advantage_coef: frozen 0.0, effective '<missing>'",
        "loss.hp_advantage_scope: frozen 'combat_ticket', effective '<missing>'"]
    refused = preflight.check_hp_score(plan, {**facts, 'gate_learner_base': gate_without_hp()})
    assert not refused.ok and 'patch it once' in refused.detail


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
    assert preflight.check_resume(plan).ok
    late = launch.build_plan('t1-r2', knobs(), run_profile.load_site(SITE), cards=2,
                             card_type='A100-40', layout_spec=None, env_cores=None,
                             smoke=False, probe_rows=[probe_row()], long_run=True,
                             chain={**chain, 'deadline_at': __import__('time').time() + 600})
    assert late['wall_seconds'] <= 600
    assert 'min_generation_seconds' in preflight.check_resume(late).detail
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
    site.write_text(text)
    cluster = FakeCluster(tmp_path, endings)
    args = __import__('argparse').Namespace(
        chain='lr1', profile=PROFILE, site=site, cards=2, card_type='A100-40', layout=None,
        set=[f'{k}={v}' for k, v in sets.items()], resume_checkpoint=None,
        eval_execution=None, eval_manifest=None, eval_baseline_plan=None,
        eval_a10_manifest=None, eval_slot_registered=False, dry_run=False)
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


def test_prep_estimate_is_only_a_warning():
    plan = plan_for(cards=3)
    for start in (None, 99_999.0):
        result = preflight.check_prep_schedulable(plan, {**good_facts(plan),
                                                         'prep_test_start_seconds': start})
        assert result.ok and result.warn
    assert 'WARN prep_schedulable' in preflight.report([result])


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
    with pytest.raises(run_profile.ProfileError, match='eval_a10_suite'):
        run_profile.load_profile(PROFILE, ['eval_a10_enabled=true'])
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
             r'temperature in \[1, 3\]'),
            ('{ a = ' + arm % '{ temperature = 1.0, uniform_mix = 0.2 }' + ' }',
             r'uniform_mix in \[0, 0.1\]'),
            ('{ a = ' + arm % '{ temperature = 1.0 }' + ' }', 'exactly'),
            ('{ a = ' + (arm % '{ temperature = 1.0, uniform_mix = 0.0 }').replace(
                '1.0, combat', '0.5, combat') + ' }', 'summing to 1')):
        with pytest.raises(run_profile.ProfileError, match=match):
            run_profile.load_profile(PROFILE, [f'exploration_ladder={bad}'])
    with pytest.raises(run_profile.ProfileError, match='replay_k 13 must not exceed env_slots'):
        run_profile.load_profile(PROFILE, ['replay_k=13'])
    with pytest.raises(run_profile.ProfileError, match='replay_k'):
        run_profile.load_profile(PROFILE, ['replay_k=0'])


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


FAKE_FAST_ENV = '''
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
    const id = `inst${n}`, floor = 3 + (n % 5);
    appendFileSync(join(dirname(path), "opens.jsonl"), JSON.stringify(c) + "\\n");
    if (c.seed.startsWith("bad")) {  // a start the native cannot mount
      emit({ event: "open_refused", slot: c.slot, game_ref: c.game_ref, reason: "reset_failed:x" });
      return;
    }
    const game = { slot: c.slot, game_ref: c.game_ref, game_instance_id: id };
    emit({ event: "opened", ...game, start: c.start });
    setTimeout(() => emit({ event: "closed", ...game, reason: "terminal", decisions: 10,
      public_facts: { floor }, terminal: { result: floor === 7 ? "victory" : "defeat" },
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
    built = readout.build(run, 1200)
    assert built['recipe'] == recipe
    text = readout.render(built)
    assert 'arm hot' in text and 'ascension A0' in text and 'k 3' in text


def test_env_driver_start_ticket_hook_and_refused_flags(tmp_path):
    world, admit = fake_driver_world(tmp_path), AdmitAll()
    tickets = tmp_path / 'tickets.mjs'
    tickets.write_text('let n = 0;\nexport function nextTicket({ tag }) { n += 1; '
                       'return { seed: `${tag}-t${n}`, start: { kind: "new_run" } }; }\n')
    out = tmp_path / 'hook'
    try:
        done = run_driver(world, out, admit.path, '--exploration-ladder', LADDER_JSON,
                          '--arm', 'hot', '--replay-k', '2', '--tickets', str(tickets))
    finally:
        admit.close()
    assert done.returncode == 0, done.stderr
    opens = [json.loads(line) for line in (out / 'opens.jsonl').read_text().splitlines()]
    assert [o['seed'] for o in opens] == [f'n0-p1-t{i // 2 + 1}' for i in range(len(opens))]
    for flags, message in (
            (('--exploration-ladder', LADDER_JSON, '--arm', 'cold', '--replay-k', '1'), '--arm'),
            (('--exploration-ladder', LADDER_JSON, '--arm', 'hot'), '--replay-k'),
            (('--exploration-ladder', LADDER_JSON, '--arm', 'hot', '--replay-k', '5'),
             'at most --slots 4'),
            (('--arm', 'hot', '--replay-k', '1'), '--exploration-ladder')):
        refused = run_driver(world, tmp_path / 'refused', tmp_path / 'none.sock', *flags)
        assert refused.returncode == 2 and message in refused.stderr, flags



def test_env_driver_stops_loudly_on_a_refused_or_failing_hook_ticket(tmp_path):
    """Review L1: a hook ticket that comes back refused never hot-loops open/refuse."""
    import time
    world = fake_driver_world(tmp_path)
    for name, body in (
            ('refused', 'export function nextTicket() { return { seed: "bad-save", '
                        'start: { kind: "new_run" } }; }\n'),
            ('throws', 'export function nextTicket() { throw new Error("no save left"); }\n')):
        tickets = tmp_path / f'{name}.mjs'
        tickets.write_text(body)
        admit, out, began = AdmitAll(), tmp_path / name, time.monotonic()
        try:
            done = run_driver(world, out, admit.path, '--exploration-ladder', LADDER_JSON,
                              '--arm', 'hot', '--replay-k', '1', '--tickets', str(tickets),
                              steady=40)
        finally:
            admit.close()
        assert done.returncode == 3 and 'driver stops' in done.stderr, done.stderr
        assert time.monotonic() - began < 20  # stopped at once, not at the end of steady
        [failed] = [r for r in readout.jsonl(out / 'driver.jsonl')
                    if r['event'] == 'ticket_failed']
        assert failed['reason'] == ('open_refused' if name == 'refused'
                                    else 'ticket_hook_failed')
        opens = out / 'opens.jsonl'
        assert len(opens.read_text().splitlines() if opens.exists() else []) <= 4  # one/slot


def test_readout_recipe_counts_only_admitted_games_with_evidence(tmp_path):
    driver = tmp_path / 'env-n0-1' / 'p1'
    driver.mkdir(parents=True)

    def closed(ref, group, floor, reason='terminal', result='defeat', failed=False, arm='mid'):
        return {'event': 'closed', 'game_ref': ref, 'reason': reason, 'arm': arm,
                'ascension': 10, 'replay_k': 2, 'group': group, 'member': 0, 'seed': 's',
                'start': 'new_run', 'decisions': 100, 'floor': floor, 'terminal': result,
                'evidence_failed': failed}
    records = [{'event': 'admission', 'game_ref': f'g{i}', 'ok': i != 4} for i in range(1, 7)]
    records += [closed('g1', 'k1', 10), closed('g2', 'k1', 16, result='victory'),
                closed('g3', 'k2', 12), closed('g4', 'k2', 30),  # g4 refused admission
                closed('g5', 'k3', 5, reason='unknown'), closed('g6', 'k3', 9, failed=True)]
    (driver / 'driver.jsonl').write_text('\n'.join(map(json.dumps, records)))
    recipe = readout.recipe_edge(tmp_path)
    mid = recipe['arm']['mid']
    assert (mid['games'], mid['other'], mid['wins'], mid['rows']) == (3, 3, 1, 400)
    assert mid['mean_floor'] == pytest.approx((10 + 16 + 12) / 3)
    k2 = recipe['k']['2']
    assert (k2['groups'], k2['full_groups'], k2['mean_floor_range']) == (3, 1, 6)
    assert recipe['ascension']['10']['games'] == 3 and recipe['start']['new_run']['rows'] == 400
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
    execution = json.loads((chain.dir / 'eval-execution.json').read_text())
    assert execution['storage_mode'] == 'shared_local' and execution['peer'] == {
        'kind': 'local', 'directory': chain.site['peer_dir']}
    assert execution['store'] == chain.site['peer_dir'] and execution['models'] == {}
