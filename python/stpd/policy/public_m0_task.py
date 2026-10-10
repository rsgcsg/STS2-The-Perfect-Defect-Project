"""Declared Public M0 program task over qualified current public Snapshot facts.

This fixed completion rule is Agent code, not a learned score or game legality.
The caller validates the entire public input and original ordered catalog first.
"""

from __future__ import annotations

from typing import Any

from spireagent.json_boundary import BoundaryError

SNAPSHOT_SCHEMA = "sts2.player-environment/snapshot-1"


def ready_summary_task_spec() -> dict[str, str]:
    return {
        "schema": "stpd/public-m0-task-spec-v1",
        "id": "ready-standard-summary-v1",
        "input_schema": SNAPSHOT_SCHEMA,
        "abstention_scores": "program-zero-v1",
    }


def validate_task_spec(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value != ready_summary_task_spec():
        raise BoundaryError("public_m0_policy", "unsupported_task_spec")
    return ready_summary_task_spec()


def ready_summary(snapshot: dict[str, Any]) -> bool:
    """Recognize only the exact ordinary GameOverSurfaceReader ready summary.

    Int32 is the published GameOverLiveContext field type. No score threshold,
    win requirement or research reward is introduced here.
    """
    interaction = snapshot["interaction"]
    if interaction.get("kind") != "game_over":
        return False
    content = interaction["content"]
    surface, context = content.get("surface"), content.get("context")
    if not isinstance(surface, dict) or not isinstance(context, dict):
        raise BoundaryError("public_m0_policy", "malformed_game_over_task_input")
    if interaction.get("stage") != "summary" and surface.get("stage") != "summary":
        return False
    if (
        snapshot.get("schema") != SNAPSHOT_SCHEMA
        or interaction.get("content_schema") != "sts2.player-environment/surface/game_over-1"
        or interaction.get("stage") != "summary"
        or surface.get("stage") != "summary"
        or surface.get("kind") != "game_over"
        or context.get("kind") != "game_over"
        or surface.get("can_return") is not True
        or surface.get("can_advance_summary") is not False
        or surface.get("return_destination") not in ("main_menu", "timeline")
        or context.get("game_mode") != "standard"
        or context.get("result") not in ("win", "loss")
        or any(type(context.get(field)) is not int
               or not -(2**31) <= context[field] < 2**31
               for field in ("score", "floor_reached", "ascension"))
    ):
        raise BoundaryError("public_m0_policy", "malformed_game_over_task_input")
    return True
