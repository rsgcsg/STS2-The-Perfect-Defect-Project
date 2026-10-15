"""Bounded, test-only projection of returned suite facts; never contacts owners."""

from __future__ import annotations

import hashlib
import json
import math
import re
from contextlib import suppress
from pathlib import Path
from typing import Any

MAX_DIAGNOSTIC_BYTES = 131072
MAX_ORIGINAL_BYTES = 1024 * 1024
_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_MISSING = {"availability": "missing"}


def _field(value: Any, key: str, *, identity: bool = False) -> Any:
    if not isinstance(value, dict) or key not in value:
        return dict(_MISSING)
    item = value[key]
    if item is None:
        return {"availability": "null"}
    if identity:
        if not isinstance(item, str) or len(item) > 512:
            return {"availability": "invalid"}
        return {"availability": "present", "sha256": hashlib.sha256(item.encode()).hexdigest()}
    if type(item) is bool or type(item) is int:
        return {"availability": "present", "value": item}
    if type(item) is float and math.isfinite(item):
        return {"availability": "present", "value": item}
    if key == "schema" and isinstance(item, str) and re.fullmatch(r"[a-zA-Z0-9._/-]{1,128}", item):
        return {"availability": "present", "value": item}
    if isinstance(item, str) and _CODE.fullmatch(item):
        return {"availability": "present", "value": item}
    return {"availability": "invalid"}


def _fields(value: Any, names: tuple[str, ...]) -> dict[str, Any]:
    return {name: _field(value, name) for name in names}


def _original(root: Path, reference: Any) -> tuple[dict[str, Any], Any]:
    """Read only exact bounded, hash-matching public test artifacts, no globals."""
    facts = _fields(reference, ("bytes", "sha256"))
    if not isinstance(reference, dict) or "path" not in reference:
        return {**facts, "availability": "missing"}, None
    name = reference["path"]
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"\d{4}-[a-z-]+\.json", name)
        or Path(name).name != name
    ):
        return {**facts, "availability": "unsafe_path"}, None
    facts["label"] = name
    path = root / name
    try:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            return {**facts, "availability": "unsafe_path"}, None
        with path.open("rb") as stream:
            raw = stream.read(MAX_ORIGINAL_BYTES + 1)
        if len(raw) > MAX_ORIGINAL_BYTES:
            return {**facts, "availability": "over_limit"}, None
        claimed = reference.get("sha256")
        if (
            type(reference.get("bytes")) is not int
            or reference["bytes"] != len(raw)
            or not isinstance(claimed, str)
            or not _SHA.fullmatch(claimed)
            or hashlib.sha256(raw).hexdigest() != claimed
        ):
            return {**facts, "availability": "hash_or_length_mismatch"}, None
        value = json.loads(raw)
        if not isinstance(value, dict):
            return {**facts, "availability": "not_object"}, None
        return {**facts, "availability": "verified"}, value
    except Exception as error:
        return {**facts, "availability": "unavailable", "error_type": type(error).__name__}, None


def _identity(value: Any) -> dict[str, Any]:
    return {
        name: _field(value, name, identity=True)
        for name in (
            "intent_id",
            "environment_ref",
            "generation_id",
            "run_id",
            "operation_id",
            "application_instance_id",
            "configuration_id",
            "composition_id",
            "selection_id",
        )
    }


def _request(value: Any) -> dict[str, Any]:
    obj = value if isinstance(value, dict) else {}
    admission = obj.get("admission_context")
    episode = admission.get("native_episode") if isinstance(admission, dict) else None
    binding = episode.get("binding") if isinstance(episode, dict) else None
    budget = obj.get("budget")
    return {
        "fields": _fields(value, ("schema", "operation", "run_profile", "target")),
        "identity": _identity(value),
        "episode": _identity(episode),
        "binding": _identity(binding),
        "limits": _fields(
            budget.get("limits") if isinstance(budget, dict) else None,
            ("max_submissions", "max_policy_calls", "deadline_ms"),
        ),
    }


def _finish(value: Any) -> dict[str, Any]:
    obj = value if isinstance(value, dict) else {}
    actor, cleanup = obj.get("actor_finish"), obj.get("cleanup")
    return {
        "fields": _fields(value, ("phase", "finish_complete")),
        "identity": _identity(value),
        "actor": _fields(actor, ("status", "seal", "process_exit_code", "stop_error")),
        "actor_run": _field(actor, "run_id", identity=True),
        "evaluation_reference": _fields(
            actor.get("evaluation_reference") if isinstance(actor, dict) else None,
            ("schema", "sha256", "owner"),
        ),
        "cleanup": _fields(
            cleanup,
            (
                "status",
                "host_close",
                "native_exit_disposition",
                "native_shutdown_ack",
                "driver_exit_code",
                "forced_fallback",
                "cleanup_error",
            ),
        ),
    }


def _evaluation(value: Any) -> dict[str, Any]:
    obj = value if isinstance(value, dict) else {}
    outcome, execution = obj.get("agent_task_outcome"), obj.get("agent_execution")
    result = {
        "fields": _fields(
            value,
            (
                "schema",
                "evaluation_id",
                "evidence_content_id",
                "evidence_verification",
                "controller_at_stop",
                "tainted",
                "pending_request",
            ),
        ),
        "run_id": _field(value, "run_id", identity=True),
        "execution": _fields(execution, ("status",)),
        "outcomes": {},
    }
    for name in ("task_completion", "terminal_observation", "operation_end"):
        axis = outcome.get(name) if isinstance(outcome, dict) else None
        result["outcomes"][name] = _fields(axis, ("status", "result", "controller", "tainted"))
    for name in ("delivery_counts", "request_outcome_counts"):
        result[name] = _fields(obj.get(name), ("unknown", "partially_delivered"))
    return result


