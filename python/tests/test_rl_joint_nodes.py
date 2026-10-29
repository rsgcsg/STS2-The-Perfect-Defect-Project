"""Joint node graph is disjoint; eval math/gradients and empty branches survive."""
import copy

import pytest
import torch

from spireagent.json_boundary import BoundaryError
from stpd.p1.execution import MStdExecution
from stpd.p1.model import MStd
from stpd.p1.profiling import synthetic_frame
from stpd.p1.tensor_input import TensorFrame, compile_frame, joint_nodes
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl.performance import PerformanceConfig, cache_on_device, prepare_models


def inputs(empty):
    frames = [synthetic_frame(2), synthetic_frame(3), synthetic_frame(1)]
    frames[0].entities[0].numeric += [("missing", None), ("zero", 0.), ("negative", -3.)]
    frames[0].entities[0].refs += [1, 1, 2]
    frames[0].roles[0] = ((0, 1), -1)
    for frame in frames[1:] if not empty else frames:
        for entity in frame.entities:
            entity.text.clear()
            entity.numeric.clear()
            entity.identities.clear()
    return frames


@pytest.mark.parametrize("empty", [False, True])
def test_joint_matches_logits_values_gradients_and_none_for_absent_branches(empty):
    torch.set_num_threads(2)
    torch.manual_seed(3)
    old = MStd(public_vocabulary(), MStdExecution.for_plan("mstd-training-v1"),
               value_heads=True).eval()
    joint = copy.deepcopy(old)
    joint.execution = MStdExecution(implementation="experimental-joint-nodes-v1")
    frames = inputs(empty)
    packed = [compile_frame(frame, old) for frame in frames]
    results = []
    for model in (old, joint):
        scores, values = model.forward_with_values(packed)
        (sum(row.square().sum() for row in scores) + values.square().sum()).backward()
        results.append((scores, values))
    for first, second in zip(results[0][0], results[1][0], strict=True):
        torch.testing.assert_close(first, second, rtol=4e-4, atol=5e-6)
    torch.testing.assert_close(results[0][1], results[1][1], rtol=4e-4, atol=5e-6)
    for (name, left), (_, right) in zip(old.named_parameters(), joint.named_parameters(),
                                       strict=True):
        assert (left.grad is None) == (right.grad is None), name
        if left.grad is not None:
            torch.testing.assert_close(left.grad, right.grad, rtol=2e-3, atol=3e-5, msg=name)
    if empty:
        assert old.text_ids.weight.grad is None
        assert joint.text_ids.weight.grad is None
        assert old.number_slope.weight.grad is None
        assert joint.number_slope.weight.grad is None


def test_device_cache_keeps_cpu_metadata_for_joint_without_cuda_readback():
    model = MStd(public_vocabulary())
    packed = [compile_frame(frame, model) for frame in inputs(False)]
    resident = [cache_on_device(frame, torch.device("meta")) for frame in packed]
    assert all(frame.host is original for frame, original in zip(resident, packed, strict=True))
    assert all(TensorFrame.restore(frame.state()).host is None for frame in resident)
    raw = joint_nodes(packed)
    other = joint_nodes(resident)
    for key, value in raw.tensors.items():
        assert torch.equal(value, other.tensors[key])
    for key, value in raw.reductions.items():
        assert torch.equal(value.inverse, other.reductions[key].inverse)
        for before, after in zip(value.buckets, other.reductions[key].buckets, strict=True):
            assert before[:2] == after[:2]
            assert torch.equal(before[2], after[2])


def test_joint_config_is_opt_in_scoped_and_raw_frames_keep_packed_fallback():
    with pytest.raises(BoundaryError, match="joint_nodes_requires_tensor_cache"):
        PerformanceConfig(joint_nodes=True)
    model = MStd(public_vocabulary(), MStdExecution.for_plan("mstd-training-v1")).eval()
    reference = copy.deepcopy(model)
    initial = model.execution
    frame = inputs(False)[0]
    with torch.no_grad():
        expected = model(frame)
        with prepare_models(model, reference, PerformanceConfig(
                packed=True, tensor_cache=True, joint_nodes=True)):
            actual = model(frame)
        assert torch.equal(expected, actual)
    assert model.execution == reference.execution == initial
    assert not PerformanceConfig().joint_nodes
