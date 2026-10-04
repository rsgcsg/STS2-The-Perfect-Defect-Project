"""Real tiny numerical CAS exports and CPU Port4 children; no game qualification."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
import torch
from test_public_m2_port import request
from test_public_m2_run import _chain, _fixture

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench.developer import ROOT
from spireagent.workbench.local_model_registration import _requirements
from stpd.policy.public_m2_export import export_model, model_lineage, receipt, validate_package
from stpd.policy.public_m2_port import PORT_SCHEMA, PublicM2PolicyAdapter
from stpd.public_m2_policy_installation import (
    ADAPTER_SOURCE_CLOSURE,
    bind_public_m2_export,
    code_digest,
    validate,
)
from stpd.workers.public_m2_run import MODEL_SCHEMA, execute_public_m2_run, prepare_public_m2_run


@pytest.fixture(scope="module")
def stages(tmp_path_factory):
    root = tmp_path_factory.mktemp("public-m2-consumer")
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    groups = []
    try:
        for count in (3, 5, 9):
            store, reporter, producer, view, allocation, value, config = _fixture(root / "store")
            value = replace(value, chains=(_chain("train", "train", count), value.chains[1]),
                            max_action_bytes=8192)
            # PublicM2Input pins selected transition IDs separately from chains.
            value = replace(value, codec_fit_chain_ids=(value.chains[0].chain_id,),
                            codec_fit_transition_ids=tuple(r.transition_id
                                                       for r in value.chains[0].evidence))
            config = replace(config, source_digest=value.identity, max_action_bytes=8192,
                             window_steps=4)
            run = prepare_public_m2_run(
                store, value, config, producer, source_view_id=view.artifact_id,
                allocation_id=allocation.artifact_id, operation_id=f"{count:032x}",
            )
            execute_public_m2_run(store, reporter, run.artifact_id, producer)
            models = sorted((store.get_manifest(i) for i in store.manifest_ids()
                             if store.get_manifest(i).parameters.value().get("schema")
                             == MODEL_SCHEMA and store.get_manifest(i).parent("run")
                             == run.artifact_id), key=lambda m: m.parameters.value()["epoch"])
            groups.append((store, models))
    finally:
        torch.set_num_threads(previous)
    return groups


def _generic_contract():
    from test_public_m0_registration_budget import capabilities

    return _requirements(capabilities(), input_profile="public-snapshot-m0-v1")


def _install(store, model, folder):
    exported = folder / "export"
    checked = _export(store, model, exported)
    config_path, manifest_path = folder / "binding/config.json", folder / "binding/manifest.json"
    requirements, support = _generic_contract()
    bind_public_m2_export(
        ROOT, exported, config_path, manifest_path, manifest_id="test-public-m2",
        policy={"id": "test-public-m2", "version": "1.0.0", "provider": "stpd",
                "architecture": "public-m2-observation-stateful-v1"},
        requirements=requirements, support=support, binding_root=folder,
    )
    return exported, config_path, manifest_path, checked


def _stage_id(store, model):
    return next(i for i in store.manifest_ids() if (
        (m := store.get_manifest(i)).parameters.value().get("schema")
        == "stpd/public-m2-epoch-stage-v1" and m.parent("model") == model.artifact_id))


def _export(store, model, directory):
    return export_model(store, model.artifact_id, directory, stage_id=_stage_id(store, model))


def test_nine_export_receipts_and_exact_stage_cpu_weights(stages, tmp_path):
    identities = set()
    for group, (store, models) in enumerate(stages):
        assert [m.parameters.value()["epoch"] for m in models] == [1, 3, 5]
        for model in models:
            identities.add(model.artifact_id)
            folder = tmp_path / str(group) / str(model.parameters.value()["epoch"])
            exported, config_path, manifest_path, checked = _install(store, model, folder)
            installed, manifest = validate(ROOT, config_path, manifest_path, binding_root=folder)
            assert installed["runtime_device"] == "cpu"
            assert manifest["adapter"]["protocol"].endswith("ndjson-4")
            assert manifest["requirements"]["reads"] == []
            assert checked == _export(store, model, exported)
            assert checked["training_operation_id"] == model.parameters.value()["operation_id"]
            assert validate_package(exported, load_weights=True)[0] == model
    assert len(identities) == 9


def test_real_cli_cpu_ready_replay_and_segment_reset(stages, tmp_path):
    store, models = stages[0]
    exported, config_path, manifest_path, checked = _install(store, models[-1], tmp_path)
    manifest = json.loads(manifest_path.read_text())
    model, config, _lineage = validate_package(exported)
    adapter = PublicM2PolicyAdapter.from_export(
        (exported / "weights.tensor-tree").read_bytes(), config,
        input_digest=model.parameters.value()["engine_input_digest"], completed_epochs=5,
        tokenizer_bytes=(exported / "state_tokenizer.json").read_bytes(), manifest=manifest,
        inference_device="cpu",
    )
    first = request(SimpleNamespace(manifest=manifest))
    next_segment = request(SimpleNamespace(manifest=manifest), token="token-b")
    expected, _ = adapter.decide(first["decision"], first["control"])
    payloads = [first, first, next_segment]
    wire = "".join(json.dumps({"schema": PORT_SCHEMA, "message_type": "decide",
                              "request_id": str(i), **payload}) + "\n"
                   for i, payload in enumerate(payloads))
    result = subprocess.run(
        [sys.executable, "-I", "-m", "stpd.policy.public_m2_cli", "serve", "--config",
         str(config_path), "--manifest", str(manifest_path), "--binding-root", str(tmp_path)],
        input=wire, text=True, capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    messages = [json.loads(line) for line in result.stdout.splitlines()]
    assert messages[0]["message_type"] == "ready"
    assert len(messages) == 4
    assert all(m["message_type"] == "decision" for m in messages[1:])
    assert messages[1]["output"] == expected
    assert messages[2]["output"] == expected
    assert messages[3]["output"] == expected  # fresh token resets numerical memory
    assert receipt(exported) == checked
    audit = subprocess.run(
        [sys.executable, "-I", "-c", """
