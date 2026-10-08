"""One explicit natural-run task over already qualified public native input.

This program control belongs to the delivered Agent, not to its learned model.
It neither selects gameplay actions nor changes the complete current catalog.
The predicate is stateless: its entire basis is the acknowledged observation
already covered by opaque scorer export/restore and Runtime generation binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def ready_summary_task_spec() -> dict[str, Any]:
    """Fresh fixed descriptor for new packages; legacy AgentSpecs stay unchanged."""
    return {
        "schema": "stpd/native-task-spec-v1",
        "id": "native-standard-run-ready-summary",
        "version": "1.0.0",
        "goal": "one_native_run_to_ready_terminal_summary",
        "completion": "qualified_public_game_over_summary_ready",
        "navigation": "complete_catalog_greedy_until_summary_ready",
        "return_to_menu_required": False,
        "timing_learned": False,
        "administrative_actions": [],
        "censoring": [
            "budget", "disconnect", "unknown_delivery", "source_gap",
            "unsupported_surface", "external_stop",
        ],
    }


@dataclass(frozen=True)
class NativeTaskObservation:
    outcome: str | None
    agent_task_complete: bool

    @property
    def game_outcome_known(self) -> bool:
        return self.outcome is not None


def observe_ready_summary(observation: dict[str, Any]) -> NativeTaskObservation:
    """Read terminal facts from one qualified full-reference observation.

    Caller must use the exact accepted consume_ack basis. No status flag, HP,
    receipt, clock, process exit or future Current substitutes for these fields.
    Ready-summary completion intentionally does not require returning to a menu.
    """
    incomplete = NativeTaskObservation(None, False)
    if (observation.get("schema") != "sts2.player-environment/native-logical-observation-1"
            or observation.get("input_profile") != "native-logical-v1"
            or observation.get("status") not in ("interactive", "settling", "observed", "terminal")
            or observation.get("completeness") != {
                "status": "complete",
                "included": ["persistent", "interaction", "referents", "catalog"],
                "missing": [],
                "full_reference_complete": True,
            }):
        return incomplete
    interaction = observation.get("interaction")
    if not isinstance(interaction, dict) or interaction.get("kind") != "game_over":
        return incomplete
    content = interaction.get("content")
    if not isinstance(content, dict):
        return incomplete
    context, surface = content.get("context"), content.get("surface")
    if (not isinstance(context, dict) or not isinstance(surface, dict)
            or context.get("kind") != "game_over"
            or surface.get("kind") != "game_over"
            or context.get("game_mode") != "standard"
            or context.get("result") not in ("win", "loss")):
        return incomplete
    ready = (
        observation.get("status") in ("interactive", "observed", "terminal")
        and interaction.get("stage") == surface.get("stage") == "summary"
        and surface.get("can_return") is True
        and surface.get("can_advance_summary") is False
        and surface.get("return_destination") in ("main_menu", "timeline")
        and all(type(context.get(field)) is int
                for field in ("score", "floor_reached", "ascension"))
    )
    return NativeTaskObservation(context["result"], ready)
