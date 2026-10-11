"""Fenced structured workload execution over existing immutable artifacts/reporting.

No automatic latest checkpoint, writer replacement, data admission or job ledger.
The application owns exact attempt authority and terminal-process reconciliation.
"""

from __future__ import annotations

import hashlib
import io
import math
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import torch

from spireagent.artifact_contracts import Manifest, Parent, Payload, Producer
from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    decode_json,
    digest,
    json_bytes,
    object_fields,
)
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from ..fullrun.structured_sequences import (
    MAX_SOURCE_BYTES,
    StructuredDataset,
)
from ..models.structured_engine import (
    MAX_CHECKPOINT_BYTES,
    StructuredTrainingEngine,
    execution_identity,
)
from ..models.structured_training import StructuredTrainingConfig
from ..native_graph_spec import NativeGraphControl, control_from_identity
from ..structured_code_scope import (
    INFERENCE_SCOPE,
    LEGACY_SCOPE,
    MAX_MANIFEST_BYTES,
    MAX_WEIGHTS_BYTES,
    SCOPED_MODEL_SCHEMA,
    SCOPED_REPORT_SCHEMA,
    TRAINING_SCOPE,
    checkpoint_schema,
    exporter_runtime,
    run_code_scope,
)
from ..structured_profiles import (
    NATIVE_SCOPES,
    ORDERED_SCOPE,
    common_sampled_source,
    native_experiment,
    native_input_schema,
    native_model_schema,
    native_report_schema,
    native_source_schema,
    parse_dataset,
    qualification,
    source_verification,
    trained_source,
    verify_training_partition,
)
from ..structured_workload_contracts import MAX_RESUME_ANCESTRY
from .checkpoint_codec import decode_checkpoint
from .reporting import RunReporter
from .structured_control import (
    AttemptAuthority,
    ExecutionControl,
    StructuredExecutionResult,
    StructuredWorkloadRequest,
)
from .structured_run import checked_training_execution_policy, prepare_structured_run

RUN_SCHEMA = "stpd/structured-m2-run-v2"
MODEL_SCHEMA = "stpd/structured-m2-model-v2"


def _package_execution_policy(
    metadata: dict[str, Any], model_control: NativeGraphControl | None,
) -> dict[str, Any] | None:
    agent_spec = metadata["agent_spec"]
    if not isinstance(agent_spec, dict):
        raise BoundaryError("structured_workload", "package_execution_policy_mismatch")
    if "execution_policy" not in agent_spec:
        return None
    from ..native_sampled_carry_spec import sampled_agent_execution_policy

    if model_control is None:
        raise BoundaryError("structured_workload", "package_execution_policy_mismatch")
    return sampled_agent_execution_policy(agent_spec, model_control)


def prepare_structured_workload(
    store: ArtifactStore,
    dataset: StructuredDataset,
    producer: Producer,
    config: StructuredTrainingConfig,
    *,
    operation_id: str,
    source_id: str | None = None,
    code_scope: str = LEGACY_SCOPE,
    model_control: NativeGraphControl | None = None,
    execution_policy: dict[str, Any] | None = None,
) -> Manifest:
    """Freeze caller-authorized source/config/execution identity; numerical work is separate."""
    if parse_dataset(dataset.source_bytes, code_scope) != dataset:
        raise BoundaryError("structured_workload", "source_projection_mismatch")
    return prepare_structured_run(
        store, dataset, producer, config, source_id=source_id, operation_id=operation_id,
        code_scope=code_scope, model_control=model_control, execution_policy=execution_policy
    )


