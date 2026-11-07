"""Normal RL4.1 owner boundaries; old input-log/mid-epoch recovery is retired."""

from __future__ import annotations

import dataclasses
import os
import threading
import time
from types import SimpleNamespace

import pytest
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
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


def test_first_use_bias_pairs_rows_and_pools_histograms_without_pooling_quantiles():
    from types import SimpleNamespace

    import torch

    from stpd.rl.segment_learner import _first_use_policy_bias

    batch = SimpleNamespace(policy_mask=torch.tensor([True, False, True, True, True]),
        metadata=[{"generated_timestamp": value, "mu_version": 8, "mu_updates": 18}
                  for value in (99., 95., None, 101., 50.)])
    samples = [{"row": index, "ratio": ratio, "kl_mu_theta": kl,
                "outside_clip": ratio is not None and abs(ratio - 1) > .2,
                "ppo_objective_clipped": index == 0,
                "ratio_above_one": ratio is not None and ratio > 1}
               for index, ratio, kl in ((4, 2., .3), (0, .5, .01), (1, 9., 9.),
                                        (2, None, None), (3, 1., 0.))]
    first = _first_use_policy_bias(batch, [0], samples, now=100., version=10, updates=20)
    second = _first_use_policy_bias(batch, [1, 2, 3, 4], samples,
                                    now=100., version=10, updates=20)
    merged = _first_use_policy_bias(batch, [0, 1, 2, 3, 4], samples,
                                    now=100., version=10, updates=20)
    assert first["all"]["n"] == 1 and second["all"]["n"] == 3
    assert merged["coverage_complete"] and merged["expected_n"] == merged["all"]["n"] == 4
    assert merged["missing_age_n"] == merged["negative_age_n"] == 1
    assert merged["age_seconds"][0]["n"] == 1  # Negative ages stay visible.
    assert merged["age_seconds"][1]["ratio"]["sum"] == .5  # Exact logical pairing.
    assert sum(part["n"] for part in merged["age_seconds"]) == 3
    for metric in ("ratio", "kl_mu_theta"):
        left, right, all_rows = (value["all"][metric] for value in (first, second, merged))
        assert [a + b for a, b in zip(left["histogram"], right["histogram"], strict=True)
                ] == all_rows["histogram"]
        assert left["finite_n"] + right["finite_n"] == all_rows["finite_n"] == 3
        assert all_rows["nonfinite_n"] == 1
        assert left["sum"] + right["sum"] == pytest.approx(all_rows["sum"])
    reused = _first_use_policy_bias(batch, [], samples, now=100., version=10, updates=20)
    assert reused["all"]["n"] == 0 and reused["coverage_complete"]
    gap = _first_use_policy_bias(batch, [0], [], now=100., version=10, updates=20)
    assert not gap["coverage_complete"] and gap["expected_n"] == 1


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


