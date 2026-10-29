#!/usr/bin/env python3
"""Run a frozen fixed-seed Managed evaluation in one Friday Slurm allocation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import signal
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import submit as scheduler
from batch import capacity, hashed, private_root, read_pinned, save, snapshot

SCHEMA = "spireagent/friday-evaluation-v1"
RETENTION = {"tier": "trajectory", "purpose": "evaluation", "audit_every": 50}


def validate_execution(execution: dict[str, Any], selections: dict[str, str]) -> None:
    from spireagent.workbench.managed_evaluation import storage_configuration
    from spireagent.workbench.managed_retention import (
        pipeline_configuration,
        retention_configuration,
    )

    if (execution.get("schema") != "spireagent/managed-evaluation-execution-v1"
            or execution.get("retention") != RETENTION or execution.get("store") != "s3"
            or execution.get("peer", {}).get("kind") != "s3"):
        raise ValueError("fixed_remote_evaluation_configuration_required")
    if (not selections or len(set(selections.values())) != len(selections)
            or set(selections.values()) != set(execution["models"])):
        raise ValueError("exact_distinct_model_selections_required")
    storage_configuration(execution)
    retention_configuration(execution)
    pipeline_configuration(execution)


def prepare(execution_path: Path, output: Path, *, selections: dict[str, str],
            workers: int = 4, wall_seconds: int = 7200, episode_seconds: int = 900,
            manifest_path: Path | None = None, limit: int | None = None,
            seed_prefix: int | None = None, runtime_source: Path | None = None) -> dict[str, Any]:
    from spireagent.json_boundary import json_bytes
    from spireagent.workbench.managed_retention import pipeline_configuration
    from spireagent.workbench.reference_suite import load_manifest
    from stpd.evaluation.suites import FOUR_AGENT_SUITE_ID, load_suite

    if (type(workers) is not int or not 1 <= workers <= 16
            or type(wall_seconds) is not int or not 300 <= wall_seconds <= 21600
            or type(episode_seconds) is not int or not 10 <= episode_seconds <= 1800
            or wall_seconds <= episode_seconds + 180
            or limit is not None and (type(limit) is not int or not 1 <= limit <= 200)):
        raise ValueError("bounded_allocation_and_episode_limits_required")
    if runtime_source is not None and (not runtime_source.is_absolute()
            or runtime_source.is_symlink() or runtime_source.resolve() != runtime_source
            or not (runtime_source / 'tools/friday/evaluate.py').is_file()):
        raise ValueError('canonical_frozen_runtime_source_required')
    execution_raw = execution_path.read_bytes()
    execution = json.loads(execution_raw)
    validate_execution(execution, selections)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    private_root(output)
    manifest = output / "manifest.json"
    if manifest_path is None:
        value = load_suite("ref-20")
        value["host"] = "Managed"
        manifest.write_bytes(json_bytes(value))
    else:
        manifest.write_bytes(manifest_path.read_bytes())
    _, value = load_manifest(manifest, hashed(manifest))
    if (value["host"] != "Managed" or value["purpose"] == "diagnostic_reference"
            or value["suite_id"] == FOUR_AGENT_SUITE_ID):
        raise ValueError("canonical_fixed_managed_evaluation_manifest_required")
    if seed_prefix is not None and (
            type(seed_prefix) is not int or not 1 <= seed_prefix <= len(value["seeds"])):
        raise ValueError("bounded_canonical_seed_prefix_required")
    planned = (seed_prefix or len(value["seeds"])) * len(selections)
    selected_count = min(limit or planned, planned)
    if selected_count > 200:
        raise ValueError("single_dispatch_maximum200_selected_slots")
    pipeline = pipeline_configuration(execution)
    frozen_execution = output / "execution.json"
    frozen_execution.write_bytes(execution_raw)
    plan = {"schema": SCHEMA,
            "execution": {"path": str(frozen_execution), "sha256": hashed(frozen_execution)},
            "manifest": {"path": str(manifest), "sha256": hashed(manifest)},
            "selections": selections, "workers": workers, "wall_seconds": wall_seconds,
            "episode_seconds": episode_seconds, "selected_limit": selected_count,
            "planned": planned, "parallelism": {"requested_workers": workers,
                "pending_limit": pipeline["pending_limit"], "save_workers": pipeline["save_workers"],
                "execution_upper_bound": min(workers, pipeline["pending_limit"])},
            "source_root": str(runtime_source or Path(__file__).resolve().parents[2]),
            **({"seed_prefix": seed_prefix} if seed_prefix is not None else {}),
            "target": "friday", "automatic_mac_fallback": False, "retry_unknown": False}
    save(output / "plan.json", plan)
    return plan


def create_ledger(plan: dict[str, Any], directory: Path):
    from spireagent.workbench.evaluation_ledger import EvaluationLedger

    execution = read_pinned(plan["execution"])
    read_pinned(plan["manifest"])
    validate_execution(execution, plan["selections"])
    options = {"seed_prefix": plan["seed_prefix"]} if "seed_prefix" in plan else {}
    return EvaluationLedger.create(directory, Path(plan["manifest"]["path"]),
        plan["manifest"]["sha256"], selections=plan["selections"],
        execution=Path(plan["execution"]["path"]), **options)


def load_plan(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if (value.get("schema") != SCHEMA or value.get("target") != "friday"
            or value.get("automatic_mac_fallback") is not False
            or value.get("retry_unknown") is not False):
        raise ValueError("frozen_friday_evaluation_plan_required")
    validate_execution(read_pinned(value["execution"]), value["selections"])
    read_pinned(value["manifest"])
    return value


def launcher(plan_path: Path, plan: dict[str, Any]) -> str:
    """Nonsensitive user launcher; the program loads the user's private credentials."""
    source = Path(plan["source_root"])
    actor = source.parent
    replacements = {"__ACTOR__": shlex.quote(str(actor)),
                    "__SOURCE__": shlex.quote(str(source)),
                    "__PLAN__": shlex.quote(str(plan_path)), "__SLOT_MODE__": "evaluation"}
    script = r'''#!/bin/bash
set +x
set -euo pipefail
umask 077
actor_root=__ACTOR__
source_root=__SOURCE__
credentials=${FRIDAY_R2_ENV:-/data/$(id -un)/.config/spireagent/friday-r2.env}
[[ -f "$credentials" && ! -L "$credentials" \
   && $(stat -c '%a' "$credentials") = 600 \
   && $(stat -c '%u' "$credentials") = $(id -u) ]] || {
  echo 'Friday evaluation needs the user-owned600 R2 environment' >&2; exit 2;
}
set -a
source "$credentials"
set +a
if ! type module >/dev/null 2>&1; then source /etc/profile; fi
module load python/3.11.5-gcccore-13.2.0 nodejs/20.9.0-gcccore-13.2.0
test -x "$actor_root/node22.23.4/bin/node" || { echo 'pinned Node22.23.4 required' >&2; exit 2; }
export PATH="$actor_root/env/bin:$actor_root/dotnet-sdk9.0.117:/data/$(id -un)/dotnet9:$PATH"
export PATH="$actor_root/node22.23.4/bin:$PATH"
export PYTHONPATH="$source_root/python"
export STS2_GAME_DIR="$actor_root/game"
export DOTNET_PROCESSOR_COUNT=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
export FRIDAY_INFERENCE_THREADS=${FRIDAY_INFERENCE_THREADS:-1}
exec "$actor_root/env/bin/python" "$source_root/tools/friday/allocation.py" \
  --mode __SLOT_MODE__ --configuration __PLAN__ --source-root "$source_root" -- \
  "$actor_root/env/bin/python" "$source_root/tools/friday/evaluate.py" run --plan __PLAN__
'''
    for token, value in replacements.items():
        script = script.replace(token, value)
    return script


