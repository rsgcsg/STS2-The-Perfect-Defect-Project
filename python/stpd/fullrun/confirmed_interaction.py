"""Public, one-action history projection shared by replay and the live adapter."""

from __future__ import annotations

from typing import Any

from spireagent.json_boundary import BoundaryError

from .text_menu_inputs import INPUT_PROFILE, V2_INPUT_PROFILE

HISTORY_INPUT_PROFILE = "text-menu-v1-confirmed-interaction"
V2_HISTORY_INPUT_PROFILE = "text-menu-v2-confirmed-interaction"
HISTORY_PROFILES = frozenset({HISTORY_INPUT_PROFILE, V2_HISTORY_INPUT_PROFILE})


def page_profile(profile: str) -> str:
    if profile == HISTORY_INPUT_PROFILE:
        return INPUT_PROFILE
    if profile == V2_HISTORY_INPUT_PROFILE:
        return V2_INPUT_PROFILE
    return profile


def confirmed_action_text(snapshot: dict[str, Any], action_id: str, *,
                          effect_domain: str, basis: str, profile: str) -> str:
    """Use the frozen complete menu; never infer an action from its position/name."""
    from .memory_token_inputs import project_memory_profile_snapshot

    if profile not in HISTORY_PROFILES or basis not in {
        "confirmed_connector_result", "last_known_human_input_witness",
    }:
        raise BoundaryError("confirmed_interaction", "unsupported_history_profile")
    if effect_domain not in {"text_menu", "native_input"}:
        raise BoundaryError("confirmed_interaction", "effect_domain_invalid")
    if not isinstance(action_id, str) or not action_id:
        raise BoundaryError("confirmed_interaction", "action_id_invalid")
    public = project_memory_profile_snapshot(snapshot, profile)
    if public.action_ids.count(action_id) != 1:
        raise BoundaryError("confirmed_interaction", "action_binding_mismatch")
    index = public.action_ids.index(action_id)
    action = snapshot["menu_actions"]["actions"][index]
    if action["action_id"] != action_id or action["effect_domain"] != effect_domain:
        raise BoundaryError("confirmed_interaction", "effect_domain_mismatch")
    return ("previous_confirmed_interaction basis=" + basis + " domain="
            + effect_domain + " action=" + public.action_texts[index])
