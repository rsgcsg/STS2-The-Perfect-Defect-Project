"""Locked-image stdin/stdout entry for one public M2 remote attempt.

`read_runtime_evidence` is also the exact GPU qualification probe observation.
The controller must obtain and pin that observation before reserving a run;
neither the Modal wrapper nor this worker creates qualification by assertion.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes, object_fields
from spireagent.source import source_identity

from .public_m2_modal import (
    MAX_PUBLIC_M2_REQUEST_BYTES,
    MAX_PUBLIC_M2_RESULT_BYTES,
    PublicM2ModalResources,
)

RUNTIME_RECEIPT_SCHEMA = "stpd/public-m2-modal-runtime-v1"
_RUNTIME_FIELDS = frozenset({
    "schema", "torch", "python", "platform", "default_dtype", "cpu_threads",
    "implementation_sha256", "cuda_available", "gpu_name", "gpu_compute_capability",
    "torch_cuda", "cudnn_version", "allow_tf32_matmul", "allow_tf32_cudnn",
    "float32_matmul_precision", "deterministic_algorithms", "cudnn_deterministic",
    "cudnn_benchmark",
})
_IMAGE_ROOT = Path("/opt/stpd/python")


def read_runtime_evidence() -> dict[str, Any]:
    """Observe the engine's six runtime fields and execution-relevant GPU flags."""
    import torch

    from stpd.workers.public_m2_engine import _implementation_digest

    cuda = bool(torch.cuda.is_available())
    capability = torch.cuda.get_device_capability(0) if cuda else None
    return {
        "schema": RUNTIME_RECEIPT_SCHEMA,
        "torch": str(torch.__version__),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "default_dtype": str(torch.get_default_dtype()),
        "cpu_threads": torch.get_num_threads(),
        "implementation_sha256": _implementation_digest(),
        "cuda_available": cuda,
        "gpu_name": torch.cuda.get_device_name(0) if cuda else None,
        "gpu_compute_capability": list(capability) if capability is not None else None,
        "torch_cuda": str(torch.version.cuda) if torch.version.cuda is not None else None,
        "cudnn_version": torch.backends.cudnn.version(),
        "allow_tf32_matmul": bool(torch.backends.cuda.matmul.allow_tf32),
        "allow_tf32_cudnn": bool(torch.backends.cudnn.allow_tf32),
        "float32_matmul_precision": torch.get_float32_matmul_precision(),
        "deterministic_algorithms": bool(torch.are_deterministic_algorithms_enabled()),
        "cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
        "cudnn_benchmark": bool(torch.backends.cudnn.benchmark),
    }


