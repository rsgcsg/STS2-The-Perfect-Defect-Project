"""One RL optimizer update: clipped policy gradient + KL to the start model + Human anchor.

Off-policy correction has two parts: PPO clips pi_theta / pi_old, where pi_old
is the acting model's own (untempered) probability recorded in the trajectory,
and a separate truncated importance weight min(pi_old / mu, rho_max) corrects
for the exploration policy mu (temperature + uniform mix) actually sampled.
Advantages are Monte-Carlo episode returns minus a baseline supplied by the
caller; no value head is used.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import torch
from torch.nn import functional as F

from spireagent.json_boundary import BoundaryError
from stpd.p1.execution import microbatches
from stpd.p1.input import Frame
from stpd.p1.model import MStd

if TYPE_CHECKING:
    from stpd.p1.tensor_input import TensorFrame

    from .performance import PerformanceConfig


@dataclass(frozen=True)
class RlSample:
    frame: Frame
    action: int
    behavior_probability: float
    old_probability: float
    advantage: float
    model_version: int
    key: str


@dataclass(frozen=True)
class LossConfig:
    clip: float = 0.2
    kl_coef: float = 0.02
    anchor_coef: float = 0.5
    max_grad_norm: float = 1.0
    rho_max: float = 1.0


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


def _policy_objective(packed: torch.Tensor, reference_log: torch.Tensor,
                      batch: list[RlSample], config: LossConfig, sample_count: int
                      ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """One learning objective shared by execution plans; retain legacy operation order."""
    device = packed.device
    log_probabilities = F.log_softmax(packed, dim=-1)
    actions = torch.tensor([s.action for s in batch], device=device)
    chosen = log_probabilities.gather(1, actions[:, None]).squeeze(1)
    old = torch.tensor([math.log(s.old_probability) for s in batch],
                       device=device, dtype=chosen.dtype)
    weight = torch.tensor([min(s.old_probability / s.behavior_probability, config.rho_max)
                           for s in batch], device=device, dtype=chosen.dtype)
    advantage = torch.tensor([s.advantage for s in batch], device=device, dtype=chosen.dtype)
    ratio = torch.exp(chosen - old)
    clipped = torch.clamp(ratio, 1 - config.clip, 1 + config.clip)
    policy = -weight * torch.minimum(ratio * advantage, clipped * advantage)
    probabilities = log_probabilities.exp()
    # Padded positions have probability 0; zero them before subtracting so
    # neither the value nor its gradient ever sees (-inf) - (-inf).
    valid = torch.isfinite(log_probabilities) & torch.isfinite(reference_log)
    difference = (log_probabilities.masked_fill(~valid, 0)
                  - reference_log.masked_fill(~valid, 0))
    kl = (probabilities * difference).sum(-1)
    objective = (policy.sum() + config.kl_coef * kl.sum()) / sample_count
    return objective, policy, kl, ratio


def rl_update(model: MStd, reference: MStd, optimizer: torch.optim.Optimizer,
              samples: list[RlSample], anchor: list[tuple[Frame, int]], learning_rate: float,
              config: LossConfig, reference_cache: dict[str, torch.Tensor] | None = None,
              *, performance: PerformanceConfig | None = None,
              frame_cache: dict[object, TensorFrame] | None = None
              ) -> dict[str, Any]:
    """``reference_cache`` keeps each sample's start-model log-probabilities across reuses."""
    if performance is not None:
        from .performance import prepare_models

        with prepare_models(model, reference, performance):
            return _execution_update(model, reference, optimizer, samples, anchor, learning_rate,
                                     config, reference_cache, performance, frame_cache)
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
        objective, policy, kl, ratio = _policy_objective(
            packed, reference_log, batch, config, len(samples))
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


