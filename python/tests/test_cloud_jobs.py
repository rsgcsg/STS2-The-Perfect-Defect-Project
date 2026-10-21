from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

import pytest
from test_artifact_store_v1 import PRODUCER
from test_fullrun_features import prepared

from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.cloud_jobs.contracts import (
    ComputeReceipt,
    ComputeRequest,
    FeatureJobSpec,
    load_feature_job,
    publish_feature_job,
)
from stpd.cloud_jobs.execution import (
    CandidateReporter,
    dispatch,
    prepare_feature_run,
    validate_receipt,
)
from stpd.fullrun.features import compile_features
from stpd.qwen.fake_backend import DeterministicFakeQwenBackend
from stpd.workers.contracts import TrainingConfig
from stpd.workers.worker import WorkerExecutionError


def job_fixture(tmp_path: Path):
    store, dataset, view = prepared(tmp_path)
    backend = DeterministicFakeQwenBackend(8)
    spec = FeatureJobSpec(
        FrozenObject.of(asdict(backend.identity)),
        TrainingConfig(seed=17, max_steps=3, checkpoint_interval=1),
    )
    job = publish_feature_job(store, view.artifact_id, PRODUCER, spec)
    request = ComputeRequest("features", job.artifact_id, PRODUCER, "features-attempt-1")
    return store, backend, spec, job, request


def test_cpu_controller_freezes_job_without_encoding_then_worker_publishes(tmp_path: Path) -> None:
    store, backend, spec, job, request = job_fixture(tmp_path)
    assert all(store.get_manifest(i).kind != "feature_set" for i in store.manifest_ids())
    assert FeatureJobSpec.decode(spec.to_dict()) == spec
    assert ComputeRequest.decode(request.to_dict()) == request
    receipt = dispatch(store, request, PRODUCER, backend=backend)
    raw_receipt = receipt.to_dict()
    assert ComputeReceipt.decode(raw_receipt) == receipt
    assert raw_receipt == receipt.to_dict()
    validate_receipt(store, request, receipt)
    assert receipt.state == "candidate_prepared" and receipt.output_id
    pipeline = prepare_feature_run(store, job.artifact_id, receipt.output_id, PRODUCER)
    assert pipeline.feature_job_id == job.artifact_id
    assert store.get_manifest(pipeline.training_input_id).parameters.value()["config"] == (
        spec.training.to_dict()
    )
    full_request = ComputeRequest("training", pipeline.run_id, PRODUCER, "train-full")
    finished = dispatch(store, full_request, PRODUCER)
    validate_receipt(store, full_request, finished)
    # The remote worker can prepare a candidate, but it cannot win a shared
    # completion slot. Hub lease/fence admission remains a separate transaction.
    assert ObjectStoreRunReporter(store, store.blobs).completed(pipeline.run_id) is None
    events = CandidateReporter(store, pipeline.run_id, PRODUCER).events(pipeline.run_id)
    assert all(
        event.parameters.value()["details"]["compute_request_id"] == full_request.request_id
        for event in events
    )
    assert finished.output_id
    result = store.get_manifest(finished.output_id)
    assert result.parent("training_input") == pipeline.training_input_id
    assert result.parameters.value()["scientific_verdict"] == "not_claimed"


def test_explicit_resume_retains_run_identity_and_matches_uninterrupted(tmp_path: Path) -> None:
    store, backend, _, job, request = job_fixture(tmp_path)
    features = dispatch(store, request, PRODUCER, backend=backend)
    pipeline = prepare_feature_run(store, job.artifact_id, features.output_id, PRODUCER)
    full_request = ComputeRequest("training", pipeline.run_id, PRODUCER, "uninterrupted")
    full = dispatch(store, full_request, PRODUCER)
    pause_request = replace(full_request, attempt_id="pause", stop_after=1)
    paused = dispatch(store, pause_request, PRODUCER)
    validate_receipt(store, pause_request, paused)
    assert paused.state == "paused" and paused.checkpoint_id
    resume_request = replace(full_request, attempt_id="replacement", resume=paused.checkpoint_id)
    resumed = dispatch(store, resume_request, PRODUCER)
    validate_receipt(store, resume_request, resumed)
    assert resumed.output_id == full.output_id
    with pytest.raises(BoundaryError, match="request_binding"):
        validate_receipt(store, full_request, resumed)
    foreign = prepare_feature_run(
        store, job.artifact_id, features.output_id, PRODUCER, replicate="foreign"
    )
    with pytest.raises(WorkerExecutionError, match="resume_identity"):
        dispatch(store, replace(resume_request, input_id=foreign.run_id), PRODUCER)