def test_live_progress_pairs_only_successful_first_use_policy_rows_and_omits_transient_vectors(
        tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    config = dataclasses.replace(config, segments=dataclasses.replace(
        config.segments, minibatches=1, epochs=2, target_kl=.99))
    owner = SegmentLearner(config, tmp_path / "owner", io)
    try:
        deliver(owner, actor="a")
        deliver(owner, actor="b")
        assert owner._prepare()
        first, reused = owner._step(), owner._step()
        assert first["stepped"] and reused["stepped"]
        assert first["first_use_policy_bias"]["all"]["n"] == 2
        assert first["first_use_policy_bias"]["coverage_complete"]
        assert reused["first_use_policy_bias"]["all"]["n"] == 0
        assert reused["first_use_multi"] == first["first_use_multi"] == 2
        for event in owner.exchange.event_records():
            assert "_policy_rows" not in event
    finally:
        owner.close()


def test_partial_dataset_stop_preserves_SGD_but_never_restores_hot_remainder(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    deliver(owner, actor="a")
    deliver(owner, actor="b")
    assert owner._prepare()
    step = owner._step()
    assert "_policy_rows" not in step
    assert step["first_use_policy_bias"]["coverage_complete"]
    assert step["first_use_policy_bias"]["all"]["n"] == step["first_use_multi"]
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


@pytest.mark.parametrize("closure", ["open", "closed", "retired"])
def test_reward_HP_backfills_consumed_rows_across_episode_close(tmp_path, monkeypatch, closure):
    import copy

    from test_rl41_w1 import segment

    from stpd.p1.data import digest, json_bytes
    from stpd.p1.frame_compiler import FrameCompiler
    from stpd.p1.local_mu import LocalMuScorer

    config, io = configuration(tmp_path, monkeypatch)
    config = dataclasses.replace(
        config, segments=dataclasses.replace(config.segments, episode_rows=4,
                                            dataset_rows=2, minibatches=1, epochs=1)
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
    producer_released = False
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
            if not number:
                assert owner._prepare()
                owner._step()
                assert owner.active is None and owner.first_use == 2
        close = {**base, "operation": "episode_end", "run_id": base["episode"],
                 "terminal": None, "outcome_states": [], "hp_battles": []}
        if closure == "closed":
            owner.control(close)
        elif closure == "retired":
            owner.retire_actor("a", {"actor": "a", "job_id": "original",
                                    "terminal": True, "state": "FAILED"})
        if closure != "open":
            for payload in payloads:
                owner.exchange.release_hot_payload(payload, "episode:" + base["episode"])
            producer_released = True
        assert owner._prepare()
        owner._collect_labels(wait=True)
        rows = owner.hp.groups[base["episode"] + ":0"]
        assert len(rows) == 4 and all(row["hp"] == 0.57 and row["uses"] == 0 for row in rows)
        assert [row["source"]["key"] for row in rows] == [
            f"{base['episode']}:{i}" for i in range(4)
        ]
        assert {row["source"]["segment_id"] for row in rows} == {"a/0", "a/1"}
        if closure == "open":
            assert owner.actor_episodes
            owner.control(close)
        else:
            assert not owner.actor_episodes
        owner._step()
        assert not any(ref_owner == "delayed:" + base["episode"]
                       for _slot, ref_owner in owner.hot_references)
        if not producer_released:
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


def test_CP_export_uses_exact_raw_theta_without_learning_or_auxiliary_heads(tmp_path, monkeypatch):
    from stpd.evaluation.rl_checkpoint import export_checkpoint
    from stpd.p1.export import action_weights, load_model
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    deliver(owner, actor="a")
    deliver(owner, actor="b")
    assert owner._prepare()
    owner._step()
    expected = action_weights(owner.model.state_dict())
    owner.run(initialize_only=True)
    record, _ = read_cp(owner)
    binding = export_checkpoint(owner.exchange.store, record["checkpoint_artifact_id"],
                                tmp_path / "evaluation")
    from pathlib import Path
    model, _ = load_model(Path(binding["package_directory"]) / "model.tensor-tree",
                          binding["model_sha256"])
    assert_tree(model.state_dict(), expected)
    assert not any(k.startswith("rl4_heads.") for k in model.state_dict())
    assert binding["updates"] == 1 and binding["weights"] == "raw"
    assert binding["checkpoint_artifact_id"] == record["checkpoint_artifact_id"]
    assert owner.exchange.latest_checkpoint(training_generation=owner.generation) == record


def test_cold_restore_inherits_frozen_deadline_and_rejects_extension(tmp_path,monkeypatch):
    from spireagent.json_boundary import BoundaryError

    config, io = configuration(tmp_path, monkeypatch)
    old_deadline = time.time() + 7200
    config = dataclasses.replace(config, deadline_at=old_deadline)
    owner = SegmentLearner(config, tmp_path / "first", io)
    owner.run(initialize_only=True)
    record = owner.exchange.latest_checkpoint(training_generation=owner.generation)
    cold_peer = {"kind": "local", "directory": str(tmp_path / "cold")}
    cold = Exchange(cold_peer, config.run)
    copy_artifact(
        owner.exchange.store, cold.store, record["checkpoint_artifact_id"], scratch=tmp_path
    )
    request = dataclasses.replace(
        config,
        peer=cold_peer,
        start_package=None,
        resume_checkpoint=record["checkpoint_artifact_id"],
        deadline_at=old_deadline + 3600,
        segments=dataclasses.replace(config.segments, training_generation="g-cold"),
    )
    with pytest.raises(BoundaryError, match="checkpoint_deadline_cannot_be_reset"):
        SegmentLearner(request, tmp_path / "rejected", io)
    replacement = SegmentLearner(
        dataclasses.replace(request, deadline_at=None), tmp_path / "inherited", io
    )
    try:
        assert replacement.deadline_at == old_deadline
        assert replacement.config.deadline_at == old_deadline
        assert replacement._learning_state().config["deadline_at"] == old_deadline
    finally:
        replacement.close()


@pytest.mark.parametrize("pool_fails", [False, True])
def test_capacity_monitor_failure_still_closes_all_learning_resources(pool_fails):
    called = []

    class Condition:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def notify_all(self):
            pass

    class Archive:
        def stop_capacity_monitor(self):
            called.append("archive")
            raise RuntimeError("monitor still running")

    def pool_close():
        called.append("pool")
        if pool_fails:
            raise RuntimeError("pool close failure")

    owner = SimpleNamespace(
        condition=Condition(),
        newlease_stopped=False,
        input_fenced=False,
        pending_labels=[],
        source_prefetch_thread=None,
        hot_references={},
        wal=None,
        telemetry_futures=[],
        pool=SimpleNamespace(close=pool_close),
        archive=Archive(),
        publisher=SimpleNamespace(shutdown=lambda **kw: called.append("publisher")),
        header_checks=SimpleNamespace(shutdown=lambda **kw: called.append("header_checks")),
        telemetry=SimpleNamespace(shutdown=lambda **kw: called.append("telemetry")),
        checkpoint_encoder=SimpleNamespace(shutdown=lambda **kw: called.append("encoder")),
        checkpoints=SimpleNamespace(__exit__=lambda *args: called.append("checkpoints")),
        input_preparer=SimpleNamespace(shutdown=lambda **kw: called.append("preparer")),
    )
    with pytest.raises(RuntimeError, match="monitor still running"):
        SegmentLearner.close(owner)
    assert set(called) == {
        "pool",
        "preparer",
        "archive",
        "checkpoints",
        "encoder",
        "telemetry",
        "publisher",
        "header_checks",
    }


def test_operating_budget_restores_consumed_time_from_CP_without_reset(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    config = dataclasses.replace(
        config,
        wall_seconds=600,
        deadline_at=time.time() + 7200,
        operating_budget={"id": "ops-test", "total_seconds": 600, "consumed_seconds": 120},
    )
    owner = SegmentLearner(config, tmp_path / "owner", io)
    owner.active_training_seconds = 30
    owner.checkpoint(final=True)
    record, cp = read_cp(owner)
    assert cp.config["operating_budget"]["consumed_seconds"] == 150
    owner.close()
    cold_peer = {"kind": "local", "directory": str(tmp_path / "cold")}
    cold = Exchange(cold_peer, config.run)
    copy_artifact(
        owner.exchange.store, cold.store, record["checkpoint_artifact_id"], scratch=tmp_path
    )
    request = dataclasses.replace(
        config,
        peer=cold_peer,
        start_package=None,
        resume_checkpoint=record["checkpoint_artifact_id"],
        deadline_at=None,
        operating_budget=None,
        segments=dataclasses.replace(config.segments, training_generation="g-cold-budget"),
    )
    replacement = SegmentLearner(request, tmp_path / "replacement", io)
    try:
        assert replacement.config.operating_budget["consumed_seconds"] == 150
        assert replacement.config.wall_seconds == 450
        assert replacement.deadline_at == config.deadline_at
        assert replacement._learning_state().config["operating_budget"]["consumed_seconds"] == 150
    finally:
        replacement.close()
    with pytest.raises(ValueError, match="checkpoint_operating_budget_cannot_be_reset"):
        SegmentLearner(
            dataclasses.replace(
                request,
                operating_budget={
                    "id": "different-budget",
                    "total_seconds": 600,
                    "consumed_seconds": 0,
                },
            ),
            tmp_path / "rejected",
            io,
        )


def test_terminal_actor_releases_capacity_but_preserves_original_ACKs_and_no_labels(
    tmp_path, monkeypatch
):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    base = {
        "run": config.run,
        "training_generation": owner.generation,
        "actor": "dead",
        "episode": "e",
    }
    try:
        opened = owner.control({**base, "operation": "episode_open"})
        grant = owner.control({**base, "operation": "begin", "previous_lease": None})
        assert grant["lease"] in owner.queue.leases
        proof = {"actor": "dead", "job_id": "123", "terminal": False, "state": "RUNNING"}
        with pytest.raises(ValueError, match="exact_terminal_actor_allocation_required"):
            owner.retire_actor("dead", proof)
        assert grant["lease"] in owner.queue.leases
        result = owner.retire_actor("dead", {**proof, "terminal": True, "state": "FAILED"})
        assert result["unresolved_episodes"] == ["e"] and result["new_games"] == 0
        assert not owner.queue.leases and not owner.actor_episodes
        assert not owner.terminal.groups and not owner.hp.groups
        assert owner.control({**base, "operation": "episode_open"}) == opened
        assert owner.control({**base, "episode": "new", "operation": "episode_open"})["stopped"]
        assert owner.control({**base, "actor": "replacement", "operation": "episode_open"})[
            "admitted"
        ]
    finally:
        cleanup(owner)


def test_learner_publishes_actual_failed_and_finished_events(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    owner.run(initialize_only=True)
    assert owner.exchange.latest_event()["kind"] == "finished"
    (tmp_path / "second").mkdir()
    second_config, io = configuration(tmp_path / "second", monkeypatch)
    second = SegmentLearner(second_config, tmp_path / "second/owner", io)

    def failed():
        raise BoundaryError("rl_learner", "injected_preparation_failure")

    monkeypatch.setattr(second, "_prepare", failed)
    with pytest.raises(BoundaryError, match="injected_preparation_failure"):
        second.run()
    event = second.exchange.latest_event()
    assert event["kind"] == "failed" and event["error_code"] == "injected_preparation_failure"


def test_explicit_completed_window_link_preserves_learning_state_with_new_budget(
    tmp_path, monkeypatch
):
    config, io = configuration(tmp_path, monkeypatch)
    old_deadline = time.time()+7200
    config = dataclasses.replace(config, deadline_at=old_deadline,
        operating_budget={'id': 'h1-window', 'total_seconds': 3600, 'consumed_seconds': 0})
    owner = SegmentLearner(config, tmp_path/'h1-owner', io)
    owner.active_training_seconds = 3600
    owner.checkpoint(final=True)
    record, cp = read_cp(owner)
    owner.close()
    parent = {'checkpoint_artifact_id': record['checkpoint_artifact_id'],
              'operating_budget': cp.config['operating_budget'], 'deadline_at': old_deadline}
    request = dataclasses.replace(config, start_package=None,
        resume_checkpoint=record['checkpoint_artifact_id'], deadline_at=old_deadline+72000,
        operating_budget={'id': 'long-window', 'total_seconds': 72000, 'consumed_seconds': 0},
        operating_budget_parent=parent,
        segments=dataclasses.replace(config.segments, training_generation='g-long'))
    replacement = SegmentLearner(request, tmp_path/'long-owner', io)
    try:
        restored = replacement._learning_state()
        assert_tree(restored.weights, cp.weights)
        assert_tree(restored.optimizer, cp.optimizer)
        assert_tree(restored.ema, cp.ema)
        assert restored.counters.updates == cp.counters.updates
        assert restored.counters.datasets == cp.counters.datasets
        assert restored.counters.first_use == cp.counters.first_use
        assert restored.counters.first_use_multi == cp.counters.first_use_multi
        assert restored.counters.mu_version == cp.counters.mu_version+1
        assert restored.counters.published_updates == cp.counters.updates
        assert_tree(restored.rng.torch_rng, cp.rng.torch_rng)
        assert restored.rng.learner_random == cp.rng.learner_random
        assert replacement.config.operating_budget['total_seconds'] == 72000
        assert cp.config['operating_budget']['consumed_seconds'] == 3600
        assert restored.config['operating_budget_parent'] == parent
    finally:
        replacement.close()
    # Review #388 M1: the window's first attempt died before its first CP, so the next
    # one re-enters from the same parent carrying the seconds already charged here.
    reentry = SegmentLearner(dataclasses.replace(request,
        operating_budget={'id': 'long-window', 'total_seconds': 72000, 'consumed_seconds': 600},
        segments=dataclasses.replace(config.segments, training_generation='g-long-2')),
        tmp_path/'reentry-owner', io)
    try:
        assert reentry.config.operating_budget == {
            'id': 'long-window', 'total_seconds': 72000, 'consumed_seconds': 600}
        assert reentry.config.wall_seconds <= 72000 - 600
        assert reentry._learning_state().counters.updates == cp.counters.updates
    finally:
        reentry.close()
    with pytest.raises(ValueError, match='cannot_be_reset'):
        SegmentLearner(dataclasses.replace(request, operating_budget_parent={**parent,
            'checkpoint_artifact_id': '0'*64}), tmp_path/'bad-window', io)


@pytest.mark.parametrize('failure_phase', [None, 'run', 'close'])
def test_binary_terminal_event_is_durable_before_wal_closes(tmp_path, monkeypatch, failure_phase):
    """Real binary owner/WAL: both terminal paths append before close fences writes."""
    from spireagent.rl_local_wal import LocalControlWAL
    from stpd.p1.local_inference import AUTH_ENV

    config, io = configuration(tmp_path, monkeypatch)
    monkeypatch.setenv(AUTH_ENV, 'ab' * 32)
    io = dataclasses.replace(io, dataplane={
        'lanes': 2, 'root': str(tmp_path / 'live'), 'source_slots': 8,
        'prepare_slots': 2, 'source_bytes': 16 * 1024**2,
        'preparation_workers': 1, 'assembly_workers': 1,
    })
    owner = SegmentLearner(config, tmp_path / 'owner', io)
    journal = owner.live_work / 'control.sqlite'
    failed = failure_phase is not None
    if failure_phase == 'close':
        original_close = owner.pool.close
        def injected_close_failure():
            original_close()
            raise RuntimeError('bounded original run failure')
        monkeypatch.setattr(owner.pool, 'close', injected_close_failure)
    if failure_phase == 'run':
        def injected_failure(*, wait=False):
            raise RuntimeError('bounded original run failure')
        monkeypatch.setattr(owner, '_collect_labels', injected_failure)
    if failed:
        with pytest.raises(RuntimeError, match='bounded original run failure'):
            owner.run(initialize_only=True)
    else:
        owner.run(initialize_only=True)
    assert owner.wal.closed
    import json
    final_snapshot = json.loads((owner.work / 'control-wal-final.json').read_bytes())
    assert final_snapshot['run'] == config.run
    assert final_snapshot['training_generation'] == owner.generation
    reader = LocalControlWAL(journal, run=config.run, generation=owner.generation)
    try:
        events = [row['request'] for row in reader.records_since(0) if row['kind'] == 'event']
    finally:
        reader.close()
    expected = 'failed' if failed else 'finished'
    assert final_snapshot['events'][-1]['request'] == events[-1]
    assert events[-1]['kind'] == expected
    assert sum(event['kind'] == expected for event in events) == 1
    if failed:
        assert events[-1]['error_type'] == 'RuntimeError'
        assert not any(event['kind'] == 'finished' for event in events)
    assert not (owner.work / 'failed-event-unpublished.json').exists()


@pytest.mark.skipif(os.name != 'posix', reason='real publisher process failure')
def test_final_publication_process_failure_preserves_last_complete_SGD_checkpoint(
        tmp_path, monkeypatch):
    import signal
    from concurrent.futures.process import BrokenProcessPool

    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / 'owner', io)
    deliver(owner, actor='a')
    deliver(owner, actor='b')
    assert owner._prepare()
    assert owner._step()['stepped']
    assert owner.updates == 1 and owner.published_updates == 0 and owner.active is not None
    # Kill only this test's actual CPU publisher, after initialization and SGD.
    process = next(iter(owner.publisher._processes.values()))
    os.kill(process.pid, signal.SIGKILL)
    process.join(timeout=5)
    assert not process.is_alive()
    with pytest.raises(BrokenProcessPool):
        owner.publisher.submit(os.getpid).result(timeout=5)
    (owner.work / 'STOP').touch()
    with pytest.raises(BrokenProcessPool):
        owner.run()
    record, cp = read_cp(owner)
    assert cp.counters.updates == cp.counters.first_use == 1
    assert cp.counters.published_updates == 0
    assert_tree(cp.weights, owner.model.state_dict())
    assert_tree(cp.optimizer, owner.optimizer.state_dict())
    failed = [event for event in owner.exchange.event_records() if event['kind'] == 'failed']
    assert len(failed) == 1 and failed[0]['error_type'] == 'BrokenProcessPool'
    assert failed[0]['finalization_failure']['stage'] == 'final_model_publication'
    assert failed[0]['finalization_failure']['diagnostic']['exception_type'] == 'BrokenProcessPool'
    assert not [event for event in owner.exchange.event_records() if event['kind'] == 'finished']
    assert owner.publisher._shutdown_thread
    cold_peer = {'kind': 'local', 'directory': str(tmp_path / 'cold')}
    cold = Exchange(cold_peer, config.run)
    copy_artifact(
        owner.exchange.store, cold.store, record['checkpoint_artifact_id'], scratch=tmp_path)
    replacement = SegmentLearner(dataclasses.replace(config, peer=cold_peer, start_package=None,
        resume_checkpoint=record['checkpoint_artifact_id'], segments=dataclasses.replace(
            config.segments, training_generation='g-after-publisher-failure')),
        tmp_path / 'replacement', io)
    try:
        assert_tree(replacement.model.state_dict(), cp.weights)
        assert_tree(replacement.optimizer.state_dict(), cp.optimizer)
        assert_tree(replacement.ema, cp.ema)
        assert replacement.updates == replacement.first_use == 1
        deliver(replacement, actor='fresh-c')
        deliver(replacement, actor='fresh-d')
        assert replacement._prepare() and replacement._step()['stepped']
        assert replacement.updates == 2
    finally:
        replacement.close()


def test_drain_keeps_listener_until_actor_final_lifecycle_work_finishes(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / 'owner', io)
    owner.actor_allocation_pending = frozenset({'a'})
    observed = []
    original_close = owner.pool.close
    def close_after_actor():
        assert not owner.actor_allocation_pending
        observed.append('listener_closed')
        return original_close()
    monkeypatch.setattr(owner.pool, 'close', close_after_actor)
    def actor_completed():
        observed.append('actor_final_events_and_evidence_copy_finished')
        owner.actor_allocation_pending = frozenset()
    owner.run(initialize_only=True, on_tick=actor_completed)
    assert observed == ['actor_final_events_and_evidence_copy_finished', 'listener_closed']


def test_actor_allocation_drain_deadline_is_failed_with_pending_names(tmp_path, monkeypatch):
    import itertools
    import json

    import stpd.rl.segment_learner as module

    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / 'owner', io)
    owner.actor_allocation_pending = frozenset({'audit-still-running'})
    clock = itertools.count(0, 1000)
    real = module.time
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: next(clock),
        perf_counter=real.perf_counter, time=real.time))
    with pytest.raises(BoundaryError, match='unresolved_actor_allocations_after_drain'):
        owner.run(initialize_only=True)
    summary = json.loads((owner.work / 'learner-summary.json').read_bytes())
    assert summary['unresolved_actor_allocations'] == ['audit-still-running']
    events = owner.exchange.event_records()
    assert events[-1]['kind'] == 'failed'
    assert not any(e['kind'] == 'finished' for e in events)


@pytest.mark.parametrize("original_fails", [False, True])
def test_resubmitted_episode_end_waits_for_inflight_original(original_fails) -> None:
    gate, started, calls, receipts = threading.Event(), threading.Event(), [], {}

    class Owner:
        condition = threading.Condition()
        episode_ends: dict = {}
        _end_episode_once_only = SegmentLearner._end_episode_once_only

        def _receipt(self, request):
            return receipts.get(request["episode"])

        def _end_episode(self, request):
            calls.append(request)
            if len(calls) == 1:
                started.set()
                assert gate.wait(2)
                if original_fails:
                    raise ValueError("original_failed_without_fact")
            receipts[request["episode"]] = {"run_id": request["run_id"]}
            return receipts[request["episode"]]

    owner, request = Owner(), {"actor": "a", "episode": "e", "run_id": "e"}
    results, errors = [], []

    def call():
        try:
            results.append(owner._end_episode_once_only(request))
        except ValueError as error:
            errors.append(str(error))

    first = threading.Thread(target=call)
    first.start()
    assert started.wait(2)
    second = threading.Thread(target=call)
    second.start()
    time.sleep(0.05)
    assert len(calls) == 1  # the reconnect resubmission never runs a second validation
    gate.set()
    first.join(2)
    second.join(2)
    # A failed original leaves no fact, so the waiting resubmission validates once itself.
    assert results == [{"run_id": "e"}] * (1 if original_fails else 2)
    assert errors == (["original_failed_without_fact"] if original_fails else [])
    assert len(calls) == (2 if original_fails else 1) and not owner.episode_ends
