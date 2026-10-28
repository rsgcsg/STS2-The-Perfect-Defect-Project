"""One command for one node: start the learner, then N CPU actors, then stop cleanly.

Actors on other machines run ``python -m spireagent.workbench.rl_actor`` with
the same ``run`` and peer; this launcher only owns the processes it starts.
The finished run writes ``time-ledger.json`` from the per-process summaries.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench.rl_exchange import Exchange

CONFIG_SCHEMA = "stpd/rl-run-1"


def _write(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))
    return path


def launch(config: dict[str, Any], work: Path) -> dict[str, Any]:
    if config.get("schema") != CONFIG_SCHEMA:
        raise BoundaryError("rl_run", "unsupported_run_config")
    python = config.get("python", sys.executable)
    from spireagent.workbench.service_process import cloud_environment

    # R2 credentials reach child processes only, from an explicit private file.
    secret = config.get("cloud_environment")
    environment = {**os.environ, **cloud_environment(Path(secret) if secret else None)}
    run, peer = config["run"], config["peer"]
    learner_work = work / "learner"
    learner_config = _write(work / "configs" / "learner.json", {
        "schema": "stpd/rl-learner-config-1", "run": run, "peer": peer,
        "wall_seconds": config["wall_seconds"], **config["learner"]})
    logs = work / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    learner = subprocess.Popen(
        [python, "-m", "stpd.rl.learner", "--config", str(learner_config),
         "--work", str(learner_work)],
        stdout=(logs / "learner.out").open("wb"), stderr=subprocess.STDOUT, env=environment)
    os.environ.update({k: v for k, v in environment.items() if k not in os.environ})
    exchange = Exchange(peer, run)
    while exchange.latest_model() is None:
        if learner.poll() is not None:
            raise BoundaryError("rl_run", "learner_exited_before_version_zero")
        time.sleep(0.5)
    first_version_seconds = time.monotonic() - began
    actors = {}
    for spec in config["actors"]:
        path = _write(work / "configs" / f"actor-{spec['actor']}.json", {
            "schema": "spireagent/rl-actor-config-1", "run": run, "peer": peer,
            "seed_namespace": config["seed_namespace"],
            "wall_seconds": config["wall_seconds"], **config.get("actor_defaults", {}), **spec})
        actors[spec["actor"]] = subprocess.Popen(
            [python, "-m", "spireagent.workbench.rl_actor", "--config", str(path),
             "--work", str(work / "actors" / spec["actor"])],
            stdout=(logs / f"actor-{spec['actor']}.out").open("wb"), stderr=subprocess.STDOUT,
            env=environment)
    codes = {name: process.wait() for name, process in actors.items()}
    # Actors are done; let the learner finish its current update and version.
    learner_work.mkdir(parents=True, exist_ok=True)
    (learner_work / "STOP").write_text("actors finished\n")
    codes["learner"] = learner.wait()
    summaries = {"learner": _read(learner_work / "learner-summary.json")}
    for name in actors:
        summaries[name] = _read(work / "actors" / name / "actor-summary.json")
    ledger = time_ledger(summaries, time.monotonic() - began, first_version_seconds)
    result = {"schema": "stpd/rl-run-result-1", "run": run, "exit_codes": codes,
              "time_ledger": ledger}
    _write(work / "time-ledger.json", result)
    return result


def _read(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_bytes()) if path.exists() else None


def time_ledger(summaries: dict[str, Any], wall: float, first_version: float) -> dict[str, Any]:
    """Phase seconds per process plus the cross-process numbers the card asks for."""
    learner = summaries.get("learner") or {}
    actors = {k: v for k, v in summaries.items() if k != "learner" and v}
    games = [g for actor in actors.values() for g in actor["games"]]
    finished = [g for g in games if g["status"] == "saved"]
    phases: dict[str, float] = {}
    for actor in actors.values():
        for name, seconds in actor["timing"].items():
            phases[name] = phases.get(name, 0.0) + seconds
    actor_wall = sum(a["wall_seconds"] for a in actors.values())
    actor_cpu = sum(a.get("cpu_seconds", 0.0) for a in actors.values())
    timing = learner.get("timing", {})
    return {"wall_seconds": wall, "learner_first_version_seconds": first_version,
            "games": len(games), "games_saved": len(finished),
            "games_unknown": sum(g["status"] == "unknown" for g in games),
            "games_per_hour": len(finished) / wall * 3600 if wall else None,
            "game_seconds": [g["elapsed_seconds"] for g in games],
            "actor_cpu_seconds_per_game": actor_cpu / len(games) if games else None,
            "actor_phase_seconds": phases, "actor_wall_seconds": actor_wall,
            "actor_play_fraction": phases.get("ledger_run", 0.0) / actor_wall
            if actor_wall else None,
            "learner_phase_fractions": timing.get("fractions"),
            "learner_updates": learner.get("updates"),
            "learner_versions": learner.get("versions"),
            "trajectory_to_training_seconds": learner.get("trajectory_to_training_seconds"),
            "actor_model_fetch_seconds": {k: a["model_fetch_seconds"] for k, a in actors.items()},
            "actor_versions_played": {k: sorted({g.get("model_version") for g in a["games"]
                                                 if g.get("model_version") is not None})
                                      for k, a in actors.items()}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = launch(json.loads(args.config.read_bytes()), args.work.resolve())
    print(json.dumps(result["exit_codes"]))


if __name__ == "__main__":
    main()
