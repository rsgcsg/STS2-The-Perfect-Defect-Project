"""Canonical-first immutable Human evidence, without research admission.

The exact producer's independently run causal audit is preserved and hashed.
This portable verifier checks typed identities, references and cross-stream
equivalence; it neither replays STS2 nor infers legality or missing successors.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .core import VerificationFinding, VerificationResult, VerifierDescriptor
from .human_session_bundle_v1 import (
    BundleVerificationError, _digest, _identifier, _jsonl, _load_json,
    _object, _opaque_identifier, _read_checksums, _semantic_hash,
    _sha256_file, _strings, _text,
)
from .human_session_bundle_v2 import _capture_profile_hash, _validate_profile, _validate_journal, _required_reads
from .human_summary import _current_summary
from .human_text_inputs import freeze_json, verify_human_text_inputs
from .interrupted_recovery import verify_recovery

BUNDLE_SCHEMA = "sts2.human-annotator/session-bundle-3"
AUDIT_SCHEMA = "sts2.human-annotator/session-bundle-audit-3"
CANONICAL_SCHEMAS = {
    2: "sts2.human-annotator/canonical-transition-evidence-2",
    3: "sts2.human-annotator/canonical-transition-evidence-3",
}
ACTION_SPACE_SCHEMAS = {
    2: "sts2.human-annotator/execution-semantic-action-space-2",
    3: "sts2.human-annotator/execution-semantic-action-space-3",
}


@dataclass(frozen=True)
class HumanSessionBundleV3:
    directory: Path
    manifest: Mapping[str, Any]
    capture_profile: Mapping[str, Any]
    session_id: str
    timeline_id: str
    worker_id: str
    campaign_id: str
    profile_id: str
    bundle_content_id: str
    bundle_sha256: str
    export_sha256: str
    canonical_count: int
    run_ids: tuple[str, ...]
    invalidations: int
    dispositions: Mapping[str, int]
    summary: Mapping[str, Any]
    text_input_schema_version: int | None = None
    human_text_inputs: tuple[Mapping[str, Any], ...] = ()

    @property
    def export_path(self) -> Path:
        return self.directory / "export" / "canonical-transitions.jsonl"


DESCRIPTOR: VerifierDescriptor[HumanSessionBundleV3] = VerifierDescriptor(
    "human-session-bundle-v3", BUNDLE_SCHEMA, 3, HumanSessionBundleV3,
)


def _require(condition: bool, code: str, detail: str) -> None:
    if not condition:
        raise BundleVerificationError(code, detail)


def _nonnegative(value: Any, name: str) -> int:
    _require(type(value) is int and value >= 0, "count_invalid", name)
    return int(value)


class HumanSessionBundleV3Verifier:
    descriptor = DESCRIPTOR

    def verify(
        self, source: str | Path, expected: Mapping[str, object] | None = None,
    ) -> VerificationResult[HumanSessionBundleV3]:
        directory = Path(source).resolve()
        try:
            return VerificationResult(self.descriptor, "pass", directory, self._verify(directory, expected))
        except BundleVerificationError as error:
            return VerificationResult(self.descriptor, "fail", directory,
                findings=(VerificationFinding(error.code, str(error), error.path),))
        except (OSError, TypeError, ValueError, KeyError, AttributeError) as error:
            return VerificationResult(self.descriptor, "fail", directory,
                findings=(VerificationFinding("malformed_bundle", str(error)),))

    def _verify(self, directory: Path, expected: Mapping[str, object] | None) -> HumanSessionBundleV3:
        checksums_path = directory / "checksums.sha256"
        checksums = _read_checksums(checksums_path)
        paths = list(directory.rglob("*"))
        _require(not any(path.is_symlink() for path in paths), "bundle_symlink", "symlinks are not evidence files")
        files = {path.relative_to(directory).as_posix() for path in paths
                 if path.is_file() and path != checksums_path}
        _require(set(checksums) == files, "checksum_inventory_mismatch", "bundle inventory differs")
        for relative, digest in checksums.items():
            _require(_sha256_file(directory / relative) == digest, "checksum_mismatch", relative)
        manifest = _load_json(directory / "session-bundle-manifest.json")
        _require(manifest.get("schema") == BUNDLE_SCHEMA and manifest.get("schema_version") == 3,
                 "bundle_schema_mismatch", "expected canonical bundle schema 3")
        identity = _object(manifest, "content_identity")
        content_id = _digest(manifest, "bundle_content_id")
        _require(_semantic_hash(identity) == content_id, "content_identity_mismatch", "identity hash differs")
        raw = directory / "raw"
        profile = _load_json(directory / "profile" / "capture-profile.json")
        _validate_profile(profile)
        _require(profile == _load_json(raw / "capture-profile.json"), "capture_profile_drift", "raw profile differs")
        profile_sha = _capture_profile_hash(profile)
        profile_id = _identifier(profile, "profile_id")
        _require(manifest.get("capture_profile_id") == profile_id
                 and manifest.get("capture_profile_sha256") == profile_sha,
                 "capture_profile_drift", "profile identity differs")
        if expected is not None:
            _require(expected.get("profile_id", profile_id) == profile_id
                     and expected.get("profile_sha256", profile_sha) == profile_sha,
                     "capture_profile_drift", "expected profile differs")
        session = _opaque_identifier(manifest, "session_id")
        timeline = _opaque_identifier(manifest, "timeline_id")
        recording = _load_json(raw / "recording-manifest.json")
        _require(recording.get("schema") == "sts2.human-annotator/recording-manifest-2"
                 and recording.get("schema_version") == 2
                 and recording.get("session_id") == session
                 and recording.get("timeline_id") == timeline
                 and recording.get("capture_profile_id") == profile_id
                 and recording.get("capture_profile_sha256") == profile_sha,
                 "recording_manifest_drift", "raw recording identity differs")
        worker = _identifier(manifest, "worker_id")
        attestation = _object(manifest, "human_origin_attestation")
        _require(attestation.get("attested") is True and attestation.get("machine_verifiable") is False
                 and attestation.get("worker_id") == worker
                 and attestation.get("method") == "explicit_owner_pack",
                 "attestation_missing", "explicit Human origin attestation is absent")
        audit_path = directory / "audit" / "audit-report.json"
        audit = _load_json(audit_path)
        count = _nonnegative(manifest.get("canonical_count"), "canonical_count")
        _nonnegative(audit.get("canonical_count"), "audit canonical_count")
        _require(audit.get("schema") == AUDIT_SCHEMA and audit.get("status") == "pass"
                 and manifest.get("audit_status") == "pass" and audit.get("invalid_records") == 0
                 and audit.get("canonical_count") == count and not audit.get("errors"),
                 "audit_failed", "producer causal audit is absent, failed or inconsistent")
        compatibility_count = _nonnegative(audit.get("valid_records"), "valid_records")
        _nonnegative(audit.get("invalid_records"), "invalid_records")
        compatibility_rows = [row for path in sorted(raw.glob("run-*.jsonl"))
                              if path.name != "run-journal.jsonl" for _, row in _jsonl(path)]
        _require(len(compatibility_rows) == compatibility_count,
                 "compatibility_count_mismatch", "audit compatibility count differs from raw Decision records")
        for row in compatibility_rows:
            _require(row.get("schema_version") == 2
                     and row.get("schema") == "sts2.human-annotator/decision-record-2"
                     and row.get("session_id") == session and row.get("timeline_id") == timeline,
                     "compatibility_record_identity_mismatch", "compatibility envelope differs")
        invalidations = _nonnegative(audit.get("invalidations"), "invalidations")
        export_path = directory / "export" / "canonical-transitions.jsonl"
        export_sha = _sha256_file(export_path)
        _require(export_sha == _digest(manifest, "export_sha256"), "export_digest_mismatch", "export hash differs")
        _require(export_path.read_bytes() == (raw / "canonical-transitions.jsonl").read_bytes(),
                 "raw_export_mismatch", "canonical export must preserve exact raw bytes")
        run_ids = tuple(_strings(manifest, "run_ids"))
        _require(list(run_ids) == sorted(set(run_ids)), "run_ids_invalid", "run IDs must be ordered and unique")
        raw_hashes = {path.relative_to(raw).as_posix(): _sha256_file(path)
                      for path in sorted(raw.rglob("*")) if path.is_file()}
        packer = _object(manifest, "packer")
        revision = _text(packer, "source_revision")
        _require(len(revision) == 40 and all(character in "0123456789abcdefABCDEF" for character in revision)
                 and packer.get("product") == "STS2 Native UI Human Annotator Tool" and bool(packer.get("version")),
                 "packer_identity_invalid", "exact packer source identity missing")
        expected_identity = {
            "schema": BUNDLE_SCHEMA, "session_id": session, "timeline_id": timeline, "packer": dict(packer),
            "capture_profile_id": profile_id, "capture_profile_sha256": profile_sha,
            "campaign_id": _identifier(manifest, "campaign_id"), "worker_id": worker,
            "human_origin_attestation": dict(attestation), "canonical_count": count,
            "run_ids": list(run_ids), "export_sha256": export_sha, "raw_file_sha256": raw_hashes,
            "audit_sha256": _sha256_file(audit_path),
            "audit": {key: audit.get(key) for key in (
                "status", "valid_records", "invalid_records", "invalidations", "canonical_count")},
        }
        _require(dict(identity) == expected_identity, "content_identity_facts_mismatch", "identity differs from files")
        _validate_journal(raw / "run-journal.jsonl", session, timeline)
        journal = [row for _, row in _jsonl(raw / "run-journal.jsonl")]
        _require(journal[-1].get("kind") == "session_closed"
                 and sum(row.get("kind") == "session_closed" for row in journal) == 1,
                 "session_not_closed", "one final native Recorder close is required")
        _require(recording.get("continuous_schema_version") in (None, 1),
                 "continuous_recording_schema_invalid", "unsupported continuous schema")
        _require(recording.get("close_schema_version") in (None, 1),
                 "session_close_schema_invalid", "unsupported close schema")
        receipt = None
        if recording.get("close_schema_version") == 1:
            receipt_path = raw / "session-close-receipt.json"
            _require(receipt_path.is_file(), "session_close_receipt_invalid_or_missing", "durable close seal missing")
            receipt = _load_json(receipt_path)
            _require(receipt.get("schema") == "sts2.human-annotator/session-close-1"
                     and receipt.get("session_id") == session and receipt.get("timeline_id") == timeline
                     and receipt.get("status") == "closed" and bool(receipt.get("closed_at")),
                     "session_close_receipt_invalid_or_missing", "durable close seal differs")
            try:
                datetime.fromisoformat(str(receipt["closed_at"]).replace("Z", "+00:00"))
            except ValueError as error:
                raise BundleVerificationError("session_close_receipt_invalid_or_missing", "invalid close timestamp") from error
        rows = [row for _, row in _jsonl(export_path)]
        _require(len(rows) == count, "canonical_count_mismatch", "canonical row count differs")
        text_inputs = verify_human_text_inputs(raw, recording, receipt, run_ids)
        expected_runs = {row["run_id"] for row in rows}
        expected_runs.update(row["run_id"] for row in journal if row.get("run_id") is not None)
        _require(set(run_ids) == expected_runs, "run_ids_mismatch", "canonical and journal run IDs differ")
        failed = [row for _, row in _jsonl(raw / "invalidations.jsonl")]
        _require(len(failed) == invalidations, "invalidation_count_mismatch", "invalidation count differs")
        for row in failed:
            _require(row.get("schema_version") == 2
                     and row.get("schema") == "sts2.human-annotator/invalidation-2"
                     and row.get("session_id") == session,
                     "invalidation_identity_mismatch", "invalidation envelope differs")
        trace = [row for _, row in _jsonl(raw / "semantic-boundary-trace.jsonl")]
        recovery = verify_recovery(raw, recording, journal, trace)
        dispositions = _verify_references(raw, recording, profile, trace, rows)
        accepted_ids = {event["action"]["action_witness_id"] for event in trace
                        if event.get("kind") == "action_accepted"}
        canonical_ids = {row["action_witness_id"] for row in rows}
        _require(recording.get("disposition_schema_version") in (None, 1),
                 "invalidation_disposition_schema_mismatch", "unsupported disposition schema")
        for row in failed:
            occurrence = row.get("human_occurrence")
            if occurrence is not None:
                _require(isinstance(occurrence, dict) and all(isinstance(occurrence.get(key), str)
                         and occurrence[key].strip() for key in ("occurrence_id", "native_action_type", "family", "verb", "native_mechanism")),
                         "human_occurrence_identity_invalid", "Human occurrence identity missing")
                _require(occurrence.get("disposition") == "failed_closed",
                         "human_occurrence_disposition_invalid", "Human occurrence disposition differs")
                operands = occurrence.get("native_operands")
                _require(isinstance(operands, dict) and all(isinstance(key, str) and key.strip()
                         and isinstance(value, str) and value.strip() for key, value in operands.items()),
                         "human_occurrence_operands_invalid", "Human occurrence operands are invalid")
            disposition = row.get("disposition")
            _require(disposition in (None, "diagnostic", "unsupported", "failed_closed")
                     and (recording.get("disposition_schema_version") != 1 or disposition is not None),
                     "invalidation_disposition_schema_mismatch", "invalid or missing disposition")
            failure = row.get("decision_failure")
            if failure is None:
                _require(disposition != "failed_closed", "invalidation_decision_failure_missing", "exact failure identity missing")
                continue
            _require(isinstance(failure, dict) and row.get("disposition") == "failed_closed"
                     and failure.get("kind") in {"capture", "persistence"}
                     and bool(failure.get("action_family")) and bool(failure.get("decision_witness_id")),
                     "invalidation_decision_failure_invalid", "invalid failure disposition")
            witness = failure["decision_witness_id"]
            if failure["kind"] == "capture":
                _require(isinstance(row.get("human_occurrence"), dict)
                         and row["human_occurrence"].get("occurrence_id") == witness,
                         "invalidation_decision_failure_invalid", "capture occurrence differs")
            else:
                _require(witness in accepted_ids and witness not in canonical_ids,
                         "invalidation_persistence_action_mismatch", "persistence loss contradicts trace/canonical")
        _verify_projection_coverage(profile, trace, rows, failed, journal)
        # A failure-only session is transportable. No zero-failure or Full-Run
        # eligibility gate belongs in the evidence logistics layer.
        summary = _current_summary(recording, trace, rows, failed, journal, run_ids)
        if recording.get("close_schema_version") == 1:
            summary["closed_at"] = receipt["closed_at"]
        summary["recovery"] = None if recovery is None else {
            "disposition": recovery["disposition"], "original_inventory_sha256": recovery["original_inventory_sha256"]}
        summary["counts"]["compatibility_valid"] = compatibility_count
        summary["counts"]["compatibility_invalid"] = audit["invalid_records"]
        return HumanSessionBundleV3(directory, manifest, profile, session, timeline,
            worker, str(manifest["campaign_id"]), profile_id, content_id,
            _sha256_file(checksums_path), export_sha, count, run_ids, invalidations, dispositions, summary,
            recording.get("text_input_schema_version"), tuple(freeze_json(row) for row in text_inputs))


def _verify_references(
    raw: Path, recording: Mapping[str, Any], profile: Mapping[str, Any],
    trace: list[dict[str, Any]], canonical: list[dict[str, Any]],
) -> Mapping[str, int]:
    cache: dict[str, dict[str, Any]] = {}
    verified_reads: set[str] = set()

    def resolve(ref: Mapping[str, Any], prefix: str) -> dict[str, Any]:
        digest = _digest(ref, "content_sha256")
        relative = _text(ref, "object_ref")
        _require(relative == f"{prefix}/sha256/{digest[:2]}/{digest}.json",
                 "object_reference_invalid", relative)
        if relative not in cache:
            path = raw / relative
            _require(_sha256_file(path) == digest, "object_digest_mismatch", relative)
            cache[relative] = _load_json(path)
        return cache[relative]

    def frame(ref: Mapping[str, Any], role: str | None = None) -> None:
        value = resolve(ref, "semantic-frames")
        snapshot_id = _text(ref, "snapshot_id")
        snapshot = _object(value, "snapshot")
        _require(value.get("snapshot_id") == snapshot_id and snapshot.get("snapshot_id") == snapshot_id,
                 "frame_identity_mismatch", snapshot_id)
        reads = value.get("reads")
        _require(isinstance(reads, list), "read_evidence_invalid", snapshot_id)
        seen: set[str] = set()
        for read in reads:
            _require(isinstance(read, dict) and read.get("schema_version") == 2
                     and read.get("schema") == "sts2.human-annotator/read-evidence-2"
                     and read.get("snapshot_id") == snapshot_id,
                     "read_identity_mismatch", snapshot_id)
            kind = _text(read, "kind")
            _require(kind not in seen, "read_evidence_duplicate", kind)
            seen.add(kind)
            if read.get("status") == "materialized":
                digest = _digest(read, "payload_sha256")
                relative = _text(read, "payload_ref")
                _require(relative == f"blobs/sha256/{digest[:2]}/{digest}.json",
                         "read_blob_path_invalid", relative)
                if relative not in verified_reads:
                    _require(_sha256_file(raw / relative) == digest, "read_blob_missing_or_changed", relative)
                    verified_reads.add(relative)
            else:
                _require(read.get("status") in {"not_available", "failed", "stale"},
                         "read_status_invalid", kind)
        if role is not None:
            required = _required_reads(profile, value.get("interaction_kind"))[role]
            present = {read["kind"] for read in reads if read.get("status") == "materialized"}
            _require(required <= present, "required_read_missing", str(sorted(required - present)))

    def catalog(ref: Mapping[str, Any], action: Mapping[str, Any]) -> None:
        value = resolve(ref, "semantic-action-spaces")
        _require(ACTION_SPACE_SCHEMAS.get(value.get("schema_version")) == value.get("schema"),
                 "action_space_schema_invalid", str(value.get("schema")))
        for key in ("action_witness_id", "semantic_state_digest", "semantic_catalog_digest"):
            _require(value.get(key) == ref.get(key), "action_space_reference_mismatch", key)
        _require(value.get("action_witness_id") == action.get("action_witness_id"),
                 "action_space_action_mismatch", "action witness differs")
        # Native digests name the provider's projection; its serializer and
        # action-key hashing are not the wire-object content hash. Preserve the
        # exact digest links and verified object bytes, never reinterpret them.
        phase = value.get("phase")
        queued = action.get("native_mechanism") == "game_action" or action.get("native_queue_id") is not None
        _require(phase == "before_execution" if queued else phase in {
            "before_execution", "before_native_action_admission"},
            "queued_action_requires_execution_action_space", "native binding phase differs")
        selected = [item for item in value.get("actions", [])
                    if item.get("key") == value.get("observed_action_key")]
        _require(value.get("status") == "captured" and value.get("observed_membership") == "exact_once"
                 and value.get("observed_match_count") == 1 and len(selected) == 1,
                 "action_space_membership_invalid", "selected action is not exact-once")
        bound, native = action.get("bound_action"), action.get("native_input")
        _require((bound is None) != (native is None), "action_binding_invalid", "expected one binding")
        if native is not None:
            _require(value.get("human_bound_action_id") is None
                     and value.get("human_native_action_key") == native.get("action_key")
                     and value.get("observed_action_key") == native.get("action_key")
                     and all(selected[0].get(key) == native.get(key)
                             for key in ("verb", "subject_referent_id", "arguments")),
                     "native_input_binding_mismatch", "native input differs from exact selection")
        else:
            _require(value.get("human_native_action_key") is None
                     and value.get("human_bound_action_id") == bound.get("bound_action_id"),
                     "bound_action_binding_mismatch", "public binding differs")

    accepted: dict[str, dict[str, Any]] = {}
    decisions: dict[str, dict[str, Any]] = {}
    decision_actions: dict[str, dict[str, Any]] = {}
    proofs: dict[str, list[dict[str, Any]]] = {}
    dispositions: Counter[str] = Counter()
    terminals_by_action: Counter[str] = Counter()
    terminal_kinds = {"transition_proved", "transition_unknown", "action_cancelled_before_start",
                      "action_cancelled_after_start", "action_aborted_before_commit"}
    known_kinds = terminal_kinds | {
        "action_accepted", "action_started", "action_paused_for_player_choice", "action_ready_to_resume",
        "action_resumed", "action_finished", "native_commit_observed", "native_continuation_observed",
        "native_human_continuation_observed", "boundary_observed"}
    previous = 0
    for event in trace:
        _require(event.get("schema") == "sts2.human-annotator/semantic-evidence-event-4"
                 and event.get("schema_version") == 4
                 and event.get("session_id") == recording.get("session_id")
                 and event.get("timeline_id") == recording.get("timeline_id"),
                 "trace_identity_mismatch", "current trace envelope differs")
        sequence = _nonnegative(event.get("sequence"), "trace sequence")
        _require(sequence > previous, "trace_sequence_invalid", "trace sequence must increase")
        previous = sequence
        action = _object(event, "action")
        witness = _text(action, "action_witness_id")
        kind = _text(event, "kind")
        _require(kind in known_kinds, "trace_kind_invalid", kind)
        _require(event.get("run_id") == action.get("run_id"), "trace_run_identity_mismatch", witness)
        if kind == "action_accepted":
            _require(witness not in accepted, "trace_duplicate_acceptance", witness)
            accepted[witness] = dict(action)
            decision = action.get("decision")
            if recording.get("decision_schema_version") is not None:
                _require(isinstance(decision, dict)
                         and decision.get("schema_version") == recording.get("decision_schema_version"),
                         "decision_schema_mismatch", witness)
            if decision is not None:
                _decision_identity(decision, action)
                decision_id = _text(decision, "decision_id")
                _require(decision_id not in decisions, "decision_duplicate", decision_id)
                parent_id = decision.get("parent_decision_id")
                if parent_id is not None:
                    _require(parent_id in decisions
                             and decisions[parent_id].get("causal_root_id") == decision.get("causal_root_id")
                             and decision_actions[parent_id].get("run_id") == action.get("run_id")
                             and decision_actions[parent_id].get("action_sequence", 0) < action.get("action_sequence", 0),
                             "decision_parent_mismatch", decision_id)
                decisions[decision_id] = decision
                decision_actions[decision_id] = dict(action)
        else:
            _require(witness in accepted and accepted[witness] == action,
                     "trace_action_drift", witness)
        if kind in terminal_kinds:
            dispositions[kind] += 1
            terminals_by_action[witness] += 1
        if kind == "transition_proved":
            proofs.setdefault(witness, []).append(event)
        for field in ("human_observation_ref", "execution_pre_ref", "successor_ref"):
            if event.get(field) is not None:
                frame(_object(event, field))
        boundary = event.get("boundary")
        if boundary is not None and boundary.get("state_ref") is not None:
            frame(_object(boundary, "state_ref"))
        if event.get("execution_semantic_action_space_ref") is not None:
            catalog(_object(event, "execution_semantic_action_space_ref"), action)

    for witness in accepted:
        _require(terminals_by_action[witness] == 1, "trace_action_disposition_not_exactly_one", witness)
    seen: set[str] = set()
    for row in canonical:
        _require(CANONICAL_SCHEMAS.get(row.get("schema_version")) == row.get("schema")
                 and row.get("collection_mode") == "causal_human_native_observation"
                 and row.get("proof_status") == "canonical_s_a_s_prime"
                 and row.get("session_id") == recording.get("session_id")
                 and row.get("timeline_id") == recording.get("timeline_id"),
                 "canonical_identity_mismatch", "canonical envelope differs")
        transition_id = _text(row, "transition_id")
        _require(transition_id not in seen, "canonical_duplicate", transition_id)
        seen.add(transition_id)
        matches = proofs.get(_text(row, "action_witness_id"), [])
        _require(len(matches) == 1, "canonical_proof_missing_or_duplicate", transition_id)
        proof = matches[0]
        action = _object(proof, "action")
        _require(row.get("decision") == action.get("decision")
                 and row.get("action") == action.get("bound_action")
                 and row.get("native_input") == action.get("native_input")
                 and row.get("run_id") == action.get("run_id")
                 and row.get("action_sequence") == action.get("action_sequence")
                 and row.get("native_mechanism") == action.get("native_mechanism")
                 and transition_id == "canonical-" + str(action.get("record_id")),
                 "canonical_action_mismatch", transition_id)
        for canonical_key, trace_key in (("pre_state_ref", "execution_pre_ref"), ("successor_ref", "successor_ref")):
            ref = _object(row, canonical_key)
            _require(ref == proof.get(trace_key), "canonical_frame_mismatch", transition_id)
            frame(ref, "pre" if canonical_key == "pre_state_ref" else "successor")
        _require(row["pre_state_ref"]["snapshot_id"] != row["successor_ref"]["snapshot_id"],
                 "canonical_successor_not_advanced", transition_id)
        if row.get("action_space_authority") == "native_semantic_execution":
            ref = _object(row, "execution_semantic_action_space_ref")
            _require(ref == proof.get("execution_semantic_action_space_ref"),
                     "canonical_catalog_mismatch", transition_id)
            catalog(ref, action)
        else:
            _require(row.get("action_space_authority") == "public_bound_actions"
                     and row.get("native_mechanism") == "direct_ui_commit"
                     and row.get("native_input") is None,
                     "canonical_action_space_authority_invalid", transition_id)
            pre = resolve(_object(row, "pre_state_ref"), "semantic-frames")
            snapshot = _object(pre, "snapshot")
            public = _object(snapshot, "bound_actions")
            actions = public.get("actions", [])
            selected = _object(row, "action")
            _require(snapshot.get("completeness", {}).get("status") == "complete"
                     and public.get("status") == "complete" and pre.get("catalog_count") == len(actions)
                     and sum(all(item.get(key) == selected.get(key)
                                 for key in ("bound_action_id", "verb", "subject_referent_id"))
                             and _public_arguments_match(item.get("arguments"), selected.get("arguments", {}))
                             for item in actions) == 1,
                     "canonical_public_action_membership_invalid", transition_id)
    return dict(sorted(dispositions.items()))


def _public_arguments_match(arguments: Any, expected: Mapping[str, Any]) -> bool:
    if not isinstance(arguments, list):
        return not expected
    actual = {item.get("role"): item.get("referent_id") for item in arguments if isinstance(item, dict)}
    return len(actual) == len(arguments) and all(actual.keys()) and all(actual.values()) and actual == expected


def _decision_identity(decision: Mapping[str, Any], action: Mapping[str, Any]) -> None:
    for key in ("decision_id", "causal_root_id", "surface", "family"):
        _text(decision, key)
    kind = decision.get("decision_kind")
    parent = decision.get("parent_decision_id")
    root = decision.get("causal_root_id")
    witness = action.get("action_witness_id")
    _require(kind in {"root", "nested_selector", "native_selector"}, "decision_kind_invalid", str(kind))
    if kind == "root":
        _require(parent is None and root == witness, "decision_root_lineage_invalid", str(witness))
    elif kind == "nested_selector":
        _require(bool(parent) and parent != decision.get("decision_id") and root != witness
                 and bool(decision.get("native_owner_witness_id")),
                 "decision_nested_lineage_invalid", str(witness))
    else:
        origin = decision.get("native_origin")
        native = action.get("native_witness", {})
        arguments = native.get("argument_witness_ids", {})
        _require(decision.get("schema_version") == 2 and parent is None and root != witness
                 and bool(decision.get("native_owner_witness_id")) and isinstance(origin, dict)
                 and origin.get("native_action_witness_id") == root
                 and all(origin.get(key) for key in ("native_action_type", "choice_context_type", "factory_mechanism"))
                 and action.get("native_mechanism") == "direct_ui_commit" and action.get("native_queue_id") is None
                 and native.get("origin") == "native_selector_input" and arguments.get("native_origin") == root
                 and arguments.get("selector_owner") == decision.get("native_owner_witness_id"),
                 "decision_native_origin_invalid", str(witness))
    if kind != "native_selector":
        _require(decision.get("native_origin") is None, "decision_unexpected_native_origin", str(witness))


def _verify_projection_coverage(
    profile: Mapping[str, Any], trace: list[dict[str, Any]], canonical: list[dict[str, Any]],
    invalidations: list[dict[str, Any]], journal: list[dict[str, Any]],
) -> None:
    proofs = [row for row in trace if row.get("kind") == "transition_proved"]
    canonical_counts = Counter(row["action_witness_id"] for row in canonical)
    failures = [row for row in invalidations if isinstance(row.get("decision_failure"), dict)
                and row["decision_failure"].get("kind") == "persistence"]
    omissions = [row for row in journal if row.get("kind") == "canonical_projection_unsupported"]
    families = set(profile["supported_action_families"])
    for proof in proofs:
        action = proof["action"]
        witness = action["action_witness_id"]
        decision = action.get("decision")
        family = None if decision is None else (
            "nested_selector.decision" if decision.get("decision_kind") in {"nested_selector", "native_selector"}
            else decision.get("family"))
        failed = [row for row in failures if row["decision_failure"].get("decision_witness_id") == witness]
        unsupported = [row for row in omissions if row.get("record_id") == action.get("record_id")]
        failure_valid = len(failed) == 1 and failed[0].get("run_id") == action.get("run_id") \
            and failed[0]["decision_failure"].get("action_family") in families \
            and (family is None or failed[0]["decision_failure"].get("action_family") == family)
        unsupported_valid = len(unsupported) == 1 and family is not None and family not in families \
            and unsupported[0].get("run_id") == action.get("run_id") and unsupported[0].get("detail") == family
        _require(canonical_counts[witness] + int(failure_valid) + int(unsupported_valid) == 1
                 and (not failed or failure_valid) and (not unsupported or unsupported_valid),
                 "proved_action_projection_disposition_missing_or_ambiguous", witness)
    for omission in omissions:
        _require(any(row["action"].get("record_id") == omission.get("record_id") for row in proofs),
                 "unsupported_projection_proof_missing", str(omission.get("record_id")))
