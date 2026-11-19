"""`loss.arm_policy_update` (owner 2026-11-19): "own" = each row updates its own cell's
policy pi_cell = (1 - m) softmax(logits / T) + m / |catalog|, the collection-side formula.

Pinned here: the training-side cell reproduces `stpd.p1.sampling.distribution` exactly, so
at unchanged weights every cell's ratio is 1; the pure-temperature gradient is the 1/T
logit gradient; "normal" (default) is the loss before the knob; "own" refuses the ratio
floor and rows without a sampling config; the SGD child (single and two ranks) and its
offline dump/replay step on rows of several cells.
"""

from __future__ import annotations

import copy
import dataclasses
import math
import multiprocessing
from dataclasses import replace
from types import SimpleNamespace

import pytest
import torch
from test_rl41_w1 import model as model  # noqa: F401  (fixture)
from test_rl_rows_forward import prepared
from torch.nn import functional as F

from spireagent.json_boundary import BoundaryError
from stpd.p1.sampling import distribution
from stpd.rl import offline_step, recipe_changes
from stpd.rl.loss import (
    NORMAL_SAMPLING,
    LossConfig,
    arm_log_probabilities,
    batch_arm_sampling,
    batched_segment_loss,
    prepared_update,
    rl_update,
    with_arm_sampling,
)
from stpd.rl.performance import PerformanceConfig
from stpd.rl.segment_learner import _resume_config

# control, hot, cold, hot + mix, coldest + largest mix (the rl-inference-4 envelope ends)
CELLS = [(1.0, 0.0), (2.5, 0.0), (0.5, 0.0), (1.4, 0.01), (0.25, 0.1)]
OPTIONS = dict(clip=.2, entropy_coef=.01, value_coef=.2, terminal_coef=.1, hp_coef=.1,
               td_rows=0, terminal_rows=0, hp_rows=0)


def _rows(widths, seed=0):
    """Random scores on catalogs of `widths` (padded with -inf to the widest)."""
    generator = torch.Generator().manual_seed(seed)
    width = max(widths)
    scores = torch.randn((len(widths), width), generator=generator) * 3
    valid = torch.arange(width)[None, :] < torch.tensor(widths)[:, None]
    return scores.masked_fill(~valid, -torch.inf), valid


def _tensors(scores, valid, cells, old, actions, advantages, policy=None):
    rows, width = scores.shape
    return {"actions": torch.tensor(actions), "old_probabilities": old,
            "advantages": torch.tensor(advantages, dtype=torch.float32),
            "targets": torch.zeros((rows, 51)), "hp_targets": torch.zeros(rows),
            "policy_mask": torch.ones(rows) if policy is None else torch.tensor(policy),
            "td_mask": torch.zeros(rows), "terminal_mask": torch.zeros(rows),
            "hp_mask": torch.zeros(rows), "catalog_valid": valid,
            "outcome_valid": torch.ones((rows, 51), dtype=torch.bool),
            **({"arm_sampling": torch.tensor(cells, dtype=torch.float32)}
               if cells is not None else {})}


def _behavior(scores, valid, cells):
    """mu as the inference service records it: `distribution` on the row's catalog."""
    old = torch.zeros(scores.shape, dtype=torch.float32)
    for row, (temperature, mix) in enumerate(cells):
        count = int(valid[row].sum())
        _, q = distribution(scores[row, :count].tolist(), temperature, mix)
        old[row, :count] = torch.tensor(q)
    return old


def test_cell_reproduces_the_collection_side_formula_on_padded_catalogs():
    widths = [1, 2, 5, 9, 9, 3, 7, 4, 9, 6]
    scores, valid = _rows(widths)
    cells = [CELLS[i % len(CELLS)] for i in range(len(widths))]
    got = arm_log_probabilities(scores, torch.tensor(cells), valid).exp()
    want = _behavior(scores, valid, cells)
    assert torch.allclose(got, want, rtol=1e-5, atol=1e-7)
    assert bool((got[~valid] == 0).all())  # nothing outside the catalog
    # T = 1, m = 0 is the T = 1 policy exactly (the control cell under "own")
    plain = arm_log_probabilities(scores, torch.tensor([NORMAL_SAMPLING] * len(widths)), valid)
    assert torch.equal(plain[valid], scores.log_softmax(-1)[valid])


