#!/usr/bin/env python3
"""Bounded natural-pair quick check over normal Friday evaluation batches.

Every World offer, audit, save and readback stays in evaluate/EvaluationLedger.
This controller advances through the original pool; it never repairs a ledger
or retries a baseline/current seed in the same round.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import signal
import statistics
import threading
import time
from pathlib import Path
from typing import Any, cast

import evaluate
from batch import hashed, private_root, read_pinned, save

SCHEMA = 'spireagent/friday-natural-quick-1'
PROTOCOL_KEYS = {'host_pin', 'runtime_pin', 'input_spec', 'initial_progress_sha256',
                 'behavior_policy'}


def reference(path: Path) -> dict[str, str]:
    return {'path': str(path), 'sha256': hashed(path)}


def prepare(execution_path: Path, output: Path, *, manifest_path: Path,
            runtime_source: Path, models: dict[str, Any], baseline_cache: list[dict[str, Any]],
            baseline_unknown_seeds: list[str], version: int, updates: int,
            wall_seconds: int = 7200) -> dict[str, Any]:
    from spireagent.workbench.reference_suite import load_manifest
    from stpd.evaluation.suites import RL3_LEARNING_CURVE_SUITE_ID

    execution = json.loads(execution_path.read_bytes())
    if set(models) != {'current', 'baseline'} or set(execution['models']) != set(models):
        raise ValueError('exact_current_and_start_models_required')
    for role in models:
        value = {**execution, 'models': {role: execution['models'][role]}}
        evaluate.validate_execution(value, {role: role})
        if value['models'][role]['artifact_id'] != models[role]['model_artifact_id']:
            raise ValueError('quick_model_artifact_binding_mismatch')
        package = Path(value['models'][role]['package_directory'])
        if json.loads((package / 'model.json').read_bytes())['package_sha256'] \
                != models[role]['model_sha256']:
            raise ValueError('quick_package_hash_binding_mismatch')
    _, manifest = load_manifest(manifest_path, hashed(manifest_path))
    if (manifest['suite_id'] != RL3_LEARNING_CURVE_SUITE_ID or len(manifest['seeds']) != 100
            or not isinstance(baseline_unknown_seeds, list)
            or any(not isinstance(seed, str) for seed in baseline_unknown_seeds)
            or not set(baseline_unknown_seeds) <= set(manifest['seeds'])
            or type(wall_seconds) is not int or not 1200 <= wall_seconds <= 7200
            or not isinstance(baseline_cache, list) or not baseline_cache):
        raise ValueError('registered100_bounded_quick_with_baseline_required')
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    private_root(output)
    save(output / 'execution.json', execution)
    (output / 'manifest.json').write_bytes(manifest_path.read_bytes())
    plan = {'schema': SCHEMA, 'execution': reference(output / 'execution.json'),
            'manifest': reference(output / 'manifest.json'), 'source_root': str(runtime_source),
            'models': models, 'baseline_cache': baseline_cache,
            'baseline_unknown_seeds': baseline_unknown_seeds, 'target_pairs': 20,
            'workers': 4, 'selected_limit': 200, 'wall_seconds': wall_seconds,
            'version': version, 'updates': updates,
            'retry_unknown': False, 'created_at': time.time()}
    save(output / 'plan.json', plan)
    return plan


def load_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_bytes())
    if (plan.get('schema') != SCHEMA or plan.get('target_pairs') != 20
            or plan.get('workers') != 4 or plan.get('retry_unknown') is not False):
        raise ValueError('frozen_natural_quick_plan_required')
    read_pinned(plan['execution'])
    read_pinned(plan['manifest'])
    return cast(dict[str, Any], plan)


def launcher(path: Path, plan: dict[str, Any]) -> str:
    return evaluate.launcher(path, plan, program='quick.py')


def batch_reference(plan: Path) -> dict[str, Any]:
    return {name: reference(plan.parent / filename) for name, filename in
            (('plan', 'plan.json'), ('result', 'result.json'), ('snapshot', 'snapshot.json'))}


def read_batch(bundle: dict[str, Any], expected_model: dict[str, Any],
               round_plan: dict[str, Any]) -> dict[str, Any]:
    """A failed audit stays failed; only original admitted natural rows qualify."""
    for name in ('plan', 'result', 'snapshot'):
        read_pinned(bundle[name])
    if read_pinned(bundle['snapshot']).get('full_readback') is not True:
        raise ValueError('normal_batch_full_snapshot_required')
    plan, original, files = evaluate.read_observation_report(
        Path(bundle['result']['path']), Path(bundle['plan']['path']))
    execution = read_pinned(plan['execution'])
    expected_execution = read_pinned(round_plan['execution'])
    if (plan['manifest']['sha256'] != round_plan['manifest']['sha256']
            or len(plan['selections']) != 1
            or execution['agent_adapter'] != expected_execution['agent_adapter']
            or execution['retention'] != expected_execution['retention']):
        raise ValueError('quick_batch_protocol_binding_mismatch')
    model = execution['models'][next(iter(plan['selections'].values()))]
    if model['artifact_id'] != expected_model['model_artifact_id']:
        raise ValueError('quick_batch_model_mismatch')
    manifest = read_pinned(round_plan['manifest'])
    for slot in original['ledger_report']['slots']:
        ordinal = slot['seed_ordinal']
        if type(ordinal) is not int or not 1 <= ordinal <= 100 \
                or manifest['seeds'][ordinal - 1] != slot['seed']:
            raise ValueError('original_one_based_seed_ordinal_required')
    producers = []
    for item in files['details']['episodes']:
        identity = item['trajectory']['identity']
        if (identity['model_artifact_id'] != expected_model['model_artifact_id']
                or identity['model_sha256'] != expected_model['model_sha256']):
            raise ValueError('quick_trajectory_model_binding_mismatch')
        if identity['producer'] not in producers:
            producers.append(identity['producer'])
    cohort = files['cohort']
    if (cohort['score_admitted'] and cohort['floor_n']
            and (not PROTOCOL_KEYS <= cohort['protocol_observed'].keys()
                 or cohort['protocol_observed']['behavior_policy']['mode'] != 'argmax')):
        raise ValueError('complete_argmax_observation_protocol_required')
    slots = original['ledger_report']['slots']
    closed = (original.get('error_type') is None and not any(
        s.get('execution_reason') == 'typed_original_verification_failed' for s in slots) and all(
        s['status'] in {'finished', 'unknown'} and s.get('cleanup_accepted') is True
        and (s['status'] == 'unknown' or type(s.get('max_observed_floor')) is int
             and s.get('natural_result') in {'win', 'loss'}) for s in slots))
    return {'reference': bundle, 'cohort': cohort, 'closed': closed, 'producers': producers,
            'raw_score_admitted': original['ledger_report']['score_admitted'],
            'audit': original['ledger_report'].get('formal_audit'),
            'world_offers': sum(s.get('world_mutation_offered') is True for s in slots)}


def qualified(batch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r['seed']: r for r in batch['cohort']['rows']
            if batch['closed'] and batch['raw_score_admitted'] is True
            and r['scored'] is True and r['status'] == 'finished'
            and r['result'] in {'win', 'loss'} and r['floor'] is not None}


def next_ordinals(seeds: list[str], cursor: int, deficit: int,
                  unavailable_baseline: set[str]) -> tuple[list[int], int]:
    selected: list[int] = []
    while cursor <= len(seeds) and len(selected) < deficit:
        if seeds[cursor - 1] not in unavailable_baseline:
            selected.append(cursor)
        cursor += 1
    return selected, cursor


def aggregate(rows: list[dict[str, Any]], role: str, protocol: dict[str, Any],
              character: str, ascension: int) -> dict[str, Any]:
    floors = [r['floor'] for r in rows if r['scored'] and r['floor'] is not None]
    return {'schema': 'spireagent/quick-original-batch-projection-1', 'selection': role,
            'host_kind': 'managed', 'character': character, 'ascension': ascension,
            'protocol_observed': protocol, 'rows': rows, 'planned': len(rows),
            'floor_n': len(floors), 'wins': sum(r['scored'] and r['result'] == 'win' for r in rows),
            'losses': sum(r['scored'] and r['result'] == 'loss' for r in rows),
            'mean_floor': statistics.mean(floors) if floors else None,
            'median_floor': statistics.median(floors) if floors else None,
            'max_floor': max(floors, default=None),
            'loss_floor_counts': dict(collections.Counter(
                r['death_floor'] for r in rows if r['death_floor'] is not None)),
            'death_floor_observed_n': sum(r['death_floor'] is not None for r in rows)}


def run(path: Path) -> dict[str, Any]:
    from stpd.evaluation.rl_observation import compare, write_report
    from stpd.p1.export import adapter_identity

    plan = load_plan(path)
    root = private_root(path.parent)
    if not os.environ.get('SLURM_JOB_ID') or int(
            os.environ.get('SLURM_CPUS_PER_TASK', '0')) < 4:
        raise ValueError('natural_quick_requires_normal_four_CPU_allocation')
    execution = read_pinned(plan['execution'])
    if execution['agent_adapter'] != adapter_identity():
        raise ValueError('frozen_quick_action_adapter_changed')
    intent = root / 'run.intent.json'
    with intent.open('x') as stream:
        json.dump({'at': time.time(), 'plan_sha256': hashed(path)}, stream)
    began = time.time()
    deadline = began + plan['wall_seconds']
    manifest = read_pinned(plan['manifest'])
    seeds = manifest['seeds']
    baseline_good: dict[str, dict[str, Any]] = {}
    current_good: dict[str, dict[str, Any]] = {}
    current_rows: dict[str, dict[str, Any]] = {}
    attempted_baseline = set(plan['baseline_unknown_seeds'])
    batches: list[dict[str, Any]] = []
    baseline_bundles = list(plan['baseline_cache'])
    protocol: dict[str, Any] | None = None
    active: list[Path] = []
    stopping = threading.Event()

    def stop(_number: int, _frame: object) -> None:
        (root / 'STOP').touch()

    previous = {number: signal.signal(number, stop)
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)}

    def consume(batch: dict[str, Any], role: str) -> None:
        nonlocal protocol
        good = qualified(batch)
        observed = batch['cohort']['protocol_observed']
        if good:
            if protocol is not None and observed != protocol:
                raise ValueError('quick_cached_or_current_protocol_changed')
            protocol = observed
        if role == 'baseline':
            attempted_baseline.update(r['seed'] for r in batch['cohort']['rows']
                                      if r['status'] != 'not_started')
            baseline_good.update(good)
        else:
            current_rows.update({r['seed']: r for r in batch['cohort']['rows']})
            current_good.update(good)
        batches.append({'role': role, **batch})

    def observe_stop() -> None:
        while not stopping.wait(.5):
            current = active[:1]
            if (root / 'STOP').exists() and current:
                (current[0] / 'STOP').touch()

    observer = threading.Thread(target=observe_stop, daemon=True)
    observer.start()
    reason = None
    try:
        for bundle in plan['baseline_cache']:
            batch = read_batch(bundle, plan['models']['baseline'], plan)
            consume(batch, 'baseline')
            if not batch['closed']:
                reason = 'non_unknown_or_unsettled_batch_requires_owner_read'
                break
        cursor = 1
        while reason is None and len(set(current_good) & set(baseline_good)) < 20:
            if (root / 'STOP').exists() or deadline - time.time() <= 1080:
                reason = 'round_stop_or_wall_limit'
                break
            unavailable = attempted_baseline - set(baseline_good)
            deficit = 20 - len(set(current_good) & set(baseline_good))
            ordinals, cursor = next_ordinals(seeds, cursor, deficit, unavailable)
            if not ordinals:
                reason = 'original100_pool_exhausted'
                break
            for role in ('current', 'baseline'):
                selected = (ordinals if role == 'current' else
                    [n for n in ordinals if seeds[n - 1] in current_good
                     and seeds[n - 1] not in baseline_good])
                if not selected:
                    continue
                if (root / 'STOP').exists() or deadline - time.time() <= 1080:
                    reason = 'round_stop_or_wall_limit'
                    break
                batch_root = root / 'batches' / f'{len(batches):03d}-{role}'
                batch_root.mkdir(parents=True)
                batch_execution = {**execution, 'models': {role: execution['models'][role]}}
                input_path = batch_root / 'input-execution.json'
                save(input_path, batch_execution)
                evaluate.prepare(input_path, batch_root / 'plan', selections={role: role},
                    workers=4, wall_seconds=min(7200, int(deadline - time.time())),
                    episode_seconds=900, manifest_path=Path(plan['manifest']['path']),
                    seed_ordinals=selected, runtime_source=Path(plan['source_root']),
                    observation_report=True, unknown_policy='continue_cleaned')
                batch_path = batch_root / 'plan' / 'plan.json'
                save(root / 'state.json', {'at': time.time(), 'cursor': cursor,
                    'active_plan': reference(batch_path), 'batches': batches})
                active[:] = [batch_path.parent]
                evaluate.run(batch_path, on_stop=lambda: (root / 'STOP').touch())
                active.clear()
                if (batch_path.parent / 'STOP').exists():
                    (root / 'STOP').touch()
                bundle = batch_reference(batch_path)
                batch = read_batch(bundle, plan['models'][role], plan)
                consume(batch, role)
                if role == 'baseline':
                    baseline_bundles.append(bundle)
                save(root / 'state.json', {'at': time.time(), 'cursor': cursor,
                    'active_plan': None, 'batches': batches})
                if not batch['closed']:
                    reason = 'non_unknown_or_unsettled_batch_requires_owner_read'
                    break
            if reason:
                break
    finally:
        stopping.set()
        observer.join(timeout=2)
        for number, handler in previous.items():
            signal.signal(number, handler)
    pairs = [s for s in seeds if s in current_good and s in baseline_good]
    if len(pairs) > 20:
        raise ValueError('natural_quick_target_overshoot')
    paired_baseline = statistics.mean(baseline_good[s]['floor'] for s in pairs) if pairs else None
    paired_current = statistics.mean(current_good[s]['floor'] for s in pairs) if pairs else None
    cohorts = {'current': aggregate([current_good[s] for s in pairs], 'current', protocol or {},
                                  manifest['character'], manifest['ascension']),
               'baseline': aggregate([baseline_good[s] for s in pairs], 'baseline', protocol or {},
                                    manifest['character'], manifest['ascension'])}
    comparison = compare(cohorts['baseline'], cohorts['current'])
    report = write_report(cohorts, root / 'report', scope_note=
        'Qualified natural-pair subset; original attempts and exclusions remain in ../result.json.')
    del report  # Existing JSON/CSV/SVG writer owns these artifacts.
    result = {'schema': SCHEMA, 'plan_sha256': hashed(path), 'complete': len(pairs) == 20,
              'status': 'complete' if len(pairs) == 20 else 'incomplete', 'reason': reason,
              'version': plan['version'], 'updates': plan['updates'], 'target_pairs': 20,
              'paired_seeds': pairs, 'paired_n': len(pairs), 'baseline_mean': paired_baseline,
              'current_mean': paired_current, 'comparison': comparison,
              'model_artifact_id': plan['models']['current']['model_artifact_id'],
              'action_adapter': execution['agent_adapter'], 'baseline_cache': baseline_bundles,
              'batches': batches, 'report': str(root / 'report'), 'finished_at': time.time(),
              'new_current_planned_slots': len(current_rows),
              'new_current_world_offers': sum(b['world_offers'] for b in batches
                                               if b['role'] == 'current'),
              'new_baseline_world_offers': sum(b['world_offers'] for b in batches
                  if b['role'] == 'baseline' and b['reference'] not in plan['baseline_cache']),
              'raw_ledger_flags_unchanged': True,
              'meaning': '20 qualified natural pairs for a quick alarm; original batches, '
                         'audit failures, unknowns and unpaired natural outcomes are retained.'}
    save(root / 'result.json', result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run'])
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.plan.resolve())
    print(json.dumps({k: result[k] for k in ('status', 'paired_n', 'baseline_mean', 'current_mean')}))


if __name__ == '__main__':
    main()
