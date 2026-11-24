"""Value/HP head diagnostics of the live update: known tensors give known sums and EV."""

from __future__ import annotations

import copy
import dataclasses
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
    calibration = result["value_calibration"]  # R: terminal rows by predicted value
    assert len(calibration) == ld.CALIBRATION_BINS
    assert sum(row[0] for row in calibration) == minibatch.terminal_rows
    for key in ("value_explained_variance", "outcome_explained_variance", "value_mse",
                "hp_mae_points"):
        assert key in result
    assert result["value_mse"] is not None and result["value_mse"] >= 0


def nontrivial(model, minibatch):
    """Real gradients on every path: perturbed weights (the fixture heads start at zero),
    random advantages, and 2 terminal plus 3 HP auxiliary rows (no td/policy weight)."""
    generator = torch.Generator().manual_seed(5)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.add_(torch.randn(parameter.shape, generator=generator,
                                       dtype=parameter.dtype) * 0.05)
    t = {key: value.clone() for key, value in minibatch.tensors.items()}
    terminal, hp = [8, 9], [5, 6, 7]
    for row in terminal + hp:
        t["td_mask"][row] = t["policy_mask"][row] = 0
    t["advantages"] = torch.randn(t["advantages"].shape, generator=generator) * t["policy_mask"]
    for row in terminal:
        t["terminal_mask"][row] = 1
        t["targets"][row] = 0
        t["targets"][row, int(t["outcome_valid"][row].nonzero()[0])] = 1
    for row in hp:
        t["hp_mask"][row] = 1
        t["hp_targets"][row] = float(torch.rand((), generator=generator))
    for name in ("policy", "td", "terminal", "hp"):
        t[f"{name}_indices"] = torch.nonzero(t[f"{name}_mask"], as_tuple=True)[0]
    return dataclasses.replace(
        minibatch, tensors=t, policy_rows=len(t["policy_indices"]),
        td_rows=len(t["td_indices"]), terminal_rows=len(terminal), hp_rows=len(hp))


@pytest.mark.parametrize("compiled", [False, True])
def test_diagnostics_leave_loss_gradients_and_step_bit_identical(model, compiled, monkeypatch):
    """The sums read detached outputs under no_grad: with them replaced by zeros, the
    loss totals, gradients, Adam state, parameters and RNG stay bitwise equal over three
    steps with real gradients (eager and the compiled rows forward)."""
    minibatch = nontrivial(model, prepared(model, "rows-v2").minibatches[0])
    performance = PerformanceConfig(prepared_forward="rows-v2", prepared_compile=compiled)
    runs = []
    for disabled in (False, True):
        if disabled:
            monkeypatch.setattr(ld, "value_statistics",
                                lambda logits, hp, t: logits.new_zeros(ld.SIZE))
        trained = copy.deepcopy(model)
        optimizer = torch.optim.AdamW(trained.parameters(), lr=1e-3)
        torch.manual_seed(123)
        steps = []
        for _ in range(3):
            seen = {}

            def sync(model, totals, valid, seen=seen):
                seen["totals"] = totals[:8].clone()
                seen["gradients"] = {name: p.grad.clone()
                                     for name, p in model.named_parameters()
                                     if p.grad is not None}
                return totals, valid

            result = prepared_update(trained, optimizer, minibatch,
                                     config=LossConfig(mode="ppo", value_coef=0.2),
                                     performance=performance, target_kl=1e9,
                                     gradient_sync=sync)
            assert result["stepped"] and result["rows_forward"]["compiled"] is compiled
            steps.append((result, seen))
        runs.append((steps, trained.state_dict(), optimizer.state_dict()["state"],
                     torch.get_rng_state()))
    (on_steps, on_state, on_adam, on_rng), (off_steps, off_state, off_adam, off_rng) = runs
    on, off = on_steps[0][0], off_steps[0][0]
    whole = on["value_statistics"]["all"]
    assert whole["td"][0] > 0 and whole["terminal"][0] == 2 and whole["hp"][0] == 3
    assert off["value_statistics"]["all"]["td"][0] == 0
    assert on["gradient_norm"] > 0 and on["policy_loss"] != 0  # not a zero-gradient case
    assert sum(bool(g.any()) for g in on_steps[0][1]["gradients"].values()) > 1
    for (on_result, on_seen), (off_result, off_seen) in zip(on_steps, off_steps, strict=True):
        assert torch.equal(on_seen["totals"], off_seen["totals"])
        assert on_seen["gradients"].keys() == off_seen["gradients"].keys()
        for name, gradient in on_seen["gradients"].items():
            assert torch.equal(gradient, off_seen["gradients"][name]), name
        for key in ("td_loss", "policy_loss", "entropy", "approx_kl_mu_theta",
                    "clip_fraction", "terminal_loss", "hp_loss", "gradient_norm"):
            assert on_result[key] == off_result[key], key
    for name, value in on_state.items():
        assert torch.equal(value, off_state[name]), name
    for index, state in on_adam.items():
        for key, value in state.items():
            assert torch.equal(value, off_adam[index][key]), (index, key)
    assert torch.equal(on_rng, off_rng)


