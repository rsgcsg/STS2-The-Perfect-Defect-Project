"""RL4 head, frozen short-segment target and frozen-trunk warmup checks."""
from __future__ import annotations

import copy
import hashlib
import json
import sys

import pytest
import torch
from test_p1_t0 import observation

from stpd.p1.export import action_weights, export_model, load_model
from stpd.p1.input import fit_vocabulary, project
from stpd.p1.model import MStd
from stpd.rl.heads import (
    OUTCOME_CLASSES,
    categorical_lambda_targets,
    checked_terminal_class,
    class_reward_values,
    outcome_cross_entropy,
    outcome_mask,
)
from stpd.rl.warmup import (
    FeatureExamples,
    WarmupConfig,
    WarmupExample,
    validation_seed,
    warmup_heads,
)


def member():
    torch.set_num_threads(2)
    torch.manual_seed(7)
    frame = project(observation())
    return MStd(fit_vocabulary([frame])).eval(), frame


def test_enable_preserves_action_weights_rng_and_export_load(tmp_path):
    model, frame = member()
    previous = copy.deepcopy(model.state_dict())
    scores = model.forward_batch([frame])[0].detach().clone()
    rng = torch.get_rng_state().clone()
    model.enable_rl4_heads(29, [0] * 49 + [1, 0])
    assert torch.equal(rng, torch.get_rng_state())
    assert all(torch.equal(value, model.state_dict()[key]) for key, value in previous.items())
    stripped = action_weights(dict(model.state_dict()))
    assert stripped.keys() == previous.keys()
    assert all(torch.equal(value, stripped[key]) for key, value in previous.items())
    assert torch.equal(scores, model.forward_batch([frame])[0])
    checkpoint = tmp_path / "checkpoint"
    checkpoint.write_bytes(b"unit-checkpoint")
    descriptor = export_model(model, checkpoint, tmp_path / "package", {"test": True})
    loaded, _ = load_model(tmp_path / "package/model.tensor-tree", descriptor["package_sha256"])
    assert torch.equal(scores, loaded.eval().forward_batch([frame])[0])
    assert descriptor["training_architecture"]["auxiliary_heads"] == [
        "rl4-outcome-attention-51", "rl4-hp-attention-1"]
    with pytest.raises(ValueError, match="action_only"):
        model.enable_rl4_heads(29)


def test_heads_are_independent_and_auxiliary_gradient_enters_trunk():
    model, frame = member()
    model.enable_rl4_heads(31)
    scores, logits, hp = model.forward_rl4([frame], [12])
    assert len(scores) == 1 and logits.shape == (1, OUTCOME_CLASSES) and hp.shape == (1,)
    assert torch.isneginf(logits[0, :12]).all()
    assert torch.equal(hp, torch.zeros_like(hp))
    before = logits.detach().clone()
    with torch.no_grad():
        model.rl4_heads.hp.mlp[-1].bias.add_(4)
    assert torch.equal(before, model.forward_rl4([frame], [12])[1])
    # Zero output initialization intentionally needs one head update before trunk
    # gradients appear. Test the online trained-head path, rather than removing it.
    with torch.no_grad():
        model.rl4_heads.outcome.mlp[-1].weight.normal_(0, .02)
    target = torch.nn.functional.one_hot(torch.tensor([24]), OUTCOME_CLASSES).float()
    outcome_cross_entropy(model.forward_rl4([frame], [12])[1], target).sum().backward()
    assert model.scene.layers[0].linear1.weight.grad.abs().sum() > 0
    assert model.rl4_heads.hp.mlp[-1].weight.grad is None
    model.zero_grad(set_to_none=True)
    with torch.no_grad():
        model.rl4_heads.hp.mlp[-1].weight.normal_(0, .02)
    model.forward_rl4([frame], [12])[2].square().sum().backward()
    assert model.scene.layers[0].linear1.weight.grad.abs().sum() > 0
    assert model.rl4_heads.outcome.mlp[-1].weight.grad is None


def test_value_only_skips_action_readout_and_matches_auxiliary_values():
    model, frame = member()
    model.enable_rl4_heads(41)
    full = model.forward_rl4([frame], [3])
    called = []
    hook = model.cross.register_forward_hook(lambda *_: called.append(True))
    try:
        scores, outcome, hp = model.forward_rl4([frame], [3], policy=False)
    finally:
        hook.remove()
    assert scores == [] and called == []
    assert torch.equal(outcome, full[1]) and torch.equal(hp, full[2])


