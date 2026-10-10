"""Legacy fixed token/memory preparation, numerical launch and result validation.

The application retains its journal, OS lock and public v1/v2 contract.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import suppress
from typing import Any

from spireagent.json_boundary import BoundaryError, digest


class LegacyRecipeAdapter:
    def execute(self, service: Any, path: Any, identity: str, owner: Any,
                store: Any, mark_started: Any) -> None:
        # These compatibility hooks retain callers/tests that patch the original
        # boundary; no request data can choose a hook or imported module.
        from spireagent.storage.registry import SQLiteRegistry, sync_registry
        from spireagent.storage.run_reporter import ObjectStoreRunReporter
        from spireagent.workbench.local_training import (
            DEFAULT_RECIPE,
            MEMORY_RECIPES,
            ROOT,
            SCHEMA_V2,
            SCHEMA_V3,
            V2_MEMORY_RECIPES,
            _private_child,
            recipe_for_memory_config,
            source_identity,
        )
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

        operation = service._read(path, owner.identity)
        dataset_id = operation["dataset_id"]
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
            service._advance(path, identity, stage="tokenizing")
            service._advance(path, identity, stage="preparing_run")
            prepare_command = [sys.executable, "-m", "spireagent.research_cli",
                               "--store", str(owner.store_dir),
                               "prepare-workbench-memory", "--source", dataset_id,
                               "--operation", identity,
                               "--recipe", operation["recipe"]]
            prepare_log = owner.path.parent / ("local-training-" + identity + "-prepare.log")
            prepare_exit, captured = _private_child(prepare_command, prepare_log,
                                                    environment,
                                                    on_started=mark_started)
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
            service._advance(path, identity, stage="training", input_id=input_id,
                          run_id=run_id)
            command_name = "run-memory"
        elif human:
            service._advance(path, identity, stage="public_view")
            view = publish_human_text_bc_view(store, dataset_id, producer)
        else:
            assert spec is not None
            service._advance(path, identity, stage="allocating")
            allocation = publish_allocation(store, dataset_id, spec, producer)
            service._advance(path, identity, stage="public_view",
                          allocation_id=allocation.artifact_id)
            view = publish_public_bc_view(store, allocation.artifact_id, producer)
        if not memory:
            service._advance(path, identity, stage="tokenizing", view_id=view.artifact_id)
            inputs_manifest = publish_token_inputs(store, view.artifact_id, "s", producer,
                                                   max_tokens=16384)
            service._advance(path, identity, stage="preparing_run",
                          input_id=inputs_manifest.artifact_id)
            import torch

            torch.set_num_threads(2)
            inputs = load_token_inputs(store, inputs_manifest.artifact_id)
            config = TokenConfig(recipe=DEFAULT_RECIPE, width=48, layers=1,
                                 heads=2, feedforward=96, dropout=0.0, steps=3,
                                 device="cpu", max_tokens=16384)
            run = prepare_token_run(store, inputs, config, producer,
                                    replicate="local-" + identity)
            service._advance(path, identity, stage="training", run_id=run.artifact_id)
            command_name = "run-tokens"
        command = [sys.executable, "-m", "spireagent.research_cli", "--store",
                   str(owner.store_dir), command_name, "--run", run.artifact_id]
        log_path = owner.path.parent / ("local-training-" + identity + ".log")
        exit_code, _ = _private_child(command, log_path, environment,
                                     on_started=mark_started)
        service._advance(path, identity, stage="verifying_result", _exit_code=exit_code)
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
        _, _, registry_path = service._selected()
        sync_registry(store, SQLiteRegistry(registry_path))
        completion = {"checkpoint_id": model.parent("checkpoint"),
                      "result_id": result.artifact_id, "model_id": model_id}
        if not memory:
            completion["evaluation_id"] = result.parent("offline_evaluation")
            if operation["schema"] in {SCHEMA_V2, SCHEMA_V3}:
                completion["evaluation_status"] = "completed"
        if operation["schema"] == SCHEMA_V3:
            service._finish_attempt(path, owner, identity,
                                    expected_attempt_id=operation["attempt_id"], status="completed",
                                    stage="completed", **completion)
        else:
            service._advance(path, identity, stage="completed", status="completed", **completion)
