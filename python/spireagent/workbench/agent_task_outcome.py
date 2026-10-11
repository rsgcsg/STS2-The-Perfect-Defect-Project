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
from typing import Any, Literal, TypedDict

from spireagent.json_boundary import BoundaryError

SCHEMA = "spireagent/agent-task-outcome-v1"
MAX_EVENTS_BYTES = 64 * 1024 * 1024
MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024


class TaskCompletion(TypedDict):
    status: Literal["completed", "unproved", "unknown"]
    task_spec: dict[str, Any] | None
    reason_codes: list[str]
    witness: dict[str, Any] | None


class OperationEnd(TypedDict):
    status: Literal["task_ended", "censored", "failed", "unknown"]
    reason_codes: list[str]
    event_sequence: int | None
    controller: str | None
    tainted: bool | None
    recorded_reason: str | None
    budget: dict[str, Any] | None


class TerminalObservation(TypedDict):
    status: Literal["observed", "ambiguous", "not_observed", "unknown"]
    result: Literal["win", "loss"] | None
    ready_summary: bool | None
    witnesses: list[dict[str, Any]]


class AgentTaskOutcome(TypedDict):
    schema: str
    task_completion: TaskCompletion
    operation_end: OperationEnd
    terminal_observation: TerminalObservation
    agent_composition: dict[str, Any]
    evidence: dict[str, Any]
    strategy_quality: Literal["not_assessed"]


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


def _empty(verification: str) -> AgentTaskOutcome:
    return {
        "schema": SCHEMA,
        "task_completion": {
            "status": "unknown",
            "task_spec": None,
            "reason_codes": ["evidence_not_verified"],
            "witness": None,
        },
        "operation_end": {
            "status": "unknown",
            "reason_codes": ["evidence_not_verified"],
            "event_sequence": None,
            "controller": None,
            "tainted": None,
            "recorded_reason": None,
            "budget": None,
        },
        "terminal_observation": {
            "status": "unknown",
            "result": None,
            "ready_summary": None,
            "witnesses": [],
        },
        "agent_composition": {},
        "evidence": {"verification": verification, "content_id": None, "events": None},
        "strategy_quality": "not_assessed",
    }


def _composition(manifest: Mapping[str, Any], native: bool) -> dict[str, Any]:
    return {
        "manifest_id": manifest.get("manifest_id"),
        "agent": dict(manifest.get("agent" if native else "policy", {})),
        "adapter": dict(manifest.get("adapter", {})),
        "runtime_artifact": dict(manifest.get("artifact", {})),
        "models": (
            list(manifest.get("input", {}).get("state_recovery", {}).get("model_bindings", []))
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


def _public_terminal(snapshot: Any) -> str | None:
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema") != "sts2.player-environment/snapshot-1"
    ):
        return None
    interaction = snapshot.get("interaction", {})
    if (
        interaction.get("kind") != "game_over"
        or interaction.get("content_schema") != "sts2.player-environment/surface/game_over-1"
    ):
        return None
    content = interaction.get("content", {})
    context, surface = content.get("context", {}), content.get("surface", {})
    result = context.get("result")
    return (
        result
        if (context.get("kind") == surface.get("kind") == "game_over" and result in {"win", "loss"})
        else None
    )


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
    if teacher:
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
        if native:
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
