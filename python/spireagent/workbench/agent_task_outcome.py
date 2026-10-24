"""Read-only task/operation/terminal projection from accepted original Agent evidence.

This is an application result contract. Profile readers consume owning Task
predicates and verified witness joins; they create no gameplay, data or control
permission. Missing original composition or observation bytes remain unknown.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from spireagent.encoding import canonical_json, semantic_hash
from spireagent.json_boundary import BoundaryError
from spireagent.task_outcome import SCHEMA as SCHEMA
from spireagent.task_outcome import AgentTaskOutcome, _empty
from spireagent.task_outcome import OperationEnd as OperationEnd
from spireagent.task_outcome import TaskCompletion as TaskCompletion
from spireagent.task_outcome import TerminalObservation as TerminalObservation
from spireagent.task_outcome import public_terminal as _public_terminal
from stpd.p1.spec import MSTD_PACKAGE_SCHEMAS

MAX_EVENTS_BYTES = 64 * 1024 * 1024
MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024




def _verified_bytes(value: Any, name: str, maximum: int) -> bytes:
    """Recheck the exact accepted file, including body files read after SDK validation."""
    if Path(name).name != name:
        raise BoundaryError("agent_task_outcome", "unbound_evidence_file")
    record = next(
        (entry for entry in value.evidence_manifest["files"] if entry["path"] == name), None
    )
    if (
        record is None
        or type(record.get("bytes")) is not int
        or not 0 <= record["bytes"] <= maximum
    ):
        raise BoundaryError("agent_task_outcome", "projection_file_unavailable_or_over_limit")
    path = Path(value.directory) / name
    if path.is_symlink() or not path.is_file():
        raise BoundaryError("agent_task_outcome", "verified_file_changed")
    with path.open("rb") as handle:
        raw = handle.read(maximum + 1)
    if len(raw) != record["bytes"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
        raise BoundaryError("agent_task_outcome", "verified_file_changed")
    return raw


def verified_events(value: Any, frozen: bytes | None = None) -> Iterator[dict[str, Any]]:
    raw = _verified_bytes(value, "events.jsonl", MAX_EVENTS_BYTES) if frozen is None else frozen
    if frozen is not None:
        reference = _file_reference(value, "events.jsonl")
        if len(raw) > MAX_EVENTS_BYTES:
            raise BoundaryError("agent_task_outcome", "projection_file_unavailable_or_over_limit")
        if len(raw) != reference["bytes"] or hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise BoundaryError("agent_task_outcome", "verified_file_changed")
    for line in raw.splitlines():
        yield json.loads(line)


def _file_reference(value: Any, name: str) -> dict[str, Any]:
    record = next(entry for entry in value.evidence_manifest["files"] if entry["path"] == name)
    return {"path": name, "sha256": record["sha256"], "bytes": record["bytes"]}




def _composition(manifest: Mapping[str, Any], native: bool) -> dict[str, Any]:
    return {
        "manifest_id": manifest.get("manifest_id"),
        "agent": dict(manifest.get("agent" if native else "policy", {})),
        "adapter": dict(manifest.get("adapter", {})),
        "runtime_artifact": {
            "id": manifest.get("artifact", {}).get("id"),
            "sha256": manifest.get("artifact", {}).get("sha256"),
        },
        "models": (
            None
            if native
            else [
                {
                    "runtime_artifact_id": manifest.get("artifact", {}).get("id"),
                    "runtime_artifact_sha256": manifest.get("artifact", {}).get("sha256"),
                }
            ]
        ),
        "model": {
            "runtime_artifact_id": manifest.get("artifact", {}).get("id"),
            "runtime_artifact_sha256": manifest.get("artifact", {}).get("sha256"),
        },
        "input": dict(manifest.get("input" if native else "representation", {})),
        "task_spec": None,
        "program_spec": None,
        "binding": "sealed_manifest",
        "reason_codes": [],
    }


def _public_task(manifest: dict[str, Any]) -> dict[str, Any] | None:
    adapter = manifest.get("adapter", {})
    if (adapter.get("id"), adapter.get("protocol")) != (
        "stpd-public-m2-decision-adapter",
        "sts2.policy-runtime/decision-only-ndjson-4",
    ):
        return None
    from stpd.policy.public_m2_task import validate_task_spec

    try:
        return validate_task_spec(
            manifest.get("adapter_config", {}).get("public_m2_installation", {}).get("task_spec")
        )
    except BoundaryError:
        return None


def _m0_frame(
    snapshot: dict[str, Any], manifest: dict[str, Any], reference: dict[str, Any]
) -> dict[str, Any] | None:
    from stpd.fullrun.public_inputs import project_public_snapshot
    from stpd.policy.public_m0_task import ready_summary

    try:
        public = project_public_snapshot(
            snapshot, compact=manifest["representation"]["id"] == "public_compact"
        )
        if (snapshot["completeness"]["missing"] != []
                or snapshot["interaction"]["kind"] not in manifest["support"]["interaction_kinds"]
                or any(action["verb"] not in manifest["support"]["action_verbs"]
                       for action in snapshot["bound_actions"]["actions"])):
            return None
        try:
            ready = ready_summary(snapshot)
        except BoundaryError:
            ready = False
        return {**reference, "snapshot": snapshot, "candidate_digest": public.candidate_digest,
                "candidate_count": len(public.actions), "ready_summary": ready}
    except (BoundaryError, KeyError, TypeError, AttributeError):
        return None


def _m0_end_tail(
    events: list[dict[str, Any]], decision_index: int
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    tail = events[decision_index + 1:]
    if not tail or tail[-1]["kind"] != "stopped":
        return None
    handoff = None
    for event in tail[:-1]:
        kind = event["kind"]
        if kind == "controller_released":
            continue
        if (handoff is None and kind == "handoff_to_human"
                and event["payload"].get("reason") == "policy_abstained"):
            handoff = event
        elif handoff is not None and kind in {
            "fail_closed", "runtime_tainted", "controller_release_failed"
        }:
            # A declared Goal and later failed/uncertain cleanup remain separate.
            continue
        else:
            return None
    return (handoff, tail[-1]) if handoff is not None else None


def _legacy_m0(
    outcome: AgentTaskOutcome, manifest: dict[str, Any], events: list[dict[str, Any]]
) -> None:
    from stpd.policy.public_m0_task import validate_task_spec

    original = manifest.get("adapter_config", {}).get("public_m0", {})
    composition = outcome["agent_composition"]
    composition.update(task_spec=original.get("task_spec"),
                       program_spec=original.get("program_spec"))
    try:
        task = validate_task_spec(original.get("task_spec"))
    except BoundaryError:
        task = None
    completion = outcome["task_completion"]
    completion.update({"task_spec": composition["task_spec"], "reason_codes": [
        "task_spec_unbound" if task is None else "original_input_witness_unavailable"
    ]})
    signatures: dict[str, str] = {}
    frames: dict[str, dict[str, Any]] = {}
    conflicting: set[str] = set()
    observed: list[dict[str, Any]] = []
    environment = None
    available = False
    candidate = None
    for index, event in enumerate(events):
        kind, payload = event["kind"], event["payload"]
        if kind == "environment_admitted":
            environment = payload["environment"]
        snapshot = (payload.get("successor") if kind == "successor"
                    else payload.get("receipt", {}).get("successor") if kind == "receipt"
                    else None)
        if snapshot is not None:
            identity = snapshot["snapshot_id"]
            # Compare all PRIOR same-ID bodies before selecting by digest/count.
            # Only this top-level timestamp is non-semantic in the owning tracker.
            body = {key: value for key, value in snapshot.items() if key != "observed_at"}
            signature = canonical_json(body)
            if identity in signatures and signatures[identity] != signature:
                conflicting.add(identity)
                frames.pop(identity, None)
                observed[:] = [record for record in observed if record["snapshot_id"] != identity]
            signatures[identity] = signature
            if identity not in conflicting:
                reference = {
                    "basis": "prior_sealed_public_observation", "profile": "snapshot-1",
                    "source_kind": "successor" if kind == "successor" else "receipt.successor",
                    "event_sequence": event["sequence"],
                    "source_decision_id": payload["decision_id"],
                    "snapshot_id": identity, "sequence": snapshot["sequence"],
                    "body_sha256": semantic_hash(body), "events": outcome["evidence"]["events"],
                }
                frame = _m0_frame(snapshot, manifest, reference)
                if frame is not None:
                    available = True
                    frames[identity] = frame
                    result = _public_terminal(snapshot)
                    if result is not None:
                        _remember_terminal(observed, {**reference, "result": result,
                                                     "ready_summary": frame["ready_summary"]})
        if kind != "decision":
            continue
        # Every later decision replaces an earlier candidate; future bodies cannot fill it.
        candidate = None
        decision = payload["decision"]
        frame = frames.get(decision["snapshot_id"])
        if task is None:
            continue
        completion.update({"status": "unknown",
                           "reason_codes": ["original_input_witness_unavailable"]})
        if frame is None or environment is None:
            continue
        if (frame["snapshot"]["session"] != {
            key: environment[key] for key in ("runtime_instance_id", "environment_fingerprint")
        } or decision["candidate_digest"] != frame["candidate_digest"]
                or decision["candidate_count"] != frame["candidate_count"]):
            completion["reason_codes"] = ["original_public_observation_binding_unavailable"]
            continue
        completion.update({"status": "unproved",
                           "reason_codes": ["declared_task_end_not_recorded"]})
        if (frame["ready_summary"] and decision["disposition"] == "abstain"
                and decision["selected_index"] is None
                and payload["resolved_bound_action_id"] is None
                and len(decision["scores"]) == frame["candidate_count"]
                and all(type(score) in (int, float) and score == 0
                        for score in decision["scores"])):
            candidate = (index, {key: value for key, value in frame.items()
                                 if key not in {"snapshot", "ready_summary"}})
            candidate[1].update(decision_id=decision["decision_id"],
                                decision_event_sequence=event["sequence"],
                                end_kind="declared_program_zero_abstention")
    if candidate is not None:
        end = _m0_end_tail(events, candidate[0])
        if end is not None:
            handoff, stopped = end
            witness = {**candidate[1], "handoff_event_sequence": handoff["sequence"],
                       "stopped_event_sequence": stopped["sequence"]}
            completion.update({"status": "completed", "reason_codes": [], "witness": witness})
    _terminal(outcome, observed, available and not conflicting)


def _terminal(outcome: AgentTaskOutcome, observed: list[dict[str, Any]], available: bool) -> None:
    results = {record["result"] for record in observed}
    outcome["terminal_observation"] = {
        "status": "ambiguous"
        if len(results) > 1
        else "observed"
        if results
        else "not_observed"
        if available
        else "unknown",
        "result": next(iter(results)) if len(results) == 1 else None,
        "ready_summary": any(record["ready_summary"] for record in observed) if observed else None,
        "witnesses": observed,
    }
    if len(results) > 1:
        outcome["task_completion"].update(
            {
                "status": "unknown",
                "reason_codes": ["conflicting_terminal_observations"],
                "witness": None,
            }
        )


def _remember_terminal(observed: list[dict[str, Any]], witness: dict[str, Any]) -> None:
    """Retain the latest original reference for each result/readiness combination."""
    identity = (witness["result"], witness["ready_summary"])
    observed[:] = [
        record for record in observed if (record["result"], record["ready_summary"]) != identity
    ]
    observed.append(witness)


def _operation_end(outcome: AgentTaskOutcome, value: Any, events: list[dict[str, Any]]) -> None:
    failures = [event for event in events if event["kind"] in {"fail_closed", "runtime_tainted"}]
    stopped = next((event for event in reversed(events) if event["kind"] == "stopped"), None)
    payload = stopped["payload"] if stopped else {}
    budget = payload.get("autonomy_budget")
    budget = budget if isinstance(budget, dict) else None
    end = outcome["operation_end"]
    end.update(
        {
            "event_sequence": stopped.get("sequence") if stopped else None,
            "controller": payload.get("controller"),
            "tainted": value.manifest.get("tainted"),
            "recorded_reason": payload.get("reason"),
            "budget": budget,
        }
    )
    if failures:
        end.update(
            {
                "status": "failed",
                "reason_codes": [
                    "recorded_fail_closed"
                    if failures[-1]["kind"] == "fail_closed"
                    else "recorded_runtime_taint"
                ],
                "event_sequence": failures[-1]["sequence"],
                "recorded_reason": failures[-1]["payload"].get("reason"),
            }
        )
    elif (
        value.manifest.get("tainted")
        or payload.get("pending_request") is not None
        or payload.get("agent_state") == "uncertain"
        or payload.get("controller") == "unknown"
    ):
        end.update(
            {"status": "unknown", "reason_codes": ["recorded_execution_or_cleanup_uncertain"]}
        )
    elif budget is not None and budget.get("exhausted_reason") is not None:
        end.update({"status": "censored", "reason_codes": ["recorded_budget_end"]})
    elif outcome["task_completion"]["status"] == "completed":
        end.update({"status": "task_ended", "reason_codes": ["declared_task_end_recorded"]})
    elif any(event["kind"] == "autonomy_budget_exhausted" for event in events):
        end.update({"status": "censored", "reason_codes": ["recorded_budget_end"]})
    elif stopped:
        end.update(
            {"status": "censored", "reason_codes": ["recorded_stop_without_proved_task_completion"]}
        )
    else:
        end.update({"status": "unknown", "reason_codes": ["operation_end_not_recorded"]})
    if (
        stopped is not None
        and events[-1] is stopped
        and end["recorded_reason"] is None
        and end["status"] in {"censored", "task_ended"}
        and "recorded_budget_end" not in end["reason_codes"]
    ):
        # Only controller release is documented cleanup. Any other intervening
        # event blocks reuse of an earlier handoff, including a new Auto epoch.
        for event in reversed(events[:-1]):
            if event["kind"] == "controller_released":
                continue
            if event["kind"] == "handoff_to_human":
                reason = event["payload"].get("reason")
                if isinstance(reason, str) and reason:
                    end.update({"recorded_reason": reason, "event_sequence": event["sequence"]})
            break


def _generic(
    outcome: AgentTaskOutcome,
    value: Any,
    manifest: dict[str, Any],
    events: list[dict[str, Any]],
    text_terminal: Callable[[Any], str | None],
) -> None:
    if ((manifest.get("adapter", {}).get("id"), manifest.get("adapter", {}).get("protocol"))
            == ("stpd-public-m0-decision-adapter", "sts2.policy-runtime/decision-only-ndjson-1")
            and manifest.get("representation", {}).get("input_schema")
            == "sts2.player-environment/snapshot-1"):
        _legacy_m0(outcome, manifest, events)
        return
    composition = outcome["agent_composition"]
    task = _public_task(manifest)
    public_m0 = manifest.get("adapter", {}).get("id") == "stpd-public-m0-decision-adapter"
    if public_m0:
        composition["task_spec"] = (
            manifest.get("adapter_config", {}).get("public_m0", {}).get("task_spec")
        )
        composition["program_spec"] = (
            manifest.get("adapter_config", {}).get("public_m0", {}).get("program_spec")
        )
    else:
        composition["task_spec"] = task
    outcome["task_completion"].update(
        {
            "task_spec": composition["task_spec"],
            "reason_codes": [
                "original_input_witness_unavailable"
                if public_m0
                else "task_spec_unbound"
                if task is None
                else "declared_task_end_not_recorded"
            ],
            "status": "unknown" if task is None else "unproved",
        }
    )
    observed: list[dict[str, Any]] = []
    pending = None
    available = False
    completion = None
    for event in events:
        payload, kind = event["payload"], event["kind"]
        if kind == "public_decision_input":
            available = True
            snapshot = payload["snapshot"]
            result = _public_terminal(snapshot)
            ready = False
            if task is not None:
                from stpd.policy.public_m2_task import task_complete

                try:
                    ready = task_complete(snapshot)
                except (BoundaryError, KeyError, TypeError):
                    ready = False
            reference = {
                "profile": "snapshot-1",
                "event_sequence": event["sequence"],
                "decision_id": payload["decision_id"],
                "snapshot_id": snapshot["snapshot_id"],
                "sequence": snapshot["sequence"],
                "observation_ordinal": payload["control"]["observation_ordinal"],
                "events": outcome["evidence"]["events"],
            }
            if result is not None:
                _remember_terminal(
                    observed, {**reference, "result": result, "ready_summary": ready}
                )
            if completion is not None and "handoff_event_sequence" not in completion:
                completion = None
            pending = (payload, reference, ready)
        elif kind == "decision" and pending is not None:
            decision = payload["decision"]
            original, reference, ready = pending
            if (
                ready
                and task is not None
                and decision["decision_id"] == original["decision_id"]
                and decision["snapshot_id"] == original["snapshot"]["snapshot_id"]
                and decision["disposition"] == "abstain"
                and decision["selected_index"] is None
                and decision["scores"]
                and all(score == 0 for score in decision["scores"])
            ):
                completion = {
                    **reference,
                    "decision_event_sequence": event["sequence"],
                    "end_kind": "declared_program_zero_abstention",
                }
            pending = None
        elif (
            kind == "handoff_to_human"
            and completion is not None
            and payload.get("reason") == "policy_abstained"
        ):
            completion["handoff_event_sequence"] = event["sequence"]
        snapshot = None
        if kind in {"text_decision_input", "text_observation_not_admitted"}:
            snapshot = payload.get("snapshot")
        elif kind == "text_observed_successor":
            snapshot = payload.get("successor")
        elif kind == "menu_navigation":
            snapshot = payload.get("result", {}).get("successor")
        result = text_terminal(snapshot)
        if snapshot is not None:
            available = True
        if result is not None:
            _remember_terminal(
                observed,
                {
                    "profile": snapshot["input_profile"],
                    "event_sequence": event["sequence"],
                    "result": result,
                    "ready_summary": False,
                    "events": outcome["evidence"]["events"],
                },
            )
    if completion is not None and "handoff_event_sequence" in completion:
        outcome["task_completion"].update(
            {"status": "completed", "reason_codes": [], "witness": completion}
        )
    elif task is not None and not available:
        outcome["task_completion"].update(
            {"status": "unknown", "reason_codes": ["original_input_witness_unavailable"]}
        )
    _terminal(outcome, observed, available and not public_m0)


def _native(
    outcome: AgentTaskOutcome,
    value: Any,
    events: list[dict[str, Any]],
    package: dict[str, Any] | None,
    package_reason: str | None,
) -> None:
    from stpd.policy.native_task import (
        map_timed_ready_summary_task_spec,
        observe_ready_summary,
        ready_summary_task_spec,
    )

    composition = outcome["agent_composition"]
    task = package.get("agent_spec", {}).get("task_spec") if package is not None else None
    known = task in (ready_summary_task_spec(), map_timed_ready_summary_task_spec())
    teacher = bool(
        package is not None
        and package.get("schema") == "stpd/native-program-teacher-artifact-v1"
        and value.agent_manifest["adapter"]["id"] == "stpd-native-public-program-teacher"
    )
    if teacher and package is not None:
        composition["models"] = []
        composition["model"] = None
        composition["program"] = package["agent_spec"]
    composition.update(
        {
            "task_spec": task if known else None,
            "binding": "sealed_manifest_and_original_package"
            if package is not None
            else "sealed_manifest",
            "reason_codes": []
            if package is not None
            else [package_reason or "original_package_metadata_unavailable"],
        }
    )
    if package is not None and not teacher:
        # Recovery bindings do not declare the complete model dependency set.
        # A sampled-current learned Agent can have recovery.mode=none and [].
        composition["models"] = [{
            "model_id": package["model_id"],
            "weights_sha256": package.get("weights", {}).get("sha256"),
            "binding": "original_native_model_package",
        }]
        composition["model"].update(
            {
                "package_schema": package.get("schema"),
                "weights_sha256": package.get("weights", {}).get("sha256"),
            }
        )
    outcome["task_completion"].update(
        {
            "task_spec": task if known else None,
            "status": "unproved" if known else "unknown",
            "reason_codes": [] if known else [package_reason or "task_spec_unbound"],
        }
    )
    if value.agent_manifest["input"]["history_mode"] != "sampled_current":
        outcome["task_completion"].update(
            {"status": "unknown", "reason_codes": ["original_observation_body_not_recorded"]}
        )
        return
    stored: dict[str, dict[str, Any]] = {}
    last = None
    last_sequence = None
    acknowledged_basis = None
    observed: list[dict[str, Any]] = []
    available = False
    for event in events:
        kind, payload = event["kind"], event["payload"]
        if kind == "agent_sample_input_stored" and payload["disposition"] == "consume_proposed":
            stored[payload["metadata"]["acquisition_id"]] = payload["metadata"]
        elif kind == "agent_consumed":
            last = payload
            last_sequence = event["sequence"]
            acknowledged_basis = None
        elif kind == "agent_sample_consume_ack_offered" and last is not None:
            report = last["report"]
            acknowledgement = payload["acknowledgement"]
            if (
                acknowledgement != last["acknowledgement"]
                or any(
                    acknowledgement[key] != report[key]
                    for key in ("acquisition_id", "consumption_id", "state_version", "advanced")
                )
                or acknowledgement["prefix"]["continuity_token"] != report["continuity_token"]
            ):
                continue
            metadata = stored.get(report["acquisition_id"])
            if metadata is None:
                continue
            observation_file = metadata["observation"]
            raw = _verified_bytes(value, observation_file["path"], MAX_BODY_BYTES)
            if (
                len(raw) != observation_file["bytes"]
                or hashlib.sha256(raw).hexdigest() != observation_file["sha256"]
            ):
                raise BoundaryError("agent_task_outcome", "original_sample_binding_changed")
            observation = json.loads(raw)
            terminal = observe_ready_summary(observation)
            available = True
            witness = {
                "profile": "native-logical-v1",
                "consume_event_sequence": last_sequence,
                "acknowledgement_event_sequence": event["sequence"],
                "acquisition_id": report["acquisition_id"],
                "consumption_id": report["consumption_id"],
                "state_version": report["state_version"],
                "continuity_token": report["continuity_token"],
                "snapshot_id": observation["snapshot_id"],
                "observation": _file_reference(value, observation_file["path"]),
                "events": outcome["evidence"]["events"],
            }
            acknowledged_basis = (witness, terminal.agent_task_complete, terminal.outcome)
            if terminal.outcome is not None:
                _remember_terminal(
                    observed,
                    {
                        **witness,
                        "result": terminal.outcome,
                        "ready_summary": terminal.agent_task_complete,
                    },
                )
        elif kind == "agent_directive" and last is not None and acknowledged_basis is not None:
            output = payload["output"]
            directive = output["directive"]
            report = last["report"]
            witness, ready, terminal_result = acknowledged_basis
            reason = ("natural_ready_summary_" + terminal_result
                      if teacher and terminal_result in {"win", "loss"}
                      else "native_ready_summary_task_complete" if not teacher else None)
            if (
                known
                and ready
                and directive["type"] == "close"
                and reason is not None and directive["reason"] == reason
                and all(
                    output[key] == report[key]
                    for key in ("continuity_token", "consumption_id", "state_version")
                )
            ):
                outcome["task_completion"].update(
                    {
                        "status": "completed",
                        "reason_codes": [],
                        "witness": {**witness, "directive_event_sequence": event["sequence"]},
                    }
                )
    if known and outcome["task_completion"]["status"] != "completed":
        bound = last is not None and last["report"]["acquisition_id"] in stored
        outcome["task_completion"].update(
            {
                "status": "unproved" if bound else "unknown",
                "reason_codes": [
                    "declared_task_end_not_recorded"
                    if bound
                    else "acknowledged_terminal_body_unavailable"
                ],
            }
        )
    _terminal(outcome, observed, available)


def _global_program(outcome, value, manifest, events, original):
    """GLOBAL public observations remain separate from a Task success predicate."""
    package = json.loads(original) if original is not None else None
    if (
        not isinstance(package, dict)
        or package.get("schema") != "spireagent/global-public-program-1"
        or hashlib.sha256(original).hexdigest() != manifest["artifact"]["sha256"]
        or package.get("adapter") != manifest["adapter"]
        or package.get("agent_spec", {}).get("model_bindings") != []
        or manifest["input"]["state_recovery"]["model_bindings"] != []
    ):
        raise BoundaryError("agent_task_outcome", "global_original_program_unavailable")
    outcome["agent_composition"].update(
        models=[],
        model=None,
        program_spec=package["agent_spec"],
        task_spec=package["agent_spec"].get("task_spec"),
        binding="original_public_program_artifact",
    )
    outcome["task_completion"].update(
        status="unproved",
        task_spec=package["agent_spec"].get("task_spec"),
        reason_codes=["program_declares_no_task_success_predicate"],
        witness=None,
    )
    _global_terminal(outcome, value, events)


def _global_model(outcome, value, manifest, events, original):
    """Bind original P1 metadata without loading tensors or inventing a Goal."""
    package = json.loads(original) if original is not None else None
    baseline = manifest.get("adapter", {}).get("id") == "stpd-p1-baseline-agent"
    if (
        not isinstance(package, dict)
        or package.get("schema") not in (
            {"stpd/p1-baseline-model-1"} if baseline else MSTD_PACKAGE_SCHEMAS)
        or package.get("package_sha256") != manifest["artifact"]["sha256"]
        or package.get("agent_spec", {}).get("id") != manifest["agent"]["id"]
        or package.get("agent_spec", {}).get("version") != manifest["agent"]["version"]
        or baseline and package.get("input_spec") != manifest["input"]["input_spec"]
        or manifest["input"]["state_recovery"]["model_bindings"] != []
    ):
        raise BoundaryError("agent_task_outcome", "global_original_model_unavailable")
    model = dict(outcome["agent_composition"]["model"])
    outcome["agent_composition"].update(
        models=[model], model=model, program_spec=package["agent_spec"],
        task_spec=package["agent_spec"].get("task_spec"),
        binding="original_p1_model_artifact",
    )
    outcome["task_completion"].update(
        status="unproved", task_spec=package["agent_spec"].get("task_spec"),
        reason_codes=["program_declares_no_task_success_predicate"], witness=None,
    )
    _global_terminal(outcome, value, events)


def _global_terminal(outcome, value, events):
    sources = {
        event["payload"]["ordinal"]: event["payload"]["artifact"]
        for event in events
        if event["kind"] == "global_source"
    }
    terminals = []
    for event in events:
        if (
            event["kind"] != "global_observation"
            or event["payload"]["status"] != "terminal"
            or not event["payload"]["complete"]
        ):
            continue
        ref = sources[event["payload"]["source_ordinal"]]
        record = json.loads(_verified_bytes(value, ref["path"], MAX_BODY_BYTES))
        basis = json.loads(record["serialized_observation"])
        result = _public_terminal(basis["snapshot"])
        if basis["capture_status"] == "complete" and result is not None:
            terminals.append(
                {
                    "result": result,
                    "source": _file_reference(value, ref["path"]),
                    "event_sequence": event["sequence"],
                }
            )
    results = {item["result"] for item in terminals}
    outcome["terminal_observation"].update(
        status="observed"
        if len(results) == 1
        else "ambiguous"
        if len(results) > 1
        else "not_observed",
        result=next(iter(results)) if len(results) == 1 else None,
        ready_summary=None,
        witnesses=terminals,
    )


def project(
    result: Any,
    *,
    native: bool,
    native_package: bytes | None = None,
    package_reason: str | None = None,
    text_terminal: Callable[[Any], str | None] = lambda _value: None,
    frozen_events: bytes | None = None,
    application_request_status: str | None = None,
) -> AgentTaskOutcome:
    outcome = _empty(result.status)
    if result.status != "pass" or result.value is None:
        return outcome
    value = result.value
    outcome["evidence"]["content_id"] = value.content_id
    events = None
    try:
        events = list(verified_events(value, frozen_events))
        outcome["evidence"]["events"] = _file_reference(value, "events.jsonl")
        # Operation facts depend on these accepted events, not on a later
        # Task/package/body projection. Recompute after a successful completion.
        _operation_end(outcome, value, events)
        manifest = (
            dict(value.agent_manifest)
            if native
            else json.loads(_verified_bytes(value, "policy-manifest.json", MAX_METADATA_BYTES))
        )
        composition = _composition(manifest, native)
        composition["manifest_sha256"] = value.manifest[
            "agent_manifest_sha256" if native else "policy_manifest_sha256"
        ]
        outcome["agent_composition"] = composition
        if native and manifest.get("input", {}).get("profile") == "p1-global-r1":
            if manifest.get("adapter", {}).get("id") in {
                "stpd-p1-mstd-agent", "stpd-p1-baseline-agent"
            }:
                _global_model(outcome, value, manifest, events, native_package)
            else:
                _global_program(outcome, value, manifest, events, native_package)
        elif native:
            package = None
            if native_package is not None:
                try:
                    if len(native_package) > MAX_METADATA_BYTES:
                        raise BoundaryError(
                            "agent_task_outcome", "original_package_metadata_over_limit"
                        )
                    candidate = json.loads(native_package)
                    teacher = (
                        isinstance(candidate, dict)
                        and candidate.get("schema") == "stpd/native-program-teacher-artifact-v1"
                        and manifest.get("adapter", {}).get("id")
                        == "stpd-native-public-program-teacher"
                        and candidate.get("adapter") == manifest["adapter"]
                        and candidate.get("agent_spec", {}).get("model_bindings") == []
                        and candidate.get("agent_spec", {}).get("learned") is False
                        and candidate.get("agent_spec", {}).get("scores") is None
                        and manifest["input"]["state_recovery"]["model_bindings"] == []
                    )
                    if (
                        hashlib.sha256(native_package).hexdigest() != manifest["artifact"]["sha256"]
                        or not isinstance(candidate, dict)
                        or (not teacher and candidate.get("model_id") != manifest["artifact"]["id"])
                        or candidate.get("input_spec") != manifest["input"]["input_spec"]
                        or candidate.get("agent_spec", {}).get("id") != manifest["agent"]["id"]
                        or candidate.get("agent_spec", {}).get("version")
                        != manifest["agent"]["version"]
                    ):
                        package_reason = "original_composition_binding_changed"
                    else:
                        package = candidate
                except (ValueError, KeyError, TypeError, AttributeError, BoundaryError) as error:
                    package_reason = (
                        error.code
                        if isinstance(error, BoundaryError)
                        else "original_composition_binding_changed"
                    )
            _native(outcome, value, events, package, package_reason)
            if (manifest.get("adapter", {}).get("id") == "stpd-native-public-program-teacher"
                    and application_request_status is not None
                    and application_request_status != "bound"):
                outcome["task_completion"].update({
                    "status": "unknown",
                    "reason_codes": ["original_request_run_binding_unavailable"],
                    "witness": None,
                })
        else:
            _generic(outcome, value, manifest, events, text_terminal)
        _operation_end(outcome, value, events)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, BoundaryError) as error:
        outcome["task_completion"].update(
            {
                "status": "unknown",
                "reason_codes": [
                    error.code
                    if isinstance(error, BoundaryError)
                    else "original_projection_unavailable"
                ],
                "witness": None,
            }
        )
        outcome["terminal_observation"].update(
            {"status": "unknown", "result": None, "ready_summary": None, "witnesses": []}
        )
        if events is None:
            outcome["operation_end"].update(
                {"status": "unknown", "reason_codes": ["original_projection_unavailable"]}
            )
    return outcome
