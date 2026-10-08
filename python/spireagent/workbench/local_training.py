"""One explicit, store-owned local engineering training operation.

The operation file is a conservative journal, not a worker queue. An unfinished
run is never launched again implicitly; only an exact completed marker proves
the child finished. The OS lock spans the parent-owned child lifecycle.
"""

from __future__ import annotations

import json
import os
import subprocess as subprocess
import threading
import time
import traceback
import uuid
from contextlib import AbstractContextManager, suppress
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, digest
from spireagent.source import source_identity as source_identity
from spireagent.storage.replaceable_file import (
    has_unresolved_replacement,
    read_replaceable_bytes,
    write_replaceable_json,
)
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ROOT as ROOT
from spireagent.workbench.developer import ProjectConfig
from spireagent.workbench.instance_lock import instance_lock
from spireagent.workbench.local_curation import LocalCurationOwner
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_model_dependencies import require_recipe_dependencies
from spireagent.workbench.memory_recipe import (
    M2_K1_RECIPE,
    MEMORY_RECIPES,
)
from spireagent.workbench.memory_recipe import (
    V2_MEMORY_RECIPES as V2_MEMORY_RECIPES,
)
from spireagent.workbench.memory_recipe import (
    recipe_for_memory_config as recipe_for_memory_config,
)
from spireagent.workbench.recipe_contracts import (
    LOCAL_PLACEMENT,
    SNAPSHOT_SCHEMA,
    TrainingRequest,
    validate_limits,
)
from spireagent.workbench.research_process import private_child
from spireagent.workbench.training_scratch import retained_scratch_bytes
from spireagent.workbench.trusted_recipes import (
    MAX_TOTAL_ATTEMPTS,
    STRUCTURED_RECIPE,
    TRUSTED_RECIPES,
    describe_recipe,
    validate_recipe_config,
)

_private_child = private_child  # Legacy patchable child boundary.

SCHEMA = "stpd/local-training-operation-v1"
SCHEMA_V2 = "stpd/local-training-operation-v2"
SCHEMA_V3 = "spireagent/local-training-operation-v3"
DEFAULT_RECIPE = "stage1a.dsimple.s.v1"
MEMORY_RECIPE = M2_K1_RECIPE  # Preserve the existing recipe constant for callers.
OPERATION_FILE = "local-training-operation.json"
LOCK_FILE = ".local-training.lock"
PARENT_FAILURE_LOG_BYTES = 64 * 1024
IDS = ("allocation_id", "view_id", "input_id", "run_id", "checkpoint_id",
       "result_id", "model_id", "evaluation_id")
PREVIOUS_COMPLETED_IDS = ("operation_id", "dataset_id", "result_id", "model_id",
                          "evaluation_id")
STAGES = frozenset({"reserving", "allocating", "public_view", "tokenizing",
                    "preparing_run", "training", "verifying_result", "completed"})


def _safe_parent_failure(error: Exception, stage: str) -> dict[str, Any]:
    frames = traceback.extract_tb(error.__traceback__)
    owner = next((frame for frame in frames if
                  Path(frame.filename).name == "local_training.py" and
                  frame.name == "_run"), None)
    origin = frames[-1] if frames else None

    def point(frame: traceback.FrameSummary | None) -> dict[str, Any] | None:
        if frame is None:
            return None
        return {"file": Path(frame.filename).name, "function": frame.name,
                "line": frame.lineno}

    errno = getattr(error, "errno", None)
    winerror = getattr(error, "winerror", None)
    return {"stage": stage, "exception_type": type(error).__module__ + "." +
            type(error).__qualname__,
            "errno": errno if type(errno) is int else None,
            "winerror": winerror if type(winerror) is int else None,
            "owner_call": point(owner), "origin": point(origin)}


def _write_parent_failure(path: Path, identity: str, error: Exception) -> None:
    destination = path.parent / ("local-training-" + identity + "-parent-error.log")
    raw = "".join(traceback.format_exception(error)).encode("utf-8", "replace")
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw[:PARENT_FAILURE_LOG_BYTES])
        handle.flush()
        os.fsync(handle.fileno())


