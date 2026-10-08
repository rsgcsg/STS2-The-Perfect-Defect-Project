"""Actual shared application boundary using immutable synthetic fixtures only."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace

import pytest
from test_local_training import _ready
from test_structured_s0 import PRODUCER, sample, source

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.workbench.inplace_curation import configured_owner
from spireagent.workbench.local_training import OPERATION_FILE, LocalTrainingService
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.recipes import structured as adapter_module
from spireagent.workbench.trusted_recipes import STRUCTURED_RECIPE


def ready(tmp_path, monkeypatch, *, source_kind="synthetic", qualification="synthetic_fixture"):
    config, _legacy, _, store = _ready(tmp_path, monkeypatch)
    value = source([sample(20), sample(21, hp=11), sample(22, hp=10)], source_kind=source_kind)
    raw = json_bytes(value)
    payload = store.put_payload("source", io.BytesIO(raw), "application/json")
    manifest = Manifest("dataset", PRODUCER, payloads=(payload,), parameters=FrozenObject.of({
        "schema": value["schema"], "source_kind": source_kind,
        "source_sha256": payload.sha256, "qualification": qualification,
    }))
    store.publish(manifest)
    owner = configured_owner(config)
    runs = {run["run_id"] for run in value["runs"]}
    # Synthetic test fixtures prepare existing ledger authority explicitly; the
    # application never creates these records from caller-supplied source JSON.
    with owner.transaction() as db:
        db.execute("INSERT INTO curation_sources VALUES(?,?,1)", (manifest.artifact_id, "fixture"))
        db.execute("INSERT INTO curation_exact_source_index VALUES(?)", (manifest.artifact_id,))
        db.executemany("INSERT INTO curation_source_runs VALUES(?,?)",
                       [(manifest.artifact_id, run) for run in runs])
        db.execute("INSERT INTO curation_claims VALUES(?,?,?,?)",
                   ("fixture", "training", manifest.artifact_id, 1))
        db.executemany("INSERT INTO curation_claim_runs VALUES(?,?)",
                       [("fixture", run) for run in runs])
    monkeypatch.setattr(adapter_module, "source_identity", lambda _: PRODUCER)
    request = TrainingRequest("1" * 32, STRUCTURED_RECIPE, manifest.artifact_id,
                              {"epochs": 1}, limits={"wall_seconds": 600})
    service = LocalTrainingService(config)
    return service, request, owner, store


def settle(service):
    assert service._thread is not None
    service._thread.join(timeout=30)
    assert not service._thread.is_alive()
    return service.status()["operation"]


def test_capabilities_are_metadata_only_without_torch_import(tmp_path):
    script = """
import builtins, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'torch' or name.startswith('torch.'):
        raise AssertionError('tensor import during discovery')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.developer import ProjectConfig, combination
