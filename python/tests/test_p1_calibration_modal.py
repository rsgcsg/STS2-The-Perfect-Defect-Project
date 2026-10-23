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
