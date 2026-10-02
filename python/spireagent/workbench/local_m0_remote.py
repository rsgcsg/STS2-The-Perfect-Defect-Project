"""Owner-bound, journal-first controller for one bounded Modal M0 attempt."""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from spireagent.json_boundary import BoundaryError, digest
from spireagent.source import source_identity
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ROOT
from spireagent.workbench.local_training import (
    REMOTE_MAX_REQUEST_BYTES,
    REMOTE_MAX_TIMEOUT_SECONDS,
    REMOTE_MAX_TOTAL_STEPS,
    LocalTrainingService,
    _read_remote_blob,
    _remote_target_for_attempt_spec,
)
from stpd.cloud_jobs.m0_modal import (
    M0_PILOT_EXECUTION_PLAN,
    M0AttemptSpec,
    M0ExecutionPlan,
    M0PrepareStoppedProof,
    M0StopInspection,
    ModalM0Call,
    ModalM0Provider,
    ModalM0RuntimeEvidence,
    ModalM0Target,
)
from stpd.workers.token_ranking import LightActionM0Config, decode_config
from stpd.workers.token_remote_update import (
    TokenRemoteUpdateRequest,
    TokenRemoteUpdateResult,
    finalize_token_remote_update,
    prepare_token_remote_update,
    publish_token_remote_checkpoint,
)


@dataclass(frozen=True)
class ModalM0Settings:
    account_id: str
    environment_name: str
    image_object_id: str
    poll_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not all(isinstance(item, str) and item for item in (
            self.account_id, self.environment_name, self.image_object_id,
        )):
            raise BoundaryError("local_m0_remote", "modal_settings_required")
        if (isinstance(self.poll_interval_seconds, bool)
                or not isinstance(self.poll_interval_seconds, (int, float))
                or not 0.05 <= self.poll_interval_seconds <= 30):
            raise BoundaryError("local_m0_remote", "poll_interval_limit")


ProviderFactory = Callable[..., ModalM0Provider]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _bounded_wait(value: object) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 <= value <= REMOTE_MAX_TIMEOUT_SECONDS):
        raise BoundaryError("local_m0_remote", "wait_timeout_limit")
    return float(value)


def _stop_inspection(value: M0StopInspection) -> dict[str, Any]:
    return {
        "app_id": value.app_id,
        "app_state": value.app_state,
        "active_tasks": value.active_tasks,
        "active_containers": value.active_containers,
        "confirmed": value.confirmed,
    }


