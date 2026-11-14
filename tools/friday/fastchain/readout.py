"""Fast-chain readout: one edge table per run (stdlib only).

    python3 readout.py RUN [--window 1200] [--json]

Built from the private ts.py / c3c_analyze.py logic. Reads the run directory written by
launch.py (plan.json for the name `N卡 · 布局 · 卡型`, the target and the perfect-packing
ideal) and the files the roles write (learner-events/-status, service-status-*, gpu.csv,
env-n*/). Prints per role: rows/s, busy %, queue, wait and drops over the best window;
then the training rows/h against the target of this card count; for multi-card runs a
loss-table skeleton (ideal by perfect packing, measured, one row per cause); and the
environment-side recipe (games, mean floor, wins, rows per exploration arm, ascension
level, start kind and K) so short runs with different recipes can be compared.

Missing files give an empty row, never a guessed number.

`--json` prints the run record (`SCHEMA`): the same numbers plus the run's identity
(source revision, profile digest, `N卡 · 布局 · 卡型`, recipe knobs), throughput in
training rows/h with and without aux rows, first use and intake, the learner's value/HP
diagnostics and PPO statistics (latest window and run mean), training-game results of
the control arm's full games (rolling and whole run, per ascension), the SGD boundary
sections, stops/refusals/unknowns with cause counts and the checkpoint ids.
supervise.py writes it as run-record.json at every checkpoint and at the end.
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "spireagent/friday-fastchain-run-record-1"
WAN = 10_000
AUX = {"rows", "terminal_rows", "hp_rows"}
# stpd.rl.learning_diagnostics: per bucket and head these sums, in this order.
VALUE_FIELDS = ("rows", "target_sum", "target_square_sum", "error_sum", "error_square_sum",
                "abs_error_sum")
LEARNING_SCALARS = ("td_loss", "policy_loss", "entropy", "approx_kl_mu_theta",
                    "clip_fraction", "acting_kl_theta_mu", "terminal_loss", "hp_loss",
                    "gradient_norm")
ROLLING_GAMES = 100  # the training-game rolling mean: the last N complete games
BOSS_FLOORS = (17, 33, 48)  # stpd.rl.heads; A10 has a second last-act Boss on 49


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


def _find(record: Any, key: str) -> list[Any]:
    """Every value of `key` anywhere inside one event (the step probe nests it)."""
    if isinstance(record, dict):
        return [v for k, v in record.items() if k == key] + [
            x for v in record.values() for x in _find(v, key)]
    if isinstance(record, list):
        return [x for v in record for x in _find(v, key)]
    return []


def compile_edge(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compile warm-up: the dynamo graph count after warm-up is the first stepped value;
    any rise later is an online recompile (alarm). gc seconds when the probe is on."""
    started = [e for e in events if e.get("kind") == "sgd_process_started"]
    warm = (started[0].get("compile") or {}).get("warmup") if started else None
    graphs = [(e["t"], e["rows_forward"]["dynamo_unique_graphs"]) for e in events
              if e.get("kind") == "progress" and e.get("stepped")
              and isinstance(e.get("rows_forward"), dict)
              and "dynamo_unique_graphs" in e["rows_forward"]]
    out: dict[str, Any] = {"warmup_dumps": len(warm) if isinstance(warm, list) else None,
                           "graphs_after_warmup": graphs[0][1] if graphs else None,
                           "graphs_now": graphs[-1][1] if graphs else None, "alarm": None}
    if graphs and graphs[-1][1] > graphs[0][1]:
        first = next(t for t, g in graphs if g > graphs[0][1])
        out["alarm"] = (f"dynamo_unique_graphs rose {graphs[0][1]} -> {graphs[-1][1]} after "
                        f"warm-up (first at +{first - graphs[0][0]:.0f} s): online recompiles")
    gc = [x for e in events if e.get("kind") == "progress" for x in _find(e, "gc_seconds")
          if isinstance(x, (int, float))]
    out["gc_seconds"] = sum(gc) if gc else None
    out["gc_freeze"] = any(_find(e, "gc_freeze") for e in events
                           if e.get("kind") == "progress")
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


