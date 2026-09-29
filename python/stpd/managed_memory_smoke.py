"""Bounded engineering smoke for an exported M2/Reset model on Managed text menus.

The Host owns game state, current action binding and delivery. This experiment
owns only scoring and a finite stop rule; it is not Policy Runtime evidence.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from spireagent.json_boundary import json_bytes

from .fullrun.memory_token_inputs import project_memory_profile_snapshot
from .fullrun.text_menu_inputs import INPUT_PROFILE, V2_INPUT_PROFILE
from .game_seed import require_canonical_game_seed

CONTEXT_SCHEMA = "sts2.player-environment/text-menu-observation-context-1"
RESULT_SCHEMA = "sts2.player-environment/text-menu-action-result-1"
SNAPSHOT_SCHEMA = "sts2.player-environment/text-menu-snapshot-1"
SUPPORTED_CHARACTERS = frozenset({"Ironclad", "Silent", "Defect", "Regent", "Necrobinder"})


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


def _profile_schemas(input_profile: str) -> tuple[str, str, str]:
    if input_profile == INPUT_PROFILE:
        return CONTEXT_SCHEMA, RESULT_SCHEMA, SNAPSHOT_SCHEMA
    if input_profile == V2_INPUT_PROFILE:
        return ("sts2.player-environment/text-menu-observation-context-2",
                "sts2.player-environment/text-menu-action-result-2",
                "sts2.player-environment/text-menu-snapshot-2")
    raise SmokeBoundaryError("unknown_text_menu_profile")


def _terminal(snapshot: dict[str, Any], input_profile: str = INPUT_PROFILE) -> bool:
    if (snapshot.get("status") != "observed"
            or not isinstance(snapshot.get("interaction"), dict)
            or snapshot["interaction"].get("kind") != "game_over"):
        return False
    interaction = snapshot["interaction"]
    catalog = snapshot.get("menu_actions")
    menu = snapshot.get("menu")
    content = interaction.get("content")
    if not isinstance(catalog, dict) or not isinstance(content, dict):
        raise SmokeBoundaryError("terminal_incomplete")
    surface = content.get("surface", {})
    context = content.get("context", {})
    if not isinstance(surface, dict) or not isinstance(context, dict):
        raise SmokeBoundaryError("terminal_incomplete")
    session = snapshot.get("session", {})
    completeness = snapshot.get("completeness", {})
    policy = snapshot.get("information_policy", {})
    _, _, snapshot_schema = _profile_schemas(input_profile)
    if (set(snapshot) != {"protocol_version", "schema", "input_profile", "snapshot_id",
                            "sequence", "observed_at", "status", "persistent", "interaction",
                            "referents", "completeness", "session", "information_policy",
                            "menu", "menu_actions"}
            or snapshot.get("protocol_version") != "1.0.0"
            or snapshot.get("schema") != snapshot_schema
            or snapshot.get("input_profile") != input_profile
            or not isinstance(snapshot.get("snapshot_id"), str) or not snapshot["snapshot_id"]
            or type(snapshot.get("sequence")) is not int or snapshot["sequence"] < 1
            or not isinstance(snapshot.get("observed_at"), str) or not snapshot["observed_at"]
            or not isinstance(session, dict)
            or not isinstance(session.get("runtime_instance_id"), str)
            or not session["runtime_instance_id"]
            or not isinstance(session.get("environment_fingerprint"), str)
            or not session["environment_fingerprint"]
            or not isinstance(snapshot.get("referents"), list)
            or not isinstance(policy, dict)
            or policy.get("includes_hidden_information") is not False
            or not isinstance(menu, dict) or menu.get("cursor") != "root"
            or (input_profile == V2_INPUT_PROFILE and menu.get("selection") != [])
            or type(menu.get("revision")) is not int or menu["revision"] < 0
            or not isinstance(menu.get("native_snapshot_id"), str)
            or not menu["native_snapshot_id"]
            or not isinstance(completeness, dict) or completeness.get("status") != "complete"
            or completeness.get("missing") != []
            or not isinstance(interaction.get("interaction_id"), str)
            or not interaction["interaction_id"]
            or interaction.get("kind") != "game_over"
            or interaction.get("stage") != "complete"
            or interaction.get("content_schema") !=
                "sts2.player-environment/surface/game_over-1"
            or interaction.get("capabilities") != []
            or catalog.get("status") != "complete" or catalog.get("actions") != []
            or catalog.get("total_count") != 0 or catalog.get("materialized_count") != 0
            or not isinstance(catalog.get("ordering_semantics"), str)
            or not catalog["ordering_semantics"]
            or surface.get("kind") != "game_over" or surface.get("stage") != "complete"
            or type(surface.get("victory")) is not bool
            or context.get("kind") != "terminal"):
        raise SmokeBoundaryError("terminal_incomplete")
    return True


def validate_smoke_request(seeds: tuple[str, ...], model_id: str,
                           reset_each_step: bool, character: str = "Defect",
                           ascension: int = 0, *,
                           input_profile: str = INPUT_PROFILE) -> None:
    """Reject invalid experiment inputs before a CLI creates its child."""
    try:
        valid_seeds = 1 <= len(seeds) <= 2 and all(
            isinstance(seed, str) and require_canonical_game_seed(seed) == seed
            for seed in seeds
        )
    except ValueError:
        valid_seeds = False
    if (input_profile not in {INPUT_PROFILE, V2_INPUT_PROFILE}
            or not valid_seeds or not isinstance(model_id, str) or len(model_id) != 64
            or type(reset_each_step) is not bool
            or not isinstance(character, str) or character not in SUPPORTED_CHARACTERS
            or type(ascension) is not int or ascension != 0):
        raise SmokeBoundaryError("invalid_smoke_request")


def _observed_episode_configuration(snapshot: dict[str, Any]) -> tuple[str, int]:
    persistent = snapshot.get("persistent")
    if not isinstance(persistent, dict) or not isinstance(persistent.get("content"), dict):
        raise SmokeBoundaryError("episode_configuration_unverified")
    content = persistent["content"]
    run, player = content.get("run"), content.get("player")
    if (not isinstance(run, dict) or not isinstance(player, dict)
            or not isinstance(player.get("character_definition_id"), str)
            or type(run.get("ascension")) is not int):
        raise SmokeBoundaryError("episode_configuration_unverified")
    return player["character_definition_id"], run["ascension"]


def run_managed_memory_smoke(
    environment: Any, scorer: Any, *, seeds: tuple[str, ...],
    model_id: str, reset_each_step: bool, limits: SmokeLimits | None = None,
    character: str = "Defect", ascension: int = 0,
    input_profile: str = INPUT_PROFILE,
    clock: Any = time.monotonic,
) -> dict[str, Any]:
    """Run at most two explicit episodes, closing the dedicated child on every exit."""
    limits = limits if limits is not None else SmokeLimits()
    report: dict[str, Any] = {
        "schema": "stpd/managed-memory-engineering-smoke-v1",
        "model_id": model_id, "recipe": "reset-k1" if reset_each_step else "m2-k1",
        "qualification": "engineering_only", "policy_runtime_http": False,
        "status": "stopped", "stop_reason": "not_started",
        "requested_character": character, "requested_ascension": ascension,
        "observed_character": None, "observed_ascension": None,
        "episodes_started": 0, "observations": 0, "policy_calls": 0,
        "submissions": 0, "native_delivered": 0, "terminal_observed": 0,
    }
    if input_profile == V2_INPUT_PROFILE:
        report["input_profile"] = V2_INPUT_PROFILE
    deadline = clock() + limits.max_seconds
    previous_continuity: str | None = None

    def within_budget() -> None:
        if clock() >= deadline:
            raise SmokeBoundaryError("wall_budget_exhausted")

    try:
        validate_smoke_request(seeds, model_id, reset_each_step, character, ascension,
                               input_profile=input_profile)
        context_schema, result_schema, snapshot_schema = _profile_schemas(input_profile)
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
                context = (environment.observe_text_menu(input_profile=input_profile)
                           if input_profile == V2_INPUT_PROFILE else
                           environment.observe_text_menu())
                report["observations"] += 1
                within_budget()
                if not isinstance(context, dict) or context.get("schema") != context_schema:
                    raise SmokeBoundaryError("text_context_invalid")
                snapshot = context.get("snapshot")
                continuity = context.get("game_continuity_id")
                if (not isinstance(snapshot, dict) or not isinstance(continuity, str)
                        or not continuity):
                    raise SmokeBoundaryError("text_context_invalid")
                if episode_continuity is None:
                    observed_character, observed_ascension = _observed_episode_configuration(
                        snapshot)
                    report["observed_character"] = observed_character
                    report["observed_ascension"] = observed_ascension
                    if (observed_character != character.upper()
                            or observed_ascension != ascension):
                        raise SmokeBoundaryError("episode_configuration_mismatch")
                    if continuity == previous_continuity:
                        raise SmokeBoundaryError("continuity_reused_after_reset")
                    episode_continuity = continuity
                elif continuity != episode_continuity:
                    raise SmokeBoundaryError("continuity_changed_within_episode")
                if _terminal(snapshot, input_profile):
                    report["terminal_observed"] += 1
                    report["stop_reason"] = "terminal_observed"
                    break
                if snapshot.get("status") != "interactive":
                    raise SmokeBoundaryError("text_page_not_interactive")
                if report["policy_calls"] >= limits.max_policy_calls:
                    raise SmokeBoundaryError("policy_call_budget_exhausted")
                if report["submissions"] >= limits.max_submissions:
                    raise SmokeBoundaryError("submission_budget_exhausted")
                public = project_memory_profile_snapshot(snapshot, input_profile)
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
                result = (environment.submit_text_menu(
                    action_id, snapshot["snapshot_id"], continuity, request_id,
                    input_profile=input_profile) if input_profile == V2_INPUT_PROFILE else
                    environment.submit_text_menu(action_id, snapshot["snapshot_id"],
                                                 continuity, request_id))
                if (not isinstance(result, dict) or result.get("schema") != result_schema
                        or result.get("input_profile") != input_profile
                        or result.get("request_id") != request_id):
                    raise SmokeBoundaryError("text_result_invalid")
                if result.get("status") == "unknown":
                    if input_profile == V2_INPUT_PROFILE and (
                        result.get("action") is not None
                        or result.get("effect_domain") != "native_input"
                        or result.get("native_delivery") != "unknown"
                        or result.get("successor") is not None or result.get("retry") != "never"
                    ):
                        raise SmokeBoundaryError("text_result_invalid")
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
                if (successor.get("schema") != snapshot_schema
                        or successor.get("input_profile") != input_profile
                        or successor.get("session") != snapshot.get("session")):
                    raise SmokeBoundaryError("text_successor_identity_invalid")
                if native:
                    report["native_delivered"] += 1
                terminal = _terminal(successor, input_profile)
                if terminal:
                    report["terminal_observed"] += 1
                # The native call may cross the deadline. Its returned receipt is still
                # a known outcome and must be accounted before stopping further calls.
                within_budget()
                if terminal:
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
