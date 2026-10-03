"""Typed local public M2 run, explicit pause/resume and three epoch readouts.

The source owner admits use before preparation. This worker checks declared
parent identities and immutable bytes, not authoritative public-view reprojection
or train-only tokenizer fitting; an input digest cannot grant source admission.
The caller serializes writers for each run ID. Crash recovery reaches only the
last durable epoch or explicit-pause checkpoint advertised in run events.
"""

from __future__ import annotations

import hashlib
import io
import math
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, fields
from typing import Any

from torch import Tensor

from spireagent.artifact_contracts import Manifest, Parent, Payload, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, digest
from spireagent.storage.store import ArtifactStore

from ..fullrun.decision_training import ALLOCATION_SCHEMA
from ..fullrun.light_action_inputs import PUBLIC_COMPACT_VIEW_SCHEMA
from ..fullrun.public_inputs import COMPACT_IDENTITY
from ..fullrun.public_m2_input_storage import (
    MAX_INPUT_BYTES,
    MAX_TOKENIZER_BYTES,
    public_m2_source_binding_digest,
    read_public_m2_input,
)
from ..fullrun.public_m2_sequences import PublicM2Input
from ..models.token_core import ScratchShape
from .checkpoint_codec import decode_checkpoint
from .public_m2_engine import (
    PUBLIC_M2_CHECKPOINT_SCHEMA,
    PUBLIC_M2_EXPORT_SCHEMA,
    PublicM2Engine,
    PublicM2EngineChain,
    PublicM2EngineConfig,
    _tensor_digest,
)
from .reporting import RunReporter
from .worker import WorkerExecutionError, WorkerResult

INPUT_SCHEMA = "stpd/public-m2-worker-input-v1"
EXPERIMENT_SCHEMA = "stpd/public-m2-experiment-v1"
RUN_SCHEMA = "stpd/public-m2-run-v1"
CHECKPOINT_SCHEMA = "stpd/public-m2-worker-checkpoint-v1"
MODEL_SCHEMA = "stpd/public-m2-model-v1"
EVALUATION_SCHEMA = "stpd/public-m2-dev-evaluation-v1"
STAGE_SCHEMA = "stpd/public-m2-epoch-stage-v1"
RESULT_SCHEMA = "stpd/run-result-v1"
CHECKPOINT_POLICY = "each_epoch_and_explicit_pause_at_window_boundary"
MAX_CHECKPOINT_BYTES = 512 * 1024 * 1024
_BOUNDARY = "public_m2_run"


def _read(store: ArtifactStore, payload: Payload, maximum: int) -> bytes:
    if payload.size > maximum:
        raise BoundaryError(_BOUNDARY, "payload_size_limit")
    raw = b"".join(store.read_payload(payload))
    if len(raw) != payload.size or hashlib.sha256(raw).hexdigest() != payload.sha256:
        raise BoundaryError(_BOUNDARY, "payload_integrity_mismatch")
    return raw


def _source_lineage(
    store: ArtifactStore, source_view_id: str, allocation_id: str
) -> tuple[Manifest, Manifest]:
    public_m2_source_binding_digest(source_view_id, allocation_id)
    view = store.get_manifest(source_view_id)
    allocation = store.get_manifest(allocation_id)
    if (
        view.kind != "model_view"
        or view.parameters.value().get("schema") != PUBLIC_COMPACT_VIEW_SCHEMA
        or view.parameters.value().get("serializer") != COMPACT_IDENTITY
        or sorted(parent.role for parent in view.parents) != ["allocation", "dataset"]
        or view.parent("allocation") != allocation_id
        or allocation.kind != "protocol"
        or allocation.parameters.value().get("schema") != ALLOCATION_SCHEMA
        or [parent.role for parent in allocation.parents] != ["dataset"]
        or allocation.parent("dataset") != view.parent("dataset")
        or store.get_manifest(view.parent("dataset")).kind != "dataset"
    ):
        raise BoundaryError(_BOUNDARY, "source_lineage_mismatch")
    return view, allocation


