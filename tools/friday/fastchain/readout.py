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
diagnostics and PPO statistics (latest window and run mean; with c_hp > 0 the HP term's
share of the advantage variance), the exploration dose per arm (rank of the chosen action
in the model's own ordering, top p > 0.99 share, non-top choices with a positive
advantage; by decision type and act) and the PPO update's clip fraction and approximate
KL per arm, per combat type the format-4 HP score counters (all arms together and per
arm), training-game results of the control arm's full games and of every arm's (rolling
over the profile's `readout_rolling_games` windows, default the last 100, and whole run,
per ascension), the learning-settings diagnostics (gradient-clip bind rate and pre-clip
norm mean/p95, value-target entropy, value-head calibration on terminal rows, policy
entropy by decision type/act/arm, row age as version lag and behaviour-model age), the
game value-target intake and end counters, the SGD boundary sections,
stops/refusals/unknowns with cause counts and the checkpoint ids.
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
import math
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
                    "gradient_norm",
                    # ticket K: control-arm KL (kl_stop_scope "control" only) and the lr used
                    "approx_kl_control", "lr",
                    # #632 (stpd.rl.loss.SCORE_READOUT): per attempt p50/p90 of policy rows'
                    # raw score mean, max |score|, max - min; |action + read| on diagnostics
                    # steps only (absent elsewhere); averaged over attempts like the rest;
                    # under data parallel rank 0's rows only (as the loss.py comment)
                    "score_row_mean_p50", "score_row_mean_p90", "score_max_abs_p50",
                    "score_max_abs_p90", "score_range_p50", "score_range_p90",
                    "score_read_norm_p50", "score_read_norm_p90",
                    # stpd.rl.loss.BARRIER_READOUT (loss.noncombat_log_barrier_coef > 0 only):
                    # the barrier term, its rows, p50/p10 of their smallest legal probability;
                    # rank 0's rows under data parallel, like the scores
                    "noncombat_barrier", "noncombat_barrier_rows",
                    "noncombat_min_probability_p50", "noncombat_min_probability_p10",
                    # segments.observe_arms only: policy rows per attempt that learn / observe
                    "policy_learning_rows", "policy_observed_rows")
# stpd.rl.learning_diagnostics.exploration_fields: per "arm|actN|decision" these counts.
EXPLORATION_FIELDS = ("rows", "second", "third_plus", "top_p_over_099", "nontop_positive")
# stpd.rl.learning_diagnostics.LOW_PROBABILITY_FIELDS: per "arm|decision", noncombat rows
# whose chosen option had model probability (T 1) < 5%, plus the distinct games (`games`)
LOW_PROBABILITY_FIELDS = ("rows", "advantage_sum", "outcome_rows", "outcome_minus_value_sum")
# stpd.rl.learning_diagnostics.POLICY_ARM_FIELDS: per arm, summed over policy rows
UNATTRIBUTED = "unattributed"  # learning_diagnostics: rows of a source without a table
POLICY_ARM_FIELDS = ("rows", "approx_kl_sum", "outside_clip", "ratio_nonfinite")
# stpd.rl.learning_diagnostics.EXPLORE_FLOOR_FIELDS (loss.explore_ratio_floor > 0 only)
EXPLORE_FLOOR_FIELDS = ("rows", "floored", "floored_positive", "floored_abs_advantage_sum",
                        "floor_weight_sum", "ratio_weight_sum")
# RL5-METHODS R, learning settings (stpd.rl.learning_diagnostics / segment_learner):
ENTROPY_FIELDS = ("rows", "entropy_sum", "normalized_entropy_sum")  # per "arm|actN|decision"
CALIBRATION_FIELDS = ("rows", "predicted_sum", "realised_sum")  # per bin of predicted value
# learning_diagnostics value heads per exploration cell: per cell these heads' VALUE_FIELDS
# and the calibration bins (`value_by_cell`), and the cross-cell query (`value_cross_cell`)
CELL_HEADS = ("td", "terminal", "hp")
MU_AGE_EDGES = (5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 450, 600, 900, 1200,
                1800, 3600)  # segment_learner.MU_AGE_EDGES: bin upper edges, seconds
NORM_BINS_PER_OCTAVE = 8  # pre-clip gradient norm histogram: 2^(1/8) wide bins (9%)
VALUE_TARGET_MEANS = ("outcome_weight_mean", "noncombat_outcome_weight_mean",
                      "target_entropy_mean")
ROLLING_GAMES = 100  # the training-game rolling mean: the last N complete games
# effective-profile knob `readout_rolling_games` (#556): [headline, windows shown beside it]
ROLLING_KNOB = "readout_rolling_games"
SAVE_USE_BUCKETS = (("1", 1), ("2", 2), ("3-4", 4), ("5-8", 8), ("9-16", 16), (">16", 1 << 30))
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


def _last_record(path: str) -> dict[str, Any] | None:
    """The last complete JSON line of a sampler file (read from its end only)."""
    with open(path, "rb") as handle:
        handle.seek(0, 2)
        handle.seek(max(handle.tell() - (1 << 20), 0))
        lines = handle.read().splitlines()
    for line in reversed(lines):
        with contextlib.suppress(ValueError):
            return json.loads(line)
    return None


def _hp_scores(run: Path) -> list[dict[str, Any]]:
    """Each service's latest cumulative `assembler.hp_score` (format 4)."""
    return [((_last_record(path) or {}).get("assembler") or {}).get("hp_score") or {}
            for path in sorted(glob.glob(str(run / "service-status-*.jsonl")))]


def _hp_table(parts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, list[int]] = {}
    for by in parts:
        for kind, c in by.items():
            t = totals.setdefault(kind, [0, 0, 0])
            t[0] += c["battles"]
            t[1] += c["deaths"]
            t[2] += c["hp_change"]
    return {kind: {"combats": n, "deaths": d, "death_rate": d / n if n else None,
                   "mean_net_hp_change": h / n if n else None}
            for kind, (n, d, h) in totals.items()}


def hp_score_edge(run: Path, scores: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Segment format 4 (A2): per encounter type (normal / elite / boss / unknown) the
    combats the assemblers scored, their deaths and the mean net HP change per combat
    (H_T - H_0 + M_T - M_0, death -H_0; HP points, unweighted), from each service's
    latest cumulative `assembler.hp_score`. Gross damage taken never reaches a segment
    (only HP at decision states), so it is not here. Empty under formats 1-3."""
    scores = _hp_scores(run) if scores is None else scores
    return _hp_table(score.get("by_encounter") or {} for score in scores)


def hp_score_arm_edge(run: Path, scores: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The same per exploration arm (`<arm>+save` = that arm's save starts), from the
    assemblers' `hp_score.by_arm` (RL5-METHODS R); empty before it or under formats 1-3."""
    scores = _hp_scores(run) if scores is None else scores
    names = sorted({arm for score in scores for arm in score.get("by_arm") or {}})
    return {arm: _hp_table((score.get("by_arm") or {}).get(arm) or {} for score in scores)
            for arm in names}


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


def _start_model(learner: dict[str, Any], at_submit: dict[str, Any]) -> dict[str, Any] | None:
    """The start the learner was given (learner.json): a fresh start's package sha, which
    the learner verified, or a resume's, from the restored CP. The training_identity (a CP
    export's binding, a random init's seed) is read at submit (facts.json), so it is shown
    only for that same package; the submit-time sha stays in the record either way."""
    sha = learner.get("start_model_sha256")
    if not sha:
        return None
    same = at_submit.get("package_sha256") == sha
    return {"package_sha256": sha,
            "training_identity": at_submit.get("training_identity") if same else None,
            "site_package_at_submit": at_submit.get("package_sha256")}


def identity_edge(run: Path, plan: dict[str, Any], plan_path: Path) -> dict[str, Any]:
    """What this run is: plan/profile digests, source, layout name and recipe knobs.

    Source facts come from facts.json (launch.py, at freeze); the loss keys (c_hp and its
    scope included) and the start model from the learner config the learner actually read."""
    profile = _json(run / "effective-profile.json") or {}
    knobs = {name: knob.get("value") for name, knob in (profile.get("knobs") or {}).items()}
    facts = _json(run / "facts.json") or {}
    learner = _json(run / "learner" / "learner.json") or {}
    loss = learner.get("loss") or {}
    segments = learner.get("segments") or {}
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
        "start_model": _start_model(learner, facts.get("start_model") or {}),
        "resume_checkpoint": plan.get("resume_checkpoint"),
        # RL5-METHODS C: the declared recipe changes the learner was given (learner.json)
        "declared_changes": learner.get("resume_recipe_changes"),
        "placement": plan.get("placement"),
        "recipe": {"exploration_ladder": knobs.get("exploration_ladder"),
                   "ascension_mix": knobs.get("ascension_mix"),
                   "replay_k": knobs.get("replay_k"), "save_starts": knobs.get("save_starts"),
                   "segment_format": segment_format,
                   "hp_coef": loss.get("hp_coef"),
                   "c_hp": loss.get("hp_advantage_coef"),
                   "c_hp_scope": loss.get("hp_advantage_scope"),
                   "explore_ratio_floor": loss.get("explore_ratio_floor", 0.0),
                   "hp_combat_weights": knobs.get("hp_combat_weights"),
                   "hp_potion_mode": knobs.get("hp_potion_mode"),
                   # ticket K (#577): as the learner read them, else the profile knob
                   "kl_stop_scope": segments.get("kl_stop_scope", knobs.get("kl_stop_scope")),
                   "lr_warmup_steps": segments.get("lr_warmup_steps",
                                                   knobs.get("lr_warmup_steps"))}}


