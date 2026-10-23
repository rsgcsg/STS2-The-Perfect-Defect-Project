"""Independent behavior-ranking evaluation.

Seed planning remains available without loading optional model implementations.
Public evaluation exports load when their consumers explicitly access them.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .gates import (
        ComputeObservation,
        GoldAnnotation,
        GoldReport,
        InterventionCase,
        PairedOutcome,
        RetrievalMetrics,
        TransferObservation,
        audit_gold,
        compute_report,
        mask_state_path,
        paired_fixed_seed_report,
        shuffled_action_case,
        stratify_transfer,
        successor_retrieval,
    )
    from .ranking import RankingMetrics, evaluate_ranking

__all__ = [
    "ComputeObservation",
    "GoldAnnotation",
    "GoldReport",
    "InterventionCase",
    "PairedOutcome",
    "RankingMetrics",
    "RetrievalMetrics",
    "TransferObservation",
    "audit_gold",
    "compute_report",
    "evaluate_ranking",
    "mask_state_path",
    "paired_fixed_seed_report",
    "shuffled_action_case",
    "stratify_transfer",
    "successor_retrieval",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = "ranking" if name in {"RankingMetrics", "evaluate_ranking"} else "gates"
    value = getattr(import_module("." + module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
