"""Weight propagation, exact unit control, batch denominator and durable resume."""
from dataclasses import replace

import pytest
import torch
from test_artifact_store_v1 import PRODUCER
from test_p1_formal_training import engine
from test_p1_snapshot import explicit_snapshot, projection_fixture, store_at

from spireagent.json_boundary import BoundaryError
from stpd.cloud_jobs.contracts import ComputeRequest
from stpd.cloud_jobs.execution import dispatch, validate_receipt
from stpd.p1.data import digest, json_bytes
from stpd.p1.dataset import SnapshotReader
from stpd.p1.execution import MStdExecution
from stpd.p1.formal_training import MStdTrainer
from stpd.p1.optimization import optimizer_update
from stpd.p1.spec import MStdConfig
from stpd.p1.training import maximum_difference
from stpd.p1.weighting import CHECKPOINT_POLICY, SCHEMA, checked_weights, point_weights
from stpd.p1.workload import populations_from_reader, prepare_p1_run, verify_p1_run
from stpd.workers.checkpoint_codec import decode_checkpoint
from stpd.workers.p1_run import verify_p1_checkpoint


def selection():
    return {"points": [{"point_id": str(i), "root_run_id": r, "split": s}
                       for i, (r, s) in enumerate([("a", "train"), ("a", "train"),
                                                  ("b", "train"), ("holdout", "dev")])]}


def policy(a=1, b=2):
    return {"schema": SCHEMA, "rule_id": "example-only", "weights_by_root": {"a": a, "b": b}}


def test_normalization_uses_train_point_mass_and_exact_root_inventory():
    assert point_weights(policy(), selection()) == {"0": .75, "1": .75, "2": 1.5}
    for value in [0, -1, float("nan"), float("inf"), True]:
        with pytest.raises(BoundaryError):
            checked_weights(policy(value), selection())
    for weights in [{"a": 1}, {"a": 1, "b": 2, "holdout": 3}]:
        with pytest.raises(BoundaryError, match="exact_train_root"):
            checked_weights({**policy(), "weights_by_root": weights}, selection())


@pytest.mark.parametrize("plan", ["reference-v1", "mstd-training-v1"])
def test_all_one_matches_original_loss_gradient_optimizer_and_rng(plan):
    left = engine(train_count=3)
    right = engine(train_count=3)
    # Compare the same approved execution, including dropout/ID-mask draws.
    left.model.execution = right.model.execution = MStdExecution.for_plan(plan)
    pop = right.populations["train"]
    right.populations["train"] = replace(pop, weights=(1.0,) * 3)
    a, b = left.advance(), right.advance()
    assert a["mean_N_loss"] == b["mean_N_loss"]
    assert a["gradient_norm"] == b["gradient_norm"]
    assert maximum_difference(decode_checkpoint(left.checkpoint()),
                              decode_checkpoint(right.checkpoint())) == 0
    for x, y in zip(left.model.parameters(), right.model.parameters(), strict=True):
        assert (x.grad is None) == (y.grad is None)
        if x.grad is not None:
            assert torch.equal(x.grad, y.grad)


def test_weighted_microbatch_uses_global_mean_then_optimizer_batch_mean(monkeypatch):
    trainer = engine(train_count=3)
    model = trainer.model
    parameter = model.ids.weight
    # Make the independently calculable CE depend on a real leaf parameter.
    def scores(frames, **kwargs):
        return [torch.stack((parameter[0, 0], parameter[0, 1])) for _ in frames]
    monkeypatch.setattr(model, "forward_batch", scores)
    monkeypatch.setattr("stpd.p1.optimization.microbatches", lambda *a: iter([[0], [1], [2]]))
    optimizer = torch.optim.SGD(model.parameters(), lr=0)
    frames = list(trainer.populations["train"].frames)
    before = parameter.detach().clone().requires_grad_()
    losses = torch.nn.functional.cross_entropy(before[0, :2].repeat(3, 1),
                                               torch.tensor([0, 1, 0]), reduction="none")
    expected = (losses * torch.tensor([.75, 1.5, .75])).sum() / 3
    expected.backward()
    actual = optimizer_update(model, optimizer, frames, [0, 1, 0], 0,
                              weights=[.75, 1.5, .75])
    assert actual["mean_N_loss"] == pytest.approx(float(expected.detach()), abs=1e-7)
    torch.testing.assert_close(parameter.grad, before.grad)


