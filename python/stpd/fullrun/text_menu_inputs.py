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
CURSORS = frozenset({"root", "information", "relic_inspect", "relic_tips",
                     "card_tips", "power_tips", "intent_tips", "orb_tips",
                     "topbar_tips"})
NAV_VERBS = frozenset({"open_information", "open_relic_inspect", "open_relic_tips",
                       "open_card_tips", "open_power_tips", "open_intent_tips",
                       "open_orb_tips", "open_topbar_tips", "back"})


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


def _render(value: dict[str, Any]) -> str:
    # The current page's native order and repeated descriptions remain inline.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def project_text_menu_snapshot(snapshot: dict[str, Any]) -> TextMenuInput:
    """Project exactly one complete current menu with order-preserving bindings."""
    try:
        if (snapshot.get("schema") != SNAPSHOT_SCHEMA
                or snapshot.get("input_profile") != INPUT_PROFILE
                or snapshot.get("status") != "interactive"
                or snapshot["information_policy"]["includes_hidden_information"] is not False
                or snapshot["completeness"]["status"] != "complete"
                or "bound_actions" in snapshot or "reads" in snapshot):
            raise BoundaryError("text_menu_input", "complete_current_menu_required")
        menu = _object(snapshot["menu"], "menu_required")
        cursor = menu.get("cursor")
        if (cursor not in CURSORS or type(menu.get("revision")) not in {int, str}
                or not str(menu["revision"]) or not isinstance(menu.get("native_snapshot_id"), str)
                or not menu["native_snapshot_id"]):
            raise BoundaryError("text_menu_input", "current_cursor_required")
        catalog = _object(snapshot["menu_actions"], "catalog_required")
        values = catalog.get("actions")
        if (catalog.get("status") != "complete" or not isinstance(values, list) or not values
                or type(catalog.get("total_count")) is not int
                or type(catalog.get("materialized_count")) is not int
                or catalog["total_count"] != len(values)
                or catalog["materialized_count"] != len(values)
                or not isinstance(catalog.get("ordering_semantics"), str)
                or not catalog["ordering_semantics"]):
            raise BoundaryError("text_menu_input", "complete_catalog_required")
        interaction = _object(snapshot["interaction"], "interaction_required")
        content = _object(interaction["content"], "current_page_content_required")
        persistent = _object(snapshot["persistent"], "persistent_required")
        persistent_content = _object(persistent["content"], "persistent_required")
        referents = snapshot["referents"]
        if not isinstance(referents, list):
            raise BoundaryError("text_menu_input", "referents_required")
        refs: dict[str, dict[str, Any]] = {}
        for raw in referents:
            ref = _object(raw, "malformed_referent")
            identifier = _text(ref.get("referent_id"), "referent_id_required")
            if identifier in refs:
                raise BoundaryError("text_menu_input", "duplicate_referent")
            refs[identifier] = ref
        projector = _SemanticProjection([persistent_content, content, referents])
        state = {
            "CURRENT_PERSISTENT": projector.clean(persistent_content),
            "CURRENT_PAGE": {"kind": _text(interaction.get("kind"), "kind_required"),
                             "content": projector.clean(content)},
            "CURRENT_MENU": {"cursor": cursor, "actions": []},
            "CURRENT_VISIBLE_REFERENTS": [projector.clean({"role": ref.get("role"),
                "properties": ref.get("properties") or {}, "state": ref.get("state") or {}})
                for ref in referents],
        }
        keys: list[str] = []
        kinds: list[str] = []
        action_texts: list[str] = []
        for ordinal, raw in enumerate(values):
            action = _object(raw, "malformed_action")
            key = _text(action.get("action_id"), "action_id_required")
            kind = action.get("kind")
            verb = _text(action.get("verb"), "verb_required")
            label = _text(action.get("label"), "label_required")
            effect = action.get("effect_domain")
            if (key in keys or kind not in {"system_navigation", "native_input"}
                    or effect != ("text_menu" if kind == "system_navigation" else "native_input")
                    or (verb in NAV_VERBS) != (kind == "system_navigation")):
                raise BoundaryError("text_menu_input", "action_domain_mismatch")
            subject = action.get("subject_referent_id")
            if subject is not None and subject not in refs:
                raise BoundaryError("text_menu_input", "subject_binding_mismatch")
            arguments = action.get("arguments")
            if not isinstance(arguments, list):
                raise BoundaryError("text_menu_input", "arguments_required")
            roles: set[str] = set()
            semantic_arguments = []
            for raw_argument in arguments:
                argument = _object(raw_argument, "malformed_argument")
                role = _text(argument.get("role"), "argument_role_required")
                reference = argument.get("referent_id")
                if role in roles or reference not in refs:
                    raise BoundaryError("text_menu_input", "argument_binding_mismatch")
                roles.add(role)
                semantic_arguments.append({"role": role, "target": projector.entity(reference)})
            fact = {"ordinal": ordinal, "kind": kind, "verb": verb, "display_text": label,
                    "subject": projector.entity(subject) if subject is not None else None,
                    "arguments": semantic_arguments}
            state["CURRENT_MENU"]["actions"].append(fact)
            keys.append(key)
            kinds.append(kind)
            action_texts.append(f"[STPD_ACTION version={VERSION}]\n{_render(fact)}\n[/STPD_ACTION]")
        reject_leakage(state)
        state_text = (f"[STPD_STATE version={VERSION} profile=text_menu]\n"
                      f"{_render(state)}\n[/STPD_STATE]")
        # All known execution/capture handles are sidecar-only, even when an
        # upstream public value happens to contain one as a string.
        model_text = state_text + "".join(action_texts)
        for identifier in (*keys, *refs, snapshot["snapshot_id"], menu["native_snapshot_id"]):
            if _render(identifier) in model_text:
                raise BoundaryError("text_menu_input", "opaque_binding_in_model_text")
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
        if status == "unknown":
            if domain not in {"text_menu", "native_input", None}:
                raise BoundaryError("text_menu_input", "result_domain_mismatch")
            return {"text_menu": "unknown_navigation", "native_input": "unknown_native_delivery",
                    None: "unknown_unclassified"}[domain]
        if status == "not_applied":
            if domain is not None or delivery not in {None, "not_delivered"}:
                raise BoundaryError("text_menu_input", "result_domain_mismatch")
            return "not_applied"
        if status != "applied":
            raise BoundaryError("text_menu_input", "result_status_mismatch")
        if domain == "text_menu" and delivery is None:
            return "system_navigation"
        if domain == "native_input" and delivery == "delivered":
            return "native_input_delivered_unsettled"
        raise BoundaryError("text_menu_input", "result_domain_mismatch")
    except (KeyError, TypeError) as error:
        raise BoundaryError("text_menu_input", "malformed_result") from error
