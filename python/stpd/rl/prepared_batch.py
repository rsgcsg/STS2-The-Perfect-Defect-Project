"""W1-owned CPU preparation -> learner boundary, with no learned-theta outputs.

The learner alone selects members/aux uses and RNG order. CPU workers return a
complete batch; metadata may describe buckets/joint tensors but never cached
trainable text embeddings. Different batches/precision need not be equivalent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from torch import Tensor

from spireagent.rl_contracts import HotPayloadRef, RowRef, RunSequence
from stpd.p1.tensor_input import JointNodes, TensorFrame


# W1 owns all selection, preparation and successful-use accounting.
@dataclass(frozen=True)
class BatchMember:
    segment_id: str
    payload: HotPayloadRef
    run_id: str
    rows: int
    mu_snapshot_sha256: str


@dataclass(frozen=True)
class AuxiliarySelection:
    source: RowRef
    kind: Literal["terminal", "hp"]
    label: float
    uses_before: int
    use_limit: int  # terminal 2, HP 1; reserve until successful update


@dataclass(frozen=True)
class PrepareRequest:
    identity: RunSequence  # sequence is dataset number
    members: tuple[BatchMember, ...]
    auxiliary: tuple[AuxiliarySelection, ...]  # <=128 terminal + <=128 HP
    minibatch_rows: tuple[tuple[int, ...], ...]  # four exhaustive disjoint PPO partitions
    auxiliary_minibatches: tuple[int, ...]  # one minibatch index per selected aux row
    compiler_identity: dict[str, Any]
    selection_rng_state: tuple[Any, ...]
    max_output_bytes: int  # current + next bounded by original learner owner


@dataclass(frozen=True)
class AdvantageNormalization:
    multi_rows: int
    mean: float
    scale: float  # dataset-wide multi-choice population std, floor 1e-8


@dataclass(frozen=True)
class PreparedGroup:
    rows: tuple[int, ...]  # indices into PreparedBatch.frames (PPO first, aux after)
    policy: bool
    joint: JointNodes


@dataclass(frozen=True)
class PreparedMinibatch:
    groups: tuple[PreparedGroup, ...]
    policy_rows: int
    td_rows: int
    terminal_rows: int
    hp_rows: int  # denominators across groups, not per microbatch


@dataclass(frozen=True)
class PreparedBatch:
    request: PrepareRequest  # exact complete members and aux reservations
    row_refs: tuple[RowRef, ...]
    frames: tuple[TensorFrame, ...]  # parameter-free CPU tensors, all PPO + aux rows
    kinds: tuple[Literal["ppo", "terminal", "hp"], ...]
    floors: Tensor  # [N] int64
    actions: Tensor  # [N] int64; -1 for aux
    catalog_offsets: Tensor  # [N+1] int64 into flat old probabilities
    behavior_probabilities: Tensor  # [sum catalog lengths], exact recorded mu policy
    behavior_outcomes: Tensor  # [N,51]; zero outside PPO
    outcome_targets: Tensor  # [N,51]; lambda PPO / one-hot terminal, zero for HP
    hp_targets: Tensor  # [N]; public post-combat HP / 100, zero outside HP
    advantages: Tensor  # [N]; normalized across dataset multi rows; zero forced/aux
    policy_mask: Tensor  # [N] bool: only PPO multi-choice
    td_mask: Tensor  # [N] bool: every PPO row, including forced
    terminal_mask: Tensor  # [N] bool
    hp_mask: Tensor  # [N] bool
    normalization: AdvantageNormalization
    minibatches: tuple[PreparedMinibatch, ...]
    prepared_at: float
    payload_bytes: int

    # A returned batch is ready only after whole-request read/verification/targets.
    # No worker owns theta, Adam, EMA, successful-use counters or model publication.
