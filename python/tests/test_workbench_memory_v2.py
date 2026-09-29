"""Synthetic Managed v2 source through the existing Workbench M2 worker graph."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_local_managed_source import _setup, _uses
from test_local_training import _settle

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.local_model_export import LocalModelExport
from spireagent.workbench.local_model_registration import LocalModelRegistration
from spireagent.workbench.local_models import LocalModelService
from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.memory_recipe import (
    M2_K1_RECIPE, V2_M2_K1_RECIPE, V2_RESET_K1_RECIPE,
)
from stpd.policy.memory_export import validate_memory_package


def _export_done(service: LocalModelExport) -> dict:
    assert service.thread is not None
    service.thread.join(timeout=90)
    assert not service.thread.is_alive()
    return service.status()["operation"]


def test_managed_v2_m2_reset_train_export_and_registration_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    importer, _, report_id, owner = _setup(tmp_path, monkeypatch)
    admitted = importer.import_report(report_id, "training")
    source_id = admitted["artifact_id"]
    config = importer.config
    training = LocalTrainingService(config)

    # The identical M2 config cannot turn an actor-unverified v2 source into v1 Human.
    training.start(source_id, recipe=M2_K1_RECIPE)
    refused = _settle(training)
    assert refused["status"] == "failed"
    assert refused["error_code"] == "managed_v2_recipe_required"
    assert _uses(owner) == (0, 0)

    previous = None
    for recipe in (V2_M2_K1_RECIPE, V2_RESET_K1_RECIPE):
        started = training.start(source_id, recipe=recipe,
                                 after_completed_operation_id=previous)["operation"]
        assert started["status"] == "pending"
        completed = _settle(training)
        assert completed["status"] == "completed", completed
        assert completed["recipe"] == recipe
        assert completed["result_type"] == "train_only"
        assert completed["evaluation_status"] == "not_run"
        assert "evaluation_id" not in completed
        previous = completed["operation_id"]

        exporter = LocalModelExport(config)
        exporter.start(completed["model_id"])
        exported = _export_done(exporter)
        assert exported["status"] == "completed", exported
        package_path = config.state_dir / "model-exports" / completed["model_id"]
        package, _, _, _ = validate_memory_package(
            package_path, input_profile="text-menu-v2")
        assert package["ids"]["source"] == source_id
        assert package["ids"]["training_input"] == completed["input_id"]
        assert package["renderer"]["input_schema"] == (
            "sts2.player-environment/text-menu-snapshot-2")
        assert exporter.verified_memory_for_registration(completed["model_id"]) == package_path
        assert exporter.verified_memory_recipe_for_registration(completed["model_id"]) == recipe
        registration = LocalModelRegistration(config, exporter, LocalModelService(config))
        status = registration.status(completed["model_id"])
        assert status["runtime_profile"] == "text-menu-m2-v2"
        assert status["status"] == "not_registered"
        with pytest.raises(BoundaryError, match="text_runtime_profile_required"):
            registration.register(completed["model_id"])
        assert not (registration.models.private_root / "token-policies-v1.json").exists()
    assert _uses(owner) == (2, 1)


def test_test_purpose_cannot_start_v2_training(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    importer, _, report_id, owner = _setup(tmp_path, monkeypatch)
    source_id = importer.import_report(report_id, "test")["artifact_id"]
    training = LocalTrainingService(importer.config)
    training.start(source_id, recipe=V2_M2_K1_RECIPE)
    refused = _settle(training)
    assert refused["status"] == "failed"
    assert refused["error_code"] == "training_claim_mismatch"
    assert _uses(owner) == (0, 0)
