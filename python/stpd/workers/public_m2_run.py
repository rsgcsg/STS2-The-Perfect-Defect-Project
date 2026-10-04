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
from dataclasses import asdict, dataclass, fields
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
from .public_m2_preflight import preflight_public_m2_engine
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
    _check_engine_input(value, config)
    return PublicM2Engine(_chains(value, "train"), _chains(value, "dev"), config)


def _check_engine_input(value: PublicM2Input, config: PublicM2EngineConfig) -> None:
    if (
        config.source_digest != value.identity
        or config.state_tokenizer_sha256 != hashlib.sha256(value.state_tokenizer).hexdigest()
        or config.shape.vocab_size != _tokenizer_vocab_size(value.state_tokenizer)
        or config.max_action_bytes != value.max_action_bytes
        or config.shape.max_tokens < value.max_state_tokens
        or config.epochs != 5
    ):
        raise BoundaryError(_BOUNDARY, "input_config_mismatch")


def _preflight_engine(value: PublicM2Input, config: PublicM2EngineConfig) -> Any:
    _check_engine_input(value, config)
    return preflight_public_m2_engine(_chains(value, "train"), _chains(value, "dev"), config)


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


@dataclass(frozen=True)
class _RunFlavor:
    input_schema: str
    experiment_schema: str
    run_schema: str
    checkpoint_schema: str
    model_schema: str
    evaluation_schema: str
    stage_schema: str
    engine_checkpoint_schema: str
    engine_export_schema: str
    purpose: str
    model_kind: str | None
    config_decode: Callable[[object], Any]
    engine_factory: Callable[[PublicM2Input, Any], Any]
    preflight_factory: Callable[[PublicM2Input, Any], Any]
    input_binding: Callable[[Any], str]
    checkpoint_binding_key: str
    export_binding_key: str
    export_extra_fields: frozenset[str]
    export_extra_expected: Callable[[Any], dict[str, Any]]
    verify_reference: Callable[[ArtifactStore, str, Producer, PublicM2Input, Any], None] | None


_M2_FLAVOR = _RunFlavor(
    INPUT_SCHEMA, EXPERIMENT_SCHEMA, RUN_SCHEMA, CHECKPOINT_SCHEMA, MODEL_SCHEMA,
    EVALUATION_SCHEMA, STAGE_SCHEMA, PUBLIC_M2_CHECKPOINT_SCHEMA,
    PUBLIC_M2_EXPORT_SCHEMA, "scratch_public_m2_five_epoch", None,
    _config, _engine, _preflight_engine, lambda engine: engine.input_digest,
    "input_digest", "input_digest", frozenset(), lambda engine: {}, None,
)


def _kind(flavor: _RunFlavor) -> dict[str, str]:
    return {} if flavor.model_kind is None else {"model_kind": flavor.model_kind}


def _reference_parents(flavor: _RunFlavor, reference: str | None) -> tuple[Parent, ...]:
    if flavor.verify_reference is None:
        if reference is not None:
            raise BoundaryError(_BOUNDARY, "unexpected_matched_reference")
        return ()
    if reference is None:
        raise BoundaryError(_BOUNDARY, "matched_m2_run_required")
    digest(reference, _BOUNDARY + ".matched_m2_run")
    return (Parent("matched_m2_run", reference),)


