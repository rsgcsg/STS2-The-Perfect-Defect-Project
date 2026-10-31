"""State credit assignment, independent warmup and full checkpoint consumers."""
from __future__ import annotations

import copy
from dataclasses import replace

import pytest
import torch
from test_p1_t0 import observation

from spireagent.json_boundary import BoundaryError
from stpd.p1.input import fit_vocabulary, project
from stpd.p1.model import MStd
from stpd.rl.critic import (
    CriticConfig,
    CriticEpisode,
    OnlineCritic,
    gae,
    held_out,
    terminal_targets,
)
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint


def test_terminal_gae_each_state_and_no_terminal_bootstrap():
    # lambda=1 telescopes to discounted MC minus that state's baseline.
    values = [0.2, 0.1, 0.4]
    assert gae(1, values, 1, 1) == pytest.approx([0.8, 0.9, 0.6])
    assert gae(1, values, 0.9, 1) == pytest.approx([0.81 - .2, .9 - .1, 1 - .4])
    assert gae(1, values, 1, 0.5) == pytest.approx([.2, .6, .6])
    assert terminal_targets(.8, 3, .9) == pytest.approx([.648, .72, .8])
    assert gae(1, [], 1, 1) == []
    for kwargs in ({"gamma": 0}, {"gae_lambda": float("nan")}, {"batch_size": True},
                   {"warmup_seconds": -1}, {"max_rows": 1}, {"min_updates": 3000}):
        with pytest.raises(BoundaryError):
            CriticConfig(**kwargs)


def _model():
    frame = project(observation())
    return MStd(fit_vocabulary([frame])).eval(), frame


def _seed(validation):
    return next(str(i) for i in range(100) if held_out(str(i)) == validation)


def test_critic_is_independent_and_does_not_consume_policy_rng_or_fit_held_out():
    policy, frame = _model()
    before = copy.deepcopy(policy.state_dict())
    rng = torch.get_rng_state().clone()
    critic = OnlineCritic(policy, CriticConfig(batch_size=2), 7)
    assert torch.equal(rng, torch.get_rng_state())
    # All rows below are holdout: regression must refuse to fit any of them.
    critic.add(CriticEpisode("validation", "shard", _seed(True), 1, 0, [frame] * 2, ["v:0", "v:1"]))
    assert critic.update() is None
    critic.add(CriticEpisode("train", "shard", _seed(False), .5, 0, [frame] * 2, ["t:0", "t:1"]))
    result = critic.update()
    assert result["critic_updates"] == 1
    assert critic.predict([frame, frame]) == pytest.approx(critic.predict([frame, frame]))
    assert torch.equal(rng, torch.get_rng_state())
    for key, value in policy.state_dict().items():
        assert torch.equal(value, before[key])
    assert critic.predict([frame])[0] > 0


def test_critic_checkpoint_preserves_network_optimizer_rng_and_next_update():
    policy, frame = _model()
    config = CriticConfig(batch_size=2)
    first = OnlineCritic(policy, config, 19)
    episode = CriticEpisode("train", "shard", _seed(False), .5, 0,
                            [frame] * 3, ["t:0", "t:1", "t:2"])
    first.add(episode)
    first.update()
    saved = decode_checkpoint(encode_checkpoint({"weights": first.network.state_dict(),
        "optimizer": first.optimizer.state_dict()}))
    second = OnlineCritic(policy, config, 19)
    second.restore(first.state())
    second.add(episode)
    second.network.load_state_dict(saved["weights"])
    second.optimizer.load_state_dict(saved["optimizer"])
    one, two = first.update(), second.update()
    assert one.pop("ids") == two.pop("ids")
    assert one == pytest.approx(two)
    for key, value in first.network.state_dict().items():
        assert torch.equal(value, second.network.state_dict()[key])
    with pytest.raises(BoundaryError, match="resume_configuration_mismatch"):
        OnlineCritic(policy, replace(config, gae_lambda=.99), 19).restore(first.state())


