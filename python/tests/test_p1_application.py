"""P1 engineering allocation and actual existing local service/private child."""
from __future__ import annotations

import uuid

import pytest
import torch
from legacy_p1_fixture import reserve_p1_snapshot
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


@pytest.mark.parametrize("fault", ["valid", "unrecorded", "excluded", "heldout_exposed"])
def test_recorded_calibration_use_rechecks_ledger_without_local_reprojection(
    tmp_path, monkeypatch, fault,
):
    from spireagent import p1_curation

    _, store, owner, snapshot = workspace(tmp_path)
    reserve_p1_snapshot(owner, store, snapshot.artifact_id)
    record_p1_training_use(owner, store, snapshot.artifact_id, "d" * 32)
    _, selection = load_snapshot(store, snapshot.artifact_id)
    expected = None
    reference = "d" * 32
    if fault == "unrecorded":
        reference, expected = "e" * 32, "training_source_use_missing"
    elif fault == "excluded":
        chosen = next(point for point in selection["points"] if point["split"] == "train")
        owner.ledger.annotate(p1_curation._occurrence(chosen["source_decision_id"]),
                              "engineering-test", "exclude", "quality exclusion")
        expected = "snapshot_selected_excluded_N"
    elif fault == "heldout_exposed":
        dev = {p["root_run_id"] for p in selection["points"] if p["split"] == "dev"}
        owner.ledger.use(dev, "training", "previous-training")
        expected = "held_out_run_has_training_exposure"

    def no_reprojection(*_args, **_kwargs):
        pytest.fail("existing-use check must not project the full Snapshot locally")

    monkeypatch.setattr(p1_curation, "load_snapshot", no_reprojection)
    with owner.transaction() as db:
        before = db.execute("SELECT COUNT(*) FROM curation_uses").fetchone()[0]
    if expected:
        with pytest.raises(BoundaryError, match=expected):
            p1_curation.require_recorded_p1_training_use(
                owner.ledger, store, snapshot.artifact_id, reference)
    else:
        actual, scopes = p1_curation.require_recorded_p1_training_use(
            owner.ledger, store, snapshot.artifact_id, reference)
        assert actual == selection and set(scopes) == {"train", "dev", "test"}
    with owner.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM curation_uses").fetchone()[0] == before


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

    # The formal worker's actual exported weights enter the existing Agent.
    import io
    import json

    from stpd.p1.agent import SCHEMA, serve
    from stpd.p1.data import digest, json_bytes
    from stpd.p1.export import register

    private = tmp_path / "formal-model-agent"
    register(export, private, {"schema": "sts2.policy-runtime/agent-manifest-1",
                              "input": {"profile": observation["profile"],
                                        "global": {"revision": observation["revision"]}}},
             entry_id="formal-engineering-model", label="P1 formal recipe engineering model")
    manifest = json.loads((private / "manifest.json").read_bytes())
    current = {k: v for k, v in observation.items() if k != "envelope"}
    destination = io.StringIO()
    context = {"schema": SCHEMA, "session_id": "formal-test", "recovery_epoch": 0}
    value = {"acquisition_id": "one", "input_spec": manifest["input"]["input_spec"],
             "continuity_token": "one", "previous_consumption_id": None,
             "observation": current, "catalog": current["catalog"],
             "consumption_unit": {"id": "one"}}

    def messages():
        yield json.dumps({**context, "request_id": "consume", "message_type": "consume",
                          "input": value})
        completion = json.loads(destination.getvalue().splitlines()[-1])["completion"]
        yield json.dumps({**context, "request_id": "consume", "message_type": "consume_ack",
                          "completion": completion})
        yield json.dumps({**context, "request_id": "next", "message_type": "next",
                          "input": {"continuity_token": "one"}})

    serve(private / "config.json", private / "manifest.json", private,
          source=messages(), destination=destination)
    directive = json.loads(destination.getvalue().splitlines()[-1])["output"]["directive"]
    assert directive["selection"]["action_id"] == frame.choices[int(scores.argmax())]["choice_id"]
    assert directive["scores"] == {"catalog_digest": digest(json_bytes(current["catalog"])),
                                   "values": scores.tolist()}


