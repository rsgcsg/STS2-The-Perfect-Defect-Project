"""Value heads per exploration cell (diagnostics only, owner 2026-11-19).

`value_by_cell` splits the live value statistics by the row's cell and sums exactly to
the unsplit ones; `value_cross_cell` (value-config heads, diagnostics steps) evaluates
the outcome head at every cell's config on the same rows and equals a direct forward at
that config; with the input off it is absent; data-parallel ranks add up to one rank.
"""
from __future__ import annotations

import copy
import dataclasses as dc
from types import SimpleNamespace

import pytest

pytest.importorskip("torch")

import torch  # noqa: E402
from test_rl41_w1 import model as model  # noqa: E402,F401  (fixture)
from test_rl_rows_forward import prepared  # noqa: E402

from stpd.rl import learning_diagnostics as ld  # noqa: E402
from stpd.rl.batch_preparation import row_layout  # noqa: E402
from stpd.rl.heads import (  # noqa: E402
    CONTROL_VALUE_CONFIG,
    VALUE_CONFIG_KEYS,
    class_reward_values,
    value_config_vector,
)
from stpd.rl.loss import LossConfig, prepared_update, with_value_config  # noqa: E402
from stpd.rl.performance import PerformanceConfig  # noqa: E402

WARM = value_config_vector({"combat": {"temperature": 1.4, "uniform_mix": 0.005},
                            "noncombat": {"temperature": 2.0, "uniform_mix": 0.0}})
HOT = value_config_vector({"combat": {"temperature": 2.5, "uniform_mix": 0.01},
                           "noncombat": {"temperature": 3.0, "uniform_mix": 0.01}})
CONFIGS = {"control": CONTROL_VALUE_CONFIG, "hot": HOT, "warm": WARM}
CONFIG = LossConfig(mode="ppo", value_coef=0.2)


def test_row_cells_strip_save_and_match_auxiliary_rows_by_config():
    """PPO rows take their source's arm ("+save" is the same cell); auxiliary rows take
    the cell of their recorded config (unique match), otherwise unattributed."""
    metadata = [{"exploration": {"hot|act1|combat": [1, 0, 0, 0, 0]}}, {},
                {"exploration": {"control+save|act2|map": [1, 0, 0, 0, 0]}},
                {"exploration": {"control|act1|map": [1, 0, 0, 0, 0]}}, {}]
    members = [SimpleNamespace(rows=2), SimpleNamespace(rows=1), SimpleNamespace(rows=2)]
    request = SimpleNamespace(members=members, auxiliary=[None] * 3)
    configs = [HOT, HOT, CONTROL_VALUE_CONFIG, CONTROL_VALUE_CONFIG, CONTROL_VALUE_CONFIG,
               CONTROL_VALUE_CONFIG, WARM, HOT]  # aux: control, unknown, hot
    cells = ld.row_cells(metadata, request, configs)
    assert cells is not None and cells.names == ("control", "hot", ld.UNATTRIBUTED)
    assert cells.rows == (1, 1, 0, 0, 0, 0, 2, 1)
    assert cells.configs == (CONTROL_VALUE_CONFIG, HOT)
    off = ld.row_cells(metadata, request, None)  # knob off: aux rows carry no cell
    assert off is not None and off.configs is None and off.rows[5:] == (2, 2, 2)
    assert ld.row_cells([{}, {}], SimpleNamespace(members=members[:1], auxiliary=[]),
                        None) is None


def heads_model(model):
    """Value-config heads with non-zero final layers and config weights."""
    model = copy.deepcopy(model)
    model.enable_value_config()
    generator = torch.Generator().manual_seed(11)
    with torch.no_grad():
        for module in (model.rl4_heads.outcome, model.rl4_heads.hp):
            module.mlp[-1].weight.copy_(torch.randn(module.mlp[-1].weight.shape,
                                                    generator=generator) * 0.05)
        for key in VALUE_CONFIG_KEYS:
            weight = model.rl4_heads.get_parameter(key)
            weight.copy_(torch.randn(weight.shape, generator=generator))
    return model


