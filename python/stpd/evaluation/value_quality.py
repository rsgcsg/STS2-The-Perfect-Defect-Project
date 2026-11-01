"""Current-policy Monte Carlo value diagnostics, never a fitting objective.

The caller binds one frozen model/critic, behavior policy and verified natural
trajectories. Targets are complete terminal returns in raw reward units.
"""
from __future__ import annotations

import math
import random
import statistics
from collections import Counter
from typing import Any


def distribution(values: list[float | None]) -> dict[str, Any]:
    """Equal-game, linearly interpolated quantiles; undefined games stay counted."""
    valid = sorted(v for v in values if v is not None)
    def quantile(q: float) -> float | None:
        if not valid:
            return None
        index = (len(valid) - 1) * q
        low = math.floor(index)
        high = math.ceil(index)
        return valid[low] + (valid[high] - valid[low]) * (index - low)
    return {"total_games": len(values), "valid_games": len(valid),
        "undefined_games": len(values) - len(valid),
        **{name: quantile(q) for name, q in (("min", 0), ("p10", .1), ("p25", .25),
            ("median", .5), ("p75", .75), ("p90", .9), ("max", 1))}}


def temporal_quality(predictions: list[float], targets: list[float]) -> dict[str, Any]:
    """Describe a realized MC path; neither correlation nor smoothness proves skill."""
    if (not predictions or len(predictions) != len(targets)
            or not all(type(v) in (int, float) and math.isfinite(v)
                       for v in predictions + targets)):
        raise ValueError("finite_aligned_temporal_predictions_required")
    def std(values: list[float]) -> float | None:
        return statistics.stdev(values) if len(values) >= 2 else None
    ps, ts = std(predictions), std(targets)
    correlation, correlation_reason = None, None
    if len(targets) < 2:
        correlation_reason = "fewer_than_two_rows"
    elif not ps or not ts:
        correlation_reason = "constant_prediction_or_target"
    else:
        pm, tm = statistics.fmean(predictions), statistics.fmean(targets)
        covariance = math.fsum((p - pm) * (t - tm)
                              for p, t in zip(predictions, targets, strict=True))
        correlation = min(1.0, max(-1.0, covariance / ((len(targets) - 1) * ps * ts)))
    dp = [b - a for a, b in zip(predictions[:-1], predictions[1:], strict=True)]
    dt = [b - a for a, b in zip(targets[:-1], targets[1:], strict=True)]
    pds, tds = std(dp), std(dt)
    jitter, jitter_reason = None, None
    if len(targets) < 3:
        jitter_reason = "fewer_than_three_rows"
    elif tds == 0:
        jitter_reason = "constant_target_differences"
    else:
        assert pds is not None and tds is not None
        jitter = pds / tds
    return {"rows": len(targets), "prediction_std": ps, "target_std": ts,
        "correlation": correlation, "correlation_undefined_reason": correlation_reason,
        "prediction_difference_std": pds, "target_difference_std": tds,
        "jitter_ratio": jitter, "jitter_undefined_reason": jitter_reason,
        "target_differences_near_constant": (tds is not None and
            tds <= max(1e-12, 1e-8 * max(abs(v) for v in targets)))}


def _stats(predicted: list[float], targets: list[float], weights: list[float] | None = None
           ) -> dict[str, Any]:
    if not predicted:
        return {"rows": 0, "mean_prediction": None, "mean_target": None,
                "bias_prediction_minus_target": None, "mse": None,
                "constant_mse": None, "explained_variance": None}
    weights = weights or [1.0] * len(predicted)
    total = math.fsum(weights)
    def mean(values: list[float]) -> float:
        return math.fsum(x * w for x, w in zip(values, weights, strict=True)) / total
    error = [p - t for p, t in zip(predicted, targets, strict=True)]
    target_mean, error_mean = mean(targets), mean(error)
    variance = mean([(t - target_mean) ** 2 for t in targets])
    residual_variance = mean([(e - error_mean) ** 2 for e in error])
    return {"rows": len(predicted), "mean_prediction": mean(predicted),
            "mean_target": target_mean, "bias_prediction_minus_target": error_mean,
            "mse": mean([e * e for e in error]), "constant_mse": variance,
            "explained_variance": 1 - residual_variance / variance if variance > 0 else None}