def _bucket() -> dict[str, Any]:
    return {"games": 0, "other": 0, "floor_sum": 0, "wins": 0, "rows": 0}


def _add(bucket: dict[str, Any], record: dict[str, Any], counted: bool) -> None:
    """One closed game. complete = terminal, admitted and evidence ok; rows = the model rows
    (decisions) of every admitted game whose evidence held, complete or not."""
    if counted:
        bucket["rows"] += record.get("decisions") or 0
    if counted and record.get("reason") == "terminal":
        bucket["games"] += 1
        bucket["floor_sum"] += record.get("floor") or 0
        bucket["wins"] += record.get("terminal") == "victory"
    else:
        bucket["other"] += 1


def _finish(bucket: dict[str, Any]) -> dict[str, Any]:
    out = {key: value for key, value in bucket.items() if key != "floor_sum"}
    out["mean_floor"] = bucket["floor_sum"] / bucket["games"] if bucket["games"] else None
    return out


def recipe_edge(run: Path) -> dict[str, Any]:
    """Environment-side recipe over the whole run so far, from every driver's driver.jsonl
    `closed` lines: per exploration arm, per ascension level, per start kind and per K
    (games = complete games, mean floor and wins over them, other = every other close, rows
    = model rows of admitted games; for K > 1 also groups, full groups = all K complete, and
    the mean floor range inside a group). A run without those lines gives {}."""
    tables: dict[str, dict[str, dict[str, Any]]] = {"arm": {}, "ascension": {}, "start": {},
                                                    "k": {}}
    groups: dict[str, dict[str, Any]] = {}  # driver dir/group -> k, complete, floors
    for path in sorted(glob.glob(str(run / "env-n*" / "p*" / "driver.jsonl"))):
        records = jsonl(path)
        admitted = {r.get("game_ref") for r in records
                    if r.get("event") == "admission" and r.get("ok")}
        for r in records:
            if r.get("event") != "closed" or "arm" not in r:
                continue
            counted = r.get("game_ref") in admitted and not r.get("evidence_failed")
            for key in tables:
                _add(tables[key].setdefault(str(r.get("replay_k" if key == "k" else key)),
                                            _bucket()), r, counted)
            g = groups.setdefault(f"{Path(path).parent}/{r.get('group')}",
                                  {"k": str(r.get("replay_k")), "floors": []})
            if counted and r.get("reason") == "terminal":
                g["floors"].append(r.get("floor") or 0)
    if not groups:
        return {}
    out: dict[str, Any] = {key: {name: _finish(b) for name, b in sorted(table.items())}
                           for key, table in tables.items()}
    for k, row in out["k"].items():
        mine = [g["floors"] for g in groups.values() if g["k"] == k]
        spread = [max(f) - min(f) for f in mine if len(f) >= 2]
        row.update({"groups": len(mine), "full_groups": sum(len(f) == int(k) for f in mine),
                    "mean_floor_range": sum(spread) / len(spread) if spread else None})
    return out


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


def disk_edge(run: Path) -> dict[str, Any]:
    """A2 watermarks: the last free % per sampled directory and every WARN/STOP line."""
    last: dict[str, float] = {}
    path = run / "disk.txt"
    for line in (path.read_text().splitlines() if path.exists() else []):
        parts = line.split()
        if len(parts) >= 3 and parts[2].startswith("free_pct="):
            last[parts[1]] = float(parts[2].split("=", 1)[1])
    marks = run / "disk-watermark.txt"
    return {"free_pct": last,
            "events": marks.read_text().splitlines() if marks.exists() else []}


