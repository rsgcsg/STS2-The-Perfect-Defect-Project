"""Independent dev-only evaluation for frozen public M2 epoch exports.

The caller owns source admission and immutable artifact orchestration. This
module binds an ordered dev-only selection to an already-read PublicM2Input,
loads exported weights without constructing a training engine, and combines
complete-chain shards only when their identities and coverage agree exactly.
"""

from __future__ import annotations

import hashlib
import math
import platform
import time
from dataclasses import asdict, dataclass
from typing import Any

import torch

from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes

from ..canonical import semantic_hash
from ..fullrun.public_m2_sequences import PublicM2Input
from ..light_action_codec import SPEC_SHA256
from ..models.light_action_m2_training_data import LightActionM2TrainingStep
from .checkpoint_codec import decode_checkpoint
from .public_m2_engine import (
    PUBLIC_M2_EXPORT_SCHEMA,
    PublicM2EngineChain,
    PublicM2EngineConfig,
    _chain_values,
    _tensor_digest,
    load_public_m2_weights,
)

SELECTION_SCHEMA = "stpd/public-m2-dev-eval-selection-v1"
EVALUATION_SCHEMA = "stpd/public-m2-dev-eval-shard-v1"
SUMMARY_SCHEMA = "stpd/public-m2-dev-eval-summary-v1"
RUNTIME_FIELDS = frozenset({
    "torch", "python", "platform", "default_dtype", "cpu_threads", "device_type",
    "gpu_name", "gpu_compute_capability", "gpu_total_memory_bytes", "cuda_version",
    "cudnn_version", "tf32_matmul", "tf32_cudnn", "float32_matmul_precision",
    "deterministic_algorithms", "deterministic_warn_only", "cudnn_benchmark",
    "cudnn_deterministic",
})
_MAX_ROWS = 100_000
_MAX_CHAINS = 100_000
_MAX_SELECTION_BYTES = 32 * 1024 * 1024


def _sha(value: object, field: str) -> str:
    try:
        return digest(value, "public_m2_evaluation." + field)
    except (TypeError, ValueError) as error:
        raise BoundaryError("public_m2_evaluation", "invalid_digest") from error


@dataclass(frozen=True)
class PublicM2EvalLimits:
    """Independent evaluation ceilings; these never rewrite training config."""

    max_rows: int = _MAX_ROWS
    max_chains: int = _MAX_CHAINS
    max_payload_bytes: int = _MAX_SELECTION_BYTES
    max_chain_steps: int = _MAX_ROWS
    max_state_tokens: int = 1_000_000
    max_actions_per_step: int = 16_384
    max_action_bytes: int = 8192
    max_total_input_tokens: int = 100_000_000

    def validate(self) -> None:
        if (type(self.max_rows) is not int or not 1 <= self.max_rows <= _MAX_ROWS
                or type(self.max_chains) is not int or not 1 <= self.max_chains <= _MAX_CHAINS
                or type(self.max_payload_bytes) is not int
                or not 1 <= self.max_payload_bytes <= _MAX_SELECTION_BYTES
                or type(self.max_chain_steps) is not int
                or not 1 <= self.max_chain_steps <= _MAX_ROWS
                or type(self.max_state_tokens) is not int
                or not 1 <= self.max_state_tokens <= 1_000_000
                or type(self.max_actions_per_step) is not int
                or not 1 <= self.max_actions_per_step <= 16_384
                or type(self.max_action_bytes) is not int
                or not 1 <= self.max_action_bytes <= 8192
                or type(self.max_total_input_tokens) is not int
                or not 1 <= self.max_total_input_tokens <= 100_000_000):
            raise BoundaryError("public_m2_evaluation", "invalid_eval_limits")


@dataclass(frozen=True)
class PublicM2EvalChainRef:
    ordinal: int
    chain_id: str
    row_count: int
    ordered_transition_ids: tuple[str, ...]
    ordered_evidence_sha256: tuple[str, ...]

    def validate(self) -> None:
        if (type(self.ordinal) is not int or self.ordinal < 0
                or type(self.chain_id) is not str or not self.chain_id
                or type(self.row_count) is not int or self.row_count < 1
                or len(self.ordered_transition_ids) != self.row_count
                or len(self.ordered_evidence_sha256) != self.row_count):
            raise BoundaryError("public_m2_evaluation", "invalid_chain_ref")
        for item in self.ordered_transition_ids:
            if type(item) is not str or not item:
                raise BoundaryError("public_m2_evaluation", "invalid_transition_membership")
        for item in self.ordered_evidence_sha256:
            _sha(item, "evidence_sha256")


