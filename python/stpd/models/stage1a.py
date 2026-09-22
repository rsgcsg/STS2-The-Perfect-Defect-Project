"""Versioned Stage 1a model graphs; no data access, native execution or tokenizer fitting.

Callers retain complete candidate bindings and use the same versioned tokenization
for training and serving. The input contains current observation and candidates only.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from .token_core import TokenCore


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    graph: str
    family: Literal["b", "dsimple"]
    backbone: Literal["s", "pf"]
    objective: str = "rank_n"
    memory: str = "m0"


_FAMILIES: tuple[Literal["b", "dsimple"], ...] = ("b", "dsimple")
_BACKBONES: tuple[Literal["s", "pf"], ...] = ("s", "pf")

RECIPES = MappingProxyType({**{
    f"stage1a.{family}.{backbone}.v1": Recipe(
        f"stage1a.{family}.{backbone}.v1",
        "b.single-stream.v1" if family == "b" else "dsimple.vector.v1",
        family, backbone,
    )
    for family in _FAMILIES
    for backbone in _BACKBONES
}, **{
    f"stage1a.b.{backbone}.v2": Recipe(
        f"stage1a.b.{backbone}.v2", "b.shared-observation.v2", "b", backbone,
    ) for backbone in _BACKBONES
}})


def recipe_for(recipe_id: str) -> Recipe:
    try:
        return RECIPES[recipe_id]
    except KeyError as exc:
        raise ValueError("unsupported stage1a recipe") from exc


def validate_catalog(core: TokenCore, state: Tensor, actions: tuple[Tensor, ...]) -> None:
    core.validate_tokens(state)
    if not actions:
        raise ValueError("complete candidate catalog must be nonempty")
    for action in actions:
        core.validate_tokens(action)
        if action.device != state.device:
            raise ValueError("state and action token devices differ")


class BTokenScorer(nn.Module):
    """One core and head; v2 packs the shared observation, v1 retains isolated calls."""

    def __init__(self, core: TokenCore, *, readout_initial: Tensor | None = None,
                 packed: bool = False) -> None:
        super().__init__()
        self.core = core
        self.packed = packed
        if readout_initial is None:
            if core.frozen:
                raise ValueError("frozen B requires the pinned EOS readout initializer")
            readout_initial = torch.randn(core.width) * 0.02
        if (
            readout_initial.shape != (core.width,)
            or not bool(torch.isfinite(readout_initial).all())
        ):
            raise ValueError("invalid B readout initializer")
        self.readout = nn.Parameter(readout_initial.detach().clone())
        self.head = nn.Linear(core.width, 1)

    def forward(self, state: Tensor, actions: tuple[Tensor, ...]) -> Tensor:
        validate_catalog(self.core, state, actions)
        # Preflight every branch before scoring any; never silently drop a long candidate.
        if any(state.numel() + a.numel() + 1 > self.core.max_tokens for a in actions):
            raise ValueError("joint input token limit exceeded; truncation is forbidden")
        if self.packed:
            hidden = self.core.read_action_queries(state, actions, self.readout)
            return self.head(hidden).flatten()  # type: ignore[no-any-return]
        scores = []
        for action in actions:
            tokens = torch.cat((state, action))
            # Frozen parameter weights do NOT remove the gradient path to readout.
            hidden = self.core.read_last_query(tokens, self.readout)
            scores.append(self.head(hidden).reshape(()))
        return torch.stack(scores)


class DSimpleTokenScorer(nn.Module):
    """Shared current vector and per-action vector update; no successor input."""

    def __init__(self, core: TokenCore) -> None:
        super().__init__()
        self.core = core
        width = core.width
        self.transition = nn.Sequential(
            nn.Linear(2 * width, width), nn.GELU(), nn.Linear(width, width),
        )
        self.head = nn.Sequential(nn.Linear(width, 256), nn.GELU(), nn.Linear(256, 1))

    def encode(self, tokens: Tensor) -> Tensor:
        embedded = self.core.embed_tokens(tokens)
        hidden = self.core.contextualize(
            embedded, causal=not self.core.supports_bidirectional,
        )
        return hidden.mean(dim=0)

    def score_vectors(self, state: Tensor, actions: Tensor) -> Tensor:
        """Also serves precomputed PF vectors; no implicit detach for scratch training."""
        if (
            state.shape != (self.core.width,) or actions.ndim != 2
            or actions.shape[1] != self.core.width or actions.shape[0] == 0
            or not bool(torch.isfinite(state).all()) or not bool(torch.isfinite(actions).all())
        ):
            raise ValueError("invalid D-Simple state/action vectors")
        current = state.expand(actions.shape[0], -1)
        future = F.normalize(current + self.transition(torch.cat((current, actions), dim=1)), dim=1)
        return self.head(future).flatten()  # type: ignore[no-any-return]

    def forward(self, state: Tensor, actions: tuple[Tensor, ...]) -> Tensor:
        validate_catalog(self.core, state, actions)
        current = self.encode(state)
        action_vectors = torch.stack([self.encode(action) for action in actions])
        return self.score_vectors(current, action_vectors)


def build_scorer(
    recipe_id: str, core: TokenCore, *, readout_initial: Tensor | None = None,
) -> BTokenScorer | DSimpleTokenScorer:
    recipe = recipe_for(recipe_id)
    if core.frozen != (recipe.backbone == "pf"):
        raise ValueError("recipe and backbone training scope disagree")
    if recipe.family == "b":
        return BTokenScorer(core, readout_initial=readout_initial,
                            packed=recipe.graph == "b.shared-observation.v2")
    if readout_initial is not None:
        raise ValueError("D-Simple has no B readout query")
    return DSimpleTokenScorer(core)
