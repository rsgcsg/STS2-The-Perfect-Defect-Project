"""Deterministic B2-B7 evaluation records and report builders.

These tools calculate evidence; they do not manufacture labels, runtime outcomes, or
scientific claims when source observations are absent.
"""

from __future__ import annotations

import copy
import math
import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

import torch
from torch import Tensor
from torch.nn import functional as F

from ..canonical import semantic_hash
from ..contracts import ContractError, EnvironmentIdentity
from ..representation import ResearchAction, ResearchState


@dataclass(frozen=True)
class GoldAnnotation:
    annotation_id: str
    state_hash: str
    candidate_action_keys: tuple[str, ...]
    candidate_display_order: tuple[str, ...]
    best_action: str
    acceptable_actions: tuple[str, ...]
    confidence: float
    ambiguous: bool
    annotator_id: str
    annotation_version: str
    split: Literal["gold_dev", "gold_test"]

    def validate(self) -> None:
        candidates = set(self.candidate_action_keys)
        if not self.annotation_id or not self.state_hash or not self.annotator_id:
            raise ContractError("Gold annotation identity fields must be non-empty")
        if len(candidates) != len(self.candidate_action_keys) or not candidates:
            raise ContractError("Gold candidates must be non-empty and unique")
        if set(self.candidate_display_order) != candidates:
            raise ContractError("Gold display order must be a permutation of all candidates")
        if self.best_action not in candidates:
            raise ContractError("Gold best action is outside candidates")
        acceptable = set(self.acceptable_actions)
        if self.best_action not in acceptable or not acceptable.issubset(candidates):
            raise ContractError("Gold acceptable actions must include best and stay in catalog")
        if not 0 <= self.confidence <= 1:
            raise ContractError("Gold confidence must be in [0, 1]")


@dataclass(frozen=True)
class GoldReport:
    annotation_count: int
    state_count: int
    double_labeled_states: int
    best_action_agreement: float | None
    acceptable_overlap: float | None
    split_counts: dict[str, int]
    test_sealed: bool


def audit_gold(annotations: Iterable[GoldAnnotation], *, test_sealed: bool) -> GoldReport:
    materialized = list(annotations)
    if not materialized:
        raise ValueError("Gold audit requires annotations")
    by_state: defaultdict[str, list[GoldAnnotation]] = defaultdict(list)
    ids: set[str] = set()
    for annotation in materialized:
        annotation.validate()
        if annotation.annotation_id in ids:
            raise ContractError("duplicate Gold annotation id")
        ids.add(annotation.annotation_id)
        by_state[annotation.state_hash].append(annotation)
    pairs = [values[:2] for values in by_state.values() if len(values) >= 2]
    best_agreement = None
    overlap = None
    if pairs:
        best_agreement = sum(pair[0].best_action == pair[1].best_action for pair in pairs) / len(
            pairs
        )
        overlap = sum(
            len(set(pair[0].acceptable_actions).intersection(pair[1].acceptable_actions))
            / len(set(pair[0].acceptable_actions).union(pair[1].acceptable_actions))
            for pair in pairs
        ) / len(pairs)
    split_counts = Counter(annotation.split for annotation in materialized)
    if split_counts["gold_test"] and not test_sealed:
        raise ContractError("Gold-test annotations require an explicit sealed state")
    return GoldReport(
        len(materialized),
        len(by_state),
        len(pairs),
        best_agreement,
        overlap,
        dict(sorted(split_counts.items())),
        test_sealed,
    )


@dataclass(frozen=True)
class InterventionCase:
    intervention_id: str
    kind: str
    original_state_hash: str
    modified_state: ResearchState
    actions: tuple[ResearchAction, ...]
    changed_paths: tuple[str, ...]


def mask_state_path(
    state: ResearchState,
    actions: Sequence[ResearchAction],
    *,
    path: Sequence[str],
    marker: str = "<MASKED>",
) -> InterventionCase:
    """B3 diagnostic intervention over already player-visible semantic facts."""

    if not path:
        raise ValueError("intervention path must be non-empty")
    facts = copy.deepcopy(dict(state.facts))
    cursor: dict[str, Any] = facts
    for key in path[:-1]:
        value = cursor.get(key)
        if not isinstance(value, dict):
            raise KeyError(".".join(path))
        cursor = value
    final = path[-1]
    if final not in cursor:
        raise KeyError(".".join(path))
    cursor[final] = marker
    modified = replace(state, facts=facts)
    modified.validate()
    rendered_path = ".".join(path)
    return InterventionCase(
        f"mask-{semantic_hash([state.state_hash, rendered_path])[:16]}",
        "state_mask",
        state.state_hash,
        modified,
        tuple(actions),
        (rendered_path,),
    )


