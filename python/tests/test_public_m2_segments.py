"""Synthetic metadata checks; these do not qualify any Human source."""

from __future__ import annotations

from dataclasses import replace

import pytest
from platform_bundle3_fixture import bundle3, rows, seal, stream

from spireagent.json_boundary import BoundaryError
from stpd.canonical import semantic_hash
from stpd.fullrun.platform_bundle3 import PlatformBundle3SourceAdapter, archive_bundle
from stpd.fullrun.public_m2_segments import (
    AcceptedMark,
    FrameRef,
    JournalMark,
    LedgerOccurrence,
    ProvedTransition,
    derive_public_m2_segments,
)

SOURCE = "a" * 64
SESSION = "session-test"
RUN = "run-0001"


def journal(*kinds: tuple[str, int]) -> tuple[JournalMark, ...]:
    return tuple(JournalMark(i + 1, semantic_hash({"journal": i, "kind": kind}),
                             kind, SESSION, RUN, f"2026-09-01T00:00:{second:02}Z")
                 for i, (kind, second) in enumerate(kinds))


def frames(index: int) -> tuple[FrameRef, FrameRef]:
    return (FrameRef(f"frame-{index}", f"{index + 1:064x}", f"snapshot-{index}"),
            FrameRef(f"frame-{index + 1}", f"{index + 2:064x}", f"snapshot-{index + 1}"))


def occurrence(index: int, second: int, *, disposition: str = "transition_proved"):
    ref = semantic_hash({"accepted": index})
    witness = f"witness-{index}"
    accepted = AcceptedMark(index, ref, SESSION, RUN, witness, index,
                            f"2026-09-01T00:00:{second:02}Z")
    ledger = LedgerOccurrence(index, ref, RUN, witness, index, disposition,
                              f"transition-{index}" if disposition == "transition_proved" else None)
    pre, successor = frames(index)
    proof = ProvedTransition(ref, f"transition-{index}", witness, index, pre, successor)
    return accepted, ledger, proof


def derive(marks, records):
    return derive_public_m2_segments(
        source_archive_sha256=SOURCE, session_id=SESSION, run_id=RUN,
        journal=tuple(marks), accepted=tuple(row[0] for row in records),
        ledger=tuple(row[1] for row in records),
        proved=tuple(row[2] for row in records if row[1].disposition == "transition_proved"),
    )


def test_partial_observed_segment_has_exact_edge_without_full_run_boundaries():
    marks = journal(("session_started", 0), ("run_observed_in_progress", 1),
                    ("recording_segment_exit", 5), ("session_closed", 6))
    result = derive(marks, [occurrence(1, 2), occurrence(2, 3)])
    assert [p.status for p in result.placements] == ["known-qualified"] * 2
    assert result.edges[0].allowed
    assert result.placements[0].recording_segment_id == result.placements[1].recording_segment_id
    assert result.placements[0].capture_segment_id == result.placements[1].capture_segment_id


def test_pause_closes_capture_and_resume_is_new_observed_segment():
    marks = journal(("session_started", 0), ("run_started_native", 1),
                    ("recording_paused", 3), ("recording_resumed", 5),
                    ("run_ended_native", 8), ("run_abandoned_native", 9),
                    ("session_closed", 10))
    result = derive(marks, [occurrence(1, 2), occurrence(2, 4), occurrence(3, 6)])
    assert [p.status for p in result.placements] == [
        "known-qualified", "unknown", "known-qualified"]
    assert result.placements[1].reason == "capture_not_active_or_journal_gap"
    assert result.placements[0].recording_segment_id != result.placements[2].recording_segment_id
    assert result.placements[0].capture_segment_id != result.placements[2].capture_segment_id
    assert not any(edge.allowed for edge in result.edges)


def test_unproved_accepted_occurrence_breaks_qualified_continuity():
    marks = journal(("session_started", 0), ("run_observed_in_progress", 1),
                    ("run_ended_native", 7), ("session_closed", 8))
    middle = occurrence(2, 3, disposition="transition_unknown")
    result = derive(marks, [occurrence(1, 2), middle, occurrence(3, 4)])
    assert result.placements[1].status == "rejected"
    assert result.placements[1].reason == "disposition_transition_unknown"
    assert result.placements[0].capture_segment_id == result.placements[2].capture_segment_id
    assert result.placements[0].recording_segment_id != result.placements[2].recording_segment_id
    assert all(not edge.allowed for edge in result.edges)


def test_missing_exact_ref_and_clock_conflict_are_local_unknowns():
    marks = journal(("session_started", 0), ("run_observed_in_progress", 1),
                    ("run_ended_native", 4), ("run_observed_in_progress", 5),
                    ("session_closed", 9))
    first, second = occurrence(1, 2), occurrence(2, 6)
    bad_second = (second[0], replace(second[1], accepted_ref="f" * 64), second[2])
    result = derive(marks, [first, bad_second])
    assert result.placements[0].status == "known-qualified"
    assert result.placements[1].reason == "exact_accepted_ledger_join_missing"
    regressed = tuple(replace(mark, recorded_at="2026-09-01T00:00:00Z")
                      if mark.kind == "run_ended_native" else mark for mark in marks)
    result = derive(regressed, [first, second])
    assert result.placements[0].status == "unknown"
    assert result.placements[1].status == "known-qualified"


