"""Opt-in execution must preserve update membership and reject bad scores before step."""
import copy
import json
from unittest.mock import Mock

import pytest
import torch

from spireagent.json_boundary import BoundaryError
from stpd.p1.execution import MStdExecution
from stpd.p1.model import MStd
from stpd.p1.profiling import synthetic_frame
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl.learner import CONFIG_SCHEMA, LearnerConfig
from stpd.rl.loss import LossConfig, RlSample, rl_update
from stpd.rl.performance import PerformanceConfig, prepare_models


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
                   {"packed": 1}, {"scene_attention_area": False}):
        with pytest.raises(BoundaryError):
            PerformanceConfig(**fields)


def test_tensor_and_deferred_update_match_same_packed_fp32_update_and_restore_execution():
    model, reference, samples, anchor = fixture()
    other = copy.deepcopy(model)
    initial = model.execution
    states = []
    cache = {}
    results = []
    for member, performance in (
            (model, None),
            (other, PerformanceConfig(packed=True, tensor_cache=True, defer_sync=True))):
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
