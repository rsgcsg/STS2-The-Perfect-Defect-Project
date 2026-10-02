"""One explicit, store-owned local engineering training operation.

The operation file is a conservative journal, not a worker queue. An unfinished
run is never launched again implicitly; only an exact completed marker proves
the child finished. The OS lock spans the parent-owned child lifecycle.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from contextlib import AbstractContextManager, suppress
from pathlib import Path
from typing import Any, cast

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes
from spireagent.source import source_identity
from spireagent.storage.blobs import safe_key
from spireagent.storage.replaceable_file import (
    has_unresolved_replacement,
    read_replaceable_bytes,
    write_replaceable_json,
)
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ROOT, ProjectConfig
from spireagent.workbench.developer_server import instance_lock
from spireagent.workbench.local_curation import LocalCurationOwner
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_model_dependencies import require_local_models
from spireagent.workbench.memory_recipe import (
    M2_K1_RECIPE,
    MEMORY_RECIPES,
    V2_MEMORY_RECIPES,
    recipe_for_memory_config,
)
from spireagent.workbench.research_process import private_child as _private_child
from stpd.cloud_jobs.m0_modal import MAX_M0_REQUEST_BYTES

SCHEMA = "stpd/local-training-operation-v1"
SCHEMA_V2 = "stpd/local-training-operation-v2"
SCHEMA_V3 = "stpd/local-training-operation-v3"
SCHEMA_V4 = "stpd/local-training-operation-v4"
DEFAULT_RECIPE = "stage1a.dsimple.s.v1"
PUBLIC_M0_RECIPE = "stage1a.dsimple.light-action.m0.s.v1"
MEMORY_RECIPE = M2_K1_RECIPE  # Preserve the existing recipe constant for callers.
OPERATION_FILE = "local-training-operation.json"
LOCK_FILE = ".local-training.lock"
PARENT_FAILURE_LOG_BYTES = 64 * 1024
IDS = ("allocation_id", "view_id", "input_id", "run_id", "checkpoint_id",
       "result_id", "model_id", "evaluation_id")
PREVIOUS_COMPLETED_IDS = ("operation_id", "dataset_id", "result_id", "model_id",
                          "evaluation_id")
LEGACY_STAGES = frozenset({"reserving", "allocating", "public_view", "tokenizing",
                           "preparing_run", "training", "verifying_result", "completed"})
REMOTE_STAGES = frozenset({"remote_submit_intent", "remote_running", "remote_cancelling",
                           "remote_unknown", "remote_paused", "remote_cancelled",
                           "remote_stopping", "remote_acceptance", "remote_failed",
                           "completed"})
STAGES = LEGACY_STAGES | REMOTE_STAGES
REMOTE_STATUSES = frozenset({"pending", "cancelling", "interrupted_unknown", "paused",
                             "cancelled", "completed", "failed"})
REMOTE_ATTEMPT_PHASES = frozenset({"submit_intent", "running", "cancelling", "stopping",
                                  "terminal", "preflight_failed"})
REMOTE_APP_PHASES = frozenset({"not_prepared", "prepare_intent", "app_pinned", "submitted"})
REMOTE_TERMINAL_STATES = frozenset({"paused", "cancelled", "completed", "failed"})
REMOTE_REQUEST_PREFIX = "local-training/remote-requests/"
REMOTE_REQUEST_CHUNK_PREFIX = "local-training/remote-request-chunks/"
REMOTE_REQUEST_SCHEMA = "stpd/token-remote-update-request-v1"
REMOTE_RESULT_PREFIX = "local-training/remote-results/"
REMOTE_RESULT_CHUNK_PREFIX = "local-training/remote-result-chunks/"
REMOTE_CONTROL_PREFIX = "local-training/remote-control/"
REMOTE_CHUNK_BYTES = 8 * 1024 * 1024
REMOTE_MAX_REQUEST_BYTES = MAX_M0_REQUEST_BYTES
REMOTE_MAX_RESULT_BYTES = 128 * 1024 * 1024
REMOTE_MAX_CONTROL_BYTES = 128 * 1024
REMOTE_MAX_TIMEOUT_SECONDS = 900
REMOTE_MAX_STARTUP_TIMEOUT_SECONDS = 120
REMOTE_MAX_TOTAL_STEPS = 100_000


def _remote_control_key(kind: str, sha256: str) -> str:
    if kind not in {"attempt-spec", "app-ref", "call-handle", "runtime-evidence"}:
        raise ValueError("remote control kind invalid")
    return REMOTE_CONTROL_PREFIX + kind + "/" + sha256


def _persist_remote_control(
    store: ManifestArtifactStore, raw: bytes, *, kind: str,
) -> tuple[str, str]:
    """Persist exact bounded provider identity bytes before the next side effect."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= REMOTE_MAX_CONTROL_BYTES:
        raise BoundaryError("local_training", "remote_control_size_limit")
    blobs: Any = getattr(store, "blobs", None)
    if not callable(getattr(blobs, "put_if_absent", None)) or not callable(
        getattr(blobs, "get", None)
    ):
        raise BoundaryError("local_training", "remote_control_blob_store_unavailable")
    sha256 = hashlib.sha256(raw).hexdigest()
    key = _remote_control_key(kind, sha256)
    try:
        blobs.put_if_absent(key, raw)
        if blobs.get(key) != raw:
            raise ValueError("remote control bytes mismatch")
    except Exception as error:
        raise BoundaryError("local_training", "remote_control_persistence_failed") from error
    return key, sha256


def _read_remote_control(
    store: ManifestArtifactStore, key: str, sha256: str, *, kind: str,
) -> bytes:
    blobs: Any = getattr(store, "blobs", None)
    if not callable(getattr(blobs, "get", None)):
        raise BoundaryError("local_training", "remote_control_blob_store_unavailable")
    if safe_key(key) != _remote_control_key(kind, sha256):
        raise BoundaryError("local_training", "remote_control_reference_invalid")
    try:
        raw = blobs.get(key)
        if (not isinstance(raw, bytes) or not 1 <= len(raw) <= REMOTE_MAX_CONTROL_BYTES
                or hashlib.sha256(raw).hexdigest() != sha256):
            raise ValueError("remote control integrity mismatch")
        return raw
    except BoundaryError:
        raise
    except Exception as error:
        raise BoundaryError("local_training", "remote_control_unavailable") from error