def same(left, right, path="batch"):
    """Bitwise structural equality: tensors by dtype and torch.equal, the rest by ==."""
    if isinstance(left, torch.Tensor):
        assert isinstance(right, torch.Tensor) and left.dtype == right.dtype, path
        assert torch.equal(left, right), path
    elif dataclasses.is_dataclass(left) and not isinstance(left, type):
        assert type(left) is type(right), path
        for field in dataclasses.fields(left):
            same(getattr(left, field.name), getattr(right, field.name), f"{path}.{field.name}")
    elif isinstance(left, dict):
        assert left.keys() == right.keys(), path
        for key, value in left.items():
            same(value, right[key], f"{path}[{key!r}]")
    elif isinstance(left, (tuple, list)):
        assert type(left) is type(right) and len(left) == len(right), path
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            same(a, b, f"{path}[{index}]")
    else:
        assert left == right, path


@pytest.mark.parametrize("compiled", [False, True])
def test_the_hp_share_leaves_the_prepared_batch_and_three_steps_bit_identical(model, compiled):
    """RL5-METHODS A2, the bar above for the run record's HP share: with c_hp > 0 the
    fast chain hands the preparation its c_hp * A_hp part only to measure that share.
    A batch prepared with it and one prepared without it (as at c_hp = 0 and before A2)
    hold bitwise equal tensors apart from the two share fields, and three steps on them
    keep totals, gradients, Adam state, parameters and RNG bitwise equal."""
    from test_rl_fast_hp_score import HpGame, batch, single_combat, sources

    from stpd.p1.frame_compiler import FrameCompiler

    compiler = FrameCompiler(model.vocabulary)
    game = HpGame(compiler=compiler)
    single_combat(game)  # c_hp's default scope: a single-combat ticket
    parts = sources(game.sealed, "t", 0.003, compiler=compiler)
    assert all(source.hp_advantages is not None for source in parts)
    on, off = (batch(game.sealed, list(items), "t", compiler) for items in (
        parts, [dataclasses.replace(source, hp_advantages=None) for source in parts]))
    assert on.normalization.hp_share is not None and off.normalization.hp_share is None
    assert dataclasses.replace(on.normalization, hp_std=None, hp_share=None) == \
        off.normalization
    ignored = {"normalization", "prepared_at", "timings", "worker_pids"}
    for field in dataclasses.fields(on):
        if field.name not in ignored:
            same(getattr(on, field.name), getattr(off, field.name), field.name)
    generator = torch.Generator().manual_seed(5)  # real gradients: the heads start at zero
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.add_(torch.randn(parameter.shape, generator=generator,
                                       dtype=parameter.dtype) * 0.05)
    performance = PerformanceConfig(prepared_forward="rows-v2", prepared_compile=compiled)
    runs = []
    for prepared_batch in (on, off):
        [minibatch] = prepared_batch.minibatches
        trained = copy.deepcopy(model)
        optimizer = torch.optim.AdamW(trained.parameters(), lr=1e-3)
        torch.manual_seed(123)
        steps = []
        for _ in range(3):
            seen = {}

            def sync(model, totals, valid, seen=seen):
                seen["totals"] = totals[:8].clone()
                seen["gradients"] = {name: p.grad.clone()
                                     for name, p in model.named_parameters()
                                     if p.grad is not None}
                return totals, valid

            result = prepared_update(trained, optimizer, minibatch,
                                     config=LossConfig(mode="ppo", value_coef=0.2),
                                     performance=performance, target_kl=1e9,
                                     gradient_sync=sync)
            assert result["stepped"] and result["rows_forward"]["compiled"] is compiled
            steps.append((result, seen))
        runs.append((steps, trained.state_dict(), optimizer.state_dict()["state"],
                     torch.get_rng_state()))
    (on_steps, on_state, on_adam, on_rng), (off_steps, off_state, off_adam, off_rng) = runs
    assert on_steps[0][0]["gradient_norm"] > 0 and on_steps[0][0]["policy_loss"] != 0
    assert sum(bool(g.any()) for g in on_steps[0][1]["gradients"].values()) > 1
    for (on_result, on_seen), (off_result, off_seen) in zip(on_steps, off_steps, strict=True):
        same(on_seen, off_seen, "step")
        for key in ("td_loss", "policy_loss", "entropy", "approx_kl_mu_theta",
                    "clip_fraction", "gradient_norm"):
            assert on_result[key] == off_result[key], key
    same(on_state, off_state, "parameters")
    same(on_adam, off_adam, "adam")
    assert torch.equal(on_rng, off_rng)


