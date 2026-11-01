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


def test_tick_crossing_due_time_checks_once_without_an_extra_health_interval(watch, monkeypatch):
    observer = object.__new__(watch.Watcher)
    observer.config = {'poll_seconds': 900, 'started_at': 0, 'wall_seconds': 72000,
                       'evaluation_seconds': 14400}
    observer.state = {'stop': None, 'pending': None, 'last_evaluation_at': 0,
                      'config_sha256': 'test'}
    observer.maintenance_requested = False
    observer.telemetry = lambda *_args, **_kwargs: None
    observer.drain = lambda: True
    now, ticks, sleeps = [14399.0], [], []

    def tick(start=None):
        ticks.append(now[0] if start is None else start)
        if len(ticks) == 1:
            now[0] = 14401.0  # A real exchange read can cross the due boundary.
        else:
            observer.state['stop'] = 'test_finished'

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    observer.tick = tick
    monkeypatch.setattr(watch.sys, 'platform', 'linux')
    monkeypatch.setenv('SLURM_JOB_ID', 'test-owned-job')
    monkeypatch.setattr(watch.signal, 'signal', lambda *_: None)
    monkeypatch.setattr(watch.time, 'time', lambda: now[0])
    monkeypatch.setattr(watch.time, 'sleep', sleep)
    observer.run()
    assert ticks == [14399.0, 14401.0]
    assert sleeps == [0]
    observer.state['stop'] = None
    assert observer.next_delay(14402, tick_started_at=14401) == 900


def rl3_tiers():
    return [{'name': 'quick', 'seconds': 14400, 'seed_prefix': 20},
            {'name': 'milestone', 'seconds': 28800, 'seed_prefix': 100}]


def tier_observer(watch, tmp_path, **configuration):
    instance = watch.Watcher({'peer': {'kind': 'local', 'directory': str(tmp_path / 'peer')},
                              'run': 'rl3-watch', 'started_at': 0,
                              'evaluation_tiers': rl3_tiers(), **configuration}, tmp_path / 'watch')
    return instance


def test_tier_schedule_uses_fixed_boundaries_and_one_pending_without_catchup(watch, tmp_path):
    instance = tier_observer(watch, tmp_path)
    offers = []

    def prepare(model, now, **options):
        offers.append((model['version'], now, options))
        instance.state['pending'] = {'version': model['version']}

    instance.prepare_evaluation = prepare
    instance.schedule_tiers({'version': 8, 'model_artifact_id': '8' * 64}, 14400)
    assert offers[0][2]['tier']['name'] == 'quick'
    instance.schedule_tiers({'version': 18, 'model_artifact_id': '9' * 64}, 28800)
    assert len(offers) == 1
    skipped = instance.state['evaluation_schedule'][-2:]
    assert {r['tier'] for r in skipped} == {'quick', 'milestone'}
    assert all(r['state'] == 'skipped' and r['reason'] == 'pending_evaluation' for r in skipped)
    instance.state['pending'] = None
    instance.schedule_tiers({'version': 22, 'model_artifact_id': 'a' * 64}, 28801)
    assert len(offers) == 1  # no reoffer of an old scheduled slot
    instance.schedule_tiers({'version': 30, 'model_artifact_id': 'b' * 64}, 57600)
    assert len(offers) == 2 and offers[-1][2]['tier']['name'] == 'milestone'
    assert offers[-1][2]['due_at'] == 57600
    assert instance.state['evaluation_schedule'][-1]['unevaluated_version_range'] == [9, 29]
    resumed = watch.Watcher(instance.config, instance.work)
    resumed.prepare_evaluation = prepare
    resumed.schedule_tiers({'version': 31, 'model_artifact_id': 'c' * 64}, 57601)
    assert len(offers) == 2