def cells_for(minibatch, *, configs: bool):
    rows = 1 + max(row for group in minibatch.groups for row in group.rows)
    names = ("control", "hot", "warm", ld.UNATTRIBUTED)
    assignment = tuple(row % 4 for row in range(rows))  # every cell, unattributed too
    return ld.ValueCells(names, assignment, tuple(CONFIGS[n] for n in names[:3])
                         if configs else None)


def staged(minibatch, cells):
    own = [cells.configs[c] if c < 3 else CONTROL_VALUE_CONFIG for c in cells.rows]
    return with_value_config(minibatch, own, torch.device("cpu"))


def expected_reward(logits: torch.Tensor) -> torch.Tensor:
    return (logits.float().softmax(-1) * class_reward_values()).sum(-1)


@pytest.mark.parametrize("layout", ["padded-v1", "rows-v2"])
def test_cells_sum_to_the_unsplit_statistics_and_cross_equals_a_direct_forward(model, layout):
    new = heads_model(model)
    minibatch = prepared(model, layout).minibatches[0]  # scored before the input
    if layout == "rows-v2":  # several buckets: the capture is put back in row order
        minibatch = dc.replace(minibatch, groups=[
            dc.replace(g, layout=row_layout(g, bucket_rows=3, width_multiple=8))
            for g in minibatch.groups])
    cells = cells_for(minibatch, configs=True)
    batch = staged(minibatch, cells)
    reference = copy.deepcopy(new)  # pre-step weights
    trained = copy.deepcopy(new)
    result = prepared_update(trained, torch.optim.AdamW(trained.parameters(), lr=1e-3), batch,
                             config=CONFIG, performance=PerformanceConfig(prepared_forward=layout),
                             target_kl=1e9, diagnostics=True, value_cells=cells)
    assert not trained.rl4_heads.outcome.mlp[0]._forward_hooks  # removed after the step
    # A: the cells add up to the "all" bucket and the calibration, exactly as sums.
    by_cell = result["value_by_cell"]
    assert set(by_cell) == set(cells.names)
    for head in ld.HEADS:
        pooled = [sum(by_cell[c][head][i] for c in cells.names) for i in range(len(ld.FIELDS))]
        assert pooled == pytest.approx(result["value_statistics"]["all"][head],
                                       rel=1e-5, abs=1e-5)
    for b in range(ld.CALIBRATION_BINS):
        pooled = [sum(by_cell[c]["calibration"][b][i] for c in cells.names) for i in range(3)]
        assert pooled == pytest.approx(result["value_calibration"][b], rel=1e-5, abs=1e-5)
    assert by_cell["hot"]["td"][0] > 0
    # B: per (own cell, queried cell) the sum equals the head's forward at that config.
    cross = result["value_cross_cell"]
    assert cross["configs"] == {n: list(CONFIGS[n]) for n in ("control", "hot", "warm")}
    order = [row for group in minibatch.groups for row in group.rows]
    own = torch.tensor([cells.rows[row] for row in order])
    for query, config in CONFIGS.items():
        values = []
        with torch.no_grad():
            for group in minibatch.groups:
                queried = torch.tensor([config] * len(group.rows), dtype=torch.float32)
                _, logits, _ = reference.forward_prepared_rl4(group, value_config=queried)
                values.append(expected_reward(logits))
        values = torch.cat(values)
        for c, name in enumerate(cells.names):
            assert cross["rows"][name] == int((own == c).sum())
            assert cross["expected_reward_sum"][name][query] == pytest.approx(
                float(values[own == c].sum()), rel=1e-5, abs=1e-6), (name, query)
    # The input matters here: the hot query differs from the control one.
    assert (cross["expected_reward_sum"]["hot"]["hot"]
            != pytest.approx(cross["expected_reward_sum"]["hot"]["control"], abs=1e-6))


