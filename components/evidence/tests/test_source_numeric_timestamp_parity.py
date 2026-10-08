from __future__ import annotations
import shutil
import tempfile
import unittest
from pathlib import Path
from sts2_platform_evidence import SourceSessionBundleV2Verifier, SourceSessionBundleV3Verifier
import test_source_session_bundle_v2 as helpers


class SourceNumericTimestampParityTests(unittest.TestCase):
    read = helpers.SourceSessionBundleV2Tests.read
    write = helpers.SourceSessionBundleV2Tests.write
    rows = helpers.SourceSessionBundleV2Tests.rows
    write_rows = helpers.SourceSessionBundleV2Tests.write_rows
    inventory = staticmethod(helpers.SourceSessionBundleV2Tests.inventory)
    reseal = helpers.SourceSessionBundleV2Tests.reseal

    def test_real_typed_bundle_verifier_rejects_wrong_numeric_or_timestamp_values_after_complete_resealing(self) -> None:
        cases = [
            ("source-session-bundle-manifest.json", ("schema_version",), "float_version"),
            ("raw/recording-manifest.json", ("schema_version",), "float_version"),
            ("raw/recording-manifest.json", ("source_schema_version",), "float_version"),
            ("raw/source-close-receipt.json", ("input_count",), True),
            ("raw/source-close-receipt.json", ("epoch_count",), 2.0),
            ("raw/source-close-receipt.json", ("counts", "native-input-witnesses.jsonl"), True),
            ("audit/source-audit.json", ("input_count",), True),
            ("source-session-bundle-manifest.json", ("input_count",), True),
            ("raw/recording-manifest.json", ("created_at",), "not-a-date"),
            ("raw/recording-manifest.json", ("created_at",), "2026-02-30T00:00:00Z"),
            ("raw/recording-manifest.json", ("created_at",), "2026-10-09T00:00:00+14:01"),
            ("raw/recording-manifest.json", ("created_at",), "0001-01-01T00:00:00+14:00"),
            ("native-input-witnesses.jsonl", ("recorded_at",), None),
            ("source-attachment-epochs.jsonl", ("recorded_at",), 1),
            ("source-segments.jsonl", ("recorded_at",), "not-a-date"),
            ("native-input-witnesses.jsonl", ("pre_capture", "captured_at"), False),
        ]
        for version in (2, 3):
            for file, path, bad in cases:
                with self.subTest(version=version, file=file, field=path):
                    with tempfile.TemporaryDirectory() as temporary:
                        self.bundle = Path(temporary) / "bundle"
                        shutil.copytree(Path(__file__).parent / f"fixtures/source_session_v{version}/bundle", self.bundle)
                        bad_value = float(version) if bad == "float_version" else bad
                        if file.endswith(".jsonl"):
                            rows = self.rows(file); value = rows[0]
                        else:
                            value = self.read(file)
                        current = value
                        for part in path[:-1]: current = current[part]
                        current[path[-1]] = bad_value
                        if file.endswith(".jsonl"): self.write_rows(file, rows)
                        else: self.write(file, value)
                        self.reseal(preserve_counts=True)
                        verifier = SourceSessionBundleV2Verifier() if version == 2 else SourceSessionBundleV3Verifier()
                        result = verifier.verify(self.bundle)
                        self.assertTrue(result.failed, result)
                        self.assertIn(result.findings[0].code, {"source_integer_invalid", "source_timestamp_invalid"})

    def test_source2_successor_requires_its_original_boundary_after_complete_resealing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.bundle = Path(temporary) / "bundle"
            shutil.copytree(Path(__file__).parent / "fixtures/source_session_v2/bundle", self.bundle)
            values = [value for value in self.rows("source-boundaries.jsonl") if value["kind"] != "epoch_transition"]
            for offset, value in enumerate(values, 1): value["sequence"] = offset
            self.write_rows("source-boundaries.jsonl", values); self.reseal()
            result = SourceSessionBundleV2Verifier().verify(self.bundle)
            self.assertTrue(result.failed, result); self.assertEqual("source_epoch_boundary_accounting_incomplete", result.findings[0].code)


if __name__ == "__main__":
    unittest.main()
