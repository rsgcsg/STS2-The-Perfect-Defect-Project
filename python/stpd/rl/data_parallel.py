"""C-SCALE data parallel, first version: one minibatch per rank, one all-reduce.

Every rank starts from the same theta/Adam and runs `prepared_update` on its own
prepared minibatch with `gradient_sync=GradientAverage()`. After the local
backward the hook averages, in one flat all-reduce, every parameter gradient,
the eight loss totals and the finite flag. Clip norm, KL stop and the Adam step
then see identical values on every rank, so theta/Adam stay identical without a
broadcast. The combined step is the mean of the ranks' minibatch gradients
(equal-sized minibatches: the gradient of their union).

A parameter with no gradient on any rank keeps `grad=None` (Adam skips it, as
on one device); one with a gradient on some rank gets the mean over all ranks,
counting a missing rank as zero. No overlap with backward: the gradient is a
few MB against a step of about 0.4 s (C-SCALE step 2 measures the cost).
Not wired into the SGD process yet; that integration is a later card.
"""

from __future__ import annotations

from typing import Any

import torch
from torch import Tensor


class GradientAverage:
    """`prepared_update(gradient_sync=...)` hook over one torch.distributed group."""

    def __init__(self, group: Any = None) -> None:
        import torch.distributed as dist

        if not dist.is_available() or not dist.is_initialized():
            raise ValueError("data_parallel_requires_initialized_process_group")
        self.dist = dist
        self.group = group
        self.world = dist.get_world_size(group)

    def __call__(self, model: torch.nn.Module, totals: Tensor,
                 valid: Tensor) -> tuple[Tensor, Tensor]:
        parameters = [p for p in model.parameters() if p.requires_grad]
        device = totals.device
        present = torch.tensor([p.grad is not None for p in parameters], device=device,
                               dtype=torch.float32)
        flat = torch.cat([*(p.grad.reshape(-1).float() if p.grad is not None
                            else torch.zeros(p.numel(), device=device)
                            for p in parameters),
                          totals.float(), valid.float().reshape(1), present])
        self.dist.all_reduce(flat, group=self.group)
        counts = flat[-len(parameters):] if parameters else flat[:0]
        flat = flat[:flat.numel() - len(parameters)] / self.world
        # The one host read of this hook: which parameters any rank touched.
        touched = (counts > 0).tolist()
        cursor = 0
        for parameter, used in zip(parameters, touched, strict=True):
            size = parameter.numel()
            if used:
                value = flat[cursor:cursor + size].view_as(parameter).to(parameter.dtype)
                if parameter.grad is None:
                    parameter.grad = value.clone()
                else:
                    parameter.grad.copy_(value)
            cursor += size
        mean_totals = flat[cursor:cursor + totals.numel()].to(totals.dtype)
        # Every rank finite: the mean of 0/1 flags is exactly 1.0 only then.
        all_valid = flat[cursor + totals.numel()] == 1.0
        return mean_totals, all_valid