def test_off_steps_and_knob_off_emit_no_cross_query_and_a_still_works(model):
    minibatch = prepared(model, "rows-v2").minibatches[0]
    cells = cells_for(minibatch, configs=False)  # heads without the input
    for diagnostics in (False, True):
        trained = copy.deepcopy(model)
        result = prepared_update(trained, torch.optim.AdamW(trained.parameters()), minibatch,
                                 config=CONFIG,
                                 performance=PerformanceConfig(prepared_forward="rows-v2"),
                                 target_kl=1e9, diagnostics=diagnostics, value_cells=cells)
        assert "value_cross_cell" not in result
        assert sum(result["value_by_cell"][c]["td"][0] for c in cells.names) == (
            result["value_statistics"]["all"]["td"][0])
    # value-config heads, a step that is not a diagnostics step: A only
    new = heads_model(model)
    cells = cells_for(minibatch, configs=True)
    result = prepared_update(new, torch.optim.AdamW(new.parameters()), staged(minibatch, cells),
                             config=CONFIG, performance=PerformanceConfig(
                                 prepared_forward="rows-v2"), target_kl=1e9, value_cells=cells)
    assert "value_by_cell" in result and "value_cross_cell" not in result
    plain = prepared_update(copy.deepcopy(model), torch.optim.AdamW(model.parameters()),
                            minibatch, config=CONFIG, target_kl=1e9,
                            performance=PerformanceConfig(prepared_forward="rows-v2"))
    assert "value_by_cell" not in plain and "value_cross_cell" not in plain


def test_data_parallel_ranks_add_up_to_one_rank(model):
    """Each rank runs its groups; the cell sums ride the same all-reduce as the totals,
    so the summed vector gives the one-rank fields."""
    new = heads_model(model)
    minibatch = prepared(model, "rows-v2", microbatch_size=4).minibatches[0]
    assert len(minibatch.groups) >= 2
    cells = cells_for(minibatch, configs=True)
    batch = staged(minibatch, cells)
    performance = PerformanceConfig(prepared_forward="rows-v2")

    def run(shard, sync):
        trained = copy.deepcopy(new)
        return prepared_update(trained, torch.optim.SGD(trained.parameters(), lr=0.0), batch,
                               config=CONFIG, performance=performance, target_kl=1e9,
                               diagnostics=True, value_cells=cells, gradient_sync=sync,
                               shard=shard)

    parts = []
    for rank in range(2):
        run((rank, 2), lambda model, totals, valid: (parts.append(totals.clone()) or
                                                     (totals, valid)))
    summed = parts[0] + parts[1]
    joined = run((0, 2), lambda model, totals, valid: (summed, valid))
    whole = run(None, None)
    for key in ("value_by_cell", "value_cross_cell"):
        left, right = joined[key], whole[key]
        if key == "value_cross_cell":
            assert left["rows"] == right["rows"]
            left, right = left["expected_reward_sum"], right["expected_reward_sum"]
        for cell, part in right.items():
            for name, values in part.items():
                if name == "calibration":  # per bin
                    values, left[cell][name] = sum(values, []), sum(left[cell][name], [])
                assert left[cell][name] == pytest.approx(values, rel=1e-5, abs=1e-5), (
                    key, cell, name)


def test_the_readouts_leave_the_step_bit_identical(model):
    """Diagnostics only: with and without `value_cells` (diagnostics steps, cross query
    on) the totals, parameters and Adam state stay bitwise equal over two steps."""
    new = heads_model(model)
    minibatch = prepared(model, "rows-v2").minibatches[0]
    cells = cells_for(minibatch, configs=True)
    batch = staged(minibatch, cells)
    runs = []
    for given in (cells, None):
        trained = copy.deepcopy(new)
        optimizer = torch.optim.AdamW(trained.parameters(), lr=1e-3)
        results = [prepared_update(trained, optimizer, batch, config=CONFIG,
                                   performance=PerformanceConfig(prepared_forward="rows-v2"),
                                   target_kl=1e9, diagnostics=True, value_cells=given)
                   for _ in range(2)]
        runs.append((results, trained.state_dict(), optimizer.state_dict()["state"]))
    (on, on_state, on_adam), (off, off_state, off_adam) = runs
    assert "value_cross_cell" in on[0] and "value_cross_cell" not in off[0]
    for a, b in zip(on, off, strict=True):
        for key in ("td_loss", "policy_loss", "terminal_loss", "hp_loss", "gradient_norm"):
            assert a[key] == b[key], key
    for name, value in on_state.items():
        assert torch.equal(value, off_state[name]), name
    for index, state in on_adam.items():
        for key, value in state.items():
            assert torch.equal(value, off_adam[index][key]), (index, key)
