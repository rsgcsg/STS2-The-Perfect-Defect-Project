"""Modal is a launch mechanism, never a task lease or result selection authority."""

from __future__ import annotations

import importlib
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.compute import ComputeHandle
from spireagent.json_boundary import BoundaryError, object_fields, unsigned

from ..canonical import semantic_hash
from .contracts import ComputeReceipt, ComputeRequest

MODAL_SDK_VERSION = "1.5.5"


def _original_output(sdk: Any, call_id: str) -> tuple[bool, Any]:
    """Read this spawn's original ACK; an exception never establishes pending.

    The locked SDK's get(timeout=0) uses built-in TimeoutError for both a fresh
    empty ACK and an RPC timeout. OutputExpiredError also inherits its polling
    TimeoutError. Keep this SDK-specific distinction inside the existing provider.
    """
    import asyncio

    api = importlib.import_module("modal_proto.api_pb2")
    decode = importlib.import_module("modal._utils.function_utils")._process_result
    synchronizer = importlib.import_module("modal._utils.async_utils").synchronizer

    async def read() -> tuple[bool, Any]:
        client = await sdk.Client.from_env.aio()
        response = await client.stub.FunctionGetOutputs(
            api.FunctionGetOutputsRequest(
                function_call_id=call_id, timeout=0, last_entry_id="0-0",
                clear_on_success=False, requested_at=time.time(), start_idx=0, end_idx=0,
            ),
            retry=None, timeout=30,
        )
        if not isinstance(response, api.FunctionGetOutputsResponse):
            raise BoundaryError("modal", "original_output_binding_mismatch")
        if not response.outputs:
            if response.num_unfinished_inputs == 1:
                return True, None
            if response.num_unfinished_inputs == 0:
                raise sdk.exception.OutputExpiredError()
            raise BoundaryError("modal", "original_output_binding_mismatch")
        if (len(response.outputs) != 1 or response.outputs[0].idx != 0
                or response.num_unfinished_inputs != 0):
            raise BoundaryError("modal", "original_output_binding_mismatch")
        output = response.outputs[0]
        terminal_statuses = {
            api.GenericResult.GENERIC_STATUS_SUCCESS, api.GenericResult.GENERIC_STATUS_FAILURE,
            api.GenericResult.GENERIC_STATUS_TERMINATED, api.GenericResult.GENERIC_STATUS_TIMEOUT,
            api.GenericResult.GENERIC_STATUS_INIT_FAILURE,
            api.GenericResult.GENERIC_STATUS_INTERNAL_FAILURE,
            api.GenericResult.GENERIC_STATUS_IDLE_TIMEOUT,
            api.GenericResult.GENERIC_STATUS_MEMORY_MANAGER_EVICTION,
        }
        if not output.HasField("result") or output.result.status not in terminal_statuses:
            raise BoundaryError("modal", "original_output_binding_mismatch")
        return False, await decode(output.result, output.data_format, client.stub, client)

    async def bounded() -> tuple[bool, Any]:
        return await asyncio.wait_for(read(), timeout=30)

    result: tuple[bool, Any] = synchronizer.create_blocking(bounded)()
    return result


@dataclass(frozen=True)
class ModalTarget:
    producer: Producer
    image: str
    gpu: str = "L4"
    timeout_seconds: int = 3600
    cpu_cores: int = 2
    memory_mib: int = 8192
    storage_secret_name: str = "stpd-worker-storage"
    qwen_volume_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.producer, Producer):
            raise BoundaryError("modal_target", "untyped_producer")
        if not isinstance(self.image, str) or not re.fullmatch(
            r"(?:[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}|im-[A-Za-z0-9]{22})",
            self.image,
        ):
            raise BoundaryError("modal_target", "immutable_image_digest_required")
        if self.gpu not in {"none", "L4", "A10", "L40S", "A100-40GB", "A100-80GB", "H100!"}:
            raise BoundaryError("modal_target", "explicit_single_compatible_gpu_required")
        if not 1 <= unsigned(self.timeout_seconds, "modal.timeout") <= 86400:
            raise BoundaryError("modal_target", "timeout_limit")
        if not unsigned(self.cpu_cores, "modal.cpu_cores"):
            raise BoundaryError("modal_target", "positive_cpu_required")
        if not unsigned(self.memory_mib, "modal.memory_mib"):
            raise BoundaryError("modal_target", "positive_memory_required")
        for value in (self.storage_secret_name, self.qwen_volume_name):
            if value is not None and (
                not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value)
            ):
                raise BoundaryError("modal_target", "invalid_resource_name")

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "stpd/modal-target-v2", **asdict(self)}

    @property
    def target_id(self) -> str:
        return semantic_hash(self.to_dict())

    @property
    def app_name(self) -> str:
        # Free plans do not offer version-pinned function lookups. Distinct source,
        # image, resource and timeout configurations have distinct deployment names.
        return "stpd-compute-" + self.target_id[:48]

    @classmethod
    def decode(cls, value: object) -> ModalTarget:
        obj = dict(object_fields(value, {"schema", *cls.__dataclass_fields__}, "modal_target"))
        if obj.pop("schema") != "stpd/modal-target-v2":
            raise BoundaryError("modal_target", "unsupported_schema")
        obj["producer"] = Producer.decode(obj["producer"])
        return cls(**obj)


