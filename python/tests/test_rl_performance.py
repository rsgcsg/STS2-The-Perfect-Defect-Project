"""Opt-in execution must preserve update membership and reject bad scores before step."""
import copy
import json
from unittest.mock import Mock

import pytest
import torch

from spireagent.json_boundary import BoundaryError
from stpd.p1.execution import MStdExecution, microbatches
from stpd.p1.model import MStd
from stpd.p1.profiling import synthetic_frame
from stpd.p1.tensor_input import compile_frame
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl.learner import CONFIG_SCHEMA, LearnerConfig
from stpd.rl.loss import LossConfig, RlSample, rl_update
from stpd.rl.performance import (
    PerformanceConfig,
    cache_on_device,
    frame_storage_bytes,
    prepare_models,
)


def fixture():
    torch.set_num_threads(2)
    torch.manual_seed(3)
    model = MStd(public_vocabulary(), MStdExecution.for_plan("mstd-training-v1"))
    reference = copy.deepcopy(model).eval()
    frames = [synthetic_frame(2), synthetic_frame(3)]
    samples = [RlSample(frame, 0, 0.5, 0.5, 1.0 - i * 2, 0, f"s{i}")
               for i, frame in enumerate(frames)]
    return model, reference, samples, [(frames[0], 1)]


def test_config_omitted_is_legacy_and_explicit_fields_are_validated(tmp_path):
    path = tmp_path / "config.json"
    value = {"schema": CONFIG_SCHEMA, "run": "test", "peer": {}}
    path.write_text(json.dumps(value))
    assert LearnerConfig.read(path).performance is None
    path.write_text(json.dumps({**value, "performance": {"packed": True,
                                                       "tensor_cache": True}}))
    assert LearnerConfig.read(path).performance == PerformanceConfig(packed=True, tensor_cache=True)
    for fields in ({"tensor_cache": True}, {"precision": "fp16"}, {"microbatch_size": 0},
                   {"packed": 1}, {"scene_attention_area": False}, {"device_cache": True}):
        with pytest.raises(BoundaryError):
            PerformanceConfig(**fields)


@pytest.mark.parametrize("device_cache", [False, True])
def test_tensor_and_deferred_update_match_same_packed_fp32_update(device_cache):
    model, reference, samples, anchor = fixture()
    other = copy.deepcopy(model)
    initial = model.execution
    states = []
    cache = {}
    results = []
    for member, performance in (
            (model, None),
            (other, PerformanceConfig(packed=True, tensor_cache=True, defer_sync=True,
                                      device_cache=device_cache))):
        torch.manual_seed(17)
        results.append(rl_update(member, reference, torch.optim.SGD(member.parameters(), lr=0.01),
                                 samples, anchor, 0.01, LossConfig(), {},
                                 performance=performance, frame_cache=cache))
        states.append(torch.get_rng_state())
    assert model.execution == other.execution == reference.execution == initial
    assert torch.equal(*states)
    for name in ("policy_loss", "kl", "anchor_loss", "gradient_norm", "mean_ratio"):
        assert results[0][name] == pytest.approx(results[1][name], abs=1e-6)
    for name, value in model.state_dict().items():
        torch.testing.assert_close(value, other.state_dict()[name], rtol=0, atol=1e-7)
    assert len(cache) == 3


def test_device_cache_moves_all_metadata_without_changing_shapes_or_source():
    model, _, samples, _ = fixture()
    source = compile_frame(samples[0].frame, model)
    moved = cache_on_device(source, torch.device("meta"))
    assert moved.choices == source.choices and moved.entity_count == source.entity_count
    assert frame_storage_bytes(source) == frame_storage_bytes(moved) > 0
    for key, value in moved.tensors.items():
        assert value.device.type == "meta"
        assert value.shape == source.tensors[key].shape
        assert value.dtype == source.tensors[key].dtype
        assert source.tensors[key].device.type == "cpu"
    for key, value in moved.reductions.items():
        assert value.inverse.device.type == "meta"
        assert value.inverse.shape == source.reductions[key].inverse.shape
        for bucket, original in zip(value.buckets, source.reductions[key].buckets, strict=True):
            assert bucket[:2] == original[:2]
            assert bucket[2].device.type == "meta"
            assert bucket[2].shape == original[2].shape