def test_policy_rows_fold_per_arm_by_the_source_tables():
    """RL5-METHODS R: a row's arm is its source's (the exploration table on its first row;
    a source without one is unattributed, never the previous source's arm); KL per row is
    r - 1 - log r; a missing or zero ratio adds no KL, counted apart."""
    from stpd.rl.learning_diagnostics import UNATTRIBUTED, policy_by_arm, row_arms

    metadata = [{"floor": 1},  # a source without a table (single-choice rows only)
                {"floor": 1, "exploration": {"hot|act1|combat": [2, 0, 0, 0, 0]}},
                {"floor": 2}, {"floor": 2},
                {"floor": 5, "exploration": {"control+save|act2|map": [1, 0, 0, 0, 0],
                                             "control+save|act2|combat": [1, 0, 0, 0, 0]}},
                {"floor": 6}, {"floor": 7}]  # the last source has no table
    arms = row_arms(metadata, [1, 3, 2, 1])
    assert arms == [None, "hot", "hot", "hot", "control+save", "control+save", None]
    with pytest.raises(ValueError, match="cover 6 rows"):
        row_arms(metadata, [1, 3, 2])
    samples = [{"row": 0, "ratio": 1.0, "outside_clip": False},
               {"row": 1, "ratio": 1.5, "outside_clip": True},
               {"row": 3, "ratio": 0.8, "outside_clip": False},
               {"row": 4, "ratio": None, "outside_clip": False},
               {"row": 5, "ratio": 0.0, "outside_clip": True},
               {"row": 6, "ratio": 1.0, "outside_clip": False}]
    out = policy_by_arm(arms, samples)
    assert out[UNATTRIBUTED] == [2, 0.0, 0, 0]
    assert out["hot"][0] == 2 and out["hot"][2] == 1 and out["hot"][3] == 0
    assert out["hot"][1] == pytest.approx(0.5 - math.log(1.5) - 0.2 - math.log(0.8))
    assert out["control+save"] == [2, 0.0, 1, 2]
    assert policy_by_arm(arms, []) == {}


def test_value_calibration_bins_terminal_rows_by_predicted_value():
    """RL5-METHODS R: terminal aux rows only, binned by the predicted scalar value (width
    0.1), with their predicted and realised (one-hot class x table) sums; pools exactly."""
    logits, hp, t = tensors([0.0] * 5, [0.0] * 5)
    values = class_reward_values()
    vector = ld.value_statistics(logits, hp, t).tolist()
    assert len(vector) == ld.SIZE == ld.STATISTICS_SIZE + ld.CALIBRATION_SIZE
    bins = ld.calibration_fields(vector)
    predicted = [scalar(row, values) for row in logits]
    target = [float((row * values).sum()) for row in t["targets"]]
    want = [[0.0, 0.0, 0.0] for _ in range(ld.CALIBRATION_BINS)]
    for i in (2, 4):  # the terminal rows
        b = min(int(predicted[i] * 10), 9)
        want[b][0] += 1
        want[b][1] += predicted[i]
        want[b][2] += target[i]
    for got, expected in zip(bins, want, strict=True):
        assert got == pytest.approx(expected, rel=1e-5, abs=1e-6)
    # the statistics part is unchanged by the tail
    assert ld.statistics_fields(vector)["all"]["terminal"][0] == 2
    with pytest.raises(ValueError, match="size_mismatch"):
        ld.calibration_fields(vector[:-1])
    # A one-hot target is a realised reward; a value of exactly 1 goes in the last bin.
    t2 = {k: v[4:5] for k, v in t.items()}
    win = torch.full((1, 51), -torch.inf)
    win[0, 49] = 0.0  # all mass on the win class: value 1.0
    t2["targets"] = torch.zeros(1, 51)
    t2["targets"][0, 49] = 1.0
    t2["outcome_valid"] = torch.ones(1, 51, dtype=torch.bool)
    last = ld.calibration_fields(ld.value_statistics(win, hp[4:5], t2).tolist())
    assert last[9] == [1.0, 1.0, 1.0] and all(row[0] == 0 for row in last[:9])