def restored_edge(plan: dict[str, Any], restored: dict[str, Any] | None) -> dict[str, Any]:
    """Which CP the learner actually restored, checked against the plan's resume CP, and
    the declared changes it applied. The learner restores the CP named by
    FC_RESUME_CHECKPOINT (the plan's) and the store checks its bytes against that content
    id, so a mismatch can only be a plumbing bug: it is an incident, not a stop. It does not
    catch a stale CP the plan itself chose (RL4.2); that one matches."""
    want = plan.get("resume_checkpoint")
    if restored is None:
        return {"expected": want, "restored": None,
                "matches_plan": None if want else True}
    return {"expected": want, **restored,
            "matches_plan": restored.get("checkpoint_artifact_id") == want}


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


LEARNING_KEYS = ("t", "stepped", *LEARNING_SCALARS, "value_statistics", "advantage_raw",
                 "exploration", "policy_by_arm", "explore_floor_by_arm", "gradient_clipped",
                 "value_target",
                 "value_calibration", "policy_entropy", "first_use_mu_age_seconds",
                 "value_by_cell", "value_cross_cell", "low_probability_noncombat",
                 "potion_discard", "combat_settle")
# first-use lag lists, kept in the latest window as {lag: rows} only
LAG_KEYS = ("first_use_version_lag", "first_use_pre_update_SGD_lag")


