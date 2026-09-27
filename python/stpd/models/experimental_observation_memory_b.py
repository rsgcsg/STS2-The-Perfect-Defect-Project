"""Experimental observation-only Scratch B memory graph for synthetic windows.

This is not a registered M1 recipe. In particular, the previous confirmed
executed action required by the documented M1 graph is unavailable here.
No Human selected action, planned choice, or unconfirmed delivery is substituted.
"""

from __future__ import annotations

from typing import cast

import torch
from torch import Tensor, nn

from .stage1a import validate_catalog
from .token_core import ScratchTokenCore


class ExperimentalObservationMemoryB(nn.Module):
    """One summary call, one GRU update, then shared-prefix candidate reads."""

    def __init__(self, core: ScratchTokenCore, *, hidden_width: int = 32,
                 memory_slots: int = 1, reset_each_step: bool = False) -> None:
        super().__init__()
        if core.shape.dropout != 0:
            raise ValueError("experimental memory requires zero-dropout Scratch")
        if (type(hidden_width) is not int or hidden_width <= 0
                or type(memory_slots) is not int or memory_slots <= 0):
            raise ValueError("invalid memory dimensions")
        self.core = core
        self.hidden_width = hidden_width
        self.memory_slots = memory_slots
        self.reset_each_step = reset_each_step
        self.summary_query = nn.Parameter(torch.randn(core.width) * 0.02)
        self.readout = nn.Parameter(torch.randn(core.width) * 0.02)
        self.gru = nn.GRUCell(core.width, hidden_width)
        self.memory_projection = nn.Linear(hidden_width, memory_slots * core.width)
        self.head = nn.Linear(core.width, 1)

    def initial_hidden(self) -> Tensor:
        return self.summary_query.new_zeros(self.hidden_width)

    def advance(self, observation: Tensor, hidden: Tensor, *, reset_before: bool = False) -> Tensor:
        """Consume the observation once; candidate labels and actions are absent."""
        self.core.validate_tokens(observation)
        if (hidden.shape != (self.hidden_width,)
                or hidden.device != self.summary_query.device
                or hidden.dtype != self.summary_query.dtype):
            raise ValueError("invalid memory hidden shape, device or dtype")
        if reset_before or self.reset_each_step:
            hidden = torch.zeros_like(hidden)
        summary = self.core.read_last_query(observation, self.summary_query)
        return cast(Tensor, self.gru(summary, hidden))

    def _validate_catalog_capacity(self, observation: Tensor,
                                   actions: tuple[Tensor, ...]) -> None:
        validate_catalog(self.core, observation, actions)
        # Preflight all branches before constructing the shared Transformer graph.
        if any(self.memory_slots + observation.numel() + action.numel() + 1
               > self.core.max_tokens for action in actions):
            raise ValueError("joint input token limit exceeded; truncation is forbidden")

    def score(self, observation: Tensor, actions: tuple[Tensor, ...], hidden: Tensor) -> Tensor:
        """Read one frozen-for-this-decision hidden across the complete catalog."""
        self._validate_catalog_capacity(observation, actions)
        if (hidden.shape != (self.hidden_width,)
                or hidden.device != self.summary_query.device
                or hidden.dtype != self.summary_query.dtype):
            raise ValueError("invalid memory hidden shape, device or dtype")
        prefix = self.memory_projection(hidden).reshape(self.memory_slots, self.core.width)
        shared = torch.cat((prefix, self.core.embed_tokens(observation)), dim=0)
        encoded = self.core.read_action_queries_from_shared(shared, actions, self.readout)
        return self.head(encoded).flatten()  # type: ignore[no-any-return]

    def step(self, observation: Tensor, actions: tuple[Tensor, ...], hidden: Tensor,
             *, reset_before: bool = False) -> tuple[Tensor, Tensor]:
        """Return scores and next hidden; caller controls event idempotency."""
        self._validate_catalog_capacity(observation, actions)
        updated = self.advance(observation, hidden, reset_before=reset_before)
        return self.score(observation, actions, updated), updated
