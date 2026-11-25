#!/usr/bin/env python3
"""Eval-only readout and same-seed pairing (rl5-eval-only.toml): `table RUN --out B.json`,
`from-paired PAIRED.csv --side a46 --checkpoint ID --suite S --out B.json`, `pair A B` (B - A;
"same" = per-seed outcome and floor equal). A baseline: one game per planned seed, keyed by
CP + action path + list. Readings as bing_eval_pairs.py / compare_final.py: official = seeds
scored on both sides; conservative = every planned seed, an unknown game a loss at its floor,
no floor 0.0; 95% interval from 10,000 paired-bootstrap resamples, random.Random(0)."""
from __future__ import annotations

import argparse
import collections
import csv
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from readout import BOSS_FLOORS, jsonl, reward_v21  # noqa: E402

UNPLAYED = {"result": None, "floor": None, "bosses_passed": 0, "last_boss_reached": False,
            "reward": None, "conservative": 0.0}


def game(r: dict[str, Any]) -> dict[str, Any]:  # a driver closed / open_refused line
    floor, asc, end = r.get("floor"), r.get("ascension", 0), r.get("terminal")
    if r.get("event") == "open_refused" or r.get("reason") == "not_started":
        return {"status": "not_started", **UNPLAYED}
    known = type(floor) is int
    done = known and r.get("reason") == "terminal" and end in ("victory", "defeat")
    done = done and not r.get("evidence_failed")
    bosses, win = (*BOSS_FLOORS, 49) if asc == 10 else BOSS_FLOORS, done and end == "victory"
    reward = reward_v21(end, floor, asc) if done else None
    return {"status": "finished" if done else "unknown",
            "result": ("win" if win else "loss") if done else None, "floor": floor,
            "bosses_passed": sum(win or (known and floor > b) for b in bosses),
            "last_boss_reached": win or (known and floor >= bosses[-1]), "reward": reward,
            "conservative": reward if reward is not None
            else (reward_v21("defeat", floor, asc) or 0.0) if known else 0.0}


def baseline(checkpoint: str | None, path: str, suite: str, sha256: str | None,
             seeds: list[str], games: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "spireagent/friday-eval-baseline-1", "checkpoint": checkpoint,
            "action_path": path, "list": {"suite": suite, "sha256": sha256}, "seeds": seeds,
            "games": {s: games.get(s, {"status": "not_played", **UNPLAYED}) for s in seeds}}


def table(run: Path, action_path: str) -> dict[str, Any]:
    plan = json.loads((run / "plan.json").read_text())
    listed, games = plan["eval_only"], {}
    for path in sorted(run.glob("env-*/p*/driver.jsonl")):
        for r in jsonl(path):
            if r.get("event") in ("closed", "open_refused") and r.get("seed") is not None:
                if r["seed"] in games or r["seed"] not in listed["seeds"]:
                    raise SystemExit(f"seed {r['seed']} ({path}): closed twice or not listed")
                games[r["seed"]] = game(r)
    return baseline(plan.get("resume_checkpoint"), action_path, listed["suite"],
                    listed["sha256"], listed["seeds"], games)


def from_paired(path: Path, side: str, checkpoint: str, suite: str) -> dict[str, Any]:
    """`side` columns of a paired.csv (old CPU evaluator; unscored rows: no floor, 0.0)."""
    with path.open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    games = {}
    for row in rows:
        col = lambda name, row=row: row[f"{side}_{name}"]  # noqa: E731
        scored, win = col("scored") == "True", col("result") == "win"
        reward = float(col("reward")) if scored else None
        games[row["seed"]] = {
            "status": "finished" if scored else "not_started" if col("status") == "not_started"
            else "unknown", "result": col("result") or None,
            "floor": int(col("floor")) if col("floor") else None,
            "bosses_passed": max(len(json.loads(col("boss_cleared_acts") or "[]")), 3 * win),
            "last_boss_reached": win or 3 in json.loads(col("boss_reached_acts") or "[]"),
            "reward": reward, "conservative": reward if scored else 0.0}
    return baseline(checkpoint, "cpu-evaluator-old", suite, None, [r["seed"] for r in rows],
                    games)


