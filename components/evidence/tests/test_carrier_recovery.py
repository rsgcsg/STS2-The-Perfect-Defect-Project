from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sts2_platform_evidence import verify_human_session_bundle
from sts2_platform_evidence import carrier_recovery as recovery
from tests import test_evidence as v1
from tests import test_human_session_bundle_v2 as v2
from tests import test_human_session_bundle_v3 as v3


class CarrierRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.destination = self.root / "destination"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _bundle(self, version: int = 1) -> Path:
        if version == 1:
            helper = v1.EvidenceTests()
            helper.root = self.root
            helper.profile_value = helper._profile_value()
            helper.profile = v1.load_collection_profile_from_value(helper.profile_value)
            return helper._bundle("original")
        if version == 2:
            helper = v2.HumanSessionBundleV2Tests()
            helper.root = self.root
            return helper._bundle("original")
        helper = v3.HumanSessionBundleV3Tests()
        helper.root = self.root
        return helper._bundle()

    def _bytes(self, directory: Path) -> dict[str, bytes]:
        return {path.relative_to(directory).as_posix(): path.read_bytes()
                for path in directory.rglob("*") if path.is_file()}

    def _recover(self, source: Path) -> recovery.CarrierRecoveryResult:
        return recovery.recover_human_bundle_carrier(source, self.destination)

    def _symlink(self, target: Path, link: Path, *, directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"filesystem cannot create this symlink: {error}")

    def test_metadata_recovery_preserves_v1_bytes_identity_and_attestation(self) -> None:
        source = self._bundle()
        before_manifest = (source / "session-bundle-manifest.json").read_bytes()
        original_id = json.loads(before_manifest)["bundle_content_id"]
        (source / ".DS_Store").write_bytes(b"synthetic Finder metadata")
        before = self._bytes(source)
        self.assertEqual(verify_human_session_bundle(source).findings[0].code,
                         "checksum_inventory_mismatch")
        result = self._recover(source)
        self.assertEqual(result.transfer.status, "promoted")
        directory = result.transfer.directory
        self.assertIsNotNone(directory)
        verified = verify_human_session_bundle(directory)
        self.assertTrue(verified.passed, verified.findings)
        self.assertEqual(verified.require_value().bundle_content_id, original_id)
        self.assertEqual(self._bytes(source), before)
        self.assertEqual((directory / "session-bundle-manifest.json").read_bytes(), before_manifest)
        self.assertEqual(self._bytes(directory), {k: v for k, v in before.items() if k != ".DS_Store"})
        receipt = result.receipt
        self.assertEqual(receipt["excluded_metadata"], [{
            "path": ".DS_Store", "bytes": len(before[".DS_Store"]),
            "sha256": hashlib.sha256(before[".DS_Store"]).hexdigest(),
        }])
        self.assertNotEqual(receipt["original_carrier_manifest_sha256"],
                            receipt["recovered_carrier_manifest_sha256"])
        self.assertFalse(receipt["claims"]["new_human_attestation"])
        self.assertFalse(receipt["claims"]["research_admission"])
        self.assertFalse(result.receipt_path.is_relative_to(directory))
        self.assertEqual(result.receipt_path.read_bytes(), result.receipt_bytes)
        self.assertEqual(hashlib.sha256(result.receipt_bytes).hexdigest(), result.receipt_sha256)

    def test_clean_carrier_is_not_reinterpreted_and_recovery_is_idempotent(self) -> None:
        source = self._bundle()
        first = self._recover(source)
        second = self._recover(source)
        self.assertEqual(first.receipt["excluded_metadata"], [])
        self.assertEqual(first.receipt["original_carrier_manifest_sha256"],
                         first.receipt["recovered_carrier_manifest_sha256"])
        self.assertEqual(second.transfer.status, "reused")
        self.assertEqual(first.receipt_sha256, second.receipt_sha256)
        self.assertEqual(first.receipt_bytes, second.receipt_bytes)

    def test_current_versioned_verifiers_still_own_v2_and_v3(self) -> None:
        for version in (2, 3):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as name:
                self.root = Path(name)
                self.destination = self.root / "destination"
                source = self._bundle(version)
                (source / "raw" / ".DS_Store").write_bytes(b"metadata")
                before = self._bytes(source)
                result = self._recover(source)
                self.assertTrue(verify_human_session_bundle(result.transfer.directory).passed)
                self.assertEqual(result.receipt["bundle_schema"],
                                 f"sts2.human-annotator/session-bundle-{version}")
                self.assertEqual(self._bytes(source), before)
                self.assertEqual(result.receipt["excluded_metadata"][0]["path"], "raw/.DS_Store")

    def test_declared_metadata_file_is_copied_and_never_excluded(self) -> None:
        source = self._bundle()
        (source / ".DS_Store").write_bytes(b"explicitly inventoried bytes")
        helper = v1.EvidenceTests()
        helper._checksums(source)
        self.assertTrue(verify_human_session_bundle(source).passed)
        result = self._recover(source)
        self.assertEqual(result.receipt["excluded_metadata"], [])
        self.assertEqual((result.transfer.directory / ".DS_Store").read_bytes(),
                         (source / ".DS_Store").read_bytes())

    def test_unknown_extra_file_cannot_be_silently_filtered(self) -> None:
        source = self._bundle()
        (source / "unlisted-evidence.json").write_bytes(b"{}")
        before = self._bytes(source)
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "unknown_extra_file"):
            self._recover(source)
        self.assertEqual(self._bytes(source), before)
        self.assertFalse((self.destination / "objects").exists())

    def test_corrupt_or_missing_listed_bytes_do_not_gain_a_pass(self) -> None:
        source = self._bundle()
        target = source / "export" / "decisions.jsonl"
        target.write_bytes(target.read_bytes() + b"tamper")
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "listed_file_checksum_mismatch"):
            self._recover(source)
        target.unlink()
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "listed_file_missing"):
            self._recover(source)

    def test_duplicate_and_traversing_checksum_paths_fail_closed(self) -> None:
        source = self._bundle()
        checksums = source / "checksums.sha256"
        original = checksums.read_bytes()
        first = original.splitlines()[0]
        cases = [original + first + b"\n",
                 original + b"a" * 64 + b"  ../outside\n",
                 original + b"a" * 64 + b"  raw/../../outside\n",
                 original + b"a" * 64 + b"  /absolute\n"]
        for value in cases:
            with self.subTest(checksum=value[-50:]):
                checksums.write_bytes(value)
                with self.assertRaises(ValueError):
                    self._recover(source)
        self.assertFalse((self.destination / "objects").exists())

    def test_checksum_case_alias_is_rejected_before_copy(self) -> None:
        source = self._bundle()
        checksums = source / "checksums.sha256"
        value = checksums.read_text()
        line = next(row for row in value.splitlines() if row.endswith("audit/audit-report.json"))
        checksums.write_text(value + line.replace("audit/", "AUDIT/") + "\n")
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "duplicate_path_alias"):
            self._recover(source)

    def test_unicode_checksum_aliases_are_rejected(self) -> None:
        source = self._bundle()
        checksums = source / "checksums.sha256"
        checksums.write_text(checksums.read_text()
                             + "a" * 64 + "  raw/e\u0301.json\n"
                             + "a" * 64 + "  raw/\u00e9.json\n")
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "duplicate_path_alias"):
            self._recover(source)

    def test_hard_link_aliases_are_rejected(self) -> None:
        source = self._bundle()
        first = source / "unlisted-one"
        first.write_bytes(b"value")
        second = source / "unlisted-two"
        try:
            os.link(first, second)
        except OSError as error:
            self.skipTest(f"filesystem cannot create a hard link: {error}")
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "source_hard_link"):
            self._recover(source)

    def test_external_hard_link_alias_is_rejected(self) -> None:
        source = self._bundle()
        target = source / "export" / "decisions.jsonl"
        alias = self.root / "external-alias"
        try:
            os.link(target, alias)
        except OSError as error:
            self.skipTest(f"filesystem cannot create a hard link: {error}")
        self.assertEqual(target.stat().st_nlink, 2)
        before = self._bytes(source)
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "source_hard_link"):
            self._recover(source)
        self.assertEqual(self._bytes(source), before)
        self.assertFalse((self.destination / "objects").exists())

    def test_source_root_file_and_directory_links_are_rejected(self) -> None:
        source = self._bundle()
        root_link = self.root / "source-link"
        self._symlink(source, root_link, directory=True)
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "source_or_destination_link"):
            self._recover(root_link)
        external = self.root / "outside"
        external.write_bytes(b"secret synthetic data")
        link = source / ".DS_Store"
        self._symlink(external, link)
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "source_link"):
            self._recover(source)
        link.unlink()
        self._symlink(self.root, source / "linked-directory", directory=True)
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "source_link"):
            self._recover(source)

    def test_destination_links_and_inside_source_are_rejected(self) -> None:
        source = self._bundle()
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "destination_inside_source"):
            recovery.recover_human_bundle_carrier(source, source / "new-store")
        self.destination.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        self._symlink(outside, self.destination / "objects", directory=True)
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "destination_link_or_not_directory"):
            self._recover(source)
        self.assertEqual(list(outside.iterdir()), [])

    def test_source_change_during_copy_rejects_promotion(self) -> None:
        source = self._bundle()
        metadata = source / ".DS_Store"
        metadata.write_bytes(b"before")
        original_copy = recovery._copy_file

        def changed(*args: object) -> None:
            original_copy(*args)
            metadata.write_bytes(b"after")

        with patch.object(recovery, "_copy_file", side_effect=changed):
            with self.assertRaisesRegex(recovery.CarrierRecoveryError, "source_changed"):
                self._recover(source)
        self.assertFalse((self.destination / "objects").exists())

    def test_invalid_attestation_cannot_be_recovered_into_human_origin(self) -> None:
        source = self._bundle()
        manifest_path = source / "session-bundle-manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["human_origin_attestation"]["attested"] = False
        manifest_path.write_text(v1.canonical(manifest) + "\n")
        v1.EvidenceTests()._checksums(source)
        before = self._bytes(source)
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "bundle_verification_failed"):
            self._recover(source)
        self.assertEqual(self._bytes(source), before)
        self.assertFalse((self.destination / "objects").exists())

    def test_destination_collision_is_not_overwritten(self) -> None:
        source = self._bundle()
        content_id = json.loads((source / "session-bundle-manifest.json").read_bytes())["bundle_content_id"]
        target = self.destination / "objects" / content_id
        target.mkdir(parents=True)
        marker = target / "original.txt"
        marker.write_bytes(b"existing object")
        with self.assertRaisesRegex(recovery.CarrierRecoveryError, "carrier_promotion_failed"):
            self._recover(source)
        self.assertEqual(marker.read_bytes(), b"existing object")

    def test_empty_target_created_at_receiver_promotion_is_not_replaced(self) -> None:
        source = self._bundle()
        (source / ".DS_Store").write_bytes(b"metadata")
        content_id = json.loads((source / "session-bundle-manifest.json").read_bytes())["bundle_content_id"]
        target = self.destination / "objects" / content_id
        original_verify = recovery.DirectoryReceiver._verify_artifact
        created = []

        def create_existing(receiver: object, directory: Path, manifest: object) -> None:
            original_verify(receiver, directory, manifest)
            if not created:
                target.mkdir()
                created.append(target.stat().st_ino)

        before = self._bytes(source)
        with patch.object(recovery.DirectoryReceiver, "_verify_artifact", create_existing):
            with self.assertRaisesRegex(recovery.CarrierRecoveryError, "carrier_promotion_failed"):
                self._recover(source)
        self.assertEqual(target.stat().st_ino, created[0])
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(self._bytes(source), before)

    def test_receipt_failure_preserves_promoted_carrier_without_success(self) -> None:
        source = self._bundle()
        content_id = json.loads((source / "session-bundle-manifest.json").read_bytes())["bundle_content_id"]
        with patch.object(recovery, "_write_receipt", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(recovery.CarrierRecoveryError, "receipt_publication_failed"):
                self._recover(source)
        self.assertTrue(verify_human_session_bundle(self.destination / "objects" / content_id).passed)
        self.assertEqual(self._recover(source).transfer.status, "reused")

    def test_limits_reject_before_unbounded_copy(self) -> None:
        source = self._bundle()
        with patch.object(recovery, "MAX_BYTES", 1):
            with self.assertRaisesRegex(recovery.CarrierRecoveryError, "carrier_limit"):
                self._recover(source)
        with patch.object(recovery, "MAX_CHECKSUM_BYTES", 1):
            with self.assertRaisesRegex(recovery.CarrierRecoveryError, "carrier_limit"):
                self._recover(source)


if __name__ == "__main__":
    unittest.main()
