"""V3 子: the RL5 long-run training configuration (rl5 profile, row buckets, compiled
SGD defaults, compile warm-up cover). CPU only; GPU numbers are Friday facts."""
from __future__ import annotations

import dataclasses as dc
import json

import pytest
import test_rl41_w1 as w1
import torch
from test_rl41_w1 import model as model  # noqa: F401  (fixture)
from test_rl_rows_forward import forward_and_gradients
from test_rl_rows_forward import varied as varied  # noqa: F401  (fixture)

from spireagent.json_boundary import BoundaryError
from stpd.rl import offline_step
from stpd.rl.batch_preparation import ROW_BUCKET_ROWS, BatchPreparer, assemble, row_layout
from stpd.rl.learner import LearnerConfig
from stpd.rl.performance import FORWARD_IMPLEMENTATION_DEFAULTS, PerformanceConfig
from stpd.rl.segment_learner import _resume_config
from stpd.rl.sgd_process import (
    COMPILED_RECOMPILE_LIMIT,
    COMPILED_STALL_SECONDS,
    compiled_defaults,
    sgd_options,
)


def batch(model, **options):
    values = [w1.segment(model, name=f"actor/{i}", run_id=f"episode-{i}", rows=5)
              for i in range(2)]
    return assemble(w1.request(values), tuple(w1.source(model, v) for v in values),
                    microbatch_size=8, deduplicate_text=True, **options)


def test_bucket_rows_default_is_the_old_constant_and_needs_rows_v2():
    assert PerformanceConfig().prepared_bucket_rows == ROW_BUCKET_ROWS == 256
    assert FORWARD_IMPLEMENTATION_DEFAULTS["prepared_bucket_rows"] == ROW_BUCKET_ROWS
    PerformanceConfig(prepared_forward="rows-v2", prepared_bucket_rows=100000)
    with pytest.raises(BoundaryError, match="prepared_bucket_rows_requires_rows_v2"):
        PerformanceConfig(prepared_bucket_rows=100000)
    for bad in (0, -1, 2**21, 1.5, True):
        with pytest.raises(BoundaryError, match="invalid_prepared_bucket_rows"):
            PerformanceConfig(prepared_forward="rows-v2", prepared_bucket_rows=bad)


def test_bucket_rows_is_exported_only_when_selected_and_never_a_resume_field():
    from stpd.rl.segments import SegmentConfig

    rows = PerformanceConfig(prepared_forward="rows-v2")
    base = LearnerConfig(run="u", peer={"kind": "local", "directory": "/nonexistent"},
                         performance=rows, segments=SegmentConfig())
    one = dc.replace(base, performance=dc.replace(rows, prepared_bucket_rows=100000))
    assert "prepared_bucket_rows" not in base.frozen()["performance"]
    assert one.frozen()["performance"]["prepared_bucket_rows"] == 100000
    assert _resume_config(dc.asdict(base)) == _resume_config(dc.asdict(one))


def test_preparers_pass_bucket_rows_only_when_it_is_not_the_default(tmp_path):
    default = BatchPreparer({}, workers=1, threads=1, peer=None, run="u", scratch=tmp_path,
                            prepared_layout="rows-v2")
    one = BatchPreparer({}, workers=1, threads=1, peer=None, run="u", scratch=tmp_path,
                        prepared_layout="rows-v2", prepared_bucket_rows=100000)
    try:
        assert "prepared_bucket_rows" not in default.options
        assert one.options["prepared_bucket_rows"] == 100000
    finally:
        default.pool.shutdown()
        one.pool.shutdown()


def test_bucket_rows_changes_only_the_partition_not_the_function(model, varied):
    one = batch(model, prepared_layout="rows-v2", prepared_bucket_rows=100000)
    groups = [g for m in one.minibatches for g in m.groups]
    assert all(len(g.layout.scene_buckets) == 1 for g in groups)
    for group in groups:
        # The assembled layout is exactly `row_layout` at the selected partition.
        again = row_layout(group, bucket_rows=100000)
        assert again.scene_buckets == group.layout.scene_buckets
        assert torch.equal(again.scene_nodes, group.layout.scene_nodes)
        small = dc.replace(group, layout=row_layout(group, bucket_rows=2))
        assert len(small.layout.scene_buckets) > 1 or len(group.rows) <= 2
        (sa, la, ha), ga = forward_and_gradients(model, small, packed=False)
        (sb, lb, hb), gb = forward_and_gradients(model, group, packed=False)
        for x, y in ((sa, sb), (la, lb), (ha, hb)):
            torch.testing.assert_close(x, y, rtol=1e-4, atol=1e-5)
        for name in ga:
            torch.testing.assert_close(ga[name], gb[name], rtol=1e-3, atol=1e-5)