def delta(a: dict[str, Any], b: dict[str, Any], seeds: list[str], key: str) -> dict[str, Any]:
    d = [float(b[s][key]) - float(a[s][key]) for s in seeds]
    if not d:
        return {"pairs": 0}
    rng = random.Random(0)
    means = sorted(statistics.fmean(rng.choices(d, k=len(d))) for _ in range(10000))
    return {"pairs": len(d), "mean": statistics.fmean(d), "ci95": [means[250], means[9749]],
            "higher_same_lower": [sum(v > 0 for v in d), d.count(0), sum(v < 0 for v in d)]}


def describe(x: dict[str, Any]) -> dict[str, Any]:
    games = list(x["games"].values())
    status = dict(collections.Counter(g["status"] for g in games))
    f = sorted(g["floor"] for g in games if g["status"] == "finished")
    wins = sum(g["result"] == "win" for g in games)
    out = {"checkpoint": x["checkpoint"], "action_path": x["action_path"],
           "planned": len(x["seeds"]), "status": status, "wins": wins,
           "last_boss_reached": sum(g["last_boss_reached"] for g in games
                                    if g["status"] == "finished")}
    if f:
        out.update({"mean_floor": statistics.fmean(f), "p10_25_75_90": [
            f[min(len(f) - 1, int(p * len(f)))] for p in (.1, .25, .75, .9)],
            "bins": {"1-16": sum(v <= 16 for v in f), "17-33": sum(17 <= v <= 33 for v in f),
                     "34-48 loss": sum(v >= 34 for v in f) - wins, "win": wins},
            "tail_floor_le10": sum(v <= 10 for v in f)})
    return out


def pair(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    if a["list"]["suite"] != b["list"]["suite"] or sorted(a["seeds"]) != sorted(b["seeds"]):
        raise SystemExit("the two baselines are not the same eval list (suite and seeds)")
    ga, gb, seeds = a["games"], b["games"], a["seeds"]
    scored = [s for s in seeds if ga[s]["reward"] is not None and gb[s]["reward"] is not None]
    both = [s for s in seeds if ga[s]["status"] == gb[s]["status"] == "finished"]
    differ = [s for s in both if (ga[s]["result"], ga[s]["floor"])
              != (gb[s]["result"], gb[s]["floor"])]
    official = {"pairs": len(scored), "dropped": len(seeds) - len(scored),
                "reward": [statistics.fmean(g[s]["reward"] for s in scored) for g in (ga, gb)]
                if scored else None, "reward_delta": delta(ga, gb, scored, "reward"),
                "floor_delta": delta(ga, gb, scored, "floor"),
                "wins": [sum(g[s]["result"] == "win" for s in scored) for g in (ga, gb)],
                "last_boss_reached": [sum(g[s]["last_boss_reached"] for s in scored)
                                      for g in (ga, gb)]}
    return {"a": describe(a), "b": describe(b), "lists": [a["list"], b["list"]],
            "official": official, "conservative": {
                "pairs": len(seeds), "reward_delta": delta(ga, gb, seeds, "conservative")},
            # same model on two action paths: outcome and floor per seed finished on both
            "same": {"compared": len(both), "identical": len(both) - len(differ),
                     "differ": len(differ), "differ_seeds": differ}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("table", "from-paired", "pair"))
    parser.add_argument("inputs", nargs="+", type=Path)
    for name, default in (("out", None), ("action-path", "gpu-service"), ("side", "a46"),
                          ("checkpoint", None), ("suite", None)):
        parser.add_argument(f"--{name}", default=default)
    args = parser.parse_args(argv)
    if args.command == "pair":
        print(json.dumps(pair(*(json.loads(p.read_text()) for p in args.inputs[:2])), indent=1))
        return 0
    if not args.out or (args.command == "from-paired" and not (args.checkpoint and args.suite)):
        parser.error("table needs --out; from-paired also --checkpoint and --suite")
    out = (table(args.inputs[0], args.action_path) if args.command == "table"
           else from_paired(args.inputs[0], args.side, args.checkpoint, args.suite))
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(describe(out), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
