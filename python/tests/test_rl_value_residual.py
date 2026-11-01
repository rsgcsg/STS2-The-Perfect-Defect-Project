"""Residual critic isolation, padding and exact policy export boundaries."""

import copy

import torch
from test_p1_t0 import observation

from stpd.p1.export import action_weights
from stpd.p1.input import fit_vocabulary, project
from stpd.p1.model import MStd
from stpd.p1.value_head import AttentionValueResidual
from stpd.rl.critic import enable_value_head
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint


def test_zero_head_is_exact_rng_policy_value_and_export_identity_then_critic_isolated():
    torch.set_num_threads(2)
    torch.manual_seed(5)
    frame = project(observation())
    model = MStd(fit_vocabulary([frame])).eval()
    enable_value_head(model, 7)
    with torch.no_grad():
        model.progress_head.weight.normal_()
        old_scores, old_values = model.forward_with_values([frame])
    old_action = {
        k: v.clone() for k, v in action_weights(model.state_dict(), value_heads=True).items()
    }
    rng = torch.get_rng_state().clone()
    model.enable_progress_residual(42)
    assert torch.equal(rng, torch.get_rng_state())
    with torch.no_grad():
        scores, values = model.forward_with_values([frame])
    assert torch.equal(old_scores[0], scores[0])
    assert torch.equal(old_values, values)
    assert sum(p.numel() for p in model.progress_residual.parameters()) == 313025
    for p in model.progress_residual.parameters():
        p.requires_grad_(True)
    _, differentiable_values = model.forward_with_values([frame])
    (differentiable_values[:, 1] - 3).square().mean().backward()
    assert model.progress_head.weight.grad is not None
    assert all(
        p.grad is None
        for n, p in model.named_parameters()
        if not n.startswith(("win_head.", "progress_head.", "progress_residual."))
    )
    optimizer = torch.optim.AdamW(model.progress_residual.parameters(), lr=3e-4)
    optimizer.step()
    model.zero_grad(set_to_none=True)
    with torch.no_grad():
        scores, values = model.forward_with_values([frame])
    assert torch.equal(scores[0], old_scores[0])
    assert not torch.equal(old_values, values)
    exported = action_weights(model.state_dict(), value_heads=True)
    assert exported.keys() == old_action.keys()
    assert all(torch.equal(exported[k], v) for k, v in old_action.items())
    state = decode_checkpoint(encode_checkpoint({"weights": model.state_dict()}))
    restored = copy.deepcopy(model)
    restored.load_state_dict(state["weights"])
    with torch.no_grad():
        again, again_values = restored.forward_with_values([frame])
    assert torch.equal(again[0], scores[0]) and torch.equal(again_values, values)


def test_attention_excludes_padding_and_value_features_are_detached():
    torch.set_num_threads(2)
    head = AttentionValueResidual().eval()
    with torch.no_grad():
        head.mlp[-1].weight.normal_()
    scene = torch.randn(2, 4, 192)
    mask = torch.tensor([[False, False, True, True], [False, False, False, False]])
    mean = torch.randn(2, 192)
    with torch.no_grad():
        original = head(scene, mask, mean)
        changed = scene.clone()
        changed[0, 2:] = 1e6
        assert torch.equal(original, head(changed, mask, mean))
    frame = project(observation())
    model = MStd(fit_vocabulary([frame])).eval()
    scene, mask, mean = model.value_features([frame])
    assert not scene.requires_grad and not mask.requires_grad and not mean.requires_grad


def test_declared_residual_parameter_addition_preserves_every_old_Adam_moment():
    import pytest

    from spireagent.json_boundary import BoundaryError
    from stpd.rl.critic import CriticConfig, optimizer_groups, restore_optimizer

    torch.set_num_threads(2)
    frame = project(observation())
    model = MStd(fit_vocabulary([frame])).eval()
    enable_value_head(model, 7)
    config = CriticConfig(head_lr=1e-4)
    old = torch.optim.AdamW(optimizer_groups(model, config), lr=4e-5, weight_decay=0)
    # Materialize moments for every legacy parameter, independent of unused branches.
    for p in model.parameters():
        p.grad = torch.full_like(p, 0.01)
    old.step()
    old.zero_grad(set_to_none=True)
    old_state = copy.deepcopy(old.state_dict())
    old_moments = {n: copy.deepcopy(old.state[p]) for n, p in model.named_parameters()}
    model.enable_progress_residual(9)
    successor = torch.optim.AdamW(optimizer_groups(model, config), lr=4e-5, weight_decay=0)
    with pytest.raises(BoundaryError, match="not_declared"):
        restore_optimizer(
            successor, model, old_state, migrate=False, new_prefixes=("progress_residual.",)
        )
    restore_optimizer(
        successor, model, old_state, migrate=True, new_prefixes=("progress_residual.",)
    )
    for n, p in model.named_parameters():
        if n.startswith("progress_residual."):
            assert p not in successor.state
        else:
            assert all(torch.equal(v, successor.state[p][k]) for k, v in old_moments[n].items())
    assert [g["lr"] for g in successor.param_groups] == [4e-5, 1e-4]
    roundtrip = torch.optim.AdamW(optimizer_groups(model, config), lr=4e-5, weight_decay=0)
    restore_optimizer(roundtrip, model, successor.state_dict(), migrate=False)
    assert [g["lr"] for g in roundtrip.param_groups] == [4e-5, 1e-4]


