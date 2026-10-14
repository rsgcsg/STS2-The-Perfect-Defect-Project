"""Trusted structured workload composition over existing Store/Reporter/curation.

STPD verifies numeric checkpoints/results. The application owns source admission,
use reservations, attempt publication fences, control and cumulative wall budget.
"""

from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, digest
from spireagent.source import source_identity
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ROOT
from spireagent.workbench.recipe_contracts import TrainingRequest, effective_training_limits
from spireagent.workbench.research_process import private_child
from spireagent.workbench.training_scratch import retained_scratch_bytes
from spireagent.workbench.trusted_recipes import (
    NATIVE_SAMPLED_RECIPE,
    ORDERED_RECIPES,
    checked_recipe_execution_policy,
    structured_recipe_scope,
    typed_recipe_checkpoint_schema,
    typed_recipe_is_scoped,
    typed_recipe_run_schema,
)

_private_child = private_child


@dataclass(frozen=True)
class StructuredPreflight:
    dataset: Any
    data_counts: FrozenObject
    distinct_N_basis: str


def _preflight_result(dataset: Any, index: Any, with_monitor_facts: bool) -> Any:
    if not with_monitor_facts:
        return dataset
    runs = tuple(run for run in dataset.runs if run.split == "train")
    labelled = {(run.run_id, step.position) for run in runs for step in run.steps
                if step.chosen_action_id is not None}
    counts = {
        "distinct_supervised_N":len(labelled),
        "contexts":sum(len(run.steps) for run in runs),
        "unlabelled_contexts":sum(
            step.chosen_action_id is None for run in runs for step in run.steps),
        "excluded_N":None,
        "captured_readiness_dispositions":0,
    }
    basis = "admitted_train_run_step_identity"
    if index and all("N_eligible" in row for row in index):
        counts["distinct_supervised_N"] = len({(row["raw_id"], row["occurrence_id"])
            for row in index if row["N_eligible"]})
        basis = "qualified_original_train_run_and_source_occurrence_identity"
        if all("original_N_candidate" in row for row in index):
            counts["excluded_N"] = sum(row["original_N_candidate"] and not row["N_eligible"]
                                       for row in index)
        counts["captured_readiness_dispositions"] = sum(
            row.get("target_eligibility") in {
                "empty_C_readiness_not_sample", "unchanged_current_readiness_not_sample",
                "discarded_readiness_no_sample", "unchanged_original_unit_no_student_sample"}
            for row in index)
    return StructuredPreflight(dataset, FrozenObject.of(counts), basis)


def verify_run_execution_policy(run: Any, operation: dict[str, Any]) -> dict[str, Any] | None:
    """The admitted request and immutable Run select the same package policy."""
    request = TrainingRequest.from_dict(operation["request"])
    info = run.parameters.value()
    if request.batch_policy != info.get("execution_identity", {}).get("batch_policy"):
        raise BoundaryError("local_training", "training_batch_policy_mismatch")
    policy = (checked_recipe_execution_policy(operation["recipe"], info["execution_policy"])
              if "execution_policy" in info else None)
    if request.recipe_id != operation["recipe"] or request.execution_policy != policy:
        raise BoundaryError("local_training", "training_execution_policy_mismatch")
    return policy


