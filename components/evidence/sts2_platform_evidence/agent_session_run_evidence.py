"""Typed verification of native Agent Session operational evidence.

Acquisition witnesses bind the producer's public input metadata. They are not
capture archives, native causality proofs, or a replay of numerical Model state.
Opaque state is verified by inventory, hash and durable consumption metadata;
this component never decodes a Model codec or opens the Agent artifact path.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent_run_evidence import (
    AgentRunEvidenceError,
    _canonical_json,
    _file_entry,
    _load_json_object_bytes,
    _read_checksums,
)
from .core import VerificationFinding, VerificationResult, VerifierDescriptor

RUN_SCHEMA = "sts2.policy-runtime/agent-session-run-1"
EVENT_SCHEMA = "sts2.policy-runtime/agent-session-event-1"
MANIFEST_SCHEMA = "sts2.policy-runtime/agent-manifest-1"
ATTESTATION_SCHEMA = "sts2.policy-runtime/agent-session-adapter-attestation-1"
TYPE_ID = "policy-runtime-agent-session-run"
PROFILE = "native-logical-v1"
PROTOCOL = "sts2.policy-runtime/agent-session-ndjson-1"
SCOPE = ["persistent", "interaction", "referents", "catalog"]
_SHA = re.compile(r"^[0-9a-f]{64}$")
_INDEX = re.compile(r"^(?:0|[1-9][0-9]*)$")
_STATE_FILE = re.compile(r"^agent-state-([0-9a-f]{64})\.(bin|json)$")
_BASE_FILES = {"adapter-attestation.json", "agent-manifest.json", "events.jsonl", "manifest.json"}
_EXTRA_FILES = {"checksums.sha256", "evidence-manifest.json"}
_CONTEXT = {"session_id", "recovery_epoch"}
_EVENT_FIELDS = {
    "native_session_attached": {"subscription", "environment"},
    "native_event_received": {"original", "received_cursor"},
    "native_acquisition_registered": {"witness"},
    "agent_consumed": {"report", "acknowledgement", "witness"},
    "agent_directive": {"output"},
    "native_submission_requested": {
        "request_id",
        "basis_acquisition_id",
        "snapshot_id",
        "action_id",
        "catalog_digest",
        "run_id",
        "runtime_instance_id",
    },
    "native_submission_not_started": {"request_id", "submission_epoch", "reason"},
    "native_result": {"result"},
    "native_request_pending": {"original"},
    "native_request_reconciled": {"original", "resolution", "result"},
    "native_request_unresolved": {"original", "reason"},
    "native_await_result": {"wait_id", "after_cursor", "result"},
    "native_gap": {"gap"},
    "controller_acquired": {"controller"},
    "controller_released": {"controller"},
    "controller_release_unknown": {"controller", "reason"},
    "mode_changed": {"mode", "autonomy_budget", "controller"},
    "autonomy_budget_exhausted": {"reason", "autonomy_budget", "controller"},
    "handoff_to_human": {"reason", "autonomy_budget", "controller"},
    "fail_closed": {"reason", "agent_state"},
    "runtime_tainted": {"reason", "retry"},
    "agent_state_stored": {"metadata", "path", "metadata_path", "bytes", "sha256"},
    "agent_state_restored": {"metadata"},
    "stopped": {"autonomy_budget", "controller", "pending_request", "agent_state"},
}
_PENDING_FIELDS = {
    "request_id",
    "run_id",
    "runtime_instance_id",
    "session_id",
    "submission_epoch",
    "basis_acquisition_id",
    "snapshot_id",
    "action_id",
    "status",
    "reason",
}


def _require(condition: bool, code: str, detail: str = "") -> None:
    if not condition:
        raise AgentRunEvidenceError(code, detail or code)


def _object(value: object, fields: set[str] | None = None) -> dict[str, Any]:
    _require(isinstance(value, dict), "native_session_object_required")
    assert isinstance(value, dict)
    if fields is not None:
        _require(set(value) == fields, "native_session_fields", "unknown or missing typed fields")
    return value


def _text(value: object, *, maximum: int = 65536, allow_empty: bool = False) -> str:
    _require(isinstance(value, str) and (allow_empty or bool(value)), "native_session_text")
    assert isinstance(value, str)
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise AgentRunEvidenceError(
            "native_session_scalar", "unpaired Unicode surrogate"
        ) from error
    _require(size <= maximum, "native_session_text_size")
    return value


def _digest(value: object) -> str:
    text = _text(value, maximum=64)
    _require(_SHA.fullmatch(text) is not None, "native_session_digest")
    return text


def _integer(value: object, *, positive: bool = False, maximum: int = (1 << 53) - 1) -> int:
    _require(
        type(value) is int and (1 if positive else 0) <= value <= maximum, "native_session_integer"
    )
    assert isinstance(value, int)
    return value


def _boolean(value: object) -> bool:
    _require(type(value) is bool, "native_session_boolean")
    assert isinstance(value, bool)
    return value


def _nullable_text(value: object, *, maximum: int = 65536) -> None:
    if value is not None:
        _text(value, maximum=maximum)


def _strings(
    value: object, *, scope: bool = False, nonempty: bool = False, maximum: int = 65536
) -> list[str]:
    _require(isinstance(value, list) and (not nonempty or bool(value)), "native_session_array")
    assert isinstance(value, list)
    result = [_text(item, maximum=maximum) for item in value]
    _require(len(set(result)) == len(result), "native_session_duplicate")
    if scope:
        _require(result == [field for field in SCOPE if field in result], "native_session_scope")
    return result


def _index(value: object, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    text = _text(value, maximum=20)
    _require(
        _INDEX.fullmatch(text) is not None and int(text) <= (1 << 64) - 1,
        "native_session_source_index",
    )
    return int(text)


def _time(value: object) -> datetime:
    text = _text(value)
    _require("T" in text, "native_session_timestamp")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None, "native_session_timestamp")
    return parsed


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(raw: bytes, label: str) -> dict[str, Any]:
    return _load_json_object_bytes(raw, label)


def _input_spec(value: object) -> dict[str, Any]:
    result = _object(value, {"id", "version", "sha256"})
    _text(result["id"], maximum=256)
    _text(result["version"], maximum=256)
    _digest(result["sha256"])
    return result


def _adapter(value: object) -> dict[str, Any]:
    result = _object(value, {"id", "version", "protocol", "code_sha256"})
    _text(result["id"], maximum=256)
    _text(result["version"], maximum=256)
    _require(result["protocol"] == PROTOCOL, "native_session_adapter_protocol")
    _digest(result["code_sha256"])
    return result


def _environment(value: object) -> dict[str, Any]:
    result = _object(
        value,
        {
            "host_kind",
            "connector_version",
            "connector_source_revision",
            "connector_artifact_sha256",
            "connector_module_version_id",
            "modset_status",
            "modset_fingerprint",
            "loaded_mod_ids",
        },
    )
    _require(
        result["host_kind"] in {"live_ui", "headless", "replay", "test"}, "native_session_host_kind"
    )
    for key in (
        "connector_version",
        "connector_source_revision",
        "connector_module_version_id",
        "modset_status",
    ):
        _text(result[key], maximum=256)
    for key in ("connector_artifact_sha256", "modset_fingerprint"):
        _digest(result[key])
    _strings(result["loaded_mod_ids"], maximum=256)
    return result


def _agent_manifest(value: object) -> dict[str, Any]:
    result = _object(
        value,
        {
            "schema",
            "manifest_id",
            "agent",
            "adapter",
            "artifact",
            "input",
            "requirements",
            "support",
            "limits",
            "claims",
        },
    )
    _require(result["schema"] == MANIFEST_SCHEMA, "native_session_manifest_schema")
    _text(result["manifest_id"], maximum=256)
    agent = _object(result["agent"], {"id", "version", "provider", "architecture"})
    for item in agent.values():
        _text(item, maximum=256)
    _adapter(result["adapter"])
    artifact = _object(result["artifact"], {"id", "path", "sha256"})
    _text(artifact["id"], maximum=256)
    _text(artifact["path"], maximum=4096)
    _digest(artifact["sha256"])
    input_ = _object(
        result["input"],
        {
            "profile",
            "input_spec",
            "projection",
            "state_format_version",
            "state_recovery",
            "history_mode",
            "consumption_mode",
            "gap_policy",
            "attachment",
        },
    )
    _require(input_["profile"] == PROFILE, "native_session_profile")
    _input_spec(input_["input_spec"])
    projection = _object(input_["projection"], {"id", "version"})
    _text(projection["id"], maximum=256)
    _text(projection["version"], maximum=256)
    _text(input_["state_format_version"], maximum=256)
    _require(
        input_["history_mode"] in {"full_reference", "scoped_query"}
        and input_["consumption_mode"] in {"once_per_occurrence", "incremental_view"}
        and input_["gap_policy"] in {"handoff", "explicit_reset"},
        "native_session_input_modes",
    )
    recovery = _object(input_["state_recovery"], {"mode", "max_state_bytes", "model_bindings"})
    _require(recovery["mode"] in {"opaque", "none"}, "native_session_state_mode")
    _integer(
        recovery["max_state_bytes"],
        positive=recovery["mode"] == "opaque",
        maximum=16 * 1024 * 1024,
    )
    _require(
        isinstance(recovery["model_bindings"], list) and len(recovery["model_bindings"]) <= 16,
        "native_session_model_bindings",
    )
    model_ids = set()
    for binding in recovery["model_bindings"]:
        binding = _object(binding, {"model_id", "weights_sha256"})
        model_id = _text(binding["model_id"], maximum=256)
        _require(model_id not in model_ids, "native_session_duplicate_model")
        model_ids.add(model_id)
        _digest(binding["weights_sha256"])
    if recovery["mode"] == "none":
        _require(recovery["max_state_bytes"] == 0 and not model_ids, "native_session_stateless")
    attachment = _object(input_["attachment"], {"eager_scope", "required_seams", "delivery_mode"})
    scope = _strings(attachment["eager_scope"], scope=True)
    _require(
        attachment["delivery_mode"] in {"full_reference", "scoped"}, "native_session_delivery_mode"
    )
    _require(
        (input_["history_mode"] == "full_reference")
        == (attachment["delivery_mode"] == "full_reference"),
        "native_session_history_delivery",
    )
    if input_["history_mode"] == "full_reference":
        _require(
            scope == SCOPE
            and attachment["delivery_mode"] == "full_reference"
            and input_["consumption_mode"] == "once_per_occurrence",
            "native_session_full_reference",
        )
    seams = attachment["required_seams"]
    _require(isinstance(seams, list) and 0 < len(seams) <= 256, "native_session_required_seams")
    seam_ids = set()
    for seam in seams:
        seam = _object(seam, {"source_seam", "version", "coverage"})
        seam_id = _text(seam["source_seam"], maximum=128)
        _text(seam["version"], maximum=128)
        _require(
            seam_id not in seam_ids
            and seam["coverage"] in {"complete_at_seam", "sampled", "unsupported"},
            "native_session_seam",
        )
        seam_ids.add(seam_id)
    if input_["history_mode"] == "full_reference":
        _require(
            all(seam["coverage"] == "complete_at_seam" for seam in seams),
            "native_session_full_reference_seams",
        )
    requirements = _object(
        result["requirements"], {"connector_protocol_version", "environment", "required_methods"}
    )
    _text(requirements["connector_protocol_version"], maximum=256)
    _environment(requirements["environment"])
    methods = _strings(requirements["required_methods"], nonempty=True, maximum=256)
    allowed = {
        "capabilities",
        "attach",
        "current",
        "read",
        "catalog",
        "resolve",
        "submit",
        "result",
        "events",
        "await",
        "cancel_wait",
        "detach",
        "renew",
        "retain",
        "release",
    }
    minimum = {
        "capabilities",
        "attach",
        "events",
        "await",
        "cancel_wait",
        "detach",
        "submit",
        "result",
        "renew",
    }
    if input_["history_mode"] == "full_reference":
        minimum.update({"read", "catalog", "retain", "release"})
    elif "current" in methods:
        minimum.update({"read", "retain", "release"})
    _require(set(methods) <= allowed and minimum <= set(methods), "native_session_methods")
    support = _object(
        result["support"], {"game_versions", "game_commits", "interaction_kinds", "action_verbs"}
    )
    for key, item in support.items():
        declared = _strings(item, nonempty=True, maximum=256)
        _require(
            "*" not in declared
            or key in {"interaction_kinds", "action_verbs"}
            and declared == ["*"],
            "native_session_support_wildcard",
        )
    maxima = {
        "max_message_bytes": 96 * 1024 * 1024,
        "max_acquisitions": 256,
        "max_retained_acquisition_bytes": 256 * 1024 * 1024,
        "max_pending_queries": 8,
        "max_queries_per_turn": 64,
        "max_query_bytes_per_turn": 128 * 1024 * 1024,
        "max_capture_bytes": 64 * 1024 * 1024,
        "max_catalog_actions": 65536,
        "max_cancelled_ids": 256,
        "agent_timeout_ms": 30000,
    }
    limits = _object(result["limits"], set(maxima))
    for key, maximum in maxima.items():
        _integer(limits[key], positive=True, maximum=maximum)
    claims = _object(
        result["claims"],
        {
            "catalog_filtered",
            "creates_action_authority",
            "creates_native_operands",
            "human_origin",
            "causal_successor",
        },
    )
    _require(
        all(type(item) is bool and item is False for item in claims.values()),
        "native_session_claims",
    )
    return result


def _prefix(value: object, agent: Mapping[str, Any], continuity: str) -> dict[str, Any]:
    prefix = _object(
        value,
        {
            "continuity_token",
            "history_mode",
            "consumption_mode",
            "received_cursor",
            "consumed_publication_index",
            "omissions",
        },
    )
    _require(
        prefix["continuity_token"] == continuity
        and prefix["history_mode"] == agent["input"]["history_mode"]
        and prefix["consumption_mode"] == agent["input"]["consumption_mode"],
        "native_session_prefix_binding",
    )
    _nullable_text(prefix["received_cursor"])
    _index(prefix["consumed_publication_index"], nullable=True)
    omissions = _object(prefix["omissions"], {"received_unconsumed_count", "missing_scopes", "gap"})
    if omissions["received_unconsumed_count"] is not None:
        _integer(omissions["received_unconsumed_count"])
    _strings(omissions["missing_scopes"], scope=True)
    if omissions["gap"] is not None:
        _object(omissions["gap"])
    return prefix


class _DeclaredConsumptionStream:
    """Check the owner's declared consumption metadata, without decoding input or W.

    The unit and prefix rules match AgentConsumptionLedger. Witnesses do not
    contain public field bytes, so this checks their association and transitions,
    never their native/content coherence or numerical memory interpretation.
    """

    def __init__(self, agent: Mapping[str, Any]) -> None:
        self.agent = agent
        self.continuity: str | None = None
        self.occurrence: tuple[Any, ...] | None = None
        self.revision = -1
        self.scopes: set[str] = set()
        self.consumption_ids: set[str] = set()
        self.last_report: dict[str, Any] | None = None
        self.last_publication = -1
        self.consumed_publication: str | None = None
        self.received_unconsumed = 0
        self.gap: dict[str, Any] | None = None

    def received(self) -> None:
        # All source publication entries are views, including terminal and
        # unavailable entries. Their kind does not declare a task completion.
        self.received_unconsumed += 1

    def record_gap(self, value: dict[str, Any]) -> None:
        self.gap = value

    def watermark(self, output: Mapping[str, Any]) -> None:
        token = _text(output["continuity_token"], maximum=256)
        if self.continuity is None:
            self.continuity = token
        _require(token == self.continuity, "native_session_continuity_changed")
        if self.last_report is None:
            _require(
                output["consumption_id"] is None and output["state_version"] == 0,
                "native_session_directive_without_consumption",
            )
        else:
            _require(
                all(
                    output[key] == self.last_report[key]
                    for key in ("continuity_token", "consumption_id", "state_version")
                ),
                "native_session_directive_prefix",
            )

    def accept(self, report: dict[str, Any], ack: dict[str, Any], witness: dict[str, Any]) -> None:
        token = report["continuity_token"]
        if self.continuity is None:
            self.continuity = token
        _require(token == self.continuity, "native_session_continuity_changed")
        previous = self.last_report
        _require(
            report["previous_consumption_id"]
            == (None if previous is None else previous["consumption_id"]),
            "native_session_consumption_prefix",
        )
        owner = witness["owner_occurrence"]
        occurrence = (
            witness["capture"]["stream_generation"],
            witness["snapshot_id"],
            owner["occurrence_id"],
            owner["binding_revision"],
            owner["focus_occurrence"],
        )
        new_occurrence = occurrence != self.occurrence
        revision = witness["revision"]
        _require(
            revision >= self.revision and (not new_occurrence or revision > self.revision),
            "native_session_consumption_revision",
        )
        added = set(witness["included"]) - self.scopes
        advanced = new_occurrence or (
            self.agent["input"]["consumption_mode"] == "incremental_view" and bool(added)
        )
        version = 0 if previous is None else previous["state_version"]
        _require(
            report["advanced"] == advanced and report["state_version"] == version + int(advanced),
            "native_session_consumption_unit",
        )
        identity = report["consumption_id"]
        _require(
            (advanced and identity not in self.consumption_ids)
            or (not advanced and previous is not None and identity == previous["consumption_id"]),
            "native_session_consumption_identity",
        )
        publication = _index(witness["publication_index"], nullable=True)
        new_publication = publication is not None and publication > self.last_publication
        if self.agent["input"]["history_mode"] == "full_reference":
            _require(
                self.gap is None
                and not (publication is None and advanced)
                and (publication is None or publication >= self.last_publication),
                "native_session_consumption_publication",
            )
        if advanced:
            if new_occurrence:
                self.scopes.clear()
            self.scopes.update(witness["included"])
            self.occurrence, self.revision = occurrence, revision
            self.consumption_ids.add(identity)
        if advanced or new_publication:
            self.consumed_publication = witness["publication_index"]
        if new_publication:
            assert publication is not None
            self.last_publication = publication
            self.received_unconsumed = max(0, self.received_unconsumed - 1)
        prefix = ack["prefix"]
        omissions = prefix["omissions"]
        _require(
            prefix["consumed_publication_index"] == self.consumed_publication
            and omissions["missing_scopes"] == witness["missing"]
            and omissions["gap"] == self.gap
            and omissions["received_unconsumed_count"]
            == (self.received_unconsumed if self.gap is None else None),
            "native_session_acknowledged_prefix",
        )
        self.last_report = report


def _capture(value: object) -> dict[str, Any]:
    capture = _object(
        value,
        {
            "schema",
            "capture_id",
            "snapshot_id",
            "input_profile",
            "session",
            "stream_generation",
            "scope_id",
            "capture_ordinal",
            "captured_at",
            "expires_at",
            "byte_count",
            "sha256",
            "read_cursor",
        },
    )
    _require(
        capture["schema"] == "sts2.player-environment/native-logical-capture-1"
        and capture["input_profile"] == PROFILE,
        "native_session_capture_namespace",
    )
    for key in ("capture_id", "snapshot_id", "stream_generation", "scope_id", "read_cursor"):
        _text(capture[key], maximum=1024)
    session = _object(capture["session"], {"runtime_instance_id", "environment_fingerprint"})
    _text(session["runtime_instance_id"], maximum=128)
    _text(session["environment_fingerprint"])
    _index(capture["capture_ordinal"])
    _require(
        _time(capture["expires_at"]) > _time(capture["captured_at"]), "native_session_capture_time"
    )
    _integer(capture["byte_count"], positive=True, maximum=64 * 1024 * 1024)
    _digest(capture["sha256"])
    return capture


def _witness(value: object) -> dict[str, Any]:
    witness = _object(
        value,
        {
            "acquisition_id",
            "capture",
            "publication_index",
            "snapshot_id",
            "revision",
            "owner_occurrence",
            "status",
            "included",
            "missing",
            "catalog_digest",
            "catalog_count",
            "catalog_materialized",
        },
    )
    _text(witness["acquisition_id"], maximum=256)
    capture = _capture(witness["capture"])
    _require(witness["snapshot_id"] == capture["snapshot_id"], "native_session_capture_snapshot")
    _index(witness["publication_index"], nullable=True)
    _integer(witness["revision"])
    _require(
        witness["status"] in {"interactive", "settling", "observed", "terminal"},
        "native_session_observation_status",
    )
    owner = _object(
        witness["owner_occurrence"],
        {"owner_id", "occurrence_id", "binding_revision", "focus_referent_id", "focus_occurrence"},
    )
    for key in ("owner_id", "occurrence_id", "binding_revision"):
        _text(owner[key])
    _nullable_text(owner["focus_referent_id"])
    _nullable_text(owner["focus_occurrence"])
    included = _strings(witness["included"], scope=True)
    missing = _strings(witness["missing"], scope=True)
    _require(
        not set(included) & set(missing) and set(included) | set(missing) == set(SCOPE),
        "native_session_scope_overlap",
    )
    _boolean(witness["catalog_materialized"])
    if "catalog" in included:
        _digest(witness["catalog_digest"])
        _integer(witness["catalog_count"], maximum=65536)
    else:
        _require(
            witness["catalog_digest"] is None
            and witness["catalog_count"] is None
            and witness["catalog_materialized"] is False,
            "native_session_catalog_omitted",
        )
    return witness


def _budget(value: object) -> dict[str, Any]:
    budget = _object(
        value,
        {
            "max_submissions",
            "submissions_used",
            "max_policy_calls",
            "policy_calls_used",
            "deadline_ms",
            "elapsed_ms",
            "remaining_ms",
            "state",
            "exhausted_reason",
            "ended_reason",
        },
    )
    for key in ("max_submissions", "max_policy_calls", "deadline_ms"):
        _integer(budget[key], positive=True)
    for key in ("submissions_used", "policy_calls_used", "elapsed_ms", "remaining_ms"):
        _integer(budget[key])
    _require(budget["state"] in {"inactive", "active", "exhausted"}, "native_session_budget_state")
    _nullable_text(budget["exhausted_reason"])
    _nullable_text(budget["ended_reason"])
    return budget


def _pending(
    value: object, run_id: str, session_id: str, submissions: Mapping[str, Any]
) -> dict[str, Any]:
    original = _object(value, _PENDING_FIELDS)
    for key in (
        "request_id",
        "run_id",
        "runtime_instance_id",
        "session_id",
        "basis_acquisition_id",
        "snapshot_id",
        "action_id",
    ):
        _text(original[key], maximum=256)
    _integer(original["submission_epoch"])
    _require(original["status"] in {"pending", "unresolved"}, "native_session_pending_status")
    _nullable_text(original["reason"])
    _require(
        original["run_id"] == run_id and original["session_id"] == session_id,
        "native_session_pending_owner",
    )
    attempt = submissions.get(original["request_id"])
    _require(isinstance(attempt, dict), "native_session_pending_without_submission")
    assert isinstance(attempt, dict)
    _require(
        all(
            original[key] == attempt[key]
            for key in (
                "request_id",
                "run_id",
                "runtime_instance_id",
                "basis_acquisition_id",
                "snapshot_id",
                "action_id",
                "session_id",
            )
        )
        and original["submission_epoch"] == attempt["recovery_epoch"],
        "native_session_pending_basis",
    )
    return original


def _action(value: object) -> dict[str, Any]:
    action = _object(
        value,
        {"action_id", "kind", "verb", "label", "subject_referent_id", "arguments", "effect_domain"},
    )
    _require(action["kind"] == "native_input", "native_session_action_kind")
    for key in ("action_id", "verb", "label", "effect_domain"):
        _text(action[key])
    _nullable_text(action["subject_referent_id"])
    _require(isinstance(action["arguments"], list), "native_session_action_arguments")
    roles = set()
    for arg in action["arguments"]:
        arg = _object(arg, {"role", "referent_id"})
        role = _text(arg["role"])
        _require(role not in roles, "native_session_argument_role")
        roles.add(role)
        _text(arg["referent_id"])
    return action


def _result(value: object, attempt: Mapping[str, Any]) -> dict[str, Any]:
    result = _object(
        value,
        {
            "protocol_version",
            "schema",
            "input_profile",
            "request_id",
            "snapshot_id",
            "action",
            "delivery",
            "execution",
            "effect",
            "cancel",
            "stages",
            "reason",
            "retry",
            "observed_frame",
            "attribution",
        },
    )
    _require(
        result["protocol_version"] == "1.0.0"
        and result["schema"] == "sts2.player-environment/native-logical-result-1"
        and result["input_profile"] == PROFILE
        and result["retry"] == "never_automatic",
        "native_session_result_namespace",
    )
    _require(
        result["request_id"] == attempt["request_id"]
        and result["snapshot_id"] == attempt["snapshot_id"],
        "native_session_result_request",
    )
    if result["action"] is not None:
        _require(
            _action(result["action"])["action_id"] == attempt["action_id"],
            "native_session_result_action",
        )
    _require(
        result["delivery"]
        in {"not_started", "rejected_before_input", "delivered", "partially_delivered", "unknown"}
        and result["execution"] in {"not_started", "native_accepted", "native_rejected", "unknown"}
        and result["effect"] in {"not_observed", "pending", "observed", "unknown"}
        and result["cancel"] in {"not_requested", "cancelled_before_start", "too_late", "unknown"},
        "native_session_result_disposition",
    )
    _nullable_text(result["reason"])
    _require(
        isinstance(result["stages"], list) and len(result["stages"]) <= 16,
        "native_session_result_stages",
    )
    for stage in result["stages"]:
        stage = _object(stage, {"stage", "delivery", "evidence"})
        _text(stage["stage"], maximum=128)
        _text(stage["evidence"], maximum=128)
        _require(
            stage["delivery"]
            in {
                "not_started",
                "rejected_before_input",
                "delivered",
                "partially_delivered",
                "unknown",
            },
            "native_session_stage_delivery",
        )
    if result["attribution"] is not None:
        attribution = _object(
            result["attribution"],
            {
                "runtime_instance_id",
                "client_session_id",
                "client_instance_id",
                "product_id",
                "product_name",
                "product_version",
                "controller_lease_id",
                "controller_generation",
            },
        )
        for key in set(attribution) - {"controller_generation"}:
            _text(attribution[key])
        _integer(attribution["controller_generation"], positive=True)
        _require(
            attribution["runtime_instance_id"] == attempt["runtime_instance_id"],
            "native_session_result_attribution",
        )
    if result["observed_frame"] is not None:
        frame = _object(
            result["observed_frame"],
            {
                "schema",
                "input_profile",
                "observation_ref",
                "capture_ref",
                "game_continuity_id",
                "stream_generation",
                "publication_cursor",
            },
        )
        _require(
            frame["schema"] == "sts2.player-environment/native-logical-context-1"
            and frame["input_profile"] == PROFILE,
            "native_session_result_context",
        )
        for key in ("observation_ref", "capture_ref", "stream_generation"):
            _text(frame[key])
        _nullable_text(frame["game_continuity_id"])
        _nullable_text(frame["publication_cursor"])
    return result


@dataclass(frozen=True)
class AgentSessionRunEvidence:
    directory: Path
    manifest: Mapping[str, Any]
    agent_manifest: Mapping[str, Any]
    evidence_manifest: Mapping[str, Any]
    event_count: int
    content_id: str

    @property
    def run_id(self) -> str:
        return str(self.manifest["run_id"])


DESCRIPTOR = VerifierDescriptor(TYPE_ID, RUN_SCHEMA, 1, AgentSessionRunEvidence)


class AgentSessionRunEvidenceVerifier:
    descriptor = DESCRIPTOR

    def verify(
        self, source: str | Path, expected: Mapping[str, object] | None = None
    ) -> VerificationResult[AgentSessionRunEvidence]:
        directory = Path(source)
        try:
            _require(not directory.is_symlink() and directory.is_dir(), "native_session_directory")
            value = self._verify(directory.resolve(), expected)
            return VerificationResult(self.descriptor, "pass", directory, value)
        except AgentRunEvidenceError as error:
            return VerificationResult(
                self.descriptor,
                "fail",
                directory,
                findings=(VerificationFinding(error.code, str(error), error.path),),
            )
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
            return VerificationResult(
                self.descriptor,
                "fail",
                directory,
                findings=(VerificationFinding("malformed_native_session", str(error)),),
            )

    def _verify(
        self, directory: Path, expected: Mapping[str, object] | None
    ) -> AgentSessionRunEvidence:
        entries = list(directory.iterdir())
        _require(
            all(path.is_file() and not path.is_symlink() for path in entries),
            "native_session_inventory",
        )
        names = {path.name for path in entries}
        state_files = names - _BASE_FILES - _EXTRA_FILES
        _require(
            names >= _BASE_FILES | _EXTRA_FILES
            and len(state_files) <= 256
            and all(_STATE_FILE.fullmatch(name) for name in state_files),
            "native_session_inventory",
        )
        _require(
            (directory / "checksums.sha256").stat().st_size <= 256 * 1024
            and (directory / "events.jsonl").stat().st_size <= 256 * 1024 * 1024
            and all(
                (directory / name).stat().st_size <= 1024 * 1024
                for name in names - state_files - {"events.jsonl"}
            )
            and all(
                (directory / name).stat().st_size
                <= (64 * 1024 * 1024 if name.endswith(".bin") else 64 * 1024)
                for name in state_files
            )
            and sum(
                (directory / name).stat().st_size for name in state_files if name.endswith(".bin")
            )
            <= 256 * 1024 * 1024,
            "native_session_file_capacity",
        )
        for name in state_files:
            stem, extension = name.rsplit(".", 1)
            _require(
                stem + (".json" if extension == "bin" else ".bin") in state_files,
                "native_session_state_pair",
            )
        checksums = _read_checksums(directory / "checksums.sha256")
        _require(
            set(checksums) == names - {"checksums.sha256"}, "native_session_checksum_inventory"
        )
        for name, expected_sha in checksums.items():
            _require(
                _sha((directory / name).read_bytes()) == expected_sha, "native_session_checksum"
            )
        run = _object(
            _json((directory / "manifest.json").read_bytes(), "manifest.json"),
            {
                "schema",
                "run_id",
                "manifest_id",
                "agent_manifest_sha256",
                "agent_id",
                "agent_version",
                "agent_artifact_sha256",
                "runtime_version",
                "runtime_code_sha256",
                "started_at",
                "ended_at",
                "status",
                "mode",
                "tainted",
                "append_only",
            },
        )
        _require(run["schema"] == RUN_SCHEMA, "native_session_run_namespace")
        for key in ("run_id", "manifest_id", "agent_id", "agent_version", "runtime_version"):
            _text(run[key], maximum=256)
        for key in ("agent_manifest_sha256", "agent_artifact_sha256", "runtime_code_sha256"):
            _digest(run[key])
        _require(_time(run["ended_at"]) >= _time(run["started_at"]), "native_session_run_time")
        _require(
            run["status"] in {"completed", "stopped", "tainted"}
            and run["mode"] in {"human", "shadow", "one_step", "auto"}
            and run["append_only"] is True,
            "native_session_run_finalized",
        )
        _boolean(run["tainted"])
        _require(run["status"] != "tainted" or run["tainted"], "native_session_taint")
        if expected is not None:
            aliases = {"agent_manifest_id": "manifest_id"}
            _require(
                expected.get("schema", "sts2.policy-runtime/agent-session-startup-1")
                == "sts2.policy-runtime/agent-session-startup-1",
                "native_session_expected_namespace",
            )
            for key in (
                "run_id",
                "agent_manifest_id",
                "agent_id",
                "agent_version",
                "agent_manifest_sha256",
                "agent_artifact_sha256",
                "runtime_version",
                "runtime_code_sha256",
            ):
                if key in expected:
                    _require(
                        expected[key] == run[aliases.get(key, key)],
                        "native_session_expected_identity",
                    )
        agent = _agent_manifest(
            _json((directory / "agent-manifest.json").read_bytes(), "agent-manifest.json")
        )
        _require(
            _sha(_canonical_json(agent).encode("utf-8")) == run["agent_manifest_sha256"]
            and agent["manifest_id"] == run["manifest_id"]
            and agent["agent"]["id"] == run["agent_id"]
            and agent["agent"]["version"] == run["agent_version"]
            and agent["artifact"]["sha256"] == run["agent_artifact_sha256"],
            "native_session_agent_binding",
        )
        attestation = _object(
            _json(
                (directory / "adapter-attestation.json").read_bytes(), "adapter-attestation.json"
            ),
            {
                "schema",
                "run_id",
                "manifest_id",
                "agent_manifest_sha256",
                "status",
                "expected",
                "actual",
                "attested_at",
            },
        )
        _require(
            attestation["schema"] == ATTESTATION_SCHEMA
            and all(
                attestation[key] == run[key]
                for key in ("run_id", "manifest_id", "agent_manifest_sha256")
            )
            and _adapter(attestation["expected"]) == agent["adapter"],
            "native_session_adapter_binding",
        )
        if attestation["status"] == "attested":
            _require(
                _adapter(attestation["actual"]) == agent["adapter"], "native_session_adapter_actual"
            )
            _time(attestation["attested_at"])
        else:
            _require(
                attestation["status"] == "not_attested"
                and attestation["actual"] is None
                and attestation["attested_at"] is None,
                "native_session_adapter_status",
            )
        if expected is not None and "adapter" in expected:
            _require(expected["adapter"] == agent["adapter"], "native_session_expected_adapter")
        immutable = _object(
            _json((directory / "evidence-manifest.json").read_bytes(), "evidence-manifest.json"),
            {"schema", "run_id", "complete", "append_only", "files", "manifest_sha256"},
        )
        _require(isinstance(immutable["files"], list), "native_session_immutable_files")
        for entry in immutable["files"]:
            entry = _object(entry, {"path", "bytes", "sha256"})
            _text(entry["path"])
            _integer(entry["bytes"])
            _digest(entry["sha256"])
        expected_entries = [
            _file_entry(directory / name, name) for name in sorted(_BASE_FILES | state_files)
        ]
        _require(
            immutable["schema"] == "sts2.policy-runtime/immutable-evidence-manifest-1"
            and immutable["run_id"] == run["run_id"]
            and immutable["complete"] is True
            and immutable["append_only"] is True
            and immutable["files"] == expected_entries
            and immutable["manifest_sha256"]
            == _sha(
                _canonical_json({"run_id": run["run_id"], "files": expected_entries}).encode(
                    "utf-8"
                )
            ),
            "native_session_immutable_manifest",
        )
        events = self._events(
            directory, run, agent, attestation["status"] == "attested", state_files
        )
        identity = {
            "schema": "sts2.evidence/store-directory-1",
            "files": [_file_entry(directory / name, name) for name in sorted(names)],
        }
        return AgentSessionRunEvidence(
            directory,
            run,
            agent,
            immutable,
            len(events),
            _sha(_canonical_json(identity).encode("utf-8")),
        )

    def _events(
        self,
        directory: Path,
        run: dict[str, Any],
        agent: dict[str, Any],
        attested: bool,
        state_files: set[str],
    ) -> list[dict[str, Any]]:
        # The executable producer's exported AgentRuntimeEventPayloads map is closed.
        raw = (directory / "events.jsonl").read_bytes()
        _require(not raw or raw.endswith(b"\n"), "native_session_event_termination")
        events = [_json(line, "events.jsonl") for line in raw.splitlines()]
        session_id: str | None = None
        epoch = 0
        witnesses: dict[str, dict[str, Any]] = {}
        submissions: dict[str, dict[str, Any]] = {}
        completed: set[str] = set()
        outstanding: set[str] = set()
        pending: dict[str, Any] | None = None
        stored_files: set[str] = set()
        stored_metadata: list[dict[str, Any]] = []
        last_report: dict[str, Any] | None = None
        last_ack: dict[str, Any] | None = None
        last_witness: dict[str, Any] | None = None
        tainted = False
        controller = "released"
        mode: str | None = None  # A finalized manifest records final mode, not initial mode.
        stream = _DeclaredConsumptionStream(agent)
        last_act: dict[str, Any] | None = None
        last_act_epoch: int | None = None
        agent_uncertain = False
        attachment: tuple[dict[str, Any], dict[str, Any]] | None = None
        for sequence, event in enumerate(events, 1):
            event = _object(event, {"schema", "sequence", "recorded_at", "kind", "payload"})
            _require(
                event["schema"] == EVENT_SCHEMA
                and type(event["sequence"]) is int
                and event["sequence"] == sequence,
                "native_session_event_sequence",
            )
            _require(
                _time(run["started_at"]) <= _time(event["recorded_at"]) <= _time(run["ended_at"]),
                "native_session_event_time",
            )
            kind = _text(event["kind"], maximum=256)
            _require(kind in _EVENT_FIELDS, "native_session_event_kind")
            payload = _object(event["payload"], _CONTEXT | _EVENT_FIELDS[kind])
            current_session = _text(payload["session_id"], maximum=256)
            current_epoch = _integer(payload["recovery_epoch"])
            _require(
                session_id in {None, current_session} and current_epoch >= epoch,
                "native_session_context_order",
            )
            session_id, epoch = current_session, current_epoch
            _require(
                sequence == 1 or events[sequence - 2]["kind"] != "stopped",
                "native_session_event_after_stop",
            )
            if kind in {
                "agent_consumed",
                "agent_directive",
                "native_submission_requested",
                "agent_state_stored",
                "agent_state_restored",
            }:
                _require(attested, "native_session_adapter_not_attested")
            if kind == "native_session_attached":
                _require(attachment is None, "native_session_duplicate_attachment")
                attachment = self._attachment(payload, agent)
            elif kind == "native_acquisition_registered":
                witness = _witness(payload["witness"])
                _require(attachment is not None, "native_session_acquisition_before_attachment")
                assert attachment is not None
                subscription, environment = attachment
                capture = witness["capture"]
                _require(
                    capture["session"]["runtime_instance_id"] == environment["runtime_instance_id"]
                    and capture["session"]["environment_fingerprint"]
                    == environment["environment_fingerprint"]
                    and capture["stream_generation"] == subscription["stream_generation"],
                    "native_session_acquisition_environment",
                )
                _require(
                    witness["acquisition_id"] not in witnesses, "native_session_acquisition_reused"
                )
                _require(
                    witness["capture"]["byte_count"] <= agent["limits"]["max_capture_bytes"]
                    and (
                        witness["catalog_count"] is None
                        or witness["catalog_count"] <= agent["limits"]["max_catalog_actions"]
                    ),
                    "native_session_acquisition_limits",
                )
                witnesses[witness["acquisition_id"]] = witness
            elif kind == "agent_consumed":
                witness = _witness(payload["witness"])
                _require(
                    witnesses.get(witness["acquisition_id"]) == witness,
                    "native_session_consumption_witness",
                )
                if agent["input"]["history_mode"] == "full_reference":
                    _require(
                        witness["included"] == SCOPE
                        and not witness["missing"]
                        and witness["catalog_materialized"] is True,
                        "native_session_full_reference_consumption",
                    )
                report, ack = self._consumption(payload, agent, last_report)
                stream.accept(report, ack, witness)
                last_report, last_ack, last_witness = report, ack, witness
            elif kind == "agent_directive":
                output = self._directive(payload["output"], last_report, witnesses)
                stream.watermark(output)
                last_act = output if output["directive"]["type"] == "act" else None
                last_act_epoch = current_epoch if last_act is not None else None
            elif kind == "native_submission_requested":
                request_id = _text(payload["request_id"], maximum=128)
                basis = witnesses.get(payload["basis_acquisition_id"])
                _require(
                    request_id not in submissions
                    and not outstanding
                    and pending is None
                    and not tainted
                    and not agent_uncertain
                    and basis is not None
                    and controller == "held"
                    and mode in {None, "auto", "one_step"}
                    and payload["run_id"] == run["run_id"],
                    "native_session_submission_owner",
                )
                assert basis is not None
                _require(
                    last_report is not None
                    and last_act is not None
                    and last_act_epoch == current_epoch
                    and payload["basis_acquisition_id"] == last_report["acquisition_id"]
                    and payload["basis_acquisition_id"]
                    == last_act["directive"]["basis_acquisition_id"]
                    and (
                        last_act["directive"]["selection"]["kind"] == "expression"
                        or payload["action_id"] == last_act["directive"]["selection"]["action_id"]
                    ),
                    "native_session_submission_without_acknowledged_act",
                )
                for key in ("action_id", "runtime_instance_id"):
                    _text(payload[key])
                _require(
                    payload["snapshot_id"] == basis["snapshot_id"]
                    and payload["catalog_digest"] == basis["catalog_digest"]
                    and payload["runtime_instance_id"]
                    == basis["capture"]["session"]["runtime_instance_id"]
                    and basis["catalog_count"] is not None
                    and basis["catalog_count"] > 0
                    and (
                        agent["input"]["history_mode"] != "full_reference"
                        or basis["catalog_materialized"] is True
                    ),
                    "native_session_submission_basis",
                )
                submissions[request_id] = payload
                outstanding.add(request_id)
                last_act, last_act_epoch = None, None
            elif kind == "native_submission_not_started":
                request_id = _text(payload["request_id"], maximum=128)
                submission_epoch = _integer(payload["submission_epoch"])
                _text(payload["reason"])
                attempt = submissions.get(request_id)
                _require(
                    attempt is not None
                    and request_id in outstanding
                    and request_id not in completed
                    and pending is None
                    and submission_epoch == attempt["recovery_epoch"]
                    and submission_epoch <= current_epoch,
                    "native_session_not_started_original_intent",
                )
                outstanding.remove(request_id)
                completed.add(request_id)
            elif kind == "native_result":
                result = _object(payload["result"])
                attempt = submissions.get(_text(result.get("request_id"), maximum=128))
                _require(
                    attempt is not None
                    and result["request_id"] in outstanding
                    and result["request_id"] not in completed
                    and pending is None,
                    "native_session_result_without_submission",
                )
                assert attempt is not None
                result = _result(result, attempt)
                completed.add(result["request_id"])
                outstanding.remove(result["request_id"])
                tainted |= result["delivery"] in {"partially_delivered", "unknown"}
            elif kind == "native_request_pending":
                original = _pending(
                    payload["original"], run["run_id"], current_session, submissions
                )
                _require(
                    pending is None
                    and original["request_id"] in outstanding
                    and original["request_id"] not in completed
                    and original["status"] == "pending"
                    and original["reason"] is None,
                    "native_session_pending_duplicate",
                )
                pending = original
            elif kind in {"native_request_reconciled", "native_request_unresolved"}:
                original = _pending(
                    payload["original"], run["run_id"], current_session, submissions
                )
                _require(
                    pending == original and mode == "human", "native_session_reconcile_original"
                )
                if kind == "native_request_unresolved":
                    _text(payload["reason"])
                    pending = {**original, "status": "unresolved", "reason": payload["reason"]}
                else:
                    resolution = payload["resolution"]
                    _require(
                        resolution in {"pending", "resolved", "unresolved", "tainted"},
                        "native_session_reconcile_resolution",
                    )
                    if resolution in {"pending", "unresolved"}:
                        _require(payload["result"] is None, "native_session_pending_is_not_result")
                    else:
                        result = _result(payload["result"], submissions[original["request_id"]])
                        unknown = result["delivery"] in {"partially_delivered", "unknown"}
                        _require(
                            resolution in {"resolved", "tainted"}
                            and (resolution == "tainted") == unknown,
                            "native_session_reconcile_result",
                        )
                        tainted |= unknown
                        completed.add(original["request_id"])
                        outstanding.discard(original["request_id"])
                        if not unknown:
                            pending = None
            elif kind == "controller_acquired":
                _require(payload["controller"] == "held", "native_session_controller")
                controller = "held"
            elif kind == "controller_released":
                _require(payload["controller"] == "released", "native_session_controller")
                controller = "released"
            elif kind == "controller_release_unknown":
                _require(payload["controller"] == "unknown", "native_session_controller")
                _text(payload["reason"])
                tainted = True
                controller = "unknown"
            elif kind in {
                "mode_changed",
                "autonomy_budget_exhausted",
                "handoff_to_human",
                "stopped",
            }:
                _budget(payload["autonomy_budget"])
                _require(
                    payload["controller"] in {"held", "released", "unknown"},
                    "native_session_controller",
                )
                controller = payload["controller"]
                if kind == "mode_changed":
                    _require(
                        payload["mode"] in {"human", "shadow", "one_step", "auto"},
                        "native_session_mode",
                    )
                    mode = payload["mode"]
                else:
                    mode = "human"
                    if "reason" in payload:
                        _text(payload["reason"])
                if kind == "stopped":
                    _require(
                        sequence == len(events)
                        and payload["pending_request"] == pending
                        and payload["agent_state"] in {"known", "uncertain"},
                        "native_session_stop",
                    )
                    tainted |= pending is not None
                    tainted |= controller != "released"
            elif kind == "runtime_tainted":
                _text(payload["reason"])
                _require(payload["retry"] is False, "native_session_retry_forbidden")
                tainted = True
                mode = "human"
            elif kind == "fail_closed":
                _text(payload["reason"])
                _require(
                    payload["agent_state"] in {"known", "uncertain"}, "native_session_agent_state"
                )
                agent_uncertain = payload["agent_state"] == "uncertain"
            elif kind == "native_gap":
                stream.record_gap(_object(payload["gap"]))
            elif kind == "native_event_received":
                self._event_availability(payload["original"], payload["received_cursor"])
                stream.received()
            elif kind == "native_await_result":
                self._await(payload)
            elif kind in {"agent_state_stored", "agent_state_restored"}:
                _require(
                    not tainted
                    and pending is None
                    and not outstanding
                    and stream.gap is None
                    and stream.received_unconsumed == 0
                    and (kind == "agent_state_restored" or not agent_uncertain),
                    "native_session_state_requires_durable_prefix",
                )
                metadata = self._state_metadata(
                    payload["metadata"], agent, last_report, last_ack, last_witness
                )
                if kind == "agent_state_stored":
                    pair = self._state_files(directory, payload, metadata, agent)
                    stored_files.update(pair)
                    stored_metadata.append(metadata)
                else:
                    _require(metadata in stored_metadata, "native_session_restore_not_stored")
                    agent_uncertain = False
        _require(not outstanding or tainted, "native_session_unclosed_submission")
        _require(not tainted or run["tainted"], "native_session_taint_erased")
        _require(stored_files == state_files, "native_session_undeclared_state")
        return events

    @staticmethod
    def _attachment(
        payload: dict[str, Any], agent: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        subscription = _object(
            payload["subscription"],
            {
                "subscription_id",
                "scope_id",
                "eager_scope",
                "coverage",
                "delivery_mode",
                "stream_generation",
                "starting_cursor",
                "expires_at",
            },
        )
        for key in ("subscription_id", "scope_id", "stream_generation", "starting_cursor"):
            _text(subscription[key], maximum=1024)
        _time(subscription["expires_at"])
        expected = agent["input"]["attachment"]
        _require(
            subscription["eager_scope"] == expected["eager_scope"]
            and subscription["delivery_mode"] == expected["delivery_mode"]
            and subscription["coverage"] == expected["required_seams"],
            "native_session_attachment",
        )
        environment = _object(
            payload["environment"],
            {
                "runtime_instance_id",
                "environment_fingerprint",
                "host_kind",
                "connector_protocol_version",
                "connector_version",
                "connector_source_revision",
                "connector_artifact_sha256",
                "connector_module_version_id",
                "game_version",
                "game_commit",
                "modset_status",
                "modset_fingerprint",
                "loaded_mod_ids",
            },
        )
        required = agent["requirements"]["environment"]
        _require(
            all(environment.get(key) == value for key, value in required.items()),
            "native_session_environment",
        )
        _text(environment.get("runtime_instance_id"))
        _text(environment["environment_fingerprint"])
        _require(
            environment["connector_protocol_version"]
            == agent["requirements"]["connector_protocol_version"],
            "native_session_environment_protocol",
        )
        _require(
            environment.get("game_version") in agent["support"]["game_versions"]
            and environment.get("game_commit") in agent["support"]["game_commits"],
            "native_session_game_support",
        )
        return subscription, environment

    @staticmethod
    def _consumption(
        payload: dict[str, Any], agent: dict[str, Any], previous: dict[str, Any] | None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        report = _object(
            payload["report"],
            {
                "acquisition_id",
                "input_spec",
                "continuity_token",
                "previous_consumption_id",
                "consumption_id",
                "state_version",
                "advanced",
            },
        )
        acknowledgement = _object(
            payload["acknowledgement"],
            {"consumption_id", "acquisition_id", "state_version", "advanced", "prefix"},
        )
        for key in ("acquisition_id", "continuity_token", "consumption_id"):
            _text(report[key], maximum=256)
        _nullable_text(report["previous_consumption_id"], maximum=256)
        _input_spec(report["input_spec"])
        version, advanced = _integer(report["state_version"]), _boolean(report["advanced"])
        _integer(acknowledgement["state_version"])
        _boolean(acknowledgement["advanced"])
        _require(
            report["input_spec"] == agent["input"]["input_spec"]
            and report["acquisition_id"] == payload["witness"]["acquisition_id"]
            and all(
                acknowledgement[key] == report[key]
                for key in ("consumption_id", "acquisition_id", "state_version", "advanced")
            ),
            "native_session_consume_ack",
        )
        if previous is None or previous["continuity_token"] != report["continuity_token"]:
            _require(
                report["previous_consumption_id"] is None and version == int(advanced),
                "native_session_consumption_initial",
            )
        else:
            _require(
                report["previous_consumption_id"] == previous["consumption_id"]
                and version == previous["state_version"] + int(advanced)
                and (advanced or report["consumption_id"] == previous["consumption_id"]),
                "native_session_consumption_prefix",
            )
        _prefix(acknowledgement["prefix"], agent, report["continuity_token"])
        return report, acknowledgement

    @staticmethod
    def _directive(
        value: object, report: dict[str, Any] | None, witnesses: Mapping[str, dict[str, Any]]
    ) -> dict[str, Any]:
        output = _object(
            value, {"continuity_token", "consumption_id", "state_version", "directive"}
        )
        _text(output["continuity_token"], maximum=256)
        _nullable_text(output["consumption_id"])
        _integer(output["state_version"])
        _require(
            (output["state_version"] == 0) == (output["consumption_id"] is None),
            "native_session_directive_watermark",
        )
        if report is not None:
            _require(
                all(
                    output[key] == report[key]
                    for key in ("continuity_token", "consumption_id", "state_version")
                ),
                "native_session_directive_prefix",
            )
        directive = _object(output["directive"])
        kind = directive.get("type")
        if kind == "act":
            _object(directive, {"type", "basis_acquisition_id", "selection", "scores"})
            witness = witnesses.get(directive["basis_acquisition_id"])
            _require(
                report is not None
                and output["consumption_id"] is not None
                and witness is not None
                and directive["basis_acquisition_id"] == report["acquisition_id"],
                "native_session_directive_basis",
            )
            selection = _object(directive["selection"])
            if selection.get("kind") == "handle":
                _object(selection, {"kind", "action_id"})
                _text(selection["action_id"])
            else:
                _require(selection.get("kind") == "expression", "native_session_selection")
                _object(selection, {"kind", "expression"})
                _object(selection["expression"])
            if directive["scores"] is not None:
                scores = _object(directive["scores"], {"catalog_digest", "values"})
                _digest(scores["catalog_digest"])
                _require(
                    isinstance(scores["values"], list)
                    and all(
                        type(item) in {int, float} and math.isfinite(item)
                        for item in scores["values"]
                    ),
                    "native_session_scores",
                )
                assert witness is not None
                _require(
                    scores["catalog_digest"] == witness["catalog_digest"]
                    and len(scores["values"]) == witness["catalog_count"],
                    "native_session_score_catalog",
                )
        elif kind == "await":
            _object(directive, {"type", "after_cursor", "condition", "timeout_ms"})
            _text(directive["after_cursor"], maximum=1024)
            _require(
                directive["condition"]
                in {"any_event", "observation", "catalog_nonempty", "terminal"},
                "native_session_await_condition",
            )
            _integer(directive["timeout_ms"], positive=True, maximum=30000)
        else:
            _require(kind in {"abstain", "close"}, "native_session_directive_type")
            _object(directive, {"type", "reason"})
            _text(directive["reason"], maximum=256, allow_empty=True)
        return output

    @staticmethod
    def _event_availability(value: object, received_cursor: object | None = None) -> None:
        availability = _object(value, {"event", "availability"})
        _require(
            availability["availability"] in {"available", "payload_expired", "missing"},
            "native_session_event_availability",
        )
        event = _object(
            availability["event"],
            {
                "schema",
                "cursor",
                "stream_generation",
                "publication_index",
                "kind",
                "source_seam",
                "source_phase",
                "source_index",
                "scope_id",
                "capture_ref",
                "missing_reason",
                "coverage",
                "payload_reference",
            },
        )
        _require(
            event["schema"] == "sts2.player-environment/native-logical-event-1"
            and event["coverage"] in {"complete_at_seam", "sampled", "unsupported"},
            "native_session_event_namespace",
        )
        for key in (
            "cursor",
            "stream_generation",
            "kind",
            "source_seam",
            "source_phase",
            "scope_id",
        ):
            _text(event[key])
        if received_cursor is not None:
            _require(received_cursor == event["cursor"], "native_session_received_cursor")
        _index(event["publication_index"])
        _index(event["source_index"])
        _nullable_text(event["capture_ref"])
        _nullable_text(event["missing_reason"])
        if event["payload_reference"] is not None:
            capture = _capture(event["payload_reference"])
            _require(
                event["capture_ref"] == capture["capture_id"]
                and event["stream_generation"] == capture["stream_generation"]
                and event["scope_id"] == capture["scope_id"]
                and event["missing_reason"] is None,
                "native_session_event_payload",
            )
        else:
            _require(
                event["capture_ref"] is None and event["missing_reason"] is not None,
                "native_session_event_missing",
            )
        _require(
            (availability["availability"] == "missing") == (event["payload_reference"] is None),
            "native_session_event_availability_binding",
        )

    def _await(self, payload: dict[str, Any]) -> None:
        _text(payload["wait_id"], maximum=256)
        _text(payload["after_cursor"], maximum=1024)
        result = _object(payload["result"], {"schema", "status", "event", "gap", "reason"})
        _require(
            result["schema"] == "sts2.player-environment/native-logical-await-1"
            and result["status"]
            in {
                "event",
                "timeout",
                "gap",
                "cancelled",
                "subscription_expired",
                "generation_changed",
                "capacity_exceeded",
            },
            "native_session_await_namespace",
        )
        _require(
            (result["status"] == "event") == (result["event"] is not None)
            and (result["status"] == "gap") == (result["gap"] is not None),
            "native_session_await_disposition",
        )
        if result["event"] is not None:
            self._event_availability(result["event"])
        if result["gap"] is not None:
            gap = _object(
                result["gap"], {"reason", "from_publication_index", "through_publication_index"}
            )
            _text(gap["reason"])
            start, end = (
                _index(gap["from_publication_index"]),
                _index(gap["through_publication_index"]),
            )
            _require(
                start is not None and end is not None and start <= end, "native_session_gap_order"
            )
        _nullable_text(result["reason"])

    @staticmethod
    def _state_metadata(
        value: object,
        agent: dict[str, Any],
        report: dict[str, Any] | None,
        ack: dict[str, Any] | None,
        witness: dict[str, Any] | None,
    ) -> dict[str, Any]:
        metadata = _object(
            value,
            {
                "agent_artifact_id",
                "agent_artifact_sha256",
                "adapter_code_sha256",
                "model_bindings",
                "input_spec",
                "profile",
                "state_format_version",
                "stream_generation",
                "continuity_token",
                "consumption_id",
                "state_version",
                "prefix",
                "last_acknowledged_basis",
            },
        )
        _integer(metadata["state_version"], positive=True)
        _require(
            report is not None and ack is not None and witness is not None,
            "native_session_state_without_ack",
        )
        assert report is not None and ack is not None and witness is not None
        _require(
            metadata["agent_artifact_id"] == agent["artifact"]["id"]
            and metadata["agent_artifact_sha256"] == agent["artifact"]["sha256"]
            and metadata["adapter_code_sha256"] == agent["adapter"]["code_sha256"]
            and metadata["model_bindings"] == agent["input"]["state_recovery"]["model_bindings"]
            and metadata["input_spec"] == agent["input"]["input_spec"]
            and metadata["profile"] == PROFILE
            and metadata["state_format_version"] == agent["input"]["state_format_version"]
            and metadata["stream_generation"] == witness["capture"]["stream_generation"]
            and all(
                metadata[key] == report[key]
                for key in ("continuity_token", "consumption_id", "state_version")
            )
            and metadata["prefix"] == ack["prefix"],
            "native_session_state_bindings",
        )
        expected_basis = {
            "acquisition_id": witness["acquisition_id"],
            "capture_sha256": witness["capture"]["sha256"],
            "snapshot_id": witness["snapshot_id"],
            "owner_occurrence": witness["owner_occurrence"],
            "revision": witness["revision"],
            "included": witness["included"],
            "publication_index": witness["publication_index"],
        }
        _require(
            metadata["last_acknowledged_basis"] == expected_basis, "native_session_state_basis"
        )
        return metadata

    @staticmethod
    def _state_files(
        directory: Path, payload: dict[str, Any], metadata: dict[str, Any], agent: dict[str, Any]
    ) -> set[str]:
        _require(agent["input"]["state_recovery"]["mode"] == "opaque", "native_session_state_mode")
        count = _integer(
            payload["bytes"],
            positive=True,
            maximum=agent["input"]["state_recovery"]["max_state_bytes"],
        )
        sha = _digest(payload["sha256"])
        identity = _sha(_canonical_json({"metadata": metadata, "sha256": sha}).encode("utf-8"))
        path, metadata_path = f"agent-state-{identity}.bin", f"agent-state-{identity}.json"
        _require(
            payload["path"] == path and payload["metadata_path"] == metadata_path,
            "native_session_state_paths",
        )
        raw = (directory / path).read_bytes()
        _require(len(raw) == count and _sha(raw) == sha, "native_session_state_integrity")
        record = _json((directory / metadata_path).read_bytes(), metadata_path)
        _integer(
            _object(record.get("payload"), {"path", "bytes", "sha256"})["bytes"], positive=True
        )
        _require(
            record
            == {
                "schema": "sts2.policy-runtime/agent-state-snapshot-1",
                "metadata": metadata,
                "payload": {"path": path, "bytes": count, "sha256": sha},
            },
            "native_session_state_descriptor",
        )
        return {path, metadata_path}


def verify_agent_session_run_evidence(
    source: str | Path, expected: Mapping[str, object] | None = None
) -> VerificationResult[AgentSessionRunEvidence]:
    return AgentSessionRunEvidenceVerifier().verify(source, expected)


def detect_agent_session_run_type(source: str | Path) -> str | None:
    try:
        value = _json((Path(source) / "manifest.json").read_bytes(), "manifest.json")
        return TYPE_ID if value.get("schema") == RUN_SCHEMA else None
    except (AgentRunEvidenceError, OSError, ValueError, TypeError):
        return None


__all__ = [
    "DESCRIPTOR",
    "TYPE_ID",
    "AgentSessionRunEvidence",
    "AgentSessionRunEvidenceVerifier",
    "detect_agent_session_run_type",
    "verify_agent_session_run_evidence",
]
