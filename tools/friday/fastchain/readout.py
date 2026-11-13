"""Fast-chain readout: one edge table per run (stdlib only).

    python3 readout.py RUN [--window 1200] [--json]

Built from the private ts.py / c3c_analyze.py logic. Reads the run directory written by
launch.py (plan.json for the name `N卡 · 布局 · 卡型`, the target and the perfect-packing
ideal) and the files the roles write (learner-events/-status, service-status-*, gpu.csv,
env-n*/). Prints per role: rows/s, busy %, queue, wait and drops over the best window;
then the training rows/h against the target of this card count; for multi-card runs a
loss-table skeleton (ideal by perfect packing, measured, one row per cause).

Missing files give an empty row, never a guessed number.
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import json
from pathlib import Path
from typing import Any

WAN = 10_000
AUX = {"rows", "terminal_rows", "hp_rows"}


def jsonl(path: str | Path) -> list[dict[str, Any]]:
    try:
        with open(path) as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except (OSError, ValueError):
        return []


def best_window(steps: list[tuple[float, int]], t0: float,
                window: float) -> tuple[int, float, float]:
    """Highest row sum over any `window` seconds that ends at least `window` after t0."""
    best, j, total = (0, t0, t0), 0, 0
    for t, rows in steps:
        total += rows
        while steps[j][0] < t - window:
            total -= steps[j][1]
            j += 1
        if t - t0 >= window and total > best[0]:
            best = (total, steps[j][0], t)
    if best[0] == 0 and steps:  # run shorter than the window: whole run
        best = (sum(r for _, r in steps), steps[0][0], steps[-1][0])
    return best


def nearest(seq: list[dict[str, Any]], t: float) -> dict[str, Any]:
    return min(seq, key=lambda x: abs(x["t"] - t))


def delta(a: dict[str, Any], z: dict[str, Any], *path: str) -> float:
    def get(x: dict[str, Any]) -> float:
        for key in path:
            x = x.get(key, {}) if isinstance(x, dict) else {}
        return float(x) if isinstance(x, (int, float)) else 0.0
    return get(z) - get(a)


def training_edge(events: list[dict[str, Any]], status: list[dict[str, Any]],
                  window: float) -> dict[str, Any]:
    """Training rows INCLUDE aux: per stepped step td `rows` + `terminal_rows` + `hp_rows`
    (every target is in this unit; the private tool counted td only, -12.4 %). A stepped
    event without the aux fields makes the incl.-aux number unknown, never td-only."""
    starts = [e["t"] for e in events if e.get("kind") == "training_started"]
    stepped = [e for e in events if e.get("kind") == "progress" and e.get("stepped")]
    if not starts or not stepped:
        return {"rows_h": None, "td_rows_h": None, "window": None}
    t0 = starts[0]
    complete = all(set(e) >= AUX for e in stepped)
    steps = [(e["t"], e["rows"] + (e.get("terminal_rows", 0) + e.get("hp_rows", 0)))
             for e in stepped]
    rows, a_t, z_t = best_window(steps, t0, window)
    span = max(z_t - a_t, 1e-9)
    inside = [e for e in stepped if a_t <= e["t"] <= z_t]
    td = sum(e["rows"] for e in inside)
    secs = sorted(e["seconds"] for e in inside if "seconds" in e)
    out: dict[str, Any] = {
        "t0": t0, "window": (a_t, z_t), "rows": rows if complete else None,
        "rows_s": rows / span if complete else None,
        "rows_h": rows / span * 3600 if complete else None,
        "td_rows_h": td / span * 3600,
        "aux_share": (rows - td) / rows if complete and rows else None,
        "aux_missing": not complete,
        "step_median_s": secs[len(secs) // 2] if secs else None,
        "step_p90_s": secs[int(len(secs) * 0.9)] if secs else None}
    timed = [x for x in status if "timing" in x]
    if timed:
        a, z = nearest(timed, a_t), nearest(timed, z_t)
        dt = max(z["t"] - a["t"], 1e-9)
        pct = {k: 100 * delta(a, z, "timing", k) / dt for k in (
            "preparation_wait_seconds", "sgd_stage_wait_seconds",
            "optimizer_attempt_seconds", "input_transfer_host_seconds")}
        out.update({
            "prepare_wait_pct": pct["preparation_wait_seconds"],
            "stage_wait_pct": pct["sgd_stage_wait_seconds"],
            "optimizer_pct": pct["optimizer_attempt_seconds"],
            "transfer_pct": pct["input_transfer_host_seconds"],
            "admitted_rows_s": delta(a, z, "fast_chain", "learner", "admitted_rows") / dt,
            "dropped_rows_s": delta(a, z, "fast_chain", "learner", "capacity_dropped_rows") / dt,
            "queued_rows": z.get("queued_rows")})
    return out


def service_edges(run: Path, window: tuple[float, float] | None) -> list[dict[str, Any]]:
    out = []
    for path in sorted(glob.glob(str(run / "service-status-*.jsonl"))):
        records = jsonl(path)
        if window:
            records = [x for x in records if window[0] <= x["t"] <= window[1]] or records
        if len(records) < 2:
            continue
        a, z = records[0], records[-1]
        dt = max(z["t"] - a["t"], 1e-9)
        queue = [x["handoff_q"] for x in records if isinstance(x.get("handoff_q"), (int, float))]
        out.append({
            "service": Path(path).stem.rsplit("-", 1)[-1],
            "rows_s": delta(a, z, "service", "rows") / dt,
            "device_pct": 100 * delta(a, z, "service", "device_seconds") / dt,
            "finish_pct": 100 * delta(a, z, "service", "finish_seconds") / dt,
            "join_pct": 100 * delta(a, z, "service", "join_seconds") / dt,
            "handoff_q_mean": sum(queue) / len(queue) if queue else None,
            "handoff_q_max": max(queue) if queue else None,
            "written_s": delta(a, z, "handoff", "written") / dt,
            "dropped_s": delta(a, z, "handoff", "dropped") / dt})
    return out


def gpu_cards(run: Path, window: tuple[float, float] | None) -> dict[str, dict[str, float]]:
    cards: dict[str, list[tuple[float, float]]] = {}
    try:
        lines = (run / "gpu.csv").read_text().splitlines()
    except OSError:
        return {}
    for line in lines:
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            t, util, mem = int(parts[0]) / 1000, float(parts[2]), float(parts[3])
        except ValueError:
            continue
        if window and not window[0] <= t <= window[1]:
            continue
        cards.setdefault(parts[1], []).append((util, mem))
    return {k: {"busy_pct": sum(u for u, _ in v) / len(v), "mem_mib_max": max(m for _, m in v)}
            for k, v in cards.items() if v}


def env_edge(run: Path, window: tuple[float, float] | None) -> dict[str, Any]:
    nodes, busy, rows, cpu, closed = 0, 0.0, 0, 0.0, {}
    for d in sorted(glob.glob(str(run / "env-n*"))):
        try:
            cg = [tuple(map(int, line.split())) for line in Path(d, "cgroup.txt").read_text()
                  .splitlines() if len(line.split()) == 2]
        except (OSError, ValueError):
            cg = []
        if len(cg) > 2:
            cpu += (cg[-1][1] - cg[0][1]) / 1e9
            inside = [c for c in cg if not window or window[0] * 1000 <= c[0] <= window[1] * 1000]
            if len(inside) > 2:
                nodes += 1
                busy += (inside[-1][1] - inside[0][1]) / 1e9 / max(
                    (inside[-1][0] - inside[0][0]) / 1000, 1e-9)
        for agg in glob.glob(f"{d}/aggregator-*.json"):
            with contextlib.suppress(OSError, ValueError):
                rows += int(json.loads(Path(agg).read_text()).get("rows", 0))
        for summary in glob.glob(f"{d}/p*/summary.json"):
            try:
                for item in json.loads(Path(summary).read_text()).get("closed", []):
                    closed[item.get("reason")] = closed.get(item.get("reason"), 0) + 1
            except (OSError, ValueError):
                pass
    return {"nodes_in_window": nodes, "busy_cores": busy, "rows": rows,
            "core_ms_per_row": 1000 * cpu / rows if rows else None, "closed": closed}


def loss_table(plan: dict[str, Any], train: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Multi-card skeleton: ideal (perfect packing) vs measured, one row per cause.

    Wait shares are the measured fraction of learner time spent waiting, converted to
    rows/h at the measured rate; the residual row holds whatever is not attributed.
    "fixable" and "bound" stay for the reader to fill (multi-card loss accounting)."""
    cards = plan["placement"]["cards"]
    ideal = plan["target"].get("perfect_packing_rows_h")
    measured = train.get("rows_h")
    if cards < 2 or not ideal or measured is None:
        return None
    gap = ideal - measured
    rows = []
    attributed = 0.0
    for cause, key in (("prepared batch not ready (prepare/data plane)", "prepare_wait_pct"),
                       ("SGD stage wait (transfer/assembly into SGD)", "stage_wait_pct")):
        pct = train.get(key)
        loss = measured * pct / max(100 - pct, 1e-9) if pct is not None else None
        attributed += loss or 0.0
        rows.append({"cause": cause, "loss_rows_h": loss, "fixable": "?", "bound": "?"})
    rows.append({"cause": "inference card idle / env supply short", "loss_rows_h": None,
                 "fixable": "?", "bound": "?"})
    rows.append({"cause": "multi-rank sync and per-card step below single-card rate",
                 "loss_rows_h": None, "fixable": "?", "bound": "?"})
    rows.append({"cause": "unattributed (gap minus rows above)",
                 "loss_rows_h": gap - attributed, "fixable": "?", "bound": "?"})
    return [{"cause": "ideal (perfect packing)", "loss_rows_h": ideal},
            {"cause": "measured", "loss_rows_h": measured}, *rows]


