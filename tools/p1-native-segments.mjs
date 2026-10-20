/** Offline segments from the converter's actual shared-core state and originals.
 * This neither repairs missing bytes nor grants whole-run/training admission. */
export function buildNativeP1Segments({ source, report, successful, boundaries }) {
  const identity = source.manifest.content_identity ?? source.manifest;
  const events = source.events.filter(row => row.run_id === report.scope.run_id);
  const accepted = events.filter(row => row.kind === 'decision_accepted');
  const byDecision = new Map(accepted.map(row => [row.decision_id, row]));
  const byWitness = new Map(accepted.map(row => [row.action_witness_id, row]));
  const terminal = new Map(events.filter(row => row.kind === 'lifecycle'
    && ['finished', 'cancelled', 'aborted', 'unknown'].includes(row.phase)).map(row => [row.decision_id, row]));
  const barriers = [];
  function barrier(row, reason, lastSequence = null) {
    if (!row) {
      barriers.push({ from_sequence: events[0]?.sequence ?? 0,
        through_sequence: events.at(-1)?.sequence ?? 0, reasons: [reason], decision_ids: [] });
      return;
    }
    let first = row, parent = byWitness.get(row.native_witness?.parent_action_witness_id);
    const visited = new Set([row.decision_id]);
    while (parent && !visited.has(parent.decision_id)) {
      visited.add(parent.decision_id); first = parent;
      parent = byWitness.get(parent.native_witness?.parent_action_witness_id);
    }
    barriers.push({ from_sequence: first.sequence,
      through_sequence: Math.max(lastSequence ?? terminal.get(row.decision_id)?.sequence ?? events.at(-1)?.sequence ?? row.sequence,
        terminal.get(first.decision_id)?.sequence ?? events.at(-1)?.sequence ?? first.sequence),
      reasons: [reason], decision_ids: [...visited] });
  }
  for (const issue of [...report.blocking_issues, ...report.pending_decisions])
    barrier(byDecision.get(issue.decision_id ?? issue.native_witness?.decision_id), issue.reason);
  // The exception has an actual state effect, no P1 successful choice and no
  // next target. Later complete states can start a D segment, never a splice.
  for (const span of report.native_presentations.filter(item => item.exception)) {
    const last = events.find(row => row.event_id === span.source_event_id);
    barrier(byDecision.get(span.decision_ids[0]), `projection_exception:${span.exception.id}`, last?.sequence);
  }
  barriers.sort((a, b) => a.from_sequence - b.from_sequence);
  const gaps = [];
  for (const gap of barriers) {
    const prior = gaps.at(-1);
    if (prior && gap.from_sequence <= prior.through_sequence) {
      prior.through_sequence = Math.max(prior.through_sequence, gap.through_sequence);
      prior.reasons = [...new Set([...prior.reasons, ...gap.reasons])];
      prior.decision_ids = [...new Set([...prior.decision_ids, ...gap.decision_ids])];
    } else gaps.push({ ...gap });
  }
  const branch = { session_id: identity.session_id, timeline_id: identity.timeline_id ?? null,
    run_id: report.scope.run_id };
  const terminalCapture = report.captures.findLast(item => item.complete && item.observation?.status === 'terminal');
  const journal = source.journal.filter(row => row.run_id === report.scope.run_id);
  const endProven = terminalCapture && journal.filter(row => row.kind === 'run_ended_native').length === 1
    && !journal.some(row => ['run_resumed_native', 'run_abandoned_native', 'run_ended_unproved'].includes(row.kind));
  const endTarget = endProven ? { branch, source_event_id: terminalCapture.event_id,
    source_public_basis: terminalCapture.public_basis,
    result: terminalCapture.observation.scene.content.context?.result ?? null } : null;
  const segments = [], discarded = [];
  let current = null, recoveryReason = 'source_start';
  function finish(reason) {
    if (!current) return;
    current.end_reason = reason;
    current.source_event_range.through_sequence = current.points.at(-1).source_sequence;
    segments.push(current); current = null;
  }
  for (const action of successful) {
    const capture = report.captures[action.capture], sequence = capture.sequence;
    const overlap = gaps.find(gap => gap.from_sequence <= (action.terminal_sequence ?? sequence)
      && gap.through_sequence >= sequence);
    const crossed = current && gaps.find(gap => gap.from_sequence > current.points.at(-1).source_sequence
      && gap.from_sequence < sequence);
    if (crossed) { finish(crossed.reasons.join(';')); recoveryReason = 'after:' + crossed.reasons.join(';'); }
    if (overlap || !capture.complete || !capture.public_basis
        || !action.choice_id || !capture.observation.catalog.some(choice => choice.choice_id === action.choice_id)) {
      const reason = overlap ? overlap.reasons.join(';') : 'unqualified_choice_basis';
      finish(reason); recoveryReason = 'after:' + reason;
      discarded.push({ decision_id: action.decision_id, sequence, reason }); continue;
    }
    if (!current) {
      const boundary = boundaries.get(action.capture);
      if (!boundary?.eligible) {
        discarded.push({ decision_id: action.decision_id, sequence, reason: 'no_complete_uncommitted_recovery_boundary' });
        continue;
      }
      const priorGaps = gaps.filter(gap => gap.through_sequence < sequence);
      current = { segment_id: `segment-${segments.length + 1}`, data_class: priorGaps.length ? 'D' : 'T',
        branch, start_reason: priorGaps.length ? recoveryReason === 'source_start' ? 'after:recorded_gap' : recoveryReason : 'source_start',
        source_event_range: { from_sequence: sequence, through_sequence: sequence },
        start_public_basis: capture.public_basis,
        start_protocol_state: boundary.protocol_state,
        start_boundary: { complete_basis: true, unfinished_reward_commitment: false,
          pending_native_decisions: [], selector_continuation: false },
        source_exceptions: priorGaps.filter(gap => gap.reasons.some(reason => reason.startsWith('projection_exception:'))),
        end_target: endTarget, points: [] };
    }
    if (action.data_class === 'D') current.data_class = 'D';
    if (current.points.length) current.points.at(-1).mask.next = true;
    current.points.push({ index: current.points.length, source_sequence: sequence,
      source_event_id: capture.event_id,
      source_decision_id: action.decision_id.startsWith('D:') ? action.selector_transaction.anchor_decision_id : action.decision_id,
      ...(action.selector_transaction ? { data_class: 'D', selector_transaction: action.selector_transaction,
        ...(action.decision_id.startsWith('D:') ? { derived_choice_id: action.decision_id } : {}) } : {}),
      source_public_basis: capture.public_basis, observation: capture.observation, chosen: action.choice_id,
      mask: { N: true, next: false, end: endTarget?.result != null } });
  }
  if (current && terminalCapture && terminalCapture.sequence > current.points.at(-1).source_sequence
      && !gaps.some(gap => gap.from_sequence > current.points.at(-1).source_sequence)) {
    current.points.at(-1).mask.next = true;
    current.points.push({ index: current.points.length, source_sequence: terminalCapture.sequence,
      source_event_id: terminalCapture.event_id, source_decision_id: null,
      source_public_basis: terminalCapture.public_basis, observation: terminalCapture.observation,
      chosen: null, mask: { N: false, next: false, end: endTarget?.result != null } });
    finish('observed_natural_terminal');
  } else finish('source_boundary');
  return { schema: 'sts2.p1/native-segments-1', source_bundle: source.bundle,
    source_content_id: report.source_content_id, source_manifest_sha256: report.source_manifest_sha256,
    projection_claims: report.projection_claims,
    branch, profile: 'p1-global-r1', revision: segments[0]?.points[0]?.observation.revision ?? null,
    whole_run_complete: report.whole_run_recording_complete,
    source_conversion_complete: report.complete, gaps, discarded_choices: discarded, segments,
    training_loader_consumed: false,
    nonclaims: ['not_whole_run_repair', 'not_host_equivalence', 'not_research_admission'] };
}
