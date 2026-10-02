"""Single Modal entry for the train-only M0 remote update adapter.

Deploy only after the operator supplies one fixed Modal Image object ID and the exact
source/lock identity represented by that image. Importing this module defines an app;
it never submits a function or starts Modal compute.
"""

from __future__ import annotations

import os
import re

import modal

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, digest
from spireagent.source import REPOSITORY
from stpd.cloud_jobs.m0_modal import (
    M0_MODAL_APP_NAME,
    M0_MODAL_FUNCTION_NAME,
    execute_m0_request_bytes,
)


def _required_identity() -> tuple[str, Producer]:
    image_id = os.environ.get("STPD_M0_MODAL_IMAGE_ID", "")
    revision = os.environ.get("STPD_M0_SOURCE_REVISION", "")
    lock_sha256 = os.environ.get("STPD_M0_UV_LOCK_SHA256", "")
    if re.fullmatch(r"im-[A-Za-z0-9_-]+", image_id) is None:
        raise BoundaryError("modal_m0_deploy", "fixed_image_object_id_required")
    try:
        digest(revision, "modal_m0_deploy.source_revision", length=40)
        digest(lock_sha256, "modal_m0_deploy.uv_lock_sha256")
    except BoundaryError as error:
        raise BoundaryError("modal_m0_deploy", "fixed_source_and_lock_required") from error
    return image_id, Producer(REPOSITORY, revision, lock_sha256)


_IMAGE_OBJECT_ID, _EXPECTED_PRODUCER = _required_identity()
_IMAGE = modal.Image.from_id(_IMAGE_OBJECT_ID)
_APP = modal.App(M0_MODAL_APP_NAME)


@_APP.function(
    image=_IMAGE,
    name=M0_MODAL_FUNCTION_NAME,
    gpu="A10",
    cpu=4.0,
    memory=32_768,
    ephemeral_disk=20_480,
    timeout=3_600,
    startup_timeout=1_200,
    max_containers=1,
    max_inputs=1,
    min_containers=0,
    scaledown_window=60,
    single_use_containers=True,
    retries=0,
    env={
        "STPD_M0_MODAL_IMAGE_ID": _IMAGE_OBJECT_ID,
        "STPD_M0_SOURCE_REVISION": _EXPECTED_PRODUCER.source_revision,
        "STPD_M0_UV_LOCK_SHA256": _EXPECTED_PRODUCER.uv_lock_sha256,
        "OMP_NUM_THREADS": "4",
        "MKL_NUM_THREADS": "4",
    },
)
def token_remote_update(request_bytes: bytes) -> bytes:
    """Run one exact update and return a bounded result frame; never publish it."""
    image_id = os.environ.get("STPD_M0_MODAL_IMAGE_ID", "")
    revision = os.environ.get("STPD_M0_SOURCE_REVISION", "")
    lock_sha256 = os.environ.get("STPD_M0_UV_LOCK_SHA256", "")
    if (
        image_id != _IMAGE_OBJECT_ID
        or revision != _EXPECTED_PRODUCER.source_revision
        or lock_sha256 != _EXPECTED_PRODUCER.uv_lock_sha256
    ):
        raise BoundaryError("modal_m0", "deployed_environment_identity_mismatch")
    return execute_m0_request_bytes(
        request_bytes,
        expected_producer=_EXPECTED_PRODUCER,
        expected_image_object_id=_IMAGE_OBJECT_ID,
    )


app = _APP
