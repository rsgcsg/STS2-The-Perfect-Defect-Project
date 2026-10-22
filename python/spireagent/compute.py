"""Provider-neutral scheduling seam; task meaning remains with its worker contract."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, digest, object_fields, text
from stpd.cloud_jobs.contracts import ComputeReceipt, ComputeRequest


class ComputeTarget(Protocol):
    @property
    def producer(self) -> Producer: ...

    @property
    def timeout_seconds(self) -> int: ...

    @property
    def target_id(self) -> str: ...


class ComputeHandle(Protocol):
    @property
    def call_id(self) -> str: ...

    @property
    def target_id(self) -> str: ...

    @property
    def request(self) -> ComputeRequest: ...

    def to_dict(self) -> dict[str, Any]: ...


class ComputeProvider(Protocol):
    @property
    def target(self) -> ComputeTarget: ...

    def submit(self, request: ComputeRequest) -> ComputeHandle: ...
    def restore_handle(self, value: object) -> ComputeHandle: ...
    def poll(self, handle: ComputeHandle) -> ComputeReceipt | None: ...
    def cancel(self, handle: ComputeHandle) -> None: ...


def decode_compute_handle(value: object) -> ComputeHandle:
    """The supported transports have closed handle codecs, never import hooks."""
    if isinstance(value, dict) and value.get("schema") == "spireagent/ssh-call-1":
        from spireagent.ssh_compute import SshCall

        return SshCall.decode(value)
    from stpd.cloud_jobs.modal import ModalCall

    return ModalCall.decode(value)


@dataclass(frozen=True)
class ComputeTermination:
    """Original invocation process termination; no data/checkpoint acceptance."""

    target_id: str
    request_id: str
    attempt_id: str
    call_id: str
    outcome: str
    process_exited: bool

    def __post_init__(self) -> None:
        digest(self.target_id, "compute_termination.target_id")
        digest(self.request_id, "compute_termination.request_id")
        text(self.attempt_id, "compute_termination.attempt_id", maximum=128)
        text(self.call_id, "compute_termination.call_id", maximum=256)
        if (self.outcome not in {"failed", "cancelled", "timeout"}
                or self.process_exited is not True):
            raise BoundaryError("compute_termination", "confirmed_terminal_process_required")

    def bind(self, handle: ComputeHandle) -> None:
        if (self.target_id != handle.target_id or self.request_id != handle.request.request_id
                or self.attempt_id != handle.request.attempt_id or self.call_id != handle.call_id):
            raise BoundaryError("compute_termination", "original_handle_binding_mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "spireagent/compute-termination-1", **asdict(self)}

    @classmethod
    def decode(cls, value: object) -> ComputeTermination:
        obj = dict(object_fields(value, {"schema", *cls.__dataclass_fields__},
                                 "compute_termination"))
        if obj.pop("schema") != "spireagent/compute-termination-1":
            raise BoundaryError("compute_termination", "unsupported_schema")
        return cls(**obj)


class ComputeTerminated(Exception):
    def __init__(self, termination: ComputeTermination) -> None:
        if not isinstance(termination, ComputeTermination):
            raise BoundaryError("compute_termination", "typed_termination_required")
        self.termination = termination
        super().__init__("execution_" + termination.outcome)


class ComputeSubmissionUnknown(Exception):
    """A transport can identify the original invocation despite a lost submit ACK."""

    def __init__(self, handle: ComputeHandle) -> None:
        self.handle = handle
        super().__init__("submission_unknown")