def _load(
    store: ArtifactStore,
    request: StructuredWorkloadRequest,
    producer: Producer,
) -> tuple[Manifest, Manifest, StructuredDataset, StructuredTrainingConfig]:
    request.validate()
    run = store.get_manifest(request.run_id)
    scope = run_code_scope(run.parameters.value().get("schema"))
    _roles(run, {"experiment", "training_input"}, set())
    info = object_fields(
        run.parameters.value(),
        {
            "schema",
            "config",
            "source_sha256",
            "torch_version",
            "partition",
            "operation_id",
            "execution_identity",
        } | ({"execution_policy"} if "execution_policy" in run.parameters.value() else set()),
        "structured_workload_run",
    )
    if (
        run.kind != "run"
        or run.producer != producer
        or run_code_scope(info.get("schema")) != scope
        or info.get("operation_id") != request.operation_id
        or info.get("partition") != "train"
        or run.parent("training_input") != request.training_input_id
    ):
        raise BoundaryError(
            "structured_workload", "exact_run_identity_mismatch_or_v1_not_resumable"
        )
    config = StructuredTrainingConfig(**info["config"])
    config.validate()
    if "execution_policy" in info:
        from ..policy.native_operational_outcome import checked_execution_policy

        checked_execution_policy(info["execution_policy"])
    training = store.get_manifest(request.training_input_id)
    _roles(training, {"source"}, {"source"})
    source = store.get_manifest(training.parent("source"))
    payload = training.payload("source")
    if (
        source.kind != "dataset"
        or training.kind != "training_input"
        or training.producer != producer
        or source.payload("source") != payload
        or payload.media_type != "application/json"
    ):
        raise BoundaryError("structured_workload", "training_source_binding_mismatch")
    raw = _read_payload(store, payload, MAX_SOURCE_BYTES)
    dataset = parse_dataset(raw, scope)
    if scope == ORDERED_SCOPE:
        from ..fullrun.ordered_source import verify_ordered_source_partition

        verified = verify_ordered_source_partition(store, source.artifact_id)
        if verified.dataset != dataset or verified.split != "train":
            raise BoundaryError("structured_workload", "source3_partition_dataset_binding")
    elif common_sampled_source(dataset, scope):
        verify_training_partition(store, source.artifact_id, dataset, scope)
    if "execution_policy" in info:
        checked_training_execution_policy(
            info["execution_policy"], dataset, scope,
            control_from_identity(info["execution_identity"]))
    torch.set_num_threads(config.cpu_threads)
    native = scope in NATIVE_SCOPES
    if (
        dataset.source_sha256 != info["source_sha256"]
        or training.parameters.value().get("source_sha256") != dataset.source_sha256
        or source.parameters.value().get("source_sha256", dataset.source_sha256)
        != dataset.source_sha256
        or source.parameters.value().get("source_kind", dataset.source_kind) != dataset.source_kind
        or execution_identity(
            dataset,
            config,
            code_scope=scope,
            model_control=control_from_identity(info["execution_identity"]),
        )
        != info.get("execution_identity")
    ):
        raise BoundaryError("structured_workload", "source_config_code_or_runtime_changed")
    expected_training_info = {
        "schema": native_input_schema(scope) if native else "stpd/structured-m2-training-input-v1",
        "source_sha256": dataset.source_sha256,
        "projection": info["execution_identity"]["projection"],
        "splits": {
            split: [item.run_id for item in dataset.runs if item.split == split]
            for split in ("train", "dev", "test")
        },
        "qualification": qualification(dataset),
        **(
            {
                "input_spec": dataset.input_spec.value(),
                "verification_identity": source_verification(dataset),
                "code_identity": info["execution_identity"]["code_identity"],
            }
            if native and dataset.input_spec
            else {}
        ),
    }
    if json_bytes(training.parameters.value()) != json_bytes(expected_training_info) or info[
        "torch_version"
    ] != str(torch.__version__):
        raise BoundaryError("structured_workload", "training_input_metadata_mismatch")
    if (
        native
        and scope != ORDERED_SCOPE and not common_sampled_source(dataset, scope)
        and source.parameters.value()
        != {
            "schema": native_source_schema(scope, dataset),
            "source_kind": dataset.source_kind,
            "source_sha256": dataset.source_sha256,
            "qualification": qualification(dataset),
            "input_spec": expected_training_info["input_spec"],
            "verification_identity": source_verification(dataset),
            "code_identity": info["execution_identity"]["code_identity"],
        }
    ):
        raise BoundaryError("structured_workload", "native_source_manifest_binding")
    experiment = store.get_manifest(run.parent("experiment"))
    _roles(experiment, {"training_input"}, set())
    if (
        experiment.kind != "experiment"
        or experiment.producer != producer
        or experiment.parent("training_input") != training.artifact_id
        or json_bytes(experiment.parameters.value())
        != json_bytes(
            {
                **(
                    native_experiment(scope)
                    if native
                    else {"schema": "stpd/experiment-v1", "purpose": "s0_agent_teacher_imitation"}
                ),
                "graph_id": info["execution_identity"]["graph_id"],
                "config": asdict(config),
            }
        )
    ):
        raise BoundaryError("structured_workload", "experiment_binding_mismatch")
    return run, training, dataset, config


