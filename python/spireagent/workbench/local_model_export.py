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
from time import monotonic
from typing import Any

from spireagent.artifact_contracts import Manifest
from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, digest
from spireagent.package_identity import file_sha256
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ProjectConfig, atomic_json
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_model_dependencies import require_local_models
from spireagent.workbench.local_workspace import LocalWorkspace, open_registered_workspace
from spireagent.workbench.memory_recipe import (
    V2_MEMORY_RECIPES,
    input_profile_for_recipe,
    recipe_for_memory_config,
)
from spireagent.workbench.research_process import private_child

SCHEMA = "stpd/local-model-export-operation-v1"
SCHEMA_V2 = "stpd/local-model-export-operation-v2"
SCHEMA_V3 = "stpd/local-model-export-operation-v3"
SCHEMA_V4 = "stpd/local-model-export-operation-v4"
RECEIPT_SCHEMA = "stpd/local-memory-export-verification-v1"
PUBLIC_M0_RECEIPT_SCHEMA = "stpd/local-public-m0-export-verification-v1"
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


def _destination(config: ProjectConfig, model_id: str, *, memory: bool = False,
                 public_m0: bool = False, public_m2: bool = False) -> Path:
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
        names: tuple[str, ...]
        if public_m2:
            names = ("model.json", "weights.tensor-tree", "state_tokenizer.json")
        elif public_m0:
            from stpd.policy.token_decision import LIGHT_ACTION_M0_FILES

            names = ("model.json", *LIGHT_ACTION_M0_FILES.values())
        else:
            names = (("model.json", "weights.tensor-tree", "tokenizer.json") if memory
                     else ("model.json", "weights.safetensors", "tokenizer.json"))
        if (memory or public_m0 or public_m2) and {
            path.name for path in target.iterdir()
        } != set(names):
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


def _public_m0_lineage(store: Any, owner: Any, model: Manifest) -> dict[str, Any]:
    from stpd.fullrun.light_action_inputs import PUBLIC_SCHEMA, public_training_binding
    from stpd.policy.token_decision import (
        PUBLIC_LIGHT_ACTION_M0_MODEL_SCHEMA,
        check_light_action_m0_model,
    )
    from stpd.workers.token_worker import (
        preflight_m0_model_completion,
        verify_m0_model_completion,
    )

    try:
        config, info = check_light_action_m0_model(model)
        if (model.kind != "model" or info.get("schema") != PUBLIC_LIGHT_ACTION_M0_MODEL_SCHEMA
                or info.get("qualification") != "engineering_only"
                or config.recipe != "stage1a.dsimple.light-action.m0.s.v1"
                or sorted(parent.role for parent in model.parents)
                != ["checkpoint", "model_view", "run", "training_input"]):
            raise ValueError
        run_id = model.parent("run")
        input_id = model.parent("training_input")
        run = store.get_manifest(run_id)
        training_input = store.get_manifest(input_id)
        binding = public_training_binding(store, input_id)
        dataset_ids = tuple(binding.get("dataset_ids", ()))
        training_operation_id = binding.get("training_operation_id")
        if len(dataset_ids) != 1 or not isinstance(training_operation_id, str):
            raise ValueError
        preflight_completed = preflight_m0_model_completion(store, model)
        # Re-admit this exact immutable binding against the currently configured owner
        # before traversing completed-result payloads or verifying the export package.
        owner.require_training_datasets(store, dataset_ids, training_operation_id)
        view = store.get_manifest(training_input.parent("model_view"))
        renderer = view.parameters.value().get("serializer")
        profile = renderer.get("profile") if isinstance(renderer, dict) else None
        renderer_identity = {"public_lite": ("stpd-public-snapshot-lite-v1", "provisional"),
                             "public_compact": ("stpd-public-snapshot-compact-v2", "provisional")}
        expected_renderer = renderer_identity.get(profile) if isinstance(profile, str) else None
        if (training_input.parameters.value().get("schema") != PUBLIC_SCHEMA
                or not expected_renderer
                or renderer != {"version": expected_renderer[0], "profile": profile,
                                "status": expected_renderer[1]}
                or model.parent("model_view") != view.artifact_id
                or binding.get("model_view_id") != view.artifact_id
                or run.parent("training_input") != input_id
                or run.parameters.value().get("training_binding") != binding
                or info.get("training_binding") != binding
                or len(binding.get("dataset_ids", [])) != 1):
            raise ValueError
        completed = verify_m0_model_completion(store, model)
        if completed.artifact_id != preflight_completed.artifact_id:
            raise ValueError
        return {"run_id": run_id, "result_id": completed.artifact_id,
                "checkpoint_id": model.parent("checkpoint"), "input_id": input_id,
                "allocation_id": binding["allocation_id"],
                "view_id": binding["model_view_id"],
                "training_operation_id": binding["training_operation_id"],
                "dataset_id": binding["dataset_ids"][0], "source_profile": profile,
                "input_schema": PUBLIC_SCHEMA}
    except (BoundaryError, OSError, ValueError, KeyError, TypeError) as error:
        raise BoundaryError("local_model_export", "public_m0_lineage_invalid") from error


