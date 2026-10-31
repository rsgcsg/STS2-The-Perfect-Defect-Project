"""PPO credit assignment, faithful warmup and complete checkpoint consumers."""

from __future__ import annotations

import copy
import json
import time
from dataclasses import replace

import pytest
import torch
from test_p1_t0 import observation

from spireagent.json_boundary import BoundaryError
from stpd.game_seed import derive_game_seed
from stpd.p1.export import action_weights
from stpd.p1.input import fit_vocabulary, project
from stpd.p1.model import MStd
from stpd.rl.checkpoint import (
    LEGACY_SCHEMA,
    METADATA_ENCODING,
    PPO_SCHEMA,
    learner_state,
    metadata_tensor,
)
from stpd.rl.critic import (
    CriticConfig,
    CriticEpisode,
    OnlineCritic,
    enable_value_head,
    held_out,
    terminal_targets,
    trace_gae,
)
from stpd.rl.loss import LossConfig, RlSample, rl_update
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint


def seed(validation, offset=0):
    return next(
        derive_game_seed("rl3-critic-unit", i)
        for i in range(offset + 1, offset + 1000)
        if held_out(derive_game_seed("rl3-critic-unit", i)) == validation
    )


def controller(config=None):
    torch.set_num_threads(2)
    frame = project(observation())
    model = MStd(fit_vocabulary([frame])).eval()
    reference = copy.deepcopy(model)
    rng = torch.get_rng_state().clone()
    enable_value_head(model, 7)
    assert torch.equal(rng, torch.get_rng_state())
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4, weight_decay=0)
    critic = OnlineCritic(
        model, reference, optimizer, config or CriticConfig(batch_size=2), 7, None
    )
    return critic, frame


def episode(frame, name="train", validation=False, reward=0.5, count=2, offset=0):
    return CriticEpisode(
        name,
        "shard-" + name,
        seed(validation, offset),
        reward,
        0,
        [frame] * count,
        [0] * count,
        [0.5] * count,
        [[0.5, 0.5]] * count,
        [f"{name}:{i}" for i in range(count)],
        time.time() - 10,
    )


def test_terminal_trace_reduces_to_GAE_and_corrects_future_without_double_current_rho():
    values = [0.2, 0.1, 0.4]
    advantages, targets = trace_gae(1, values, [1, 1, 1], 1, 0.5)
    assert advantages == pytest.approx([0.2, 0.6, 0.6])
    assert targets == pytest.approx([0.4, 0.7, 1])
    a, v = trace_gae(1, values, [1, 1, 1], 0.9, 1)
    assert a == pytest.approx([0.61, 0.8, 0.6])
    assert v == pytest.approx([0.81, 0.9, 1])
    a, v = trace_gae(1, values, [0.5, 2, 0.25], 1, 0.5)
    assert a == pytest.approx([0.0875, 0.375, 0.6])
    assert v == pytest.approx([0.24375, 0.475, 0.55])
    assert terminal_targets(0.8, 3, 0.9) == pytest.approx([0.648, 0.72, 0.8])
    for ratios in ([1], [1, 0, 1], [1, float("inf"), 1]):
        with pytest.raises(BoundaryError):
            trace_gae(1, values, ratios, 1, 0.95)
    for kwargs in (
        {"gamma": 0},
        {"gae_lambda": float("nan")},
        {"batch_size": True},
        {"warmup_min_seconds": 7200},
        {"max_rows": 1},
    ):
        with pytest.raises(BoundaryError):
            CriticConfig(**kwargs)


