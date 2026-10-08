"""Pure trusted recipe and application-to-worker contracts; no tensor runtime imports.

Applications own operation authority, writer reconciliation, and source permission.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol

from spireagent.json_boundary import BoundaryError, digest, object_fields


@dataclass(frozen=True)
class StructuredTrainingConfig:
    epochs: int = 1
    learning_rate: float = 1e-3
    seed: int = 0
    tbptt_advances: int = 4
    cpu_threads: int = 2
    max_updates: int = 1000

    def validate(self) -> None:
        if (
            type(self.epochs) is not int
            or not 1 <= self.epochs <= 100
            or self.learning_rate != 1e-3
            or type(self.seed) is not int
            or self.seed != 0
            or type(self.tbptt_advances) is not int
            or self.tbptt_advances != 4
            or type(self.cpu_threads) is not int
            or self.cpu_threads != 2
            or type(self.max_updates) is not int
            or not 1 <= self.max_updates <= 10000
        ):
            raise BoundaryError("structured_training", "unsupported_fixed_recipe")


REQUEST_SCHEMA = "stpd/structured-workload-request-v1"


class AttemptAuthority(Protocol):
    def assert_current(self, run_id: str, operation_id: str, attempt_id: str) -> None:
        """Raise when this application-owned writer is no longer authorized."""
        ...

    def authorize_resume(self, run_id: str, attempt_id: str, checkpoint_id: str) -> None:
        """Confirm exact checkpoint selection and prior writer terminal reconciliation."""
        ...


class ExecutionControl(Protocol):
    def requested_action(self) -> Literal["continue", "pause", "cancel"]: ...


@dataclass(frozen=True)
class StructuredWorkloadRequest:
    run_id: str
    training_input_id: str
    operation_id: str
    attempt_id: str
    mode: str = "start"
    resume_checkpoint_id: str | None = None
    checkpoint_every_boundaries: int = 1

    def validate(self) -> None:
        digest(self.run_id, "structured_workload.run_id")
        digest(self.training_input_id, "structured_workload.training_input_id")
        digest(self.operation_id, "structured_workload.operation_id", length=32)
        digest(self.attempt_id, "structured_workload.attempt_id", length=32)
        if self.mode not in {"start", "resume", "reconcile"}:
            raise BoundaryError("structured_workload", "unsupported_mode")
        if self.mode == "resume":
            digest(self.resume_checkpoint_id, "structured_workload.resume_checkpoint_id")
        elif self.resume_checkpoint_id is not None:
            raise BoundaryError("structured_workload", "unexpected_checkpoint")
        if (
            type(self.checkpoint_every_boundaries) is not int
            or not 1 <= self.checkpoint_every_boundaries <= 100
        ):
            raise BoundaryError("structured_workload", "checkpoint_interval")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"schema": REQUEST_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, value: object) -> StructuredWorkloadRequest:
        request = object_fields(
            value,
            {
                "schema",
                "run_id",
                "training_input_id",
                "operation_id",
                "attempt_id",
                "mode",
                "resume_checkpoint_id",
                "checkpoint_every_boundaries",
            },
            "structured_workload",
        )
        if request["schema"] != REQUEST_SCHEMA:
            raise BoundaryError("structured_workload", "unsupported_schema")
        result = cls(**{key: item for key, item in request.items() if key != "schema"})
        result.validate()
        return result


@dataclass(frozen=True)
class StructuredExecutionResult:
    state: str
    run_id: str
    attempt_id: str
    checkpoint_id: str | None
    result_id: str | None
    model_id: str | None
    event_step: int
    optimizer_updates: int
    attempt_seconds: float


def structured_workload_capabilities() -> dict[str, Any]:
    """Describe this installed fixed recipe without importing its numerical runtime."""
    return {
        "recipe": "structured-m2-cpu-v2",
        "request_schema": REQUEST_SCHEMA,
        "config_defaults": asdict(StructuredTrainingConfig()),
        "config_bounds": {"epochs": [1, 100], "max_updates": [1, 10000]},
        "fixed_config_fields": ["learning_rate", "seed", "tbptt_advances", "cpu_threads"],
        "device": "cpu",
        "resume": "explicit_verified_checkpoint_and_terminal_prior_writer",
        "control": ["pause", "cancel"],
        "control_boundary": "completed_tbptt_chunk_or_complete_evaluation_pass",
        "checkpoint_schema": "stpd/structured-m2-training-checkpoint-v2",
        "legacy_v1": "final_only_not_resumable",
        "automatic_retry": False,
    }
