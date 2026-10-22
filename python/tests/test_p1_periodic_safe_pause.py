"""Real CPU worker/checkpoint seams under a fake operational clock, without cloud."""
from __future__ import annotations

import copy
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_cloud_jobs import job_fixture
from test_p1_snapshot import projection_fixture, store_at
from test_p1_workload import small_run

from spireagent.json_boundary import BoundaryError
from stpd.cloud_jobs.__main__ import execute_request, main
from stpd.cloud_jobs.contracts import ComputeRequest
from stpd.cloud_jobs.execution import CandidateReporter, dispatch, validate_receipt
from stpd.p1.baseline_spec import BaselineConfig
from stpd.p1.baseline_workload import prepare_baseline_run
from stpd.p1.baselines import BaselineTrainer
from stpd.p1.dataset import inspect_projections, publish_snapshot
from stpd.p1.formal_training import MStdTrainer
from stpd.p1.spec import MStdConfig
from stpd.p1.training import maximum_difference
from stpd.p1.workload import prepare_p1_run, verify_p1_run
from stpd.workers.p1_run import verify_p1_checkpoint


def clock_worker(monkeypatch, clock):
    import stpd.workers.p1_run as worker

    monkeypatch.setattr(worker, "time", SimpleNamespace(
        perf_counter=time.perf_counter, monotonic=lambda: clock.now))


def longer_run(tmp_path):
    store = store_at(tmp_path / "store")
    original, source, value = projection_fixture(store, "train")
    template = value["segments"][0]["points"][0]
    points = []
    for index in range(65):
        point = copy.deepcopy(template)
        point.update(index=index, source_event_id=f"train-event-{index}",
                     source_decision_id=f"train-decision-{index}")
        point["observation"]["scene"]["content"]["entities"][0]["description"] = f"Sample {index}"
        points.append(point)
    value["segments"][0]["points"] = points
    train, _, _ = projection_fixture(store, "train", original=source, value=value)
    projections = [train, *(projection_fixture(store, split)[0] for split in ("dev", "test"))]
    ids = [p.artifact_id for p in projections]
    inspection = inspect_projections(store, ids)
    split_by_group = {g["group_id"]: g["root_run_ids"][0] for g in inspection["groups"]}
    snapshot = publish_snapshot(store, ids, PRODUCER, split_salt="fixed-test",
                                split_by_group=split_by_group)
    run = prepare_p1_run(store, snapshot.artifact_id, PRODUCER, MStdConfig(max_epochs=4), "a" * 32)
    return store, run


def test_periodic_checkpoint_full_resume_matches_without_extra_dev_test_or_double_save(
    tmp_path, monkeypatch,
):
    store, run = longer_run(tmp_path)
    clock = SimpleNamespace(now=0.0, timed=False, attempt="reference")
    clock_worker(monkeypatch, clock)
    evaluations = {"reference": [], "periodic": [], "resume": []}
    advance, evaluate = MStdTrainer.advance, MStdTrainer.evaluate

    def observed_advance(trainer):
        progress = advance(trainer)
        if clock.timed and trainer.step in {3, 4}:
            clock.now += 1800  # step3 is ordinary; step4 is milestone + epoch DEV.
        return progress

    def observed_evaluate(model, population):
        evaluations[clock.attempt].append(tuple(population.point_ids))
        return evaluate(model, population)

    monkeypatch.setattr(MStdTrainer, "advance", observed_advance)
    monkeypatch.setattr(MStdTrainer, "evaluate", staticmethod(observed_evaluate))
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "reference")
    full = dispatch(store, request, PRODUCER)
    clock.timed, clock.attempt = True, "periodic"
    partial_request = replace(request, attempt_id="periodic", stop_after=3)
    partial = dispatch(store, partial_request, PRODUCER)
    assert partial.state == "paused" and partial.output_id is None
    workload = verify_p1_run(store, run.artifact_id, PRODUCER)
    saved = verify_p1_checkpoint(store, partial.checkpoint_id, workload)
    assert saved.state["step"] == 3 and saved.state["test_result"] is None
    fields = {"model", "optimizer", "rng", "sampler", "best_model", "dev_history"}
    assert fields <= saved.state.keys()
    del saved
    clock.attempt = "resume"
    resumed = dispatch(store, replace(request, attempt_id="resume", resume=partial.checkpoint_id),
                        PRODUCER)
    expected = verify_p1_checkpoint(store, full.checkpoint_id, workload)
    actual = verify_p1_checkpoint(store, resumed.checkpoint_id, workload)
    assert maximum_difference(expected.state, actual.state) == 0.0
    assert evaluations["reference"] == evaluations["periodic"] + evaluations["resume"]
    result = store.get_manifest(resumed.output_id)
    assert set(result.parameters.value()["checkpoint_ids"]) == {
        "5", "10", "20", "40", "60", "80", "100"}
    events = [e.parameters.value() for e in CandidateReporter(store, run.artifact_id, PRODUCER)
              .events(run.artifact_id)]
    periodic = [e for e in events if e["kind"] == "checkpoint"
                and e["details"]["reason"] == "periodic"]
    assert len(periodic) == 1 and periodic[0]["details"]["optimizer_updates"] == 3
    step4 = [e for e in events if e["attempt"] == "resume" and e["kind"] == "checkpoint"
             and e["details"]["optimizer_updates"] == 4]
    assert len(step4) == 1 and step4[0]["details"]["reason"] == "scheduled_or_dev_best"


