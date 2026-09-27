from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import tomllib
import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from unittest.mock import patch

from sts2_platform_evidence.cli import main
from sts2_platform_evidence.core import VerificationResult, VerifierDescriptor, VerifierRegistry
from sts2_platform_evidence.human_session_bundle_v1 import (
    BUNDLE_SCHEMA,
    HumanSessionBundleVerifier,
    load_collection_profile_from_value,
)
from sts2_platform_evidence.human_session_bundle import VersionedHumanSessionBundleVerifier
from sts2_platform_evidence.store import ContentAddressedStore
from sts2_platform_evidence.transfer import DirectoryReceiver, DirectoryTransferManifest


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


class EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profile_value = self._profile_value()
        self.profile = load_collection_profile_from_value(self.profile_value)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _profile_value(self) -> dict[str, Any]:
        digest = "a" * 64
        return {
            "schema": "stpd/human-collection-profile-v1",
            "profile_id": "human-mac-combat-v1",
            "platform": "osx-arm64",
            "game": {
                "version": "v0.111.0",
                "commit": "41cef1ea",
                "main_assembly_sha256": digest,
                "main_assembly_mvid": "11111111-1111-4111-8111-111111111111",
            },
            "connector": {
                "source_revision": "b" * 40,
                "source_digest_sha256": digest,
                "artifact_sha256": digest,
                "mvid": "22222222-2222-4222-8222-222222222222",
            },
            "annotator": {
                "source_revision": "c" * 40,
                "source_digest_sha256": digest,
                "artifact_sha256": digest,
                "mvid": "33333333-3333-4333-8333-333333333333",
            },
            "player_environment_protocol": "1.0.0",
            "modset": {"status": "canary_exact_observer_modset", "fingerprint": digest},
            "record_schema": "sts2.human-annotator/decision-record-1",
            "allowed_action_families": [
                "ordinary_combat.play_card",
                "ordinary_combat.end_turn",
            ],
        }

    def _record(self, session_id: str = "session-test") -> dict[str, Any]:
        profile = self.profile_value
        return {
            "schema_version": 1,
            "schema": "sts2.human-annotator/decision-record-1",
            "record_id": "record-1",
            "session_id": session_id,
            "run_id": "run-0001",
            "sequence": 1,
            "decision_family": "ordinary_combat",
            "action": {"verb": "play"},
            "environment": {
                "game": {
                    "version": profile["game"]["version"],
                    "commit": profile["game"]["commit"],
                    "main_assembly_sha256": profile["game"]["main_assembly_sha256"],
                    "main_assembly_module_version_id": profile["game"]["main_assembly_mvid"],
                },
                "connector": {
                    "source_revision": profile["connector"]["source_revision"],
                    "source_digest_sha256": profile["connector"]["source_digest_sha256"],
                    "sha256": profile["connector"]["artifact_sha256"],
                    "module_version_id": profile["connector"]["mvid"],
                },
                "annotator": {
                    "source_revision": profile["annotator"]["source_revision"],
                    "source_digest_sha256": profile["annotator"]["source_digest_sha256"],
                    "sha256": profile["annotator"]["artifact_sha256"],
                    "module_version_id": profile["annotator"]["mvid"],
                },
                "player_environment_protocol": profile["player_environment_protocol"],
                "modset_status": profile["modset"]["status"],
                "modset_fingerprint": profile["modset"]["fingerprint"],
            },
        }

    def _bundle(self, name: str = "bundle") -> Path:
        bundle = self.root / name
        raw = bundle / "raw"
        (raw).mkdir(parents=True)
        (bundle / "audit").mkdir()
        (bundle / "export").mkdir()
        (bundle / "profile").mkdir()
        record = json.dumps(self._record(), separators=(",", ":"))
        (raw / "run-0001.jsonl").write_bytes((record + "\n").encode("utf-8"))
        (bundle / "export" / "decisions.jsonl").write_bytes((record + "\n").encode("utf-8"))
        (raw / "recording-manifest.json").write_text(
            canonical({
                "schema": "sts2.human-annotator/recording-manifest-1",
                "session_id": "session-test",
                "platform": "osx-arm64",
            }) + "\n",
            encoding="utf-8",
        )
        (raw / "coverage.json").write_text(
            canonical({
                "session_id": "session-test",
                "admitted_records": 1,
                "invalidations_by_reason": {},
            }) + "\n",
            encoding="utf-8",
        )
        audit = {
            "schema": "sts2.human-annotator/session-bundle-audit-1",
            "status": "pass",
            "valid_records": 1,
            "invalid_records": 0,
            "invalidations": 0,
        }
        (bundle / "audit" / "audit-report.json").write_text(canonical(audit) + "\n", encoding="utf-8")
        (bundle / "profile" / "collection-profile.json").write_text(
            canonical(self.profile_value) + "\n", encoding="utf-8"
        )
        export_sha = sha_file(bundle / "export" / "decisions.jsonl")
        raw_hashes = {path.name: sha_file(path) for path in sorted(raw.iterdir())}
        attestation = {
            "attested": True,
            "method": "explicit_owner_pack",
            "worker_id": "human-001",
            "machine_verifiable": False,
        }
        identity = {
            "schema": BUNDLE_SCHEMA,
            "session_id": "session-test",
            "collection_profile_id": self.profile.profile_id,
            "collection_profile_sha256": self.profile.sha256,
            "campaign_id": "human-combat-smoke-2026-08",
            "worker_id": "human-001",
            "human_origin_attestation": attestation,
            "record_count": 1,
            "run_ids": ["run-0001"],
            "export_sha256": export_sha,
            "raw_file_sha256": raw_hashes,
            "audit": {"status": "pass", "valid_records": 1, "invalid_records": 0, "invalidations": 0},
        }
        manifest = {
            "schema_version": 1,
            "schema": BUNDLE_SCHEMA,
            "bundle_content_id": sha_bytes(canonical(identity).encode()),
            "session_id": "session-test",
            "collection_profile_id": self.profile.profile_id,
            "collection_profile_sha256": self.profile.sha256,
            "campaign_id": "human-combat-smoke-2026-08",
            "worker_id": "human-001",
            "human_origin_attestation": attestation,
            "record_count": 1,
            "run_ids": ["run-0001"],
            "export_sha256": export_sha,
            "audit_status": "pass",
            "content_identity": identity,
        }
        (bundle / "session-bundle-manifest.json").write_text(canonical(manifest) + "\n", encoding="utf-8")
        self._checksums(bundle)
        return bundle

    def _checksums(self, directory: Path) -> None:
        lines = []
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.name != "checksums.sha256":
                lines.append(f"{sha_file(path)}  {path.relative_to(directory).as_posix()}")
        (directory / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _verify(self, bundle: Path, profile: Any | None = None):
        return HumanSessionBundleVerifier().verify(bundle, profile or self.profile)

    def test_valid_bundle_and_typed_registry(self) -> None:
        bundle = self._bundle()
        verifier = HumanSessionBundleVerifier()
        result = verifier.verify(bundle, self.profile)
        self.assertTrue(result.passed)
        self.assertEqual(result.require_value().record_count, 1)
        registry = VerifierRegistry()
        registry.register(verifier.descriptor, verifier.verify)
        registered = registry.verify("human-session-bundle-v1", bundle, self.profile)
        self.assertTrue(registered.passed)

    def test_component_and_python_package_versions_are_one_fact(self) -> None:
        root = Path(__file__).resolve().parents[1]
        component = json.loads((root / "component.json").read_text(encoding="utf-8"))
        package = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(component["version"], package["project"]["version"])

    def test_tamper_checksum_and_duplicate_checksum_path_fail_closed(self) -> None:
        bundle = self._bundle()
        export = bundle / "export" / "decisions.jsonl"
        export.write_text(export.read_text(encoding="utf-8") + "tamper\n", encoding="utf-8")
        result = self._verify(bundle)
        self.assertFalse(result.passed)
        self.assertEqual(result.findings[0].code, "checksum_mismatch")

        duplicate = self._bundle("duplicate-checksum")
        checksums = duplicate / "checksums.sha256"
        checksums.write_text(checksums.read_text(encoding="utf-8") + checksums.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
        result = self._verify(duplicate)
        self.assertEqual(result.findings[0].code, "checksum_manifest_duplicate")

    def test_profile_drift_is_detected_against_admitted_profile(self) -> None:
        bundle = self._bundle()
        drifted = json.loads(json.dumps(self.profile_value))
        drifted["platform"] = "win-x64"
        profile = load_collection_profile_from_value(drifted)
        result = self._verify(bundle, profile)
        self.assertEqual(result.findings[0].code, "embedded_profile_drift")

    def test_raw_export_mismatch_is_checked_after_manifest_facts(self) -> None:
        bundle = self._bundle()
        export = bundle / "export" / "decisions.jsonl"
        export.write_text("different\n", encoding="utf-8")
        manifest_path = bundle / "session-bundle-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        export_sha = sha_file(export)
        manifest["export_sha256"] = export_sha
        manifest["content_identity"]["export_sha256"] = export_sha
        manifest["bundle_content_id"] = sha_bytes(canonical(manifest["content_identity"]).encode())
        manifest_path.write_text(canonical(manifest) + "\n", encoding="utf-8")
        self._checksums(bundle)
        result = self._verify(bundle)
        self.assertEqual(result.findings[0].code, "raw_export_mismatch")

    def test_store_is_content_addressed_and_retry_is_immutable(self) -> None:
        source = self.root / "payload"
        source.mkdir()
        (source / "file.txt").write_text("one", encoding="utf-8")
        store = ContentAddressedStore(self.root / "store")
        first = store.put_directory(source)
        retry = store.put_directory(source)
        self.assertEqual(first.status, "stored")
        self.assertEqual(retry.status, "reused")
        self.assertEqual(first.content_id, retry.content_id)
        (source / "file.txt").write_text("two", encoding="utf-8")
        changed = store.put_directory(source)
        self.assertNotEqual(first.content_id, changed.content_id)

    def test_receiver_promotes_reuses_quarantines_partial_checksum_and_collision(self) -> None:
        bundle = self._bundle()
        manifest = DirectoryTransferManifest.from_directory(
            bundle, content_id="d" * 64, artifact_type="human-session-bundle"
        )
        store = ContentAddressedStore(self.root / "receiver")
        receiver = DirectoryReceiver(store)
        first = receiver.receive(bundle, manifest)
        self.assertEqual(first.status, "promoted")
        retry = receiver.receive(bundle, manifest)
        self.assertEqual(retry.status, "reused")
        self.assertTrue(first.directory and first.directory.is_dir())

        tampered = self._bundle("receiver-tampered")
        transfer = DirectoryTransferManifest.from_directory(
            tampered, content_id="e" * 64, artifact_type="human-session-bundle"
        )
        (tampered / "export" / "decisions.jsonl").write_text("bad\n", encoding="utf-8")
        checksum_result = receiver.receive(tampered, transfer)
        self.assertEqual(checksum_result.status, "quarantined")
        self.assertTrue(checksum_result.quarantine and checksum_result.quarantine.is_dir())

        partial = self._bundle("receiver-partial")
        partial_manifest = DirectoryTransferManifest.from_directory(
            partial, content_id="f" * 64, artifact_type="human-session-bundle"
        )
        (partial / "raw" / "coverage.json").unlink()
        partial_result = receiver.receive(partial, partial_manifest)
        self.assertEqual(partial_result.status, "quarantined")

        collision = self._bundle("receiver-collision")
        (collision / "export" / "decisions.jsonl").write_text("collision\n", encoding="utf-8")
        collision_manifest = DirectoryTransferManifest.from_directory(
            collision, content_id="d" * 64, artifact_type="human-session-bundle"
        )
        collision_result = receiver.receive(collision, collision_manifest)
        self.assertEqual(collision_result.status, "collision")

    def test_receiver_quarantines_manifest_path_traversal(self) -> None:
        source = self.root / "traversal"
        source.mkdir()
        (source / "safe.txt").write_text("safe", encoding="utf-8")
        manifest_path = self.root / "traversal.json"
        manifest_path.write_text(
            canonical({
                "schema": "sts2.evidence/directory-transfer-1",
                "content_id": "1" * 64,
                "artifact_type": "test",
                "files": [{"path": "../escape.txt", "bytes": 4, "sha256": "2" * 64}],
            }) + "\n",
            encoding="utf-8",
        )
        receiver = DirectoryReceiver(ContentAddressedStore(self.root / "path-store"))
        result = receiver.receive(source, manifest_path)
        self.assertEqual(result.status, "quarantined")
        self.assertTrue(result.quarantine and result.quarantine.is_dir())
        self.assertFalse((self.root / "escape.txt").exists())

    def test_typed_receiver_requires_verified_bundle_and_matching_content_id(self) -> None:
        bundle = self._bundle()
        content_id = json.loads(
            (bundle / "session-bundle-manifest.json").read_text(encoding="utf-8")
        )["bundle_content_id"]
        manifest = DirectoryTransferManifest.from_directory(
            bundle, content_id=content_id, artifact_type="human-session-bundle"
        )

        def verify(directory: Path, transfer: DirectoryTransferManifest) -> None:
            result = VersionedHumanSessionBundleVerifier().verify(directory, self.profile)
            value = result.require_value()
            if value.bundle_content_id != transfer.content_id:
                raise ValueError("bundle content ID drift")

        receiver = DirectoryReceiver(
            ContentAddressedStore(self.root / "typed-store"), promotion_verifier=verify
        )
        self.assertEqual(receiver.receive(bundle, manifest).status, "promoted")

        wrong = DirectoryTransferManifest.from_directory(
            bundle, content_id="9" * 64, artifact_type="human-session-bundle"
        )
        rejected = receiver.receive(bundle, wrong)
        self.assertEqual(rejected.status, "quarantined")
        self.assertIn("bundle content ID drift", rejected.findings[0])

    def test_cli_verify_and_transfer_manifest(self) -> None:
        bundle = self._bundle()
        profile_path = self.root / "profile.json"
        profile_path.write_text(canonical(self.profile_value) + "\n", encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["verify-human-bundle", str(bundle), "--profile", str(profile_path)])
        self.assertEqual(code, 0)
        self.assertIn('"status": "pass"', output.getvalue())
        transfer_path = self.root / "transfer.json"
        code = main([
            "transfer-manifest", str(bundle), "--content-id", "2" * 64,
            "--artifact-type", "human-session-bundle", "--output", str(transfer_path),
        ])
        self.assertEqual(code, 0)
        self.assertEqual(DirectoryTransferManifest.read(transfer_path).artifact_type, "human-session-bundle")

    def test_cli_verify_human_bundle_serializes_immutable_text_input_mappings(self) -> None:
        @dataclass(frozen=True)
        class ReportValue:
            human_text_inputs: tuple[Mapping[str, object], ...]

        report_value = ReportValue((MappingProxyType({
            "record_id": "record-1",
            "snapshot": MappingProxyType({"snapshot_id": "snapshot-1"}),
        }),))
        descriptor = VerifierDescriptor(
            "human-session-bundle-v3", "sts2.human-session-bundle-3", 3, ReportValue,
        )
        result = VerificationResult(descriptor, "pass", self.root, report_value)

        class StubRegistry:
            def verify(self, *_args: object, **_kwargs: object) -> VerificationResult[ReportValue]:
                return result

        output = io.StringIO()
        with patch("sts2_platform_evidence.cli.registry", return_value=StubRegistry()):
            with contextlib.redirect_stdout(output):
                code = main(["verify-human-bundle", str(self.root)])

        self.assertEqual(code, 0)
        document = json.loads(output.getvalue())
        self.assertEqual(
            document["value"]["human_text_inputs"],
            [{"record_id": "record-1", "snapshot": {"snapshot_id": "snapshot-1"}}],
        )

    def test_cli_receive_publishes_read_only_store_status_and_receipt(self) -> None:
        bundle = self._bundle("cli-receive")
        content_id = json.loads(
            (bundle / "session-bundle-manifest.json").read_text(encoding="utf-8")
        )["bundle_content_id"]
        transfer = DirectoryTransferManifest.from_directory(
            bundle,
            content_id=content_id,
            artifact_type="human-session-bundle",
        )
        manifest_path = transfer.write(self.root / "cli-transfer.json")
        store_root = self.root / "cli-store"
        receipt_path = self.root / "cli-transfer" / "transfer-receipt.json"

        with contextlib.redirect_stdout(io.StringIO()):
            code = main([
                "receive",
                str(bundle),
                str(manifest_path),
                "--root",
                str(store_root),
                "--verify-type",
                "human-session-bundle",
                "--receipt",
                str(receipt_path),
            ])

        self.assertEqual(code, 0)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        status = json.loads((store_root / "store-status.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "promoted")
        self.assertEqual(status["schema"], "sts2.evidence/store-status-1")
        self.assertEqual(status["last_receipt"], receipt)


if __name__ == "__main__":
    unittest.main()