def test_quick_only_schedule_never_opens_intermediate100_or_confirmation(watch, tmp_path):
    instance = tier_observer(watch, tmp_path, evaluation_tiers=rl3_tiers()[:1])
    offers = []
    instance.prepare_evaluation = lambda model, now, **options: offers.append(options)
    for at in (14400, 28800, 43200):
        instance.schedule_tiers({'version': at // 14400, 'model_artifact_id': 'a' * 64}, at)
    assert [row['tier']['seed_prefix'] for row in offers] == [20, 20, 20]
    assert [row['due_at'] for row in offers] == [14400, 28800, 43200]
    assert not instance.state.get('confirmation_requested')


@pytest.mark.parametrize("mode", ["submit", "direct"])
def test_tier_prepare_overrides_serial_pipeline_and_never_opens_confirmation(
    watch, tmp_path, monkeypatch, mode
):
    from types import SimpleNamespace

    from stpd.evaluation.suites import rl3_learning_curve_manifest

    runtime = tmp_path / 'runtime'
    (runtime / 'tools/friday').mkdir(parents=True)
    (runtime / 'tools/friday/evaluate.py').write_text('# frozen runtime fixture\n')
    execution = tmp_path / 'execution.json'
    execution.write_text(json.dumps({'schema': 'spireagent/managed-evaluation-execution-v1',
        'store': 's3', 'peer': {'kind': 's3', 'bucket': 'test', 'prefix': 'test',
        'endpoint': 'https://example.invalid', 'region': 'auto'},
        'retention': watch.evaluate.RETENTION, 'pipeline': {'save_workers': 1, 'pending_limit': 1},
        'models': {}}))
    manifest = tmp_path / 'new100.json'
    manifest.write_text(json.dumps(rl3_learning_curve_manifest('v0.111.0')))
    instance = tier_observer(watch, tmp_path)
    monkeypatch.setenv('SLURM_CPUS_PER_TASK', '4')
    instance.config.update(evaluation_mode=mode, evaluation_execution=str(execution),
        evaluation_manifest=str(manifest), runtime_source=str(runtime))
    instance.exchange = SimpleNamespace(materialize_model=lambda *_: tmp_path / 'model')
    instance.publish = lambda *_args, **_kwargs: None
    with (patch.object(watch.evaluate, 'submit', return_value={'job_id': 'fixture-123'}),
          patch.object(watch.subprocess, 'Popen', return_value=SimpleNamespace(pid=123))):
        instance.prepare_evaluation({'version': 2, 'model_artifact_id': '1' * 64,
                                     'training': {'updates': 16}}, 14400,
                                    tier=rl3_tiers()[0], due_at=14400)
    plan = json.loads(Path(instance.state['pending']['plan']).read_bytes())
    assert plan['planned'] == 20 and plan['seed_prefix'] == 20
    assert plan['observation_report'] is True
    assert plan['workers'] == plan['parallelism']['execution_upper_bound'] == 4
    assert plan['parallelism']['pending_limit'] == 8 and plan['parallelism']['save_workers'] == 2
    assert Path(plan['manifest']['path']).read_bytes() == manifest.read_bytes()
    assert instance.state['pending']['evaluation_kind'] == 'quick'
    assert not instance.state.get('confirmation_requested')


def test_stop_signals_are_persisted_once_even_if_scheduler_outcome_unknown(watch, tmp_path):
    instance = tier_observer(watch, tmp_path,
                             actor_plan=str(tmp_path / 'actors/plan.json'), actor_job_id='123')
    instance.telemetry = lambda *_args, **_kwargs: None
    with patch.object(watch.subprocess, 'run',
                      side_effect=OSError('lost scheduler response')) as send:
        instance.stop('owner_stop')
        instance.stop('owner_stop')
        assert send.call_count == 1
    resumed = watch.Watcher(instance.config, instance.work)
    resumed.telemetry = instance.telemetry
    with patch.object(watch.subprocess, 'run') as send:
        resumed.stop('owner_stop')
        send.assert_not_called()
    assert resumed.state['stop_signals']['123']['state'] == 'outcome_unknown'
    assert resumed.exchange.stop_request()['reason'] == 'owner_stop'


def cohort(floors, *, wins=0, missing_boss=0):
    return {'wins': wins, 'planned': len(floors), 'floor_n': len(floors),
            'boss_count_missing': missing_boss, 'boss1_cleared_witnessed': 0,
            'boss2_reached_witnessed': 0, 'boss1_reached_witnessed': 0,
            'host_kind': 'Managed', 'character': 'Defect', 'ascension': 0,
            'protocol_observed': {'behavior_policy': {'mode': 'argmax'}},
            'rows': [{'seed': str(i), 'scored': True, 'floor': f} for i, f in enumerate(floors)]}


def test_candidate_references_keep_originals_and_missing_witnesses(watch, tmp_path):
    instance = tier_observer(watch, tmp_path)
    result = {'complete': True, 'comparison_key': 'suite-and-protocol', 'unknown': 0,
              'planned': 2, 'cohort': cohort([8, 17]), 'result_sha256': '1' * 64,
              'evaluation_kind': 'quick', 'version': 2, 'updates': 16,
              'model_artifact_id': 'a' * 64}
    instance.state['evaluations'].append(result)
    instance.candidate_reference(result)
    first = list((instance.work / 'references/candidate').glob('*.json'))
    original = first[0].read_bytes()
    better = {**result, 'result_sha256': '2' * 64, 'cohort': cohort([9, 18]), 'version': 3}
    instance.state['evaluations'].append(better)
    instance.candidate_reference(better)
    assert instance.state['candidate_best']['suite-and-protocol'] == better['result_sha256']
    instance.candidate_reference(better)
    assert len(list((instance.work / 'references/candidate').glob('*.json'))) == 2
    assert first[0].read_bytes() == original
    assert instance.exchange.latest_model() is None  # no training-model replacement
    missing = {**better, 'cohort': cohort([10, 20], missing_boss=2)}
    assert instance.candidate_comparison(better, missing) == (
        True, 'arrival_distribution_dominance')


def test_plateau_only_uses_same_complete_seed_protocol_and_never_stops(watch, tmp_path):
    instance = tier_observer(watch, tmp_path)
    rows = [{'complete': True, 'comparison_key': 'same', 'unknown': 0, 'planned': 2,
             'cohort': cohort([8, 17])} for _ in range(4)]
    instance.state['evaluations'] = rows
    assert instance.plateau() is True
    assert instance.state['stop'] is None
    rows[-1] = {**rows[-1], 'comparison_key': 'different'}
    assert instance.plateau() is False
    rows[-1] = {**rows[0], 'unknown': 1}
    assert instance.plateau() is False


def test_milestone_pins_actual_complete_pair_across_uncertain_reference_write(watch, tmp_path):
    from types import SimpleNamespace

    instance = tier_observer(watch, tmp_path)
    checkpoint = {'version': 8, 'published_at': 14390, 'checkpoint_artifact_id': 'c' * 64}
    instance.exchange = SimpleNamespace(latest_checkpoint=lambda: checkpoint,
                                       model=lambda v: {'version': v})
    pairs = []
    instance.checkpoint_binding = lambda cp, model: pairs.append((cp, model)) or {
        'version': model['version'], 'updates': 64,
        'checkpoint_artifact_id': cp['checkpoint_artifact_id']}
    with (patch.object(instance, 'append_reference', side_effect=OSError('uncertain reference')),
          pytest.raises(OSError)):
        instance.milestone_reference(14400)
    checkpoint.update(version=9, published_at=14410)
    references = []
    instance.append_reference = lambda kind, identity, fields: references.append(fields)
    instance.milestone_reference(14420)
    assert len(pairs) == 1 and references[0]['version'] == 8
    assert references[0]['captured_at'] == 14390
    assert instance.state['next_milestone_at'] == 28800


def test_full_checkpoint_reference_rejects_wrong_updates_or_different_weights(watch, tmp_path):
    import hashlib
    from types import SimpleNamespace

    import torch
    from test_artifact_store_v1 import PRODUCER

    from spireagent.json_boundary import FrozenObject
    from stpd.workers.checkpoint_codec import encode_checkpoint

    instance = tier_observer(watch, tmp_path)
    info = {'schema': 'stpd/rl-learner-checkpoint-2',
            'run': 'rl3-watch', 'version': 8, 'updates': 64,
            'config': {'lr': 1e-5}, 'producer': PRODUCER.to_dict()}
    learner = {'run': 'rl3-watch', 'version': 8, 'updates': 64,
               'random': [3, [1, 2], None], 'buffer': [], 'counts': {}, 'seen': [], 'returns': []}
    state = {'schema': 'stpd/rl-learner-checkpoint-2', 'weights': {'weight': torch.tensor([1.0])},
             'optimizer': {}, 'torch_rng': torch.tensor([1], dtype=torch.uint8),
             'learner': json.dumps(learner)}
    raw = encode_checkpoint(state)
    manifest = SimpleNamespace(kind='checkpoint', artifact_id='c' * 64,
        parameters=FrozenObject.of(info), producer=PRODUCER,
        payload=lambda _: SimpleNamespace(sha256=hashlib.sha256(raw).hexdigest()))
    store = SimpleNamespace(get_manifest=lambda _: manifest, read_payload=lambda _: [raw])
    instance.exchange = SimpleNamespace(store=store, materialize_model=lambda *_: tmp_path)
    model = {'version': 8, 'training': {'updates': 64},
             'model_artifact_id': 'a' * 64, 'model_sha256': 'b' * 64}
    checkpoint = {'version': 8, 'checkpoint_artifact_id': 'c' * 64}
    with patch('stpd.p1.export.load_model', return_value=(
            SimpleNamespace(state_dict=lambda: {'weight': torch.tensor([1.0])}), {})):
        binding = instance.checkpoint_binding(checkpoint, model)
    assert binding['version'] == 8 and binding['updates'] == 64
    with (patch('stpd.p1.export.load_model', return_value=(
            SimpleNamespace(state_dict=lambda: {'weight': torch.tensor([2.0])}), {})),
          pytest.raises(ValueError, match='weights_mismatch')):
        instance.checkpoint_binding(checkpoint, model)
    with pytest.raises(ValueError, match='binding_mismatch'):
        instance.checkpoint_binding(checkpoint, {**model, 'training': {'updates': 65}})


def test_crossing_CDF_uses_paired95_lower_bound_after_skipping_missing_Boss(watch):
    before = {'comparison_key': 'same', 'cohort': cohort([10] * 20, missing_boss=20)}
    after = {'comparison_key': 'same', 'cohort': cohort([9] + [12] * 19, missing_boss=20)}
    assert watch.Watcher.candidate_comparison(before, after) == (
        True, 'crossing_CDF_paired95_lower_bound')
    noisy = {'comparison_key': 'same', 'cohort': cohort([0] + [11] * 19, missing_boss=20)}
    assert watch.Watcher.candidate_comparison(before, noisy) == (
        False, 'crossing_CDF_paired95_lower_bound')


def test_watcher_observation_report_reads_only_verified_local_files(watch, tmp_path):
    import hashlib

    from spireagent.json_boundary import json_bytes

    instance = tier_observer(watch, tmp_path)
    root = tmp_path / 'evaluation'
    evidence = root / 'evidence'
    evidence.mkdir(parents=True)
    ledger = {'slots': []}
    watch.save(root / 'plan.json', {'fixture': 'bound normal plan bytes'})
    source = {'plan_sha256': watch.hashed(root / 'plan.json'), 'ledger_report': ledger}
    watch.save(evidence / 'report-source.json', source)
    watch.save(evidence / 'report-details.json', {'episodes': []})
    current = cohort([8, 17])
    current.update(source_sha256=watch.hashed(evidence / 'report-source.json'),
                   details_sha256=watch.hashed(evidence / 'report-details.json'))
    watch.save(evidence / 'report-cohort.json', current)
    references = {name: {'path': f'report-{name}.json',
                         'sha256': watch.hashed(evidence / f'report-{name}.json')}
                  for name in ('source', 'details', 'cohort')}
    watch.save(root / 'result.json', {**source, 'observation_report': {
        'schema': 'spireagent/evaluation-observation-files-1', 'files': references,
        'ledger_report_sha256': hashlib.sha256(json_bytes(ledger)).hexdigest()}})
    plan = {'manifest': {'sha256': 'b' * 64}, 'seed_prefix': 20}
    with (patch.object(watch.evaluate, 'load_plan', return_value=plan),
          patch('spireagent.storage.config.open_peer', side_effect=AssertionError('no watcher R2')),
          patch('stpd.evaluation.rl_observation.write_report') as render):
        result = instance.evaluation_report(root / 'result.json', {'plan': str(root / 'plan.json')})
    assert result['cohort'] == current
    assert render.call_count == 1
    (evidence / 'report-cohort.json').write_text('{}')
    with (patch.object(watch.evaluate, 'load_plan', return_value=plan),
          pytest.raises(ValueError, match='pinned_input_changed')):
        instance.evaluation_report(root / 'result.json', {'plan': str(root / 'plan.json')})
