"""Bounded SSH execution transport for the original ComputeProvider contract."""
from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.compute import (
    ComputeHandle,
    ComputeSubmissionUnknown,
    ComputeTerminated,
    ComputeTermination,
)
from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    decode_json,
    digest,
    json_bytes,
    object_fields,
)
from stpd.canonical import semantic_hash
from stpd.cloud_jobs.contracts import ComputeReceipt, ComputeRequest

CUBLAS_WORKSPACE = ":4096:8"  # Same fixed deployment setting as the worker image.


@dataclass(frozen=True)
class SshTarget:
    producer: Producer
    ssh_config: str
    host: str
    checkout: str
    interpreter: str
    private_env: str
    state_directory: str
    timeout_seconds: int
    gpu: str
    runtime: FrozenObject
    preparation_seconds: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.producer, Producer):
            raise BoundaryError("ssh_target", "untyped_producer")
        for field in ("ssh_config", "checkout", "interpreter", "private_env", "state_directory"):
            value = getattr(self, field)
            path_type = Path if field == "ssh_config" else PurePosixPath
            if (not isinstance(value, str) or not path_type(value).is_absolute()
                    or ".." in path_type(value).parts or "\x00" in value or "\n" in value):
                raise BoundaryError("ssh_target", "absolute_path_required")
        if (not isinstance(self.host, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.host)):
            raise BoundaryError("ssh_target", "host_alias_required")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 86400:
            raise BoundaryError("ssh_target", "timeout_limit")
        if (type(self.preparation_seconds) is not int
                or not 0 <= self.preparation_seconds <= 3600):
            raise BoundaryError("ssh_target", "preparation_timeout_limit")
        if not isinstance(self.gpu, str) or self.gpu not in {"none", "L40S"}:
            raise BoundaryError("ssh_target", "explicit_single_gpu_required")
        if any(PurePosixPath(getattr(self, field)).is_relative_to(PurePosixPath(self.checkout))
               for field in ("state_directory", "private_env")):
            raise BoundaryError("ssh_target", "private_state_outside_checkout_required")
        fields = {"python", "torch", "cuda", "gpu_name", "gpu_uuid", "driver"}
        if not isinstance(self.runtime, FrozenObject):
            raise BoundaryError("ssh_target", "typed_runtime_required")
        runtime = object_fields(self.runtime.value(), fields, "ssh_target.runtime")
        if any(not isinstance(v, str) or not v or len(v) > 256 for v in runtime.values()):
            raise BoundaryError("ssh_target", "explicit_runtime_identity_required")
        if not str(runtime["python"]).startswith("3.11."):
            raise BoundaryError("ssh_target", "python_311_required")
        if self.gpu == "none" and any(runtime[k] != "none" for k in
                                      ("cuda", "gpu_name", "gpu_uuid", "driver")):
            raise BoundaryError("ssh_target", "cpu_identity_mismatch")
        if self.gpu != "none" and (self.gpu not in str(runtime["gpu_name"])
                                   or any(runtime[k] == "none" for k in
                                          ("cuda", "gpu_uuid", "driver"))):
            raise BoundaryError("ssh_target", "gpu_identity_required")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if not self.preparation_seconds:
            value.pop("preparation_seconds")
        return {"schema": ("spireagent/ssh-target-2" if self.preparation_seconds
                           else "spireagent/ssh-target-1"), **value,
                "runtime": self.runtime.value()}

    @property
    def target_id(self) -> str:
        return semantic_hash(self.to_dict())

    @classmethod
    def decode(cls, value: object) -> SshTarget:
        schema = value.get("schema") if isinstance(value, dict) else None
        fields = {"schema", *cls.__dataclass_fields__}
        if schema == "spireagent/ssh-target-1":
            fields.remove("preparation_seconds")
        obj = dict(object_fields(value, fields, "ssh_target"))
        if obj.pop("schema") not in {"spireagent/ssh-target-1", "spireagent/ssh-target-2"}:
            raise BoundaryError("ssh_target", "unsupported_schema")
        if schema == "spireagent/ssh-target-2" and not obj["preparation_seconds"]:
            raise BoundaryError("ssh_target", "explicit_preparation_timeout_required")
        obj["producer"] = Producer.decode(obj["producer"])
        obj["runtime"] = FrozenObject.of(obj["runtime"])
        return cls(**obj)


@dataclass(frozen=True)
class SshCall:
    target_id: str
    request: ComputeRequest
    call_id: str

    def __post_init__(self) -> None:
        digest(self.target_id, "ssh_call.target_id")
        if not isinstance(self.request, ComputeRequest):
            raise BoundaryError("ssh_call", "untyped_request")
        if self.call_id != self.request.request_id:
            raise BoundaryError("ssh_call", "original_request_handle_required")

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "spireagent/ssh-call-1", "target_id": self.target_id,
                "request": self.request.to_dict(), "call_id": self.call_id}

    @classmethod
    def decode(cls, value: object) -> SshCall:
        obj = object_fields(value, {"schema", "target_id", "request", "call_id"}, "ssh_call")
        if obj["schema"] != "spireagent/ssh-call-1":
            raise BoundaryError("ssh_call", "unsupported_schema")
        return cls(obj["target_id"], ComputeRequest.decode(obj["request"]), obj["call_id"])


