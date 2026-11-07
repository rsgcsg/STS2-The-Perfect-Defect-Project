#!/usr/bin/env python3
"""One bounded Friday Slurm watcher; actors/evaluations retain frozen runtime owners."""
from __future__ import annotations

import argparse
import collections
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import evaluate
from batch import hashed, private_root, read_published, save
from spireagent.json_boundary import json_bytes
from spireagent.rl_exchange import Exchange

scheduler = evaluate.scheduler
SCHEMA = 'spireagent/friday-rl-watch-config-1'


def read_config(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_bytes())
    if (value.get('schema') != SCHEMA or value.get('evaluation_mode') not in {'submit', 'direct'}
            or not 1 <= value.get('wall_seconds', 0) <= 86400
            or not 60 <= value.get('poll_seconds', 900) <= 1800
            or not 14400 <= value.get('evaluation_seconds', 18000) <= 18000
            or type(value.get('started_at')) not in {int, float}
            or not math.isfinite(value['started_at'])):
        raise ValueError('bounded_RL_watch_configuration_required')
    limit = value.get('unknown_stop_fraction', 0.05)
    if (type(limit) not in (int, float) or not math.isfinite(limit) or not 0 < limit <= 1
            or type(value.get('floor_stop_enabled', True)) is not bool):
        raise ValueError('invalid_explicit_health_threshold')
    plan = json.loads(Path(value['actor_plan']).read_bytes())
    if plan['run'] != value['run'] or plan['peer'] != value['peer']:
        raise ValueError('watch_actor_run_peer_binding_mismatch')
    # Pin both the original runtime and actual submitted actor allocation.
    if plan['source_root'] != value['runtime_source']:
        raise ValueError('watch_frozen_runtime_binding_mismatch')
    evaluation_source = value.get('evaluation_runtime_source')
    if evaluation_source is not None:
        source = Path(evaluation_source)
        if (not source.is_absolute() or source.resolve() != source or source.is_symlink()
                or not (source / 'tools/friday/evaluate.py').is_file()):
            raise ValueError('canonical_frozen_evaluation_runtime_required')
    if value.get('natural_quick') is not None:
        natural = value['natural_quick']
        if (value['evaluation_mode'] != 'direct' or not isinstance(natural, dict)
                or not isinstance(natural.get('baseline_cache'), list)
                or not natural['baseline_cache']
                or set(natural.get('baseline_model', {})) !=
                    {'model_artifact_id', 'model_sha256'}
                or not isinstance(natural.get('baseline_unknown_seeds', []), list)):
            raise ValueError('explicit_direct_natural_quick_baseline_required')
        if {tier['name'] for tier in tiers(value)} != {'quick'}:
            raise ValueError('natural_quick_requires_explicit_quick_only_schedule')
    submission = json.loads((Path(value['actor_plan']).parent / 'submission.json').read_bytes())
    if str(submission['job_id']).split(';')[0] != str(value['actor_job_id']):
        raise ValueError('watch_actor_job_binding_mismatch')
    if value.get('evaluation_tiers') is not None:
        tiers(value)
        from spireagent.workbench.reference_suite import load_manifest
        from stpd.evaluation.suites import RL3_LEARNING_CURVE_SUITE_ID

        manifest = Path(value['evaluation_manifest'])
        _, frozen = load_manifest(manifest, hashed(manifest))
        if frozen['suite_id'] != RL3_LEARNING_CURVE_SUITE_ID or len(frozen['seeds']) != 100:
            raise ValueError('RL3_registered100_evaluation_manifest_required')
    if value.get('additional_actor_plans') is not None:
        extras = value['additional_actor_plans']
        if (not isinstance(extras, list) or not extras
                or any(not isinstance(ref, dict) or set(ref) != {'plan', 'job_id'}
                       for ref in extras)):
            raise ValueError('explicit_additional_actor_plan_bindings_required')
        names: set[str] = set()
        jobs: set[str] = set()
        count = 0
        for allocation in actor_allocations(value):
            frozen = (evaluate.read_pinned(allocation['reference'])
                      if allocation.get('reference') else
                      json.loads(Path(allocation['plan']).read_bytes()))
            submitted = json.loads((Path(allocation['plan']).parent / 'submission.json').read_bytes())
            current_names = {ref['actor'] for ref in frozen['actors']}
            if (frozen['run'] != value['run'] or frozen['peer'] != value['peer']
                    or frozen['source_root'] != value['runtime_source']
                    or str(submitted['job_id']).split(';')[0] != allocation['job_id']
                    or allocation['job_id'] in jobs or names & current_names):
                raise ValueError('additional_actor_plan_or_submission_binding_mismatch')
            names.update(current_names)
            jobs.add(allocation['job_id'])
            count += len(frozen['actors'])
        if count > 36:
            raise ValueError('concurrent_RL_actor_limit_36_exceeded')
    return value


def actor_allocations(config: dict[str, Any]) -> list[dict[str, Any]]:
    # Existing plans and submission receipts remain each allocation's owners.
    return [{'plan': config.get('actor_plan'), 'job_id': str(config['actor_job_id'])}] + [
        {'plan': ref['plan']['path'], 'job_id': str(ref['job_id']), 'reference': ref['plan']}
        for ref in config.get('additional_actor_plans', [])]


def tiers(config: dict[str, Any]) -> list[dict[str, Any]]:
    values = config.get('evaluation_tiers', [])
    expected = {'quick': (14400, 20), 'milestone': (28800, 100)}
    if (not isinstance(values, list) or not 1 <= len(values) <= 2
            or {v.get('name') for v in values if isinstance(v, dict)}
                not in ({'quick'}, set(expected))
            or len({v.get('name') for v in values if isinstance(v, dict)}) != len(values)):
        raise ValueError('explicit_quick20_optional_milestone100_tiers_required')
    for value in values:
        if ((value.get('seconds'), value.get('seed_prefix')) != expected[value['name']]
                or type(value.get('seconds')) is not int
                or type(value.get('seed_prefix')) is not int
                or type(value.get('wall_seconds', 7200)) is not int
                or not 1200 <= value.get('wall_seconds', 7200) <= 21600):
            raise ValueError('bounded_RL3_evaluation_tier_required')
    return values


def evaluate_result(path: Path, version: int, updates: int, *,
                    confirmation: bool = False, kind: str | None = None,
                    expected_planned: int | None = None) -> dict[str, Any]:
    result = json.loads(path.read_bytes())
    report = result['ledger_report']
    groups = report['groups']
    floors = [row['max_observed_floor'] for row in report['slots']
              if row['status'] == 'finished' and row.get('max_observed_floor') is not None]
    planned = expected_planned or (40 if confirmation else 20)
    # save_failed is the ledger's state AFTER its one batch-end resume-save retry:
    # one excluded pair like an unknown (RL5 3E). On the formal-audit slot it leaves
    # score_admitted False, so the cohort stays incomplete and unscored (fail closed).
    complete = (report['score_admitted'] is True and report['planned'] == planned
                and report['not_started'] == report['reserved'] == report['save_pending'] == 0
                and report['saved_unscored'] == 0
                and report['trajectory_pending'] == report['censored'] == 0
                and report['win'] + report['loss'] + report['unknown']
                + report['save_failed'] == planned)
    return {'version': version, 'updates': updates, 'wins': report['win'],
            'evaluation_kind': kind or ('confirmation' if confirmation else 'fixed20'),
            'losses': report['loss'], 'unknown': report['unknown'],
            'save_failed': report['save_failed'], 'not_started': report['not_started'],
            'save_failed_alarm': save_failed_alarm(report['slots']),
            'mean_max_floor': sum(floors) / len(floors) if floors else None,
            'evaluated_at': time.time(), 'complete': complete,
            'score_admitted': report['score_admitted'], 'planned': report['planned'],
            'scored': sum(group['scored'] for group in groups),
            'result_path': str(path), 'result_sha256': hashed(path)}


def save_failed_alarm(slots: list[dict[str, Any]]) -> str | None:
    """Typed alarm for saves that still failed after the ledger's one batch-end retry.

    A non-audit game is one excluded pair. The formal-audit slot's failure leaves the
    ledger's score_admitted False, so the whole cohort is closed as unscored (fail
    closed, like an unknown audit slot) rather than one pair being excluded.
    """
    failed = [slot for slot in slots if slot.get('status') == 'save_failed']
    if not failed:
        return None
    if any(slot.get('formal_audit_selected') for slot in failed):
        return 'evaluation_audit_save_failed_cohort_not_scored'
    return 'evaluation_save_failed_after_retry_pair_excluded'


def reward_overview(cohort: dict[str, Any]) -> dict[str, Any]:
    """Compact official reward-v2 mean and [L, U] for watch state; full rows stay in report/."""
    from stpd.evaluation.rl_observation import reward_summary

    value = reward_summary(cohort)
    return {k: v for k, v in value.items()
            if k not in {'by_seed', 'lower_by_seed', 'conservative_by_seed'}}


