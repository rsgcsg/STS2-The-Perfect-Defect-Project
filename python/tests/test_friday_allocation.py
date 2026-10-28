"""Real E2 slot/guard and USR1 transport, with no game, model or Slurm execution."""
from __future__ import annotations

import importlib.util
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip('fcntl', reason='Friday allocation resource domains are POSIX')
ROOT = Path(__file__).resolve().parents[2]
FRIDAY = ROOT / 'tools/friday'


def module_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def allocation():
    batch = module_file('allocation_batch', FRIDAY / 'batch.py')
    with patch.dict(sys.modules, {'batch': batch}):
        return module_file('friday_allocation', FRIDAY / 'allocation.py')


def test_original_modes_map_correct_slurm_lanes_and_budgets(allocation, tmp_path, monkeypatch):
    file = tmp_path / 'plan.json'
    file.write_text(json.dumps({'workers': 4, 'selected_limit': 60, 'wall_seconds': 7200}),
                    encoding='utf-8')
    details = allocation.metadata('evaluation', file)
    assert details['lanes'] == 4 and details['games_bound'] == 60
    assert details['wall_seconds'] == 7200
    actor = tmp_path / 'actor.json'
    actor.write_text(json.dumps({'max_games': 20, 'wall_seconds': 3600}), encoding='utf-8')
    file.write_text(json.dumps({'actors': [{'path': str(actor)}] * 8}), encoding='utf-8')
    monkeypatch.setenv('SLURM_ARRAY_TASK_ID', '3')
    details = allocation.metadata('rl', file)
    assert details['lanes'] == 1 and details['games_bound'] == 20


def test_real_slot_run_forwards_advance_USR1_to_python_and_releases_original_slot(tmp_path):
    node = shutil.which('node')
    assert node, 'development Node is required for the original E2 CLI regression'
    root = tmp_path.resolve()
    configuration = root / 'plan.json'
    configuration.write_text(json.dumps({'workers': 2, 'selected_limit': 1, 'wall_seconds': 60}),
                             encoding='utf-8')
    child = textwrap.dedent('''\
        import os, signal, sys, threading
        from spireagent.resource_slot import require_managed_slot
        received = threading.Event()
        signal.signal(signal.SIGUSR1, lambda *_: received.set())
        require_managed_slot()
        print('ready:' + str(os.getppid()), flush=True)
        assert received.wait(15), 'advance signal not delivered to original Python'
        print('received', flush=True)
        assert sys.stdin.readline().strip() == 'finish'
        require_managed_slot()
        print('drained', flush=True)
        ''')
    # The test uses the developer's real supported Node to run UNCHANGED E2. Only
    # production's binary-location binding is supplied; neither guard nor token is mocked.
    driver = textwrap.dedent('''\
        import json, sys
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        import allocation
        result = allocation.run('evaluation', Path(sys.argv[2]), Path(sys.argv[3]),
            [sys.executable, '-c', sys.argv[5]], node=Path(sys.argv[4]))
        print('result:' + json.dumps(result), flush=True)
        ''')
    command = [sys.executable, '-c', driver, str(FRIDAY), str(configuration),
               str(ROOT), node, child]
    environment = {'PATH': str(Path(node).parent) + ':/usr/bin:/bin',
                   'PYTHONPATH': str(ROOT / 'python'), 'SLURM_JOB_ID': 'slot-regression'}
    with subprocess.Popen(command, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) as process:
        try:
            readable, _, _ = select.select([process.stdout], [], [], 20)
            assert readable, 'original E2 did not start its guarded Python child'
            ready = process.stdout.readline().strip()
            assert ready.startswith('ready:')
            e2_pid = int(ready.split(':')[1])
            process.send_signal(signal.SIGUSR1)
            readable, _, _ = select.select([process.stdout], [], [], 20)
            assert readable and process.stdout.readline().strip() == 'received'
            os.kill(e2_pid, 0)  # Original E2 Node remains alive during the child's drain.
            assert process.poll() is None
            process.stdin.write('finish\n')
            process.stdin.flush()
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0 and stderr == ''
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)
    lines = stdout.splitlines()
    assert lines[0] == 'drained'
    result = json.loads(lines[1].removeprefix('result:'))
    assert result['slot_command_exit_code'] == 0
    assert result['received_signals'] == result['forwarded_signals'] == [signal.SIGUSR1]
    assert result['lanes'] == 2
    slot = Path(result['slot_root'])
    assert not (slot / 'managed.json').exists()  # Normal E2 release, no manual token/lock writes.
    journal = Path(result['journal']).read_text(encoding='utf-8')
    assert all(value in journal for value in ('F2-Friday', '开始', '结束', '2路1局'))
    assert 'token' not in journal
    shutil.rmtree(slot)  # Only this fully released test-created private directory.
