"""Bounded training execution facts; the application owns durability and selection.

This contract does not reinterpret ComputeRequest's frozen-feature training kind.
An executor receives a finite slice, never the canonical journal/Store authority.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    decode_json,
    digest,
    json_bytes,
    object_fields,
    text,
)

ATTEMPT_SCHEMA = "spireagent/prepared-training-attempt-v1"
MAX_METADATA_BYTES = 64 * 1024
MAX_TRANSPORT_BYTES = 256 * 1024 * 1024
STAGE = "training_execution"
TRAINING_WORKER_CAPABILITY = "structured-m2-slice-v1"


def _worker_capability(value: object) -> None:
    if value != TRAINING_WORKER_CAPABILITY:
        raise BoundaryError(STAGE, "unsupported_worker_capability")


def _positive(value: object, name: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise BoundaryError(STAGE, "invalid_" + name)
    return value


def _provider_id(value: object, prefix: str) -> str:
    if not isinstance(value, str) or re.fullmatch(prefix + r"[A-Za-z0-9_-]{1,96}", value) is None:
        raise BoundaryError(STAGE, "invalid_provider_id")
    return value


@dataclass(frozen=True)
class TrainingSliceLimits:
    """Work limits are numerical authorization, not provider price predictions."""

    max_optimizer_updates: int
    max_boundaries: int
    wall_seconds: int
    startup_seconds: int
    max_request_bytes: int
    max_projection_bytes: int
    max_candidate_bytes: int
    max_artifacts: int
    cpu_millicores: int
    memory_mib: int
    cost_authorization_id: str
    max_cost_microusd: int

    def __post_init__(self) -> None:
        for field, maximum in (
            ("max_optimizer_updates", 100_000),
            ("max_boundaries", 1_000_000),
            ("wall_seconds", 86_400),
            ("startup_seconds", 3600),
            ("max_request_bytes", MAX_METADATA_BYTES),
            ("max_projection_bytes", MAX_TRANSPORT_BYTES),
            ("max_candidate_bytes", MAX_TRANSPORT_BYTES),
            ("max_artifacts", 4096),
            ("cpu_millicores", 64_000),
            ("memory_mib", 262_144),
        ):
            _positive(getattr(self, field), field, maximum)
        if self.max_boundaries < self.max_optimizer_updates:
            raise BoundaryError(STAGE, "update_boundary_limits_inconsistent")
        digest(self.cost_authorization_id, STAGE)
        if type(self.max_cost_microusd) is not int or not 0 <= self.max_cost_microusd <= 10**12:
            raise BoundaryError(STAGE, "invalid_cost_limit")

    @classmethod
    def from_dict(cls, value: object) -> TrainingSliceLimits:
        return cls(**object_fields(value, set(cls.__dataclass_fields__), STAGE))


@dataclass(frozen=True)
class TrainingPlacement:
    provider: str
    account_id: str
    environment: str
    image_object_id: str
    gpu: str
    device: str

    def __post_init__(self) -> None:
        if self.provider != "modal":
            raise BoundaryError(STAGE, "unsupported_provider")
        for value in (self.account_id, self.environment):
            if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value) is None:
                raise BoundaryError(STAGE, "invalid_placement_identity")
        _provider_id(self.image_object_id, "im-")
        if self.gpu not in {"none", "L4", "A10", "L40S", "A100-40GB", "A100-80GB", "H100!"}:
            raise BoundaryError(STAGE, "unsupported_gpu")
        if self.device not in {"cpu", "cuda:0"} or (self.gpu == "none") != (self.device == "cpu"):
            raise BoundaryError(STAGE, "device_resource_mismatch")

    @classmethod
    def from_dict(cls, value: object) -> TrainingPlacement:
        return cls(**object_fields(value, set(cls.__dataclass_fields__), STAGE))


@dataclass(frozen=True)
class PreparedTrainingAttempt:
    run_id: str
    training_input_id: str
    operation_id: str
    attempt_id: str
    request_sha256: str
    projection_sha256: str
    producer: Producer
    code_closure_sha256: str
    worker_capability: str
    runtime: FrozenObject
    placement: TrainingPlacement
    limits: TrainingSliceLimits

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "training_input_id",
            "request_sha256",
            "projection_sha256",
            "code_closure_sha256",
        ):
            digest(getattr(self, name), STAGE)
        for name in ("operation_id", "attempt_id"):
            digest(getattr(self, name), STAGE, length=32)
        if (
            not isinstance(self.producer, Producer)
            or not isinstance(self.runtime, FrozenObject)
            or not isinstance(self.placement, TrainingPlacement)
            or not isinstance(self.limits, TrainingSliceLimits)
        ):
            raise BoundaryError(STAGE, "typed_attempt_required")
        _worker_capability(self.worker_capability)
        runtime = self.runtime.value()
        # Reuse the durable artifact metadata boundary; this does not publish.
        Manifest("analysis", self.producer, parameters=self.runtime)
        if runtime.get("device") != self.placement.device or runtime.get("dtype") != "float32":
            raise BoundaryError(STAGE, "runtime_device_or_dtype_mismatch")
        for name in ("torch_version", "python_version"):
            text(runtime.get(name), STAGE, maximum=96)
        if len(self.to_bytes()) > MAX_METADATA_BYTES:
            raise BoundaryError(STAGE, "attempt_metadata_limit")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": ATTEMPT_SCHEMA,
            **{
                name: getattr(self, name)
                for name in (
                    "run_id",
                    "training_input_id",
                    "operation_id",
                    "attempt_id",
                    "request_sha256",
                    "projection_sha256",
                    "code_closure_sha256",
                    "worker_capability",
                )
            },
            "producer": self.producer.to_dict(),
            "runtime": self.runtime.value(),
            "placement": asdict(self.placement),
            "limits": asdict(self.limits),
        }

    def to_bytes(self) -> bytes:
        return json_bytes(self.to_dict())

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()

    @property
    def execution_binding_id(self) -> str:
        return training_execution_binding(
            self.producer,
            self.code_closure_sha256,
            self.worker_capability,
            self.runtime,
            self.placement,
            self.limits,
        )

    @property
    def app_name(self) -> str:
        return "spireagent-train-" + self.attempt_id + "-" + self.identity[:12]

    @classmethod
    def from_dict(cls, value: object) -> PreparedTrainingAttempt:
        obj = dict(object_fields(value, {"schema", *cls.__dataclass_fields__}, STAGE))
        if obj.pop("schema") != ATTEMPT_SCHEMA:
            raise BoundaryError(STAGE, "unsupported_attempt_schema")
        obj["producer"] = Producer.decode(obj["producer"])
        obj["runtime"] = FrozenObject.of(obj["runtime"])
        obj["placement"] = TrainingPlacement.from_dict(obj["placement"])
        obj["limits"] = TrainingSliceLimits.from_dict(obj["limits"])
        return cls(**obj)

    @classmethod
    def from_bytes(cls, raw: bytes) -> PreparedTrainingAttempt:
        if type(raw) is not bytes or not 1 <= len(raw) <= MAX_METADATA_BYTES:
            raise BoundaryError(STAGE, "attempt_metadata_limit")
        result = cls.from_dict(decode_json(raw))
        if result.to_bytes() != raw:
            raise BoundaryError(STAGE, "noncanonical_attempt")
        return result

    def bind_payloads(self, request: bytes, projection: bytes) -> None:
        for raw, expected, maximum in (
            (request, self.request_sha256, self.limits.max_request_bytes),
            (projection, self.projection_sha256, self.limits.max_projection_bytes),
        ):
            if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
                raise BoundaryError(STAGE, "payload_size_limit")
            if hashlib.sha256(raw).hexdigest() != expected:
                raise BoundaryError(STAGE, "prepared_payload_mismatch")


@dataclass(frozen=True)
class TrainingAppRef:
    prepared_id: str
    app_id: str
    function_id: str
    app_name: str
    account_id: str
    environment: str
    image_object_id: str

    def __post_init__(self) -> None:
        digest(self.prepared_id, STAGE)
        _provider_id(self.app_id, "ap-")
        _provider_id(self.function_id, "fu-")
        _provider_id(self.image_object_id, "im-")
        for value in (self.app_name, self.account_id, self.environment):
            text(value, STAGE, maximum=128)

    def bind(self, attempt: PreparedTrainingAttempt) -> None:
        if (
            self.prepared_id != attempt.identity
            or self.app_name != attempt.app_name
            or self.account_id != attempt.placement.account_id
            or self.environment != attempt.placement.environment
            or self.image_object_id != attempt.placement.image_object_id
        ):
            raise BoundaryError(STAGE, "app_ref_binding_mismatch")

    def to_dict(self) -> dict[str, str]:
        return {"schema": "spireagent/training-app-ref-v1", **asdict(self)}

    @classmethod
    def from_dict(cls, value: object) -> TrainingAppRef:
        obj = dict(object_fields(value, {"schema", *cls.__dataclass_fields__}, STAGE))
        if obj.pop("schema") != "spireagent/training-app-ref-v1":
            raise BoundaryError(STAGE, "unsupported_app_ref")
        return cls(**obj)


@dataclass(frozen=True)
class TrainingCallHandle:
    app_ref: TrainingAppRef
    call_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.app_ref, TrainingAppRef):
            raise BoundaryError(STAGE, "typed_app_ref_required")
        _provider_id(self.call_id, "fc-")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "spireagent/training-call-handle-v1",
            "app_ref": self.app_ref.to_dict(),
            "call_id": self.call_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> TrainingCallHandle:
        obj = object_fields(value, {"schema", "app_ref", "call_id"}, STAGE)
        if obj["schema"] != "spireagent/training-call-handle-v1":
            raise BoundaryError(STAGE, "unsupported_handle")
        return cls(TrainingAppRef.from_dict(obj["app_ref"]), obj["call_id"])


@dataclass(frozen=True)
class CandidateResult:
    handle: TrainingCallHandle
    candidate_bytes: bytes
    finish_reason: Literal["slice_boundary", "workload_complete"]
    runtime: FrozenObject
    counters: FrozenObject

    def __post_init__(self) -> None:
        if (
            not isinstance(self.handle, TrainingCallHandle)
            or type(self.candidate_bytes) is not bytes
            or not 1 <= len(self.candidate_bytes) <= MAX_TRANSPORT_BYTES
            or self.finish_reason not in {"slice_boundary", "workload_complete"}
            or not isinstance(self.runtime, FrozenObject)
            or not isinstance(self.counters, FrozenObject)
        ):
            raise BoundaryError(STAGE, "typed_candidate_result_required")


@dataclass(frozen=True)
class KnownFailure:
    """Post-submission terminal evidence requires the exact original handle."""

    prepared_id: str
    code: str
    handle: TrainingCallHandle | None = None

    def __post_init__(self) -> None:
        _observation_code(self.prepared_id, self.code)
        if self.handle is not None and not isinstance(self.handle, TrainingCallHandle):
            raise BoundaryError(STAGE, "typed_call_handle_required")


@dataclass(frozen=True)
class Unknown:
    prepared_id: str
    code: str
    app_ref: TrainingAppRef | None = None
    handle: TrainingCallHandle | None = None

    def __post_init__(self) -> None:
        _observation_code(self.prepared_id, self.code)
        if (
            self.app_ref is not None
            and not isinstance(self.app_ref, TrainingAppRef)
            or self.handle is not None
            and not isinstance(self.handle, TrainingCallHandle)
            or self.handle is not None
            and self.app_ref != self.handle.app_ref
        ):
            raise BoundaryError(STAGE, "typed_unknown_binding_required")


TrainingObservation = CandidateResult | KnownFailure | Unknown | None


class TrainingClaim(StrEnum):
    """Durable prior intent and a withheld first effect are different facts."""

    CLAIMED = "claimed"
    PRIOR = "prior"
    WITHHELD = "withheld"


@dataclass(frozen=True)
class NotStarted:
    """No provider side effect occurred at the named boundary.

    A submit no-effect does not prove an already prepared App stopped. Only the
    original application may map this fact to control state or release authority.
    """

    prepared_id: str
    stage: Literal["prepare", "submit"]
    code: Literal["owner_control_withheld", "submission_window_exhausted", "attempt_deadline"]

    def __post_init__(self) -> None:
        digest(self.prepared_id, STAGE)
        if self.stage not in {"prepare", "submit"} or self.code not in {
            "owner_control_withheld", "submission_window_exhausted", "attempt_deadline"
        }:
            raise BoundaryError(STAGE, "invalid_not_started")


@dataclass(frozen=True)
class TrainingProviderBudget:
    """Read-only dynamic allowance from the original attempt's application owner.

    The callback includes already elapsed preparation and the owner's explicit
    cleanup reserve. This object grants neither a fresh deadline nor new work.
    """

    remaining_seconds: Callable[[], float]

    def remaining(self) -> float:
        value = self.remaining_seconds()
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            raise BoundaryError(STAGE, "invalid_owner_remaining_budget")
        return float(value)

    def timeout(self, maximum: float, *, reserve: float = 0) -> float:
        if (
            type(maximum) not in {int, float} or not math.isfinite(maximum) or maximum <= 0
            or type(reserve) not in {int, float} or not math.isfinite(reserve) or reserve < 0
        ):
            raise BoundaryError(STAGE, "invalid_provider_timeout")
        available = self.remaining() - reserve
        if available <= 0:
            raise BoundaryError(STAGE, "attempt_deadline")
        return min(float(maximum), available)

    def require_submission_window(self, attempt: PreparedTrainingAttempt) -> None:
        self.timeout(30, reserve=attempt.limits.startup_seconds + attempt.limits.wall_seconds)

    def reserving(self, seconds: float) -> TrainingProviderBudget:
        """Reserve the full declared remote window throughout submission lookups."""
        self.timeout(30, reserve=seconds)
        return TrainingProviderBudget(lambda: max(0, self.remaining() - seconds))


def _observation_code(prepared_id: str, code: str) -> None:
    digest(prepared_id, STAGE)
    if not isinstance(code, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,95}", code) is None:
        raise BoundaryError(STAGE, "invalid_observation_code")


@dataclass(frozen=True)
class StopInspection:
    app_ref: TrainingAppRef
    state: Literal["stopped", "not_stopped", "unknown"]
    running_tasks: int | None
    running_containers: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.app_ref, TrainingAppRef):
            raise BoundaryError(STAGE, "typed_app_ref_required")
        if self.state not in {"stopped", "not_stopped", "unknown"}:
            raise BoundaryError(STAGE, "invalid_stop_state")
        for value in (self.running_tasks, self.running_containers):
            if value is not None and (type(value) is not int or value < 0):
                raise BoundaryError(STAGE, "invalid_stop_count")

    @property
    def confirmed(self) -> bool:
        return self.state == "stopped" and self.running_tasks == self.running_containers == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "spireagent/training-stop-inspection-v1",
            "app_ref": self.app_ref.to_dict(),
            "state": self.state,
            "running_tasks": self.running_tasks,
            "running_containers": self.running_containers,
        }

    @classmethod
    def from_dict(cls, value: object) -> StopInspection:
        obj = dict(
            object_fields(
                value, {"schema", "app_ref", "state", "running_tasks", "running_containers"}, STAGE
            )
        )
        if obj.pop("schema") != "spireagent/training-stop-inspection-v1":
            raise BoundaryError(STAGE, "unsupported_stop_inspection")
        obj["app_ref"] = TrainingAppRef.from_dict(obj["app_ref"])
        return cls(**obj)


def training_execution_binding(
    producer: Producer,
    code_closure_sha256: str,
    worker_capability: str,
    runtime: FrozenObject,
    placement: TrainingPlacement,
    limits: TrainingSliceLimits,
) -> str:
    """Target digest can enter a domain request without a self-referential hash."""
    digest(code_closure_sha256, STAGE)
    _worker_capability(worker_capability)
    if (
        not isinstance(producer, Producer)
        or not isinstance(runtime, FrozenObject)
        or not isinstance(placement, TrainingPlacement)
        or not isinstance(limits, TrainingSliceLimits)
    ):
        raise BoundaryError(STAGE, "typed_execution_binding_required")
    return hashlib.sha256(
        json_bytes(
            {
                "schema": "spireagent/training-execution-binding-v1",
                "producer": producer.to_dict(),
                "code_closure_sha256": code_closure_sha256,
                "worker_capability": worker_capability,
                "runtime": runtime.value(),
                "placement": asdict(placement),
                "limits": asdict(limits),
            }
        )
    ).hexdigest()


class TrainingAttemptOwnerPort(Protocol):
    """Implemented by the original application journal; never by a remote worker.

    CLAIMED persists intent before return. PRIOR only permits reconciliation.
    WITHHELD proves no first effect was admitted; it is not a prior mutation.
    Remaining budget comes from the original attempt start, including preparation,
    and the owner's explicit current work/cleanup phase inside the same wallet.
    """

    def load_app_ref(self, prepared_id: str) -> TrainingAppRef | None: ...
    def remaining_seconds(self, prepared_id: str) -> float: ...
    def claim_preparation(self, attempt: PreparedTrainingAttempt) -> TrainingClaim: ...
    def persist_app_ref(self, attempt: PreparedTrainingAttempt, ref: TrainingAppRef) -> None: ...
    def claim_submission_once(
        self, attempt: PreparedTrainingAttempt, ref: TrainingAppRef
    ) -> TrainingClaim: ...
    def load_call_handle(self, prepared_id: str) -> TrainingCallHandle | None: ...
    def persist_call_handle(
        self, attempt: PreparedTrainingAttempt, handle: TrainingCallHandle
    ) -> None: ...


class TrainingExecutor(Protocol):
    def prepare(
        self, attempt: PreparedTrainingAttempt
    ) -> TrainingAppRef | NotStarted | Unknown: ...
    def recover_preparation(self, attempt: PreparedTrainingAttempt) -> TrainingAppRef | Unknown: ...
    def submit_once(
        self,
        attempt: PreparedTrainingAttempt,
        request: bytes,
        projection: bytes,
        app_ref: TrainingAppRef,
    ) -> TrainingCallHandle | NotStarted | Unknown: ...
    def poll(
        self, attempt: PreparedTrainingAttempt, handle: TrainingCallHandle
    ) -> TrainingObservation: ...
    def reconcile(
        self,
        attempt: PreparedTrainingAttempt,
        app_ref: TrainingAppRef,
        handle: TrainingCallHandle | None = None,
    ) -> TrainingObservation: ...
    def cancel(self, attempt: PreparedTrainingAttempt, handle: TrainingCallHandle) -> bool: ...
    def stop_and_inspect(
        self, attempt: PreparedTrainingAttempt, app_ref: TrainingAppRef
    ) -> StopInspection: ...