MAX_REPORT_BYTES = 8 * 1024 * 1024


def _read_payload(store: ArtifactStore, payload: Payload, maximum: int) -> bytes:
    """Verify a bounded immutable payload even when the injected store is only a port."""
    if not 0 < payload.size <= maximum:
        raise BoundaryError("structured_workload", "payload_size_limit")
    chunks = []
    size = 0
    checksum = hashlib.sha256()
    for chunk in store.read_payload(payload):
        size += len(chunk)
        if size > payload.size or size > maximum:
            raise BoundaryError("structured_workload", "payload_integrity_mismatch")
        checksum.update(chunk)
        chunks.append(chunk)
    if size != payload.size or checksum.hexdigest() != payload.sha256:
        raise BoundaryError("structured_workload", "payload_integrity_mismatch")
    return b"".join(chunks)


def _roles(manifest: Manifest, parents: set[str], payloads: set[str]) -> None:
    if {parent.role for parent in manifest.parents} != parents or {
        payload.role for payload in manifest.payloads
    } != payloads:
        raise BoundaryError("structured_workload", "artifact_role_inventory_mismatch")


def _checkpoint_manifest(
    store: ArtifactStore, checkpoint_id: str, run: Manifest, training: Manifest
) -> Manifest:
    saved = store.get_manifest(checkpoint_id)
    scoped = run_code_scope(run.parameters.value().get("schema")) in {
        TRAINING_SCOPE, *NATIVE_SCOPES
    }
    parent_roles = {"run", "training_input"}
    if any(parent.role == "resume_checkpoint" for parent in saved.parents):
        parent_roles.add("resume_checkpoint")
    _roles(saved, parent_roles, {"checkpoint"})
    info = object_fields(
        saved.parameters.value(),
        {
            "schema",
            "execution_identity_sha256",
            "boundary",
            "cursor",
            "phase",
            "optimizer_updates",
            "attempt",
        } | ({"attempt_producer"} if scoped else set()),
        "structured_checkpoint_manifest",
    )
    digest(info["attempt"], "structured_checkpoint_manifest.attempt", length=32)
    if scoped:
        declared_producer = Producer.decode(info["attempt_producer"])
        if (declared_producer != saved.producer or declared_producer.uv_lock_sha256 !=
                run.parameters.value()["execution_identity"]["code_identity"]["dependency_lock_sha256"]):
            raise BoundaryError("structured_workload", "checkpoint_attempt_producer_mismatch")
    if (
        saved.kind != "checkpoint"
        or not scoped and saved.producer != run.producer
        or saved.parent("run") != run.artifact_id
        or saved.parent("training_input") != training.artifact_id
        or info["schema"] != checkpoint_schema(
            run_code_scope(run.parameters.value().get("schema")))
        or info["execution_identity_sha256"]
        != semantic_hash(run.parameters.value()["execution_identity"])
    ):
        raise BoundaryError("structured_workload", "checkpoint_run_identity_mismatch")
    return saved