def test_source_qwen_dimensions_and_future_schema_fail_before_encoding(tmp_path: Path) -> None:
    store, backend, _, job, request = job_fixture(tmp_path)
    wrong_source = replace(PRODUCER, source_revision="c" * 40)
    with pytest.raises(BoundaryError, match="runtime_source_lock"):
        dispatch(store, request, wrong_source, backend=backend)
    with pytest.raises(BoundaryError, match="source_lock"):
        load_feature_job(store, job.artifact_id, wrong_source)
    with pytest.raises(BoundaryError, match="backend_identity_or_dimension"):
        dispatch(store, request, PRODUCER, backend=DeterministicFakeQwenBackend(9))
    backend.identity = replace(backend.identity, model_revision="tampered")
    with pytest.raises(BoundaryError, match="backend_identity_or_dimension"):
        dispatch(store, request, PRODUCER, backend=backend)
    with pytest.raises(BoundaryError, match="unsupported_schema"):
        ComputeRequest.decode({**request.to_dict(), "schema": "future"})
    with pytest.raises(BoundaryError, match="feature_resume_not_supported"):
        replace(request, resume="a" * 64)
    assert all(store.get_manifest(i).kind != "feature_set" for i in store.manifest_ids())


def test_feature_reuse_revalidates_expected_backend_and_output_bytes(tmp_path: Path) -> None:
    store, backend, _, job, request = job_fixture(tmp_path)
    wrong = compile_features(
        store, job.parent("model_view"), DeterministicFakeQwenBackend(9), PRODUCER
    )
    with pytest.raises(BoundaryError, match="feature_output_mismatch"):
        prepare_feature_run(store, job.artifact_id, wrong.artifact_id, PRODUCER)
    receipt = dispatch(store, request, PRODUCER, backend=backend)
    feature = store.get_manifest(receipt.output_id)
    index = store.blobs.root / "payload-indexes" / "v1"
    (index / f"{feature.payload('features').sha256}.json").unlink()
    with pytest.raises(BoundaryError, match="object_not_found"):
        validate_receipt(store, request, receipt)


def test_candidate_reporter_rejects_foreign_run_and_tampered_receipt(tmp_path: Path) -> None:
    store, backend, _, job, request = job_fixture(tmp_path)
    features = dispatch(store, request, PRODUCER, backend=backend)
    pipeline = prepare_feature_run(store, job.artifact_id, features.output_id, PRODUCER)
    training = ComputeRequest("training", pipeline.run_id, PRODUCER, "attempt")
    receipt = dispatch(store, training, PRODUCER)
    reporter = CandidateReporter(store, "a" * 64, PRODUCER)
    with pytest.raises(BoundaryError, match="foreign_run_or_source"):
        reporter.complete(store.get_manifest(receipt.output_id))
    result = store.get_manifest(receipt.output_id)
    params = result.parameters.value()
    params["steps"] += 1
    bad = replace(result, parameters=FrozenObject.of(params))
    store.publish(bad)
    with pytest.raises(BoundaryError, match="completion_step_mismatch"):
        validate_receipt(store, training, replace(receipt, output_id=bad.artifact_id))


