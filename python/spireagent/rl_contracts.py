"""RL4.1 internal handoff types; existing wire readers remain unchanged.

W1/W2/W3 import these definitions rather than copying message shapes. Public
schema numbers are assigned in VERSION-SLOT before the implementation emits
new wire records. JSON objects below reuse the existing public-input, compiler,
adapter, original-commit and typed-Evidence records; they confer no authority.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, Protocol, TypedDict

from spireagent.json_boundary import BoundaryError, json_bytes

RL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def exchange_prefix(run: str) -> str:
    """The existing immutable run namespace, without constructing an Exchange."""
    if not RL_NAME.fullmatch(run):
        raise BoundaryError("rl_exchange", "invalid_run_name")
    return f"exchange/rl/{run}/"

JsonObject = dict[str, Any]


# W1 owns learning generations; W2/W3 carry this identity without renumbering it.
@dataclass(frozen=True)
class RunSequence:
    run: str
    training_generation: str  # fresh on restore; display integers may repeat across generations
    sequence: int


# W1 publishes completed immutable full models; W2 loads, W3 transports via Exchange.
class MuIdentity(TypedDict):
    run: str
    training_generation: str
    version: int
    updates: int
    model_artifact_id: str
    model_sha256: str
    heads_artifact_id: str
    heads_sha256: str
    snapshot_sha256: str
    # W1 computes once from text_ids/text_numbers/text_blocks/text_projection state;
    # W2 uses the published digest, rather than defining a different text subset.
    text_digest: str
    vocabulary_sha256: str
    input_spec: JsonObject
    compiler_identity: JsonObject
    adapter: JsonObject
    projection: JsonObject
    precision: str
    tf32: bool
    execution: JsonObject
    deduplicate_text: bool


class MuPublication(TypedDict):
    identity: MuIdentity
    parent: RunSequence | None
    published_at: float
    trajectory_encoding: str


# W2 owns cache identity; training text remains trainable, actor mu is immutable.
@dataclass(frozen=True)
class TextCacheNamespace:
    text_digest: str
    vocabulary_sha256: str
    tokenizer_sha256: str
    encoder_semantics: str
    precision: str
    tf32: bool
    execution_digest: str


@dataclass(frozen=True)
class MuScore:
    snapshot_sha256: str
    scores: tuple[float, ...] | None  # complete catalog order when policy=True
    outcome_probabilities: tuple[float, ...]  # 51 classes, including forced rows


# W2 implements; W3 retains at begin, scores forced/tail with that mu, releases after commit.
class LocalMuScorer(Protocol):
    def retain_mu(self, snapshot_sha256: str) -> None: ...

    def score_compiled(self, snapshot_sha256: str, frame_wire: JsonObject,
                       floor: int, *, policy: bool) -> MuScore: ...

    def release_mu(self, snapshot_sha256: str) -> None: ...


# W3 produces hot data, W2 supplies every behavior value, W1 consumes it once ready.
class Behavior(TypedDict):
    mu: MuIdentity
    lease: str
    acquisition_id: str
    input_sha256: str
    observation_sha256: str
    catalog_sha256: str
    consumption_unit: str
    compiled_input: JsonObject
    scores: list[float]
    floor: int
    outcome_probabilities: list[float]
    compile_seconds: float
    compiled_wire_bytes: int
    request_seconds: float
    batch_size: int
    queue_seconds: float
    server_seconds: float
    network_request: bool
    clock_offset: dict[str, float] | None
    score_source: str


class HotStep(TypedDict):
    schema: Literal["sts2.policy-runtime/verified-global-transition-1"]
    run_id: str
    step_index: int
    input: JsonObject  # unchanged public observation + complete native candidate catalog
    directive: JsonObject
    result: JsonObject  # existing verified execution result/receipt, status=completed
    successor: JsonObject  # confirmed successor, never the next unexecuted action
    sampling: JsonObject
    behavior: Behavior


class Bootstrap(TypedDict):
    mu_snapshot_sha256: str
    successor_input_sha256: str
    floor: int  # max historical public floor and confirmed successor public floor
    outcome_probabilities: list[float]


# W3 emits only after original Evidence verifies terminal class/reward/content binding.
TerminalLabel = TypedDict("TerminalLabel", {
    "class": int,
    "reward": float,
    "typed_original": str,  # existing Evidence content_id; never an observer verdict
})


class RowRef(TypedDict):
    run_id: str
    segment_id: str
    training_generation: str
    row: int
    key: str


class OutcomeRow(TypedDict):
    source: RowRef
    outcome: int


class HpRow(TypedDict):
    source: RowRef
    hp: float  # public absolute post-combat HP / 100, not max-HP fraction


class HpBattle(TypedDict):
    battle_id: str
    states: list[HpRow]
    post_hp: float  # raw public HP; each HpRow.hp is post_hp / 100
    start_step: NotRequired[int]
    end_step: NotRequired[int]
    boundary_acquisition_id: NotRequired[str]
    boundary_step_index: NotRequired[int]
    boundary_input_sha256: NotRequired[str]  # confirmed public reward successor
    post_observation_index: NotRequired[int]  # typed terminal variant
    public_native_kind: NotRequired[str | None]


# W3 owns delayed labels; W1 owns aux selection/use counters. No PPO first-use replay.
class EpisodeLabels(TypedDict):
    run: str
    training_generation: str
    actor: str
    episode: str
    run_id: str
    terminal: TerminalLabel | None
    outcome_states: list[OutcomeRow]
    hp_battles: list[HpBattle]
    unqualified: bool  # unknown/technical exit produces no invented terminal label


class HotSegment(TypedDict):
    training_split: Literal["TRAIN"]
    run: str
    training_generation: str
    identity: JsonObject
    run_id: str
    rows: int
    segment_id: str
    mu: MuIdentity
    steps: list[HotStep]
    terminal: TerminalLabel | None
    bootstrap: Bootstrap | None  # required iff nonterminal; no theta-side fill-in
    hp_battles: list[HpBattle]
    original_commit: JsonObject
    previous_segment_artifact_id: str | None  # lineage only, no recursive materialization
    compiler_identity: JsonObject
    compiled_boundary: JsonObject | None


# W3 owns bounded Exchange slot bytes. An ACK is recorded, NOT ready/trained/durable CP.
@dataclass(frozen=True)
class HotPayloadRef:
    slot: str  # opaque existing Exchange-owned slot, not an additional Store
    sha256: str
    size: int


class ControlBase(TypedDict):
    run: str
    training_generation: str
    request_key: str  # control_request_key below; body mismatch under the same key is rejected


class EpisodeOpen(ControlBase):
    operation: Literal["episode_open"]
    actor: str
    episode: str


class Begin(ControlBase):
    operation: Literal["begin"]
    actor: str
    episode: str
    previous_lease: str | None


class Commit(ControlBase):
    operation: Literal["commit"]
    segment_id: str
    run_id: str
    rows: int
    mu: MuIdentity
    payload: HotPayloadRef


class EpisodeEnd(ControlBase):
    operation: Literal["episode_end"]
    labels: EpisodeLabels


ControlInput = EpisodeOpen | Begin | Commit | EpisodeEnd


# W3 Exchange delegates new-envelope keys here; W1/W2 use the same original key.
def control_request_key(request: Mapping[str, Any]) -> str:
    """Original operation identity scoped by run/generation, never connection/clock.

    The existing Exchange owner must still compare the complete original body
    before returning a duplicate receipt. This key does not authorize a resend.
    """
    operation = request["operation"]
    if operation == "commit":
        identity = [request["segment_id"]]
    elif operation == "episode_end":
        identity = [request["labels"]["actor"], request["labels"]["episode"]]
    elif operation in {"episode_open", "begin"}:
        identity = [request["actor"], request["episode"]]
        if operation == "begin":
            identity.append(request["previous_lease"])
    else:
        raise ValueError("unsupported_rl_control_operation")
    return hashlib.sha256(json_bytes(
        [request["run"], request["training_generation"], operation, *identity])).hexdigest()


# W1 grants capacity and latest completed mu only at begin; W2 adopts at that boundary.
@dataclass(frozen=True)
class SegmentGrant:
    identity: RunSequence
    lease: str
    mu: MuIdentity
    server_time: float


@dataclass(frozen=True)
class EpisodeAdmission:
    admitted: bool


@dataclass(frozen=True)
class SegmentCommitted:
    segment_id: str  # acceptance ACK only, not ready or successful training


@dataclass(frozen=True)
class EpisodeEnded:
    run_id: str


@dataclass(frozen=True)
class ControlUnavailable:
    reason: Literal["stopped", "generation_changed"]


ControlResponse = (SegmentGrant | EpisodeAdmission | SegmentCommitted | EpisodeEnded
                   | ControlUnavailable)


# W1 owns stage transitions; W3 stores original ACK and appends later dispositions.
@dataclass(frozen=True)
class InputReceipt:
    request_key: str
    identity: RunSequence
    payload_sha256: str | None
    recorded_at: float
    operation: Literal["episode_open", "begin", "commit", "episode_end"]
    response: ControlResponse  # original exact response, including begin's lease + mu
    stage: Literal["recorded"] = "recorded"


@dataclass(frozen=True)
class InputDisposition:
    request_key: str
    identity: RunSequence
    stage: Literal["ready", "used", "rejected", "discarded_on_restore"]
    reason: str | None
    # ready: read/verify/targets finished; used: successful Adam step only.
    # Restore appends discarded_on_restore, never rewrites the original ACK.


# W2 reconnects control at segment boundary; credentials remain environment-only.
@dataclass(frozen=True)
class LearnerRendezvous:
    run: str
    training_generation: str
    instance: str
    endpoint: str
    input_spec: JsonObject
    adapter: JsonObject


class ControlClient(Protocol):
    def control(self, request: ControlInput) -> ControlResponse: ...

    def receipt(self, request_key: str, *, training_generation: str) -> InputReceipt | None: ...

    def status(self) -> JsonObject: ...