def _prepare_run(
    store: ArtifactStore,
    training_input: PublicM2Input,
    config: Any,
    producer: Producer,
    *,
    source_view_id: str,
    allocation_id: str,
    operation_id: str,
    flavor: _RunFlavor,
    reference_run_id: str | None = None,
) -> Manifest:
    """Publish one immutable five-epoch run after complete typed preflight."""
    if not isinstance(training_input, PublicM2Input) or not isinstance(producer, Producer):
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
    _source_lineage(store, source_view_id, allocation_id)
    reference_parents = _reference_parents(flavor, reference_run_id)
    if flavor.verify_reference is not None:
        assert reference_run_id is not None
        flavor.verify_reference(store, reference_run_id, producer, value, config)
    engine = flavor.preflight_factory(value, config)
    # One caller-held writer lock is still required. This read makes an
    # already-published operation idempotent and rejects a new meaning for it;
    # ArtifactStore has no atomic operation-ID index of its own.
    for identity in store.manifest_ids():
        existing = store.get_manifest(identity)
        existing_info = existing.parameters.value()
        if (existing.kind != "run" or existing_info.get("schema") != flavor.run_schema
                or existing_info.get("operation_id") != operation_id):
            continue
        if (
            existing.producer != producer
            or existing_info.get("config") != asdict(config)
            or existing_info.get("input_identity") != value.identity
            or existing_info.get("source_binding_digest") != expected_source
            or existing_info.get("source_view_id") != source_view_id
            or existing_info.get("allocation_id") != allocation_id
            or existing_info.get("engine_input_digest") != flavor.input_binding(engine)
            or existing_info.get("implementation_sha256")
            != engine.runtime["implementation_sha256"]
            or existing_info.get("matched_m2_run_id") != reference_run_id
        ):
            raise BoundaryError(_BOUNDARY, "operation_id_collision")
        _load_run(store, existing.artifact_id, producer, flavor=flavor, preflight_only=True)
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
            "schema": flavor.input_schema, "input_identity": value.identity,
            "source_binding_digest": expected_source,
            "train_chain_count": len(_chains(value, "train")),
            "dev_chain_count": len(_chains(value, "dev")),
            "qualification": "engineering_only", **_kind(flavor),
        }),
    )
    store.publish(training)
    experiment = Manifest(
        "experiment", producer, (Parent("training_input", training.artifact_id),),
        parameters=FrozenObject.of({
            "schema": flavor.experiment_schema, "purpose": flavor.purpose,
            "config": asdict(config), **_kind(flavor),
        }),
    )
    store.publish(experiment)
    run = Manifest(
        "run", producer,
        (Parent("training_input", training.artifact_id),
         Parent("experiment", experiment.artifact_id), *reference_parents),
        parameters=FrozenObject.of({
            "schema": flavor.run_schema, "operation_id": operation_id,
            "config": asdict(config), "input_identity": value.identity,
            "engine_input_digest": flavor.input_binding(engine),
            "implementation_sha256": engine.runtime["implementation_sha256"],
            "source_binding_digest": expected_source,
            "source_view_id": source_view_id, "allocation_id": allocation_id,
            "checkpoint_policy": CHECKPOINT_POLICY, "partition": "train",
            **_kind(flavor),
            **({} if reference_run_id is None else {"matched_m2_run_id": reference_run_id}),
        }),
    )
    store.publish(run)
    return run


def prepare_public_m2_run(
    store: ArtifactStore, training_input: PublicM2Input, config: PublicM2EngineConfig,
    producer: Producer, *, source_view_id: str, allocation_id: str, operation_id: str,
) -> Manifest:
    if not isinstance(config, PublicM2EngineConfig):
        raise BoundaryError(_BOUNDARY, "typed_prepare_required")
    return _prepare_run(
        store, training_input, config, producer, source_view_id=source_view_id,
        allocation_id=allocation_id, operation_id=operation_id, flavor=_M2_FLAVOR,
    )


def _load_run(
    store: ArtifactStore, run_id: str, runtime: Producer, *, flavor: _RunFlavor = _M2_FLAVOR,
    preflight_only: bool = False,
) -> tuple[Manifest, Manifest, PublicM2Input, Any, Any]:
    run = store.get_manifest(run_id)
    info = run.parameters.value()
    if (
        run.kind != "run" or run.producer != runtime
        or set(info) != {
            "schema", "operation_id", "config", "input_identity", "engine_input_digest",
            "implementation_sha256", "source_binding_digest", "source_view_id",
            "allocation_id", "checkpoint_policy", "partition",
            *(_kind(flavor)),
            *({"matched_m2_run_id"} if flavor.verify_reference is not None else set()),
        }
        or info["schema"] != flavor.run_schema
        or any(info[key] != value for key, value in _kind(flavor).items())
        or info["checkpoint_policy"] != CHECKPOINT_POLICY
        or info["partition"] != "train"
        or sorted(parent.role for parent in run.parents) != sorted(
            ["experiment", "training_input", *(
                ["matched_m2_run"] if flavor.verify_reference is not None else []
            )]
        )
        or (flavor.verify_reference is not None and
            run.parent("matched_m2_run") != info["matched_m2_run_id"])
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
            "dev_chain_count", "qualification", *(_kind(flavor)),
        }
        or training_info["schema"] != flavor.input_schema
        or any(training_info[key] != value for key, value in _kind(flavor).items())
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
    config = flavor.config_decode(info["config"])
    if flavor.verify_reference is not None:
        flavor.verify_reference(store, info["matched_m2_run_id"], runtime, value, config)
    engine = (flavor.preflight_factory(value, config) if preflight_only
              else flavor.engine_factory(value, config))
    experiment = store.get_manifest(run.parent("experiment"))
    if (
        experiment.kind != "experiment" or experiment.producer != runtime
        or sorted(parent.role for parent in experiment.parents) != ["training_input"]
        or experiment.parent("training_input") != training.artifact_id
        or experiment.parameters.value() != {
            "schema": flavor.experiment_schema, "purpose": flavor.purpose,
            "config": asdict(config), **_kind(flavor),
        }
        or info["engine_input_digest"] != flavor.input_binding(engine)
        or info["implementation_sha256"] != engine.runtime["implementation_sha256"]
    ):
        raise BoundaryError(_BOUNDARY, "run_input_mismatch")
    return run, training, value, config, engine


