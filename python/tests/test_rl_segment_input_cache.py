"""Normal Store/pack consumers reuse only verified immutable CPU input bytes."""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from test_p1_frame_compiler import assert_frame_equal
from test_rl41_w1 import model as model
from test_rl_loop import PRODUCER

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.managed_segment_store import read_segment
from stpd.rl import segment_inputs
from stpd.rl.segment_inputs import SegmentInputs, publish_input_pack


@pytest.fixture(params=[False, True], ids=["old-pack", "actor-wire"])
def inputs(tmp_path, model, request):
    # Explicit historical source fixture. The live learner never consumes this
    # artifact/input-pack route after RL4.1; its retained reader is tested here.
    import copy
    import gzip
    import hashlib
    from types import SimpleNamespace

    from test_rl41_w1 import segment

    from spireagent.artifact_contracts import Manifest, Parent
    from spireagent.json_boundary import FrozenObject, json_bytes
    from spireagent.rl_exchange import Exchange
    from spireagent.workbench.global_evidence_store import publish_detached_p1_model
    from stpd.p1.export import export_model
    from stpd.p1.frame_compiler import FrameCompiler
    from stpd.rl.segment_inputs import TRAJECTORY_SCHEMA, segment_body
    from stpd.workers.checkpoint_codec import encode_checkpoint

    exchange = Exchange({"kind": "local", "directory": str(tmp_path / "archive-peer")}, "archive")
    cp = tmp_path / "fixture-checkpoint"
    cp.write_bytes(b"historical representation fixture")
    package = tmp_path / "package"
    export_model(model, cp, package, {})
    policy = publish_detached_p1_model(exchange.store, package, PRODUCER)
    head_payload = exchange.store.put_bytes(
        "checkpoint", encode_checkpoint({"heads": model.rl4_heads.state_dict()})
    )
    head = Manifest(
        "checkpoint",
        PRODUCER,
        parents=(Parent("policy", policy.artifact_id),),
        payloads=(head_payload,),
    )
    exchange.store.publish(head)
    identity = FrameCompiler(model.vocabulary).identity
    mu = {
        "run": "archive",
        "training_generation": "old",
        "version": 0,
        "updates": 0,
        "snapshot_sha256": "a" * 64,
        "model_artifact_id": policy.artifact_id,
        "model_sha256": policy.payload("weights").sha256,
        "heads_artifact_id": head.artifact_id,
        "heads_sha256": head_payload.sha256,
    }
    records = []
    for actor in ("first", "second"):
        value = segment(model, rows=2, name=f"{actor}/0", run_id=actor, generation="old", mu=mu)
        value["schema"] = TRAJECTORY_SCHEMA if request.param else "stpd/p1-trajectory-segment-1"
        originals = b"historical original bytes fixture; no native qualification"
        value["original_commit"].update(
            archive_sha256=hashlib.sha256(originals).hexdigest(), archive_bytes=len(originals)
        )
        if not request.param:
            value.pop("compiler_identity")
            value.pop("compiled_boundary")
            for step in value["steps"]:
                step["behavior"].pop("compiled_input")
        # Manufacture only this immutable historical reader fixture. The old
        # live publication function has been deleted by W3, as required.
        payloads = [exchange.store.put_bytes("originals", originals)]
        trajectory = copy.deepcopy(value)
        if request.param:
            payloads.append(
                exchange.store.put_bytes(
                    "compiled_inputs",
                    gzip.compress(json_bytes(segment_body(value)), mtime=0),
                    "application/gzip",
                )
            )
            for step in trajectory["steps"]:
                step["behavior"].pop("compiled_input")
            trajectory.pop("compiled_boundary")
        raw = json_bytes(trajectory)
        payloads.append(
            exchange.store.put_bytes("trajectory", gzip.compress(raw, mtime=0), "application/gzip")
        )
        artifact = Manifest(
            "analysis",
            PRODUCER,
            parents=tuple(
                sorted((Parent("model", policy.artifact_id), Parent("heads", head.artifact_id)))
            ),
            payloads=tuple(sorted(payloads)),
            parameters=FrozenObject.of(
                {
                    **{key: value[key] for key in ("schema", "run_id", "segment_id", "rows", "mu")},
                    "start_step": 0,
                    "end_step": 1,
                    "decoded_bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            ),
        )
        exchange.store.publish(artifact)
        records.append({"segment_artifact_id": artifact.artifact_id})
    pack = None
    if not request.param:
        pack = publish_input_pack(
            exchange.store,
            [row["segment_artifact_id"] for row in records],
            model.vocabulary,
            PRODUCER,
            read_segment=read_segment,
            scratch=tmp_path,
            checkpoint=head.artifact_id,
        ).artifact_id

    def reader(**kwargs):
        return SegmentInputs(exchange.store, model.vocabulary, identity, pack, **kwargs)

    yield SimpleNamespace(exchange=exchange, model=model), records, reader


def test_auxiliary_cold_then_hot_decodes_only_selected_row_and_keeps_outputs_private(
    inputs, tmp_path, monkeypatch
):
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
    second, second_floor = reader.auxiliary(
        artifact, 1, read_segment=counted_read, scratch=tmp_path
    )
    assert counts == {"source": 1, "body": 1, "decode": 2}
    assert_frame_equal(expected, second)
    assert floor == second_floor and first is not second
    # Historical analysis can read this verified representation, including its boundary.
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

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(
            reader.auxiliary, artifact, 1, read_segment=blocked_read, scratch=tmp_path
        )
        try:
            assert entered.wait(5)
            second = pool.submit(prepared)
            assert second_started.wait(5)
            # A cold IO owner and its same-key waiter cannot hold the shared lock.
            assert pool.submit(reader.cache_report).result(timeout=2)["misses"] == 1
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


def test_container_high_water_capacity_is_counted_after_many_small_entries(
    inputs, tmp_path, monkeypatch
):
    owner, records, make_reader = inputs
    probe = make_reader()
    if probe.entries:
        pytest.skip("container allocation regression uses actor wire; old pack covered above")
    source = owner.exchange.store.get_manifest(records[0]["segment_artifact_id"])
    probe.auxiliary(source.artifact_id, 0, read_segment=read_segment, scratch=tmp_path)
    small = probe.cache_report()["accounted_bytes"]
    reader = make_reader(cache_bytes=small * 48)
    for index in range(40):
        # Distinct, normally verified manifests share an immutable fixture payload.
        manifest = replace(source, producer=replace(PRODUCER, source_revision=f"{index:040x}"))
        owner.exchange.store.publish(manifest)
        reader.auxiliary(manifest.artifact_id, 0, read_segment=read_segment, scratch=tmp_path)
    assert reader.cache_report()["segments"] == 40
    high_water = sys.getsizeof(reader._cache)
    original = segment_inputs._verified_row
    sample = next(iter(probe._cache.values()))
    row_count = len(sample.current) + int(sample.boundary is not None)
    padding = (reader._limit - small - high_water - 1024) // row_count

    def large_row(*args):
        # Valid JSON whitespace creates real retained bytes of a larger row;
        # source validation and the decoded mathematical input stay unchanged.
        return original(*args) + b" " * padding

    monkeypatch.setattr(segment_inputs, "_verified_row", large_row)
    reader.auxiliary(source.artifact_id, 0, read_segment=read_segment, scratch=tmp_path)
    report = reader.cache_report()
    assert 0 < report["segments"] < 5
    container = sys.getsizeof(reader._cache) - sys.getsizeof(type(reader._cache)())
    retained = sum(entry.size for entry in reader._cache.values()) + container
    assert report["accounted_bytes"] == retained
    assert report["accounted_bytes"] <= report["limit_bytes"]
