"""Provider-neutral bounded wire for independent frozen-stage M2 evaluation.

This module transports typed evidence and frozen weights. It does not admit a
source, reserve paid capacity, or assert a qualified runtime; those decisions
belong to the caller/provider. Every evaluation request carries exact source,
selection, model and operation bindings and binary SHA-deduplicated payloads.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass
from typing import Any

from spireagent.json_boundary import BoundaryError, digest, json_bytes, object_fields

from . import public_m2_remote as _wire

REQUEST_SCHEMA = "stpd/public-m2-eval-remote-request-v1"
RESULT_SCHEMA = "stpd/public-m2-eval-remote-result-v1"
MAX_REQUEST_BYTES = 256 * 1024 * 1024
MAX_RESULT_BYTES = 8 * 1024 * 1024
_REQUEST_MAGIC = b"STPD-M2-EVAL-REQUEST\x00"
_RESULT_MAGIC = b"STPD-M2-EVAL-RESULT\x00"
_STAGE = "public_m2_eval_remote"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class InferenceRuntimeRequirement:
    """Caller-declared inference contract; never a runtime qualification receipt."""

    gpu_name_contains: str
    precision: str
    torch: str
    python: str
    evaluation_producer_sha256: str
    implementation_sha256: str

    def validate(self) -> None:
        if (not isinstance(self.gpu_name_contains, str) or not self.gpu_name_contains
                or self.precision not in {"float32", "float16", "bfloat16"}
                or not isinstance(self.torch, str) or not self.torch
                or not isinstance(self.python, str) or not self.python
                or not _DIGEST.fullmatch(self.evaluation_producer_sha256)
                or not _DIGEST.fullmatch(self.implementation_sha256)):
            raise BoundaryError(_STAGE, "invalid_inference_runtime_requirement")

    @classmethod
    def from_value(cls, value: object) -> InferenceRuntimeRequirement:
        obj = object_fields(value, {"gpu_name_contains", "precision", "torch", "python",
                                    "evaluation_producer_sha256", "implementation_sha256"}, _STAGE)
        try:
            result = cls(**obj)
        except TypeError as error:
            raise BoundaryError(_STAGE, "invalid_inference_runtime_requirement") from error
        result.validate()
        return result


_BLOB_ROLES = ("pilot_input", "full_parent_input", "tokenizer", "weights")
_HEADER_FIELDS = {
    "schema", "evaluation_operation_id", "attempt_id", "evaluation_producer",
    "training_producer", "resources", "model_id", "stage_id", "checkpoint_id",
    "training_config", "training_source_digest", "engine_input_digest",
    "evaluation_source_ids", "evaluation_source_digests", "selection_identities",
    "chain_range", "completed_epochs", "pilot_replay", "runtime_requirement",
    "blob_roles", "blobs",
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity(value: object, name: str) -> str:
    try:
        return digest(value, _STAGE + "." + name)
    except (TypeError, ValueError) as error:
        raise BoundaryError(_STAGE, "invalid_identity") from error


def _operation_id(value: object, name: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise BoundaryError(_STAGE, "invalid_" + name)
    return value


def _request_parts(raw: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    header, blobs = _wire._unpack(raw, _REQUEST_MAGIC, MAX_REQUEST_BYTES)
    if set(header) != _HEADER_FIELDS or header.get("schema") != REQUEST_SCHEMA:
        raise BoundaryError(_STAGE, "request_fieldset")
    roles = header["blob_roles"]
    if (not isinstance(roles, dict) or set(roles) != set(_BLOB_ROLES)
            or any(type(value) is not str or value not in blobs for value in roles.values())
            or set(roles.values()) != set(blobs)):
        raise BoundaryError(_STAGE, "blob_role_binding_mismatch")
    _operation_id(header["evaluation_operation_id"], "evaluation_operation_id")
    _operation_id(header["attempt_id"], "attempt_id")
    for key in ("model_id", "stage_id", "checkpoint_id", "training_source_digest",
                "engine_input_digest"):
        _identity(header[key], key)
    if (type(header["completed_epochs"]) is not int or header["completed_epochs"] != 1
            or not isinstance(header["training_config"], dict)
            or not isinstance(header["resources"], dict)
            or not isinstance(header["evaluation_producer"], dict)
            or not isinstance(header["training_producer"], dict)):
        raise BoundaryError(_STAGE, "invalid_exact_pins")
    if (not isinstance(header["evaluation_source_ids"], dict)
            or set(header["evaluation_source_ids"]) != {"pilot", "full_parent"}
            or not isinstance(header["evaluation_source_digests"], dict)
            or set(header["evaluation_source_digests"]) != {"pilot", "full_parent"}
            or not isinstance(header["selection_identities"], dict)
            or set(header["selection_identities"]) != {"pilot", "full_parent"}):
        raise BoundaryError(_STAGE, "source_selection_pin_mismatch")
    for kind in ("evaluation_source_digests", "selection_identities"):
        for value in header[kind].values():
            _identity(value, kind)
    runtime = InferenceRuntimeRequirement.from_value(header["runtime_requirement"])
    if runtime.evaluation_producer_sha256 != _sha(json_bytes(header["evaluation_producer"])):
        raise BoundaryError(_STAGE, "evaluation_producer_runtime_pin_mismatch")
    chain_range = header["chain_range"]
    if (not isinstance(chain_range, list) or len(chain_range) != 2
            or any(type(value) is not int for value in chain_range)
            or chain_range[0] < 0 or chain_range[1] <= chain_range[0]):
        raise BoundaryError(_STAGE, "invalid_chain_range")
    replay = header["pilot_replay"]
    if (not isinstance(replay, dict) or set(replay) != {"expected_count", "expected_correct",
                "expected_loss", "absolute_tolerance", "relative_tolerance"}
            or replay["expected_count"] != 13 or replay["expected_correct"] != 4
            or any(type(replay[key]) not in {float, int} or not math.isfinite(replay[key])
                   for key in ("expected_loss", "absolute_tolerance", "relative_tolerance"))
            or replay["expected_loss"] <= 0
            or not 0 <= replay["absolute_tolerance"] <= 1
            or not 0 <= replay["relative_tolerance"] <= 1):
        raise BoundaryError(_STAGE, "invalid_pilot_replay_contract")
    return header, blobs


def validate_public_m2_eval_request(raw: bytes) -> dict[str, Any]:
    """Check source and ordered selection bindings without loading weights."""
    header, blobs = _request_parts(raw)
    from ..fullrun.public_m2_input_storage import read_public_m2_input
    from .public_m2_evaluation import build_public_m2_eval_selection

    tokenizer = blobs[header["blob_roles"]["tokenizer"]]
    found: dict[str, str] = {}
    for name, role in (("pilot", "pilot_input"), ("full_parent", "full_parent_input")):
        source = read_public_m2_input(blobs[header["blob_roles"][role]], tokenizer)
        if source.identity != header["evaluation_source_digests"][name]:
            raise BoundaryError(_STAGE, "evaluation_source_identity_mismatch")
        selection = build_public_m2_eval_selection(
            source, training_input_digest=header["engine_input_digest"],
        )
        if selection.identity != header["selection_identities"][name]:
            raise BoundaryError(_STAGE, "selection_identity_mismatch")
        found[name] = selection.identity
    start, stop = header["chain_range"]
    full_count = len(build_public_m2_eval_selection(
        read_public_m2_input(blobs[header["blob_roles"]["full_parent_input"]], tokenizer),
        training_input_digest=header["engine_input_digest"],
    ).chains)
    if stop > full_count:
        raise BoundaryError(_STAGE, "invalid_chain_range")
    return {**{key: value for key, value in header.items() if not key.startswith("_")},
            "validated_selection_identities": found}


def build_public_m2_eval_request(
    *, evaluation_operation_id: str, attempt_id: str,
    evaluation_producer: dict[str, Any], training_producer: dict[str, Any],
    resources: dict[str, Any], model_id: str, stage_id: str, checkpoint_id: str,
    training_config: dict[str, Any], training_source_digest: str,
    engine_input_digest: str, pilot_input: bytes, full_parent_input: bytes,
    tokenizer: bytes, weights: bytes, pilot_source_id: str, full_parent_source_id: str,
    runtime_requirement: InferenceRuntimeRequirement | dict[str, Any],
    chain_range: tuple[int, int], expected_pilot_loss: float = 1.854619842,
    pilot_absolute_tolerance: float = 0.02, pilot_relative_tolerance: float = 0.02,
) -> bytes:
    """Build a binary request from caller-admitted, already-read source payloads."""
    runtime = (runtime_requirement if isinstance(runtime_requirement, InferenceRuntimeRequirement)
               else InferenceRuntimeRequirement.from_value(runtime_requirement))
    runtime.validate()
    payloads = {"pilot_input": pilot_input, "full_parent_input": full_parent_input,
                "tokenizer": tokenizer, "weights": weights}
    if any(type(raw) is not bytes or not raw for raw in payloads.values()):
        raise BoundaryError(_STAGE, "payload_required")
    blobs: dict[str, bytes] = {}
    roles: dict[str, str] = {}
    for role, raw in payloads.items():
        sha = _sha(raw)
        prior = blobs.get(sha)
        if prior is not None and prior != raw:
            raise BoundaryError(_STAGE, "sha_collision")
        blobs[sha] = raw
        roles[role] = sha
    from ..fullrun.public_m2_input_storage import read_public_m2_input
    from .public_m2_evaluation import build_public_m2_eval_selection

    pilot_source = read_public_m2_input(pilot_input, tokenizer)
    full_source = read_public_m2_input(full_parent_input, tokenizer)
    pilot_selection = build_public_m2_eval_selection(
        pilot_source, training_input_digest=engine_input_digest,
    )
    full_selection = build_public_m2_eval_selection(
        full_source, training_input_digest=engine_input_digest,
    )
    if len(chain_range) != 2 or not 0 <= chain_range[0] < chain_range[1] <= len(
            full_selection.chains):
        raise BoundaryError(_STAGE, "invalid_chain_range")
    header = {
        "schema": REQUEST_SCHEMA,
        "evaluation_operation_id": evaluation_operation_id,
        "attempt_id": attempt_id,
        "evaluation_producer": evaluation_producer,
        "training_producer": training_producer,
        "resources": resources,
        "model_id": model_id, "stage_id": stage_id, "checkpoint_id": checkpoint_id,
        "training_config": training_config,
        "training_source_digest": training_source_digest,
        "engine_input_digest": engine_input_digest,
        "evaluation_source_ids": {"pilot": pilot_source_id, "full_parent": full_parent_source_id},
        "evaluation_source_digests": {"pilot": pilot_source.identity,
                                       "full_parent": full_source.identity},
        "selection_identities": {"pilot": pilot_selection.identity,
                                 "full_parent": full_selection.identity},
        "chain_range": list(chain_range),
        "completed_epochs": 1,
        "pilot_replay": {"expected_count": 13, "expected_correct": 4,
                         "expected_loss": expected_pilot_loss,
                         "absolute_tolerance": pilot_absolute_tolerance,
                         "relative_tolerance": pilot_relative_tolerance},
        "runtime_requirement": asdict(runtime), "blob_roles": roles,
    }
    return _wire._pack(_REQUEST_MAGIC, header, blobs, MAX_REQUEST_BYTES)


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if hasattr(value, "item") and callable(value.item):
        return value.item()
    return value


def _config_from_value(value: dict[str, Any]):
    from ..models.token_core import ScratchShape
    from .public_m2_engine import PublicM2EngineConfig

    config_value = dict(value)
    shape = config_value.get("shape")
    if not isinstance(shape, dict):
        raise BoundaryError(_STAGE, "invalid_training_config")
    config_value["shape"] = ScratchShape(**shape)
    try:
        config = PublicM2EngineConfig(**config_value)
    except (TypeError, ValueError) as error:
        raise BoundaryError(_STAGE, "invalid_training_config") from error
    config.validate()
    return config


def _build_result(raw_request: bytes, request_sha256: str) -> dict[str, Any]:
    header, blobs = _request_parts(raw_request)
    validate_public_m2_eval_request(raw_request)
    from ..fullrun.public_m2_input_storage import read_public_m2_input
    from .public_m2_evaluation import (
        build_public_m2_eval_selection,
        combine_public_m2_eval_shards,
        open_public_m2_eval_session,
        partition_public_m2_eval_selection,
    )

    tokenizer = blobs[header["blob_roles"]["tokenizer"]]
    pilot_raw = blobs[header["blob_roles"]["pilot_input"]]
    full_raw = blobs[header["blob_roles"]["full_parent_input"]]
    pilot = read_public_m2_input(pilot_raw, tokenizer)
    full = read_public_m2_input(full_raw, tokenizer)
    if (pilot.identity != header["evaluation_source_digests"]["pilot"]
            or full.identity != header["evaluation_source_digests"]["full_parent"]):
        raise BoundaryError(_STAGE, "evaluation_source_identity_mismatch")
    engine_digest = header["engine_input_digest"]
    pilot_selection = build_public_m2_eval_selection(
        pilot, training_input_digest=engine_digest,
    )
    full_selection = build_public_m2_eval_selection(
        full, training_input_digest=engine_digest,
    )
    if ({"pilot": pilot_selection.identity, "full_parent": full_selection.identity}
            != header["selection_identities"]):
        raise BoundaryError(_STAGE, "selection_identity_mismatch")
    config = _config_from_value(header["training_config"])
    if config.source_digest != header["training_source_digest"]:
        raise BoundaryError(_STAGE, "training_source_binding_mismatch")
    session = open_public_m2_eval_session(
        blobs[header["blob_roles"]["weights"]], config,
        training_input_digest=engine_digest, inference_device="cuda:0",
        completed_epochs=header["completed_epochs"],
    )
    runtime = session.runtime
    req = InferenceRuntimeRequirement.from_value(header["runtime_requirement"])
    if (not isinstance(runtime, dict) or runtime.get("device_type") != "cuda"
            or req.gpu_name_contains not in str(runtime.get("gpu_name", ""))
            or runtime.get("default_dtype") != {
                "float32": "torch.float32", "float16": "torch.float16",
                "bfloat16": "torch.bfloat16",
            }[req.precision]
            or str(runtime.get("torch", "")) != req.torch
            or str(runtime.get("python", "")) != req.python
            or session.bindings.implementation_sha256 != req.implementation_sha256
            or req.evaluation_producer_sha256 != _sha(
                json_bytes(header["evaluation_producer"]))):
        raise BoundaryError(_STAGE, "inference_runtime_requirement_mismatch")

    pilot_shard = partition_public_m2_eval_selection(
        pilot_selection, 0, len(pilot_selection.chains),
    )
    pilot_result = session.evaluate(pilot, pilot_selection, shard=pilot_shard)
    pilot_summary = combine_public_m2_eval_shards(
        pilot_selection, (pilot_result,), expected_bindings=session.bindings,
        expected_runtime=session.runtime,
    )
    contract = header["pilot_replay"]
    tolerance = max(contract["absolute_tolerance"],
                    abs(contract["expected_loss"]) * contract["relative_tolerance"])
    if (pilot_summary.label_count != contract["expected_count"]
            or pilot_summary.correct_count != contract["expected_correct"]
            or abs(pilot_summary.loss_mean - contract["expected_loss"]) > tolerance):
        raise BoundaryError(_STAGE, "pilot_aggregate_replay_failed")

    start, stop = header["chain_range"]
    full_shard = partition_public_m2_eval_selection(full_selection, start, stop)
    full_result = session.evaluate(full, full_selection, shard=full_shard)
    full_complete = start == 0 and stop == len(full_selection.chains)
    summary = combine_public_m2_eval_shards(
        full_selection, (full_result,), expected_bindings=session.bindings,
        expected_runtime=session.runtime,
    ) \
        if full_complete else None
    chain_values = []
    for item in full_result.chains:
        value = _json_value(asdict(item))
        value["ordered_transition_ids"] = list(
            full_selection.chains[item.ordinal].ordered_transition_ids,
        )
        chain_values.append(value)
    chain_commit = hashlib.sha256(json_bytes({
        "request_sha256": request_sha256,
        "selection_identity": full_selection.identity,
        "start_ordinal": start, "stop_ordinal": stop,
        "chains": chain_values,
    })).hexdigest()
    return {
        "schema": RESULT_SCHEMA, "request_sha256": request_sha256,
        "evaluation_operation_id": header["evaluation_operation_id"],
        "attempt_id": header["attempt_id"],
        "evaluation_producer": header["evaluation_producer"],
        "training_producer": header["training_producer"],
        "model_id": header["model_id"], "stage_id": header["stage_id"],
        "checkpoint_id": header["checkpoint_id"],
        "pilot": _json_value(asdict(pilot_summary)),
        "full_parent": {
            "source_digest": full.identity,
            "selection_identity": full_selection.identity,
            "chain_range": [start, stop], "complete": full_complete,
            "next_chain_ordinal": None if full_complete else stop,
            "chains": chain_values, "chain_commit_sha256": chain_commit,
            "summary": None if summary is None else _json_value(asdict(summary)),
            "runtime": _json_value(runtime),
            "inference_device": full_result.inference_device,
            "phase_seconds": _json_value(full_result.phase_seconds),
            "peak_allocated_bytes": full_result.peak_allocated_bytes,
            "peak_reserved_bytes": full_result.peak_reserved_bytes,
            "bindings": _json_value(asdict(session.bindings)),
        },
    }


def execute_public_m2_eval_request(raw: bytes, *, request_sha256: str) -> bytes:
    if _identity(request_sha256, "request_sha256") != _sha(raw):
        raise BoundaryError(_STAGE, "request_digest_mismatch")
    value = _build_result(raw, request_sha256)
    return _wire._pack(_RESULT_MAGIC, value, {}, MAX_RESULT_BYTES)


def decode_public_m2_eval_result(raw: bytes, request: bytes) -> dict[str, Any]:
    """Strict CPU-side result binding check before owner admission/commit."""
    request_sha = _sha(request)
    header, _ = _request_parts(request)
    result, blobs = _wire._unpack(raw, _RESULT_MAGIC, MAX_RESULT_BYTES)
    if (blobs or result.get("schema") != RESULT_SCHEMA
            or result.get("request_sha256") != request_sha):
        raise BoundaryError(_STAGE, "result_request_binding_mismatch")
    for key in ("evaluation_operation_id", "attempt_id", "evaluation_producer",
                "training_producer", "model_id", "stage_id", "checkpoint_id"):
        if result.get(key) != header[key]:
            raise BoundaryError(_STAGE, "result_exact_pin_mismatch")
    full = result.get("full_parent")
    if (not isinstance(full, dict) or full.get("source_digest")
            != header["evaluation_source_digests"]["full_parent"]
            or full.get("selection_identity") != header["selection_identities"]["full_parent"]
            or full.get("chain_range") != header["chain_range"]
            or type(full.get("complete")) is not bool
            or full.get("next_chain_ordinal")
            != (None if full["complete"] else header["chain_range"][1])):
        raise BoundaryError(_STAGE, "result_selection_binding_mismatch")
    chains = full.get("chains")
    if not isinstance(chains, list) or any(not isinstance(item, dict) for item in chains):
        raise BoundaryError(_STAGE, "result_chain_inventory_invalid")
    ordinals = [item.get("ordinal") for item in chains]
    if ordinals != list(range(*header["chain_range"])):
        raise BoundaryError(_STAGE, "result_chain_range_mismatch")
    request_header, request_blobs = _request_parts(request)
    from ..fullrun.public_m2_input_storage import read_public_m2_input
    from .public_m2_evaluation import build_public_m2_eval_selection
    source = read_public_m2_input(
        request_blobs[request_header["blob_roles"]["full_parent_input"]],
        request_blobs[request_header["blob_roles"]["tokenizer"]],
    )
    selection = build_public_m2_eval_selection(
        source, training_input_digest=request_header["engine_input_digest"],
    )
    for item, ordinal in zip(chains, ordinals, strict=True):
        reference = selection.chains[ordinal]
        if (item.get("chain_id") != reference.chain_id
                or item.get("label_count") != reference.row_count
                or item.get("evidence_sha256") != list(reference.ordered_evidence_sha256)
                or item.get("ordered_transition_ids")
                != list(reference.ordered_transition_ids)):
            raise BoundaryError(_STAGE, "result_chain_selection_mismatch")
    bindings = full.get("bindings")
    from ..canonical import semantic_hash
    if (not isinstance(bindings, dict)
            or bindings.get("training_input_digest") != header["engine_input_digest"]
            or bindings.get("weights_sha256")
            != _sha(request_blobs[request_header["blob_roles"]["weights"]])
            or bindings.get("completed_epochs") != 1
            or bindings.get("run_complete") is not False
            or bindings.get("config_digest") != str(semantic_hash(header["training_config"]))
            or bindings.get("implementation_sha256")
            != header["runtime_requirement"]["implementation_sha256"]):
        raise BoundaryError(_STAGE, "result_model_binding_mismatch")
    runtime = full.get("runtime")
    requirement = InferenceRuntimeRequirement.from_value(header["runtime_requirement"])
    if (not isinstance(runtime, dict) or runtime.get("device_type") != "cuda"
            or requirement.gpu_name_contains not in str(runtime.get("gpu_name", ""))
            or runtime.get("default_dtype") != {
                "float32": "torch.float32", "float16": "torch.float16",
                "bfloat16": "torch.bfloat16",
            }[requirement.precision]
            or runtime.get("torch") != requirement.torch
            or runtime.get("python") != requirement.python
            or full.get("inference_device") != "cuda:0"):
        raise BoundaryError(_STAGE, "result_runtime_binding_mismatch")
    timings = full.get("phase_seconds")
    if (not isinstance(timings, dict) or not timings
            or any(type(value) not in {int, float} or not math.isfinite(value) or value < 0
                   for value in timings.values())
            or any(type(full.get(key)) is not int or full[key] < 0 for key in (
                "peak_allocated_bytes", "peak_reserved_bytes"))):
        raise BoundaryError(_STAGE, "result_runtime_metrics_invalid")
    expected_commit = hashlib.sha256(json_bytes({
        "request_sha256": request_sha,
        "selection_identity": full["selection_identity"],
        "start_ordinal": header["chain_range"][0],
        "stop_ordinal": header["chain_range"][1], "chains": chains,
    })).hexdigest()
    if full.get("chain_commit_sha256") != expected_commit:
        raise BoundaryError(_STAGE, "result_chain_commit_mismatch")
    pilot = result.get("pilot")
    replay = header["pilot_replay"]
    tolerance = max(replay["absolute_tolerance"],
                    abs(replay["expected_loss"]) * replay["relative_tolerance"])
    if (not isinstance(pilot, dict) or pilot.get("label_count") != replay["expected_count"]
            or pilot.get("correct_count") != replay["expected_correct"]
            or not isinstance(pilot.get("loss_mean"), (int, float))
            or abs(pilot["loss_mean"] - replay["expected_loss"]) > tolerance):
        raise BoundaryError(_STAGE, "result_pilot_replay_mismatch")
    return result
