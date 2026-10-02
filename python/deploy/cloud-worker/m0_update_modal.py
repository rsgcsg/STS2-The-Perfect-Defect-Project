"""Per-attempt Modal App for the fixed, train-only M0 token update.

The caller deploys this entry only after persisting a unique attempt spec. Importing
it validates identity and declares one bounded function; it never submits training.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess

import modal

from stpd.cloud_jobs.m0_modal import MAX_M0_REQUEST_BYTES, MAX_M0_RESULT_BYTES

M0_MODAL_APP_PREFIX = "stpd-m0-update-"
M0_MODAL_FUNCTION_NAME = "token_remote_update"
MAX_RESULT_FRAME_OVERHEAD_BYTES = 16 * 1024 + 64
WORKER_TIMEOUT_SECONDS = 900

_PLAN = {
    "gpu": "L4",
    "cpu": 2.0,
    "memory_mib": 8192,
    "function_timeout_seconds": 900,
    "startup_timeout_seconds": 120,
    "scaledown_seconds": 30,
    "max_containers": 1,
    "retries": 0,
}
_EXPECTED_PLAN_SHA256 = hashlib.sha256(
    (json.dumps(_PLAN, sort_keys=True, ensure_ascii=False, allow_nan=False,
                separators=(",", ":")) + "\n").encode("utf-8")
).hexdigest()


def _required_identity() -> dict[str, str | int]:
    identity: dict[str, str | int] = {
        "app_name": os.environ.get("STPD_M0_MODAL_APP_NAME", ""),
        "attempt_id": os.environ.get("STPD_M0_MODAL_ATTEMPT_ID", ""),
        "request_sha256": os.environ.get("STPD_M0_MODAL_REQUEST_SHA256", ""),
        "spec_sha256": os.environ.get("STPD_M0_MODAL_SPEC_SHA256", ""),
        "plan_sha256": os.environ.get("STPD_M0_MODAL_PLAN_SHA256", ""),
        "image_id": os.environ.get("STPD_M0_MODAL_IMAGE_ID", ""),
        "revision": os.environ.get("STPD_M0_SOURCE_REVISION", ""),
        "lock_sha256": os.environ.get("STPD_M0_UV_LOCK_SHA256", ""),
        "torch_version": os.environ.get("STPD_M0_TORCH_VERSION", ""),
    }
    try:
        identity["cpu_threads"] = int(os.environ.get("STPD_M0_CPU_THREADS", ""))
    except ValueError:
        raise ValueError("fixed_cpu_thread_count_required") from None
    attempt_id = str(identity["attempt_id"])
    if identity["app_name"] != f"{M0_MODAL_APP_PREFIX}{attempt_id}":
        raise ValueError("unique_attempt_app_name_required")
    checks = (
        ("attempt_id", r"[0-9a-f]{32}"),
        ("request_sha256", r"[0-9a-f]{64}"),
        ("spec_sha256", r"[0-9a-f]{64}"),
        ("plan_sha256", r"[0-9a-f]{64}"),
        ("image_id", r"im-[A-Za-z0-9_-]+"),
        ("revision", r"[0-9a-f]{40}"),
        ("lock_sha256", r"[0-9a-f]{64}"),
        ("torch_version", r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}"),
    )
    for name, pattern in checks:
        if re.fullmatch(pattern, str(identity[name])) is None:
            raise ValueError(f"fixed_{name}_required")
    if identity["plan_sha256"] != _EXPECTED_PLAN_SHA256:
        raise ValueError("unsupported_resource_plan")
    if type(identity["cpu_threads"]) is not int or not 1 <= identity["cpu_threads"] <= 256:
        raise ValueError("fixed_cpu_thread_count_required")
    return identity


_IDENTITY = _required_identity()
_APP_NAME = str(_IDENTITY["app_name"])
_IMAGE_OBJECT_ID = str(_IDENTITY["image_id"])
_IMAGE = modal.Image.from_id(_IMAGE_OBJECT_ID)
_APP = modal.App(_APP_NAME)


@_APP.function(
    image=_IMAGE,
    name=M0_MODAL_FUNCTION_NAME,
    gpu="L4",
    cpu=2.0,
    memory=8192,
    timeout=WORKER_TIMEOUT_SECONDS,
    startup_timeout=120,
    max_containers=1,
    max_inputs=1,
    min_containers=0,
    scaledown_window=30,
    single_use_containers=True,
    retries=0,
    serialized=True,
    include_source=False,
    env={
        "STPD_M0_MODAL_APP_NAME": _APP_NAME,
        "STPD_M0_MODAL_ATTEMPT_ID": _IDENTITY["attempt_id"],
        "STPD_M0_MODAL_REQUEST_SHA256": _IDENTITY["request_sha256"],
        "STPD_M0_MODAL_SPEC_SHA256": _IDENTITY["spec_sha256"],
        "STPD_M0_MODAL_PLAN_SHA256": _IDENTITY["plan_sha256"],
        "STPD_M0_MODAL_IMAGE_ID": _IMAGE_OBJECT_ID,
        "STPD_M0_SOURCE_REVISION": _IDENTITY["revision"],
        "STPD_M0_UV_LOCK_SHA256": _IDENTITY["lock_sha256"],
        "STPD_M0_TORCH_VERSION": _IDENTITY["torch_version"],
        "STPD_M0_CPU_THREADS": str(_IDENTITY["cpu_threads"]),
        "OMP_NUM_THREADS": str(_IDENTITY["cpu_threads"]),
        "MKL_NUM_THREADS": str(_IDENTITY["cpu_threads"]),
    },
)
def token_remote_update(request_bytes: bytes) -> bytes:
    """Invoke only the worker installed in the immutable pinned image."""
    if (
        not isinstance(request_bytes, bytes)
        or not 1 <= len(request_bytes) <= MAX_M0_REQUEST_BYTES
    ):
        raise ValueError("request_size_limit")
    expected_env = {
        "STPD_M0_MODAL_APP_NAME": _APP_NAME,
        "STPD_M0_MODAL_ATTEMPT_ID": _IDENTITY["attempt_id"],
        "STPD_M0_MODAL_REQUEST_SHA256": _IDENTITY["request_sha256"],
        "STPD_M0_MODAL_SPEC_SHA256": _IDENTITY["spec_sha256"],
        "STPD_M0_MODAL_PLAN_SHA256": _IDENTITY["plan_sha256"],
        "STPD_M0_MODAL_IMAGE_ID": _IMAGE_OBJECT_ID,
        "STPD_M0_SOURCE_REVISION": _IDENTITY["revision"],
        "STPD_M0_UV_LOCK_SHA256": _IDENTITY["lock_sha256"],
        "STPD_M0_TORCH_VERSION": _IDENTITY["torch_version"],
        "STPD_M0_CPU_THREADS": str(_IDENTITY["cpu_threads"]),
    }
    if any(os.environ.get(key) != str(value) for key, value in expected_env.items()):
        raise RuntimeError("deployed_environment_identity_mismatch")

    worker_env = dict(os.environ)
    worker_env.pop("PYTHONPATH", None)
    worker_env.pop("PYTHONHOME", None)
    worker_env.update(expected_env)
    try:
        completed = subprocess.run(
            ["/opt/stpd/python/.venv/bin/python", "-m", "stpd.cloud_jobs.m0_modal"],
            cwd="/opt/stpd/python",
            env=worker_env,
            input=request_bytes,
            capture_output=True,
            timeout=WORKER_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:
        raise RuntimeError("m0_worker_failed") from None
    if completed.returncode != 0:
        raise RuntimeError("m0_worker_failed")
    response = completed.stdout
    if (
        not isinstance(response, bytes)
        or not response
        or len(response) > MAX_M0_RESULT_BYTES + MAX_RESULT_FRAME_OVERHEAD_BYTES
    ):
        raise RuntimeError("m0_worker_response_size_limit")
    return response


app = _APP
