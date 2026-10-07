"""Opt-in current text-menu input. Connector remains the menu and delivery authority.

Only the current cursor and its complete advertised choices are projected. Opaque
bindings stay in ``action_ids``; the text contains public semantics, never a
synthetic history, a chosen action, or an inferred native effect.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from spireagent.json_boundary import BoundaryError

from ..canonical import semantic_hash
from .platform_bundle3 import _SemanticProjection
from .representation import reject_leakage

INPUT_PROFILE = "text-menu-v1"
SNAPSHOT_SCHEMA = "sts2.player-environment/text-menu-snapshot-1"
VERSION = "stpd-text-menu-current-page-v1"
IDENTITY = {"version": VERSION, "profile": "text_menu_current_page",
            "source_schema": SNAPSHOT_SCHEMA, "input_profile": INPUT_PROFILE,
            "status": "provisional"}
V2_INPUT_PROFILE = "text-menu-v2"
V2_SNAPSHOT_SCHEMA = "sts2.player-environment/text-menu-snapshot-2"
V2_VERSION = "stpd-text-menu-current-page-v2"
V2_IDENTITY = {"version": V2_VERSION, "profile": "text_menu_current_page",
               "source_schema": V2_SNAPSHOT_SCHEMA, "input_profile": V2_INPUT_PROFILE,
               "status": "provisional"}
CURSORS = frozenset({"root", "information", "relic_inspect", "relic_tips",
                     "card_tips", "power_tips", "intent_tips", "orb_tips",
                     "topbar_tips"})
NAV_VERBS = frozenset({"open_information", "open_relic_inspect", "open_relic_tips",
                       "open_card_tips", "open_power_tips", "open_intent_tips",
                       "open_orb_tips", "open_topbar_tips", "back"})
V2_CURSORS = CURSORS | {"card_targets", "card_confirmation"}
SELECTION_VERBS = frozenset({"select_card", "select_target", "cancel_selection"})
CARD_ROLES = frozenset({"card", "playable_card"})
TARGET_ROLES = frozenset({"target", "enemy", "ally", "creature", "player", "companion"})


@dataclass(frozen=True)
class TextMenuInput:
    state_text: str
    action_texts: tuple[str, ...]
    action_ids: tuple[str, ...]
    action_kinds: tuple[str, ...]
    candidate_digest: str

    def scores_in_catalog_order(self, values: Sequence[float]) -> tuple[float, ...]:
        if len(values) != len(self.action_ids) or any(
            type(value) not in {float, int} or not math.isfinite(value) for value in values
        ):
            raise BoundaryError("text_menu_input", "score_binding_mismatch")
        return tuple(float(value) for value in values)


def _object(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BoundaryError("text_menu_input", code)
    return value


def _text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise BoundaryError("text_menu_input", code)
    return value


def _render(value: Any) -> str:
    # The current page's native order and repeated descriptions remain inline.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _selectable(ref: dict[str, Any], role: str) -> bool:
    return (ref["kind"] == "entity"
            and ref["role"] in (CARD_ROLES if role == "card" else TARGET_ROLES)
            and ref["state"]["visible"] is True
            and ref["state"].get("enabled") is not False)


def _require_combat_page(interaction: dict[str, Any], content: dict[str, Any]) -> None:
    surface = content.get("surface")
    if (interaction.get("kind") != "combat_turn" or interaction.get("stage") != "ready"
            or interaction.get("content_schema") != "sts2.player-environment/surface/combat_turn-1"
            or not isinstance(surface, dict) or surface.get("kind") != "combat_turn"):
        raise BoundaryError("text_menu_input", "selection_combat_page_required")


def _validate_v2_action(action: dict[str, Any], cursor: str,
                        visible: dict[str, dict[str, Any]], navigation: set[str],
                        selection_edges: set[tuple[str, str | None]]) -> None:
    if set(action) != {"action_id", "kind", "verb", "label", "subject_referent_id",
                       "arguments", "effect_domain"}:
        raise BoundaryError("text_menu_input", "malformed_v2_action")
    kind, verb = action["kind"], action["verb"]
    subject, arguments = action["subject_referent_id"], action["arguments"]
    if kind == "system_navigation":
        allowed = ({"open_information"} if cursor == "root" else
                   NAV_VERBS - {"open_information"} if cursor == "information" else {"back"})
        if subject is not None or arguments or verb not in allowed or verb in navigation:
            raise BoundaryError("text_menu_input", "navigation_edge_mismatch")
        navigation.add(verb)
    elif kind == "system_selection":
        expected = ({"select_card"} if cursor == "root" else
                    {"select_target", "cancel_selection"} if cursor == "card_targets" else
                    {"cancel_selection"} if cursor == "card_confirmation" else set())
        edge = (verb, subject)
        if (verb not in expected or arguments or edge in selection_edges
                or (verb == "cancel_selection") != (subject is None)
                or subject is not None and not _selectable(
                    visible[subject], "card" if verb == "select_card" else "target")):
            raise BoundaryError("text_menu_input", "selection_edge_mismatch")
        selection_edges.add(edge)


def _validate_v2_catalog(cursor: str, selection: list[dict[str, Any]],
                         system: list[dict[str, Any]], native: list[dict[str, Any]],
                         menu: dict[str, Any], interaction: dict[str, Any],
                         content: dict[str, Any]) -> None:
    if system:
        _require_combat_page(interaction, content)
    if cursor == "card_targets" and (native or
            not any(action["verb"] == "select_target" for action in system) or
            not any(action["verb"] == "cancel_selection" for action in system)):
        raise BoundaryError("text_menu_input", "target_catalog_mismatch")
    if cursor == "card_confirmation":
        leaves = [action for action in native if action["verb"] == "play"]
        card = menu["selection"][0]["referent_id"]
        expected = ([{"role": "target", "referent_id": menu["selection"][1]["referent_id"]}]
                    if len(selection) == 2 else [])
        if (len(native) != 1 or len(leaves) != 1 or
                not any(action["verb"] == "cancel_selection" for action in system) or
                leaves[0]["subject_referent_id"] != card or
                leaves[0]["arguments"] != expected):
            raise BoundaryError("text_menu_input", "confirmation_binding_mismatch")


def project_text_menu_snapshot(snapshot: dict[str, Any]) -> TextMenuInput:
    """The historical v1 entry point; v2 never enters through this route."""
    return _project_text_menu_snapshot(snapshot, v2=False)


def project_text_menu_v2_snapshot(snapshot: dict[str, Any]) -> TextMenuInput:
    """Explicit v2 current-page projection, with staged public selection."""
    return _project_text_menu_snapshot(snapshot, v2=True)


def validate_text_menu_v2_snapshot(snapshot: dict[str, Any], *,
                                 allow_observation_only: bool = False) -> None:
    """Validate the public source directly, without creating or parsing renderer text.

    Structured consumers extract the authorized source fields after this common
    complete-menu validation. This does not validate native legality anew.
    """
    _project_text_menu_snapshot(snapshot, v2=True, render=False,
                                allow_observation_only=allow_observation_only)


def _project_text_menu_snapshot(snapshot: dict[str, Any], *, v2: bool,
                                render: bool = True,
                                allow_observation_only: bool = False) -> TextMenuInput:
    """Project one complete current menu; keep opaque bindings outside text."""
    profile = V2_INPUT_PROFILE if v2 else INPUT_PROFILE
    schema = V2_SNAPSHOT_SCHEMA if v2 else SNAPSHOT_SCHEMA
    version = V2_VERSION if v2 else VERSION
    try:
        if v2 and set(snapshot) != {"protocol_version", "schema", "input_profile",
                "snapshot_id", "sequence", "observed_at", "status", "persistent",
                "interaction", "referents", "completeness", "session",
                "information_policy", "menu", "menu_actions"}:
            raise BoundaryError("text_menu_input", "malformed_v2_snapshot")
        if (v2 and snapshot.get("protocol_version") != "1.0.0"
                or snapshot.get("schema") != schema
                or snapshot.get("input_profile") != profile
                or snapshot.get("status") not in ({"interactive", "observed"}
                    if allow_observation_only else {"interactive"})
                or snapshot["information_policy"]["includes_hidden_information"] is not False
                or snapshot["completeness"]["status"] != "complete"
                or "bound_actions" in snapshot or "reads" in snapshot):
            raise BoundaryError("text_menu_input", "complete_current_menu_required")
        menu = _object(snapshot["menu"], "menu_required")
        if v2 and set(menu) != {"cursor", "revision", "native_snapshot_id", "selection"}:
            raise BoundaryError("text_menu_input", "malformed_v2_menu")
        cursor = menu.get("cursor")
        if (cursor not in (V2_CURSORS if v2 else CURSORS)
                or type(menu.get("revision")) is not int
                or menu["revision"] < 0 or not isinstance(menu.get("native_snapshot_id"), str)
                or not menu["native_snapshot_id"]):
            raise BoundaryError("text_menu_input", "current_cursor_required")
        catalog = _object(snapshot["menu_actions"], "catalog_required")
        if v2 and set(catalog) != {"status", "materialized_count", "total_count",
                                  "ordering_semantics", "actions"}:
            raise BoundaryError("text_menu_input", "malformed_v2_catalog")
        values = catalog.get("actions")
        if (catalog.get("status") != "complete" or not isinstance(values, list)
                or not values and not allow_observation_only
                or snapshot.get("status") == "observed" and bool(values)
                or type(catalog.get("total_count")) is not int
                or type(catalog.get("materialized_count")) is not int
                or catalog["total_count"] != len(values)
                or catalog["materialized_count"] != len(values)
                or not isinstance(catalog.get("ordering_semantics"), str)
                or not catalog["ordering_semantics"]):
            raise BoundaryError("text_menu_input", "complete_catalog_required")
        interaction = _object(snapshot["interaction"], "interaction_required")
        content = _object(interaction["content"], "current_page_content_required")
        persistent = snapshot["persistent"]
        persistent_content = (None if persistent is None else
                              _object(persistent, "persistent_required")["content"])
        if persistent_content is not None:
            persistent_content = _object(persistent_content, "persistent_content_required")
        referents = snapshot["referents"]
        if not isinstance(referents, list):
            raise BoundaryError("text_menu_input", "referents_required")
        refs: dict[str, dict[str, Any]] = {}
        for raw in referents:
            ref = _object(raw, "malformed_referent")
            identifier = _text(ref.get("referent_id"), "referent_id_required")
            if identifier in refs:
                raise BoundaryError("text_menu_input", "duplicate_referent")
            state = _object(ref.get("state"), "referent_state_required")
            if type(state.get("visible")) is not bool:
                raise BoundaryError("text_menu_input", "referent_visibility_required")
            if (ref.get("kind") not in {"entity", "control"}
                    or not isinstance(ref.get("role"), str) or not ref["role"]
                    or ref.get("label") is not None and not isinstance(ref["label"], str)):
                raise BoundaryError("text_menu_input", "referent_semantics_required")
            refs[identifier] = ref
        projector = _SemanticProjection([persistent_content, content, referents])
        visible = {identifier: ref for identifier, ref in refs.items()
                   if ref["state"]["visible"]}
        positions = {ref["referent_id"]: index for index, ref in enumerate(referents)}

        def semantic_ref(identifier: str) -> dict[str, Any]:
            ref = visible[identifier]
            return {"ordinal": positions[identifier], "role": ref["role"], "kind": ref["kind"],
                    "display_text": ref.get("label"),
                    "properties": projector.clean(ref.get("properties")),
                    "state": projector.clean(ref["state"])}

        selection: list[dict[str, Any]] = []
        if v2:
            raw_selection = menu.get("selection")
            if (not isinstance(raw_selection, list) or len(raw_selection) > 2
                    or (cursor not in {"card_targets", "card_confirmation"} and raw_selection)
                    or (cursor == "card_targets" and len(raw_selection) != 1)
                    or (cursor == "card_confirmation" and len(raw_selection) not in {1, 2})):
                raise BoundaryError("text_menu_input", "selection_phase_mismatch")
            for index, raw_item in enumerate(raw_selection):
                item = _object(raw_item, "malformed_selection")
                role = "card" if index == 0 else "target"
                selection_identifier = item.get("referent_id")
                if (set(item) != {"role", "referent_id"} or item.get("role") != role
                        or not isinstance(selection_identifier, str)
                        or selection_identifier not in visible
                        or not _selectable(visible[selection_identifier], role)):
                    raise BoundaryError("text_menu_input", "selection_binding_mismatch")
                selection.append({"role": role, "referent": semantic_ref(selection_identifier)})
            if (cursor in {"card_targets", "card_confirmation"}):
                _require_combat_page(interaction, content)

        state = {
            "CURRENT_PERSISTENT": projector.clean(persistent_content),
            "CURRENT_PAGE": {"kind": _text(interaction.get("kind"), "kind_required"),
                             "stage": interaction.get("stage"),
                             "prompt": interaction.get("prompt"),
                             "content": projector.clean(content)},
            "CURRENT_MENU": ({"cursor": cursor, "selection": selection}
                             if v2 else {"cursor": cursor}),
            "CURRENT_VISIBLE_REFERENTS": [semantic_ref(ref["referent_id"])
                                          for ref in referents if ref["state"]["visible"]],
        }
        keys: list[str] = []
        kinds: list[str] = []
        action_texts: list[str] = []
        navigation: set[str] = set()
        selection_edges: set[tuple[str, str | None]] = set()
        selection_actions: list[dict[str, Any]] = []
        native_actions: list[dict[str, Any]] = []
        for ordinal, raw in enumerate(values):
            action = _object(raw, "malformed_action")
            key = _text(action.get("action_id"), "action_id_required")
            kind = action.get("kind")
            verb = _text(action.get("verb"), "verb_required")
            label = _text(action.get("label"), "label_required")
            effect = action.get("effect_domain")
            kinds_allowed = ({"system_navigation", "system_selection", "native_input"} if v2
                             else {"system_navigation", "native_input"})
            domain_mismatch = (key in keys or kind not in kinds_allowed
                               or effect != ("native_input" if kind == "native_input" else
                                             "text_menu")
                               or (verb in NAV_VERBS) != (kind == "system_navigation"))
            if v2:
                domain_mismatch = (domain_mismatch or
                                   (verb in SELECTION_VERBS) != (kind == "system_selection"))
            if domain_mismatch:
                raise BoundaryError("text_menu_input", "action_domain_mismatch")
            assert isinstance(kind, str)
            subject = action.get("subject_referent_id")
            if subject is not None and subject not in visible:
                raise BoundaryError("text_menu_input", "subject_binding_mismatch")
            arguments = action.get("arguments")
            if not isinstance(arguments, list):
                raise BoundaryError("text_menu_input", "arguments_required")
            if v2:
                _validate_v2_action(action, cursor, visible, navigation, selection_edges)
                if kind == "system_selection":
                    selection_actions.append(action)
                elif kind == "native_input":
                    native_actions.append(action)
            roles: set[str] = set()
            semantic_arguments = []
            for raw_argument in arguments:
                argument = _object(raw_argument, "malformed_argument")
                role = _text(argument.get("role"), "argument_role_required")
                reference = argument.get("referent_id")
                if role in roles or reference not in visible:
                    raise BoundaryError("text_menu_input", "argument_binding_mismatch")
                roles.add(role)
                semantic_arguments.append({"role": role, "target": semantic_ref(reference)})
            fact = {"ordinal": ordinal, "kind": kind, "verb": verb, "display_text": label,
                    "subject": semantic_ref(subject) if subject is not None else None,
                    "arguments": semantic_arguments}
            reject_leakage(fact)
            keys.append(key)
            kinds.append(kind)
            if render:
                action_texts.append(
                    f"[STPD_ACTION version={version}]\n{_render(fact)}\n[/STPD_ACTION]")
        if v2:
            _validate_v2_catalog(cursor, selection, selection_actions, native_actions, menu,
                                 interaction, content)
        reject_leakage(state)
        state_text = ((f"[STPD_STATE version={version} profile=text_menu]\n"
                       f"{_render(state)}\n[/STPD_STATE]") if render else "")
        return TextMenuInput(state_text, tuple(action_texts), tuple(keys), tuple(kinds),
                             semantic_hash(keys))
    except (KeyError, TypeError, AttributeError) as error:
        raise BoundaryError("text_menu_input", "malformed_snapshot") from error


def classify_text_menu_result(result: dict[str, Any]) -> str:
    """Development accounting only; navigation is never native delivery/effect."""
    try:
        if result["schema"] != "sts2.player-environment/text-menu-action-result-1":
            raise BoundaryError("text_menu_input", "result_schema_mismatch")
        status, domain, delivery = (result["status"], result["effect_domain"],
                                    result["native_delivery"])
        action = result.get("action")
        if action is not None:
            action = _object(action, "malformed_result_action")
            expected = ("text_menu" if action.get("kind") == "system_navigation" else
                        "native_input" if action.get("kind") == "native_input" else None)
            if expected is None or action.get("effect_domain") != expected or domain != expected:
                raise BoundaryError("text_menu_input", "result_domain_mismatch")
        if status == "unknown":
            if (domain != "native_input" or delivery != "unknown"
                    or result.get("retry") != "never"):
                raise BoundaryError("text_menu_input", "result_domain_mismatch")
            return "unknown_native_delivery"
        if status == "not_applied":
            if (delivery not in {None, "not_delivered"}
                    or action is None and domain is not None):
                raise BoundaryError("text_menu_input", "result_domain_mismatch")
            return "not_applied"
        if status != "applied":
            raise BoundaryError("text_menu_input", "result_status_mismatch")
        if domain == "text_menu" and delivery is None and action is not None:
            return "system_navigation"
        if domain == "native_input" and delivery == "delivered" and action is not None:
            return "native_input_delivered_unsettled"
        raise BoundaryError("text_menu_input", "result_domain_mismatch")
    except (KeyError, TypeError) as error:
        raise BoundaryError("text_menu_input", "malformed_result") from error