def summarize(games: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep per-decision and equal-game summaries distinct; never impute unknowns."""
    if not games or len({g["run_id"] for g in games}) != len(games):
        raise ValueError("distinct_complete_natural_games_required")
    predictions, targets, weights = [], [], []
    centered_predictions: list[float] = []
    centered_targets: list[float] = []
    third_predictions: list[list[float]] = [[], [], []]
    third_targets: list[list[float]] = [[], [], []]
    third_biases: list[list[float]] = [[], [], []]
    per_game = []
    for game in games:
        p, t = game["predictions"], game["targets"]
        if (not p or len(p) != len(t)
                or not all(type(x) in (float, int) and math.isfinite(x) for x in p + t)
                or type(game["floor"]) is not int or game["floor"] < 0
                or game["result"] not in ("win", "loss")):
            raise ValueError("finite_complete_predictions_targets_and_natural_outcome_required")
        stats = _stats(p, t)
        per_game.append({k: game[k] for k in ("run_id", "seed", "floor", "result")} | stats
                        | {"temporal": temporal_quality(p, t)})
        predictions.extend(p)
        targets.extend(t)
        weights.extend([1 / len(p)] * len(p))
        centered_predictions.extend(x - stats["mean_prediction"] for x in p)
        centered_targets.extend(x - stats["mean_target"] for x in t)
        for part in range(3):
            indices = [i for i in range(len(p)) if min(2, 3 * i // len(p)) == part]
            ps, ts = [p[i] for i in indices], [t[i] for i in indices]
            third_predictions[part].extend(ps)
            third_targets[part].extend(ts)
            if indices:
                third_biases[part].append(math.fsum(a - b for a, b in zip(ps, ts, strict=True))
                                          / len(indices))
    biases = [g["bias_prediction_minus_target"] for g in per_game]
    bias_sensitivity = None
    if len(games) >= 2:
        rng = random.Random(20261101)
        resampled = sorted(math.fsum(rng.choices(biases, k=len(biases))) / len(biases)
                           for _ in range(2000))
        bias_sensitivity = [resampled[49], resampled[1949]]
    thirds = {}
    for i, name in enumerate(("early", "middle", "late")):
        thirds[name] = {**_stats(third_predictions[i], third_targets[i]),
            "games": len(third_biases[i]),
            "mean_game_bias": (math.fsum(third_biases[i]) / len(third_biases[i])
                               if third_biases[i] else None)}
    edges = [None, 0.0, .025, .05, .075, .1, .15, .25, .5, 1.0, None]
    calibration = []
    for lower, upper in zip(edges[:-1], edges[1:], strict=True):
        ps, ts, ws, runs = [], [], [], set()
        for game in games:
            for p, t in zip(game["predictions"], game["targets"], strict=True):
                if (lower is None or p >= lower) and (upper is None or p < upper):
                    ps.append(p)
                    ts.append(t)
                    ws.append(1 / len(game["predictions"]))
                    runs.add(game["run_id"])
        calibration.append({"lower_inclusive": lower, "upper_exclusive": upper,
            "games": len(runs), **_stats(ps, ts),
            "equal_game_weighted": _stats(ps, ts, ws)})
    floors = sorted(g["floor"] for g in games)
    return {"schema": "stpd/current-policy-value-quality-1", "games": len(games),
        "rows": len(predictions), "per_decision": _stats(predictions, targets),
        "equal_game_weighted": _stats(predictions, targets, weights),
        "mean_game_bias": math.fsum(biases) / len(biases),
        "mean_absolute_game_bias": statistics.fmean(abs(v) for v in biases),
        "absolute_game_bias": distribution([abs(v) for v in biases]),
        "mean_game_bias_bootstrap95_fixed_group_sensitivity": bias_sensitivity,
        "within_game_correlation": distribution([g["temporal"]["correlation"] for g in per_game]),
        "jitter_ratio": distribution([g["temporal"]["jitter_ratio"] for g in per_game]),
        "within_game": _stats(centered_predictions, centered_targets),
        "within_game_equal_game_weighted": _stats(centered_predictions, centered_targets, weights),
        "thirds": thirds, "calibration_fixed_prediction_bins": calibration,
        "per_game": per_game,
        "floor_distribution": {"games": len(games),
            "wins": sum(g["result"] == "win" for g in games),
            "mean": math.fsum(floors) / len(floors),
            "median": (floors[(len(floors) - 1) // 2] + floors[len(floors) // 2]) / 2,
            "min": floors[0], "max": floors[-1], "counts": dict(sorted(Counter(floors).items()))},
        "definitions": {"bias": "prediction - MC target; headline bias weights games equally",
            "EV": "1 - Var(target-prediction)/Var(target); null when target variance is zero",
            "within_game": "center predictions and targets by their own game means, then pool EV",
            "correlation": "per-game Pearson(prediction, terminal MC target); equal-game quantiles",
            "jitter_ratio": "sample std(diff prediction) / sample std(diff target); no epsilon",
            "undefined_temporal": "constant denominator or insufficient rows remain null and counted",
            "temporal_caution": "MC targets are smooth time trends; correlation/jitter are not gameplay proof",
            "targets": "discounted full terminal return; no lambda or value bootstrap",
            "calibration": "fixed prediction bins; per-decision and inverse-game-length summaries",
            "scope": "frozen-policy Managed TRAIN-holdout sampling; not real-game proof"}}