def test_real_service_pause_resume_keeps_run_input_and_optimizer_state(tmp_path, monkeypatch):
    import json

    from spireagent.workbench.local_training import LocalTrainingService
    from spireagent.workbench.recipes import structured as adapter
    from stpd.workers.checkpoint_codec import decode_checkpoint

    config, store, owner, snapshot = workspace(tmp_path)
    reserve_p1_snapshot(owner, store, snapshot.artifact_id)
    request = TrainingRequest(uuid.uuid4().hex, RECIPE, snapshot.artifact_id,
                              MStdConfig(max_epochs=2).to_dict(),
                              limits={"wall_seconds": 120, "scratch_bytes": 512 * 1024**2})
    service = LocalTrainingService(config)
    actual_child = adapter._private_child
    pause_requests = []

    def observe_child(command, *args, **kwargs):
        callback = kwargs["on_stdout_line"]

        def event_line(raw):
            message = json.loads(raw)
            callback(raw)
            if message["kind"] == "event":
                event = store.get_manifest(message["details"]["event_id"]).parameters.value()
                if (event["kind"] == "progress" and event["details"]["optimizer_updates"] == 1
                        and not pause_requests):
                    pause_requests.append(service.pause(message["operation_id"],
                                                        message["attempt_id"]))
        kwargs["on_stdout_line"] = event_line
        return actual_child(command, *args, **kwargs)

    monkeypatch.setattr(adapter, "_private_child", observe_child)
    service.start(request)
    service._thread.join(timeout=120)
    assert not service._thread.is_alive()
    paused = service.status()["operation"]
    assert pause_requests and paused["status"] == "paused", paused
    assert paused["progress"]["completed"] == 1 and paused["worker_state"] == "terminal"
    saved = store.get_manifest(paused["checkpoint_id"])
    state = decode_checkpoint(b"".join(store.read_payload(saved.payload("checkpoint"))))
    assert state["step"] == 1 and state["optimizer"]["state"]
    assert all(float(slot["step"]) == 1 for slot in state["optimizer"]["state"].values())
    assert ObjectStoreRunReporter(store, store.blobs).completed(paused["run_id"]) is None
    # A resume verifier would populate a missing checkpoint before a late guard.
    # Seed a real peer, evict only this synthetic chunk and keep the exact request.

    from spireagent.storage.cache import configure_cache
    from spireagent.storage.store import copy_artifact

    peer = ManifestArtifactStore(LocalBlobStore(tmp_path / "resume-peer"))
    copy_artifact(store, peer, paused["checkpoint_id"], scratch=tmp_path)
    configure_cache(store, str(peer.blobs.root), max_bytes=2 * 1024**3)
    payload = saved.payload("checkpoint")
    index = json.loads(store.blobs.get(f"payload-indexes/v1/{payload.sha256}.json"))
    missing = store.blobs.root / "objects/sha256" / index["chunks"][0]["sha256"]
    missing.unlink()
    operation_path, _ = service._paths(owner)
    before_operation = operation_path.read_bytes()
    with monkeypatch.context() as low:
        low.setenv("SPIREAGENT_MIN_FREE_BYTES", str(2**60))
        with pytest.raises(BoundaryError, match="space_insufficient"):
            service.resume(paused["operation_id"], paused["attempt_id"], paused["checkpoint_id"],
                           uuid.uuid4().hex, request.limits)
    assert not missing.exists()
    assert operation_path.read_bytes() == before_operation
    service.resume(paused["operation_id"], paused["attempt_id"], paused["checkpoint_id"],
                   uuid.uuid4().hex, request.limits)
    service._thread.join(timeout=120)
    assert not service._thread.is_alive()
    completed = service.status()["operation"]
    assert completed["status"] == "completed", completed
    assert completed["attempt_id"] != paused["attempt_id"]
    assert completed["run_id"] == paused["run_id"] and completed["input_id"] == paused["input_id"]
    result = ObjectStoreRunReporter(store, store.blobs).completed(completed["run_id"])
    workload = verify_p1_run(store, completed["run_id"], source_identity(ROOT))
    _, _, last = verify_p1_result(store, result.artifact_id, workload)
    assert last.state["step"] == 2 and last.state["history"][0] == state["history"][0]
    assert all(float(slot["step"]) == 2 for slot in last.state["optimizer"]["state"].values())


