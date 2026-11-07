"""Per-node unknown accounting: future placement only, never retry or recount."""

from __future__ import annotations

import json
import sys
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
