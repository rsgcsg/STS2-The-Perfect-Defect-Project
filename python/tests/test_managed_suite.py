from __future__ import annotations

import threading

import pytest

from stpd.evaluation.gates import PairedOutcome, paired_fixed_seed_report
from stpd.evaluation.managed_suite import SuiteLimits, run_suite, wilson_interval
from stpd.evaluation.suites import (
    load_suite,
    require_non_evaluation_seed,
    seed_membership,
    suite_names,
)


class View:
    @classmethod
    def from_snapshot(cls, snapshot):
        value = cls()
        value.action_ids = tuple(a["bound_action_id"] for a in snapshot["bound_actions"]["actions"])
        value.snapshot_id = snapshot["snapshot_id"]
        return value


def snapshot(terminal=False):
    return {
        "schema": "sts2.player-environment/snapshot-1",
        "snapshot_id": "s1",
        "status": "observed" if terminal else "interactive",
        "session": {"runtime_instance_id": "r1", "environment_fingerprint": "e1"},
        "persistent": {
            "content": {
                "run": {"floor": 5, "act": 1, "ascension": 0},
                "player": {"character_definition_id": "DEFECT"},
            }
        },
        "completeness": {"status": "complete", "missing": []},
        "interaction": {
            "kind": "game_over" if terminal else "combat_turn",
            "stage": "complete",
            "content_schema": "sts2.player-environment/surface/game_over-1",
            "content": {
                "surface": {"kind": "game_over", "stage": "complete", "victory": False},
                "context": {"kind": "terminal"},
            },
        },
        "bound_actions": {
            "status": "complete",
            "total_count": 0 if terminal else 2,
            "materialized_count": 0 if terminal else 2,
            "actions": [] if terminal else [{"bound_action_id": "a"}, {"bound_action_id": "b"}],
        },
    }


class Environment:
    instances = []
    behavior = "normal"

    def __init__(self, *, on_started):
        self.ready = {
            "exact_game": {"version": "v0.111.0"},
            "adapter_runtime_instance_id": "r1",
            "environment_fingerprint": "e1",
        }
        self.steps = []
        self.closed = threading.Event()
        self.observed = 0
        self.instances.append(self)
        on_started(self)

    def reset(self, seed):
        self.seed = seed
        return snapshot()

    def episode_identity(self):
        return {
            "episode_provenance": {
                "verdict": "provenance_pass",
                "requested_seed": self.seed,
                "actual_seed": self.seed,
            }
        }

    def observe(self):
        self.observed += 1
        if self.behavior == "idle_hang":
            self.closed.wait(1)
            raise RuntimeError("closed by deadline")
        return snapshot(bool(self.steps) and self.behavior not in {"stale", "budget"})

    def step(self, action_id, snapshot_id, request_id):
        self.steps.append(action_id)
        receipt = {
            "request_id": request_id,
            "action": {"bound_action_id": action_id},
            "delivery": "delivered",
        }
        if self.behavior == "unknown":
            receipt["delivery"] = "unknown"
        if self.behavior == "stale" and len(self.steps) == 1:
            receipt.update(
                delivery="not_delivered", reason_code="stale_snapshot", retry={"allowed": True}
            )
        if self.behavior == "mutation_hang":
            self.closed.wait(1)
            raise RuntimeError("missing response after offered step")
        return receipt

    def close(self, force=False):
        self.closed.set()

    def close_receipt(self):
        return {"driver_exit_code": 0, "cleanup_error": None, "forced_fallback": False}


def run(behavior, *, count=2, limits=None):
    Environment.instances = []
    Environment.behavior = behavior
    manifest = {**load_suite("smoke-20"), "seeds": load_suite("smoke-20")["seeds"][:count]}
    events = []
    return run_suite(
        manifest,
        Environment,
        View,
        limits=limits or SuiteLimits(max_steps=2),
        random_seed=42,
        emit_factory=lambda i: lambda kind, value: events.append((i, kind, value)),
        identity={},
    )


def test_b6_empty_missing_and_unfinished_cannot_pass():
    assert (
        paired_fixed_seed_report([], required_policies=("p",), planned_seeds=())["verdict"]
        == "fail"
    )
    missing = paired_fixed_seed_report(
        [PairedOutcome("a", "p", True, "loss")], required_policies=("p",), planned_seeds=("a", "b")
    )
    assert missing["verdict"] == "fail" and missing["seed_count"] == 2
    assert missing["incomplete"] == {"b": ["p"]}
    assert (
        paired_fixed_seed_report(
            [PairedOutcome("a", "p", False, None)], required_policies=("p",), planned_seeds=("a",)
        )["verdict"]
        == "fail"
    )


def test_all_suites_unique_canonical_and_membership_blacklist():
    all_seeds = []
    for name in suite_names():
        manifest = load_suite(name)
        assert len(manifest["seeds"]) == manifest["episode_count"]
        assert seed_membership(manifest["seeds"][0]) == (name,)
        with pytest.raises(ValueError, match="reserved"):
            require_non_evaluation_seed(manifest["seeds"][0])
        all_seeds.extend(manifest["seeds"])
    assert len(all_seeds) == len(set(all_seeds))


