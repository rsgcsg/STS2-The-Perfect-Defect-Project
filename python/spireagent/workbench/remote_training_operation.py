"""Remote placement of the existing application's one training operation.

Only LocalTrainingService writes this journal. Provider claims precede side effects;
returned artifacts are quarantined until the original call and App are terminal.
"""

from __future__ import annotations

import hashlib
import math
import os
import stat
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    digest,
    json_bytes,
    object_fields,
    text,
)
from spireagent.storage.replaceable_file import write_replaceable_json
from spireagent.training_execution_contracts import (
    CandidateResult,
    KnownFailure,
    NotStarted,
    PreparedTrainingAttempt,
    StopInspection,
    TrainingAppRef,
    TrainingCallHandle,
    TrainingClaim,
    TrainingExecutor,
    TrainingPlacement,
    TrainingSliceLimits,
    Unknown,
)
from spireagent.training_transport import decode_candidate, decode_projection
from spireagent.workbench.recipe_contracts import (
    REMOTE_PLACEMENTS,
    checked_training_counters,
    effective_training_limits,
)

REMOTE_OPERATION_SCHEMA = "spireagent/local-training-operation-v4"
REMOTE_ISOLATION = "finite_remote_slice-v1"
TARGET_SCHEMA = "spireagent/configured-training-target-v1"


