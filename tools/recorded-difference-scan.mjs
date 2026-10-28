/** One diagnostic mode of recorded replay: drive the original native timeline,
 * collect differences independently, stop only at unavailable/unknown original
 * inputs, failed Managed execution, source boundaries or explicit owner budgets. */
import { randomUUID } from 'node:crypto';
import * as core from '../components/connector/sdk/typescript/dist/p1Global/index.js';
import { RecordedActionLocator } from './recorded-action-locator.mjs';
import { collectObservationDifferences, collectNativeDisplayDifferences } from './recorded-difference-collector.mjs';
export function buildRecordedInputTimeline(original, conversion) {
  const runId = conversion.scope.run_id;
  const events = original.events.filter(row => row.run_id == null || row.run_id === runId);
  const accepted = new Map(events.filter(row => row.kind === 'decision_accepted').map(row => [row.decision_id, row]));
  const executed = new Map(events.filter(row => row.kind === 'execution').map(row => [row.decision_id, row]));
  const terminal = new Map(events.filter(row => row.kind === 'lifecycle' && ['finished', 'cancelled', 'unknown', 'aborted'].includes(row.phase))
    .map(row => [row.decision_id, row]));
  let barrier = Infinity; const boundaries = [];
  for (const event of original.journal ?? []) {
    if (!['run_resumed_native', 'run_abandoned_native', 'run_ended_unproved', 'recording_paused', 'recording_interrupted'].includes(event.kind)
      || event.run_id != null && event.run_id !== runId) continue;
    const at = Date.parse(event.recorded_at), later = Number.isFinite(at) ? events.find(row => Date.parse(row.recorded_at) >= at) : null;
    const sequence = Number.isFinite(at) ? later?.sequence ?? Infinity : 0;
    barrier = Math.min(barrier, sequence); boundaries.push({ sequence: Number.isFinite(sequence) ? sequence : null, reason: event.kind });
  }
  const source = reference => reference ? JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(original.bytes(reference.payload_ref))) : null;
  const actions = new Map(conversion.actions.map(action => [action.decision_id, action]));
  const continuationParents = new Map();
  for (const action of conversion.actions) for (const child of action.native_continuations ?? []) {
    continuationParents.set(child.decision_id, action.decision_id);
    if (!actions.has(child.decision_id)) actions.set(child.decision_id, { ...child, continuation_of: action.decision_id,
      terminal_sequence: terminal.get(child.decision_id)?.sequence ?? null, choice: null });
  }
  // Missing recorded operations must never be silently bridged by the next row.
  // Conversion-only gaps with an exact original native input are diagnostics,
  // not missing actions; they can still be replayed without making P1 labels.
  for (const issue of conversion.blocking_issues ?? []) {
    const decision = issue.decision_id ?? issue.native_witness?.decision_id;
    const known = accepted.get(decision), execute = executed.get(decision), end = terminal.get(decision);
    const sourceRow = execute ?? known;
    if (sourceRow?.basis && (sourceRow.bound_action || sourceRow.native_input) && end
        && ['finished', 'cancelled'].includes(end.phase)) continue;
    const sequence = issue.sequence ?? (decision && actions.has(decision) ? conversion.captures[actions.get(decision).capture]?.sequence : null);
    if (Number.isSafeInteger(sequence)) {
      barrier = Math.min(barrier, sequence); boundaries.push({ sequence, reason: issue.reason, source: 'unrepresented_original_operation' });
    }
  }

  const captures = new Map();
  for (const capture of conversion.captures) {
    const list = captures.get(capture.event_id) ?? []; list.push(capture); captures.set(capture.event_id, list);
  }
  const timeline = [];
  for (const event of events) {
    if (event.sequence >= barrier) break;
    if (event.kind === 'issue') {
      const known = accepted.get(event.decision_id) ?? (event.action_witness_id ? [...accepted.values()].find(row => row.action_witness_id === event.action_witness_id) : null);
      if (!known && (event.native_action_type || event.native_witness || /action|input|selector|choice/u.test(event.reason ?? ''))) {
        timeline.push({ event, stop: 'original_unrecorded_input_gap', source_issue: event }); break;
      }
      timeline.push({ event, captures: [], source_gap: event }); continue;
    }
    const parent = accepted.get(event.decision_id), execution = executed.get(event.decision_id), end = terminal.get(event.decision_id);
    const isGameAccepted = event.kind === 'decision_accepted' && event.native_receipt?.kind === 'game_action_accepted';
    const cancelledWithoutCommit = conversion.native_cancellations.some(item => item.decision_id === event.decision_id && ['cancelled_before_execution', 'cancelled_after_execution'].includes(item.phase));
    const isInput = !cancelledWithoutCommit && (event.kind === 'execution' || event.kind === 'decision_accepted' && !isGameAccepted);
    if (isGameAccepted && !execution && end?.phase === 'cancelled') continue; // original rejected before execution
    if (isGameAccepted && !execution) {
      timeline.push({ event, stop: 'original_execution_or_terminal_unavailable' }); break;
    }
    if (isGameAccepted) continue; // execute at the recorded pre-execution boundary
    if (isInput && (!end || ['unknown', 'aborted'].includes(end.phase))) {
      timeline.push({ event, stop: end?.phase === 'unknown' ? 'original_unknown_no_reoffer' : 'original_unfinished_or_aborted_no_reoffer' }); break;
    }
    const inputRow = event.kind === 'execution' ? event : parent;
    const item = { event, captures: captures.get(event.event_id) ?? [], action: actions.get(event.decision_id) ?? null };
    if (isInput) {
      if (!inputRow || !(inputRow.bound_action || inputRow.native_input) || !inputRow.basis) { timeline.push({ event, stop: 'original_action_or_basis_unavailable' }); break; }
      item.input = { basis: source(inputRow.basis), bound_action: inputRow.bound_action, native_input: inputRow.native_input ?? null,
        original_execution_action_space: inputRow.execution_action_space ?? null,
        original_terminal_phase: end.phase, original_event_id: inputRow.event_id,
        decision_id: inputRow.decision_id, native_action_type: parent?.native_action_type ?? inputRow.native_action_type };
    }
    if (event.basis || event.successor_basis || isInput || event.kind === 'lifecycle') timeline.push(item);
  }
  return { items: timeline, source, boundaries, stopped_before_sequence: Number.isFinite(barrier) ? barrier : null,
    original_actions: accepted.size, original_events: events.length, original_unknown_count: conversion.raw_unknown_count,
    known_inputs: timeline.filter(item => item.input).length, source_actions: actions, continuationParents };
}

