"""One observation plus isolated action/readout branches in one logical token sequence."""
from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import Tensor


@dataclass(frozen=True)
class PackedActions:
    embeddings: Tensor
    positions: Tensor
    blocked: Tensor
    readouts: Tensor
    prefix: Tensor


def pack_actions(state: Tensor, actions: tuple[Tensor, ...], query: Tensor) -> PackedActions:
    """Masks apply at every layer; repeated branch positions are intentional."""
    length = state.shape[0]
    parts = [state]
    positions = [torch.arange(length, device=state.device)]
    ends = []
    spans = []
    offset = length
    for action in actions:
        count = action.shape[0] + 1
        parts.extend((action, query.unsqueeze(0)))
        positions.append(torch.arange(length, length + count, device=state.device))
        spans.append((offset, offset + count))
        ends.append(offset + count - 1)
        offset += count
    blocked = torch.ones(offset, offset, dtype=torch.bool, device=state.device)
    blocked[:length, :length] = torch.ones(
        length, length, dtype=torch.bool, device=state.device,
    ).triu(1)
    for start, end in spans:
        blocked[start:end, :length] = False
        blocked[start:end, start:end] = torch.ones(
            end - start, end - start, dtype=torch.bool, device=state.device,
        ).triu(1)
    readouts = torch.tensor(ends, device=state.device, dtype=torch.long)
    keep = torch.ones(offset, device=state.device, dtype=torch.bool)
    keep[readouts] = False
    return PackedActions(torch.cat(parts), torch.cat(positions), blocked,
                         readouts, torch.arange(offset, device=state.device)[keep])
