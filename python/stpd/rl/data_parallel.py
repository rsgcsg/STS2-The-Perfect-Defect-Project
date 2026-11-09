"""C-SCALE data parallel: one all-reduce per optimizer attempt.

Two reductions over the same hook:

* `reduce="mean"` (weak scaling, first version): one minibatch per rank.
* `reduce="sum"` (WP8 default, same update): every rank runs a disjoint part of
  the physical groups of the *same* minibatch with that minibatch's global
  denominators, so each rank's gradient/totals are partial sums of the
  one-device values and their sum is the one-device minibatch gradient. The
  partition (`shard_groups`) only changes balance, never the result beyond
  float summation order.

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
Wired into the SGD process by `dataplane.sgd_data_parallel` (WP8).
"""

from __future__ import annotations

from typing import Any

import torch
from torch import Tensor


class GradientAverage:
    """`prepared_update(gradient_sync=...)` hook over one torch.distributed group."""

    def __init__(self, group: Any = None, *, reduce: str = "mean") -> None:
        import torch.distributed as dist

        if reduce not in ("mean", "sum"):
            raise ValueError("data_parallel_reduce_must_be_mean_or_sum")
        if not dist.is_available() or not dist.is_initialized():
            raise ValueError("data_parallel_requires_initialized_process_group")
        self.dist = dist
        self.group = group
        self.reduce = reduce
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
        flags = flat[flat.numel() - len(parameters) - 1]
        flat = flat[:flat.numel() - len(parameters)]
        if self.reduce == "mean":
            flat = flat / self.world
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
        reduced_totals = flat[cursor:cursor + totals.numel()].to(totals.dtype)
        # Every rank finite: the sum of 0/1 flags is exactly the world size only then.
        all_valid = flags == float(self.world)
        return reduced_totals, all_valid


def shard_groups(rows: list[int], rank: int, world: int) -> tuple[int, ...]:
    """Contiguous group indices of `rank`, balanced by row count.

    Group g goes to the rank whose equal share of the minibatch rows contains
    g's midpoint. Deterministic from the batch alone, so every rank derives the
    same disjoint cover; a rank may get no group (it then all-reduces zeros).
    """
    if type(world) is not int or world < 1 or type(rank) is not int or not 0 <= rank < world:
        raise ValueError("invalid_data_parallel_rank")
    total = sum(rows)
    if total <= 0:
        return ()
    chosen, before = [], 0
    for index, size in enumerate(rows):
        owner = min(world - 1, int(world * (before + size / 2) / total))
        if owner == rank:
            chosen.append(index)
        before += size
    return tuple(chosen)


def replica_check(model: torch.nn.Module, group: Any = None) -> None:
    """Loud if theta differs across ranks (it must be bitwise identical)."""
    import torch.distributed as dist

    parameters = list(model.parameters())
    if not parameters:
        return
    # On the parameters' device: NCCL collectives take CUDA tensors only.
    local = torch.stack([p.detach().double().sum() for p in parameters])
    world = dist.get_world_size(group)
    gathered = [torch.empty_like(local) for _ in range(world)]
    dist.all_gather(gathered, local, group=group)
    if any(not torch.equal(gathered[0], other) for other in gathered[1:]):
        raise ValueError("data_parallel_replicas_diverged")