def _chains(value: PublicM2Input, split: str) -> tuple[PublicM2EngineChain, ...]:
    return tuple(PublicM2EngineChain(chain.chain_id, chain.steps)
                 for chain in value.chains if chain.split == split)


def _engine(value: PublicM2Input, config: PublicM2EngineConfig) -> PublicM2Engine:
    if (
        config.source_digest != value.identity
        or config.state_tokenizer_sha256 != hashlib.sha256(value.state_tokenizer).hexdigest()
        or config.shape.vocab_size != _tokenizer_vocab_size(value.state_tokenizer)
        or config.max_action_bytes != value.max_action_bytes
        or config.shape.max_tokens < value.max_state_tokens
        or config.epochs != 5
    ):
        raise BoundaryError(_BOUNDARY, "input_config_mismatch")
    return PublicM2Engine(_chains(value, "train"), _chains(value, "dev"), config)


def _tokenizer_vocab_size(raw: bytes) -> int:
    from tokenizers import Tokenizer

    try:
        return int(Tokenizer.from_str(raw.decode("utf-8")).get_vocab_size())
    except Exception as error:
        raise BoundaryError(_BOUNDARY, "invalid_state_tokenizer") from error


def _config(value: object) -> PublicM2EngineConfig:
    if not isinstance(value, dict) or set(value) != {f.name for f in fields(PublicM2EngineConfig)}:
        raise BoundaryError(_BOUNDARY, "config_format_mismatch")
    shape = value.get("shape")
    if not isinstance(shape, dict) or set(shape) != {f.name for f in fields(ScratchShape)}:
        raise BoundaryError(_BOUNDARY, "shape_format_mismatch")
    return PublicM2EngineConfig(**{**value, "shape": ScratchShape(**shape)})


def prepare_public_m2_run(
    store: ArtifactStore,
    training_input: PublicM2Input,
    config: PublicM2EngineConfig,
    producer: Producer,
    *,
    source_view_id: str,
    allocation_id: str,
    operation_id: str,
) -> Manifest:
    """Publish one immutable five-epoch run after complete typed preflight."""
    if (not isinstance(training_input, PublicM2Input)
            or not isinstance(config, PublicM2EngineConfig)
            or not isinstance(producer, Producer)):
        raise BoundaryError(_BOUNDARY, "typed_prepare_required")
    digest(operation_id, _BOUNDARY + ".operation", length=32)
    raw = training_input.payload_bytes()
    if len(raw) > MAX_INPUT_BYTES:
        raise BoundaryError(_BOUNDARY, "payload_size_limit")
    value = read_public_m2_input(raw, training_input.state_tokenizer)
    if value != training_input:
        raise BoundaryError(_BOUNDARY, "input_roundtrip_mismatch")
    expected_source = public_m2_source_binding_digest(source_view_id, allocation_id)
    if value.source_binding_digest != expected_source:
        raise BoundaryError(_BOUNDARY, "source_binding_mismatch")
    engine = _engine(value, config)
    _source_lineage(store, source_view_id, allocation_id)
    # One caller-held writer lock is still required. This read makes an
    # already-published operation idempotent and rejects a new meaning for it;
    # ArtifactStore has no atomic operation-ID index of its own.
    for identity in store.manifest_ids():
        existing = store.get_manifest(identity)
        existing_info = existing.parameters.value()
        if (existing.kind != "run" or existing_info.get("schema") != RUN_SCHEMA
                or existing_info.get("operation_id") != operation_id):
            continue
        if (
            existing.producer != producer
            or existing_info.get("config") != asdict(config)
            or existing_info.get("input_identity") != value.identity
            or existing_info.get("source_binding_digest") != expected_source
            or existing_info.get("source_view_id") != source_view_id
            or existing_info.get("allocation_id") != allocation_id
            or existing_info.get("engine_input_digest") != engine.input_digest
            or existing_info.get("implementation_sha256")
            != engine.runtime["implementation_sha256"]
        ):
            raise BoundaryError(_BOUNDARY, "operation_id_collision")
        _load_run(store, existing.artifact_id, producer)
        return existing
    input_payload = store.put_payload("training_input", io.BytesIO(raw), "application/json")
    tokenizer_payload = store.put_payload(
        "state_tokenizer", io.BytesIO(value.state_tokenizer), "application/json"
    )
    training = Manifest(
        "training_input", producer,
        (Parent("source_view", source_view_id), Parent("allocation", allocation_id)),
        (input_payload, tokenizer_payload),
        FrozenObject.of({
            "schema": INPUT_SCHEMA, "input_identity": value.identity,
            "source_binding_digest": expected_source,
            "train_chain_count": len(_chains(value, "train")),
            "dev_chain_count": len(_chains(value, "dev")),
            "qualification": "engineering_only",
        }),
    )
    store.publish(training)
    experiment = Manifest(
        "experiment", producer, (Parent("training_input", training.artifact_id),),
        parameters=FrozenObject.of({
            "schema": EXPERIMENT_SCHEMA, "purpose": "scratch_public_m2_five_epoch",
            "config": asdict(config),
        }),
    )
    store.publish(experiment)
    run = Manifest(
        "run", producer,
        (Parent("training_input", training.artifact_id),
         Parent("experiment", experiment.artifact_id)),
        parameters=FrozenObject.of({
            "schema": RUN_SCHEMA, "operation_id": operation_id,
            "config": asdict(config), "input_identity": value.identity,
            "engine_input_digest": engine.input_digest,
            "implementation_sha256": engine.runtime["implementation_sha256"],
            "source_binding_digest": expected_source,
            "source_view_id": source_view_id, "allocation_id": allocation_id,
            "checkpoint_policy": CHECKPOINT_POLICY, "partition": "train",
        }),
    )
    store.publish(run)
    return run


