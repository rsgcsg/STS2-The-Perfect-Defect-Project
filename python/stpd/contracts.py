"""Stable insertion points for STPD's external and cross-layer contracts.

The current smoke lane does not depend on these protocols yet.  They define the
minimum boundaries that future data, Qwen, model, training, and evaluation code
must preserve without importing Headless or Connector implementation details.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    """Raised when a versioned STPD identity or interface value is incomplete."""


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be a non-empty string")


def _require_sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value.lower()) is None:
        raise ContractError(f"{name} must be a 64-character SHA-256 digest")


def _require_mvid(name: str, value: str) -> None:
    try:
        UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ContractError(f"{name} must be a UUID-form module MVID") from exc


@dataclass(frozen=True)
class EnvironmentIdentity:
    """Exact game, Host, Player Environment implementation, and policy identity."""

    game_version: str
    game_commit: str
    game_artifact_sha256: str
    game_artifact_mvid: str
    host_kind: str
    host_source_revision: str
    host_source_digest_sha256: str
    host_artifact_sha256: str
    host_artifact_mvid: str
    player_environment_protocol: str
    player_environment_implementation: str
    player_environment_revision: str
    player_environment_digest_sha256: str
    information_policy_id: str

    def validate(self) -> None:
        for name, value in (
            ("game_version", self.game_version),
            ("game_commit", self.game_commit),
            ("host_kind", self.host_kind),
            ("host_source_revision", self.host_source_revision),
            ("player_environment_protocol", self.player_environment_protocol),
            ("player_environment_implementation", self.player_environment_implementation),
            ("player_environment_revision", self.player_environment_revision),
            ("information_policy_id", self.information_policy_id),
        ):
            _require_text(name, value)
        for name, value in (
            ("game_artifact_sha256", self.game_artifact_sha256),
            ("host_source_digest_sha256", self.host_source_digest_sha256),
            ("host_artifact_sha256", self.host_artifact_sha256),
            ("player_environment_digest_sha256", self.player_environment_digest_sha256),
        ):
            _require_sha256(name, value)
        _require_mvid("game_artifact_mvid", self.game_artifact_mvid)
        _require_mvid("host_artifact_mvid", self.host_artifact_mvid)


@dataclass(frozen=True)
class TransitionEligibility:
    """Independent permissions for ranking, dynamics, and return supervision."""

    rank: bool
    rank_mode: Literal["full_listwise", "partial_pairwise", "chosen_only", "none"]
    transition: bool
    return_: bool
    legal_action_completeness: Literal["complete", "partial", "unknown"]
    reason_codes: tuple[str, ...] = ()

    def validate(self) -> None:
        if not (self.rank or self.transition or self.return_):
            raise ContractError("a transition must be eligible for at least one use")
        if self.rank == (self.rank_mode == "none"):
            raise ContractError("rank and rank_mode disagree")
        if self.rank_mode == "full_listwise" and self.legal_action_completeness != "complete":
            raise ContractError("full_listwise ranking requires a complete legal action catalog")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "rank_mode": self.rank_mode,
            "transition": self.transition,
            "return": self.return_,
            "legal_action_completeness": self.legal_action_completeness,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class QwenIdentity:
    """Pinned encoder identity required by a reproducible v0 run."""

    model_id: str
    model_revision: str
    tokenizer_revision: str
    dtype: str
    device: str
    frozen: bool
    control: str = "unspecified"
    config_sha256: str = "unspecified"
    tokenizer_sha256: str = "unspecified"
    weights_sha256: str | None = None
    random_seed: int | None = None
    initialization_sha256: str | None = None
    attention_implementation: str = "unspecified"
    feature_dtype: str = "unspecified"
    cache_mode: str = "none"
    torch_version: str = "unspecified"
    transformers_version: str = "unspecified"

    def validate(self) -> None:
        for name, value in (
            ("model_id", self.model_id),
            ("model_revision", self.model_revision),
            ("tokenizer_revision", self.tokenizer_revision),
            ("dtype", self.dtype),
            ("device", self.device),
            ("control", self.control),
            ("config_sha256", self.config_sha256),
            ("tokenizer_sha256", self.tokenizer_sha256),
            ("attention_implementation", self.attention_implementation),
            ("feature_dtype", self.feature_dtype),
            ("cache_mode", self.cache_mode),
            ("torch_version", self.torch_version),
            ("transformers_version", self.transformers_version),
        ):
            _require_text(name, value)

    def validate_v0(self) -> None:
        self.validate()
        if not self.frozen:
            raise ContractError("the v0 core Qwen backbone must be frozen")

    def validate_scientific_v0(self) -> None:
        """Fail closed on the stronger identity needed by pretrained/random experiments."""

        self.validate_v0()
        if self.model_id != "Qwen/Qwen3-0.6B-Base":
            raise ContractError("scientific v0 requires Qwen/Qwen3-0.6B-Base")
        if not re.fullmatch(r"[0-9a-f]{40}", self.model_revision):
            raise ContractError("scientific Qwen model_revision must be an immutable Git SHA")
        if self.tokenizer_revision != self.model_revision:
            raise ContractError("model and tokenizer revisions must be identical")
        if self.dtype != "bfloat16" or not self.device.startswith("cuda:"):
            raise ContractError("scientific v0 requires a CUDA bfloat16 backbone")
        _require_sha256("config_sha256", self.config_sha256)
        _require_sha256("tokenizer_sha256", self.tokenizer_sha256)
        if self.control == "pretrained":
            if self.weights_sha256 is None:
                raise ContractError("pretrained Qwen requires a pinned weight digest")
            _require_sha256("weights_sha256", self.weights_sha256)
            if self.random_seed is not None:
                raise ContractError("pretrained Qwen cannot carry a random initialization seed")
            if self.initialization_sha256 is not None:
                raise ContractError("pretrained Qwen cannot carry a random initialization digest")
        elif self.control == "random":
            if self.weights_sha256 is not None or self.random_seed is None:
                raise ContractError("random Qwen requires a seed and no pretrained weight identity")
            if self.initialization_sha256 is None:
                raise ContractError("random Qwen requires an exact initialization digest")
            _require_sha256("initialization_sha256", self.initialization_sha256)
        else:
            raise ContractError("scientific Qwen control must be pretrained or random")


@runtime_checkable
class PlayerEnvironmentPort(Protocol):
    """Strategy-free public Player Environment consumed by STPD."""

    @property
    def ready(self) -> Mapping[str, Any]: ...

    def reset(self, seed: str) -> Mapping[str, Any]: ...

    def observe(self) -> Mapping[str, Any]: ...

    def read(self, read_id: str, snapshot_id: str) -> Mapping[str, Any]: ...

    def step(
        self,
        bound_action_id: str,
        snapshot_id: str,
        mutation_request_id: str | None = None,
    ) -> Mapping[str, Any]: ...

    def close(self) -> None: ...


@runtime_checkable
class ResearchProjector(Protocol):
    """Converts coherent public environment facts into research objects."""

    def project_state(
        self,
        snapshot: Mapping[str, Any],
        reads: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]: ...

    def project_actions(self, snapshot: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]: ...


@runtime_checkable
class ModelSerializer(Protocol):
    """Deterministic, versioned ResearchState/Action serialization."""

    profile_id: str

    def serialize_state(self, research_state: Mapping[str, Any]) -> str: ...

    def serialize_action(self, research_action: Mapping[str, Any]) -> str: ...


@runtime_checkable
class QwenBackend(Protocol):
    """Frozen encoder operations used by the v0 model families."""

    identity: QwenIdentity

    def encode_joint(
        self,
        state_texts: Sequence[str],
        action_texts: Sequence[str],
    ) -> Any: ...

    def encode_state(self, state_texts: Sequence[str], *, return_sequence: bool) -> Any: ...

    def embed_action_tokens(self, action_texts: Sequence[str]) -> Any: ...


@runtime_checkable
class ActionScorer(Protocol):
    """Returns one scalar score for each current legal candidate."""

    def score(self, model_state: str, model_actions: Sequence[str]) -> Sequence[float]: ...


def ensure_score_alignment(scores: Sequence[float], actions: Sequence[Any]) -> None:
    """Fail when a scorer invents, drops, or reorders the candidate count."""

    if len(scores) != len(actions):
        raise ContractError(
            f"score/action count mismatch: {len(scores)} scores for {len(actions)} actions"
        )
    if not actions:
        raise ContractError("cannot score an empty legal action set")