def shuffled_action_case(
    state: ResearchState, actions: Sequence[ResearchAction], *, seed: int
) -> InterventionCase:
    """Shuffle presentation order without changing action identity or authority."""

    shuffled = list(actions)
    random.Random(seed).shuffle(shuffled)
    if set(action.action_key for action in shuffled) != set(
        action.action_key for action in actions
    ):
        raise AssertionError("action shuffle changed the candidate set")
    return InterventionCase(
        f"action-shuffle-{semantic_hash([state.state_hash, seed])[:16]}",
        "action_order_shuffle",
        state.state_hash,
        state,
        tuple(shuffled),
        ("candidate_display_order",),
    )


@dataclass(frozen=True)
class RetrievalMetrics:
    count: int
    top1_accuracy: float
    mean_reciprocal_rank: float
    mean_margin: float
    embedding_variance: float


def successor_retrieval(
    predicted: Tensor, candidates: Tensor, target_indices: Tensor
) -> RetrievalMetrics:
    """B4 successor/ASR retrieval with collapse-sensitive variance."""

    if predicted.ndim != 2 or candidates.ndim != 2 or predicted.shape[1] != candidates.shape[1]:
        raise ValueError("retrieval embeddings must be [query/candidate, hidden]")
    if target_indices.shape != (predicted.shape[0],):
        raise ValueError("one retrieval target is required per query")
    similarities = F.normalize(predicted, dim=-1) @ F.normalize(candidates, dim=-1).T
    ranks: list[int] = []
    margins: list[float] = []
    for row, target_tensor in zip(similarities, target_indices, strict=True):
        target = int(target_tensor)
        if not 0 <= target < candidates.shape[0]:
            raise ValueError("retrieval target outside candidates")
        order = torch.argsort(row, descending=True, stable=True)
        rank = int((order == target).nonzero(as_tuple=False)[0]) + 1
        ranks.append(rank)
        negatives = torch.cat((row[:target], row[target + 1 :]))
        margin = float(row[target] - negatives.max()) if negatives.numel() else 0.0
        margins.append(margin)
    return RetrievalMetrics(
        len(ranks),
        sum(rank == 1 for rank in ranks) / len(ranks),
        sum(1.0 / rank for rank in ranks) / len(ranks),
        sum(margins) / len(margins),
        float(candidates.var(dim=0, unbiased=False).mean()),
    )


@dataclass(frozen=True)
class TransferObservation:
    source_kind: str
    source_environment: EnvironmentIdentity
    target_environment: EnvironmentIdentity
    surface: str
    action_kind: str
    metric_name: str
    metric_value: float


def stratify_transfer(observations: Iterable[TransferObservation]) -> dict[str, Any]:
    """B5 keeps historical/current and Managed/Reference transfer partitions distinct."""

    rows = list(observations)
    if not rows:
        raise ValueError("transfer report requires observations")
    groups: defaultdict[str, list[float]] = defaultdict(list)
    for row in rows:
        row.source_environment.validate()
        row.target_environment.validate()
        key = "|".join(
            (
                row.source_kind,
                row.source_environment.host_kind,
                row.target_environment.host_kind,
                row.target_environment.game_version,
                row.surface,
                row.action_kind,
                row.metric_name,
            )
        )
        groups[key].append(row.metric_value)
    return {
        "schema": "stpd/b5-transfer-report-v0",
        "count": len(rows),
        "groups": {
            key: {"count": len(values), "mean": sum(values) / len(values)}
            for key, values in sorted(groups.items())
        },
    }


@dataclass(frozen=True)
class PairedOutcome:
    seed_root: str
    policy_id: str
    completed: bool
    terminal_outcome: str | None
    pipeline_failure: str | None = None
    unknown_delivery: bool = False