import json, pathlib, runpy, sys
sys.argv = ['stpd.policy.public_m2_cli', *sys.argv[1:]]
try:
    runpy.run_module('stpd.policy.public_m2_cli', run_name='__main__')
except SystemExit as error:
    if error.code: raise
root = pathlib.Path(sys.modules['stpd'].__file__).resolve().parent.parent
paths = sorted({pathlib.Path(module.__file__).resolve().relative_to(root).as_posix()
    for name,module in sys.modules.items()
    if name.split('.')[0] in {'stpd','spireagent'} and getattr(module,'__file__',None)})
print('CLOSURE=' + json.dumps(paths), file=sys.stderr)
""", "serve", "--config", str(config_path), "--manifest", str(manifest_path),
         "--binding-root", str(tmp_path)],
        input="", text=True, capture_output=True, timeout=30, check=False,
    )
    assert audit.returncode == 0, audit.stderr
    closure = json.loads(next(line[8:] for line in audit.stderr.splitlines()
                              if line.startswith("CLOSURE=")))
    (tmp_path / "runtime-closure.json").write_text(json.dumps(closure, indent=2))
    assert set(closure) <= set(ADAPTER_SOURCE_CLOSURE)
    # Existing public renderer imports shared archive transport helpers from Hub.
    # The Hub application and Workbench lifecycle are outside the runtime graph.
    assert not any(p.startswith("spireagent/workbench/")
                   or p == "spireagent/hub/application.py" for p in closure)


@pytest.mark.parametrize("mutation", ["weights", "state_tokenizer", "extra", "lineage"])
def test_export_tampering_is_rejected(stages, tmp_path, mutation):
    store, models = stages[0]
    exported = tmp_path / "export"
    _export(store, models[0], exported)
    if mutation in {"weights", "state_tokenizer"}:
        name = "weights.tensor-tree" if mutation == "weights" else "state_tokenizer.json"
        with (exported / name).open("ab") as handle:
            handle.write(b"x")
    elif mutation == "extra":
        (exported / ".DS_Store").write_bytes(b"extra")
    else:
        envelope = json.loads((exported / "model.json").read_text())
        envelope["lineage"]["input_id"] = "f" * 64
        (exported / "model.json").write_bytes(json_bytes(envelope))
    with pytest.raises(BoundaryError):
        validate_package(exported, load_weights=True)


def test_source_metadata_lineage_without_payload_reads(stages, monkeypatch):
    store, models = stages[0]
    monkeypatch.setattr(store, "read_payload", lambda *_: pytest.fail("payload read"))
    for model in models:
        assert model_lineage(store, model, stage_id=_stage_id(store, model))["run_id"] \
            == model.parent("run")


def test_consumer_pin_excludes_workbench_and_detects_scorer_change(tmp_path):
    for name in ADAPTER_SOURCE_CLOSURE:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    original = code_digest(tmp_path)
    unrelated = tmp_path / "spireagent/workbench/local_models.py"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("# unrelated Workbench change\n")
    assert code_digest(tmp_path) == original
    scorer = tmp_path / "stpd/policy/public_m2_port.py"
    scorer.write_text(scorer.read_text() + "\n# changed scorer\n")
    assert code_digest(tmp_path) != original
    scorer.unlink()
    with pytest.raises(BoundaryError, match="consumer_source_missing"):
        code_digest(tmp_path)


def test_epoch_stage_and_terminal_completion_are_required(stages, monkeypatch):
    store, models = stages[0]
    with pytest.raises(BoundaryError, match="epoch_stage_required"):
        model_lineage(store, models[0], stage_id=_stage_id(store, models[1]))
    from spireagent.storage.run_reporter import ObjectStoreRunReporter

    monkeypatch.setattr(ObjectStoreRunReporter, "completed", lambda *_args: None)
    assert model_lineage(store, models[0], stage_id=_stage_id(store, models[0]))
    with pytest.raises(BoundaryError, match="completed_run_result_required"):
        model_lineage(store, models[-1], stage_id=_stage_id(store, models[-1]))


def test_valid_weights_substitution_cannot_name_another_checkpoint(stages, tmp_path):
    from spireagent.artifact_contracts import Parent
    from stpd.workers.public_m2_engine import _tensor_digest, decode_checkpoint, encode_checkpoint

    store, models = stages[0]
    original = models[0]
    raw = b"".join(store.read_payload(original.payload("weights")))
    weights = decode_checkpoint(raw)
    first = next(iter(weights["weights"].values()))
    first.reshape(-1)[0] += 1.0
    weights["weights_digest"] = _tensor_digest(weights["weights"])
    payload = store.put_payload("weights", io.BytesIO(encode_checkpoint(weights)),
                                "application/vnd.stpd.tensor-tree")
    replacement = replace(original, payloads=(payload, original.payload("state_tokenizer")))
    store.publish(replacement)
    stage = store.get_manifest(_stage_id(store, original))
    evaluation = store.get_manifest(stage.parent("offline_evaluation"))
    changed_evaluation = replace(evaluation, parents=tuple(
        Parent(p.role, replacement.artifact_id) if p.role == "model" else p
        for p in evaluation.parents))
    store.publish(changed_evaluation)
    changed_stage = replace(stage, parents=tuple(
        Parent(p.role, replacement.artifact_id) if p.role == "model" else
        Parent(p.role, changed_evaluation.artifact_id) if p.role == "offline_evaluation" else p
        for p in stage.parents))
    store.publish(changed_stage)
    with pytest.raises(BoundaryError, match="checkpoint_weights_mismatch"):
        export_model(store, replacement.artifact_id, tmp_path / "substituted",
                     stage_id=changed_stage.artifact_id)
    assert not (tmp_path / "substituted").exists()


def test_shared_text_wrapper_and_config_decoder_match_producer(stages):
    from stpd.fullrun.token_format import input_texts
    from stpd.fullrun.token_inputs import input_texts as training_texts
    from stpd.policy.public_m2_export import model_details
    from stpd.workers.public_m2_run import _config

    assert input_texts is training_texts
    assert input_texts("a\n中文", ("x", "")) == ("OBS\na\n中文\n", ("ACT\nx\n", "ACT\n\n"))
    for _, models in stages:
        for model in models:
            assert model_details(model)[0] == _config(model.parameters.value()["config"])


def test_workbench_nine_real_child_exports_archives_and_registration(stages, tmp_path, monkeypatch):
    """Real CAS/child/registry/binder/Runtime decoder, with owner/connector leaves stubbed.

    Owner admission itself is exercised with a real curation ledger in the
    separate test_local_training_projection_receipt suite. No game or release pin.
    """
    from test_public_m0_policy_port import capabilities

    from spireagent.storage.registry import SQLiteRegistry, sync_registry
    from spireagent.workbench import local_model_registration as registration_module
    from spireagent.workbench.developer import (
        LocalResearchWorkspaceConfig,
        ProjectConfig,
        combination,
    )
    from spireagent.workbench.local_model_export import EXPORT_ROOT, LocalModelExport
    from spireagent.workbench.local_model_registration import LocalModelRegistration
    from spireagent.workbench.local_models import LocalModelService

    store = stages[0][0]
    registry = SQLiteRegistry(tmp_path / "registry.sqlite")
    sync_registry(store, registry)
    state = tmp_path / "state"
    state.mkdir()
    config = ProjectConfig(state, "", "", None, combination(),
                           LocalResearchWorkspaceConfig(store.blobs.root, registry.path))
    exports = LocalModelExport(config)
    calls = []

    class AdmissionLeaf:
        def verified_training_receipt(self, selected_store, datasets, operation):
            assert selected_store.blobs.root == store.blobs.root
            calls.append(("verify", datasets, operation))
            return operation * 2

        def require_training_receipt(self, selected_store, datasets, operation, identity):
            assert selected_store.blobs.root == store.blobs.root and identity == operation * 2
            calls.append(("require", datasets, operation))

    monkeypatch.setattr(exports, "_memory_owner", lambda _: AdmissionLeaf())
    for _, models in stages:
        for model in models:
            exports.start(model.artifact_id)
            assert exports.thread is not None
            exports.thread.join(timeout=30)
            assert not exports.thread.is_alive()
            done = exports.status()["operation"]
            assert done["status"] == "completed", done
            assert done["model_id"] == model.artifact_id
            assert exports._read()["verified_receipt"]["model_id"] == model.artifact_id
    assert len(list((state / "model-export-receipts").iterdir())) == 9
    models_service = LocalModelService(config)
    monkeypatch.setattr(registration_module, "validate_runtime_install", lambda *_: {})
    registration = LocalModelRegistration(config, exports, models_service)
    monkeypatch.setattr(registration, "_capabilities", lambda *_args, **_kwargs: capabilities())
    check_runtime = registration._m2_runtime_manifest_compatible
    monkeypatch.setattr(registration, "_m2_runtime_manifest_compatible",
                        lambda _modules, manifest, *, deadline: check_runtime(
                            ROOT.parent / "node_modules", manifest, deadline=deadline))
    selections = []
    for _, models in stages:
        for model in models:
            identity = model.artifact_id
            assert exports.status_for_model(identity)["operation"]["model_id"] == identity
            result = registration.register(identity)
            assert result["status"] == "registered" and result["loaded"] is False
            assert result["runtime_profile"] == "public-snapshot-m2-v1"
            selections.append(result["selection_id"])
            assert registration.register(identity)["selection_id"] == result["selection_id"]
            assert validate_package(state / EXPORT_ROOT / identity)[0].artifact_id == identity
    assert len(set(selections)) == 9
    assert len(models_service.registry()["policies"]) >= 9
    assert calls and {event[0] for event in calls} == {"verify", "require"}


def test_real_cpu_child_runtime_http_and_workbench_segment_owner(stages, tmp_path, monkeypatch):
    """Actual numerical child, Node Runtime, disk Evidence and WB HTTP consumer.

    The Connector leaf supplies synthetic public observations and refuses every
    submission. This validates transport/state ownership, never game acceptance.
    """
    from test_public_inputs import snapshot
    from test_public_m0_policy_port import capabilities

    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.local_models import (
        LocalModelService,
        RuntimeClient,
        RuntimeControlBinding,
    )

    store, models = stages[0]
    _, config_path, manifest_path, _ = _install(store, models[0], tmp_path)
    fixture = tmp_path / "fixture.json"
    fixture.write_bytes(json_bytes({"capabilities": capabilities(), "snapshot": snapshot()}))
    (tmp_path / "evidence").mkdir()
    script = tmp_path / "runtime-fixture.mjs"
    runtime_url = (ROOT.parent / "components/policy-runtime/dist/index.js").as_uri()
    script.write_text(r"""