def _resume_checkpoint_depth(
    store: ArtifactStore, checkpoint_id: str, run: Manifest, training: Manifest,
    *, prospective_attempt_id: str | None = None,
) -> int:
    """Validate only bounded explicit attempt-start ancestry, not periodic chronology."""
    depth = 0
    seen: set[str] = set()
    # A legitimately transferred checkpoint closure need not carry sibling
    # run events. Bind freshness against every ancestor before creating an
    # engine, not only against the selected checkpoint or local event history.
    seen_attempts: set[str] = (
        {prospective_attempt_id} if prospective_attempt_id is not None else set()
    )
    while True:
        if checkpoint_id in seen:
            raise BoundaryError("structured_workload", "resume_checkpoint_cycle")
        seen.add(checkpoint_id)
        saved = _checkpoint_manifest(store, checkpoint_id, run, training)
        attempt = saved.parameters.value()["attempt"]
        if attempt in seen_attempts:
            raise BoundaryError("structured_workload", "resume_attempt_identity_reused")
        seen_attempts.add(attempt)
        if not any(parent.role == "resume_checkpoint" for parent in saved.parents):
            return depth
        depth += 1
        if depth > MAX_RESUME_ANCESTRY:
            raise BoundaryError("structured_workload", "resume_attempt_limit")
        checkpoint_id = saved.parent("resume_checkpoint")


def _checkpoint_parents(
    run: Manifest, training: Manifest, resume_checkpoint_id: str | None
) -> tuple[Parent, ...]:
    parents = (Parent("run", run.artifact_id), Parent("training_input", training.artifact_id))
    if resume_checkpoint_id is not None:
        return (*parents, Parent("resume_checkpoint", resume_checkpoint_id))
    return parents


def _checkpoint_bytes(
    store: ArtifactStore, checkpoint_id: str, run: Manifest, training: Manifest
) -> bytes:
    _resume_checkpoint_depth(store, checkpoint_id, run, training)
    saved = _checkpoint_manifest(store, checkpoint_id, run, training)
    info = saved.parameters.value()
    payload = saved.payload("checkpoint")
    if payload.media_type != "application/vnd.stpd.tensor-tree":
        raise BoundaryError("structured_workload", "checkpoint_media_type_mismatch")
    raw = _read_payload(store, payload, MAX_CHECKPOINT_BYTES)
    state = decode_checkpoint(raw)
    counters = object_fields(
        state.get("counters"),
        {"updates", "labels", "observations", "advances", "unlabelled_chunks"},
        "structured_checkpoint_payload_counters",
    )
    if (
        state.get("schema") != info["schema"]
        or json_bytes({key: info[key]
                       for key in ("boundary", "cursor", "phase", "optimizer_updates")})
        != json_bytes(
            {
                "boundary": state.get("boundary"),
                "cursor": state.get("cursor"),
                "phase": state.get("phase"),
                "optimizer_updates": counters["updates"],
            }
        )
        or semantic_hash(state.get("identity")) != info["execution_identity_sha256"]
    ):
        raise BoundaryError("structured_workload", "checkpoint_metadata_payload_mismatch")
    return raw


