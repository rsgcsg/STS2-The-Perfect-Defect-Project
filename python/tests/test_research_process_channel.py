"""Actual private-child exit, bounded channel and forced-stop regression fixtures."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.research_process import private_child


@pytest.mark.parametrize("filename, function, process_name", [
    ("test_native_structured_model.py",
     "test_actual_stdio_agent_session_consume_ack_act_and_empty_c_await", "process"),
    ("test_native_training_source_v2_application.py", "fresh_stdio", "child"),
    ("test_sampled_carry_package_integration.py",
     "test_fresh_real_stdio_child_query_consume_ack_original_member_and_summary", "child"),
])
def test_agent_fixture_parent_sends_original_utf8_lf_under_cp1252(
    filename: str, function: str, process_name: str,
):
    # Run the fixture's actual Popen options and send body without constructing
    # a model/fit. Only the peer command is replaced by a byte-exact echo child.
    script = r'''
import ast
import locale
import os
import subprocess
import sys
from pathlib import Path
from spireagent.json_boundary import json_bytes

path, function, process_name = sys.argv[1:]
path = Path(path)
owner = next(node for node in ast.parse(path.read_text(encoding="utf-8")).body
             if isinstance(node, ast.FunctionDef) and node.name == function)
launch = next(node for node in ast.walk(owner) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute) and node.func.attr == "Popen")
send = next(node for node in ast.walk(owner)
            if isinstance(node, ast.FunctionDef) and node.name == "send")
# Popen(text=True) consults this parent-local locale; the production protocol
# still owns UTF-8. Do not change global PYTHONUTF8 or the fixture's environment.
locale.getencoding = lambda: "cp1252"
assert subprocess._text_encoding() == "cp1252"
message_value = {"public": "é/e\u0301/你好/🐉", "carriage_return": "one\rtwo"}
try:
    json_bytes(message_value).decode("utf-8").encode(locale.getencoding())
except UnicodeEncodeError:
    pass
else:
    raise AssertionError("adverse locale control did not reject Unicode")
command = [sys.executable, "-c",
           "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read());sys.stdout.buffer.flush()"]
launch.args = [ast.Name(id="command", ctx=ast.Load())]
ROOT = path.parents[1] if process_name == "process" else path.parents[2]
SESSION_SCHEMA = "sts2.policy-runtime/agent-session-1"
peer = eval(compile(ast.fix_missing_locations(ast.Expression(launch)), str(path), "eval"))
globals()[process_name] = peer
try:
    exec(compile(ast.fix_missing_locations(ast.Module(body=[send], type_ignores=[])),
                 str(path), "exec"))
    send("consume", "unicode-wire", message_value)
    peer.stdin.close()
    output = peer.stdout.read()
    assert peer.wait(timeout=5) == 0, peer.stderr.read()
    assert peer.stderr.read() == b""
    expected = json_bytes({"schema": SESSION_SCHEMA, "message_type": "consume",
        "session_id": {"test_native_structured_model.py": "session",
                       "test_native_training_source_v2_application.py": "common-source-ordinary-stdio",
                       "test_sampled_carry_package_integration.py": "fresh-stdio"}[path.name],
        "recovery_epoch": 0, "request_id": "unicode-wire", "input": message_value})
    assert output == expected, (output, expected)
    assert output.endswith(b"\n") and b"\r" not in output
    print("locale=cp1252 parent_pid=%d peer_pid=%d peer_exit=%d bytes=%d" %
          (os.getpid(), peer.pid, peer.returncode, len(output)))
finally:
    if peer.poll() is None:
        peer.kill()
        peer.wait(timeout=5)
    for stream in (peer.stdin, peer.stdout, peer.stderr):
        stream.close()
'''
    result = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", script,
         str(Path(__file__).with_name(filename)), function, process_name],
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == b"" and b"locale=cp1252" in result.stdout
    print(result.stdout.decode("ascii").strip())


@pytest.mark.parametrize("payload", [b"one\ntwo\n", b"one\r\ntwo\r\n",
                                   "你好\ré\n🐉\r\n".encode("utf-8")])
def test_bounded_channel_streams_lines_and_retains_actual_exit(tmp_path: Path, payload: bytes):
    lines, exits = [], []
    code, captured = private_child(
        [sys.executable, "-c", f"import sys;sys.stdout.buffer.write({payload!r})"],
        tmp_path/"child.log",
        dict(os.environ), on_stdout_line=lines.append,
        on_exited=lambda *args: exits.append(args))
    # The raw reader removes only LF; CR remains original payload data.
    assert code == 0 and lines == payload.split(b"\n")[:-1]
    assert captured == payload and (tmp_path/"child.log").read_bytes() == payload
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
