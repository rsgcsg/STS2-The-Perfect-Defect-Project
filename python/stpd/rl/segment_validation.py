"""Research input validation shared by hot encoders and historical Store readers.

These checks bind supplied verified transitions, public inputs and behavior.
Native legality and original verification remain with their existing owners.
"""

from __future__ import annotations

import math
from typing import Any

from spireagent.json_boundary import BoundaryError
from stpd.p1.data import digest
from stpd.p1.data import json_bytes as p1_json_bytes
from stpd.p1.trajectory import validate_behavior
from stpd.rl.segment_inputs import TRAJECTORY_SCHEMA as COMPILED_SCHEMA
from stpd.rl.segment_inputs import segment_body, validate_body

SCHEMA = "stpd/p1-trajectory-segment-1"
MAX_ORIGINAL_BYTES = 256 * 1024**2


def validate_segment(value: dict[str, Any], *, _hot: bool = False) -> None:
    steps = value.get("steps")
    mu = value.get("mu") or {}
    if (
        (not _hot and value.get("schema") not in {SCHEMA, COMPILED_SCHEMA})
        or not isinstance(steps, list)
        or not 1 <= len(steps) <= 64
        or value.get("rows") != len(steps)
        or value.get("training_split") != "TRAIN"
        or not isinstance(value.get("run_id"), str)
        or not isinstance(value.get("segment_id"), str)
        or len(mu.get("snapshot_sha256", "")) != 64
        or type(mu.get("updates")) is not int or mu["updates"] < 0
        or not isinstance(value.get("original_commit"), dict)
    ):
        raise BoundaryError("trajectory_segment", "invalid_segment")
    if _hot or value["schema"] == COMPILED_SCHEMA:
        validate_body(segment_body(value), value, value["compiler_identity"])
    original = value["original_commit"]
    if (
        original.get("run_id") != value["run_id"]
        or original.get("segment_id") != value["segment_id"]
        or not _hot and (not isinstance(original.get("archive_sha256"), str)
        or len(original.get("archive_sha256", "")) != 64
        or type(original.get("archive_bytes")) is not int
        or not 0 < original.get("archive_bytes", 0) <= MAX_ORIGINAL_BYTES)
    ):
        raise BoundaryError("trajectory_segment", "invalid_original_block_binding")
    previous = None
    for step in steps:
        current, behavior = step["input"], step["behavior"]
        catalog = current["catalog"]
        directive = step["directive"]["directive"]
        selected = [
            i
            for i, item in enumerate(catalog)
            if item["choice_id"] == directive["selection"]["action_id"]
        ]
        if (
            step.get("schema") != "sts2.policy-runtime/verified-global-transition-1"
            or step["run_id"] != value["run_id"]
            or step["result"]["status"] != "completed"
            or behavior["mu"] != mu
            or behavior["lease"] != value["segment_id"]
            or behavior["acquisition_id"] != current["acquisition_id"]
            or behavior["input_sha256"] != digest(p1_json_bytes(current))
            or behavior["catalog_sha256"] != digest(p1_json_bytes(catalog))
            or catalog != current["observation"]["catalog"]
            or directive["scores"]["values"] != behavior["scores"]
            or step["sampling"].get("model_logits") != behavior["scores"]
            or step["sampling"].get("mode") != "temperature_uniform"
            or step["sampling"].get("temperature") != 1
            or step["sampling"].get("uniform_mix") != 0
            or len(selected) != 1
            or (previous is not None and step["step_index"] != previous + 1)
        ):
            raise BoundaryError("trajectory_segment", "transition_behavior_binding_changed")
        validate_behavior(step["sampling"], len(catalog), selected[0])
        previous = step["step_index"]
    if any(a["successor"] != b["input"] for a, b in zip(steps, steps[1:], strict=False)):
        raise BoundaryError("trajectory_segment", "segment_successor_changed")
    if (
        value.get("terminal") is not None
        and steps[-1]["successor"]["observation"]["status"] != "terminal"
    ):
        raise BoundaryError("trajectory_segment", "terminal_without_verified_successor")
    for battle in value.get("hp_battles", []):
        boundary = next(
            (step for step in steps if step["step_index"] == battle["boundary_step_index"]), None
        )
        if boundary is None:
            raise BoundaryError("trajectory_segment", "battle_hp_boundary_changed")

        successor = boundary["successor"]
        observation = successor["observation"]
        hp = ((observation.get("player") or {}).get("content") or {}).get("hp")
        if (
            successor["acquisition_id"] != battle["boundary_acquisition_id"]
            or (_hot and battle.get("boundary_input_sha256") != digest(p1_json_bytes(successor)))
            or not str((observation.get("scene") or {}).get("native_kind", "")).startswith("reward")
            or (observation.get("combat") or {}).get("availability") == "available"
            or not isinstance(hp, (int, float))
            or isinstance(hp, bool)
            or hp < 0
            or (battle.get("post_hp") != hp if _hot else battle["hp"] != hp / 100)
            or not battle["start_step"] < battle["end_step"] <= boundary["step_index"] + 1
            or battle["start_step"] >= battle["end_step"]
            or battle["battle_id"] != f"{value['run_id']}:{battle['start_step']}"
        ):
            raise BoundaryError("trajectory_segment", "battle_hp_boundary_changed")


