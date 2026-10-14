"""Real structured recipe and TrainingService with synthetic numerics and fake provider."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, replace

import pytest
from test_protocol_source import PROJECTION, publish_run, setup_store
from test_remote_training_application import Provider

from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.training_execution_contracts import (
    CandidateResult,
    TrainingPlacement,
    TrainingSliceLimits,
)
from spireagent.training_transport import decode_response
from spireagent.workbench import local_training as local_module
from spireagent.workbench.developer import ProjectConfig
from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.recipe_contracts import TrainingRequest, checked_training_plan
from spireagent.workbench.recipes import structured as recipe_module
from spireagent.workbench.remote_training_operation import (
    ConfiguredTrainingTarget,
    RemoteTrainingBinding,
)
from spireagent.workbench.trusted_recipes import STRUCTURED_SCOPED_RECIPE
from stpd.fullrun.protocol_source import publish_protocol_source_partition
from stpd.models.structured_device import structured_device_runtime, structured_device_session
from stpd.structured_code_scope import ROOT, TRAINING_SCOPE
from stpd.structured_remote_contracts import (
    CAPABILITY,
    StructuredDevicePolicy,
    remote_code_closure_sha256,
)
from stpd.structured_workload_contracts import StructuredBatchPolicy, StructuredTrainingConfig
from stpd.workers.structured_remote_domain import execute_structured_remote_request


class NumericProvider(Provider):
    """Provider truth is fake; submitted projection uses the real closed numerical handler."""

    def submit_once(self, attempt, request, projection, ref):
        self.request, self.projection = request, projection
        return super().submit_once(attempt, request, projection, ref)

    def candidate(self, attempt):
        result = decode_response(
            execute_structured_remote_request(attempt, self.request, self.projection),
            attempt,
            self.handle,
        )
        return CandidateResult(
            self.handle,
            result.candidate_bytes,
            result.finish_reason,
            result.runtime,
            result.counters,
        )


def real_service(tmp_path, monkeypatch, *, lanes=2):
    store, owner = setup_store(tmp_path)
    refs = tuple(publish_run(store, tmp_path, str(index), str(index))[0] for index in (1, 2, 3))
    source = publish_protocol_source_partition(store, refs, "train", PROJECTION)
    owner.reserve_verified_protocol_source(store, source.manifest.artifact_id)
    producer = replace(
        PROJECTION, uv_lock_sha256=hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
    )
    monkeypatch.setattr(local_module, "source_identity", lambda _: producer)
    monkeypatch.setattr(recipe_module, "source_identity", lambda _: producer)
    with structured_device_session(StructuredDevicePolicy(), cpu_threads=2):
        runtime = FrozenObject.of(structured_device_runtime(StructuredDevicePolicy()))
    target = ConfiguredTrainingTarget(
        "modal-cpu",
        STRUCTURED_SCOPED_RECIPE,
        CAPABILITY,
        TrainingPlacement("modal", "synthetic-account", "test", "im-fixture", "none", "cpu"),
        runtime,
        producer,
        remote_code_closure_sha256(TRAINING_SCOPE),
        "a" * 64,
        TrainingSliceLimits(
            1, 1, 30, 30, 65536, 16 * 1024**2, 16 * 1024**2, 8, 2000, 1024, "b" * 64, 0
        ),
        0,
        1,
    )
    provider = NumericProvider()
    service = LocalTrainingService(
        ProjectConfig(tmp_path / "state", "", "", None, {}),
        remote_bindings=(RemoteTrainingBinding(target, provider.bind),),
    )
    monkeypatch.setattr(service, "_selected", lambda: (owner, store, tmp_path / "registry.sqlite"))
    config = asdict(StructuredTrainingConfig(epochs=2, max_updates=3))
    request = TrainingRequest(
        "1" * 32,
        STRUCTURED_SCOPED_RECIPE,
        source.manifest.artifact_id,
        config,
        "modal-cpu",
        {"wall_seconds": 180},
        batch_policy=StructuredBatchPolicy(lanes).to_dict(),
    )
    return service, request, store, provider


def settle(service):
    service._thread.join(20)
    assert not service._thread.is_alive()
    return service.status()["operation"]


def test_training_service_real_batch_recipe_slice_checkpoint_resume_complete(tmp_path, monkeypatch):
    service, request, store, provider = real_service(tmp_path, monkeypatch)
    service.start(request)
    operation = settle(service)
    assert operation["status"] == "paused", operation
    assert operation["accepted_counters"]["optimizer_updates"] == 1
    assert operation["accepted_counters"]["label_uses"] == 4
    assert checked_training_plan(operation["training_plan"])["batch_policy"]["lanes"] == 2
    run_id = operation["run_id"]
    for intent in ("2", "3"):
        service.resume(
            operation["operation_id"],
            operation["attempt_id"],
            operation["checkpoint_id"],
            intent_id=intent * 32,
            limits=request.limits,
        )
        operation = settle(service)
    assert operation["status"] == "completed", operation
    assert (
        operation["run_id"] == run_id and operation["accepted_counters"]["optimizer_updates"] == 3
    )
    assert provider.submit_calls == 3
    assert store.get_manifest(operation["model_id"]).parent("run") == run_id
    assert store.get_manifest(operation["result_id"]).parent("run") == run_id


def test_cancel_existing_attempt_retains_checkpoint_and_explicit_resume(tmp_path, monkeypatch):
    service, request, store, provider = real_service(tmp_path, monkeypatch)
    provider.hold_poll = True
    service.start(request)
    assert provider.cancel_entered.wait(10)
    active = service.status()["operation"]
    service.cancel(active["operation_id"], active["attempt_id"])
    provider.release_poll.set()
    cancelled = settle(service)
    assert cancelled["status"] == "cancelled" and cancelled["worker_state"] == "terminal", cancelled
    assert cancelled["checkpoint_id"]
    provider.hold_poll = False
    service.resume(
        cancelled["operation_id"],
        cancelled["attempt_id"],
        cancelled["checkpoint_id"],
        intent_id="4" * 32,
        limits=request.limits,
    )
    resumed = settle(service)
    assert resumed["run_id"] == cancelled["run_id"]
    assert resumed["accepted_counters"]["optimizer_updates"] == 2
    assert provider.submit_calls == 2


def test_batch_is_separate_from_native_policy_and_requires_explicit_supported_placement():
    batch = StructuredBatchPolicy(2).to_dict()
    request = TrainingRequest(
        "1" * 32, STRUCTURED_SCOPED_RECIPE, "2" * 64, {}, "modal-cpu", batch_policy=batch
    )
    assert TrainingRequest.from_dict(request.to_dict()).batch_policy == batch
    assert request.execution_policy is None
    with pytest.raises(BoundaryError, match="batch_remote_structured"):
        TrainingRequest(
            "1" * 32, STRUCTURED_SCOPED_RECIPE, "2" * 64, {}, batch_policy=batch
        ).validate()