def _completed(
    store: ArtifactStore,
    result: Manifest,
    run: Manifest,
    training: Manifest,
    dataset: StructuredDataset,
    config: StructuredTrainingConfig,
) -> tuple[str, int]:
    from ..policy.structured_export import load_structured_package

    scope = run_code_scope(run.parameters.value().get("schema"))
    scoped = scope in {TRAINING_SCOPE, *NATIVE_SCOPES}
    native = scope in NATIVE_SCOPES
    run_info = run.parameters.value()
    policy = (checked_training_execution_policy(
        run_info["execution_policy"], dataset, scope,
        control_from_identity(run_info["execution_identity"]))
        if "execution_policy" in run_info else None)
    _checkpoint_manifest(store, result.parent("checkpoint"), run, training)
    # A publication-phase resume may export already-complete numerical state
    # from an earlier writer's immutable checkpoint. Bind the actual exporter
    # independently; reconciliation must not relabel either producer.
    completed_producer = result.producer if scoped else run.producer
    _roles(result, {"run", "training_input", "checkpoint", "model"}, {"report"})
    info = object_fields(
        result.parameters.value(),
        {
            "schema",
            "state",
            "partition",
            "source_sha256",
            "qualification",
            "attempt",
        },
        "structured_completed_result",
    )
    attempt = digest(info["attempt"], "structured_completed_result.attempt", length=32)
    if (
        result.kind != "run_result"
        or result.producer != completed_producer
        or scoped
        and (
            completed_producer.uv_lock_sha256
            != run.parameters.value()["execution_identity"]["code_identity"][
                "dependency_lock_sha256"
            ]
        )
        or info
        != {
            "schema": "stpd/run-result-v1",
            "state": "completed",
            "partition": "train",
            "source_sha256": dataset.source_sha256,
            "qualification": qualification(dataset),
            "attempt": attempt,
        }
        or result.parent("run") != run.artifact_id
        or result.parent("training_input") != training.artifact_id
    ):
        raise BoundaryError("structured_workload", "completed_result_identity_mismatch")
    engine = StructuredTrainingEngine(
        dataset,
        config,
        code_scope=scope,
        model_control=control_from_identity(run.parameters.value()["execution_identity"]),
    )
    engine.restore(_checkpoint_bytes(store, result.parent("checkpoint"), run, training))
    if engine.phase != "publication":
        raise BoundaryError("structured_workload", "completed_checkpoint_phase")
    model = store.get_manifest(result.parent("model"))
    _roles(model, {"run", "checkpoint", "training_input"}, {"package_manifest", "weights"})
    model_info = object_fields(
        model.parameters.value(),
        {
            "schema",
            "model_id",
            "graph_id",
            "qualification",
            "attempt",
        }
        | (
            {
                "input_spec",
                "agent_spec",
                "state_format_version",
                "code_identity",
                "source",
                "provenance",
            }
            if native
            else set()
        ),
        "structured_completed_model",
    )
    if (
        model.kind != "model"
        or model.producer != completed_producer
        or model_info["schema"]
        != (
            native_model_schema(scope, dataset)
            if native
            else SCOPED_MODEL_SCHEMA
            if scoped
            else MODEL_SCHEMA
        )
        or model_info["graph_id"] != engine.identity["graph_id"]
        or model_info["qualification"] != qualification(dataset)
        or model_info["attempt"] != attempt
        or model.parent("run") != run.artifact_id
        or model.parent("checkpoint") != result.parent("checkpoint")
        or model.parent("training_input") != training.artifact_id
    ):
        raise BoundaryError("structured_workload", "completed_model_identity_mismatch")
    with tempfile.TemporaryDirectory(prefix="structured-completion-") as folder:
        package = Path(folder)
        for role, name, maximum, media in (
            ("package_manifest", "model.json", MAX_MANIFEST_BYTES, "application/json"),
            (
                "weights",
                "weights.tensor-tree",
                MAX_WEIGHTS_BYTES,
                "application/vnd.stpd.tensor-tree",
            ),
        ):
            payload = model.payload(role)
            if payload.media_type != media:
                raise BoundaryError("structured_workload", "model_media_type_mismatch")
            (package / name).write_bytes(_read_payload(store, payload, maximum))
        if native:
            from ..policy.native_structured_export import (
                load_native_package,
                require_native_model_package,
            )

            metadata, restored = load_native_package(package)
            require_native_model_package(model, metadata)
            if _package_execution_policy(metadata, engine.model_control) != policy:
                raise BoundaryError("structured_workload", "package_execution_policy_mismatch")
        else:
            metadata, restored = load_structured_package(package)
        if scoped and metadata.get("provenance") != {
            "training_producer": run.producer.to_dict(),
            "export_producer": completed_producer.to_dict(),
            "run_id": run.artifact_id,
            "training_input_id": training.artifact_id,
            "checkpoint_id": result.parent("checkpoint"),
            "export_runtime": exporter_runtime(),
        }:
            raise BoundaryError("structured_workload", "completed_export_provenance_mismatch")
        if (
            metadata["model_id"] != model_info["model_id"]
            or metadata["source"]
            != (
                trained_source(dataset, scope, training.parent("source"), training.artifact_id)
                if native
                else {
                    "source_revision": run.producer.source_revision,
                    "data_sha256": dataset.source_sha256,
                    "source_kind": dataset.source_kind,
                    "teacher_sha256": semantic_hash(dataset.teacher.value()),
                }
            )
            or json_bytes(metadata["training"])
            != json_bytes({"config": asdict(config), "metrics": engine.metrics()})
            or any(
                not torch.equal(tensor, restored.state_dict()[name])
                for name, tensor in engine.model.state_dict().items()
            )
        ):
            raise BoundaryError("structured_workload", "completed_export_state_mismatch")
    payload = result.payload("report")
    if payload.media_type != "application/json":
        raise BoundaryError("structured_workload", "report_media_type_mismatch")
    raw = _read_payload(store, payload, MAX_REPORT_BYTES)
    report = object_fields(
        decode_json(raw),
        {
            "schema",
            "producer",
            "source_sha256",
            "config",
            "metrics",
            "execution_identity",
            "attempt",
            "attempt_seconds",
            "run_id",
            "training_input_id",
            "checkpoint_id",
            "model_artifact_id",
            "operation_id",
        }
        | ({"training_producer"} if scoped else set())
        | ({"execution_policy"} if policy is not None else set()),
        "structured_completed_report",
    )
    seconds = report["attempt_seconds"]
    if type(seconds) not in {int, float} or not math.isfinite(seconds) or seconds < 0:
        raise BoundaryError("structured_workload", "report_attempt_seconds_mismatch")
    expected_report = {
        "schema": (
            native_report_schema(scope)
            if native
            else SCOPED_REPORT_SCHEMA
            if scoped
            else "stpd/structured-m2-training-report-v2"
        ),
        "producer": completed_producer.to_dict(),
        **({"training_producer": run.producer.to_dict()} if scoped else {}),
        "source_sha256": dataset.source_sha256,
        "config": asdict(config),
        "metrics": engine.metrics(),
        "execution_identity": engine.identity,
        "attempt": attempt,
        "attempt_seconds": seconds,
        "run_id": run.artifact_id,
        "training_input_id": training.artifact_id,
        "checkpoint_id": result.parent("checkpoint"),
        "model_artifact_id": model.artifact_id,
        "operation_id": run.parameters.value()["operation_id"],
        **({"execution_policy": policy} if policy is not None else {}),
    }
    if raw != json_bytes(expected_report):
        raise BoundaryError("structured_workload", "completed_report_binding_mismatch")
    return model.artifact_id, engine.updates


