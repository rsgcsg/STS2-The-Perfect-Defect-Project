#!/usr/bin/env python3
"""Submit a finite one-core Friday array; defaults to a32-worker trial only."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import pwd
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path

from batch import capacity, private_root


def output(arguments: list[str]) -> str:
    return subprocess.check_output(arguments, text=True).strip()


def username() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def submission_lock_path() -> Path:
    return Path("/data") / username() / ".config/spireagent/friday-submit.lock"


@contextmanager
def user_submission_lock():
    """Serialize scheduler observation through accepted submission across all arrays."""
    path = submission_lock_path()
    private_root(path.parent)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+b") as handle:
        identity = os.fstat(handle.fileno())
        if (identity.st_uid != os.getuid() or not stat.S_ISREG(identity.st_mode)
                or stat.S_IMODE(identity.st_mode) != 0o600):
            raise ValueError("private_user_submission_lock_required")
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def submit(configuration: Path, cell: str, requested: int, constraint: str | None,
           *, dry_run: bool) -> dict:
    if not 1 <= requested <= 200:
        raise ValueError("requested1..200cores_required")
    with user_submission_lock():
        existing = output(['squeue', '-h', '-u', username(), '-n',
                           'spireagent-f2', '-o', '%i'])
        if existing:
            raise ValueError('existing_friday_array_must_drain_before_new_submission')
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
