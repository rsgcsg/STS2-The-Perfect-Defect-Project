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
import uuid
from contextlib import AbstractContextManager, suppress
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, digest
from spireagent.source import source_identity
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ROOT, ProjectConfig, atomic_json
from spireagent.workbench.developer_server import instance_lock
from spireagent.workbench.local_curation import LocalCurationOwner
from spireagent.workbench.local_dataset import LocalDatasetService

SCHEMA = "stpd/local-training-operation-v1"
OPERATION_FILE = "local-training-operation.json"
LOCK_FILE = ".local-training.lock"
IDS = ("allocation_id", "view_id", "input_id", "run_id", "checkpoint_id",
       "result_id", "model_id", "evaluation_id")
STAGES = frozenset({"reserving", "allocating", "public_view", "tokenizing",
                    "preparing_run", "training", "verifying_result", "completed"})


class LocalTrainingService:
    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self._selection = LocalDatasetService(config)
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def _selected(self):
        return self._selection._selected()

    @staticmethod
    def _paths(owner: LocalCurationOwner) -> tuple[Path, Path]:
        return owner.path.parent / OPERATION_FILE, owner.path.parent / LOCK_FILE

    @staticmethod
    def _read(path: Path, identity: tuple[str, ...]) -> dict[str, Any]:
        if not path.exists() and not path.is_symlink():
            return {"status": "idle"}
        if path.is_symlink() or not path.is_file():
            raise BoundaryError("local_training", "operation_recovery_required")
        try:
            value = json.loads(path.read_bytes())
            if (not isinstance(value, dict) or value.get("schema") != SCHEMA
                    or value.get("status") not in
                    {"pending", "completed", "failed", "interrupted_unknown"}
                    or value.get("stage") not in STAGES
                    or value.get("_owner") != list(identity)):
                raise ValueError
            digest(value["operation_id"], "local_training.operation", length=32)
            digest(value["dataset_id"], "local_training.dataset")
            for key in IDS:
                if key in value:
                    digest(value[key], "local_training." + key)
            if value["status"] == "completed" and not all(
                value.get(key) for key in ("run_id", "result_id", "model_id", "evaluation_id")
            ):
                raise ValueError
            if value["status"] in {"failed", "interrupted_unknown"} and not isinstance(
                value.get("error_code"), str
            ):
                raise ValueError
            return value
        except (OSError, ValueError, KeyError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_training", "operation_recovery_required") from error

    @staticmethod
    def _public(value: dict[str, Any]) -> dict[str, Any]:
        keys = {"status", "stage", "operation_id", "dataset_id", "error_code", *IDS}
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
        except BoundaryError as error:
            return {"schema": SCHEMA, "availability": "recovery_required",
                    "reason": error.code, "operation": {"status": "idle"}}
        if operation["status"] == "pending":
            # A lock held by this or another profile proves the supervising parent
            # is alive. A missing lock means an unknown child outcome, not failure.
            try:
                if not lock_path.is_file() or lock_path.is_symlink():
                    raise BoundaryError("local_training", "operation_lock_missing")
                with instance_lock(lock_path):
                    operation = {**operation, "status": "interrupted_unknown",
                                 "error_code": "previous_training_outcome_unknown"}
            except BoundaryError as error:
                if error.code != "already_running":
                    operation = {**operation, "status": "interrupted_unknown",
                                 "error_code": "previous_training_outcome_unknown"}
        return {"schema": SCHEMA, "availability": "ready",
                "operation": self._public(operation)}

    @staticmethod
    def _advance(path: Path, identity: str, **updates: Any) -> None:
        current = json.loads(path.read_bytes())
        if current.get("operation_id") != identity or current.get("status") != "pending":
            raise BoundaryError("local_training", "operation_superseded")
        atomic_json(path, {**current, **updates})

    def start(self, dataset_id: object) -> dict[str, Any]:
        dataset_id = digest(dataset_id, "local_training.dataset_id")
        owner, store, _ = self._selected()
        path, lock_path = self._paths(owner)
        if lock_path.is_symlink():
            raise BoundaryError("local_training", "operation_recovery_required")
        held: AbstractContextManager[None] = instance_lock(lock_path)
        try:
            held.__enter__()
        except BoundaryError as error:
            if error.code == "already_running":
                operation = self._read(path, owner.identity)
                if operation.get("dataset_id") == dataset_id and operation["status"] == "pending":
                    return self.status()
                raise BoundaryError("local_training", "operation_in_progress") from error
            raise
        try:
            previous = self._read(path, owner.identity)
            if previous["status"] in {"pending", "interrupted_unknown"} or (
                previous["status"] == "failed" and previous.get("run_id")
            ):
                raise BoundaryError("local_training", "previous_training_outcome_unknown")
            if previous["status"] == "completed" and previous["dataset_id"] == dataset_id:
                return self.status()
            identity = uuid.uuid4().hex
            operation = {"schema": SCHEMA, "status": "pending", "stage": "reserving",
                         "operation_id": identity, "dataset_id": dataset_id,
                         "_owner": list(owner.identity)}
            atomic_json(path, operation)
            thread = threading.Thread(target=self._run, args=(held, path, identity, owner, store),
                                      name="local-small-b-training", daemon=True)
            with self._lock:
                self._thread = thread
            thread.start()
            held = None  # type: ignore[assignment]
            return {"schema": SCHEMA, "availability": "ready",
                    "operation": self._public(operation)}
        finally:
            if held is not None:
                held.__exit__(None, None, None)

    def _run(self, held: AbstractContextManager[None], path: Path, identity: str,
             owner: LocalCurationOwner, store: ManifestArtifactStore) -> None:
        run_started = False
        try:
            from spireagent.storage.registry import SQLiteRegistry, sync_registry
            from spireagent.storage.run_reporter import ObjectStoreRunReporter
            from stpd.fullrun.curated_dataset import SCHEMA as DATASET_SCHEMA
            from stpd.fullrun.curated_dataset import load_selection
            from stpd.fullrun.decision_spool import SpoolSelection
            from stpd.fullrun.decision_training import AllocationSpec, allocate, publish_allocation
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

            dataset_id = self._read(path, owner.identity)["dataset_id"]
            producer = source_identity(ROOT)
            manifest = store.get_manifest(dataset_id)
            info = manifest.parameters.value()
            human = info.get("schema") == HUMAN_SOURCE_SCHEMA
            if human:
                source_manifest, rows = load_human_text_source(store, dataset_id)
                if source_manifest != manifest:
                    raise BoundaryError("local_training", "human_source_identity_mismatch")
                # This is the actual engineering split gate; session count alone
                # cannot establish independent groups after duplicate collapse.
                project_human_inputs(rows)
                sources = {parent.artifact_id for parent in manifest.parents}
                runs: set[str] = set()
                for source_id in sources:
                    _, bundle, _ = load_verified_human_text_bundle(store, source_id)
                    runs.update(bundle.session_id + "/" + run for run in bundle.run_ids)
                spec = None
            else:
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
            if human:
                self._advance(path, identity, stage="public_view")
                view = publish_human_text_bc_view(store, dataset_id, producer)
            else:
                assert spec is not None
                self._advance(path, identity, stage="allocating")
                allocation = publish_allocation(store, dataset_id, spec, producer)
                self._advance(path, identity, stage="public_view",
                              allocation_id=allocation.artifact_id)
                view = publish_public_bc_view(store, allocation.artifact_id, producer)
            self._advance(path, identity, stage="tokenizing", view_id=view.artifact_id)
            inputs_manifest = publish_token_inputs(store, view.artifact_id, "s", producer,
                                                   max_tokens=16384)
            self._advance(path, identity, stage="preparing_run",
                          input_id=inputs_manifest.artifact_id)
            import torch

            torch.set_num_threads(2)
            inputs = load_token_inputs(store, inputs_manifest.artifact_id)
            config = TokenConfig.text_menu_small_b(width=48, layers=1, heads=2,
                                                   feedforward=96, steps=3,
                                                   device="cpu", max_tokens=16384)
            run = prepare_token_run(store, inputs, config, producer,
                                    replicate="local-" + identity)
            self._advance(path, identity, stage="training", run_id=run.artifact_id)
            environment = dict(os.environ)
            for name in ("STPD_HUB_ADMIN_TOKEN", "PYTHONPATH", "PYTHONHOME"):
                environment.pop(name, None)
            command = [sys.executable, "-m", "spireagent.research_cli", "--store",
                       str(owner.store_dir), "run-tokens", "--run", run.artifact_id]
            log_path = owner.path.parent / ("local-training-" + identity + ".log")
            log_fd = os.open(log_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(log_fd, "wb") as log:
                with subprocess.Popen(command, cwd=ROOT, env=environment,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT) as child:
                    run_started = True
                    assert child.stdout is not None
                    remaining = 128 * 1024
                    while chunk := child.stdout.read(4096):
                        if remaining:
                            log.write(chunk[:remaining])
                            remaining -= min(remaining, len(chunk))
                    exit_code = child.wait()
                log.flush()
                os.fsync(log.fileno())
            self._advance(path, identity, stage="verifying_result", _exit_code=exit_code)
            if exit_code:
                raise BoundaryError("local_training", "training_process_failed")
            reporter = ObjectStoreRunReporter(store, store.blobs)
            result = reporter.completed(run.artifact_id)
            if result is None:
                raise BoundaryError("local_training", "completion_marker_missing")
            _verify_completed(store, result, run)
            model_id = result.parent("model")
            evaluation_id = result.parent("offline_evaluation")
            model = store.get_manifest(model_id)
            _, _, registry_path = self._selected()
            sync_registry(store, SQLiteRegistry(registry_path))
            self._advance(path, identity, stage="completed", status="completed",
                          checkpoint_id=model.parent("checkpoint"), result_id=result.artifact_id,
                          model_id=model_id, evaluation_id=evaluation_id)
        except (BoundaryError, OSError, ValueError, KeyError, TypeError,
                subprocess.SubprocessError) as error:
            code = (error.code if isinstance(error, BoundaryError)
                    else "training_storage_or_process_error")
            # The last durable pending operation remains blocking if terminal
            # persistence fails, so its run identity is never discarded.
            with suppress(OSError, ValueError, BoundaryError):
                self._advance(path, identity,
                              status="interrupted_unknown" if run_started else "failed",
                              error_code=code)
        finally:
            held.__exit__(None, None, None)
