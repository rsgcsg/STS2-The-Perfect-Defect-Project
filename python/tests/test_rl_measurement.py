"""Counterexamples for offline units, resume windows and missing first-use data."""

from __future__ import annotations

import copy
import itertools

import pytest

from stpd.rl.measurement import counter_window, fixed_work_comparison, row_use_bounds


def event(*, wall=10, at=1010, updates=1, admitted=10, stale=2, retired=1, buffer=7):
    return {
        "run": "example",
        "kind": "progress",
        "at": at,
        "updates": updates,
        "buffer_size": buffer,
        "counts": {
            "rl_samples": admitted,
            "samples_dropped_stale": stale,
            "samples_retired_reuse": retired,
        },
        "timing": {"wall_seconds": wall, "phases": {"train": wall / 2, "poll": wall / 5}},
    }


def test_resume_uses_delta_not_lifetime_updates():
    a = event(updates=827, admitted=100000, stale=90000, retired=1000, buffer=9000)
    b = event(
        wall=30, at=1030, updates=829, admitted=100000, stale=90000, retired=1000, buffer=9000
    )
    result = counter_window(
        {"event": a}, {"latest_status": {"learner": b}}, batch_size=64, max_reuse=3
    )
    assert result["sample_uses"] == 128
    assert result["seconds_per_update"] == 10
    assert result["effective_new_game_decisions_per_hour"] is None


@pytest.mark.parametrize(
    "change,error",
    [
        ({"run": "other"}, "different_runs"),
        ({"at": 2000}, "timer_lifetime_mismatch"),
        ({"updates": 0}, "updates_regressed"),
        ({"timing": {"wall_seconds": 1, "phases": {}}}, "nonpositive_window"),
    ],
)
def test_reject_incompatible_windows(change, error):
    a = event()
    b = event(wall=20, at=1020, updates=2)
    b.update(change)
    with pytest.raises(ValueError, match=error):
        counter_window(a, b, batch_size=4, max_reuse=3)


def test_stale_is_not_never_trained_and_bounds_cover_all_assignments():
    # Exhaustively enumerate every allocation of uses for 3 stale and 2 live rows.
    # A retired row has exactly 3; stale rows have at most 2; live rows at most 3.
    by_uses = {}
    for stale in itertools.product(range(3), repeat=3):
        for live in itertools.product(range(4), repeat=2):
            uses = 3 + sum(stale) + sum(live)
            by_uses.setdefault(uses, []).append(
                (1 + sum(x > 0 for x in (*stale, *live)), stale.count(0))
            )
    for uses, possibilities in by_uses.items():
        actual = row_use_bounds(
            event(updates=uses, admitted=6, stale=3, retired=1, buffer=2), batch_size=1, max_reuse=3
        )
        assert actual["distinct_rows_used_bounds"] == [
            min(x[0] for x in possibilities),
            max(x[0] for x in possibilities),
        ]
        assert actual["stale_rows_never_used_bounds"] == [
            min(x[1] for x in possibilities),
            max(x[1] for x in possibilities),
        ]
        assert actual["exact_effective_new_game_decisions"] is None


def test_reject_broken_row_conservation_and_impossible_use_capacity():
    with pytest.raises(ValueError, match="row_conservation_failed"):
        row_use_bounds(event(admitted=999), batch_size=4, max_reuse=3)
    with pytest.raises(ValueError, match="row_use_capacity_failed"):
        row_use_bounds(event(updates=999), batch_size=4, max_reuse=3)


def test_nested_phases_are_not_summed_as_exclusive():
    a = event(updates=1)
    b = event(wall=20, at=1020, updates=2)
    b["timing"]["phases"].update(wait_anchor=1, version_wait=2)
    result = counter_window(a, b, batch_size=4, max_reuse=3)
    assert result["unclassified_owner_seconds_bounds"] == [1, 3]


def test_fixed_work_refuses_changed_work_failed_completion_or_unequal_outputs():
    row = {
        "workload_sha256": "a" * 64,
        "conditions_sha256": "b" * 64,
        "variant": "baseline",
        "complete": True,
        "outputs_equivalent": True,
        "wall_seconds": 10,
    }
    runs = [row, {**row, "wall_seconds": 12}, {**row, "wall_seconds": 11}]
    result = fixed_work_comparison(runs)
    assert result["variants"]["baseline"]["median_seconds"] == 11
    assert result["variants"]["baseline"]["stable_repeat_gate"] is True
    for change in (
        {"workload_sha256": "c" * 64},
        {"conditions_sha256": "c" * 64},
        {"complete": False},
        {"outputs_equivalent": False},
        {"wall_seconds": float("nan")},
    ):
        modified = copy.deepcopy(runs)
        modified[0].update(change)
        with pytest.raises(ValueError):
            fixed_work_comparison(modified)


def test_measurement_cli_real_serializer_roundtrip_and_preserves_output(tmp_path):
    import json
    import os
    import subprocess
    import sys

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(event()))
    b.write_text(json.dumps(event(wall=20, at=1020, updates=2)))
    output = tmp_path / "window.json"
    command = [
        sys.executable,
        "-m",
        "stpd.rl.measurement",
        "--output",
        str(output),
        "window",
        "--start",
        str(a),
        "--end",
        str(b),
        "--batch-size",
        "4",
        "--max-reuse",
        "3",
    ]
    environment = {
        **os.environ,
        "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[1]),
    }
    first = subprocess.run(command, capture_output=True, env=environment)
    assert first.returncode == 0, first.stderr
    assert json.loads(output.read_text())["sample_uses"] == 4
    second = subprocess.run(command, capture_output=True, env=environment)
    assert second.returncode != 0
    assert b"output already exists" in second.stderr


def test_inventory_uses_original_catalog_and_retains_unknown_prefix(tmp_path):
    import json

    from test_p1_trajectory import episode

    from stpd.rl.measurement import trajectory_inventory

    two = episode("e" * 64, "two")
    forced = episode("e" * 64, "forced")
    forced["observations"][0]["observation"]["catalog"] = [{"choice_id": "a"}]
    forced["steps"][0]["behavior"].update(probabilities=[1.0], model_probabilities=[1.0])
    forced["outcome"].update(status="unknown", unknown=True, reason="diagnostic_prefix")
    paths = [tmp_path / "two.json", tmp_path / "forced.json"]
    for path, value in zip(paths, [two, forced], strict=True):
        path.write_text(json.dumps(value))
    report = trajectory_inventory(paths)
    assert report["choice_rows"] == 2
    assert report["game_decisions"] == 1
    assert report["forced_choice_rows"] == 1
    assert report["rows"][1]["unknown"] is True
    with pytest.raises(ValueError, match="duplicate_trajectory_run"):
        trajectory_inventory([paths[0], paths[0]])
