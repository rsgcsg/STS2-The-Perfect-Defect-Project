"""Frozen-model dev/test evaluation over independently verified single partitions.

Applications own permissions, use reservations, attempts and selection. Replaying
from the partition start is explicit; no optimizer or training ancestry is created.
"""

from __future__ import annotations

import hashlib
import io
import math
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import torch
from torch.nn import functional as F

from spireagent.artifact_contracts import Manifest, Parent, Payload, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, digest, json_bytes, object_fields
from spireagent.storage.store import ArtifactStore

from ..fullrun.structured_sequences import StructuredDataset
from ..models.structured_m2 import StructuredM2
from ..models.structured_training import evaluate_runs
from ..ordered_source_spec import (
    EVALUATION_INPUT_SCHEMA as ORDERED_INPUT_SCHEMA,
)
from ..ordered_source_spec import (
    EVALUATION_REPORT_SCHEMA as ORDERED_REPORT_SCHEMA,
)
from ..ordered_source_spec import (
    MODEL_SCHEMA as ORDERED_MODEL_SCHEMA,
)
from ..ordered_source_spec import (
    PARTITION_SCHEMA as ORDERED_PARTITION_SCHEMA,
)
from ..policy.structured_export import (
    MANIFEST_NAME,
    MAX_MANIFEST_BYTES,
    MAX_WEIGHTS_BYTES,
    PROJECTION,
    WEIGHTS_NAME,
    load_structured_package,
)
from ..structured_code_scope import is_structured_model_schema, require_structured_model_package

INPUT_SCHEMA = "stpd/structured-fixed-model-evaluation-input-v1"
REPORT_SCHEMA = "stpd/structured-fixed-model-evaluation-report-v1"
MAX_REPORT_BYTES = 128 * 1024 * 1024

if TYPE_CHECKING:
    from ..fullrun.ordered_source import VerifiedOrderedSource
    from ..fullrun.protocol_source import VerifiedProtocolSource


class EvaluationAuthority(Protocol):
    """An application-owned attempt fence, never a domain operation journal."""

    def assert_current(self) -> None: ...


@dataclass(frozen=True)
class StructuredEvaluationRequest:
    source_id: str
    model_id: str
    operation_id: str
    partition: str
    intent: str = "fixed_model_descriptive"

    def __post_init__(self) -> None:
        digest(self.source_id, "structured_evaluation.source_id")
        digest(self.model_id, "structured_evaluation.model_id")
        digest(self.operation_id, "structured_evaluation.operation_id", length=32)
        if (not isinstance(self.partition, str) or self.partition not in {"dev", "test"}
                or self.intent != "fixed_model_descriptive"):
            raise BoundaryError("structured_evaluation", "unsupported_intent_or_partition")


def _current(authority: EvaluationAuthority | None) -> None:
    if authority is not None:
        authority.assert_current()


def _bytes(store: ArtifactStore, payload: Payload, limit: int) -> bytes:
    if payload.size > limit:
        raise BoundaryError("structured_evaluation", "payload_size_limit")
    raw = b"".join(store.read_payload(payload))
    if len(raw) != payload.size or hashlib.sha256(raw).hexdigest() != payload.sha256:
        raise BoundaryError("structured_evaluation", "payload_integrity")
    return raw