def _counts(values: Iterable[Any]) -> dict[int, int]:
    out: dict[int, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def _slim(e: dict[str, Any]) -> dict[str, Any]:
    """A progress event as the latest window keeps it: LEARNING_KEYS, lag lists counted."""
    out = {k: e[k] for k in LEARNING_KEYS if k in e}
    for key in LAG_KEYS:
        if isinstance(e.get(key), list):
            out[key] = _counts(e[key])
    return out


def _lag_summary(counts: dict[int, int]) -> dict[str, Any]:
    """n, mean and p95 (nearest rank, as segment_learner.lag_summary) of pooled lags."""
    n = sum(counts.values())
    if not n:
        return {"n": 0, "mean": None, "p95": None}
    rank, seen, p95 = min(n - 1, int(n * 0.95)), 0, None
    for lag in sorted(counts):
        seen += counts[lag]
        if seen > rank:
            p95 = lag
            break
    return {"n": n, "mean": sum(lag * c for lag, c in counts.items()) / n, "p95": p95}


def _histogram_p95(counts: list[int] | dict[int, int], upper: Any) -> Any:
    """The upper edge (`upper(bin)`) of the bin holding the 95th percentile (nearest rank):
    the p95 is at most this; None without rows or when it is in an open last bin."""
    items = sorted(counts.items()) if isinstance(counts, dict) else list(enumerate(counts))
    n = sum(c for _, c in items)
    if not n:
        return None
    rank, seen = min(n - 1, int(n * 0.95)), 0
    for index, c in items:
        seen += c
        if seen > rank:
            return upper(index)
    return None


def _shares(counts: list[int]) -> dict[str, Any]:
    rows, second, third, top, positive = counts
    nontop = second + third
    return {"rows": rows, "top": (rows - nontop) / rows, "second": second / rows,
            "third_plus": third / rows, "top_p_over_099": top / rows, "nontop_rows": nontop,
            "nontop_positive": positive / nontop if nontop else None}


def exploration_summary(counts: dict[str, list[int]]) -> dict[str, Any] | None:
    """Per arm, over its training rows (multi-choice): shares of the chosen action's rank
    in the model's own ordering (top / second / third or later), of rows whose model top
    probability is > 0.99, and of non-top choices whose advantage before normalisation is
    > 0; all rows, by decision type and by act."""
    arms: dict[str, dict[str, Any]] = {}
    for key, values in sorted(counts.items()):
        arm, act, decision = key.split("|")
        part = arms.setdefault(arm, {"all": [0] * 5, "by_decision": {}, "by_act": {}})
        for total in (part["all"], part["by_decision"].setdefault(decision, [0] * 5),
                      part["by_act"].setdefault(act, [0] * 5)):
            for i, value in enumerate(values):
                total[i] += value
    return {arm: {"all": _shares(part["all"]),
                  **{name: {k: _shares(v) for k, v in part[name].items()}
                     for name in ("by_decision", "by_act")}}
            for arm, part in arms.items()} or None


def _entropy_shares(values: list[float]) -> dict[str, Any]:
    rows, entropy, normalized = values
    return {"rows": round(rows), "entropy_mean": entropy / rows if rows else None,
            "normalized_entropy_mean": normalized / rows if rows else None}


def entropy_summary(counts: dict[str, list[float]]) -> dict[str, Any] | None:
    """The acting model's own policy entropy (temperature and uniform mix removed) over
    multi-choice training rows: mean nats and mean entropy / ln(choices), all arms
    together by decision type and by act, and per arm."""
    if not counts:
        return None
    total = [0, 0.0, 0.0]
    by: dict[str, dict[str, list[float]]] = {"by_decision": {}, "by_act": {}, "by_arm": {}}
    for key, values in sorted(counts.items()):
        arm, act, decision = key.split("|")
        for row in (total, by["by_decision"].setdefault(decision, [0, 0.0, 0.0]),
                    by["by_act"].setdefault(act, [0, 0.0, 0.0]),
                    by["by_arm"].setdefault(arm, [0, 0.0, 0.0])):
            for i, value in enumerate(values):
                row[i] += value
    return {"all": _entropy_shares(total),
            **{name: {k: _entropy_shares(v) for k, v in part.items()}
               for name, part in by.items()}}


class _Learning:
    """PPO scalars (mean per attempt) and pooled value/HP sums of progress attempts."""

    def __init__(self) -> None:
        self.attempts = self.stepped = self.datasets = 0
        self.scalars = {name: [0.0, 0] for name in LEARNING_SCALARS}
        self.pooled: dict[str, dict[str, list[float]]] = {}
        self.raw_mean = self.raw_std = 0.0
        self.raw_last: dict[str, Any] | None = None
        self.hp = [0.0, 0.0, 0]  # A2, c_hp > 0: sum of hp_share, of hp_std, datasets
        self.exploration: dict[str, list[int]] = {}
        # rare noncombat choices: per "arm|decision" LOW_PROBABILITY_FIELDS and game keys
        self.rare: dict[str, list[float]] = {}
        self.rare_games: dict[str, set[str]] = {}
        self.policy_arms: dict[str, list[float]] = {}
        self.floor_arms: dict[str, list[float]] = {}  # loss.explore_ratio_floor > 0
        self.cells: dict[str, dict[str, Any]] = {}  # value_by_cell, pooled sums
        # value_cross_cell: queried configs (latest), rows per own cell; per (own, queried)
        # pair the sum and its rows (a dataset queries only the cells it has); per queried
        # cell sum/rows of V(cell) - V(control) and per own cell of V(control) - V(own),
        # each over the events that queried both
        self.cross: dict[str, Any] = {"configs": {}, "rows": {}, "sums": {}, "pair_rows": {},
                                      "vs_control": {}, "control_own": {}}
        self.raw_variance = [0.0, 0]  # sum, datasets that report it (#564)
        # R: optimizer steps with a finite norm, of them clip-bound, norm sum; norm bins
        self.clip = [0, 0, 0.0]
        self.norms: dict[int, int] = {}
        self.value_target: dict[str, list[float]] = {k: [0.0, 0] for k in VALUE_TARGET_MEANS}
        self.value_target_last: dict[str, Any] | None = None
        self.calibration: list[list[float]] = []
        self.entropy: dict[str, list[float]] = {}
        self.discard: dict[str, list[int]] = {}  # recipe A: per arm [offered, chosen]
        # combat-end settlement: combat rows, settled, lost, G sum / sq, G' sum / sq
        self.settle: list[float] | None = None
        self.lags: dict[str, dict[int, int]] = {key: {} for key in LAG_KEYS}
        self.mu_age = [0, 0, 0.0, [0] * (len(MU_AGE_EDGES) + 1)]  # n, missing, sum, bins

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
            if isinstance(raw.get("hp_share"), (int, float)):
                self.hp[0] += raw["hp_share"]
                self.hp[1] += raw["hp_std"]
                self.hp[2] += 1
        for key, values in (e.get("exploration") or {}).items():
            if len(values) != len(EXPLORATION_FIELDS):
                raise ValueError(f"exploration {key}: {len(values)} fields, expected "
                                 f"{len(EXPLORATION_FIELDS)}")
            row = self.exploration.setdefault(key, [0] * len(EXPLORATION_FIELDS))
            for i, value in enumerate(values):
                row[i] += value
        rare = e.get("low_probability_noncombat") or {}
        for key, values in (rare.get("rows") or {}).items():
            if len(values) != len(LOW_PROBABILITY_FIELDS):
                raise ValueError(f"low_probability_noncombat {key}: {len(values)} fields, "
                                 f"expected {len(LOW_PROBABILITY_FIELDS)}")
            row = self.rare.setdefault(key, [0, 0.0, 0, 0.0])
            for i, value in enumerate(values):
                row[i] += value
            self.rare_games.setdefault(key, set()).update(rare["games"].get(key, ()))
        for arm, values in (e.get("policy_by_arm") or {}).items():
            if len(values) != len(POLICY_ARM_FIELDS):
                raise ValueError(f"policy_by_arm {arm}: {len(values)} fields, expected "
                                 f"{len(POLICY_ARM_FIELDS)}")
            row = self.policy_arms.setdefault(arm, [0] * len(POLICY_ARM_FIELDS))
            for i, value in enumerate(values):
                row[i] += value
        self._cells(e)
        if e.get("combat_settle") is not None:
            self.settle = [a + b for a, b in zip(self.settle or [0] * len(e["combat_settle"]),
                                                  e["combat_settle"], strict=True)]
        for arm, values in (e.get("potion_discard") or {}).items():
            row = self.discard.setdefault(arm, [0, 0])
            row[0] += values[0]
            row[1] += values[1]
        for arm, values in (e.get("explore_floor_by_arm") or {}).items():
            if len(values) != len(EXPLORE_FLOOR_FIELDS):
                raise ValueError(f"explore_floor_by_arm {arm}: {len(values)} fields, "
                                 f"expected {len(EXPLORE_FLOOR_FIELDS)}")
            row = self.floor_arms.setdefault(arm, [0] * len(EXPLORE_FLOOR_FIELDS))
            for i, value in enumerate(values):
                row[i] += value
        self._settings(e)

    def _cells(self, e: dict[str, Any]) -> None:
        """Value heads per exploration cell: pool the sums (all fields add)."""
        for cell, heads in (e.get("value_by_cell") or {}).items():
            pooled = self.cells.setdefault(cell, {
                **{head: [0.0] * len(VALUE_FIELDS) for head in CELL_HEADS},
                "calibration": [[0.0] * len(CALIBRATION_FIELDS) for _ in heads["calibration"]]})
            for head in CELL_HEADS:
                for i, value in enumerate(heads[head]):
                    pooled[head][i] += value
            for b, values in enumerate(heads["calibration"]):
                for i, value in enumerate(values):
                    pooled["calibration"][b][i] += value
        cross = e.get("value_cross_cell")
        if not cross:
            return
        pooled = self.cross
        pooled["configs"].update(cross["configs"])
        control = next((name for name, config in cross["configs"].items() if not any(config)),
                       None)

        def add(table: dict[str, list[float]], key: str, value: float, n: int) -> None:
            total = table.setdefault(key, [0.0, 0])
            total[0] += value
            total[1] += n

        for own, queried in cross["expected_reward_sum"].items():
            n = cross["rows"][own]
            pooled["rows"][own] = pooled["rows"].get(own, 0) + n
            sums = pooled["sums"].setdefault(own, {})
            pair = pooled["pair_rows"].setdefault(own, {})
            for query, value in queried.items():
                sums[query] = sums.get(query, 0.0) + value
                pair[query] = pair.get(query, 0) + n
            if control in queried:
                for query, value in queried.items():
                    if query != control:
                        add(pooled["vs_control"], query, value - queried[control], n)
                if own in queried and own != control:
                    add(pooled["control_own"], own, queried[control] - queried[own], n)

    def cells_result(self) -> dict[str, Any]:
        """Per cell: the outcome head vs lambda-return ("td") and vs the realised outcome
        ("terminal"), the HP head, calibration bins; the cross-cell table and its gaps."""
        out: dict[str, Any] = {"value_by_cell": {
            cell: {**{head: value_summary(p[head], 100.0 if head == "hp" else 1.0)
                      for head in CELL_HEADS},
                   "calibration": [
                       {"bin": [b / len(p["calibration"]), (b + 1) / len(p["calibration"])],
                        "rows": round(rows), "mean_predicted": predicted / rows,
                        "mean_realised": realised / rows}
                       for b, (rows, predicted, realised) in enumerate(p["calibration"])
                       if rows]}
            for cell, p in sorted(self.cells.items())} or None}
        cross = self.cross
        if not cross["rows"]:
            out["value_cross_cell"] = None
            return out
        configs = cross["configs"]
        out["value_cross_cell"] = {
            "rows": dict(sorted(cross["rows"].items())),
            # mean over the rows that queried that cell (pair rows, not own-cell rows)
            "mean_expected_reward": {
                own: {query: value / cross["pair_rows"][own][query]
                      for query, value in sorted(queried.items())}
                for own, queried in sorted(cross["sums"].items())},
            "control": next((name for name in sorted(configs) if not any(configs[name])),
                            None),
            # per queried cell: mean over all rows of V(row, cell) - V(row, control)
            "cell_minus_control": {
                query: {"config": configs[query], "gap": total / n, "rows": n}
                for query, (total, n) in sorted(cross["vs_control"].items()) if n} or None,
            # per own cell: mean over its rows of V(row, control) - V(row, own cell)
            "control_minus_own": {own: total / n for own, (total, n)
                                  in sorted(cross["control_own"].items()) if n} or None}
        return out

    def _settings(self, e: dict[str, Any]) -> None:
        """RL5-METHODS R: the folds that judge the learning settings."""
        raw = e.get("advantage_raw")
        if isinstance(raw, dict) and isinstance(raw.get("variance"), (int, float)):
            self.raw_variance[0] += raw["variance"]
            self.raw_variance[1] += 1
        norm = e.get("gradient_norm")
        if (e.get("stepped") and "gradient_clipped" in e and isinstance(norm, (int, float))
                and math.isfinite(norm)):
            self.clip[0] += 1
            self.clip[1] += bool(e["gradient_clipped"])
            self.clip[2] += norm
            index = (math.floor(math.log2(norm) * NORM_BINS_PER_OCTAVE) if norm > 0
                     else -(1 << 20))
            self.norms[index] = self.norms.get(index, 0) + 1
        target = e.get("value_target")
        if isinstance(target, dict):
            self.value_target_last = target
            for key, total in self.value_target.items():
                if isinstance(target.get(key), (int, float)):
                    total[0] += target[key]
                    total[1] += 1
        for b, values in enumerate(e.get("value_calibration") or ()):
            if len(values) != len(CALIBRATION_FIELDS):
                raise ValueError(f"value_calibration bin {b}: {len(values)} fields, expected "
                                 f"{len(CALIBRATION_FIELDS)}")
            while len(self.calibration) <= b:
                self.calibration.append([0.0] * len(CALIBRATION_FIELDS))
            for i, value in enumerate(values):
                self.calibration[b][i] += value
        for key, values in (e.get("policy_entropy") or {}).items():
            if len(values) != len(ENTROPY_FIELDS):
                raise ValueError(f"policy_entropy {key}: {len(values)} fields, expected "
                                 f"{len(ENTROPY_FIELDS)}")
            row = self.entropy.setdefault(key, [0, 0.0, 0.0])
            for i, value in enumerate(values):
                row[i] += value
        for key, total in self.lags.items():
            lags = e.get(key)
            for lag, count in (_counts(lags) if isinstance(lags, list)
                               else lags if isinstance(lags, dict) else {}).items():
                total[int(lag)] = total.get(int(lag), 0) + count
        age = e.get("first_use_mu_age_seconds")
        if isinstance(age, dict):
            self.mu_age[0] += age["n"]
            self.mu_age[1] += age["missing"]
            self.mu_age[2] += age["sum"]
            for i, count in enumerate(age["histogram"]):
                self.mu_age[3][i] += count

    def settings_result(self) -> dict[str, Any]:
        """Gradient clip, value targets and calibration, policy entropy, row age."""
        steps, bound, norm_sum = self.clip
        out: dict[str, Any] = {"gradient_clip": {
            "steps": steps, "bound": bound, "bind_rate": bound / steps if steps else None,
            "pre_clip_norm_mean": norm_sum / steps if steps else None,
            # upper edge of the 2^(1/8)-wide bin holding the p95: within 9% above it
            "pre_clip_norm_p95_at_most": _histogram_p95(
                self.norms, lambda i: 2 ** ((i + 1) / NORM_BINS_PER_OCTAVE))} if steps else None}
        out["value_target"] = {
            "datasets": max((n for _, n in self.value_target.values()), default=0),
            **{key: total / n if n else None for key, (total, n) in self.value_target.items()},
            "last": self.value_target_last} if self.value_target_last is not None else None
        # Terminal aux rows (games that ended), binned by the value head's predicted reward:
        # mean predicted vs mean realised reward v2.1 per bin of width 0.1.
        out["value_calibration"] = [
            {"bin": [b / len(self.calibration), (b + 1) / len(self.calibration)],
             "rows": round(rows),
             "mean_predicted": predicted / rows if rows else None,
             "mean_realised": realised / rows if rows else None}
            for b, (rows, predicted, realised) in enumerate(self.calibration)
        ] if any(rows for rows, _, _ in self.calibration) else None
        out["policy_entropy"] = entropy_summary(self.entropy)
        n, missing, total, bins = self.mu_age
        out["row_age"] = {
            "version_lag": _lag_summary(self.lags["first_use_version_lag"]),
            "pre_update_SGD_lag": _lag_summary(self.lags["first_use_pre_update_SGD_lag"]),
            # seconds from the row's behaviour model's publication to the row's first use:
            # an upper bound on the row's own age (the row came after that publication)
            "mu_age_seconds": {
                "n": n, "missing": missing, "mean": total / n if n else None,
                "p95_at_most": _histogram_p95(
                    bins, lambda i: MU_AGE_EDGES[i] if i < len(MU_AGE_EDGES) else None),
                "histogram_edges": MU_AGE_EDGES, "histogram": list(bins)}
            if n or missing else None}
        return out

    def result(self) -> dict[str, Any]:
        out: dict[str, Any] = {"attempts": self.attempts, "stepped": self.stepped}
        for name, (total, count) in self.scalars.items():
            out[name] = total / count if count else None
        # "td" = vs lambda-return, "terminal" = vs final outcome, "hp" in HP points.
        out["value"] = {bucket: {head: value_summary(values, 100.0 if head == "hp" else 1.0)
                                 for head, values in heads.items()}
                        for bucket, heads in self.pooled.items()} or None
        hp_share, hp_std, hp_datasets = self.hp
        out["advantage_raw"] = {
            "datasets": self.datasets, "mean": self.raw_mean / self.datasets,
            "std": self.raw_std / self.datasets, "last": self.raw_last,
            # c_hp * A_hp's share of Var(A) and its std, mean over datasets (None: c_hp 0)
            "hp_share": hp_share / hp_datasets if hp_datasets else None,
            "hp_std": hp_std / hp_datasets if hp_datasets else None,
            "variance": (self.raw_variance[0] / self.raw_variance[1]
                         if self.raw_variance[1] else None)} if self.datasets else None
        out["exploration"] = exploration_summary(self.exploration)
        # Noncombat multi-choice training rows whose chosen option had model probability
        # (T 1, recovered from q) < 5%, per arm and decision type: rows, distinct games, mean
        # advantage before normalisation, and over the rows whose game's real reward is known
        # ("game" value targets, terminal end) the mean of real reward - the row's V.
        out["low_probability_noncombat"] = {
            key: {"rows": round(rows), "games": len(self.rare_games.get(key, ())),
                  "advantage_mean": advantage / rows if rows else None,
                  "outcome_rows": round(known),
                  "outcome_minus_value_mean": gap / known if known else None}
            for key, (rows, advantage, known, gap) in sorted(self.rare.items())} or None
        # Per arm, pooled over its policy rows of every attempt (KL-stopped included):
        # share outside the clip range and mean r - 1 - log r. The run-wide scalars above
        # are means of per-attempt means, so they need not equal a row-weighted mix of these.
        out["policy_by_arm"] = {
            arm: {"rows": round(rows), "clip_fraction": clipped / rows if rows else None,
                  "approx_kl": kl / (rows - bad) if rows > bad else None,
                  "ratio_nonfinite": round(bad)}
            for arm, (rows, kl, clipped, bad) in sorted(self.policy_arms.items())} or None
        # loss.explore_ratio_floor c > 0: per arm, pooled like policy_by_arm, the rows the
        # floor took and the gradient weight c |A| they got vs what the clipped surrogate
        # gave (r |A|, 0 for clip-bound A < 0 rows).
        out["explore_floor_by_arm"] = {
            arm: {"rows": round(rows), "floored_per_1000": 1000 * floored / rows if rows else None,
                  "floored_positive_share": positive / floored if floored else None,
                  "floored_mean_abs_advantage": magnitude / floored if floored else None,
                  "floor_weight_sum": given, "ratio_weight_sum": replaced}
            for arm, (rows, floored, positive, magnitude, given, replaced)
            in sorted(self.floor_arms.items())} or None
        # Recipe A: per arm (save starts apart, "+save"), noncombat multi-choice training rows
        # offering potion.discard, the share choosing it (row visits, KL-stopped included).
        out["potion_discard"] = {
            arm: {"rows_offered": offered, "chosen": chosen, "rate": chosen / offered}
            for arm, (offered, chosen) in sorted(self.discard.items()) if offered} or None
        # Combat-end settlement (segments.combat_end_kappa < 1): training combat rows per
        # dataset (row visits), the share settled, rows of lost battles (kept G), and the
        # standard deviation of the same rows' outcome advantage under G and under G'.
        out["combat_settle"] = None
        if self.settle is not None and self.settle[0]:
            n, settled, lost, s_old, q_old, s_new, q_new = self.settle

            def std(total: float, square: float) -> float:
                return max(square / n - (total / n) ** 2, 0.0) ** 0.5

            out["combat_settle"] = {"combat_rows": round(n), "settled_rows": round(settled),
                                    "settled_share": settled / n, "lost_rows": round(lost),
                                    "std_game": std(s_old, q_old),
                                    "std_settled": std(s_new, q_new)}
        out.update(self.settings_result())
        out.update(self.cells_result())
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
            "reward_undefined_games": self.games - self.rewarded,
            # = the act-2 Boss clear rate: a game reaches act 3 iff it clears that Boss
            "act3_reached_rate": self.bosses[1][2] / self.games, "bosses": {}}
        for name, (games, reached, cleared) in zip(BOSS_NAMES, self.bosses, strict=True):
            if games:
                out["bosses"][name] = {
                    "games": games, "reached": reached, "cleared": cleared,
                    "clear_rate": cleared / games,
                    "clear_rate_given_reached": cleared / reached if reached else None}
        return out


