"""Independent dev-only evaluation for a frozen one-epoch public M2 export.

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
    load_public_m2_weights,
)

SELECTION_SCHEMA = "stpd/public-m2-dev-eval-selection-v1"
EVALUATION_SCHEMA = "stpd/public-m2-dev-eval-shard-v1"
SUMMARY_SCHEMA = "stpd/public-m2-dev-eval-summary-v1"
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
class PublicM2EvalShardResult:
    schema: str
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


def _runtime() -> dict[str, Any]:
    return {
        "torch": str(torch.__version__), "python": platform.python_version(),
        "platform": platform.platform(), "default_dtype": str(torch.get_default_dtype()),
        "cpu_threads": torch.get_num_threads(),
    }


def evaluate_public_m2_stage(
    weights_raw: bytes, source: PublicM2Input, selection: PublicM2EvalSelection,
    config: PublicM2EngineConfig, *, training_input_digest: str,
    inference_device: str = "cpu", shard: PublicM2EvalShard | None = None,
    completed_epochs: int = 1,
) -> PublicM2EvalShardResult:
    """Score ordered full dev chains with frozen stage weights and no grad."""
    start_total = time.perf_counter()
    _validate_selection(selection, source)
    if (not isinstance(config, PublicM2EngineConfig)
            or training_input_digest != selection.training_input_digest
            or config.state_tokenizer_sha256 != selection.tokenizer_sha256
            or config.action_codec_sha256 != selection.action_codec_sha256
            or type(completed_epochs) is not int or completed_epochs not in {1, 3, 5}):
        raise BoundaryError("public_m2_evaluation", "evaluation_input_binding_mismatch")
    if type(weights_raw) is not bytes or not weights_raw:
        raise BoundaryError("public_m2_evaluation", "weights_required")
    chosen = shard or partition_public_m2_eval_selection(selection, 0, len(selection.chains))
    chosen.validate(selection)
    raw_sha = hashlib.sha256(weights_raw).hexdigest()
    decode_start = time.perf_counter()
    header = decode_checkpoint(weights_raw)
    if (not isinstance(header, dict) or header.get("schema") != PUBLIC_M2_EXPORT_SCHEMA
            or header.get("input_digest") != training_input_digest
            or header.get("completed_epochs") != completed_epochs
            or header.get("run_complete") is not (completed_epochs == config.epochs)
            or header.get("config") != asdict(config)):
        raise BoundaryError("public_m2_evaluation", "stage1_weights_header_mismatch")
    model = load_public_m2_weights(
        weights_raw, config, input_digest=training_input_digest,
        completed_epochs=completed_epochs,
        inference_device=inference_device,
    )
    model.eval()
    device = next(model.parameters()).device
    if device.type == "cuda":
        torch.cuda.synchronize(device)
        torch.cuda.reset_peak_memory_stats(device)
    load_seconds = time.perf_counter() - decode_start
    dev = tuple(chain for chain in source.chains if chain.split == "dev")
    for ref in selection.chains:
        chain = dev[ref.ordinal]
        for step in chain.steps:
            if (len(step.page) > config.shape.max_tokens
                    or any(len(action) - 2 > config.max_action_bytes
                           for action in step.byte_actions)):
                raise BoundaryError("public_m2_evaluation", "model_input_limit_exceeded")
    chain_results: list[PublicM2EvalChainResult] = []
    compute_start = time.perf_counter()
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    with torch.no_grad():
        for ordinal in range(chosen.start_ordinal, chosen.stop_ordinal):
            ref, chain = selection.chains[ordinal], dev[ordinal]
            memory = model.initial_memory()
            loss_sum = 0.0
            correct = 0
            for position, step in enumerate(chain.steps):
                page = torch.tensor(step.page, dtype=torch.long, device=device)
                actions = tuple(torch.tensor(action, dtype=torch.long, device=device)
                                for action in step.byte_actions)
                scores, memory = model.step(
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
    compute_seconds = time.perf_counter() - compute_start
    phase_seconds = {
        "header_and_model_load": load_seconds,
        "numeric_evaluation": compute_seconds,
        "total": time.perf_counter() - start_total,
    }
    if any(not math.isfinite(value) or value < 0 for value in phase_seconds.values()):
        raise BoundaryError("public_m2_evaluation", "invalid_phase_timing")
    return PublicM2EvalShardResult(
        EVALUATION_SCHEMA, selection.identity, source.identity, training_input_digest,
        raw_sha, str(header["weights_digest"]), completed_epochs,
        bool(header["run_complete"]),
        str(header["implementation_sha256"]), str(semantic_hash(asdict(config))),
        _runtime(), str(device), chosen,
        tuple(result.chain_id for result in chain_results), tuple(chain_results),
        phase_seconds, peak_allocated, peak_reserved,
    )


def combine_public_m2_eval_shards(
    selection: PublicM2EvalSelection, shard_results: tuple[PublicM2EvalShardResult, ...],
) -> PublicM2EvalSummary:
    """Require exact once-only ordinal coverage and aggregate by total labels."""
    if (not isinstance(shard_results, tuple) or not shard_results
            or not isinstance(selection, PublicM2EvalSelection)):
        raise BoundaryError("public_m2_evaluation", "typed_shard_results_required")
    by_ordinal: dict[int, PublicM2EvalChainResult] = {}
    common: tuple[Any, ...] | None = None
    seconds: dict[str, float] = {}
    peak_allocated = peak_reserved = 0
    for result in shard_results:
        if (not isinstance(result, PublicM2EvalShardResult)
                or result.schema != EVALUATION_SCHEMA):
            raise BoundaryError("public_m2_evaluation", "invalid_shard_result")
        result.shard.validate(selection)
        identity = (
            result.selection_identity, result.source_input_identity, result.training_input_digest,
            result.weights_sha256, result.weights_digest, result.completed_epochs,
            result.run_complete, result.implementation_sha256, result.config_digest,
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
            if (item.ordinal != ref.ordinal or item.chain_id != ref.chain_id
                    or item.evidence_sha256 != ref.ordered_evidence_sha256
                    or item.label_count != ref.row_count
                    or type(item.correct_count) is not int
                    or not 0 <= item.correct_count <= item.label_count
                    or not math.isfinite(item.cross_entropy_sum) or item.cross_entropy_sum < 0
                    or item.ordinal in by_ordinal):
                raise BoundaryError("public_m2_evaluation", "duplicate_or_tampered_chain_metric")
            by_ordinal[item.ordinal] = item
        for key, value in result.phase_seconds.items():
            if not math.isfinite(value) or value < 0:
                raise BoundaryError("public_m2_evaluation", "invalid_phase_timing")
            seconds[key] = seconds.get(key, 0.0) + value
        if (type(result.peak_allocated_bytes) is not int or result.peak_allocated_bytes < 0
                or type(result.peak_reserved_bytes) is not int or result.peak_reserved_bytes < 0):
            raise BoundaryError("public_m2_evaluation", "invalid_memory_metrics")
        peak_allocated = max(peak_allocated, result.peak_allocated_bytes)
        peak_reserved = max(peak_reserved, result.peak_reserved_bytes)
    if tuple(sorted(by_ordinal)) != tuple(range(len(selection.chains))) or common is None:
        raise BoundaryError("public_m2_evaluation", "incomplete_eval_coverage")
    ordered = tuple(by_ordinal[index] for index in range(len(selection.chains)))
    count = sum(item.label_count for item in ordered)
    correct = sum(item.correct_count for item in ordered)
    total_loss = sum(item.cross_entropy_sum for item in ordered)
    if count < 1 or not math.isfinite(total_loss):
        raise BoundaryError("public_m2_evaluation", "empty_or_nonfinite_summary")
    return PublicM2EvalSummary(
        SUMMARY_SCHEMA, selection.identity, selection.source_input_identity,
        selection.training_input_digest, str(common[3]), str(common[4]),
        int(common[5]), bool(common[6]),
        str(common[7]), str(common[8]), dict(common[9]), str(common[10]),
        len(ordered), count,
        total_loss, total_loss / count, correct, correct / count,
        tuple(item.chain_id for item in ordered), selection.ordered_transition_ids,
        selection.ordered_membership_digest, seconds, peak_allocated, peak_reserved,
    )
