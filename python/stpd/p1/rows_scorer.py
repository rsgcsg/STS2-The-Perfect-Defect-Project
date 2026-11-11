"""fast_chain inference forward: rows-v2 tensor forward over decoded rows (WP4).

A thin wrapper; the model function stays in `model.py` / `rows_forward.py`
(C-SCALE). It runs exactly what the rows-v2 learner runs
(`prepare_group(layout="rows-v2")` then `MStd.forward_prepared_rl4`), in eval
mode, optionally under BF16 autocast on CUDA, optionally with the compiled
per-bucket block. CPU preparation (`prepare`) and the device forward
(`forward`) are separate so a service can prepare the next batch while the
current one is on the GPU.

Scores differ from the action forward (`MStd.forward_rl4`) by float
accumulation order and BF16 (R4: reported, not a stop). `compare_action_forward`
measures that difference.
"""
from __future__ import annotations

from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, cast

import torch

from .tensor_input import TensorFrame


@dataclass(frozen=True)
class RowScores:
    """One row's catalog logits (catalog order) and outcome probabilities."""
    scores: list[float]
    outcome_probabilities: list[float]
    hp: float


def prepare(frames: Sequence[TensorFrame], floors: Sequence[int]) -> Any:
    """CPU-only rows-v2 group for one batch (no model or device access)."""
    from stpd.rl.batch_preparation import prepare_group

    if not frames or len(frames) != len(floors):
        raise ValueError("rows_scorer_batch_mismatch")
    return prepare_group(list(frames), tuple(range(len(frames))), True,
                         torch.tensor(list(floors), dtype=torch.long),
                         deduplicate_text=True, layout="rows-v2")


class RowsScorer:
    """Score prepared rows-v2 groups with one immutable mu on one device."""

    def __init__(self, model: Any, *, bf16: bool = True, compile: bool = False,
                 kernels: str = "") -> None:
        if model.training or any(p.requires_grad for p in model.parameters()):
            raise ValueError("rows_scorer_requires_immutable_eval_snapshot")
        if not hasattr(model, "rl4_heads"):
            raise ValueError("rl4_heads_not_enabled")
        from stpd.p1.rows_kernels import parse_kernels

        parse_kernels(kernels)  # '' (the original block) or a valid lever set; loud otherwise
        self.model = model
        self.device = next(model.parameters()).device
        self.bf16 = bf16 and self.device.type == "cuda"
        self.compile = compile
        self.kernels = kernels

    def forward_device(self, group: Any) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """The model call alone (caller holds inference mode and autocast): device
        (scores, outcome logits, hp)."""
        if self.kernels:
            return cast(tuple[torch.Tensor, torch.Tensor, torch.Tensor],
                        self.model.forward_prepared_rl4(group, compile_rows=self.compile,
                                                        kernels=self.kernels))
        return cast(tuple[torch.Tensor, torch.Tensor, torch.Tensor],
                    self.model.forward_prepared_rl4(group, compile_rows=self.compile))

    def forward_tensors(self, group: Any) -> tuple[torch.Tensor, ...]:
        """Device forward only: CPU float (scores, outcome probabilities, logits, hp)."""
        autocast = (torch.autocast("cuda", dtype=torch.bfloat16) if self.bf16
                    else nullcontext())
        with torch.inference_mode(), autocast:
            scores, logits, hp = self.forward_device(group)
            probabilities = logits.float().softmax(-1)
        return (scores.float().cpu(), probabilities.cpu(), logits.float().cpu(),
                hp.float().cpu())

    @staticmethod
    def rows(tensors: tuple[torch.Tensor, ...], catalog_lengths: Sequence[int]
             ) -> list[RowScores | None]:
        """Per-row scores from `forward_tensors`; None for a row that is not finite."""
        scores_cpu, probabilities_cpu, logits_cpu, hp_cpu = tensors
        count_rows = len(catalog_lengths)
        out: list[RowScores | None] = []
        if count_rows == 0:
            return out
        # One vectorized pass per batch (was ~5 small tensor ops per row); the
        # values and the finiteness rule are unchanged: real catalog positions
        # finite, outcome logits never NaN or +inf, hp finite.
        scores_cpu = scores_cpu[:count_rows]
        width = scores_cpu.shape[1]
        counts = torch.tensor(list(catalog_lengths), dtype=torch.long)
        real = torch.arange(width).unsqueeze(0) < counts.unsqueeze(1)
        bad = (~torch.isfinite(scores_cpu) & real).any(1)
        logits = logits_cpu[:count_rows].reshape(count_rows, -1)
        bad |= (logits.isnan() | logits.isposinf()).any(1)
        bad |= ~torch.isfinite(hp_cpu[:count_rows].reshape(count_rows))
        scores_list = scores_cpu.tolist()
        probabilities_list = probabilities_cpu[:count_rows].tolist()
        hp_list = hp_cpu[:count_rows].reshape(count_rows).tolist()
        for row, (count, failed) in enumerate(zip(catalog_lengths, bad.tolist(), strict=True)):
            if count < 1 or failed:  # an empty catalog is never a decision
                out.append(None)
                continue
            out.append(RowScores(scores_list[row][:count], probabilities_list[row],
                                 hp_list[row]))
        return out

    def forward(self, group: Any, catalog_lengths: Sequence[int]) -> list[RowScores | None]:
        """Scores per row; None for a row whose outputs are not finite."""
        return self.rows(self.forward_tensors(group), catalog_lengths)

    def score(self, frames: Sequence[TensorFrame], floors: Sequence[int]
              ) -> list[RowScores | None]:
        return self.forward(prepare(frames, floors), [len(f.choices) for f in frames])


def compare_action_forward(model: Any, frames: Sequence[TensorFrame], floors: Sequence[int],
                           rows: Sequence[RowScores | None]) -> dict[str, float]:
    """R4: max |tensor forward - action forward| over scores and outcome probabilities."""
    with torch.inference_mode():
        scores, logits, _ = model.forward_rl4(list(frames), list(floors), check_finite=True)
        probabilities = logits.float().softmax(-1).cpu()
    score_gap = probability_gap = 0.0
    for index, row in enumerate(rows):
        if row is None:
            continue
        reference = scores[index].float().cpu()
        score_gap = max(score_gap, float((torch.tensor(row.scores) - reference).abs().max()))
        probability_gap = max(probability_gap, float(
            (torch.tensor(row.outcome_probabilities) - probabilities[index]).abs().max()))
    return {"max_score_abs_diff": score_gap, "max_outcome_probability_abs_diff": probability_gap}
