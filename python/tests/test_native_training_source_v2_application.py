"""One actual private-child fit, own-byte export/registration and ordinary fresh stdio.

All original inputs and capability values are declared synthetic fixtures. This
does not launch a game/Host, qualify an installation or run a real-data fit.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from test_native_agent_sampled_source import ROOT, SHARED, publish
from test_native_agent_sampled_source import original as original
from test_protocol_source import setup_store

from spireagent.json_boundary import json_bytes
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_model_export import LocalModelExport
from spireagent.workbench.local_model_registration import LocalModelRegistration
from spireagent.workbench.local_models import LocalModelService
from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.native_agent_support import validate
from spireagent.workbench.recipe_contracts import TrainingRequest
from stpd.native_training_source_spec import MODEL_SCHEMA, PACKAGE_SCHEMA, RECIPE

SESSION_SCHEMA = "sts2.policy-runtime/agent-session-1"


def fixture_capabilities() -> dict:
    """Same declared synthetic environment as the existing SDK/Runtime fixture."""
    profile_bytes = (ROOT / "components/connector/contracts/"
                     "native-logical-publication-profile-v1.json").read_bytes()
    profile = json.loads(profile_bytes)
    caps = json.loads((ROOT / "components/connector/contracts/fixtures/"
                      "native-logical-v1.json").read_bytes())["wire_samples"]["capabilities"]
    environment = SHARED["manifest"]["requirements"]["environment"]
    caps["host"].update(host_kind=environment["host_kind"],
                         version=environment["connector_version"], implementation={
                             "source_revision": environment["connector_source_revision"],
                             "artifact_sha256": environment["connector_artifact_sha256"],
                             "module_version_id": environment["connector_module_version_id"]})
    caps["game"].update(version=SHARED["manifest"]["support"]["game_versions"][0],
                         commit=SHARED["manifest"]["support"]["game_commits"][0])
    caps["game"]["modset"].update(status=environment["modset_status"],
        fingerprint=environment["modset_fingerprint"],
        loaded_mod_ids=copy.deepcopy(environment["loaded_mod_ids"]))
    caps["supported_methods"] = copy.deepcopy(
        SHARED["manifest"]["requirements"]["required_methods"])
    caps["capture_coverage"] = copy.deepcopy(profile["required_seams"])
    return {"capabilities": caps, "publication_profile": profile,
            "publication_profile_sha256": hashlib.sha256(profile_bytes).hexdigest()}


def fresh_stdio(folder: Path, manifest_path: Path) -> dict:
    """Launch the ordinary production module with the same unchanged environment."""
    command = [sys.executable, "-B", "-m", "stpd.policy.native_agent", "--package", str(folder),
               "--manifest", str(manifest_path)]
    child = subprocess.Popen(command, cwd=ROOT / "python", env=dict(os.environ),
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    assert child.stdin is not None and child.stdout is not None and child.stderr is not None
    received: queue.Queue[str] = queue.Queue()

    def drain():
        assert child.stdout is not None
        for line in child.stdout:
            received.put(line)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    def read():
        return json.loads(received.get(timeout=10))

    def send(kind, identity, value):
        field = "completion" if kind == "consume_ack" else "result" if kind == "query_result" \
            else "input"
        assert child.stdin is not None
        child.stdin.write(json_bytes({"schema": SESSION_SCHEMA, "message_type": kind,
            "session_id": "common-source-ordinary-stdio", "recovery_epoch": 0,
            "request_id": identity, field: value}).decode())
        child.stdin.flush()

    prefix = {"continuity_token": "segment-1", "consumption_id": None, "state_version": 0,
              "basis_acquisition_id": None, "received_cursor": "cursor"}
    advances, directives = [], []
    try:
        ready = read()
        assert ready["message_type"] == "ready"
        assert ready["adapter"] == json.loads(manifest_path.read_bytes())["adapter"]
        for serial, name in enumerate(("map_a", "map_a", "inspect_b", "map_c", "empty_wait",
                                       "ready_summary")):
            request = "next-" + str(serial)
            send("next", request, prefix)
            query = read()
            assert query["message_type"] == "query"
            assert query["input"] == {"method": "current", "arguments": {
                "eager_scope": ["persistent", "interaction", "referents", "catalog"],
                "expected_snapshot_id": None}}
            frame = SHARED["frames"][name]
            send("query_result", query["request_id"], {"method": "current",
                "acquisition_id": "sample-" + str(serial), "value": {
                    **{key: frame[key] for key in ("capture", "observation", "catalog")},
                    "catalog_materialized": True}})
            response = read()
            if response["message_type"] == "consumed":
                report = response["completion"]
                assert report["state_version"] == prefix["state_version"] + 1
                assert report["previous_consumption_id"] == prefix["consumption_id"]
                advances.append(name)
                completion = {key: report[key] for key in
                              ("consumption_id", "acquisition_id", "state_version", "advanced")}
                completion["prefix"] = {"continuity_token": "segment-1",
                    "history_mode": "sampled_current", "consumption_mode": "once_per_occurrence",
                    "received_cursor": "cursor", "consumed_publication_index": None,
                    "omissions": {"received_unconsumed_count": 0, "missing_scopes": [],
                                  "gap": None}}
                send("consume_ack", response["request_id"], completion)
                prefix.update({key: report[key] for key in ("consumption_id", "state_version")})
                prefix["basis_acquisition_id"] = report["acquisition_id"]
                response = read()
            assert response["message_type"] == "directive" and response["request_id"] == request
            directive = response["output"]["directive"]
            directives.append(directive["type"])
            if directive["type"] == "act":
                assert directive["selection"]["action_id"] in {
                    action["action_id"] for action in frame["catalog"]}
                assert len(directive["scores"]["values"]) == len(frame["catalog"])
            if directive["type"] == "close":
                assert name == "ready_summary"
                assert directive["reason"] == "native_ready_summary_task_complete"
        assert advances == ["map_a", "inspect_b", "map_c", "ready_summary"]
        assert directives == ["act", "await", "act", "act", "await", "close"]
        child.stdin.close()
        assert child.wait(timeout=10) == 0
        assert child.stderr.read() == ""
        reader.join(timeout=2)
        assert not reader.is_alive()
        return {"pid": child.pid, "command": command, "exit_code": child.returncode,
                "advanced_frames": advances, "directives": directives,
                "runtime_threads": "ordinary_process_not_exposed_by_ready_wire"}
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_actual_common_private_child_use_export_registry_and_ordinary_stdio(
    tmp_path, original, monkeypatch
):
    store, owner = setup_store(tmp_path)
    raw, admission, partition = publish(store, original)
    owner.reserve_verified_native_agent_sampled_source(store, partition.manifest.artifact_id)
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    service = LocalTrainingService(config)
    # The parent has no numerical setup or test-specific child launcher. The
    # production child records its actual default interop count under OMP/MKL2.
    service.start(TrainingRequest("1" * 32, RECIPE, partition.manifest.artifact_id,
                                 {"epochs": 1, "max_updates": 1},
                                 limits={"wall_seconds": 45}))
    assert service._thread is not None
    service._thread.join(timeout=50)
    operation = service.status()["operation"]
    assert not service._thread.is_alive()
    assert operation["status"] == "completed", operation
    journal = service._read(service._paths(owner)[0], owner.identity)
    assert operation["validation_state"] == "verified" and journal["child_handshake"]
    use = owner.require_verified_native_agent_sampled_training_use(
        store, partition.manifest.artifact_id, operation["operation_id"])
    runtime = store.get_manifest(operation["run_id"]).parameters.value()["execution_identity"][
        "runtime"]
    assert runtime["threads"] == 2 and type(runtime["interop_threads"]) is int
    # Numerical consumer imports happen after the genuine child result exists.
    from stpd.policy.native_structured_export import load_native_package

    export = LocalModelExport(config)
    export.start(operation["model_id"])
    assert export.thread is not None
    export.thread.join(timeout=20)
    assert not export.thread.is_alive() and export.status()["operation"]["status"] == "completed"
    folder = export.verified_for_registration(operation["model_id"])
    package, _ = load_native_package(folder)
    assert package["schema"] == PACKAGE_SCHEMA
    assert package["training"]["metrics"]["optimizer_updates"] == 1
    assert store.get_manifest(operation["model_id"]).parameters.value()["schema"] == MODEL_SCHEMA
    assert package["provenance"]["checkpoint_id"] == operation["checkpoint_id"]
    models = LocalModelService(config)
    registration = LocalModelRegistration(config, export, models)
    # Installed runtime/capability metadata are synthetic. Package own-byte load,
    # registration journal, binder, registry entry and adapter identity stay real.
    monkeypatch.setattr(registration, "_native_runtime", lambda **_: (Path("/test"), Path("/sdk")))
    monkeypatch.setattr(registration, "_capabilities", lambda *_, **__: fixture_capabilities())
    monkeypatch.setattr(models, "_public_manifest_contract", lambda *_, **__: None)
    try:
        registered = registration.register(operation["model_id"])
        assert registered["status"] == "registered" and not registered["loaded"]
        entry = models.selection(registered["selection_id"])
        manifest_path = models.entry_path(entry, "manifest")
        bound, manifest = validate(models.root, models.entry_path(entry, "config"), manifest_path,
                                   binding_root=models.private_root)
        assert bound["package_model_id"] == package["model_id"] != operation["model_id"]
        assert manifest["agent"]["version"] == "1.1.0"
        assert models.readiness(entry["id"])["checks"]["policy_identity"] == {"status": "pass"}
        stdio = fresh_stdio(folder, manifest_path)
        receipt = {"scope": "synthetic_package_conformance_only", "optimizer_updates": 1,
            "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"],
                cwd=ROOT, text=True).strip(), "source_raw_id": raw.artifact_id,
            "admission_id": admission.admission_id,
            "source_partition_id": partition.manifest.artifact_id,
            "model_artifact_id": operation["model_id"], "package_model_id": package["model_id"],
            "checkpoint_id": operation["checkpoint_id"], "input_spec": package["input_spec"],
            "weights_sha256": package["weights"]["sha256"],
            "adapter_code_sha256": manifest["adapter"]["code_sha256"],
            "package_manifest_sha256": manifest["artifact"]["sha256"],
            "publication_profile_sha256": bound["publication_profile"]["definition_sha256"],
            "required_seams": len(manifest["input"]["attachment"]["required_seams"]),
            "intra_threads": runtime["threads"], "interop_threads": runtime["interop_threads"],
            "tbptt_advances": 4, "actual_child_runtime": runtime,
            "use_receipt": use, "operation": operation, "registration": registered,
            "ordinary_fresh_stdio": stdio,
            "installed_runtime_or_native_game": False}
        destination = Path(os.environ.get("E6_NATIVE_TRAINING_SOURCE_V2_PACKAGE_ROOT",
                                          str(tmp_path / "closed-package")))
        destination.mkdir(parents=True, exist_ok=False)
        shutil.copytree(folder, destination / "package")
        shutil.copyfile(manifest_path, destination / "agent.json")
        (destination / "receipt.json").write_bytes(json_bytes(receipt))
        assert hashlib.sha256((destination / "package/model.json").read_bytes()).hexdigest() == (
            receipt["package_manifest_sha256"])
    finally:
        models.close()


def test_explicit_policy_first_model_private_child_publication_pause_resume_reconcile(
    tmp_path, original, monkeypatch,
):
    from test_training_service_contracts import child_script

    from spireagent.artifact_contracts import Manifest, Parent
    from spireagent.json_boundary import BoundaryError, FrozenObject
    from stpd.policy.native_operational_outcome import owned_current_known_stale_policy

    store, owner = setup_store(tmp_path)
    _raw, _admission, partition = publish(store, original)
    owner.reserve_verified_native_agent_sampled_source(store, partition.manifest.artifact_id)
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    service = LocalTrainingService(config)
    policy = owned_current_known_stale_policy()
    paused_boundary = []

    def pause_at_publication(raw, callback):
        callback(raw)
        message = json.loads(raw)
        if message["kind"] != "event":
            return
        event = store.get_manifest(message["details"]["event_id"])
        info = event.parameters.value()
        if (info["kind"] == "checkpoint" and info["details"]["phase"] == "publication"
                and not paused_boundary):
            paused_boundary.append(info["details"]["checkpoint_id"])
            service.pause(message["operation_id"], message["attempt_id"])

    # Synchronize only the real publication/control boundary. Numerical engine,
    # typed Source/use, parent ACK, Store/Reporter and private lifecycle stay real.
    child_script(monkeypatch, """
