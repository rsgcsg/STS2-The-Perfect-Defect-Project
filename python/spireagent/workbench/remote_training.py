"""Application mapping to the existing fenced Scheduler; no second task/use ledger."""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from spireagent.artifact_contracts import Manifest
from spireagent.compute import ComputeProvider
from spireagent.hub.database import Operations
from spireagent.json_boundary import BoundaryError, digest, object_fields
from spireagent.source import source_identity
from spireagent.storage.store import copy_artifact
from spireagent.workbench.developer import ROOT, ProjectConfig
from spireagent.workbench.p1_recording_storage import open_peer
from stpd.cloud_jobs.modal import ModalProvider, ModalTarget

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

    def capabilities(self) -> dict[str, Any]:
        deployment = self.config.remote_compute
        configured = deployment is not None
        return {"schema": "spireagent/remote-training-capabilities-1",
                "deployment": "local_scheduler", "configured": configured,
                "available": (deployment is not None and not self.config.hub_url
                              and (self._injected_provider or
                                   deployment.compute_store.get("kind") == "s3")),
                "provider": "trusted_injected_engineering" if self._injected_provider else "modal",
                "supported_actions": ["prepare", "start", "status", "cancel", "reconcile",
                                      "resume"],
                "live_pause_supported": False, "fixed_stop_after_supported": True,
                "automatic_retry": False, "gpu_qualification": "not_established",
                "compute_store": dict(deployment.compute_store) if deployment else None,
                "remote_store_access": "prepare_verifies_inputs_only_not_worker_secret_injection"}

    def _context(self) -> tuple[Any, Any, ComputeProvider, Any]:
        deployment = self.config.remote_compute
        if deployment is None:
            raise BoundaryError("remote_training", "explicit_scheduler_deployment_required")
        if self.config.hub_url:
            raise BoundaryError("remote_training", "configured_hub_requires_hub_scheduler_route")
        if not self._injected_provider and deployment.compute_store.get("kind") != "s3":
            raise BoundaryError("remote_training", "shared_compute_store_required")
        owner, store, _ = self.select()
        if self._provider is None:
            target = ModalTarget.decode(json.loads(deployment.target_path.read_bytes()))
            self._provider = ModalProvider(target)
        provider = self._provider
        if provider.target.producer != source_identity(ROOT):
            raise BoundaryError("remote_training", "target_source_lock_mismatch")
        # Ops must never share the private allocation/use database.
        if deployment.operations_path.resolve() == owner.path.resolve():
            raise BoundaryError("remote_training", "separate_operations_database_required")
        if self._compute_store is None:
            self._compute_store = open_peer(deployment.compute_store)
        return owner, store, provider, self._compute_store

    def _ready(self) -> tuple[Any, Any, ComputeProvider, Operations, Scheduler]:
        owner, store, provider, peer = self._context()
        deployment = self.config.remote_compute
        assert deployment is not None
        if self._scheduler is None:
            from spireagent.hub.scheduler import Scheduler

            self._operations = Operations(deployment.operations_path)

            def record_use(run: Manifest) -> None:
                from spireagent.workbench.p1_training_data import record_p1_training_use
                from stpd.p1.workload import verify_p1_run

                current_owner, current_store, _ = self.select()
                workload = verify_p1_run(current_store, run.artifact_id, provider.target.producer)
                record_p1_training_use(current_owner, current_store, workload.snapshot.artifact_id,
                                       run.parameters.value()["operation_id"])

            self._scheduler = Scheduler(self._operations, peer, provider,
                                        budget_limit=deployment.budget_limit,
                                        record_training_use=record_use)
        assert self._operations is not None
        return owner, store, provider, self._operations, self._scheduler

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
                    scheduler.tick(time.time())
                    self._error = None
            except (BoundaryError, OSError, ValueError) as error:
                self._error = (error.code if isinstance(error, BoundaryError)
                               else type(error).__name__)
                return  # A failed owner never silently restarts or resubmits.
            self._stop.wait(0.5)

    def start_loop(self) -> None:
        if self.config.remote_compute is not None and not self.config.hub_url:
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
        from stpd.p1.spec import RUN_SCHEMA

        intent = digest(intent_id, "remote_training.intent_id", length=32)
        if self.config.hub_url:
            raise BoundaryError("remote_training", "configured_hub_requires_hub_scheduler_route")
        deployment = self.config.remote_compute
        if deployment is None:
            raise BoundaryError("remote_training", "explicit_scheduler_deployment_required")
        _, local, _ = self.select()
        runs = [manifest for identity in local.manifest_ids()
                if (manifest := local.get_manifest(identity)).parameters.value().get("schema")
                == RUN_SCHEMA and manifest.parameters.value().get("operation_id") == intent]
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
        with self._lock:
            _, _, _, ops, _ = self._ready()
            jobs = ops.jobs() if job_id is None else [self._job(ops, job_id)]
            visible = [{k: row[k] for k in ("id", "kind", "input_id", "status", "attempt_id",
                       "fence", "max_seconds", "reserved_units", "provider_ref", "result")}
                       for row in jobs]
            for row in visible:
                row["result"] = json.loads(row["result"]) if row["result"] else None
            return {"schema": "spireagent/remote-training-status-1", "jobs": visible,
                    "scheduler_error": self._error, "automatic_retry": False}

    def prepare(self, body: object) -> dict[str, Any]:
        from spireagent.workbench.p1_training_data import record_p1_training_use, require_p1_snapshot
        from spireagent.workbench.remote_training_storage import transfer_p1_input
        from stpd.p1.spec import MStdConfig
        from stpd.p1.workload import prepare_p1_run

        owner, store, provider, _ = self._context()
        deployment = self.config.remote_compute
        assert deployment is not None
        obj = object_fields(body, {"intent_id", "source_id", "config", "max_seconds",
                                    "stop_after"}, "remote_training.start")
        intent = digest(obj["intent_id"], "remote_training.intent_id", length=32)
        snapshot = digest(obj["source_id"], "remote_training.source_id")
        config = MStdConfig.decode(obj["config"])
        target = provider.target
        gpu = getattr(target, "gpu", None)
        if ((config.device == "cpu") != (gpu == "none")):
            raise BoundaryError("remote_training", "explicit_target_device_mismatch")
        self._limits(obj, target.timeout_seconds)
        require_p1_snapshot(owner, store, snapshot)
        # Vocabulary fitting itself discloses training examples; reserve use first.
        record_p1_training_use(owner, store, snapshot, intent)
        run = prepare_p1_run(store, snapshot, target.producer, config, intent)
        scratch = self.config.state_dir / "remote-training-transfer"
        scratch.mkdir(parents=True, exist_ok=True)
        facts = transfer_p1_input(store, deployment.compute_store, run.artifact_id,
                                  target.producer, scratch=scratch)
        return {"schema": "spireagent/remote-training-prepared-1", "state": "prepared",
                "run_id": run.artifact_id, "input": facts, "enqueued": False,
                "provider_submitted": False, "gpu_qualification": "not_established"}

    def command(self, action: str, body: object) -> dict[str, Any]:
        if action == "pause":
            raise BoundaryError("remote_training", "live_pause_unsupported_use_fixed_stop_after")
        with self._lock:
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
                            copy_artifact(self._compute_store, store, identity, scratch=scratch)
                else:
                    intent = digest(obj["intent_id"], "remote_training.intent_id", length=32)
                    checkpoint = digest(obj["checkpoint_id"], "remote_training.checkpoint_id")
                    result = json.loads(row["result"]) if row["result"] else {}
                    if row["status"] != "paused" or result.get("checkpoint_id") != checkpoint:
                        raise BoundaryError("remote_training", "exact_paused_checkpoint_required")
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
