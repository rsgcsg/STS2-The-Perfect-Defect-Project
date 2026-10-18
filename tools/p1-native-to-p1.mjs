#!/usr/bin/env node
/** Interpret sealed native semantic originals with the shared P1 core.
 * No Host access, native dispatch, Agent Consume/ACK, or Source4-prefix grammar. */
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');

/** Pure interpretation seam. `source` is returned by the owning bundle reader;
 * this export also permits production-shaped semantic fixtures without disk IO. */
export function convertNativeSemanticOriginals(source, sdk, core, options = {}) {
  const identity = source.manifest.content_identity;
  const episode = `native-semantic:${identity.session_id}`;
  let state = core.createSession(episode, 'reference');
  const report = { schema: 'sts2.p1/native-semantic-conversion-report-1', source_bundle: source.bundle,
    source_content_id: source.manifest.bundle_content_id, source_session_id: identity.session_id,
    origin: 'native_semantic_recording', declared_origin: identity.declared_origin,
    human_origin_attestation: identity.human_origin_attestation,
    scope: { kind: 'bounded_native_semantic_prefix', run_id: options.runId ?? null,
      natural_terminal_observed: false }, captures: [], actions: [], issues: [], complete: false,
    trajectory: null };
  const active = new Map();
  const bases = new Map();
  const terminalPhases = new Set(['finished', 'cancelled', 'aborted', 'unknown']);
  let lastComplete = null;

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
    return { snapshot: value.snapshot, reads: value.reads };
  }

  function capture(reference, row, origin, reduction = null) {
    if (reference === null) {
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
    if (reduced.complete && ['decision', 'terminal'].includes(reduced.observation.status)) {
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

  for (const row of source.events) {
    if (row.run_id !== null && row.run_id !== report.scope.run_id) continue;
    if (row.kind === 'decision_accepted') {
      if (active.has(row.decision_id)) throw new Error('native_p1_duplicate_decision');
      const original = row.basis === null ? null : basis(row.basis);
      const match = original && row.bound_action !== null ? core.matchNativeSemanticChoice(state, original, row)
        : { status: 'incomplete', reason: original ? 'original_native_bound_action_missing' : 'original_human_basis_missing',
          choice_id: null, lowering: null, reduction: null };
      const captured = capture(row.basis, row, 'human_decision_basis', match.reduction);
      const action = { decision_id: row.decision_id, action_witness_id: row.action_witness_id,
        accepted_event_id: row.event_id, match_status: match.status, reason: match.reason,
        choice_id: match.choice_id, choice: match.reduction?.observation.catalog.find(item => item.choice_id === match.choice_id) ?? null,
        capture: report.captures.length - 1, lifecycle: [], terminal_sequence: null, successor: null,
        result: null, lowering: match.lowering, observation: captured.capture.observation };
      active.set(row.decision_id, action);
      report.actions.push(action);
      if (match.status === 'matched') state = match.reduction.state;
    } else if (row.kind === 'lifecycle') {
      const action = active.get(row.decision_id);
      if (!action || action.action_witness_id !== row.action_witness_id)
        throw new Error('native_p1_lifecycle_binding_mismatch');
      if (!['started', ...terminalPhases].includes(row.phase)) throw new Error('native_p1_unknown_lifecycle_phase');
      if (action.terminal_sequence !== null) throw new Error('native_p1_lifecycle_after_terminal');
      action.lifecycle.push({ event_id: row.event_id, phase: row.phase,
        successor_missing_reason: row.successor_missing_reason ?? null });
      if (terminalPhases.has(row.phase)) {
        action.terminal_sequence = row.sequence;
        if (action.lowering && row.phase === 'finished') state = core.applyCommitment(state, action.lowering, true);
        action.result = { schema: 'sts2.p1/native-semantic-result-1', occurrence: action.observation?.envelope.occurrence ?? null,
          choice_id: action.choice_id, status: row.phase === 'finished' ? 'completed'
            : row.phase === 'unknown' ? 'unknown' : 'interrupted_known',
          steps: [{ index: 0, origin: 'native_semantic_choice', native_verb: action.lowering?.first?.verb ?? null,
            label: action.lowering?.first?.label ?? null, delivery: 'delivered',
            reason_code: row.phase === 'finished' ? null : `native_action_${row.phase}` }], commitment_delta: null };
      }
      if (row.successor_basis) capture(row.successor_basis, row, `native_lifecycle_${row.phase}`);
    } else if (row.kind === 'observation') capture(row.basis, row, row.reason);
    else if (row.kind === 'issue') report.issues.push(row);
    else throw new Error(`native_p1_unknown_event_kind:${row.kind}`);
  }

  for (const action of report.actions) {
    if (action.terminal_sequence === null) report.issues.push({ decision_id: action.decision_id,
      reason: 'accepted_action_without_terminal_disposition' });
    if (action.successor === null) report.issues.push({ decision_id: action.decision_id,
      reason: 'accepted_action_without_complete_successor' });
    if (action.match_status !== 'matched') report.issues.push({ decision_id: action.decision_id, reason: action.reason });
    if (action.result?.status === 'unknown') report.issues.push({ decision_id: action.decision_id,
      reason: 'accepted_action_unknown' });
    delete action.lowering;
    delete action.observation;
  }
  report.complete = report.actions.length > 0 && report.issues.length === 0;
  if (report.complete) {
    const points = report.actions.map((action, index) => ({ index,
      observation: report.captures[action.capture].observation, chosen: action.choice_id }));
    points.push({ index: points.length, observation: lastComplete.observation, chosen: null });
    report.trajectory = { schema: 'sts2.p1/trajectory-1', run_directory: source.bundle,
      origin: 'native_semantic_recording', scope: report.scope, points };
  }
  return report;
}

export async function convertP1NativeBundle(options) {
  const { readVerifiedP1NativeBundle } = await import('./p1-native-bundle.mjs');
  const source = readVerifiedP1NativeBundle(options.bundle);
  const sdkRoot = resolve(options.sdkRoot ?? resolve(root, 'components/connector/sdk/typescript/dist'));
  const core = await import(pathToFileURL(resolve(sdkRoot, 'p1Global/index.js')).href);
  const sdk = await import(pathToFileURL(resolve(sdkRoot, 'index.js')).href);
  return convertNativeSemanticOriginals(source, sdk, core, options);
}

async function main() {
  const args = process.argv.slice(2), options = {};
  for (let i = 0; i < args.length; i += 2) {
    const keys = { '--bundle': 'bundle', '--sdk-root': 'sdkRoot', '--run-id': 'runId', '--output': 'output' };
    if (!keys[args[i]] || !args[i + 1]) throw new Error('usage: --bundle PATH [--sdk-root PATH] [--run-id ID] [--output PATH]');
    options[keys[args[i]]] = args[i + 1];
  }
  if (!options.bundle) throw new Error('native_p1_bundle_required');
  const report = await convertP1NativeBundle(options);
  const serialized = JSON.stringify(report, null, 2) + '\n';
  if (options.output) writeFileSync(options.output, serialized, { flag: 'wx' });
  else process.stdout.write(serialized);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  main().catch(error => { process.stderr.write(`${error.stack ?? error}\n`); process.exitCode = 1; });
