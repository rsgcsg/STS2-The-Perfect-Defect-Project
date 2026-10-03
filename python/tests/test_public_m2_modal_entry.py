from __future__ import annotations

import hashlib
import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.cloud_jobs import public_m2_remote_worker as worker
from stpd.cloud_jobs.public_m2_modal import PublicM2ModalResources

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
    plan = PublicM2ModalResources("L4", 2.0, 8192, 900, 120)
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
    return request, plan


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
    assert options["gpu"] == "L4" and options["cpu"] == 2.0
    assert options["memory"] == 8192 and options["timeout"] == 900
    assert options["startup_timeout"] == 120
    assert options["scaledown_window"] == plan.scaledown_seconds
    assert options["retries"] == 0 and options["max_containers"] == 1
    assert options["max_inputs"] == 1 and options["single_use_containers"] is True
    assert options["serialized"] is True and options["include_source"] is False
    assert options["env"]["STPD_PUBLIC_M2_RUNTIME_RECEIPT_SHA256"] == (
        hashlib.sha256(json_bytes(_runtime())).hexdigest())

    calls: list[dict[str, Any]] = []

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        calls.append({"args": args, **kwargs})
        return SimpleNamespace(returncode=0, stdout=b"opaque-result", stderr=b"")

    monkeypatch.setattr(namespace["public_m2_remote"].__globals__["subprocess"],
                        "run", fake_run)
    monkeypatch.setenv("PYTHONPATH", "/host/unsafe")
    assert namespace["public_m2_remote"](request) == b"opaque-result"
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
