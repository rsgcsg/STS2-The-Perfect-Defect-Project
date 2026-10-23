"""One real saved step -> original verifier -> diagnostic Model and normal CLI."""
from __future__ import annotations

import copy
import json
import threading
from dataclasses import replace

import pytest
import torch
from safetensors.torch import load as load_tensors
from safetensors.torch import save as save_tensors
from test_artifact_store_v1 import PRODUCER
from test_p1_workload import small_run

from spireagent.json_boundary import BoundaryError
from spireagent.storage.blobs import StoreError
from spireagent.storage.registry import SQLiteRegistry
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import (
    LocalResearchWorkspaceConfig,
    ProjectConfig,
    atomic_json,
    combination,
)
from spireagent.workbench.developer_cli import main as project_main
from spireagent.workbench.developer_server import Application, configuration_id, create_server
from stpd.p1.checkpoint_export import publish_checkpoint_model
from stpd.p1.dataset import SnapshotReader
from stpd.p1.execution import MStdExecution
from stpd.p1.export import load_model
from stpd.p1.formal_training import MStdTrainer
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.p1_run import execute_p1_run, materialize_p1_model

EXPORTER = replace(PRODUCER, source_revision="f" * 40)


@pytest.fixture(scope="module", params=[None, "experimental-packed-v1"])
def saved_checkpoint(tmp_path_factory, request):
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(2)
    root = tmp_path_factory.mktemp("diagnostic-checkpoint")
    policy = (MStdExecution(implementation=request.param).to_dict() if request.param else None)
    store, snapshot, run = small_run(root, policy)
    reporter = ObjectStoreRunReporter(store, store.blobs)
    result = execute_p1_run(store, reporter, run.artifact_id, PRODUCER,
                            attempt_id="one-step", stop_after=1)
    assert result.state == "paused"
    checkpoint = store.get_manifest(result.checkpoint_id)
    raw = b"".join(store.read_payload(checkpoint.payload("checkpoint")))
    state = decode_checkpoint(raw)
    assert state["step"] == state["best_step"] == 1
    assert state["test_result"] is None
    yield root, store, snapshot, run, checkpoint, state
    torch.set_num_threads(previous_threads)


def no_population_or_evaluation(monkeypatch):
    def prohibited(*_args, **_kwargs):
        pytest.fail("diagnostic export must not construct populations or evaluate TEST")
    monkeypatch.setattr(SnapshotReader, "iter_points", prohibited)
    monkeypatch.setattr(MStdTrainer, "evaluate", prohibited)
    monkeypatch.setattr(MStdTrainer, "finish", prohibited)


@pytest.mark.parametrize("selection", ["best", "current"])
def test_real_checkpoint_export_preserves_training_identity_rng_and_originals(
    saved_checkpoint, tmp_path, monkeypatch, selection,
):
    _, store, snapshot, run, checkpoint, state = saved_checkpoint
    no_population_or_evaluation(monkeypatch)
    before = store.manifest_ids()
    rng = torch.get_rng_state().clone()
    result = publish_checkpoint_model(store, checkpoint.artifact_id, EXPORTER, selection=selection)
    assert torch.equal(torch.get_rng_state(), rng)
    model = store.get_manifest(result["model_id"])
    assert model.producer == EXPORTER and model.producer != run.producer
    assert model.parent("run") == run.artifact_id
    assert model.parent("training_input") == run.parent("training_input")
    assert model.parent("checkpoint") == checkpoint.artifact_id
    assert result["selected_step"] == 1
    assert result["training_producer"] == run.producer.to_dict()
    assert result["export_producer"] == EXPORTER.to_dict()
    assert result["test_access"] == "integrity_verification_only_no_population_or_evaluation"
    assert model.parameters.value()["export_role"] == (
        "diagnostic_checkpoint_inference_not_final_result")
    assert store.get_manifest(checkpoint.artifact_id) == checkpoint
    added = set(store.manifest_ids()) - set(before)
    assert {store.get_manifest(i).kind for i in added} <= {"model"}
    descriptor = materialize_p1_model(store, model.artifact_id, tmp_path / "package")
    loaded, package = load_model(tmp_path / "package/model.tensor-tree",
                                 descriptor["package_sha256"])
    assert loaded.execution.to_dict() == state["identity"]["execution"]
    assert package["training_identity"] == state["identity"]
    assert package["training_identity"]["snapshot_id"] == snapshot.artifact_id
    assert package["checkpoint_sha256"] == checkpoint.payload("checkpoint").sha256
    chosen = state["best_model"] if selection == "best" else state["model"]
    assert all(torch.equal(tensor, chosen[name]) for name, tensor in loaded.state_dict().items())
    assert publish_checkpoint_model(store, checkpoint.artifact_id, EXPORTER,
                                    selection=selection)["model_id"] == model.artifact_id


def altered_checkpoint(store, original, state):
    payload = store.put_bytes("checkpoint", encode_checkpoint(state))
    changed = replace(original, payloads=(payload,))
    store.publish(changed)
    return changed


