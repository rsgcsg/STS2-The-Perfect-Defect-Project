"""Replay scripts for save_replay_probe.mjs (RL5-METHODS B phase 1) from one evaluation round.

    PYTHONPATH=python python3 export_replay_scripts.py PEER RESULT_JSON OUT_JSONL SCRATCH MIN_FLOOR

One JSONL line per argmax evaluation game that reached MIN_FLOOR: seed, outcome, and per step
[P1 catalog without choice_id, chosen index, act, floor, native_kind]. Read-only on the Store
peer (PEER) whose trajectory shards the round's result (RESULT_JSON) names.
"""
import json
import sys
from pathlib import Path

from spireagent.storage.config import open_store
from spireagent.workbench.managed_trajectory_store import read_shard


def main() -> None:
    peer, result_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    scratch, min_floor = Path(sys.argv[4]), int(sys.argv[5])
    with open(result_path) as file:
        result = json.load(file)
    slots = sorted((s for s in result["ledger_report"]["slots"]
                    if (s["max_observed_floor"] or 0) >= min_floor),
                   key=lambda s: s["seed_ordinal"])
    store = open_store(peer)
    written = 0
    with open(out_path, "w") as out:
        for slot in slots:
            episode = next((ep for ep in read_shard(store, slot["trajectory_shard_id"],
                                                    scratch=scratch)
                            if ep["run_id"] == slot["run_id"]), None)
            if episode is None:
                raise SystemExit(f"run {slot['run_id']} not in shard {slot['trajectory_shard_id']}")
            if episode["identity"]["behavior_policy"]["mode"] != "argmax":
                raise SystemExit(f"run {slot['run_id']} is not argmax")
            steps = []
            for step in episode["steps"]:
                observation = episode["observations"][step["observation_index"]]["observation"]
                catalog = [{k: v for k, v in item.items() if k != "choice_id"}
                           for item in observation["catalog"]]
                index = step["action_index"]
                chosen = observation["catalog"][index]["choice_id"]
                if chosen != step["action_id"] or step["behavior"]["probabilities"][index] != 1.0:
                    raise SystemExit(f"run {slot['run_id']} step {step['index']}: "
                                     "index/choice mismatch")
                run = (observation.get("run") or {}).get("content") or {}
                steps.append([catalog, index, run.get("act"), run.get("floor"),
                              observation["scene"].get("native_kind")])
            terminal = (episode.get("outcome") or {}).get("terminal") or {}
            out.write(json.dumps({"seed": slot["seed"], "seed_ordinal": slot["seed_ordinal"],
                                  "run_id": slot["run_id"],
                                  "max_floor": slot["max_observed_floor"],
                                  "result": terminal.get("result"), "ascension": slot["ascension"],
                                  "steps": steps},
                                 ensure_ascii=False, separators=(",", ":")) + "\n")
            written += 1
    print(json.dumps({"scripts": written, "min_floor": min_floor, "out": out_path}))


if __name__ == "__main__":
    main()
