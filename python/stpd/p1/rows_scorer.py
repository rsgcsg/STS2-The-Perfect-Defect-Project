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

import math
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

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

    def __init__(self, model: Any, *, bf16: bool = True, compile: bool = False) -> None:
        if model.training or any(p.requires_grad for p in model.parameters()):
            raise ValueError("rows_scorer_requires_immutable_eval_snapshot")
        if not hasattr(model, "rl4_heads"):
            raise ValueError("rl4_heads_not_enabled")
        self.model = model
        self.device = next(model.parameters()).device
        self.bf16 = bf16 and self.device.type == "cuda"
        self.compile = compile

    def forward(self, group: Any, catalog_lengths: Sequence[int]) -> list[RowScores | None]:
        """Scores per row; None for a row whose outputs are not finite."""
        autocast = (torch.autocast("cuda", dtype=torch.bfloat16) if self.bf16
                    else nullcontext())
        with torch.inference_mode(), autocast:
            scores, logits, hp = self.model.forward_prepared_rl4(group,
                                                                 compile_rows=self.compile)
            probabilities = logits.float().softmax(-1)
        scores_cpu = scores.float().cpu()
        probabilities_cpu = probabilities.cpu()
        logits_cpu = logits.float().cpu()
        hp_cpu = hp.float().cpu()
        out: list[RowScores | None] = []
        for row, count in enumerate(catalog_lengths):
            values = scores_cpu[row, :count]
            # Same finiteness rule as forward_rl4(check_finite=True): real catalog
            # positions finite, outcome logits never NaN or +inf, hp finite.
            if (not bool(torch.isfinite(values).all()) or bool(logits_cpu[row].isnan().any())
                    or bool(logits_cpu[row].isposinf().any())
                    or not math.isfinite(float(hp_cpu[row]))):
                out.append(None)
                continue
            out.append(RowScores(values.tolist(), probabilities_cpu[row].tolist(),
                                 float(hp_cpu[row])))
        return out

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