def _verify_public_m0_export(model: Manifest, destination: Path,
                             lineage: dict[str, Any]) -> dict[str, Any]:
    from stpd.policy.token_decision import (
        LIGHT_ACTION_M0_FILES,
        PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA,
        LightActionM0DecisionScorer,
    )

    expected_names = {"model.json", *LIGHT_ACTION_M0_FILES.values()}
    if (not _ordinary(destination, directory=True)
            or {p.name for p in destination.iterdir()} != expected_names):
        raise BoundaryError("local_model_export", "public_m0_export_inventory_mismatch")
    if any(not _ordinary(destination / name, directory=False) for name in expected_names):
        raise BoundaryError("local_model_export", "public_m0_export_inventory_mismatch")
    try:
        envelope = json.loads((destination / "model.json").read_bytes())
        if (not isinstance(envelope, dict) or set(envelope) != {"schema", "model_id", "model"}
                or envelope["schema"] != PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA
                or envelope["model_id"] != model.artifact_id):
            raise ValueError
        packaged = Manifest.from_bytes(
            (canonical_json(envelope["model"]) + "\n").encode("utf-8"), model.artifact_id,
        )
        if packaged != model:
            raise ValueError
        scorer = LightActionM0DecisionScorer(destination)
        if scorer.artifact != model or scorer.info.get("schema") != \
                "stpd/stage1a-light-action-m0-public-model-v1":
            raise ValueError
        files = {role: destination / name for role, name in LIGHT_ACTION_M0_FILES.items()}
        hashes: dict[str, str] = {}
        sizes: dict[str, int] = {}
        for role, path in files.items():
            payload = model.payload(role)
            size = path.stat().st_size
            sha = file_sha256(path)
            if size != payload.size or sha != payload.sha256:
                raise ValueError
            hashes[role] = sha
            sizes[role] = size
        package_sha = file_sha256(destination / "model.json")
        package_size = (destination / "model.json").stat().st_size
        return {
            "schema": PUBLIC_M0_RECEIPT_SCHEMA,
            "model_id": model.artifact_id,
            "export_schema": PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA,
            "model_schema": "stpd/stage1a-light-action-m0-public-model-v1",
            **lineage,
            "package_sha256": package_sha,
            "package_size": package_size,
            "payload_sha256": hashes,
            "payload_sizes": sizes,
            "payload_bytes": sum(sizes.values()),
        }
    except (OSError, ValueError, KeyError, TypeError, BoundaryError) as error:
        raise BoundaryError("local_model_export", "public_m0_export_verification_failed") from error