def _load_run(
    store: ArtifactStore, run_id: str, runtime: Producer
) -> tuple[Manifest, Manifest, PublicM2Input, PublicM2EngineConfig, PublicM2Engine]:
    run = store.get_manifest(run_id)
    info = run.parameters.value()
    if (
        run.kind != "run" or run.producer != runtime
        or set(info) != {
            "schema", "operation_id", "config", "input_identity", "engine_input_digest",
            "implementation_sha256", "source_binding_digest", "source_view_id",
            "allocation_id", "checkpoint_policy", "partition",
        }
        or info["schema"] != RUN_SCHEMA or info["checkpoint_policy"] != CHECKPOINT_POLICY
        or info["partition"] != "train"
        or sorted(parent.role for parent in run.parents) != ["experiment", "training_input"]
    ):
        raise BoundaryError(_BOUNDARY, "run_identity_mismatch")
    digest(info["operation_id"], _BOUNDARY + ".operation", length=32)
    training = store.get_manifest(run.parent("training_input"))
    training_info = training.parameters.value()
    if (
        training.kind != "training_input" or training.producer != runtime
        or sorted(parent.role for parent in training.parents) != ["allocation", "source_view"]
        or sorted(payload.role for payload in training.payloads)
        != ["state_tokenizer", "training_input"]
        or set(training_info) != {
            "schema", "input_identity", "source_binding_digest", "train_chain_count",
            "dev_chain_count", "qualification",
        }
        or training_info["schema"] != INPUT_SCHEMA
        or training_info["qualification"] != "engineering_only"
        or training.parent("source_view") != info["source_view_id"]
        or training.parent("allocation") != info["allocation_id"]
    ):
        raise BoundaryError(_BOUNDARY, "training_input_manifest_mismatch")
    _source_lineage(store, info["source_view_id"], info["allocation_id"])
    raw = _read(store, training.payload("training_input"), MAX_INPUT_BYTES)
    tokenizer = _read(store, training.payload("state_tokenizer"), MAX_TOKENIZER_BYTES)
    value = read_public_m2_input(raw, tokenizer)
    source_digest = public_m2_source_binding_digest(
        info["source_view_id"], info["allocation_id"]
    )
    if (
        source_digest != value.source_binding_digest
        or training_info["source_binding_digest"] != source_digest
        or info["source_binding_digest"] != source_digest
        or training_info["input_identity"] != value.identity
        or info["input_identity"] != value.identity
        or training_info["train_chain_count"] != len(_chains(value, "train"))
        or training_info["dev_chain_count"] != len(_chains(value, "dev"))
    ):
        raise BoundaryError(_BOUNDARY, "input_identity_mismatch")
    config = _config(info["config"])
    engine = _engine(value, config)
    experiment = store.get_manifest(run.parent("experiment"))
    if (
        experiment.kind != "experiment" or experiment.producer != runtime
        or sorted(parent.role for parent in experiment.parents) != ["training_input"]
        or experiment.parent("training_input") != training.artifact_id
        or experiment.parameters.value() != {
            "schema": EXPERIMENT_SCHEMA, "purpose": "scratch_public_m2_five_epoch",
            "config": asdict(config),
        }
        or info["engine_input_digest"] != engine.input_digest
        or info["implementation_sha256"] != engine.runtime["implementation_sha256"]
    ):
        raise BoundaryError(_BOUNDARY, "run_input_mismatch")
    return run, training, value, config, engine


