"""Disposable training projection/candidate bytes, without canonical Store access.

Only the domain adapter knows what numerical projections mean. This codec checks
bounded immutable bytes, original identity and candidate-only artifact deltas.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import Any

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    decode_json,
    json_bytes,
    object_fields,
)
from spireagent.training_execution_contracts import (
    CandidateResult,
    KnownFailure,
    PreparedTrainingAttempt,
    TrainingCallHandle,
)

_PROJECTION_MAGIC = b"SPIREAGENT-TRAIN-PROJECTION\x00"
_CANDIDATE_MAGIC = b"SPIREAGENT-TRAIN-CANDIDATE\x00"
_RESPONSE_MAGIC = b"SPIREAGENT-TRAIN-RESPONSE\x00"
_HEADER_LIMIT = 1024 * 1024
_STAGE = "training_transport"
_CANDIDATE_KINDS = frozenset(
    {"run_event", "checkpoint", "model", "offline_evaluation", "analysis", "run_result"}
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pack(magic: bytes, header: dict[str, Any], parts: tuple[bytes, ...], maximum: int) -> bytes:
    framed = json_bytes(header)
    total = len(magic) + 4 + len(framed) + sum(len(part) for part in parts)
    if len(framed) > _HEADER_LIMIT or total > maximum:
        raise BoundaryError(_STAGE, "frame_limit")
    return magic + struct.pack(">I", len(framed)) + framed + b"".join(parts)


def _unpack(raw: bytes, magic: bytes, maximum: int) -> tuple[dict[str, Any], bytes]:
    prefix = len(magic) + 4
    if type(raw) is not bytes or not prefix < len(raw) <= maximum or not raw.startswith(magic):
        raise BoundaryError(_STAGE, "invalid_frame")
    size = struct.unpack(">I", raw[len(magic) : prefix])[0]
    if not 1 <= size <= _HEADER_LIMIT or prefix + size > len(raw):
        raise BoundaryError(_STAGE, "header_limit")
    header_raw = raw[prefix : prefix + size]
    header = decode_json(header_raw)
    if not isinstance(header, dict) or json_bytes(header) != header_raw:
        raise BoundaryError(_STAGE, "noncanonical_header")
    return header, raw[prefix + size :]


@dataclass(frozen=True)
class ProjectionPart:
    role: str
    raw: bytes

    def __post_init__(self) -> None:
        if self.role not in {"training_projection", "resume_checkpoint"}:
            raise BoundaryError(_STAGE, "original_source_or_authority_payload_forbidden")
        if type(self.raw) is not bytes or not self.raw:
            raise BoundaryError(_STAGE, "immutable_nonempty_projection_required")


def encode_projection(
    identity: dict[str, str],
    parts: tuple[ProjectionPart, ...],
    maximum: int,
) -> bytes:
    """No raw source, journal, credentials, completion slot or Store locator role."""
    identity = object_fields(
        identity, {"run_id", "training_input_id", "operation_id", "attempt_id"}, _STAGE
    )
    if (
        not isinstance(parts, tuple)
        or not 1 <= len(parts) <= 2
        or any(not isinstance(part, ProjectionPart) for part in parts)
        or len({part.role for part in parts}) != len(parts)
        or "training_projection" not in {part.role for part in parts}
    ):
        raise BoundaryError(_STAGE, "projection_roles_required")
    ordered = tuple(sorted(parts, key=lambda part: part.role))
    return _pack(
        _PROJECTION_MAGIC,
        {
            "schema": "spireagent/disposable-training-projection-v1",
            **identity,
            "parts": [
                {"role": part.role, "sha256": _sha(part.raw), "size": len(part.raw)}
                for part in ordered
            ],
        },
        tuple(part.raw for part in ordered),
        maximum,
    )


def decode_projection(raw: bytes, attempt: PreparedTrainingAttempt) -> tuple[ProjectionPart, ...]:
    if _sha(raw) != attempt.projection_sha256:
        raise BoundaryError(_STAGE, "projection_digest_mismatch")
    header, body = _unpack(raw, _PROJECTION_MAGIC, attempt.limits.max_projection_bytes)
    obj = object_fields(
        header,
        {"schema", "run_id", "training_input_id", "operation_id", "attempt_id", "parts"},
        _STAGE,
    )
    if obj["schema"] != "spireagent/disposable-training-projection-v1" or any(
        obj[name] != getattr(attempt, name)
        for name in ("run_id", "training_input_id", "operation_id", "attempt_id")
    ):
        raise BoundaryError(_STAGE, "projection_original_identity_mismatch")
    if not isinstance(obj["parts"], list) or not 1 <= len(obj["parts"]) <= 2:
        raise BoundaryError(_STAGE, "projection_part_limit")
    parts, cursor = [], 0
    for entry in obj["parts"]:
        row = object_fields(entry, {"role", "sha256", "size"}, _STAGE)
        size = row["size"]
        if type(size) is not int or not 1 <= size <= len(body) - cursor:
            raise BoundaryError(_STAGE, "projection_part_size")
        part = ProjectionPart(row["role"], body[cursor : cursor + size])
        if _sha(part.raw) != row["sha256"]:
            raise BoundaryError(_STAGE, "projection_part_digest")
        parts.append(part)
        cursor += size
    result = tuple(parts)
    identity = {
        name: obj[name] for name in ("run_id", "training_input_id", "operation_id", "attempt_id")
    }
    if (
        cursor != len(body)
        or encode_projection(identity, result, attempt.limits.max_projection_bytes) != raw
    ):
        raise BoundaryError(_STAGE, "noncanonical_projection")
    return result


@dataclass(frozen=True)
class CandidateArtifact:
    manifest: Manifest
    payloads: tuple[bytes, ...]


def encode_candidate(
    attempt: PreparedTrainingAttempt,
    artifacts: tuple[CandidateArtifact, ...],
) -> bytes:
    if not isinstance(artifacts, tuple) or not 1 <= len(artifacts) <= attempt.limits.max_artifacts:
        raise BoundaryError(_STAGE, "candidate_artifact_limit")
    manifests, blobs = [], {}
    for artifact in artifacts:
        if not isinstance(artifact, CandidateArtifact):
            raise BoundaryError(_STAGE, "typed_candidate_required")
        manifest = artifact.manifest
        if (
            not isinstance(manifest, Manifest)
            or manifest.kind not in _CANDIDATE_KINDS
            or manifest.producer != attempt.producer
            or manifest.parent("run") != attempt.run_id
        ):
            raise BoundaryError(_STAGE, "canonical_or_foreign_artifact_forbidden")
        if any(
            parent.role == "training_input" and parent.artifact_id != attempt.training_input_id
            for parent in manifest.parents
        ):
            raise BoundaryError(_STAGE, "candidate_training_input_mismatch")
        if not isinstance(artifact.payloads, tuple) or len(artifact.payloads) != len(
            manifest.payloads
        ):
            raise BoundaryError(_STAGE, "candidate_payload_inventory")
        for payload, raw in zip(manifest.payloads, artifact.payloads, strict=True):
            if type(raw) is not bytes or len(raw) != payload.size or _sha(raw) != payload.sha256:
                raise BoundaryError(_STAGE, "candidate_payload_integrity")
            blobs[payload.sha256] = raw
        manifests.append({"id": manifest.artifact_id, "raw": manifest.to_bytes().decode()})
    if len({row["id"] for row in manifests}) != len(manifests):
        raise BoundaryError(_STAGE, "duplicate_candidate_manifest")
    ordered = sorted(blobs)
    return _pack(
        _CANDIDATE_MAGIC,
        {
            "schema": "spireagent/training-candidate-delta-v1",
            "prepared_id": attempt.identity,
            "manifests": sorted(manifests, key=lambda row: row["id"]),
            "blobs": [{"sha256": key, "size": len(blobs[key])} for key in ordered],
        },
        tuple(blobs[key] for key in ordered),
        attempt.limits.max_candidate_bytes,
    )


def decode_candidate(raw: bytes, attempt: PreparedTrainingAttempt) -> tuple[CandidateArtifact, ...]:
    header, body = _unpack(raw, _CANDIDATE_MAGIC, attempt.limits.max_candidate_bytes)
    obj = object_fields(header, {"schema", "prepared_id", "manifests", "blobs"}, _STAGE)
    if (
        obj["schema"] != "spireagent/training-candidate-delta-v1"
        or obj["prepared_id"] != attempt.identity
    ):
        raise BoundaryError(_STAGE, "candidate_attempt_mismatch")
    if (
        not isinstance(obj["manifests"], list)
        or not 1 <= len(obj["manifests"]) <= attempt.limits.max_artifacts
        or not isinstance(obj["blobs"], list)
    ):
        raise BoundaryError(_STAGE, "candidate_inventory_limit")
    blobs, cursor = {}, 0
    for entry in obj["blobs"]:
        row = object_fields(entry, {"sha256", "size"}, _STAGE)
        size, key = row["size"], row["sha256"]
        if (
            type(size) is not int
            or not 0 <= size <= len(body) - cursor
            or key in blobs
            or type(key) is not str
        ):
            raise BoundaryError(_STAGE, "invalid_candidate_blob_inventory")
        blob = body[cursor : cursor + size]
        if _sha(blob) != key:
            raise BoundaryError(_STAGE, "candidate_blob_digest")
        blobs[key], cursor = blob, cursor + size
    artifacts: list[CandidateArtifact] = []
    used: set[str] = set()
    for entry in obj["manifests"]:
        row = object_fields(entry, {"id", "raw"}, _STAGE)
        if type(row["raw"]) is not str:
            raise BoundaryError(_STAGE, "invalid_candidate_manifest")
        manifest = Manifest.from_bytes(row["raw"].encode(), row["id"])
        try:
            payloads = tuple(blobs[payload.sha256] for payload in manifest.payloads)
        except KeyError as error:
            raise BoundaryError(_STAGE, "candidate_payload_missing") from error
        used.update(payload.sha256 for payload in manifest.payloads)
        artifacts.append(CandidateArtifact(manifest, payloads))
    result = tuple(artifacts)
    if cursor != len(body) or used != set(blobs) or encode_candidate(attempt, result) != raw:
        raise BoundaryError(_STAGE, "noncanonical_candidate_or_unreferenced_blob")
    return result


def encode_response(
    attempt: PreparedTrainingAttempt,
    *,
    candidate: bytes,
    runtime: FrozenObject,
    counters: FrozenObject,
    finish_reason: str,
) -> bytes:
    decode_candidate(candidate, attempt)
    _validate_observation(attempt, runtime, counters, finish_reason)
    return _pack(
        _RESPONSE_MAGIC,
        {
            "schema": "spireagent/training-candidate-response-v1",
            "prepared_id": attempt.identity,
            "runtime": runtime.value(),
            "counters": counters.value(),
            "finish_reason": finish_reason,
            "candidate_sha256": _sha(candidate),
            "candidate_size": len(candidate),
        },
        (candidate,),
        attempt.limits.max_candidate_bytes,
    )


def _validate_observation(
    attempt: PreparedTrainingAttempt,
    runtime: FrozenObject,
    counters: FrozenObject,
    finish_reason: str,
) -> None:
    if runtime != attempt.runtime or finish_reason not in {"slice_boundary", "workload_complete"}:
        raise BoundaryError(_STAGE, "runtime_or_finish_mismatch")
    counts = object_fields(counters.value(), {"optimizer_updates", "boundaries"}, _STAGE)
    for name, maximum in (
        ("optimizer_updates", attempt.limits.max_optimizer_updates),
        ("boundaries", attempt.limits.max_boundaries),
    ):
        if type(counts[name]) is not int or not 0 <= counts[name] <= maximum:
            raise BoundaryError(_STAGE, "slice_work_limit_exceeded")
    if counts["optimizer_updates"] > counts["boundaries"]:
        raise BoundaryError(_STAGE, "slice_counters_inconsistent")


def decode_response(
    raw: bytes, attempt: PreparedTrainingAttempt, handle: TrainingCallHandle
) -> CandidateResult:
    """Associate worker bytes with the actual saved provider call, not worker assertions."""
    handle.app_ref.bind(attempt)
    obj, candidate = _unpack(raw, _RESPONSE_MAGIC, attempt.limits.max_candidate_bytes)
    obj = object_fields(
        obj,
        {
            "schema",
            "prepared_id",
            "runtime",
            "counters",
            "finish_reason",
            "candidate_sha256",
            "candidate_size",
        },
        _STAGE,
    )
    if (
        obj["schema"] != "spireagent/training-candidate-response-v1"
        or obj["prepared_id"] != attempt.identity
        or type(obj["candidate_size"]) is not int
        or len(candidate) != obj["candidate_size"]
        or _sha(candidate) != obj["candidate_sha256"]
    ):
        raise BoundaryError(_STAGE, "response_binding_or_integrity")
    runtime, counters = FrozenObject.of(obj["runtime"]), FrozenObject.of(obj["counters"])
    _validate_observation(attempt, runtime, counters, obj["finish_reason"])
    decode_candidate(candidate, attempt)
    return CandidateResult(handle, candidate, obj["finish_reason"], runtime, counters)


def known_transport_failure(attempt: PreparedTrainingAttempt, code: str) -> KnownFailure:
    """Codes only; never forward SDK stderr, exception messages or signed URLs."""
    if code not in {
        "capability_unavailable",
        "runtime_mismatch",
        "projection_invalid",
        "worker_failed",
    }:
        raise BoundaryError(_STAGE, "unknown_failure_code")
    return KnownFailure(attempt.identity, code)
