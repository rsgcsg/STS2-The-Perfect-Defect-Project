#!/usr/bin/env python3
"""Hold an existing Slurm allocation without training or artificial GPU load.

Normal stages run as Slurm job steps. After their Stop/CP/Actor closeout, send
USR1 to this batch owner to release. Slurm's explicit time limit remains hard.
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import threading
import time
from pathlib import Path

from batch import read_published, save


def hold(root: Path, budget_file: Path | None = None) -> int:
    job = os.environ.get('SLURM_JOB_ID')
    if not job or not job.isdigit():
        raise ValueError('existing_Slurm_allocation_required')
    last_budget: int | None = None

    def seconds() -> int | None:
        nonlocal last_budget
        if budget_file is None:
            return None
        record = read_published(budget_file)
        if record is None:
            return last_budget
        value = record['seconds']
        if type(value) is not int or not 1 <= value <= 172800:
            raise ValueError('explicit_GPU_budget_seconds_required')
        last_budget = value
        return value

    began = time.monotonic()
    initial_budget = seconds()
    # A configured but unpublished budget is not permission for an unlimited hold.
    # Wait for its complete first publication before declaring allocation readiness.
    while budget_file is not None and initial_budget is None:
        if time.monotonic() - began >= 60:
            raise TimeoutError('GPU_budget_not_published_within_startup_window')
        time.sleep(.1)
        initial_budget = seconds()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    stopped = threading.Event()
    requested: list[int] = []

    def release(number: int, _frame: object) -> None:
        requested.append(number)
        stopped.set()

    numbers = (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT)
    handlers = {number: signal.signal(number, release) for number in numbers}
    receipt = {'schema': 'spireagent/friday-held-allocation-1', 'job_id': job,
        'node': socket.gethostname(), 'held_at': time.time(), 'pid': os.getpid(),
        'slot_root': os.environ.get('SPIREAGENT_SLOT_ROOT'),
        'budget_file': str(budget_file) if budget_file is not None else None,
        'initial_budget_seconds': initial_budget,
        'stage_entry': 'normal srun --jobid plus resource-slot and learn',
        'release': 'after stage closeout: scancel --signal=USR1 --batch JOB_ID',
        'resource_owner': 'Slurm batch allocation',
        'GPU_load_generated': False, 'secrets_written': False}
    try:
        save(root/'allocation.json', receipt)
        while not stopped.is_set():
            limit = seconds()
            if limit is not None and time.monotonic() - began >= limit:
                break
            stopped.wait(1)
    finally:
        save(root/'released.json', {'at': time.time(), 'job_id': job, 'signals': requested})
        for number, handler in handlers.items():
            signal.signal(number, handler)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--budget-file', type=Path,
        help='Operator-declared per-allocation seconds; update only with actual budget authorization')
    arguments = parser.parse_args()
    raise SystemExit(hold(arguments.root.resolve(), arguments.budget_file))
