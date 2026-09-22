"""Current public-snapshot inputs for online/offline parity, without execution witnesses.

This is a model input projection, not a ResearchTransition or native legality proof.
The complete catalog remains Connector-owned. Historical semantic-execution views
and their trained models retain their original identities.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from spireagent.json_boundary import BoundaryError, FrozenObject

from ..canonical import canonical_json, semantic_hash
from .contracts import SemanticAction
from .platform_bundle3 import _SemanticProjection
from .public_compaction import compact_public_state
from .representation import reject_leakage

VERSION = "stpd-public-snapshot-lite-v1"
IDENTITY = {"version": VERSION, "profile": "public_lite", "status": "provisional"}
COMPACT_VERSION = "stpd-public-snapshot-compact-v2"
COMPACT_IDENTITY = {"version": COMPACT_VERSION, "profile": "public_compact",
                    "status": "provisional"}
PUBLIC_VERBS = frozenset({
    "activate", "select", "deselect", "confirm", "cancel", "play", "target",
    "use", "end_turn", "skip", "open", "close",
})


@dataclass(frozen=True)
class PublicInput:
    state_text: str
    action_texts: tuple[str, ...]
    actions: tuple[SemanticAction, ...]
    candidate_digest: str


def project_public_snapshot(snapshot: dict[str, Any], *, compact: bool = False) -> PublicInput:
    """Consume an interactive snapshot; no reads, native catalogs or chosen action.

    These are input-integrity preconditions, not a second legality engine. The
    Runtime/SDK still validate their complete public contract before delivery.
    """
    try:
        if (snapshot["schema"] != "sts2.player-environment/snapshot-1"
                or snapshot["status"] != "interactive"
                or snapshot["information_policy"]["includes_hidden_information"] is not False
                or snapshot["completeness"]["status"] != "complete"):
            raise BoundaryError("public_input", "interactive_complete_public_snapshot_required")
        catalog = snapshot["bound_actions"]
        values, referents = catalog["actions"], snapshot["referents"]
        if (not isinstance(values, list) or not values or not isinstance(referents, list)
                or catalog["status"] != "complete"
                or type(catalog["total_count"]) is not int
                or type(catalog["materialized_count"]) is not int
                or catalog["total_count"] != len(values)
                or catalog["materialized_count"] != len(values)):
            raise BoundaryError("public_input", "complete_catalog_required")
        interaction = snapshot["interaction"]
        if (not isinstance(interaction["interaction_id"], str) or not interaction["interaction_id"]
                or not isinstance(interaction["content"], dict)):
            raise BoundaryError("public_input", "interaction_binding_required")
        ref_ids = [r["referent_id"] for r in referents]
        keys = [a["bound_action_id"] for a in values]
        if (any(not isinstance(x, str) or not x for x in [*ref_ids, *keys])
                or len(set(ref_ids)) != len(ref_ids) or len(set(keys)) != len(keys)):
            raise BoundaryError("public_input", "unique_bindings_required")
        persistent = snapshot["persistent"]["content"]
        # Null properties are valid for control referents in the public contract.
        descriptions = [{**r, "properties": r.get("properties") or {}} for r in referents]
        projector = _SemanticProjection([
            persistent, interaction["content"], descriptions, interaction,
        ])
        run = projector.clean({**persistent.get("run", {}), **persistent.get("player", {})})
        if "character_definition_id" in run:
            run["character"] = run.pop("character_definition_id")
        run = {k: v for k, v in run.items() if k in {
            "character", "act", "floor", "hp", "max_hp", "gold", "run_modifiers",
        }}
        entities = [projector.clean({"role": r["role"], "properties": r.get("properties") or {},
                                     "state": r["state"]}) for r in referents]
        state = {
            "RUN": run,
            "DECISION": {"surface": interaction["kind"],
                         "content": projector.clean(interaction["content"])},
            "VISIBLE_ENTITIES": sorted(entities, key=canonical_json), "READS": [],
        }
        reject_leakage(state)
        actions = []
        for value in values:
            subject, arguments = value.get("subject_referent_id"), value["arguments"]
            if (value["interaction_id"] != interaction["interaction_id"]
                    or value["verb"] not in PUBLIC_VERBS or not isinstance(arguments, list)
                    or (subject is not None and subject not in ref_ids)):
                raise BoundaryError("public_input", "action_binding_mismatch")
            roles = [a["role"] for a in arguments]
            if (any(not isinstance(r, str) or not r for r in roles)
                    or len(set(roles)) != len(roles)
                    or any(a["referent_id"] not in ref_ids for a in arguments)):
                raise BoundaryError("public_input", "argument_binding_mismatch")
            action = SemanticAction(
                value["bound_action_id"], value["verb"], FrozenObject.of(projector.entity(subject)),
                FrozenObject.of({a["role"]: projector.entity(a["referent_id"]) for a in arguments}),
            )
            reject_leakage(action.semantic_dict())
            actions.append(action)
        version = COMPACT_VERSION if compact else VERSION
        profile = "public_compact" if compact else "public_lite"
        return PublicInput(
            f"[STPD_STATE version={version} profile={profile}]\n"
            + canonical_json(compact_public_state(state) if compact else state) + "\n[/STPD_STATE]",
            tuple(f"[STPD_ACTION version={version}]\n" + canonical_json(a.semantic_dict())
                  + "\n[/STPD_ACTION]" for a in actions),
            tuple(actions), semantic_hash(keys),
        )
    except (KeyError, TypeError, AttributeError) as error:
        raise BoundaryError("public_input", "malformed_snapshot") from error


def match_recorded_choice(public: PublicInput, recorded: tuple[SemanticAction, ...],
                          chosen_key: str) -> int:
    """Audit whether a recorded complete catalog maps exactly to a public one.

    Never guess a native verb mapping, filter extra candidates or disambiguate an
    observed choice by order. A failed audit does not delete the source decision.
    """
    old = [canonical_json(a.semantic_dict()) for a in recorded]
    new = [canonical_json(a.semantic_dict()) for a in public.actions]
    if Counter(old) != Counter(new):
        raise BoundaryError("public_input", "recorded_public_catalog_semantics_differ")
    selected = [i for i, a in enumerate(recorded) if a.key == chosen_key]
    if len(selected) != 1:
        raise BoundaryError("public_input", "recorded_choice_not_unique")
    matches = [i for i, text in enumerate(new) if text == old[selected[0]]]
    if len(matches) != 1:
        raise BoundaryError("public_input", "public_choice_ambiguous")
    return matches[0]
