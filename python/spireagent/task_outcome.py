"""Shared read-only Task/operation/terminal result contract.

One schema and type owner for application and research readers. No UI,
operational control, research admission or gameplay authority lives here.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

SCHEMA = "spireagent/agent-task-outcome-v1"


class TaskCompletion(TypedDict):
    status: Literal["completed", "unproved", "unknown"]
    task_spec: dict[str, Any] | None
    reason_codes: list[str]
    witness: dict[str, Any] | None


class OperationEnd(TypedDict):
    status: Literal["task_ended", "censored", "failed", "unknown"]
    reason_codes: list[str]
    event_sequence: int | None
    controller: str | None
    tainted: bool | None
    recorded_reason: str | None
    budget: dict[str, Any] | None


class TerminalObservation(TypedDict):
    status: Literal["observed", "ambiguous", "not_observed", "unknown"]
    result: Literal["win", "loss"] | None
    ready_summary: bool | None
    witnesses: list[dict[str, Any]]


class AgentTaskOutcome(TypedDict):
    schema: str
    task_completion: TaskCompletion
    operation_end: OperationEnd
    terminal_observation: TerminalObservation
    agent_composition: dict[str, Any]
    evidence: dict[str, Any]
    strategy_quality: Literal["not_assessed"]


def _empty(verification: str) -> AgentTaskOutcome:
    return {
        "schema": SCHEMA,
        "task_completion": {
            "status": "unknown",
            "task_spec": None,
            "reason_codes": ["evidence_not_verified"],
            "witness": None,
        },
        "operation_end": {
            "status": "unknown",
            "reason_codes": ["evidence_not_verified"],
            "event_sequence": None,
            "controller": None,
            "tainted": None,
            "recorded_reason": None,
            "budget": None,
        },
        "terminal_observation": {
            "status": "unknown",
            "result": None,
            "ready_summary": None,
            "witnesses": [],
        },
        "agent_composition": {},
        "evidence": {"verification": verification, "content_id": None, "events": None},
        "strategy_quality": "not_assessed",
    }
