"""Actual public CLI byte pipes under explicit host encodings; no Model weights."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from spireagent.json_boundary import json_bytes

ROOT = Path(__file__).resolve().parents[1]
UNICODE = "é/e\u0301/你好/🐉 CR\rLF\n"
COMMAND = r"""
import json, sys
from pathlib import Path
from types import SimpleNamespace

family, directory = sys.argv[1:3]
initial = sys.stdin.encoding
sys.stderr.write('initial_stdio=' + initial + ' utf8_mode=' + str(sys.flags.utf8_mode) + '\n')
sys.stderr.flush()
assert sys.flags.utf8_mode == 0
text = 'é/e\u0301/你好/🐉 CR\rLF\n'
protocol = 'sts2.policy-runtime/decision-only-ndjson-' + ('1' if family == 'm0' else '4')
manifest = {'adapter': {'id': 'stpd-public-' + family + '-decision-adapter',
                       'version': '1.0.0', 'protocol': protocol, 'code_sha256': 'c' * 64}}
class Probe:
    # Only construction/numerical/package admission is replaced. Actual main,
    # TextIOWrappers, NDJSON grammar, parser and wire emit remain production code.
    def __init__(self, *args, **kwargs): self.manifest = manifest
    def decide(self, decision, control=None):
        assert decision['bundle']['observation']['public'] == text
        if decision['run_id'] == 'error': raise ValueError(text)
        output = {'candidate_digest': decision['candidate_digest'], 'scores': [1.0],
                  'selected_index': 0}
        if family == 'm0': return output
        return output, {'continuity_token': control['continuity_token'],
                        'episode_id': control['episode_id'], 'segment_id': control['segment_id'],
                        'observation_ordinal': control['observation_ordinal'],
                        'snapshot_id': text, 'sequence': 1, 'previous_action_request_id': None}
    def close(self): pass
if family == 'm0':
    import stpd.policy.public_m0_port as cli
    cli.PublicM0PolicyAdapter = Probe
    sys.argv = ['public_m0', '--config', 'unused-config', '--manifest', 'unused-manifest']
    raise SystemExit(cli.main())
import stpd.public_m2_policy_installation as installation
import stpd.policy.public_m2_export as exported
import stpd.policy.public_m2_port as port
import stpd.policy.public_m2_cli as cli
directory = Path(directory); directory.mkdir()
for name in exported.FILES.values(): (directory / name).write_bytes(b'probe-not-weights')
installation.validate = lambda *args, **kwargs: ({'export_path': str(directory)}, manifest)
info = {'engine_input_digest':'d'*64, 'epoch':1}
exported.validate_package = lambda *args, **kwargs: (
    SimpleNamespace(parameters=SimpleNamespace(value=lambda: info)),
    None, None)
port.PublicM2PolicyAdapter.from_export = classmethod(lambda cls, *args, **kwargs: Probe())
raise SystemExit(cli.main(['serve', '--config', 'unused-config', '--manifest', 'unused-manifest',
                          '--binding-root', 'unused-owner']))
"""


def message(family, request_id="normal", run_id="normal"):
    decision = {
        "run_id": run_id,
        "manifest": {},
        "bundle": {"observation": {"public": UNICODE}, "reads": []},
        "candidate_digest": "a" * 64,
        "candidate_count": 1,
    }
    if family == "m0":
        return {
            "schema": "sts2.policy-runtime/policy-port-1",
            "message_type": "decide",
            "request_id": request_id,
            "input": decision,
        }
    return {
        "schema": "sts2.policy-runtime/policy-port-4",
        "message_type": "decide",
        "request_id": request_id,
        "decision": decision,
        "control": {
            "continuity_token": "token",
            "episode_scope": "bounded_policy_segment",
            "episode_id": UNICODE,
            "segment_id": UNICODE,
            "observation_ordinal": 1,
            "previous_action": None,
        },
    }


def run_pipe(family, encoding, payload, tmp_path):
    environment = {
        **os.environ,
        "PYTHONIOENCODING": encoding + ":strict",
        "PYTHONPATH": str(ROOT),
        "PYTHONCOERCECLOCALE": "0",
    }
    result = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", COMMAND, family, str(tmp_path / "probe")],
        input=payload,
        capture_output=True,
        env=environment,
        timeout=15,
    )
    # This fixture diagnostic uses the initial host TextIOWrapper's newline;
    # production protocol stdout is independently required to contain LF only.
    initial_diagnostic = "initial_stdio=" + encoding + " utf8_mode=0" + os.linesep
    assert result.stderr.startswith(initial_diagnostic.encode())
    (tmp_path / "input.bin").write_bytes(payload)
    (tmp_path / "output.bin").write_bytes(result.stdout)
    (tmp_path / "diagnostic.bin").write_bytes(result.stderr)
    return result


@pytest.mark.parametrize("family", ["m0", "m2"])
@pytest.mark.parametrize("encoding", ["utf-8", "cp1252"])
def test_actual_public_main_and_serve_preserve_raw_unicode_with_explicit_host_encoding(
    family,
    encoding,
    tmp_path,
):
    result = run_pipe(
        family,
        encoding,
        json_bytes(message(family)) + json_bytes(message(family, "error", "error")),
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith(b"\n") and b"\r\n" not in result.stdout
    ready, decision, error = map(json.loads, result.stdout.split(b"\n")[:-1])
    assert ready["message_type"] == "ready"
    assert decision["message_type"] == "decision"
    assert decision["output"] == {
        "candidate_digest": "a" * 64,
        "scores": [1.0],
        "selected_index": 0,
    }
    assert error["message_type"] == "error" and error["error"]["message"] == UNICODE
    assert UNICODE.encode("utf-8") not in result.stdout  # CR/LF stay JSON escapes
    assert "你好/🐉".encode() in result.stdout
    if family == "m2":
        assert decision["completion"]["snapshot_id"] == UNICODE
        assert decision["completion"]["episode_id"] == UNICODE


@pytest.mark.parametrize("family", ["m0", "m2"])
def test_public_cli_rejects_invalid_utf8_instead_of_accepting_a_locale_decoding(family, tmp_path):
    result = run_pipe(family, "cp1252", b"\xff\n", tmp_path)
    assert result.returncode != 0
    replies = list(map(json.loads, result.stdout.split(b"\n")[:-1]))
    assert len(replies) == 1 and replies[0]["message_type"] == "ready"
    assert b'"message_type":"decision"' not in result.stdout