def test_ratio_is_one_at_unchanged_weights_for_every_cell_and_kl_zero():
    widths = [2, 5, 9, 9, 3, 7, 4, 9, 6, 8]
    scores, valid = _rows(widths, seed=1)
    cells = [CELLS[i % len(CELLS)] for i in range(len(widths))]
    old = _behavior(scores, valid, cells)
    actions = [i % w for i, w in enumerate(widths)]
    t = _tensors(scores, valid, cells, old, actions, [1., -1.] * 5)
    rows: list[torch.Tensor] = []
    _, totals, _, _ = batched_segment_loss(scores, torch.zeros((10, 51)), torch.zeros(10), t,
                                           multi=10, row_diagnostics=rows, **OPTIONS)
    forward, ratio, outside = rows[0][:, 0], rows[0][:, 1], rows[0][:, 2]
    assert torch.allclose(ratio, torch.ones(10), atol=2e-6)
    assert float(forward.abs().max()) < 1e-5 and not bool(outside.any())
    kl, clipped, reverse = totals[3], totals[4], totals[5]
    assert abs(float(kl)) < 1e-6 and float(clipped) == 0 and abs(float(reverse)) < 1e-5
    # the same rows under "normal" (no cells): the tempered/mixed rows are far from 1
    normal: list[torch.Tensor] = []
    batched_segment_loss(scores, torch.zeros((10, 51)), torch.zeros(10),
                         {k: v for k, v in t.items() if k != "arm_sampling"},
                         multi=10, row_diagnostics=normal, **OPTIONS)
    control = torch.tensor([cell == (1.0, 0.0) for cell in cells])
    assert torch.allclose(normal[0][control, 1], torch.ones(int(control.sum())), atol=2e-6)
    assert float((normal[0][~control, 1] - 1).abs().max()) > 0.1


def test_pure_temperature_gradient_is_the_one_over_T_logit_gradient():
    """A hot row (T 2.5, m 0), unclipped at r = 1: d policy / d scores =
    -(A / multi) (1/T) (onehot(a) - softmax(scores / T)); entropy coefficient 0."""
    temperature, advantage = 2.5, 0.7
    scores, valid = _rows([6], seed=2)
    scores.requires_grad_(True)
    old = _behavior(scores.detach(), valid, [(temperature, 0.0)])
    t = _tensors(scores.detach(), valid, [(temperature, 0.0)], old, [4], [advantage])
    options = {**OPTIONS, "entropy_coef": 0.0}
    policy = batched_segment_loss(scores, torch.zeros((1, 51)), torch.zeros(1), t, multi=1,
                                  **options)[2][0]
    gradient = torch.autograd.grad(policy, scores)[0][0]
    onehot = F.one_hot(torch.tensor(4), 6).float()
    want = -advantage / temperature * (onehot - (scores.detach()[0] / temperature).softmax(-1))
    assert torch.allclose(gradient, want, atol=1e-6)
    # "normal" on the same row: the T = 1 gradient r (onehot - softmax), r far from 1
    del t["arm_sampling"]
    normal = batched_segment_loss(scores, torch.zeros((1, 51)), torch.zeros(1), t, multi=1,
                                  **options)[2][0]
    assert not torch.allclose(torch.autograd.grad(normal, scores)[0][0], want, atol=1e-3)


def _before_knob(scores, t, clip, multi, entropy_coef):
    """The policy part and policy totals as written before the knob (reference copy)."""
    logp = scores.float().log_softmax(-1)
    probability = logp.exp()
    safe_logp = logp.masked_fill(~t["catalog_valid"], 0)
    action = t["actions"][:, None]
    log_ratio = logp.gather(1, action)[:, 0] - t["old_probabilities"].gather(1, action)[:, 0].log()
    ratio = log_ratio.exp()
    advantage = t["advantages"]
    mask = t["policy_mask"] / max(multi, 1)
    policy = -(torch.minimum(ratio * advantage, ratio.clamp(1 - clip, 1 + clip) * advantage)
               * mask).sum()
    entropy = (-(probability * safe_logp).sum(-1) * mask).sum()
    kl = ((ratio - 1 - log_ratio) * mask).sum()
    clipped = (((ratio.detach() - 1).abs() > clip).float() * mask).sum()
    reverse = ((probability * safe_logp - torch.special.xlogy(probability, t["old_probabilities"]))
               .sum(-1) * mask).sum()
    return policy - entropy_coef * entropy, torch.stack((policy, entropy, kl, clipped, reverse))


