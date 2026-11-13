"""RL5 P-REWRITE WP7 slice 2: K games per environment process (``fast_chain``).

A scripted child process (``fast_env_fake.py``) speaks the WP2 ``fast-env`` NDJSON
interface through real pipes and a real process group, so crash, kill and Stop timing
are exercised, not mocked. Counterexamples: Stop latency bound met and exceeded, one
game crashing or hanging isolates only that game, environment crash, late close,
inference timeout is censored (never unknown), inference down refuses before offer.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.episode_start import SAVE_START_SCHEMA, EpisodeStart
from spireagent.workbench.fast_chain_episodes import (
    FastChainEpisodeExecutor,
    FastEnvUnavailable,
    GameManager,
    StopBound,
    apply_close,
    load_env_config,
)

FAKE = Path(__file__).with_name("fast_env_fake.py")
MANIFEST = {"character": "Defect", "ascension": 0}


def _env(slots: int, *, inference_ms: int = 200, request_ms: int = 300) -> dict:
    return {"schema": "sts2.policy-runtime/fast-env-config-1", "env_process_id": "env-a",
            "slots": slots, "inference": {"timeout_ms": inference_ms},
            "host": {"request_timeout_ms": request_ms, "ascension": 0}}


def _manager(tmp_path: Path, slots: int, games: list[dict], **scenario) -> GameManager:
    log = tmp_path / "commands.jsonl"
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps({"env_process_id": "env-a", "slots": slots, "games": games,
                                "log": str(log), **scenario}))
    manager = GameManager([sys.executable, "-I", str(FAKE), str(path)], _env(slots),
                          work=tmp_path, inference_failure_streak=scenario.get("streak", 3),
                          open_seconds=5)
    manager.start()
    return manager


def _commands(tmp_path: Path) -> list[dict]:
    log = tmp_path / "commands.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _native_alive(tmp_path: Path) -> bool:
    pid = int((tmp_path / "commands.jsonl.native").read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class _Artifacts:
    def __init__(self) -> None:
        self.stored: dict[str, bytes] = {}

    def put(self, name: str, value: bytes) -> str:
        self.stored[name] = value
        return name


def _executor(manager: GameManager, *, episode_seconds: float = 30, starts=None,
              before=None, offered=None, instance_opened=None,
              instance_excluded=None) -> FastChainEpisodeExecutor:
    return FastChainEpisodeExecutor(
        {"initial_progress": {"progress_sha256": "progress"},
         "episode_starts": starts or {}}, manager=manager, episode_seconds=episode_seconds,
        before_world_offer=before, world_offered=offered, instance_opened=instance_opened,
        reserve_seconds=5, instance_excluded=instance_excluded)


def _play(executor: FastChainEpisodeExecutor, seed: str, checkpoints=None) -> dict:
    row = {"seed": seed, "intent_id": "intent-" + seed, "selection_id": "model"}

    def checkpoint() -> None:
        if checkpoints is not None:
            checkpoints.append(dict(row))

    executor(row, dict(MANIFEST), _Artifacts(), checkpoint)
    return row


def _wait_opened(manager: GameManager, count: int, seconds: float = 10) -> None:
    """Wait for ``count`` opened games (no wall-clock guess about scheduling)."""
    deadline = time.monotonic() + seconds
    while len(manager.instances) < count:
        assert time.monotonic() < deadline
        time.sleep(0.01)


def _parallel(executor, seeds: list[str]) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    threads = [threading.Thread(target=lambda s=s: rows.__setitem__(s, _play(executor, s)))
               for s in seeds]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert all(not thread.is_alive() for thread in threads)
    return rows


def test_stop_bound_is_k_step_sync_plus_longest_in_flight_resolution():
    assert StopBound(8, 4, 2000, 30000).seconds == pytest.approx((32 + 30000 + 500) / 1000)
    assert StopBound(8, 4, 2000, 1000, margin_ms=0).seconds == pytest.approx(2.032)


def test_env_config_is_validated_before_any_process(tmp_path):
    path = tmp_path / "env.json"
    path.write_text(json.dumps(_env(17)))
    with pytest.raises(BoundaryError, match="fast_env_config_invalid"):
        load_env_config(path)
    path.write_text(json.dumps(_env(8)))
    assert load_env_config(path)["slots"] == 8


def test_failed_start_is_loud_and_kills_the_group(tmp_path):
    manager = GameManager([sys.executable, "-I", "-c", "import time; time.sleep(30)"],
                          _env(2), work=tmp_path, ready_seconds=0.5)
    with pytest.raises(BoundaryError, match="fast_env_start_failed"):
        manager.start()
    assert manager.process is not None and manager.process.process.poll() is not None


def test_k_games_share_one_process_and_reuse_slots(tmp_path):
    games = [{"close": {"reason": "terminal", "terminal": {"result": "victory"}},
              "after": 0.3} for _ in range(4)]
    manager = _manager(tmp_path, 2, games)
    rows = _parallel(_executor(manager), ["s1", "s2", "s3", "s4"])
    assert {row["status"] for row in rows.values()} == {"finished"}
    assert all(row["cleanup_accepted"] and row["world_mutation_offered"]
               for row in rows.values())
    opens = [c for c in _commands(tmp_path) if c["cmd"] == "open"]
    assert sorted({c["slot"] for c in opens}) == [0, 1] and len(opens) == 4
    refs = [c["game_ref"] for c in opens]
    # Opaque, unique, never seed-derived; only the environment side sees the seed.
    assert len(set(refs)) == 4 and not any(seed in ref for ref in refs for seed in rows)
    assert len({row["game_instance_id"] for row in rows.values()}) == 4
    report = manager.stop()
    assert report["within_bound"] and report["cleanup_confirmed"]
    assert not _native_alive(tmp_path)


def test_stop_latency_bound_is_met_by_a_cooperative_process(tmp_path):
    manager = _manager(tmp_path, 2, [{}, {}], stop_delay=0.2)  # both games run until Stop
    executor = _executor(manager)
    rows: dict[str, dict] = {}
    threads = [threading.Thread(target=lambda s=s: rows.__setitem__(s, _play(executor, s)))
               for s in ("a", "b")]
    for thread in threads:
        thread.start()
    _wait_opened(manager, 2)
    report = manager.stop()
    for thread in threads:
        thread.join(10)
    assert report["within_bound"] and not report["killed"]
    assert report["latency_seconds"] <= report["bound_seconds"]
    assert {row["status"] for row in rows.values()} == {"censored"}
    assert {row["reason"] for row in rows.values()} == {"fast_chain_stop:stop_before_send"}
    assert manager.stop() is report  # idempotent from any thread


def test_stop_latency_bound_exceeded_kills_and_leaves_games_unknown(tmp_path):
    manager = _manager(tmp_path, 2, [{}, {}], stop_ignore=True)
    executor = _executor(manager)
    rows: dict[str, dict] = {}
    threads = [threading.Thread(target=lambda s=s: rows.__setitem__(s, _play(executor, s)))
               for s in ("a", "b")]
    for thread in threads:
        thread.start()
    _wait_opened(manager, 2)
    began = time.monotonic()
    report = manager.stop()
    for thread in threads:
        thread.join(10)
    assert report["killed"] and not report["within_bound"] and report["cleanup_confirmed"]
    # Killed right after the bound, not at some later timeout.
    assert report["bound_seconds"] <= time.monotonic() - began < report["bound_seconds"] + 3
    assert {row["status"] for row in rows.values()} == {"unknown"}
    assert {row["reason"] for row in rows.values()} == {
        "fast_chain_unknown:stop_latency_bound_exceeded"}
    assert all(row["cleanup_accepted"] for row in rows.values())
    assert not _native_alive(tmp_path)
    assert sum(c["cmd"] == "open" for c in _commands(tmp_path)) == 2  # nothing resent


def test_one_native_crash_closes_only_that_game_and_its_slot_is_reused(tmp_path):
    games = [{"seed": "crash", "close": {"reason": "unknown", "detail": "native_crash",
                                         "native": "replaced"}, "after": 0.1},
             {"seed": "other", "close": {"reason": "terminal",
                                         "terminal": {"result": "defeat"}}, "after": 0.6},
             {"close": {"reason": "terminal", "terminal": {"result": "victory"}},
              "after": 0.1}]
    manager = _manager(tmp_path, 2, games)
    executor = _executor(manager)
    rows = _parallel(executor, ["crash", "other"])
    assert rows["crash"]["status"] == "unknown"
    assert rows["crash"]["reason"] == "fast_chain_unknown:native_crash"
    assert rows["crash"]["cleanup_accepted"] is True  # the environment replaced it
    assert rows["crash"]["failure_facts"]["fast_chain_close"]["native"] == "replaced"
    assert rows["other"]["status"] == "finished"
    assert manager.admission_closed() is None
    assert _play(executor, "next")["status"] == "finished"
    assert manager.stop()["within_bound"]


def test_unknown_close_that_keeps_its_native_is_not_accepted_cleanup(tmp_path):
    manager = _manager(tmp_path, 1, [{"close": {"reason": "unknown",
                                                "detail": "delivery_unknown"}}])
    row = _play(_executor(manager), "a")
    assert (row["status"], row["cleanup_accepted"]) == ("unknown", False)
    manager.stop()


def test_one_hung_game_is_fenced_others_finish_then_only_it_is_unknown(tmp_path):
    games = [{"seed": "hung", "ignore_close_slot": True},
             {"seed": "other", "close": {"reason": "terminal",
                                         "terminal": {"result": "defeat"}}, "after": 2.5}]
    manager = _manager(tmp_path, 2, games)
    short = _executor(manager, episode_seconds=0.2)  # the hung game passes its deadline
    executor = _executor(manager)
    rows: dict[str, dict] = {}
    finished_at: dict[str, float] = {}

    def run(seed: str, owner: FastChainEpisodeExecutor) -> None:
        rows[seed] = _play(owner, seed)
        finished_at[seed] = time.monotonic()

    hung = threading.Thread(target=run, args=("hung", short))
    hung.start()
    time.sleep(0.2)
    other = threading.Thread(target=run, args=("other", executor))
    other.start()
    hung.join(15)
    other.join(15)
    assert rows["other"]["status"] == "finished"
    assert rows["hung"]["status"] == "unknown"
    assert rows["hung"]["reason"] == "fast_chain_unknown:fast_env_game_close_unconfirmed"
    assert rows["hung"]["cleanup_accepted"] is True  # the whole group was killed after
    assert finished_at["hung"] >= finished_at["other"] - 0.05
    assert manager.counters["slots_fenced"] == 1
    assert [c["slot"] for c in _commands(tmp_path) if c["cmd"] == "close_slot"] == [0]
    assert not _native_alive(tmp_path)
    late = _play(executor, "late")
    assert (late["status"], late["world_mutation_offered"]) == ("not_started", False)


def test_env_crash_makes_its_games_unknown_and_refuses_before_any_new_offer(tmp_path):
    games = [{"exit_after": 1.0}, {}]  # both opens land before the crash
    manager = _manager(tmp_path, 2, games)
    executor = _executor(manager)
    rows = _parallel(executor, ["a", "b"])
    assert {row["status"] for row in rows.values()} == {"unknown"}
    assert all(row["reason"].startswith("fast_chain_unknown:env_crash:fast_env_exited:3")
               for row in rows.values())
    assert all(row["cleanup_accepted"] for row in rows.values())
    assert not _native_alive(tmp_path)
    assert manager.admission_closed().startswith("env_crash")
    after = _play(executor, "c")
    assert (after["status"], after["reason"], after["world_mutation_offered"]) == (
        "not_started", "fast_env_unavailable_before_offer", False)
    assert sum(c["cmd"] == "open" for c in _commands(tmp_path)) == 2


def test_late_close_of_a_closed_game_is_discarded(tmp_path):
    games = [{"close": {"reason": "terminal", "terminal": {"result": "defeat"}},
              "late_close": True},
             {"close": {"reason": "terminal", "terminal": {"result": "defeat"}},
              "after": 0.3}]
    manager = _manager(tmp_path, 1, games)
    executor = _executor(manager)
    first = _play(executor, "a")
    second = _play(executor, "b")
    assert first["observed_result"] == second["observed_result"] == "defeat"
    assert manager.counters["late_closes_discarded"] == 1
    manager.stop()


def test_inference_timeout_is_censored_and_a_streak_refuses_before_offer(tmp_path):
    timeout = {"close": {"reason": "not_started", "detail": "inference_timeout"}}
    manager = _manager(tmp_path, 1, [timeout, timeout], streak=2)
    executor = _executor(manager)
    first = _play(executor, "a")
    # Contract: an inference timeout is not_started for the step; the game is censored.
    assert (first["status"], first["reason"]) == (
        "censored", "fast_chain_not_started:inference_timeout")
    assert first["cleanup_accepted"] is True and "failure_facts" not in first
    assert manager.admission_closed() is None
    _play(executor, "b")
    assert manager.admission_closed() == "inference_service_unavailable_before_offer"
    offered: list[str] = []
    refused = _play(_executor(manager, before=lambda row: offered.append(row["seed"])), "c")
    assert (refused["status"], refused["reason"], refused["world_mutation_offered"]) == (
        "not_started", "inference_service_unavailable_before_offer", False)
    assert offered == [] and sum(c["cmd"] == "open" for c in _commands(tmp_path)) == 2
    manager.stop()


def test_inference_probe_refuses_and_recovers(tmp_path):
    manager = _manager(tmp_path, 1, [])
    alive = [False]
    manager.probe, manager.probe_interval = (lambda: alive[0]), 0
    with pytest.raises(FastEnvUnavailable, match="inference_service_unavailable"):
        manager.reserve(1)
    alive[0] = True
    manager.release(manager.reserve(1))
    manager.stop()


def test_budget_and_refused_open_are_censored_and_free_the_slot(tmp_path):
    games = [{"close": {"reason": "budget", "detail": "game_wall_ms"}},
             {"refuse": "slot_busy"},
             {"close": {"reason": "terminal", "terminal": {"result": "defeat"}}}]
    manager = _manager(tmp_path, 1, games)
    executor = _executor(manager)
    assert _play(executor, "a")["reason"] == "fast_chain_budget:game_wall_ms"
    refused = _play(executor, "b")
    assert (refused["status"], refused["reason"]) == (
        "censored", "fast_chain_open_refused:slot_busy")
    assert _play(executor, "c")["status"] == "finished"
    manager.stop()


def test_learner_refusal_releases_the_slot_without_an_offer(tmp_path):
    manager = _manager(tmp_path, 1, [])

    def refuse(row):
        raise BoundaryError("rl_segment", "learner_stopped_before_world_offer")

    checkpoints: list[dict] = []
    row = _play(_executor(manager, before=refuse), "a", checkpoints)
    assert (row["status"], row["reason"], row["world_mutation_offered"]) == (
        "not_started", "learner_stopped_before_world_offer", False)
    assert checkpoints == [] and manager.free == {0}
    assert _commands(tmp_path) == []
    manager.stop()


def test_offer_is_durable_before_open_and_watcher_hears_the_slot(tmp_path):
    manager = _manager(tmp_path, 1, [])
    checkpoints: list[dict] = []
    offered: list[dict] = []
    row = _play(_executor(manager, offered=lambda r: offered.append(dict(r))), "a",
                checkpoints)
    assert checkpoints[0]["world_mutation_offered"] is True
    assert checkpoints[0]["status"] == "running" and offered[0]["fast_chain_slot"] == 0
    assert offered[0]["fast_chain_game_ref"] == row["fast_chain_game_ref"]
    assert row["fast_chain_close_record"] == "fast-chain-close.json"
    manager.stop()


def _save_start(seed: str) -> dict:
    return {"schema": SAVE_START_SCHEMA,
            "save": {"schema": "sts2.host-runtime/managed-map-continue-1",
                     "path": "/private/saves/one.save", "bytes": 10, "sha256": "a" * 64,
                     "candidate": {"artifact_sha256": "b" * 64,
                                   "runtime_sts2_sha256": "c" * 64},
                     "seed": seed, "act": 2, "floor": 20, "save_boundary": "map_room",
                     "source_room_type": "MonsterRoom",
                     "parent_runtime_instance_id": "r", "parent_game_continuity_id": "g"},
            "lineage": {"initial_progress_sha256": "progress", "character": "Defect",
                        "ascension": 0, "parent_intent_id": "p"},
            "parent_identity": {"adapter_runtime_instance_id": "r",
                                "episode_provenance": {"origin": {"kind": "seed"},
                                                       "verdict": "provenance_pass",
                                                       "actual_seed": seed},
                                "initial_progress": {"progress_sha256": "progress",
                                                     "run_unlock_state_matches": True}}}


def test_save_start_reaches_the_open_and_the_echoed_start_is_checked(tmp_path):
    games = [{"close": {"reason": "terminal", "terminal": {"result": "victory"}}},
             {"close": {"reason": "terminal", "terminal": {"result": "victory"}},
              "echo_start": {"kind": "new_run"}}]
    manager = _manager(tmp_path, 1, games)
    executor = _executor(manager, starts={"a": _save_start("a"), "b": _save_start("b")})
    loaded = _play(executor, "a")
    assert loaded["status"] == "finished" and loaded["episode_start"]["kind"] == "from_save"
    opened = [c for c in _commands(tmp_path) if c["cmd"] == "open"][0]
    assert opened["start"]["kind"] == "from_save"
    assert opened["start"]["save"]["sha256"] == "a" * 64
    wrong = _play(executor, "b")
    assert (wrong["status"], wrong["reason"]) == ("unknown", "first_frame_start_changed")
    manager.stop()


@pytest.mark.parametrize("close,expected", [
    ({"reason": "weird"}, ("unknown", "fast_chain_close_reason_invalid")),
    ({"reason": "terminal", "terminal": None, "native": "kept",
      "opened": {"start": {"kind": "new_run"}}},
     ("unknown", "fast_chain_terminal_without_result")),
    # M3 fail closed: no opened echo, or an echo without ``start``, is a mismatch.
    ({"reason": "terminal", "terminal": {"result": "victory"}, "native": "kept"},
     ("unknown", "first_frame_start_changed")),
    ({"reason": "terminal", "terminal": {"result": "victory"}, "native": "kept",
      "opened": {"start": None}}, ("unknown", "first_frame_start_changed")),
    ({"reason": "env_crash", "detail": "native_crash", "native": "replaced"},
     ("unknown", "fast_chain_env_crash:native_crash")),
    ({"event": "open_not_sent", "reason": "env_crash:x"},
     ("censored", "fast_chain_open_not_sent:env_crash:x")),
])
def test_close_mapping_is_typed(close, expected):
    row: dict = {}
    apply_close(row, close, start=EpisodeStart.new_run("seed"))
    assert (row["status"], row["reason"]) == expected


def test_old_chain_executors_are_unchanged_by_the_shared_admission_helpers():
    from spireagent.workbench.rl_segment_producer import (
        SegmentManagedExecutor,
        publish_world_offered,
    )

    events: list[tuple] = []
    exchange = SimpleNamespace(publish_event=lambda kind, **fields: events.append(
        (kind, fields)))
    executor = object.__new__(SegmentManagedExecutor)
    executor.exchange, executor.actor = exchange, "actor-a"
    row = {"intent_id": "i", "seed": "s", "segment_admission_owner": {
        "run": "r", "training_generation": "g", "budget_id": "b"}}
    executor._world_offer_checkpointed(row)
    assert events[-1] == ("actor_world_offered", {"actor": "actor-a", "intent_id": "i",
                                                  "seed": "s", "training_generation": "g",
                                                  "budget_id": "b"})
    publish_world_offered({**row, "fast_chain_slot": 3, "fast_chain_game_ref": "g-x"},
                          exchange=exchange, actor="actor-a")
    assert events[-1][1]["fast_chain_slot"] == 3


def test_actor_fast_chain_configuration_is_opt_in_and_checked(tmp_path):
    from spireagent.workbench.rl_actor import Actor

    env = tmp_path / "env.json"
    env.write_text(json.dumps(_env(8)))
    actor = object.__new__(Actor)
    actor.segment_mode, actor.work = True, tmp_path
    actor.base = {"retention": {"tier": "trajectory", "purpose": "training",
                                "audit_every": 20}}
    actor.config = {"pipeline": {"save_workers": 1, "pending_limit": 8}}
    value = actor._fast_chain_configuration({"command": ["node", "p.js"],
                                             "env_config": str(env)})
    assert value["env"]["slots"] == 8
    actor.config = {"pipeline": {"save_workers": 1, "pending_limit": 4}}
    with pytest.raises(BoundaryError, match="fast_chain_pending_limit_below_slots"):
        actor._fast_chain_configuration({"command": ["node"], "env_config": str(env)})
    actor.segment_mode = False
    with pytest.raises(BoundaryError, match="fast_chain_configuration_invalid"):
        actor._fast_chain_configuration({"command": ["node"], "env_config": str(env)})


def test_actor_announce_adds_fast_chain_facts_only_for_fast_rows(tmp_path):
    from spireagent.workbench.rl_actor import Actor

    events: list[dict] = []
    actor = object.__new__(Actor)
    actor.segment_mode, actor.name, actor.config = True, "actor-a", {}
    actor.games, actor.counts = [], __import__("collections").Counter()
    actor.exchange = SimpleNamespace(publish_event=lambda kind, **f: events.append(f))
    rows = [{"run_id": None, "chain": "fast_chain", "game_instance_id": "gi",
             "fast_chain_slot": 1, "intent_id": "i1",
             "fast_chain_close": {"reason": "unknown", "detail": "native_crash"},
             "failure_facts": {"execution_reason": "x"}},
            {"run_id": None, "intent_id": "i2"}]
    slots = []
    for index, row in enumerate(rows):
        path = tmp_path / f"r{index}.json"
        path.write_text(json.dumps({"episodes": [row]}))
        slots.append({"seed": f"s{index}", "status": "unknown", "attempt_report": str(path),
                      "run_id": None})
    actor._announce({"version": 1}, slots)
    fast, old = events[0]["games"]
    assert (fast["game_instance_id"], fast["fast_chain_slot"], fast["close_reason"],
            fast["close_detail"]) == ("gi", 1, "unknown", "native_crash")
    assert "game_instance_id" not in old and "close_reason" not in old


# -- review fixes (REVIEW-PR419-421) -----------------------------------------------------
def test_m1_open_after_stop_began_is_never_sent_and_is_censored_at_once(tmp_path):
    manager = _manager(tmp_path, 1, [], stop_delay=2)
    reservation = manager.reserve(5)
    stopper = threading.Thread(target=manager.stop)
    stopper.start()
    while manager.unavailable is None:
        time.sleep(0.01)
    began = time.monotonic()
    close = manager.play(reservation, seed="a", initial_progress=None,
                         start=EpisodeStart.new_run("a"), r7=False, deadline_seconds=60)
    row: dict = {}
    apply_close(row, close, start=EpisodeStart.new_run("a"))
    assert time.monotonic() - began < 1
    assert row["status"] == "censored" and row["reason"].startswith(
        "fast_chain_open_not_sent:")
    stopper.join(10)
    assert [c["cmd"] for c in _commands(tmp_path)] == ["stop"]


def test_m1_exit_with_a_sent_game_unclosed_resolves_it_now_not_at_its_deadline(tmp_path):
    games = [{"close": {"reason": "terminal", "terminal": {"result": "victory"}},
              "after": 60, "survive_stop": True}]
    manager = _manager(tmp_path, 1, games)
    rows: dict = {}
    player = threading.Thread(target=lambda: rows.setdefault(
        "a", _play(_executor(manager, episode_seconds=120), "a")))
    player.start()
    while not manager.instances:
        time.sleep(0.01)
    began = time.monotonic()
    report = manager.stop()
    player.join(10)
    assert not player.is_alive() and time.monotonic() - began < 5
    assert (rows["a"]["status"], rows["a"]["reason"]) == (
        "unknown", "fast_chain_unknown:fast_env_exited_without_close")
    assert report["cleanup_confirmed"] is True and not _native_alive(tmp_path)


def test_m2_save_start_lineage_is_checked_like_the_old_chain(tmp_path):
    manager = _manager(tmp_path, 1, [])
    changed = _save_start("a")
    # A consistent save whose progress is not this actor's configured progress.
    changed["lineage"]["initial_progress_sha256"] = "other"
    changed["parent_identity"]["initial_progress"]["progress_sha256"] = "other"
    with pytest.raises(BoundaryError, match="loaded_initial_progress_lineage_changed"):
        _executor(manager, starts={"a": changed})
    parent = _save_start("a")
    parent["parent_identity"]["episode_provenance"]["actual_seed"] = "b"
    with pytest.raises(BoundaryError, match="save_parent_identity_changed"):
        _executor(manager, starts={"a": parent})
    assert _commands(tmp_path) == []
    manager.stop()


def test_m3_opened_without_start_is_refused_on_arrival(tmp_path):
    games = [{"close": {"reason": "terminal", "terminal": {"result": "victory"}},
              "after": 30, "echo_start": None}]
    manager = _manager(tmp_path, 1, games)
    began = time.monotonic()
    row = _play(_executor(manager, episode_seconds=60), "a")
    # Not at the close 30 s later: close_slot goes out as soon as ``opened`` lands.
    assert time.monotonic() - began < 5
    assert (row["status"], row["reason"]) == ("unknown", "first_frame_start_changed")
    commands = _commands(tmp_path)
    assert [c["cmd"] for c in commands] == ["open", "close_slot"]
    # close_slot names the instance it means (the environment ignores any other).
    assert commands[1]["game_instance_id"] == row["game_instance_id"]
    assert manager.counters["start_mismatch_close_slot"] == 1
    manager.stop()


def test_m4_offer_hook_failures_return_the_slot(tmp_path):
    manager = _manager(tmp_path, 1, [])

    def broken_admission(row):
        raise KeyError("intent_id")

    def broken_watcher(row):
        raise RuntimeError("watcher down")

    first = _play(_executor(manager, before=broken_admission), "a")
    assert (first["status"], first["reason"], first["world_mutation_offered"]) == (
        "not_started", "KeyError", False)
    assert manager.free == {0}
    second = _play(_executor(manager, offered=broken_watcher), "b")
    assert (second["status"], second["reason"]) == (
        "unknown", "fast_chain_world_offer_event_failed:RuntimeError")
    assert manager.free == {0} and _commands(tmp_path) == []
    assert _play(_executor(manager), "c")["status"] == "finished"
    manager.stop()


def test_fast_chain_refuses_nonzero_ascension_before_any_offer(tmp_path, capsys):
    manager = _manager(tmp_path, 1, [])
    offered: list[str] = []
    row = {"seed": "a", "intent_id": "intent-a", "selection_id": "model"}
    _executor(manager, before=lambda r: offered.append(r["seed"]))(
        row, {"character": "Defect", "ascension": 10}, _Artifacts(), lambda: None)
    assert (row["status"], row["reason"], row["world_mutation_offered"]) == (
        "not_started", "fast_chain_ascension_unsupported:10", False)
    assert offered == [] and _commands(tmp_path) == [] and manager.free == {0}
    assert "refuses ascension 10" in capsys.readouterr().err
    manager.stop()


def test_fast_chain_admits_only_the_env_process_ascension(tmp_path, capsys):
    manager = _manager(tmp_path, 1, [])
    manager.config["host"]["ascension"] = 5
    row = {"seed": "a", "intent_id": "intent-a", "selection_id": "model"}
    _executor(manager)(row, {"character": "Defect", "ascension": 0}, _Artifacts(), lambda: None)
    assert (row["status"], row["reason"]) == ("not_started", "fast_chain_ascension_unsupported:0")
    assert "this env process serves 5" in capsys.readouterr().err
    del manager.config["host"]["ascension"]  # never a default level
    row = {"seed": "b", "intent_id": "intent-b", "selection_id": "model"}
    _executor(manager)(row, {"character": "Defect", "ascension": 0}, _Artifacts(), lambda: None)
    assert row["reason"] == "fast_chain_ascension_unsupported:0"
    assert _commands(tmp_path) == [] and manager.free == {0}
    manager.stop()


def test_inference_streak_without_probe_unlocks_for_one_trial_after_retry(tmp_path):
    timeout = {"close": {"reason": "not_started", "detail": "inference_timeout"}}
    manager = _manager(tmp_path, 1, [timeout, timeout, timeout], streak=2)
    now = [0.0]
    manager.clock, manager.inference_retry = (lambda: now[0]), 60
    executor = _executor(manager)
    _play(executor, "a")
    _play(executor, "b")
    assert manager.admission_closed() == "inference_service_unavailable_before_offer"
    now[0] = 61
    assert manager.admission_closed() is None  # one trial game
    _play(executor, "c")  # it times out too: locked again at once
    assert manager.admission_closed() == "inference_service_unavailable_before_offer"
    assert manager.counters["inference_retry_trials"] == 1
    manager.stop()


# -- slice 3: learner instance admission and unknown categories --------------------------
class _FakeLearner:
    """The learner side of ``fast_instance_admission`` (owned by C-SCALE, WP8)."""

    def __init__(self, reply=None, error=None):
        self.calls: list[tuple[str, dict]] = []
        self.reply, self.error = reply or {"admitted": True}, error

    def request(self, operation, **fields):
        self.calls.append((operation, fields))
        if self.error is not None:
            raise self.error
        return self.reply


def _owner(row):
    row["segment_admission_owner"] = {"run": "run-1", "training_generation": "gen-1",
                                      "budget_id": "b"}


def _admitting(manager, learner, **kwargs):
    from functools import partial

    from spireagent.workbench.rl_segment_producer import fast_instance_admission

    return _executor(manager, before=_owner, **kwargs, instance_opened=partial(
        fast_instance_admission, admission=learner, actor="actor-1"))


def test_instance_admission_is_sent_once_after_opened_with_the_exact_fields(tmp_path):
    from spireagent.workbench.rl_segment_producer import FAST_INSTANCE_ADMISSION_FIELDS

    manager = _manager(tmp_path, 1, [{"close": {"reason": "terminal",
                                                "terminal": {"result": "victory"}}}])
    learner = _FakeLearner()
    row = _play(_admitting(manager, learner), "a")
    assert row["status"] == "finished"
    assert len(learner.calls) == 1
    operation, fields = learner.calls[0]
    assert operation == "fast_instance_admission"
    assert tuple(fields) == FAST_INSTANCE_ADMISSION_FIELDS
    assert fields == {"actor": "actor-1", "intent_id": "intent-a", "seed": "a",
                      "game_instance_id": row["game_instance_id"],
                      "training_split": "TRAIN", "run": "run-1",
                      "training_generation": "gen-1"}
    assert row["fast_instance_admission"]["admitted"] is True
    manager.stop()


@pytest.mark.parametrize("learner,reason", [
    (_FakeLearner(error=EOFError()),
     "fast_chain_instance_admission_failed:fast_instance_admission_delivery_failed"),
    (_FakeLearner(reply={"admitted": False, "reason": "holdout_seed"}),
     "fast_chain_instance_admission_failed:fast_instance_admission_refused:holdout_seed"),
    (_FakeLearner(reply={"stopped": True, "reason": "Not A Code!"}),
     "fast_chain_instance_admission_failed:fast_instance_admission_refused:unspecified"),
])
def test_instance_admission_failure_closes_the_game_and_is_never_resent(
        tmp_path, learner, reason):
    games = [{"close": {"reason": "terminal", "terminal": {"result": "victory"}},
              "after": 30},
             {"close": {"reason": "terminal", "terminal": {"result": "defeat"}}}]
    manager = _manager(tmp_path, 1, games)
    began = time.monotonic()
    row = _play(_admitting(manager, learner, episode_seconds=60), "a")
    assert time.monotonic() - began < 5  # closed now, not at the game's own end
    assert (row["status"], row["reason"]) == ("censored", reason)
    assert len(learner.calls) == 1
    assert [c["cmd"] for c in _commands(tmp_path)] == ["open", "close_slot"]
    assert manager.counters["instance_admission_close_slot"] == 1
    # The slot came back once and the next game runs on it.
    assert manager.free == {0}
    learner.error, learner.reply = None, {"admitted": True}
    assert _play(_admitting(manager, learner), "b")["status"] == "finished"
    manager.stop()


def test_a_close_slot_never_reaches_the_next_game_on_the_same_slot(tmp_path):
    # The game ends on its own while its admission is failing: no close_slot is
    # written for a closed game (and one being written holds the slot), so the
    # following game on the same slot is never closed by it.
    games = [{"close": {"reason": "terminal", "terminal": {"result": "victory"}},
              "after": 0},
             {"close": {"reason": "terminal", "terminal": {"result": "defeat"}},
              "after": 0.3}]
    manager = _manager(tmp_path, 1, games)

    class Slow(_FakeLearner):
        def request(self, operation, **fields):
            time.sleep(0.3)
            raise EOFError()

    first = _play(_admitting(manager, Slow()), "a")
    assert first["reason"].startswith("fast_chain_instance_admission_failed:")
    second = _play(_executor(manager), "b")
    assert (second["status"], second.get("observed_result")) == ("finished", "defeat")
    assert [c["cmd"] for c in _commands(tmp_path)] == ["open", "open"]
    manager.stop()


@pytest.mark.parametrize("close,category,rule", [
    ({"reason": "unknown", "detail": "delivery_unknown", "native": "replaced"},
     "environment_gap", "fast_chain_unknown:delivery_unknown"),
    ({"reason": "terminal", "terminal": None, "native": "kept",
      "opened": {"start": {"kind": "new_run"}}},
     "environment_gap", "fast_chain_terminal_without_result"),
    ({"reason": "terminal", "terminal": {"result": "victory"}, "native": "kept"},
     "environment_gap", "first_frame_start_changed"),
    ({"reason": "env_crash", "detail": "native_crash", "native": "replaced"},
     "infrastructure", "fast_chain_env_crash"),
    ({"reason": "unknown", "detail": "native_crash", "native": "replaced"},
     "infrastructure", "fast_chain_unknown:native_crash"),
    ({"reason": "unknown", "detail": "stop_latency_bound_exceeded", "synthetic": True},
     "infrastructure", "fast_chain_unknown:stop_latency_bound_exceeded"),
    ({"reason": "unknown", "detail": "env_crash:fast_env_exited:3", "synthetic": True},
     "infrastructure", "fast_chain_unknown:env_crash"),
    ({"reason": "weird"}, "infrastructure", "fast_chain_close_reason_invalid"),
    ({"reason": "unknown", "detail": "delivery_unknown:receipt_delivery_unknown:none",
      "native": "replaced"}, "environment_gap", "fast_chain_unknown:delivery_unknown"),
    ({"reason": "unknown", "detail": "delivery_unknown:host_step_failed:EPIPE",
      "native": "replaced"}, "environment_gap", "fast_chain_unknown:delivery_unknown"),
    ({"reason": "unknown", "detail": "delivered_successor_unreadable:capture_capacity",
      "native": "replaced"}, "environment_gap",
     "fast_chain_unknown:delivered_successor_unreadable"),
    ({"reason": "unknown",
      "detail": "delivered_successor_unreadable:managed_settling_stalled",
      "native": "replaced"}, "environment_gap",
     "fast_chain_unknown:delivered_successor_unreadable"),
    ({"reason": "unknown", "detail": "compact_state_divergence", "native": "replaced"},
     "infrastructure", "fast_chain_unknown:compact_state_divergence"),
    ({"reason": "unknown", "detail": "projection_memo_divergence", "native": "replaced"},
     "infrastructure", "fast_chain_unknown:projection_memo_divergence"),
    ({"reason": "unknown", "detail": "something_new", "native": "replaced"},
     "unclassified", "no_classifying_fact"),
])
def test_fast_chain_unknowns_have_a_typed_category(close, category, rule):
    from stpd.evaluation.unknown_category import classify_unknown

    row: dict = {}
    apply_close(row, close, start=EpisodeStart.new_run("seed"))
    assert row["status"] == "unknown"
    value = classify_unknown(row["status"], row["reason"], row.get("failure_facts"))
    assert (value["category"], value["rule"]) == (category, rule)


def test_offer_event_failure_is_typed_infrastructure():
    from stpd.evaluation.unknown_category import classify_unknown

    value = classify_unknown("unknown", "fast_chain_world_offer_event_failed:RuntimeError")
    assert (value["category"], value["rule"]) == (
        "infrastructure", "fast_chain_world_offer_event_failed")


# -- review fixes (REVIEW-PR426-427) -----------------------------------------------------
def test_s1_a_slot_closing_while_its_close_slot_is_written_is_not_reused_until_written(
        tmp_path):
    # Two threads: the game closes on its own while its close_slot write is blocked.
    # The slot must stay held (no reserve succeeds) until the write returns; without
    # ``hold_slot`` the reserve below gets the slot and this test fails.
    games = [{"close": {"reason": "terminal", "terminal": {"result": "victory"}},
              "after": 0.3},
             {"close": {"reason": "terminal", "terminal": {"result": "defeat"}}}]
    manager = _manager(tmp_path, 1, games)
    assert manager.process is not None
    real_send = manager.process.send
    writing, release = threading.Event(), threading.Event()

    def blocking_send(command):
        if command["cmd"] == "close_slot":
            writing.set()
            assert release.wait(10)
        real_send(command)

    manager.process.send = blocking_send  # type: ignore[method-assign]

    def failing(row, opened):
        raise BoundaryError("rl_segment", "fast_instance_admission_delivery_failed")

    rows: dict = {}
    player = threading.Thread(target=lambda: rows.setdefault("a", _play(
        _executor(manager, before=_owner, instance_opened=failing), "a")))
    player.start()
    assert writing.wait(5)
    deadline = time.monotonic() + 5
    while not any(g.closed is not None for g in manager.instances.values()):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    with pytest.raises(FastEnvUnavailable, match="no_free_slot"):
        manager.reserve(0.3)
    release.set()
    player.join(10)
    assert not player.is_alive()
    assert rows["a"]["reason"].startswith("fast_chain_instance_admission_failed:")
    second = _play(_executor(manager), "b")
    assert (second["status"], second.get("observed_result")) == ("finished", "defeat")
    assert [c["cmd"] for c in _commands(tmp_path)] == ["open", "close_slot", "open"]
    manager.stop()


def test_open_keeps_originals_only_for_evaluation_and_r7_never_a_training_sample(
        tmp_path):
    """CARD-RL5 §12.6: no whole-game sample of ordinary fast-chain training games."""
    from spireagent.workbench.fast_chain_episodes import retain_originals

    assert retain_originals({"retention": {"purpose": "evaluation"}}) is True
    # Even a row that carries the old chain's 1/audit_every pick keeps no originals.
    assert retain_originals({"retention": {"purpose": "training"},
                             "training_audit_selected": True}) is False
    assert retain_originals({"retention": {"purpose": "training"},
                             "training_audit_selected": False}) is False
    manager = _manager(tmp_path, 1, [])

    def evaluation(row):
        row.update(retention={"purpose": "evaluation"})

    def training(row):
        row.update(retention={"purpose": "training"}, training_audit_selected=True)

    picks = iter([True, False, False])
    for name, admission in (("a", training), ("b", evaluation), ("c", training)):
        executor = FastChainEpisodeExecutor(
            {"initial_progress": None}, manager=manager, episode_seconds=30,
            before_world_offer=admission,
            r7_select=lambda: {"selected": next(picks)}, reserve_seconds=5)
        _play(executor, name)
    opens = [c for c in _commands(tmp_path) if c["cmd"] == "open"]
    assert [(c["retain_originals"], c["r7"]) for c in opens] == [
        (False, True), (True, False), (False, False)]
    manager.stop()


# -- R7 selection (R7Sampler wired into the fast chain) -------------------------------
class _Clock:
    def __init__(self, now: float = 7200.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _r7(tmp_path: Path, clock, **overrides):
    from spireagent.workbench.fast_chain_episodes import R7Selection

    return R7Selection.from_configuration(
        {"key": "r7-test-key", "quota": 3, "expected_games": 10, **overrides},
        work=tmp_path, clock=clock)


def test_r7_selection_is_the_sampler_choice_seeded_and_recorded(tmp_path):
    from stpd.rl.r7_audit import R7Sampler

    clock = _Clock()
    selection = _r7(tmp_path, clock)
    reference = R7Sampler(key="r7-test-key", quota=3, expected_games=10)
    decisions = [selection() for _ in range(10)]
    assert [d["selected"] for d in decisions] == [reference.select(7200.0)
                                                  for _ in range(10)]
    assert [d["ordinal"] for d in decisions] == list(range(10))
    assert {d["window"] for d in decisions} == {2}
    assert sum(d["selected"] for d in decisions) == 3
    state = json.loads((tmp_path / "fast-chain-r7-state.json").read_text())
    assert (state["window"], state["opened"]) == (2, 10)
    assert "r7-test-key" not in (tmp_path / "fast-chain-r7-state.json").read_text()
    report = selection.report()
    assert report["sampler_ordinals"]["current"]["chosen_so_far"] == 3
    assert report["resume"] is None and report["r7_games"] == {}


def test_r7_selection_restart_continues_the_window_and_never_resumes_a_stale_one(tmp_path):
    clock = _Clock()
    whole = [_r7(tmp_path / "whole", clock)()["selected"] for _ in range(10)]
    first = _r7(tmp_path / "split", clock)
    picks = [first()["selected"] for _ in range(4)]
    restarted = _r7(tmp_path / "split", clock)
    assert restarted.report()["resume"] == {"resumed": True, "window": 2, "opened": 4}
    picks += [restarted()["selected"] for _ in range(6)]
    assert picks == whole  # a restart does not change which ordinals are R7
    # A later window, or other sampler parameters: not resumed (coverage only).
    clock.now += 3600
    later = _r7(tmp_path / "split", clock)
    assert later.report()["resume"]["resumed"] is False
    assert later()["ordinal"] == 0
    other = _r7(tmp_path / "split", clock, quota=1)
    assert other.report()["resume"] == {"resumed": False, "state_window": 3, "window": 3,
                                        "same_parameters": False}


def test_r7_selection_refuses_bad_configuration_and_unreadable_state(tmp_path):
    from spireagent.workbench.fast_chain_episodes import R7Selection

    for bad in ({"quota": 1, "expected_games": 10}, {"key": "k", "quota": 11,
                "expected_games": 10}, {"key": "k", "quota": "1", "expected_games": 10},
                {"key": "k", "quota": 1, "expected_games": 10, "extra": 1},
                {"key": "", "quota": 1, "expected_games": 10}, "k"):
        with pytest.raises(BoundaryError) as error:
            R7Selection.from_configuration(bad, work=tmp_path)
        assert error.value.code == "fast_chain_r7_configuration_invalid"
    (tmp_path / "fast-chain-r7-state.json").write_text("{not json")
    with pytest.raises(BoundaryError) as error:
        _r7(tmp_path, _Clock())
    assert error.value.code == "fast_chain_r7_state_invalid"


def test_r7_is_decided_before_admission_and_is_durable_with_the_offer(tmp_path):
    manager = _manager(tmp_path, 1, [])
    order: list[str] = []
    selection = _r7(tmp_path / "r7", _Clock(), quota=10)  # every game is R7

    def select():
        order.append("r7")
        return selection()

    select.report, select.count = selection.report, selection.count
    executor = FastChainEpisodeExecutor(
        {"initial_progress": None}, manager=manager, episode_seconds=30,
        before_world_offer=lambda row: order.append("admission"), r7_select=select,
        reserve_seconds=5)
    checkpoints: list[dict] = []
    row = _play(executor, "a", checkpoints)
    assert order == ["r7", "admission"]
    offered = next(c for c in checkpoints if c["world_mutation_offered"])
    assert offered["r7"] == {"selected": True, "window": 2, "ordinal": 0}
    assert row["r7"]["selected"] is True
    assert [c["r7"] for c in _commands(tmp_path) if c["cmd"] == "open"] == [True]
    closed = executor.close()
    assert closed["r7_sampler"]["sampler_ordinals"]["current"]["opened"] == 1
    assert closed["r7_sampler"]["r7_games"] == {"offered": 1, "opened": 1,
                                                "status:finished": 1}


def test_r7_select_failure_is_not_started_before_any_offer_and_returns_the_slot(tmp_path):
    manager = _manager(tmp_path, 1, [])
    admitted: list[str] = []

    def broken():
        raise ValueError("r7_sampler_clock_went_backwards")

    executor = FastChainEpisodeExecutor(
        {"initial_progress": None}, manager=manager, episode_seconds=30,
        before_world_offer=lambda row: admitted.append(row["seed"]), r7_select=broken,
        reserve_seconds=5)
    row = _play(executor, "a")
    assert (row["status"], row["reason"], row["world_mutation_offered"]) == (
        "not_started", "fast_chain_r7_select_failed:r7_sampler_clock_went_backwards", False)
    executor.r7_select = lambda: {"selected": "yes"}
    assert _play(executor, "b")["reason"] == (
        "fast_chain_r7_select_failed:r7_selected_not_bool")
    assert admitted == [] and _commands(tmp_path) == []
    # The slot came back: an unwired executor plays the next game, recorded as not R7.
    second = _play(_executor(manager), "c")
    assert second["status"] == "finished"
    assert second["r7"] == {"selected": False, "sampler": None}
    assert [c["r7"] for c in _commands(tmp_path) if c["cmd"] == "open"] == [False]
    manager.stop()


def test_actor_fast_chain_configuration_builds_one_r7_selection(tmp_path):
    from spireagent.workbench.fast_chain_episodes import R7Selection
    from spireagent.workbench.rl_actor import Actor

    env = tmp_path / "env.json"
    env.write_text(json.dumps(_env(2)))
    retention = {"tier": "trajectory", "purpose": "training", "audit_every": 20}
    fake = SimpleNamespace(segment_mode=True, work=tmp_path, base={"retention": retention},
                           config={"pipeline": {"save_workers": 1, "pending_limit": 2}})
    base = {"command": ["fast-env"], "env_config": str(env)}
    assert Actor._fast_chain_configuration(fake, base)["r7"] is None
    wired = Actor._fast_chain_configuration(
        fake, {**base, "r7": {"key": "k", "quota": 2, "expected_games": 40}})
    assert isinstance(wired["r7"], R7Selection)
    assert wired["r7"].state_path == tmp_path / "fast-chain-r7-state.json"
    with pytest.raises(BoundaryError) as error:
        Actor._fast_chain_configuration(fake, {**base, "r7": {"key": "k"}})
    assert error.value.code == "fast_chain_r7_configuration_invalid"

    # CARD-RL5 §12.6: the fast chain draws no whole-game sample, so the execution's
    # audit_every (the old chain's rule) is neither required nor refused.
    for every in (1, 20, 100):
        fake.base = {"retention": {**retention, "audit_every": every}}
        assert Actor._fast_chain_configuration(fake, base)["r7"] is None


def test_r7_coverage_counts_games_that_ran_not_ordinals_burned_before_admission(tmp_path):
    manager = _manager(tmp_path, 1, [])
    selection = _r7(tmp_path / "r7", _Clock(), quota=10)  # every ordinal is chosen
    refuse = iter([True, False])

    def admission(row):
        if next(refuse):
            raise BoundaryError("rl_segment", "learner_stopped_before_world_offer")

    executor = FastChainEpisodeExecutor(
        {"initial_progress": None}, manager=manager, episode_seconds=30,
        before_world_offer=admission, r7_select=selection, reserve_seconds=5)
    assert _play(executor, "a")["status"] == "not_started"
    assert _play(executor, "b")["status"] == "finished"
    report = executor.close()["r7_sampler"]
    assert report["sampler_ordinals"]["current"]["chosen_so_far"] == 2  # ordinals
    assert report["r7_games"] == {"offered": 1, "opened": 1, "status:finished": 1}


def test_evidence_failed_close_is_censored_excluded_and_counted(tmp_path):
    games = [
        {"seed": "flag", "close": {"reason": "terminal", "terminal": {"result": "victory"},
                                   "evidence_failed": True, "evidence_dir": None}},
        {"seed": "event", "evidence_event": "disk_full",
         "close": {"reason": "terminal", "terminal": {"result": "defeat"}}},
        {"seed": "bad", "close": {"reason": "terminal", "terminal": {"result": "defeat"},
                                  "evidence_failed": "yes"}},
        {"seed": "absent", "close": {"reason": "terminal", "terminal": {"result": "defeat"}}},
    ]
    manager = _manager(tmp_path, 1, games)
    executor = _executor(manager)
    flag, event, bad, absent = (_play(executor, s) for s in ("flag", "event", "bad", "absent"))
    assert (flag["status"], flag["reason"], flag["training_excluded"]) == (
        "censored", "fast_chain_evidence_failed:terminal", True)
    assert "observed_result" not in flag
    assert flag["evidence_failed"] == {"close_flag": True, "event": None, "evidence_dir": None}
    assert (event["status"], event["evidence_failed"]["event"]) == ("censored", "disk_full")
    assert (bad["status"], bad["reason"]) == ("unknown", "fast_chain_close_evidence_failed_invalid")
    assert absent["status"] == "finished" and "evidence_failed" not in absent
    report = executor.close()["fast_chain_stop"]["counters"]
    assert report["evidence_failed_games"] == 2 and report["evidence_failed_events"] == 1


def test_evidence_failed_on_an_unknown_close_stays_unknown_and_is_recorded():
    row: dict = {}
    apply_close(row, {"reason": "unknown", "detail": "delivery_unknown", "native": "replaced",
                      "evidence_failed": True, "evidence_dir": None},
                start=EpisodeStart.new_run("a"))
    assert row["status"] == "unknown" and row["evidence_failed"]["close_flag"] is True


# -- WP8: exclusion request after an evidence failure ------------------------------------
class _ExcludingLearner(_FakeLearner):
    """Admits every instance; answers ``fast_instance_excluded`` with ``exclude``."""

    def __init__(self, exclude=None, error=None):
        super().__init__()
        self.exclude, self.exclude_error = exclude, error

    def request(self, operation, **fields):
        if operation != "fast_instance_excluded":
            return super().request(operation, **fields)
        self.calls.append((operation, fields))
        if self.exclude_error is not None:
            raise self.exclude_error
        return (self.exclude if self.exclude is not None
                else {"excluded": True, "game_instance_id": fields["game_instance_id"]})


def _excluding(manager, learner):
    from functools import partial

    from spireagent.workbench.rl_segment_producer import (
        fast_instance_admission,
        fast_instance_excluded,
    )

    return _executor(manager, before=_owner, instance_opened=partial(
        fast_instance_admission, admission=learner, actor="actor-1"),
        instance_excluded=partial(fast_instance_excluded, admission=learner,
                                  actor="actor-1"))


def test_evidence_failed_game_sends_one_exclusion_with_the_exact_fields(tmp_path):
    from spireagent.workbench.rl_segment_producer import FAST_INSTANCE_EXCLUDED_FIELDS

    games = [
        {"seed": "flag", "close": {"reason": "terminal", "terminal": {"result": "victory"},
                                   "evidence_failed": True, "evidence_dir": None}},
        {"seed": "event", "evidence_event": "disk_full",
         "close": {"reason": "terminal", "terminal": {"result": "defeat"}}},
        {"seed": "clean", "close": {"reason": "terminal", "terminal": {"result": "defeat"}}},
    ]
    manager = _manager(tmp_path, 1, games)
    learner = _ExcludingLearner()
    executor = _excluding(manager, learner)
    flag, event, clean = (_play(executor, s) for s in ("flag", "event", "clean"))
    excluded = [fields for op, fields in learner.calls if op == "fast_instance_excluded"]
    assert [tuple(f) for f in excluded] == [FAST_INSTANCE_EXCLUDED_FIELDS] * 2
    assert excluded == [{"actor": "actor-1", "game_instance_id": row["game_instance_id"],
                         "reason": "evidence_failed", "run": "run-1",
                         "training_generation": "gen-1"} for row in (flag, event)]
    for row in (flag, event):
        assert row["fast_instance_excluded"] == {
            "excluded": True, "game_instance_id": row["game_instance_id"],
            "reason": "evidence_failed"}
        assert row["training_excluded"] is True
    assert "fast_instance_excluded" not in clean and clean["status"] == "finished"
    counters = executor.close()["fast_chain_stop"]["counters"]
    assert counters["fast_instance_excluded_sent"] == 2
    assert counters.get("fast_instance_excluded_failed", 0) == 0


@pytest.mark.parametrize("learner,code", [
    (_ExcludingLearner(error=EOFError()), "fast_instance_excluded_delivery_failed"),
    (_ExcludingLearner(exclude={"excluded": False, "reason": "generation_changed"}),
     "fast_instance_excluded_refused:generation_changed"),
    (_ExcludingLearner(exclude={"excluded": True, "game_instance_id": "another"}),
     "fast_instance_excluded_refused:unspecified"),
])
def test_failed_exclusion_is_recorded_counted_and_never_resent(tmp_path, learner, code,
                                                              capsys):
    games = [{"close": {"reason": "terminal", "terminal": {"result": "victory"},
                        "evidence_failed": True, "evidence_dir": None}}]
    manager = _manager(tmp_path, 1, games)
    executor = _excluding(manager, learner)
    row = _play(executor, "a")
    assert row["fast_instance_excluded"] == {"excluded": False, "error": code}
    assert (row["status"], row["training_excluded"]) == ("censored", True)
    assert [op for op, _ in learner.calls].count("fast_instance_excluded") == 1
    assert "exclusion request failed" in capsys.readouterr().err
    counters = executor.close()["fast_chain_stop"]["counters"]
    assert counters["fast_instance_excluded_failed"] == 1
    assert counters.get("fast_instance_excluded_sent", 0) == 0


def test_learner_admission_samples_whole_games_only_on_the_old_chain():
    """The fast chain records no 1/audit_every pick; the old chain's 5% is unchanged."""
    from spireagent.workbench.managed_retention import training_audit_selected
    from spireagent.workbench.rl_segment_producer import open_learner_episode

    exchange = SimpleNamespace(admission_pause=lambda budget: None)
    admission = SimpleNamespace(request=lambda *a, **k: {"admitted": True, "run": "r"})
    base = {"selection_id": "s", "manifest_sha256": "m", "intent_id": "i",
            "retention": {"purpose": "training", "audit_every": 20}}
    picked = next(str(n) for n in range(1000) if training_audit_selected(
        {**base, "seed": str(n)}, audit_every=20))
    old = {**base, "seed": picked}
    open_learner_episode(old, exchange=exchange, admission=admission, actor="a",
                         budget_id=None)
    assert old["training_audit_selected"] is True
    fast = {**base, "seed": picked}
    open_learner_episode(fast, exchange=exchange, admission=admission, actor="a",
                         budget_id=None, whole_game_sample=False)
    assert fast["training_audit_selected"] is False
    assert fast["segment_episode_admitted"] is True


def test_r7_max_games_caps_the_run_loudly_and_survives_a_restart(tmp_path, capsys):
    """The first bound on the local R7 mu archive: no silent drop, a typed record."""
    clock = _Clock()
    selection = _r7(tmp_path, clock, quota=10, max_games=2)  # every ordinal is chosen
    records = [selection() for _ in range(4)]
    assert [r["selected"] for r in records] == [True, True, False, False]
    assert [r.get("capped") for r in records] == [None, None, True, True]
    assert capsys.readouterr().err.count("max_games 2 reached") == 1
    report = selection.report()
    assert (report["selected_total"], report["max_games"]) == (2, 2)
    assert report["r7_games"]["capped"] == 2
    clock.now += 7200  # a later window of the same run: the cap still holds
    restarted = _r7(tmp_path, clock, quota=10, max_games=2)
    assert restarted() == {"selected": False, "window": restarted.sampler.window,
                           "ordinal": 0, "capped": True}
    with pytest.raises(BoundaryError) as error:
        _r7(tmp_path / "bad", clock, max_games=-1)
    assert error.value.code == "fast_chain_r7_configuration_invalid"