def execute_structured_workload(
    store: ArtifactStore,
    reporter: RunReporter,
    request: StructuredWorkloadRequest,
    producer: Producer,
    *,
    authority: AttemptAuthority,
    control: ExecutionControl | None = None,
    attempt_producer: Producer | None = None,
) -> StructuredExecutionResult:
    """One explicit attempt; all publication follows the injected application fence."""
    started = time.perf_counter()
    request.validate()
    authority.assert_current(request.run_id, request.operation_id, request.attempt_id)
    run, training, dataset, config = _load(store, request, producer)
    policy = run.parameters.value().get("execution_policy")
    scope = run_code_scope(run.parameters.value().get("schema"))
    scoped = scope in {TRAINING_SCOPE, *NATIVE_SCOPES}
    native = scope in NATIVE_SCOPES
    if scoped:
        if (
            not isinstance(attempt_producer, Producer)
            or attempt_producer.uv_lock_sha256
            != run.parameters.value()["execution_identity"]["code_identity"][
                "dependency_lock_sha256"
            ]
        ):
            raise BoundaryError("structured_workload", "current_attempt_producer_required")
        publisher = attempt_producer
    else:
        if attempt_producer is not None and attempt_producer != producer:
            raise BoundaryError("structured_workload", "legacy_attempt_producer_mismatch")
        publisher = producer

    def guard() -> None:
        authority.assert_current(request.run_id, request.operation_id, request.attempt_id)

    guard()
    history = reporter.events(run.artifact_id)
    if any(
        event.kind != "run_event"
        or (
            event.producer != producer
            if not scoped
            else event.parameters.value().get("details", {}).get("attempt_producer")
            != event.producer.to_dict()
            or event.producer.uv_lock_sha256
            != run.parameters.value()["execution_identity"]["code_identity"][
                "dependency_lock_sha256"
            ]
        )
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
        if any(event.parameters.value().get("attempt") == request.attempt_id for event in history):
            raise BoundaryError("structured_workload", "resume_attempt_identity_reused")
        assert request.resume_checkpoint_id is not None
        authority.authorize_resume(
            run.artifact_id, request.attempt_id, request.resume_checkpoint_id
        )
        guard()
        assert request.resume_checkpoint_id is not None
        selected_checkpoint = _checkpoint_manifest(
            store, request.resume_checkpoint_id, run, training
        )
        if selected_checkpoint.parameters.value()["attempt"] == request.attempt_id:
            raise BoundaryError("structured_workload", "resume_attempt_identity_reused")
        if (
            _resume_checkpoint_depth(
                store,
                request.resume_checkpoint_id,
                run,
                training,
                prospective_attempt_id=request.attempt_id,
            )
            >= MAX_RESUME_ANCESTRY
        ):
            raise BoundaryError("structured_workload", "resume_attempt_limit")
    checkpoint_id = request.resume_checkpoint_id
    engine = StructuredTrainingEngine(
        dataset,
        config,
        code_scope=scope,
        model_control=control_from_identity(run.parameters.value()["execution_identity"]),
    )
    if checkpoint_id is not None:
        engine.restore(_checkpoint_bytes(store, checkpoint_id, run, training))

    reporting_failed = False
    completion_attempted = False

    def event(kind: str, **details: Any) -> None:
        nonlocal event_step, reporting_failed
        guard()
        candidate = Manifest(
            "run_event",
            publisher,
            (Parent("run", run.artifact_id),),
            parameters=FrozenObject.of(
                {
                    "schema": "stpd/run-event-v1",
                    "step": event_step + 1,
                    "attempt": request.attempt_id,
                    "kind": kind,
                    "details": {
                        **details,
                        **({"attempt_producer": publisher.to_dict()} if scoped else {}),
                    },
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
        previous_checkpoint_id = checkpoint_id
        parents = _checkpoint_parents(run, training, request.resume_checkpoint_id)
        saved = Manifest(
            "checkpoint",
            publisher,
            parents,
            (payload,),
            FrozenObject.of(
                {
                    "schema": engine.checkpoint_schema,
                    "execution_identity_sha256": semantic_hash(engine.identity),
                    "boundary": engine.boundary,
                    "cursor": asdict(engine.cursor),
                    "phase": engine.phase,
                    "optimizer_updates": engine.updates,
                    "attempt": request.attempt_id,
                    **({"attempt_producer": publisher.to_dict()} if scoped else {}),
                }
            ),
        )
        store.publish(saved)
        checkpoint_id = saved.artifact_id
        event(
            "checkpoint",
            checkpoint_id=checkpoint_id,
            previous_checkpoint_id=previous_checkpoint_id,
            resume_checkpoint_id=request.resume_checkpoint_id,
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
        from ..policy.structured_export import export_structured_package

        with tempfile.TemporaryDirectory(prefix="structured-export-") as folder:
            package = Path(folder) / "agent"
            if native:
                from ..policy.native_structured_export import (
                    export_native_trained_package,
                    export_native_trained_sampled_package,
                    export_ordered_native_trained_package,
                )

                exporter = (
                    export_native_trained_sampled_package
                    if common_sampled_source(dataset, scope) else
                    export_ordered_native_trained_package
                    if scope == ORDERED_SCOPE
                    else export_native_trained_package
                )
                metadata = exporter(
                    engine.model.eval(),
                    package,
                    producer=publisher,
                    data_sha256=dataset.source_sha256,
                    source=trained_source(
                        dataset, scope, training.parent("source"), training.artifact_id),
                    training={"config": asdict(config), "metrics": engine.metrics()},
                    provenance={
                        "training_producer": run.producer.to_dict(),
                        "export_producer": publisher.to_dict(),
                        "run_id": run.artifact_id,
                        "training_input_id": training.artifact_id,
                        "checkpoint_id": checkpoint_id,
                        "export_runtime": exporter_runtime(),
                    },
                    **({"execution_policy": policy} if policy is not None else {}),
                )
                if _package_execution_policy(metadata, engine.model_control) != policy:
                    raise BoundaryError("structured_workload", "package_execution_policy_mismatch")
            else:
                metadata = export_structured_package(
                    engine.model.eval(),
                    package,
                    source_revision=producer.source_revision,
                    data_sha256=dataset.source_sha256,
                    source_kind=dataset.source_kind,
                    teacher_sha256=semantic_hash(dataset.teacher.value()),
                    training={"config": asdict(config), "metrics": engine.metrics()},
                    code_scope=INFERENCE_SCOPE if scoped else LEGACY_SCOPE,
                    provenance=(
                        {
                            "training_producer": run.producer.to_dict(),
                            "export_producer": publisher.to_dict(),
                            "run_id": run.artifact_id,
                            "training_input_id": training.artifact_id,
                            "checkpoint_id": checkpoint_id,
                        }
                        if scoped
                        else None
                    ),
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
        if native:
            from ..policy.native_structured_export import native_model_parameters

            model_parameters = native_model_parameters(metadata, request.attempt_id)
        else:
            model_parameters = {
                "schema": SCOPED_MODEL_SCHEMA if scoped else MODEL_SCHEMA,
                "model_id": metadata["model_id"],
                "graph_id": engine.identity["graph_id"],
                "qualification": "engineering_only",
                "attempt": request.attempt_id,
            }
        model = Manifest(
            "model",
            publisher,
            (
                Parent("run", run.artifact_id),
                Parent("checkpoint", checkpoint_id),
                Parent("training_input", training.artifact_id),
            ),
            tuple(payloads),
            FrozenObject.of(model_parameters),
        )
        store.publish(model)
        report = {
            "schema": (
                native_report_schema(scope)
                if native
                else SCOPED_REPORT_SCHEMA
                if scoped
                else "stpd/structured-m2-training-report-v2"
            ),
            "run_id": run.artifact_id,
            "training_input_id": training.artifact_id,
            "checkpoint_id": checkpoint_id,
            "model_artifact_id": model.artifact_id,
            "operation_id": request.operation_id,
            "producer": publisher.to_dict(),
            **({"training_producer": run.producer.to_dict()} if scoped else {}),
            "source_sha256": dataset.source_sha256,
            "config": asdict(config),
            "metrics": engine.metrics(),
            "execution_identity": engine.identity,
            "attempt": request.attempt_id,
            "attempt_seconds": time.perf_counter() - started,
            **({"execution_policy": policy} if policy is not None else {}),
        }
        guard()
        report_payload = store.put_payload(
            "report", io.BytesIO(json_bytes(report)), "application/json"
        )
        result = Manifest(
            "run_result",
            publisher,
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
                    "qualification": qualification(dataset),
                    "attempt": request.attempt_id,
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