def _actor_binding_observations(value: Any) -> dict[str, Any]:
    obj = value if isinstance(value, dict) else {}
    resource = obj.get("native_resource")
    state = obj.get("application_status")
    actor = resource.get("actor") if isinstance(resource, dict) else None
    startup = state.get("startup") if isinstance(state, dict) else None
    return {
        "read_order": _field(value, "read_order"),
        "resource_identity": _identity(resource),
        "resource_phase": _field(resource, "phase"),
        "resource_actor_run": _field(actor, "run_id", identity=True),
        "application_run": _field(startup, "run_id", identity=True),
    }


def report_diagnostic(report: Any, root: Path) -> str:
    """No raw config, headers, World, snapshots, stderr, or exception messages."""
    rows = report.get("episodes") if isinstance(report, dict) else None
    value: dict[str, Any] = {
        "schema": "r0-test-report-diagnostic-1",
        "projection_not_original": True,
        "top": _fields(
            report,
            (
                "status",
                "stop_reason",
                "elapsed_seconds",
                "planned",
                "max_episodes",
                "world_offers",
                "actor_started",
                "persistence_error",
            ),
        ),
        "attempt_id": _field(report, "attempt_id", identity=True),
        "limits": _fields(
            report.get("limits") if isinstance(report, dict) else None,
            (
                "preparation_seconds",
                "episode_seconds",
                "cleanup_seconds",
                "suite_seconds",
                "poll_seconds",
                "http_seconds",
            ),
        ),
        "summary": _fields(
            report.get("summary") if isinstance(report, dict) else None,
            ("not_started", "cleanup_unknown"),
        ),
        "episode_count": {"availability": "present", "value": len(rows)}
        if isinstance(rows, list)
        else dict(_MISSING),
        "episodes": [],
    }
    for row in rows[:20] if isinstance(rows, list) else []:
        entry: dict[str, Any] = {
            "fields": _fields(
                row,
                (
                    "ordinal",
                    "status",
                    "reason",
                    "preparation_outcome",
                    "actor_started",
                    "world_mutation_offered",
                    "cleanup_accepted",
                    "elapsed_seconds",
                    "status_read_error",
                    "cleanup_error",
                    "persistence_error",
                ),
            ),
            "identity": _identity(row),
            "seed": _field(row, "seed", identity=True),
        }
        obj = row if isinstance(row, dict) else {}
        state = obj.get("last_status")
        entry["status_availability"] = _fields(state, ("status", "observation_error", "error_code"))
        artifacts = obj.get("artifacts")
        entry["artifact_count"] = len(artifacts) if isinstance(artifacts, list) else dict(_MISSING)
        entry["saved_phases"] = []
        for ref in artifacts[-12:] if isinstance(artifacts, list) else []:
            name = ref.get("path") if isinstance(ref, dict) else None
            entry["saved_phases"].append(
                {
                    "label": name
                    if isinstance(name, str) and re.fullmatch(r"\d{4}-[a-z-]+\.json", name)
                    else dict(_MISSING),
                    **_fields(ref, ("bytes", "sha256")),
                }
            )
        if isinstance(artifacts, list) and len(artifacts) > 12:
            entry["earlier_phase_metadata_omitted"] = True
        runtime = state.get("runtime") if isinstance(state, dict) else None
        operation = state.get("operation") if isinstance(state, dict) else None
        entry["runtime_status"] = _fields(
            runtime, ("lifecycle", "mode", "controller", "tainted", "pending_request")
        )
        entry["runtime_run"] = _field(runtime, "run_id", identity=True)
        entry["operation_status"] = _field(operation, "status")
        entry["operation_id"] = _field(operation, "id", identity=True)
        budget = runtime.get("autonomy_budget") if isinstance(runtime, dict) else None
        entry["runtime_budget"] = _fields(
            budget, ("state", "exhausted_reason", "ended_reason", "remaining_ms")
        )
        entry["originals"] = {}
        for name, project in (
            ("request", _request),
            ("original_request", _request),
            ("finish", _finish),
            ("evaluation", _evaluation),
            ("actor_binding_observations", _actor_binding_observations),
        ):
            facts, original = _original(root, obj.get(name))
            entry["originals"][name] = {
                "file": facts,
                "facts": project(original)
                if original is not None
                else {"availability": "unavailable"},
            }
        value["episodes"].append(entry)
    if isinstance(rows, list) and len(rows) > 20:
        value["extra_rows_omitted"] = True
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode()) > MAX_DIAGNOSTIC_BYTES:
        for entry in value["episodes"]:
            for original in entry["originals"].values():
                original["facts"] = {"availability": "omitted_for_size"}
            entry.pop("saved_phases", None)
        value["original_detail_omitted_for_size"] = True
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode()) > MAX_DIAGNOSTIC_BYTES:
        raise ValueError("diagnostic_projection_over_limit")
    return raw


def retain_report_diagnostic(report: Any, root: Path, user_properties: Any) -> str:
    """Diagnostic failures cannot replace the parent's original assertion."""
    try:
        message = report_diagnostic(report, root)
    except Exception as error:
        message = json.dumps(
            {
                "schema": "r0-test-report-diagnostic-1",
                "diagnostic_error": type(error).__name__,
                "status": _field(report, "status"),
                "stop_reason": _field(report, "stop_reason"),
            },
            sort_keys=True,
        )
    with suppress(Exception):
        user_properties.append(("r0_suite_diagnostic", message))
    return message
