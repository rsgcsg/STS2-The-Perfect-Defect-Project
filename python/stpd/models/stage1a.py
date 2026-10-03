"""Versioned Stage 1a model graphs; no data access, native execution or tokenizer fitting.

Callers retain complete candidate bindings and use the same versioned tokenization
for training and serving. The input contains current observation and candidates only.
"""

from __future__ import annotations

import hashlib

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from stpd.light_action_codec import BOS_ACT, EOS_ACT, VOCAB_SIZE
from stpd.stage1a_recipes import LIGHT_ACTION_M0_GRAPH as LIGHT_ACTION_M0_GRAPH
from stpd.stage1a_recipes import RECIPES as RECIPES
from stpd.stage1a_recipes import Recipe as Recipe
from stpd.stage1a_recipes import recipe_for as recipe_for

from .light_action_encoder import LightActionEncoder
from .token_core import TokenCore

LIGHT_ACTION_LATENT_WIDTH = 384


def _m0_initialization_seed(seed: int, branch: str) -> int:
    payload = f"stage1a:{LIGHT_ACTION_M0_GRAPH}:initialization:{seed}:{branch}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % (2**63)


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


class LightActionM0Scorer(nn.Module):
    """Stateless shared-state D-Simple with a fixed independent byte-action path."""

    def __init__(self, core: TokenCore, *, max_action_bytes: int,
                 initialization_seed: int, public_profile: str | None = None) -> None:
        super().__init__()
        if public_profile not in {None, "public_lite", "public_compact"}:
            raise ValueError("invalid public profile")
        cap = 1_000_000 if public_profile is not None else 8192
        if type(max_action_bytes) is not int or not 1 <= max_action_bytes <= cap:
            raise ValueError("invalid action byte limit")
        if core.width <= 0 or core.vocab_size <= 0:
            raise ValueError("invalid state core dimensions")
        self.core = core
        self.max_action_bytes = max_action_bytes
        width = LIGHT_ACTION_LATENT_WIDTH
        # Each M0 branch gets a stable stream so core.width cannot shift the
        # initial values of the common action and scoring branches.
        with torch.random.fork_rng(devices=[]):
            torch.random.default_generator.manual_seed(
                _m0_initialization_seed(initialization_seed, "state"),
            )
            self.state_projection = nn.Linear(core.width, width)
        with torch.random.fork_rng(devices=[]):
            torch.random.default_generator.manual_seed(
                _m0_initialization_seed(initialization_seed, "action"),
            )
            self.action_encoder = LightActionEncoder(VOCAB_SIZE, width, width)
        with torch.random.fork_rng(devices=[]):
            torch.random.default_generator.manual_seed(
                _m0_initialization_seed(initialization_seed, "transition"),
            )
            self.transition = nn.Sequential(
                nn.Linear(2 * width, width), nn.GELU(), nn.Linear(width, width),
            )
        with torch.random.fork_rng(devices=[]):
            torch.random.default_generator.manual_seed(
                _m0_initialization_seed(initialization_seed, "score"),
            )
            self.score_head = nn.Sequential(
                nn.Linear(width, 256), nn.GELU(), nn.Linear(256, 1),
            )

    def encode_state(self, tokens: Tensor) -> Tensor:
        self.core.validate_tokens(tokens)
        if self.core.frozen:
            with torch.no_grad():
                hidden = self.core.contextualize(self.core.embed_tokens(tokens), causal=True)
        else:
            hidden = self.core.contextualize(self.core.embed_tokens(tokens), causal=True)
        return self.state_projection(hidden.mean(dim=0))  # type: ignore[no-any-return]

    def _validate_action(self, ids: Tensor, device: torch.device) -> None:
        if (ids.ndim != 1 or ids.dtype != torch.long or ids.device != device
                or ids.numel() < 3 or ids.numel() - 2 > self.max_action_bytes
                or int(ids[0]) != BOS_ACT or int(ids[-1]) != EOS_ACT
                or bool(((ids[1:-1] < 0) | (ids[1:-1] > 255)).any())):
            raise ValueError("invalid or over-limit byte action; truncation is forbidden")

    def score_vectors(self, state: Tensor, actions: Tensor) -> Tensor:
        width = LIGHT_ACTION_LATENT_WIDTH
        if (state.shape != (width,) or actions.ndim != 2
                or actions.shape[1] != width or actions.shape[0] == 0
                or not bool(torch.isfinite(state).all())
                or not bool(torch.isfinite(actions).all())):
            raise ValueError("invalid M0 scoring latents")
        current = state.expand(actions.shape[0], -1)
        future = F.normalize(current + self.transition(torch.cat((current, actions), dim=1)), dim=1)
        return self.score_head(future).flatten()  # type: ignore[no-any-return]

    def forward(self, state: Tensor, actions: tuple[Tensor, ...]) -> Tensor:
        # Validate the entire catalog before state or action compute begins.
        self.core.validate_tokens(state)
        if not isinstance(actions, tuple) or not actions:
            raise ValueError("complete candidate catalog must be a nonempty tuple")
        for action in actions:
            self._validate_action(action, state.device)
        current = self.encode_state(state)
        action_vectors = torch.stack([self.action_encoder(action) for action in actions])
        return self.score_vectors(current, action_vectors)


def build_scorer(
    recipe_id: str, core: TokenCore, *, readout_initial: Tensor | None = None,
    max_action_bytes: int | None = None, scoring_seed: int | None = None,
    public_profile: str | None = None,
) -> BTokenScorer | DSimpleTokenScorer | LightActionM0Scorer:
    recipe = recipe_for(recipe_id)
    if recipe.graph == LIGHT_ACTION_M0_GRAPH:
        if core.frozen != (recipe.backbone == "pf"):
            raise ValueError("recipe and backbone training scope disagree")
        if recipe.backbone == "pl" and not getattr(core, "is_lora", False):
            raise ValueError("LoRA recipe requires an adapter-enabled core")
        if max_action_bytes is None or scoring_seed is None or readout_initial is not None:
            raise ValueError("M0 requires its independent byte limit and scoring seed")
        return LightActionM0Scorer(
            core, max_action_bytes=max_action_bytes, initialization_seed=scoring_seed,
            public_profile=public_profile,
        )
    if public_profile is not None:
        raise ValueError("public M0 profile requires the M0 graph")
    if core.frozen != (recipe.backbone == "pf"):
        raise ValueError("recipe and backbone training scope disagree")
    if recipe.family == "b":
        return BTokenScorer(core, readout_initial=readout_initial,
                            packed=recipe.graph == "b.shared-observation.v2")
    if readout_initial is not None:
        raise ValueError("D-Simple has no B readout query")
    return DSimpleTokenScorer(core)