def test_full_ledger_adjacency_and_exact_frame_equality_both_required():
    marks = journal(("session_started", 0), ("run_started_native", 1),
                    ("run_ended_native", 7), ("session_closed", 8))
    first, second = occurrence(1, 2), occurrence(2, 3)
    wrong = (second[0], second[1], replace(second[2], pre=FrameRef("other", "b" * 64, "other")))
    result = derive(marks, [first, wrong])
    assert result.edges[0].reason == "proved_frame_edge_mismatch"
    assert result.placements[0].recording_segment_id != result.placements[1].recording_segment_id


def test_session_pause_tagged_prior_run_and_local_projection_omission():
    marks = list(journal(("session_started", 0), ("recording_paused", 1),
                         ("run_started_native", 2), ("recording_resumed", 4),
                         ("canonical_projection_unsupported", 6),
                         ("run_ended_native", 9), ("session_closed", 10)))
    marks[1] = replace(marks[1], run_id="previous-run")
    result = derive(marks, [occurrence(1, 3), occurrence(2, 5), occurrence(3, 7)])
    assert result.placements[0].status == "unknown"
    assert [p.status for p in result.placements[1:]] == ["known-qualified"] * 2
    # A local projection omission is not a capture pause or whole-run ban.
    assert result.edges[-1].allowed


@pytest.mark.parametrize("bad_sequence", [None, "2", True])
def test_malformed_journal_sequence_is_typed_boundary_failure(bad_sequence):
    marks = list(journal(("session_started", 0), ("run_started_native", 1),
                         ("session_closed", 8)))
    marks[1] = replace(marks[1], sequence=bad_sequence)
    with pytest.raises(BoundaryError, match="journal_binding_invalid"):
        derive(marks, [occurrence(1, 2)])


def test_official_bundle3_fixture_fields_join_by_exact_accepted_ref(tmp_path):
    bundle = bundle3(tmp_path, runs=1)
    path = bundle / "raw/run-journal.jsonl"
    original = rows(path)
    ordinary = {**original[1], "kind": "act_change_owner_ready", "event_id": "same-tick"}
    original.insert(2, ordinary)  # Same timestamp as native start, no capture gap.
    for index, row in enumerate(original, 1):
        row["sequence"] = index
    stream(path, original)
    seal(bundle)
    source = archive_bundle(bundle)
    projection = PlatformBundle3SourceAdapter().project(source)
    accounting = projection.accounting.value()
    original_journal = rows(bundle / "raw/run-journal.jsonl")
    trace = rows(bundle / "raw/semantic-boundary-trace.jsonl")
    canonical = rows(bundle / "raw/canonical-transitions.jsonl")
    accepted_rows = [row for row in trace if row["kind"] == "action_accepted"]
    accepted = tuple(AcceptedMark(
        row["sequence"], semantic_hash(row), row["session_id"], row["run_id"],
        row["action"]["action_witness_id"], row["action"]["action_sequence"],
        row["observed_at"],
    ) for row in accepted_rows)
    ordinals = {row.accepted_ref: row.trace_ordinal for row in accepted}
    ledger = tuple(LedgerOccurrence(
        ordinals[item["accepted_ref"]], item["accepted_ref"], item["action"]["run_id"],
        item["action"]["action_witness_id"], item["action"]["action_sequence"],
        item["disposition"], item["canonical_record_id"],
    ) for item in accounting["occurrences"])
    refs = {row["action_witness_id"]: next(
        item["accepted_ref"] for item in accounting["occurrences"]
        if item["action"]["action_witness_id"] == row["action_witness_id"])
        for row in canonical}
    proved = tuple(ProvedTransition(
        refs[row["action_witness_id"]], row["transition_id"], row["action_witness_id"],
        row["action_sequence"], FrameRef(**row["pre_state_ref"]),
        FrameRef(**row["successor_ref"]),
    ) for row in canonical)
    marks = tuple(JournalMark(
        row["sequence"], semantic_hash(row), row["kind"], row["session_id"],
        row.get("run_id"), row["recorded_at"],
    ) for row in original_journal)
    result = derive_public_m2_segments(
        source_archive_sha256=projection.source_sha256,
        session_id=accepted[0].session_id, run_id=accepted[0].run_id,
        journal=marks, accepted=accepted, ledger=ledger, proved=proved,
    )
    assert len(result.placements) == 2
    assert all(item.status == "known-qualified" for item in result.placements)
    assert result.placements[0].capture_segment_id == result.placements[1].capture_segment_id
    # This fixture's two independent frame objects do not assert an exact edge.
    assert result.edges[0].reason == "proved_frame_edge_mismatch"
