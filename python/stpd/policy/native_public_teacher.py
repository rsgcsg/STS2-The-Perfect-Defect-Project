"""Transparent native-C demonstration teacher; no backend, legality or learned policy.

This research strategy is selected statically by the application collector. It
ranks existing complete public actions and discloses its finite scripted state.
It must never be inserted as a fallback in a learned evaluation journey.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.native_structured_inputs import native_catalog_digest
from stpd.policy.native_task import observe_ready_summary, public_map_travel_pending

TEACHER_ID = "native-public-demonstration-v1"
TEACHER_VERSION = "1.0.9"
OWNED_STALE_TEACHER_VERSION = "1.0.10"
MAX_BROWSE_CHOICES = 12
RETURNS = {
    "run_deck": "return_native_information",
    "combat_draw_pile": "return_native_information",
    "combat_discard_pile": "return_native_information",
    "combat_exhaust_pile": "return_native_information",
    "inspect_card": "return_card_inspect",
    "relic_inspect": "return_relic_inspect",
    **{
        kind: "return_native_tips"
        for kind in (
            "card_tips",
            "relic_tips",
            "power_tips",
            "intent_tips",
            "orb_tips",
            "topbar_tips",
            "native_tip",
        )
    },
}


@dataclass(frozen=True)
class TeacherChoice:
    action_id: str | None
    reason: str
    state: dict[str, Any]
    directive: Literal["act", "await", "close"] = "act"


@dataclass
class NativePublicTeacher:
    browse: bool = True
    phase: str = "map"
    browse_choices: int = 0
    decisions: int = 0
    saw_card_entry: bool = False
    combat_pile_viewed: bool = False
    focus_target_id: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    version: str = TEACHER_VERSION

    def state(self) -> dict[str, Any]:
        return {
            "teacher_id": TEACHER_ID,
            "version": self.version,
            "browse": self.browse,
            "phase": self.phase,
            "browse_choices": self.browse_choices,
            "decisions": self.decisions,
            "saw_card_entry": self.saw_card_entry,
            "combat_pile_viewed": self.combat_pile_viewed,
            "focus_target_id": self.focus_target_id,
            "choice_verbs": dict(self.counts),
            "learned": False,
        }

    def _stop(self, reason: str) -> TeacherChoice:
        return TeacherChoice(None, reason, self.state(), "close")

    def _await(self, reason: str) -> TeacherChoice:
        # A pending public owner/focus is a program timing decision, never an
        # invented action or proof that the earlier delivery finished closing.
        return TeacherChoice(None, reason, self.state(), "await")

    def _choose(
        self, action: dict[str, Any] | None, *, browse: bool = False, phase: str | None = None
    ) -> TeacherChoice:
        if action is None:
            return self._stop("required_native_action_unavailable")
        if browse:
            if self.browse_choices >= MAX_BROWSE_CHOICES:
                return self._stop("scripted_inspection_budget_exhausted")
            self.browse_choices += 1
        if phase is not None:
            self.phase = phase
        self.decisions += 1
        verb = action["verb"]
        self.counts[verb] = self.counts.get(verb, 0) + 1
        return TeacherChoice(action["action_id"], "original_public_member", self.state())

    def decide(self, observation: dict[str, Any], catalog: list[dict[str, Any]]) -> TeacherChoice:
        """Preserve C unchanged. All state/action facts must belong to this input."""
        descriptor = observation.get("catalog")
        if (
            observation.get("schema") != "sts2.player-environment/native-logical-observation-1"
            or observation.get("input_profile") != "native-logical-v1"
            or observation.get("completeness")
            != {
                "status": "complete",
                "included": ["persistent", "interaction", "referents", "catalog"],
                "missing": [],
                "full_reference_complete": True,
            }
            or not isinstance(descriptor, dict)
            or descriptor.get("snapshot_id") != observation.get("snapshot_id")
            or descriptor.get("status") != "complete"
            or type(descriptor.get("total_count")) is not int
            or descriptor["total_count"] != len(catalog)
            or descriptor.get("digest") != native_catalog_digest(catalog)
            or observation.get("information_policy", {}).get("includes_hidden_information")
            is not False
        ):
            raise BoundaryError("native_public_teacher", "complete_current_catalog_required")
        summary = observe_ready_summary(observation)
        if summary.agent_task_complete:
            return self._stop("natural_ready_summary_" + str(summary.outcome))
        if observation.get("status") == "settling":
            return self._await("await_public_ready_owner")
        if observation.get("status") not in {"interactive", "observed"} or not catalog:
            return self._stop("current_not_actionable")
        page = observation.get("interaction")
        if not isinstance(page, dict) or not isinstance(page.get("content"), dict):
            return self._stop("public_owner_missing")
        surface = page["content"].get("surface")
        if not isinstance(surface, dict):
            return self._stop("public_surface_missing")
        refs = observation.get("referents")
        if not isinstance(refs, list):
            return self._stop("public_referents_missing")
        referents = {
            ref["referent_id"]: ref
            for ref in refs
            if isinstance(ref, dict) and isinstance(ref.get("referent_id"), str)
        }
        kind, stage = page.get("kind"), page.get("stage")

        # Public information/Skip leaves can make the whole input interactive
        # before reward card holders finish mounting. The entered page owns
        # its readiness; absence of Select alone never establishes this wait.
        if (kind == "card_reward_selection" and stage == "settling"
                and page.get("content_schema")
                == "sts2.player-environment/surface/card_reward_selection-1"
                and surface.get("kind") == "card_reward_selection"):
            return self._await("await_public_card_reward_ready")

        # Shared declared Agent timing; independent information leaves stay in C.
        if public_map_travel_pending(observation):
            return self._await("await_public_map_travel")

        # Choosing to await another offer makes no assertion of native progress.
        # A complete information map can have no currently deliverable route.
        if (kind == "native_map" and stage == "native_information_page"
                and page.get("content_schema")
                == "sts2.player-environment/surface/map_navigation-1"
                and surface.get("kind") == "map_navigation"
                and surface.get("travel_enabled") is True
                and surface.get("traveling") is False
                and surface.get("drawing_mode") == "none"
                and surface.get("next_options") == []):
            return self._await("await_public_map_route")

        def find(verb: str, subject: str | None = None) -> dict[str, Any] | None:
            return next(
                (
                    action
                    for action in catalog
                    if action["verb"] == verb
                    and (subject is None or action["subject_referent_id"] == subject)
                ),
                None,
            )

        def visible(identity: object, role: str | None = None, *, kind: str = "entity") -> bool:
            ref = referents.get(identity) if isinstance(identity, str) else None
            return bool(
                ref
                and ref.get("kind") == kind
                and (role is None or ref.get("role") == role)
                and ref.get("state", {}).get("visible") is True
                and ref["state"].get("enabled") is not False
            )

        if self.browse and self.phase != "progress":
            pending = {
                "deck": ({"native_map", "map_navigation"}, "run_deck"),
                "inspect": ({"run_deck"}, "inspect_card"),
                "deck_return": ({"inspect_card"}, "run_deck"),
                "map_return": ({"run_deck"}, "map_navigation"),
            }.get(self.phase)
            if pending is not None and kind in pending[0]:
                return self._await("await_public_owner_" + pending[1])
            if self.phase == "map" and kind in {"native_map", "map_navigation"}:
                return self._choose(find("open_run_deck"), browse=True, phase="deck")
            if self.phase == "deck" and kind == "run_deck":
                action = next(
                    (
                        a
                        for a in catalog
                        if a["verb"] == "inspect_deck_card"
                        and visible(a["subject_referent_id"], "card")
                    ),
                    None,
                )
                return self._choose(action, browse=True, phase="inspect")
            if self.phase == "inspect" and kind == "inspect_card":
                preview = find("toggle_card_upgrade_preview")
                if preview is not None:
                    return self._choose(preview, browse=True, phase="inspect_return")
                return self._choose(find("return_card_inspect"), browse=True, phase="deck_return")
            if self.phase == "inspect_return" and kind == "inspect_card":
                return self._choose(find("return_card_inspect"), browse=True, phase="deck_return")
            if self.phase == "deck_return" and kind == "run_deck":
                return self._choose(
                    find("return_native_information"), browse=True, phase="map_return"
                )
            if self.phase == "map_return" and kind in {"native_map", "map_navigation"}:
                self.phase = "progress"
            else:
                return self._stop("scripted_owner_arrival_not_observed")
        elif not self.browse:
            self.phase = "progress"

        if kind in RETURNS:
            return self._choose(find(RETURNS[kind]), browse=True)
        if kind in {"native_map", "map_navigation"}:
            if surface.get("kind") != "map_navigation" or not isinstance(
                surface.get("next_options"), list
            ):
                return self._stop("map_public_options_unavailable")
            options = {
                option.get("entity_id"): option
                for option in surface["next_options"]
                if isinstance(option, dict) and visible(option.get("entity_id"))
            }
            ranked = [
                a
                for a in catalog
                if a["verb"] == "activate" and a["subject_referent_id"] in options
            ]
            action = next(
                (
                    a
                    for a in ranked
                    if options[a["subject_referent_id"]].get("point_type") == "monster"
                ),
                ranked[0] if ranked else None,
            )
            return self._choose(action)
        if kind == "combat_turn":
            if self.browse and not self.combat_pile_viewed:
                self.combat_pile_viewed = True
                return self._choose(find("open_combat_draw_pile"), browse=True)
            begin = next(
                (
                    a
                    for a in catalog
                    if a["verb"] == "begin_card_play"
                    and any(
                        visible(a["subject_referent_id"], role)
                        for role in ("card", "playable_card", "hand")
                    )
                ),
                None,
            )
            if begin is not None:
                self.saw_card_entry = True
                self.focus_target_id = None
                return self._choose(begin)
            if not self.saw_card_entry:
                return self._stop("native_card_entry_unavailable_or_unproven")
            return self._choose(find("end_turn"))
        if kind == "combat_card_operation":
            if stage == "card_targeting":
                if (page.get("content_schema")
                        != "sts2.player-environment/surface/combat_card_operation_text_menu-1"
                        or surface.get("kind") != kind or surface.get("stage") != stage):
                    return self._stop("native_card_targeting_facts_unavailable")
                focused = surface.get("focused_target_referent_id")
                confirm = find("confirm_target", focused) if isinstance(focused, str) else None
                if (isinstance(focused, str) and visible(focused) and confirm is not None
                        and self.focus_target_id in (None, focused)):
                    # Current public focus and its original Confirm member are
                    # sufficient without prior Focus history. An actual pending
                    # different target still waits for its observed arrival.
                    self.focus_target_id = None
                    return self._choose(confirm)
                if self.focus_target_id is not None:
                    return self._await("await_public_target_focus")
                target = next(
                    (
                        a
                        for a in catalog
                        if a["verb"] == "focus_target" and visible(a["subject_referent_id"])
                    ),
                    None,
                )
                if target is not None:
                    self.focus_target_id = target["subject_referent_id"]
                return self._choose(target)
            if stage == "card_confirm":
                return self._choose(find("confirm_card"))
            return self._stop("native_card_operation_unrecognized")
        if kind == "reward_claim":
            if (
                page.get("content_schema")
                == "sts2.player-environment/surface/linked_rewards_text_menu-1"
            ):
                entries = surface.get("entries")
                if not isinstance(entries, list):
                    return self._stop("linked_reward_public_entries_unavailable")
                choices: Any
                for entry in entries:
                    if not isinstance(entry, dict):
                        return self._stop("linked_reward_public_entries_unavailable")
                    if entry.get("kind") == "ordinary_reward":
                        choices, verb = [entry], "claim_reward"
                    elif entry.get("kind") == "linked_reward_set":
                        choices, verb = entry.get("choices"), "claim_linked_reward"
                        if not isinstance(choices, list):
                            return self._stop("linked_reward_public_choices_unavailable")
                    else:
                        return self._stop("linked_reward_public_kind_unrecognized")
                    for choice in choices:
                        if (
                            isinstance(choice, dict)
                            and choice.get("enabled") is True
                            and visible(choice.get("referent_id"), "reward")
                        ):
                            return self._choose(find(verb, choice["referent_id"]))
                if surface.get("proceed_enabled") is not True:
                    return self._stop("linked_reward_proceed_not_public")
                skip = surface.get("proceed_is_skip")
                if type(skip) is not bool:
                    return self._stop("linked_reward_proceed_effect_unavailable")
                verb = "skip_rewards" if skip else "proceed_rewards"
                actions = [
                    a
                    for a in catalog
                    if a["verb"] == verb
                    and visible(a["subject_referent_id"], "screen", kind="control")
                ]
                return self._choose(actions[0] if len(actions) == 1 else None)
            enabled = surface.get("rewards")
            if page.get(
                "content_schema"
            ) == "sts2.player-environment/surface/reward_claim-1" and isinstance(enabled, list):
                rewards = [r for r in enabled if isinstance(r, dict) and r.get("enabled") is True]
                ids = {r.get("entity_id") for r in rewards if visible(r.get("entity_id"), "reward")}
                action = next(
                    (
                        a
                        for a in catalog
                        if a["verb"] == "activate"
                        and a["subject_referent_id"] in ids
                        and a["arguments"] == []
                    ),
                    None,
                )
                if action is not None:
                    return self._choose(action)
                if rewards:
                    return self._stop("reward_public_binding_unavailable")
                if surface.get("can_proceed") is not True:
                    return self._stop("reward_proceed_not_public")
                matches = [
                    a
                    for a in catalog
                    if a["subject_referent_id"] is None
                    and a["arguments"] == []
                    and (
                        a["verb"] == "activate"
                        or a["verb"] == "skip"
                        and surface.get("proceed_skips_remaining_rewards") is True
                    )
                ]
                return self._choose(matches[0] if len(matches) == 1 else None)
            return self._stop("reward_page_semantics_unrecognized")
        if kind == "card_reward_selection":
            selectable = surface.get("selectable_card_entity_ids")
            cards = surface.get("cards")
            if not isinstance(selectable, list) or not isinstance(cards, list):
                return self._stop("card_reward_public_selection_unavailable")
            ids = {
                c.get("entity_id")
                for c in cards
                if isinstance(c, dict)
                and c.get("entity_id") in selectable
                and visible(c.get("entity_id"))
            }
            action = next(
                (a for a in catalog if a["verb"] == "select" and a["subject_referent_id"] in ids),
                None,
            )
            if action is None:
                return self._stop("card_reward_alternative_effect_not_public")
            return self._choose(action)
        # Additional page families need their own public content/control join;
        # catalog membership alone does not supply the missing demonstration semantics.
        return self._stop("unsupported_public_owner")