import {readFile} from 'node:fs/promises';
import {PolicyRuntime, NdjsonPolicyPort, AgentRunEvidence, startPolicyRuntimeHttpServer,
  validatePolicyManifest, POLICY_RUNTIME_VERSION} from 'RUNTIME_URL';
const [python,configPath,manifestPath,bindingRoot,fixturePath] = process.argv.slice(2);
const manifest=validatePolicyManifest(JSON.parse(await readFile(manifestPath,'utf8')));
const fixture=JSON.parse(await readFile(fixturePath,'utf8'));
const cap=fixture.capabilities;
const port=NdjsonPolicyPort.spawn(python,['-I','-m','stpd.policy.public_m2_cli','serve',
  '--config',configPath,'--manifest',manifestPath,'--binding-root',bindingRoot]);
const evidence=await AgentRunEvidence.create({root:bindingRoot+'/evidence',
  policyManifest:manifest,runtimeVersion:POLICY_RUNTIME_VERSION,
  runtimeCodeSha256:'d'.repeat(64),mode:'human'});
await evidence.attestAdapter(await port.attest(manifest.adapter));
let sequence=0;
const connector={capabilities:async()=>cap, observeBundle:async()=>{
  const observation={...fixture.snapshot,snapshot_id:'wire-'+(++sequence),sequence,
    session:{runtime_instance_id:cap.host.runtime_instance_id,
      environment_fingerprint:cap.environment_fingerprint}};
  return {observation,reads:[]};}, acquireController:async()=>{},releaseController:async()=>{},
  submit:async()=>{throw new Error('synthetic connector forbids native submissions');}};
