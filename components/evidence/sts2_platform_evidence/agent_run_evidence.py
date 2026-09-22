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
        events = _verify_events(directory / _EVENTS_FILE, manifest)
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


def _verify_events(path: Path, manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
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
        if kind == "environment_admitted":
            environment = _verify_environment_admission(payload, manifest)
        elif kind == "stale_whole_bundle_discarded":
            _verify_stale_event(payload)
        elif kind == "mode_changed":
            _exact_keys(payload, {"mode"}, "mode_changed payload")
            _mode(payload, "mode", _EVENTS_FILE)
        elif kind == "decision":
            if environment is None:
                raise AgentRunEvidenceError(
                    "environment_identity_order",
                    "decision evidence requires a prior environment_admitted event",
                    _EVENTS_FILE,
                )
            decision, resolved_action_id = _verify_decision_event(payload, manifest)
            decision_id = str(decision["decision_id"])
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
        elif kind == "handoff_to_human":
            _exact_keys(payload, {"reason"}, "handoff_to_human payload")
            _text(payload, "reason", _EVENTS_FILE)
        elif kind == "one_step_completed":
            _exact_keys(payload, set(), "one_step_completed payload")
        elif kind == "fail_closed":
            _exact_keys(payload, {"reason"}, "fail_closed payload")
            _text(payload, "reason", _EVENTS_FILE)
        elif kind == "runtime_tainted":
            _exact_keys(payload, {"reason", "retry"}, "runtime_tainted payload")
            _text(payload, "reason", _EVENTS_FILE)
            _literal(payload, "retry", False, _EVENTS_FILE)
        events.append(value)
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