def _json(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def identity_edge(run: Path, plan: dict[str, Any], plan_path: Path) -> dict[str, Any]:
    """What this run is: plan/profile digests, source, layout name and recipe knobs.

    Source facts come from facts.json (launch.py, at freeze); the loss keys (c_hp and its
    scope included) from the learner config the learner actually read."""
    profile = _json(run / "effective-profile.json") or {}
    knobs = {name: knob.get("value") for name, knob in (profile.get("knobs") or {}).items()}
    facts = _json(run / "facts.json") or {}
    learner = _json(run / "learner" / "learner.json") or {}
    loss = learner.get("loss") or {}
    segment_format = knobs.get("segment_format") or (
        knobs.get("service_kwargs") or {}).get("segment_format")
    return {
        "name": plan["name"], "runtag": plan.get("runtag"),
        "plan_sha256": _sha256(plan_path),
        "profile_sha256": _sha256(run / "effective-profile.json"),
        "overridden_knobs": {name: knob.get("value") for name, knob in
                             (profile.get("knobs") or {}).items() if "overridden_from" in knob},
        "source_revision": facts.get("src_rev"),
        "source_dirty": (facts.get("src_checkout") or {}).get("dirty"),
        "env_package_revision": facts.get("env_pkg_rev"),
        "env_candidate_sha256": facts.get("env_candidate_sha256"),
        "resume_checkpoint": plan.get("resume_checkpoint"),
        "placement": plan.get("placement"),
        "recipe": {"exploration_ladder": knobs.get("exploration_ladder"),
                   "ascension_mix": knobs.get("ascension_mix"),
                   "replay_k": knobs.get("replay_k"), "segment_format": segment_format,
                   "hp_coef": loss.get("hp_coef"),
                   "c_hp": loss.get("hp_advantage_coef"),
                   "c_hp_scope": loss.get("hp_advantage_scope")}}


def throughput_edge(events: list[dict[str, Any]], status: list[dict[str, Any]],
                    train: dict[str, Any], window: float) -> dict[str, Any]:
    """Training rows/h with and without aux rows (best window, latest window, whole run),
    first use and intake. First use and admitted rows are deltas over the same span."""
    starts = [e["t"] for e in events if e.get("kind") == "training_started"]
    stepped = [e for e in events if e.get("kind") == "progress" and e.get("stepped")]
    if not starts or not stepped:
        return {}
    complete = all(set(e) >= AUX for e in stepped)

    def rates(steps: list[dict[str, Any]], seconds: float) -> tuple[Any, Any]:
        td = sum(e["rows"] for e in steps)
        aux = sum(e.get("terminal_rows", 0) + e.get("hp_rows", 0) for e in steps)
        hours = max(seconds, 1e-9) / 3600
        return ((td + aux) / hours if complete else None), td / hours

    end = stepped[-1]["t"]
    run_seconds = end - starts[0]
    latest = [e for e in stepped if e["t"] >= end - window]
    run_incl, run_td = rates(stepped, run_seconds)
    late_incl, late_td = rates(latest, min(window, run_seconds))
    first, last = stepped[0], stepped[-1]
    first_use = (last.get("first_use", 0) - first.get("first_use", 0)
                 if "first_use" in last else None)
    out: dict[str, Any] = {
        "best_window_training_rows_per_h_incl_aux": train.get("rows_h"),
        "best_window_training_rows_per_h_td_only": train.get("td_rows_h"),
        "latest_window_seconds": window,
        "latest_training_rows_per_h_incl_aux": late_incl,
        "latest_training_rows_per_h_td_only": late_td,
        "run_training_seconds": run_seconds,
        "run_training_rows_per_h_incl_aux": run_incl,
        "run_training_rows_per_h_td_only": run_td,
        "run_training_rows_td_only": sum(e["rows"] for e in stepped),
        "run_training_rows_aux": sum(e.get("terminal_rows", 0) + e.get("hp_rows", 0)
                                     for e in stepped) if complete else None,
        "updates": len(stepped),
        "kl_stopped_attempts": sum(1 for e in events
                                   if e.get("kind") == "progress" and e.get("kl_stopped")),
        "first_use_rows_total": last.get("first_use"),
        "first_use_multi_rows_total": last.get("first_use_multi"),
        "first_use_rows": first_use,
        "first_use_rows_per_h": (first_use / max(last["t"] - first["t"], 1e-9) * 3600
                                 if first_use is not None else None)}
    counted = [x for x in status if isinstance((x.get("fast_chain") or {}).get("learner"), dict)]
    if counted:
        z = counted[-1]["fast_chain"]["learner"]
        out.update({key + "_total": z.get(key) for key in (
            "admitted_rows", "capacity_dropped_rows", "capacity_evicted_rows",
            "capacity_refused_rows", "excluded_dropped_rows", "excluded_trained_rows")})
        out["dropped_rows_total"] = (z.get("capacity_dropped_rows", 0)
                                     + z.get("excluded_dropped_rows", 0))
        a, b = nearest(counted, first["t"]), nearest(counted, last["t"])
        admitted = delta(a, b, "fast_chain", "learner", "admitted_rows")
        out["admitted_rows"] = admitted
        out["first_use_ratio_of_admitted"] = (first_use / admitted
                                              if first_use is not None and admitted else None)
    return out


def value_summary(sums: list[float], scale: float = 1.0) -> dict[str, Any]:
    """learning_diagnostics.summarize (value_quality._stats keys) in stdlib."""
    rows, target, square, error, error_square, absolute = sums
    if rows <= 0:
        return {"rows": 0, "mean_prediction": None, "mean_target": None,
                "bias_prediction_minus_target": None, "mse": None, "constant_mse": None,
                "explained_variance": None, "mae": None}
    mean_target, mean_error = target / rows, error / rows
    variance = square / rows - mean_target ** 2  # below 1e-6 of the 2nd moment: rounding
    variance = variance if variance > 1e-6 * square / rows else 0.0
    residual = max(error_square / rows - mean_error ** 2, 0.0)
    return {"rows": round(rows), "mean_prediction": (mean_target + mean_error) * scale,
            "mean_target": mean_target * scale,
            "bias_prediction_minus_target": mean_error * scale,
            "mse": error_square / rows * scale * scale, "constant_mse": variance * scale * scale,
            "explained_variance": 1 - residual / variance if variance > 0 else None,
            "mae": absolute / rows * scale}


def _learning(steps: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"attempts": len(steps),
                           "stepped": sum(1 for e in steps if e.get("stepped"))}
    for name in LEARNING_SCALARS:
        values = [e[name] for e in steps if isinstance(e.get(name), (int, float))]
        out[name] = sum(values) / len(values) if values else None
    pooled: dict[str, dict[str, list[float]]] = {}
    for e in steps:
        for bucket, heads in (e.get("value_statistics") or {}).items():
            for head, values in heads.items():
                if len(values) != len(VALUE_FIELDS):
                    raise ValueError(f"value_statistics {bucket}/{head}: "
                                     f"{len(values)} fields, expected {len(VALUE_FIELDS)}")
                row = pooled.setdefault(bucket, {}).setdefault(head, [0.0] * len(VALUE_FIELDS))
                for i, value in enumerate(values):
                    row[i] += value
    # "td" = vs lambda-return, "terminal" = vs final outcome, "hp" in HP points.
    out["value"] = {bucket: {head: value_summary(values, 100.0 if head == "hp" else 1.0)
                             for head, values in heads.items()}
                    for bucket, heads in pooled.items()} or None
    raw = [e["advantage_raw"] for e in steps if isinstance(e.get("advantage_raw"), dict)]
    out["advantage_raw"] = {
        "datasets": len(raw),
        "mean": sum(x["mean"] for x in raw) / len(raw),
        "std": sum(x["std"] for x in raw) / len(raw),
        "last": raw[-1]} if raw else None
    return out


def learning_edge(events: list[dict[str, Any]], window: float) -> dict[str, Any]:
    """PPO and value/HP diagnostics over every progress attempt (KL-stopped included:
    their numbers are pre-step): the latest `window` seconds and the whole run."""
    steps = [e for e in events if e.get("kind") == "progress"]
    if not steps:
        return {}
    end = steps[-1]["t"]
    return {"latest_window_seconds": window,
            "latest": _learning([e for e in steps if e["t"] >= end - window]),
            "run_mean": _learning(steps)}


def reward_v21(terminal: str | None, floor: int, ascension: int) -> float | None:
    """heads.class_reward_values of a natural end from its public final floor; None for an
    A10 loss on floor 49+ (no class). The Golden-Compass floor-35 alternate (Boss 2 not
    cleared) is not visible in a closed line; it counts as the normal floor-35 class."""
    if terminal == "victory":
        return 1.0
    finish = 49 if ascension == 10 else 48
    if ascension == 10 and floor >= 49:
        return None
    capped = min(floor, 48)
    return 0.5 * (capped / finish) ** 2 + 0.1 * sum(boss < capped for boss in BOSS_FLOORS)


def _games(games: list[dict[str, Any]]) -> dict[str, Any]:
    if not games:
        return {"games": 0}
    rewards = [g["reward"] for g in games if g["reward"] is not None]
    out: dict[str, Any] = {
        "games": len(games), "mean_floor": sum(g["floor"] for g in games) / len(games),
        "win_rate": sum(g["win"] for g in games) / len(games),
        "mean_reward_v21": sum(rewards) / len(rewards) if rewards else None,
        "reward_undefined_games": len(games) - len(rewards), "bosses": {}}
    names = ("act1_boss", "act2_boss", "act3_boss", "act3_boss2")
    for index, name in enumerate(names):
        mine = [g for g in games if index < len(g["bosses"])]
        if not mine:
            continue
        reached = sum(g["bosses"][index][0] for g in mine)
        cleared = sum(g["bosses"][index][1] for g in mine)
        out["bosses"][name] = {"games": len(mine), "reached": reached, "cleared": cleared,
                               "clear_rate": cleared / len(mine),
                               "clear_rate_given_reached": cleared / reached if reached else None}
    return out


def driver_records(run: Path) -> list[tuple[str, list[dict[str, Any]]]]:
    return [(path, jsonl(path)) for path in
            sorted(glob.glob(str(run / "env-n*" / "p*" / "driver.jsonl")))]


def training_games_edge(drivers: list[tuple[str, list[dict[str, Any]]]]) -> dict[str, Any]:
    """Sampled-play results straight from training data: the control arm's full games
    (start new_run) that are complete (terminal, admitted, evidence ok), per ascension and
    all together, over the whole run and the last ROLLING_GAMES by close time. A Boss on
    floor b is cleared iff the game was won or ended above b (natural layout)."""
    games: list[dict[str, Any]] = []
    for _, records in drivers:
        admitted = {r.get("game_ref") for r in records
                    if r.get("event") == "admission" and r.get("ok")}
        for r in records:
            if (r.get("event") != "closed" or r.get("arm") != "control"
                    or r.get("start") != "new_run" or r.get("reason") != "terminal"
                    or r.get("game_ref") not in admitted or r.get("evidence_failed")):
                continue
            floor, ascension = r.get("floor"), r.get("ascension")
            if type(floor) is not int or type(ascension) is not int:
                raise ValueError(f"closed control game without int floor/ascension: {r}")
            win = r.get("terminal") == "victory"
            bosses = (*BOSS_FLOORS, 49) if ascension == 10 else BOSS_FLOORS
            games.append({"t": r["t"], "ascension": ascension, "floor": floor, "win": win,
                          "reward": reward_v21(r.get("terminal"), floor, ascension),
                          "bosses": [(win or floor >= b, win or floor > b) for b in bosses]})
    if not games:
        return {}
    games.sort(key=lambda g: g["t"])
    levels = sorted({g["ascension"] for g in games})
    hours = max(games[-1]["t"] - games[0]["t"], 1) / 3_600_000

    def table(chosen: list[dict[str, Any]]) -> dict[str, Any]:
        return {"all": _games(chosen), **{f"A{a}": _games([g for g in chosen
                                                            if g["ascension"] == a])
                                          for a in levels}}

    rolling = {f"A{a}": _games([g for g in games if g["ascension"] == a][-ROLLING_GAMES:])
               for a in levels}
    return {"filter": "arm control, start new_run, complete (terminal, admitted, evidence ok)",
            "rolling_games": ROLLING_GAMES,
            "complete_games_per_h": len(games) / hours if len(games) > 1 else None,
            "rolling": {"all": _games(games[-ROLLING_GAMES:]), **rolling},
            "run": table(games)}


def incidents_edge(run: Path, events: list[dict[str, Any]],
                   drivers: list[tuple[str, list[dict[str, Any]]]]) -> dict[str, Any]:
    """Stops, refusals and unknowns with cause counts: env closes by reason (and detail),
    admission refusals by code, open refusals and driver failures by kind, the learner's
    report failure / STOP / disk stop / gpu rc and lost hand-offs."""
    def count(table: dict[str, int], key: Any) -> None:
        table[str(key)] = table.get(str(key), 0) + 1

    closed: dict[str, int] = {}
    details: dict[str, dict[str, int]] = {}
    refused: dict[str, int] = {}
    open_refused: dict[str, int] = {}
    failures: dict[str, int] = {}
    for _, records in drivers:
        for r in records:
            kind = r.get("event")
            if kind == "closed":
                count(closed, r.get("reason"))
                if r.get("reason") != "terminal":
                    count(details.setdefault(str(r.get("reason")), {}), r.get("detail"))
                if r.get("evidence_failed"):
                    count(failures, "evidence_failed_close")
            elif kind == "admission" and not r.get("ok"):
                count(refused, r.get("code"))
            elif kind == "open_refused":
                count(open_refused, r.get("reason"))
            elif kind in ("slot_failed", "build_output_stopped", "fatal", "evidence_failed"):
                count(failures, kind)
    report = _json(run / "learner-report.json") or {}
    text = (run / "gpu-rc").read_text().strip() if (run / "gpu-rc").exists() else None
    return {"env_closed_by_reason": closed, "env_close_details": details,
            "admission_refused_by_code": refused, "open_refused_by_reason": open_refused,
            "driver_failures_by_kind": failures,
            "learner_failure": report.get("failure"),
            "learner_input_failure": report.get("input_failure"),
            "stop": (run / "STOP").read_text().strip() if (run / "STOP").exists() else None,
            "disk_stop": ((run / "disk-stop").read_text().strip()
                          if (run / "disk-stop").exists() else None),
            "gpu_rc": int(text) if text and text.lstrip("-").isdigit() else text,
            "handoff_lost": sum(1 for e in events if e.get("kind") == "fast_handoff_lost")}


def boundary_edge(events: list[dict[str, Any]], window: float) -> dict[str, Any]:
    """SGD boundary sections (#537), once per dataset on its first step: wall and CPU
    seconds per section, total and mean per dataset, latest window and whole run."""
    timed = [(e["t"], e["sgd_timing"]) for e in events if e.get("kind") == "progress"
             and isinstance((e.get("sgd_timing") or {}).get("boundary_sections"), dict)]
    if not timed:
        return {}

    def pool(chosen: list[tuple[float, dict[str, Any]]]) -> dict[str, Any]:
        out: dict[str, Any] = {"datasets": len(chosen)}
        for key in ("boundary_sections", "boundary_sections_cpu"):
            total: dict[str, float] = {}
            for _, timing in chosen:
                for name, seconds in (timing.get(key) or {}).items():
                    total[name] = total.get(name, 0.0) + seconds
            out[key + "_seconds_total"] = total
            out[key + "_seconds_mean"] = {k: v / len(chosen) for k, v in total.items()}
        seconds = [t["boundary_seconds"] for _, t in chosen
                   if isinstance(t.get("boundary_seconds"), (int, float))]
        out["boundary_seconds_mean"] = sum(seconds) / len(seconds) if seconds else None
        return out

    end = timed[-1][0]
    return {"latest_window_seconds": window,
            "latest": pool([x for x in timed if x[0] >= end - window]), "run": pool(timed)}


def checkpoints_edge(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"checkpoint_artifact_id": e.get("checkpoint_artifact_id"),
             "sequence": e.get("sequence"), "t": e.get("t")}
            for e in events if e.get("kind") == "checkpoint_published"]