@pytest.mark.parametrize("fault,code", [
    ("foreign", "checkpoint_run_binding_mismatch"),
    ("code", "checkpoint_code_or_architecture_mismatch"),
    ("shape", "checkpoint_tensor_binding_mismatch"),
    ("dtype", "checkpoint_tensor_binding_mismatch"),
    ("best_none", "selected_checkpoint_weights_unavailable"),
])
def test_foreign_code_shape_dtype_and_unselected_best_reject(
    saved_checkpoint, monkeypatch, fault, code,
):
    _, store, _, _, checkpoint, full = saved_checkpoint
    no_population_or_evaluation(monkeypatch)
    # A malformed candidate needs no optimizer/moments or full weights to falsify
    # the admission. Retain the original normal verifier's required envelope.
    state = {k: copy.deepcopy(full[k]) for k in ("schema", "identity", "step", "phase")}
    state.update(model={}, best_model={}, best_step=1)
    first, tensor = next(iter(full["model"].items()))
    if fault == "code":
        state["identity"]["code"]["model.py"] = "0" * 64
    elif fault == "shape":
        state["model"] = {first: torch.zeros(1, dtype=tensor.dtype)}
    elif fault == "dtype":
        state["model"] = {first: torch.zeros(tensor.shape, dtype=torch.float64)}
    elif fault == "best_none":
        state.update(best_model=None, best_step=None)
    changed = altered_checkpoint(store, checkpoint, state)
    if fault == "foreign":
        changed = replace(changed, producer=EXPORTER)
        store.publish(changed)
    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match=code):
        publish_checkpoint_model(store, changed.artifact_id, EXPORTER,
                                  selection="best" if fault == "best_none" else "current")
    assert store.manifest_ids() == before


def test_corrupt_payload_and_nonfinite_tensor_reject_before_any_model_write(
    saved_checkpoint, monkeypatch,
):
    _, store, _, _, checkpoint, full = saved_checkpoint
    no_population_or_evaluation(monkeypatch)
    index = json.loads(store.blobs.get(
        "payload-indexes/v1/" + checkpoint.payload("checkpoint").sha256 + ".json"))
    corrupt_key = "objects/sha256/" + index["chunks"][0]["sha256"]

    class CorruptTransport:
        def get(self, key):
            raw = store.blobs.get(key)
            return bytes([raw[0] ^ 1]) + raw[1:] if key == corrupt_key else raw
        def keys(self, prefix):
            return store.blobs.keys(prefix)
        def put_if_absent(self, *_args):
            pytest.fail("corrupt checkpoint cannot write a Model")

    with pytest.raises(StoreError, match="payload_chunk_integrity_failure"):
        publish_checkpoint_model(ManifestArtifactStore(CorruptTransport()),
                                  checkpoint.artifact_id, EXPORTER)
    # safetensors can encode NaN, whereas the normal checkpoint codec refuses it.
    # Build adversarial bytes below that producer guard to test the real reader.
    state = {k: full[k] for k in ("schema", "identity", "step", "phase")}
    state.update(model={"invalid": torch.zeros(1)}, best_model=None, best_step=None)
    raw = encode_checkpoint(state)
    offset = 16 + int.from_bytes(raw[8:16], "little")
    tensors = load_tensors(raw[offset:])
    tensors[next(iter(tensors))].fill_(float("nan"))
    payload = store.put_bytes("checkpoint", raw[:offset] + save_tensors(tensors))
    poisoned = replace(checkpoint, payloads=(payload,))
    store.publish(poisoned)
    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match="non_finite_tensor"):
        publish_checkpoint_model(store, poisoned.artifact_id, EXPORTER, selection="current")
    assert store.manifest_ids() == before


def test_original_cli_application_export_and_own_byte_download(
    saved_checkpoint, tmp_path, capsys, monkeypatch,
):
    _, store, _, _, checkpoint, _ = saved_checkpoint
    registry = tmp_path / "registry.sqlite"
    SQLiteRegistry(registry)
    config = ProjectConfig(tmp_path / "state", "", "", None, combination(),
        LocalResearchWorkspaceConfig(store.blobs.root, registry))
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    def unavailable_game():
        raise AssertionError("Pure checkpoint publication must not check a running game")
    monkeypatch.setattr(app, "check_environment_instance", unavailable_game)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    atomic_json(config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
        "port": server.server_port, "control_token": app.control_token})
    thread.start()
    try:
        assert project_main(["model", "--config", str(config_path), "--action", "export-checkpoint",
            "--artifact", checkpoint.artifact_id, "--checkpoint-selection", "best"]) == 0
        started = json.loads(capsys.readouterr().out)
        assert started["operation"]["action"] == "export-checkpoint"
        app.models.thread.join(timeout=15)
        assert not app.models.thread.is_alive()
        assert project_main(["model", "--config", str(config_path), "--action", "status"]) == 0
        status = json.loads(capsys.readouterr().out)
        assert status["operation"]["status"] == "completed", status
        model_id = status["last_checkpoint_export"]["model_id"]
        assert project_main(["model", "--config", str(config_path), "--action", "download",
            "--artifact", model_id, "--download-source", "store"]) == 0
        capsys.readouterr()
        app.models.thread.join(timeout=10)
        assert app.models.status()["last_download"]["parents_downloaded"] is False
        assert (config.state_dir / "downloads" / model_id / "download.json").is_file()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()
