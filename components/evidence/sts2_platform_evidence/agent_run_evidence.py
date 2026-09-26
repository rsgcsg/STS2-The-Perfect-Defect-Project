"""Fail-closed verification for finalized Policy Runtime agent-run evidence.

The Policy Runtime producer emits one bounded directory containing its run
manifest, append-only event log, immutable evidence manifest, and checksums.
This adapter verifies that contract without importing Policy Runtime or
duplicating the generic store and transfer machinery.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .core import VerificationFinding, VerificationResult, VerifierDescriptor

AGENT_RUN_SCHEMA = "sts2.policy-runtime/agent-run-1"
AGENT_RUN_EVENT_SCHEMA = "sts2.policy-runtime/agent-run-event-1"
EVIDENCE_MANIFEST_SCHEMA = "sts2.policy-runtime/immutable-evidence-manifest-1"
POLICY_MANIFEST_SCHEMA = "sts2.policy-runtime/policy-manifest-1"
ADAPTER_ATTESTATION_SCHEMA = "sts2.policy-runtime/adapter-attestation-1"
AGENT_RUN_TYPE = "policy-runtime-agent-run"

_MANIFEST_FILE = "manifest.json"
_EVENTS_FILE = "events.jsonl"
_POLICY_MANIFEST_FILE = "policy-manifest.json"
_ADAPTER_ATTESTATION_FILE = "adapter-attestation.json"
_EVIDENCE_MANIFEST_FILE = "evidence-manifest.json"
_CHECKSUMS_FILE = "checksums.sha256"
_PAYLOAD_FILES = (
    _ADAPTER_ATTESTATION_FILE,
    _EVENTS_FILE,
    _EVIDENCE_MANIFEST_FILE,
    _MANIFEST_FILE,
    _POLICY_MANIFEST_FILE,
)
_EVIDENCE_FILES = (
    _ADAPTER_ATTESTATION_FILE,
    _EVENTS_FILE,
    _MANIFEST_FILE,
    _POLICY_MANIFEST_FILE,
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHECKSUM_LINE = re.compile(r"^([0-9a-f]{64})  ([^/\\]+(?:/[^/\\]+)*)$")


class AgentRunEvidenceError(ValueError):
    """A stable, machine-readable typed verification failure."""

    def __init__(self, code: str, detail: str, path: str | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.path = path


@dataclass(frozen=True)
class AgentRunEvidence:
    directory: Path
    manifest: Mapping[str, Any]
    evidence_manifest: Mapping[str, Any]
    event_count: int
    content_id: str

    @property
    def run_id(self) -> str:
        return str(self.manifest["run_id"])


DESCRIPTOR: VerifierDescriptor[AgentRunEvidence] = VerifierDescriptor(
    AGENT_RUN_TYPE,
    AGENT_RUN_SCHEMA,
    1,
    AgentRunEvidence,
)


class AgentRunEvidenceVerifier:
    """Verify only the finalized Policy Runtime evidence directory format."""

    descriptor = DESCRIPTOR

    def verify(
        self,
        source: str | Path,
        expected: Mapping[str, object] | None = None,
    ) -> VerificationResult[AgentRunEvidence]:
        directory = Path(source).resolve()
        try:
            value = self._verify(directory, expected)
            return VerificationResult(self.descriptor, "pass", directory, value)
        except AgentRunEvidenceError as error:
            return VerificationResult(
                self.descriptor,
                "fail",
                directory,
                findings=(VerificationFinding(error.code, str(error), error.path),),
            )
        except (OSError, TypeError, ValueError) as error:
            return VerificationResult(
                self.descriptor,
                "fail",
                directory,
                findings=(VerificationFinding("malformed_agent_run", str(error)),),
            )

    def _verify(
        self,
        directory: Path,
        expected: Mapping[str, object] | None,
    ) -> AgentRunEvidence:
        if not directory.is_dir():
            raise AgentRunEvidenceError("agent_run_absent", f"agent-run evidence is absent: {directory}")
        manifest = _load_json_object(directory / _MANIFEST_FILE)
        if manifest.get("schema") != AGENT_RUN_SCHEMA:
            raise AgentRunEvidenceError(
                "unknown_schema",
                f"unsupported Policy Runtime agent-run schema: {manifest.get('schema')!r}",
                _MANIFEST_FILE,
            )
        self._verify_inventory(directory)
        checksums = _read_checksums(directory / _CHECKSUMS_FILE)
        self._verify_checksums(directory, checksums)

        _verify_manifest(manifest, expected)
        adapter_attested = _verify_policy_provenance(directory, manifest)
        policy_manifest = _load_json_object(directory / _POLICY_MANIFEST_FILE)
        representation = policy_manifest.get("representation")
        input_schema = representation.get("input_schema") if isinstance(representation, dict) else None
        events = _verify_events(directory / _EVENTS_FILE, manifest, input_schema)
        if any(event["kind"] == "decision" for event in events) and not adapter_attested:
            raise AgentRunEvidenceError(
                "adapter_not_attested",
                "decision evidence requires an exact adapter startup attestation",
                _ADAPTER_ATTESTATION_FILE,
            )

        evidence_manifest = _load_json_object(directory / _EVIDENCE_MANIFEST_FILE)
        _verify_evidence_manifest(
            evidence_manifest,
            run_id=_text(manifest, "run_id"),
            directory=directory,
        )
        return AgentRunEvidence(
            directory,
            manifest,
            evidence_manifest,
            len(events),
            _verified_content_id(directory),
        )

    @staticmethod
    def _verify_inventory(directory: Path) -> None:
        entries = list(directory.rglob("*"))
        if any(path.is_symlink() for path in entries):
            raise AgentRunEvidenceError("symbolic_link", "symbolic links are not allowed")
        actual_files = {
            path.relative_to(directory).as_posix()
            for path in entries
            if path.is_file()
        }
        if actual_files != set(_PAYLOAD_FILES) | {_CHECKSUMS_FILE}:
            raise AgentRunEvidenceError(
                "checksum_inventory_mismatch",
                "agent-run directory must contain exactly the six Policy Runtime output files",
            )
        actual_directories = {path for path in entries if path.is_dir()}
        if actual_directories:
            raise AgentRunEvidenceError("unexpected_directory", "agent-run evidence cannot contain directories")

    @staticmethod
    def _verify_checksums(directory: Path, checksums: Mapping[str, str]) -> None:
        expected_paths = set(_PAYLOAD_FILES)
        if set(checksums) != expected_paths:
            raise AgentRunEvidenceError(
                "checksum_inventory_mismatch",
                "checksums.sha256 must cover the exact Policy Runtime evidence payload inventory",
                _CHECKSUMS_FILE,
            )
        for relative, expected in checksums.items():
            path = directory / relative
            actual_bytes = path.read_bytes()
            actual = _sha256_bytes(actual_bytes)
            if actual != expected:
                raise AgentRunEvidenceError("checksum_mismatch", f"checksum mismatch: {relative}", relative)


def detect_agent_run_type(source: str | Path) -> str:
    """Detect this adapter only from the producer's explicit manifest schema."""

    directory = Path(source).resolve()
    try:
        manifest = _load_json_object(directory / _MANIFEST_FILE)
    except (OSError, TypeError, ValueError) as error:
        raise KeyError(f"unable to detect evidence type: {error}") from error
    schema = manifest.get("schema")
    if schema != AGENT_RUN_SCHEMA:
        raise KeyError(f"unknown evidence schema: {schema!r}")
    return AGENT_RUN_TYPE


def verify_agent_run_evidence(
    source: str | Path,
    expected: Mapping[str, object] | None = None,
) -> VerificationResult[AgentRunEvidence]:
    return AgentRunEvidenceVerifier().verify(source, expected)


def _verify_manifest(value: Mapping[str, Any], expected: Mapping[str, object] | None) -> None:
    _exact_keys(
        value,
        {
            "schema",
            "run_id",
            "manifest_id",
            "policy_manifest_sha256",
            "policy_id",
            "policy_version",
            "policy_artifact_sha256",
            "runtime_version",
            "runtime_code_sha256",
            "started_at",
            "ended_at",
            "status",
            "mode",
            "tainted",
            "append_only",
        },
        "agent-run manifest",
    )
    _literal(value, "schema", AGENT_RUN_SCHEMA)
    for key in ("run_id", "manifest_id", "policy_id", "policy_version", "runtime_version"):
        _text(value, key)
    for key in ("policy_manifest_sha256", "policy_artifact_sha256", "runtime_code_sha256"):
        digest = _text(value, key)
        if not _SHA256.fullmatch(digest):
            raise AgentRunEvidenceError(
                "invalid_digest",
                f"manifest {key} is not a SHA-256",
                _MANIFEST_FILE,
            )
    _timestamp(value, "started_at")
    ended_at = value.get("ended_at")
    if ended_at is None:
        raise AgentRunEvidenceError("run_not_finalized", "finalized agent-run evidence requires ended_at", _MANIFEST_FILE)
    if not isinstance(ended_at, str):
        raise AgentRunEvidenceError("invalid_timestamp", "ended_at must be a date-time", _MANIFEST_FILE)
    _timestamp(value, "ended_at")
    status = value.get("status")
    if status not in {"completed", "stopped", "tainted"}:
        raise AgentRunEvidenceError("invalid_status", "finalized agent-run status is invalid", _MANIFEST_FILE)
    if value.get("mode") not in {"human", "shadow", "one_step", "auto"}:
        raise AgentRunEvidenceError("invalid_mode", "agent-run mode is invalid", _MANIFEST_FILE)
    _boolean(value, "tainted")
    if status == "tainted" and value["tainted"] is not True:
        raise AgentRunEvidenceError("taint_drift", "tainted status requires tainted=true", _MANIFEST_FILE)
    _literal(value, "append_only", True)
    if expected is not None:
        for key in (
            "run_id",
            "manifest_id",
            "policy_manifest_sha256",
            "policy_id",
            "policy_version",
            "policy_artifact_sha256",
            "runtime_version",
            "runtime_code_sha256",
        ):
            if key in expected and expected[key] != value[key]:
                raise AgentRunEvidenceError("expected_manifest_drift", f"manifest {key} differs from expected", _MANIFEST_FILE)