@dataclass(frozen=True)
class PublicM2EvalSelection:
    source_input_identity: str
    training_input_digest: str
    tokenizer_sha256: str
    action_codec_sha256: str
    chains: tuple[PublicM2EvalChainRef, ...]
    limits: PublicM2EvalLimits

    def content(self) -> dict[str, Any]:
        return {
            "schema": SELECTION_SCHEMA,
            "source_input_identity": self.source_input_identity,
            "training_input_digest": self.training_input_digest,
            "tokenizer_sha256": self.tokenizer_sha256,
            "action_codec_sha256": self.action_codec_sha256,
            "chains": [asdict(chain) for chain in self.chains],
            "limits": asdict(self.limits),
        }

    @property
    def identity(self) -> str:
        return str(semantic_hash(self.content()))

    def payload_bytes(self) -> bytes:
        return json_bytes({**self.content(), "identity": self.identity})

    @property
    def ordered_transition_ids(self) -> tuple[str, ...]:
        return tuple(item for chain in self.chains for item in chain.ordered_transition_ids)

    @property
    def ordered_membership_digest(self) -> str:
        return str(semantic_hash([
            {"chain_ordinal": chain.ordinal, "chain_id": chain.chain_id,
             "transition_ids": chain.ordered_transition_ids,
             "evidence_sha256": chain.ordered_evidence_sha256}
            for chain in self.chains
        ]))

    @property
    def row_count(self) -> int:
        return sum(chain.row_count for chain in self.chains)

    def validate_against(self, source: PublicM2Input) -> None:
        _validate_selection(self, source)


@dataclass(frozen=True)
class PublicM2EvalShard:
    selection_identity: str
    start_ordinal: int
    stop_ordinal: int

    def validate(self, selection: PublicM2EvalSelection) -> None:
        _sha(self.selection_identity, "selection_identity")
        if (self.selection_identity != selection.identity
                or type(self.start_ordinal) is not int
                or type(self.stop_ordinal) is not int
                or not 0 <= self.start_ordinal < self.stop_ordinal <= len(selection.chains)):
            raise BoundaryError("public_m2_evaluation", "invalid_shard_range")


@dataclass(frozen=True)
class PublicM2EvalChainResult:
    ordinal: int
    chain_id: str
    evidence_sha256: tuple[str, ...]
    label_count: int
    cross_entropy_sum: float
    correct_count: int


@dataclass(frozen=True)
class PublicM2EvalExpectedBindings:
    """Independently pinned model/header identity required by the reducer."""

    training_input_digest: str
    weights_sha256: str
    weights_digest: str
    completed_epochs: int
    configured_epochs: int
    run_complete: bool
    implementation_sha256: str
    config_digest: str
    inference_device: str

    def validate(self) -> None:
        for name in (
            "training_input_digest", "weights_sha256", "weights_digest",
            "implementation_sha256", "config_digest",
        ):
            _sha(getattr(self, name), name)
        if (type(self.completed_epochs) is not int or self.completed_epochs not in {1, 3, 5}
                or type(self.configured_epochs) is not int
                or self.configured_epochs not in {1, 3, 5}
                or self.completed_epochs > self.configured_epochs
                or type(self.run_complete) is not bool
                or self.run_complete is not (self.completed_epochs == self.configured_epochs)
                or type(self.inference_device) is not str
                or not self.inference_device):
            raise BoundaryError("public_m2_evaluation", "invalid_expected_model_bindings")


@dataclass(frozen=True)
class PublicM2EvalShardResult:
    schema: str
    selection_identity: str
    source_input_identity: str
    training_input_digest: str
    weights_sha256: str
    weights_digest: str
    completed_epochs: int
    configured_epochs: int
    run_complete: bool
    implementation_sha256: str
    config_digest: str
    runtime: dict[str, Any]
    inference_device: str
    shard: PublicM2EvalShard
    ordered_chain_ids: tuple[str, ...]
    chains: tuple[PublicM2EvalChainResult, ...]
    phase_seconds: dict[str, float]
    peak_allocated_bytes: int
    peak_reserved_bytes: int


@dataclass(frozen=True)
class PublicM2EvalSummary:
    schema: str
    selection_identity: str
    source_input_identity: str
    training_input_digest: str
    weights_sha256: str
    weights_digest: str
    completed_epochs: int
    configured_epochs: int
    run_complete: bool
    implementation_sha256: str
    config_digest: str
    runtime: dict[str, Any]
    inference_device: str
    chain_count: int
    label_count: int
    cross_entropy_sum: float
    loss_mean: float
    correct_count: int
    top1_accuracy: float
    ordered_chain_ids: tuple[str, ...]
    ordered_transition_ids: tuple[str, ...]
    ordered_membership_digest: str
    phase_seconds: dict[str, float]
    peak_allocated_bytes: int
    peak_reserved_bytes: int

    @property
    def row_count(self) -> int:
        return self.label_count


