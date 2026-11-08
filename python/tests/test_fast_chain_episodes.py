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
            "host": {"request_timeout_ms": request_ms}}


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
              before=None, offered=None) -> FastChainEpisodeExecutor:
    return FastChainEpisodeExecutor(
        {"initial_progress": {"progress_sha256": "progress"},
         "episode_starts": starts or {}}, manager=manager, episode_seconds=episode_seconds,
        before_world_offer=before, world_offered=offered, reserve_seconds=5)


def _play(executor: FastChainEpisodeExecutor, seed: str, checkpoints=None) -> dict:
    row = {"seed": seed, "intent_id": "intent-" + seed, "selection_id": "model"}

    def checkpoint() -> None:
        if checkpoints is not None:
            checkpoints.append(dict(row))

    executor(row, dict(MANIFEST), _Artifacts(), checkpoint)
    return row


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
    time.sleep(0.5)
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
    time.sleep(0.5)
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
    games = [{"close": {"reason": "unknown", "detail": "native_crash",
                        "native": "replaced"}, "after": 0.1},
             {"close": {"reason": "terminal", "terminal": {"result": "defeat"}},
              "after": 0.6},
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
    games = [{"ignore_close_slot": True},
             {"close": {"reason": "terminal", "terminal": {"result": "defeat"}},
              "after": 2.5}]
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
    games = [{"exit_after": 0.3}, {}]
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
                        "ascension": 0, "parent_intent_id": "p"}}


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
    ({"reason": "terminal", "terminal": None, "native": "kept"},
     ("unknown", "fast_chain_terminal_without_result")),
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
    actor.segment_mode = True
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
