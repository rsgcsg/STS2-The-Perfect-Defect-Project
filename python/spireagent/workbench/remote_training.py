"""Application mapping to the existing fenced Scheduler; no second task/use ledger."""
from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from spireagent.artifact_contracts import Manifest
from spireagent.compute import ComputeProvider, ComputeTermination
from spireagent.hub.database import Operations
from spireagent.json_boundary import BoundaryError, digest, object_fields
from spireagent.source import source_identity
from spireagent.ssh_compute import provider_from_target
from spireagent.storage.store import copy_artifact
from spireagent.workbench.developer import ROOT, ProjectConfig
from spireagent.workbench.p1_recording_storage import open_peer

if TYPE_CHECKING:
    from spireagent.hub.scheduler import Scheduler


class RemoteTrainingService:
    def __init__(self, config: ProjectConfig, select: Callable[[], Any], *,
                 provider: ComputeProvider | None = None) -> None:
        self.config, self.select, self._provider = config, select, provider
        self._injected_provider = provider is not None
        self._lock = threading.RLock()
        self._scheduler: Scheduler | None = None
        self._operations: Operations | None = None
        self._compute_store: Any = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None
        self._progress_lock = threading.Lock()
        self._progress_reporters: dict[str, Any] = {}

    def capabilities(self) -> dict[str, Any]:
        from spireagent.workbench.trusted_recipes import P1_BASELINE_RECIPE, P1_MSTD_RECIPE

        deployment = self.config.remote_compute
        configured = deployment is not None
        return {"schema": "spireagent/remote-training-capabilities-1",
                "deployment": "local_scheduler", "configured": configured,
                "available": (deployment is not None and not self.config.hub_url
                              and (self._injected_provider or
                                   deployment.compute_store.get("kind") == "s3")),
                "provider": ("trusted_injected_engineering" if self._injected_provider else
                             "ssh" if deployment and self._target_schema() ==
                             "spireagent/ssh-target-1" else "modal"),
                "recipes": [
                    {"recipe_id": P1_MSTD_RECIPE, "devices": ["cpu", "cuda:index"]},
                    {"recipe_id": P1_BASELINE_RECIPE, "devices": ["cpu"],
                     "algorithms": ["random", "frequency", "linear"]},
                ],
                "supported_actions": (["status", "progress"] if deployment and
                                      deployment.observation_only else
                                      ["prepare", "start", "status", "progress", "cancel",
                                       "reconcile", "resume", "auto-continue",
                                       "stop-continuation"]),
                "observation_only": deployment.observation_only if deployment else False,
                "live_pause_supported": False, "fixed_stop_after_supported": True,
                "automatic_retry": False, "gpu_qualification": "not_established",
                "compute_store": dict(deployment.compute_store) if deployment else None,
                "remote_store_access": "prepare_verifies_inputs_only_not_worker_secret_injection"}

    def _context(self) -> tuple[Any, Any, ComputeProvider, Any]:
        deployment = self.config.remote_compute
        if deployment is None:
            raise BoundaryError("remote_training", "explicit_scheduler_deployment_required")
        if deployment.observation_only:
            raise BoundaryError("remote_training", "observation_only_original_writer_required")
        if self.config.hub_url:
            raise BoundaryError("remote_training", "configured_hub_requires_hub_scheduler_route")
        if not self._injected_provider and deployment.compute_store.get("kind") != "s3":
            raise BoundaryError("remote_training", "shared_compute_store_required")
        owner, store, _ = self.select()
        if self._provider is None:
            self._provider = provider_from_target(json.loads(deployment.target_path.read_bytes()))
        provider = self._provider
        if provider.target.producer != (deployment.execution_producer or source_identity(ROOT)):
            raise BoundaryError("remote_training", "target_source_lock_mismatch")
        # Ops must never share the private allocation/use database.
        if deployment.operations_path.resolve() == owner.path.resolve():
            raise BoundaryError("remote_training", "separate_operations_database_required")
        if self._compute_store is None:
            self._compute_store = open_peer(deployment.compute_store)
        return owner, store, provider, self._compute_store

    def _target_schema(self) -> str | None:
        deployment = self.config.remote_compute
        if deployment is None:
            return None
        try:
            return str(json.loads(deployment.target_path.read_bytes()).get("schema"))
        except (OSError, ValueError, AttributeError):
            return None

    def _ready(self) -> tuple[Any, Any, ComputeProvider, Operations, Scheduler]:
        owner, store, provider, peer = self._context()
        deployment = self.config.remote_compute
        assert deployment is not None
        if self._scheduler is None:
            from spireagent.hub.scheduler import Scheduler

            self._operations = Operations(deployment.operations_path)

            def record_use(run: Manifest) -> None:
                from spireagent.workbench.p1_training_data import record_p1_training_use
                from spireagent.workbench.remote_training_storage import verify_training_run

                current_owner, current_store, _ = self.select()
                workload = verify_training_run(current_store, run.artifact_id,
                                               provider.target.producer)
                record_p1_training_use(current_owner, current_store, workload.snapshot.artifact_id,
                                       run.parameters.value()["operation_id"])

            self._scheduler = Scheduler(self._operations, peer, provider,
                                        budget_limit=deployment.budget_limit,
                                        record_training_use=record_use)
        assert self._operations is not None
        return owner, store, provider, self._operations, self._scheduler

    def _terminated_resume_checkpoint(
        self, row: dict[str, Any], checkpoint_id: str, provider: ComputeProvider, ops: Operations,
    ) -> None:
        """Admit an explicitly selected durable checkpoint from this ended attempt."""
        from spireagent.storage.run_reporter import ObjectStoreRunReporter
        from stpd.cloud_jobs.contracts import ComputeRequest
        from stpd.cloud_jobs.execution import preflight_training_request

        saved = ops.compute_state(row["id"])
        if (row["status"] not in {"failed", "cancelled"} or saved is None
                or saved["handle"] is None or saved["attempt_id"] != row["attempt_id"]
                or saved["fence"] != row["fence"] or saved["phase"] != row["status"]):
            raise BoundaryError("remote_training", "confirmed_original_termination_required")
        termination = ComputeTermination.decode(json.loads(row["result"] or "null"))
        handle = provider.restore_handle(saved["handle"])
        termination.bind(handle)
        request = ComputeRequest.decode(saved["request"])
        if (request != handle.request or request.kind != "training"
                or request.input_id != row["input_id"] or request.attempt_id != row["attempt_id"]
                or saved["target_id"] != provider.target.target_id
                or row["provider_ref"] != handle.call_id):
            raise BoundaryError("remote_training", "original_attempt_binding_mismatch")
        # Reuse the installed family verifier for the complete checkpoint payload;
        # numerical/GPU exact restore remains independently owned by the worker.
        from dataclasses import replace

        peer = self._compute_store
        preflight_training_request(peer, replace(request, resume=checkpoint_id))
        checkpoint = peer.get_manifest(checkpoint_id)
        if checkpoint.parameters.value().get("attempt") != row["attempt_id"]:
            raise BoundaryError("remote_training", "original_attempt_checkpoint_required")
        events = ObjectStoreRunReporter(peer, peer.blobs, read_concurrency=8).events(
            request.input_id)
        for event in events:
            info = event.parameters.value()
            details = info.get("details", {})
            if (event.producer == request.producer
                    and {p.role for p in event.parents} == {"run"}
                    and info.get("kind") == "checkpoint"
                    and info.get("attempt") == request.attempt_id
                    and type(info.get("step")) is int and info["step"] > 0
                    and isinstance(details, dict) and details.get("checkpoint_id") == checkpoint_id
                    and details.get("compute_request_id") == request.request_id
                    and details.get("compute_attempt_id") == request.attempt_id
                    and details.get("optimizer_updates") == checkpoint.parameters.value().get(
                        "optimizer_updates")):
                return
        raise BoundaryError("remote_training", "bound_durable_checkpoint_event_required")

    def _launch_loop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="application-remote-scheduler",
                                        daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with self._lock:
                    _, _, _, _, scheduler = self._ready()
                    self._continue_training(time.time())
                    scheduler.tick(time.time())
                    self._continue_training(time.time())
                    self._error = None
            except (BoundaryError, OSError, ValueError) as error:
                self._error = (error.code if isinstance(error, BoundaryError)
                               else type(error).__name__)
                return  # A failed owner never silently restarts or resubmits.
            self._stop.wait(0.5)

    def _continue_training(self, now: float) -> None:
        from spireagent.hub import training_continuation as continuation
        from stpd.cloud_jobs.contracts import ComputeReceipt, ComputeRequest

        _, _, provider, ops, _ = self._ready()
        policy = continuation.read(ops)
        if policy is None or not policy["enabled"]:
            return
        job = self._job(ops, policy["current_job_id"])
        if (provider.target.target_id != policy["target_id"]
                or provider.target.producer.to_dict() != policy["producer"]):
            if job["status"] == "queued":
                ops.cancel(job["id"])
            continuation.stop(ops, policy["root_job_id"], "continuation_identity_mismatch")
            return
        if job["status"] == "queued" and now + policy["max_seconds"] > policy["deadline_unix"]:
            ops.cancel(job["id"])
            continuation.stop(ops, policy["root_job_id"], "deadline_exhausted")
            return
        if job["status"] in {"completed", "failed", "cancelled", "cancelling"}:
            continuation.stop(ops, policy["root_job_id"], job["status"])
            return
        if job["status"] != "paused":
            return  # Unknown/running/queued is observed by the same Scheduler, never retried.
        try:
            saved = ops.compute_state(job["id"])
            if (saved is None or saved["phase"] != "paused" or saved["handle"] is None
                    or saved["attempt_id"] != job["attempt_id"]
                    or saved["fence"] != job["fence"]
                    or saved["target_id"] != policy["target_id"]
                    or provider.target.target_id != policy["target_id"]
                    or provider.target.producer.to_dict() != policy["producer"]):
                raise BoundaryError("remote_training", "continuation_identity_mismatch")
            request = ComputeRequest.decode(saved["request"])
            handle = provider.restore_handle(saved["handle"])
            receipt = ComputeReceipt.decode(json.loads(job["result"]))
            receipt.bind(request)
            if (handle.request != request or handle.call_id != job["provider_ref"]
                    or request.input_id != policy["run_id"] or receipt.state != "paused"
                    or request.producer != provider.target.producer):
                raise BoundaryError("remote_training", "continuation_identity_mismatch")
            # Only Scheduler's accepted, fully validated PAUSED receipt reaches here.
            # Next submission still runs the existing full checkpoint/use preflight.
            assert receipt.checkpoint_id is not None
            deployment = self.config.remote_compute
            assert deployment is not None
            continuation.advance(ops, policy, job, receipt.checkpoint_id, now=now,
                                 reserved_units=deployment.reserved_units,
                                 budget_limit=deployment.budget_limit)
        except (BoundaryError, OSError, ValueError) as error:
            code = error.code if isinstance(error, BoundaryError) else type(error).__name__
            continuation.stop(ops, policy["root_job_id"], code)

    def _configure_continuation(self, action: str, body: object) -> dict[str, Any]:
        from spireagent.hub import training_continuation as continuation

        _, _, provider, ops, _ = self._ready()
        fields = {"job_id", "expected_attempt_id"}
        if action == "auto-continue":
            fields |= {"deadline_unix", "max_new_segments", "max_seconds"}
        obj = object_fields(body, fields, "remote_training." + action)
        job = self._job(ops, obj["job_id"])
        if job["attempt_id"] != obj["expected_attempt_id"] or job["attempt_id"] is None:
            raise BoundaryError("remote_training", "stale_remote_attempt")
        if action == "stop-continuation":
            policy = continuation.read(ops)
            if policy is None or job["id"] not in {
                    policy["root_job_id"], policy["current_job_id"]}:
                raise BoundaryError("remote_training", "continuation_not_found")
            current = self._job(ops, policy["current_job_id"])
            if current["status"] == "queued":
                ops.cancel(current["id"])
            continuation.stop(ops, policy["root_job_id"], "stopped_by_operator")
        else:
            deadline = obj["deadline_unix"]
            if (type(deadline) not in {int, float} or not math.isfinite(deadline)
                    or deadline <= time.time()
                    or type(obj["max_new_segments"]) is not int
                    or not 1 <= obj["max_new_segments"] <= 64):
                raise BoundaryError("remote_training", "invalid_continuation_limits")
            self._limits({"max_seconds": obj["max_seconds"], "stop_after": None},
                         provider.target.timeout_seconds)
            if time.time() + obj["max_seconds"] > deadline:
                raise BoundaryError("remote_training", "continuation_deadline_insufficient")
            continuation.arm(ops, job, target_id=provider.target.target_id,
                             producer=provider.target.producer.to_dict(), deadline=deadline,
                             max_new_segments=obj["max_new_segments"],
                             max_seconds=obj["max_seconds"])
        self._launch_loop()
        return self.status()

    def start_loop(self) -> None:
        if (self.config.remote_compute is not None and not self.config.hub_url
                and not self.config.remote_compute.observation_only):
            with self._lock:
                self._ready()
                self._launch_loop()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
            if self._thread.is_alive():
                raise BoundaryError("remote_training", "scheduler_shutdown_pending")
        if self._scheduler is not None:
            self._scheduler.close()

    @staticmethod
    def _job(ops: Operations, job_id: object) -> dict[str, Any]:
        identity = digest(job_id, "remote_training.job_id", length=32)
        row = next((r for r in ops.jobs() if r["id"] == identity), None)
        if row is None:
            raise BoundaryError("remote_training", "job_not_found")
        return row

    def preparation_status(self, intent_id: object) -> dict[str, Any]:
        """Observe only existing local facts, even while transfer holds its write lock."""
        from stpd.p1.baseline_spec import RUN_SCHEMA as BASELINE_RUN_SCHEMA
        from stpd.p1.spec import RUN_SCHEMAS

        intent = digest(intent_id, "remote_training.intent_id", length=32)
        if self.config.hub_url:
            raise BoundaryError("remote_training", "configured_hub_requires_hub_scheduler_route")
        deployment = self.config.remote_compute
        if deployment is None:
            raise BoundaryError("remote_training", "explicit_scheduler_deployment_required")
        _, local, _ = self.select()
        runs = [manifest for identity in local.manifest_ids()
                if (manifest := local.get_manifest(identity)).parameters.value().get("schema")
                in {*RUN_SCHEMAS, BASELINE_RUN_SCHEMA}
                and manifest.parameters.value().get("operation_id") == intent]
        jobs = []
        if deployment.operations_path.is_file():
            operations = self._operations or Operations(deployment.operations_path)
            jobs = [{"job_id": row["id"], "run_id": row["input_id"], "status": row["status"]}
                    for row in operations.jobs() if row["input_id"] in
                    {manifest.artifact_id for manifest in runs}]
        return {"schema": "spireagent/remote-preparation-observation-1", "intent_id": intent,
                "local_runs": [{"run_id": m.artifact_id, "producer": m.producer.to_dict()}
                               for m in runs], "jobs": jobs,
                "transfer_completion": "not_inferred_from_local_run",
                "provider_queried": False, "peer_queried": False, "automatic_retry": False}

    def status(self, job_id: object | None = None) -> dict[str, Any]:
        """Observe durable local jobs without waiting for Scheduler network writes."""
        if self.config.hub_url:
            raise BoundaryError("remote_training", "configured_hub_requires_hub_scheduler_route")
        deployment = self.config.remote_compute
        if deployment is None:
            raise BoundaryError("remote_training", "explicit_scheduler_deployment_required")
        # Like preparation_status, this is an observation of existing local Ops
        # facts. It must not initialize a Scheduler or query provider/Store state.
        ops = (self._operations or Operations(deployment.operations_path)
               if deployment.operations_path.is_file() else None)
        if ops is None:
            if job_id is not None:
                digest(job_id, "remote_training.job_id", length=32)
                raise BoundaryError("remote_training", "job_not_found")
            jobs = []
        else:
            jobs = ops.jobs() if job_id is None else [self._job(ops, job_id)]
        visible = [{k: row[k] for k in ("id", "kind", "input_id", "status", "attempt_id",
                   "fence", "max_seconds", "reserved_units", "provider_ref", "result")}
                   for row in jobs]
        for row in visible:
            row["result"] = json.loads(row["result"]) if row["result"] else None
            compute = ops.compute_state(row["id"]) if ops is not None else None
            row["compute_phase"] = compute["phase"] if compute else None
            row["compute_error"] = compute["last_error"] if compute else None
        from spireagent.hub.training_continuation import read as read_continuation

        return {"schema": "spireagent/remote-training-status-1", "jobs": visible,
                "continuation": read_continuation(ops) if ops is not None else None,
                "scheduler_error": self._error, "automatic_retry": False,
                **({"observation_only": True} if deployment.observation_only else {})}

    def progress(self, job_id: object, *, after_step: int = 0) -> dict[str, Any]:
        """Explicit peer read of durable RunReporter events; never create/retry a task."""
        if not self._progress_lock.acquire(blocking=False):
            raise BoundaryError("remote_training", "progress_read_in_progress")
        try:
            return self._read_progress(job_id, after_step=after_step)
        finally:
            self._progress_lock.release()

    def _read_progress(self, job_id: object, *, after_step: int) -> dict[str, Any]:
        from spireagent.storage.run_reporter import ObjectStoreRunReporter
        from spireagent.workbench.dashboard import _safe_value

        if type(after_step) is not int or after_step < 0:
            raise BoundaryError("remote_training", "invalid_event_cursor")
        row = self.status(job_id)["jobs"][0]
        deployment = self.config.remote_compute
        assert deployment is not None
        reporter = self._progress_reporters.get(row["input_id"])
        if reporter is None:
            peer = open_peer(deployment.compute_store)
            reporter = ObjectStoreRunReporter(peer, peer.blobs, cache_events=True,
                                               read_concurrency=8)
            self._progress_reporters[row["input_id"]] = reporter
        observed_store = reporter.store
        run = observed_store.get_manifest(row["input_id"])
        training = observed_store.get_manifest(run.parent("training_input"))
        planned_steps = training.parameters.value().get("planned_steps")
        events = reporter.events(run.artifact_id)
        facts = []
        for event in events:
            info = event.parameters.value()
            if (event.producer != run.producer or type(info.get("step")) is not int
                    or not isinstance(info.get("kind"), str)
                    or not isinstance(info.get("details"), dict)):
                raise BoundaryError("remote_training", "run_event_identity_mismatch")
            facts.append({"event_id": event.artifact_id, "event_step": info["step"],
                          "attempt_id": info.get("attempt"), "kind": info["kind"],
                          "details": _safe_value(info["details"])})
        selected = [event for event in facts if event["event_step"] > after_step]
        def latest(kind: str) -> dict[str, Any] | None:
            return next((event for event in reversed(facts) if event["kind"] == kind), None)

        return {"schema": "spireagent/remote-training-progress-1", "job_id": row["id"],
                "run_id": run.artifact_id, "attempt_id": row["attempt_id"],
                "producer": run.producer.to_dict(), "job_status": row["status"],
                "planned_steps": planned_steps,
                "event_count": len(facts), "through_step": max(
                    (event["event_step"] for event in facts), default=0),
                "events": selected[-200:], "history_truncated": len(selected) > 200,
                "latest_progress": latest("progress"), "latest_checkpoint": latest("checkpoint"),
                "recorded_errors": [event for event in facts
                                    if event["kind"] in {"failed", "error"}][-20:],
                "result": row["result"], "provider_queried": False,
                "automatic_retry": False, "observation": "persisted_run_events_only"}

    def prepare(self, body: object) -> dict[str, Any]:
        from spireagent.workbench.p1_training_data import (
            record_p1_training_use,
            require_p1_snapshot,
        )
        from spireagent.workbench.remote_training_storage import transfer_p1_input
        from stpd.p1.spec import MStdConfig
        from stpd.p1.workload import prepare_p1_run

        owner, store, provider, _ = self._context()
        # A control upgrade may resume an existing frozen Run. New Run/Input
        # construction executes here and must retain this code's actual provenance.
        if provider.target.producer != source_identity(ROOT):
            raise BoundaryError("remote_training", "new_run_requires_current_execution_source")
        deployment = self.config.remote_compute
        assert deployment is not None
        recipe = body.get("recipe_id", "p1-mstd-N-v1") if isinstance(body, dict) else None
        fields = {"intent_id", "source_id", "config", "max_seconds", "stop_after"}
        optional = {key for key in ("recipe_id", "execution_policy", "initial_checkpoint")
                    if isinstance(body, dict) and key in body}
        obj = object_fields(body, fields | optional,
                            "remote_training.start")
        intent = digest(obj["intent_id"], "remote_training.intent_id", length=32)
        snapshot = digest(obj["source_id"], "remote_training.source_id")
        from spireagent.workbench.trusted_recipes import P1_BASELINE_RECIPE, P1_MSTD_RECIPE

        config: Any
        prepare_run: Callable[..., Manifest]
        if recipe == P1_BASELINE_RECIPE:
            from stpd.p1.baseline_spec import BaselineConfig
            from stpd.p1.baseline_workload import prepare_baseline_run

            config = BaselineConfig.decode(obj["config"])
            prepare_run = prepare_baseline_run
        elif recipe == P1_MSTD_RECIPE:
            config = MStdConfig.decode(obj["config"])
            prepare_run = prepare_p1_run
        else:
            raise BoundaryError("remote_training", "unsupported_training_recipe")
        execution_options: dict[str, Any] = {}
        if "execution_policy" in obj:
            from spireagent.workbench.trusted_recipes import checked_recipe_execution_policy

            execution_options["execution_policy"] = checked_recipe_execution_policy(
                recipe, obj["execution_policy"])
        if "initial_checkpoint" in obj:
            if recipe != P1_MSTD_RECIPE:
                raise BoundaryError("remote_training", "derivation_requires_M_std_recipe")
            execution_options["initial_checkpoint"] = digest(
                obj["initial_checkpoint"], "remote_training.initial_checkpoint")
        target = provider.target
        gpu = getattr(target, "gpu", None)
        if ((config.device == "cpu") != (gpu == "none")):
            raise BoundaryError("remote_training", "explicit_target_device_mismatch")
        self._limits(obj, target.timeout_seconds)
        require_p1_snapshot(owner, store, snapshot)
        # Vocabulary fitting itself discloses training examples; reserve use first.
        record_p1_training_use(owner, store, snapshot, intent)
        run = prepare_run(store, snapshot, target.producer, config, intent, **execution_options)
        scratch = self.config.state_dir / "remote-training-transfer"
        scratch.mkdir(parents=True, exist_ok=True)
        facts = transfer_p1_input(store, deployment.compute_store, run.artifact_id,
                                  target.producer, scratch=scratch)
        return {"schema": "spireagent/remote-training-prepared-1", "state": "prepared",
                "run_id": run.artifact_id, "input": facts, "enqueued": False,
                "provider_submitted": False, "gpu_qualification": "not_established"}

    def command(self, action: str, body: object) -> dict[str, Any]:
        if self.config.remote_compute is not None and self.config.remote_compute.observation_only:
            raise BoundaryError("remote_training", "observation_only_original_writer_required")
        if action == "pause":
            raise BoundaryError("remote_training", "live_pause_unsupported_use_fixed_stop_after")
        with self._lock:
            if action in {"auto-continue", "stop-continuation"}:
                return self._configure_continuation(action, body)
            if action == "prepare":
                return self.prepare(body)
            if action == "start":
                prepared = self.prepare(body)
                assert isinstance(body, dict)  # prepare checked the exact request grammar.
                obj = body
                _, _, _, ops, _ = self._ready()
                deployment = self.config.remote_compute
                assert deployment is not None
                job_id = ops.enqueue("training", prepared["run_id"], obj["intent_id"],
                                     max_seconds=obj["max_seconds"],
                                     reserved_units=deployment.reserved_units,
                                     budget_limit=deployment.budget_limit,
                                     options={"stop_after": obj["stop_after"]})
            elif action in {"cancel", "reconcile", "resume"}:
                owner, store, provider, ops, _ = self._ready()
                deployment = self.config.remote_compute
                assert deployment is not None
                fields = {"job_id", "expected_attempt_id"}
                if action == "resume":
                    fields |= {"intent_id", "checkpoint_id", "max_seconds", "stop_after"}
                obj = object_fields(body, fields, "remote_training." + action)
                row = self._job(ops, obj["job_id"])
                if row["attempt_id"] != obj["expected_attempt_id"]:
                    raise BoundaryError("remote_training", "stale_remote_attempt")
                job_id = row["id"]
                if action == "cancel":
                    ops.cancel(job_id)
                elif action == "reconcile":
                    # Existing Scheduler polls the saved exact handle; no guessed stop evidence.
                    self._ready()[4].tick(time.time())
                    selected = self._job(ops, job_id)
                    result = json.loads(selected["result"]) if selected["result"] else {}
                    if selected["status"] in {"paused", "completed"}:
                        scratch = self.config.state_dir / "remote-training-transfer"
                        scratch.mkdir(parents=True, exist_ok=True)
                        identity = result.get("output_id") or result.get("checkpoint_id")
                        if identity:
                            from spireagent.workbench.p1_recording_storage import storage_budget

                            # Receiving a result can include its immutable input
                            # ancestry. Budget that closure before creating bytes;
                            # copy_artifact reuses verified local payloads.
                            storage_budget(self._compute_store, identity, scratch=scratch,
                                           destination=store.blobs.root, destination_store=store)
                            copy_artifact(self._compute_store, store, identity, scratch=scratch)
                else:
                    from spireagent.hub.training_continuation import read as read_continuation

                    policy = read_continuation(ops)
                    if policy and policy["enabled"] and policy["run_id"] == row["input_id"]:
                        raise BoundaryError("remote_training",
                                            "manual_resume_requires_stop_continuation")
                    intent = digest(obj["intent_id"], "remote_training.intent_id", length=32)
                    checkpoint = digest(obj["checkpoint_id"], "remote_training.checkpoint_id")
                    result = json.loads(row["result"]) if row["result"] else {}
                    if row["status"] == "paused":
                        if result.get("checkpoint_id") != checkpoint:
                            raise BoundaryError("remote_training",
                                                "exact_paused_checkpoint_required")
                    else:
                        self._terminated_resume_checkpoint(row, checkpoint, provider, ops)
                    if intent == row["request_key"]:
                        raise BoundaryError("remote_training", "new_remote_intent_required")
                    self._limits(obj, provider.target.timeout_seconds)
                    job_id = ops.enqueue("training", row["input_id"], intent,
                                         max_seconds=obj["max_seconds"],
                                         reserved_units=deployment.reserved_units,
                                         budget_limit=deployment.budget_limit,
                                         options={"resume": checkpoint,
                                                  "stop_after": obj["stop_after"]})
            else:
                raise BoundaryError("remote_training", "unsupported_training_action")
            self._launch_loop()
            return self.status(job_id)

    @staticmethod
    def _limits(obj: dict[str, Any], timeout: int) -> None:
        if type(obj["max_seconds"]) is not int or not timeout <= obj["max_seconds"] <= 86400:
            raise BoundaryError("remote_training", "target_timeout_exceeds_job_budget")
        if obj["stop_after"] is not None and (type(obj["stop_after"]) is not int
                                              or obj["stop_after"] <= 0):
            raise BoundaryError("remote_training", "invalid_stop_after")
