"""Dispatch to existing feature/training owners without granting final Hub authority."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, Protocol

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, unsigned
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ArtifactStore, ManifestArtifactStore

from ..contracts import QwenBackend
from ..fullrun.evaluation import load_evaluation
from ..fullrun.features import compile_features, load_features
from ..workers.checkpoint_codec import MAX_BYTES, decode_checkpoint
from ..workers.contracts import (
    RUN_SCHEMA,
    TrainingConfig,
    load_training_input,
    prepare_run,
    prepare_training_input,
)
from ..workers.ranking import TrainingPlan, make_training_plan
from ..workers.worker import CHECKPOINT_SCHEMA, MODEL_SCHEMA, execute
from .contracts import ComputeReceipt, ComputeRequest, load_feature_job

SAVE_MARGIN_SECONDS = 300


@dataclass
class _PauseBeforeTimeout:
    seconds: int
    on_start: Callable[[], None] | None = None
    pause_at: float | None = None

    def requested_action(self) -> Literal["continue", "pause"]:
        if self.pause_at is None:
            if self.on_start is not None:
                self.on_start()
            self.pause_at = time.monotonic() + self.seconds - SAVE_MARGIN_SECONDS
        return "pause" if time.monotonic() >= self.pause_at else "continue"


def _wall_pause(wall_seconds: int | None,
                on_start: Callable[[], None] | None = None) -> _PauseBeforeTimeout | None:
    if wall_seconds is None:
        return None
    if wall_seconds <= SAVE_MARGIN_SECONDS:
        raise BoundaryError("compute", "wall_seconds_must_exceed_save_margin")
    control = _PauseBeforeTimeout(wall_seconds, on_start)
    if on_start is None:
        control.pause_at = time.monotonic() + wall_seconds - SAVE_MARGIN_SECONDS
    return control


class FeatureBackend(QwenBackend, Protocol):
    hidden_size: int


class CandidateReporter:
    """Persist candidates/events, never select a shared completion or grant a lease.

    A stale or preempted attempt may leave immutable diagnostic artifacts. Only the
    Hub's transactional attempt/fence check may accept a returned receipt.
    """

    def __init__(
        self, store: ManifestArtifactStore, run_id: str, runtime: Producer,
        *, request: ComputeRequest | None = None,
    ) -> None:
        self.store, self.run_id, self.runtime = store, run_id, runtime
        self._events = ObjectStoreRunReporter(store, store.blobs, read_concurrency=8)
        if request is not None and (
            request.kind != "training" or request.input_id != run_id or request.producer != runtime
        ):
            raise BoundaryError("candidate_reporter", "request_binding_mismatch")
        self.request = request

    def _bound(self, manifest: Manifest, kind: str) -> None:
        if (
            manifest.kind != kind or manifest.parent("run") != self.run_id
            or manifest.producer != self.runtime
        ):
            raise BoundaryError("candidate_reporter", "foreign_run_or_source")

    def emit(self, event: Manifest) -> str:
        self._bound(event, "run_event")
        if event.parameters.value().get("schema") != "stpd/run-event-v1":
            raise BoundaryError("candidate_reporter", "unsupported_event")
        if self.request is not None:
            parameters = event.parameters.value()
            details = dict(parameters.get("details", {}))
            details.update({
                "compute_request_id": self.request.request_id,
                "compute_attempt_id": self.request.attempt_id,
            })
            parameters["details"] = details
            event = replace(event, parameters=FrozenObject.of(parameters))
        return self._events.emit(event)

    def completed(self, run_id: str) -> Manifest | None:
        if run_id != self.run_id:
            raise BoundaryError("candidate_reporter", "foreign_run")
        return None

    def complete(self, result: Manifest) -> str:
        self._bound(result, "run_result")
        if result.parameters.value().get("schema") != "stpd/run-result-v1":
            raise BoundaryError("candidate_reporter", "unsupported_result")
        return self.store.publish(result)

    def events(self, run_id: str) -> tuple[Manifest, ...]:
        if run_id != self.run_id:
            raise BoundaryError("candidate_reporter", "foreign_run")
        return tuple(event for event in self._events.events(run_id)
                     if event.producer == self.runtime)


def execute_feature_job(
    store: ArtifactStore, job_id: str, runtime: Producer, backend: FeatureBackend
) -> Manifest:
    job, spec = load_feature_job(store, job_id, runtime)
    if asdict(backend.identity) != spec.qwen.value() or backend.hidden_size != spec.hidden_size:
        raise BoundaryError("feature_job", "backend_identity_or_dimension_mismatch")
    return compile_features(
        store, job.parent("model_view"), backend, runtime, batch_size=spec.batch_size
    )


def validate_feature_output(
    store: ArtifactStore, job_id: str, feature_id: str, runtime: Producer
) -> Manifest:
    job, spec = load_feature_job(store, job_id, runtime)
    features = load_features(store, feature_id)
    if (
        features.manifest.producer != runtime
        or features.view.artifact_id != job.parent("model_view")
        or features.manifest.parameters.value().get("qwen") != spec.qwen.value()
        or features.matrix.shape[1] != spec.hidden_size
    ):
        raise BoundaryError("feature_job", "feature_output_mismatch")
    return features.manifest


@dataclass(frozen=True)
class PreparedFeatureRun:
    feature_job_id: str
    feature_set_id: str
    training_input_id: str
    experiment_id: str
    run_id: str


def prepare_feature_run(
    store: ArtifactStore, job_id: str, feature_id: str, runtime: Producer,
    *, replicate: str = "primary",
) -> PreparedFeatureRun:
    _, spec = load_feature_job(store, job_id, runtime)
    validate_feature_output(store, job_id, feature_id, runtime)
    training = prepare_training_input(
        store, feature_id, runtime, spec.training, protocol_id=spec.protocol_id
    )
    experiment, run = prepare_run(store, training.artifact_id, runtime, replicate=replicate)
    return PreparedFeatureRun(
        job_id, feature_id, training.artifact_id, experiment.artifact_id, run.artifact_id
    )


def dispatch(
    store: ManifestArtifactStore, request: ComputeRequest, runtime: Producer,
    *, backend: FeatureBackend | None = None, wall_seconds: int | None = None,
    training_ready: Callable[[], None] | None = None,
    input_cache: Path | None = None,
) -> ComputeReceipt:
    if request.producer != runtime:
        raise BoundaryError("compute", "runtime_source_lock_mismatch")
    if (wall_seconds is not None
            and not 1 <= unsigned(wall_seconds, "compute.wall_seconds") <= 86400):
        raise BoundaryError("compute", "wall_seconds_out_of_range")
    if request.kind == "features":
        if backend is None:
            raise BoundaryError("compute", "feature_backend_required")
        features = execute_feature_job(store, request.input_id, runtime, backend)
        return ComputeReceipt(
            request.request_id, request.attempt_id, request.kind, request.input_id,
            runtime, "candidate_prepared", features.artifact_id,
        )
    from ..p1.baseline_spec import RUN_SCHEMA as P1_BASELINE_RUN_SCHEMA
    from ..p1.spec import RUN_SCHEMAS as P1_RUN_SCHEMAS

    if store.get_manifest(request.input_id).parameters.value().get("schema") == (
            P1_BASELINE_RUN_SCHEMA):
        if backend is not None:
            raise BoundaryError("compute", "P1_training_does_not_accept_feature_backend")
        from ..p1.baseline_worker import execute_baseline_run

        baseline_result = execute_baseline_run(
            store, CandidateReporter(store, request.input_id, runtime, request=request),
            request.input_id, runtime, attempt_id=request.attempt_id,
            resume=request.resume, stop_after=request.stop_after,
            mode="resume" if request.resume is not None else "start",
            control=_wall_pause(wall_seconds, training_ready),
        )
        return ComputeReceipt(
            request.request_id, request.attempt_id, request.kind, request.input_id, runtime,
            "candidate_prepared" if baseline_result.state == "completed" else "paused",
            baseline_result.result_id, baseline_result.checkpoint_id,
        )

    if store.get_manifest(request.input_id).parameters.value().get("schema") in P1_RUN_SCHEMAS:
        if backend is not None:
            raise BoundaryError("compute", "P1_training_does_not_accept_feature_backend")
        from ..workers.p1_run import execute_p1_run

        # One P1 request shares immutable parent bytes between verification and
        # the synchronous reporter. Fresh observers/auditors keep default reads.
        store = ManifestArtifactStore(store.blobs, cache_manifests=True)
        p1_result = execute_p1_run(
            store, CandidateReporter(store, request.input_id, runtime, request=request),
            request.input_id, runtime, attempt_id=request.attempt_id,
            resume=request.resume, stop_after=request.stop_after,
            mode="resume" if request.resume is not None else "start",
            control=_wall_pause(wall_seconds, training_ready), input_cache=input_cache,
        )
        return ComputeReceipt(
            request.request_id, request.attempt_id, request.kind, request.input_id, runtime,
            "candidate_prepared" if p1_result.state == "completed" else "paused",
            p1_result.result_id, p1_result.checkpoint_id,
        )
    if backend is not None:
        raise BoundaryError("compute", "training_must_use_frozen_features")
    result = execute(
        store, CandidateReporter(store, request.input_id, runtime, request=request),
        request.input_id, runtime,
        resume=request.resume, stop_after=request.stop_after,
    )
    return ComputeReceipt(
        request.request_id, request.attempt_id, request.kind, request.input_id, runtime,
        "candidate_prepared" if result.state == "completed" else "paused",
        result.result_id, result.checkpoint_id,
    )


def preflight_training_request(store: ArtifactStore, request: ComputeRequest) -> None:
    """Exact installed training families; never relabel P1 as a FeatureJob."""
    if request.kind != "training":
        raise BoundaryError("compute", "training_request_required")
    run = store.get_manifest(request.input_id)
    if run.kind != "run" or run.producer != request.producer:
        raise BoundaryError("compute", "run_source_or_kind_mismatch")
    from ..p1.baseline_spec import RUN_SCHEMA as P1_BASELINE_RUN_SCHEMA
    from ..p1.spec import RUN_SCHEMAS as P1_RUN_SCHEMAS

    if run.parameters.value().get("schema") == P1_BASELINE_RUN_SCHEMA:
        from ..p1.baseline_worker import verify_baseline_checkpoint
        from ..p1.baseline_workload import verify_baseline_run

        baseline_workload = verify_baseline_run(store, request.input_id, request.producer)
        if request.resume is not None:
            verify_baseline_checkpoint(store, request.resume, baseline_workload)
        return

    if run.parameters.value().get("schema") in P1_RUN_SCHEMAS:
        from ..p1.workload import verify_p1_run
        from ..workers.p1_run import verify_p1_checkpoint

        workload = verify_p1_run(store, request.input_id, request.producer)
        if request.resume is not None:
            verify_p1_checkpoint(store, request.resume, workload)
        return
    if run.parameters.value().get("schema") != RUN_SCHEMA:
        raise BoundaryError("compute", "unsupported_training_run_schema")
    load_training_input(store, run.parent("training_input"), request.producer)


def _checkpoint(
    store: ArtifactStore, checkpoint_id: str, run_id: str, training_id: str, runtime: Producer,
    config: TrainingConfig, plan: TrainingPlan,
) -> Manifest:
    checkpoint = store.get_manifest(checkpoint_id)
    if (
        checkpoint.kind != "checkpoint" or checkpoint.producer != runtime
        or checkpoint.parameters.value().get("schema") != CHECKPOINT_SCHEMA
        or sorted(p.role for p in checkpoint.parents) != ["run", "training_input"]
        or checkpoint.parent("run") != run_id
        or checkpoint.parent("training_input") != training_id
        or {p.role for p in checkpoint.payloads} != {"checkpoint"}
    ):
        raise BoundaryError("compute_receipt", "checkpoint_binding_mismatch")
    if checkpoint.payload("checkpoint").size > MAX_BYTES:
        raise BoundaryError("compute_receipt", "checkpoint_size_limit")
    state = decode_checkpoint(b"".join(store.read_payload(checkpoint.payload("checkpoint"))))
    info = checkpoint.parameters.value()
    step = unsigned(info.get("step"), "compute_receipt.checkpoint_step")
    total = unsigned(info.get("total_steps"), "compute_receipt.checkpoint_total")
    if (
        total != plan.total_steps or step > total
        or info.get("data_identity") != plan.data_identity
        or state.get("schema") != CHECKPOINT_SCHEMA
        or state.get("config") != config.to_dict() or state.get("step") != step
        or state.get("total_steps") != total
        or state.get("data_identity") != info.get("data_identity")
        or not isinstance(state.get("head"), dict)
    ):
        raise BoundaryError("compute_receipt", "checkpoint_content_mismatch")
    return checkpoint


def validate_receipt(
    store: ArtifactStore, request: ComputeRequest, receipt: ComputeReceipt
) -> None:
    """CPU-side binding/integrity admission; not a replay or a scientific verdict.

    Validate before the Hub's atomic fenced selection. Worker training admission and
    checkpoint resume continue to perform their stronger device/optimizer validation.
    """
    receipt.bind(request)
    runtime = request.producer
    if request.kind == "features":
        if receipt.output_id is None:
            raise BoundaryError("compute_receipt", "missing_feature_output")
        validate_feature_output(store, request.input_id, receipt.output_id, runtime)
        return
    run = store.get_manifest(request.input_id)
    from ..p1.baseline_spec import RUN_SCHEMA as P1_BASELINE_RUN_SCHEMA
    from ..p1.spec import RUN_SCHEMAS as P1_RUN_SCHEMAS

    if run.parameters.value().get("schema") == P1_BASELINE_RUN_SCHEMA:
        from ..p1.baseline_worker import verify_baseline_checkpoint, verify_baseline_result
        from ..p1.baseline_workload import verify_baseline_run

        baseline_workload = verify_baseline_run(store, request.input_id, runtime)
        if receipt.checkpoint_id is None:
            raise BoundaryError("compute_receipt", "P1_checkpoint_required")
        verify_baseline_checkpoint(store, receipt.checkpoint_id, baseline_workload)
        if receipt.state == "paused":
            return
        if receipt.output_id is None:
            raise BoundaryError("compute_receipt", "missing_P1_result")
        _, _, baseline_checkpoint = verify_baseline_result(
            store, receipt.output_id, baseline_workload)
        if baseline_checkpoint.manifest.artifact_id != receipt.checkpoint_id:
            raise BoundaryError("compute_receipt", "P1_result_checkpoint_mismatch")
        return

    if run.parameters.value().get("schema") in P1_RUN_SCHEMAS:
        from ..p1.workload import verify_p1_run
        from ..workers.p1_run import verify_p1_checkpoint, verify_p1_result

        workload = verify_p1_run(store, request.input_id, runtime)
        if receipt.checkpoint_id is None:
            raise BoundaryError("compute_receipt", "P1_checkpoint_required")
        verify_p1_checkpoint(store, receipt.checkpoint_id, workload)
        if receipt.state == "paused":
            return
        if receipt.output_id is None:
            raise BoundaryError("compute_receipt", "missing_P1_result")
        _, _, checkpoint = verify_p1_result(store, receipt.output_id, workload)
        if checkpoint.manifest.artifact_id != receipt.checkpoint_id:
            raise BoundaryError("compute_receipt", "P1_result_checkpoint_mismatch")
        return
    if (
        run.kind != "run" or run.producer != runtime
        or run.parameters.value().get("schema") != RUN_SCHEMA
        or sorted(p.role for p in run.parents) != ["experiment", "training_input"]
    ):
        raise BoundaryError("compute_receipt", "run_identity_mismatch")
    training, config, features = load_training_input(
        store, run.parent("training_input"), runtime
    )
    plan = make_training_plan(features, config)
    experiment = store.get_manifest(run.parent("experiment"))
    if (
        experiment.kind != "experiment" or experiment.producer != runtime
        or experiment.parent("training_input") != training.artifact_id
    ):
        raise BoundaryError("compute_receipt", "experiment_input_mismatch")
    if receipt.checkpoint_id is not None:
        _checkpoint(
            store, receipt.checkpoint_id, run.artifact_id, training.artifact_id,
            runtime, config, plan,
        )
    if receipt.state == "paused":
        return
    if receipt.output_id is None:
        raise BoundaryError("compute_receipt", "missing_result")
    result = store.get_manifest(receipt.output_id)
    roles = [
        "baseline_action_only", "baseline_uniform_legal", "model", "offline_evaluation",
        "run", "training_input",
    ]
    if (
        result.kind != "run_result" or result.producer != runtime
        or result.parameters.value().get("schema") != "stpd/run-result-v1"
        or result.parameters.value().get("state") != "completed"
        or sorted(p.role for p in result.parents) != roles
        or result.parent("run") != request.input_id
        or result.parent("training_input") != training.artifact_id
    ):
        raise BoundaryError("compute_receipt", "result_binding_mismatch")
    model = store.get_manifest(result.parent("model"))
    info = model.parameters.value()
    if (
        model.kind != "model" or model.producer != runtime or info.get("schema") != MODEL_SCHEMA
        or sorted(p.role for p in model.parents)
        != ["checkpoint", "model_view", "run", "training_input"]
        or model.parent("run") != run.artifact_id
        or model.parent("training_input") != training.artifact_id
        or model.parent("model_view") != features.view.artifact_id
        or info.get("qwen") != training.parameters.value()["qwen"]
        or info.get("serializer") != training.parameters.value()["serializer"]
        or info.get("scope") != training.parameters.value()["scope"]
        or info.get("head") != config.head or info.get("dtype") != config.dtype
        or info.get("hidden_size") != features.matrix.shape[1]
        or {p.role for p in model.payloads} != {"weights"}
    ):
        raise BoundaryError("compute_receipt", "model_binding_mismatch")
    saved = _checkpoint(
        store, model.parent("checkpoint"), run.artifact_id, training.artifact_id,
        runtime, config, plan,
    )
    details = saved.parameters.value()
    if (
        details.get("step") != details.get("total_steps")
        or details.get("step") != info.get("steps")
        or details.get("step") != result.parameters.value().get("steps")
        or (receipt.checkpoint_id is not None and receipt.checkpoint_id != saved.artifact_id)
    ):
        raise BoundaryError("compute_receipt", "completion_step_mismatch")
    if model.payload("weights").size > MAX_BYTES:
        raise BoundaryError("compute_receipt", "model_size_limit")
    from ..workers.ranking import load_head

    # Loading and exact tensor comparison on CPU does not replay GPU training or
    # change the frozen training config stored in the checkpoint.
    loaded = load_head(
        b"".join(store.read_payload(model.payload("weights"))),
        features.matrix.shape[1], replace(config, device="cpu"),
    )
    saved_state = decode_checkpoint(b"".join(store.read_payload(saved.payload("checkpoint"))))
    import torch

    model_state = loaded.state_dict()
    if set(model_state) != set(saved_state["head"]) or any(
        not isinstance(saved_state["head"][key], torch.Tensor)
        or not torch.equal(value, saved_state["head"][key])
        for key, value in model_state.items()
    ):
        raise BoundaryError("compute_receipt", "model_checkpoint_weights_mismatch")
    for role, baseline in (
        ("offline_evaluation", "model"), ("baseline_uniform_legal", "uniform_legal"),
        ("baseline_action_only", "action_only"),
    ):
        evaluation = load_evaluation(store, result.parent(role))
        if (
            evaluation.manifest.producer != runtime
            or evaluation.manifest.parent("model") != model.artifact_id
            or evaluation.manifest.parent("model_view") != features.view.artifact_id
            or evaluation.manifest.parameters.value().get("partition") != "dev"
            or evaluation.manifest.parameters.value().get("baseline") != baseline
        ):
            raise BoundaryError("compute_receipt", "evaluation_binding_mismatch")