const runtime=new PolicyRuntime({manifest,connector,evidence,runId:evidence.runId,
  runtimeIdentity:{version:POLICY_RUNTIME_VERSION,code_sha256:'d'.repeat(64)},
  publicStatefulPolicy:(decision,control,signal,onOffer)=>port.decideV4(decision,control,signal,onOffer),
  statefulOfferBoundary:'port_write'});
let service;
service=await startPolicyRuntimeHttpServer(runtime,{port:0,autoDrive:false,
  onStopped:async()=>{await service.close();port.close();}});
process.stdout.write(JSON.stringify({address:service.address,run_id:evidence.runId,
  manifest_id:manifest.manifest_id,policy_artifact_sha256:manifest.artifact.sha256,
  runtime_version:POLICY_RUNTIME_VERSION,runtime_code_sha256:'d'.repeat(64)})+'\n');
process.on('SIGTERM',async()=>{await runtime.stop();await service.close();port.close();});
""".replace("RUNTIME_URL", runtime_url))
    error_log = (tmp_path / "runtime.stderr").open("wb")
    process = subprocess.Popen([shutil.which("node") or "node", str(script), sys.executable,
                                str(config_path), str(manifest_path), str(tmp_path), str(fixture)],
                               stdout=subprocess.PIPE, stderr=error_log)
    try:
        import queue
        import threading

        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
        startup_line = lines.get(timeout=30)
        assert startup_line, (tmp_path / "runtime.stderr").read_text()
        wire_startup = json.loads(startup_line)
        client = RuntimeClient(wire_startup["address"], wire_startup)
        wb = LocalModelService(ProjectConfig(tmp_path / "state", "", "", None, combination()))
        wb.client, wb.process = client, process
        wb.state.update(status="loaded", loaded=True, selection_id="synthetic-public-m2")
        monkeypatch.setattr(wb, "selection", lambda _: {"runtime_profile":"public-snapshot-m2-v1"})

        def command(action):
            wb.command(action)
            wb.thread.join(timeout=10)
            assert not wb.thread.is_alive()
            value = wb.status()
            assert value["operation"]["status"] == "completed", value
            return value

        assert wb.status()["public_stateful_segment"] is None
        begun = command("begin_segment")["public_stateful_segment"]
        assert set(begun) == {"scope", "episode_id", "segment_id"}
        before = client.request("/environment")
        binding = RuntimeControlBinding.from_environment(before, wire_startup["run_id"])
        client.request("/mode", {"mode":"shadow"}, binding=binding)
        for _ in range(2):
            result = client.request("/tick", {"max_ticks":1}, binding=binding)
            assert result["results"][0]["type"] == "shadow", result
        client.request("/mode", {"mode":"human"})
        assert wb.status()["public_stateful_segment"] is None
        fresh = command("begin_segment")["public_stateful_segment"]
        assert fresh["episode_id"] != begun["episode_id"]
        assert command("end_segment")["public_stateful_segment"] is None
        client.request("/stop", {})
        process.wait(timeout=10)
        assert process.returncode == 0, (tmp_path / "runtime.stderr").read_text()
        events = [json.loads(line) for path in (tmp_path / "evidence").glob("*/events.jsonl")
                  for line in path.read_text().splitlines()]
        assert sum(e["kind"] == "public_stateful_decision_input" for e in events) == 2
        assert not any(e["kind"] == "action_submitted" for e in events)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        error_log.close()


def test_public_m2_requires_runtime_control_capability_and_safe_status(stages, tmp_path):
    from time import monotonic

    from test_local_models import startup, status

    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.local_model_export import LocalModelExport
    from spireagent.workbench.local_model_registration import LocalModelRegistration
    from spireagent.workbench.local_models import LocalModelService, RuntimeClient

    store, models = stages[0]
    _, _, manifest, _ = _install(store, models[0], tmp_path / "binding")
    old_modules = tmp_path / "old/node_modules"
    package = old_modules / "@rsgcsg/sts2-policy-runtime"
    (package / "dist").mkdir(parents=True)
    (package / "package.json").write_text('{"type":"module"}')
    (package / "dist/index.js").write_text("export const validatePolicyManifest=x=>x;\n")
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    registration = LocalModelRegistration(
        config, LocalModelExport(config), LocalModelService(config),
    )
    with pytest.raises(BoundaryError, match="m2_runtime_contract_unavailable"):
        registration._m2_runtime_manifest_compatible(old_modules, manifest,
                                                     deadline=monotonic() + 10)
    client = RuntimeClient("http://127.0.0.1:12345", startup())
    client.validate_status(status())  # Old M0 may omit the new view.
    for segment in (None, {"scope":"bounded_policy_segment", "episode_id":"e", "segment_id":"s"}):
        client.validate_status({**status(), "public_stateful_segment":segment})
    with pytest.raises(ValueError):
        client.validate_status({**status(), "public_stateful_segment":{
            "scope":"bounded_policy_segment", "episode_id":"e", "segment_id":"s",
            "continuity_token":"must-not-project"}})
