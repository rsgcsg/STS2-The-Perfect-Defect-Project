"""Owner boundary for independent, fixed-stage public M2 evaluation.

The original run remains immutable. This module admits one epoch-stage model,
asks an application-owned projector to repeat the authoritative full-dev public
projection, validates the original checkpoint/export closure, then publishes a
separate evaluation-input and offline-evaluation pair. It makes no Gold,
independence, or clean-held-out claim.
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, digest, json_bytes
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from ..fullrun.public_m2_input_storage import (
    MAX_INPUT_BYTES,
    MAX_TOKENIZER_BYTES,
    public_m2_source_binding_digest,
    read_public_m2_input,
)
from ..fullrun.public_m2_sequences import PublicM2Input
from . import public_m2_run as run_owner
from .checkpoint_codec import decode_checkpoint
from .public_m2_preflight import (
    validate_public_m2_checkpoint,
    validate_public_m2_export,
)

BOUNDARY = "public_m2_evaluation_owner"
EVALUATION_INPUT_SCHEMA = "stpd/public-m2-full-dev-evaluation-input-v1"
OFFLINE_EVALUATION_SCHEMA = "stpd/public-m2-full-dev-offline-evaluation-v1"
MAX_REPORT_BYTES = 32 * 1024 * 1024


class EvaluationOwner(Protocol):
    """Narrow structural adapter for the existing curation owner."""

    def require_training_datasets(
        self, store: Any, dataset_ids: tuple[str, ...], operation_id: str,
    ) -> dict[str, Any]: ...

    def _allocation_dev_use(
        self, store: Any, *, allocation_id: str, training_operation_id: str,
        evaluation_operation_id: str, record_use: bool,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class FullDevProjection:
    """A fresh official reprojection and its exact selected membership.

    The projector must re-run source verification, public semantic projection,
    and exact allocation membership/hash checks. Passing a caller-supplied
    PublicM2Input alone is deliberately insufficient.
    """

    value: PublicM2Input
    transition_ids: tuple[str, ...]
    public_sample_sha256: tuple[str, ...]
    membership_sha256: str


class FullDevProjector(Protocol):
    def reproject_full_dev(
        self, store: ArtifactStore, *, source_view_id: str, allocation_id: str,
    ) -> FullDevProjection: ...


class EvaluationChainRef(Protocol):
    chain_id: str


class EvaluationSelection(Protocol):
    source_input_identity: str
    training_input_digest: str
    tokenizer_sha256: str
    chains: tuple[EvaluationChainRef, ...]
    identity: str
    ordered_transition_ids: tuple[str, ...]
    ordered_membership_digest: str
    row_count: int


class EvaluationSummary(Protocol):
    selection_identity: str
    source_input_identity: str
    training_input_digest: str
    weights_sha256: str
    weights_digest: str
    completed_epochs: int
    run_complete: bool
    implementation_sha256: str
    config_digest: str
    runtime: dict[str, Any]
    chain_count: int
    label_count: int
    cross_entropy_sum: float
    loss_mean: float
    correct_count: int
    top1_accuracy: float
    ordered_chain_ids: tuple[str, ...]
    ordered_transition_ids: tuple[str, ...]
    ordered_membership_digest: str
    inference_device: str


@dataclass(frozen=True)
class FullDevAdmission:
    """A typed result of fresh full-dev reprojection, never a boolean pass."""

    input_value: PublicM2Input
    transition_ids: tuple[str, ...]
    public_sample_sha256: tuple[str, ...]
    membership_sha256: str
    selection: EvaluationSelection | None


class EvaluationKernel(Protocol):
    """Adapter over the canonical stage evaluator.

    ``evaluate`` must return a summary after exact-once shard combination with
    that module's expected weight/config bindings and a strict runtime receipt.
    This owner checks the resulting complete summary against PreparedEval.
    """

    def build_selection(
        self, source: PublicM2Input, *, training_input_digest: str,
    ) -> EvaluationSelection: ...

    def evaluate(
        self, *, weights_raw: bytes, tokenizer_raw: bytes, config: Any,
        training_input_digest: str, source: PublicM2Input,
        selection: Any, completed_epochs: int,
    ) -> EvaluationSummary: ...


@dataclass(frozen=True)
class PublicM2StageEvaluationResult:
    evaluation_input: Manifest
    offline_evaluation: Manifest
    admission: FullDevAdmission


@dataclass(frozen=True)
class PreparedEval:
    """Immutable CPU-prepared evaluation; inference may run after releasing locks."""

    evaluation_input: Manifest
    stage_id: str
    run_id: str
    training_input_id: str
    checkpoint_id: str
    model_id: str
    source_view_id: str
    allocation_id: str
    run_producer: Producer
    evaluation_producer: Producer
    operation_id: str
    training_input_identity: str
    training_engine_input_digest: str
    full_dev_input: PublicM2Input
    selection: EvaluationSelection
    transition_ids: tuple[str, ...]
    public_sample_sha256: tuple[str, ...]
    membership_sha256: str
    expected_dev_decisions: int
    weights_payload: Any
    tokenizer_payload: Any
    weights_sha256: str
    weights_digest: str
    implementation_sha256: str
    config_digest: str
    config: Any
    completed_epochs: int
    owner_binding_sha256: str


def _read(store: ArtifactStore, payload: Any, maximum: int) -> bytes:
    if payload.size > maximum:
        raise BoundaryError(BOUNDARY, "payload_size_limit")
    raw = b"".join(store.read_payload(payload))
    if len(raw) != payload.size or hashlib.sha256(raw).hexdigest() != payload.sha256:
        raise BoundaryError(BOUNDARY, "payload_integrity_mismatch")
    return raw


def _ids_and_hashes(value: PublicM2Input) -> tuple[tuple[str, ...], tuple[str, ...]]:
    evidence = tuple(row for chain in value.chains if chain.split == "dev"
                     for row in chain.evidence)
    return (tuple(row.transition_id for row in evidence),
            tuple(row.public_sample_sha256 for row in evidence))


def _admit_full_dev(
    projection: FullDevProjection, expected: PublicM2Input, *,
    expected_dev_decisions: int, expected_membership_sha256: str,
) -> FullDevAdmission:
    if not isinstance(projection, FullDevProjection):
        raise BoundaryError(BOUNDARY, "typed_full_dev_projection_required")
    value = projection.value
    if not isinstance(value, PublicM2Input) or value != expected:
        raise BoundaryError(BOUNDARY, "full_dev_reprojection_mismatch")
    transitions, sample_hashes = _ids_and_hashes(value)
    membership_digest = hashlib.sha256(json_bytes({
        "transition_ids": list(transitions),
        "public_sample_sha256": list(sample_hashes),
    })).hexdigest()
    if (
        type(expected_dev_decisions) is not int or expected_dev_decisions < 1
        or len(transitions) != expected_dev_decisions
        or len(set(transitions)) != expected_dev_decisions
        or projection.membership_sha256 != expected_membership_sha256
        or projection.transition_ids != transitions
        or projection.public_sample_sha256 != sample_hashes
        or projection.membership_sha256 != membership_digest
    ):
        raise BoundaryError(BOUNDARY, "full_dev_membership_mismatch")
    return FullDevAdmission(
        value, transitions, sample_hashes, projection.membership_sha256, None,
    )


def _load_stage(
    store: ArtifactStore, stage_id: str, run_producer: Producer,
) -> tuple[Manifest, Manifest, Manifest, Manifest, Manifest, Manifest, Any, bytes, bytes, dict]:
    """Validate epoch stage, original run, checkpoint and exact exported weights."""
    stage = store.get_manifest(stage_id)
    stage_info = stage.parameters.value()
    if (stage.kind != "analysis" or stage.producer != run_producer
            or set(stage_info) != {"schema", "epoch", "run_complete", "operation_id",
                                  "input_identity"}
            or stage_info.get("schema") != run_owner.STAGE_SCHEMA
            or type(stage_info.get("epoch")) is not int
            or stage_info["epoch"] not in {1, 3, 5}
            or stage_info.get("run_complete") is not (stage_info["epoch"] == 5)
            or sorted(parent.role for parent in stage.parents) != [
                "checkpoint", "model", "offline_evaluation", "run", "training_input",
            ]):
        raise BoundaryError(BOUNDARY, "stage_identity_mismatch")
    run_id = stage.parent("run")
    run, training, source, config, preflight = run_owner._load_run(
        store, run_id, run_producer, preflight_only=True,
    )
    run_info = run.parameters.value()
    training_info = training.parameters.value()
    epoch = stage_info["epoch"]
    model = store.get_manifest(stage.parent("model"))
    checkpoint = store.get_manifest(stage.parent("checkpoint"))
    original_eval = store.get_manifest(stage.parent("offline_evaluation"))
    model_info = model.parameters.value()
    cp_info = checkpoint.parameters.value()
    if (
        stage.parent("training_input") != training.artifact_id
        or stage_info["operation_id"] != run_info["operation_id"]
        or stage_info["input_identity"] != run_info["input_identity"]
        or model.kind != "model" or model.producer != run_producer
        or model_info != {
            "schema": run_owner.MODEL_SCHEMA, "epoch": epoch,
            "run_complete": epoch == 5, "operation_id": run_info["operation_id"],
            "config": asdict(config), "input_identity": run_info["input_identity"],
            "engine_input_digest": preflight.input_digest,
            "implementation_sha256": preflight.runtime["implementation_sha256"],
            "qualification": "engineering_only",
        }
        or sorted(parent.role for parent in model.parents)
        != ["checkpoint", "run", "training_input"]
        or model.parent("run") != run.artifact_id
        or model.parent("training_input") != training.artifact_id
        or model.parent("checkpoint") != checkpoint.artifact_id
        or sorted(payload.role for payload in model.payloads)
        != ["state_tokenizer", "weights"]
        or checkpoint.kind != "checkpoint" or checkpoint.producer != run_producer
        or sorted(parent.role for parent in checkpoint.parents)
        != ["run", "training_input"]
        or checkpoint.parent("run") != run.artifact_id
        or checkpoint.parent("training_input") != training.artifact_id
        or [payload.role for payload in checkpoint.payloads] != ["checkpoint"]
        or cp_info.get("schema") != run_owner.CHECKPOINT_SCHEMA
        or cp_info.get("operation_id") != run_info["operation_id"]
        or cp_info.get("input_identity") != run_info["input_identity"]
        or cp_info.get("engine_input_digest") != preflight.input_digest
        or cp_info.get("implementation_sha256")
        != preflight.runtime["implementation_sha256"]
        or cp_info.get("config") != asdict(config)
        or cp_info.get("completed_epochs") != epoch
        or cp_info.get("chain_index") != 0 or cp_info.get("window_cursor") != 0
        or cp_info.get("engine_schema") != run_owner.PUBLIC_M2_CHECKPOINT_SCHEMA
        or original_eval.kind != "offline_evaluation"
        or original_eval.producer != run_producer
        or original_eval.parameters.value() != {
            "schema": run_owner.EVALUATION_SCHEMA, "epoch": epoch,
            "partition": "dev", "input_identity": run_info["input_identity"],
            **{key: value for key, value in original_eval.parameters.value().items()
               if key in {"loss_mean", "top1_accuracy", "label_count", "correct_count"}},
            "qualification": "engineering_only",
        }
        or sorted(parent.role for parent in original_eval.parents)
        != ["checkpoint", "model", "run", "training_input"]
        or original_eval.parent("checkpoint") != checkpoint.artifact_id
        or original_eval.parent("model") != model.artifact_id
        or original_eval.parent("run") != run.artifact_id
        or original_eval.parent("training_input") != training.artifact_id
    ):
        raise BoundaryError(BOUNDARY, "stage_parent_closure_mismatch")
    checkpoint_raw = _read(store, checkpoint.payload("checkpoint"),
                           run_owner.MAX_CHECKPOINT_BYTES)
    weights_raw = _read(store, model.payload("weights"),
                        run_owner.MAX_CHECKPOINT_BYTES)
    checkpoint_header = decode_checkpoint(checkpoint_raw)
    runtime = checkpoint_header.get("runtime")
    if not isinstance(runtime, dict):
        raise BoundaryError(BOUNDARY, "checkpoint_runtime_required")
    checked_checkpoint = validate_public_m2_checkpoint(
        checkpoint_raw, preflight, expected_runtime=runtime,
    )
    exported = validate_public_m2_export(
        weights_raw, preflight, completed_epochs=epoch,
        checkpoint_raw=checkpoint_raw, expected_runtime=runtime,
    )
    source_tokenizer = training.payload("state_tokenizer")
    if (
        exported.get("run_complete") is not (epoch == 5)
        or exported.get("checkpoint_digest") != hashlib.sha256(checkpoint_raw).hexdigest()
        or model.payload("state_tokenizer") != source_tokenizer
        or checked_checkpoint.get("input_digest") != preflight.input_digest
    ):
        raise BoundaryError(BOUNDARY, "stage_export_binding_mismatch")
    source_view_id = training.parent("source_view")
    allocation_id = training.parent("allocation")
    if (
        run_info["source_view_id"] != source_view_id
        or run_info["allocation_id"] != allocation_id
        or run_info["source_binding_digest"]
        != public_m2_source_binding_digest(source_view_id, allocation_id)
        or training_info["source_binding_digest"] != run_info["source_binding_digest"]
    ):
        raise BoundaryError(BOUNDARY, "stage_source_lineage_mismatch")
    source_view = store.get_manifest(source_view_id)
    allocation = store.get_manifest(allocation_id)
    return (run, training, source_view, allocation, checkpoint, model, config,
            weights_raw, _read(store, training.payload("state_tokenizer"),
                               run_owner.MAX_CHECKPOINT_BYTES), {
                "stage": stage, "original_eval": original_eval,
                "run_producer": run_producer, "preflight": preflight,
                "checkpoint_sha256": hashlib.sha256(checkpoint_raw).hexdigest(),
                "epoch": epoch, "input_identity": run_info["input_identity"],
                "training_operation_id": run_info["operation_id"],
            })


def _stage_owner_preflight(
    store: ArtifactStore, *, stage_id: str, run_producer: Producer,
    evaluation_operation_id: str, owner: EvaluationOwner,
) -> tuple[Manifest, Manifest, Manifest, Manifest, Manifest, str, str, dict, dict]:
    """Check lightweight lineage and dynamic curation access before payload reads."""
    stage = store.get_manifest(stage_id)
    stage_info = stage.parameters.value()
    if (stage.kind != "analysis" or stage.producer != run_producer
            or stage_info.get("schema") != run_owner.STAGE_SCHEMA
            or type(stage_info.get("epoch")) is not int
            or stage_info.get("epoch") not in {1, 3, 5}
            or stage_info.get("run_complete") is not (stage_info.get("epoch") == 5)
            or sorted(parent.role for parent in stage.parents) != [
                "checkpoint", "model", "offline_evaluation", "run", "training_input",
            ]):
        raise BoundaryError(BOUNDARY, "stage_identity_mismatch")
    run = store.get_manifest(stage.parent("run"))
    training = store.get_manifest(stage.parent("training_input"))
    run_info = run.parameters.value()
    training_info = training.parameters.value()
    if (run.kind != "run" or run.producer != run_producer
            or run_info.get("schema") != run_owner.RUN_SCHEMA
            or training.kind != "training_input" or training.producer != run_producer
            or training_info.get("schema") != run_owner.INPUT_SCHEMA
            or sorted(parent.role for parent in run.parents)
            != ["experiment", "training_input"]
            or run.parent("training_input") != training.artifact_id
            or sorted(parent.role for parent in training.parents)
            != ["allocation", "source_view"]):
        raise BoundaryError(BOUNDARY, "stage_source_lineage_mismatch")
    training_operation_id = digest(
        run_info.get("operation_id"), BOUNDARY + ".training_operation", length=32,
    )
    digest(evaluation_operation_id, BOUNDARY + ".evaluation_operation", length=32)
    source_view_id = training.parent("source_view")
    allocation_id = training.parent("allocation")
    if (run_info.get("source_view_id") != source_view_id
            or run_info.get("allocation_id") != allocation_id):
        raise BoundaryError(BOUNDARY, "stage_source_lineage_mismatch")
    source_view = store.get_manifest(source_view_id)
    allocation = store.get_manifest(allocation_id)
    if (source_view.kind != "model_view"
            or source_view.parameters.value().get("schema")
            != run_owner.PUBLIC_COMPACT_VIEW_SCHEMA
            or source_view.parameters.value().get("serializer")
            != run_owner.COMPACT_IDENTITY
            or sorted(parent.role for parent in source_view.parents)
            != ["allocation", "dataset"]
            or source_view.parent("allocation") != allocation_id
            or allocation.kind != "protocol"
            or allocation.parameters.value().get("schema")
            != run_owner.ALLOCATION_SCHEMA
            or [parent.role for parent in allocation.parents] != ["dataset"]
            or allocation.parent("dataset") != source_view.parent("dataset")
            or store.get_manifest(source_view.parent("dataset")).kind != "dataset"):
        raise BoundaryError(BOUNDARY, "stage_source_lineage_mismatch")
    # These owner calls only inspect current curation/allocation metadata. In
    # particular, neither the full training input nor model/checkpoint payloads
    # have been read when they reject this request.
    training_admission = owner.require_training_datasets(
        store, (source_view.parent("dataset"),), training_operation_id,
    )
    allocation_preflight = owner._allocation_dev_use(
        store, allocation_id=allocation_id,
        training_operation_id=training_operation_id,
        evaluation_operation_id=evaluation_operation_id, record_use=False,
    )
    return (stage, run, training, source_view, allocation,
            training_operation_id, source_view.parent("dataset"),
            training_admission, allocation_preflight)


def _result_payload(result: Any) -> tuple[dict[str, Any], bytes]:
    if hasattr(result, "to_dict") and callable(result.to_dict):
        value = result.to_dict()
    elif hasattr(result, "__dataclass_fields__"):
        value = asdict(result)
    else:
        raise BoundaryError(BOUNDARY, "typed_evaluation_result_required")
    if not isinstance(value, dict):
        raise BoundaryError(BOUNDARY, "typed_evaluation_result_required")
    raw = json_bytes(value)
    if len(raw) > MAX_REPORT_BYTES:
        raise BoundaryError(BOUNDARY, "evaluation_report_size_limit")
    return value, raw


def _evaluation_input_manifest(
    store: ArtifactStore, *, producer: Producer, parents: tuple[Parent, ...],
    info: dict[str, Any], source: PublicM2Input, tokenizer_raw: bytes,
) -> Manifest:
    return Manifest(
        "analysis", producer, parents,
        (store.put_payload("full_dev_input", io.BytesIO(source.payload_bytes()),
                           "application/json"),
         store.put_payload("state_tokenizer", io.BytesIO(tokenizer_raw),
                           "application/json")),
        FrozenObject.of(info),
    )


def _summary_payload(summary: EvaluationSummary, prepared: PreparedEval) -> bytes:
    selection = prepared.selection
    if (
        getattr(summary, "selection_identity", None) != selection.identity
        or getattr(summary, "source_input_identity", None)
        != prepared.full_dev_input.identity
        or getattr(summary, "training_input_digest", None)
        != prepared.training_engine_input_digest
        or getattr(summary, "weights_sha256", None) != prepared.weights_sha256
        or getattr(summary, "weights_digest", None) != prepared.weights_digest
        or getattr(summary, "implementation_sha256", None)
        != prepared.implementation_sha256
        or getattr(summary, "config_digest", None) != prepared.config_digest
        or getattr(summary, "completed_epochs", None) != prepared.completed_epochs
        or getattr(summary, "run_complete", None)
        is not (prepared.completed_epochs == prepared.config.epochs)
        or type(getattr(summary, "label_count", None)) is not int
        or summary.label_count != prepared.expected_dev_decisions
        or tuple(getattr(summary, "ordered_transition_ids", ()))
        != prepared.transition_ids
        or getattr(summary, "ordered_membership_digest", None)
        != selection.ordered_membership_digest
        or tuple(getattr(summary, "ordered_chain_ids", ()))
        != tuple(chain.chain_id for chain in selection.chains)
        or type(getattr(summary, "correct_count", None)) is not int
        or not 0 <= summary.correct_count <= prepared.expected_dev_decisions
        or type(getattr(summary, "cross_entropy_sum", None)) not in {int, float}
        or not math.isfinite(summary.cross_entropy_sum)
        or summary.cross_entropy_sum < 0
        or type(getattr(summary, "loss_mean", None)) not in {int, float}
        or not math.isfinite(summary.loss_mean)
        or summary.loss_mean != summary.cross_entropy_sum / prepared.expected_dev_decisions
        or type(getattr(summary, "top1_accuracy", None)) not in {int, float}
        or not math.isfinite(summary.top1_accuracy)
        or summary.top1_accuracy != summary.correct_count / prepared.expected_dev_decisions
        or type(getattr(summary, "chain_count", None)) is not int
        or summary.chain_count != len(selection.chains)
        or not isinstance(getattr(summary, "runtime", None), dict)
        or not isinstance(getattr(summary, "inference_device", None), str)
        or not summary.inference_device
    ):
        raise BoundaryError(BOUNDARY, "full_dev_result_coverage_mismatch")
    return _result_payload(summary)[1]


def prepare_stage_evaluation(
    store: ArtifactStore, *, stage_id: str, run_producer: Producer,
    evaluation_producer: Producer, operation_id: str,
    full_dev_input: PublicM2Input, expected_dev_decisions: int,
    expected_membership_sha256: str, projector: FullDevProjector,
    evaluator: EvaluationKernel, owner: EvaluationOwner,
) -> PreparedEval:
    """Prepare CPU inputs under the caller's global lock; never runs inference.

    A caller may release its single-CPU lock as soon as this returns and run
    bounded GPU inference using the immutable payload references in PreparedEval.
    """
    digest(operation_id, BOUNDARY + ".operation_id", length=32)
    digest(expected_membership_sha256, BOUNDARY + ".membership", length=64)
    if (type(expected_dev_decisions) is not int or expected_dev_decisions < 1
            or not isinstance(run_producer, Producer)
            or not isinstance(evaluation_producer, Producer)
            or evaluation_producer == run_producer
            or not isinstance(full_dev_input, PublicM2Input)):
        raise BoundaryError(BOUNDARY, "typed_evaluation_request_required")
    (_stage_metadata, _run_metadata, _training_metadata, _view_metadata,
     _allocation_metadata, _training_op_metadata, _dataset_id,
     training_admission, allocation_preflight) = _stage_owner_preflight(
        store, stage_id=stage_id, run_producer=run_producer,
        evaluation_operation_id=operation_id, owner=owner,
    )
    (run, training, source_view, allocation, checkpoint, model, config, weights_raw,
     tokenizer_raw, closure) = _load_stage(store, stage_id, run_producer)
    if closure["training_operation_id"] != _training_op_metadata:
        raise BoundaryError(BOUNDARY, "stage_source_lineage_mismatch")
    projection = projector.reproject_full_dev(
        store, source_view_id=source_view.artifact_id,
        allocation_id=allocation.artifact_id,
    )
    admission = _admit_full_dev(
        projection, full_dev_input, expected_dev_decisions=expected_dev_decisions,
        expected_membership_sha256=expected_membership_sha256,
    )
    if (admission.input_value.source_binding_digest
            != public_m2_source_binding_digest(
                source_view.artifact_id, allocation.artifact_id)):
        raise BoundaryError(BOUNDARY, "full_dev_only_input_required")
    if admission.input_value.state_tokenizer != tokenizer_raw:
        raise BoundaryError(BOUNDARY, "full_dev_tokenizer_mismatch")
    full_dev_raw = admission.input_value.payload_bytes()
    if (len(full_dev_raw) > MAX_INPUT_BYTES
            or len(admission.input_value.state_tokenizer) > MAX_TOKENIZER_BYTES):
        raise BoundaryError(BOUNDARY, "full_dev_payload_size_limit")
    if read_public_m2_input(full_dev_raw, tokenizer_raw) != admission.input_value:
        raise BoundaryError(BOUNDARY, "full_dev_input_roundtrip_mismatch")
    if len(admission.transition_ids) != expected_dev_decisions:
        raise BoundaryError(BOUNDARY, "full_dev_membership_mismatch")
    selection = evaluator.build_selection(
        admission.input_value,
        training_input_digest=closure["preflight"].input_digest,
    )
    if (
        getattr(selection, "source_input_identity", None) != admission.input_value.identity
        or getattr(selection, "training_input_digest", None)
        != closure["preflight"].input_digest
        or getattr(selection, "tokenizer_sha256", None)
        != hashlib.sha256(tokenizer_raw).hexdigest()
        or tuple(getattr(selection, "ordered_transition_ids", ()))
        != admission.transition_ids
        or getattr(selection, "row_count", None) != expected_dev_decisions
        or not getattr(selection, "identity", None)
        or not getattr(selection, "ordered_membership_digest", None)
    ):
        raise BoundaryError(BOUNDARY, "evaluation_selection_binding_mismatch")
    input_info = {
        "schema": EVALUATION_INPUT_SCHEMA,
        "operation_id": operation_id,
        "stage_epoch": closure["epoch"],
        "training_input_identity": closure["input_identity"],
        "training_engine_input_digest": closure["preflight"].input_digest,
        "checkpoint_payload_sha256": closure["checkpoint_sha256"],
        "weights_payload_sha256": hashlib.sha256(weights_raw).hexdigest(),
        "weights_tensor_digest": str(decode_checkpoint(weights_raw)["weights_digest"]),
        "implementation_sha256": model.parameters.value()["implementation_sha256"],
        "config_digest": str(semantic_hash(asdict(config))),
        "full_dev_input_identity": admission.input_value.identity,
        "full_dev_selection_identity": selection.identity,
        "selection_membership_sha256": selection.ordered_membership_digest,
        "membership_sha256": admission.membership_sha256,
        "membership_count": expected_dev_decisions,
        "source_view_id": source_view.artifact_id,
        "allocation_id": allocation.artifact_id,
        "qualification": "engineering_only",
        "scientific_verdict": "not_claimed",
        "gold_claim": False,
        "clean_held_out_claim": False,
    }
    parents = (
        Parent("run", run.artifact_id), Parent("stage", closure["stage"].artifact_id),
        Parent("checkpoint", checkpoint.artifact_id), Parent("model", model.artifact_id),
        Parent("source_view", source_view.artifact_id),
        Parent("allocation", allocation.artifact_id),
    )
    existing = None
    for identity in store.manifest_ids():
        candidate = store.get_manifest(identity)
        if (candidate.kind == "analysis"
                and candidate.parameters.value().get("schema") == EVALUATION_INPUT_SCHEMA
                and candidate.parameters.value().get("operation_id") == operation_id):
            if (candidate.producer != evaluation_producer
                    or candidate.parameters.value() != input_info
                    or tuple(sorted(candidate.parents)) != tuple(sorted(parents))):
                raise BoundaryError(BOUNDARY, "operation_id_collision")
            existing = candidate
            break
    if existing is not None:
        return PreparedEval(
            existing, closure["stage"].artifact_id, run.artifact_id,
            training.artifact_id, checkpoint.artifact_id, model.artifact_id,
            source_view.artifact_id, allocation.artifact_id, run_producer,
            evaluation_producer, operation_id, closure["input_identity"],
            closure["preflight"].input_digest, admission.input_value, selection,
            admission.transition_ids, admission.public_sample_sha256,
            admission.membership_sha256, expected_dev_decisions,
            model.payload("weights"), training.payload("state_tokenizer"),
            hashlib.sha256(weights_raw).hexdigest(),
            str(decode_checkpoint(weights_raw)["weights_digest"]),
            model.parameters.value()["implementation_sha256"],
            str(semantic_hash(asdict(config))),
            config, closure["epoch"], hashlib.sha256(json_bytes({
                "training": training_admission,
                "allocation_preflight": allocation_preflight,
                "source_view_id": source_view.artifact_id,
                "allocation_id": allocation.artifact_id,
            })).hexdigest(),
        )

    owner._allocation_dev_use(
        store, allocation_id=allocation.artifact_id,
        training_operation_id=closure["training_operation_id"],
        evaluation_operation_id=operation_id, record_use=True,
    )
    evaluation_input = _evaluation_input_manifest(
        store, producer=evaluation_producer, parents=parents, info=input_info,
        source=admission.input_value, tokenizer_raw=tokenizer_raw,
    )
    store.publish(evaluation_input)
    owner_binding_sha256 = hashlib.sha256(json_bytes({
        "training": training_admission,
        "allocation_preflight": allocation_preflight,
        "source_view_id": source_view.artifact_id,
        "allocation_id": allocation.artifact_id,
    })).hexdigest()
    return PreparedEval(
        evaluation_input, closure["stage"].artifact_id, run.artifact_id,
        training.artifact_id, checkpoint.artifact_id, model.artifact_id,
        source_view.artifact_id, allocation.artifact_id, run_producer,
        evaluation_producer, operation_id, closure["input_identity"],
        closure["preflight"].input_digest, admission.input_value, selection,
        admission.transition_ids, admission.public_sample_sha256,
        admission.membership_sha256, expected_dev_decisions,
        model.payload("weights"), training.payload("state_tokenizer"),
        hashlib.sha256(weights_raw).hexdigest(),
        str(decode_checkpoint(weights_raw)["weights_digest"]),
        model.parameters.value()["implementation_sha256"],
        str(semantic_hash(asdict(config))),
        config, closure["epoch"], owner_binding_sha256,
    )


def accept_stage_evaluation(
    store: ArtifactStore, prepared: PreparedEval, summary: EvaluationSummary, *,
    projector: FullDevProjector, evaluator: EvaluationKernel,
    owner: EvaluationOwner,
) -> PublicM2StageEvaluationResult:
    """Revalidate prepared identities and publish the independent evaluation."""
    if not isinstance(prepared, PreparedEval):
        raise BoundaryError(BOUNDARY, "typed_prepared_evaluation_required")
    (_stage_metadata, _run_metadata, _training_metadata, _view_metadata,
     _allocation_metadata, _training_op_metadata, _dataset_id,
     training_admission, allocation_preflight) = _stage_owner_preflight(
        store, stage_id=prepared.stage_id, run_producer=prepared.run_producer,
        evaluation_operation_id=prepared.operation_id, owner=owner,
    )
    (run, training, source_view, allocation, checkpoint, model, config, weights_raw,
     tokenizer_raw, closure) = _load_stage(
        store, prepared.stage_id, prepared.run_producer,
    )
    if (
        run.artifact_id != prepared.run_id
        or training.artifact_id != prepared.training_input_id
        or checkpoint.artifact_id != prepared.checkpoint_id
        or model.artifact_id != prepared.model_id
        or source_view.artifact_id != prepared.source_view_id
        or allocation.artifact_id != prepared.allocation_id
        or config != prepared.config
        or closure["epoch"] != prepared.completed_epochs
        or closure["input_identity"] != prepared.training_input_identity
        or closure["preflight"].input_digest != prepared.training_engine_input_digest
        or closure["training_operation_id"] != _training_op_metadata
        or model.payload("weights") != prepared.weights_payload
        or training.payload("state_tokenizer") != prepared.tokenizer_payload
        or prepared.weights_sha256 != hashlib.sha256(weights_raw).hexdigest()
        or prepared.weights_digest != decode_checkpoint(weights_raw).get("weights_digest")
        or prepared.implementation_sha256
        != model.parameters.value().get("implementation_sha256")
        or prepared.config_digest != semantic_hash(asdict(config))
    ):
        raise BoundaryError(BOUNDARY, "prepared_stage_changed")
    current_owner_binding = hashlib.sha256(json_bytes({
        "training": training_admission,
        "allocation_preflight": allocation_preflight,
        "source_view_id": source_view.artifact_id,
        "allocation_id": allocation.artifact_id,
    })).hexdigest()
    if current_owner_binding != prepared.owner_binding_sha256:
        raise BoundaryError(BOUNDARY, "prepared_owner_admission_changed")
    projection = projector.reproject_full_dev(
        store, source_view_id=source_view.artifact_id,
        allocation_id=allocation.artifact_id,
    )
    admission = _admit_full_dev(
        projection, prepared.full_dev_input,
        expected_dev_decisions=prepared.expected_dev_decisions,
        expected_membership_sha256=prepared.membership_sha256,
    )
    current_selection = evaluator.build_selection(
        admission.input_value,
        training_input_digest=closure["preflight"].input_digest,
    )
    if (current_selection.identity != prepared.selection.identity
            or current_selection.ordered_membership_digest
            != prepared.selection.ordered_membership_digest):
        raise BoundaryError(BOUNDARY, "prepared_selection_changed")
    try:
        evaluation_input = store.get_manifest(prepared.evaluation_input.artifact_id)
    except Exception as error:
        raise BoundaryError(BOUNDARY, "prepared_evaluation_input_missing") from error
    expected_input_info = {
        "schema": EVALUATION_INPUT_SCHEMA,
        "operation_id": prepared.operation_id,
        "stage_epoch": closure["epoch"],
        "training_input_identity": closure["input_identity"],
        "training_engine_input_digest": closure["preflight"].input_digest,
        "checkpoint_payload_sha256": closure["checkpoint_sha256"],
        "weights_payload_sha256": hashlib.sha256(weights_raw).hexdigest(),
        "weights_tensor_digest": decode_checkpoint(weights_raw)["weights_digest"],
        "implementation_sha256": model.parameters.value()["implementation_sha256"],
        "config_digest": str(semantic_hash(asdict(config))),
        "full_dev_input_identity": admission.input_value.identity,
        "full_dev_selection_identity": current_selection.identity,
        "selection_membership_sha256": current_selection.ordered_membership_digest,
        "membership_sha256": admission.membership_sha256,
        "membership_count": prepared.expected_dev_decisions,
        "source_view_id": source_view.artifact_id,
        "allocation_id": allocation.artifact_id,
        "qualification": "engineering_only",
        "scientific_verdict": "not_claimed",
        "gold_claim": False,
        "clean_held_out_claim": False,
    }
    expected_input_parents = (
        Parent("run", run.artifact_id), Parent("stage", closure["stage"].artifact_id),
        Parent("checkpoint", checkpoint.artifact_id), Parent("model", model.artifact_id),
        Parent("source_view", source_view.artifact_id),
        Parent("allocation", allocation.artifact_id),
    )
    if evaluation_input.artifact_id != prepared.evaluation_input.artifact_id:
        raise BoundaryError(BOUNDARY, "prepared_evaluation_input_object_mismatch")
    if (evaluation_input.kind != "analysis"
            or evaluation_input.producer != prepared.evaluation_producer):
        raise BoundaryError(BOUNDARY, "prepared_evaluation_input_identity_mismatch")
    if evaluation_input.parameters.value() != expected_input_info:
        raise BoundaryError(BOUNDARY, "prepared_evaluation_input_parameters_mismatch")
    if tuple(sorted(evaluation_input.parents)) != tuple(sorted(expected_input_parents)):
        raise BoundaryError(BOUNDARY, "prepared_evaluation_input_parents_mismatch")
    if sorted(payload.role for payload in evaluation_input.payloads) \
            != ["full_dev_input", "state_tokenizer"]:
        raise BoundaryError(BOUNDARY, "prepared_evaluation_input_changed")
    input_payload = evaluation_input.payload("full_dev_input")
    tokenizer_payload = evaluation_input.payload("state_tokenizer")
    if (
        _read(store, input_payload, MAX_INPUT_BYTES)
        != admission.input_value.payload_bytes()
        or _read(store, tokenizer_payload, MAX_TOKENIZER_BYTES) != tokenizer_raw
    ):
        raise BoundaryError(BOUNDARY, "prepared_evaluation_input_changed")
    metrics_raw = _summary_payload(summary, prepared)
    evaluation_info = {
        "schema": OFFLINE_EVALUATION_SCHEMA,
        "operation_id": prepared.operation_id,
        "partition": "dev", "epoch": prepared.completed_epochs,
        "training_input_identity": prepared.training_input_identity,
        "training_engine_input_digest": prepared.training_engine_input_digest,
        "checkpoint_payload_sha256": closure["checkpoint_sha256"],
        "weights_payload_sha256": prepared.weights_sha256,
        "weights_tensor_digest": prepared.weights_digest,
        "implementation_sha256": prepared.implementation_sha256,
        "config_digest": prepared.config_digest,
        "full_dev_input_identity": prepared.full_dev_input.identity,
        "full_dev_selection_identity": prepared.selection.identity,
        "selection_membership_sha256": prepared.selection.ordered_membership_digest,
        "membership_sha256": prepared.membership_sha256,
        "membership_count": prepared.expected_dev_decisions,
        "qualification": "engineering_only", "scientific_verdict": "not_claimed",
        "gold_claim": False, "clean_held_out_claim": False,
    }
    evaluation_parents = (
        Parent("evaluation_input", prepared.evaluation_input.artifact_id),
        Parent("run", run.artifact_id), Parent("stage", closure["stage"].artifact_id),
        Parent("checkpoint", checkpoint.artifact_id), Parent("model", model.artifact_id),
    )
    for identity in store.manifest_ids():
        candidate = store.get_manifest(identity)
        if (candidate.kind == "offline_evaluation"
                and candidate.parameters.value().get("schema") == OFFLINE_EVALUATION_SCHEMA
                and candidate.parameters.value().get("operation_id") == prepared.operation_id):
            if (candidate.producer != prepared.evaluation_producer
                    or candidate.parameters.value() != evaluation_info
                    or tuple(sorted(candidate.parents)) != tuple(sorted(evaluation_parents))
                    or [payload.role for payload in candidate.payloads] != ["metrics"]
                    or _read(store, candidate.payload("metrics"), MAX_REPORT_BYTES)
                    != metrics_raw):
                raise BoundaryError(BOUNDARY, "operation_id_collision")
            return PublicM2StageEvaluationResult(
                prepared.evaluation_input, candidate,
                FullDevAdmission(admission.input_value, admission.transition_ids,
                                 admission.public_sample_sha256,
                                 admission.membership_sha256, current_selection),
            )
    metrics_payload = store.put_payload("metrics", io.BytesIO(metrics_raw), "application/json")
    offline_evaluation = Manifest(
        "offline_evaluation", prepared.evaluation_producer, evaluation_parents,
        (metrics_payload,), FrozenObject.of(evaluation_info),
    )
    store.publish(offline_evaluation)
    return PublicM2StageEvaluationResult(
        prepared.evaluation_input, offline_evaluation,
        FullDevAdmission(admission.input_value, admission.transition_ids,
                         admission.public_sample_sha256, admission.membership_sha256,
                         current_selection),
    )


def evaluate_public_m2_stage(
    store: ArtifactStore, *, stage_id: str, run_producer: Producer,
    evaluation_producer: Producer, operation_id: str,
    full_dev_input: PublicM2Input, expected_dev_decisions: int,
    expected_membership_sha256: str, projector: FullDevProjector,
    evaluator: EvaluationKernel, owner: EvaluationOwner,
) -> PublicM2StageEvaluationResult:
    """Convenience wrapper; production orchestration should split prepare/accept."""
    prepared = prepare_stage_evaluation(
        store, stage_id=stage_id, run_producer=run_producer,
        evaluation_producer=evaluation_producer, operation_id=operation_id,
        full_dev_input=full_dev_input, expected_dev_decisions=expected_dev_decisions,
        expected_membership_sha256=expected_membership_sha256,
        projector=projector, evaluator=evaluator, owner=owner,
    )
    weights_raw = _read(store, prepared.weights_payload, run_owner.MAX_CHECKPOINT_BYTES)
    tokenizer_bytes = _read(store, prepared.tokenizer_payload, MAX_TOKENIZER_BYTES)
    summary = evaluator.evaluate(
        weights_raw=weights_raw, tokenizer_raw=tokenizer_bytes,
        config=prepared.config,
        training_input_digest=prepared.training_engine_input_digest,
        source=prepared.full_dev_input, selection=prepared.selection,
        completed_epochs=prepared.completed_epochs,
    )
    return accept_stage_evaluation(
        store, prepared, summary, projector=projector, evaluator=evaluator, owner=owner,
    )