def _verify_policy_provenance(directory: Path, manifest: Mapping[str, Any]) -> bool:
    policy_manifest = _load_json_object(directory / _POLICY_MANIFEST_FILE)
    _literal(policy_manifest, "schema", POLICY_MANIFEST_SCHEMA, _POLICY_MANIFEST_FILE)
    manifest_id = _text(policy_manifest, "manifest_id", _POLICY_MANIFEST_FILE)
    policy = _object(policy_manifest.get("policy"), "policy manifest policy")
    artifact = _object(policy_manifest.get("artifact"), "policy manifest artifact")
    adapter = _object(policy_manifest.get("adapter"), "policy manifest adapter")
    _verify_adapter_identity(adapter, _POLICY_MANIFEST_FILE)

    canonical_digest = _sha256_bytes(_canonical_json(policy_manifest).encode("utf-8"))
    if canonical_digest != manifest["policy_manifest_sha256"]:
        raise AgentRunEvidenceError(
            "policy_manifest_digest",
            "canonical policy-manifest.json differs from the run manifest digest",
            _POLICY_MANIFEST_FILE,
        )
    if manifest_id != manifest["manifest_id"]:
        raise AgentRunEvidenceError("manifest_association", "policy manifest ID differs from run manifest", _POLICY_MANIFEST_FILE)
    if _text(policy, "id", _POLICY_MANIFEST_FILE) != manifest["policy_id"]:
        raise AgentRunEvidenceError("policy_association", "policy ID differs from run manifest", _POLICY_MANIFEST_FILE)
    if _text(policy, "version", _POLICY_MANIFEST_FILE) != manifest["policy_version"]:
        raise AgentRunEvidenceError("policy_association", "policy version differs from run manifest", _POLICY_MANIFEST_FILE)
    if _text(artifact, "sha256", _POLICY_MANIFEST_FILE) != manifest["policy_artifact_sha256"]:
        raise AgentRunEvidenceError("artifact_association", "policy artifact differs from run manifest", _POLICY_MANIFEST_FILE)

    attestation = _load_json_object(directory / _ADAPTER_ATTESTATION_FILE)
    _exact_keys(
        attestation,
        {
            "schema",
            "run_id",
            "manifest_id",
            "policy_manifest_sha256",
            "status",
            "expected",
            "actual",
            "attested_at",
        },
        "adapter attestation",
    )
    _literal(attestation, "schema", ADAPTER_ATTESTATION_SCHEMA, _ADAPTER_ATTESTATION_FILE)
    if _text(attestation, "run_id", _ADAPTER_ATTESTATION_FILE) != manifest["run_id"]:
        raise AgentRunEvidenceError("run_id_drift", "adapter attestation run differs", _ADAPTER_ATTESTATION_FILE)
    if _text(attestation, "manifest_id", _ADAPTER_ATTESTATION_FILE) != manifest["manifest_id"]:
        raise AgentRunEvidenceError("manifest_association", "adapter attestation manifest differs", _ADAPTER_ATTESTATION_FILE)
    if _text(attestation, "policy_manifest_sha256", _ADAPTER_ATTESTATION_FILE) != manifest["policy_manifest_sha256"]:
        raise AgentRunEvidenceError("manifest_association", "adapter attestation digest differs", _ADAPTER_ATTESTATION_FILE)
    expected_adapter = _object(attestation.get("expected"), "adapter attestation expected")
    _verify_adapter_identity(expected_adapter, _ADAPTER_ATTESTATION_FILE)
    if expected_adapter != adapter:
        raise AgentRunEvidenceError("adapter_association", "expected adapter differs from Policy Manifest", _ADAPTER_ATTESTATION_FILE)

    status = attestation.get("status")
    if status == "attested":
        actual_adapter = _object(attestation.get("actual"), "adapter attestation actual")
        _verify_adapter_identity(actual_adapter, _ADAPTER_ATTESTATION_FILE)
        if actual_adapter != expected_adapter:
            raise AgentRunEvidenceError("adapter_association", "actual adapter differs from expected", _ADAPTER_ATTESTATION_FILE)
        _timestamp(attestation, "attested_at", _ADAPTER_ATTESTATION_FILE)
        return True
    if status == "not_attested":
        if attestation.get("actual") is not None or attestation.get("attested_at") is not None:
            raise AgentRunEvidenceError("adapter_attestation", "non-attested adapter must not claim actual identity", _ADAPTER_ATTESTATION_FILE)
        return False
    raise AgentRunEvidenceError("adapter_attestation", "adapter attestation status is invalid", _ADAPTER_ATTESTATION_FILE)


def _verify_adapter_identity(value: Mapping[str, Any], path: str) -> None:
    _exact_keys(value, {"id", "version", "protocol", "code_sha256"}, "adapter identity")
    _text(value, "id", path)
    _text(value, "version", path)
    _literal(value, "protocol", "sts2.policy-runtime/decision-only-ndjson-1", path)
    digest = _text(value, "code_sha256", path)
    if not _SHA256.fullmatch(digest):
        raise AgentRunEvidenceError("invalid_digest", "adapter code_sha256 is invalid", path)


_EVENT_KINDS = {
    "environment_admitted",
    "stale_whole_bundle_discarded",
    "mode_changed",
    "decision",
    "controller_acquired",
    "controller_released",
    "receipt_rejected",
    "receipt",
    "successor",
    "handoff_to_human",
    "one_step_completed",
    "fail_closed",
    "runtime_tainted",
    "stopped",
    "text_decision_input",
    "text_menu_dispatch_attempt",
    "menu_navigation",
    "text_native_delivery",
    "text_native_unknown",
    "text_menu_not_applied",
    "text_observed_successor",
    "text_menu_result_rejected",
    "controller_release_failed",
    "autonomy_budget_exhausted",
}
_PLAYER_VERBS = {
    "activate",
    "select",
    "deselect",
    "confirm",
    "cancel",
    "play",
    "target",
    "use",
    "end_turn",
    "skip",
    "open",
    "close",
}


def _verify_events(path: Path, manifest: Mapping[str, Any], input_schema: str | None) -> list[Mapping[str, Any]]:
    raw = path.read_bytes()
    if not raw:
        return []
    lines = raw.split(b"\n")
    if lines[-1] != b"":
        raise AgentRunEvidenceError("unterminated_event", "events.jsonl must end each event with a newline", _EVENTS_FILE)
    events: list[Mapping[str, Any]] = []
    environment: Mapping[str, Any] | None = None
    decisions: dict[str, Mapping[str, Any]] = {}
    receipts: dict[str, Mapping[str, Any]] = {}
    rejected_receipts: set[str] = set()
    successors: dict[str, Mapping[str, Any]] = {}
    text_inputs: dict[str, Mapping[str, Any]] = {}
    text_dispatches: dict[str, Mapping[str, Any]] = {}
    text_outcomes: dict[str, str] = {}
    text_successors: set[str] = set()
    pending_text_input: str | None = None
    for sequence, content in enumerate(lines[:-1], start=1):
        if content.endswith(b"\r"):
            content = content[:-1]
        if not content:
            raise AgentRunEvidenceError("blank_event", f"blank event line: {sequence}", _EVENTS_FILE)
        value = _load_json_object_bytes(content, f"events.jsonl:{sequence}")
        _exact_keys(value, {"schema", "sequence", "recorded_at", "kind", "payload"}, "agent-run event")
        _literal(value, "schema", AGENT_RUN_EVENT_SCHEMA, _EVENTS_FILE)
        if type(value.get("sequence")) is not int or value["sequence"] != sequence:
            raise AgentRunEvidenceError("event_sequence_gap", f"event sequence is not contiguous at line {sequence}", _EVENTS_FILE)
        _timestamp(value, "recorded_at", _EVENTS_FILE)
        kind = value.get("kind")
        if not isinstance(kind, str) or not kind or "\n" in kind or "\r" in kind:
            raise AgentRunEvidenceError("invalid_event_kind", f"invalid event kind at line {sequence}", _EVENTS_FILE)
        if not isinstance(value.get("payload"), dict):
            raise AgentRunEvidenceError("invalid_event_payload", f"event payload is not an object at line {sequence}", _EVENTS_FILE)
        if kind not in _EVENT_KINDS:
            raise AgentRunEvidenceError("unsupported_event_kind", f"unsupported event kind: {kind}", _EVENTS_FILE)
        payload = value["payload"]
        if pending_text_input is not None and kind not in {"decision", "fail_closed", "runtime_tainted"}:
            raise AgentRunEvidenceError("text_decision_order", "text input must immediately precede its decision", _EVENTS_FILE)
        if kind.startswith("text_") or kind == "menu_navigation":
            if input_schema != "sts2.player-environment/text-menu-snapshot-1":
                raise AgentRunEvidenceError("text_profile_association", "text event requires a text-menu policy representation", _EVENTS_FILE)
        if kind == "text_decision_input":
            if environment is None:
                raise AgentRunEvidenceError("environment_identity_order", "text input requires environment admission", _EVENTS_FILE)
            _exact_keys(payload, {"decision_id", "snapshot"}, "text_decision_input payload")
            decision_id = _text(payload, "decision_id", _EVENTS_FILE)
            if decision_id in text_inputs or decision_id in decisions:
                raise AgentRunEvidenceError("duplicate_decision", "duplicate text decision input", _EVENTS_FILE)
            snapshot = _object(payload["snapshot"], "text decision snapshot")
            _verify_text_snapshot(snapshot, environment, "text decision snapshot")
            if snapshot["status"] != "interactive" or snapshot["completeness"]["status"] != "complete" or snapshot["menu_actions"]["status"] != "complete" or not snapshot["menu_actions"]["actions"]:
                raise AgentRunEvidenceError("text_input_incomplete", "text decision requires a complete executable menu", _EVENTS_FILE)
            text_inputs[decision_id] = snapshot
            pending_text_input = decision_id
        elif kind == "environment_admitted":
            environment = _verify_environment_admission(payload, manifest)
        elif kind == "stale_whole_bundle_discarded":
            _verify_stale_event(payload)
        elif kind == "mode_changed":
            if set(payload) not in ({"mode"}, {"mode", "autonomy_budget"}):
                raise AgentRunEvidenceError("schema_keys", "mode_changed payload has invalid fields", _EVENTS_FILE)
            _mode(payload, "mode", _EVENTS_FILE)
            if "autonomy_budget" in payload:
                _verify_autonomy_budget(payload["autonomy_budget"])
        elif kind == "autonomy_budget_exhausted":
            _exact_keys(payload, {"reason", "budget", "controller"}, "autonomy_budget_exhausted payload")
            _enum(payload, "reason", {"submission_attempt_limit", "policy_call_limit", "deadline"}, _EVENTS_FILE)
            _enum(payload, "controller", {"held", "released"}, _EVENTS_FILE)
            budget = _verify_autonomy_budget(payload["budget"])
            if budget["state"] != "exhausted" or budget["exhausted_reason"] != payload["reason"]:
                raise AgentRunEvidenceError("budget_association", "exhaustion event and budget differ", _EVENTS_FILE)
        elif kind == "controller_release_failed":
            _exact_keys(payload, {"reason"}, "controller_release_failed payload")
            _text(payload, "reason", _EVENTS_FILE)
        elif kind == "decision":
            if environment is None:
                raise AgentRunEvidenceError(
                    "environment_identity_order",
                    "decision evidence requires a prior environment_admitted event",
                    _EVENTS_FILE,
                )
            decision, resolved_action_id = _verify_decision_event(payload, manifest)
            decision_id = str(decision["decision_id"])
            if input_schema == "sts2.player-environment/text-menu-snapshot-1":
                if pending_text_input != decision_id or decision_id not in text_inputs:
                    raise AgentRunEvidenceError("text_decision_order", "text decision has no immediately preceding input", _EVENTS_FILE)
                _verify_text_decision_binding(decision, resolved_action_id, text_inputs[decision_id])
                pending_text_input = None
            elif pending_text_input is not None:
                raise AgentRunEvidenceError("text_decision_order", "text input and decision IDs differ", _EVENTS_FILE)
            if decision_id in decisions:
                raise AgentRunEvidenceError("duplicate_decision", f"duplicate decision_id: {decision_id}", _EVENTS_FILE)
            decisions[decision_id] = {
                "decision": decision,
                "resolved_action_id": resolved_action_id,
                "environment": environment,
            }
        elif kind == "controller_acquired":
            _exact_keys(payload, set(), "controller_acquired payload")
        elif kind == "controller_released":
            _exact_keys(payload, set(), "controller_released payload")
        elif kind == "receipt_rejected":
            if environment is None:
                raise AgentRunEvidenceError(
                    "environment_identity_order",
                    "receipt rejection requires a prior environment_admitted event",
                    _EVENTS_FILE,
                )
            decision_id, receipt = _verify_receipt_rejected(payload, manifest, environment, decisions)
            if decision_id in receipts or decision_id in rejected_receipts:
                raise AgentRunEvidenceError("duplicate_receipt", f"duplicate receipt for decision: {decision_id}", _EVENTS_FILE)
            rejected_receipts.add(decision_id)
            receipts[decision_id] = receipt
        elif kind == "receipt":
            if environment is None:
                raise AgentRunEvidenceError(
                    "environment_identity_order",
                    "receipt evidence requires a prior environment_admitted event",
                    _EVENTS_FILE,
                )
            decision_id, receipt = _verify_receipt_event(payload, manifest, environment, decisions)
            if decision_id in receipts or decision_id in rejected_receipts:
                raise AgentRunEvidenceError("duplicate_receipt", f"duplicate receipt for decision: {decision_id}", _EVENTS_FILE)
            receipts[decision_id] = receipt
        elif kind == "successor":
            if environment is None:
                raise AgentRunEvidenceError(
                    "environment_identity_order",
                    "successor evidence requires a prior environment_admitted event",
                    _EVENTS_FILE,
                )
            decision_id, successor = _verify_successor_event(
                payload,
                environment,
                decisions,
                receipts,
                rejected_receipts,
            )
            if decision_id in successors:
                raise AgentRunEvidenceError("duplicate_successor", f"duplicate successor for decision: {decision_id}", _EVENTS_FILE)
            successors[decision_id] = successor
        elif kind == "text_menu_dispatch_attempt":
            decision_id = _verify_text_dispatch(payload, decisions, text_inputs, text_dispatches)
            text_dispatches[decision_id] = payload
        elif kind in {"menu_navigation", "text_native_delivery", "text_native_unknown", "text_menu_not_applied", "text_menu_result_rejected"}:
            decision_id = _verify_text_outcome(kind, payload, manifest, environment, decisions, text_inputs, text_dispatches)
            if decision_id in text_outcomes:
                raise AgentRunEvidenceError("duplicate_text_result", "multiple text outcomes for one decision", _EVENTS_FILE)
            text_outcomes[decision_id] = kind
        elif kind == "text_observed_successor":
            decision_id = _verify_text_observed_successor(payload, environment, text_inputs, text_outcomes)
            if decision_id in text_successors:
                raise AgentRunEvidenceError("duplicate_successor", "duplicate text successor", _EVENTS_FILE)
            text_successors.add(decision_id)
        elif kind == "handoff_to_human":
            _exact_keys(payload, {"reason"}, "handoff_to_human payload")
            _text(payload, "reason", _EVENTS_FILE)
        elif kind == "one_step_completed":
            if set(payload) not in (set(), {"autonomy_budget"}):
                raise AgentRunEvidenceError("schema_keys", "one_step_completed payload has invalid fields", _EVENTS_FILE)
            if "autonomy_budget" in payload:
                _verify_autonomy_budget(payload["autonomy_budget"])
        elif kind == "stopped":
            if set(payload) not in (set(), {"autonomy_budget", "controller"}):
                raise AgentRunEvidenceError("schema_keys", "stopped payload has invalid fields", _EVENTS_FILE)
            if "autonomy_budget" in payload:
                _verify_autonomy_budget(payload["autonomy_budget"])
                _enum(payload, "controller", {"held", "released"}, _EVENTS_FILE)
        elif kind == "fail_closed":
            pending_text_input = None
            _exact_keys(payload, {"reason"}, "fail_closed payload")
            _text(payload, "reason", _EVENTS_FILE)
        elif kind == "runtime_tainted":
            _exact_keys(payload, {"reason", "retry"}, "runtime_tainted payload")
            _text(payload, "reason", _EVENTS_FILE)
            _literal(payload, "retry", False, _EVENTS_FILE)
        events.append(value)
    if pending_text_input is not None and manifest["status"] != "tainted":
        raise AgentRunEvidenceError("text_decision_order", "unmatched text input in finalized run", _EVENTS_FILE)
    if any(decision_id not in text_outcomes for decision_id in text_dispatches) and manifest["status"] != "tainted":
        raise AgentRunEvidenceError("text_result_missing", "dispatched text action has no result in untainted run", _EVENTS_FILE)
    if manifest["status"] == "completed" and any(kind == "text_native_delivery" and decision_id not in text_successors for decision_id, kind in text_outcomes.items()):
        raise AgentRunEvidenceError("successor_missing", "completed native text delivery lacks observed successor", _EVENTS_FILE)
    if any(kind in {"text_native_unknown", "text_menu_result_rejected"} for kind in text_outcomes.values()) and manifest["status"] != "tainted":
        raise AgentRunEvidenceError("taint_drift", "unknown or rejected text result requires tainted run", _EVENTS_FILE)
    return events


