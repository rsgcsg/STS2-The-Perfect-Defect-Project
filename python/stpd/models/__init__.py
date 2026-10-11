"""Candidate-aligned STPD v0 model families.

Exports load on first access so importing a specialized submodule does not load
unrelated model or training implementations.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .batches import DynamicsBatch, RankBatch
    from .losses import anchor_loss, listwise_rank_loss, normalized_successor_loss
    from .objectives import (
        ObjectiveResult,
        s2_sdt_objective,
        s2_simple_objective,
        scheme1_objective,
    )
    from .s2_sdt import S2SDTOutput, S2SDTScorer
    from .s2_simple import S2SimpleOutput, S2SimpleScorer
    from .scheme1 import Scheme1Scorer

__all__ = [
    "DynamicsBatch",
    "ObjectiveResult",
    "RankBatch",
    "S2SDTOutput",
    "S2SDTScorer",
    "S2SimpleOutput",
    "S2SimpleScorer",
    "Scheme1Scorer",
    "anchor_loss",
    "listwise_rank_loss",
    "normalized_successor_loss",
    "s2_sdt_objective",
    "s2_simple_objective",
    "scheme1_objective",
]

_EXPORT_MODULES = {
    "DynamicsBatch": "batches",
    "RankBatch": "batches",
    "anchor_loss": "losses",
    "listwise_rank_loss": "losses",
    "normalized_successor_loss": "losses",
    "ObjectiveResult": "objectives",
    "s2_sdt_objective": "objectives",
    "s2_simple_objective": "objectives",
    "scheme1_objective": "objectives",
    "S2SDTOutput": "s2_sdt",
    "S2SDTScorer": "s2_sdt",
    "S2SimpleOutput": "s2_simple",
    "S2SimpleScorer": "s2_simple",
    "Scheme1Scorer": "scheme1",
}


def __getattr__(name: str) -> Any:
    module = _EXPORT_MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("." + module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
