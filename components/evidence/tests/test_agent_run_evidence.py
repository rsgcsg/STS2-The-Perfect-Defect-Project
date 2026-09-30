from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from sts2_platform_evidence.agent_run_evidence import (
    ADAPTER_ATTESTATION_SCHEMA,
    AGENT_RUN_EVENT_SCHEMA,
    AGENT_RUN_SCHEMA,
    EVIDENCE_MANIFEST_SCHEMA,
    POLICY_MANIFEST_SCHEMA,
    AgentRunEvidenceVerifier,
    detect_agent_run_type,
)
from sts2_platform_evidence.cli import main
from sts2_platform_evidence.store import ContentAddressedStore
from sts2_platform_evidence.transfer import DirectoryReceiver, DirectoryTransferManifest


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class AgentRunEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _evidence(
        self,
        name: str = "run-1",
        adapter_protocol: str = "sts2.policy-runtime/decision-only-ndjson-1",
    ) -> Path:
        directory = self.root / name
        directory.mkdir()
        adapter = {
            "id": "fixture-adapter",
            "version": "1.0.0",
            "protocol": adapter_protocol,
            "code_sha256": "d" * 64,
        }
        policy_manifest = {
            "schema": POLICY_MANIFEST_SCHEMA,
            "manifest_id": "manifest-1",
            "policy": {"id": "policy-1", "version": "1.0.0"},
            "adapter": adapter,
            "artifact": {"sha256": "b" * 64},
            "representation": {"id": "snapshot", "version": "1", "input_schema": "sts2.player-environment/snapshot-1"},
        }
        policy_manifest_sha256 = sha256(
            json.dumps(
                policy_manifest,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        manifest = {
            "schema": AGENT_RUN_SCHEMA,
            "run_id": name,
            "manifest_id": "manifest-1",
            "policy_manifest_sha256": policy_manifest_sha256,
            "policy_id": "policy-1",
            "policy_version": "1.0.0",
            "policy_artifact_sha256": "b" * 64,
            "runtime_version": "0.1.0-rc.1",
            "runtime_code_sha256": "c" * 64,
            "started_at": "2026-08-25T00:00:00.000Z",
            "ended_at": "2026-08-25T00:01:00.000Z",
            "status": "completed",
            "mode": "one_step",
            "tainted": False,
            "append_only": True,
        }
        events = [
            {
                "schema": AGENT_RUN_EVENT_SCHEMA,
                "sequence": 1,
                "recorded_at": "2026-08-25T00:00:01.000Z",
                "kind": "environment_admitted",
                "payload": {
                    "runtime": {
                        "version": "0.1.0-rc.1",
                        "code_sha256": "c" * 64,
                    },
                    "policy_artifact_sha256": "b" * 64,
                    "environment": {
                        "runtime_instance_id": "runtime-fixture",
                        "environment_fingerprint": "environment-fixture",
                        "host_kind": "test",
                        "connector_protocol_version": "1.0.0",
                        "connector_version": "1.2.0-rc.6",
                        "connector_source_revision": "source-fixture",
                        "connector_artifact_sha256": "d" * 64,
                        "connector_module_version_id": "mvid-fixture",
                        "game_version": "v0.111.0",
                        "game_commit": "41cef1ea",
                        "modset_status": "fixture",
                        "modset_fingerprint": "modset-fixture",
                        "loaded_mod_ids": ["fixture-mod"],
                    },
                },
            },
            {
                "schema": AGENT_RUN_EVENT_SCHEMA,
                "sequence": 2,
                "recorded_at": "2026-08-25T00:00:02.000Z",
                "kind": "decision",
                "payload": {
                    "decision": {
                        "schema": "sts2.policy-runtime/decision-1",
                        "decision_id": "decision-1",
                        "run_id": name,
                        "manifest_id": "manifest-1",
                        "snapshot_id": "snapshot-1",
                        "candidate_digest": "f" * 64,
                        "candidate_count": 1,
                        "scores": [0.0],
                        "selected_index": None,
                        "disposition": "abstain",
                        "issued_at": "2026-08-25T00:00:02.000Z",
                    },
                    "resolved_bound_action_id": None,
                },
            },
        ]
        (directory / "manifest.json").write_bytes(canonical(manifest))
        (directory / "policy-manifest.json").write_bytes(canonical(policy_manifest))
        (directory / "adapter-attestation.json").write_bytes(canonical({
            "schema": ADAPTER_ATTESTATION_SCHEMA,
            "run_id": name,
            "manifest_id": "manifest-1",
            "policy_manifest_sha256": policy_manifest_sha256,
            "status": "attested",
            "expected": adapter,
            "actual": adapter,
            "attested_at": "2026-08-25T00:00:00.500Z",
        }))
        (directory / "events.jsonl").write_bytes(
            b"".join(canonical(event) for event in events)
        )
        files = []
        for relative in ("adapter-attestation.json", "events.jsonl", "manifest.json", "policy-manifest.json"):
            data = (directory / relative).read_bytes()
            files.append({"path": relative, "bytes": len(data), "sha256": sha256(data)})
        evidence_manifest = {
            "schema": EVIDENCE_MANIFEST_SCHEMA,
            "run_id": name,
            "complete": True,
            "append_only": True,
            "files": files,
            "manifest_sha256": sha256(json.dumps({"run_id": name, "files": files}, separators=(",", ":"), sort_keys=True).encode()),
        }
        (directory / "evidence-manifest.json").write_bytes(canonical(evidence_manifest))
        checksum_lines = []
        for relative in ("adapter-attestation.json", "events.jsonl", "evidence-manifest.json", "manifest.json", "policy-manifest.json"):
            checksum_lines.append(f"{sha256((directory / relative).read_bytes())}  {relative}")
        (directory / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
        return directory

    def _rewrite_events(self, directory: Path, events: list[dict[str, Any]]) -> None:
        (directory / "events.jsonl").write_bytes(b"".join(canonical(event) for event in events))
        files = []
        for relative in ("adapter-attestation.json", "events.jsonl", "manifest.json", "policy-manifest.json"):
            data = (directory / relative).read_bytes()
            files.append({"path": relative, "bytes": len(data), "sha256": sha256(data)})
        evidence_manifest = {
            "schema": EVIDENCE_MANIFEST_SCHEMA,
            "run_id": json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["run_id"],
            "complete": True,
            "append_only": True,
            "files": files,
        }
        evidence_manifest["manifest_sha256"] = sha256(
            json.dumps(
                {"run_id": evidence_manifest["run_id"], "files": files},
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )
        (directory / "evidence-manifest.json").write_bytes(canonical(evidence_manifest))
        checksum_lines = []
        for relative in ("adapter-attestation.json", "events.jsonl", "evidence-manifest.json", "manifest.json", "policy-manifest.json"):
            checksum_lines.append(f"{sha256((directory / relative).read_bytes())}  {relative}")
        (directory / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    def _content_id(self, directory: Path) -> str:
        return ContentAddressedStore(self.root / "identity-store").put_directory(directory).content_id

    def _snapshot(self, snapshot_id: str, sequence: int) -> dict[str, Any]:
        return {
            "protocol_version": "1.0.0",
            "schema": "sts2.player-environment/snapshot-1",
            "snapshot_id": snapshot_id,
            "sequence": sequence,
            "observed_at": "2026-08-25T00:00:03.000Z",
            "status": "interactive",
            "persistent": None,
            "interaction": {
                "interaction_id": "interaction-1",
                "kind": "combat",
                "stage": "main",
                "prompt": None,
                "content_schema": "sts2.player-environment/surface/combat-1",
                "content": {"surface": {"kind": "combat"}, "context": {"kind": "run"}},
                "capabilities": [],
            },
            "referents": [],
            "bound_actions": {
                "schema": "sts2.player-environment/bound-actions-1",
                "status": "complete",
                "materialized_count": 1,
                "total_count": 1,
                "limit": 1,
                "ordering_semantics": "fixture",
                "actions": [
                    {
                        "bound_action_id": "action-1",
                        "verb": "end_turn",
                        "interaction_id": "interaction-1",
                        "arguments": [],
                        "label": "End turn",
                    }
                ],
            },
            "reads": [],
            "completeness": {
                "status": "complete",
                "visible_information": "fixture",
                "interaction_discovery": "fixture",
                "missing": [],
                "hidden_by_policy": [],
            },
            "session": {
                "runtime_instance_id": "runtime-fixture",
                "environment_fingerprint": "environment-fixture",
            },
            "information_policy": {
                "id": "fixture-policy",
                "scope": "fixture",
                "includes_hidden_information": False,
                "unknown_field_behavior": "reject",
            },
        }

    def _delivered_evidence(self, name: str) -> Path:
        directory = self._evidence(name)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        decision = events[1]
        decision["payload"]["decision"].update(
            {
                "candidate_count": 1,
                "scores": [1.0],
                "selected_index": 0,
                "disposition": "admit",
            }
        )
        decision["payload"]["resolved_bound_action_id"] = "action-1"
        successor = self._snapshot("snapshot-2", 2)
        events.extend(
            [
                {
                    "schema": AGENT_RUN_EVENT_SCHEMA,
                    "sequence": 3,
                    "recorded_at": "2026-08-25T00:00:02.100Z",
                    "kind": "controller_acquired",
                    "payload": {},
                },
                {
                    "schema": AGENT_RUN_EVENT_SCHEMA,
                    "sequence": 4,
                    "recorded_at": "2026-08-25T00:00:02.200Z",
                    "kind": "receipt",
                    "payload": {
                        "decision_id": "decision-1",
                        "receipt": {
                            "protocol_version": "1.0.0",
                            "schema": "sts2.player-environment/receipt-1",
                            "request_id": f"request-{name}-decision-1",
                            "delivery": "delivered",
                            "action": {"bound_action_id": "action-1", "verb": "end_turn", "arguments": []},
                            "retry": {"allowed": False, "reason": "fixture"},
                            "successor": successor,
                        },
                    },
                },
                {
                    "schema": AGENT_RUN_EVENT_SCHEMA,
                    "sequence": 5,
                    "recorded_at": "2026-08-25T00:00:02.300Z",
                    "kind": "successor",
                    "payload": {"decision_id": "decision-1", "successor": successor},
                },
                {
                    "schema": AGENT_RUN_EVENT_SCHEMA,
                    "sequence": 6,
                    "recorded_at": "2026-08-25T00:00:02.400Z",
                    "kind": "controller_released",
                    "payload": {},
                },
                {
                    "schema": AGENT_RUN_EVENT_SCHEMA,
                    "sequence": 7,
                    "recorded_at": "2026-08-25T00:00:02.500Z",
                    "kind": "one_step_completed",
                    "payload": {},
                },
            ]
        )
        self._rewrite_events(directory, events)
        return directory

    def test_valid_agent_run_is_detected_and_verified(self) -> None:
        directory = self._evidence()
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertTrue(result.passed, result.findings)
        self.assertEqual(result.require_value().event_count, 2)
        self.assertEqual(detect_agent_run_type(directory), "policy-runtime-agent-run")

    def test_adapter_protocol_v1_and_exact_v2_are_supported(self) -> None:
        legacy = AgentRunEvidenceVerifier().verify(self._evidence("run-v1"))
        self.assertTrue(legacy.passed, legacy.findings)

    def test_v2_adapter_with_legacy_snapshot_representation_is_rejected(self) -> None:
        directory = self._evidence(
            "run-v2-legacy-snapshot",
            adapter_protocol="sts2.policy-runtime/decision-only-ndjson-2",
        )
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "adapter_representation")

    def test_adapter_protocol_unknown_and_attestation_drift_fail_closed(self) -> None:
        directory = self._evidence(
            "run-unknown-adapter",
            adapter_protocol="sts2.policy-runtime/decision-only-ndjson-999",
        )
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "invalid_value")

        directory = self._evidence("run-expected-adapter-drift")
        attestation_path = directory / "adapter-attestation.json"
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
        attestation["expected"]["protocol"] = "sts2.policy-runtime/decision-only-ndjson-2"
        attestation_path.write_bytes(canonical(attestation))
        self._rewrite_events(directory, [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()])
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "adapter_association")

        directory = self._evidence("run-actual-adapter-drift")
        attestation_path = directory / "adapter-attestation.json"
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
        attestation["actual"]["protocol"] = "sts2.policy-runtime/decision-only-ndjson-2"
        attestation_path.write_bytes(canonical(attestation))
        self._rewrite_events(directory, [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()])
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "adapter_association")

    def test_policy_manifest_and_adapter_attestation_are_verified(self) -> None:
        directory = self._evidence("run-policy-manifest-drift")
        policy_manifest_path = directory / "policy-manifest.json"
        policy_manifest = json.loads(policy_manifest_path.read_text(encoding="utf-8"))
        policy_manifest["policy"]["version"] = "tampered"
        policy_manifest_path.write_bytes(canonical(policy_manifest))
        self._rewrite_events(
            directory,
            [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()],
        )
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "policy_manifest_digest")

        directory = self._evidence("run-adapter-drift")
        attestation_path = directory / "adapter-attestation.json"
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
        attestation["actual"]["code_sha256"] = "e" * 64
        attestation_path.write_bytes(canonical(attestation))
        self._rewrite_events(
            directory,
            [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()],
        )
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "adapter_association")

    def test_delivered_receipt_and_successor_associations_are_verified(self) -> None:
        directory = self._delivered_evidence("run-delivered")
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertTrue(result.passed, result.findings)
        self.assertEqual(result.require_value().event_count, 7)

        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[3]["payload"]["receipt"]["request_id"] = "wrong-request"
        self._rewrite_events(directory, events)
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "request_association")

    def test_snapshot_read_optional_target_matches_public_sdk(self) -> None:
        for target, passed in [("absent", True), (None, True), ("missing", False), (12, False)]:
            with self.subTest(target=target):
                directory = self._delivered_evidence("run-read-" + str(target))
                events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
                read = {"read_id": "read:piles", "kind": "combat_piles",
                        "content_schema": "sts2.player-environment/read/combat_piles-1",
                        "visibility_basis": "player_visible", "snapshot_bound": True,
                        "ordering_semantics": "unordered_multiset", "hidden_by_policy": []}
                if target != "absent":
                    read["target_referent_id"] = target
                for event in events:
                    if event["kind"] == "receipt":
                        event["payload"]["receipt"]["successor"]["reads"] = [read]
                    elif event["kind"] == "successor":
                        event["payload"]["successor"]["reads"] = [read]
                self._rewrite_events(directory, events)
                result = AgentRunEvidenceVerifier().verify(directory)
                self.assertEqual(result.passed, passed, result.findings)
                if passed:
                    read["unknown"] = True
                    self._rewrite_events(directory, events)
                    self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)

    def test_tamper_is_rejected_before_receiver_promotion(self) -> None:
        directory = self._evidence("run-tampered")
        (directory / "events.jsonl").write_text(
            (directory / "events.jsonl").read_text(encoding="utf-8").replace("decision-1", "tampered"),
            encoding="utf-8",
        )
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "checksum_mismatch")

        transfer = DirectoryTransferManifest.from_directory(
            directory,
            content_id="a" * 64,
            artifact_type="policy-runtime-agent-run",
        )
        receiver = DirectoryReceiver(
            ContentAddressedStore(self.root / "store"),
            promotion_verifier=lambda source, manifest: AgentRunEvidenceVerifier().verify(source).require_value(),
        )
        receipt = receiver.receive(directory, transfer)
        self.assertEqual(receipt.status, "quarantined")
        self.assertFalse((self.root / "store" / "objects" / ("a" * 64)).exists())

    def test_event_payload_sequence_and_associations_fail_closed(self) -> None:
        directory = self._evidence("run-association")
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[1]["payload"]["decision"]["run_id"] = "other-run"
        self._rewrite_events(directory, events)
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "run_id_drift")

        directory = self._evidence("run-sequence")
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[1]["sequence"] = 3
        self._rewrite_events(directory, events)
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "event_sequence_gap")

        directory = self._evidence("run-payload")
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[1]["payload"]["unexpected"] = True
        self._rewrite_events(directory, events)
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "schema_keys")

    def test_unknown_schema_is_not_guessed(self) -> None:
        directory = self._evidence("run-unknown")
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema"] = "sts2.policy-runtime/future-agent-run-9"
        manifest_path.write_bytes(canonical(manifest))
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "unknown_schema")
        with self.assertRaisesRegex(KeyError, "unknown evidence schema"):
            detect_agent_run_type(directory)

    def test_cli_and_typed_receive_use_existing_transfer_plumbing(self) -> None:
        directory = self._evidence("run-cli")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["verify-agent-run", str(directory)]), 0)
        self.assertIn('"status": "pass"', output.getvalue())

        transfer = DirectoryTransferManifest.from_directory(
            directory,
            content_id=self._content_id(directory),
            artifact_type="policy-runtime-agent-run",
        )
        transfer_path = transfer.write(self.root / "transfer.json")
        receipt_path = self.root / "receipt.json"
        store_root = self.root / "store-cli"
        with contextlib.redirect_stdout(io.StringIO()):
            code = main([
                "receive", str(directory), str(transfer_path), "--root", str(store_root),
                "--verify-type", "policy-runtime-agent-run", "--receipt", str(receipt_path),
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(receipt_path.read_text())["status"], "promoted")

    def test_agent_run_promotion_rejects_caller_content_id_mismatch(self) -> None:
        directory = self._evidence("run-cli-mismatch")
        transfer = DirectoryTransferManifest.from_directory(
            directory,
            content_id="b" * 64,
            artifact_type="policy-runtime-agent-run",
        )
        transfer_path = transfer.write(self.root / "mismatch-transfer.json")
        store_root = self.root / "store-mismatch"
        receipt_path = self.root / "mismatch-receipt.json"
        with contextlib.redirect_stdout(io.StringIO()):
            code = main([
                "receive", str(directory), str(transfer_path), "--root", str(store_root),
                "--verify-type", "policy-runtime-agent-run", "--receipt", str(receipt_path),
            ])
        self.assertEqual(code, 1)
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt["status"], "quarantined")
        self.assertFalse((store_root / "objects" / ("b" * 64)).exists())


