"""Explicit offline export of one local text-menu engineering model.

The fixed private destination is not a policy registration or a loaded runtime.
An unfinished journal is observed as interrupted after restart, never replayed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import uuid
from contextlib import AbstractContextManager
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any

from spireagent.artifact_contracts import Manifest, Payload
from spireagent.json_boundary import BoundaryError, digest
from spireagent.package_identity import file_sha256
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ProjectConfig, atomic_json
from spireagent.workbench.local_model_dependencies import (
    require_local_models,
    require_native_models,
)
from spireagent.workbench.local_workspace import LocalWorkspace, open_registered_workspace
from spireagent.workbench.memory_recipe import (
    V2_MEMORY_RECIPES,
    input_profile_for_recipe,
    recipe_for_memory_config,
    recorded_memory_recipe,
)
from spireagent.workbench.research_process import private_child
from stpd.native_code_scope import is_native_model_schema
from stpd.structured_code_scope import (
    is_structured_model_schema,
    require_structured_model_package,
)

SCHEMA = "stpd/local-model-export-operation-v1"
SCHEMA_V2 = "stpd/local-model-export-operation-v2"
RECEIPT_SCHEMA = "stpd/local-memory-export-verification-v1"
OPERATION_FILE = "local-model-export-operation.json"
LOCK_FILE = ".local-model-export.lock"
EXPORT_ROOT = "model-exports"
SUPPORT_SCHEMA = "stpd/local-model-export-support-v1"


def _ordinary(path: Path, *, directory: bool) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if getattr(metadata, "st_file_attributes", 0) & 0x400:
        return False
    mode = metadata.st_mode
    return stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)


def _destination(config: ProjectConfig, model_id: str, *, memory: bool = False,
                 structured: bool = False, native: bool = False) -> Path:
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
        names = (("model.json", "weights.tensor-tree") if structured or native else
                 ("model.json", "weights.tensor-tree", "tokenizer.json") if memory
                 else ("model.json", "weights.safetensors", "tokenizer.json"))
        if (memory or structured or native) and {path.name for path in target.iterdir()} != set(
            names
        ):
            raise BoundaryError("local_model_export", "unsafe_export_destination")
        for name in names:
            if not _ordinary(target / name, directory=False):
                raise BoundaryError("local_model_export", "unsafe_export_destination")
    return target


def _token_metadata_supported(model: Manifest) -> bool:
    """The token family's metadata gate, shared by discovery and actual export."""
    from stpd.fullrun.text_menu_inputs import IDENTITY as TEXT_MENU_IDENTITY

    info = model.parameters.value()
    config = info.get("config")
    backbone = info.get("backbone")
    return not (model.kind != "model" or info.get("schema") != "stpd/stage1a-model-v1"
            or info.get("qualification") != "engineering_only"
            or info.get("serializer") != TEXT_MENU_IDENTITY
            or not isinstance(config, dict)
            or config.get("recipe") not in {"stage1a.b.s.v2", "stage1a.dsimple.s.v1"}
            or config.get("device") != "cpu"
            or not isinstance(backbone, dict) or backbone.get("kind") != "scratch")


def _eligible(model: Manifest) -> None:
    # Discovery shares the metadata gate; only real export imports the scorer.
    if not _token_metadata_supported(model):
        raise BoundaryError("local_model_export", "unsupported_model_for_offline_export")
    from stpd.policy.token_decision import check_model

    try:
        check_model(model)
    except (BoundaryError, ValueError, KeyError, TypeError) as error:
        raise BoundaryError("local_model_export", "unsupported_model_for_offline_export") from error