def _checkpoint(
    store: ArtifactStore, run: Manifest, training: Manifest,
    engine: Any, event: Callable[..., None], *, flavor: _RunFlavor = _M2_FLAVOR,
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
            "schema": flavor.checkpoint_schema,
            "operation_id": run.parameters.value()["operation_id"],
            "input_identity": run.parameters.value()["input_identity"],
            "engine_input_digest": flavor.input_binding(engine),
            "implementation_sha256": engine.runtime["implementation_sha256"],
            "config": asdict(engine.config),
            "engine_schema": flavor.engine_checkpoint_schema,
            "completed_epochs": engine.completed_epochs,
            "chain_index": engine.chain_index, "window_cursor": engine.window_cursor,
            "label_count": engine.label_count,
            "optimizer_updates": engine.optimizer_updates,
            **_kind(flavor),
        }),
    )
    store.publish(item)
    event("checkpoint", checkpoint_id=item.artifact_id)
    return item.artifact_id


def _restore(
    store: ArtifactStore, checkpoint_id: str, run: Manifest, training: Manifest,
    engine: Any, *, flavor: _RunFlavor = _M2_FLAVOR,
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
            *(_kind(flavor)),
        }
        or info["schema"] != flavor.checkpoint_schema
        or info["engine_schema"] != flavor.engine_checkpoint_schema
        or any(info[key] != value for key, value in _kind(flavor).items())
        or info["operation_id"] != run.parameters.value()["operation_id"]
        or info["input_identity"] != run.parameters.value()["input_identity"]
        or info["engine_input_digest"] != flavor.input_binding(engine)
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
        or decoded[flavor.checkpoint_binding_key] != flavor.input_binding(engine)
    ):
        raise BoundaryError(_BOUNDARY, "checkpoint_progress_mismatch")
    engine.restore(raw)
    return decoded, hashlib.sha256(raw).hexdigest()


def _stage(
    store: ArtifactStore, run: Manifest, training: Manifest, engine: Any,
    checkpoint_id: str, *, flavor: _RunFlavor = _M2_FLAVOR,
) -> str:
    epoch = engine.completed_epochs
    if epoch not in {1, 3, 5} or engine.chain_index != 0 or engine.window_cursor != 0:
        raise BoundaryError(_BOUNDARY, "stage_epoch_boundary_required")
    metrics = engine.evaluate_dev()
    raw_weights = engine.export_weights()
    if len(raw_weights) > MAX_CHECKPOINT_BYTES:
        raise BoundaryError(_BOUNDARY, "weights_size_limit")
    exported = decode_checkpoint(raw_weights)
    if (exported["schema"] != flavor.engine_export_schema
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
            "schema": flavor.model_schema, "epoch": epoch, "run_complete": epoch == 5,
            "operation_id": run.parameters.value()["operation_id"],
            "config": asdict(engine.config),
            "input_identity": run.parameters.value()["input_identity"],
            "engine_input_digest": flavor.input_binding(engine),
            "implementation_sha256": engine.runtime["implementation_sha256"],
            "qualification": "engineering_only", **_kind(flavor),
        }),
    )
    store.publish(model)
    evaluation = Manifest(
        "offline_evaluation", run.producer,
        (Parent("run", run.artifact_id), Parent("training_input", training.artifact_id),
         Parent("checkpoint", checkpoint_id), Parent("model", model.artifact_id)),
        parameters=FrozenObject.of({
            "schema": flavor.evaluation_schema, "epoch": epoch, "partition": "dev",
            "input_identity": run.parameters.value()["input_identity"],
            "loss_mean": metrics.loss_mean, "top1_accuracy": metrics.top1_accuracy,
            "label_count": metrics.label_count, "correct_count": metrics.correct_count,
            "qualification": "engineering_only", **_kind(flavor),
        }),
    )
    store.publish(evaluation)
    stage = Manifest(
        "analysis", run.producer,
        (Parent("run", run.artifact_id), Parent("training_input", training.artifact_id),
         Parent("checkpoint", checkpoint_id), Parent("model", model.artifact_id),
         Parent("offline_evaluation", evaluation.artifact_id)),
        parameters=FrozenObject.of({
            "schema": flavor.stage_schema, "epoch": epoch, "run_complete": epoch == 5,
            "operation_id": run.parameters.value()["operation_id"],
            "input_identity": run.parameters.value()["input_identity"], **_kind(flavor),
        }),
    )
    store.publish(stage)
    return stage.artifact_id


