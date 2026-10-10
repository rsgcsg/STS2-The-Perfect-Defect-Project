"""Public M2 task composition, consuming a qualified terminal observation once.

The ordinary M2 projection, codec, neural step and memory update remain required.
Only the resulting action selection is replaced by declared program abstention.
"""

from __future__ import annotations

from typing import Any

from spireagent.json_boundary import BoundaryError

from .public_m0_task import SNAPSHOT_SCHEMA, ready_summary


def ready_summary_task_spec() -> dict[str, str]:
    return {
        "schema": "stpd/public-m2-task-spec-v1",
        "id": "ready-standard-summary-v1",
        "input_schema": SNAPSHOT_SCHEMA,
        "terminal_observation": "normal-step-once-v1",
        "abstention_scores": "program-zero-v1",
    }


def validate_task_spec(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value != ready_summary_task_spec():
        raise BoundaryError("public_m2_policy", "unsupported_task_spec")
    return ready_summary_task_spec()


def task_complete(snapshot: dict[str, Any]) -> bool:
    """Explicitly reuse the shared public surface predicate, with M2 ownership."""
    try:
        return ready_summary(snapshot)
    except BoundaryError as error:
        raise BoundaryError("public_m2_policy", error.code) from error
