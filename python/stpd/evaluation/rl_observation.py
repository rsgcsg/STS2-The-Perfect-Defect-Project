"""Read saved evaluation ledgers into comparable JSON/CSV/SVG; never run a game.

Example: python -m stpd.evaluation.rl_observation --current result.json \
    --baseline baseline.json --baseline-selection T2b-2016 --output new-report
Details are optional saved episode reports (``episodes``) or trajectory analysis
(``rows``). They join by run_id, never by array position. Original ledgers own
qualification; this projection cannot admit scores or infer Boss clearance.

``--excluded-facts FACTS.json`` ({"games": {run_id: {"floor", "bosses"}}}) supplies
public progress read from excluded originals for the reward lower bound only.
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
        floor = slot.get('max_observed_floor')
        if floor is not None and (type(floor) is not int or floor < 0):
            raise ValueError('nonnegative_integer_floor_required')
        lower_bound = floor
        public_floor = slot.get('public_max_observed_floor')
        if (public_floor is not None and floor is None
                and slot['status'] in {'unknown', 'censored', 'save_failed'}):
            if type(public_floor) is not int or public_floor < 0:
                raise ValueError('nonnegative_integer_floor_required')
            # Verified public progress before the error: a bound, never an endpoint.
            lower_bound = public_floor
        detail_floor = outcome.get('max_observed_floor', floor)
        if detail_floor is not None and (type(detail_floor) is not int or detail_floor < 0):
            raise ValueError('nonnegative_integer_floor_required')
        if detail_floor != floor:
            if (slot['status'] not in {'unknown', 'censored', 'save_failed'} or
                    detail_floor is None or floor is not None and detail_floor < floor):
                raise ValueError('detail_floor_mismatch')
            # A verified unresolved prefix adds observations, never a scored endpoint.
            lower_bound = max(detail_floor, lower_bound or 0)
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
                   for act in (1, 2, 3)}
        final_combat = combats[-1] if combats else {}
        context = final_combat.get('context') or {}
        terminal_public: dict[str, Any] = r.get('terminal_public') or next(
            (o for o in reversed(observations) if o.get('kind') == 'game_over'), {})
        terminal_floor = terminal_public.get('floor')
        if terminal_floor is not None and (type(terminal_floor) is not int or terminal_floor < 0
                or lower_bound is not None and terminal_floor > lower_bound):
            raise ValueError('detail_terminal_floor_mismatch')
        if (terminal_public.get('result') is not None and slot.get('natural_result') is not None
                and terminal_public['result'] != slot['natural_result']):
            raise ValueError('detail_terminal_result_mismatch')
        rows.append({'seed': slot['seed'], 'run_id': slot.get('run_id'),
            'status': slot['status'], 'result': slot.get('natural_result'), 'scored': scored,
            'floor': floor, 'observed_floor_lower_bound': lower_bound,
            'elapsed_seconds': slot.get('elapsed_seconds'),
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
        'boss3_reached_witnessed': sum(3 in r['boss_reached_acts'] for r in rows),
        'boss2_cleared_witnessed': sum(2 in r['boss_cleared_acts'] for r in rows),
        'boss3_cleared_witnessed': sum(3 in r['boss_cleared_acts'] for r in rows),
        'boss1_cleared_witnessed': sum(1 in r['boss_cleared_acts'] for r in rows),
        'boss_count_missing': sum(r['boss_count'] is None for r in rows),
        'boss_reach_unobserved': sum(not r['boss_reached_acts'] for r in rows),
        'rows': rows}


def progress_summary(
    rows: list[dict[str, Any]], *, through_floor: int | None = None,
) -> dict[str, Any]:
    """One descriptive ruler for already projected model, Human or mixed records.

    Callers retain source qualification and lineage. ``scored`` is supplied by the
    owning admission path, never inferred here. Human/mixed overlays may use False
    for every row; observations still contribute positive lower-bound witnesses.
    ``endpoint_complete=True`` may describe a verified Human endpoint without
    admitting it as a model score. No missing Boss witness becomes a negative.
    """
    planned = len(rows)
    lower = [r.get('observed_floor_lower_bound', r.get('floor')) for r in rows]
    if any(x is not None and (type(x) is not int or x < 0) for x in lower):
        raise ValueError('nonnegative_integer_floor_required')
    if through_floor is not None and (type(through_floor) is not int or through_floor < 0):
        raise ValueError('nonnegative_integer_floor_required')
    reach = []
    last_floor = max(max((x for x in lower if x is not None), default=0), through_floor or 0)
    for floor in range(last_floor + 1):
        witnessed = sum(x is not None and x >= floor for x in lower)
        # A verified endpoint is separate from model score admission.
        unresolved = sum((x is None or x < floor) and not (
            (r.get('endpoint_complete') is True or r.get('scored') and
             r.get('status') == 'finished') and x is not None)
            for r, x in zip(rows, lower, strict=True))
        reach.append({'floor': floor, 'planned': planned, 'witnessed': witnessed,
            'unresolved': unresolved, 'possible_upper': witnessed + unresolved,
            'lower_fraction': witnessed / planned if planned else None,
            'upper_fraction': (witnessed + unresolved) / planned if planned else None})
    bosses = []
    for act in (1, 2, 3):
        reached = sum(act in r.get('boss_reached_acts', []) for r in rows)
        cleared = sum(act in r.get('boss_cleared_acts', []) for r in rows)
        natural_reached = sum(act in r.get('boss_reached_acts', []) and
                              bool(r.get('scored')) for r in rows)
        natural_cleared = sum(act in r.get('boss_cleared_acts', []) and
                              bool(r.get('scored')) for r in rows)
        bosses.append({'act': act, 'planned': planned, 'reached_witnessed': reached,
            'cleared_witnessed': cleared, 'scored_reached_witnessed': natural_reached,
            'scored_cleared_witnessed': natural_cleared,
            'details_present_n': sum(bool(r.get('details_present')) for r in rows),
            'clear_without_reach_witness_n': sum(act in r.get('boss_cleared_acts', [])
                and act not in r.get('boss_reached_acts', []) for r in rows)})
    # This is an index of terminal-floor combat context, NOT a causal death label.
    terminal_combats = Counter((r.get('last_combat_type') or 'unobserved')
        if r.get('last_combat_floor') == r.get('death_floor') and
           r.get('death_floor') is not None else 'unobserved'
        for r in rows if r.get('scored') and r.get('result') == 'loss')
    return {'planned': planned, 'status_counts': dict(Counter(r['status'] for r in rows)),
        'reach': reach, 'bosses': bosses,
        'terminal_floor_last_combat_types': dict(terminal_combats),
        'meaning': 'Observed max-floor progress; bounds are missing-outcome bounds, '
                   'not confidence intervals. Boss counts are positive witnesses per act/run; '
                   'absence is not zero. Human/mixed overlays never grant evaluation eligibility.'}


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


def _reward_spec() -> dict[str, Any]:
    from stpd.rl.reward import REWARD_SPEC

    return REWARD_SPEC


def _loss_reward(floor: int, bosses: int, ascension: Any) -> float | None:
    spec = _reward_spec()
    finish = spec['finish_floors'].get(str(ascension))
    if not finish:
        return None
    return float(spec['loss_floor_scale'] * min(floor / finish, 1.0)
                 ** spec['loss_progress_exponent'] + spec['boss_bonus'] * bosses)


def _row_bosses(row: dict[str, Any]) -> int:
    # Same public witness as training: outcome.boss_clearance; missing adds nothing.
    count = row.get('boss_count')
    return count if type(count) is int else len(row.get('boss_cleared_acts') or [])


def official_reward(row: dict[str, Any], ascension: Any) -> float | None:
    """Episode reward v2 of one admitted natural row; None means excluded."""
    if not row.get('scored') or row.get('status') != 'finished':
        return None
    if row.get('result') == 'win':
        return float(_reward_spec()['win'])
    floor = row.get('floor')
    if row.get('result') != 'loss' or type(floor) is not int or floor < 0:
        return None
    return _loss_reward(floor, _row_bosses(row), ascension)


def reward_summary(cohort: dict[str, Any],
                   excluded_facts: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Official reward-v2 mean plus the diagnostic bound [L, U] over every planned row.

    L scores each excluded row (unknown, save_failed, censored, not_started, missing
    floor) as a loss at its highest public floor and witnessed Bosses before the
    error (``excluded_facts`` by run_id, else the row's verified lower bound, else
    0); U scores it as a win (the reward maximum). Neither is a natural endpoint
    and neither changes the official score or its denominator.
    """
    ascension = cohort.get('ascension')
    planned = len(cohort['rows'])
    spec = _reward_spec()
    if str(ascension) not in spec['finish_floors']:
        # Never a partial mean (wins alone): this difficulty has no declared loss scale.
        return {'reward_spec': {k: spec[k] for k in ('id', 'version')},
            'ascension': ascension, 'planned': planned, 'scored': None, 'excluded': None,
            'mean': None, 'reason': 'reward_spec_missing_ascension',
            'scored_loss_boss_witness_missing': None,
            'lower_bound': {'L': None, 'U': None,
                            'meaning': 'Not computed: reward_spec_missing_ascension.'},
            'excluded_rows': [], 'by_seed': {}, 'lower_by_seed': {}}
    official: dict[str, float] = {}
    lower: dict[str, float] = {}
    excluded = []
    for row in cohort['rows']:
        value = official_reward(row, ascension)
        if value is not None:
            official[row['seed']] = lower[row['seed']] = value
            continue
        fact = (excluded_facts or {}).get(row.get('run_id') or '')
        if fact is not None:
            floor, bosses, source = fact['floor'], fact['bosses'], 'excluded_facts'
        else:
            bound = row.get('observed_floor_lower_bound')
            floor = bound if type(bound) is int else 0
            bosses = len(row.get('boss_cleared_acts') or [])
            source = 'row_lower_bound' if type(bound) is int else 'unobserved_zero'
        if type(floor) is not int or floor < 0 or type(bosses) is not int or bosses < 0:
            raise ValueError('nonnegative_integer_excluded_fact_required')
        value = _loss_reward(floor, bosses, ascension)
        lower[row['seed']] = value if value is not None else 0.0
        excluded.append({'seed': row['seed'], 'run_id': row.get('run_id'),
                         'status': row.get('status'), 'public_floor_lower_bound': floor,
                         'bosses_witnessed': bosses, 'source': source})
    win = float(spec['win'])
    total = sum(official.values())
    # A scored loss without a Boss witness adds no Boss bonus (same as training), so
    # the official mean can be low by 0.1 per uncounted Boss: report the count with it.
    witness_missing = sum(row['seed'] in official and row.get('result') == 'loss'
                          and type(row.get('boss_count')) is not int
                          and not row.get('boss_cleared_acts') for row in cohort['rows'])
    return {'reward_spec': {k: spec[k] for k in ('id', 'version')},
        'ascension': ascension, 'planned': planned, 'scored': len(official),
        'excluded': len(excluded),
        'mean': total / len(official) if official else None,
        'reason': None if official else 'no_scored_rows',
        'scored_loss_boss_witness_missing': witness_missing,
        'lower_bound': {
            'L': sum(lower.values()) / planned if planned else None,
            'U': (total + win * len(excluded)) / planned if planned else None,
            'meaning': 'Diagnostic only: excluded rows as public-progress losses (L) or '
                       'wins (U); not natural endpoints, not the official score.'},
        'excluded_rows': excluded, 'by_seed': official, 'lower_by_seed': lower}


