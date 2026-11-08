"""Per-node unknown accounting: future placement only, never retry or recount."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_friday_watch_segments as segments
from test_friday_watch_segments import attempt, event

FIXTURE = Path(__file__).parent / "fixtures/friday_c11_node_unknown_replay.json"


@pytest.fixture
def watch():
    return segments.original_watch_fixture.__wrapped__()


@pytest.fixture
def owner(watch, tmp_path):
    value = segments.owner.__wrapped__(watch, tmp_path)
    value.config["node_unknown_exclusion"] = {}
    value.config["exclude_nodes"] = ["smp-9-8"]
    return value


def settings(watch, **values):
    return watch.node_exclusion_settings({"node_unknown_exclusion": values})


def test_settings_are_explicit_opt_in_with_bounded_values(watch):
    assert watch.node_exclusion_settings({}) is None
    assert settings(watch) == watch.NODE_EXCLUSION_DEFAULTS
    assert settings(watch, max_nodes=0)["max_nodes"] == 0
    for bad in ({"unknown": 1}, {"min_attempts": 31}, {"node_fraction": 0},
                {"fleet_median_max": 0.25}, {"window_attempts": 9}, {"max_nodes": 1.0}):
        with pytest.raises(ValueError, match="explicit_node_unknown_exclusion_settings"):
            settings(watch, **bad)
    with pytest.raises(ValueError, match="explicit_node_unknown_exclusion_settings"):
        watch.node_exclusion_settings({"node_unknown_exclusion": True})


def test_threshold_needs_minimum_sample_and_healthy_fleet(watch):
    rules = settings(watch)
    healthy = {f"ok-{i}": [0] * 29 + [1] for i in range(5)}
    # 19 closes, all unknown: below the minimum sample, no decision yet.
    assert watch.node_exclusion_decisions({**healthy, "bad": [1] * 19}, {}, rules) == []
    # 20 closes at 25%: excluded; one fewer unknown stays.
    [decision] = watch.node_exclusion_decisions(
        {**healthy, "bad": [1] * 5 + [0] * 15}, {}, rules)
    assert decision["kind"] == "node_unknown_exclusion" and decision["node"] == "bad"
    assert decision["window_unknown"] == 5 and decision["window_attempts"] == 20
    assert watch.node_exclusion_decisions({**healthy, "bad": [1] * 4 + [0] * 16}, {}, rules) == []
    # Fleet-wide fault: every node bad -> withheld, the global stop owns it.
    fleet = {f"n-{i}": [1] * 10 + [0] * 20 for i in range(6)}
    decisions = watch.node_exclusion_decisions(fleet, {}, rules)
    assert {d["kind"] for d in decisions} == {"node_exclusion_withheld_fleet_wide"}
    # No other qualifying node to compare with -> withheld, never guessed.
    [alone] = watch.node_exclusion_decisions({"bad": [1] * 30}, {}, rules)
    assert alone["kind"] == "node_exclusion_withheld_fleet_wide"
    assert alone["fleet_median_fraction"] is None


def test_cap_falls_back_to_global_stop_and_logs(watch):
    rules = settings(watch, max_nodes=2)
    windows = {**{f"ok-{i}": [0] * 30 for i in range(5)},
               **{f"bad-{i}": [1] * 30 for i in range(3)}}
    decisions = watch.node_exclusion_decisions(windows, {}, rules)
    assert [(d["kind"], d["node"]) for d in decisions] == [
        ("node_unknown_exclusion", "bad-0"), ("node_unknown_exclusion", "bad-1"),
        ("node_exclusion_cap_reached", "bad-2")]
    assert decisions[-1]["auto_excluded"] == ["bad-0", "bad-1"]


def closes(owner, actor, statuses, prefix):
    event(owner, "actor_games_closed", actor=actor, games=[
        {"intent_id": f"{prefix}-{i}", "seed": f"{prefix}-s{i}", "status": status,
         "world_mutation_offered": True, "training_generation": "g-one"}
        for i, status in enumerate(statuses)])


def wire(owner, watch, monkeypatch, tmp_path, accounting):
    rl = SimpleNamespace(NAME=__import__("re").compile(r"^[a-z0-9][a-z0-9-]{0,62}$"),
                         actor_accounting=lambda path, with_node=False: accounting(with_node),
                         prepare=lambda *a, **k: None, submissions=[])
    rl.submit = lambda path, **kwargs: rl.submissions.append(kwargs)
    monkeypatch.setitem(sys.modules, "rl", rl)
    signals = []
    owner.signal_once = lambda job: signals.append(job) or {"state": "response_received"}
    monkeypatch.setattr(watch, "hashed", lambda path: "sha")
    monkeypatch.setattr(watch, "save", lambda *a, **k: None)
    return rl, signals


def test_bad_node_is_excluded_signalled_and_replaced_without_recounting(
        owner, watch, monkeypatch, tmp_path):
    value = attempt(owner)
    value["root"] = str(tmp_path / "attempt")
    owner.config.update(execution=str(tmp_path / "execution.json"),
                        runtime_source=str(tmp_path / "runtime"),
                        unknown_stop_fraction=0.5)  # isolate the node rule from the 5% backstop
    placement = {"good-0": "smp-9-2", "good-1": "smp-9-4", "bad-0": "smp-9-20",
                 "bad-1": "smp-9-20"}
    for actor in ("good-0", "good-1"):
        closes(owner, actor, ["finished"] * 25, actor)
    closes(owner, "bad-0", ["unknown"] * 6 + ["finished"] * 4, "bad-0")
    closes(owner, "bad-1", ["unknown"] * 7 + ["finished"] * 3, "bad-1")
    owner.collect_events()
    before = dict(owner.state["health"])
    terminal = {"bad-1"}

    def accounting(with_node):
        assert with_node is True
        return [{"actor": a, "job_id": f"9{i}", "array_job_id": f"77_{i}", "state": "RUNNING",
                 "exit_code": "0:0", "terminal": a in terminal, "node": n}
                for i, (a, n) in enumerate(placement.items())]

    rl, signals = wire(owner, watch, monkeypatch, tmp_path, accounting)
    owner.replace_actors(value)
    nodes = owner.state["node_exclusion"]
    assert list(nodes["excluded"]) == ["smp-9-20"]
    record = nodes["excluded"]["smp-9-20"]
    assert (record["window_unknown"], record["window_attempts"]) == (13, 20)
    assert record["total_attempts"] == 20 and record["fleet_median_fraction"] == 0
    assert [e["kind"] for e in nodes["events"]] == ["node_unknown_exclusion"]
    # Only the running element on the bad node gets the cooperative USR1.
    assert signals == ["77_2"] and record["signalled_actors"] == ["bad-0"]
    # Terminal capacity is replaced with configured + automatic exclusions.
    assert rl.submissions[-1]["exclude_nodes"] == ["smp-9-8", "smp-9-20"]
    assert owner.actor_exclude_nodes() == ["smp-9-8", "smp-9-20"]
    # Original closes, unknown counts and the global gate inputs are unchanged.
    assert owner.state["health"]["known_unknown"] == before["known_unknown"] == 13
    assert sorted(owner.state["unknown_intents"]) == sorted(
        [f"bad-0-{i}" for i in range(6)] + [f"bad-1-{i}" for i in range(7)])
    # A later tick neither re-signals nor re-logs.
    owner.replace_actors(value)
    assert signals == ["77_2"] and len(nodes["events"]) == 1


def test_closes_wait_for_placement_and_feature_off_is_old_behaviour(
        owner, watch, monkeypatch, tmp_path):
    value = attempt(owner)
    closes(owner, "late", ["unknown"] * 25, "late")
    owner.collect_events()
    rows = [{"actor": "late", "job_id": "1", "array_job_id": "5_0", "state": "PENDING",
             "exit_code": "0:0", "terminal": False, "node": None}]
    wire(owner, watch, monkeypatch, tmp_path, lambda with_node: rows)
    owner.replace_actors(value)
    assert len(owner.state["node_exclusion"]["pending"]) == 25
    assert owner.state["node_exclusion"].get("windows") == {}
    del owner.config["node_unknown_exclusion"]
    calls = []
    wire(owner, watch, monkeypatch, tmp_path, lambda with_node: calls.append(with_node) or [])
    owner.replace_actors(value)
    assert calls == [False] and owner.actor_exclude_nodes() == ["smp-9-8"]


def test_c11_replay_excludes_both_bad_nodes_well_before_the_global_stop(watch):
    """RL4.2 window c11 stopped at 03:42 on 346/5174 unknown, mostly two nodes."""
    fixture = json.loads(FIXTURE.read_bytes())
    stop = fixture["global_stop"]["epoch"] - fixture["t0_epoch"]
    rules = settings(watch)
    windows: dict[str, list[int]] = {}
    excluded: dict[str, int] = {}
    healthy_peak = 0.0
    for at, node, ok, unknown in fixture["batches"]:
        if at >= stop:
            break
        if node is None:
            continue
        windows[node] = (windows.get(node, []) + [0] * ok + [1] * unknown)[
            -rules["window_attempts"]:]
        if node not in {"smp-9-20", "smp-9-7"} and len(windows[node]) >= rules["min_attempts"]:
            healthy_peak = max(healthy_peak, sum(windows[node]) / len(windows[node]))
        for decision in watch.node_exclusion_decisions(windows, excluded, rules):
            assert decision["kind"] == "node_unknown_exclusion", decision
            excluded[decision["node"]] = at
    assert set(excluded) == {"smp-9-20", "smp-9-7"}
    assert stop - excluded["smp-9-20"] >= 30 * 60
    assert stop - excluded["smp-9-7"] >= 10 * 60
    # Eight healthy nodes stay far below the 25% line in every window.
    assert len(windows) == 10 and healthy_peak <= 0.07


def test_readmitted_through_continuation_then_excluded_again_by_account_nodes(
        owner, watch, monkeypatch, tmp_path):
    """Review of #400: re-admission runs in the same-window continuation's __init__, a
    request for a node that is not auto-excluded leaves a typed record, an in-place restart
    does not repeat it, and a node that goes bad again is excluded again by #386."""
    attempt(owner)
    excluded_at = time.time() - 900
    owner.state["node_exclusion"] = {
        "actor_nodes": {"old-bad": "smp-9-20"},
        "excluded": {"smp-9-20": {"kind": "node_unknown_exclusion", "at": excluded_at,
                                  "signalled_actors": []}},
        "windows": {"smp-9-20": [1] * 20}, "logged": {"smp-9-20": "node_unknown_exclusion"}}
    owner.persist()
    reference = {"path": str(owner.state_path), "sha256": watch.hashed(owner.state_path)}
    config = {**owner.config, "previous_watch_state": reference,
              "readmit_nodes": [{"node": "smp-9-20", "evidence": "node check"},
                                {"node": "smp-9-8", "evidence": "node check"}]}
    resumed = watch.SegmentWatcher(config, tmp_path / "watch-next")
    nodes = resumed.state["node_exclusion"]
    assert nodes["excluded"] == {} and "smp-9-20" not in nodes["windows"]
    [period] = nodes["readmitted"]["smp-9-20"]
    assert period["excluded_at"] == excluded_at and period["evidence"] == "node check"
    assert [(e["kind"], e["node"]) for e in nodes["events"]] == [
        ("node_readmitted", "smp-9-20"), ("node_readmission_skipped", "smp-9-8")]
    assert nodes["events"][1]["reason"] == "not_auto_excluded_in_inherited_state"
    pinned = json.loads((resumed.work / "previous-watch-state.json").read_bytes())
    assert "smp-9-20" in pinned["node_exclusion"]["excluded"]  # original kept
    resumed.persist()
    again = watch.SegmentWatcher(config, tmp_path / "watch-next")  # in-place restart
    assert len(again.state["node_exclusion"]["events"]) == 2
    assert again.actor_exclude_nodes() == ["smp-9-8"]

    # After re-admission the node's new closes count; #386 excludes it again.
    resumed.config.update(execution=str(tmp_path / "execution.json"),
                          runtime_source=str(tmp_path / "runtime"), unknown_stop_fraction=0.5)
    placement = {"good-0": "smp-9-2", "good-1": "smp-9-4", "new-bad": "smp-9-20"}
    for actor in ("good-0", "good-1"):
        closes(resumed, actor, ["finished"] * 25, actor)
    closes(resumed, "new-bad", ["unknown"] * 13 + ["finished"] * 7, "new-bad")
    resumed.collect_events()

    def accounting(with_node):
        return [{"actor": a, "job_id": f"9{i}", "array_job_id": f"78_{i}", "state": "RUNNING",
                 "exit_code": "0:0", "terminal": False, "node": n}
                for i, (a, n) in enumerate(placement.items())]

    _, signals = wire(resumed, watch, monkeypatch, tmp_path, accounting)
    resumed.replace_actors(resumed.state["attempts"][resumed.state["active"]])
    record = nodes["excluded"]["smp-9-20"]
    assert record["at"] >= period["readmitted_at"]
    assert (record["window_unknown"], record["window_attempts"]) == (13, 20)
    assert nodes["events"][-1]["kind"] == "node_unknown_exclusion"
    assert signals == ["78_2"]
    assert resumed.actor_exclude_nodes() == ["smp-9-8", "smp-9-20"]


