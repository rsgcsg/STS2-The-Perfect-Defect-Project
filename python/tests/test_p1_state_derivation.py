"""Synthetic legacy boundary, real target Store/Compute/worker/strict restore.

The source fixture deliberately rewrites modern producer output into the known f7
boundary identity. It is NOT an actual f7 checkpoint-byte or CUDA qualification.
Only an isolated real immutable f7 checkpoint can supply that stronger evidence.
"""

from __future__ import annotations

import io
from dataclasses import replace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_p1_snapshot import explicit_snapshot, projection_fixture, store_at

from spireagent.json_boundary import BoundaryError, FrozenObject
from stpd.cloud_jobs.contracts import ComputeRequest
from stpd.cloud_jobs.execution import dispatch, preflight_training_request, validate_receipt
from stpd.p1.derivation import LEGACY_CODE, LEGACY_CODEC, LEGACY_PRODUCER, state_sha256
from stpd.p1.execution import MStdExecution
from stpd.p1.formal_training import MStdTrainer
from stpd.p1.spec import DERIVED_CHECKPOINT_SCHEMA, DERIVED_RUN_SCHEMA, MStdConfig
from stpd.p1.workload import populations_from_reader, prepare_p1_run, verify_p1_run
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.p1_run import materialize_p1_model, verify_p1_checkpoint, verify_p1_result

POLICY = MStdExecution(implementation="experimental-packed-v1", microbatch_size=1).to_dict()


@pytest.fixture(autouse=True)
def discard_synthetic_stores(tmp_path):
    # These reproducible synthetic stores contain no irreplaceable runtime evidence.
    yield
    import shutil

    shutil.rmtree(tmp_path, ignore_errors=True)


def legacy_fixture(tmp_path, store=None, snapshot=None):
    if store is None:
        store = store_at(tmp_path / "store")
    if snapshot is None:
        projections = [projection_fixture(store, "source-" + str(i))[0] for i in range(3)]
        snapshot = explicit_snapshot(store, projections)
    run = prepare_p1_run(
        store, snapshot.artifact_id, LEGACY_PRODUCER, MStdConfig(max_epochs=4), "a" * 32
    )
    # This fixture deliberately represents a legacy Run with no execution tag.
    run = replace(run, parameters=FrozenObject.of({
        key: value for key, value in run.parameters.value().items() if key != "execution_plan"
    }))
    store.publish(run)
    request = ComputeRequest(
        "training", run.artifact_id, LEGACY_PRODUCER, "synthetic-original", stop_after=2
    )
    receipt = dispatch(store, request, LEGACY_PRODUCER)
    mapped = {}

    def convert(identity):
        if identity in mapped:
            return mapped[identity]
        cp = store.get_manifest(identity)
        state = decode_checkpoint(b"".join(store.read_payload(cp.payload("checkpoint"))))
        state["identity"].pop("execution")
        state["identity"]["code"] = dict(LEGACY_CODE)
        state["identity"]["codec_sha256"] = LEGACY_CODEC
        info = cp.parameters.value()
        prior = {k: convert(v) for k, v in info["prior_checkpoints"].items()}
        best = convert(info["prior_best_checkpoint"]) if info["prior_best_checkpoint"] else None
        parents = tuple(
            replace(
                p,
                artifact_id=(
                    prior[p.role[11:]]
                    if p.role.startswith("checkpoint_")
                    else best
                    if p.role == "prior_best"
                    else p.artifact_id
                ),
            )
            for p in cp.parents
        )
        payload = store.put_payload(
            "checkpoint", io.BytesIO(encode_checkpoint(state)), "application/vnd.stpd.tensor-tree"
        )
        old = replace(
            cp,
            parents=parents,
            payloads=(payload,),
            parameters=FrozenObject.of(
                {
                    **info,
                    "prior_checkpoints": prior,
                    "prior_best_checkpoint": best,
                }
            ),
        )
        store.publish(old)
        mapped[identity] = old.artifact_id
        return old.artifact_id

    root = convert(receipt.checkpoint_id)
    return store, snapshot, run, root


def derive(store, snapshot, original, checkpoint, **kwargs):
    return prepare_p1_run(
        store,
        snapshot.artifact_id,
        PRODUCER,
        MStdConfig(max_epochs=4),
        "b" * 32,
        execution_policy=POLICY,
        initial_checkpoint=checkpoint,
        **kwargs,
    )


