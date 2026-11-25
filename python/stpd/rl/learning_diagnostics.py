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

Calibration (RL5-METHODS R): after those sums, `value_statistics` appends per bin of
the predicted scalar value (10 equal bins over [0, 1), the last one closed) the terminal
aux rows, their predicted-value sum and their realised-reward sum (the one-hot final
class x the same table). Terminal aux rows are rows of games that have ended (64 per
clean-closed game, uniform depth), so this exists under both `segments.value_target`
settings; it is empty when no terminal pool runs (terminal_coef 0).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from torch import Tensor

BUCKETS = ("all", "act1", "act2", "act3", "boss_floor")
HEADS = ("td", "terminal", "hp")
FIELDS = ("rows", "target_sum", "target_square_sum", "error_sum", "error_square_sum",
          "abs_error_sum")
STATISTICS_SIZE = len(BUCKETS) * len(HEADS) * len(FIELDS)
CALIBRATION_BINS = 10
CALIBRATION_FIELDS = ("rows", "predicted_sum", "realised_sum")
CALIBRATION_SIZE = CALIBRATION_BINS * len(CALIBRATION_FIELDS)
SIZE = STATISTICS_SIZE + CALIBRATION_SIZE  # value_statistics' vector: sums, then calibration
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


def _row_terms(logits: Tensor, hp: Tensor, t: dict[str, Tensor]
               ) -> tuple[Tensor, Tensor, Tensor]:
    """Per row: bucket membership [rows, BUCKETS], head columns [rows, HEADS x FIELDS] and
    calibration terms [rows, CALIBRATION_SIZE] (bin-major), whose row sums are the sums
    of `value_statistics`. Caller holds no_grad."""
    import torch

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
    terminal = t["terminal_mask"] > 0
    # NaN goes to bin 0 on every platform (the step then fails as non-finite).
    bins = (value.nan_to_num(0.0).clamp(0, 1) * CALIBRATION_BINS).floor().clamp(
        max=CALIBRATION_BINS - 1).long()
    member = torch.nn.functional.one_hot(bins, CALIBRATION_BINS).float()
    member = torch.where(terminal[:, None], member, torch.zeros_like(member))
    predicted = torch.where(terminal, value, torch.zeros_like(value))
    realised = torch.where(terminal, target, torch.zeros_like(target))
    calibration = torch.stack((member, member * predicted[:, None],
                               member * realised[:, None]), dim=2).flatten(1)
    return buckets, torch.stack(columns, dim=1), calibration


def value_statistics(logits: Tensor, hp: Tensor, t: dict[str, Tensor]) -> Tensor:
    """[SIZE] float32 sums of one group, laid out bucket-major, then head, then field.

    Elementwise ops and sums only (no matmul), so an autocast region cannot lower them
    to BF16.
    """
    import torch

    with torch.no_grad():
        buckets, features, calibration = _row_terms(logits, hp, t)
        sums = (buckets[:, :, None] * features[:, None, :]).sum(0).flatten()
        return torch.cat((sums, calibration.sum(0)))


def statistics_fields(values: list[float]) -> dict[str, dict[str, list[float]]]:
    """The event form: {bucket: {head: [FIELDS...]}}, 8 significant digits (float32), of
    the first STATISTICS_SIZE values of a SIZE vector (the calibration tail is
    `calibration_fields`)."""
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


def calibration_fields(values: list[float]) -> list[list[float]]:
    """The calibration tail of a SIZE vector: per bin [CALIBRATION_FIELDS...]."""
    if len(values) != SIZE:
        raise ValueError("value_statistics_size_mismatch")
    width = len(CALIBRATION_FIELDS)
    tail = values[STATISTICS_SIZE:]
    return [[float(f"{v:.8g}") for v in tail[b * width:(b + 1) * width]]
            for b in range(CALIBRATION_BINS)]


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
    with c_hp > 0 that advantage already includes c_hp * A_hp (fast_source), and
    `hp_std` / `hp_share` give that part's std and its share of Var(A) (None at c_hp 0)."""
    return {"multi_rows": normalization.multi_rows, "mean": normalization.mean,
            "std": normalization.scale, "variance": normalization.scale ** 2,
            "hp_std": normalization.hp_std, "hp_share": normalization.hp_share}