def _last(games: deque[dict[str, Any]], n: int) -> Iterable[dict[str, Any]]:
    """The newest n of a deque (all of it when it holds no more)."""
    return games if len(games) <= n else list(games)[-n:]


class _Results:
    """One arm's scored full games: whole run and the last `windows[0]` by close time (more
    windows beside it under `rolling_windows`), all levels and per ascension; `unscored` =
    its complete closes without a natural end."""

    def __init__(self, windows: tuple[int, ...] = (ROLLING_GAMES,)) -> None:
        self.all, self.level, self.windows = _Games(), {}, windows
        self.rolling: deque[dict[str, Any]] = deque(maxlen=max(windows))
        self.rolling_level: dict[int, deque[dict[str, Any]]] = {}
        self.unscored = 0

    def add(self, game: dict[str, Any]) -> None:
        level = game["ascension"]
        self.all.add(game)
        self.level.setdefault(level, _Games()).add(game)
        self.rolling.append(game)
        self.rolling_level.setdefault(level, deque(maxlen=max(self.windows))).append(game)

    def seed(self, earlier: _Results) -> None:
        """Take an earlier generation's rolling games (older than any of this one's): the
        windows run on across a resume; the whole-run tables stay this generation's."""
        self.rolling.extend(earlier.rolling)
        for level, games in earlier.rolling_level.items():
            self.rolling_level.setdefault(level, deque(maxlen=max(self.windows))).extend(games)

    def _rolling(self, n: int, levels: list[int]) -> dict[str, Any]:
        return {"all": _Games(_last(self.rolling, n)).result(),
                **{f"A{a}": _Games(_last(self.rolling_level[a], n)).result() for a in levels}}

    def result(self) -> dict[str, Any]:
        levels, rolling = sorted(self.level), sorted(self.rolling_level)
        out = {"rolling": self._rolling(self.windows[0], rolling),
               "run": {"all": self.all.result(),
                       **{f"A{a}": self.level[a].result() for a in levels}}}
        if len(self.windows) > 1:
            out["rolling_windows"] = {str(n): self._rolling(n, rolling)
                                      for n in self.windows[1:]}
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

    def __init__(self, run: Path, window: float = 1200.0, plan_path: Path | None = None,
                 rolling: Iterable[int] | None = None, seed: Iterable[Path] = ()):
        self.run, self.window = Path(run), float(window)
        self.plan_path = plan_path or self.run / "plan.json"
        # rolling windows in complete games: given, else the run's effective-profile knob,
        # else (100,) (a run frozen before #556)
        if rolling is None:
            knob = ((_json(self.run / "effective-profile.json") or {}).get("knobs") or {}).get(
                ROLLING_KNOB) or {}
            rolling = knob.get("value") or (ROLLING_GAMES,)
        self.rolling = tuple(rolling)
        if not self.rolling or any(type(n) is not int or n < 1 for n in self.rolling):
            raise ValueError(f"rolling windows {self.rolling!r}: positive ints expected")
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
        self.kl_no_control = 0  # ticket K: attempts whose KL stop had no control-arm row
        self.checkpoints: list[dict[str, Any]] = []
        self.restored: dict[str, Any] | None = None  # the learner's checkpoint_restored
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
        self.intake_last: dict[str, Any] = {}
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
        self.arms: dict[str, _Results] = {}  # full games per exploration arm (A3)
        self.control = self.arms["control"] = _Results(self.rolling)
        self.games_t: tuple[float, float] | None = None
        # save starts (tickets.mjs): pooled saves per act; complete save-start games per
        # start act and per use of the save; opened games per start; each driver's latest
        # save counters (cumulative, #553 review C)
        self.saves: dict[str, Any] = {"pooled": {}, "problem": 0, "act": {}, "use": {},
                                      "source": {}, "opened": {}, "counters": {}}
        # recipe A: floors played per act by complete games (terminal, admitted, evidence
        # ok), per arm and start ("arm" new runs, "arm+save" save starts)
        self.floors: dict[str, list[int]] = {}
        # a resumed chain generation: the earlier generations' run dirs, oldest first; their
        # drivers' scored games start the rolling windows (bounded by the largest window)
        self.seed_runs = [Path(earlier) for earlier in seed]
        for earlier in self.seed_runs:
            prior = Readout(earlier, self.window, rolling=self.rolling)
            prior._fold_drivers()
            for arm, results in prior.arms.items():
                self.arms.setdefault(arm, _Results(self.rolling)).seed(results)

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
        self._fold_drivers()
        return self

    def _fold_drivers(self) -> None:
        games: list[dict[str, Any]] = []
        for path in sorted(glob.glob(str(self.run / "env-n*" / "p*" / "driver.jsonl"))):
            tail, admitted = self.drivers.setdefault(path, (Tail(path), set()))
            for records in tail.chunks():
                for r in records:
                    self._driver(path, r, admitted, games)
        for game in sorted(games, key=lambda g: g["t"]):  # rolling = last N by close time
            self._game(game)

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
        elif kind == "checkpoint_restored":
            self.restored = {k: v for k, v in e.items() if k not in ("t", "kind")}

    def _progress(self, e: dict[str, Any]) -> None:
        """Every attempt (KL-stopped included: their numbers are pre-step)."""
        self.learning.add(e)
        _window(self.learning_latest, e["t"], _slim(e), self.window)
        self.kl_stopped += bool(e.get("kl_stopped"))
        self.kl_no_control += e.get("kl_stop_basis") == "all_rows_no_control"
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
        intake = (x.get("fast_chain") or {}).get("intake")
        if isinstance(intake, dict):
            self.intake_last = intake

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
        elif kind == "save_pooled":
            _count(self.saves["pooled"], r.get("act"))
            self.saves["problem"] += r.get("problem") is True
        elif kind == "opened":
            _count(self.saves["opened"], r.get("start"))
        elif kind == "save_counters":
            self.saves["counters"][path] = r
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
        if counted and reason == "terminal" and r.get("start") == "from_save":
            self._save_start(r)
        if counted and reason == "terminal" and type(r.get("floor")) is int and "arm" in r:
            self._floors(r)
        arm = r.get("arm")
        if not (counted and reason == "terminal" and type(arm) is str
                and r.get("start") == "new_run"):
            return
        floor, ascension, terminal = r.get("floor"), r.get("ascension"), r.get("terminal")
        if (terminal not in ("victory", "defeat") or type(floor) is not int
                or type(ascension) is not int):
            # no natural end on record: counted apart, never scored
            self.arms.setdefault(arm, _Results(self.rolling)).unscored += 1
            return
        win = terminal == "victory"
        bosses = (*BOSS_FLOORS, 49) if ascension == 10 else BOSS_FLOORS
        games.append({"t": r["t"], "arm": arm, "ascension": ascension, "floor": floor,
                      "win": win,
                      "reward": reward_v21(terminal, floor, ascension),
                      "bosses": [(win or floor >= b, win or floor > b) for b in bosses]})

    def _recipe(self, path: str, r: dict[str, Any], counted: bool) -> None:
        # The arm, ascension and K tables compare games started from scratch; a save start
        # is counted in the start table (and save_starts) only, never mixed in.
        scratch = r.get("start") != "from_save"
        for key, table in self.tables.items():
            if scratch or key == "start":
                _add(table.setdefault(str(r.get("replay_k" if key == "k" else key)),
                                      _bucket()), r, counted)
        if not scratch:
            return
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

    def _floors(self, r: dict[str, Any]) -> None:
        """Floors a complete game played per act (tickets.mjs actOf: act 1 up to 17, act 2 up
        to 33): a new run from floor 1, a save start from the floor after its save."""
        save = r.get("start") == "from_save"
        first = (r.get("save_floor") if save else 0)
        if type(first) is not int:
            return
        row = self.floors.setdefault(f"{r['arm']}{'+save' if save else ''}", [0, 0, 0])
        for floor in range(first + 1, r["floor"] + 1):
            row[0 if floor <= 17 else 1 if floor <= 33 else 2] += 1

    def floors_by_act(self) -> dict[str, Any]:
        """Recipe A: floors per act of complete games, per arm|start and all trained games
        (every arm that learns is not known here: all rows, the reader picks)."""
        if not self.floors:
            return {}
        total = [sum(row[i] for row in self.floors.values()) for i in range(3)]

        def shares(row: list[int]) -> dict[str, Any]:
            n = sum(row)
            return {"floors": list(row), "shares": [x / n for x in row] if n else None}

        return {"by_arm_start": {key: shares(row) for key, row in sorted(self.floors.items())},
                "all": shares(total)}

    def _save_start(self, r: dict[str, Any]) -> None:
        """A complete game started from a save: end floor, floors gained over the save, and
        whether it got past the floor its source game died on (source died only)."""
        floor, start, use = r.get("floor"), r.get("save_floor"), r.get("use")
        if type(floor) is not int or type(start) is not int or type(use) is not int:
            return
        died = r.get("source_terminal") == "defeat" and type(r.get("source_floor")) is int
        won = r.get("terminal") == "victory"
        n = use + 1
        uses = next(label for label, top in SAVE_USE_BUCKETS if n <= top)
        for table, key in ((self.saves["act"], str(r.get("save_act"))), (self.saves["use"], uses),
                           (self.saves["source"], r.get("save_source") or "ordinary")):
            b = table.setdefault(key, {"games": 0, "floor_sum": 0, "gained_sum": 0, "wins": 0,
                                       "source_died": 0, "passed": 0})
            b["games"] += 1
            b["floor_sum"] += floor
            b["gained_sum"] += floor - start
            b["wins"] += won
            b["source_died"] += died
            b["passed"] += died and (won or floor > r["source_floor"])

    def save_starts(self) -> dict[str, Any]:
        """Save starts over the whole run (tickets.mjs): saves pooled per act (problem = the
        last save before its source died in that act); complete save-start games per start
        act and per use of their save (1st, 2nd, 3-4th ...): mean end floor, mean floors
        gained over the save, wins, and of those whose source died, the share that got past
        the source's death floor. Rolling training-game results never include them.
        While the run goes (#553 review C): games opened per start and the effective share
        (from_save / all opened), and the drivers' latest save counters summed (fallback
        new runs, load failures, paused families, claim conflicts, evictions; the node pool
        views `pool_*` are left out, several drivers share one pool)."""
        s = self.saves
        if not s["pooled"] and not s["act"] and not s["counters"]:
            return {}
        opened = s["opened"]
        total = sum(opened.values())
        counters: dict[str, Any] = {}
        # recipe A B1: every pool driver reads the same greedy stats; the latest read is shown
        targets = [line["act_target"] for line in s["counters"].values()
                   if isinstance(line.get("act_target"), dict)]
        target = max(targets, key=lambda t: t.get("read_at") or 0) if targets else None
        for line in s["counters"].values():
            for key, value in line.items():
                if key.startswith("pool_") or key in ("t", "event", "act_target"):
                    continue
                if type(value) is int:
                    counters[key] = counters.get(key, 0) + value
                elif isinstance(value, dict):
                    part = counters.setdefault(key, {})
                    for name, n in value.items():
                        part[name] = part.get(name, 0) + n

        def rows(table: dict[str, Any]) -> dict[str, Any]:
            return {key: {"games": b["games"], "mean_floor": b["floor_sum"] / b["games"],
                          "mean_floors_gained": b["gained_sum"] / b["games"], "wins": b["wins"],
                          "source_died": b["source_died"], "passed_source_death": b["passed"],
                          "pass_rate": b["passed"] / b["source_died"] if b["source_died"] else None}
                    for key, b in sorted(table.items())}
        return {"pooled_by_act": dict(sorted(s["pooled"].items())), "problem_saves": s["problem"],
                "by_start_act": rows(s["act"]), "by_use": rows(s["use"]),
                "by_source": rows(s["source"]), "act_target": target,
                "opened_by_start": dict(sorted(opened.items())),
                "effective_share": opened.get("from_save", 0) / total if total else None,
                "drivers_reporting": len(s["counters"]),
                "driver_counters": dict(sorted(counters.items()))}

    def _game(self, game: dict[str, Any]) -> None:
        self.arms.setdefault(game["arm"], _Results(self.rolling)).add(game)
        if game["arm"] != "control":
            return
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
            "kl_stop_no_control_attempts": self.kl_no_control,
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

    def value_target_intake(self) -> dict[str, Any]:
        """"game" value targets (#564), latest learner status: the intake's held rows and
        node-local spill, rows dropped per cause, chain ends, and the learner's selected /
        capacity-evicted rows by distance to the game's end. Empty under "segment"."""
        intake, learner = self.intake_last, self.learner_last
        if intake.get("value_target") != "game":
            return {}
        return {"intake": {k: v for k, v in sorted(intake.items())
                           if k in ("held_rows", "held_instances") or k.startswith(
                               ("spill_", "game_target_"))},
                "learner": {k: v for k, v in sorted(learner.items())
                            if k.startswith("game_end_")}}

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
        over the whole run and the last `rolling_games` by close time (the knob's first
        window; its other windows under `rolling_windows`, only when set). A Boss on floor b is
        cleared iff the game was won or ended above b (natural layout). Other complete
        closes are `unscored_closes` (counted, never scored). `arms`: the same for every
        exploration arm's full games (control included). In a resumed chain generation the
        rolling windows start from the earlier generations' games (`rolling_seed_runs`);
        `run` stays this generation's."""
        if not any(r.all.games or r.unscored or r.rolling for r in self.arms.values()):
            return {}
        first, last = self.games_t or (0.0, 0.0)
        count = self.control.all.games
        return {"filter": "arm control, start new_run, complete (terminal, admitted, "
                          "evidence ok), result victory/defeat",
                "unscored_closes": self.control.unscored, "rolling_games": self.rolling[0],
                **({"rolling_seed_runs": [p.name for p in self.seed_runs]}
                   if self.seed_runs else {}),
                "complete_games_per_h": (count / (max(last - first, 1) / 3_600_000)
                                         if count > 1 else None),
                **self.control.result(),
                "arms": {arm: {**results.result(), "unscored_closes": results.unscored}
                         for arm, results in sorted(self.arms.items())
                         if results.all.games or results.unscored or results.rolling}}

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
                "handoff_lost": self.handoff_lost,
                "restored_checkpoint_mismatch": self._restored_mismatch()}

    def _restored_mismatch(self) -> dict[str, Any] | None:
        """The learner restored a CP other than the plan's resume CP (RL5-METHODS C)."""
        edge = restored_edge(json.loads(self.plan_path.read_text()), self.restored)
        if edge.get("checkpoint_artifact_id") is None or edge["matches_plan"]:
            return None
        return {"expected": edge["expected"], "restored": edge["checkpoint_artifact_id"]}

    def record(self) -> dict[str, Any]:
        plan = json.loads(self.plan_path.read_text())
        train = self.training()
        span = train.get("window")
        target = plan["target"]
        scores = _hp_scores(self.run)
        return {"schema": SCHEMA, "name": plan["name"], "target": target, "training": train,
                "services": service_edges(self.run, span), "gpu": gpu_cards(self.run, span),
                "env": env_edge(self.run, span), "compile": self.compile.result(),
                "disk": disk_edge(self.run), "recipe": self.recipe(),
                "save_starts": self.save_starts(),
                "floors_by_act": self.floors_by_act(),
                # #603: training and wall seconds from the learner report (None while it runs)
                "clock": (_json(self.run / "learner-report.json") or {}).get("clock"),
                "verdict": verdict(train.get("rows_h"), target),
                "loss_table": loss_table(plan, train),
                "identity": identity_edge(self.run, plan, self.plan_path),
                "throughput": self.throughput(train),
                "learning": self.learning_result(),
                "value_target_intake": self.value_target_intake(),
                "hp_score": hp_score_edge(self.run, scores),
                "hp_score_arms": hp_score_arm_edge(self.run, scores),
                "training_games": self.training_games(),
                "boundary_sections": self.boundary_result(),
                "incidents": self.incidents(),
                "restored_checkpoint": restored_edge(plan, self.restored),
                "checkpoints": list(self.checkpoints)}


