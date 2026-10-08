"""Fenced structured workload execution over existing immutable artifacts/reporting.

No automatic latest checkpoint, writer replacement, data admission or job ledger.
The application owns exact attempt authority and terminal-process reconciliation.
"""

from __future__ import annotations

import io
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import torch

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    json_bytes,
)
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from ..fullrun.structured_sequences import (
    MAX_SOURCE_BYTES,
    StructuredDataset,
    parse_structured_dataset,
)
from ..models.structured_engine import (
    CHECKPOINT_SCHEMA,
    MAX_CHECKPOINT_BYTES,
    StructuredTrainingEngine,
    execution_identity,
)
from ..models.structured_training import StructuredTrainingConfig
from ..policy.structured_export import export_structured_package, load_structured_package
from .reporting import RunReporter
from .structured_control import (
    AttemptAuthority,
    ExecutionControl,
    StructuredExecutionResult,
    StructuredWorkloadRequest,
)
from .structured_run import prepare_structured_run

RUN_SCHEMA = "stpd/structured-m2-run-v2"
MODEL_SCHEMA = "stpd/structured-m2-model-v2"


def prepare_structured_workload(
    store: ArtifactStore,
    dataset: StructuredDataset,
    producer: Producer,
    config: StructuredTrainingConfig,
    *,
    operation_id: str,
    source_id: str | None = None,
) -> Manifest:
    """Freeze caller-authorized source/config/execution identity; numerical work is separate."""
    if parse_structured_dataset(dataset.source_bytes) != dataset:
        raise BoundaryError("structured_workload", "source_projection_mismatch")
    return prepare_structured_run(
        store, dataset, producer, config, source_id=source_id, operation_id=operation_id
    )


def _load(
    store: ArtifactStore,
    request: StructuredWorkloadRequest,
    producer: Producer,
) -> tuple[Manifest, Manifest, StructuredDataset, StructuredTrainingConfig]:
    request.validate()
    run = store.get_manifest(request.run_id)
    info = run.parameters.value()
    if (
        run.kind != "run"
        or run.producer != producer
        or info.get("schema") != RUN_SCHEMA
        or info.get("operation_id") != request.operation_id
        or run.parent("training_input") != request.training_input_id
    ):
        raise BoundaryError(
            "structured_workload", "exact_run_identity_mismatch_or_v1_not_resumable"
        )
    config = StructuredTrainingConfig(**info["config"])
    config.validate()
    torch.set_num_threads(config.cpu_threads)
    training = store.get_manifest(request.training_input_id)
    source = store.get_manifest(training.parent("source"))
    payload = training.payload("source")
    if (
        training.kind != "training_input"
        or training.producer != producer
        or source.payload("source") != payload
        or payload.size > MAX_SOURCE_BYTES
    ):
        raise BoundaryError("structured_workload", "training_source_binding_mismatch")
    raw = b"".join(store.read_payload(payload))
    dataset = parse_structured_dataset(raw)
    if (
        dataset.source_sha256 != info["source_sha256"]
        or training.parameters.value().get("source_sha256") != dataset.source_sha256
        or execution_identity(dataset, config) != info.get("execution_identity")
    ):
        raise BoundaryError("structured_workload", "source_config_code_or_runtime_changed")
    experiment = store.get_manifest(run.parent("experiment"))
    if (
        experiment.kind != "experiment"
        or experiment.producer != producer
        or experiment.parent("training_input") != training.artifact_id
        or experiment.parameters.value().get("config") != asdict(config)
    ):
        raise BoundaryError("structured_workload", "experiment_binding_mismatch")
    return run, training, dataset, config


def _checkpoint_bytes(
    store: ArtifactStore, checkpoint_id: str, run: Manifest, training: Manifest
) -> bytes:
    saved = store.get_manifest(checkpoint_id)
    info = saved.parameters.value()
    if (
        saved.kind != "checkpoint"
        or saved.producer != run.producer
        or saved.parent("run") != run.artifact_id
        or saved.parent("training_input") != training.artifact_id
        or info.get("schema") != CHECKPOINT_SCHEMA
        or info.get("execution_identity_sha256")
        != semantic_hash(run.parameters.value()["execution_identity"])
    ):
        raise BoundaryError("structured_workload", "checkpoint_run_identity_mismatch")
    payload = saved.payload("checkpoint")
    if payload.size > MAX_CHECKPOINT_BYTES:
        raise BoundaryError("structured_workload", "checkpoint_size_limit")
    return b"".join(store.read_payload(payload))