@pytest.mark.parametrize("stage", ["failed", "submission_intent", "running"])
def test_continuation_closes_only_an_inherited_failed_evaluation(
        owner, watch, tmp_path, stage):
    """c13e 11-08: c13d's eval-0005 preparation was judged failed (plan not yet visible),
    the continuation inherited the sticky pending and scheduled no evaluation at all. A
    failed preparation offered no World, so it becomes one skipped record; an uncertain
    submission or a running job is never closed or resubmitted."""
    attempt(owner)
    root = str(tmp_path / "watch-prev/evaluation-0005")
    owner.state["pending"] = {"root": root, "stage": stage, "due_seconds": 28800,
                              "job_id": "1445040"}
    owner.state.update(training_seconds=50500.0, next_evaluation_seconds=28800)
    owner.persist()
    reference = {"path": str(owner.state_path), "sha256": watch.hashed(owner.state_path)}
    config = {**owner.config, "previous_watch_state": reference}
    resumed = watch.SegmentWatcher(config, tmp_path / "watch-next")
    pinned = json.loads((resumed.work / "previous-watch-state.json").read_bytes())
    assert pinned["pending"]["stage"] == stage  # original kept
    if stage == "failed":
        assert resumed.state["pending"] is None
        record = resumed.state["evaluations"][-1]
        assert record["skipped"] == "evaluation_preparation_failed_no_World_offer"
        assert record["root"] == root and record["at_training_seconds"] == 28800
        assert record["stage"] == "preparation_failed"
        # One evaluation at the latest passed 2 h point (50400), no catch-up of 36000/43200.
        assert resumed.state["next_evaluation_seconds"] == 50400
    else:
        assert resumed.state["pending"]["stage"] == stage and not resumed.state["evaluations"]
        assert resumed.state["next_evaluation_seconds"] == 28800


