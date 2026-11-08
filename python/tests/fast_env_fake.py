"""A scripted stand-in for the WP2 fast-env process (NDJSON on stdin/stdout).

``python fast_env_fake.py <scenario.json>``. Each ``open`` takes the next entry of
``scenario["games"]``; every command is appended to ``scenario["log"]``. A sleeping child
process stands in for the native dotnet children (same process group).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid

with open(sys.argv[1], "rb") as source:
    scenario = json.loads(source.read())
lock = threading.Lock()
games = iter(scenario.get("games", []))
open_games: dict[int, dict] = {}
native = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
with open(scenario["log"] + ".native", "w") as handle:
    handle.write(str(native.pid))


def emit(event: dict) -> None:
    with lock:
        sys.stdout.write(json.dumps({"schema": "sts2.policy-runtime/fast-env-event-1",
                                     **event}) + "\n")
        sys.stdout.flush()


def log(command: dict) -> None:
    with open(scenario["log"], "a") as handle:
        handle.write(json.dumps(command) + "\n")


def close(slot: int, **fields) -> None:
    game = open_games.pop(slot, None)
    if game is None:
        return
    emit({"event": "closed", "slot": slot, "game_ref": game["game_ref"],
          "game_instance_id": game["game_instance_id"], "decisions": 3, "native_steps": 5,
          "last_step_index": 2, "terminal": None, "public_facts": None, "native": "kept",
          "evidence_dir": "/evidence/" + game["game_instance_id"], **fields})


def later(seconds: float, action) -> None:
    timer = threading.Timer(seconds, action)
    timer.daemon = True
    timer.start()


emit({"event": "ready", "env_process_id": scenario["env_process_id"],
      "slots": scenario["slots"], "pid": 1, "identity": {}})
for raw in sys.stdin:
    command = json.loads(raw)
    log(command)
    if command["cmd"] == "open":
        plan = next(games, {"close": {"reason": "terminal",
                                      "terminal": {"result": "defeat"}}, "after": 0.05})
        if "refuse" in plan:
            emit({"event": "open_refused", "command_id": command["command_id"],
                  "slot": command["slot"], "game_ref": command["game_ref"],
                  "reason": plan["refuse"]})
            continue
        instance = "gi-" + uuid.uuid4().hex
        game = {"game_ref": command["game_ref"], "game_instance_id": instance,
                "plan": plan}
        open_games[command["slot"]] = game
        start = plan.get("echo_start", command["start"])
        emit({"event": "opened", "command_id": command["command_id"],
              "slot": command["slot"], "game_ref": command["game_ref"],
              "game_instance_id": instance, "start": start, "native": "reused",
              "mount_ms": 1})
        slot = command["slot"]
        if "close" in plan:
            def finish(slot=slot, plan=plan, game=game):
                close(slot, **plan["close"])
                if plan.get("late_close"):
                    emit({"event": "closed", "slot": slot, "game_ref": game["game_ref"],
                          "game_instance_id": game["game_instance_id"],
                          "reason": "terminal", "detail": None, "native": "kept",
                          "terminal": {"result": "victory"}})
            later(plan.get("after", 0.05), finish)
        if "exit_after" in plan:
            later(plan["exit_after"], lambda: os._exit(3))  # a crash, children left over
    elif command["cmd"] == "close_slot":
        game = open_games.get(command["slot"])
        if game is not None and not game["plan"].get("ignore_close_slot"):
            close(command["slot"], reason="stop", detail="stop_before_send")
    elif command["cmd"] == "stop":
        if scenario.get("stop_ignore"):
            continue
        time.sleep(scenario.get("stop_delay", 0))
        for slot in list(open_games):
            if not open_games[slot]["plan"].get("survive_stop"):  # a contract breach
                close(slot, reason="stop", detail="stop_before_send")
        emit({"event": "stopped", "games_closed": 0})
        native.kill()
        native.wait()
        sys.exit(0)