class LocalTrainingService:
    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self._selection = LocalDatasetService(config)
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._failure_diagnostic: dict[str, Any] | None = None
        self._active_attempt: str | None = None
        self._attempt_started_monotonic: float | None = None
        self._active_child_exit: dict[str, Any] | None = None
        self._child_failure_diagnostic: dict[str, str] | None = None
        self._cancel_requested_monotonic: float | None = None

    def _selected(self):
        return self._selection._selected()

    @staticmethod
    def _paths(owner: LocalCurationOwner) -> tuple[Path, Path]:
        return owner.path.parent / OPERATION_FILE, owner.path.parent / LOCK_FILE

    @staticmethod
    def _read(path: Path, identity: tuple[str, ...]) -> dict[str, Any]:
        try:
            if path.is_symlink():
                raise ValueError
            value = json.loads(read_replaceable_bytes(path))
            schema = value.get("schema") if isinstance(value, dict) else None
            if schema == SCHEMA_V3:
                return LocalTrainingService._validate_v3(value, identity)
            if (not isinstance(value, dict) or schema not in {SCHEMA, SCHEMA_V2}
                    or value.get("status") not in
                    {"pending", "completed", "failed", "interrupted_unknown"}
                    or value.get("stage") not in STAGES
                    or value.get("_owner") != list(identity)):
                raise ValueError
            digest(value["operation_id"], "local_training.operation", length=32)
            digest(value["dataset_id"], "local_training.dataset")
            memory = value.get("recipe") in MEMORY_RECIPES
            if schema == SCHEMA_V2:
                if value.get("recipe") not in {DEFAULT_RECIPE, *MEMORY_RECIPES}:
                    raise ValueError
                if memory:
                    if (value.get("result_type") != "train_only"
                            or value.get("evaluation_status") != "not_run"
                            or "evaluation_id" in value):
                        raise ValueError
                elif (value.get("result_type") != "evaluated"
                      or value.get("evaluation_status") != (
                          "completed" if value["status"] == "completed" else "pending")):
                    raise ValueError
            for key in IDS:
                if key in value:
                    digest(value[key], "local_training." + key)
            completed_ids = (("input_id", "run_id", "checkpoint_id", "result_id", "model_id")
                             if schema == SCHEMA_V2 and memory else
                             ("run_id", "result_id", "model_id", "evaluation_id"))
            if value["status"] == "completed" and not all(value.get(key) for key in completed_ids):
                raise ValueError
            if "previous_completed" in value:
                previous = value["previous_completed"]
                legacy = set(PREVIOUS_COMPLETED_IDS)
                typed = {"operation_id", "dataset_id", "result_id", "model_id", "recipe",
                         "result_type", "evaluation_status", "checkpoint_id", "input_id"}
                if not isinstance(previous, dict) or (
                    set(previous) != legacy and (
                        schema != SCHEMA_V2
                        or set(previous) not in (typed, typed | {"evaluation_id"})
                    )
                ):
                    raise ValueError
                digest(previous["operation_id"], "local_training.previous_operation", length=32)
                for key in ("dataset_id", "result_id", "model_id", "evaluation_id",
                            "checkpoint_id", "input_id"):
                    if key not in previous:
                        continue
                    digest(previous[key], "local_training.previous_" + key)
                if "recipe" in previous:
                    if previous["recipe"] in MEMORY_RECIPES:
                        if (set(previous) != typed or previous["result_type"] != "train_only"
                                or previous["evaluation_status"] != "not_run"):
                            raise ValueError
                    elif (previous["recipe"] != DEFAULT_RECIPE
                          or set(previous) != typed | {"evaluation_id"}
                          or previous["result_type"] != "evaluated"
                          or previous["evaluation_status"] != "completed"):
                        raise ValueError
            if value["status"] in {"failed", "interrupted_unknown"} and not isinstance(
                value.get("error_code"), str
            ):
                raise ValueError
            return value
        except FileNotFoundError as error:
            if path.is_symlink() or has_unresolved_replacement(path):
                raise BoundaryError("local_training", "operation_recovery_required") from error
            return {"status": "idle"}
        except (OSError, ValueError, KeyError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_training", "operation_recovery_required") from error

    @staticmethod
    def _public(value: dict[str, Any]) -> dict[str, Any]:
        keys = {"status", "stage", "operation_id", "dataset_id", "error_code",
                "recipe", "result_type", "evaluation_status", "schema",
                "previous_completed", *IDS}
        return {key: item for key, item in value.items() if key in keys}

    def status(self, operation_id: object | None = None) -> dict[str, Any]:
        try:
            owner, _, _ = self._selected()
        except BoundaryError as error:
            availability = ("workspace_required" if error.code == "workspace_required" else
                            "preparation_required" if error.code == "curation_preparation_required"
                            else "recovery_required")
            return {"schema": SCHEMA, "availability": availability, "reason": error.code,
                    "operation": {"status": "idle"}}
        path, lock_path = self._paths(owner)
        try:
            operation = self._read(path, owner.identity)
            if operation["status"] == "idle":
                # Absence during a live writer's replacement is not an idle
                # operation. Reconcile under the existing owner lock without
                # creating it; an unlocked pre-admission lock is still harmless.
                try:
                    if lock_path.is_symlink():
                        raise BoundaryError("local_training", "operation_recovery_required")
                    with instance_lock(lock_path, create=False):
                        operation = self._read(path, owner.identity)
                except FileNotFoundError:
                    pass
                except (OSError, BoundaryError) as error:
                    raise BoundaryError("local_training", "operation_recovery_required") from error
        except BoundaryError as error:
            return {"schema": SCHEMA, "availability": "recovery_required",
                    "reason": error.code, "operation": {"status": "idle"}}
        if operation["status"] == "pending":
            # A lock held by this or another profile proves the supervising parent
            # is alive. A missing lock means an unknown child outcome, not failure.
            try:
                if not lock_path.is_file() or lock_path.is_symlink():
                    raise BoundaryError("local_training", "operation_lock_missing")
                with instance_lock(lock_path, create=False):
                    # The supervisor may have written its terminal record and
                    # released the lock after our first read. Re-read under the
                    # lock before classifying an apparently unfinished operation.
                    try:
                        operation = self._read(path, owner.identity)
                        if operation["status"] == "idle":
                            raise BoundaryError("local_training", "operation_recovery_required")
                    except BoundaryError as error:
                        return {"schema": SCHEMA, "availability": "recovery_required",
                                "reason": error.code, "operation": {"status": "idle"}}
                    if operation["status"] == "pending":
                        operation = {**operation, "status": "interrupted_unknown",
                                     "error_code": "previous_training_outcome_unknown"}
            except (OSError, BoundaryError) as error:
                if not isinstance(error, BoundaryError) or error.code != "already_running":
                    operation = {**operation, "status": "interrupted_unknown",
                                 "error_code": "previous_training_outcome_unknown"}
        if operation_id is not None and operation.get("operation_id") != digest(
            operation_id, "local_training.operation_id", length=32
        ):
            raise BoundaryError("local_training", "operation_not_current")
        if operation.get("schema") == SCHEMA_V3:
            return self._snapshot(operation)
        return {"schema": operation.get("schema", SCHEMA), "availability": "ready",
                "operation": self._public(operation)}

    def _advance(self, path: Path, identity: str, **updates: Any) -> None:
        with self._lock:
            current = json.loads(read_replaceable_bytes(path))
            if current.get("operation_id") != identity or current.get("status") != "pending":
                raise BoundaryError("local_training", "operation_superseded")
            if current.get("schema") == SCHEMA_V3:
                updates.setdefault("updated_at", time.time())
            write_replaceable_json(path, {**current, **updates})

    def start(self, dataset_id: object, *,
              after_completed_operation_id: object | None = None,
              recipe: object = DEFAULT_RECIPE) -> dict[str, Any]:
        if isinstance(dataset_id, dict):
            dataset_id = TrainingRequest.from_dict(dataset_id)
        if isinstance(dataset_id, TrainingRequest):
            request = dataset_id
            request.validate()
            canonical = validate_recipe_config(request.recipe_id, request.config)
            request = TrainingRequest(request.intent_id, request.recipe_id, request.source_id,
                                      canonical, request.placement_id,
                                      validate_limits(request.limits),
                                      request.after_completed_operation_id)
            if request.recipe_id == STRUCTURED_RECIPE:
                if not request.limits:
                    raise BoundaryError("local_training", "wall_limit_required")
                return self._start_structured(request)
            if request.limits:
                raise BoundaryError("local_training", "recipe_limits_not_supported")
            return self._start_legacy(request.source_id,
                                      after_completed_operation_id=request.after_completed_operation_id,
                                      recipe=request.recipe_id, _request=request)
        return self._start_legacy(dataset_id,
                                  after_completed_operation_id=after_completed_operation_id,
                                  recipe=recipe)

    def _start_legacy(self, dataset_id: object, *,
              after_completed_operation_id: object | None = None,
              recipe: object = DEFAULT_RECIPE,
              _request: TrainingRequest | None = None) -> dict[str, Any]:
        if (not isinstance(recipe, str)
                or recipe not in {DEFAULT_RECIPE, *MEMORY_RECIPES}):
            raise BoundaryError("local_training", "unsupported_training_recipe")
        dataset_id = digest(dataset_id, "local_training.dataset_id")
        after_completed = (None if after_completed_operation_id is None else
                           digest(after_completed_operation_id,
                                  "local_training.after_completed_operation_id", length=32))
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        if lock_path.is_symlink():
            raise BoundaryError("local_training", "operation_recovery_required")
        held: AbstractContextManager[None] = instance_lock(lock_path)
        try:
            held.__enter__()
        except BoundaryError as error:
            if error.code == "already_running":
                if after_completed is not None:
                    raise BoundaryError("local_training", "operation_in_progress") from error
                operation = self._read(path, owner.identity)
                if (operation.get("dataset_id") == dataset_id and operation["status"] == "pending"
                        and operation.get("recipe", DEFAULT_RECIPE) == recipe
                        and (_request is None or operation.get("request") == _request.to_dict())):
                    return self.status()
                raise BoundaryError("local_training", "operation_in_progress") from error
            raise
        try:
            previous = self._read(path, owner.identity)
            if _request is not None and previous.get("intent_id") == _request.intent_id:
                if previous.get("request") != _request.to_dict():
                    raise BoundaryError("local_training", "intent_payload_mismatch")
                return self._snapshot(previous)
            if previous["status"] in {"pending", "paused", "cancelled", "interrupted_unknown"} or (
                previous["status"] == "failed" and previous.get("run_id")
            ):
                raise BoundaryError("local_training", "previous_training_outcome_unknown")
            if after_completed is not None and (
                previous["status"] != "completed" or previous["dataset_id"] != dataset_id
                or previous["operation_id"] != after_completed
            ):
                raise BoundaryError("local_training", "new_experiment_precondition_failed")
            if (previous["status"] == "completed" and previous["dataset_id"] == dataset_id
                    and previous.get("recipe", DEFAULT_RECIPE) == recipe
                    and after_completed is None):
                if _request is not None:
                    raise BoundaryError("local_training", "new_experiment_precondition_failed")
                return self.status()
            if (previous["status"] == "completed" and previous["dataset_id"] == dataset_id
                    and after_completed is None):
                raise BoundaryError("local_training", "new_experiment_precondition_failed")
            if previous.get("schema") == SCHEMA_V3 and _request is None:
                _request = TrainingRequest(uuid.uuid4().hex, str(recipe), dataset_id, {},
                                           limits={}, after_completed_operation_id=after_completed)
            require_recipe_dependencies(str(recipe), "local_training")
            identity = uuid.uuid4().hex
            operation = {"schema": (SCHEMA_V2 if recipe in MEMORY_RECIPES
                                    or previous.get("schema") == SCHEMA_V2 else SCHEMA),
                         "status": "pending", "stage": "reserving",
                         "operation_id": identity, "dataset_id": dataset_id,
                         "_owner": list(owner.identity)}
            if operation["schema"] == SCHEMA_V2:
                operation.update(
                    recipe=recipe,
                    result_type="train_only" if recipe in MEMORY_RECIPES else "evaluated",
                    evaluation_status="not_run" if recipe in MEMORY_RECIPES else "pending",
                )
            if previous["status"] == "completed" and _request is None:
                if operation["schema"] == SCHEMA_V2 and previous["schema"] == SCHEMA:
                    # Preserve a v1 completion intact when changing recipes.
                    operation["previous_completed"] = {
                        key: previous[key] for key in PREVIOUS_COMPLETED_IDS
                    }
                elif operation["schema"] == SCHEMA_V2:
                    operation["previous_completed"] = {
                        key: previous[key] for key in ("operation_id", "dataset_id", "result_id",
                            "model_id", "recipe", "result_type", "evaluation_status",
                            "checkpoint_id", "input_id", "evaluation_id") if key in previous
                    }
                else:
                    operation["previous_completed"] = {
                        key: previous[key] for key in PREVIOUS_COMPLETED_IDS
                    }
            if _request is not None:
                operation = self._new_operation(_request, owner, identity,
                                                previous=previous)
            write_replaceable_json(path, operation)
            thread = threading.Thread(target=self._run, args=(held, path, identity, owner, store),
                                      name="local-small-b-training", daemon=True)
            with self._lock:
                self._failure_diagnostic = None
                self._thread = thread
                self._active_attempt = operation.get("attempt_id")
                self._attempt_started_monotonic = time.monotonic()
            thread.start()
            held = None  # type: ignore[assignment]
            if operation.get("schema") == SCHEMA_V3:
                return self._snapshot(operation)
            return {"schema": operation["schema"], "availability": "ready",
                    "operation": self._public(operation)}
        finally:
            if held is not None:
                held.__exit__(None, None, None)

    def _run(self, held: AbstractContextManager[None], path: Path, identity: str,
             owner: LocalCurationOwner, store: ManifestArtifactStore) -> None:
        run_started = False
        initial_attempt: str | None = None

        def mark_started() -> None:
            nonlocal run_started
            run_started = True

        try:
            operation = self._read(path, owner.identity)
            initial_attempt = operation.get("attempt_id")
            from spireagent.workbench.trusted_recipes import recipe_adapter

            recipe_adapter(operation.get("recipe", DEFAULT_RECIPE)).execute(
                self, path, identity, owner, store, mark_started)
        except Exception as error:
            code = (error.code if isinstance(error, BoundaryError)
                    else "training_storage_or_process_error")
            stage = "unavailable"
            with suppress(OSError, ValueError, KeyError, TypeError, BoundaryError):
                recorded = self._read(path, owner.identity)
                if recorded.get("operation_id") == identity and recorded.get("stage") in STAGES:
                    stage = recorded["stage"]
            with suppress(Exception):
                self._failure_diagnostic = _safe_parent_failure(error, stage)
                if self._child_failure_diagnostic is not None:
                    self._failure_diagnostic["child"] = dict(self._child_failure_diagnostic)
            with suppress(Exception):
                _write_parent_failure(path, identity, error)
            # The last durable pending operation remains blocking if terminal
            # persistence fails, so its run identity is never discarded.
            with suppress(OSError, ValueError, BoundaryError), self._lock:
                recorded = self._read(path, owner.identity)
                if recorded.get("schema") == SCHEMA_V3:
                    self._finish_attempt(path, owner, identity,
                                         status="interrupted_unknown" if run_started else "failed",
                                         error_code=code, expected_attempt_id=initial_attempt)
                else:
                    self._advance(path, identity,
                                  status="interrupted_unknown" if run_started else "failed",
                                  error_code=code)
        finally:
            held.__exit__(None, None, None)

    @staticmethod
    def _validate_v3(value: dict[str, Any], owner: tuple[str, ...]) -> dict[str, Any]:
        if (value.get("_owner") != list(owner)
                or value.get("status") not in {"pending", "completed", "failed", "paused",
                                                "cancelled", "interrupted_unknown"}
                or value.get("stage") not in STAGES
                or value.get("recipe") not in TRUSTED_RECIPES):
            raise ValueError("operation_identity")
        request = TrainingRequest.from_dict(value["request"])
        if (request.recipe_id != value["recipe"] or request.source_id != value["dataset_id"]
                or request.config != validate_recipe_config(request.recipe_id, request.config)
                or value.get("intent_id") != request.intent_id):
            raise ValueError("operation_request")
        digest(value["operation_id"], "local_training.operation", length=32)
        digest(value["attempt_id"], "local_training.attempt", length=32)
        if (type(value.get("writer_terminal")) is not bool
                or value.get("requested_action") not in {"continue", "pause", "cancel"}
                or not isinstance(value.get("attempts"), list)
                or type(value.get("elapsed_seconds")) not in {float, int}
                or value["elapsed_seconds"] < 0):
            raise ValueError("operation_attempt")
        for key in ("created_at", "attempt_started_at", "updated_at"):
            if type(value.get(key)) not in {float, int} or value[key] <= 0:
                raise ValueError("operation_timestamp")
        for attempt in value["attempts"]:
            if not isinstance(attempt, dict) or type(attempt.get("writer_terminal")) is not bool:
                raise ValueError("operation_attempt_history")
            digest(attempt["attempt_id"], "local_training.prior_attempt", length=32)
        for key in IDS:
            if key in value:
                digest(value[key], "local_training." + key)
        if value["status"] == "completed" and not all(value.get(key) for key in
                                                       ("run_id", "result_id", "model_id")):
            raise ValueError("operation_completion")
        return value

    def _snapshot(self, value: dict[str, Any]) -> dict[str, Any]:
        running = value["status"] == "pending"
        structured = value["recipe"] == STRUCTURED_RECIPE
        actions = (["cancel", "pause"] if running else
                   ["resume"] if value["status"] in {"paused", "cancelled", "interrupted_unknown"}
                   and value.get("writer_terminal") and value.get("checkpoint_id") else [])
        if structured and value["status"] == "interrupted_unknown":
            actions = [*actions, "reconcile"]
        if not structured or (running and self._active_attempt != value["attempt_id"]):
            actions = []
        operation = {"schema": SNAPSHOT_SCHEMA, "operation_id": value["operation_id"],
                     "intent_id": value["intent_id"], "recipe_id": value["recipe"],
                     "attempt_id": value["attempt_id"], "status": value["status"],
                     "phase": value["stage"], "created_at": value["created_at"],
                     "observed_at": time.time(), "updated_at": value["updated_at"],
                     "last_progress_at": value.get("last_progress_at"),
                     "placement_id": value["request"]["placement_id"],
                     "supported_actions": actions, "requested_action": value["requested_action"],
                     "input_refs": {"source_id": value["dataset_id"],
                                    "training_input_id": value.get("input_id")},
                     "progress": value.get("progress", {"unit": "optimizer_update",
                                                          "completed": 0, "total": None}),
                     "worker_state": "terminal" if value["writer_terminal"] else
                                     "unknown" if value["status"] == "interrupted_unknown" else
                                     "running",
                     "validation_state": "verified" if value.get("domain_completion_state") ==
                                         "completed" or value["status"] == "completed" else
                                         "not_completed",
                     "domain_completion_state": value.get("domain_completion_state", "unknown"),
                     "selected_result": value.get("selected_result",
                                                  value["status"] != "cancelled"),
                     "application_disposition": value.get("application_disposition", "authorized"),
                     "use_state": value.get("use_state", "not_reserved"),
                     "elapsed_seconds": value["elapsed_seconds"]}
        for key in {*IDS, "stage", "dataset_id", "recipe", "result_type", "evaluation_status",
                    "child_exit", "worker_isolation", "artifact_reserved_bytes"}:
            if key in value:
                operation[key] = value[key]
        if value.get("error_code"):
            operation["error"] = {"code": value["error_code"], "automatic_retry": False}
        if "previous_completed" in value:
            operation["previous_completed"] = value["previous_completed"]
        return {"schema": SNAPSHOT_SCHEMA, "availability": "ready", "operation": operation}

    def capabilities(self) -> dict[str, Any]:
        from spireagent.workbench.trusted_agents import agent_capabilities

        return {"schema": "spireagent/training-capabilities-v1",
                "recipes": [describe_recipe(item) for item in sorted(TRUSTED_RECIPES)],
                "placements": [{"placement_id": LOCAL_PLACEMENT, "device": "cpu",
                                "remote": False, "paid": False}],
                "agent_profiles": agent_capabilities(), "automatic_retry": False}

    @staticmethod
    def _new_operation(request: TrainingRequest, owner: LocalCurationOwner, identity: str,
                       *, previous: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        value: dict[str, Any] = {
            "schema": SCHEMA_V3, "status": "pending", "stage": "reserving",
            "operation_id": identity, "attempt_id": uuid.uuid4().hex,
            "intent_id": request.intent_id, "recipe": request.recipe_id,
            "dataset_id": request.source_id, "request": request.to_dict(),
            "_owner": list(owner.identity), "writer_terminal": False,
            "requested_action": "continue", "created_at": now,
            "attempt_started_at": now, "updated_at": now, "elapsed_seconds": 0.0,
            "attempts": [], "mode": "start", "selected_result": True,
            "artifact_reserved_bytes": 0,
            "application_disposition": "authorized", "domain_completion_state": "not_completed",
            "result_type": "train_only" if request.recipe_id in MEMORY_RECIPES else "evaluated",
            "evaluation_status": "not_run" if request.recipe_id in MEMORY_RECIPES else "pending",
        }
        if request.recipe_id == STRUCTURED_RECIPE:
            value.update(worker_isolation="private_child-v1",
                         child_lock_name="local-training-"+value["attempt_id"]+".child.lock",
                         result_type="train_only", evaluation_status="not_run")
        if previous["status"] == "completed":
            value["previous_completed"] = LocalTrainingService._public(previous)
        return value

    def _start_structured(self, request: TrainingRequest) -> dict[str, Any]:
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        if lock_path.is_symlink():
            raise BoundaryError("local_training", "operation_recovery_required")
        held: Any = instance_lock(lock_path)
        try:
            held.__enter__()
        except BoundaryError as error:
            if error.code != "already_running":
                raise
            current = self._read(path, owner.identity)
            if current.get("request") == request.to_dict():
                return self.status(current["operation_id"])
            raise BoundaryError("local_training", "operation_in_progress") from error
        try:
            previous = self._read(path, owner.identity)
            if previous.get("intent_id") == request.intent_id:
                if previous.get("request") != request.to_dict():
                    raise BoundaryError("local_training", "intent_payload_mismatch")
                return self._snapshot(previous)
            if previous["status"] in {"pending", "paused", "cancelled", "interrupted_unknown"} or (
                previous["status"] == "failed" and previous.get("run_id")
            ):
                raise BoundaryError("local_training", "previous_training_outcome_unknown")
            after = request.after_completed_operation_id
            if after is not None and (previous["status"] != "completed" or
                                      previous["operation_id"] != after or
                                      previous["dataset_id"] != request.source_id):
                raise BoundaryError("local_training", "new_experiment_precondition_failed")
            if (previous["status"] == "completed" and previous["dataset_id"] == request.source_id
                    and after is None):
                raise BoundaryError("local_training", "new_experiment_precondition_failed")
            require_recipe_dependencies(request.recipe_id, "local_training")
            # Fail admission before writing a pending operation. This is read-only
            # verification; use reservation remains prior to preparation/fit.
            from spireagent.workbench.recipes.structured import StructuredRecipeAdapter

            StructuredRecipeAdapter().preflight(store, owner, request.source_id)
            operation = self._new_operation(request, owner, uuid.uuid4().hex, previous=previous)
            self._prepare_child_ownership(path, operation)
            write_replaceable_json(path, operation)
            self._launch_attempt(held, path, operation, owner, store)
            held = None
            return self._snapshot(operation)
        finally:
            if held is not None:
                held.__exit__(None, None, None)

    def _launch_attempt(self, held: Any, path: Path, operation: dict[str, Any],
                        owner: LocalCurationOwner, store: ManifestArtifactStore) -> None:
        thread = threading.Thread(target=self._run,
                                  args=(held, path, operation["operation_id"], owner, store),
                                  name="local-training-" + operation["attempt_id"], daemon=True)
        with self._lock:
            self._active_attempt = operation["attempt_id"]
            self._attempt_started_monotonic = time.monotonic()
            self._thread = thread
            self._failure_diagnostic = None
            self._active_child_exit = None
            self._child_failure_diagnostic = None
            self._cancel_requested_monotonic = None
        thread.start()

    def _command_operation(self, path: Path, owner: LocalCurationOwner,
                           operation_id: object, expected_attempt_id: object) -> dict[str, Any]:
        operation_id = digest(operation_id, "local_training.operation_id", length=32)
        expected = digest(expected_attempt_id, "local_training.expected_attempt_id", length=32)
        value = self._read(path, owner.identity)
        if (value.get("schema") != SCHEMA_V3 or value.get("operation_id") != operation_id
                or value.get("attempt_id") != expected):
            raise BoundaryError("local_training", "stale_operation_attempt")
        if value["recipe"] != STRUCTURED_RECIPE:
            raise BoundaryError("local_training", "recipe_control_not_supported")
        return value

    def cancel(self, operation_id: object, expected_attempt_id: object) -> dict[str, Any]:
        return self._request_control(operation_id, expected_attempt_id, "cancel")

    def pause(self, operation_id: object, expected_attempt_id: object) -> dict[str, Any]:
        return self._request_control(operation_id, expected_attempt_id, "pause")

    def _request_control(self, operation_id: object, expected_attempt_id: object,
                         action: str) -> dict[str, Any]:
        owner, _, _ = self._selected()
        path, _ = self._paths(owner)
        with self._lock:
            value = self._command_operation(path, owner, operation_id, expected_attempt_id)
            if (value["status"] != "pending" or self._active_attempt != value["attempt_id"]
                    or self._thread is None or not self._thread.is_alive()):
                raise BoundaryError("local_training", "writer_reconciliation_required")
            if value["requested_action"] == "cancel" and action != "cancel":
                raise BoundaryError("local_training", "cancel_already_requested")
            value.update(requested_action=action, updated_at=time.time())
            if action == "cancel":
                if self._cancel_requested_monotonic is None:
                    self._cancel_requested_monotonic = time.monotonic()
                value.update(selected_result=False, application_disposition="cancel_requested")
            write_replaceable_json(path, value)
            # ACK records intent only. The worker is pending until a safe boundary
            # produces a terminal receipt; this method never reports terminated.
            return self._snapshot(value)

    def _finish_attempt(self, path: Path, owner: LocalCurationOwner, operation_id: str,
                        expected_attempt_id: str | None = None, **updates: Any) -> None:
        with self._lock:
            value = self._read(path, owner.identity)
            if (value.get("operation_id") != operation_id or value.get("status") != "pending"
                    or value.get("attempt_id") != expected_attempt_id):
                raise BoundaryError("local_training", "operation_superseded")
            if self._attempt_started_monotonic is None:
                raise BoundaryError("local_training", "attempt_clock_required")
            seconds = value["elapsed_seconds"] + max(
                0.0, time.monotonic()-self._attempt_started_monotonic)
            domain_status = updates.get("status")
            value.update(updates, elapsed_seconds=seconds, updated_at=time.time())
            value["domain_completion_state"] = ("completed" if domain_status == "completed" else
                                                "unknown" if domain_status == "interrupted_unknown"
                                                else "not_completed")
            cancelled = (value.get("selected_result") is False or
                         value["requested_action"] == "cancel")
            if cancelled:
                value.update(selected_result=False, application_disposition="cancel_requested")
                if value["status"] in {"completed", "interrupted_unknown", "cancelled"}:
                    value["status"] = "cancelled"
                if domain_status == "completed":
                    value["error_code"] = "cancel_requested_during_completion"
            if value.get("worker_isolation") == "private_child-v1":
                receipt = self._active_child_exit
                actual_exit = (receipt is not None and
                               receipt.get("attempt_id") == expected_attempt_id)
                if actual_exit:
                    value["child_exit"] = receipt
                value["writer_terminal"] = actual_exit or not value.get("child_spawned", False)
            else:
                # Legacy subprocess uncertainty retains its historical meaning.
                value["writer_terminal"] = value["status"] == "completed"
            write_replaceable_json(path, value)

    def reconcile(self, operation_id: object, expected_attempt_id: object) -> dict[str, Any]:
        return self._recover(operation_id, expected_attempt_id, mode="reconcile")

    def resume(self, operation_id: object, expected_attempt_id: object, checkpoint_id: object,
               intent_id: object, limits: object) -> dict[str, Any]:
        return self._recover(operation_id, expected_attempt_id, mode="resume",
                             checkpoint_id=digest(checkpoint_id, "local_training.checkpoint_id"),
                             intent_id=digest(intent_id, "local_training.intent_id", length=32),
                             limits=validate_limits(limits))

    def _recover(self, operation_id: object, expected_attempt_id: object, *, mode: str,
                 checkpoint_id: str | None = None, intent_id: str | None = None,
                 limits: dict[str, int] | None = None) -> dict[str, Any]:
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        if lock_path.is_symlink():
            raise BoundaryError("local_training", "operation_recovery_required")
        held: Any = instance_lock(lock_path, create=False)
        prior_child_lock: Any = None
        try:
            held.__enter__()
        except BoundaryError as error:
            raise BoundaryError("local_training", "writer_still_running") from error
        try:
            with self._lock:
                value = self._command_operation(path, owner, operation_id, expected_attempt_id)
                if value.get("worker_isolation") == "private_child-v1":
                    child_path = self._child_path(path, value)
                    prior_child_lock = instance_lock(child_path, create=False)
                    try:
                        prior_child_lock.__enter__()
                    except BoundaryError as error:
                        prior_child_lock = None
                        raise BoundaryError(
                            "local_training", "orphan_child_still_running") from error
                if len(value["attempts"])+1 >= MAX_TOTAL_ATTEMPTS:
                    raise BoundaryError("local_training", "operation_attempt_limit")
                if mode == "resume":
                    if (not value["writer_terminal"] or value["status"] not in
                            {"paused", "cancelled", "interrupted_unknown"}):
                        raise BoundaryError("local_training", "prior_writer_terminal_required")
                    if limits != value["request"]["limits"] or not limits:
                        raise BoundaryError("local_training", "cumulative_limits_must_be_preserved")
                    if (value["elapsed_seconds"] >= limits["wall_seconds"] or
                            value.get("artifact_reserved_bytes", 0) >= limits["scratch_bytes"] or
                            retained_scratch_bytes(path, value) >= limits["scratch_bytes"]):
                        raise BoundaryError("local_training", "cumulative_budget_exhausted")
                    from spireagent.workbench.recipes.structured import verify_resume_checkpoint

                    verify_resume_checkpoint(store, value, checkpoint_id)
                    if any(item.get("intent_id") == intent_id for item in value["attempts"]) or (
                        value["intent_id"] == intent_id
                    ):
                        raise BoundaryError("local_training", "new_resume_intent_required")
                elif not value.get("run_id"):
                    resolved = self._resolve_prepared_run(store, value)
                    if resolved is None:
                        value.update(status=("cancelled" if value.get("selected_result") is False
                                             else "failed"), writer_terminal=True,
                                     error_code="reconciled_preparation_without_run",
                                     domain_completion_state="not_completed",
                                     updated_at=time.time())
                        write_replaceable_json(path, value)
                        return self._snapshot(value)
                    value.update(run_id=resolved[0], input_id=resolved[1])
                    value["writer_terminal"] = True
                else:
                    # Acquiring the same owner lock proves no prior writer retains
                    # publication authority. Keep its outcome unknown until verified.
                    value["writer_terminal"] = True
                if "selected_result" not in value:
                    value["selected_result"] = value["status"] != "cancelled"
                prior = {key: value.get(key) for key in ("attempt_id", "intent_id", "status",
                         "writer_terminal", "checkpoint_id", "attempt_started_at", "child_exit")}
                if value.get("worker_isolation") == "private_child-v1":
                    prior["terminal_proof"] = "child_ownership_reconciled"
                value["attempts"].append(prior)
                if value["status"] == "pending":
                    # A restarted process has no prior monotonic clock. Account
                    # conservatively from the durable wall timestamp, and refuse
                    # rollback instead of replenishing the cumulative budget.
                    elapsed = time.time()-value["attempt_started_at"]
                    if elapsed < 0:
                        raise BoundaryError("local_training", "clock_recovery_required")
                    value["elapsed_seconds"] += elapsed
                value.update(attempt_id=uuid.uuid4().hex, status="pending", writer_terminal=False,
                             requested_action="continue", mode=mode, attempt_started_at=time.time(),
                             updated_at=time.time())
                value.pop("error_code", None)
                value.pop("child_exit", None)
                value.pop("child_spawned", None)
                value.pop("child_handshake", None)
                value.pop("artifact_reservation", None)
                value.update(worker_isolation="private_child-v1",
                             child_lock_name="local-training-"+value["attempt_id"]+".child.lock")
                self._prepare_child_ownership(path, value)
                if mode == "resume":
                    value.update(selected_result=True, application_disposition="explicit_resume")
                    value["intent_id"] = intent_id
                    value["request"]["intent_id"] = intent_id
                    value["resume_checkpoint_id"] = checkpoint_id
                write_replaceable_json(path, value)
                self._launch_attempt(held, path, value, owner, store)
                held = None
                return self._snapshot(value)
        finally:
            if prior_child_lock is not None:
                prior_child_lock.__exit__(None, None, None)
            if held is not None:
                held.__exit__(None, None, None)

    @staticmethod
    def _child_path(path: Path, operation: dict[str, Any]) -> Path:
        name = "local-training-"+operation["attempt_id"]+".child.lock"
        if operation.get("child_lock_name") != name:
            raise BoundaryError("local_training", "child_ownership_identity_mismatch")
        child_path = path.parent/name
        if child_path.is_symlink() or not child_path.is_file():
            raise BoundaryError("local_training", "child_ownership_recovery_required")
        return child_path

    @staticmethod
    def _prepare_child_ownership(path: Path, operation: dict[str, Any]) -> None:
        name = "local-training-"+operation["attempt_id"]+".child.lock"
        if operation.get("child_lock_name") != name:
            raise BoundaryError("local_training", "child_ownership_identity_mismatch")
        descriptor = os.open(path.parent/name, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(descriptor)
        scratch_name = "local-training-"+operation["attempt_id"]+".scratch"
        (path.parent/scratch_name).mkdir(mode=0o700)
        operation["scratch_name"] = scratch_name

    @staticmethod
    def _resolve_prepared_run(store: ManifestArtifactStore,
                              operation: dict[str, Any]) -> tuple[str, str] | None:
        # Explicit orphan reconciliation only. Numeric execution cannot start
        # before the parent's prepared-run ACK, so no run publication means no
        # numerical execution in this fixed private-child protocol.
        identities = store.manifest_ids()
        if len(identities) > 10000:
            raise BoundaryError("local_training", "run_reconciliation_inventory_limit")
        matches = []
        for identity in identities:
            run = store.get_manifest(identity)
            info = run.parameters.value()
            if (run.kind != "run" or info.get("schema") != "stpd/structured-m2-run-v2"
                    or info.get("operation_id") != operation["operation_id"]):
                continue
            training = store.get_manifest(run.parent("training_input"))
            if (training.kind != "training_input" or training.producer != run.producer
                    or training.parent("source") != operation["dataset_id"]):
                raise BoundaryError("local_training", "prepared_run_binding_mismatch")
            matches.append((run.artifact_id, training.artifact_id))
        if len(matches) > 1:
            raise BoundaryError("local_training", "multiple_operation_runs_recovery_required")
        return matches[0] if matches else None
