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

`Readout` reads incrementally: each update parses only the complete lines appended to
learner-events.jsonl, learner-status.jsonl and every driver.jsonl since the last one and
folds them into running sums (latest-window items in short deques), so a record costs
the new bytes, not the whole run. supervise keeps one per generation; `build` is a fresh
one read once. The small samplers (service-status, gpu.csv, cgroup, disk) are read whole.
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import hashlib
import json
from collections import deque
from collections.abc import Iterable, Iterator
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
BOSS_NAMES = ("act1_boss", "act2_boss", "act3_boss", "act3_boss2")
TIMING = ("preparation_wait_seconds", "sgd_stage_wait_seconds", "optimizer_attempt_seconds",
          "input_transfer_host_seconds")
CHUNK_BYTES = 16 << 20  # parsed per step, so a first (whole-file) read stays bounded


class Tail:
    """The new complete lines of one append-only JSONL file since the last read.

    Only bytes up to the last newline are parsed: a line still being written (the learner's
    buffered writer, an env node appending over NFS) waits for the next read, and earlier
    lines are never dropped. A complete line that is not JSON fails loudly (file, byte).
    `offset` is every byte consumed so far."""

    def __init__(self, path: str | Path) -> None:
        self.path, self.offset = Path(path), 0

    def chunks(self) -> Iterator[list[dict[str, Any]]]:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if size < self.offset:
            raise ValueError(f"{self.path} shrank to {size} bytes; {self.offset} already read")
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            while self.offset < size:
                chunk = handle.read(min(CHUNK_BYTES, size - self.offset))
                end = chunk.rfind(b"\n") + 1
                if not end:
                    if len(chunk) == CHUNK_BYTES:
                        raise ValueError(f"{self.path}: a line over {CHUNK_BYTES} bytes at "
                                         f"byte {self.offset}")
                    return  # the unterminated tail waits for its newline
                records, start = [], self.offset
                for line in chunk[:end].split(b"\n"):
                    if line.strip():
                        try:
                            records.append(json.loads(line))
                        except ValueError as error:
                            raise ValueError(f"{self.path} at byte {start}: not JSON "
                                             f"({error})") from None
                    start += len(line) + 1
                self.offset += end
                handle.seek(self.offset)
                yield records


def jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Every complete line of a JSONL file (a missing file is empty)."""
    return [record for chunk in Tail(path).chunks() for record in chunk]


def nearest(seq: list[dict[str, Any]], t: float) -> dict[str, Any]:
    return min(seq, key=lambda x: abs(x["t"] - t))


def delta(a: dict[str, Any], z: dict[str, Any], *path: str) -> float:
    def get(x: dict[str, Any]) -> float:
        for key in path:
            x = x.get(key, {}) if isinstance(x, dict) else {}
        return float(x) if isinstance(x, (int, float)) else 0.0
    return get(z) - get(a)


def _find(record: Any, key: str) -> list[Any]:
    """Every value of `key` anywhere inside one event (the step probe nests it)."""
    if isinstance(record, dict):
        return [v for k, v in record.items() if k == key] + [
            x for v in record.values() for x in _find(v, key)]
    if isinstance(record, list):
        return [x for v in record for x in _find(v, key)]
    return []


class _Compile:
    """Compile warm-up: the dynamo graph count after warm-up is the first stepped value;
    any rise later is an online recompile (alarm). gc seconds when the probe is on."""

    def __init__(self) -> None:
        self.started, self.warm, self.alarm_t = False, None, None
        self.first: tuple[float, int] | None = None
        self.now: int | None = None
        self.gc: float | None = None
        self.gc_freeze = False

    def add(self, e: dict[str, Any]) -> None:
        kind = e.get("kind")
        if kind == "sgd_process_started" and not self.started:
            self.started, self.warm = True, (e.get("compile") or {}).get("warmup")
        if kind != "progress":
            return
        forward = e.get("rows_forward")
        if e.get("stepped") and isinstance(forward, dict) and "dynamo_unique_graphs" in forward:
            graphs = forward["dynamo_unique_graphs"]
            if self.first is None:
                self.first = (e["t"], graphs)
            elif self.alarm_t is None and graphs > self.first[1]:
                self.alarm_t = e["t"]
            self.now = graphs
        for x in _find(e, "gc_seconds"):
            if isinstance(x, (int, float)):
                self.gc = (self.gc or 0.0) + x
        self.gc_freeze = self.gc_freeze or bool(_find(e, "gc_freeze"))

    def result(self) -> dict[str, Any]:
        first = self.first
        out: dict[str, Any] = {
            "warmup_dumps": len(self.warm) if isinstance(self.warm, list) else None,
            "graphs_after_warmup": first[1] if first else None, "graphs_now": self.now,
            "alarm": None, "gc_seconds": self.gc, "gc_freeze": self.gc_freeze}
        if first and self.now is not None and self.now > first[1] and self.alarm_t is not None:
            out["alarm"] = (f"dynamo_unique_graphs rose {first[1]} -> {self.now} after warm-up "
                            f"(first at +{self.alarm_t - first[0]:.0f} s): online recompiles")
        return out


def compile_edge(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    fold = _Compile()
    for e in events:
        fold.add(e)
    return fold.result()


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
    return Readout(run).update().recipe()


def _group_share(group: list[Any], k_row: list[int], sign: int) -> None:
    """One K group's part of its K row: [groups, full groups, floor range sum, ranged]."""
    k, games, low, high = group[:4]
    if games >= 2:
        k_row[2] += sign * (high - low)
        k_row[3] += sign
    if k.isdigit() and games == int(k):
        k_row[1] += sign


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


LEARNING_KEYS = ("t", "stepped", *LEARNING_SCALARS, "value_statistics", "advantage_raw")


class _Learning:
    """PPO scalars (mean per attempt) and pooled value/HP sums of progress attempts."""

    def __init__(self) -> None:
        self.attempts = self.stepped = self.datasets = 0
        self.scalars = {name: [0.0, 0] for name in LEARNING_SCALARS}
        self.pooled: dict[str, dict[str, list[float]]] = {}
        self.raw_mean = self.raw_std = 0.0
        self.raw_last: dict[str, Any] | None = None

    def add(self, e: dict[str, Any]) -> None:
        self.attempts += 1
        self.stepped += bool(e.get("stepped"))
        for name, total in self.scalars.items():
            if isinstance(e.get(name), (int, float)):
                total[0] += e[name]
                total[1] += 1
        for bucket, heads in (e.get("value_statistics") or {}).items():
            for head, values in heads.items():
                if len(values) != len(VALUE_FIELDS):
                    raise ValueError(f"value_statistics {bucket}/{head}: "
                                     f"{len(values)} fields, expected {len(VALUE_FIELDS)}")
                row = self.pooled.setdefault(bucket, {}).setdefault(
                    head, [0.0] * len(VALUE_FIELDS))
                for i, value in enumerate(values):
                    row[i] += value
        raw = e.get("advantage_raw")
        if isinstance(raw, dict):
            self.datasets += 1
            self.raw_mean += raw["mean"]
            self.raw_std += raw["std"]
            self.raw_last = raw

    def result(self) -> dict[str, Any]:
        out: dict[str, Any] = {"attempts": self.attempts, "stepped": self.stepped}
        for name, (total, count) in self.scalars.items():
            out[name] = total / count if count else None
        # "td" = vs lambda-return, "terminal" = vs final outcome, "hp" in HP points.
        out["value"] = {bucket: {head: value_summary(values, 100.0 if head == "hp" else 1.0)
                                 for head, values in heads.items()}
                        for bucket, heads in self.pooled.items()} or None
        out["advantage_raw"] = {
            "datasets": self.datasets, "mean": self.raw_mean / self.datasets,
            "std": self.raw_std / self.datasets, "last": self.raw_last} if self.datasets else None
        return out


