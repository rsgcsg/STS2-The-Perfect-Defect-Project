"""Value/HP head diagnostics of the live update: known tensors give known sums and EV."""

from __future__ import annotations

import copy
import math

import pytest
import torch
from test_rl41_w1 import model as model  # noqa: F401  (fixture)
from test_rl_rows_forward import prepared

from stpd.evaluation.value_quality import _stats
from stpd.rl import learning_diagnostics as ld
from stpd.rl.heads import class_reward_values, outcome_mask
from stpd.rl.loss import LossConfig, prepared_update
from stpd.rl.performance import PerformanceConfig

FLOORS = [5, 17, 25, 40, 48]


def tensors(hp_predicted: list[float], hp_targets: list[float]):
    torch.manual_seed(3)
    valid = outcome_mask(FLOORS)
    logits = torch.randn(len(FLOORS), 51).masked_fill(~valid, -torch.inf)
    raw = torch.rand(len(FLOORS), 51).masked_fill(~valid, 0)
    t = {"outcome_valid": valid, "targets": raw / raw.sum(-1, keepdim=True),
         "td_mask": torch.tensor([True, True, False, True, True]),
         "terminal_mask": torch.tensor([False, False, True, False, True]),
         "hp_mask": torch.tensor([False, True, False, True, True]),
         "hp_targets": torch.tensor(hp_targets)}
    return logits, torch.tensor(hp_predicted), t


def scalar(row: torch.Tensor, values: torch.Tensor) -> float:
    finite = [(float(x), float(v)) for x, v in zip(row, values, strict=True)
              if math.isfinite(float(x))]
    top = max(x for x, _ in finite)
    weights = [(math.exp(x - top), v) for x, v in finite]
    return sum(w * v for w, v in weights) / sum(w for w, _ in weights)


def test_known_tensors_give_value_quality_statistics_per_bucket_and_head():
    # HP: rows 1, 3, 4 predicted 0.1/0.2/0.3 against 0.1/0.3/0.2 -> EV exactly 0.
    logits, hp, t = tensors([9.0, 0.1, 9.0, 0.2, 0.3], [0.0, 0.1, 0.0, 0.3, 0.2])
    fields = ld.statistics_fields(ld.value_statistics(logits, hp, t).tolist())
    values = class_reward_values()
    predicted = [scalar(row, values) for row in logits]
    target = [float((row * values).sum()) for row in t["targets"]]
    assert ld.row_floors(t["outcome_valid"]).tolist() == FLOORS
    members = {"all": [0, 1, 2, 3, 4], "act1": [0, 1], "act2": [2], "act3": [3, 4],
               "boss_floor": [1, 4]}
    for bucket, rows in members.items():
        for head, mask in (("td", "td_mask"), ("terminal", "terminal_mask")):
            chosen = [i for i in rows if t[mask][i]]
            got = ld.summarize(fields[bucket][head])
            want = _stats([predicted[i] for i in chosen], [target[i] for i in chosen])
            assert got["rows"] == want["rows"]
            for key, value in want.items():
                if value is None or key == "rows":
                    assert got[key] == value, (bucket, head, key)
                else:
                    assert got[key] == pytest.approx(value, rel=1e-4, abs=1e-6), (
                        bucket, head, key)
    whole = ld.summary(fields)["all"]["hp"]
    assert whole["rows"] == 3 and whole["explained_variance"] == pytest.approx(0, abs=1e-5)
    assert whole["mae"] == pytest.approx(20 / 3, rel=1e-5)  # HP points
    assert whole["mse"] == pytest.approx(200 / 3, rel=1e-5)
    head = ld.headline(fields)
    assert head["hp_mae_points"] == pytest.approx(20 / 3, rel=1e-5)
    assert head["value_explained_variance"] == pytest.approx(
        _stats([predicted[i] for i in (0, 1, 3, 4)],
               [target[i] for i in (0, 1, 3, 4)])["explained_variance"], rel=1e-4)
    assert head["outcome_explained_variance"] == pytest.approx(
        _stats([predicted[i] for i in (2, 4)], [target[i] for i in (2, 4)])[
            "explained_variance"], rel=1e-4)


def test_perfect_and_shifted_predictions_and_exact_pooling():
    logits, hp, t = tensors([9.0, 0.3, 9.0, 0.5, 0.8], [0.0, 0.3, 0.0, 0.5, 0.8])
    perfect = ld.summarize(ld.statistics_fields(
        ld.value_statistics(logits, hp, t).tolist())["all"]["hp"], hp_scale=100)
    assert perfect["explained_variance"] == pytest.approx(1) and perfect["mae"] == 0
    shifted = ld.summarize(ld.statistics_fields(
        ld.value_statistics(logits, hp + 0.1, t).tolist())["all"]["hp"], hp_scale=100)
    assert shifted["explained_variance"] == pytest.approx(1, abs=1e-5)
    assert shifted["bias_prediction_minus_target"] == pytest.approx(10, rel=1e-5)
    # Sums pool exactly: two halves added == the whole.
    whole = ld.value_statistics(logits, hp, t)
    halves = [ld.statistics_fields(ld.value_statistics(
        logits[part], hp[part], {k: v[part] for k, v in t.items()}).tolist())
        for part in (slice(0, 2), slice(2, 5))]
    pooled = ld.add(ld.add(None, halves[0]), halves[1])
    for bucket, heads in ld.statistics_fields(whole.tolist()).items():
        for name, sums in heads.items():
            assert pooled[bucket][name] == pytest.approx(sums, rel=1e-6, abs=1e-6)
    with pytest.raises(ValueError, match="size_mismatch"):
        ld.statistics_fields([0.0] * (ld.SIZE - 1))
    empty = ld.summarize([0.0] * len(ld.FIELDS))
    assert empty["rows"] == 0 and empty["explained_variance"] is None


def test_prepared_update_reports_value_sums_in_the_synced_totals(model):
    minibatch = prepared(model, "rows-v2").minibatches[0]
    seen = []

    def sync(model, totals, valid):
        seen.append(totals.numel())
        return totals, valid

    trained = copy.deepcopy(model)
    result = prepared_update(trained, torch.optim.AdamW(trained.parameters(), lr=1e-3),
                             minibatch, config=LossConfig(mode="ppo", value_coef=0.2),
                             performance=PerformanceConfig(prepared_forward="rows-v2"),
                             target_kl=1e9, gradient_sync=sync)
    assert seen == [8 + ld.SIZE]
    t = minibatch.tensors
    whole = result["value_statistics"]["all"]
    assert whole["td"][0] == int(t["td_mask"].sum())
    assert whole["terminal"][0] == minibatch.terminal_rows
    assert whole["hp"][0] == minibatch.hp_rows
    assert set(result["value_statistics"]) == set(ld.BUCKETS)
    for key in ("value_explained_variance", "outcome_explained_variance", "value_mse",
                "hp_mae_points"):
        assert key in result
    assert result["value_mse"] is not None and result["value_mse"] >= 0
