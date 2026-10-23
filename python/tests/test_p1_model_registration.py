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
from stpd.p1.execution import MStdExecution
from stpd.p1.export import export_model
from stpd.p1.input import LEGACY_INPUT_SPEC, fit_vocabulary, project
from stpd.p1.model import MStd


@pytest.fixture(params=[
    "detached", "trained", "legacy-detached", "packed-detached", "packed-trained",
])
def p1_registration(tmp_path, monkeypatch, request):
    store = store_at(tmp_path / "store")
    package = tmp_path / "original"
    checkpoint = tmp_path / "last.tensor-tree"
    checkpoint.write_bytes(b"synthetic engineering checkpoint")
    value = observation()
    projection = LEGACY_INPUT_SPEC if request.param == "legacy-detached" else None
    if projection is not None:
        value["revision"] = projection["revision"]
    vocabulary = fit_vocabulary([project(value, input_projection=projection)])
    if projection is not None:
        vocabulary["input_spec"] = dict(projection)
    execution = (MStdExecution(implementation="experimental-packed-v1")
                 if request.param.startswith("packed-") else MStdExecution())
    if request.param.startswith("baseline-"):
        from test_p1_baselines import populations

        from stpd.p1.baseline_export import export_model as baseline_export
        from stpd.p1.baseline_spec import BaselineConfig
        from stpd.p1.baselines import BaselineTrainer

        trainer = BaselineTrainer(populations(), "synthetic-snapshot",
                                  BaselineConfig(algorithm=request.param.removeprefix("baseline-"),
                                                 max_epochs=1))
        while trainer.phase == "training":
            trainer.advance()
        trainer.finish()
        checkpoint.write_bytes(trainer.checkpoint())
        descriptor = baseline_export(
            trainer.selected_model(), checkpoint, package, trainer.identity)
    else:
        descriptor = export_model(MStd(vocabulary, execution), checkpoint, package,
                                  {"scope": "synthetic"})
    payloads = tuple(store.put_payload(role, io.BytesIO((package / name).read_bytes()), media)
                     for role, name, media in (
                         ("weights", "model.tensor-tree", "application/vnd.stpd.tensor-tree"),
                         ("package_manifest", "model.json", "application/json")))
    metadata = {**descriptor, "training_ancestry": "unknown"}
    if request.param.startswith("baseline-"):
        metadata["schema"] = "stpd/p1-baseline-trained-model-1"
    if request.param in {"trained", "packed-trained"}:
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
                "input": {"profile": "p1-global-r1",
                          "global": {"core_sha256": "a" * 64, "revision": "r1.5"}},
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
    service, models, _, model, _, _ = p1_registration
    selection = service.register(model.artifact_id)["selection_id"]
    verified = []
    monkeypatch.setattr(models, "_runtime_package", lambda name: verified.append(name))
    monkeypatch.setattr(models, "_node_modules", lambda _: Path("/trusted/runtime/node_modules"))
    result = models._agent_evidence_expected({
        "agent_manifest_id": selection, "run_id": "run-original"})
    assert verified == [selection]
    config = models.entry_path(models.selection(selection), "config")
    import json

    original = json.loads(config.read_bytes())
    assert result["model_binding"] == {key: original[key] for key in (
        "package_input_spec", "input_spec", "input_projection")}
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