def test_default_normal_is_the_loss_before_the_knob_on_a_mixed_arm_batch():
    """Rows recorded by control, hot and cold arms; no cells (the default): same value,
    gradient, totals and row columns as before the knob, bit for bit."""
    widths = [2, 5, 9, 9, 3, 7, 4, 9, 6, 8]
    scores, valid = _rows(widths, seed=3)
    cells = [CELLS[i % len(CELLS)] for i in range(len(widths))]
    t = _tensors(scores, valid, None, _behavior(scores, valid, cells),
                 [i % w for i, w in enumerate(widths)], [1., -2., .5, 3., -1.] * 2,
                 policy=[1.] * 9 + [0.])
    scores.requires_grad_(True)
    rows: list[torch.Tensor] = []
    _, totals, components, _ = batched_segment_loss(
        scores, torch.zeros((10, 51)), torch.zeros(10), t, multi=9, row_diagnostics=rows,
        **OPTIONS)
    reference, reference_totals = _before_knob(scores, t, .2, 9, .01)
    assert torch.equal(components[0], reference)
    assert torch.equal(totals[[1, 2, 3, 4, 5]], reference_totals.detach())
    assert torch.equal(torch.autograd.grad(components[0], scores)[0],
                       torch.autograd.grad(reference, scores)[0])
    assert rows[0].shape == (10, 6)
    assert LossConfig().arm_policy_update == "normal"


def test_entropy_stays_the_T1_policy_under_own():
    widths = [3, 6, 9]
    scores, valid = _rows(widths, seed=4)
    cells = [(2.5, 0.0), (0.5, 0.1), (1.0, 0.0)]
    t = _tensors(scores, valid, cells, _behavior(scores, valid, cells), [0, 1, 2], [1., 1., 1.])
    own = batched_segment_loss(scores, torch.zeros((3, 51)), torch.zeros(3), t, multi=3,
                               **OPTIONS)[1]
    del t["arm_sampling"]
    normal = batched_segment_loss(scores, torch.zeros((3, 51)), torch.zeros(3), t, multi=3,
                                  **OPTIONS)[1]
    assert torch.equal(own[2], normal[2])


def test_own_refuses_the_ratio_floor_and_is_resume_locked_and_declarable():
    with pytest.raises(BoundaryError, match="refuses_explore_ratio_floor"):
        LossConfig(arm_policy_update="own", explore_ratio_floor=0.1)
    with pytest.raises(BoundaryError):
        LossConfig(arm_policy_update="cell")
    assert LossConfig(arm_policy_update="own", explore_ratio_floor=0.0)
    base = {"segments": {}, "loss": dataclasses.asdict(LossConfig())}
    assert "arm_policy_update" not in _resume_config(base)["loss"]  # older CP digests hold
    own = {"segments": {}, "loss": dataclasses.asdict(LossConfig(arm_policy_update="own"))}
    assert _resume_config(own)["loss"]["arm_policy_update"] == "own"
    declared = {"checkpoint_artifact_id": "cp", "changes": {
        "loss.arm_policy_update": {"old": "normal", "new": "own"}}}
    assert recipe_changes.check(declared, _resume_config(base),
                                _resume_config(own)) == declared["changes"]


def test_fast_chain_builds_the_knob_and_refuses_it_with_a_floor():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "friday" / "fastchain"))
    import learner_config

    from stpd.rl.recipe_changes import DECLARABLE

    assert set(learner_config.DECLARABLE) == set(DECLARABLE)
    base = {"loss": {"explore_ratio_floor": 0.0}, "segments": {}, "performance": {}}
    knobs = {"levers": False, "sgd_threads_single": 1, "max_versions": 2, "capacity_rows": 8,
             "prepared_gib": 1, "diagnostics_every": 8, "checkpoint_seconds": 0}
    run = {"run_name": "r", "resume_checkpoint": None, "peer_dir": "p", "start_package": "s",
           "wall_seconds": 1, "device": "cpu", "generation": "g", "warmup_artifact_id": None,
           "rendezvous_path": "x", "ranks": 1}
    assert "arm_policy_update" not in learner_config.build(base, knobs, run)["loss"]
    built = learner_config.build(base, {**knobs, "arm_policy_update": "own"}, run)
    assert built["loss"]["arm_policy_update"] == "own"
    floored = {**base, "loss": {"explore_ratio_floor": 0.1}}
    with pytest.raises(ValueError, match="refuses loss.explore_ratio_floor"):
        learner_config.build(floored, {**knobs, "arm_policy_update": "own"}, run)