# ---------------------------------------------------------------- OPS-2 in-window readmission
def test_node_state_failure_counts_all_but_game_side_categories(watch):
    """c13f: 823/826 bad-node unknowns are runtime_execution_or_release_unknown ->
    unclassified; they must count. Only categories that follow the game do not."""
    assert watch.node_state_failure("unknown", {"category": "unclassified"})
    assert watch.node_state_failure("unknown", None)
    assert watch.node_state_failure("save_failed", {"category": "infrastructure"})
    assert not watch.node_state_failure("unknown", {"category": "environment_gap"})
    assert not watch.node_state_failure("unknown", {"category": "wall_limit"})
    assert not watch.node_state_failure("finished", None)


def test_readmission_and_slow_settings_are_bounded(watch):
    rules = settings(watch)
    assert rules["cooldown_seconds"] == [7200, 14400, 28800] and rules["canary_lanes"] == 2
    assert watch.node_cooldown(rules, 1) == 7200 and watch.node_cooldown(rules, 3) == 28800
    assert watch.node_cooldown(rules, 4) is None  # past the list: out for the window
    for bad in ({"cooldown_seconds": [14400, 7200]}, {"cooldown_seconds": []},
                {"cooldown_seconds": [60]}, {"canary_lanes": 5}, {"canary_games": 0},
                {"slow_ratio": 1}, {"slow_min_games": 2}):
        with pytest.raises(ValueError, match="explicit_node_unknown_exclusion_settings"):
            settings(watch, **bad)