@pytest.mark.parametrize("runtime_version", ["0.1.0-rc.40", "0.1.0-rc.41"])
def test_application_stop_window_is_in_original_request_and_runtime_spawn(
    p1_registration, monkeypatch, runtime_version,
):
    from spireagent.workbench import local_models
    from spireagent.workbench.agent_run_request import publish

    service, models, _, model, _, _ = p1_registration
    selection = service.register(model.artifact_id)["selection_id"]
    progress = {"windowDecisions": 32, "maxRepeats": 8}
    monkeypatch.setattr(models, "runtime_profile", lambda _: (
        Path("/trusted/runtime"), {"version": runtime_version}))
    assert models._continuous_profile(selection)
    continuous = models.agent_request(selection, "start", run_profile="continuous")
    assert continuous["run_profile"] == "continuous"
    request = models.agent_request(selection, "start", global_no_progress=progress)
    assert request["budget"]["no_progress"] == progress
    assert "no_progress" not in models.agent_request(selection, "start")["budget"]
    models.state["agent_run_request"] = publish(models.directory, request, "original-operation")
    monkeypatch.setattr(models, "readiness", lambda _: {"status": "ready_to_load"})
    monkeypatch.setattr(models, "_runtime_package", lambda _: {
        "version": runtime_version, "code_sha256": "b" * 64})
    monkeypatch.setattr(models, "_node_modules", lambda _: Path("/trusted/node_modules"))
    monkeypatch.setattr(local_models, "_check_runtime_port", lambda _: None)
    commands = []

    def capture(command, **_):
        commands.append(command)
        raise RuntimeError("captured-before-native-launch")

    monkeypatch.setattr(local_models.subprocess, "Popen", capture)
    with pytest.raises(RuntimeError, match="captured-before-native-launch"):
        models._start(selection)
    assert len(commands) == 1
    command = commands[0]
    assert command[command.index("--no-progress-window") + 1] == "32"
    assert command[command.index("--no-progress-repeats") + 1] == "8"
    with pytest.raises(ValueError, match="invalid_global_no_progress"):
        models.agent_request(selection, "start", global_no_progress={**progress, "extra": 1})


def test_downloaded_original_model_registers_through_own_payload_cache(
    p1_registration, monkeypatch,
):
    from spireagent.workbench.developer import atomic_json

    service, models, store, model, _, _ = p1_registration
    target = service.config.state_dir / "downloads" / model.artifact_id
    target.mkdir(parents=True)
    (target / "manifest.json").write_bytes(model.to_bytes())
    atomic_json(target / "download.json", {
        "schema": "stpd/result-download-v1", "artifact_id": model.artifact_id})
    for payload in model.payloads:
        (target / (payload.sha256 + ".bin")).write_bytes(b"".join(store.read_payload(payload)))
    monkeypatch.setattr(service.export, "_source",
                        LocalModelExport._source.__get__(service.export, LocalModelExport))
    result = service.register(model.artifact_id)
    assert result["status"] == "registered"
    entry = models.selection(result["selection_id"])
    assert installation(models.entry_path(entry, "config"),
                        models.entry_path(entry, "manifest"), models.private_root)[0][
                            "model_artifact_id"] == model.artifact_id
    # The normal cache reader authenticates bytes; a downloaded object is not
    # an excuse to bypass integrity or invent its missing training ancestry.
    weights = model.payload("weights")
    (target / (weights.sha256 + ".bin")).write_bytes(b"broken")
    with pytest.raises(ValueError, match="download_payload_invalid"):
        service.register(model.artifact_id)


def stopped_context(p1_registration, monkeypatch):
    from spireagent.workbench.local_models import _startup_identity

    service, models, _, model, _, _ = p1_registration
    selection = service.register(model.artifact_id)["selection_id"]
    entry = models.selection(selection)
    import json

    manifest = json.loads(models.entry_path(entry, "manifest").read_bytes())
    package = {"version": "0.1.0-rc.40", "code_sha256": "b" * 64}
    startup = {**_startup_identity(manifest, package),
               "run_id": "run-11111111-1111-1111-1111-111111111111"}
    models.state.update(status="stopped", startup=startup, loaded=False)
    monkeypatch.setattr(models, "_runtime_package", lambda _: package)
    monkeypatch.setattr(models, "_node_modules", lambda _: Path("/trusted/node_modules"))
    return service, models, model, startup