def _completed(
    store: ArtifactStore,
    result: Manifest,
    run: Manifest,
    training: Manifest,
    dataset: StructuredDataset,
    config: StructuredTrainingConfig,
) -> tuple[str, int]:
    info = result.parameters.value()
    if (
        result.kind != "run_result"
        or result.producer != run.producer
        or info.get("schema") != "stpd/run-result-v1"
        or info.get("state") != "completed"
        or result.parent("run") != run.artifact_id
        or result.parent("training_input") != training.artifact_id
    ):
        raise BoundaryError("structured_workload", "completed_result_identity_mismatch")
    engine = StructuredTrainingEngine(dataset, config)
    engine.restore(_checkpoint_bytes(store, result.parent("checkpoint"), run, training))
    if engine.phase != "publication":
        raise BoundaryError("structured_workload", "completed_checkpoint_phase")
    model = store.get_manifest(result.parent("model"))
    if (
        model.kind != "model"
        or model.producer != run.producer
        or model.parameters.value().get("schema") != MODEL_SCHEMA
        or model.parent("run") != run.artifact_id
        or model.parent("checkpoint") != result.parent("checkpoint")
    ):
        raise BoundaryError("structured_workload", "completed_model_identity_mismatch")
    with tempfile.TemporaryDirectory(prefix="structured-completion-") as folder:
        package = Path(folder)
        for role, name in (("package_manifest", "model.json"), ("weights", "weights.tensor-tree")):
            payload = model.payload(role)
            if payload.size > MAX_CHECKPOINT_BYTES:
                raise BoundaryError("structured_workload", "model_payload_size_limit")
            (package / name).write_bytes(b"".join(store.read_payload(payload)))
        metadata, restored = load_structured_package(package)
        if (
            metadata["source"]["data_sha256"] != dataset.source_sha256
            or metadata["training"] != {"config": asdict(config), "metrics": engine.metrics()}
            or any(
                not torch.equal(tensor, restored.state_dict()[name])
                for name, tensor in engine.model.state_dict().items()
            )
        ):
            raise BoundaryError("structured_workload", "completed_export_state_mismatch")
    return result.parent("model"), engine.updates