def test_full_CP_residual_cut_preserves_policy_rng_buffer_critic_and_next_update(
    tmp_path, monkeypatch
):
    import hashlib
    import json
    from dataclasses import replace

    from test_rl_critic import _configured, _incoming

    from spireagent.artifact_contracts import Producer
    from spireagent.workbench.rl_learner import IO
    from stpd.rl import learner as module
    from stpd.rl.learner import Learner

    first, _ = _configured(tmp_path, monkeypatch)
    first.critic.ready = True
    first.critic.freeze_policy(False)
    rows = [_incoming(first, "train", "train-shard")]
    first._admit(rows)
    first.train_once()
    monkeypatch.setattr(Learner, "_read_once", lambda _self, shard, **_: copy.deepcopy(rows))
    first.maybe_version(force=True)
    first._collect_version(wait=True)
    first._take_checkpoint(wait=True)
    (first.work / "STOP").touch()
    first.run()
    cp = first.exchange.latest_checkpoint()
    old_producer = first.producer
    candidate = tmp_path / "head.bin"
    head = AttentionValueResidual()
    with torch.no_grad():
        head.mlp[-1].bias.fill_(0.001)
    candidate.write_bytes(encode_checkpoint({"weights": head.state_dict()}))
    h = hashlib.sha256(candidate.read_bytes()).hexdigest()
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps({"passed": True, "candidate_sha256": h}))
    upgrade = {
        "candidate_path": str(candidate),
        "candidate_sha256": h,
        "qualification_path": str(qualification),
        "qualification_sha256": hashlib.sha256(qualification.read_bytes()).hexdigest(),
    }
    monkeypatch.setattr(module, "source_identity", lambda _: Producer("test", "c" * 40, "b" * 64))
    critic = replace(first.config.critic, head_kind="attention-residual-v1", head_lr=3e-4)
    changes = {
        k: {"old": getattr(first.config.critic, k), "new": getattr(critic, k)}
        for k in ("head_kind", "head_lr")
    }
    config = replace(
        first.config,
        resume_checkpoint=cp["checkpoint_artifact_id"],
        resume_source=old_producer.to_dict(),
        resume_critic_changes=changes,
        resume_value_head=upgrade,
        critic=critic,
    )
    successor = Learner(config, tmp_path / "residual", IO)
    assert successor.updates == first.updates
    assert successor.random.getstate() == first.random.getstate()
    assert successor.critic.target_mean == first.critic.target_mean
    assert successor.critic.fixed_validation_ids == first.critic.fixed_validation_ids
    assert successor.runtime_control == first.runtime_control
    assert successor.ledger.counts == first.ledger.counts
    assert [i.sample.key for i in successor.buffer] == [i.sample.key for i in first.buffer]
    for k, v in first.model.state_dict().items():
        assert torch.equal(v, successor.model.state_dict()[k])
    for n, p in first.model.named_parameters():
        q = dict(successor.model.named_parameters())[n]
        assert all(
            torch.equal(v, successor.optimizer.state[q][k])
            for k, v in first.optimizer.state[p].items()
        )
    before = {
        k: v.clone()
        for k, v in action_weights(successor.model.state_dict(), value_heads=True).items()
    }
    assert before.keys() == action_weights(first.model.state_dict(), value_heads=True).keys()
    successor.train_once()
    assert successor.updates == first.updates + 1
    (successor.work / "STOP").touch()
    successor.run()
    new_cp = successor.exchange.latest_checkpoint()
    third = Learner(
        replace(
            config,
            resume_checkpoint=new_cp["checkpoint_artifact_id"],
            resume_value_head=None,
            resume_source=None,
            resume_critic_changes=None,
        ),
        tmp_path / "residual-again",
        IO,
    )
    assert third.updates == successor.updates
    for k, v in successor.model.state_dict().items():
        assert torch.equal(v, third.model.state_dict()[k])
    for n, p in successor.model.named_parameters():
        q = dict(third.model.named_parameters())[n]
        assert all(
            torch.equal(v, third.optimizer.state[q][k])
            for k, v in successor.optimizer.state[p].items()
        )
    (third.work / "STOP").touch()
    third.run()