def _paired_reward_delta(before: dict[str, float], after: dict[str, float]) -> dict[str, Any]:
    seeds = sorted(before.keys() & after.keys())
    differences = [after[s] - before[s] for s in seeds]
    interval = None
    if len(differences) >= 2:
        rng = random.Random(20261029)
        samples = sorted(statistics.mean(rng.choices(differences, k=len(differences)))
                         for _ in range(10000))
        interval = [samples[250], samples[9749]]
    return {'paired_n': len(differences),
            'better': sum(d > 0 for d in differences), 'same': sum(d == 0 for d in differences),
            'worse': sum(d < 0 for d in differences),
            'mean_delta': statistics.mean(differences) if differences else None,
            'paired_delta_standard_error': (statistics.stdev(differences) / math.sqrt(
                len(differences)) if len(differences) >= 2 else None),
            'bootstrap95_fixed_seed_sensitivity': interval}


def compare_rewards(before: dict[str, Any], after: dict[str, Any], *,
                    before_facts: dict[str, dict[str, Any]] | None = None,
                    after_facts: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    if before.get('ascension') != after.get('ascension'):
        raise ValueError('different_host_character_or_ascension')
    old, new = reward_summary(before, before_facts), reward_summary(after, after_facts)
    official = _paired_reward_delta(old['by_seed'], new['by_seed'])
    bound = None
    # Each side's [L, U] bounds the mean over its own planned seeds; their difference
    # is like-for-like only when both cohorts planned exactly the same seeds.
    same_seeds = {r['seed'] for r in before['rows']} == {r['seed'] for r in after['rows']}
    if (same_seeds and old['planned'] and new['planned'] and old['lower_bound']['L'] is not None
            and new['lower_bound']['L'] is not None):
        bound = [new['lower_bound']['L'] - old['lower_bound']['U'],
                 new['lower_bound']['U'] - old['lower_bound']['L']]
    return {'official': official, 'reason': new['reason'] or old['reason'],
            'mean_difference_bound': bound,
            'mean_difference_bound_reason': (None if same_seeds
                                             else 'different_planned_seed_set'),
            'lower_bound_paired': _paired_reward_delta(old['lower_by_seed'],
                                                       new['lower_by_seed']),
            'meaning': 'official = paired reward-v2 delta on seeds scored on both sides. '
                       'mean_difference_bound = [L_new-U_old, U_new-L_old]. A difference of '
                       'lower bounds is NOT a lower bound on improvement.'}


def write_report(
    cohorts: dict[str, dict[str, Any]], output: Path, *, through_floor: int | None = None,
    scope_note: str | None = None,
    excluded_facts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    comparisons = {name: compare(cohort, cohorts['current'])
                   for name, cohort in cohorts.items() if name != 'current'}
    rewards = {name: reward_summary(c, excluded_facts) for name, c in cohorts.items()}
    reward_comparisons = {name: compare_rewards(cohort, cohorts['current'],
                                                before_facts=excluded_facts,
                                                after_facts=excluded_facts)
                          for name, cohort in cohorts.items() if name != 'current'}
    output.mkdir(parents=True, exist_ok=False)
    progress = {name: progress_summary(c['rows'], through_floor=through_floor)
                for name, c in cohorts.items()}
    report: dict[str, Any] = {'progress': progress, 'schema': 'stpd/rl-observation-report-1',
              'cohorts': cohorts,
              'comparisons': comparisons,
              'rewards': rewards, 'reward_comparisons': reward_comparisons,
              'non_claims': ['Missing Boss witnesses are not zero clearances.',
                  'A floor threshold is not a Boss encounter.',
                  'No gameplay, storage mutation, training admission or model qualification.']}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    with (output / 'seeds.csv').open('w', newline='', encoding='utf-8') as file:
        columns = ['cohort', 'seed', 'run_id', 'status', 'result', 'scored', 'floor',
                   'observed_floor_lower_bound', 'death_floor',
                   'boss_reached_acts', 'boss_cleared_acts',
                   'last_combat_floor', 'last_combat_act', 'last_combat',
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
    for filename, key in (('reach.csv', 'reach'), ('bosses.csv', 'bosses')):
        records = [{'cohort': name, **row} for name, summary in progress.items()
                   for row in summary[key]]
        if records:
            with (output / filename).open('w', newline='', encoding='utf-8') as file:
                writer = csv.DictWriter(file, fieldnames=list(records[0]))
                writer.writeheader()
                writer.writerows(records)
    svg = distribution_svg(cohorts, through_floor=through_floor, scope_note=scope_note)
    (output / 'distribution.svg').write_text(svg, encoding='utf-8')
    lines = ['# Saved fixed-seed evaluation', '',
             '| Cohort | Planned | Scored floors | Win / loss | Mean | Median | Max |',
             '|---|---:|---:|---|---:|---:|---:|']
    if scope_note:
        lines[2:2] = [scope_note, '']
    for name, c in cohorts.items():
        lines.append(f"| {name} | {c['planned']} | {c['floor_n']} | {c['wins']} / {c['losses']} | "
                     f"{c['mean_floor']} | {c['median_floor']} | {c['max_floor']} |")
    lines += reward_lines(rewards, reward_comparisons)
    lines += ['', '![Loss histogram and observed reach](distribution.svg)', '',
        'bosses.csv counts positive witnesses for all three acts and the scored subset.',
        'reach.csv gives lower/possible upper bounds, retaining every planned slot.',
        'Terminal-floor last combat is context, not a proven cause of death.',
        'Boss metrics retain missing witnesses; seeds.csv retains missing combat IDs.',
        'Observed reach is the count with max floor >= x, not proof of visiting every floor.',
        'Verify exact producer/game/seed/selection protocol before interpreting changes.', '']
    (output / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
    return report


def _number(value: float | None, sign: bool = False) -> str:
    return '—' if value is None else f'{value:+.4f}' if sign else f'{value:.4f}'


def reward_lines(rewards: dict[str, dict[str, Any]],
                 comparisons: dict[str, dict[str, Any]]) -> list[str]:
    lines = ['', '## Mean reward score (reward v2, per difficulty)', '',
             '| Cohort | Ascension | Planned | Scored | Excluded | Official mean | '
             'Lower bound [L, U] (diagnostic) |', '|---|---:|---:|---:|---:|---:|---|']
    for name, r in rewards.items():
        bound = r['lower_bound']
        if r['reason'] == 'reward_spec_missing_ascension':
            lines.append(f"| {name} | {r['ascension']} | {r['planned']} | — | — | "
                         f"not computed: {r['reason']} | — |")
            continue
        lines.append(f"| {name} | {r['ascension']} | {r['planned']} | {r['scored']} | "
                     f"{r['excluded']} | {_number(r['mean'])} | "
                     f"[{_number(bound['L'])}, {_number(bound['U'])}] |")
    if comparisons:
        lines += ['', '| current vs | Pairs | Better / same / worse | Official paired delta | '
                  'Bootstrap 95% | Mean-difference bound |', '|---|---:|---|---:|---|---|']
        for name, c in comparisons.items():
            o, bound = c['official'], c['mean_difference_bound']
            interval = o['bootstrap95_fixed_seed_sensitivity']
            lines.append(
                f"| {name} | {o['paired_n']} | {o['better']} / {o['same']} / {o['worse']} | "
                f"{_number(o['mean_delta'], True)} | "
                + (f"[{_number(interval[0], True)}, {_number(interval[1], True)}]"
                   if interval else '—') + ' | '
                + (f"[{_number(bound[0], True)}, {_number(bound[1], True)}]" if bound else '—')
                + ' |')
    excluded = [(name, row) for name, r in rewards.items() for row in r['excluded_rows']]
    if excluded:
        lines += ['', 'Excluded games (never scored; their pairs are dropped):', '']
        lines += [f"- {name} {row['seed']}: {row['status']}, public floor >= "
                  f"{row['public_floor_lower_bound']} ({row['source']}), "
                  f"Bosses witnessed {row['bosses_witnessed']}" for name, row in excluded]
    missing = [(name, r['scored_loss_boss_witness_missing']) for name, r in rewards.items()
               if r.get('scored_loss_boss_witness_missing')]
    if missing:
        lines += ['', 'Scored losses without a Boss witness (counted as 0 Bosses, so the '
                  'official mean can be low by 0.1 per uncounted Boss): '
                  + ', '.join(f'{name} {count}' for name, count in missing) + '.']
    lines += ['', 'The lower-bound interval is a diagnostic, not a natural endpoint; '
              'a difference of lower bounds is not a lower bound on improvement.']
    return lines


def distribution_svg(
    cohorts: dict[str, dict[str, Any]], *, through_floor: int | None = None,
    scope_note: str | None = None,
) -> str:
    colors = ['#386cb0', '#f28e2b', '#59a14f']
    max_floor = max((r.get('observed_floor_lower_bound', r['floor']) or 0
                     for c in cohorts.values() for r in c['rows']), default=1)
    max_floor = max(max_floor, through_floor or 0, 1)
    max_count = 100
    width, height = 1100, 690
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}">',
             f'<rect width="{width}" height="{height}" fill="white"/>',
             '<g font-family="sans-serif" font-size="14" fill="#222">',
             '<text x="55" y="28" font-size="21">Saved observations: '
             'losses and observed reach</text>']
    for i, (name, c) in enumerate(cohorts.items()):
        color = colors[i % len(colors)]
        parts.append(f'<text x="{55+i*340}" y="57" fill="{color}">'
                     f'{html.escape(name)} / {html.escape(c["selection"])}: '
                     f'n={c["planned"]}, floor n={c["floor_n"]}, '
                     f'death floor={c["death_floor_observed_n"]}</text>')
    titles = ('Natural losses per terminal floor (% of all planned)',
              'Observed max floor >= x (% of all planned; incomplete = lower bound)')
    for panel, title in enumerate(titles):
        top, bottom = 100 + panel * 280, 320 + panel * 280
        parts.append(f'<text x="55" y="{top-12}">{title.replace(">", "&gt;")}</text>')
        for count in range(0, max_count+1, max(1, max_count//5)):
            y = bottom - count / max_count * 210
            parts += [f'<path d="M55 {y} H1070" stroke="#ddd"/>',
                      f'<text x="12" y="{y+5}">{count}%</text>']
        step = 1000 / (max_floor + 1)
        for floor in range(max_floor + 1):
            x = 55 + floor * step
            if floor % 2 == 0 or floor in (8, 17):
                parts.append(f'<text x="{x}" y="{bottom+22}">{floor}</text>')
        for i, (_name, c) in enumerate(cohorts.items()):
            color = colors[i % len(colors)]
            points = []
            upper_points = []
            reach_rows = progress_summary(c['rows'], through_floor=max_floor)['reach']
            for floor in range(max_floor + 1):
                x = 55 + floor * step
                count = (c['loss_floor_counts'].get(floor, 0) if panel == 0 else
                         sum(r.get('observed_floor_lower_bound', r['floor']) is not None and
                             r.get('observed_floor_lower_bound', r['floor']) >= floor
                             for r in c['rows']))
                count = count / c['planned'] * 100 if c['planned'] else 0
                y = bottom - count / max_count * 210
                if panel == 0 and count:
                    bar = step / (len(cohorts)+1)
                    parts.append(f'<rect x="{x+i*bar}" y="{y}" width="{bar}" '
                                 f'height="{bottom-y}" fill="{color}"/>')
                points.append(f'{x},{y}')
                upper = reach_rows[floor]['upper_fraction']
                upper_points.append(f'{x},{bottom - (upper or 0) * 210}')
            if panel == 1:
                polygon = ' '.join(points + list(reversed(upper_points)))
                parts.append(f'<polygon points="{polygon}" fill="{color}" opacity="0.10"/>')
                parts.append(f'<polyline points="{" ".join(points)}" fill="none" '
                             f'stroke="{color}" stroke-width="3"/>')
    note = scope_note or ('Unknown / censored / unstarted remain in JSON and CSV; '
                          'Boss clearance requires explicit witnesses.')
    parts += [f'<text x="55" y="665">{html.escape(note)}</text>', '</g></svg>']
    return ''.join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('current', 'baseline', 'previous'):
        parser.add_argument('--' + name, type=Path, required=name == 'current')
        parser.add_argument('--' + name + '-selection')
        parser.add_argument('--' + name + '-details', type=Path)
        parser.add_argument('--' + name + '-detail-cohort')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--through-floor', type=int, help='Extend observed progress axis, e.g. 49')
    parser.add_argument('--excluded-facts', type=Path,
                        help='Public progress of excluded games for the reward lower bound')
    args = vars(parser.parse_args())
    cohorts = {name: load_cohort(args[name], args[name + '_selection'],
        details=args[name + '_details'], detail_cohort=args[name + '_detail_cohort'])
        for name in ('baseline', 'previous', 'current') if args[name] is not None}
    facts = (json.loads(args['excluded_facts'].read_bytes())['games']
             if args['excluded_facts'] else None)
    write_report(cohorts, args['output'], through_floor=args['through_floor'],
                 excluded_facts=facts)


if __name__ == '__main__':
    main()
