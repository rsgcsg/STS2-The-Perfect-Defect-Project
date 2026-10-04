"""Per-attempt Modal entry for opaque public M2 remote request bytes.

The controller first reserves cost, persists the attempt and prepares an exact
image/deployment target. Importing this file only declares a function; no call
is spawned. The pinned image contains the public M2 worker implementation.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from typing import cast

import modal

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import decode_json, json_bytes
from stpd.cloud_jobs.public_m2_modal import (
    MAX_PUBLIC_M2_REQUEST_BYTES,
    MAX_PUBLIC_M2_TRANSPORT_BYTES,
    PublicM2ModalResources,
)
from stpd.cloud_jobs.public_m2_remote_worker import decode_runtime_evidence

APP_PREFIX = "stpd-public-m2-"
FUNCTION_NAME = "public_m2_remote"
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE_ID = re.compile(r"im-[A-Za-z0-9_-]+\Z")


def _required(name: str, pattern: re.Pattern[str]) -> str:
    value = os.environ.get(name, "")
    if pattern.fullmatch(value) is None:
        raise ValueError("invalid_" + name.lower())
    return value


_ATTEMPT_ID = _required("STPD_PUBLIC_M2_ATTEMPT_ID", _HEX32)
_APP_NAME = os.environ.get("STPD_PUBLIC_M2_APP_NAME", "")
if _APP_NAME != APP_PREFIX + _ATTEMPT_ID:
    raise ValueError("unique_attempt_app_name_required")
_REQUEST_SHA256 = _required("STPD_PUBLIC_M2_REQUEST_SHA256", _HEX64)
_IMAGE_OBJECT_ID = _required("STPD_PUBLIC_M2_IMAGE_ID", _IMAGE_ID)
_RUNTIME_RECEIPT_SHA256 = _required("STPD_PUBLIC_M2_RUNTIME_RECEIPT_SHA256", _HEX64)
_RUNTIME_RECEIPT_RAW = os.environ.get("STPD_PUBLIC_M2_RUNTIME_RECEIPT", "").encode("utf-8")
if not 1 <= len(_RUNTIME_RECEIPT_RAW) <= 8192:
    raise ValueError("runtime_receipt_size_limit")
if hashlib.sha256(_RUNTIME_RECEIPT_RAW).hexdigest() != _RUNTIME_RECEIPT_SHA256:
    raise ValueError("runtime_receipt_digest_mismatch")
_RUNTIME_RECEIPT = decode_runtime_evidence(_RUNTIME_RECEIPT_RAW)
_PLAN_RAW = os.environ.get("STPD_PUBLIC_M2_RESOURCE_PLAN", "").encode("utf-8")
_PLAN = PublicM2ModalResources.from_bytes(_PLAN_RAW)
_PLAN_SHA256 = _required("STPD_PUBLIC_M2_RESOURCE_PLAN_SHA256", _HEX64)
if _PLAN.plan_sha256 != _PLAN_SHA256:
    raise ValueError("resource_plan_digest_mismatch")
_PRODUCER_RAW = os.environ.get("STPD_PUBLIC_M2_PRODUCER", "").encode("utf-8")
if not 1 <= len(_PRODUCER_RAW) <= 4096:
    raise ValueError("producer_size_limit")
_PRODUCER = Producer.decode(decode_json(_PRODUCER_RAW))
if json_bytes(_PRODUCER.to_dict()) != _PRODUCER_RAW:
    raise ValueError("noncanonical_producer")

_PINNED_ENV = {
    "STPD_PUBLIC_M2_APP_NAME": _APP_NAME,
    "STPD_PUBLIC_M2_ATTEMPT_ID": _ATTEMPT_ID,
    "STPD_PUBLIC_M2_REQUEST_SHA256": _REQUEST_SHA256,
    "STPD_PUBLIC_M2_IMAGE_ID": _IMAGE_OBJECT_ID,
    "STPD_PUBLIC_M2_RUNTIME_RECEIPT_SHA256": _RUNTIME_RECEIPT_SHA256,
    "STPD_PUBLIC_M2_RUNTIME_RECEIPT": _RUNTIME_RECEIPT_RAW.decode("utf-8"),
    "STPD_PUBLIC_M2_RESOURCE_PLAN": _PLAN_RAW.decode("utf-8"),
    "STPD_PUBLIC_M2_RESOURCE_PLAN_SHA256": _PLAN_SHA256,
    "STPD_PUBLIC_M2_PRODUCER": _PRODUCER_RAW.decode("utf-8"),
    # Qualification probes and execution must use the same explicit thread
    # environment before Torch loads. Other numerical flags are observed only.
    "OMP_NUM_THREADS": str(_RUNTIME_RECEIPT["cpu_threads"]),
    "MKL_NUM_THREADS": str(_RUNTIME_RECEIPT["cpu_threads"]),
    "OPENBLAS_NUM_THREADS": str(_RUNTIME_RECEIPT["cpu_threads"]),
}
_DEADLINE_SECONDS = _PLAN.deadline_seconds

_APP = modal.App(_APP_NAME)
_IMAGE = modal.Image.from_id(_IMAGE_OBJECT_ID)


@_APP.function(
    image=_IMAGE,
    name=FUNCTION_NAME,
    gpu=None if _PLAN.gpu == "none" else _PLAN.gpu,
    cpu=(_PLAN.cpu, _PLAN.cpu_limit),
    memory=(_PLAN.memory_mib, _PLAN.memory_limit_mib),
    timeout=_PLAN.deadline_seconds,
    startup_timeout=_PLAN.startup_timeout_seconds,
    max_containers=1,
    max_inputs=1,
    min_containers=0,
    scaledown_window=_PLAN.scaledown_seconds,
    single_use_containers=True,
    retries=0,
    serialized=True,
    include_source=False,
    env=cast(dict[str, str | None], _PINNED_ENV),
)
def public_m2_remote(request_bytes: bytes) -> bytes:
    """Run the image's locked venv child once; return only opaque result bytes."""
    if any(os.environ.get(key) != value for key, value in _PINNED_ENV.items()):
        raise RuntimeError("deployed_environment_identity_mismatch")
    if (not isinstance(request_bytes, bytes)
            or not 1 <= len(request_bytes) <= MAX_PUBLIC_M2_REQUEST_BYTES
            or hashlib.sha256(request_bytes).hexdigest() != _REQUEST_SHA256):
        raise ValueError("request_identity_or_size_mismatch")
    worker_env = dict(os.environ)
    worker_env.pop("PYTHONPATH", None)
    worker_env.pop("PYTHONHOME", None)
    worker_env.update(_PINNED_ENV)
    try:
        completed = subprocess.run(
            ["/opt/stpd/python/.venv/bin/python", "-m",
             "stpd.cloud_jobs.public_m2_remote_worker"],
            cwd="/opt/stpd/python", env=worker_env, input=request_bytes,
            capture_output=True, timeout=_DEADLINE_SECONDS, check=False,
        )
    except Exception:
        raise RuntimeError("locked_worker_failed") from None
    if completed.returncode != 0:
        raise RuntimeError("locked_worker_failed")
    result = completed.stdout
    if not isinstance(result, bytes) or not 1 <= len(result) <= MAX_PUBLIC_M2_TRANSPORT_BYTES:
        raise RuntimeError("result_size_limit")
    return result


app = _APP