def test_rows_without_a_sampling_config_are_refused_never_control():
    members = SimpleNamespace(auxiliary=[object(), object()])
    metadata = [{"arm_sampling": [2.5, 0.01]}, {"arm_sampling": [1, 0]}]
    assert batch_arm_sampling(metadata, members) == [(2.5, 0.01), (1.0, 0.0),
                                                     NORMAL_SAMPLING, NORMAL_SAMPLING]
    for bad in ({}, {"arm_sampling": None}, {"arm_sampling": [0.1, 0.0]},
                {"arm_sampling": [1.0, 0.5]}, {"arm_sampling": [1.0]}):
        with pytest.raises(ValueError, match="arm_sampling_missing_or_invalid"):
            batch_arm_sampling([metadata[0], bad], members)


def test_prepared_update_needs_cells_exactly_when_own(model):
    batch = prepared(model)
    minibatch = batch.minibatches[0]
    rows = 1 + max(row for group in minibatch.groups for row in group.rows)
    performance = PerformanceConfig()
    own = LossConfig(mode="ppo", arm_policy_update="own")
    optimizer = torch.optim.AdamW(model.parameters())
    with pytest.raises(ValueError, match="arm_sampling_must_match"):
        prepared_update(model, optimizer, minibatch, config=own, performance=performance)
    staged = with_arm_sampling(minibatch, [NORMAL_SAMPLING] * rows, torch.device("cpu"))
    with pytest.raises(ValueError, match="arm_sampling_must_match"):
        prepared_update(model, optimizer, staged, config=LossConfig(mode="ppo"),
                        performance=performance)
    with pytest.raises(BoundaryError, match="needs_prepared_update"):
        rl_update(model, model, optimizer, [], [], 0.0, own)


def test_real_model_ratio_is_one_for_rows_of_every_cell(model):
    """The prepared path end to end: mu recorded from the model's own scores per cell,
    unchanged weights -> every policy row's ratio 1, row KL 0, no clipping."""
    batch = prepared(model)
    minibatch = batch.minibatches[0]
    order = [row for group in minibatch.groups for row in group.rows]
    cells = {row: CELLS[index % len(CELLS)] for index, row in enumerate(order)}
    width = minibatch.tensors["old_probabilities"].shape[1]
    valid = minibatch.tensors["catalog_valid"]
    old = minibatch.tensors["old_probabilities"].clone()
    with torch.no_grad():
        offset = 0
        for group in minibatch.groups:
            scores = model.eval().forward_prepared_rl4(group)[0]
            scores = F.pad(scores, (0, width - scores.shape[1]), value=-torch.inf)
            part = slice(offset, offset + len(group.rows))
            old[part] = _behavior(scores, valid[part], [cells[row] for row in group.rows])
            offset += len(group.rows)
    staged = replace(minibatch, tensors={**minibatch.tensors, "old_probabilities": old})
    staged = with_arm_sampling(staged, [cells.get(row, NORMAL_SAMPLING)
                                        for row in range(1 + max(order))], torch.device("cpu"))
    result = prepared_update(model, torch.optim.AdamW(model.parameters()), staged,
                             config=LossConfig(mode="ppo", arm_policy_update="own"),
                             performance=PerformanceConfig(), target_kl=-1.0,
                             collect_policy_rows=True)
    rows = result["_policy_rows"]
    used = {cells[sample["row"]] for sample in rows}
    assert len(used) >= 3 and any(cell != (1.0, 0.0) for cell in used)
    assert all(sample["ratio"] == pytest.approx(1, abs=1e-5) for sample in rows)
    assert all(abs(sample["kl_mu_theta"]) < 1e-5 for sample in rows)
    assert not any(sample["outside_clip"] or "explore_floored" in sample for sample in rows)
    assert result["clip_fraction"] == 0 and abs(result["approx_kl_mu_theta"]) < 1e-6


def test_fast_source_records_each_rows_cell_from_the_header_class():
    from test_rl_fast_source import COMPILER, SAMPLING, Game, envelope_for

    from stpd.rl.fast_source import decode_fast_packet

    high = {**SAMPLING, "arm_id": "high",
            "classes": {"combat": {"temperature": 1.4, "uniform_mix": 0.005},
                        "noncombat": {"temperature": 0.5, "uniform_mix": 0.01}}}
    g = Game(sampling=high)
    g.open("g1")
    for step in range(64 + 1):
        g.step("g1", in_combat=step % 2 == 0)
    [segment] = g.sealed
    source, _ = decode_fast_packet(segment.data, envelope=envelope_for(segment),
                                   compiler=COMPILER)
    for meta, row in zip(source.metadata, segment.header["rows"], strict=True):
        cell = high["classes"]["combat" if row["in_combat"] else "noncombat"]
        assert meta["arm_sampling"] == [cell["temperature"], cell["uniform_mix"]]
    assert {tuple(meta["arm_sampling"]) for meta in source.metadata} == {(1.4, .005), (.5, .01)}


