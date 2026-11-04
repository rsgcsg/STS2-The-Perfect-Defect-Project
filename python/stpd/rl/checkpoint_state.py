"""W1-owned self-contained learning state and explicit legacy CP16 conversion seam.

Serialize with the existing tensor-tree codec, not pickle. Construction, weights
(including all heads/text), Adam, weight EMA, counters and RNG are embedded.
Provenance references are descriptive, never prerequisites for restoring learning.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, fields, is_dataclass
from typing import Any

import torch
from torch import Tensor

from spireagent.rl_contracts import RunSequence

# RL4.1 self-contained format, registered in the shared VERSION-SLOT before publication.
SCHEMA = "stpd/rl-segment-checkpoint-2"


# W1 owns this payload; W3 transports the completed CP through original Store/R2.
@dataclass(frozen=True)
class ModelConstruction:
    architecture: dict[str, Any]
    vocabulary: dict[str, Any]
    input_spec: dict[str, Any]
    compiler_identity: dict[str, Any]
    adapter: dict[str, Any]
    numerical: dict[str, Any]
    text_digest: str


@dataclass(frozen=True)
class LearningCounters:
    updates: int
    datasets: int
    first_use: int
    first_use_multi: int
    mu_version: int
    published_updates: int


@dataclass(frozen=True)
class RngState:
    python_random: tuple[Any, ...] | None  # legacy CP16 uses only the learner-local generator
    learner_random: tuple[Any, ...]
    torch_rng: Tensor
    cuda_rng: tuple[Tensor, ...]  # retain bytes on CPU migration even without CUDA


@dataclass(frozen=True)
class ConversionRecord:
    source_checkpoint_artifact_id: str
    source_schema: str
    source_sequence: int
    source_generation: str
    config_changes: dict[str, Any]
    discarded_hot_state: tuple[str, ...]


@dataclass(frozen=True)
class SelfContainedCheckpoint:
    identity: RunSequence  # sequence is CP number; snapshot only at dataset boundary
    construction: ModelConstruction
    config: dict[str, Any]
    producer: dict[str, Any]
    weights: dict[str, Tensor]
    optimizer: dict[str, Any]  # complete native Adam state_dict, groups/hyperparameters too
    optimizer_parameter_names: tuple[tuple[str, ...], ...]  # exact group/index ordering
    ema: dict[str, Tensor]  # actual existing self.ema is a weight EMA, not a scale
    counters: LearningCounters
    rng: RngState
    conversion: ConversionRecord | None
    # No queue/lease/active remainder/aux/input ancestry is a learning restore dependency.


@dataclass(frozen=True)
class CP16ConversionInput:
    legacy_state: dict[str, Any]  # decoded original CP16; never mutated
    construction: ModelConstruction  # materialized once at migration, then embedded
    optimizer_parameter_names: tuple[tuple[str, ...], ...]
    source_checkpoint_artifact_id: str
    source_generation: str
    target_generation: str
    target_config: dict[str, Any]
    producer: dict[str, Any]


# W1 implements this entry point; F0 freezes its signature, not a fake migration.
def convert_cp16(source: CP16ConversionInput) -> SelfContainedCheckpoint:
    """Zero SGD: preserve CP16/362/48951/42787/mu57 and theta/Adam/EMA/RNG.

    Carry source_sequence=16, then first normal save publishes CP17 and first
    new mu is 58 in target_generation. Discard unconsumed hot data only;
    already successful updates/counts remain. Legacy source stays immutable.
    """
    state = source.legacy_state
    metadata = json.loads(state["metadata"].cpu().numpy().tobytes())
    if (
        state.get("schema") != "stpd/rl-segment-checkpoint-1"
        or metadata["checkpoint_sequence"] != 16
        or source.target_generation == source.source_generation
        or source.target_config["run"] != metadata["config"]["run"]
    ):
        raise ValueError("explicit_CP16_and_new_generation_required")
    if text_digest(state["weights"]) != source.construction.text_digest:
        raise ValueError("CP16_text_construction_does_not_match_theta")
    validate_optimizer_order(state["optimizer"], source.optimizer_parameter_names, state["weights"])
    previous = metadata["config"]
    changes = {
        key: {"before": previous.get(key), "after": value}
        for key, value in source.target_config.items()
        if previous.get(key) != value
    }
    return SelfContainedCheckpoint(
        RunSequence(previous["run"], source.target_generation, 16),
        copy.deepcopy(source.construction),
        copy.deepcopy(source.target_config),
        copy.deepcopy(source.producer),
        cpu_copy(state["weights"]),
        cpu_copy(state["optimizer"]),
        source.optimizer_parameter_names,
        cpu_copy(state["ema"]),
        LearningCounters(
            *(
                metadata[name]
                for name in (
                    "updates",
                    "datasets",
                    "first_use",
                    "first_use_multi",
                    "version",
                    "published_updates",
                )
            )
        ),
        RngState(
            None,
            copy.deepcopy(state["random"]),
            state["torch_rng"].cpu().clone(),
            tuple(value.cpu().clone() for value in state["cuda_rng"]),
        ),
        ConversionRecord(
            source.source_checkpoint_artifact_id,
            state["schema"],
            16,
            source.source_generation,
            changes,
            ("queue", "leases", "active_remainder", "auxiliary", "input_log", "delayed_labels"),
        ),
    )


def cpu_copy(value: Any) -> Any:
    """Own immutable bytes: state_dict() alone aliases the next Adam update."""
    if isinstance(value, Tensor):
        return value.detach().to(device="cpu", copy=True).contiguous()
    if isinstance(value, dict):
        return {key: cpu_copy(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(cpu_copy(item) for item in value)
    if isinstance(value, list):
        return [cpu_copy(item) for item in value]
    return copy.deepcopy(value)


def snapshot_weights(weights: dict[str, Tensor]) -> dict[str, Tensor]:
    """One owned slab and one D2H copy per dtype, rather than one sync per parameter."""
    groups: dict[torch.dtype, list[tuple[str, Tensor]]] = defaultdict(list)
    for name, tensor in weights.items():
        groups[tensor.dtype].append((name, tensor))
    result = {}
    for group in groups.values():
        slab = torch.cat([tensor.detach().reshape(-1) for _, tensor in group]).cpu()
        cursor = 0
        for name, tensor in group:
            result[name] = slab[cursor : cursor + tensor.numel()].view(tensor.shape)
            cursor += tensor.numel()
    return {name: result[name] for name in weights}


def text_digest(weights: dict[str, Tensor]) -> str:
    result = hashlib.sha256()
    for name, tensor in sorted(weights.items()):
        if name.startswith(("text_ids.", "text_numbers.", "text_blocks.", "text_projection.")):
            value = tensor.detach().cpu().contiguous()
            result.update(name.encode())
            result.update(str((value.dtype, tuple(value.shape))).encode())
            result.update(value.view(torch.uint8).numpy().tobytes())
    return result.hexdigest()


def validate_optimizer_order(
    optimizer: dict[str, Any], names: tuple[tuple[str, ...], ...], weights: dict[str, Tensor]
) -> None:
    groups = optimizer["param_groups"]
    if (
        len(groups) != len(names)
        or any(
            len(group["params"]) != len(order) for group, order in zip(groups, names, strict=True)
        )
        or len({name for group in names for name in group}) != sum(map(len, names))
        or any(name not in weights for group in names for name in group)
    ):
        raise ValueError("checkpoint_optimizer_parameter_order_changed")


def encode_state(checkpoint: SelfContainedCheckpoint) -> bytes:
    from stpd.workers.checkpoint_codec import encode_checkpoint

    # _learning_state already owns immutable snapshot bytes. asdict() deepcopies
    # every Tensor view independently, cloning the entire shared slab once per
    # parameter. Convert metadata dataclasses without copying tensor storage.
    state: dict[str, Any] = {"schema": SCHEMA}
    for field in fields(checkpoint):
        value = getattr(checkpoint, field.name)
        if is_dataclass(value) and not isinstance(value, type):
            value = {part.name: getattr(value, part.name) for part in fields(value)}
        state[field.name] = value
    return encode_checkpoint(state)


def decode_state(raw: bytes) -> SelfContainedCheckpoint:
    from stpd.workers.checkpoint_codec import decode_checkpoint

    value = decode_checkpoint(raw)
    if value.pop("schema", None) != SCHEMA:
        raise ValueError("explicit_self_contained_checkpoint_required")
    value["identity"] = RunSequence(**value["identity"])
    value["construction"] = ModelConstruction(**value["construction"])
    value["counters"] = LearningCounters(**value["counters"])
    value["rng"] = RngState(**value["rng"])
    if value["conversion"] is not None:
        value["conversion"] = ConversionRecord(**value["conversion"])
    checkpoint = SelfContainedCheckpoint(**value)
    validate_optimizer_order(
        checkpoint.optimizer, checkpoint.optimizer_parameter_names, checkpoint.weights
    )
    if text_digest(checkpoint.weights) != checkpoint.construction.text_digest:
        raise ValueError("checkpoint_text_digest_changed")
    return checkpoint


def construct_model(construction: ModelConstruction) -> Any:
    """No start package, warmup checkpoint, input log or Exchange reads."""
    from stpd.p1.execution import MStdExecution
    from stpd.p1.export import input_spec, runtime_projection
    from stpd.p1.model import MStd

    model = MStd(
        construction.vocabulary,
        MStdExecution(**construction.numerical["execution"]),
        model_size=construction.numerical["model_size"],
    )
    model.enable_rl4_heads(0)
    if (
        model.architecture != construction.architecture
        or input_spec(model.vocabulary, runtime_projection(model.vocabulary))
        != construction.input_spec
    ):
        raise ValueError("checkpoint_model_construction_changed")
    return model
