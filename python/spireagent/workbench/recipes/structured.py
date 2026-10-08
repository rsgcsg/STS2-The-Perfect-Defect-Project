"""Trusted structured workload composition over existing Store/Reporter/curation.

STPD verifies numeric checkpoints/results. The application owns source admission,
use reservations, attempt publication fences, control and cumulative wall budget.
"""

from __future__ import annotations

import time
from typing import Any, Literal, cast

from spireagent.json_boundary import BoundaryError
from spireagent.source import source_identity
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ROOT


def verify_resume_checkpoint(store: Any, operation: dict[str, Any], checkpoint_id: Any) -> None:
    checkpoint = store.get_manifest(checkpoint_id)
    run = store.get_manifest(operation["run_id"])
    info = checkpoint.parameters.value()
    if (checkpoint.kind != "checkpoint" or checkpoint.producer != run.producer
            or checkpoint.parent("run") != run.artifact_id
            or checkpoint.parent("training_input") != operation["input_id"]
            or info.get("schema") != "stpd/structured-m2-training-checkpoint-v2"):
        raise BoundaryError("local_training", "checkpoint_run_identity_mismatch")
    prior = {item["attempt_id"] for item in operation["attempts"]
             if item["writer_terminal"]}
    if operation["writer_terminal"]:
        prior.add(operation["attempt_id"])
    if info.get("attempt") not in prior:
        raise BoundaryError("local_training", "checkpoint_writer_terminal_required")
    # A published checkpoint absent its owning event is an uncertain publication,
    # not a selectable recovery point.
    events = ObjectStoreRunReporter(store, store.blobs).events(run.artifact_id)
    if not any(event.parameters.value().get("kind") == "checkpoint"
               and event.parameters.value().get("attempt") == info["attempt"]
               and event.parameters.value().get("details", {}).get("checkpoint_id")
               == checkpoint_id for event in events):
        raise BoundaryError("local_training", "checkpoint_event_required")


class AttemptFence:
    def __init__(self, service: Any, path: Any, owner: Any,
                 operation_id: str, attempt_id: str) -> None:
        self.service, self.path, self.owner = service, path, owner
        self.operation_id, self.attempt_id = operation_id, attempt_id
        self.store: Any = None

    def assert_current(self, run_id: str | None, operation_id: str, attempt_id: str) -> None:
        with self.service._lock:
            value = self.service._read(self.path, self.owner.identity)
            if (operation_id != self.operation_id or attempt_id != self.attempt_id
                    or value.get("operation_id") != operation_id
                    or value.get("attempt_id") != attempt_id
                    or value.get("status") != "pending"
                    or value.get("writer_terminal")
                    or self.service._active_attempt != attempt_id):
                raise BoundaryError("local_training", "attempt_fence_lost")
            if run_id is not None and value.get("run_id") != run_id:
                raise BoundaryError("local_training", "attempt_run_binding_mismatch")

    def authorize_resume(self, run_id: str, attempt_id: str, checkpoint_id: str) -> None:
        self.assert_current(run_id, self.operation_id, attempt_id)
        value = self.service._read(self.path, self.owner.identity)
        if (value.get("mode") != "resume" or value.get("resume_checkpoint_id") != checkpoint_id
                or not value["attempts"] or not value["attempts"][-1]["writer_terminal"]):
            raise BoundaryError("local_training", "prior_writer_terminal_required")
        verify_resume_checkpoint(self.store, value, checkpoint_id)

    def requested_action(self) -> Literal["continue", "pause", "cancel"]:
        self.assert_current(None, self.operation_id, self.attempt_id)
        value = self.service._read(self.path, self.owner.identity)
        limit = value["request"]["limits"]["wall_seconds"]
        if value["elapsed_seconds"] + max(0, time.time()-value["attempt_started_at"]) >= limit:
            return "pause"
        return cast(Literal["continue", "pause", "cancel"], value["requested_action"])


class FencedStore:
    def __init__(self, store: Any, fence: AttemptFence) -> None:
        self.store, self.fence = store, fence

    def __getattr__(self, name: str) -> Any:
        return getattr(self.store, name)

    def put_payload(self, *args: Any, **kwargs: Any) -> Any:
        self.fence.assert_current(None, self.fence.operation_id, self.fence.attempt_id)
        return self.store.put_payload(*args, **kwargs)

    def publish(self, *args: Any, **kwargs: Any) -> Any:
        self.fence.assert_current(None, self.fence.operation_id, self.fence.attempt_id)
        return self.store.publish(*args, **kwargs)


class AttemptReporter:
    def __init__(self, service: Any, path: Any, owner: Any, fence: AttemptFence,
                 reporter: ObjectStoreRunReporter) -> None:
        self.service, self.path, self.owner = service, path, owner
        self.fence, self.reporter = fence, reporter

    def __getattr__(self, name: str) -> Any:
        return getattr(self.reporter, name)

    def emit(self, event: Any) -> str:
        with self.service._lock:
            self.fence.assert_current(event.parent("run"), self.fence.operation_id,
                                      self.fence.attempt_id)
            identity = self.reporter.emit(event)
            info = event.parameters.value()
            details = info.get("details", {})
            updates: dict[str, Any] = {"updated_at": time.time()}
            if info.get("kind") == "checkpoint":
                updates["checkpoint_id"] = details["checkpoint_id"]
            if "optimizer_updates" in details:
                updates["last_progress_at"] = time.time()
                updates["progress"] = {"unit": "optimizer_update",
                                       "completed": details["optimizer_updates"], "total": None}
            self.service._advance(self.path, self.fence.operation_id, **updates)
            return identity

    def complete(self, result: Any) -> str:
        with self.service._lock:
            self.fence.assert_current(result.parent("run"), self.fence.operation_id,
                                      self.fence.attempt_id)
            return self.reporter.complete(result)


