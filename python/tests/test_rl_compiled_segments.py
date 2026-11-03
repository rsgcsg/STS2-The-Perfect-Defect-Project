"""Actor wire -> original Store -> frozen PPO/TD/auxiliary and bounded read-ahead."""
from __future__ import annotations

import threading
from collections import Counter
from dataclasses import replace

import pytest
import torch
from test_p1_frame_compiler import assert_frame_equal
from test_rl_loop import PRODUCER
from test_rl_segment_learner import cleanup, configuration, deliver

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.managed_segment_store import read_segment
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl.segment_inputs import publish_input_pack
from stpd.rl.segment_learner import SegmentLearner


def compiled_config(config):
    return replace(config, segments=replace(config.segments,
        input_compiler_identity=FrameCompiler(public_vocabulary()).identity, prepare_ahead=True))


def test_actor_compiled_payload_restores_exact_frames_and_normal_optimizer_update(
        tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ("reference", "compiled")]
    for root in roots:
        root.mkdir()
    config, io = configuration(roots[0], monkeypatch)
    reference = SegmentLearner(config, roots[0] / "owner", io)
    config2, io2 = configuration(roots[1], monkeypatch)
    compiled = SegmentLearner(compiled_config(config2), roots[1] / "owner", io2)
    try:
        for owner, root, packed in [(reference, roots[0], False), (compiled, roots[1], True)]:
            for actor in ("first", "second"):
                records = deliver(owner, root, actor, monkeypatch, compiled=packed)
                manifest = owner.exchange.store.get_manifest(records[0]["segment_artifact_id"])
                assert len(manifest.payloads) == (3 if packed else 2)
        assert reference._prepare()

        def forbidden(*_args, **_kwargs):
            raise AssertionError("the compiled consumer recompiled raw input")

        monkeypatch.setattr(FrameCompiler, "compile", forbidden)
        monkeypatch.setattr("stpd.rl.segment_targets.compile_frame", forbidden)
        monkeypatch.setattr("stpd.rl.segment_learner.compile_frame", forbidden)
        assert compiled._prepare()
        for left, right in zip(reference.active_rows, compiled.active_rows, strict=True):
            assert left.frame.choices == right.frame.choices
            for name, tensor in left.frame.tensors.items():
                assert tensor.equal(right.frame.tensors[name]), name
        for name in ("targets", "probabilities", "advantages", "raw_advantages"):
            assert reference.active["frozen"][name].equal(compiled.active["frozen"][name]), name
        rng = torch.get_rng_state().clone()
        reference._step()
        torch.set_rng_state(rng)
        compiled._step()
        assert reference.updates == compiled.updates == 1
        assert reference.first_use == compiled.first_use
        for name, value in reference.model.state_dict().items():
            torch.testing.assert_close(value, compiled.model.state_dict()[name],
                                       atol=2e-7, rtol=2e-6)
    finally:
        cleanup(reference)
        cleanup(compiled)


