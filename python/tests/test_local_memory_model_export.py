"""Synthetic claimed Human M2 export through the existing Workbench owner."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import torch
from m2_export_fixture import clone_completed_m2

from spireagent.json_boundary import BoundaryError
from spireagent.storage.blobs import StoreError
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import atomic_json
from spireagent.workbench.inplace_curation import configured_owner
from spireagent.workbench.local_curation import OWNER_NAME
from spireagent.workbench.local_model_export import (
    EXPORT_ROOT,
    OPERATION_FILE,
    SCHEMA_V2,
    LocalModelExport,
)
from spireagent.workbench.memory_recipe import M2_K1_RECIPE, RESET_K1_RECIPE
from spireagent.workbench.memory_training import prepare_workbench_memory
from spireagent.workbench.research_process import private_child
from stpd.fullrun.text_menu_human_import import load_verified_human_text_bundle
from stpd.policy.memory_export import validate_memory_package
from stpd.workers.memory_run import execute_memory_run

pytest_plugins = ("m2_export_fixture",)


def _fixture(tmp_path: Path, completed_m2_recipes, *, recipe: str = M2_K1_RECIPE):
    return clone_completed_m2(completed_m2_recipes.get(recipe), tmp_path)


def _settle(service: LocalModelExport) -> dict:
    assert service.thread is not None
    service.thread.join(timeout=60)
    assert not service.thread.is_alive()
    return service.status()["operation"]


def test_completed_m2_exports_private_package_after_later_training(
        tmp_path: Path, monkeypatch, completed_m2_recipes) -> None:
    config, owner, store, dataset, sources, runs, run_id, model_id = _fixture(
        tmp_path, completed_m2_recipes)
    # A later completed operation replaces the UI's latest record. Immutable
    # run and ledger use remain sufficient for the earlier model.
    later = "b" * 32
    for source in sources:
        owner.ledger.use_source(source, "training", later)
    owner.ledger.use(runs, "training", later)
    second_run, _ = prepare_workbench_memory(
        store, dataset, store.get_manifest(dataset).producer, later)
    second = execute_memory_run(
        store, ObjectStoreRunReporter(store, store.blobs), second_run,
        store.get_manifest(dataset).producer)
    assert second.state == "completed"
    atomic_json(owner.path.parent / "local-training-operation.json", {
        "schema": "stpd/local-training-operation-v2", "status": "completed",
        "stage": "completed", "operation_id": later, "dataset_id": dataset,
        "recipe": "stage1a.dsimple.m2.k1.experimental.v1", "result_type": "train_only",
        "evaluation_status": "not_run", "run_id": second_run,
        "result_id": second.result_id,
        "model_id": store.get_manifest(second.result_id).parent("model"),
        "input_id": store.get_manifest(second_run).parent("training_input"),
        "checkpoint_id": second.checkpoint_id, "_owner": list(owner.identity),
    })
    service = LocalModelExport(config)
    assert service.status()["operation"] == {"status": "idle"}
    started = service.start(model_id)["operation"]
    assert started["status"] == "pending" and started["model_type"] == "memory"
    assert started["model_id"] == model_id
    done = _settle(service)
    assert done["status"] == "completed", done
    assert service.status()["schema"] == SCHEMA_V2
    directory = config.state_dir / EXPORT_ROOT / model_id
    package, weights, tokenizer, _ = validate_memory_package(directory)
    assert package["ids"]["run"] == run_id and package["ids"]["model"] == model_id
    assert package["evaluation_status"] == "not_run"
    assert done["payload_bytes"] == len(weights) + len(tokenizer)
    assert set(path.name for path in directory.iterdir()) == {
        "model.json", "weights.tensor-tree", "tokenizer.json"}
    assert "store_root" not in service.status()["operation"]
    with pytest.raises(BoundaryError, match="memory_registration_not_ready"):
        service.verified_for_registration(model_id)
    assert _settle_after_recheck(service, model_id)["status"] == "completed"


def _settle_after_recheck(service: LocalModelExport, model_id: str) -> dict:
    service.start(model_id)
    return _settle(service)


def test_missing_use_and_index_block_before_export_or_child(
        tmp_path: Path, monkeypatch, completed_m2_recipes) -> None:
    config, owner, store, dataset, sources, runs, _run_id, model_id = _fixture(
        tmp_path, completed_m2_recipes)
    service = LocalModelExport(config)
    with owner.transaction() as db:
        db.execute("DELETE FROM curation_source_uses WHERE source=?", (sources[0],))
    with patch("spireagent.workbench.local_model_export.private_child") as child:
        with pytest.raises(BoundaryError, match="training_source_use_missing"):
            service.start(model_id)
        child.assert_not_called()
    assert not (config.state_dir / OPERATION_FILE).exists()
    owner.ledger.use_source(sources[0], "training", "a" * 32)
    with owner.transaction() as db:
        db.execute("DELETE FROM curation_exact_source_index WHERE source=?", (sources[0],))
    with pytest.raises(BoundaryError, match="source_index_incomplete"):
        service.start(model_id)
    assert not (config.state_dir / EXPORT_ROOT).exists()
    assert dataset and runs and store


def test_spawn_failure_is_failed_but_started_child_outcome_is_unknown(
        tmp_path: Path, monkeypatch, completed_m2_recipes) -> None:
    config, _owner, _store, _dataset, _sources, _runs, _run_id, model_id = _fixture(
        tmp_path, completed_m2_recipes)
    service = LocalModelExport(config)
    with patch("spireagent.workbench.local_model_export.private_child",
               side_effect=OSError("no child")):
        service.start(model_id)
        assert _settle(service)["status"] == "failed"

    def started_failure(_command, _log_path, _environment, *, on_started):
        on_started()
        return 2, b""

    with patch("spireagent.workbench.local_model_export.private_child",
               side_effect=started_failure):
        service.start(model_id)
        assert _settle(service)["status"] == "interrupted"
    durable = json.loads((config.state_dir / OPERATION_FILE).read_bytes())
    assert durable["status"] == "pending" and durable["model_type"] == "memory"
    assert not (config.state_dir / EXPORT_ROOT / model_id).exists()


def test_registration_uses_completed_child_receipt_without_web_replay(
        tmp_path: Path, monkeypatch, completed_m2_recipes) -> None:
    config, _, _, _, _, _, _, model_id = _fixture(tmp_path, completed_m2_recipes)
    service = LocalModelExport(config)
    service.start(model_id)
    assert _settle(service)["status"] == "completed"
    export_logs = list(config.state_dir.glob("local-model-export-*.log"))
    assert len(export_logs) == 1
    original_log = export_logs[0].read_bytes()
    receipt = service._read()["verified_receipt"]
    assert receipt["model_id"] == model_id
    with patch.object(service, "_memory_child", side_effect=AssertionError("replayed")):
        previous = torch.get_num_threads()
        try:
            torch.set_num_threads(3)  # Deliberately unlike the run's pinned two threads.
            assert service.verified_memory_for_registration(model_id) == (
                config.state_dir / EXPORT_ROOT / model_id)
            assert torch.get_num_threads() == 3
        finally:
            torch.set_num_threads(previous)
    assert export_logs[0].read_bytes() == original_log
    assert not list(config.state_dir.glob("local-model-registration-verify-*.log"))


def test_reset_k1_exports_and_retains_its_verified_recipe_identity(
        tmp_path: Path, monkeypatch, completed_m2_recipes) -> None:
    config, _owner, store, _dataset, _sources, _runs, run_id, model_id = _fixture(
        tmp_path, completed_m2_recipes, recipe=RESET_K1_RECIPE)
    model = store.get_manifest(model_id)
    assert model.parameters.value()["config"]["reset_each_step"] is True
    service = LocalModelExport(config)
    service.start(model_id)
    done = _settle(service)
    assert done["status"] == "completed", done
    destination = config.state_dir / EXPORT_ROOT / model_id
    package, _, _, _ = validate_memory_package(destination)
    assert package["ids"]["run"] == run_id
    assert package["config"]["reset_each_step"] is True
    assert service.verified_memory_for_registration(model_id) == destination
    assert service.verified_memory_recipe_for_registration(model_id) == RESET_K1_RECIPE


def test_old_completed_m2_requires_explicit_reverify_for_receipt(
        tmp_path: Path, monkeypatch, completed_m2_recipes) -> None:
    config, _, _, _, _, _, _, model_id = _fixture(tmp_path, completed_m2_recipes)
    service = LocalModelExport(config)
    service.start(model_id)
    assert _settle(service)["status"] == "completed"
    prior = service._read()
    destination = config.state_dir / EXPORT_ROOT / model_id
    original = {path.name: path.read_bytes() for path in destination.iterdir()}
    invalid_pending = {**prior, "status": "pending"}
    atomic_json(config.state_dir / OPERATION_FILE, invalid_pending)
    with pytest.raises(BoundaryError, match="operation_recovery_required"):
        service.status()
    atomic_json(config.state_dir / OPERATION_FILE, prior)
    del prior["verified_receipt"]
    atomic_json(config.state_dir / OPERATION_FILE, prior)
    with pytest.raises(BoundaryError, match="verified_export_receipt_required"):
        service.verified_memory_for_registration(model_id)
    assert service._read() == prior
    service.start(model_id)  # Explicit existing-package verify; no implicit receipt mint.
    assert _settle(service)["status"] == "completed"
    assert service._read()["operation_id"] != prior["operation_id"]
    assert service._read()["verified_receipt"]["model_id"] == model_id
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == original
    assert len(list(config.state_dir.glob("local-model-export-*.log"))) == 2


def test_private_verification_child_timeout_reaps_without_reusing_log(tmp_path: Path) -> None:
    log = tmp_path / "verification.log"
    with pytest.raises(BoundaryError, match="private_child_timeout"):
        private_child([sys.executable, "-c", "import time; time.sleep(10)"],
                      log, dict(os.environ), timeout_seconds=0.1)
    assert log.is_file()
    with pytest.raises(FileExistsError):
        private_child([sys.executable, "-c", "pass"], log,
                      dict(os.environ), timeout_seconds=0.1)


def test_completed_recipe_clones_preserve_lineage_and_isolate_payloads_and_ledger(
        tmp_path: Path, completed_m2_recipes) -> None:
    source = completed_m2_recipes.get(M2_K1_RECIPE)
    assert source is completed_m2_recipes.get(M2_K1_RECIPE)
    prefixes = ("objects/", "payload-indexes/", "manifests/", "run-events/", "run-completions/")
    original = {key: source.store.blobs.get(key)
                for prefix in prefixes for key in source.store.blobs.keys(prefix)}
    first = clone_completed_m2(source, tmp_path / "first")
    second = clone_completed_m2(source, tmp_path / "second")
    _, first_owner, first_store, dataset, sources, runs, run_id, _ = first
    _, second_owner, second_store, _, _, _, _, _ = second
    assert first_owner.identity != second_owner.identity
    for clone in (first_store, second_store):
        assert clone.manifest_ids() == source.store.manifest_ids()
        for key, content in original.items():
            assert clone.blobs.get(key) == content
    for owner in (first_owner, second_owner):
        owner.ledger.require_training_use(dataset, sources, runs, "a" * 32)
    with first_owner.transaction() as db:
        db.execute("DELETE FROM curation_source_uses WHERE source=?", (sources[0],))
    with pytest.raises(BoundaryError, match="training_source_use_missing"):
        first_owner.ledger.require_training_use(dataset, sources, runs, "a" * 32)
    second_owner.ledger.require_training_use(dataset, sources, runs, "a" * 32)
    archive = first_store.get_manifest(sources[0]).payload("archive")
    index = json.loads(first_store.blobs.get(f"payload-indexes/v1/{archive.sha256}.json"))
    chunk_key = f"objects/sha256/{index['chunks'][0]['sha256']}"
    damaged = first_store.blobs.root / chunk_key
    damaged.write_bytes(damaged.read_bytes() + b"corrupt")
    with pytest.raises((BoundaryError, StoreError), match="integrity"):
        load_verified_human_text_bundle(first_store, sources[0])
    for clone in (source.store, second_store):
        load_verified_human_text_bundle(clone, sources[0])
        assert clone.blobs.get(chunk_key) == original[chunk_key]
        assert ObjectStoreRunReporter(clone, clone.blobs).completed(run_id) is not None
    assert {key: source.store.blobs.get(key) for key in original} == original
    with pytest.raises(StoreError, match="read_only_store"):
        source.store.blobs.put_if_absent("objects/forbidden", b"mutation")


def test_cloned_owner_still_rejects_foreign_ledger_path(
        tmp_path: Path, completed_m2_recipes) -> None:
    source = completed_m2_recipes.get(M2_K1_RECIPE)
    config, owner, _, _, _, _, _, _ = clone_completed_m2(source, tmp_path)
    marker_path = owner.store_dir / OWNER_NAME
    marker = json.loads(marker_path.read_bytes())
    marker["ledger_path"] = str(source.config.research_workspace.store_dir / ".curation.sqlite")
    atomic_json(marker_path, marker)
    with pytest.raises(BoundaryError, match="store_identity_mismatch"):
        configured_owner(config)
