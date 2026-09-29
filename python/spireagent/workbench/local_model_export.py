"""Explicit offline export of one local text-menu engineering model.

The fixed private destination is not a policy registration or a loaded runtime.
An unfinished journal is observed as interrupted after restart, never replayed.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import uuid
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, cast

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, digest
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ProjectConfig, atomic_json
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_workspace import LocalWorkspace, open_registered_workspace
from spireagent.workbench.research_process import private_child

SCHEMA = "stpd/local-model-export-operation-v1"
SCHEMA_V2 = "stpd/local-model-export-operation-v2"
OPERATION_FILE = "local-model-export-operation.json"
LOCK_FILE = ".local-model-export.lock"
EXPORT_ROOT = "model-exports"


def _ordinary(path: Path, *, directory: bool) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if getattr(metadata, "st_file_attributes", 0) & 0x400:
        return False
    mode = metadata.st_mode
    return stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)


def _destination(config: ProjectConfig, model_id: str, *, memory: bool = False) -> Path:
    state = config.state_dir
    if not _ordinary(state, directory=True):
        raise BoundaryError("local_model_export", "unsafe_export_root")
    root = state / EXPORT_ROOT
    if root.exists() or root.is_symlink():
        if not _ordinary(root, directory=True):
            raise BoundaryError("local_model_export", "unsafe_export_root")
    else:
        root.mkdir(mode=0o700)
    target = root / model_id
    if target.exists() or target.is_symlink():
        if not _ordinary(target, directory=True):
            raise BoundaryError("local_model_export", "unsafe_export_destination")
        names = (("model.json", "weights.tensor-tree", "tokenizer.json") if memory
                 else ("model.json", "weights.safetensors", "tokenizer.json"))
        if memory and {path.name for path in target.iterdir()} != set(names):
            raise BoundaryError("local_model_export", "unsafe_export_destination")
        for name in names:
            if not _ordinary(target / name, directory=False):
                raise BoundaryError("local_model_export", "unsafe_export_destination")
    return target


def _eligible(model: Manifest) -> None:
    # Cheap, exact metadata gate before reading any weights or invoking a backend.
    from stpd.fullrun.text_menu_inputs import IDENTITY as TEXT_MENU_IDENTITY
    from stpd.policy.token_decision import check_model
    from stpd.workers.token_worker import MODEL_SCHEMA

    info = model.parameters.value()
    config = info.get("config")
    backbone = info.get("backbone")
    if (model.kind != "model" or info.get("schema") != MODEL_SCHEMA
            or info.get("qualification") != "engineering_only"
            or info.get("serializer") != TEXT_MENU_IDENTITY
            or not isinstance(config, dict)
            or config.get("recipe") not in {"stage1a.b.s.v2", "stage1a.dsimple.s.v1"}
            or config.get("device") != "cpu"
            or not isinstance(backbone, dict) or backbone.get("kind") != "scratch"):
        raise BoundaryError("local_model_export", "unsupported_model_for_offline_export")
    try:
        check_model(model)
    except (BoundaryError, ValueError, KeyError, TypeError) as error:
        raise BoundaryError("local_model_export", "unsupported_model_for_offline_export") from error


def _memory_lineage(store: Any, owner: Any, model: Manifest) -> str:
    """Cheap Workbench admission before any derivative bytes or private child."""
    from stpd.fullrun.text_menu_human_import import (
        SOURCE_SCHEMA,
        load_human_text_source,
        load_verified_human_text_bundle,
    )
    from stpd.workers.memory_run import INPUT_SCHEMA_V2, MODEL_SCHEMA, RUN_SCHEMA

    info = model.parameters.value()
    if (model.kind != "model" or info.get("schema") != MODEL_SCHEMA
            or info.get("partition") != "train"
            or info.get("qualification") != "engineering_only"
            or sorted(p.role for p in model.parents) !=
            ["checkpoint", "run", "training_input"]):
        raise BoundaryError("local_model_export", "unsupported_model_for_offline_export")
    run_id = model.parent("run")
    run = store.get_manifest(run_id)
    run_info = run.parameters.value()
    operation_id = digest(run_info.get("operation_id"),
                          "local_model_export.operation_id", length=32)
    training_input = store.get_manifest(model.parent("training_input"))
    source_id = training_input.parent("source")
    source = store.get_manifest(source_id)
    if (run.kind != "run" or run.producer != model.producer
            or run_info.get("schema") != RUN_SCHEMA
            or run_info.get("partition") != "train"
            or run.parent("training_input") != training_input.artifact_id
            or training_input.producer != model.producer
            or training_input.parameters.value().get("schema") != INPUT_SCHEMA_V2
            or source.kind != "dataset"
            or source.parameters.value().get("schema") != SOURCE_SCHEMA):
        raise BoundaryError("local_model_export", "memory_lineage_mismatch")
    checked_source, _rows = load_human_text_source(store, source_id)
    if checked_source != source:
        raise BoundaryError("local_model_export", "human_source_identity_mismatch")
    source_ids = {parent.artifact_id for parent in source.parents}
    runs: set[str] = set()
    for evidence_id in source_ids:
        _, bundle, _ = load_verified_human_text_bundle(store, evidence_id)
        runs.update(bundle.session_id + "/" + run for run in bundle.run_ids)
    owner.ledger.require_training_use(source_id, source_ids, runs, operation_id)
    completed = ObjectStoreRunReporter(store, store.blobs).completed(run_id)
    if (completed is None or completed.producer != model.producer
            or completed.parent("model") != model.artifact_id
            or completed.parent("training_input") != training_input.artifact_id
            or completed.parent("checkpoint") != model.parent("checkpoint")):
        raise BoundaryError("local_model_export", "completed_memory_result_required")
    return run_id


def _verify_export(store: Any, model: Manifest, destination: Path) -> int:
    from stpd.policy.token_decision import TokenDecisionScorer

    if not _ordinary(destination, directory=True):
        raise BoundaryError("local_model_export", "export_identity_mismatch")
    for name in ("model.json", "weights.safetensors", "tokenizer.json"):
        if not _ordinary(destination / name, directory=False):
            raise BoundaryError("local_model_export", "export_identity_mismatch")
    scorer = TokenDecisionScorer(destination)
    if scorer.artifact != model:
        raise BoundaryError("local_model_export", "export_identity_mismatch")
    return sum(item.size for item in model.payloads)


class LocalModelExport:
    """One durable slot; only explicit POST may export or reconcile it."""

    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.thread: threading.Thread | None = None
        self.lock = threading.RLock()

    def _workspace(self) -> LocalWorkspace:
        if self.config.research_workspace is not None:
            selected = open_registered_workspace(self.config.research_workspace)
            if selected is None:
                raise BoundaryError("local_model_export", "workspace_required")
            return selected
        from spireagent.workbench.managed_local_workspace import inspect_managed_workspace

        selected = inspect_managed_workspace(self.config.state_dir).get("workspace")
        if not isinstance(selected, LocalWorkspace):
            raise BoundaryError("local_model_export", "workspace_required")
        return selected

    def _memory_owner(self, store: Any) -> Any:
        owner, selected_store, _ = LocalDatasetService(self.config)._selected()
        actual = getattr(getattr(store, "blobs", None), "root", None)
        expected = getattr(getattr(selected_store, "blobs", None), "root", None)
        if not isinstance(actual, Path) or actual != expected:
            raise BoundaryError("local_model_export", "workspace_changed")
        return owner

    def _path(self) -> Path:
        return self.config.state_dir / OPERATION_FILE

    def _read(self) -> dict[str, Any]:
        path = self._path()
        if not path.exists() and not path.is_symlink():
            return {"status": "idle"}
        if not _ordinary(path, directory=False):
            raise BoundaryError("local_model_export", "operation_recovery_required")
        try:
            if path.stat().st_size > 4096:
                raise ValueError
            value = json.loads(path.read_bytes())
            if (not isinstance(value, dict) or value.get("schema") not in {SCHEMA, SCHEMA_V2}
                    or value.get("status") not in {"pending", "completed", "failed"}
                    or not isinstance(value.get("store_root"), str)):
                raise ValueError
            if value["schema"] == SCHEMA_V2:
                if (value.get("model_type") != "memory"
                        or not isinstance(value.get("run_id"), str)):
                    raise ValueError
                digest(value["run_id"], "local_model_export.run_id")
            digest(value["model_id"], "local_model_export.model_id")
            digest(value["operation_id"], "local_model_export.operation_id", length=32)
            if value["status"] == "completed" and (
                type(value.get("payload_bytes")) is not int or value["payload_bytes"] < 0
            ):
                raise ValueError
            if value["status"] == "failed" and not isinstance(value.get("error_code"), str):
                raise ValueError
            return value
        except (OSError, ValueError, KeyError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_model_export", "operation_recovery_required") from error

    def _public(self, value: dict[str, Any]) -> dict[str, Any]:
        operation = {key: value[key] for key in
                     ("status", "operation_id", "model_id", "model_type",
                      "payload_bytes", "error_code")
                     if key in value}
        if (operation["status"] == "pending"
                and (self.thread is None or not self.thread.is_alive())):
            from spireagent.workbench.developer_server import instance_lock

            lock_path = self.config.state_dir / LOCK_FILE
            if not _ordinary(lock_path, directory=False):
                operation = {**operation, "status": "interrupted",
                             "error_code": "previous_export_outcome_unknown"}
            else:
                try:
                    with instance_lock(lock_path):
                        operation = {**operation, "status": "interrupted",
                                     "error_code": "previous_export_outcome_unknown"}
                except BoundaryError as error:
                    if error.code != "already_running":
                        raise
        return {"schema": value.get("schema", SCHEMA), "operation": operation}

    def status(self) -> dict[str, Any]:
        with self.lock:
            value = self._read()
            result = self._public(value)
            try:
                selected = self._workspace()
                root = getattr(getattr(selected.store, "blobs", None), "root", None)
                result["availability"] = (
                    "ready" if value["status"] == "idle" or str(root) == value["store_root"]
                    else "workspace_changed"
                )
            except BoundaryError as error:
                result["availability"] = "workspace_required"
                result["reason"] = error.code
            return result

    def verified_for_registration(self, model_id: object) -> Path:
        """Explicitly recheck the completed export against the selected store."""
        identity = digest(model_id, "local_model_export.model_id")
        with self.lock:
            operation = self._read()
            if operation.get("status") != "completed" or operation.get("model_id") != identity:
                raise BoundaryError("local_model_export", "verified_export_required")
            workspace = self._workspace()
            root = getattr(getattr(workspace.store, "blobs", None), "root", None)
            if not isinstance(root, Path) or operation["store_root"] != str(root):
                raise BoundaryError("local_model_export", "workspace_changed")
            model = workspace.store.get_manifest(identity)
            if model.parameters.value().get("schema") == "stpd/experimental-m2-model-v1":
                raise BoundaryError("local_model_export", "memory_registration_not_ready")
            _eligible(model)
            destination = self.config.state_dir / EXPORT_ROOT / identity
            _verify_export(workspace.store, model, destination)
            return destination

    def _finish(self, operation_id: str, **updates: Any) -> None:
        current = self._read()
        if (current.get("operation_id") != operation_id
                or current.get("status") != "pending"):
            raise BoundaryError("local_model_export", "operation_superseded")
        atomic_json(self._path(), {**current, **updates})

    def start(self, model_id: object) -> dict[str, Any]:
        from spireagent.workbench.developer_server import instance_lock

        identity = digest(model_id, "local_model_export.model_id")
        with self.lock:
            previous = self._read()
            if (previous["status"] == "pending" and self.thread is not None
                    and self.thread.is_alive()):
                if previous["model_id"] == identity:
                    return self._public(previous)
                raise BoundaryError("local_model_export", "export_in_progress")
            if previous["status"] == "pending" and previous["model_id"] != identity:
                raise BoundaryError("local_model_export", "previous_export_outcome_unknown")
            workspace = self._workspace()
            store = workspace.store
            root = getattr(getattr(store, "blobs", None), "root", None)
            if not isinstance(root, Path):
                raise BoundaryError("local_model_export", "unsupported_workspace_store")
            if (previous["status"] == "pending"
                    and previous["store_root"] != str(root)):
                raise BoundaryError("local_model_export", "workspace_changed")
            model = store.get_manifest(identity)
            memory = model.parameters.value().get("schema") == "stpd/experimental-m2-model-v1"
            run_id = _memory_lineage(store, self._memory_owner(store), model) if memory else None
            if not memory:
                _eligible(model)
            lock_path = self.config.state_dir / LOCK_FILE
            if lock_path.is_symlink() or (lock_path.exists()
                                          and not _ordinary(lock_path, directory=False)):
                raise BoundaryError("local_model_export", "operation_recovery_required")
            held: AbstractContextManager[None] = instance_lock(lock_path)
            try:
                held.__enter__()
            except BoundaryError as error:
                if error.code == "already_running":
                    raise BoundaryError("local_model_export", "export_in_progress") from error
                raise
            try:
                destination = _destination(self.config, identity, memory=memory)
                operation = {"schema": SCHEMA_V2 if memory else SCHEMA,
                             "status": "pending",
                             "operation_id": uuid.uuid4().hex, "model_id": identity,
                             "store_root": str(root)}
                if memory:
                    assert run_id is not None
                    operation.update(model_type="memory", run_id=run_id)
                atomic_json(self._path(), operation)
                thread = threading.Thread(
                    target=self._run, args=(held, operation["operation_id"], store, model,
                                            destination, run_id),
                    name="local-model-export", daemon=True,
                )
                self.thread = thread
                thread.start()
                held = None  # type: ignore[assignment]
                return self._public(operation)
            finally:
                if held is not None:
                    held.__exit__(None, None, None)

    def _memory_child(self, operation_id: str, store: Any, model: Manifest,
                      destination: Path, run_id: str, *,
                      on_started: Any) -> int:
        root = getattr(getattr(store, "blobs", None), "root", None)
        if not isinstance(root, Path):
            raise BoundaryError("local_model_export", "unsupported_workspace_store")
        command_name = "verify-memory-export" if destination.exists() else "export-memory"
        command = [sys.executable, "-m", "spireagent.research_cli", "--store", str(root),
                   command_name, "--run", run_id, "--model", model.artifact_id,
                   "--destination", str(destination)]
        environment = dict(os.environ)
        for name in ("STPD_HUB_ADMIN_TOKEN", "PYTHONPATH", "PYTHONHOME"):
            environment.pop(name, None)
        log_path = self.config.state_dir / ("local-model-export-" + operation_id + ".log")
        exit_code, captured = private_child(command, log_path, environment,
                                            on_started=on_started)
        if exit_code:
            raise BoundaryError("local_model_export", "memory_export_process_failed")
        try:
            value = json.loads(captured)
            if (not isinstance(value, dict) or value.get("model_id") != model.artifact_id
                    or value.get("run_id") != run_id
                    or value.get("package_schema")
                    != "stpd/experimental-m2-portable-policy-v1"
                    or type(value.get("payload_bytes")) is not int
                    or value["payload_bytes"] < 1):
                raise ValueError
            return cast(int, value["payload_bytes"])
        except (ValueError, KeyError, TypeError) as error:
            raise BoundaryError("local_model_export", "memory_export_result_invalid") from error

    def _run(self, held: AbstractContextManager[None], operation_id: str, store: Any,
             model: Manifest, destination: Path, run_id: str | None) -> None:
        verified = False
        child_started = False

        def mark_started() -> None:
            nonlocal child_started
            child_started = True

        try:
            if run_id is None:
                from stpd.policy.token_decision import export_token_model

                if not destination.exists():
                    export_token_model(store, model.artifact_id, destination)
                count = _verify_export(store, model, destination)
            else:
                _memory_lineage(store, self._memory_owner(store), model)
                count = self._memory_child(operation_id, store, model, destination,
                                           run_id, on_started=mark_started)
            verified = True
            with self.lock:
                self._finish(operation_id, status="completed", payload_bytes=count)
        except Exception as error:
            if not verified and not child_started:
                code = error.code if isinstance(error, BoundaryError) else "export_or_verify_failed"
                try:
                    with self.lock:
                        self._finish(operation_id, status="failed", error_code=code)
                except (BoundaryError, OSError, ValueError):
                    # The durable pending record remains an unknown outcome on restart.
                    pass
            # A verified export whose completion journal cannot be written remains
            # pending/unknown; never replace it with a false failure record.
        finally:
            held.__exit__(None, None, None)
