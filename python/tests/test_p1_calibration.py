"""Verified P1 Run -> TRAIN capsule -> shared optimizer -> immutable performance report."""

from __future__ import annotations

import copy
import io
import json
from dataclasses import replace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_p1_snapshot import projection_fixture, store_at

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from stpd.cloud_jobs.contracts import ComputeRequest
from stpd.cloud_jobs.execution import dispatch
from stpd.p1.calibration import measure, prepare, read_input
from stpd.p1.dataset import inspect_projections, publish_snapshot
from stpd.p1.execution import MStdExecution
from stpd.p1.spec import MStdConfig
from stpd.p1.workload import prepare_p1_run


@pytest.fixture(scope="module")
def calibration_input(tmp_path_factory):
    store = store_at(tmp_path_factory.mktemp("TRAIN-calibration") / "store")
    projections = []
    for name, count in (("train", 192), ("dev", 1), ("test", 1)):
        _, original, value = projection_fixture(store, name)
        point = value["segments"][0]["points"][0]
        points = []
        for number in range(count):
            item = copy.deepcopy(point)
            item.update(index=number, source_event_id=f"event-{name}-{number}",
                        source_decision_id=f"decision-{name}-{number}")
            points.append(item)
        value["segments"][0]["points"] = points
        projection, _, _ = projection_fixture(store, name, value=value, original=original)
        projections.append(projection)
    inspected = inspect_projections(store, [p.artifact_id for p in projections])
    splits = {group["group_id"]: group["root_run_ids"][0] for group in inspected["groups"]}
    snapshot = publish_snapshot(store, [p.artifact_id for p in projections], PRODUCER,
                                split_salt="explicit-calibration-fixture", split_by_group=splits)
    run = prepare_p1_run(store, snapshot.artifact_id, PRODUCER, MStdConfig(max_epochs=2), "a" * 32)
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "first", None, 1)
    paused = dispatch(store, request, PRODUCER)
    capsule = prepare(store, run.artifact_id, paused.checkpoint_id, PRODUCER, "b" * 32, updates=2)
    return store, capsule


def test_complete_TRAIN_batches_and_source_checkpoint_are_immutable(calibration_input):
    store, capsule = calibration_input
    source = store.get_manifest(capsule.parent("checkpoint"))
    original = b"".join(store.read_payload(source.payload("checkpoint")))
    manifest, frames, targets, vocabulary, state = read_input(store, capsule.artifact_id)
    assert manifest.artifact_id == capsule.artifact_id
    assert len(frames) == len(targets) == 128
    assert state["step"] == 1 and len(capsule.parameters.value()["point_ids"]) == 128
    report = measure(store, capsule.artifact_id, PRODUCER,
                     {"variants": [MStdExecution().to_dict(), MStdExecution(
                         implementation="row-fused-v1", defer_score_sync=True).to_dict(),
                         MStdExecution().to_dict()],
                      "profile": False}, device="cpu")
    value = report.parameters.value()
    assert report.kind == "performance"
    assert report.parent("calibration_input") == capsule.artifact_id
    assert value["DEV_consumed"] is False and value["TEST_consumed"] is False
    assert value["snapshot_validation_reads_all_partitions"] is True
    assert value["DEV_TEST_frames_scored"] is False
    assert value["DEV_TEST_gradients"] is False
    assert value["model_published"] is False and value["formal_resume"] is False
    assert len(value["variants"]) == 3
    assert (value["variants"][0]["final_numerical_sha256"]
            == value["variants"][-1]["final_numerical_sha256"])
    for variant in value["variants"]:
        assert variant["post_update_raw_scores_within_declared_tolerance"] is True
        assert set(variant["final_numerical_sha256"]) == {"model", "optimizer", "rng"}
    for variant in value["variants"]:
        assert variant["measured_updates"] == 1 and len(variant["updates"]) == 2
        assert variant["steady_N_per_second"] > 0
    assert original == b"".join(store.read_payload(source.payload("checkpoint")))


def test_calibration_rejects_heldout_row_even_with_republished_valid_bytes(calibration_input):
    store, capsule = calibration_input
    rows = json.loads(b"".join(store.read_payload(capsule.payload("samples"))))
    rows[0]["split"] = "test"
    payload = store.put_payload("samples", io.BytesIO(json_bytes(rows)), "application/json")
    changed = replace(capsule, payloads=tuple(payload if p.role == "samples" else p
                                              for p in capsule.payloads))
    store.publish(changed)
    with pytest.raises(BoundaryError, match="TRAIN_batch_membership_mismatch"):
        read_input(store, changed.artifact_id)


def test_calibration_rejects_foreign_sampler_order(calibration_input):
    store, capsule = calibration_input
    info = capsule.parameters.value()
    rows = json.loads(b"".join(store.read_payload(capsule.payload("samples"))))
    rows.reverse()
    info["point_ids"].reverse()
    payload = store.put_payload("samples", io.BytesIO(json_bytes(rows)), "application/json")
    changed = Manifest(capsule.kind, capsule.producer, capsule.parents,
                       tuple(payload if p.role == "samples" else p for p in capsule.payloads),
                       FrozenObject.of(info))
    store.publish(changed)
    with pytest.raises(BoundaryError, match="source_sampler_TRAIN_membership_mismatch"):
        read_input(store, changed.artifact_id)


@pytest.mark.parametrize("updates", [0, 1, 9, True])
def test_calibration_requires_bounded_complete_optimizer_batches(calibration_input, updates):
    store, capsule = calibration_input
    with pytest.raises(BoundaryError, match="bounded_two_to_eight_updates_required"):
        prepare(store, capsule.parent("run"), capsule.parent("checkpoint"), PRODUCER,
                "c" * 32, updates=updates)
