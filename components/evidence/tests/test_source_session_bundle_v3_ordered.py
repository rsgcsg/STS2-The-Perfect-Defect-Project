from __future__ import annotations
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from sts2_platform_evidence import SourceSessionBundleV3Verifier
import test_source_session_bundle_v2 as helpers


class OrderedSource3ProducerParityTests(unittest.TestCase):
    read = helpers.SourceSessionBundleV2Tests.read
    write = helpers.SourceSessionBundleV2Tests.write
    rows = helpers.SourceSessionBundleV2Tests.rows
    write_rows = helpers.SourceSessionBundleV2Tests.write_rows
    inventory = staticmethod(helpers.SourceSessionBundleV2Tests.inventory)
    reseal = helpers.SourceSessionBundleV2Tests.reseal
    assert_fail = helpers.SourceSessionBundleV2Tests.assert_fail

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        shutil.copytree(Path(__file__).parent / "fixtures/source_session_v3_ordered/bundle", self.bundle)
        self.verifier = SourceSessionBundleV3Verifier()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_actual_csharp_physical_producer_prefix_order_survives_reverse_terminal_and_actor_epoch_handoff(self) -> None:
        result = self.verifier.verify(self.bundle)
        self.assertTrue(result.passed, result.findings)
        value = result.require_value()
        self.assertEqual(["2", "1"], [row["input_prefix_ordinal"] for row in value.inputs])
        self.assertEqual([1, 2], [row["sequence"] for row in value.inputs])
        ordered = sorted(value.inputs, key=lambda row: int(row["input_prefix_ordinal"]))
        self.assertEqual(["1", "1"], [row["pre_position"]["publication_index"] for row in ordered])
        segments = {row["segment_id"]: row for row in value.segments}
        self.assertEqual(["actor-original", "actor-second"], [segments[row["segment_id"]]["declaration"]["actor_id"] for row in ordered])
        self.assertEqual("2", value.final_input_prefix_ordinal)
        self.assertEqual("1", value.boundaries[0]["after_input_ordinal"])
        self.assertEqual("1", value.boundaries[1]["after_input_ordinal"])
        self.assertEqual("2", value.epochs[1]["after_input_ordinal"])
        frames = [json.loads((self.bundle / "raw" / row["pre_capture"]["payload_ref"]).read_bytes()) for row in ordered]
        self.assertLess(frames[0]["revision"], frames[1]["revision"])
        self.assertEqual(["input-a", "input-b"], [row["interaction"]["kind"] for row in frames])

    def test_same_publication_cut_cannot_relabel_original_input_as_the_next_declared_actor(self) -> None:
        values = self.rows("native-input-witnesses.jsonl")
        self.assertEqual(values[0]["pre_position"], values[1]["pre_position"])
        values[1]["segment_id"] = values[0]["segment_id"]
        self.write_rows("native-input-witnesses.jsonl", values); self.reseal(); self.assert_fail("source_input_actor_fence_mismatch")

    def test_duplicate_or_missing_native_prefix_ordinal_rejects_even_when_durable_sequences_and_hashes_are_sound(self) -> None:
        values = self.rows("native-input-witnesses.jsonl"); values[1]["input_prefix_ordinal"] = "2"
        self.write_rows("native-input-witnesses.jsonl", values); self.reseal(); self.assert_fail("source_input_ordinal_not_contiguous")
        values[1]["input_prefix_ordinal"] = "1"; values[0]["input_prefix_ordinal"] = "3"
        self.write_rows("native-input-witnesses.jsonl", values); self.reseal(); self.assert_fail("source_input_ordinal_not_contiguous")

    def test_boundary_only_pause_resume_ties_still_require_the_original_input_fence(self) -> None:
        values = self.rows("source-boundaries.jsonl"); values[1]["after_input_ordinal"] = "2"
        self.write_rows("source-boundaries.jsonl", values); self.reseal(); self.assert_fail("source_input_admitted_while_paused")

    def test_successor_epoch_cannot_rewrite_the_predecessor_input_cut(self) -> None:
        values = self.rows("source-attachment-epochs.jsonl"); values[1]["after_input_ordinal"] = "1"
        self.write_rows("source-attachment-epochs.jsonl", values); self.reseal(); self.assert_fail("source_epoch_predecessor_input_fence_mismatch")

    def test_coordinated_actor_cut_and_input_actor_rewrite_cannot_move_source_change_outside_pause(self) -> None:
        segments = self.rows("source-segments.jsonl"); values = self.rows("native-input-witnesses.jsonl")
        segments[1]["after_input_ordinal"] = "2"; values[0]["segment_id"] = segments[0]["segment_id"]
        self.write_rows("source-segments.jsonl", segments); self.write_rows("native-input-witnesses.jsonl", values)
        self.reseal(); self.assert_fail("source_actor_pause_input_fence_mismatch")

    def test_resume_cannot_silently_keep_the_pre_change_actor(self) -> None:
        segments = self.rows("source-segments.jsonl"); values = self.rows("source-boundaries.jsonl")
        values[1]["segment_id"] = segments[0]["segment_id"]
        self.write_rows("source-boundaries.jsonl", values); self.reseal(); self.assert_fail("source_boundary_actor_changed_outside_pause")

    def test_multiple_declarations_at_one_original_pause_cut_remain_valid_without_moving_pending_inputs(self) -> None:
        segments = self.rows("source-segments.jsonl")
        intermediate = copy.deepcopy(segments[1]); intermediate["segment_id"] = "source-segment-intermediate"
        intermediate["declaration"]["actor_id"] = "actor-intermediate"
        intermediate["declaration"]["declaration_id"] = "declaration-intermediate"
        segments[1]["previous_segment_id"] = intermediate["segment_id"]
        segments.insert(1, intermediate)
        for offset, segment in enumerate(segments, 1): segment["sequence"] = offset
        self.write_rows("source-segments.jsonl", segments); self.reseal()
        result = self.verifier.verify(self.bundle); self.assertTrue(result.passed, result.findings)
        self.assertEqual(["2", "1"], [value["input_prefix_ordinal"] for value in result.require_value().inputs])

    def test_successor_epoch_requires_its_original_immutable_boundary_even_after_count_and_hash_repair(self) -> None:
        values = [row for row in self.rows("source-boundaries.jsonl") if row["kind"] != "epoch_transition"]
        for offset, row in enumerate(values, 1): row["sequence"] = offset
        self.write_rows("source-boundaries.jsonl", values); self.reseal(); self.assert_fail("source_epoch_boundary_accounting_incomplete")

    def test_native_order_guarantee_does_not_require_terminal_disk_rows_to_be_prefix_sorted(self) -> None:
        values = list(reversed(self.rows("native-input-witnesses.jsonl")))
        for offset, row in enumerate(values, 1): row["sequence"] = offset
        self.write_rows("native-input-witnesses.jsonl", values); self.reseal()
        self.assertTrue(self.verifier.verify(self.bundle).passed)


if __name__ == "__main__":
    unittest.main()