def verify_resume_checkpoint(store: Any, operation: dict[str, Any], checkpoint_id: Any) -> None:
    from spireagent.workbench.trusted_recipes import HISTORICAL_M0_RECIPE, REWARD_M0_RECIPE

    if operation["recipe"] == HISTORICAL_M0_RECIPE:
        from spireagent.workbench.recipes.historical_m0 import verify_historical_resume_checkpoint

        verify_historical_resume_checkpoint(store, operation, checkpoint_id)
        return

    if operation["recipe"] == REWARD_M0_RECIPE:
        from spireagent.workbench.recipes.reward_m0 import verify_reward_resume_checkpoint

        verify_reward_resume_checkpoint(store, operation, checkpoint_id)
        return
    checkpoint = store.get_manifest(checkpoint_id)
    run = store.get_manifest(operation["run_id"])
    verify_run_execution_policy(run, operation)
    from stpd.structured_code_scope import checkpoint_schema, run_code_scope

    info = checkpoint.parameters.value()
    scope = structured_recipe_scope(operation["recipe"])
    scoped = typed_recipe_is_scoped(operation["recipe"])
    if (
        run.parameters.value().get("schema") != typed_recipe_run_schema(operation["recipe"])
        or run_code_scope(run.parameters.value().get("schema")) != scope
        or checkpoint.kind != "checkpoint"
        or not scoped
        and checkpoint.producer != run.producer
        or checkpoint.parent("run") != run.artifact_id
        or checkpoint.parent("training_input") != operation["input_id"]
        or info.get("schema") != checkpoint_schema(scope)
    ):
        raise BoundaryError("local_training", "checkpoint_run_identity_mismatch")
    if scoped and (
        info.get("attempt_producer") != checkpoint.producer.to_dict()
        or checkpoint.producer.uv_lock_sha256
        != run.parameters.value()["execution_identity"]["code_identity"]["dependency_lock_sha256"]
    ):
        raise BoundaryError("local_training", "checkpoint_attempt_producer_mismatch")
    prior = {item["attempt_id"]: item for item in operation["attempts"] if item["writer_terminal"]}
    if operation["writer_terminal"]:
        prior[operation["attempt_id"]] = operation
    creator = prior.get(info.get("attempt"))
    if creator is None:
        raise BoundaryError("local_training", "checkpoint_writer_terminal_required")
    if scoped and Producer.decode(creator["attempt_producer"]) != checkpoint.producer:
        raise BoundaryError("local_training", "checkpoint_attempt_producer_mismatch")
    # A copied or partially published checkpoint needs its exact historical event,
    # never a relabelled current writer or the run's original producer.
    events = ObjectStoreRunReporter(store, store.blobs).events(run.artifact_id)
    if not any(
        event.kind == "run_event"
        and event.parent("run") == run.artifact_id
        and event.producer == checkpoint.producer
        and event.parameters.value().get("schema") == "stpd/run-event-v1"
        and event.parameters.value().get("kind") == "checkpoint"
        and event.parameters.value().get("attempt") == info["attempt"]
        and event.parameters.value().get("details", {}).get("checkpoint_id") == checkpoint_id
        and (
            not scoped
            or event.parameters.value().get("details", {}).get("attempt_producer")
            == checkpoint.producer.to_dict()
        )
        for event in events
    ):
        raise BoundaryError("local_training", "checkpoint_event_required")


