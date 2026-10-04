from __future__ import annotations

import hashlib
import io
import os
import runpy
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.cloud_jobs import public_m2_remote_worker as worker
from stpd.cloud_jobs.public_m2_modal import (
    PublicM2ModalBinding,
    PublicM2ModalResources,
    _decode_modal_response,
    encode_modal_response,
)

ENTRY = Path(__file__).resolve().parents[1] / "deploy/cloud-worker/public_m2_update_modal.py"


def _runtime() -> dict[str, Any]:
    return {
        "schema": worker.RUNTIME_RECEIPT_SCHEMA,
        "torch": "2.7.1+cu128", "python": "3.11.9", "platform": "Linux-test",
        "default_dtype": "torch.float32", "cpu_threads": 2,
        "implementation_sha256": "a" * 64,
        "cuda_available": True, "gpu_name": "NVIDIA L4",
        "gpu_compute_capability": [8, 9], "torch_cuda": "12.8",
        "cudnn_version": 90000, "allow_tf32_matmul": False,
        "allow_tf32_cudnn": True, "float32_matmul_precision": "highest",
        "deterministic_algorithms": False, "cudnn_deterministic": False,
        "cudnn_benchmark": False,
    }


def _env(monkeypatch: pytest.MonkeyPatch) -> tuple[bytes, PublicM2ModalResources]:
    request = b"opaque-remote-request"
    plan = PublicM2ModalResources("L4", 2.0, 8192, 4.0, 12288, 900, 120)
    receipt = json_bytes(_runtime())
    producer = Producer("repo", "b" * 40, "c" * 64)
    values = {
        "STPD_PUBLIC_M2_APP_NAME": "stpd-public-m2-" + "d" * 32,
        "STPD_PUBLIC_M2_ATTEMPT_ID": "d" * 32,
        "STPD_PUBLIC_M2_REQUEST_SHA256": hashlib.sha256(request).hexdigest(),
        "STPD_PUBLIC_M2_IMAGE_ID": "im-one",
        "STPD_PUBLIC_M2_RUNTIME_RECEIPT_SHA256": hashlib.sha256(receipt).hexdigest(),
        "STPD_PUBLIC_M2_RUNTIME_RECEIPT": receipt.decode(),
        "STPD_PUBLIC_M2_RESOURCE_PLAN": plan.to_bytes().decode(),
        "STPD_PUBLIC_M2_RESOURCE_PLAN_SHA256": plan.plan_sha256,
        "STPD_PUBLIC_M2_PRODUCER": json_bytes(producer.to_dict()).decode(),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        monkeypatch.setenv(key, "2")
    return request, plan


def _binding(request: bytes, plan: PublicM2ModalResources) -> PublicM2ModalBinding:
    return PublicM2ModalBinding(
        Producer("repo", "b" * 40, "c" * 64), hashlib.sha256(request).hexdigest(),
        "im-one", hashlib.sha256(json_bytes(_runtime())).hexdigest(), plan,
    )


def _load_entry(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], dict[str, Any]]:
    observed: dict[str, Any] = {}

    class FakeApp:
        def __init__(self, name: str) -> None:
            observed["app_name"] = name

        def function(self, **kwargs: Any) -> Any:
            observed["function_options"] = kwargs
            return lambda function: function

    class FakeImage:
        @staticmethod
        def from_id(identity: str) -> str:
            observed["image_id"] = identity
            return identity

    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(App=FakeApp, Image=FakeImage))
    return runpy.run_path(str(ENTRY), run_name="_fake_modal_entry"), observed


