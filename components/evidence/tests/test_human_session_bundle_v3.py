from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from sts2_platform_evidence import HumanSessionBundleV3, verify_human_session_bundle
from sts2_platform_evidence.human_text_inputs import verify_human_text_inputs
from tests import test_human_session_bundle_v2 as v2
from tests.test_human_session_bundle_v2 import canonical, sha_bytes, sha_file


class HumanSessionBundleV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, path: Path, value: Any) -> None:
        path.write_text(canonical(value) + "\n", encoding="utf-8")

    def _stream(self, path: Path, rows: list[dict[str, Any]]) -> None:
        path.write_text("".join(canonical(row) + "\n" for row in rows), encoding="utf-8")

    def _rows(self, path: Path) -> list[dict[str, Any]]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def _bundle(self, native_input: bool = False, failed_only: bool = False) -> Path:
        # The compatibility fixture establishes a real schema-ordered profile
        # and Read blobs; V3 retains those bytes but consumes only canonical.
        helper = v2.HumanSessionBundleV2Tests()
        helper.root = self.root
        bundle = helper._bundle("canonical-bundle")
        raw = bundle / "raw"
        old = json.loads((raw / "run-0001.jsonl").read_text(encoding="utf-8"))
        recording = json.loads((raw / "recording-manifest.json").read_text(encoding="utf-8"))
        recording["decision_schema_version"] = 2
        self._write(raw / "recording-manifest.json", recording)
        selected = {"bound_action_id": "bound-exact", "verb": "play",
                    "subject_referent_id": "card-exact", "arguments": {}}

        def frame(phase: str) -> dict[str, Any]:
            value = old[phase]
            value["snapshot"]["completeness"] = {"status": "complete"}
            value["snapshot"]["bound_actions"] = {"status": "complete", "actions": [selected]}
            value["catalog_count"] = 1
            return self._object(raw, "semantic-frames", value) | {"snapshot_id": value["snapshot_id"]}

        pre, successor = frame("pre"), frame("successor")
        decision = {"schema_version": 2, "decision_id": "decision-exact", "causal_root_id": "action-exact",
                    "parent_decision_id": None, "surface": "combat_turn", "family": "ordinary_combat.play_card",
                    "decision_kind": "root", "native_owner_witness_id": None}
        action = {"action_witness_id": "action-exact", "record_id": "record-exact", "run_id": "run-0001",
                  "action_sequence": 1, "native_mechanism": "game_action" if native_input else "direct_ui_commit",
                  "native_queue_id": 1 if native_input else None, "bound_action": None if native_input else selected,
                  "native_input": {"action_key": "native-exact", "verb": "play", "subject_referent_id": "card-exact",
                                   "arguments": {}} if native_input else None, "decision": decision}
        execution_ref = None
        if native_input:
            space = {"schema_version": 3, "schema": "sts2.human-annotator/execution-semantic-action-space-3",
                     "action_witness_id": "action-exact", "phase": "before_execution", "status": "captured",
                     "semantic_state_digest": "a" * 64, "semantic_catalog_digest": "b" * 64,
                     "semantic_state": {"player_phase": "Play"},
                     "actions": [{"key": "native-exact", "verb": "play", "subject_referent_id": "card-exact", "arguments": {}}],
                     "observed_action_key": "native-exact", "observed_membership": "exact_once", "observed_match_count": 1,
                     "human_bound_action_id": None, "human_native_action_key": "native-exact"}
            execution_ref = self._object(raw, "semantic-action-spaces", space) | {
                key: space[key] for key in ("action_witness_id", "semantic_state_digest", "semantic_catalog_digest")}
        common = {"schema_version": 4, "schema": "sts2.human-annotator/semantic-evidence-event-4",
                  "session_id": recording["session_id"], "timeline_id": recording["timeline_id"],
                  "run_id": "run-0001", "action": action, "human_observation_ref": pre}
        trace = [common | {"sequence": 1, "kind": "action_accepted"},
                 common | {"sequence": 2, "kind": "transition_proved", "execution_pre_ref": pre,
                           "successor_ref": successor, "execution_semantic_action_space_ref": execution_ref}]
        row = {"schema_version": 3, "schema": "sts2.human-annotator/canonical-transition-evidence-3",
               "collection_mode": "causal_human_native_observation", "proof_status": "canonical_s_a_s_prime",
               "transition_id": "canonical-record-exact", "session_id": recording["session_id"],
               "timeline_id": recording["timeline_id"], "run_id": "run-0001", "action_sequence": 1,
               "action_witness_id": "action-exact", "native_mechanism": action["native_mechanism"],
               "action": action["bound_action"], "native_input": action["native_input"], "decision": decision,
               "pre_state_ref": pre, "successor_ref": successor,
               "action_space_authority": "native_semantic_execution" if native_input else "public_bound_actions",
               "execution_semantic_action_space_ref": execution_ref}
        if failed_only:
            trace[1] = common | {"sequence": 2, "kind": "transition_unknown"}
        self._stream(raw / "semantic-boundary-trace.jsonl", trace)
        self._stream(raw / "canonical-transitions.jsonl", [] if failed_only else [row])
        journal = self._rows(raw / "run-journal.jsonl")
        journal[0]["kind"] = "session_started"
        journal.append(journal[0] | {"sequence": 2, "kind": "session_closed"})
        self._stream(raw / "run-journal.jsonl", journal)
        (raw / "run-0001.jsonl").unlink()
        (bundle / "export" / "decisions.jsonl").unlink()
        manifest_path = bundle / "session-bundle-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(schema_version=3, schema="sts2.human-annotator/session-bundle-3", canonical_count=0 if failed_only else 1)
        manifest.pop("record_count")
        manifest["packer"] = {"product": "STS2 Native UI Human Annotator Tool", "version": "fixture", "source_revision": "c" * 40}
        self._write(manifest_path, manifest)
        audit = json.loads((bundle / "audit" / "audit-report.json").read_text(encoding="utf-8"))
        audit.update(schema="sts2.human-annotator/session-bundle-audit-3", canonical_count=manifest["canonical_count"], valid_records=0, errors={})
        self._write(bundle / "audit" / "audit-report.json", audit)
        self._reseal(bundle)
        return bundle

    def _object(self, raw: Path, prefix: str, value: Any) -> dict[str, Any]:
        payload = canonical(value).encode()
        digest = sha_bytes(payload)
        relative = f"{prefix}/sha256/{digest[:2]}/{digest}.json"
        path = raw / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return {"content_sha256": digest, "object_ref": relative}

    def _reseal(self, bundle: Path) -> None:
        raw = bundle / "raw"
        export = bundle / "export" / "canonical-transitions.jsonl"
        export.write_bytes((raw / "canonical-transitions.jsonl").read_bytes())
        manifest_path = bundle / "session-bundle-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        audit_path = bundle / "audit" / "audit-report.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        manifest["export_sha256"] = sha_file(export)
        identity = {key: manifest[key] for key in ("schema", "session_id", "timeline_id", "capture_profile_id",
                    "capture_profile_sha256", "campaign_id", "worker_id", "human_origin_attestation",
                    "canonical_count", "run_ids", "export_sha256")}
        identity["raw_file_sha256"] = {path.relative_to(raw).as_posix(): sha_file(path)
                                      for path in sorted(raw.rglob("*")) if path.is_file()}
        identity["audit_sha256"] = sha_file(audit_path)
        identity["packer"] = manifest["packer"]
        identity["audit"] = {key: audit[key] for key in ("status", "valid_records", "invalid_records", "invalidations", "canonical_count")}
        manifest["content_identity"] = identity
        manifest["bundle_content_id"] = sha_bytes(canonical(identity).encode())
        self._write(manifest_path, manifest)
        checksums = [f"{sha_file(path)}  {path.relative_to(bundle).as_posix()}"
                     for path in sorted(bundle.rglob("*")) if path.is_file() and path.name != "checksums.sha256"]
        (bundle / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")

    def test_canonical_only_bundle_has_no_compatibility_record_requirement(self) -> None:
        bundle = self._bundle()
        result = verify_human_session_bundle(bundle)
        self.assertTrue(result.passed, result.findings)
        self.assertIsInstance(result.require_value(), HumanSessionBundleV3)
        self.assertEqual(result.require_value().canonical_count, 1)
        self.assertEqual(result.require_value().export_path.name, "canonical-transitions.jsonl")

    def test_native_input_canonical_bundle_is_first_class(self) -> None:
        result = verify_human_session_bundle(self._bundle(native_input=True))
        self.assertTrue(result.passed, result.findings)

    def test_zero_canonical_unknown_session_remains_transportable(self) -> None:
        result = verify_human_session_bundle(self._bundle(failed_only=True))
        self.assertTrue(result.passed, result.findings)
        self.assertEqual(result.require_value().canonical_count, 0)
        self.assertEqual(result.require_value().dispositions, {"transition_unknown": 1})

    def test_outer_rehash_does_not_hide_changed_canonical_action(self) -> None:
        bundle = self._bundle()
        path = bundle / "raw" / "canonical-transitions.jsonl"
        rows = self._rows(path)
        rows[0]["action"]["bound_action_id"] = "wrong"
        self._stream(path, rows)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "canonical_action_mismatch")

    def test_missing_proof_fails_even_with_consistent_outer_hashes(self) -> None:
        bundle = self._bundle()
        path = bundle / "raw" / "semantic-boundary-trace.jsonl"
        rows = self._rows(path)
        rows[1]["kind"] = "transition_unknown"
        self._stream(path, rows)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "canonical_proof_missing_or_duplicate")

    def test_invalid_parent_cannot_be_hidden_in_matching_canonical_and_trace(self) -> None:
        bundle = self._bundle()
        for filename in ("semantic-boundary-trace.jsonl", "canonical-transitions.jsonl"):
            path = bundle / "raw" / filename
            rows = self._rows(path)
            for row in rows:
                decision = row["action"]["decision"] if "kind" in row else row["decision"]
                decision["parent_decision_id"] = "missing-parent"
                decision["decision_kind"] = "nested_selector"
                decision["causal_root_id"] = "missing-root"
                decision["native_owner_witness_id"] = "selector-owner"
            self._stream(path, rows)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "decision_parent_mismatch")

    def test_wrong_phase_is_not_repaired_by_transport(self) -> None:
        bundle = self._bundle(native_input=True)
        self._change_catalog(bundle, "phase", "before_native_action_admission")
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "queued_action_requires_execution_action_space")

    def test_native_selection_mismatch_fails(self) -> None:
        bundle = self._bundle(native_input=True)
        self._change_catalog(bundle, "human_native_action_key", "wrong-input")
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "native_input_binding_mismatch")

    def _recover_bundle(self, bundle: Path) -> None:
        raw = bundle / "raw"
        recording = json.loads((raw / "recording-manifest.json").read_text())
        recording.update(recovery_schema_version=1, close_schema_version=1)
        self._write(raw / "recording-manifest.json", recording)
        trace = self._rows(raw / "semantic-boundary-trace.jsonl")[:1]
        journal = self._rows(raw / "run-journal.jsonl")[:1]
        self._stream(raw / "semantic-boundary-trace.jsonl", trace)
        self._stream(raw / "run-journal.jsonl", journal)
        (raw / "session-close-receipt.json").unlink(missing_ok=True)
        inventory = {p.relative_to(raw).as_posix(): {"bytes": p.stat().st_size, "sha256": sha_file(p)}
                     for p in raw.rglob("*") if p.is_file()}
        identity = sha_bytes(canonical(inventory).encode())
        from sts2_platform_evidence.interrupted_recovery import SCHEMA
        self._write(raw / "recording-recovery.json", {
            "schema": SCHEMA, "original_inventory_sha256": identity, "original_files": inventory,
            "disposition": "interrupted_partial", "unknowns_added": 1})
        self._write(raw / "session-close-receipt.json", {
            "schema": "sts2.human-annotator/session-close-1", "session_id": recording["session_id"],
            "timeline_id": recording["timeline_id"], "closed_at": "2026-09-17T01:00:00Z",
            "status": "closed", "recovery": SCHEMA})
        self._stream(raw / "semantic-boundary-trace.jsonl", trace + [trace[0] | {
            "sequence": 2, "kind": "transition_unknown", "proof_status": "process_interrupted"}])
        self._stream(raw / "run-journal.jsonl", journal + [journal[0] | {
            "sequence": 2, "kind": "recording_interrupted",
            "detail": "Offline recovery of immutable source inventory " + identity},
            journal[0] | {"sequence": 3, "kind": "session_closed"}])
        self._reseal(bundle)

    def test_recovered_unknown_only_bundle_preserves_prefix_and_rejects_tampering(self) -> None:
        bundle = self._bundle(failed_only=True)
        raw = bundle / "raw"
        self._recover_bundle(bundle)
        result = verify_human_session_bundle(bundle)
        self.assertEqual(result.status, "pass", result.findings)
        self.assertEqual(result.require_value().summary["recovery"]["disposition"], "interrupted_partial")
        # Rehashing the outer bundle must not launder changed original bytes.
        with (raw / "capture-profile.json").open("a") as file:
            file.write(" ")
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "recording_recovery_invalid")

    def test_recovery_marker_without_provenance_is_rejected(self) -> None:
        bundle = self._bundle(failed_only=True)
        path = bundle / "raw" / "semantic-boundary-trace.jsonl"
        trace = self._rows(path)
        trace[-1]["proof_status"] = "process_interrupted"
        self._stream(path, trace)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "recording_recovery_invalid")

    def _change_catalog(self, bundle: Path, key: str, value: Any) -> None:
        raw = bundle / "raw"
        path = next((raw / "semantic-action-spaces").rglob("*.json"))
        content = json.loads(path.read_text(encoding="utf-8"))
        content[key] = value
        ref = self._object(raw, "semantic-action-spaces", content)
        for filename in ("semantic-boundary-trace.jsonl", "canonical-transitions.jsonl"):
            target = raw / filename
            rows = self._rows(target)
            for row in rows:
                if row.get("execution_semantic_action_space_ref") is not None:
                    row["execution_semantic_action_space_ref"].update(ref)
            self._stream(target, rows)
        self._reseal(bundle)

    def _remove_canonical(self, bundle: Path) -> None:
        (bundle / "raw" / "canonical-transitions.jsonl").write_text("", encoding="utf-8")
        for path in (bundle / "session-bundle-manifest.json", bundle / "audit" / "audit-report.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            value["canonical_count"] = 0
            self._write(path, value)

    def test_proved_but_failed_canonical_append_is_transportable_and_exactly_accounted(self) -> None:
        bundle = self._bundle()
        raw = bundle / "raw"
        self._remove_canonical(bundle)
        recording = json.loads((raw / "recording-manifest.json").read_text(encoding="utf-8"))
        failure = {"schema_version": 2, "schema": "sts2.human-annotator/invalidation-2",
                   "session_id": recording["session_id"], "run_id": "run-0001", "disposition": "failed_closed",
                   "decision_failure": {"decision_witness_id": "action-exact", "kind": "persistence",
                                        "action_family": "ordinary_combat.play_card"}}
        self._stream(raw / "invalidations.jsonl", [failure])
        audit_path = bundle / "audit" / "audit-report.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit["invalidations"] = 1
        self._write(audit_path, audit)
        self._reseal(bundle)
        result = verify_human_session_bundle(bundle)
        self.assertTrue(result.passed, result.findings)
        self.assertEqual(result.require_value().canonical_count, 0)
        failure["decision_failure"]["action_family"] = "unrelated_family"
        self._stream(raw / "invalidations.jsonl", [failure])
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "proved_action_projection_disposition_missing_or_ambiguous")
        self._stream(raw / "invalidations.jsonl", [])
        audit["invalidations"] = 0
        self._write(audit_path, audit)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "proved_action_projection_disposition_missing_or_ambiguous")

    def test_outside_profile_proof_requires_exact_durable_unsupported_disposition(self) -> None:
        bundle = self._bundle()
        raw = bundle / "raw"
        self._remove_canonical(bundle)
        trace = self._rows(raw / "semantic-boundary-trace.jsonl")
        for event in trace:
            event["action"]["decision"]["family"] = "outside.capture.profile"
        self._stream(raw / "semantic-boundary-trace.jsonl", trace)
        journal = self._rows(raw / "run-journal.jsonl")
        journal[-1]["sequence"] = 3
        journal.insert(1, journal[0] | {"sequence": 2, "kind": "canonical_projection_unsupported",
                                     "record_id": "record-exact", "detail": "outside.capture.profile"})
        self._stream(raw / "run-journal.jsonl", journal)
        self._reseal(bundle)
        result = verify_human_session_bundle(bundle)
        self.assertTrue(result.passed, result.findings)
        journal[1]["detail"] = "ordinary_combat.play_card"
        self._stream(raw / "run-journal.jsonl", journal)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "proved_action_projection_disposition_missing_or_ambiguous")

    def test_closed_trace_requires_one_known_terminal_per_accepted_action(self) -> None:
        bundle = self._bundle(failed_only=True)
        path = bundle / "raw" / "semantic-boundary-trace.jsonl"
        rows = self._rows(path)
        self._stream(path, rows[:1])
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "trace_action_disposition_not_exactly_one")
        self._stream(path, rows + [rows[1] | {"sequence": 3}])
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "trace_action_disposition_not_exactly_one")
        rows[1]["kind"] = "unrecognized_success"
        self._stream(path, rows)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "trace_kind_invalid")

    def test_compatibility_audit_count_is_typed_and_bound_to_raw_records(self) -> None:
        bundle = self._bundle()
        path = bundle / "audit" / "audit-report.json"
        audit = json.loads(path.read_text(encoding="utf-8"))
        for count, code in ((-1, "count_invalid"), (True, "count_invalid"), (1, "compatibility_count_mismatch")):
            audit["valid_records"] = count
            self._write(path, audit)
            self._reseal(bundle)
            self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, code)

    def test_audit_canonical_count_requires_integer_even_when_python_equality_matches(self) -> None:
        bundle = self._bundle()
        path = bundle / "audit" / "audit-report.json"
        audit = json.loads(path.read_text(encoding="utf-8"))
        for count in (True, 1.0):
            audit["canonical_count"] = count
            self._write(path, audit)
            self._reseal(bundle)
            self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "count_invalid")

    def test_capture_failure_requires_complete_typed_occurrence_identity(self) -> None:
        bundle = self._bundle(failed_only=True)
        raw = bundle / "raw"
        session = self._rows(raw / "semantic-boundary-trace.jsonl")[0]["session_id"]
        occurrence = {"occurrence_id": "input-exact", "native_action_type": "PlayCardAction",
                      "family": "ordinary_combat.play_card", "verb": "play", "native_mechanism": "native_ui",
                      "native_operands": {}, "disposition": "failed_closed"}
        failure = {"schema_version": 2, "schema": "sts2.human-annotator/invalidation-2",
                   "session_id": session, "run_id": "run-0001", "disposition": "failed_closed",
                   "human_occurrence": occurrence,
                   "decision_failure": {"decision_witness_id": "input-exact", "kind": "capture",
                                        "action_family": "ordinary_combat.play_card"}}
        self._stream(raw / "invalidations.jsonl", [failure])
        path = bundle / "audit" / "audit-report.json"
        audit = json.loads(path.read_text(encoding="utf-8"))
        audit["invalidations"] = 1
        self._write(path, audit)
        self._reseal(bundle)
        result = verify_human_session_bundle(bundle)
        self.assertTrue(result.passed, result.findings)
        occurrence["native_mechanism"] = ""
        self._stream(raw / "invalidations.jsonl", [failure])
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "human_occurrence_identity_invalid")
        occurrence["native_mechanism"] = "native_ui"
        occurrence["native_operands"] = {"target": ""}
        self._stream(raw / "invalidations.jsonl", [failure])
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "human_occurrence_operands_invalid")

    def test_open_session_is_not_immutable_bundle(self) -> None:
        bundle = self._bundle()
        path = bundle / "raw" / "run-journal.jsonl"
        rows = self._rows(path)
        rows[-1]["kind"] = "session_close_requested"
        self._stream(path, rows)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "session_not_closed")

    def test_failure_metadata_must_match_exact_trace_and_current_schema(self) -> None:
        bundle = self._bundle(failed_only=True)
        raw = bundle / "raw"
        recording = json.loads((raw / "recording-manifest.json").read_text(encoding="utf-8"))
        recording["disposition_schema_version"] = 1
        self._write(raw / "recording-manifest.json", recording)
        failure = {"schema_version": 2, "schema": "sts2.human-annotator/invalidation-2",
                   "session_id": recording["session_id"], "disposition": "failed_closed",
                   "decision_failure": {"decision_witness_id": "action-exact", "kind": "persistence",
                                        "action_family": "ordinary_combat.play_card"}}
        self._stream(raw / "invalidations.jsonl", [failure])
        audit_path = bundle / "audit" / "audit-report.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit["invalidations"] = 1
        self._write(audit_path, audit)
        self._reseal(bundle)
        result = verify_human_session_bundle(bundle)
        self.assertTrue(result.passed, result.findings)
        failure["decision_failure"]["decision_witness_id"] = "unknown-action"
        self._stream(raw / "invalidations.jsonl", [failure])
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "invalidation_persistence_action_mismatch")
        failure.pop("disposition")
        self._stream(raw / "invalidations.jsonl", [failure])
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "invalidation_disposition_schema_mismatch")

    def test_new_manifest_requires_exact_durable_close_seal(self) -> None:
        bundle = self._bundle()
        raw = bundle / "raw"
        recording = json.loads((raw / "recording-manifest.json").read_text(encoding="utf-8"))
        recording["close_schema_version"] = 1
        self._write(raw / "recording-manifest.json", recording)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "session_close_receipt_invalid_or_missing")
        receipt = {"schema": "sts2.human-annotator/session-close-1", "session_id": recording["session_id"],
                   "timeline_id": recording["timeline_id"], "status": "closed", "closed_at": "2026-09-11T00:00:00Z"}
        self._write(raw / "session-close-receipt.json", receipt)
        self._reseal(bundle)
        result = verify_human_session_bundle(bundle)
        self.assertTrue(result.passed, result.findings)
        receipt["session_id"] = "different-session"
        self._write(raw / "session-close-receipt.json", receipt)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "session_close_receipt_invalid_or_missing")

    def _declare_text(self, bundle: Path, rows: list[dict[str, Any]]) -> Path:
        raw = bundle / "raw"
        recording = json.loads((raw / "recording-manifest.json").read_text())
        recording.update(text_input_schema_version=1, close_schema_version=1)
        self._write(raw / "recording-manifest.json", recording)
        path = raw / "human-text-inputs.jsonl"
        self._stream(path, rows)
        self._write(raw / "session-close-receipt.json", {
            "schema": "sts2.human-annotator/session-close-1",
            "session_id": recording["session_id"], "timeline_id": recording["timeline_id"],
            "status": "closed", "closed_at": "2026-09-11T00:00:00Z",
            "human_text_input_count": len(rows), "human_text_inputs_sha256": sha_file(path),
        })
        self._reseal(bundle)
        return path

    def _text_row(self, bundle: Path) -> dict[str, Any]:
        recording = json.loads((bundle / "raw" / "recording-manifest.json").read_text())
        action = {"action_id": "card-1", "kind": "native_input", "verb": "begin_card_play",
                  "label": "Play Strike", "subject_referent_id": "card-1", "arguments": [],
                  "effect_domain": "native_input"}
        snapshot = {"schema": "sts2.player-environment/text-menu-snapshot-1",
                    "input_profile": "text-menu-v1", "protocol_version": "1.0.0",
                    "snapshot_id": "snapshot-1", "sequence": 1,
                    "observed_at": "1970-01-01T00:00:00Z", "persistent": None,
                    "status": "interactive", "completeness": {"status": "complete"},
                    "session": {"runtime_instance_id": "runtime-1",
                                "environment_fingerprint": "environment-1"},
                    "interaction": {"interaction_id": "combat-1", "kind": "combat_turn",
                                    "content_schema": "combat_turn-1",
                                    "content": {"surface": {"kind": "combat_turn"}, "context": {}}},
                    "referents": [{"referent_id": "card-1", "kind": "entity",
                                   "role": "hand_card", "state": {"visible": True}}],
                    "information_policy": {"scope": "current_page",
                                           "includes_hidden_information": False},
                    "menu": {"cursor": "root", "revision": 0,
                             "native_snapshot_id": "native-1"},
                    "menu_actions": {"status": "complete", "materialized_count": 1,
                                     "total_count": 1,
                                     "ordering_semantics": "native_order_with_fixed_information_groups",
                                     "actions": [action]}}
        digest = sha_bytes(canonical(snapshot).encode())
        artifact = {"product": "artifact", "version": "1", "source_revision": "b" * 40,
                    "source_digest_sha256": "a" * 64, "sha256": "a" * 64,
                    "module_version_id": "11111111-1111-1111-1111-111111111111"}
        return {"schema_version": 1, "schema": "sts2.human-annotator/human-text-input-1",
                "sequence": 1, "record_id": "text-1", "session_id": recording["session_id"],
                "timeline_id": recording["timeline_id"], "run_id": "run-0001",
                "observed_at": "1970-01-01T00:00:00Z", "recorded_at": "1970-01-01T00:00:01Z",
                "environment": {"game": {"version": "game", "commit": "commit",
                                          "main_assembly_sha256": "a" * 64,
                                          "main_assembly_module_version_id": "11111111-1111-1111-1111-111111111111"},
                                "connector": artifact, "annotator": artifact,
                                "player_environment_protocol": "1.0.0", "runtime_instance_id": "runtime-1",
                                "environment_fingerprint": "environment-1", "modset_status": "exact",
                                "modset_fingerprint": "a" * 64},
                "snapshot": snapshot, "snapshot_sha256": digest, "chosen_action": action,
                "mapping_status": "exact_unique", "match_count": 1,
                "mapping_basis": "text_menu_native_reference_equality",
                "native_owner_witness_id": "owner-1", "native_subject_witness_id": "subject-1",
                "native_carrier_witness_id": "carrier-1",
                "native_mechanism": "begin_card_play_exact_factory_return",
                "disposition": "accepted_input", "external_controller_active": False}

    def test_declared_text_stream_accepts_empty_and_exact_human_input(self) -> None:
        bundle = self._bundle()
        self._declare_text(bundle, [])
        empty = verify_human_session_bundle(bundle)
        self.assertTrue(empty.passed, empty.findings)
        self.assertEqual(empty.require_value().text_input_schema_version, 1)
        self.assertEqual(empty.require_value().human_text_inputs, ())
        row = self._text_row(bundle)
        self._declare_text(bundle, [row])
        result = verify_human_session_bundle(bundle)
        self.assertTrue(result.passed, result.findings)
        verified = result.require_value()
        self.assertEqual(verified.text_input_schema_version, 1)
        self.assertEqual(len(verified.human_text_inputs), 1)
        with self.assertRaises(TypeError):
            verified.human_text_inputs[0]["record_id"] = "mutated"  # type: ignore[index]
        with self.assertRaises(TypeError):
            verified.human_text_inputs[0]["snapshot"]["snapshot_id"] = "mutated"  # type: ignore[index]

    def test_declared_text_stream_requires_file_and_close_seal(self) -> None:
        bundle = self._bundle()
        path = self._declare_text(bundle, [self._text_row(bundle)])
        path.unlink()
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "human_text_input_stream_or_close_seal_missing")
        self._declare_text(bundle, [self._text_row(bundle)])
        receipt_path = bundle / "raw" / "session-close-receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["human_text_input_count"] = 0
        self._write(receipt_path, receipt)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "human_text_input_close_seal_mismatch")
        receipt["human_text_input_count"] = 1
        receipt["human_text_inputs_sha256"] = "0" * 64
        self._write(receipt_path, receipt)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "human_text_input_close_seal_mismatch")

    def test_resealed_text_stream_rejects_torn_tail_and_fabricated_result(self) -> None:
        bundle = self._bundle()
        row = self._text_row(bundle)
        path = self._declare_text(bundle, [row])
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "human_text_input_torn_tail")
        row["result"] = {"status": "applied"}
        self._declare_text(bundle, [row])
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "human_text_input_schema_invalid")

    def test_text_input_exact_binding_and_failure_are_distinct(self) -> None:
        bundle = self._bundle()
        row = self._text_row(bundle)
        row["native_carrier_witness_id"] = None
        self._declare_text(bundle, [row])
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "human_text_input_exact_mapping_missing")
        failed = self._text_row(bundle)
        failed.update(disposition="capture_failed", reason_code="pre_capture_failed",
                      mapping_status="capture_failed", match_count=0, mapping_basis="none",
                      environment=None, snapshot=None, snapshot_sha256=None,
                      chosen_action=None, native_carrier_witness_id=None)
        self._declare_text(bundle, [failed])
        result = verify_human_session_bundle(bundle)
        self.assertTrue(result.passed, result.findings)
        failed.update(disposition="rejected_or_cancelled", reason_code="native_cancelled",
                      mapping_status="exact_unique", match_count=1,
                      environment=self._text_row(bundle)["environment"],
                      snapshot=self._text_row(bundle)["snapshot"],
                      snapshot_sha256=self._text_row(bundle)["snapshot_sha256"],
                      chosen_action=self._text_row(bundle)["chosen_action"])
        self._declare_text(bundle, [failed])
        self.assertTrue(verify_human_session_bundle(bundle).passed)
        failed.update(disposition="not_mapped", reason_code="ambiguous",
                      mapping_status="ambiguous", match_count=2)
        self._declare_text(bundle, [failed])
        self.assertTrue(verify_human_session_bundle(bundle).passed)

    def test_text_stream_rejects_resealed_identity_digest_and_order_drift(self) -> None:
        bundle = self._bundle()
        original = self._text_row(bundle)
        cases = [
            ({"session_id": "other"}, "human_text_input_identity_invalid"),
            ({"snapshot_sha256": "0" * 64}, "human_text_input_snapshot_digest_mismatch"),
            ({"selected_action_id": "invented"}, "human_text_input_schema_invalid"),
            ({"sequence": 2}, "human_text_input_identity_invalid"),
        ]
        for update, code in cases:
            with self.subTest(update=update):
                row = dict(original) | update
                self._declare_text(bundle, [row])
                self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, code)
        second = dict(original) | {"sequence": 2}
        self._declare_text(bundle, [original, second])
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "human_text_input_duplicate_record_id")

    def test_resealed_complete_catalog_rejects_bad_nonchosen_action(self) -> None:
        bundle = self._bundle()
        row = self._text_row(bundle)
        row["snapshot"]["menu_actions"]["actions"].append(
            {"action_id": "other", "kind": "native_input", "verb": "begin_card_play",
             "label": "Other", "subject_referent_id": "hidden-card", "arguments": [],
             "effect_domain": "native_input"})
        row["snapshot"]["menu_actions"].update(total_count=2, materialized_count=2)
        row["snapshot_sha256"] = sha_bytes(canonical(row["snapshot"]).encode())
        self._declare_text(bundle, [row])
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "human_text_input_catalog_incomplete")

    def test_resealed_input_requires_actual_current_page_content(self) -> None:
        bundle = self._bundle()
        row = self._text_row(bundle)
        del row["snapshot"]["interaction"]["content"]
        row["snapshot_sha256"] = sha_bytes(canonical(row["snapshot"]).encode())
        self._declare_text(bundle, [row])
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "human_text_input_catalog_incomplete")

    def test_undeclared_text_stream_and_unsupported_version_fail(self) -> None:
        bundle = self._bundle()
        raw = bundle / "raw"
        (raw / "human-text-inputs.jsonl").write_bytes(b"")
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "undeclared_human_text_input_stream")
        recording = json.loads((raw / "recording-manifest.json").read_text())
        recording["text_input_schema_version"] = 2
        self._write(raw / "recording-manifest.json", recording)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "human_text_input_schema_invalid")

    def test_actual_core_serialized_synthetic_row_has_exact_snapshot_digest(self) -> None:
        source = Path(__file__).parent / "fixtures/human_text/core_accepted.jsonl"
        path = self.root / "human-text-inputs.jsonl"
        path.write_bytes(source.read_bytes())
        rows = verify_human_text_inputs(
            self.root,
            {"text_input_schema_version": 1, "session_id": "session-test",
             "timeline_id": "timeline-test"},
            {"human_text_input_count": 1, "human_text_inputs_sha256": sha_file(path)},
            ("run-0001",),
        )
        self.assertEqual(rows[0]["disposition"], "accepted_input")
        self.assertEqual(rows[0]["snapshot_sha256"],
                         "a17ebd56dc40c2619ed4e531076ace3bd3080761946ac58b24868d357ea59785")

    def test_durable_text_append_failure_survives_resealing_and_recovery(self) -> None:
        bundle = self._bundle()
        self._declare_text(bundle, [self._text_row(bundle)])
        raw = bundle / "raw"
        self._write(raw / "human-text-input-failure.json", {
            "schema": "sts2.human-annotator/human-text-input-failure-1",
            "session_id": self._text_row(bundle)["session_id"],
            "timeline_id": self._text_row(bundle)["timeline_id"],
            "failed_at": "2026-09-11T00:00:00Z", "reason": "write_failed",
        })
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "human_text_input_append_failure")
        receipt_path = raw / "session-close-receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["recovery"] = "sts2.human-annotator/interrupted-recovery-1"
        self._write(receipt_path, receipt)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "human_text_input_append_failure")
        (raw / "human-text-input-failure.json").unlink()
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code,
                         "human_text_input_recovery_unsupported")

    def test_read_blob_reference_cannot_escape_raw(self) -> None:
        bundle = self._bundle()
        raw = bundle / "raw"
        original = self._rows(raw / "canonical-transitions.jsonl")[0]["pre_state_ref"]
        value = json.loads((raw / original["object_ref"]).read_text(encoding="utf-8"))
        value["reads"][0]["payload_ref"] = "../../outside.json"
        updated = self._object(raw, "semantic-frames", value) | {"snapshot_id": original["snapshot_id"]}
        for filename in ("semantic-boundary-trace.jsonl", "canonical-transitions.jsonl"):
            path = raw / filename
            rows = self._rows(path)
            for row in rows:
                for key in ("human_observation_ref", "execution_pre_ref", "pre_state_ref"):
                    if row.get(key) == original:
                        row[key] = updated
            self._stream(path, rows)
        self._reseal(bundle)
        self.assertEqual(verify_human_session_bundle(bundle).findings[0].code, "read_blob_path_invalid")


if __name__ == "__main__":
    unittest.main()
