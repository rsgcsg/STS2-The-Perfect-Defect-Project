"""CPU boundaries and real-CUDA stream lifecycle checks; no parallel speed claim."""
from __future__ import annotations

import copy
import gc
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import torch
from test_p1_input_v2 import public_state

from stpd.p1.execution import MStdExecution
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.input import V2_INPUT_SPEC
from stpd.p1.local_inference import SegmentConnection, SegmentInferencePool
from stpd.p1.model import MStd
from stpd.p1.text_cache import TextVectorCache
from stpd.p1.vocabulary import public_vocabulary

CUDA = pytest.mark.skipif(not torch.cuda.is_available(), reason="requires real CUDA hardware")


def member():
    torch.set_num_threads(2)
    torch.manual_seed(17)
    model = MStd(public_vocabulary(), MStdExecution.for_plan("mstd-training-v1")).eval()
    model.enable_rl4_heads(19)
    model.deduplicate_text = True
    return model.requires_grad_(False)


class CountedLock:
    def __init__(self):
        self.lock = threading.RLock()
        self.entries = 0

    def __enter__(self):
        self.lock.acquire()
        self.entries += 1

    def __exit__(self, *args):
        self.lock.release()


class NoTrainingLock:
    def __enter__(self):
        raise AssertionError("side-stream scorer acquired the training GPU lock")

    def __exit__(self, *args):
        pass


def pool_for(model, gpu_lock, monkeypatch, *, inference_stream=False):
    identity = {"snapshot_sha256": "a" * 64}
    pool = SegmentInferencePool(model, identity, control=lambda request: {}, gpu_lock=gpu_lock,
        address=("127.0.0.1", 0), max_batch=2, window_ms=0, inference_stream=inference_stream)
    for key, value in pool.environment().items():
        monkeypatch.setenv(key, value)
    return pool, identity


def request(client, model, identity, observation):
    return client.request("score", snapshot_sha256=identity["snapshot_sha256"], floor=0,
        input_projection=V2_INPUT_SPEC,
        compiled_input=FrameCompiler(model.vocabulary).encode(observation))


def test_cpu_explicit_stream_request_is_rejected_before_any_service_or_cuda(monkeypatch):
    model = member()

    def unexpected(*args, **kwargs):
        raise AssertionError("CPU stream rejection attempted CUDA/service allocation")

    monkeypatch.setattr(torch.cuda, "Stream", unexpected)
    monkeypatch.setattr("stpd.p1.local_inference.Listener", unexpected)
    with pytest.raises(ValueError, match="inference_stream_requires_CUDA"):
        SegmentInferencePool(model, {"snapshot_sha256": "a" * 64}, control=lambda _: {},
            gpu_lock=threading.RLock(), inference_stream=True)
    with pytest.raises(ValueError, match="flag_must_be_bool"):
        SegmentInferencePool(model, {"snapshot_sha256": "a" * 64}, control=lambda _: {},
            gpu_lock=threading.RLock(), inference_stream=1)


def test_default_cpu_tcp_scoring_keeps_original_lock_and_outputs(monkeypatch):
    model = member()
    lock = CountedLock()
    pool, identity = pool_for(model, lock, monkeypatch)
    client = SegmentConnection(pool.attachment())
    observation = public_state()
    frame = FrameCompiler(model.vocabulary).compile(observation)
    try:
        result = request(client, model, identity, observation)
        with torch.inference_mode():
            scores, logits, _ = model.forward_rl4([frame], [0])
        assert result["scores"] == scores[0].tolist()
        assert result["outcome_probabilities"] == logits.softmax(-1)[0].tolist()
        assert lock.entries == 1
        assert pool.attachment()["inference_stream"] == {
            "requested": False, "enabled": False, "device": "cpu", "priority": None}
    finally:
        client.close()
        summary = pool.close()
    assert summary["closed"] and summary["inference_stream"]["enabled"] is False


def test_two_real_cache_writers_keep_first_immutable_publication_and_old_guards():
    cache = TextVectorCache(4096, stream_safe=True)
    barrier = threading.Barrier(2)

    def writer():
        with torch.inference_mode():
            assert cache.get("mu", b"same-text") is None
            barrier.wait(timeout=5)
            cache.put("mu", (b"same-text",), torch.ones(1, 192))
            return cache.get("mu", b"same-text").clone()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: writer(), range(2)))
    assert all(torch.equal(value, torch.ones(192)) for value in results)
    assert cache.report()["blocks"] == cache.report()["rows"] == 1
    assert cache.report()["accounted_bytes"] <= 4096
    with torch.inference_mode():
        with pytest.raises(ValueError, match="duplicate_miss"):
            cache.put("mu", (b"duplicate", b"duplicate"), torch.ones(2, 192))
        with pytest.raises(ValueError, match="immutable_no_grad"):
            cache.put("mu", (b"wrong-length",), torch.ones(2, 192))
        original = TextVectorCache(4096)
        original.put("mu", (b"original",), torch.ones(1, 192))
        with pytest.raises(ValueError, match="duplicate_miss"):
            original.put("mu", (b"original",), torch.ones(1, 192))
    cache.retire(set())
    assert cache.report()["rows"] == cache.report()["accounted_bytes"] == 0


