"""Closed finite Structured M2 numeric handler and parent-side candidate validator.

No source archive replay, canonical Store/Reporter, current-journal impersonation,
provider call, arbitrary callable, final selection or automatic retry lives here.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import torch
from torch import Tensor

from spireagent.artifact_contracts import Manifest, Parent, Payload
from spireagent.json_boundary import BoundaryError, FrozenObject, object_fields
from spireagent.training_execution_contracts import (
    CandidateResult,
    PreparedTrainingAttempt,
    StopInspection,
)
from spireagent.training_transport import (
    CandidateArtifact,
    ProjectionPart,
    decode_candidate,
    decode_projection,
    encode_candidate,
    encode_projection,
    encode_response,
)

from ..canonical import semantic_hash
from ..models.structured_batch import remote_training_engine
from ..models.structured_device import (
    DeviceStructuredTrainingEngine,
    cpu_model_from_checkpoint,
    structured_device_runtime,
    structured_device_session,
)
from ..models.structured_engine import MAX_CHECKPOINT_BYTES, _plans
from ..models.structured_m2 import WIDTH, validate_structured_frame
from ..structured_remote_contracts import (
    CAPABILITY,
    CHECKPOINT_SCHEMA,
    StructuredDevicePolicy,
    numerical_projection,
    parse_numerical_projection,
    parse_structured_remote_request,
    remote_code_closure_sha256,
    sha256,
)
from ..structured_workload_contracts import StructuredTrainingConfig, StructuredWorkloadRequest
from .checkpoint_codec import decode_checkpoint

STAGE = "structured_remote"


def build_structured_projection(
    dataset: Any,
    workload: StructuredWorkloadRequest,
    *,
    maximum: int,
    resume_checkpoint: bytes | None = None,
) -> bytes:
    workload.validate()
    if (workload.mode == "resume") != (resume_checkpoint is not None):
        raise BoundaryError(STAGE, "explicit_resume_payload_required")
    parts: tuple[ProjectionPart, ...] = (
        ProjectionPart("training_projection", numerical_projection(dataset)),)
    if resume_checkpoint is not None:
        if (
            type(resume_checkpoint) is not bytes
            or not 0 < len(resume_checkpoint) <= MAX_CHECKPOINT_BYTES
        ):
            raise BoundaryError(STAGE, "resume_checkpoint_limit")
        parts += (ProjectionPart("resume_checkpoint", resume_checkpoint),)
    identity = {
        name: getattr(workload, name)
        for name in ("run_id", "training_input_id", "operation_id", "attempt_id")
    }
    return encode_projection(identity, parts, maximum)


def _inputs(
    attempt: PreparedTrainingAttempt, request: bytes, projection: bytes
) -> tuple[dict[str, Any], StructuredWorkloadRequest, Any, StructuredTrainingConfig,
           StructuredDevicePolicy, dict[str, bytes]]:
    attempt.bind_payloads(request, projection)
    if attempt.worker_capability != CAPABILITY:
        raise BoundaryError(STAGE, "unsupported_closed_capability")
    body = parse_structured_remote_request(request)
    workload = StructuredWorkloadRequest.from_dict(body["workload"])
    if any(
        getattr(workload, name) != getattr(attempt, name)
        for name in ("run_id", "training_input_id", "operation_id", "attempt_id")
    ):
        raise BoundaryError(STAGE, "request_attempt_identity_mismatch")
    parts = {part.role: part.raw for part in decode_projection(projection, attempt)}
    if (workload.mode == "resume") != ("resume_checkpoint" in parts):
        raise BoundaryError(STAGE, "explicit_resume_payload_required")
    dataset = parse_numerical_projection(parts["training_projection"])
    for run in dataset.runs:
        for step in run.steps:
            validate_structured_frame(step.frame)
    policy = StructuredDevicePolicy.from_dict(body["device_policy"])
    identity = body["execution_identity"]
    if (
        not isinstance(identity, dict)
        or identity.get("engine") != "structured-remote-tbptt-v1"
        or identity.get("runtime") != attempt.runtime.value()
        or not isinstance(identity.get("code_identity"), dict)
        or identity["code_identity"].get("dependency_lock_sha256")
        != attempt.producer.uv_lock_sha256
        or identity.get("training_device_policy") != policy.to_dict()
        or identity.get("execution_binding_id") != attempt.execution_binding_id
        or identity.get("remote_code_closure_sha256") != attempt.code_closure_sha256
        or remote_code_closure_sha256(body["code_scope"]) != attempt.code_closure_sha256
        or identity.get("source_sha256") != dataset.source_sha256
        or identity.get("numerical_projection_sha256") != sha256(parts["training_projection"])
        or body["numerical_projection_sha256"] != sha256(parts["training_projection"])
        or policy.device != attempt.placement.device
    ):
        raise BoundaryError(STAGE, "frozen_run_target_projection_mismatch")
    config = StructuredTrainingConfig(**identity["config"])
    config.validate()
    return body, workload, dataset, config, policy, parts


def _checkpoint_artifact(
    attempt: PreparedTrainingAttempt,
    workload: StructuredWorkloadRequest,
    body: dict[str, Any],
    engine: DeviceStructuredTrainingEngine,
    raw: bytes,
) -> CandidateArtifact:
    parents: tuple[Parent, ...] = (
        Parent("run", attempt.run_id), Parent("training_input", attempt.training_input_id))
    if workload.resume_checkpoint_id is not None:
        parents += (Parent("resume_checkpoint", workload.resume_checkpoint_id),)
    payload = Payload("checkpoint", sha256(raw), len(raw), "application/vnd.stpd.tensor-tree")
    parameters = {
        "schema": CHECKPOINT_SCHEMA,
        "execution_identity_sha256": semantic_hash(body["execution_identity"]),
        "boundary": engine.boundary,
        "cursor": asdict(engine.cursor),
        "phase": engine.phase,
        "optimizer_updates": engine.updates,
        "attempt": attempt.attempt_id,
        "attempt_producer": attempt.producer.to_dict(),
        "operation_id": attempt.operation_id,
        "prepared_id": attempt.identity,
        "execution_binding_id": attempt.execution_binding_id,
        "numerical_projection_sha256": body["numerical_projection_sha256"],
    }
    return CandidateArtifact(
        Manifest("checkpoint", attempt.producer, parents, (payload,), FrozenObject.of(parameters)),
        (raw,),
    )


def execute_structured_remote_request(
    attempt: PreparedTrainingAttempt,
    request: bytes,
    projection: bytes,
) -> bytes:
    """At most one finite slice; expiry checks precede every new TBPTT chunk."""
    started = time.monotonic()
    body, workload, dataset, config, policy, parts = _inputs(attempt, request, projection)
    expiry = body["grant_expires_at_ms"] / 1000
    if time.time() >= expiry:
        raise BoundaryError(STAGE, "projection_grant_expired")
    with structured_device_session(policy, cpu_threads=config.cpu_threads):
        if structured_device_runtime(policy) != attempt.runtime.value():
            raise BoundaryError(STAGE, "actual_runtime_mismatch")
        engine = remote_training_engine(
            dataset,
            config,
            code_scope=body["code_scope"],
            identity=body["execution_identity"],
            policy=policy,
        )
        if "resume_checkpoint" in parts:
            engine.restore(parts["resume_checkpoint"])
        before_updates, before_boundaries = engine.updates, engine.boundary
        while not engine.training_complete:
            if (
                engine.updates - before_updates >= attempt.limits.max_optimizer_updates
                or engine.boundary - before_boundaries >= attempt.limits.max_boundaries
                or time.monotonic() - started >= attempt.limits.wall_seconds
                or time.time() >= expiry
            ):
                break
            engine.advance_chunk()
        if engine.boundary == before_boundaries:
            raise BoundaryError(STAGE, "no_new_numeric_boundary")
        raw = engine.checkpoint()
        checkpoint = _checkpoint_artifact(attempt, workload, body, engine, raw)
        event = CandidateArtifact(
            Manifest(
                "run_event",
                attempt.producer,
                (Parent("run", attempt.run_id),),
                parameters=FrozenObject.of(
                    {
                        "schema": "stpd/run-event-v1",
                        "step": body["event_step"] + 1,
                        "kind": "checkpoint",
                        "attempt": attempt.attempt_id,
                        "details": {
                            "checkpoint_id": checkpoint.manifest.artifact_id,
                            "optimizer_updates": engine.updates,
                            "attempt_producer": attempt.producer.to_dict(),
                        },
                    }
                ),
            ),
            (),
        )
        candidate = encode_candidate(attempt, (checkpoint, event))
        counters = FrozenObject.of(
            {
                "optimizer_updates": engine.updates - before_updates,
                "boundaries": engine.boundary - before_boundaries,
            }
        )
        return encode_response(
            attempt,
            candidate=candidate,
            runtime=attempt.runtime,
            counters=counters,
            finish_reason="workload_complete" if engine.training_complete else "slice_boundary",
        )


@dataclass(frozen=True)
class ValidatedStructuredCandidate:
    checkpoint: CandidateArtifact
    event: CandidateArtifact
    state: dict[str, Any]
    training_complete: bool


def _checked_state(
    raw: bytes, body: dict[str, Any], dataset: Any, config: StructuredTrainingConfig
) -> dict[str, Any]:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_CHECKPOINT_BYTES:
        raise BoundaryError(STAGE, "candidate_checkpoint_limit")
    fields = {
        "schema",
        "identity",
        "parameter_names",
        "model",
        "optimizer",
        "optimizer_participation",
        "cursor",
        "boundary",
        "memory",
        "memory_run",
        "phase",
        "counters",
        "chunk_losses",
        "initial_train",
        "partitions",
        "rng",
        "device_rng",
    }
    from ..structured_workload_contracts import (
        StructuredBatchPolicy,
        batch_chunk_plans,
        batch_lane_cursors,
        batch_rounds,
    )
    batch = (StructuredBatchPolicy.from_dict(body["execution_identity"]["batch_policy"])
             if "batch_policy" in body["execution_identity"] else None)
    if batch is not None:
        fields.add("batch_lanes")
    state = dict(object_fields(decode_checkpoint(raw), fields, STAGE))
    if state["schema"] != CHECKPOINT_SCHEMA or state["identity"] != body["execution_identity"]:
        raise BoundaryError(STAGE, "candidate_numeric_identity_mismatch")
    model = cpu_model_from_checkpoint(raw, body["execution_identity"])
    parameters = tuple(model.parameters())
    if state["parameter_names"] != tuple(name for name, _ in model.named_parameters()):
        raise BoundaryError(STAGE, "candidate_parameter_names")
    plans = (_plans(dataset, config) if batch is None
             else batch_chunk_plans(dataset, config, batch))
    cursor = object_fields(state["cursor"], {"epoch", "chunk", "run_index", "next_step"}, STAGE)
    epoch, chunk = cursor["epoch"], cursor["chunk"]
    if (
        type(epoch) is not int
        or not 0 <= epoch <= config.epochs
        or type(chunk) is not int
        or not 0 <= chunk < len(plans)
        or epoch == config.epochs
        and chunk != 0
    ):
        raise BoundaryError(STAGE, "candidate_cursor_outside_plan")
    plan = None if epoch == config.epochs else plans[chunk]
    if (
        type(state["boundary"]) is not int
        or state["boundary"] != epoch * len(plans) + chunk
        or cursor["run_index"] != (None if plan is None or batch is not None else plan.run_index)
        or cursor["next_step"] != (None if plan is None or batch is not None else plan.start)
        or state["memory_run"] != (None if chunk == 0 or batch is not None
                                   else plans[chunk - 1].run_index)
    ):
        raise BoundaryError(STAGE, "candidate_cursor_plan_mismatch")
    done = list(plans) * epoch + list(plans[:chunk])
    counters = {
        "updates": sum(plan.labels > 0 for plan in done),
        "labels": sum(plan.labels for plan in done),
        "observations": sum(plan.end - plan.start for plan in done),
        "advances": sum(plan.advances for plan in done),
        "unlabelled_chunks": sum(plan.labels == 0 for plan in done),
    }
    if (
        state["counters"] != counters
        or any(type(value) is not int for value in state["counters"].values())
        or counters["updates"] > config.max_updates
    ):
        raise BoundaryError(STAGE, "candidate_counters_mismatch")
    complete = epoch == config.epochs or counters["updates"] == config.max_updates
    if state["phase"] != ("evaluation" if complete else "train") or state["partitions"] != {}:
        raise BoundaryError(STAGE, "candidate_not_numeric_only")
    memory = state["memory"]
    if (
        not isinstance(memory, Tensor)
        or memory.device.type != "cpu"
        or memory.dtype != torch.float32
        or memory.shape != ((model.slots, WIDTH) if batch is None
                           else (batch.lanes, model.slots, WIDTH))
        or not bool(torch.isfinite(memory).all())
        or chunk == 0
        and bool(memory.any())
    ):
        raise BoundaryError(STAGE, "candidate_memory_mismatch")
    if batch is not None:
        lanes = batch_lane_cursors(batch_rounds(dataset, config, batch), batch.lanes,
                                  epoch, chunk, config.epochs)
        if state["batch_lanes"] != lanes or any(
            (lane["next_step"] is None or lane["next_step"] == 0) and bool(memory[index].any())
            for index, lane in enumerate(lanes)
        ):
            raise BoundaryError(STAGE, "candidate_lane_state_mismatch")
    from ..models.structured_m2 import StructuredM2
    from ..models.structured_training import evaluate_runs
    from ..native_graph_spec import control_from_identity

    initial_model = StructuredM2(seed=config.seed, model_control=control_from_identity(
        body["execution_identity"]))
    expected_initial = evaluate_runs(initial_model, tuple(dataset.runs))
    initial = state["initial_train"]
    if (not isinstance(initial, dict) or set(initial) != set(expected_initial)
            or any((actual is None) != (expected is None)
                   or expected is not None and (type(actual) not in {int, float}
                       or not math.isfinite(actual)
                       or (actual != expected if type(expected) is int else
                           not math.isclose(actual, expected, rel_tol=2e-5, abs_tol=2e-5)))
                   for key, expected in expected_initial.items() for actual in [initial[key]])):
        raise BoundaryError(STAGE, "candidate_initial_train_mismatch")
    losses = state["chunk_losses"]
    if (
        not isinstance(losses, list)
        or len(losses) != counters["updates"]
        or any(
            type(value) not in {int, float} or not math.isfinite(value) or value < 0
            for value in losses
        )
    ):
        raise BoundaryError(STAGE, "candidate_loss_history_mismatch")
    participation = state["optimizer_participation"]
    if (
        not isinstance(participation, list)
        or len(participation) != len(parameters)
        or any(
            type(count) is not int or not 0 <= count <= counters["updates"]
            for count in participation
        )
    ):
        raise BoundaryError(STAGE, "candidate_optimizer_participation")
    optimizer = object_fields(state["optimizer"], {"state", "param_groups"}, STAGE)
    expected_groups = torch.optim.AdamW(model.parameters(), lr=config.learning_rate).state_dict()[
        "param_groups"
    ]
    if (
        optimizer["param_groups"] != expected_groups
        or not isinstance(optimizer["state"], dict)
        or set(optimizer["state"])
        != {index for index, count in enumerate(participation) if count > 0}
    ):
        raise BoundaryError(STAGE, "candidate_optimizer_inventory")
    if bool(optimizer["state"]) != bool(counters["updates"]):
        raise BoundaryError(STAGE, "candidate_optimizer_update_state_missing")
    for index, value in optimizer["state"].items():
        if type(index) is not int or not 0 <= index < len(parameters):
            raise BoundaryError(STAGE, "candidate_optimizer_index")
        node = object_fields(value, {"step", "exp_avg", "exp_avg_sq"}, STAGE)
        for key in ("exp_avg", "exp_avg_sq"):
            tensor = node[key]
            if (
                not isinstance(tensor, Tensor)
                or tensor.device.type != "cpu"
                or tensor.dtype != torch.float32
                or tensor.shape != parameters[index].shape
                or not bool(torch.isfinite(tensor).all())
                or key == "exp_avg_sq"
                and bool((tensor < 0).any())
            ):
                raise BoundaryError(STAGE, "candidate_optimizer_tensor")
        step = node["step"]
        if (
            not isinstance(step, Tensor)
            or step.device.type != "cpu"
            or step.shape != ()
            or step.dtype != torch.float32
            or float(step) != participation[index]
        ):
            raise BoundaryError(STAGE, "candidate_optimizer_step")
    rng = object_fields(state["rng"], {"torch_cpu", "python", "numpy"}, STAGE)
    cpu_rng = rng["torch_cpu"]
    if (
        not isinstance(cpu_rng, Tensor)
        or cpu_rng.device.type != "cpu"
        or cpu_rng.dtype != torch.uint8
    ):
        raise BoundaryError(STAGE, "candidate_cpu_rng")
    torch.Generator().set_state(cpu_rng)
    random.Random().setstate(rng["python"])
    numpy_rng = rng["numpy"]
    if (
        not isinstance(numpy_rng, tuple)
        or len(numpy_rng) != 5
        or not isinstance(numpy_rng[1], Tensor)
        or numpy_rng[1].device.type != "cpu"
        or numpy_rng[1].dtype != torch.uint32
    ):
        raise BoundaryError(STAGE, "candidate_numpy_rng")
    np.random.RandomState().set_state((numpy_rng[0], numpy_rng[1].numpy(), *numpy_rng[2:]))
    device_rng = state["device_rng"]
    cuda = StructuredDevicePolicy.from_dict(body["device_policy"]).device == "cuda:0"
    if (
        (device_rng is not None) != cuda
        or cuda
        and (
            not isinstance(device_rng, Tensor)
            or device_rng.device.type != "cpu"
            or device_rng.dtype != torch.uint8
            or device_rng.ndim != 1
            or device_rng.numel()
            != body["execution_identity"]["runtime"].get("cuda_rng_state_bytes")
        )
    ):
        raise BoundaryError(STAGE, "candidate_device_rng")
    return state


def check_structured_remote_candidate(
    attempt: PreparedTrainingAttempt,
    request: bytes,
    projection: bytes,
    candidate: CandidateResult,
    stop: StopInspection,
) -> ValidatedStructuredCandidate:
    """No writes: exact provider stop and every numeric/candidate binding precede adoption."""
    candidate.handle.app_ref.bind(attempt)
    stop.app_ref.bind(attempt)
    if not stop.confirmed or stop.app_ref != candidate.handle.app_ref:
        raise BoundaryError(STAGE, "exact_provider_stop_required")
    body, workload, dataset, config, _, parts = _inputs(attempt, request, projection)
    if candidate.runtime != attempt.runtime:
        raise BoundaryError(STAGE, "candidate_runtime_mismatch")
    artifacts = decode_candidate(candidate.candidate_bytes, attempt)
    by_kind = {item.manifest.kind: item for item in artifacts}
    if len(artifacts) != 2 or set(by_kind) != {"checkpoint", "run_event"}:
        raise BoundaryError(STAGE, "candidate_checkpoint_event_only")
    checkpoint, event = by_kind["checkpoint"], by_kind["run_event"]
    expected_parents = {"run": attempt.run_id, "training_input": attempt.training_input_id}
    if workload.resume_checkpoint_id is not None:
        expected_parents["resume_checkpoint"] = workload.resume_checkpoint_id
    if (
        {parent.role: parent.artifact_id for parent in checkpoint.manifest.parents}
        != expected_parents
        or len(checkpoint.payloads) != 1
        or len(checkpoint.manifest.payloads) != 1
        or checkpoint.manifest.payloads[0].role != "checkpoint"
        or checkpoint.manifest.payloads[0].media_type != "application/vnd.stpd.tensor-tree"
    ):
        raise BoundaryError(STAGE, "candidate_checkpoint_parent_payload_binding")
    state = _checked_state(checkpoint.payloads[0], body, dataset, config)
    prior = (
        _checked_state(parts["resume_checkpoint"], body, dataset, config)
        if "resume_checkpoint" in parts
        else None
    )
    before_boundaries = 0 if prior is None else prior["boundary"]
    before_updates = 0 if prior is None else prior["counters"]["updates"]
    delta = {
        "optimizer_updates": state["counters"]["updates"] - before_updates,
        "boundaries": state["boundary"] - before_boundaries,
    }
    if (
        candidate.counters.value() != delta
        or not 1 <= delta["boundaries"] <= attempt.limits.max_boundaries
        or not 0 <= delta["optimizer_updates"] <= attempt.limits.max_optimizer_updates
    ):
        raise BoundaryError(STAGE, "candidate_slice_counter_mismatch")
    info = checkpoint.manifest.parameters.value()
    expected = {
        "schema": CHECKPOINT_SCHEMA,
        "execution_identity_sha256": semantic_hash(body["execution_identity"]),
        "boundary": state["boundary"],
        "cursor": state["cursor"],
        "phase": state["phase"],
        "optimizer_updates": state["counters"]["updates"],
        "attempt": attempt.attempt_id,
        "attempt_producer": attempt.producer.to_dict(),
        "operation_id": attempt.operation_id,
        "prepared_id": attempt.identity,
        "execution_binding_id": attempt.execution_binding_id,
        "numerical_projection_sha256": body["numerical_projection_sha256"],
    }
    if info != expected:
        raise BoundaryError(STAGE, "candidate_checkpoint_metadata")
    if (
        event.manifest.parents != (Parent("run", attempt.run_id),)
        or event.payloads
        or event.manifest.parameters.value()
        != {
            "schema": "stpd/run-event-v1",
            "step": body["event_step"] + 1,
            "kind": "checkpoint",
            "attempt": attempt.attempt_id,
            "details": {
                "checkpoint_id": checkpoint.manifest.artifact_id,
                "optimizer_updates": state["counters"]["updates"],
                "attempt_producer": attempt.producer.to_dict(),
            },
        }
    ):
        raise BoundaryError(STAGE, "candidate_event_binding")
    complete = state["phase"] == "evaluation"
    if candidate.finish_reason != ("workload_complete" if complete else "slice_boundary"):
        raise BoundaryError(STAGE, "candidate_finish_phase_mismatch")
    return ValidatedStructuredCandidate(checkpoint, event, state, complete)


def publish_structured_remote_candidate(
    store: Any,
    reporter: Any,
    validated: ValidatedStructuredCandidate,
    attempt: PreparedTrainingAttempt,
    run: Manifest,
    dataset: Any,
    *,
    code_scope: str,
    authority: Any,
) -> dict[str, Any]:
    """Parent-only adoption and CPU export through the existing package owners.

    The caller has reconciled the original provider call, checked stopped0, and
    reverified its canonical source/use reservation. A worker cannot invoke this
    with a Store credential or journal in its frozen projection.
    """
    import io
    import tempfile
    from pathlib import Path

    from ..models.structured_training import evaluate_runs
    from ..structured_code_scope import (
        INFERENCE_SCOPE,
        LEGACY_SCOPE,
        TRAINING_SCOPE,
        exporter_runtime,
    )
    from ..structured_profiles import (
        NATIVE_SCOPES,
        ORDERED_SCOPE,
        common_sampled_source,
        qualification,
        trained_source,
    )

    def guard() -> None:
        authority.assert_current(attempt.run_id, attempt.operation_id, attempt.attempt_id)

    guard()
    info = run.parameters.value()
    if (
        run.artifact_id != attempt.run_id
        or run.parent("training_input") != attempt.training_input_id
        or info.get("operation_id") != attempt.operation_id
        or info.get("execution_identity") != validated.state["identity"]
        or info["execution_identity"].get("execution_binding_id") != attempt.execution_binding_id
        or sha256(numerical_projection(dataset))
        != info["execution_identity"].get("numerical_projection_sha256")
    ):
        raise BoundaryError(STAGE, "canonical_run_projection_binding")
    history = reporter.events(run.artifact_id)
    if validated.event.manifest.parameters.value()["step"] != len(history) + 1:
        raise BoundaryError(STAGE, "canonical_event_sequence_changed")
    for artifact in (validated.checkpoint, validated.event):
        guard()
        for payload, raw in zip(artifact.manifest.payloads, artifact.payloads, strict=True):
            actual = store.put_payload(payload.role, io.BytesIO(raw), payload.media_type)
            if actual != payload:
                raise BoundaryError(STAGE, "candidate_adoption_bytes_mismatch")
        guard()
        if artifact.manifest.kind == "run_event":
            history = reporter.events(run.artifact_id)
            if artifact.manifest.parameters.value()["step"] != len(history) + 1:
                raise BoundaryError(STAGE, "canonical_event_sequence_changed")
            reporter.emit(artifact.manifest)
        else:
            store.publish(artifact.manifest)
    checkpoint_id = validated.checkpoint.manifest.artifact_id
    updates = validated.state["counters"]["updates"]
    terminal: dict[str, Any] = {
        "checkpoint_id": checkpoint_id,
        "optimizer_updates": updates,
        "result_id": None,
        "model_id": None,
        "training_complete": validated.training_complete,
    }
    if not validated.training_complete:
        return terminal
    guard()
    model = cpu_model_from_checkpoint(validated.checkpoint.payloads[0], info["execution_identity"])
    config = StructuredTrainingConfig(**info["config"])
    metrics = {
        "optimizer_updates": updates,
        "label_uses": validated.state["counters"]["labels"],
        "chunk_losses": validated.state["chunk_losses"],
        "unlabelled_chunks": validated.state["counters"]["unlabelled_chunks"],
        "completed_epochs": validated.state["cursor"]["epoch"],
        "budget_reached": updates >= config.max_updates,
        "initial_train": validated.state["initial_train"],
        "partitions": {
            split: evaluate_runs(model, tuple(item for item in dataset.runs if item.split == split))
            for split in ("train", "dev", "test")
        },
        "evaluation_scope": "independent_run_test"
        if dataset.independent_test_eligible
        else "learning_smoke",
        "capsule_bytes_verified": dataset.capsules_verified,
        "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "claims": {"human": False, "optimal_teacher": False, "all_scene_quality": False},
        "training_runtime": info["execution_identity"]["runtime"],
        "fixed_weight_evaluation_device": "cpu",
        "cpu_gpu_bitwise_parity": False,
    }
    provenance = {
        "training_producer": run.producer.to_dict(),
        "export_producer": attempt.producer.to_dict(),
        "run_id": run.artifact_id,
        "training_input_id": attempt.training_input_id,
        "checkpoint_id": checkpoint_id,
    }
    native = code_scope in NATIVE_SCOPES
    training = store.get_manifest(attempt.training_input_id)
    policy = info.get("execution_policy")
    with tempfile.TemporaryDirectory(prefix="structured-remote-export-") as temporary:
        package = Path(temporary) / "agent"
        if native:
            from ..policy.native_structured_export import (
                export_native_trained_package,
                export_native_trained_sampled_package,
                export_ordered_native_trained_package,
                native_model_parameters,
            )

            exporter = (
                export_native_trained_sampled_package
                if common_sampled_source(dataset, code_scope)
                else export_ordered_native_trained_package
                if code_scope == ORDERED_SCOPE
                else export_native_trained_package
            )
            metadata = exporter(
                model,
                package,
                producer=attempt.producer,
                data_sha256=dataset.source_sha256,
                source=trained_source(
                    dataset, code_scope, training.parent("source"), training.artifact_id
                ),
                training={"config": asdict(config), "metrics": metrics},
                provenance={**provenance, "export_runtime": exporter_runtime()},
                **({"execution_policy": policy} if policy is not None else {}),
            )
            model_parameters = native_model_parameters(metadata, attempt.attempt_id)
        else:
            from ..policy.structured_export import export_structured_package

            metadata = export_structured_package(
                model,
                package,
                source_revision=run.producer.source_revision,
                data_sha256=dataset.source_sha256,
                source_kind=dataset.source_kind,
                teacher_sha256=semantic_hash(dataset.teacher.value()),
                training={"config": asdict(config), "metrics": metrics},
                code_scope=INFERENCE_SCOPE if code_scope == TRAINING_SCOPE else LEGACY_SCOPE,
                provenance=provenance if code_scope == TRAINING_SCOPE else None,
            )
            model_parameters = {
                "schema": "stpd/structured-m2-model-v3"
                if code_scope == TRAINING_SCOPE
                else "stpd/structured-m2-model-v2",
                "model_id": metadata["model_id"],
                "graph_id": info["execution_identity"]["graph_id"],
                "qualification": qualification(dataset),
                "attempt": attempt.attempt_id,
            }
        payloads = []
        for role, filename, media in (
            ("package_manifest", "model.json", "application/json"),
            ("weights", "weights.tensor-tree", "application/vnd.stpd.tensor-tree"),
        ):
            guard()
            payloads.append(
                store.put_payload(role, io.BytesIO((package / filename).read_bytes()), media)
            )
    guard()
    exported = Manifest(
        "model",
        attempt.producer,
        (
            Parent("run", run.artifact_id),
            Parent("checkpoint", checkpoint_id),
            Parent("training_input", training.artifact_id),
        ),
        tuple(payloads),
        FrozenObject.of(model_parameters),
    )
    store.publish(exported)
    from spireagent.json_boundary import json_bytes

    report = {
        "schema": "stpd/structured-remote-training-report-v1",
        "run_id": run.artifact_id,
        "training_input_id": training.artifact_id,
        "checkpoint_id": checkpoint_id,
        "model_artifact_id": exported.artifact_id,
        "operation_id": attempt.operation_id,
        "attempt": attempt.attempt_id,
        "producer": attempt.producer.to_dict(),
        "config": asdict(config),
        "metrics": metrics,
        "execution_identity": info["execution_identity"],
    }
    guard()
    payload = store.put_payload("report", io.BytesIO(json_bytes(report)), "application/json")
    result = Manifest(
        "run_result",
        attempt.producer,
        (
            Parent("run", run.artifact_id),
            Parent("training_input", training.artifact_id),
            Parent("checkpoint", checkpoint_id),
            Parent("model", exported.artifact_id),
        ),
        (payload,),
        FrozenObject.of(
            {
                "schema": "stpd/run-result-v1",
                "state": "completed",
                "partition": "train",
                "source_sha256": dataset.source_sha256,
                "qualification": qualification(dataset),
                "attempt": attempt.attempt_id,
            }
        ),
    )
    guard()
    result_id = reporter.complete(result)
    selected = reporter.completed(run.artifact_id)
    if selected is None or selected.artifact_id != result_id:
        raise BoundaryError(STAGE, "canonical_completion_race")
    return {
        **terminal,
        "result_id": result_id,
        "model_id": exported.artifact_id,
        "metrics": metrics,
    }


def verify_structured_remote_checkpoint(
    raw: bytes,
    request: bytes,
    training_projection: bytes,
) -> dict[str, Any]:
    """Parent numerical verification; historical writer/stop permission stays in application."""
    body = parse_structured_remote_request(request)
    if sha256(training_projection) != body["numerical_projection_sha256"]:
        raise BoundaryError(STAGE, "resume_projection_binding")
    dataset = parse_numerical_projection(training_projection)
    for run in dataset.runs:
        for step in run.steps:
            validate_structured_frame(step.frame)
    return _checked_state(
        raw, body, dataset, StructuredTrainingConfig(**body["execution_identity"]["config"])
    )
