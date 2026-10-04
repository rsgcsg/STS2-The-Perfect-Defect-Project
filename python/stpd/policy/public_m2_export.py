"""Immutable Public M2 payload export; no Runtime or game authority."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import tempfile
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, digest, json_bytes, object_fields
from spireagent.package_identity import file_sha256
from spireagent.policy_files import _object_file

EXPORT_SCHEMA = "stpd/public-m2-policy-export-v1"
RECEIPT_SCHEMA = "stpd/public-m2-export-verification-v1"
FILES = {"weights": "weights.tensor-tree", "state_tokenizer": "state_tokenizer.json"}
MAXIMUMS = {"weights": 512 * 1024 * 1024, "state_tokenizer": 16 * 1024 * 1024}
MODEL_SCHEMA = "stpd/public-m2-model-v1"


def model_details(model: Manifest) -> tuple[Any, dict[str, Any]]:
    from stpd.models.token_core import ScratchShape
    from stpd.workers.public_m2_engine import PublicM2EngineConfig

    info = model.parameters.value()
    object_fields(info, {"schema", "epoch", "run_complete", "operation_id", "config",
                         "input_identity", "engine_input_digest", "implementation_sha256",
                         "qualification"}, "public_m2_export.model")
    if (model.kind != "model" or info["schema"] != MODEL_SCHEMA
            or info["qualification"] != "engineering_only"
            or type(info["epoch"]) is not int or info["epoch"] not in {1, 3, 5}
            or info["run_complete"] is not (info["epoch"] == 5)
            or sorted(p.role for p in model.parents) != ["checkpoint", "run", "training_input"]
            or sorted(p.role for p in model.payloads) != sorted(FILES)):
        raise BoundaryError("public_m2_export", "unsupported_public_m2_model")
    # Decode the published dataclass contract without importing the training run
    # owner (and its offline source/data/application dependency graph) into serve.
    value = object_fields(info["config"], {f.name for f in fields(PublicM2EngineConfig)},
                          "public_m2_export.config")
    shape = object_fields(value["shape"], {f.name for f in fields(ScratchShape)},
                          "public_m2_export.shape")
    config = PublicM2EngineConfig(**{**value, "shape": ScratchShape(**shape)})
    for key in ("operation_id", "input_identity", "engine_input_digest", "implementation_sha256"):
        digest(info[key], "public_m2_export." + key, length=32 if key == "operation_id" else 64)
    config.validate(require_device_available=False)
    if config.epochs != 5 or config.source_digest != info["input_identity"]:
        raise BoundaryError("public_m2_export", "public_m2_input_identity_mismatch")
    for role, maximum in MAXIMUMS.items():
        payload = model.payload(role)
        if not 0 < payload.size <= maximum:
            raise BoundaryError("public_m2_export", "payload_size_limit")
    if (model.payload("weights").media_type != "application/vnd.stpd.tensor-tree"
            or model.payload("state_tokenizer").sha256 != config.state_tokenizer_sha256):
        raise BoundaryError("public_m2_export", "payload_identity_mismatch")
    return config, info


def model_lineage(store: Any, model: Manifest, *, stage_id: str) -> dict[str, Any]:
    """Exact metadata parents, without reading training rows or optimizer payloads."""
    from stpd.fullrun.public_inputs import COMPACT_IDENTITY

    _config, info = model_details(model)
    run = store.get_manifest(model.parent("run"))
    training = store.get_manifest(model.parent("training_input"))
    checkpoint = store.get_manifest(model.parent("checkpoint"))
    r, t, c = (item.parameters.value() for item in (run, training, checkpoint))
    if (run.kind != "run" or r.get("schema") != "stpd/public-m2-run-v1"
            or training.kind != "training_input"
            or t.get("schema") != "stpd/public-m2-worker-input-v1"
            or checkpoint.kind != "checkpoint"
            or c.get("schema") != "stpd/public-m2-worker-checkpoint-v1"
            or any(item.producer != model.producer for item in (run, training, checkpoint))
            or run.parent("training_input") != training.artifact_id
            or sorted(p.role for p in training.parents) != ["allocation", "source_view"]
            or sorted(p.role for p in checkpoint.parents) != ["run", "training_input"]
            or checkpoint.parent("run") != run.artifact_id
            or checkpoint.parent("training_input") != training.artifact_id
            or any(r.get(key) != info[key] or c.get(key) != info[key] for key in (
                "operation_id", "config", "input_identity", "engine_input_digest",
                "implementation_sha256"))
            or t.get("input_identity") != info["input_identity"]
            or c.get("completed_epochs") != info["epoch"]
            or c.get("chain_index") != 0 or c.get("window_cursor") != 0
            or r.get("source_view_id") != training.parent("source_view")
            or r.get("allocation_id") != training.parent("allocation")
            or model.payload("state_tokenizer") != training.payload("state_tokenizer")):
        raise BoundaryError("public_m2_export", "public_m2_lineage_mismatch")
    view = store.get_manifest(training.parent("source_view"))
    allocation = store.get_manifest(training.parent("allocation"))
    if (view.kind != "model_view"
            or view.parameters.value().get("schema") != "stpd/public-observation-bc-view-v2"
            or view.parameters.value().get("serializer") != COMPACT_IDENTITY
            or sorted(p.role for p in view.parents) != ["allocation", "dataset"]
            or view.parent("allocation") != allocation.artifact_id
            or allocation.kind != "protocol"
            or allocation.parameters.value().get("schema") != "stpd/decision-allocation-v1"
            or [p.role for p in allocation.parents] != ["dataset"]
            or allocation.parent("dataset") != view.parent("dataset")
            or store.get_manifest(view.parent("dataset")).kind != "dataset"):
        raise BoundaryError("public_m2_export", "public_m2_lineage_mismatch")
    stage = store.get_manifest(stage_id)
    s = stage.parameters.value()
    if (stage.kind != "analysis" or stage.producer != model.producer
            or s != {"schema": "stpd/public-m2-epoch-stage-v1", "epoch": info["epoch"],
                     "run_complete": info["run_complete"], "operation_id": info["operation_id"],
                     "input_identity": info["input_identity"]}
            or sorted(p.role for p in stage.parents) != [
                "checkpoint", "model", "offline_evaluation", "run", "training_input"]
            or stage.parent("model") != model.artifact_id
            or any(stage.parent(role) != model.parent(role)
                   for role in ("checkpoint", "run", "training_input"))):
        raise BoundaryError("public_m2_export", "epoch_stage_required")
    evaluation = store.get_manifest(stage.parent("offline_evaluation"))
    e = evaluation.parameters.value()
    if (evaluation.kind != "offline_evaluation" or evaluation.producer != model.producer
            or set(e) != {"schema", "epoch", "partition", "input_identity", "loss_mean",
                          "top1_accuracy", "label_count", "correct_count", "qualification"}
            or e.get("schema") != "stpd/public-m2-dev-evaluation-v1"
            or e.get("epoch") != info["epoch"] or e.get("partition") != "dev"
            or e.get("input_identity") != info["input_identity"]
            or e.get("qualification") != "engineering_only"
            or type(e.get("label_count")) is not int or e["label_count"] <= 0
            or type(e.get("correct_count")) is not int
            or not 0 <= e["correct_count"] <= e["label_count"]
            or type(e.get("loss_mean")) not in {int, float}
            or not math.isfinite(e["loss_mean"]) or e["loss_mean"] < 0
            or type(e.get("top1_accuracy")) not in {int, float}
            or not math.isfinite(e["top1_accuracy"])
            or e["top1_accuracy"] != e["correct_count"] / e["label_count"]
            or sorted(p.role for p in evaluation.parents) != [
                "checkpoint", "model", "run", "training_input"]
            or evaluation.parent("model") != model.artifact_id
            or any(evaluation.parent(role) != model.parent(role)
                   for role in ("checkpoint", "run", "training_input"))):
        raise BoundaryError("public_m2_export", "epoch_evaluation_required")
    completion_id = None
    if info["epoch"] == 5:
        from spireagent.storage.run_reporter import ObjectStoreRunReporter

        result = ObjectStoreRunReporter(store, store.blobs).completed(run.artifact_id)
        if (result is None or result.producer != model.producer
                or result.parent("model") != model.artifact_id
                or result.parent("stage") != stage.artifact_id
                or result.parent("offline_evaluation") != evaluation.artifact_id
                or any(result.parent(role) != model.parent(role)
                       for role in ("checkpoint", "run", "training_input"))
                or sorted(p.role for p in result.parents) != [
                    "checkpoint", "model", "offline_evaluation", "run", "stage", "training_input"]
                or result.parameters.value() != {
                    "schema": "stpd/run-result-v1", "state": "completed", "epoch": 5,
                    "operation_id": info["operation_id"], "input_identity": info["input_identity"],
                    "qualification": "engineering_only"}):
            raise BoundaryError("public_m2_export", "completed_run_result_required")
        completion_id = result.artifact_id
    return {"run_id": run.artifact_id, "input_id": training.artifact_id,
            "checkpoint_id": checkpoint.artifact_id,
            "checkpoint_sha256": checkpoint.payload("checkpoint").sha256,
            "view_id": view.artifact_id, "allocation_id": allocation.artifact_id,
            "dataset_id": view.parent("dataset"), "training_operation_id": info["operation_id"],
            "stage_id": stage.artifact_id, "evaluation_id": evaluation.artifact_id,
            "completion_id": completion_id}


def validate_package(directory: Path, *, load_weights: bool = False, check_payloads: bool = True
                     ) -> tuple[Manifest, Any, dict[str, Any]]:
    if (directory.is_symlink() or not directory.is_dir()
            or {p.name for p in directory.iterdir()} != {"model.json", *FILES.values()}):
        raise BoundaryError("public_m2_export", "export_inventory_mismatch")
    envelope = _object_file(directory / "model.json")
    object_fields(envelope, {"schema", "model_id", "model", "lineage"}, "public_m2_export")
    if envelope["schema"] != EXPORT_SCHEMA:
        raise BoundaryError("public_m2_export", "unsupported_export")
    model = Manifest.from_bytes(json_bytes(envelope["model"]), envelope["model_id"])
    config, info = model_details(model)
    lineage = object_fields(envelope["lineage"], {
        "run_id", "input_id", "checkpoint_id", "checkpoint_sha256", "view_id",
        "allocation_id", "dataset_id", "training_operation_id",
        "stage_id", "evaluation_id", "completion_id",
    }, "public_m2_export.lineage")
    if any(lineage[key] != model.parent(role) for key, role in (
        ("run_id", "run"), ("input_id", "training_input"), ("checkpoint_id", "checkpoint")
    )) or lineage["training_operation_id"] != info["operation_id"]:
        raise BoundaryError("public_m2_export", "public_m2_lineage_mismatch")
    for key, identity in lineage.items():
        if key == "completion_id" and identity is None and info["epoch"] != 5:
            continue
        digest(identity, "public_m2_export." + key,
               length=32 if key == "training_operation_id" else 64)
    for role, name in FILES.items():
        path, payload = directory / name, model.payload(role)
        if (path.is_symlink() or not path.is_file() or path.stat().st_size != payload.size
                or (check_payloads or load_weights) and file_sha256(path) != payload.sha256):
            raise BoundaryError("public_m2_export", "payload_identity_mismatch")
    if load_weights:
        from tokenizers import Tokenizer

        from stpd.workers.checkpoint_codec import decode_checkpoint
        from stpd.workers.public_m2_engine import load_public_m2_weights

        raw = (directory / FILES["weights"]).read_bytes()
        weights = decode_checkpoint(raw)
        if (weights.get("checkpoint_digest") != lineage["checkpoint_sha256"]
                or weights.get("implementation_sha256") != info["implementation_sha256"]):
            raise BoundaryError("public_m2_export", "checkpoint_identity_mismatch")
        scorer = load_public_m2_weights(raw, config, input_digest=info["engine_input_digest"],
                                        completed_epochs=info["epoch"], inference_device="cpu")
        del scorer
        tokenizer = Tokenizer.from_str((directory / FILES["state_tokenizer"]).read_text())
        if tokenizer.get_vocab_size(with_added_tokens=True) != config.shape.vocab_size:
            raise BoundaryError("public_m2_export", "tokenizer_identity_mismatch")
    return model, config, lineage


def receipt(directory: Path) -> dict[str, Any]:
    model, _config, lineage = validate_package(directory)
    return {"schema": RECEIPT_SCHEMA, "model_id": model.artifact_id, **lineage,
            "package_sha256": file_sha256(directory / "model.json"),
            "payload_sha256": {p.role: p.sha256 for p in model.payloads},
            "payload_sizes": {p.role: p.size for p in model.payloads},
            "payload_bytes": sum(p.size for p in model.payloads)}


def export_model(store: Any, model_id: str, destination: Path, *, stage_id: str) -> dict[str, Any]:
    from stpd.workers.public_m2_engine import (
        PUBLIC_M2_CHECKPOINT_SCHEMA,
        _tensor_digest,
        decode_checkpoint,
    )

    model = store.get_manifest(model_id)
    config, info = model_details(model)
    lineage = model_lineage(store, model, stage_id=stage_id)
    # Decode only the bounded safetensors tree, never pickle. Bind inference
    # tensors to this exact epoch checkpoint using the engine's digest function.
    checkpoint = store.get_manifest(model.parent("checkpoint")).payload("checkpoint")
    if not 0 < checkpoint.size <= MAXIMUMS["weights"]:
        raise BoundaryError("public_m2_export", "checkpoint_size_limit")
    checksum, count, chunks = hashlib.sha256(), 0, []
    for block in store.read_payload(checkpoint):
        count += len(block)
        if count > checkpoint.size:
            raise BoundaryError("public_m2_export", "checkpoint_size_limit")
        checksum.update(block)
        chunks.append(block)
    if count != checkpoint.size or checksum.hexdigest() != checkpoint.sha256:
        raise BoundaryError("public_m2_export", "checkpoint_identity_mismatch")
    checkpoint_value = decode_checkpoint(b"".join(chunks))
    del chunks
    tensors = checkpoint_value.get("model")
    from torch import Tensor

    if (checkpoint_value.get("schema") != PUBLIC_M2_CHECKPOINT_SCHEMA
            or checkpoint_value.get("config") != asdict(config)
            or checkpoint_value.get("input_digest") != info["engine_input_digest"]
            or checkpoint_value.get("completed_epochs") != info["epoch"]
            or checkpoint_value.get("chain_index") != 0
            or checkpoint_value.get("window_cursor") != 0
            or checkpoint_value.get("runtime", {}).get("implementation_sha256")
            != info["implementation_sha256"]
            or not isinstance(tensors, dict)
            or any(not isinstance(value, Tensor) for value in tensors.values())
            or checkpoint_value.get("model_digest") != _tensor_digest(tensors)):
        raise BoundaryError("public_m2_export", "checkpoint_model_mismatch")
    checkpoint_digest = checkpoint_value["model_digest"]
    updates = checkpoint_value.get("optimizer_updates")
    del checkpoint_value, tensors

    def check_weights(path: Path) -> None:
        weights = decode_checkpoint((path / FILES["weights"]).read_bytes())
        if (weights.get("weights_digest") != checkpoint_digest
                or weights.get("optimizer_updates") != updates):
            raise BoundaryError("public_m2_export", "checkpoint_weights_mismatch")

    if destination.exists() or destination.is_symlink():
        checked, _config, previous = validate_package(destination, load_weights=True)
        if checked != model or previous != lineage:
            raise BoundaryError("public_m2_export", "export_identity_mismatch")
        check_weights(destination)
        return receipt(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".public-m2-export-", dir=destination.parent))
    try:
        (staged / "model.json").write_bytes(json_bytes({
            "schema": EXPORT_SCHEMA, "model_id": model.artifact_id,
            "model": {**model.body(), "artifact_id": model.artifact_id}, "lineage": lineage,
        }))
        for role, name in FILES.items():
            payload = model.payload(role)
            written = 0
            with (staged / name).open("xb") as handle:
                for block in store.read_payload(payload):
                    written += len(block)
                    if written > payload.size:
                        raise BoundaryError("public_m2_export", "payload_size_limit")
                    handle.write(block)
            if file_sha256(staged / name) != payload.sha256:
                raise BoundaryError("public_m2_export", "payload_identity_mismatch")
        validate_package(staged, load_weights=True)
        check_weights(staged)
        os.rename(staged, destination)
        return receipt(destination)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