def test_slow_node_is_its_own_throughput_verdict(watch):
    rules = settings(watch)
    fleet = {f"ok-{i}": [600.0 + i] * 20 for i in range(4)}
    [slow] = watch.node_slow_decisions({**fleet, "slow": [1500.0] * 20}, {}, rules)
    assert slow["kind"] == "node_slow" and slow["node"] == "slow" and slow["ratio"] > 2
    assert watch.node_slow_decisions({**fleet, "slow": [1100.0] * 20}, {}, rules) == []
    assert watch.node_slow_decisions({**fleet, "slow": [1500.0] * 19}, {}, rules) == []
    # Fewer than three other qualifying nodes: no comparison, no verdict.
    assert watch.node_slow_decisions({"ok-0": [600.0] * 20, "ok-1": [600.0] * 20,
                                      "slow": [1500.0] * 20}, {}, rules) == []
    assert watch.node_slow_decisions({**fleet, "slow": [1500.0] * 20}, {"slow": {}},
                                     rules) == []


def canary_owner(owner, watch, monkeypatch, tmp_path, placement, terminal=()):
    value = attempt(owner)
    value["root"] = str(tmp_path / "attempt")
    owner.config.update(execution=str(tmp_path / "execution.json"),
                        runtime_source=str(tmp_path / "runtime"), unknown_stop_fraction=0.5)

    def accounting(with_node):
        return [{"actor": a, "job_id": f"9{i}", "array_job_id": f"77_{i}", "state": "RUNNING",
                 "exit_code": "0:0", "terminal": a in terminal, "node": n}
                for i, (a, n) in enumerate(placement.items())]
    rl, signals = wire(owner, watch, monkeypatch, tmp_path, accounting)
    prepared = []
    rl.prepare = lambda *a, **k: prepared.append((a, k))
    rl.load_plan = lambda path: {"actors": [{"actor": f"canary-{len(prepared)}-a"},
                                            {"actor": f"canary-{len(prepared)}-b"}]}
    return value, rl, signals, prepared