def _memory_lineage(store: Any, owner: Any, model: Manifest) -> str:
    """Cheap Workbench admission before any derivative bytes or private child."""
    from stpd.fullrun.managed_text_menu_import import (
        SOURCE_SCHEMA as MANAGED_SOURCE_SCHEMA,
    )
    from stpd.fullrun.managed_text_menu_import import (
        load_managed_text_menu_source,
    )
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
    if info.get("config") != run_info.get("config"):
        raise BoundaryError("local_model_export", "memory_lineage_mismatch")
    operation_id = digest(run_info.get("operation_id"),
                          "local_model_export.operation_id", length=32)
    training_input = store.get_manifest(model.parent("training_input"))
    source_id = training_input.parent("source")
    source = store.get_manifest(source_id)
    try:
        recipe = recipe_for_memory_config(
            info.get("config"),
            projection_config=training_input.parameters.value().get("projection_config"))
    except ValueError as error:
        raise BoundaryError("local_model_export", "unsupported_workbench_memory_config") from error
    managed = recipe in V2_MEMORY_RECIPES
    if (run.kind != "run" or run.producer != model.producer
            or run_info.get("schema") != RUN_SCHEMA
            or run_info.get("partition") != "train"
            or run.parent("training_input") != training_input.artifact_id
            or training_input.producer != model.producer
            or training_input.parameters.value().get("schema") != INPUT_SCHEMA_V2
            or source.kind != "dataset"
            or source.parameters.value().get("schema") != (
                MANAGED_SOURCE_SCHEMA if managed else SOURCE_SCHEMA)):
        raise BoundaryError("local_model_export", "memory_lineage_mismatch")
    if managed:
        checked = load_managed_text_menu_source(store, source_id)
        if checked.manifest != source:
            raise BoundaryError("local_model_export", "managed_source_identity_mismatch")
        source_ids = {source_id}
        runs = {checked.split_run_id}
    else:
        checked_source, _rows = load_human_text_source(store, source_id)
        if checked_source != source:
            raise BoundaryError("local_model_export", "human_source_identity_mismatch")
        source_ids = {parent.artifact_id for parent in source.parents}
        runs = set()
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


class _DownloadedStructuredModel:
    """A result cache with only own payloads; never masquerades as a lineage store."""

    def __init__(self, directory: Path, identity: str) -> None:
        if not _ordinary(directory, directory=True) or not _ordinary(
                directory / "manifest.json", directory=False):
            raise BoundaryError("local_model_export", "unsafe_download_cache")
        self.model = Manifest.from_bytes((directory / "manifest.json").read_bytes(), identity)
        if not _ordinary(directory / "download.json", directory=False):
            raise BoundaryError("local_model_export", "unsafe_download_cache")
        receipt = json.loads((directory / "download.json").read_bytes())
        if (not isinstance(receipt, dict)
                or receipt.get("schema") != "stpd/result-download-v1"
                or receipt.get("artifact_id") != identity
                or self.model.kind != "model"
                or not (is_structured_model_schema(self.model.parameters.value().get("schema"))
                        or is_native_model_schema(self.model.parameters.value().get("schema")))):
            raise BoundaryError("local_model_export", "structured_download_required")
        self.directory = directory
        self.blobs = SimpleNamespace(root=directory)

    def get_manifest(self, identity: str) -> Manifest:
        if identity != self.model.artifact_id:
            raise BoundaryError("local_model_export", "private_ancestry_unavailable")
        return self.model

    def bytes(self, payload: Payload, *, maximum: int) -> bytes:
        path = self.directory / (payload.sha256 + ".bin")
        if (payload not in self.model.payloads or payload.size > maximum
                or not _ordinary(path, directory=False) or path.stat().st_size != payload.size):
            raise BoundaryError("local_model_export", "download_payload_invalid")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != payload.sha256:
            raise BoundaryError("local_model_export", "download_payload_integrity")
        return raw

    def read_payload(self, payload: Payload) -> Any:
        # Same authorized own-byte adapter used by the native domain exporter.
        yield self.bytes(payload, maximum=payload.size)