def headline(statistics: dict[str, dict[str, list[float]]]) -> dict[str, float | None]:
    """The few per-step scalars (all rows): EV vs lambda-return and vs final outcome,
    value MSE, HP MAE in HP points."""
    whole = summary({"all": statistics["all"]})["all"]
    return {"value_explained_variance": whole["td"]["explained_variance"],
            "value_mse": whole["td"]["mse"],
            "outcome_explained_variance": whole["terminal"]["explained_variance"],
            "hp_mae_points": whole["hp"]["mae"]}


# Exploration dose (RL5-METHODS A3), one fold per training dataset. Each fast-chain
# source counts its multi-choice rows once (fast_source.decode_fast_packet) and puts the
# counts on its first row's `metadata["exploration"]`: per arm, act and decision type,
# the chosen action's rank in the model's own ordering, whether the model's top
# probability exceeds 0.99 and whether the row's advantage (before the one
# normalisation) is positive. Read from the public frame and the recorded q only:
# nothing reaches the model, the loss or the prepared digest.
DECISIONS = ("combat", "card_reward", "map", "event", "rest", "shop", "treasure", "reward",
             "other")
# Noncombat decision type = the first of these that one of the row's choices has
# (RETHINK-C: a card reward is a reward screen that offers `reward.take_card`).
_DECISION_PREFIXES = (("card_reward.", "reward.take_card"), ("map.",), ("event.",),
                      ("rest.",), ("shop.",), ("treasure.",), ("reward.",))
EXPLORATION_FIELDS = ("rows", "second", "third_plus", "top_p_over_099", "nontop_positive")


def decision_codes(actions: list[str]) -> tuple[int, ...]:
    """Vocabulary action names -> noncombat decision code (index into DECISIONS);
    a row's type is the smallest code among its choices."""
    out = []
    for name in actions:
        code = next((i + 1 for i, prefixes in enumerate(_DECISION_PREFIXES)
                     if name.startswith(prefixes)), len(DECISIONS) - 1)
        out.append(code)
    return tuple(out)


def exploration_row(q: list[float], selected: int, temperature: float, uniform_mix: float,
                    ) -> tuple[int, bool]:
    """(rank of the chosen action: 0 top, 1 second, 2 third or later; the model's top
    probability > 0.99) from the recorded q of one row.

    q = (1-eps) p^(1/T) / sum + eps/N is strictly increasing in p, so the rank in q is
    the rank in the model's p; p is recovered as ((q - eps/N) / (1-eps))^T normalised
    (T = 1, eps = 0: p = q). A greedy row (T = 0) keeps p unknown: q is one-hot at the
    model's top (rank 0), and its top-p flag is counted False (not measured).
    """
    chosen = q[selected]
    rank = min(sum(value > chosen for value in q), 2)
    if temperature == 0:
        return rank, False
    share = uniform_mix / len(q)
    tempered = [max(value - share, 0.0) ** temperature for value in q]
    return rank, max(tempered) > 0.99 * sum(tempered)


def row_entropy(q: list[float], temperature: float, uniform_mix: float
                ) -> tuple[float, float]:
    """(entropy in nats, entropy / ln N) of the model's own p over a row's N >= 2 choices,
    recovered from the recorded q as in `exploration_row` (T = 1, eps = 0: p = q). A greedy
    row (T = 0) has no recoverable p: refused (fast_source leaves such rows out)."""
    if temperature == 0:
        raise ValueError("row_entropy_of_a_greedy_row")
    share = uniform_mix / len(q)
    tempered = [max(value - share, 0.0) ** temperature for value in q]
    total = sum(tempered)
    entropy = -sum(x / total * math.log(x / total) for x in tempered if x > 0)
    return entropy, entropy / math.log(len(q))


