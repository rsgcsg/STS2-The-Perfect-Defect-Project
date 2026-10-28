"""One RL optimizer update: clipped policy gradient + KL to the start model + Human anchor.

Off-policy correction uses the recorded behavior probability of the chosen
action (ratio = pi_theta / mu, PPO-clipped, as in V-trace/DouZero-style actors
that play with a slightly older model). Advantages are Monte-Carlo episode
returns minus a baseline supplied by the caller; no value head is used.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import torch
from torch.nn import functional as F

from spireagent.json_boundary import BoundaryError
from stpd.p1.execution import microbatches
from stpd.p1.input import Frame
from stpd.p1.model import MStd


@dataclass(frozen=True)
class RlSample:
    frame: Frame
    action: int
    behavior_probability: float
    advantage: float
    model_version: int
    key: str


@dataclass(frozen=True)
class LossConfig:
    clip: float = 0.2
    kl_coef: float = 0.02
    anchor_coef: float = 0.5
    max_grad_norm: float = 1.0


def _scores(model: MStd, frames: list[Frame]) -> torch.Tensor:
    scores = model.forward_batch(frames)
    return torch.nn.utils.rnn.pad_sequence(scores, batch_first=True, padding_value=-torch.inf)


def _reference(reference: MStd, batch: list[RlSample],
               cache: dict[str, torch.Tensor] | None, width: int,
               device: torch.device) -> torch.Tensor:
    missing = [s for s in batch if cache is None or s.key not in cache]
    computed: dict[str, torch.Tensor] = {}
    if missing:
        with torch.no_grad():
            scores = reference.forward_batch([s.frame for s in missing])
        for sample, value in zip(missing, scores, strict=True):
            computed[sample.key] = F.log_softmax(value.detach(), dim=-1).cpu()
        if cache is not None:
            cache.update(computed)
    rows = [(computed if cache is None else cache)[s.key] for s in batch]
    padded = torch.full((len(batch), width), -torch.inf)
    for index, row in enumerate(rows):
        padded[index, :row.shape[0]] = row
    return padded.to(device)


def rl_update(model: MStd, reference: MStd, optimizer: torch.optim.Optimizer,
              samples: list[RlSample], anchor: list[tuple[Frame, int]], learning_rate: float,
              config: LossConfig, reference_cache: dict[str, torch.Tensor] | None = None
              ) -> dict[str, Any]:
    """``reference_cache`` keeps each sample's start-model log-probabilities across reuses."""
    if not samples:
        raise BoundaryError("rl_loss", "empty_rl_batch")
    began = time.perf_counter()
    device = model.ids.weight.device
    for parameters in optimizer.param_groups:
        parameters["lr"] = learning_rate
    optimizer.zero_grad(set_to_none=True)
    model.train()
    totals = {"policy": 0.0, "kl": 0.0, "anchor": 0.0, "ratio": 0.0, "clipped": 0}
    frames = [s.frame for s in samples]
    for group in microbatches(frames, list(range(len(frames))), model):
        batch = [samples[i] for i in group]
        packed = _scores(model, [s.frame for s in batch])
        reference_log = _reference(reference, batch, reference_cache, packed.shape[1], device)
        log_probabilities = F.log_softmax(packed, dim=-1)
        actions = torch.tensor([s.action for s in batch], device=device)
        chosen = log_probabilities.gather(1, actions[:, None]).squeeze(1)
        behavior = torch.tensor([math.log(s.behavior_probability) for s in batch],
                                device=device, dtype=chosen.dtype)
        advantage = torch.tensor([s.advantage for s in batch], device=device, dtype=chosen.dtype)
        ratio = torch.exp(chosen - behavior)
        clipped = torch.clamp(ratio, 1 - config.clip, 1 + config.clip)
        policy = -torch.minimum(ratio * advantage, clipped * advantage)
        probabilities = log_probabilities.exp()
        # Padded positions have probability 0; zero them before subtracting so
        # neither the value nor its gradient ever sees (-inf) - (-inf).
        valid = torch.isfinite(log_probabilities) & torch.isfinite(reference_log)
        difference = (log_probabilities.masked_fill(~valid, 0)
                      - reference_log.masked_fill(~valid, 0))
        kl = (probabilities * difference).sum(-1)
        objective = (policy.sum() + config.kl_coef * kl.sum()) / len(samples)
        objective.backward()
        totals["policy"] += float(policy.detach().sum())
        totals["kl"] += float(kl.detach().sum())
        totals["ratio"] += float(ratio.detach().sum())
        totals["clipped"] += int(((ratio.detach() - 1).abs() > config.clip).sum())
    if anchor and config.anchor_coef > 0:
        anchor_frames = [frame for frame, _ in anchor]
        for group in microbatches(anchor_frames, list(range(len(anchor))), model):
            packed = _scores(model, [anchor_frames[i] for i in group])
            labels = torch.tensor([anchor[i][1] for i in group], device=device)
            loss = F.cross_entropy(packed, labels, reduction="sum")
            (config.anchor_coef * loss / len(anchor)).backward()
            totals["anchor"] += float(loss.detach())
    gradients = [p.grad for p in model.parameters() if p.grad is not None]
    norm = float(torch.nn.utils.clip_grad_norm_(model.parameters(), config.max_grad_norm))
    if not gradients or not math.isfinite(norm) or not all(
            math.isfinite(totals[k]) for k in ("policy", "kl", "anchor")):
        raise BoundaryError("rl_loss", "non_finite_update")
    optimizer.step()
    count = len(samples)
    return {"policy_loss": totals["policy"] / count, "kl": totals["kl"] / count,
            "anchor_loss": totals["anchor"] / len(anchor) if anchor else None,
            "mean_ratio": totals["ratio"] / count, "clipped_fraction": totals["clipped"] / count,
            "gradient_norm": norm, "rl_samples": count, "anchor_samples": len(anchor),
            "update_seconds": time.perf_counter() - began}