def test_masked_soft_targets_have_finite_loss_and_gradient():
    logits = torch.randn(2, OUTCOME_CLASSES, requires_grad=True)
    masked = logits.masked_fill(~outcome_mask([0, 48]), -torch.inf)
    target = torch.zeros(2, OUTCOME_CLASSES)
    target[0, 10] = .4
    target[0, 49] = .6
    target[1, 48:50] = .5
    loss = outcome_cross_entropy(masked, target)
    loss.sum().backward()
    assert torch.isfinite(loss).all() and torch.isfinite(logits.grad).all()
    assert torch.equal(logits.grad[1, :48], torch.zeros(48))
    target[1, 0], target[1, 48] = .1, .4
    with pytest.raises(ValueError, match="invalid_outcome"):
        outcome_cross_entropy(masked, target)


@pytest.mark.parametrize("count", [1, 64])
@pytest.mark.parametrize("terminal", [False, True])
def test_frozen_lambda_distribution_matches_scalar_gae(count, terminal):
    torch.manual_seed(13)
    p = torch.rand(count, OUTCOME_CLASSES).softmax(-1).requires_grad_()
    boundary = None if terminal else torch.rand(OUTCOME_CLASSES).softmax(-1)
    q, advantage = categorical_lambda_targets(p, boundary,
        terminal_class=49 if terminal else None, gae_lambda=.99)
    assert not q.requires_grad and not advantage.requires_grad
    g = class_reward_values()
    values = p.detach() @ g
    result = []
    future = 0.0
    for index in reversed(range(count)):
        if index == count - 1:
            delta = 1.0 - values[index] if terminal else boundary @ g - values[index]
        else:
            delta = values[index + 1] - values[index]
        future = delta + .99 * future
        result.append(future)
    assert torch.allclose(advantage, torch.stack(list(reversed(result))), atol=2e-6)
    assert torch.allclose(q.sum(1), torch.ones(count))
    with pytest.raises(ValueError, match="confirmed_boundary"):
        categorical_lambda_targets(p, None)


def test_reward_class_checks_actual_reward_never_fills_missing_boss_witness():
    assert checked_terminal_class(result="loss", floor=17, reward=.5 * (17 / 48)**2) == 17
    assert checked_terminal_class(result="loss", floor=18, reward=.5 * (18 / 48)**2 + .1) == 18
    assert checked_terminal_class(result="win", floor=48, reward=1) == 49
    assert checked_terminal_class(result="loss", floor=35, reward=.5 * (35 / 48)**2 + .1) == 50
    assert checked_terminal_class(result="loss", floor=35, reward=.5 * (35 / 48)**2 + .2) == 35
    assert outcome_mask([35])[0, 50] and not outcome_mask([36])[0, 50]
    # Golden Path adds two actual floors; reward-v2's floor term is already capped.
    assert checked_terminal_class(result="loss", floor=50, reward=.7) == 48
    assert checked_terminal_class(result="win", floor=50, reward=1) == 49
    assert torch.equal(outcome_mask([48])[0], outcome_mask([50])[0])
    assert set(torch.where(outcome_mask([50])[0])[0].tolist()) == {48, 49}
    with pytest.raises(ValueError, match="reward_mismatch"):
        checked_terminal_class(result="loss", floor=18, reward=.5 * (18 / 48)**2)


def test_normal_feature_reader_preserves_the_observed_alternate_map_reward(tmp_path):
    from stpd.workers.checkpoint_codec import encode_checkpoint

    raw = encode_checkpoint({"seed": "train-example", "scene": [torch.zeros(2, 192)],
        "mean": torch.zeros(1, 192), "floors": [35], "outcome_class": 35,
        "reward": .5 * (35 / 48)**2 + .1, "hp": [0.0]})
    (tmp_path / "features.bin").write_bytes(raw)
    manifest = {"model_package_sha256": "a" * 64, "terminal_label_encoding": "floor-win-1",
        "episodes": [{"path": "features.bin", "sha256": hashlib.sha256(raw).hexdigest(),
                      "rows": 1, "seed": "train-example"}]}
    (tmp_path / "features.json").write_text(json.dumps(manifest))
    row = FeatureExamples(tmp_path / "features.json", "a" * 64)[0]
    assert row.floor == 35 and row.outcome == 50
    assert abs(float(class_reward_values()[row.outcome]) - (.5 * (35/48)**2 + .1)) < 1e-6


