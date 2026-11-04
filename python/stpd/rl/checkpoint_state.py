"""W1-owned self-contained learning state and explicit legacy CP16 conversion seam.

Serialize with the existing tensor-tree codec, not pickle. Construction, weights
(including all heads/text), Adam, weight EMA, counters and RNG are embedded.
Provenance references are descriptive, never prerequisites for restoring learning.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from torch import Tensor

from spireagent.rl_contracts import RunSequence


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
    raise NotImplementedError("RL4.1 W1 implements CP16 migration; F0 defines the boundary")