@dataclass(frozen=True)
class ModalCall:
    target_id: str
    request: ComputeRequest
    call_id: str

    def __post_init__(self) -> None:
        from spireagent.json_boundary import digest

        digest(self.target_id, "modal_call.target_id")
        if not isinstance(self.request, ComputeRequest):
            raise BoundaryError("modal_call", "untyped_request")
        if not isinstance(self.call_id, str) or not re.fullmatch(
            r"fc-[A-Za-z0-9_-]+", self.call_id
        ):
            raise BoundaryError("modal_call", "invalid_call_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "stpd/modal-call-v1", "target_id": self.target_id,
            "request": self.request.to_dict(), "call_id": self.call_id,
        }

    @classmethod
    def decode(cls, value: object) -> ModalCall:
        obj = object_fields(value, {"schema", "target_id", "request", "call_id"}, "modal_call")
        if obj["schema"] != "stpd/modal-call-v1":
            raise BoundaryError("modal_call", "unsupported_schema")
        return cls(obj["target_id"], ComputeRequest.decode(obj["request"]), obj["call_id"])


class ModalProvider:
    def __init__(self, target: ModalTarget, *, sdk: Any = None) -> None:
        self.target = target
        self._sdk = sdk

    def restore_handle(self, value: object) -> ModalCall:
        return ModalCall.decode(value)

    def _client(self) -> Any:
        if self._sdk is None:
            from importlib.metadata import PackageNotFoundError, version

            try:
                if version("modal") != MODAL_SDK_VERSION:
                    raise BoundaryError("modal", "sdk_version_mismatch")
                self._sdk = importlib.import_module("modal")
            except (ImportError, PackageNotFoundError) as error:
                raise BoundaryError("modal", "install_locked_cloud_extra") from error
        return self._sdk

    def submit(self, request: ComputeRequest) -> ModalCall:
        if request.producer != self.target.producer:
            raise BoundaryError("modal", "target_source_lock_mismatch")
        sdk = self._client()
        # The Hub must durably mark 'submitting' before this call. An exception
        # after invocation can mean the remote call exists: never retry it here.
        try:
            function = sdk.Function.from_name(self.target.app_name, "compute")
            call = function.spawn(request.to_dict(), self.target.target_id)
            return ModalCall(self.target.target_id, request, call.object_id)
        except Exception:
            raise BoundaryError(
                "modal", "submission_unknown",
                "reconcile the recorded attempt with the provider; do not submit again",
            ) from None

    def _call(self, handle: ComputeHandle) -> Any:
        if not isinstance(handle, ModalCall):
            raise BoundaryError("modal", "foreign_handle")
        if (
            handle.target_id != self.target.target_id
            or handle.request.producer != self.target.producer
        ):
            raise BoundaryError("modal", "foreign_target")
        return self._client().FunctionCall.from_id(handle.call_id)

    def poll(self, handle: ComputeHandle) -> ComputeReceipt | None:
        sdk = self._client()
        try:
            self._call(handle)  # Validate the original target/source/handle before any I/O.
            pending, value = _original_output(sdk, handle.call_id)
        except sdk.exception.FunctionTimeoutError:
            # This subclasses the SDK polling timeout, not Python's built-in
            # TimeoutError. Keep it distinct without inventing a terminal receipt.
            raise BoundaryError(
                "modal", "execution_timeout",
                "retain the exact call handle for terminal reconciliation; do not resubmit",
            ) from None
        except BoundaryError:
            raise
        except Exception:
            # Transport failure and remote failure are not guessed apart. Hub
            # retains uncertain until provider terminal evidence is available.
            raise BoundaryError("modal", "result_unavailable") from None
        if pending:
            return None
        result = object_fields(value, {"target_id", "receipt"}, "modal.response")
        if result["target_id"] != self.target.target_id:
            raise BoundaryError("modal", "deployed_target_mismatch")
        receipt = ComputeReceipt.decode(result["receipt"])
        receipt.bind(handle.request)
        return receipt

    def cancel(self, handle: ComputeHandle) -> None:
        try:
            self._call(handle).cancel(terminate_containers=True)
        except BoundaryError:
            raise
        except Exception:
            raise BoundaryError("modal", "cancellation_unknown") from None
        # Acknowledgement is not a fabricated terminal/result receipt. The Hub
        # records it and reconciles the provider's task status before rescheduling.


def credential_presence() -> dict[str, bool]:
    """Only booleans are exposed; never parse, print or copy profile credentials."""
    import os
    from pathlib import Path

    return {
        "token_id_env": bool(os.environ.get("MODAL_TOKEN_ID")),
        "token_secret_env": bool(os.environ.get("MODAL_TOKEN_SECRET")),
        "profile_file": (Path.home() / ".modal.toml").is_file(),
    }