def _model(store: ArtifactStore, model_id: str) -> tuple[Manifest, dict[str, Any], StructuredM2]:
    manifest = store.get_manifest(model_id)
    parameters = manifest.parameters.value()
    ordered = parameters.get("schema") == ORDERED_MODEL_SCHEMA
    if (manifest.kind != "model"
            or not (is_structured_model_schema(parameters.get("schema")) or ordered)
            or {payload.role for payload in manifest.payloads} != {"package_manifest", "weights"}):
        raise BoundaryError("structured_evaluation", "structured_model_required")
    # Downloaded models need their own closed bytes, never private training payloads.
    metadata_bytes = _bytes(store, manifest.payload("package_manifest"), MAX_MANIFEST_BYTES)
    weights_bytes = _bytes(store, manifest.payload("weights"), MAX_WEIGHTS_BYTES)
    with tempfile.TemporaryDirectory(prefix="stpd-fixed-evaluation-") as temporary:
        directory = Path(temporary)
        (directory / MANIFEST_NAME).write_bytes(metadata_bytes)
        (directory / WEIGHTS_NAME).write_bytes(weights_bytes)
        if ordered:
            from ..policy.native_structured_export import load_native_package

            metadata, model = load_native_package(
                directory, expected_manifest_sha256=hashlib.sha256(metadata_bytes).hexdigest())
        else:
            metadata, model = load_structured_package(
            directory, expected_manifest_sha256=hashlib.sha256(metadata_bytes).hexdigest())
    if ordered:
        from ..policy.native_structured_export import require_native_model_package

        require_native_model_package(manifest, metadata)
    else:
        require_structured_model_package(manifest, metadata)
    return manifest, metadata, model


def _source(store: ArtifactStore,
            request: StructuredEvaluationRequest) -> VerifiedProtocolSource | VerifiedOrderedSource:
    from ..fullrun.protocol_source import verify_protocol_source_partition

    verified: VerifiedProtocolSource | VerifiedOrderedSource
    if store.get_manifest(request.source_id).parameters.value().get(
        "partition_schema"
    ) == ORDERED_PARTITION_SCHEMA:
        from ..fullrun.ordered_source import verify_ordered_source_partition

        verified = verify_ordered_source_partition(store, request.source_id)
    else:
        verified = verify_protocol_source_partition(store, request.source_id)
    if (verified.split != request.partition
            or any(run.split != request.partition for run in verified.dataset.runs)):
        raise BoundaryError("structured_evaluation", "single_partition_required")
    # Verify all available source bytes, including any qualified auxiliary payloads.
    for payload in verified.manifest.payloads:
        for _ in store.read_payload(payload):
            pass
    return verified


def _input_parameters(request: StructuredEvaluationRequest,
                      source: VerifiedProtocolSource | VerifiedOrderedSource,
                      metadata: dict[str, Any], model: Manifest) -> dict[str, Any]:
    info = source.manifest.parameters.value()
    ordered = info["partition_schema"] == ORDERED_PARTITION_SCHEMA
    if ordered != (model.parameters.value()["schema"] == ORDERED_MODEL_SCHEMA):
        raise BoundaryError("structured_evaluation", "model_source_family_mismatch")
    if ordered:
        identity = metadata["source"]["verification_identity"]
        if any(identity[key] != info[key] for key in ("projection_spec", "target_spec")):
            raise BoundaryError("structured_evaluation", "projection_target_view_mismatch")
        return {"schema": ORDERED_INPUT_SCHEMA, "request": asdict(request),
                "projection": metadata["projection"], "input_spec": info["input_spec"],
                "projection_spec": info["projection_spec"], "target_spec": info["target_spec"],
                "source_sha256": source.dataset.source_sha256,
                "source_kind": source.dataset.source_kind, "source_parameters": info,
                "package_model_id": metadata["model_id"],
                "package_manifest_sha256": model.payload("package_manifest").sha256,
                "weights_sha256": metadata["weights"]["sha256"],
                "replay": "partition_start_fixed_weights_zero_memory_per_original_epoch",
                "optimizer_updates": 0, "qualification": "engineering_descriptive",
                "scientific_verdict": "not_claimed"}
    return {"schema": INPUT_SCHEMA, "request": asdict(request), "projection": PROJECTION,
            "source_sha256": source.dataset.source_sha256,
            "source_kind": source.dataset.source_kind,
            "source_parameters": source.manifest.parameters.value(),
            "package_model_id": metadata["model_id"],
            "package_manifest_sha256": model.payload("package_manifest").sha256,
            "weights_sha256": metadata["weights"]["sha256"],
            "replay": "partition_start_fixed_weights_zero_memory_per_run",
            "optimizer_updates": 0, "qualification": "engineering_descriptive",
            "scientific_verdict": "not_claimed"}