def execute_structured_workload(
    store: ArtifactStore,
    reporter: RunReporter,
    request: StructuredWorkloadRequest,
    producer: Producer,
    *,
    authority: AttemptAuthority,
    control: ExecutionControl | None = None,
) -> StructuredExecutionResult:
    """One explicit attempt; all publication follows the injected application fence."""
    started = time.perf_counter()
    run, training, dataset, config = _load(store, request, producer)

    def guard() -> None:
        authority.assert_current(request.run_id, request.operation_id, request.attempt_id)

    guard()
    history = reporter.events(run.artifact_id)
    if any(
        event.kind != "run_event"
        or event.producer != producer
        or event.parent("run") != run.artifact_id
        or event.parameters.value().get("schema") != "stpd/run-event-v1"
        for event in history
    ):
        raise BoundaryError("structured_workload", "event_identity_mismatch")
    raw_steps = [event.parameters.value().get("step") for event in history]
    steps = [step for step in raw_steps if isinstance(step, int) and not isinstance(step, bool)]
    if (
        len(steps) != len(raw_steps)
        or any(step < 1 for step in steps)
        or len(set(steps)) != len(steps)
    ):
        raise BoundaryError("structured_workload", "event_sequence_integrity")
    if sorted(steps) != list(range(1, len(steps) + 1)):
        raise BoundaryError("structured_workload", "event_sequence_gap")
    event_step = len(steps)
    previous = reporter.completed(run.artifact_id)
    if previous is not None:
        model_id, updates = _completed(store, previous, run, training, dataset, config)
        return StructuredExecutionResult(
            "completed",
            run.artifact_id,
            request.attempt_id,
            previous.parent("checkpoint"),
            previous.artifact_id,
            model_id,
            event_step,
            updates,
            time.perf_counter() - started,
        )
    if request.mode == "reconcile":
        raise BoundaryError("structured_workload", "no_completed_result_reconcile_required")
    if request.mode == "start" and history:
        raise BoundaryError("structured_workload", "explicit_resume_required")
    if request.mode == "resume":
        assert request.resume_checkpoint_id is not None
        authority.authorize_resume(
            run.artifact_id, request.attempt_id, request.resume_checkpoint_id
        )
        guard()
    checkpoint_id = request.resume_checkpoint_id
    engine = StructuredTrainingEngine(dataset, config)
    if checkpoint_id is not None:
        engine.restore(_checkpoint_bytes(store, checkpoint_id, run, training))

    reporting_failed = False
    completion_attempted = False

    def event(kind: str, **details: Any) -> None:
        nonlocal event_step, reporting_failed
        guard()
        candidate = Manifest(
            "run_event",
            producer,
            (Parent("run", run.artifact_id),),
            parameters=FrozenObject.of(
                {
                    "schema": "stpd/run-event-v1",
                    "step": event_step + 1,
                    "attempt": request.attempt_id,
                    "kind": kind,
                    "details": details,
                }
            ),
        )
        try:
            reporter.emit(candidate)
        except Exception:
            # Publication may already be durable. Do not create a duplicate/gapped step.
            reporting_failed = True
            raise
        event_step += 1

    def checkpoint() -> str:
        nonlocal checkpoint_id
        raw = engine.checkpoint()
        guard()
        payload = store.put_payload(
            "checkpoint", io.BytesIO(raw), "application/vnd.stpd.tensor-tree"
        )
        guard()
        parents: tuple[Parent, ...] = (
            Parent("run", run.artifact_id),
            Parent("training_input", training.artifact_id),
        )
        if checkpoint_id is not None:
            parents += (Parent("previous_checkpoint", checkpoint_id),)
        saved = Manifest(
            "checkpoint",
            producer,
            parents,
            (payload,),
            FrozenObject.of(
                {
                    "schema": CHECKPOINT_SCHEMA,
                    "execution_identity_sha256": semantic_hash(engine.identity),
                    "boundary": engine.boundary,
                    "cursor": asdict(engine.cursor),
                    "phase": engine.phase,
                    "optimizer_updates": engine.updates,
                    "attempt": request.attempt_id,
                }
            ),
        )
        store.publish(saved)
        checkpoint_id = saved.artifact_id
        event(
            "checkpoint",
            checkpoint_id=checkpoint_id,
            boundary=engine.boundary,
            cursor=asdict(engine.cursor),
            optimizer_updates=engine.updates,
            phase=engine.phase,
        )
        return checkpoint_id

    def stop_action() -> str:
        action = "continue" if control is None else control.requested_action()
        if action not in {"continue", "pause", "cancel"}:
            raise BoundaryError("structured_workload", "invalid_control_action")
        return action

    try:
        event(
            "resumed" if request.mode == "resume" else "started",
            checkpoint_id=checkpoint_id,
            phase=engine.phase,
            cursor=asdict(engine.cursor),
        )
        # Even a fresh zero-update state is recoverable after this durable boundary.
        if checkpoint_id is None:
            checkpoint()
        while not engine.training_complete:
            action = stop_action()
            if action != "continue":
                saved = checkpoint()
                state = "paused" if action == "pause" else "cancelled"
                event(
                    state,
                    checkpoint_id=saved,
                    optimizer_updates=engine.updates,
                    attempt_seconds=time.perf_counter() - started,
                )
                return StructuredExecutionResult(
                    state,
                    run.artifact_id,
                    request.attempt_id,
                    saved,
                    None,
                    None,
                    event_step,
                    engine.updates,
                    time.perf_counter() - started,
                )
            guard()
            progress = engine.advance_chunk()
            event("progress", **asdict(progress), durable_checkpoint_id=checkpoint_id)
            if (
                engine.boundary % request.checkpoint_every_boundaries == 0
                or engine.training_complete
            ):
                checkpoint()
        # A durable training-complete checkpoint precedes any dev/test evaluation.
        action = stop_action()
        if action != "continue":
            saved = checkpoint()
            state = "paused" if action == "pause" else "cancelled"
            event(
                state,
                checkpoint_id=saved,
                optimizer_updates=engine.updates,
                attempt_seconds=time.perf_counter() - started,
            )
            return StructuredExecutionResult(
                state,
                run.artifact_id,
                request.attempt_id,
                saved,
                None,
                None,
                event_step,
                engine.updates,
                time.perf_counter() - started,
            )
        if engine.phase == "evaluation":
            event(
                "evaluation",
                replay=request.mode == "resume",
                fixed_weights=True,
                optimizer_updates=engine.updates,
            )
            engine.evaluate()
            checkpoint()
        action = stop_action()
        if action != "continue":
            saved = checkpoint()
            state = "paused" if action == "pause" else "cancelled"
            event(
                state,
                checkpoint_id=saved,
                optimizer_updates=engine.updates,
                attempt_seconds=time.perf_counter() - started,
            )
            return StructuredExecutionResult(
                state,
                run.artifact_id,
                request.attempt_id,
                saved,
                None,
                None,
                event_step,
                engine.updates,
                time.perf_counter() - started,
            )
        assert checkpoint_id is not None
        with tempfile.TemporaryDirectory(prefix="structured-export-") as folder:
            package = Path(folder) / "agent"
            metadata = export_structured_package(
                engine.model.eval(),
                package,
                source_revision=producer.source_revision,
                data_sha256=dataset.source_sha256,
                source_kind=dataset.source_kind,
                teacher_sha256=semantic_hash(dataset.teacher.value()),
                training={"config": asdict(config), "metrics": engine.metrics()},
            )
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
        model = Manifest(
            "model",
            producer,
            (
                Parent("run", run.artifact_id),
                Parent("checkpoint", checkpoint_id),
                Parent("training_input", training.artifact_id),
            ),
            tuple(payloads),
            FrozenObject.of(
                {
                    "schema": MODEL_SCHEMA,
                    "model_id": metadata["model_id"],
                    "graph_id": engine.identity["graph_id"],
                    "qualification": "engineering_only",
                }
            ),
        )
        store.publish(model)
        report = {
            "schema": "stpd/structured-m2-training-report-v2",
            "producer": producer.to_dict(),
            "source_sha256": dataset.source_sha256,
            "config": asdict(config),
            "metrics": engine.metrics(),
            "execution_identity": engine.identity,
            "attempt": request.attempt_id,
            "attempt_seconds": time.perf_counter() - started,
        }
        guard()
        report_payload = store.put_payload(
            "report", io.BytesIO(json_bytes(report)), "application/json"
        )
        result = Manifest(
            "run_result",
            producer,
            (
                Parent("run", run.artifact_id),
                Parent("training_input", training.artifact_id),
                Parent("checkpoint", checkpoint_id),
                Parent("model", model.artifact_id),
            ),
            (report_payload,),
            FrozenObject.of(
                {
                    "schema": "stpd/run-result-v1",
                    "state": "completed",
                    "partition": "train",
                    "source_sha256": dataset.source_sha256,
                    "qualification": "engineering_only",
                }
            ),
        )
        guard()
        completion_attempted = True
        result_id = reporter.complete(result)
        selected = reporter.completed(run.artifact_id)
        if selected is None or selected.artifact_id != result_id:
            raise BoundaryError("structured_workload", "completion_race")
        event(
            "completed",
            result_id=result_id,
            model_id=model.artifact_id,
            checkpoint_id=checkpoint_id,
            optimizer_updates=engine.updates,
            attempt_seconds=time.perf_counter() - started,
        )
        return StructuredExecutionResult(
            "completed",
            run.artifact_id,
            request.attempt_id,
            checkpoint_id,
            result_id,
            model.artifact_id,
            event_step,
            engine.updates,
            time.perf_counter() - started,
        )
    except Exception as error:
        # A revoked writer cannot publish even a failure annotation.
        guard()
        if reporting_failed or completion_attempted:
            # The application must reconcile uncertain publication before another attempt.
            raise
        event(
            "failed",
            error_type=type(error).__name__,
            checkpoint_id=checkpoint_id,
            attempt_seconds=time.perf_counter() - started,
        )
        raise
