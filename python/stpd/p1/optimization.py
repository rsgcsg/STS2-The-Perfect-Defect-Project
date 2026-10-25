"""One numeric optimizer update, shared by formal training and bounded calibration.

Callers own the scoped numerical environment, sampler and artifact lifecycle.
No data is omitted and every finite-value check precedes optimizer.step().
"""

from __future__ import annotations

import math
import time
from typing import Any

import torch
from torch.nn import functional as F

from spireagent.json_boundary import BoundaryError

from .execution import microbatches
from .input import Frame
from .model import MStd


def optimizer_update(
    model: MStd, optimizer: torch.optim.Optimizer, frames: list[Frame],
    targets: list[int], learning_rate: float,
) -> dict[str, Any]:
    if not frames or len(frames) != len(targets):
        raise BoundaryError("p1_training", "optimizer_batch_length_mismatch")
    began = time.perf_counter()
    device = model.ids.weight.device
    for parameter_group in optimizer.param_groups:
        parameter_group["lr"] = learning_rate
    optimizer.zero_grad(set_to_none=True)
    model.train()
    losses: list[torch.Tensor] = []
    finite_scores: list[torch.Tensor] = []
    sizes = []
    for group in microbatches(frames, list(range(len(frames))), model):
        sizes.append(len(group))
        batch_frames = [frames[index] for index in group]
        scores = (model.forward_batch(batch_frames, check_finite=False)
                  if model.execution.defer_score_sync else model.forward_batch(batch_frames))
        if model.execution.defer_score_sync:
            # Preserve the full-score guard, including unselected -inf scores
            # whose cross-entropy and gradient can both remain finite. These
            # device flags cause only one host synchronization per update.
            finite_scores.append(torch.isfinite(torch.cat(scores)).all())
        packed = torch.nn.utils.rnn.pad_sequence(scores, batch_first=True, padding_value=-torch.inf)
        labels = torch.tensor([targets[index] for index in group], device=device)
        loss = F.cross_entropy(packed, labels, reduction="none")
        (loss.sum() / len(frames)).backward()
        losses.extend(loss.detach().unbind())
    if finite_scores:
        values = torch.cat((torch.stack(losses), torch.stack(finite_scores))).cpu().tolist()
        loss_values = values[:len(losses)]
        if not all(values[len(losses):]):
            raise BoundaryError("p1_training", "non_finite_scores")
    else:
        loss_values = torch.stack(losses).cpu().tolist()
    if not all(math.isfinite(loss) for loss in loss_values):
        raise BoundaryError("p1_training", "non_finite_loss")
    gradients = [p.grad.norm() for p in model.parameters() if p.grad is not None]
    norm = float(torch.linalg.vector_norm(torch.stack(gradients)))
    if not math.isfinite(norm):
        raise BoundaryError("p1_training", "non_finite_gradient")
    optimizer.step()
    return {"mean_N_loss": sum(loss_values) / len(frames), "gradient_norm": norm,
            "train_seconds": time.perf_counter() - began, "microbatch_sizes": sizes}
