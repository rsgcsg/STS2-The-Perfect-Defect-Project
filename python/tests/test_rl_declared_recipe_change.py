"""RL5-METHODS C (#573): declared learning-recipe changes at a continuation from a CP."""

from __future__ import annotations

import dataclasses
import os

import pytest
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
from test_rl41_w1 import assert_tree, configuration, deliver

from stpd.rl import recipe_changes
from stpd.rl.checkpoint_state import decode_state
from stpd.rl.segment_learner import SegmentLearner

LOCKED = "self_contained_resume_source_config_or_generation_changed"


def read_cp(owner):
    record = owner.exchange.latest_checkpoint(training_generation=owner.generation)
    manifest = owner.exchange.store.get_manifest(record["checkpoint_artifact_id"])
    payload = manifest.payload("checkpoint")
    return record, decode_state(owner.exchange.store.bytes(payload, maximum=payload.size))


def trained_cp(tmp_path, monkeypatch):
    """A CP after one real SGD step (non-empty Adam moments)."""
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    try:
        deliver(owner, actor="a")
        deliver(owner, actor="b")
        assert owner._prepare() and owner._step()["stepped"]
        (owner.work / "STOP").touch()
        owner.run()
        record, cp = read_cp(owner)
    finally:
        owner.close()
    assert cp.counters.updates == 1 and cp.optimizer["state"]
    return config, io, record["checkpoint_artifact_id"], cp