def _verify_environment_admission(
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> Mapping[str, Any]:
    _exact_keys(payload, {"runtime", "policy_artifact_sha256", "environment"}, "environment admission payload")
    runtime = payload.get("runtime")
    environment = payload.get("environment")
    if not isinstance(runtime, dict) or not isinstance(environment, dict):
        raise AgentRunEvidenceError(
            "environment_identity_invalid",
            "environment admission runtime and environment must be objects",
            _EVENTS_FILE,
        )
    _exact_keys(runtime, {"version", "code_sha256"}, "runtime identity")
    if _text(runtime, "version", _EVENTS_FILE) != manifest["runtime_version"]:
        raise AgentRunEvidenceError("runtime_identity_drift", "event runtime version differs from manifest", _EVENTS_FILE)
    runtime_digest = _text(runtime, "code_sha256", _EVENTS_FILE)
    if runtime_digest != manifest["runtime_code_sha256"] or not _SHA256.fullmatch(runtime_digest):
        raise AgentRunEvidenceError("runtime_identity_drift", "event runtime code differs from manifest", _EVENTS_FILE)
    artifact_digest = _text(payload, "policy_artifact_sha256", _EVENTS_FILE)
    if artifact_digest != manifest["policy_artifact_sha256"] or not _SHA256.fullmatch(artifact_digest):
        raise AgentRunEvidenceError("artifact_identity_drift", "event policy artifact differs from manifest", _EVENTS_FILE)
    _exact_keys(
        environment,
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
        "environment identity",
    )
    for key in (
        "runtime_instance_id",
        "environment_fingerprint",
        "connector_protocol_version",
        "connector_version",
        "modset_status",
        "modset_fingerprint",
    ):
        _text(environment, key, _EVENTS_FILE)
    _enum(environment, "host_kind", {"live_ui", "headless", "replay", "test"}, _EVENTS_FILE)
    for key in ("connector_source_revision", "connector_module_version_id", "game_version", "game_commit"):
        _nullable_text(environment, key, _EVENTS_FILE)
    connector_digest = environment.get("connector_artifact_sha256")
    if connector_digest is not None and (not isinstance(connector_digest, str) or not _SHA256.fullmatch(connector_digest)):
        raise AgentRunEvidenceError("environment_identity_invalid", "connector artifact digest is invalid", _EVENTS_FILE)
    loaded_mod_ids = environment.get("loaded_mod_ids")
    if not isinstance(loaded_mod_ids, list) or any(not isinstance(item, str) or not item for item in loaded_mod_ids):
        raise AgentRunEvidenceError("environment_identity_invalid", "loaded_mod_ids must be non-empty strings", _EVENTS_FILE)
    if len(set(loaded_mod_ids)) != len(loaded_mod_ids):
        raise AgentRunEvidenceError("environment_identity_invalid", "loaded_mod_ids must be unique", _EVENTS_FILE)
    return environment


def _verify_stale_event(payload: Mapping[str, Any]) -> None:
    _exact_keys(
        payload,
        {"attempt", "delay_ms", "whole_bundle_discarded", "action_submission_attempted"},
        "stale_whole_bundle_discarded payload",
    )
    _positive_int(payload, "attempt", _EVENTS_FILE)
    _nonnegative_int(payload, "delay_ms", _EVENTS_FILE)
    _literal(payload, "whole_bundle_discarded", True, _EVENTS_FILE)
    _literal(payload, "action_submission_attempted", False, _EVENTS_FILE)


def _verify_decision_event(
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> tuple[Mapping[str, Any], str | None]:
    _exact_keys(payload, {"decision", "resolved_bound_action_id"}, "decision payload")
    decision = payload.get("decision")
    if not isinstance(decision, dict):
        raise AgentRunEvidenceError("invalid_decision", "decision payload must contain an object", _EVENTS_FILE)
    _exact_keys(
        decision,
        {
            "schema",
            "decision_id",
            "run_id",
            "manifest_id",
            "snapshot_id",
            "candidate_digest",
            "candidate_count",
            "scores",
            "selected_index",
            "disposition",
            "issued_at",
        },
        "policy decision",
    )
    _literal(decision, "schema", "sts2.policy-runtime/decision-1", _EVENTS_FILE)
    for key in ("decision_id", "run_id", "manifest_id", "snapshot_id"):
        _text(decision, key, _EVENTS_FILE)
    if decision["run_id"] != manifest["run_id"]:
        raise AgentRunEvidenceError("run_id_drift", "decision run_id differs from manifest", _EVENTS_FILE)
    if decision["manifest_id"] != manifest["manifest_id"]:
        raise AgentRunEvidenceError("manifest_id_drift", "decision manifest_id differs from manifest", _EVENTS_FILE)
    candidate_digest = _text(decision, "candidate_digest", _EVENTS_FILE)
    if not _SHA256.fullmatch(candidate_digest):
        raise AgentRunEvidenceError("invalid_decision", "decision candidate_digest is not a SHA-256", _EVENTS_FILE)
    candidate_count = decision.get("candidate_count")
    if not isinstance(candidate_count, int) or isinstance(candidate_count, bool) or candidate_count < 0:
        raise AgentRunEvidenceError("invalid_decision", "decision candidate_count is invalid", _EVENTS_FILE)
    scores = decision.get("scores")
    if not isinstance(scores, list) or len(scores) != candidate_count:
        raise AgentRunEvidenceError("invalid_decision", "decision scores do not match candidate_count", _EVENTS_FILE)
    if any(isinstance(score, bool) or not isinstance(score, (int, float)) or not _finite(score) for score in scores):
        raise AgentRunEvidenceError("invalid_decision", "decision scores must be finite numbers", _EVENTS_FILE)
    selected_index = decision.get("selected_index")
    if selected_index is not None and (
        not isinstance(selected_index, int)
        or isinstance(selected_index, bool)
        or selected_index < 0
        or selected_index >= candidate_count
    ):
        raise AgentRunEvidenceError("invalid_decision", "decision selected_index is invalid", _EVENTS_FILE)
    disposition = decision.get("disposition")
    if disposition not in {"admit", "abstain"}:
        raise AgentRunEvidenceError("invalid_decision", "decision disposition is invalid", _EVENTS_FILE)
    if (selected_index is None) != (disposition == "abstain"):
        raise AgentRunEvidenceError("invalid_decision", "decision disposition does not match selected_index", _EVENTS_FILE)
    _timestamp(decision, "issued_at", _EVENTS_FILE)
    resolved = payload.get("resolved_bound_action_id")
    if resolved is not None and (not isinstance(resolved, str) or not resolved):
        raise AgentRunEvidenceError("invalid_decision", "resolved_bound_action_id must be a non-empty string or null", _EVENTS_FILE)
    if (selected_index is None) != (resolved is None):
        raise AgentRunEvidenceError("action_association", "resolved action does not match decision selection", _EVENTS_FILE)
    return decision, resolved


def _verify_receipt_rejected(
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
    environment: Mapping[str, Any],
    decisions: Mapping[str, Mapping[str, Any]],
) -> tuple[str, Mapping[str, Any]]:
    _exact_keys(
        payload,
        {"decision_id", "expected_request_id", "expected_bound_action_id", "receipt"},
        "receipt_rejected payload",
    )
    decision_id = _text(payload, "decision_id", _EVENTS_FILE)
    decision_record = decisions.get(decision_id)
    if decision_record is None:
        raise AgentRunEvidenceError("decision_association", "receipt rejection references an unknown decision", _EVENTS_FILE)
    expected_request_id = _request_id(str(manifest["run_id"]), decision_id)
    expected_action_id = decision_record["resolved_action_id"]
    if payload.get("expected_request_id") != expected_request_id:
        raise AgentRunEvidenceError("request_association", "receipt rejection expected request differs from derived request", _EVENTS_FILE)
    if payload.get("expected_bound_action_id") != expected_action_id:
        raise AgentRunEvidenceError("action_association", "receipt rejection expected action differs from decision", _EVENTS_FILE)
    if expected_action_id is None:
        raise AgentRunEvidenceError("action_association", "receipt rejection cannot reference an abstained decision", _EVENTS_FILE)
    receipt = _object(payload.get("receipt"), "rejected receipt")
    _verify_receipt(receipt, environment)
    if receipt["request_id"] == expected_request_id and receipt["action"]["bound_action_id"] == expected_action_id:
        raise AgentRunEvidenceError("receipt_association", "receipt_rejected must record a correlation mismatch", _EVENTS_FILE)
    return decision_id, receipt


def _verify_receipt_event(
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
    environment: Mapping[str, Any],
    decisions: Mapping[str, Mapping[str, Any]],
) -> tuple[str, Mapping[str, Any]]:
    _exact_keys(payload, {"decision_id", "receipt"}, "receipt payload")
    decision_id = _text(payload, "decision_id", _EVENTS_FILE)
    decision_record = decisions.get(decision_id)
    if decision_record is None:
        raise AgentRunEvidenceError("decision_association", "receipt references an unknown decision", _EVENTS_FILE)
    if environment["environment_fingerprint"] != decision_record["environment"]["environment_fingerprint"]:
        raise AgentRunEvidenceError("runtime_association", "receipt environment differs from decision environment", _EVENTS_FILE)
    expected_action_id = decision_record["resolved_action_id"]
    if expected_action_id is None:
        raise AgentRunEvidenceError("action_association", "abstained decision cannot have a receipt", _EVENTS_FILE)
    receipt = _object(payload.get("receipt"), "receipt")
    _verify_receipt(receipt, environment)
    expected_request_id = _request_id(str(manifest["run_id"]), decision_id)
    if receipt["request_id"] != expected_request_id:
        raise AgentRunEvidenceError("request_association", "receipt request_id differs from derived request", _EVENTS_FILE)
    if receipt["action"]["bound_action_id"] != expected_action_id:
        raise AgentRunEvidenceError("action_association", "receipt action differs from decision", _EVENTS_FILE)
    return decision_id, receipt


def _verify_successor_event(
    payload: Mapping[str, Any],
    environment: Mapping[str, Any],
    decisions: Mapping[str, Mapping[str, Any]],
    receipts: Mapping[str, Mapping[str, Any]],
    rejected_receipts: set[str],
) -> tuple[str, Mapping[str, Any]]:
    _exact_keys(payload, {"decision_id", "successor"}, "successor payload")
    decision_id = _text(payload, "decision_id", _EVENTS_FILE)
    decision_record = decisions.get(decision_id)
    receipt = receipts.get(decision_id)
    if decision_record is None or receipt is None:
        raise AgentRunEvidenceError("successor_association", "successor must follow a recorded receipt", _EVENTS_FILE)
    if decision_id in rejected_receipts:
        raise AgentRunEvidenceError("successor_association", "rejected receipt cannot have a successor", _EVENTS_FILE)
    if environment["environment_fingerprint"] != decision_record["environment"]["environment_fingerprint"]:
        raise AgentRunEvidenceError("runtime_association", "successor environment differs from decision environment", _EVENTS_FILE)
    if receipt["delivery"] != "delivered":
        raise AgentRunEvidenceError("successor_association", "only delivered receipts may have a successor", _EVENTS_FILE)
    successor = _object(payload.get("successor"), "successor")
    _verify_snapshot(successor, environment, "successor")
    if successor["snapshot_id"] == decision_record["decision"]["snapshot_id"]:
        raise AgentRunEvidenceError("snapshot_association", "successor snapshot must differ from decision snapshot", _EVENTS_FILE)
    if successor["status"] == "settling":
        raise AgentRunEvidenceError("snapshot_association", "successor snapshot must be stable", _EVENTS_FILE)
    receipt_successor = receipt.get("successor")
    if receipt_successor is not None and receipt_successor["snapshot_id"] == successor["snapshot_id"]:
        if receipt_successor["sequence"] != successor["sequence"]:
            raise AgentRunEvidenceError("snapshot_association", "receipt and successor snapshot sequences differ", _EVENTS_FILE)
    return decision_id, successor


def _verify_receipt(receipt: Mapping[str, Any], environment: Mapping[str, Any]) -> None:
    allowed = {"protocol_version", "schema", "request_id", "delivery", "action", "reason_code", "detail", "retry", "successor", "attribution"}
    if set(receipt) - allowed or not {"protocol_version", "schema", "request_id", "delivery", "action", "retry", "successor"}.issubset(receipt):
        raise AgentRunEvidenceError("receipt_schema", "receipt contains unknown or missing fields", _EVENTS_FILE)
    if receipt.get("protocol_version") != environment["connector_protocol_version"]:
        raise AgentRunEvidenceError("runtime_association", "receipt protocol differs from admitted environment", _EVENTS_FILE)
    if receipt.get("schema") != "sts2.player-environment/receipt-1":
        raise AgentRunEvidenceError("receipt_schema", "receipt schema is invalid", _EVENTS_FILE)
    _text(receipt, "request_id", _EVENTS_FILE)
    if receipt.get("delivery") not in {"delivered", "not_delivered", "unknown"}:
        raise AgentRunEvidenceError("receipt_schema", "receipt delivery is invalid", _EVENTS_FILE)
    action = _object(receipt.get("action"), "receipt action")
    if set(action) - {"bound_action_id", "verb", "subject_referent_id", "arguments"} or not {"bound_action_id", "verb", "arguments"}.issubset(action):
        raise AgentRunEvidenceError("action_schema", "receipt action contains unknown or missing fields", _EVENTS_FILE)
    _text(action, "bound_action_id", _EVENTS_FILE)
    _enum(action, "verb", {"activate", "select", "deselect", "confirm", "cancel", "play", "target", "use", "end_turn", "skip", "open", "close"}, _EVENTS_FILE)
    _nullable_text(action, "subject_referent_id", _EVENTS_FILE)
    arguments = action.get("arguments")
    if not isinstance(arguments, list):
        raise AgentRunEvidenceError("action_schema", "receipt action arguments must be an array", _EVENTS_FILE)
    for argument in arguments:
        item = _object(argument, "receipt action argument")
        _exact_keys(item, {"role", "referent_id"}, "receipt action argument")
        _text(item, "role", _EVENTS_FILE)
        _text(item, "referent_id", _EVENTS_FILE)
    retry = _object(receipt.get("retry"), "receipt retry")
    _exact_keys(retry, {"allowed", "reason"}, "receipt retry")
    _boolean(retry, "allowed", _EVENTS_FILE)
    _text(retry, "reason", _EVENTS_FILE)
    if receipt["delivery"] == "unknown" and retry["allowed"] is True:
        raise AgentRunEvidenceError("receipt_schema", "unknown delivery cannot allow retry", _EVENTS_FILE)
    for key in ("reason_code", "detail"):
        if key in receipt and receipt[key] is not None and (not isinstance(receipt[key], str) or not receipt[key]):
            raise AgentRunEvidenceError("receipt_schema", f"receipt {key} must be a non-empty string or null", _EVENTS_FILE)
    successor = receipt.get("successor")
    if successor is not None:
        _verify_snapshot(_object(successor, "receipt successor"), environment, "receipt successor")
    attribution = receipt.get("attribution")
    if attribution is not None:
        attribution_value = _object(attribution, "receipt attribution")
        _exact_keys(
            attribution_value,
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
            "receipt attribution",
        )
        if attribution_value["runtime_instance_id"] != environment["runtime_instance_id"]:
            raise AgentRunEvidenceError("runtime_association", "receipt attribution runtime differs from environment", _EVENTS_FILE)
        for key in ("client_session_id", "client_instance_id", "product_id", "product_name", "product_version", "controller_lease_id"):
            _text(attribution_value, key, _EVENTS_FILE)
        _positive_int(attribution_value, "controller_generation", _EVENTS_FILE)


def _verify_snapshot(value: Mapping[str, Any], environment: Mapping[str, Any], label: str) -> None:
    _exact_keys(
        value,
        {
            "protocol_version",
            "schema",
            "snapshot_id",
            "sequence",
            "observed_at",
            "status",
            "persistent",
            "interaction",
            "referents",
            "bound_actions",
            "reads",
            "completeness",
            "session",
            "information_policy",
        },
        label,
    )
    if value["protocol_version"] != environment["connector_protocol_version"]:
        raise AgentRunEvidenceError("runtime_association", f"{label} protocol differs from environment", _EVENTS_FILE)
    _literal(value, "schema", "sts2.player-environment/snapshot-1", _EVENTS_FILE)
    _text(value, "snapshot_id", _EVENTS_FILE)
    _positive_int(value, "sequence", _EVENTS_FILE)
    _timestamp(value, "observed_at", _EVENTS_FILE)
    if value["status"] not in {"interactive", "visible_unsupported", "settling", "observed"}:
        raise AgentRunEvidenceError("snapshot_schema", f"{label} status is invalid", _EVENTS_FILE)
    persistent = value.get("persistent")
    if persistent is not None:
        persistent_value = _object(persistent, f"{label} persistent")
        _exact_keys(persistent_value, {"content_schema", "content"}, f"{label} persistent")
        _literal(persistent_value, "content_schema", "sts2.player-environment/persistent/run-player-1", _EVENTS_FILE)
    interaction = _object(value.get("interaction"), f"{label} interaction")
    interaction_keys = {"interaction_id", "kind", "stage", "prompt", "content_schema", "content", "capabilities"}
    if set(interaction) - interaction_keys or not {"interaction_id", "kind", "stage", "content_schema", "content", "capabilities"}.issubset(interaction):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} interaction contains unknown or missing fields", _EVENTS_FILE)
    for key in ("interaction_id", "kind", "stage", "content_schema"):
        _text(interaction, key, _EVENTS_FILE)
    if "prompt" in interaction:
        _nullable_text(interaction, "prompt", _EVENTS_FILE)
    if not re.fullmatch(r"^sts2\.player-environment/surface/[a-z0-9_]+-1$", interaction["content_schema"]):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} interaction content_schema is invalid", _EVENTS_FILE)
    interaction_content = _object(interaction["content"], f"{label} interaction content")
    _exact_keys(interaction_content, {"surface", "context"}, f"{label} interaction content")
    for key in ("surface", "context"):
        surface = _object(interaction_content[key], f"{label} interaction {key}")
        _text(surface, "kind", _EVENTS_FILE)
    capabilities = interaction["capabilities"]
    if not isinstance(capabilities, list):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} capabilities must be an array", _EVENTS_FILE)
    for capability in capabilities:
        item = _object(capability, f"{label} capability")
        capability_keys = {"verb", "subject_role", "arguments", "availability_basis"}
        if set(item) - capability_keys or not {"verb", "arguments", "availability_basis"}.issubset(item):
            raise AgentRunEvidenceError("snapshot_schema", f"{label} capability contains unknown or missing fields", _EVENTS_FILE)
        _enum(item, "verb", _PLAYER_VERBS, _EVENTS_FILE)
        _nullable_text(item, "subject_role", _EVENTS_FILE)
        _literal(item, "availability_basis", "current_native_interaction", _EVENTS_FILE)
        arguments = item["arguments"]
        if not isinstance(arguments, list):
            raise AgentRunEvidenceError("snapshot_schema", f"{label} capability arguments must be an array", _EVENTS_FILE)
        for argument in arguments:
            argument_value = _object(argument, f"{label} capability argument")
            _exact_keys(argument_value, {"role", "required"}, f"{label} capability argument")
            _text(argument_value, "role", _EVENTS_FILE)
            _boolean(argument_value, "required", _EVENTS_FILE)
    referents = value.get("referents")
    if not isinstance(referents, list):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} referents must be an array", _EVENTS_FILE)
    referent_ids: set[str] = set()
    for referent in referents:
        item = _object(referent, f"{label} referent")
        referent_keys = {"referent_id", "role", "kind", "label", "state", "properties_schema", "properties"}
        if set(item) - referent_keys or not {"referent_id", "role", "kind", "state"}.issubset(item):
            raise AgentRunEvidenceError("snapshot_schema", f"{label} referent contains unknown or missing fields", _EVENTS_FILE)
        referent_id = _text(item, "referent_id", _EVENTS_FILE)
        if referent_id in referent_ids:
            raise AgentRunEvidenceError("snapshot_schema", f"{label} referent IDs must be unique", _EVENTS_FILE)
        referent_ids.add(referent_id)
        _text(item, "role", _EVENTS_FILE)
        _enum(item, "kind", {"entity", "control"}, _EVENTS_FILE)
        _nullable_text(item, "label", _EVENTS_FILE)
        state = _object(item["state"], f"{label} referent state")
        state_keys = {"visible", "enabled", "selected", "focused", "observation_basis"}
        if set(state) - state_keys or "visible" not in state or "observation_basis" not in state:
            raise AgentRunEvidenceError("snapshot_schema", f"{label} referent state contains unknown or missing fields", _EVENTS_FILE)
        _boolean(state, "visible", _EVENTS_FILE)
        for key in ("enabled", "selected", "focused"):
            _nullable_boolean(state, key, _EVENTS_FILE)
        _literal(state, "observation_basis", "native_visible_fact", _EVENTS_FILE)
        _nullable_text(item, "properties_schema", _EVENTS_FILE)
        if item.get("properties_schema") is not None and not re.fullmatch(
            r"^sts2\.player-environment/referent/[a-z0-9_]+-1$",
            item["properties_schema"],
        ):
            raise AgentRunEvidenceError("snapshot_schema", f"{label} referent properties_schema is invalid", _EVENTS_FILE)
        if "properties" in item and item["properties"] is not None and item.get("properties_schema") is None:
            raise AgentRunEvidenceError("snapshot_schema", f"{label} referent properties require a schema", _EVENTS_FILE)
    bound_actions = _object(value.get("bound_actions"), f"{label} bound_actions")
    _exact_keys(
        bound_actions,
        {"schema", "status", "materialized_count", "total_count", "limit", "ordering_semantics", "actions"},
        f"{label} bound_actions",
    )
    _literal(bound_actions, "schema", "sts2.player-environment/bound-actions-1", _EVENTS_FILE)
    _enum(bound_actions, "status", {"complete", "truncated", "unavailable"}, _EVENTS_FILE)
    _nonnegative_int(bound_actions, "materialized_count", _EVENTS_FILE)
    _nonnegative_int(bound_actions, "total_count", _EVENTS_FILE)
    _positive_int(bound_actions, "limit", _EVENTS_FILE)
    _text(bound_actions, "ordering_semantics", _EVENTS_FILE)
    actions = bound_actions["actions"]
    if not isinstance(actions, list):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} actions must be an array", _EVENTS_FILE)
    action_ids: set[str] = set()
    for action in actions:
        item = _object(action, f"{label} bound action")
        action_keys = {"bound_action_id", "verb", "interaction_id", "subject_referent_id", "arguments", "label"}
        if set(item) - action_keys or not {"bound_action_id", "verb", "interaction_id", "arguments", "label"}.issubset(item):
            raise AgentRunEvidenceError("snapshot_schema", f"{label} bound action contains unknown or missing fields", _EVENTS_FILE)
        action_id = _text(item, "bound_action_id", _EVENTS_FILE)
        if action_id in action_ids:
            raise AgentRunEvidenceError("snapshot_schema", f"{label} bound action IDs must be unique", _EVENTS_FILE)
        action_ids.add(action_id)
        _enum(item, "verb", _PLAYER_VERBS, _EVENTS_FILE)
        if item["interaction_id"] != interaction["interaction_id"]:
            raise AgentRunEvidenceError("snapshot_schema", f"{label} bound action interaction differs", _EVENTS_FILE)
        _nullable_text(item, "subject_referent_id", _EVENTS_FILE)
        if item.get("subject_referent_id") is not None and item["subject_referent_id"] not in referent_ids:
            raise AgentRunEvidenceError("snapshot_schema", f"{label} bound action subject is not a referent", _EVENTS_FILE)
        _text(item, "label", _EVENTS_FILE)
        _verify_action_arguments(item["arguments"], referent_ids, f"{label} bound action")
    if bound_actions["materialized_count"] != len(actions) or bound_actions["materialized_count"] > bound_actions["total_count"] or bound_actions["materialized_count"] > bound_actions["limit"]:
        raise AgentRunEvidenceError("snapshot_schema", f"{label} bound action counts are inconsistent", _EVENTS_FILE)
    if bound_actions["status"] == "complete" and bound_actions["materialized_count"] != bound_actions["total_count"]:
        raise AgentRunEvidenceError("snapshot_schema", f"{label} complete action catalog is incomplete", _EVENTS_FILE)
    if (value["status"] == "interactive") != (bound_actions["status"] == "complete" and len(actions) > 0):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} interactive status and action catalog differ", _EVENTS_FILE)
    reads = value.get("reads")
    if not isinstance(reads, list):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} reads must be an array", _EVENTS_FILE)
    for read in reads:
        item = _object(read, f"{label} read")
        required = {"read_id", "kind", "content_schema", "visibility_basis", "snapshot_bound", "ordering_semantics", "hidden_by_policy"}
        if not required.issubset(item) or set(item) - (required | {"target_referent_id"}):
            raise AgentRunEvidenceError("schema_keys", f"{label} read contains unknown or missing fields")
        _text(item, "read_id", _EVENTS_FILE)
        _text(item, "kind", _EVENTS_FILE)
        _nullable_text(item, "target_referent_id", _EVENTS_FILE)
        if item.get("target_referent_id") is not None and item["target_referent_id"] not in referent_ids:
            raise AgentRunEvidenceError("snapshot_schema", f"{label} read target is not a referent", _EVENTS_FILE)
        _text(item, "content_schema", _EVENTS_FILE)
        if not re.fullmatch(r"^sts2\.player-environment/read/[a-z0-9_]+-1$", item["content_schema"]):
            raise AgentRunEvidenceError("snapshot_schema", f"{label} read content_schema is invalid", _EVENTS_FILE)
        _text(item, "visibility_basis", _EVENTS_FILE)
        _literal(item, "snapshot_bound", True, _EVENTS_FILE)
        _text(item, "ordering_semantics", _EVENTS_FILE)
        _string_array(item, "hidden_by_policy", _EVENTS_FILE)
    completeness = _object(value.get("completeness"), f"{label} completeness")
    _exact_keys(completeness, {"status", "visible_information", "interaction_discovery", "missing", "hidden_by_policy"}, f"{label} completeness")
    _enum(completeness, "status", {"complete", "partial", "visible_unmapped", "unknown"}, _EVENTS_FILE)
    for key in ("visible_information", "interaction_discovery"):
        _text(completeness, key, _EVENTS_FILE)
    for key in ("missing", "hidden_by_policy"):
        _string_array(completeness, key, _EVENTS_FILE)
    session = _object(value.get("session"), f"{label} session")
    _exact_keys(session, {"runtime_instance_id", "environment_fingerprint"}, f"{label} session")
    if session.get("runtime_instance_id") != environment["runtime_instance_id"] or session.get("environment_fingerprint") != environment["environment_fingerprint"]:
        raise AgentRunEvidenceError("runtime_association", f"{label} session differs from environment", _EVENTS_FILE)
    _text(session, "runtime_instance_id", _EVENTS_FILE)
    _text(session, "environment_fingerprint", _EVENTS_FILE)
    information_policy = _object(value.get("information_policy"), f"{label} information_policy")
    _exact_keys(information_policy, {"id", "scope", "includes_hidden_information", "unknown_field_behavior"}, f"{label} information_policy")
    for key in ("id", "scope", "unknown_field_behavior"):
        _text(information_policy, key, _EVENTS_FILE)
    _literal(information_policy, "includes_hidden_information", False, _EVENTS_FILE)


