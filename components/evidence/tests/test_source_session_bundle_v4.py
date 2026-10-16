from __future__ import annotations
import contextlib
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from sts2_platform_evidence import SourceSessionBundleV3Verifier, SourceSessionBundleV4Verifier
from sts2_platform_evidence.cli import main, registry
from sts2_platform_evidence.transfer import DirectoryTransferManifest
from sts2_platform_evidence.source_session_bundle_v2 import STREAMS
import test_source_session_bundle_v2 as helpers


class SourceSessionBundleV4Tests(unittest.TestCase):
    read = helpers.SourceSessionBundleV2Tests.read
    write = helpers.SourceSessionBundleV2Tests.write
    rows = helpers.SourceSessionBundleV2Tests.rows
    write_rows = helpers.SourceSessionBundleV2Tests.write_rows
    inventory = staticmethod(helpers.SourceSessionBundleV2Tests.inventory)
    assert_fail = helpers.SourceSessionBundleV2Tests.assert_fail

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        shutil.copytree(Path(__file__).parent / "fixtures/source_session_v4/bundle", self.bundle)
        self.verifier = SourceSessionBundleV4Verifier()

    def tearDown(self) -> None: self.temporary.cleanup()

    def reseal(self) -> None:
        # Same independent integrity mutation helper, extended only for Source4's new family.
        helpers.SourceSessionBundleV2Tests.reseal(self)
        raw, export = self.bundle / "raw", self.bundle / "export"
        if (raw / "public-bases").exists(): shutil.copytree(raw / "public-bases", export / "public-bases")
        identity = self.read("content-identity.json"); identity["export_file_sha256"] = self.inventory(export)
        self.write("content-identity.json", identity)
        manifest = self.read("source-session-bundle-manifest.json")
        manifest["content_identity"] = identity; manifest["bundle_content_id"] = helpers.sha((self.bundle / "content-identity.json").read_bytes())
        self.write("source-session-bundle-manifest.json", manifest)
        (self.bundle / "checksums.sha256").write_text("".join(f"{digest}  {key}\n" for key, digest in sorted(self.inventory(self.bundle).items()) if key != "checksums.sha256"))

    def mutate_basis(self, mutation) -> None:
        reference = self.rows("native-input-witnesses.jsonl")[0]["captured_parts"]["public_basis"]
        old_path = reference["payload_ref"]; node = self.read("raw/" + old_path); mutation(node)
        data = helpers.encoded(node); digest = helpers.sha(data); path = f"public-bases/sha256/{digest[:2]}/{digest}.bin"
        output = self.bundle / "raw" / path; output.parent.mkdir(parents=True, exist_ok=True); output.write_bytes(data)
        def replace(value):
            if isinstance(value, dict):
                if value.get("payload_ref") == old_path:
                    value.update(sha256=digest, payload_ref=path, byte_count=len(data))
                for child in value.values(): replace(child)
            elif isinstance(value, list):
                for child in value: replace(child)
        for file in ("native-input-witnesses.jsonl", "public-observations.jsonl"):
            rows = self.rows(file); replace(rows); self.write_rows(file, rows)
        (self.bundle / "raw" / old_path).unlink(); self.reseal()

    def test_actual_core_store_packer_golden_preserves_independent_semantic_snapshot_and_original_native_facts(self) -> None:
        result = self.verifier.verify(self.bundle); self.assertTrue(result.passed, result.findings)
        value = result.require_value(); original = value.inputs[0]
        self.assertNotEqual(original["pre_capture"]["snapshot_id"], original["captured_parts"]["public_basis"]["snapshot_id"])
        self.assertEqual("original-card", original["outcome"]["native_facts"]["subject_ref"])
        self.assertEqual("game_action_accepted", original["outcome"]["native_facts"]["carriers"][1]["kind"])
        self.assertFalse(value.human_origin_verified); self.assertTrue(SourceSessionBundleV3Verifier().verify(self.bundle).failed)
        self.assertTrue(registry().verify("source-session-bundle-v4", self.bundle).passed)

    def test_repaired_parent_read_scope_and_inventory_cannot_substitute_other_originals(self) -> None:
        self.mutate_basis(lambda value: value["reads"][0]["response"].update(observed_snapshot_id="different"))
        self.assert_fail("source_public_basis_invalid")

    def test_omitted_advertised_instance_stays_invalid_after_all_outer_hashes_repaired(self) -> None:
        self.mutate_basis(lambda value: value["reads"].clear()); self.assert_fail("source_public_basis_invalid")

    def test_missing_native_still_keeps_ordinary_neutral_original(self) -> None:
        rows = self.rows("native-input-witnesses.jsonl"); row = rows[0]
        row["pre_capture"] = row["catalog"] = None
        row["outcome"].update(mapping_status="capture_missing", match_count=0, selected_action=None)
        self.write_rows("native-input-witnesses.jsonl", rows); self.reseal()
        self.assertTrue(self.verifier.verify(self.bundle).passed)

    def test_unproven_prefix_cannot_keep_neutral_original(self) -> None:
        rows = self.rows("native-input-witnesses.jsonl"); rows[0]["basis_order"] = {
            "status": "unproven", "reason_code": "source_prefix_capture_order_unproven"}
        self.write_rows("native-input-witnesses.jsonl", rows); self.reseal(); self.assert_fail("source_public_basis_binding_invalid")

    def test_closed_native_carrier_family_rejects_invented_commit_fact(self) -> None:
        rows = self.rows("native-input-witnesses.jsonl"); rows[0]["outcome"]["native_facts"]["carriers"][0]["kind"] = "native_commit"
        self.write_rows("native-input-witnesses.jsonl", rows); self.reseal(); self.assert_fail("source_native_facts_invalid")

    def test_typed_receive_uses_original_evidence_content_id(self) -> None:
        verified = self.verifier.verify(self.bundle).require_value()
        manifest = DirectoryTransferManifest.from_directory(self.bundle, content_id=verified.content_id, artifact_type="source-session-bundle-v4")
        path = self.root / "transfer.json"; manifest.write(path)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["receive", str(self.bundle), str(path), "--root", str(self.root / "store"), "--verify-type", "source-session-bundle-v4"])
        self.assertEqual(0, code); self.assertEqual("promoted", json.loads(output.getvalue())["status"])

    def test_cancel_stage_is_typed_context_publication_without_input_or_commit(self) -> None:
        rows = self.rows("public-observations.jsonl")
        rows[0]["source_seam"] = "native_input_callback"; rows[0]["phase"] = "card_play_canceled"
        rows[0]["captured_parts"]["native_stage"] = {"kind": "card_play_canceled", "owner_ref": "original-play",
            "subject_ref": "original-card", "arguments": {"hand": "original-hand"},
            "evidence": "native_finished_false", "no_submission": None}
        self.write_rows("public-observations.jsonl", rows); self.reseal()
        result = self.verifier.verify(self.bundle); self.assertTrue(result.passed, result.findings)
        for invalid in (False, 1, "true"):
            rows[0]["captured_parts"]["native_stage"]["no_submission"] = invalid
            self.write_rows("public-observations.jsonl", rows); self.reseal(); self.assert_fail()

    def test_stage_cannot_be_backfilled_into_input_or_wrong_callback(self) -> None:
        stage = {"kind": "card_play_canceled", "owner_ref": "original-play", "subject_ref": "original-card",
            "arguments": {"hand": "original-hand"}, "evidence": "native_finished_false", "no_submission": True}
        rows = self.rows("native-input-witnesses.jsonl"); rows[0]["captured_parts"]["native_stage"] = stage
        self.write_rows("native-input-witnesses.jsonl", rows); self.reseal(); self.assert_fail()


if __name__ == "__main__": unittest.main()