def build(run: Path, window: float, plan_path: Path | None = None) -> dict[str, Any]:
    plan_path = plan_path or run / "plan.json"
    plan = json.loads(plan_path.read_text())
    events = jsonl(run / "learner-events.jsonl")
    status = jsonl(run / "learner-status.jsonl")
    train = training_edge(events, status, window)
    span = train.get("window")
    target = plan["target"]
    drivers = driver_records(run)
    return {"schema": SCHEMA, "name": plan["name"], "target": target, "training": train,
            "services": service_edges(run, span), "gpu": gpu_cards(run, span),
            "env": env_edge(run, span), "compile": compile_edge(events),
            "disk": disk_edge(run), "recipe": recipe_edge(run),
            "verdict": verdict(train.get("rows_h"), target),
            "loss_table": loss_table(plan, train),
            "identity": identity_edge(run, plan, plan_path),
            "throughput": throughput_edge(events, status, train, window),
            "learning": learning_edge(events, window),
            "training_games": training_games_edge(drivers),
            "boundary_sections": boundary_edge(events, window),
            "incidents": incidents_edge(run, events, drivers),
            "checkpoints": checkpoints_edge(events)}


def fmt(value: Any, digits: int = 0) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}" if isinstance(value, (int, float)) else str(value)


def render_record(r: dict[str, Any]) -> list[str]:
    """The run-record sections as table lines (the JSON carries the same numbers)."""
    out: list[str] = []
    i = r.get("identity") or {}
    if i:
        recipe = i.get("recipe") or {}
        out.append(f"identity: src {str(i.get('source_revision'))[:12]}"
                   + (" DIRTY" if i.get("source_dirty") else "")
                   + f", profile {str(i.get('profile_sha256'))[:12]}, segment "
                   f"{recipe.get('segment_format')}, K {recipe.get('replay_k')}, ascension mix "
                   f"{recipe.get('ascension_mix')}, c_hp {recipe.get('c_hp')} "
                   f"({recipe.get('c_hp_scope')})")
    p = r.get("throughput") or {}
    if p:
        out.append(f"training rows/h, run mean: {fmt(p['run_training_rows_per_h_incl_aux'])} "
                   f"incl. aux, {fmt(p['run_training_rows_per_h_td_only'])} td only; latest "
                   f"{fmt(p['latest_training_rows_per_h_incl_aux'])} / "
                   f"{fmt(p['latest_training_rows_per_h_td_only'])}")
        ratio = p.get("first_use_ratio_of_admitted")
        out.append(f"first use: {fmt(p.get('first_use_rows_per_h'))} rows/h"
                   + (f", {100 * ratio:.1f}% of admitted" if ratio is not None else "")
                   + f"; admitted {fmt(p.get('admitted_rows_total'))}, dropped "
                   f"{fmt(p.get('dropped_rows_total'))} rows; KL-stopped attempts "
                   f"{p.get('kl_stopped_attempts')}")
    learning = r.get("learning") or {}
    for label, key in (("latest", "latest"), ("run mean", "run_mean")):
        x = learning.get(key)
        if not x:
            continue
        value = (x.get("value") or {}).get("all") or {}
        adv = x.get("advantage_raw") or {}

        def ev(head: str, part: dict[str, Any] = value) -> str:
            return fmt((part.get(head) or {}).get("explained_variance"), 3)

        out.append(f"learning {label}: EV vs lambda-return {ev('td')}, vs outcome "
                   f"{ev('terminal')}, value mse {fmt((value.get('td') or {}).get('mse'), 4)}, "
                   f"HP MAE {fmt((value.get('hp') or {}).get('mae'), 2)} HP; KL "
                   f"{fmt(x.get('approx_kl_mu_theta'), 4)}, clip "
                   f"{fmt(x.get('clip_fraction'), 3)}, entropy {fmt(x.get('entropy'), 3)}; "
                   f"advantage mean {fmt(adv.get('mean'), 4)} std {fmt(adv.get('std'), 4)}")
        if key == "latest" and x.get("value"):
            out.append("  EV vs lambda-return by bucket: " + ", ".join(
                f"{bucket} {ev('td', heads)}" for bucket, heads in x["value"].items()
                if bucket != "all"))
    games = r.get("training_games") or {}
    for level, g in (games.get("rolling") or {}).items():
        if not g.get("games"):
            continue
        bosses = " ".join(f"{name.replace('_boss', '')} {100 * b['clear_rate']:.0f}%"
                          for name, b in g["bosses"].items())
        out.append(f"training games {level} (control, full, last {games['rolling_games']}): "
                   f"{g['games']} games, floor {g['mean_floor']:.1f}, win "
                   f"{100 * g['win_rate']:.1f}%, reward {fmt(g['mean_reward_v21'], 3)}; "
                   f"Boss cleared {bosses}")
    inc = r.get("incidents") or {}
    if inc:
        out.append(f"incidents: closed {inc['env_closed_by_reason']}, non-terminal details "
                   f"{inc['env_close_details']}, admission refused "
                   f"{inc['admission_refused_by_code']}, failures "
                   f"{inc['driver_failures_by_kind']}, hand-offs lost {inc['handoff_lost']}")
    boundary = ((r.get("boundary_sections") or {}).get("run") or {})
    mean = boundary.get("boundary_sections_seconds_mean") or {}
    if mean:
        top = sorted(mean.items(), key=lambda kv: -kv[1])[:4]
        out.append(f"boundary per dataset ({boundary['datasets']}): "
                   + ", ".join(f"{name} {seconds:.2f} s" for name, seconds in top))
    cps = r.get("checkpoints") or []
    if cps:
        out.append(f"checkpoints: {len(cps)}, last {cps[-1]['checkpoint_artifact_id']}")
    return out


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
    c = r.get("compile") or {}
    out.append(f"compile: warm-up dumps {fmt(c.get('warmup_dumps'))}, graphs after warm-up "
               f"{fmt(c.get('graphs_after_warmup'))}, now {fmt(c.get('graphs_now'))}"
               + (f", gc {fmt(c['gc_seconds'], 1)} s" if c.get("gc_seconds") is not None else "")
               + (", gc frozen" if c.get("gc_freeze") else ""))
    if c.get("alarm"):
        out.append("ALARM " + c["alarm"])
    d = r.get("disk") or {}
    if d.get("free_pct"):
        out.append("disk free: " + ", ".join(f"{name} {pct:.0f}%"
                                             for name, pct in sorted(d["free_pct"].items())))
    out += [("ALARM " if line.startswith("DISK STOP") else "") + line
            for line in d.get("events", [])]
    out += render_record(r)
    if r.get("recipe"):
        out += ["", "recipe, whole run so far (games = complete: terminal, admitted, evidence "
                "ok; rows = model rows of admitted games):",
                f"  {'':<22}{'games':>8}{'other':>7}{'floor':>7}{'wins':>6}{'rows':>12}"
                f"{'groups':>8}{'full':>6}{'range':>7}"]
        for key in ("arm", "ascension", "start", "k"):
            for name, row in r["recipe"][key].items():
                label = f"{key} {'A' + name if key == 'ascension' else name}"
                grouped = key == "k" and name != "1"
                out.append(f"  {label:<22}{row['games']:>8,}{row['other']:>7,}"
                           f"{fmt(row['mean_floor'], 1):>7}{row['wins']:>6,}{row['rows']:>12,}"
                           + (f"{row['groups']:>8,}{row['full_groups']:>6,}"
                              f"{fmt(row['mean_floor_range'], 1):>7}" if grouped else ""))
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