def _verify_action_arguments(value: object, referent_ids: set[str], label: str) -> None:
    if not isinstance(value, list):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} arguments must be an array", _EVENTS_FILE)
    for argument in value:
        item = _object(argument, f"{label} argument")
        _exact_keys(item, {"role", "referent_id"}, f"{label} argument")
        _text(item, "role", _EVENTS_FILE)
        referent_id = _text(item, "referent_id", _EVENTS_FILE)
        if referent_id not in referent_ids:
            raise AgentRunEvidenceError("snapshot_schema", f"{label} argument is not a referent", _EVENTS_FILE)


def _nullable_boolean(value: Mapping[str, Any], key: str, path: str) -> None:
    if key in value and value[key] is not None and type(value[key]) is not bool:
        raise AgentRunEvidenceError("schema_boolean", f"{key} must be a boolean or null", path)


def _string_array(value: Mapping[str, Any], key: str, path: str) -> None:
    item = value.get(key)
    if not isinstance(item, list) or any(not isinstance(entry, str) for entry in item):
        raise AgentRunEvidenceError("schema_array", f"{key} must be an array of strings", path)


def _verify_evidence_manifest(
    value: Mapping[str, Any],
    *,
    run_id: str,
    directory: Path,
) -> None:
    _exact_keys(value, {"schema", "run_id", "complete", "append_only", "files", "manifest_sha256"}, "evidence manifest")
    _literal(value, "schema", EVIDENCE_MANIFEST_SCHEMA, _EVIDENCE_MANIFEST_FILE)
    if value.get("run_id") != run_id:
        raise AgentRunEvidenceError("run_id_drift", "evidence manifest run_id differs from manifest", _EVIDENCE_MANIFEST_FILE)
    _literal(value, "complete", True, _EVIDENCE_MANIFEST_FILE)
    _literal(value, "append_only", True, _EVIDENCE_MANIFEST_FILE)
    files = value.get("files")
    if not isinstance(files, list) or len(files) != len(_EVIDENCE_FILES):
        raise AgentRunEvidenceError("evidence_file_inventory", "evidence manifest must list manifest.json and events.jsonl", _EVIDENCE_MANIFEST_FILE)
    expected_entries: list[dict[str, object]] = []
    for item in files:
        if not isinstance(item, dict):
            raise AgentRunEvidenceError("evidence_file_entry", "evidence manifest file entry is not an object", _EVIDENCE_MANIFEST_FILE)
        _exact_keys(item, {"path", "bytes", "sha256"}, "evidence manifest file entry")
        relative = item.get("path")
        if relative not in _EVIDENCE_FILES:
            raise AgentRunEvidenceError("evidence_file_inventory", f"unexpected evidence file: {relative!r}", _EVIDENCE_MANIFEST_FILE)
        if not isinstance(item.get("bytes"), int) or isinstance(item.get("bytes"), bool) or item["bytes"] < 0:
            raise AgentRunEvidenceError("evidence_file_bytes", f"invalid byte count: {relative!r}", _EVIDENCE_MANIFEST_FILE)
        digest = item.get("sha256")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise AgentRunEvidenceError("evidence_file_digest", f"invalid digest: {relative!r}", _EVIDENCE_MANIFEST_FILE)
        expected_entries.append({"path": relative, "bytes": item["bytes"], "sha256": digest})
    if [str(item["path"]) for item in expected_entries] != list(_EVIDENCE_FILES):
        raise AgentRunEvidenceError("evidence_file_inventory", "evidence manifest files must be sorted and unique", _EVIDENCE_MANIFEST_FILE)
    actual_entries = [_file_entry(directory / relative, relative) for relative in _EVIDENCE_FILES]
    if expected_entries != actual_entries:
        raise AgentRunEvidenceError("evidence_file_mismatch", "evidence manifest file entries differ from bytes", _EVIDENCE_MANIFEST_FILE)
    manifest_sha = value.get("manifest_sha256")
    if not isinstance(manifest_sha, str) or not _SHA256.fullmatch(manifest_sha):
        raise AgentRunEvidenceError("evidence_manifest_digest", "evidence manifest digest is invalid", _EVIDENCE_MANIFEST_FILE)
    expected_sha = _sha256_bytes(_canonical_json({"run_id": run_id, "files": expected_entries}).encode("utf-8"))
    if manifest_sha != expected_sha:
        raise AgentRunEvidenceError("evidence_manifest_digest", "evidence manifest digest mismatch", _EVIDENCE_MANIFEST_FILE)