class SshProvider:
    def __init__(self, target: SshTarget) -> None:
        self.target = SshTarget.decode(target.to_dict())

    def restore_handle(self, value: object) -> SshCall:
        handle = SshCall.decode(value)
        self._validate(handle)
        return handle

    def _validate(self, handle: ComputeHandle) -> SshCall:
        if not isinstance(handle, SshCall):
            raise BoundaryError("ssh", "foreign_handle")
        if (handle.target_id != self.target.target_id
                or handle.request.producer != self.target.producer):
            raise BoundaryError("ssh", "foreign_target")
        return handle

    def _exchange(self, action: str, handle: SshCall) -> dict[str, Any]:
        # There is no caller-provided command. OpenSSH necessarily uses a remote
        # shell; quote only the fixed module invocation and explicit trusted paths.
        command = ("cd " + shlex.quote(self.target.checkout + "/python") + " && "
                   + shlex.join(["/usr/bin/env", "CUBLAS_WORKSPACE_CONFIG=" + CUBLAS_WORKSPACE,
                                 self.target.interpreter, "-m", "spireagent.ssh_compute_agent"]))
        argv = ["ssh", "-F", self.target.ssh_config, "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no",
                "-o", "ConnectionAttempts=1", "-o", "ConnectTimeout=15",
                "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=1",
                self.target.host, command]
        response = subprocess.run(argv, input=json_bytes({"action": action,
                                  "target": self.target.to_dict(), "handle": handle.to_dict()}),
                                  capture_output=True, timeout=45, check=False)
        if response.returncode or len(response.stdout) > 65536:
            raise BoundaryError("ssh", "transport_unavailable")
        value = object_fields(decode_json(response.stdout), {"target_id", "call_id", "state",
                              "receipt", "error", "termination"}, "ssh.response")
        if value["target_id"] != handle.target_id or value["call_id"] != handle.call_id:
            raise BoundaryError("ssh", "original_response_binding_mismatch")
        return dict(value)

    def submit(self, request: ComputeRequest) -> SshCall:
        if request.producer != self.target.producer:
            raise BoundaryError("ssh", "target_source_lock_mismatch")
        handle = SshCall(self.target.target_id, request, request.request_id)
        try:
            result = self._exchange("submit", handle)
            if result["state"] != "accepted" or result["error"] is not None:
                raise BoundaryError("ssh", "submission_rejected")
        except Exception:
            # The deterministic original handle can be reconstructed from the
            # durable submitting request for explicit observation, never resubmit.
            raise ComputeSubmissionUnknown(handle) from None
        return handle

    def poll(self, handle: ComputeHandle) -> ComputeReceipt | None:
        original = self._validate(handle)
        try:
            result = self._exchange("poll", original)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise BoundaryError("ssh", "result_unavailable") from None
        if (result["state"] == "running" and result["error"] is None
                and result["receipt"] is None and result.get("termination") is None):
            return None
        if result["state"] != "completed" or result["error"] is not None:
            if result.get("termination") is not None:
                termination = ComputeTermination.decode(result["termination"])
                termination.bind(original)
                if (result["state"] != "failed" or result["receipt"] is not None
                        or result["error"] != "execution_" + termination.outcome):
                    raise BoundaryError("ssh", "terminal_response_mismatch")
                raise ComputeTerminated(termination)
            code = result["error"]
            if code not in {"execution_timeout", "execution_cancelled", "execution_failed"}:
                code = "result_unavailable"
            raise BoundaryError("ssh", code)
        if result.get("termination") is not None:
            raise BoundaryError("ssh", "terminal_response_mismatch")
        receipt = ComputeReceipt.decode(result["receipt"])
        receipt.bind(original.request)
        return receipt

    def cancel(self, handle: ComputeHandle) -> None:
        original = self._validate(handle)
        try:
            result = self._exchange("cancel", original)
            if result["state"] not in {"cancel_requested", "completed", "failed"}:
                raise BoundaryError("ssh", "cancellation_unknown")
        except Exception:
            raise BoundaryError("ssh", "cancellation_unknown") from None


def provider_from_target(value: object) -> Any:
    """Decode only supported fixed target contracts; no import/command hooks."""
    if isinstance(value, dict) and value.get("schema") in {
        "spireagent/ssh-target-1", "spireagent/ssh-target-2"
    }:
        return SshProvider(SshTarget.decode(value))
    from stpd.cloud_jobs.modal import ModalProvider, ModalTarget

    return ModalProvider(ModalTarget.decode(value))