def protocol_differences(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Observed-protocol keys for which ``rl_observation.compare`` refuses a pair.

    Same rule as compare (shared keys only). A Host/Runtime/model-input pin change, as
    between operating windows, is a different measuring instrument, not a regression.
    """
    old, new = before['protocol_observed'], after['protocol_observed']
    return sorted(k for k in old.keys() & new.keys() if old[k] != new[k])


def protocol_skip(before: dict[str, Any], after: dict[str, Any], reference: str) -> dict[str, Any]:
    keys = protocol_differences(before, after)
    return {'skipped': 'different_observed_protocol', 'differing_keys': keys,
            'reference': reference, 'values': {
                key: {'reference': before['protocol_observed'][key],
                      'current': after['protocol_observed'][key]} for key in keys}}


COMPATIBLE_FIXES_PATH = Path(__file__).with_name('compatible-fixes.json')
COMPATIBLE_FIXES_SCHEMA = 'spireagent/friday-compatible-fixes-1'
COMPATIBLE_FIX_KEYS = frozenset({'id', 'key', 'from', 'to', 'proof', 'approved', 'note'})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def read_compatible_fixes(path: Path = COMPATIBLE_FIXES_PATH) -> list[dict[str, Any]]:
    """Declared compatible Host fixes (card U4 D); each entry pins its fixed-seed proof.

    Only ``host_pin`` changes can be registered. A pair is matched exactly, in either
    direction (the proof shows the same games on both pins); chains are not composed.
    """
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError):
        raise ValueError('compatible_fix_registry_unreadable') from None
    entries = value.get('entries') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get('schema') != COMPATIBLE_FIXES_SCHEMA
            or not isinstance(entries, list)):
        raise ValueError('compatible_fix_registry_invalid')
    ids: set[str] = set()
    pairs: set[frozenset[str]] = set()
    for entry in entries:
        proof = entry.get('proof') if isinstance(entry, dict) else None
        if (not isinstance(entry, dict) or not {'id', 'key', 'from', 'to', 'proof'} <= entry.keys()
                or entry.keys() - COMPATIBLE_FIX_KEYS
                or not isinstance(entry['id'], str) or not entry['id']
                or entry['key'] != 'host_pin'
                or not isinstance(entry['from'], dict) or not isinstance(entry['to'], dict)
                or _canonical(entry['from']) == _canonical(entry['to'])
                or not isinstance(proof, dict)
                or not isinstance(proof.get('artifact'), str) or not proof['artifact']
                or not isinstance(proof.get('sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', proof['sha256'])):
            raise ValueError('compatible_fix_registry_invalid')
        pair = frozenset({_canonical(entry['from']), _canonical(entry['to'])})
        if entry['id'] in ids or pair in pairs:
            raise ValueError('compatible_fix_registry_duplicate')
        ids.add(entry['id'])
        pairs.add(pair)
    return entries


def protocol_blocking(before: dict[str, Any], after: dict[str, Any],
                      fixes: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """(differing keys that refuse a comparison, notes for registered compatible fixes)."""
    blocking, notes = [], []
    for key in protocol_differences(before, after):
        old = _canonical(before['protocol_observed'][key])
        new = _canonical(after['protocol_observed'][key])
        entry = next((e for e in fixes if e['key'] == key
                      and {_canonical(e['from']), _canonical(e['to'])} == {old, new}), None)
        if entry is None:
            blocking.append(key)
            continue
        notes.append({'key': key, 'compatible_fix': entry['id'], 'proof': entry['proof'],
                      'direction': 'forward' if _canonical(entry['from']) == old else 'reverse',
                      'reference': before['protocol_observed'][key],
                      'current': after['protocol_observed'][key]})
    return blocking, notes


# Card U4 C (user 11-07): over a rolling window of global closes, >= 10% unknown is a
# typed attention alarm (lead investigates, training continues), >= 20% stops with all
# originals kept. The denominator is at least 100 closes, so a few early closes never
# trip it. Replaces the per-attempt cumulative 5% gate (unknown_stop_fraction) of
# segment runs; that key now only bounds unresolved admissions.
UNKNOWN_WINDOW_DEFAULTS: dict[str, Any] = {
    'window_seconds': 1800, 'attention_fraction': .10, 'stop_fraction': .20,
    'min_denominator': 100}
EVALUATION_EPISODE_SECONDS = 1800  # evaluate.py's maximum; 900 s cut deep games (U4-1).
EVALUATION_RESULT_GRACE_SECONDS = 900  # terminal job, result not yet visible on NFS
SELECTION_SCORES = ('conservative', 'finished_mean', 'paired_max_floor')
UNKNOWN_STATUSES = frozenset({'unknown', 'save_failed'})


def unknown_window_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get('unknown_window', {})
    if not isinstance(raw, dict) or raw.keys() - UNKNOWN_WINDOW_DEFAULTS.keys():
        raise ValueError('explicit_unknown_window_settings_required')
    value = {**UNKNOWN_WINDOW_DEFAULTS, **raw}
    if (any(type(value[k]) is not int for k in ('window_seconds', 'min_denominator'))
            or any(type(value[k]) not in (int, float) or not math.isfinite(value[k])
                   for k in ('attention_fraction', 'stop_fraction'))
            or not 300 <= value['window_seconds'] <= 7200
            or not 1 <= value['min_denominator'] <= 10000
            or not 0 < value['attention_fraction'] < value['stop_fraction'] <= 1):
        raise ValueError('explicit_unknown_window_settings_required')
    return value


def unknown_window_level(unknown: int, closes: int,
                         settings: dict[str, Any]) -> tuple[float, str | None]:
    fraction = unknown / max(closes, settings['min_denominator'])
    if fraction >= settings['stop_fraction']:
        return fraction, 'stop'
    return fraction, 'attention' if fraction >= settings['attention_fraction'] else None


def job_node(job_id: str) -> str | None:
    """Scheduler NodeList of one job for unknown details; None if unavailable."""
    try:
        raw = subprocess.check_output(['sacct', '-n', '-P', '-X', '-j', str(job_id),
                                       '--format=NodeList'], text=True, timeout=30)
    except Exception:  # noqa: BLE001 -- a diagnostic detail only
        return None
    nodes = sorted({line.strip() for line in raw.splitlines()
                    if line.strip() and line.strip() != 'None assigned'})
    return ','.join(nodes) or None


EVALUATION_PLAYED_STATUSES = frozenset({'finished', 'unknown', 'save_failed', 'censored'})


def evaluation_incomplete(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Typed reasons an evaluation is incomplete; empty means it is used for decisions.

    ``audit_mismatch``: the ledger did not admit scores (formal audit not satisfied) or a
    scored-status row failed typed verification. ``originals_missing``: the job failed,
    a planned game was not played or its original is still pending, or its cleanup was
    not accepted. Scores are reported either way; unknown/save_failed/censored games are
    excluded pairs, never a reason here.
    """
    ledger = result['ledger_report']
    slots = ledger['slots']
    reasons = []
    audit: dict[str, Any] = {}
    if ledger.get('score_admitted') is not True:
        audit['score_admitted'] = ledger.get('score_admitted')
    failed = [row.get('seed') for row in slots
              if row.get('execution_reason') == 'typed_original_verification_failed'
              and row['status'] not in UNKNOWN_STATUSES]
    if failed:
        audit['typed_verification_failed_on_scored_status'] = failed
    if audit:
        reasons.append({'reason': 'audit_mismatch', **audit})
    missing: dict[str, Any] = {}
    if result.get('error_type'):
        missing['job_error'] = {'type': result['error_type'], 'code': result.get('error_code')}
    statuses = collections.Counter(row['status'] for row in slots
                                   if row['status'] not in EVALUATION_PLAYED_STATUSES)
    if statuses:
        missing['statuses'] = dict(statuses)
    unclean = [row.get('seed') for row in slots if row['status'] in EVALUATION_PLAYED_STATUSES
               and row.get('cleanup_accepted') is not True]
    if unclean:
        missing['cleanup_not_accepted'] = unclean
    if missing:
        reasons.append({'reason': 'originals_missing', **missing})
    return reasons


def unknown_over_limit(unknown: int, total: int, config: dict[str, Any]) -> bool:
    """Occasional unknowns never stop a round: the fraction's denominator is at least
    100 games, so the first few completed games cannot trip it with one unknown."""
    return unknown > config.get('unknown_stop_fraction', .05) * max(total, 100)


# Chosen from the RL4.2 c11 replay (python/tests/fixtures/friday_c11_node_unknown_replay.json):
# before the 03:42 global stop healthy nodes peak at 2/30 unknown in any window,
# while the two bad nodes cross 25% about 32 and 15 minutes earlier.
NODE_EXCLUSION_DEFAULTS: dict[str, Any] = {
    'window_attempts': 30, 'min_attempts': 20, 'node_fraction': .25,
    'fleet_median_max': .10, 'max_nodes': 4}


def node_exclusion_settings(config: dict[str, Any]) -> dict[str, Any] | None:
    """Explicit opt-in (``node_unknown_exclusion``); absent keeps the old behaviour."""
    raw = config.get('node_unknown_exclusion')
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - NODE_EXCLUSION_DEFAULTS.keys():
        raise ValueError('explicit_node_unknown_exclusion_settings_required')
    value = {**NODE_EXCLUSION_DEFAULTS, **raw}
    ints = ('window_attempts', 'min_attempts', 'max_nodes')
    fractions = ('node_fraction', 'fleet_median_max')
    if (any(type(value[k]) is not int for k in ints)
            or any(type(value[k]) not in (int, float) or not math.isfinite(value[k])
                   for k in fractions)
            or not 10 <= value['window_attempts'] <= 1000
            or not 1 <= value['min_attempts'] <= value['window_attempts']
            or not 0 < value['node_fraction'] <= 1
            or not 0 <= value['fleet_median_max'] < value['node_fraction']
            or not 0 <= value['max_nodes'] <= 16):
        raise ValueError('explicit_node_unknown_exclusion_settings_required')
    return value


def node_exclusion_decisions(windows: dict[str, list[int]], excluded: Any,
                             settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-node rolling unknown windows -> typed decisions; no scheduler effect here.

    A node qualifies with >= min_attempts closes in its last window_attempts and an
    unknown fraction >= node_fraction. It is excluded only while the median window
    of the other qualifying nodes is <= fleet_median_max (a fleet-wide fault is the
    global stop's to judge) and fewer than max_nodes are already auto-excluded.
    """
    def fraction(window: list[int]) -> float:
        return sum(window) / len(window)

    excluded = set(excluded)
    decisions = []
    for node in sorted(windows):
        window = windows[node]
        if node in excluded or len(window) < settings['min_attempts']:
            continue
        if fraction(window) < settings['node_fraction']:
            continue
        others = sorted(fraction(w) for n, w in windows.items()
                        if n != node and n not in excluded and len(w) >= settings['min_attempts'])
        median = (others[len(others) // 2] if len(others) % 2 else
                  (others[len(others) // 2 - 1] + others[len(others) // 2]) / 2) if others else None
        fact = {'node': node, 'window_attempts': len(window), 'window_unknown': sum(window),
                'window_fraction': fraction(window), 'fleet_median_fraction': median,
                'fleet_nodes': len(others)}
        if median is None or median > settings['fleet_median_max']:
            decisions.append({'kind': 'node_exclusion_withheld_fleet_wide', **fact})
        elif len(excluded) >= settings['max_nodes']:
            decisions.append({'kind': 'node_exclusion_cap_reached', **fact,
                              'auto_excluded': sorted(excluded)})
        else:
            decisions.append({'kind': 'node_unknown_exclusion', **fact})
            excluded.add(node)
    return decisions


def stop_denominator(health: dict[str, Any]) -> int:
    """Actors report closes in batches at segment/actor end, so closed games lag far
    behind offered ones; one actor's batch of cut games must not read as a fleet rate."""
    return max(health.get('completed_for_stop', 0), health.get('offered', 0))


def stop_reason(now: float, config: dict[str, Any], games: list[dict[str, Any]],
                event: dict[str, Any] | None, evaluations: list[dict[str, Any]]) -> str | None:
    if now >= config['started_at'] + config['wall_seconds']:
        return 'first_round_wall_limit'
    limit = config.get('unknown_stop_fraction', 0.05)
    if unknown_over_limit(sum(g['status'] == 'unknown' for g in games), len(games), config):
        return f'training_unknown_fraction_above_{100 * limit:g}_percent'
    if event:
        if event['kind'] == 'failed':
            return 'learner_failed'
        for name in ('loss', 'policy_loss', 'kl', 'gradient_norm'):
            if name in event and not math.isfinite(event[name]):
                return 'learner_nonfinite_' + name
        threshold = config.get('kl_stop_threshold')
        if threshold is not None and event.get('kl', 0) > threshold:
            return 'learner_KL_above_configured_threshold'
    completed = [row for row in evaluations if row['complete']
                 and row.get('evaluation_kind', 'fixed20') == 'fixed20']
    if (config.get('floor_stop_enabled', True) and len(completed) >= 2
            and all(row['mean_max_floor'] is not None
            and row['mean_max_floor'] < 11.75 for row in completed[-2:])):
        return 'two_evaluations_below_baseline_by_more_than_two_floors'
    return None


TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'NODE_FAIL',
            'OUT_OF_MEMORY', 'PREEMPTED', 'BOOT_FAIL', 'DEADLINE', 'REVOKED'}


def job_accounting(job_id: str) -> dict[str, Any]:
    raw = subprocess.check_output(['sacct', '-n', '-P', '-j', job_id,
                                   '--format=JobIDRaw,State,ExitCode,Submit,Eligible,Start,End,ElapsedRaw'], text=True)
    rows = []
    for line in raw.splitlines():
        fields = line.split('|')
        if len(fields) >= 3 and (fields[0] == job_id if '.' in job_id
                                 else '.' not in fields[0]):
            rows.append({'job_id': fields[0], 'state': fields[1].split()[0].rstrip('+'),
                         'exit_code': fields[2], **({'timing': dict(zip(
                             ('submit', 'eligible', 'start', 'end', 'elapsed_seconds'), fields[3:8], strict=True))}
                             if len(fields) >= 8 else {})})
    return {'job_id': job_id, 'rows': rows,
            'terminal': bool(rows) and all(row['state'] in TERMINAL for row in rows),
            'all_completed': bool(rows) and all(row['state'] == 'COMPLETED' for row in rows)}


class Watcher:
    def __init__(self, config: dict[str, Any], work: Path):
        self.config, self.work = config, private_root(work)
        self.exchange = Exchange(config['peer'], config['run'])
        self.state_path = work / 'watch-state.json'
        self.state: dict[str, Any] = (json.loads(self.state_path.read_bytes()) if self.state_path.exists() else
                      {'config_sha256': hashlib.sha256(json_bytes(config)).hexdigest(),
                       'shards': {}, 'evaluations': [], 'pending': None, 'stop': None,
                       'last_evaluation_at': config['started_at']})
        if self.state['config_sha256'] != hashlib.sha256(json_bytes(config)).hexdigest():
            raise ValueError('watch_resume_configuration_changed')
        self.direct: subprocess.Popen | None = None
        self.signalled = False
        self.maintenance_requested = False

    def persist(self) -> None:
        save(self.state_path, self.state)

    def publish(self, kind: str, **fields: Any) -> dict[str, Any]:
        value = {'schema': 'spireagent/rl-watch-status-1', 'run': self.config['run'],
                 'at': time.time(), 'kind': kind, **fields}
        key = f'{self.exchange.prefix}status/{time.time_ns():020d}.json'
        self.exchange.blobs.put_if_absent(key, json_bytes(value))
        return value

    def telemetry(self, kind: str, **fields: Any) -> dict[str, Any] | None:
        try:
            return self.publish(kind, **fields)
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            print(json.dumps({'kind': 'watcher_telemetry_failed', 'event': kind,
                              'error_type': type(error).__name__}), file=sys.stderr, flush=True)
            return None

    def signal_once(self, job_id: str) -> dict[str, Any]:
        """Durable at-most-once signal intent; uncertain scheduler effects need reconciliation."""
        signals = self.state.setdefault('stop_signals', {})
        if job_id in signals:
            return cast(dict[str, Any], signals[job_id])
        signals[job_id] = {'state': 'intent', 'at': time.time()}
        self.persist()
        try:
            command = ['scancel', '--signal=USR1']
            if not re.fullmatch(r'[1-9][0-9]*\.[0-9]+', job_id):
                command.append('--batch')
            result = subprocess.run([*command, job_id],
                                    check=False, capture_output=True)
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            signals[job_id].update(state='outcome_unknown', error_type=type(error).__name__)
        else:
            signals[job_id].update(state='response_received', exit_code=result.returncode)
        self.persist()
        return cast(dict[str, Any], signals[job_id])

    def stop(self, reason: str) -> None:
        if self.state['stop'] is None:
            self.state['stop'] = reason
            self.persist()
        actor_signals = []
        for allocation in actor_allocations(self.config):
            save(Path(allocation['plan']).parent / 'STOP',
                 {'reason': self.state['stop'], 'run': self.config['run']})
            actor_signals.append(self.signal_once(allocation['job_id']))
        actor_signal = actor_signals[0]
        if 'drain_started_at' not in self.state:
            self.state['drain_started_at'] = time.time()
            self.persist()
        try:
            self.exchange.request_stop(self.state['stop'])
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            self.state['learner_stop_pending'] = True
            self.state['learner_stop_error_type'] = type(error).__name__
        else:
            self.state['learner_stop_pending'] = False
        self.persist()
        if self.state['pending']:
            pending = self.state['pending']
            save(Path(pending['plan']).parent / 'STOP', {'reason': self.state['stop']})
            if pending.get('job_id'):
                self.signal_once(str(pending['job_id']))
        self.telemetry('stop_requested', reason=self.state['stop'],
                     actor_signal_exit_code=actor_signal.get('exit_code'),
                     learner_stop_pending=self.state['learner_stop_pending'])

    def prepare_evaluation(self, model: dict[str, Any], now: float, *,
                           confirmation: bool = False, tier: dict[str, Any] | None = None,
                           due_at: float | None = None) -> None:
        if tier is not None and tier['name'] == 'quick' and self.config.get('natural_quick'):
            return self.prepare_natural_quick(model, tier, due_at)
        kind = tier['name'] if tier else ('confirmation' if confirmation else 'fixed20')
        if (tier is not None and self.config['evaluation_mode'] == 'direct'
                and int(os.environ.get('SLURM_CPUS_PER_TASK', '0')) < 4):
            raise ValueError('direct_RL3_evaluation_requires_four_allocated_CPUs')
        root = self.work / 'evaluations' / f"{kind}-v{model['version']:06d}-{time.time_ns()}"
        root.mkdir(parents=True, mode=0o700)
        package = self.exchange.materialize_model(model, self.work / 'models')
        execution = json.loads(Path(self.config['evaluation_execution']).read_bytes())
        if tier is not None:
            execution['pipeline'] = {'pending_limit': 8, 'save_workers': 2}
        execution['models'] = {'latest': {'artifact_id': model['model_artifact_id'],
                                           'package_directory': str(package)}}
        execution_path = root / 'input-execution.json'
        save(execution_path, execution)
        plan_path = root / 'plan' / 'plan.json'
        evaluate.prepare(execution_path, plan_path.parent, selections={'latest': 'latest'},
            workers=4 if tier else (1 if self.config['evaluation_mode'] == 'direct'
                else self.config.get('evaluation_workers', 4)),
            wall_seconds=tier.get('wall_seconds', 7200) if tier else (10800 if confirmation else 7200),
            manifest_path=Path(self.config['evaluation_manifest']),
            seed_prefix=tier['seed_prefix'] if tier else (40 if confirmation else 20),
            runtime_source=Path(self.config.get('evaluation_runtime_source',
                                                self.config['runtime_source'])),
            observation_report=tier is not None)
        pending = {'plan': str(plan_path), 'version': model['version'],
                   'updates': model.get('training', {}).get('updates', 0),
                   'state': 'submission_intent', 'job_id': None, 'evaluation_kind': kind,
                   'model_artifact_id': model['model_artifact_id'],
                   'planned': tier['seed_prefix'] if tier else (40 if confirmation else 20),
                   'due_at': due_at, 'prepared_at': time.time(),
                   'plan_sha256': hashed(plan_path)}
        self.state['pending'] = pending
        if not confirmation and tier is None:
            self.state['last_evaluation_at'] = now
        self.persist()
        if self.config['evaluation_mode'] == 'submit':
            try:
                result = evaluate.submit(plan_path, constraint='fc430')
            except ValueError as error:
                # This explicit pre-sbatch capacity refusal owns no allocation or world offer.
                if str(error) != 'requested_evaluation_workers_exceed_current_fair_share':
                    raise
                self.state['pending'] = None
                if not confirmation and tier is None:
                    self.state['last_evaluation_at'] = (now - self.config.get('evaluation_seconds', 18000)
                                                        + self.config.get('poll_seconds', 900))
                self.persist()
                self.publish('attention', reason='evaluation_capacity_unavailable_no_submission')
                return
            pending.update(state='submitted', job_id=str(result['job_id']).split(';')[0])
        else:
            plan = evaluate.load_plan(plan_path)
            launcher = root / 'launch.sh'
            launcher.write_text(evaluate.launcher(plan_path, plan))
            launcher.chmod(0o700)
            with (root / 'direct.out').open('wb') as stdout, (root / 'direct.err').open('wb') as stderr:
                self.direct = subprocess.Popen(['bash', str(launcher)], stdout=stdout, stderr=stderr)
            pending.update(state='direct_running', pid=self.direct.pid)
        self.persist()
        self.publish('evaluation_started', **pending)

    def prepare_natural_quick(self, model: dict[str, Any], tier: dict[str, Any],
                              due_at: float | None) -> None:
        import quick

        if int(os.environ.get('SLURM_CPUS_PER_TASK', '0')) < 4:
            raise ValueError('direct_RL3_evaluation_requires_four_allocated_CPUs')
        config = self.config['natural_quick']
        models = {'current': {k: model[k] for k in ('model_artifact_id', 'model_sha256')},
                  'baseline': config['baseline_model']}
        execution = json.loads(Path(self.config['evaluation_execution']).read_bytes())
        execution['pipeline'] = {'pending_limit': 8, 'save_workers': 2}
        execution['models'] = {role: {'artifact_id': record['model_artifact_id'],
            'package_directory': str(self.exchange.materialize_model(record, self.work / 'models'))}
            for role, record in models.items()}
        root = self.work / 'evaluations' / f"natural20-v{model['version']:06d}-{time.time_ns()}"
        input_path = self.work / 'natural-quick-execution.json'
        save(input_path, execution)
        quick.prepare(input_path, root, manifest_path=Path(self.config['evaluation_manifest']),
            runtime_source=Path(self.config.get('evaluation_runtime_source',
                                                self.config['runtime_source'])),
            models=models, baseline_cache=self.state.get('natural_quick_baseline_cache',
                                                         config['baseline_cache']),
            baseline_unknown_seeds=config.get('baseline_unknown_seeds', []),
            version=model['version'], updates=model.get('training', {}).get('updates', 0),
            wall_seconds=tier.get('wall_seconds', 7200))
        plan_path = root / 'plan.json'
        pending = {'plan': str(plan_path), 'version': model['version'],
                   'updates': model.get('training', {}).get('updates', 0),
                   'state': 'submission_intent', 'job_id': None,
                   'evaluation_kind': 'quick_natural20', 'planned': 20, 'due_at': due_at,
                   'model_artifact_id': model['model_artifact_id'], 'prepared_at': time.time(),
                   'plan_sha256': hashed(plan_path)}
        self.state['pending'] = pending
        self.persist()
        launcher = root / 'launch.sh'
        launcher.write_text(quick.launcher(plan_path, quick.load_plan(plan_path)))
        with (root / 'direct.out').open('wb') as stdout, (root / 'direct.err').open('wb') as stderr:
            self.direct = subprocess.Popen(['bash', str(launcher)], stdout=stdout, stderr=stderr)
        pending.update(state='direct_running', pid=self.direct.pid)
        self.persist()
        self.publish('evaluation_started', **pending)

    def finish_natural_quick(self, result_path: Path, pending: dict[str, Any]) -> None:
        import quick

        original = json.loads(result_path.read_bytes())
        count = original.get('paired_n')
        if (original.get('schema') != quick.SCHEMA
                or original.get('plan_sha256') != pending['plan_sha256']
                or original.get('model_artifact_id') != pending['model_artifact_id']
                or original.get('version') != pending['version']
                or original.get('updates') != pending['updates']
                or type(count) is not int or not 0 <= count <= 20
                or len(original.get('paired_seeds', [])) != count
                or len(set(original.get('paired_seeds', []))) != count
                or original.get('complete') is not (count == 20)
                or original['comparison']['paired_n'] != count):
            raise ValueError('natural_quick_original_result_binding_mismatch')
        result = {'version': pending['version'], 'updates': pending['updates'],
                  'model_artifact_id': pending['model_artifact_id'],
                  'evaluation_kind': 'quick_natural20', 'complete': original['complete'],
                  'paired_n': count, 'planned': 20, 'mean_max_floor': original['current_mean'],
                  'baseline_paired_mean_floor': original['baseline_mean'],
                  'comparison': original['comparison'], 'due_at': pending['due_at'],
                  'evaluated_at': original['finished_at'], 'result_path': str(result_path),
                  'result_sha256': hashed(result_path), 'report': original['report'],
                  'reason': original['reason'], 'raw_ledger_flags_unchanged': True}
        if not any(e.get('result_sha256') == result['result_sha256']
                   for e in self.state['evaluations']):
            self.state['evaluations'].append(result)
        self.state['natural_quick_baseline_cache'] = original['baseline_cache']
        if original['reason'] == 'non_unknown_or_unsettled_batch_requires_owner_read':
            pending['state'] = 'attention_required'
        else:
            self.state['pending'] = None
        self.persist()
        self.append_reference('quick_natural20', result['result_sha256'], result)
        self.publish('evaluation_finished', evaluation=result)

    def collect_evaluation(self):
        pending = self.state['pending']
        if pending is None:
            return
        if pending.get('state') == 'attention_required':
            return
        result_path = Path(pending['plan']).parent / 'result.json'
        if result_path.exists() and read_published(result_path) is None:
            return
        if read_published(result_path) is not None:
            if pending.get('evaluation_kind') == 'quick_natural20':
                self.finish_natural_quick(result_path, pending)
                return
            result = evaluate_result(result_path, pending['version'], pending['updates'],
                confirmation=pending.get('evaluation_kind') == 'confirmation',
                kind=pending.get('evaluation_kind'), expected_planned=pending.get('planned'))
            if pending.get('plan_sha256') is not None:
                original = json.loads(result_path.read_bytes())
                if original['plan_sha256'] != pending['plan_sha256']:
                    raise ValueError('evaluation_result_plan_binding_mismatch')
            result['model_artifact_id'] = pending.get('model_artifact_id')
            result['due_at'] = pending.get('due_at')
            if pending.get('job_id'):
                result['scheduler_accounting'] = job_accounting(pending['job_id'])
            if self.config.get('evaluation_tiers') is not None:
                result.update(self.evaluation_report(result_path, pending))
            existing = next((r for r in self.state['evaluations']
                             if r['result_sha256'] == result['result_sha256']), None)
            if existing is None:
                self.state['evaluations'].append(result)
            else:
                result = existing
            self.persist()
            if self.config.get('evaluation_tiers') is not None and result['complete']:
                self.candidate_reference(result)
            self.state['pending'] = None
            self.persist()
            self.publish('evaluation_finished', evaluation=result)
            if result['save_failed_alarm']:
                # Typed alarm only: originals stay retained, training continues.
                self.publish('attention', reason=result['save_failed_alarm'],
                             evaluation={k: result[k] for k in (
                                 'version', 'planned', 'save_failed', 'unknown', 'not_started',
                                 'complete', 'result_path')})
            if result['complete']:
                evaluation_file = result_path.parent / 'tensorboard-evaluation.json'
                save(evaluation_file, {k: result[k] for k in ('version', 'updates', 'wins',
                        'mean_max_floor', 'unknown', 'evaluated_at', 'evaluation_kind', 'planned')})
                peer_file = self.work / 'peer.json'
                save(peer_file, self.config['peer'])
                subprocess.run([sys.executable, '-m', 'spireagent.rl_tensorboard',
                    '--peer-config', str(peer_file), '--run', self.config['run'],
                    '--evaluation', str(evaluation_file), '--work', str(self.work / 'tb')], check=True)
        elif pending['state'] == 'submission_intent':
            self.publish('attention', reason='evaluation_submission_outcome_unknown_no_retry', pending=pending)
        elif pending.get('job_id'):
            accounting = job_accounting(pending['job_id'])
            if accounting['terminal']:
                pending['state'] = 'terminal_without_result'
                self.persist()
                self.publish('attention', reason='evaluation_job_terminal_without_result_no_retry',
                             pending=pending, accounting=accounting)
        elif self.direct is not None and self.direct.poll() is not None:
            self.publish('attention', reason='evaluation_process_exited_without_result', pending=pending)

    def schedule_tiers(self, model: dict[str, Any] | None, now: float) -> None:
        schedules = self.state.setdefault('tier_next_due', {})
        due = []
        for tier in tiers(self.config):
            at = schedules.setdefault(tier['name'], self.config['started_at'] + tier['seconds'])
            while at <= now:
                due.append((at, tier))
                at += tier['seconds']
            schedules[tier['name']] = at
        if not due:
            return
        due.sort(key=lambda item: (item[0], item[1]['seed_prefix']), reverse=True)
        selected = due[0]
        records = self.state.setdefault('evaluation_schedule', [])
        intent = None
        for at, tier in reversed(due):
            reason = ('pending_evaluation' if self.state['pending'] is not None else
                      'maintenance' if self.maintenance_requested else
                      'model_unavailable' if model is None else
                      'covered_by_milestone' if at == selected[0] and tier != selected[1] else
                      'missed_scheduled_slot' if (at, tier) != selected else None)
            record = {'due_at': at, 'tier': tier['name'],
                      'version_observed': (model or {}).get('version'),
                      'model_artifact_id': (model or {}).get('model_artifact_id'),
                      'state': 'skipped' if reason else 'intent', 'reason': reason}
            records.append(record)
            if reason is None:
                intent = record
        self.persist()  # consume the wall-clock slot before preparation/submission
        if intent is not None:
            assert model is not None  # An intent is selected only for an available model.
            previous = self.state.get('last_evaluated_version_observed')
            if previous is not None and model['version'] > previous + 1:
                intent['unevaluated_version_range'] = [previous + 1, model['version'] - 1]
            self.state['last_evaluated_version_observed'] = model['version']
            self.persist()
            try:
                self.prepare_evaluation(model, now, tier=selected[1], due_at=selected[0])
            except Exception as error:
                intent.update(state='preparation_or_submission_failed', error_type=type(error).__name__)
                self.persist()
                raise
            intent['state'] = 'pending' if self.state['pending'] else 'skipped_capacity'
            self.persist()

    def evaluation_report(self, result_path: Path, pending: dict[str, Any]) -> dict[str, Any]:
        """One saved ledger -> existing validated trajectory/report implementation."""
        from stpd.evaluation.rl_observation import write_report

        plan, original, files = evaluate.read_observation_report(result_path, Path(pending['plan']))
        metadata = original['observation_report']
        cohort = files['cohort']
        protocol = {'manifest_sha256': plan['manifest']['sha256'],
                    'seed_prefix': plan.get('seed_prefix'),
                    'seeds': [row['seed'] for row in cohort['rows']],
                    'host': cohort['host_kind'], 'character': cohort['character'],
                    'ascension': cohort['ascension'], 'observed': cohort['protocol_observed']}
        key = hashlib.sha256(json_bytes(protocol)).hexdigest()
        cohorts = {'current': cohort}
        comparable = [e for e in self.state['evaluations']
                      if e.get('comparison_key') == key and e.get('cohort') is not None]
        if comparable:
            cohorts['baseline'] = comparable[0]['cohort']
            if len(comparable) > 1:
                cohorts['previous'] = comparable[-1]['cohort']
        output = result_path.parent / 'report'
        if not output.exists():
            write_report(cohorts, output)
        return {'cohort': cohort, 'comparison_key': key, 'report': str(output),
                'reward': reward_overview(cohort),
                'details_sha256': metadata['files']['details']['sha256']}

    def append_reference(self, kind: str, identity: str, fields: dict[str, Any]) -> None:
        reference = {'schema': 'spireagent/rl-retained-reference-1', 'run': self.config['run'],
                     'kind': kind, 'identity': identity, **fields}
        raw = json_bytes(reference)
        digest = hashlib.sha256(raw).hexdigest()
        key = f'{self.exchange.prefix}references/{kind}/{identity}-{digest}.json'
        self.exchange.blobs.put_if_absent(key, raw)
        save(self.work / 'references' / kind / f'{identity}-{digest}.json', reference)

    def checkpoint_binding(self, checkpoint: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
        import torch
        from stpd.p1.export import action_weights, load_model
        from stpd.rl.checkpoint import learner_state
        from stpd.workers.checkpoint_codec import decode_checkpoint

        manifest = self.exchange.store.get_manifest(checkpoint['checkpoint_artifact_id'])
        info = manifest.parameters.value()
        raw = b''.join(self.exchange.store.read_payload(manifest.payload('checkpoint')))
        state = decode_checkpoint(raw)
        learner = learner_state(state)
        updates = model['training']['updates']
        model_manifest = self.exchange.store.get_manifest(model['model_artifact_id'])
        if (manifest.kind != 'checkpoint' or info['run'] != self.config['run']
                or info['version'] != model['version'] or checkpoint['version'] != model['version']
                or info['updates'] != updates or learner['updates'] != updates
                or learner['version'] != model['version'] or learner['run'] != self.config['run']
                or not {'weights', 'optimizer', 'torch_rng', 'schema'} <= state.keys()
                or not {'random', 'buffer', 'counts', 'seen', 'returns'} <= learner.keys()
                or not isinstance(info.get('config'), dict)
                or state.get('schema') != info.get('schema')
                or info.get('producer') != manifest.producer.to_dict()
                or model_manifest.producer != manifest.producer):
            raise ValueError('milestone_full_checkpoint_model_binding_mismatch')
        package = self.exchange.materialize_model(model, self.work / 'models')
        weights, _ = load_model(package / 'model.tensor-tree', model['model_sha256'])
        current = weights.state_dict()
        action_state = action_weights(state['weights'], value_heads=info['config'].get('critic') is not None)
        if (current.keys() != action_state.keys()
                or any(not torch.equal(value, action_state[name])
                       for name, value in current.items())):
            raise ValueError('milestone_checkpoint_model_weights_mismatch')
        return {'checkpoint_artifact_id': manifest.artifact_id,
                'checkpoint_payload_sha256': manifest.payload('checkpoint').sha256,
                'checkpoint_parameters': info, 'checkpoint_producer': manifest.producer.to_dict(),
                'version': model['version'], 'updates': updates,
                'model_artifact_id': model['model_artifact_id'], 'model_sha256': model['model_sha256']}

    def milestone_reference(self, now: float) -> None:
        next_at = self.state.setdefault('next_milestone_at', self.config['started_at'] + 14400)
        if now < next_at:
            return
        intents = self.state.setdefault('milestone_intents', {})
        identity = f'wall-{int(next_at):012d}'
        if identity not in intents:
            checkpoint = self.exchange.latest_checkpoint()
            if checkpoint is None:
                self.state['milestone_pending'] = {'due_at': next_at, 'reason': 'no_complete_checkpoint'}
                self.persist()
                return
            model = self.exchange.model(checkpoint['version'])
            binding = self.checkpoint_binding(checkpoint, model)
            intents[identity] = {'due_at': next_at, 'captured_at': checkpoint['published_at'],
                **binding, 'watch_configuration_sha256': self.state['config_sha256']}
            self.persist()  # freeze the exact pair before an uncertain external write
        self.append_reference('milestone', identity, intents[identity])
        self.state['next_milestone_at'] = next_at + 14400
        self.state.pop('milestone_pending', None)
        self.persist()

    @staticmethod
    def candidate_comparison(before: dict[str, Any], after: dict[str, Any]) -> tuple[bool, str]:
        """Predeclared priority; missing/conflicting witnesses do not win."""
        from stpd.evaluation.rl_observation import compare

        if before['comparison_key'] != after['comparison_key']:
            return False, 'incomparable_protocol'
        a, b = before['cohort'], after['cohort']
        if b['wins'] != a['wins']:
            return b['wins'] > a['wins'], 'wins'
        if not a['boss_count_missing'] and not b['boss_count_missing']:
            for field in ('boss1_cleared_witnessed', 'boss2_reached_witnessed',
                          'boss1_reached_witnessed'):
                if a[field] != b[field]:
                    return b[field] > a[field], field
        floors_a = [r['floor'] for r in a['rows'] if r['scored'] and r['floor'] is not None]
        floors_b = [r['floor'] for r in b['rows'] if r['scored'] and r['floor'] is not None]
        if len(floors_a) != a['planned'] or len(floors_b) != b['planned']:
            return False, 'incomparable_incomplete_scored_floors'
        differences = [sum(f >= t for f in floors_b) - sum(f >= t for f in floors_a)
                       for t in sorted(set(floors_a + floors_b))]
        if differences and all(d >= 0 for d in differences) and any(d > 0 for d in differences):
            return True, 'arrival_distribution_dominance'
        if differences and all(d <= 0 for d in differences) and any(d < 0 for d in differences):
            return False, 'arrival_distribution_decline'
        if not any(differences):
            return False, 'arrival_distribution_equal'
        delta = compare(a, b)
        interval = delta['bootstrap95_fixed_seed_sensitivity']
        return bool(delta['paired_n'] == a['planned'] and interval is not None
                    and interval[0] > 0), 'crossing_CDF_paired95_lower_bound'

    def candidate_reference(self, result: dict[str, Any]) -> None:
        processed = self.state.setdefault('candidate_references', [])
        if result['result_sha256'] in processed:
            return
        candidates = self.state.setdefault('candidate_best', {})
        key = result['comparison_key']
        before = next((r for r in self.state['evaluations']
                       if r['result_sha256'] == candidates.get(key)), None)
        if result['unknown'] or result['cohort']['floor_n'] != result['planned']:
            better, reason = False, 'incomplete_scored_denominator'
        else:
            better, reason = ((True, 'first_complete_comparable') if before is None else
                              self.candidate_comparison(before, result))
        self.append_reference('candidate', result['result_sha256'], {
            'result_sha256': result['result_sha256'], 'comparison_key': key,
            'evaluation_kind': result['evaluation_kind'], 'version': result['version'],
            'updates': result['updates'], 'model_artifact_id': result['model_artifact_id'],
            'rule': 'wins-Boss-CDF-paired-v1', 'selected': better, 'reason': reason,
            'previous_result_sha256': candidates.get(key)})
        if better:
            candidates[key] = result['result_sha256']
        processed.append(result['result_sha256'])
        self.persist()

    def plateau(self) -> bool:
        groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        for result in self.state['evaluations']:
            if (result['complete'] and result.get('comparison_key') is not None
                    and result['unknown'] == 0 and result['cohort']['floor_n'] == result['planned']):
                groups[result['comparison_key']].append(result)
        for results in groups.values():
            if len(results) < 4:
                continue
            best = results[0]
            stagnant = 0
            for result in results[1:]:
                from stpd.evaluation.rl_observation import compare

                delta = compare(best['cohort'], result['cohort'])
                improved = delta['mean_delta'] is not None and delta['mean_delta'] > 0
                stagnant = 0 if improved else stagnant + 1
                if improved:
                    best = result
            if stagnant >= 3:
                return True
        return False

    def _actor_allocation_health(self, allocation: dict[str, Any]) -> dict[str, Any]:
        """Scheduler facts distinguish a healthy learner from lost actor capacity."""
        accounting = job_accounting(allocation['job_id'])
        plan = (evaluate.read_pinned(allocation['reference']) if allocation.get('reference')
                else json.loads(Path(allocation['plan']).read_bytes()))
        expected = len(plan['actors'])
        rows = accounting['rows']
        running_jobs = {row['job_id'] for row in rows if row['state'] == 'RUNNING'}
        failed = [row for row in rows if row['state'] in TERMINAL
                  and (row['state'] != 'COMPLETED' or row['exit_code'] != '0:0')]
        stopped, results, active = [], [], set()
        actor_root = Path(allocation['plan']).parent / 'actors'
        for ref in plan['actors']:
            root = actor_root / ref['actor']
            owner_path, result_path = root / 'owner.json', root / 'result.json'
            owner = read_published(owner_path)
            preserved = result_path.exists()
            if not preserved and owner is not None:
                # rl.run seals the exit result here BEFORE its potentially long snapshot.
                result_path = Path(owner['scratch']) / 'result.json'
            if not result_path.exists():
                if owner is not None and str(owner['job_id']) in running_jobs:
                    active.add(str(owner['job_id']))
                continue
            value = read_published(result_path)
            if value is None:
                if owner is not None and str(owner['job_id']) in running_jobs:
                    active.add(str(owner['job_id']))
                continue
            if value['actor'] != ref['actor'] or value['run'] != self.config['run']:
                raise ValueError('actor_health_result_binding_changed')
            summary = value.get('original_summary') or {}
            results.append({'actor': value['actor'], 'exit_code': value['exit_code'],
                            'status': summary.get('status'),
                            'preservation_complete': preserved})
            if str(summary.get('status', '')).startswith('stopped_ledger:'):
                stopped.append({'actor': value['actor'], 'status': summary['status']})
        health = {**accounting, 'expected': expected, 'running': len(running_jobs),
                  'active_actors': len(active), 'actor_results': results,
                  'failed': failed, 'stopped_ledgers': stopped}
        return health

    def actor_health(self) -> dict[str, Any]:
        parts = [self._actor_allocation_health(a) for a in actor_allocations(self.config)]
        health = parts[0] if len(parts) == 1 else {
            'job_id': str(self.config['actor_job_id']), 'allocations': parts,
            'terminal': all(p['terminal'] for p in parts),
            'all_completed': all(p['all_completed'] for p in parts),
            **{name: sum(p[name] for p in parts)
               for name in ('expected', 'running', 'active_actors')},
            **{name: [row for p in parts for row in p[name]]
               for name in ('rows', 'failed', 'actor_results', 'stopped_ledgers')}}
        failed, results = health['failed'], health['actor_results']
        missing = any(not p['rows'] for p in parts)
        signature = json.dumps({'failed': failed, 'actor_results': results,
                                'missing': missing}, sort_keys=True)
        if (failed or results or missing) and self.state.get('actor_attention') != signature:
            self.publish('attention', reason='actor_capacity_requires_owner_read', actors=health)
            self.state['actor_attention'] = signature
        return health

    def tick(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        for key in self.exchange.shard_keys():
            if key not in self.state['shards']:
                self.state['shards'][key] = self.exchange.shard_record(key)
        games = [game for shard in self.state['shards'].values() for game in shard['games']]
        event = self.exchange.latest_event()
        model = self.exchange.latest_model()
        self.collect_evaluation()
        try:
            actors = self.actor_health()
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure  # noqa: BLE001 -- telemetry failure must not suppress stop rules
            actors = {'observation': 'unavailable', 'error_type': type(error).__name__}
            self.telemetry('attention', reason='actor_accounting_unavailable', **actors)
        reason = ('watcher_signal' if self.signalled else
                  stop_reason(now, self.config, games, event, self.state['evaluations']))
        stopping = self.state['stop'] or reason
        if stopping:
            self.stop(stopping)
        elif self.config.get('evaluation_tiers') is not None:
            self.schedule_tiers(model, now)
            self.milestone_reference(now)
        elif (not self.maintenance_requested and self.state['pending'] is None
              and model is not None):
            winner = next((row for row in self.state['evaluations'] if row['complete']
                           and row.get('evaluation_kind', 'fixed20') == 'fixed20'
                           and row['wins'] > 0), None)
            if winner is not None and not self.state.get('confirmation_requested'):
                # One independent original forty-seed ledger: first twenty repeat, last twenty new.
                # Persist this one-shot intent before preparation/submission; never reoffer uncertainty.
                self.state['confirmation_requested'] = True
                self.persist()
                self.prepare_evaluation(self.exchange.model(winner['version']), now, confirmation=True)
            elif now - self.state['last_evaluation_at'] >= self.config.get('evaluation_seconds', 18000):
                self.prepare_evaluation(model, now)
        hour_games = sum(len(shard['games']) for shard in self.state['shards'].values()
                         if shard['finished_at'] >= now - 3600)
        unknown = sum(g['status'] == 'unknown' for g in games)
        seen = set((event or {}).get('seen_shards', []))
        evaluations = self.state['evaluations']
        complete = [row for row in evaluations if row['complete']
                    and row.get('evaluation_kind', 'fixed20') == 'fixed20']
        no_improvement = (len(complete) >= 3 and max(row['mean_max_floor'] or 0 for row in complete[-3:])
                          <= max([13.75] + [row['mean_max_floor'] or 0 for row in complete[:-3]]))
        if self.config.get('evaluation_tiers') is not None:
            no_improvement = self.plateau()
        event_age = now - event['at'] if event else None
        if event_age is None or event_age > 900:
            self.publish('attention', reason='learner_event_missing_or_older_than_15_minutes',
                         learner_event_age_seconds=event_age)
        counts = (event or {}).get('counts', {})
        stale = counts.get('samples_dropped_stale', 0)
        samples = counts.get('rl_samples', 0)
        status = self.publish('status', version=(model or {}).get('version'), actors=actors,
            learner_event_age_seconds=event_age,
            learner_wait_data_fraction=(event or {}).get('timing', {}).get('fractions', {}).get('wait_for_data'),
            stale_sample_discard_fraction=stale / samples if samples else None,
            games_total=len(games), games_last_hour=hour_games, unknown=unknown,
            unknown_fraction=unknown / len(games) if games else None,
            shard_backlog=len(set(self.state['shards']) - seen), learner=event,
            latest_evaluation=evaluations[-1] if evaluations else None,
            three_evaluations_without_improvement=no_improvement,
            win_confirmation_required=(any(row['wins'] > 0 for row in complete)
                                       and not any(row.get('evaluation_kind') == 'confirmation'
                                                   and row['complete'] for row in evaluations)),
            confirmation_requested=self.state.get('confirmation_requested', False),
            latest_confirmation=next((row for row in reversed(evaluations)
                if row.get('evaluation_kind') == 'confirmation'), None),
            stopped=self.state['stop'], pending_evaluation=self.state['pending'])
        self.persist()
        return status

    def evaluation_drain(self) -> dict[str, Any]:
        pending = self.state['pending']
        if self.direct is not None:
            return {'terminal': self.direct.poll() is not None,
                    'exit_code': self.direct.poll(), 'mode': 'direct',
                    'result_present': bool(pending and
                        (Path(pending['plan']).parent / 'result.json').exists())}
        if pending is None:
            return {'terminal': True, 'mode': self.config['evaluation_mode']}
        if pending.get('job_id'):
            return {'mode': 'submit', **job_accounting(pending['job_id'])}
        return {'terminal': False, 'mode': self.config['evaluation_mode'],
                'reason': 'evaluation_owner_requires_reconciliation'}

    def drain(self) -> bool:
        learner_finished = False
        checkpoint = None
        try:
            event = self.exchange.latest_event()
            model = self.exchange.latest_model()
            checkpoint = self.exchange.latest_checkpoint()
            learner_finished = bool(event and event['kind'] == 'finished'
                and event['at'] >= self.config['started_at']
                and model and checkpoint and checkpoint['version'] == model['version'])
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            self.telemetry('attention', reason='learner_drain_observation_unavailable',
                           error_type=type(error).__name__)
        try:
            accounts = [job_accounting(a['job_id']) for a in actor_allocations(self.config)]
            accounting = accounts[0] if len(accounts) == 1 else {
                'terminal': all(a['terminal'] for a in accounts), 'allocations': accounts}
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            accounting = {'terminal': False, 'error_type': type(error).__name__}
        try:
            evaluation = self.evaluation_drain()
        except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
            evaluation = {'terminal': False, 'error_type': type(error).__name__}
        complete = bool(learner_finished and accounting['terminal'] and evaluation['terminal'])
        expired = time.time() - self.state['drain_started_at'] >= 2400
        self.telemetry('drain_status', learner_finished=learner_finished,
            latest_checkpoint=checkpoint, actors=accounting, evaluation=evaluation,
            drain_complete=complete, drain_timeout=expired,
            learner_stop_pending=self.state.get('learner_stop_pending', True))
        save(self.work / 'drain-status.json', {'at': time.time(),
            'learner_finished': learner_finished, 'latest_checkpoint': checkpoint,
            'actors': accounting, 'evaluation': evaluation, 'drain_complete': complete, 'drain_timeout': expired,
            'learner_stop_pending': self.state.get('learner_stop_pending', True)})
        return complete or expired

    def next_delay(self, now: float, *, tick_started_at: float | None = None) -> float:
        """Next bounded health tick or exact evaluation/deadline boundary.

        An overdue submission that could not obtain capacity waits the ordinary
        health interval instead of spinning. This changes only future deployments.
        """
        interval = float(self.config.get('poll_seconds', 900))
        if self.state['stop']:
            return min(interval, max(0., float(2400 - (now - self.state['drain_started_at']))))
        interval = min(interval, max(0., float(self.config['started_at']
            + self.config['wall_seconds'] - now)))
        if self.config.get('evaluation_tiers') is not None and not self.maintenance_requested:
            schedules = self.state.get('tier_next_due', {})
            due_at = min(schedules.get(t['name'], self.config['started_at'] + t['seconds'])
                         for t in tiers(self.config))
            due_in = float(due_at - now)
            if due_in > 0:
                return min(interval, due_in)
            if tick_started_at is not None and tick_started_at < due_at:
                return 0
            return interval
        if self.state['pending'] is None and not self.maintenance_requested:
            due_at = (self.state['last_evaluation_at']
                      + self.config.get('evaluation_seconds', 18000))
            due_in = float(due_at - now)
            if due_in > 0:
                interval = min(interval, due_in)
            elif tick_started_at is not None and tick_started_at < due_at:
                # The last tick started before the deadline: it did not yet get
                # a due evaluation attempt. Check once now; failed overdue ticks
                # still back off for the ordinary health interval.
                interval = 0
        return interval

    def run(self):
        if sys.platform != 'linux' or not os.environ.get('SLURM_JOB_ID'):
            raise ValueError('Friday_Slurm_watcher_required')
        for number in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT):
            signal.signal(number, lambda *_: setattr(self, 'signalled', True))
        signal.signal(signal.SIGUSR2, lambda *_: setattr(self, 'maintenance_requested', True))
        self.telemetry('watcher_started', config_sha256=self.state['config_sha256'],
                       job_id=os.environ['SLURM_JOB_ID'])
        while True:
            tick_started_at = time.time()
            try:
                self.tick(tick_started_at)
            except Exception as error:  # noqa: BLE001 -- preserve owner intent on external failure
                self.telemetry('attention', reason='watcher_tick_failed',
                    error_type=type(error).__name__, error_code=getattr(error, 'code', None))
                # The actor signal is local/Slurm and precedes all remote learner control.
                if self.state['stop'] or self.signalled or time.time() >= (
                        self.config['started_at'] + self.config['wall_seconds']):
                    self.stop(self.state['stop'] or ('watcher_signal' if self.signalled
                                                    else 'first_round_wall_limit'))
            if self.state['stop'] and self.drain():
                return
            if self.maintenance_requested and not self.state['stop'] and self.state['pending'] is None:
                self.persist()
                self.telemetry('watcher_maintenance_exit', actors_and_learner_stopped=False)
                return
            time.sleep(self.next_delay(time.time(), tick_started_at=tick_started_at))


def read_segment_config(path: Path) -> dict[str, Any]:
    """The existing watcher owns this mode; it consumes the original launch/ledger."""
    value: dict[str, Any] = json.loads(path.read_bytes())
    if (value.get('schema') != SCHEMA or value.get('mode') != 'segments'
            or type(value.get('deadline_at')) not in (int, float)
            or not math.isfinite(value['deadline_at'])
            or type(value.get('training_seconds')) not in (int, float)
            or not 1 <= value['training_seconds'] <= 72000
            or type(value.get('actors')) is not int
            or not 1 <= value['actors'] <= (4096 if value.get('dataplane') is not None else 500)
            or not isinstance(value.get('budget_id'), str)
            or not 1 <= value.get('poll_seconds', 30) <= 300
            or value.get('actor_partition', 'smp') not in {'smp', 'general', 'debug'}
            or not 0 < value.get('unknown_stop_fraction', .05) <= 1):
        raise ValueError('bounded_segment_run_watch_configuration_required')
    if value.get('dataplane') is not None and not isinstance(value['dataplane'], dict):
        raise ValueError('explicit_binary_dataplane_profile_required')
    node_exclusion_settings(value)
    unknown_window_settings(value)
    read_compatible_fixes()
    if value.get('dataplane') is not None:
        learner_cpus = value.get('cpus', 32)
        if value['actors'] + learner_cpus > value.get('cpu_budget', 500):
            raise ValueError('actor_and_learner_exceed_explicit_CPU_budget')
    from stpd.rl.learner import LearnerConfig
    source = Path(value['runtime_source'])
    base = LearnerConfig.read(Path(value['learner_config']))
    if (source.resolve() != source or source.is_symlink() or base.segments is None
            or not base.resume_checkpoint or base.run != value['run'] or base.peer != value['peer']
            or value.get('cpu_only', False) != (base.device == 'cpu')):
        raise ValueError('exact_segment_learner_source_CP_and_compute_binding_required')
    for key in ('learner_config', 'execution', 'learner_python'):
        target = Path(value[key])
        if not target.is_absolute() or not target.is_file():
            raise ValueError('existing_absolute_segment_watch_input_required')
    exchange = Exchange(value['peer'], value['run'])
    acknowledgements = value.get('acknowledged_integrity_events', {})
    if not isinstance(acknowledgements, dict):
        raise TypeError('pinned_diagnosed_integrity_findings_required')
    for key, digest in acknowledgements.items():
        event = exchange.event_record(key)
        if (not key.startswith(exchange.prefix + 'learner-events/')
                or event['kind'] != 'integrity_failed' or event.get('budget_id') != value['budget_id']
                or event['record_sha256'] != digest):
            raise ValueError('pinned_diagnosed_integrity_findings_required')
    if value.get('previous_window_state') is not None:
        previous = evaluate.read_pinned(value['previous_window_state'])
        require_completed_previous_window(
            previous, evaluate.read_pinned(previous['configuration']), value)
    if value.get('evaluation') is not None:
        evaluation = value['evaluation']
        if (not 50 <= evaluation.get('seeds', 50) <= 100
                or evaluation.get('seconds', 7200) != 7200
                or evaluation.get('partition', 'general') not in {'general', 'debug', 'smp'}
                or type(evaluation.get('episode_seconds', EVALUATION_EPISODE_SECONDS)) is not int
                or not 10 <= evaluation.get('episode_seconds', EVALUATION_EPISODE_SECONDS) <= 1800
                or evaluation.get('selection_score', 'conservative') not in SELECTION_SCORES
                or type(evaluation.get('avoid_actor_nodes', False)) is not bool):
            raise ValueError('fixed_two_hour_paired_evaluation_required')
        if (evaluation.get('initial_checkpoint') is not None
                and evaluation['initial_checkpoint'] != base.resume_checkpoint):
            raise ValueError('initial_evaluation_must_bind_continuation_checkpoint')
        if (evaluation.get('rollback_reference', 'previous') not in
                {'previous', 'initial_checkpoint'}
                or evaluation.get('rollback_reference') == 'initial_checkpoint'
                and not evaluation.get('initial_checkpoint')):
            raise ValueError('explicit_initial_checkpoint_rollback_reference_required')
        binding = evaluate.read_pinned(evaluation['baseline_binding'])
        if binding['checkpoint_artifact_id'] != evaluation['baseline_checkpoint']:
            raise ValueError('baseline_checkpoint_model_and_result_binding_required')
        projection = evaluate.fixed_baseline(evaluation['baseline_batches'], binding,
            evaluation['manifest'], seeds=evaluation.get('seeds', 50))
        if not projection['offers_accounted']:
            raise ValueError('frozen_baseline_attempts_must_be_accounted_before_training')
    return value


# Its frozen budget used up or its absolute deadline reached completes a window. Any
# other stop (unknown fraction, watcher failure, signal) keeps its own recovery path.
COMPLETED_WINDOW_STOPS = frozenset({'run_budget_complete', 'absolute_deadline'})


def require_completed_previous_window(previous: dict[str, Any], prior: dict[str, Any],
                                      config: dict[str, Any]) -> None:
    if (previous['active'] is not None or previous.get('pending') is not None
            or previous['stop'] not in COMPLETED_WINDOW_STOPS
            or not all((a.get('terminal_accounting') or {}).get('terminal') is True
                       for a in previous['attempts'])
            or prior['run'] != config['run'] or prior['peer'] != config['peer']
            or prior['budget_id'] == config['budget_id']):
        raise ValueError('completed_distinct_previous_operating_window_required')


def require_watch_continuation(previous: dict[str, Any], prior: dict[str, Any],
                               config: dict[str, Any]) -> None:
    """Same-window continuation (previous_watch_state): the pinned original config and its
    run/peer/budget/deadline/total are unchanged. Also used by release_swap.py."""
    if (previous['config_sha256'] != hashlib.sha256(json_bytes(prior)).hexdigest()
            or any(prior[k] != config[k] for k in (
                'run', 'peer', 'budget_id', 'deadline_at', 'training_seconds'))):
        raise ValueError('previous_watch_run_peer_and_budget_identity_changed')


def require_acknowledged_findings(previous: dict[str, Any], config: dict[str, Any]) -> None:
    """A continuation names every blocking integrity finding it diagnosed (by record sha)."""
    if any(config.get('acknowledged_integrity_events', {}).get(key) != digest
           for key, digest in previous.get('blocking_integrity_findings', {}).items()):
        raise ValueError('diagnosed_integrity_findings_required_for_continuation')


def checkpoint_link(cp: dict[str, Any], config: dict[str, Any], *, has_history: bool) -> bool:
    """The resume CP rule of start_attempt; True when it enters a new operating window.

    The link stays in force until this window publishes its own first CP; a first
    attempt that dies earlier re-enters from the same pinned parent. Also used by
    release_swap.py, so a prepared continuation is checked by this one rule.
    """
    new_window = bool(config.get('previous_window_state')
                      and (cp.get('operating_budget') or {}).get('id') != config['budget_id'])
    if new_window:
        previous = evaluate.read_pinned(config['previous_window_state'])
        prior = evaluate.read_pinned(previous['configuration'])
        require_completed_previous_window(previous, prior, config)
        generations = {g for a in previous['attempts']
                       for g in a.get('generations', [a['generation']])}
        budget = cp.get('operating_budget') or {}
        if (budget.get('id') != prior['budget_id']
                or budget.get('total_seconds') != prior['training_seconds']
                or cp.get('deadline_at') != prior['deadline_at']
                or cp.get('training_generation') not in generations):
            raise ValueError('checkpoint_does_not_belong_to_completed_previous_window')
    if (not new_window and cp.get('deadline_at') is not None
            and cp['deadline_at'] != config['deadline_at']):
        raise ValueError('checkpoint_deadline_cannot_be_reset')
    if cp.get('operating_budget') is not None and not new_window:
        budget = cp['operating_budget']
        if not has_history:
            raise ValueError('original_watch_state_required_to_reconcile_deliveries')
        if (budget['id'] != config['budget_id']
                or budget['total_seconds'] != config['training_seconds']):
            raise ValueError('checkpoint_operating_budget_cannot_be_reset')
    return new_window


class SegmentWatcher(Watcher):
    """RL4.1 adaptation of the original Friday owner, not a gameplay scheduler.

    All offers go through rl.launch/rl.submit/evaluate and their sticky intents.
    Lost allocations replace capacity, never an old World. No secret is persisted.
    """

    def __init__(self, config: dict[str, Any], work: Path):
        self.config, self.work = config, private_root(work)
        self.exchange = Exchange(config['peer'], config['run'])
        self.exchange.run_deadline(config['deadline_at'], budget_id=config['budget_id'])
        self.state_path = self.work / 'watch-state.json'
        resumed = config.get('previous_watch_state')
        previous = evaluate.read_pinned(resumed) if resumed and not self.state_path.exists() else None
        if previous is not None:
            require_watch_continuation(
                previous, evaluate.read_pinned(previous['configuration']), config)
        frozen_config = self.work / 'watch-config.json'
        if not frozen_config.exists():
            save(frozen_config, config)
        if hashed(frozen_config) != hashlib.sha256(json_bytes(config)).hexdigest():
            raise ValueError('watch_resume_configuration_changed')
        config_reference = {'path': str(frozen_config), 'sha256': hashed(frozen_config)}
        self.state: dict[str, Any] = (json.loads(self.state_path.read_bytes()) if self.state_path.exists() else previous or
            {'config_sha256': hashlib.sha256(json_bytes(config)).hexdigest(), 'attempts': [],
             'active': None, 'stop': None, 'training_seconds': 0., 'games_total': 0,
             'unknown_intents': [], 'unresolved_admissions': [], 'last_event_key': '',
             'open_worlds': {}, 'seen_event_keys': [], 'worlds': {}, 'terminal_actors': [],
             'generation_seconds': {}, 'lost_world_offers': [], 'health': {},
             'unresolved_admission_generations': {},
             'blocking_integrity_findings': {},
             'configuration': config_reference,
             'pending': None, 'evaluations': [], 'next_evaluation_seconds': (
                 0 if (config.get('evaluation') or {}).get('initial_checkpoint') else 7200),
             'protected_checkpoints': [], 'incarnation': None})
        if previous is not None:
            require_acknowledged_findings(previous, config)
            save(self.work / 'previous-watch-state.json', previous)
            self.state['previous_config_sha256'] = self.state['config_sha256']
            self.state['config_sha256'] = hashlib.sha256(json_bytes(config)).hexdigest()
            self.state['configuration'] = config_reference
            self.state['stop'] = None  # Explicit continuation; original state remains pinned above.
        if self.state['config_sha256'] != hashlib.sha256(json_bytes(config)).hexdigest():
            raise ValueError('watch_resume_configuration_changed')
        self.signalled = self.maintenance_requested = False
        self.incarnation = f'{os.getpid()}-{time.time_ns()}'
        self.capability_lost = self.state['active'] is not None
        self.state['incarnation'] = self.incarnation
        self.seen_events = set(self.state.get('seen_event_keys', []))
        self.compatible_fixes = read_compatible_fixes()

    def _socket_attachment(self, attempt):
        import rl

        attachment = attempt.get('socket_attachment')
        if attachment is None:
            plans = attempt.get('actor_plans', [])
            if not plans:
                return None
            plan = rl.load_plan(Path(plans[0]))
            attachment = evaluate.read_pinned(plan['inference'])
            if not attachment.get('dataplane'):
                raise ValueError('binary_watch_requires_exact_socket_plan')
            attempt['socket_attachment'] = attachment  # immutable bootstrap, no polling
        return attachment

    def _socket_event_rows(self):
        from spireagent.rl_exchange import EVENT_SCHEMA, _open
        from spireagent.rl_local_wal import LocalControlWAL
        from stpd.p1.local_inference import SegmentConnection

        if not hasattr(self, 'event_channels'):
            self.event_channels: dict[str, SegmentConnection] = {}
        rows, cursors = [], []
        for attempt in self.state['attempts']:
            attachment = self._socket_attachment(attempt)
            if attachment is None or attempt.get('socket_events_closed'):
                continue
            generation = attachment['training_generation']
            channel = self.event_channels.setdefault(generation, SegmentConnection(attachment))
            sequence = attempt.get('event_sequence', 0)
            final_path = Path(attempt['root']) / 'learner/control-wal-final.json'
            def terminal_snapshot(sequence, final_path=final_path,
                                  generation=generation, attempt=attempt):
                final = json.loads(final_path.read_bytes())
                if (final.get('run') != self.config['run']
                        or final.get('training_generation') != generation
                        or not final.get('events')
                        or final['events'][-1]['request']['kind'] not in {'finished', 'failed'}):
                    raise ValueError('terminal_lifecycle_snapshot_binding_changed')
                attempt['terminal_lifecycle_sha256'] = hashed(final_path)
                return {**final, 'events': [e for e in final['events']
                                          if e['sequence'] > sequence]}
            try:
                for _chunk in range(16):
                    final = final_path.exists()
                    if final:
                        result = terminal_snapshot(sequence)
                    else:
                        try:
                            result = channel.request('events_since', sequence=sequence)
                        except (OSError, EOFError, TimeoutError):
                            if not final_path.exists():
                                raise
                            result, final = terminal_snapshot(sequence), True
                    if (result.get('run') != self.config['run']
                            or result.get('training_generation') != generation):
                        raise ValueError('lifecycle_cursor_owner_changed')
                    events = result['events']
                    for fact in events:
                        if fact['sequence'] <= sequence or fact['kind'] != 'event':
                            raise ValueError('lifecycle_cursor_order_changed')
                        body = LocalControlWAL._body(fact['sequence'], fact['key'], fact['kind'],
                            json_bytes(fact['request']), json_bytes(fact['response']),
                            fact['previous'], fact['created'])
                        if hashlib.sha256(body).hexdigest() != fact['hash']:
                            raise ValueError('lifecycle_durable_record_changed')
                        event = _open(json_bytes(fact['request']), EVENT_SCHEMA)
                        if (event['run'] != self.config['run']
                                or event['training_generation'] != generation):
                            raise ValueError('lifecycle_event_owner_changed')
                        key = f"socket:{attachment['pool_id']}:{fact['sequence']:012d}:{fact['hash']}"
                        rows.append((key, event))
                        sequence = fact['sequence']
                    if final:
                        attempt['socket_events_closed'] = True
                        channel.close()
                    if final or len(events) < 256:
                        break
                cursors.append((attempt, sequence))
            except (OSError, EOFError, ValueError, TimeoutError) as error:
                channel.close()
                attempt['socket_events_error'] = {'type': type(error).__name__,
                    'code': getattr(error, 'code', None), 'after_sequence': sequence}
                # A missing lifecycle suffix is not zero Worlds/unknowns. Keep
                # original health facts and stop new offers for diagnosis.
                self.state['stop'] = self.state['stop'] or 'dataplane_events_unavailable'
        return rows, cursors

    def collect_events(self):
        worlds = self.state['worlds']
        unresolved = set(self.state['unresolved_admissions'])
        terminal = set(self.state['terminal_actors'])
        attempts = {g: a for a in self.state['attempts']
                    for g in a.get('generations', [a['generation']])}
        if self.config.get('dataplane') is not None:
            rows, cursors = self._socket_event_rows()
        else:
            keys = sorted(k for k in self.exchange.blobs.keys(self.exchange.prefix + 'learner-events/')
                          if k.endswith('.json') and k not in self.seen_events)
            rows = [(key, self.exchange.event_record(key)) for key in keys]
            cursors = []
        for key, event in rows:
            if key in self.seen_events:
                continue
            self.seen_events.add(key)
            if event.get('budget_id') != self.config['budget_id']:
                continue
            if event['kind'] == 'learner_replacement_started':
                previous = attempts.get(event['previous_generation'])
                if previous is None:
                    raise ValueError('learner_replacement_requires_owned_previous_generation')
                generation = event['training_generation']
                previous.setdefault('generations', [previous['generation']]).append(generation)
                previous.setdefault('replacement_elapsed_seconds', 0.)
                previous['replacement_elapsed_seconds'] += event['charged_seconds']
                previous['active_seconds'] = 0.
                previous.pop('active_measured_at', None)
                previous.pop('window_closed', None)
                attempts[generation] = previous
            attempt = attempts.get(event.get('training_generation'))
            if event['kind'] in {'heartbeat', 'training_window'}:
                generation = event['training_generation']
                self.state['generation_seconds'][generation] = max(
                    self.state['generation_seconds'].get(generation, 0), event['active_seconds'])
            if attempt is not None:
                attempt['last_event'] = event
                updates = event.get('updates', event.get('update'))
                if updates is not None and updates > attempt.get(
                        'last_progress_updates', attempt['checkpoint']['updates']):
                    attempt['last_progress_updates'] = updates
                    attempt['last_progress_at'] = event['at']
                if event['kind'] == 'training_started':
                    attempt.setdefault('last_progress_at', event['at'])
                if (event['kind'] in {'heartbeat', 'training_window'} and
                        event['training_generation'] == attempt.get(
                            'generations', [attempt['generation']])[-1]):
                    attempt['active_seconds'] = max(attempt.get('active_seconds', 0),
                                                     event['active_seconds'])
                    attempt['active_measured_at'] = event['at']
                    if event['kind'] == 'training_window':
                        attempt['window_closed'] = True
                if event['kind'] == 'failed':
                    attempt['failed'] = event
                    self.state['stop'] = self.state['stop'] or 'learner_failed_requires_diagnosis'
                if event['kind'] == 'finished':
                    attempt['finished'] = True
            if event['kind'] == 'actor_retired':
                unresolved.update(event['unresolved_episodes'])
                self.state['unresolved_admission_generations'].update({
                    intent: event['training_generation'] for intent in event['unresolved_episodes']})
                terminal.add(event['actor'])
            if event['kind'] in {'sampling_audit_scheduled', 'sampling_audit'}:
                audits = self.state.setdefault('sampling_audits', {})
                status = event.get('status', 'pending')
                previous = audits.get(event['run_id'])
                if previous and previous['status'] != 'pending' and previous['status'] != status:
                    raise ValueError('immutable_sampling_audit_result_changed')
                audits[event['run_id']] = {'status': status, 'at': event['at']}
            if event['kind'] == 'integrity_failed':
                self.state['blocking_integrity_findings'][key] = event['record_sha256']
                acknowledged = self.config.get('acknowledged_integrity_events', {}).get(key)
                if acknowledged != event['record_sha256']:
                    self.state['stop'] = 'integrity_failed_requires_diagnosis'
            if event['kind'] == 'actor_world_offered':
                intent = event['intent_id']
                fact = worlds.setdefault(intent, {})
                if fact.get('actor') not in {None, event['actor']}:
                    raise ValueError('world_intent_actor_changed')
                fact.update(actor=event['actor'], generation=event['training_generation'], offered=True)
            if event['kind'] == 'actor_games_closed':
                for game in event['games']:
                    intent = game.get('intent_id') or game.get('run_id') or game['seed']
                    fact = worlds.setdefault(intent, {})
                    if fact.get('actor') not in {None, event['actor']}:
                        raise ValueError('world_intent_actor_changed')
                    if fact.get('closed') and fact['status'] != game['status']:
                        raise ValueError('immutable_actor_close_changed')
                    if not fact.get('closed'):
                        at = event.get('at')
                        fact['closed_at'] = (at if type(at) in (int, float) and math.isfinite(at)
                                             else time.time())
                    if not fact.get('closed') and node_exclusion_settings(self.config):
                        # Node attribution waits for the actor's scheduler placement.
                        self.state.setdefault('node_exclusion', {}).setdefault(
                            'pending', []).append([event['actor'], int(
                                game['status'] in {'unknown', 'save_failed'})])
                    fact.update(actor=event['actor'], closed=True, status=game['status'])
                    if game['status'] in {'unknown', 'save_failed'}:
                        # Diagnostic only (U3): classify without the node-local summary.
                        fact.update(reason=game.get('reason'), failure=game.get('failure'))
                        from stpd.evaluation.unknown_category import classify_unknown

                        # Typed at close from the recorded facts (card U4 A); never a retry.
                        fact['unknown_class'] = classify_unknown(
                            game['status'], game.get('reason'), game.get('failure'))
                    if not fact.get('generation') and game.get('training_generation'):
                        fact['generation'] = game['training_generation']
                    if game.get('world_mutation_offered'):
                        fact['offered'] = True
                    # A late original close reconciles a derived lost-offer
                    # observation; the original ledger's unknown is never upgraded.
                    unresolved.discard(intent)
        self.state['seen_event_keys'] = sorted(self.seen_events)
        for attempt, sequence in cursors:
            attempt['event_sequence'] = sequence
        self.state['unresolved_admissions'] = sorted(intent for intent in unresolved
            if not worlds.get(intent, {}).get('offered') and not worlds.get(intent, {}).get('closed'))
        self.state['terminal_actors'] = sorted(terminal)
        self.refresh_world_health()
        total = 0.
        for attempt in self.state['attempts']:
            measured = attempt.get('active_seconds', 0.)
            if not attempt.get('window_closed') and attempt.get('active_measured_at'):
                end = attempt.get('terminal_observed_at', time.time())
                measured += max(0, end - attempt['active_measured_at'])
            measured += attempt.get('replacement_elapsed_seconds', 0.)
            attempt['charged_seconds'] = max(attempt.get('charged_seconds', 0), measured)
            total = max(total, attempt.get('budget_start_seconds', 0) + attempt['charged_seconds'])
        self.state['training_seconds'] = max(self.state['training_seconds'], total,
                                             sum(self.state['generation_seconds'].values()))

    def refresh_world_health(self):
        terminal = set(self.state['terminal_actors'])
        worlds = self.state['worlds']
        offered = {k for k, v in worlds.items() if v.get('offered')}
        unknown = {k for k in offered if worlds[k].get('status') in {'unknown', 'save_failed'}}
        lost = {k for k in offered if not worlds[k].get('closed') and worlds[k]['actor'] in terminal}
        known = set(self.state.get('lost_world_offers', []))
        for intent in lost:
            # Rolling-window time only. Offers already lost in a state saved before this
            # field existed have no observed time and never enter the window on resume.
            worlds[intent].setdefault('lost_at', None if intent in known else time.time())
        self.state['games_total'] = len(offered)
        self.state['unknown_intents'] = sorted(unknown)
        self.state['lost_world_offers'] = sorted(lost)
        self.state['open_worlds'] = {k: v for k, v in worlds.items()
                                     if k in offered and not v.get('closed') and k not in lost}
        index = self.state['active']
        generation = self.state['attempts'][index]['generation'] if index is not None else None
        generations = set(self.state['attempts'][index].get('generations', [generation])) \
            if index is not None else set()
        active = {k for k in offered if worlds[k].get('generation') in generations}
        self.state['health'] = {'generation': generation, 'offered': len(active),
            'completed_for_stop': sum(worlds[k].get('closed') is True
                                      or worlds[k]['actor'] in terminal for k in active),
            'known_unknown': len(active & unknown), 'lost_offers': len(active & lost),
            'unresolved_admissions': sum(self.state['unresolved_admission_generations'].get(k)
                in generations for k in self.state['unresolved_admissions']) if generation else 0}
        self.refresh_audit_health()

    def refresh_audit_health(self):
        audits = list(self.state.get('sampling_audits', {}).values())
        completed = sorted((a for a in audits if a['status'] != 'pending'),
                           key=lambda a: a['at'])
        window = completed[-50:]
        inconclusive = sum(a['status'] == 'audit_inconclusive' for a in window)
        fraction = inconclusive / len(window) if window else 0.
        paused = self.state.get('audit_admission_paused', False)
        if inconclusive >= 5 and fraction > .2 and not paused:
            self.exchange.pause_admission(self.config['budget_id'],
                                           'sampling_audit_inconclusive_window')
            self.state['audit_admission_paused'] = paused = True
            self.telemetry('attention', reason='sampling_audit_inconclusive_admission_paused',
                           audit_window=len(window), inconclusive=inconclusive)
        self.state['health']['sampling_audit'] = {
            'pending': sum(a['status'] == 'pending' for a in audits),
            'completed': len(completed), 'inconclusive': sum(
                a['status'] == 'audit_inconclusive' for a in completed),
            'window_size': 50, 'window_completed': len(window),
            'window_inconclusive': inconclusive, 'window_fraction': fraction,
            'admission_paused': paused}

    def unknown_class(self, intent: str, fact: dict[str, Any]) -> dict[str, Any]:
        """Close-time typed category, plus the node; a #386 auto-excluded node's
        unclassified or environment-gap closes count as infrastructure (kept as close_*)."""
        from stpd.evaluation.unknown_category import classify_unknown

        if fact.get('closed'):
            value = fact.get('unknown_class') or classify_unknown(
                fact.get('status', 'unknown'), fact.get('reason'), fact.get('failure'))
        else:
            value = classify_unknown('unknown', 'actor_terminal_before_close')
        nodes = self.state.get('node_exclusion', {})
        node = nodes.get('actor_nodes', {}).get(fact.get('actor'))
        value = {**value, 'detail': {**value['detail'], 'node': node}}
        if (node in nodes.get('excluded', {})
                and value['category'] in {'environment_gap', 'unclassified'}):
            value = {**value, 'category': 'infrastructure', 'rule': 'node_auto_excluded',
                     'close_category': value['category'], 'close_rule': value['rule']}
        return value

    def unknown_window_gate(self) -> str | None:
        """Rolling global unknown window (card U4 C): 'stop', 'attention' or None.

        Counts original closes and derived lost offers by close time across every
        generation of this watch. Records a typed overview; never retries a game.
        Closes on a #386 auto-excluded node at or after its exclusion time are left out
        of numerator and denominator (user 11-07: they only raise an alarm).
        """
        now = time.time()
        settings = unknown_window_settings(self.config)
        since = now - settings['window_seconds']
        lost = set(self.state.get('lost_world_offers', []))
        placed = self.state.get('node_exclusion', {}).get('actor_nodes', {})
        excluded_at = {node: record.get('at') for node, record in
                       self.state.get('node_exclusion', {}).get('excluded', {}).items()}
        closes = unknown = 0
        window: collections.Counter[str] = collections.Counter()
        totals: collections.Counter[str] = collections.Counter()
        nodes: collections.Counter[str] = collections.Counter()
        isolated: collections.Counter[str] = collections.Counter()  # window unknown
        isolated_closes = isolated_total = 0
        closed_total = 0
        recent = []
        for intent, fact in self.state['worlds'].items():
            if not fact.get('offered'):
                continue  # Same population as the old gate: Worlds actually offered.
            if fact.get('closed'):
                at, bad = fact.get('closed_at'), fact.get('status') in UNKNOWN_STATUSES
            elif intent in lost:
                at, bad = fact.get('lost_at'), True
            else:
                continue
            closed_total += 1
            value = self.unknown_class(intent, fact) if bad else None
            if value is not None:
                totals[value['category']] += 1
            node = placed.get(fact.get('actor'))
            cut = excluded_at.get(node)
            if (type(cut) in (int, float) and type(at) in (int, float) and at >= cut):
                isolated_total += value is not None
                if at > since:
                    isolated_closes += 1
                    if value is not None:
                        isolated[node] += 1
                continue
            if at is None or at <= since:
                continue
            closes += 1
            if value is not None:
                unknown += 1
                window[value['category']] += 1
                nodes[str(value['detail'].get('node'))] += 1
                recent.append((at, intent, value))
        fraction, level = unknown_window_level(unknown, closes, settings)
        infra = totals.get('infrastructure', 0)
        self.state['unknown_overview'] = {
            'closes': closed_total, 'unknown': sum(totals.values()),
            'by_category': dict(totals),
            # Model statistics leave infrastructure out of numerator and denominator.
            'model_unknown_fraction': ((sum(totals.values()) - infra)
                                       / max(closed_total - infra, 1)) if closed_total else None,
            'excluded_node_unknown': isolated_total}
        record = {'at': now, 'window_seconds': settings['window_seconds'], 'closes': closes,
                  'unknown': unknown, 'fraction': fraction, 'level': level,
                  'by_category': dict(window), 'top_nodes': nodes.most_common(5),
                  'excluded_node_closes': isolated_closes,
                  'excluded_node_unknown': dict(isolated),
                  'recent': [{'intent': i, 'category': v['category'], 'rule': v['rule'],
                              'detail': v['detail']}
                             for _, i, v in sorted(recent, key=lambda r: r[0])[-5:]],
                  'settings': settings}
        self.state['unknown_window'] = record
        marks = self.state.setdefault('excluded_node_unknown_alarm_at', {})
        for node, count in sorted(isolated.items()):
            if marks.get(node) is None or now - marks[node] >= settings['window_seconds']:
                marks[node] = now
                self.persist()
                self.telemetry('attention', reason='excluded_node_unknown', node=node,
                               unknown=count, window_seconds=settings['window_seconds'],
                               action='training_continues_node_already_excluded')
        # Attention and stop alarms are rate-limited separately, so a stop is always
        # announced even right after the attention alarm of the same window.
        mark = 'unknown_stop_alarm_at' if level == 'stop' else 'unknown_attention_at'
        last = self.state.get(mark)
        if level is not None and (last is None or now - last >= settings['window_seconds']):
            self.state[mark] = now
            alarms = self.state.setdefault('unknown_alarms', [])
            alarms.append({k: v for k, v in record.items() if k != 'recent'})
            del alarms[:-50]
            self.persist()
            print(json.dumps({'kind': 'unknown_window_' + level, 'fraction': fraction,
                              'unknown': unknown, 'closes': closes,
                              'by_category': dict(window)}), file=sys.stderr, flush=True)
            self.telemetry('attention', reason=(
                'training_unknown_window_at_or_above_stop_fraction' if level == 'stop' else
                'training_unknown_window_at_or_above_attention_fraction_lead_investigates'),
                action='stop_originals_kept' if level == 'stop' else 'training_continues',
                **{k: v for k, v in record.items() if k not in {'at', 'level'}})
        return level

    def cold_checkpoint(self, artifact_id: str) -> dict[str, Any]:
        import tempfile

        from spireagent.storage.config import open_peer
        from spireagent.storage.store import copy_artifact
        from stpd.rl.checkpoint_state import decode_state
        archive = self.config['peer'].get('archive')
        if archive is None:
            raise ValueError('independent_checkpoint_archive_required')
        source = open_peer(archive)
        with tempfile.TemporaryDirectory(prefix='cp-cold-', dir=self.work) as temporary:
            target = open_peer({'kind': 'local', 'directory': temporary})
            copy_artifact(source, target, artifact_id, scratch=Path(temporary))
            manifest = target.get_manifest(artifact_id)
            payload = manifest.payload('checkpoint')
            cp = decode_state(target.bytes(payload, maximum=payload.size))
            if cp.identity.run != self.config['run']:
                raise ValueError('restored_checkpoint_run_changed')
            receipt = {'checkpoint_artifact_id': artifact_id, 'payload_sha256': payload.sha256,
                'payload_bytes': payload.size, 'sequence': cp.identity.sequence,
                'updates': cp.counters.updates, 'training_generation': cp.identity.training_generation,
                'producer': manifest.producer.to_dict(), 'empty_directory_readback': True,
                'operating_budget': cp.config.get('operating_budget'),
                'deadline_at': cp.config.get('deadline_at'),
                'normal_reader': True, 'at': time.time()}
        save(self.work / 'CP-readbacks' / (artifact_id + '.json'), receipt)
        return receipt

    def select_checkpoint(self) -> dict[str, Any]:
        from spireagent.storage.blobs import StoreError
        from spireagent.storage.config import open_peer
        from spireagent.storage.store import copy_artifact
        from stpd.rl.learner import LearnerConfig
        base = LearnerConfig.read(Path(self.config['learner_config']))
        linked = bool(self.config.get('previous_window_state'))
        if linked and not self.state['attempts']:
            assert base.resume_checkpoint is not None
            return self.cold_checkpoint(base.resume_checkpoint)
        records = self.exchange.checkpoint_records()
        archive = self.config['peer'].get('archive')
        if archive is not None:
            # Segment learners publish CP records to their node-local hot store and
            # preserve them asynchronously to the archive; the shared exchange may
            # hold none. Sequences restart per attempt, so newest publication wins.
            records = records + Exchange(archive, self.config['run']).checkpoint_records()
        records = sorted(records, key=lambda r: r['published_at'])
        identities = list(dict.fromkeys(r['checkpoint_artifact_id'] for r in reversed(records)))
        fallback = LearnerConfig.read(Path(self.config['learner_config'])).resume_checkpoint
        if fallback not in identities:
            identities.append(fallback)
        for identity in identities:
            try:
                cp = self.cold_checkpoint(identity)
            except StoreError as error:
                if error.code != 'object_not_found':
                    raise
                self.telemetry('checkpoint_archive_pending', checkpoint_artifact_id=identity)
                # A node can die after local CP publication but before async R2
                # preservation. Finish that same immutable upload before restore.
                copy_artifact(self.exchange.store, open_peer(self.config['peer']['archive']),
                              identity, scratch=self.work)
                cp = self.cold_checkpoint(identity)
            if linked and (cp.get('operating_budget') or {}).get('id') != self.config['budget_id']:
                # This window has no CP of its own budget yet (its attempts died before
                # the first one), so the newest CP is the previous window's: re-enter
                # from the pinned parent through the same validated new-window link.
                assert base.resume_checkpoint is not None
                self.telemetry('window_first_checkpoint_pending',
                               newest_checkpoint_artifact_id=identity,
                               parent_checkpoint_artifact_id=base.resume_checkpoint)
                return (cp if identity == base.resume_checkpoint
                        else self.cold_checkpoint(base.resume_checkpoint))
            return cp
        raise ValueError('no_independently_recoverable_checkpoint')

    def submit_cpu_step(self, script: Path, root: Path, *, name: str, cpus: int,
                        minutes: int, memory_gb: int) -> str:
        """Engineering/CPU allocation only. GPU submission stays in I1's launcher."""
        intent, receipt = root / 'submission-intent.json', root / 'submission.json'
        if intent.exists() or receipt.exists():
            raise ValueError('original_submission_intent_requires_reconciliation_no_retry')
        command = ['sbatch', '--parsable', '--partition=' + self.config.get('cpu_partition', 'debug'),
            '--nodes=1', '--ntasks=1', f'--cpus-per-task={cpus}', f'--mem={memory_gb}G',
            f'--time={minutes}', '--no-requeue', '--signal=B:USR1@300', '--export=ALL',
            '--job-name=' + name, '--output=' + str(root / '%j.out'),
            '--error=' + str(root / '%j.err')]
        if self.config.get('cpu_constraint'):
            command.append('--constraint=' + self.config['cpu_constraint'])
        excluded = self.actor_exclude_nodes()  # also the eval-prepare step (U4 7b)
        if excluded:
            command.append('--exclude=' + ','.join(excluded))
        command.append(str(script))
        with scheduler.user_submission_lock():
            save(intent, {'at': time.time(), 'command': command, 'script_sha256': hashed(script)})
            job = scheduler.output(command)
            save(receipt, {'at': time.time(), 'job_id': job})
        return job.split(';')[0]

    def start_attempt(self):
        import dataclasses
        import inspect

        import rl
        from stpd.rl.learner import LearnerConfig
        if self.state['active'] is not None:
            raise ValueError('previous_learner_and_actor_allocations_must_be_terminal')
        if len(self.state['attempts']) > self.config.get('max_recoveries', 3):
            self.state['stop'] = 'learner_recovery_limit_requires_diagnosis'
            self.persist()
            return
        if not self.config.get('cpu_only') and 'retain_capability' not in inspect.signature(
                rl.launch).parameters:
            raise ValueError('I1_launch_retain_capability_integration_required_before_GPU_offer')
        cp = self.select_checkpoint()
        new_window = checkpoint_link(cp, self.config, has_history=bool(
            self.state['attempts'] or self.config.get('previous_watch_state')))
        if cp.get('operating_budget') is not None and not new_window:
            self.state['training_seconds'] = max(self.state['training_seconds'],
                                                 cp['operating_budget']['consumed_seconds'])
        number = len(self.state['attempts']) + 1
        root = private_root(self.work / f'attempt-{number:04d}')
        generation = f'g-ops-{number:04d}-{time.time_ns()}'
        base = LearnerConfig.read(Path(self.config['learner_config']))
        if base.segments is None:
            raise ValueError('short_segment_learner_configuration_required')
        remaining = min(self.config['training_seconds'] - self.state['training_seconds'],
                        self.config['deadline_at'] - time.time() - 600)
        if remaining <= 0:
            self.state['stop'] = 'run_budget_complete'
            self.persist()
            return
        config = dataclasses.replace(base, resume_checkpoint=cp['checkpoint_artifact_id'],
            resume_source=cp['producer'], start_package=None, start_artifact_id=None,
            start_model_sha256=None, deadline_at=self.config['deadline_at'], wall_seconds=remaining,
            operating_budget_parent=({
                'checkpoint_artifact_id': cp['checkpoint_artifact_id'],
                'operating_budget': cp['operating_budget'], 'deadline_at': cp['deadline_at']}
                if new_window else base.operating_budget_parent),
            operating_budget={'id': self.config['budget_id'],
                'total_seconds': self.config['training_seconds'],
                'consumed_seconds': self.state['training_seconds']},
            segments=dataclasses.replace(base.segments, training_generation=generation,
            rendezvous_path=str(self.work / 'inference.json')))
        if self.config.get('dataplane') is not None:
            from stpd.rl.segment_learner import bind_dataplane_capacity

            config = bind_dataplane_capacity(config, self.config['actors'])
        else:
            config = config.bind_actors(self.config['actors'])
        configuration = root / 'learner.json'
        save(configuration, config.frozen())
        script = root / 'learner.sh'
        script.write_text(rl.learner_launcher(configuration, root / 'learner',
            Path(self.config['execution']), root / 'actors', source=Path(self.config['runtime_source']),
            actors=self.config['actors'], seed_namespace=f'ops-{self.work.name}-{number}',
            python=Path(self.config['learner_python']),
            episode_seconds=self.config.get('episode_seconds', 14400),
            supervisor=self.config.get('learner_supervisor'),
            dataplane=self.config.get('dataplane')))
        script.chmod(0o700)
        attempt = {'root': str(root), 'generation': generation, 'config': str(configuration),
                   'checkpoint': cp, 'actor_plans': [], 'replaced': [], 'active_seconds': 0,
                   'budget_start_seconds': self.state['training_seconds']}
        self.state['attempts'].append(attempt)
        self.state['active'] = number - 1
        self.persist()  # Freeze the attempt before any potentially uncertain sbatch.
        if self.config.get('cpu_only'):
            old_stop = os.environ.get(rl.LAUNCH_STOP_ENV)
            os.environ[rl.LAUNCH_STOP_ENV] = str(root / 'login-launch/STOP')
            try:
                attempt['job_id'] = self.submit_cpu_step(script, root / 'login-launch',
                    name='rl4-learner-cpu', cpus=self.config.get('cpus', 2),
                    minutes=min(60, math.ceil((remaining + 900) / 60)),
                    memory_gb=self.config.get('memory_gb', 8))
            finally:
                if old_stop is None:
                    os.environ.pop(rl.LAUNCH_STOP_ENV, None)
                else:
                    os.environ[rl.LAUNCH_STOP_ENV] = old_stop
        else:
            receipt = rl.launch(script, configuration, root / 'learner', root / 'actors',
                root / 'login-launch', gpu=self.config.get('gpu', 'a100'),
                minutes=self.config.get('minutes', 2880), cpus=self.config.get('cpus', 32),
                memory_gb=self.config.get('memory_gb', 64),
                ready_seconds=self.config.get('ready_seconds', 3600), retain_capability=True,
                held_job_id=self.config.get('held_job_id'),
                actor_node_types=self.config.get('actor_node_types'),
                actor_exclude_nodes=self.actor_exclude_nodes())
            attempt['job_id'] = receipt['GPU_job_id']
            attempt['actor_plans'] = [str(root / 'actors/plan.json')]
        self.capability_lost = False
        self.persist()

    def offer_initial_actors(self, attempt: dict[str, Any]) -> None:
        import rl
        path = Path(attempt['root']) / 'actors/plan.json'
        if attempt['actor_plans'] or not path.exists():
            return
        submitted = path.parent / 'submission.json'
        if submitted.exists():
            # A receipt is not permission to offer again; recover only ownership.
            attempt['actor_plans'] = [str(path)]
        else:
            rl.submit(path, partition=self.config.get('actor_partition', 'smp'),
                constraint=self.config.get('actor_constraint'),
                memory_gb=self.config.get('actor_memory_gb', 4),
                exclude_nodes=self.actor_exclude_nodes())
            attempt['actor_plans'] = [str(path)]
        self.persist()

    def actor_exclude_nodes(self) -> list[str] | None:
        """Configured exclusions plus this run's typed automatic node exclusions.
        Every submission (actors, CPU steps, evaluations) starts from this list."""
        nodes = [*(self.config.get('exclude_nodes') or []),
                 *self.state.get('node_exclusion', {}).get('excluded', {})]
        return list(dict.fromkeys(nodes)) or None

    def evaluation_exclude_nodes(self) -> list[str] | None:
        """Configured and #386 auto-excluded nodes; with ``evaluation.avoid_actor_nodes``
        also the nodes of this watch's running actors (#386 placement facts), so an
        evaluation does not share a node with actors. Opt-in: excluding many nodes can
        delay the job."""
        nodes = list(self.actor_exclude_nodes() or [])
        if (self.config.get('evaluation') or {}).get('avoid_actor_nodes'):
            terminal = set(self.state['terminal_actors'])
            placed = self.state.get('node_exclusion', {}).get('actor_nodes', {})
            nodes += sorted({node for actor, node in placed.items() if actor not in terminal})
        return list(dict.fromkeys(nodes)) or None

    def account_nodes(self, rows: list[dict[str, Any]], settings: dict[str, Any]) -> None:
        """Per-node unknown accounting over original closes; changes only future placement.

        Closed attempts and the global gate are untouched; no attempt is retried.
        An excluded node's running actors get the existing cooperative USR1
        (finish the current game, offer no more); their capacity returns through
        the normal replacement boundary with the node in ``--exclude``.
        """
        import rl

        nodes = self.state.setdefault('node_exclusion', {})
        actor_nodes = nodes.setdefault('actor_nodes', {})
        for row in rows:
            if row.get('node') and rl.NAME.fullmatch(row['node']):
                actor_nodes.setdefault(row['actor'], row['node'])
        windows = nodes.setdefault('windows', {})
        totals = nodes.setdefault('totals', {})
        waiting = []
        for actor, unknown in nodes.get('pending', []):
            node = actor_nodes.get(actor)
            if node is None:
                waiting.append([actor, unknown])
                continue
            windows[node] = (windows.get(node, []) + [unknown])[-settings['window_attempts']:]
            total = totals.setdefault(node, [0, 0])
            total[0] += 1
            total[1] += unknown
        # Closes whose actor never shows a placement stay unattributed, counted once.
        overflow = max(0, len(waiting) - 10000)
        nodes['unattributed'] = nodes.get('unattributed', 0) + overflow
        nodes['pending'] = waiting[overflow:]
        excluded = nodes.setdefault('excluded', {})
        logged = nodes.setdefault('logged', {})
        for decision in node_exclusion_decisions(windows, excluded, settings):
            node = decision['node']
            record = {**decision, 'at': time.time(), 'total_attempts': totals[node][0],
                      'total_unknown': totals[node][1], 'settings': settings,
                      'configured_exclude_nodes': self.config.get('exclude_nodes') or []}
            if decision['kind'] == 'node_unknown_exclusion':
                excluded[node] = {**record, 'signalled_actors': []}
            elif logged.get(node) == decision['kind']:
                continue  # one typed record per node and outcome, not one per tick
            logged[node] = decision['kind']
            nodes.setdefault('events', []).append(record)
            self.persist()
            print(json.dumps({'kind': decision['kind'], 'node': node,
                              'window_unknown': record['window_unknown'],
                              'window_attempts': record['window_attempts']}),
                  file=sys.stderr, flush=True)
            self.telemetry('attention' if decision['kind'] != 'node_unknown_exclusion'
                           else 'node_excluded', reason=decision['kind'], **{
                               k: v for k, v in record.items() if k != 'kind'})
        for node, record in excluded.items():
            for row in rows:
                if (row.get('node') == node and not row['terminal']
                        and row['actor'] not in record['signalled_actors']):
                    self.signal_once(row['array_job_id'])
                    record['signalled_actors'].append(row['actor'])
                    self.persist()

    def replace_actors(self, attempt: dict[str, Any]) -> None:
        import rl

        if self.state.get('audit_admission_paused'):
            return
        settings = node_exclusion_settings(self.config)
        rows = [row for plan in attempt['actor_plans']
                for row in rl.actor_accounting(Path(plan), with_node=settings is not None)]
        if settings is not None:
            self.account_nodes(rows, settings)
        terminal = [row for row in rows if row['terminal']
                    and row['actor'] not in attempt['replaced']]
        if not terminal:
            return
        self.state['terminal_actors'] = sorted(set(self.state['terminal_actors'])
                                               | {row['actor'] for row in terminal})
        self.refresh_world_health()
        health = self.state['health']
        if self.unknown_window_gate() == 'stop':
            self.state['stop'] = 'training_unknown_fraction_above_limit'
            self.state.setdefault('stop_detail', {'at': time.time(), 'site': 'replace_actors',
                                                  'health': dict(health),
                                                  'rule': 'rolling_unknown_window',
                                                  'window': self.state['unknown_window']})
            self.stop_attempt(attempt, self.state['stop'])
            return
        if time.time() + 900 >= self.config['deadline_at']:
            return
        number = len(attempt.get('replacements', [])) + 1
        root = Path(attempt['root']) / f'replacement-{number:04d}'
        config = Path(attempt['root']) / 'learner/learner-config.json'
        rl.prepare(Path(self.config['execution']), config, root, actors=len(terminal),
            wall_seconds=max(300, min(79200, int(self.config['deadline_at'] - time.time() - 600))),
            episode_seconds=self.config.get('episode_seconds', 14400), max_games=100000,
            games_per_ledger=10, runtime_source=Path(self.config['runtime_source']),
            seed_namespace=f'ops-{self.work.name}-{attempt["generation"]}-replacement-{number}',
            segment_inference=self.work / 'inference.json')
        replacement = {'plan': str(root / 'plan.json'), 'replaces': terminal}
        attempt.setdefault('replacements', []).append(replacement)
        attempt['replaced'].extend(row['actor'] for row in terminal)
        self.persist()  # Never synthesize a second plan after an uncertain submit.
        rl.submit(root / 'plan.json', additional_to=[Path(p) for p in attempt['actor_plans']],
            partition=self.config.get('actor_partition', 'smp'),
            constraint=self.config.get('actor_constraint'),
            node_types=self.config.get('actor_node_types'),
            memory_gb=self.config.get('actor_memory_gb', 4),
            exclude_nodes=self.actor_exclude_nodes())
        attempt['actor_plans'].append(str(root / 'plan.json'))
        refs = [{'path': p, 'sha256': hashed(Path(p))} for p in attempt['actor_plans'][1:]]
        save(Path(attempt['root']) / 'actor-allocations.json', {'plans': refs})
        self.persist()

    def stop_attempt(self, attempt: dict[str, Any], reason: str, *,
                     learner_terminal: bool = False) -> None:
        if self.config.get('dataplane') is None:
            for generation in attempt.get('generations', [attempt['generation']]):
                self.exchange.request_stop(reason, training_generation=generation)
        root = Path(attempt['root'])
        # The first Stop reason is the diagnosis; later drain reasons never replace it.
        if not (root / 'login-launch/STOP').exists():
            save(root / 'login-launch/STOP', {'reason': reason})
        if (root / 'learner').exists():
            (root / 'learner/STOP').touch()
        # Numeric steps share the held allocation. Cooperative generation/file
        # Stop leaves their CPU pools and resource tracker alive for drain/CP.
        # A step-wide USR1 also reaches descendants; hard cancellation remains
        # owned by the frozen deadline below.
        if not re.fullmatch(r'[1-9][0-9]*\.[0-9]+', attempt['job_id']):
            self.signal_once(attempt['job_id'])
        for plan in attempt['actor_plans']:
            path = Path(plan)
            save(path.parent / 'STOP', {'reason': reason})
            job = json.loads((path.parent / 'submission.json').read_bytes())['job_id']
            self.signal_once(str(job).split(';')[0])
        attempt.setdefault('first_stopping', reason)
        attempt['stopping'] = reason
        self.persist()
        if (self.config.get('dataplane') is not None and not learner_terminal
                and not attempt.get('socket_events_closed')):
            from stpd.p1.local_inference import SegmentConnection

            attachment = self._socket_attachment(attempt)
            if attachment is not None:
                attempt['stop_epoch'] = attempt.get('stop_epoch', 0) + 1
                self.persist()
                try:
                    with SegmentConnection(attachment) as channel:
                        channel.request('stop', run=self.config['run'],
                            training_generation=attachment['training_generation'],
                            reason=reason, epoch=attempt['stop_epoch'])
                except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError, EOFError) as error:
                    # The learner already closed its socket while draining; the file
                    # Stop and signal above stand, and the next tick sees it terminal.
                    if 'stop_channel_unavailable' not in attempt:
                        attempt['stop_channel_unavailable'] = {
                            'at': time.time(), 'error_type': type(error).__name__,
                            'epoch': attempt['stop_epoch']}
                        self.persist()
                        self.telemetry('attention', reason='stop_channel_unavailable',
                                       error_type=type(error).__name__)

    def cancel_at_deadline(self):
        """The frozen absolute boundary authorizes cancellation of owned allocations."""
        jobs = set()
        if self.state['active'] is not None:
            attempt = self.state['attempts'][self.state['active']]
            if attempt.get('job_id'):
                jobs.add(attempt['job_id'])
            for plan in attempt['actor_plans']:
                receipt = Path(plan).parent / 'submission.json'
                if receipt.exists():
                    jobs.add(str(json.loads(receipt.read_bytes())['job_id']).split(';')[0])
        if self.state['pending'] and self.state['pending'].get('job_id'):
            jobs.add(self.state['pending']['job_id'])
        intents = self.state.setdefault('deadline_cancel_intents', {})
        for job in jobs - intents.keys():
            intents[job] = {'at': time.time(), 'outcome': 'unknown'}
            self.persist()
            result = subprocess.run(['scancel', job], check=False, capture_output=True)
            intents[job].update(outcome='response_received', exit_code=result.returncode)
            self.persist()

    def evaluation_cohort(self, plan_path: Path, result_path: Path
                          ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bool]:
        snapshot = read_published(plan_path.parent / 'snapshot.json')
        if snapshot is None:
            raise FileNotFoundError('evaluation_snapshot_not_published')
        if snapshot.get('full_readback') is not True:
            raise ValueError('evaluation_full_snapshot_readback_required')
        plan, result, files = evaluate.read_observation_report(result_path, plan_path)
        # Card U4 B: unknowns and save failures never void a cohort; each is one excluded
        # pair with its originals kept. Only missing originals or an audit mismatch do.
        return plan, result, files['cohort'], not evaluation_incomplete(result)

    def evaluation_failure_facts(self, slot: dict[str, Any], result: dict[str, Any],
                                 plan_path: Path) -> tuple[dict[str, Any] | None, str]:
        """U3 failure facts of one unknown slot from its retained attempt original."""
        reference = slot.get('report')
        if not isinstance(reference, dict) or not reference.get('path'):
            return None, 'slot_only:no_attempt_report'
        path = Path(reference['path'])
        try:
            if not path.exists() and result.get('scratch_retained'):
                path = (plan_path.parent / 'evidence'
                        / path.relative_to(result['scratch_retained']))
            if hashed(path) != reference.get('sha256'):
                return None, 'slot_only:attempt_report_changed'
            row = json.loads(path.read_bytes())['episodes'][0]
            from spireagent.workbench.rl_segment_producer import failure_facts

            facts = failure_facts(path.parent, row)
            facts['wall_limit_reached'] = row.get('wall_limit_reached') is True
            return facts, 'attempt_original'
        except Exception as error:  # noqa: BLE001 -- a diagnostic detail only
            return None, 'slot_only:' + type(error).__name__

    def evaluation_unknown_details(self, result: dict[str, Any], plan_path: Path,
                                   job_id: str | None) -> list[dict[str, Any]]:
        from stpd.evaluation.unknown_category import classify_unknown

        slots = [s for s in result['ledger_report']['slots'] if s.get('status') in UNKNOWN_STATUSES]
        node = job_node(job_id) if slots and job_id else None
        details = []
        for slot in slots:
            facts, source = self.evaluation_failure_facts(slot, result, plan_path)
            failure = {**(facts or {}), 'execution_reason': (
                (facts or {}).get('execution_reason') or slot.get('execution_reason'))}
            value = classify_unknown(slot['status'], slot.get('reason'), failure)
            if value['detail']['floor'] is None:
                value['detail']['floor'] = slot.get('public_max_observed_floor')
            details.append({'seed': slot.get('seed'), 'run_id': slot.get('run_id'),
                            'status': slot['status'], **value,
                            'detail': {**value['detail'], 'node': node},
                            'facts_source': source})
        return details

    def collect_segment_evaluation(self):
        pending = self.state['pending']
        if pending is None:
            return
        root = Path(pending['root'])
        if (pending['stage'] == 'preparation_abandoned'
                or pending['stage'] == 'failed'
                and pending.get('reason') == 'absolute_deadline_no_World_offer'):
            # Preparation only builds a plan; the watcher never submitted it, so no
            # World was offered. Once its job is terminal the original is recorded as
            # a skipped evaluation and no longer blocks a completed window's link.
            if not job_accounting(pending['job_id'])['terminal']:
                return
            self.state['evaluations'].append({**pending, 'stage': 'preparation_abandoned',
                'at_training_seconds': pending['due_seconds'],
                'skipped': 'absolute_deadline_no_World_offer'})
            self.state['pending'] = None
            self.persist()
            return
        if pending['stage'] == 'preparation':
            if time.time() >= self.config['deadline_at'] - 900:
                self.signal_once(pending['job_id'])
                pending['stage'] = 'preparation_abandoned'
                pending['reason'] = 'absolute_deadline_no_World_offer'
                # Same rule as tick_segment: the deadline never relabels an earlier stop.
                self.state['stop'] = self.state['stop'] or 'absolute_deadline'
                self.persist()
                return
            accounting = job_accounting(pending['job_id'])
            if not accounting['terminal']:
                return
            plan_path = root / 'round/evaluation/plan.json'
            if not plan_path.exists():
                # A completed preparation job may have written the plan on its compute
                # node before it is visible here (c13d 11-08: plan.json written
                # 00:16:30.8, job terminal 00:16:31, read missing). Wait like a result;
                # a failed or cancelled job has no plan to wait for. Own key: the result
                # grace below must start from the evaluation job, not from this one.
                if accounting.get('all_completed'):
                    if 'plan_terminal_seen_at' not in pending:
                        pending['plan_terminal_seen_at'] = time.time()
                        self.persist()
                    grace = (self.config.get('evaluation') or {}).get(
                        'result_grace_seconds', EVALUATION_RESULT_GRACE_SECONDS)
                    if time.time() - pending['plan_terminal_seen_at'] < grace:
                        return
                self.state['stop'] = self.state['stop'] or 'evaluation_CP_preparation_failed'
                pending['stage'] = 'failed'
                self.persist()
                return
            pending['stage'] = 'submission_intent'
            pending['plan'] = str(plan_path)
            self.persist()
            cfg = self.config['evaluation']
            receipt = evaluate.submit(plan_path, partition=cfg.get('partition', 'general'),
                constraint=cfg.get('constraint'), exclude_nodes=self.evaluation_exclude_nodes())
            pending.update(stage='running', job_id=str(receipt['job_id']).split(';')[0])
            self.persist()
            return
        if pending['stage'] in {'failed', 'submission_intent'}:
            return  # Sticky uncertain/failed original; no replacement games.
        if not job_accounting(pending['job_id'])['terminal']:
            return
        path = Path(pending['plan'])
        result_path = path.parent / 'result.json'
        if (read_published(result_path) is None
                or read_published(path.parent / 'snapshot.json') is None):
            # A terminal job may still be publishing, so wait. A job that ended without
            # publishing (e.g. cancelled on a bad node) is one skipped evaluation with its
            # originals kept; it must not stay pending, which blocks exit and later evals.
            if 'terminal_seen_at' not in pending:
                pending['terminal_seen_at'] = time.time()
                self.persist()
            grace = (self.config.get('evaluation') or {}).get(
                'result_grace_seconds', EVALUATION_RESULT_GRACE_SECONDS)
            if time.time() - pending['terminal_seen_at'] < grace:
                return
            self.state['evaluations'].append({**pending, 'stage': 'terminal_without_result',
                'at_training_seconds': pending['due_seconds'],
                'skipped': 'evaluation_job_terminal_without_result'})
            self.state['pending'] = None
            self.telemetry('attention', reason='evaluation_job_terminal_without_result',
                           evaluation=pending['root'], job_id=pending['job_id'])
            self.persist()
            return
        plan, result, cohort, closed = self.evaluation_cohort(path, result_path)
        binding = evaluate.read_pinned(plan['checkpoint_binding'])
        cfg = self.config['evaluation']
        score = cfg.get('selection_score', 'conservative')
        # Card U4 A: every unknown typed from its recorded facts; the cohort projection
        # (never the original) carries the category so the conservative score can leave
        # infrastructure unknowns out and list wall-limited games on their own.
        details = self.evaluation_unknown_details(result, path, pending.get('job_id'))
        categories = {d['seed']: d['category'] for d in details}
        for row in cohort['rows']:
            if row['seed'] in categories:
                row['unknown_category'] = categories[row['seed']]
        incomplete = [] if closed else (evaluation_incomplete(result)
                                        or [{'reason': 'audit_or_originals_incomplete'}])
        report = {'root': pending['root'], 'plan': str(path), 'result': str(result_path),
            'checkpoint': binding, 'closed': closed, 'cohort': cohort,
            'at_training_seconds': pending['due_seconds'], 'job_id': pending['job_id'],
            'unknown': sum(r['status'] == 'unknown' for r in result['ledger_report']['slots']),
            'save_failed': sum(r['status'] == 'save_failed'
                               for r in result['ledger_report']['slots']),
            'unknown_details': details,
            'unknown_by_category': dict(collections.Counter(d['category'] for d in details))}
        alarm = save_failed_alarm(result['ledger_report']['slots'])
        if alarm:
            # Typed alarm only: originals retained, training continues.
            self.telemetry('attention', reason=alarm,
                           evaluation=pending['root'], save_failed=report['save_failed'])
        # Both headline scores on every evaluation (card U4 B): the official mean over
        # finished games, and the conservative score over all planned games.
        report['reward'] = reward_overview(cohort)
        report['scores'] = {
            'finished_mean': report['reward']['mean'], 'finished_n': report['reward']['scored'],
            'conservative': report['reward']['conservative']['score'],
            'conservative_n': report['reward']['conservative']['counted'],
            'planned': report['reward']['planned'], 'unknown_by_category':
                report['unknown_by_category'], 'complete': closed,
            'incomplete': [r['reason'] for r in incomplete], 'selection_score': score}
        if closed:
            from stpd.evaluation.rl_observation import compare, compare_rewards, write_report
            baseline_projection = evaluate.fixed_baseline(cfg['baseline_batches'],
                evaluate.read_pinned(cfg['baseline_binding']), cfg['manifest'],
                seeds=cfg.get('seeds', 50))
            baseline = baseline_projection['cohort']
            if (not baseline_projection['offers_accounted']
                    or cfg['manifest']['sha256'] != plan['manifest']['sha256']):
                raise ValueError('exact_frozen_baseline_protocol_and_denominator_required')
            required = {'host_pin', 'runtime_pin', 'input_spec', 'initial_progress_sha256',
                        'behavior_policy'}
            for group in (baseline, cohort):
                protocol = group['protocol_observed']
                if (not required <= protocol.keys()
                        or protocol['behavior_policy'].get('mode') != 'argmax'):
                    raise ValueError('complete_argmax_evaluation_protocol_required')
            if {r['seed'] for r in baseline['rows']} != {r['seed'] for r in cohort['rows']}:
                raise ValueError('exact_same_frozen_paired_seed_set_required')
            # compare's own refusal, checked here so a protocol skip can never bypass it.
            if any(baseline[k] != cohort[k] for k in ('host_kind', 'character', 'ascension')):
                raise ValueError('different_host_character_or_ascension')
            compared = [row for row in self.state['evaluations'] if not row.get('skipped')]
            # Paired deltas only within one observed protocol (Host/Runtime/input/progress
            # pins), or across a registered compatible host_pin fix (card U4 D, with a
            # note): any other difference is recorded as skipped with its keys.
            skipped: dict[str, list[str]] = {}
            compatible: dict[str, list[dict[str, Any]]] = {}

            def blocking(group: dict[str, Any]) -> list[str]:
                return protocol_blocking(group, cohort, self.compatible_fixes)[0]

            def allowed(group: dict[str, Any]) -> set[str]:
                return {n['key'] for n in protocol_blocking(
                    group, cohort, self.compatible_fixes)[1]}

            def note(label: str, group: dict[str, Any]) -> None:
                notes = protocol_blocking(group, cohort, self.compatible_fixes)[1]
                if notes:
                    compatible[label] = notes

            def score_delta(group: dict[str, Any]) -> dict[str, Any]:
                """The selection/regression score's paired delta (card U4 B)."""
                if score == 'paired_max_floor':
                    value = compare(group, cohort, compatible_keys=allowed(group))
                else:
                    rewards = compare_rewards(group, cohort)
                    value = rewards['conservative_paired' if score == 'conservative'
                                    else 'official']
                return {'score': score, **{k: value.get(k) for k in (
                    'paired_n', 'mean_delta', 'bootstrap95_fixed_seed_sensitivity')}}

            def against(label: str, name: str, group: dict[str, Any]) -> tuple[Any, Any]:
                keys = blocking(group)
                if keys:
                    skipped[label] = keys
                    return protocol_skip(group, cohort, name), protocol_skip(group, cohort, name)
                note(label, group)
                return (compare(group, cohort, compatible_keys=allowed(group)),
                        compare_rewards(group, cohort))

            report['against_baseline'], report['reward_against_baseline'] = against(
                'baseline', 'baseline', baseline)
            latest_name, latest = ((compared[-1]['root'], compared[-1]['cohort'])
                                   if compared else ('baseline', baseline))
            # The most recent closed cohort with this protocol, else the baseline if it
            # matches; with neither, the latest closed cohort is recorded as skipped.
            def scored(row: dict[str, Any]) -> bool:
                # A record with no game counted by the selection score cannot be a
                # regression reference (pre-U4 records have no scores and stay usable).
                counts = row.get('scores')
                return counts is None or bool(counts.get(
                    'conservative_n' if score == 'conservative' else 'finished_n'))

            previous_name, previous = next(
                ((row['root'], row['cohort']) for row in reversed(compared)
                 if not blocking(row['cohort']) and scored(row)),
                ('baseline', baseline) if 'baseline' not in skipped else (latest_name, latest))
            if previous_name != latest_name:
                if blocking(latest):
                    skipped['latest_closed'] = blocking(latest)
                else:  # Same protocol, but it counted no game under the selection score.
                    report['previous_passed_over'] = {'root': latest_name,
                                                      'reason': 'no_counted_games'}
            report['previous_reference'] = previous_name
            report['against_previous'], report['reward_against_previous'] = against(
                'previous', previous_name, previous)
            best = self.state.get('best_checkpoint')
            best_cohort = self.state.get('best_cohort', baseline)
            best_keys = blocking(best_cohort)
            if best_keys:
                skipped['best'] = best_keys
            else:
                note('best', best_cohort)
            reference = cfg.get('rollback_reference', 'previous')
            anchor = (next((row for row in compared if row['at_training_seconds'] == 0), None)
                      if reference == 'initial_checkpoint' else None)
            if reference == 'initial_checkpoint':
                # Refusals before the report is written, so a reconciled re-run is clean.
                if anchor is None and pending['due_seconds'] != 0:
                    raise ValueError('initial_checkpoint_evaluation_missing')
                if (anchor is not None and anchor['checkpoint']['checkpoint_artifact_id']
                        != cfg['initial_checkpoint']):
                    raise ValueError('initial_checkpoint_evaluation_binding_changed')
            anchor_keys = blocking(anchor['cohort']) if anchor else []
            if anchor_keys:
                skipped['recipe_switch'] = anchor_keys
            elif anchor:
                note('recipe_switch', anchor['cohort'])
            # Report from pure inputs before any state change: if it fails, the watch state
            # (best, superseded_best, stop) is untouched and pending stays the original.
            report['report_directory'] = str(root / 'comparison')
            cohorts = {name: group for name, group in (
                ('baseline', None if 'baseline' in skipped else baseline),
                ('previous', None if 'previous' in skipped else previous),
                ('current', cohort)) if group is not None}
            write_report(cohorts, root / 'comparison',
                         scope_note='Managed fixed pool; unknown pairs excluded, '
                         'no retries. Selection uses raw CP theta, never training returns.'
                         + (' Protocol-skipped references: ' + json.dumps(skipped, sort_keys=True)
                            if skipped else '')
                         + (' Compatible fixes: ' + json.dumps(
                             {k: [n['compatible_fix'] for n in v] for k, v in compatible.items()},
                             sort_keys=True) if compatible else ''),
                         compatible_keys={name: allowed(group) for name, group in cohorts.items()
                                          if name != 'current'})
            first_of_protocol = False
            if best_keys:
                # The best is chosen only within one protocol. The current best is kept in
                # superseded_best (never compared across protocols); a returning protocol
                # resumes its own stored best, else this cohort is the new protocol's first.
                superseded = self.state.setdefault('superseded_best', [])
                restored = next((row for row in reversed(superseded)
                                 if row.get('cohort') is not None
                                 and not blocking(row['cohort'])), None)
                superseded.append({
                    'best_checkpoint': best, 'cohort': best_cohort,
                    'best_selection_score': self.state.get('best_selection_score'),
                    'best_cohort_source': ('frozen_baseline' if best_cohort == baseline
                                           else 'evaluation'),
                    'differing_keys': best_keys, 'superseded_by_evaluation': pending['root']})
                if restored is None:
                    first_of_protocol = True
                    self.state['best_checkpoint'] = binding['checkpoint_artifact_id']
                    self.state['best_cohort'] = cohort
                    self.state['best_selection_score'] = score
                    report['best_selection'] = {
                        'reason': 'first_cohort_of_new_observed_protocol',
                        'differing_keys': best_keys}
                else:
                    best, best_cohort = restored['best_checkpoint'], restored['cohort']
                    self.state['best_checkpoint'], self.state['best_cohort'] = best, best_cohort
                    self.state['best_selection_score'] = restored.get('best_selection_score')
                    report['best_selection'] = {
                        'reason': 'restored_best_of_returning_observed_protocol',
                        'restored_checkpoint': best, 'differing_keys': best_keys}
            # Best-CP selection and the regression check name their score (default:
            # conservative, which is less biased against deep runs than the finished mean).
            comparison = None if first_of_protocol else score_delta(best_cohort)
            report['best_comparison'] = comparison
            if comparison is None:
                pass  # This cohort already is its new protocol's best reference.
            elif comparison['mean_delta'] is not None and comparison['mean_delta'] > 0:
                self.state['best_checkpoint'] = binding['checkpoint_artifact_id']
                self.state['best_cohort'] = cohort
                self.state['best_selection_score'] = score
            elif best is None:
                resume = cfg.get('baseline_resume_checkpoint')
                if resume is None:
                    from stpd.rl.learner import LearnerConfig
                    resume = LearnerConfig.read(
                        Path(self.config['learner_config'])).resume_checkpoint
                self.state['best_checkpoint'] = resume
                self.state['best_cohort'] = baseline
                self.state['best_selection_score'] = score
            regression_comparison: dict[str, Any] | None = (
                report['against_previous'] if 'previous' in skipped else score_delta(previous))
            if reference == 'initial_checkpoint':
                if anchor is None:
                    regression_comparison = None  # The anchor is not a post-switch evaluation.
                    report['against_recipe_switch'] = None
                elif anchor_keys:
                    regression_comparison = protocol_skip(anchor['cohort'], cohort,
                                                          anchor['root'])
                    report['against_recipe_switch'] = regression_comparison
                else:
                    report['against_recipe_switch'] = compare(
                        anchor['cohort'], cohort, compatible_keys=allowed(anchor['cohort']))
                    regression_comparison = score_delta(anchor['cohort'])
            report['regression_delta'] = regression_comparison
            interval = (regression_comparison.get('bootstrap95_fixed_seed_sensitivity')
                        if regression_comparison is not None else None)
            regression: bool | None = bool(interval and interval[1] < 0)
            if regression_comparison is not None and regression_comparison.get('skipped'):
                # Not compared is not "no regression": None, with the reason, and it never
                # counts towards the two-regressions stop.
                regression = None
                report['paired_regression_skipped'] = regression_comparison['skipped']
            elif regression_comparison is not None and interval is None:
                # Too few paired games for an interval: not measured, never "no regression".
                regression = None
                report['paired_regression_skipped'] = 'no_paired_interval'
            prior = self.state['evaluations'][-1] if self.state['evaluations'] else {}
            report['paired_regression'] = regression
            report['regression_reference'] = reference
            report['regression_score'] = score
            # Records before card U4 judged regressions by the paired max-floor delta.
            if (regression and prior.get('paired_regression')
                    and prior.get('regression_reference', 'previous') == reference
                    and prior.get('regression_score', 'paired_max_floor') == score):
                self.state['stop'] = 'two_paired_regressions_restore_best_after_diagnosis'
                save(self.work / 'rollback-checkpoint.json', {
                    'checkpoint_artifact_id': self.state['best_checkpoint'],
                    'selection': 'highest_fixed_pool_paired_point_estimate',
                    'selection_score': score,
                    'regression_reference': reference,
                    'recipe_after_diagnosis': cfg.get('rollback_recipe'),
                    'automatic_training_resume': False, 'reason': self.state['stop']})
            if compatible:
                report['compatible_fixes'] = compatible
            if skipped:
                # Typed alarm only: the cohort and its originals are kept, training continues.
                report['protocol_skipped'] = skipped
                self.telemetry('attention', reason='different_observed_protocol',
                               evaluation=pending['root'], protocol_skipped=skipped)
        else:
            # Incomplete (originals missing or an audit mismatch): scores are reported with
            # the label, the record is not a comparison reference, training continues.
            report['incomplete'] = incomplete
            report['skipped'] = 'evaluation_audit_or_originals_incomplete'
            self.telemetry('attention', reason=report['skipped'], evaluation=pending['root'],
                           incomplete=incomplete)
        # Full original result is retained by evaluate. This record never upgrades it.
        self.state['evaluations'].append(report)
        self.state['pending'] = None
        self.persist()
        print(json.dumps({'kind': 'evaluation_scores', 'evaluation': pending['root'],
                          **report['scores']}), file=sys.stderr, flush=True)
        self.telemetry('evaluation_scores', evaluation=pending['root'], **report['scores'])

    def schedule_segment_evaluation(self):
        import shlex
        cfg = self.config.get('evaluation')
        due = self.state['next_evaluation_seconds']
        if (cfg is None or self.state['pending'] is not None
                or self.state['training_seconds'] < due
                or time.time() >= self.config['deadline_at'] - 900
                or self.state['stop'] not in {None, 'run_budget_complete'}):
            return
        required_window = cfg.get('episode_seconds', EVALUATION_EPISODE_SECONDS) + 180 + 600
        if self.config['deadline_at'] - time.time() <= required_window:
            self.telemetry('evaluation_not_started', reason='absolute_deadline_headroom',
                           due_training_seconds=due)
            return
        cp = (self.cold_checkpoint(cfg['initial_checkpoint'])
              if due == 0 and cfg.get('initial_checkpoint') else self.select_checkpoint())
        if cp['checkpoint_artifact_id'] not in self.state['protected_checkpoints']:
            self.state['protected_checkpoints'].append(cp['checkpoint_artifact_id'])
        number = len(self.state['evaluations']) + 1
        root = private_root(self.work / f'evaluation-{number:04d}')
        script = root / 'prepare.sh'
        prefix = evaluate.launcher(root / 'not-offered-plan.json',
            {'source_root': self.config['runtime_source']}).split(
                'exec "$actor_root/env/bin/python"', 1)[0]
        command = ['"$actor_root/env/bin/python"', '"$source_root/tools/friday/evaluate.py"',
            'prepare-checkpoint', '--learner-config', shlex.quote(self.config['learner_config']),
            '--checkpoint', cp['checkpoint_artifact_id'], '--execution',
            shlex.quote(cfg['execution']), '--manifest', shlex.quote(cfg['manifest']['path']),
            '--output', shlex.quote(str(root / 'round')), '--workers', str(cfg.get('workers', 4)),
            '--seeds', str(cfg.get('seeds', 50)), '--wall-seconds', str(cfg.get('wall_seconds', 7200)),
            '--episode-seconds', str(cfg.get('episode_seconds', EVALUATION_EPISODE_SECONDS))]
        command.extend(['--deadline-at', str(self.config['deadline_at'])])
        script.write_text(prefix + 'exec ' + ' '.join(command) + '\n')
        script.chmod(0o700)
        pending = {'stage': 'preparation_intent', 'root': str(root),
                   'checkpoint': cp, 'due_seconds': due}
        self.state['pending'] = pending
        self.state['next_evaluation_seconds'] += 7200
        self.persist()
        job = self.submit_cpu_step(script, root / 'preparation', name='rl4-eval-prepare',
                                  cpus=1, minutes=10, memory_gb=6)
        pending.update(stage='preparation', job_id=job)
        self.persist()

    def retain_segment_checkpoints(self):
        records = self.exchange.checkpoint_records()
        current = records[-1]['checkpoint_artifact_id'] if records else None
        if current is None or current == self.state.get('last_CP_retention'):
            return
        protected = set(self.state['protected_checkpoints'])
        if self.state.get('best_checkpoint'):
            protected.add(self.state['best_checkpoint'])
        result = self.exchange.retain_checkpoints(protected=tuple(sorted(protected)))
        save(self.work / 'CP-retention' / (current + '.json'), result)
        if not result['unverified_chunks']:
            self.state['last_CP_retention'] = current
        self.persist()

    def tick_segment(self):
        now = time.time()
        self.collect_events()
        if now >= self.config['deadline_at']:
            # An earlier typed stop keeps its recovery path; the deadline never launders it.
            self.state['stop'] = self.state['stop'] or 'absolute_deadline'
            self.cancel_at_deadline()
        self.collect_segment_evaluation()
        self.schedule_segment_evaluation()
        self.retain_segment_checkpoints()
        if self.signalled:
            self.state['stop'] = self.state['stop'] or 'watcher_signal'
        if (now >= self.config['deadline_at'] - 600
                or self.state['training_seconds'] >= self.config['training_seconds']):
            self.state['stop'] = self.state['stop'] or 'run_budget_complete'
        health = self.state['health']
        total = stop_denominator(health)
        if self.unknown_window_gate() == 'stop':
            if not self.state['stop']:
                self.state['stop_detail'] = {'at': time.time(), 'site': 'loop',
                                             'health': dict(health),
                                             'rule': 'rolling_unknown_window',
                                             'window': self.state['unknown_window']}
            self.state['stop'] = self.state['stop'] or 'training_unknown_fraction_above_limit'
        uncertain = health.get('unresolved_admissions', 0)
        if unknown_over_limit(uncertain, total + uncertain, self.config):
            self.state['stop'] = self.state['stop'] or 'unresolved_admission_fraction_above_limit'
        if now >= self.config['deadline_at'] - 900 and self.state['pending'] is not None:
            pending = self.state['pending']
            if pending.get('plan'):
                save(Path(pending['plan']).parent / 'STOP', {'reason': 'absolute_deadline'})
            if pending.get('job_id'):
                self.signal_once(pending['job_id'])
        index = self.state['active']
        if index is None:
            if not self.state['stop']:
                self.start_attempt()
            self.persist()
            return
        attempt = self.state['attempts'][index]
        if not attempt.get('job_id'):
            raise ValueError('original_learner_submission_requires_reconciliation_no_retry')
        account = job_accounting(attempt['job_id'])
        if account['terminal']:
            attempt.setdefault('terminal_observed_at', now)
            self.stop_attempt(attempt, 'learner_allocation_terminal', learner_terminal=True)
            import rl

            actors_terminal = True
            retired_names: set[str] = set()
            for plan_path in attempt['actor_plans']:
                plan = rl.load_plan(Path(plan_path))
                rows = rl.actor_accounting(Path(plan_path))
                if len(rows) != len(plan['actors']) or not all(r['terminal'] for r in rows):
                    actors_terminal = False
                retired_names.update(r['actor'] for r in rows if r['terminal'])
            self.state['terminal_actors'] = sorted(set(self.state['terminal_actors']) | retired_names)
            self.refresh_world_health()
            if actors_terminal:
                attempt['terminal_accounting'] = account
                self.state['active'] = None
                self.persist()
            return
        if self.state['stop'] or self.capability_lost or attempt.get('failed'):
            reason = self.state['stop'] or ('watcher_capability_lost' if self.capability_lost
                                           else 'learner_failed')
            self.stop_attempt(attempt, reason)
            if attempt.get('failed'):
                self.state['stop'] = 'learner_failed_requires_diagnosis'
            return
        if attempt.get('window_closed'):
            # The learner emits its closed training window before draining
            # original Worlds/CP, including failure unwinding. No new capacity
            # is useful here, even while Slurm still reports RUNNING.
            self.stop_attempt(attempt, 'learner_draining')
            self.persist()
            return
        self.offer_initial_actors(attempt)
        if attempt['actor_plans']:
            progress_at = attempt.get('last_progress_at')
            if progress_at and now - progress_at > self.config.get('stall_seconds', 900):
                self.state['stop'] = 'learner_progress_stalled'
                self.stop_attempt(attempt, self.state['stop'])
            else:
                self.replace_actors(attempt)
        self.persist()

    def run(self):
        import secrets

        from stpd.p1.local_inference import AUTH_ENV
        if sys.platform != 'linux':
            raise ValueError('Friday_login_owner_required')
        prior = os.environ.get(AUTH_ENV)
        os.environ[AUTH_ENV] = secrets.token_hex(32)
        for number in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT):
            signal.signal(number, lambda *_: setattr(self, 'signalled', True))
        try:
            while True:
                self.tick_segment()
                if self.state['stop'] and self.state['active'] is None:
                    pending = self.state['pending']
                    if pending is None or pending['stage'] in {'failed', 'submission_intent'}:
                        from spireagent.storage.async_archive import ArchivedStore

                        if isinstance(self.exchange.store, ArchivedStore):
                            status = self.exchange.store.archive.drain(timeout=120, owned=False)
                            save(self.work / 'archive-final.json', status)
                            self.retain_segment_checkpoints()
                        return self.state
                time.sleep(self.config.get('poll_seconds', 30))
        except BaseException as error:
            self.telemetry('owner_failed', error_type=type(error).__name__,
                           error_code=getattr(error, 'code', None))
            self.state['stop'] = self.state['stop'] or 'watcher_failure_requires_reconciliation'
            if self.state['active'] is not None:
                attempt = self.state['attempts'][self.state['active']]
                if attempt.get('job_id'):
                    self.stop_attempt(attempt, self.state['stop'])
            self.persist()
            raise
        finally:
            if prior is None:
                os.environ.pop(AUTH_ENV, None)
            else:
                os.environ[AUTH_ENV] = prior


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    work = private_root(args.work.resolve())
    with (work / 'watch.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mode = json.loads(args.config.read_bytes()).get('mode')
        if mode == 'segments':
            SegmentWatcher(read_segment_config(args.config), work).run()
        else:
            Watcher(read_config(args.config), work).run()


if __name__ == '__main__':
    main()