# Rare noncombat choices (user 2026-11-24), per "arm|decision": multi-choice noncombat rows
# whose chosen option had model probability (T = 1, recovered from q as in
# `exploration_row`) below LOW_PROBABILITY; greedy rows (T = 0) are left out (p unknown).
# Per key: the rows, the sum of their advantage before the one normalisation (the
# current A, c_hp part included), the rows whose game's real reward is known (the
# fast-source envelope's `game_reward`: "game" value targets and a terminal end) and the
# sum over those of real reward - the row's recorded V (outcome probabilities x reward
# table). Each source names its game (`game_key`) so the readout counts distinct games.
# Metadata only: nothing reaches the model, the loss or the prepared digest.
LOW_PROBABILITY = 0.05
LOW_PROBABILITY_FIELDS = ("rows", "advantage_sum", "outcome_rows", "outcome_minus_value_sum")


def chosen_model_probability(q: list[float], selected: int, temperature: float,
                             uniform_mix: float) -> float:
    """The model's own T = 1 probability of the chosen option, recovered from the recorded
    q as in `exploration_row` (T = 1, eps = 0: q itself). Not defined for T = 0."""
    if temperature == 0:
        raise ValueError("chosen_model_probability_of_a_greedy_row")
    share = uniform_mix / len(q)
    tempered = [max(value - share, 0.0) ** temperature for value in q]
    return float(tempered[selected] / sum(tempered))


def add_low_probability(table: dict[str, list[float]], arm: str, decision: int,
                        advantage: float, outcome_minus_value: float | None) -> None:
    row = table.setdefault(f"{arm}|{DECISIONS[decision]}", [0, 0.0, 0, 0.0])
    row[0] += 1
    row[1] += advantage
    if outcome_minus_value is not None:
        row[2] += 1
        row[3] += outcome_minus_value


def game_key(game_instance_id: str) -> str:
    """A short stable name of one game for distinct counts (not the id itself)."""
    import hashlib

    return hashlib.sha256(game_instance_id.encode()).hexdigest()[:16]


def low_probability_fields(metadata: Any) -> dict[str, Any] | None:
    """One dataset's rare noncombat choices: per "arm|decision" the summed
    `LOW_PROBABILITY_FIELDS` and the sorted distinct `game_key`s; None without a row."""
    rows: dict[str, list[float]] = {}
    games: dict[str, set[str]] = {}
    for meta in metadata:
        part = meta.get("low_probability_noncombat")
        if not part:
            continue
        for key, values in part["rows"].items():
            total = rows.setdefault(key, [0, 0.0, 0, 0.0])
            for i, value in enumerate(values):
                total[i] += value
            games.setdefault(key, set()).add(part["game"])
    if not rows:
        return None
    return {"rows": rows, "games": {key: sorted(names) for key, names in games.items()}}


# Policy entropy (RL5-METHODS R) per "arm|actN|decision", the exploration key: the
# multi-choice rows, the sum of the model's entropy (nats) and of entropy / ln N.
ENTROPY_FIELDS = ("rows", "entropy_sum", "normalized_entropy_sum")


def add_entropy(counts: dict[str, list[float]], arm: str, act: int, decision: int,
                entropy: tuple[float, float]) -> None:
    row = counts.setdefault(f"{arm}|act{act}|{DECISIONS[decision]}", [0, 0.0, 0.0])
    row[0] += 1
    row[1] += entropy[0]
    row[2] += entropy[1]


def entropy_fields(metadata: Any) -> dict[str, list[float]]:
    """One dataset's policy entropy sums: the sum of its sources'
    `metadata["policy_entropy"]` (fast_source.decode_fast_packet)."""
    out: dict[str, list[float]] = {}
    for row in metadata:
        for key, values in (row.get("policy_entropy") or {}).items():
            total = out.setdefault(key, [0, 0.0, 0.0])
            for i, value in enumerate(values):
                total[i] += value
    return out


def add_exploration(counts: dict[str, list[int]], arm: str, act: int, decision: int,
                    rank: int, top: bool, positive: bool) -> None:
    """Count one multi-choice row into {"arm|actN|decision": [EXPLORATION_FIELDS...]}."""
    row = counts.setdefault(f"{arm}|act{act}|{DECISIONS[decision]}", [0, 0, 0, 0, 0])
    row[0] += 1
    row[1] += rank == 1
    row[2] += rank == 2
    row[3] += top
    row[4] += rank > 0 and positive