def public_m2_engine_input_digest(
    train_chains: tuple[PublicM2EngineChain, ...],
    dev_chains: tuple[PublicM2EngineChain, ...],
) -> str:
    """Compute the export's original train/dev chain digest when needed."""
    if not train_chains or not dev_chains:
        raise BoundaryError("public_m2_evaluation", "train_dev_membership_required")
    return hashlib.sha256(json_bytes({
        "train": _chain_values(train_chains), "dev": _chain_values(dev_chains),
    })).hexdigest()


def _evidence_sha(chain: object) -> tuple[str, ...]:
    evidence = getattr(chain, "evidence", None)
    if not isinstance(evidence, tuple) or not evidence:
        raise BoundaryError("public_m2_evaluation", "dev_evidence_required")
    return tuple(str(semantic_hash(asdict(row))) for row in evidence)


def _transition_ids(chain: object) -> tuple[str, ...]:
    return tuple(str(row.transition_id) for row in getattr(chain, "evidence", ()))


def build_public_m2_eval_selection(
    source: PublicM2Input, *, training_input_digest: str,
    limits: PublicM2EvalLimits | None = None,
) -> PublicM2EvalSelection:
    """Freeze complete dev chain order/evidence without consulting train labels."""
    if limits is None:
        limits = PublicM2EvalLimits()
    if not isinstance(source, PublicM2Input) or not isinstance(limits, PublicM2EvalLimits):
        raise BoundaryError("public_m2_evaluation", "typed_input_and_limits_required")
    _sha(source.identity, "source_input_identity")
    _sha(training_input_digest, "training_input_digest")
    limits.validate()
    dev = tuple(chain for chain in source.chains if chain.split == "dev")
    refs = tuple(PublicM2EvalChainRef(
        index, chain.chain_id, len(chain.steps), _transition_ids(chain), _evidence_sha(chain),
    ) for index, chain in enumerate(dev))
    selection = PublicM2EvalSelection(
        source.identity, training_input_digest,
        hashlib.sha256(source.state_tokenizer).hexdigest(), SPEC_SHA256,
        refs, limits,
    )
    _validate_selection(selection, source)
    raw = selection.payload_bytes()
    if len(raw) > limits.max_payload_bytes:
        raise BoundaryError("public_m2_evaluation", "selection_payload_limit")
    return selection


def _validate_selection(selection: PublicM2EvalSelection, source: PublicM2Input) -> None:
    if not isinstance(source, PublicM2Input) or not isinstance(selection, PublicM2EvalSelection):
        raise BoundaryError("public_m2_evaluation", "typed_selection_required")
    for field, value in (("source_input_identity", selection.source_input_identity),
                         ("training_input_digest", selection.training_input_digest),
                         ("tokenizer_sha256", selection.tokenizer_sha256),
                         ("action_codec_sha256", selection.action_codec_sha256)):
        _sha(value, field)
    selection.limits.validate()
    dev = tuple(chain for chain in source.chains if chain.split == "dev")
    if (selection.source_input_identity != source.identity
            or selection.tokenizer_sha256 != hashlib.sha256(source.state_tokenizer).hexdigest()
            or selection.action_codec_sha256 != SPEC_SHA256
            or not selection.chains or len(selection.chains) != len(dev)
            or len(selection.chains) > selection.limits.max_chains
            or any(ref.ordinal != index for index, ref in enumerate(selection.chains))
            or sum(ref.row_count for ref in selection.chains) > selection.limits.max_rows
            or sum(len(step.page) + sum(map(len, step.byte_actions))
                   for chain in dev for step in chain.steps)
            > selection.limits.max_total_input_tokens):
        raise BoundaryError("public_m2_evaluation", "selection_membership_mismatch")
    for ref, chain in zip(selection.chains, dev, strict=True):
        ref.validate()
        if (ref.chain_id != chain.chain_id or ref.row_count != len(chain.steps)
                or ref.ordered_transition_ids != _transition_ids(chain)
                or ref.ordered_evidence_sha256 != _evidence_sha(chain)
                or len(chain.steps) > selection.limits.max_chain_steps
                or not _valid_chain(chain.steps, selection.limits)):
            raise BoundaryError("public_m2_evaluation", "selection_chain_mismatch")
    payload = selection.payload_bytes()
    if len(payload) > selection.limits.max_payload_bytes:
        raise BoundaryError("public_m2_evaluation", "selection_payload_limit")


def _valid_chain(
    steps: tuple[LightActionM2TrainingStep, ...], limits: PublicM2EvalLimits,
) -> bool:
    if not steps:
        return False
    for position, step in enumerate(steps):
        if (not isinstance(step, LightActionM2TrainingStep)
                or type(step.position) is not int or step.position != position
                or step.reset_before is not (position == 0)
                or step.previous_actual_action is not None
                or step.target_action_id is None
                or not step.action_ids or len(step.action_ids) != len(step.byte_actions)
                or len(step.page) > limits.max_state_tokens
                or len(step.action_ids) > limits.max_actions_per_step
                or any(len(action) < 3 or len(action) - 2 > limits.max_action_bytes
                       for action in step.byte_actions)
                or step.action_ids.count(step.target_action_id) != 1):
            return False
    return sum(len(step.page) + sum(map(len, step.byte_actions)) for step in steps) \
        <= limits.max_total_input_tokens


