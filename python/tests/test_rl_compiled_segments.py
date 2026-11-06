"""Complete process-prepared batches, bounded next-batch credit and input bindings."""

from __future__ import annotations

import dataclasses

import pytest
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
from test_rl41_w1 import configuration, deliver, ready, segment, source
from test_rl41_w1 import model as model

from stpd.p1.frame_compiler import FrameCompiler
from stpd.rl.segment_learner import SegmentLearner


def compiled_config(config):
    return dataclasses.replace(
        config, segments=dataclasses.replace(config.segments, prepare_ahead=True)
    )


def test_live_ready_never_projects_or_tokenizes_again(model, monkeypatch):
    value = segment(model)

    def forbidden(*args, **kwargs):
        raise AssertionError("actor-compiled input was projected again")

    monkeypatch.setattr(FrameCompiler, "compile", forbidden)
    prepared = source(model, value)
    assert len(prepared.frames) == value["rows"]
    assert prepared.frames[0].choices == tuple(
        row["choice_id"] for row in value["steps"][0]["input"]["catalog"]
    )


def test_bad_actor_compiled_input_fails_before_ready(model):
    value = segment(model)
    value["steps"][0]["input"]["observation"]["catalog"][0]["choice_id"] = "changed-choice"
    with pytest.raises(ValueError, match="hash|binding"):
        source(model, value)


def test_finite_buckets_keep_real_members_and_every_catalog_entry(model):
    batch = ready(model, [segment(model)])
    physical = 0
    for group in batch.minibatches[0].groups:
        t = group.tensors
        assert t["scene_mask"].shape[0] in (1, 2, 4, 8, 16, 32, 64)
        assert t["scene_mask"].shape[1] in (32, 64, 128, 256, 512, 1024)
        assert t["text_ids"].shape[0] in (0, 32, 128, 512, 2048)
        physical += t["scene_mask"].shape[0]
    assert physical >= 4
    assert batch.minibatches[0].td_rows == len(batch.row_refs) == 4
    assert sum(batch.policy_mask) == 3
    assert len(batch.frames[0].choices) == 2 and len(batch.frames[1].choices) == 1


def test_next_complete_batch_stays_charged_until_owner_selects_it(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    try:
        for actor in ("a", "b", "c", "d"):
            deliver(owner, actor=actor, terminal=True)
        assert owner.queue.rows == 8 and owner._prepare()
        selected = owner.queue.dataset(take=False)
        assert owner.queue.rows == 4
        owner._prepare_ahead()
        next_batch = owner.pending_batch[1].result()
        assert owner.queue.rows == 4 and owner.first_use == owner.updates == 0
        assert not next_batch.request.auxiliary  # current owner has reserved them
        assert all(row["uses"] == 0 for rows in owner.terminal.groups.values() for row in rows)
        while owner.active is not None:
            owner._step()
        assert owner.first_use == 4 and owner.datasets == 1
        assert owner._prepare()
        assert owner.active["segments"] == selected and owner.queue.rows == 0
        assert owner.active_batch.request is next_batch.request
        assert owner.active_batch.worker_pids
    finally:
        owner.close()