def verdict(rows_h: float | None, target: dict[str, Any]) -> str | None:
    """1 card: >= target; 3 cards: strictly > target (CHAIN-CONFIG-MODEL §2); ready >=."""
    goal = target.get("target_rows_h")
    if goal is None or rows_h is None:
        return None
    met = rows_h > goal if target.get("cards", 1) > 1 else rows_h >= goal
    ready = target.get("ready_rows_h")
    if met and ready and rows_h >= ready:
        return "ready"
    return "meets target" if met else "below target"


def build(run: Path, window: float, plan_path: Path | None = None) -> dict[str, Any]:
    plan = json.loads((plan_path or run / "plan.json").read_text())
    train = training_edge(jsonl(run / "learner-events.jsonl"),
                          jsonl(run / "learner-status.jsonl"), window)
    span = train.get("window")
    target = plan["target"]
    return {"name": plan["name"], "target": target, "training": train,
            "services": service_edges(run, span), "gpu": gpu_cards(run, span),
            "env": env_edge(run, span),
            "verdict": verdict(train.get("rows_h"), target),
            "loss_table": loss_table(plan, train)}


def fmt(value: Any, digits: int = 0) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}" if isinstance(value, (int, float)) else str(value)


def render(r: dict[str, Any]) -> str:
    t, tg = r["training"], r["target"]
    goal = tg.get("target_rows_h")
    out = [f"== {r['name']}",
           "target: " + (f"{goal / WAN:,.0f}万 rows/h" if goal else (tg.get("note") or "-"))
           + (f" (ready {tg['ready_rows_h'] / WAN:,.0f}万)" if tg.get("ready_rows_h") else ""),
           f"training (incl. aux = td + terminal + hp): "
           f"{fmt(t.get('rows_h') and t['rows_h'] / WAN)}万 rows/h"
           + (f" -> {r['verdict']}" if r["verdict"] else "")
           + (" [aux fields missing: incl.-aux unknown]" if t.get("aux_missing") else ""),
           f"training td only: {fmt(t.get('td_rows_h') and t['td_rows_h'] / WAN)}万 rows/h"
           f" (aux share {fmt(t.get('aux_share') and 100 * t['aux_share'], 1)}%)",
           "", f"{'edge':<12}{'rows/s':>10}{'busy%':>8}{'queue':>10}{'wait%':>8}{'drop/s':>9}"]
    env = r["env"]
    out.append(f"{'env':<12}{'-':>10}{fmt(env['busy_cores'], 0) + 'c':>8}{'-':>10}{'-':>8}"
               f"{'-':>9}  nodes {env['nodes_in_window']} core-ms/row "
               f"{fmt(env['core_ms_per_row'])} closed {env['closed']}")
    for s in r["services"]:
        out.append(f"{'infer ' + s['service']:<12}{fmt(s['rows_s']):>10}{fmt(s['device_pct']):>8}"
                   f"{fmt(s['handoff_q_mean'], 1):>10}{fmt(s['join_pct']):>8}"
                   f"{fmt(s['dropped_s'], 1):>9}  finish {fmt(s['finish_pct'])}%")
    out.append(f"{'intake':<12}{fmt(t.get('admitted_rows_s')):>10}{'-':>8}"
               f"{fmt(t.get('queued_rows')):>10}{'-':>8}{fmt(t.get('dropped_rows_s')):>9}")
    out.append(f"{'prepare':<12}{'-':>10}{'-':>8}{'-':>10}{fmt(t.get('prepare_wait_pct')):>8}"
               f"{'-':>9}")
    out.append(f"{'training':<12}{fmt(t.get('rows_s')):>10}{fmt(t.get('optimizer_pct')):>8}"
               f"{'-':>10}{fmt(t.get('stage_wait_pct')):>8}{'-':>9}  step median "
               f"{fmt(t.get('step_median_s'), 3)} s p90 {fmt(t.get('step_p90_s'), 3)} s")
    for card, g in sorted(r["gpu"].items()):
        out.append(f"{'gpu ' + card:<12}{'-':>10}{fmt(g['busy_pct']):>8}  mem max "
                   f"{fmt(g['mem_mib_max'])} MiB")
    if r["loss_table"]:
        out += ["", "loss table (万 rows/h incl. aux; fixable / bound to fill):"]
        for row in r["loss_table"]:
            loss = row["loss_rows_h"]
            out.append(f"  {row['cause']:<58}{fmt(loss and loss / WAN):>8}"
                       + (f"  {row['fixable']} / {row['bound']}" if "fixable" in row else ""))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path)
    parser.add_argument("--window", type=float, default=1200.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--plan", type=Path, help="plan.json of a run made before launch.py "
                        "(written by `launch.py plan --out`)")
    args = parser.parse_args(argv)
    plan = args.plan or args.run / "plan.json"
    if not plan.exists():
        parser.error(f"{plan} missing: not a launch.py run directory (use --plan)")
    result = build(args.run, args.window, plan)
    print(json.dumps(result, ensure_ascii=False, default=str) if args.json else render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