class _Boundary:
    """SGD boundary sections (#537), one entry per dataset: wall and CPU seconds."""

    def __init__(self) -> None:
        self.datasets = 0
        self.totals: dict[str, dict[str, float]] = {"boundary_sections": {},
                                                    "boundary_sections_cpu": {}}
        self.seconds = [0.0, 0]

    def add(self, timing: dict[str, Any]) -> None:
        self.datasets += 1
        for key, total in self.totals.items():
            for name, seconds in (timing.get(key) or {}).items():
                total[name] = total.get(name, 0.0) + seconds
        if isinstance(timing.get("boundary_seconds"), (int, float)):
            self.seconds[0] += timing["boundary_seconds"]
            self.seconds[1] += 1

    def result(self) -> dict[str, Any]:
        out: dict[str, Any] = {"datasets": self.datasets}
        for key, total in self.totals.items():
            out[key + "_seconds_total"] = dict(total)
            out[key + "_seconds_mean"] = {k: v / self.datasets for k, v in total.items()}
        total_seconds, count = self.seconds
        out["boundary_seconds_mean"] = total_seconds / count if count else None
        return out


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


class _Games:
    """Scored training games: floor, wins, reward v2.1 and per-Boss reached/cleared."""

    def __init__(self, games: Iterable[dict[str, Any]] = ()) -> None:
        self.games = self.floor_sum = self.wins = self.rewarded = 0
        self.reward_sum = 0.0
        self.bosses = [[0, 0, 0] for _ in BOSS_NAMES]  # games, reached, cleared
        for game in games:
            self.add(game)

    def add(self, game: dict[str, Any]) -> None:
        self.games += 1
        self.floor_sum += game["floor"]
        self.wins += game["win"]
        if game["reward"] is not None:
            self.reward_sum += game["reward"]
            self.rewarded += 1
        for boss, (reached, cleared) in zip(self.bosses, game["bosses"], strict=False):
            boss[0] += 1
            boss[1] += reached
            boss[2] += cleared

    def result(self) -> dict[str, Any]:
        if not self.games:
            return {"games": 0}
        out: dict[str, Any] = {
            "games": self.games, "mean_floor": self.floor_sum / self.games,
            "win_rate": self.wins / self.games,
            "mean_reward_v21": self.reward_sum / self.rewarded if self.rewarded else None,
            "reward_undefined_games": self.games - self.rewarded, "bosses": {}}
        for name, (games, reached, cleared) in zip(BOSS_NAMES, self.bosses, strict=True):
            if games:
                out["bosses"][name] = {
                    "games": games, "reached": reached, "cleared": cleared,
                    "clear_rate": cleared / games,
                    "clear_rate_given_reached": cleared / reached if reached else None}
        return out


def _count(table: dict[str, int], key: Any) -> None:
    table[str(key)] = table.get(str(key), 0) + 1


def _window(items: deque[tuple[float, Any]], t: float, item: Any, window: float) -> None:
    """Append and keep only the items of the last `window` seconds (t >= newest - window)."""
    items.append((t, item))
    while items[0][0] < t - window:
        items.popleft()