def test_explicit_old_checkpoint_input_pack_uses_original_rows_without_hot_recompile(
        tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    replacement = None
    try:
        records = []
        for actor in ("first", "second"):
            records += deliver(owner, tmp_path, actor, monkeypatch)
        owner.checkpoint(final=True)
        cp = owner.exchange.latest_checkpoint()["checkpoint_artifact_id"]
        pack = publish_input_pack(owner.exchange.store,
            [record["segment_artifact_id"] for record in records], owner.model.vocabulary, PRODUCER,
            read_segment=read_segment, scratch=tmp_path, checkpoint=cp)
        settings = replace(config.segments, input_compiler_identity=FrameCompiler(
            owner.model.vocabulary).identity, input_pack_artifact_id=pack.artifact_id,
            prepare_ahead=True)
        replacement = SegmentLearner(replace(config, segments=settings, resume_checkpoint=cp),
                                     tmp_path / "replacement", io)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("old data silently recompiled in the hot consumer")

        monkeypatch.setattr(FrameCompiler, "compile", forbidden)
        monkeypatch.setattr("stpd.rl.segment_targets.compile_frame", forbidden)
        monkeypatch.setattr("stpd.rl.segment_learner.compile_frame", forbidden)
        assert replacement._prepare()
        assert len(replacement.active_rows) == 4 and replacement.updates == 0
        replacement._step()
        assert replacement.updates == 1 and replacement.first_use == 2
        assert {row["segment_artifact_id"] for row in replacement.active["segments"]} == {
            row["segment_artifact_id"] for row in records}
    finally:
        cleanup(owner)
        if replacement is not None:
            cleanup(replacement)


def test_read_ahead_overlaps_real_step_without_releasing_queue_or_changing_membership(
        tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(compiled_config(config), tmp_path / "owner", io)
    blocked, entered = threading.Event(), threading.Event()
    try:
        for actor in ("first", "second", "third", "fourth"):
            deliver(owner, tmp_path, actor, monkeypatch, compiled=True)
        assert owner._prepare()
        before = owner.queue.state()
        selected = owner.queue.dataset(take=False)
        original_read = owner.io.read_segment
        waiting_for = selected[0]["segment_artifact_id"]

        def delayed(store, artifact, **kwargs):
            if artifact == waiting_for:
                entered.set()
                assert blocked.wait(10)
            return original_read(store, artifact, **kwargs)

        owner.io = replace(owner.io, read_segment=delayed)
        owner._prepare_ahead()
        assert entered.wait(2)
        assert owner.queue.state() == before  # The cache is not a second reservation count.
        owner._step()  # Actual MStd optimizer while the actual reader thread is in flight.
        blocked.set()
        owner.prepared_inputs.result(timeout=5)
        while owner.datasets == 0:
            owner._step()
        assert owner._prepare()
        assert owner.active["segments"] == selected
        assert owner.queue.rows == 0
        assert owner.timing["input_optimizer_overlap_seconds"] > 0
    finally:
        blocked.set()
        cleanup(owner)


def test_compiled_terminal_and_hp_auxiliaries_share_the_original_actor_frames(
        tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(compiled_config(config), tmp_path / "owner", io)
    replacement = None
    try:
        records = []
        for actor in ("first", "second"):
            records += deliver(owner, tmp_path, actor, monkeypatch,
                               compiled=True, battle=True, natural=True)

        reads = []

        def counted_read(store, artifact, **kwargs):
            reads.append(artifact)
            return io.read_segment(store, artifact, **kwargs)

        owner.io = replace(io, read_segment=counted_read)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("auxiliary input was recompiled")

        monkeypatch.setattr(FrameCompiler, "compile", forbidden)
        monkeypatch.setattr("stpd.rl.segment_targets.compile_frame", forbidden)
        monkeypatch.setattr("stpd.rl.segment_learner.compile_frame", forbidden)
        assert owner._prepare()
        assert tuple(map(len, owner.active_auxiliary)) == (8, 8)
        assert Counter(reads) == Counter(record["segment_artifact_id"] for record in records)
        # Later datasets and terminal/HP labels remain fresh reference metadata.
        previous = owner.active_auxiliary[0]
        again = owner._auxiliary(owner.active["terminal"])
        changed = owner._auxiliary([{**ref, "outcome": None, "hp": .75}
                                    for ref in owner.active["terminal"]])
        assert len(reads) == len(records)
        for before, after, relabelled in zip(previous, again, changed, strict=True):
            assert before is not after and before.frame is not after.frame
            assert (before.floor, before.key, before.outcome, before.hp) == (
                after.floor, after.key, after.outcome, after.hp)
            assert relabelled.outcome is None and relabelled.hp == .75
            assert_frame_equal(before.frame, after.frame)
        owner._step()
        assert owner.updates == 1
        assert sum(row["uses"] for group in owner.hp.groups.values() for row in group) == 4
        assert sum(row["uses"] for group in owner.terminal.groups.values() for row in group) == 4
        owner.checkpoint(final=True)
        cp = owner.exchange.latest_checkpoint()["checkpoint_artifact_id"]
        replacement = SegmentLearner(replace(owner.config, resume_checkpoint=cp),
                                     tmp_path / "replacement", io)
        assert replacement.input_reader.cache_report()["segments"] == 0
        rng = replacement.rng.getstate()
        assert replacement._prepare()
        assert replacement.rng.getstate() == rng and replacement.updates == 1
        assert replacement.active["terminal"] == owner.active["terminal"]
        assert replacement.active["hp"] == owner.active["hp"]
        assert replacement.terminal.state() == owner.terminal.state()
        assert replacement.hp.state() == owner.hp.state()
        for before, after in zip(owner.active_auxiliary[0], replacement.active_auxiliary[0],
                                 strict=True):
            assert (before.floor, before.key, before.outcome, before.hp) == (
                after.floor, after.key, after.outcome, after.hp)
            assert_frame_equal(before.frame, after.frame)
    finally:
        cleanup(owner)
        if replacement is not None:
            cleanup(replacement)


def test_compiled_consumer_rejects_missing_old_representation_without_silent_fallback(
        tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(compiled_config(config), tmp_path / "owner", io)
    try:
        for actor in ("first", "second"):
            deliver(owner, tmp_path, actor, monkeypatch)  # Deliberate old encoding, no pack.
        with pytest.raises(BoundaryError, match="original_segment_missing_from_frozen_input_pack"):
            owner._prepare()
        assert owner.updates == owner.first_use == 0
        assert sum(record["rows"] for record in owner.active["segments"]) == 4
    finally:
        cleanup(owner)