def _validate_remote_target(target: object) -> None:
    target_fields = {"target_id", "app_id", "deployment_source_sha256", "gpu",
                     "timeout_seconds", "startup_timeout_seconds", "max_total_steps"}
    if not isinstance(target, dict) or set(target) != target_fields:
        raise ValueError("remote target fields invalid")
    digest(target["target_id"], "local_training.target_id")
    digest(target["deployment_source_sha256"], "local_training.deployment_source")
    if (not isinstance(target["app_id"], str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", target["app_id"]) is None
            or target["gpu"] != "L4"
            or type(target["timeout_seconds"]) is not int
            or not 1 <= target["timeout_seconds"] <= REMOTE_MAX_TIMEOUT_SECONDS
            or type(target["startup_timeout_seconds"]) is not int
            or not 1 <= target["startup_timeout_seconds"]
            <= REMOTE_MAX_STARTUP_TIMEOUT_SECONDS
            or type(target["max_total_steps"]) is not int
            or not 1 <= target["max_total_steps"] <= REMOTE_MAX_TOTAL_STEPS):
        raise ValueError("remote target limit invalid")


def _validate_remote_error_code(error_code: object) -> None:
    if (not isinstance(error_code, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code) is None):
        raise ValueError("remote error code invalid")


def _remote_request_identity(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= REMOTE_MAX_REQUEST_BYTES:
        raise BoundaryError("local_training", "remote_request_size_limit")
    try:
        value = decode_json(raw)
        if (not isinstance(value, dict)
                or value.get("schema") != REMOTE_REQUEST_SCHEMA):
            raise ValueError
        digest(value.get("attempt_id"), "local_training.attempt_id", length=32)
        digest(value.get("operation_id"), "local_training.operation_id", length=32)
        if type(value.get("target_step")) is not int or value["target_step"] < 1:
            raise ValueError
        resume_checkpoint_id = value.get("resume_checkpoint_id")
        encoded_manifest = value.get("resume_manifest")
        if encoded_manifest is not None:
            if not isinstance(encoded_manifest, str):
                raise ValueError
            try:
                manifest_bytes = base64.b64decode(encoded_manifest, validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError from error
            if base64.b64encode(manifest_bytes).decode("ascii") != encoded_manifest:
                raise ValueError
            manifest_id = Manifest.from_bytes(manifest_bytes).artifact_id
            if resume_checkpoint_id is not None and resume_checkpoint_id != manifest_id:
                raise ValueError
            resume_checkpoint_id = manifest_id
        if resume_checkpoint_id is not None:
            digest(resume_checkpoint_id, "local_training.resume_checkpoint_id")
        return {**value, "resume_checkpoint_id": resume_checkpoint_id}
    except (BoundaryError, ValueError, TypeError, KeyError) as error:
        raise BoundaryError("local_training", "remote_request_identity_invalid") from error


def _validate_remote_operation(value: dict[str, Any]) -> None:
    """Validate the durable, provider-neutral portion of one Modal M0 operation."""
    if (type(value.get("created_at_unix_ns")) is not int
            or value["created_at_unix_ns"] < 1):
        raise ValueError("remote operation creation time invalid")
    if value.get("completed_at_unix_ns") is not None and (
        type(value["completed_at_unix_ns"]) is not int
        or value["completed_at_unix_ns"] < value["created_at_unix_ns"]
    ):
        raise ValueError("remote operation completion time invalid")
    if value.get("stopped_at_unix_ns") is not None and (
        type(value["stopped_at_unix_ns"]) is not int
        or value["stopped_at_unix_ns"] < value["created_at_unix_ns"]
    ):
        raise ValueError("remote operation stop time invalid")
    if value.get("total_wall_seconds") is not None and (
        isinstance(value["total_wall_seconds"], bool)
        or not isinstance(value["total_wall_seconds"], (int, float))
        or not math.isfinite(value["total_wall_seconds"])
        or value["total_wall_seconds"] < 0
    ):
        raise ValueError("remote operation total wall time invalid")
    if (value.get("recipe") != PUBLIC_M0_RECIPE
            or value.get("input_profile") not in {"public_lite", "public_compact"}
            or value.get("result_type") != "evaluated"
            or value.get("evaluation_status") not in {"pending", "completed"}):
        raise ValueError("remote M0 identity invalid")

    remote = value.get("remote")
    if not isinstance(remote, dict) or set(remote) != {"target", "attempts"}:
        raise ValueError("remote operation fields invalid")
    target = remote["target"]
    _validate_remote_target(target)

    attempts = remote["attempts"]
    attempt_fields = {"attempt_id", "request_sha256", "request_ref", "start_step",
                      "target_step", "resume_checkpoint_id", "resume_of_attempt_id",
                      "attempt_spec_sha256", "attempt_spec_ref", "app_ref_sha256",
                      "app_ref_ref", "handle_sha256", "handle_ref",
                      "runtime_evidence_sha256", "runtime_evidence_ref", "app_phase",
                      "stop_confirmation", "provider_error_code",
                      "submit_intent_at_unix_ns", "provider_terminal_observed_at_unix_ns",
                      "local_acceptance_started_at_unix_ns",
                      "local_acceptance_completed_at_unix_ns",
                      "core_finalizer_attempt_seconds",
                      "phase", "provider_terminal", "terminal_state",
                      "checkpoint_candidate_sha256", "validated_checkpoint_id",
                      "provider_result_sha256", "provider_result_ref"}
    if not isinstance(attempts, list) or not attempts:
        raise ValueError("remote attempts invalid")
    seen: set[str] = set()
    expected_checkpoint_id: str | None = None
    expected_checkpoint_step = 0
    for index, attempt in enumerate(attempts):
        legacy_attempt_fields = attempt_fields - {
            "runtime_evidence_sha256", "runtime_evidence_ref",
        }
        if isinstance(attempt, dict) and set(attempt) == legacy_attempt_fields:
            # Older v4 journals remain readable; new controllers write the added
            # evidence reference only after an observed remote result.
            attempt["runtime_evidence_sha256"] = None
            attempt["runtime_evidence_ref"] = None
        if not isinstance(attempt, dict) or set(attempt) != attempt_fields:
            raise ValueError("remote attempt fields invalid")
        attempt_id = digest(attempt["attempt_id"], "local_training.attempt_id", length=32)
        digest(attempt["request_sha256"], "local_training.request_sha256")
        request_ref = safe_key(attempt["request_ref"])
        if request_ref != REMOTE_REQUEST_PREFIX + attempt["request_sha256"]:
            raise ValueError("remote request reference invalid")
        for stem, kind in (("attempt_spec", "attempt-spec"),
                           ("app_ref", "app-ref"), ("handle", "call-handle"),
                           ("runtime_evidence", "runtime-evidence")):
            sha_field = stem + "_sha256"
            ref_field = stem + "_ref"
            if (attempt[sha_field] is None) != (attempt[ref_field] is None):
                raise ValueError("remote control reference incomplete")
            if attempt[sha_field] is not None:
                digest(attempt[sha_field], "local_training." + sha_field)
                if safe_key(attempt[ref_field]) != _remote_control_key(
                    kind, attempt[sha_field]
                ):
                    raise ValueError("remote control reference invalid")
        app_phase_refs = {
            "not_prepared": (False, False, False),
            "prepare_intent": (True, False, False),
            "app_pinned": (True, True, False),
            "submitted": (True, True, True),
        }[attempt["app_phase"]]
        if tuple(attempt[stem + "_ref"] is not None for stem in (
            "attempt_spec", "app_ref", "handle"
        )) != app_phase_refs:
            raise ValueError("remote provider lifecycle reference mismatch")
        if (attempt["provider_result_sha256"] is None
                or attempt["provider_result_ref"] is None):
            if (attempt["provider_result_sha256"] is not None
                    or attempt["provider_result_ref"] is not None):
                raise ValueError("remote provider result reference incomplete")
        else:
            digest(attempt["provider_result_sha256"], "local_training.provider_result_sha256")
            result_ref = safe_key(attempt["provider_result_ref"])
            if result_ref != REMOTE_RESULT_PREFIX + attempt["provider_result_sha256"]:
                raise ValueError("remote provider result reference invalid")
        if (type(attempt["start_step"]) is not int or attempt["start_step"] < 0
                or type(attempt["target_step"]) is not int
                or not attempt["start_step"] < attempt["target_step"]
                <= target["max_total_steps"]):
            raise ValueError("remote step bound invalid")
        for field in ("resume_checkpoint_id", "validated_checkpoint_id",
                      "checkpoint_candidate_sha256"):
            if attempt[field] is not None:
                digest(attempt[field], "local_training." + field)
        for field in ("resume_of_attempt_id",):
            if attempt[field] is not None:
                digest(attempt[field], "local_training." + field, length=32)
        if attempt["phase"] not in REMOTE_ATTEMPT_PHASES:
            raise ValueError("remote attempt phase invalid")
        if attempt["app_phase"] not in REMOTE_APP_PHASES:
            raise ValueError("remote app phase invalid")
        if type(attempt["provider_terminal"]) is not bool:
            raise ValueError("remote terminal flag invalid")
        submit_at = attempt["submit_intent_at_unix_ns"]
        if ((submit_at is None) != (attempt["app_ref_ref"] is None)
                or (submit_at is not None
                    and (type(submit_at) is not int or submit_at < 1))):
            raise ValueError("remote submit intent time invalid")
        terminal_at = attempt["provider_terminal_observed_at_unix_ns"]
        if ((terminal_at is None) != (not attempt["provider_terminal"])
                or (terminal_at is not None
                    and (type(terminal_at) is not int or terminal_at < 1))):
            raise ValueError("remote provider terminal time invalid")
        acceptance_started = attempt["local_acceptance_started_at_unix_ns"]
        acceptance_completed = attempt["local_acceptance_completed_at_unix_ns"]
        for timestamp in (acceptance_started, acceptance_completed):
            if timestamp is not None and (type(timestamp) is not int or timestamp < 1):
                raise ValueError("remote local acceptance time invalid")
        if acceptance_completed is not None and (
            acceptance_started is None or acceptance_completed < acceptance_started
        ):
            raise ValueError("remote local acceptance duration invalid")
        finalizer_seconds = attempt["core_finalizer_attempt_seconds"]
        if finalizer_seconds is not None and (
            isinstance(finalizer_seconds, bool)
            or not isinstance(finalizer_seconds, (int, float))
            or not math.isfinite(finalizer_seconds)
            or finalizer_seconds < 0
        ):
            raise ValueError("remote core finalizer time invalid")
        if (attempt["terminal_state"] is not None
                and attempt["terminal_state"] not in REMOTE_TERMINAL_STATES):
            raise ValueError("remote terminal state invalid")
        if attempt["provider_error_code"] is not None:
            _validate_remote_error_code(attempt["provider_error_code"])
        stop_confirmation = attempt["stop_confirmation"]
        if (stop_confirmation is not None
                and (not isinstance(stop_confirmation, dict)
                     or set(stop_confirmation) != {"app_id", "app_state", "active_tasks",
                                                    "active_containers", "confirmed"}
                     or not isinstance(stop_confirmation["app_id"], str)
                     or stop_confirmation["app_state"] != "stopped"
                     or stop_confirmation["active_tasks"] != 0
                     or stop_confirmation["active_containers"] != 0
                     or stop_confirmation["confirmed"] is not True)):
            raise ValueError("remote stop confirmation invalid")
        if attempt["phase"] == "submit_intent":
            if (attempt["handle_ref"] is not None or attempt["provider_terminal"]
                    or attempt["terminal_state"] is not None):
                raise ValueError("submit intent cannot imply a remote handle or terminal")
        elif attempt["phase"] == "preflight_failed":
            if (attempt["handle_ref"] is not None or attempt["provider_terminal"]
                    or attempt["terminal_state"] != "failed"):
                raise ValueError("preflight failure cannot imply a remote task")
        elif attempt["phase"] in {"running", "cancelling"}:
            if (attempt["handle_ref"] is None or attempt["app_ref_ref"] is None
                    or attempt["attempt_spec_ref"] is None or attempt["provider_terminal"]
                    or attempt["terminal_state"] is not None):
                raise ValueError("active remote attempt fields invalid")
        elif attempt["phase"] == "stopping":
            if (attempt["handle_ref"] is None or attempt["app_ref_ref"] is None
                    or attempt["attempt_spec_ref"] is None or not attempt["provider_terminal"]
                    or attempt["terminal_state"] not in REMOTE_TERMINAL_STATES
                    or attempt["stop_confirmation"] is not None):
                raise ValueError("remote stop-pending attempt fields invalid")
        elif (attempt["handle_ref"] is None or attempt["app_ref_ref"] is None
              or attempt["attempt_spec_ref"] is None or not attempt["provider_terminal"]
              or attempt["terminal_state"] not in REMOTE_TERMINAL_STATES):
            raise ValueError("remote terminal observation invalid")
        if (attempt["phase"] == "terminal" and attempt["stop_confirmation"] is None):
            raise ValueError("terminal attempt lacks confirmed provider stop")
        if (attempt["phase"] == "stopping" and attempt["terminal_state"] == "failed"
                and attempt["provider_error_code"] is None):
            raise ValueError("provider failure lacks an error code")
        if (attempt["phase"] == "terminal"
                and attempt["terminal_state"] in {"paused", "completed"}
                and attempt["provider_result_ref"] is None):
            raise ValueError("terminal update result missing")
        if attempt_id in seen:
            raise ValueError("remote attempt ID repeated")
        seen.add(attempt_id)
        if index == 0:
            if (attempt["start_step"] != 0 or attempt["resume_checkpoint_id"] is not None
                    or attempt["resume_of_attempt_id"] is not None):
                raise ValueError("initial remote attempt cannot resume")
        else:
            previous = attempts[index - 1]
            if (attempt["resume_checkpoint_id"] is None
                    or previous["phase"] != "terminal" or not previous["provider_terminal"]
                    or previous["terminal_state"] not in {"paused", "cancelled"}
                    or attempt["resume_of_attempt_id"] != previous["attempt_id"]
                    or attempt["resume_checkpoint_id"] != expected_checkpoint_id
                    or attempt["start_step"] != expected_checkpoint_step):
                raise ValueError(
                    "remote resume requires a terminal attempt and verified checkpoint"
                )
        if attempt["resume_checkpoint_id"] != expected_checkpoint_id:
            raise ValueError("remote attempt checkpoint lineage mismatch")
        if attempt["start_step"] != expected_checkpoint_step:
            raise ValueError("remote attempt step lineage mismatch")
        if (attempt["phase"] == "terminal" and attempt["terminal_state"] == "completed"
                and attempt["checkpoint_candidate_sha256"] is None):
            raise ValueError("completed attempt lacks a checkpoint candidate")
        if (attempt["phase"] == "terminal"
                and attempt["terminal_state"] in {"paused", "completed"}
                and attempt["validated_checkpoint_id"] is not None):
            expected_checkpoint_id = attempt["validated_checkpoint_id"]
            expected_checkpoint_step = attempt["target_step"]
    if ("checkpoint_step" in value and type(value["checkpoint_step"]) is not int):
        raise ValueError("remote checkpoint step invalid")
    if (value.get("checkpoint_id") != expected_checkpoint_id
            or value.get("checkpoint_step", 0) != expected_checkpoint_step):
        raise ValueError("remote operation checkpoint lineage mismatch")

    latest = attempts[-1]
    status = value["status"]
    stage = value["stage"]
    expected = {
        "pending": ({"submit_intent", "running", "stopping", "terminal"},
                    {"remote_submit_intent", "remote_running", "remote_stopping",
                     "remote_acceptance"}),
        "cancelling": ({"cancelling"}, {"remote_cancelling"}),
        "interrupted_unknown": ({"submit_intent", "running", "cancelling", "stopping"},
                                {"remote_unknown"}),
        "paused": ({"terminal"}, {"remote_paused"}),
        "cancelled": ({"terminal"}, {"remote_cancelled"}),
        "completed": ({"terminal"}, {"completed"}),
        "failed": ({"terminal", "preflight_failed"}, {"remote_failed"}),
    }
    phases, stages = expected[status]
    if latest["phase"] not in phases or stage not in stages:
        raise ValueError("remote operation status mismatch")
    if status == "cancelling" and latest["handle_ref"] is None:
        raise ValueError("remote cancellation requires a handle")
    if status == "interrupted_unknown":
        _validate_remote_error_code(value.get("error_code"))
    if status == "paused":
        if (latest["terminal_state"] != "paused"
                or latest["validated_checkpoint_id"] != value.get("checkpoint_id")
                or value.get("checkpoint_step") != latest["target_step"]
                or latest["local_acceptance_completed_at_unix_ns"] is None):
            raise ValueError("paused operation requires its validated target checkpoint")
    elif status == "cancelled" and latest["terminal_state"] != "cancelled":
        raise ValueError("cancelled operation lacks provider cancellation proof")
    elif status == "completed":
        if (latest["terminal_state"] != "completed"
                or value.get("evaluation_status") != "completed"
                or value.get("completed_at_unix_ns") is None
                or value.get("total_wall_seconds") is None
                or latest["local_acceptance_completed_at_unix_ns"] is None):
            raise ValueError("completed remote operation lacks local acceptance")
    elif status == "failed" and latest["terminal_state"] != "failed":
        raise ValueError("failed operation lacks a terminal provider failure")
    if status in {"cancelled", "failed"} and (
        value.get("stopped_at_unix_ns") is None or value.get("total_wall_seconds") is None
    ):
        raise ValueError("terminal remote operation lacks end-to-end duration")
    if status == "failed":
        _validate_remote_error_code(value.get("error_code"))


def _persist_remote_blob(store: ManifestArtifactStore, raw: bytes, *,
                         object_prefix: str, chunk_prefix: str,
                         maximum: int, label: str) -> tuple[str, str]:
    if not isinstance(raw, bytes):
        raise BoundaryError("local_training", label + "_must_be_bytes")
    if not raw or len(raw) > maximum:
        raise BoundaryError("local_training", label + "_size_limit")
    blobs: Any = getattr(store, "blobs", None)
    if not callable(getattr(blobs, "put_if_absent", None)) or not callable(
        getattr(blobs, "get", None)
    ):
        raise BoundaryError("local_training", label + "_blob_store_unavailable")
    sha256 = hashlib.sha256(raw).hexdigest()
    object_key = object_prefix + sha256
    chunks = []
    try:
        for offset in range(0, len(raw), REMOTE_CHUNK_BYTES):
            chunk = raw[offset:offset + REMOTE_CHUNK_BYTES]
            chunk_sha256 = hashlib.sha256(chunk).hexdigest()
            chunk_key = chunk_prefix + chunk_sha256
            blobs.put_if_absent(chunk_key, chunk)
            if blobs.get(chunk_key) != chunk:
                raise ValueError("remote evidence chunk mismatch")
            chunks.append({"sha256": chunk_sha256, "size": len(chunk)})
        index = json.dumps({
            "schema": "stpd/local-training-remote-evidence-index-v1",
            "sha256": sha256,
            "size": len(raw),
            "chunks": chunks,
        }, sort_keys=True, separators=(",", ":")).encode()
        blobs.put_if_absent(object_key, index)
        if blobs.get(object_key) != index:
            raise ValueError("remote evidence index mismatch")
    except Exception as error:
        raise BoundaryError("local_training", label + "_persistence_failed") from error
    return object_key, sha256


def _read_remote_blob(store: ManifestArtifactStore, object_ref: str, sha256: str, *,
                      object_prefix: str, chunk_prefix: str,
                      maximum: int, label: str) -> bytes:
    blobs: Any = getattr(store, "blobs", None)
    if not callable(getattr(blobs, "get", None)):
        raise BoundaryError("local_training", label + "_blob_store_unavailable")
    if safe_key(object_ref) != object_prefix + sha256:
        raise BoundaryError("local_training", label + "_reference_invalid")
    try:
        index_bytes = blobs.get(object_ref)
        index = json.loads(index_bytes)
        if (not isinstance(index, dict)
                or set(index) != {"schema", "sha256", "size", "chunks"}
                or index["schema"] != "stpd/local-training-remote-evidence-index-v1"
                or index["sha256"] != sha256
                or type(index["size"]) is not int or not 0 < index["size"] <= maximum
                or not isinstance(index["chunks"], list)
                or len(index["chunks"]) != math.ceil(index["size"] / REMOTE_CHUNK_BYTES)):
            raise ValueError("remote evidence index invalid")
        pieces = []
        total = 0
        for item in index["chunks"]:
            if not isinstance(item, dict) or set(item) != {"sha256", "size"}:
                raise ValueError("remote evidence chunk index invalid")
            chunk_sha256 = digest(item["sha256"], "local_training.evidence_chunk_sha256")
            size = item["size"]
            if type(size) is not int or not 0 < size <= REMOTE_CHUNK_BYTES:
                raise ValueError("remote evidence chunk size invalid")
            chunk = blobs.get(chunk_prefix + chunk_sha256)
            if len(chunk) != size or hashlib.sha256(chunk).hexdigest() != chunk_sha256:
                raise ValueError("remote evidence chunk integrity mismatch")
            total += size
            pieces.append(chunk)
        raw = b"".join(pieces)
        if (total != index["size"] or hashlib.sha256(raw).hexdigest() != sha256
                or len(raw) > maximum):
            raise ValueError("remote evidence integrity mismatch")
        return raw
    except BoundaryError:
        raise
    except Exception as error:
        raise BoundaryError("local_training", label + "_unavailable") from error


def _persist_remote_request(store: ManifestArtifactStore, request_bytes: bytes) -> tuple[str, str]:
    """Store exact typed request bytes in bounded chunks, outside manifest inventory."""
    return _persist_remote_blob(
        store, request_bytes, object_prefix=REMOTE_REQUEST_PREFIX,
        chunk_prefix=REMOTE_REQUEST_CHUNK_PREFIX,
        maximum=REMOTE_MAX_REQUEST_BYTES, label="remote_request",
    )


def _persist_remote_result(store: ManifestArtifactStore, result_bytes: bytes) -> tuple[str, str]:
    """Keep exact typed provider result bytes for restart-safe local finalization."""
    return _persist_remote_blob(
        store, result_bytes, object_prefix=REMOTE_RESULT_PREFIX,
        chunk_prefix=REMOTE_RESULT_CHUNK_PREFIX,
        maximum=REMOTE_MAX_RESULT_BYTES, label="remote_result",
    )


def _read_remote_evidence(
    store: ManifestArtifactStore, operation: dict[str, Any],
) -> tuple[bytes, bytes]:
    attempt = operation["remote"]["attempts"][-1]
    request_bytes = _read_remote_blob(
        store, attempt["request_ref"], attempt["request_sha256"],
        object_prefix=REMOTE_REQUEST_PREFIX, chunk_prefix=REMOTE_REQUEST_CHUNK_PREFIX,
        maximum=REMOTE_MAX_REQUEST_BYTES, label="remote_request",
    )
    result_bytes = _read_remote_blob(
        store, attempt["provider_result_ref"], attempt["provider_result_sha256"],
        object_prefix=REMOTE_RESULT_PREFIX, chunk_prefix=REMOTE_RESULT_CHUNK_PREFIX,
        maximum=REMOTE_MAX_RESULT_BYTES, label="remote_result",
    )
    return request_bytes, result_bytes


def _remote_provider_bytes(
    store: ManifestArtifactStore, attempt: dict[str, Any],
) -> tuple[bytes | None, bytes | None, bytes | None]:
    values: list[bytes | None] = []
    for stem, kind in (("attempt_spec", "attempt-spec"),
                       ("app_ref", "app-ref"), ("handle", "call-handle")):
        ref, sha256 = attempt[stem + "_ref"], attempt[stem + "_sha256"]
        values.append(None if ref is None else _read_remote_control(
            store, ref, sha256, kind=kind,
        ))
    return values[0], values[1], values[2]


def _remote_runtime_evidence_bytes(
    store: ManifestArtifactStore, attempt: dict[str, Any],
) -> bytes | None:
    ref, sha256 = attempt["runtime_evidence_ref"], attempt["runtime_evidence_sha256"]
    return (None if ref is None else
            _read_remote_control(store, ref, sha256, kind="runtime-evidence"))


def _decode_remote_provider_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or json_bytes(value) != raw:
            raise ValueError("noncanonical provider bytes")
        return value
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise BoundaryError("local_training", label + "_invalid") from error


def _safe_parent_failure(error: Exception, stage: str) -> dict[str, Any]:
    frames = traceback.extract_tb(error.__traceback__)
    owner = next((frame for frame in frames if
                  Path(frame.filename).name == "local_training.py" and
                  frame.name == "_run"), None)
    origin = frames[-1] if frames else None

    def point(frame: traceback.FrameSummary | None) -> dict[str, Any] | None:
        if frame is None:
            return None
        return {"file": Path(frame.filename).name, "function": frame.name,
                "line": frame.lineno}

    errno = getattr(error, "errno", None)
    winerror = getattr(error, "winerror", None)
    return {"stage": stage, "exception_type": type(error).__module__ + "." +
            type(error).__qualname__,
            "errno": errno if type(errno) is int else None,
            "winerror": winerror if type(winerror) is int else None,
            "owner_call": point(owner), "origin": point(origin)}


def _child_json_record(captured: bytes) -> dict[str, Any]:
    """Read the final machine record; libraries may emit earlier stdout diagnostics."""
    lines = [line for line in captured.decode("utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError("child_json_record_missing")
    value = json.loads(lines[-1])
    if not isinstance(value, dict):
        raise ValueError("child_json_record_invalid")
    return value


def _write_parent_failure(path: Path, identity: str, error: Exception) -> None:
    destination = path.parent / ("local-training-" + identity + "-parent-error.log")
    raw = "".join(traceback.format_exception(error)).encode("utf-8", "replace")
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw[:PARENT_FAILURE_LOG_BYTES])
        handle.flush()
        os.fsync(handle.fileno())


class LocalTrainingService:
    def __init__(self, config: ProjectConfig, *, config_path: Path | None = None) -> None:
        self.config = config
        self.config_path = config_path
        self._selection = LocalDatasetService(config)
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._failure_diagnostic: dict[str, Any] | None = None

    def _selected(self):
        return self._selection._selected()

    @staticmethod
    def _paths(owner: LocalCurationOwner) -> tuple[Path, Path]:
        return owner.path.parent / OPERATION_FILE, owner.path.parent / LOCK_FILE

    @staticmethod
    def _require_remote_lock(lock_path: Path) -> None:
        if lock_path.is_symlink() or not lock_path.is_file():
            raise BoundaryError("local_training", "operation_recovery_required")

    @staticmethod
    def _read(path: Path, identity: tuple[str, ...]) -> dict[str, Any]:
        try:
            if path.is_symlink():
                raise ValueError
            value = json.loads(read_replaceable_bytes(path))
            schema = value.get("schema") if isinstance(value, dict) else None
            allowed_statuses = (REMOTE_STATUSES if schema == SCHEMA_V4 else
                                {"pending", "completed", "failed", "interrupted_unknown"})
            allowed_stages = REMOTE_STAGES if schema == SCHEMA_V4 else LEGACY_STAGES
            if (not isinstance(value, dict)
                    or schema not in {SCHEMA, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4}
                    or value.get("status") not in allowed_statuses
                    or value.get("stage") not in allowed_stages
                    or value.get("_owner") != list(identity)):
                raise ValueError
            digest(value["operation_id"], "local_training.operation", length=32)
            digest(value["dataset_id"], "local_training.dataset")
            if schema == SCHEMA_V4:
                _validate_remote_operation(value)
                for key in IDS:
                    if key in value:
                        digest(value[key], "local_training." + key)
                completed_ids: tuple[str, ...] = (
                    "input_id", "run_id", "checkpoint_id", "result_id", "model_id",
                    "evaluation_id",
                )
                if value["status"] == "completed" and not all(
                    value.get(key) for key in completed_ids
                ):
                    raise ValueError("completed remote result IDs missing")
                if "previous_completed" in value:
                    previous = value["previous_completed"]
                    allowed_previous = (set(PREVIOUS_COMPLETED_IDS),
                                        set(PREVIOUS_COMPLETED_IDS) - {"evaluation_id"})
                    if not isinstance(previous, dict) or set(previous) not in allowed_previous:
                        raise ValueError("previous completion invalid")
                    digest(previous["operation_id"], "local_training.previous_operation",
                           length=32)
                    for key in ("dataset_id", "result_id", "model_id", "evaluation_id"):
                        if key in previous:
                            digest(previous[key], "local_training.previous_" + key)
                return value
            memory = value.get("recipe") in MEMORY_RECIPES
            if schema == SCHEMA_V3:
                if (value.get("recipe") != PUBLIC_M0_RECIPE
                        or value.get("input_profile") not in {"public_lite", "public_compact"}
                        or value.get("result_type") != "evaluated"
                        or value.get("evaluation_status") not in {"pending", "completed"}
                        or (value["status"] == "completed"
                            and value.get("evaluation_status") != "completed")):
                    raise ValueError
            elif schema == SCHEMA_V2:
                if value.get("recipe") not in {DEFAULT_RECIPE, *MEMORY_RECIPES}:
                    raise ValueError
                if memory:
                    if (value.get("result_type") != "train_only"
                            or value.get("evaluation_status") != "not_run"
                            or "evaluation_id" in value):
                        raise ValueError
                elif (value.get("result_type") != "evaluated"
                      or value.get("evaluation_status") != (
                          "completed" if value["status"] == "completed" else "pending")):
                    raise ValueError
            for key in IDS:
                if key in value:
                    digest(value[key], "local_training." + key)
            completed_ids = (("input_id", "run_id", "result_id", "model_id")
                             if schema == SCHEMA_V3 else
                             ("input_id", "run_id", "checkpoint_id", "result_id", "model_id")
                             if schema == SCHEMA_V2 and memory else
                             ("run_id", "result_id", "model_id", "evaluation_id"))
            if value["status"] == "completed" and not all(value.get(key) for key in completed_ids):
                raise ValueError
            if "previous_completed" in value:
                previous = value["previous_completed"]
                legacy = set(PREVIOUS_COMPLETED_IDS)
                legacy_without_eval = legacy - {"evaluation_id"}
                typed = {"operation_id", "dataset_id", "result_id", "model_id", "recipe",
                         "result_type", "evaluation_status", "checkpoint_id", "input_id"}
                if not isinstance(previous, dict) or (
                    set(previous) not in ((legacy, legacy_without_eval)
                                          if schema == SCHEMA_V3 else (legacy,)) and (
                        schema != SCHEMA_V2
                        or set(previous) not in (typed, typed | {"evaluation_id"})
                    )
                ):
                    raise ValueError
                digest(previous["operation_id"], "local_training.previous_operation", length=32)
                for key in ("dataset_id", "result_id", "model_id", "evaluation_id",
                            "checkpoint_id", "input_id"):
                    if key not in previous:
                        continue
                    digest(previous[key], "local_training.previous_" + key)
                if "recipe" in previous:
                    if previous["recipe"] in MEMORY_RECIPES:
                        if (set(previous) != typed or previous["result_type"] != "train_only"
                                or previous["evaluation_status"] != "not_run"):
                            raise ValueError
                    elif (previous["recipe"] != DEFAULT_RECIPE
                          or set(previous) != typed | {"evaluation_id"}
                          or previous["result_type"] != "evaluated"
                          or previous["evaluation_status"] != "completed"):
                        raise ValueError
            if value["status"] in {"failed", "interrupted_unknown"} and not isinstance(
                value.get("error_code"), str
            ):
                raise ValueError
            return value
        except FileNotFoundError as error:
            if path.is_symlink() or has_unresolved_replacement(path):
                raise BoundaryError("local_training", "operation_recovery_required") from error
            return {"status": "idle"}
        except (OSError, ValueError, KeyError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_training", "operation_recovery_required") from error

    @staticmethod
    def _public(value: dict[str, Any]) -> dict[str, Any]:
        keys = {"status", "stage", "operation_id", "dataset_id", "error_code",
                "recipe", "result_type", "evaluation_status", "schema",
                "input_profile", "previous_completed", "checkpoint_step", *IDS}
        result = {key: item for key, item in value.items() if key in keys}
        if value.get("schema") == SCHEMA_V4:
            remote = value["remote"]
            public_attempts = []
            for attempt in remote["attempts"]:
                public_attempt = {key: item for key, item in attempt.items()
                                  if not key.endswith("_ref")}
                submit_at = attempt["submit_intent_at_unix_ns"]
                terminal_at = attempt["provider_terminal_observed_at_unix_ns"]
                acceptance_started = attempt["local_acceptance_started_at_unix_ns"]
                acceptance_completed = attempt["local_acceptance_completed_at_unix_ns"]
                if submit_at is not None and terminal_at is not None:
                    public_attempt["submit_to_terminal_seconds"] = max(
                        0.0, (terminal_at - submit_at) / 1_000_000_000,
                    )
                if terminal_at is not None and acceptance_started is not None:
                    public_attempt["terminal_to_acceptance_seconds"] = max(
                        0.0, (acceptance_started - terminal_at) / 1_000_000_000,
                    )
                if acceptance_started is not None and acceptance_completed is not None:
                    public_attempt["local_acceptance_seconds"] = max(
                        0.0, (acceptance_completed - acceptance_started) / 1_000_000_000,
                    )
                public_attempts.append(public_attempt)
            result["remote"] = {
                "target": remote["target"],
                "attempts": public_attempts,
            }
            for key in ("created_at_unix_ns", "completed_at_unix_ns", "stopped_at_unix_ns",
                        "total_wall_seconds"):
                if key in value:
                    result[key] = value[key]
        return result

    def status(self) -> dict[str, Any]:
        try:
            owner, _, _ = self._selected()
        except BoundaryError as error:
            availability = ("workspace_required" if error.code == "workspace_required" else
                            "preparation_required" if error.code == "curation_preparation_required"
                            else "recovery_required")
            return {"schema": SCHEMA, "availability": availability, "reason": error.code,
                    "operation": {"status": "idle"}}
        path, lock_path = self._paths(owner)
        try:
            operation = self._read(path, owner.identity)
            if operation["status"] == "idle":
                # Absence during a live writer's replacement is not an idle
                # operation. Reconcile under the existing owner lock without
                # creating it; an unlocked pre-admission lock is still harmless.
                try:
                    if lock_path.is_symlink():
                        raise BoundaryError("local_training", "operation_recovery_required")
                    with instance_lock(lock_path, create=False):
                        operation = self._read(path, owner.identity)
                except FileNotFoundError:
                    pass
                except (OSError, BoundaryError) as error:
                    raise BoundaryError("local_training", "operation_recovery_required") from error
        except BoundaryError as error:
            return {"schema": SCHEMA, "availability": "recovery_required",
                    "reason": error.code, "operation": {"status": "idle"}}
        attempt = (operation.get("remote", {}).get("attempts", [{}])[-1]
                   if operation.get("schema") == SCHEMA_V4 else {})
        durable_remote_recovery = (
            operation.get("schema") == SCHEMA_V4
            and attempt.get("handle_ref") is not None
            and operation.get("stage") in {
                "remote_running", "remote_cancelling", "remote_stopping",
                "remote_acceptance",
            }
        )
        if (operation["status"] in {"pending", "cancelling"}
                and not durable_remote_recovery):
            # A lock held by this or another profile proves the supervising parent
            # is alive. A missing lock means an unknown child outcome, not failure.
            try:
                if not lock_path.is_file() or lock_path.is_symlink():
                    raise BoundaryError("local_training", "operation_lock_missing")
                with instance_lock(lock_path, create=False):
                    # The supervisor may have written its terminal record and
                    # released the lock after our first read. Re-read under the
                    # lock before classifying an apparently unfinished operation.
                    try:
                        operation = self._read(path, owner.identity)
                        if operation["status"] == "idle":
                            raise BoundaryError("local_training", "operation_recovery_required")
                    except BoundaryError as error:
                        return {"schema": SCHEMA, "availability": "recovery_required",
                                "reason": error.code, "operation": {"status": "idle"}}
                    if operation["status"] in {"pending", "cancelling"}:
                        operation = {**operation, "status": "interrupted_unknown",
                                     "stage": ("remote_unknown"
                                               if operation.get("schema") == SCHEMA_V4
                                               else operation["stage"]),
                                     "error_code": "previous_training_outcome_unknown"}
                        if operation.get("schema") == SCHEMA_V4:
                            _validate_remote_operation(operation)
                            write_replaceable_json(path, operation)
            except (OSError, BoundaryError) as error:
                if not isinstance(error, BoundaryError) or error.code != "already_running":
                    operation = {**operation, "status": "interrupted_unknown",
                                 "stage": ("remote_unknown" if operation.get("schema") == SCHEMA_V4
                                           else operation["stage"]),
                                 "error_code": "previous_training_outcome_unknown"}
        return {"schema": operation.get("schema", SCHEMA), "availability": "ready",
                "operation": self._public(operation)}

    @staticmethod
    def _advance(path: Path, identity: str, **updates: Any) -> None:
        current = json.loads(read_replaceable_bytes(path))
        if current.get("operation_id") != identity or current.get("status") != "pending":
            raise BoundaryError("local_training", "operation_superseded")
        write_replaceable_json(path, {**current, **updates})

    @staticmethod
    def _write_remote(path: Path, owner_identity: tuple[str, ...], operation_id: str,
                      update: Any) -> dict[str, Any]:
        current = LocalTrainingService._read(path, owner_identity)
        if current.get("schema") != SCHEMA_V4 or current.get("operation_id") != operation_id:
            raise BoundaryError("local_training", "remote_operation_superseded")
        updated = update(current)
        try:
            _validate_remote_operation(updated)
            # Run the same parser used after restart before publishing a transition.
            encoded = json.dumps(updated, sort_keys=True, separators=(",", ":")).encode()
            decoded = json.loads(encoded)
            _validate_remote_operation(decoded)
        except (ValueError, KeyError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_training", "remote_operation_transition_invalid") from error
        write_replaceable_json(path, updated)
        return cast(dict[str, Any], updated)

    @staticmethod
    def _new_remote_attempt(*, attempt_id: str, request_sha256: str, request_ref: str,
                            start_step: int, target_step: int,
                            resume_checkpoint_id: str | None = None,
                            resume_of_attempt_id: str | None = None) -> dict[str, Any]:
        return {
            "attempt_id": attempt_id,
            "request_sha256": request_sha256,
            "request_ref": request_ref,
            "start_step": start_step,
            "target_step": target_step,
            "resume_checkpoint_id": resume_checkpoint_id,
            "resume_of_attempt_id": resume_of_attempt_id,
            "app_phase": "not_prepared",
            "attempt_spec_sha256": None,
            "attempt_spec_ref": None,
            "app_ref_sha256": None,
            "app_ref_ref": None,
            "handle_sha256": None,
            "handle_ref": None,
            "runtime_evidence_sha256": None,
            "runtime_evidence_ref": None,
            "stop_confirmation": None,
            "provider_error_code": None,
            "submit_intent_at_unix_ns": None,
            "provider_terminal_observed_at_unix_ns": None,
            "local_acceptance_started_at_unix_ns": None,
            "local_acceptance_completed_at_unix_ns": None,
            "core_finalizer_attempt_seconds": None,
            "phase": "submit_intent",
            "provider_terminal": False,
            "terminal_state": None,
            "checkpoint_candidate_sha256": None,
            "validated_checkpoint_id": None,
            "provider_result_sha256": None,
            "provider_result_ref": None,
        }

    def reserve_remote_m0(self, dataset_id: object, *, input_profile: object,
                          request_bytes: bytes, target: dict[str, Any],
                          target_step: object,
                          after_completed_operation_id: object | None = None) -> dict[str, Any]:
        """Persist exact M0 request/target/attempt before any provider call is allowed."""
        if (not isinstance(input_profile, str)
                or input_profile not in {"public_lite", "public_compact"}):
            raise BoundaryError("local_training", "unsupported_input_profile")
        dataset_id = digest(dataset_id, "local_training.dataset_id")
        if type(target_step) is not int:
            raise BoundaryError("local_training", "remote_step_bound_invalid")
        request_identity = _remote_request_identity(request_bytes)
        if (request_identity["target_step"] != target_step
                or request_identity.get("resume_checkpoint_id") is not None):
            raise BoundaryError("local_training", "remote_initial_request_mismatch")
        operation_id = request_identity["operation_id"]
        attempt_id = request_identity["attempt_id"]
        after_completed = (None if after_completed_operation_id is None else
                           digest(after_completed_operation_id,
                                  "local_training.after_completed_operation_id", length=32))
        if not isinstance(target, dict) or set(target) != {
            "target_id", "app_id", "deployment_source_sha256", "gpu",
            "timeout_seconds", "startup_timeout_seconds", "max_total_steps",
        }:
            raise BoundaryError("local_training", "remote_target_invalid")
        try:
            _validate_remote_target(target)
        except (ValueError, KeyError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_training", "remote_target_invalid") from error
        if not 1 <= target_step <= target["max_total_steps"]:
            raise BoundaryError("local_training", "remote_step_bound_invalid")
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        if lock_path.is_symlink():
            raise BoundaryError("local_training", "operation_recovery_required")
        with instance_lock(lock_path):
            previous = self._read(path, owner.identity)
            if previous["status"] in {"pending", "cancelling", "interrupted_unknown",
                                      "paused", "cancelled"}:
                raise BoundaryError("local_training", "previous_training_outcome_unknown")
            if previous["status"] == "failed" and previous.get("run_id"):
                raise BoundaryError("local_training", "previous_training_failed")
            if previous.get("schema") == SCHEMA_V4 and previous["status"] == "failed":
                raise BoundaryError("local_training", "previous_remote_training_failed")
            if previous["status"] == "completed" and previous["dataset_id"] == dataset_id:
                if (previous.get("recipe") == PUBLIC_M0_RECIPE
                        and previous.get("input_profile") == input_profile
                        and after_completed is None):
                    return {"schema": previous["schema"], "availability": "ready",
                            "operation": self._public(previous)}
                if after_completed != previous["operation_id"]:
                    raise BoundaryError("local_training", "new_experiment_precondition_failed")
            if after_completed is not None and (
                previous["status"] != "completed" or previous["dataset_id"] != dataset_id
                or previous["operation_id"] != after_completed
            ):
                raise BoundaryError("local_training", "new_experiment_precondition_failed")
            request_ref, request_sha256 = _persist_remote_request(store, request_bytes)
            attempt = self._new_remote_attempt(
                attempt_id=attempt_id, request_sha256=request_sha256,
                request_ref=request_ref, start_step=0, target_step=target_step,
            )
            operation: dict[str, Any] = {
                "schema": SCHEMA_V4,
                "status": "pending",
                "stage": "remote_submit_intent",
                "operation_id": operation_id,
                "dataset_id": dataset_id,
                "created_at_unix_ns": time.time_ns(),
                "_owner": list(owner.identity),
                "recipe": PUBLIC_M0_RECIPE,
                "input_profile": input_profile,
                "result_type": "evaluated",
                "evaluation_status": "pending",
                "remote": {"target": dict(target), "attempts": [attempt]},
            }
            if previous["status"] == "completed":
                operation["previous_completed"] = {
                    key: previous[key] for key in PREVIOUS_COMPLETED_IDS if key in previous
                }
            try:
                _validate_remote_operation(operation)
            except (ValueError, KeyError, TypeError, BoundaryError) as error:
                raise BoundaryError("local_training", "remote_operation_invalid") from error
            write_replaceable_json(path, operation)
            return {"schema": SCHEMA_V4, "availability": "ready",
                    "operation": self._public(operation)}

    def persist_remote_attempt_spec(
        self, operation_id: object, spec_bytes: bytes,
    ) -> dict[str, Any]:
        """Persist typed M0AttemptSpec bytes before preparing its one-attempt App."""
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        self._require_remote_lock(lock_path)
        with instance_lock(lock_path, create=False):
            current = self._read(path, owner.identity)
            latest = current.get("remote", {}).get("attempts", [{}])[-1]
            if (current.get("schema") != SCHEMA_V4 or current.get("operation_id") != operation_id
                    or current.get("status") not in {"pending", "interrupted_unknown"}
                    or current.get("stage") not in {"remote_submit_intent", "remote_unknown"}
                    or latest.get("phase") != "submit_intent"
                    or latest.get("app_phase") != "not_prepared"):
                raise BoundaryError("local_training", "remote_attempt_spec_not_ready")
            spec = _decode_remote_provider_object(spec_bytes, label="remote_attempt_spec")
            if (set(spec) != {"schema", "attempt_id", "request_sha256", "account_id",
                              "environment_name", "image_object_id", "producer",
                              "target_runtime", "resource_plan", "app_name"}
                    or spec.get("schema") != "stpd/modal-m0-attempt-spec-v1"
                    or spec.get("attempt_id") != latest["attempt_id"]
                    or spec.get("request_sha256") != latest["request_sha256"]
                    or spec.get("app_name") != "stpd-m0-update-" + latest["attempt_id"]):
                raise BoundaryError("local_training", "remote_attempt_spec_identity_mismatch")
            plan = spec.get("resource_plan")
            if (not isinstance(plan, dict)
                    or plan.get("gpu") != current["remote"]["target"]["gpu"]
                    or plan.get("function_timeout_seconds")
                    != current["remote"]["target"]["timeout_seconds"]
                    or plan.get("startup_timeout_seconds")
                    != current["remote"]["target"]["startup_timeout_seconds"]):
                raise BoundaryError("local_training", "remote_attempt_spec_resource_mismatch")
            ref, sha256 = _persist_remote_control(store, spec_bytes, kind="attempt-spec")
            attempts = list(current["remote"]["attempts"])
            attempts[-1] = {**latest, "attempt_spec_ref": ref,
                            "attempt_spec_sha256": sha256,
                            # After this durable point, a restarted controller may only
                            # resolve the deterministic name; it must never prepare again.
                            "app_phase": "prepare_intent"}
            updated = {**current, "remote": {**current["remote"], "attempts": attempts}}
            try:
                _validate_remote_operation(updated)
            except (ValueError, KeyError, TypeError, BoundaryError) as error:
                raise BoundaryError("local_training", "remote_attempt_spec_invalid") from error
            write_replaceable_json(path, updated)
            return self._public(updated)

    def persist_remote_app_ref(self, operation_id: object, app_ref_bytes: bytes) -> dict[str, Any]:
        """Persist the exact typed AppRef before calling Modal submit."""
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        self._require_remote_lock(lock_path)
        with instance_lock(lock_path, create=False):
            current = self._read(path, owner.identity)
            latest = current.get("remote", {}).get("attempts", [{}])[-1]
            if (current.get("schema") != SCHEMA_V4 or current.get("operation_id") != operation_id
                    or current.get("status") not in {"pending", "interrupted_unknown"}
                    or current.get("stage") not in {"remote_submit_intent", "remote_unknown"}
                    or latest.get("phase") != "submit_intent"
                    or latest.get("app_phase") != "prepare_intent"
                    or latest.get("app_ref_ref") is not None):
                raise BoundaryError("local_training", "remote_app_ref_not_ready")
            spec_bytes, _, _ = _remote_provider_bytes(store, latest)
            if spec_bytes is None:
                raise BoundaryError("local_training", "remote_attempt_spec_unavailable")
            spec = _decode_remote_provider_object(spec_bytes, label="remote_attempt_spec")
            app_ref = _decode_remote_provider_object(app_ref_bytes, label="remote_app_ref")
            if (set(app_ref) != {"schema", "account_id", "environment_name", "app_id",
                                 "app_version", "function_id", "image_object_id", "producer",
                                 "attempt_id", "request_sha256", "target_runtime",
                                 "resource_plan", "app_name", "function_name"}
                    or app_ref.get("schema") != "stpd/modal-m0-app-ref-v1"
                    or app_ref.get("attempt_id") != latest["attempt_id"]
                    or app_ref.get("request_sha256") != latest["request_sha256"]
                    or app_ref.get("account_id") != spec.get("account_id")
                    or app_ref.get("environment_name") != spec.get("environment_name")
                    or app_ref.get("image_object_id") != spec.get("image_object_id")
                    or app_ref.get("producer") != spec.get("producer")
                    or app_ref.get("target_runtime") != spec.get("target_runtime")
                    or app_ref.get("resource_plan") != spec.get("resource_plan")
                    or app_ref.get("app_name") != spec.get("app_name")
                    or app_ref.get("function_name") != "token_remote_update"):
                raise BoundaryError("local_training", "remote_app_ref_identity_mismatch")
            ref, sha256 = _persist_remote_control(store, app_ref_bytes, kind="app-ref")
            attempts = list(current["remote"]["attempts"])
            attempts[-1] = {**latest, "app_ref_ref": ref, "app_ref_sha256": sha256,
                            "app_phase": "app_pinned",
                            "submit_intent_at_unix_ns": time.time_ns()}
            updated = {**current, "remote": {**current["remote"], "attempts": attempts}}
            try:
                _validate_remote_operation(updated)
            except (ValueError, KeyError, TypeError, BoundaryError) as error:
                raise BoundaryError("local_training", "remote_app_ref_invalid") from error
            write_replaceable_json(path, updated)
            return self._public(updated)

    def remote_provider_evidence(self, operation_id: object) -> dict[str, bytes | None]:
        """Read back exact saved provider bytes for explicit poll/reconcile/cancel calls."""
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        self._require_remote_lock(lock_path)
        with instance_lock(lock_path, create=False):
            current = self._read(path, owner.identity)
            if current.get("schema") != SCHEMA_V4 or current.get("operation_id") != operation_id:
                raise BoundaryError("local_training", "remote_operation_not_found")
            spec_bytes, app_ref_bytes, handle_bytes = _remote_provider_bytes(
                store, current["remote"]["attempts"][-1],
            )
            return {"attempt_spec_bytes": spec_bytes, "app_ref_bytes": app_ref_bytes,
                    "handle_bytes": handle_bytes,
                    "runtime_evidence_bytes": _remote_runtime_evidence_bytes(
                        store, current["remote"]["attempts"][-1],
                    )}

    def resume_remote_m0(self, operation_id: object, *, request_bytes: bytes,
                         target_step: object) -> dict[str, Any]:
        """Start a fresh explicit attempt only from a provider-terminal verified checkpoint."""
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        if type(target_step) is not int:
            raise BoundaryError("local_training", "remote_step_bound_invalid")
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        self._require_remote_lock(lock_path)
        with instance_lock(lock_path, create=False):
            current = self._read(path, owner.identity)
            if (current.get("schema") != SCHEMA_V4 or current.get("operation_id") != operation_id
                    or current.get("status") not in {"paused", "cancelled"}
                    or not current.get("checkpoint_id")):
                raise BoundaryError("local_training", "verified_remote_checkpoint_required")
            request_identity = _remote_request_identity(request_bytes)
            if (request_identity["operation_id"] != operation_id
                    or request_identity["target_step"] != target_step):
                raise BoundaryError("local_training", "remote_resume_request_mismatch")
            previous_attempt = current["remote"]["attempts"][-1]
            if (request_identity["attempt_id"] == previous_attempt["attempt_id"]
                    or request_identity.get("resume_checkpoint_id")
                    != current.get("checkpoint_id")):
                raise BoundaryError("local_training", "remote_resume_checkpoint_mismatch")
            if (previous_attempt["phase"] != "terminal"
                    or not previous_attempt["provider_terminal"]
                    or previous_attempt["terminal_state"] not in {"paused", "cancelled"}):
                raise BoundaryError("local_training", "provider_terminal_required_for_resume")
            request_ref, request_sha256 = _persist_remote_request(store, request_bytes)
            attempt = self._new_remote_attempt(
                attempt_id=request_identity["attempt_id"], request_sha256=request_sha256,
                request_ref=request_ref, start_step=current["checkpoint_step"],
                target_step=target_step,
                resume_checkpoint_id=current["checkpoint_id"],
                resume_of_attempt_id=previous_attempt["attempt_id"],
            )
            updated = {**current, "status": "pending", "stage": "remote_submit_intent",
                       "remote": {**current["remote"],
                                  "attempts": [*current["remote"]["attempts"], attempt]}}
            updated.pop("stopped_at_unix_ns", None)
            updated.pop("total_wall_seconds", None)
            try:
                _validate_remote_operation(updated)
            except (ValueError, KeyError, TypeError, BoundaryError) as error:
                raise BoundaryError("local_training", "remote_resume_invalid") from error
            write_replaceable_json(path, updated)
            return {"schema": SCHEMA_V4, "availability": "ready",
                    "operation": self._public(updated)}

    def _remote_finalization_evidence(self, operation_id: object) -> dict[str, Any]:
        """Internal only: recover exact request/result bytes and pinned handle for core."""
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        self._require_remote_lock(lock_path)
        with instance_lock(lock_path, create=False):
            current = self._read(path, owner.identity)
            if (current.get("schema") != SCHEMA_V4 or current.get("operation_id") != operation_id
                    or current.get("status") != "pending"
                    or current.get("stage") != "remote_acceptance"):
                raise BoundaryError("local_training", "remote_candidate_not_ready")
            latest = current["remote"]["attempts"][-1]
            if (latest["phase"] != "terminal" or not latest["provider_terminal"]
                    or latest["terminal_state"] not in {"paused", "completed"}
                    or latest["provider_result_ref"] is None):
                raise BoundaryError("local_training", "remote_candidate_not_ready")
            request_bytes, result_bytes = _read_remote_evidence(store, current)
            spec_bytes, app_ref_bytes, handle_bytes = _remote_provider_bytes(store, latest)
            if latest["local_acceptance_started_at_unix_ns"] is None:
                attempts = list(current["remote"]["attempts"])
                attempts[-1] = {**latest,
                                "local_acceptance_started_at_unix_ns": time.time_ns()}
                current = {**current,
                           "remote": {**current["remote"], "attempts": attempts}}
                _validate_remote_operation(current)
                write_replaceable_json(path, current)
                latest = attempts[-1]
            return {
                "operation_id": operation_id,
                "target": dict(current["remote"]["target"]),
                "attempt": {key: latest[key] for key in (
                    "attempt_id", "request_sha256", "start_step", "target_step",
                    "resume_checkpoint_id", "resume_of_attempt_id",
                    "provider_result_sha256", "runtime_evidence_sha256", "terminal_state",
                )},
                "attempt_spec_bytes": spec_bytes,
                "app_ref_bytes": app_ref_bytes,
                "provider_handle_bytes": handle_bytes,
                "provider_runtime_evidence_bytes": _remote_runtime_evidence_bytes(
                    store, latest,
                ),
                "request_bytes": request_bytes,
                "result_bytes": result_bytes,
            }

    def accept_remote_checkpoint(
        self, operation_id: object, checkpoint_id: object,
    ) -> dict[str, Any]:
        """Mark a pause resumable only after core-validated checkpoint bytes are local."""
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        checkpoint_id = digest(checkpoint_id, "local_training.checkpoint_id")
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        self._require_remote_lock(lock_path)
        with instance_lock(lock_path, create=False):
            current = self._read(path, owner.identity)
            if (current.get("schema") != SCHEMA_V4 or current.get("operation_id") != operation_id
                    or current.get("status") != "pending"
                    or current.get("stage") != "remote_acceptance"):
                raise BoundaryError("local_training", "remote_checkpoint_not_ready")
            latest = current["remote"]["attempts"][-1]
            if (latest["phase"] != "terminal" or not latest["provider_terminal"]
                    or latest["terminal_state"] != "paused"):
                raise BoundaryError("local_training", "remote_checkpoint_not_ready")
            request_bytes, result_bytes = _read_remote_evidence(store, current)
            try:
                request_record = json.loads(request_bytes)
                result_record = json.loads(result_bytes)
                checkpoint = store.get_manifest(checkpoint_id)
                checkpoint_info = checkpoint.parameters.value()
                parents = {parent.role: parent.artifact_id for parent in checkpoint.parents}
                payload = checkpoint.payload("checkpoint")
                result_producer = result_record.get("producer")
                if (not isinstance(request_record, dict) or not isinstance(result_record, dict)
                        or request_record.get("attempt_id") != latest["attempt_id"]
                        or result_record.get("attempt_id") != latest["attempt_id"]
                        or result_record.get("request_sha256") != latest["request_sha256"]
                        or result_record.get("operation_id") != operation_id
                        or result_record.get("checkpoint_step") != latest["target_step"]
                        or result_record.get("checkpoint_sha256")
                        != latest["checkpoint_candidate_sha256"]
                        or not isinstance(result_producer, dict)
                        or checkpoint.kind != "checkpoint"
                        or checkpoint.producer.to_dict() != result_producer
                        or set(parents) != {"run", "training_input"}
                        or parents["run"] != result_record.get("run_id")
                        or parents["training_input"] != result_record.get("input_id")
                        or checkpoint_info.get("schema")
                        != "stpd/stage1a-light-action-m0-checkpoint-v1"
                        or checkpoint_info.get("step") != latest["target_step"]
                        or [item.role for item in checkpoint.payloads] != ["checkpoint"]
                        or payload.sha256 != latest["checkpoint_candidate_sha256"]):
                    raise ValueError
            except (BoundaryError, OSError, ValueError, KeyError, TypeError) as error:
                raise BoundaryError(
                    "local_training", "remote_checkpoint_local_validation_failed"
                ) from error
            attempts = list(current["remote"]["attempts"])
            acceptance_completed = max(time.time_ns(),
                                       latest["local_acceptance_started_at_unix_ns"] or 0)
            attempts[-1] = {
                **latest,
                "validated_checkpoint_id": checkpoint_id,
                "local_acceptance_completed_at_unix_ns": acceptance_completed,
            }
            updated = {**current, "status": "paused", "stage": "remote_paused",
                       "checkpoint_id": checkpoint_id,
                       "checkpoint_step": latest["target_step"],
                       "remote": {**current["remote"], "attempts": attempts}}
            try:
                _validate_remote_operation(updated)
            except (ValueError, KeyError, TypeError, BoundaryError) as error:
                raise BoundaryError("local_training", "remote_checkpoint_invalid") from error
            write_replaceable_json(path, updated)
            return self._public(updated)

    def request_remote_cancel(self, operation_id: object) -> dict[str, Any]:
        """Request cancellation; only a provider terminal observation confirms it."""
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        owner, _, _ = self._selected()
        path, lock_path = self._paths(owner)
        self._require_remote_lock(lock_path)
        with instance_lock(lock_path, create=False):
            current = self._read(path, owner.identity)
            if (current.get("schema") != SCHEMA_V4 or current.get("operation_id") != operation_id
                    or current.get("status") not in {"pending", "cancelling",
                                                      "interrupted_unknown"}):
                raise BoundaryError("local_training", "remote_cancel_unavailable")
            latest = current["remote"]["attempts"][-1]
            if latest["phase"] == "cancelling":
                return self._public(current)
            if latest["phase"] != "running" or latest["handle_ref"] is None:
                raise BoundaryError("local_training", "remote_handle_unavailable")

            def update(operation: dict[str, Any]) -> dict[str, Any]:
                attempts = list(operation["remote"]["attempts"])
                attempts[-1] = {**attempts[-1], "phase": "cancelling"}
                return {**operation, "status": "cancelling", "stage": "remote_cancelling",
                        "remote": {**operation["remote"], "attempts": attempts}}

            updated = self._write_remote(path, owner.identity, operation_id, update)
            return self._public(updated)

    def record_remote_preflight_failure(
        self, operation_id: object, *, error_code: str,
    ) -> dict[str, Any]:
        """Record a confirmed pre-spawn failure without inventing a provider handle."""
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        if not isinstance(error_code, str) or not error_code:
            raise BoundaryError("local_training", "remote_failure_code_required")
        owner, _, _ = self._selected()
        path, lock_path = self._paths(owner)
        self._require_remote_lock(lock_path)
        with instance_lock(lock_path, create=False):
            current = self._read(path, owner.identity)
            if (current.get("schema") != SCHEMA_V4 or current.get("operation_id") != operation_id
                    or current.get("status") not in {"pending", "interrupted_unknown"}
                    or current.get("stage") not in {"remote_submit_intent", "remote_unknown"}):
                raise BoundaryError("local_training", "remote_preflight_unavailable")
            latest = current["remote"]["attempts"][-1]
            if (latest["phase"] != "submit_intent" or latest["handle_ref"] is not None
                    or latest["app_phase"] != "not_prepared"):
                raise BoundaryError("local_training", "remote_submit_already_started")
            attempts = list(current["remote"]["attempts"])
            attempts[-1] = {**latest, "phase": "preflight_failed", "terminal_state": "failed"}
            stopped_at = max(time.time_ns(), current["created_at_unix_ns"])
            updated = {**current, "status": "failed", "stage": "remote_failed",
                       "error_code": error_code,
                       "stopped_at_unix_ns": stopped_at,
                       "total_wall_seconds": max(
                           0.0, (stopped_at - current["created_at_unix_ns"]) / 1_000_000_000,
                       ),
                       "remote": {**current["remote"], "attempts": attempts}}
            try:
                _validate_remote_operation(updated)
            except (ValueError, KeyError, TypeError, BoundaryError) as error:
                raise BoundaryError("local_training", "remote_preflight_failure_invalid") from error
            write_replaceable_json(path, updated)
            return self._public(updated)

    def record_remote_prepare_absent(
        self, operation_id: object, *, expected_attempt_spec_sha256: str,
    ) -> dict[str, Any]:
        """Close an interrupted prepare after complete current exact-name lookup is absent.

        The controller must make this transition only after a complete, successful
        lookup in the workspace bound to the immutable attempt spec. This method
        rechecks the exact spec digest and the journal's no-AppRef/no-submit boundary.
        It records a bounded current lookup result, not a claim that the App never existed.
        """
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        expected_attempt_spec_sha256 = digest(
            expected_attempt_spec_sha256,
            "local_training.expected_attempt_spec_sha256",
        )
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        self._require_remote_lock(lock_path)
        with instance_lock(lock_path, create=False):
            current = self._read(path, owner.identity)
            if (current.get("schema") != SCHEMA_V4
                    or current.get("operation_id") != operation_id
                    or current.get("status") != "interrupted_unknown"
                    or current.get("stage") != "remote_unknown"):
                raise BoundaryError("local_training", "remote_prepare_absence_unavailable")
            latest = current["remote"]["attempts"][-1]
            if (latest["phase"] != "submit_intent"
                    or latest["app_phase"] != "prepare_intent"
                    or latest["attempt_spec_ref"] is None
                    or latest["app_ref_ref"] is not None
                    or latest["handle_ref"] is not None
                    or latest["submit_intent_at_unix_ns"] is not None
                    or latest["provider_terminal"]):
                raise BoundaryError("local_training", "remote_prepare_absence_unproven")
            spec_bytes, app_ref_bytes, handle_bytes = _remote_provider_bytes(store, latest)
            if (spec_bytes is None or app_ref_bytes is not None or handle_bytes is not None
                    or hashlib.sha256(spec_bytes).hexdigest()
                    != expected_attempt_spec_sha256):
                raise BoundaryError("local_training", "remote_prepare_spec_changed")
            attempts = list(current["remote"]["attempts"])
            attempts[-1] = {
                **latest,
                "phase": "preflight_failed",
                "terminal_state": "failed",
                "provider_error_code": "provider_app_absent_before_submit",
            }
            stopped_at = max(time.time_ns(), current["created_at_unix_ns"])
            updated = {
                **current,
                "status": "failed",
                "stage": "remote_failed",
                "error_code": "provider_app_absent_before_submit",
                "stopped_at_unix_ns": stopped_at,
                "total_wall_seconds": max(
                    0.0,
                    (stopped_at - current["created_at_unix_ns"]) / 1_000_000_000,
                ),
                "remote": {**current["remote"], "attempts": attempts},
            }
            try:
                _validate_remote_operation(updated)
            except (ValueError, KeyError, TypeError, BoundaryError) as error:
                raise BoundaryError(
                    "local_training", "remote_prepare_absence_invalid",
                ) from error
            write_replaceable_json(path, updated)
            return self._public(updated)

    def record_remote_observation(
        self, operation_id: object, *, state: str, provider_terminal: bool,
        handle_bytes: bytes | None = None,
        checkpoint_candidate_sha256: str | None = None,
        provider_result_bytes: bytes | None = None,
        runtime_evidence_bytes: bytes | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        """Persist the provider observation before separate stop verification."""
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        self._require_remote_lock(lock_path)
        if state not in {"running", *REMOTE_TERMINAL_STATES, "unknown"}:
            raise BoundaryError("local_training", "remote_observation_invalid")
        if type(provider_terminal) is not bool:
            raise BoundaryError("local_training", "remote_terminal_observation_required")
        with instance_lock(lock_path, create=False):
            current = self._read(path, owner.identity)
            if (current.get("schema") != SCHEMA_V4 or current.get("operation_id") != operation_id
                    or current.get("status") not in {"pending", "cancelling",
                                                      "interrupted_unknown"}):
                raise BoundaryError("local_training", "remote_observation_unavailable")
            latest = current["remote"]["attempts"][-1]
            if latest["phase"] == "terminal":
                raise BoundaryError("local_training", "remote_attempt_already_terminal")
            if latest["phase"] == "stopping":
                raise BoundaryError("local_training", "remote_stop_confirmation_pending")
            handle_ref, handle_sha256 = latest["handle_ref"], latest["handle_sha256"]
            if handle_bytes is not None:
                if latest["app_ref_ref"] is None or latest["attempt_spec_ref"] is None:
                    raise BoundaryError("local_training", "remote_app_ref_required_before_submit")
                app_ref_bytes = _read_remote_control(
                    store, latest["app_ref_ref"], latest["app_ref_sha256"], kind="app-ref",
                )
                app_ref = _decode_remote_provider_object(app_ref_bytes, label="remote_app_ref")
                call_handle = _decode_remote_provider_object(
                    handle_bytes, label="remote_call_handle",
                )
                app_target = {key: item for key, item in app_ref.items() if key != "schema"}
                if (set(call_handle) != {"schema", "target", "target_id", "target_runtime",
                                        "call_id", "request_sha256", "request_size_bytes",
                                        "attempt_id", "run_id", "input_id", "operation_id",
                                        "producer", "result_identity_sha256", "target_device",
                                        "target_step", "resume_checkpoint_id"}
                        or call_handle.get("schema") != "stpd/modal-m0-call-v3"
                        or call_handle.get("attempt_id") != latest["attempt_id"]
                        or call_handle.get("request_sha256") != latest["request_sha256"]
                        or call_handle.get("target") != app_target
                        or call_handle.get("operation_id") != operation_id
                        or call_handle.get("target_step") != latest["target_step"]
                        or call_handle.get("resume_checkpoint_id")
                        != latest["resume_checkpoint_id"]
                        or call_handle.get("target_device") != "cuda"
                        or type(call_handle.get("request_size_bytes")) is not int
                        or not 1 <= call_handle["request_size_bytes"]
                        <= REMOTE_MAX_REQUEST_BYTES):
                    raise BoundaryError("local_training", "remote_handle_identity_mismatch")
                handle_ref, handle_sha256 = _persist_remote_control(
                    store, handle_bytes, kind="call-handle",
                )
            app_phase = "submitted" if handle_ref is not None else latest["app_phase"]
            if state == "running" and provider_terminal:
                raise BoundaryError("local_training", "remote_observation_invalid")
            if state == "unknown":
                if provider_terminal:
                    raise BoundaryError("local_training", "remote_observation_invalid")
                phase = latest["phase"]
                if phase == "submit_intent" and handle_ref is not None:
                    phase = "running"
                status, stage = "interrupted_unknown", "remote_unknown"
                terminal_state = None
                error_code = error_code or "provider_outcome_unknown"
            elif state == "running":
                if handle_ref is None:
                    raise BoundaryError("local_training", "remote_handle_required")
                phase = ("cancelling" if latest["phase"] == "cancelling"
                         or current["status"] == "cancelling" else "running")
                status = "cancelling" if phase == "cancelling" else "pending"
                stage = "remote_cancelling" if phase == "cancelling" else "remote_running"
                terminal_state = None
            else:
                if not provider_terminal or handle_ref is None:
                    raise BoundaryError("local_training", "provider_terminal_required")
                phase = "stopping"
                terminal_state = state
                # The app must also be stopped and independently inspected after
                # the call result is observed; only then may a terminal status publish.
                status, stage = "pending", "remote_stopping"
                if state in {"paused", "completed"} and (
                    provider_result_bytes is None or checkpoint_candidate_sha256 is None
                ):
                    raise BoundaryError("local_training", "remote_candidate_required")
                if state == "failed" and not isinstance(error_code, str):
                    raise BoundaryError("local_training", "remote_failure_code_required")

            provider_result_ref = latest["provider_result_ref"]
            provider_result_sha256 = latest["provider_result_sha256"]
            if provider_result_bytes is not None:
                provider_result_ref, provider_result_sha256 = _persist_remote_result(
                    store, provider_result_bytes,
                )
            runtime_evidence_ref = latest["runtime_evidence_ref"]
            runtime_evidence_sha256 = latest["runtime_evidence_sha256"]
            if runtime_evidence_bytes is not None:
                runtime_evidence_ref, runtime_evidence_sha256 = _persist_remote_control(
                    store, runtime_evidence_bytes, kind="runtime-evidence",
                )
            updated_attempt = {**latest, "phase": phase,
                               "app_phase": app_phase,
                               "provider_terminal": provider_terminal,
                               "terminal_state": terminal_state,
                               "provider_result_ref": provider_result_ref,
                               "provider_result_sha256": provider_result_sha256,
                               "runtime_evidence_ref": runtime_evidence_ref,
                               "runtime_evidence_sha256": runtime_evidence_sha256,
                               "handle_ref": handle_ref,
                               "handle_sha256": handle_sha256,
                               "provider_terminal_observed_at_unix_ns": (
                                   time.time_ns() if provider_terminal
                                   else latest["provider_terminal_observed_at_unix_ns"]
                               ),
                               "stop_confirmation": (None if provider_terminal else
                                                     latest["stop_confirmation"]),
                               "provider_error_code": (error_code if state == "failed"
                                                       else latest["provider_error_code"])}
            if checkpoint_candidate_sha256 is not None:
                updated_attempt["checkpoint_candidate_sha256"] = checkpoint_candidate_sha256
            attempts = list(current["remote"]["attempts"])
            attempts[-1] = updated_attempt
            updated: dict[str, Any] = {**current, "status": status, "stage": stage,
                                       "remote": {**current["remote"], "attempts": attempts}}
            if status == "failed":
                updated["error_code"] = error_code
            if status == "interrupted_unknown":
                updated["error_code"] = error_code
            try:
                _validate_remote_operation(updated)
            except (ValueError, KeyError, TypeError, BoundaryError) as error:
                raise BoundaryError("local_training", "remote_observation_invalid") from error
            write_replaceable_json(path, updated)
            return self._public(updated)

    def record_remote_stop_confirmation(
        self, operation_id: object, inspection: dict[str, Any],
    ) -> dict[str, Any]:
        """Require an exact per-attempt App inspection with zero tasks and containers."""
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        self._require_remote_lock(lock_path)
        with instance_lock(lock_path, create=False):
            current = self._read(path, owner.identity)
            if (current.get("schema") != SCHEMA_V4
                    or current.get("operation_id") != operation_id
                    or current.get("status") not in {"pending", "interrupted_unknown"}
                    or current.get("stage") not in {"remote_stopping", "remote_unknown"}):
                raise BoundaryError("local_training", "remote_stop_confirmation_unavailable")
            latest = current["remote"]["attempts"][-1]
            if latest["phase"] != "stopping" or not latest["provider_terminal"]:
                raise BoundaryError("local_training", "provider_terminal_required")
            _, app_ref_bytes, _ = _remote_provider_bytes(store, latest)
            if app_ref_bytes is None:
                raise BoundaryError("local_training", "remote_app_ref_unavailable")
            app_ref = _decode_remote_provider_object(app_ref_bytes, label="remote_app_ref")
            if (not isinstance(inspection, dict)
                    or set(inspection) != {"app_id", "app_state", "active_tasks",
                                           "active_containers", "confirmed"}
                    or inspection.get("app_id") != app_ref.get("app_id")
                    or inspection.get("app_state") != "stopped"
                    or inspection.get("active_tasks") != 0
                    or inspection.get("active_containers") != 0
                    or inspection.get("confirmed") is not True):
                raise BoundaryError("local_training", "provider_stop_confirmation_invalid")
            attempts = list(current["remote"]["attempts"])
            attempts[-1] = {**latest, "phase": "terminal",
                            "stop_confirmation": dict(inspection)}
            terminal_state = latest["terminal_state"]
            status, stage = {
                "paused": ("pending", "remote_acceptance"),
                "cancelled": ("cancelled", "remote_cancelled"),
                "completed": ("pending", "remote_acceptance"),
                "failed": ("failed", "remote_failed"),
            }[terminal_state]
            updated = {**current, "status": status, "stage": stage,
                       "remote": {**current["remote"], "attempts": attempts}}
            if terminal_state == "failed":
                updated["error_code"] = latest["provider_error_code"]
            elif status != "interrupted_unknown":
                updated.pop("error_code", None)
            if status in {"cancelled", "failed"}:
                stopped_at = max(time.time_ns(), current["created_at_unix_ns"])
                updated["stopped_at_unix_ns"] = stopped_at
                updated["total_wall_seconds"] = max(
                    0.0, (stopped_at - current["created_at_unix_ns"]) / 1_000_000_000,
                )
            try:
                _validate_remote_operation(updated)
            except (ValueError, KeyError, TypeError, BoundaryError) as error:
                raise BoundaryError("local_training", "remote_stop_confirmation_invalid") from error
            write_replaceable_json(path, updated)
            return self._public(updated)

    def record_remote_unknown_app_stopped(
        self, operation_id: object, inspection: dict[str, Any],
    ) -> dict[str, Any]:
        """Record App stop proof while preserving an ambiguous submitted-call outcome."""
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        self._require_remote_lock(lock_path)
        with instance_lock(lock_path, create=False):
            current = self._read(path, owner.identity)
            if (current.get("schema") != SCHEMA_V4
                    or current.get("operation_id") != operation_id
                    or current.get("status") != "interrupted_unknown"
                    or current.get("stage") != "remote_unknown"):
                raise BoundaryError("local_training", "remote_unknown_stop_unavailable")
            latest = current["remote"]["attempts"][-1]
            if latest["provider_terminal"] or latest["app_ref_ref"] is None:
                raise BoundaryError("local_training", "remote_unknown_stop_unavailable")
            _, app_ref_bytes, _ = _remote_provider_bytes(store, latest)
            if app_ref_bytes is None:
                raise BoundaryError("local_training", "remote_app_ref_unavailable")
            app_ref = _decode_remote_provider_object(app_ref_bytes, label="remote_app_ref")
            if (not isinstance(inspection, dict)
                    or set(inspection) != {"app_id", "app_state", "active_tasks",
                                           "active_containers", "confirmed"}
                    or inspection.get("app_id") != app_ref.get("app_id")
                    or inspection.get("app_state") != "stopped"
                    or inspection.get("active_tasks") != 0
                    or inspection.get("active_containers") != 0
                    or inspection.get("confirmed") is not True):
                raise BoundaryError("local_training", "provider_stop_confirmation_invalid")
            attempts = list(current["remote"]["attempts"])
            attempts[-1] = {**latest, "stop_confirmation": dict(inspection)}
            updated = {**current, "remote": {**current["remote"], "attempts": attempts}}
            try:
                _validate_remote_operation(updated)
            except (ValueError, KeyError, TypeError, BoundaryError) as error:
                raise BoundaryError("local_training", "remote_unknown_stop_invalid") from error
            write_replaceable_json(path, updated)
            return self._public(updated)

    def accept_remote_candidate(self, operation_id: object, *, input_id: object,
                                run_id: object, result_id: object,
                                core_finalizer_attempt_seconds: object | None = None
                                ) -> dict[str, Any]:
        """Accept only a candidate already validated and published in the local artifact store."""
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        input_id = digest(input_id, "local_training.input_id")
        run_id = digest(run_id, "local_training.run_id")
        result_id = digest(result_id, "local_training.result_id")
        if core_finalizer_attempt_seconds is not None and (
            isinstance(core_finalizer_attempt_seconds, bool)
            or not isinstance(core_finalizer_attempt_seconds, (int, float))
            or not math.isfinite(core_finalizer_attempt_seconds)
            or core_finalizer_attempt_seconds < 0
        ):
            raise BoundaryError("local_training", "core_finalizer_time_invalid")
        owner, store, registry_path = self._selected()
        path, lock_path = self._paths(owner)
        self._require_remote_lock(lock_path)
        with instance_lock(lock_path, create=False):
            current = self._read(path, owner.identity)
            if (current.get("schema") != SCHEMA_V4 or current.get("operation_id") != operation_id
                    or current.get("status") != "pending"
                    or current.get("stage") != "remote_acceptance"):
                raise BoundaryError("local_training", "remote_candidate_not_ready")
            latest = current["remote"]["attempts"][-1]
            if (latest["phase"] != "terminal" or not latest["provider_terminal"]
                    or latest["terminal_state"] != "completed"):
                raise BoundaryError("local_training", "remote_candidate_not_ready")
            try:
                from spireagent.storage.registry import SQLiteRegistry, sync_registry
                from spireagent.storage.run_reporter import ObjectStoreRunReporter
                from stpd.fullrun.light_action_inputs import (
                    PUBLIC_SCHEMA,
                    public_training_binding,
                )
                from stpd.workers.token_worker import _verify_completed

                training_input = store.get_manifest(input_id)
                binding = public_training_binding(store, input_id)
                run = store.get_manifest(run_id)
                result = store.get_manifest(result_id)
                completed = ObjectStoreRunReporter(store, store.blobs).completed(run_id)
                if (training_input.parameters.value().get("schema") != PUBLIC_SCHEMA
                        or binding.get("dataset_ids") != [current["dataset_id"]]
                        or binding.get("training_operation_id") != operation_id
                        or run.parent("training_input") != input_id
                        or run.parameters.value().get("training_binding") != binding
                        or completed is None or completed.artifact_id != result_id
                        or result.parent("model") != completed.parent("model")):
                    raise ValueError
                _verify_completed(store, completed, run)
                model_id = completed.parent("model")
                model = store.get_manifest(model_id)
                checkpoint_id = model.parent("checkpoint")
                checkpoint = store.get_manifest(checkpoint_id)
                checkpoint_info = checkpoint.parameters.value()
                if (model.parameters.value().get("schema")
                        != "stpd/stage1a-light-action-m0-public-model-v1"
                        or checkpoint.kind != "checkpoint"
                        or checkpoint.parent("run") != run_id
                        or checkpoint.parent("training_input") != input_id
                        or checkpoint_info.get("step") != latest["target_step"]
                        or [payload.role for payload in checkpoint.payloads] != ["checkpoint"]
                        or checkpoint.payload("checkpoint").sha256
                        != latest["checkpoint_candidate_sha256"]):
                    raise ValueError
                identifiers = {
                    "input_id": input_id,
                    "run_id": run_id,
                    "checkpoint_id": checkpoint_id,
                    "result_id": result_id,
                    "model_id": model_id,
                    "evaluation_id": completed.parent("offline_evaluation"),
                }
                sync_registry(store, SQLiteRegistry(registry_path))
            except (BoundaryError, OSError, ValueError, KeyError, TypeError) as error:
                raise BoundaryError(
                    "local_training", "remote_candidate_local_validation_failed"
                ) from error
            completed_at = max(time.time_ns(),
                               latest["local_acceptance_started_at_unix_ns"] or 0,
                               current["created_at_unix_ns"])
            updated = {**current, **identifiers, "status": "completed", "stage": "completed",
                       "evaluation_status": "completed",
                       "checkpoint_step": latest["target_step"],
                       "completed_at_unix_ns": completed_at,
                       "total_wall_seconds": max(
                           0.0, (completed_at - current["created_at_unix_ns"]) / 1_000_000_000,
                       )}
            attempts = list(current["remote"]["attempts"])
            attempts[-1] = {
                **latest,
                "validated_checkpoint_id": identifiers["checkpoint_id"],
                "local_acceptance_completed_at_unix_ns": completed_at,
                "core_finalizer_attempt_seconds": core_finalizer_attempt_seconds,
            }
            updated["remote"] = {**current["remote"], "attempts": attempts}
            try:
                _validate_remote_operation(updated)
            except (ValueError, KeyError, TypeError, BoundaryError) as error:
                raise BoundaryError("local_training", "remote_acceptance_invalid") from error
            write_replaceable_json(path, updated)
            return self._public(updated)

    def _run_public_m0(self, operation: dict[str, Any], path: Path, identity: str,
                       owner: LocalCurationOwner, store: ManifestArtifactStore,
                       on_started: Any, on_finished: Any) -> None:
        if (self.config_path is None or self.config_path.is_symlink()
                or not self.config_path.is_file()):
            raise BoundaryError("local_training", "configured_local_workspace_required")
        profile = operation.get("input_profile")
        if profile not in {"public_lite", "public_compact"}:
            raise BoundaryError("local_training", "unsupported_input_profile")
        root = getattr(getattr(store, "blobs", None), "root", None)
        if not isinstance(root, Path):
            raise BoundaryError("local_training", "unsupported_workspace_store")
        environment = dict(os.environ)
        for name in ("STPD_HUB_ADMIN_TOKEN", "PYTHONPATH", "PYTHONHOME"):
            environment.pop(name, None)

        self._advance(path, identity, stage="allocating")
        prepare_command = [
            sys.executable, "-m", "spireagent.research_cli", "--store", str(root),
            "prepare-light-action-m0", "--project-config", str(self.config_path),
            "--dataset", operation["dataset_id"], "--operation", identity,
            "--backbone", "s", "--input-profile", profile,
        ]
        prepare_log = owner.path.parent / ("local-training-" + identity + "-prepare.log")
        prepare_exit, captured = _private_child(prepare_command, prepare_log, environment,
                                                on_started=on_started)
        on_finished(prepare_exit)
        if prepare_exit:
            raise BoundaryError("local_training", "public_m0_preparation_process_failed")
        try:
            prepared = _child_json_record(captured)
            expected = {"allocation_id", "model_view_id", "training_input_id", "input_schema",
                        "backbone", "admission", "counts", "elapsed_seconds", "verification"}
            if (not isinstance(prepared, dict) or set(prepared) != expected
                    or prepared["input_schema"] !=
                    "stpd/stage1a-light-action-m0-public-input-v1"
                    or prepared["backbone"] != "s"):
                raise ValueError
            allocation_id = digest(prepared["allocation_id"], "local_training.allocation_id")
            view_id = digest(prepared["model_view_id"], "local_training.view_id")
            input_id = digest(prepared["training_input_id"], "local_training.input_id")
            allocation = store.get_manifest(allocation_id)
            view = store.get_manifest(view_id)
            training_input = store.get_manifest(input_id)
            from stpd.fullrun.light_action_inputs import PUBLIC_SCHEMA, public_training_binding

            binding = public_training_binding(store, input_id)
            if (allocation.parent("dataset") != operation["dataset_id"]
                    or view.parent("allocation") != allocation_id
                    or training_input.parent("model_view") != view_id
                    or training_input.parameters.value().get("schema") != PUBLIC_SCHEMA
                    or binding.get("dataset_ids") != [operation["dataset_id"]]
                    or binding.get("training_operation_id") != identity
                    or view.parameters.value().get("serializer", {}).get("profile") != profile):
                raise ValueError
        except (BoundaryError, OSError, ValueError, KeyError, TypeError) as error:
            raise BoundaryError("local_training", "public_m0_preparation_result_invalid") from error
        self._advance(path, identity, stage="training", allocation_id=allocation_id,
                      view_id=view_id, input_id=input_id)

        train_command = [
            sys.executable, "-m", "spireagent.research_cli", "--store", str(root),
            "train-light-action-m0", "--project-config", str(self.config_path),
            "--inputs", input_id, "--operation", identity,
            "--recipe", "stage1a.dsimple.light-action.m0.s.v1",
            "--steps", "3", "--backend", "cpu",
        ]
        train_log = owner.path.parent / ("local-training-" + identity + ".log")
        train_exit, trained_output = _private_child(train_command, train_log, environment,
                                                    on_started=on_started)
        on_finished(train_exit)
        if train_exit:
            raise BoundaryError("local_training", "public_m0_training_process_failed")
        try:
            trained = _child_json_record(trained_output)
            if (not isinstance(trained, dict) or trained.get("state") != "completed"
                    or trained.get("training_input_id") != input_id
                    or trained.get("training_binding") != binding):
                raise ValueError
            run_id = digest(trained["run_id"], "local_training.run_id")
            result_id = digest(trained["result_id"], "local_training.result_id")
            run = store.get_manifest(run_id)
            result = store.get_manifest(result_id)
            from spireagent.storage.run_reporter import ObjectStoreRunReporter
            from stpd.workers.token_worker import _verify_completed

            completed = ObjectStoreRunReporter(store, store.blobs).completed(run_id)
            if (completed is None or completed.artifact_id != result_id
                    or run.parent("training_input") != input_id
                    or run.parameters.value().get("training_binding") != binding
                    or result.parent("model") != completed.parent("model")):
                raise ValueError
            _verify_completed(store, completed, run)
            model_id = completed.parent("model")
            model = store.get_manifest(model_id)
            if model.parameters.value().get("schema") != \
                    "stpd/stage1a-light-action-m0-public-model-v1":
                raise ValueError
            completion = {"run_id": run_id, "result_id": result_id, "model_id": model_id,
                          "evaluation_id": completed.parent("offline_evaluation"),
                          "checkpoint_id": model.parent("checkpoint"),
                          "evaluation_status": "completed"}
        except (BoundaryError, OSError, ValueError, KeyError, TypeError) as error:
            raise BoundaryError("local_training", "public_m0_training_result_invalid") from error
        from spireagent.storage.registry import SQLiteRegistry, sync_registry

        sync_registry(store, SQLiteRegistry(self._selected()[2]))
        self._advance(path, identity, stage="completed", status="completed", **completion)

    def start(self, dataset_id: object, *,
              after_completed_operation_id: object | None = None,
              recipe: object = DEFAULT_RECIPE,
              input_profile: object = None) -> dict[str, Any]:
        public_profile = input_profile
        if (public_profile is not None
                and (not isinstance(public_profile, str)
                     or public_profile not in {"public_lite", "public_compact"})):
            raise BoundaryError("local_training", "unsupported_input_profile")
        if public_profile is not None:
            if recipe != DEFAULT_RECIPE:
                raise BoundaryError("local_training", "conflicting_training_profile")
            recipe = PUBLIC_M0_RECIPE
        if (not isinstance(recipe, str)
                or recipe not in {DEFAULT_RECIPE, *MEMORY_RECIPES, PUBLIC_M0_RECIPE}):
            raise BoundaryError("local_training", "unsupported_training_recipe")
        dataset_id = digest(dataset_id, "local_training.dataset_id")
        after_completed = (None if after_completed_operation_id is None else
                           digest(after_completed_operation_id,
                                  "local_training.after_completed_operation_id", length=32))
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        if lock_path.is_symlink():
            raise BoundaryError("local_training", "operation_recovery_required")
        held: AbstractContextManager[None] = instance_lock(lock_path)
        try:
            held.__enter__()
        except BoundaryError as error:
            if error.code == "already_running":
                if after_completed is not None:
                    raise BoundaryError("local_training", "operation_in_progress") from error
                operation = self._read(path, owner.identity)
                if (operation.get("dataset_id") == dataset_id and operation["status"] == "pending"
                        and operation.get("recipe", DEFAULT_RECIPE) == recipe
                        and operation.get("input_profile") == public_profile):
                    return self.status()
                raise BoundaryError("local_training", "operation_in_progress") from error
            raise
        try:
            previous = self._read(path, owner.identity)
            if previous["status"] in {"pending", "cancelling", "interrupted_unknown"}:
                raise BoundaryError("local_training", "previous_training_outcome_unknown")
            if previous.get("schema") == SCHEMA_V4 and previous["status"] in {
                "paused", "cancelled", "failed"
            }:
                raise BoundaryError("local_training", "remote_operation_explicit_action_required")
            if previous["status"] == "failed" and previous.get("run_id"):
                raise BoundaryError("local_training", "previous_training_failed")
            if after_completed is not None and (
                previous["status"] != "completed" or previous["dataset_id"] != dataset_id
                or previous["operation_id"] != after_completed
            ):
                raise BoundaryError("local_training", "new_experiment_precondition_failed")
            if (previous["status"] == "completed" and previous["dataset_id"] == dataset_id
                    and previous.get("recipe", DEFAULT_RECIPE) == recipe
                    and previous.get("input_profile") == public_profile
                    and after_completed is None):
                return self.status()
            if (previous["status"] == "completed" and previous["dataset_id"] == dataset_id
                    and after_completed is None):
                raise BoundaryError("local_training", "new_experiment_precondition_failed")
            require_local_models("local_training")
            identity = uuid.uuid4().hex
            operation = {"schema": (SCHEMA_V3 if public_profile is not None else
                                     SCHEMA_V2 if recipe in MEMORY_RECIPES
                                    or previous.get("schema") == SCHEMA_V2 else SCHEMA),
                         "status": "pending", "stage": "reserving",
                         "operation_id": identity, "dataset_id": dataset_id,
                         "_owner": list(owner.identity)}
            if operation["schema"] == SCHEMA_V3:
                operation.update(
                    recipe=PUBLIC_M0_RECIPE,
                    input_profile=public_profile,
                    result_type="evaluated",
                    evaluation_status="pending",
                )
            elif operation["schema"] == SCHEMA_V2:
                operation.update(
                    recipe=recipe,
                    result_type="train_only" if recipe in MEMORY_RECIPES else "evaluated",
                    evaluation_status="not_run" if recipe in MEMORY_RECIPES else "pending",
                )
            if previous["status"] == "completed":
                if operation["schema"] == SCHEMA_V2 and previous["schema"] == SCHEMA:
                    # Preserve a v1 completion intact when changing recipes.
                    operation["previous_completed"] = {
                        key: previous[key] for key in PREVIOUS_COMPLETED_IDS
                    }
                elif operation["schema"] == SCHEMA_V2:
                    operation["previous_completed"] = {
                        key: previous[key] for key in ("operation_id", "dataset_id", "result_id",
                            "model_id", "recipe", "result_type", "evaluation_status",
                            "checkpoint_id", "input_id", "evaluation_id") if key in previous
                    }
                else:
                    operation["previous_completed"] = {
                        key: previous[key] for key in PREVIOUS_COMPLETED_IDS
                    }
            write_replaceable_json(path, operation)
            thread = threading.Thread(target=self._run, args=(held, path, identity, owner, store),
                                      name="local-small-b-training", daemon=True)
            with self._lock:
                self._failure_diagnostic = None
                self._thread = thread
            thread.start()
            held = None  # type: ignore[assignment]
            return {"schema": operation["schema"], "availability": "ready",
                    "operation": self._public(operation)}
        finally:
            if held is not None:
                held.__exit__(None, None, None)

    def _run(self, held: AbstractContextManager[None], path: Path, identity: str,
             owner: LocalCurationOwner, store: ManifestArtifactStore) -> None:
        child_outcome_unknown = False

        def mark_started() -> None:
            nonlocal child_outcome_unknown
            child_outcome_unknown = True

        def mark_finished(_exit_code: int) -> None:
            nonlocal child_outcome_unknown
            child_outcome_unknown = False

        try:
            from spireagent.storage.registry import SQLiteRegistry, sync_registry
            from spireagent.storage.run_reporter import ObjectStoreRunReporter
            from stpd.fullrun.curated_dataset import SCHEMA as DATASET_SCHEMA
            from stpd.fullrun.curated_dataset import load_selection
            from stpd.fullrun.decision_spool import SpoolSelection
            from stpd.fullrun.decision_training import AllocationSpec, allocate, publish_allocation
            from stpd.fullrun.managed_text_menu_import import (
                SOURCE_SCHEMA as MANAGED_SOURCE_SCHEMA,
            )
            from stpd.fullrun.managed_text_menu_import import (
                load_managed_text_menu_source,
            )
            from stpd.fullrun.public_bc import publish_public_bc_view
            from stpd.fullrun.text_menu_human_import import (
                SOURCE_SCHEMA as HUMAN_SOURCE_SCHEMA,
            )
            from stpd.fullrun.text_menu_human_import import (
                _project as project_human_inputs,
            )
            from stpd.fullrun.text_menu_human_import import (
                load_human_text_source,
                load_verified_human_text_bundle,
                publish_human_text_bc_view,
            )
            from stpd.fullrun.token_inputs import load_token_inputs, publish_token_inputs
            from stpd.workers.token_ranking import TokenConfig
            from stpd.workers.token_worker import _verify_completed, prepare_token_run

            operation = self._read(path, owner.identity)
            dataset_id = operation["dataset_id"]
            if operation.get("schema") == SCHEMA_V3:
                self._run_public_m0(operation, path, identity, owner, store,
                                    mark_started, mark_finished)
                return
            memory = operation.get("recipe") in MEMORY_RECIPES
            producer = source_identity(ROOT)
            manifest = store.get_manifest(dataset_id)
            info = manifest.parameters.value()
            human = info.get("schema") == HUMAN_SOURCE_SCHEMA
            managed = info.get("schema") == MANAGED_SOURCE_SCHEMA
            if managed and operation.get("recipe") not in V2_MEMORY_RECIPES:
                raise BoundaryError("local_training", "managed_v2_recipe_required")
            if human and operation.get("recipe") in V2_MEMORY_RECIPES:
                raise BoundaryError("local_training", "managed_v2_source_required")
            if human:
                source_manifest, rows = load_human_text_source(store, dataset_id)
                if source_manifest != manifest:
                    raise BoundaryError("local_training", "human_source_identity_mismatch")
                # This is the actual engineering split gate; session count alone
                # cannot establish independent groups after duplicate collapse.
                if not memory:
                    samples, _ = project_human_inputs(rows)
                    if (sum(sample.split == "train" for sample in samples) > 32
                            or sum(sample.split == "dev" for sample in samples) > 8):
                        raise BoundaryError("local_training", "human_engineering_sample_limit")
                sources = {parent.artifact_id for parent in manifest.parents}
                runs: set[str] = set()
                for source_id in sources:
                    _, bundle, _ = load_verified_human_text_bundle(store, source_id)
                    runs.update(bundle.session_id + "/" + run for run in bundle.run_ids)
                spec = None
            elif managed:
                managed_source = load_managed_text_menu_source(store, dataset_id)
                if managed_source.manifest != manifest:
                    raise BoundaryError("local_training", "managed_source_identity_mismatch")
                runs = {managed_source.split_run_id}
                sources = {dataset_id}
                spec = None
            else:
                if memory:
                    raise BoundaryError("local_training", "human_training_source_required")
                if (manifest.kind != "dataset" or info.get("schema") != DATASET_SCHEMA
                        or info.get("purpose") != "training" or info.get("merging") is not False
                        or not manifest.parents or len(manifest.parents) > 100
                        or any(p.role != "source_" + p.artifact_id for p in manifest.parents)):
                    raise BoundaryError("local_training", "curated_training_dataset_required")
                dataset = load_selection(store, manifest, cache=None)
                try:
                    runs = {row["run_id"] for row in dataset.records.summaries()} if isinstance(
                        dataset.records, SpoolSelection
                    ) else {record.run_id for record in dataset.records}
                    spec = AllocationSpec(isolation="run", max_train=32, max_dev=8)
                    allocate(dataset, spec)
                finally:
                    if isinstance(dataset.records, SpoolSelection):
                        dataset.records.owner.close()
                sources = {parent.artifact_id for parent in manifest.parents}
            claim = owner.ledger.dataset(dataset_id)
            if claim != ("training", runs):
                raise BoundaryError("local_training", "training_claim_mismatch")
            indexed_runs: set[str] = set()
            for source in sorted(sources):
                source_runs = owner.ledger.source_runs(source)
                if source_runs is None:
                    raise BoundaryError("local_training", "source_index_incomplete")
                indexed_runs.update(source_runs)
            if not runs <= indexed_runs:
                raise BoundaryError("local_training", "source_run_identity_mismatch")
            # All exposure records precede the first derivative publication or model step.
            for source in sorted(sources):
                owner.ledger.use_source(source, "training", identity)
            owner.ledger.use(runs, "training", identity)
            environment = dict(os.environ)
            for name in ("STPD_HUB_ADMIN_TOKEN", "PYTHONPATH", "PYTHONHOME"):
                environment.pop(name, None)
            if memory:
                self._advance(path, identity, stage="tokenizing")
                self._advance(path, identity, stage="preparing_run")
                prepare_command = [sys.executable, "-m", "spireagent.research_cli",
                                   "--store", str(owner.store_dir),
                                   "prepare-workbench-memory", "--source", dataset_id,
                                   "--operation", identity,
                                   "--recipe", operation["recipe"]]
                prepare_log = owner.path.parent / ("local-training-" + identity + "-prepare.log")
                prepare_exit, captured = _private_child(prepare_command, prepare_log,
                                                        environment,
                                                        on_started=mark_started)
                mark_finished(prepare_exit)
                if prepare_exit:
                    failure = None
                    with suppress(ValueError, TypeError):
                        failure = json.loads(captured)
                    if (isinstance(failure, dict) and set(failure) == {"error_code"}
                            and isinstance(failure["error_code"], str)):
                        raise BoundaryError("local_training", failure["error_code"])
                    raise BoundaryError("local_training", "memory_preparation_process_failed")
                prepared = json.loads(captured)
                if (not isinstance(prepared, dict)
                        or set(prepared) != {"run_id", "input_id", "verification",
                                            "elapsed_seconds"}):
                    raise BoundaryError("local_training", "memory_preparation_result_invalid")
                run_id = digest(prepared["run_id"], "local_training.run_id")
                input_id = digest(prepared["input_id"], "local_training.input_id")
                run = store.get_manifest(run_id)
                training_input = store.get_manifest(input_id)
                if (run.producer != producer
                        or run.parameters.value().get("operation_id") != identity
                        or run.parent("training_input") != input_id
                        or training_input.parent("source") != dataset_id):
                    raise BoundaryError("local_training", "memory_preparation_result_invalid")
                try:
                    prepared_recipe = recipe_for_memory_config(
                        run.parameters.value().get("config"),
                        projection_config=training_input.parameters.value().get(
                            "projection_config"))
                except ValueError as error:
                    raise BoundaryError("local_training",
                                        "memory_preparation_result_invalid") from error
                if prepared_recipe != operation["recipe"]:
                    raise BoundaryError("local_training", "memory_preparation_profile_mismatch")
                self._advance(path, identity, stage="training", input_id=input_id,
                              run_id=run_id)
                command_name = "run-memory"
            elif human:
                self._advance(path, identity, stage="public_view")
                view = publish_human_text_bc_view(store, dataset_id, producer)
            else:
                assert spec is not None
                self._advance(path, identity, stage="allocating")
                allocation = publish_allocation(store, dataset_id, spec, producer)
                self._advance(path, identity, stage="public_view",
                              allocation_id=allocation.artifact_id)
                view = publish_public_bc_view(store, allocation.artifact_id, producer)
            if not memory:
                self._advance(path, identity, stage="tokenizing", view_id=view.artifact_id)
                inputs_manifest = publish_token_inputs(store, view.artifact_id, "s", producer,
                                                       max_tokens=16384)
                self._advance(path, identity, stage="preparing_run",
                              input_id=inputs_manifest.artifact_id)
                import torch

                torch.set_num_threads(2)
                inputs = load_token_inputs(store, inputs_manifest.artifact_id)
                config = TokenConfig(recipe=DEFAULT_RECIPE, width=48, layers=1,
                                     heads=2, feedforward=96, dropout=0.0, steps=3,
                                     device="cpu", max_tokens=16384)
                run = prepare_token_run(store, inputs, config, producer,
                                        replicate="local-" + identity)
                self._advance(path, identity, stage="training", run_id=run.artifact_id)
                command_name = "run-tokens"
            command = [sys.executable, "-m", "spireagent.research_cli", "--store",
                       str(owner.store_dir), command_name, "--run", run.artifact_id]
            log_path = owner.path.parent / ("local-training-" + identity + ".log")
            exit_code, _ = _private_child(command, log_path, environment,
                                         on_started=mark_started)
            mark_finished(exit_code)
            self._advance(path, identity, stage="verifying_result", _exit_code=exit_code)
            if exit_code:
                raise BoundaryError("local_training", "training_process_failed")
            reporter = ObjectStoreRunReporter(store, store.blobs)
            result = reporter.completed(run.artifact_id)
            if result is None:
                raise BoundaryError("local_training", "completion_marker_missing")
            if memory:
                # The read-only verifier must use the exact child runtime identity.
                verify_command = [sys.executable, "-m", "spireagent.research_cli",
                                  "--store", str(owner.store_dir), "verify-memory",
                                  "--run", run.artifact_id]
                verify_log = owner.path.parent / ("local-training-" + identity + "-verify.log")
                verify_exit, verified_output = _private_child(verify_command, verify_log,
                                                             environment,
                                                             on_started=mark_started)
                mark_finished(verify_exit)
                if verify_exit:
                    raise BoundaryError("local_training", "memory_verification_process_failed")
                verified = json.loads(verified_output)
                if (not isinstance(verified, dict) or verified.get("run_id") != run.artifact_id
                        or verified.get("result_id") != result.artifact_id
                        or verified.get("model_id") != result.parent("model")
                        or verified.get("checkpoint_id") != result.parent("checkpoint")):
                    raise BoundaryError("local_training", "memory_verification_result_invalid")
            else:
                _verify_completed(store, result, run)
            model_id = result.parent("model")
            model = store.get_manifest(model_id)
            _, _, registry_path = self._selected()
            sync_registry(store, SQLiteRegistry(registry_path))
            completion = {"checkpoint_id": model.parent("checkpoint"),
                          "result_id": result.artifact_id, "model_id": model_id}
            if not memory:
                completion["evaluation_id"] = result.parent("offline_evaluation")
                if operation["schema"] == SCHEMA_V2:
                    completion["evaluation_status"] = "completed"
            self._advance(path, identity, stage="completed", status="completed", **completion)
        except (BoundaryError, OSError, ValueError, KeyError, TypeError,
                subprocess.SubprocessError) as error:
            code = (error.code if isinstance(error, BoundaryError)
                    else "training_storage_or_process_error")
            stage = "unavailable"
            with suppress(OSError, ValueError, KeyError, TypeError, BoundaryError):
                recorded = self._read(path, owner.identity)
                if recorded.get("operation_id") == identity and recorded.get("stage") in STAGES:
                    stage = recorded["stage"]
            with suppress(Exception):
                self._failure_diagnostic = _safe_parent_failure(error, stage)
            with suppress(Exception):
                _write_parent_failure(path, identity, error)
            # The last durable pending operation remains blocking if terminal
            # persistence fails, so its run identity is never discarded.
            with suppress(OSError, ValueError, BoundaryError):
                self._advance(path, identity,
                              status="interrupted_unknown" if child_outcome_unknown else "failed",
                              error_code=code)
        finally:
            held.__exit__(None, None, None)
