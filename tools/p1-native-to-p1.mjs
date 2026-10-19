#!/usr/bin/env node
/** Interpret sealed native semantic originals with the shared P1 core.
 * No Host access, native dispatch, Agent Consume/ACK, or Source4-prefix grammar. */
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { classifyNativeCancellation } from './p1-native-cancellation.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');

/** Pure interpretation seam. `source` is returned by the owning bundle reader;
 * this export also permits production-shaped semantic fixtures without disk IO. */
export function convertNativeSemanticOriginals(source, sdk, core, options = {}) {
  const openPrefix = source.manifest.schema === 'sts2.human-annotator/p1-native-prefix-1';
  if (!openPrefix && source.manifest.schema !== 'sts2.human-annotator/p1-native-session-bundle-1')
    throw new Error('native_p1_recording_schema_required');
  const identity = openPrefix ? source.manifest : source.manifest.content_identity;
  const episode = `native-semantic:${identity.session_id}`;
  let state = core.createSession(episode, 'reference');
  const report = { schema: 'sts2.p1/native-semantic-conversion-report-1', source_bundle: source.bundle,
    source_content_id: openPrefix ? null : source.manifest.bundle_content_id,
    source_manifest_sha256: source.manifestSha256 ?? null, source_session_id: identity.session_id,
    origin: 'native_semantic_recording', declared_origin: identity.declared_origin,
    human_origin_attestation: openPrefix ? null : identity.human_origin_attestation,
    scope: { kind: 'bounded_native_semantic_prefix', run_id: options.runId ?? null,
      natural_terminal_observed: false, recording_status: openPrefix ? 'open' : 'closed',
      watermark: openPrefix ? source.manifest.watermark : null }, captures: [], actions: [], issues: [],
    pending_decisions: [], complete: false,
    trajectory: null };
  const active = new Map();
  const continuationParents = new Map();
  const bases = new Map();
  const terminalPhases = new Set(['finished', 'cancelled', 'aborted', 'unknown']);
  // These are native subscription progress phases, not new decisions or results.
  const progressPhases = new Set(['before_execution_resume', 'started', 'paused_for_player_choice', 'ready_to_resume', 'resumed']);
  let lastComplete = null;
  const executionMode = source.executionSchemaVersion === 1;
  const acceptedById = new Map();
  const terminalById = new Map(source.events.filter(row => row.kind === 'lifecycle' && terminalPhases.has(row.phase))
    .map(row => [row.decision_id, row]));
  const executionById = new Map(source.events.filter(row => row.kind === 'execution').map(row => [row.decision_id, row]));
  const cancellations = new Map();
  // Looking at a recorded final disposition classifies an outcome; it never
  // supplies an earlier S, legality or choice from a future state.
  function cancellation(accepted, original, execution = null, executionBasis = null, match = null) {
    if (!executionMode) return null;
    return classifyNativeCancellation({ accepted, humanBasis: original, execution, executionBasis, match,
      terminal: terminalById.get(accepted.decision_id), state, core });
  }

  function basis(reference) {
    if (!reference || typeof reference.payload_ref !== 'string') throw new Error('native_p1_basis_reference_required');
    const bytes = source.bytes(reference.payload_ref);
    if (bytes.length !== reference.byte_count || sha256(bytes) !== reference.sha256)
      throw new Error('native_p1_basis_reference_changed');
    let value = bases.get(reference.sha256);
    if (!value) {
      value = sdk.decodePublicBasis(JSON.parse(bytes.toString('utf8')));
      bases.set(reference.sha256, value);
    }
    if (value.snapshot.snapshot_id !== reference.snapshot_id) throw new Error('native_p1_basis_snapshot_mismatch');
    return { snapshot: value.snapshot, reads: value.reads, capture_status: value.capture_status, missing: value.missing };
  }

  function capture(reference, row, origin, reduction = null, promote = true) {
    if (reference == null) {
      const item = { sequence: row.sequence, event_id: row.event_id, origin, public_basis: null,
        complete: false, missing: [row.basis_missing_reason ?? 'original_public_basis_missing'], observation: null };
      report.captures.push(item);
      return { basis: null, reduction: null, capture: item };
    }
    const value = basis(reference);
    const reduced = reduction ?? core.reduce(state, value);
    const item = { sequence: row.sequence, event_id: row.event_id, origin, public_basis: reference,
      complete: reduced.complete, missing: [...reduced.missing], observation: reduced.observation };
    report.captures.push(item);
    // Only an actual complete later boundary supplies a successor. A native
    // finished callback may itself carry a partial/settling original.
    if (promote && reduced.complete && ['decision', 'terminal'].includes(reduced.observation.status)) {
      lastComplete = item;
      report.scope.natural_terminal_observed ||= reduced.observation.status === 'terminal';
      for (const action of active.values()) if (action.terminal_sequence !== null && action.successor === null
        && row.sequence >= action.terminal_sequence) action.successor = item;
    }
    return { basis: value, reduction: reduced, capture: item };
  }

  const runIds = [...new Set(source.events.filter(row => row.kind === 'decision_accepted')
    .map(row => row.run_id))];
  if (options.runId === undefined && runIds.length > 1) throw new Error('native_p1_select_one_run');
  report.scope.run_id ??= runIds[0] ?? null;
  const acceptedRows = source.events.filter(row => row.kind === 'decision_accepted'
    && row.run_id === report.scope.run_id);

  // Reuse the online lowering's exact declared follow. A later accepted choice
  // can identify a full intention, but never supplies an earlier missing basis.
  function matchRecordedIntention(match, row) {
    if (!['ambiguous', 'continuation_required'].includes(match.status)) return match;
    const following = acceptedRows[acceptedRows.indexOf(row) + 1];
    if (!following?.basis || !following.bound_action) return match;
    const nextBasis = basis(following.basis);
    const nextMatch = core.matchNativeSemanticChoice(state, nextBasis, following);
    if (!['matched', 'ambiguous', 'continuation_required'].includes(nextMatch.status)) return match;
    const matches = Object.entries(match.reduction.lowering).filter(([, lowering]) => {
      if (lowering.first?.bound_action_id !== row.bound_action.bound_action_id
        || lowering.first.verb !== row.bound_action.verb || lowering.follow.length !== 1
        || lowering.follow[0].optional) return false;
      const step = core.resolveFollowBasis(lowering.follow[0], lowering, nextBasis);
      return step?.bound_action_id === following.bound_action.bound_action_id
        && step.verb === following.bound_action.verb;
    });
    if (matches.length !== 1) return match;
    const [choiceId, lowering] = matches[0];
    continuationParents.set(following.decision_id, row.decision_id);
    return { ...match, status: 'matched', reason: 'exact_accepted_span_matches_shared_p1_lowering',
      choice_id: choiceId, lowering, continuation_decision_id: following.decision_id };
  }

  for (const row of source.events) {
    if (row.run_id !== null && row.run_id !== report.scope.run_id) continue;
    if (row.kind === 'decision_accepted') {
      if (active.has(row.decision_id)) throw new Error('native_p1_duplicate_decision');
      acceptedById.set(row.decision_id, row);
      const awaitsExecution = executionMode && row.native_receipt?.kind === 'game_action_accepted';
      const original = row.basis == null ? null : basis(row.basis);
      let match = original && row.bound_action != null ? core.matchNativeSemanticChoice(state, original, row)
        : { status: 'incomplete', reason: original ? 'original_native_bound_action_missing' : 'original_human_basis_missing',
          choice_id: null, lowering: null, reduction: null };
      const continuationOf = continuationParents.get(row.decision_id) ?? null;
      if (!continuationOf && !awaitsExecution) match = matchRecordedIntention(match, row);
      if (awaitsExecution) match = { status: 'incomplete', reason: 'accepted_action_without_execution_original',
        choice_id: null, lowering: null, reduction: null };
      const cancelled = !executionById.has(row.decision_id) ? cancellation(row, original) : null;
      if (cancelled) { cancellations.set(row.decision_id, cancelled);
        match = { ...match, status: 'native_cancelled', reason: cancelled.receipt.reason, choice_id: null, lowering: null }; }
      const captured = capture(row.basis, row, 'human_decision_basis', match.reduction, !awaitsExecution);
      const action = { decision_id: row.decision_id, action_witness_id: row.action_witness_id,
        accepted_event_id: row.event_id, match_status: match.status, reason: match.reason,
        choice_id: match.choice_id, choice: match.reduction?.observation.catalog.find(item => item.choice_id === match.choice_id) ?? null,
        capture: report.captures.length - 1, lifecycle: [], terminal_sequence: null, successor: null,
        native_terminal_sequence: null, execution: null, source_human_basis: row.basis, continuation_of: continuationOf,
        continuation_decision_id: match.continuation_decision_id ?? null,
        result: null, lowering: match.lowering, observation: captured.capture.observation };
      active.set(row.decision_id, action);
      report.actions.push(action);
      if (match.status === 'matched') state = match.reduction.state;
    } else if (row.kind === 'execution') {
      const action = active.get(row.decision_id), accepted = acceptedById.get(row.decision_id);
      if (!executionMode || !action || action.execution !== null || action.native_terminal_sequence !== null
        || action.action_witness_id !== row.action_witness_id || row.run_id !== accepted.run_id)
        throw new Error('native_p1_execution_binding_mismatch');
      const original = row.basis == null ? null : basis(row.basis);
      const match = original ? core.matchNativeExecutionChoice(state, original, accepted, row)
        : { status: 'incomplete', reason: 'original_execution_basis_missing', choice_id: null, lowering: null, reduction: null };
      const cancelled = cancellation(accepted, accepted.basis ? basis(accepted.basis) : null, row, original, match);
      if (cancelled) cancellations.set(row.decision_id, cancelled);
      const projected = cancelled?.checkpoint ?? match.reduction;
      const captured = capture(row.basis, row, 'native_before_execution', projected);
      Object.assign(action, { execution: { event_id: row.event_id, queued: row.queued,
          queue_interval_ms: row.queue_interval_ms, basis: row.basis },
        match_status: cancelled ? 'native_cancelled' : match.status, reason: cancelled?.receipt.reason ?? match.reason,
        choice_id: cancelled ? null : match.choice_id,
        choice: cancelled ? null : match.reduction?.observation.catalog.find(item => item.choice_id === match.choice_id) ?? null,
        capture: report.captures.length - 1, lowering: cancelled ? null : match.lowering, observation: captured.capture.observation });
      if (cancelled?.checkpoint) state = cancelled.checkpoint.state;
      else if (match.status === 'matched') state = match.reduction.state;
    } else if (row.kind === 'lifecycle') {
      const action = active.get(row.decision_id);
      if (!action || action.action_witness_id !== row.action_witness_id)
        throw new Error('native_p1_lifecycle_binding_mismatch');
      if (!progressPhases.has(row.phase) && !terminalPhases.has(row.phase)) throw new Error('native_p1_unknown_lifecycle_phase');
      if (action.native_terminal_sequence !== null) throw new Error('native_p1_lifecycle_after_terminal');
      action.lifecycle.push({ event_id: row.event_id, phase: row.phase,
        successor_missing_reason: row.successor_missing_reason ?? null });
      if (terminalPhases.has(row.phase)) {
        action.native_terminal_sequence = row.sequence;
        if (!action.continuation_decision_id) action.terminal_sequence = row.sequence;
        if (action.lowering && row.phase === 'finished' && !action.continuation_of && !action.continuation_decision_id)
          state = core.applyCommitment(state, action.lowering, true);
        action.result = { schema: 'sts2.p1/native-semantic-result-1', occurrence: action.observation?.envelope.occurrence ?? null,
          choice_id: action.choice_id, status: row.phase === 'finished' ? 'completed'
            : row.phase === 'unknown' ? 'unknown' : 'interrupted_known',
          steps: [{ index: 0, origin: 'native_semantic_choice', native_verb: action.lowering?.first?.verb ?? action.choice?.kind ?? null,
            label: action.lowering?.first?.label ?? action.choice?.label ?? null, delivery: 'delivered',
            reason_code: row.phase === 'finished' ? null : `native_action_${row.phase}` }], commitment_delta: null };
        const parent = action.continuation_of ? active.get(action.continuation_of)
          : action.continuation_decision_id ? action : null;
        const child = parent && active.get(parent.continuation_decision_id);
        if (parent?.native_terminal_sequence !== null && child?.native_terminal_sequence != null) {
          parent.terminal_sequence = Math.max(parent.native_terminal_sequence, child.native_terminal_sequence);
          if (parent.result?.status === 'completed' && child.result?.status === 'completed')
            state = core.applyCommitment(state, parent.lowering, true);
        }
      }
      if (row.successor_basis) capture(row.successor_basis, row, `native_lifecycle_${row.phase}`);
    } else if (row.kind === 'observation') capture(row.basis, row, row.reason);
    else if (row.kind === 'issue') report.issues.push(row);
    else throw new Error(`native_p1_unknown_event_kind:${row.kind}`);
  }

  report.native_action_count = report.actions.length;
  for (const action of report.actions) if (action.continuation_decision_id) {
    const child = active.get(action.continuation_decision_id);
    action.native_continuations = [{ decision_id: child.decision_id, action_witness_id: child.action_witness_id,
      accepted_event_id: child.accepted_event_id, capture: child.capture, lifecycle: child.lifecycle,
      result: child.result }];
    if (!action.result || !child.result) action.result = null;
    else action.result = { ...action.result,
      status: [action.result, child.result].some(value => value.status === 'unknown') ? 'unknown'
        : [action.result, child.result].every(value => value.status === 'completed') ? 'completed' : 'interrupted_known',
      steps: [...action.result.steps, ...child.result.steps].map((step, index) => ({ ...step, index })) };
  }
  const nativeActions = [...report.actions];
  const derivedNative = nativeActions.filter(action => action.result?.status === 'completed'
    && (action.match_status === 'matched'
      || action.continuation_of && active.get(action.continuation_of)?.match_status === 'matched')).length;
  // Actual execution capture order, including same-boundary UI choices, owns P1.
  report.actions = report.actions.filter(action => !action.continuation_of)
    .sort((a, b) => report.captures[a.capture].sequence - report.captures[b.capture].sequence);
  const successful = report.actions.filter(action => action.match_status === 'matched' && action.result?.status === 'completed');
  const pointActions = [...successful.map(action => ({ action, capture: report.captures[action.capture] })),
    ...(lastComplete ? [{ action: null, capture: lastComplete }] : [])];
  const nativeCancellations = [];
  for (const action of report.actions) {
    const cancelled = cancellations.get(action.decision_id);
    if (terminalById.get(action.decision_id)?.phase === 'cancelled') {
      action.choice_id = null; action.choice = null;
    }
    if (cancelled) {
      action.native_disposition = cancelled.receipt;
      action.choice_id = null; action.choice = null; // A cancellation never yields a successful label.
      report.issues.push({ decision_id: action.decision_id, action_witness_id: action.action_witness_id,
        reason: cancelled.receipt.reason, disposition: 'native_cancelled', blocking: false });
      const before = report.captures[action.capture];
      const beforePoint = pointActions.findIndex(point => point.capture.sequence >= before.sequence);
      const afterPoint = pointActions.findIndex(point => point.capture.sequence >= cancelled.receipt.terminal_sequence);
      if (beforePoint < 0 || afterPoint < 0) (openPrefix ? report.pending_decisions : report.issues)
        .push({ decision_id: action.decision_id, reason: 'native_cancellation_without_complete_continuity_boundary', blocking: true });
      else {
        const checks = [{ boundary: 'before', capture: before, comparison_point_index: beforePoint },
          { boundary: 'after', capture: pointActions[afterPoint].capture, comparison_point_index: afterPoint }]
          .map(({ boundary, capture, comparison_point_index }) => ({ boundary,
            source_event_id: capture.event_id, source_public_basis: capture.public_basis,
            comparison_point_index, observation: capture.observation }));
        nativeCancellations.push({ ...cancelled.receipt, checks,
          continuity_checkpoints: checks.map(({ observation: _observation, ...checkpoint }) => checkpoint) });
      }
    } else {
      if (action.terminal_sequence === null || action.successor === null) {
        const reason = action.terminal_sequence === null ? 'accepted_action_without_terminal_disposition'
          : 'accepted_action_without_complete_successor';
        (openPrefix ? report.pending_decisions : report.issues).push({ decision_id: action.decision_id, reason });
      }
      if (action.match_status !== 'matched') report.issues.push({ decision_id: action.decision_id, reason: action.reason });
      if (action.result?.status === 'unknown') report.issues.push({ decision_id: action.decision_id, reason: 'accepted_action_unknown' });
      if (action.result?.status === 'interrupted_known') report.issues.push({ decision_id: action.decision_id,
        reason: 'native_interruption_without_qualified_cancellation' });
    }
    delete action.lowering;
    delete action.observation;
  }
  // Original issues stay visible. Only the exact issues explained by an exact
  // cancelled carrier are nonblocking; no per-type/nearby-event forgiveness.
  report.issues = report.issues.map(issue => {
    if (issue.blocking !== undefined) return issue;
    const decision = issue.decision_id ?? issue.native_witness?.decision_id;
    const witness = issue.action_witness_id ?? issue.native_witness?.action_witness_id;
    const cancelled = decision ? cancellations.get(decision)
      : [...cancellations.values()].find(item => item.receipt.action_witness_id === witness);
    const explained = cancelled && (!witness || cancelled.receipt.action_witness_id === witness)
      && cancelled.expected_source_issues.includes(issue.reason);
    return { ...issue, blocking: !explained, ...(explained ? { disposition: 'native_cancelled' } : {}) };
  });
  report.blocking_issues = report.issues.filter(issue => issue.blocking);
  report.native_cancellations = nativeCancellations.map(({ checks: _checks, ...receipt }) => receipt);
  report.accepted_accounting = { accepted: report.native_action_count, derived: derivedNative,
    native_cancelled: cancellations.size, explained_gaps: report.native_action_count - derivedNative - cancellations.size };
  report.complete = successful.length > 0 && report.blocking_issues.length === 0 && report.pending_decisions.length === 0;
  const journal = source.journal.filter(row => row.run_id === report.scope.run_id);
  const starts = journal.filter(row => row.kind === 'run_started_native');
  const ends = journal.filter(row => row.kind === 'run_ended_native');
  report.whole_run_recording_complete = report.complete && !openPrefix && starts.length === 1 && ends.length === 1
    && starts[0].sequence < ends[0].sequence && report.scope.natural_terminal_observed
    && !journal.some(row => ['run_resumed_native', 'run_abandoned_native', 'run_ended_unproved', 'recording_paused'].includes(row.kind));
  report.strict_managed_comparison = 'not_run';
  if (report.complete) {
    const points = pointActions.map(({ action, capture }, index) => ({ index,
      observation: capture.observation, chosen: action?.choice_id ?? null,
      ...(nativeCancellations.some(item => item.checks.some(check => check.comparison_point_index === index)) ? {
        continuity_checks: nativeCancellations.flatMap(item => item.checks
          .filter(check => check.comparison_point_index === index)
          .map(({ boundary, source_event_id, observation }) => ({ decision_id: item.decision_id,
            phase: item.phase, boundary, source_event_id, observation })))
      } : {}) }));
    report.trajectory = { schema: 'sts2.p1/trajectory-1', run_directory: source.bundle,
      origin: 'native_semantic_recording', scope: report.scope,
      cancellation_count: report.native_cancellations.length, native_cancellations: report.native_cancellations, points };
  }
  return report;
}