from pathlib import Path
service = LocalTrainingService(ProjectConfig(Path(sys.argv[1]), '', '', None, combination()))
value = service.capabilities()
assert any(item['recipe_id'] == 'structured-m2-cpu-v2' for item in value['recipes'])
assert 'torch' not in sys.modules
assert not Path(sys.argv[1]).exists()
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path / "unused")],
                            env=dict(os.environ), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_actual_structured_service_training_result_use_and_idempotency(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    before = set(store.manifest_ids())
    started = service.start(request)["operation"]
    assert started["status"] == "pending"
    completed = settle(service)
    assert completed["status"] == "completed", completed
    assert completed["worker_state"] == "terminal"
    assert completed["validation_state"] == "verified"
    result = store.get_manifest(completed["result_id"])
    assert result.parent("model") == completed["model_id"]
    assert result.parent("checkpoint") == completed["checkpoint_id"]
    assert result.parent("run") == completed["run_id"]
    owner.ledger.require_training_use(request.source_id, {request.source_id},
                                      {"group-1-run"}, completed["operation_id"])
    count = set(store.manifest_ids())
    assert count > before
    assert service.start(request)["operation"]["attempt_id"] == completed["attempt_id"]
    assert set(store.manifest_ids()) == count
    with pytest.raises(BoundaryError, match="intent_payload_mismatch"):
        service.start(replace(request, config={"epochs": 2}))


def test_cancel_ack_safe_checkpoint_and_explicit_resume(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    from stpd.workers import structured_execution

    original = structured_execution.execute_structured_workload
    entered, released = threading.Event(), threading.Event()

    def delayed(*args, **kwargs):
        entered.set()
        assert released.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(structured_execution, "execute_structured_workload", delayed)
    started = service.start(request)["operation"]
    assert entered.wait(10)
    with pytest.raises(BoundaryError, match="stale_operation_attempt"):
        service.cancel(started["operation_id"], "0" * 32)
    ack = service.cancel(started["operation_id"], started["attempt_id"])["operation"]
    assert ack["status"] == "pending" and ack["worker_state"] == "running"
    assert ack["requested_action"] == "cancel"
    with pytest.raises(BoundaryError, match="writer_still_running"):
        service.reconcile(started["operation_id"], started["attempt_id"])
    released.set()
    cancelled = settle(service)
    assert cancelled["status"] == "cancelled", cancelled
    assert cancelled["worker_state"] == "terminal" and cancelled["checkpoint_id"]
    assert "result_id" not in cancelled
    with pytest.raises(BoundaryError, match="cumulative_limits"):
        service.resume(cancelled["operation_id"], cancelled["attempt_id"],
                       cancelled["checkpoint_id"], "2" * 32, {"wall_seconds": 601})
    resumed = service.resume(cancelled["operation_id"], cancelled["attempt_id"],
                             cancelled["checkpoint_id"], "2" * 32, request.limits)["operation"]
    assert resumed["attempt_id"] != cancelled["attempt_id"]
    completed = settle(service)
    assert completed["status"] == "completed", completed
    assert completed["run_id"] == cancelled["run_id"]
    journal = json.loads((owner.path.parent / OPERATION_FILE).read_bytes())
    assert journal["attempts"][-1]["writer_terminal"] is True
    assert completed["elapsed_seconds"] >= cancelled["elapsed_seconds"]


def test_source_json_and_unverified_agent_provenance_cannot_admit_training(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch, source_kind="agent",
                                         qualification="engineering_only")
    before = set(store.manifest_ids())
    with pytest.raises(BoundaryError, match="structured_source_verifier_required"):
        service.start(request)
    assert not (owner.path.parent / OPERATION_FILE).exists()
    assert set(store.manifest_ids()) == before
    with pytest.raises(BoundaryError, match="invalid_recipe_config"):
        service.start(replace(request, config={"program": "/tmp/anything"}))
    with pytest.raises(BoundaryError, match="unsupported_placement"):
        service.start(replace(request, placement_id="/tmp/worker"))


def test_unknown_attempt_requires_reconcile_no_automatic_retry(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    from stpd.workers import structured_execution

    original = structured_execution.execute_structured_workload

    def uncertain(*args, **kwargs):
        # Actual durable safe checkpoint + terminal cancelled event, followed by
        # an injected application publication uncertainty.
        control = kwargs["control"]
        monkeypatch.setattr(control, "requested_action", lambda: "cancel")
        original(*args, **kwargs)
        raise BoundaryError("fixture", "uncertain_after_checkpoint")

    monkeypatch.setattr(structured_execution, "execute_structured_workload", uncertain)
    service.start(request)
    unknown = settle(service)
    assert unknown["status"] == "interrupted_unknown"
    count = set(store.manifest_ids())
    assert service.status()["operation"]["attempt_id"] == unknown["attempt_id"]
    assert set(store.manifest_ids()) == count
    monkeypatch.setattr(structured_execution, "execute_structured_workload", original)
    reconciled = service.reconcile(unknown["operation_id"], unknown["attempt_id"])["operation"]
    assert reconciled["status"] == "pending"
    terminal = settle(service)
    assert terminal["status"] == "interrupted_unknown"
    assert terminal["worker_state"] == "terminal"
    assert terminal["error"]["code"] == "no_completed_result_reconcile_required"
    assert set(store.manifest_ids()) == count


def test_completion_journal_uncertainty_reconciles_without_training(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    original = service._finish_attempt
    once = [False]

    def fail_completion(*args, **kwargs):
        if kwargs.get("status") == "completed" and not once[0]:
            once[0] = True
            raise BoundaryError("fixture", "completion_journal_uncertainty")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_finish_attempt", fail_completion)
    service.start(request)
    unknown = settle(service)
    assert unknown["status"] == "interrupted_unknown"
    before = set(store.manifest_ids())
    service.reconcile(unknown["operation_id"], unknown["attempt_id"])
    completed = settle(service)
    assert completed["status"] == "completed", completed
    assert set(store.manifest_ids()) == before
    assert completed["run_id"] == unknown["run_id"]


def test_cumulative_wall_limit_is_enforced_at_worker_boundary(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    from stpd.workers import structured_execution

    original = structured_execution.execute_structured_workload

    def spent(*args, **kwargs):
        # Live budgets use monotonic time; a wall clock adjustment cannot
        # replenish the already-spent attempt's allowance.
        control = kwargs["control"]
        service._attempt_started_monotonic = time.monotonic()-600.0
        value = service._read(control.path, owner.identity)
        monkeypatch.setattr(adapter_module.time, "time", lambda: value["attempt_started_at"]-1)
        return original(*args, **kwargs)

    monkeypatch.setattr(structured_execution, "execute_structured_workload", spent)
    service.start(request)
    paused = settle(service)
    assert paused["status"] == "paused", paused
    assert paused["progress"]["completed"] == 0
    assert paused["checkpoint_id"]
    with pytest.raises(BoundaryError, match="cumulative_budget_exhausted"):
        service.resume(paused["operation_id"], paused["attempt_id"], paused["checkpoint_id"],
                       "2" * 32, request.limits)


def test_wrong_or_v1_checkpoint_rejected_before_new_attempt(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    from stpd.workers import structured_execution

    original = structured_execution.execute_structured_workload

    def cancelled(*args, **kwargs):
        monkeypatch.setattr(kwargs["control"], "requested_action", lambda: "cancel")
        return original(*args, **kwargs)

    monkeypatch.setattr(structured_execution, "execute_structured_workload", cancelled)
    service.start(request)
    saved = settle(service)
    cp = store.get_manifest(saved["checkpoint_id"])
    wrong = Manifest(cp.kind, cp.producer, cp.parents, cp.payloads,
                     FrozenObject.of({**cp.parameters.value(),
                                      "schema": "stpd/structured-m2-training-checkpoint-v1"}))
    store.publish(wrong)
    journal = (owner.path.parent / OPERATION_FILE).read_bytes()
    with pytest.raises(BoundaryError, match="checkpoint_run_identity_mismatch"):
        service.resume(saved["operation_id"], saved["attempt_id"], wrong.artifact_id,
                       "2" * 32, request.limits)
    assert (owner.path.parent / OPERATION_FILE).read_bytes() == journal


def test_typed_legacy_request_preserves_previous_v1_and_legacy_idempotency(tmp_path, monkeypatch):
    config, dataset_id, _, _store = _ready(tmp_path, monkeypatch)
    service = LocalTrainingService(config)
    service.start(dataset_id)
    legacy = settle(service)
    assert legacy["status"] == "completed", legacy
    request = TrainingRequest("1" * 32, "stage1a.dsimple.s.v1", dataset_id, {}, limits={},
                              after_completed_operation_id=legacy["operation_id"])
    service.start(request)
    typed = settle(service)
    assert typed["status"] == "completed", typed
    assert typed["previous_completed"] == legacy
    assert typed["attempt_id"] and typed["evaluation_id"]
    assert typed["recipe_id"] == "stage1a.dsimple.s.v1"
    assert typed["supported_actions"] == []
    assert service.start(dataset_id)["operation"]["operation_id"] == typed["operation_id"]
    assert service.start(request)["operation"]["attempt_id"] == typed["attempt_id"]


@pytest.mark.parametrize("table", ["curation_claims", "curation_exact_source_index"])
def test_source_claim_and_exact_index_are_required_before_journal_admission(
    tmp_path, monkeypatch, table,
):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    with owner.transaction() as db:
        db.execute("DELETE FROM " + table)
    before = set(store.manifest_ids())
    with pytest.raises(BoundaryError, match="training_claim_mismatch|source_index_incomplete"):
        service.start(request)
    assert not (owner.path.parent / OPERATION_FILE).exists()
    assert set(store.manifest_ids()) == before