def excluded_record(watch, at, strike=1, due=None):
    return {"kind": "node_unknown_exclusion", "at": at, "strike": strike,
            "cooldown_until": due, "signalled_actors": []}


def test_cooldown_then_canary_pass_readmits_and_releases_pending_arrays(
        owner, watch, monkeypatch, tmp_path):
    now = time.time()
    placement = {"good-0": "smp-9-2", "good-1": "smp-9-4", "good-2": "smp-9-5"}
    value, rl, signals, prepared = canary_owner(owner, watch, monkeypatch, tmp_path, placement)
    owner.state["node_exclusion"] = {
        "excluded": {"smp-9-20": excluded_record(watch, now - 3600, due=now + 3600)},
        "strikes": {"smp-9-20": 1}}
    owner.replace_actors(value)
    assert prepared == [] and rl.submissions == []  # still in its 2 h cooldown
    owner.state["node_exclusion"]["excluded"]["smp-9-20"]["cooldown_until"] = now - 1
    owner.replace_actors(value)
    [(args, kwargs)] = prepared
    assert kwargs["actors"] == 2 and "canary-smp-9-20-1-1" in kwargs["seed_namespace"]
    [submitted] = rl.submissions
    assert submitted["nodelist"] == ["smp-9-20"] and "exclude_nodes" not in submitted
    record = owner.state["node_exclusion"]["excluded"]["smp-9-20"]
    assert record["canary"]["actors"] == ["canary-1-a", "canary-1-b"]
    assert value["actor_plans"][-1].endswith("canary-smp-9-20-1-1/plan.json")
    assert owner.actor_exclude_nodes() == ["smp-9-8", "smp-9-20"]  # others still avoid it
    # Ten clean canary closes on the node re-admit it; nothing is retried or recounted.
    placement.update({"canary-1-a": "smp-9-20", "canary-1-b": "smp-9-20"})
    closes(owner, "canary-1-a", ["finished"] * 5, "ca")
    closes(owner, "canary-1-b", ["finished"] * 4 + ["saved"], "cb")
    owner.collect_events()
    released = []
    monkeypatch.setattr(watch, "read_published", lambda path: {"job_id": "77"})
    monkeypatch.setattr(watch.subprocess, "run", lambda command, **k: (
        released.append(command) or SimpleNamespace(returncode=0)))
    pending_rows = [{"actor": "late", "state": "PENDING", "terminal": False}]
    rl.actor_accounting = lambda path, with_node=False: (
        pending_rows if not with_node else [
            {"actor": a, "job_id": f"9{i}", "array_job_id": f"77_{i}", "state": "RUNNING",
             "exit_code": "0:0", "terminal": False, "node": n}
            for i, (a, n) in enumerate(placement.items())])
    owner.replace_actors(value)
    nodes = owner.state["node_exclusion"]
    assert "smp-9-20" not in nodes["excluded"]
    [period] = nodes["readmitted"]["smp-9-20"]
    assert period["evidence"]["kind"] == "canary" and period["evidence"]["closes"] == 10
    assert owner.actor_exclude_nodes() == ["smp-9-8"]
    assert released and released[0][:3] == ["scontrol", "update", "JobId=77"]
    assert released[0][3] == "ExcNodeList=smp-9-8"
    kinds = [e["kind"] for e in nodes["events"]]
    assert kinds == ["node_canary_started", "node_readmitted", "pending_exclusion_released"]
    assert signals == []  # canary actors are never signalled on a pass
    assert len(rl.submissions) == 1  # canary lanes are never replaced