def submit(plan_path: Path, *, constraint: str | None = None,
           dry_run: bool = False, begin: str | None = None) -> dict[str, Any]:
    # Slurm owns the timer; this is an earliest start, not a resource reservation.
    if begin is not None:
        parsed = datetime.fromisoformat(begin)
        if parsed.tzinfo is None or parsed.timestamp() <= time.time():
            raise ValueError("future_begin_with_explicit_timezone_required")
        begin = parsed.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    plan = load_plan(plan_path)
    root = private_root(plan_path.parent)
    with scheduler.user_submission_lock():
        job_name = 'spireagent-eval-' + hashlib.sha256(str(plan_path).encode()).hexdigest()[:12]
        existing = scheduler.output(['squeue', '-h', '-u', scheduler.username(), '-n',
                                     job_name, '-o', '%i'])
        if existing:
            raise ValueError('existing_evaluation_plan_must_drain_before_new_submission')
        if (root / 'owner.json').exists() or (root / 'STOP').exists():
            raise ValueError('previous_evaluation_requires_original_owner_reconciliation')
        if (root / 'submission-intent.json').exists():
            raise ValueError('previous_evaluation_submission_requires_owner_reconciliation')
        idle = sum(int(line.split('/')[1]) for line in scheduler.output(
            ['sinfo', '-h', '-p', 'smp', '-o', '%C']).splitlines())
        queued = len(scheduler.output(
            ['squeue', '-h', '-p', 'smp', '-t', 'PD', '-o', '%i']).splitlines())
        if capacity(idle, queued, plan['workers']) < plan['workers']:
            raise ValueError('requested_evaluation_workers_exceed_current_fair_share')
        script = root / 'launch.sh'
        script.write_text(launcher(plan_path, plan), encoding='utf-8')
        script.chmod(0o700)
        logs = private_root(root / 'slurm')
        minutes = (plan['wall_seconds'] + 300 + 59) // 60
        command = ['sbatch', '--parsable', '--partition=smp', '--nodes=1', '--ntasks=1',
                   f"--cpus-per-task={plan['workers']}", f"--mem={4 * plan['workers']}G",
                   f'--time={minutes}', '--no-requeue', '--signal=B:USR1@300',
                   f'--job-name={job_name}', f'--output={logs}/%j.out',
                   f'--error={logs}/%j.err']
        if constraint:
            command.append('--constraint=' + constraint)
        if begin is not None:
            command.append('--begin=' + begin)
        command.append(str(script))
        result = {'schema': 'spireagent/friday-evaluation-submit-v1',
                  'idle_smp_cores': idle, 'queued_smp_jobs': queued,
                  'cores': plan['workers'], 'command': command, 'dry_run': dry_run,
                  'earliest_begin_local': begin, 'scheduler_start_guaranteed': False}
        if not dry_run:
            result['submission_requested_at'] = time.time()
            save(root / 'submission-intent.json', result)
            result['job_id'] = scheduler.output(command)
            result['submission_confirmed_at'] = time.time()
            save(root / 'submission.json', result)
        return result