def test_entry_declares_explicit_resources_and_uses_locked_venv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, plan = _env(monkeypatch)
    namespace, observed = _load_entry(monkeypatch)
    options = observed["function_options"]
    assert observed["app_name"] == "stpd-public-m2-" + "d" * 32
    assert observed["image_id"] == "im-one"
    assert options["gpu"] == "L4" and options["cpu"] == (2.0, 4.0)
    assert options["memory"] == (8192, 12288) and options["timeout"] == 900
    assert options["startup_timeout"] == 120
    assert options["scaledown_window"] == plan.scaledown_seconds
    assert options["retries"] == 0 and options["max_containers"] == 1
    assert options["max_inputs"] == 1 and options["single_use_containers"] is True
    assert options["serialized"] is True and options["include_source"] is False
    assert options["env"]["STPD_PUBLIC_M2_RUNTIME_RECEIPT_SHA256"] == (
        hashlib.sha256(json_bytes(_runtime())).hexdigest())

    calls: list[dict[str, Any]] = []
    binding = _binding(request, plan)

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        calls.append({"args": args, **kwargs})
        return SimpleNamespace(returncode=0,
                               stdout=encode_modal_response(b"opaque-result", binding),
                               stderr=b"")

    monkeypatch.setattr(namespace["public_m2_remote"].__globals__["subprocess"],
                        "run", fake_run)
    monkeypatch.setenv("PYTHONPATH", "/host/unsafe")
    framed = namespace["public_m2_remote"](request)
    assert _decode_modal_response(framed, binding) == b"opaque-result"
    assert len(calls) == 1
    assert calls[0]["args"] == [
        "/opt/stpd/python/.venv/bin/python", "-m",
        "stpd.cloud_jobs.public_m2_remote_worker",
    ]
    assert calls[0]["cwd"] == "/opt/stpd/python"
    assert calls[0]["timeout"] == 900 and calls[0]["input"] == request
    assert "PYTHONPATH" not in calls[0]["env"]


def test_entry_rejects_bad_plan_or_request_before_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, _ = _env(monkeypatch)
    monkeypatch.setenv("STPD_PUBLIC_M2_RESOURCE_PLAN_SHA256", "f" * 64)
    with pytest.raises(ValueError, match="resource_plan_digest_mismatch"):
        _load_entry(monkeypatch)
    _env(monkeypatch)
    namespace, _ = _load_entry(monkeypatch)
    with pytest.raises(ValueError, match="request_identity_or_size_mismatch"):
        namespace["public_m2_remote"](request + b"changed")
    monkeypatch.setenv("STPD_PUBLIC_M2_IMAGE_ID", "im-other")
    with pytest.raises(RuntimeError, match="deployed_environment_identity_mismatch"):
        namespace["public_m2_remote"](request)