if __name__ == "__main__":
    unittest.main()

class TextMenuAgentRunEvidenceTests(AgentRunEvidenceTests):
    def test_exact_v3_adapter_keeps_text_menu_and_native_delivery_validation(self) -> None:
        for native in (False, True):
            with self.subTest(native=native):
                directory = self._text_evidence(
                    f"run-v3-native-{native}", native=native,
                    adapter_protocol="sts2.policy-runtime/decision-only-ndjson-3",
                )
                result = AgentRunEvidenceVerifier().verify(directory)
                self.assertTrue(result.passed, result.findings)
                # An opt-in protocol is not permission to change what happened.
                events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
                events[5]["payload"]["result"]["native_delivery"] = None if native else "delivered"
                self._rewrite_events(directory, events)
                rejected = AgentRunEvidenceVerifier().verify(directory)
                self.assertFalse(rejected.passed)
                self.assertEqual(rejected.findings[0].code, "text_result_binding")

    def test_v3_adapter_rejects_legacy_snapshot_and_attestation_drift(self) -> None:
        directory = self._evidence(
            "run-v3-legacy", adapter_protocol="sts2.policy-runtime/decision-only-ndjson-3",
        )
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "adapter_representation")

        directory = self._text_evidence(
            "run-v3-attestation", adapter_protocol="sts2.policy-runtime/decision-only-ndjson-3",
        )
        attestation_path = directory / "adapter-attestation.json"
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
        attestation["actual"]["protocol"] = "sts2.policy-runtime/decision-only-ndjson-2"
        attestation_path.write_bytes(canonical(attestation))
        self._rewrite_events(directory, [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()])
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "adapter_association")

    def test_v3_adapter_accepts_exact_v2_connector_selection_fixture(self) -> None:
        directory = self._text_v2_evidence(
            "v3-v2-selection", adapter_protocol="sts2.policy-runtime/decision-only-ndjson-3",
        )
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertTrue(result.passed, result.findings)

    def test_exact_v2_adapter_with_text_menu_manifest_is_supported(self) -> None:
        result = AgentRunEvidenceVerifier().verify(
            self._text_evidence(
                "run-v2",
                native=True,
                adapter_protocol="sts2.policy-runtime/decision-only-ndjson-2",
            )
        )
        self.assertTrue(result.passed, result.findings)

    def _game_over_intro(self, snapshot: dict[str, Any], result: str) -> dict[str, Any]:
        page = json.loads(json.dumps(snapshot))
        page["interaction"].update(
            interaction_id="game-over", kind="game_over", stage="intro",
            content_schema="sts2.player-environment/surface/game_over-1",
            content={"surface": {"kind": "game_over", "stage": "intro",
                                 "screen_entity_id": "game-over-screen",
                                 "return_destination": None,
                                 "can_advance_summary": True, "can_return": False,
                                 "other_controls": []},
                     "context": {"kind": "game_over", "result": result,
                                 "game_mode": "standard", "score": None,
                                 "floor_reached": None, "ascension": None}},
            capabilities=[{"verb": "activate", "subject_role": None,
                           "arguments": [], "availability_basis": "exact_current_text_menu"}],
        )
        page["menu_actions"]["actions"] = [{
            "action_id": "game-over-advance", "kind": "native_input",
            "verb": "activate", "label": "Continue", "subject_referent_id": None,
            "arguments": [], "effect_domain": "native_input",
        }]
        return page

    def test_nonadmitted_text_observation_is_verified_without_decision_association(self) -> None:
        directory = self._text_evidence("text-nonadmitted")
        original = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        page = self._game_over_intro(original[1]["payload"]["snapshot"], "win")
        observation = {"schema": AGENT_RUN_EVENT_SCHEMA, "sequence": 2,
                       "recorded_at": "2026-08-25T00:00:02.000Z", "kind": "text_observation_not_admitted",
                       "payload": {"reason": "unsupported_interaction_kind", "snapshot": page}}
        self._rewrite_events(directory, [original[0], observation])
        verifier = AgentRunEvidenceVerifier()
        self.assertTrue(verifier.verify(directory).passed)

        invalid = (
            ({"decision_id": "decision-1"}, "schema_keys"),
            ({"reason": "snapshot_observed"}, "text_observation_admission"),
            ({"snapshot": {**page, "session": {**page["session"], "environment_fingerprint": "foreign"}}}, "runtime_association"),
            ({"snapshot": {**page, "input_profile": "other"}}, "schema_literal"),
        )
        for change, expected_code in invalid:
            with self.subTest(change=change):
                altered = json.loads(json.dumps(observation))
                altered["payload"].update(change)
                self._rewrite_events(directory, [original[0], altered])
                result = verifier.verify(directory)
                self.assertFalse(result.passed)
                self.assertEqual(result.findings[0].code, expected_code)
        self._rewrite_events(directory, [{**observation, "sequence": 1}])
        self.assertEqual(verifier.verify(directory).findings[0].code, "environment_identity_order")

    def _text_snapshot(self, snapshot_id: str, sequence: int, action: dict[str, Any], cursor: str = "root") -> dict[str, Any]:
        snapshot = self._snapshot(snapshot_id, sequence)
        snapshot.pop("bound_actions")
        snapshot.pop("reads")
        snapshot.update(schema="sts2.player-environment/text-menu-snapshot-1", input_profile="text-menu-v1")
        snapshot["interaction"]["content_schema"] = "sts2.player-environment/surface/combat_text_menu-1"
        snapshot["menu"] = {"cursor": cursor, "revision": sequence, "native_snapshot_id": "native-1"}
        snapshot["menu_actions"] = {"status": "complete", "materialized_count": 1, "total_count": 1, "ordering_semantics": "connector_order", "actions": [action]}
        return snapshot

    def _text_evidence(
        self,
        name: str,
        *,
        native: bool = False,
        adapter_protocol: str = "sts2.policy-runtime/decision-only-ndjson-1",
    ) -> Path:
        directory = self._evidence(name, adapter_protocol=adapter_protocol)
        policy_path = directory / "policy-manifest.json"
        policy = json.loads(policy_path.read_text())
        policy["representation"] = {"id": "text", "version": "1", "input_schema": "sts2.player-environment/text-menu-snapshot-1"}
        policy_path.write_bytes(canonical(policy))
        digest = sha256(json.dumps(policy, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode())
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["policy_manifest_sha256"] = digest
        manifest_path.write_bytes(canonical(manifest))
        attestation_path = directory / "adapter-attestation.json"
        attestation = json.loads(attestation_path.read_text())
        attestation["policy_manifest_sha256"] = digest
        attestation["expected"] = policy["adapter"]
        attestation["actual"] = policy["adapter"]
        attestation_path.write_bytes(canonical(attestation))
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        action = {"action_id": "native-end" if native else "nav-info", "kind": "native_input" if native else "system_navigation", "verb": "end_turn" if native else "open_information", "label": "End turn" if native else "Information", "subject_referent_id": None, "arguments": [], "effect_domain": "native_input" if native else "text_menu"}
        first = self._text_snapshot("text-1", 1, action)
        next_action = action if native else {**action, "action_id": "nav-back", "verb": "back", "label": "Back"}
        successor = self._text_snapshot("text-2", 2, next_action, "root" if native else "information")
        candidate_digest = sha256(json.dumps([action["action_id"]], separators=(",", ":")).encode())
        decision = events[1]
        decision["payload"]["decision"].update(snapshot_id="text-1", candidate_digest=candidate_digest, candidate_count=1, scores=[1.0], selected_index=0, disposition="admit")
        decision["payload"]["resolved_bound_action_id"] = action["action_id"]
        request_id = f"request-{name}-decision-1"
        result = {"protocol_version": "1.0.0", "schema": "sts2.player-environment/text-menu-action-result-1", "input_profile": "text-menu-v1", "request_id": request_id, "status": "applied", "effect_domain": action["effect_domain"], "native_delivery": "delivered" if native else None, "action": action, "reason_code": None, "detail": None, "retry": "never", "successor": successor if not native else None, "attribution": None}
        def event(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {"schema": AGENT_RUN_EVENT_SCHEMA, "sequence": 0, "recorded_at": "2026-08-25T00:00:02.000Z", "kind": kind, "payload": payload}
        events = [events[0], event("text_decision_input", {"decision_id": "decision-1", "snapshot": first}), decision,
                  event("controller_acquired", {}),
                  event("text_menu_dispatch_attempt", {"decision_id": "decision-1", "action_id": action["action_id"], "effect_domain": action["effect_domain"], "native_submissions_used": 1 if native else 0, "menu_navigations_used": 0 if native else 1}),
                  event("text_native_delivery" if native else "menu_navigation", {"decision_id": "decision-1", **({} if native else {"action_id": action["action_id"]}), "result": result})]
        if native:
            events.append(event("text_observed_successor", {"decision_id": "decision-1", "successor": successor}))
        events.append(event("controller_released", {}))
        if adapter_protocol == "sts2.policy-runtime/decision-only-ndjson-3":
            events[1]["payload"]["observation_context"] = {
                "continuity_token": "continuity-1", "previous_interaction_request_id": None,
            }
        for index, item in enumerate(events, 1): item["sequence"] = index
        self._rewrite_events(directory, events)
        return directory

    def _text_v2_evidence(
        self, name: str, *, adapter_protocol: str = "sts2.policy-runtime/decision-only-ndjson-2",
    ) -> Path:
        directory = self._text_evidence(name, adapter_protocol=adapter_protocol)
        policy_path = directory / "policy-manifest.json"
        policy = json.loads(policy_path.read_text())
        policy["representation"] = {"id": "text", "version": "2", "input_schema": "sts2.player-environment/text-menu-snapshot-2"}
        policy["policy"].update(provider="fixture", architecture="fixture")
        policy["artifact"].update(id="fixture-artifact", path="fixture-artifact.bin")
        policy["requirements"] = {
            "connector_protocol_version": "1.0.0",
            "environment": {"host_kind": "test", "connector_version": "1.2.0-rc.6", "connector_source_revision": "source-fixture",
                            "connector_artifact_sha256": "d" * 64, "connector_module_version_id": "mvid-fixture",
                            "modset_status": "fixture", "modset_fingerprint": "modset-fixture", "loaded_mod_ids": ["fixture-mod"]},
            "reads": [], "whole_decision_admission": True,
            "candidate_order_digest": "sha256-json-menu-action-id-order", "score_count_matches_candidate_count": True,
            "selected_index": True, "successor_required": True,
        }
        policy["support"] = {"game_versions": ["v0.111.0"], "game_commits": ["41cef1ea"],
                             "interaction_kinds": ["combat_turn"], "action_verbs": ["select_card", "select_target", "cancel_selection", "play", "open_information", "back"]}
        policy["adapter_config"] = {}
        policy["claims"] = {"full_run": False, "selector": False, "catalog_filtered": False,
                            "creates_action_authority": False, "creates_native_operands": False}
        policy_path.write_bytes(canonical(policy))
        digest = sha256(json.dumps(policy, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode())
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["policy_manifest_sha256"] = digest
        manifest_path.write_bytes(canonical(manifest))
        attestation_path = directory / "adapter-attestation.json"
        attestation = json.loads(attestation_path.read_text())
        attestation["policy_manifest_sha256"] = digest
        attestation_path.write_bytes(canonical(attestation))
        fixtures = Path(__file__).resolve().parents[2] / "connector" / "sdk" / "typescript" / "test" / "fixtures"
        first = json.loads((fixtures / "text-menu-v2-targeted-root.json").read_text())
        result = json.loads((fixtures / "text-menu-v2-targeted-select.json").read_text())
        for snapshot in (first, result["successor"]):
            snapshot["session"] = {"runtime_instance_id": "runtime-fixture", "environment_fingerprint": "environment-fixture"}
        action = first["menu_actions"]["actions"][0]
        result["request_id"] = f"request-{name}-decision-1"
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        decision = events[2]
        decision["payload"]["decision"].update(snapshot_id=first["snapshot_id"], candidate_digest=sha256(json.dumps([action["action_id"]], separators=(",", ":")).encode()), candidate_count=1, scores=[1.0], selected_index=0, disposition="admit")
        decision["payload"]["resolved_bound_action_id"] = action["action_id"]
        events[1]["payload"]["snapshot"] = first
        events[4]["payload"].update(action_id=action["action_id"], effect_domain="text_menu", native_submissions_used=0, menu_navigations_used=1)
        events[5]["payload"].update(action_id=action["action_id"], result=result)
        self._rewrite_events(directory, events)
        return directory

    def _managed_v2_evidence(self, name: str) -> Path:
        directory = self._text_v2_evidence(
            name, adapter_protocol="sts2.policy-runtime/decision-only-ndjson-3"
        )
        policy_path = directory / "policy-manifest.json"
        policy = json.loads(policy_path.read_text())
        policy["requirements"].pop("connector_protocol_version")
        policy["requirements"]["environment"] = {
            "kind": "managed_text_v2", "text_protocol_version": "1.0.0",
            "input_profile": "text-menu-v2",
        }
        policy_path.write_bytes(canonical(policy))
        binding = {
            "schema": "sts2.policy-runtime/managed-environment-binding-1",
            "profile_sha256": "a" * 64, "input_profile": "text-menu-v2",
            "host_package_identity": {
                "package": "@rsgcsg/sts2-host-runtime", "version": "1.1.0-rc.22",
                "source_revision": "1" * 40, "component_tree_revision": "2" * 40,
                "release_asset_sha256": "3" * 64, "package_content_sha256": "4" * 64,
            },
            "candidate_build": {
                "upstream_revision": "candidate-revision", "source_patch_sha256": "5" * 64,
                "artifact_sha256": "6" * 64, "artifact_mvid": "candidate-mvid",
                "original_sts2_sha256": "7" * 64, "runtime_sts2_sha256": "7" * 64,
            },
        }
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["policy_manifest_sha256"] = sha256(canonical(policy).rstrip(b"\n"))
        manifest["environment_binding"] = binding
        manifest_path.write_bytes(canonical(manifest))
        attestation_path = directory / "adapter-attestation.json"
        attestation = json.loads(attestation_path.read_text())
        attestation["policy_manifest_sha256"] = manifest["policy_manifest_sha256"]
        attestation_path.write_bytes(canonical(attestation))
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        environment = {
            "kind": "managed_text_v2",
            "binding_sha256": sha256(canonical(binding).rstrip(b"\n")),
            "service_instance_id": "service-fixture", "runtime_instance_id": "runtime-fixture",
            "environment_fingerprint": "environment-fixture",
            "game_continuity_id": "managed-episode-fixture",
            "text_protocol_version": "1.0.0", "input_profile": "text-menu-v2",
            "host_package_identity": binding["host_package_identity"],
            "host_identity": {
                "package_name": "@rsgcsg/sts2-host-runtime", "version": "1.1.0-rc.22",
                "source_revision": "1" * 40, "component_tree_revision": "2" * 40,
                "source_digest_sha256": "8" * 64,
            },
            "candidate_build": binding["candidate_build"],
            "game_version": "v0.111.0", "game_commit": "41cef1ea",
            "game_assembly_sha256": "7" * 64,
            "episode_provenance": {
                "verdict": "provenance_pass", "requested_seed": "seed-fixture",
                "actual_seed": "seed-fixture", "runtime_instance_id": "runtime-fixture",
            },
        }
        events[0]["payload"]["environment"] = environment
        control = {
            "service_instance_id": "service-fixture", "runtime_instance_id": "runtime-fixture",
            "game_continuity_id": "managed-episode-fixture", "control_epoch": "epoch-fixture",
        }
        for event in events:
            if event["kind"] == "controller_acquired":
                event["payload"] = {"status": "held", **control}
            elif event["kind"] == "controller_released":
                event["payload"] = {"status": "released", **control}
        self._rewrite_events(directory, events)
        return directory

    def test_managed_v2_binding_and_control_are_verified_without_changing_connector_records(self) -> None:
        directory = self._managed_v2_evidence("managed-v2-valid")
        verifier = AgentRunEvidenceVerifier()
        self.assertTrue(verifier.verify(directory).passed)
        self.assertTrue(verifier.verify(self._text_v2_evidence("connector-v2-still-valid")).passed)

        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        changed = json.loads(json.dumps(events))
        changed[0]["payload"]["environment"]["binding_sha256"] = "9" * 64
        self._rewrite_events(directory, changed)
        self.assertEqual(verifier.verify(directory).findings[0].code, "environment_association")

        changed = json.loads(json.dumps(events))
        changed[0]["payload"]["environment"]["candidate_build"]["artifact_sha256"] = "9" * 64
        self._rewrite_events(directory, changed)
        self.assertEqual(verifier.verify(directory).findings[0].code, "environment_association")

        changed = json.loads(json.dumps(events))
        claim = next(event for event in changed if event["kind"] == "controller_acquired")
        claim["payload"]["control_epoch"] = "wrong-epoch"
        self._rewrite_events(directory, changed)
        self.assertEqual(verifier.verify(directory).findings[0].code, "managed_control")

        changed = json.loads(json.dumps(events))
        changed = [event for event in changed if event["kind"] != "controller_acquired"]
        for index, event in enumerate(changed, 1):
            event["sequence"] = index
        self._rewrite_events(directory, changed)
        self.assertEqual(verifier.verify(directory).findings[0].code, "managed_control")

        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["environment_binding"]["profile_sha256"] = "9" * 64
        manifest_path.write_bytes(canonical(manifest))
        self._rewrite_events(directory, events)
        self.assertEqual(verifier.verify(directory).findings[0].code, "environment_association")

    def test_managed_stop_cannot_claim_release_without_a_matching_event_even_when_tainted(self) -> None:
        directory = self._managed_v2_evidence("managed-false-release")
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events = [event for event in events if event["kind"] != "controller_released"]
        events.append({"schema": AGENT_RUN_EVENT_SCHEMA, "sequence": 0,
                       "recorded_at": "2026-08-25T00:00:04.000Z", "kind": "stopped",
                       "payload": {"autonomy_budget": {
                           "state": "inactive", "max_submissions": 16, "submissions_used": 1,
                           "max_policy_calls": 32, "policy_calls_used": 1, "deadline_ms": 60000,
                           "elapsed_ms": 0, "remaining_ms": 60000,
                           "exhausted_reason": None, "ended_reason": "stopped",
                       }, "controller": "released"}})
        for index, event in enumerate(events, 1):
            event["sequence"] = index
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(status="tainted", tainted=True)
        manifest_path.write_bytes(canonical(manifest))
        self._rewrite_events(directory, events)
        self.assertEqual(AgentRunEvidenceVerifier().verify(directory).findings[0].code, "managed_control")

    def _v3_context_pair(self, name: str) -> tuple[Path, list[dict[str, Any]]]:
        directory = self._text_evidence(name, adapter_protocol="sts2.policy-runtime/decision-only-ndjson-3")
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        page = json.loads(json.dumps(events[5]["payload"]["result"]["successor"]))
        entry = {**events[1], "payload": {
            "decision_id": "decision-2", "snapshot": page,
            "observation_context": {"continuity_token": "continuity-1",
                                    "previous_interaction_request_id": events[5]["payload"]["result"]["request_id"]}}}
        decision = json.loads(json.dumps(events[2]))
        decision["payload"]["decision"].update(
            decision_id="decision-2", snapshot_id=page["snapshot_id"],
            candidate_digest=sha256(json.dumps([a["action_id"] for a in page["menu_actions"]["actions"]], separators=(",", ":")).encode()))
        decision["payload"]["resolved_bound_action_id"] = page["menu_actions"]["actions"][0]["action_id"]
        events.extend([entry, decision])
        return directory, events

    def test_v3_context_binds_actual_confirmed_history_without_changing_page(self) -> None:
        directory, events = self._v3_context_pair("context-valid")
        self._rewrite_events(directory, [{**e, "sequence": n} for n, e in enumerate(events, 1)])
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertTrue(result.passed, result.findings)

    def test_v3_context_rejects_missing_foreign_reused_or_unconfirmed_history(self) -> None:
        for case in ("missing", "extra", "foreign", "new_token_history", "same_page",
                     "older_page", "duplicate_echo", "retired_token", "cancelled"):
            with self.subTest(case=case):
                directory, events = self._v3_context_pair("context-" + case)
                context = events[-2]["payload"]["observation_context"]
                code = "observation_context_binding"
                if case == "missing":
                    del events[1]["payload"]["observation_context"]
                    code = "schema_keys"
                elif case == "extra":
                    context["game_id"] = "not-public-model-input"
                    code = "schema_keys"
                elif case == "foreign":
                    context["previous_interaction_request_id"] = "foreign-request"
                elif case == "new_token_history":
                    context["continuity_token"] = "another-continuity"
                elif case == "same_page":
                    events[-2]["payload"]["snapshot"] = events[1]["payload"]["snapshot"]
                elif case == "older_page":
                    events[-2]["payload"]["snapshot"]["sequence"] = 1
                elif case in {"duplicate_echo", "retired_token"}:
                    pair = json.loads(json.dumps(events[-2:]))
                    pair[0]["payload"]["decision_id"] = "decision-3"
                    pair[0]["payload"]["snapshot"].update(snapshot_id="text-3", sequence=3)
                    pair[1]["payload"]["decision"].update(decision_id="decision-3", snapshot_id="text-3")
                    if case == "retired_token":
                        context.update(continuity_token="another-continuity", previous_interaction_request_id=None)
                        pair[0]["payload"]["observation_context"]["previous_interaction_request_id"] = None
                    events.extend(pair)
                elif case == "cancelled":
                    events[5] = {**events[5], "kind": "text_menu_dispatch_cancelled",
                                 "payload": {"decision_id": "decision-1", "reason": "recovery_before_submit"}}
                self._rewrite_events(directory, [{**e, "sequence": n} for n, e in enumerate(events, 1)])
                result = AgentRunEvidenceVerifier().verify(directory)
                self.assertFalse(result.passed)
                self.assertEqual(result.findings[0].code, code)

    def test_old_port_rejects_new_context_metadata(self) -> None:
        directory = self._text_evidence("old-port-context")
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[1]["payload"]["observation_context"] = {
            "continuity_token": "continuity-1", "previous_interaction_request_id": None,
        }
        self._rewrite_events(directory, events)
        result = AgentRunEvidenceVerifier().verify(directory)
        self.assertEqual(result.findings[0].code, "schema_keys")

    def test_cancelled_dispatch_seals_without_fabricating_connector_result(self) -> None:
        for protocol in (2, 3):
            for native in (False, True):
                with self.subTest(protocol=protocol, native=native):
                    directory = self._text_evidence(
                        f"cancelled-{protocol}-{native}", native=native,
                        adapter_protocol=f"sts2.policy-runtime/decision-only-ndjson-{protocol}",
                    )
                    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
                    cancellation = {**events[5], "kind": "text_menu_dispatch_cancelled",
                                    "payload": {"decision_id": "decision-1", "reason": "recovery_before_submit"}}
                    events = [*events[:5], cancellation, events[-1]]
                    self._rewrite_events(directory, [{**event, "sequence": index} for index, event in enumerate(events, 1)])
                    result = AgentRunEvidenceVerifier().verify(directory)
                    self.assertTrue(result.passed, result.findings)

    def test_cancelled_dispatch_rejects_missing_attempt_bad_reason_and_duplicate_outcome(self) -> None:
        for case in ("no_attempt", "wrong_decision", "bad_reason", "extra_result", "duplicate",
                     "late_result", "after_result", "successor"):
            with self.subTest(case=case):
                directory = self._text_evidence("cancelled-" + case, native=True)
                original = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
                cancellation = {**original[5], "kind": "text_menu_dispatch_cancelled",
                                "payload": {"decision_id": "decision-1", "reason": "recovery_before_submit"}}
                events = [*original[:5], cancellation, original[-1]]
                code = "text_dispatch_binding"
                if case == "no_attempt":
                    del events[4]
                elif case == "wrong_decision":
                    cancellation["payload"]["decision_id"] = "not-this-decision"
                elif case == "bad_reason":
                    cancellation["payload"]["reason"] = "connector_rejected"
                    code = "invalid_value"
                elif case == "extra_result":
                    cancellation["payload"]["result"] = original[5]["payload"]["result"]
                    code = "schema_keys"
                elif case == "duplicate":
                    events.insert(6, cancellation)
                    code = "duplicate_text_result"
                elif case == "late_result":
                    events.insert(6, original[5])
                    code = "duplicate_text_result"
                elif case == "after_result":
                    events.insert(5, original[5])
                    code = "duplicate_text_result"
                elif case == "successor":
                    events.insert(6, original[6])
                    code = "successor_association"
                # Rehash the actual bundle: failures must be semantic, not stale digests.
                self._rewrite_events(directory, [{**event, "sequence": index} for index, event in enumerate(events, 1)])
                result = AgentRunEvidenceVerifier().verify(directory)
                self.assertFalse(result.passed)
                self.assertEqual(result.findings[0].code, code)

    def test_v2_connector_selection_fixture_is_verified(self) -> None:
        directory = self._text_v2_evidence("v2-selection")
        self.assertTrue(AgentRunEvidenceVerifier().verify(directory).passed)

    def test_v2_system_navigation_has_only_menu_effect(self) -> None:
        directory = self._text_v2_evidence("v2-navigation")
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        action = {"action_id": "v2-nav-info", "kind": "system_navigation", "verb": "open_information",
                  "label": "Information", "subject_referent_id": None, "arguments": [], "effect_domain": "text_menu"}
        first = events[1]["payload"]["snapshot"]
        first["menu_actions"].update(actions=[action], materialized_count=1, total_count=1)
        successor = events[5]["payload"]["result"]["successor"]
        successor["menu"].update(cursor="information", selection=[])
        successor["menu_actions"].update(actions=[{**action, "action_id": "v2-nav-back", "verb": "back", "label": "Back"}], materialized_count=1, total_count=1)
        decision = events[2]["payload"]["decision"]
        decision["candidate_digest"] = sha256(json.dumps([action["action_id"]], separators=(",", ":")).encode())
        events[2]["payload"]["resolved_bound_action_id"] = action["action_id"]
        events[4]["payload"]["action_id"] = action["action_id"]
        events[5]["payload"]["action_id"] = action["action_id"]
        events[5]["payload"]["result"]["action"] = action
        self._rewrite_events(directory, events)
        self.assertTrue(AgentRunEvidenceVerifier().verify(directory).passed)
        events[5]["payload"]["result"]["native_delivery"] = "delivered"
        self._rewrite_events(directory, events)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)

    def _text_v2_native(self, name: str, *, unknown: bool) -> Path:
        directory = self._text_v2_evidence(name)
        fixtures = Path(__file__).resolve().parents[2] / "connector" / "sdk" / "typescript" / "test" / "fixtures"
        first = json.loads((fixtures / "text-menu-v2-card-only-select.json").read_text())["successor"]
        first["session"] = {"runtime_instance_id": "runtime-fixture", "environment_fingerprint": "environment-fixture"}
        action = first["menu_actions"]["actions"][0]
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[1]["payload"]["snapshot"] = first
        decision = events[2]["payload"]["decision"]
        decision.update(snapshot_id=first["snapshot_id"], candidate_count=2,
                        candidate_digest=sha256(json.dumps([item["action_id"] for item in first["menu_actions"]["actions"]], separators=(",", ":")).encode()),
                        scores=[1.0, 0.0], selected_index=0)
        events[2]["payload"]["resolved_bound_action_id"] = action["action_id"]
        events[4]["payload"].update(action_id=action["action_id"], effect_domain="native_input", native_submissions_used=1, menu_navigations_used=0)
        events[5]["kind"] = "text_native_unknown" if unknown else "text_native_delivery"
        events[5]["payload"] = {"decision_id": "decision-1", "result": {
            "protocol_version": "1.0.0", "schema": "sts2.player-environment/text-menu-action-result-2", "input_profile": "text-menu-v2",
            "request_id": f"request-{name}-decision-1", "status": "unknown" if unknown else "applied",
            "effect_domain": "native_input", "native_delivery": "unknown" if unknown else "delivered", "action": action,
            "reason_code": None, "detail": None, "retry": "never", "successor": None, "attribution": None,
        }}
        if unknown:
            manifest_path = directory / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest.update(status="tainted", tainted=True)
            manifest_path.write_bytes(canonical(manifest))
        else:
            successor = json.loads((fixtures / "text-menu-v2-card-only-root.json").read_text())
            successor.update(snapshot_id="v2-menu-22", sequence=22)
            successor["menu"].update(native_snapshot_id="managed-source-22", revision=0)
            successor["session"] = first["session"]
            events.insert(6, {"schema": AGENT_RUN_EVENT_SCHEMA, "sequence": 0, "recorded_at": "2026-08-25T00:00:03.000Z",
                              "kind": "text_observed_successor", "payload": {"decision_id": "decision-1", "successor": successor}})
        for index, event in enumerate(events, 1):
            event["sequence"] = index
        self._rewrite_events(directory, events)
        return directory

    def test_v2_full_catalog_order_and_selection_are_bound(self) -> None:
        directory = self._text_v2_native("v2-native-catalog", unknown=False)
        verifier = AgentRunEvidenceVerifier()
        self.assertTrue(verifier.verify(directory).passed)
        original = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        for change in ("order", "count", "selection", "leaf", "result_profile", "successor_profile"):
            events = json.loads(json.dumps(original))
            snapshot = events[1]["payload"]["snapshot"]
            if change == "order":
                snapshot["menu_actions"]["actions"].reverse()
            elif change == "count":
                snapshot["menu_actions"]["total_count"] = 3
            elif change == "selection":
                snapshot["menu"]["selection"][0]["referent_id"] = "missing-card"
            elif change == "leaf":
                snapshot["menu_actions"]["actions"][0]["subject_referent_id"] = "missing-card"
            elif change == "result_profile":
                events[5]["payload"]["result"]["input_profile"] = "text-menu-v1"
            else:
                events[6]["payload"]["successor"]["schema"] = "sts2.player-environment/text-menu-snapshot-1"
            self._rewrite_events(directory, events)
            self.assertFalse(verifier.verify(directory).passed, change)
        self._rewrite_events(directory, original)
        self.assertTrue(verifier.verify(directory).passed)

    def test_v2_unknown_keeps_exact_native_action_and_never_claims_successor(self) -> None:
        directory = self._text_v2_native("v2-native-unknown", unknown=True)
        verifier = AgentRunEvidenceVerifier()
        self.assertTrue(verifier.verify(directory).passed)
        original = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        for field, value in (("action", {**original[5]["payload"]["result"]["action"], "action_id": "other"}),
                             ("retry", "reobserve"), ("successor", original[1]["payload"]["snapshot"])):
            events = json.loads(json.dumps(original))
            events[5]["payload"]["result"][field] = value
            self._rewrite_events(directory, events)
            self.assertFalse(verifier.verify(directory).passed, field)
        self._rewrite_events(directory, original)
        continued = json.loads(json.dumps(original))
        continued.append({"schema": AGENT_RUN_EVENT_SCHEMA, "sequence": len(continued) + 1,
                          "recorded_at": "2026-08-25T00:00:04.000Z", "kind": "text_decision_input",
                          "payload": {"decision_id": "decision-2", "snapshot": original[1]["payload"]["snapshot"]}})
        self._rewrite_events(directory, continued)
        self.assertEqual(verifier.verify(directory).findings[0].code, "unknown_retry")

    def test_v2_native_delivery_is_sealed_by_existing_evidence_inventory(self) -> None:
        directory = self._text_v2_native("v2-sealed-native", unknown=False)
        verifier = AgentRunEvidenceVerifier()
        verified = verifier.verify(directory)
        self.assertTrue(verified.passed)
        self.assertEqual(verified.require_value().content_id, self._content_id(directory))
        events_path = directory / "events.jsonl"
        events_path.write_bytes(events_path.read_bytes().replace(b"v2-play-card-C", b"v2-play-card-X", 1))
        self.assertEqual(verifier.verify(directory).findings[0].code, "checksum_mismatch")

    def test_v2_native_not_delivered_has_no_observed_successor(self) -> None:
        directory = self._text_v2_native("v2-native-not-delivered", unknown=False)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[5]["kind"] = "text_menu_not_applied"
        events[5]["payload"]["result"].update(status="not_applied", native_delivery="not_delivered", retry="reobserve")
        events.pop(6)
        for index, event in enumerate(events, 1):
            event["sequence"] = index
        self._rewrite_events(directory, events)
        verifier = AgentRunEvidenceVerifier()
        self.assertTrue(verifier.verify(directory).passed)
        events[5]["payload"]["result"]["native_delivery"] = "delivered"
        self._rewrite_events(directory, events)
        self.assertFalse(verifier.verify(directory).passed)

    def test_v2_unknown_cannot_be_reinterpreted_as_generic_receipt_success(self) -> None:
        directory = self._text_v2_native("v2-generic-receipt", unknown=True)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        action_id = events[2]["payload"]["resolved_bound_action_id"]
        successor = self._snapshot("generic-successor", 23)
        receipt = {"protocol_version": "1.0.0", "schema": "sts2.player-environment/receipt-1",
                   "request_id": "request-v2-generic-receipt-decision-1", "delivery": "delivered",
                   "action": {"bound_action_id": action_id, "verb": "play", "arguments": []},
                   "retry": {"allowed": False, "reason": "fixture"}, "successor": successor}
        for kind, payload in (("receipt", {"decision_id": "decision-1", "receipt": receipt}),
                              ("successor", {"decision_id": "decision-1", "successor": successor})):
            events.append({"schema": AGENT_RUN_EVENT_SCHEMA, "sequence": len(events) + 1,
                           "recorded_at": "2026-08-25T00:00:04.000Z", "kind": kind, "payload": payload})
        self._rewrite_events(directory, events)
        self.assertEqual(AgentRunEvidenceVerifier().verify(directory).findings[0].code, "text_profile_association")

    def test_text_profiles_reject_all_generic_receipt_channels(self) -> None:
        verifier = AgentRunEvidenceVerifier()
        for profile in ("v1", "v2"):
            for kind in ("receipt", "receipt_rejected", "successor"):
                name = f"{profile}-generic-{kind}"
                directory = self._text_evidence(name) if profile == "v1" else self._text_v2_evidence(name)
                events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
                events.append({"schema": AGENT_RUN_EVENT_SCHEMA, "sequence": len(events) + 1,
                               "recorded_at": "2026-08-25T00:00:04.000Z", "kind": kind, "payload": {}})
                self._rewrite_events(directory, events)
                self.assertEqual(verifier.verify(directory).findings[0].code, "text_profile_association", name)

    def test_v2_system_selection_dispatch_counters_match_selected_domain(self) -> None:
        directory = self._text_v2_evidence("v2-counter-domain")
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[4]["payload"].update(native_submissions_used=1, menu_navigations_used=0)
        self._rewrite_events(directory, events)
        self.assertEqual(AgentRunEvidenceVerifier().verify(directory).findings[0].code, "text_dispatch_binding")

    def _v2_cumulative_dispatches(self, name: str) -> tuple[Path, list[dict[str, Any]]]:
        directory = self._text_v2_evidence(name)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        targeted = events[5]["payload"]["result"]["successor"]
        target_action = targeted["menu_actions"]["actions"][0]
        confirmation = json.loads(json.dumps(targeted))
        confirmation.update(snapshot_id="v2-menu-22", sequence=22)
        confirmation["menu"].update(cursor="card_confirmation", revision=2,
                                    selection=[*targeted["menu"]["selection"], {"role": "target", "referent_id": "enemy-E"}])
        leaf = {"action_id": "v2-play-targeted-C-E", "kind": "native_input", "verb": "play", "label": "Play Strike",
                "subject_referent_id": "card-C", "arguments": [{"role": "target", "referent_id": "enemy-E"}],
                "effect_domain": "native_input"}
        confirmation["menu_actions"].update(actions=[leaf, targeted["menu_actions"]["actions"][1]],
                                            materialized_count=2, total_count=2)

        def event(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {"schema": AGENT_RUN_EVENT_SCHEMA, "sequence": 0, "recorded_at": "2026-08-25T00:00:04.000Z",
                    "kind": kind, "payload": payload}

        for number, snapshot, action, result_kind, result_successor, native_count, menu_count in (
            (2, targeted, target_action, "menu_navigation", confirmation, 0, 2),
            (3, confirmation, leaf, "text_menu_not_applied", None, 1, 2),
        ):
            decision_id = f"decision-{number}"
            ids = [candidate["action_id"] for candidate in snapshot["menu_actions"]["actions"]]
            decision = json.loads(json.dumps(events[2]["payload"]["decision"]))
            decision.update(decision_id=decision_id, snapshot_id=snapshot["snapshot_id"],
                            candidate_digest=sha256(json.dumps(ids, separators=(",", ":")).encode()),
                            candidate_count=len(ids), scores=[1.0, 0.0], selected_index=0)
            events.extend([
                event("text_decision_input", {"decision_id": decision_id, "snapshot": snapshot}),
                event("decision", {"decision": decision, "resolved_bound_action_id": action["action_id"]}),
                event("controller_acquired", {}),
                event("text_menu_dispatch_attempt", {"decision_id": decision_id, "action_id": action["action_id"],
                                                     "effect_domain": action["effect_domain"],
                                                     "native_submissions_used": native_count,
                                                     "menu_navigations_used": menu_count}),
                event(result_kind, {"decision_id": decision_id, **({"action_id": action["action_id"]} if result_kind == "menu_navigation" else {}),
                                    "result": {"protocol_version": "1.0.0", "schema": "sts2.player-environment/text-menu-action-result-2",
                                               "input_profile": "text-menu-v2", "request_id": f"request-{name}-{decision_id}",
                                               "status": "applied" if result_kind == "menu_navigation" else "not_applied",
                                               "effect_domain": action["effect_domain"],
                                               "native_delivery": None if result_kind == "menu_navigation" else "not_delivered",
                                               "action": action, "reason_code": None, "detail": None,
                                               "retry": "never" if result_kind == "menu_navigation" else "reobserve",
                                               "successor": result_successor, "attribution": None}}),
                event("controller_released", {}),
            ])
        for index, item in enumerate(events, 1):
            item["sequence"] = index
        self._rewrite_events(directory, events)
        return directory, events

    def test_v2_dispatch_counters_accumulate_across_selection_and_native_leaf(self) -> None:
        directory, events = self._v2_cumulative_dispatches("v2-cumulative-counters")
        verifier = AgentRunEvidenceVerifier()
        self.assertTrue(verifier.verify(directory).passed)
        for index, field, bad_value in ((10, "menu_navigations_used", 1), (16, "native_submissions_used", 0),
                                        (16, "menu_navigations_used", 3)):
            changed = json.loads(json.dumps(events))
            changed[index]["payload"][field] = bad_value
            self._rewrite_events(directory, changed)
            self.assertEqual(verifier.verify(directory).findings[0].code, "text_dispatch_binding")

    def test_v2_counters_restart_only_after_human_to_new_auto_budget(self) -> None:
        directory, original = self._v2_cumulative_dispatches("v2-budget-restart")
        active = {"state": "active", "max_submissions": 4, "submissions_used": 0,
                  "max_policy_calls": 4, "policy_calls_used": 0, "deadline_ms": 10000,
                  "elapsed_ms": 0, "remaining_ms": 10000, "exhausted_reason": None, "ended_reason": None}
        inactive = {**active, "state": "inactive", "submissions_used": 1, "policy_calls_used": 1,
                    "ended_reason": "human_recovery"}

        def mode_event(mode: str, budget: dict[str, Any]) -> dict[str, Any]:
            return {"schema": AGENT_RUN_EVENT_SCHEMA, "sequence": 0,
                    "recorded_at": "2026-08-25T00:00:03.000Z", "kind": "mode_changed",
                    "payload": {"mode": mode, "autonomy_budget": budget}}

        def reseal(events: list[dict[str, Any]]) -> None:
            for index, event in enumerate(events, 1):
                event["sequence"] = index
            self._rewrite_events(directory, events)

        first_release = next(index for index, event in enumerate(original) if event["kind"] == "controller_released")
        restarted = json.loads(json.dumps(original))
        restarted.insert(1, mode_event("auto", active))
        restarted[first_release + 2:first_release + 2] = [mode_event("human", inactive), mode_event("auto", active)]
        dispatches = [event for event in restarted if event["kind"] == "text_menu_dispatch_attempt"]
        dispatches[1]["payload"].update(native_submissions_used=0, menu_navigations_used=1)
        dispatches[2]["payload"].update(native_submissions_used=1, menu_navigations_used=1)
        reseal(restarted)
        verifier = AgentRunEvidenceVerifier()
        self.assertTrue(verifier.verify(directory).passed)

        forged = json.loads(json.dumps(original))
        forged.insert(1, mode_event("auto", active))
        forged.insert(first_release + 2, mode_event("auto", active))
        forged_dispatches = [event for event in forged if event["kind"] == "text_menu_dispatch_attempt"]
        forged_dispatches[1]["payload"].update(native_submissions_used=0, menu_navigations_used=1)
        reseal(forged)
        self.assertEqual(verifier.verify(directory).findings[0].code, "text_dispatch_binding")
        missing_budget = json.loads(json.dumps(restarted))
        missing_budget[1]["payload"].pop("autonomy_budget")
        reseal(missing_budget)
        self.assertEqual(verifier.verify(directory).findings[0].code, "budget_association")

    def test_v2_budget_counters_follow_initial_auto_exhaustion_and_shadow(self) -> None:
        active = {"state": "active", "max_submissions": 4, "submissions_used": 0,
                  "max_policy_calls": 4, "policy_calls_used": 0, "deadline_ms": 10000,
                  "elapsed_ms": 0, "remaining_ms": 10000, "exhausted_reason": None,
                  "ended_reason": None}
        exhausted = {**active, "state": "exhausted", "elapsed_ms": 10000,
                     "remaining_ms": 0, "exhausted_reason": "deadline"}

        def event(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {"schema": AGENT_RUN_EVENT_SCHEMA, "sequence": 0,
                    "recorded_at": "2026-08-25T00:00:03.000Z", "kind": kind,
                    "payload": payload}

        # Runtime may start in Auto without a mode_changed event. Shadow belongs
        # to the same autonomy budget; only an actual handoff permits a reset.
        cases = (
            ("initial-auto-forged-reset", [], True, False),
            ("exhaustion-restart", [event("autonomy_budget_exhausted", {
                "reason": "deadline", "budget": exhausted, "controller": "released"})], True, True),
            ("shadow-keeps-counts", [event("mode_changed", {
                "mode": "shadow", "autonomy_budget": active})], False, True),
            ("shadow-forged-reset", [event("mode_changed", {
                "mode": "shadow", "autonomy_budget": active})], True, False),
        )
        for name, handoff, reset, expected in cases:
            with self.subTest(name=name):
                directory, events = self._v2_cumulative_dispatches(name)
                if name == "exhaustion-restart":
                    events.insert(1, event("mode_changed", {"mode": "auto", "autonomy_budget": active}))
                first_release = next(index for index, item in enumerate(events)
                                     if item["kind"] == "controller_released")
                events[first_release + 1:first_release + 1] = [
                    *handoff, event("mode_changed", {"mode": "auto", "autonomy_budget": active})]
                if reset:
                    dispatches = [item for item in events if item["kind"] == "text_menu_dispatch_attempt"]
                    dispatches[1]["payload"].update(native_submissions_used=0, menu_navigations_used=1)
                    dispatches[2]["payload"].update(native_submissions_used=1, menu_navigations_used=1)
                for sequence, item in enumerate(events, 1):
                    item["sequence"] = sequence
                self._rewrite_events(directory, events)
                result = AgentRunEvidenceVerifier().verify(directory)
                self.assertEqual(result.passed, expected, result.findings)
                if not expected:
                    self.assertEqual(result.findings[0].code, "text_dispatch_binding")

    def test_v2_dispatch_requires_new_active_mode_after_handoff(self) -> None:
        budget = {"state": "exhausted", "max_submissions": 4, "submissions_used": 0,
                  "max_policy_calls": 4, "policy_calls_used": 0, "deadline_ms": 10000,
                  "elapsed_ms": 10000, "remaining_ms": 0, "exhausted_reason": "deadline",
                  "ended_reason": None}
        active = {**budget, "state": "active", "elapsed_ms": 0, "remaining_ms": 10000,
                  "exhausted_reason": None}
        for name, kind, payload in (
            ("exhausted", "autonomy_budget_exhausted", {
                "reason": "deadline", "budget": budget, "controller": "released"}),
            ("human", "mode_changed", {"mode": "human"}),
            ("shadow", "mode_changed", {"mode": "shadow", "autonomy_budget": active}),
            ("handoff", "handoff_to_human", {"reason": "auto_surface_not_admitted"}),
            ("one-step", "one_step_completed", {}),
            ("closed", "fail_closed", {"reason": "policy_unavailable"}),
        ):
            with self.subTest(name=name):
                directory, events = self._v2_cumulative_dispatches("v2-no-resume-" + name)
                release = next(index for index, item in enumerate(events)
                               if item["kind"] == "controller_released")
                events.insert(release + 1, {
                    "schema": AGENT_RUN_EVENT_SCHEMA, "sequence": 0,
                    "recorded_at": "2026-08-25T00:00:03.000Z", "kind": kind,
                    "payload": payload})
                for sequence, item in enumerate(events, 1):
                    item["sequence"] = sequence
                self._rewrite_events(directory, events)
                report = AgentRunEvidenceVerifier().verify(directory)
                self.assertFalse(report.passed)
                self.assertEqual(report.findings[0].code, "text_dispatch_binding")

    def test_v2_manifest_port_and_renderer_profile_are_closed(self) -> None:
        directory = self._text_v2_evidence("v2-manifest")
        verifier = AgentRunEvidenceVerifier()
        self.assertTrue(verifier.verify(directory).passed)
        for change in ("port", "digest", "reads"):
            policy_path = directory / "policy-manifest.json"
            policy = json.loads(policy_path.read_text())
            if change == "port":
                policy["adapter"]["protocol"] = "sts2.policy-runtime/decision-only-ndjson-1"
            elif change == "digest":
                policy["requirements"]["candidate_order_digest"] = "sha256-json-bound-action-id-order"
            else:
                policy["requirements"]["reads"] = ["surface_card"]
            policy_path.write_bytes(canonical(policy))
            digest = sha256(json.dumps(policy, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode())
            manifest_path = directory / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["policy_manifest_sha256"] = digest
            manifest_path.write_bytes(canonical(manifest))
            attestation_path = directory / "adapter-attestation.json"
            attestation = json.loads(attestation_path.read_text())
            attestation["policy_manifest_sha256"] = digest
            attestation["expected"] = policy["adapter"]
            attestation["actual"] = policy["adapter"]
            attestation_path.write_bytes(canonical(attestation))
            self._rewrite_events(directory, [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()])
            self.assertFalse(verifier.verify(directory).passed, change)
            directory = self._text_v2_evidence("v2-manifest-" + change)
        directory = self._text_v2_evidence("v2-renderer-profile")
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[1]["payload"]["snapshot"]["schema"] = "sts2.player-environment/text-menu-snapshot-1"
        self._rewrite_events(directory, events)
        self.assertEqual(verifier.verify(directory).findings[0].code, "schema_literal")

    def test_text_navigation_is_verified_without_native_receipt(self) -> None:
        directory = self._text_evidence("text-nav")
        self.assertTrue(AgentRunEvidenceVerifier().verify(directory).passed)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[5]["payload"]["result"]["native_delivery"] = "delivered"
        self._rewrite_events(directory, events)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)

    def test_text_native_delivery_requires_terminal_observation(self) -> None:
        directory = self._text_evidence("text-native", native=True)
        self.assertTrue(AgentRunEvidenceVerifier().verify(directory).passed)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events.pop(6)
        for index, event in enumerate(events, 1): event["sequence"] = index
        self._rewrite_events(directory, events)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)

    def test_text_decision_rejects_digest_and_selected_action_drift(self) -> None:
        for change in ("candidate_digest", "resolved_bound_action_id"):
            directory = self._text_evidence("text-drift-" + change)
            events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
            if change == "candidate_digest": events[2]["payload"]["decision"][change] = "0" * 64
            else: events[2]["payload"][change] = "wrong-action"
            self._rewrite_events(directory, events)
            self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)

    def test_text_unknown_requires_tainted_manifest_and_never_retry(self) -> None:
        directory = self._text_evidence("text-unknown", native=True)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[5]["kind"] = "text_native_unknown"
        result = events[5]["payload"]["result"]
        result.update(status="unknown", native_delivery="unknown", successor=None, retry="never")
        events.pop(6)
        for index, event in enumerate(events, 1): event["sequence"] = index
        self._rewrite_events(directory, events)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(status="tainted", tainted=True)
        manifest_path.write_bytes(canonical(manifest))
        self._rewrite_events(directory, events)
        self.assertTrue(AgentRunEvidenceVerifier().verify(directory).passed)
        result["retry"] = "reobserve"
        self._rewrite_events(directory, events)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)

    def test_text_result_rejects_wrong_request(self) -> None:
        directory = self._text_evidence("text-request")
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[5]["payload"]["result"]["request_id"] = "wrong"
        self._rewrite_events(directory, events)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)

    def test_text_not_applied_is_not_a_native_delivery(self) -> None:
        directory = self._text_evidence("text-not-applied", native=True)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[5]["kind"] = "text_menu_not_applied"
        result = events[5]["payload"]["result"]
        result.update(status="not_applied", native_delivery="not_delivered", successor=None, retry="reobserve")
        events.pop(6)
        for index, event in enumerate(events, 1): event["sequence"] = index
        self._rewrite_events(directory, events)
        self.assertTrue(AgentRunEvidenceVerifier().verify(directory).passed)
        result["native_delivery"] = "delivered"
        self._rewrite_events(directory, events)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)

    def test_text_rejected_result_requires_real_correlation_mismatch_and_taint(self) -> None:
        directory = self._text_evidence("text-rejected", native=True)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[5]["kind"] = "text_menu_result_rejected"
        events[5]["payload"] = {"decision_id": "decision-1", "expected_request_id": "request-text-rejected-decision-1", "expected_action_id": "native-end", "result": events[5]["payload"]["result"]}
        events.pop(6)
        for index, event in enumerate(events, 1): event["sequence"] = index
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(status="tainted", tainted=True)
        manifest_path.write_bytes(canonical(manifest))
        self._rewrite_events(directory, events)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)
        events[5]["payload"]["result"]["request_id"] = "wrong-request"
        self._rewrite_events(directory, events)
        self.assertTrue(AgentRunEvidenceVerifier().verify(directory).passed)

    def test_text_dispatch_and_result_must_match_selected_action(self) -> None:
        for field in ("dispatch", "result"):
            directory = self._text_evidence("text-action-" + field, native=True)
            events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
            if field == "dispatch": events[4]["payload"]["action_id"] = "wrong-action"
            else: events[5]["payload"]["result"]["action"]["action_id"] = "wrong-action"
            self._rewrite_events(directory, events)
            self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)

    def test_text_input_must_precede_decision_and_be_complete(self) -> None:
        directory = self._text_evidence("text-input")
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        events[1]["payload"]["snapshot"]["menu_actions"]["status"] = "truncated"
        self._rewrite_events(directory, events)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)
        events = [event for event in events if event["kind"] != "text_decision_input"]
        for index, event in enumerate(events, 1): event["sequence"] = index
        self._rewrite_events(directory, events)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)

    def test_text_one_step_runtime_budget_events_use_strict_current_shape(self) -> None:
        directory = self._text_evidence("text-one-step", native=True)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        budget = {"state": "inactive", "max_submissions": 2, "submissions_used": 1,
                  "max_policy_calls": 3, "policy_calls_used": 1, "deadline_ms": 10000,
                  "elapsed_ms": 100, "remaining_ms": 9900, "exhausted_reason": None,
                  "ended_reason": "mode_changed"}
        def event(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {"schema": AGENT_RUN_EVENT_SCHEMA, "sequence": 0, "recorded_at": "2026-08-25T00:00:03.000Z", "kind": kind, "payload": payload}
        events.insert(0, event("mode_changed", {"mode": "one_step", "autonomy_budget": {**budget, "state": "active", "ended_reason": None, "submissions_used": 0, "policy_calls_used": 0}}))
        events.append(event("one_step_completed", {"autonomy_budget": budget}))
        events.append(event("stopped", {"autonomy_budget": budget, "controller": "released"}))
        for index, item in enumerate(events, 1): item["sequence"] = index
        self._rewrite_events(directory, events)
        self.assertTrue(AgentRunEvidenceVerifier().verify(directory).passed)
        events[-2]["payload"]["autonomy_budget"]["remaining_ms"] = 9901
        self._rewrite_events(directory, events)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)

    def test_budget_exhaustion_and_release_failure_are_typed(self) -> None:
        directory = self._text_evidence("text-exhausted")
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        budget = {"state": "exhausted", "max_submissions": 1, "submissions_used": 1,
                  "max_policy_calls": 2, "policy_calls_used": 1, "deadline_ms": 10000,
                  "elapsed_ms": 100, "remaining_ms": 9900,
                  "exhausted_reason": "submission_attempt_limit", "ended_reason": None}
        events.append({"schema": AGENT_RUN_EVENT_SCHEMA, "sequence": len(events)+1,
                       "recorded_at": "2026-08-25T00:00:03.000Z",
                       "kind": "autonomy_budget_exhausted",
                       "payload": {"reason": "submission_attempt_limit", "budget": budget, "controller": "released"}})
        self._rewrite_events(directory, events)
        self.assertTrue(AgentRunEvidenceVerifier().verify(directory).passed)
        events[-1]["payload"]["reason"] = "deadline"
        self._rewrite_events(directory, events)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)

    def test_text_referent_and_transport_identity_match_public_codec(self) -> None:
        directory = self._text_evidence("text-referent", native=True)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        referent = {"referent_id": "card.1", "role": "card", "kind": "entity", "label": "Strike",
                    "state": {"visible": True, "enabled": True, "selected": False,
                              "focused": False, "observation_basis": "native_visible_fact"},
                    "properties_schema": None, "properties": None}
        events[1]["payload"]["snapshot"]["referents"].append(referent)
        events[1]["payload"]["snapshot"]["menu_actions"]["actions"][0]["subject_referent_id"] = "card.1"
        events[5]["payload"]["result"]["action"]["subject_referent_id"] = "card.1"
        self._rewrite_events(directory, events)
        self.assertTrue(AgentRunEvidenceVerifier().verify(directory).passed)
        for field, bad in (("kind", "linked_choice"), ("observation_basis", "inferred")):
            broken = json.loads(json.dumps(events))
            state = broken[1]["payload"]["snapshot"]["referents"][0]
            if field == "kind": state[field] = bad
            else: state["state"][field] = bad
            self._rewrite_events(directory, broken)
            self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)
        broken = json.loads(json.dumps(events))
        broken[1]["payload"]["snapshot"]["snapshot_id"] = "bad/id"
        self._rewrite_events(directory, broken)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)
        broken = json.loads(json.dumps(events))
        broken[1]["payload"]["snapshot"]["menu_actions"]["actions"][0]["action_id"] = "bad/id"
        self._rewrite_events(directory, broken)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)
        broken = json.loads(json.dumps(events))
        broken[1]["payload"]["snapshot"]["interaction"]["content_schema"] = "sts2.player-environment/surface/bad-page-1"
        self._rewrite_events(directory, broken)
        self.assertFalse(AgentRunEvidenceVerifier().verify(directory).passed)