def build(run: Path, window: float, plan_path: Path | None = None) -> dict[str, Any]:
    """The run record from one fresh, whole read (the CLI; supervise keeps a Readout)."""
    return Readout(run, window, plan_path).update().record()


def fmt(value: Any, digits: int = 0) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}" if isinstance(value, (int, float)) else str(value)


def _settings_lines(label: str, key: str, x: dict[str, Any]) -> list[str]:
    """RL5-METHODS R lines of one learning block (latest window or run mean)."""
    out = []
    clip, target = x.get("gradient_clip") or {}, x.get("value_target") or {}
    age = x.get("row_age") or {}
    version, sgd = age.get("version_lag") or {}, age.get("pre_update_SGD_lag") or {}
    seconds = age.get("mu_age_seconds") or {}
    parts = []
    if clip:
        parts.append(f"grad clip bound {fmt(clip['bind_rate'] and 100 * clip['bind_rate'], 1)}%"
                     f" of {clip['steps']:,} steps, pre-clip norm mean "
                     f"{fmt(clip['pre_clip_norm_mean'], 3)} p95 <= "
                     f"{fmt(clip['pre_clip_norm_p95_at_most'], 3)}")
    if target:
        parts.append(f"value target entropy {fmt(target.get('target_entropy_mean'), 3)} nats "
                     f"(mean of dataset means), "
                     f"outcome weight {fmt(target.get('outcome_weight_mean'), 3)} (noncombat "
                     f"{fmt(target.get('noncombat_outcome_weight_mean'), 3)})")
    if version.get("n"):
        parts.append(f"row age {fmt(version['mean'], 2)} versions (p95 {version['p95']}), "
                     f"{fmt(sgd.get('mean'), 2)} updates (p95 {sgd.get('p95')}), "
                     f"behaviour-model age since publication (upper bound on row age) "
                     f"{fmt(seconds.get('mean'), 0)} s (p95 <= "
                     f"{fmt(seconds.get('p95_at_most'), 0)} s)")
    adv = x.get("advantage_raw") or {}
    if adv.get("variance") is not None:
        parts.append(f"advantage variance {fmt(adv['variance'], 5)}")
    if parts:
        out.append(f"  settings {label}: " + "; ".join(parts))
    calibration = x.get("value_calibration") or []
    if key == "run_mean" and calibration:
        out.append("  value calibration (terminal training rows, in-sample, row visits incl. "
                   "KL-stopped attempts, all arms; predicted -> realised, visits): " + ", ".join(
            f"{fmt(b['mean_predicted'], 2)} -> {fmt(b['mean_realised'], 2)} ({b['rows']:,})"
            for b in calibration if b["rows"]))
    entropy = x.get("policy_entropy") or {}
    if key == "latest" and entropy:
        out.append("  behaviour-model entropy, T/mix removed (nats / of ln N) by decision: "
                   + ", ".join(
            f"{name} {fmt(d['entropy_mean'], 2)}/{fmt(d['normalized_entropy_mean'], 2)}"
            for name, d in entropy["by_decision"].items()) + "; by act: " + ", ".join(
            f"{name} {fmt(d['entropy_mean'], 2)}/{fmt(d['normalized_entropy_mean'], 2)}"
            for name, d in entropy["by_act"].items()))
    return out


