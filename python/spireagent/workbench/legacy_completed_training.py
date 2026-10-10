"""Closed historical completion compatibility; no provider execution or data authority.

The v4 grammar comes from the historical LocalTrainingService owner. This reader
accepts only a locally accepted, fully stopped Public M0 chain. Indexes remain
historical metadata: no request/result chunks, weights or checkpoints are hydrated.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.encoding import semantic_hash
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes
from spireagent.storage.blobs import safe_key
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore

SCHEMA = "stpd/local-training-operation-v4"
RECIPE = "stage1a.dsimple.light-action.m0.s.v1"
HISTORY_SCHEMA = "spireagent/completed-training-history-v1"
HISTORY_PREFIX = "local-training/remote-operation-history/"
MAX_JOURNAL_BYTES = 1024 * 1024
MAX_ATTEMPTS = 256
MAX_CONTROL_BYTES = 128 * 1024
IDS = ("input_id", "run_id", "checkpoint_id", "model_id", "result_id", "evaluation_id")
ATTEMPT_FIELDS = {
    "attempt_id",
    "request_sha256",
    "request_ref",
    "start_step",
    "target_step",
    "resume_checkpoint_id",
    "resume_of_attempt_id",
    "attempt_spec_sha256",
    "attempt_spec_ref",
    "app_ref_sha256",
    "app_ref_ref",
    "handle_sha256",
    "handle_ref",
    "runtime_evidence_sha256",
    "runtime_evidence_ref",
    "app_phase",
    "stop_confirmation",
    "provider_error_code",
    "submit_intent_at_unix_ns",
    "provider_terminal_observed_at_unix_ns",
    "local_acceptance_started_at_unix_ns",
    "local_acceptance_completed_at_unix_ns",
    "core_finalizer_attempt_seconds",
    "phase",
    "provider_terminal",
    "terminal_state",
    "checkpoint_candidate_sha256",
    "validated_checkpoint_id",
    "provider_result_sha256",
    "provider_result_ref",
    "recovery_receipt",
}
OPTIONAL_OLD_FIELDS = {"runtime_evidence_sha256", "runtime_evidence_ref", "recovery_receipt"}


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise BoundaryError("legacy_completed_training", code)


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _timestamp(value: Any) -> bool:
    return _integer(value, 1, 2**63 - 1)


def _seconds(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def _reference(value: dict[str, Any], stem: str, prefix: str, *, optional: bool = False) -> None:
    sha, ref = value.get(stem + "_sha256"), value.get(stem + "_ref")
    if optional and sha is None and ref is None:
        return
    sha = digest(sha, "legacy_training." + stem)
    if not isinstance(ref, str):
        raise BoundaryError("legacy_completed_training", "historical_reference_mismatch")
    _require(safe_key(ref) == prefix + sha, "historical_reference_mismatch")


def validate_completed(value: dict[str, Any], owner: tuple[str, ...]) -> dict[str, Any]:
    """Pure validation, retaining the original dictionary and historical schema."""
    _require(
        value.get("schema") == SCHEMA
        and value.get("_owner") == list(owner)
        and value.get("status") == "completed"
        and value.get("stage") == "completed"
        and value.get("recipe") == RECIPE
        and value.get("result_type") == "evaluated"
        and value.get("evaluation_status") == "completed"
        and value.get("input_profile") in {"public_lite", "public_compact"},
        "closed_completion_required",
    )
    digest(value["operation_id"], "legacy_training.operation", length=32)
    digest(value["dataset_id"], "legacy_training.dataset")
    for key in IDS:
        digest(value[key], "legacy_training." + key)
    _require(
        _timestamp(value.get("created_at_unix_ns"))
        and _timestamp(value.get("completed_at_unix_ns"))
        and value["completed_at_unix_ns"] >= value["created_at_unix_ns"]
        and _seconds(value.get("total_wall_seconds")),
        "historical_completion_time_invalid",
    )
    remote = value.get("remote")
    _require(
        isinstance(remote, dict) and set(remote) == {"target", "attempts"},
        "historical_remote_fields_invalid",
    )
    remote = value["remote"]
    target = remote["target"]
    _require(
        isinstance(target, dict)
        and set(target)
        == {
            "target_id",
            "app_id",
            "deployment_source_sha256",
            "gpu",
            "timeout_seconds",
            "startup_timeout_seconds",
            "max_total_steps",
        },
        "historical_target_invalid",
    )
    digest(target["target_id"], "legacy_training.target")
    digest(target["deployment_source_sha256"], "legacy_training.deployment")
    _require(
        isinstance(target["app_id"], str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", target["app_id"]) is not None
        and target["gpu"] == "L4"
        and _integer(target["timeout_seconds"], 1, 900)
        and _integer(target["startup_timeout_seconds"], 1, 120)
        and _integer(target["max_total_steps"], 1, 100_000),
        "historical_target_bound_invalid",
    )
    attempts = remote["attempts"]
    _require(
        isinstance(attempts, list) and 1 <= len(attempts) <= MAX_ATTEMPTS,
        "historical_attempt_bound_invalid",
    )
    expected_id, expected_step, previous_attempt = None, 0, None
    seen: set[str] = set()
    for position, attempt in enumerate(attempts):
        _require(
            isinstance(attempt, dict)
            and set(attempt)
            in (
                ATTEMPT_FIELDS,
                ATTEMPT_FIELDS - {"recovery_receipt"},
                ATTEMPT_FIELDS - OPTIONAL_OLD_FIELDS,
            ),
            "historical_attempt_fields_invalid",
        )
        attempt_id = digest(attempt["attempt_id"], "legacy_training.attempt", length=32)
        _require(attempt_id not in seen, "historical_attempt_repeated")
        seen.add(attempt_id)
        _require(
            attempt["phase"] == "terminal"
            and attempt["provider_terminal"] is True
            and attempt["app_phase"] == "submitted"
            and attempt["terminal_state"]
            == ("completed" if position == len(attempts) - 1 else "paused")
            and attempt.get("recovery_receipt") is None,
            "historical_attempt_not_closed",
        )
        _reference(attempt, "request", "local-training/remote-requests/")
        _reference(attempt, "provider_result", "local-training/remote-results/")
        for stem, kind in (
            ("attempt_spec", "attempt-spec"),
            ("app_ref", "app-ref"),
            ("handle", "call-handle"),
            ("runtime_evidence", "runtime-evidence"),
        ):
            _reference(
                attempt,
                stem,
                "local-training/remote-control/" + kind + "/",
                optional=stem == "runtime_evidence",
            )
        _require(
            _integer(attempt["start_step"], 0, target["max_total_steps"])
            and _integer(attempt["target_step"], 1, target["max_total_steps"])
            and attempt["start_step"] == expected_step
            and attempt["target_step"] > expected_step
            and attempt["resume_checkpoint_id"] == expected_id
            and attempt["resume_of_attempt_id"] == previous_attempt,
            "historical_checkpoint_chain_invalid",
        )
        expected_id = digest(
            attempt["validated_checkpoint_id"], "legacy_training.accepted_checkpoint"
        )
        if attempt["checkpoint_candidate_sha256"] is not None:
            digest(attempt["checkpoint_candidate_sha256"], "legacy_training.candidate")
        _require(
            position != len(attempts) - 1 or attempt["checkpoint_candidate_sha256"] is not None,
            "historical_final_candidate_missing",
        )
        for key in (
            "submit_intent_at_unix_ns",
            "provider_terminal_observed_at_unix_ns",
            "local_acceptance_started_at_unix_ns",
            "local_acceptance_completed_at_unix_ns",
        ):
            _require(_timestamp(attempt[key]), "historical_acceptance_time_invalid")
        _require(
            attempt["local_acceptance_completed_at_unix_ns"]
            >= (attempt["local_acceptance_started_at_unix_ns"])
            and (
                attempt["core_finalizer_attempt_seconds"] is None
                or _seconds(attempt["core_finalizer_attempt_seconds"])
            ),
            "historical_acceptance_duration_invalid",
        )
        if attempt["provider_error_code"] is not None:
            _require(
                isinstance(attempt["provider_error_code"], str)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", attempt["provider_error_code"])
                is not None,
                "historical_provider_error_invalid",
            )
        stop = attempt["stop_confirmation"]
        _require(
            isinstance(stop, dict)
            and set(stop)
            == {"app_id", "app_state", "active_tasks", "active_containers", "confirmed"}
            and isinstance(stop["app_id"], str)
            and bool(stop["app_id"])
            and stop["app_state"] == "stopped"
            and stop["confirmed"] is True
            and type(stop["active_tasks"]) is int
            and stop["active_tasks"] == 0
            and type(stop["active_containers"]) is int
            and stop["active_containers"] == 0,
            "historical_stop_unconfirmed",
        )
        expected_step, previous_attempt = attempt["target_step"], attempt_id
    _require(
        value["checkpoint_id"] == expected_id
        and type(value.get("checkpoint_step")) is int
        and value["checkpoint_step"] == expected_step,
        "historical_final_checkpoint_mismatch",
    )
    if "previous_completed" in value:
        previous = value["previous_completed"]
        fields = {"operation_id", "dataset_id", "model_id", "result_id", "evaluation_id"}
        _require(
            isinstance(previous, dict) and set(previous) in (fields, fields - {"evaluation_id"}),
            "historical_previous_completion_invalid",
        )
        for key, identity in previous.items():
            digest(
                identity,
                "legacy_training.previous_" + key,
                length=32 if key == "operation_id" else 64,
            )
    return value


def decode_completed(raw: bytes, owner: tuple[str, ...]) -> dict[str, Any]:
    _require(
        isinstance(raw, bytes) and 1 <= len(raw) <= MAX_JOURNAL_BYTES,
        "historical_journal_bound_invalid",
    )
    value = decode_json(raw)
    _require(isinstance(value, dict), "historical_operation_required")
    return validate_completed(value, owner)


def public_completed(value: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "schema",
        "status",
        "stage",
        "operation_id",
        "dataset_id",
        "recipe",
        "result_type",
        "evaluation_status",
        "input_profile",
        "checkpoint_step",
        "previous_completed",
        *IDS,
    }
    result = {key: value[key] for key in keys if key in value}
    result.update(
        historical_completion=True,
        supported_actions=[],
        historical_remote_attempts=len(value["remote"]["attempts"]),
        provider_stop="recorded_terminal_stops_not_live_query",
    )
    if "_historical_operation" in value:
        result["historical_operation"] = value["_historical_operation"]
    return result


def _small_json(
    store: ManifestArtifactStore, ref: str, expected_sha: str | None = None
) -> dict[str, Any]:
    raw = store.blobs.get(safe_key(ref))
    _require(1 <= len(raw) <= MAX_CONTROL_BYTES, "historical_control_bound_invalid")
    if expected_sha is not None:
        _require(
            hashlib.sha256(raw).hexdigest() == expected_sha, "historical_control_digest_mismatch"
        )
    value = decode_json(raw)
    if not isinstance(value, dict):
        raise BoundaryError("legacy_completed_training", "historical_control_encoding_invalid")
    _require(
        expected_sha is None or raw == json_bytes(value),
        "historical_control_encoding_invalid",
    )
    return value


def _index(
    store: ManifestArtifactStore, ref: str, expected: str, *, request: bool
) -> dict[str, Any]:
    # These are index descriptors, not logical payload bytes. Never hydrate chunks.
    index = _small_json(store, ref)
    compressed = (
        request and index.get("schema") == "stpd/local-training-remote-request-wire-index-v1"
    )
    fields = {"schema", "sha256", "size", "chunks"}
    if compressed:
        fields |= {"wire_sha256", "wire_size"}
    _require(
        set(index) == fields
        and index["sha256"] == expected
        and (compressed or index["schema"] == "stpd/local-training-remote-evidence-index-v1")
        and _integer(index["size"], 1, (1024 if request else 128) * 1024**2),
        "historical_index_descriptor_invalid",
    )
    stored = index["wire_size"] if compressed else index["size"]
    _require(
        _integer(stored, 1, (256 if request else 128) * 1024**2),
        "historical_index_stored_bound_invalid",
    )
    if compressed:
        digest(index["wire_sha256"], "legacy_training.request_wire")
    chunks = index["chunks"]
    _require(
        isinstance(chunks, list) and len(chunks) == math.ceil(stored / (8 * 1024**2)),
        "historical_index_chunk_count_invalid",
    )
    for position, chunk in enumerate(chunks):
        _require(
            isinstance(chunk, dict)
            and set(chunk) == {"sha256", "size"}
            and type(chunk["size"]) is int
            and chunk["size"] == min(8 * 1024**2, stored - position * 8 * 1024**2),
            "historical_index_chunk_descriptor_invalid",
        )
        digest(chunk["sha256"], "legacy_training.chunk")
    return index


def verify_completion_metadata(store: ManifestArtifactStore, value: dict[str, Any]) -> None:
    """Metadata-only transition gate under the existing training lock."""
    inputs, run, checkpoint, model, result, evaluation = (
        store.get_manifest(value[key]) for key in IDS
    )
    mi, ri, ii, ci = (item.parameters.value() for item in (model, run, inputs, checkpoint))
    binding = mi.get("training_binding")
    _require(
        isinstance(binding, dict)
        and binding.get("schema") == "stpd/light-action-m0-training-binding-v1"
        and binding.get("dataset_ids") == [value["dataset_id"]]
        and binding.get("training_operation_id") == value["operation_id"],
        "historical_model_training_binding_mismatch",
    )
    binding = mi["training_binding"]
    view = store.get_manifest(binding["model_view_id"])
    allocation = store.get_manifest(binding["allocation_id"])
    _require(
        model.kind == "model"
        and sorted(parent.role for parent in model.parents)
        == ["checkpoint", "model_view", "run", "training_input"]
        and mi.get("schema") == "stpd/stage1a-light-action-m0-public-model-v1"
        and mi.get("recipe") == RECIPE
        and mi.get("steps") == value["checkpoint_step"]
        and model.parent("run") == run.artifact_id
        and model.parent("training_input") == inputs.artifact_id
        and model.parent("checkpoint") == checkpoint.artifact_id
        and model.parent("model_view") == view.artifact_id
        and run.kind == "run"
        and sorted(parent.role for parent in run.parents) == ["experiment", "training_input"]
        and ri.get("schema") == "stpd/stage1a-run-v1"
        and run.parent("training_input") == inputs.artifact_id
        and ri.get("config") == mi.get("config")
        and ri.get("training_binding") == binding
        and isinstance(mi.get("config"), dict)
        and mi["config"].get("recipe") == RECIPE
        and mi["config"].get("steps") == value["checkpoint_step"]
        and inputs.kind == "training_input"
        and [parent.role for parent in inputs.parents] == ["model_view"]
        and ii.get("schema") == "stpd/stage1a-light-action-m0-public-input-v1"
        and inputs.parent("model_view") == view.artifact_id
        and ii.get("training_binding") == binding
        and checkpoint.kind == "checkpoint"
        and sorted(parent.role for parent in checkpoint.parents) == ["run", "training_input"]
        and ci.get("schema") == "stpd/stage1a-light-action-m0-checkpoint-v1"
        and checkpoint.parent("run") == run.artifact_id
        and checkpoint.parent("training_input") == inputs.artifact_id
        and ci.get("step") == value["checkpoint_step"]
        and ci.get("recipe") == RECIPE
        and ci.get("training_binding") == binding
        and view.kind == "model_view"
        and view.parameters.value().get("schema") == "stpd/public-observation-bc-view-v2"
        and sorted(parent.role for parent in view.parents) == ["allocation", "dataset"]
        and view.parent("dataset") == value["dataset_id"]
        and view.parent("allocation") == allocation.artifact_id
        and allocation.kind == "protocol"
        and allocation.parameters.value().get("schema") == "stpd/decision-allocation-v1"
        and [parent.role for parent in allocation.parents] == ["dataset"]
        and allocation.parent("dataset") == value["dataset_id"],
        "historical_completion_manifest_lineage_mismatch",
    )
    selected = ObjectStoreRunReporter(store, store.blobs).completed(run.artifact_id)
    _require(
        selected == result
        and result.kind == "run_result"
        and sorted(parent.role for parent in result.parents)
        == ["model", "offline_evaluation", "run", "training_input"]
        and result.parameters.value().get("schema") == "stpd/run-result-v1"
        and result.parameters.value().get("state") == "completed"
        and result.parameters.value().get("steps") == value["checkpoint_step"]
        and result.parent("model") == model.artifact_id
        and result.parent("training_input") == inputs.artifact_id
        and result.parent("offline_evaluation") == evaluation.artifact_id
        and evaluation.kind == "offline_evaluation"
        and evaluation.parameters.value().get("schema") == "stpd/stage1a-ranking-evaluation-v1"
        and sorted(parent.role for parent in evaluation.parents) == ["model", "model_view"]
        and evaluation.parent("model") == model.artifact_id
        and evaluation.parent("model_view") == view.artifact_id
        and all(
            item.producer == model.producer
            for item in (run, inputs, checkpoint, result, evaluation)
        ),
        "historical_completion_marker_mismatch",
    )
    for role in ("state_tokenizer", "action_codec"):
        _require(
            model.payload(role) == inputs.payload(role), "historical_model_codec_binding_mismatch"
        )
    for position, attempt in enumerate(value["remote"]["attempts"]):
        controls = {
            stem: _small_json(store, attempt[stem + "_ref"], attempt[stem + "_sha256"])
            for stem in ("attempt_spec", "app_ref", "handle")
        }
        spec, app, handle = (controls[stem] for stem in ("attempt_spec", "app_ref", "handle"))
        app_target = {key: item for key, item in app.items() if key != "schema"}
        producer = Producer.decode(spec["producer"])
        plan, runtime_spec = spec.get("resource_plan"), spec.get("target_runtime")
        _require(
            isinstance(plan, dict) and isinstance(runtime_spec, dict),
            "historical_deployment_metadata_required",
        )
        plan, runtime_spec = spec["resource_plan"], spec["target_runtime"]
        target_identity = {
            "account_id": spec["account_id"],
            "environment_name": spec["environment_name"],
            "image_object_id": spec["image_object_id"],
            "producer": producer.to_dict(),
            "target_runtime": runtime_spec,
            "resource_plan": plan,
        }
        target = value["remote"]["target"]
        _require(
            target["target_id"] == hashlib.sha256(json_bytes(target_identity)).hexdigest()
            and target["deployment_source_sha256"]
            == hashlib.sha256(json_bytes(producer.to_dict())).hexdigest()
            and (position != 0 or target["app_id"] == spec["app_name"])
            and target["gpu"] == plan.get("gpu")
            and target["timeout_seconds"] == plan.get("function_timeout_seconds")
            and target["startup_timeout_seconds"] == plan.get("startup_timeout_seconds")
            and target["max_total_steps"] == value["checkpoint_step"]
            and producer == model.producer,
            "historical_deployment_binding_mismatch",
        )
        _require(
            spec.get("schema") == "stpd/modal-m0-attempt-spec-v1"
            and app.get("schema") == "stpd/modal-m0-app-ref-v1"
            and handle.get("schema") == "stpd/modal-m0-call-v3"
            and all(
                control.get("attempt_id") == attempt["attempt_id"]
                and control.get("request_sha256") == attempt["request_sha256"]
                and Producer.decode(control["producer"]) == producer
                for control in (spec, app, handle)
            )
            and app.get("app_name")
            == spec.get("app_name")
            == "stpd-m0-update-" + attempt["attempt_id"]
            and app.get("app_id") == attempt["stop_confirmation"]["app_id"]
            and app.get("function_name") == "token_remote_update"
            and app.get("resource_plan") == plan
            and app.get("target_runtime") == runtime_spec
            and all(
                app.get(key) == spec.get(key)
                for key in ("account_id", "environment_name", "image_object_id")
            )
            and handle.get("target") == app_target
            and handle.get("target_id") == semantic_hash(app_target)
            and handle.get("target_device") == "cuda"
            and handle.get("target_runtime")
            == {
                **runtime_spec,
                "producer": producer.to_dict(),
                "image_object_id": app["image_object_id"],
                "target_id": handle.get("target_id"),
            }
            and handle.get("operation_id") == value["operation_id"]
            and handle.get("run_id") == run.artifact_id
            and handle.get("input_id") == inputs.artifact_id
            and handle.get("target_step") == attempt["target_step"]
            and handle.get("resume_checkpoint_id") == attempt["resume_checkpoint_id"],
            "historical_control_binding_mismatch",
        )
        request_index = _index(
            store, attempt["request_ref"], attempt["request_sha256"], request=True
        )
        result_index = _index(
            store, attempt["provider_result_ref"], attempt["provider_result_sha256"], request=False
        )
        _require(
            handle.get("request_size_bytes")
            == request_index.get("wire_size", request_index["size"]),
            "historical_request_index_binding_mismatch",
        )
        if attempt.get("runtime_evidence_ref") is not None:
            runtime = _small_json(
                store, attempt["runtime_evidence_ref"], attempt["runtime_evidence_sha256"]
            )
            _require(
                runtime.get("schema") == "stpd/modal-m0-runtime-evidence-v1"
                and runtime.get("attempt_id") == attempt["attempt_id"]
                and runtime.get("request_sha256") == attempt["request_sha256"]
                and runtime.get("producer") == producer.to_dict()
                and runtime.get("target_id") == handle["target_id"]
                and runtime.get("call_id") == handle["call_id"]
                and runtime.get("result_sha256") == attempt["provider_result_sha256"]
                and runtime.get("result_size_bytes") == result_index["size"]
                and runtime.get("image_object_id") == app["image_object_id"]
                and runtime.get("torch_version") == spec["target_runtime"]["torch_version"]
                and runtime.get("cpu_threads") == spec["target_runtime"]["cpu_threads"],
                "historical_runtime_control_binding_mismatch",
            )


def archive_completed(
    store: ManifestArtifactStore, raw: bytes, value: dict[str, Any]
) -> dict[str, Any]:
    """Reuse the historical owner's exact immutable archive namespace."""
    _require(
        decode_completed(raw, tuple(value["_owner"])) == value,
        "historical_journal_changed_before_archive",
    )
    verify_completion_metadata(store, value)
    sha = hashlib.sha256(raw).hexdigest()
    reference = HISTORY_PREFIX + sha
    try:
        store.blobs.put_if_absent(reference, raw)
        _require(store.blobs.get(reference) == raw, "historical_archive_readback_mismatch")
    except Exception as error:
        raise BoundaryError(
            "local_training", "remote_operation_history_persistence_failed"
        ) from error
    return {
        "schema": HISTORY_SCHEMA,
        "original_schema": SCHEMA,
        "operation_id": value["operation_id"],
        "dataset_id": value["dataset_id"],
        "model_id": value["model_id"],
        "result_id": value["result_id"],
        "archive_ref": reference,
        "archive_sha256": sha,
        "archive_size": len(raw),
    }