def value_target_fields(metadata: Any) -> dict[str, Any]:
    """One dataset's share of target weight on the real outcome (RL5-METHODS V): the
    sum of its sources' `metadata["value_target"]` ([rows, weight sum, noncombat rows,
    noncombat weight sum], fast_source.decode_fast_packet); per row the weight is the
    target's mass on the terminal one-hot (lambda^(rows to the trace end), 0 when the
    trace ends in a bootstrap). Means are None without rows.

    RL5-METHODS R: `target_entropy_mean` = the mean entropy (nats; ln 51 = 3.93 is
    uniform) of the rows' categorical value targets, from each source's
    `metadata["target_entropy"]` ([rows, entropy sum])."""
    total = [0.0, 0.0, 0.0, 0.0]
    entropy = [0.0, 0.0]
    for row in metadata:
        counts = row.get("value_target")
        for i, value in enumerate(counts or ()):
            total[i] += value
        for i, value in enumerate(row.get("target_entropy") or ()):
            entropy[i] += value
    return {"rows": int(total[0]),
            "outcome_weight_mean": total[1] / total[0] if total[0] else None,
            "noncombat_rows": int(total[2]),
            "noncombat_outcome_weight_mean": total[3] / total[2] if total[2] else None,
            "target_entropy_mean": entropy[1] / entropy[0] if entropy[0] else None}


def potion_discard_fields(metadata: Any) -> dict[str, list[int]]:
    """One dataset's noncombat potion discards per arm: [multi-choice noncombat rows offering
    `potion.discard`, rows choosing it], summed over its sources (recipe A readout)."""
    out: dict[str, list[int]] = {}
    for row in metadata:
        for arm, values in (row.get("potion_discard") or {}).items():
            total = out.setdefault(arm, [0, 0])
            total[0] += values[0]
            total[1] += values[1]
    return out


def exploration_fields(metadata: Any) -> dict[str, list[int]]:
    """One dataset's counts: the sum of its sources' `metadata["exploration"]`."""
    out: dict[str, list[int]] = {}
    for row in metadata:
        counts = row.get("exploration")
        for key, values in (counts or {}).items():
            total = out.setdefault(key, [0, 0, 0, 0, 0])
            for i, value in enumerate(values):
                total[i] += value
    return out


# PPO update per exploration arm (RL5-METHODS R): the per-row policy facts the update
# already returns (`_policy_rows`: ratio and outside-clip of every multi-choice row,
# pre-step) summed per arm. The arm is derived, not carried: sources are whole and in
# order in a dataset, and every source with a multi-choice row names its arm in its first
# row's exploration table, so a row's arm is that of the last table at or before it.
# Diagnostics only: computed on the host after the step, never part of the loss.
POLICY_ARM_FIELDS = ("rows", "approx_kl_sum", "outside_clip", "ratio_nonfinite")
UNATTRIBUTED = "unattributed"


def row_arms(metadata: Any, sources: Any) -> list[str | None]:
    """Per dataset row, its source's arm: the exploration table on the source's first row
    (fast_source), None for a source without one. `sources` = each source's row count, in
    dataset order (the batch request's members); they must cover the metadata exactly."""
    out: list[str | None] = []
    for rows in sources:
        counts = metadata[len(out)].get("exploration") if rows else None
        arm = next(iter(counts)).split("|", 1)[0] if counts else None
        out.extend([arm] * rows)
    if len(out) != len(metadata):
        raise ValueError(f"row_arms: sources cover {len(out)} rows, metadata has "
                         f"{len(metadata)}")
    return out


def policy_by_arm(arms: list[str | None],
                  samples: list[dict[str, Any]]) -> dict[str, list[float]]:
    """{arm: [POLICY_ARM_FIELDS...]} over one attempt's policy rows. approx KL per row is
    the loss's r - 1 - log r (its mean over rows = `approx_kl_mu_theta`); a row whose
    ratio is missing, non-finite or 0 counts in `ratio_nonfinite` and adds no KL."""
    out: dict[str, list[float]] = {}
    for sample in samples:
        arm = arms[sample["row"]] or UNATTRIBUTED
        total = out.setdefault(arm, [0, 0.0, 0, 0])
        total[0] += 1
        total[2] += bool(sample["outside_clip"])
        ratio = sample["ratio"]
        if ratio is None or not math.isfinite(ratio) or ratio <= 0:
            total[3] += 1
        else:
            total[1] += ratio - 1 - math.log(ratio)
    return out


