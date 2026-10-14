"""Modal placement over the application's durable original-attempt port.

No provider-owned operation journal or result selection. A restart can only
resolve the original prepared App/handle after an intent has been consumed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Protocol

from spireagent.json_boundary import BoundaryError
from spireagent.training_execution_contracts import (
    CandidateResult,
    KnownFailure,
    NotStarted,
    PreparedTrainingAttempt,
    StopInspection,
    TrainingAppRef,
    TrainingAttemptOwnerPort,
    TrainingCallHandle,
    TrainingClaim,
    TrainingObservation,
    TrainingProviderBudget,
    Unknown,
)
from spireagent.training_transport import decode_projection, decode_response

MODAL_SDK_VERSION = "1.5.5"
_STAGE = "modal_training_executor"


class ModalTrainingBackend(Protocol):
    """Provider effects only. Durable intent and result meaning belong elsewhere."""

    def prepare(
        self, attempt: PreparedTrainingAttempt, *, budget: TrainingProviderBudget
    ) -> TrainingAppRef | NotStarted: ...
    def resolve(
        self, attempt: PreparedTrainingAttempt, *, budget: TrainingProviderBudget
    ) -> TrainingAppRef | None: ...
    def submit(
        self,
        attempt: PreparedTrainingAttempt,
        app_ref: TrainingAppRef,
        request: bytes,
        projection: bytes,
        *, budget: TrainingProviderBudget,
    ) -> TrainingCallHandle | NotStarted: ...
    def poll(
        self, attempt: PreparedTrainingAttempt, handle: TrainingCallHandle,
        *, budget: TrainingProviderBudget,
    ) -> bytes | KnownFailure | None: ...
    def recover_calls(
        self, attempt: PreparedTrainingAttempt, app_ref: TrainingAppRef,
        *, budget: TrainingProviderBudget,
    ) -> tuple[TrainingCallHandle, ...] | None: ...
    def cancel(self, handle: TrainingCallHandle, *, budget: TrainingProviderBudget) -> bool: ...
    def stop(self, app_ref: TrainingAppRef, *, budget: TrainingProviderBudget) -> bool: ...
    def inspect_stop(
        self, app_ref: TrainingAppRef, *, budget: TrainingProviderBudget
    ) -> StopInspection: ...


class ModalTrainingExecutor:
    """Ordinary library path has a lazy real backend; fakes are conformance seams."""

    def __init__(
        self, owner: TrainingAttemptOwnerPort, *, backend: ModalTrainingBackend | None = None
    ) -> None:
        self.owner = owner
        self.backend = backend if backend is not None else SdkModalTrainingBackend()

    def _budget(self, attempt: PreparedTrainingAttempt) -> TrainingProviderBudget:
        return TrainingProviderBudget(lambda: self.owner.remaining_seconds(attempt.identity))

    @staticmethod
    def _claim(value: object) -> TrainingClaim:
        if not isinstance(value, TrainingClaim):
            raise BoundaryError(_STAGE, "typed_training_claim_required")
        return value

    @staticmethod
    def _no_effect(attempt: PreparedTrainingAttempt, result: NotStarted, stage: str) -> NotStarted:
        if result.prepared_id != attempt.identity or result.stage != stage:
            raise BoundaryError(_STAGE, "foreign_not_started")
        return result

    def _original_handle(
        self, attempt: PreparedTrainingAttempt, handle: TrainingCallHandle
    ) -> None:
        handle.app_ref.bind(attempt)
        if (
            self.owner.load_app_ref(attempt.identity) != handle.app_ref
            or self.owner.load_call_handle(attempt.identity) != handle
        ):
            raise BoundaryError(_STAGE, "durable_original_call_handle_required")

    def _resolve(self, attempt: PreparedTrainingAttempt) -> TrainingAppRef | Unknown:
        try:
            budget = self._budget(attempt)
            budget.timeout(30)
            ref = self.backend.resolve(attempt, budget=budget)
            if ref is None:
                return Unknown(attempt.identity, "prepare_unknown")
            ref.bind(attempt)
            self.owner.persist_app_ref(attempt, ref)
            budget.timeout(30)
            return ref
        except Exception:
            return Unknown(attempt.identity, "prepare_unknown")

    def prepare(self, attempt: PreparedTrainingAttempt) -> TrainingAppRef | NotStarted | Unknown:
        if not isinstance(attempt, PreparedTrainingAttempt):
            raise BoundaryError(_STAGE, "typed_attempt_required")
        saved = self.owner.load_app_ref(attempt.identity)
        if saved is not None:
            saved.bind(attempt)
            return saved
        claim = self._claim(self.owner.claim_preparation(attempt))
        if claim is TrainingClaim.WITHHELD:
            return NotStarted(attempt.identity, "prepare", "owner_control_withheld")
        if claim is TrainingClaim.PRIOR:
            return self._resolve(attempt)
        budget = self._budget(attempt)
        if budget.remaining() <= 0:
            return NotStarted(attempt.identity, "prepare", "attempt_deadline")
        try:
            ref = self.backend.prepare(attempt, budget=budget)
            if isinstance(ref, NotStarted):
                return self._no_effect(attempt, ref, "prepare")
            ref.bind(attempt)
        except Exception:
            return self._resolve(attempt)
        try:
            self.owner.persist_app_ref(attempt, ref)
        except Exception:
            return Unknown(attempt.identity, "app_ref_persistence_unknown", app_ref=ref)
        if budget.remaining() <= 0:
            return Unknown(attempt.identity, "late_preparation_acknowledgement", app_ref=ref)
        return ref

    def recover_preparation(self, attempt: PreparedTrainingAttempt) -> TrainingAppRef | Unknown:
        """Read-only recovery of a prior prepare intent; never create/deploy again."""
        saved = self.owner.load_app_ref(attempt.identity)
        if saved is not None:
            saved.bind(attempt)
            return saved
        return self._resolve(attempt)

    def submit_once(
        self,
        attempt: PreparedTrainingAttempt,
        request: bytes,
        projection: bytes,
        app_ref: TrainingAppRef,
    ) -> TrainingCallHandle | NotStarted | Unknown:
        app_ref.bind(attempt)
        attempt.bind_payloads(request, projection)
        decode_projection(projection, attempt)
        if self.owner.load_app_ref(attempt.identity) != app_ref:
            raise BoundaryError(_STAGE, "durable_original_app_ref_required")
        saved = self.owner.load_call_handle(attempt.identity)
        if saved is not None:
            saved.app_ref.bind(attempt)
            if saved.app_ref != app_ref:
                raise BoundaryError(_STAGE, "foreign_saved_handle")
            return saved
        claim = self._claim(self.owner.claim_submission_once(attempt, app_ref))
        if claim is TrainingClaim.WITHHELD:
            return NotStarted(attempt.identity, "submit", "owner_control_withheld")
        if claim is TrainingClaim.PRIOR:
            return Unknown(attempt.identity, "submission_unknown", app_ref=app_ref)
        budget = self._budget(attempt)
        try:
            budget.require_submission_window(attempt)
        except BoundaryError as error:
            if error.code != "attempt_deadline":
                raise
            return NotStarted(attempt.identity, "submit", "submission_window_exhausted")
        try:
            handle = self.backend.submit(attempt, app_ref, request, projection, budget=budget)
            if isinstance(handle, NotStarted):
                return self._no_effect(attempt, handle, "submit")
            handle.app_ref.bind(attempt)
            if handle.app_ref != app_ref:
                raise BoundaryError(_STAGE, "foreign_submitted_handle")
        except Exception:
            return Unknown(attempt.identity, "submission_unknown", app_ref=app_ref)
        try:
            self.owner.persist_call_handle(attempt, handle)
        except Exception:
            return Unknown(
                attempt.identity, "handle_persistence_unknown", app_ref=app_ref, handle=handle
            )
        try:
            budget.require_submission_window(attempt)
        except Exception:
            return Unknown(
                attempt.identity, "late_submission_acknowledgement", app_ref=app_ref, handle=handle
            )
        return handle

    def poll(
        self, attempt: PreparedTrainingAttempt, handle: TrainingCallHandle
    ) -> TrainingObservation:
        self._original_handle(attempt, handle)
        try:
            budget = self._budget(attempt)
            budget.timeout(30)
            result = self.backend.poll(attempt, handle, budget=budget)
            budget.timeout(30)
            if result is None:
                return None  # Exact provider call is known-running.
            if isinstance(result, KnownFailure):
                if result.prepared_id != attempt.identity or result.handle != handle:
                    raise BoundaryError(_STAGE, "foreign_failure_receipt")
                return result
            candidate = decode_response(result, attempt, handle)
            if not isinstance(candidate, CandidateResult):
                raise BoundaryError(_STAGE, "typed_candidate_required")
            return candidate
        except Exception:
            return Unknown(
                attempt.identity, "poll_or_candidate_unknown", app_ref=handle.app_ref, handle=handle
            )

    def reconcile(
        self,
        attempt: PreparedTrainingAttempt,
        app_ref: TrainingAppRef,
        handle: TrainingCallHandle | None = None,
    ) -> TrainingObservation:
        app_ref.bind(attempt)
        if self.owner.load_app_ref(attempt.identity) != app_ref:
            raise BoundaryError(_STAGE, "durable_original_app_ref_required")
        selected = handle or self.owner.load_call_handle(attempt.identity)
        if selected is None:
            try:
                recovered = self.backend.recover_calls(
                    attempt, app_ref, budget=self._budget(attempt)
                )
                if recovered is None or len(recovered) != 1:
                    return Unknown(attempt.identity, "original_call_unresolved", app_ref=app_ref)
                selected = recovered[0]
                if selected.app_ref != app_ref:
                    raise BoundaryError(_STAGE, "foreign_recovered_call")
                self.owner.persist_call_handle(attempt, selected)
            except Exception:
                return Unknown(attempt.identity, "original_call_unresolved", app_ref=app_ref)
        if selected.app_ref != app_ref:
            raise BoundaryError(_STAGE, "foreign_reconciliation_handle")
        return self.poll(attempt, selected)

    def cancel(self, attempt: PreparedTrainingAttempt, handle: TrainingCallHandle) -> bool:
        self._original_handle(attempt, handle)
        try:
            budget = self._budget(attempt)
            budget.timeout(30)
            return self.backend.cancel(handle, budget=budget) is True
        except Exception:
            return False  # Acknowledgement is never terminal/stop proof.

    def stop_and_inspect(
        self, attempt: PreparedTrainingAttempt, app_ref: TrainingAppRef
    ) -> StopInspection:
        app_ref.bind(attempt)
        if self.owner.load_app_ref(attempt.identity) != app_ref:
            raise BoundaryError(_STAGE, "durable_original_app_ref_required")
        unknown = StopInspection(app_ref, "unknown", None, None)
        try:
            budget = self._budget(attempt)
            budget.timeout(30)
            self.backend.stop(app_ref, budget=budget)
            # Inspect even when stop acknowledgement is ambiguous; same original wallet.
            result = self.backend.inspect_stop(app_ref, budget=budget)
            budget.timeout(30)
            if result.app_ref != app_ref:
                return unknown
            return result
        except Exception:
            return unknown


class SdkModalTrainingBackend:
    """Exact SDK launch/control; no import, network or deployment at construction."""

    def __init__(self, *, sdk: Any = None, command_runner: Any = None) -> None:
        self._sdk = sdk
        self._runner = command_runner or subprocess.run

    def _client(self) -> Any:
        if self._sdk is None:
            from importlib.metadata import version

            if version("modal") != MODAL_SDK_VERSION:
                raise BoundaryError(_STAGE, "modal_sdk_version_mismatch")
            self._sdk = importlib.import_module("modal")
        return self._sdk

    def _json(self, argv: list[str], *, budget: TrainingProviderBudget) -> Any:
        result = self._runner(
            [str(Path(sys.executable).with_name("modal")), *argv, "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=budget.timeout(30),
        )
        budget.timeout(30)
        if result.returncode:
            raise BoundaryError(_STAGE, "provider_metadata_unavailable")
        return json.loads(result.stdout)

    @staticmethod
    def _tag(attempt: PreparedTrainingAttempt) -> str:
        # SDK1.5.5 tags have a50-character limit; retain all256digest bits.
        return "sha256-" + base64.urlsafe_b64encode(
            hashlib.sha256(attempt.to_bytes()).digest()
        ).decode().rstrip("=")

    def _workspace(self, account_id: str, *, budget: TrainingProviderBudget) -> None:
        result = self._runner(
            [str(Path(sys.executable).with_name("modal")), "token", "info"],
            capture_output=True,
            text=True,
            check=False,
            timeout=budget.timeout(30),
        )
        budget.timeout(30)
        if result.returncode or not isinstance(result.stdout, str):
            raise BoundaryError(_STAGE, "workspace_identity_unavailable")
        ids = re.findall(
            r"^Workspace: .+?\s+\(([A-Za-z0-9][A-Za-z0-9._-]{0,127})\)\s*$", result.stdout, re.M
        )
        if ids != [account_id]:
            raise BoundaryError(_STAGE, "canonical_workspace_mismatch")

    @staticmethod
    def _await(
        factory: Any, budget: TrainingProviderBudget, *, accept_late: bool = False
    ) -> Any:
        """Bound SDK coroutine/RPC work; timeout after a mutation remains unknown."""
        async_utils = importlib.import_module("modal._utils.async_utils")

        async def bounded():
            timeout = budget.timeout(30)
            result = await asyncio.wait_for(factory(), timeout=timeout)
            if not accept_late:
                budget.timeout(30)
            return result

        return async_utils.synchronizer.create_blocking(bounded)()

    def _function_identity(
        self, attempt: PreparedTrainingAttempt, app_id: str, *, budget: TrainingProviderBudget
    ) -> str:
        """Use the observed ranked-function protobuf, not facade fields/defaults."""
        sdk = self._client()
        api = importlib.import_module("modal_proto.api_pb2")
        client = self._await(lambda: sdk.Client.from_env.aio(), budget)
        layout = self._await(
            lambda: client.stub.AppGetLayout(
                api.AppGetLayoutRequest(app_id=app_id),
                retry=None,
                timeout=budget.timeout(30),
            ), budget,
        )
        result = self._await(
            lambda: client.stub.FunctionGet(
                api.FunctionGetRequest(
                    app_name=attempt.app_name,
                    object_tag="training_attempt",
                    environment_name=attempt.placement.environment,
                    app_version=0,
                ),
                retry=None,
                timeout=budget.timeout(30),
            ), budget,
        )
        if not isinstance(layout, api.AppGetLayoutResponse) or not isinstance(
            result, api.FunctionGetResponse
        ):
            raise BoundaryError(_STAGE, "function_definition_unavailable")
        fid = result.function_id
        objects = [obj for obj in layout.app_layout.objects if obj.object_id == fid]
        if (
            dict(layout.app_layout.function_ids) != {"training_attempt": fid}
            or len(objects) != 1
            or not objects[0].HasField("function_handle_metadata")
            or objects[0].function_handle_metadata.app_id != app_id
            or result.handle_metadata.app_id != app_id
            or result.handle_metadata.function_name != "training_attempt"
            or result.handle_metadata.is_method
            or result.handle_metadata.function_type != api.Function.FUNCTION_TYPE_FUNCTION
        ):
            raise BoundaryError(_STAGE, "function_app_binding_mismatch")
        ranked = result.function.ranked_functions
        if len(ranked) != 1 or ranked[0].rank != 0:
            raise BoundaryError(_STAGE, "ranked_function_binding_mismatch")
        definition = ranked[0].function
        resources, scaling = definition.resources, result.function.autoscaler_settings
        expected_gpu_count = 0 if attempt.placement.gpu == "none" else 1
        expected_gpu = "" if expected_gpu_count == 0 else attempt.placement.gpu
        if (
            definition.image_id != attempt.placement.image_object_id
            or definition.function_name != "training_attempt"
            or resources.gpu_config.gpu_type != expected_gpu
            or resources.gpu_config.count != expected_gpu_count
            or resources.milli_cpu != attempt.limits.cpu_millicores
            or resources.milli_cpu_max != attempt.limits.cpu_millicores
            or resources.memory_mb != attempt.limits.memory_mib
            or resources.memory_mb_max != attempt.limits.memory_mib
            or result.function.timeout_secs != attempt.limits.wall_seconds
            or definition.startup_timeout_secs != attempt.limits.startup_seconds
            or scaling.max_containers != 1
            or scaling.min_containers != 0
            or definition.retry_policy.retries != 0
        ):
            raise BoundaryError(_STAGE, "function_resource_or_image_mismatch")
        return fid

    def resolve(
        self, attempt: PreparedTrainingAttempt, *, budget: TrainingProviderBudget
    ) -> TrainingAppRef | None:
        # No latest named Function binding alone: require exact unique App/tag/image.
        self._workspace(attempt.placement.account_id, budget=budget)
        rows = self._json(["app", "list", "--env", attempt.placement.environment], budget=budget)
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or "app_id" not in row or "description" not in row
            for row in rows
        ):
            raise BoundaryError(_STAGE, "provider_app_inventory_unavailable")
        matches = [
            row
            for row in rows
            if isinstance(row, dict) and row.get("description") == attempt.app_name
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise BoundaryError(_STAGE, "ambiguous_original_app")
        app_id = matches[0]["app_id"]
        history = self._json(
            ["app", "history", app_id, "--env", attempt.placement.environment], budget=budget
        )
        if (
            not isinstance(history, list)
            or len(history) != 1
            or not isinstance(history[0], dict)
            or str(history[0].get("version")) != "v1"
            or history[0].get("tag") != self._tag(attempt)
        ):
            raise BoundaryError(_STAGE, "attempt_redeployed_or_tag_mismatch")
        fid = self._function_identity(attempt, app_id, budget=budget)
        self._workspace(attempt.placement.account_id, budget=budget)
        return TrainingAppRef(
            attempt.identity,
            app_id,
            fid,
            attempt.app_name,
            attempt.placement.account_id,
            attempt.placement.environment,
            attempt.placement.image_object_id,
        )

    def prepare(
        self, attempt: PreparedTrainingAttempt, *, budget: TrainingProviderBudget
    ) -> TrainingAppRef | NotStarted:
        from spireagent.source import source_identity

        root = Path(__file__).resolve().parents[1]
        # Shared source identity performs local read-only Git probes without hard
        # preemption. It may overrun the wallet; recheck before any provider call.
        try:
            if source_identity(root) != attempt.producer:
                raise BoundaryError(_STAGE, "deployment_source_lock_mismatch")
            budget.timeout(30)
            self._workspace(attempt.placement.account_id, budget=budget)
        except BoundaryError as error:
            if error.code != "attempt_deadline":
                raise
            return NotStarted(attempt.identity, "prepare", "attempt_deadline")
        env = dict(os.environ)
        env["SPIREAGENT_PREPARED_TRAINING_ATTEMPT"] = attempt.to_bytes().decode()
        entry = root / "deploy/cloud-worker/training_attempt_modal.py"
        try:
            timeout = budget.timeout(120)
        except BoundaryError as error:
            if error.code != "attempt_deadline":
                raise
            return NotStarted(attempt.identity, "prepare", "attempt_deadline")
        result = self._runner(
            [
                str(Path(sys.executable).with_name("modal")),
                "deploy",
                str(entry),
                "--env",
                attempt.placement.environment,
                "--tag",
                self._tag(attempt),
            ],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        # An error/timeout can occur after provider acceptance. Caller will resolve.
        if result.returncode:
            raise BoundaryError(_STAGE, "prepare_unknown")
        ref = self.resolve(attempt, budget=budget)
        if ref is None:
            raise BoundaryError(_STAGE, "prepare_unknown")
        return ref

    def submit(
        self,
        attempt: PreparedTrainingAttempt,
        app_ref: TrainingAppRef,
        request: bytes,
        projection: bytes,
        *, budget: TrainingProviderBudget,
    ) -> TrainingCallHandle | NotStarted:
        app_ref.bind(attempt)
        # Every pre-submit lookup and the spawn RPC consumes only the allowance
        # above the complete remote startup/work window. No fixed extra timer.
        try:
            submission = budget.reserving(
                attempt.limits.startup_seconds + attempt.limits.wall_seconds
            )
            if self.resolve(attempt, budget=submission) != app_ref:
                raise BoundaryError(_STAGE, "original_deployment_changed")
            function = self._client().Function.from_name(
                app_ref.app_name,
                "training_attempt",
                environment_name=app_ref.environment,
            )
            self._await(lambda: function.hydrate.aio(), submission)
            if (
                function.object_id != app_ref.function_id
                or function._get_metadata().app_id != app_ref.app_id
            ):
                raise BoundaryError(_STAGE, "original_function_identity_changed")
            submission.timeout(30)
        except BoundaryError as error:
            if error.code != "attempt_deadline":
                raise
            return NotStarted(attempt.identity, "submit", "submission_window_exhausted")
        # Never report NotStarted after entering this potentially accepted mutation.
        call = self._await(
            lambda: function.spawn.aio(attempt.to_bytes(), request, projection), submission,
            accept_late=True,
        )
        return TrainingCallHandle(app_ref, call.object_id)

    def poll(
        self, attempt: PreparedTrainingAttempt, handle: TrainingCallHandle,
        *, budget: TrainingProviderBudget,
    ) -> bytes | KnownFailure | None:
        sdk = self._client()
        try:
            call = sdk.FunctionCall.from_id(handle.call_id)
            return self._await(lambda: call.get.aio(timeout=0), budget)
        except sdk.exception.FunctionTimeoutError:
            return KnownFailure(attempt.identity, "provider_execution_timeout", handle)
        except sdk.exception.TimeoutError:
            return None
        except sdk.exception.ExecutionError:
            return KnownFailure(attempt.identity, "provider_worker_failed", handle)
        except sdk.exception.RemoteError:
            # SDK1.5.5 maps an empty TERMINATED result to RemoteError. Other
            # RemoteErrors (e.g. server or decoding failures) remain unknown.
            # Re-read only the original call's retained index-0 protobuf result,
            # without retries or consuming/deleting outputs, before claiming terminal.
            api = importlib.import_module("modal_proto.api_pb2")
            client = self._await(lambda: sdk.Client.from_env.aio(), budget)
            response = self._await(lambda: client.stub.FunctionGetOutputs(
                api.FunctionGetOutputsRequest(function_call_id=handle.call_id,
                    timeout=0, last_entry_id="0-0", clear_on_success=False,
                    requested_at=time.time(), start_idx=0, end_idx=0),
                retry=None, timeout=budget.timeout(30)), budget)
            if (len(response.outputs) == 1 and response.num_unfinished_inputs == 0
                    and response.outputs[0].idx == 0
                    and response.outputs[0].result.status ==
                    api.GenericResult.GENERIC_STATUS_TERMINATED):
                return KnownFailure(attempt.identity, "provider_call_terminated", handle)
            raise

    def recover_calls(
        self, attempt: PreparedTrainingAttempt, app_ref: TrainingAppRef,
        *, budget: TrainingProviderBudget,
    ) -> tuple[TrainingCallHandle, ...] | None:
        app_ref.bind(attempt)
        budget.timeout(30)
        # SDK1.5.5 does not expose a qualified request-bound call enumeration
        # here. Original App can still be stopped/inspected; never respawn it.
        return None

    def cancel(self, handle: TrainingCallHandle, *, budget: TrainingProviderBudget) -> bool:
        self._workspace(handle.app_ref.account_id, budget=budget)
        call = self._client().FunctionCall.from_id(handle.call_id)
        self._await(lambda: call.cancel.aio(terminate_containers=True), budget)
        return True

    def stop(self, app_ref: TrainingAppRef, *, budget: TrainingProviderBudget) -> bool:
        self._workspace(app_ref.account_id, budget=budget)
        result = self._runner(
            [
                str(Path(sys.executable).with_name("modal")),
                "app",
                "stop",
                app_ref.app_id,
                "--yes",
                "--env",
                app_ref.environment,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=budget.timeout(30),
        )
        budget.timeout(30)
        return result.returncode == 0

    def inspect_stop(
        self, app_ref: TrainingAppRef, *, budget: TrainingProviderBudget
    ) -> StopInspection:
        self._workspace(app_ref.account_id, budget=budget)
        rows = self._json(["app", "list", "--env", app_ref.environment], budget=budget)
        if not isinstance(rows, list):
            return StopInspection(app_ref, "unknown", None, None)
        matches = [
            row for row in rows if isinstance(row, dict) and row.get("app_id") == app_ref.app_id
        ]
        if len(matches) != 1:
            return StopInspection(app_ref, "unknown", None, None)
        row = matches[0]
        if row.get("description") != app_ref.app_name:
            return StopInspection(app_ref, "unknown", None, None)
        tasks = row.get("tasks")
        tasks = int(tasks) if isinstance(tasks, (str, int)) and str(tasks).isdigit() else None
        if row.get("state") != "stopped" or tasks != 0:
            return StopInspection(app_ref, "not_stopped", tasks, None)
        containers = self._json(["container", "list", "--env", app_ref.environment], budget=budget)
        # The provider list must carry app association for every retained row.
        if not isinstance(containers, list) or any(
            not isinstance(entry, dict) or "app_id" not in entry for entry in containers
        ):
            return StopInspection(app_ref, "unknown", tasks, None)
        count = sum(entry["app_id"] == app_ref.app_id for entry in containers)
        return StopInspection(app_ref, "stopped" if count == 0 else "not_stopped", tasks, count)