export async function convertP1NativeBundle(options) {
  const { readVerifiedP1NativeBundle, readVerifiedP1NativePrefix } = await import('./p1-native-bundle.mjs');
  if (Boolean(options.prefix) === Boolean(options.bundle)) throw new Error('select_one_native_p1_bundle_or_prefix');
  const source = options.prefix ? readVerifiedP1NativePrefix(options.prefix) : readVerifiedP1NativeBundle(options.bundle);
  if (options.prefix && (!/^[a-f0-9]{64}$/.test(options.manifestSha256 ?? '')
    || source.manifestSha256 !== options.manifestSha256)) throw new Error('native_p1_prefix_command_receipt_digest_required');
  const sdkRoot = resolve(options.sdkRoot ?? resolve(root, 'components/connector/sdk/typescript/dist'));
  const core = await import(pathToFileURL(resolve(sdkRoot, 'p1Global/index.js')).href);
  const sdk = await import(pathToFileURL(resolve(sdkRoot, 'index.js')).href);
  return convertNativeSemanticOriginals(source, sdk, core, options);
}

async function main() {
  const args = process.argv.slice(2), options = {};
  for (let i = 0; i < args.length; i += 2) {
    const keys = { '--bundle': 'bundle', '--prefix': 'prefix', '--manifest-sha256': 'manifestSha256',
      '--sdk-root': 'sdkRoot', '--run-id': 'runId', '--output': 'output' };
    if (!keys[args[i]] || !args[i + 1]) throw new Error('usage: --bundle PATH | --prefix PATH --manifest-sha256 SHA [--sdk-root PATH] [--run-id ID] [--output PATH]');
    options[keys[args[i]]] = args[i + 1];
  }
  const report = await convertP1NativeBundle(options);
  const serialized = JSON.stringify(report, null, 2) + '\n';
  if (options.output) writeFileSync(options.output, serialized, { flag: 'wx' });
  else process.stdout.write(serialized);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  main().catch(error => { process.stderr.write(`${error.stack ?? error}\n`); process.exitCode = 1; });
