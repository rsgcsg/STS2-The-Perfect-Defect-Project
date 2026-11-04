"""Normal RL4.1 owner boundaries; old input-log/mid-epoch recovery is retired."""

from __future__ import annotations

import dataclasses
import threading

import pytest
from spireagent.json_boundary import BoundaryError
from spireagent.rl_exchange import Exchange
from spireagent.storage.store import copy_artifact
from stpd.rl.checkpoint_state import decode_state
from stpd.rl.segment_learner import SegmentLearner
from test_rl41_w1 import assert_tree, configuration, deliver


def cleanup(learner):
    learner.close()


def read_cp(owner):
    record = owner.exchange.latest_checkpoint(training_generation=owner.generation)
    manifest = owner.exchange.store.get_manifest(record["checkpoint_artifact_id"])
    payload = manifest.payload("checkpoint")
    return record, decode_state(owner.exchange.store.bytes(payload, maximum=payload.size))


def test_permanent_remote_stop_rejects_before_publication_or_listener(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    exchange = Exchange(config.peer, config.run)
    exchange.request_stop("permanent user stop")
    with pytest.raises(BoundaryError, match="cannot_resume_a_permanently_stopped_run"):
        SegmentLearner(config, tmp_path / "stopped", io)
    assert exchange.latest_model(training_generation=config.segments.training_generation) is None
    assert not (tmp_path / "stopped/inference.json").exists()


def test_initialize_only_saves_CP_with_zero_successful_uses(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    summary = owner.run(initialize_only=True)
    assert summary["updates"] == summary["first_use"] == summary["first_use_multi"] == 0
    assert summary["versions"] == 0 and summary["checkpoint_sequence"] == 1
    _, cp = read_cp(owner)
    assert cp.counters.updates == 0 and cp.identity.training_generation == owner.generation


def test_async_CP_is_announced_while_idle(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    original = owner.exchange.publish_checkpoint
    seen = []

    def published(*args, **kwargs):
        result = original(*args, **kwargs)
        seen.append(owner.newlease_stopped)
        (owner.work / "STOP").touch()
        return result

    monkeypatch.setattr(owner.exchange, "publish_checkpoint", published)
    owner.checkpoint()
    owner.run()
    assert seen[0] is False
    assert read_cp(owner)[0]["checkpoint_sequence"] == 2


def test_partial_dataset_stop_preserves_SGD_but_never_restores_hot_remainder(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    deliver(owner, actor="a")
    deliver(owner, actor="b")
    assert owner._prepare()
    owner._step()
    assert owner.updates == owner.first_use == 1 and owner.active is not None
    (owner.work / "STOP").touch()
    owner.run()
    record, cp = read_cp(owner)
    assert cp.counters.updates == cp.counters.first_use == 1
    assert "active" not in dataclasses.asdict(cp)
    cold_peer = {"kind": "local", "directory": str(tmp_path / "cold")}
    cold = Exchange(cold_peer, config.run)
    copy_artifact(
        owner.exchange.store, cold.store, record["checkpoint_artifact_id"], scratch=tmp_path
    )
    replacement = SegmentLearner(
        dataclasses.replace(
            config,
            peer=cold_peer,
            start_package=None,
            resume_checkpoint=record["checkpoint_artifact_id"],
            segments=dataclasses.replace(config.segments, training_generation="g-recovered"),
        ),
        tmp_path / "replacement",
        io,
    )
    try:
        assert replacement.updates == replacement.first_use == 1
        assert replacement.active is None and not replacement._prepare()
        assert not replacement.queue.pending and not replacement.lease_owners
        assert_tree(replacement.model.state_dict(), cp.weights)
        assert_tree(replacement.optimizer.state_dict(), cp.optimizer)
        assert_tree(replacement.ema, cp.ema)
    finally:
        cleanup(replacement)


def test_failed_telemetry_cannot_pair_new_Adam_with_old_counts(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    try:
        deliver(owner, actor="a")
        deliver(owner, actor="b")
        assert owner._prepare()
        actual = owner._event

        def broken(kind, **fields):
            if kind == "progress":
                raise OSError("injected telemetry failure")
            return actual(kind, **fields)

        monkeypatch.setattr(owner, "_event", broken)
        with pytest.raises(OSError, match="injected telemetry"):
            owner._step()
        assert owner.updates == owner.first_use == 1 and owner.active["minibatch"] == 1
        owner.checkpoint(final=True)
        _, cp = read_cp(owner)
        assert cp.counters.updates == cp.counters.first_use == 1
        assert_tree(cp.weights, owner.model.state_dict())
        assert_tree(cp.optimizer, owner.optimizer.state_dict())
        assert_tree(cp.ema, owner.ema)
    finally:
        cleanup(owner)


def test_episode_end_wakes_capacity_waiter_without_orphan_grant(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    base = {
        "run": config.run,
        "training_generation": owner.generation,
        "actor": "a",
        "episode": "e",
    }
    owner.control({**base, "operation": "episode_open"})
    previous = None
    for _ in range(config.segments.capacity_rows // config.segments.steps):
        previous = owner.control({**base, "operation": "begin", "previous_lease": previous})[
            "lease"
        ]
    waiting = threading.Event()
    actual = owner.condition.wait

    def wait(timeout=None):
        waiting.set()
        return actual(timeout)

    monkeypatch.setattr(owner.condition, "wait", wait)
    answers = []
    thread = threading.Thread(
        target=lambda: answers.append(
            owner.control({**base, "operation": "begin", "previous_lease": previous})
        )
    )
    thread.start()
    try:
        assert waiting.wait(5)
        owner.control(
            {
                **base,
                "operation": "episode_end",
                "run_id": None,
                "terminal": None,
                "outcome_states": [],
                "hp_battles": [],
                "unqualified": True,
            }
        )
        thread.join(5)
        assert not thread.is_alive() and answers == [{"stopped": True}]
        assert not owner.queue.leases and not owner.lease_owners and not owner.actor_episodes
    finally:
        with owner.condition:
            owner.newlease_stopped = True
            owner.condition.notify_all()
        thread.join(5)
        cleanup(owner)


def test_reward_HP_backfills_cross_segment_rows_before_episode_end(tmp_path, monkeypatch):
    import copy

    from stpd.p1.data import digest, json_bytes
    from stpd.p1.frame_compiler import FrameCompiler
    from stpd.p1.local_mu import LocalMuScorer
    from test_rl41_w1 import segment

    config, io = configuration(tmp_path, monkeypatch)
    config = dataclasses.replace(
        config, segments=dataclasses.replace(config.segments, episode_rows=4)
    )
    owner = SegmentLearner(config, tmp_path / "owner", io)
    scorer = LocalMuScorer(owner.exchange, tmp_path / "actor-model")
    base = {
        "run": config.run,
        "training_generation": owner.generation,
        "actor": "a",
        "episode": "episode-a",
    }
    payloads = []
    try:
        owner.control({**base, "operation": "episode_open"})
        previous = None
        for number in range(2):
            grant = owner.control({**base, "operation": "begin", "previous_lease": previous})
            scorer.adopt(grant["mu"])
            mu = scorer.models[grant["mu"]["snapshot_sha256"]]
            value = segment(
                mu,
                name=grant["lease"],
                run_id=base["episode"],
                rows=2,
                start=2 * number,
                generation=owner.generation,
                mu=grant["mu"],
            )
            if number:
                successor = copy.deepcopy(value["steps"][-1]["successor"])
                observation = successor["observation"]
                observation["combat"] = {"availability": "unavailable"}
                observation["scene"] = {"native_kind": "reward_card"}
                observation["player"] = {"content": {"hp": 57, "max_hp": 80}}
                compiler = FrameCompiler(mu.vocabulary)
                wire = compiler.encode(observation)
                outcome = scorer.score_compiled(
                    grant["mu"]["snapshot_sha256"], wire, 3, policy=False
                )
                value["steps"][-1]["successor"] = successor
                value["compiled_boundary"] = wire
                value["bootstrap"].update(
                    successor_input_sha256=digest(json_bytes(successor)),
                    outcome_probabilities=list(outcome.outcome_probabilities),
                )
                states = [
                    {
                        "key": f"{base['episode']}:{i}",
                        "segment_artifact_id": "hot:" + payloads[0].slot,
                        "row": i,
                        "hp": 0.57,
                    }
                    for i in range(2)
                ]
                states += [
                    {"key": f"{base['episode']}:{i + 2}", "current_row": i, "hp": 0.57}
                    for i in range(2)
                ]
                value["hp_battles"] = [
                    {
                        "battle_id": base["episode"] + ":0",
                        "states": states,
                        "start_step": 0,
                        "end_step": 4,
                        "hp": 0.57,
                        "boundary_step_index": 3,
                        "boundary_acquisition_id": successor["acquisition_id"],
                        "boundary_input_sha256": digest(json_bytes(successor)),
                    }
                ]
            payload = owner.exchange.put_hot_segment(value, owner="episode:" + base["episode"])
            payloads.append(payload)
            owner.control(
                {
                    "operation": "commit",
                    "run": config.run,
                    "training_generation": owner.generation,
                    "segment_id": grant["lease"],
                    "run_id": value["run_id"],
                    "rows": 2,
                    "mu": grant["mu"],
                    "payload": dataclasses.asdict(payload),
                }
            )
            previous = grant["lease"]
        assert owner._prepare()
        owner._collect_labels(wait=True)
        rows = owner.hp.groups[base["episode"] + ":0"]
        assert len(rows) == 4 and all(row["hp"] == 0.57 and row["uses"] == 0 for row in rows)
        assert [row["source"]["key"] for row in rows] == [
            f"{base['episode']}:{i}" for i in range(4)
        ]
        assert {row["source"]["segment_id"] for row in rows} == {"a/0", "a/1"}
        assert owner.actor_episodes  # label arrived while the same World is still open
        owner.control(
            {
                **base,
                "operation": "episode_end",
                "run_id": base["episode"],
                "terminal": None,
                "outcome_states": [],
                "hp_battles": [],
            }
        )
        for payload in payloads:
            owner.exchange.release_hot_payload(payload, "episode:" + base["episode"])
    finally:
        scorer.close()
        owner.close()
