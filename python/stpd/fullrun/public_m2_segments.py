"""Conservative observed-segment metadata from an already verified Bundle3 view.

The caller supplies complete session journal and accepted trace streams, with exact
refs computed from their original events. This module neither reads archives nor
verifies Human origin. Its qualified segment ID is split at every unproved edge;
it is suitable for PublicM2EvidenceRow.recording_segment_id.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from spireagent.json_boundary import BoundaryError, digest

from ..canonical import semantic_hash


@dataclass(frozen=True)
class JournalMark:
    sequence: int  # Full, session-wide run-journal sequence.
    event_ref: str  # semantic_hash of the original complete journal event.
    kind: str
    session_id: str
    run_id: str | None
    recorded_at: str


@dataclass(frozen=True)
class AcceptedMark:
    trace_ordinal: int  # Original semantic-boundary-trace sequence.
    accepted_ref: str  # semantic_hash of the original complete accepted event.
    session_id: str
    run_id: str
    witness_id: str
    action_sequence: int
    observed_at: str


@dataclass(frozen=True)
class LedgerOccurrence:
    trace_ordinal: int
    accepted_ref: str
    run_id: str
    witness_id: str
    action_sequence: int
    disposition: str
    canonical_record_id: str | None


@dataclass(frozen=True)
class FrameRef:
    object_ref: str
    content_sha256: str
    snapshot_id: str


@dataclass(frozen=True)
class ProvedTransition:
    accepted_ref: str
    transition_id: str
    witness_id: str
    action_sequence: int
    pre: FrameRef
    successor: FrameRef


@dataclass(frozen=True)
class AcceptedPlacement:
    accepted_ref: str
    trace_ordinal: int
    status: str  # known-qualified, unknown, rejected
    reason: str | None
    capture_segment_id: str | None
    recording_segment_id: str | None  # Qualified continuity component.


@dataclass(frozen=True)
class SegmentEdge:
    left_ref: str
    right_ref: str
    allowed: bool
    reason: str


@dataclass(frozen=True)
class SegmentResult:
    placements: tuple[AcceptedPlacement, ...]
    edges: tuple[SegmentEdge, ...]


_OPEN = frozenset({"run_started_native", "run_observed_in_progress", "run_resumed_native"})
_CLOSE_RUN = frozenset({
    "run_ended_native", "run_ended_unproved", "run_reloaded_native",
    "run_launched_native_origin_unknown", "run_abandoned",
})
_CLOSE_CAPTURE = frozenset({
    "recording_interrupted", "recording_close_requested", "recording_closed",
    "session_closed", "session_close_requested",
})


def _time(value: str) -> datetime | None:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except (AttributeError, ValueError):
        return None


def _id(source: str, session: str, run: str, marker: str, label: str) -> str:
    return semantic_hash({"profile": "stpd/public-m2-observed-segments-v1",
                          "source": source, "session": session, "run": run,
                          "marker": marker, "label": label})


def _frame_valid(ref: FrameRef) -> bool:
    return (bool(ref.object_ref and ref.snapshot_id)
            and re.fullmatch(r"[0-9a-f]{64}", ref.content_sha256) is not None)


def derive_public_m2_segments(
    *, source_archive_sha256: str, session_id: str, run_id: str,
    journal: tuple[JournalMark, ...], accepted: tuple[AcceptedMark, ...],
    ledger: tuple[LedgerOccurrence, ...], proved: tuple[ProvedTransition, ...],
) -> SegmentResult:
    """Place all accepted actions, then split qualified IDs at every missing edge.

    Requires a complete verified projection for one source/session/run; slim refs
    alone cannot establish source integrity. Journal time only locates an exact-ref
    accepted event inside a sequence-ordered interval; it never proves an edge.
    """
    digest(source_archive_sha256, "public_m2_segment.source")
    if not session_id or not run_id or not journal:
        raise BoundaryError("public_m2_segment", "source_session_run_journal_required")
    marks = sorted(journal, key=lambda row: row.sequence)
    if any(type(m.sequence) is not int or m.sequence < 1 or m.session_id != session_id
           or not m.kind or not m.event_ref for m in marks):
        raise BoundaryError("public_m2_segment", "journal_binding_invalid")
    for mark in marks:
        digest(mark.event_ref, "public_m2_segment.journal_ref")
    if len({m.sequence for m in marks}) != len(marks):
        raise BoundaryError("public_m2_segment", "duplicate_journal_sequence")
    if len({a.trace_ordinal for a in accepted}) != len(accepted):
        raise BoundaryError("public_m2_segment", "duplicate_trace_ordinal")
    if len({item.trace_ordinal for item in ledger}) != len(ledger):
        raise BoundaryError("public_m2_segment", "duplicate_ledger_ordinal")
    for a in accepted:
        digest(a.accepted_ref, "public_m2_segment.accepted_ref")
        if (a.session_id != session_id or a.run_id != run_id
                or type(a.trace_ordinal) is not int or a.trace_ordinal < 1
                or type(a.action_sequence) is not int or a.action_sequence < 1):
            raise BoundaryError("public_m2_segment", "accepted_binding_invalid")
    for item in ledger:
        digest(item.accepted_ref, "public_m2_segment.ledger_ref")
        if (item.run_id != run_id or type(item.trace_ordinal) is not int
                or item.trace_ordinal < 1):
            raise BoundaryError("public_m2_segment", "ledger_binding_invalid")

    # State is projected after each journal mark. A missing sequence or time
    # contradiction clears the active span; a later explicit run marker may open
    # a new one, without disqualifying already bounded earlier observations.
    capture = False
    run_active = False
    segment: str | None = None
    intervals: list[tuple[datetime, datetime, str | None, str]] = []
    previous: JournalMark | None = None
    for mark in marks:
        when = _time(mark.recorded_at)
        if previous is not None:
            before = _time(previous.recorded_at)
            valid_order = (mark.sequence == previous.sequence + 1 and before is not None
                           and when is not None and before < when)
            if valid_order:
                assert before is not None and when is not None
                intervals.append((before, when, segment, "inactive_or_gap"))
            else:
                run_active = False
                segment = None
        kind = mark.kind
        target = mark.run_id == run_id
        if kind == "session_started":
            capture = True
        elif kind == "recording_paused":
            capture = False
            segment = None
        elif kind == "recording_resumed":
            capture = True
            if run_active:
                # New observed capture span, never a fresh native run claim.
                segment = _id(source_archive_sha256, session_id, run_id,
                              mark.event_ref, "capture")
        elif kind in _CLOSE_CAPTURE:
            capture = False
            run_active = False
            segment = None
        elif kind == "recording_segment_exit":
            # A recording boundary may still name the preceding run.
            run_active = False
            segment = None
        elif kind in _OPEN:
            if target:
                run_active = True
                segment = (_id(source_archive_sha256, session_id, run_id,
                               mark.event_ref, "capture") if capture else None)
            elif run_active:
                run_active = False
                segment = None
        elif kind in _CLOSE_RUN and target:
            run_active = False
            segment = None
        elif kind == "run_abandoned_native" and target and run_active:
            # Normally follows run_ended_native; anomalous ordering fails closed.
            run_active = False
            segment = None
        previous = mark

    trace_by_ordinal = {a.trace_ordinal: a for a in accepted}
    ledger_by_ordinal = {item.trace_ordinal: item for item in ledger}
    trace_counts = Counter(a.accepted_ref for a in accepted)
    ledger_counts = Counter(item.accepted_ref for item in ledger)
    duplicate_refs = ({ref for ref, count in trace_counts.items() if count > 1}
                      | {ref for ref, count in ledger_counts.items() if count > 1})
    proof_by_ref = {p.accepted_ref: p for p in proved}
    if len(proof_by_ref) != len(proved):
        raise BoundaryError("public_m2_segment", "duplicate_proved_ref")
    by_time = sorted(intervals, key=lambda span: span[0])
    starts = [span[0] for span in by_time]
    prefix_end: list[datetime] = []
    for span in by_time:
        prefix_end.append(max(prefix_end[-1], span[1]) if prefix_end else span[1])

    def containing_segment(observed: datetime | None) -> tuple[bool, str | None]:
        if observed is None:
            return False, None
        index = bisect_left(starts, observed) - 1
        if (index < 0 or by_time[index][1] <= observed
                or (index > 0 and prefix_end[index - 1] > observed)):
            return False, None
        return True, by_time[index][2]

    ordered = sorted(set(trace_by_ordinal) | set(ledger_by_ordinal))
    raw: list[tuple[AcceptedPlacement, ProvedTransition | None]] = []
    for ordinal in ordered:
        action = trace_by_ordinal.get(ordinal)
        occurrence = ledger_by_ordinal.get(ordinal)
        ref = action.accepted_ref if action else occurrence.accepted_ref  # type: ignore[union-attr]
        status = "unknown"
        reason: str | None = "exact_accepted_ledger_join_missing"
        capture_id: str | None = None
        proof = None
        if ref in duplicate_refs:
            reason = "duplicate_exact_accepted_ref"
        elif action is not None and occurrence is not None and (
            action.accepted_ref == occurrence.accepted_ref
            and action.run_id == occurrence.run_id
            and action.witness_id == occurrence.witness_id
            and action.action_sequence == occurrence.action_sequence
        ):
            uniquely_bounded, placed_segment = containing_segment(_time(action.observed_at))
            if not uniquely_bounded:
                reason = "accepted_time_not_unique_bounded_journal_interval"
            elif placed_segment is None:
                reason = "capture_not_active_or_journal_gap"
            else:
                capture_id = placed_segment
                if occurrence.disposition != "transition_proved":
                    status, reason = "rejected", "disposition_" + occurrence.disposition
                else:
                    proof = proof_by_ref.get(ref)
                    if (proof is None or not occurrence.canonical_record_id
                            or proof.transition_id != occurrence.canonical_record_id
                            or proof.witness_id != occurrence.witness_id
                            or proof.action_sequence != occurrence.action_sequence
                            or not _frame_valid(proof.pre) or not _frame_valid(proof.successor)):
                        reason = "canonical_exact_binding_missing"
                        proof = None
                    else:
                        status, reason = "known-qualified", None
        raw.append((AcceptedPlacement(ref, ordinal, status, reason, capture_id, None), proof))

    placements: list[AcceptedPlacement] = []
    edges: list[SegmentEdge] = []
    current_qualified: str | None = None
    for index, (place, proof) in enumerate(raw):
        edge_ok = False
        if index:
            prior, prior_proof = raw[index - 1]
            left = trace_by_ordinal.get(prior.trace_ordinal)
            right = trace_by_ordinal.get(place.trace_ordinal)
            if prior.status != "known-qualified" or place.status != "known-qualified":
                edge_reason = "unqualified_accepted_between_or_endpoint"
            elif prior.capture_segment_id != place.capture_segment_id:
                edge_reason = "capture_boundary"
            elif left is None or right is None or prior_proof is None or proof is None:
                edge_reason = "exact_ledger_or_proof_missing"
            elif right.action_sequence != left.action_sequence + 1:
                edge_reason = "nonconsecutive_action_sequence"
            elif prior_proof.successor != proof.pre:
                edge_reason = "proved_frame_edge_mismatch"
            else:
                edge_ok, edge_reason = True, "exact_adjacent_proved_edge"
            edges.append(SegmentEdge(prior.accepted_ref, place.accepted_ref,
                                     edge_ok, edge_reason))
        if place.status == "known-qualified":
            if not edge_ok:
                current_qualified = _id(source_archive_sha256, session_id, run_id,
                                        place.accepted_ref, "qualified")
            placements.append(AcceptedPlacement(
                place.accepted_ref, place.trace_ordinal, place.status, place.reason,
                place.capture_segment_id, current_qualified))
        else:
            current_qualified = None
            placements.append(place)
    return SegmentResult(tuple(placements), tuple(edges))
