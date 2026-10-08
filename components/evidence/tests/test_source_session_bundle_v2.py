from __future__ import annotations
import hashlib
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable
from sts2_platform_evidence import SourceSessionBundleV2Verifier, verify_source_session_bundle_v2
from sts2_platform_evidence.source_session_bundle_v2 import STREAMS
from sts2_platform_evidence.source_session_bundle import SourceSessionBundleVerifier
from sts2_platform_evidence.human_session_bundle_v3 import HumanSessionBundleV3Verifier
from sts2_platform_evidence.store import ContentAddressedStore
from sts2_platform_evidence.cli import main, registry
from sts2_platform_evidence.transfer import DirectoryTransferManifest


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")

def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class SourceSessionBundleV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        shutil.copytree(Path(__file__).parent / "fixtures/source_session_v2/bundle", self.bundle)
        self.verifier = SourceSessionBundleV2Verifier()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def read(self, file: str) -> Any:
        return json.loads((self.bundle / file).read_bytes())

    def write(self, file: str, value: Any) -> None:
        (self.bundle / file).write_bytes(encoded(value))

    def rows(self, file: str) -> list[dict[str, Any]]:
        return [json.loads(value) for value in (self.bundle / "raw" / file).read_bytes().splitlines()]

    def write_rows(self, file: str, values: list[dict[str, Any]]) -> None:
        (self.bundle / "raw" / file).write_bytes(b"".join(encoded(value) for value in values))

    @staticmethod
    def inventory(directory: Path) -> dict[str, str]:
        return {value.relative_to(directory).as_posix(): sha(value.read_bytes()) for value in sorted(directory.rglob("*")) if value.is_file()}

    def reseal(self) -> None:
        raw, export = self.bundle / "raw", self.bundle / "export"
        shutil.rmtree(export); export.mkdir()
        for file in STREAMS:
            shutil.copyfile(raw / file, export / file)
        for family in ("public-captures", "public-catalogs"):
            if (raw / family).exists(): shutil.copytree(raw / family, export / family)
        receipt = self.read("raw/source-close-receipt.json")
        for file in STREAMS:
            data = (raw / file).read_bytes()
            receipt["counts"][file] = len(data.splitlines()); receipt["stream_sha256"][file] = sha(data)
        self.write("raw/source-close-receipt.json", receipt)
        identity = self.read("content-identity.json")
        identity["raw_file_sha256"] = self.inventory(raw); identity["export_file_sha256"] = self.inventory(export)
        identity["audit_sha256"] = sha((self.bundle / "audit/source-audit.json").read_bytes())
        self.write("content-identity.json", identity)
        manifest = self.read("source-session-bundle-manifest.json")
        manifest["content_identity"] = identity; manifest["bundle_content_id"] = sha((self.bundle / "content-identity.json").read_bytes())
        self.write("source-session-bundle-manifest.json", manifest)
        (self.bundle / "checksums.sha256").write_text("".join(f"{value}  {key}\n" for key, value in sorted(self.inventory(self.bundle).items()) if key != "checksums.sha256"), encoding="utf-8")

    def assert_fail(self, code: str | None = None) -> None:
        result = self.verifier.verify(self.bundle)
        self.assertTrue(result.failed, result)
        if code: self.assertEqual(code, result.findings[0].code)

    def test_csharp_worker_packer_golden_original_epoch_actor_and_no_human_promotion(self) -> None:
        result = verify_source_session_bundle_v2(self.bundle)
        self.assertTrue(result.passed, result.findings)
        value = result.require_value()
        self.assertFalse(value.human_origin_verified)
        self.assertEqual(2, len(value.epochs)); self.assertEqual(2, len(value.final_drains)); self.assertEqual(1, len(value.inputs))
        original = value.inputs[0]
        self.assertEqual(value.epochs[0]["epoch_id"], original["epoch_id"])
        self.assertEqual("actor-original", next(row for row in value.segments if row["segment_id"] == original["segment_id"])["declaration"]["actor_id"])
        self.assertEqual("exact", original["outcome"]["mapping_status"])
        self.assertEqual("delivered", original["outcome"]["delivery"])
        self.assertEqual("source-session-bundle-v2", result.descriptor.type_id)
        self.assertEqual("pass", registry().verify("source-session-bundle-v2", self.bundle).status)
        self.assertTrue(SourceSessionBundleVerifier().verify(self.bundle).failed)
        self.assertTrue(HumanSessionBundleV3Verifier().verify(self.bundle).failed)
        stored = ContentAddressedStore(self.root / "store").put_directory(self.bundle)
        self.assertTrue(self.verifier.verify(stored.directory).passed)
        with self.assertRaises(TypeError): value.manifest["human_origin_attested"] = True
        for observation in value.observations:
            if observation["capture"]:
                relative = observation["capture"]["payload_ref"]
                self.assertEqual((self.bundle / "raw" / relative).read_bytes(), (self.bundle / "export" / relative).read_bytes())

    def test_hash_repaired_original_input_actor_or_epoch_rewrites_are_rejected(self) -> None:
        inputs = self.rows("native-input-witnesses.jsonl"); segments = self.rows("source-segments.jsonl")
        inputs[0]["segment_id"] = segments[-1]["segment_id"]
        self.write_rows("native-input-witnesses.jsonl", inputs); self.reseal(); self.assert_fail("source_input_original_segment_mismatch")

    def test_hash_repaired_completion_beyond_original_reserved_seal_is_rejected(self) -> None:
        receipt = self.read("raw/source-close-receipt.json"); drain = receipt["final_drains"][0]
        drain["completed_through"] = str(int(drain["sealed_reserved_through"]) + 100)
        self.write("raw/source-close-receipt.json", receipt); self.reseal(); self.assert_fail("source_final_drain_incomplete")

    def test_hash_repaired_final_position_or_omitted_old_seals_is_rejected(self) -> None:
        receipt = self.read("raw/source-close-receipt.json"); receipt["final_position"]["publication_index"] = "999"
        self.write("raw/source-close-receipt.json", receipt); self.reseal(); self.assert_fail("source_original_close_seals_invalid")

    def test_hash_repaired_false_native_stage_delivery_is_rejected(self) -> None:
        inputs = self.rows("native-input-witnesses.jsonl")
        inputs[0]["outcome"]["stages"] = [{"stage": "dispatch", "delivery": "commit_proved", "evidence": "fake"}]
        self.write_rows("native-input-witnesses.jsonl", inputs); self.reseal(); self.assert_fail("source_input_stage_delivery_invalid")

    def test_hash_repaired_source_definition_change_is_rejected(self) -> None:
        epochs = self.rows("source-attachment-epochs.jsonl"); epochs[0]["context"]["publication_profile_definition_sha256"] = "0" * 64
        self.write_rows("source-attachment-epochs.jsonl", epochs); self.reseal(); self.assert_fail("source_epoch_context_invalid")

    def test_hash_repaired_owner_occurrence_cannot_replace_original_captured_owner(self) -> None:
        observations = self.rows("public-observations.jsonl")
        next(value for value in observations if value["capture"] is not None)["owner_occurrence"] = "invented-owner-occurrence"
        self.write_rows("public-observations.jsonl", observations); self.reseal(); self.assert_fail("source_observation_owner_occurrence_mismatch")

    def test_hash_repaired_selected_action_outside_original_pre_catalog_is_rejected(self) -> None:
        inputs = self.rows("native-input-witnesses.jsonl"); inputs[0]["outcome"]["selected_action"]["action_id"] = "fabricated-action"
        self.write_rows("native-input-witnesses.jsonl", inputs); self.reseal(); self.assert_fail("source_selected_action_not_in_original_catalog")

    def test_hash_repaired_native_row_omission_cannot_use_counts_as_final_drain(self) -> None:
        observations = self.rows("public-observations.jsonl")
        removable = next(index for index, value in enumerate(observations) if value["position"]["publication_index"] != "1" and value["gap_after_index"] is None)
        del observations[removable]
        for index, value in enumerate(observations): value["sequence"] = index + 1
        self.write_rows("public-observations.jsonl", observations)
        manifest = self.read("source-session-bundle-manifest.json"); manifest["observation_count"] = len(observations); self.write("source-session-bundle-manifest.json", manifest)
        audit = self.read("audit/source-audit.json"); audit["observation_count"] = len(observations); self.write("audit/source-audit.json", audit)
        self.reseal(); self.assert_fail("source_original_durable_prefix_gap")

    def test_extra_unknown_raw_file_and_failed_accounting_block_clean_generic_bundle(self) -> None:
        (self.bundle / "raw/unowned.txt").write_bytes(b"unowned fixture metadata")
        self.reseal(); self.assert_fail("source_unexpected_raw_file")

    def test_obsolete_source_schema_cannot_be_loaded_as_v2(self) -> None:
        manifest = self.read("source-session-bundle-manifest.json"); manifest["schema_version"] = 1
        self.write("source-session-bundle-manifest.json", manifest); self.reseal(); self.assert_fail("source_bundle_identity_invalid")

    def receive(self, *, content_id: str | None = None, artifact_type: str = "source-session-bundle-v2") -> tuple[int, dict[str, Any]]:
        transfer = DirectoryTransferManifest.from_directory(self.bundle,
            content_id=content_id or self.read("source-session-bundle-manifest.json")["bundle_content_id"], artifact_type=artifact_type)
        path = self.root / "transfer.json"; transfer.write(path)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["receive", str(self.bundle), str(path), "--root", str(self.root / "receiver"),
                "--verify-type", "source-session-bundle-v2"])
        return status, json.loads(output.getvalue())

    def test_typed_receive_promotes_verified_source_v2(self) -> None:
        status, receipt = self.receive()
        self.assertEqual(0, status, receipt); self.assertEqual("promoted", receipt["status"])

    def test_typed_receive_rejects_hash_consistent_semantic_corruption(self) -> None:
        inputs = self.rows("native-input-witnesses.jsonl")
        inputs[0]["outcome"]["delivery"] = "commit_proved"
        self.write_rows("native-input-witnesses.jsonl", inputs); self.reseal()
        status, receipt = self.receive()
        self.assertEqual(1, status, receipt); self.assertEqual("quarantined", receipt["status"])

    def test_typed_receive_rejects_wrong_artifact_type(self) -> None:
        status, receipt = self.receive(artifact_type="directory")
        self.assertEqual(1, status, receipt); self.assertEqual("quarantined", receipt["status"])

    def test_typed_receive_rejects_wrong_bundle_content_identity(self) -> None:
        status, receipt = self.receive(content_id="0" * 64)
        self.assertEqual(1, status, receipt); self.assertEqual("quarantined", receipt["status"])
