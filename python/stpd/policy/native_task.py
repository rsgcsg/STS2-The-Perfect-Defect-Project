"""Explicit natural-run task and fixed timing over qualified public native input.

This program control belongs to the delivered Agent, not to its learned model.
It neither selects gameplay actions nor changes the complete current catalog.
The predicate is stateless: its entire basis is the acknowledged observation
already covered by opaque scorer export/restore and Runtime generation binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def public_map_travel_timing_spec() -> dict[str, Any]:
    """Fixed Agent choice, never native readiness or legality authority."""
    return {
        "id": "stpd-native-public-map-travel-timing-v1",
        "version": "1.0.0",
        "predicate": "exact_native_map_information_page_map_navigation_schema_traveling_true",
        "positive": "Await_known_cursor_any_event_after_exact_ACK",
        "otherwise": "AgentSpec_declared_choice",
        "recheck_timeout_ms": 250,
        "catalog": "complete_unchanged",
        "learned": False,
    }


def public_map_travel_pending(observation: dict[str, Any]) -> bool:
    """Defer only this exact public Map state, after owning input qualification."""
    if (observation.get("schema") != "sts2.player-environment/native-logical-observation-1"
            or observation.get("input_profile") != "native-logical-v1"):
        return False
    interaction = observation.get("interaction")
    if (not isinstance(interaction, dict)
            or interaction.get("kind") != "native_map"
            or interaction.get("stage") != "native_information_page"
            or interaction.get("content_schema")
            != "sts2.player-environment/surface/map_navigation-1"):
        return False
    content = interaction.get("content")
    surface = content.get("surface") if isinstance(content, dict) else None
    return (isinstance(surface, dict) and surface.get("kind") == "map_navigation"
            and surface.get("traveling") is True)


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


def map_timed_ready_summary_task_spec() -> dict[str, Any]:
    """New navigation composition; the original 1.0 task remains the default."""
    return {
        **ready_summary_task_spec(),
        "version": "1.1.0",
        "navigation": "fixed_public_map_travel_timing_then_AgentSpec_choice_until_summary_ready",
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