def test_warmup_changes_only_heads_keeps_seed_holdout_and_best_epoch():
    model, _ = member()
    model.enable_rl4_heads(43)
    seeds = [f"warmup-{i}" for i in range(100)]
    selected = ([seed for seed in seeds if validation_seed(seed)][:2]
                + [seed for seed in seeds if not validation_seed(seed)][:3])
    examples = [WarmupExample(seed, torch.randn(3, 192), torch.randn(192),
                             2, outcome=12, hp=.75) for seed in selected]
    before = copy.deepcopy(action_weights(dict(model.state_dict())))
    flags = [parameter.requires_grad for parameter in model.parameters()]
    report = warmup_heads(model, examples, WarmupConfig(batch_size=2, max_epochs=2))
    assert report["train_games"] == 3 and report["validation_games"] == 2
    assert report["updates"] > 0 and report["optimizer_retained"] is False
    assert [parameter.requires_grad for parameter in model.parameters()] == flags
    assert all(torch.equal(value, model.state_dict()[key]) for key, value in before.items())
    for row in report["epochs"]:
        assert row["validation"]["hp_mae_points"] is not None
        assert row["validation"]["equal_game_mse"] is not None


def test_warmup_cli_streams_verified_tensor_tree_blocks(tmp_path, monkeypatch, capsys):
    from stpd.rl.warmup import FeatureExamples, main
    from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint

    model, _ = member()
    checkpoint = tmp_path / "source-checkpoint"
    checkpoint.write_bytes(b"unit-source")
    package = tmp_path / "package"
    descriptor = export_model(model, checkpoint, package, {"test": True})
    seeds = [f"warmup-cli-{i}" for i in range(100)]
    selected = [next(seed for seed in seeds if validation_seed(seed)),
                next(seed for seed in seeds if not validation_seed(seed))]
    records = []
    for index, seed in enumerate(selected):
        raw = encode_checkpoint({"seed": seed, "run_id": f"run-{index}",
            "scene": [torch.randn(3, 192), torch.randn(4, 192)],
            "mean": torch.randn(2, 192), "floors": [0, 1],
            "outcome_class": 12, "hp": [None, .8]})
        path = tmp_path / f"features-{index}.bin"
        path.write_bytes(raw)
        records.append({"path": path.name, "seed": seed, "rows": 2,
                        "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = tmp_path / "features.json"
    manifest.write_text(json.dumps({"model_package_sha256": descriptor["package_sha256"],
                                    "episodes": records}))
    examples = FeatureExamples(manifest, descriptor["package_sha256"])
    assert len(examples) == 4 and examples.seed_at(2) == selected[1]
    assert examples[0].hp is None and examples[1].hp == .8
    monkeypatch.setattr(sys, "argv", ["warmup", "--package", str(package),
        "--features", str(manifest), "--output", str(tmp_path / "warm"),
        "--device", "cpu", "--epochs", "1", "--seconds", "30"])
    main()
    report = json.loads(capsys.readouterr().out)
    assert report["train_games"] == report["validation_games"] == 1
    raw = (tmp_path / "warm/heads.tensor-tree").read_bytes()
    state = decode_checkpoint(raw)
    assert state["model_package_sha256"] == descriptor["package_sha256"]
    assert hashlib.sha256(raw).hexdigest() == report["heads_sha256"]
    broken = tmp_path / records[0]["path"]
    broken.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="feature_hash"):
        FeatureExamples(manifest, descriptor["package_sha256"])[0]


def test_head_metrics_keep_optional_rows_aligned_and_use_same_masked_constant():
    import math

    from stpd.rl.warmup import evaluate_heads

    model, frame = member()
    model.enable_rl4_heads(7)
    with torch.no_grad():
        scene, mask, mean = model.value_features([frame])
    features = scene[0, ~mask[0]].clone()
    examples = [WarmupExample('loss', features, mean[0], 50, 48, 0.0),
        WarmupExample('hp-only', features, mean[0], 50, None, .5),
        WarmupExample('win', features, mean[0], 50, 49, None)]
    result = evaluate_heads(model, examples, return_predictions=True)
    assert result['outcome_rows'] == 2 and result['hp_rows'] == 2
    assert result['nll'] == pytest.approx(math.log(2))
    assert result['brier'] == pytest.approx(.5)
    assert result['constant_brier'] == pytest.approx(.5)
    assert [row['index'] for row in result['predictions']] == [0, 2]
    assert [row['seed'] for row in result['predictions']] == ['loss', 'win']
    assert [row['hp_target'] for row in result['predictions']] == [0.0, None]
