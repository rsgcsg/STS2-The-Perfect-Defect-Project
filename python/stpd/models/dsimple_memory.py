"""Experimental D-Simple M2 computation, without a runtime recipe or event owner.

The caller supplies only a *confirmed* previous actual action and known public
native feedback. This module cannot establish either provenance, deduplicate an
event, or decide when a new run begins. Candidate scoring never writes memory.
"""

from __future__ import annotations

import math

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from .token_core import TokenCore


class _LightActionEncoder(nn.Module):
    """An independent token embedding and local convolution, with no page core."""

    def __init__(self, vocab_size: int, width: int) -> None:
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, width)
        self.conv = nn.Conv1d(width, width, kernel_size=3, padding=1)
        self.projection = nn.Linear(width, width)
        self.norm = nn.LayerNorm(width)

    def forward(self, ids: Tensor) -> Tensor:
        embedded = self.embedding(ids).transpose(0, 1).unsqueeze(0)
        pooled = F.gelu(self.conv(embedded)).mean(dim=-1).squeeze(0)
        return self.norm(self.projection(pooled))  # type: ignore[no-any-return]


class ExperimentalDSimpleM2(nn.Module):
    """One page write per event, then O(candidates × fixed slots) read-only scoring.

    ``reset_each_step`` changes only the old-memory input. A reset control and a
    persistent model therefore have exactly the same state-dict structure.
    """

    def __init__(self, core: TokenCore, *, slots: int = 1,
                 reset_each_step: bool = False, gated: bool = False) -> None:
        super().__init__()
        if type(slots) is not int or slots not in (1, 8) or 2 * slots + 1 > core.max_tokens:
            raise ValueError("invalid memory slots or page token capacity")
        if type(reset_each_step) is not bool or type(gated) is not bool:
            raise ValueError("invalid memory control")
        if core.width <= 0 or core.vocab_size <= 0:
            raise ValueError("invalid token core dimensions")
        self.core = core
        self.slots = slots
        self.reset_each_step = reset_each_step
        self.write_queries = nn.Parameter(torch.randn(slots, core.width) * 0.02)
        self.previous_action_marker = nn.Parameter(torch.randn(core.width) * 0.02)
        self.feedback_marker = nn.Parameter(torch.randn(core.width) * 0.02)
        self.action_encoder = _LightActionEncoder(core.vocab_size, core.width)
        self.feedback_embedding = nn.Embedding(core.vocab_size, core.width)
        self.feedback_projection = nn.Sequential(
            nn.Linear(core.width, core.width), nn.LayerNorm(core.width),
        )
        self.write_norm = nn.LayerNorm(core.width)
        self.memory_gate = nn.Linear(2 * core.width, core.width) if gated else None
        self.action_query = nn.Linear(core.width, core.width)
        self.memory_key = nn.Linear(core.width, core.width)
        self.memory_value = nn.Linear(core.width, core.width)
        self.transition = nn.Sequential(
            nn.Linear(2 * core.width, core.width), nn.GELU(),
            nn.Linear(core.width, core.width),
        )
        self.score_head = nn.Sequential(
            nn.Linear(core.width, core.width), nn.GELU(), nn.Linear(core.width, 1),
        )

    def initial_memory(self) -> Tensor:
        return self.write_queries.new_zeros((self.slots, self.core.width))

    def _validate_memory(self, memory: Tensor) -> None:
        if (not isinstance(memory, Tensor)
                or memory.shape != (self.slots, self.core.width)
                or memory.device != self.write_queries.device
                or memory.dtype != self.write_queries.dtype
                or not bool(torch.isfinite(memory).all())):
            raise ValueError("invalid memory shape, device, dtype or values")
        first_core_parameter = next(self.core.parameters(), None)
        if (first_core_parameter is None
                or first_core_parameter.device != memory.device
                or first_core_parameter.dtype != memory.dtype):
            raise ValueError("core and memory device or dtype differ")

    def _validate_tokens(self, ids: Tensor, memory: Tensor) -> None:
        if not isinstance(ids, Tensor):
            raise ValueError("tokens must be tensors")
        self.core.validate_tokens(ids)
        if ids.device != memory.device:
            raise ValueError("token and memory devices differ")

    def _validate_advance(
        self, page: Tensor, memory: Tensor, previous_actual_action: Tensor | None,
        feedback: Tensor | None, reset_before: bool,
    ) -> None:
        self._validate_memory(memory)
        if type(reset_before) is not bool:
            raise ValueError("invalid reset marker")
        self._validate_tokens(page, memory)
        if previous_actual_action is not None:
            self._validate_tokens(previous_actual_action, memory)
        if feedback is not None:
            self._validate_tokens(feedback, memory)
        length = 2 * self.slots + page.numel()
        length += int(previous_actual_action is not None) + int(feedback is not None)
        if length > self.core.max_tokens:
            raise ValueError("page and memory token limit exceeded; truncation is forbidden")

    def _validate_actions(self, actions: tuple[Tensor, ...], memory: Tensor) -> None:
        if not isinstance(actions, tuple) or not actions:
            raise ValueError("complete candidate catalog must be a nonempty tuple")
        for action in actions:
            self._validate_tokens(action, memory)

    def advance(
        self, page: Tensor, memory: Tensor, *,
        previous_actual_action: Tensor | None = None,
        feedback: Tensor | None = None, reset_before: bool = False,
    ) -> Tensor:
        """Absorb one caller-owned observation; no current candidate or label enters."""
        self._validate_advance(page, memory, previous_actual_action, feedback, reset_before)
        old = torch.zeros_like(memory) if reset_before or self.reset_each_step else memory
        parts = [old]
        if previous_actual_action is not None:
            parts.append((self.action_encoder(previous_actual_action)
                          + self.previous_action_marker).unsqueeze(0))
        if feedback is not None:
            feedback_vector = self.feedback_projection(
                self.feedback_embedding(feedback).mean(dim=0))
            parts.append((feedback_vector + self.feedback_marker).unsqueeze(0))
        page_embeddings = self.core.embed_tokens(page)
        if (page_embeddings.shape != (page.numel(), self.core.width)
                or page_embeddings.device != memory.device
                or page_embeddings.dtype != memory.dtype):
            raise ValueError("page embeddings and memory differ")
        parts.extend((page_embeddings, self.write_queries))
        encoded = self.core.contextualize(
            torch.cat(parts, dim=0), causal=not self.core.supports_bidirectional,
        )
        proposal: Tensor = self.write_norm(encoded[-self.slots:])
        if proposal.shape != memory.shape or not bool(torch.isfinite(proposal).all()):
            raise ValueError("invalid memory write")
        if self.memory_gate is None:
            return proposal
        keep = torch.sigmoid(self.memory_gate(torch.cat((old, proposal), dim=-1)))
        updated: Tensor = keep * old + (1 - keep) * proposal
        if not bool(torch.isfinite(updated).all()):
            raise ValueError("invalid gated memory write")
        return updated

    def score(self, memory: Tensor, actions: tuple[Tensor, ...]) -> Tensor:
        """Read the same fixed-slot memory for every complete-catalog candidate."""
        self._validate_memory(memory)
        self._validate_actions(actions, memory)
        action_vectors = torch.stack([self.action_encoder(action) for action in actions])
        query = self.action_query(action_vectors)
        keys = self.memory_key(memory)
        values = self.memory_value(memory)
        attention = torch.softmax(query @ keys.T / math.sqrt(self.core.width), dim=-1)
        memory_read = attention @ values
        future = F.normalize(
            memory_read + self.transition(
                torch.cat((memory_read, action_vectors), dim=-1),
            ),
            p=2, dim=-1,
        )
        scores = self.score_head(future).flatten()
        if not bool(torch.isfinite(scores).all()):
            raise ValueError("nonfinite candidate score")
        return scores  # type: ignore[no-any-return]

    def step(
        self, page: Tensor, actions: tuple[Tensor, ...], memory: Tensor, *,
        previous_actual_action: Tensor | None = None,
        feedback: Tensor | None = None, reset_before: bool = False,
    ) -> tuple[Tensor, Tensor]:
        """Preflight the *entire* menu before one write and read-only scoring."""
        self._validate_advance(page, memory, previous_actual_action, feedback, reset_before)
        self._validate_actions(actions, memory)
        updated = self.advance(
            page, memory, previous_actual_action=previous_actual_action,
            feedback=feedback, reset_before=reset_before,
        )
        return self.score(updated, actions), updated