def test_natural_loss_is_completed_and_reproducible_uniform_catalog_choice():
    report = run("normal")
    choices = [env.steps[:] for env in Environment.instances]
    assert report["verdict"] == "pass" and report["losses"] == report["planned"] == 2
    assert report["wins"] == 0 and report["win_rate_wilson_95"][1] > 0
    assert all(env.closed.is_set() for env in Environment.instances)
    run("normal")
    assert choices == [env.steps[:] for env in Environment.instances]


def test_unknown_no_retry_stops_suite_keeps_full_denominator():
    report = run("unknown", count=3)
    assert report["unknown_delivery"] == 1 and report["started"] == 1
    assert report["planned"] == report["censored"] == 3
    assert len(Environment.instances) == 1 and len(Environment.instances[0].steps) == 1
    assert report["b6"]["seed_count"] == 3 and len(report["b6"]["incomplete"]) == 2


def test_stale_reobserves_then_chooses_and_budget_censors():
    report = run("stale", count=1)
    row = report["episodes"][0]
    assert row["stale_refusals"] == 1 and row["decisions"] == 2
    assert Environment.instances[0].observed == 3
    assert row["censor_reason"] == "step_budget" and report["win_rate"] == 0


def test_deadline_reaches_host_close_without_second_step():
    report = run(
        "idle_hang", count=1, limits=SuiteLimits(max_episode_seconds=0.03, max_suite_seconds=1)
    )
    assert Environment.instances[0].closed.is_set()
    assert report["unknown_delivery"] == 0 and report["unrecovered_hang"] == 0
    assert report["natural_terminal"] == 0


def test_deadline_after_offered_step_is_unknown_and_never_retried():
    report = run(
        "mutation_hang", count=2, limits=SuiteLimits(max_episode_seconds=0.03, max_suite_seconds=1)
    )
    assert report["unknown_delivery"] == 1 and report["started"] == 1
    assert len(Environment.instances[0].steps) == 1


def test_wilson_full_denominator_known_value():
    assert wilson_interval(0, 20) == pytest.approx([0, 0.16112515805281938])
    assert wilson_interval(0, 0) is None


@pytest.mark.parametrize("malformed", [None, [], "bad", {"request_id": "r", "action": None}])
def test_malformed_receipt_is_unknown_and_fences_next_seed(malformed):
    class Malformed(Environment):
        def step(self, action_id, snapshot_id, request_id):
            self.steps.append(action_id)
            return malformed

    Environment.instances = []
    Environment.behavior = "normal"
    manifest = {**load_suite("smoke-20"), "seeds": load_suite("smoke-20")["seeds"][:2]}
    report = run_suite(
        manifest,
        Malformed,
        View,
        limits=SuiteLimits(),
        random_seed=42,
        emit_factory=lambda i: lambda kind, value: None,
        identity={},
    )
    assert report["started"] == 1 and report["unknown_delivery"] == 1
    assert len(Environment.instances) == 1 and len(Environment.instances[0].steps) == 1


def test_cleanup_uncertainty_does_not_become_success_or_launch_next_seed():
    class Unreaped(Environment):
        def close_receipt(self):
            return {
                "driver_exit_code": -15,
                "forced_fallback": True,
                "cleanup_error": "unconfirmed",
            }

    Environment.instances = []
    Environment.behavior = "normal"
    manifest = {**load_suite("smoke-20"), "seeds": load_suite("smoke-20")["seeds"][:2]}
    report = run_suite(
        manifest,
        Unreaped,
        View,
        limits=SuiteLimits(),
        random_seed=42,
        emit_factory=lambda i: lambda kind, value: None,
        identity={},
    )
    assert report["natural_terminal"] == 1 and report["started"] == 1
    assert report["unrecovered_hang"] == 1 and report["verdict"] == "fail"


def test_cli_test_opening_is_explicit_before_side_effects(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    directory = tmp_path / "must-not-exist"
    command = [
        sys.executable,
        str(root / "tools/evaluate_managed_suite.py"),
        "--suite",
        "test-1000",
        "--candidate",
        str(tmp_path),
        "--run-dir",
        str(directory),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(root)},
        timeout=20,
    )
    assert result.returncode == 2 and "--allow-test and --test-purpose" in result.stderr
    assert not directory.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "development"),
        ("episode_count", 1),
        ("game_version", "future"),
        ("character", "Ironclad"),
    ],
)
def test_suite_configuration_tamper_rejected(tmp_path, monkeypatch, field, value):
    import json

    import stpd.evaluation.suites as suites

    manifest = load_suite("test-1000")
    manifest[field] = value
    (tmp_path / "test-1000.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(suites, "MANIFEST_ROOT", tmp_path)
    with pytest.raises(ValueError, match="configuration changed"):
        load_suite("test-1000")
