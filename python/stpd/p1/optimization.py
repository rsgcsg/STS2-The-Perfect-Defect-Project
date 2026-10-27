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
    targets: list[int], learning_rate: float, *, weights: list[float] | None = None,
    value_targets: list[tuple[float | None, float | None]] | None = None,
) -> dict[str, Any]:
    if not frames or len(frames) != len(targets):
        raise BoundaryError("p1_training", "optimizer_batch_length_mismatch")
    if weights is not None and (len(weights) != len(frames) or any(
            type(w) not in {int, float} or not math.isfinite(w) or w <= 0 for w in weights)):
        raise BoundaryError("p1_training", "invalid_loss_weights")
    if model.value_heads != (value_targets is not None):
        raise BoundaryError("p1_training", "value_target_protocol_mismatch")
    if value_targets is not None and len(value_targets) != len(frames):
        raise BoundaryError("p1_training", "value_target_length_mismatch")
    # Preserve the original expression and RNG exactly for the all-one control.
    weighted = weights is not None and any(w != 1 for w in weights)
    began = time.perf_counter()
    device = model.ids.weight.device
    for parameter_group in optimizer.param_groups:
        parameter_group["lr"] = learning_rate
    optimizer.zero_grad(set_to_none=True)
    model.train()
    losses: list[torch.Tensor] = []
    finite_scores: list[torch.Tensor] = []
    sizes = []
    auxiliary_losses: list[torch.Tensor] = []
    for group in microbatches(frames, list(range(len(frames))), model):
        sizes.append(len(group))
        batch_frames = [frames[index] for index in group]
        values = None
        if value_targets is not None:
            scores, values = model.forward_with_values(
                batch_frames, check_finite=not model.execution.defer_score_sync)
            finite_scores.append(torch.isfinite(values).all())
        else:
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
        if weighted:
            assert weights is not None
            loss = loss * torch.tensor([weights[i] for i in group], device=device,
                                       dtype=loss.dtype)
        objective = loss.sum() / len(frames)
        if values is not None and value_targets is not None:
            for head in range(2):
                selected = [j for j, i in enumerate(group) if value_targets[i][head] is not None]
                if selected:
                    labels_value = torch.tensor(
                        [value_targets[group[j]][head] for j in selected], device=device)
                    predictions = values[selected, head]
                    auxiliary = (F.binary_cross_entropy_with_logits(
                        predictions, labels_value, reduction="none") if head == 0 else
                        F.mse_loss(predictions.sigmoid(), labels_value, reduction="none"))
                    if weighted:
                        assert weights is not None
                        auxiliary = auxiliary * torch.tensor(
                            [weights[group[j]] for j in selected], device=device)
                    term = 0.01 * auxiliary.sum() / len(frames)
                    objective = objective + term
                    auxiliary_losses.append(term.detach())
        objective.backward()
        losses.extend(loss.detach().unbind())
    if auxiliary_losses and not bool(torch.isfinite(torch.stack(auxiliary_losses)).all()):
        raise BoundaryError("p1_training", "non_finite_auxiliary_loss")
    if finite_scores:
        checked_values = torch.cat((torch.stack(losses), torch.stack(finite_scores))).cpu().tolist()
        loss_values = checked_values[:len(losses)]
        if not all(checked_values[len(losses):]):
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
            "train_seconds": time.perf_counter() - began, "microbatch_sizes": sizes,
            **({"auxiliary_loss": sum(float(x) for x in auxiliary_losses)}
               if value_targets is not None else {})}