def test_receipt_rejects_rehashed_model_weights_and_paused_checkpoint_content(tmp_path: Path):
    import torch
    from safetensors.torch import load, save

    store, backend, _, job, feature_request = job_fixture(tmp_path)
    feature = dispatch(store, feature_request, PRODUCER, backend=backend)
    pipeline = prepare_feature_run(store, job.artifact_id, feature.output_id, PRODUCER)
    request = ComputeRequest("training", pipeline.run_id, PRODUCER, "full")
    receipt = dispatch(store, request, PRODUCER)
    result = store.get_manifest(receipt.output_id)
    model = store.get_manifest(result.parent("model"))
    tensors = load(store.bytes(model.payload("weights")))
    altered = {key: value + torch.ones_like(value) for key, value in tensors.items()}
    weights = store.put_bytes("weights", save(altered), "application/x-safetensors")
    forged_model = replace(model, payloads=(weights,))
    store.publish(forged_model)
    forged_result = replace(result, parents=tuple(
        replace(parent, artifact_id=forged_model.artifact_id) if parent.role == "model" else parent
        for parent in result.parents
    ))
    store.publish(forged_result)
    with pytest.raises(BoundaryError, match="model_checkpoint_weights_mismatch"):
        validate_receipt(store, request, replace(receipt, output_id=forged_result.artifact_id))

    paused_request = replace(request, attempt_id="pause", stop_after=1)
    paused = dispatch(store, paused_request, PRODUCER)
    checkpoint = store.get_manifest(paused.checkpoint_id)
    info = checkpoint.parameters.value()
    info["step"] = 2  # Actual durable checkpoint still contains step 1.
    forged_checkpoint = replace(checkpoint, parameters=FrozenObject.of(info))
    store.publish(forged_checkpoint)
    with pytest.raises(BoundaryError, match="checkpoint_content_mismatch"):
        validate_receipt(
            store, paused_request, replace(paused, checkpoint_id=forged_checkpoint.artifact_id)
        )


def test_paused_checkpoint_cannot_forge_a_self_consistent_different_training_plan(tmp_path: Path):
    from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint

    store, backend, _, job, features_request = job_fixture(tmp_path)
    feature = dispatch(store, features_request, PRODUCER, backend=backend)
    pipeline = prepare_feature_run(store, job.artifact_id, feature.output_id, PRODUCER)
    request = ComputeRequest("training", pipeline.run_id, PRODUCER, "pause", stop_after=1)
    paused = dispatch(store, request, PRODUCER)
    checkpoint = store.get_manifest(paused.checkpoint_id)
    state = decode_checkpoint(store.bytes(checkpoint.payload("checkpoint")))
    info = checkpoint.parameters.value()
    state["data_identity"] = info["data_identity"] = "f" * 64
    state["total_steps"] = info["total_steps"] = 999
    forged_payload = store.put_bytes(
        "checkpoint", encode_checkpoint(state), "application/vnd.stpd.tensor-tree"
    )
    forged = replace(checkpoint, payloads=(forged_payload,), parameters=FrozenObject.of(info))
    store.publish(forged)
    with pytest.raises(BoundaryError, match="checkpoint_content_mismatch"):
        validate_receipt(store, request, replace(paused, checkpoint_id=forged.artifact_id))


def test_candidate_reporter_uses_original_declared_event_index(tmp_path: Path) -> None:
    from spireagent.artifact_contracts import Manifest, Parent
    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import ManifestArtifactStore

    store = ManifestArtifactStore(LocalBlobStore(tmp_path))
    run = Manifest("run", PRODUCER,
                   parameters=FrozenObject.of({"event_index": "run-events-v1"}))
    store.publish(run)
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "indexed-attempt")
    reporter = CandidateReporter(store, run.artifact_id, PRODUCER, request=request)
    event = Manifest("run_event", PRODUCER, (Parent("run", run.artifact_id),),
                     parameters=FrozenObject.of({"schema": "stpd/run-event-v1", "step": 1}))
    identity = reporter.emit(event)
    store.blobs.put_if_absent("manifests/" + "a" * 64 + ".json", b"unrelated bad manifest")
    events = reporter.events(run.artifact_id)
    assert len(events) == 1 and events[0].artifact_id == identity
    assert events[0].parameters.value()["details"] == {
        "compute_request_id": request.request_id, "compute_attempt_id": request.attempt_id}
    assert ObjectStoreRunReporter(store, store.blobs).events(run.artifact_id) == events
    assert reporter.completed(run.artifact_id) is None  # Still no shared completion authority.
