"""Experimental light-action D-Simple M2 graph; caller owns event provenance.

This graph applies the token core only to the current page. It then updates a
fixed set of latent memory slots with page features and any already-qualified
previous interaction/feedback, and scores a complete catalog through read-only
slot attention. No candidate or target enters the memory write.
"""

from __future__ import annotations

import hashlib
import math
from typing import cast

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from stpd.light_action_codec import BOS_ACT, EOS_ACT, VOCAB_SIZE

from .light_action_encoder import LightActionEncoder
from .token_core import TokenCore

LIGHT_ACTION_M2_GRAPH = "dsimple.light-action.m2.v1"
LIGHT_ACTION_M2_WIDTH = 384


def _branch_seed(seed: int, branch: str) -> int:
    payload = f"stage1a:{LIGHT_ACTION_M2_GRAPH}:initialization:{seed}:{branch}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % (2**63)


class LightActionM2Scorer(nn.Module):
    """One shared page write followed by complete-catalog, fixed-slot scoring.

    The caller supplies only a previously confirmed actual action and any
    independently qualified public feedback. This module cannot validate their
    provenance, deduplicate events, or decide episode continuity. Memory remains
    attached to autograd; sequence owners choose where to truncate BPTT.

    Frozen PF-style cores process only page/feedback tokens and remain eval-only;
    memory updates happen afterward in trainable 384-wide layers. Trainable
    scratch/LoRA-style cores preserve gradients through page contextualization.
    """

    def __init__(self, core: TokenCore, *, max_action_bytes: int,
                 initialization_seed: int, slots: int = 1,
                 reset_each_step: bool = False) -> None:
        super().__init__()
        if (type(max_action_bytes) is not int or not 1 <= max_action_bytes <= 8192
                or type(initialization_seed) is not int or initialization_seed < 0
                or type(slots) is not int or slots not in (1, 8)
                or type(reset_each_step) is not bool):
            raise ValueError("invalid light-action M2 configuration")
        if core.width <= 0 or core.vocab_size <= 0 or core.max_tokens <= 0:
            raise ValueError("invalid token core dimensions")
        self.core = core
        self.max_action_bytes = max_action_bytes
        self.slots = slots
        self.reset_each_step = reset_each_step

        width = LIGHT_ACTION_M2_WIDTH
        with torch.random.fork_rng(devices=[]):
            torch.random.default_generator.manual_seed(_branch_seed(initialization_seed, "state"))
            self.state_projection = nn.Linear(core.width, width)
        with torch.random.fork_rng(devices=[]):
            torch.random.default_generator.manual_seed(_branch_seed(initialization_seed, "action"))
            self.action_encoder = LightActionEncoder(VOCAB_SIZE, width, width)
        with torch.random.fork_rng(devices=[]):
            torch.random.default_generator.manual_seed(_branch_seed(initialization_seed, "write"))
            self.write_queries = nn.Parameter(torch.randn(8, width) * 0.02)
            self.old_memory_marker = nn.Parameter(torch.randn(width) * 0.02)
            self.page_marker = nn.Parameter(torch.randn(width) * 0.02)
            self.previous_action_marker = nn.Parameter(torch.randn(width) * 0.02)
            self.feedback_marker = nn.Parameter(torch.randn(width) * 0.02)
            self.write_key = nn.Linear(width, width)
            self.write_value = nn.Linear(width, width)
            self.write_norm = nn.LayerNorm(width)
            self.memory_gate = nn.Linear(2 * width, width)
        with torch.random.fork_rng(devices=[]):
            torch.random.default_generator.manual_seed(_branch_seed(initialization_seed, "read"))
            self.action_query = nn.Linear(width, width)
            self.memory_key = nn.Linear(width, width)
            self.memory_value = nn.Linear(width, width)
            self.transition = nn.Sequential(
                nn.Linear(2 * width, width), nn.GELU(), nn.Linear(width, width),
            )
            self.score_head = nn.Sequential(
                nn.Linear(width, width), nn.GELU(), nn.Linear(width, 1),
            )

    def initial_memory(self) -> Tensor:
        return self.write_queries.new_zeros((self.slots, LIGHT_ACTION_M2_WIDTH))

    def _validate_memory(self, memory: Tensor) -> None:
        if (not isinstance(memory, Tensor)
                or memory.shape != (self.slots, LIGHT_ACTION_M2_WIDTH)
                or memory.device != self.write_queries.device
                or memory.dtype != self.write_queries.dtype
                or not bool(torch.isfinite(memory).all())):
            raise ValueError("invalid M2 memory shape, device, dtype or values")
        core_parameter = next(self.core.parameters(), None)
        if (core_parameter is None or core_parameter.device != memory.device
                or core_parameter.dtype != memory.dtype):
            raise ValueError("core and memory device or dtype differ")

    def _validate_action(self, action: Tensor, device: torch.device) -> None:
        if (not isinstance(action, Tensor) or action.ndim != 1
                or action.dtype != torch.long or action.device != device
                or action.numel() < 3 or action.numel() - 2 > self.max_action_bytes
                or int(action[0]) != BOS_ACT or int(action[-1]) != EOS_ACT
                or bool(((action[1:-1] < 0) | (action[1:-1] > 255)).any())):
            raise ValueError("invalid or over-limit byte action; truncation is forbidden")

    def _validate_inputs(
        self, page: Tensor, memory: Tensor, previous_actual_action: Tensor | None,
        public_feedback: Tensor | None, reset_before: bool,
    ) -> None:
        self._validate_memory(memory)
        if type(reset_before) is not bool:
            raise ValueError("invalid reset marker")
        self.core.validate_tokens(page)
        if page.device != memory.device:
            raise ValueError("page and memory devices differ")
        if previous_actual_action is not None:
            self._validate_action(previous_actual_action, memory.device)
        if public_feedback is not None:
            self.core.validate_tokens(public_feedback)
            if public_feedback.device != memory.device:
                raise ValueError("feedback and memory devices differ")
            if page.numel() + public_feedback.numel() > self.core.max_tokens:
                raise ValueError("page and feedback token limit exceeded; truncation is forbidden")

    def _validate_catalog(self, actions: tuple[Tensor, ...], memory: Tensor) -> None:
        if not isinstance(actions, tuple) or not actions:
            raise ValueError("complete candidate catalog must be a nonempty tuple")
        for action in actions:
            self._validate_action(action, memory.device)

    def _core_features(self, tokens: Tensor) -> Tensor:
        embeddings = self.core.embed_tokens(tokens)
        if self.core.frozen:
            with torch.no_grad():
                hidden = self.core.contextualize(
                    embeddings.detach(), causal=not self.core.supports_bidirectional,
                )
        else:
            hidden = self.core.contextualize(
                embeddings, causal=not self.core.supports_bidirectional,
            )
        return cast(Tensor, self.state_projection(hidden))

    def advance(
        self, page: Tensor, memory: Tensor, *,
        previous_actual_action: Tensor | None = None,
        public_feedback: Tensor | None = None,
        reset_before: bool = False,
    ) -> Tensor:
        """Write once from the current page and caller-qualified past inputs."""
        self._validate_inputs(page, memory, previous_actual_action, public_feedback, reset_before)
        old = (torch.zeros_like(memory) if reset_before or self.reset_each_step else memory)
        parts = [old + self.old_memory_marker]
        parts.append(self._core_features(page) + self.page_marker)
        if previous_actual_action is not None:
            parts.append((self.action_encoder(previous_actual_action)
                          + self.previous_action_marker).unsqueeze(0))
        if public_feedback is not None:
            parts.append((self._core_features(public_feedback).mean(dim=0)
                          + self.feedback_marker).unsqueeze(0))
        inputs = torch.cat(parts, dim=0)
        keys = self.write_key(inputs)
        values = self.write_value(inputs)
        queries = self.write_queries[:self.slots]
        attention = torch.softmax(queries @ keys.T / math.sqrt(LIGHT_ACTION_M2_WIDTH), dim=-1)
        proposal = self.write_norm(queries + attention @ values)
        keep = torch.sigmoid(self.memory_gate(torch.cat((old, proposal), dim=-1)))
        updated = keep * old + (1 - keep) * proposal
        if not bool(torch.isfinite(updated).all()):
            raise ValueError("nonfinite M2 memory write")
        return cast(Tensor, updated)

    def score(self, memory: Tensor, actions: tuple[Tensor, ...]) -> Tensor:
        """Read the same fixed memory for each full-catalog candidate."""
        self._validate_memory(memory)
        self._validate_catalog(actions, memory)
        action_vectors = torch.stack([self.action_encoder(action) for action in actions])
        query = self.action_query(action_vectors)
        keys = self.memory_key(memory)
        values = self.memory_value(memory)
        attention = torch.softmax(query @ keys.T / math.sqrt(LIGHT_ACTION_M2_WIDTH), dim=-1)
        memory_read = attention @ values
        future = F.normalize(
            memory_read + self.transition(torch.cat((memory_read, action_vectors), dim=-1)),
            p=2, dim=-1,
        )
        scores = self.score_head(future).flatten()
        if not bool(torch.isfinite(scores).all()):
            raise ValueError("nonfinite M2 candidate score")
        return cast(Tensor, scores)

    def step(
        self, page: Tensor, actions: tuple[Tensor, ...], memory: Tensor, *,
        previous_actual_action: Tensor | None = None,
        public_feedback: Tensor | None = None,
        reset_before: bool = False,
    ) -> tuple[Tensor, Tensor]:
        """Preflight all inputs/catalog, advance once, and score read-only."""
        self._validate_inputs(page, memory, previous_actual_action, public_feedback, reset_before)
        self._validate_catalog(actions, memory)
        updated = self.advance(
            page, memory, previous_actual_action=previous_actual_action,
            public_feedback=public_feedback, reset_before=reset_before,
        )
        return self.score(updated, actions), updated
