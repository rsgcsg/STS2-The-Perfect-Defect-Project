"""Actual learned-Agent main/serve transport, without model construction or fit."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from spireagent.json_boundary import json_bytes

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "sts2.policy-runtime/agent-session-1"
IDENTITY = "é/e\u0301/你好/🐉"
COMMAND_SCRIPT = r'''
import io
import os
import sys
from stpd.policy import native_agent

assert sys.flags.utf8_mode == 0
assert isinstance(sys.stdin, io.TextIOWrapper) and isinstance(sys.stdout, io.TextIOWrapper)
assert sys.stdin.encoding == sys.stdout.encoding == "cp1252"
# This test-owned diagnostic uses binary LF framing independently of the
# production stdout wire and the initial cp1252 TextIOWrappers.
sys.stderr.buffer.write(("probe_pid=%d parent_pid=%d initial_stdio=cp1252 utf8_mode=0\n"
                         % (os.getpid(), os.getppid())).encode("ascii"))
sys.stderr.buffer.flush()

class ProbeAgent:
    # Only numerical/package construction is replaced. The production main,
    # real TextIOWrappers, framing, decoder, session checks and serve emit run.
    def __init__(self, package, manifest):
        self.manifest = {"adapter": {"adapter_id": "transport-probe"},
                         "limits": {"max_message_bytes": 16384}}
        self.sampled = False

    def next(self, value):
        assert value["public"] == "\u00e9/e\u0301/\u4f60\u597d/\U0001f409 CR\rLF\n"
        return {"directive": {"type": "abstain", "reason": value["public"]}}

native_agent.NativeStructuredAgent = ProbeAgent
raise SystemExit(native_agent.main())
'''


def probe_pid(diagnostic: bytes, launcher_pid: int, *, windows: bool) -> int:
    first_line, delimiter, _ = diagnostic.partition(b"\n")
    assert delimiter == b"\n", diagnostic
    header = first_line + delimiter
    match = re.fullmatch(
        rb"probe_pid=([1-9][0-9]*) parent_pid=([1-9][0-9]*) "
        rb"initial_stdio=cp1252 utf8_mode=0\n", header,
    )
    assert match is not None, diagnostic
    interpreter_pid, parent_pid = map(int, match.groups())
    # Windows venv python.exe may be a redirector that owns the actual Python
    # subprocess. Require its observed parent relation, not any positive PID.
    assert interpreter_pid == launcher_pid or (windows and parent_pid == launcher_pid), header
    return interpreter_pid


def run_stdio(payload: bytes, tmp_path: Path) -> tuple[int, bytes, bytes]:
    environment = {**os.environ, "PYTHONPATH": str(ROOT),
                   "PYTHONIOENCODING": "cp1252:strict"}
    with subprocess.Popen(
        [sys.executable, "-X", "utf8=0", "-c", COMMAND_SCRIPT,
         "--package", "unused-probe-package", "--manifest", "unused-probe-manifest"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment,
    ) as child:
        try:
            output, diagnostic = child.communicate(payload, timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=5)
            raise
        assert child.returncode is not None
        (tmp_path / "input.bin").write_bytes(payload)
        (tmp_path / "stdout.bin").write_bytes(output)
        (tmp_path / "stderr.bin").write_bytes(diagnostic)
        (tmp_path / "exit.txt").write_text(
            f"pid={child.pid} exit={child.returncode}\n", encoding="ascii")
        interpreter_pid = probe_pid(diagnostic, child.pid, windows=sys.platform == "win32")
        print(f"pid={child.pid} interpreter_pid={interpreter_pid} "
              f"exit={child.returncode} stdout_bytes={len(output)}")
        return child.returncode, output, diagnostic


@pytest.mark.parametrize("windows,pid,parent,accepted", [
    (False, 101, 99, True), (True, 101, 99, True),
    (True, 202, 101, True), (False, 202, 101, False),
    (True, 202, 99, False),
])
def test_probe_requires_launched_interpreter_or_windows_redirector_child(
    windows: bool, pid: int, parent: int, accepted: bool,
):
    header = f"probe_pid={pid} parent_pid={parent} initial_stdio=cp1252 utf8_mode=0\n".encode()
    if accepted:
        assert probe_pid(header, 101, windows=windows) == pid
    else:
        with pytest.raises(AssertionError):
            probe_pid(header, 101, windows=windows)
    with pytest.raises(AssertionError):
        probe_pid(header.replace(b"\n", b"\r\n"), 101, windows=windows)


def test_actual_main_serve_preserves_unicode_and_lf_under_cp1252(tmp_path: Path):
    public = IDENTITY + " CR\rLF\n"
    message = {"schema": SCHEMA, "message_type": "next", "session_id": IDENTITY,
               "recovery_epoch": 0, "request_id": "request-" + IDENTITY,
               "input": {"public": public}}
    code, output, diagnostic = run_stdio(json_bytes(message), tmp_path)
    assert code == 0, diagnostic
    replies = [json.loads(line) for line in output.split(b"\n")[:-1]]
    assert replies == [
        {"schema": SCHEMA, "message_type": "ready",
         "adapter": {"adapter_id": "transport-probe"}},
        {"schema": SCHEMA, "message_type": "directive", "session_id": IDENTITY,
         "recovery_epoch": 0, "request_id": message["request_id"],
         "output": {"directive": {"type": "abstain", "reason": public}}},
    ]
    assert output == b"".join(json_bytes(reply) for reply in replies)
    assert IDENTITY.encode("utf-8") in output and b"\r" not in output
    assert b"Traceback" not in diagnostic


@pytest.mark.parametrize("payload", [
    b'{"schema":"sts2.policy-runtime/agent-session-1","message_type":"next",'
    b'"session_id":"session","recovery_epoch":0,"request_id":"invalid",'
    b'"input":{"public":"\xff"}}\n',
    b"\xc3\n",
])
def test_actual_main_serve_rejects_malformed_utf8_before_reply(tmp_path: Path, payload: bytes):
    code, output, diagnostic = run_stdio(payload, tmp_path)
    assert code != 0
    assert b"UnicodeDecodeError" in diagnostic and b"'utf-8' codec" in diagnostic
    assert output == json_bytes({"schema": SCHEMA, "message_type": "ready",
                                "adapter": {"adapter_id": "transport-probe"}})