def test_warmup_freezes_every_action_tensor_and_never_fits_held_out_seeds():
    critic, frame = controller()
    before = {
        k: v.clone() for k, v in action_weights(critic.model.state_dict(), value_heads=True).items()
    }
    rng = torch.get_rng_state().clone()
    critic.add(episode(frame, "validation", validation=True))
    assert critic.warmup_update() is None
    critic.add(episode(frame))
    result = critic.warmup_update()
    assert result["critic_updates"] == 1 and all(k.startswith("train:") for k in result["ids"])
    for key, value in before.items():
        assert torch.equal(critic.model.state_dict()[key], value)
    assert torch.equal(rng, torch.get_rng_state())
    assert critic.evaluate(critic.episodes[-1])[0][0] > 0
    critic.ready = True
    critic.freeze_policy(False)
    assert critic.model.ids.weight.requires_grad and not critic.model.win_head.weight.requires_grad
    assert critic.warmup_update() is None


def test_validation_needs_independent_seed_variance_and_better_than_constant(monkeypatch):
    config = CriticConfig(
        batch_size=1, min_train_episodes=1, min_validation_episodes=2, min_updates=1
    )
    critic, frame = controller(config)
    critic.updates = 1
    critic.add(episode(frame, reward=0.5, count=1))
    critic.add(episode(frame, "v1", True, 0.1, count=1))
    monkeypatch.setattr(critic, "evaluate", lambda e: ([e.reward] * len(e.frames), [1], [0]))
    assert not critic.validate()["passed"]
    critic.add(episode(frame, "v2", True, 0.9, count=1, offset=1000))
    assert critic.validate()["passed"]
    monkeypatch.setattr(critic, "evaluate", lambda e: ([0.5] * len(e.frames), [1], [0]))
    assert not critic.validate()["passed"]


def test_entropy_has_live_gradient_and_value_head_is_removed_from_actor_exports():
    critic, frame = controller()
    critic.freeze_policy(False)
    for parameter in critic.model.parameters():
        parameter.requires_grad_(False)
    critic.model.head[-1].weight.requires_grad_(True)
    critic.model.progress_head.weight.requires_grad_(True)
    with torch.no_grad():
        logits = critic.model.forward_batch([frame])[0]
        probabilities = logits.softmax(-1)
        before = float(-(probabilities * logits.log_softmax(-1)).sum())
    samples = [
        RlSample(
            frame,
            0,
            float(probabilities[0]),
            float(probabilities[0]),
            0,
            0,
            f"test:{i}",
            value_target=0,
            action_kind="event.choose",
            acting_kl=0,
        )
        for i in range(4)
    ]
    optimizer = torch.optim.SGD(critic.model.parameters(), lr=0.01)
    result = rl_update(
        critic.model,
        critic.reference,
        optimizer,
        samples,
        [],
        0.01,
        LossConfig(mode="ppo", kl_coef=0, anchor_coef=0, value_coef=0.5, entropy_coef=0.1),
    )
    with torch.no_grad():
        logits = critic.model.forward_batch([frame])[0]
        after = float(-(logits.softmax(-1) * logits.log_softmax(-1)).sum())
    assert after > before
    assert result["value_loss"] == 0 and result["acting_policy_kl"] == 0
    assert result["ratio_quantiles"]["0.5"] == pytest.approx(1, abs=1e-6)
    assert not any(
        k.startswith(("win_head.", "progress_head."))
        for k in action_weights(critic.model.state_dict(), value_heads=True)
    )


def _configured(tmp_path, monkeypatch, **extra):
    from test_rl_engineering import configured

    return configured(
        tmp_path,
        monkeypatch,
        critic=CriticConfig(
            batch_size=1,
            min_train_episodes=2,
            min_validation_episodes=2,
            min_updates=1,
            validation_every=1,
            warmup_min_seconds=0,
        ),
        loss=LossConfig(mode="ppo", value_coef=0.5, entropy_coef=0.01, anchor_coef=0),
        max_episode_age_seconds=600,
        **extra,
    )


def _incoming(learner, name, shard):
    from test_rl_engineering import incoming

    row = incoming(learner, run=name)
    row["key"] = shard
    row["episode"]["identity"]["seed"] = seed(False)
    row["record"]["games"] = [{"run_id": name, "started_at": time.time() - 10}]
    row["episode"]["steps"][0]["behavior"].update(temperature=1, uniform_mix=0)
    return row