def _execution_update(model: MStd, reference: MStd, optimizer: torch.optim.Optimizer,
                      samples: list[RlSample], anchor: list[tuple[Frame, int]],
                      learning_rate: float, config: LossConfig,
                      reference_cache: dict[str, torch.Tensor] | None,
                      performance: PerformanceConfig,
                      frame_cache: dict[object, TensorFrame] | None) -> dict[str, Any]:
    """Same optimizer membership/objective; opt-in execution may change dropout order.

    All losses use FP32 under autocast. Delayed checks still reject invalid
    scores, reference distributions, losses or gradients before optimizer.step.
    """
    from stpd.p1.tensor_input import compile_frame

    from .performance import cache_on_device

    if not samples:
        raise BoundaryError("rl_loss", "empty_rl_batch")
    began = time.perf_counter()
    device = model.ids.weight.device
    cache = frame_cache if frame_cache is not None else {}

    def frame(value: Frame, key: object) -> Any:
        if not performance.tensor_cache:
            return value
        if key not in cache:
            cache[key] = compile_frame(value, model)
        if performance.device_cache and cache[key].tensors["actions"].device != device:
            cache[key] = cache_on_device(cache[key], device)
        return cache[key]

    frames = [frame(s.frame, ("rl", s.key)) for s in samples]
    anchor_frames = [frame(f, ("anchor", id(f))) for f, _ in anchor]

    def groups(values: list[Any]) -> Any:
        indices = list(range(len(values)))
        if performance.bucket:
            indices.sort(key=lambda i: (values[i].entity_count if hasattr(
                values[i], "entity_count") else len(values[i].entities)))
        return microbatches(values, indices, model)

    valid_scores = torch.ones((), device=device, dtype=torch.bool)

    def scores(member: MStd, values: list[Any]) -> list[torch.Tensor]:
        nonlocal valid_scores
        output = member.forward_batch(values, check_finite=not performance.defer_sync)
        if performance.defer_sync:
            valid_scores = valid_scores & torch.isfinite(torch.cat(output)).all()
        return [row.float() for row in output]

    for parameters in optimizer.param_groups:
        parameters["lr"] = learning_rate
    optimizer.zero_grad(set_to_none=True)
    model.train()
    metric_rows = []
    zero = torch.zeros((), device=device)
    for group in groups(frames):
        batch = [samples[i] for i in group]
        output = scores(model, [frames[i] for i in group])
        packed = torch.nn.utils.rnn.pad_sequence(output, batch_first=True,
                                                 padding_value=-torch.inf)
        missing = [i for i in group if reference_cache is None
                   or samples[i].key not in reference_cache]
        computed: dict[str, torch.Tensor] = {}
        if missing:
            with torch.no_grad():
                reference_rows = scores(reference, [frames[i] for i in missing])
                # Optional resident reference cache avoids draining the GPU
                # queue for each new reference microbatch. Rows remain FP32.
                logs = torch.nn.utils.rnn.pad_sequence(
                    [F.log_softmax(row, -1) for row in reference_rows],
                    batch_first=True, padding_value=-torch.inf)
                if not performance.reference_device_cache:
                    logs = logs.cpu()
            computed = {samples[i].key: logs[row, :len(frames[i].choices)]
                        for row, i in enumerate(missing)}
            if reference_cache is not None:
                reference_cache.update(computed)
        rows = [(computed if reference_cache is None else reference_cache)[s.key]
                for s in batch]
        reference_log = torch.nn.utils.rnn.pad_sequence(
            rows, batch_first=True, padding_value=-torch.inf).to(device)
        # Inspect only actual catalog entries: padding is intentionally -inf.
        # A corrupt/non-finite cached reference must not disappear in KL masking.
        valid_scores = valid_scores & (torch.isfinite(reference_log).sum()
                                       == sum(row.numel() for row in rows))
        # The current batch can have a wider action catalog than missing rows.
        if reference_log.shape[1] < packed.shape[1]:
            reference_log = F.pad(reference_log, (0, packed.shape[1] - reference_log.shape[1]),
                                  value=-torch.inf)
        objective, policy, kl, ratio = _policy_objective(
            packed, reference_log, batch, config, len(samples))
        objective.backward()
        metric_rows.append(torch.stack((policy.detach().sum(), kl.detach().sum(), zero,
                                        ratio.detach().sum(),
                                        ((ratio.detach() - 1).abs() > config.clip).sum())))
    if anchor and config.anchor_coef > 0:
        for group in groups(anchor_frames):
            packed = torch.nn.utils.rnn.pad_sequence(
                scores(model, [anchor_frames[i] for i in group]), batch_first=True,
                padding_value=-torch.inf)
            labels = torch.tensor([anchor[i][1] for i in group], device=device)
            loss = F.cross_entropy(packed, labels, reduction="sum")
            (config.anchor_coef * loss / len(anchor)).backward()
            metric_rows.append(torch.stack((zero, zero, loss.detach(), zero, zero)))
    gradients = [p.grad for p in model.parameters() if p.grad is not None]
    norm = torch.nn.utils.clip_grad_norm_(model.parameters(), config.max_grad_norm)
    # Transfer once, then retain the original microbatch-order host accumulation
    # rather than replacing Python double accumulation with a device FP32 sum.
    status = torch.cat((torch.stack(metric_rows).flatten(), norm.detach().reshape(1),
                        valid_scores.float().reshape(1))).cpu().tolist()
    totals = [sum(status[offset:-2:5]) for offset in range(5)]
    policy_total, kl_total, anchor_total, ratio_total, clipped_total = totals
    norm_value, valid_value = status[-2:]
    if (not gradients or not valid_value or not all(math.isfinite(value) for value in (
            policy_total, kl_total, anchor_total, norm_value))):
        raise BoundaryError("rl_loss", "non_finite_update")
    optimizer.step()
    count = len(samples)
    return {"policy_loss": policy_total / count, "kl": kl_total / count,
            "anchor_loss": anchor_total / len(anchor) if anchor else None,
            "mean_ratio": ratio_total / count, "clipped_fraction": clipped_total / count,
            "gradient_norm": norm_value, "rl_samples": count, "anchor_samples": len(anchor),
            "update_seconds": time.perf_counter() - began}