class LocalM0RemoteController:
    """Drive only journaled owner operations; never retries an ambiguous submit."""

    def __init__(
        self,
        training: LocalTrainingService,
        settings: ModalM0Settings | None = None,
        *,
        provider_factory: ProviderFactory = ModalM0Provider,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.training = training
        self.settings = settings
        self.provider_factory = provider_factory
        self._sleep = sleep
        self._monotonic = monotonic

    def _context(self):
        owner, store, registry_path = self.training._selected()
        return owner, store, registry_path

    def _saved(self, operation_id: object) -> tuple[dict[str, Any], bytes]:
        operation_id = digest(operation_id, "local_m0_remote.operation_id", length=32)
        owner, store, _ = self._context()
        path, _lock = self.training._paths(owner)
        current = self.training._read(path, owner.identity)
        if current.get("schema") != "stpd/local-training-operation-v4" or (
            current.get("operation_id") != operation_id
        ):
            raise BoundaryError("local_m0_remote", "remote_operation_not_found")
        latest = current["remote"]["attempts"][-1]
        request_bytes = _read_remote_blob(
            store, latest["request_ref"], latest["request_sha256"],
            object_prefix="local-training/remote-requests/",
            chunk_prefix="local-training/remote-request-chunks/",
            maximum=REMOTE_MAX_REQUEST_BYTES, label="remote_request",
        )
        return current, request_bytes

    @staticmethod
    def _profile_and_dataset(store: Any, run_id: str) -> tuple[str, str, str]:
        run = store.get_manifest(run_id)
        training_input = store.get_manifest(run.parent("training_input"))
        info = training_input.parameters.value()
        binding = info.get("training_binding")
        renderer = info.get("source_renderer")
        if (not isinstance(binding, dict)
                or not isinstance(binding.get("dataset_ids"), list)
                or len(binding["dataset_ids"]) != 1
                or not isinstance(renderer, dict)
                or renderer.get("profile") not in {"public_lite", "public_compact"}):
            raise BoundaryError("local_m0_remote", "owner_admitted_public_m0_run_required")
        dataset_id = digest(binding["dataset_ids"][0], "local_m0_remote.dataset_id")
        operation_id = digest(binding.get("training_operation_id"),
                              "local_m0_remote.training_operation_id", length=32)
        return dataset_id, renderer["profile"], operation_id

    @staticmethod
    def _target(spec: M0AttemptSpec, max_steps: int) -> dict[str, Any]:
        return _remote_target_for_attempt_spec(spec, max_steps)

    @staticmethod
    def _spec(
        request: TokenRemoteUpdateRequest,
        settings: ModalM0Settings,
        plan: M0ExecutionPlan = M0_PILOT_EXECUTION_PLAN,
    ) -> M0AttemptSpec:
        return M0AttemptSpec(
            request.attempt_id, request.request_sha256, settings.account_id,
            settings.environment_name, settings.image_object_id, request.producer,
            request.target_runtime, plan,
        )

    def start(
        self, run_id: object, target_step: object, *, wait_seconds: object = 900,
        after_completed_operation_id: object | None = None,
    ) -> dict[str, Any]:
        """Prepare train-only bytes, journal them, then submit one provider call."""
        if self.settings is None:
            raise BoundaryError("local_m0_remote", "modal_settings_required")
        if type(target_step) is not int:
            raise BoundaryError("local_m0_remote", "step_plan_required")
        wait = _bounded_wait(wait_seconds)
        owner, store, _ = self._context()
        producer = source_identity(ROOT)
        run_id = digest(run_id, "local_m0_remote.run_id")
        run = store.get_manifest(run_id)
        config = decode_config(run.parameters.value().get("config"))
        if (not isinstance(config, LightActionM0Config)
                or config.device != "cuda" or not 1 <= config.steps <= REMOTE_MAX_TOTAL_STEPS
                or not 1 <= target_step <= config.steps):
            raise BoundaryError("local_m0_remote", "bounded_cuda_m0_step_plan_required")
        dataset_id, profile, operation_id = self._profile_and_dataset(store, run_id)
        attempt_id = uuid.uuid4().hex
        request = prepare_token_remote_update(
            store, owner, run_id, producer, operation_id, target_step,
            attempt_id=attempt_id,
        )
        request_bytes = request.to_bytes()
        if len(request_bytes) > REMOTE_MAX_REQUEST_BYTES:
            raise BoundaryError("local_m0_remote", "request_size_limit")
        spec = self._spec(request, self.settings)
        target = self._target(spec, config.steps)
        provider = self.provider_factory(spec=spec)
        # `--modal-account` is a canonical provider workspace ID. Check it before
        # reserving the operation or persisting request/spec bytes, so a slug typo
        # cannot strand a new prepare_intent in the journal.
        provider.verify_account_id(spec.account_id)
        retry_context = self.training.remote_preflight_retry_context()
        if retry_context is not None and (
            spec.account_id != retry_context["canonical_account_id"]
            or spec.environment_name != retry_context["environment_name"]
        ):
            raise BoundaryError("local_m0_remote", "preflight_retry_workspace_mismatch")
        reserved = self.training.reserve_remote_m0(
            dataset_id, input_profile=profile, request_bytes=request_bytes,
            target=target, target_step=target_step,
            attempt_spec_bytes=spec.to_bytes(),
            after_completed_operation_id=after_completed_operation_id,
        )["operation"]
        if reserved.get("status") == "completed":
            return cast(dict[str, Any], reserved)
        self.training.persist_remote_attempt_spec(operation_id, spec.to_bytes())
        try:
            app_ref = provider.prepare_app(spec)
            app_ref_bytes = app_ref.to_bytes()
            self.training.persist_remote_app_ref(operation_id, app_ref_bytes)
            handle = provider.submit(request_bytes, app_ref)
            self.training.record_remote_observation(
                operation_id, state="running", provider_terminal=False,
                handle_bytes=handle.to_bytes(),
            )
        except BoundaryError as error:
            # Once a spec or AppRef was durably recorded, a provider side effect may
            # have happened. Keep it unknown; do not turn errors into a second submit.
            latest, _ = self._saved(operation_id)
            attempt = latest["remote"]["attempts"][-1]
            if attempt["app_phase"] == "not_prepared":
                return self.training.record_remote_preflight_failure(
                    operation_id, error_code=error.code,
                )
            self.training.record_remote_observation(
                operation_id, state="unknown", provider_terminal=False,
                error_code="provider_outcome_unknown",
            )
            return self.reconcile(operation_id, wait_seconds=0)
        return self._poll_and_finalize(operation_id, wait)

    def resume(
        self, operation_id: object, target_step: object, *, wait_seconds: object = 900,
    ) -> dict[str, Any]:
        """Start exactly one new attempt from the journal's validated checkpoint."""
        operation_id = digest(operation_id, "local_m0_remote.operation_id", length=32)
        if type(target_step) is not int:
            raise BoundaryError("local_m0_remote", "step_plan_required")
        wait = _bounded_wait(wait_seconds)
        current, _old_request_bytes = self._saved(operation_id)
        checkpoint_id = current.get("checkpoint_id")
        if not checkpoint_id:
            raise BoundaryError("local_m0_remote", "verified_remote_checkpoint_required")
        evidence = self.training.remote_provider_evidence(operation_id)
        if evidence["attempt_spec_bytes"] is None:
            raise BoundaryError("local_m0_remote", "saved_attempt_spec_required")
        previous_spec = M0AttemptSpec.from_bytes(evidence["attempt_spec_bytes"])
        owner, store, _ = self._context()
        _, old_request = self._saved(operation_id)
        old = TokenRemoteUpdateRequest.from_bytes(old_request)
        identity_provider = self.provider_factory(spec=previous_spec)
        # Old v4 records could pin the workspace display slug. Accept it only when
        # token-info binds that exact slug to its canonical current workspace ID.
        canonical_account_id = identity_provider.verify_account_id(
            previous_spec.account_id, allow_legacy_workspace_alias=True,
        )
        settings = ModalM0Settings(
            canonical_account_id, previous_spec.environment_name,
            previous_spec.image_object_id,
        )
        request = prepare_token_remote_update(
            store, owner, old.run_id, old.producer, operation_id, target_step,
            resume_checkpoint_id=checkpoint_id, attempt_id=uuid.uuid4().hex,
        )
        config = request.config
        if not isinstance(config, LightActionM0Config):
            raise BoundaryError("local_m0_remote", "m0_config_required")
        spec = self._spec(request, settings, previous_spec.resource_plan)
        provider = self.provider_factory(spec=spec)
        provider.verify_account_id(spec.account_id)
        self.training.resume_remote_m0(
            operation_id, request_bytes=request.to_bytes(), target_step=target_step,
        )
        self.training.persist_remote_attempt_spec(operation_id, spec.to_bytes())
        try:
            app_ref = provider.prepare_app(spec)
            self.training.persist_remote_app_ref(operation_id, app_ref.to_bytes())
            handle = provider.submit(request.to_bytes(), app_ref)
            self.training.record_remote_observation(
                operation_id, state="running", provider_terminal=False,
                handle_bytes=handle.to_bytes(),
            )
        except BoundaryError as error:
            latest, _ = self._saved(operation_id)
            attempt = latest["remote"]["attempts"][-1]
            if attempt["app_phase"] == "not_prepared":
                return self.training.record_remote_preflight_failure(
                    operation_id, error_code=error.code,
                )
            self.training.record_remote_observation(
                operation_id, state="unknown", provider_terminal=False,
                error_code="provider_outcome_unknown",
            )
            return self.reconcile(operation_id, wait_seconds=0)
        return self._poll_and_finalize(operation_id, wait)

    def cancel(self, operation_id: object, *, wait_seconds: object = 900) -> dict[str, Any]:
        operation_id = digest(operation_id, "local_m0_remote.operation_id", length=32)
        wait = _bounded_wait(wait_seconds)
        evidence = self.training.remote_provider_evidence(operation_id)
        if evidence["attempt_spec_bytes"] is None or evidence["app_ref_bytes"] is None \
                or evidence["handle_bytes"] is None:
            raise BoundaryError("local_m0_remote", "saved_remote_handle_required")
        spec = M0AttemptSpec.from_bytes(evidence["attempt_spec_bytes"])
        app_ref = ModalM0Target.from_bytes(evidence["app_ref_bytes"])
        provider = self.provider_factory(spec=spec)
        handle = provider.restore_handle(evidence["handle_bytes"], app_ref)
        self.training.request_remote_cancel(operation_id)
        provider.cancel(handle)  # Ack is deliberately not a terminal outcome.
        return self._poll_and_finalize(operation_id, wait)

    def retire_preflight_failure(self, operation_id: object) -> dict[str, Any]:
        """Release only the reconciled, archived no-submit prepare failure."""
        return self.training.retire_remote_preflight_failure(operation_id)

    def reconcile(
        self, operation_id: object, *, wait_seconds: object = 900,
    ) -> dict[str, Any]:
        """Poll a saved handle or stop a known App when submit returned no handle."""
        operation_id = digest(operation_id, "local_m0_remote.operation_id", length=32)
        wait = _bounded_wait(wait_seconds)
        snapshot = self.training.status().get("operation", {})
        snapshot_attempts = snapshot.get("remote", {}).get("attempts", [])
        if (snapshot.get("operation_id") == operation_id
                and snapshot.get("status") == "pending"
                and snapshot.get("stage") == "remote_submit_intent"
                and snapshot_attempts):
            # status() keeps a live prepare/submit process pending while its local
            # operation lock is held. Do not race it with App lookup or stop.
            return cast(dict[str, Any], snapshot)
        current, _ = self._saved(operation_id)
        stage = current.get("stage")
        if current.get("status") in {"completed", "paused", "cancelled", "failed"}:
            return cast(dict[str, Any], self.training.status()["operation"])
        if stage == "remote_acceptance":
            return self._finalize_saved(operation_id)
        evidence = self.training.remote_provider_evidence(operation_id)
        if evidence["attempt_spec_bytes"] is None:
            latest = current["remote"]["attempts"][-1]
            if latest["app_phase"] == "not_prepared":
                return self.training.record_remote_preflight_failure(
                    operation_id, error_code="interrupted_before_provider_prepare",
                )
            return cast(dict[str, Any], self.training.status()["operation"])
        if evidence["handle_bytes"] is None:
            if evidence["app_ref_bytes"] is not None:
                return self._stop_unknown_app(operation_id, wait_seconds=wait)
            spec = M0AttemptSpec.from_bytes(evidence["attempt_spec_bytes"])
            provider = self.provider_factory(spec=spec)
            latest = current["remote"]["attempts"][-1]
            resolving_unpinned_prepare = (
                latest["app_phase"] == "prepare_intent"
                and latest["app_ref_ref"] is None
                and latest["submit_intent_at_unix_ns"] is None
            )
            try:
                # Resolve only the deterministic already-journaled App name. Never deploy
                # or submit from a restarted controller.
                app_ref = provider.resolve_app(
                    spec,
                    allow_legacy_workspace_alias=resolving_unpinned_prepare,
                )
                if app_ref is None:
                    if resolving_unpinned_prepare:
                        canonical_account_id = provider.verify_account_id(
                            spec.account_id, allow_legacy_workspace_alias=True,
                        )
                        recovery_receipt = {
                            "schema": "stpd/local-modal-m0-prepare-absence-receipt-v1",
                            "observed_at_unix_ns": time.time_ns(),
                            "attempt_id": latest["attempt_id"],
                            "attempt_spec_sha256": _sha(evidence["attempt_spec_bytes"]),
                            "canonical_account_id": canonical_account_id,
                            "pinned_account_id": spec.account_id,
                            "environment_name": spec.environment_name,
                            "app_name": spec.app_name,
                            "lookup_result":
                                "complete_app_list_and_exact_history_not_found",
                            "submit_boundary": "no_app_ref_handle_or_submit_intent",
                        }
                        return self.training.record_remote_prepare_absent(
                            operation_id,
                            expected_attempt_spec_sha256=_sha(
                                evidence["attempt_spec_bytes"],
                            ),
                            recovery_receipt=recovery_receipt,
                        )
                    return cast(dict[str, Any], self.training.status()["operation"])
                self.training.persist_remote_app_ref(operation_id, app_ref.to_bytes())
            except BoundaryError:
                if resolving_unpinned_prepare:
                    return self._recover_stopped_prepare(operation_id, spec, provider)
                return cast(dict[str, Any], self.training.status()["operation"])
            return self._stop_unknown_app(operation_id, wait_seconds=wait)
        return self._poll_and_finalize(operation_id, wait)

    def _recover_stopped_prepare(
        self, operation_id: str, spec: M0AttemptSpec, provider: Any,
    ) -> dict[str, Any]:
        # In this single-owner protocol the submit-intent timestamp is durable
        # before any spawn. A stopped App is not App absence or a submitted-call result.
        try:
            proof = provider.inspect_prepare_stopped(spec, allow_legacy_workspace_alias=True)
            if not isinstance(proof, M0PrepareStoppedProof) or proof.spec != spec:
                raise BoundaryError("local_m0_remote", "prepare_stop_binding_invalid")
            receipt = {
                "schema": "stpd/local-modal-m0-prepare-stopped-receipt-v1",
                "observed_at_unix_ns": time.time_ns(),
                "attempt_id": spec.attempt_id,
                "attempt_spec_sha256": _sha(spec.to_bytes()),
                "request_sha256": spec.request_sha256,
                "canonical_account_id": proof.canonical_account_id,
                "pinned_account_id": spec.account_id,
                "environment_name": spec.environment_name,
                "app_name": spec.app_name,
                "app_id": proof.app_id,
                "app_version": 1,
                "deployment_tag": ModalM0Provider._deployment_tag(spec),
                "function_id": proof.function_id,
                "stop_confirmation": _stop_inspection(proof.inspection),
                "lookup_result": "exact_once_deployed_app_stopped",
                "submit_boundary": "no_app_ref_handle_or_submit_intent",
            }
            return self.training.record_remote_prepare_stopped(
                operation_id, expected_attempt_spec_sha256=_sha(spec.to_bytes()),
                recovery_receipt=receipt,
            )
        except BoundaryError:
            return cast(dict[str, Any], self.training.status()["operation"])

    def _stop_unknown_app(self, operation_id: str, *, wait_seconds: float) -> dict[str, Any]:
        current, _ = self._saved(operation_id)
        if (current.get("status") != "interrupted_unknown"
                or current.get("stage") != "remote_unknown"):
            self.training.record_remote_observation(
                operation_id, state="unknown", provider_terminal=False,
                error_code="provider_outcome_unknown",
            )
        evidence = self.training.remote_provider_evidence(operation_id)
        if evidence["attempt_spec_bytes"] is None or evidence["app_ref_bytes"] is None:
            return cast(dict[str, Any], self.training.status()["operation"])
        spec = M0AttemptSpec.from_bytes(evidence["attempt_spec_bytes"])
        app_ref = ModalM0Target.from_bytes(evidence["app_ref_bytes"])
        provider = self.provider_factory(spec=spec)
        provider.stop_app(app_ref, allow_legacy_workspace_alias=True)
        inspection = provider.inspect_stop(
            app_ref, allow_legacy_workspace_alias=True,
        )
        if inspection.confirmed:
            self.training.record_remote_unknown_app_stopped(
                operation_id, _stop_inspection(inspection),
            )
        return cast(dict[str, Any], self.training.status()["operation"])

    def _poll_and_finalize(
        self, operation_id: str, wait_seconds: float,
    ) -> dict[str, Any]:
        current, _ = self._saved(operation_id)
        latest = current["remote"]["attempts"][-1]
        evidence = self.training.remote_provider_evidence(operation_id)
        if latest["phase"] == "stopping":
            return self._stop_and_finalize(operation_id)
        if latest["phase"] == "terminal":
            if latest["terminal_state"] in {"paused", "completed"}:
                return self._finalize_saved(operation_id)
            return cast(dict[str, Any], self.training.status()["operation"])
        if (evidence["attempt_spec_bytes"] is None or evidence["app_ref_bytes"] is None
                or evidence["handle_bytes"] is None):
            return self._stop_unknown_app(operation_id, wait_seconds=wait_seconds)

        spec = M0AttemptSpec.from_bytes(evidence["attempt_spec_bytes"])
        app_ref = ModalM0Target.from_bytes(evidence["app_ref_bytes"])
        provider = self.provider_factory(spec=spec)
        handle = provider.restore_handle(evidence["handle_bytes"], app_ref)
        deadline = self._monotonic() + wait_seconds
        terminal_state: str | None = None
        result_bytes: bytes | None = None
        runtime_bytes: bytes | None = None
        error_code: str | None = None
        while True:
            try:
                result_bytes = provider.poll(handle, timeout_seconds=min(5.0, max(
                    0.0, deadline - self._monotonic(),
                )))
            except BoundaryError as error:
                if error.code == "call_terminal_failure":
                    terminal_state, error_code = "failed", error.code
                elif error.code == "call_cancelled":
                    terminal_state = "cancelled"
                else:
                    error_code = "provider_outcome_unknown"
                break
            if result_bytes is not None:
                runtime_bytes = provider.get_runtime_evidence(handle).to_bytes()
                parsed = TokenRemoteUpdateResult.from_bytes(result_bytes)
                terminal_state = (
                    "completed" if parsed.checkpoint_step == parsed.config.steps else "paused"
                )
                break
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                error_code = "provider_outcome_unknown"
                break
            self._sleep(min(self.settings.poll_interval_seconds if self.settings else 1.0,
                            remaining))

        if terminal_state is None:
            self.training.record_remote_observation(
                operation_id, state="unknown", provider_terminal=False,
                error_code=error_code or "provider_outcome_unknown",
            )
            return self._stop_unknown_app(operation_id, wait_seconds=0)

        candidate_sha = None
        if result_bytes is not None:
            parsed = TokenRemoteUpdateResult.from_bytes(result_bytes)
            candidate_sha = parsed.checkpoint_sha256
        self.training.record_remote_observation(
            operation_id, state=terminal_state, provider_terminal=True,
            checkpoint_candidate_sha256=candidate_sha,
            provider_result_bytes=result_bytes,
            runtime_evidence_bytes=runtime_bytes,
            error_code=error_code,
        )
        return self._stop_and_finalize(operation_id)

    def _stop_and_finalize(self, operation_id: str) -> dict[str, Any]:
        evidence = self.training.remote_provider_evidence(operation_id)
        if evidence["attempt_spec_bytes"] is None or evidence["app_ref_bytes"] is None:
                return cast(dict[str, Any], self.training.status()["operation"])
        spec = M0AttemptSpec.from_bytes(evidence["attempt_spec_bytes"])
        app_ref = ModalM0Target.from_bytes(evidence["app_ref_bytes"])
        provider = self.provider_factory(spec=spec)
        provider.stop_app(app_ref)
        inspection = provider.inspect_stop(app_ref)
        if not inspection.confirmed:
            return cast(dict[str, Any], self.training.status()["operation"])
        self.training.record_remote_stop_confirmation(
            operation_id, _stop_inspection(inspection),
        )
        operation = cast(dict[str, Any], self.training.status()["operation"])
        if operation.get("stage") == "remote_acceptance":
            return self._finalize_saved(operation_id)
        return operation

    def _finalize_saved(self, operation_id: str) -> dict[str, Any]:
        evidence = self.training._remote_finalization_evidence(operation_id)
        request = TokenRemoteUpdateRequest.from_bytes(evidence["request_bytes"])
        result = TokenRemoteUpdateResult.from_bytes(evidence["result_bytes"])
        provider_handle_bytes = evidence["provider_handle_bytes"]
        app_ref_bytes = evidence["app_ref_bytes"]
        spec_bytes = evidence["attempt_spec_bytes"]
        runtime_bytes = evidence["provider_runtime_evidence_bytes"]
        expected_result_sha = evidence["attempt"]["provider_result_sha256"]
        if any(raw is None for raw in (provider_handle_bytes, app_ref_bytes, spec_bytes,
                                       runtime_bytes)):
            raise BoundaryError("local_m0_remote", "durable_provider_evidence_required")

        def verify_provider_result(
            saved_request: TokenRemoteUpdateRequest,
            saved_result: TokenRemoteUpdateResult,
        ) -> bool:
            try:
                spec = M0AttemptSpec.from_bytes(spec_bytes)
                app_ref = ModalM0Target.from_bytes(app_ref_bytes)
                handle = ModalM0Call.from_bytes(provider_handle_bytes)
                runtime = ModalM0RuntimeEvidence.from_bytes(runtime_bytes)
                runtime.bind(handle)
                raw_request = evidence["request_bytes"]
                raw_result = evidence["result_bytes"]
                return (
                    saved_request.to_bytes() == raw_request
                    and saved_result.to_bytes() == raw_result
                    and _sha(raw_request) == handle.request_sha256
                    and _sha(raw_result) == expected_result_sha
                    and handle.target == app_ref
                    and handle.target_id == app_ref.target_id
                    and handle.target.attempt_id == spec.attempt_id
                    and handle.target.request_sha256 == spec.request_sha256
                    and handle.target.producer == spec.producer
                    and handle.target.image_object_id == spec.image_object_id
                    and handle.target.target_runtime == spec.target_runtime
                    and handle.target.resource_plan == spec.resource_plan
                    and handle.operation_id == request.operation_id
                    and handle.request_sha256 == request.request_sha256
                    and handle.attempt_id == request.attempt_id
                    and handle.run_id == request.run_id
                    and handle.input_id == request.input_id
                    and handle.target_step == request.target_step
                    and handle.resume_checkpoint_id == request.resume_checkpoint_id
                    and request.producer == spec.producer
                    and runtime.request_sha256 == request.request_sha256
                    and runtime.attempt_id == request.attempt_id
                    and runtime.image_object_id == spec.image_object_id
                    and runtime.producer == spec.producer
                    and runtime.result_sha256 == _sha(raw_result)
                    and runtime.result_size_bytes == len(raw_result)
                    and saved_result.request_sha256 == request.request_sha256
                    and saved_result.operation_id == request.operation_id
                    and saved_result.attempt_id == request.attempt_id
                    and saved_result.run_id == request.run_id
                    and saved_result.input_id == request.input_id
                    and saved_result.producer == request.producer
                )
            except (BoundaryError, ValueError, TypeError):
                return False

        owner, store, _ = self._context()
        reporter = ObjectStoreRunReporter(store, store.blobs)
        if request.target_step < request.config.steps:
            checkpoint_id = publish_token_remote_checkpoint(
                store, reporter, owner, request, result, request.producer,
                request.operation_id,
                verify_provider_result=verify_provider_result,
            )
            self.training.accept_remote_checkpoint(operation_id, checkpoint_id)
        else:
            binding = request.training_binding.value()

            def admit_dev(model: Any, view: Any) -> dict[str, Any]:
                if (model.parent("run") != request.run_id
                        or model.parent("training_input") != request.input_id
                        or model.parent("model_view") != binding.get("model_view_id")):
                    raise BoundaryError("local_m0_remote", "dev_binding_mismatch")
                evaluation_id = hashlib.sha256(
                    f"stage1a-m0-dev-v1:{request.run_id}:{model.artifact_id}".encode("ascii"),
                ).hexdigest()[:32]
                return cast(dict[str, Any], owner.reserve_allocation_dev(
                    store, model.artifact_id, binding["allocation_id"],
                    request.operation_id, evaluation_id,
                ))

            finalizer_started = time.perf_counter()
            completed = finalize_token_remote_update(
                store, reporter, owner, request, result, request.producer,
                request.operation_id,
                verify_provider_result=verify_provider_result,
                dev_admitter=admit_dev,
            )
            finalizer_seconds = max(0.0, time.perf_counter() - finalizer_started)
            self.training.accept_remote_candidate(
                operation_id, input_id=request.input_id, run_id=request.run_id,
                result_id=completed.result_id,
                core_finalizer_attempt_seconds=finalizer_seconds,
            )
        return cast(dict[str, Any], self.training.status()["operation"])
