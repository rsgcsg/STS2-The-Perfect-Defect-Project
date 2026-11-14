"""Save tickets (tools/friday/fastchain/tickets.mjs) and the P1-e save probe (save_probe.mjs):
their node:test suites, run where CI runs Python."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

FASTCHAIN = Path(__file__).resolve().parents[2] / 'tools/friday/fastchain'


@pytest.mark.parametrize('suite', ['tickets.test.mjs', 'save_probe.test.mjs'])
def test_save_tickets_node_suite(suite: str) -> None:
    node = shutil.which('node')
    assert node, 'development Node is required for the fast-chain save tickets suites'
    result = subprocess.run([node, '--test', str(FASTCHAIN / suite)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert '# fail 0' in result.stdout