def _read_checksums(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise AgentRunEvidenceError("invalid_checksum_encoding", "checksums.sha256 is not UTF-8", _CHECKSUMS_FILE) from error
    lines = text.splitlines(keepends=True)
    if not lines or any(not line.endswith("\n") for line in lines):
        raise AgentRunEvidenceError("invalid_checksum_file", "checksums.sha256 must contain newline-terminated entries", _CHECKSUMS_FILE)
    result: dict[str, str] = {}
    paths: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        content = line[:-1]
        if content.endswith("\r"):
            content = content[:-1]
        match = _CHECKSUM_LINE.fullmatch(content)
        if match is None:
            raise AgentRunEvidenceError("invalid_checksum_line", f"invalid checksum line: {line_number}", _CHECKSUMS_FILE)
        digest, relative = match.groups()
        if relative in result or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise AgentRunEvidenceError("invalid_checksum_path", f"invalid or duplicate checksum path: {relative}", _CHECKSUMS_FILE)
        result[relative] = digest
        paths.append(relative)
    if paths != sorted(paths):
        raise AgentRunEvidenceError("unsorted_checksums", "checksums.sha256 entries must be sorted", _CHECKSUMS_FILE)
    return result


def _file_entry(path: Path, relative: str) -> dict[str, object]:
    data = path.read_bytes()
    return {"path": relative, "bytes": len(data), "sha256": _sha256_bytes(data)}


def _load_json_object(path: Path) -> dict[str, Any]:
    return _load_json_object_bytes(path.read_bytes(), path.name)


def _load_json_object_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise AgentRunEvidenceError("invalid_json", f"invalid JSON: {label}", label) from error
    if not isinstance(value, dict):
        raise AgentRunEvidenceError("json_object_required", f"JSON object required: {label}", label)
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON number: {value}")


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise AgentRunEvidenceError("schema_keys", f"{label} contains unknown or missing fields")


def _literal(value: Mapping[str, Any], key: str, expected: object, path: str | None = None) -> None:
    if value.get(key) != expected or (isinstance(expected, bool) and type(value.get(key)) is not bool):
        raise AgentRunEvidenceError("schema_literal", f"{key} has an invalid literal", path)


def _text(value: Mapping[str, Any], key: str, path: str = _MANIFEST_FILE) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise AgentRunEvidenceError("schema_text", f"{key} must be a non-empty string", path)
    return item


def _nullable_text(value: Mapping[str, Any], key: str, path: str) -> None:
    if key in value and value[key] is not None and (not isinstance(value[key], str) or not value[key]):
        raise AgentRunEvidenceError("schema_text", f"{key} must be a non-empty string or null", path)


def _boolean(value: Mapping[str, Any], key: str, path: str = _MANIFEST_FILE) -> None:
    if type(value.get(key)) is not bool:
        raise AgentRunEvidenceError("schema_boolean", f"{key} must be a boolean", path)


def _mode(value: Mapping[str, Any], key: str, path: str) -> None:
    if value.get(key) not in {"human", "shadow", "one_step", "auto"}:
        raise AgentRunEvidenceError("invalid_mode", f"{key} is invalid", path)


def _enum(value: Mapping[str, Any], key: str, allowed: set[str], path: str) -> None:
    if not isinstance(value.get(key), str) or value[key] not in allowed:
        raise AgentRunEvidenceError("invalid_value", f"{key} is invalid", path)


def _positive_int(value: Mapping[str, Any], key: str, path: str) -> None:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
        raise AgentRunEvidenceError("invalid_integer", f"{key} must be a positive integer", path)


def _nonnegative_int(value: Mapping[str, Any], key: str, path: str) -> None:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or item < 0:
        raise AgentRunEvidenceError("invalid_integer", f"{key} must be a non-negative integer", path)


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentRunEvidenceError("schema_object", f"{label} must be an object", _EVENTS_FILE)
    return value


def _request_id(run_id: str, decision_id: str) -> str:
    return f"request-{run_id}-{decision_id}"


def _timestamp(value: Mapping[str, Any], key: str, path: str = _MANIFEST_FILE) -> None:
    item = value.get(key)
    if not isinstance(item, str) or "T" not in item:
        raise AgentRunEvidenceError("invalid_timestamp", f"{key} must be an RFC3339 date-time", path)
    try:
        parsed = datetime.fromisoformat(item.replace("Z", "+00:00"))
    except ValueError as error:
        raise AgentRunEvidenceError("invalid_timestamp", f"{key} must be an RFC3339 date-time", path) from error
    if parsed.tzinfo is None:
        raise AgentRunEvidenceError("invalid_timestamp", f"{key} must include a timezone", path)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _verified_content_id(directory: Path) -> str:
    # Match the generic store identity, but derive it only after typed bytes pass verification.
    files = [_file_entry(directory / relative, relative) for relative in sorted((*_PAYLOAD_FILES, _CHECKSUMS_FILE))]
    identity = {"schema": "sts2.evidence/store-directory-1", "files": files}
    return _sha256_bytes(_canonical_json(identity).encode("utf-8"))


__all__ = [
    "AGENT_RUN_EVENT_SCHEMA",
    "AGENT_RUN_SCHEMA",
    "AGENT_RUN_TYPE",
    "AgentRunEvidence",
    "AgentRunEvidenceError",
    "AgentRunEvidenceVerifier",
    "DESCRIPTOR",
    "EVIDENCE_MANIFEST_SCHEMA",
    "detect_agent_run_type",
    "verify_agent_run_evidence",
]

_TEXT_SNAPSHOT_SCHEMA = "sts2.player-environment/text-menu-snapshot-1"
_TEXT_RESULT_SCHEMA = "sts2.player-environment/text-menu-action-result-1"
_TEXT_CURSORS = {"root", "information", "relic_inspect", "relic_tips", "card_tips", "power_tips", "intent_tips", "orb_tips", "topbar_tips"}
_TEXT_NAVIGATION = {"open_information", "open_relic_inspect", "open_relic_tips", "open_card_tips", "open_power_tips", "open_intent_tips", "open_orb_tips", "open_topbar_tips", "back"}


def _verify_text_snapshot(value: Mapping[str, Any], environment: Mapping[str, Any], label: str) -> None:
    _exact_keys(value, {"protocol_version", "schema", "input_profile", "snapshot_id", "sequence", "observed_at", "status", "persistent", "interaction", "referents", "completeness", "session", "information_policy", "menu", "menu_actions"}, label)
    _literal(value, "schema", _TEXT_SNAPSHOT_SCHEMA, _EVENTS_FILE)
    _literal(value, "input_profile", "text-menu-v1", _EVENTS_FILE)
    if value["protocol_version"] != environment["connector_protocol_version"]:
        raise AgentRunEvidenceError("runtime_association", f"{label} protocol differs", _EVENTS_FILE)
    _text(value, "snapshot_id", _EVENTS_FILE)
    _positive_int(value, "sequence", _EVENTS_FILE)
    _timestamp(value, "observed_at", _EVENTS_FILE)
    _enum(value, "status", {"interactive", "observed", "settling", "visible_unsupported"}, _EVENTS_FILE)
    if value["persistent"] is not None:
        persistent = _object(value["persistent"], f"{label} persistent")
        _exact_keys(persistent, {"content_schema", "content"}, f"{label} persistent")
        _literal(persistent, "content_schema", "sts2.player-environment/persistent/run-player-1", _EVENTS_FILE)
        _object(persistent["content"], f"{label} persistent content")
    interaction = _object(value["interaction"], f"{label} interaction")
    required = {"interaction_id", "kind", "stage", "content_schema", "content", "capabilities"}
    if not required.issubset(interaction) or set(interaction) - (required | {"prompt"}):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} interaction keys are invalid", _EVENTS_FILE)
    for key in ("interaction_id", "kind", "stage", "content_schema"):
        _text(interaction, key, _EVENTS_FILE)
    _nullable_text(interaction, "prompt", _EVENTS_FILE)
    content = _object(interaction["content"], f"{label} interaction content")
    _exact_keys(content, {"surface", "context"}, f"{label} interaction content")
    for part in ("surface", "context"):
        _text(_object(content[part], f"{label} {part}"), "kind", _EVENTS_FILE)
    capabilities = interaction["capabilities"]
    if not isinstance(capabilities, list):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} capabilities are not an array", _EVENTS_FILE)
    for raw in capabilities:
        capability = _object(raw, f"{label} capability")
        required_capability = {"verb", "arguments", "availability_basis"}
        if not required_capability.issubset(capability) or set(capability) - (required_capability | {"subject_role"}):
            raise AgentRunEvidenceError("snapshot_schema", f"{label} capability keys are invalid", _EVENTS_FILE)
        _text(capability, "verb", _EVENTS_FILE)
        _text(capability, "availability_basis", _EVENTS_FILE)
        _nullable_text(capability, "subject_role", _EVENTS_FILE)
        if not isinstance(capability["arguments"], list):
            raise AgentRunEvidenceError("snapshot_schema", f"{label} capability arguments are invalid", _EVENTS_FILE)
        for raw_argument in capability["arguments"]:
            argument = _object(raw_argument, f"{label} capability argument")
            _exact_keys(argument, {"role", "required"}, f"{label} capability argument")
            _text(argument, "role", _EVENTS_FILE)
            _boolean(argument, "required", _EVENTS_FILE)
    referents = value["referents"]
    if not isinstance(referents, list):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} referents are invalid", _EVENTS_FILE)
    ids: set[str] = set()
    for raw in referents:
        referent = _object(raw, f"{label} referent")
        required_referent = {"referent_id", "role", "kind", "state"}
        if not required_referent.issubset(referent) or set(referent) - (required_referent | {"label", "properties_schema", "properties"}):
            raise AgentRunEvidenceError("snapshot_schema", f"{label} referent keys are invalid", _EVENTS_FILE)
        referent_id = _text(referent, "referent_id", _EVENTS_FILE)
        if referent_id in ids:
            raise AgentRunEvidenceError("snapshot_schema", f"{label} duplicate referent", _EVENTS_FILE)
        ids.add(referent_id)
        _text(referent, "role", _EVENTS_FILE)
        _text(referent, "kind", _EVENTS_FILE)
        _nullable_text(referent, "label", _EVENTS_FILE)
        _nullable_text(referent, "properties_schema", _EVENTS_FILE)
        state = _object(referent["state"], f"{label} referent state")
        if not {"visible", "observation_basis"}.issubset(state) or set(state) - {"visible", "enabled", "selected", "focused", "observation_basis"}:
            raise AgentRunEvidenceError("snapshot_schema", f"{label} referent state keys are invalid", _EVENTS_FILE)
        _boolean(state, "visible", _EVENTS_FILE)
        _text(state, "observation_basis", _EVENTS_FILE)
        for key in ("enabled", "selected", "focused"):
            _nullable_boolean(state, key, _EVENTS_FILE)
        if referent.get("properties") is not None and referent.get("properties_schema") is None:
            raise AgentRunEvidenceError("snapshot_schema", f"{label} referent properties lack schema", _EVENTS_FILE)
    completeness = _object(value["completeness"], f"{label} completeness")
    _exact_keys(completeness, {"status", "visible_information", "interaction_discovery", "missing", "hidden_by_policy"}, f"{label} completeness")
    _enum(completeness, "status", {"complete", "partial", "visible_unmapped", "unknown"}, _EVENTS_FILE)
    for key in ("visible_information", "interaction_discovery"):
        _text(completeness, key, _EVENTS_FILE)
    for key in ("missing", "hidden_by_policy"):
        _string_array(completeness, key, _EVENTS_FILE)
    session = _object(value["session"], f"{label} session")
    _exact_keys(session, {"runtime_instance_id", "environment_fingerprint"}, f"{label} session")
    if session["runtime_instance_id"] != environment["runtime_instance_id"] or session["environment_fingerprint"] != environment["environment_fingerprint"]:
        raise AgentRunEvidenceError("runtime_association", f"{label} session differs from environment", _EVENTS_FILE)
    policy = _object(value["information_policy"], f"{label} information policy")
    _exact_keys(policy, {"id", "scope", "includes_hidden_information", "unknown_field_behavior"}, f"{label} information policy")
    for key in ("id", "scope", "unknown_field_behavior"):
        _text(policy, key, _EVENTS_FILE)
    _literal(policy, "includes_hidden_information", False, _EVENTS_FILE)
    menu = _object(value["menu"], f"{label} menu")
    _exact_keys(menu, {"cursor", "revision", "native_snapshot_id"}, f"{label} menu")
    _enum(menu, "cursor", _TEXT_CURSORS, _EVENTS_FILE)
    _nonnegative_int(menu, "revision", _EVENTS_FILE)
    _text(menu, "native_snapshot_id", _EVENTS_FILE)
    catalog = _object(value["menu_actions"], f"{label} menu actions")
    _exact_keys(catalog, {"status", "materialized_count", "total_count", "ordering_semantics", "actions"}, f"{label} menu actions")
    _enum(catalog, "status", {"complete", "truncated", "unavailable"}, _EVENTS_FILE)
    _nonnegative_int(catalog, "materialized_count", _EVENTS_FILE)
    _nonnegative_int(catalog, "total_count", _EVENTS_FILE)
    _text(catalog, "ordering_semantics", _EVENTS_FILE)
    actions = catalog["actions"]
    if not isinstance(actions, list):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} menu actions are not an array", _EVENTS_FILE)
    action_ids: set[str] = set()
    nav_verbs: set[str] = set()
    for raw in actions:
        action = _object(raw, f"{label} menu action")
        _verify_text_action(action, ids)
        action_id = action["action_id"]
        if action_id in action_ids:
            raise AgentRunEvidenceError("snapshot_schema", f"{label} duplicate action ID", _EVENTS_FILE)
        action_ids.add(action_id)
        if action["kind"] == "system_navigation":
            allowed = {"open_information"} if menu["cursor"] == "root" else ({"back"} | (_TEXT_NAVIGATION - {"open_information"}) if menu["cursor"] == "information" else {"back"})
            if action["verb"] not in allowed or action["verb"] in nav_verbs:
                raise AgentRunEvidenceError("snapshot_schema", f"{label} illegal or duplicate menu edge", _EVENTS_FILE)
            nav_verbs.add(action["verb"])
    if catalog["materialized_count"] != len(actions) or catalog["materialized_count"] > catalog["total_count"]:
        raise AgentRunEvidenceError("snapshot_schema", f"{label} menu counts differ", _EVENTS_FILE)
    if catalog["status"] == "complete" and catalog["materialized_count"] != catalog["total_count"]:
        raise AgentRunEvidenceError("snapshot_schema", f"{label} complete menu count differs", _EVENTS_FILE)
    if catalog["status"] != "complete" and (actions or capabilities):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} incomplete menu advertises actions", _EVENTS_FILE)
    if (value["status"] == "interactive") != (catalog["status"] == "complete" and bool(actions)):
        raise AgentRunEvidenceError("snapshot_schema", f"{label} interactive menu state differs", _EVENTS_FILE)


