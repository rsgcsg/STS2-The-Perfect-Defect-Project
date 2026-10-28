/** Replay-only plan over the unmodified converter report and verified originals.
 * No P1 labels, projection flags or source records are repaired here. */
const complete = basis => basis?.capture_status === 'complete' && basis.missing?.length === 0
  && basis.snapshot?.completeness?.status === 'complete';
const bridgeReasons = new Set(['accepted_action_has_no_p1_choice',
  'accepted_action_needs_intention_or_continuation', 'p1_intention_requires_native_continuation',
  'native_presentation_public_state_changed']);
const sourceBasis = (original, reference) => reference
  ? JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(original.bytes(reference.payload_ref))) : null;

export function buildRecordedReplayPlan(original, conversion) {
  const rows = original.events;
  const accepted = new Map(rows.filter(row => row.kind === 'decision_accepted').map(row => [row.decision_id, row]));
  const terminals = new Map(rows.filter(row => row.kind === 'lifecycle'
    && ['finished', 'unknown', 'cancelled'].includes(row.phase)).map(row => [row.decision_id, row]));
  const actions = new Map(conversion.actions.map(action => [action.decision_id, action]));
  const bridges = new Map();
  function bridge(action) {
    const row = accepted.get(action.decision_id), end = terminals.get(action.decision_id);
    if (!row?.bound_action || row.native_receipt?.kind !== 'semantic_ui_accepted'
        || row.missing_reason != null || end?.phase !== 'finished'
        || !complete(sourceBasis(original, row.basis)) || !complete(sourceBasis(original, end.successor_basis))) return null;
    return { decision_id: row.decision_id, source_event_id: row.event_id,
      original_terminal_event_id: end.event_id, original_terminal_phase: end.phase,
      basis: sourceBasis(original, row.basis), bound_action: row.bound_action,
      reason: action.reason, p1_compared: false };
  }
  let barrier = Infinity; const barriers = [];
  for (const event of original.journal ?? []) {
    if (!['run_resumed_native', 'run_abandoned_native', 'run_ended_unproved',
      'recording_paused', 'recording_interrupted'].includes(event.kind)) continue;
    if (event.run_id != null && event.run_id !== conversion.scope?.run_id) continue;
    const at = Date.parse(event.recorded_at);
    // Journal and input sequence numbers are separate domains. Only timestamps
    // align a recorded boundary; absent timing cannot qualify any prefix.
    const later = Number.isFinite(at) ? rows.find(row => Date.parse(row.recorded_at) >= at) : null;
    const sequence = Number.isFinite(at) ? later?.sequence ?? Infinity : 0;
    barrier = Math.min(barrier, sequence);
    barriers.push({ reason: event.kind, source_event_id: event.event_id, sequence: Number.isFinite(sequence) ? sequence : null });
  }
  for (const issue of conversion.blocking_issues) {
    const action = actions.get(issue.decision_id);
    const witness = action && bridgeReasons.has(issue.reason) ? bridge(action) : null;
    if (witness) { bridges.set(action.decision_id, witness); continue; }
    const sequence = issue.sequence ?? (action ? conversion.captures[action.capture].sequence : 0);
    barrier = Math.min(barrier, sequence);
    barriers.push({ reason: issue.reason, decision_id: issue.decision_id ?? null, sequence });
  }
  for (const action of conversion.actions) {
    if (action.result?.status === 'unknown' || action.result == null) {
      const sequence = conversion.captures[action.capture].sequence;
      barrier = Math.min(barrier, sequence);
      barriers.push({ reason: 'original_unknown_or_unfinished_no_reoffer', decision_id: action.decision_id, sequence });
    }
  }
  const plan = [];
  for (const action of conversion.actions) {
    const capture = conversion.captures[action.capture];
    if (capture.sequence >= barrier) continue;
    if (['cancelled', 'native_cancelled'].includes(action.native_disposition?.disposition)) continue;
    // Every recorded browsing input is driven once, including a changed world.
    // It is never promoted to a P1 choice or to a matching-point numerator.
    const isPresentation = action.native_disposition?.kind != null;
    if (bridges.has(action.decision_id) || isPresentation) {
      const witness = bridges.get(action.decision_id) ?? bridge(action);
      if (!witness) {
        barrier = Math.min(barrier, capture.sequence);
        barriers.push({ reason: 'native_bridge_original_incomplete', decision_id: action.decision_id, sequence: capture.sequence });
        continue;
      }
      plan.push({ sequence: capture.sequence, observation: capture.observation, chosen: null, native_bridge: witness });
    } else if (action.match_status === 'matched' && action.result?.status === 'completed') {
      plan.push({ sequence: capture.sequence, observation: capture.observation, chosen: action.choice_id });
    }
  }
  // Retain the converter's existing no-input cancellation checkpoints.
  for (const span of conversion.native_cancellations) for (const check of span.continuity_checkpoints) {
    const capture = conversion.captures.find(item => item.event_id === check.source_event_id
      && item.public_basis?.sha256 === check.source_public_basis?.sha256);
    if (capture && capture.sequence < barrier) plan.push({ sequence: capture.sequence,
      observation: capture.observation, chosen: null, replay_checkpoint: true });
  }
  const points = plan.filter(point => point.sequence < barrier).sort((a, b) => a.sequence - b.sequence || Number(Boolean(b.replay_checkpoint)) - Number(Boolean(a.replay_checkpoint)));
  const last = conversion.captures.filter(capture => capture.sequence < barrier && capture.complete
    && ['decision', 'terminal'].includes(capture.observation.status)).at(-1);
  if (!points.length || !last || last.sequence < points.at(-1).sequence)
    throw new Error('recording_replay_no_safe_known_prefix');
  points.push({ sequence: last.sequence, observation: last.observation, chosen: null,
    bounded_end: !conversion.whole_run_recording_complete || last.observation.status !== 'terminal' || Number.isFinite(barrier) });
  return { schema: 'sts2.p1/trajectory-1', origin: 'native_semantic_recording',
    points: points.map((point, index) => ({ ...point, index })), native_presentations: [],
    replay_scope: { kind: 'recorded_native_bridge_diagnostic', p1_conversion_changed: false,
      original_complete: conversion.complete, original_whole_run_complete: conversion.whole_run_recording_complete,
      original_unknown_count: conversion.raw_unknown_count, stop_before_sequence: Number.isFinite(barrier) ? barrier : null,
      barriers, unlabelled_native_inputs: points.filter(point => point.native_bridge).length } };
}