def _checkpoint(
    store: ArtifactStore, run: Manifest, training: Manifest,
    engine: PublicM2Engine, event: Callable[..., None],
) -> str:
    raw = engine.checkpoint()
    if len(raw) > MAX_CHECKPOINT_BYTES:
        raise BoundaryError(_BOUNDARY, "checkpoint_size_limit")
    payload = store.put_payload(
        "checkpoint", io.BytesIO(raw), "application/vnd.stpd.tensor-tree"
    )
    item = Manifest(
        "checkpoint", run.producer,
        (Parent("run", run.artifact_id), Parent("training_input", training.artifact_id)),
        (payload,), FrozenObject.of({
            "schema": CHECKPOINT_SCHEMA,
            "operation_id": run.parameters.value()["operation_id"],
            "input_identity": run.parameters.value()["input_identity"],
            "engine_input_digest": engine.input_digest,
            "implementation_sha256": engine.runtime["implementation_sha256"],
            "config": asdict(engine.config),
            "engine_schema": PUBLIC_M2_CHECKPOINT_SCHEMA,
            "completed_epochs": engine.completed_epochs,
            "chain_index": engine.chain_index, "window_cursor": engine.window_cursor,
            "label_count": engine.label_count,
            "optimizer_updates": engine.optimizer_updates,
        }),
    )
    store.publish(item)
    event("checkpoint", checkpoint_id=item.artifact_id)
    return item.artifact_id