class StructuredRecipeAdapter:
    def __init__(self, recipe_id: str | None = None) -> None:
        self.recipe_id = recipe_id

    def preflight(self, store: Any, owner: Any, source_id: str,
                  *, with_monitor_facts: bool = False) -> Any:
        from stpd.fullrun.structured_sequences import (
            MAX_SOURCE_BYTES,
            SOURCE_SCHEMA,
            parse_structured_dataset,
        )

        source = store.get_manifest(source_id)
        info = source.parameters.value()
        if self.recipe_id == NATIVE_SAMPLED_RECIPE:
            from stpd.structured_profiles import verify_sampled_partition

            verified = verify_sampled_partition(store, source_id)
            if owner.ledger.dataset(source_id) != ("training", set(verified.runs)):
                raise BoundaryError("local_training", "source_not_reserved_for_training")
            if not any(step.chosen_action_id is not None
                       for run in verified.dataset.runs for step in run.steps):
                raise BoundaryError("local_training", "sampled_no_eligible_N_for_training")
            return _preflight_result(verified.dataset, verified.index, with_monitor_facts)
        if info.get("partition_schema") == "stpd/source3-ordered-partition-v1":
            from stpd.fullrun.ordered_source import verify_ordered_source_partition
            from stpd.ordered_source_spec import checked_view, recipe_view

            if self.recipe_id not in ORDERED_RECIPES:
                raise BoundaryError("local_training", "source3_recipe_required")
            verified_ordered = verify_ordered_source_partition(store, source_id)
            if verified_ordered.split != "train" or any(
                run.split != "train" for run in verified_ordered.dataset.runs
            ):
                raise BoundaryError("local_training", "train_only_source_required")
            if checked_view(info["projection_spec"], info["target_spec"]) != recipe_view(
                self.recipe_id
            ):
                raise BoundaryError("local_training", "source3_recipe_projection_target_mismatch")
            if owner.ledger.dataset(source_id) != ("training", set(verified_ordered.runs)):
                raise BoundaryError("local_training", "training_claim_mismatch")
            if not any(
                step.chosen_action_id is not None
                for run in verified_ordered.dataset.runs
                for step in run.steps
            ):
                raise BoundaryError("local_training", "source3_no_eligible_N_for_training")
            return _preflight_result(
                verified_ordered.dataset, verified_ordered.index, with_monitor_facts)
        if self.recipe_id in ORDERED_RECIPES:
            raise BoundaryError("local_training", "typed_source3_partition_required")
        if "partition_schema" in info:
            from stpd.fullrun.protocol_source import verify_protocol_source_partition

            verified = verify_protocol_source_partition(store, source_id)
            if verified.split != "train" or any(
                run.split != "train" for run in verified.dataset.runs
            ):
                raise BoundaryError("local_training", "train_only_source_required")
            if owner.ledger.dataset(source_id) != ("training", set(verified.runs)):
                raise BoundaryError("local_training", "training_claim_mismatch")
            return _preflight_result(verified.dataset, verified.index, with_monitor_facts)
        payload = source.payload("source")
        if (
            source.kind != "dataset"
            or info.get("schema") != SOURCE_SCHEMA
            or payload.size > MAX_SOURCE_BYTES
        ):
            raise BoundaryError("local_training", "structured_source_manifest_required")
        dataset = parse_structured_dataset(b"".join(store.read_payload(payload)))
        if (
            dataset.source_sha256 != payload.sha256
            or info.get("source_sha256") != dataset.source_sha256
            or info.get("source_kind") != dataset.source_kind
        ):
            raise BoundaryError("local_training", "structured_source_identity_mismatch")
        # Synthetic fixtures exercise this actual application path without
        # granting Agent/Human provenance or runtime qualification. A production
        # Agent source needs its owning typed verifier and immutable report join.
        if dataset.source_kind != "synthetic" or info.get("qualification") != "synthetic_fixture":
            raise BoundaryError("local_training", "structured_source_verifier_required")
        if any(run.split != "train" for run in dataset.runs):
            raise BoundaryError("local_training", "train_only_source_required")
        runs = {run.run_id for run in dataset.runs}
        if owner.ledger.dataset(source_id) != ("training", runs):
            raise BoundaryError("local_training", "training_claim_mismatch")
        if (
            not owner.ledger.exact_source_ready(source_id)
            or owner.ledger.source_runs(source_id) != runs
        ):
            raise BoundaryError("local_training", "source_index_incomplete")
        return _preflight_result(dataset, None, with_monitor_facts)

    def record_training_use(self, owner: Any, store: Any, operation: dict[str, Any],
                            source_id: str, identity: str, runs: set[str]) -> None:
        protocol_source = "partition_schema" in store.get_manifest(source_id).parameters.value()
        ordered_source = store.get_manifest(source_id).parameters.value().get(
            "partition_schema") == "stpd/source3-ordered-partition-v1"
        if self.recipe_id == NATIVE_SAMPLED_RECIPE and not ordered_source:
            if operation["mode"] == "start":
                owner.record_verified_native_agent_sampled_training_use(store, source_id, identity)
            owner.require_verified_native_agent_sampled_training_use(store, source_id, identity)
        elif ordered_source:
            if operation["mode"] == "start":
                owner.record_verified_ordered_training_use(store, source_id, identity)
            owner.require_verified_ordered_training_use(store, source_id, identity)
        elif protocol_source:
            if operation["mode"] == "start":
                owner.record_verified_protocol_training_use(store, source_id, identity)
            owner.require_verified_protocol_training_use(store, source_id, identity)
        else:
            if operation["mode"] == "start":
                owner.ledger.use_source(source_id, "training", identity)
                owner.ledger.use(runs, "training", identity)
            owner.ledger.require_training_use(source_id, {source_id}, runs, identity)


    def preflight_request(self, store: Any, owner: Any, request: TrainingRequest) -> Any:
        return self.preflight(store, owner, request.source_id, with_monitor_facts=True)

    def training_plan(self, store: Any, owner: Any, request: TrainingRequest,
                      *, prepared_dataset: Any = None) -> dict[str, Any]:
        from stpd.structured_workload_contracts import (
            StructuredBatchPolicy,
            StructuredTrainingConfig,
            frozen_training_plan,
        )

        prepared = (self.preflight_request(store, owner, request)
                    if prepared_dataset is None else prepared_dataset)
        if not isinstance(prepared, StructuredPreflight):
            raise BoundaryError("local_training", "verified_preflight_monitor_facts_required")
        config = StructuredTrainingConfig(**{key:item for key,item in request.config.items()
                                            if key != "checkpoint_every_boundaries"})
        return frozen_training_plan(prepared.dataset, config,
            data_counts=prepared.data_counts.value(), distinct_N_basis=prepared.distinct_N_basis,
            batch_policy=(None if request.batch_policy is None else
                          StructuredBatchPolicy.from_dict(request.batch_policy)))

    def preflight_remote(self, request: TrainingRequest, target: Any) -> None:
        from stpd.structured_code_scope import LEGACY_SCOPE
        from stpd.structured_remote_contracts import remote_code_closure_sha256

        if (request.recipe_id != self.recipe_id or target.recipe_id != self.recipe_id
                or structured_recipe_scope(request.recipe_id) == LEGACY_SCOPE
                or target.code_closure_sha256 != remote_code_closure_sha256(
                    structured_recipe_scope(request.recipe_id))
                or target.producer != source_identity(ROOT)):
            raise BoundaryError("local_training", "remote_exact_source_scope_required")

    @staticmethod
    def _remote_request(operation: dict[str, Any], store: Any, dataset: Any,
                        target: Any) -> Any:
        from spireagent.training_execution_contracts import PreparedTrainingAttempt
        from spireagent.workbench.remote_training_operation import RemotePreparedWorkload
        from stpd.structured_remote_contracts import (
            StructuredDevicePolicy,
            build_structured_remote_request,
            numerical_projection,
            sha256,
        )
        from stpd.structured_workload_contracts import StructuredWorkloadRequest
        from stpd.workers.structured_remote_domain import build_structured_projection

        run = store.get_manifest(operation["run_id"])
        request = TrainingRequest.from_dict(operation["request"])
        identity = run.parameters.value()["execution_identity"]
        workload = StructuredWorkloadRequest(
            operation["run_id"], operation["input_id"], operation["operation_id"],
            operation["attempt_id"], mode=operation["mode"],
            resume_checkpoint_id=operation.get("resume_checkpoint_id"),
            checkpoint_every_boundaries=request.config.get("checkpoint_every_boundaries", 100))
        events = ObjectStoreRunReporter(store, store.blobs).events(run.artifact_id)
        raw_request = build_structured_remote_request(
            workload, execution_identity=identity,
            code_scope=structured_recipe_scope(request.recipe_id),
            device_policy=StructuredDevicePolicy(target.placement.device),
            numerical_projection_sha256=sha256(numerical_projection(dataset)),
            grant_expires_at_ms=int(operation["remote"]["deadline_at"] * 1000),
            event_step=len(events))
        checkpoint = None
        if workload.mode == "resume":
            checkpoint = b"".join(store.read_payload(store.get_manifest(
                workload.resume_checkpoint_id).payload("checkpoint")))
        projection = build_structured_projection(dataset, workload,
            maximum=target.limits.max_projection_bytes, resume_checkpoint=checkpoint)
        prepared = PreparedTrainingAttempt(
            workload.run_id, workload.training_input_id, workload.operation_id, workload.attempt_id,
            sha256(raw_request), sha256(projection), target.producer, target.code_closure_sha256,
            target.worker_capability, target.runtime, target.placement, target.limits)
        return RemotePreparedWorkload(prepared, raw_request, projection)

    def prepare_remote(self, service: Any, path: Any, identity: str, owner: Any,
                       store: Any, target: Any, *, budget: Any) -> Any:
        from spireagent.training_execution_contracts import training_execution_binding
        from stpd.native_graph_spec import NativeGraphControl
        from stpd.structured_profiles import CONTROL_SCOPES
        from stpd.structured_remote_contracts import StructuredDevicePolicy
        from stpd.structured_workload_contracts import StructuredTrainingConfig
        from stpd.workers.structured_run import prepare_structured_run

        budget.assert_preparation_budget()
        operation = service._read(path, owner.identity)
        request = TrainingRequest.from_dict(operation["request"])
        self.preflight_remote(request, target)
        dataset = self.preflight(store, owner, request.source_id)
        self.record_training_use(owner, store, operation, request.source_id, identity,
                                 {run.run_id for run in dataset.runs})
        budget.assert_preparation_budget()
        scope = structured_recipe_scope(request.recipe_id)
        if operation["mode"] == "start":
            config = StructuredTrainingConfig(**{key: item for key, item in request.config.items()
                                                if key != "checkpoint_every_boundaries"})
            binding = training_execution_binding(target.producer, target.code_closure_sha256,
                target.worker_capability, target.runtime, target.placement, target.limits)
            run = prepare_structured_run(store, dataset, target.producer, config,
                operation_id=identity, source_id=request.source_id, code_scope=scope,
                model_control=NativeGraphControl() if scope in CONTROL_SCOPES else None,
                execution_policy=request.execution_policy,
                remote_binding={"runtime": target.runtime.value(),
                    "device_policy": StructuredDevicePolicy(target.placement.device).to_dict(),
                    "execution_binding_id": binding,
                    "code_closure_sha256": target.code_closure_sha256,
                    "batch_policy": request.batch_policy})
            with service._lock:
                budget.assert_preparation_budget()
                service._advance(path, identity, use_state="reserved", stage="training",
                                 run_id=run.artifact_id, input_id=run.parent("training_input"))
            operation = service._read(path, owner.identity)
        else:
            verify_run_execution_policy(store.get_manifest(operation["run_id"]), operation)
            self.verify_remote_resume(store, operation, operation["resume_checkpoint_id"])
        budget.assert_preparation_budget()
        return self._remote_request(operation, store, dataset, target)

    def accept_remote_candidate(self, store: Any, operation: dict[str, Any], candidate: Any,
                                workload: Any, *, budget: Any) -> dict[str, Any]:
        from spireagent.workbench.remote_training_operation import remote_terminal_proven
        from stpd.workers.structured_remote_domain import (
            check_structured_remote_candidate,
            publish_structured_remote_candidate,
        )

        if not operation["writer_terminal"] or not remote_terminal_proven(operation["remote"]):
            raise BoundaryError("local_training", "remote_terminal_proof_required")
        owner = budget.owner
        request = TrainingRequest.from_dict(operation["request"])
        dataset = self.preflight(store, owner, request.source_id)
        self.record_training_use(owner, store, operation, request.source_id,
                                 operation["operation_id"], {run.run_id for run in dataset.runs})
        run = store.get_manifest(operation["run_id"])
        verify_run_execution_policy(run, operation)
        from spireagent.training_execution_contracts import StopInspection
        checked = check_structured_remote_candidate(workload.prepared,
            workload.request_bytes, workload.projection_bytes, candidate,
            StopInspection.from_dict(operation["remote"]["stop_inspection"]))

        class Authority:
            def assert_current(self, run_id: str, operation_id: str, attempt_id: str) -> None:
                current = budget.current()
                if (current.get("run_id") != run_id or current["operation_id"] != operation_id
                        or current["attempt_id"] != attempt_id or not current["writer_terminal"]
                        or not remote_terminal_proven(current["remote"])
                        or budget.remaining_seconds(workload.prepared.identity) <= 0):
                    raise BoundaryError("local_training", "remote_parent_adoption_fence_lost")

        accepted = publish_structured_remote_candidate(store,
            ObjectStoreRunReporter(store, store.blobs), checked, workload.prepared, run, dataset,
            code_scope=structured_recipe_scope(request.recipe_id), authority=Authority())
        state = checked.state
        counters = {"optimizer_updates": state["counters"]["updates"],
            "label_uses": state["counters"]["labels"],
            "observations": state["counters"]["observations"], "boundaries": state["boundary"],
            "epoch": state["cursor"]["epoch"], "whole_cohort_passes": state["cursor"]["epoch"]}
        return {**accepted, "state": "completed" if checked.training_complete else "paused",
            "run_id": operation["run_id"], "attempt_id": operation["attempt_id"],
            "accepted_counters": counters, "checkpoint_progress": {
                "checkpoint_id": accepted["checkpoint_id"],
                "optimizer_updates": counters["optimizer_updates"],
                "boundaries": counters["boundaries"], "epoch": counters["epoch"]}}

    def verify_remote_resume(self, store: Any, operation: dict[str, Any],
                             checkpoint_id: Any) -> None:
        from spireagent.workbench.remote_training_operation import ConfiguredTrainingTarget
        from stpd.structured_remote_contracts import CHECKPOINT_SCHEMA
        from stpd.workers.structured_remote_domain import verify_structured_remote_checkpoint

        run = store.get_manifest(operation["run_id"])
        verify_run_execution_policy(run, operation)
        checkpoint = store.get_manifest(digest(checkpoint_id, "local_training.checkpoint_id"))
        info = checkpoint.parameters.value()
        prior = {row["attempt_id"]: row for row in operation["attempts"] if row["writer_terminal"]}
        if operation["writer_terminal"]:
            prior[operation["attempt_id"]] = operation
        if (checkpoint.kind != "checkpoint" or info.get("schema") != CHECKPOINT_SCHEMA
                or checkpoint.parent("run") != run.artifact_id
                or checkpoint.parent("training_input") != operation["input_id"]
                or info.get("attempt") not in prior):
            raise BoundaryError("local_training", "remote_checkpoint_writer_binding")
        events = ObjectStoreRunReporter(store, store.blobs).events(run.artifact_id)
        if not any(event.kind == "run_event" and event.parent("run") == run.artifact_id
                   and event.producer == checkpoint.producer
                   and event.parameters.value().get("attempt") == info["attempt"]
                   and event.parameters.value().get("kind") == "checkpoint"
                   and event.parameters.value().get("details", {}).get(
                       "checkpoint_id") == checkpoint_id
                   for event in events):
            raise BoundaryError("local_training", "checkpoint_event_required")
        from stpd.structured_profiles import parse_dataset
        dataset = parse_dataset(b"".join(store.read_payload(store.get_manifest(
            operation["input_id"]).payload("source"))),
            structured_recipe_scope(operation["recipe"]))
        target = ConfiguredTrainingTarget.from_dict(operation["remote"]["target"])
        # Build only the frozen numerical request; no provider or mutation occurs.
        probe = dict(operation, mode="resume", resume_checkpoint_id=checkpoint_id)
        workload = self._remote_request(probe, store, dataset, target)
        from stpd.structured_remote_contracts import numerical_projection
        checkpoint_raw = b"".join(store.read_payload(checkpoint.payload("checkpoint")))
        verify_structured_remote_checkpoint(checkpoint_raw,
                                            workload.request_bytes, numerical_projection(dataset))

    def validate_terminal(self, store: Any, operation: dict[str, Any],
                          terminal: dict[str, Any]) -> None:
        """Existing structured terminals are validated by their registered domain worker."""

    def execute(self, service: Any, path: Any, identity: str, owner: Any,
                store: Any, mark_started: Any) -> None:
        operation = service._read(path, owner.identity)
        expected_policy = TrainingRequest.from_dict(operation["request"]).execution_policy
        if operation["mode"] != "start":
            verify_run_execution_policy(store.get_manifest(operation["run_id"]), operation)
        dataset = self.preflight(store, owner, operation["dataset_id"])
        runs = {run.run_id for run in dataset.runs}
        source_id, attempt_id = operation["dataset_id"], operation["attempt_id"]
        scoped = typed_recipe_is_scoped(operation["recipe"])
        attempt_producer = (Producer.decode(operation["attempt_producer"]) if scoped else
                            source_identity(ROOT))
        expected_run_schema = typed_recipe_run_schema(operation["recipe"])
        self.record_training_use(owner, store, operation, source_id, identity, runs)

        def current() -> dict[str, Any]:
            value: dict[str, Any] = service._read(path, owner.identity)
            if (value.get("operation_id") != identity or value.get("attempt_id") != attempt_id
                    or value.get("status") != "pending" or value.get("writer_terminal")
                    or service._active_attempt != attempt_id):
                raise BoundaryError("local_training", "attempt_fence_lost")
            if TrainingRequest.from_dict(value["request"]).execution_policy != expected_policy:
                raise BoundaryError("local_training", "training_execution_policy_mismatch")
            return value

        with service._lock:
            current()
            service._advance(path, identity, use_state="reserved", stage="preparing_run")
        sequence = 0
        handshake = False
        terminal: dict[str, Any] | None = None
        failure: dict[str, str] | None = None

        def message(raw: bytes) -> None:
            nonlocal sequence, handshake, terminal, failure
            value = decode_json(raw)
            if (not isinstance(value, dict) or set(value) != {"schema", "operation_id",
                    "attempt_id", "sequence", "kind", "details"}
                    or value["schema"] != "spireagent/structured-child-event-v1"
                    or value["operation_id"] != identity or value["attempt_id"] != attempt_id
                    or type(value["sequence"]) is not int or value["sequence"] != sequence+1
                    or not isinstance(value["details"], dict)):
                raise BoundaryError("local_training", "child_channel_identity_mismatch")
            sequence += 1
            details, kind = value["details"], value["kind"]
            with service._lock:
                recorded = current()
                if kind == "started":
                    expected_started = ({"attempt_producer": attempt_producer.to_dict()}
                                        if scoped else {})
                    if sequence != 1 or details != expected_started or handshake:
                        raise BoundaryError("local_training", "child_handshake_invalid")
                    handshake = True
                    service._advance(path, identity, child_handshake=True)
                elif kind == "reserve_artifact":
                    if (not handshake or set(details) != {"number", "size", "role"}
                            or type(details["number"]) is not int
                            or type(details["size"]) is not int or details["size"] < 0
                            or details["role"] not in {"payload", "manifest", "slot"}):
                        raise BoundaryError("local_training", "child_reservation_invalid")
                    prior = recorded.get("artifact_reservation", {})
                    expected = (prior.get("number", 0)+1
                                if prior.get("attempt_id") == attempt_id else 1)
                    used = recorded.get("artifact_reserved_bytes", 0)+details["size"]
                    if details["number"] != expected:
                        raise BoundaryError("local_training", "child_reservation_invalid")
                    if used > recorded["request"]["limits"]["scratch_bytes"]:
                        raise BoundaryError("local_training", "artifact_budget_exhausted")
                    service._advance(path, identity, artifact_reserved_bytes=used,
                                     artifact_reservation={"attempt_id": attempt_id, **details})
                elif kind == "prepared":
                    if not handshake or set(details) != {"run_id", "input_id"}:
                        raise BoundaryError("local_training", "child_preparation_invalid")
                    run_id = digest(details["run_id"], "local_training.run_id")
                    input_id = digest(details["input_id"], "local_training.input_id")
                    run, training = store.get_manifest(run_id), store.get_manifest(input_id)
                    verify_run_execution_policy(run, recorded)
                    if (recorded["mode"] != "start" or recorded.get("run_id")
                            or run.kind != "run" or run.producer != attempt_producer
                            or run.parameters.value().get("schema") != expected_run_schema
                            or run.parameters.value().get("operation_id") != identity
                            or run.parent("training_input") != input_id
                            or training.kind != "training_input"
                            or training.producer != run.producer
                            or training.parent("source") != source_id):
                        raise BoundaryError("local_training", "child_preparation_invalid")
                    plan = recorded.get("training_plan")
                    if plan is not None and (
                            plan["source_sha256"] != run.parameters.value()["source_sha256"]
                            or any(plan[field] != run.parameters.value()["config"][config_field]
                                   for field, config_field in (
                                       ("epochs", "epochs"), ("update_cap", "max_updates"),
                                       ("tbptt_advances", "tbptt_advances")))):
                        raise BoundaryError(
                            "local_training", "frozen_training_plan_binding_mismatch")
                    service._advance(path, identity, run_id=run_id, input_id=input_id,
                                     stage="training")
                elif kind == "event":
                    if not handshake or set(details) != {"event_id"} or not recorded.get("run_id"):
                        raise BoundaryError("local_training", "child_event_invalid")
                    event = store.get_manifest(digest(
                        details["event_id"], "local_training.event_id"))
                    info = event.parameters.value()
                    run = store.get_manifest(recorded["run_id"])
                    if (run.parameters.value().get("schema") != expected_run_schema
                            or event.kind != "run_event"
                            or event.producer != (attempt_producer if scoped else run.producer)
                            or scoped and info.get("details", {}).get("attempt_producer")
                            != attempt_producer.to_dict()
                            or event.parent("run") != run.artifact_id
                            or info.get("schema") != "stpd/run-event-v1"
                            or info.get("attempt") != attempt_id
                            or type(info.get("step")) is not int
                            or info["step"] <= recorded.get("_event_step", 0)):
                        raise BoundaryError("local_training", "child_event_invalid")
                    update: dict[str, Any] = {"_event_step": info["step"]}
                    event_details = info.get("details", {})
                    if info.get("kind") == "checkpoint":
                        cp_id = digest(event_details.get("checkpoint_id"),
                                       "local_training.checkpoint")
                        checkpoint = store.get_manifest(cp_id)
                        cp_info = checkpoint.parameters.value()
                        if (checkpoint.kind != "checkpoint"
                                or checkpoint.producer != event.producer
                                or cp_info.get("schema") != typed_recipe_checkpoint_schema(
                                    operation["recipe"])
                                or scoped and cp_info.get("attempt_producer")
                                != attempt_producer.to_dict()
                                or checkpoint.parent("run") != run.artifact_id
                                or checkpoint.parent("training_input") != recorded["input_id"]
                                or checkpoint.parameters.value().get("attempt") != attempt_id):
                            raise BoundaryError("local_training", "child_checkpoint_invalid")
                        update["checkpoint_id"] = cp_id
                        update["last_checkpoint_at"] = time.time()
                        update["checkpoint_progress"] = {
                            "checkpoint_id":cp_id, "optimizer_updates":cp_info["optimizer_updates"],
                            "boundaries":cp_info["boundary"], "epoch":cp_info["cursor"]["epoch"]}
                    counters = event_details.get("accepted_counters")
                    if counters is not None:
                        if (not isinstance(counters, dict) or set(counters) != {
                                "optimizer_updates", "label_uses", "observations", "boundaries",
                                "epoch", "whole_cohort_passes"}
                                or any(type(item) is not int or item < 0
                                       for item in counters.values())
                                or counters["optimizer_updates"] != event_details.get(
                                    "optimizer_updates")
                                or counters["boundaries"] != event_details.get("boundary")
                                or counters["epoch"] != event_details.get("cursor", {}).get("epoch")
                                or counters["whole_cohort_passes"] != counters["epoch"]):
                            raise BoundaryError("local_training", "child_progress_invalid")
                        prior_counters = recorded.get("accepted_counters", {})
                        if any(counters[key] < prior_counters.get(key, 0) for key in counters):
                            raise BoundaryError("local_training", "child_progress_regressed")
                        update["accepted_counters"] = counters
                        if info.get("kind") == "progress" and counters != prior_counters:
                            update["last_progress_at"] = time.time()
                    if "optimizer_updates" in event_details:
                        updates = event_details["optimizer_updates"]
                        if type(updates) is not int or not 0 <= updates <= 10000:
                            raise BoundaryError("local_training", "child_progress_invalid")
                        update["progress"] = {
                            "unit":"optimizer_update", "completed":updates,
                            "total":recorded.get("training_plan", {}).get(
                                "planned_optimizer_updates")}
                        if (counters is None and info.get("kind") == "progress"
                                and updates > recorded.get("progress", {}).get("completed", 0)):
                            update["last_progress_at"] = time.time()
                    service._advance(path, identity, **update)
                elif kind == "terminal":
                    keys = {"state", "run_id", "attempt_id", "checkpoint_id", "result_id",
                            "model_id", "event_step", "optimizer_updates", "attempt_seconds"}
                    if (not handshake or terminal is not None or set(details) != keys
                            or details["state"] not in {"completed", "paused", "cancelled"}
                            or details["run_id"] != recorded.get("run_id")
                            or details["attempt_id"] != attempt_id
                            or type(details["event_step"]) is not int
                            or details["event_step"] < recorded.get("_event_step", 0)
                            or type(details["optimizer_updates"]) is not int
                            or not 0 <= details["optimizer_updates"] <= 10000
                            or type(details["attempt_seconds"]) not in {float, int}
                            or not math.isfinite(details["attempt_seconds"])
                            or details["attempt_seconds"] < 0):
                        raise BoundaryError("local_training", "child_terminal_invalid")
                    digest(details["checkpoint_id"], "local_training.checkpoint_id")
                    for key in ("result_id", "model_id"):
                        if details["state"] == "completed":
                            digest(details[key], "local_training."+key)
                        elif details[key] is not None:
                            raise BoundaryError("local_training", "child_terminal_invalid")
                    self.validate_terminal(store, recorded, details)
                    terminal = details
                elif kind == "error":
                    if (set(details) != {"error_code", "exception_type"}
                            or any(not isinstance(item, str) or not 0 < len(item) <= 256
                                   for item in details.values())):
                        raise BoundaryError("local_training", "child_error_invalid")
                    failure = details
                    service._child_failure_diagnostic = dict(details)
                else:
                    raise BoundaryError("local_training", "child_channel_kind_invalid")

        def spawned() -> None:
            mark_started()
            with service._lock:
                current()
                service._advance(path, identity, child_spawned=True)

        def stop_requested() -> bool:
            with service._lock:
                value = current()
                if (retained_scratch_bytes(path, value, active_attempt=True) >
                        value["request"]["limits"]["scratch_bytes"]):
                    raise BoundaryError("local_training", "scratch_budget_exhausted")
                if value["requested_action"] != "cancel":
                    return False
                started_cancel: float | None = service._cancel_requested_monotonic
                if started_cancel is None:
                    service._cancel_requested_monotonic = time.monotonic()
                    return False
                return time.monotonic()-started_cancel >= 1.0

        def exited(exit_code: int, forced: bool, seconds: float) -> None:
            receipt = {"attempt_id": attempt_id, "exit_code": exit_code,
                       "forced": forced, "elapsed_seconds": seconds, "ended_at": time.time()}
            service._active_child_exit = receipt
            with service._lock:
                current()
                service._advance(path, identity, child_exit=receipt)

        started = service._attempt_started_monotonic
        if started is None:
            raise BoundaryError("local_training", "attempt_clock_required")
        remaining = (effective_training_limits(operation)["wall_seconds"]-
                     operation["elapsed_seconds"]-(time.monotonic()-started))
        if remaining <= 0:
            raise BoundaryError("local_training", "cumulative_budget_exhausted")
        command = [sys.executable, "-m", "spireagent.workbench.recipes.structured_child",
                   "--store", str(owner.store_dir), "--operation-file", str(path),
                   "--operation-id", identity, "--attempt-id", attempt_id,
                   "--child-lock", str(path.parent/operation["child_lock_name"]),
                   "--scratch-dir", str(path.parent/operation["scratch_name"]),
                   "--remaining-seconds", str(remaining)]
        environment = dict(os.environ)
        for name in ("STPD_HUB_ADMIN_TOKEN", "PYTHONPATH", "PYTHONHOME"):
            environment.pop(name, None)
        log = path.parent/("local-training-"+identity+"-"+attempt_id+".log")
        exit_code, _ = _private_child(command, log, environment, on_started=spawned,
                                     timeout_seconds=remaining, on_stdout_line=message,
                                     on_exited=exited, stop_requested=stop_requested)
        if exit_code:
            raise BoundaryError("local_training", failure["error_code"] if failure else
                                "structured_training_process_failed")
        if terminal is None:
            raise BoundaryError("local_training", "child_terminal_missing")
        with service._lock:
            current()
            completion = {"checkpoint_id": terminal["checkpoint_id"],
                          "progress": {"unit": "optimizer_update",
                                       "completed": terminal["optimizer_updates"],
                                       "total":current().get("training_plan", {}).get(
                                           "planned_optimizer_updates")}}
            if terminal["state"] == "completed":
                completion.update(result_id=terminal["result_id"], model_id=terminal["model_id"],
                                  evaluation_status="not_run", stage="completed")
            service._finish_attempt(path, owner, identity, expected_attempt_id=attempt_id,
                                    status=terminal["state"], **completion)
        if terminal["state"] == "completed":
            from spireagent.storage.registry import SQLiteRegistry, sync_registry

            _, _, registry_path = service._selected()
            sync_registry(store, SQLiteRegistry(registry_path))