def test_canary_failure_is_the_next_strike_and_past_the_list_stays_out(
        owner, watch, monkeypatch, tmp_path):
    now = time.time()
    placement = {"good-0": "smp-9-2"}
    value, rl, signals, prepared = canary_owner(owner, watch, monkeypatch, tmp_path, placement)
    owner.state["node_exclusion"] = {
        "excluded": {"smp-9-20": excluded_record(watch, now - 9000, due=now - 1)},
        "strikes": {"smp-9-20": 1}}
    owner.replace_actors(value)
    placement.update({"canary-1-a": "smp-9-20", "canary-1-b": "smp-9-20"})
    closes(owner, "canary-1-a", ["finished", "unknown"], "ca")
    owner.collect_events()
    owner.replace_actors(value)
    nodes = owner.state["node_exclusion"]
    record = nodes["excluded"]["smp-9-20"]
    assert record["canary"] is None and record["strike"] == 2
    assert record["cooldown_until"] == pytest.approx(time.time() + 14400, abs=60)
    assert record["canaries"][-1]["outcome"] == "failed"
    assert sorted(signals) == ["77_1", "77_2"]  # both canary lanes finish their game
    assert nodes["events"][-1]["kind"] == "node_canary_failed"
    # The original unknown stays counted for the global gate.
    assert "ca-1" in owner.state["unknown_intents"]
    # Canary lanes that end before their sample fail closed as well.
    record.update(cooldown_until=time.time() - 1)
    owner.replace_actors(value)
    assert record["canary"]["actors"] == ["canary-2-a", "canary-2-b"]
    rl.actor_accounting = lambda path, with_node=False: [
        {"actor": a, "job_id": "1", "array_job_id": "78_0", "state": "COMPLETED",
         "exit_code": "0:0", "terminal": True, "node": "smp-9-20"}
        for a in ("canary-2-a", "canary-2-b")]
    owner.replace_actors(value)
    assert record["canaries"][-1]["outcome"] == "lanes_ended_early" and record["strike"] == 3
    # Third strike exhausted (strike 4 has no cooldown): no more canaries this window.
    record.update(strike=4, cooldown_until=None)
    owner.replace_actors(value)
    assert len(prepared) == 2