def test_policy_and_target_entropy_and_model_age_helpers():
    # p = q at T 1, eps 0; tempered/mixed q recovers the model's p first.
    h, normalized = ld.row_entropy([0.5, 0.5], 1.0, 0.0)
    assert h == pytest.approx(math.log(2)) and normalized == pytest.approx(1.0)
    p = [0.7, 0.2, 0.1]
    eps, temperature = 0.3, 2.0
    tempered = [x ** (1 / temperature) for x in p]
    q = [(1 - eps) * x / sum(tempered) + eps / 3 for x in tempered]
    h, normalized = ld.row_entropy(q, temperature, eps)
    want = -sum(x * math.log(x) for x in p)
    assert h == pytest.approx(want, rel=1e-9) and normalized == pytest.approx(
        want / math.log(3), rel=1e-9)
    counts: dict[str, list[float]] = {}
    ld.add_entropy(counts, "control", 1, 0, (1.0, 0.5))
    ld.add_entropy(counts, "control", 1, 0, (0.5, 0.25))
    ld.add_entropy(counts, "hot", 3, 4, (0.1, 0.1))
    assert counts == {"control|act1|combat": [2, 1.5, 0.75], "hot|act3|rest": [1, 0.1, 0.1]}
    metadata = [{"policy_entropy": counts, "value_target": [4, 2.0, 2, 1.5],
                 "target_entropy": [4, 6.0]}, {},
                {"policy_entropy": {"hot|act3|rest": [1, 0.3, 0.2]},
                 "value_target": [2, 0.0, 0, 0.0], "target_entropy": [2, 3.0]}]
    assert ld.entropy_fields(metadata) == {"control|act1|combat": [2, 1.5, 0.75],
                                           "hot|act3|rest": [2, pytest.approx(0.4),
                                                             pytest.approx(0.3)]}
    fields = ld.value_target_fields(metadata)
    assert fields["target_entropy_mean"] == 1.5 and fields["outcome_weight_mean"] == 2 / 6
    assert ld.value_target_fields([{}])["target_entropy_mean"] is None

    from stpd.rl.segment_learner import MU_AGE_EDGES, mu_age_fields

    rows = [{"mu_version": 3}, {"mu_version": 4}, {"mu_version": 4}, {"mu_version": 1}]
    age = mu_age_fields(rows, 1000.0, {3: 900.0, 4: 995.0})
    assert age["n"] == 3 and age["missing"] == 1 and age["sum"] == 110.0
    assert len(age["histogram"]) == len(MU_AGE_EDGES) + 1
    assert age["histogram"][0] == 2  # 5 s: equal to the first edge, in that bin
    assert age["histogram"][MU_AGE_EDGES.index(120)] == 1  # 100 s


def test_rare_noncombat_choices_per_arm_and_decision():
    """User 2026-11-24: the chosen option's model p (from q), the per-source table and the
    dataset fold with distinct games."""
    from stpd.p1.sampling import distribution
    from stpd.rl.learning_diagnostics import (
        DECISIONS,
        add_low_probability,
        chosen_model_probability,
        game_key,
        low_probability_fields,
    )

    p, _ = distribution([3.0, 0.0, -1.0], 1.0, 0.0)
    for temperature, mix in ((1.0, 0.0), (2.5, 0.0), (0.5, 0.05)):
        _, q = distribution([3.0, 0.0, -1.0], temperature, mix)
        assert [chosen_model_probability(q, i, temperature, mix) for i in range(3)] == \
            pytest.approx(p, abs=1e-9)
    with pytest.raises(ValueError, match="greedy"):
        chosen_model_probability([1.0, 0.0], 0, 0, 0.0)
    table: dict[str, list[float]] = {}
    rest = DECISIONS.index("rest")
    add_low_probability(table, "control", rest, 0.5, 0.25)
    add_low_probability(table, "control", rest, -0.1, None)
    assert table == {"control|rest": [2, 0.4, 1, 0.25]}
    assert game_key("g1") == game_key("g1") != game_key("g2") and len(game_key("g1")) == 16
    metadata = [{"low_probability_noncombat": {"game": game_key("g1"), "rows": table}}, {},
                {"low_probability_noncombat": {"game": game_key("g1"), "rows": {
                    "control|rest": [1, 0.1, 0, 0.0], "control|map": [1, 0.2, 1, -0.1]}}},
                {"low_probability_noncombat": {"game": game_key("g2"), "rows": {
                    "control|rest": [1, 0.0, 1, 0.5]}}}]
    fields = low_probability_fields(metadata)
    assert fields["rows"] == {"control|rest": [4, pytest.approx(0.5), 2, 0.75],
                              "control|map": [1, 0.2, 1, -0.1]}
    assert fields["games"] == {"control|rest": sorted([game_key("g1"), game_key("g2")]),
                               "control|map": [game_key("g1")]}
    assert low_probability_fields([{}, {}]) is None
