"""Normal operational facts, not gameplay or GPU qualification."""

from __future__ import annotations

import time
from contextlib import nullcontext

import pytest
from test_friday_watch import watch as original_watch_fixture


@pytest.fixture
def watch():
    return original_watch_fixture.__wrapped__()


@pytest.fixture
def owner(watch, tmp_path):
    config = {
        "run": "ops-watch-test",
        "budget_id": "ops-budget",
        "peer": {"kind": "local", "directory": str(tmp_path / "store")},
        "deadline_at": time.time() + 10800,
        "training_seconds": 72000,
        "actors": 1,
        "stall_seconds": 60,
    }
    return watch.SegmentWatcher(config, tmp_path / "watch")


def event(owner, kind, **fields):
    return owner.exchange.publish_event(kind, budget_id=owner.config["budget_id"], **fields)


def attempt(owner, *, budget_start=0):
    value = {
        "root": str(owner.work / "attempt"),
        "generation": "g-one",
        "checkpoint": {"updates": 362},
        "job_id": "learner",
        "actor_plans": ["actor-plan"],
        "budget_start_seconds": budget_start,
        "active_seconds": 0,
        "replaced": [],
    }
    owner.state["attempts"].append(value)
    owner.state["active"] = 0
    return value


def test_close_before_offer_is_one_world_and_never_reopens(owner):
    attempt(owner)
    event(
        owner,
        "actor_games_closed",
        actor="actor",
        games=[
            {
                "intent_id": "intent",
                "seed": "seed",
                "status": "finished",
                "world_mutation_offered": True,
                "training_generation": "g-one",
            }
        ],
    )
    owner.collect_events()
    event(
        owner,
        "actor_world_offered",
        actor="actor",
        intent_id="intent",
        seed="seed",
        training_generation="g-one",
    )
    owner.collect_events()
    assert owner.state["games_total"] == 1
    assert not owner.state["open_worlds"] and not owner.state["lost_world_offers"]
    owner.collect_events()
    assert owner.state["games_total"] == 1


def test_terminal_actor_before_delayed_offer_counts_lost_delivery_without_inventing_close(owner):
    attempt(owner)
    event(
        owner,
        "actor_retired",
        actor="actor",
        unresolved_episodes=["intent"],
        training_generation="g-one",
    )
    owner.collect_events()
    assert owner.state["games_total"] == 0
    assert owner.state["unresolved_admissions"] == ["intent"]
    event(
        owner,
        "actor_world_offered",
        actor="actor",
        intent_id="intent",
        seed="seed",
        training_generation="g-one",
    )
    owner.collect_events()
    assert owner.state["games_total"] == 1
    assert owner.state["lost_world_offers"] == ["intent"]
    assert owner.state["unknown_intents"] == []  # No fabricated original ledger result.
    assert owner.state["health"]["lost_offers"] == 1
    assert not owner.state["unresolved_admissions"] and not owner.state["open_worlds"]


def test_original_unknown_close_cannot_be_upgraded_by_later_close(owner):
    event(
        owner,
        "actor_games_closed",
        actor="actor",
        games=[
            {
                "intent_id": "intent",
                "seed": "seed",
                "status": "unknown",
                "world_mutation_offered": True,
            }
        ],
    )
    owner.collect_events()
    event(
        owner,
        "actor_games_closed",
        actor="actor",
        games=[
            {
                "intent_id": "intent",
                "seed": "seed",
                "status": "finished",
                "world_mutation_offered": True,
            }
        ],
    )
    with pytest.raises(ValueError, match="immutable_actor_close_changed"):
        owner.collect_events()


def test_fresh_heartbeat_does_not_hide_stalled_updates(owner, watch, monkeypatch):
    value = attempt(owner)
    event(owner, "training_started", training_generation="g-one", active_seconds=0, updates=362)
    owner.collect_events()
    began = value["last_progress_at"]
    monkeypatch.setattr(watch.time, "time", lambda: began + 3600)
    event(owner, "heartbeat", training_generation="g-one", active_seconds=3600, updates=362)
    owner.collect_segment_evaluation = lambda: None
    owner.schedule_segment_evaluation = lambda: None
    owner.retain_segment_checkpoints = lambda: None
    owner.offer_initial_actors = lambda _: None
    owner.replace_actors = lambda _: None
    stops = []
    owner.stop_attempt = lambda _, reason: stops.append(reason)
    monkeypatch.setattr(watch, "job_accounting", lambda _: {"terminal": False})
    owner.tick_segment()
    assert owner.state["stop"] == "learner_progress_stalled"
    assert stops == ["learner_progress_stalled"]