def _verify_text_action(value: Mapping[str, Any], referent_ids: set[str] | None) -> None:
    _exact_keys(value, {"action_id", "kind", "verb", "label", "subject_referent_id", "arguments", "effect_domain"}, "text menu action")
    _text(value, "action_id", _EVENTS_FILE)
    _enum(value, "kind", {"system_navigation", "native_input"}, _EVENTS_FILE)
    _text(value, "verb", _EVENTS_FILE)
    _text(value, "label", _EVENTS_FILE)
    _enum(value, "effect_domain", {"text_menu", "native_input"}, _EVENTS_FILE)
    _nullable_text(value, "subject_referent_id", _EVENTS_FILE)
    if referent_ids is not None and value["subject_referent_id"] is not None and value["subject_referent_id"] not in referent_ids:
        raise AgentRunEvidenceError("text_binding", "text action subject is unknown", _EVENTS_FILE)
    if referent_ids is not None:
        _verify_action_arguments(value["arguments"], referent_ids, "text menu action")
    else:
        if not isinstance(value["arguments"], list):
            raise AgentRunEvidenceError("text_binding", "text action arguments are invalid", _EVENTS_FILE)
        for raw in value["arguments"]:
            argument = _object(raw, "text action argument")
            _exact_keys(argument, {"role", "referent_id"}, "text action argument")
            _text(argument, "role", _EVENTS_FILE)
            _text(argument, "referent_id", _EVENTS_FILE)
    if (value["kind"] == "system_navigation") != (value["effect_domain"] == "text_menu"):
        raise AgentRunEvidenceError("text_binding", "text action kind and effect differ", _EVENTS_FILE)
    if value["kind"] == "system_navigation" and (value["verb"] not in _TEXT_NAVIGATION or value["subject_referent_id"] is not None or value["arguments"]):
        raise AgentRunEvidenceError("text_binding", "system navigation has illegal operand or verb", _EVENTS_FILE)