def initialized_cp(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    owner.run(initialize_only=True)
    record, _ = read_cp(owner)
    return config, io, record["checkpoint_artifact_id"]


def continuation(config, cp_id, generation="g-new", declared=None, **changes):
    """The new run's config: same run, a fresh generation, the given recipe changes."""
    segments = {k[9:]: changes.pop(k) for k in list(changes) if k.startswith("segments.")}
    loss = {k[5:]: changes.pop(k) for k in list(changes) if k.startswith("loss.")}
    return dataclasses.replace(
        config, start_package=None, resume_checkpoint=cp_id, resume_recipe_changes=declared,
        loss=dataclasses.replace(config.loss, **loss),
        segments=dataclasses.replace(config.segments, training_generation=generation,
                                     **segments), **changes)


def declaration(cp_id, **changes):
    return {"checkpoint_artifact_id": cp_id,
            "changes": {key.replace("__", "."): {"old": old, "new": new}
                        for key, (old, new) in changes.items()}}


def test_accepted_change_keeps_model_and_optimizer_state_and_applies_the_new_values(
        tmp_path, monkeypatch):
    config, io, cp_id, cp = trained_cp(tmp_path, monkeypatch)
    old_lr, old_head = config.lr, config.segments.head_lr
    declared = declaration(cp_id, lr=(old_lr, 5e-6), segments__head_lr=(old_head, 2e-5),
                           loss__entropy_coef=(0.01, 0.02),
                           segments__value_target=("segment", "game"),
                           segments__gae_lambda=(0.99, 0.95))
    new = continuation(config, cp_id, declared=declared, lr=5e-6, **{
        "segments.head_lr": 2e-5, "loss.entropy_coef": 0.02,
        "segments.value_target": "game", "segments.gae_lambda": 0.95})
    owner = SegmentLearner(new, tmp_path / "next", io)
    try:
        assert_tree(owner.model.state_dict(), cp.weights)
        assert_tree(owner.ema, cp.ema)
        state = owner.optimizer.state_dict()
        assert_tree(state["state"], cp.optimizer["state"])  # Adam moments and steps kept
        assert [g["lr"] for g in state["param_groups"]] == [5e-6, 2e-5]
        for group, saved in zip(state["param_groups"], cp.optimizer["param_groups"],
                                strict=True):
            assert {k: v for k, v in group.items() if k != "lr"} == \
                {k: v for k, v in saved.items() if k != "lr"}
        assert owner.updates == 1  # the warm-up (if any) counts on, never restarts
        restored = owner.restored_checkpoint
        assert restored["checkpoint_artifact_id"] == cp_id and restored["updates"] == 1
        assert restored["recipe_changes"] == {k: dict(v) for k, v in declared["changes"].items()}
        assert restored["optimizer_lr"] == {"restored": [old_lr, old_head],
                                            "applied": [5e-6, 2e-5]}
        event, = [e for e in owner.exchange.event_records()
                  if e["kind"] == "checkpoint_restored"]
        assert event["checkpoint_artifact_id"] == cp_id
        assert event["restored_training_generation"] == cp.identity.training_generation
        assert event["training_generation"] == "g-new"
        # It trains on with the new step size; the new CP carries the new recipe.
        deliver(owner, actor="c")
        deliver(owner, actor="d")
        assert owner._prepare() and owner._step()["stepped"]
        assert owner.updates == 2
        assert [g["lr"] for g in owner.optimizer.param_groups] == [5e-6, 2e-5]
        owner.checkpoint(final=True)
        record, second = read_cp(owner)
        assert second.config["lr"] == 5e-6 and second.config["segments"]["value_target"] == "game"
        assert second.config["resume_recipe_changes"] == declared
    finally:
        owner.close()
    # A second automatic resume inside the new run: locked to the NEW recipe. The
    # declaration still in the config names the first CP, so it is inactive here.
    again = continuation(new, record["checkpoint_artifact_id"], generation="g-new-r2",
                         declared=declared)
    third = SegmentLearner(again, tmp_path / "third", io)
    try:
        assert third.recipe_changes is None and third.updates == 2
        assert third.restored_checkpoint["recipe_changes"] is None
        assert [g["lr"] for g in third.optimizer.param_groups] == [5e-6, 2e-5]
    finally:
        third.close()
    back = continuation(config, record["checkpoint_artifact_id"], generation="g-new-r3")
    with pytest.raises(ValueError, match=LOCKED):  # the old recipe is a change now
        SegmentLearner(back, tmp_path / "back", io)
    declared_back = continuation(config, record["checkpoint_artifact_id"],
                                 generation="g-new-r4", declared=declared)
    with pytest.raises(ValueError, match="declared_recipe_changes_name_another_checkpoint"):
        SegmentLearner(declared_back, tmp_path / "back2", io)


def test_without_a_declaration_resume_is_exactly_as_before(tmp_path, monkeypatch):
    config, io, cp_id = initialized_cp(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match=LOCKED):
        SegmentLearner(continuation(config, cp_id, lr=5e-6), tmp_path / "a", io)
    with pytest.raises(ValueError, match=LOCKED):  # the same generation stays refused
        SegmentLearner(continuation(config, cp_id, generation=config.segments
                                    .training_generation), tmp_path / "b", io)
    owner = SegmentLearner(continuation(config, cp_id), tmp_path / "c", io)
    try:
        assert owner.recipe_changes is None
        assert owner.restored_checkpoint["optimizer_lr"]["restored"] == \
            owner.restored_checkpoint["optimizer_lr"]["applied"]
        owner.checkpoint(final=True)
        _, cp = read_cp(owner)
        assert "resume_recipe_changes" not in cp.config  # default CPs keep their bytes
    finally:
        owner.close()


@pytest.mark.parametrize(("changes", "declared", "message"), [
    ({"lr": 5e-6, "loss.entropy_coef": 0.02}, {"lr": (1e-5, 5e-6)},
     "undeclared_recipe_change: loss.entropy_coef"),
    ({"lr": 5e-6}, {"lr": (2e-5, 5e-6)}, "declared_recipe_change_old_mismatch: lr"),
    ({"lr": 5e-6}, {"lr": (1e-5, 6e-6)}, "declared_recipe_change_new_mismatch: lr"),
    ({}, {"lr": (1e-5, 1e-5)}, "declared_recipe_change_not_different: lr"),
    ({"loss.value_coef": 0.3}, {"loss__value_coef": (0.2, 0.3)},
     "declared_recipe_change_not_allowed: loss.value_coef"),
    ({"loss.value_coef": 0.3, "lr": 5e-6}, {"lr": (1e-5, 5e-6)},
     "undeclared_recipe_change: loss.value_coef"),
    ({"segments.lr_warmup_steps": 10}, {"segments__lr_warmup_steps": (0, 10)},
     "declared_recipe_change_not_allowed: segments.lr_warmup_steps"),
])
def test_each_bad_declaration_is_refused(tmp_path, monkeypatch, changes, declared, message):
    config, io, cp_id = initialized_cp(tmp_path, monkeypatch)
    new = continuation(config, cp_id, declared=declaration(cp_id, **declared), **changes)
    with pytest.raises(ValueError, match=message):
        SegmentLearner(new, tmp_path / "next", io)


def test_a_declaration_needs_a_resume_cp_and_a_valid_shape(tmp_path, monkeypatch):
    config, io, cp_id = initialized_cp(tmp_path, monkeypatch)
    fresh = dataclasses.replace(config, resume_recipe_changes=declaration(
        cp_id, lr=(1e-5, 5e-6)), lr=5e-6, segments=dataclasses.replace(
            config.segments, training_generation="g-fresh"))
    with pytest.raises(ValueError, match="declared_recipe_changes_require_resume_checkpoint"):
        SegmentLearner(fresh, tmp_path / "fresh", io)
    for bad in ({"changes": {}}, {"checkpoint_artifact_id": cp_id, "changes": {}},
                {"checkpoint_artifact_id": cp_id, "changes": {"lr": {"new": 5e-6}}}):
        with pytest.raises(ValueError, match="declared_recipe_changes_malformed"):
            SegmentLearner(continuation(config, cp_id, declared=bad, lr=5e-6),
                           tmp_path / "bad", io)
    with pytest.raises(ValueError, match="declared_recipe_changes_name_another_checkpoint"):
        SegmentLearner(continuation(config, cp_id, declared=declaration(
            "another", lr=(1e-5, 5e-6)), lr=5e-6), tmp_path / "other", io)


def test_switching_to_game_value_targets_starts_an_empty_game_intake(tmp_path, monkeypatch):
    """value_target segment -> game on the fast chain: the old generation's queue is
    discarded with it, the new intake holds nothing and its spill directory is empty."""
    from test_rl_fast_value_target import game_learner

    owner = game_learner(tmp_path / "a", monkeypatch, value_target="segment")
    try:
        owner.run(initialize_only=True)
        record, _ = read_cp(owner)
        config, io = owner.config, owner.io
    finally:
        owner.close()
    # The new run directory has its own live root (WAL, spill), as launch gives it.
    io = dataclasses.replace(io, dataplane={**io.dataplane, "root": str(tmp_path / "b-live")})
    cp_id = record["checkpoint_artifact_id"]
    new = continuation(config, cp_id, declared=declaration(
        cp_id, segments__value_target=("segment", "game")), **{"segments.value_target": "game"})
    owner = SegmentLearner(new, tmp_path / "b", io)
    try:
        intake = owner.fast_status()["fast_chain"]["intake"]
        assert owner.fast_intake.value_target == "game"
        assert intake["held_rows"] == intake["spill_files"] == 0
        assert not owner.queue.pending
        assert os.listdir(tmp_path / "b-live" / "value-target-spill") == []
    finally:
        owner.close()


def test_rules_are_pure_and_bool_aware():
    old = {"lr": 1e-5, "loss": {"entropy_coef": 0.01, "hp_coef": 0.0},
           "segments": {"steps": 2}}
    new = {"lr": 1e-5, "loss": {"entropy_coef": 0.01, "hp_coef": 0.1},
           "segments": {"steps": 2, "value_target": "game"}}
    declared = {"checkpoint_artifact_id": "cp",
                "changes": {"segments.value_target": {"old": "segment", "new": "game"}}}
    with pytest.raises(ValueError, match="declared_recipe_change_old_mismatch"):
        recipe_changes.check({"checkpoint_artifact_id": "cp", "changes": {
            "loss.hp_coef": {"old": False, "new": 0.1}}}, old, new)  # 0.0 == False in Python
    new["loss"]["hp_coef"] = 0.0
    assert recipe_changes.check(declared, old, new) == declared["changes"]
    assert recipe_changes.lookup(old, "segments.value_target") == "segment"
    assert recipe_changes.differing_paths({"a": {"b": 1}}, {"a": {"b": 2, "c": 3}}) == [
        "a.b", "a.c"]
    with pytest.raises(ValueError, match="declared_recipe_change_key_absent: segments.head_lr"):
        recipe_changes.check({"checkpoint_artifact_id": "cp", "changes": {
            "segments.head_lr": {"old": 1, "new": 2}}}, old, new)


def test_preflight_reads_the_cp_config_from_the_peer_header_without_torch(
        tmp_path, monkeypatch):
    """launch.checkpoint_config (torch-free, header only) returns the recipe the learner
    stored in the CP, which preflight compares with the declaration."""
    from pathlib import Path

    from test_friday_fastchain import launch

    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    owner.run(initialize_only=True)
    record, cp = read_cp(owner)
    cp_id = record["checkpoint_artifact_id"]
    peer = Path(config.peer["directory"])
    assert launch.checkpoint_config(peer, cp_id) == cp.config
    assert cp.config["lr"] == config.lr and "resume_recipe_changes" not in cp.config
    with pytest.raises(FileNotFoundError):
        launch.checkpoint_config(peer, "0" * 64)
