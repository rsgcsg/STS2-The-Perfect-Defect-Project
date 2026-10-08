"""Actual private-child exit, bounded channel and forced-stop regression fixtures."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.research_process import private_child


def test_bounded_channel_streams_lines_and_retains_actual_exit(tmp_path: Path):
    lines, exits = [], []
    code, captured = private_child(
        [sys.executable, "-c", "print('one'); print('two')"], tmp_path/"child.log",
        dict(os.environ), on_stdout_line=lines.append,
        on_exited=lambda *args: exits.append(args))
    assert code == 0 and lines == [b"one", b"two"]
    assert captured == b"one\ntwo\n"
    assert exits[0][0:2] == (0, False) and exits[0][2] >= 0


@pytest.mark.parametrize("callback", ["start", "message", "control"])
def test_callback_failure_kills_and_waits_child_without_hanging(tmp_path: Path, callback: str):
    exits = []

    def failure(*_args):
        raise RuntimeError("fixture callback failed")

    options = {"on_started": None, "on_stdout_line": None, "stop_requested": None}
    options[{"start": "on_started", "message": "on_stdout_line",
             "control": "stop_requested"}[callback]] = failure
    with pytest.raises(RuntimeError, match="fixture callback failed"):
        script = "import time; print('ready',flush=True);time.sleep(30)"
        private_child([sys.executable, "-c", script],
                      tmp_path/"child.log", dict(os.environ),
                      on_exited=lambda *args: exits.append(args), **options)
    assert len(exits) == 1 and exits[0][1] is True and exits[0][0] != 0
    assert exits[0][2] < 5


def test_hard_timeout_is_recorded_after_actual_wait(tmp_path: Path):
    exits = []
    with pytest.raises(BoundaryError, match="private_child_timeout"):
        private_child([sys.executable, "-c", "import time;time.sleep(30)"],
                      tmp_path/"child.log", dict(os.environ), timeout_seconds=0.1,
                      on_exited=lambda *args: exits.append(args))
    assert exits[0][1] is True and exits[0][0] != 0 and exits[0][2] < 5


@pytest.mark.parametrize("script,code", [
    ("print('x'*20000,flush=True)", "private_child_line_budget"),
    ("import sys;sys.stdout.write('truncated')", "private_child_channel_truncated"),
])
def test_unbounded_or_truncated_machine_channel_is_not_a_terminal_receipt(
    tmp_path: Path, script: str, code: str,
):
    exits = []
    with pytest.raises(BoundaryError, match=code):
        private_child([sys.executable, "-c", script], tmp_path/"child.log", dict(os.environ),
                      on_stdout_line=lambda _line: None,
                      on_exited=lambda *args: exits.append(args))
    assert len(exits) == 1  # Real exit preserved even if the stream is untrustworthy.