def test_global_preservation_context_is_only_from_owned_stopped_run(p1_registration, monkeypatch):
    _, models, model, startup = stopped_context(p1_registration, monkeypatch)
    context = models.stopped_agent_evidence_context(startup["run_id"])
    assert context["model_artifact_id"] == model.artifact_id
    assert context["directory"] == str(models.directory / "agent-runs" / startup["run_id"])
    assert context["expected"]["run_id"] == startup["run_id"]
    assert context["expected"]["global_runtime_directory"].startswith("/trusted/node_modules/")
    with pytest.raises(ValueError, match="owned_stopped_agent_run_required"):
        models.stopped_agent_evidence_context("run-22222222-2222-2222-2222-222222222222")
    models.state.update(status="loaded", loaded=True)
    with pytest.raises(ValueError, match="stop_runtime_before_install"):
        models.stopped_agent_evidence_context(startup["run_id"])


def test_global_publish_http_uses_trusted_context_and_original_data_operation(
    p1_registration, monkeypatch, tmp_path,
):
    import json
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from spireagent.workbench.developer_server import (
        Application,
        configuration_id,
        create_server,
    )

    service, models, model, startup = stopped_context(p1_registration, monkeypatch)
    config_path = tmp_path / "project-global.json"
    atomic_json(config_path, service.config.to_dict())
    app = Application(service.config, config_path=config_path)
    app.models = models
    atomic_json(service.config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(service.config)})
    received = []
    monkeypatch.setattr(app.local_recording_import, "start_global_evidence",
                        lambda context, peer: received.append((context, peer)) or {
                            "schema": "stpd/local-import-v1", "status": "pending"}, raising=False)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/local-models/evidence/publish"
    headers = {"Authorization": "Bearer " + app.control_token, "Content-Type": "application/json"}
    body = {"run_id": startup["run_id"], "peer": {"kind": "local", "directory": "/peer"}}
    try:
        request = Request(url, headers=headers, data=json.dumps(body).encode())
        with urlopen(request, timeout=5) as response:
            assert json.load(response)["status"] == "pending"
        context, peer = received[0]
        assert context["model_artifact_id"] == model.artifact_id and peer == body["peer"]
        assert context["expected"]["run_id"] == startup["run_id"]
        with pytest.raises(HTTPError) as invalid:
            bad = Request(url, headers=headers,
                          data=json.dumps({**body, "directory": "/untrusted"}).encode())
            urlopen(bad, timeout=5)
        assert invalid.value.code == 409 and len(received) == 1
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=3)


