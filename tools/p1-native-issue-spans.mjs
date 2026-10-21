/** Locate missing facts in the original timeline. Recovery is an independently
 * captured complete, uncommitted boundary, never a reconstructed missing action. */
export function nativeIssueSpan({ events, issue, captures, boundaries = new Map() }) {
  const accepted = events.filter(row => row.kind === 'decision_accepted');
  const byDecision = new Map(accepted.map(row => [row.decision_id, row]));
  const byWitness = new Map(accepted.map(row => [row.action_witness_id, row]));
  const terminal = new Map(events.filter(row => row.kind === 'lifecycle'
    && ['finished', 'cancelled', 'aborted', 'unknown'].includes(row.phase)).map(row => [row.decision_id, row]));
  const decisionId = issue.decision_id ?? issue.native_witness?.decision_id;
  const row = byDecision.get(decisionId);
  if (row) {
    let first = row, parent = byWitness.get(row.native_witness?.parent_action_witness_id);
    const ids = new Set([row.decision_id]);
    while (parent && !ids.has(parent.decision_id)) {
      ids.add(parent.decision_id); first = parent;
      parent = byWitness.get(parent.native_witness?.parent_action_witness_id);
    }
    return { from_sequence: first.sequence,
      through_sequence: Math.max(...[...ids].map(id => terminal.get(id)?.sequence ?? events.at(-1)?.sequence ?? row.sequence)),
      decision_ids: [...ids], location: 'accepted_decision_and_native_parent' };
  }
  // Only a retained original issue event can locate an unbound failure. A
  // converter diagnostic with no source event retains the run-wide barrier.
  const original = events.find(event => event.kind === 'issue' && event.event_id === issue.event_id
    && event.sequence === issue.sequence);
  if (!original) return { from_sequence: events[0]?.sequence ?? 0,
    through_sequence: events.at(-1)?.sequence ?? 0, decision_ids: [], location: 'unlocated_run_scope' };
  const recovery = captures.map((capture, index) => ({ capture, boundary: boundaries.get(index) }))
    .find(({ capture, boundary }) => capture.sequence > original.sequence && capture.complete
      && capture.public_basis && boundary?.eligible);
  return { from_sequence: original.sequence,
    through_sequence: recovery ? recovery.capture.sequence - 1 : events.at(-1)?.sequence ?? original.sequence,
    decision_ids: [], location: 'original_issue_to_complete_uncommitted_boundary',
    source_issue_event_id: original.event_id,
    ...(recovery ? { recovery_boundary: { source_event_id: recovery.capture.event_id,
      source_sequence: recovery.capture.sequence, source_public_basis: recovery.capture.public_basis,
      complete_basis: true, pending_native_decisions: recovery.boundary.pending_native_decisions,
      unfinished_reward_commitment: false, selector_continuation: false } } : {}) };
}