import time
original_emit = child.ChildReporter.emit
def emit(self, event):
    identity = original_emit(self, event)
    info = event.parameters.value()
    if (info['kind'] == 'checkpoint' and info['details']['phase'] == 'publication'
            and self.fence.operation()['mode'] == 'start'):
        deadline = time.monotonic()+5
        while self.fence.operation()['requested_action'] == 'continue':
            if time.monotonic() >= deadline:
                raise AssertionError('publication pause synchronization timeout')
            time.sleep(0.005)
    return identity
child.ChildReporter.emit = emit
""", on_line=pause_at_publication)
    request = TrainingRequest("1" * 32, RECIPE, partition.manifest.artifact_id,
                              {"epochs": 1, "max_updates": 1},
                              limits={"wall_seconds": 60}, execution_policy=policy)
    service.start(request)

    def settle():
        assert service._thread is not None
        service._thread.join(timeout=65)
        assert not service._thread.is_alive()
        return service.status()["operation"]

    paused = settle()
    assert paused["status"] == "paused" and paused_boundary, paused
    checkpoint = store.get_manifest(paused["checkpoint_id"])
    assert checkpoint.parameters.value()["phase"] == "publication"
    assert checkpoint.parameters.value()["optimizer_updates"] == 1
    run = store.get_manifest(paused["run_id"])
    info = run.parameters.value()
    assert info["execution_policy"] == paused["execution_policy"] == policy
    assert "execution_policy" not in info["config"]
    assert "execution_policy" not in info["execution_identity"]
    assert not any(store.get_manifest(identity).kind == "model"
                   for identity in store.manifest_ids())
    service.resume(paused["operation_id"], paused["attempt_id"], paused["checkpoint_id"],
                   "2" * 32, paused["limits"])
    completed = settle()
    assert completed["status"] == "completed", completed
    assert completed["run_id"] == paused["run_id"]
    assert completed["execution_policy"] == policy
    assert completed["progress"]["completed"] == 1
    model = store.get_manifest(completed["model_id"])
    assert model.parameters.value()["agent_spec"]["version"] == "1.2.0"
    assert model.parameters.value()["agent_spec"]["execution_policy"] == policy
    result = store.get_manifest(completed["result_id"])
    report = json.loads(b"".join(store.read_payload(result.payload("report"))))
    assert report["execution_policy"] == policy
    assert report["metrics"]["optimizer_updates"] == 1
    before = set(store.manifest_ids())
    service.reconcile(completed["operation_id"], completed["attempt_id"])
    reconciled = settle()
    assert reconciled["status"] == "completed", reconciled
    assert reconciled["result_id"] == completed["result_id"]
    assert reconciled["model_id"] == completed["model_id"]
    assert reconciled["execution_policy"] == policy
    assert set(store.manifest_ids()) == before

    # Preserve original immutable results. New tampered fixtures use the exact
    # same weights/checkpoint but a different deployment policy declaration.
    from stpd.canonical import semantic_hash
    from stpd.models.structured_training import StructuredTrainingConfig
    from stpd.native_graph_spec import NativeGraphControl
    from stpd.native_sampled_carry_spec import sampled_agent_spec
    from stpd.policy.native_structured_export import (
        export_native_model,
        load_native_package,
        native_model_parameters,
    )
    from stpd.workers.structured_execution import _completed

    folder = tmp_path / "policy-package"
    export_native_model(store, model.artifact_id, folder)
    package, _model = load_native_package(folder)
    assert package["agent_spec"]["execution_policy"] == policy
    altered_policy = {**policy, "max_known_stale_rejections": 7}
    altered = copy.deepcopy(package)
    altered["agent_spec"] = sampled_agent_spec(
        NativeGraphControl(), execution_policy=altered_policy)
    altered["model_id"] = semantic_hash(
        {key: value for key, value in altered.items() if key != "model_id"})
    manifest = store.put_bytes("package_manifest", json_bytes(altered), "application/json")
    forged_model = Manifest("model", model.producer, model.parents,
                            (manifest, model.payload("weights")),
                            FrozenObject.of(native_model_parameters(altered, report["attempt"])))
    store.publish(forged_model)
    forged_result = Manifest(
        "run_result", result.producer,
        tuple(Parent(parent.role, forged_model.artifact_id if parent.role == "model"
                     else parent.artifact_id) for parent in result.parents),
        result.payloads, result.parameters,
    )
    training = store.get_manifest(run.parent("training_input"))
    numeric_config = StructuredTrainingConfig(**info["config"])
    with pytest.raises(BoundaryError, match="package_execution_policy_mismatch"):
        _completed(store, forged_result, run, training, partition.dataset, numeric_config)
    altered_report = {**report, "execution_policy": altered_policy}
    report_payload = store.put_bytes("report", json_bytes(altered_report), "application/json")
    forged_report = Manifest("run_result", result.producer, result.parents,
                             (report_payload,), result.parameters)
    with pytest.raises(BoundaryError, match="completed_report_binding_mismatch"):
        _completed(store, forged_report, run, training, partition.dataset, numeric_config)