@dataclass(frozen=True)
class ConfiguredTrainingTarget:
    """Expected nonsecret target; the qualification reference is not proof by itself."""

    placement_id: str
    recipe_id: str
    worker_capability: str
    placement: TrainingPlacement
    runtime: FrozenObject
    producer: Producer
    code_closure_sha256: str
    qualification_id: str
    limits: TrainingSliceLimits
    max_operation_cost_microusd: int
    cleanup_seconds: int

    def __post_init__(self) -> None:
        from spireagent.workbench.trusted_recipes import STRUCTURED_RECIPES

        if (
            self.placement_id not in REMOTE_PLACEMENTS
            or self.recipe_id not in STRUCTURED_RECIPES
            or self.worker_capability != "structured-m2-slice-v1"
        ):
            raise BoundaryError("local_training", "unsupported_remote_tuple")
        expected = "cpu" if self.placement_id == "modal-cpu" else "cuda:0"
        if (
            not isinstance(self.runtime, FrozenObject)
            or not isinstance(self.producer, Producer)
            or not isinstance(self.placement, TrainingPlacement)
            or not isinstance(self.limits, TrainingSliceLimits)
            or self.placement.device != expected
            or self.runtime.value().get("device") != expected
            or self.runtime.value().get("dtype") != "float32"
        ):
            raise BoundaryError("local_training", "remote_runtime_binding_mismatch")
        for name in ("torch_version", "python_version"):
            text(self.runtime.value().get(name), "local_training.remote_runtime", maximum=96)
        digest(self.code_closure_sha256, "local_training.remote_code")
        digest(self.qualification_id, "local_training.remote_qualification")
        if (
            type(self.max_operation_cost_microusd) is not int
            or not self.limits.max_cost_microusd <= self.max_operation_cost_microusd <= 10**12
        ):
            raise BoundaryError("local_training", "invalid_remote_operation_cost_cap")
        if type(self.cleanup_seconds) is not int or not 1 <= self.cleanup_seconds <= 3600:
            raise BoundaryError("local_training", "explicit_finite_cleanup_seconds_required")
        # Apply the existing durable metadata secret boundary to the whole configuration.
        Manifest("analysis", self.producer, parameters=FrozenObject.of(self.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return {
            "schema": TARGET_SCHEMA,
            "placement_id": self.placement_id,
            "recipe_id": self.recipe_id,
            "worker_capability": self.worker_capability,
            "placement": asdict(self.placement),
            "runtime": self.runtime.value(),
            "producer": self.producer.to_dict(),
            "code_closure_sha256": self.code_closure_sha256,
            "qualification_id": self.qualification_id,
            "limits": asdict(self.limits),
            "max_operation_cost_microusd": self.max_operation_cost_microusd,
            "cleanup_seconds": self.cleanup_seconds,
        }

    @classmethod
    def from_dict(cls, value: object) -> ConfiguredTrainingTarget:
        row = dict(object_fields(value, {"schema", *cls.__dataclass_fields__}, "local_training"))
        if row.pop("schema") != TARGET_SCHEMA:
            raise BoundaryError("local_training", "unsupported_remote_target_schema")
        row.update(
            placement=TrainingPlacement.from_dict(row["placement"]),
            runtime=FrozenObject.of(row["runtime"]),
            producer=Producer.decode(row["producer"]),
            limits=TrainingSliceLimits.from_dict(row["limits"]),
        )
        return cls(**row)


@dataclass(frozen=True)
class RemoteTrainingBinding:
    target: ConfiguredTrainingTarget
    executor_factory: Callable[[Any], TrainingExecutor]

    def __post_init__(self) -> None:
        if not isinstance(self.target, ConfiguredTrainingTarget) or not callable(
            self.executor_factory
        ):
            raise BoundaryError("local_training", "typed_remote_binding_required")

    @classmethod
    def modal(cls, target: ConfiguredTrainingTarget) -> RemoteTrainingBinding:
        """Ordinary internal-library entry; uses the same executor as qualification."""

        def factory(owner: Any) -> TrainingExecutor:
            from spireagent.modal_training_executor import ModalTrainingExecutor

            return ModalTrainingExecutor(owner=owner)

        return cls(target, factory)

    @staticmethod
    def supports_adapter(adapter: Any) -> bool:
        return all(
            callable(getattr(adapter, name, None))
            for name in (
                "prepare_remote",
                "accept_remote_candidate",
                "preflight_remote",
                "verify_remote_resume",
                "training_plan",
            )
        )

    def available(self) -> bool:
        from spireagent.workbench.trusted_recipes import recipe_adapter

        return self.supports_adapter(recipe_adapter(self.target.recipe_id))

    def preflight(self, request: Any, adapter: Any) -> None:
        if (
            request.placement_id != self.target.placement_id
            or request.recipe_id != self.target.recipe_id
            or not self.supports_adapter(adapter)
        ):
            raise BoundaryError("local_training", "remote_recipe_unavailable")
        limits = request.limits
        if (
            not limits
            or self.target.limits.wall_seconds
            + self.target.limits.startup_seconds
            + self.target.cleanup_seconds
            > limits["wall_seconds"]
            or self.target.limits.max_candidate_bytes
            + self.target.limits.max_projection_bytes
            + self.target.limits.max_request_bytes
            > limits["scratch_bytes"]
        ):
            raise BoundaryError("local_training", "remote_grant_exceeds_operation_budget")
        adapter.preflight_remote(request, self.target)


@dataclass(frozen=True)
class RemotePreparedWorkload:
    prepared: PreparedTrainingAttempt
    request_bytes: bytes
    projection_bytes: bytes


class ApplicationTrainingAttemptOwner:
    """The executor's durability port, scoped to the original operation and attempt."""

    def __init__(self, service: Any, path: Path, owner: Any, operation: dict[str, Any]) -> None:
        self.service, self.path, self.owner = service, path, owner
        self.operation_id, self.attempt_id = operation["operation_id"], operation["attempt_id"]
        self._control_deadline_monotonic: float | None = None

    def current(self) -> dict[str, Any]:
        value = self.service._read(self.path, self.owner.identity)
        if (
            value.get("schema") != REMOTE_OPERATION_SCHEMA
            or value.get("operation_id") != self.operation_id
            or value.get("attempt_id") != self.attempt_id
        ):
            raise BoundaryError("local_training", "remote_attempt_fence_lost")
        return value

    def write(self, value: dict[str, Any]) -> None:
        value["updated_at"] = time.time()
        write_replaceable_json(self.path, value)

    def _bound(self, prepared_id: str) -> dict[str, Any]:
        value = self.current()
        if value["remote"].get("prepared_id") != prepared_id:
            raise BoundaryError("local_training", "remote_prepared_identity_mismatch")
        return value

    def remaining_wallet_seconds(self) -> float:
        """The original deadline never resets when a phase or backend changes."""
        value = self.current()
        if self._control_deadline_monotonic is not None:
            return max(0.0, self._control_deadline_monotonic - time.monotonic())
        remote = value["remote"]
        deadline = remote.get("deadline_at")
        if type(deadline) not in {int, float} or not math.isfinite(deadline):
            raise BoundaryError("local_training", "original_remote_deadline_required")
        active = (
            self.service._active_attempt == self.attempt_id
            and self.service._attempt_deadline_monotonic is not None
        )
        if active:
            return max(0.0, self.service._attempt_deadline_monotonic - time.monotonic())
        now = time.time()
        if now < max(value["attempt_started_at"], value["updated_at"]):
            raise BoundaryError("local_training", "clock_recovery_required")
        return max(
            0.0,
            min(
                deadline - now,
                effective_training_limits(value)["wall_seconds"] - value["elapsed_seconds"],
            ),
        )

    def remaining_seconds(self, prepared_id: str) -> float:
        """A's per-call budget is a projection of this owner's original wallet."""
        value = self._bound(prepared_id)
        reserve = (
            ConfiguredTrainingTarget.from_dict(value["remote"]["target"]).cleanup_seconds
            if value["remote"].get("budget_phase") != "cleanup"
            and self._control_deadline_monotonic is None
            else 0
        )
        return max(0.0, self.remaining_wallet_seconds() - reserve)

    def assert_preparation_budget(self) -> None:
        """Domain input preparation checks this fence at each bounded work boundary."""
        value = self.current()
        target = ConfiguredTrainingTarget.from_dict(value["remote"]["target"])
        if (
            value["status"] != "pending"
            or value["writer_terminal"]
            or value["requested_action"] != "continue"
        ):
            raise BoundaryError("local_training", "remote_preparation_control_withheld")
        if self.remaining_wallet_seconds() < (
            target.limits.wall_seconds + target.limits.startup_seconds + target.cleanup_seconds
        ):
            raise BoundaryError("local_training", "remote_preparation_budget_exhausted")

    def begin_cleanup(self) -> None:
        with self.service._lock:
            value = self.current()
            value["remote"]["budget_phase"] = "cleanup"
            self.write(value)

    def begin_explicit_control(self) -> None:
        """Only explicit reconcile authorizes this saved finite control-only cap."""
        with self.service._lock:
            value = self.current()
            target = ConfiguredTrainingTarget.from_dict(value["remote"]["target"])
            previous = value["remote"].get("control_observation")
            if previous is not None:
                raw = json_bytes(previous)
                _private_bytes(
                    _quarantine_path(
                        self.path, self.attempt_id, "control-" + previous["control_id"]
                    ),
                    raw,
                )
            now = time.time()
            self._control_deadline_monotonic = time.monotonic() + target.cleanup_seconds
            value["remote"]["control_observation"] = {
                "control_id": uuid.uuid4().hex,
                "operation_id": self.operation_id,
                "attempt_id": self.attempt_id,
                "allowance_seconds": target.cleanup_seconds,
                "started_at": now,
                "state": "pending",
                "training_budget_extended": False,
                "automatic_retry": False,
            }
            self.write(value)

    def finish_explicit_control(self, state: str) -> None:
        if self._control_deadline_monotonic is None:
            return
        with self.service._lock:
            value = self.current()
            record = value["remote"]["control_observation"]
            record.update(
                state=state, ended_at=time.time(), remaining_seconds=self.remaining_wallet_seconds()
            )
            self.write(value)

    def _claim(self, attempt: PreparedTrainingAttempt, field: str) -> TrainingClaim:
        with self.service._lock:
            value = self._bound(attempt.identity)
            # Durable prior intent always wins over a later control change.
            if value["remote"].get(field):
                return TrainingClaim.PRIOR
            if (
                self._control_deadline_monotonic is not None
                or value["status"] != "pending"
                or value["writer_terminal"]
                or value["requested_action"] != "continue"
            ):
                return TrainingClaim.WITHHELD
            if self.remaining_seconds(attempt.identity) <= 0:
                return TrainingClaim.WITHHELD
            if field == "prepare_claimed":
                target = ConfiguredTrainingTarget.from_dict(value["remote"]["target"])
                reserved = value.get("cost_reserved_microusd", 0) + attempt.limits.max_cost_microusd
                if reserved > target.max_operation_cost_microusd:
                    raise BoundaryError("local_training", "cumulative_remote_cost_exhausted")
                value["cost_reserved_microusd"] = reserved
            value["remote"][field] = True
            self.write(value)
            return TrainingClaim.CLAIMED

    def claim_preparation(self, attempt: PreparedTrainingAttempt) -> TrainingClaim:
        return self._claim(attempt, "prepare_claimed")

    def claim_submission_once(
        self, attempt: PreparedTrainingAttempt, ref: TrainingAppRef
    ) -> TrainingClaim:
        ref.bind(attempt)
        if self.load_app_ref(attempt.identity) != ref:
            raise BoundaryError("local_training", "remote_app_ref_mismatch")
        return self._claim(attempt, "submit_claimed")

    def load_app_ref(self, prepared_id: str) -> TrainingAppRef | None:
        with self.service._lock:
            raw = self._bound(prepared_id)["remote"].get("app_ref")
            return TrainingAppRef.from_dict(raw) if raw is not None else None

    def persist_app_ref(self, attempt: PreparedTrainingAttempt, ref: TrainingAppRef) -> None:
        ref.bind(attempt)
        with self.service._lock:
            value = self._bound(attempt.identity)
            previous = value["remote"].get("app_ref")
            if not value["remote"].get("prepare_claimed") or (
                previous is not None and previous != ref.to_dict()
            ):
                raise BoundaryError("local_training", "remote_app_ref_mismatch")
            value["remote"]["app_ref"] = ref.to_dict()
            self.write(value)

    def load_call_handle(self, prepared_id: str) -> TrainingCallHandle | None:
        with self.service._lock:
            raw = self._bound(prepared_id)["remote"].get("call_handle")
            return TrainingCallHandle.from_dict(raw) if raw is not None else None

    def persist_call_handle(
        self, attempt: PreparedTrainingAttempt, handle: TrainingCallHandle
    ) -> None:
        handle.app_ref.bind(attempt)
        with self.service._lock:
            value = self._bound(attempt.identity)
            previous = value["remote"].get("call_handle")
            if (
                not value["remote"].get("submit_claimed")
                or value["remote"].get("app_ref") != handle.app_ref.to_dict()
                or previous is not None
                and previous != handle.to_dict()
            ):
                raise BoundaryError("local_training", "remote_call_handle_mismatch")
            value["remote"]["call_handle"] = handle.to_dict()
            self.write(value)


def validate_remote_journal(value: dict[str, Any]) -> None:
    remote = value.get("remote")
    if value.get("worker_isolation") != REMOTE_ISOLATION or not isinstance(remote, dict):
        raise ValueError("remote_journal")
    target = ConfiguredTrainingTarget.from_dict(remote["target"])
    if (
        target.placement_id != value["request"]["placement_id"]
        or target.recipe_id != value["recipe"]
    ):
        raise ValueError("remote_target")
    deadline = remote.get("deadline_at")
    if deadline is not None:
        before = remote.get("elapsed_before_attempt")
        if (
            type(deadline) not in {int, float}
            or not math.isfinite(deadline)
            or type(before) not in {int, float}
            or not math.isfinite(before)
            or before < 0
            or deadline
            != value["attempt_started_at"]
            + max(0.0, effective_training_limits(value)["wall_seconds"] - before)
            or remote.get("budget_phase") not in {"execution", "cleanup"}
        ):
            raise ValueError("remote_original_deadline")
    elif remote.get("prepared") is not None:
        raise ValueError("remote_original_deadline_required")
    no_effect = remote.get("not_started_receipt")
    if no_effect is not None and (
        not isinstance(no_effect, dict)
        or set(no_effect) != {"prepared_id", "stage", "code"}
        or no_effect["prepared_id"] != remote.get("prepared_id")
        or no_effect["stage"] not in {"prepare", "submit"}
        or no_effect["code"]
        not in {"owner_control_withheld", "submission_window_exhausted", "attempt_deadline"}
    ):
        raise ValueError("remote_no_effect_receipt")
    control = remote.get("control_observation")
    if control is not None:
        digest(control["control_id"], "local_training.remote_control", length=32)
        if (
            control.get("operation_id") != value["operation_id"]
            or control.get("attempt_id") != value["attempt_id"]
            or control.get("allowance_seconds") != target.cleanup_seconds
            or control.get("training_budget_extended") is not False
            or control.get("automatic_retry") is not False
            or control.get("state") not in {"pending", "completed", "unknown"}
            or type(control.get("started_at")) not in {int, float}
            or not math.isfinite(control["started_at"])
            or control["started_at"] <= 0
        ):
            raise ValueError("remote_explicit_control_budget")
    for key in ("prepare_claimed", "submit_claimed", "provider_terminal"):
        if type(remote.get(key)) is not bool:
            raise ValueError("remote_phase")
    if remote.get("prepared") is None and (
        any(remote[key] for key in ("prepare_claimed", "submit_claimed", "provider_terminal"))
        or remote.get("app_ref") is not None
        or remote.get("call_handle") is not None
    ):
        raise ValueError("remote_claim_without_grant")
    if remote.get("prepared") is not None:
        prepared = PreparedTrainingAttempt.from_dict(remote["prepared"])
        if (
            prepared.identity != remote.get("prepared_id")
            or prepared.operation_id != value["operation_id"]
            or prepared.attempt_id != value["attempt_id"]
            or prepared.run_id != value.get("run_id")
            or prepared.training_input_id != value.get("input_id")
            or prepared.producer != target.producer
            or prepared.runtime != target.runtime
            or prepared.placement != target.placement
            or prepared.worker_capability != target.worker_capability
            or prepared.code_closure_sha256 != target.code_closure_sha256
            or prepared.limits != target.limits
            or value["attempt_producer"] != target.producer.to_dict()
            or remote.get("application_request_sha256")
            != hashlib.sha256(json_bytes(value["request"])).hexdigest()
        ):
            raise ValueError("remote_prepared")
        if remote.get("app_ref") is not None:
            ref = TrainingAppRef.from_dict(remote["app_ref"])
            ref.bind(prepared)
        if remote.get("call_handle") is not None:
            handle = TrainingCallHandle.from_dict(remote["call_handle"])
            handle.app_ref.bind(prepared)
            if handle.app_ref.to_dict() != remote.get("app_ref"):
                raise ValueError("remote_handle")
    expected_cost = target.limits.max_cost_microusd if remote["prepare_claimed"] else 0
    for prior in value["attempts"]:
        if not isinstance(prior.get("remote"), dict):
            raise ValueError("remote_attempt_history")
        prior_spec = PreparedTrainingAttempt.from_dict(prior["remote"]["prepared"])
        if (
            prior_spec.operation_id != value["operation_id"]
            or prior_spec.attempt_id != prior["attempt_id"]
            or prior_spec.run_id != value.get("run_id")
            or prior_spec.training_input_id != value.get("input_id")
            or prior_spec.producer.to_dict() != prior.get("attempt_producer")
            or not prior["writer_terminal"]
            or not remote_terminal_proven(prior["remote"])
        ):
            raise ValueError("remote_attempt_history_terminal")
        expected_cost += prior_spec.limits.max_cost_microusd
    reserved = value.get("cost_reserved_microusd", 0)
    if (
        type(reserved) is not int
        or reserved != expected_cost
        or not 0 <= reserved <= target.max_operation_cost_microusd
    ):
        raise ValueError("remote_cost_reservation")
    if value["writer_terminal"] and not (
        remote_terminal_proven(remote) or remote_no_effect_proven(remote)
    ):
        raise ValueError("remote_terminal_proof_required")


def remote_terminal_proven(remote: dict[str, Any]) -> bool:
    raw = remote.get("stop_inspection")
    if not remote.get("provider_terminal") or not isinstance(raw, dict):
        return False
    try:
        proof = StopInspection.from_dict(raw)
        return proof.confirmed and proof.app_ref.to_dict() == remote.get("app_ref")
    except (BoundaryError, ValueError, TypeError):
        return False


def _private_bytes(path: Path, raw: bytes) -> str:
    if path.is_symlink():
        raise BoundaryError("local_training", "remote_quarantine_invalid")
    sha = hashlib.sha256(raw).hexdigest()
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if _bounded_bytes(path, len(raw)) != raw:
            raise BoundaryError("local_training", "remote_quarantine_mismatch") from None
        return sha
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    if os.name != "nt":
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return sha


def _bounded_bytes(path: Path, maximum: int) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
        raise BoundaryError("local_training", "remote_quarantine_invalid")
    with path.open("rb") as handle:
        raw = handle.read(maximum + 1)
    if len(raw) != info.st_size:
        raise BoundaryError("local_training", "remote_quarantine_changed")
    return raw


def _quarantine_path(path: Path, attempt_id: str, role: str) -> Path:
    return path.parent / ("local-training-" + attempt_id + ".remote-" + role)


def execute_remote(
    service: Any,
    path: Path,
    identity: str,
    owner: Any,
    store: Any,
    binding: RemoteTrainingBinding,
    adapter: Any,
    mark_started: Any,
) -> None:
    port = ApplicationTrainingAttemptOwner(
        service, path, owner, service._read(path, owner.identity)
    )
    operation = port.current()
    try:
        port.assert_preparation_budget()
    except BoundaryError as error:
        _finish_no_effect(port, error.code)
        return
    # Trusted preparation checks this same owner fence at input/write boundaries.
    try:
        prepared_workload = adapter.prepare_remote(
            service, path, identity, owner, store, binding.target, budget=port
        )
    except BoundaryError as error:
        if error.code not in {
            "remote_preparation_control_withheld",
            "remote_preparation_budget_exhausted",
        }:
            raise
        _finish_no_effect(port, error.code)
        return
    if not isinstance(prepared_workload, RemotePreparedWorkload):
        raise BoundaryError("local_training", "typed_remote_workload_required")
    attempt = prepared_workload.prepared
    operation = port.current()
    target = binding.target
    if (
        attempt.operation_id != identity
        or attempt.attempt_id != operation["attempt_id"]
        or attempt.run_id != operation.get("run_id")
        or attempt.training_input_id != operation.get("input_id")
        or attempt.producer != target.producer
        or attempt.runtime != target.runtime
        or attempt.placement != target.placement
        or attempt.worker_capability != target.worker_capability
        or attempt.code_closure_sha256 != target.code_closure_sha256
        or attempt.limits != target.limits
    ):
        raise BoundaryError("local_training", "remote_grant_binding_mismatch")
    attempt.bind_payloads(prepared_workload.request_bytes, prepared_workload.projection_bytes)
    decode_projection(prepared_workload.projection_bytes, attempt)
    with service._lock:
        operation = port.current()
        reserved_bytes = (
            operation.get("artifact_reserved_bytes", 0)
            + len(prepared_workload.request_bytes)
            + len(prepared_workload.projection_bytes)
            + attempt.limits.max_candidate_bytes
        )
        if reserved_bytes > effective_training_limits(operation)["scratch_bytes"]:
            raise BoundaryError("local_training", "cumulative_budget_exhausted")
        operation["artifact_reserved_bytes"] = reserved_bytes
        operation["remote"].update(
            prepared=attempt.to_dict(),
            prepared_id=attempt.identity,
            application_request_sha256=hashlib.sha256(json_bytes(operation["request"])).hexdigest(),
        )
        port.write(operation)
    for role, raw in (
        ("request", prepared_workload.request_bytes),
        ("projection", prepared_workload.projection_bytes),
    ):
        _private_bytes(_quarantine_path(path, attempt.attempt_id, role), raw)
    executor = binding.executor_factory(port)
    try:
        port.assert_preparation_budget()
    except BoundaryError as error:
        _finish_no_effect(port, error.code)
        return
    mark_started()
    ref = executor.prepare(attempt)
    if isinstance(ref, NotStarted):
        finish_not_started(port, executor, attempt, ref)
        return
    if isinstance(ref, Unknown):
        finalize_remote(service, path, owner, store, binding, adapter, executor, port, attempt, ref)
        return
    if port.remaining_seconds(attempt.identity) < (
        attempt.limits.wall_seconds + attempt.limits.startup_seconds
    ):
        finish_not_started(
            port,
            executor,
            attempt,
            NotStarted(attempt.identity, "submit", "submission_window_exhausted"),
        )
        return
    handle = executor.submit_once(
        attempt, prepared_workload.request_bytes, prepared_workload.projection_bytes, ref
    )
    if isinstance(handle, NotStarted):
        finish_not_started(port, executor, attempt, handle)
        return
    if isinstance(handle, Unknown):
        finalize_remote(
            service, path, owner, store, binding, adapter, executor, port, attempt, handle)
        return
    while True:
        current = port.current()
        if current["requested_action"] == "cancel":
            port.begin_cleanup()
            if port.remaining_seconds(attempt.identity) <= 0:
                _unknown(port, "remote_owner_deadline")
                return
            executor.cancel(attempt, handle)
            observation = executor.reconcile(attempt, ref, handle)
            break
        if port.remaining_seconds(attempt.identity) <= 0:
            observation = Unknown(attempt.identity, "remote_owner_deadline", ref, handle)
            break
        observation = executor.poll(attempt, handle)
        if observation is not None:
            break
        time.sleep(min(0.1, port.remaining_seconds(attempt.identity)))
    finalize_remote(
        service, path, owner, store, binding, adapter, executor, port, attempt, observation
    )


def _unknown(port: ApplicationTrainingAttemptOwner, code: str) -> None:
    with port.service._lock:
        value = port.current()
        _account_remote_elapsed(port, value)
        value.update(
            status="interrupted_unknown",
            writer_terminal=False,
            error_code=code,
            domain_completion_state="unknown",
        )
        port.write(value)


def finalize_remote(
    service: Any,
    path: Path,
    owner: Any,
    store: Any,
    binding: RemoteTrainingBinding,
    adapter: Any,
    executor: TrainingExecutor,
    port: ApplicationTrainingAttemptOwner,
    attempt: PreparedTrainingAttempt,
    observation: Any,
) -> None:
    ref = port.load_app_ref(attempt.identity)
    if isinstance(observation, CandidateResult):
        if (
            observation.handle != port.load_call_handle(attempt.identity)
            or observation.runtime != attempt.runtime
        ):
            raise BoundaryError("local_training", "remote_candidate_binding_mismatch")
        counts = object_fields(
            observation.counters.value(), {"optimizer_updates", "boundaries"}, "local_training"
        )
        if (
            observation.finish_reason not in {"slice_boundary", "workload_complete"}
            or any(
                type(counts[key]) is not int or not 0 <= counts[key] <= limit
                for key, limit in (
                    ("optimizer_updates", attempt.limits.max_optimizer_updates),
                    ("boundaries", attempt.limits.max_boundaries),
                )
            )
            or counts["optimizer_updates"] > counts["boundaries"]
        ):
            raise BoundaryError("local_training", "remote_candidate_counters_invalid")
        decode_candidate(observation.candidate_bytes, attempt)
        sha = _private_bytes(
            _quarantine_path(path, attempt.attempt_id, "candidate"), observation.candidate_bytes
        )
        with service._lock:
            value = port.current()
            value["remote"].update(
                provider_terminal=True,
                candidate_sha256=sha,
                candidate_finish_reason=observation.finish_reason,
                candidate_runtime=observation.runtime.value(),
                candidate_counters=observation.counters.value(),
            )
            port.write(value)
    elif isinstance(observation, KnownFailure):
        if (
            observation.prepared_id != attempt.identity
            or observation.handle is None
            or observation.handle != port.load_call_handle(attempt.identity)
        ):
            raise BoundaryError("local_training", "remote_failure_binding_mismatch")
        with service._lock:
            value = port.current()
            value["remote"].update(provider_terminal=True, provider_failure=observation.code)
            port.write(value)
    elif isinstance(observation, Unknown):
        _unknown(port, observation.code)
    elif observation is None:
        _unknown(port, "remote_call_still_running")
    if ref is None:
        _unknown(port, "remote_app_ref_required")
        return
    port.begin_cleanup()
    if port.remaining_seconds(attempt.identity) <= 0:
        _unknown(port, "remote_cleanup_budget_exhausted")
        return
    inspection = executor.stop_and_inspect(attempt, ref)
    if inspection.app_ref != ref:
        raise BoundaryError("local_training", "remote_stop_binding_mismatch")
    with service._lock:
        value = port.current()
        value["remote"]["stop_inspection"] = inspection.to_dict()
        _account_remote_elapsed(port, value)
        if not remote_terminal_proven(value["remote"]):
            value.update(
                status="interrupted_unknown",
                writer_terminal=False,
                error_code="remote_terminal_reconciliation_required",
            )
            port.write(value)
            return
        value["writer_terminal"] = True
        port.write(value)
        if value["remote"].get("provider_failure"):
            value.update(
                status="cancelled" if value["requested_action"] == "cancel" else "failed",
                error_code=value["remote"]["provider_failure"],
            )
            port.write(value)
            return
        raw = _bounded_bytes(
            _quarantine_path(path, attempt.attempt_id, "candidate"),
            attempt.limits.max_candidate_bytes,
        )
        if hashlib.sha256(raw).hexdigest() != value["remote"].get("candidate_sha256"):
            raise BoundaryError("local_training", "remote_quarantine_mismatch")
        saved = value["remote"]
        candidate = CandidateResult(
            port.load_call_handle(attempt.identity),
            raw,
            saved["candidate_finish_reason"],
            FrozenObject.of(saved["candidate_runtime"]),
            FrozenObject.of(saved["candidate_counters"]),
        )
        # The trusted domain owner validates/imports the delta through normal Store APIs.
        workload = RemotePreparedWorkload(
            attempt,
            _bounded_bytes(
                _quarantine_path(path, attempt.attempt_id, "request"),
                attempt.limits.max_request_bytes,
            ),
            _bounded_bytes(
                _quarantine_path(path, attempt.attempt_id, "projection"),
                attempt.limits.max_projection_bytes,
            ),
        )
        attempt.bind_payloads(workload.request_bytes, workload.projection_bytes)
        prior_counters = deepcopy(value.get("accepted_counters"))
        prior_updates = value.get("progress", {}).get("completed", 0)
        if port.remaining_seconds(attempt.identity) <= 0:
            value.update(
                status="interrupted_unknown",
                selected_result=False,
                error_code="remote_candidate_validation_budget_exhausted",
            )
            _account_remote_elapsed(port, value)
            port.write(value)
            return
        terminal = adapter.accept_remote_candidate(store, value, candidate, workload, budget=port)
        if port.remaining_seconds(attempt.identity) <= 0:
            value.update(
                status="interrupted_unknown",
                selected_result=False,
                error_code="remote_candidate_validation_budget_exhausted",
            )
            _account_remote_elapsed(port, value)
            port.write(value)
            return
        if (
            not isinstance(terminal, dict)
            or terminal.get("state") not in {"completed", "paused"}
            or terminal.get("run_id") != attempt.run_id
            or terminal.get("attempt_id") != attempt.attempt_id
        ):
            raise BoundaryError("local_training", "remote_domain_terminal_invalid")
        updates_count = terminal.get("optimizer_updates")
        if (
            type(updates_count) is not int
            or not 0 <= updates_count <= 10000
            or updates_count < value.get("progress", {}).get("completed", 0)
        ):
            raise BoundaryError("local_training", "remote_domain_progress_invalid")
        digest(terminal.get("checkpoint_id"), "local_training.remote_checkpoint")
        if terminal["state"] == "completed":
            for name in ("result_id", "model_id"):
                digest(terminal.get(name), "local_training.remote_" + name)
        accepted = terminal.get("accepted_counters")
        stamp = time.time()
        if accepted is not None:
            accepted = checked_training_counters(accepted)
            if (
                accepted["optimizer_updates"] != updates_count
                or prior_counters is not None
                and any(accepted[key] < prior_counters[key] for key in accepted)
            ):
                raise BoundaryError("local_training", "remote_domain_counters_invalid")
            value["accepted_counters"] = accepted
            if any(accepted[key] > (prior_counters or {}).get(key, 0) for key in accepted):
                value["last_progress_at"] = stamp
        elif updates_count > prior_updates:
            value["last_progress_at"] = stamp
        checkpoint_progress = terminal.get("checkpoint_progress")
        if checkpoint_progress is not None:
            checkpoint_progress = dict(
                object_fields(
                    checkpoint_progress,
                    {"checkpoint_id", "optimizer_updates", "boundaries", "epoch"},
                    "local_training",
                )
            )
            if (
                checkpoint_progress["checkpoint_id"] != terminal["checkpoint_id"]
                or checkpoint_progress["optimizer_updates"] != updates_count
                or any(
                    type(checkpoint_progress[name]) is not int or checkpoint_progress[name] < 0
                    for name in ("optimizer_updates", "boundaries", "epoch")
                )
            ):
                raise BoundaryError("local_training", "remote_checkpoint_progress_invalid")
            value["checkpoint_progress"] = checkpoint_progress
        value["last_checkpoint_at"] = stamp
        saved.update(accepted=True, accepted_at=stamp, accepted_runtime=candidate.runtime.value())
        updates = {
            name: terminal[name]
            for name in ("checkpoint_id", "result_id", "model_id")
            if terminal.get(name) is not None
        }
        state = "cancelled" if value["requested_action"] == "cancel" else terminal["state"]
        if value["requested_action"] == "pause" and state == "completed":
            state = "paused"
        _account_remote_elapsed(port, value)
        saved["elapsed_accounted"] = True
        value.update(
            updates,
            status=state,
            stage="completed" if state == "completed" else "training",
            selected_result=state == "completed",
            writer_terminal=True,
            domain_completion_state=(
                "completed" if terminal["state"] == "completed" else "not_completed"
            ),
            progress={
                "unit": "optimizer_update",
                "completed": terminal["optimizer_updates"],
                "total": None,
            },
        )
        value.pop("error_code", None)
        port.write(value)


def saved_remote_candidate(
    path: Path, operation: dict[str, Any], attempt: PreparedTrainingAttempt
) -> CandidateResult | KnownFailure | None:
    """Re-use original quarantined terminal bytes after a failed stop or owner restart."""
    remote = operation["remote"]
    handle = (
        TrainingCallHandle.from_dict(remote["call_handle"])
        if remote.get("call_handle") is not None
        else None
    )
    if not remote.get("provider_terminal") or handle is None:
        return None
    if remote.get("provider_failure"):
        return KnownFailure(attempt.identity, remote["provider_failure"], handle)
    if not remote.get("candidate_sha256"):
        return None
    candidate_path = _quarantine_path(path, attempt.attempt_id, "candidate")
    if candidate_path.is_symlink():
        raise BoundaryError("local_training", "remote_quarantine_invalid")
    raw = _bounded_bytes(candidate_path, attempt.limits.max_candidate_bytes)
    if hashlib.sha256(raw).hexdigest() != remote["candidate_sha256"]:
        raise BoundaryError("local_training", "remote_quarantine_mismatch")
    return CandidateResult(
        handle,
        raw,
        remote["candidate_finish_reason"],
        FrozenObject.of(remote["candidate_runtime"]),
        FrozenObject.of(remote["candidate_counters"]),
    )


def remote_monitor_snapshot(
    operation: dict[str, Any], *, readiness: str = "configured_requires_exact_runtime_receipt"
) -> dict[str, Any]:
    """Read-only durable projection; polling time is never a progress heartbeat."""
    remote = operation["remote"]
    target = ConfiguredTrainingTarget.from_dict(remote["target"])
    ref = remote.get("app_ref") or {}
    handle = remote.get("call_handle") or {}
    stop = remote.get("stop_inspection")
    stopped = remote_terminal_proven(remote)
    if remote_no_effect_proven(remote):
        phase = "not_started"
    elif remote.get("not_started"):
        phase = "not_started_cleanup_pending"
    elif remote.get("accepted"):
        phase = "accepted"
    elif remote.get("provider_failure") and stopped:
        phase = "provider_failed"
    elif stopped and remote.get("candidate_sha256"):
        phase = "candidate_validation_pending"
    elif stop is not None:
        inspection = StopInspection.from_dict(stop)
        phase = (
            "stopped_call_unknown"
            if inspection.confirmed and not remote["provider_terminal"]
            else "stop_unconfirmed"
        )
    elif remote["provider_terminal"]:
        phase = "terminal_observed"
    elif remote.get("call_handle") is not None:
        phase = "call_bound"
    elif remote["submit_claimed"]:
        phase = "submission_claimed"
    elif remote.get("app_ref") is not None:
        phase = "app_bound"
    elif remote["prepare_claimed"]:
        phase = "preparation_claimed"
    elif remote.get("prepared") is not None:
        phase = "grant_persisted"
    else:
        phase = "not_prepared"
    return {
        "configured_target": target.to_dict(),
        "readiness": readiness,
        "qualification": {
            "state": "not_established_by_configuration",
            "receipt_ref": target.qualification_id,
        },
        "expected_device": target.placement.device,
        "configured_runtime": target.runtime.value(),
        "observed_runtime": deepcopy(remote.get("accepted_runtime")),
        "runtime_observation_state": "accepted_candidate"
        if remote.get("accepted_runtime")
        else "not_observed",
        "provider_phase": phase,
        "original_deadline_at": remote.get("deadline_at"),
        "cleanup_reserved_seconds": target.cleanup_seconds,
        "budget_phase": remote.get("budget_phase"),
        "control_observation": deepcopy(remote.get("control_observation")),
        "provider_refs": {
            "prepared_id": remote.get("prepared_id"),
            "app_id": ref.get("app_id"),
            "function_id": ref.get("function_id"),
            "call_id": handle.get("call_id"),
        },
        "provider_terminal": remote["provider_terminal"],
        "stop_inspection": deepcopy(stop),
        "slice_bounds": {
            "max_optimizer_updates": target.limits.max_optimizer_updates,
            "max_boundaries": target.limits.max_boundaries,
            "wall_seconds": target.limits.wall_seconds,
            "startup_seconds": target.limits.startup_seconds,
        },
        "within_slice_progress": {
            "state": "not_running" if operation["writer_terminal"] else "unknown",
            "optimizer_updates": None,
            "boundaries": None,
        },
        "cost_reserved_microusd": operation.get("cost_reserved_microusd", 0),
        "cost": {
            "reserved_maximum_microusd": operation.get("cost_reserved_microusd", 0),
            "cumulative_cap_microusd": target.max_operation_cost_microusd,
            "metered_microusd": None,
            "meter_state": "unknown",
            "authorization_ref": target.limits.cost_authorization_id,
        },
        "pause_boundary": "end_of_bounded_slice_with_verified_checkpoint",
        "automatic_retry": False,
    }


def remote_no_effect_proven(remote: dict[str, Any]) -> bool:
    """Trusted executor proved no call was offered; App cleanup is still distinct."""
    if not remote.get("not_started") or remote.get("call_handle") is not None:
        return False
    receipt = remote.get("not_started_receipt")
    if receipt is None:
        return (
            not remote["prepare_claimed"]
            and not remote["submit_claimed"]
            and remote.get("app_ref") is None
        )
    if (
        receipt.get("prepared_id") != remote.get("prepared_id")
        or receipt.get("stage") not in {"prepare", "submit"}
        or receipt.get("code")
        not in {"owner_control_withheld", "submission_window_exhausted", "attempt_deadline"}
    ):
        return False
    if receipt["stage"] == "prepare":
        return remote.get("app_ref") is None and not remote["submit_claimed"]
    if remote.get("app_ref") is None:
        return False
    try:
        inspection = StopInspection.from_dict(remote.get("stop_inspection"))
        return inspection.confirmed and inspection.app_ref.to_dict() == remote["app_ref"]
    except (BoundaryError, ValueError, TypeError):
        return False


def _account_remote_elapsed(port: ApplicationTrainingAttemptOwner, value: dict[str, Any]) -> None:
    remote = value["remote"]
    before = remote.get("elapsed_before_attempt", value["elapsed_seconds"])
    if (
        port.service._active_attempt == port.attempt_id
        and port.service._attempt_started_monotonic is not None
    ):
        elapsed = time.monotonic() - port.service._attempt_started_monotonic
        original_window = (
            remote.get("deadline_at", value["attempt_started_at"]) - value["attempt_started_at"]
        )
        if port.service._attempt_deadline_monotonic is not None:
            elapsed += max(
                0.0,
                original_window
                - (
                    port.service._attempt_deadline_monotonic
                    - port.service._attempt_started_monotonic
                ),
            )
    elif remote.get("elapsed_accounted"):
        return
    else:
        elapsed = time.time() - value["attempt_started_at"]
    if elapsed < 0:
        raise BoundaryError("local_training", "clock_recovery_required")
    value["elapsed_seconds"] = max(value["elapsed_seconds"], before + elapsed)


def _finish_no_effect(port: ApplicationTrainingAttemptOwner, code: str) -> None:
    with port.service._lock:
        value = port.current()
        value["remote"]["not_started"] = True
        _account_remote_elapsed(port, value)
        action = value["requested_action"]
        value.update(
            status="paused"
            if action == "pause"
            else "cancelled"
            if action == "cancel"
            else "failed",
            writer_terminal=remote_no_effect_proven(value["remote"]),
            selected_result=False,
            domain_completion_state="not_completed",
            error_code=code,
        )
        if not value["writer_terminal"]:
            value["status"] = "interrupted_unknown"
        else:
            _seal_no_effect_receipt(port, value, code)
        port.write(value)


def finish_not_started(
    port: ApplicationTrainingAttemptOwner,
    executor: TrainingExecutor,
    attempt: PreparedTrainingAttempt,
    observation: NotStarted,
) -> None:
    if observation.prepared_id != attempt.identity:
        raise BoundaryError("local_training", "remote_no_effect_binding_mismatch")
    with port.service._lock:
        value = port.current()
        value["remote"].update(
            not_started=True,
            not_started_receipt={
                "prepared_id": observation.prepared_id,
                "stage": observation.stage,
                "code": observation.code,
            },
        )
        port.write(value)
    if observation.stage == "submit":
        ref = port.load_app_ref(attempt.identity)
        if ref is None:
            _unknown(port, "remote_app_ref_required")
            return
        port.begin_cleanup()
        if port.remaining_seconds(attempt.identity) <= 0:
            _unknown(port, "remote_cleanup_budget_exhausted")
            return
        proof = executor.stop_and_inspect(attempt, ref)
        if proof.app_ref != ref:
            raise BoundaryError("local_training", "remote_stop_binding_mismatch")
        with port.service._lock:
            value = port.current()
            value["remote"]["stop_inspection"] = proof.to_dict()
            port.write(value)
    _finish_no_effect(port, observation.code)


NO_EFFECT_RECEIPT_SCHEMA = "spireagent/training-not-started-receipt-v1"


def _seal_no_effect_receipt(
    port: ApplicationTrainingAttemptOwner, operation: dict[str, Any], code: str
) -> None:
    remote = operation["remote"]
    outcome = remote.get("not_started_receipt")
    record = {
        "schema": NO_EFFECT_RECEIPT_SCHEMA,
        "operation_id": port.operation_id,
        "attempt_id": port.attempt_id,
        "prepared_id": remote.get("prepared_id"),
        "application_request_sha256": hashlib.sha256(json_bytes(operation["request"])).hexdigest(),
        "stage": outcome["stage"] if outcome else "prepare",
        "code": code,
        "observed_at": time.time(),
        "no_remote_call_offered": True,
        "app_ref": deepcopy(remote.get("app_ref")),
        "stop_inspection": deepcopy(remote.get("stop_inspection")),
        "original_deadline_at": remote.get("deadline_at"),
        "cost_reserved_microusd": operation.get("cost_reserved_microusd", 0),
    }
    raw = json_bytes(record)
    sha = hashlib.sha256(raw).hexdigest()
    _private_bytes(_quarantine_path(port.path, port.attempt_id, "no-effect-" + sha), raw)
    remote["no_effect_receipt"] = {"sha256": sha, "size": len(raw), "record": record}


def retireable_no_effect(path: Path, operation: dict[str, Any]) -> bool:
    """One exact released no-call predecessor; this grants no retry of its Run."""
    remote = operation.get("remote")
    if (
        operation.get("schema") != REMOTE_OPERATION_SCHEMA
        or operation.get("status") not in {"paused", "cancelled", "failed"}
        or operation.get("writer_terminal") is not True
        or not isinstance(remote, dict)
        or not remote_no_effect_proven(remote)
        or any(
            operation.get(name) is not None
            for name in ("checkpoint_id", "result_id", "model_id", "evaluation_id")
        )
        or operation.get("progress", {}).get("completed", 0) != 0
        or any((operation.get("accepted_counters") or {}).values())
        or remote.get("candidate_sha256") is not None
        or remote.get("accepted")
        or remote.get("provider_terminal")
    ):
        return False
    pointer = remote.get("no_effect_receipt")
    if not isinstance(pointer, dict) or set(pointer) != {"sha256", "size", "record"}:
        return False
    record = pointer["record"]
    if (
        not isinstance(record, dict)
        or record.get("schema") != NO_EFFECT_RECEIPT_SCHEMA
        or record.get("operation_id") != operation["operation_id"]
        or record.get("attempt_id") != operation["attempt_id"]
        or record.get("prepared_id") != remote.get("prepared_id")
        or record.get("application_request_sha256")
        != hashlib.sha256(json_bytes(operation["request"])).hexdigest()
        or record.get("no_remote_call_offered") is not True
        or record.get("app_ref") != remote.get("app_ref")
        or record.get("stop_inspection") != remote.get("stop_inspection")
        or record.get("original_deadline_at") != remote.get("deadline_at")
        or record.get("cost_reserved_microusd") != operation.get("cost_reserved_microusd", 0)
    ):
        return False
    raw = json_bytes(record)
    sha = hashlib.sha256(raw).hexdigest()
    if pointer["sha256"] != sha or pointer["size"] != len(raw):
        return False
    try:
        return (
            _bounded_bytes(
                _quarantine_path(path, operation["attempt_id"], "no-effect-" + sha), 64 * 1024
            )
            == raw
        )
    except (OSError, BoundaryError):
        return False


def archive_no_effect_predecessor(path: Path, operation: dict[str, Any]) -> dict[str, Any]:
    if not retireable_no_effect(path, operation):
        raise BoundaryError("local_training", "known_no_effect_receipt_required")
    from spireagent.storage.replaceable_file import read_replaceable_bytes

    raw = read_replaceable_bytes(path)
    sha = _private_bytes(
        _quarantine_path(path, operation["attempt_id"], "operation-" + operation["operation_id"]),
        raw,
    )
    return {
        "schema": "spireagent/known-not-started-predecessor-v1",
        "operation_id": operation["operation_id"],
        "attempt_id": operation["attempt_id"],
        "dataset_id": operation["dataset_id"],
        "run_id": operation.get("run_id"),
        "input_id": operation.get("input_id"),
        "archive_sha256": sha,
        "archive_size": len(raw),
        "no_effect_receipt_sha256": operation["remote"]["no_effect_receipt"]["sha256"],
    }
