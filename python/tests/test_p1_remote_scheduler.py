"""Existing P1 worker under Hub lifecycle and the application's one LocalLedger.

The provider is an explicit synthetic CPU target. No Modal API or CUDA is used.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest
from test_hub_scheduler import Provider
from test_p1_application import workspace

from spireagent.artifact_contracts import Manifest
from spireagent.hub.database import Operations
from spireagent.hub.scheduler import Scheduler
from spireagent.json_boundary import BoundaryError
from spireagent.p1_curation import record_p1_training_use, reserve_p1_snapshot
from stpd.p1.spec import RUN_SCHEMA, MStdConfig
from stpd.p1.workload import prepare_p1_run, verify_p1_run
from stpd.workers.p1_run import verify_p1_result


def composition(tmp_path, *, reserve=True):
    _, store, owner, snapshot = workspace(tmp_path)
    if reserve:
        reserve_p1_snapshot(owner.ledger, store, snapshot.artifact_id)
    run = prepare_p1_run(
        store, snapshot.artifact_id, snapshot.producer, MStdConfig(max_epochs=2), "a" * 32,
    )
    operations = Operations(tmp_path / "remote-operations.sqlite")
    provider = Provider(operations, store, None, run.producer)
    provider.target = replace(provider.target, gpu="none")

    def record_use(manifest: Manifest) -> None:
        if manifest.parameters.value().get("schema") != RUN_SCHEMA:
            raise BoundaryError("remote_training", "P1_recipe_required")
        workload = verify_p1_run(store, manifest.artifact_id, provider.target.producer)
        record_p1_training_use(
            owner.ledger, store, workload.snapshot.artifact_id,
            workload.run.parameters.value()["operation_id"],
        )

    return store, owner, snapshot, run, operations, provider, record_use


def no_second_curation(operations):
    with operations.transaction() as db:
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'curation_%'"
        ).fetchall()


def test_local_ledger_same_worker_bounded_pause_explicit_same_run_resume(tmp_path):
    store, owner, snapshot, run, ops, provider, record_use = composition(tmp_path)
    first = ops.enqueue(
        "training", run.artifact_id, "initial-intent", max_seconds=300,
        reserved_units=1, budget_limit=2, options={"stop_after": 1},
    )
    provider.ready = True
    with Scheduler(ops, store, provider, budget_limit=2,
                   record_training_use=record_use) as scheduler:
        assert scheduler.tick(1)["state"] == "submitted"
        assert scheduler.tick(2)["state"] == "paused"
        pause = json.loads(next(row for row in ops.jobs() if row["id"] == first)["result"])
        checkpoint = pause["checkpoint_id"]
        resumed = ops.enqueue(
            "training", run.artifact_id, "explicit-resume-intent", max_seconds=300,
            reserved_units=1, budget_limit=2, options={"resume": checkpoint},
        )
        assert scheduler.tick(3)["state"] == "submitted"
        assert scheduler.tick(4)["state"] == "completed"
        result = json.loads(next(row for row in ops.jobs() if row["id"] == resumed)["result"])
        _, model, last = verify_p1_result(
            store, result["output_id"], verify_p1_run(store, run.artifact_id, run.producer),
        )
        assert last.state["step"] == 2
        assert model.parent("run") == run.artifact_id
        assert scheduler.tick(5)["state"] == "idle"
    assert len(provider.calls) == 2
    assert provider.calls[0].input_id == provider.calls[1].input_id == run.artifact_id
    assert provider.calls[1].resume == checkpoint
    assert provider.target.gpu == "none" and run.parameters.value()["config"]["device"] == "cpu"
    with owner.transaction() as db:
        uses = db.execute("SELECT count(*) FROM curation_uses WHERE kind='training'").fetchone()
        assert uses == (1,)
    no_second_curation(ops)


@pytest.mark.parametrize("defect", ["missing_reservation", "historical_dev_training"])
def test_injected_owner_rejects_missing_purpose_or_exposed_heldout_before_submit(tmp_path, defect):
    store, owner, snapshot, run, ops, provider, record_use = composition(
        tmp_path, reserve=defect != "missing_reservation",
    )
    if defect == "historical_dev_training":
        from stpd.p1.dataset import load_snapshot

        _, selection = load_snapshot(store, snapshot.artifact_id)
        dev_roots = {p["root_run_id"] for p in selection["points"] if p["split"] == "dev"}
        owner.ledger.use(dev_roots, "training", "honest-historical-exposure")
    ops.enqueue("training", run.artifact_id, "blocked-intent", max_seconds=300,
                reserved_units=1, budget_limit=1)
    with Scheduler(ops, store, provider, budget_limit=1,
                   record_training_use=record_use) as scheduler:
        reply = scheduler.tick(1)
    assert reply["state"] == "preflight_failed"
    assert reply["code"] == (
        "snapshot_purpose_reservation_required" if defect == "missing_reservation"
        else "held_out_run_has_training_exposure"
    )
    assert not provider.calls
    no_second_curation(ops)