@CUDA
def test_real_cuda_publication_is_ready_and_scoring_uses_only_owned_stream(monkeypatch):
    model = member()
    publication = torch.cuda.Stream()
    with torch.cuda.stream(publication):
        model.to("cuda", non_blocking=True)
        model.ids.weight.add_(.125)
        pool, identity = pool_for(model, NoTrainingLock(), monkeypatch, inference_stream=True)
    client = SegmentConnection(pool.attachment())
    observed = []
    hook = model.ids.register_forward_pre_hook(
        lambda *args: observed.append(torch.cuda.current_stream(model.ids.weight.device)))
    observation = public_state()
    try:
        result = request(client, model, identity, observation)
        assert observed and all(stream == pool.cuda_stream for stream in observed)
        assert pool.attachment()["inference_stream"]["requested"] is True
        assert pool.attachment()["inference_stream"]["enabled"] is True
        assert pool.cuda_stream != torch.cuda.default_stream(model.ids.weight.device)
        assert pool.cuda_stream.priority <= 0
        hook.remove()
        # A separate default-stream consumer also borrows the side-stream text
        # cache, exercising both event direction and record_stream on its blocks.
        frame = FrameCompiler(model.vocabulary).compile(observation)
        with torch.inference_mode():
            scores, logits, _ = model.forward_rl4([frame], [0])
        torch.testing.assert_close(torch.tensor(result["scores"]), scores[0].cpu(),
                                   atol=1e-5, rtol=1e-5)
        torch.testing.assert_close(torch.tensor(result["outcome_probabilities"]),
                                   logits.softmax(-1)[0].cpu(), atol=1e-6, rtol=1e-5)
        assert pool.text_cache.report()["hits"] > 0
        replacement = copy.deepcopy(member()).to("cuda")
        replacement_identity = {"snapshot_sha256": "b" * 64}
        pool.publish(replacement, replacement_identity)
        pool.retire(set())
        assert set(pool.models) == set(pool.model_ready) == {"b" * 64}
        assert len(request(client, replacement, replacement_identity, observation)["scores"]) == 2
    finally:
        hook.remove()
        client.close()
        summary = pool.close()
    assert summary["closed"] and pool.cuda_stream.query()


@CUDA
def test_real_cuda_cached_storage_survives_cross_stream_borrow_and_eviction():
    cache = TextVectorCache(4096, stream_safe=True)
    producer, consumer = torch.cuda.Stream(), torch.cuda.Stream()
    with torch.cuda.stream(producer), torch.inference_mode():
        values = torch.full((1, 192), 3., device="cuda")
        cache.put("mu", (b"key",), values)
    with torch.cuda.stream(consumer), torch.inference_mode():
        borrowed = cache.get("mu", b"key")
        total = borrowed.sum()
    cache.retire(set())
    del borrowed, values
    gc.collect()
    # Allocate on the original producer after the cache's last strong reference
    # disappears. Allocator reuse must honor the recorded consuming stream.
    with torch.cuda.stream(producer):
        scratch = [torch.empty((1, 192), device="cuda") for _ in range(16)]
        for tensor in scratch:
            tensor.zero_()
    consumer.synchronize()
    assert total.item() == 3. * 192
    assert cache.report()["rows"] == 0


@CUDA
def test_real_cuda_exception_drains_only_inference_before_retire_and_next_request(monkeypatch):
    model = member().to("cuda")
    pool, identity = pool_for(model, NoTrainingLock(), monkeypatch, inference_stream=True)
    client = SegmentConnection(pool.attachment())
    forward = model.forward_rl4
    fail = True

    def interrupted(*args, **kwargs):
        nonlocal fail
        if fail:
            fail = False
            pending = torch.ones((1, 192), device=model.ids.weight.device)
            pending.mul_(3)
            pool.text_cache.put(identity["snapshot_sha256"], (b"failure-storage",), pending)
            raise RuntimeError("injected_after_real_CUDA_enqueue")
        return forward(*args, **kwargs)

    monkeypatch.setattr(model, "forward_rl4", interrupted)
    try:
        with pytest.raises(ValueError, match="RuntimeError"):
            request(client, model, identity, public_state())
        assert pool.cuda_stream.query()
        assert len(request(client, model, identity, public_state())["scores"]) == 2
        replacement = member().to("cuda")
        pool.publish(replacement, {"snapshot_sha256": "b" * 64})
        pool.retire(set())
        assert identity["snapshot_sha256"] not in pool.models
        assert pool.text_cache.get(identity["snapshot_sha256"], b"failure-storage") is None
    finally:
        client.close()
        summary = pool.close()
    assert summary["errors"] == 1 and summary["closed"]