@pytest.mark.parametrize(
    "fault", [
        "missing_heldout_reservation", "dev_training_exposure", "dev_gold", "excluded_N", "valid",
    ],
)
def test_hub_P1_preflight_checks_the_same_roles_before_provider_submit(tmp_path, fault):
    from test_hub_scheduler import Provider

    from spireagent.hub.curation import CurationLedger
    from spireagent.hub.database import Operations
    from spireagent.hub.scheduler import Scheduler
    from spireagent.p1_curation import reserve_p1_snapshot as reserve_shared
    from stpd.p1.workload import prepare_p1_run

    _, store, _, snapshot = workspace(tmp_path)
    _, selection = load_snapshot(store, snapshot.artifact_id)
    train = {p["root_run_id"] for p in selection["points"] if p["split"] == "train"}
    dev = {p["root_run_id"] for p in selection["points"] if p["split"] == "dev"}
    producer = snapshot.producer
    run = prepare_p1_run(store, snapshot.artifact_id, producer,
                         MStdConfig(max_epochs=1), "a" * 32)
    operations = Operations(tmp_path / "hub.sqlite")
    ledger = CurationLedger(operations)
    if fault in {"valid", "excluded_N"}:
        assert reserve_shared(ledger, store, snapshot.artifact_id) == reserve_shared(
            ledger, store, snapshot.artifact_id
        )
        expected = None
        if fault == "excluded_N":
            from spireagent.p1_curation import _occurrence

            ledger.annotate(_occurrence(selection["points"][0]["source_decision_id"]),
                            "engineering-test", "exclude", "explicit N quality exclusion")
            expected = "snapshot_selected_excluded_N"
    elif fault == "dev_training_exposure":
        reserve_shared(ledger, store, snapshot.artifact_id)
        ledger.use(dev, "training", "prior-training")
        expected = "held_out_run_has_training_exposure"
    else:
        ledger.claim(snapshot.artifact_id, "training", train)
        ledger.bind(snapshot.artifact_id, snapshot.artifact_id)
        if fault == "dev_gold":
            ledger.claim("gold-dev", "gold", dev)
            with pytest.raises(BoundaryError, match="gold_reserved_data"):
                reserve_shared(ledger, store, snapshot.artifact_id)
        expected = "snapshot_purpose_reservation_required"
    operations.enqueue("training", run.artifact_id, "P1-role-counterexample",
                       max_seconds=300, reserved_units=1, budget_limit=1)
    provider = Provider(operations, store, None, producer)
    with Scheduler(operations, store, provider, budget_limit=1) as scheduler:
        reply = scheduler.tick(1)
    if fault == "valid":
        assert reply["state"] == "submitted" and len(provider.calls) == 1
    else:
        assert reply["state"] == "preflight_failed" and reply["code"] == expected
        assert not provider.calls
    with operations.transaction() as db:
        used = {r[0] for r in db.execute("SELECT run FROM curation_uses WHERE kind='training'")}
    assert used == (train if fault == "valid" else
                    dev if fault == "dev_training_exposure" else set())


def test_training_capacity_refuses_before_pending_use_or_child(tmp_path, monkeypatch):
    from spireagent.workbench.local_training import LocalTrainingService

    config, store, owner, snapshot = workspace(tmp_path)
    reserve_p1_snapshot(owner, store, snapshot.artifact_id)
    service = LocalTrainingService(config)
    before = set(store.manifest_ids())
    monkeypatch.setenv("SPIREAGENT_MIN_FREE_BYTES", str(2**60))
    request = TrainingRequest.from_dict({
        "schema": "spireagent/training-request-v1", "intent_id": "a" * 32,
        "recipe_id": RECIPE, "source_id": snapshot.artifact_id,
        "config": MStdConfig(max_epochs=1).to_dict(), "placement_id": "local-cpu",
        "limits": {"wall_seconds": 30, "scratch_bytes": 64 * 1024**2},
        "after_completed_operation_id": None,
    })
    with pytest.raises(BoundaryError, match="space_insufficient"):
        service.start(request)
    path, _ = service._paths(owner)
    assert not path.exists()
    assert set(store.manifest_ids()) == before
    assert service._thread is None



def test_explicit_research_volume_combines_real_scratch_and_store_allowances(tmp_path, monkeypatch):
    import os
    from dataclasses import replace
    from types import SimpleNamespace

    from spireagent.workbench import p1_recording_storage
    from spireagent.workbench.developer import LocalResearchWorkspaceConfig
    from spireagent.workbench.local_training import LocalTrainingService

    config, store, owner, snapshot = workspace(tmp_path)
    reserve_p1_snapshot(owner, store, snapshot.artifact_id)
    app_state = tmp_path / "other-volume"
    app_state.mkdir()
    config = replace(config, state_dir=app_state, research_workspace=LocalResearchWorkspaceConfig(
        owner.store_dir, owner.path.parent / "registry.sqlite"))
    service = LocalTrainingService(config)
    real_stat = type(app_state).stat

    def stat_on_two_volumes(path, **kwargs):
        info = real_stat(path, **kwargs)
        device = 1 if path.is_relative_to(app_state) else 2
        return os.stat_result((info.st_mode, info.st_ino, device, *info[3:]))

    monkeypatch.setattr(type(app_state), "stat", stat_on_two_volumes)
    monkeypatch.setattr(p1_recording_storage.shutil, "disk_usage", lambda path: SimpleNamespace(
        free=(20 * 1024**3 if path.is_relative_to(app_state) else 7 * 1024**3 // 2)))
    monkeypatch.delenv("SPIREAGENT_MIN_FREE_BYTES", raising=False)
    request = TrainingRequest(uuid.uuid4().hex, RECIPE, snapshot.artifact_id,
        MStdConfig(max_epochs=1).to_dict(),
        limits={"wall_seconds": 30, "scratch_bytes": 1024**3})
    with pytest.raises(BoundaryError, match="space_insufficient"):
        service.start(request)
    path, _ = service._paths(owner)
    assert not path.exists() and service._thread is None
