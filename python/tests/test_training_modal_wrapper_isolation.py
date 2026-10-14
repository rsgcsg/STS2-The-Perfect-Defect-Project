"""Real SDK serialization/isolated callback; substitute only locked-child process I/O."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from training_executor_fixtures import prepared_fixture

from spireagent.json_boundary import FrozenObject
from spireagent.source import source_identity
from stpd.structured_code_scope import ROOT

# -I ignores PYTHONPATH/cwd; -S prevents editable .pth processing. Only the
# installed SDK/dependencies are added, with no source/project import directory.
CONSUMER = r"""
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.append(sys.argv[1])
from modal._serialization import deserialize

root = Path(sys.argv[2])
mode = sys.argv[3]
assert importlib.util.find_spec("spireagent") is None
assert importlib.util.find_spec("stpd") is None
function = deserialize((root / "callback.pickle").read_bytes(), None)
assert "PreparedTrainingAttempt" not in function.__globals__
assert "_attempt" not in function.__globals__
if mode == "deserialize":
    print(json.dumps({"decoded": True, "no_project_imports": True, "calls": [],
                      "isolated": sys.flags.isolated, "no_site": sys.flags.no_site}))
    raise SystemExit(0)
prepared = (root / "prepared.json").read_bytes()
request = (root / "request.bytes").read_bytes()
projection = (root / "projection.bytes").read_bytes()
limits = json.loads(prepared)["limits"]
if mode == "prepared":
    prepared += b" "
elif mode == "request":
    request += b"different"
elif mode == "projection":
    projection += b"different"
elif mode == "empty":
    request = b""
elif mode == "request-size":
    request = b"x" * (limits["max_request_bytes"] + 1)
elif mode == "projection-size":
    projection = b"x" * (limits["max_projection_bytes"] + 1)
os.environ["STPD_IMAGE_PROFILE"] = "hub" if mode == "profile" else "worker"
os.environ.pop("CUBLAS_WORKSPACE_CONFIG", None)
calls = []

def child(argv, **kwargs):
    assert argv[:3] == ["/opt/stpd/python/.venv/bin/python", "-m",
                       "spireagent.training_transport_worker"]
    assert kwargs["cwd"] == "/opt/stpd/python"
    assert kwargs["timeout"] == limits["wall_seconds"]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["check"] is False and callable(kwargs["preexec_fn"])
    selected = {argv[i]: Path(argv[i + 1]) for i in range(3, len(argv), 2)}
    assert selected["--attempt"].read_bytes() == prepared
    assert selected["--request"].read_bytes() == request
    assert selected["--projection"].read_bytes() == projection
    if json.loads(prepared)["placement"]["device"] == "cuda:0":
        assert kwargs["env"]["CUBLAS_WORKSPACE_CONFIG"] == ":4096:8"
    else:
        assert "CUBLAS_WORKSPACE_CONFIG" not in kwargs["env"]
    calls.append("fixed-locked-child")
    if mode != "child-fail":
        value = b"bounded-candidate"
        if mode == "candidate-empty":
            value = b""
        elif mode == "candidate-size":
            value = b"x" * (limits["max_candidate_bytes"] + 1)
        elif mode == "candidate-link":
            target = selected["--result"].with_name("other.bytes")
            target.write_bytes(value)
            selected["--result"].symlink_to(target)
            return type("Exit", (), {"returncode": 0})()
        selected["--result"].write_bytes(value)
    return type("Exit", (), {"returncode": 1 if mode == "child-fail" else 0})()

import subprocess
subprocess.run = child
facts = {"decoded": True, "no_project_imports": True, "calls": calls,
         "isolated": sys.flags.isolated, "no_site": sys.flags.no_site}
try:
    result = function(prepared, request, projection)
    facts.update(returned=result == b"bounded-candidate")
except Exception as error:
    facts.update(returned=False, error_type=type(error).__name__, error=str(error))