def validate_hot_segment(value: dict[str, Any]) -> None:
    """F0 HotSegment is an internal transport object, not an artifact schema."""
    validate_segment(value, _hot=True)
    mu = value["mu"]
    if (value.get("run") != mu.get("run")
            or not value.get("training_generation")
            or value["training_generation"] != mu.get("training_generation")):
        raise BoundaryError("trajectory_segment", "hot_segment_generation_changed")
    original = value["original_commit"]
    if (original.get("representation") != "live-prefix"
            or len(original.get("journal_prefix_sha256", "")) != 64
            or len(original.get("behavior_trace_prefix_sha256", "")) != 64):
        raise BoundaryError("trajectory_segment", "hot_original_prefix_binding_changed")
    for step in value["steps"]:
        _probabilities(step["behavior"].get("outcome_probabilities"))
        if type(step["behavior"].get("floor")) is not int or step["behavior"]["floor"] < 0:
            raise BoundaryError("trajectory_segment", "invalid_behavior_floor")
    bootstrap = value.get("bootstrap")
    last = value["steps"][-1]
    if value["terminal"] is None:
        if (
            last["successor"]["observation"]["status"] == "terminal"
            or not isinstance(bootstrap, dict)
            or bootstrap.get("mu_snapshot_sha256") != mu["snapshot_sha256"]
            or bootstrap.get("successor_input_sha256") != digest(p1_json_bytes(last["successor"]))
            or bootstrap.get("floor") != _boundary_floor(value)
        ):
            raise BoundaryError("trajectory_segment", "hot_bootstrap_binding_changed")
        _probabilities(bootstrap.get("outcome_probabilities"))
    elif bootstrap is not None:
        raise BoundaryError("trajectory_segment", "terminal_has_no_bootstrap")


def _boundary_floor(segment: dict[str, Any]) -> int:
    successor = segment["steps"][-1]["successor"]["observation"]
    floor = ((successor.get("run") or {}).get("content") or {}).get("floor")
    return max([int(step["behavior"]["floor"]) for step in segment["steps"]]
               + [floor if type(floor) is int else 0])


def _probabilities(value: Any) -> None:
    if (not isinstance(value, list) or len(value) != 51
            or any(type(p) not in (float, int) or not math.isfinite(p) or p < 0 for p in value)
            or not math.isclose(sum(value), 1, rel_tol=0, abs_tol=1e-5)):
        raise BoundaryError("trajectory_segment", "invalid_behavior_outcome_distribution")
