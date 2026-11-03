"""Normal Store/pack consumers reuse only verified immutable CPU input bytes."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from test_p1_frame_compiler import assert_frame_equal
from test_rl_compiled_segments import compiled_config
from test_rl_loop import PRODUCER
from test_rl_segment_learner import cleanup, configuration, deliver

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.managed_segment_store import read_segment
from stpd.rl.segment_inputs import SegmentInputs, publish_input_pack
from stpd.rl.segment_learner import SegmentLearner


@pytest.fixture(params=[False, True], ids=["old-pack", "actor-wire"])
def inputs(tmp_path, monkeypatch, request):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(compiled_config(config), tmp_path / "owner", io)
    try:
        records = []
        for actor in ("first", "second"):
            records += deliver(owner, tmp_path, actor, monkeypatch, compiled=request.param)
        pack = None
        if not request.param:
            pack = publish_input_pack(owner.exchange.store,
                [record["segment_artifact_id"] for record in records], owner.model.vocabulary,
                PRODUCER, read_segment=read_segment, scratch=tmp_path,
                checkpoint=config.segments.warmup_artifact_id).artifact_id

        def reader(**kwargs):
            return SegmentInputs(owner.exchange.store, owner.model.vocabulary,
                                 owner.input_reader.identity, pack, **kwargs)

        yield owner, records, reader
    finally:
        cleanup(owner)


def test_auxiliary_cold_then_hot_decodes_only_selected_row_and_keeps_outputs_private(
        inputs, tmp_path, monkeypatch):
    owner, records, make_reader = inputs
    reader = make_reader()
    artifact = records[0]["segment_artifact_id"]
    segment = read_segment(owner.exchange.store, artifact, scratch=tmp_path)
    body = reader.body(artifact, segment)
    expected = reader.frame(segment["steps"][1]["input"], body["current"][1])
    counts = {"source": 0, "body": 0, "decode": 0}
    original_body, original_decode = reader.body, reader.compiler.decode

    def counted_read(*args, **kwargs):
        counts["source"] += 1
        return read_segment(*args, **kwargs)

    def counted_body(*args):
        counts["body"] += 1
        return original_body(*args)

    def counted_decode(*args, **kwargs):
        counts["decode"] += 1
        return original_decode(*args, **kwargs)

    monkeypatch.setattr(reader, "body", counted_body)
    monkeypatch.setattr(reader.compiler, "decode", counted_decode)
    first, floor = reader.auxiliary(artifact, 1, read_segment=counted_read, scratch=tmp_path)
    assert counts == {"source": 1, "body": 1, "decode": 1}
    assert floor == segment["steps"][1]["behavior"]["floor"]
    assert_frame_equal(expected, first)
    first.tensors["numbers"].fill_(999)
    second, second_floor = reader.auxiliary(artifact, 1, read_segment=counted_read,
                                          scratch=tmp_path)
    assert counts == {"source": 1, "body": 1, "decode": 2}
    assert_frame_equal(expected, second)
    assert floor == second_floor and first is not second
    # A normal dataset can use the already verified pack, including bootstrap.
    frames = reader.frames(artifact, segment)
    assert len(frames) == 3 and counts["body"] == 1


def test_byte_bound_lru_eviction_and_oversized_bypass_use_original_reader(inputs, tmp_path):
    owner, records, make_reader = inputs
    artifacts = [record["segment_artifact_id"] for record in records]
    sizes = []
    for artifact in artifacts:
        reader = make_reader()
        reader.auxiliary(artifact, 0, read_segment=read_segment, scratch=tmp_path)
        sizes.append(reader.cache_report()["accounted_bytes"])
    limit = max(sizes)
    reader = make_reader(cache_bytes=limit)
    reads = []

    def counted_read(store, artifact, **kwargs):
        reads.append(artifact)
        return read_segment(store, artifact, **kwargs)

    for artifact in [*artifacts, artifacts[0]]:
        reader.auxiliary(artifact, 0, read_segment=counted_read, scratch=tmp_path)
        assert 0 < reader.cache_report()["accounted_bytes"] <= limit
    assert reads == [*artifacts, artifacts[0]]
    assert reader.cache_report()["evictions"] == 2
    bypass = make_reader(cache_bytes=min(sizes) - 1)
    for _ in range(2):
        bypass.auxiliary(artifacts[0], 0, read_segment=counted_read, scratch=tmp_path)
    assert reads[-2:] == [artifacts[0], artifacts[0]]
    assert bypass.cache_report()["accounted_bytes"] == bypass.cache_report()["segments"] == 0


def test_main_auxiliary_and_prepare_reader_share_one_complete_cold_fill(inputs, tmp_path):
    owner, records, make_reader = inputs
    reader = make_reader()
    artifact = records[0]["segment_artifact_id"]
    segment = read_segment(owner.exchange.store, artifact, scratch=tmp_path)
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    reads = []

    def blocked_read(store, artifact, **kwargs):
        reads.append(artifact)
        entered.set()
        assert release.wait(10)
        return read_segment(store, artifact, **kwargs)

    def prepared():
        second_started.set()
        return reader.frames(artifact, segment)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(reader.auxiliary, artifact, 1,
                            read_segment=blocked_read, scratch=tmp_path)
        try:
            assert entered.wait(5)
            second = pool.submit(prepared)
            assert second_started.wait(5)
        finally:
            release.set()
        frame, _ = first.result(timeout=10)
        frames = second.result(timeout=10)
    assert reads == [artifact] and reader.cache_report()["misses"] == 1
    assert any(candidate.choices == frame.choices for candidate in frames.values())


def test_failed_full_validation_does_not_populate_cache(inputs, tmp_path, monkeypatch):
    owner, records, make_reader = inputs
    reader = make_reader()
    artifact = records[0]["segment_artifact_id"]
    actual_body = reader.body

    def failed(*args):
        raise ValueError("real verification failure")

    monkeypatch.setattr(reader, "body", failed)
    with pytest.raises(ValueError, match="real verification failure"):
        reader.auxiliary(artifact, 0, read_segment=read_segment, scratch=tmp_path)
    assert reader.cache_report()["segments"] == 0
    monkeypatch.setattr(reader, "body", actual_body)
    frame, _ = reader.auxiliary(artifact, 0, read_segment=read_segment, scratch=tmp_path)
    assert frame.choices and reader.cache_report()["misses"] == 2


def test_cached_decode_still_checks_choices_against_original_catalog(inputs, tmp_path, monkeypatch):
    _, records, make_reader = inputs
    reader = make_reader()
    artifact = records[0]["segment_artifact_id"]
    reader.auxiliary(artifact, 0, read_segment=read_segment, scratch=tmp_path)
    original = reader.compiler.decode

    def wrong_choices(*args, **kwargs):
        return replace(original(*args, **kwargs), choices=("wrong-catalog-choice",))

    monkeypatch.setattr(reader.compiler, "decode", wrong_choices)
    with pytest.raises(BoundaryError, match="catalog_choice_binding_mismatch"):
        reader.auxiliary(artifact, 0, read_segment=read_segment, scratch=tmp_path)
