"""Thin Modal wrapper for the fixed M0 image checkout.

Deploy this file only with one immutable Modal Image object ID and its expected
checkout/lock identity. ``include_source=False`` is essential: the function invokes
the installed worker from that image's own checkout rather than importing code from
the deploying workstation. Importing this module defines an App only; it does not
submit work or contact Modal compute.
"""

from __future__ import annotations

import os
import re
import subprocess

import modal

M0_MODAL_APP_NAME = "stpd-token-remote-update-m0"
M0_MODAL_FUNCTION_NAME = "token_remote_update"
MAX_M0_REQUEST_BYTES = 64 * 1024 * 1024
MAX_M0_RESULT_BYTES = 128 * 1024 * 1024
MAX_RESULT_FRAME_OVERHEAD_BYTES = 16 * 1024 + 64
WORKER_TIMEOUT_SECONDS = 900


def _required_identity() -> tuple[str, str, str]:
    image_id = os.environ.get("STPD_M0_MODAL_IMAGE_ID", "")
    revision = os.environ.get("STPD_M0_SOURCE_REVISION", "")
    lock_sha256 = os.environ.get("STPD_M0_UV_LOCK_SHA256", "")
    if re.fullmatch(r"im-[A-Za-z0-9_-]+", image_id) is None:
        raise ValueError("fixed_image_object_id_required")
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("fixed_source_revision_required")
    if re.fullmatch(r"[0-9a-f]{64}", lock_sha256) is None:
        raise ValueError("fixed_uv_lock_identity_required")
    return image_id, revision, lock_sha256


_IMAGE_OBJECT_ID, _EXPECTED_SOURCE_REVISION, _EXPECTED_LOCK_SHA256 = _required_identity()
_IMAGE = modal.Image.from_id(_IMAGE_OBJECT_ID)
_APP = modal.App(M0_MODAL_APP_NAME)


@_APP.function(
    image=_IMAGE,
    name=M0_MODAL_FUNCTION_NAME,
    gpu="L4",
    cpu=2.0,
    memory=8_192,
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
        "STPD_M0_MODAL_IMAGE_ID": _IMAGE_OBJECT_ID,
        "STPD_M0_SOURCE_REVISION": _EXPECTED_SOURCE_REVISION,
        "STPD_M0_UV_LOCK_SHA256": _EXPECTED_LOCK_SHA256,
        "OMP_NUM_THREADS": "2",
        "MKL_NUM_THREADS": "2",
    },
)
def token_remote_update(request_bytes: bytes) -> bytes:
    """Invoke only the worker installed in the pinned image; return bounded bytes."""
    if (
        not isinstance(request_bytes, bytes)
        or not 1 <= len(request_bytes) <= MAX_M0_REQUEST_BYTES
    ):
        raise ValueError("request_size_limit")
    if (
        os.environ.get("STPD_M0_MODAL_IMAGE_ID") != _IMAGE_OBJECT_ID
        or os.environ.get("STPD_M0_SOURCE_REVISION") != _EXPECTED_SOURCE_REVISION
        or os.environ.get("STPD_M0_UV_LOCK_SHA256") != _EXPECTED_LOCK_SHA256
    ):
        raise RuntimeError("deployed_environment_identity_mismatch")

    worker_env = dict(os.environ)
    # The fixed checkout is the sole import source for the worker subprocess.
    worker_env.pop("PYTHONPATH", None)
    worker_env.pop("PYTHONHOME", None)
    worker_env.update(
        {
            "STPD_M0_MODAL_IMAGE_ID": _IMAGE_OBJECT_ID,
            "STPD_M0_SOURCE_REVISION": _EXPECTED_SOURCE_REVISION,
            "STPD_M0_UV_LOCK_SHA256": _EXPECTED_LOCK_SHA256,
        }
    )
    try:
        completed = subprocess.run(
            [
                "/opt/stpd/python/.venv/bin/python",
                "-m",
                "stpd.cloud_jobs.m0_modal",
            ],
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
        # Do not forward worker stderr: SDK diagnostics can contain signed URLs.
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