def test_learner_warmup_only_CP_FIFO_and_next_head_update_restore(tmp_path, monkeypatch):
    from spireagent.workbench.rl_learner import IO
    from stpd.rl.learner import Learner

    learner, original = _configured(tmp_path, monkeypatch)
    rows = [_incoming(learner, f"run-{i}", shard) for i, shard in enumerate(("z-shard", "a-shard"))]
    learner._admit(rows)
    assert not learner.buffer and learner.counts["critic_warmup_episodes"] == 2
    assert learner.train_once() is None
    assert learner.updates == learner.ledger.counts["sample_uses"] == 0
    for key, value in original.state_dict().items():
        assert torch.equal(learner.model.state_dict()[key], value)
    monkeypatch.setattr(
        Learner,
        "_read_once",
        lambda _self, shard, **_: [copy.deepcopy(r) for r in rows if r["key"] == shard],
    )
    learner.maybe_version(force=True)
    learner._collect_version(wait=True)
    learner._take_checkpoint(wait=True)
    learner.critic.warmup_update()
    learner.exchange.request_stop("test_completed")
    learner.run()
    checkpoint = learner.exchange.latest_checkpoint()
    assert checkpoint["version"] == 2
    successor = Learner(
        replace(learner.config, resume_checkpoint=checkpoint["checkpoint_artifact_id"]),
        tmp_path / "successor",
        IO,
    )
    assert [e.shard for e in successor.critic.episodes] == ["z-shard", "a-shard"]
    assert successor.critic.random.getstate() == learner.critic.random.getstate()
    one, two = learner.critic.warmup_update(), successor.critic.warmup_update()
    assert one.pop("ids") == two.pop("ids")
    assert one == pytest.approx(two)
    for key, value in learner.model.state_dict().items():
        assert torch.equal(value, successor.model.state_dict()[key])
    successor.run()


def test_resume_preserves_original_episode_age_and_retires_expired_buffer(tmp_path, monkeypatch):
    from spireagent.workbench.rl_learner import IO
    from stpd.rl import learner as module
    from stpd.rl.learner import Learner

    first, _ = _configured(tmp_path, monkeypatch)
    first.critic.ready = True
    first.critic.freeze_policy(False)
    row = _incoming(first, "ready", "shard")
    first._admit([row])
    assert len(first.buffer) == 1
    monkeypatch.setattr(Learner, "_read_once", lambda *_args, **_kwargs: [copy.deepcopy(row)])
    first.exchange.request_stop("test_completed")
    first.run()
    checkpoint = first.exchange.latest_checkpoint()
    future = time.time() + 700
    monkeypatch.setattr(module.time, "time", lambda: future)
    second = Learner(
        replace(first.config, resume_checkpoint=checkpoint["checkpoint_artifact_id"]),
        tmp_path / "successor",
        IO,
    )
    assert second.buffer[0].episode_started_at == row["record"]["games"][0]["started_at"]
    assert second.usable() == []
    assert second.ledger.counts["retired_stale_uses0_decisions"] == 1
    second.run()


def test_PPO_metadata_exceeds_old_node_and_header_limits_without_raising_them():
    metadata = {
        "history": [{"step": i, "note": "x" * 160} for i in range(60000)],
        "buffer": [["s", "key", 2, 0.123, 8]],
        "random": [3, [1, 2], None],
    }
    state = {
        "schema": PPO_SCHEMA,
        "learner_encoding": METADATA_ENCODING,
        "learner": metadata_tensor(metadata),
        "weights": {"x": torch.tensor([0.1])},
    }
    raw = encode_checkpoint(state)
    header_size = int.from_bytes(raw[8:16], "little")
    assert header_size < 4096 and len(raw) > 8 * 1024 * 1024
    assert learner_state(decode_checkpoint(raw)) == metadata
    assert learner_state({"schema": LEGACY_SCHEMA, "learner": json.dumps({"old": 1})}) == {"old": 1}
