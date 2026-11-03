"""RL4 public-scene outcome and absolute post-combat HP readouts.

The two heads read the shared scene, never each other's predictions. Reward
values describe the selected standard A0 configuration; observed terminal reward
must match before a class label is admitted. Missing Boss witnesses are not filled.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import cast

import torch
from torch import Tensor, nn

from stpd.p1.value_head import AttentionValueResidual

# Golden Compass has a witnessed alternate Act-2 map: natural death on floor35
# with only Boss1 cleared. Keep the original default classes, and split only the
# actual reward ambiguity; this is still one outcome head, not another objective.
OUTCOME_CLASSES = 51
CLASS_FLOORS = tuple(range(49)) + (49, 35)
WIN_CLASS = 49
HEAD_PREFIX = "rl4_heads."


class Rl4Heads(nn.Module):
    def __init__(self, frequencies: Sequence[float] | None = None) -> None:
        super().__init__()
        counts = torch.ones(OUTCOME_CLASSES) if frequencies is None else torch.tensor(
            list(frequencies), dtype=torch.float32)
        if (counts.shape != (OUTCOME_CLASSES,) or not bool(torch.isfinite(counts).all())
                or bool((counts < 0).any())):
            raise ValueError("invalid_outcome_frequencies")
        # Reuse the established learned query/attention/mean/MLP implementation.
        self.outcome = AttentionValueResidual()
        self.outcome.mlp[-1] = nn.Linear(256, OUTCOME_CLASSES)
        final = cast(nn.Linear, self.outcome.mlp[-1])
        nn.init.zeros_(final.weight)
        with torch.no_grad():
            final.bias.copy_((counts + 1e-3).log())
        self.hp = AttentionValueResidual()

    def forward(self, scene: Tensor, mask: Tensor, mean: Tensor,
                floors: Sequence[int] | Tensor) -> tuple[Tensor, Tensor]:
        logits = self.outcome(scene, mask, mean).float()
        valid = outcome_mask(floors, device=logits.device)
        if logits.shape != valid.shape:
            raise ValueError("outcome_floor_batch_mismatch")
        return logits.masked_fill(~valid, -torch.inf), self.hp(scene, mask, mean)[:, 0].float()


def outcome_mask(floors: Sequence[int] | Tensor, *, device: torch.device | None = None) -> Tensor:
    values = torch.as_tensor(floors, device=device)
    if (values.ndim != 1 or values.dtype not in (torch.int32, torch.int64)
            or bool((values < 0).any())):
        raise ValueError("invalid_max_observed_floor")
    # Golden Compass lengthens the native map. Reward-v2 already saturates its
    # floor term at 48, so class48 also represents later equal-reward losses.
    # Preserve actual public floors in inputs/records; only this mask is capped.
    values = values.clamp(max=48)
    class_floors = torch.tensor(CLASS_FLOORS, device=values.device)
    return class_floors[None, :] >= values[:, None]


def outcome_cross_entropy(logits: Tensor, targets: Tensor) -> Tensor:
    """Per-row soft-label CE; impossible classes never multiply minus infinity."""
    values = logits.float()
    target = targets.to(device=values.device, dtype=torch.float32)
    valid = torch.isfinite(values)
    if (values.ndim != 2 or values.shape[1] != OUTCOME_CLASSES
            or target.shape != values.shape or not bool(valid.any(1).all())
            or bool(torch.isnan(values).any()) or bool(torch.isposinf(values).any())
            or not bool(torch.isfinite(target).all()) or bool((target < 0).any())
            or bool(target.masked_select(~valid).ne(0).any())
            or not torch.allclose(target.sum(1), torch.ones_like(target[:, 0]), atol=1e-5)):
        raise ValueError("invalid_outcome_distribution_or_target")
    logp = values.log_softmax(-1).masked_fill(~valid, 0)
    return -(target * logp).sum(-1)


def class_reward_values(*, device: torch.device | None = None) -> Tensor:
    """Selected A0 class representation, checked against actual reward at admission.

    A natural loss at a Boss floor has not cleared that Boss. A higher floor has
    passed it in this fixed native layout. This table is not evidence of clearance.
    """
    return torch.tensor([0.5 * (floor / 48) ** 2
                         + 0.1 * sum(boss < floor for boss in (17, 33, 48))
                         for floor in range(49)] + [1.0, 0.5 * (35 / 48) ** 2 + 0.1], device=device)


def checked_terminal_class(*, result: str, floor: int, reward: float) -> int:
    if type(floor) is not int or floor < 0 or result not in {"win", "loss"}:
        raise ValueError("invalid_natural_terminal_class")
    floor = min(floor, 48)
    candidates = [WIN_CLASS] if result == "win" else [
        index for index, value in enumerate(CLASS_FLOORS)
        if value == floor and index != WIN_CLASS]
    matched = [label for label in candidates if math.isfinite(reward)
        and math.isclose(reward, float(class_reward_values()[label]), abs_tol=1e-6)]
    if len(matched) != 1:
        raise ValueError("terminal_class_reward_mismatch")
    return matched[0]


def categorical_lambda_targets(probabilities: Tensor, boundary: Tensor | None, *,
                               terminal_class: int | None = None,
                               gae_lambda: float = 0.99) -> tuple[Tensor, Tensor]:
    """Frozen gamma=1 segment targets and scalar advantages, without extra rho.

    probabilities contains only the T current states. A nonterminal segment also
    supplies its confirmed successor distribution; a real terminal supplies its
    checked class instead. No trace is stitched across segments or behavior mu.
    """
    p = probabilities.detach().float()
    if (p.ndim != 2 or p.shape[1] != OUTCOME_CLASSES or not 1 <= len(p) <= 64
            or not math.isfinite(gae_lambda) or not 0 < gae_lambda <= 1):
        raise ValueError("invalid_segment_distribution_or_lambda")
    if terminal_class is not None:
        if type(terminal_class) is not int or not 0 <= terminal_class < OUTCOME_CLASSES:
            raise ValueError("invalid_terminal_class")
        if boundary is not None:
            raise ValueError("terminal_has_no_bootstrap")
        tail = torch.nn.functional.one_hot(torch.tensor(terminal_class, device=p.device),
                                          OUTCOME_CLASSES).float()
    elif boundary is None:
        raise ValueError("nonterminal_requires_confirmed_boundary")
    else:
        tail = boundary.detach().to(device=p.device, dtype=torch.float32)
    if (tail.shape != (OUTCOME_CLASSES,) or not bool(torch.isfinite(p).all())
            or not bool(torch.isfinite(tail).all()) or bool((p < 0).any())
            or bool((tail < 0).any())
            or not torch.allclose(p.sum(1), torch.ones_like(p[:, 0]), atol=1e-5)
            or not torch.isclose(tail.sum(), tail.new_tensor(1), atol=1e-5)):
        raise ValueError("invalid_segment_probability_mass")
    targets = torch.empty_like(p)
    targets[-1] = tail
    for index in range(len(p) - 2, -1, -1):
        targets[index] = (1 - gae_lambda) * p[index + 1] + gae_lambda * targets[index + 1]
    advantages = (targets - p) @ class_reward_values(device=p.device)
    return targets, advantages