def validate_history(previous: Any) -> None:
    """History is provenance only; it grants no source/use or remote control authority."""
    depth = 0
    while isinstance(previous, dict):
        depth += 1
        _require(depth <= 256, "historical_history_bound_invalid")
        pointer = previous.get("historical_operation")
        if pointer is not None:
            _require(
                isinstance(pointer, dict)
                and set(pointer)
                == {
                    "schema",
                    "original_schema",
                    "operation_id",
                    "dataset_id",
                    "model_id",
                    "result_id",
                    "archive_ref",
                    "archive_sha256",
                    "archive_size",
                }
                and pointer["schema"] == HISTORY_SCHEMA
                and pointer["original_schema"] == SCHEMA
                and previous.get("schema") == SCHEMA
                and previous.get("status") == "completed"
                and all(
                    pointer[key] == previous[key]
                    for key in ("operation_id", "dataset_id", "model_id", "result_id")
                )
                and _integer(pointer["archive_size"], 1, MAX_JOURNAL_BYTES),
                "historical_history_pointer_invalid",
            )
            digest(pointer["archive_sha256"], "legacy_training.archive")
            for key in ("operation_id", "dataset_id", "model_id", "result_id"):
                digest(
                    pointer[key],
                    "legacy_training.history_" + key,
                    length=32 if key == "operation_id" else 64,
                )
            _require(
                safe_key(pointer["archive_ref"]) == HISTORY_PREFIX + pointer["archive_sha256"],
                "historical_history_archive_reference_invalid",
            )
        previous = previous.get("previous_completed")