@pytest.mark.parametrize("policy", [MStdExecution().to_dict(), POLICY])
def test_full_state_preserved_DAG_remapped_pause_replacement_result_export(tmp_path, policy):
    store, snapshot, original, checkpoint = legacy_fixture(tmp_path)
    original_raw = b"".join(
        store.read_payload(store.get_manifest(checkpoint).payload("checkpoint"))
    )
    before = decode_checkpoint(original_raw)
    run = prepare_p1_run(
        store,
        snapshot.artifact_id,
        PRODUCER,
        MStdConfig(max_epochs=4),
        "b" * 32,
        execution_policy=policy,
        initial_checkpoint=checkpoint,
    )
    assert run.parameters.value()["schema"] == DERIVED_RUN_SCHEMA
    assert run.parent("original_run") == original.artifact_id
    workload = verify_p1_run(store, run.artifact_id, PRODUCER)
    with pytest.raises(BoundaryError, match="explicit_frozen_execution_policy"):
        prepare_p1_run(
            store,
            snapshot.artifact_id,
            PRODUCER,
            workload.config,
            "d" * 32,
            initial_checkpoint=checkpoint,
        )
    missing_policy = replace(
        run,
        parameters=FrozenObject.of(
            {k: v for k, v in run.parameters.value().items() if k != "execution_policy"}
        ),
    )
    store.publish(missing_policy)
    with pytest.raises(BoundaryError, match="explicit_frozen_execution_policy"):
        verify_p1_run(store, missing_policy.artifact_id, PRODUCER)
    from stpd.p1.derivation import source_checkpoint_dag
    from stpd.p1.preparation import input_transfer_inventory

    inventory = input_transfer_inventory(store, run.artifact_id)
    computed = {
        r["artifact_id"]
        for p in inventory["compute_payloads"]
        for r in p["references"]
        if r["role"] == "checkpoint"
    }
    assert computed == {m.artifact_id for m in source_checkpoint_dag(store, checkpoint, original)}
    assert "original_full_checkpoint_DAG" in inventory["worker_reads"]
    with pytest.raises(BoundaryError, match="checkpoint_execution_identity"):
        MStdTrainer(
            populations_from_reader(workload.reader),
            workload.vocabulary,
            snapshot.artifact_id,
            workload.config,
            workload.execution,
        ).restore(original_raw)
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "derived-first", stop_after=1)
    preflight_training_request(store, request)
    receipt = dispatch(store, request, PRODUCER)
    validate_receipt(store, request, receipt)
    assert receipt.state == "paused"
    paused = verify_p1_checkpoint(store, receipt.checkpoint_id, workload)
    assert paused.state["step"] == before["step"] + 1
    migrated = [
        store.get_manifest(k)
        for k in store.manifest_ids()
        if store.get_manifest(k).parameters.value().get("schema") == DERIVED_CHECKPOINT_SCHEMA
    ]
    root = next(m for m in migrated if m.parent("original_checkpoint") == checkpoint)
    derived_state = verify_p1_checkpoint(store, root.artifact_id, workload).state
    assert state_sha256(derived_state) == state_sha256(before)
    assert derived_state["identity"] != before["identity"]
    assert len(migrated) >= 2
    for item in migrated:
        verify_p1_checkpoint(store, item.artifact_id, workload)
        assert item.parent("run") == run.artifact_id
        assert item.parameters.value()["derivation"]["inherited_DEV"].startswith(
            "original_reference"
        )
    # A new worker process imports only target source and performs ordinary strict resume.
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    output = tmp_path / "replacement-receipt.json"
    replacement = replace(
        request, attempt_id="replacement", resume=receipt.checkpoint_id, stop_after=None
    )
    script = """import json,sys
from pathlib import Path
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from stpd.cloud_jobs.contracts import ComputeRequest
from stpd.cloud_jobs.execution import dispatch,validate_receipt
request=ComputeRequest.decode(json.loads(sys.argv[2]))
store=ManifestArtifactStore(LocalBlobStore(Path(sys.argv[1]),create=False))
receipt=dispatch(store,request,request.producer)
validate_receipt(store,request,receipt)
Path(sys.argv[3]).write_text(json.dumps(receipt.to_dict()))
"""
    subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(store.blobs.root),
            json.dumps(replacement.to_dict()),
            str(output),
        ],
        check=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    from stpd.cloud_jobs.contracts import ComputeReceipt

    completed = ComputeReceipt.decode(json.loads(output.read_text()))
    result, model, last = verify_p1_result(store, completed.output_id, workload)
    assert last.state["step"] == 4
    assert set(result.parameters.value()["checkpoint_ids"]) == {
        "5",
        "10",
        "20",
        "40",
        "60",
        "80",
        "100",
    }
    metadata = materialize_p1_model(store, model.artifact_id, tmp_path / "export")
    from stpd.p1.export import load_model

    loaded, package = load_model(tmp_path / "export/model.tensor-tree", metadata["package_sha256"])
    assert loaded.execution.to_dict() == policy
    import torch

    with torch.inference_mode():
        frame = populations_from_reader(workload.reader)["dev"].frames[0]
        scores = loaded(frame)
    assert bool(torch.isfinite(scores).all()) and scores.numel() == len(frame.choices)
    assert package["schema"] == (
        "stpd/p1-mstd-model-1" if policy == MStdExecution().to_dict() else "stpd/p1-mstd-model-2"
    )
    assert (
        b"".join(store.read_payload(store.get_manifest(checkpoint).payload("checkpoint")))
        == original_raw
    )