def _native_package(store: Any, model: Manifest, destination: Path, *,
                    materialize: bool = False) -> int:
    from stpd.policy.native_structured_export import (
        MAX_MANIFEST_BYTES,
        MAX_WEIGHTS_BYTES,
        export_native_model,
        load_native_package,
        require_native_model_package,
    )

    if not is_native_model_schema(model.parameters.value().get("schema")):
        raise BoundaryError("local_model_export", "native_model_required")
    if not destination.exists() and not destination.is_symlink():
        if not materialize:
            raise BoundaryError("local_model_export", "verified_export_required")
        export_native_model(store, model.artifact_id, destination)
    if (
        not _ordinary(destination, directory=True)
        or {path.name for path in destination.iterdir()} != {"model.json", "weights.tensor-tree"}
        or any(
            not _ordinary(destination / name, directory=False)
            for name in ("model.json", "weights.tensor-tree")
        )
    ):
        raise BoundaryError("local_model_export", "unsafe_export_destination")
    package, loaded = load_native_package(destination)
    del loaded
    require_native_model_package(model, package)
    # Reconciliation verifies existing own bytes. It never re-exports or reads parents.
    for role, name, limit in (("package_manifest", "model.json", MAX_MANIFEST_BYTES),
                             ("weights", "weights.tensor-tree", MAX_WEIGHTS_BYTES)):
        payload = model.payload(role)
        if not 0 < payload.size <= limit:
            raise BoundaryError("local_model_export", "download_payload_invalid")
        raw = bytearray()
        for chunk in store.read_payload(payload):
            if not isinstance(chunk, bytes) or len(raw) + len(chunk) > payload.size:
                raise BoundaryError("local_model_export", "download_payload_integrity")
            raw.extend(chunk)
        if (len(raw) != payload.size or hashlib.sha256(raw).hexdigest() != payload.sha256
                or bytes(raw) != (destination / name).read_bytes()):
            raise BoundaryError("local_model_export", "export_identity_mismatch")
    return sum(payload.size for payload in model.payloads)