PARALLEL = {"sgd_data_parallel": {"world": 2, "backend": "gloo", "timeout_seconds": 120}}


@pytest.mark.parametrize("ranks", [1, 2])
def test_sgd_child_steps_on_rows_of_two_cells_and_its_dump_replays(tmp_path, monkeypatch,
                                                                   ranks):
    """"own" through the SGD child (one rank, and two data-parallel ranks): the PPO rows of
    a hot arm's game carry two cells (combat, noncombat); the dumped attempt carries the
    cells and the loss rule, and replays offline; without the cells it is refused."""
    from test_rl_fast_learner import _sender, admission, closed_for, fast_learner, learner_mu
    from test_rl_fast_source import SAMPLING, combat_game
    from test_rl_pipeline_checkpoint import SGD_PROCESS

    from stpd.p1.frame_compiler import FrameCompiler

    high = {**SAMPLING, "arm_id": "high",
            "classes": {"combat": {"temperature": 1.4, "uniform_mix": 0.005},
                        "noncombat": {"temperature": 2.5, "uniform_mix": 0.01}}}
    dumps = tmp_path / "dumps"
    owner = fast_learner(tmp_path, monkeypatch, aux_labels=["terminal", "hp"],
                         dataplane_extra={**SGD_PROCESS, "sgd_prestart": False,
                                          "sgd_offline_dump_every": 1,
                                          "sgd_offline_dump_limit": 1,
                                          "sgd_offline_dump_directory": str(dumps),
                                          **(PARALLEL if ranks == 2 else {})},
                         segments={"target_kl": 0.99, "diagnostics_every": 1000},
                         arm_policy_update="own")
    try:
        assert owner.sgd is not None
        compiler = FrameCompiler(owner.input_preparer.vocabulary)
        for index, name in enumerate(("g1", "g2"), 1):
            sealed = combat_game(name, owner.generation, handoff=learner_mu(owner),
                                 compiler=compiler, assembler_id="node-" + name,
                                 sampling=high)
            admission(owner, name, index)
            ours, theirs = multiprocessing.Pipe()
            process = multiprocessing.Process(target=_sender,
                                              args=(theirs, sealed, closed_for(sealed), True))
            process.start()
            theirs.close()
            reader = owner.attach_fast_handoff(ours)
            process.join(30)
            assert process.exitcode == 0
            reader.join(30)
            assert not reader.is_alive() and not owner.input_fenced
        assert owner._prepare()
        batch = owner.active_batch
        assert batch is not None
        cells = batch_arm_sampling(batch.metadata, batch.request)
        assert set(cells[:len(batch.metadata)]) == {(1.4, 0.005), (2.5, 0.01)}
        results = []
        while owner.active is not None:
            results.append(owner._step())
        owner._collect_publication(wait=True)
        assert any(result["stepped"] for result in results)
        if ranks == 2:
            assert all(result["data_parallel"]["world"] == 2 for result in results)
    finally:
        owner.close()
    [root] = sorted(dumps.iterdir())
    value, minibatch, _ = offline_step.load(root)
    assert value["loss"]["arm_policy_update"] == "own"
    dumped = {(round(t, 4), round(m, 4)) for t, m in minibatch.tensors["arm_sampling"].tolist()}
    assert dumped & {(1.4, 0.005), (2.5, 0.01)} and dumped <= {(1.4, 0.005), (2.5, 0.01),
                                                               NORMAL_SAMPLING}
    _, _, result = offline_step.replay(copy.deepcopy(value), minibatch)
    assert result["rows"] == minibatch.td_rows
    assert all(math.isfinite(result[key]) for key in ("policy_loss", "approx_kl_mu_theta"))
    stripped = replace(minibatch, tensors={k: v for k, v in minibatch.tensors.items()
                                           if k != "arm_sampling"})
    with pytest.raises(ValueError, match="arm_sampling_must_match"):
        offline_step.replay(copy.deepcopy(value), stripped)