@pytest.mark.parametrize(
    "defect", ["source", "lock", "code", "TEST", "phase", "snapshot", "config"]
)
def test_derivation_rejects_incompatible_source_before_new_run(tmp_path, defect):
    store, snapshot, original, checkpoint = legacy_fixture(tmp_path)
    cp = store.get_manifest(checkpoint)
    if defect in {"source", "lock"}:
        producer = replace(
            cp.producer,
            **(
                {"source_revision": "f" * 40}
                if defect == "source"
                else {"uv_lock_sha256": "f" * 64}
            ),
        )
        cp = replace(cp, producer=producer)
    elif defect in {"code", "TEST", "phase"}:
        state = decode_checkpoint(b"".join(store.read_payload(cp.payload("checkpoint"))))
        if defect == "code":
            state["identity"]["code"]["model.py"] = "f" * 64
        elif defect == "TEST":
            state["test_result"] = {"already_opened": True}
        else:
            state["phase"] = "trained"
        payload = store.put_payload(
            "checkpoint", io.BytesIO(encode_checkpoint(state)), "application/vnd.stpd.tensor-tree"
        )
        cp = replace(
            cp,
            payloads=(payload,),
            parameters=FrozenObject.of({**cp.parameters.value(), "phase": state["phase"]}),
        )
    store.publish(cp)
    if defect == "snapshot":
        other = explicit_snapshot(
            store, [projection_fixture(store, "other-" + str(i))[0] for i in range(3)]
        )
        snapshot = other
    config = MStdConfig(max_epochs=3 if defect == "config" else 4)
    with pytest.raises(BoundaryError):
        prepare_p1_run(
            store,
            snapshot.artifact_id,
            PRODUCER,
            config,
            "c" * 32,
            execution_policy=POLICY,
            initial_checkpoint=cp.artifact_id,
        )


@pytest.mark.parametrize("defect", ["runtime", "partition", "sampler", "extra_state"])
def test_worker_rejects_incompatible_full_state(tmp_path, defect):
    store, snapshot, original, checkpoint = legacy_fixture(tmp_path)
    cp = store.get_manifest(checkpoint)
    state = decode_checkpoint(b"".join(store.read_payload(cp.payload("checkpoint"))))
    if defect == "runtime":
        state["identity"]["runtime"]["torch"] = "unsupported"
    elif defect == "partition":
        state["identity"]["partitions"]["train"]["points_sha256"] = "f" * 64
    elif defect == "sampler":
        state["sampler"]["order"] = [999]
    else:
        state["unknown_full_state"] = "must_not_be_dropped"
    payload = store.put_payload(
        "checkpoint", io.BytesIO(encode_checkpoint(state)), "application/vnd.stpd.tensor-tree"
    )
    cp = replace(cp, payloads=(payload,))
    store.publish(cp)
    run = derive(store, snapshot, original, cp.artifact_id)
    with pytest.raises(BoundaryError):
        dispatch(store, ComputeRequest("training", run.artifact_id, PRODUCER, "rejected"), PRODUCER)