def test_weighted_input_reaches_real_worker_checkpoint_and_resume(tmp_path):
    store = store_at(tmp_path / "store")
    projections = [projection_fixture(store, "weighted-" + str(i))[0] for i in range(3)]
    snapshot = explicit_snapshot(store, projections)
    reader = SnapshotReader(store, snapshot.artifact_id)
    roots = {p["root_run_id"] for p in reader.selection["points"] if p["split"] == "train"}
    weights = {"schema": SCHEMA, "rule_id": "unit-control",
               "weights_by_root": dict.fromkeys(roots, 1)}
    run = prepare_p1_run(store, snapshot.artifact_id, PRODUCER, MStdConfig(max_epochs=2), "a" * 32,
                         loss_weighting=weights, checkpoint_policy=CHECKPOINT_POLICY)
    workload = verify_p1_run(store, run.artifact_id, PRODUCER)
    assert workload.training.parameters.value()["loss_weighting"] == weights
    pops = populations_from_reader(reader, weights)
    assert pops["train"].weights == (1.0,)
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "weighted-pause", stop_after=1)
    paused = dispatch(store, request, PRODUCER)
    validate_receipt(store, request, paused)
    checkpoint = verify_p1_checkpoint(store, paused.checkpoint_id, workload)
    assert checkpoint.state["identity"]["train_weights_sha256"] == digest(json_bytes((1.0,)))
    resumed = dispatch(store, replace(request, attempt_id="weighted-resume", stop_after=None,
                                      resume=paused.checkpoint_id), PRODUCER)
    validate_receipt(store, replace(request, attempt_id="weighted-resume", stop_after=None,
                                    resume=paused.checkpoint_id), resumed)
    assert resumed.state == "candidate_prepared"
    # A changed frozen policy cannot restore the original optimizer/cursor.
    trainer = MStdTrainer(pops, workload.vocabulary, snapshot.artifact_id, workload.config,
                          execution=workload.execution, checkpoint_policy=CHECKPOINT_POLICY)
    trainer.populations["train"] = replace(pops["train"], weights=(2.0,))
    other = MStdTrainer(trainer.populations, workload.vocabulary, snapshot.artifact_id,
                        workload.config,
                        execution=workload.execution, checkpoint_policy=CHECKPOINT_POLICY)
    with pytest.raises(BoundaryError):
        other.restore(checkpoint.raw)


def test_shortlist_uses_current_measured_DEV_states_and_no_test_evaluation(tmp_path):
    from stpd.p1.shortlist import checkpoint_shortlist

    store = store_at(tmp_path / "store")
    projections = [projection_fixture(store, "shortlist-" + str(i))[0] for i in range(3)]
    snapshot = explicit_snapshot(store, projections)
    run = prepare_p1_run(store, snapshot.artifact_id, PRODUCER, MStdConfig(max_epochs=4), "f" * 32,
                         checkpoint_policy=CHECKPOINT_POLICY)
    with pytest.raises(BoundaryError, match="completed_run_required"):
        checkpoint_shortlist(store, run.artifact_id)
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "full")
    result = dispatch(store, request, PRODUCER)
    assert result.state == "candidate_prepared"
    report = checkpoint_shortlist(store, run.artifact_id)
    assert 3 <= len(report["candidates"]) <= 4
    assert all(c["selection"] == "current" for c in report["candidates"])
    assert report["test_scored"] is report["gameplay_run"] is False
    assert report["progress_observations"] == 4
    assert report["candidates"][0]["mean_N_loss"] == min(
        row["mean_N_loss"] for row in report["candidates"])
