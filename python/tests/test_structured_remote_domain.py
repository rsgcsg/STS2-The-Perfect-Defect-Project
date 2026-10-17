"""Synthetic source-level remote numerics; no provider, image, Human or GPU claims."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest
from test_structured_resume import dataset, equal_tree
from test_structured_s0 import PRODUCER

from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.training_execution_contracts import (
    PreparedTrainingAttempt,
    StopInspection,
    TrainingAppRef,
    TrainingCallHandle,
    TrainingPlacement,
    TrainingSliceLimits,
    training_execution_binding,
)
from spireagent.training_transport import decode_candidate, decode_response
from stpd.models.structured_device import (
    DeviceStructuredTrainingEngine,
    cpu_model_from_checkpoint,
    structured_device_runtime,
    structured_device_session,
)
from stpd.models.structured_engine import StructuredTrainingEngine, execution_identity
from stpd.structured_code_scope import TRAINING_SCOPE
from stpd.structured_remote_contracts import (
    CAPABILITY,
    StructuredDevicePolicy,
    build_structured_remote_request,
    freeze_remote_execution_identity,
    numerical_projection,
    parse_numerical_projection,
    remote_code_closure_sha256,
    sha256,
)
from stpd.structured_workload_contracts import StructuredTrainingConfig, StructuredWorkloadRequest
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.structured_remote_domain import (
    build_structured_projection,
    check_structured_remote_candidate,
    execute_structured_remote_request,
)


def packet(
    *,
    mode="start",
    batch_policy=None,
    data_value=None,
    checkpoint=None,
    checkpoint_id=None,
    attempt_id="d" * 32,
    event_step=0,
    expiry=None,
    max_slice_updates=1,
    run_id="a" * 64,
    training_input_id="b" * 64,
):
    data = dataset() if data_value is None else data_value
    config = StructuredTrainingConfig(epochs=2, max_updates=3)
    policy = StructuredDevicePolicy()
    placement = TrainingPlacement(
        "modal", "synthetic-account", "synthetic-env", "im-synthetic", "none", "cpu"
    )
    limits = TrainingSliceLimits(
        max_slice_updates,
        max_slice_updates,
        30,
        30,
        65536,
        16 * 1024 * 1024,
        16 * 1024 * 1024,
        8,
        2000,
        1024,
        "0" * 64,
        0,
    )
    workload = StructuredWorkloadRequest(
        run_id,
        training_input_id,
        "c" * 32,
        attempt_id,
        mode=mode,
        resume_checkpoint_id=checkpoint_id,
    )
    closure = remote_code_closure_sha256(TRAINING_SCOPE)
    with structured_device_session(policy, cpu_threads=2):
        runtime = FrozenObject.of(structured_device_runtime(policy))
        base = execution_identity(data, config, code_scope=TRAINING_SCOPE)
    producer = replace(PRODUCER, uv_lock_sha256=base["code_identity"]["dependency_lock_sha256"])
    binding = training_execution_binding(producer, closure, CAPABILITY, runtime, placement, limits)
    identity = freeze_remote_execution_identity(
        base,
        data,
        runtime=runtime,
        device_policy=policy,
        execution_binding_id=binding,
        code_closure_sha256=closure,
        batch_policy=batch_policy,
    )
    request = build_structured_remote_request(
        workload,
        execution_identity=identity,
        code_scope=TRAINING_SCOPE,
        device_policy=policy,
        numerical_projection_sha256=sha256(numerical_projection(data)),
        grant_expires_at_ms=expiry if expiry is not None else int(time.time() * 1000) + 60000,
        event_step=event_step,
    )
    projection = build_structured_projection(
        data, workload, maximum=limits.max_projection_bytes, resume_checkpoint=checkpoint
    )
    prepared = PreparedTrainingAttempt(
        workload.run_id,
        workload.training_input_id,
        workload.operation_id,
        workload.attempt_id,
        sha256(request),
        sha256(projection),
        producer,
        closure,
        CAPABILITY,
        runtime,
        placement,
        limits,
    )
    ref = TrainingAppRef(
        prepared.identity,
        "ap-synthetic",
        "fu-synthetic",
        prepared.app_name,
        placement.account_id,
        placement.environment,
        placement.image_object_id,
    )
    handle = TrainingCallHandle(ref, "fc-synthetic")
    return data, config, identity, prepared, request, projection, handle


def test_train_projection_omits_original_bytes_and_preserves_exact_numeric_frames():
    data = dataset()
    raw = numerical_projection(data)
    assert (
        b'"teacher"' not in raw and b'"source_bytes"' not in raw and b'"capsule_sha256"' not in raw
    )
    projected = parse_numerical_projection(raw)
    assert projected.source_bytes == b""
    assert projected.source_sha256 == data.source_sha256
    assert [
        [(step.frame, step.chosen_action_id, step.advance, step.reset_before) for step in run.steps]
        for run in projected.runs
    ] == [
        [(step.frame, step.chosen_action_id, step.advance, step.reset_before) for step in run.steps]
        for run in data.runs
        if run.split == "train"
    ]


def test_explicit_policy_refuses_precision_fallback_and_request_has_no_unbounded_grant():
    for kwargs in (
        {"device": "cuda:1"},
        {"dtype": "float16"},
        {"allow_tf32": True},
        {"deterministic_algorithms": False},
        {"cudnn_benchmark": True},
    ):
        with pytest.raises(BoundaryError):
            StructuredDevicePolicy(**kwargs)
    with pytest.raises(BoundaryError, match="finite_grant_expiry"):
        packet(expiry=0)


def test_cpu_adapter_preserves_original_chunk_numerics_and_portable_checkpoint():
    data, config, identity, _, _, _, _ = packet()
    policy = StructuredDevicePolicy()
    projected = parse_numerical_projection(numerical_projection(data))
    with structured_device_session(policy, cpu_threads=2):
        reference = StructuredTrainingEngine(data, config, code_scope=TRAINING_SCOPE)
        with pytest.raises(BoundaryError, match="declared_graph_control_scope"):
            DeviceStructuredTrainingEngine(
                projected,
                config,
                code_scope=TRAINING_SCOPE,
                identity=dict(identity, graph_id="forged-graph"),
                policy=policy,
            )
        remote = DeviceStructuredTrainingEngine(
            projected, config, code_scope=TRAINING_SCOPE, identity=identity, policy=policy
        )
        reference.advance_chunk()
        remote.advance_chunk()
        equal_tree(reference.model.state_dict(), remote.model.state_dict())
        equal_tree(reference.optimizer.state_dict(), remote.optimizer.state_dict())
        raw = remote.checkpoint()
        assert decode_checkpoint(raw)["device_rng"] is None
        restored = DeviceStructuredTrainingEngine(
            projected, config, code_scope=TRAINING_SCOPE, identity=identity, policy=policy
        )
        restored.restore(raw)
        remote.advance_chunk()
        restored.advance_chunk()
        equal_tree(remote.model.state_dict(), restored.model.state_dict())
        equal_tree(remote.optimizer.state_dict(), restored.optimizer.state_dict())
        equal_tree(remote.memory, restored.memory)
        exported = cpu_model_from_checkpoint(restored.checkpoint(), identity)
        equal_tree(exported.state_dict(), restored.model.state_dict())
        wrong = dict(
            identity, training_device_policy=StructuredDevicePolicy(device="cuda:0").to_dict()
        )
        with pytest.raises(BoundaryError, match="export_checkpoint_identity"):
            cpu_model_from_checkpoint(raw, wrong)


def test_actual_slice_is_bounded_and_parent_acceptance_requires_exact_stopped_provider():
    _, _, _, prepared, request, projection, handle = packet()
    raw = execute_structured_remote_request(prepared, request, projection)
    candidate = decode_response(raw, prepared, handle)
    assert candidate.counters.value() == {"optimizer_updates": 1, "boundaries": 1}
    stop = StopInspection(handle.app_ref, "stopped", 0, 0)
    checked = check_structured_remote_candidate(prepared, request, projection, candidate, stop)
    assert checked.state["counters"]["updates"] == 1
    assert checked.training_complete is False
    for bad in (
        StopInspection(handle.app_ref, "unknown", 0, 0),
        StopInspection(handle.app_ref, "stopped", 1, 0),
    ):
        with pytest.raises(BoundaryError, match="exact_provider_stop"):
            check_structured_remote_candidate(prepared, request, projection, candidate, bad)
    with pytest.raises(BoundaryError, match="slice_counter"):
        check_structured_remote_candidate(
            prepared,
            request,
            projection,
            replace(candidate, counters=FrozenObject.of({"optimizer_updates": 0, "boundaries": 1})),
            stop,
        )
    _, _, _, resumed, req2, proj2, handle2 = packet(
        mode="resume",
        checkpoint=checked.checkpoint.payloads[0],
        checkpoint_id=checked.checkpoint.manifest.artifact_id,
        attempt_id="e" * 32,
        event_step=1,
    )
    result2 = decode_response(
        execute_structured_remote_request(resumed, req2, proj2), resumed, handle2
    )
    checked2 = check_structured_remote_candidate(
        resumed, req2, proj2, result2, StopInspection(handle2.app_ref, "stopped", 0, 0)
    )
    assert checked2.state["counters"]["updates"] == 2
    assert result2.counters.value()["optimizer_updates"] == 1


def test_expired_grant_and_projection_tamper_execute_no_numeric_work():
    _, _, _, prepared, request, projection, _ = packet(expiry=1)
    with pytest.raises(BoundaryError, match="grant_expired"):
        execute_structured_remote_request(prepared, request, projection)
    with pytest.raises(BoundaryError, match="payload_mismatch"):
        execute_structured_remote_request(prepared, request, projection + b"tamper")


def test_candidate_codec_cannot_turn_forged_counters_into_accepted_checkpoint():
    from spireagent.artifact_contracts import Manifest, Payload
    from spireagent.training_transport import CandidateArtifact, encode_candidate

    _, _, _, prepared, request, projection, handle = packet()
    candidate = decode_response(
        execute_structured_remote_request(prepared, request, projection), prepared, handle
    )
    artifacts = decode_candidate(candidate.candidate_bytes, prepared)
    checkpoint = next(item for item in artifacts if item.manifest.kind == "checkpoint")
    event = next(item for item in artifacts if item.manifest.kind == "run_event")
    state = decode_checkpoint(checkpoint.payloads[0])
    state["counters"]["updates"] = 0
    raw = encode_checkpoint(state)
    payload = Payload("checkpoint", sha256(raw), len(raw), "application/vnd.stpd.tensor-tree")
    forged = CandidateArtifact(
        Manifest(
            "checkpoint",
            prepared.producer,
            checkpoint.manifest.parents,
            (payload,),
            checkpoint.manifest.parameters,
        ),
        (raw,),
    )
    result = replace(candidate, candidate_bytes=encode_candidate(prepared, (forged, event)))
    with pytest.raises(BoundaryError, match="candidate_counters"):
        check_structured_remote_candidate(
            prepared, request, projection, result, StopInspection(handle.app_ref, "stopped", 0, 0)
        )


def test_parent_uses_existing_exporter_and_reporter_after_full_numeric_validation(tmp_path):
    from spireagent.artifact_contracts import Manifest
    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.run_reporter import ObjectStoreRunReporter
    from spireagent.storage.store import ManifestArtifactStore
    from stpd.policy.structured_export import load_structured_package
    from stpd.workers.structured_execution import prepare_structured_workload
    from stpd.workers.structured_remote_domain import publish_structured_remote_candidate

    data, config, identity, prepared, _, _, _ = packet(max_slice_updates=3)
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    reporter = ObjectStoreRunReporter(store, store.blobs)
    with structured_device_session(StructuredDevicePolicy(), cpu_threads=2):
        base = prepare_structured_workload(
            store,
            data,
            prepared.producer,
            config,
            operation_id=prepared.operation_id,
            code_scope=TRAINING_SCOPE,
        )
    run = Manifest(
        "run",
        prepared.producer,
        base.parents,
        base.payloads,
        FrozenObject.of(
            {
                **base.parameters.value(),
                "execution_identity": identity,
                "torch_version": identity["runtime"]["torch_version"],
            }
        ),
    )
    store.publish(run)
    _, _, _, prepared, request, projection, handle = packet(
        max_slice_updates=3, run_id=run.artifact_id, training_input_id=run.parent("training_input")
    )
    candidate = decode_response(
        execute_structured_remote_request(prepared, request, projection), prepared, handle
    )
    validated = check_structured_remote_candidate(
        prepared, request, projection, candidate, StopInspection(handle.app_ref, "stopped", 0, 0)
    )

    class ParentAuthority:
        def assert_current(self, run_id, operation_id, attempt_id):
            assert (run_id, operation_id, attempt_id) == (
                prepared.run_id,
                prepared.operation_id,
                prepared.attempt_id,
            )

    result = publish_structured_remote_candidate(
        store,
        reporter,
        validated,
        prepared,
        run,
        data,
        code_scope=TRAINING_SCOPE,
        authority=ParentAuthority(),
    )
    assert result["optimizer_updates"] == 3
    assert reporter.completed(run.artifact_id).artifact_id == result["result_id"]
    model = store.get_manifest(result["model_id"])
    package = tmp_path / "exported"
    package.mkdir()
    for role, filename in (("package_manifest", "model.json"), ("weights", "weights.tensor-tree")):
        (package / filename).write_bytes(b"".join(store.read_payload(model.payload(role))))
    metadata, restored = load_structured_package(package)
    assert metadata["training"]["metrics"]["optimizer_updates"] == 3
    assert metadata["training"]["metrics"]["cpu_gpu_bitwise_parity"] is False
    equal_tree(restored.state_dict(), validated.state["model"])