def test_serialized_wrapper_runs_without_importing_stpd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    cloudpickle = pytest.importorskip("modal._vendor.cloudpickle")
    _env(monkeypatch)
    namespace, _ = _load_entry(monkeypatch)
    function = namespace["public_m2_remote"]
    assert not {"_PLAN", "_BINDING", "encode_modal_response"} & set(function.__code__.co_names)
    serialized = cloudpickle.dumps(function)
    isolated = r'''
import importlib.abc
import sys
class BlockStpd(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname == "stpd" or fullname.startswith("stpd.")
                or fullname == "spireagent" or fullname.startswith("spireagent.")):
            raise ImportError("STPD package unavailable in Modal injected Python")
sys.meta_path.insert(0, BlockStpd())
from modal._vendor import cloudpickle
function = cloudpickle.loads(sys.stdin.buffer.read())
try:
    function(b"wrong-request")
except ValueError as error:
    if str(error) != "request_identity_or_size_mismatch":
        raise
else:
    raise AssertionError("wrong request unexpectedly accepted")
from types import SimpleNamespace
function.__globals__["subprocess"].run = lambda *args, **kwargs: SimpleNamespace(
    returncode=0, stdout=b"framed-response")
if function(b"opaque-remote-request") != b"framed-response":
    raise AssertionError("stdlib wrapper did not reach subprocess result path")
sys.stdout.write("serialized-stdlib-only")
'''
    completed = subprocess.run(
        [sys.executable, "-I", "-c", isolated], input=serialized, capture_output=True,
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": ""}, timeout=10, check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    assert completed.stdout == b"serialized-stdlib-only"


def test_wrapper_child_runtime_binding_through_locked_stdio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, plan = _env(monkeypatch)
    namespace, _ = _load_entry(monkeypatch)
    producer = Producer("repo", "b" * 40, "c" * 64)
    runtime = _runtime()
    remote = ModuleType("stpd.workers.public_m2_remote")
    executions: list[bytes] = []

    def execute(raw: bytes, *, request_sha256: str) -> bytes:
        assert request_sha256 == hashlib.sha256(request).hexdigest()
        executions.append(raw)
        return b"typed-result-wire"

    remote.__dict__["execute_public_m2_remote_request"] = execute
    monkeypatch.setitem(sys.modules, remote.__name__, remote)
    monkeypatch.setattr(worker, "_runtime_source_identity", lambda: producer)
    monkeypatch.setattr(worker, "read_runtime_evidence", lambda: runtime)

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        stdout = io.BytesIO()
        with monkeypatch.context() as local:
            for key, value in kwargs["env"].items():
                local.setenv(key, value)
            local.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(kwargs["input"])))
            local.setattr(sys, "stdout", SimpleNamespace(buffer=stdout))
            local.setattr(sys, "stderr", io.StringIO())
            status = worker._worker_main()
        return SimpleNamespace(returncode=status, stdout=stdout.getvalue(), stderr=b"")

    monkeypatch.setattr(namespace["public_m2_remote"].__globals__["subprocess"],
                        "run", fake_run)
    framed = namespace["public_m2_remote"](request)
    assert _decode_modal_response(framed, _binding(request, plan)) == b"typed-result-wire"
    assert executions == [request]
    monkeypatch.setattr(worker, "read_runtime_evidence",
                        lambda: {**runtime, "gpu_name": "other"})
    with pytest.raises(RuntimeError, match="locked_worker_failed"):
        namespace["public_m2_remote"](request)
    assert executions == [request]


def test_worker_checks_source_runtime_before_opaque_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, plan = _env(monkeypatch)
    producer = Producer("repo", "b" * 40, "c" * 64)
    runtime = _runtime()
    assert worker.decode_runtime_evidence(json_bytes(runtime)) == runtime
    remote = ModuleType("stpd.workers.public_m2_remote")
    calls: list[bytes] = []

    def execute(raw: bytes, *, request_sha256: str) -> bytes:
        calls.append(raw)
        assert request_sha256 == hashlib.sha256(request).hexdigest()
        return b"opaque-result"

    remote.execute_public_m2_remote_request = execute  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, remote.__name__, remote)
    monkeypatch.setattr(worker, "_runtime_source_identity", lambda: producer)
    monkeypatch.setattr(worker, "read_runtime_evidence", lambda: runtime)
    request_sha256 = hashlib.sha256(request).hexdigest()
    assert worker._execute_once(
        request, request_sha256=request_sha256, producer=producer,
        resources=plan, expected_runtime=runtime,
    ) == b"opaque-result"
    assert calls == [request]
    with pytest.raises(BoundaryError) as error:
        worker._execute_once(
            request, request_sha256=request_sha256,
            producer=replace_producer(producer), resources=plan,
            expected_runtime=runtime,
        )
    assert error.value.code == "image_source_identity_mismatch"
    monkeypatch.setattr(worker, "read_runtime_evidence", lambda: {**runtime, "gpu_name": "other"})
    with pytest.raises(BoundaryError) as error:
        worker._execute_once(
            request, request_sha256=request_sha256, producer=producer,
            resources=plan, expected_runtime=runtime,
        )
    assert error.value.code == "runtime_receipt_mismatch"
    assert calls == [request]


def replace_producer(producer: Producer) -> Producer:
    return Producer(producer.repository, "e" * 40, producer.uv_lock_sha256)
