"""Offline RL measurement. No Exchange, credentials, training or game execution.

Choice rows, game decisions and sample uses are deliberately separate units.
This reader never fills absent first-use identities with a reuse assumption.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

UNIT = "game-decision-v1:source-catalog-cardinality>=2"


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"invalid_{name}")
    return value


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_{name}")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"invalid_{name}")
    return float(value)


def source(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"name": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def learner_event(snapshot: dict[str, Any]) -> dict[str, Any]:
    if "latest_status" in snapshot:
        event = (snapshot.get("latest_status") or {}).get("learner")
    else:
        event = snapshot.get("event", snapshot)
    if not isinstance(event, dict) or not isinstance(event.get("timing"), dict):
        raise ValueError("learner_timing_missing")
    return event


def row_use_bounds(event: dict[str, Any], *, batch_size: int, max_reuse: int) -> dict[str, Any]:
    """Conditional bounds for the current learner's row accounting, NOT decisions.

    Requires constant batch/reuse and every completed update of this run counted.
    Reuse retirement is checked before stale retirement, so a stale row has at
    most max_reuse-1 uses; a still-buffered row may have max_reuse uses.
    """
    _integer(batch_size, "batch_size", 1)
    _integer(max_reuse, "max_reuse", 2)
    counts = event["counts"]
    admitted = _integer(counts.get("rl_samples", 0), "admitted_rows")
    stale = _integer(counts.get("samples_dropped_stale", 0), "stale_rows")
    retired = _integer(counts.get("samples_retired_reuse", 0), "retired_rows")
    buffered = _integer(event["buffer_size"], "buffered_rows")
    uses = _integer(event["updates"], "updates") * batch_size
    if admitted != stale + retired + buffered:
        raise ValueError("row_conservation_failed")
    remaining = uses - max_reuse * retired
    if not 0 <= remaining <= (max_reuse - 1) * stale + max_reuse * buffered:
        raise ValueError("row_use_capacity_failed")
    stale_used_min = max(0, math.ceil((remaining - max_reuse * buffered) / (max_reuse - 1)))
    stale_used_max = min(stale, remaining)
    # Fill the higher-capacity live rows first to minimize distinct used rows.
    live_full_uses = min(remaining, max_reuse * buffered)
    used_min = retired + math.ceil(live_full_uses / max_reuse)
    used_min += math.ceil(max(0, remaining - live_full_uses) / (max_reuse - 1))
    used_max = retired + min(stale + buffered, remaining)
    return {
        "unit": "model-choice-row",
        "sample_uses": uses,
        "admitted_rows": admitted,
        "stale_rows": stale,
        "retired_rows": retired,
        "buffered_rows": buffered,
        "conservation_residual": admitted - stale - retired - buffered,
        "distinct_rows_used_bounds": [used_min, used_max],
        "stale_rows_never_used_bounds": [stale - stale_used_max, stale - stale_used_min],
        "exact_distinct_rows_used": None,
        "exact_effective_new_game_decisions": None,
        "assumptions": [
            "constant batch_size and max_reuse since run start",
            "successful updates and counts restored together",
            "no uncounted/failed optimizer updates",
            "reuse retirement precedes stale retirement",
        ],
    }


def counter_window(
    start: dict[str, Any], end: dict[str, Any], *, batch_size: int, max_reuse: int
) -> dict[str, Any]:
    """Difference two saved events from one timer lifetime, including after resume."""
    a, b = learner_event(start), learner_event(end)
    if not a.get("run") or a["run"] != b.get("run"):
        raise ValueError("different_runs")
    wa = _number(a["timing"]["wall_seconds"], "start_wall")
    wb = _number(b["timing"]["wall_seconds"], "end_wall")
    wall = wb - wa
    if wall <= 0:
        raise ValueError("nonpositive_window")
    # Epoch-minus-monotonic-elapsed is a diagnostic timer-instance check. Event
    # publication adds small skew. It cannot substitute for a process identity.
    instances = [x["timing"].get("instance_id") for x in (a, b)]
    if all(instances) and instances[0] != instances[1]:
        raise ValueError("timer_instance_mismatch")
    origins = [_number(x.get("captured_at", x["at"]), "event_at") - w
               for x, w in ((a, wa), (b, wb))]
    if abs(origins[1] - origins[0]) > 2:
        raise ValueError("timer_lifetime_mismatch")
    updates = _integer(b["updates"], "end_updates") - _integer(a["updates"], "start_updates")
    if updates < 0:
        raise ValueError("updates_regressed")
    phase_a, phase_b = a["timing"]["phases"], b["timing"]["phases"]
    phases = {
        k: _number(phase_b.get(k, 0), "phase") - _number(phase_a.get(k, 0), "phase")
        for k in phase_a.keys() | phase_b.keys()
    }
    if any(v < -1e-6 for v in phases.values()):
        raise ValueError("phase_counter_regressed")
    ca, cb = a["counts"], b["counts"]
    counts = {
        k: _integer(cb.get(k, 0), "counter") - _integer(ca.get(k, 0), "counter")
        for k in ca.keys() | cb.keys()
    }
    if any(v < 0 for v in counts.values()):
        raise ValueError("counter_regressed")
    # wait_anchor nests in train; version_wait can nest in version_capture.
    exclusive = {"train", "poll", "stop_poll", "wait_data_check", "wait_for_data",
                 "version_capture", "resume"}
    primary = sum(v for k, v in phases.items() if k in exclusive)
    wait = phases.get("version_wait", 0)
    if primary > wall + 0.01:
        raise ValueError("phase_accounting_exceeds_wall")
    uses = updates * _integer(batch_size, "batch_size", 1)
    return {
        "schema": "stpd/rl-offline-window-1",
        "game_decision_unit": UNIT,
        "run": a["run"],
        "start_at": a["at"],
        "end_at": b["at"],
        "start_updates": a["updates"],
        "end_updates": b["updates"],
        "end_kind": b.get("kind"),
        "wall_seconds": wall,
        "updates": updates,
        "seconds_per_update": wall / updates if updates else None,
        "sample_uses": uses,
        "sample_uses_per_second": uses / wall,
        "seconds_per_sample_use": wall / uses if uses else None,
        "admitted_rows_per_second": counts.get("rl_samples", 0) / wall,
        "phase_seconds": phases,
        "phase_fractions": {k: v / wall for k, v in phases.items()},
        "nested_or_background_phase_names": sorted(set(phases) - exclusive),
        "phase_seconds_per_update": {
            k: v / updates if updates else None for k, v in phases.items()
        },
        "unclassified_owner_seconds_bounds": [max(0, wall - primary - wait), wall - primary],
        "counts_delta": counts,
        "end_row_use_bounds": row_use_bounds(b, batch_size=batch_size, max_reuse=max_reuse),
        "effective_new_game_decisions_per_hour": None,
        "missing": [
            "first-use IDs",
            "source catalog cardinality of trained rows",
            "per-decision production/admission timestamps",
        ],
        "cautions": [
            "train includes sampling/sorting, not only kernels",
            "poll includes list/enqueue/admission, not all background reading",
            "unclassified includes synchronous STOP/event/TensorBoard I/O; not all network",
            "mean_staleness of non-progress events describes live buffer, not used samples",
        ],
    }


def trajectory_inventory(paths: list[Path]) -> dict[str, Any]:
    """Count original catalog choices; validation is not training admission."""
    from stpd.p1.trajectory import validate_trajectory

    seen: set[str] = set()
    rows, all_sizes = [], Counter[int]()
    for path in paths:
        value = json.loads(path.read_bytes())
        validate_trajectory(value)
        run = value["run_id"]
        if run in seen:
            raise ValueError("duplicate_trajectory_run")
        seen.add(run)
        sizes = Counter(
            len(value["observations"][s["observation_index"]]["observation"]["catalog"])
            for s in value["steps"]
        )
        if sizes.get(0):
            raise ValueError("empty_choice_catalog")
        all_sizes.update(sizes)
        rows.append(
            {
                "run_id": run,
                "source": source(path),
                "choice_rows": sum(sizes.values()),
                "game_decisions": sum(n for k, n in sizes.items() if k >= 2),
                "forced_choice_rows": sizes.get(1, 0),
                "unknown": value["outcome"].get("unknown"),
                "catalog_size_counts": dict(sorted(sizes.items())),
            }
        )
    return {
        "schema": "stpd/rl-offline-inventory-1",
        "game_decision_unit": UNIT,
        "rows": rows,
        "choice_rows": sum(all_sizes.values()),
        "forced_choice_rows": all_sizes.get(1, 0),
        "game_decisions": sum(n for k, n in all_sizes.items() if k >= 2),
        "catalog_buckets": {
            "2-4": sum(n for k, n in all_sizes.items() if 2 <= k <= 4),
            "5-8": sum(n for k, n in all_sizes.items() if 5 <= k <= 8),
            "9-16": sum(n for k, n in all_sizes.items() if 9 <= k <= 16),
            "17+": sum(n for k, n in all_sizes.items() if k >= 17),
        },
        "catalog_size_counts": dict(sorted(all_sizes.items())),
        "meaning": "original generated choices including unknown prefixes; no admission claim",
    }


def fixed_work_comparison(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate premeasured fixed-work runs. Learning conclusions belong to OBS.

    The workload hash must bind inputs, seeds, initialization, selection schedule,
    completion/output comparison policy and timer boundary, excluding candidate.
    """
    if not records:
        raise ValueError("empty_runs")
    workloads: set[str] = set()
    conditions: set[str] = set()
    variants: dict[str, list[float]] = {}
    for row in records:
        for field, target in (("workload_sha256", workloads), ("conditions_sha256", conditions)):
            digest = row[field]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
            ):
                raise ValueError(f"invalid_{field}")
            target.add(digest)
        if row.get("complete") is not True or row.get("outputs_equivalent") is not True:
            raise ValueError("fixed_work_not_completed_or_equivalent")
        seconds = _number(row["wall_seconds"], "wall_seconds")
        if not seconds:
            raise ValueError("nonpositive_wall")
        name = row["variant"]
        if not isinstance(name, str) or not name:
            raise ValueError("invalid_variant")
        variants.setdefault(name, []).append(seconds)
    if len(workloads) != 1 or len(conditions) != 1:
        raise ValueError("different_work_or_conditions")
    result = {}
    for name, durations in variants.items():
        median = statistics.median(durations)
        result[name] = {
            "repeats": len(durations),
            "wall_seconds": durations,
            "median_seconds": median,
            "min_seconds": min(durations),
            "max_seconds": max(durations),
            "relative_range": (max(durations) - min(durations)) / median,
            "stable_repeat_gate": len(durations) >= 3,
        }
    return {
        "schema": "stpd/rl-fixed-work-comparison-1",
        "workload_sha256": next(iter(workloads)),
        "conditions_sha256": next(iter(conditions)),
        "variants": result,
        "meaning": "caller-supplied completion/equivalence evidence must be checked separately",
    }