def test_recovered_budget_anchor_adds_new_window_once(owner):
    attempt(owner, budget_start=3600)
    owner.state["training_seconds"] = 3600
    event(owner, "training_window", training_generation="g-one", active_seconds=100, updates=363)
    owner.collect_events()
    assert owner.state["training_seconds"] == 3700
    owner.collect_events()
    assert owner.state["training_seconds"] == 3700


def test_named_diagnosed_finding_allows_new_owner_without_deleting_history(owner, watch, tmp_path):
    failed = event(owner, "integrity_failed", reason="sampled_original_audit_failed")
    owner.collect_events()
    assert owner.state["stop"] == "integrity_failed_requires_diagnosis"
    key = next(
        k
        for k in owner.exchange.blobs.keys(owner.exchange.prefix + "learner-events/")
        if owner.exchange.event_record(k)["kind"] == "integrity_failed"
    )
    config = {**owner.config, "acknowledged_integrity_events": {key: failed["record_sha256"]}}
    replacement = watch.SegmentWatcher(config, tmp_path / "replacement")
    replacement.collect_events()
    assert replacement.state["stop"] is None
    assert replacement.exchange.event_record(key) == failed
    config["acknowledged_integrity_events"][key] = "0" * 64
    rejected = watch.SegmentWatcher(config, tmp_path / "wrong-ack")
    rejected.collect_events()
    assert rejected.state["stop"] == "integrity_failed_requires_diagnosis"


def test_uncertain_sbatch_is_never_reoffered(owner, watch, tmp_path, monkeypatch):
    script = tmp_path / "worker.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    calls = []

    def unknown(command):
        calls.append(command)
        raise OSError("submission reply lost")

    monkeypatch.setattr(watch.scheduler, "user_submission_lock", nullcontext)
    monkeypatch.setattr(watch.scheduler, "output", unknown)
    with pytest.raises(OSError):
        owner.submit_cpu_step(script, tmp_path / "job", name="test", cpus=1, minutes=1, memory_gb=1)
    with pytest.raises(
        ValueError, match="original_submission_intent_requires_reconciliation_no_retry"
    ):
        owner.submit_cpu_step(script, tmp_path / "job", name="test", cpus=1, minutes=1, memory_gb=1)
    assert len(calls) == 1


def test_expired_absolute_deadline_prevents_even_evaluation_preparation(owner):
    owner.config.update(deadline_at=time.time() - 1, evaluation={"seconds": 7200})
    owner.state["training_seconds"] = 7200
    owner.state["stop"] = "run_budget_complete"
    owner.select_checkpoint = lambda: pytest.fail("no CP selection or new offer after deadline")
    owner.schedule_segment_evaluation()
    assert owner.state["pending"] is None


def test_previous_watch_state_must_bind_the_same_run_peer_and_budget(owner, watch, tmp_path):
    foreign = watch.SegmentWatcher({**owner.config, "run": "other-run"}, tmp_path / "foreign")
    foreign.state["attempts"] = [{"job_id": "foreign-job", "generation": "foreign-generation"}]
    foreign.state["active"] = 0
    foreign.persist()
    reference = {"path": str(foreign.state_path), "sha256": watch.hashed(foreign.state_path)}
    with pytest.raises(ValueError, match="previous_watch_run_peer_and_budget_identity_changed"):
        watch.SegmentWatcher(
            {**owner.config, "previous_watch_state": reference}, tmp_path / "rejected"
        )
    owner.persist()
    reference = {"path": str(owner.state_path), "sha256": watch.hashed(owner.state_path)}
    resumed = watch.SegmentWatcher(
        {**owner.config, "previous_watch_state": reference}, tmp_path / "new-work"
    )
    assert resumed.state["previous_config_sha256"] == owner.state["config_sha256"]
    assert resumed.state["configuration"]["path"] == str(resumed.work / "watch-config.json")
