"""The paid calibration wrapper cold-decodes without either project package."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from modal._serialization import serialize_data_format
from modal_proto import api_pb2


def test_calibration_wrapper_resources_cold_decoder_and_exact_worker_entry(tmp_path, monkeypatch):
    owner = runpy.run_path(str(Path(__file__).parents[1]
                              / "deploy/cloud-worker/calibrate_p1.py"))
    definitions = []

    class App:
        def function(self, **options):
            definitions.append(options)
            return lambda function: function

    sdk = SimpleNamespace(Image=SimpleNamespace(from_id=lambda identity: identity),
                          Secret=SimpleNamespace(from_name=lambda name: name))
    function = owner["definition"](App(), sdk, "im-" + "a" * 22, timeout=1200, cpu=2,
                                   memory=16384, secret_name="existing-worker-store")
    settings = definitions[0]
    assert settings["gpu"] == "L40S" and settings["cpu"] == (2, 2)
    assert settings["memory"] == (16384, 16384) and settings["retries"] == 0
    assert settings["timeout"] == 1200 and settings["startup_timeout"] == 120
    assert settings["single_use_containers"] and settings["max_containers"] == 1
    payload = tmp_path / "calibrate.pickle"
    payload.write_bytes(serialize_data_format(function, api_pb2.DATA_FORMAT_PICKLE))
    cold = subprocess.run([sys.executable, "-I", "-c", """
import importlib.abc
import sys
from pathlib import Path
from modal._serialization import deserialize_data_format
from modal_proto import api_pb2
class NoProject(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'stpd', 'spireagent'}:
            raise ModuleNotFoundError('project absent from injected interpreter')
sys.meta_path.insert(0, NoProject())
function = deserialize_data_format(Path(sys.argv[1]).read_bytes(),
                                   api_pb2.DATA_FORMAT_PICKLE, None)
assert callable(function) and function.__name__ == 'calibrate'
print('cold_calibration_decoded')
""", str(payload)], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert cold.returncode == 0, cold.stderr
    assert cold.stdout.strip() == "cold_calibration_decoded"
    invoked = []

    def child(command, **options):
        invoked.append((command, options))
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {"state": "completed", "performance_id": "c" * 64}))

    monkeypatch.setenv("STPD_IMAGE_PROFILE", "worker")
    monkeypatch.setattr(subprocess, "run", child)
    assert function("b" * 64, {"variants": [], "profile": False}, "d" * 40, "e" * 64) == {
        "state": "completed", "performance_id": "c" * 64}
    command, options = invoked[0]
    assert command[:3] == ["/opt/stpd/python/.venv/bin/python", "-m", "stpd.p1.calibration"]
    assert command[command.index("--expected-source") + 1] == "d" * 40
    assert command[command.index("--expected-lock") + 1] == "e" * 64
    assert options["env"]["CUBLAS_WORKSPACE_CONFIG"] == ":4096:8"
    assert options["timeout"] == 1190


def test_receipt_short_write_preserves_original_App_without_respawn(tmp_path, monkeypatch):
    """A spawn can succeed while receipt publication fails; original App survives."""
    import importlib.metadata
    from contextlib import nullcontext

    import pytest

    import spireagent.source

    owner = runpy.run_path(str(Path(__file__).parents[1]
                              / "deploy/cloud-worker/calibrate_p1.py"))
    receipt = tmp_path / "operation.json"
    plan = tmp_path / "plan.json"
    from stpd.p1.execution import MStdExecution
    plan.write_text(json.dumps({"variants": [MStdExecution().to_dict()], "profile": False}))
    calls = []

    def spawn(*args):
        calls.append(args)
        return SimpleNamespace(object_id="fc-exact-call", get=lambda **kw: pytest.fail(
            "must not wait after receipt publication failed"))

    class App:
        app_id = "ap-exact-original"

        def __init__(self, name):
            pass

        def function(self, **options):
            return lambda function: SimpleNamespace(spawn=spawn)

        def run(self, **options):
            return nullcontext()

    sdk = SimpleNamespace(App=App, enable_output=nullcontext,
                          Image=SimpleNamespace(from_id=lambda identity: identity),
                          Secret=SimpleNamespace(from_name=lambda name: name))
    monkeypatch.setitem(sys.modules, "modal", sdk)
    producer = SimpleNamespace(source_revision="a" * 40, uv_lock_sha256="b" * 64,
                               to_dict=lambda: {"source_revision": "a" * 40,
                                                "uv_lock_sha256": "b" * 64})
    monkeypatch.setattr(spireagent.source, "source_identity", lambda root: producer)
    monkeypatch.setattr(importlib.metadata, "version", lambda package: "1.5.5")
    original_dump = json.dump

    def short_write(value, stream, **options):
        if isinstance(value, dict) and "call_id" in value:
            stream.write('{"partial_write"')
            raise OSError("simulated full device")
        return original_dump(value, stream, **options)

    monkeypatch.setattr(json, "dump", short_write)
    monkeypatch.setattr(sys, "argv", ["calibrate", "--image", "im-" + "a" * 22,
                                     "--input", "c" * 64, "--plan", str(plan),
                                     "--receipt", str(receipt)])
    with pytest.raises(OSError, match="simulated full device"):
        owner["main"]()
    assert len(calls) == 1
    saved = json.loads(receipt.read_bytes())
    assert saved["app_id"] == "ap-exact-original"
    assert saved["state"] == "offered"
    assert not list(tmp_path.glob(".pending-*"))
