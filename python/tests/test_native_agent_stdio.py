"""Actual learned-Agent main/serve transport, without model construction or fit."""

from __future__ import annotations

import json
import os
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
sys.stderr.write("probe_pid=%d initial_stdio=cp1252 utf8_mode=0\n" % os.getpid())
sys.stderr.flush()

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
        assert f"probe_pid={child.pid} initial_stdio=cp1252 utf8_mode=0\n".encode("ascii") \
            in diagnostic
        print(f"pid={child.pid} exit={child.returncode} stdout_bytes={len(output)}")
        return child.returncode, output, diagnostic


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
