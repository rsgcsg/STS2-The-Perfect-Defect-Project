"""Public scene attention residual for a policy-independent progress critic."""
from __future__ import annotations

from typing import cast

import torch
from torch import Tensor, nn


class AttentionValueResidual(nn.Module):
    """One learned query; padding excluded; no dropout or policy RNG consumption."""

    def __init__(self) -> None:
        super().__init__()
        self.query = nn.Parameter(torch.randn(1, 1, 192) * 0.02)
        self.attention = nn.MultiheadAttention(192, 4, dropout=0, batch_first=True)
        self.mlp = nn.Sequential(nn.Linear(384, 256), nn.GELU(),
                                 nn.Linear(256, 256), nn.GELU(), nn.Linear(256, 1))
        nn.init.zeros_(cast(nn.Linear, self.mlp[-1]).weight)
        nn.init.zeros_(cast(nn.Linear, self.mlp[-1]).bias)

    def forward(self, scene: Tensor, mask: Tensor, mean: Tensor) -> Tensor:
        pooled, _ = self.attention(self.query.expand(scene.shape[0], -1, -1),
                                   scene, scene, key_padding_mask=mask, need_weights=False)
        return cast(Tensor, self.mlp(torch.cat((pooled[:, 0], mean), dim=1)))