def _structured_package(store: Any, model: Manifest, destination: Path, *,
                        materialize: bool = False) -> int:
    """Consume only authorized closed model bytes, without opening training ancestry."""
    from stpd.policy.structured_export import (
        MAX_MANIFEST_BYTES,
        MAX_WEIGHTS_BYTES,
        load_structured_package,
    )

    info = model.parameters.value()
    if (model.kind != "model" or not is_structured_model_schema(info.get("schema"))
            or {item.role for item in model.payloads} != {"package_manifest", "weights"}):
        raise BoundaryError("local_model_export", "structured_model_required")
    payloads = (("package_manifest", "model.json", MAX_MANIFEST_BYTES),
                ("weights", "weights.tensor-tree", MAX_WEIGHTS_BYTES))
    with tempfile.TemporaryDirectory(prefix="structured-package-", dir=destination.parent) as tmp:
        checked = Path(tmp)
        for role, name, limit in payloads:
            payload = model.payload(role)
            raw = store.bytes(payload, maximum=limit)
            (checked / name).write_bytes(raw)
        package, _ = load_structured_package(checked)
        require_structured_model_package(model, package)
        if destination.exists() or destination.is_symlink():
            load_structured_package(destination)
            if any((destination / name).read_bytes() != (checked / name).read_bytes()
                   for _, name, _ in payloads):
                raise BoundaryError("local_model_export", "export_identity_mismatch")
        elif materialize:
            shutil.copytree(checked, destination)
        else:
            raise BoundaryError("local_model_export", "verified_export_required")
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

    def _source(self, identity: str) -> Any:
        download_root = self.config.state_dir / "downloads"
        if download_root.is_symlink():
            raise BoundaryError("local_model_export", "unsafe_download_cache")
        directory = download_root / identity
        if directory.exists() or directory.is_symlink():
            if not _ordinary(directory, directory=True) or not _ordinary(
                    directory / "manifest.json", directory=False):
                raise BoundaryError("local_model_export", "unsafe_download_cache")
            cached = Manifest.from_bytes((directory / "manifest.json").read_bytes(), identity)
            if (is_structured_model_schema(cached.parameters.value().get("schema"))
                    or is_native_model_schema(cached.parameters.value().get("schema"))):
                return _DownloadedStructuredModel(directory, identity)
        return self._workspace().store

    def _memory_owner(self, store: Any) -> Any:
        from spireagent.workbench.local_dataset import LocalDatasetService

        owner, selected_store, _ = LocalDatasetService(self.config)._selected()
        actual = getattr(getattr(store, "blobs", None), "root", None)
        expected = getattr(getattr(selected_store, "blobs", None), "root", None)
        if not isinstance(actual, Path) or actual != expected:
            raise BoundaryError("local_model_export", "workspace_changed")
        return owner

    def support(self, model_id: object) -> dict[str, Any]:
        """Project closed metadata support without weights, backend or readiness checks.

        Export, registration and loading retain their own byte/lineage/source gates.
        A recognized family here is never a verified or executable model.
        """
        identity = digest(model_id, "local_model_export.model_id")
        result: dict[str, Any] = {
            "schema": SUPPORT_SCHEMA, "model_id": identity,
            "status": "unsupported", "verification_state": "not_checked",
            "reason_code": "unsupported_model_for_offline_export",
        }
        source = self._source(identity)
        model = source.get_manifest(identity)
        info = model.parameters.value()
        if model.kind != "model":
            return result
        schema = info.get("schema")
        memory_recipe = None
        if is_native_model_schema(schema):
            model_type, profile = "native", "native-logical-v1"
        elif is_structured_model_schema(schema):
            model_type, profile = "structured", "text-menu-m2-v2"
        elif schema == "stpd/experimental-m2-model-v1":
            memory_recipe = recorded_memory_recipe(source, model)
            if memory_recipe is None:
                return result
            model_type = "memory"
            profile = ("text-menu-m2-v2" if memory_recipe in V2_MEMORY_RECIPES
                       else "text-menu-m2-v1")
        else:
            if not _token_metadata_supported(model):
                return result
            model_type, profile = "token", "text-menu-v1"
        result.pop("reason_code")
        result.update(status="supported", model_type=model_type, runtime_profile=profile)
        if memory_recipe is not None:
            result["memory_recipe"] = memory_recipe
        return result

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
                receipt = value.get("verified_receipt")
                if receipt is not None:
                    if (value["status"] != "completed" or not isinstance(receipt, dict)
                            or set(receipt) != {"schema", "operation_id", "store_root",
                                                    "model_id", "run_id", "result_id",
                                                    "checkpoint_id", "package_sha256",
                                                    "package_size", "weights_sha256",
                                                    "weights_size", "tokenizer_sha256",
                                                    "tokenizer_size", "payload_bytes"}
                            or receipt["schema"] != RECEIPT_SCHEMA
                            or any(receipt[key] != value[key] for key in
                                   ("operation_id", "store_root", "model_id", "run_id"))):
                        raise ValueError
                    for key in ("result_id", "checkpoint_id", "package_sha256",
                                "weights_sha256", "tokenizer_sha256"):
                        digest(receipt[key], "local_model_export." + key)
                    for key in ("package_size", "weights_size", "tokenizer_size",
                                "payload_bytes"):
                        if type(receipt[key]) is not int or receipt[key] <= 0:
                            raise ValueError
                    if (receipt["payload_bytes"] != receipt["weights_size"]
                            + receipt["tokenizer_size"]
                            or receipt["payload_bytes"] != value.get("payload_bytes")):
                        raise ValueError
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
                selected = (self._source(value["model_id"])
                            if value.get("model_type") in {"structured", "native"}
                            else self._workspace().store)
                root = getattr(getattr(selected, "blobs", None), "root", None)
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
            source = self._source(identity)
            root = getattr(getattr(source, "blobs", None), "root", None)
            if not isinstance(root, Path) or operation["store_root"] != str(root):
                raise BoundaryError("local_model_export", "workspace_changed")
            model = source.get_manifest(identity)
            if model.parameters.value().get("schema") == "stpd/experimental-m2-model-v1":
                raise BoundaryError("local_model_export", "memory_registration_not_ready")
            destination = self.config.state_dir / EXPORT_ROOT / identity
            if is_native_model_schema(model.parameters.value().get("schema")):
                _native_package(source, model, destination)
                return destination
            if is_structured_model_schema(model.parameters.value().get("schema")):
                _structured_package(source, model, destination)
                return destination
            _eligible(model)
            _verify_export(source, model, destination)
            return destination

    def verified_memory_for_registration(self, model_id: object, *,
                                         deadline: float | None = None) -> Path:
        """Bind a child-verified M2 export to current admission and immutable store facts."""
        from stpd.policy.memory_export import validate_memory_package

        identity = digest(model_id, "local_model_export.model_id")
        with self.lock:
            operation = self._read()
            if (operation.get("schema") != SCHEMA_V2
                    or operation.get("status") != "completed"
                    or operation.get("model_id") != identity
                    or operation.get("model_type") != "memory"):
                raise BoundaryError("local_model_export", "verified_export_required")
            receipt = operation.get("verified_receipt")
            if receipt is None:
                raise BoundaryError("local_model_export", "verified_export_receipt_required")
            workspace = self._workspace()
            root = getattr(getattr(workspace.store, "blobs", None), "root", None)
            if not isinstance(root, Path) or operation["store_root"] != str(root):
                raise BoundaryError("local_model_export", "workspace_changed")
            model = workspace.store.get_manifest(identity)
            run_id = _memory_lineage(workspace.store,
                                     self._memory_owner(workspace.store), model)
            if operation["run_id"] != run_id:
                raise BoundaryError("local_model_export", "memory_lineage_mismatch")
            destination = self.config.state_dir / EXPORT_ROOT / identity
            if not _ordinary(destination, directory=True):
                raise BoundaryError("local_model_export", "verified_export_required")
            if deadline is not None and monotonic() >= deadline:
                raise BoundaryError("local_model_registration", "registration_timeout")
            training_input = workspace.store.get_manifest(model.parent("training_input"))
            try:
                recipe = recipe_for_memory_config(
                    model.parameters.value().get("config"),
                    projection_config=training_input.parameters.value().get(
                        "projection_config"))
            except ValueError as error:
                raise BoundaryError("local_model_export", "memory_lineage_mismatch") from error
            profile = input_profile_for_recipe(recipe)
            package, weights, tokenizer, _ = validate_memory_package(
                destination, input_profile=profile)
            store: Any = workspace.store
            run = store.get_manifest(run_id)
            completed = ObjectStoreRunReporter(store, store.blobs).completed(run_id)
            if completed is None:
                raise BoundaryError("local_model_export", "completed_memory_result_required")
            expected_ids = {"source": training_input.parent("source"),
                            "training_input": training_input.artifact_id,
                            "run": run_id, "result": completed.artifact_id,
                            "checkpoint": completed.parent("checkpoint"),
                            "model": identity}
            input_info = training_input.parameters.value()
            if (package["ids"] != expected_ids
                    or package["config"] != run.parameters.value().get("config")
                    or package["input_digest"] != input_info.get("input_digest")
                    or package["projection_config"] != input_info.get("projection_config")
                    or package["source_map_sha256"]
                    != training_input.payload("source_map").sha256
                    or package["source_event_count"] != input_info.get("source_event_count")
                    or package["weights"]["sha256"] != model.payload("weights").sha256
                    or package["weights"]["size"] != model.payload("weights").size
                    or package["tokenizer"]["sha256"] != model.payload("tokenizer").sha256
                    or package["tokenizer"]["size"] != model.payload("tokenizer").size
                    or receipt["result_id"] != completed.artifact_id
                    or receipt["checkpoint_id"] != completed.parent("checkpoint")):
                raise BoundaryError("local_model_export", "export_identity_mismatch")
            for name, key in (("model.json", "package_sha256"),
                              ("weights.tensor-tree", "weights_sha256"),
                              ("tokenizer.json", "tokenizer_sha256")):
                path = destination / name
                if not _ordinary(path, directory=False) or file_sha256(path) != receipt[key]:
                    raise BoundaryError("local_model_export", "export_identity_mismatch")
            if (receipt["package_size"] != (destination / "model.json").stat().st_size
                    or receipt["weights_size"] != len(weights)
                    or receipt["tokenizer_size"] != len(tokenizer)
                    or receipt["payload_bytes"] != len(weights) + len(tokenizer)):
                raise BoundaryError("local_model_export", "export_identity_mismatch")
            return destination

    def verified_memory_recipe_for_registration(self, model_id: object, *,
                                                 deadline: float | None = None) -> str:
        """Read the recipe from the same verified immutable model/run lineage."""
        identity = digest(model_id, "local_model_export.model_id")
        with self.lock:
            operation = self._read()
            if (operation.get("schema") != SCHEMA_V2
                    or operation.get("status") != "completed"
                    or operation.get("model_id") != identity
                    or operation.get("model_type") != "memory"
                    or operation.get("verified_receipt") is None):
                raise BoundaryError("local_model_export", "verified_export_required")
            receipt = operation["verified_receipt"]
            if (receipt.get("model_id") != identity
                    or receipt.get("run_id") != operation.get("run_id")):
                raise BoundaryError("local_model_export", "memory_lineage_mismatch")
            workspace = self._workspace()
            root = getattr(getattr(workspace.store, "blobs", None), "root", None)
            if not isinstance(root, Path) or operation["store_root"] != str(root):
                raise BoundaryError("local_model_export", "workspace_changed")
            model = workspace.store.get_manifest(identity)
            run = workspace.store.get_manifest(operation["run_id"])
            if (model.kind != "model" or model.parent("run") != run.artifact_id
                    or run.kind != "run"
                    or model.parameters.value().get("config")
                    != run.parameters.value().get("config")):
                raise BoundaryError("local_model_export", "memory_lineage_mismatch")
            if deadline is not None and monotonic() >= deadline:
                raise BoundaryError("local_model_registration", "registration_timeout")
            try:
                training_input = workspace.store.get_manifest(model.parent("training_input"))
                return recipe_for_memory_config(
                    model.parameters.value().get("config"),
                    projection_config=training_input.parameters.value().get(
                        "projection_config"))
            except ValueError as error:
                raise BoundaryError(
                    "local_model_export", "unsupported_workbench_memory_config",
                ) from error

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
            store = self._source(identity)
            root = getattr(getattr(store, "blobs", None), "root", None)
            if not isinstance(root, Path):
                raise BoundaryError("local_model_export", "unsupported_workspace_store")
            if (previous["status"] == "pending"
                    and previous["store_root"] != str(root)):
                raise BoundaryError("local_model_export", "workspace_changed")
            model = store.get_manifest(identity)
            native = is_native_model_schema(model.parameters.value().get("schema"))
            if native:
                require_native_models("local_model_export")
            else:
                require_local_models("local_model_export")
            memory = model.parameters.value().get("schema") == "stpd/experimental-m2-model-v1"
            run_id = _memory_lineage(store, self._memory_owner(store), model) if memory else None
            structured = is_structured_model_schema(model.parameters.value().get("schema"))
            if not memory and not structured and not native:
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
                destination = _destination(self.config, identity, memory=memory,
                                           structured=structured, native=native)
                operation = {"schema": SCHEMA_V2 if memory else SCHEMA,
                             "status": "pending",
                             "operation_id": uuid.uuid4().hex, "model_id": identity,
                             "store_root": str(root)}
                if structured:
                    operation.update(model_type="structured")
                if native:
                    operation.update(model_type="native")
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
                      on_started: Any) -> dict[str, Any]:
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
                    or value["payload_bytes"] < 1
                    or any(not isinstance(value.get(key), str)
                           or len(value[key]) != 64
                           or any(char not in "0123456789abcdef" for char in value[key])
                           for key in ("result_id", "checkpoint_id"))
                    or any(type(value.get(key)) is not int or value[key] <= 0
                           for key in ("package_size", "weights_size", "tokenizer_size"))
                    or value["payload_bytes"] != value["weights_size"]
                    + value["tokenizer_size"]
                    or any(not isinstance(value.get(key), str)
                           or len(value[key]) != 64
                           or any(char not in "0123456789abcdef" for char in value[key])
                           for key in ("package_sha256", "weights_sha256",
                                       "tokenizer_sha256"))):
                raise ValueError
            return value
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
            if is_native_model_schema(model.parameters.value().get("schema")):
                count = _native_package(store, model, destination, materialize=True)
            elif is_structured_model_schema(model.parameters.value().get("schema")):
                count = _structured_package(store, model, destination, materialize=True)
            elif run_id is None:
                from stpd.policy.token_decision import export_token_model

                if not destination.exists():
                    export_token_model(store, model.artifact_id, destination)
                count = _verify_export(store, model, destination)
            else:
                _memory_lineage(store, self._memory_owner(store), model)
                child = self._memory_child(operation_id, store, model, destination,
                                           run_id, on_started=mark_started)
                count = child["payload_bytes"]
            verified = True
            with self.lock:
                if run_id is None:
                    self._finish(operation_id, status="completed", payload_bytes=count)
                else:
                    root = getattr(getattr(store, "blobs", None), "root", None)
                    assert isinstance(root, Path)
                    receipt = {"schema": RECEIPT_SCHEMA, "operation_id": operation_id,
                               "store_root": str(root), "model_id": model.artifact_id,
                               "run_id": run_id,
                               **{key: child[key] for key in (
                                   "result_id", "checkpoint_id", "package_sha256",
                                   "package_size", "weights_sha256", "weights_size",
                                   "tokenizer_sha256", "tokenizer_size", "payload_bytes")}}
                    self._finish(operation_id, status="completed", payload_bytes=count,
                                 verified_receipt=receipt)
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
