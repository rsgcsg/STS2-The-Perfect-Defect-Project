"""New wide policy reuses packed kernels; old policies and sample order stay fixed."""
from __future__ import annotations

import pytest

from spireagent.json_boundary import BoundaryError
from stpd.p1.execution import MStdExecution, microbatches
from stpd.p1.execution_plans import training_execution
from stpd.p1.input import fit_vocabulary
from stpd.p1.model import MStd
from stpd.p1.profiling import synthetic_frame


def test_wide_new_run_fills_microbatches_without_altering_old_plan_or_true_tail():
    frame = synthetic_frame(652)
    vocabulary = fit_vocabulary([frame])
    old = MStdExecution.for_plan("mstd-training-v1")
    frozen = old.to_dict()
    wide = training_execution("mstd-training-v2")
    frames = [frame] * 65
    model = MStd(vocabulary, old)
    before = list(microbatches(frames, list(range(65)), model))
    model.execution = wide
    after = list(microbatches(frames, list(range(65)), model))
    assert all(len(group) == 1 for group in before)
    assert [len(group) for group in after] == [8] * 8 + [1]
    assert [index for group in after for index in group] == list(range(65))
    assert old.to_dict() == frozen
    assert training_execution("mstd-training-v1") == old
    assert wide.implementation == old.implementation
    assert MStdExecution.decode(wide.to_dict()) == wide
    assert MStdExecution.for_plan("mstd-training-v1").scene_attention_area == 262144


@pytest.mark.parametrize("size", [1, 2, 4, 8])
def test_benchmark_variants_are_explicit_frozen_policies(size):
    policy = training_execution("mstd-training-v2", microbatch_size=size)
    assert policy.microbatch_size == size
    assert policy.scene_attention_area == policy.text_attention_area == 2**26


@pytest.mark.parametrize("size", [True, 0, 3, 16])
def test_unsupported_override_does_not_silently_change_plan(size):
    with pytest.raises(BoundaryError):
        training_execution("mstd-training-v2", microbatch_size=size)
    with pytest.raises(BoundaryError):
        training_execution("mstd-training-v1", microbatch_size=size)