def _cell_lines(label: str, key: str, x: dict[str, Any]) -> list[str]:
    """Value heads per exploration cell: per cell rows, predicted -> realised (difference),
    EV (latest window); the cross-cell table (latest); cell - control and control - own
    (both windows)."""
    out = []

    def head(name: str, h: dict[str, Any], digits: int = 3) -> str:
        if not h["rows"]:
            return f"{name} -"
        return (f"{name} {h['rows']:,} rows {fmt(h['mean_prediction'], digits)} -> "
                f"{fmt(h['mean_target'], digits)} ({h['bias_prediction_minus_target']:+.{digits}f})"
                f" EV {fmt(h['explained_variance'], 3)}")

    for cell, c in (x.get("value_by_cell") or {}).items() if key == "latest" else ():
        out.append(f"  value cell {cell}: " + "; ".join((
            head("vs lambda-return", c["td"]), head("vs outcome", c["terminal"]),
            head("HP", c["hp"], 1))))
    cross = x.get("value_cross_cell")
    if not cross:
        return out
    if key == "latest":
        queried = sorted({q for m in cross["mean_expected_reward"].values() for q in m})
        out.append(f"  V at queried cell ({label}; rows by own cell, columns "
                   f"{' / '.join(queried)}): " + "; ".join(
            f"{own} ({cross['rows'][own]:,}) " + " / ".join(fmt(m.get(q), 3) for q in queried)
            for own, m in cross["mean_expected_reward"].items()))
    gaps, own = cross["cell_minus_control"], cross["control_minus_own"]
    if gaps or own:
        out.append(f"  V gap {label}: " + "; ".join(part for part in (
            ("cell - control, all rows: " + ", ".join(
                f"{cell} [{' '.join(f'{v:.2f}' for v in g['config'])}] {g['gap']:+.4f}"
                for cell, g in gaps.items())) if gaps else "",
            ("control - own: " + ", ".join(
                f"{cell} {value:+.4f}" for cell, value in own.items())) if own else "")
            if part))
    return out


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
                   f"({recipe.get('c_hp_scope')}), KL stop {recipe.get('kl_stop_scope')}, "
                   f"lr warm-up {recipe.get('lr_warmup_steps')} steps")
        start = i.get("start_model") or {}
        if start:
            made = start.get("training_identity") or {}
            sha, submit = start.get("package_sha256"), start.get("site_package_at_submit")
            out.append(f"start model: {str(sha)[:12]}"
                       + "".join(f", {key} {made[key]}" for key in ("schema", "seed",
                                 "checkpoint_artifact_id") if key in made)
                       + ("" if submit == sha else
                          f"; site start_package at submit was {str(submit)[:12]}"))
    restored = r.get("restored_checkpoint") or {}
    if restored.get("expected") or restored.get("checkpoint_artifact_id"):
        if restored.get("checkpoint_artifact_id") is None:
            out.append(f"restored CP: not yet reported (plan resumes {restored.get('expected')})")
        else:
            out.append(f"restored CP: {restored['checkpoint_artifact_id']} (generation "
                       f"{restored.get('training_generation')}, CP {restored.get('sequence')}, "
                       f"mu {restored.get('mu_version')}, {restored.get('updates')} updates)"
                       + ("" if restored.get("matches_plan") else
                          f"; NOT the plan's resume CP {restored.get('expected')}"))
        changes = restored.get("recipe_changes")
        if changes:
            lr = restored.get("optimizer_lr") or {}
            out.append("declared recipe changes: " + ", ".join(
                f"{key} {change['old']!r} -> {change['new']!r}"
                for key, change in changes.items())
                + f"; optimizer lr restored {lr.get('restored')}, base {lr.get('base')}, "
                f"next step {lr.get('applied')}")
    declared = i.get("declared_changes") if i else None
    if declared and not (restored.get("recipe_changes")):
        out.append(f"declared changes for CP {declared.get('checkpoint_artifact_id')}: "
                   + ", ".join(sorted(declared.get("changes") or {}))
                   + (" (not applied: this run restored another CP, recipe locked)"
                      if restored.get("checkpoint_artifact_id") else ""))
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
                   f"{p.get('kl_stopped_attempts')}"
                   + (f" ({p['kl_stop_no_control_attempts']} without a control-arm row: "
                      "all-rows KL)" if p.get("kl_stop_no_control_attempts") else ""))
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
                   f"advantage mean {fmt(adv.get('mean'), 4)} std {fmt(adv.get('std'), 4)}"
                   + (f"; KL control arm {fmt(x['approx_kl_control'], 4)}"
                      if x.get("approx_kl_control") is not None else "")
                   + (f"; lr {x['lr']:.3g}" if x.get("lr") is not None else ""))
        if adv.get("hp_share") is not None:
            out.append(f"  HP term (c_hp A_hp) {label}: {100 * adv['hp_share']:.1f}% of "
                       f"advantage variance, std {fmt(adv['hp_std'], 4)}")
        out += _settings_lines(label, key, x)
        if key == "latest" and x.get("value"):
            out.append("  EV vs lambda-return by bucket: " + ", ".join(
                f"{bucket} {ev('td', heads)}" for bucket, heads in x["value"].items()
                if bucket != "all"))
        for arm, dose in (x.get("exploration") or {}).items() if key == "latest" else ():
            a = dose["all"]
            out.append(
                f"  exploration arm {arm}: {a['rows']:,} rows, chosen top/2nd/3rd+ "
                f"{100 * a['top']:.0f}/{100 * a['second']:.0f}/{100 * a['third_plus']:.0f}%, "
                f"top p>0.99 {100 * a['top_p_over_099']:.0f}%, non-top with advantage>0 "
                f"{fmt(a['nontop_positive'] and 100 * a['nontop_positive'], 0)}%; by decision "
                "(top% / p>0.99%): " + ", ".join(
                    f"{name} {100 * d['top']:.0f}/{100 * d['top_p_over_099']:.0f}"
                    for name, d in dose["by_decision"].items()))
        if key == "run_mean" and x.get("low_probability_noncombat"):
            out.append("  noncombat choices at model p < 5% (arm|decision: rows / games / "
                       "mean advantage / mean real reward - V over rows with a known end): "
                       + ", ".join(
                           f"{name} {t['rows']:,} / {t['games']:,} / "
                           f"{fmt(t['advantage_mean'], 4)} / "
                           f"{fmt(t['outcome_minus_value_mean'], 4)} ({t['outcome_rows']:,})"
                           for name, t in x["low_probability_noncombat"].items()))
        if x.get("combat_settle"):
            c = x["combat_settle"]
            out.append(f"  combat-end settlement {label}: {c['settled_rows']:,} of "
                       f"{c['combat_rows']:,} combat rows settled "
                       f"({100 * c['settled_share']:.1f}%), {c['lost_rows']:,} in lost "
                       f"battles (kept G); outcome advantage std G {fmt(c['std_game'], 4)}, "
                       f"G' {fmt(c['std_settled'], 4)} (same rows)")
        if x.get("potion_discard"):
            out.append(f"  noncombat potion discard {label} (arm: chosen / rows offering it): "
                       + ", ".join(f"{arm} {d['chosen']:,}/{d['rows_offered']:,} "
                                   f"{100 * d['rate']:.1f}%"
                                   for arm, d in x["potion_discard"].items()))
        for arm, dose in (x.get("exploration") or {}).items() if key == "latest" else ():
            out.append(f"  training rows by act, arm {arm}: " + ", ".join(
                f"{name} {d['rows']:,}" for name, d in sorted(dose["by_act"].items())))
        if x.get("noncombat_barrier") is not None:
            out.append(f"  noncombat barrier {label}: term {fmt(x['noncombat_barrier'], 5)} "
                       f"over {fmt(x.get('noncombat_barrier_rows'))} rows per attempt, "
                       "smallest legal p p50 "
                       f"{fmt(x.get('noncombat_min_probability_p50'), 4)} p10 "
                       f"{fmt(x.get('noncombat_min_probability_p10'), 4)}")
        if x.get("policy_observed_rows") is not None:
            out.append(f"  observation cells {label}: {fmt(x['policy_observed_rows'], 1)} policy "
                       f"rows per attempt observed, {fmt(x.get('policy_learning_rows'), 1)} "
                       "learn")
        arms = dict(x.get("policy_by_arm") or {})
        lost = arms.pop(UNATTRIBUTED, None)
        if key == "latest" and set(arms) - {"control"}:
            out.append("  PPO update per arm (rows, clip, approx KL): " + ", ".join(
                f"{arm} {a['rows']:,} / {fmt(a['clip_fraction'], 3)} / "
                f"{fmt(a['approx_kl'], 4)}" for arm, a in arms.items()))
        for arm, f in (x.get("explore_floor_by_arm") or {}).items() if key == "latest" else ():
            out.append(f"  ratio floor arm {arm}: {fmt(f['floored_per_1000'], 1)} floored per "
                       f"1,000 rows, A>0 {fmt(f['floored_positive_share'], 2)}, mean |A| "
                       f"{fmt(f['floored_mean_abs_advantage'], 3)}, weight c|A| "
                       f"{fmt(f['floor_weight_sum'], 2)} vs clipped surrogate "
                       f"{fmt(f['ratio_weight_sum'], 2)}")
        if lost:
            out.append(f"  WARNING ({key}): {lost['rows']:,} PPO rows unattributed (a source "
                       "without an exploration table), left out of the per-arm line")
        out += _cell_lines(label, key, x)
    game_targets = r.get("value_target_intake") or {}
    if game_targets:
        intake = game_targets["intake"]
        dropped = {k[len("game_target_"):-len("_dropped_rows")]: v for k, v in intake.items()
                   if k.endswith("_dropped_rows") and v}
        out.append(f"game value targets: held {fmt(intake.get('held_rows'))} rows, spill "
                   f"{fmt(intake.get('spill_bytes'))} bytes in {fmt(intake.get('spill_files'))} "
                   f"files (peak {fmt(intake.get('spill_peak_bytes'))}), dropped rows {dropped}"
                   + (f", combat-end settlement fell back to G in "
                      f"{intake['game_target_settle_lost_battles']:,} battles (end not reached)"
                      if "game_target_settle_lost_battles" in intake else ""))
    games = r.get("training_games") or {}
    windows = [(games.get("rolling_games"), games.get("rolling") or {}),
               *((int(n), part) for n, part in (games.get("rolling_windows") or {}).items())]
    for n, rolling in windows:
        for level, g in rolling.items():
            if not g.get("games"):
                continue
            bosses = " ".join(f"{name.replace('_boss', '')} {100 * b['clear_rate']:.0f}%"
                              for name, b in g["bosses"].items())
            out.append(f"training games {level} (control, full, last {n}): "
                       f"{g['games']} games, floor {g['mean_floor']:.1f}, win "
                       f"{100 * g['win_rate']:.1f}%, reward {fmt(g['mean_reward_v21'], 3)}; "
                       f"Boss cleared {bosses}")
    arms = games.get("arms") or {}
    for arm, results in arms.items() if set(arms) != {"control"} else ():
        arm_windows = [(games["rolling_games"], results["rolling"]),
                       *((int(n), part) for n, part in
                         (results.get("rolling_windows") or {}).items())]
        for n, rolling in arm_windows:
            for level, g in rolling.items():
                if g.get("games"):
                    out.append(f"training games arm {arm} {level} (full, last "
                               f"{n}): {g['games']} games, floor "
                               f"{g['mean_floor']:.1f}, win {100 * g['win_rate']:.1f}%, act 3 "
                               f"reached {100 * g['act3_reached_rate']:.0f}%; Boss cleared "
                               + " ".join(f"{name.replace('_boss', '')} "
                                          f"{100 * b['clear_rate']:.0f}%"
                                          for name, b in g["bosses"].items()))

    acts = r.get("floors_by_act") or {}
    if acts:
        def part(d: dict[str, Any]) -> str:
            return "/".join(f"{100 * s:.1f}" for s in d["shares"]) if d["shares"] else "-"
        out.append("floors by act 1/2/3 % (complete games, whole run): all " + part(acts["all"])
                   + "; " + ", ".join(f"{key} {part(d)} ({sum(d['floors']):,})"
                                      for key, d in acts["by_arm_start"].items()))

    def hp_line(table: dict[str, Any]) -> str:
        return ", ".join(f"{kind} {c['combats']} / {c['deaths']} / "
                         f"{fmt(c['mean_net_hp_change'], 1)}" for kind, c in table.items())
    hp_score = r.get("hp_score") or {}
    if hp_score:
        out.append("HP score by encounter (combats, deaths, mean net HP change): "
                   + hp_line(hp_score))
    hp_arms = r.get("hp_score_arms") or {}
    for arm, table in hp_arms.items() if set(hp_arms) - {"control"} else ():
        out.append(f"  arm {arm}: " + hp_line(table))
    if games.get("unscored_closes"):
        out.append(f"training games: {games['unscored_closes']} complete control closes "
                   "without a natural end (no victory/defeat or floor) not scored")
    inc = r.get("incidents") or {}
    if inc:
        out.append(f"incidents: closed {inc['env_closed_by_reason']}, non-terminal details "
                   f"{inc['env_close_details']}, admission refused "
                   f"{inc['admission_refused_by_code']}, failures "
                   f"{inc['driver_failures_by_kind']}, hand-offs lost {inc['handoff_lost']}")
        bad = inc.get("restored_checkpoint_mismatch")
        if bad:
            out.append(f"INCIDENT: the learner restored CP {bad['restored']}, the plan's resume "
                       f"CP is {bad['expected']}")
    boundary = ((r.get("boundary_sections") or {}).get("run") or {})
    mean = boundary.get("boundary_sections_seconds_mean") or {}
    if mean:
        top = sorted(mean.items(), key=lambda kv: -kv[1])[:4]
        out.append(f"boundary per dataset ({boundary['datasets']}): "
                   + ", ".join(f"{name} {seconds:.2f} s" for name, seconds in top))
    cps = r.get("checkpoints") or []
    if cps:
        out.append(f"checkpoints: {len(cps)}, last {cps[-1]['checkpoint_artifact_id']}")
    clock = r.get("clock")
    if clock:
        chain = clock.get("chain_training_seconds")
        out.append(f"clock: training {clock['training_seconds']:.0f} s, wall "
                   f"{clock['wall_seconds']:.0f} s this generation"
                   + (f"; chain training {chain:.0f} of "
                      f"{clock['chain_training_budget_seconds']} s budget"
                      if chain is not None else ""))
    return out