def _restore(
    store: ArtifactStore, checkpoint_id: str, run: Manifest, training: Manifest,
    engine: PublicM2Engine,
) -> tuple[dict[str, Any], str]:
    item = store.get_manifest(checkpoint_id)
    info = item.parameters.value()
    if (
        item.kind != "checkpoint" or item.producer != run.producer
        or sorted(parent.role for parent in item.parents) != ["run", "training_input"]
        or item.parent("run") != run.artifact_id
        or item.parent("training_input") != training.artifact_id
        or [payload.role for payload in item.payloads] != ["checkpoint"]
        or set(info) != {
            "schema", "operation_id", "input_identity", "engine_input_digest",
            "implementation_sha256", "config", "engine_schema", "completed_epochs",
            "chain_index", "window_cursor", "label_count", "optimizer_updates",
        }
        or info["schema"] != CHECKPOINT_SCHEMA
        or info["engine_schema"] != PUBLIC_M2_CHECKPOINT_SCHEMA
        or info["operation_id"] != run.parameters.value()["operation_id"]
        or info["input_identity"] != run.parameters.value()["input_identity"]
        or info["engine_input_digest"] != engine.input_digest
        or info["implementation_sha256"] != engine.runtime["implementation_sha256"]
        or info["config"] != asdict(engine.config)
    ):
        raise BoundaryError(_BOUNDARY, "checkpoint_lineage_mismatch")
    raw = _read(store, item.payload("checkpoint"), MAX_CHECKPOINT_BYTES)
    decoded = decode_checkpoint(raw)
    if (
        any(info[key] != decoded[key] for key in (
            "completed_epochs", "chain_index", "window_cursor", "label_count",
            "optimizer_updates",
        ))
        or decoded["input_digest"] != engine.input_digest
    ):
        raise BoundaryError(_BOUNDARY, "checkpoint_progress_mismatch")
    engine.restore(raw)
    return decoded, hashlib.sha256(raw).hexdigest()


def _stage(
    store: ArtifactStore, run: Manifest, training: Manifest, engine: PublicM2Engine,
    checkpoint_id: str,
) -> str:
    epoch = engine.completed_epochs
    if epoch not in {1, 3, 5} or engine.chain_index != 0 or engine.window_cursor != 0:
        raise BoundaryError(_BOUNDARY, "stage_epoch_boundary_required")
    metrics = engine.evaluate_dev()
    raw_weights = engine.export_weights()
    if len(raw_weights) > MAX_CHECKPOINT_BYTES:
        raise BoundaryError(_BOUNDARY, "weights_size_limit")
    exported = decode_checkpoint(raw_weights)
    if (exported["schema"] != PUBLIC_M2_EXPORT_SCHEMA
            or exported["completed_epochs"] != epoch
            or exported["run_complete"] is not (epoch == 5)):
        raise BoundaryError(_BOUNDARY, "stage_export_mismatch")
    weights = store.put_payload(
        "weights", io.BytesIO(raw_weights), "application/vnd.stpd.tensor-tree"
    )
    tokenizer = training.payload("state_tokenizer")
    model = Manifest(
        "model", run.producer,
        (Parent("run", run.artifact_id), Parent("training_input", training.artifact_id),
         Parent("checkpoint", checkpoint_id)),
        (weights, Payload("state_tokenizer", tokenizer.sha256, tokenizer.size,
                          tokenizer.media_type)),
        FrozenObject.of({
            "schema": MODEL_SCHEMA, "epoch": epoch, "run_complete": epoch == 5,
            "operation_id": run.parameters.value()["operation_id"],
            "config": asdict(engine.config),
            "input_identity": run.parameters.value()["input_identity"],
            "engine_input_digest": engine.input_digest,
            "implementation_sha256": engine.runtime["implementation_sha256"],
            "qualification": "engineering_only",
        }),
    )
    store.publish(model)
    evaluation = Manifest(
        "offline_evaluation", run.producer,
        (Parent("run", run.artifact_id), Parent("training_input", training.artifact_id),
         Parent("checkpoint", checkpoint_id), Parent("model", model.artifact_id)),
        parameters=FrozenObject.of({
            "schema": EVALUATION_SCHEMA, "epoch": epoch, "partition": "dev",
            "input_identity": run.parameters.value()["input_identity"],
            "loss_mean": metrics.loss_mean, "top1_accuracy": metrics.top1_accuracy,
            "label_count": metrics.label_count, "correct_count": metrics.correct_count,
            "qualification": "engineering_only",
        }),
    )
    store.publish(evaluation)
    stage = Manifest(
        "analysis", run.producer,
        (Parent("run", run.artifact_id), Parent("training_input", training.artifact_id),
         Parent("checkpoint", checkpoint_id), Parent("model", model.artifact_id),
         Parent("offline_evaluation", evaluation.artifact_id)),
        parameters=FrozenObject.of({
            "schema": STAGE_SCHEMA, "epoch": epoch, "run_complete": epoch == 5,
            "operation_id": run.parameters.value()["operation_id"],
            "input_identity": run.parameters.value()["input_identity"],
        }),
    )
    store.publish(stage)
    return stage.artifact_id