print(json.dumps(facts, sort_keys=True))
"""


def serialize_actual_definition(tmp_path, monkeypatch, *, cuda=False, invalid_cublas=False):
    attempt, request, projection = prepared_fixture()
    attempt = replace(attempt, producer=source_identity(ROOT))
    if cuda:
        runtime = attempt.runtime.value()
        runtime.update(
            device="cuda:0", cublas_workspace_config="invalid" if invalid_cublas else ":4096:8"
        )
        attempt = replace(
            attempt,
            runtime=FrozenObject.of(runtime),
            placement=replace(attempt.placement, gpu="A10", device="cuda:0"),
        )
    monkeypatch.setenv("SPIREAGENT_PREPARED_TRAINING_ATTEMPT", attempt.to_bytes().decode())
    # Actual entry and local producer check, not a mocked source or SDK decorator.
    from modal.cli.import_refs import ImportRef, import_file_or_module

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, "training_attempt_modal", ModuleType("pending-local-import"))
    definition = import_file_or_module(
        ImportRef(
            str(ROOT / "deploy/cloud-worker/training_attempt_modal.py"),
            False,
            command="modal deploy",
        )
    )
    callback = definition.training_attempt
    (tmp_path / "callback.pickle").write_bytes(callback._get_source_info().serialized_function())
    (tmp_path / "prepared.json").write_bytes(attempt.to_bytes())
    (tmp_path / "request.bytes").write_bytes(request)
    (tmp_path / "projection.bytes").write_bytes(projection)


def consume(tmp_path, mode="positive"):
    # Derive SDK path from the installed package for Windows as well as POSIX.
    import modal

    site = Path(modal.__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", CONSUMER, str(site), str(tmp_path), mode],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("cuda", [False, True])
def test_actual_callback_deserializes_without_project_imports_on_control_os(
    tmp_path, monkeypatch, cuda
):
    serialize_actual_definition(tmp_path, monkeypatch, cuda=cuda)
    facts = consume(tmp_path, "deserialize")
    assert facts["decoded"] and facts["no_project_imports"] and facts["calls"] == []
    assert facts["isolated"] == facts["no_site"] == 1


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX wrapper resource import before Linux fork"
)
@pytest.mark.parametrize("cuda", [False, True])
def test_actual_callback_deserializes_and_uses_fixed_locked_child_without_project_imports(
    tmp_path, monkeypatch, cuda
):
    serialize_actual_definition(tmp_path, monkeypatch, cuda=cuda)
    facts = consume(tmp_path)
    assert facts["decoded"] and facts["no_project_imports"] and facts["returned"]
    assert facts["isolated"] == facts["no_site"] == 1
    assert facts["calls"] == ["fixed-locked-child"]


@pytest.mark.parametrize(
    "mode,code",
    [
        ("prepared", "prepared_attempt_or_worker_image_mismatch"),
        ("profile", "prepared_attempt_or_worker_image_mismatch"),
        ("request", "prepared_payload_mismatch"),
        ("projection", "prepared_payload_mismatch"),
        ("empty", "payload_size_limit"),
        ("request-size", "payload_size_limit"),
        ("projection-size", "payload_size_limit"),
    ],
)
def test_isolated_callback_rejects_wrong_original_binding_before_child(
    tmp_path, monkeypatch, mode, code
):
    serialize_actual_definition(tmp_path, monkeypatch)
    facts = consume(tmp_path, mode)
    assert not facts["returned"] and facts["error_type"] == "ValueError"
    assert facts["error"] == code and facts["calls"] == []


def test_isolated_cuda_callback_rejects_unqualified_cublas_before_child(tmp_path, monkeypatch):
    serialize_actual_definition(tmp_path, monkeypatch, cuda=True, invalid_cublas=True)
    facts = consume(tmp_path)
    assert not facts["returned"] and facts["error"] == "qualified_cublas_workspace_required"
    assert facts["calls"] == []


@pytest.mark.parametrize(
    "mode,code",
    [
        ("child-fail", "bounded_worker_failed_no_candidate"),
        ("candidate-link", "bounded_worker_failed_no_candidate"),
        ("candidate-empty", "candidate_size_limit"),
        ("candidate-size", "candidate_size_limit"),
    ],
)
@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX wrapper resource import before Linux fork"
)
def test_isolated_callback_retains_candidate_transport_guards(tmp_path, monkeypatch, mode, code):
    serialize_actual_definition(tmp_path, monkeypatch)
    facts = consume(tmp_path, mode)
    assert not facts["returned"] and facts["error_type"] == "RuntimeError"
    assert facts["error"] == code and facts["calls"] == ["fixed-locked-child"]
