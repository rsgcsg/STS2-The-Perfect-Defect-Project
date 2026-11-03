"""Copying a model retains its parameters, never a previous mu's runtime cache."""
from __future__ import annotations

import copy

import pytest
import torch
from test_rl_text_reuse import model_and_frames

from stpd.p1.text_cache import TextVectorCache


@pytest.mark.parametrize("stream_safe", [False, True])
def test_cache_copy_owns_fresh_lock_and_empty_bound_state(stream_safe):
    original = TextVectorCache(4096, stream_safe=stream_safe)
    with torch.inference_mode():
        original.put("old-mu", (b"same-input",), torch.ones(1, 192))
        assert original.get("old-mu", b"same-input") is not None
    before = original.report()
    aliases = copy.deepcopy([original, original])
    copied = aliases[0]
    assert copied is aliases[1] and copied is not original
    assert copied.lock is not original.lock
    assert copied.limit == original.limit and copied.stream_safe == stream_safe
    assert copied.report() == {"limit_bytes": 4096, "accounted_bytes": 0, "rows": 0,
        "blocks": 0, "hits": 0, "misses": 0, "evictions": 0}
    assert original.report() == before
    with torch.inference_mode():
        copied.put("old-mu", (b"same-input",), torch.zeros(1, 192))
        assert torch.equal(copied.get("old-mu", b"same-input"), torch.zeros(192))
        assert torch.equal(original.get("old-mu", b"same-input"), torch.ones(192))
    original.retire(set())
    assert copied.report()["rows"] == 1 and original.report()["rows"] == 0


def test_frozen_model_copy_rebuilds_cache_after_parameter_change():
    model, frames = model_and_frames()
    model.requires_grad_(False)
    model.deduplicate_text = True
    model.text_cache = TextVectorCache(1024 * 1024, stream_safe=True)
    model.text_cache_namespace = "old-mu"
    with torch.inference_mode():
        original_output = model.forward_rl4(frames, [3, 3, 3])
    assert model.text_cache.report()["rows"] > 0
    copied = copy.deepcopy(model)
    assert copied.text_cache is not model.text_cache
    assert copied.text_cache.report()["rows"] == copied.text_cache.report()["blocks"] == 0
    assert all(torch.equal(value, copied.state_dict()[name])
               for name, value in model.state_dict().items())
    with torch.no_grad():
        copied.text_projection.weight[0].add_(.1)
    reference = copy.deepcopy(copied)
    with torch.inference_mode():
        changed = copied.forward_rl4(frames, [3, 3, 3])
        expected = reference.forward_rl4(frames, [3, 3, 3])
        repeated_original = model.forward_rl4(frames, [3, 3, 3])
    for actual, wanted in zip(changed[0], expected[0], strict=True):
        assert torch.equal(actual, wanted)
    for actual, wanted in zip(changed[1:], expected[1:], strict=True):
        assert torch.equal(actual, wanted)
    for actual, wanted in zip(original_output[0], repeated_original[0], strict=True):
        assert torch.equal(actual, wanted)
    assert copied.text_cache.report()["rows"] > 0


@pytest.mark.skipif(not torch.cuda.is_available(), reason="requires real CUDA hardware")
def test_cuda_cache_copy_does_not_copy_ready_events_or_device_storage():
    original = TextVectorCache(4096, stream_safe=True)
    producer = torch.cuda.Stream()
    with torch.cuda.stream(producer), torch.inference_mode():
        original.put("old-mu", (b"key",), torch.ones(1, 192, device="cuda"))
    assert original.blocks and next(iter(original.blocks.values()))[4] is not None
    copied = copy.deepcopy(original)
    assert not copied.blocks and not copied.rows and copied.lock is not original.lock
    with torch.inference_mode():
        value = original.get("old-mu", b"key")
        assert torch.equal(value.cpu(), torch.ones(192))
    original.retire(set())
    assert copied.report()["rows"] == 0