def decode_runtime_evidence(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 8192:
        raise BoundaryError("public_m2_modal_worker", "runtime_receipt_size_limit")
    obj = object_fields(decode_json(raw), set(_RUNTIME_FIELDS),
                        "public_m2_modal_worker.runtime")
    if json_bytes(obj) != raw or obj["schema"] != RUNTIME_RECEIPT_SCHEMA:
        raise BoundaryError("public_m2_modal_worker", "noncanonical_runtime_receipt")
    text_fields = ("torch", "python", "platform", "default_dtype",
                   "implementation_sha256", "float32_matmul_precision")
    if any(not isinstance(obj[key], str) or not obj[key] for key in text_fields):
        raise BoundaryError("public_m2_modal_worker", "runtime_receipt_field_type")
    digest(obj["implementation_sha256"], "public_m2_modal_worker.implementation_sha256")
    bool_fields = ("cuda_available", "allow_tf32_matmul", "allow_tf32_cudnn",
                   "deterministic_algorithms", "cudnn_deterministic", "cudnn_benchmark")
    if any(type(obj[key]) is not bool for key in bool_fields):
        raise BoundaryError("public_m2_modal_worker", "runtime_receipt_field_type")
    if type(obj["cpu_threads"]) is not int or not 1 <= obj["cpu_threads"] <= 256:
        raise BoundaryError("public_m2_modal_worker", "runtime_receipt_field_type")
    if obj["torch_cuda"] is not None and not isinstance(obj["torch_cuda"], str):
        raise BoundaryError("public_m2_modal_worker", "runtime_receipt_field_type")
    if (obj["cudnn_version"] is not None
            and (type(obj["cudnn_version"]) is not int or obj["cudnn_version"] < 0)):
        raise BoundaryError("public_m2_modal_worker", "runtime_receipt_field_type")
    if obj["cuda_available"]:
        cap = obj["gpu_compute_capability"]
        if (not isinstance(obj["gpu_name"], str) or not obj["gpu_name"]
                or not isinstance(cap, list) or len(cap) != 2
                or any(type(part) is not int or part < 0 for part in cap)):
            raise BoundaryError("public_m2_modal_worker", "runtime_receipt_gpu_fields")
    elif obj["gpu_name"] is not None or obj["gpu_compute_capability"] is not None:
        raise BoundaryError("public_m2_modal_worker", "runtime_receipt_gpu_fields")
    return obj


def _runtime_source_identity() -> Producer:
    root = Path(__file__).resolve().parents[2]
    if root != _IMAGE_ROOT.resolve():
        raise BoundaryError("public_m2_modal_worker", "fixed_image_checkout_required")
    return source_identity(root)


def _execute_once(
    request_bytes: bytes, *, request_sha256: str, producer: Producer,
    resources: PublicM2ModalResources, expected_runtime: dict[str, Any],
) -> bytes:
    if (not isinstance(request_bytes, bytes)
            or not 1 <= len(request_bytes) <= MAX_PUBLIC_M2_REQUEST_BYTES
            or hashlib.sha256(request_bytes).hexdigest() != request_sha256):
        raise BoundaryError("public_m2_modal_worker", "request_identity_or_size_mismatch")
    if _runtime_source_identity() != producer:
        raise BoundaryError("public_m2_modal_worker", "image_source_identity_mismatch")
    actual_runtime = read_runtime_evidence()
    if (actual_runtime != expected_runtime
            or resources.gpu != "none" and not actual_runtime["cuda_available"]):
        raise BoundaryError("public_m2_modal_worker", "runtime_receipt_mismatch")
    remote = importlib.import_module("stpd.workers.public_m2_remote")
    result = remote.execute_public_m2_remote_request(
        request_bytes, request_sha256=request_sha256)
    if not isinstance(result, bytes) or not 1 <= len(result) <= MAX_PUBLIC_M2_RESULT_BYTES:
        raise BoundaryError("public_m2_modal_worker", "result_size_limit")
    return result


def _worker_main() -> int:
    try:
        request_sha256 = digest(os.environ.get("STPD_PUBLIC_M2_REQUEST_SHA256", ""),
                                "public_m2_modal_worker.request_sha256")
        attempt_id = os.environ.get("STPD_PUBLIC_M2_ATTEMPT_ID", "")
        image_id = os.environ.get("STPD_PUBLIC_M2_IMAGE_ID", "")
        if (re.fullmatch(r"[0-9a-f]{32}", attempt_id) is None
                or re.fullmatch(r"im-[A-Za-z0-9_-]+", image_id) is None
                or os.environ.get("STPD_PUBLIC_M2_APP_NAME") !=
                "stpd-public-m2-" + attempt_id):
            raise BoundaryError("public_m2_modal_worker", "attempt_identity_mismatch")
        plan_raw = os.environ.get("STPD_PUBLIC_M2_RESOURCE_PLAN", "").encode("utf-8")
        resources = PublicM2ModalResources.from_bytes(plan_raw)
        if (resources.plan_sha256 != os.environ.get(
                "STPD_PUBLIC_M2_RESOURCE_PLAN_SHA256")):
            raise BoundaryError("public_m2_modal_worker", "resource_plan_mismatch")
        producer_raw = os.environ.get("STPD_PUBLIC_M2_PRODUCER", "").encode("utf-8")
        if not 1 <= len(producer_raw) <= 4096:
            raise BoundaryError("public_m2_modal_worker", "producer_size_limit")
        producer = Producer.decode(decode_json(producer_raw))
        if json_bytes(producer.to_dict()) != producer_raw:
            raise BoundaryError("public_m2_modal_worker", "noncanonical_producer")
        runtime_raw = os.environ.get("STPD_PUBLIC_M2_RUNTIME_RECEIPT", "").encode("utf-8")
        runtime_sha = digest(os.environ.get("STPD_PUBLIC_M2_RUNTIME_RECEIPT_SHA256", ""),
                             "public_m2_modal_worker.runtime_receipt_sha256")
        if hashlib.sha256(runtime_raw).hexdigest() != runtime_sha:
            raise BoundaryError("public_m2_modal_worker", "runtime_receipt_digest_mismatch")
        expected_runtime = decode_runtime_evidence(runtime_raw)
        threads = str(expected_runtime["cpu_threads"])
        if any(os.environ.get(key) != threads for key in (
                "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")):
            raise BoundaryError("public_m2_modal_worker", "thread_environment_mismatch")
        request = sys.stdin.buffer.read(MAX_PUBLIC_M2_REQUEST_BYTES + 1)
        result = _execute_once(
            request, request_sha256=request_sha256, producer=producer,
            resources=resources, expected_runtime=expected_runtime,
        )
        sys.stdout.buffer.write(result)
        sys.stdout.buffer.flush()
    except Exception:
        print("public_m2_remote_worker_failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main())
