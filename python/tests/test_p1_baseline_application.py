"""Comparison recipes through the normal private child, registry and Agent consumer."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from legacy_p1_fixture import reserve_p1_snapshot
from test_p1_application import workspace

from spireagent.workbench.developer import ROOT
from spireagent.workbench.local_model_export import LocalModelExport
from spireagent.workbench.local_model_registration import LocalModelRegistration
from spireagent.workbench.local_models import LocalModelService
from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.recipe_contracts import TrainingRequest
from stpd.p1.baseline_agent import installation
from stpd.p1.baseline_spec import RECIPE, BaselineConfig


def test_baseline_preflight_rejects_missing_tokenizers_before_owned_operation(
    tmp_path, monkeypatch
):
    from spireagent.json_boundary import BoundaryError
    from spireagent.workbench import local_model_dependencies
    from spireagent.workbench.trusted_recipes import describe_recipe

    config, store, owner, snapshot = workspace(tmp_path)
    reserve_p1_snapshot(owner, store, snapshot.artifact_id)
    service = LocalTrainingService(config)
    before = store.manifest_ids()
    monkeypatch.setattr(local_model_dependencies, "find_spec",
                        lambda name: None if name == "tokenizers" else object())
    assert describe_recipe(RECIPE)["dependencies_available"] is False
    request = TrainingRequest(uuid.uuid4().hex, RECIPE, snapshot.artifact_id,
                              BaselineConfig().to_dict())
    with pytest.raises(BoundaryError, match="local_models_extra_required"):
        service.start(request)
    assert service.status()["operation"]["status"] == "idle"
    assert service._thread is None
    assert store.manifest_ids() == before
    monkeypatch.setattr(local_model_dependencies, "find_spec",
                        lambda name: None if name == "safetensors" else object())
    assert describe_recipe(RECIPE)["dependencies_available"] is True


@pytest.mark.parametrize("algorithm", ["random", "frequency", "linear"])
def test_normal_private_child_original_model_registration_and_composition(
    tmp_path, monkeypatch, algorithm
):
    config, store, owner, snapshot = workspace(tmp_path)
    reserve_p1_snapshot(owner, store, snapshot.artifact_id)
    trainer = LocalTrainingService(config)
    request = TrainingRequest(uuid.uuid4().hex, RECIPE, snapshot.artifact_id,
                              BaselineConfig(algorithm=algorithm, max_epochs=2).to_dict(),
                              limits={"wall_seconds": 120, "scratch_bytes": 512 * 1024**2})
    admitted = trainer.start(request)["operation"]
    trainer._thread.join(timeout=60)
    assert not trainer._thread.is_alive()
    operation = trainer.status(admitted["operation_id"])["operation"]
    assert operation["status"] == "completed", operation
    assert operation["worker_state"] == "terminal"
    assert operation["progress"]["completed"] == (2 if algorithm == "linear" else 0)
    model_id = operation["model_id"]
    model = store.get_manifest(model_id)
    assert model.parameters.value()["algorithm"] == algorithm
    models = LocalModelService(config)
    models.root = ROOT
    models.prepare(model_id, source="store")
    models.thread.join(timeout=10)
    assert models.status()["last_download"]["parents_downloaded"] is False
    models.close()
    # The next process has only the own-byte cache, and uses the normal registration.
    models = LocalModelService(config)
    models.root = ROOT
    registration = LocalModelRegistration(config, LocalModelExport(config), models)
    monkeypatch.setattr(registration, "_native_runtime",
                        lambda **_: (tmp_path / "modules", Path(".")))

    def envelope(_runtime, _options, _artifact, adapter, selection, _deadline):
        return {"schema": "sts2.policy-runtime/agent-manifest-1", "manifest_id": selection,
                "input": {"profile": "p1-global-r1",
                          "global": {"core_sha256": "a" * 64, "revision": "r1.5"}},
                "environment": {"kind": "native"}, "adapter": adapter}

    monkeypatch.setattr(registration, "_global_environment_manifest", envelope)
    monkeypatch.setattr(models, "_public_manifest_contract", lambda *_: None)
    registered = registration.register(model_id)
    entry = models.selection(registered["selection_id"])
    assert entry["adapter"] == "stpd-p1-baseline-agent"
    bound, manifest, loaded, _ = installation(models.entry_path(entry, "config"),
                                              models.entry_path(entry, "manifest"),
                                              models.private_root)
    assert bound["model_artifact_id"] == model_id
    assert loaded.algorithm == algorithm
    assert registration.status(model_id)["selection_id"] == registered["selection_id"]
    evidence = SimpleNamespace(status="pass", value=SimpleNamespace(agent_manifest=manifest))
    raw, reason = models._original_native_composition(evidence)
    assert reason is None and json.loads(raw)["algorithm"] == algorithm
    from spireagent.workbench.agent_composition import describe

    composition = describe(entry, manifest, bound, json.loads(raw))
    assert composition["semantic_definition"]["state"]["kind"] == "ACK_committed_session_rng"


def test_normal_cli_start_status_and_model_registration_status(tmp_path, monkeypatch, capsys):
    from test_remote_training_http import client

    from spireagent.workbench.developer_cli import main as project_main
    from spireagent.workbench.training_cli import main as training_main

    with client(tmp_path) as (app, _call, _provider, _store, _owner, snapshot, config_path):
        request = TrainingRequest(uuid.uuid4().hex, RECIPE, snapshot.artifact_id,
                                  BaselineConfig(algorithm="frequency").to_dict())
        path = tmp_path / "request.json"
        path.write_text(json.dumps(request.to_dict()))
        assert training_main(["start", "--config", str(config_path),
                              "--input", str(path)]) == 0
        started = json.loads(capsys.readouterr().out)["operation"]
        deadline = time.monotonic() + 30
        while True:
            assert training_main(["status", "--config", str(config_path),
                                  "--operation-id", started["operation_id"]]) == 0
            operation = json.loads(capsys.readouterr().out)["operation"]
            if operation["status"] != "pending":
                break
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert operation["status"] == "completed", operation
        model_id = operation["model_id"]
        registration = app.local_model_registration
        monkeypatch.setattr(registration, "_native_runtime",
                            lambda **_: (tmp_path / "modules", Path(".")))

        def envelope(_runtime, _options, _artifact, adapter, selection, _deadline):
            return {"schema": "sts2.policy-runtime/agent-manifest-1", "manifest_id": selection,
                    "input": {"profile": "p1-global-r1",
                              "global": {"core_sha256": "a" * 64, "revision": "r1.5"}},
                    "environment": {"kind": "native"}, "adapter": adapter}

        monkeypatch.setattr(registration, "_global_environment_manifest", envelope)
        monkeypatch.setattr(app.models, "_public_manifest_contract", lambda *_: None)
        assert project_main(["model", "--config", str(config_path), "--action", "register",
                             "--artifact", model_id]) == 0
        registered = json.loads(capsys.readouterr().out)
        assert registered["status"] == "registered"
        assert project_main(["model", "--config", str(config_path),
                             "--action", "registration-status", "--artifact", model_id]) == 0
        assert json.loads(capsys.readouterr().out)["selection_id"] == registered["selection_id"]
