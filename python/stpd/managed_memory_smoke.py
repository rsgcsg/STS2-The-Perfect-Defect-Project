"""Bounded engineering smoke for an exported M2/Reset model on Managed text-menu-v1.

The Host owns game state, current action binding and delivery. This experiment
owns only scoring and a finite stop rule; it is not Policy Runtime evidence.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from spireagent.json_boundary import json_bytes

from .fullrun.memory_token_inputs import project_memory_snapshot

CONTEXT_SCHEMA = "sts2.player-environment/text-menu-observation-context-1"
RESULT_SCHEMA = "sts2.player-environment/text-menu-action-result-1"
SNAPSHOT_SCHEMA = "sts2.player-environment/text-menu-snapshot-1"


class SmokeBoundaryError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SmokeLimits:
    max_policy_calls: int = 4
    max_submissions: int = 1
    max_observations: int = 8
    max_seconds: float = 30.0

    def __post_init__(self) -> None:
        if (any(type(value) is not int or not 1 <= value <= 32 for value in (
                self.max_policy_calls, self.max_submissions, self.max_observations))
                or type(self.max_seconds) not in (int, float)
                or not math.isfinite(self.max_seconds)
                or not 0 < self.max_seconds <= 120):
            raise SmokeBoundaryError("invalid_smoke_limits")


def _terminal(snapshot: dict[str, Any]) -> bool:
    if (snapshot.get("status") != "observed"
            or snapshot.get("interaction", {}).get("kind") != "game_over"):
        return False
    interaction = snapshot["interaction"]
    catalog = snapshot.get("menu_actions", {})
    surface = interaction.get("content", {}).get("surface", {})
    if (snapshot.get("schema") != SNAPSHOT_SCHEMA or snapshot.get("input_profile") != "text-menu-v1"
            or snapshot.get("completeness", {}).get("status") != "complete"
            or interaction.get("stage") != "complete"
            or interaction.get("capabilities") != []
            or catalog.get("status") != "complete" or catalog.get("actions") != []
            or catalog.get("total_count") != 0 or catalog.get("materialized_count") != 0
            or surface.get("kind") != "game_over" or type(surface.get("victory")) is not bool):
        raise SmokeBoundaryError("terminal_incomplete")
    return True


def run_managed_memory_smoke(
    environment: Any, scorer: Any, *, seeds: tuple[str, ...],
    model_id: str, reset_each_step: bool, limits: SmokeLimits | None = None,
    clock: Any = time.monotonic,
) -> dict[str, Any]:
    """Run at most two explicit episodes, closing the dedicated child on every exit."""
    if (not 1 <= len(seeds) <= 2 or any(not isinstance(seed, str)
            or re.fullmatch(r"[A-Z0-9]{1,64}", seed) is None for seed in seeds)
            or not isinstance(model_id, str) or len(model_id) != 64
            or type(reset_each_step) is not bool):
        raise SmokeBoundaryError("invalid_smoke_request")
    limits = limits if limits is not None else SmokeLimits()
    report: dict[str, Any] = {
        "schema": "stpd/managed-memory-engineering-smoke-v1",
        "model_id": model_id, "recipe": "reset-k1" if reset_each_step else "m2-k1",
        "qualification": "engineering_only", "policy_runtime_http": False,
        "status": "stopped", "stop_reason": "not_started",
        "episodes_started": 0, "observations": 0, "policy_calls": 0,
        "submissions": 0, "native_delivered": 0, "terminal_observed": 0,
    }
    deadline = clock() + limits.max_seconds
    previous_continuity: str | None = None

    def within_budget() -> None:
        if clock() >= deadline:
            raise SmokeBoundaryError("wall_budget_exhausted")

    try:
        if (not callable(getattr(environment, "observe_text_menu", None))
                or not callable(getattr(environment, "submit_text_menu", None))):
            raise SmokeBoundaryError("text_menu_consumer_unavailable")
        for seed in seeds:
            within_budget()
            environment.reset(seed)
            within_budget()
            identity = environment.episode_identity().get("episode_provenance", {})
            if (identity.get("verdict") != "provenance_pass"
                    or identity.get("requested_seed") != seed
                    or identity.get("actual_seed") != seed):
                raise SmokeBoundaryError("episode_provenance_unverified")
            report["episodes_started"] += 1
            episode_continuity: str | None = None
            while True:
                within_budget()
                if report["observations"] >= limits.max_observations:
                    raise SmokeBoundaryError("observation_budget_exhausted")
                context = environment.observe_text_menu()
                report["observations"] += 1
                within_budget()
                if not isinstance(context, dict) or context.get("schema") != CONTEXT_SCHEMA:
                    raise SmokeBoundaryError("text_context_invalid")
                snapshot = context.get("snapshot")
                continuity = context.get("game_continuity_id")
                if (not isinstance(snapshot, dict) or not isinstance(continuity, str)
                        or not continuity):
                    raise SmokeBoundaryError("text_context_invalid")
                if episode_continuity is None:
                    if continuity == previous_continuity:
                        raise SmokeBoundaryError("continuity_reused_after_reset")
                    episode_continuity = continuity
                elif continuity != episode_continuity:
                    raise SmokeBoundaryError("continuity_changed_within_episode")
                if _terminal(snapshot):
                    report["terminal_observed"] += 1
                    report["stop_reason"] = "terminal_observed"
                    break
                if snapshot.get("status") != "interactive":
                    raise SmokeBoundaryError("text_page_not_interactive")
                if report["policy_calls"] >= limits.max_policy_calls:
                    raise SmokeBoundaryError("policy_call_budget_exhausted")
                if report["submissions"] >= limits.max_submissions:
                    raise SmokeBoundaryError("submission_budget_exhausted")
                public = project_memory_snapshot(snapshot)
                report["policy_calls"] += 1
                scored = scorer.observe_and_score(
                    continuity_token=continuity, snapshot_bytes=json_bytes(snapshot),
                    expected_candidate_digest=public.candidate_digest,
                    expected_candidate_count=len(public.action_ids),
                )
                within_budget()
                if (scored.action_ids != public.action_ids
                        or scored.candidate_digest != public.candidate_digest
                        or len(scored.scores) != len(public.action_ids)
                        or any(type(score) not in (float, int) or not math.isfinite(score)
                               for score in scored.scores)):
                    raise SmokeBoundaryError("score_binding_invalid")
                selected = max(range(len(scored.scores)), key=scored.scores.__getitem__)
                action = snapshot["menu_actions"]["actions"][selected]
                action_id = public.action_ids[selected]
                if action.get("action_id") != action_id:
                    raise SmokeBoundaryError("selected_action_unbound")
                request_id = uuid4().hex
                report["submissions"] += 1
                result = environment.submit_text_menu(action_id, snapshot["snapshot_id"],
                                                      continuity, request_id)
                within_budget()
                if (not isinstance(result, dict) or result.get("schema") != RESULT_SCHEMA
                        or result.get("input_profile") != "text-menu-v1"
                        or result.get("request_id") != request_id):
                    raise SmokeBoundaryError("text_result_invalid")
                if result.get("status") == "unknown":
                    report["stop_reason"] = "native_delivery_unknown"
                    break
                if result.get("status") != "applied":
                    report["stop_reason"] = "text_action_not_applied"
                    break
                native = action["effect_domain"] == "native_input"
                if (result.get("action") != action
                        or result.get("effect_domain") != action["effect_domain"]
                        or result.get("native_delivery") != ("delivered" if native else None)
                        or not isinstance(result.get("successor"), dict)):
                    raise SmokeBoundaryError("text_result_binding_invalid")
                successor = result["successor"]
                if (successor.get("schema") != SNAPSHOT_SCHEMA
                        or successor.get("input_profile") != "text-menu-v1"
                        or successor.get("session") != snapshot.get("session")):
                    raise SmokeBoundaryError("text_successor_identity_invalid")
                if native:
                    report["native_delivered"] += 1
                if _terminal(successor):
                    report["terminal_observed"] += 1
                    report["stop_reason"] = "terminal_observed"
                    break
                if report["submissions"] >= limits.max_submissions:
                    report["stop_reason"] = "submission_budget_exhausted"
                    break
            previous_continuity = episode_continuity
            if report["stop_reason"] != "terminal_observed":
                break
        if report["stop_reason"] == "not_started":
            report["stop_reason"] = "episodes_complete"
    except SmokeBoundaryError as error:
        report["stop_reason"] = error.code
    except Exception:
        report["stop_reason"] = "experiment_failed"
    finally:
        try:
            environment.close()
        except Exception:
            report["stop_reason"] = "child_close_failed"
    if report["stop_reason"] in {"terminal_observed", "submission_budget_exhausted"} \
            and report["native_delivered"] > 0:
        report["status"] = "engineering_smoke_complete"
    return report
