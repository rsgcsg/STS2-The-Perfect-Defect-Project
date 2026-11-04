#!/usr/bin/env python3
"""Hold an existing Slurm allocation without training or artificial GPU load.

Normal stages run as Slurm job steps. After their Stop/CP/Actor closeout, send
USR1 to this batch owner to release. Slurm's explicit time limit remains hard.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import threading
import time
from pathlib import Path


def hold(root: Path) -> int:
    job = os.environ.get('SLURM_JOB_ID')
    if not job or not job.isdigit():
        raise ValueError('existing_Slurm_allocation_required')
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
        'stage_entry': 'normal srun --jobid plus resource-slot and learn',
        'release': 'after stage closeout: scancel --signal=USR1 --batch JOB_ID',
        'resource_owner': 'Slurm batch allocation',
        'GPU_load_generated': False, 'secrets_written': False}
    try:
        (root/'allocation.json').write_text(json.dumps(receipt, indent=2)+'\n')
        stopped.wait()
    finally:
        (root/'released.json').write_text(json.dumps(
            {'at': time.time(), 'job_id': job, 'signals': requested})+'\n')
        for number, handler in handlers.items():
            signal.signal(number, handler)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    arguments = parser.parse_args()
    raise SystemExit(hold(arguments.root.resolve()))