def _child_json_record(captured: bytes) -> dict[str, Any]:
    """Read the final machine record; model libraries may emit stdout diagnostics."""
    lines = [line for line in captured.decode("utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError("child_json_record_missing")
    value = json.loads(lines[-1])
    if not isinstance(value, dict):
        raise ValueError("child_json_record_invalid")
    return value


class LocalModelExport:
    """One durable slot; only explicit POST may export or reconcile it."""

    def __init__(self, config: ProjectConfig, *, config_path: Path | None = None) -> None:
        self.config = config
        self.config_path = config_path
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

    def _read(self, path: Path | None = None) -> dict[str, Any]:
        path = path or self._path()
        if not path.exists() and not path.is_symlink():
            return {"status": "idle"}
        if not _ordinary(path, directory=False):
            raise BoundaryError("local_model_export", "operation_recovery_required")
        try:
            if path.stat().st_size > 65536:
                raise ValueError
            value = json.loads(path.read_bytes())
            if (not isinstance(value, dict)
                    or value.get("schema") not in {SCHEMA, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4}
                    or value.get("status") not in {"pending", "completed", "failed"}
                    or not isinstance(value.get("store_root"), str)):
                raise ValueError
            if value["schema"] == SCHEMA_V4:
                fields = {"schema", "status", "operation_id", "model_id", "store_root",
                          "model_type", "profile", "lineage", "training_receipt_id"}
                if (set(value) not in (fields, fields | {"error_code"},
                                       fields | {"verified_receipt", "payload_bytes"})
                        or value.get("model_type") != "public_m2"
                        or value.get("profile") != "public-snapshot-m2-v1"
                        or not isinstance(value.get("lineage"), dict)
                        or set(value["lineage"]) != {
                            "run_id", "input_id", "checkpoint_id", "checkpoint_sha256",
                            "view_id", "allocation_id", "dataset_id", "training_operation_id",
                            "stage_id", "evaluation_id", "completion_id"}):
                    raise ValueError
                digest(value["training_receipt_id"], "local_model_export.training_receipt")
                for key, identity in value["lineage"].items():
                    if key == "completion_id" and identity is None:
                        continue
                    digest(identity, "local_model_export." + key,
                           length=32 if key == "training_operation_id" else 64)
                receipt = value.get("verified_receipt")
                if value["status"] == "completed":
                    from stpd.policy.public_m2_export import RECEIPT_SCHEMA as M2_RECEIPT

                    if (not isinstance(receipt, dict) or set(receipt) != {
                            "schema", "model_id", *value["lineage"], "package_sha256",
                            "payload_sha256", "payload_sizes", "payload_bytes"}
                            or receipt["schema"] != M2_RECEIPT
                            or receipt["model_id"] != value["model_id"]
                            or any(receipt[key] != identity for key, identity
                                   in value["lineage"].items())
                            or set(receipt["payload_sha256"]) != {"weights", "state_tokenizer"}
                            or set(receipt["payload_sizes"]) != {"weights", "state_tokenizer"}
                            or any(type(size) is not int or size <= 0
                                   for size in receipt["payload_sizes"].values())
                            or receipt["payload_bytes"] != sum(receipt["payload_sizes"].values())
                            or value.get("payload_bytes") != receipt["payload_bytes"]):
                        raise ValueError
                    digest(receipt["package_sha256"], "local_model_export.package")
                    for identity in receipt["payload_sha256"].values():
                        digest(identity, "local_model_export.payload")
                elif receipt is not None:
                    raise ValueError
            elif value["schema"] == SCHEMA_V3:
                fields = {"schema", "status", "operation_id", "model_id", "store_root",
                          "model_type", "profile", "source_profile", "run_id", "result_id",
                          "checkpoint_id",
                          "input_id", "allocation_id", "view_id", "training_operation_id",
                          "dataset_id", "input_schema"}
                allowed = (fields, fields | {"error_code"},
                           fields | {"verified_receipt", "payload_bytes"})
                if (set(value) not in allowed
                        or value.get("model_type") != "public_m0"
                        or value.get("profile") != "public-snapshot-m0-v1"
                        or value.get("input_schema") !=
                        "stpd/stage1a-light-action-m0-public-input-v1"):
                    raise ValueError
                if value["status"] == "failed" and not isinstance(value.get("error_code"), str):
                    raise ValueError
                if value.get("source_profile") not in {"public_lite", "public_compact"}:
                    raise ValueError
                for key in ("run_id", "result_id", "checkpoint_id", "input_id",
                            "allocation_id", "view_id", "dataset_id"):
                    digest(value[key], "local_model_export." + key)
                digest(value["training_operation_id"],
                       "local_model_export.training_operation_id", length=32)
                receipt = value.get("verified_receipt")
                if receipt is not None:
                    receipt_fields = {"schema", "model_id", "export_schema", "model_schema",
                                      "run_id", "result_id", "checkpoint_id", "input_id",
                                      "allocation_id", "view_id", "training_operation_id",
                                      "dataset_id", "source_profile", "input_schema",
                                      "package_sha256", "package_size", "payload_sha256",
                                      "payload_sizes", "payload_bytes"}
                    if (value["status"] != "completed" or not isinstance(receipt, dict)
                            or set(receipt) != receipt_fields
                            or receipt["schema"] != PUBLIC_M0_RECEIPT_SCHEMA
                            or receipt["export_schema"] !=
                            "stpd/stage1a-light-action-m0-public-export-v1"
                            or receipt["model_schema"] !=
                            "stpd/stage1a-light-action-m0-public-model-v1"
                            or any(receipt.get(key) != value.get(key) for key in (
                                "model_id", "run_id", "result_id", "checkpoint_id", "input_id",
                                "allocation_id", "view_id", "training_operation_id", "dataset_id",
                                "source_profile", "input_schema"))):
                        raise ValueError
                    for key in ("package_sha256",):
                        digest(receipt[key], "local_model_export." + key)
                    payload_hashes = receipt["payload_sha256"]
                    payload_sizes = receipt["payload_sizes"]
                    if (not isinstance(payload_hashes, dict) or not isinstance(payload_sizes, dict)
                            or set(payload_hashes) != {"action_codec", "state_tokenizer", "weights"}
                            or set(payload_sizes) != set(payload_hashes)):
                        raise ValueError
                    for key in payload_hashes:
                        digest(payload_hashes[key], "local_model_export.payload_sha256")
                        if type(payload_sizes[key]) is not int or payload_sizes[key] <= 0:
                            raise ValueError
                    if (type(receipt["package_size"]) is not int or receipt["package_size"] <= 0
                            or type(receipt["payload_bytes"]) is not int
                            or receipt["payload_bytes"] != sum(payload_sizes.values())
                            or value.get("payload_bytes") != receipt["payload_bytes"]):
                        raise ValueError
            elif value["schema"] == SCHEMA_V2:
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
            if (value["schema"] == SCHEMA_V3
                    and (value["status"] == "completed")
                    != (value.get("verified_receipt") is not None)):
                raise ValueError
            if value["status"] == "failed" and not isinstance(value.get("error_code"), str):
                raise ValueError
            return value
        except (OSError, ValueError, KeyError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_model_export", "operation_recovery_required") from error

    def _public(self, value: dict[str, Any]) -> dict[str, Any]:
        operation = {key: value[key] for key in
                     ("status", "operation_id", "model_id", "model_type", "profile",
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
                    with instance_lock(lock_path, create=False):
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

    def verified_public_m0_for_registration(self, model_id: object, *,
                                            deadline: float | None = None) -> Path:
        """Return only a completed public M0 export rechecked against its exact store lineage."""
        identity = digest(model_id, "local_model_export.model_id")
        with self.lock:
            if deadline is not None and monotonic() >= deadline:
                raise BoundaryError("local_model_registration", "registration_timeout")
            operation = self._read()
            if (operation.get("schema") != SCHEMA_V3
                    or operation.get("status") != "completed"
                    or operation.get("model_id") != identity
                    or operation.get("model_type") != "public_m0"
                    or operation.get("profile") != "public-snapshot-m0-v1"
                    or operation.get("verified_receipt") is None):
                raise BoundaryError("local_model_export", "verified_export_required")
            workspace = self._workspace()
            root = getattr(getattr(workspace.store, "blobs", None), "root", None)
            if not isinstance(root, Path) or operation["store_root"] != str(root):
                raise BoundaryError("local_model_export", "workspace_changed")
            model = workspace.store.get_manifest(identity)
            lineage = _public_m0_lineage(
                workspace.store, self._memory_owner(workspace.store), model,
            )
            destination = self.config.state_dir / EXPORT_ROOT / identity
            verified = _verify_public_m0_export(model, destination, lineage)
            receipt = {"schema": PUBLIC_M0_RECEIPT_SCHEMA,
                       "model_id": model.artifact_id,
                       "export_schema": verified["export_schema"],
                       "model_schema": verified["model_schema"],
                       **lineage,
                       "package_sha256": verified["package_sha256"],
                       "package_size": verified["package_size"],
                       "payload_sha256": verified["payload_sha256"],
                       "payload_sizes": verified["payload_sizes"],
                       "payload_bytes": verified["payload_bytes"]}
            if receipt != operation.get("verified_receipt"):
                raise BoundaryError("local_model_export", "verified_export_required")
            if deadline is not None and monotonic() >= deadline:
                raise BoundaryError("local_model_registration", "registration_timeout")
            return destination

    def verified_public_m2_for_registration(self, model_id: object, *,
                                           deadline: float | None = None) -> Path:
        from stpd.policy.public_m2_export import model_lineage, receipt

        identity = digest(model_id, "local_model_export.model_id")
        with self.lock:
            operation = self._read()
            if operation.get("model_id") != identity:
                operation = self._read(self.config.state_dir / "model-export-receipts" /
                                       (identity + ".json"))
            if (operation.get("schema") != SCHEMA_V4
                    or operation.get("status") != "completed"
                    or operation.get("model_id") != identity):
                raise BoundaryError("local_model_export", "verified_export_required")
            workspace = self._workspace()
            root = getattr(getattr(workspace.store, "blobs", None), "root", None)
            if not isinstance(root, Path) or operation["store_root"] != str(root):
                raise BoundaryError("local_model_export", "workspace_changed")
            model = workspace.store.get_manifest(identity)
            lineage = model_lineage(workspace.store, model,
                                    stage_id=operation["lineage"]["stage_id"])
            if lineage != operation["lineage"]:
                raise BoundaryError("local_model_export", "public_m2_lineage_mismatch")
            self._memory_owner(workspace.store).require_training_receipt(
                workspace.store, (lineage["dataset_id"],), lineage["training_operation_id"],
                operation["training_receipt_id"],
            )
            destination = _destination(self.config, identity, public_m2=True)
            if receipt(destination) != operation["verified_receipt"]:
                raise BoundaryError("local_model_export", "verified_export_required")
            if deadline is not None and monotonic() >= deadline:
                raise BoundaryError("local_model_registration", "registration_timeout")
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
        final = {**current, **updates}
        if current.get("schema") == SCHEMA_V4 and updates.get("status") == "completed":
            archive = self.config.state_dir / "model-export-receipts"
            if archive.exists() or archive.is_symlink():
                if not _ordinary(archive, directory=True):
                    raise BoundaryError("local_model_export", "operation_recovery_required")
            else:
                archive.mkdir(mode=0o700)
            path = archive / (current["model_id"] + ".json")
            if path.is_symlink() or path.exists() and not _ordinary(path, directory=False):
                raise BoundaryError("local_model_export", "operation_recovery_required")
            atomic_json(path, final)
        atomic_json(self._path(), final)

    def status_for_model(self, model_id: str) -> dict[str, Any]:
        identity = digest(model_id, "local_model_export.model_id")
        with self.lock:
            result = self.status()
            if result["operation"].get("model_id") != identity:
                archived = self._read(self.config.state_dir / "model-export-receipts" /
                                      (identity + ".json"))
                if archived.get("schema") == SCHEMA_V4:
                    root = getattr(getattr(self._workspace().store, "blobs", None), "root", None)
                    result = {**self._public(archived),
                              "availability": "ready" if str(root)
                              == archived["store_root"] else "workspace_changed"}
            return result

    def start(self, model_id: object) -> dict[str, Any]:
        from spireagent.workbench.developer_server import instance_lock

        identity = digest(model_id, "local_model_export.model_id")
        with self.lock:
            previous = self._read()
            if (previous.get("schema") == SCHEMA_V4
                    and previous.get("status") == "completed"
                    and previous.get("model_id") == identity):
                self.verified_public_m2_for_registration(identity)
                return self._public(previous)
            if (previous.get("schema") == SCHEMA_V3
                    and previous.get("status") == "completed"
                    and previous.get("model_id") == identity):
                self.verified_public_m0_for_registration(identity)
                return self._public(previous)
            if (previous["status"] == "pending" and self.thread is not None
                    and self.thread.is_alive()):
                if previous["model_id"] == identity:
                    return self._public(previous)
                raise BoundaryError("local_model_export", "export_in_progress")
            if previous.get("schema") in {SCHEMA_V3, SCHEMA_V4} and previous["status"] == "pending":
                raise BoundaryError("local_model_export", "previous_export_outcome_unknown")
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
            require_local_models("local_model_export")
            memory = model.parameters.value().get("schema") == "stpd/experimental-m2-model-v1"
            public_m0 = model.parameters.value().get("schema") == \
                "stpd/stage1a-light-action-m0-public-model-v1"
            public_m2 = model.parameters.value().get("schema") == "stpd/public-m2-model-v1"
            owner = self._memory_owner(store) if memory or public_m0 or public_m2 else None
            run_id = _memory_lineage(store, owner, model) if memory else None
            lineage = _public_m0_lineage(store, owner, model) if public_m0 else None
            if public_m2:
                from spireagent.storage.registry import SQLiteRegistry
                from stpd.policy.public_m2_export import model_lineage

                if not isinstance(workspace.registry, SQLiteRegistry):
                    raise BoundaryError("local_model_export", "indexed_epoch_stage_required")
                stages = [candidate for candidate in workspace.registry.dependents(
                    identity, kind="analysis") if candidate.parameters.value().get("schema")
                    == "stpd/public-m2-epoch-stage-v1"]
                if len(stages) != 1:
                    raise BoundaryError("local_model_export", "indexed_epoch_stage_required")
                lineage = model_lineage(store, model, stage_id=stages[0].artifact_id)
                assert owner is not None
                training_receipt = owner.verified_training_receipt(
                    store, (lineage["dataset_id"],), lineage["training_operation_id"],
                )
            if not memory and not public_m0 and not public_m2:
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
                                           public_m0=public_m0, public_m2=public_m2)
                operation: dict[str, Any] = {
                             "schema": SCHEMA_V4 if public_m2 else SCHEMA_V3 if public_m0 else
                             SCHEMA_V2 if memory else SCHEMA,
                             "status": "pending",
                             "operation_id": uuid.uuid4().hex, "model_id": identity,
                             "store_root": str(root)}
                if public_m2:
                    assert lineage is not None
                    operation.update(model_type="public_m2", profile="public-snapshot-m2-v1",
                                     lineage=lineage, training_receipt_id=training_receipt)
                elif memory:
                    assert run_id is not None
                    operation.update(model_type="memory", run_id=run_id)
                elif public_m0:
                    assert lineage is not None
                    operation.update(model_type="public_m0",
                                     profile="public-snapshot-m0-v1", **lineage)
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
                      on_started: Any, on_finished: Any) -> dict[str, Any]:
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
        on_finished(exit_code)
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

    def _public_m0_child(self, operation_id: str, training_operation_id: str,
                         model: Manifest, destination: Path, *,
                         on_started: Any, on_finished: Any) -> dict[str, Any]:
        if (self.config_path is None or self.config_path.is_symlink()
                or not self.config_path.is_file()):
            raise BoundaryError("local_model_export", "configured_local_workspace_required")
        root = getattr(getattr(self._workspace().store, "blobs", None), "root", None)
        if not isinstance(root, Path):
            raise BoundaryError("local_model_export", "unsupported_workspace_store")
        command = [sys.executable, "-m", "spireagent.research_cli", "--store", str(root),
                   "export-light-action-m0", "--project-config", str(self.config_path),
                   "--operation", training_operation_id, "--model", model.artifact_id,
                   "--destination", str(destination)]
        environment = dict(os.environ)
        for name in ("STPD_HUB_ADMIN_TOKEN", "PYTHONPATH", "PYTHONHOME"):
            environment.pop(name, None)
        log_path = self.config.state_dir / ("local-model-export-" + operation_id + ".log")
        exit_code, captured = private_child(command, log_path, environment,
                                             on_started=on_started)
        on_finished(exit_code)
        if exit_code:
            raise BoundaryError("local_model_export", "public_m0_export_process_failed")
        try:
            result = _child_json_record(captured)
            from stpd.policy.token_decision import PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA

            if (not isinstance(result, dict)
                    or result.get("model_id") != model.artifact_id
                    or result.get("schema") != PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA
                    or type(result.get("payload_bytes")) is not int
                    or result["payload_bytes"] <= 0
                    or not isinstance(result.get("verification"), dict)):
                raise ValueError
            return result
        except (ValueError, KeyError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_model_export", "public_m0_export_result_invalid") from error

    def _run(self, held: AbstractContextManager[None], operation_id: str, store: Any,
             model: Manifest, destination: Path, run_id: str | None) -> None:
        verified = False
        child_outcome_unknown = False

        def mark_started() -> None:
            nonlocal child_outcome_unknown
            child_outcome_unknown = True

        def mark_finished(_exit_code: int) -> None:
            nonlocal child_outcome_unknown
            child_outcome_unknown = False

        try:
            operation = self._read()
            if operation.get("model_type") == "public_m2":
                from stpd.policy.public_m2_export import model_lineage
                from stpd.policy.public_m2_export import receipt as m2_receipt

                lineage = model_lineage(store, model,
                                        stage_id=operation["lineage"]["stage_id"])
                self._memory_owner(store).require_training_receipt(
                    store, (lineage["dataset_id"],), lineage["training_operation_id"],
                    operation["training_receipt_id"],
                )
                root = getattr(getattr(store, "blobs", None), "root", None)
                if not isinstance(root, Path) or lineage != operation["lineage"]:
                    raise BoundaryError("local_model_export", "public_m2_lineage_mismatch")
                environment = dict(os.environ)
                for name in ("STPD_HUB_ADMIN_TOKEN", "PYTHONPATH", "PYTHONHOME"):
                    environment.pop(name, None)
                log_path = self.config.state_dir / ("local-model-export-" + operation_id + ".log")
                exit_code, captured = private_child(
                    [sys.executable, "-I", "-m", "stpd.policy.public_m2_cli", "export",
                     "--store", str(root), "--model", model.artifact_id,
                     "--stage", lineage["stage_id"],
                     "--destination", str(destination)], log_path, environment,
                    on_started=mark_started,
                )
                mark_finished(exit_code)
                if exit_code:
                    raise BoundaryError("local_model_export", "public_m2_export_process_failed")
                checked = m2_receipt(destination)
                if _child_json_record(captured) != checked:
                    raise BoundaryError("local_model_export", "public_m2_export_result_invalid")
                # Recheck authority after the child, before recording completion.
                self._memory_owner(store).require_training_receipt(
                    store, (lineage["dataset_id"],), lineage["training_operation_id"],
                    operation["training_receipt_id"],
                )
                verified = True
                with self.lock:
                    self._finish(operation_id, status="completed",
                                 payload_bytes=checked["payload_bytes"], verified_receipt=checked)
                return
            elif operation.get("model_type") == "public_m0":
                lineage = _public_m0_lineage(store, self._memory_owner(store), model)
                child = self._public_m0_child(operation_id, operation["training_operation_id"],
                                              model, destination,
                                              on_started=mark_started,
                                              on_finished=mark_finished)
                receipt = _verify_public_m0_export(model, destination, lineage)
                if child["payload_bytes"] != receipt["payload_bytes"]:
                    raise BoundaryError("local_model_export", "public_m0_export_result_invalid")
                verified = True
                with self.lock:
                    self._finish(operation_id, status="completed",
                                 payload_bytes=receipt["payload_bytes"],
                                 verified_receipt={"schema": PUBLIC_M0_RECEIPT_SCHEMA,
                                     "model_id": model.artifact_id,
                                     "export_schema": receipt["export_schema"],
                                     "model_schema": receipt["model_schema"],
                                     **lineage,
                                     "package_sha256": receipt["package_sha256"],
                                     "package_size": receipt["package_size"],
                                     "payload_sha256": receipt["payload_sha256"],
                                     "payload_sizes": receipt["payload_sizes"],
                                     "payload_bytes": receipt["payload_bytes"]})
                return
            elif run_id is None:
                from stpd.policy.token_decision import export_token_model

                if not destination.exists():
                    export_token_model(store, model.artifact_id, destination)
                count = _verify_export(store, model, destination)
            else:
                _memory_lineage(store, self._memory_owner(store), model)
                child = self._memory_child(operation_id, store, model, destination,
                                           run_id, on_started=mark_started,
                                           on_finished=mark_finished)
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
            if not verified and not child_outcome_unknown:
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