# `loss.explore_ratio_floor` c > 0: per arm, over one attempt's policy rows, the rows the
# floor took (exploration arms only, ratio below c), those with A > 0, and over the
# floored rows the sums of |A|, c |A| (the weight given) and the weight the clipped
# surrogate would have given: r |A|, or 0 for a row whose objective the clip bound (A < 0
# below 1 - clip). Diagnostics only, like policy_by_arm.
EXPLORE_FLOOR_FIELDS = ("rows", "floored", "floored_positive", "floored_abs_advantage_sum",
                        "floor_weight_sum", "ratio_weight_sum")


def explore_floor_by_arm(arms: list[str | None], samples: list[dict[str, Any]],
                         floor: float) -> dict[str, list[float]] | None:
    """{arm: [EXPLORE_FLOOR_FIELDS...]}; None when the rows carry no floor facts (c = 0)."""
    if not samples or "explore_floored" not in samples[0]:
        return None
    out: dict[str, list[float]] = {}
    for sample in samples:
        total = out.setdefault(arms[sample["row"]] or UNATTRIBUTED, [0, 0, 0, 0.0, 0.0, 0.0])
        total[0] += 1
        if sample["explore_floored"]:
            magnitude = abs(sample["advantage"])
            total[1] += 1
            total[2] += sample["advantage"] > 0
            total[3] += magnitude
            total[4] += floor * magnitude
            if not sample["ppo_objective_clipped"]:
                total[5] += (sample["ratio"] or 0.0) * magnitude
    return out


# Value heads per exploration cell (owner 2026-11-19). A cell is a sampling config: the
# row's arm without the "+save" suffix (a save start changes the starting state, not the
# config). Diagnostics only, from tensors the loss already holds plus, for the cross query,
# a few head-MLP passes on the detached first-layer activations (no trunk pass).
#
# `value_by_cell` (diagnostics steps): per cell the "all"-bucket head sums of
# `value_statistics` (td, terminal, hp: FIELDS each) and its calibration tail, from the
# same per-row terms, so the cells sum exactly to that step's unsplit statistics.
# Auxiliary (terminal/HP) rows carry no arm; with
# `segments.value_config_input` they take the cell whose PPO rows in the same dataset
# have their recorded config (unique match only), otherwise UNATTRIBUTED.
#
# `value_cross_cell` (value_config_input only, diagnostics steps): the outcome head's
# expected reward of every row at every cell's config: per (row's own cell, queried
# cell) the sum, plus the row count per own cell.
CELL_SIZE = len(HEADS) * len(FIELDS) + CALIBRATION_SIZE


@dataclass(frozen=True)
class ValueCells:
    """One dataset's cells: `names` (sorted, UNATTRIBUTED last if any row lacks one),
    `rows` = each dataset row's cell index (PPO rows, then auxiliary rows `n + local`),
    `configs` = each named cell's value config in `names` order (None: knob off)."""
    names: tuple[str, ...]
    rows: tuple[int, ...]
    configs: tuple[tuple[float, ...], ...] | None


def row_cells(metadata: Any, request: Any,
              configs: list[tuple[float, ...]] | None) -> ValueCells | None:
    """`ValueCells` of a prepared dataset; None when no source names an arm. `configs` =
    `heads.batch_value_configs` of the dataset (value_config_input), else None."""
    arms = row_arms(metadata, [member.rows for member in request.members])
    if not any(arms):
        return None
    names: list[str | None] = [arm.split("+", 1)[0] if arm else None for arm in arms]
    cell_config: dict[str, tuple[float, ...]] = {}
    if configs is not None:
        owners: dict[tuple[float, ...], set[str]] = {}
        for name, config in zip(names, configs, strict=False):
            if name is not None:
                cell_config.setdefault(name, config)
                owners.setdefault(config, set()).add(name)
        unique = {config: next(iter(cells)) for config, cells in owners.items()
                  if len(cells) == 1}
        names += [unique.get(config) for config in configs[len(metadata):]]
    else:
        names += [None] * len(request.auxiliary)
    cells = sorted({name for name in names if name is not None})
    if None in names:
        cells.append(UNATTRIBUTED)
    index = {cell: i for i, cell in enumerate(cells)}
    return ValueCells(tuple(cells), tuple(index[name or UNATTRIBUTED] for name in names),
                      tuple(cell_config[cell] for cell in cells if cell != UNATTRIBUTED)
                      if configs is not None else None)


