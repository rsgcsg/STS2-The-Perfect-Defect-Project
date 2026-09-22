"""Unpadded token-core boundary shared by scratch and pinned frozen backbones.

One call describes one candidate branch. Packed/padded execution must establish
equivalence before replacing this reference; it is not implicit in this API.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

import torch
from torch import Tensor, nn

from .packed_actions import pack_actions


class TokenCore(nn.Module, ABC):
    width: int
    vocab_size: int
    max_tokens: int
    frozen: bool
    supports_bidirectional: bool = True

    def train(self, mode: bool = True) -> TokenCore:
        super().train(False if self.frozen else mode)
        return self

    def validate_tokens(self, ids: Tensor) -> None:
        if ids.ndim != 1 or ids.dtype != torch.long or ids.numel() == 0:
            raise ValueError("tokens must be a nonempty unpadded int64 vector")
        if ids.numel() > self.max_tokens:
            raise ValueError("input token limit exceeded; truncation is forbidden")
        if bool((ids < 0).any()) or bool((ids >= self.vocab_size).any()):
            raise ValueError("token ID outside backbone vocabulary")

    def read_last_query(self, ids: Tensor, query: Tensor) -> Tensor:
        """Reference causal branch. A frozen core may use verified equivalent KV execution."""
        self.validate_tokens(ids)
        if ids.numel() + 1 > self.max_tokens or query.shape != (self.width,):
            raise ValueError("invalid query shape or joint input token limit")
        embedded = torch.cat((self.embed_tokens(ids), query.unsqueeze(0)), dim=0)
        return self.contextualize(embedded, causal=True)[-1]

    def read_action_queries(
        self, state: Tensor, actions: tuple[Tensor, ...], query: Tensor,
    ) -> Tensor:
        packed = pack_actions(self.embed_tokens(state),
                              tuple(self.embed_tokens(a) for a in actions), query)
        return self.contextualize_packed(
            packed.embeddings, packed.positions, packed.blocked,
        )[packed.readouts]

    def contextualize_packed(
        self, embeddings: Tensor, positions: Tensor, blocked: Tensor,
    ) -> Tensor:
        raise NotImplementedError("backbone must implement shared-observation packed execution")

    @abstractmethod
    def embed_tokens(self, ids: Tensor) -> Tensor:
        """Return [length,width], preserving gradients for trainable embeddings."""

    @abstractmethod
    def contextualize(self, embeddings: Tensor, *, causal: bool) -> Tensor:
        """Return [length,width]; preserve input gradients even when frozen."""


@dataclass(frozen=True)
class ScratchShape:
    vocab_size: int
    width: int = 384
    layers: int = 2
    heads: int = 6
    feedforward: int = 1536
    dropout: float = 0.1
    max_tokens: int = 8192

    def validate(self) -> None:
        integers = (
            self.vocab_size, self.width, self.layers, self.heads,
            self.feedforward, self.max_tokens,
        )
        if any(type(v) is not int or v <= 0 for v in integers):
            raise ValueError("scratch dimensions must be positive integers")
        if self.width % 2 or self.width % self.heads:
            raise ValueError("scratch width must be even and divisible by heads")
        if not 0 <= self.dropout < 1:
            raise ValueError("invalid scratch dropout")


class ScratchTokenCore(TokenCore):
    """One trainable Transformer; B has no second joint encoder after this core."""

    def __init__(self, shape: ScratchShape) -> None:
        super().__init__()
        shape.validate()
        self.shape = shape
        self.width = shape.width
        self.vocab_size = shape.vocab_size
        self.max_tokens = shape.max_tokens
        self.frozen = False
        self.embedding = nn.Embedding(shape.vocab_size, shape.width)
        layer = nn.TransformerEncoderLayer(
            shape.width, shape.heads, shape.feedforward, shape.dropout,
            activation="gelu", batch_first=True, norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(
            layer, shape.layers, norm=nn.LayerNorm(shape.width), enable_nested_tensor=False,
        )
        # TransformerEncoder clones a prototype. Initialize layers independently.
        for block in self.encoder.layers:
            for parameter in block.parameters():
                if parameter.ndim > 1:
                    nn.init.xavier_uniform_(parameter)

    def embed_tokens(self, ids: Tensor) -> Tensor:
        self.validate_tokens(ids)
        return self.embedding(ids)  # type: ignore[no-any-return]

    def contextualize(self, embeddings: Tensor, *, causal: bool) -> Tensor:
        if (
            embeddings.ndim != 2 or embeddings.shape[1] != self.width
            or not 0 < embeddings.shape[0] <= self.max_tokens
        ):
            raise ValueError("invalid embedding shape or input token limit")
        length = embeddings.shape[0]
        position = torch.arange(length, device=embeddings.device, dtype=torch.float32)[:, None]
        frequency = torch.exp(
            torch.arange(0, self.width, 2, device=embeddings.device, dtype=torch.float32)
            * (-math.log(10000.0) / self.width)
        )
        angles = position * frequency
        position_embedding = torch.stack((angles.sin(), angles.cos()), dim=-1).flatten(1)
        inputs = embeddings + position_embedding.to(embeddings.dtype)
        mask = None
        if causal:
            mask = torch.ones(length, length, device=embeddings.device, dtype=torch.bool).triu(1)
        return self.encoder(inputs.unsqueeze(0), mask=mask, is_causal=causal)[0]  # type: ignore[no-any-return]

    def contextualize_packed(
        self, embeddings: Tensor, positions: Tensor, blocked: Tensor,
    ) -> Tensor:
        frequency = torch.exp(
            torch.arange(0, self.width, 2, device=embeddings.device, dtype=torch.float32)
            * (-math.log(10000.0) / self.width)
        )
        angles = positions.to(torch.float32)[:, None] * frequency
        encoding = torch.stack((angles.sin(), angles.cos()), dim=-1).flatten(1)
        inputs = embeddings + encoding.to(embeddings.dtype)
        # The custom branch mask is not an ordinary triangular causal mask.
        return self.encoder(inputs.unsqueeze(0), mask=blocked, is_causal=False)[0]  # type: ignore[no-any-return]
