"""Real Store/ComputeRequest/worker/checkpoint/export/readback over a small N population."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
import torch
from test_artifact_store_v1 import PRODUCER
from test_p1_snapshot import explicit_snapshot, projection_fixture, store_at

from spireagent.json_boundary import BoundaryError
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import copy_artifact
from stpd.cloud_jobs.contracts import ComputeRequest
from stpd.cloud_jobs.execution import dispatch, preflight_training_request, validate_receipt
from stpd.p1.dataset import iter_snapshot_points
from stpd.p1.export import load_model
from stpd.p1.input import project
from stpd.p1.spec import MStdConfig
from stpd.p1.training import maximum_difference
from stpd.p1.workload import prepare_p1_run, verify_p1_run
from stpd.workers.p1_run import materialize_p1_model, verify_p1_checkpoint, verify_p1_result


def small_run(tmp_path):
    store = store_at(tmp_path / "store")
    projections = [projection_fixture(store, "source-" + str(i))[0] for i in range(3)]
    snapshot = explicit_snapshot(store, projections)
    run = prepare_p1_run(store, snapshot.artifact_id, PRODUCER, MStdConfig(max_epochs=2), "a" * 32)
    return store, snapshot, run


def test_same_worker_full_pause_resume_candidate_and_exact_model_readback(tmp_path):
    store, snapshot, run = small_run(tmp_path)
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "full")
    preflight_training_request(store, request)
    full = dispatch(store, request, PRODUCER)
    validate_receipt(store, request, full)
    assert full.state == "candidate_prepared" and full.output_id
    # A cloud worker candidate does not acquire the Hub's selected-result slot.
    assert ObjectStoreRunReporter(store, store.blobs).completed(run.artifact_id) is None
    workload = verify_p1_run(store, run.artifact_id, PRODUCER)
    complete = verify_p1_checkpoint(store, full.checkpoint_id, workload)
    paused_request = replace(request, attempt_id="bounded-pause", stop_after=1)
    paused = dispatch(store, paused_request, PRODUCER)
    validate_receipt(store, paused_request, paused)
    assert paused.state == "paused" and paused.output_id is None
    resumed_request = replace(
        request, attempt_id="explicit-replacement", resume=paused.checkpoint_id
    )
    resumed = dispatch(store, resumed_request, PRODUCER)
    validate_receipt(store, resumed_request, resumed)
    restored = verify_p1_checkpoint(store, resumed.checkpoint_id, workload)
    assert maximum_difference(complete.state, restored.state) == 0.0
    result, model, _ = verify_p1_result(store, resumed.output_id, workload)
    assert result.parameters.value()["scientific_verdict"] == "not_claimed"
    assert set(result.parameters.value()["checkpoint_ids"]) == {
        "5",
        "10",
        "20",
        "40",
        "60",
        "80",
        "100",
    }
    assert model.parameters.value()["selected_state"] == "best_model"
    readback = store_at(tmp_path / "readback")
    assert copy_artifact(store, readback, result.artifact_id) == result.artifact_id
    assert copy_artifact(store, readback, result.artifact_id) == result.artifact_id
    verify_p1_result(readback, result.artifact_id, workload)
    destination = tmp_path / "export"
    metadata = materialize_p1_model(readback, model.artifact_id, destination)
    loaded, package = load_model(destination / "model.tensor-tree", metadata["package_sha256"])
    row = next(iter_snapshot_points(readback, snapshot.artifact_id))
    frame = project(row[1])
    with torch.inference_mode():
        scores = loaded(frame)
    assert scores.shape == (len(frame.choices),) and bool(torch.isfinite(scores).all())
    assert frame.choices[int(scores.argmax())]["choice_id"] in {
        c["choice_id"] for c in frame.choices
    }
    assert package["training_identity"]["snapshot_id"] == snapshot.artifact_id
    assert (
        json.loads((destination / "model.json").read_bytes())["input_spec"]
        == metadata["input_spec"]
    )
    with pytest.raises(BoundaryError, match="request_binding"):
        validate_receipt(store, request, resumed)


def test_preparation_fits_vocabulary_from_train_only_and_source_lock_is_preserved(tmp_path):
    store, snapshot, run = small_run(tmp_path)
    workload = verify_p1_run(store, run.artifact_id, PRODUCER)
    assert workload.training.parameters.value()["purpose"] == "engineering"
    assert workload.training.parameters.value()["research_admission"] == "not_evaluated"
    assert workload.training.parent("source") == snapshot.artifact_id
    assert all("/Users/" not in str(v) for v in workload.training.parameters.value().values())
    with pytest.raises(BoundaryError, match="run_source_or_schema"):
        verify_p1_run(store, run.artifact_id, replace(PRODUCER, source_revision="f" * 40))
    with pytest.raises(BoundaryError, match="explicit_unique_projections"):
        from stpd.p1.dataset import publish_snapshot

        publish_snapshot(store, [], PRODUCER, split_salt="no-source")