def test_validation_requires_held_out_variance_mse_and_minimum_games(monkeypatch):
    policy, frame = _model()
    config = CriticConfig(batch_size=1, min_train_episodes=1,
                          min_validation_episodes=2, min_updates=1)
    critic = OnlineCritic(policy, config, 1)
    critic.updates = 1
    critic.add(CriticEpisode("train", "s", _seed(False), .5, 0, [frame], ["t:0"]))
    critic.add(CriticEpisode("v1", "s", _seed(True), .1, 0, [frame], ["v1:0"]))
    monkeypatch.setattr(critic, "predict", lambda frames: [.1] * len(frames))
    assert not critic.validate()["passed"]  # no variance and too few games
    other = copy.deepcopy(frame)
    another = next(str(i) for i in range(100) if held_out(str(i)) and str(i) != _seed(True))
    critic.add(CriticEpisode("v2", "s", another, .9, 0, [other], ["v2:0"]))
    monkeypatch.setattr(critic, "predict",
                        lambda frames: [.1 if f is frame else .9 for f in frames])
    report = critic.validate()
    assert report["passed"] and report["explained_variance"] == 1
    monkeypatch.setattr(critic, "predict", lambda frames: [.5] * len(frames))
    assert not critic.validate()["passed"]


def test_learner_critic_warmup_checkpoint_fifo_and_critic_only_final_save(tmp_path, monkeypatch):
    from test_rl_engineering import configured, incoming

    from spireagent.workbench.rl_learner import IO
    from stpd.game_seed import derive_game_seed
    from stpd.rl.learner import Learner

    config = CriticConfig(batch_size=1, min_train_episodes=2,
                          min_validation_episodes=2, min_updates=1)
    learner, original = configured(tmp_path, monkeypatch, critic=config)
    rows = []
    # Deliberately reverse shard lexical order relative to admission FIFO.
    for number, shard in enumerate(("z-shard", "a-shard")):
        row = incoming(learner, run=f"run-{number}")
        seed = next(derive_game_seed("critic-unit-test", i) for i in range(number * 100 + 1,
            number * 100 + 100) if not held_out(derive_game_seed("critic-unit-test", i)))
        row["episode"]["identity"]["seed"] = seed
        row["key"] = shard
        rows.append(row)
    learner._admit(rows)
    assert learner.train_once() is None
    assert learner.updates == 0 and learner.critic.updates == 1
    assert learner.ledger.counts["sample_uses"] == 0
    for key, value in original.state_dict().items():
        assert torch.equal(learner.model.state_dict()[key], value)
    monkeypatch.setattr(Learner, "_read_once", lambda _self, shard, **_: [
        copy.deepcopy(r) for r in rows if r["key"] == shard])
    learner.maybe_version(force=True)
    learner._collect_version(wait=True)
    learner._take_checkpoint(wait=True)
    assert learner.exchange.latest_model()["version"] == 1
    # No policy update, but another successful critic update must get a NEW
    # immutable model/checkpoint record at stop, retaining all critic work.
    learner.critic.update()
    learner.exchange.request_stop("test_completed")
    learner.run()
    checkpoint = learner.exchange.latest_checkpoint()
    assert checkpoint["version"] == 2
    successor = Learner(replace(learner.config,
        resume_checkpoint=checkpoint["checkpoint_artifact_id"]),
                        tmp_path / "successor", IO)
    assert successor.updates == 0 and successor.critic.updates == learner.critic.updates
    assert [e.shard for e in successor.critic.episodes] == ["z-shard", "a-shard"]
    assert successor.critic.random.getstate() == learner.critic.random.getstate()
    one, two = learner.critic.update(), successor.critic.update()
    assert one.pop("ids") == two.pop("ids")
    assert one == pytest.approx(two)
    for key, value in learner.critic.network.state_dict().items():
        assert torch.equal(value, successor.critic.network.state_dict()[key])
    successor.run()  # existing STOP performs normal owned cleanup


def test_failed_warmup_blocks_policy_and_unknown_has_no_label(tmp_path, monkeypatch):
    from test_rl_engineering import configured, incoming

    critic = CriticConfig(batch_size=1, min_updates=1, warmup_max_updates=1,
                          min_train_episodes=10, min_validation_episodes=10)
    learner, original = configured(tmp_path, monkeypatch, critic=critic)
    row = incoming(learner)
    row["episode"]["outcome"].update(status="unknown", unknown=True)
    learner._admit([row])
    assert not learner.critic.episodes and not learner.buffer
    assert learner.counts["episodes_unknown"] == 1
    # A no-data warmup has its own finite time stop, too.
    learner.critic_began -= critic.warmup_seconds + 1
    with pytest.raises(BoundaryError, match="warmup_not_qualified"):
        learner.run()
    assert learner.updates == learner.ledger.counts["sample_uses"] == 0
    for key, value in original.state_dict().items():
        assert torch.equal(learner.model.state_dict()[key], value)
