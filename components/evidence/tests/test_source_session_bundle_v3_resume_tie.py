from __future__ import annotations
import shutil
import tempfile
import unittest
from pathlib import Path

from sts2_platform_evidence import SourceSessionBundleV3Verifier
import test_source_session_bundle_v2 as helpers


class Source3ResumeTieTests(unittest.TestCase):
    read = helpers.SourceSessionBundleV2Tests.read
    write = helpers.SourceSessionBundleV2Tests.write
    rows = helpers.SourceSessionBundleV2Tests.rows
    write_rows = helpers.SourceSessionBundleV2Tests.write_rows
    inventory = staticmethod(helpers.SourceSessionBundleV2Tests.inventory)
    reseal = helpers.SourceSessionBundleV2Tests.reseal
    assert_fail = helpers.SourceSessionBundleV2Tests.assert_fail

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.bundle = Path(self.temporary.name) / "bundle"
        shutil.copytree(Path(__file__).parent / "fixtures/source_session_v3_resume_tie/bundle", self.bundle)
        self.verifier = SourceSessionBundleV3Verifier()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_actual_original_physical_worker_prefix_after_resume_passes_at_unchanged_pause_end_watermark(self) -> None:
        result = self.verifier.verify(self.bundle); self.assertTrue(result.passed, result.findings)
        value = result.require_value(); self.assertEqual(["2", "1"], [row["input_prefix_ordinal"] for row in value.inputs])
        resume = next(row for row in value.boundaries if row["kind"] == "resume")
        self.assertEqual(value.inputs[0]["pre_position"], resume["position"])
        self.assertEqual("3", resume["position"]["publication_index"])
        self.assertEqual("1", resume["after_input_ordinal"])
        self.assertEqual([("1", "3")], [(row["after_index"], row["through_index"]) for row in resume["paused_intervals"]])
        self.assertEqual(["delivered", "delivered"], [row["outcome"]["delivery"] for row in value.inputs])
        self.assertEqual(["exact", "exact"], [row["outcome"]["mapping_status"] for row in value.inputs])
        segments = {row["segment_id"]: row for row in value.segments}
        self.assertEqual(["actor-after-resume", "actor-original"], [segments[row["segment_id"]]["declaration"]["actor_id"] for row in value.inputs])

    def test_input_strictly_inside_original_paused_native_range_remains_invalid_after_hash_repair(self) -> None:
        values = self.rows("native-input-witnesses.jsonl"); values[0]["pre_position"]["publication_index"] = "2"
        self.write_rows("native-input-witnesses.jsonl", values); self.reseal(); self.assert_fail("source_input_original_binding_invalid")

    def test_input_ordinal_at_original_pause_cut_cannot_borrow_resume_endpoint(self) -> None:
        values = self.rows("native-input-witnesses.jsonl"); values[0]["input_prefix_ordinal"] = "1"
        self.write_rows("native-input-witnesses.jsonl", values); self.reseal(); self.assert_fail("source_input_original_binding_invalid")

    def test_input_ordinal_before_original_pause_cut_cannot_borrow_resume_endpoint(self) -> None:
        values = self.rows("native-input-witnesses.jsonl"); values[0]["input_prefix_ordinal"] = "0"
        self.write_rows("native-input-witnesses.jsonl", values); self.reseal(); self.assert_fail("source_input_original_binding_invalid")

    def test_coordinated_resume_and_interval_cut_rewrite_cannot_move_original_before_pause_input(self) -> None:
        values = self.rows("source-boundaries.jsonl"); resume = next(row for row in values if row["kind"] == "resume")
        resume["after_input_ordinal"] = "0"
        resume["paused_intervals"][0]["after_input_ordinal"] = "0"
        resume["paused_intervals"][0]["through_input_ordinal"] = "0"
        self.write_rows("source-boundaries.jsonl", values); self.reseal(); self.assert_fail("source_input_boundary_fence_mismatch")

    def test_resume_endpoint_cannot_relabel_new_original_input_as_the_previous_actor(self) -> None:
        values = self.rows("native-input-witnesses.jsonl"); values[0]["segment_id"] = values[1]["segment_id"]
        self.write_rows("native-input-witnesses.jsonl", values); self.reseal(); self.assert_fail("source_input_original_segment_mismatch")

    def test_original_publication_inside_paused_range_keeps_position_semantics(self) -> None:
        values = self.rows("public-observations.jsonl"); values[0]["position"]["publication_index"] = "2"
        self.write_rows("public-observations.jsonl", values); self.reseal(); self.assert_fail("source_observation_original_position_invalid")


if __name__ == "__main__":
    unittest.main()
