"""Watcher stop arithmetic and frozen-runtime dispatch; no game/GPU qualification."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("fcntl", reason="Friday targets POSIX Slurm")
FRIDAY = Path(__file__).resolve().parents[2] / "tools/friday"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def watch():
    batch = module("watch_batch", FRIDAY / "batch.py")
    with patch.dict(sys.modules, {"batch": batch}):
        submit = module("watch_submit", FRIDAY / "submit.py")
        with patch.dict(sys.modules, {"submit": submit}):
            evaluate = module("watch_evaluate", FRIDAY / "evaluate.py")
            with patch.dict(sys.modules, {"evaluate": evaluate}):
                return module("watch", FRIDAY / "watch.py")


def test_stops_keep_unknown_denominator_strict_and_baseline_strict(watch):
    config = {"started_at": 100, "wall_seconds": 72000}
    games = [{"status": "unknown"}] + [{"status": "finished"}] * 19
    assert watch.stop_reason(1000, config, games, None, []) is None
    assert (
        watch.stop_reason(1000, config, games[:-1], None, [])
        == "training_unknown_fraction_above_5_percent"
    )
    assert watch.stop_reason(72100, config, [], None, []) == "first_round_wall_limit"
    rows = [{"complete": True, "mean_max_floor": 11.75}] * 2
    assert watch.stop_reason(1000, config, [], None, rows) is None
    rows = [{"complete": True, "mean_max_floor": 11.74}] * 2
    assert (
        watch.stop_reason(1000, config, [], None, rows)
        == "two_evaluations_below_baseline_by_more_than_two_floors"
    )
    assert watch.stop_reason(1000, config, [], None, rows[:1]) is None
    assert watch.stop_reason(1000, config, [], {"kind": "progress", "kl": 0.5}, []) is None
    assert watch.stop_reason(1000, config, [], {"kind": "failed"}, []) == "learner_failed"
    assert (
        watch.stop_reason(1000, config, [], {"kind": "progress", "policy_loss": float("nan")}, [])
        == "learner_nonfinite_policy_loss"
    )


def test_incomplete_audit_evaluation_never_becomes_regression_or_TB_score(watch, tmp_path):
    report = {
        "score_admitted": False,
        "planned": 20,
        "not_started": 19,
        "reserved": 0,
        "save_pending": 0,
        "save_failed": 0,
        "saved_unscored": 0,
        "trajectory_pending": 0,
        "censored": 0,
        "win": 0,
        "loss": 0,
        "unknown": 1,
        "groups": [{"scored": 0}],
        "slots": [{"status": "unknown"}],
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"ledger_report": report}))
    result = watch.evaluate_result(path, 2, 16)
    assert result["complete"] is False and result["unknown"] == 1
    assert result["mean_max_floor"] is None and result["scored"] == 0


def test_20_hour_actor_prepare_pins_old_runtime_and_single_core(watch, tmp_path):
    with patch.dict(
        sys.modules,
        {
            "evaluate": watch.evaluate,
            "submit": watch.scheduler,
            "batch": module("long_batch", FRIDAY / "batch.py"),
        },
    ):
        rl = module("long_rl", FRIDAY / "rl.py")
    peer = {
        "kind": "s3",
        "bucket": "test",
        "endpoint": "https://example.invalid",
        "prefix": "test",
        "region": "auto",
    }
    runtime = tmp_path / "frozen-source"
    runtime.mkdir()
    execution = tmp_path / "execution.json"
    execution.write_text(
        json.dumps(
            {
                "schema": "spireagent/managed-evaluation-execution-v1",
                "candidate_directory": str(tmp_path),
                "peer": peer,
                "store": "s3",
                "retention": rl.RETENTION,
            }
        )
    )
    learner = tmp_path / "learner.json"
    learner.write_text(
        json.dumps({"schema": "stpd/rl-learner-config-1", "run": "rl2", "peer": peer})
    )
    plan = rl.prepare(
        execution,
        learner,
        tmp_path / "plan",
        actors=12,
        wall_seconds=75600,
        seed_namespace="rl2-train",
        max_games=10000,
        runtime_source=runtime,
    )
    assert plan["source_root"] == str(runtime)
    assert len(plan["actors"]) == 12
    assert all(json.loads(Path(ref["path"]).read_bytes())["max_games"] == 10000
               for ref in plan["actors"])
    assert all(
        json.loads(Path(ref["path"]).read_bytes())["wall_seconds"] == 75600
        for ref in plan["actors"]
    )


def test_slurm_terminal_state_requires_all_array_members_and_retains_failures(watch):
    with patch.object(
        watch.subprocess,
        "check_output",
        return_value=("123_0|COMPLETED|0:0\n123_0.batch|COMPLETED|0:0\n123_1|RUNNING|0:0\n"),
    ):
        assert watch.job_accounting("123")["terminal"] is False
    with patch.object(
        watch.subprocess, "check_output", return_value=("123_0|COMPLETED|0:0\n123_1|FAILED|1:0\n")
    ):
        value = watch.job_accounting("123")
        assert value["terminal"] is True and value["all_completed"] is False
        assert value["rows"][1]["exit_code"] == "1:0"
    with patch.object(watch.subprocess, "check_output", return_value=""):
        assert watch.job_accounting("123")["terminal"] is False


def test_terminal_evaluation_without_result_is_attention_and_never_reoffered(watch, tmp_path):
    instance = object.__new__(watch.Watcher)
    plan = tmp_path / "plan.json"
    instance.state = {
        "pending": {
            "plan": str(plan),
            "version": 1,
            "updates": 8,
            "job_id": "123",
            "state": "submitted",
        }
    }
    events = []
    instance.persist = lambda: None
    instance.publish = lambda kind, **fields: events.append({"kind": kind, **fields})
    instance.direct = None
    with patch.object(watch, "job_accounting", return_value={"terminal": True, "rows": []}):
        instance.collect_evaluation()
    assert instance.state["pending"]["state"] == "terminal_without_result"
    assert events[0]["reason"] == "evaluation_job_terminal_without_result_no_retry"
    assert instance.state["pending"]["job_id"] == "123"


def test_split_runtime_submit_cli_keeps_strict_module_origin(watch, tmp_path):
    import os
    import subprocess
    import textwrap

    batch = module("split_batch", FRIDAY / "batch.py")
    with patch.dict(
        sys.modules, {"evaluate": watch.evaluate, "submit": watch.scheduler, "batch": batch}
    ):
        rl = module("split_rl", FRIDAY / "rl.py")
    frozen = tmp_path / "frozen-source"
    package = frozen / "python/spireagent"
    (package / "workbench").mkdir(parents=True)
    for file in ("__init__.py", "rl_exchange.py", "workbench/__init__.py", "workbench/rl_actor.py"):
        (package / file).write_text("# frozen import-origin fixture; no actor executes\n")
    peer = {
        "kind": "s3",
        "bucket": "test",
        "endpoint": "https://example.invalid",
        "prefix": "test",
        "region": "auto",
    }
    execution = tmp_path / "execution.json"
    execution.write_text(
        json.dumps(
            {
                "schema": "spireagent/managed-evaluation-execution-v1",
                "candidate_directory": str(tmp_path),
                "peer": peer,
                "store": "s3",
                "retention": rl.RETENTION,
            }
        )
    )
    learner = tmp_path / "learner.json"
    learner.write_text(
        json.dumps({"schema": "stpd/rl-learner-config-1", "run": "rl2", "peer": peer})
    )
    output = tmp_path / "plan"
    rl.prepare(
        execution,
        learner,
        output,
        actors=12,
        wall_seconds=75600,
        seed_namespace="rl2-training",
        runtime_source=frozen,
    )
    driver = textwrap.dedent("""\
        import sys
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        import rl
        # Only site-specific scheduler IO/lock path are replaced, as in other Friday tests.
        lock_path = Path(sys.argv[2])
        rl.scheduler.submission_lock_path = lambda: lock_path
        rl.scheduler.output = lambda args: '0/400/0/400' if args[0] == 'sinfo' else ''
        sys.argv = ['rl.py', 'submit', '--plan', sys.argv[3], '--dry-run']
        rl.main()
        """)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            driver,
            str(FRIDAY),
            str(tmp_path / "private/submit.lock"),
            str(output / "plan.json"),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
        cwd=tmp_path,
        env={"PATH": os.defpath, "PYTHONPATH": str(frozen / "python")},
    )
    submission = json.loads(result.stdout)
    assert submission["dry_run"] is True and submission["cores"] == 12
    assert "--time=1265" in submission["command"]
    assert "--array=0-11%12" in submission["command"]
    launcher = (output / "launch.sh").read_text()
    assert f"source_root={frozen}" in launcher
    assert 'export PYTHONPATH="$source_root/python"' in launcher
    assert not (output / "submission.json").exists()


def test_R2_outage_cannot_bypass_actor_wall_stop_or_40_minute_drain(
    watch, tmp_path, monkeypatch, capsys
):
    from types import SimpleNamespace

    def unavailable(*_args, **_kwargs):
        raise OSError("injected R2 outage")

    exchange = SimpleNamespace(
        prefix="exchange/rl/rl2/",
        blobs=SimpleNamespace(put_if_absent=unavailable),
        shard_keys=unavailable,
        request_stop=unavailable,
        latest_event=unavailable,
    )
    monkeypatch.setattr(watch, "Exchange", lambda *_: exchange)
    monkeypatch.setattr(watch.sys, "platform", "linux")
    monkeypatch.setenv("SLURM_JOB_ID", "watch-test")
    monkeypatch.setattr(watch.signal, "signal", lambda *_: None)
    now = [100.0]
    monkeypatch.setattr(watch.time, "time", lambda: now[0])
    monkeypatch.setattr(watch.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    actor_signals = []

    def signal_actor(command, **_kwargs):
        actor_signals.append((now[0], command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(watch.subprocess, "run", signal_actor)
    monkeypatch.setattr(
        watch, "job_accounting", lambda _: {"terminal": True, "all_completed": True}
    )
    config = {
        "peer": {},
        "run": "rl2",
        "started_at": 100,
        "wall_seconds": 10,
        "poll_seconds": 900,
        "actor_plan": str(tmp_path / "actors/plan.json"),
        "actor_job_id": "123",
        "evaluation_mode": "direct",
    }
    instance = watch.Watcher(config, tmp_path / "watch")
    instance.run()
    assert actor_signals[0][0] == 110.0
    assert actor_signals[0][1] == ["scancel", "--signal=USR1", "--batch", "123"]
    assert (tmp_path / "actors/STOP").is_file()
    assert now[0] == 2510.0  # original wall deadline + forty minutes, despite every R2 call failing
    assert instance.state["learner_stop_pending"] is True
    final = json.loads((tmp_path / "watch/drain-status.json").read_bytes())
    assert final["drain_timeout"] is True and final["drain_complete"] is False
    assert final["learner_stop_pending"] is True
    assert "watcher_telemetry_failed" in capsys.readouterr().err


def test_direct_evaluation_must_exit_before_drain_can_complete(watch, tmp_path):
    from types import SimpleNamespace

    instance = object.__new__(watch.Watcher)
    instance.config = {"started_at": 100, "actor_job_id": "123", "evaluation_mode": "direct"}
    instance.state = {"pending": None, "drain_started_at": 110, "learner_stop_pending": False}
    instance.work = tmp_path
    instance.exchange = SimpleNamespace(
        latest_event=lambda: {"kind": "finished", "at": 111},
        latest_model=lambda: {"version": 3},
        latest_checkpoint=lambda: {"version": 3},
    )
    instance.telemetry = lambda *_args, **_kwargs: None
    instance.direct = SimpleNamespace(poll=lambda: None)
    with (
        patch.object(watch.time, "time", return_value=112),
        patch.object(watch, "job_accounting", return_value={"terminal": True}),
    ):
        assert instance.drain() is False
        instance.direct = SimpleNamespace(poll=lambda: 0)
        assert instance.drain() is True


def test_confirmation_uses_original_forty_and_never_enters_fixed20_regression(watch, tmp_path):
    from spireagent.json_boundary import BoundaryError
    from spireagent.workbench.reference_suite import load_manifest
    from stpd.evaluation.suites import load_suite, seed_membership, t2_managed_manifest

    # The original consumer accepts complete T2 but rejects a generalization-suite substitution.
    manifest = tmp_path / "T2.json"
    t2 = t2_managed_manifest("v0.111.0")
    manifest.write_text(json.dumps(t2))
    assert load_manifest(manifest, watch.hashed(manifest))[1]["seeds"] == t2["seeds"]
    assert all(seed_membership(seed) for seed in t2["seeds"])
    assert len(set(t2["seeds"][20:]) - set(t2["seeds"][:20])) == 20
    other = tmp_path / "gen.json"
    other.write_text(json.dumps(load_suite("gen-Defect-200")))
    with pytest.raises(BoundaryError, match="manifest_configuration_changed"):
        load_manifest(other, watch.hashed(other))
    config = {"started_at": 100, "wall_seconds": 72000}
    rows = [{"complete": True, "evaluation_kind": "confirmation", "mean_max_floor": 3}] * 2
    assert watch.stop_reason(1000, config, [], None, rows) is None


def test_winning_fixed20_schedules_one_forty_seed_confirmation_without_shifting_clock(
    watch, tmp_path
):
    from types import SimpleNamespace

    instance = object.__new__(watch.Watcher)
    instance.config = {"started_at": 100, "wall_seconds": 72000, "evaluation_mode": "direct"}
    instance.state = {
        "shards": {},
        "pending": None,
        "stop": None,
        "last_evaluation_at": 100,
        "evaluations": [
            {
                "complete": True,
                "evaluation_kind": "fixed20",
                "wins": 1,
                "version": 2,
                "mean_max_floor": 15,
            }
        ],
    }
    model = {"version": 2}
    instance.exchange = SimpleNamespace(
        shard_keys=lambda: [],
        latest_event=lambda: {"kind": "progress", "at": 1000},
        latest_model=lambda: model,
        model=lambda version: {"version": version},
    )
    instance.collect_evaluation = lambda: None
    instance.persist = lambda: None
    instance.publish = lambda kind, **fields: {"kind": kind, **fields}
    instance.signalled = False
    instance.maintenance_requested = False
    instance.actor_health = lambda: {'running': 12}
    prepared = []
    instance.prepare_evaluation = lambda model, now, **kwargs: prepared.append((model, now, kwargs))
    instance.tick(1000)
    instance.tick(1001)
    assert len(prepared) == 1 and prepared[0][2] == {"confirmation": True}
    assert prepared[0][0]["version"] == 2
    assert instance.state["last_evaluation_at"] == 100
    assert instance.state["confirmation_requested"] is True


def test_confirmation_real_prepare_freezes_original_prefix40_and_three_hour_direct_budget(
    watch, tmp_path
):
    from types import SimpleNamespace

    from stpd.evaluation.suites import t2_managed_manifest

    runtime = tmp_path / "frozen-source"
    (runtime / "tools/friday").mkdir(parents=True)
    (runtime / "tools/friday/evaluate.py").write_text("# frozen runtime boundary\n")
    peer = {
        "kind": "s3",
        "bucket": "test",
        "endpoint": "https://example.invalid",
        "prefix": "test",
        "region": "auto",
    }
    base = tmp_path / "base.json"
    base.write_text(
        json.dumps(
            {
                "schema": "spireagent/managed-evaluation-execution-v1",
                "store": "s3",
                "peer": peer,
                "retention": watch.evaluate.RETENTION,
                "pipeline": {"save_workers": 1, "pending_limit": 2},
                "models": {},
            }
        )
    )
    manifest = tmp_path / "T2.json"
    manifest.write_text(json.dumps(t2_managed_manifest("v0.111.0")))
    instance = object.__new__(watch.Watcher)
    instance.work = tmp_path / "watch"
    instance.config = {
        "evaluation_mode": "direct",
        "evaluation_execution": str(base),
        "evaluation_manifest": str(manifest),
        "runtime_source": str(runtime),
    }
    instance.state = {"pending": None, "last_evaluation_at": 100}
    instance.persist = lambda: None
    instance.publish = lambda *_args, **_kwargs: None
    instance.exchange = SimpleNamespace(materialize_model=lambda *_: tmp_path / "model")
    with patch.object(watch.subprocess, "Popen", return_value=SimpleNamespace(pid=1234)):
        instance.prepare_evaluation(
            {"version": 2, "model_artifact_id": "1" * 64, "training": {"updates": 16}},
            1000,
            confirmation=True,
        )
    pending = instance.state["pending"]
    plan = json.loads(Path(pending["plan"]).read_bytes())
    assert pending["evaluation_kind"] == "confirmation"
    assert plan["planned"] == plan["seed_prefix"] == 40
    assert plan["workers"] == 1 and plan["wall_seconds"] == 10800
    assert plan["source_root"] == str(runtime)
    assert instance.state["last_evaluation_at"] == 100
    assert Path(plan["manifest"]["path"]).read_bytes() == manifest.read_bytes()



def test_actor_health_reports_lost_capacity_once_and_keeps_original_stops(watch, tmp_path):
    from spireagent.rl_exchange import Exchange

    plan = tmp_path / "actors/plan.json"
    plan.parent.mkdir()
    plan.write_text(json.dumps({"actors": [{"actor": "a"}, {"actor": "b"}]}))
    result = plan.parent / "actors/b/result.json"
    result.parent.mkdir(parents=True)
    result.write_text(json.dumps({"actor": "b", "run": "health", "exit_code": 0,
                                  "original_summary": {
        "status": "stopped_ledger:unknown_cleanup_requires_owner_read"}}))
    peer = {"kind": "local", "directory": str(tmp_path / "peer")}
    instance = watch.Watcher({"peer": peer, "run": "health", "started_at": 100,
                              "actor_plan": str(plan), "actor_job_id": "123"},
                             tmp_path / "watch")
    rows = [{"job_id": "123_0", "state": "RUNNING", "exit_code": "0:0"},
            {"job_id": "123_1", "state": "FAILED", "exit_code": "1:0"}]
    with patch.object(watch, "job_accounting", return_value={"rows": rows, "terminal": False}):
        health = instance.actor_health()
        instance.actor_health()
    assert health["running"] == 1 and health["expected"] == 2
    assert health["failed"] == rows[1:]
    assert health["stopped_ledgers"][0]["status"].endswith("unknown_cleanup_requires_owner_read")
    exchange = Exchange(peer, "health")
    records = exchange.blobs.keys(exchange.prefix + "status/")
    assert len(records) == 1
    event = json.loads(exchange.blobs.get(records[0]))
    assert event["reason"] == "actor_capacity_requires_owner_read"
    assert result.exists() and exchange.stop_request() is None


def test_watcher_maintenance_exits_without_stopping_training_or_actors(
        watch, tmp_path, monkeypatch):
    import signal
    import time

    from spireagent.rl_exchange import Exchange

    peer = {"kind": "local", "directory": str(tmp_path / "peer")}
    plan = tmp_path / "actors/plan.json"
    plan.parent.mkdir()
    plan.write_text(json.dumps({"actors": [{"actor": "a"}]}))
    config = {"peer": peer, "run": "maintenance", "started_at": time.time(),
              "wall_seconds": 72000, "actor_plan": str(plan), "actor_job_id": "123",
              "evaluation_mode": "direct"}
    instance = watch.Watcher(config, tmp_path / "watch")
    instance.maintenance_requested = True
    monkeypatch.setattr(watch.sys, "platform", "linux")
    monkeypatch.setenv("SLURM_JOB_ID", "456")
    monkeypatch.setattr(signal, "signal", lambda *_: None)
    monkeypatch.setattr(watch, "job_accounting", lambda _: {"rows": [
        {"job_id": "123_0", "state": "RUNNING", "exit_code": "0:0"}], "terminal": False})
    instance.run()
    exchange = Exchange(peer, "maintenance")
    assert exchange.stop_request() is None and not (plan.parent / "STOP").exists()
    rows = [json.loads(exchange.blobs.get(key))
            for key in exchange.blobs.keys(exchange.prefix + "status/")]
    assert any(row["kind"] == "watcher_maintenance_exit" for row in rows)
    assert not instance.state["stop"]



def test_actor_exit_is_visible_while_scheduler_and_snapshot_are_still_running(watch, tmp_path):
    from spireagent.rl_exchange import Exchange

    plan = tmp_path / "actors/plan.json"
    plan.parent.mkdir()
    plan.write_text(json.dumps({"actors": [{"actor": "a"}, {"actor": "b"}]}))
    for actor, job in (("a", "123_0"), ("b", "123_1")):
        root = plan.parent / "actors" / actor
        root.mkdir(parents=True)
        scratch = tmp_path / (actor + "-scratch")
        scratch.mkdir()
        (root / "owner.json").write_text(json.dumps({"job_id": job, "scratch": str(scratch)}))
    # The real launcher writes this result after wait(), before snapshot/result promotion.
    (tmp_path / "b-scratch/result.json").write_text(json.dumps({
        "actor": "b", "run": "phase", "exit_code": 1, "original_summary": None}))
    peer = {"kind": "local", "directory": str(tmp_path / "peer")}
    instance = watch.Watcher({"peer": peer, "run": "phase", "started_at": 100,
                              "actor_plan": str(plan), "actor_job_id": "123"},
                             tmp_path / "watch")
    rows = [{"job_id": f"123_{i}", "state": "RUNNING", "exit_code": "0:0"} for i in range(2)]
    with patch.object(watch, "job_accounting", return_value={"rows": rows, "terminal": False}):
        health = instance.actor_health()
    assert health["running"] == 2 and health["active_actors"] == 1
    assert health["actor_results"] == [{"actor": "b", "exit_code": 1, "status": None,
                                       "preservation_complete": False}]
    assert not (plan.parent / "actors/b/result.json").exists()
    assert Exchange(peer, "phase").stop_request() is None


def test_next_delay_wakes_at_evaluation_without_polling_or_busy_retry(watch):
    observer = object.__new__(watch.Watcher)
    observer.config = {'poll_seconds': 900, 'started_at': 0, 'wall_seconds': 72000,
                       'evaluation_seconds': 14400}
    observer.state = {'stop': None, 'pending': None, 'last_evaluation_at': 0}
    observer.maintenance_requested = False
    assert observer.next_delay(14340) == 60
    # A failed submission/tick does not become a zero-delay retry loop.
    assert observer.next_delay(14400) == 900
    observer.state['pending'] = {'job_id': 'existing'}
    assert observer.next_delay(14340) == 900
    observer.state.update(stop='wall', drain_started_at=72000)
    assert observer.next_delay(74380) == 20