def test_http_owned_global_run_reaches_original_importer_and_readonly_reconcile(
    p1_registration, monkeypatch, tmp_path,
):
    """Real Application/import journal/CAS/transfer; typed replay alone is synthetic."""
    import hashlib
    import json
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    import sts2_platform_evidence as evidence

    from spireagent.encoding import semantic_hash
    from spireagent.workbench import global_evidence_store, local_recording_import
    from spireagent.workbench.developer_server import (
        Application,
        configuration_id,
        create_server,
    )

    service, models, model, startup = stopped_context(p1_registration, monkeypatch)
    store = p1_registration[2]
    modules = tmp_path.resolve() / "node_modules"
    runtime = modules / "@rsgcsg/sts2-policy-runtime/dist"
    runtime.mkdir(parents=True)
    code = b"// synthetic Runtime bytes; never executed\n"
    (runtime / "global-replay.js").write_bytes(code)
    atomic_json(runtime.parent / "package.json", {
        "name": "@rsgcsg/sts2-policy-runtime", "version": startup["runtime_version"]})
    startup["runtime_code_sha256"] = hashlib.sha256(
        b"global-replay.js\0" + code + b"\0").hexdigest()
    monkeypatch.setattr(models, "_node_modules", lambda _: modules)
    context = models.stopped_agent_evidence_context(startup["run_id"])
    original = Path(context["directory"])
    original.mkdir(parents=True)
    atomic_json(original / "manifest.json", {
        "schema": global_evidence_store.RUN_SCHEMA, "run_id": startup["run_id"],
        "agent_artifact_sha256": startup["agent_artifact_sha256"],
        "runtime_code_sha256": startup["runtime_code_sha256"],
        "runtime_version": startup["runtime_version"], "status": "stopped", "tainted": False})
    entry = models.selection(startup["agent_manifest_id"])
    (original / "agent-manifest.json").write_bytes(
        models.entry_path(entry, "manifest").read_bytes())
    raw = b"synthetic full original GLOBAL input/catalog/scores/Receipt\r\n"
    (original / "original.bin").write_bytes(raw)
    verifications = []

    def verify(directory, expected):
        assert expected == context["expected"]
        directory = Path(directory)
        verifications.append(directory)
        files = [{"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "bytes": path.stat().st_size} for path in sorted(directory.iterdir())]
        value = evidence.AgentSessionRunEvidence(
            directory, json.loads((directory / "manifest.json").read_bytes()),
            json.loads((directory / "agent-manifest.json").read_bytes()), {}, 1,
            semantic_hash({"schema": "sts2.evidence/store-directory-1", "files": files}))
        return SimpleNamespace(passed=True, value=value, findings=[])

    monkeypatch.setattr(evidence, "verify_agent_session_run_evidence", verify)
    monkeypatch.setattr(local_recording_import, "_selected_store", lambda *a, **k: (store, None))
    monkeypatch.setattr(local_recording_import, "_sync", lambda *a: None)
    monkeypatch.setattr(local_recording_import, "source_identity", lambda _: PRODUCER)
    config_path = tmp_path / "global-project.json"
    atomic_json(config_path, service.config.to_dict())
    app = Application(service.config, config_path=config_path)
    app.models = models
    atomic_json(service.config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(service.config)})
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    headers = {"Authorization": "Bearer " + app.control_token, "Content-Type": "application/json"}

    def post(path, body):
        with urlopen(Request(base + path, headers=headers, data=json.dumps(body).encode()),
                     timeout=5) as response:
            return json.load(response)

    try:
        with pytest.raises(HTTPError) as invalid:
            post("/api/local-models/evidence/publish", {"run_id": startup["run_id"], "peer": []})
        assert invalid.value.code == 409
        pending = post("/api/local-models/evidence/publish", {
            "run_id": startup["run_id"],
            "peer": {"kind": "local", "directory": str(tmp_path / "peer")}})
        assert pending["recording_type"] == "p1_global" and "_global_context" not in pending
        importer = app.local_recording_import
        importer.thread.join(10)
        assert not importer.thread.is_alive()
        with urlopen(Request(base + "/api/local-recordings/import/status", headers=headers),
                     timeout=5) as response:
            result = json.load(response)
        assert result["status"] == "completed", result
        assert result["local_readback"]["typed_verification"] == "pass"
        assert result["peer_readback"]["typed_verification"] == "pass"
        artifact_id = result["artifact_id"]
        assert store.get_manifest(artifact_id).parent("model") == model.artifact_id
        retained = json.loads(importer.path.read_bytes())
        assert retained["_global_context"] == context
        assert len(verifications) >= 4

        def no_write(*args, **kwargs):
            raise AssertionError("read-only reconciliation attempted publication")

        monkeypatch.setattr(global_evidence_store, "publish_global_evidence", no_write)
        monkeypatch.setattr(global_evidence_store, "publish_global_runtime", no_write)
        with pytest.raises(HTTPError) as invalid:
            post("/api/local-recordings/import/reconcile", {"directory": "/untrusted"})
        assert invalid.value.code == 400
        reconciled = post("/api/local-recordings/import/reconcile", {})
        assert reconciled["status"] == "completed" and reconciled["artifact_id"] == artifact_id
        assert (original / "original.bin").read_bytes() == raw
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=3)