def test_canary_waits_for_cpu_budget_and_unsubmitted_canary_looks_again(
        owner, watch, monkeypatch, tmp_path):
    now = time.time()
    value, rl, signals, prepared = canary_owner(owner, watch, monkeypatch, tmp_path,
                                                {"good-0": "smp-9-2"})
    owner.state["node_exclusion"] = {
        "excluded": {"smp-9-20": excluded_record(watch, now - 9000, due=now - 1)},
        "strikes": {"smp-9-20": 1}}
    owner.config.update(actors=467, cpus=32, cpu_budget=500)  # 467+32+2 > 500
    owner.replace_actors(value)
    record = owner.state["node_exclusion"]["excluded"]["smp-9-20"]
    assert record["canary_waiting"] == "cpu_budget" and prepared == []
    owner.config["actors"] = 400

    def refused(path, **kwargs):
        raise ValueError("Friday_fair_share_refused")
    rl.submit = refused
    owner.replace_actors(value)
    assert record["canary"] is None and record["canaries"][-1]["outcome"] == "not_submitted"
    assert record["cooldown_until"] == pytest.approx(time.time() + 1800, abs=60)
    assert value["actor_plans"] == ["actor-plan"]
    # After the intent exists the submission is uncertain: never resubmitted, raised.
    record["cooldown_until"] = now - 1

    def uncertain(path, **kwargs):
        (path.parent / "submission-intent.json").parent.mkdir(parents=True, exist_ok=True)
        (path.parent / "submission-intent.json").write_text("{}")
        raise ValueError("sbatch_failed")
    rl.submit = uncertain
    with pytest.raises(ValueError, match="sbatch_failed"):
        owner.replace_actors(value)


def test_max_nodes_bounds_concurrent_isolation_not_a_window_total(
        owner, watch, monkeypatch, tmp_path):
    rules = settings(watch, max_nodes=1)
    windows = {**{f"ok-{i}": [0] * 30 for i in range(5)}, "bad-a": [1] * 30}
    [first] = watch.node_exclusion_decisions(windows, {}, rules)
    assert first["kind"] == "node_unknown_exclusion" and first["node"] == "bad-a"
    windows["bad-b"] = [1] * 30
    capped = watch.node_exclusion_decisions(windows, {"bad-a": {}}, rules)
    assert [d["kind"] for d in capped] == ["node_exclusion_cap_reached"]
    # bad-a re-admitted (its window dropped): the cap frees for bad-b.
    del windows["bad-a"]
    [second] = watch.node_exclusion_decisions(windows, {}, rules)
    assert second["kind"] == "node_unknown_exclusion" and second["node"] == "bad-b"


def test_slow_node_gets_no_new_placements_and_no_signal(owner, watch, monkeypatch, tmp_path):
    placement = {f"a-{i}": f"smp-9-{i}" for i in range(5)}
    value, rl, signals, _ = canary_owner(owner, watch, monkeypatch, tmp_path, placement)
    for i in range(5):
        event(owner, "actor_games_closed", actor=f"a-{i}", games=[
            {"intent_id": f"g{i}-{j}", "seed": f"s{i}-{j}", "status": "finished",
             "elapsed_seconds": 3000.0 if i == 4 else 900.0, "world_mutation_offered": True,
             "training_generation": "g-one"} for j in range(20)])
    owner.collect_events()
    owner.replace_actors(value)
    nodes = owner.state["node_exclusion"]
    assert list(nodes["slow"]) == ["smp-9-4"] and nodes["excluded"] == {}
    assert owner.actor_exclude_nodes() == ["smp-9-8", "smp-9-4"]
    assert signals == []


def test_inherited_exclusions_are_seeded_once_into_a_new_work_dir(owner, watch, tmp_path):
    now = time.time()
    config = {**owner.config, "inherited_node_exclusions": {
        "smp-9-20": {"kind": "node_unknown_exclusion", "at": now - 600, "strike": 2,
                     "cooldown_until": now + 13800, "inherited": True}}}
    seeded = watch.SegmentWatcher(config, tmp_path / "watch-c16")
    nodes = seeded.state["node_exclusion"]
    assert nodes["excluded"]["smp-9-20"]["strike"] == 2 and nodes["strikes"] == {"smp-9-20": 2}
    assert seeded.actor_exclude_nodes() == ["smp-9-8", "smp-9-20"]
    nodes["excluded"].pop("smp-9-20")  # re-admitted by its canary
    seeded.persist()
    again = watch.SegmentWatcher(config, tmp_path / "watch-c16")  # in-place restart
    assert again.state["node_exclusion"]["excluded"] == {}