def paired_fixed_seed_report(
    outcomes: Iterable[PairedOutcome],
    *,
    required_policies: Sequence[str],
    planned_seeds: Sequence[str],
) -> dict[str, Any]:
    """B6 v1: the declared plan, never received rows, owns the denominator.

    v0 callers must supply their original planned seeds explicitly. Historical
    v0 reports are immutable; no inferred plan can repair missing observations.
    """
    policies, seeds = tuple(required_policies), tuple(planned_seeds)
    for values, name in ((policies, "policies"), (seeds, "seeds")):
        if len(values) != len(set(values)) or any(not isinstance(v, str) or not v for v in values):
            raise ContractError(f"planned {name} must be unique non-empty strings")
    rows = list(outcomes)
    by_seed: dict[str, dict[str, PairedOutcome]] = {seed: {} for seed in seeds}
    for row in rows:
        if row.seed_root not in by_seed or row.policy_id not in policies:
            raise ContractError("outcome outside declared fixed-seed plan")
        if row.policy_id in by_seed[row.seed_root]:
            raise ContractError("duplicate policy outcome for one fixed seed")
        by_seed[row.seed_root][row.policy_id] = row
    incomplete = {
        seed: sorted(set(policies) - set(values))
        for seed, values in by_seed.items()
        if set(values) != set(policies)
    }
    unknown_count = sum(row.unknown_delivery for row in rows)
    pipeline_failures = Counter(
        row.pipeline_failure for row in rows if row.pipeline_failure is not None
    )
    unfinished = sum(not row.completed or row.terminal_outcome is None for row in rows)
    verdict = (
        "pass"
        if (
            seeds
            and policies
            and not incomplete
            and not unknown_count
            and not pipeline_failures
            and not unfinished
        )
        else "fail"
    )
    return {
        "schema": "stpd/b6-fixed-seed-report-v1",
        "verdict": verdict,
        "seed_count": len(seeds),
        "observed_seed_count": sum(bool(v) for v in by_seed.values()),
        "planned_outcome_count": len(seeds) * len(policies),
        "observed_outcome_count": len(rows),
        "required_policies": list(policies),
        "planned_seeds": list(seeds),
        "incomplete": incomplete,
        "unknown_delivery_count": unknown_count,
        "unfinished_outcome_count": unfinished,
        "pipeline_failures": dict(sorted(pipeline_failures.items())),
        "completion_by_policy": {
            policy: sum(row.completed for row in rows if row.policy_id == policy)
            for policy in policies
        },
    }


@dataclass(frozen=True)
class ComputeObservation:
    profile: str
    decision_family: str
    state_tokens: int
    action_tokens: int
    legal_action_count: int
    cold: bool
    forward_ms: float
    end_to_end_ms: float
    peak_vram_bytes: int
    cache_bytes: int


def compute_report(observations: Iterable[ComputeObservation]) -> dict[str, Any]:
    """B7 distribution report; GPU hours and throughput require explicit measured rows."""

    rows = list(observations)
    if not rows:
        raise ValueError("compute report requires observations")

    def percentile(values: list[float], quantile: float) -> float:
        ordered = sorted(values)
        index = math.ceil(quantile * len(ordered)) - 1
        return ordered[max(0, index)]

    groups: defaultdict[str, list[ComputeObservation]] = defaultdict(list)
    for row in rows:
        if min(row.state_tokens, row.action_tokens, row.legal_action_count) < 0:
            raise ValueError("token and action counts must be non-negative")
        groups[f"{row.profile}|{row.decision_family}|{'cold' if row.cold else 'cached'}"].append(
            row
        )
    return {
        "schema": "stpd/b7-compute-report-v0",
        "count": len(rows),
        "groups": {
            key: {
                "count": len(values),
                "state_tokens": {
                    "p50": percentile([float(row.state_tokens) for row in values], 0.5),
                    "p95": percentile([float(row.state_tokens) for row in values], 0.95),
                    "max": max(row.state_tokens for row in values),
                },
                "legal_actions_p95": percentile(
                    [float(row.legal_action_count) for row in values], 0.95
                ),
                "forward_ms_p95": percentile([row.forward_ms for row in values], 0.95),
                "end_to_end_ms_p95": percentile([row.end_to_end_ms for row in values], 0.95),
                "peak_vram_bytes": max(row.peak_vram_bytes for row in values),
                "cache_bytes": max(row.cache_bytes for row in values),
            }
            for key, values in sorted(groups.items())
        },
    }
