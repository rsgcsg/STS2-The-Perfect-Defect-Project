#!/usr/bin/env python3
"""Map one Slurm allocation to the original E2 resource-slot command."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from batch import hashed, private_root, save


def metadata(mode: str, path: Path) -> dict[str, Any]:
    configuration = json.loads(path.read_bytes())
    if mode == 'batch':
        pool = json.loads(Path(configuration['pool']['path']).read_bytes())
        maximum_groups = int(os.environ.get('FRIDAY_MAX_GROUPS', '100'))
        games = sum(group['episode_count'] for group in pool['groups'][:maximum_groups])
        lanes, wall = 1, float(os.environ.get('FRIDAY_WALL_SECONDS', '3600'))
        logs = Path(configuration['persistent_root']) / 'slurm'
    elif mode == 'evaluation':
        lanes, games = configuration['workers'], configuration['selected_limit']
        wall, logs = configuration['wall_seconds'], path.parent / 'slurm'
    elif mode == 'rl':
        index = int(os.environ['SLURM_ARRAY_TASK_ID'])
        actor = json.loads(Path(configuration['actors'][index]['path']).read_bytes())
        lanes, games = 1, actor['max_games']
        wall, logs = actor['wall_seconds'], path.parent / 'slurm'
    else:
        raise ValueError('original_Friday_allocation_mode_required')
    return {'lanes': lanes, 'games_bound': games, 'wall_seconds': wall, 'logs': logs}


def run(mode: str, configuration: Path, source: Path, command: list[str], *,
        node: Path | None = None) -> dict[str, Any]:
    """Only E2 creates/releases the lock/token and supervises the command process tree.

    The PID exec bridge routes advance USR1 to the original Python command, since
    E2 Node's TERM cancellation force-kills after about one second. Actual TERM/
    INT still go to E2 unchanged; that path is cancellation, not graceful drain.
    """
    details = metadata(mode, configuration)
    node = node or source.parent / 'node22.23.4/bin/node'
    if not node.is_file() or not os.access(node, os.X_OK):
        raise ValueError('pinned_Node22.23.4_required')
    logs = private_root(details.pop('logs'))
    slot = Path(tempfile.mkdtemp(prefix='spireagent-allocation-slot-', dir='/tmp')).resolve()
    slot.chmod(0o700)
    until = (datetime.now(UTC) + timedelta(seconds=details['wall_seconds'] + 300)).isoformat()
    pid_file = slot / 'command.pid'
    # This is process routing metadata, never a reservation/token or gameplay command.
    bridge = 'umask 077; printf "%s\\n" "$$" > "$1"; shift; exec "$@"'
    arguments = [str(node), str(source / 'tools/resource-slot.mjs'), 'run',
        '--resources', 'managed', '--card', 'F2-Friday', '--lanes', str(details['lanes']),
        '--games', str(details['games_bound']), '--until', until, '--',
        '/bin/sh', '-c', bridge, 'friday-command', str(pid_file), *command]
    previous_root = os.environ.get('SPIREAGENT_SLOT_ROOT')
    os.environ['SPIREAGENT_SLOT_ROOT'] = str(slot)
    process = None
    received, forwarded = [], []

    def relay(number, _frame):
        received.append(number)
        if process is None or process.poll() is not None:
            return
        if number == signal.SIGUSR1:
            if pid_file.is_file():
                try:
                    os.kill(int(pid_file.read_text(encoding='ascii').strip()), signal.SIGUSR1)
                    forwarded.append(number)
                except ProcessLookupError:
                    pass
            # Never send USR1 to Node: Node may enable its inspector.
        else:
            process.send_signal(number)
            forwarded.append(number)

    previous = {number: signal.signal(number, relay)
                for number in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT)}
    code = None
    try:
        process = subprocess.Popen(arguments)
        code = process.wait()
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        if previous_root is None:
            os.environ.pop('SPIREAGENT_SLOT_ROOT', None)
        else:
            os.environ['SPIREAGENT_SLOT_ROOT'] = previous_root
        identity = os.environ.get('SLURM_JOB_ID', 'manual') + '-' + os.environ.get(
            'SLURM_ARRAY_TASK_ID', '0')
        journal = logs / f'{identity}-GAME-SLOT.md'
        if (slot / 'GAME-SLOT.md').is_file():
            shutil.copyfile(slot / 'GAME-SLOT.md', journal)
            journal.chmod(0o600)
            if hashed(journal) != hashed(slot / 'GAME-SLOT.md'):
                raise ValueError('allocation_journal_readback_failed')
        result = {'schema': 'spireagent/friday-allocation-slot-v1', **details,
            'until': until, 'slot_root': str(slot), 'journal': str(journal),
            'slot_command_exit_code': code, 'received_signals': received,
            'forwarded_signals': forwarded, 'mode': mode}
        save(logs / f'{identity}-allocation.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('batch', 'evaluation', 'rl'), required=True)
    parser.add_argument('--configuration', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('original command required after --')
    result = run(args.mode, args.configuration.resolve(), args.source_root.resolve(), command)
    raise SystemExit(result['slot_command_exit_code'] or 0)


if __name__ == '__main__':
    main()
