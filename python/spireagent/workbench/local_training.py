"""One explicit, store-owned local engineering training operation.

The operation file is a conservative journal, not a worker queue. An unfinished
run is never launched again implicitly; only an exact completed marker proves
the child finished. The OS lock spans the parent-owned child lifecycle.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import traceback
import uuid
from contextlib import AbstractContextManager, suppress
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, digest
from spireagent.source import source_identity
from spireagent.storage.replaceable_file import (
    has_unresolved_replacement,
    read_replaceable_bytes,
    write_replaceable_json,
)
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ROOT, ProjectConfig
from spireagent.workbench.developer_server import instance_lock
from spireagent.workbench.local_curation import LocalCurationOwner
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_model_dependencies import require_local_models
from spireagent.workbench.memory_recipe import (
    M2_K1_RECIPE,
    MEMORY_RECIPES,
    V2_MEMORY_RECIPES,
    recipe_for_memory_config,
)
from spireagent.workbench.research_process import private_child as _private_child

SCHEMA = "stpd/local-training-operation-v1"
SCHEMA_V2 = "stpd/local-training-operation-v2"
SCHEMA_V3 = "stpd/local-training-operation-v3"
DEFAULT_RECIPE = "stage1a.dsimple.s.v1"
PUBLIC_M0_RECIPE = "stage1a.dsimple.light-action.m0.s.v1"
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


def _child_json_record(captured: bytes) -> dict[str, Any]:
    """Read the final machine record; libraries may emit earlier stdout diagnostics."""
    lines = [line for line in captured.decode("utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError("child_json_record_missing")
    value = json.loads(lines[-1])
    if not isinstance(value, dict):
        raise ValueError("child_json_record_invalid")
    return value


def _write_parent_failure(path: Path, identity: str, error: Exception) -> None:
    destination = path.parent / ("local-training-" + identity + "-parent-error.log")
    raw = "".join(traceback.format_exception(error)).encode("utf-8", "replace")
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw[:PARENT_FAILURE_LOG_BYTES])
        handle.flush()
        os.fsync(handle.fileno())


class LocalTrainingService:
    def __init__(self, config: ProjectConfig, *, config_path: Path | None = None) -> None:
        self.config = config
        self.config_path = config_path
        self._selection = LocalDatasetService(config)
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._failure_diagnostic: dict[str, Any] | None = None

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
            if (not isinstance(value, dict) or schema not in {SCHEMA, SCHEMA_V2, SCHEMA_V3}
                    or value.get("status") not in
                    {"pending", "completed", "failed", "interrupted_unknown"}
                    or value.get("stage") not in STAGES
                    or value.get("_owner") != list(identity)):
                raise ValueError
            digest(value["operation_id"], "local_training.operation", length=32)
            digest(value["dataset_id"], "local_training.dataset")
            memory = value.get("recipe") in MEMORY_RECIPES
            if schema == SCHEMA_V3:
                if (value.get("recipe") != PUBLIC_M0_RECIPE
                        or value.get("input_profile") not in {"public_lite", "public_compact"}
                        or value.get("result_type") != "evaluated"
                        or value.get("evaluation_status") not in {"pending", "completed"}
                        or (value["status"] == "completed"
                            and value.get("evaluation_status") != "completed")):
                    raise ValueError
            elif schema == SCHEMA_V2:
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
            completed_ids = (("input_id", "run_id", "result_id", "model_id")
                             if schema == SCHEMA_V3 else
                             ("input_id", "run_id", "checkpoint_id", "result_id", "model_id")
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
                "input_profile", "previous_completed", *IDS}
        return {key: item for key, item in value.items() if key in keys}

    def status(self) -> dict[str, Any]:
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
        return {"schema": operation.get("schema", SCHEMA), "availability": "ready",
                "operation": self._public(operation)}

    @staticmethod
    def _advance(path: Path, identity: str, **updates: Any) -> None:
        current = json.loads(read_replaceable_bytes(path))
        if current.get("operation_id") != identity or current.get("status") != "pending":
            raise BoundaryError("local_training", "operation_superseded")
        write_replaceable_json(path, {**current, **updates})

    def _run_public_m0(self, operation: dict[str, Any], path: Path, identity: str,
                       owner: LocalCurationOwner, store: ManifestArtifactStore,
                       on_started: Any, on_finished: Any) -> None:
        if (self.config_path is None or self.config_path.is_symlink()
                or not self.config_path.is_file()):
            raise BoundaryError("local_training", "configured_local_workspace_required")
        profile = operation.get("input_profile")
        if profile not in {"public_lite", "public_compact"}:
            raise BoundaryError("local_training", "unsupported_input_profile")
        root = getattr(getattr(store, "blobs", None), "root", None)
        if not isinstance(root, Path):
            raise BoundaryError("local_training", "unsupported_workspace_store")
        environment = dict(os.environ)
        for name in ("STPD_HUB_ADMIN_TOKEN", "PYTHONPATH", "PYTHONHOME"):
            environment.pop(name, None)

        self._advance(path, identity, stage="allocating")
        prepare_command = [
            sys.executable, "-m", "spireagent.research_cli", "--store", str(root),
            "prepare-light-action-m0", "--project-config", str(self.config_path),
            "--dataset", operation["dataset_id"], "--operation", identity,
            "--backbone", "s", "--input-profile", profile,
        ]
        prepare_log = owner.path.parent / ("local-training-" + identity + "-prepare.log")
        prepare_exit, captured = _private_child(prepare_command, prepare_log, environment,
                                                on_started=on_started)
        on_finished(prepare_exit)
        if prepare_exit:
            raise BoundaryError("local_training", "public_m0_preparation_process_failed")
        try:
            prepared = _child_json_record(captured)
            expected = {"allocation_id", "model_view_id", "training_input_id", "input_schema",
                        "backbone", "admission", "counts", "elapsed_seconds", "verification"}
            if (not isinstance(prepared, dict) or set(prepared) != expected
                    or prepared["input_schema"] !=
                    "stpd/stage1a-light-action-m0-public-input-v1"
                    or prepared["backbone"] != "s"):
                raise ValueError
            allocation_id = digest(prepared["allocation_id"], "local_training.allocation_id")
            view_id = digest(prepared["model_view_id"], "local_training.view_id")
            input_id = digest(prepared["training_input_id"], "local_training.input_id")
            allocation = store.get_manifest(allocation_id)
            view = store.get_manifest(view_id)
            training_input = store.get_manifest(input_id)
            from stpd.fullrun.light_action_inputs import PUBLIC_SCHEMA, public_training_binding

            binding = public_training_binding(store, input_id)
            if (allocation.parent("dataset") != operation["dataset_id"]
                    or view.parent("allocation") != allocation_id
                    or training_input.parent("model_view") != view_id
                    or training_input.parameters.value().get("schema") != PUBLIC_SCHEMA
                    or binding.get("dataset_ids") != [operation["dataset_id"]]
                    or binding.get("training_operation_id") != identity
                    or view.parameters.value().get("serializer", {}).get("profile") != profile):
                raise ValueError
        except (BoundaryError, OSError, ValueError, KeyError, TypeError) as error:
            raise BoundaryError("local_training", "public_m0_preparation_result_invalid") from error
        self._advance(path, identity, stage="training", allocation_id=allocation_id,
                      view_id=view_id, input_id=input_id)

        train_command = [
            sys.executable, "-m", "spireagent.research_cli", "--store", str(root),
            "train-light-action-m0", "--project-config", str(self.config_path),
            "--inputs", input_id, "--operation", identity,
            "--recipe", "stage1a.dsimple.light-action.m0.s.v1",
            "--steps", "3", "--backend", "cpu",
        ]
        train_log = owner.path.parent / ("local-training-" + identity + ".log")
        train_exit, trained_output = _private_child(train_command, train_log, environment,
                                                    on_started=on_started)
        on_finished(train_exit)
        if train_exit:
            raise BoundaryError("local_training", "public_m0_training_process_failed")
        try:
            trained = _child_json_record(trained_output)
            if (not isinstance(trained, dict) or trained.get("state") != "completed"
                    or trained.get("training_input_id") != input_id
                    or trained.get("training_binding") != binding):
                raise ValueError
            run_id = digest(trained["run_id"], "local_training.run_id")
            result_id = digest(trained["result_id"], "local_training.result_id")
            run = store.get_manifest(run_id)
            result = store.get_manifest(result_id)
            from spireagent.storage.run_reporter import ObjectStoreRunReporter
            from stpd.workers.token_worker import _verify_completed

            completed = ObjectStoreRunReporter(store, store.blobs).completed(run_id)
            if (completed is None or completed.artifact_id != result_id
                    or run.parent("training_input") != input_id
                    or run.parameters.value().get("training_binding") != binding
                    or result.parent("model") != completed.parent("model")):
                raise ValueError
            _verify_completed(store, completed, run)
            model_id = completed.parent("model")
            model = store.get_manifest(model_id)
            if model.parameters.value().get("schema") != \
                    "stpd/stage1a-light-action-m0-public-model-v1":
                raise ValueError
            completion = {"run_id": run_id, "result_id": result_id, "model_id": model_id,
                          "evaluation_id": completed.parent("offline_evaluation"),
                          "checkpoint_id": model.parent("checkpoint"),
                          "evaluation_status": "completed"}
        except (BoundaryError, OSError, ValueError, KeyError, TypeError) as error:
            raise BoundaryError("local_training", "public_m0_training_result_invalid") from error
        from spireagent.storage.registry import SQLiteRegistry, sync_registry

        sync_registry(store, SQLiteRegistry(self._selected()[2]))
        self._advance(path, identity, stage="completed", status="completed", **completion)

    def start(self, dataset_id: object, *,
              after_completed_operation_id: object | None = None,
              recipe: object = DEFAULT_RECIPE,
              input_profile: object = None) -> dict[str, Any]:
        public_profile = input_profile
        if (public_profile is not None
                and (not isinstance(public_profile, str)
                     or public_profile not in {"public_lite", "public_compact"})):
            raise BoundaryError("local_training", "unsupported_input_profile")
        if public_profile is not None:
            if recipe != DEFAULT_RECIPE:
                raise BoundaryError("local_training", "conflicting_training_profile")
            recipe = PUBLIC_M0_RECIPE
        if (not isinstance(recipe, str)
                or recipe not in {DEFAULT_RECIPE, *MEMORY_RECIPES, PUBLIC_M0_RECIPE}):
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
                        and operation.get("input_profile") == public_profile):
                    return self.status()
                raise BoundaryError("local_training", "operation_in_progress") from error
            raise
        try:
            previous = self._read(path, owner.identity)
            if previous["status"] in {"pending", "interrupted_unknown"}:
                raise BoundaryError("local_training", "previous_training_outcome_unknown")
            if previous["status"] == "failed" and previous.get("run_id"):
                raise BoundaryError("local_training", "previous_training_failed")
            if after_completed is not None and (
                previous["status"] != "completed" or previous["dataset_id"] != dataset_id
                or previous["operation_id"] != after_completed
            ):
                raise BoundaryError("local_training", "new_experiment_precondition_failed")
            if (previous["status"] == "completed" and previous["dataset_id"] == dataset_id
                    and previous.get("recipe", DEFAULT_RECIPE) == recipe
                    and previous.get("input_profile") == public_profile
                    and after_completed is None):
                return self.status()
            if (previous["status"] == "completed" and previous["dataset_id"] == dataset_id
                    and after_completed is None):
                raise BoundaryError("local_training", "new_experiment_precondition_failed")
            require_local_models("local_training")
            identity = uuid.uuid4().hex
            operation = {"schema": (SCHEMA_V3 if public_profile is not None else
                                     SCHEMA_V2 if recipe in MEMORY_RECIPES
                                    or previous.get("schema") == SCHEMA_V2 else SCHEMA),
                         "status": "pending", "stage": "reserving",
                         "operation_id": identity, "dataset_id": dataset_id,
                         "_owner": list(owner.identity)}
            if operation["schema"] == SCHEMA_V3:
                operation.update(
                    recipe=PUBLIC_M0_RECIPE,
                    input_profile=public_profile,
                    result_type="evaluated",
                    evaluation_status="pending",
                )
            elif operation["schema"] == SCHEMA_V2:
                operation.update(
                    recipe=recipe,
                    result_type="train_only" if recipe in MEMORY_RECIPES else "evaluated",
                    evaluation_status="not_run" if recipe in MEMORY_RECIPES else "pending",
                )
            if previous["status"] == "completed":
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
            write_replaceable_json(path, operation)
            thread = threading.Thread(target=self._run, args=(held, path, identity, owner, store),
                                      name="local-small-b-training", daemon=True)
            with self._lock:
                self._failure_diagnostic = None
                self._thread = thread
            thread.start()
            held = None  # type: ignore[assignment]
            return {"schema": operation["schema"], "availability": "ready",
                    "operation": self._public(operation)}
        finally:
            if held is not None:
                held.__exit__(None, None, None)

    def _run(self, held: AbstractContextManager[None], path: Path, identity: str,
             owner: LocalCurationOwner, store: ManifestArtifactStore) -> None:
        child_outcome_unknown = False

        def mark_started() -> None:
            nonlocal child_outcome_unknown
            child_outcome_unknown = True

        def mark_finished(_exit_code: int) -> None:
            nonlocal child_outcome_unknown
            child_outcome_unknown = False

        try:
            from spireagent.storage.registry import SQLiteRegistry, sync_registry
            from spireagent.storage.run_reporter import ObjectStoreRunReporter
            from stpd.fullrun.curated_dataset import SCHEMA as DATASET_SCHEMA
            from stpd.fullrun.curated_dataset import load_selection
            from stpd.fullrun.decision_spool import SpoolSelection
            from stpd.fullrun.decision_training import AllocationSpec, allocate, publish_allocation
            from stpd.fullrun.managed_text_menu_import import (
                SOURCE_SCHEMA as MANAGED_SOURCE_SCHEMA,
            )
            from stpd.fullrun.managed_text_menu_import import (
                load_managed_text_menu_source,
            )
            from stpd.fullrun.public_bc import publish_public_bc_view
            from stpd.fullrun.text_menu_human_import import (
                SOURCE_SCHEMA as HUMAN_SOURCE_SCHEMA,
            )
            from stpd.fullrun.text_menu_human_import import (
                _project as project_human_inputs,
            )
            from stpd.fullrun.text_menu_human_import import (
                load_human_text_source,
                load_verified_human_text_bundle,
                publish_human_text_bc_view,
            )
            from stpd.fullrun.token_inputs import load_token_inputs, publish_token_inputs
            from stpd.workers.token_ranking import TokenConfig
            from stpd.workers.token_worker import _verify_completed, prepare_token_run

            operation = self._read(path, owner.identity)
            dataset_id = operation["dataset_id"]
            if operation.get("schema") == SCHEMA_V3:
                self._run_public_m0(operation, path, identity, owner, store,
                                    mark_started, mark_finished)
                return
            memory = operation.get("recipe") in MEMORY_RECIPES
            producer = source_identity(ROOT)
            manifest = store.get_manifest(dataset_id)
            info = manifest.parameters.value()
            human = info.get("schema") == HUMAN_SOURCE_SCHEMA
            managed = info.get("schema") == MANAGED_SOURCE_SCHEMA
            if managed and operation.get("recipe") not in V2_MEMORY_RECIPES:
                raise BoundaryError("local_training", "managed_v2_recipe_required")
            if human and operation.get("recipe") in V2_MEMORY_RECIPES:
                raise BoundaryError("local_training", "managed_v2_source_required")
            if human:
                source_manifest, rows = load_human_text_source(store, dataset_id)
                if source_manifest != manifest:
                    raise BoundaryError("local_training", "human_source_identity_mismatch")
                # This is the actual engineering split gate; session count alone
                # cannot establish independent groups after duplicate collapse.
                if not memory:
                    samples, _ = project_human_inputs(rows)
                    if (sum(sample.split == "train" for sample in samples) > 32
                            or sum(sample.split == "dev" for sample in samples) > 8):
                        raise BoundaryError("local_training", "human_engineering_sample_limit")
                sources = {parent.artifact_id for parent in manifest.parents}
                runs: set[str] = set()
                for source_id in sources:
                    _, bundle, _ = load_verified_human_text_bundle(store, source_id)
                    runs.update(bundle.session_id + "/" + run for run in bundle.run_ids)
                spec = None
            elif managed:
                managed_source = load_managed_text_menu_source(store, dataset_id)
                if managed_source.manifest != manifest:
                    raise BoundaryError("local_training", "managed_source_identity_mismatch")
                runs = {managed_source.split_run_id}
                sources = {dataset_id}
                spec = None
            else:
                if memory:
                    raise BoundaryError("local_training", "human_training_source_required")
                if (manifest.kind != "dataset" or info.get("schema") != DATASET_SCHEMA
                        or info.get("purpose") != "training" or info.get("merging") is not False
                        or not manifest.parents or len(manifest.parents) > 100
                        or any(p.role != "source_" + p.artifact_id for p in manifest.parents)):
                    raise BoundaryError("local_training", "curated_training_dataset_required")
                dataset = load_selection(store, manifest, cache=None)
                try:
                    runs = {row["run_id"] for row in dataset.records.summaries()} if isinstance(
                        dataset.records, SpoolSelection
                    ) else {record.run_id for record in dataset.records}
                    spec = AllocationSpec(isolation="run", max_train=32, max_dev=8)
                    allocate(dataset, spec)
                finally:
                    if isinstance(dataset.records, SpoolSelection):
                        dataset.records.owner.close()
                sources = {parent.artifact_id for parent in manifest.parents}
            claim = owner.ledger.dataset(dataset_id)
            if claim != ("training", runs):
                raise BoundaryError("local_training", "training_claim_mismatch")
            indexed_runs: set[str] = set()
            for source in sorted(sources):
                source_runs = owner.ledger.source_runs(source)
                if source_runs is None:
                    raise BoundaryError("local_training", "source_index_incomplete")
                indexed_runs.update(source_runs)
            if not runs <= indexed_runs:
                raise BoundaryError("local_training", "source_run_identity_mismatch")
            # All exposure records precede the first derivative publication or model step.
            for source in sorted(sources):
                owner.ledger.use_source(source, "training", identity)
            owner.ledger.use(runs, "training", identity)
            environment = dict(os.environ)
            for name in ("STPD_HUB_ADMIN_TOKEN", "PYTHONPATH", "PYTHONHOME"):
                environment.pop(name, None)
            if memory:
                self._advance(path, identity, stage="tokenizing")
                self._advance(path, identity, stage="preparing_run")
                prepare_command = [sys.executable, "-m", "spireagent.research_cli",
                                   "--store", str(owner.store_dir),
                                   "prepare-workbench-memory", "--source", dataset_id,
                                   "--operation", identity,
                                   "--recipe", operation["recipe"]]
                prepare_log = owner.path.parent / ("local-training-" + identity + "-prepare.log")
                prepare_exit, captured = _private_child(prepare_command, prepare_log,
                                                        environment,
                                                        on_started=mark_started)
                mark_finished(prepare_exit)
                if prepare_exit:
                    failure = None
                    with suppress(ValueError, TypeError):
                        failure = json.loads(captured)
                    if (isinstance(failure, dict) and set(failure) == {"error_code"}
                            and isinstance(failure["error_code"], str)):
                        raise BoundaryError("local_training", failure["error_code"])
                    raise BoundaryError("local_training", "memory_preparation_process_failed")
                prepared = json.loads(captured)
                if (not isinstance(prepared, dict)
                        or set(prepared) != {"run_id", "input_id", "verification",
                                            "elapsed_seconds"}):
                    raise BoundaryError("local_training", "memory_preparation_result_invalid")
                run_id = digest(prepared["run_id"], "local_training.run_id")
                input_id = digest(prepared["input_id"], "local_training.input_id")
                run = store.get_manifest(run_id)
                training_input = store.get_manifest(input_id)
                if (run.producer != producer
                        or run.parameters.value().get("operation_id") != identity
                        or run.parent("training_input") != input_id
                        or training_input.parent("source") != dataset_id):
                    raise BoundaryError("local_training", "memory_preparation_result_invalid")
                try:
                    prepared_recipe = recipe_for_memory_config(
                        run.parameters.value().get("config"),
                        projection_config=training_input.parameters.value().get(
                            "projection_config"))
                except ValueError as error:
                    raise BoundaryError("local_training",
                                        "memory_preparation_result_invalid") from error
                if prepared_recipe != operation["recipe"]:
                    raise BoundaryError("local_training", "memory_preparation_profile_mismatch")
                self._advance(path, identity, stage="training", input_id=input_id,
                              run_id=run_id)
                command_name = "run-memory"
            elif human:
                self._advance(path, identity, stage="public_view")
                view = publish_human_text_bc_view(store, dataset_id, producer)
            else:
                assert spec is not None
                self._advance(path, identity, stage="allocating")
                allocation = publish_allocation(store, dataset_id, spec, producer)
                self._advance(path, identity, stage="public_view",
                              allocation_id=allocation.artifact_id)
                view = publish_public_bc_view(store, allocation.artifact_id, producer)
            if not memory:
                self._advance(path, identity, stage="tokenizing", view_id=view.artifact_id)
                inputs_manifest = publish_token_inputs(store, view.artifact_id, "s", producer,
                                                       max_tokens=16384)
                self._advance(path, identity, stage="preparing_run",
                              input_id=inputs_manifest.artifact_id)
                import torch

                torch.set_num_threads(2)
                inputs = load_token_inputs(store, inputs_manifest.artifact_id)
                config = TokenConfig(recipe=DEFAULT_RECIPE, width=48, layers=1,
                                     heads=2, feedforward=96, dropout=0.0, steps=3,
                                     device="cpu", max_tokens=16384)
                run = prepare_token_run(store, inputs, config, producer,
                                        replicate="local-" + identity)
                self._advance(path, identity, stage="training", run_id=run.artifact_id)
                command_name = "run-tokens"
            command = [sys.executable, "-m", "spireagent.research_cli", "--store",
                       str(owner.store_dir), command_name, "--run", run.artifact_id]
            log_path = owner.path.parent / ("local-training-" + identity + ".log")
            exit_code, _ = _private_child(command, log_path, environment,
                                         on_started=mark_started)
            mark_finished(exit_code)
            self._advance(path, identity, stage="verifying_result", _exit_code=exit_code)
            if exit_code:
                raise BoundaryError("local_training", "training_process_failed")
            reporter = ObjectStoreRunReporter(store, store.blobs)
            result = reporter.completed(run.artifact_id)
            if result is None:
                raise BoundaryError("local_training", "completion_marker_missing")
            if memory:
                # The read-only verifier must use the exact child runtime identity.
                verify_command = [sys.executable, "-m", "spireagent.research_cli",
                                  "--store", str(owner.store_dir), "verify-memory",
                                  "--run", run.artifact_id]
                verify_log = owner.path.parent / ("local-training-" + identity + "-verify.log")
                verify_exit, verified_output = _private_child(verify_command, verify_log,
                                                             environment,
                                                             on_started=mark_started)
                mark_finished(verify_exit)
                if verify_exit:
                    raise BoundaryError("local_training", "memory_verification_process_failed")
                verified = json.loads(verified_output)
                if (not isinstance(verified, dict) or verified.get("run_id") != run.artifact_id
                        or verified.get("result_id") != result.artifact_id
                        or verified.get("model_id") != result.parent("model")
                        or verified.get("checkpoint_id") != result.parent("checkpoint")):
                    raise BoundaryError("local_training", "memory_verification_result_invalid")
            else:
                _verify_completed(store, result, run)
            model_id = result.parent("model")
            model = store.get_manifest(model_id)
            _, _, registry_path = self._selected()
            sync_registry(store, SQLiteRegistry(registry_path))
            completion = {"checkpoint_id": model.parent("checkpoint"),
                          "result_id": result.artifact_id, "model_id": model_id}
            if not memory:
                completion["evaluation_id"] = result.parent("offline_evaluation")
                if operation["schema"] == SCHEMA_V2:
                    completion["evaluation_status"] = "completed"
            self._advance(path, identity, stage="completed", status="completed", **completion)
        except (BoundaryError, OSError, ValueError, KeyError, TypeError,
                subprocess.SubprocessError) as error:
            code = (error.code if isinstance(error, BoundaryError)
                    else "training_storage_or_process_error")
            stage = "unavailable"
            with suppress(OSError, ValueError, KeyError, TypeError, BoundaryError):
                recorded = self._read(path, owner.identity)
                if recorded.get("operation_id") == identity and recorded.get("stage") in STAGES:
                    stage = recorded["stage"]
            with suppress(Exception):
                self._failure_diagnostic = _safe_parent_failure(error, stage)
            with suppress(Exception):
                _write_parent_failure(path, identity, error)
            # The last durable pending operation remains blocking if terminal
            # persistence fails, so its run identity is never discarded.
            with suppress(OSError, ValueError, BoundaryError):
                self._advance(path, identity,
                              status="interrupted_unknown" if child_outcome_unknown else "failed",
                              error_code=code)
        finally:
            held.__exit__(None, None, None)
