"""Value/HP head diagnostics of the live PPO update, from tensors the loss already holds.

`value_statistics` turns one physical group's outcome logits, HP predictions and
targets into additive sums: per row bucket and head, `FIELDS` below. No forward pass,
no gradient and no host read of its own: the sums ride along with the loss totals
(the data-parallel all-reduce and the one pre-step device read of `prepared_update`).
Sums pool exactly over groups, ranks, steps and windows, so a reader computes the
explained variance of any span with `summarize` (the definitions and key names of
`stpd.evaluation.value_quality._stats`).

Heads and targets (prediction minus target is the error, as in value_quality):
- "td": scalar value of the outcome distribution against the categorical lambda-return
  target, on the PPO rows;
- "terminal": the same against the one-hot final class, on the terminal aux rows
  (the final-outcome check);
- "hp": the HP head against public post-combat HP / 100, on the HP aux rows.

Scalar value = probabilities x the A0 class reward table (reward v2.1). It is exact for
A0-A9; at A10 the floor term of both prediction and target is (49/48)^2 too large, the
same scale on both sides. Buckets need the row's floor, which the prepared tensors do
not carry directly; for classes 0-48 `outcome_valid` is `class >= min(floor, 48)`, so the
observed floor is 49 minus the valid count (capped at 48). There is no in-combat tensor,
so "boss_floor" is every row on a Boss floor (17, 33, 48+): the Boss fight and the other
rows of that floor (its reward screen), not the fight alone.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from torch import Tensor

BUCKETS = ("all", "act1", "act2", "act3", "boss_floor")
HEADS = ("td", "terminal", "hp")
FIELDS = ("rows", "target_sum", "target_square_sum", "error_sum", "error_square_sum",
          "abs_error_sum")
SIZE = len(BUCKETS) * len(HEADS) * len(FIELDS)
_VALUES: dict[Any, Tensor] = {}


def _values(device: Any) -> Tensor:
    """The A0 class reward table on `device`, copied once (no per-step host copy)."""
    if device not in _VALUES:
        from .heads import class_reward_values

        _VALUES[device] = class_reward_values(device=device)
    return _VALUES[device]


def row_floors(outcome_valid: Tensor) -> Tensor:
    """Observed floor per row, capped at 48, from the prepared outcome mask."""
    return 49 - outcome_valid[:, :49].sum(-1)


def value_statistics(logits: Tensor, hp: Tensor, t: dict[str, Tensor]) -> Tensor:
    """[SIZE] float32 sums of one group, laid out bucket-major, then head, then field.

    Elementwise ops and sums only (no matmul), so an autocast region cannot lower them
    to BF16.
    """
    import torch

    with torch.no_grad():
        valid = t["outcome_valid"]
        values = _values(logits.device)
        floor = row_floors(valid)
        buckets = torch.stack((torch.ones_like(floor, dtype=torch.bool), floor <= 17,
                               (floor > 17) & (floor <= 33), floor > 33,
                               (floor == 17) | (floor == 33) | (floor >= 48)), dim=1).float()
        probability = logits.float().masked_fill(~valid, -torch.inf).softmax(-1)
        value = (probability * values).sum(-1)
        target = (t["targets"].float() * values).sum(-1)
        columns = []
        for mask, predicted, truth in ((t["td_mask"], value, target),
                                       (t["terminal_mask"], value, target),
                                       (t["hp_mask"], hp.float(), t["hp_targets"].float())):
            mask = mask.float()
            # Masked rows (other kinds) may hold any finite number: zero them first.
            truth = torch.where(mask > 0, truth, torch.zeros_like(truth))
            error = torch.where(mask > 0, predicted - truth, torch.zeros_like(truth))
            columns += [mask, truth, truth * truth, error, error * error, error.abs()]
        features = torch.stack(columns, dim=1)
        return (buckets[:, :, None] * features[:, None, :]).sum(0).flatten()


def statistics_fields(values: list[float]) -> dict[str, dict[str, list[float]]]:
    """The event form: {bucket: {head: [FIELDS...]}}, 8 significant digits (float32)."""
    if len(values) != SIZE:
        raise ValueError("value_statistics_size_mismatch")
    width = len(FIELDS)
    out: dict[str, dict[str, list[float]]] = {}
    for b, bucket in enumerate(BUCKETS):
        out[bucket] = {}
        for h, head in enumerate(HEADS):
            start = (b * len(HEADS) + h) * width
            out[bucket][head] = [float(f"{v:.8g}") for v in values[start:start + width]]
    return out


def add(total: dict[str, dict[str, list[float]]] | None,
        part: dict[str, dict[str, list[float]]]) -> dict[str, dict[str, list[float]]]:
    """Pool two event forms (exact: every field is a sum)."""
    if total is None:
        return {b: {h: list(v) for h, v in heads.items()} for b, heads in part.items()}
    for bucket, heads in part.items():
        for head, values in heads.items():
            row = total.setdefault(bucket, {}).setdefault(head, [0.0] * len(FIELDS))
            for i, value in enumerate(values):
                row[i] += value
    return total


def summarize(sums: list[float], *, hp_scale: float = 1.0) -> dict[str, Any]:
    """value_quality._stats keys from one head's FIELDS sums; plus MAE.

    `hp_scale` converts units (100 for the HP head: HP points instead of HP / 100).
    """
    rows, target, square, error, error_square, absolute = sums
    if rows <= 0:
        return {"rows": 0, "mean_prediction": None, "mean_target": None,
                "bias_prediction_minus_target": None, "mse": None, "constant_mse": None,
                "explained_variance": None, "mae": None}
    mean_target, mean_error = target / rows, error / rows
    # float32 sums: a variance below 1e-6 of the second moment is rounding, not spread.
    variance = square / rows - mean_target ** 2
    variance = variance if variance > 1e-6 * square / rows else 0.0
    residual = max(error_square / rows - mean_error ** 2, 0.0)
    s = hp_scale
    return {"rows": round(rows), "mean_prediction": (mean_target + mean_error) * s,
            "mean_target": mean_target * s, "bias_prediction_minus_target": mean_error * s,
            "mse": error_square / rows * s * s, "constant_mse": variance * s * s,
            "explained_variance": 1 - residual / variance if variance > 0 else None,
            "mae": absolute / rows * s}


def summary(statistics: dict[str, dict[str, list[float]]]) -> dict[str, dict[str, Any]]:
    """{bucket: {head: summarize(...)}}; the HP head in HP points."""
    return {bucket: {head: summarize(values, hp_scale=100.0 if head == "hp" else 1.0)
                     for head, values in heads.items()}
            for bucket, heads in statistics.items()}


def advantage_fields(normalization: Any) -> dict[str, Any]:
    """A dataset's multi-choice advantages before the one normalization
    (`prepared_batch.AdvantageNormalization`: mean and population std, floor 1e-8);
    with c_hp > 0 that advantage already includes c_hp * A_hp (fast_source)."""
    return {"multi_rows": normalization.multi_rows, "mean": normalization.mean,
            "std": normalization.scale}


def headline(statistics: dict[str, dict[str, list[float]]]) -> dict[str, float | None]:
    """The few per-step scalars (all rows): EV vs lambda-return and vs final outcome,
    value MSE, HP MAE in HP points."""
    whole = summary({"all": statistics["all"]})["all"]
    return {"value_explained_variance": whole["td"]["explained_variance"],
            "value_mse": whole["td"]["mse"],
            "outcome_explained_variance": whole["terminal"]["explained_variance"],
            "hp_mae_points": whole["hp"]["mae"]}