def decision_ledger(paths: list[Path], *, start_at: float | None = None,
                    end_at: float | None = None) -> dict[str, Any]:
    """Read a complete supplied cohort; never infer undiscovered actor decisions."""
    rows: list[dict[str, Any]] = []
    seen_records: set[str] = set()
    for path in paths:
        for raw in path.read_bytes().splitlines():
            identity = hashlib.sha256(raw).hexdigest()
            if identity in seen_records:
                continue
            seen_records.add(identity)
            row = json.loads(raw)
            if row.get("schema") != "stpd/rl-decision-ledger-1" or row.get("unit") != UNIT:
                raise ValueError("unsupported_decision_ledger")
            rows.append(row)
    if not rows:
        raise ValueError("empty_decision_ledger")
    begin = min(row["at"] for row in rows) if start_at is None else start_at
    end = max(row["at"] for row in rows) if end_at is None else end_at
    if not math.isfinite(begin) or not math.isfinite(end) or end < begin:
        raise ValueError("invalid_decision_window")
    counters: Counter[str] = Counter()
    decisions: dict[str, bool] = {}
    uses: Counter[str] = Counter()
    retired: set[str] = set()
    admitted_ids: set[str] = set()
    latency: list[float] = []
    first_window = 0
    unknown_clocks = 0
    for row in rows:
        kind = row["kind"]
        if kind == "episode":
            disposition = row["disposition"]
            for item in row["rows"]:
                key = item["id"]
                if key in decisions:
                    raise ValueError("duplicate_decision_admission_or_rejection")
                decisions[key] = item["decision"]
                if disposition == "admitted":
                    admitted_ids.add(key)
                    uses[key] = 0
                for stage in ("generated", "uploaded", disposition):
                    counters[stage + "_rows"] += 1
                    counters[stage + "_decisions"] += int(item["decision"])
        elif kind == "trained":
            if len(row["ids"]) != len(row["uses"]):
                raise ValueError("incomplete_successful_use_record")
            first_rows = {item["id"]: item for item in row["first"]}
            for key, total in zip(row["ids"], row["uses"], strict=True):
                if key not in admitted_ids or key in retired or total != uses[key] + 1:
                    raise ValueError("decision_ledger_use_continuity_failure")
                if uses[key] == 0:
                    if key not in first_rows:
                        raise ValueError("first_use_missing")
                    counters["first_used_rows"] += 1
                    counters["first_used_decisions"] += int(decisions[key])
                    first_window += int(decisions[key] and begin <= row["at"] <= end)
                    value = first_rows[key]["generated_to_first_seconds"]
                    if value is not None and decisions[key]:
                        latency.append(value)
                        unknown_clocks += int(first_rows[key]["clock_offset_bound_seconds"] is None)
                uses[key] = total
                counters["sample_uses"] += 1
                counters["decision_uses"] += int(decisions[key])
        elif kind == "retired":
            key = row["id"]
            if key in retired or key not in admitted_ids or uses[key] != row["uses"]:
                raise ValueError("decision_ledger_retirement_continuity_failure")
            retired.add(key)
            label = f"retired_{row['reason']}_uses{row['uses']}"
            counters[label + "_rows"] += 1
            counters[label + "_decisions"] += int(decisions[key])
    ordered = sorted(latency)
    admitted = counters["admitted_decisions"]
    first = counters["first_used_decisions"]
    wall = end - begin
    return {"schema": "stpd/rl-decision-measurement-1", "unit": UNIT,
        "scope": ("supplied read-back episode cohort; "
                  "undiscovered/unsealed/in-flight actors excluded"),
        "sources": [source(path) for path in paths], "counts": dict(counters),
        "window": {"start_at": begin, "end_at": end, "wall_seconds": wall,
            "effective_new_decisions": first_window,
            "effective_new_decisions_per_hour": first_window * 3600 / wall if wall else None},
        "cohort_first_use_rate": first / admitted if admitted else None,
        "generated_to_first_seconds": ({"count": len(ordered), "min": ordered[0],
            "median": statistics.median(ordered), "p95": ordered[int(.95 * (len(ordered)-1))],
            "max": ordered[-1], "unknown_clock_bounds": unknown_clocks} if ordered else None),
        "uses_distribution": dict(Counter(str(uses[key]) for key in uses)),
        "right_censored_admitted_rows": counters["admitted_rows"] - len(retired),
        "cohort_upload_conservation": counters["uploaded_decisions"] == (
            counters["admitted_decisions"] + counters["rejected_decisions"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    window = sub.add_parser("window")
    window.add_argument("--start", type=Path, required=True)
    window.add_argument("--end", type=Path, required=True)
    window.add_argument("--batch-size", type=int, required=True)
    window.add_argument("--max-reuse", type=int, required=True)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("trajectories", nargs="+", type=Path)
    ledger = sub.add_parser("ledger")
    ledger.add_argument("paths", nargs="+", type=Path)
    ledger.add_argument("--start-at", type=float)
    ledger.add_argument("--end-at", type=float)
    compare = sub.add_parser("compare")
    compare.add_argument("runs", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; preserve earlier measurement")
    if args.command == "window":
        result = counter_window(
            json.loads(args.start.read_bytes()),
            json.loads(args.end.read_bytes()),
            batch_size=args.batch_size,
            max_reuse=args.max_reuse,
        )
        result["sources"] = [source(args.start), source(args.end)]
    elif args.command == "inventory":
        result = trajectory_inventory(args.trajectories)
    elif args.command == "ledger":
        result = decision_ledger(args.paths, start_at=args.start_at, end_at=args.end_at)
    else:
        result = fixed_work_comparison(json.loads(args.runs.read_bytes()))
        result["sources"] = [source(args.runs)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