export async function scanRecordedDifferences({ original, conversion, driver, record, seed, initialProgress, budgetMs = 1200000 }) {
  const timeline = buildRecordedInputTimeline(original, conversion), locator = new RecordedActionLocator();
  const report = { schema: 'spireagent/recorded-difference-scan-1', mode: 'collect_all_differences',
    formal_evaluation: false, full_p1_equivalence_complete: false, planned_original_inputs: timeline.known_inputs,
    original_accepted_inputs: timeline.original_actions, original_unknown_count: timeline.original_unknown_count,
    executed_original_inputs: 0, compared_observations: 0, blocking_differences: 0, display_differences: 0,
    unresolved_alignment_groups: 0, differences_complete: true, by_category: {}, source_gaps: [],
    unknown_delivery: false, reached_original_timeline_end: false, reached_original_terminal: false,
    boundaries: timeline.boundaries, stopped_before_sequence: timeline.stopped_before_sequence,
    delivered_inputs_with_differences: 0, model_frames_pending: 0, diagnostic_not_equivalence: true, native_p1_conversion_changed: false };
  const deadline = Date.now() + budgetMs; let basis, index = 0;
  let state = core.createSession(`native-difference-scan-${randomUUID()}`, 'managed', conversion.captures.find(c => c.observation)?.observation.revision);
  const commitments = new Map(), childSubjects = new Map();
  function difference(detail, metadata) {
    const row = { ...metadata, ...detail, after_prior_difference: report.blocking_differences + report.display_differences > 0 };
    if (detail.reason?.includes('alignment_ambiguous')) {
      record.append('diagnostic_comparison_gap', row);
      report.unresolved_alignment_groups++; report.differences_complete = false; return;
    }
    record.append('diagnostic_difference', row);
    report[detail.classification === 'display' ? 'display_differences' : 'blocking_differences']++;
    const stats = report.by_category[detail.category] ??= { classification: detail.classification, occurrences: 0, examples: [] };
    stats.occurrences++; if (stats.examples.length < 3) stats.examples.push(row);
  }
  async function call(command, extra = {}) {
    if (Date.now() >= deadline) throw new Error('diagnostic_owner_time_budget');
    return driver.request({ command, ...extra }, Math.min(60000, deadline - Date.now()));
  }
  async function capture() {
    let next = (await call('capture')).full?.basis;
    while (next?.snapshot.status === 'settling') {
      await new Promise(resolve => setTimeout(resolve, 50)); next = (await call('capture')).full?.basis;
    }
    if (!next?.snapshot || next.snapshot.information_policy?.includes_hidden_information !== false)
      throw new Error('diagnostic_public_capture_unavailable');
    return next;
  }
  function reduce() {
    const reduced = core.reduce(state, basis); state = reduced.state; return reduced;
  }
  function compareCapture(capture, reduced, event) {
    const metadata = { sequence: event.sequence, event_id: event.event_id, decision_id: event.decision_id ?? null,
      original_basis_sha256: capture.public_basis?.sha256 ?? null, phase: capture.origin,
      surface: capture.observation?.scene.native_kind ?? null };
    if (!capture.complete || !reduced.complete) {
      const gap = { ...metadata, original_complete: capture.complete, managed_complete: reduced.complete,
        original_missing: capture.missing, managed_missing: reduced.missing };
      report.source_gaps.push(gap); report.differences_complete = false; record.append('diagnostic_projection_gap', gap);
      if (!reduced.complete) difference({ path: '$.observation.completeness', left: capture.complete, right: reduced.missing,
        classification: 'blocking', category: 'managed_projection_incomplete', reason: 'managed_p1_input_unavailable', affects_model_input: true }, metadata);
      // The raw original remains available for action location even if its P1
      // projection is incomplete. Never promote a partial basis to complete.
      return;
    }
    const comparison = collectObservationDifferences(capture.observation, reduced.observation);
    if (comparison.model_input_frame_check_required) {
      report.model_frames_pending++;
      record.append('diagnostic_model_frame_pending', { ...metadata, reference_observation: capture.observation, actual_observation: reduced.observation,
        strict_graph_verdict: comparison.strict.verdict, reason: 'graph_equal_is_not_InputSpec_frame_equality' });
    }
    report.compared_observations++; report.differences_complete &&= comparison.differences_complete;
    record.append('diagnostic_comparison', { ...metadata, raw_model_input_changed: comparison.raw_model_input_changed,
      strict: comparison.strict, differences_complete: comparison.differences_complete,
      reference_observation: capture.observation, actual_observation: reduced.observation });
    for (const detail of comparison.differences) difference(detail, metadata);
    const originalBasis = capture.public_basis ? timeline.source(capture.public_basis) : null;
    if (originalBasis) for (const detail of collectNativeDisplayDifferences(originalBasis, basis, capture.observation, reduced.observation))
      difference(detail, metadata);
    if (capture.observation.status === 'terminal') {
      report.reached_original_terminal = true;
      report.original_outcome = capture.observation.terminal?.outcome;
      report.managed_outcome = reduced.observation.terminal?.outcome ?? null;
    }
  }
  function currentLowerings(action, input, reduced) {
    return Object.entries(reduced.lowering ?? {}).filter(([choiceId, lowering]) => {
      const choice = reduced.observation.catalog.find(choice => choice.choice_id === choiceId);
      return lowering.first?.bound_action_id === input.bound_action_id
        && (!action?.choice || choice?.kind === action.choice.kind);
    }).map(([, lowering]) => lowering);
  }
  function commitAt(sequence) {
    for (const [decision, pending] of commitments) {
      if (!pending.action || pending.action.terminal_sequence !== sequence || pending.action.result?.status !== 'completed') continue;
      let options = pending.lowerings;
      const child = pending.action.continuation_decision_id;
      const selected = child && childSubjects.get(child);
      if (selected) options = options.filter(lowering => !lowering.reward_intention
        || lowering.reward_intention.option === selected.referent_id || lowering.reward_intention.option === selected.option_id);
      if (options.length === 1) state = core.applyCommitment(state, options[0], true);
      else if (options.some(lowering => lowering.commitment || lowering.reward_intention)) {
        const gap = { sequence, decision_id: decision, reason: 'diagnostic_protocol_commitment_not_uniquely_mapped', candidates: options.length };
        report.source_gaps.push(gap); report.differences_complete = false; record.append('diagnostic_projection_gap', gap);
      }
      commitments.delete(decision);
    }
  }
  try {
    await driver.start(); await call('reset', { seed, initial_progress: initialProgress });
    report.episode_identity = (await call('episode_identity')).identity; basis = await capture();
    for (; index < timeline.items.length; index++) {
      const item = timeline.items[index], event = item.event;
      report.last_original_sequence = event.sequence; report.last_original_event_id = event.event_id;
      report.last_original_surface = item.captures.at(-1)?.observation?.scene.native_kind ?? item.input?.basis.snapshot.interaction.kind;
      if (item.source_gap) { report.source_gaps.push(item.source_gap); report.differences_complete = false; }
      if (item.stop) { report.source_issue = item.source_issue ?? null; report.stop_reason = item.stop; report.stopped_before_sequence = event.sequence; break; }
      // Commitments are the same P1 bookkeeping as the unchanged converter;
      // native input delivery itself never becomes a new training label.
      commitAt(event.sequence);
      const reduced = reduce();
      for (const capture of item.captures) compareCapture(capture, reduced, event);
      if (!item.input) continue;
      const before = report.blocking_differences + report.display_differences;
      let located;
      try { located = locator.locate(item.input, basis); }
      catch (error) {
        difference({ path: '$.native_action', left: item.input.bound_action ?? item.input.native_input, right: basis.snapshot.bound_actions.actions,
          classification: 'blocking', category: 'recorded_action_not_executable', reason: error.message,
          affects_legal_actions: true }, { sequence: event.sequence, decision_id: event.decision_id, surface: report.last_original_surface });
        report.stop_reason = error.message; report.stopped_before_sequence = event.sequence; break;
      }
      if (!item.action?.continuation_of) commitments.set(event.decision_id, { action: item.action, lowerings: currentLowerings(item.action, located.input, reduced) });
      if (timeline.continuationParents.has(event.decision_id)) {
        const id = located.input.subject_referent_id ?? null;
        const referent = basis.snapshot.referents.find(ref => ref.referent_id === id);
        childSubjects.set(event.decision_id, { referent_id: id, option_id: referent?.properties?.option_id });
      }
      driver.assertCanMutate?.();
      const mutation = driver.newMutationId();
      record.append('diagnostic_original_input', { sequence: event.sequence, witness: item.input,
        location: located.evidence, current_input: located.input, p1_compared: false,
        mutation_request_id: mutation, expected_snapshot_id: basis.snapshot.snapshot_id });
      let receipt;
      try { receipt = (await call('step', { mutation_request_id: mutation,
        expected_snapshot_id: basis.snapshot.snapshot_id, bound_action_id: located.input.bound_action_id })).receipt; }
      catch (error) { report.unknown_delivery = true; throw error; }
      record.append('diagnostic_native_receipt', { sequence: event.sequence, mutation_request_id: mutation, receipt });
      if (receipt?.delivery === 'unknown' || receipt?.delivery == null) { report.unknown_delivery = true; throw new Error('diagnostic_unknown_delivery_no_retry'); }
      if (receipt.delivery !== 'delivered') {
        difference({ path: '$.native_receipt', left: { expected: 'delivered' }, right: receipt,
          classification: 'blocking', category: 'recorded_action_not_executable', reason: receipt.reason_code ?? 'native_not_delivered',
          affects_results: true }, { sequence: event.sequence, surface: report.last_original_surface });
        report.stop_reason = 'managed_native_action_not_delivered'; break;
      }
      report.executed_original_inputs++; report.delivered_inputs_with_differences += Number(before > 0);
      basis = await capture();
    }
    report.reached_original_timeline_end = index === timeline.items.length;
    if (report.reached_original_timeline_end) report.stop_reason = Number.isFinite(timeline.stopped_before_sequence)
      ? 'original_journal_boundary' : report.reached_original_terminal ? 'original_natural_terminal_reached' : 'original_recording_end';
    report.last_managed_surface = basis?.snapshot.interaction.kind;
    report.last_managed_status = basis?.snapshot.status;
  } catch (error) { report.error = error.message; report.stop_reason = report.unknown_delivery ? 'unknown_delivery_no_retry' : error.message; }
  finally { report.host_close = await driver.close(); }
  return report;
}
