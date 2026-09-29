"""Owner-admitted asynchronous dev evaluation for one local M2 model.

The journal is a conservative operation record. An unknown child outcome is never
retried implicitly; the existing artifact store and curation ledger remain owners.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from contextlib import AbstractContextManager, suppress
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, digest
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ProjectConfig, atomic_json
from spireagent.workbench.developer_server import instance_lock
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_evaluation import summary
from spireagent.workbench.local_training import _private_child
from stpd.workers.memory_run import INPUT_SCHEMA_V2, MODEL_SCHEMA, RUN_SCHEMA

SCHEMA = "stpd/local-memory-evaluation-operation-v1"
OPERATION_FILE = "local-memory-evaluation-operation.json"
LOCK_FILE = ".local-memory-evaluation.lock"


class LocalMemoryEvaluationService:
    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self._selection = LocalDatasetService(config)
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def _selected(self):
        return self._selection._selected()

    @staticmethod
    def _path(owner: Any) -> Path:
        return owner.path.parent / OPERATION_FILE

    @staticmethod
    def _lock_path(owner: Any) -> Path:
        return owner.path.parent / LOCK_FILE

    @staticmethod
    def _read(path: Path, identity: tuple[str, ...]) -> dict[str, Any]:
        if not path.exists() and not path.is_symlink():
            return {"status": "idle"}
        if path.is_symlink() or not path.is_file():
            raise BoundaryError("local_memory_evaluation", "operation_recovery_required")
        try:
            if path.stat().st_size > 4096:
                raise ValueError
            value = json.loads(path.read_bytes())
            if (not isinstance(value, dict) or value.get("schema") != SCHEMA
                    or value.get("status") not in {
                        "pending", "completed", "failed", "interrupted_unknown"}
                    or value.get("purpose") != "dev"
                    or value.get("_owner") != list(identity)
                    or type(value.get("max_settling_events")) is not int
                    or not 0 <= value["max_settling_events"] <= 64):
                raise ValueError
            digest(value["operation_id"], "local_memory_evaluation.operation", length=32)
            for key in ("model_id", "source_id", "training_operation_id", "train_source_id"):
                digest(value[key], "local_memory_evaluation." + key,
                       length=32 if key == "training_operation_id" else 64)
            if value["status"] == "completed":
                digest(value["evaluation_id"], "local_memory_evaluation.evaluation")
                digest(value["evaluation_input_id"], "local_memory_evaluation.input")
            if (value["status"] in {"failed", "interrupted_unknown"}
                    and not isinstance(value.get("error_code"), str)):
                raise ValueError
            return value
        except (OSError, ValueError, KeyError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_memory_evaluation", "operation_recovery_required") from error

    @staticmethod
    def _public(value: dict[str, Any]) -> dict[str, Any]:
        return {key: item for key, item in value.items() if key in {
            "status", "purpose", "operation_id", "model_id", "source_id",
            "evaluation_id", "evaluation_input_id", "error_code",
        }}

    def status(self) -> dict[str, Any]:
        try:
            owner, _, _ = self._selected()
            operation = self._read(self._path(owner), owner.identity)
        except BoundaryError as error:
            return {"schema": SCHEMA, "availability": "recovery_required",
                    "reason": error.code, "operation": {"status": "idle"}}
        if operation["status"] == "pending":
            lock_path = self._lock_path(owner)
            try:
                if lock_path.is_symlink() or not lock_path.is_file():
                    raise BoundaryError("local_memory_evaluation", "operation_lock_missing")
                with instance_lock(lock_path):
                    operation = self._read(self._path(owner), owner.identity)
                    if operation["status"] == "pending":
                        operation = {**operation, "status": "interrupted_unknown",
                                     "error_code": "previous_evaluation_outcome_unknown"}
            except BoundaryError as error:
                if error.code != "already_running":
                    operation = {**operation, "status": "interrupted_unknown",
                                 "error_code": "previous_evaluation_outcome_unknown"}
        return {"schema": SCHEMA, "availability": "ready", "operation": self._public(operation)}

    @staticmethod
    def _training_source(store: Any, model_id: str) -> tuple[str, str]:
        model = store.get_manifest(model_id)
        if (model.kind != "model" or model.parameters.value().get("schema") != MODEL_SCHEMA
                or sorted(parent.role for parent in model.parents)
                != ["checkpoint", "run", "training_input"]):
            raise BoundaryError("local_memory_evaluation", "model_training_lineage_mismatch")
        run = store.get_manifest(model.parent("run"))
        training = store.get_manifest(model.parent("training_input"))
        checkpoint = store.get_manifest(model.parent("checkpoint"))
        completed = ObjectStoreRunReporter(store, store.blobs).completed(run.artifact_id)
        operation_id = digest(run.parameters.value().get("operation_id"),
                              "local_memory_evaluation.model_operation", length=32)
        if (run.kind != "run" or run.parameters.value().get("schema") != RUN_SCHEMA
                or run.parameters.value().get("partition") != "train"
                or training.kind != "training_input"
                or training.parameters.value().get("schema") != INPUT_SCHEMA_V2
                or checkpoint.kind != "checkpoint"
                or run.parent("training_input") != training.artifact_id
                or model.parent("run") != run.artifact_id
                or model.parent("training_input") != training.artifact_id
                or completed is None or completed.kind != "run_result"
                or completed.parameters.value().get("state") != "completed"
                or completed.parent("model") != model_id
                or completed.parent("run") != run.artifact_id
                or completed.parent("checkpoint") != checkpoint.artifact_id
                or completed.parent("training_input") != training.artifact_id):
            raise BoundaryError("local_memory_evaluation", "model_training_lineage_mismatch")
        return operation_id, training.parent("source")

    def start(self, model_id: object, source_id: object, *,
              max_settling_events: object = 0) -> dict[str, Any]:
        model_id = digest(model_id, "local_memory_evaluation.model")
        source_id = digest(source_id, "local_memory_evaluation.source")
        if (type(max_settling_events) is not int or not 0 <= max_settling_events <= 64):
            raise BoundaryError("local_memory_evaluation", "invalid_settling_limit")
        owner, store, registry_path = self._selected()
        path, lock_path = self._path(owner), self._lock_path(owner)
        if lock_path.is_symlink():
            raise BoundaryError("local_memory_evaluation", "operation_recovery_required")
        held: AbstractContextManager[None] = instance_lock(lock_path)
        try:
            held.__enter__()
        except BoundaryError as error:
            if error.code == "already_running":
                raise BoundaryError("local_memory_evaluation", "evaluation_in_progress") from error
            raise
        try:
            previous = self._read(path, owner.identity)
            if previous["status"] in {"pending", "interrupted_unknown"}:
                raise BoundaryError(
                    "local_memory_evaluation", "previous_evaluation_outcome_unknown")
            if (previous["status"] == "completed" and previous["model_id"] == model_id
                    and previous["source_id"] == source_id
                    and previous["max_settling_events"] == max_settling_events):
                return self.status()
            training_operation_id, train_source_id = self._training_source(store, model_id)
            identity = uuid.uuid4().hex
            operation = {
                "schema": SCHEMA, "status": "pending", "purpose": "dev",
                "operation_id": identity, "model_id": model_id, "source_id": source_id,
                "training_operation_id": training_operation_id,
                "train_source_id": train_source_id,
                "max_settling_events": max_settling_events, "_owner": list(owner.identity),
            }
            atomic_json(path, operation)
            thread = threading.Thread(
                target=self._run,
                args=(held, path, identity, owner, store, registry_path, operation),
                name="local-memory-dev-evaluation", daemon=True,
            )
            with self._lock:
                self._thread = thread
            thread.start()
            held = None  # type: ignore[assignment]
            return {"schema": SCHEMA, "availability": "ready",
                    "operation": self._public(operation)}
        finally:
            if held is not None:
                held.__exit__(None, None, None)

    @staticmethod
    def _advance(path: Path, identity: str, **updates: Any) -> None:
        current = json.loads(path.read_bytes())
        if current.get("operation_id") != identity or current.get("status") != "pending":
            raise BoundaryError("local_memory_evaluation", "operation_superseded")
        atomic_json(path, {**current, **updates})

    def _run(self, held: AbstractContextManager[None], path: Path, identity: str,
             owner: Any, store: Any, registry_path: Path,
             operation: dict[str, Any]) -> None:
        child_started = False

        def mark_started() -> None:
            nonlocal child_started
            child_started = True
            self._mark_child_started(path, identity)

        try:
            owner.reserve_memory_dev(
                store, operation["train_source_id"], operation["source_id"],
                operation["training_operation_id"], identity,
            )
            environment = dict(os.environ)
            for name in ("STPD_HUB_ADMIN_TOKEN", "PYTHONPATH", "PYTHONHOME"):
                environment.pop(name, None)
            command = [
                sys.executable, "-m", "spireagent.research_cli", "--store", str(owner.store_dir),
                "evaluate-memory", "--model", operation["model_id"],
                "--source", operation["source_id"], "--operation", identity,
                "--max-settling-events", str(operation["max_settling_events"]),
            ]
            log_path = owner.path.parent / ("local-memory-evaluation-" + identity + ".log")
            exit_code, captured = _private_child(
                command, log_path, environment,
                on_started=mark_started,
            )
            if exit_code:
                raise BoundaryError("local_memory_evaluation", "evaluation_process_failed")
            result = json.loads(captured)
            if (not isinstance(result, dict)
                    or not {"evaluation_id", "evaluation_input_id"} <= set(result)):
                raise BoundaryError("local_memory_evaluation", "evaluation_result_invalid")
            evaluation_id = digest(result["evaluation_id"], "local_memory_evaluation.result")
            input_id = digest(result["evaluation_input_id"], "local_memory_evaluation.input")
            evaluation = store.get_manifest(evaluation_id)
            if (evaluation.parent("evaluation_input") != input_id
                    or evaluation.parent("model") != operation["model_id"]
                    or evaluation.parent("source") != operation["source_id"]
                    or evaluation.parameters.value().get("operation_id") != identity):
                raise BoundaryError("local_memory_evaluation", "evaluation_result_invalid")
            summary(store, evaluation_id)
            sync_registry(store, SQLiteRegistry(registry_path))
            self._advance(path, identity, status="completed", evaluation_id=evaluation_id,
                          evaluation_input_id=input_id)
        except (BoundaryError, OSError, ValueError, KeyError, TypeError,
                subprocess.SubprocessError) as error:
            code = (error.code if isinstance(error, BoundaryError)
                    else "evaluation_storage_or_process_error")
            with suppress(OSError, ValueError, BoundaryError):
                self._advance(path, identity,
                              status="interrupted_unknown" if child_started else "failed",
                              error_code=code)
        finally:
            held.__exit__(None, None, None)

    @staticmethod
    def _mark_child_started(path: Path, identity: str) -> None:
        LocalMemoryEvaluationService._advance(path, identity, child_started=True)