@pytest.mark.parametrize("baseline", [False, True])
def test_cloud_wall_limit_reaches_original_pause_checkpoint_before_final_test(
    tmp_path, monkeypatch, baseline,
):
    import stpd.cloud_jobs.execution as execution

    store, snapshot, run = small_run(tmp_path)
    if baseline:
        run = prepare_baseline_run(store, snapshot.artifact_id, PRODUCER,
            BaselineConfig(algorithm="random", cpu_threads=1), "b" * 32)
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "timed")
    frozen = request.to_dict()
    clock = SimpleNamespace(now=0.0)
    clock_worker(monkeypatch, clock)
    monkeypatch.setattr(execution, "time", SimpleNamespace(monotonic=lambda: clock.now))
    trainer_type = BaselineTrainer if baseline else MStdTrainer
    advance = trainer_type.advance

    def completed_update(trainer):
        result = advance(trainer)
        clock.now = 200  # wall500 - save margin300, immediately after one complete update.
        return result

    def forbidden_test(*_args):
        pytest.fail("safe pause must not enter final TEST")

    monkeypatch.setattr(trainer_type, "advance", completed_update)
    monkeypatch.setattr(trainer_type, "finish", forbidden_test)
    monkeypatch.setattr("stpd.cloud_jobs.__main__.source_identity", lambda _: PRODUCER)
    monkeypatch.setattr("stpd.cloud_jobs.__main__.open_store", lambda _: store)
    from stpd.cloud_jobs.contracts import ComputeReceipt

    receipt = ComputeReceipt.decode(execute_request(frozen, wall_seconds=500))
    validate_receipt(store, request, receipt)
    assert request.to_dict() == frozen
    assert receipt.state == "paused" and receipt.output_id is None
    checkpoint = store.get_manifest(receipt.checkpoint_id)
    assert checkpoint.parent("run") == run.artifact_id
    assert checkpoint.parameters.value()["reason"] == "paused"
    assert not any(store.get_manifest(i).kind in {"offline_evaluation", "model", "run_result"}
                   for i in store.manifest_ids())


def test_short_p1_wall_rejects_and_legacy_feature_behavior_is_retained(tmp_path):
    store, _, run = small_run(tmp_path / "p1")
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "too-short")
    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match="wall_seconds_must_exceed_save_margin"):
        dispatch(store, request, PRODUCER, wall_seconds=300)
    assert store.manifest_ids() == before
    store, backend, _, _, feature_request = job_fixture(tmp_path / "feature")
    receipt = dispatch(store, feature_request, PRODUCER, backend=backend, wall_seconds=1)
    assert receipt.state == "candidate_prepared"


def test_cloud_cli_passes_operational_wall_without_changing_request(tmp_path, monkeypatch, capsys):
    request = {"original": True}
    path = tmp_path / "request.json"
    import json

    path.write_text(json.dumps(request))
    received = []

    def execute(value, *, store_location, wall_seconds):
        received.append((value, store_location, wall_seconds))
        return {"paused": True}

    monkeypatch.setattr("stpd.cloud_jobs.__main__.execute_request", execute)
    assert main(["--request", str(path), "--store", "fixture", "--wall-seconds", "14400"]) == 0
    assert received == [(request, "fixture", 14400)]
    assert json.loads(capsys.readouterr().out) == {"paused": True}
