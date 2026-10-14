from __future__ import annotations

import pytest
import torch

from stpd.contracts import ContractError, EnvironmentIdentity
from stpd.evaluation import (
    ComputeObservation,
    GoldAnnotation,
    PairedOutcome,
    TransferObservation,
    audit_gold,
    compute_report,
    mask_state_path,
    paired_fixed_seed_report,
    shuffled_action_case,
    stratify_transfer,
    successor_retrieval,
)
from stpd.representation import DecisionFamily, ResearchAction, ResearchState


def _state() -> ResearchState:
    return ResearchState(
        "player_visible_v1",
        "v0.111.0",
        "41cef1ea",
        "combat",
        DecisionFamily.TURN_ACTION,
        "combat_turn",
        {"combat": {"energy": 3}, "player_status": {"hp": 80, "block": 0}},
    )


def _actions() -> tuple[ResearchAction, ...]:
    return (
        ResearchAction("play-a", "play_card", {"local_ref": "H0", "name": "Strike"}),
        ResearchAction("end", "end_turn"),
    )


def _environment(host_kind: str, version: str = "v0.111.0") -> EnvironmentIdentity:
    return EnvironmentIdentity(
        version,
        "41cef1ea",
        "c" * 64,
        "11111111-1111-4111-8111-111111111111",
        host_kind,
        "host-source",
        "d" * 64,
        "a" * 64,
        "22222222-2222-4222-8222-222222222222",
        "1.0.0",
        "sts2_headless_managed_adapter",
        "host-source",
        "d" * 64,
        "player_visible_v1",
    )


def test_gold_audit_requires_complete_randomized_catalog_and_sealed_test() -> None:
    first = GoldAnnotation(
        "a1",
        "state",
        ("play-a", "end"),
        ("end", "play-a"),
        "play-a",
        ("play-a",),
        0.9,
        False,
        "human-1",
        "v1",
        "gold_test",
    )
    second = GoldAnnotation(
        "a2",
        "state",
        ("play-a", "end"),
        ("play-a", "end"),
        "play-a",
        ("play-a", "end"),
        0.7,
        True,
        "human-2",
        "v1",
        "gold_test",
    )
    with pytest.raises(ContractError, match="sealed"):
        audit_gold([first, second], test_sealed=False)
    report = audit_gold([first, second], test_sealed=True)
    assert report.best_action_agreement == 1.0
    assert report.double_labeled_states == 1


def test_b3_interventions_change_only_declared_projection() -> None:
    state = _state()
    masked = mask_state_path(state, _actions(), path=("combat", "energy"))
    assert masked.original_state_hash == state.state_hash
    assert masked.modified_state.facts["combat"]["energy"] == "<MASKED>"
    shuffled = shuffled_action_case(state, _actions(), seed=7)
    assert {item.action_key for item in shuffled.actions} == {"play-a", "end"}


def test_b4_retrieval_reports_rank_margin_and_collapse_signal() -> None:
    candidates = torch.tensor([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]])
    predicted = torch.tensor([[0.9, 0.1], [0.1, 0.8]])
    metrics = successor_retrieval(predicted, candidates, torch.tensor([0, 1]))
    assert metrics.top1_accuracy == 1.0
    assert metrics.mean_margin > 0 and metrics.embedding_variance > 0


def test_b5_keeps_host_version_surface_and_action_strata_separate() -> None:
    report = stratify_transfer(
        [
            TransferObservation(
                "current_teacher",
                _environment("managed_exact"),
                _environment("live_ui"),
                "combat_turn",
                "play_card",
                "top1",
                1.0,
            ),
            TransferObservation(
                "historical",
                _environment("managed_exact", "v0.110.0"),
                _environment("managed_exact"),
                "combat_turn",
                "end_turn",
                "top1",
                0.0,
            ),
        ]
    )
    assert report["count"] == 2 and len(report["groups"]) == 2


def test_b6_never_imputes_missing_policy_or_unknown_delivery() -> None:
    report = paired_fixed_seed_report(
        [
            PairedOutcome("seed-1", "teacher", True, "victory"),
            PairedOutcome("seed-1", "scheme1", False, None, unknown_delivery=True),
        ],
        required_policies=("teacher", "scheme1", "scheme2"),
        planned_seeds=("seed-1",),
    )
    assert report["verdict"] == "fail"
    assert report["unknown_delivery_count"] == 1
    assert report["incomplete"] == {"seed-1": ["scheme2"]}


def test_b7_separates_cold_and_cached_distributions() -> None:
    rows = [
        ComputeObservation("standard", "turn_action", 100, 20, 3, True, 10, 12, 1000, 0),
        ComputeObservation("standard", "turn_action", 120, 25, 4, False, 2, 3, 900, 50),
    ]
    report = compute_report(rows)
    assert report["count"] == 2 and len(report["groups"]) == 2
