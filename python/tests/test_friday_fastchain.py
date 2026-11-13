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
    names = ('run_profile', 'learner_config', 'layout', 'preflight', 'launch', 'readout')
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


run_profile, learner_config, layout, preflight, launch, readout = load_fastchain()


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


def good_facts(plan):
    k = plan['knobs']
    return {'src_rev': f'int {k["env_pkg_build"]}',
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
    ({}, {'src_features': {'residual': False}}, 'source_features'),
    ({}, {'src_features': {'stage_options': False}}, 'source_features'),
    ({'shards_adopt_unsharded_checkpoint': True}, {'src_features': {
        name: name != 'adopt_unsharded' for name in launch.FEATURES}}, 'source_features'),
    ({'env_candidate_artifact_sha256': ''}, {}, 'env_package'),
    ({'env_candidate_artifact_sha256': 'b77f5139'}, {}, 'env_package'),
    ({}, {'src_checkout': {'git': False}}, 'producer_identity'),
    ({}, {'src_checkout': {'git': True, 'head': 'c' * 40, 'dirty': True}}, 'producer_identity'),
    ({'max_versions': 100000}, {}, 'frozen_recipe'),
    ({}, {'gate_learner_base': None}, 'frozen_recipe'),
    ({}, {'frozen_recipe': {
        **FROZEN, 'lr': 0.0002}}, 'frozen_recipe'),
    ({}, {'compile_warmup_dumps': {'/x/missing.pt': False}}, 'compile_warmup'),
    ({}, {'compile_warmup_dumps': {}}, 'compile_warmup'),
    ({}, {'src_features': {name: name != 'gc_freeze' for name in launch.FEATURES}},
     'source_features'),
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


def test_old_build_keeps_the_two_worker_workaround_only():
    """Without #523 a437612d the SGD budget counts idle local pools: gate workers refuse,
    the 2/2 workaround (and no scratch flag) passes; with the commit the gate values pass."""
    plan = plan_for(cards=3, wall_seconds=2400)
    old = {**good_facts(plan), 'src_features': {
        name: name != 'budget_counts_remote' for name in launch.FEATURES}}
    assert plan['knobs']['preparation_workers'] == 12
    assert not preflight.check_features(plan, old).ok
    workaround = plan_for(cards=3, wall_seconds=2400, preparation_workers=2,
                          assembly_workers=2, prep_scratch_min_free=False)
    assert preflight.check_features(workaround, old).ok
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


def test_long_run_and_smoke_are_exclusive():
    with pytest.raises(SystemExit):
        plan_for(cards=2, smoke=True, long_run=True)