def prepare_structured_evaluation(
    store: ArtifactStore, request: StructuredEvaluationRequest, producer: Producer, *,
    authority: EvaluationAuthority | None = None,
) -> Manifest:
    """Verify exact input facts and publish a separate immutable evaluation request."""
    if not isinstance(request, StructuredEvaluationRequest):
        raise BoundaryError("structured_evaluation", "typed_request_required")
    _current(authority)
    source = _source(store, request)
    model_manifest, metadata, model = _model(store, request.model_id)
    del model
    evaluation_input = Manifest(
        "analysis", producer,
        (Parent("source", request.source_id), Parent("model", request.model_id)),
        parameters=FrozenObject.of(_input_parameters(request, source, metadata, model_manifest)),
    )
    _current(authority)
    store.publish(evaluation_input)
    _current(authority)
    return evaluation_input


def _rows(model: StructuredM2, dataset: StructuredDataset,
          authority: EvaluationAuthority | None) -> list[dict[str, Any]]:
    rows = []
    row_bytes = 0
    with torch.inference_mode():
        for run in dataset.runs:
            _current(authority)
            memory = model.initial_memory()
            for step in run.steps:
                _current(authority)
                if step.reset_before:
                    memory = model.initial_memory()
                entities = model.encode(step.frame)
                if step.advance:
                    memory = model.advance(entities, memory)
                logits = (model.score(step.frame, entities, memory) if step.frame.action_ids
                          else entities.new_empty((0,)))
                scores = [float(item) for item in logits]
                if len(scores) != len(step.frame.action_ids) or any(
                    not math.isfinite(item) for item in scores
                ):
                    raise BoundaryError("structured_evaluation", "candidate_scores")
                target = (step.frame.action_ids.index(step.chosen_action_id)
                          if step.chosen_action_id is not None else None)
                loss = float(F.cross_entropy(logits.unsqueeze(0), torch.tensor([target]))) \
                    if target is not None else None
                if loss is not None and not math.isfinite(loss):
                    raise BoundaryError("structured_evaluation", "nonfinite_loss")
                row = {"run_id": run.run_id, "source_group": run.source_group,
                             "position": step.position, "capture_id": step.capture_id,
                             "capsule_sha256": step.capsule_sha256,
                             "state_digest": step.frame.state_digest,
                             "candidate_digest": step.frame.candidate_digest,
                             "candidate_count": len(scores),
                             "action_ids": list(step.frame.action_ids), "scores": scores,
                             "label_index": target, "loss": loss,
                             "selected_index": int(logits.argmax()) if scores else None,
                             "reset_before": step.reset_before,
                             "reset_reason": step.reset_reason, "advance": step.advance}
                row_bytes += len(json_bytes(row))
                if row_bytes > MAX_REPORT_BYTES:
                    raise BoundaryError("structured_evaluation", "report_size_limit")
                rows.append(row)
    return rows


