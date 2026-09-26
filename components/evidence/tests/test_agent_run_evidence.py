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

    def _evidence(self, name: str = "run-1") -> Path:
        directory = self.root / name
        directory.mkdir()
        adapter = {
            "id": "fixture-adapter",
            "version": "1.0.0",
            "protocol": "sts2.policy-runtime/decision-only-ndjson-1",
            "code_sha256": "d" * 64,
        }
        policy_manifest = {
            "schema": POLICY_MANIFEST_SCHEMA,
            "manifest_id": "manifest-1",
            "policy": {"id": "policy-1", "version": "1.0.0"},
            "adapter": adapter,
            "artifact": {"sha256": "b" * 64},
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
    def _text_snapshot(self, snapshot_id: str, sequence: int, action: dict[str, Any], cursor: str = "root") -> dict[str, Any]:
        snapshot = self._snapshot(snapshot_id, sequence)
        snapshot.pop("bound_actions")
        snapshot.pop("reads")
        snapshot.update(schema="sts2.player-environment/text-menu-snapshot-1", input_profile="text-menu-v1")
        snapshot["interaction"]["content_schema"] = "sts2.player-environment/surface/combat-text-menu-1"
        snapshot["menu"] = {"cursor": cursor, "revision": sequence, "native_snapshot_id": "native-1"}
        snapshot["menu_actions"] = {"status": "complete", "materialized_count": 1, "total_count": 1, "ordering_semantics": "connector_order", "actions": [action]}
        return snapshot

    def _text_evidence(self, name: str, *, native: bool = False) -> Path:
        directory = self._evidence(name)
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
        for index, item in enumerate(events, 1): item["sequence"] = index
        self._rewrite_events(directory, events)
        return directory

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