class StopOnUncertain:
    """Delegate unchanged owner execution and halt NEW admission on uncertainty."""
    def __init__(self, delegate: Any, stop: Path):
        self.delegate, self.stop = delegate, stop

    def inspect(self, row: dict[str, Any]) -> None:
        if (row.get('status') in {'unknown', 'save_failed'}
                or row.get('execution_status') == 'unknown'
                or row.get('world_mutation_offered') and not row.get('cleanup_accepted')):
            self.stop.touch()

    def __call__(self, row, manifest, artifacts, checkpoint):
        try:
            self.delegate(row, manifest, artifacts, checkpoint)
            self.inspect(row)
        except Exception:  # Preserve the original failure and stop new admission.
            self.stop.touch()
            raise

    def preserve_report(self, path):
        report = self.delegate.preserve_report(path)
        for row in report['episodes']:
            self.inspect(row)
        return report

    def finalize_trajectories(self, paths):
        results = self.delegate.finalize_trajectories(paths)
        for _path, report in results:
            for row in report['episodes']:
                self.inspect(row)
        return results


def run(plan_path: Path) -> dict[str, Any]:
    from spireagent.workbench.managed_pipeline import PipelinedManagedExecutor
    from spireagent.workbench.reference_suite import SuiteLimits

    process_started_at = time.time()
    process_began = time.monotonic()
    plan = load_plan(plan_path)
    if (sys.platform != 'linux' or not os.environ.get('SLURM_JOB_ID')
            or int(os.environ.get('SLURM_CPUS_PER_TASK', '0')) < plan['workers']):
        raise ValueError('Friday_single_node_allocation_required_no_mac_fallback')
    root = private_root(plan_path.parent)
    if (root / 'STOP').exists():
        return {'status': 'stopped', 'reason': 'stop_requested', 'new_games': 0}
    # One plan gets one allocation owner. Lost jobs are reconciled, never rerun as new games.
    descriptor = os.open(root / 'owner.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    scratch = Path(tempfile.mkdtemp(prefix='spireagent-friday-evaluation-')).resolve()
    scratch.chmod(0o700)
    with os.fdopen(descriptor, 'wb') as owner:
        owner.write(json.dumps({'job_id': os.environ['SLURM_JOB_ID'], 'scratch': str(scratch),
                                'plan_sha256': hashed(plan_path)}).encode())
        owner.flush()
        os.fsync(owner.fileno())
    ledger = create_ledger(plan, scratch / 'ledger')
    ledger_prepared_seconds = time.monotonic() - process_began
    selected = tuple(row['slot_id'] for row in ledger.plan['slots'][:plan['selected_limit']])
    save(root / 'admission.json', {'original_ledger_plan': ledger.plan,
        'selected_slot_ids': selected, 'unknown_retry': False})
    # All fixed seeds/models/audit slots/selection are durable before the first native offer.
    stopping = threading.Event()
    ledger_stop = ledger.directory / 'STOP'

    def stop(_number=None, _frame=None):
        ledger_stop.touch()

    def observe_stop():
        while not stopping.wait(1):
            if (root / 'STOP').exists():
                stop()
                return

    previous = {number: signal.signal(number, stop)
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)}
    watcher = threading.Thread(target=observe_stop, daemon=True)
    watcher.start()
    executor = None
    began = time.monotonic()
    error_type = error_code = None
    result = None
    phases: dict[str, float] = {'ledger_preparation': ledger_prepared_seconds}
    try:
        execution = read_pinned(plan['execution'])
        phase_began = time.monotonic()
        executor = PipelinedManagedExecutor(execution, episode_seconds=plan['episode_seconds'],
                                           cleanup_seconds=120)
        save(scratch / 'preparation.json', executor.preparation)
        phases['executor_preparation'] = time.monotonic() - phase_began
        limits = SuiteLimits(150, plan['episode_seconds'], 120,
                            plan['wall_seconds'] - plan['episode_seconds'] - 180, 1, 30)
        phase_began = time.monotonic()
        result = ledger.run_managed(workers=plan['workers'], max_episodes=len(selected),
            limits=limits, executor=StopOnUncertain(executor, ledger_stop), slot_ids=selected)
        phases['execution_and_pipeline_save'] = time.monotonic() - phase_began
    except Exception as error:  # noqa: BLE001 -- the existing attempt is retained, never retried.
        error_type, error_code = type(error).__name__, getattr(error, 'code', None)
        stop()
    finally:
        phase_began = time.monotonic()
        if executor is not None:
            try:
                executor.close()
            except Exception as error:  # noqa: BLE001 -- retain close failures with the same attempt.
                if error_type is None:
                    error_type, error_code = type(error).__name__, getattr(error, 'code', None)
        stopping.set()
        watcher.join(timeout=2)
        phases['executor_close'] = time.monotonic() - phase_began
        report = ledger.report()
        summary = {'schema': 'spireagent/friday-evaluation-result-v1',
            'status': 'failed' if error_type else (result or {}).get('status', 'failed'),
            'plan_sha256': hashed(plan_path), 'job_id': os.environ['SLURM_JOB_ID'],
            'elapsed_seconds': time.monotonic() - began, 'dispatch': result,
            'error_type': error_type, 'error_code': error_code, 'ledger_report': report,
            'selected_slot_ids': selected, 'scratch_retained': str(scratch),
            'full_planned_denominator': plan['planned'], 'retry_unknown': False}
        summary['timing'] = {'process_started_at': process_started_at, 'phases': phases}
        save(scratch / 'result.json', summary)
        try:
            phase_began = time.monotonic()
            receipt = snapshot(scratch, root / 'evidence')
            phases['snapshot'] = time.monotonic() - phase_began
            save(root / 'snapshot.json', receipt)
            summary['timing'].update(process_finished_at=time.time(),
                                     process_wall_seconds=time.monotonic() - process_began)
            # The original elapsed_seconds remains backward compatible. Full
            # process time includes ledger/preparation/snapshot; Slurm adds queue.
            save(root / 'result.json', summary)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_parser = commands.add_parser('prepare')
    prepare_parser.add_argument('--execution', type=Path, required=True)
    prepare_parser.add_argument('--output', type=Path, required=True)
    prepare_parser.add_argument('--manifest', type=Path)
    prepare_parser.add_argument('--selection', action='append', required=True,
                                help='model label=existing execution selection ID')
    prepare_parser.add_argument('--workers', type=int, default=4)
    prepare_parser.add_argument('--runtime-source', type=Path)
    prepare_parser.add_argument('--wall-seconds', type=int, default=7200)
    prepare_parser.add_argument('--episode-seconds', type=int, default=900)
    prepare_parser.add_argument('--limit', type=int)
    prepare_parser.add_argument('--seed-prefix', type=int,
                                help='freeze this original canonical seed prefix in a NEW ledger')
    for name in ('submit', 'run'):
        command = commands.add_parser(name)
        command.add_argument('--plan', type=Path, required=True)
        if name == 'submit':
            command.add_argument('--constraint')
            command.add_argument('--dry-run', action='store_true')
            command.add_argument('--begin', help='earliest start ISO8601 with timezone; no reservation')
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == 'prepare':
        pairs = [value.split('=', 1) for value in args.selection]
        if any(len(pair) != 2 or not all(pair) for pair in pairs):
            parser.error('--selection requires label=selection')
        selections = dict(pairs)
        if len(selections) != len(pairs):
            parser.error('duplicate selection labels')
        result = prepare(args.execution.resolve(), args.output.resolve(), selections=selections,
            workers=args.workers, wall_seconds=args.wall_seconds,
            episode_seconds=args.episode_seconds,
            manifest_path=args.manifest.resolve() if args.manifest else None, limit=args.limit,
            seed_prefix=args.seed_prefix, runtime_source=args.runtime_source)
    elif args.command == 'submit':
        result = submit(args.plan.resolve(), constraint=args.constraint,
                        dry_run=args.dry_run, begin=args.begin)
    else:
        result = run(args.plan.resolve())
    print(json.dumps(result, sort_keys=True))
    if args.command == 'run' and result.get('error_type'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