def run_structured_evaluation(
    store: ArtifactStore, evaluation_input_id: str, producer: Producer, *,
    authority: EvaluationAuthority | None = None,
) -> Manifest:
    """Replay fixed weights from each declared run start; publish descriptive scores."""
    _current(authority)
    evaluation_input = store.get_manifest(evaluation_input_id)
    parameters = evaluation_input.parameters.value()
    if evaluation_input.kind != "analysis" or parameters.get("schema") not in {
        INPUT_SCHEMA, ORDERED_INPUT_SCHEMA
    }:
        raise BoundaryError("structured_evaluation", "evaluation_input_required")
    request_fields = object_fields(parameters.get("request"), {
        "source_id", "model_id", "operation_id", "partition", "intent",
    }, "structured_evaluation.request")
    request = StructuredEvaluationRequest(**request_fields)
    if (evaluation_input.payloads or set(p.role for p in evaluation_input.parents)
            != {"source", "model"}
            or evaluation_input.parent("source") != request.source_id
            or evaluation_input.parent("model") != request.model_id):
        raise BoundaryError("structured_evaluation", "evaluation_parent_binding")
    source = _source(store, request)
    model_manifest, metadata, model = _model(store, request.model_id)
    if parameters != _input_parameters(request, source, metadata, model_manifest):
        raise BoundaryError("structured_evaluation", "evaluation_input_identity_drift")
    torch.set_num_threads(2)
    frozen = {key: value.clone() for key, value in model.state_dict().items()}
    rows = _rows(model, source.dataset, authority)
    _current(authority)
    summary = evaluate_runs(model, source.dataset.runs)
    if any(not torch.equal(value, model.state_dict()[key]) for key, value in frozen.items()):
        raise BoundaryError("structured_evaluation", "fixed_weights_changed")
    report_schema = (ORDERED_REPORT_SCHEMA if parameters["schema"] == ORDERED_INPUT_SCHEMA
                     else REPORT_SCHEMA)
    report = {**parameters, "schema": report_schema,
              "evaluation_input_id": evaluation_input_id,
              "model_artifact_id": request.model_id, "source_id": request.source_id,
              "producer": producer.to_dict(), "model_producer": model_manifest.producer.to_dict(),
              "source_producer": source.manifest.producer.to_dict(),
              "raw_source_ids": list(source.source_ids),
              "graph": metadata["graph"],
              "parameter_count": sum(value.numel() for value in model.parameters()),
              "model_selection_exposure": "unknown_caller_owned",
              "source_group_overlap_check": "caller_owned_before_invocation",
              "summary": summary, "rows": rows,
              "known_label_denominator": summary["labels"],
              "multi_candidate_label_denominator": summary["multi_candidate_labels"],
              "run_boundaries": [{"run_id": run.run_id, "source_group": run.source_group,
                                  "identity": run.identity.value(), "rows": len(run.steps)}
                                 for run in source.dataset.runs],
              "source_group_boundaries": [{
                  "source_group": group,
                  "run_ids": [run.run_id for run in source.dataset.runs
                              if run.source_group == group],
                  "observations": sum(len(run.steps) for run in source.dataset.runs
                                      if run.source_group == group),
                  "known_label_denominator": sum(step.chosen_action_id is not None
                                                 for run in source.dataset.runs
                                                 if run.source_group == group
                                                 for step in run.steps),
              } for group in sorted(source.source_groups)],
              "capsule_bytes_verified": source.dataset.capsules_verified,
              "claims": {"human": False, "independent_generalization": False,
                         "all_scene_quality": False, "performs_model_selection": False},
              "runtime": {"device": "cpu", "dtype": "float32", "threads": 2,
                          "torch_version": torch.__version__}}
    raw = json_bytes(report)
    if len(raw) > MAX_REPORT_BYTES:
        raise BoundaryError("structured_evaluation", "report_size_limit")
    _current(authority)
    payload = store.put_payload("report", io.BytesIO(raw), "application/json")
    result = Manifest(
        "offline_evaluation", producer,
        (Parent("evaluation_input", evaluation_input_id), Parent("source", request.source_id),
         Parent("model", request.model_id)), (payload,), FrozenObject.of({
             "schema": report_schema, "request": asdict(request),
             "package_model_id": metadata["model_id"],
             "weights_sha256": metadata["weights"]["sha256"],
             "source_sha256": source.dataset.source_sha256, "rows": len(rows),
             "known_label_denominator": summary["labels"], "summary": summary,
             "optimizer_updates": 0, "qualification": "engineering_descriptive",
             "scientific_verdict": "not_claimed"}),
    )
    _current(authority)
    store.publish(result)
    _current(authority)
    return result
