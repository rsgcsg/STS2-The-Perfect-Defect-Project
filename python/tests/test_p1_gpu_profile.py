"""The engineering GPU observer admits synthetic inputs before any CUDA work."""
from dataclasses import replace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_p1_snapshot import explicit_snapshot, projection_fixture, store_at

from spireagent.json_boundary import BoundaryError, FrozenObject
from stpd.cloud_jobs.contracts import ComputeRequest
from stpd.cloud_jobs.p1_gpu_profile import require_synthetic
from stpd.p1.spec import MStdConfig
from stpd.p1.workload import prepare_p1_run


def make_request(tmp_path, *, real_origin=False, device="cuda:0"):
    store = store_at(tmp_path / "store")
    rows = []
    for index in range(3):
        projection, original, _ = projection_fixture(store, "gpu-probe-" + str(index))
        if real_origin and index == 0:
            info = original.parameters.value()
            info["declared_origin"] = "native_human"
            original = replace(original, parameters=FrozenObject.of(info))
            store.publish(original)
            projection, _, _ = projection_fixture(store, "gpu-probe-changed", original=original)
        rows.append(projection)
    snapshot = explicit_snapshot(store, rows)
    run = prepare_p1_run(store, snapshot.artifact_id, PRODUCER,
                         MStdConfig(max_epochs=2, device=device), "a" * 32)
    return store, ComputeRequest("training", run.artifact_id, PRODUCER, "synthetic-probe")


def test_observer_requires_declared_synthetic_originals_and_explicit_cuda(tmp_path):
    store, request = make_request(tmp_path / "positive")
    assert require_synthetic(store, request).config.device == "cuda:0"
    store, request = make_request(tmp_path / "real-origin", real_origin=True)
    with pytest.raises(BoundaryError, match="synthetic_originals_required"):
        require_synthetic(store, request)
    store, request = make_request(tmp_path / "cpu", device="cpu")
    with pytest.raises(BoundaryError, match="explicit_cuda_zero_required"):
        require_synthetic(store, request)
