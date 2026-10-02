"""Bounded Modal transport for owner-admitted train-only M0 updates.

This adapter never prepares owner work, selects a completion, or publishes artifacts.
The caller persists its attempt before ``submit`` and persists the returned call handle
before polling. A submit exception is ambiguous and must not trigger another submit.

Request/result structure and local checkpoint validation do not prove that an arbitrary
malicious remote actually performed the claimed mathematics. This is a provider-bound
execution seam, not TEE or recomputation evidence.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import platform
import re
import runpy
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes, object_fields
from spireagent.source import REPOSITORY, source_identity

from ..canonical import semantic_hash

MODAL_SDK_VERSION = "1.5.5"
CALL_SCHEMA = "stpd/modal-m0-call-v3"
APP_REF_SCHEMA = "stpd/modal-m0-app-ref-v1"
ATTEMPT_SPEC_SCHEMA = "stpd/modal-m0-attempt-spec-v1"
M0_MODAL_APP_NAME = "stpd-m0-update-"
M0_MODAL_FUNCTION_NAME = "token_remote_update"
MAX_MODAL_COMMAND_SECONDS = 120
MAX_MODAL_CONTROL_SECONDS = 5

# A resumed request base64-embeds its checkpoint; leave headroom over the current
# ~81-MiB checkpoint result. Modal object-stores payloads over 2 MiB automatically.
MAX_M0_REQUEST_BYTES = 256 * 1024 * 1024
MAX_M0_RESULT_BYTES = 128 * 1024 * 1024
MAX_POLL_TIMEOUT_SECONDS = 60.0
_FRAME_MAGIC = b"STPD-M0-MODAL-RESULT\x00"
_MAX_FRAME_HEADER_BYTES = 16 * 1024

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}\Z")
_MODAL_ID_RE = {
    "app": re.compile(r"ap-[A-Za-z0-9_-]+\Z"),
    "function": re.compile(r"fu-[A-Za-z0-9_-]+\Z"),
    "image": re.compile(r"im-[A-Za-z0-9_-]+\Z"),
    "call": re.compile(r"fc-[A-Za-z0-9_-]+\Z"),
}


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise BoundaryError("modal_m0", f"invalid_{field}")
    return value


def _runtime_version(value: object, field: str) -> str:
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        raise BoundaryError("modal_m0", f"invalid_{field}")
    return value


def _token_target_runtime(value: object) -> Any:
    from ..workers.token_ranking import TokenTargetRuntime

    if not isinstance(value, TokenTargetRuntime):
        raise BoundaryError("modal_m0", "typed_target_runtime_required")
    return value


@dataclass(frozen=True)
class M0ExecutionPlan:
    """Fixed, explicitly bounded first-pilot Modal function resource plan."""

    gpu: str = "L4"
    cpu: float = 2.0
    memory_mib: int = 8192
    function_timeout_seconds: int = 900
    startup_timeout_seconds: int = 120
    scaledown_seconds: int = 30
    max_containers: int = 1
    retries: int = 0

    def __post_init__(self) -> None:
        if json_bytes(self.to_dict()) != json_bytes(_M0_EXECUTION_PLAN_VALUES):
            raise BoundaryError("modal_m0_plan", "unsupported_execution_plan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "gpu": self.gpu,
            "cpu": self.cpu,
            "memory_mib": self.memory_mib,
            "function_timeout_seconds": self.function_timeout_seconds,
            "startup_timeout_seconds": self.startup_timeout_seconds,
            "scaledown_seconds": self.scaledown_seconds,
            "max_containers": self.max_containers,
            "retries": self.retries,
        }

    @property
    def plan_sha256(self) -> str:
        return hashlib.sha256(json_bytes(self.to_dict())).hexdigest()

    @classmethod
    def from_dict(cls, value: object) -> M0ExecutionPlan:
        return cls(**object_fields(value, set(_M0_EXECUTION_PLAN_VALUES), "modal_m0_plan"))


_M0_EXECUTION_PLAN_VALUES = {
    "gpu": "L4",
    "cpu": 2.0,
    "memory_mib": 8192,
    "function_timeout_seconds": 900,
    "startup_timeout_seconds": 120,
    "scaledown_seconds": 30,
    "max_containers": 1,
    "retries": 0,
}
M0_PILOT_EXECUTION_PLAN = M0ExecutionPlan()


@dataclass(frozen=True)
class M0AttemptSpec:
    """Durable pre-deploy identity; persist these bytes before calling prepare_app."""

    attempt_id: str
    request_sha256: str
    account_id: str
    environment_name: str
    image_object_id: str
    producer: Producer
    target_runtime: Any
    resource_plan: M0ExecutionPlan = M0_PILOT_EXECUTION_PLAN

    def __post_init__(self) -> None:
        digest(self.attempt_id, "modal_m0_attempt.attempt_id", length=32)
        digest(self.request_sha256, "modal_m0_attempt.request_sha256")
        _identifier(self.account_id, "account_id")
        _identifier(self.environment_name, "environment_name")
        if not isinstance(self.producer, Producer):
            raise BoundaryError("modal_m0_attempt", "untyped_producer")
        if not isinstance(self.image_object_id, str) or not _MODAL_ID_RE["image"].fullmatch(
            self.image_object_id
        ):
            raise BoundaryError("modal_m0_attempt", "invalid_image_object_id")
        _token_target_runtime(self.target_runtime)
        if not isinstance(self.resource_plan, M0ExecutionPlan):
            raise BoundaryError("modal_m0_attempt", "typed_execution_plan_required")

    @property
    def app_name(self) -> str:
        return f"{M0_MODAL_APP_NAME}{self.attempt_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": ATTEMPT_SPEC_SCHEMA,
            "attempt_id": self.attempt_id,
            "request_sha256": self.request_sha256,
            "account_id": self.account_id,
            "environment_name": self.environment_name,
            "image_object_id": self.image_object_id,
            "producer": self.producer.to_dict(),
            "target_runtime": {
                "torch_version": self.target_runtime.torch_version,
                "cpu_threads": self.target_runtime.cpu_threads,
            },
            "resource_plan": self.resource_plan.to_dict(),
            "app_name": self.app_name,
        }

    def to_bytes(self) -> bytes:
        return json_bytes(self.to_dict())

    @classmethod
    def from_bytes(cls, raw: bytes) -> M0AttemptSpec:
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= 128 * 1024:
            raise BoundaryError("modal_m0_attempt", "spec_size_limit")
        obj = dict(object_fields(decode_json(raw), {
            "schema", "attempt_id", "request_sha256", "account_id", "environment_name",
            "image_object_id", "producer", "target_runtime", "resource_plan", "app_name",
        }, "modal_m0_attempt"))
        if raw != json_bytes(obj) or obj.pop("schema") != ATTEMPT_SPEC_SCHEMA:
            raise BoundaryError("modal_m0_attempt", "unsupported_or_noncanonical_spec")
        target_runtime = dict(object_fields(
            obj.pop("target_runtime"), {"torch_version", "cpu_threads"}, "modal_m0_attempt_runtime",
        ))
        from ..workers.token_ranking import TokenTargetRuntime

        obj["target_runtime"] = TokenTargetRuntime(**target_runtime)
        obj["producer"] = Producer.decode(obj["producer"])
        obj["resource_plan"] = M0ExecutionPlan.from_dict(obj["resource_plan"])
        app_name = obj.pop("app_name")
        spec = cls(**obj)
        if app_name != spec.app_name:
            raise BoundaryError("modal_m0_attempt", "app_name_attempt_mismatch")
        return spec


@dataclass(frozen=True)
class ModalM0Target:
    """Serialized exact Modal App/function binding for a single update attempt."""

    account_id: str
    environment_name: str
    app_id: str
    app_version: int
    function_id: str
    image_object_id: str
    producer: Producer
    attempt_id: str
    request_sha256: str
    target_runtime: Any
    resource_plan: M0ExecutionPlan = M0_PILOT_EXECUTION_PLAN
    function_name: str = M0_MODAL_FUNCTION_NAME

    def __post_init__(self) -> None:
        _identifier(self.account_id, "account_id")
        _identifier(self.environment_name, "environment_name")
        digest(self.attempt_id, "modal_m0_target.attempt_id", length=32)
        digest(self.request_sha256, "modal_m0_target.request_sha256")
        _token_target_runtime(self.target_runtime)
        if not isinstance(self.producer, Producer):
            raise BoundaryError("modal_m0_target", "untyped_producer")
        for value, kind in (
            (self.app_id, "app"),
            (self.function_id, "function"),
            (self.image_object_id, "image"),
        ):
            if not isinstance(value, str) or not _MODAL_ID_RE[kind].fullmatch(value):
                raise BoundaryError("modal_m0_target", f"invalid_{kind}_object_id")
        if type(self.app_version) is not int or self.app_version < 1:
            raise BoundaryError("modal_m0_target", "invalid_app_version")
        if not isinstance(self.resource_plan, M0ExecutionPlan):
            raise BoundaryError("modal_m0_target", "typed_execution_plan_required")
        if self.function_name != M0_MODAL_FUNCTION_NAME:
            raise BoundaryError("modal_m0_target", "wrong_deployed_entry")

    @property
    def app_name(self) -> str:
        return f"{M0_MODAL_APP_NAME}{self.attempt_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "environment_name": self.environment_name,
            "app_id": self.app_id,
            "app_version": self.app_version,
            "function_id": self.function_id,
            "image_object_id": self.image_object_id,
            "producer": self.producer.to_dict(),
            "attempt_id": self.attempt_id,
            "request_sha256": self.request_sha256,
            "target_runtime": {
                "torch_version": self.target_runtime.torch_version,
                "cpu_threads": self.target_runtime.cpu_threads,
            },
            "resource_plan": self.resource_plan.to_dict(),
            "app_name": self.app_name,
            "function_name": self.function_name,
        }

    def to_bytes(self) -> bytes:
        return json_bytes({"schema": APP_REF_SCHEMA, **self.to_dict()})

    @classmethod
    def from_bytes(cls, raw: bytes) -> ModalM0Target:
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= 128 * 1024:
            raise BoundaryError("modal_m0_app_ref", "handle_size_limit")
        obj = dict(object_fields(decode_json(raw), {
            "schema", "account_id", "environment_name", "app_id", "app_version",
            "function_id", "image_object_id", "producer", "attempt_id", "request_sha256",
            "target_runtime", "resource_plan", "app_name", "function_name",
        }, "modal_m0_app_ref"))
        if raw != json_bytes(obj) or obj.pop("schema") != APP_REF_SCHEMA:
            raise BoundaryError("modal_m0_app_ref", "unsupported_or_noncanonical_handle")
        app_name = obj.pop("app_name")
        runtime_value = dict(object_fields(
            obj.pop("target_runtime"), {"torch_version", "cpu_threads"}, "modal_m0_app_ref_runtime",
        ))
        from ..workers.token_ranking import TokenTargetRuntime

        obj["target_runtime"] = TokenTargetRuntime(**runtime_value)
        obj["producer"] = Producer.decode(obj["producer"])
        obj["resource_plan"] = M0ExecutionPlan.from_dict(obj["resource_plan"])
        target = cls(**obj)
        if app_name != target.app_name:
            raise BoundaryError("modal_m0_app_ref", "app_name_attempt_mismatch")
        return target

    @property
    def target_id(self) -> str:
        return semantic_hash(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> ModalM0Target:
        obj = dict(
            object_fields(
                value,
                {
                    "account_id",
                    "environment_name",
                    "app_id",
                    "app_version",
                    "function_id",
                    "image_object_id",
                    "producer",
                    "attempt_id",
                    "request_sha256",
                    "target_runtime",
                    "resource_plan",
                    "app_name",
                    "function_name",
                },
                "modal_m0_target",
            )
        )
        obj["producer"] = Producer.decode(obj["producer"])
        runtime_value = dict(object_fields(
            obj["target_runtime"], {"torch_version", "cpu_threads"}, "modal_m0_target_runtime",
        ))
        from ..workers.token_ranking import TokenTargetRuntime

        obj["target_runtime"] = TokenTargetRuntime(**runtime_value)
        obj["resource_plan"] = M0ExecutionPlan.from_dict(obj["resource_plan"])
        app_name = obj.pop("app_name")
        target = cls(**obj)
        if app_name != target.app_name:
            raise BoundaryError("modal_m0_target", "app_name_attempt_mismatch")
        return target


M0AppRef = ModalM0Target


def _target_matches_spec(target: ModalM0Target, spec: M0AttemptSpec) -> bool:
    return (
        target.account_id == spec.account_id
        and target.environment_name == spec.environment_name
        and target.attempt_id == spec.attempt_id
        and target.request_sha256 == spec.request_sha256
        and target.producer == spec.producer
        and target.image_object_id == spec.image_object_id
        and target.target_runtime == spec.target_runtime
        and target.resource_plan == spec.resource_plan
        and target.app_name == spec.app_name
    )


@dataclass(frozen=True)
class ModalM0TargetRuntimeMetadata:
    """Runtime values associated with one exact provider target and locked source."""

    target_id: str
    producer: Producer
    image_object_id: str
    torch_version: str
    cpu_threads: int

    def __post_init__(self) -> None:
        digest(self.target_id, "modal_m0_target_runtime.target_id")
        if not isinstance(self.producer, Producer):
            raise BoundaryError("modal_m0_target_runtime", "untyped_producer")
        if not isinstance(self.image_object_id, str) or not _MODAL_ID_RE["image"].fullmatch(
            self.image_object_id
        ):
            raise BoundaryError("modal_m0_target_runtime", "invalid_image_object_id")
        _runtime_version(self.torch_version, "torch_version")
        if type(self.cpu_threads) is not int or not 1 <= self.cpu_threads <= 256:
            raise BoundaryError("modal_m0_target_runtime", "invalid_cpu_threads")

    def bind(self, target: ModalM0Target) -> None:
        if (
            not isinstance(target, ModalM0Target)
            or self.target_id != target.target_id
            or self.producer != target.producer
            or self.image_object_id != target.image_object_id
            or self.torch_version != target.target_runtime.torch_version
            or self.cpu_threads != target.target_runtime.cpu_threads
        ):
            raise BoundaryError("modal_m0_target_runtime", "target_source_binding_mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "producer": self.producer.to_dict(),
            "image_object_id": self.image_object_id,
            "torch_version": self.torch_version,
            "cpu_threads": self.cpu_threads,
        }

    @classmethod
    def from_provider_metadata(
        cls,
        value: object,
        *,
        target: ModalM0Target,
    ) -> ModalM0TargetRuntimeMetadata:
        """Bind supplied probe/target metadata; this method performs no SDK call.

        The caller supplies metadata gathered for this immutable deployment. The
        handler still measures the actual runtime and compares it before training.
        """
        obj = dict(
            object_fields(
                value,
                {
                    "target_id",
                    "producer",
                    "image_object_id",
                    "torch_version",
                    "cpu_threads",
                },
                "modal_m0_target_runtime",
            )
        )
        obj["producer"] = Producer.decode(obj["producer"])
        metadata = cls(**obj)
        metadata.bind(target)
        return metadata

    @classmethod
    def from_dict(cls, value: object) -> ModalM0TargetRuntimeMetadata:
        obj = dict(
            object_fields(
                value,
                {
                    "target_id",
                    "producer",
                    "image_object_id",
                    "torch_version",
                    "cpu_threads",
                },
                "modal_m0_target_runtime",
            )
        )
        obj["producer"] = Producer.decode(obj["producer"])
        return cls(**obj)

    def token_target_runtime(self, target: ModalM0Target) -> Any:
        """Create the core run-preparation runtime only after exact target binding."""
        self.bind(target)
        from ..workers.token_ranking import TokenTargetRuntime
        return TokenTargetRuntime(
            torch_version=self.torch_version,
            cpu_threads=self.cpu_threads,
        )


@dataclass(frozen=True)
class M0CancelAck:
    """Provider accepted cancellation; this is never evidence that the App stopped."""

    app_id: str
    call_id: str
    acknowledged: bool
    status: Literal["acknowledged", "unknown"]


@dataclass(frozen=True)
class M0StopAck:
    """Provider accepted App stop; stop completion requires inspect_stop."""

    app_id: str
    acknowledged: bool
    status: Literal["acknowledged", "unknown"]


@dataclass(frozen=True)
class M0StopInspection:
    app_id: str
    app_state: Literal["stopped", "not_stopped", "unknown"]
    active_tasks: int | None
    active_containers: int | None
    confirmed: bool

    def __post_init__(self) -> None:
        if self.confirmed and (
            self.app_state != "stopped"
            or self.active_tasks != 0
            or self.active_containers != 0
        ):
            raise BoundaryError("modal_m0_stop", "invalid_stop_confirmation")
        for value in (self.active_tasks, self.active_containers):
            if value is not None and (type(value) is not int or value < 0):
                raise BoundaryError("modal_m0_stop", "invalid_active_count")


@dataclass(frozen=True)
class M0PrepareStoppedProof:
    """Exact stopped App proof; it makes no unobserved image/runtime claim."""

    spec: M0AttemptSpec
    canonical_account_id: str
    app_id: str
    function_id: str
    inspection: M0StopInspection

    def __post_init__(self) -> None:
        if (not isinstance(self.spec, M0AttemptSpec)
                or not isinstance(self.canonical_account_id, str)
                or re.fullmatch(r"ac-[A-Za-z0-9_-]+", self.canonical_account_id) is None
                or (self.spec.account_id.startswith("ac-")
                    and self.spec.account_id != self.canonical_account_id)
                or not isinstance(self.app_id, str)
                or not _MODAL_ID_RE["app"].fullmatch(self.app_id)
                or not isinstance(self.function_id, str)
                or not _MODAL_ID_RE["function"].fullmatch(self.function_id)
                or not isinstance(self.inspection, M0StopInspection)
                or self.inspection.app_id != self.app_id
                or self.inspection.confirmed is not True):
            raise BoundaryError("modal_m0_stop", "prepare_stop_binding_invalid")


@dataclass(frozen=True)
class ModalM0Call:
    """Durable provider handle plus the exact request identity needed to reconcile it."""

    target: ModalM0Target
    target_runtime: ModalM0TargetRuntimeMetadata
    call_id: str
    request_sha256: str
    request_size_bytes: int
    attempt_id: str
    run_id: str
    input_id: str
    operation_id: str
    producer: Producer
    result_identity_sha256: str
    target_device: str
    target_step: int
    resume_checkpoint_id: str | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.target, ModalM0Target)
            or not isinstance(self.target_runtime, ModalM0TargetRuntimeMetadata)
            or not isinstance(self.producer, Producer)
        ):
            raise BoundaryError("modal_m0_call", "untyped_identity")
        self.target_runtime.bind(self.target)
        if (
            self.attempt_id != self.target.attempt_id
            or self.request_sha256 != self.target.request_sha256
        ):
            raise BoundaryError("modal_m0_call", "app_ref_request_identity_mismatch")
        if not isinstance(self.call_id, str) or not _MODAL_ID_RE["call"].fullmatch(self.call_id):
            raise BoundaryError("modal_m0_call", "invalid_call_id")
        digest(self.request_sha256, "modal_m0_call.request_sha256")
        digest(self.attempt_id, "modal_m0_call.attempt_id", length=32)
        digest(self.run_id, "modal_m0_call.run_id")
        digest(self.input_id, "modal_m0_call.input_id")
        digest(self.operation_id, "modal_m0_call.operation_id", length=32)
        digest(self.result_identity_sha256, "modal_m0_call.result_identity_sha256")
        if (
            type(self.request_size_bytes) is not int
            or not 1 <= self.request_size_bytes <= MAX_M0_REQUEST_BYTES
        ):
            raise BoundaryError("modal_m0_call", "request_size_limit")
        if self.producer != self.target.producer or self.target_device != "cuda":
            raise BoundaryError("modal_m0_call", "request_target_mismatch")
        if type(self.target_step) is not int or self.target_step < 1:
            raise BoundaryError("modal_m0_call", "invalid_target_step")
        if self.resume_checkpoint_id is not None:
            digest(self.resume_checkpoint_id, "modal_m0_call.resume_checkpoint_id")

    @property
    def target_id(self) -> str:
        return self.target.target_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CALL_SCHEMA,
            "target": self.target.to_dict(),
            "target_id": self.target_id,
            "target_runtime": self.target_runtime.to_dict(),
            "call_id": self.call_id,
            "request_sha256": self.request_sha256,
            "request_size_bytes": self.request_size_bytes,
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "input_id": self.input_id,
            "operation_id": self.operation_id,
            "producer": self.producer.to_dict(),
            "result_identity_sha256": self.result_identity_sha256,
            "target_device": self.target_device,
            "target_step": self.target_step,
            "resume_checkpoint_id": self.resume_checkpoint_id,
        }

    def to_bytes(self) -> bytes:
        return json_bytes(self.to_dict())

    @classmethod
    def from_bytes(cls, raw: bytes) -> ModalM0Call:
        if not isinstance(raw, bytes) or len(raw) > 128 * 1024:
            raise BoundaryError("modal_m0_call", "handle_size_limit")
        obj = dict(
            object_fields(
                decode_json(raw),
                {
                    "schema",
                    "target",
                    "target_id",
                    "target_runtime",
                    "call_id",
                    "request_sha256",
                    "request_size_bytes",
                    "attempt_id",
                    "run_id",
                    "input_id",
                    "operation_id",
                    "producer",
                    "result_identity_sha256",
                    "target_device",
                    "target_step",
                    "resume_checkpoint_id",
                },
                "modal_m0_call",
            )
        )
        if raw != json_bytes(obj):
            raise BoundaryError("modal_m0_call", "noncanonical_handle")
        if obj.pop("schema") != CALL_SCHEMA:
            raise BoundaryError("modal_m0_call", "unsupported_schema")
        target = ModalM0Target.from_dict(obj.pop("target"))
        target_runtime = ModalM0TargetRuntimeMetadata.from_dict(obj.pop("target_runtime"))
        target_runtime.bind(target)
        saved_target_id = obj.pop("target_id")
        if saved_target_id != target.target_id:
            raise BoundaryError("modal_m0_call", "target_identity_mismatch")
        obj["target"] = target
        obj["target_runtime"] = target_runtime
        obj["producer"] = Producer.decode(obj["producer"])
        return cls(**obj)


@dataclass(frozen=True)
class ModalM0RuntimeEvidence:
    """Observed worker facts, locally associated with one saved call and request."""

    target_id: str | None
    call_id: str | None
    request_sha256: str
    attempt_id: str
    producer: Producer
    image_object_id: str
    torch_version: str
    python_version: str
    cuda_version: str
    cuda_available: bool
    gpu_name: str
    cpu_threads: int
    result_sha256: str
    result_size_bytes: int

    def __post_init__(self) -> None:
        if self.target_id is None and self.call_id is None:
            pass
        elif self.target_id is None or self.call_id is None:
            raise BoundaryError("modal_m0_runtime_evidence", "partial_provider_binding")
        else:
            digest(self.target_id, "modal_m0_runtime_evidence.target_id")
            if not _MODAL_ID_RE["call"].fullmatch(self.call_id):
                raise BoundaryError("modal_m0_runtime_evidence", "invalid_call_id")
        digest(self.request_sha256, "modal_m0_runtime_evidence.request_sha256")
        digest(self.attempt_id, "modal_m0_runtime_evidence.attempt_id", length=32)
        if not isinstance(self.producer, Producer):
            raise BoundaryError("modal_m0_runtime_evidence", "untyped_producer")
        if not isinstance(self.image_object_id, str) or not _MODAL_ID_RE["image"].fullmatch(
            self.image_object_id
        ):
            raise BoundaryError("modal_m0_runtime_evidence", "invalid_image_object_id")
        _runtime_version(self.torch_version, "torch_version")
        _runtime_version(self.python_version, "python_version")
        _runtime_version(self.cuda_version, "cuda_version")
        if self.cuda_available is not True:
            raise BoundaryError("modal_m0_runtime_evidence", "cuda_unavailable")
        if (
            not isinstance(self.gpu_name, str)
            or not self.gpu_name.strip()
            or len(self.gpu_name) > 160
        ):
            raise BoundaryError("modal_m0_runtime_evidence", "invalid_gpu_name")
        if type(self.cpu_threads) is not int or not 1 <= self.cpu_threads <= 256:
            raise BoundaryError("modal_m0_runtime_evidence", "invalid_cpu_threads")
        digest(self.result_sha256, "modal_m0_runtime_evidence.result_sha256")
        if (
            type(self.result_size_bytes) is not int
            or not 1 <= self.result_size_bytes <= MAX_M0_RESULT_BYTES
        ):
            raise BoundaryError("modal_m0_runtime_evidence", "result_size_limit")

    def bind(self, handle: ModalM0Call) -> None:
        if (
            self.target_id is None
            or self.call_id is None
            or self.target_id != handle.target_id
            or self.call_id != handle.call_id
            or self.request_sha256 != handle.request_sha256
            or self.attempt_id != handle.attempt_id
            or self.producer != handle.producer
            or self.image_object_id != handle.target.image_object_id
            or self.producer != handle.target.producer
            or self.torch_version != handle.target_runtime.torch_version
            or self.cpu_threads != handle.target_runtime.cpu_threads
        ):
            raise BoundaryError("modal_m0_runtime_evidence", "saved_handle_identity_mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "stpd/modal-m0-runtime-evidence-v1",
            "target_id": self.target_id,
            "call_id": self.call_id,
            "request_sha256": self.request_sha256,
            "attempt_id": self.attempt_id,
            "producer": self.producer.to_dict(),
            "image_object_id": self.image_object_id,
            "torch_version": self.torch_version,
            "python_version": self.python_version,
            "cuda_version": self.cuda_version,
            "cuda_available": self.cuda_available,
            "gpu_name": self.gpu_name,
            "cpu_threads": self.cpu_threads,
            "result_sha256": self.result_sha256,
            "result_size_bytes": self.result_size_bytes,
        }

    def to_bytes(self) -> bytes:
        return json_bytes(self.to_dict())

    @classmethod
    def from_bytes(cls, raw: bytes) -> ModalM0RuntimeEvidence:
        if not isinstance(raw, bytes) or len(raw) > _MAX_FRAME_HEADER_BYTES:
            raise BoundaryError("modal_m0_runtime_evidence", "evidence_size_limit")
        obj = dict(
            object_fields(
                decode_json(raw),
                {
                    "schema",
                    "target_id",
                    "call_id",
                    "request_sha256",
                    "attempt_id",
                    "producer",
                    "image_object_id",
                    "torch_version",
                    "python_version",
                    "cuda_version",
                    "cuda_available",
                    "gpu_name",
                    "cpu_threads",
                    "result_sha256",
                    "result_size_bytes",
                },
                "modal_m0_runtime_evidence",
            )
        )
        if raw != json_bytes(obj):
            raise BoundaryError("modal_m0_runtime_evidence", "noncanonical_evidence")
        return cls.from_dict(obj)

    @classmethod
    def from_dict(cls, value: object) -> ModalM0RuntimeEvidence:
        obj = dict(
            object_fields(
                value,
                {
                    "schema",
                    "target_id",
                    "call_id",
                    "request_sha256",
                    "attempt_id",
                    "producer",
                    "image_object_id",
                    "torch_version",
                    "python_version",
                    "cuda_version",
                    "cuda_available",
                    "gpu_name",
                    "cpu_threads",
                    "result_sha256",
                    "result_size_bytes",
                },
                "modal_m0_runtime_evidence",
            )
        )
        if obj.pop("schema") != "stpd/modal-m0-runtime-evidence-v1":
            raise BoundaryError("modal_m0_runtime_evidence", "unsupported_schema")
        obj["producer"] = Producer.decode(obj["producer"])
        return cls(**obj)


def _decode_request(raw: bytes) -> Any:
    from ..workers.token_remote_update import TokenRemoteUpdateRequest

    return TokenRemoteUpdateRequest.from_bytes(raw)


def _decode_result(raw: bytes) -> Any:
    from ..workers.token_remote_update import TokenRemoteUpdateResult

    return TokenRemoteUpdateResult.from_bytes(raw)


def _request_runtime(request: Any) -> tuple[str, int]:
    try:
        parameters = request.run_manifest.parameters.value()
        torch_version = parameters["torch_version"]
        cpu_threads = parameters["cpu_threads"]
    except (AttributeError, KeyError, TypeError):
        raise BoundaryError("modal_m0", "request_target_runtime_missing") from None
    _runtime_version(torch_version, "torch_version")
    if type(cpu_threads) is not int or not 1 <= cpu_threads <= 256:
        raise BoundaryError("modal_m0", "invalid_request_cpu_threads")
    return torch_version, cpu_threads


def _result_matches_call(result: Any, call: ModalM0Call) -> bool:
    return (
        result.request_sha256 == call.request_sha256
        and result.attempt_id == call.attempt_id
        and result.run_id == call.run_id
        and result.input_id == call.input_id
        and result.operation_id == call.operation_id
        and result.producer == call.producer
        and result.target_device == call.target_device
        and result.target_step == call.target_step
        and result.resume_checkpoint_id == call.resume_checkpoint_id
        and _result_identity_sha256(result) == call.result_identity_sha256
    )


def _value(value: Any) -> Any:
    return value.value() if callable(getattr(value, "value", None)) else value


def _result_identity_sha256(value: Any) -> str:
    """Hash every request-derived result field while excluding the produced checkpoint."""
    from ..workers.token_ranking import config_payload

    body = {
        "request_sha256": value.request_sha256,
        "attempt_id": value.attempt_id,
        "run_id": value.run_id,
        "input_id": value.input_id,
        "producer": value.producer.to_dict(),
        "operation_id": value.operation_id,
        "training_binding": _value(value.training_binding),
        "target_device": value.target_device,
        "target_step": value.target_step,
        "config": config_payload(value.config),
        "resume_checkpoint_id": value.resume_checkpoint_id,
        "resume_checkpoint_sha256": getattr(value, "resume_checkpoint_sha256", None),
        "backbone_identity": _value(value.backbone_identity),
    }
    return hashlib.sha256(json_bytes(body)).hexdigest()


def _request_result_identity_sha256(request: Any) -> str:
    resume_bytes = request.resume_checkpoint
    resume_sha256 = hashlib.sha256(resume_bytes).hexdigest() if resume_bytes is not None else None
    identity = SimpleNamespace(
        request_sha256=request.request_sha256,
        attempt_id=request.attempt_id,
        run_id=request.run_id,
        input_id=request.input_id,
        producer=request.producer,
        operation_id=request.operation_id,
        training_binding=request.training_binding,
        target_device=request.target_device,
        target_step=request.target_step,
        config=request.config,
        resume_checkpoint_id=request.resume_checkpoint_id,
        resume_checkpoint_sha256=resume_sha256,
        backbone_identity=request.backbone_identity,
    )
    return _result_identity_sha256(identity)


def _observe_worker_runtime() -> SimpleNamespace:
    """Measure actual container facts; never infer them from caller or checkpoint data."""
    try:
        import torch

        torch_version = str(torch.__version__)
        cuda_version = str(torch.version.cuda or "")
        cuda_available = bool(torch.cuda.is_available())
        gpu_name = str(torch.cuda.get_device_name(0)) if cuda_available else ""
        cpu_threads = torch.get_num_threads()
    except Exception:
        raise BoundaryError("modal_m0", "target_runtime_unavailable") from None
    if (
        not cuda_version
        or not cuda_available
        or type(cpu_threads) is not int
        or not 1 <= cpu_threads <= 256
    ):
        raise BoundaryError("modal_m0", "target_runtime_mismatch")
    return SimpleNamespace(
        torch_version=torch_version,
        python_version=platform.python_version(),
        cuda_version=cuda_version,
        cuda_available=cuda_available,
        gpu_name=gpu_name,
        cpu_threads=cpu_threads,
    )


def _encode_worker_response(
    request: Any,
    result_bytes: bytes,
    *,
    expected_image_object_id: str,
    runtime: SimpleNamespace,
) -> bytes:
    if not isinstance(result_bytes, bytes) or not result_bytes:
        raise BoundaryError("modal_m0", "result_bytes_required")
    evidence = ModalM0RuntimeEvidence(
        None,
        None,
        request.request_sha256,
        request.attempt_id,
        request.producer,
        expected_image_object_id,
        runtime.torch_version,
        runtime.python_version,
        runtime.cuda_version,
        runtime.cuda_available,
        runtime.gpu_name,
        runtime.cpu_threads,
        hashlib.sha256(result_bytes).hexdigest(),
        len(result_bytes),
    )
    header = evidence.to_bytes()
    if len(header) > _MAX_FRAME_HEADER_BYTES:
        raise BoundaryError("modal_m0", "result_header_size_limit")
    frame = _FRAME_MAGIC + struct.pack(">I", len(header)) + header + result_bytes
    if len(frame) > MAX_M0_RESULT_BYTES:
        raise BoundaryError("modal_m0", "result_size_limit")
    return frame


def _decode_worker_response(
    frame: bytes,
    handle: ModalM0Call,
) -> tuple[bytes, ModalM0RuntimeEvidence]:
    if not isinstance(frame, bytes) or not 1 <= len(frame) <= MAX_M0_RESULT_BYTES:
        raise BoundaryError("modal_m0", "result_size_or_type_mismatch")
    prefix_size = len(_FRAME_MAGIC) + 4
    if len(frame) < prefix_size or not frame.startswith(_FRAME_MAGIC):
        raise BoundaryError("modal_m0", "invalid_result_frame")
    header_size = struct.unpack(">I", frame[len(_FRAME_MAGIC) : prefix_size])[0]
    if header_size < 2 or header_size > _MAX_FRAME_HEADER_BYTES:
        raise BoundaryError("modal_m0", "result_header_size_limit")
    header_end = prefix_size + header_size
    if header_end >= len(frame):
        raise BoundaryError("modal_m0", "truncated_result_frame")
    header_bytes = frame[prefix_size:header_end]
    header = dict(
        object_fields(
            decode_json(header_bytes),
            {
                "schema",
                "target_id",
                "call_id",
                "request_sha256",
                "attempt_id",
                "producer",
                "image_object_id",
                "torch_version",
                "python_version",
                "cuda_version",
                "cuda_available",
                "gpu_name",
                "cpu_threads",
                "result_sha256",
                "result_size_bytes",
            },
            "modal_m0_runtime_evidence",
        )
    )
    if header_bytes != json_bytes(header):
        raise BoundaryError("modal_m0", "noncanonical_result_header")
    remote_evidence = ModalM0RuntimeEvidence.from_dict(header)
    request_sha256 = remote_evidence.request_sha256
    attempt_id = remote_evidence.attempt_id
    producer = remote_evidence.producer
    image_object_id = remote_evidence.image_object_id
    body = frame[header_end:]
    if (
        remote_evidence.target_id is not None
        or remote_evidence.call_id is not None
        or request_sha256 != handle.request_sha256
        or attempt_id != handle.attempt_id
        or producer != handle.producer
        or image_object_id != handle.target.image_object_id
        or len(body) != remote_evidence.result_size_bytes
        or hashlib.sha256(body).hexdigest() != remote_evidence.result_sha256
    ):
        raise BoundaryError("modal_m0", "result_handle_identity_mismatch")
    evidence = replace(
        remote_evidence,
        target_id=handle.target_id,
        call_id=handle.call_id,
    )
    evidence.bind(handle)
    return body, evidence


def execute_m0_request_bytes(
    request_bytes: bytes,
    *,
    expected_producer: Producer,
    expected_image_object_id: str,
) -> bytes:
    """Remote entry: execute once and return a bytes frame over typed result bytes."""
    if not isinstance(request_bytes, bytes) or not 1 <= len(request_bytes) <= MAX_M0_REQUEST_BYTES:
        raise BoundaryError("modal_m0", "request_size_limit")
    if not isinstance(expected_producer, Producer):
        raise BoundaryError("modal_m0", "untyped_deployed_producer")
    if not isinstance(expected_image_object_id, str) or not _MODAL_ID_RE["image"].fullmatch(
        expected_image_object_id
    ):
        raise BoundaryError("modal_m0", "invalid_deployed_image_object_id")

    # Resolve identity from the package actually imported in the image's checkout.
    # The deployment wrapper deliberately excludes local source mounts and invokes
    # this module with the fixed image's venv, so these facts are measured remotely.
    try:
        actual_producer = _runtime_source_identity()
    except Exception:
        raise BoundaryError("modal_m0", "runtime_source_identity_unavailable") from None
    if actual_producer != expected_producer:
        raise BoundaryError("modal_m0", "runtime_source_lock_mismatch")

    request = _decode_request(request_bytes)
    if request.producer != expected_producer or request.target_device != "cuda":
        raise BoundaryError("modal_m0", "deployed_source_or_cuda_mismatch")

    # Confirm the actual container before starting training, so an unexpected
    # provider runtime cannot consume the run and then only fail while reporting it.
    runtime = _observe_worker_runtime()
    if _request_runtime(request) != (runtime.torch_version, runtime.cpu_threads):
        raise BoundaryError("modal_m0", "target_runtime_mismatch")
    from ..workers.token_remote_update import execute_token_remote_update

    result = execute_token_remote_update(request)
    if not _result_matches_request(result, request):
        raise BoundaryError("modal_m0", "worker_result_request_mismatch")
    result_bytes = result.to_bytes()
    if not isinstance(result_bytes, bytes) or len(result_bytes) > MAX_M0_RESULT_BYTES:
        raise BoundaryError("modal_m0", "result_size_limit")
    return _encode_worker_response(
        request,
        result_bytes,
        expected_image_object_id=expected_image_object_id,
        runtime=runtime,
    )


def _result_matches_request(result: Any, request: Any) -> bool:
    return (
        result.request_sha256 == request.request_sha256
        and result.attempt_id == request.attempt_id
        and result.run_id == request.run_id
        and result.input_id == request.input_id
        and result.operation_id == request.operation_id
        and result.producer == request.producer
        and result.target_device == request.target_device
        and result.target_step == request.target_step
        and result.config == request.config
        and result.resume_checkpoint_id == request.resume_checkpoint_id
        and _result_identity_sha256(result) == _request_result_identity_sha256(request)
    )


def _runtime_source_identity() -> Producer:
    """Return source identity only when imported from the pinned image checkout."""
    root = Path(__file__).resolve().parents[2]
    if root != Path("/opt/stpd/python").resolve():
        raise BoundaryError("modal_m0", "fixed_image_checkout_required")
    return source_identity(root)


def _worker_main() -> int:
    """Read a single request from stdin and emit only the bounded response frame."""
    try:
        image_id = os.environ.get("STPD_M0_MODAL_IMAGE_ID", "")
        attempt_id = os.environ.get("STPD_M0_MODAL_ATTEMPT_ID", "")
        request_sha256 = os.environ.get("STPD_M0_MODAL_REQUEST_SHA256", "")
        revision = os.environ.get("STPD_M0_SOURCE_REVISION", "")
        lock_sha256 = os.environ.get("STPD_M0_UV_LOCK_SHA256", "")
        torch_version = os.environ.get("STPD_M0_TORCH_VERSION", "")
        cpu_threads = int(os.environ.get("STPD_M0_CPU_THREADS", "0"))
        plan_sha256 = os.environ.get("STPD_M0_MODAL_PLAN_SHA256", "")
        digest(attempt_id, "modal_m0.attempt_id", length=32)
        digest(request_sha256, "modal_m0.request_sha256")
        digest(revision, "modal_m0.source_revision", length=40)
        digest(lock_sha256, "modal_m0.uv_lock_sha256")
        if (
            plan_sha256 != M0_PILOT_EXECUTION_PLAN.plan_sha256
            or not 1 <= cpu_threads <= 256
        ):
            raise BoundaryError("modal_m0", "resource_plan_mismatch")
        request_bytes = sys.stdin.buffer.read(MAX_M0_REQUEST_BYTES + 1)
        request = _decode_request(request_bytes)
        if (
            hashlib.sha256(request_bytes).hexdigest() != request_sha256
            or request.attempt_id != attempt_id
            or _request_runtime(request) != (torch_version, cpu_threads)
        ):
            raise BoundaryError("modal_m0", "app_ref_request_identity_mismatch")
        response = execute_m0_request_bytes(
            request_bytes,
            expected_producer=Producer(REPOSITORY, revision, lock_sha256),
            expected_image_object_id=image_id,
        )
        sys.stdout.buffer.write(response)
        sys.stdout.buffer.flush()
    except Exception:
        # Avoid forwarding SDK, path, credential, or request details to Modal logs.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main())


class ModalM0Provider:
    """Prepare, reconcile, submit, and stop one immutable per-attempt Modal App."""

    def __init__(
        self,
        target: ModalM0Target | None = None,
        target_runtime: ModalM0TargetRuntimeMetadata | None = None,
        *,
        spec: M0AttemptSpec | None = None,
        sdk: Any = None,
        command_runner: Any = None,
    ) -> None:
        if target is not None:
            if not isinstance(target, ModalM0Target):
                raise BoundaryError("modal_m0", "typed_target_required")
            if not isinstance(target_runtime, ModalM0TargetRuntimeMetadata):
                raise BoundaryError("modal_m0", "typed_target_runtime_required")
            target_runtime.bind(target)
        elif target_runtime is not None:
            raise BoundaryError("modal_m0", "target_required_for_runtime_metadata")
        if spec is not None and not isinstance(spec, M0AttemptSpec):
            raise BoundaryError("modal_m0", "typed_attempt_spec_required")
        if target is not None and spec is not None and not _target_matches_spec(target, spec):
            raise BoundaryError("modal_m0", "target_attempt_spec_mismatch")
        self.target = target
        self.target_runtime = target_runtime
        self.spec = spec
        self._sdk = sdk
        self._command_runner = command_runner
        self._runtime_evidence: dict[str, ModalM0RuntimeEvidence] = {}
        self._prepare_started = False

    def token_target_runtime(self) -> Any:
        """Return the value to pass to ``prepare_token_run(target_runtime=...)``."""
        if self.target is None or self.target_runtime is None:
            raise BoundaryError("modal_m0", "app_not_prepared")
        return self.target_runtime.token_target_runtime(self.target)

    def _client(self) -> Any:
        if self._sdk is None:
            try:
                if importlib.metadata.version("modal") != MODAL_SDK_VERSION:
                    raise BoundaryError("modal_m0", "sdk_version_mismatch")
                self._sdk = importlib.import_module("modal")
            except importlib.metadata.PackageNotFoundError as error:
                raise BoundaryError("modal_m0", "install_locked_cloud_extra") from error
            except ImportError as error:
                raise BoundaryError("modal_m0", "install_locked_cloud_extra") from error
        return self._sdk

    @staticmethod
    def _smoke_helpers() -> dict[str, Any]:
        smoke = (
            Path(__file__).resolve().parents[2]
            / "deploy"
            / "cloud-worker"
            / "m0_synthetic_gpu_smoke.py"
        )
        try:
            return runpy.run_path(str(smoke), run_name="_stpd_m0_lifecycle_helpers")
        except Exception:
            raise BoundaryError("modal_m0", "lifecycle_helper_unavailable") from None

    def _run(self, args: list[str], *, timeout_seconds: float, text: bool = True) -> Any:
        cli = str(Path(sys.executable).with_name("modal"))
        runner = self._command_runner or subprocess.run
        try:
            return runner(
                [cli, *args], capture_output=True, text=text, check=False,
                timeout=timeout_seconds,
            )
        except Exception:
            raise BoundaryError("modal_m0", "modal_command_unknown") from None

    def _current_workspace(self) -> tuple[str, str]:
        """Return the CLI's live display name and canonical workspace ID."""
        completed = self._run(["token", "info"], timeout_seconds=MAX_MODAL_CONTROL_SECONDS)
        if completed.returncode != 0 or not isinstance(completed.stdout, str):
            raise BoundaryError("modal_m0", "modal_account_unavailable")
        matches = re.findall(
            r"^Workspace: (.+?)\s+\(([A-Za-z0-9][A-Za-z0-9._-]{0,127})\)\s*$",
            completed.stdout,
            re.M,
        )
        if len(matches) != 1 or not matches[0][0].strip():
            raise BoundaryError("modal_m0", "modal_account_identity_unavailable")
        workspace_name, canonical_id = matches[0]
        return workspace_name.strip(), canonical_id

    def _current_account_id(self) -> str:
        return self._current_workspace()[1]

    def verify_account_id(
        self, account_id: str, *, allow_legacy_workspace_alias: bool = False,
    ) -> str:
        """Verify a pinned ID, optionally accepting its live-bound historical slug.

        New operations call this in strict mode before writing their journal. Recovery
        may opt into the alias only when the provider's own token-info response binds
        that exact old value to the current canonical workspace ID.
        """
        workspace_name, canonical_id = self._current_workspace()
        if account_id == canonical_id:
            return canonical_id
        is_canonical_account_id = account_id.startswith("ac-")
        if (allow_legacy_workspace_alias and not is_canonical_account_id
                and account_id == workspace_name):
            return canonical_id
        raise BoundaryError("modal_m0", "modal_account_mismatch")

    @staticmethod
    def _complete_app_rows(rows: Any) -> list[dict[str, Any]]:
        if not isinstance(rows, list):
            raise BoundaryError("modal_m0", "modal_app_list_invalid")
        complete: list[dict[str, Any]] = []
        for row in rows:
            if (not isinstance(row, dict)
                    or not isinstance(row.get("description"), str)
                    or not isinstance(row.get("app_id"), str)
                    or not _MODAL_ID_RE["app"].fullmatch(row["app_id"])):
                raise BoundaryError("modal_m0", "modal_app_list_incomplete")
            complete.append(row)
        return complete

    @staticmethod
    def _complete_history_rows(rows: Any) -> list[tuple[int, dict[str, Any]]]:
        if not isinstance(rows, list):
            raise BoundaryError("modal_m0", "modal_app_history_invalid")
        parsed: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise BoundaryError("modal_m0", "modal_app_history_incomplete")
            match = re.fullmatch(r"v([1-9][0-9]*)", str(row.get("version", "")))
            if match is None or not isinstance(row.get("tag"), str):
                raise BoundaryError("modal_m0", "modal_app_history_incomplete")
            parsed.append((int(match.group(1)), row))
        return parsed

    def _confirm_named_app_absent(
        self, spec: M0AttemptSpec, *, allow_legacy_workspace_alias: bool,
    ) -> None:
        """Use Modal's typed, exact deployment-name lookup to prove absence."""
        self.verify_account_id(
            spec.account_id,
            allow_legacy_workspace_alias=allow_legacy_workspace_alias,
        )
        sdk = self._client()
        try:
            api_pb2 = importlib.import_module("modal_proto.api_pb2")
            async_utils = importlib.import_module("modal._utils.async_utils")
            client = sdk.Client.from_env()
            stub = client.stub

            async def lookup_app_by_deployment_name() -> Any:
                return await stub.AppGetByDeploymentName(
                    api_pb2.AppGetByDeploymentNameRequest(
                        name=spec.app_name,
                        environment_name=spec.environment_name,
                    ),
                    retry=None,
                    timeout=MAX_MODAL_CONTROL_SECONDS,
                )

            response = async_utils.synchronizer.create_blocking(
                lookup_app_by_deployment_name,
            )()
        except Exception as error:
            # Only the SDK's typed NOT_FOUND for this exact request proves absence.
            # Permission, transport, timeout, and every other RPC failure stay unknown.
            not_found_error = getattr(sdk.exception, "NotFoundError", None)
            if isinstance(not_found_error, type) and isinstance(error, not_found_error):
                return
            raise BoundaryError("modal_m0", "modal_app_name_lookup_unknown") from None
        if (not isinstance(response, api_pb2.AppGetByDeploymentNameResponse)
                or response.environment_name != spec.environment_name):
            raise BoundaryError("modal_m0", "modal_app_name_lookup_unknown")
        if (not isinstance(response.app_id, str)
                or not isinstance(response.previous_app_id, str)):
            raise BoundaryError("modal_m0", "modal_app_name_lookup_unknown")
        for app_id in (response.app_id, response.previous_app_id):
            if app_id and not _MODAL_ID_RE["app"].fullmatch(app_id):
                raise BoundaryError("modal_m0", "modal_app_name_lookup_unknown")
        # Match Modal 1.5.5's modal.cli.app.resolve_app_identifier: a name lookup
        # with neither current nor previous App ID raises its typed NotFoundError.
        if not response.app_id and not response.previous_app_id:
            return
        # A current or recently stopped App is not absence when the complete CLI list
        # omitted it; the provider views disagree, so keep the operation unknown.
        raise BoundaryError("modal_m0", "modal_app_lookup_incomplete")

    def _modal_json(self, args: list[str], *, timeout_seconds: float) -> Any:
        if self._command_runner is None:
            helper = self._smoke_helpers()["_modal_cli_json"]
            try:
                return helper(args, timeout_seconds=timeout_seconds)
            except Exception:
                raise BoundaryError("modal_m0", "modal_lifecycle_query_unknown") from None
        completed = self._run(args, timeout_seconds=timeout_seconds)
        if completed.returncode != 0 or not isinstance(completed.stdout, str):
            raise BoundaryError("modal_m0", "modal_lifecycle_query_unknown")
        try:
            return json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError):
            raise BoundaryError("modal_m0", "modal_lifecycle_query_invalid_json") from None

    @staticmethod
    def _spec_sha256(spec: M0AttemptSpec) -> str:
        return hashlib.sha256(spec.to_bytes()).hexdigest()

    @staticmethod
    def _deployment_tag(spec: M0AttemptSpec) -> str:
        # Modal 1.5.5 limits deployment tags to 50 characters. Keep all 256 bits
        # rather than truncating the durable spec hash to fit the provider label.
        encoded = base64.urlsafe_b64encode(hashlib.sha256(spec.to_bytes()).digest())
        return "sha256-" + encoded.decode("ascii").rstrip("=")

    def _bind_target(self, target: ModalM0Target) -> ModalM0Target:
        runtime = ModalM0TargetRuntimeMetadata(
            target.target_id,
            target.producer,
            target.image_object_id,
            target.target_runtime.torch_version,
            target.target_runtime.cpu_threads,
        )
        if self.target is not None and self.target != target:
            raise BoundaryError("modal_m0", "foreign_target")
        self.target = target
        self.target_runtime = runtime
        return target

    @staticmethod
    def _spec_for_target(target: ModalM0Target) -> M0AttemptSpec:
        return M0AttemptSpec(
            target.attempt_id, target.request_sha256, target.account_id,
            target.environment_name, target.image_object_id, target.producer,
            target.target_runtime, target.resource_plan,
        )

    def _function_identity(self, client: Any, spec: M0AttemptSpec, app_id: str) -> str:
        """Read authoritative protobuf identity/resources, never sync facade attributes."""
        try:
            api = importlib.import_module("modal_proto.api_pb2")
            async_utils = importlib.import_module("modal._utils.async_utils")

            async def read() -> tuple[Any, Any]:
                layout = await client.stub.AppGetLayout(
                    api.AppGetLayoutRequest(app_id=app_id),
                    retry=None, timeout=MAX_MODAL_CONTROL_SECONDS,
                )
                function = await client.stub.FunctionGet(
                    api.FunctionGetRequest(
                        app_name=spec.app_name, object_tag=M0_MODAL_FUNCTION_NAME,
                        environment_name=spec.environment_name, app_version=0,
                    ),
                    retry=None, timeout=MAX_MODAL_CONTROL_SECONDS,
                )
                return layout, function

            layout, response = async_utils.synchronizer.create_blocking(read)()
        except Exception:
            raise BoundaryError("modal_m0", "deployed_target_identity_unavailable") from None
        if (not isinstance(layout, api.AppGetLayoutResponse)
                or not isinstance(response, api.FunctionGetResponse)):
            raise BoundaryError("modal_m0", "deployed_target_identity_unavailable")
        function_id = response.function_id
        if not isinstance(function_id, str):
            raise BoundaryError("modal_m0", "deployed_target_identity_mismatch")
        objects = [obj for obj in layout.app_layout.objects if obj.object_id == function_id]
        metadata = response.handle_metadata
        if (
            not _MODAL_ID_RE["function"].fullmatch(function_id)
            or dict(layout.app_layout.function_ids) != {M0_MODAL_FUNCTION_NAME: function_id}
            or len(objects) != 1
            or not objects[0].HasField("function_handle_metadata")
            or metadata.app_id != app_id
            or metadata.function_name != M0_MODAL_FUNCTION_NAME
            or metadata.is_method
            or metadata.function_type != api.Function.FUNCTION_TYPE_FUNCTION
            or objects[0].function_handle_metadata.app_id != app_id
            or objects[0].function_handle_metadata.function_name != M0_MODAL_FUNCTION_NAME
            or response.function.function_name != M0_MODAL_FUNCTION_NAME
        ):
            raise BoundaryError("modal_m0", "deployed_target_identity_mismatch")
        ranked = response.function.ranked_functions
        if len(ranked) != 1 or ranked[0].rank != 0:
            raise BoundaryError("modal_m0", "deployed_target_resource_mismatch")
        definition = ranked[0].function
        if definition.image_id != spec.image_object_id:
            raise BoundaryError("modal_m0", "deployed_target_identity_mismatch")
        plan = spec.resource_plan
        resources = definition.resources
        scaling = response.function.autoscaler_settings
        # SDK 1.5.5 leaves FunctionData's legacy startup field at zero;
        # the actual rank-0 Function definition carries the configured timeout.
        if (
            definition.function_name != M0_MODAL_FUNCTION_NAME
            or resources.gpu_config.gpu_type != plan.gpu
            or resources.gpu_config.count != 1
            or resources.milli_cpu != int(plan.cpu * 1000)
            or resources.memory_mb != plan.memory_mib
            or response.function.timeout_secs != plan.function_timeout_seconds
            or definition.startup_timeout_secs != plan.startup_timeout_seconds
            or scaling.scaledown_window != plan.scaledown_seconds
            or scaling.max_containers != plan.max_containers
            or scaling.min_containers != 0
            or scaling.buffer_containers != 0
            or definition.retry_policy.retries != plan.retries
        ):
            raise BoundaryError("modal_m0", "deployed_target_resource_mismatch")
        return function_id

    def _bound_function(
        self, spec: M0AttemptSpec, app_id: str, *, allow_legacy_workspace_alias: bool,
    ) -> Any:
        # This is a single-owner, one-deployment attempt App, not server-side
        # atomic version pinning. A second deployment is rejected, never followed.
        try:
            client = self._client().Client.from_env()
            before = self._function_identity(client, spec, app_id)
            function = self._client().Function.from_name(
                spec.app_name, M0_MODAL_FUNCTION_NAME,
                environment_name=spec.environment_name, client=client,
            )
            function.hydrate()
            after = self._function_identity(client, spec, app_id)
        except BoundaryError:
            raise
        except Exception:
            raise BoundaryError("modal_m0", "deployed_target_identity_unavailable") from None
        if function.object_id != before or after != before:
            raise BoundaryError("modal_m0", "deployed_target_identity_mismatch")
        if self._deployment_identity(
            spec, allow_legacy_workspace_alias=allow_legacy_workspace_alias,
        ) != app_id:
            raise BoundaryError("modal_m0", "deployed_target_identity_mismatch")
        return function

    def _function_for(self, target: ModalM0Target) -> Any:
        if target.app_version != 1:
            raise BoundaryError("modal_m0", "attempt_app_redeployed")
        spec = self._spec_for_target(target)
        if self._deployment_identity(spec, allow_legacy_workspace_alias=True) != target.app_id:
            raise BoundaryError("modal_m0", "deployed_target_identity_mismatch")
        function = self._bound_function(spec, target.app_id, allow_legacy_workspace_alias=True)
        if function.object_id != target.function_id:
            raise BoundaryError("modal_m0", "deployed_target_identity_mismatch")
        return function

    def _active_target(self, app_ref: ModalM0Target | None = None) -> ModalM0Target:
        target = app_ref if app_ref is not None else self.target
        if not isinstance(target, ModalM0Target):
            raise BoundaryError("modal_m0", "typed_app_ref_required")
        if self.spec is not None and not _target_matches_spec(target, self.spec):
            raise BoundaryError("modal_m0", "foreign_target")
        if self.target is not None and target != self.target:
            raise BoundaryError("modal_m0", "foreign_target")
        return target

    def _deployment_identity(
        self, spec: M0AttemptSpec, *, allow_legacy_workspace_alias: bool = False,
    ) -> str | None:
        """Read-only reconciliation by the already journaled unique attempt name."""
        if not isinstance(spec, M0AttemptSpec):
            raise BoundaryError("modal_m0", "typed_attempt_spec_required")
        if self.spec is not None and self.spec != spec:
            raise BoundaryError("modal_m0", "foreign_attempt_spec")
        self.verify_account_id(
            spec.account_id,
            allow_legacy_workspace_alias=allow_legacy_workspace_alias,
        )
        app_rows = self._modal_json(
            ["app", "list", "--env", spec.environment_name],
            timeout_seconds=MAX_MODAL_CONTROL_SECONDS,
        )
        app_rows = self._complete_app_rows(app_rows)
        matches = [
            row for row in app_rows
            if isinstance(row, dict) and row.get("description") == spec.app_name
        ]
        if not matches:
            self._confirm_named_app_absent(
                spec, allow_legacy_workspace_alias=allow_legacy_workspace_alias,
            )
            return None
        if len(matches) != 1:
            raise BoundaryError("modal_m0", "modal_app_name_ambiguous")
        app_id = matches[0].get("app_id")
        if not isinstance(app_id, str) or not _MODAL_ID_RE["app"].fullmatch(app_id):
            raise BoundaryError("modal_m0", "modal_app_id_unavailable")
        self.verify_account_id(
            spec.account_id,
            allow_legacy_workspace_alias=allow_legacy_workspace_alias,
        )
        histories = self._modal_json(
            ["app", "history", app_id, "--env", spec.environment_name],
            timeout_seconds=MAX_MODAL_CONTROL_SECONDS,
        )
        if not isinstance(histories, list) or not histories:
            raise BoundaryError("modal_m0", "modal_app_history_unavailable")
        parsed = self._complete_history_rows(histories)
        if not parsed:
            raise BoundaryError("modal_m0", "modal_app_version_unavailable")
        if len(parsed) != 1 or parsed[0][0] != 1:
            raise BoundaryError("modal_m0", "attempt_app_redeployed")
        _, latest = parsed[0]
        if latest.get("tag") != self._deployment_tag(spec):
            raise BoundaryError("modal_m0", "existing_app_spec_mismatch")
        self.verify_account_id(
            spec.account_id,
            allow_legacy_workspace_alias=allow_legacy_workspace_alias,
        )
        return app_id

    def resolve_app(
        self, spec: M0AttemptSpec, *, allow_legacy_workspace_alias: bool = False,
    ) -> ModalM0Target | None:
        """Recover only the unique, once-deployed App for the journaled attempt."""
        app_id = self._deployment_identity(
            spec, allow_legacy_workspace_alias=allow_legacy_workspace_alias,
        )
        if app_id is None:
            return None
        function = self._bound_function(
            spec, app_id, allow_legacy_workspace_alias=allow_legacy_workspace_alias,
        )
        target = ModalM0Target(
            spec.account_id,
            spec.environment_name,
            app_id,
            1,
            function.object_id,
            spec.image_object_id,
            spec.producer,
            spec.attempt_id,
            spec.request_sha256,
            spec.target_runtime,
            spec.resource_plan,
        )
        self.spec = spec
        self._bind_target(target)
        return target

    def inspect_prepare_stopped(
        self, spec: M0AttemptSpec, *, allow_legacy_workspace_alias: bool = False,
    ) -> M0PrepareStoppedProof:
        """Read a once-deployed stopped App without fabricating a runnable Target."""
        app_id = self._deployment_identity(
            spec, allow_legacy_workspace_alias=allow_legacy_workspace_alias,
        )
        if app_id is None:
            raise BoundaryError("modal_m0", "prepare_stop_unproven")
        canonical = self.verify_account_id(
            spec.account_id, allow_legacy_workspace_alias=allow_legacy_workspace_alias,
        )
        try:
            api = importlib.import_module("modal_proto.api_pb2")
            async_utils = importlib.import_module("modal._utils.async_utils")
            client = self._client().Client.from_env()

            async def read() -> tuple[Any, Any]:
                named = await client.stub.AppGetByDeploymentName(
                    api.AppGetByDeploymentNameRequest(
                        name=spec.app_name, environment_name=spec.environment_name,
                    ),
                    retry=None, timeout=MAX_MODAL_CONTROL_SECONDS,
                )
                layout = await client.stub.AppGetLayout(
                    api.AppGetLayoutRequest(app_id=app_id),
                    retry=None, timeout=MAX_MODAL_CONTROL_SECONDS,
                )
                return named, layout

            named, layout = async_utils.synchronizer.create_blocking(read)()
        except Exception:
            raise BoundaryError("modal_m0", "prepare_stop_unproven") from None
        if (not isinstance(named, api.AppGetByDeploymentNameResponse)
                or named.environment_name != spec.environment_name
                or not any(value == app_id for value in (named.app_id, named.previous_app_id))
                or any(value and value != app_id for value in (named.app_id, named.previous_app_id))
                or not isinstance(layout, api.AppGetLayoutResponse)):
            raise BoundaryError("modal_m0", "prepare_stop_binding_invalid")
        functions = dict(layout.app_layout.function_ids)
        function_id = functions.get(M0_MODAL_FUNCTION_NAME, "")
        objects = [obj for obj in layout.app_layout.objects if obj.object_id == function_id]
        if (
            not _MODAL_ID_RE["function"].fullmatch(function_id)
            or functions != {M0_MODAL_FUNCTION_NAME: function_id}
            or len(objects) != 1
            or not objects[0].HasField("function_handle_metadata")
            or objects[0].function_handle_metadata.app_id != app_id
            or objects[0].function_handle_metadata.function_name != M0_MODAL_FUNCTION_NAME
            or objects[0].function_handle_metadata.is_method
            or objects[0].function_handle_metadata.function_type !=
            api.Function.FUNCTION_TYPE_FUNCTION
        ):
            raise BoundaryError("modal_m0", "prepare_stop_binding_invalid")
        try:
            evidence = self._smoke_helpers()["_stop_evidence"](
                app_id, spec.environment_name,
                deadline=time.monotonic() + MAX_MODAL_CONTROL_SECONDS,
            )
            inspection = M0StopInspection(
                app_id, evidence["app_state"], evidence["app_tasks"],
                evidence["running_containers"], True,
            )
        except Exception:
            raise BoundaryError("modal_m0", "prepare_stop_unproven") from None
        if (self._deployment_identity(
                spec, allow_legacy_workspace_alias=allow_legacy_workspace_alias,
            ) != app_id
                or self.verify_account_id(
                    spec.account_id, allow_legacy_workspace_alias=allow_legacy_workspace_alias,
                ) != canonical):
            raise BoundaryError("modal_m0", "prepare_stop_binding_invalid")
        return M0PrepareStoppedProof(spec, canonical, app_id, function_id, inspection)

    def prepare_app(self, spec: M0AttemptSpec) -> ModalM0Target:
        """Deploy a unique App once; an ambiguous outcome is reconciled, never redeployed."""
        if not isinstance(spec, M0AttemptSpec):
            raise BoundaryError("modal_m0", "typed_attempt_spec_required")
        if self.spec is not None and self.spec != spec:
            raise BoundaryError("modal_m0", "foreign_attempt_spec")
        existing = self.resolve_app(spec)
        if existing is not None:
            return existing
        if self._prepare_started:
            raise BoundaryError(
                "modal_m0", "prepare_unknown",
                "reconcile the same journaled app name; do not prepare again",
            )
        self._prepare_started = True
        entry = (
            Path(__file__).resolve().parents[2]
            / "deploy"
            / "cloud-worker"
            / "m0_update_modal.py"
        )
        env = dict(os.environ)
        env.update({
            "STPD_M0_MODAL_APP_NAME": spec.app_name,
            "STPD_M0_MODAL_ATTEMPT_ID": spec.attempt_id,
            "STPD_M0_MODAL_REQUEST_SHA256": spec.request_sha256,
            "STPD_M0_MODAL_SPEC_SHA256": self._spec_sha256(spec),
            "STPD_M0_MODAL_PLAN_SHA256": spec.resource_plan.plan_sha256,
            "STPD_M0_MODAL_IMAGE_ID": spec.image_object_id,
            "STPD_M0_SOURCE_REVISION": spec.producer.source_revision,
            "STPD_M0_UV_LOCK_SHA256": spec.producer.uv_lock_sha256,
            "STPD_M0_TORCH_VERSION": spec.target_runtime.torch_version,
            "STPD_M0_CPU_THREADS": str(spec.target_runtime.cpu_threads),
        })
        cli = str(Path(sys.executable).with_name("modal"))
        runner = self._command_runner or subprocess.run
        try:
            completed = runner(
                [cli, "deploy", str(entry), "--env", spec.environment_name,
                 "--tag", self._deployment_tag(spec)],
                cwd=str(Path(__file__).resolve().parents[2]),
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=MAX_MODAL_COMMAND_SECONDS,
            )
        except Exception:
            completed = None
        if completed is not None and completed.returncode == 0:
            self.spec = spec
        # A nonzero exit or local timeout may occur after Modal accepted a deploy.
        # Resolve only by this exact persisted name and deployment tag; never resubmit.
        try:
            resolved = self.resolve_app(spec)
        except BoundaryError as error:
            if completed is None or completed.returncode != 0:
                raise BoundaryError(
                    "modal_m0", "prepare_unknown",
                    "reconcile the same journaled app name; do not prepare again",
                ) from error
            raise
        if resolved is None:
            raise BoundaryError(
                "modal_m0", "prepare_unknown",
                "reconcile the same journaled app name; do not prepare again",
            )
        return resolved

    def restore_handle(
        self, raw: bytes, app_ref: ModalM0Target | None = None,
    ) -> ModalM0Call:
        handle = ModalM0Call.from_bytes(raw)
        target = self._active_target(app_ref or handle.target)
        if handle.target != target:
            raise BoundaryError("modal_m0", "foreign_target")
        return handle

    def get_runtime_evidence(self, handle: ModalM0Call) -> ModalM0RuntimeEvidence:
        """Read verified evidence cached by poll; persist its bytes beside the handle."""
        target = self._active_target(handle.target if isinstance(handle, ModalM0Call) else None)
        if not isinstance(handle, ModalM0Call) or handle.target != target:
            raise BoundaryError("modal_m0", "foreign_target")
        evidence = self._runtime_evidence.get(handle.call_id)
        if evidence is None:
            raise BoundaryError("modal_m0", "runtime_evidence_unavailable")
        evidence.bind(handle)
        return evidence

    def submit(
        self, request_bytes: bytes, app_ref: ModalM0Target | None = None,
    ) -> ModalM0Call:
        """Submit once. An error from ``spawn`` becomes unknown; callers must not repeat it."""
        target = self._active_target(app_ref)
        target_runtime = self.target_runtime
        if target_runtime is None:
            target_runtime = ModalM0TargetRuntimeMetadata(
                target.target_id, target.producer, target.image_object_id,
                target.target_runtime.torch_version, target.target_runtime.cpu_threads,
            )
            self._bind_target(target)
        if (
            not isinstance(request_bytes, bytes)
            or not 1 <= len(request_bytes) <= MAX_M0_REQUEST_BYTES
        ):
            raise BoundaryError("modal_m0", "request_size_limit")
        request = _decode_request(request_bytes)
        if request.to_bytes() != request_bytes:
            raise BoundaryError("modal_m0", "noncanonical_request")
        if (
            request.attempt_id != target.attempt_id
            or request.request_sha256 != target.request_sha256
        ):
            raise BoundaryError("modal_m0", "app_ref_request_identity_mismatch")
        if request.producer != target.producer or request.target_device != "cuda":
            raise BoundaryError("modal_m0", "request_source_or_cuda_mismatch")
        if _request_runtime(request) != (
            target_runtime.torch_version,
            target_runtime.cpu_threads,
        ):
            raise BoundaryError("modal_m0", "request_target_runtime_mismatch")

        function = self._function_for(target)
        try:
            remote_call = function.spawn(request_bytes)
            call_id = remote_call.object_id
            if not isinstance(call_id, str) or not _MODAL_ID_RE["call"].fullmatch(call_id):
                raise ValueError("invalid Modal function call ID")
        except Exception:
            raise BoundaryError(
                "modal_m0",
                "submission_unknown",
                "reconcile this persisted attempt; do not submit it again",
            ) from None
        return ModalM0Call(
            target,
            target_runtime,
            call_id,
            request.request_sha256,
            len(request_bytes),
            request.attempt_id,
            request.run_id,
            request.input_id,
            request.operation_id,
            request.producer,
            _request_result_identity_sha256(request),
            request.target_device,
            request.target_step,
            request.resume_checkpoint_id,
        )

    def poll(
        self,
        handle: ModalM0Call,
        *,
        timeout_seconds: float = 0.0,
    ) -> bytes | None:
        """Poll the saved call. Timeout returns None; success returns typed result bytes."""
        if not isinstance(handle, ModalM0Call):
            raise BoundaryError("modal_m0", "foreign_target")
        target = self._active_target(handle.target)
        if (
            handle.target != target
            or handle.target_runtime.target_id != target.target_id
        ):
            raise BoundaryError("modal_m0", "foreign_target")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 <= timeout_seconds <= MAX_POLL_TIMEOUT_SECONDS
        ):
            raise BoundaryError("modal_m0", "poll_timeout_limit")

        sdk = self._client()
        modal_exceptions = getattr(sdk, "exception", None)
        timeout_type = getattr(modal_exceptions, "TimeoutError", TimeoutError)
        timeout_types = tuple({TimeoutError, timeout_type})
        # Match the smoke call-status ordering: terminal subclasses must be handled
        # before Modal's base TimeoutError, which also represents a pending wait.
        terminal_types = tuple(
            error_type
            for name in ("FunctionTimeoutError", "OutputExpiredError", "RemoteError")
            if isinstance(error_type := getattr(modal_exceptions, name, None), type)
            and issubclass(error_type, BaseException)
        )
        cancellation_type = getattr(
            modal_exceptions, "InputCancellation", None,
        )
        try:
            call = self._saved_call(handle, target)
            result_bytes = call.get(timeout=float(timeout_seconds), index=0)
        except terminal_types:
            raise BoundaryError("modal_m0", "call_terminal_failure") from None
        except timeout_types:
            return None
        except BoundaryError:
            raise
        except BaseException as error:
            if cancellation_type is not None and isinstance(error, cancellation_type):
                raise BoundaryError("modal_m0", "call_cancelled") from None
            if not isinstance(error, Exception):
                raise
            raise BoundaryError("modal_m0", "result_unavailable") from None

        result_bytes, runtime_evidence = _decode_worker_response(result_bytes, handle)
        result = _decode_result(result_bytes)
        if not _result_matches_call(result, handle):
            raise BoundaryError("modal_m0", "result_handle_identity_mismatch")
        self._runtime_evidence[handle.call_id] = runtime_evidence
        return result_bytes

    def _saved_call(self, handle: ModalM0Call, target: ModalM0Target) -> Any:
        # A saved call remains recoverable after its App is stopped. Do not look
        # up the deployed function or spawn anything on this recovery path.
        self.verify_account_id(target.account_id, allow_legacy_workspace_alias=True)
        try:
            api = importlib.import_module("modal_proto.api_pb2")
            async_utils = importlib.import_module("modal._utils.async_utils")
            client = self._client().Client.from_env()

            async def read() -> Any:
                return await client.stub.FunctionCallFromId(
                    api.FunctionCallFromIdRequest(function_call_id=handle.call_id),
                    retry=None, timeout=MAX_MODAL_CONTROL_SECONDS,
                )

            response = async_utils.synchronizer.create_blocking(read)()
            if not isinstance(response, api.FunctionCallFromIdResponse):
                raise BoundaryError("modal_m0", "saved_call_unavailable")
            if (
                response.function_call_id != handle.call_id
                or response.num_inputs != 1
                or response.metadata.app_id != target.app_id
                or response.metadata.function_id != target.function_id
            ):
                raise BoundaryError("modal_m0", "saved_call_identity_mismatch")
            return self._client().FunctionCall.from_id(handle.call_id, client=client)
        except BoundaryError:
            raise
        except Exception:
            raise BoundaryError("modal_m0", "saved_call_unavailable") from None

    def cancel(self, handle: ModalM0Call) -> M0CancelAck:
        """Request cancellation of this saved call; inspect the App separately for stop."""
        if not isinstance(handle, ModalM0Call):
            raise BoundaryError("modal_m0", "typed_call_handle_required")
        target = self._active_target(handle.target)
        if handle.target != target:
            raise BoundaryError("modal_m0", "foreign_target")
        try:
            call = self._saved_call(handle, target)
            call.cancel(terminate_containers=True)
        except Exception:
            return M0CancelAck(target.app_id, handle.call_id, False, "unknown")
        return M0CancelAck(target.app_id, handle.call_id, True, "acknowledged")

    def stop_app(
        self, app_ref: ModalM0Target, *, allow_legacy_workspace_alias: bool = False,
    ) -> M0StopAck:
        """Ask Modal to stop this exact per-attempt App; acknowledgement is not proof."""
        target = self._active_target(app_ref)
        try:
            self.verify_account_id(
                target.account_id,
                allow_legacy_workspace_alias=allow_legacy_workspace_alias,
            )
            completed = self._run(
                ["app", "stop", target.app_id, "--yes", "--env", target.environment_name],
                timeout_seconds=MAX_MODAL_CONTROL_SECONDS,
            )
        except BoundaryError:
            return M0StopAck(target.app_id, False, "unknown")
        if completed.returncode != 0:
            return M0StopAck(target.app_id, False, "unknown")
        return M0StopAck(target.app_id, True, "acknowledged")

    def inspect_stop(
        self, app_ref: ModalM0Target, *, allow_legacy_workspace_alias: bool = False,
    ) -> M0StopInspection:
        """Confirm provider App state, zero tasks, and no containers using smoke tooling."""
        target = self._active_target(app_ref)
        unknown = M0StopInspection(target.app_id, "unknown", None, None, False)
        try:
            self.verify_account_id(
                target.account_id,
                allow_legacy_workspace_alias=allow_legacy_workspace_alias,
            )
            rows = self._modal_json(
                ["app", "list", "--env", target.environment_name],
                timeout_seconds=MAX_MODAL_CONTROL_SECONDS,
            )
        except BoundaryError:
            return unknown
        try:
            rows = self._complete_app_rows(rows)
        except BoundaryError:
            return unknown
        matching = [
            row for row in rows
            if isinstance(row, dict) and row.get("app_id") == target.app_id
        ]
        if len(matching) != 1:
            return unknown
        row = matching[0]
        raw_tasks = row.get("tasks")
        tasks = (
            int(raw_tasks)
            if isinstance(raw_tasks, (str, int)) and str(raw_tasks).isdigit()
            else None
        )
        state = str(row.get("state", "")).lower()
        if state != "stopped" or tasks != 0:
            return M0StopInspection(target.app_id, "not_stopped", tasks, None, False)
        try:
            helpers = self._smoke_helpers()
            evidence = helpers["_stop_evidence"](
                target.app_id,
                target.environment_name,
                deadline=time.monotonic() + MAX_MODAL_COMMAND_SECONDS,
            )
        except Exception:
            return M0StopInspection(target.app_id, "unknown", tasks, None, False)
        if evidence == {"app_state": "stopped", "app_tasks": 0, "running_containers": 0}:
            return M0StopInspection(target.app_id, "stopped", 0, 0, True)
        return M0StopInspection(target.app_id, "unknown", tasks, None, False)


M0CallHandle = ModalM0Call
