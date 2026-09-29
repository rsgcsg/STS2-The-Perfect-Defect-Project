"""Read-only, source-verified sequences of observed text-menu inputs.

This projection preserves recorded order and input identity. It is not a
canonical game trajectory, a dataset admission path, or a training allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from spireagent.json_boundary import BoundaryError
from spireagent.storage.store import ArtifactStore

from .managed_text_menu_import import (
    SOURCE_SCHEMA as MANAGED_SOURCE_SCHEMA,
)
from .managed_text_menu_import import (
    load_managed_text_menu_source,
)
from .text_menu_human_import import (
    SOURCE_SCHEMA as HUMAN_SOURCE_SCHEMA,
)
from .text_menu_human_import import (
    load_human_text_source,
)
from .text_menu_inputs import project_text_menu_snapshot
from .text_menu_runtime_import import (
    EVIDENCE_SCHEMA as AGENT_EVIDENCE_SCHEMA,
)
from .text_menu_runtime_import import (
    load_verified_agent_run_events,
)


@dataclass(frozen=True)
class SourceEventRef:
    sequence: int
    kind: str
    event_id: str


@dataclass(frozen=True)
class ObservedInput:
    stream_id: str
    source_kind: Literal["agent_decision_inputs", "human_input_stream",
                         "managed_control_input_stream"]
    event_id: str
    source_sequence: int
    snapshot: dict[str, Any] | None
    observation_mask: bool
    selected_action_id: str | None
    choice_mask: bool
    delivery_status: Literal[
        "delivered", "not_delivered", "unknown", "not_attempted",
        "not_applicable", "human_witness_only", "untrusted_result",
    ]
    delivery_mask: bool
    successor_snapshot: dict[str, Any] | None
    successor_relation: Literal[
        "ui_navigation", "post_native_observation", "unknown", "none",
    ]
    successor_observation_mask: bool
    causal_successor_mask: bool
    reset_before: bool
    reset_reason: str | None
    source_events: tuple[SourceEventRef, ...]


@dataclass(frozen=True)
class ObservedInputView:
    source_id: str
    stream_scope: Literal["verified_agent_observed_inputs", "partial_human_input_stream",
                          "managed_engineering_control_inputs"]
    trajectory_complete: Literal[False]
    inputs: tuple[ObservedInput, ...]


@dataclass(frozen=True)
class FixedWindow:
    stream_id: str
    inputs: tuple[ObservedInput | None, ...]
    valid_mask: tuple[bool, ...]
    observation_mask: tuple[bool, ...]
    burn_in_mask: tuple[bool, ...]
    choice_mask: tuple[bool, ...]
    delivery_mask: tuple[bool, ...]
    successor_observation_mask: tuple[bool, ...]
    causal_successor_mask: tuple[bool, ...]


_AGENT_BOUNDARIES = frozenset({
    "environment_admitted", "mode_changed", "handoff_to_human", "fail_closed",
    "runtime_tainted", "stale_whole_bundle_discarded",
})


def load_observed_input_view(store: ArtifactStore, source_id: str) -> ObservedInputView:
    """Load a verified Agent archive or Human input source without publishing data."""
    manifest = store.get_manifest(source_id)
    schema = manifest.parameters.value().get("schema")
    if schema == AGENT_EVIDENCE_SCHEMA:
        return _agent_view(store, source_id)
    if schema == HUMAN_SOURCE_SCHEMA:
        return _human_view(store, source_id)
    if schema == MANAGED_SOURCE_SCHEMA:
        return _managed_view(store, source_id)
    raise BoundaryError("observed_input_sequence", "unsupported_verified_source")


def _managed_view(store: ArtifactStore, source_id: str) -> ObservedInputView:
    source = load_managed_text_menu_source(store, source_id)
    stream_id = "managed:" + source.report["session_id"]
    inputs: list[ObservedInput] = []
    for item in source.inputs:
        result = item.result
        native = result["effect_domain"] == "native_input"
        inputs.append(ObservedInput(
            stream_id=stream_id, source_kind="managed_control_input_stream",
            event_id=f"managed:{item.event_artifact_id}:{item.request_id}",
            source_sequence=item.sequence,
            snapshot=item.before_context["snapshot"], observation_mask=True,
            selected_action_id=item.action_id, choice_mask=True,
            delivery_status="delivered" if native else "not_applicable",
            delivery_mask=native,
            successor_snapshot=result["successor"],
            successor_relation="post_native_observation" if native else "ui_navigation",
            successor_observation_mask=True, causal_successor_mask=False,
            reset_before=item.sequence == 1,
            reset_reason="stream_start" if item.sequence == 1 else None,
            source_events=(SourceEventRef(item.sequence, "managed_run_event",
                                          item.event_artifact_id),),
        ))
    return ObservedInputView(source_id, "managed_engineering_control_inputs", False,
                             tuple(inputs))


def _agent_view(store: ArtifactStore, source_id: str) -> ObservedInputView:
    verified = load_verified_agent_run_events(store, source_id)
    events = verified.events
    decisions: dict[str, dict[str, Any]] = {}
    dispatches: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    successors: dict[str, dict[str, Any]] = {}
    for event in events:
        kind, payload = event["kind"], event["payload"]
        if kind == "decision":
            decision = payload["decision"]
            decisions[decision["decision_id"]] = {"event": event, **payload}
        elif kind == "text_menu_dispatch_attempt":
            dispatches[payload["decision_id"]] = {"event": event, **payload}
        elif kind in {"menu_navigation", "text_native_delivery", "text_native_unknown",
                      "text_menu_not_applied", "text_menu_result_rejected"}:
            outcomes[payload["decision_id"]] = {"event": event, **payload, "kind": kind}
        elif kind == "text_observed_successor":
            successors[payload["decision_id"]] = {"event": event, **payload}

    stream_id = f"agent:{verified.content_id}:{verified.run_id}"
    pending_reset: str | None = "stream_start"
    inputs: list[ObservedInput] = []
    for event in events:
        kind, payload = event["kind"], event["payload"]
        if kind in _AGENT_BOUNDARIES:
            pending_reset = kind
            continue
        if kind != "text_decision_input":
            continue
        decision_id = payload["decision_id"]
        decision = decisions.get(decision_id)
        dispatch = dispatches.get(decision_id)
        outcome = outcomes.get(decision_id)
        successor = successors.get(decision_id)
        chosen = decision.get("resolved_bound_action_id") if decision else None
        status: Literal[
            "delivered", "not_delivered", "unknown", "not_attempted",
            "not_applicable", "human_witness_only", "untrusted_result",
        ] = "not_attempted"
        successor_snapshot = None
        relation: Literal[
            "ui_navigation", "post_native_observation", "unknown", "none",
        ] = "none"
        outcome_event = outcome["event"] if outcome else None
        if outcome:
            result = outcome["result"]
            if outcome["kind"] == "menu_navigation":
                status = "not_applicable"
                successor_snapshot = result.get("successor")
                relation = "ui_navigation" if successor_snapshot is not None else "unknown"
            elif outcome["kind"] == "text_native_delivery":
                status = "delivered"
                successor_snapshot = successor.get("successor") if successor else None
                relation = ("post_native_observation" if successor_snapshot is not None
                            else "unknown")
            elif outcome["kind"] == "text_native_unknown":
                status, relation = "unknown", "unknown"
            elif outcome["kind"] == "text_menu_not_applied":
                native_delivery = result.get("native_delivery")
                status = ("not_delivered" if native_delivery == "not_delivered" else
                          "not_applicable" if result.get("effect_domain") == "text_menu"
                          else "unknown")
                relation = "none"
            else:
                status, relation = "untrusted_result", "unknown"
        elif dispatch:
            status = "unknown"

        refs = [SourceEventRef(
            event["sequence"], kind,
            f"{verified.content_id}:{event['sequence']}",
        )]
        if decision:
            item = decision["event"]
            refs.append(SourceEventRef(
                item["sequence"], "decision", f"{verified.content_id}:{item['sequence']}",
            ))
        if dispatch:
            item = dispatch["event"]
            refs.append(SourceEventRef(
                item["sequence"], "text_menu_dispatch_attempt",
                f"{verified.content_id}:{item['sequence']}",
            ))
        if outcome_event is not None and outcome is not None:
            refs.append(SourceEventRef(
                outcome_event["sequence"], outcome["kind"],
                f"{verified.content_id}:{outcome_event['sequence']}",
            ))
        if successor:
            item = successor["event"]
            refs.append(SourceEventRef(
                item["sequence"], "text_observed_successor",
                f"{verified.content_id}:{item['sequence']}",
            ))
        inputs.append(ObservedInput(
            stream_id=stream_id, source_kind="agent_decision_inputs",
            event_id=f"{verified.content_id}:{event['sequence']}:{decision_id}",
            source_sequence=event["sequence"], snapshot=payload["snapshot"],
            observation_mask=True,
            selected_action_id=chosen,
            choice_mask=chosen is not None,
            delivery_status=status, delivery_mask=status in {"delivered", "not_delivered"},
            successor_snapshot=successor_snapshot,
            successor_relation=relation,
            successor_observation_mask=successor_snapshot is not None,
            # The text-menu archive proves UI observations, not canonical S'.
            causal_successor_mask=False,
            reset_before=pending_reset is not None, reset_reason=pending_reset,
            source_events=tuple(refs),
        ))
        pending_reset = None
    return ObservedInputView(source_id, "verified_agent_observed_inputs", False,
                             tuple(inputs))


def _human_view(store: ArtifactStore, source_id: str) -> ObservedInputView:
    _, rows = load_human_text_source(store, source_id)
    inputs: list[ObservedInput] = []
    current_identity: tuple[str, str, str] | None = None
    previous_sequence = 0
    previous_observation = True
    pending_reset: str | None = None
    for row in rows:
        identity = (row["session_id"], row["timeline_id"], row["run_id"])
        stream_id = "human:" + ":".join(identity)
        sequence = row["sequence"]
        if type(sequence) is not int or sequence < 1:
            raise BoundaryError("observed_input_sequence", "human_input_order_mismatch")
        if identity != current_identity:
            pending_reset = "identity_change" if current_identity is not None else "stream_start"
            previous_sequence = 0
            previous_observation = True
            current_identity = identity
        if sequence <= previous_sequence:
            raise BoundaryError("observed_input_sequence", "human_input_order_mismatch")
        previous_sequence = sequence
        raw_snapshot = row.get("snapshot")
        snapshot = raw_snapshot if isinstance(raw_snapshot, dict) else None
        observation_mask = snapshot is not None
        selected = row.get("chosen_action")
        choice_mask = row.get("disposition") == "accepted_input"
        selected_id = None
        if choice_mask:
            if (not isinstance(snapshot, dict) or row.get("mapping_status") != "exact_unique"
                    or row.get("match_count") != 1
                    or row.get("mapping_basis") != "text_menu_native_reference_equality"
                    or row.get("external_controller_active") is not False
                    or not isinstance(selected, dict)):
                raise BoundaryError("observed_input_sequence", "human_choice_binding_mismatch")
            public = project_text_menu_snapshot(snapshot)
            actions = snapshot["menu_actions"]["actions"]
            if actions.count(selected) != 1 or selected.get("action_id") not in public.action_ids:
                raise BoundaryError("observed_input_sequence", "human_choice_binding_mismatch")
            selected_id = selected["action_id"]
        if not observation_mask:
            pending_reset = "missing_observation"
        elif not previous_observation and pending_reset is None:
            pending_reset = "after_missing_observation"
        inputs.append(ObservedInput(
            stream_id=stream_id, source_kind="human_input_stream",
            event_id=f"human:{stream_id}:{row['record_id']}",
            source_sequence=sequence, snapshot=snapshot, observation_mask=observation_mask,
            selected_action_id=selected_id, choice_mask=choice_mask,
            # Human input witness is not Connector delivery or Commit proof.
            delivery_status="human_witness_only" if choice_mask else "unknown",
            delivery_mask=False, successor_snapshot=None, successor_relation="none",
            successor_observation_mask=False, causal_successor_mask=False,
            reset_before=pending_reset is not None, reset_reason=pending_reset,
            source_events=(SourceEventRef(sequence, "human_text_input", row["record_id"]),),
        ))
        previous_observation = observation_mask
        pending_reset = "after_missing_observation" if not observation_mask else None
    return ObservedInputView(source_id, "partial_human_input_stream", False, tuple(inputs))


def build_fixed_windows(
    view: ObservedInputView, *, learn_steps: int, burn_in_steps: int,
) -> tuple[FixedWindow, ...]:
    """Build fixed-size windows; event-number gaps alone never split a stream."""
    if (type(learn_steps) is not int or learn_steps < 1
            or type(burn_in_steps) is not int or burn_in_steps < 0):
        raise BoundaryError("observed_input_sequence", "invalid_window_shape")
    seen: set[str] = set()
    segments: list[tuple[str, list[ObservedInput]]] = []
    for item in view.inputs:
        if item.event_id in seen:
            raise BoundaryError("observed_input_sequence", "duplicate_observed_event")
        seen.add(item.event_id)
        if not segments or item.stream_id != segments[-1][0] or item.reset_before:
            segments.append((item.stream_id, []))
        if segments[-1][1] and item.source_sequence <= segments[-1][1][-1].source_sequence:
            raise BoundaryError("observed_input_sequence", "event_order_mismatch")
        segments[-1][1].append(item)
    windows: list[FixedWindow] = []
    for stream_id, segment in segments:
        if not any(item.observation_mask for item in segment):
            continue
        for target_start in range(0, len(segment), learn_steps):
            targets = segment[target_start:target_start + learn_steps]
            context = segment[max(0, target_start - burn_in_steps):target_start]
            left_padding = burn_in_steps - len(context)
            values: list[ObservedInput | None] = ([None] * left_padding
                                                  + list(context) + list(targets))
            values.extend([None] * (burn_in_steps + learn_steps - len(values)))
            burn_mask = ([False] * left_padding + [True] * len(context)
                         + [False] * learn_steps)
            valid_mask = [value is not None for value in values]
            windows.append(FixedWindow(
                stream_id, tuple(values), tuple(valid_mask),
                tuple(value is not None and value.observation_mask for value in values),
                tuple(burn_mask),
                tuple(value is not None and value.observation_mask and not burn
                      and value.choice_mask for value, burn in zip(values, burn_mask, strict=True)),
                tuple(value is not None and value.observation_mask and not burn
                      and value.delivery_mask
                      for value, burn in zip(values, burn_mask, strict=True)),
                tuple(value is not None and value.observation_mask and not burn
                      and value.successor_observation_mask
                      for value, burn in zip(values, burn_mask, strict=True)),
                tuple(value is not None and value.observation_mask and not burn
                      and value.causal_successor_mask
                      for value, burn in zip(values, burn_mask, strict=True)),
            ))
    return tuple(windows)
