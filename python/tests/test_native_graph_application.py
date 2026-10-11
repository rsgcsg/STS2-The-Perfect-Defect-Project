"""Graph packages reach the existing application without a second control selector."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_native_agent_application import native_capabilities, settled
from test_native_structured_training import data, origin
from test_native_structured_training import two_threads as two_threads
from test_structured_resume import Authority

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_model_export import LocalModelExport
from spireagent.workbench.local_model_registration import LocalModelRegistration
from spireagent.workbench.local_models import LocalModelService
from spireagent.workbench.native_agent_support import validate
from stpd.models.structured_training import StructuredTrainingConfig
from stpd.native_graph_spec import PRESETS
from stpd.policy.native_agent import adapter_identity
from stpd.policy.native_structured_export import (
    GRAPH_TRAINED_PACKAGE_SCHEMA,
    TRAINED_PACKAGE_SCHEMA,
    load_native_package,
)
from stpd.structured_profiles import NATIVE_GRAPH_SCOPE, NATIVE_SCOPE
from stpd.workers.structured_control import StructuredWorkloadRequest
from stpd.workers.structured_execution import (
    execute_structured_workload,
    prepare_structured_workload,
)


@pytest.mark.parametrize("control", (None, *PRESETS), ids=lambda c: c.id if c else "legacy")
def test_export_registration_readiness_and_identity_tampering(tmp_path, monkeypatch, control):
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    reporter = ObjectStoreRunReporter(store, store.blobs)
    producer = origin()
    run = prepare_structured_workload(
        store, data(), producer, StructuredTrainingConfig(epochs=1, max_updates=1),
        operation_id="1" * 32,
        code_scope=NATIVE_SCOPE if control is None else NATIVE_GRAPH_SCOPE,
        model_control=control,
    )
    request = StructuredWorkloadRequest(run.artifact_id, run.parent("training_input"),
                                        "1" * 32, "2" * 32)
    result = execute_structured_workload(store, reporter, request, producer,
        authority=Authority(), attempt_producer=producer)
    assert result.state == "completed" and result.optimizer_updates == 1
    config = ProjectConfig(tmp_path / "application", "", "", None, combination())
    export = LocalModelExport(config)
    monkeypatch.setattr(export, "_workspace", lambda: SimpleNamespace(store=store))
    export.start(result.model_id)
    assert settled(export)["status"] == "completed"
    folder = export.verified_for_registration(result.model_id)
    package, model = load_native_package(folder)
    assert model.model_control == control
    assert package["schema"] == (
        TRAINED_PACKAGE_SCHEMA if control is None else GRAPH_TRAINED_PACKAGE_SCHEMA
    )
    # Test the real own-byte download route, with no private ancestry available.
    directory = config.state_dir / "downloads" / result.model_id
    directory.mkdir(parents=True)
    artifact = store.get_manifest(result.model_id)
    (directory / "manifest.json").write_bytes(artifact.to_bytes())
    (directory / "download.json").write_bytes(json_bytes({
        "schema": "stpd/result-download-v1", "artifact_id": result.model_id,
    }))
    for payload in artifact.payloads:
        (directory / (payload.sha256 + ".bin")).write_bytes(b"".join(store.read_payload(payload)))
    monkeypatch.setattr(export, "_workspace", lambda: pytest.fail("private ancestry opened"))
    export.start(result.model_id)
    assert settled(export)["status"] == "completed"
    models = LocalModelService(config)
    registration = LocalModelRegistration(config, export, models)
    # Isolate application metadata/readiness identity from a native installation.
    # No package loader, binder, adapter identity or validation is replaced.
    monkeypatch.setattr(registration, "_native_runtime", lambda **_: (Path("/test"), Path("/sdk")))
    monkeypatch.setattr(registration, "_capabilities", lambda *_, **__: native_capabilities())
    monkeypatch.setattr(models, "_public_manifest_contract", lambda *_, **__: None)
    registered = registration.register(result.model_id)
    assert registered["status"] == "registered", registered
    assert registration.register(result.model_id) == registered
    assert registration.status(result.model_id) == registered
    entry = models.selection(registered["selection_id"])
    config_path = models.entry_path(entry, "config")
    manifest_path = models.entry_path(entry, "manifest")
    metadata, manifest = validate(models.root, config_path, manifest_path,
                                  binding_root=models.private_root)
    assert metadata["artifact_id"] == result.model_id
    assert metadata["package_model_id"] == package["model_id"] != result.model_id
    assert manifest["adapter"] == adapter_identity(graph=control is not None)
    assert models.adapter_arguments(entry)[0:2] == ["-m", "stpd.policy.native_agent"]
    readiness = models.readiness(entry["id"])
    assert readiness["checks"]["policy_identity"] == {"status": "pass"}
    assert readiness["checks"]["backend"] == {"status": "pass", "code": "cpu"}

    changed = copy.deepcopy(manifest)
    changed["adapter"] = adapter_identity(graph=control is None)
    manifest_path.write_bytes(json_bytes(changed))
    with pytest.raises(BoundaryError, match="export_identity_drift"):
        validate(models.root, config_path, manifest_path, binding_root=models.private_root)
    assert registration.status(result.model_id)["status"] == "not_registered"
    assert models.readiness(entry["id"])["checks"]["policy_identity"] == {
        "status": "blocked", "code": "export_identity_drift",
    }
    manifest_path.write_bytes(json_bytes(manifest))

    # A config flag cannot opt a legacy package into Graph execution or swap a control.
    changed_config = copy.deepcopy(metadata)
    changed_config["model_control"] = PRESETS[0].to_dict()
    config_path.write_bytes(json_bytes(changed_config))
    with pytest.raises(BoundaryError):
        validate(models.root, config_path, manifest_path, binding_root=models.private_root)
    config_path.write_bytes(json_bytes(metadata))
    if control is not None:
        changed_package = copy.deepcopy(package)
        other = PRESETS[(PRESETS.index(control) + 1) % len(PRESETS)]
        changed_package["graph"]["model_control"] = other.to_dict()
        (folder / "model.json").write_bytes(json_bytes(changed_package))
        with pytest.raises(BoundaryError, match="package_identity_mismatch"):
            validate(models.root, config_path, manifest_path, binding_root=models.private_root)
        (folder / "model.json").write_bytes(json_bytes(package))
    assert registration.status(result.model_id) == registered
    assert json.loads(config_path.read_bytes()) == metadata
    models.close()