def save_start_lines(saves: dict[str, Any], configured: dict[str, Any]) -> list[str]:
    """The save-start block of the readout (render): pools, counters, recipe A's greedy cell."""
    c = saves.get("driver_counters") or {}
    out = ["", f"save starts: pooled per act {saves['pooled_by_act']}, problem saves "
            f"{saves['problem_saves']} (complete games; pass = past the source's death floor)",
            f"  opened {saves.get('opened_by_start')}, effective share "
            f"{fmt(saves.get('effective_share'), 3)} (configured {configured.get('share')}); "
            f"{saves.get('drivers_reporting')} drivers: new run without ticket "
            f"{c.get('new_run_without_ticket', {})}, load failed {c.get('load_failed', 0)}, "
            f"families paused {c.get('families_paused', 0)}, claim conflicts "
            f"{c.get('claim_conflicts', 0)}, evicted {c.get('evicted', {})}"]
    t = saves.get("act_target")
    if t:
        out.append(f"  act target (greedy cell): {t['state']}, target act 1/2/3 "
                   + "/".join(f"{x:,}" if type(x) is int else f"{x:g}" for x in t["target"])
                   + f", greedy games {t['greedy_games']:,}, read failures "
                   f"{c.get('act_target_read_failed', {})}")
    if "greedy_imported" in c or "greedy_exported" in c:
        out.append(f"  greedy saves: exported {c.get('greedy_exported', 0):,} "
                   f"({c.get('greedy_export_bytes', 0) / 1e9:.2f} GB), export failed "
                   f"{c.get('greedy_export_failed', {})}, imported "
                   f"{c.get('greedy_imported', 0):,}, import failed "
                   f"{c.get('greedy_import_failed', {})}, greedy draws "
                   f"{c.get('greedy_draws', 0):,}, fallback greedy->ordinary "
                   f"{c.get('greedy_fallback', 0):,} / ordinary->greedy "
                   f"{c.get('ordinary_fallback', 0):,}")
    for key, label in (("by_start_act", "act"), ("by_use", "use"), ("by_source", "source")):
        for name, row in (saves.get(key) or {}).items():
            out.append(f"  {label + ' ' + name:<12}{row['games']:>8,} games  floor "
                       f"{fmt(row['mean_floor'], 1)}  +{fmt(row['mean_floors_gained'], 1)}"
                       f"  wins {row['wins']}  pass {row['passed_source_death']}/"
                       f"{row['source_died']}")
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
                "ok; rows = model rows of admitted games; arm / ascension / k: games started "
                "from scratch, start: all):",
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
    saves = r.get("save_starts") or {}
    if saves:
        out += save_start_lines(saves, ((r.get("identity") or {}).get("recipe") or {})
                                .get("save_starts") or {})
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