@pytest.mark.parametrize("size", [16, 32, 64])
def test_wider_microbatches_preserve_membership_tail_complete_catalog_and_actor_execution(size):
    model, reference, samples, _ = fixture()
    frames = [samples[0].frame] * 65
    initial = model.execution
    performance = PerformanceConfig(microbatch_size=size, scene_attention_area=2**28,
                                    text_attention_area=2**28)
    decoded = MStdExecution.decode(MStdExecution(microbatch_size=size).to_dict())
    assert decoded.microbatch_size == size
    model.eval()
    with prepare_models(model, reference, performance), torch.no_grad():
        groups = list(microbatches(frames, list(range(len(frames))), model))
        assert [i for group in groups for i in group] == list(range(65))
        assert [len(group) for group in groups] == [size] * (65 // size) + [65 % size]
        for group in groups:
            scores = model.forward_batch([frames[i] for i in group])
            assert len(scores) == len(group)
            assert all(row.shape == (len(frames[i].choices),) and torch.isfinite(row).all()
                       for i, row in zip(group, scores, strict=True))
    assert model.execution == reference.execution == initial
    assert MStdExecution().microbatch_size == PerformanceConfig().microbatch_size == 8


@pytest.mark.parametrize("limited_area", ["scene", "text"])
def test_wider_microbatch_still_obeys_attention_area_and_keeps_oversized_scene(limited_area):
    model, reference, samples, _ = fixture()
    small = samples[0].frame
    large = synthetic_frame(25)
    compiled = compile_frame(small, model)
    frames = [small, small, small, large, small, small, small]
    with prepare_models(model, reference, PerformanceConfig(
            microbatch_size=64,
            scene_attention_area=2 * len(small.entities)**2 if limited_area == "scene" else 2**28,
            text_attention_area=(2 * compiled.text_count * compiled.text_width**2
                                 if limited_area == "text" else 2**28))):
        assert list(microbatches(frames, list(range(7)), model)) == [[0, 1], [2], [3], [4, 5], [6]]
    assert frames[3] is large
    for invalid in (True, 0, 65):
        with pytest.raises(BoundaryError):
            MStdExecution(microbatch_size=invalid)
        with pytest.raises(BoundaryError):
            PerformanceConfig(microbatch_size=invalid)


def test_bucket_preserves_samples_and_anchor_counts():
    model, reference, samples, anchor = fixture()
    result = rl_update(model, reference, torch.optim.SGD(model.parameters(), lr=0),
                       samples, anchor, 0, LossConfig(), {}, performance=PerformanceConfig(
                           packed=True, tensor_cache=True, bucket=True, microbatch_size=1))
    assert result["rl_samples"] == len(samples)
    assert result["anchor_samples"] == len(anchor)


def test_deferred_reference_nonfinite_cannot_be_masked_into_valid_update():
    model, reference, samples, anchor = fixture()
    reference.forward_batch = lambda frames, **kwargs: [
        torch.full((len(frame.choices),), torch.nan) for frame in frames]
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    optimizer.step = Mock()
    initial = model.execution
    with pytest.raises(BoundaryError, match="non_finite_update"):
        rl_update(model, reference, optimizer, samples, anchor, 0.01, LossConfig(), {},
                  performance=PerformanceConfig(defer_sync=True, microbatch_size=1))
    optimizer.step.assert_not_called()
    assert model.execution == reference.execution == initial


def test_resident_reference_cold_and_warm_match_cpu_cache_without_recomputing():
    left, reference, samples, anchor = fixture()
    right = copy.deepcopy(left)
    reference.forward_batch = Mock(wraps=reference.forward_batch)
    histories = []
    for member, resident in ((left, False), (right, True)):
        cache = {}
        optimizer = torch.optim.SGD(member.parameters(), lr=0.01)
        history = []
        calls_before = reference.forward_batch.call_count
        for step in range(2):
            torch.manual_seed(17 + step)
            history.append(rl_update(member, reference, optimizer, samples, anchor, 0.01,
                                     LossConfig(), cache, performance=PerformanceConfig(
                                         defer_sync=True, reference_device_cache=resident)))
            assert reference.forward_batch.call_count == calls_before + 1
            assert set(cache) == {sample.key for sample in samples}
            assert all(row.dtype == torch.float32 and not row.requires_grad
                       and row.device == member.ids.weight.device for row in cache.values())
        histories.append(history)
    for first, second in zip(*histories, strict=True):
        for name in ("policy_loss", "kl", "anchor_loss", "gradient_norm", "mean_ratio"):
            assert first[name] == second[name]
    for name, value in left.state_dict().items():
        assert torch.equal(value, right.state_dict()[name])


@pytest.mark.parametrize("resident", [False, True])
def test_corrupt_cached_reference_rejects_before_optimizer_step(resident):
    model, reference, samples, anchor = fixture()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    optimizer.step = Mock()
    cache = {samples[0].key: torch.full((len(samples[0].frame.choices),), torch.nan)}
    with pytest.raises(BoundaryError, match="non_finite_update"):
        rl_update(model, reference, optimizer, samples, anchor, 0.01, LossConfig(), cache,
                  performance=PerformanceConfig(defer_sync=True,
                                                reference_device_cache=resident))
    optimizer.step.assert_not_called()


def test_precision_has_no_cpu_fallback_and_context_restores_on_failure():
    model, reference, _, _ = fixture()
    initial = model.execution
    with (pytest.raises(BoundaryError, match="bf16_requires_supported_cuda"),
          prepare_models(model, reference, PerformanceConfig(precision="bf16"))):
        pytest.fail("unsupported precision ran")
    with (pytest.raises(BoundaryError, match="tf32_requires_cuda"),
          prepare_models(model, reference, PerformanceConfig(tf32=True))):
        pytest.fail("unsupported precision ran")
    with (pytest.raises(RuntimeError, match="intentional"),
          prepare_models(model, reference, PerformanceConfig(microbatch_size=1))):
        assert model.execution.microbatch_size == 1
        raise RuntimeError("intentional")
    assert model.execution == reference.execution == initial


def test_real_learner_update_exports_original_actor_execution_with_explicit_training_identity(
        tmp_path, monkeypatch):
    from dataclasses import asdict

    from spireagent.artifact_contracts import Producer
    from spireagent.workbench.rl_learner import IO
    from stpd.p1.export import export_model, load_model
    from stpd.rl import learner as module
    from stpd.rl.learner import Buffered, Learner

    model, _, samples, anchor = fixture()
    original_execution = model.execution
    checkpoint = tmp_path / "start-checkpoint"
    checkpoint.write_bytes(b"diagnostic")
    package = tmp_path / "package"
    export_model(model, checkpoint, package, {"scope": "diagnostic"})
    monkeypatch.setattr(module, "source_identity", lambda _: Producer("test", "a"*40, "b"*64))
    performance = PerformanceConfig(packed=True, tensor_cache=True, joint_nodes=True,
                                     microbatch_size=16, defer_sync=True)
    config = LearnerConfig(run="performance", peer={"kind": "local",
        "directory": str(tmp_path / "peer")}, start_package=str(package),
        tensorboard_steps=0, batch_size=2, min_episodes_before_training=1,
        performance=performance, decision_ledger=False)
    learner = Learner(config, tmp_path / "work", IO)
    learner.anchor_future = None
    learner.anchor = anchor
    learner.buffer = [Buffered(sample) for sample in samples]
    learner.counts["episodes_loss"] = 1
    result = learner.train_once()
    assert result["update"] == 1 and result["rl_samples"] == 2
    assert all(item.uses == 1 for item in learner.buffer)
    assert learner.model.execution == learner.reference.execution == original_execution
    learner.maybe_version(force=True)
    learner._collect_version(wait=True)
    destination = learner.work / "versions" / "000001"
    descriptor = json.loads((destination / "model.json").read_bytes())
    assert descriptor["training_identity"]["learner_execution"] == asdict(performance)
    exported, _ = load_model(destination / "model.tensor-tree", descriptor["package_sha256"])
    assert exported.execution == original_execution
    learner.exchange.request_stop("diagnostic_complete")
    assert learner.run()["updates"] == 1



def test_full_FP32_PPO_and_critic_preserve_trace_order_and_two_updates():
    from stpd.rl.critic import CriticConfig, CriticEpisode, OnlineCritic, trace_gae
    from stpd.rl.performance import grouped_indices

    torch.set_num_threads(2)
    torch.manual_seed(71)
    model = MStd(public_vocabulary(), MStdExecution.for_plan("mstd-training-v1"),
                 value_heads=True).eval()
    other, reference = copy.deepcopy(model), copy.deepcopy(model)
    frames = [synthetic_frame(size) for size in (4, 2, 3, 2, 5)]
    old = PerformanceConfig(packed=True, tensor_cache=True, precompile_inputs=True,
                            microbatch_size=1, defer_sync=True)
    full = PerformanceConfig(packed=True, tensor_cache=True, precompile_inputs=True,
        joint_nodes=True, scene_attention_area=67108864, text_attention_area=67108864,
        bucket=True, reference_device_cache=True, async_transfer=True, defer_sync=True)
    optimizers = [torch.optim.SGD(m.parameters(), lr=.001) for m in (model, other)]
    traces, episodes = [], []
    for member, optimizer, execution in zip((model, other), optimizers, (old, full), strict=True):
        critic = OnlineCritic(member, reference, optimizer, CriticConfig(), 7, execution)
        critic.freeze_policy(False)
        e = CriticEpisode("trace", "shard", "unit-seed", .7, 0, frames.copy(),
                          [0] * 5, [.5] * 5, [[.5, .5]] * 5,
                          [f"trace:{i}" for i in range(5)], 1)
        values, ratios, kl = critic.evaluate(e)
        traces.append((values, ratios, kl, trace_gae(.7, values, ratios, .999, 1)))
        assert critic.values(e) == pytest.approx(values, abs=2e-5, rel=3e-4)
        assert sum(critic.last_evaluation_microbatches) == 5
        episodes.append(e)
    for left, right in zip(traces[0][:3], traces[1][:3], strict=True):
        assert left == pytest.approx(right, abs=2e-5, rel=3e-4)
    for left, right in zip(traces[0][3], traces[1][3], strict=True):
        assert left == pytest.approx(right, abs=2e-5, rel=3e-4)
    with prepare_models(other, reference, full):
        order = [i for group in grouped_indices(episodes[1].frames, other, full) for i in group]
    assert order != list(range(5)) and sorted(order) == list(range(5))
    config = LossConfig(mode="ppo", value_coef=.001, entropy_coef=.01, kl_coef=.02,
                        gradient_diagnostics=True)
    samples = [RlSample(frame, 0, .5, .5, (.3 - i * .2), 0, f"trace:{i}",
                         value_target=.07 + i * .01, value_scale=.1 + i * .02)
               for i, frame in enumerate(frames)]
    histories = [[], []]
    for number, (member, optimizer, execution) in enumerate(zip(
            (model, other), optimizers, (old, full), strict=True)):
        cache, inputs = {}, {}
        ref = copy.deepcopy(reference)
        ref.forward_batch = Mock(wraps=ref.forward_batch)
        for step in range(2):
            result = rl_update(member, ref, optimizer, samples, [], .001, config, cache,
                               performance=execution, frame_cache=inputs)
            histories[number].append(result)
            assert set(cache) == {s.key for s in samples}
            assert all(value.device == member.ids.weight.device for value in cache.values())
            assert len(inputs) == 5
            if step:
                assert ref.forward_batch.call_count == calls
            calls = ref.forward_batch.call_count
    assert histories[0][0]["execution_microbatches"]["max"] == 1
    assert histories[1][0]["execution_microbatches"]["max"] == 5
    for left, right in zip(histories[0], histories[1], strict=True):
        for metric in ("policy_loss", "value_loss", "kl", "policy_entropy", "mean_ratio",
                       "gradient_norm"):
            assert left[metric] == pytest.approx(right[metric], abs=2e-5, rel=3e-4)
    for name, parameter in model.named_parameters():
        target = dict(other.named_parameters())[name]
        assert torch.allclose(parameter, target, atol=2e-5, rtol=3e-4), name
        assert (parameter.grad is None) == (target.grad is None), name
        if parameter.grad is not None:
            assert torch.allclose(parameter.grad, target.grad, atol=2e-6, rtol=3e-4), name


@pytest.mark.parametrize("bad", ["reference_cache", "scores", "values"])
def test_PPO_deferred_full_FP32_rejects_nonfinite_before_optimizer_step(bad):
    from dataclasses import replace

    from stpd.rl.critic import enable_value_head

    model, reference, samples, _ = fixture()
    enable_value_head(model, 7)
    samples = [replace(s, value_target=.1) for s in samples]
    before = copy.deepcopy(model.state_dict())
    optimizer = torch.optim.AdamW(model.parameters(), lr=.01)
    cache = {}
    if bad == "reference_cache":
        cache[samples[0].key] = torch.full((len(samples[0].frame.choices),), torch.nan)
    else:
        original = model.forward_with_values

        def corrupt(frames, **kwargs):
            scores, values = original(frames, **kwargs)
            return ([score * torch.nan for score in scores] if bad == "scores" else scores,
                    values * torch.nan if bad == "values" else values)

        model.forward_with_values = corrupt
    with pytest.raises(BoundaryError):
        rl_update(model, reference, optimizer, samples, [], .01,
            LossConfig(mode="ppo", value_coef=.001), cache,
            performance=PerformanceConfig(packed=True, tensor_cache=True, joint_nodes=True,
                bucket=True, reference_device_cache=True, async_transfer=True, defer_sync=True,
                scene_attention_area=67108864, text_attention_area=67108864))
    assert not optimizer.state
    assert all(torch.equal(before[name], value) for name, value in model.state_dict().items())
