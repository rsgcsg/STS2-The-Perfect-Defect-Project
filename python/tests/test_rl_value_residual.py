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
    old_action = {k: v.clone() for k,v in action_weights(model.state_dict(), value_heads=True).items()}
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
    (differentiable_values[:,1] - 3).square().mean().backward()
    assert model.progress_head.weight.grad is not None
    assert all(p.grad is None for n,p in model.named_parameters()
               if not n.startswith(('win_head.', 'progress_head.', 'progress_residual.')))
    optimizer = torch.optim.AdamW(model.progress_residual.parameters(), lr=3e-4)
    optimizer.step()
    model.zero_grad(set_to_none=True)
    with torch.no_grad():
        scores, values = model.forward_with_values([frame])
    assert torch.equal(scores[0], old_scores[0])
    assert not torch.equal(old_values, values)
    exported = action_weights(model.state_dict(), value_heads=True)
    assert exported.keys() == old_action.keys()
    assert all(torch.equal(exported[k], v) for k,v in old_action.items())
    state = decode_checkpoint(encode_checkpoint({'weights': model.state_dict()}))
    restored = copy.deepcopy(model)
    restored.load_state_dict(state['weights'])
    with torch.no_grad():
        again, again_values = restored.forward_with_values([frame])
    assert torch.equal(again[0], scores[0]) and torch.equal(again_values, values)


def test_attention_excludes_padding_and_value_features_are_detached():
    torch.set_num_threads(2)
    head = AttentionValueResidual().eval()
    with torch.no_grad():
        head.mlp[-1].weight.normal_()
    scene = torch.randn(2,4,192)
    mask = torch.tensor([[False,False,True,True],[False,False,False,False]])
    mean = torch.randn(2,192)
    with torch.no_grad():
        original = head(scene,mask,mean)
        changed = scene.clone()
        changed[0,2:] = 1e6
        assert torch.equal(original,head(changed,mask,mean))
    frame = project(observation())
    model = MStd(fit_vocabulary([frame])).eval()
    scene, mask, mean = model.value_features([frame])
    assert not scene.requires_grad and not mask.requires_grad and not mean.requires_grad
