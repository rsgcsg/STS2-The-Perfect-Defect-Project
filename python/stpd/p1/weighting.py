"""Frozen per-original-game TRAIN loss weights; selection remains Snapshot-owned."""
from __future__ import annotations

import math
from typing import Any

from spireagent.json_boundary import BoundaryError, object_fields

SCHEMA = "stpd/p1-game-loss-weights-1"
CHECKPOINT_POLICY = "early-quarter-epochs-v1"


def checked_weights(value: object, selection: dict[str, Any]) -> dict[str, Any]:
    obj = object_fields(value, {"schema", "rule_id", "weights_by_root"}, "p1_weights")
    weights = obj["weights_by_root"]
    roots = {p["root_run_id"] for p in selection["points"] if p["split"] == "train"}
    if (obj["schema"] != SCHEMA or not isinstance(obj["rule_id"], str)
            or not obj["rule_id"].strip() or len(obj["rule_id"]) > 200
            or not isinstance(weights, dict) or set(weights) != roots):
        raise BoundaryError("p1_weights", "exact_train_root_weights_required")
    if any(type(w) not in {int, float} or not math.isfinite(w) or not 0 < w <= 1000000
           for w in weights.values()):
        raise BoundaryError("p1_weights", "positive_finite_game_weights_required")
    return {"schema": SCHEMA, "rule_id": obj["rule_id"],
            "weights_by_root": {k: float(weights[k]) for k in sorted(weights)}}


def point_weights(policy: dict[str, Any], selection: dict[str, Any]) -> dict[str, float]:
    """Mean-one over the whole TRAIN population, never per microbatch/batch.

    Each decision receives its game's multiplier. Longer games still contribute
    more decisions; this is not equal total mass per game. Exclusion is upstream.
    """
    value = checked_weights(policy, selection)
    rows = [p for p in selection["points"] if p["split"] == "train"]
    weights = value["weights_by_root"]
    mean = math.fsum(weights[p["root_run_id"]] for p in rows) / len(rows)
    return {p["point_id"]: weights[p["root_run_id"]] / mean for p in rows}
