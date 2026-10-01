"""Small independent text-action encoder shared by M0 and the existing M2 model."""

from __future__ import annotations

import torch
from torch import Tensor, nn
from torch.nn import functional as F


class LightActionEncoder(nn.Module):
    """Encode one complete action token row, independent of the state vocabulary."""

    def __init__(self, vocab_size: int, width: int, latent_width: int | None = None) -> None:
        super().__init__()
        if (type(vocab_size) is not int or vocab_size <= 0 or type(width) is not int
                or width <= 0 or latent_width is not None
                and (type(latent_width) is not int or latent_width <= 0)):
            raise ValueError("invalid light action encoder dimensions")
        output_width = width if latent_width is None else latent_width
        self.embedding = nn.Embedding(vocab_size, width)
        self.conv = nn.Conv1d(width, width, kernel_size=3, padding=1)
        self.projection = nn.Linear(width, output_width)
        self.norm = nn.LayerNorm(output_width)

    def forward(self, ids: Tensor) -> Tensor:
        if ids.ndim != 1 or ids.dtype != torch.long or ids.numel() == 0:
            raise ValueError("light action tokens must be a nonempty int64 vector")
        embedded = self.embedding(ids).transpose(0, 1).unsqueeze(0)
        pooled = F.gelu(self.conv(embedded)).mean(dim=-1).squeeze(0)
        return self.norm(self.projection(pooled))  # type: ignore[no-any-return]