def value_statistics_by_cell(logits: Tensor, hp: Tensor, t: dict[str, Tensor],
                             cells: Tensor, count: int) -> Tensor:
    """[count x CELL_SIZE] sums of one group: per cell (`cells` = row's cell index) the
    "all" head columns of `value_statistics`, then its calibration terms."""
    import torch

    with torch.no_grad():
        _, features, calibration = _row_terms(logits, hp, t)
        member = torch.nn.functional.one_hot(cells, count).float()
        terms = torch.cat((features, calibration), dim=1)
        return (member[:, :, None] * terms[:, None, :]).sum(0).flatten()


def cross_cell_sums(heads: Any, hidden: Tensor, valid: Tensor, cells: Tensor,
                    configs: Tensor, count: int) -> Tensor:
    """[count x (1 + queries)]: per own cell its rows, then the sum of the outcome head's
    expected reward at each queried config. `hidden` = the outcome MLP's first-layer
    output without the config term (`heads.mlp_readout`), group row order; `configs` =
    [queries, 4]. Same arithmetic as the live head: mlp[1:](hidden + shift)."""
    import torch

    with torch.no_grad():
        shifts = heads.config_shifts(configs, configs.shape[0])[0]
        out = heads.outcome.mlp[1:](hidden[:, None, :] + shifts[None].to(hidden.dtype))
        logits = out.float().masked_fill(~valid[:, None, :], -torch.inf)
        value = (logits.softmax(-1) * _values(hidden.device)).sum(-1)  # [rows, queries]
        member = torch.nn.functional.one_hot(cells, count).float()
        sums = (member[:, :, None] * value[:, None, :]).sum(0)
        return torch.cat((member.sum(0)[:, None], sums), dim=1).flatten()


def cell_fields(values: list[float], names: tuple[str, ...]) -> dict[str, Any]:
    """Event form of `value_statistics_by_cell` sums: {cell: {head: [FIELDS...],
    "calibration": [[CALIBRATION_FIELDS...] per bin]}}, 8 significant digits."""
    if len(values) != len(names) * CELL_SIZE:
        raise ValueError("value_by_cell_size_mismatch")
    width, out = len(FIELDS), {}
    for c, name in enumerate(names):
        part = [float(f"{v:.8g}") for v in values[c * CELL_SIZE:(c + 1) * CELL_SIZE]]
        tail = part[len(HEADS) * width:]
        out[name] = {**{head: part[h * width:(h + 1) * width] for h, head in enumerate(HEADS)},
                     "calibration": [tail[b * 3:(b + 1) * 3] for b in range(CALIBRATION_BINS)]}
    return out


def cross_cell_fields(values: list[float], cells: ValueCells) -> dict[str, Any]:
    """Event form of `cross_cell_sums`: the queried configs, rows per own cell and the
    expected-reward sums per (own cell, queried cell)."""
    assert cells.configs is not None
    queried = [name for name in cells.names if name != UNATTRIBUTED]
    width = 1 + len(queried)
    if len(values) != len(cells.names) * width:
        raise ValueError("value_cross_cell_size_mismatch")
    rows = {own: round(values[c * width]) for c, own in enumerate(cells.names)}
    return {"configs": {name: list(config) for name, config
                        in zip(queried, cells.configs, strict=True)},
            "rows": {own: n for own, n in rows.items() if n},
            "expected_reward_sum": {
                own: {query: float(f"{values[c * width + 1 + q]:.8g}")
                      for q, query in enumerate(queried)}
                for c, own in enumerate(cells.names) if rows[own]}}
