"""Text reuse preserves scene/policy/head gradients and never caches live theta."""

from __future__ import annotations

import copy
from dataclasses import replace

import torch
from test_p1_input_v2 import public_state

from stpd.p1.input import V2_INPUT_SPEC, project
from stpd.p1.model import MStd
from stpd.p1.tensor_input import compile_frame, text_input_keys
from stpd.p1.text_cache import TextVectorCache
from stpd.p1.vocabulary import public_vocabulary


def model_and_frames():
    torch.set_num_threads(2)
    torch.manual_seed(37)
    model = MStd(public_vocabulary()).eval()
    model.enable_rl4_heads(7)
    with torch.no_grad():
        model.rl4_heads.outcome.mlp[-1].weight.normal_(0, .02)
        model.rl4_heads.hp.mlp[-1].weight.normal_(0, .02)
    model.execution = replace(
        model.execution,
        implementation="experimental-packed-v1",
        scene_attention_area=67108864,
        text_attention_area=67108864,
    )
    state = public_state()
    changed = copy.deepcopy(state)
    changed["scene"]["content"]["surface"]["prompt"] = "Deal 19 damage, gain 3 Block."
    return model, [
        compile_frame(project(value, input_projection=V2_INPUT_SPEC), model)
        for value in (state, state, changed)
    ]


def objective(output):
    scores, logits, hp = output
    return (
        sum(value.square().sum() for value in scores)
        + hp.square().sum()
        + logits.masked_fill(~torch.isfinite(logits), 0).square().sum() / 100
    )


def test_same_forward_text_dedup_preserves_real_policy_and_both_head_gradients():
    baseline, frames = model_and_frames()
    optimized = copy.deepcopy(baseline)
    optimized.deduplicate_text = True
    first = baseline.forward_rl4(frames, [3, 3, 3])
    second = optimized.forward_rl4(frames, [3, 3, 3])
    for a, b in zip(first[0], second[0], strict=True):
        torch.testing.assert_close(a, b, atol=3e-6, rtol=3e-5)
    for a, b in zip(first[1:], second[1:], strict=True):
        torch.testing.assert_close(a, b, atol=3e-6, rtol=3e-5)
    objective(first).backward()
    objective(second).backward()
    for (name, a), (other, b) in zip(
        baseline.named_parameters(), optimized.named_parameters(), strict=True
    ):
        assert name == other and (a.grad is None) == (b.grad is None)
        if a.grad is not None:
            torch.testing.assert_close(a.grad, b.grad, atol=2e-5, rtol=3e-4, msg=name)
    assert optimized.text_statistics["unique_rows"] < optimized.text_statistics["input_rows"]


def test_mu_cache_reuses_only_frozen_no_grad_values_and_is_not_training_state():
    model, frames = model_and_frames()
    model.deduplicate_text = True
    model.requires_grad_(False)
    model.text_cache = TextVectorCache(1024 * 1024)
    model.text_cache_namespace = "frozen-mu"
    with torch.inference_mode():
        first = model.forward_rl4(frames, [3, 3, 3])
        encoded = model.text_statistics["encoded_rows"]
        second = model.forward_rl4(frames, [3, 3, 3])
    assert model.text_statistics["encoded_rows"] == encoded
    assert model.text_cache.report()["hits"] > 0
    for a, b in zip(first[0], second[0], strict=True):
        torch.testing.assert_close(a, b, atol=0, rtol=0)
    assert not any("text_cache" in key for key in model.state_dict())
    # Even eval+no_grad theta must not reuse learned vectors across SGD.
    model.requires_grad_(True)
    before = model.text_cache.report()["hits"]
    with torch.no_grad():
        model.text_projection.weight.add_(0.001)
        model.forward_rl4(frames, [3, 3, 3])
    assert model.text_statistics["encoded_rows"] > encoded
    assert model.text_cache.report()["hits"] == before


def test_cache_keys_distinguish_numeric_values_and_evict_whole_storages():
    cache = TextVectorCache(2048)
    keys = text_input_keys((torch.tensor([[1, 2], [1, 2]]),
        torch.tensor([[[1.], [0.]], [[2.], [0.]]]), torch.tensor([2, 2])))
    assert keys[0] != keys[1]
    with torch.no_grad():
        cache.put("mu-one", (keys[0],), torch.ones(1, 192))
        assert cache.get("mu-one", keys[1]) is None
        assert cache.get("mu-two", keys[0]) is None
        cache.put("mu-two", (keys[0],), torch.zeros(1, 192))
        assert cache.get("mu-one", keys[0]) is None
        assert torch.equal(cache.get("mu-two", keys[0]), torch.zeros(192))
    assert cache.report()["accounted_bytes"] <= 2048
    cache.retire(set())
    assert cache.report()["accounted_bytes"] == cache.report()["rows"] == 0


def test_train_mode_dropout_keeps_original_occurrences():
    first, frames = model_and_frames()
    second = copy.deepcopy(first)
    first.train()
    second.train()
    second.deduplicate_text = True
    torch.manual_seed(21)
    a = first.forward_rl4(frames, [3, 3, 3])
    torch.manual_seed(21)
    b = second.forward_rl4(frames, [3, 3, 3])
    assert second.text_statistics["input_rows"] == 0
    for one, two in zip(a[0], b[0], strict=True):
        torch.testing.assert_close(one, two, atol=0, rtol=0)
