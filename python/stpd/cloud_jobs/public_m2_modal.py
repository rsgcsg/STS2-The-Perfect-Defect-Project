"""Opaque public M2 bytes transport over one already prepared Modal function.

The controller owns reservation, deployment preparation, runtime qualification,
request persistence and result acceptance. This module verifies its pinned target
against Modal SDK 1.5.5 before one spawn and when restoring a saved call. It
never interprets the research request/result wire or submits a retry.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import math
import re
import struct
from dataclasses import dataclass
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes, object_fields

from .modal import MODAL_SDK_VERSION

MAX_PUBLIC_M2_REQUEST_BYTES = 256 * 1024 * 1024
MAX_PUBLIC_M2_RESULT_BYTES = 192 * 1024 * 1024
MAX_POLL_TIMEOUT_SECONDS = 60.0
_RESULT_MAGIC = b"STPD-PUBLIC-M2-MODAL-RESULT\x00"
_MAX_RESULT_HEADER_BYTES = 16 * 1024
MAX_PUBLIC_M2_TRANSPORT_BYTES = (
    MAX_PUBLIC_M2_RESULT_BYTES + len(_RESULT_MAGIC) + 4 + _MAX_RESULT_HEADER_BYTES
)
_ID = {
    "app": re.compile(r"ap-[A-Za-z0-9_-]+\Z"),
    "function": re.compile(r"fu-[A-Za-z0-9_-]+\Z"),
    "image": re.compile(r"im-[A-Za-z0-9_-]+\Z"),
    "call": re.compile(r"fc-[A-Za-z0-9_-]+\Z"),
}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _name(value: object, field: str) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise BoundaryError("public_m2_modal", "invalid_" + field)
    return value


def _id(value: object, kind: str) -> str:
    if not isinstance(value, str) or _ID[kind].fullmatch(value) is None:
        raise BoundaryError("public_m2_modal", "invalid_" + kind + "_id")
    return value


def encode_modal_response(result: bytes, binding: PublicM2ModalBinding) -> bytes:
    """Transport receipt from the deployed wrapper; leaves the M2 wire opaque."""
    if not isinstance(binding, PublicM2ModalBinding):
        raise BoundaryError("public_m2_modal", "typed_binding_required")
    if not isinstance(result, bytes) or not 1 <= len(result) <= MAX_PUBLIC_M2_RESULT_BYTES:
        raise BoundaryError("public_m2_modal", "result_size_limit")
    header = json_bytes({
        "schema": "stpd/public-m2-modal-response-v1",
        "request_sha256": binding.request_sha256,
        "producer": binding.producer.to_dict(),
        "image_object_id": binding.image_object_id,
        "runtime_receipt_sha256": binding.runtime_receipt_sha256,
        "result_sha256": hashlib.sha256(result).hexdigest(),
        "result_size_bytes": len(result),
    })
    if len(header) > _MAX_RESULT_HEADER_BYTES:
        raise BoundaryError("public_m2_modal", "result_header_size_limit")
    return _RESULT_MAGIC + struct.pack(">I", len(header)) + header + result


def _decode_modal_response(raw: bytes, binding: PublicM2ModalBinding) -> bytes:
    minimum = len(_RESULT_MAGIC) + 4 + 1 + 1
    if (not isinstance(raw, bytes) or not minimum <= len(raw)
            <= MAX_PUBLIC_M2_RESULT_BYTES + len(_RESULT_MAGIC) + 4
            + _MAX_RESULT_HEADER_BYTES):
        raise BoundaryError("public_m2_modal", "result_size_limit")
    if not raw.startswith(_RESULT_MAGIC):
        raise BoundaryError("public_m2_modal", "invalid_result_frame")
    offset = len(_RESULT_MAGIC)
    header_size = struct.unpack_from(">I", raw, offset)[0]
    offset += 4
    if not 1 <= header_size <= _MAX_RESULT_HEADER_BYTES or offset + header_size >= len(raw):
        raise BoundaryError("public_m2_modal", "result_header_size_limit")
    header_raw = raw[offset:offset + header_size]
    header = object_fields(decode_json(header_raw), {
        "schema", "request_sha256", "producer", "image_object_id",
        "runtime_receipt_sha256", "result_sha256", "result_size_bytes",
    }, "public_m2_modal.response")
    if json_bytes(header) != header_raw:
        raise BoundaryError("public_m2_modal", "noncanonical_result_header")
    body = raw[offset + header_size:]
    if not 1 <= len(body) <= MAX_PUBLIC_M2_RESULT_BYTES:
        raise BoundaryError("public_m2_modal", "result_size_limit")
    if (header["schema"] != "stpd/public-m2-modal-response-v1"
            or header["request_sha256"] != binding.request_sha256
            or header["producer"] != binding.producer.to_dict()
            or header["image_object_id"] != binding.image_object_id
            or header["runtime_receipt_sha256"] != binding.runtime_receipt_sha256
            or type(header["result_size_bytes"]) is not int
            or header["result_size_bytes"] != len(body)
            or header["result_sha256"] != hashlib.sha256(body).hexdigest()):
        raise BoundaryError("public_m2_modal", "result_target_binding_mismatch")
    return body


@dataclass(frozen=True)
class PublicM2ModalResources:
    gpu: str
    cpu: float
    memory_mib: int
    cpu_limit: float
    memory_limit_mib: int
    deadline_seconds: int
    startup_timeout_seconds: int
    scaledown_seconds: int = 30
    retries: int = 0
    max_containers: int = 1

    def __post_init__(self) -> None:
        if (not isinstance(self.gpu, str)
                or re.fullmatch(r"(?:none|[A-Za-z][A-Za-z0-9!.-]{0,31})", self.gpu) is None):
            raise BoundaryError("public_m2_modal", "explicit_gpu_required")
        if (isinstance(self.cpu, bool) or not isinstance(self.cpu, (float, int))
                or not math.isfinite(self.cpu) or not 0.125 <= self.cpu <= 64
                or self.cpu * 1000 != int(self.cpu * 1000)):
            raise BoundaryError("public_m2_modal", "explicit_cpu_required")
        if type(self.memory_mib) is not int or not 128 <= self.memory_mib <= 262_144:
            raise BoundaryError("public_m2_modal", "explicit_memory_required")
        if (isinstance(self.cpu_limit, bool)
                or not isinstance(self.cpu_limit, (float, int))
                or not math.isfinite(self.cpu_limit)
                or not self.cpu <= self.cpu_limit <= 64
                or self.cpu_limit * 1000 != int(self.cpu_limit * 1000)):
            raise BoundaryError("public_m2_modal", "explicit_cpu_limit_required")
        if (type(self.memory_limit_mib) is not int
                or not self.memory_mib <= self.memory_limit_mib <= 262_144):
            raise BoundaryError("public_m2_modal", "explicit_memory_limit_required")
        if (type(self.deadline_seconds) is not int
                or not 1 <= self.deadline_seconds <= 86_400
                or type(self.startup_timeout_seconds) is not int
                or not 1 <= self.startup_timeout_seconds <= self.deadline_seconds
                or type(self.scaledown_seconds) is not int
                or not 0 <= self.scaledown_seconds <= self.deadline_seconds):
            raise BoundaryError("public_m2_modal", "explicit_deadline_required")
        if type(self.retries) is not int or self.retries != 0:
            raise BoundaryError("public_m2_modal", "retries_must_be_zero")
        if type(self.max_containers) is not int or self.max_containers != 1:
            raise BoundaryError("public_m2_modal", "one_container_required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "gpu": self.gpu, "cpu": self.cpu, "memory_mib": self.memory_mib,
            "cpu_limit": self.cpu_limit, "memory_limit_mib": self.memory_limit_mib,
            "deadline_seconds": self.deadline_seconds,
            "startup_timeout_seconds": self.startup_timeout_seconds,
            "scaledown_seconds": self.scaledown_seconds,
            "retries": self.retries, "max_containers": self.max_containers,
        }

    def to_bytes(self) -> bytes:
        return json_bytes(self.to_dict())

    @property
    def plan_sha256(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()

    @classmethod
    def decode(cls, value: object) -> PublicM2ModalResources:
        obj = object_fields(value, set(cls.__dataclass_fields__), "public_m2_modal.resources")
        return cls(**obj)

    @classmethod
    def from_bytes(cls, raw: bytes) -> PublicM2ModalResources:
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= 4096:
            raise BoundaryError("public_m2_modal", "resource_plan_size_limit")
        value = cls.decode(decode_json(raw))
        if value.to_bytes() != raw:
            raise BoundaryError("public_m2_modal", "noncanonical_resource_plan")
        return value


@dataclass(frozen=True)
class PublicM2ModalBinding:
    """Controller-supplied pins; equality is checked, not qualification inferred."""

    producer: Producer
    request_sha256: str
    image_object_id: str
    runtime_receipt_sha256: str
    resources: PublicM2ModalResources

    def __post_init__(self) -> None:
        if not isinstance(self.producer, Producer):
            raise BoundaryError("public_m2_modal", "typed_producer_required")
        digest(self.request_sha256, "public_m2_modal.request_sha256")
        _id(self.image_object_id, "image")
        digest(self.runtime_receipt_sha256, "public_m2_modal.runtime_receipt_sha256")
        if not isinstance(self.resources, PublicM2ModalResources):
            raise BoundaryError("public_m2_modal", "typed_resources_required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "producer": self.producer.to_dict(), "request_sha256": self.request_sha256,
            "image_object_id": self.image_object_id,
            "runtime_receipt_sha256": self.runtime_receipt_sha256,
            "resources": self.resources.to_dict(),
        }

    @classmethod
    def decode(cls, value: object) -> PublicM2ModalBinding:
        obj = object_fields(value, set(cls.__dataclass_fields__), "public_m2_modal.binding")
        return cls(Producer.decode(obj["producer"]), obj["request_sha256"],
                   obj["image_object_id"], obj["runtime_receipt_sha256"],
                   PublicM2ModalResources.decode(obj["resources"]))


@dataclass(frozen=True)
class PublicM2ModalTarget:
    app_name: str
    environment_name: str
    app_id: str
    function_name: str
    function_id: str
    binding: PublicM2ModalBinding

    def __post_init__(self) -> None:
        _name(self.app_name, "app_name")
        _name(self.environment_name, "environment_name")
        _id(self.app_id, "app")
        _name(self.function_name, "function_name")
        _id(self.function_id, "function")
        if not isinstance(self.binding, PublicM2ModalBinding):
            raise BoundaryError("public_m2_modal", "typed_binding_required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "stpd/public-m2-modal-target-v1", "app_name": self.app_name,
            "environment_name": self.environment_name, "app_id": self.app_id,
            "function_name": self.function_name, "function_id": self.function_id,
            "binding": self.binding.to_dict(),
        }

    @classmethod
    def decode(cls, value: object) -> PublicM2ModalTarget:
        obj = dict(object_fields(value, {"schema", *cls.__dataclass_fields__},
                                 "public_m2_modal.target"))
        if obj.pop("schema") != "stpd/public-m2-modal-target-v1":
            raise BoundaryError("public_m2_modal", "unsupported_target_schema")
        obj["binding"] = PublicM2ModalBinding.decode(obj["binding"])
        return cls(**obj)


@dataclass(frozen=True)
class PublicM2ModalCall:
    target: PublicM2ModalTarget
    call_id: str
    request_size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.target, PublicM2ModalTarget):
            raise BoundaryError("public_m2_modal", "typed_target_required")
        _id(self.call_id, "call")
        if (type(self.request_size_bytes) is not int
                or not 1 <= self.request_size_bytes <= MAX_PUBLIC_M2_REQUEST_BYTES):
            raise BoundaryError("public_m2_modal", "request_size_limit")

    def to_bytes(self) -> bytes:
        return json_bytes({
            "schema": "stpd/public-m2-modal-call-v1", "target": self.target.to_dict(),
            "call_id": self.call_id, "request_size_bytes": self.request_size_bytes,
        })

    @classmethod
    def from_bytes(cls, raw: bytes) -> PublicM2ModalCall:
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= 16 * 1024:
            raise BoundaryError("public_m2_modal", "call_handle_size_limit")
        obj = object_fields(decode_json(raw), {
            "schema", "target", "call_id", "request_size_bytes",
        }, "public_m2_modal.call")
        if obj["schema"] != "stpd/public-m2-modal-call-v1":
            raise BoundaryError("public_m2_modal", "unsupported_call_schema")
        value = cls(PublicM2ModalTarget.decode(obj["target"]), obj["call_id"],
                    obj["request_size_bytes"])
        if value.to_bytes() != raw:
            raise BoundaryError("public_m2_modal", "noncanonical_call_handle")
        return value


class ModalPublicM2Provider:
    def __init__(self, target: PublicM2ModalTarget, expected: PublicM2ModalBinding,
                 *, sdk: Any = None, api: Any = None, async_utils: Any = None) -> None:
        if not isinstance(target, PublicM2ModalTarget) or not isinstance(
            expected, PublicM2ModalBinding
        ) or target.binding != expected:
            raise BoundaryError("public_m2_modal", "controller_target_binding_mismatch")
        self.target = target
        self.expected = expected
        self._sdk = sdk
        self._api = api
        self._async_utils = async_utils

    def _client(self) -> Any:
        if self._sdk is None:
            try:
                if importlib.metadata.version("modal") != MODAL_SDK_VERSION:
                    raise BoundaryError("public_m2_modal", "sdk_version_mismatch")
                self._sdk = importlib.import_module("modal")
            except (importlib.metadata.PackageNotFoundError, ImportError) as error:
                raise BoundaryError("public_m2_modal", "install_locked_cloud_extra") from error
        return self._sdk

    def _protocol(self) -> tuple[Any, Any]:
        if self._api is None:
            self._api = importlib.import_module("modal_proto.api_pb2")
        if self._async_utils is None:
            self._async_utils = importlib.import_module("modal._utils.async_utils")
        return self._api, self._async_utils

    def _definition(self, client: Any) -> None:
        """Read the same SDK 1.5.5 protobuf surfaces as the reviewed M0 path."""
        target = self.target
        plan = target.binding.resources
        api, async_utils = self._protocol()

        async def read() -> tuple[Any, Any, Any]:
            named = await client.stub.AppGetByDeploymentName(
                api.AppGetByDeploymentNameRequest(
                    name=target.app_name, environment_name=target.environment_name),
                retry=None, timeout=30,
            )
            layout = await client.stub.AppGetLayout(
                api.AppGetLayoutRequest(app_id=target.app_id), retry=None, timeout=30)
            function = await client.stub.FunctionGet(
                api.FunctionGetRequest(
                    app_name=target.app_name, object_tag=target.function_name,
                    environment_name=target.environment_name, app_version=0),
                retry=None, timeout=30,
            )
            return named, layout, function

        try:
            named, layout, response = async_utils.synchronizer.create_blocking(read)()
        except Exception:
            raise BoundaryError("public_m2_modal", "deployment_identity_unavailable") from None
        if (not isinstance(named, api.AppGetByDeploymentNameResponse)
                or not isinstance(layout, api.AppGetLayoutResponse)
                or not isinstance(response, api.FunctionGetResponse)
                or named.app_id != target.app_id
                or named.previous_app_id
                or response.function_id != target.function_id):
            raise BoundaryError("public_m2_modal", "deployment_identity_mismatch")
        handles = [row for row in layout.app_layout.objects
                   if row.object_id == target.function_id]
        metadata = response.handle_metadata
        if (dict(layout.app_layout.function_ids) != {
                target.function_name: target.function_id}
                or len(handles) != 1
                or not handles[0].HasField("function_handle_metadata")
                or metadata.app_id != target.app_id
                or metadata.function_name != target.function_name
                or metadata.is_method
                or metadata.function_type != api.Function.FUNCTION_TYPE_FUNCTION
                or handles[0].function_handle_metadata.app_id != target.app_id
                or handles[0].function_handle_metadata.function_name != target.function_name
                or response.function.function_name != target.function_name):
            raise BoundaryError("public_m2_modal", "deployment_identity_mismatch")
        ranked = response.function.ranked_functions
        if len(ranked) != 1 or ranked[0].rank != 0:
            raise BoundaryError("public_m2_modal", "deployment_resource_mismatch")
        definition = ranked[0].function
        resources = definition.resources
        scaling = response.function.autoscaler_settings
        gpu_count = 0 if plan.gpu == "none" else 1
        gpu_name = "" if plan.gpu == "none" else plan.gpu
        if (definition.function_name != target.function_name
                or definition.image_id != target.binding.image_object_id
                or resources.gpu_config.gpu_type != gpu_name
                or resources.gpu_config.count != gpu_count
                or resources.milli_cpu != int(plan.cpu * 1000)
                or resources.milli_cpu_max != int(plan.cpu_limit * 1000)
                or resources.memory_mb != plan.memory_mib
                or resources.memory_mb_max != plan.memory_limit_mib
                or response.function.timeout_secs != plan.deadline_seconds
                or definition.startup_timeout_secs != plan.startup_timeout_seconds
                or not definition.single_use_containers
                or definition.max_inputs != 1
                or definition.max_concurrent_inputs not in (0, 1)
                or scaling.scaledown_window != plan.scaledown_seconds
                or scaling.max_containers != 1
                or scaling.min_containers != 0
                or scaling.buffer_containers != 0
                or definition.retry_policy.retries != 0):
            raise BoundaryError("public_m2_modal", "deployment_resource_mismatch")

    def _bound_function(self) -> Any:
        try:
            sdk = self._client()
            client = sdk.Client.from_env()
            self._definition(client)
            function = sdk.Function.from_name(
                self.target.app_name, self.target.function_name,
                environment_name=self.target.environment_name, client=client,
            )
            function.hydrate()
            self._definition(client)
        except BoundaryError:
            raise
        except Exception:
            raise BoundaryError("public_m2_modal", "deployment_identity_unavailable") from None
        if function.object_id != self.target.function_id:
            raise BoundaryError("public_m2_modal", "deployment_identity_mismatch")
        return function

    def submit(self, request_bytes: bytes) -> PublicM2ModalCall:
        if (not isinstance(request_bytes, bytes)
                or not 1 <= len(request_bytes) <= MAX_PUBLIC_M2_REQUEST_BYTES):
            raise BoundaryError("public_m2_modal", "request_size_limit")
        if hashlib.sha256(request_bytes).hexdigest() != self.expected.request_sha256:
            raise BoundaryError("public_m2_modal", "request_binding_mismatch")
        function = self._bound_function()
        # The controller has already persisted a single submission intent. A
        # failure after invocation is ambiguous; no retry occurs here.
        try:
            call_id = function.spawn(request_bytes).object_id
            _id(call_id, "call")
        except Exception:
            raise BoundaryError(
                "public_m2_modal", "submission_unknown",
                "reconcile the persisted call; do not submit again",
            ) from None
        return PublicM2ModalCall(self.target, call_id, len(request_bytes))

    def _saved_call(self, handle: PublicM2ModalCall) -> Any:
        sdk = self._client()
        api, async_utils = self._protocol()
        try:
            client = sdk.Client.from_env()

            async def read() -> Any:
                return await client.stub.FunctionCallFromId(
                    api.FunctionCallFromIdRequest(function_call_id=handle.call_id),
                    retry=None, timeout=30,
                )

            response = async_utils.synchronizer.create_blocking(read)()
            if (not isinstance(response, api.FunctionCallFromIdResponse)
                    or response.function_call_id != handle.call_id
                    or response.num_inputs != 1
                    or response.metadata.app_id != self.target.app_id
                    or response.metadata.function_id != self.target.function_id):
                raise BoundaryError("public_m2_modal", "saved_call_identity_mismatch")
            return sdk.FunctionCall.from_id(handle.call_id, client=client)
        except BoundaryError:
            raise
        except Exception:
            raise BoundaryError("public_m2_modal", "saved_call_unavailable") from None

    def poll(self, handle: PublicM2ModalCall, *, timeout_seconds: float = 0.0) -> bytes | None:
        if not isinstance(handle, PublicM2ModalCall) or handle.target != self.target:
            raise BoundaryError("public_m2_modal", "foreign_handle")
        if (isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds)
                or not 0 <= timeout_seconds <= MAX_POLL_TIMEOUT_SECONDS):
            raise BoundaryError("public_m2_modal", "poll_timeout_limit")
        sdk = self._client()
        errors = getattr(sdk, "exception", None)
        timeout_type = getattr(errors, "TimeoutError", TimeoutError)
        timeout_types = tuple({TimeoutError, timeout_type})
        terminal_types = tuple(
            value for name in ("FunctionTimeoutError", "OutputExpiredError", "RemoteError")
            if isinstance(value := getattr(errors, name, None), type)
            and issubclass(value, BaseException)
        )
        cancellation_type = getattr(errors, "InputCancellation", None)
        try:
            result = self._saved_call(handle).get(timeout=float(timeout_seconds), index=0)
        except terminal_types:
            raise BoundaryError("public_m2_modal", "call_terminal_failure") from None
        except timeout_types:
            return None
        except BoundaryError:
            raise
        except BaseException as error:
            if cancellation_type is not None and isinstance(error, cancellation_type):
                raise BoundaryError("public_m2_modal", "call_cancelled") from None
            if not isinstance(error, Exception):
                raise
            raise BoundaryError("public_m2_modal", "result_unavailable") from None
        # The transport header binds the deployed wrapper's pinned environment;
        # model result semantics still belong to the controller's acceptance API.
        return _decode_modal_response(result, self.expected)
