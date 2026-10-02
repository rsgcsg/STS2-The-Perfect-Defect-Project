"""Bounded Modal transport for owner-admitted train-only M0 updates.

This adapter never prepares owner work, selects a completion, or publishes artifacts.
The caller persists its attempt before ``submit`` and persists the returned call handle
before polling. A submit exception is ambiguous and must not trigger another submit.

Request/result structure and local checkpoint validation do not prove that an arbitrary
malicious remote actually performed the claimed mathematics. This is a provider-bound
execution seam, not TEE or recomputation evidence.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import math
import platform
import re
import struct
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes, object_fields

from ..canonical import semantic_hash

MODAL_SDK_VERSION = "1.5.5"
CALL_SCHEMA = "stpd/modal-m0-call-v2"
M0_MODAL_APP_NAME = "stpd-token-remote-update-m0"
M0_MODAL_FUNCTION_NAME = "token_remote_update"

# Leave headroom over the current few-MiB request and ~70-MiB checkpoint result.
# Modal stores function payloads over 2 MiB in object storage automatically; these
# application bounds do not claim a provider-wide maximum.
MAX_M0_REQUEST_BYTES = 64 * 1024 * 1024
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


@dataclass(frozen=True)
class ModalM0Target:
    """One immutable deployed function contract, never a generic Modal target."""

    account_id: str
    environment_name: str
    app_id: str
    app_version: int
    function_id: str
    image_object_id: str
    producer: Producer
    app_name: str = M0_MODAL_APP_NAME
    function_name: str = M0_MODAL_FUNCTION_NAME

    def __post_init__(self) -> None:
        _identifier(self.account_id, "account_id")
        _identifier(self.environment_name, "environment_name")
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
        if self.app_name != M0_MODAL_APP_NAME or self.function_name != M0_MODAL_FUNCTION_NAME:
            raise BoundaryError("modal_m0_target", "wrong_deployed_entry")

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "environment_name": self.environment_name,
            "app_id": self.app_id,
            "app_version": self.app_version,
            "function_id": self.function_id,
            "image_object_id": self.image_object_id,
            "producer": self.producer.to_dict(),
            "app_name": self.app_name,
            "function_name": self.function_name,
        }

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
                    "app_name",
                    "function_name",
                },
                "modal_m0_target",
            )
        )
        obj["producer"] = Producer.decode(obj["producer"])
        return cls(**obj)


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
        try:
            from ..workers.token_ranking import TokenTargetRuntime
        except ImportError:
            raise BoundaryError(
                "modal_m0_target_runtime", "core_target_runtime_unavailable"
            ) from None
        return TokenTargetRuntime(
            torch_version=self.torch_version,
            cpu_threads=self.cpu_threads,
        )


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
            or "a10" not in self.gpu_name.casefold()
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
        or "a10" not in gpu_name.casefold()
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


class ModalM0Provider:
    """Submit and poll one version-pinned M0 function without retry or scheduler state."""

    def __init__(
        self,
        target: ModalM0Target,
        target_runtime: ModalM0TargetRuntimeMetadata,
        *,
        sdk: Any = None,
    ) -> None:
        if not isinstance(target, ModalM0Target):
            raise BoundaryError("modal_m0", "typed_target_required")
        if not isinstance(target_runtime, ModalM0TargetRuntimeMetadata):
            raise BoundaryError("modal_m0", "typed_target_runtime_required")
        target_runtime.bind(target)
        self.target = target
        self.target_runtime = target_runtime
        self._sdk = sdk
        self._runtime_evidence: dict[str, ModalM0RuntimeEvidence] = {}

    def token_target_runtime(self) -> Any:
        """Return the value to pass to ``prepare_token_run(target_runtime=...)``."""
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

    def restore_handle(self, raw: bytes) -> ModalM0Call:
        handle = ModalM0Call.from_bytes(raw)
        if handle.target != self.target or handle.target_runtime != self.target_runtime:
            raise BoundaryError("modal_m0", "foreign_target")
        return handle

    def get_runtime_evidence(self, handle: ModalM0Call) -> ModalM0RuntimeEvidence:
        """Read verified evidence cached by poll; persist its bytes beside the handle."""
        if (
            not isinstance(handle, ModalM0Call)
            or handle.target != self.target
            or handle.target_runtime != self.target_runtime
        ):
            raise BoundaryError("modal_m0", "foreign_target")
        evidence = self._runtime_evidence.get(handle.call_id)
        if evidence is None:
            raise BoundaryError("modal_m0", "runtime_evidence_unavailable")
        evidence.bind(handle)
        return evidence

    def _function(self) -> Any:
        sdk = self._client()
        try:
            function = sdk.Function.from_name(
                self.target.app_name,
                self.target.function_name,
                version=self.target.app_version,
                environment_name=self.target.environment_name,
            )
            function.hydrate()
            metadata = getattr(function, "_metadata", None)
            actual = (
                function.object_id,
                getattr(function, "_app_id", None),
                getattr(metadata, "function_name", None),
                getattr(metadata, "image_id", None),
            )
        except BoundaryError:
            raise
        except Exception:
            raise BoundaryError("modal_m0", "target_unavailable") from None
        expected = (
            self.target.function_id,
            self.target.app_id,
            self.target.function_name,
            self.target.image_object_id,
        )
        if actual != expected:
            raise BoundaryError("modal_m0", "deployed_target_identity_mismatch")
        return function

    def submit(self, request_bytes: bytes) -> ModalM0Call:
        """Submit once. An error from ``spawn`` becomes unknown; callers must not repeat it."""
        if (
            not isinstance(request_bytes, bytes)
            or not 1 <= len(request_bytes) <= MAX_M0_REQUEST_BYTES
        ):
            raise BoundaryError("modal_m0", "request_size_limit")
        request = _decode_request(request_bytes)
        if request.to_bytes() != request_bytes:
            raise BoundaryError("modal_m0", "noncanonical_request")
        if request.producer != self.target.producer or request.target_device != "cuda":
            raise BoundaryError("modal_m0", "request_source_or_cuda_mismatch")
        if _request_runtime(request) != (
            self.target_runtime.torch_version,
            self.target_runtime.cpu_threads,
        ):
            raise BoundaryError("modal_m0", "request_target_runtime_mismatch")

        function = self._function()
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
            self.target,
            self.target_runtime,
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
        if (
            not isinstance(handle, ModalM0Call)
            or handle.target != self.target
            or handle.target_runtime != self.target_runtime
        ):
            raise BoundaryError("modal_m0", "foreign_target")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 <= timeout_seconds <= MAX_POLL_TIMEOUT_SECONDS
        ):
            raise BoundaryError("modal_m0", "poll_timeout_limit")

        function = self._function()
        try:
            call = self._client().FunctionCall.from_id(
                handle.call_id,
                client=function.client,
            )
            input_count = call.num_inputs()
            call_identity = (
                getattr(call, "_app_id", None),
                getattr(call, "_function_id", None),
            )
            if input_count != 1 or call_identity != (self.target.app_id, self.target.function_id):
                raise BoundaryError("modal_m0", "saved_call_identity_mismatch")
            result_bytes = call.get(timeout=float(timeout_seconds), index=0)
        except TimeoutError:
            return None
        except BoundaryError:
            raise
        except Exception:
            raise BoundaryError("modal_m0", "result_unavailable") from None

        result_bytes, runtime_evidence = _decode_worker_response(result_bytes, handle)
        result = _decode_result(result_bytes)
        if not _result_matches_call(result, handle):
            raise BoundaryError("modal_m0", "result_handle_identity_mismatch")
        self._runtime_evidence[handle.call_id] = runtime_evidence
        return result_bytes
