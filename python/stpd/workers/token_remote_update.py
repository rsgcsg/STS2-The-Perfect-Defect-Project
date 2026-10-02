"""Train-only M0 update requests with local owner admission and checkpoint validation.

This module transports only the admitted train projection. It never publishes an artifact,
evaluates dev, creates a model-quality claim, or treats request identity as provider auth.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import torch
from tokenizers import Tokenizer

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    array,
    decode_json,
    digest,
    json_bytes,
    object_fields,
    unsigned,
)
from spireagent.storage.store import ArtifactStore

from ..fullrun.features import ModelSample
from ..fullrun.light_action_inputs import (
    CANONICAL_SCHEMA,
    MAX_TOKENIZER_BYTES,
    PUBLIC_SCHEMA,
    LightActionTokenRow,
    LoadedLightActionInputs,
    load_light_action_inputs,
)
from ..fullrun.token_inputs import MAX_PAYLOAD, input_texts
from ..light_action_codec import SPEC_BYTES, SPEC_SHA256, encode_action
from .checkpoint_codec import MAX_BYTES as MAX_CHECKPOINT_BYTES
from .token_ranking import (
    IndexedLightActionM0TrainRow,
    LightActionM0Config,
    LightActionM0TrainOnlyInputs,
    TokenRankingEngine,
    config_payload,
    decode_config,
)
from .token_worker import preflight_token_run

REQUEST_SCHEMA = "stpd/token-remote-update-request-v1"
RESULT_SCHEMA = "stpd/token-remote-update-result-v1"
MAX_REQUEST_BYTES = 1024 * 1024 * 1024
MAX_INDEXED_TRAIN_ROWS = 1_000_000


class TrainingOperationAuthority(Protocol):
    """The existing local owner method used to recheck a training operation."""

    def require_training_datasets(
        self, store: ArtifactStore, dataset_ids: tuple[str, ...], operation_id: str,
    ) -> dict[str, Any]: ...


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(value: object, stage: str) -> bytes:
    if not isinstance(value, str):
        raise BoundaryError(stage, "invalid_base64_payload")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise BoundaryError(stage, "invalid_base64_payload") from error
    if _b64encode(raw) != value:
        raise BoundaryError(stage, "noncanonical_base64_payload")
    return raw


def _sample_to_dict(sample: ModelSample) -> dict[str, Any]:
    return sample.to_dict()


def _sample_from_dict(value: object) -> ModelSample:
    item = object_fields(value, {
        "transition_id", "run_id", "split", "surface", "family", "state_text",
        "action_texts", "action_keys", "chosen_index",
    }, "token_remote_update.sample")
    texts = array(item["action_texts"], "token_remote_update.action_texts")
    keys = array(item["action_keys"], "token_remote_update.action_keys")
    if any(not isinstance(text, str) for text in texts + keys):
        raise BoundaryError("token_remote_update", "invalid_sample_text")
    fields = (item["transition_id"], item["run_id"], item["split"], item["surface"],
              item["family"], item["state_text"])
    if any(not isinstance(field, str) for field in fields):
        raise BoundaryError("token_remote_update", "invalid_sample_identity")
    return ModelSample(
        *fields,
        tuple(texts),
        tuple(keys),
        unsigned(item["chosen_index"], "token_remote_update.chosen_index"),
    )


def _token_row_to_dict(row: LightActionTokenRow) -> dict[str, Any]:
    return row.to_dict()


def _token_row_from_dict(value: object) -> LightActionTokenRow:
    item = object_fields(value, {"state", "actions", "action_ids"}, "token_remote_update.row")
    state = array(item["state"], "token_remote_update.state")
    actions = array(item["actions"], "token_remote_update.actions")
    action_ids = array(item["action_ids"], "token_remote_update.action_ids")
    if (any(type(token) is not int or token < 0 for token in state)
            or any(not isinstance(action, list)
                   or any(type(token) is not int or token < 0 for token in action)
                   for action in actions)
            or any(not isinstance(identity, str) for identity in action_ids)):
        raise BoundaryError("token_remote_update", "invalid_token_row")
    return LightActionTokenRow(
        tuple(state), tuple(tuple(action) for action in actions), tuple(action_ids),
    )


def _indexed_row_to_dict(item: IndexedLightActionM0TrainRow) -> dict[str, Any]:
    return {
        "source_index": item.source_index,
        "sample": _sample_to_dict(item.sample),
        "row": _token_row_to_dict(item.row),
    }


def _indexed_row_from_dict(value: object) -> IndexedLightActionM0TrainRow:
    item = object_fields(value, {"source_index", "sample", "row"},
                         "token_remote_update.indexed_row")
    return IndexedLightActionM0TrainRow(
        unsigned(item["source_index"], "token_remote_update.source_index"),
        _sample_from_dict(item["sample"]),
        _token_row_from_dict(item["row"]),
    )


def _decode_state_tokenizer(raw: bytes) -> Tokenizer:
    if not raw or len(raw) > MAX_TOKENIZER_BYTES:
        raise BoundaryError("token_remote_update", "state_tokenizer_size_limit")
    try:
        tokenizer = Tokenizer.from_str(raw.decode("utf-8"))
    except Exception as error:
        raise BoundaryError("token_remote_update", "invalid_state_tokenizer") from error
    if tokenizer.truncation is not None or tokenizer.padding is not None:
        raise BoundaryError("token_remote_update", "state_padding_or_truncation_forbidden")
    return tokenizer


def _validate_train_projection(
    rows: tuple[IndexedLightActionM0TrainRow, ...], tokenizer: Tokenizer,
    config: LightActionM0Config,
) -> None:
    if not rows or len(rows) > MAX_INDEXED_TRAIN_ROWS:
        raise BoundaryError("token_remote_update", "train_row_count_limit")
    indices = tuple(item.source_index for item in rows)
    if indices != tuple(sorted(set(indices))):
        raise BoundaryError("token_remote_update", "train_row_indices_invalid")
    for item in rows:
        sample, row = item.sample, item.row
        encoded_state = tuple(tokenizer.encode(
            input_texts(sample.state_text, ())[0], add_special_tokens=False,
        ).ids)
        encoded_actions = tuple(
            encode_action(action, max_bytes=config.max_action_bytes)
            for action in sample.action_texts
        )
        if (not sample.state_text or row.state != encoded_state
                or row.actions != encoded_actions
                or row.action_ids != sample.action_keys
                or len(row.state) > config.max_state_tokens):
            raise BoundaryError("token_remote_update", "train_row_projection_mismatch")


def _validate_owner_operation(
    owner: TrainingOperationAuthority,
    store: ArtifactStore,
    binding: dict[str, Any],
    operation_id: str,
) -> None:
    operation = digest(operation_id, "token_remote_update.operation_id", length=32)
    dataset_ids = binding.get("dataset_ids")
    if (binding.get("training_operation_id") != operation
            or not isinstance(dataset_ids, list)
            or not dataset_ids
            or any(not isinstance(identity, str) for identity in dataset_ids)):
        raise BoundaryError("token_remote_update", "training_binding_mismatch")
    receipt = owner.require_training_datasets(store, tuple(dataset_ids), operation)
    if (not isinstance(receipt, dict)
            or receipt.get("operation_id") != operation
            or [item.get("artifact_id") for item in receipt.get("datasets", [])
                if isinstance(item, dict)] != dataset_ids):
        raise BoundaryError("token_remote_update", "owner_training_admission_mismatch")


@dataclass(frozen=True)
class TokenRemoteUpdateRequest:
    """Exact M0 update request containing indexed train rows only."""

    attempt_id: str
    run_manifest: Manifest
    input_manifest: Manifest
    producer: Producer
    operation_id: str
    training_binding: FrozenObject
    target_device: str
    target_step: int
    config: LightActionM0Config
    backbone_identity: FrozenObject
    train_rows: tuple[IndexedLightActionM0TrainRow, ...]
    state_tokenizer: bytes
    action_codec: bytes
    resume_manifest: Manifest | None = None
    resume_checkpoint: bytes | None = None
    schema: str = REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if (self.schema != REQUEST_SCHEMA
                or not isinstance(self.run_manifest, Manifest)
                or not isinstance(self.input_manifest, Manifest)
                or not isinstance(self.producer, Producer)
                or not isinstance(self.training_binding, FrozenObject)
                or not isinstance(self.backbone_identity, FrozenObject)
                or not isinstance(self.config, LightActionM0Config)
                or not isinstance(self.train_rows, tuple)
                or any(not isinstance(item, IndexedLightActionM0TrainRow)
                       for item in self.train_rows)
                or not isinstance(self.state_tokenizer, bytes)
                or not isinstance(self.action_codec, bytes)):
            raise BoundaryError("token_remote_update", "invalid_request_contract")
        digest(self.attempt_id, "token_remote_update.attempt_id", length=32)
        digest(self.operation_id, "token_remote_update.operation_id", length=32)
        if (self.target_device != self.config.device
                or type(self.target_step) is not int
                or not 1 <= self.target_step <= self.config.steps):
            raise BoundaryError("token_remote_update", "target_config_mismatch")
        run_info = self.run_manifest.parameters.value()
        input_info = self.input_manifest.parameters.value()
        binding = self.training_binding.value()
        if (self.run_manifest.kind != "run"
                or self.run_manifest.producer != self.producer
                or sorted(parent.role for parent in self.run_manifest.parents)
                != ["experiment", "training_input"]
                or self.run_manifest.parent("training_input") != self.input_manifest.artifact_id
                or run_info.get("config") != config_payload(self.config)
                or run_info.get("training_binding") != binding
                or self.input_manifest.kind != "training_input"
                or sorted(parent.role for parent in self.input_manifest.parents) != ["model_view"]
                or input_info.get("schema") not in {CANONICAL_SCHEMA, PUBLIC_SCHEMA}
                or input_info.get("training_binding") != binding
                or binding.get("training_operation_id") != self.operation_id
                or run_info.get("training_binding") != input_info.get("training_binding")):
            raise BoundaryError("token_remote_update", "run_input_binding_mismatch")
        if input_info.get("schema") == PUBLIC_SCHEMA:
            renderer = input_info.get("source_renderer")
            if (self.config.public_profile not in {"public_lite", "public_compact"}
                    or not isinstance(renderer, dict)
                    or renderer.get("profile") != self.config.public_profile):
                raise BoundaryError("token_remote_update", "public_profile_mismatch")
        if (input_info.get("schema") == CANONICAL_SCHEMA
                and self.config.public_profile is not None):
            raise BoundaryError("token_remote_update", "public_profile_mismatch")
        state_codec = input_info.get("state_codec")
        if (not isinstance(state_codec, dict)
                or hashlib.sha256(self.state_tokenizer).hexdigest()
                != state_codec.get("sha256")
                or len(self.state_tokenizer) > MAX_TOKENIZER_BYTES
                or self.action_codec != SPEC_BYTES
                or hashlib.sha256(self.action_codec).hexdigest() != SPEC_SHA256):
            raise BoundaryError("token_remote_update", "codec_identity_mismatch")
        if (sorted(payload.role for payload in self.input_manifest.payloads)
                != ["action_codec", "rows", "state_tokenizer"]
                or self.input_manifest.payload("state_tokenizer").size != len(self.state_tokenizer)
                or self.input_manifest.payload("state_tokenizer").sha256
                != hashlib.sha256(self.state_tokenizer).hexdigest()
                or self.input_manifest.payload("action_codec").size != len(self.action_codec)
                or self.input_manifest.payload("action_codec").sha256
                != hashlib.sha256(self.action_codec).hexdigest()
                or self.input_manifest.payload("rows").size > MAX_PAYLOAD):
            raise BoundaryError("token_remote_update", "input_payload_identity_mismatch")
        tokenizer = _decode_state_tokenizer(self.state_tokenizer)
        _validate_train_projection(self.train_rows, tokenizer, self.config)
        sample_count = input_info.get("samples")
        counts = input_info.get("counts")
        indices = tuple(item.source_index for item in self.train_rows)
        if (type(sample_count) is not int or sample_count > MAX_INDEXED_TRAIN_ROWS
                or not isinstance(counts, dict) or set(counts) != {"train", "dev"}
                or any(type(counts[split]) is not int or counts[split] < 1
                       for split in ("train", "dev"))
                or counts["train"] != len(self.train_rows)
                or counts["train"] + counts["dev"] != sample_count
                or any(index >= sample_count for index in indices)):
            raise BoundaryError("token_remote_update", "train_row_count_limit")
        if (self.resume_manifest is None) != (self.resume_checkpoint is None):
            raise BoundaryError("token_remote_update", "resume_pair_required")
        if self.resume_manifest is not None and self.resume_checkpoint is not None:
            _validate_resume_manifest(
                self.resume_manifest, self.resume_checkpoint, self.run_manifest,
                self.input_manifest, self.producer,
            )

    @property
    def run_id(self) -> str:
        return self.run_manifest.artifact_id

    @property
    def input_id(self) -> str:
        return self.input_manifest.artifact_id

    @property
    def resume_checkpoint_id(self) -> str | None:
        return self.resume_manifest.artifact_id if self.resume_manifest is not None else None

    def to_bytes(self) -> bytes:
        body = {
            "schema": self.schema,
            "attempt_id": self.attempt_id,
            "run_manifest": _b64encode(self.run_manifest.to_bytes()),
            "input_manifest": _b64encode(self.input_manifest.to_bytes()),
            "producer": self.producer.to_dict(),
            "operation_id": self.operation_id,
            "training_binding": self.training_binding.value(),
            "target_device": self.target_device,
            "target_step": self.target_step,
            "config": config_payload(self.config),
            "backbone_identity": self.backbone_identity.value(),
            "train_rows": [_indexed_row_to_dict(item) for item in self.train_rows],
            "state_tokenizer": _b64encode(self.state_tokenizer),
            "action_codec": _b64encode(self.action_codec),
            "resume_manifest": (_b64encode(self.resume_manifest.to_bytes())
                                if self.resume_manifest is not None else None),
            "resume_checkpoint": (_b64encode(self.resume_checkpoint)
                                  if self.resume_checkpoint is not None else None),
        }
        raw = json_bytes(body)
        if len(raw) > MAX_REQUEST_BYTES:
            raise BoundaryError("token_remote_update", "request_size_limit")
        return raw

    @property
    def request_sha256(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()

    @classmethod
    def from_bytes(cls, raw: bytes) -> TokenRemoteUpdateRequest:
        if len(raw) > MAX_REQUEST_BYTES:
            raise BoundaryError("token_remote_update", "request_size_limit")
        value = object_fields(decode_json(raw), {
            "schema", "attempt_id", "run_manifest", "input_manifest", "producer",
            "operation_id", "training_binding", "target_device", "target_step", "config",
            "backbone_identity", "train_rows", "state_tokenizer", "action_codec",
            "resume_manifest", "resume_checkpoint",
        }, "token_remote_update.request")
        if raw != json_bytes(value):
            raise BoundaryError("token_remote_update", "noncanonical_request")
        config = decode_config(value["config"])
        if not isinstance(config, LightActionM0Config):
            raise BoundaryError("token_remote_update", "m0_config_required")
        train_rows = tuple(_indexed_row_from_dict(item)
                           for item in array(value["train_rows"], "token_remote_update.train_rows"))
        resume_manifest = (
            Manifest.from_bytes(_b64decode(value["resume_manifest"], "token_remote_update"))
            if value["resume_manifest"] is not None else None
        )
        resume_checkpoint = (
            _b64decode(value["resume_checkpoint"], "token_remote_update")
            if value["resume_checkpoint"] is not None else None
        )
        if not isinstance(value["training_binding"], dict) or not isinstance(
            value["backbone_identity"], dict,
        ):
            raise BoundaryError("token_remote_update", "invalid_request_identity")
        return cls(
            value["attempt_id"],
            Manifest.from_bytes(_b64decode(value["run_manifest"], "token_remote_update")),
            Manifest.from_bytes(_b64decode(value["input_manifest"], "token_remote_update")),
            Producer.decode(value["producer"]),
            value["operation_id"],
            FrozenObject.of(value["training_binding"]),
            value["target_device"],
            unsigned(value["target_step"], "token_remote_update.target_step"),
            config,
            FrozenObject.of(value["backbone_identity"]),
            train_rows,
            _b64decode(value["state_tokenizer"], "token_remote_update"),
            _b64decode(value["action_codec"], "token_remote_update"),
            resume_manifest,
            resume_checkpoint,
            value["schema"],
        )


def _validate_resume_manifest(
    checkpoint: Manifest,
    raw: bytes,
    run: Manifest,
    input_manifest: Manifest,
    producer: Producer,
) -> None:
    if len(raw) > MAX_CHECKPOINT_BYTES:
        raise BoundaryError("token_remote_update", "resume_checkpoint_size_limit")
    if (checkpoint.kind != "checkpoint"
            or checkpoint.producer != producer
            or sorted(parent.role for parent in checkpoint.parents) != ["run", "training_input"]
            or [payload.role for payload in checkpoint.payloads] != ["checkpoint"]
            or checkpoint.parent("run") != run.artifact_id
            or checkpoint.parent("training_input") != input_manifest.artifact_id
            or checkpoint.parameters.value().get("schema")
            != "stpd/stage1a-light-action-m0-checkpoint-v1"
            or checkpoint.payload("checkpoint").size != len(raw)
            or checkpoint.payload("checkpoint").sha256 != hashlib.sha256(raw).hexdigest()):
        raise BoundaryError("token_remote_update", "resume_manifest_identity_mismatch")


@dataclass(frozen=True)
class TokenRemoteUpdateResult:
    """Untrusted update-only response. Acceptance requires local validation."""

    request_sha256: str
    attempt_id: str
    run_id: str
    input_id: str
    producer: Producer
    operation_id: str
    training_binding: FrozenObject
    target_device: str
    target_step: int
    config: LightActionM0Config
    resume_checkpoint_id: str | None
    resume_checkpoint_sha256: str | None
    checkpoint_step: int
    checkpoint_sha256: str
    checkpoint: bytes
    backbone_identity: FrozenObject
    schema: str = RESULT_SCHEMA

    def __post_init__(self) -> None:
        if (self.schema != RESULT_SCHEMA or not isinstance(self.producer, Producer)
                or not isinstance(self.training_binding, FrozenObject)
                or not isinstance(self.backbone_identity, FrozenObject)
                or not isinstance(self.config, LightActionM0Config)
                or not isinstance(self.checkpoint, bytes)):
            raise BoundaryError("token_remote_update", "invalid_result_contract")
        digest(self.request_sha256, "token_remote_update.request_sha256")
        digest(self.attempt_id, "token_remote_update.attempt_id", length=32)
        digest(self.run_id, "token_remote_update.run_id")
        digest(self.input_id, "token_remote_update.input_id")
        digest(self.operation_id, "token_remote_update.operation_id", length=32)
        digest(self.checkpoint_sha256, "token_remote_update.checkpoint_sha256")
        if self.resume_checkpoint_id is not None:
            digest(self.resume_checkpoint_id, "token_remote_update.resume_checkpoint_id")
        if self.resume_checkpoint_sha256 is not None:
            digest(self.resume_checkpoint_sha256, "token_remote_update.resume_checkpoint_sha256")
        if ((self.resume_checkpoint_id is None) != (self.resume_checkpoint_sha256 is None)
                or self.target_device != self.config.device
                or type(self.target_step) is not int
                or type(self.checkpoint_step) is not int
                or self.checkpoint_step != self.target_step
                or not 1 <= self.checkpoint_step <= self.config.steps
                or len(self.checkpoint) > MAX_CHECKPOINT_BYTES
                or hashlib.sha256(self.checkpoint).hexdigest() != self.checkpoint_sha256):
            raise BoundaryError("token_remote_update", "result_identity_mismatch")

    def to_bytes(self) -> bytes:
        raw = json_bytes({
            "schema": self.schema,
            "request_sha256": self.request_sha256,
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "input_id": self.input_id,
            "producer": self.producer.to_dict(),
            "operation_id": self.operation_id,
            "training_binding": self.training_binding.value(),
            "target_device": self.target_device,
            "target_step": self.target_step,
            "config": config_payload(self.config),
            "resume_checkpoint_id": self.resume_checkpoint_id,
            "resume_checkpoint_sha256": self.resume_checkpoint_sha256,
            "checkpoint_step": self.checkpoint_step,
            "checkpoint_sha256": self.checkpoint_sha256,
            "checkpoint": _b64encode(self.checkpoint),
            "backbone_identity": self.backbone_identity.value(),
        })
        if len(raw) > MAX_REQUEST_BYTES:
            raise BoundaryError("token_remote_update", "result_size_limit")
        return raw

    @classmethod
    def from_bytes(cls, raw: bytes) -> TokenRemoteUpdateResult:
        if len(raw) > MAX_REQUEST_BYTES:
            raise BoundaryError("token_remote_update", "result_size_limit")
        value = object_fields(decode_json(raw), {
            "schema", "request_sha256", "attempt_id", "run_id", "input_id", "producer",
            "operation_id", "training_binding", "target_device", "target_step", "config",
            "resume_checkpoint_id", "resume_checkpoint_sha256", "checkpoint_step",
            "checkpoint_sha256", "checkpoint", "backbone_identity",
        }, "token_remote_update.result")
        if raw != json_bytes(value):
            raise BoundaryError("token_remote_update", "noncanonical_result")
        config = decode_config(value["config"])
        if (not isinstance(config, LightActionM0Config)
                or not isinstance(value["training_binding"], dict)
                or not isinstance(value["backbone_identity"], dict)):
            raise BoundaryError("token_remote_update", "invalid_result_identity")
        return cls(
            value["request_sha256"], value["attempt_id"], value["run_id"], value["input_id"],
            Producer.decode(value["producer"]), value["operation_id"],
            FrozenObject.of(value["training_binding"]), value["target_device"],
            unsigned(value["target_step"], "token_remote_update.target_step"), config,
            value["resume_checkpoint_id"], value["resume_checkpoint_sha256"],
            unsigned(value["checkpoint_step"], "token_remote_update.checkpoint_step"),
            value["checkpoint_sha256"],
            _b64decode(value["checkpoint"], "token_remote_update"),
            FrozenObject.of(value["backbone_identity"]), value["schema"],
        )


def prepare_token_remote_update(
    store: ArtifactStore,
    owner: TrainingOperationAuthority,
    run_id: str,
    producer: Producer,
    operation_id: str,
    target_step: int,
    *,
    resume_checkpoint_id: str | None = None,
    attempt_id: str | None = None,
    snapshot: Path | None = None,
) -> TokenRemoteUpdateRequest:
    """Create a remote request only after exact local run and owner admission checks."""
    run, config, input_manifest, owner_bound = preflight_token_run(
        store, run_id, producer, resume=resume_checkpoint_id,
    )
    if not isinstance(config, LightActionM0Config) or not owner_bound:
        raise BoundaryError("token_remote_update", "owner_admitted_m0_input_required")
    input_info = input_manifest.parameters.value()
    binding_value = input_info.get("training_binding")
    if not isinstance(binding_value, dict):
        raise BoundaryError("token_remote_update", "training_binding_required")
    operation = digest(operation_id, "token_remote_update.operation_id", length=32)
    _validate_owner_operation(owner, store, binding_value, operation)

    inputs = load_light_action_inputs(store, input_manifest.artifact_id)
    if not isinstance(inputs, LoadedLightActionInputs):
        raise BoundaryError("token_remote_update", "light_action_inputs_required")
    engine = TokenRankingEngine(inputs, config, snapshot=snapshot)
    resume_manifest = None
    resume_bytes = None
    if resume_checkpoint_id is not None:
        resume_manifest = store.get_manifest(resume_checkpoint_id)
        resume_bytes = b"".join(
            store.read_payload(resume_manifest.payload("checkpoint")),
        )
        _validate_resume_manifest(
            resume_manifest, resume_bytes, run, input_manifest, producer,
        )
        engine.restore(resume_bytes)
    if type(target_step) is not int or not engine.step < target_step <= config.steps:
        raise BoundaryError("token_remote_update", "invalid_update_target_step")

    indexed_rows = tuple(
        IndexedLightActionM0TrainRow(index, sample, inputs.rows[index])
        for index, sample in enumerate(inputs.samples)
        if sample.split == "train"
    )
    tokenizer_bytes = b"".join(store.read_payload(input_manifest.payload("state_tokenizer")))
    action_codec = b"".join(store.read_payload(input_manifest.payload("action_codec")))
    return TokenRemoteUpdateRequest(
        attempt_id or uuid.uuid4().hex,
        run,
        input_manifest,
        producer,
        operation,
        FrozenObject.of(binding_value),
        config.device,
        target_step,
        config,
        FrozenObject.of(engine.backbone),
        indexed_rows,
        tokenizer_bytes,
        action_codec,
        resume_manifest,
        resume_bytes,
    )


def execute_token_remote_update(
    request: TokenRemoteUpdateRequest,
    *,
    snapshot: Path | None = None,
) -> TokenRemoteUpdateResult:
    """Run only configured optimizer updates and return the existing typed checkpoint bytes."""
    run_info = request.run_manifest.parameters.value()
    if (str(torch.__version__) != run_info.get("torch_version")
            or torch.get_num_threads() != run_info.get("cpu_threads")):
        raise BoundaryError("token_remote_update", "producer_runtime_mismatch")
    tokenizer = _decode_state_tokenizer(request.state_tokenizer)
    train_inputs = LightActionM0TrainOnlyInputs(
        request.input_manifest, request.train_rows, tokenizer,
    )
    engine = TokenRankingEngine(train_inputs, request.config, snapshot=snapshot)
    if FrozenObject.of(engine.backbone) != request.backbone_identity:
        raise BoundaryError("token_remote_update", "backbone_identity_mismatch")
    if request.resume_checkpoint is not None:
        engine.restore(request.resume_checkpoint)
    if not engine.step < request.target_step <= request.config.steps:
        raise BoundaryError("token_remote_update", "invalid_update_target_step")
    while engine.step < request.target_step:
        engine.advance()
    checkpoint = engine.checkpoint()
    return TokenRemoteUpdateResult(
        request.request_sha256,
        request.attempt_id,
        request.run_id,
        request.input_id,
        request.producer,
        request.operation_id,
        request.training_binding,
        request.target_device,
        request.target_step,
        request.config,
        request.resume_checkpoint_id,
        (hashlib.sha256(request.resume_checkpoint).hexdigest()
         if request.resume_checkpoint is not None else None),
        engine.step,
        hashlib.sha256(checkpoint).hexdigest(),
        checkpoint,
        request.backbone_identity,
    )


def validate_token_remote_update(
    store: ArtifactStore,
    owner: TrainingOperationAuthority,
    request: TokenRemoteUpdateRequest,
    result: TokenRemoteUpdateResult,
    producer: Producer,
    operation_id: str,
    *,
    snapshot: Path | None = None,
) -> TokenRemoteUpdateResult:
    """Recheck local owner/input lineage and restore the returned typed checkpoint."""
    if not isinstance(result, TokenRemoteUpdateResult):
        raise BoundaryError("token_remote_update", "typed_result_required")
    if (producer != request.producer or operation_id != request.operation_id
            or result.request_sha256 != request.request_sha256
            or result.attempt_id != request.attempt_id
            or result.run_id != request.run_id
            or result.input_id != request.input_id
            or result.producer != request.producer
            or result.operation_id != request.operation_id
            or result.training_binding != request.training_binding
            or result.target_device != request.target_device
            or result.target_step != request.target_step
            or result.config != request.config
            or result.resume_checkpoint_id != request.resume_checkpoint_id
            or result.backbone_identity != request.backbone_identity):
        raise BoundaryError("token_remote_update", "result_request_binding_mismatch")

    run, config, input_manifest, owner_bound = preflight_token_run(
        store, request.run_id, producer, resume=request.resume_checkpoint_id,
    )
    if (not owner_bound or not isinstance(config, LightActionM0Config)
            or run != request.run_manifest or input_manifest != request.input_manifest
            or config != request.config):
        raise BoundaryError("token_remote_update", "local_run_identity_mismatch")
    input_info = input_manifest.parameters.value()
    binding = input_info.get("training_binding")
    if not isinstance(binding, dict):
        raise BoundaryError("token_remote_update", "training_binding_required")
    _validate_owner_operation(owner, store, binding, operation_id)
    if FrozenObject.of(binding) != request.training_binding:
        raise BoundaryError("token_remote_update", "local_training_binding_mismatch")

    inputs = load_light_action_inputs(store, input_manifest.artifact_id)
    expected_rows = tuple(
        IndexedLightActionM0TrainRow(index, sample, inputs.rows[index])
        for index, sample in enumerate(inputs.samples)
        if sample.split == "train"
    )
    tokenizer_bytes = b"".join(store.read_payload(input_manifest.payload("state_tokenizer")))
    action_codec = b"".join(store.read_payload(input_manifest.payload("action_codec")))
    if (expected_rows != request.train_rows
            or tokenizer_bytes != request.state_tokenizer
            or action_codec != request.action_codec):
        raise BoundaryError("token_remote_update", "request_training_projection_mismatch")

    engine = TokenRankingEngine(inputs, config, snapshot=snapshot)
    if FrozenObject.of(engine.backbone) != request.backbone_identity:
        raise BoundaryError("token_remote_update", "local_backbone_identity_mismatch")
    if request.resume_manifest is not None and request.resume_checkpoint is not None:
        stored_resume = store.get_manifest(request.resume_manifest.artifact_id)
        stored_bytes = b"".join(store.read_payload(stored_resume.payload("checkpoint")))
        if stored_resume != request.resume_manifest or stored_bytes != request.resume_checkpoint:
            raise BoundaryError("token_remote_update", "local_resume_checkpoint_mismatch")
        engine.restore(stored_bytes)
        if (result.resume_checkpoint_sha256 != hashlib.sha256(stored_bytes).hexdigest()):
            raise BoundaryError("token_remote_update", "result_resume_identity_mismatch")
    elif result.resume_checkpoint_sha256 is not None:
        raise BoundaryError("token_remote_update", "unexpected_resume_identity")
    engine.restore(result.checkpoint)
    if engine.step != request.target_step:
        raise BoundaryError("token_remote_update", "checkpoint_target_step_mismatch")
    return result