def test_assemble_refuses_bucket_rows_without_rows_v2(model):
    with pytest.raises(ValueError, match="invalid_prepared_bucket_rows"):
        batch(model, prepared_bucket_rows=100000)


def test_compiled_defaults_raise_the_limit_and_stall_floor_but_explicit_values_win():
    plain = sgd_options({"sgd_process": True})
    assert plain.compile is None and plain.stall_seconds == 120
    selected = compiled_defaults(plain, {"sgd_process": True})
    assert selected.compile == {"cache_dir": None, "warmup": [], "size_guard": False,
                                "recompile_limit": COMPILED_RECOMPILE_LIMIT}
    assert selected.stall_seconds == COMPILED_STALL_SECONDS == 300
    dataplane = {"sgd_process": True, "sgd_stall_seconds": 90,
                 "sgd_compile": {"recompile_limit": 24, "cache_dir": "/c", "warmup": ["/w"]}}
    explicit = compiled_defaults(sgd_options(dataplane), dataplane)
    assert explicit.stall_seconds == 90
    assert explicit.compile == {"cache_dir": "/c", "recompile_limit": 24, "warmup": ["/w"],
                                "size_guard": False}
    guarded = {"sgd_process": True, "sgd_compile": {"size_guard": True}}
    assert compiled_defaults(sgd_options(guarded), guarded).compile["size_guard"] is True
    higher = {"sgd_process": True, "sgd_compile": {}}
    assert compiled_defaults(sgd_options(higher), higher).compile["recompile_limit"] == 16


def test_rl5_profile_selects_the_measured_learner_levers(tmp_path):
    path = tmp_path / "learner.json"
    path.write_text(json.dumps({"schema": "stpd/rl-learner-config-1", "profile": "rl5",
                                "run": "u", "peer": {"kind": "local", "directory": "/x"},
                                "segments": {"dataset_rows": 4096}}))
    config = LearnerConfig.read(path)
    performance = config.performance
    assert (performance.prepared_forward, performance.prepared_compile) == ("rows-v2", True)
    assert performance.prepared_kernels == "cross+embed+heads+nodes"
    assert performance.prepared_bucket_rows >= performance.microbatch_size  # one bucket
    assert performance.precision == "bf16" and performance.tf32
    assert config.segments.steps == 64  # fast_chain needs steps == fast_segment.MAX_ROWS
    path.write_text(json.dumps({"schema": "stpd/rl-learner-config-1", "profile": "rl6",
                                "run": "u", "peer": {}}))
    with pytest.raises(BoundaryError, match="unsupported_profile"):
        LearnerConfig.read(path)


def test_warmup_cover_reports_classes_and_leaves_none_hidden(tmp_path, model, varied):
    minibatches = batch(model, prepared_layout="rows-v2").minibatches
    paths = []
    for index, minibatch in enumerate(minibatches):
        offline_step.write(tmp_path / f"d{index}", {"schema": offline_step.SCHEMA,
                           "minibatch": offline_step.cpu_tree(minibatch)}, {"online_update": 1})
        paths.append(tmp_path / f"d{index}")
    signatures = [offline_step.warmup_signatures(m, bucket_rows=100000) for m in minibatches]
    assert all(("nodes", "small") in s for s in signatures)
    assert all(sig[0] == "block" and sig[1] in {"fold", "wide", "value"}
               for s in signatures for sig in s if sig[0] == "block")
    every = set().union(*signatures)
    full = offline_step.warmup_cover(paths, bucket_rows=100000)
    assert full["classes"] == full["covered"] == len(every) and full["uncovered"] == []
    assert 1 <= len(full["warmup"]) <= len(paths)
    one = offline_step.warmup_cover(paths, bucket_rows=100000, limit=1)
    assert len(one["warmup"]) == 1
    assert one["covered"] + len(one["uncovered"]) == one["classes"]
    with pytest.raises(ValueError, match="invalid_warmup_limit"):
        offline_step.warmup_cover(paths, bucket_rows=100000, limit=9)
