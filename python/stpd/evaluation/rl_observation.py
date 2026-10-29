"""Read saved evaluation ledgers into comparable JSON/CSV/SVG; never run a game.

Example: python -m stpd.evaluation.rl_observation --current result.json \
    --baseline baseline.json --baseline-selection T2b-2016 --output new-report
Details are optional saved episode reports (``episodes``) or trajectory analysis
(``rows``). They join by run_id, never by array position. Original ledgers own
qualification; this projection cannot admit scores or infer Boss clearance.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import random
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


def trajectory_details(episode: dict[str, Any]) -> dict[str, Any]:
    """Project an already saved P1 trajectory using its existing validator."""
    from stpd.p1.trajectory import validate_trajectory

    validate_trajectory(episode)
    observations = []
    for item in episode['observations']:
        observation = item['observation']
        run = (observation.get('run') or {}).get('content') or {}
        player = (observation.get('player') or {}).get('content') or {}
        scene = observation.get('scene') or {}
        combat = (observation.get('combat') or {}).get('content') or {}
        enemies = combat.get('enemies') or []
        row = {'index': item['index'], 'act': run.get('act'), 'floor': run.get('floor'),
               'kind': scene.get('native_kind'), 'family': scene.get('family'),
               'hp': player.get('hp'), 'max_hp': player.get('max_hp'),
               'context': {'encounter_type': combat.get('encounter_type'),
                   'enemies': [e.get('definition_id') for e in enemies],
                   'encounter_id': combat.get('encounter_id')}}
        if combat:
            row['kind'] = 'combat_turn'
        observations.append(row)
    terminal = next((o for o in reversed(observations) if o.get('family') == 'terminal'), {})
    return {'run_id': episode['run_id'], 'seed': episode['identity']['seed'],
            'trajectory': {k: episode[k] for k in ('run_id', 'identity', 'outcome')},
            'observations': observations, 'terminal_public': terminal}


def load_cohort(path: Path, selection: str | None = None, *,
                details: Path | None = None, detail_cohort: str | None = None) -> dict[str, Any]:
    raw = path.read_bytes()
    original = json.loads(raw)
    ledger = original.get('ledger_report', original)
    slots = ledger['slots']
    if len(slots) != ledger['planned']:
        raise ValueError('full_ledger_denominator_required')
    if selection is not None:
        slots = [s for s in slots if s['step'] == selection]
    if not slots or len({(s['step'], s['character'], s['ascension']) for s in slots}) != 1:
        raise ValueError('select_one_model_character_ascension')
    if len({s['seed'] for s in slots}) != len(slots):
        raise ValueError('duplicate_seed_requires_separate_cohort')
    records: dict[str, Any] = {}
    detail_sha = None
    if details is not None:
        detail_raw = details.read_bytes()
        detail_sha = hashlib.sha256(detail_raw).hexdigest()
        content = json.loads(detail_raw)
        for r in content.get('rows', content.get('episodes', [])):
            if detail_cohort is not None and r.get('cohort') != detail_cohort:
                continue
            if 'steps' in r and 'identity' in r:
                r = trajectory_details(r)
            if not isinstance(r.get('run_id'), str) or not r['run_id']:
                raise ValueError('detail_run_id_required')
            if r['run_id'] in records:
                raise ValueError('duplicate_detail_run')
            records[r['run_id']] = r
    rows = []
    protocols: dict[str, Any] = {}
    for slot in slots:
        r = records.get(slot.get('run_id'), {})
        if r and r.get('seed', slot['seed']) != slot['seed']:
            raise ValueError('detail_seed_mismatch')
        trajectory = r.get('trajectory') or {}
        outcome = trajectory.get('outcome') or {}
        if trajectory.get('run_id', slot.get('run_id')) != slot.get('run_id'):
            raise ValueError('detail_run_mismatch')
        terminal_result = (outcome.get('terminal') or {}).get('result')
        if (terminal_result is not None and slot.get('natural_result') is not None
                and terminal_result != slot['natural_result']):
            raise ValueError('detail_terminal_result_mismatch')
        if slot['status'] == 'finished' and (
                outcome.get('status') in {'unknown', 'censored'} or outcome.get('unknown') is True):
            raise ValueError('detail_terminal_status_mismatch')
        identity = trajectory.get('identity') or {}
        known = {k: identity[k] for k in ('host_pin', 'runtime_pin', 'input_spec',
                                             'initial_progress_sha256') if k in identity}
        policy = identity.get('behavior_policy') or r.get('behavior_policy')
        if policy is not None:
            known['behavior_policy'] = policy
        for key, value in known.items():
            if key in protocols and protocols[key] != value:
                raise ValueError('mixed_protocol_details')
            protocols[key] = value
        if outcome.get('max_observed_floor', slot.get('max_observed_floor')) != (
                slot.get('max_observed_floor')):
            raise ValueError('detail_floor_mismatch')
        floor = slot.get('max_observed_floor')
        if floor is not None and (type(floor) is not int or floor < 0):
            raise ValueError('nonnegative_integer_floor_required')
        scored = (ledger.get('score_admitted') is True and slot['status'] == 'finished'
                  and slot.get('score_eligible', True) is True
                  and slot.get('natural_result') in {'win', 'loss'})
        boss = outcome.get('boss_clearance') or r.get('boss_clearance') or {}
        observations = (r.get('observations')
                        or (r.get('game_metrics') or {}).get('observations', []))
        combats = [o for o in observations if o.get('kind') == 'combat_turn']
        # A scene's presence is public evidence; floor17 by itself is not Boss identity.
        reached = {act: any(o.get('act') == act and
                    (o.get('context') or {}).get('encounter_type') == 'boss' for o in combats)
                   for act in (1, 2)}
        final_combat = combats[-1] if combats else {}
        context = final_combat.get('context') or {}
        terminal_public: dict[str, Any] = r.get('terminal_public') or next(
            (o for o in reversed(observations) if o.get('kind') == 'game_over'), {})
        terminal_floor = terminal_public.get('floor')
        if terminal_floor is not None and (type(terminal_floor) is not int or terminal_floor < 0
                or floor is not None and terminal_floor > floor):
            raise ValueError('detail_terminal_floor_mismatch')
        if (terminal_public.get('result') is not None and slot.get('natural_result') is not None
                and terminal_public['result'] != slot['natural_result']):
            raise ValueError('detail_terminal_result_mismatch')
        rows.append({'seed': slot['seed'], 'run_id': slot.get('run_id'),
            'status': slot['status'], 'result': slot.get('natural_result'), 'scored': scored,
            'floor': floor, 'elapsed_seconds': slot.get('elapsed_seconds'),
            'death_floor': terminal_public.get('floor')
                if scored and slot.get('natural_result') == 'loss' else None,
            'boss_cleared_acts': boss.get('cleared_acts', []), 'boss_count': boss.get('count'),
            'boss_reached_acts': [act for act, seen in reached.items() if seen],
            'last_combat_floor': final_combat.get('floor'),
            'last_combat_act': final_combat.get('act'),
            'last_combat': context.get('encounter_id'),
            'last_combat_type': context.get('encounter_type'),
            'last_combat_enemies': context.get('enemies'),
            'last_combat_context': context or None,
            'details_present': bool(r)})
    scored_floors = [r['floor'] for r in rows if r['scored'] and r['floor'] is not None]
    losses = Counter(r['death_floor'] for r in rows if r['death_floor'] is not None)
    return {'source_sha256': hashlib.sha256(raw).hexdigest(), 'details_sha256': detail_sha,
        'selection': slots[0]['step'], 'protocol_observed': protocols,
        'host_kind': ledger.get('host_kind'),
        'character': slots[0]['character'], 'ascension': slots[0]['ascension'],
        'planned': len(rows), 'original_planned': ledger['planned'],
        'score_admitted': ledger.get('score_admitted') is True,
        'status_counts': dict(Counter(r['status'] for r in rows)),
        'wins': sum(r['scored'] and r['result'] == 'win' for r in rows),
        'losses': sum(r['scored'] and r['result'] == 'loss' for r in rows),
        'floor_n': len(scored_floors),
        'mean_floor': statistics.mean(scored_floors) if scored_floors else None,
        'median_floor': statistics.median(scored_floors) if scored_floors else None,
        'max_floor': max(scored_floors, default=None), 'loss_floor_counts': dict(losses),
        'death_floor_observed_n': sum(losses.values()),
        'boss1_reached_witnessed': sum(1 in r['boss_reached_acts'] for r in rows),
        'boss2_reached_witnessed': sum(2 in r['boss_reached_acts'] for r in rows),
        'boss1_cleared_witnessed': sum(1 in r['boss_cleared_acts'] for r in rows),
        'boss_count_missing': sum(r['boss_count'] is None for r in rows),
        'boss_reach_unobserved': sum(not r['boss_reached_acts'] for r in rows),
        'rows': rows}


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if any(before[k] != after[k] for k in ('host_kind', 'character', 'ascension')):
        raise ValueError('different_host_character_or_ascension')
    for key in before['protocol_observed'].keys() & after['protocol_observed'].keys():
        if before['protocol_observed'][key] != after['protocol_observed'][key]:
            raise ValueError('different_observed_protocol')
    b = {r['seed']: r for r in before['rows']}
    a = {r['seed']: r for r in after['rows']}
    rows = []
    for seed in sorted(b.keys() | a.keys()):
        x, y = b.get(seed, {}), a.get(seed, {})
        paired = (x.get('scored') and y.get('scored') and x.get('floor') is not None
                  and y.get('floor') is not None)
        delta = y['floor'] - x['floor'] if paired else None
        comparison = 'unpaired'
        if delta is not None:
            comparison = 'farther' if delta > 0 else 'nearer' if delta < 0 else 'same'
        rows.append({'seed': seed, 'before_floor': x.get('floor'), 'after_floor': y.get('floor'),
                     'delta': delta, 'comparison': comparison})
    differences = [r['delta'] for r in rows if r['delta'] is not None]
    interval = None
    required = None
    variance = statistics.variance(differences) if len(differences) >= 2 else None
    if len(differences) >= 2:
        rng = random.Random(20261029)
        samples = sorted(statistics.mean(rng.choices(differences, k=len(differences)))
                         for _ in range(10000))
        interval = [samples[250], samples[9749]]
        # Planning approximation for a TWO-SIDED paired test,80%power, delta=1.
        if variance is not None and variance > 0:
            required = max(2, math.ceil((1.96 + 0.84) ** 2 * variance))
    return {'paired_n': len(differences), 'seed_sets_equal': b.keys() == a.keys(),
        'counts': dict(Counter(r['comparison'] for r in rows)),
        'mean_delta': statistics.mean(differences) if differences else None,
        'approx_paired_seeds_for_ci95_halfwidth_one_floor': (
            max(2, math.ceil(1.96**2 * variance))
            if variance is not None and variance > 0 else None),
        'paired_delta_standard_error': (statistics.stdev(differences) / math.sqrt(len(differences))
                                        if len(differences) >= 2 else None),
        'paired_delta_sample_variance': variance,
        'power_assumptions': 'Distinct iid seeds, alpha=.05,80%power, normal approximation; '
                            'zero variance cannot plan n; deterministic repeats do not increase n.',
        'bootstrap95_fixed_seed_sensitivity': interval,
        'approx_paired_seeds_for_one_floor_80pct_power': required,
        'protocol_warning': 'Verify exact game/Host/model/action mode/manifest identities; '
                            'a fixed suite bootstrap is sensitivity, not population qualification.',
        'rows': rows}


def write_report(cohorts: dict[str, dict[str, Any]], output: Path) -> dict[str, Any]:
    comparisons = {name: compare(cohort, cohorts['current'])
                   for name, cohort in cohorts.items() if name != 'current'}
    output.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {'schema': 'stpd/rl-observation-report-1', 'cohorts': cohorts,
              'comparisons': comparisons,
              'non_claims': ['Missing Boss witnesses are not zero clearances.',
                  'A floor threshold is not a Boss encounter.',
                  'No gameplay, storage mutation, training admission or model qualification.']}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    with (output / 'seeds.csv').open('w', newline='', encoding='utf-8') as file:
        columns = ['cohort', 'seed', 'run_id', 'status', 'result', 'scored', 'floor',
                   'death_floor', 'last_combat_floor', 'last_combat_act', 'last_combat',
                   'last_combat_type', 'last_combat_enemies', 'boss_count']
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for name, c in cohorts.items():
            for r in c['rows']:
                writer.writerow({'cohort': name, **{k: r[k] for k in columns[1:]}})
    with (output / 'paired.csv').open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=['reference', 'seed', 'before_floor',
                                                  'after_floor', 'delta', 'comparison'])
        writer.writeheader()
        for name, c in comparisons.items():
            writer.writerows({'reference': name, **r} for r in c['rows'])
    svg = distribution_svg(cohorts)
    (output / 'distribution.svg').write_text(svg, encoding='utf-8')
    lines = ['# Saved fixed-seed evaluation', '',
             '| Cohort | Planned | Scored floors | Win / loss | Mean | Median | Max |',
             '|---|---:|---:|---|---:|---:|---:|']
    for name, c in cohorts.items():
        lines.append(f"| {name} | {c['planned']} | {c['floor_n']} | {c['wins']} / {c['losses']} | "
                     f"{c['mean_floor']} | {c['median_floor']} | {c['max_floor']} |")
    lines += ['', '![Loss histogram and observed reach](distribution.svg)', '',
        'Boss metrics retain missing witnesses; seeds.csv retains missing combat IDs.',
        'Observed reach is the count with max floor >= x, not proof of visiting every floor.',
        'Verify exact producer/game/seed/selection protocol before interpreting changes.', '']
    (output / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
    return report


def distribution_svg(cohorts: dict[str, dict[str, Any]]) -> str:
    colors = ['#386cb0', '#f28e2b', '#59a14f']
    max_floor = max((r['floor'] or 0 for c in cohorts.values() for r in c['rows']), default=1)
    max_floor = max(max_floor, 1)
    max_count = max(c['planned'] for c in cohorts.values())
    width, height = 1100, 690
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="white"/>',
             '<g font-family="sans-serif" font-size="14" fill="#222">',
             '<text x="55" y="28" font-size="21">Saved evaluation: '
             'losses and observed reach</text>']
    for i, (name, c) in enumerate(cohorts.items()):
        color = colors[i % len(colors)]
        parts.append(f'<text x="{55+i*340}" y="57" fill="{color}">'
                     f'{html.escape(name)} / {html.escape(c["selection"])}: '
                     f'n={c["planned"]}, scored={c["floor_n"]}, '
                     f'death floor={c["death_floor_observed_n"]}</text>')
    for panel, title in enumerate(('Natural losses per terminal floor',
                                  'Observed max floor >= x (incomplete = lower bound)')):
        top, bottom = 100 + panel * 280, 320 + panel * 280
        parts.append(f'<text x="55" y="{top-12}">{title.replace(">", "&gt;")}</text>')
        for count in range(0, max_count+1, max(1, max_count//5)):
            y = bottom - count / max_count * 210
            parts += [f'<path d="M55 {y} H1070" stroke="#ddd"/>',
                      f'<text x="20" y="{y+5}">{count}</text>']
        step = 1000 / (max_floor + 1)
        for floor in range(max_floor + 1):
            x = 55 + floor * step
            if floor % 2 == 0 or floor in (8, 17):
                parts.append(f'<text x="{x}" y="{bottom+22}">{floor}</text>')
        for i, (_name, c) in enumerate(cohorts.items()):
            color = colors[i % len(colors)]
            points = []
            for floor in range(max_floor + 1):
                x = 55 + floor * step
                count = (c['loss_floor_counts'].get(floor, 0) if panel == 0 else
                         sum(r['floor'] is not None and r['floor'] >= floor for r in c['rows']))
                y = bottom - count / max_count * 210
                if panel == 0 and count:
                    bar = step / (len(cohorts)+1)
                    parts.append(f'<rect x="{x+i*bar}" y="{y}" width="{bar}" '
                                 f'height="{bottom-y}" fill="{color}"/>')
                points.append(f'{x},{y}')
            if panel == 1:
                parts.append(f'<polyline points="{" ".join(points)}" fill="none" '
                             f'stroke="{color}" stroke-width="3"/>')
    parts += ['<text x="55" y="665">Unknown / censored / unstarted remain in JSON and CSV; '
              'Boss clearance requires explicit witnesses.</text>', '</g></svg>']
    return ''.join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('current', 'baseline', 'previous'):
        parser.add_argument('--' + name, type=Path, required=name == 'current')
        parser.add_argument('--' + name + '-selection')
        parser.add_argument('--' + name + '-details', type=Path)
        parser.add_argument('--' + name + '-detail-cohort')
    parser.add_argument('--output', type=Path, required=True)
    args = vars(parser.parse_args())
    cohorts = {name: load_cohort(args[name], args[name + '_selection'],
        details=args[name + '_details'], detail_cohort=args[name + '_detail_cohort'])
        for name in ('baseline', 'previous', 'current') if args[name] is not None}
    write_report(cohorts, args['output'])


if __name__ == '__main__':
    main()
