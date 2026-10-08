from __future__ import annotations
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from sts2_platform_evidence import SourceSessionBundleV2Verifier, SourceSessionBundleV3Verifier, verify_source_session_bundle_v3
from sts2_platform_evidence.cli import main, registry
from sts2_platform_evidence.transfer import DirectoryTransferManifest
import test_source_session_bundle_v2 as source_v2_helpers


class SourceSessionBundleV3Tests(unittest.TestCase):
    # The existing integrity mutation helper is shared; no second bundle writer.
    read = source_v2_helpers.SourceSessionBundleV2Tests.read
    write = source_v2_helpers.SourceSessionBundleV2Tests.write
    rows = source_v2_helpers.SourceSessionBundleV2Tests.rows
    write_rows = source_v2_helpers.SourceSessionBundleV2Tests.write_rows
    inventory = staticmethod(source_v2_helpers.SourceSessionBundleV2Tests.inventory)
    reseal = source_v2_helpers.SourceSessionBundleV2Tests.reseal
    assert_fail = source_v2_helpers.SourceSessionBundleV2Tests.assert_fail

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        shutil.copytree(Path(__file__).parent / "fixtures/source_session_v3/bundle", self.bundle)
        self.verifier = SourceSessionBundleV3Verifier()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_actual_csharp_protocol_worker_packer_golden_exposes_original_order_and_boundaries(self) -> None:
        result = verify_source_session_bundle_v3(self.bundle)
        self.assertTrue(result.passed, result.findings)
        value = result.require_value()
        self.assertEqual("source-session-bundle-v3", result.descriptor.type_id)
        self.assertEqual("1", value.final_input_prefix_ordinal)
        self.assertEqual(2, len(value.epochs))
        self.assertEqual(6, len(value.boundaries))
        self.assertEqual("1", value.inputs[0]["input_prefix_ordinal"])
        self.assertEqual({"status": "native_prefix_frozen", "reason_code": None}, value.inputs[0]["basis_order"])
        self.assertEqual("exact", value.inputs[0]["outcome"]["mapping_status"])
        self.assertEqual("delivered", value.inputs[0]["outcome"]["delivery"])
        self.assertFalse(value.human_origin_verified)
        self.assertTrue(SourceSessionBundleV2Verifier().verify(self.bundle).failed)
        self.assertTrue(registry().verify("source-session-bundle-v3", self.bundle).passed)
        with self.assertRaises(TypeError): value.inputs[0]["input_prefix_ordinal"] = "2"
        for reference in (value.inputs[0]["pre_capture"], value.inputs[0]["catalog"]):
            self.assertEqual((self.bundle / "raw" / reference["payload_ref"]).read_bytes(),
                             (self.bundle / "export" / reference["payload_ref"]).read_bytes())

    def test_missing_prefix_ordinal_is_not_an_old_input_or_implicit_default(self) -> None:
        values = self.rows("native-input-witnesses.jsonl"); values[0].pop("input_prefix_ordinal")
        self.write_rows("native-input-witnesses.jsonl", values); self.reseal(); self.assert_fail("source_row_binding_invalid")

    def test_noncanonical_and_overflow_ordinals_reject_after_hash_repair(self) -> None:
        for ordinal in ("01", "-1", "18446744073709551616"):
            with self.subTest(ordinal=ordinal):
                values = self.rows("native-input-witnesses.jsonl"); values[0]["input_prefix_ordinal"] = ordinal
                self.write_rows("native-input-witnesses.jsonl", values); self.reseal(); self.assert_fail("source_clock_invalid")

    def test_missing_or_unknown_basis_order_members_are_rejected(self) -> None:
        values = self.rows("native-input-witnesses.jsonl"); values[0]["basis_order"].pop("reason_code")
        self.write_rows("native-input-witnesses.jsonl", values); self.reseal(); self.assert_fail("source_input_order_invalid")
        values[0]["basis_order"]["reason_code"] = None; values[0]["basis_order"]["native_commit"] = True
        self.write_rows("native-input-witnesses.jsonl", values); self.reseal(); self.assert_fail("source_input_order_invalid")

    def test_unproven_cannot_keep_an_exact_capture_or_selected_catalogue_member(self) -> None:
        values = self.rows("native-input-witnesses.jsonl")
        values[0]["basis_order"] = {"status": "unproven", "reason_code": "source_prefix_capture_order_unproven"}
        self.write_rows("native-input-witnesses.jsonl", values); self.reseal(); self.assert_fail("source_unproven_input_has_capture")

    def test_original_epoch_final_drain_cannot_borrow_a_different_input_cut(self) -> None:
        receipt = self.read("raw/source-close-receipt.json"); receipt["final_drains"][0]["after_input_ordinal"] = "0"
        self.write("raw/source-close-receipt.json", receipt); self.reseal(); self.assert_fail("source_final_drain_input_fence_mismatch")

    def test_input_cannot_belong_to_an_epoch_started_after_that_original_ordinal(self) -> None:
        values = self.rows("source-attachment-epochs.jsonl"); values[0]["after_input_ordinal"] = "1"
        self.write_rows("source-attachment-epochs.jsonl", values); self.reseal(); self.assert_fail("source_input_epoch_fence_mismatch")

    def test_original_pause_cut_cannot_advance_before_resume(self) -> None:
        values = self.rows("source-boundaries.jsonl")
        pause = next(row for row in values if row["kind"] == "pause"); pause["after_input_ordinal"] = "0"
        self.write_rows("source-boundaries.jsonl", values); self.reseal(); self.assert_fail("source_input_boundary_fence_mismatch")

    def test_closed_final_input_cut_accounts_for_every_issued_ordinal(self) -> None:
        receipt = self.read("raw/source-close-receipt.json"); receipt["final_input_prefix_ordinal"] = "0"
        self.write("raw/source-close-receipt.json", receipt); self.reseal(); self.assert_fail("source_input_ordinal_accounting_incomplete")

    def test_source2_cannot_be_upgraded_by_adding_a_prefix_field(self) -> None:
        old = self.root / "old-v2"
        shutil.copytree(Path(__file__).parent / "fixtures/source_session_v2/bundle", old)
        self.assertTrue(self.verifier.verify(old).failed)

    def test_typed_v3_receive_reuses_existing_content_store_and_transfer_authority(self) -> None:
        verified = self.verifier.verify(self.bundle).require_value()
        manifest = DirectoryTransferManifest.from_directory(self.bundle, content_id=verified.content_id, artifact_type="source-session-bundle-v3")
        path = self.root / "transfer.json"; manifest.write(path)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["receive", str(self.bundle), str(path), "--root", str(self.root / "store"), "--verify-type", "source-session-bundle-v3"])
        self.assertEqual(0, code); self.assertEqual("promoted", json.loads(output.getvalue())["status"])


if __name__ == "__main__":
    unittest.main()