def _verify_text_decision_binding(decision: Mapping[str, Any], resolved: str | None, snapshot: Mapping[str, Any]) -> None:
    actions = snapshot["menu_actions"]["actions"]
    ids = [action["action_id"] for action in actions]
    digest = _sha256_bytes(json.dumps(ids, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if decision["snapshot_id"] != snapshot["snapshot_id"] or decision["candidate_count"] != len(ids) or decision["candidate_digest"] != digest:
        raise AgentRunEvidenceError("text_decision_binding", "text decision does not match complete ordered menu", _EVENTS_FILE)
    selected = decision["selected_index"]
    if resolved != (ids[selected] if selected is not None else None):
        raise AgentRunEvidenceError("text_decision_binding", "resolved action differs from selected menu entry", _EVENTS_FILE)


def _verify_text_dispatch(payload: Mapping[str, Any], decisions: Mapping[str, Mapping[str, Any]], inputs: Mapping[str, Mapping[str, Any]], dispatches: Mapping[str, Mapping[str, Any]]) -> str:
    _exact_keys(payload, {"decision_id", "action_id", "effect_domain", "native_submissions_used", "menu_navigations_used"}, "text dispatch")
    decision_id = _text(payload, "decision_id", _EVENTS_FILE)
    if decision_id not in decisions or decision_id not in inputs or decision_id in dispatches:
        raise AgentRunEvidenceError("text_dispatch_binding", "text dispatch has no unique admitted decision", _EVENTS_FILE)
    action_id = decisions[decision_id]["resolved_action_id"]
    action = next((item for item in inputs[decision_id]["menu_actions"]["actions"] if item["action_id"] == action_id), None)
    if action is None or payload["action_id"] != action_id or payload["effect_domain"] != action["effect_domain"]:
        raise AgentRunEvidenceError("text_dispatch_binding", "text dispatch differs from selected action", _EVENTS_FILE)
    _nonnegative_int(payload, "native_submissions_used", _EVENTS_FILE)
    _nonnegative_int(payload, "menu_navigations_used", _EVENTS_FILE)
    return decision_id


def _verify_text_result(result: Mapping[str, Any], environment: Mapping[str, Any]) -> None:
    _exact_keys(result, {"protocol_version", "schema", "input_profile", "request_id", "status", "effect_domain", "native_delivery", "action", "reason_code", "detail", "retry", "successor", "attribution"}, "text result")
    if result["protocol_version"] != environment["connector_protocol_version"]:
        raise AgentRunEvidenceError("runtime_association", "text result protocol differs", _EVENTS_FILE)
    _literal(result, "schema", _TEXT_RESULT_SCHEMA, _EVENTS_FILE)
    _literal(result, "input_profile", "text-menu-v1", _EVENTS_FILE)
    _text(result, "request_id", _EVENTS_FILE)
    _enum(result, "status", {"applied", "not_applied", "unknown"}, _EVENTS_FILE)
    if result["effect_domain"] is not None:
        _enum(result, "effect_domain", {"text_menu", "native_input"}, _EVENTS_FILE)
    if result["native_delivery"] is not None:
        _enum(result, "native_delivery", {"delivered", "not_delivered", "unknown"}, _EVENTS_FILE)
    if result["action"] is not None:
        _verify_text_action(_object(result["action"], "text result action"), None)
    for key in ("reason_code", "detail"):
        _nullable_text(result, key, _EVENTS_FILE)
    _enum(result, "retry", {"never", "reobserve"}, _EVENTS_FILE)
    if result["successor"] is not None:
        _verify_text_snapshot(_object(result["successor"], "text result successor"), environment, "text result successor")
    if result["attribution"] is not None:
        attribution = _object(result["attribution"], "text result attribution")
        _exact_keys(attribution, {"runtime_instance_id", "client_session_id", "client_instance_id", "product_id", "product_name", "product_version", "controller_lease_id", "controller_generation"}, "text result attribution")
        if attribution["runtime_instance_id"] != environment["runtime_instance_id"]:
            raise AgentRunEvidenceError("runtime_association", "text result attribution differs", _EVENTS_FILE)
        for key in ("client_session_id", "client_instance_id", "product_id", "product_name", "product_version", "controller_lease_id"):
            _text(attribution, key, _EVENTS_FILE)
        _positive_int(attribution, "controller_generation", _EVENTS_FILE)


def _verify_text_outcome(kind: str, payload: Mapping[str, Any], manifest: Mapping[str, Any], environment: Mapping[str, Any] | None, decisions: Mapping[str, Mapping[str, Any]], inputs: Mapping[str, Mapping[str, Any]], dispatches: Mapping[str, Mapping[str, Any]]) -> str:
    expected_keys = {"decision_id", "action_id", "result"} if kind == "menu_navigation" else {"decision_id", "expected_request_id", "expected_action_id", "result"} if kind == "text_menu_result_rejected" else {"decision_id", "result"}
    _exact_keys(payload, expected_keys, f"{kind} payload")
    decision_id = _text(payload, "decision_id", _EVENTS_FILE)
    if environment is None or decision_id not in dispatches or decision_id not in decisions or decision_id not in inputs:
        raise AgentRunEvidenceError("text_result_binding", "text result lacks prior dispatch", _EVENTS_FILE)
    selected_id = decisions[decision_id]["resolved_action_id"]
    action = next(item for item in inputs[decision_id]["menu_actions"]["actions"] if item["action_id"] == selected_id)
    result = _object(payload["result"], "text result")
    _verify_text_result(result, environment)
    expected_request = _request_id(str(manifest["run_id"]), decision_id)
    if kind == "text_menu_result_rejected":
        if payload["expected_request_id"] != expected_request or payload["expected_action_id"] != selected_id:
            raise AgentRunEvidenceError("text_result_binding", "rejected result expected identity differs", _EVENTS_FILE)
        mismatched = (result["request_id"] != expected_request
            or (result["action"] is not None and result["action"] != action)
            or (result["status"] != "not_applied" and
                (result["action"] is None or result["effect_domain"] != action["effect_domain"])))
        if not mismatched:
            raise AgentRunEvidenceError("text_result_binding", "rejected result has no correlation mismatch", _EVENTS_FILE)
        return decision_id
    if result["request_id"] != expected_request:
        raise AgentRunEvidenceError("request_association", "text result request differs", _EVENTS_FILE)
    if kind == "menu_navigation" and payload["action_id"] != selected_id:
        raise AgentRunEvidenceError("action_association", "navigation action differs", _EVENTS_FILE)
    if result["action"] != action or result["effect_domain"] != action["effect_domain"]:
        if kind != "text_menu_not_applied" or result["action"] is not None and result["action"] != action:
            raise AgentRunEvidenceError("action_association", "text result action differs", _EVENTS_FILE)
    if kind == "menu_navigation":
        if action["effect_domain"] != "text_menu" or result["status"] != "applied" or result["native_delivery"] is not None or result["retry"] != "never" or result["successor"] is None:
            raise AgentRunEvidenceError("text_result_binding", "navigation falsely claims native delivery or lacks successor", _EVENTS_FILE)
        _verify_text_successor_progress(inputs[decision_id], result["successor"], navigation=True)
    elif kind == "text_native_delivery":
        if action["effect_domain"] != "native_input" or result["status"] != "applied" or result["native_delivery"] != "delivered" or result["retry"] != "never":
            raise AgentRunEvidenceError("text_result_binding", "native delivery is invalid", _EVENTS_FILE)
    elif kind == "text_native_unknown":
        if action["effect_domain"] != "native_input" or result["status"] != "unknown" or result["native_delivery"] != "unknown" or result["retry"] != "never" or result["successor"] is not None:
            raise AgentRunEvidenceError("text_result_binding", "unknown native delivery is invalid", _EVENTS_FILE)
    elif kind == "text_menu_not_applied":
        if result["status"] != "not_applied" or result["native_delivery"] in {"delivered", "unknown"} or (action["effect_domain"] == "text_menu" and result["native_delivery"] is not None) or (result["successor"] is not None and result["retry"] != "reobserve") :
            raise AgentRunEvidenceError("text_result_binding", "not-applied result claims delivery", _EVENTS_FILE)
    return decision_id


def _verify_text_successor_progress(previous: Mapping[str, Any], next_snapshot: Mapping[str, Any], *, navigation: bool) -> None:
    if next_snapshot["snapshot_id"] == previous["snapshot_id"] or next_snapshot["sequence"] <= previous["sequence"] or next_snapshot["session"] != previous["session"]:
        raise AgentRunEvidenceError("successor_association", "text successor does not advance same session", _EVENTS_FILE)
    if navigation and next_snapshot["menu"]["cursor"] == previous["menu"]["cursor"] and next_snapshot["menu"]["revision"] <= previous["menu"]["revision"]:
        raise AgentRunEvidenceError("successor_association", "system navigation did not advance menu cursor", _EVENTS_FILE)


def _verify_text_observed_successor(payload: Mapping[str, Any], environment: Mapping[str, Any] | None, inputs: Mapping[str, Mapping[str, Any]], outcomes: Mapping[str, str]) -> str:
    _exact_keys(payload, {"decision_id", "successor"}, "text observed successor")
    decision_id = _text(payload, "decision_id", _EVENTS_FILE)
    if environment is None or outcomes.get(decision_id) != "text_native_delivery":
        raise AgentRunEvidenceError("successor_association", "observed text successor lacks native delivery", _EVENTS_FILE)
    successor = _object(payload["successor"], "observed text successor")
    _verify_text_snapshot(successor, environment, "observed text successor")
    _verify_text_successor_progress(inputs[decision_id], successor, navigation=False)
    return decision_id


def _verify_autonomy_budget(raw: object) -> Mapping[str, Any]:
    budget = _object(raw, "autonomy budget")
    _exact_keys(budget, {"state", "max_submissions", "submissions_used", "max_policy_calls", "policy_calls_used", "deadline_ms", "elapsed_ms", "remaining_ms", "exhausted_reason", "ended_reason"}, "autonomy budget")
    _enum(budget, "state", {"inactive", "active", "exhausted"}, _EVENTS_FILE)
    for key in ("max_submissions", "max_policy_calls", "deadline_ms"):
        _positive_int(budget, key, _EVENTS_FILE)
    for key in ("submissions_used", "policy_calls_used", "elapsed_ms", "remaining_ms"):
        _nonnegative_int(budget, key, _EVENTS_FILE)
    if budget["submissions_used"] > budget["max_submissions"] or budget["policy_calls_used"] > budget["max_policy_calls"] or budget["elapsed_ms"] + budget["remaining_ms"] != budget["deadline_ms"]:
        raise AgentRunEvidenceError("budget_schema", "autonomy budget counts or clock are inconsistent", _EVENTS_FILE)
    if budget["exhausted_reason"] is not None:
        _enum(budget, "exhausted_reason", {"submission_attempt_limit", "policy_call_limit", "deadline"}, _EVENTS_FILE)
    if budget["ended_reason"] is not None:
        _enum(budget, "ended_reason", {"human_recovery", "mode_changed", "stopped"}, _EVENTS_FILE)
    if (budget["state"] == "exhausted") != (budget["exhausted_reason"] is not None) or (budget["state"] != "inactive" and budget["ended_reason"] is not None):
        raise AgentRunEvidenceError("budget_schema", "autonomy budget state and reasons differ", _EVENTS_FILE)
    return budget