class Readout:
    """One run's readout, folded incrementally (see the module docstring).

    update() parses the complete lines appended since the last update and folds them;
    record() is the run record from the folded state. A Readout whose update raised is
    discarded by its owner (supervise starts a fresh one), never updated again."""

    def __init__(self, run: Path, window: float = 1200.0, plan_path: Path | None = None):
        self.run, self.window = Path(run), float(window)
        self.plan_path = plan_path or self.run / "plan.json"
        self.events = Tail(self.run / "learner-events.jsonl")
        self.status = Tail(self.run / "learner-status.jsonl")
        self.drivers: dict[str, tuple[Tail, set[Any]]] = {}  # path -> tail, open admitted
        # learner events
        self.t0: float | None = None
        self.compile = _Compile()
        self.learning, self.boundary = _Learning(), _Boundary()
        self.learning_latest: deque[tuple[float, Any]] = deque()
        self.boundary_latest: deque[tuple[float, Any]] = deque()
        self.kl_stopped = self.handoff_lost = 0
        self.checkpoints: list[dict[str, Any]] = []
        # stepped attempts: run sums, the best window and the latest window
        self.steps: dict[str, Any] = {"count": 0, "td": 0, "aux": 0, "complete": True,
                                      "first": None, "last": None}
        self.recent: deque[tuple[float, Any]] = deque()  # (t, (incl, td, seconds))
        self.recent_rows = self.recent_td = 0
        self.best: tuple[int, float, float] = (0, 0.0, 0.0)
        self.best_td, self.best_seconds = 0, []
        # learner status (one line per 10 s): only the fields the edges read
        self.timed: list[dict[str, Any]] = []
        self.counted: list[dict[str, Any]] = []
        self.learner_last: dict[str, Any] = {}
        # drivers
        self.tables: dict[str, dict[str, dict[str, Any]]] = {
            "arm": {}, "ascension": {}, "start": {}, "k": {}}
        # open K groups: driver dir/group -> k, games, low, high, closes (dropped at K closes;
        # its share stays in k_rows)
        self.groups: dict[str, list[Any]] = {}
        self.k_rows: dict[str, list[int]] = {}
        self.closed: dict[str, int] = {}
        self.details: dict[str, dict[str, int]] = {}
        self.refused: dict[str, int] = {}
        self.open_refused: dict[str, int] = {}
        self.failures: dict[str, int] = {}
        self.unscored = 0
        self.games_all, self.games_level = _Games(), {}
        self.rolling_all: deque[dict[str, Any]] = deque(maxlen=ROLLING_GAMES)
        self.rolling_level: dict[int, deque[dict[str, Any]]] = {}
        self.games_t: tuple[float, float] | None = None

    @property
    def read_bytes(self) -> int:
        """Bytes consumed so far from the folded files."""
        return (self.events.offset + self.status.offset
                + sum(tail.offset for tail, _ in self.drivers.values()))

    def update(self) -> Readout:
        for records in self.events.chunks():
            for e in records:
                self._event(e)
        for records in self.status.chunks():
            for x in records:
                self._status(x)
        games: list[dict[str, Any]] = []
        for path in sorted(glob.glob(str(self.run / "env-n*" / "p*" / "driver.jsonl"))):
            tail, admitted = self.drivers.setdefault(path, (Tail(path), set()))
            for records in tail.chunks():
                for r in records:
                    self._driver(path, r, admitted, games)
        for game in sorted(games, key=lambda g: g["t"]):  # rolling = last N by close time
            self._game(game)
        return self

    # ---------------- folding ----------------
    def _event(self, e: dict[str, Any]) -> None:
        kind = e.get("kind")
        self.compile.add(e)
        if kind == "progress":
            self._progress(e)
        elif kind == "training_started" and self.t0 is None:
            self.t0 = e["t"]
        elif kind == "checkpoint_published":
            self.checkpoints.append({"checkpoint_artifact_id": e.get("checkpoint_artifact_id"),
                                     "sequence": e.get("sequence"), "t": e.get("t")})
        elif kind == "fast_handoff_lost":
            self.handoff_lost += 1

    def _progress(self, e: dict[str, Any]) -> None:
        """Every attempt (KL-stopped included: their numbers are pre-step)."""
        self.learning.add(e)
        _window(self.learning_latest, e["t"], {k: e[k] for k in LEARNING_KEYS if k in e},
                self.window)
        self.kl_stopped += bool(e.get("kl_stopped"))
        timing = e.get("sgd_timing")
        if isinstance(timing, dict) and isinstance(timing.get("boundary_sections"), dict):
            self.boundary.add(timing)
            _window(self.boundary_latest, e["t"], timing, self.window)
        if e.get("stepped"):
            self._stepped(e)

    def _stepped(self, e: dict[str, Any]) -> None:
        """Training rows INCLUDE aux: td `rows` + `terminal_rows` + `hp_rows` (every target
        is in this unit). A stepped event without the aux fields makes the incl.-aux
        number unknown, never td-only. Best window: the highest row sum over any `window`
        seconds that ends at least `window` after training_started."""
        s, td = self.steps, e["rows"]
        rows = td + e.get("terminal_rows", 0) + e.get("hp_rows", 0)
        s["complete"] = s["complete"] and set(e) >= AUX
        s["count"] += 1
        s["td"] += td
        s["aux"] += rows - td
        slim = {k: e[k] for k in ("t", "first_use", "first_use_multi") if k in e}
        s["first"] = s["first"] or slim
        s["last"] = slim
        t = e["t"]
        self.recent.append((t, (rows, td, e.get("seconds"))))
        self.recent_rows += rows
        self.recent_td += td
        while self.recent[0][0] < t - self.window:
            _, (old_rows, old_td, _) = self.recent.popleft()
            self.recent_rows -= old_rows
            self.recent_td -= old_td
        if (self.t0 is not None and t - self.t0 >= self.window
                and self.recent_rows > self.best[0]):
            self.best = (self.recent_rows, self.recent[0][0], t)
            self.best_td = self.recent_td
            self.best_seconds = [x[1][2] for x in self.recent if x[1][2] is not None]

    def _status(self, x: dict[str, Any]) -> None:
        learner = (x.get("fast_chain") or {}).get("learner")
        learner = learner if isinstance(learner, dict) else None
        counts = {"fast_chain": {"learner": {
            k: learner[k] for k in ("admitted_rows", "capacity_dropped_rows") if k in learner}}
        } if learner else {}
        if "timing" in x:
            timing = x["timing"] if isinstance(x["timing"], dict) else {}
            self.timed.append({"t": x["t"], "queued_rows": x.get("queued_rows"), **counts,
                               "timing": {k: timing[k] for k in TIMING if k in timing}})
        if learner:
            self.counted.append({"t": x["t"], **counts})
            self.learner_last = learner

    def _driver(self, path: str, r: dict[str, Any], admitted: set[Any],
                games: list[dict[str, Any]]) -> None:
        """Admission precedes its game's close (the env driver logs it while the game
        runs), so the admitted set holds only open games."""
        kind = r.get("event")
        if kind == "admission":
            if r.get("ok"):
                admitted.add(r.get("game_ref"))
            else:
                _count(self.refused, r.get("code"))
        elif kind == "closed":
            self._closed(path, r, r.get("game_ref") in admitted, games)
            admitted.discard(r.get("game_ref"))
        elif kind == "open_refused":
            _count(self.open_refused, r.get("reason"))
        elif kind in ("slot_failed", "build_output_stopped", "fatal", "evidence_failed"):
            _count(self.failures, kind)

    def _closed(self, path: str, r: dict[str, Any], admitted: bool,
                games: list[dict[str, Any]]) -> None:
        reason = r.get("reason")
        _count(self.closed, reason)
        if reason != "terminal":
            _count(self.details.setdefault(str(reason), {}), r.get("detail"))
        if r.get("evidence_failed"):
            _count(self.failures, "evidence_failed_close")
        counted = admitted and not r.get("evidence_failed")
        if "arm" in r:
            self._recipe(path, r, counted)
        if not (counted and reason == "terminal" and r.get("arm") == "control"
                and r.get("start") == "new_run"):
            return
        floor, ascension, terminal = r.get("floor"), r.get("ascension"), r.get("terminal")
        if (terminal not in ("victory", "defeat") or type(floor) is not int
                or type(ascension) is not int):
            self.unscored += 1  # no natural end on record: counted apart, never scored
            return
        win = terminal == "victory"
        bosses = (*BOSS_FLOORS, 49) if ascension == 10 else BOSS_FLOORS
        games.append({"t": r["t"], "ascension": ascension, "floor": floor, "win": win,
                      "reward": reward_v21(terminal, floor, ascension),
                      "bosses": [(win or floor >= b, win or floor > b) for b in bosses]})

    def _recipe(self, path: str, r: dict[str, Any], counted: bool) -> None:
        for key, table in self.tables.items():
            _add(table.setdefault(str(r.get("replay_k" if key == "k" else key)), _bucket()),
                 r, counted)
        name = f"{Path(path).parent}/{r.get('group')}"
        group = self.groups.get(name)
        if group is None:
            group = self.groups[name] = [str(r.get("replay_k")), 0, 0, 0, 0]
            self.k_rows.setdefault(group[0], [0, 0, 0, 0])[0] += 1
        if counted and r.get("reason") == "terminal":
            k_row, floor = self.k_rows[group[0]], r.get("floor") or 0
            _group_share(group, k_row, -1)
            group[2] = min(group[2], floor) if group[1] else floor
            group[3] = max(group[3], floor) if group[1] else floor
            group[1] += 1
            _group_share(group, k_row, 1)
        group[4] += 1  # every member game closes once: after K closes the group is done
        if group[0].isdigit() and group[4] >= int(group[0]):
            del self.groups[name]

    def _game(self, game: dict[str, Any]) -> None:
        level = game["ascension"]
        self.games_all.add(game)
        self.games_level.setdefault(level, _Games()).add(game)
        self.rolling_all.append(game)
        self.rolling_level.setdefault(level, deque(maxlen=ROLLING_GAMES)).append(game)
        first, last = self.games_t or (game["t"], game["t"])
        self.games_t = (min(first, game["t"]), max(last, game["t"]))

    # ---------------- the record ----------------
    def training(self) -> dict[str, Any]:
        s = self.steps
        if self.t0 is None or not s["count"]:
            return {"rows_h": None, "td_rows_h": None, "window": None}
        complete = s["complete"]
        rows, a_t, z_t = self.best
        td, seconds = self.best_td, self.best_seconds
        if rows == 0:  # run shorter than the window: whole run (all of it is recent)
            rows, a_t, z_t = s["td"] + s["aux"], s["first"]["t"], s["last"]["t"]
            td, seconds = s["td"], [x[1][2] for x in self.recent if x[1][2] is not None]
        span = max(z_t - a_t, 1e-9)
        secs = sorted(seconds)
        out: dict[str, Any] = {
            "t0": self.t0, "window": (a_t, z_t), "rows": rows if complete else None,
            "rows_s": rows / span if complete else None,
            "rows_h": rows / span * 3600 if complete else None,
            "td_rows_h": td / span * 3600,
            "aux_share": (rows - td) / rows if complete and rows else None,
            "aux_missing": not complete,
            "step_median_s": secs[len(secs) // 2] if secs else None,
            "step_p90_s": secs[int(len(secs) * 0.9)] if secs else None}
        if self.timed:
            a, z = nearest(self.timed, a_t), nearest(self.timed, z_t)
            dt = max(z["t"] - a["t"], 1e-9)
            pct = {k: 100 * delta(a, z, "timing", k) / dt for k in TIMING}
            out.update({
                "prepare_wait_pct": pct["preparation_wait_seconds"],
                "stage_wait_pct": pct["sgd_stage_wait_seconds"],
                "optimizer_pct": pct["optimizer_attempt_seconds"],
                "transfer_pct": pct["input_transfer_host_seconds"],
                "admitted_rows_s": delta(a, z, "fast_chain", "learner", "admitted_rows") / dt,
                "dropped_rows_s": delta(a, z, "fast_chain", "learner",
                                        "capacity_dropped_rows") / dt,
                "queued_rows": z.get("queued_rows")})
        return out

    def throughput(self, train: dict[str, Any]) -> dict[str, Any]:
        """Training rows/h with and without aux rows (best window, latest window, whole run),
        first use and intake. First use and admitted rows are deltas over the same span."""
        s = self.steps
        if self.t0 is None or not s["count"]:
            return {}
        complete = s["complete"]

        def rates(td: float, aux: float, seconds: float) -> tuple[Any, Any]:
            hours = max(seconds, 1e-9) / 3600
            return ((td + aux) / hours if complete else None), td / hours

        first, last = s["first"], s["last"]
        run_seconds = last["t"] - self.t0
        run_incl, run_td = rates(s["td"], s["aux"], run_seconds)
        late_incl, late_td = rates(self.recent_td, self.recent_rows - self.recent_td,
                                   min(self.window, run_seconds))
        first_use = (last.get("first_use", 0) - first.get("first_use", 0)
                     if "first_use" in last else None)
        out: dict[str, Any] = {
            "best_window_training_rows_per_h_incl_aux": train.get("rows_h"),
            "best_window_training_rows_per_h_td_only": train.get("td_rows_h"),
            "latest_window_seconds": self.window,
            "latest_training_rows_per_h_incl_aux": late_incl,
            "latest_training_rows_per_h_td_only": late_td,
            "run_training_seconds": run_seconds,
            "run_training_rows_per_h_incl_aux": run_incl,
            "run_training_rows_per_h_td_only": run_td,
            "run_training_rows_td_only": s["td"],
            "run_training_rows_aux": s["aux"] if complete else None,
            "updates": s["count"], "kl_stopped_attempts": self.kl_stopped,
            "first_use_rows_total": last.get("first_use"),
            "first_use_multi_rows_total": last.get("first_use_multi"),
            "first_use_rows": first_use,
            "first_use_rows_per_h": (first_use / max(last["t"] - first["t"], 1e-9) * 3600
                                     if first_use is not None else None)}
        if self.counted:
            z = self.learner_last
            out.update({key + "_total": z.get(key) for key in (
                "admitted_rows", "capacity_dropped_rows", "capacity_evicted_rows",
                "capacity_refused_rows", "excluded_dropped_rows", "excluded_trained_rows")})
            out["dropped_rows_total"] = (z.get("capacity_dropped_rows", 0)
                                         + z.get("excluded_dropped_rows", 0))
            a, b = nearest(self.counted, first["t"]), nearest(self.counted, last["t"])
            admitted = delta(a, b, "fast_chain", "learner", "admitted_rows")
            out["admitted_rows"] = admitted
            out["first_use_ratio_of_admitted"] = (first_use / admitted
                                                  if first_use is not None and admitted
                                                  else None)
        return out

    def learning_result(self) -> dict[str, Any]:
        """PPO and value/HP diagnostics over every progress attempt: the latest `window`
        seconds and the whole run."""
        if not self.learning.attempts:
            return {}
        latest = _Learning()
        for _, e in self.learning_latest:
            latest.add(e)
        return {"latest_window_seconds": self.window, "latest": latest.result(),
                "run_mean": self.learning.result()}

    def boundary_result(self) -> dict[str, Any]:
        """Once per dataset on its first step: total and mean per dataset, latest window
        and whole run."""
        if not self.boundary.datasets:
            return {}
        latest = _Boundary()
        for _, timing in self.boundary_latest:
            latest.add(timing)
        return {"latest_window_seconds": self.window, "latest": latest.result(),
                "run": self.boundary.result()}

    def training_games(self) -> dict[str, Any]:
        """Sampled-play results straight from training data: the control arm's full games
        (start new_run) that are complete (terminal, admitted, evidence ok) with a natural
        end (victory or defeat, int floor and ascension), per ascension and all together,
        over the whole run and the last ROLLING_GAMES by close time. A Boss on floor b is
        cleared iff the game was won or ended above b (natural layout). Other complete
        closes are `unscored_closes` (counted, never scored)."""
        if not self.games_all.games and not self.unscored:
            return {}
        levels = sorted(self.games_level)
        first, last = self.games_t or (0.0, 0.0)
        count = self.games_all.games
        return {"filter": "arm control, start new_run, complete (terminal, admitted, "
                          "evidence ok), result victory/defeat",
                "unscored_closes": self.unscored, "rolling_games": ROLLING_GAMES,
                "complete_games_per_h": (count / (max(last - first, 1) / 3_600_000)
                                         if count > 1 else None),
                "rolling": {"all": _Games(self.rolling_all).result(),
                            **{f"A{a}": _Games(self.rolling_level[a]).result()
                               for a in levels}},
                "run": {"all": self.games_all.result(),
                        **{f"A{a}": self.games_level[a].result() for a in levels}}}

    def recipe(self) -> dict[str, Any]:
        if not self.tables["arm"]:
            return {}
        out: dict[str, Any] = {key: {name: _finish(b) for name, b in sorted(table.items())}
                               for key, table in self.tables.items()}
        for k, row in out["k"].items():
            groups, full, spread, ranged = self.k_rows.get(k, [0, 0, 0, 0])
            row.update({"groups": groups, "full_groups": full,
                        "mean_floor_range": spread / ranged if ranged else None})
        return out

    def incidents(self) -> dict[str, Any]:
        """Stops, refusals and unknowns with cause counts: env closes by reason (and detail),
        admission refusals by code, open refusals and driver failures by kind, the learner's
        report failure / STOP / disk stop / gpu rc and lost hand-offs."""
        run = self.run
        report = _json(run / "learner-report.json") or {}
        text = (run / "gpu-rc").read_text().strip() if (run / "gpu-rc").exists() else None
        return {"env_closed_by_reason": dict(self.closed),
                "env_close_details": {k: dict(v) for k, v in self.details.items()},
                "admission_refused_by_code": dict(self.refused),
                "open_refused_by_reason": dict(self.open_refused),
                "driver_failures_by_kind": dict(self.failures),
                "learner_failure": report.get("failure"),
                "learner_input_failure": report.get("input_failure"),
                "stop": (run / "STOP").read_text().strip() if (run / "STOP").exists() else None,
                "disk_stop": ((run / "disk-stop").read_text().strip()
                              if (run / "disk-stop").exists() else None),
                "gpu_rc": int(text) if text and text.lstrip("-").isdigit() else text,
                "handoff_lost": self.handoff_lost}

    def record(self) -> dict[str, Any]:
        plan = json.loads(self.plan_path.read_text())
        train = self.training()
        span = train.get("window")
        target = plan["target"]
        return {"schema": SCHEMA, "name": plan["name"], "target": target, "training": train,
                "services": service_edges(self.run, span), "gpu": gpu_cards(self.run, span),
                "env": env_edge(self.run, span), "compile": self.compile.result(),
                "disk": disk_edge(self.run), "recipe": self.recipe(),
                "verdict": verdict(train.get("rows_h"), target),
                "loss_table": loss_table(plan, train),
                "identity": identity_edge(self.run, plan, self.plan_path),
                "throughput": self.throughput(train),
                "learning": self.learning_result(),
                "training_games": self.training_games(),
                "boundary_sections": self.boundary_result(),
                "incidents": self.incidents(),
                "checkpoints": list(self.checkpoints)}


def build(run: Path, window: float, plan_path: Path | None = None) -> dict[str, Any]:
    """The run record from one fresh, whole read (the CLI; supervise keeps a Readout)."""
    return Readout(run, window, plan_path).update().record()


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
    if games.get("unscored_closes"):
        out.append(f"training games: {games['unscored_closes']} complete control closes "
                   "without a natural end (no victory/defeat or floor) not scored")
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
