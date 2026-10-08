from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from sts2_platform_evidence.source_session_bundle import (
    STREAMS, SourceSessionBundleVerifier, _catalog_digest,
)
from sts2_platform_evidence.human_session_bundle_v3 import HumanSessionBundleV3Verifier
from sts2_platform_evidence.store import ContentAddressedStore


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class SourceSessionBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        fixture = Path(__file__).parent / "fixtures/source_session_v1/bundle"
        shutil.copytree(fixture, self.bundle)
        self.verifier = SourceSessionBundleVerifier()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def read(self, relative: str) -> Any:
        return json.loads((self.bundle / relative).read_bytes())

    def write(self, relative: str, value: Any) -> None:
        (self.bundle / relative).write_bytes(encoded(value))

    def rows(self, file: str) -> list[dict[str, Any]]:
        return [json.loads(line) for line in (self.bundle / "raw" / file).read_bytes().splitlines()]

    def write_rows(self, file: str, rows: list[dict[str, Any]]) -> None:
        (self.bundle / "raw" / file).write_bytes(b"".join(encoded(row) for row in rows))

    def seal_mutation(self) -> None:
        """Recompute transport integrity so semantic mutations must be rejected independently."""
        raw = self.bundle / "raw"
        export = self.bundle / "export"
        shutil.rmtree(export)
        export.mkdir()
        for file in STREAMS:
            shutil.copyfile(raw / file, export / file)
        for family in ("public-captures", "public-catalogs"):
            if (raw / family).exists():
                shutil.copytree(raw / family, export / family)
        receipt = self.read("raw/source-close-receipt.json")
        for file in STREAMS:
            data = (raw / file).read_bytes()
            receipt["counts"][file] = len(data.splitlines())
            receipt["stream_sha256"][file] = sha(data)
        self.write("raw/source-close-receipt.json", receipt)
        identity = self.read("content-identity.json")
        identity["raw_file_sha256"] = self.inventory(raw)
        identity["export_file_sha256"] = self.inventory(export)
        identity["audit_sha256"] = sha((self.bundle / "audit/source-audit.json").read_bytes())
        self.write("content-identity.json", identity)
        manifest = self.read("source-session-bundle-manifest.json")
        manifest["content_identity"] = identity
        manifest["bundle_content_id"] = sha((self.bundle / "content-identity.json").read_bytes())
        self.write("source-session-bundle-manifest.json", manifest)
        hashes = self.inventory(self.bundle)
        (self.bundle / "checksums.sha256").write_text(
            "".join(f"{value}  {key}\n" for key, value in sorted(hashes.items()) if key != "checksums.sha256"),
            encoding="utf-8",
        )

    @staticmethod
    def inventory(directory: Path) -> dict[str, str]:
        return {path.relative_to(directory).as_posix(): sha(path.read_bytes())
                for path in sorted(directory.rglob("*")) if path.is_file()}

    def assert_fail(self, code: str | None = None) -> None:
        result = self.verifier.verify(self.bundle)
        self.assertTrue(result.failed)
        if code:
            self.assertEqual(code, result.findings[0].code)

    def test_csharp_producer_fixture_exact_bytes_and_generic_type(self) -> None:
        result = self.verifier.verify(self.bundle)
        self.assertTrue(result.passed, result.findings)
        value = result.require_value()
        self.assertFalse(value.human_origin_verified)
        self.assertEqual(value.manifest["source_kinds"], ("agent_native_ui",))
        self.assertEqual(len(value.observations), 1)
        self.assertEqual(len(value.inputs), 2)
        self.assertEqual(value.inputs[0]["outcome"]["mapping_status"], "exact")
        self.assertEqual(value.inputs[0]["outcome"]["delivery"], "delivered")
        self.assertEqual(value.inputs[1]["outcome"]["delivery"], "unknown")
        observation = value.observations[0]
        capture = observation["capture"]
        catalog = observation["catalog"]
        self.assertEqual((self.bundle / "raw" / capture["payload_ref"]).read_bytes(),
                         (self.bundle / "export" / capture["payload_ref"]).read_bytes())
        actions = json.loads((self.bundle / "raw" / catalog["payload_ref"]).read_bytes())
        self.assertEqual("Focus 中文 🧪", actions[0]["label"])
        self.assertEqual(catalog["structural_digest"], _catalog_digest(actions))
        with self.assertRaises(TypeError):
            value.manifest["human_origin_attested"] = True
        self.assertTrue(HumanSessionBundleV3Verifier().verify(self.bundle).failed)
        receipt = ContentAddressedStore(self.root / "store").put_directory(self.bundle)
        self.assertTrue(self.verifier.verify(receipt.directory).passed)
        self.assertEqual("source-session-bundle-v1", result.descriptor.type_id)

    def test_modified_capture_without_checksum_repair_fails_inventory(self) -> None:
        capture = self.rows("public-observations.jsonl")[0]["capture"]
        (self.bundle / "raw" / capture["payload_ref"]).write_bytes(b'{"fake":"replacement"}')
        self.assert_fail("source_checksum_inventory_mismatch")

    def test_human_promotion_and_machine_origin_assertion_rejected_after_rehash(self) -> None:
        manifest = self.read("source-session-bundle-manifest.json")
        manifest["human_origin_attested"] = True
        self.write("source-session-bundle-manifest.json", manifest)
        self.seal_mutation()
        self.assert_fail("source_human_promotion_forbidden")
        manifest["human_origin_attested"] = False
        self.write("source-session-bundle-manifest.json", manifest)
        segments = self.rows("source-segments.jsonl")
        segments[0]["declaration"]["machine_verifiable"] = True
        self.write_rows("source-segments.jsonl", segments)
        self.seal_mutation()
        self.assert_fail("source_declaration_invalid")

    def test_full_reference_without_catalog_rejected_after_rehash(self) -> None:
        rows = self.rows("public-observations.jsonl")
        rows[0]["catalog"] = None
        self.write_rows("public-observations.jsonl", rows)
        self.seal_mutation()
        self.assert_fail("source_full_reference_incomplete")

    def test_structural_catalog_digest_and_snapshot_join_rejected_after_rehash(self) -> None:
        rows = self.rows("public-observations.jsonl")
        rows[0]["catalog"]["structural_digest"] = "0" * 64
        self.write_rows("public-observations.jsonl", rows)
        self.seal_mutation()
        self.assert_fail("source_catalog_digest_mismatch")

    def test_future_observation_clock_cannot_be_sealed_by_current_summary(self) -> None:
        rows = self.rows("public-observations.jsonl")
        rows[0]["clock"]["publication_index"] = "4"
        self.write_rows("public-observations.jsonl", rows)
        self.seal_mutation()
        self.assert_fail("source_observation_clock_invalid")

    def test_declared_capacity_and_disk_failure_remain_failed_closed(self) -> None:
        self.write("raw/source-accounting-failure.json", {
            "schema": "sts2.annotator/source-accounting-failure-1",
            "code": "source_disk_append_uncertain", "accounting_complete": False,
        })
        self.seal_mutation()
        self.assert_fail("source_accounting_failed")

    def test_changed_export_is_not_accepted_as_raw_equivalent(self) -> None:
        (self.bundle / "export/public-observations.jsonl").write_bytes(b"")
        identity = self.read("content-identity.json")
        identity["export_file_sha256"] = self.inventory(self.bundle / "export")
        self.write("content-identity.json", identity)
        manifest = self.read("source-session-bundle-manifest.json")
        manifest["content_identity"] = identity
        manifest["bundle_content_id"] = sha((self.bundle / "content-identity.json").read_bytes())
        self.write("source-session-bundle-manifest.json", manifest)
        hashes = self.inventory(self.bundle)
        (self.bundle / "checksums.sha256").write_text(
            "".join(f"{value}  {key}\n" for key, value in sorted(hashes.items()) if key != "checksums.sha256"))
        self.assert_fail("source_raw_export_equivalence_invalid")

    def test_duplicate_json_keys_and_unpaired_surrogates_never_normalize(self) -> None:
        action = json.loads(next((self.bundle / "raw/public-catalogs").rglob("*.bin")).read_bytes())[0]
        self.assertNotEqual(_catalog_digest([dict(action, subject_referent_id=None)]),
                            _catalog_digest([dict(action, subject_referent_id="")]))
        with self.assertRaises(ValueError):
            _catalog_digest([dict(action, label="\ud800")])
        manifest = self.read("source-session-bundle-manifest.json")
        data = encoded(manifest)
        (self.bundle / "source-session-bundle-manifest.json").write_bytes(
            data[:-2] + b',"human_origin_attested":false}\n')
        hashes = self.inventory(self.bundle)
        (self.bundle / "checksums.sha256").write_text(
            "".join(f"{value}  {key}\n" for key, value in sorted(hashes.items()) if key != "checksums.sha256"))
        self.assert_fail("source_json_duplicate_key")

    def test_expected_identity_and_symlink_fail_closed(self) -> None:
        self.assertTrue(self.verifier.verify(self.bundle, {"session_id": "different-session"}).failed)
        (self.bundle / "unexpected-link").symlink_to(self.bundle / "content-identity.json")
        self.assert_fail("source_symlink_forbidden")

    def test_selected_action_cannot_be_invented_after_all_hashes_are_repaired(self) -> None:
        rows = self.rows("native-input-witnesses.jsonl")
        rows[0]["outcome"]["selected_action"]["action_id"] = "invented-action"
        self.write_rows("native-input-witnesses.jsonl", rows)
        self.seal_mutation()
        self.assert_fail("source_selected_action_not_in_original_catalog")

    def test_input_mapping_and_original_segment_are_verified_independently(self) -> None:
        rows = self.rows("native-input-witnesses.jsonl")
        rows[0]["outcome"]["match_count"] = 2
        self.write_rows("native-input-witnesses.jsonl", rows)
        self.seal_mutation()
        self.assert_fail("source_exact_input_basis_missing")
        rows[0]["outcome"]["match_count"] = 1
        rows[0]["segment_id"] = "invented-segment"
        self.write_rows("native-input-witnesses.jsonl", rows)
        self.seal_mutation()
        self.assert_fail("source_input_identity_invalid")

    def test_missing_pre_capture_cannot_be_labelled_exact_or_ambiguous(self) -> None:
        rows = self.rows("native-input-witnesses.jsonl")
        rows[0]["pre_capture"] = None
        rows[0]["catalog"] = None
        self.write_rows("native-input-witnesses.jsonl", rows)
        self.seal_mutation()
        self.assert_fail("source_exact_input_basis_missing")
        rows[0]["outcome"].update(mapping_status="ambiguous", match_count=2, selected_action=None)
        self.write_rows("native-input-witnesses.jsonl", rows)
        self.seal_mutation()
        self.assert_fail("source_input_capture_missing")

    def test_unknown_raw_files_are_rejected_even_with_consistent_transport_hashes(self) -> None:
        self.write("raw/unexpected-private-data.json", {"fixture": "not-an-approved-stream"})
        self.seal_mutation()
        self.assert_fail("source_unexpected_raw_file")


if __name__ == "__main__":
    unittest.main()