def _verify_completed(
    store: ArtifactStore, result: Manifest, run: Manifest, training: Manifest,
    engine: PublicM2Engine,
) -> None:
    if (
        result.kind != "run_result" or result.producer != run.producer
        or sorted(parent.role for parent in result.parents) != [
            "checkpoint", "model", "offline_evaluation", "run", "stage", "training_input",
        ]
        or result.parent("run") != run.artifact_id
        or result.parent("training_input") != training.artifact_id
        or result.parameters.value() != {
            "schema": RESULT_SCHEMA, "state": "completed", "epoch": 5,
            "operation_id": run.parameters.value()["operation_id"],
            "input_identity": run.parameters.value()["input_identity"],
            "qualification": "engineering_only",
        }
    ):
        raise BoundaryError(_BOUNDARY, "completed_result_mismatch")
    stage = store.get_manifest(result.parent("stage"))
    model = store.get_manifest(result.parent("model"))
    evaluation = store.get_manifest(result.parent("offline_evaluation"))
    checkpoint_id = result.parent("checkpoint")
    expected_model = {
        "schema": MODEL_SCHEMA, "epoch": 5, "run_complete": True,
        "operation_id": run.parameters.value()["operation_id"],
        "config": asdict(engine.config),
        "input_identity": run.parameters.value()["input_identity"],
        "engine_input_digest": engine.input_digest,
        "implementation_sha256": engine.runtime["implementation_sha256"],
        "qualification": "engineering_only",
    }
    if (
        stage.kind != "analysis" or stage.producer != run.producer
        or sorted(parent.role for parent in stage.parents) != [
            "checkpoint", "model", "offline_evaluation", "run", "training_input",
        ]
        or stage.parameters.value() != {
            "schema": STAGE_SCHEMA, "epoch": 5, "run_complete": True,
            "operation_id": run.parameters.value()["operation_id"],
            "input_identity": run.parameters.value()["input_identity"],
        }
        or stage.parent("model") != model.artifact_id
        or stage.parent("offline_evaluation") != evaluation.artifact_id
        or stage.parent("checkpoint") != checkpoint_id
        or stage.parent("run") != run.artifact_id
        or stage.parent("training_input") != training.artifact_id
        or model.kind != "model" or model.producer != run.producer
        or model.parameters.value() != expected_model
        or sorted(parent.role for parent in model.parents)
        != ["checkpoint", "run", "training_input"]
        or sorted(payload.role for payload in model.payloads)
        != ["state_tokenizer", "weights"]
        or model.parent("checkpoint") != checkpoint_id
        or model.parent("run") != run.artifact_id
        or model.parent("training_input") != training.artifact_id
        or evaluation.kind != "offline_evaluation" or evaluation.producer != run.producer
        or evaluation.parameters.value().get("schema") != EVALUATION_SCHEMA
        or evaluation.parameters.value().get("epoch") != 5
        or sorted(parent.role for parent in evaluation.parents)
        != ["checkpoint", "model", "run", "training_input"]
        or evaluation.parent("model") != model.artifact_id
        or evaluation.parent("checkpoint") != checkpoint_id
        or evaluation.parent("run") != run.artifact_id
        or evaluation.parent("training_input") != training.artifact_id
    ):
        raise BoundaryError(_BOUNDARY, "completed_stage_mismatch")
    checkpoint, checkpoint_sha256 = _restore(store, checkpoint_id, run, training, engine)
    if not engine.finished:
        raise BoundaryError(_BOUNDARY, "incomplete_terminal_checkpoint")
    metrics = evaluation.parameters.value()
    dev_labels = sum(len(chain.steps) for chain in engine.dev_chains)
    if (
        set(metrics) != {
            "schema", "epoch", "partition", "input_identity", "loss_mean",
            "top1_accuracy", "label_count", "correct_count", "qualification",
        }
        or metrics["schema"] != EVALUATION_SCHEMA
        or metrics["epoch"] != 5
        or metrics["partition"] != "dev"
        or metrics["input_identity"] != run.parameters.value()["input_identity"]
        or type(metrics["label_count"]) is not int
        or metrics["label_count"] != dev_labels
        or type(metrics["correct_count"]) is not int
        or not 0 <= metrics["correct_count"] <= dev_labels
        or type(metrics["top1_accuracy"]) not in {float, int}
        or not math.isfinite(metrics["top1_accuracy"])
        or metrics["top1_accuracy"] != metrics["correct_count"] / dev_labels
        or type(metrics["loss_mean"]) not in {float, int}
        or not math.isfinite(metrics["loss_mean"])
        or metrics["loss_mean"] < 0
        or metrics["qualification"] != "engineering_only"
    ):
        raise BoundaryError(_BOUNDARY, "completed_evaluation_mismatch")
    raw_weights = _read(store, model.payload("weights"), MAX_CHECKPOINT_BYTES)
    exported = decode_checkpoint(raw_weights)
    source_tokenizer = training.payload("state_tokenizer")
    if (
        set(exported) != {
            "schema", "config", "input_digest", "implementation_sha256",
            "completed_epochs", "optimizer_updates", "run_complete",
            "checkpoint_digest", "weights", "weights_digest",
        }
        or exported["schema"] != PUBLIC_M2_EXPORT_SCHEMA
        or exported["config"] != asdict(engine.config)
        or exported["input_digest"] != engine.input_digest
        or exported["implementation_sha256"] != engine.runtime["implementation_sha256"]
        or exported["completed_epochs"] != 5
        or exported["run_complete"] is not True
        or exported["optimizer_updates"] != checkpoint["optimizer_updates"]
        or exported["checkpoint_digest"] != checkpoint_sha256
        or not isinstance(exported["weights"], dict)
        or set(exported["weights"]) != set(checkpoint["model"])
        or any(not isinstance(value, Tensor) for value in exported["weights"].values())
        or exported["weights_digest"] != _tensor_digest(exported["weights"])
        or exported["weights_digest"] != checkpoint["model_digest"]
        or model.payload("state_tokenizer") != Payload(
            "state_tokenizer", source_tokenizer.sha256,
            source_tokenizer.size, source_tokenizer.media_type,
        )
    ):
        raise BoundaryError(_BOUNDARY, "completed_weights_mismatch")


