"""Normal RL4.1 owner boundaries; old input-log/mid-epoch recovery is retired."""

from __future__ import annotations

import dataclasses
import threading

import pytest
from test_rl41_w1 import assert_tree, configuration, deliver

from spireagent.json_boundary import BoundaryError
from spireagent.rl_exchange import Exchange
from spireagent.storage.store import copy_artifact
from stpd.rl.checkpoint_state import decode_state
from stpd.rl.segment_learner import SegmentLearner


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
    owner.optimizer.param_groups[0]["lr"] = 8e-5
    summary = owner.run(initialize_only=True)
    assert summary["learning_configuration"]["lr"] == 8e-5
    assert summary["learning_configuration"]["optimizer_param_groups"][0]["lr"] == 8e-5
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
    for _ in range(min(config.segments.capacity_rows, config.segments.episode_rows)
                   // config.segments.steps):
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

    from test_rl41_w1 import segment

    from stpd.p1.data import digest, json_bytes
    from stpd.p1.frame_compiler import FrameCompiler
    from stpd.p1.local_mu import LocalMuScorer

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
                        "source": {"run_id": base["episode"], "segment_id": "a/0",
                            "training_generation": owner.generation, "row": i,
                            "key": f"{base['episode']}:{i}"},
                        "hp": 0.57,
                    }
                    for i in range(2)
                ]
                states += [
                    {"source": {"run_id": base["episode"], "segment_id": "a/1",
                        "training_generation": owner.generation, "row": i,
                        "key": f"{base['episode']}:{i + 2}"}, "hp": 0.57}
                    for i in range(2)
                ]
                value["hp_battles"] = [
                    {
                        "battle_id": base["episode"] + ":0",
                        "states": states,
                        "start_step": 0,
                        "end_step": 4,
                        "post_hp": 57,
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


def test_rl41_profile_binds_capacity_and_exposes_only_live_performance(tmp_path):
    import json

    from stpd.rl.learner import LearnerConfig
    from stpd.rl.performance import PREPARED_PERFORMANCE_FIELDS

    path = tmp_path / "bindings.json"
    path.write_text(json.dumps({"schema": "stpd/rl-learner-config-1", "profile": "rl41",
        "run": "profile-unit", "peer": {"kind": "local", "directory": str(tmp_path / "peer")},
        "wall_seconds": 123, "segments": {"training_generation": "g-profile"}}))
    config = LearnerConfig.read(path).bind_actors(32)
    assert (config.loss.mode, config.loss.value_coef, config.loss.entropy_coef) == ("ppo", .2, .01)
    assert (config.segments.steps, config.segments.dataset_rows,
            config.segments.capacity_rows) == (64, 1024, 3072)
    assert (config.segments.minibatches, config.segments.epochs) == (4, 1)
    assert (config.segments.auxiliary_rows, config.segments.terminal_uses,
            config.segments.hp_uses) == (128, 2, 1)
    assert config.wall_seconds == 123 and config.segments.training_generation == "g-profile"
    assert set(config.frozen()["performance"]) == set(PREPARED_PERFORMANCE_FIELDS)
    frozen = tmp_path / "effective.json"
    frozen.write_text(json.dumps(config.frozen()))
    assert LearnerConfig.read(frozen).frozen() == config.frozen()
    path.write_text(path.read_text().replace('"rl41"', '"unknown-profile"'))
    with pytest.raises(BoundaryError, match="unsupported_profile"):
        LearnerConfig.read(path)


def test_fast_episode_backpressure_keeps_first_dataset_reachable(tmp_path, monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor

    from test_rl41_w1 import segment

    from spireagent.rl_exchange import control_input

    config, io = configuration(tmp_path, monkeypatch)
    config = dataclasses.replace(config, segments=dataclasses.replace(config.segments,
        steps=2, dataset_rows=8, episode_rows=4, capacity_rows=12))
    owner = SegmentLearner(config, tmp_path / "owner", io)
    pool = ThreadPoolExecutor(1)
    from stpd.p1.local_mu import LocalMuScorer
    scorer = LocalMuScorer(owner.exchange, owner.work / "fixture-mu")

    def request(actor, operation, **fields):
        return owner.control(control_input({"operation": operation, "run": config.run,
            "training_generation": owner.generation, "actor": actor, "episode": "episode-" + actor,
            **fields}))

    def commit(actor, previous, start):
        grant = request(actor, "begin", previous_lease=previous)
        scorer.adopt(grant["mu"])
        frozen = scorer.models[grant["mu"]["snapshot_sha256"]]
        value = segment(frozen, name=grant["lease"], run_id="episode-" + actor,
            rows=2, start=start, generation=owner.generation, mu=grant["mu"])
        payload = owner.exchange.put_hot_segment(value, owner="fixture:" + actor)
        result = owner.control(control_input({"operation": "commit", "run": config.run,
            "training_generation": owner.generation, "segment_id": grant["lease"],
            "run_id": value["run_id"], "rows": 2, "mu": grant["mu"],
            "payload": dataclasses.asdict(payload)}))
        assert result["segment_id"] == grant["lease"]
        return grant["lease"]

    try:
        for actor in ("a", "b"):
            assert request(actor, "episode_open")["admitted"]
        previous = commit("a", None, 0)
        previous = commit("a", previous, 2)
        waiting = pool.submit(request, "a", "begin", previous_lease=previous)
        time.sleep(.1)
        assert not waiting.done()  # old code granted a third same-episode segment
        other = commit("b", None, 0)
        commit("b", other, 2)
        chosen = owner.queue.dataset()
        assert sum(value["rows"] for value in chosen) == 8
        assert {run: sum(v["rows"] for v in chosen if v["run_id"] == run)
                for run in {v["run_id"] for v in chosen}} == {"episode-a": 4, "episode-b": 4}
        assert waiting.result(timeout=2)["lease"] == "a/2"
    finally:
        with owner.condition:
            owner.newlease_stopped = True
            owner.condition.notify_all()
        pool.shutdown(wait=True)
        scorer.close()
        owner.close()


@pytest.mark.parametrize("terminal_uses", [1, 4])
def test_configured_epochs_and_coefficients_preserve_first_use_and_aux_limits(
        tmp_path, monkeypatch, terminal_uses):
    config, io = configuration(tmp_path, monkeypatch)
    config = dataclasses.replace(config, lr=1e-8,
        loss=dataclasses.replace(config.loss, policy_coef=.7, value_coef=.3,
                                 entropy_coef=.02, terminal_coef=.2, hp_coef=.3),
        segments=dataclasses.replace(config.segments, epochs=2, terminal_uses=terminal_uses,
                                     hp_uses=2,
                                     gae_lambda=0, head_lr=1e-8, target_kl=.9))
    owner = SegmentLearner(config, tmp_path / "owner", io)
    try:
        deliver(owner, actor="a", terminal=True)
        deliver(owner, actor="b", terminal=True)
        assert owner._prepare()
        expected_multi = int(owner.active_batch.policy_mask.sum())
        auxiliary = list(owner.active["terminal"])
        assert auxiliary
        results = [owner._step() for _ in range(config.segments.minibatches * 2)]
        assert all(result["stepped"] for result in results)
        assert [result["epoch"] for result in results] == [0] * 4 + [1] * 4
        assert owner.active is None and owner.datasets == 1 and owner.updates == 8
        assert owner.first_use == 4 and owner.first_use_multi == expected_multi
        assert all(row["uses"] == min(2, terminal_uses) and row["datasets"] == [1]
                   for row in auxiliary)
        assert sum(r["terminal_rows"] for r in results[:4]) == len(auxiliary)
        assert sum(r["terminal_rows"] for r in results[4:]) == (
            len(auxiliary) if terminal_uses > 1 else 0)
        owner.checkpoint(final=True)
        _, cp = read_cp(owner)
        assert cp.config["segments"]["epochs"] == 2
        assert cp.config["segments"]["gae_lambda"] == 0
        assert cp.config["loss"]["policy_coef"] == .7
    finally:
        cleanup(owner)


def test_zero_auxiliary_rows_disables_selection_from_nonempty_pool(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    config = dataclasses.replace(config, segments=dataclasses.replace(config.segments,
                                                                     auxiliary_rows=0))
    owner = SegmentLearner(config, tmp_path / "owner", io)
    try:
        deliver(owner, actor="a", terminal=True)
        deliver(owner, actor="b", terminal=True)
        assert owner.terminal.groups
        selected = owner._select(take=False)
        assert selected is not None and not selected["request"].auxiliary
        assert not selected["terminal"] and not selected["hp"]
    finally:
        cleanup(owner)