def read_public_m2_eval_selection(raw: bytes, source: PublicM2Input) -> PublicM2EvalSelection:
    """Strict canonical wire roundtrip against the exact already-read input."""
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_SELECTION_BYTES:
        raise BoundaryError("public_m2_evaluation", "selection_payload_limit")
    data = decode_json(raw)
    required = {
        "schema", "source_input_identity", "training_input_digest", "tokenizer_sha256",
        "action_codec_sha256", "chains", "limits", "identity",
    }
    if (not isinstance(data, dict) or set(data) != required
            or data.get("schema") != SELECTION_SCHEMA):
        raise BoundaryError("public_m2_evaluation", "selection_wire_fields")
    try:
        limits_data = data["limits"]
        if not isinstance(limits_data, dict) or set(limits_data) != {
            "max_rows", "max_chains", "max_payload_bytes", "max_chain_steps",
            "max_state_tokens", "max_actions_per_step", "max_action_bytes",
            "max_total_input_tokens",
        }:
            raise ValueError
        limits = PublicM2EvalLimits(**limits_data)
        raw_refs = data["chains"]
        if not isinstance(raw_refs, list):
            raise ValueError
        refs = tuple(PublicM2EvalChainRef(
            item["ordinal"], item["chain_id"], item["row_count"],
            tuple(item["ordered_transition_ids"]), tuple(item["ordered_evidence_sha256"]),
        ) for item in raw_refs if isinstance(item, dict) and set(item) == {
            "ordinal", "chain_id", "row_count", "ordered_transition_ids",
            "ordered_evidence_sha256",
        })
        if len(refs) != len(raw_refs):
            raise ValueError
        selection = PublicM2EvalSelection(
            data["source_input_identity"], data["training_input_digest"],
            data["tokenizer_sha256"], data["action_codec_sha256"], refs, limits,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise BoundaryError("public_m2_evaluation", "selection_wire_types") from error
    _validate_selection(selection, source)
    if (len(raw) > limits.max_payload_bytes or data["identity"] != selection.identity
            or raw != selection.payload_bytes()):
        raise BoundaryError("public_m2_evaluation", "selection_identity_or_canonical_mismatch")
    return selection


def partition_public_m2_eval_selection(
    selection: PublicM2EvalSelection, start_ordinal: int, stop_ordinal: int,
) -> PublicM2EvalShard:
    shard = PublicM2EvalShard(selection.identity, start_ordinal, stop_ordinal)
    shard.validate(selection)
    return shard


def _runtime(device: torch.device) -> dict[str, Any]:
    is_cuda = device.type == "cuda"
    if is_cuda:
        properties = torch.cuda.get_device_properties(device)
        capability: list[int] | None = [properties.major, properties.minor]
        gpu_name: str | None = properties.name
        memory: int | None = int(properties.total_memory)
        cuda_version: str | None = torch.version.cuda
        cudnn_version: int | None = torch.backends.cudnn.version()
    else:
        capability = None
        gpu_name = None
        memory = None
        cuda_version = None
        cudnn_version = None
    return {
        "torch": str(torch.__version__), "python": platform.python_version(),
        "platform": platform.platform(), "default_dtype": str(torch.get_default_dtype()),
        "cpu_threads": torch.get_num_threads(), "device_type": device.type,
        "gpu_name": gpu_name, "gpu_compute_capability": capability,
        "gpu_total_memory_bytes": memory, "cuda_version": cuda_version,
        "cudnn_version": cudnn_version,
        "tf32_matmul": bool(torch.backends.cuda.matmul.allow_tf32),
        "tf32_cudnn": bool(torch.backends.cudnn.allow_tf32),
        "float32_matmul_precision": str(torch.get_float32_matmul_precision()),
        "deterministic_algorithms": bool(torch.are_deterministic_algorithms_enabled()),
        "deterministic_warn_only": bool(torch.is_deterministic_algorithms_warn_only_enabled()),
        "cudnn_benchmark": bool(torch.backends.cudnn.benchmark),
        "cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
    }


def _validate_runtime(value: object, inference_device: str) -> None:
    if (not isinstance(value, dict) or set(value) != RUNTIME_FIELDS
            or any(type(value.get(key)) is not str or not value[key]
                   for key in ("torch", "python", "platform", "default_dtype",
                               "float32_matmul_precision"))
            or type(value["cpu_threads"]) is not int or value["cpu_threads"] < 1
            or type(value["device_type"]) is not str
            or value["device_type"] not in {"cpu", "cuda"}
            or any(type(value[key]) is not bool for key in (
                "tf32_matmul", "tf32_cudnn", "deterministic_algorithms",
                "deterministic_warn_only", "cudnn_benchmark", "cudnn_deterministic",
            ))):
        raise BoundaryError("public_m2_evaluation", "invalid_runtime_identity")
    try:
        device = torch.device(inference_device)
    except (TypeError, ValueError, RuntimeError) as error:
        raise BoundaryError("public_m2_evaluation", "invalid_inference_device") from error
    if (str(device) != inference_device or device.type != value["device_type"]
            or device.type not in {"cpu", "cuda"}
            or device.type == "cpu" and device.index is not None
            or device.type == "cuda" and device.index is None):
        raise BoundaryError("public_m2_evaluation", "runtime_device_mismatch")
    gpu_keys = ("gpu_name", "gpu_compute_capability", "gpu_total_memory_bytes",
                "cuda_version", "cudnn_version")
    if device.type == "cpu":
        if any(value[key] is not None for key in gpu_keys):
            raise BoundaryError("public_m2_evaluation", "cpu_runtime_gpu_fields_present")
    else:
        capability = value["gpu_compute_capability"]
        if (type(value["gpu_name"]) is not str or not value["gpu_name"]
                or not isinstance(capability, (tuple, list)) or len(capability) != 2
                or any(type(item) is not int or item < 0 for item in capability)
                or type(value["gpu_total_memory_bytes"]) is not int
                or value["gpu_total_memory_bytes"] < 1
                or type(value["cuda_version"]) is not str or not value["cuda_version"]
                or value["cudnn_version"] is not None and (
                    type(value["cudnn_version"]) is not int
                    or value["cudnn_version"] < 1
                )):
            raise BoundaryError("public_m2_evaluation", "incomplete_cuda_runtime_identity")
    if value["default_dtype"] != "torch.float32":
        raise BoundaryError("public_m2_evaluation", "invalid_runtime_dtype")


_SESSION_TOKEN = object()


class PublicM2EvalSession:
    """Run-scoped, validated frozen weights reusable across dev selections/shards.

    Construct sessions only with ``open_public_m2_eval_session``. The session
    owns one model load and resets memory at every selected chain boundary.
    """

    def __init__(
        self, token: object, model: Any, config: PublicM2EngineConfig, *,
        training_input_digest: str, completed_epochs: int, header: dict[str, Any],
        weights_sha256: str, load_seconds: float, inference_device: str,
    ) -> None:
        if token is not _SESSION_TOKEN:
            raise BoundaryError("public_m2_evaluation", "validated_session_factory_required")
        self._model = model
        self._config = config
        self._training_input_digest = training_input_digest
        self._completed_epochs = completed_epochs
        self._configured_epochs = config.epochs
        self._header = dict(header)
        self._weights_sha256 = weights_sha256
        self._weights_digest = str(header["weights_digest"])
        self._implementation_sha256 = str(header["implementation_sha256"])
        self._config_digest = str(semantic_hash(asdict(config)))
        self._inference_device = inference_device
        self._runtime = _runtime(torch.device(inference_device))
        self._pending_load_seconds = load_seconds
        self._factory_validated = True

    @property
    def bindings(self) -> PublicM2EvalExpectedBindings:
        return PublicM2EvalExpectedBindings(
            self._training_input_digest, self._weights_sha256, self._weights_digest,
            self._completed_epochs, self._configured_epochs,
            bool(self._header["run_complete"]), self._implementation_sha256,
            self._config_digest, self._inference_device,
        )

    @property
    def runtime(self) -> dict[str, Any]:
        return dict(self._runtime)

    def evaluate(
        self, source: PublicM2Input, selection: PublicM2EvalSelection, *,
        shard: PublicM2EvalShard | None = None,
    ) -> PublicM2EvalShardResult:
        return _evaluate_loaded_session(self, source, selection, shard=shard)


def open_public_m2_eval_session(
    weights_raw: bytes, config: PublicM2EngineConfig, *, training_input_digest: str,
    inference_device: str = "cpu", completed_epochs: int = 1,
) -> PublicM2EvalSession:
    """Validate and load frozen epoch weights exactly once for this run."""
    if (not isinstance(config, PublicM2EngineConfig) or type(weights_raw) is not bytes
            or not weights_raw or type(completed_epochs) is not int
            or completed_epochs not in {1, 3, 5}):
        raise BoundaryError("public_m2_evaluation", "invalid_eval_session_input")
    _sha(training_input_digest, "training_input_digest")
    started = time.perf_counter()
    header = decode_checkpoint(weights_raw)
    if (not isinstance(header, dict) or header.get("schema") != PUBLIC_M2_EXPORT_SCHEMA
            or header.get("input_digest") != training_input_digest
            or header.get("completed_epochs") != completed_epochs
            or header.get("run_complete") is not (completed_epochs == config.epochs)
            or header.get("config") != asdict(config)):
        raise BoundaryError("public_m2_evaluation", "stage_weights_header_mismatch")
    model = load_public_m2_weights(
        weights_raw, config, input_digest=training_input_digest,
        completed_epochs=completed_epochs, inference_device=inference_device,
    )
    model.eval()
    device = next(model.parameters()).device
    if str(device) != inference_device:
        raise BoundaryError("public_m2_evaluation", "inference_device_mismatch")
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    return PublicM2EvalSession(
        _SESSION_TOKEN, model, config, training_input_digest=training_input_digest,
        completed_epochs=completed_epochs, header=header,
        weights_sha256=hashlib.sha256(weights_raw).hexdigest(),
        load_seconds=time.perf_counter() - started, inference_device=str(device),
    )


def _evaluate_loaded_session(
    session: PublicM2EvalSession, source: PublicM2Input,
    selection: PublicM2EvalSelection, *, shard: PublicM2EvalShard | None,
) -> PublicM2EvalShardResult:
    started = time.perf_counter()
    _validate_selection(selection, source)
    config = session._config
    if (selection.training_input_digest != session._training_input_digest
            or selection.tokenizer_sha256 != config.state_tokenizer_sha256
            or selection.action_codec_sha256 != config.action_codec_sha256):
        raise BoundaryError("public_m2_evaluation", "evaluation_input_binding_mismatch")
    if (session._model.training
            or _tensor_digest(session._model.state_dict()) != session._weights_digest
            or semantic_hash(asdict(config)) != session._config_digest):
        raise BoundaryError("public_m2_evaluation", "loaded_model_binding_changed")
    chosen = shard or partition_public_m2_eval_selection(selection, 0, len(selection.chains))
    chosen.validate(selection)
    device = next(session._model.parameters()).device
    if session._inference_device != str(device):
        raise BoundaryError("public_m2_evaluation", "inference_device_mismatch")
    if _runtime(device) != session._runtime:
        raise BoundaryError("public_m2_evaluation", "runtime_identity_changed")
    if device.type == "cuda":
        torch.cuda.synchronize(device)
        torch.cuda.reset_peak_memory_stats(device)
    dev = tuple(chain for chain in source.chains if chain.split == "dev")
    for ref in selection.chains[chosen.start_ordinal:chosen.stop_ordinal]:
        chain = dev[ref.ordinal]
        for step in chain.steps:
            if (len(step.page) > config.shape.max_tokens
                    or any(len(action) - 2 > config.max_action_bytes
                           for action in step.byte_actions)):
                raise BoundaryError("public_m2_evaluation", "model_input_limit_exceeded")
    chain_results: list[PublicM2EvalChainResult] = []
    numeric_started = time.perf_counter()
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    with torch.no_grad():
        for ordinal in range(chosen.start_ordinal, chosen.stop_ordinal):
            ref, chain = selection.chains[ordinal], dev[ordinal]
            memory = session._model.initial_memory()
            loss_sum = 0.0
            correct = 0
            for position, step in enumerate(chain.steps):
                page = torch.tensor(step.page, dtype=torch.long, device=device)
                actions = tuple(torch.tensor(action, dtype=torch.long, device=device)
                                for action in step.byte_actions)
                scores, memory = session._model.step(
                    page, actions, memory,
                    previous_actual_action=None, public_feedback=None,
                    reset_before=step.reset_before,
                )
                target = step.action_ids.index(step.target_action_id)
                loss = torch.nn.functional.cross_entropy(
                    scores.unsqueeze(0), torch.tensor([target], device=device), reduction="sum",
                )
                scalar = float(loss)
                if not math.isfinite(scalar):
                    raise BoundaryError("public_m2_evaluation", "nonfinite_loss")
                loss_sum += scalar
                correct += int(int(scores.argmax()) == target)
                if position == 0 and not step.reset_before:
                    raise BoundaryError("public_m2_evaluation", "first_step_reset_required")
            chain_results.append(PublicM2EvalChainResult(
                ordinal, ref.chain_id, ref.ordered_evidence_sha256,
                len(chain.steps), loss_sum, correct,
            ))
    if device.type == "cuda":
        torch.cuda.synchronize(device)
        peak_allocated = int(torch.cuda.max_memory_allocated(device))
        peak_reserved = int(torch.cuda.max_memory_reserved(device))
    else:
        peak_allocated = peak_reserved = 0
    numeric_seconds = time.perf_counter() - numeric_started
    load_seconds = session._pending_load_seconds
    session._pending_load_seconds = 0.0
    phase_seconds = {
        "model_load": load_seconds,
        "numeric_evaluation": numeric_seconds,
        "total": time.perf_counter() - started + load_seconds,
    }
    if any(not math.isfinite(value) or value < 0 for value in phase_seconds.values()):
        raise BoundaryError("public_m2_evaluation", "invalid_phase_timing")
    return PublicM2EvalShardResult(
        EVALUATION_SCHEMA, selection.identity, source.identity,
        session._training_input_digest, session._weights_sha256, session._weights_digest,
        session._completed_epochs, session._configured_epochs,
        bool(session._header["run_complete"]), session._implementation_sha256,
        session._config_digest, dict(session._runtime), session._inference_device, chosen,
        tuple(result.chain_id for result in chain_results), tuple(chain_results),
        phase_seconds, peak_allocated, peak_reserved,
    )


def evaluate_public_m2_stage(
    weights_raw: bytes, source: PublicM2Input, selection: PublicM2EvalSelection,
    config: PublicM2EngineConfig, *, training_input_digest: str,
    inference_device: str = "cpu", shard: PublicM2EvalShard | None = None,
    completed_epochs: int = 1,
) -> PublicM2EvalShardResult:
    """Convenience one-shot evaluator; use a session when scoring stages/shards repeatedly."""
    session = open_public_m2_eval_session(
        weights_raw, config, training_input_digest=training_input_digest,
        inference_device=inference_device, completed_epochs=completed_epochs,
    )
    return session.evaluate(source, selection, shard=shard)


def combine_public_m2_eval_shards(
    selection: PublicM2EvalSelection, shard_results: tuple[PublicM2EvalShardResult, ...],
    *, expected_bindings: PublicM2EvalExpectedBindings,
    expected_runtime: dict[str, Any],
) -> PublicM2EvalSummary:
    """Verify shards against loaded weights and aggregate exact once-only coverage."""
    if (not isinstance(shard_results, tuple) or not shard_results
            or not isinstance(selection, PublicM2EvalSelection)
            or not isinstance(expected_bindings, PublicM2EvalExpectedBindings)):
        raise BoundaryError("public_m2_evaluation", "typed_shard_results_required")
    expected_bindings.validate()
    _validate_runtime(expected_runtime, expected_bindings.inference_device)
    if expected_bindings.training_input_digest != selection.training_input_digest:
        raise BoundaryError("public_m2_evaluation", "expected_input_binding_mismatch")
    _sha(selection.identity, "selection_identity")
    _sha(selection.source_input_identity, "source_input_identity")
    _sha(selection.training_input_digest, "training_input_digest")
    _sha(selection.tokenizer_sha256, "tokenizer_sha256")
    _sha(selection.action_codec_sha256, "action_codec_sha256")
    selection.limits.validate()
    if (not selection.chains or len(selection.chains) > selection.limits.max_chains
            or selection.row_count > selection.limits.max_rows
            or len(selection.payload_bytes()) > selection.limits.max_payload_bytes
            or any(ref.ordinal != index for index, ref in enumerate(selection.chains))):
        raise BoundaryError("public_m2_evaluation", "invalid_selection_summary_binding")
    for ref in selection.chains:
        ref.validate()
    by_ordinal: dict[int, PublicM2EvalChainResult] = {}
    common: tuple[Any, ...] | None = None
    seconds: dict[str, float] = {}
    peak_allocated = peak_reserved = 0
    for result in shard_results:
        if (not isinstance(result, PublicM2EvalShardResult)
                or result.schema != EVALUATION_SCHEMA
                or not isinstance(result.shard, PublicM2EvalShard)
                or type(result.run_complete) is not bool
                or type(result.completed_epochs) is not int
                or type(result.configured_epochs) is not int
                or result.completed_epochs not in {1, 3, 5}
                or result.configured_epochs not in {1, 3, 5}
                or result.completed_epochs > result.configured_epochs
                or result.run_complete is not (
                    result.completed_epochs == result.configured_epochs
                )):
            raise BoundaryError("public_m2_evaluation", "invalid_shard_result")
        result.shard.validate(selection)
        for field, value in (
            ("selection_identity", result.selection_identity),
            ("source_input_identity", result.source_input_identity),
            ("training_input_digest", result.training_input_digest),
            ("weights_sha256", result.weights_sha256),
            ("weights_digest", result.weights_digest),
            ("implementation_sha256", result.implementation_sha256),
            ("config_digest", result.config_digest),
        ):
            _sha(value, field)
        if (result.selection_identity != selection.identity
                or result.source_input_identity != selection.source_input_identity
                or result.training_input_digest != selection.training_input_digest
                or result.weights_sha256 != expected_bindings.weights_sha256
                or result.weights_digest != expected_bindings.weights_digest
                or result.completed_epochs != expected_bindings.completed_epochs
                or result.configured_epochs != expected_bindings.configured_epochs
                or result.run_complete is not expected_bindings.run_complete
                or result.implementation_sha256 != expected_bindings.implementation_sha256
                or result.config_digest != expected_bindings.config_digest
                or result.inference_device != expected_bindings.inference_device):
            raise BoundaryError("public_m2_evaluation", "shard_expected_model_binding_mismatch")
        _validate_runtime(result.runtime, result.inference_device)
        if result.runtime != expected_runtime:
            raise BoundaryError("public_m2_evaluation", "shard_expected_runtime_mismatch")
        if (not isinstance(result.chains, tuple)
                or not isinstance(result.ordered_chain_ids, tuple)
                or any(type(item) is not str or not item for item in result.ordered_chain_ids)
                or not isinstance(result.phase_seconds, dict)
                or set(result.phase_seconds) != {
                    "model_load", "numeric_evaluation", "total",
                }):
            raise BoundaryError("public_m2_evaluation", "invalid_shard_result_shape")
        identity = (
            result.selection_identity, result.source_input_identity, result.training_input_digest,
            result.weights_sha256, result.weights_digest, result.completed_epochs,
            result.configured_epochs, result.run_complete,
            result.implementation_sha256, result.config_digest,
            tuple(sorted(result.runtime.items())), result.inference_device,
        )
        if common is None:
            common = identity
        elif identity != common:
            raise BoundaryError("public_m2_evaluation", "shard_identity_mismatch")
        expected_refs = selection.chains[result.shard.start_ordinal:result.shard.stop_ordinal]
        if (len(result.chains) != len(expected_refs)
                or result.ordered_chain_ids != tuple(ref.chain_id for ref in expected_refs)
                or tuple(item.chain_id for item in result.chains) != result.ordered_chain_ids):
            raise BoundaryError("public_m2_evaluation", "shard_chain_catalog_mismatch")
        for item, ref in zip(result.chains, expected_refs, strict=True):
            if (not isinstance(item, PublicM2EvalChainResult)
                    or type(item.ordinal) is not int or item.ordinal != ref.ordinal
                    or type(item.chain_id) is not str or item.chain_id != ref.chain_id
                    or item.evidence_sha256 != ref.ordered_evidence_sha256
                    or type(item.label_count) is not int or item.label_count != ref.row_count
                    or type(item.correct_count) is not int
                    or not 0 <= item.correct_count <= item.label_count
                    or type(item.cross_entropy_sum) not in {int, float}
                    or not math.isfinite(item.cross_entropy_sum)
                    or item.cross_entropy_sum < 0
                    or item.ordinal in by_ordinal):
                raise BoundaryError("public_m2_evaluation", "duplicate_or_tampered_chain_metric")
            by_ordinal[item.ordinal] = item
        for key, phase_value in result.phase_seconds.items():
            if (type(phase_value) not in {int, float}
                    or not math.isfinite(phase_value) or phase_value < 0):
                raise BoundaryError("public_m2_evaluation", "invalid_phase_timing")
            seconds[key] = seconds.get(key, 0.0) + phase_value
        if (result.phase_seconds["total"] < result.phase_seconds["numeric_evaluation"]
                or result.phase_seconds["model_load"] > result.phase_seconds["total"]):
            raise BoundaryError("public_m2_evaluation", "inconsistent_phase_timing")
        if (type(result.peak_allocated_bytes) is not int or result.peak_allocated_bytes < 0
                or type(result.peak_reserved_bytes) is not int or result.peak_reserved_bytes < 0
                or result.peak_reserved_bytes < result.peak_allocated_bytes
                or result.runtime["device_type"] == "cpu"
                and (result.peak_allocated_bytes != 0 or result.peak_reserved_bytes != 0)):
            raise BoundaryError("public_m2_evaluation", "invalid_memory_metrics")
        peak_allocated = max(peak_allocated, result.peak_allocated_bytes)
        peak_reserved = max(peak_reserved, result.peak_reserved_bytes)
    if (tuple(sorted(by_ordinal)) != tuple(range(len(selection.chains))) or common is None
            or len(by_ordinal) != len(selection.chains)):
        raise BoundaryError("public_m2_evaluation", "incomplete_eval_coverage")
    ordered = tuple(by_ordinal[index] for index in range(len(selection.chains)))
    count = sum(item.label_count for item in ordered)
    correct = sum(item.correct_count for item in ordered)
    total_loss = sum(item.cross_entropy_sum for item in ordered)
    if count < 1 or not math.isfinite(total_loss):
        raise BoundaryError("public_m2_evaluation", "empty_or_nonfinite_summary")
    if count != selection.row_count or len(ordered) != len(selection.chains):
        raise BoundaryError("public_m2_evaluation", "summary_selection_coverage_mismatch")
    return PublicM2EvalSummary(
        SUMMARY_SCHEMA, selection.identity, selection.source_input_identity,
        selection.training_input_digest, str(common[3]), str(common[4]),
        int(common[5]), int(common[6]), bool(common[7]),
        str(common[8]), str(common[9]), dict(common[10]), str(common[11]),
        len(ordered), count,
        total_loss, total_loss / count, correct, correct / count,
        tuple(item.chain_id for item in ordered), selection.ordered_transition_ids,
        selection.ordered_membership_digest, seconds, peak_allocated, peak_reserved,
    )
