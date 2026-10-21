"""Normal Store Model registration; only public environment negotiation is synthetic."""
from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_p1_snapshot import store_at
from test_p1_t0 import observation

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import FrozenObject
from spireagent.workbench.developer import ROOT, ProjectConfig, atomic_json, combination
from spireagent.workbench.local_model_export import LocalModelExport
from spireagent.workbench.local_model_registration import REGISTRY, LocalModelRegistration
from spireagent.workbench.local_models import LocalModelService
from stpd.p1.agent import installation
from stpd.p1.export import export_model
from stpd.p1.input import fit_vocabulary, project
from stpd.p1.model import MStd


@pytest.fixture(params=["detached", "trained"])
def p1_registration(tmp_path, monkeypatch, request):
    store = store_at(tmp_path / "store")
    package = tmp_path / "original"
    checkpoint = tmp_path / "last.tensor-tree"
    checkpoint.write_bytes(b"synthetic engineering checkpoint")
    vocabulary = fit_vocabulary([project(observation())])
    descriptor = export_model(MStd(vocabulary), checkpoint, package, {"scope": "synthetic"})
    payloads = tuple(store.put_payload(role, io.BytesIO((package / name).read_bytes()), media)
                     for role, name, media in (
                         ("weights", "model.tensor-tree", "application/vnd.stpd.tensor-tree"),
                         ("package_manifest", "model.json", "application/json")))
    metadata = {**descriptor, "training_ancestry": "unknown"}
    if request.param == "trained":
        metadata["schema"] = "stpd/p1-mstd-trained-model-1"
    model = Manifest("model", PRODUCER, payloads=payloads,
                     parameters=FrozenObject.of(metadata))
    store.publish(model)
    state = tmp_path / "app-state"
    state.mkdir()
    config = ProjectConfig(state, "", "http://127.0.0.1:19191", None, combination())
    export = LocalModelExport(config)
    monkeypatch.setattr(export, "_source", lambda _: store)
    models = LocalModelService(config)
    models.root = ROOT
    service = LocalModelRegistration(config, export, models)
    monkeypatch.setattr(service, "_native_runtime", lambda **_: (tmp_path / "modules", Path(".")))
    negotiations = []

    def envelope(runtime, options, artifact, adapter, selection, deadline):
        negotiations.append(options)
        assert artifact.read_bytes() == (package / "model.tensor-tree").read_bytes()
        return {"schema": "sts2.policy-runtime/agent-manifest-1", "manifest_id": selection,
                "input": {"profile": "p1-global-r1", "global": {"core_sha256": "a" * 64}},
                "environment": {"kind": "native"}, "adapter": adapter}

    monkeypatch.setattr(service, "_global_environment_manifest", envelope)
    monkeypatch.setattr(models, "_public_manifest_contract", lambda *_: None)
    return service, models, store, model, package, negotiations


def test_original_model_normal_registration_binds_exact_bytes_and_composition(p1_registration):
    service, models, store, model, package, negotiations = p1_registration
    result = service.register(model.artifact_id)
    assert result["status"] == "registered"
    assert negotiations == [{"connector-endpoint": "http://127.0.0.1:19191"}]
    entry = models.selection(result["selection_id"])
    config_path = models.entry_path(entry, "config")
    config, manifest, _, _ = installation(config_path, models.entry_path(entry, "manifest"),
                                          models.private_root)
    assert config["model_artifact_id"] == model.artifact_id
    assert manifest["manifest_id"] == result["selection_id"]
    assert Path(config["package_path"]).read_bytes() == (package / "model.tensor-tree").read_bytes()
    assert service.status(model.artifact_id)["selection_id"] == result["selection_id"]
    evidence = SimpleNamespace(status="pass", value=SimpleNamespace(agent_manifest=manifest))
    raw, reason = models._original_native_composition(evidence)
    assert reason is None and raw == (package / "model.json").read_bytes()
    (config_path.parent / "model.json").write_bytes(b"{}")
    assert models._original_native_composition(evidence)[1] == "original_package_metadata_changed"
    assert store.get_manifest(model.artifact_id).parents == ()


def test_failed_p1_environment_negotiation_does_not_publish_selection(p1_registration, monkeypatch):
    service, models, _, model, _, _ = p1_registration
    def fail(*_):
        raise ValueError("synthetic negotiation failed")
    monkeypatch.setattr(service, "_global_environment_manifest", fail)
    with pytest.raises(ValueError, match="synthetic negotiation failed"):
        service.register(model.artifact_id)
    assert not (models.private_root / REGISTRY).exists()


def test_global_model_evidence_uses_trusted_application_runtime(p1_registration, monkeypatch):
    _, models, _, _, _, _ = p1_registration
    monkeypatch.setattr(models, "selection", lambda _: {"id": "m", "adapter": "stpd-p1-mstd-agent"})
    verified = []
    monkeypatch.setattr(models, "_runtime_package", lambda name: verified.append(name))
    monkeypatch.setattr(models, "_node_modules", lambda _: Path("/trusted/runtime/node_modules"))
    result = models._agent_evidence_expected({"agent_manifest_id": "m", "run_id": "run-original"})
    assert verified == ["m"]
    assert result["global_runtime_directory"].startswith("/trusted/runtime/node_modules/")


def test_normal_application_http_control_token_registers_p1_model(
    p1_registration, tmp_path, monkeypatch,
):
    import json
    import threading
    from urllib.request import Request, urlopen

    from spireagent.workbench.developer_server import (
        Application,
        configuration_id,
        create_server,
    )

    service, models, _, model, _, _ = p1_registration
    config_path = tmp_path / "project.json"
    atomic_json(config_path, service.config.to_dict())
    app = Application(service.config, config_path=config_path)
    app.models = models
    app.local_model_registration = service
    atomic_json(service.config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(service.config),
    })
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/local-model-registrations/"
    headers = {"Authorization": "Bearer " + app.control_token, "Content-Type": "application/json"}
    try:
        with urlopen(Request(url + "register", headers=headers,
                            data=json.dumps({"model_id": model.artifact_id}).encode()),
                     timeout=5) as response:
            registered = json.load(response)
        assert registered["status"] == "registered"
        with urlopen(Request(url + "status?model_id=" + model.artifact_id, headers=headers),
                     timeout=5) as response:
            assert json.load(response)["selection_id"] == registered["selection_id"]
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=3)