def execute_public_m2_run(
    store: ArtifactStore, reporter: RunReporter, run_id: str, runtime: Producer,
    *, resume: str | None = None, stop_after_windows: int | None = None,
) -> WorkerResult:
    """Execute one bounded attempt; explicit resume selects one durable checkpoint."""
    if (stop_after_windows is not None and
            (type(stop_after_windows) is not int or stop_after_windows < 1)):
        raise BoundaryError(_BOUNDARY, "invalid_pause_budget")
    run, training, _, _, engine = _load_run(store, run_id, runtime)
    completed = reporter.completed(run_id)
    if completed is not None:
        _verify_completed(store, completed, run, training, engine)
        return WorkerResult("completed", run_id, result_id=completed.artifact_id)
    events = reporter.events(run_id)
    if resume is None and events:
        raise BoundaryError(_BOUNDARY, "existing_attempt_requires_explicit_resume")
    if resume is not None:
        checkpoints = [event for event in events
                       if event.parameters.value().get("kind") == "checkpoint"
                       and event.parent("run") == run_id]
        matching = [event for event in checkpoints
                    if event.parameters.value().get("details", {}).get("checkpoint_id")
                    == resume]
        if (not matching or any(
            type(event.parameters.value().get("optimizer_updates")) is not int
            for event in checkpoints
        ) or max(event.parameters.value()["optimizer_updates"] for event in matching)
                != max(event.parameters.value()["optimizer_updates"] for event in checkpoints)):
            raise BoundaryError(_BOUNDARY, "resume_requires_latest_reported_checkpoint")
    attempt = uuid.uuid4().hex

    def event(kind: str, **details: object) -> None:
        reporter.emit(Manifest(
            "run_event", runtime, (Parent("run", run_id),),
            parameters=FrozenObject.of({
                "schema": "stpd/run-event-v1", "attempt": attempt,
                "kind": kind, "completed_epochs": engine.completed_epochs,
                "chain_index": engine.chain_index, "window_cursor": engine.window_cursor,
                "label_count": engine.label_count,
                "optimizer_updates": engine.optimizer_updates,
                "details": details,
            }),
        ))

    try:
        event("loading", resume=resume)
        if resume is not None:
            _restore(store, resume, run, training, engine)
        event("resumed" if resume else "started")
        checkpoint_id: str | None = resume
        initial_updates = engine.optimizer_updates
        # A crash after an epoch checkpoint but before its readout can be
        # reconciled idempotently on explicit resume of that exact checkpoint.
        if (engine.completed_epochs in {1, 3, 5} and resume is not None
                and engine.chain_index == 0 and engine.window_cursor == 0):
            stage_id = _stage(store, run, training, engine, resume)
            event("epoch_stage", epoch=engine.completed_epochs, stage_id=stage_id)
        else:
            stage_id = None
        while not engine.finished:
            before_epoch = engine.completed_epochs
            progress = engine.advance_window()
            event("window_completed", loss_mean=progress.loss_mean,
                  labels=progress.label_count)
            if engine.completed_epochs != before_epoch:
                checkpoint_id = _checkpoint(store, run, training, engine, event)
                if engine.completed_epochs in {1, 3, 5}:
                    stage_id = _stage(store, run, training, engine, checkpoint_id)
                    event("epoch_stage", epoch=engine.completed_epochs, stage_id=stage_id)
            if (stop_after_windows is not None
                    and engine.optimizer_updates - initial_updates >= stop_after_windows
                    and not engine.finished):
                checkpoint_id = _checkpoint(store, run, training, engine, event)
                event("paused", checkpoint_id=checkpoint_id)
                return WorkerResult("paused", run_id, checkpoint_id=checkpoint_id)
        if checkpoint_id is None or stage_id is None:
            raise BoundaryError(_BOUNDARY, "missing_terminal_stage")
        stage = store.get_manifest(stage_id)
        result = Manifest(
            "run_result", runtime,
            (Parent("run", run_id), Parent("training_input", training.artifact_id),
             Parent("checkpoint", checkpoint_id), Parent("stage", stage_id),
             Parent("model", stage.parent("model")),
             Parent("offline_evaluation", stage.parent("offline_evaluation"))),
            parameters=FrozenObject.of({
                "schema": RESULT_SCHEMA, "state": "completed", "epoch": 5,
                "operation_id": run.parameters.value()["operation_id"],
                "input_identity": run.parameters.value()["input_identity"],
                "qualification": "engineering_only",
            }),
        )
        _verify_completed(store, result, run, training, engine)
        result_id = reporter.complete(result)
        selected = reporter.completed(run_id)
        if selected is None or selected.artifact_id != result_id:
            raise BoundaryError(_BOUNDARY, "completion_race")
        event("completed", result_id=result_id)
        return WorkerResult("completed", run_id, checkpoint_id, result_id)
    except WorkerExecutionError:
        raise
    except Exception as error:
        with suppress(Exception):
            event("failed", error_type=type(error).__name__)
        raise WorkerExecutionError(
            "execution_failed", failure_durable=bool(reporter.events(run_id))
        ) from error
