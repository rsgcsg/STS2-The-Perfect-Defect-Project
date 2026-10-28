#!/usr/bin/env python3
"""Submit a finite one-core Friday array; defaults to a32-worker trial only."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from batch import capacity, private_root


def output(arguments: list[str]) -> str:
    return subprocess.check_output(arguments, text=True).strip()


def submit(configuration: Path, cell: str, requested: int, constraint: str | None,
           *, dry_run: bool) -> dict:
    if not 1 <= requested <= 200:
        raise ValueError("requested1..200cores_required")
    # Exact current scheduler observations, never the hardware inventory as free capacity.
    idle = sum(int(line.split('/')[1]) for line in output(
        ['sinfo', '-h', '-p', 'smp', '-o', '%C']).splitlines())
    queued = len(output(['squeue', '-h', '-p', 'smp', '-t', 'PD', '-o', '%i']).splitlines())
    count = capacity(idle, queued, requested)
    if count < 1:
        raise ValueError('no_fair_share_capacity_now')
    root = Path(__file__).resolve().parent
    configuration = configuration.resolve(strict=True)
    settings = json.loads(configuration.read_bytes())
    persistent = private_root(Path(settings['persistent_root']))
    logs = private_root(persistent / 'slurm')
    command = ['sbatch', '--parsable', '--partition=smp', '--nodes=1', '--ntasks=1',
               '--cpus-per-task=1', '--mem=4G', '--time=01:05:00', '--no-requeue',
               '--signal=B:USR1@900', '--job-name=spireagent-f2',
               f'--array=0-{requested - 1}%{count}',
               f'--output={logs}/%A_%a.out', f'--error={logs}/%A_%a.err']
    if constraint:
        command.append('--constraint=' + constraint)
    command.extend([str(root / 'worker.sh'), str(configuration), cell])
    result = {'schema': 'spireagent/friday-submit-v1', 'requested': requested,
              'idle_smp_cores': idle, 'queued_smp_jobs': queued, 'concurrency': count,
              'command': command, 'dry_run': dry_run}
    if not dry_run:
        result['job_id'] = output(command)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configuration', type=Path)
    parser.add_argument('--cell', required=True)
    parser.add_argument('--cores', type=int, default=32)
    parser.add_argument('--constraint')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    print(json.dumps(submit(args.configuration, args.cell, args.cores, args.constraint,
                            dry_run=args.dry_run), indent=2))


if __name__ == '__main__':
    main()