def test_normal_Application_CLI_curation_Ops_derived_start_and_resume(tmp_path):
    from test_remote_training_http import client, settled

    with client(tmp_path) as (app, call, provider, store, owner, snapshot, _config_path):
        _, _, original, checkpoint = legacy_fixture(tmp_path, store, snapshot)
        body = {
            "intent_id": "b" * 32,
            "source_id": snapshot.artifact_id,
            "config": MStdConfig(max_epochs=4).to_dict(),
            "max_seconds": 300,
            "stop_after": 1,
            "execution_policy": POLICY,
            "initial_checkpoint": checkpoint,
        }
        with pytest.raises(BoundaryError, match="explicit_frozen_execution_policy"):
            call("start", body={k: v for k, v in body.items() if k != "execution_policy"})
        provider.ready = True
        first = call("start", body=body)["jobs"][0]
        paused = settled(call, first["id"], "paused")
        assert paused["result"]["checkpoint_id"]
        run = store.get_manifest(first["input_id"])
        assert run.parameters.value()["schema"] == DERIVED_RUN_SCHEMA
        assert run.parent("original_run") == original.artifact_id
        assert call("status", intent_id="b" * 32)["local_runs"][0]["run_id"] == run.artifact_id
        second = call(
            "resume",
            body={
                "job_id": first["id"],
                "expected_attempt_id": paused["attempt_id"],
                "intent_id": "c" * 32,
                "checkpoint_id": paused["result"]["checkpoint_id"],
                "max_seconds": 300,
                "stop_after": None,
            },
        )["jobs"][0]
        completed = settled(call, second["id"], "completed")
        call(
            "reconcile",
            body={"job_id": second["id"], "expected_attempt_id": completed["attempt_id"]},
        )
        workload = verify_p1_run(store, run.artifact_id, provider.target.producer)
        _, model, last = verify_p1_result(store, completed["result"]["output_id"], workload)
        assert last.state["step"] == 4 and model.parent("run") == run.artifact_id
        assert len(provider.calls) == 2
        with owner.transaction() as db:
            assert db.execute(
                "SELECT count(*) FROM curation_uses WHERE kind='training'"
            ).fetchone() == (1,)
        from spireagent.hub.training_continuation import read

        assert read(app.remote_training._operations) is None


def test_inherited_best_remains_auditable_when_future_DEV_does_not_improve(tmp_path, monkeypatch):
    store, snapshot, original, checkpoint = legacy_fixture(tmp_path)
    run = derive(store, snapshot, original, checkpoint)
    # Isolate the retained-selection path; actual updates/export/auditor are real.
    # Controlled no-improvement selection is not numerical/quality evidence.
    monkeypatch.setattr(MStdTrainer, "_select_best", lambda self, loss, epoch: False)
    result = dispatch(
        store, ComputeRequest("training", run.artifact_id, PRODUCER, "retain-best"), PRODUCER
    )
    final, _, _ = verify_p1_result(
        store, result.output_id, verify_p1_run(store, run.artifact_id, PRODUCER)
    )
    best = store.get_manifest(final.parent("best_checkpoint"))
    assert best.parameters.value()["schema"] == DERIVED_CHECKPOINT_SCHEMA
    assert best.parent("original_checkpoint") == checkpoint


def test_qualification_tool_real_updates_receipts_and_fixed_DEV_without_TEST(tmp_path):
    import copy

    from stpd.p1.dataset import inspect_projections, publish_snapshot
    from stpd.p1.derivation_qualification import (
        fixed_dev_comparison,
        initial_request,
        qualification_report,
        replacement_request,
    )

    store = store_at(tmp_path / "store")
    projections = []
    for name, count in (("train", 449), ("dev", 1), ("test", 1)):
        _, original, value = projection_fixture(store, name)
        point = value["segments"][0]["points"][0]
        points = []
        for number in range(count):
            row = copy.deepcopy(point)
            row.update(
                index=number,
                source_event_id=f"event-{name}-{number}",
                source_decision_id=f"decision-{name}-{number}",
            )
            points.append(row)
        value["segments"][0]["points"] = points
        projections.append(projection_fixture(store, name, original=original, value=value)[0])
    inspected = inspect_projections(store, [p.artifact_id for p in projections])
    splits = {g["group_id"]: g["root_run_ids"][0] for g in inspected["groups"]}
    snapshot = publish_snapshot(
        store,
        [p.artifact_id for p in projections],
        PRODUCER,
        split_salt="qualification-fixture",
        split_by_group=splits,
    )
    _, _, original, checkpoint = legacy_fixture(tmp_path, store, snapshot)
    run = derive(store, snapshot, original, checkpoint)
    first = initial_request(store, run.artifact_id, PRODUCER, "QA-first")
    receipt1 = dispatch(store, first, PRODUCER)
    second = replacement_request(store, first, receipt1, "QA-replacement")
    receipt2 = dispatch(store, second, PRODUCER)
    report = qualification_report(store, first, receipt1, second, receipt2)
    assert report["ending_step"] - report["starting_step"] == 4
    assert report["TEST_scored"] is False
    assert report["quality_or_live_switch_qualified"] is False
    import torch

    rng = torch.get_rng_state().clone()
    dev = fixed_dev_comparison(store, run.artifact_id, receipt2.checkpoint_id)
    assert torch.equal(rng, torch.get_rng_state())
    assert dev["TEST_scored"] is False
    assert dev["reference_DEV"]["N"] == dev["target_DEV"]["N"] == 1
    assert dev["historical_selection_recomputed"] is False
    with pytest.raises(BoundaryError, match="new_attempt"):
        replacement_request(store, first, receipt1, first.attempt_id)
    with pytest.raises(BoundaryError):
        qualification_report(store, first, receipt1, second, receipt1)