class StructuredRecipeAdapter:
    def preflight(self, store: Any, owner: Any, source_id: str) -> Any:
        from stpd.fullrun.structured_sequences import (
            MAX_SOURCE_BYTES,
            SOURCE_SCHEMA,
            parse_structured_dataset,
        )

        source = store.get_manifest(source_id)
        info = source.parameters.value()
        payload = source.payload("source")
        if (source.kind != "dataset" or info.get("schema") != SOURCE_SCHEMA
                or payload.size > MAX_SOURCE_BYTES):
            raise BoundaryError("local_training", "structured_source_manifest_required")
        dataset = parse_structured_dataset(b"".join(store.read_payload(payload)))
        if (dataset.source_sha256 != payload.sha256
                or info.get("source_sha256") != dataset.source_sha256
                or info.get("source_kind") != dataset.source_kind):
            raise BoundaryError("local_training", "structured_source_identity_mismatch")
        # Synthetic fixtures exercise this actual application path without
        # granting Agent/Human provenance or runtime qualification. A production
        # Agent source needs its owning typed verifier and immutable report join.
        if dataset.source_kind != "synthetic" or info.get("qualification") != "synthetic_fixture":
            raise BoundaryError("local_training", "structured_source_verifier_required")
        runs = {run.run_id for run in dataset.runs}
        if owner.ledger.dataset(source_id) != ("training", runs):
            raise BoundaryError("local_training", "training_claim_mismatch")
        if (not owner.ledger.exact_source_ready(source_id)
                or owner.ledger.source_runs(source_id) != runs):
            raise BoundaryError("local_training", "source_index_incomplete")
        return dataset

    def execute(self, service: Any, path: Any, identity: str, owner: Any,
                store: Any, mark_started: Any) -> None:
        from stpd.structured_workload_contracts import (
            StructuredTrainingConfig,
            StructuredWorkloadRequest,
        )
        from stpd.workers.structured_execution import (
            execute_structured_workload,
            prepare_structured_workload,
        )

        operation = service._read(path, owner.identity)
        dataset = self.preflight(store, owner, operation["dataset_id"])
        runs = {run.run_id for run in dataset.runs}
        source_id = operation["dataset_id"]
        if operation["mode"] == "start":
            owner.ledger.use_source(source_id, "training", identity)
            owner.ledger.use(runs, "training", identity)
        owner.ledger.require_training_use(source_id, {source_id}, runs, identity)
        fence = AttemptFence(service, path, owner, identity, operation["attempt_id"])
        fence.store = store
        fenced_store = FencedStore(store, fence)
        with service._lock:
            fence.assert_current(None, identity, operation["attempt_id"])
            service._advance(path, identity, use_state="reserved", stage="preparing_run")
        if operation["mode"] == "start":
            run = prepare_structured_workload(
                fenced_store, dataset, source_identity(ROOT),
                StructuredTrainingConfig(**operation["request"]["config"]),
                operation_id=identity, source_id=source_id)
            with service._lock:
                fence.assert_current(None, identity, operation["attempt_id"])
                service._advance(path, identity, run_id=run.artifact_id,
                                 input_id=run.parent("training_input"), stage="training")
        else:
            run = store.get_manifest(operation["run_id"])
        request = StructuredWorkloadRequest(
            run.artifact_id, run.parent("training_input"), identity, operation["attempt_id"],
            mode=operation["mode"],
            resume_checkpoint_id=(operation.get("resume_checkpoint_id")
                                  if operation["mode"] == "resume" else None),
            checkpoint_every_boundaries=1)
        reporter = AttemptReporter(service, path, owner, fence,
                                   ObjectStoreRunReporter(fenced_store, store.blobs))
        mark_started()
        result = execute_structured_workload(fenced_store, reporter, request, run.producer,
                                            authority=fence, control=fence)
        with service._lock:
            fence.assert_current(run.artifact_id, identity, operation["attempt_id"])
            completion = {"checkpoint_id": result.checkpoint_id,
                          "progress": {"unit": "optimizer_update",
                                       "completed": result.optimizer_updates, "total": None}}
            if result.state == "completed":
                completion.update(result_id=result.result_id, model_id=result.model_id,
                                  evaluation_status="completed", stage="completed")
                # The immutable verified completion is recorded before indexing;
                # an index failure cannot cause a second numerical attempt.
            service._finish_attempt(path, owner, identity, expected_attempt_id=fence.attempt_id,
                                    status=result.state, **completion)
        if result.state == "completed":
            from spireagent.storage.registry import SQLiteRegistry, sync_registry

            _, _, registry_path = service._selected()
            sync_registry(store, SQLiteRegistry(registry_path))
