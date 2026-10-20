"""P1 engineering allocation and actual existing local service/private child."""
from __future__ import annotations

import uuid

import pytest
import torch
from test_p1_snapshot import explicit_snapshot, projection_fixture

from spireagent.json_boundary import BoundaryError
from spireagent.source import source_identity
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ROOT, ProjectConfig, combination
from spireagent.workbench.managed_local_workspace import ROOT_NAME, create_managed_workspace
from spireagent.workbench.p1_training_data import (
    record_p1_training_use,
    require_p1_snapshot,
    reserve_p1_snapshot,
)
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.trusted_recipes import describe_recipe, validate_recipe_config
from stpd.p1.dataset import iter_snapshot_points, load_snapshot
from stpd.p1.export import load_model
from stpd.p1.input import project
from stpd.p1.spec import RECIPE, MStdConfig
from stpd.p1.workload import verify_p1_run
from stpd.workers.p1_run import materialize_p1_model, verify_p1_result


def workspace(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    selected = create_managed_workspace(state)
    directory = state / ROOT_NAME / selected["workspace_id"]
    owner = selected["curation_owner"]
    store = ManifestArtifactStore(LocalBlobStore(directory / "store", create=False))
    projections = [projection_fixture(store, "application-" + str(i))[0] for i in range(3)]
    snapshot = explicit_snapshot(store, projections)
    config = ProjectConfig(state, "", "", None, combination())
    return config, store, owner, snapshot


def test_engineering_purposes_and_use_do_not_label_dev_test_as_gradient_training(tmp_path):
    _, store, owner, snapshot = workspace(tmp_path)
    with pytest.raises(BoundaryError, match="purpose_reservation_required"):
        require_p1_snapshot(owner, store, snapshot.artifact_id)
    reserved = reserve_p1_snapshot(owner, store, snapshot.artifact_id)
    assert reserved == reserve_p1_snapshot(owner, store, snapshot.artifact_id)
    record_p1_training_use(owner, store, snapshot.artifact_id, "c" * 32)
    train = set(reserved["partitions"]["train"]["root_run_ids"])
    with owner.transaction() as db:
        used = {r[0] for r in db.execute("SELECT run FROM curation_uses WHERE kind='training'")}
    assert used == train
    assert owner.ledger.dataset(snapshot.artifact_id) == ("training", train)
    assert validate_recipe_config(RECIPE, {"max_epochs": 1})["device"] == "cpu"
    with pytest.raises(BoundaryError, match="requires_cpu"):
        validate_recipe_config(RECIPE, {"device": "cuda:0"})
    assert describe_recipe(RECIPE)["automatic_retry"] is False


def test_prior_training_exposure_or_cross_purpose_overlap_rejects_heldout(tmp_path):
    _, store, owner, snapshot = workspace(tmp_path)
    _, value = load_snapshot(store, snapshot.artifact_id)
    dev = {p["root_run_id"] for p in value["points"] if p["split"] == "dev"}
    owner.ledger.use(dev, "training", "previous-training")
    with pytest.raises(BoundaryError, match="held_out_run_has_training_exposure"):
        reserve_p1_snapshot(owner, store, snapshot.artifact_id)


def test_real_service_child_store_result_model_export_and_score(tmp_path):
    from spireagent.workbench.local_training import LocalTrainingService

    config, store, owner, snapshot = workspace(tmp_path)
    reserve_p1_snapshot(owner, store, snapshot.artifact_id)
    producer = source_identity(ROOT)
    request = TrainingRequest(uuid.uuid4().hex, RECIPE, snapshot.artifact_id,
                              MStdConfig(max_epochs=2).to_dict(),
                              limits={"wall_seconds": 120, "scratch_bytes": 512 * 1024**2})
    service = LocalTrainingService(config)
    admitted = service.start(request)
    assert service._thread is not None
    service._thread.join(timeout=120)
    assert not service._thread.is_alive()
    operation = service.status(admitted["operation"]["operation_id"])["operation"]
    assert operation["status"] == "completed", operation
    assert operation["worker_state"] == "terminal"
    assert operation["progress"]["completed"] == 2
    workload = verify_p1_run(store, operation["run_id"], producer)
    result = ObjectStoreRunReporter(store, store.blobs).completed(operation["run_id"])
    assert result is not None
    _, model, checkpoint = verify_p1_result(store, result.artifact_id, workload)
    assert checkpoint.state["phase"] == "completed"
    export = tmp_path / "export"
    descriptor = materialize_p1_model(store, model.artifact_id, export)
    loaded, _ = load_model(export / "model.tensor-tree", descriptor["package_sha256"])
    reference, observation = next(iter_snapshot_points(store, snapshot.artifact_id))
    frame = project(observation)
    with torch.inference_mode():
        scores = loaded(frame)
    assert bool(torch.isfinite(scores).all()) and len(scores) == len(frame.choices)
    assert reference["chosen"] in {c["choice_id"] for c in frame.choices}