def _verify_completed(
    store: ArtifactStore, result: Manifest, run: Manifest, training: Manifest,
    engine: Any, *, flavor: _RunFlavor = _M2_FLAVOR,
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
            "qualification": "engineering_only", **_kind(flavor),
        }
    ):
        raise BoundaryError(_BOUNDARY, "completed_result_mismatch")
    stage = store.get_manifest(result.parent("stage"))
    model = store.get_manifest(result.parent("model"))
    evaluation = store.get_manifest(result.parent("offline_evaluation"))
    checkpoint_id = result.parent("checkpoint")
    expected_model = {
        "schema": flavor.model_schema, "epoch": 5, "run_complete": True,
        "operation_id": run.parameters.value()["operation_id"],
        "config": asdict(engine.config),
        "input_identity": run.parameters.value()["input_identity"],
        "engine_input_digest": flavor.input_binding(engine),
        "implementation_sha256": engine.runtime["implementation_sha256"],
        "qualification": "engineering_only", **_kind(flavor),
    }
    if (
        stage.kind != "analysis" or stage.producer != run.producer
        or sorted(parent.role for parent in stage.parents) != [
            "checkpoint", "model", "offline_evaluation", "run", "training_input",
        ]
        or stage.parameters.value() != {
            "schema": flavor.stage_schema, "epoch": 5, "run_complete": True,
            "operation_id": run.parameters.value()["operation_id"],
            "input_identity": run.parameters.value()["input_identity"],
            **_kind(flavor),
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
        or evaluation.parameters.value().get("schema") != flavor.evaluation_schema
        or evaluation.parameters.value().get("epoch") != 5
        or sorted(parent.role for parent in evaluation.parents)
        != ["checkpoint", "model", "run", "training_input"]
        or evaluation.parent("model") != model.artifact_id
        or evaluation.parent("checkpoint") != checkpoint_id
        or evaluation.parent("run") != run.artifact_id
        or evaluation.parent("training_input") != training.artifact_id
    ):
        raise BoundaryError(_BOUNDARY, "completed_stage_mismatch")
    checkpoint, checkpoint_sha256 = _restore(
        store, checkpoint_id, run, training, engine, flavor=flavor,
    )
    if not engine.finished:
        raise BoundaryError(_BOUNDARY, "incomplete_terminal_checkpoint")
    metrics = evaluation.parameters.value()
    dev_labels = sum(len(chain.steps) for chain in engine.dev_chains)
    if (
        set(metrics) != {
            "schema", "epoch", "partition", "input_identity", "loss_mean",
            "top1_accuracy", "label_count", "correct_count", "qualification",
            *(_kind(flavor)),
        }
        or metrics["schema"] != flavor.evaluation_schema
        or any(metrics[key] != value for key, value in _kind(flavor).items())
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
            "schema", "config", flavor.export_binding_key, "implementation_sha256",
            "completed_epochs", "optimizer_updates", "run_complete",
            "checkpoint_digest", "weights", "weights_digest",
            *flavor.export_extra_fields,
        }
        or exported["schema"] != flavor.engine_export_schema
        or exported["config"] != asdict(engine.config)
        or exported[flavor.export_binding_key] != flavor.input_binding(engine)
        or any(exported[key] != value for key, value in
               flavor.export_extra_expected(engine).items())
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


def _execute_run(
    store: ArtifactStore, reporter: RunReporter, run_id: str, runtime: Producer,
    *, resume: str | None = None, stop_after_windows: int | None = None,
    flavor: _RunFlavor = _M2_FLAVOR,
    stage_reuse: dict[str, Any] | None = None,
) -> WorkerResult:
    """Execute one bounded attempt; explicit resume selects one durable checkpoint."""
    if (stop_after_windows is not None and
            (type(stop_after_windows) is not int or stop_after_windows < 1)):
        raise BoundaryError(_BOUNDARY, "invalid_pause_budget")
    run, training, _, _, engine = _load_run(store, run_id, runtime, flavor=flavor)
    completed = reporter.completed(run_id)
    if completed is not None:
        _verify_completed(store, completed, run, training, engine, flavor=flavor)
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
                "details": details, **_kind(flavor),
            }),
        ))

    try:
        event("loading", resume=resume)
        if resume is not None:
            _restore(store, resume, run, training, engine, flavor=flavor)
        event("resumed" if resume else "started")
        checkpoint_id: str | None = resume
        initial_updates = engine.optimizer_updates
        # A crash after an epoch checkpoint but before its readout can be
        # reconciled idempotently on explicit resume of that exact checkpoint.
        if stage_reuse is not None:
            if (resume is None or stage_reuse.get("run_id") != run_id
                    or stage_reuse.get("producer") != runtime.to_dict()
                    or stage_reuse.get("checkpoint_id") != resume
                    or stage_reuse.get("epoch") != engine.completed_epochs
                    or engine.completed_epochs not in {1, 3, 5}
                    or engine.chain_index != 0 or engine.window_cursor != 0
                    or type(stage_reuse.get("commitment_sha256")) is not str):
                raise BoundaryError(_BOUNDARY, "stage_reuse_binding_mismatch")
            stage_id = stage_reuse["stage_id"]
            event("epoch_stage_reused", epoch=engine.completed_epochs,
                  stage_id=stage_id,
                  commitment_sha256=stage_reuse["commitment_sha256"])
        elif (engine.completed_epochs in {1, 3, 5} and resume is not None
                and engine.chain_index == 0 and engine.window_cursor == 0):
            stage_id = _stage(store, run, training, engine, resume, flavor=flavor)
            event("epoch_stage", epoch=engine.completed_epochs, stage_id=stage_id)
        else:
            stage_id = None
        while not engine.finished:
            before_epoch = engine.completed_epochs
            progress = engine.advance_window()
            event("window_completed", loss_mean=progress.loss_mean,
                  labels=progress.label_count)
            if engine.completed_epochs != before_epoch:
                checkpoint_id = _checkpoint(
                    store, run, training, engine, event, flavor=flavor,
                )
                if engine.completed_epochs in {1, 3, 5}:
                    stage_id = _stage(
                        store, run, training, engine, checkpoint_id, flavor=flavor,
                    )
                    event("epoch_stage", epoch=engine.completed_epochs, stage_id=stage_id)
            if (stop_after_windows is not None
                    and engine.optimizer_updates - initial_updates >= stop_after_windows
                    and not engine.finished):
                checkpoint_id = _checkpoint(
                    store, run, training, engine, event, flavor=flavor,
                )
                event("paused", checkpoint_id=checkpoint_id)
                return WorkerResult("paused", run_id, checkpoint_id=checkpoint_id)
        if checkpoint_id is None or stage_id is None:
            raise BoundaryError(_BOUNDARY, "missing_terminal_stage")
        terminal_reuses_claim = (
            stage_reuse is not None and stage_id == stage_reuse["stage_id"]
            and engine.completed_epochs == 5
            and checkpoint_id == stage_reuse["checkpoint_id"]
        )
        if terminal_reuses_claim and stage_reuse is not None:
            model_id = stage_reuse["model_id"]
            evaluation_id = stage_reuse["evaluation_id"]
        else:
            stage = store.get_manifest(stage_id)
            model_id = stage.parent("model")
            evaluation_id = stage.parent("offline_evaluation")
        result = Manifest(
            "run_result", runtime,
            (Parent("run", run_id), Parent("training_input", training.artifact_id),
             Parent("checkpoint", checkpoint_id), Parent("stage", stage_id),
             Parent("model", model_id), Parent("offline_evaluation", evaluation_id)),
            parameters=FrozenObject.of({
                "schema": RESULT_SCHEMA, "state": "completed", "epoch": 5,
                "operation_id": run.parameters.value()["operation_id"],
                "input_identity": run.parameters.value()["input_identity"],
                "qualification": "engineering_only", **_kind(flavor),
            }),
        )
        if not terminal_reuses_claim:
            _verify_completed(store, result, run, training, engine, flavor=flavor)
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


def execute_public_m2_run(
    store: ArtifactStore, reporter: RunReporter, run_id: str, runtime: Producer,
    *, resume: str | None = None, stop_after_windows: int | None = None,
) -> WorkerResult:
    return _execute_run(
        store, reporter, run_id, runtime, resume=resume,
        stop_after_windows=stop_after_windows, flavor=_M2_FLAVOR,
    )
