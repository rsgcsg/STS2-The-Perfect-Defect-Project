"""W1-owned CPU preparation -> learner boundary, with no learned-theta outputs.

The learner alone selects members/aux uses and RNG order. CPU workers return a
complete batch; metadata may describe buckets/joint tensors but never cached
trainable text embeddings. Different batches/precision need not be equivalent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from torch import Tensor

from spireagent.rl_contracts import HotPayloadRef, RowRef, RunSequence
from spireagent.rl_dataplane_ports import BinaryRef
from stpd.p1.tensor_input import JointNodes, TensorFrame


# W1 owns all selection, preparation and successful-use accounting.
@dataclass(frozen=True)
class BatchMember:
    segment_id: str
    payload: HotPayloadRef | BinaryRef
    run_id: str
    rows: int
    mu_snapshot_sha256: str
    episode: str  # exact granted original intent, not inferred from native run_id


@dataclass(frozen=True)
class AuxiliarySelection:
    source: RowRef
    kind: Literal["terminal", "hp"]
    label: float
    uses_before: int
    use_limit: int  # configured total successful uses; admission does not charge a use


@dataclass(frozen=True)
class AuxiliaryInput:
    """Verified parameter-free row bytes retained by the existing aux pool.

    Created after normal source/label verification, never from theta or a new
    Store. Pool retirement releases these bytes and its original hot reference.
    """

    source: RowRef
    member: BatchMember
    body: bytes
    # Binary snapshots carry the already verified packet envelope so selection
    # need not parse a large descriptor again on the learner thread. Historical
    # JSON snapshots leave this unset; reader choice is still explicit by ref.
    envelope: dict[str, Any] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class AuxiliaryFrame:
    source: RowRef
    frame: TensorFrame
    floor: int


@dataclass(frozen=True)
class PrepareRequest:
    identity: RunSequence  # sequence is dataset number
    members: tuple[BatchMember, ...]
    auxiliary: tuple[AuxiliarySelection, ...]  # configured per-head selection
    minibatch_rows: tuple[tuple[int, ...], ...]  # exhaustive disjoint PPO partitions
    auxiliary_minibatches: tuple[int, ...]  # one minibatch index per selected aux row
    compiler_identity: dict[str, Any]
    selection_rng_state: tuple[Any, ...]
    max_output_bytes: int  # current + next bounded by original learner owner
    epochs: int = 1


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
    # All padding, text deduplication, scene/role gathers and masks are built on
    # CPU. Never contains text embeddings or any other theta-dependent value.
    tensors: dict[str, Tensor] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedMinibatch:
    groups: tuple[PreparedGroup, ...]
    policy_rows: int
    td_rows: int
    terminal_rows: int
    hp_rows: int  # denominators across groups, not per microbatch
    tensors: dict[str, Tensor] = field(default_factory=dict)
    # Only multi-epoch requests need new masks: exhausted aux rows stop learning
    # while PPO rows retain their frozen targets and dataset normalization.
    epoch_auxiliary: tuple[dict[str, Tensor], ...] = ()
    epoch_auxiliary_counts: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class FrameLayout:
    choices: tuple[str, ...]
    entity_count: int
    text_count: int
    text_width: int


@dataclass(frozen=True)
class PreparedBatch:
    request: PrepareRequest  # exact complete members and aux reservations
    row_refs: tuple[RowRef, ...]
    # Logical rows only. The complete forward tensors live once in groups;
    # retaining the original TensorFrames too doubled IPC bytes for no consumer.
    frames: tuple[FrameLayout, ...]
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
    metadata: tuple[dict[str, Any], ...] = ()
    timings: dict[str, float] = field(default_factory=dict)
    worker_pids: tuple[int, ...] = ()
    label_updates: tuple[dict[str, Any], ...] = ()  # labels verified by the W3 source reader

    # A returned batch is ready only after whole-request read/verification/targets.
    # No worker owns theta, Adam, EMA, successful-use counters or model publication.
