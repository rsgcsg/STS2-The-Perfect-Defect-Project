import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as sdk from '../../components/connector/sdk/typescript/dist/index.js';
import * as core from '../../components/connector/sdk/typescript/dist/p1Global/index.js';
import { convertNativeSemanticOriginals } from '../p1-native-to-p1.mjs';
import { rewardForwardPair } from '../../components/connector/sdk/typescript/test/fixtures/reward-forward.mjs';

// Semantic-only fixture: actual Store/packer bytes are tested at the reader seam.
// These bases exercise shared reduction and action bindings, not native gameplay.
function example() {
  const original = JSON.parse(readFileSync(new URL('../../components/connector/sdk/typescript/test/fixtures/'
    + 'p1-global/synthetic-complete-combat.json', import.meta.url), 'utf8'));
  const blobs = new Map();
  const events = [];
  function retain(basis) {
    const value = { schema: 'sts2.player-environment/public-basis-1', revision: '1',
      capture_scope: 'current_public_snapshot_and_advertised_read_instances', capture_status: 'complete',
      ...structuredClone(basis), missing: [] };
    const bytes = Buffer.from(JSON.stringify(value));
    const sha256 = createHash('sha256').update(bytes).digest('hex');
    const payload_ref = `public-bases/sha256/${sha256.slice(0, 2)}/${sha256}.json`;
    blobs.set(payload_ref, bytes);
    return { snapshot_id: value.snapshot.snapshot_id, sha256, byte_count: bytes.length, payload_ref };
  }
  function nextBasis(suffix, settling = false) {
    const basis = structuredClone(original);
    basis.snapshot.snapshot_id += suffix;
    basis.snapshot.sequence++;
    for (const read of basis.reads)
      read.response.expected_snapshot_id = read.response.observed_snapshot_id = basis.snapshot.snapshot_id;
    if (settling) {
      basis.snapshot.status = 'settling';
      basis.snapshot.interaction.stage = 'settling';
      basis.snapshot.bound_actions.actions = [];
      basis.snapshot.bound_actions.total_count = basis.snapshot.bound_actions.materialized_count = 0;
      basis.snapshot.interaction.content.surface.can_end_turn = false;
      basis.snapshot.interaction.content.surface.playable_cards = [];
    }
    return basis;
  }
  function row(kind, fields) {
    const sequence = events.length + 1;
    const event = { schema: 'sts2.human-annotator/p1-native-event-1', sequence,
      session_id: 'test-session', timeline_id: 'test-timeline', run_id: 'run-1',
      event_id: `event-${sequence}`, recorded_at: '2026-10-18T15:00:00Z', kind, ...fields };
    events.push(event); return event;
  }
  const first = retain(original), afterPlayBasis = nextBasis('-after-play'), afterPlay = retain(afterPlayBasis);
  const settling = retain(nextBasis('-settling', true)), afterTurn = retain(nextBasis('-turn-2'));
  row('observation', { basis: first, reason: 'native_decision_owner_ready' });
  function accept(id, basis, reference, verb) {
    const action = basis.snapshot.bound_actions.actions.find(action => action.verb === verb);
    assert.ok(action, `fixture needs ${verb}`);
    return row('decision_accepted', { decision_id: id, action_witness_id: `witness-${id}`,
      native_action_type: verb, native_queue_id: `queue-${id}`, native_mechanism: 'game_action', basis: reference,
      bound_action: { bound_action_id: action.bound_action_id, verb: action.verb,
        subject_referent_id: action.subject_referent_id ?? null, arguments: action.arguments },
      native_receipt: { kind: 'game_action_accepted', carrier_ref: `witness-${id}` } });
  }
  accept('play', original, first, 'play');
  row('lifecycle', { decision_id: 'play', action_witness_id: 'witness-play', phase: 'started' });
  row('lifecycle', { decision_id: 'play', action_witness_id: 'witness-play', phase: 'finished', successor_basis: afterPlay });
  accept('end-turn', afterPlayBasis, afterPlay, 'end_turn');
  row('lifecycle', { decision_id: 'end-turn', action_witness_id: 'witness-end-turn', phase: 'finished',
    successor_basis: settling, successor_missing_reason: null });
  row('observation', { basis: afterTurn, reason: 'native_decision_owner_ready' });
  const source = { bundle: '/synthetic-native-semantic-bundle', manifest: {
    schema: 'sts2.human-annotator/p1-native-session-bundle-1', bundle_content_id: 'synthetic',
    content_identity: { session_id: 'test-session', declared_origin: 'agent_native_ui', human_origin_attestation: { attested: false } } }, events,
    journal: [], bytes: path => blobs.get(path) };
  return { source, blobs, original, retain, nextBasis, row };
}

test('two accepted decisions derive without any record for unsubmitted pickup/cancel', () => {
  const { source } = example();
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true);
  assert.equal(report.projection_claims.lossless_p1_trace, 'not_established');
  assert.deepEqual(report.actions.map(action => action.choice.kind), ['combat.play_card', 'combat.end_turn']);
  assert.deepEqual(report.actions.map(action => action.result.status), ['completed', 'completed']);
  assert.equal(report.actions[1].successor.origin, 'native_decision_owner_ready');
  assert.equal(report.trajectory.points.length, 3);
  assert.equal(report.trajectory.origin, 'native_semantic_recording');
  assert.equal(report.scope.natural_terminal_observed, false);
  assert.equal(report.human_origin_attestation.attested, false);
  assert.ok(report.captures.some(capture => capture.observation.status !== 'decision' && !capture.complete));
});

test('accepted alone never becomes completed and does not yield an eligible trajectory', () => {
  const { source } = example();
  source.events = source.events.slice(0, 2);
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, false);
  assert.equal(report.actions[0].result, null);
  assert.equal(report.trajectory, null);
  assert.ok(report.issues.some(issue => issue.reason === 'accepted_action_without_terminal_disposition'));
});

test('a mechanical diagnostic with explicit absent basis creates no decision or successor', () => {
  const { source } = example();
  source.events.push({ ...source.events.at(-1), sequence: 8, event_id: 'diagnostic-8',
    basis: null, reason: 'native_automatic_transition',
    basis_missing_reason: 'native_diagnostic_not_decision_boundary' });
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true);
  assert.equal(report.actions.length, 2);
  assert.equal(report.trajectory.points.length, 3);
  assert.equal(report.captures.at(-1).observation, null);
  assert.deepEqual(report.captures.at(-1).missing, ['native_diagnostic_not_decision_boundary']);
  assert.equal(report.actions[1].successor.origin, 'native_decision_owner_ready');
});

test('a finished callback with only settling original still needs a later complete successor', () => {
  const { source } = example();
  source.events.pop();
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, false);
  assert.equal(report.actions[1].result.status, 'completed');
  assert.equal(report.actions[1].successor, null);
  assert.ok(report.issues.some(issue => issue.reason === 'accepted_action_without_complete_successor'));
});

test('a native unknown is retained even when a later complete public observation exists', () => {
  const { source } = example();
  source.events[5].phase = 'unknown';
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, false);
  assert.equal(report.actions[1].result.status, 'unknown');
  assert.equal(report.trajectory, null);
});

test('native PlayerChoice progress remains lifecycle of its accepted parent', () => {
  const { source } = example();
  const parent = source.events[3];
  source.events.splice(3, 0, ...['paused_for_player_choice', 'ready_to_resume', 'resumed'].map(phase =>
    ({ ...parent, phase, successor_basis: null, successor_missing_reason: 'native_lifecycle_not_terminal' })));
  source.events.forEach((event, index) => { event.sequence = index + 1; event.event_id = `event-${index + 1}`; });
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true);
  assert.equal(report.actions.length, 2);
  assert.deepEqual(report.actions[0].lifecycle.map(item => item.phase),
    ['started', 'paused_for_player_choice', 'ready_to_resume', 'resumed', 'finished']);
  assert.equal(report.actions[0].result.status, 'completed');
});

test('an unsupported real native UI choice is an explicit gap', () => {
  const { source } = example();
  source.events.push({ ...source.events.at(-1), sequence: 8, event_id: 'event-8', kind: 'issue',
    reason: 'unsupported_native_ui_decision', native_action_type: 'ChooseEventOption' });
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, false);
  assert.equal(report.issues[0].reason, 'unsupported_native_ui_decision');
});

test('changing retained basis bytes after sealing is refused', () => {
  const { source, blobs } = example();
  const path = source.events[0].basis.payload_ref;
  blobs.set(path, Buffer.concat([blobs.get(path), Buffer.from(' ')]));
  assert.throws(() => convertNativeSemanticOriginals(source, sdk, core), /basis_reference_changed/u);
});

test('an open durable prefix preserves pending acceptance without closing or inventing unknown', () => {
  const { source } = example();
  source.events = source.events.slice(0, 2);
  source.manifest = { ...source.manifest.content_identity, schema: 'sts2.human-annotator/p1-native-prefix-1',
    status: 'open', scope: 'durable_prefix', watermark: { event_count: 2, accepted_count: 1, pending_count: 1 } };
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, false);
  assert.equal(report.scope.recording_status, 'open');
  assert.equal(report.human_origin_attestation, null);
  assert.equal(report.actions[0].result, null);
  assert.equal(report.pending_decisions.length, 1);
  assert.deepEqual(report.issues, []);
});

function nativeRewardSpan() {
  const { outer, child } = rewardForwardPair();
  const { source, blobs } = example();
  source.events = [];
  const retain = basis => {
    const bytes = Buffer.from(JSON.stringify({ schema: 'sts2.player-environment/public-basis-1', revision: '1',
      capture_scope: 'current_public_snapshot_and_advertised_read_instances', capture_status: 'complete', missing: [], ...basis }));
    const sha256 = createHash('sha256').update(bytes).digest('hex');
    const payload_ref = `public-bases/sha256/${sha256.slice(0, 2)}/${sha256}.json`;
    blobs.set(payload_ref, bytes);
    return { snapshot_id: basis.snapshot.snapshot_id, sha256, byte_count: bytes.length, payload_ref };
  };
  const add = (kind, fields) => {
    const sequence = source.events.length + 1;
    source.events.push({ kind, sequence, event_id: `reward-event-${sequence}`, run_id: 'run-1', ...fields });
  };
  const accept = (id, basis, actionId) => add('decision_accepted', { decision_id: id, action_witness_id: `native-${id}`,
    basis: retain(basis), bound_action: basis.snapshot.bound_actions.actions.find(action => action.bound_action_id === actionId),
    native_receipt: { kind: 'semantic_ui_accepted', carrier_ref: `native-${id}` } });
  accept('open', outer, 'open');
  add('lifecycle', { decision_id: 'open', action_witness_id: 'native-open', phase: 'finished', successor_basis: retain(child) });
  accept('select', child, 'select-0');
  add('lifecycle', { decision_id: 'select', action_witness_id: 'native-select', phase: 'finished', successor_basis: retain(outer) });
  return { source, child, retain };
}

test('native reward open and exact accepted selection use the existing full P1 intention', () => {
  const { source } = nativeRewardSpan();
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true);
  assert.equal(report.native_action_count, 2);
  assert.equal(report.actions.length, 1);
  assert.equal(report.actions[0].choice.kind, 'reward.take_card');
  assert.equal(report.actions[0].result.steps.length, 2);
  assert.equal(report.actions[0].native_continuations[0].decision_id, 'select');
  assert.equal(report.actions[0].terminal_sequence, 4);
  assert.equal(report.actions[0].successor.sequence, 4);
  assert.equal(report.trajectory.points.length, 2);
});

test('native reward opening never guesses a card without its original accepted selection', () => {
  const { source } = nativeRewardSpan();
  source.events = source.events.slice(0, 2);
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, false);
  assert.equal(report.actions[0].choice_id, null);
});

test('native reward continuation cannot borrow a foreign original owner or receipt', () => {
  for (const defect of ['owner', 'receipt']) {
    const { source, child, retain } = nativeRewardSpan();
    if (defect === 'owner') {
      child.snapshot.interaction.content.surface.reward_set_id = 'foreign-reward';
      source.events[2].basis = retain(child);
    } else source.events[2].native_receipt.carrier_ref = 'foreign-native-action';
    const report = convertNativeSemanticOriginals(source, sdk, core);
    assert.equal(report.complete, false);
    assert.equal(report.actions[0].choice_id, null);
  }
});

test('unknown native selection cannot become a completed reward intention', () => {
  const { source } = nativeRewardSpan();
  source.events[3].phase = 'unknown';
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, false);
  assert.equal(report.actions[0].result.status, 'unknown');
  assert.equal(report.trajectory, null);
});

test('an open reward prefix retains its unfinished accepted continuation', () => {
  const { source } = nativeRewardSpan();
  source.events.pop();
  source.manifest = { ...source.manifest.content_identity, schema: 'sts2.human-annotator/p1-native-prefix-1',
    status: 'open', scope: 'durable_prefix', watermark: { accepted_count: 2, terminal_count: 1, pending_count: 1 } };
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, false);
  assert.equal(report.actions[0].result, null);
  assert.equal(report.actions[0].terminal_sequence, null);
  assert.ok(report.pending_decisions.some(item => item.decision_id === 'open'));
});

function queuedExample() {
  const fixture = example(), { source, original, retain, nextBasis, row } = fixture;
  source.events.length = 0;
  source.executionSchemaVersion = 1;
  const h = retain(nextBasis('-H', true)), s1 = retain(nextBasis('-S1', true)), s2 = retain(nextBasis('-S2', true));
  const actions = original.snapshot.bound_actions.actions.map((action, index) => ({ key: `native-${index}`,
    verb: action.verb, subject_referent_id: action.subject_referent_id ?? null,
    arguments: Object.fromEntries(action.arguments.map(arg => [arg.role, arg.referent_id])), native_legality_basis: 'native-exact-S' }));
  function accepted(id, index) {
    const selected = actions[index];
    return row('decision_accepted', { decision_id: id, action_witness_id: `witness-${id}`, basis: h,
      bound_action: null, native_input: { action_key: selected.key, verb: selected.verb,
        subject_referent_id: selected.subject_referent_id, arguments: structuredClone(selected.arguments) },
      native_receipt: { kind: 'game_action_accepted', carrier_ref: `witness-${id}` } });
  }
  function execution(accepted, basis) {
    return row('execution', { decision_id: accepted.decision_id, action_witness_id: accepted.action_witness_id,
      phase: 'before_execution', basis, execution_snapshot_id: basis.snapshot_id,
      missing_reason: null, queued: true, queue_interval_ms: 120,
      native_input: structuredClone(accepted.native_input), bound_action: null, execution_action_space: {
        schema: 'sts2.human-annotator/execution-semantic-action-space-3', phase: 'before_execution', status: 'captured',
        action_witness_id: accepted.action_witness_id, actions, observed_action_key: accepted.native_input.action_key,
        human_native_action_key: accepted.native_input.action_key, observed_membership: 'exact_once', observed_match_count: 1 } });
  }
  row('observation', { basis: retain(original), reason: 'native_decision_owner_ready' });
  const first = accepted('first', 0);
  execution(first, s1);
  const second = accepted('second', 1);
  row('lifecycle', { decision_id: 'first', action_witness_id: 'witness-first', phase: 'finished', successor_basis: h });
  const secondExecution = execution(second, s2);
  row('lifecycle', { decision_id: 'second', action_witness_id: 'witness-second', phase: 'finished',
    successor_basis: retain(nextBasis('-after')) });
  return { ...fixture, first, second, secondExecution };
}

test('turn-start and in-card queue derive at execution S while H stays provenance and next S is a successor', () => {
  const { source } = queuedExample();
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true);
  assert.deepEqual(report.accepted_accounting, { accepted: 2, derived: 2, native_cancelled: 0, presentation: 0, presentation_exceptions: 0, explained_gaps: 0 });
  assert.ok(report.actions.every(action => action.execution.queued && action.execution.queue_interval_ms === 120));
  assert.ok(report.actions.every(action => action.source_human_basis.snapshot_id !== action.execution.basis.snapshot_id));
  assert.equal(report.actions[0].successor.origin, 'native_before_execution');
  assert.ok(report.actions.every(action => report.captures[action.capture].origin === 'native_before_execution'));
  assert.equal(report.trajectory.points.length, 3);
});

test('the recorded UI-filter producer cannot qualify a known incomplete execution catalog', () => {
  const { source, secondExecution, nextBasis, retain } = queuedExample();
  const original = nextBasis('-queued-potion', true);
  const potionFixture = JSON.parse(readFileSync(new URL('../../components/connector/sdk/typescript/test/fixtures/'
    + 'p1-global/managed-deck_enchant_selection.json', import.meta.url), 'utf8'));
  const potion = structuredClone(potionFixture.snapshot.persistent.content.player.potions[0]);
  original.snapshot.persistent.content.player.potions = [potion];
  secondExecution.basis = retain(original);
  secondExecution.execution_snapshot_id = secondExecution.basis.snapshot_id;
  Object.assign(secondExecution.execution_action_space, { scope: 'combat_play_phase',
    native_evidence: ['P1:CombatTurnSurfaceReader.CanDiscardPotion+exact_current_enabled_potion_holder'] });
  const unchanged = structuredClone(secondExecution);
  const report = convertNativeSemanticOriginals(source, sdk, core, { includeSegments: true });
  assert.equal(report.complete, false);
  assert.equal(report.trajectory, null);
  assert.ok(report.blocking_issues.some(issue => issue.decision_id === secondExecution.decision_id
    && issue.reason === 'original_execution_catalog_filtered_by_ui_queue_gate'
    && issue.missing_discard_subjects.includes(potion.entity_id)));
  assert.ok(report.segment_output.gaps.some(gap => gap.decision_ids.includes(secondExecution.decision_id)));
  assert.ok(report.segment_output.segments.every(segment => segment.points.every(point => point.source_decision_id !== secondExecution.decision_id)));
  assert.deepEqual(secondExecution, unchanged);
});

test('a complete successful S cannot fill an accepted source H that was never captured', () => {
  const { source, second } = queuedExample();
  second.basis = null;
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, false);
  assert.equal(report.trajectory, null);
  assert.ok(report.blocking_issues.some(issue => issue.decision_id === second.decision_id
    && issue.reason === 'accepted_input_source_information_missing'));
});

for (const failure of ['target-death', 'operands-changed', 'missing-execution']) test(`queued ${failure} remains an explained gap`, () => {
  const { source, secondExecution } = queuedExample();
  if (failure === 'target-death') secondExecution.execution_action_space.observed_match_count = 0;
  if (failure === 'operands-changed') secondExecution.native_input.arguments = { target: 'changed-target' };
  if (['cancel', 'missing-execution'].includes(failure)) source.events = source.events.filter(row => row !== secondExecution);
  if (failure === 'cancel') source.events.at(-1).phase = 'cancelled';
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, false);
  assert.equal(report.trajectory, null);
  assert.equal(report.accepted_accounting.accepted, 2);
  assert.equal(report.accepted_accounting.explained_gaps, 1);
  assert.ok(report.issues.some(issue => issue.decision_id === 'second'));
});

test('native execution order owns derived points even when admission order differs', () => {
  const { source } = queuedExample();
  const [observation, first, firstExecution, second, firstFinished, secondExecution, secondFinished] = source.events;
  const finalBasis = secondFinished.successor_basis;
  secondFinished.successor_basis = firstFinished.successor_basis;
  firstFinished.successor_basis = finalBasis;
  source.events = [observation, first, second, secondExecution, secondFinished, firstExecution, firstFinished];
  source.events.forEach((row, index) => row.sequence = index + 1);
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true);
  assert.deepEqual(report.actions.map(action => action.decision_id), ['second', 'first']);
  assert.deepEqual(report.trajectory.points.slice(0, -1).map(point => point.observation.envelope.occurrence), [1, 2]);
  assert.equal(report.actions[0].successor.public_basis.snapshot_id, firstExecution.basis.snapshot_id);
});

function cancelledExample(afterStart = false) {
  const fixture = queuedExample(), { source, second, secondExecution, nextBasis, retain } = fixture;
  second.native_action_type = 'PlayCardAction';
  const terminal = source.events.at(-1);
  terminal.phase = 'cancelled';
  if (!afterStart) source.events = source.events.filter(row => row !== secondExecution);
  else {
    const current = nextBasis('-rejected-S', true);
    const player = current.snapshot.interaction.content.context.player;
    player.energy = 0;
    player.hand.forEach(card => { card.can_play = false; card.unplayable_reason = 'EnergyCostTooHigh'; });
    secondExecution.basis = retain(current);
    secondExecution.execution_snapshot_id = secondExecution.basis.snapshot_id;
    secondExecution.missing_reason = 'execution_semantic_action_space_missing';
    secondExecution.execution_action_space = null;
    secondExecution.native_semantic_capture = {
      schema: 'sts2.player-environment/process-local-native-semantic-capture-1', phase: 'before_execution', status: 'captured',
      semantic_actions: fixture.source.events.find(row => row.kind === 'execution').execution_action_space.actions.filter(action => action.verb === 'end_turn'),
      ui_catalog: { snapshot_id: current.snapshot.snapshot_id },
      observed_action: { membership: 'absent', semantic_match_count: 0, native_action_type: 'PlayCardAction' } };
  }
  const reason = afterStart ? 'execution_semantic_action_space_missing' : 'accepted_native_action_ended_before_execution';
  source.events.push({ ...terminal, kind: 'issue', event_id: 'original-native-cancel-issue', reason,
    native_witness: { action_witness_id: second.action_witness_id, decision_id: second.decision_id } });
  source.events.forEach((row, index) => row.sequence = index + 1);
  return fixture;
}

for (const afterStart of [false, true]) test(`an explained native cancellation ${afterStart ? 'after' : 'before'} execution retains originals/issues and no successful label`, () => {
  const { source } = cancelledExample(afterStart);
  const originalBytes = JSON.stringify(source.events);
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true);
  assert.deepEqual(report.accepted_accounting, { accepted: 2, derived: 1, native_cancelled: 1, presentation: 0, presentation_exceptions: 0, explained_gaps: 0 });
  const cancelled = report.actions.find(action => action.decision_id === 'second');
  assert.equal(cancelled.choice_id, null); assert.equal(cancelled.choice, null);
  assert.equal(cancelled.result.status, 'interrupted_known');
  assert.equal(cancelled.native_disposition.successful_label, false);
  assert.equal(report.trajectory.points.length, 2);
  assert.equal(report.trajectory.cancellation_count, 1);
  assert.equal(report.trajectory.points.at(-1).continuity_checks[0].decision_id, 'second');
  assert.deepEqual(report.trajectory.points.at(-1).continuity_checks.map(check => check.boundary), ['before', 'after']);
  assert.equal(report.native_cancellations[0].continuity, 'requires_strict_managed_comparison');
  assert.ok(report.issues.some(issue => issue.event_id === 'original-native-cancel-issue' && issue.blocking === false));
  assert.deepEqual(report.blocking_issues, []);
  assert.equal(report.strict_managed_comparison, 'not_run');
  assert.equal(JSON.stringify(source.events), originalBytes);
});

for (const defect of ['missing-H-read', 'foreign-native-input', 'missing-S-read', 'missing-catalog',
  'foreign-execution-snapshot', 'duplicate-input-carriers', 'source-failure', 'unknown']) test(`native cancellation does not excuse ${defect}`, () => {
  const { source, second, secondExecution, retain, nextBasis } = cancelledExample(true);
  if (defect === 'missing-H-read') { const bad = nextBasis('-bad-H', true); bad.reads.shift(); second.basis = retain(bad); }
  if (defect === 'foreign-native-input') secondExecution.native_input.arguments = { target: 'foreign-target' };
  if (defect === 'missing-S-read') { const bad = nextBasis('-bad-S', true); bad.reads.shift(); secondExecution.basis = retain(bad);
    secondExecution.execution_snapshot_id = bad.snapshot.snapshot_id; secondExecution.native_semantic_capture.ui_catalog.snapshot_id = bad.snapshot.snapshot_id; }
  if (defect === 'missing-catalog') secondExecution.native_semantic_capture = null;
  if (defect === 'foreign-execution-snapshot') secondExecution.execution_snapshot_id = 'foreign-snapshot';
  if (defect === 'duplicate-input-carriers') secondExecution.bound_action = {
    verb: secondExecution.native_input.verb, subject_referent_id: secondExecution.native_input.subject_referent_id, arguments: [] };
  if (defect === 'source-failure') source.events.at(-1).reason = 'pre_frame_capture_failed';
  if (defect === 'unknown') source.events.find(row => row.kind === 'lifecycle' && row.decision_id === 'second').phase = 'unknown';
  if (defect === 'missing-H-read' || defect === 'missing-S-read') {
    assert.throws(() => convertNativeSemanticOriginals(source, sdk, core), /public Read inventory is incomplete/);
    return;
  }
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, false); assert.equal(report.trajectory, null);
  assert.ok(report.blocking_issues.length > 0);
});

test('a cancelled-before-execution input retains complete popup H as source without requiring an H P1 choice', () => {
  const { source, second, retain, nextBasis } = cancelledExample();
  const human = nextBasis('-popup-H');
  human.snapshot.interaction.kind = 'potion_popup';
  human.snapshot.interaction.content_schema = 'sts2.player-environment/surface/potion_popup-1';
  human.snapshot.interaction.content.surface.kind = 'potion_popup';
  second.basis = retain(human);
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true);
  assert.equal(report.accepted_accounting.native_cancelled, 1);
  assert.equal(report.actions.find(action => action.decision_id === 'second').choice, null);
  const before = report.trajectory.points.flatMap(point => point.continuity_checks ?? [])
    .find(check => check.boundary === 'before');
  assert.equal(before.observation.scene.native_kind, 'potion_popup');
  assert.equal(before.observation.envelope.required_information_complete, false);
});

test('cancellation continuity retains the real before facts instead of copying its later after point', () => {
  const { source, second, retain, nextBasis } = cancelledExample();
  const human = nextBasis('-different-before');
  human.snapshot.persistent.content.player.gold += 999;
  second.basis = retain(human);
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true);
  const checks = report.trajectory.points.flatMap(point => point.continuity_checks ?? []);
  assert.equal(checks.find(check => check.boundary === 'before').source_event_id, second.event_id);
  const before = checks.find(check => check.boundary === 'before').observation;
  const after = checks.find(check => check.boundary === 'after').observation;
  assert.equal(before.player.content.gold, after.player.content.gold + 999);
});

test('the production converter and replay consumer reject changed cancellation-before facts even when every main point matches', () => {
  const { source, second, retain, nextBasis } = cancelledExample();
  const human = nextBasis('-different-before');
  human.snapshot.persistent.content.player.gold += 999;
  second.basis = retain(human);
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true);
  const directory = mkdtempSync(join(tmpdir(), 'p1-cancel-continuity-'));
  try {
    const trajectory = join(directory, 'trajectory.json'), log = join(directory, 'replay.jsonl');
    writeFileSync(trajectory, JSON.stringify(report.trajectory));
    const input = [{ type: 'hello', protocol: 'sts2.p1/agent-port-1' },
      ...report.trajectory.points.map(point => ({ type: 'observation', requires_choice: true,
        observation: point.observation })), { type: 'episode_end', censor_reason: 'fixture_end' }]
      .map(value => JSON.stringify(value)).join('\n') + '\n';
    const child = spawnSync(process.execPath, [fileURLToPath(new URL(
      '../../components/policy-runtime/examples/p1/replay_agent.mjs', import.meta.url)), trajectory, log],
    { input, encoding: 'utf8', timeout: 10000 });
    assert.equal(child.status, 0, child.stderr);
    const events = readFileSync(log, 'utf8').trim().split('\n').map(JSON.parse);
    assert.ok(events.some(event => event.type === 'cancellation_continuity'
      && event.boundary === 'before' && event.verdict === 'different'));
    assert.ok(events.some(event => event.type === 'divergence'
      && event.reason === 'cancellation_continuity_difference'));
    assert.equal(events.some(event => event.type === 'prefix_end'), false);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('cancelled inputs may coexist with complete native run boundaries; strict replay remains separate', () => {
  const { source, retain } = cancelledExample();
  const terminal = JSON.parse(readFileSync(new URL('../../components/connector/sdk/typescript/test/fixtures/p1-global/managed-game_over.json', import.meta.url), 'utf8'));
  source.events.push({ ...source.events.at(-1), kind: 'observation',
    event_id: 'terminal-observation', sequence: source.events.length + 1,
    reason: 'native_run_ended', basis: retain(terminal) });
  source.journal = [{ run_id: 'run-1', kind: 'run_started_native', sequence: 1 }, { run_id: 'run-1', kind: 'run_ended_native', sequence: 2 }];
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true); assert.equal(report.whole_run_recording_complete, true);
  assert.equal(report.trajectory.points.at(-1).observation.status, 'terminal');
  assert.equal(report.strict_managed_comparison, 'not_run');
  source.journal.push({ run_id: 'run-1', kind: 'run_abandoned_native', sequence: 3 });
  assert.equal(convertNativeSemanticOriginals(source, sdk, core).whole_run_recording_complete, false);
});

// Native-shaped synthetic originals exercise the adapter, never prove gameplay.
async function nativeBrowse(kind, change = null) {
  const { selectorForwardPair } = await import('../../components/connector/sdk/typescript/test/fixtures/selector-forward.mjs');
  const { source, retain, row } = example(); source.events.length = 0;
  const pair = kind !== 'reward' ? selectorForwardPair() : rewardForwardPair();
  const before = pair.outer, middle = pair.preview ?? pair.child;
  if (kind === 'deck') for (const basis of [before, middle]) {
    const s = basis.snapshot.interaction.content.surface;
    s.kind = basis.snapshot.interaction.kind = 'native_deck_card_selection';
    basis.snapshot.interaction.content_schema = 'sts2.player-environment/surface/native_deck_card_selection-1';
    s.selector_request.family = 'deck_card';
    for (const option of s.preview_options) {
      option.after = null; option.after_availability = 'not_applicable'; option.display_context = 'native_deck_card_before';
    }
  }
  const restored = structuredClone(before);
  restored.snapshot.snapshot_id += '-restored'; restored.snapshot.sequence++;
  for (const read of restored.reads) read.response.expected_snapshot_id = read.response.observed_snapshot_id = restored.snapshot.snapshot_id;
  if (change === 'world') restored.snapshot.persistent.content.player.gold++;
  if (change === 'refresh') restored.snapshot.interaction.content.surface.rewards[0].native_relic_refresh_subscription = 'attached';
  if (change === 'directory') restored.snapshot.bound_actions.actions[0].label += ' changed';
  if (change === 'read') restored.reads[0].response.content.extra_public_fact = 'changed';
  const accept = (id, value, action) => row('decision_accepted', { decision_id: id, action_witness_id: `witness-${id}`,
    basis: retain(value), bound_action: action, missing_reason: null,
    native_receipt: { kind: 'semantic_ui_accepted', carrier_ref: `witness-${id}` } });
  const first = before.snapshot.bound_actions.actions.find(a => a.verb === (kind !== 'reward' ? 'select' : 'activate'));
  const second = middle.snapshot.bound_actions.actions.find(a => a.verb === (kind !== 'reward' ? 'cancel' : 'skip'));
  if (change === 'native-activate') second.verb = 'activate';
  accept('browse-start', before, first);
  const opened = structuredClone(middle);
  if (change === 'settling-child' || change === 'settling-foreign-world') {
    opened.snapshot.status = 'settling'; opened.snapshot.interaction.stage = 'settling';
    opened.snapshot.interaction.content.surface.selectable_card_entity_ids = [];
    opened.snapshot.bound_actions.actions = [];
    opened.snapshot.bound_actions.materialized_count = opened.snapshot.bound_actions.total_count = 0;
    if (change === 'settling-foreign-world') opened.snapshot.persistent.content.player.gold++;
  }
  row('lifecycle', { decision_id: 'browse-start', action_witness_id: 'witness-browse-start', phase: 'finished', successor_basis: retain(opened) });
  accept('browse-back', middle, second);
  row('lifecycle', { decision_id: 'browse-back', action_witness_id: 'witness-browse-back', phase: change === 'unknown' ? 'unknown' : 'finished', successor_basis: retain(restored) });
  if (change === 'foreign') source.events[2].bound_action = { ...second, subject_referent_id: 'foreign-subject' };
  if (kind === 'deck') {
    for (const [accepted, terminal, callback] of [[source.events[0], source.events[1], 'OnCardClicked'],
      [source.events[2], source.events[3], 'PreviewCancel.Released']]) {
      accepted.native_action_type = 'MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NDeckCardSelectScreen.' + callback;
      accepted.native_witness = { selector_owner_witness_id: 'native-selector', parent_action_witness_id: 'native-parent',
        parent_owner_witness_id: 'native-parent-owner' };
      accepted.native_receipt.selector_callback = { native_callback: accepted.native_action_type,
        native_acceptance_proven: true, terminal: false };
      terminal.native_completion = { native_callback: accepted.native_action_type, terminal: false };
    }
    if (change === 'foreign-owner') source.events[2].native_witness.selector_owner_witness_id = 'different-selector';
    if (change === 'foreign-parent') source.events[2].native_witness.parent_action_witness_id = 'different-parent';
    if (change === 'purchase-cancel') source.events[3].native_completion.terminal = true;
    if (change === 'missing-acceptance') source.events[2].native_receipt.selector_callback.native_acceptance_proven = false;
  }
  return convertNativeSemanticOriginals(source, sdk, core);
}
for (const kind of ['selector', 'deck', 'reward']) test(`${kind} browsing is a separate disposition with two real original checkpoints and zero successful labels`, async () => {
  const report = await nativeBrowse(kind);
  assert.equal(report.native_presentations.length, 1);
  assert.equal(report.native_presentations[0].comparison.equal, true);
  assert.deepEqual(report.native_presentations[0].continuity_checkpoints.map(x => x.boundary), ['before', 'after']);
  assert.equal(report.accepted_accounting.presentation, 2);
  assert.equal(report.accepted_accounting.derived, 0);
  assert.ok(report.actions.every(a => a.choice_id === null && a.choice === null && a.result.status === 'completed'));
  assert.ok(report.issues.every(issue => !issue.blocking));

});
for (const change of ['foreign-owner', 'foreign-parent', 'purchase-cancel', 'missing-acceptance', 'unknown', 'foreign', 'world', 'read', 'directory'])
  test(`deck preview return preserves ${change} as an unresolved gap`, async () => {
    const report = await nativeBrowse('deck', change);
    assert.equal(report.complete, false);
    assert.equal(report.accepted_accounting.presentation, 0);
    assert.ok(report.blocking_issues.length > 0);
    assert.ok(report.actions.every(action => action.choice_id === null));
  });
for (const change of ['world', 'refresh', 'directory', 'read']) test(`changed browsing ${change} is reported with real field differences and no successful labels`, async () => {
  const report = await nativeBrowse('reward', change);
  assert.equal(report.presentation_gaps.length, 1);
  assert.equal(report.presentation_gaps[0].comparison.equal, false);
  assert.ok(report.presentation_gaps[0].comparison.changes.length > 0);
  assert.equal(report.accepted_accounting.presentation, 0);
  assert.equal(report.accepted_accounting.explained_gaps, 2);
  assert.ok(report.actions.every(action => action.choice_id === null));
  assert.equal(report.trajectory, null);
});
for (const change of ['unknown', 'foreign']) test(`browsing cannot forgive ${change} native evidence`, async () => {
  const report = await nativeBrowse('selector', change);
  assert.equal(report.native_presentations.length, 0);
  assert.ok(report.blocking_issues.length > 0);
  assert.equal(report.trajectory, null);
});

test('Reference Activate on an explicitly native Skip is browsing, independent of its label', async () => {
  const report = await nativeBrowse('reward', 'native-activate');
  assert.equal(report.native_presentations[0].comparison.equal, true);
  assert.equal(report.accepted_accounting.presentation, 2);
  assert.ok(report.actions.every(a => a.choice_id === null));
});


test('a diagnostic derived range remains bound to the full bundle and cannot use a future terminal', () => {
  const { source } = example();
  const last = source.events.at(-1).sequence;
  const report = convertNativeSemanticOriginals(source, sdk, core, { throughSequence: last - 1 });
  assert.equal(report.scope.native_event_range.derived_only, true);
  assert.equal(report.source_content_id, source.manifest.bundle_content_id);
  assert.equal(report.scope.native_event_range.source_last_sequence, last);
  assert.equal(report.whole_run_recording_complete, false);
  assert.ok(report.captures.every(c => c.sequence <= last - 1));
  const withoutTerminal = convertNativeSemanticOriginals(source, sdk, core, { throughSequence: 2 });
  assert.equal(withoutTerminal.actions[0].result, null);
  assert.equal(withoutTerminal.trajectory, null);
  for (const throughSequence of [0, last + 1, 1.5, '2'])
    assert.throws(() => convertNativeSemanticOriginals(source, sdk, core, { throughSequence }), /sequence_out_of_range/);
});


test('the exact approved refresh side effect stays a separate exception with literal unequal checkpoints', async () => {
  const { source, retain, row } = example(); source.events.length = 0;
  const { outer, child, group } = rewardForwardPair(); group.native_relic_refresh_subscription = 'attached';
  child.snapshot.interaction.content.surface.native_relic_refresh_subscription = 'attached';
  const reference = { ...outer.snapshot.referents[0], referent_id: group.entity_id,
    properties: { ...group } }; outer.snapshot.referents = [reference];
  const after = structuredClone(outer);
  after.snapshot.interaction.content.surface.rewards[0].native_relic_refresh_subscription = 'absent';
  after.snapshot.referents[0].properties.native_relic_refresh_subscription = 'absent';
  const accept = (id, value, action) => row('decision_accepted', { decision_id: id, action_witness_id: id,
    basis: retain(value), bound_action: action, native_receipt: { kind: 'semantic_ui_accepted', carrier_ref: id } });
  accept('open-refresh', outer, outer.snapshot.bound_actions.actions[0]);
  row('lifecycle', { decision_id: 'open-refresh', action_witness_id: 'open-refresh', phase: 'finished', successor_basis: retain(child) });
  accept('skip-refresh', child, child.snapshot.bound_actions.actions.find(a => a.verb === 'skip'));
  row('lifecycle', { decision_id: 'skip-refresh', action_witness_id: 'skip-refresh', phase: 'finished', successor_basis: retain(after) });
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.native_presentations[0].exception.id, 'stop_relic_refresh');
  assert.equal(report.native_presentations[0].comparison.equal, false);
  assert.equal(report.native_presentations[0].comparison.changes.length, 2);
  assert.equal(report.accepted_accounting.presentation_exceptions, 2);
  assert.equal(report.projection_claims.lossless_p1_trace, false);
  assert.equal(report.projection_claims.exceptions.length, 1);
  assert.equal(report.projection_claims.exceptions[0].p1_action, null);
  assert.equal(report.projection_claims.p1_host_conformance, 'not_established');
  const replay = convertNativeSemanticOriginals(source, sdk, core, { includeNativeReplayWitnesses: true });
  assert.equal(replay.native_presentations[0].native_replay_steps.length, 2);
  assert.deepEqual(replay.actions, report.actions);
  assert.deepEqual(replay.native_presentations[0].comparison, report.native_presentations[0].comparison);
  assert.equal(report.presentation_gaps.length, 0);
  assert.ok(report.actions.every(action => action.choice_id === null));
  assert.ok(report.issues.every(issue => !issue.blocking));
});

// Synthetic native-shaped Smith/cancel originals, not a gameplay qualification.
async function smithCancellationExample() {
  const { selectorForwardPair } = await import('../../components/connector/sdk/typescript/test/fixtures/selector-forward.mjs');
  const { source, original, retain, row } = example(); source.events.length = 0;
  const before = structuredClone(original);
  const option = { entity_id: 'smith-option', index: 0, option_id: 'SMITH', name: 'Smith', description: 'Upgrade.', enabled: true };
  Object.assign(before.snapshot.interaction, { kind: 'rest_site', content_schema: 'sts2.player-environment/surface/rest_site-1',
    content: { surface: { kind: 'rest_site', options: [option], can_proceed: false }, context: { kind: 'rest_site', room_entity_id: 'rest-room' } } });
  const action = { bound_action_id: 'smith', verb: 'activate', interaction_id: before.snapshot.interaction.interaction_id,
    subject_referent_id: option.entity_id, arguments: [], label: 'Smith' };
  before.snapshot.bound_actions.actions = [action];
  before.snapshot.bound_actions.total_count = before.snapshot.bound_actions.materialized_count = 1;
  before.snapshot.referents = [{ referent_id: option.entity_id, role: 'rest_option', kind: 'entity', label: 'Smith',
    state: { visible: true, observation_basis: 'native_visible_fact' }, properties_schema: 'sts2.player-environment/referent/rest_option-1', properties: option }];
  const middle = selectorForwardPair().outer;
  const selector = middle.snapshot.interaction.content.surface;
  selector.current_controls.push({ entity_id: 'cancel', operation: 'cancel_selection' });
  const cancel = { ...action, bound_action_id: 'selector-cancel', verb: 'cancel',
    interaction_id: middle.snapshot.interaction.interaction_id, subject_referent_id: 'cancel', label: 'Cancel' };
  middle.snapshot.bound_actions.actions.push(cancel);
  middle.snapshot.bound_actions.materialized_count = middle.snapshot.bound_actions.total_count = middle.snapshot.bound_actions.actions.length;
  const callback = 'MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NDeckUpgradeSelectScreen.Close.Released';
  row('decision_accepted', { decision_id: 'smith', action_witness_id: 'root-smith', basis: retain(before), bound_action: action,
    native_action_type: 'RestSiteSynchronizer.ChooseLocalOption', native_witness: { subject_witness_id: 'smith-operand' },
    native_receipt: { kind: 'semantic_ui_accepted', carrier_ref: 'root-smith' } });
  row('decision_accepted', { decision_id: 'cancel', action_witness_id: 'root-cancel', basis: retain(middle), bound_action: cancel,
    native_action_type: callback, native_witness: { parent_action_witness_id: 'root-smith', parent_owner_witness_id: 'smith-operand' },
    native_receipt: { kind: 'semantic_ui_accepted', carrier_ref: 'root-cancel',
      selector_callback: { native_callback: callback, native_acceptance_proven: true } } });
  row('lifecycle', { decision_id: 'cancel', action_witness_id: 'root-cancel', phase: 'finished',
    native_completion: { native_callback: callback, terminal: true }, successor_basis: retain(before) });
  row('lifecycle', { decision_id: 'smith', action_witness_id: 'root-smith', phase: 'unknown',
    native_completion: { family: 'rest_site', kind: 'RestSiteSynchronizer.ChooseLocalOption', action_witness_id: 'root-smith',
      task_witness_id: 'exact-task', native_operand_witness_id: 'smith-operand', succeeded: false }, successor_basis: retain(before) });
  row('decision_accepted', { decision_id: 'next', action_witness_id: 'root-next', basis: retain(before), bound_action: action,
    native_receipt: { kind: 'semantic_ui_accepted', carrier_ref: 'root-next' } });
  row('lifecycle', { decision_id: 'next', action_witness_id: 'root-next', phase: 'finished', successor_basis: retain(before) });
  return { source, before, middle, retain };
}

test('exact Smith selector cancellation keeps raw unknown, has zero labels, and requires both strict checkpoints', async () => {
  const { source } = await smithCancellationExample();
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true, JSON.stringify(report.blocking_issues));
  assert.equal(report.accepted_accounting.native_cancelled, 2);
  assert.equal(report.raw_unknown_count, 1); assert.equal(report.explained_raw_unknown_count, 1);
  assert.deepEqual(report.actions.slice(0, 2).map(a => [a.choice_id, a.result.status]), [[null, 'unknown'], [null, 'completed']]);
  assert.equal(report.native_cancellations.length, 1);
  const cancel = report.native_cancellations[0];
  assert.equal(cancel.original_parent_terminal.phase, 'unknown');
  assert.equal(cancel.successful_label, false);
  assert.deepEqual(cancel.continuity_checkpoints.map(c => c.boundary), ['before', 'after']);
  assert.equal(report.trajectory.points.filter(p => p.chosen !== null).length, 1);
  assert.equal(report.trajectory.points[0].continuity_checks.length, 2);
});

for (const status of ['Faulted', 'Canceled', 'RanToCompletion', null])
  test(`new Smith ${status} unknown is not the legacy cancellation exception`, async () => {
    const { source } = await smithCancellationExample();
    Object.assign(source.events[3].native_completion, { native_task_status: status, native_bool_result: null,
      explicit_selector_cancel: null });
    const report = convertNativeSemanticOriginals(source, sdk, core);
    assert.equal(report.complete, false); assert.equal(report.native_cancellations.length, 0);
    assert.ok(report.blocking_issues.some(issue => issue.reason === 'accepted_action_unknown'));
  });

async function explicitMerchantCancellationExample() {
  const fixture = await smithCancellationExample(), { source, before, middle, retain } = fixture;
  const action = source.events[0].bound_action;
  action.verb = 'open'; action.label = 'Choose a card to remove';
  before.snapshot.bound_actions.actions = [structuredClone(action)];
  Object.assign(before.snapshot.interaction, { kind: 'shop_inventory',
    content_schema: 'sts2.player-environment/surface/shop_inventory-1', content: {
      surface: { kind: 'shop_inventory', shop_session_entity_id: 'shop-session', cards: [], relics: [], potions: [],
        card_removal: { entity_id: action.subject_referent_id, price: 75, stocked: true, can_purchase: true } },
      context: { kind: 'shop', room_entity_id: 'shop-room' } } });
  before.snapshot.referents[0].role = 'shop_entry';
  const selector = middle.snapshot.interaction.content.surface;
  selector.kind = middle.snapshot.interaction.kind = 'native_deck_card_selection';
  middle.snapshot.interaction.content_schema = 'sts2.player-environment/surface/native_deck_card_selection-1';
  selector.selector_request.family = 'deck_card';
  for (const option of selector.preview_options) {
    option.after = null; option.after_availability = 'not_applicable'; option.display_context = 'native_deck_card_before';
  }
  const [parent, child, childEnd, parentEnd, next, nextEnd] = source.events;
  parent.native_action_type = 'MerchantCardRemovalEntry.OnTryPurchaseWrapper'; parent.basis = retain(before);
  child.native_action_type = child.native_receipt.selector_callback.native_callback
    = childEnd.native_completion.native_callback = 'MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NDeckCardSelectScreen.Close.Released';
  child.native_witness.selector_owner_witness_id = 'exact-selector'; child.basis = retain(middle);
  parentEnd.phase = 'cancelled';
  Object.assign(parentEnd.native_completion, { kind: parent.native_action_type, family: 'shop_inventory',
    native_task_status: 'RanToCompletion', native_bool_result: false,
    explicit_selector_cancel: { ...structuredClone(child.native_witness), child_decision_id: child.decision_id,
      child_action_witness_id: child.action_witness_id, native_callback: child.native_action_type,
      native_acceptance_proven: true, terminal: true } });
  childEnd.successor_basis = parentEnd.successor_basis = next.basis = nextEnd.successor_basis = retain(before);
  next.bound_action = structuredClone(action);
  return fixture;
}

test('new explicit merchant cancellation has zero labels and full original continuity checks', async () => {
  const { source } = await explicitMerchantCancellationExample();
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true, JSON.stringify(report.blocking_issues));
  assert.equal(report.accepted_accounting.native_cancelled, 2);
  assert.equal(report.raw_unknown_count, 0);
  assert.equal(report.native_cancellations[0].kind, 'merchant_removal_cancel');
  assert.deepEqual(report.actions.slice(0, 2).map(a => a.choice_id), [null, null]);
  assert.deepEqual(report.native_cancellations[0].continuity_checkpoints.map(c => c.boundary), ['before', 'after']);
});

test('an explicit final selector cancellation uses its actual parent successor without another input', async () => {
  const { source } = await explicitMerchantCancellationExample();
  source.events.splice(4);
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.blocking_issues.length, 0, JSON.stringify(report.blocking_issues));
  // A cancel-only fixture still has no successful P1 action/trajectory.
  assert.equal(report.complete, false);
  assert.equal(report.native_cancellations.length, 1);
  assert.equal(report.accepted_accounting.native_cancelled, 2);
  assert.ok(report.actions.every(action => action.choice_id === null));
  const after = report.native_cancellations[0].continuity_checkpoints.find(check => check.boundary === 'after');
  assert.equal(after.source_event_id, source.events[3].event_id);
  assert.deepEqual(after.source_public_basis, source.events[3].successor_basis);
});

for (const defect of ['old_unknown', 'faulted', 'missing_result', 'foreign_child', 'foreign_selector', 'missing_selector', 'missing_proof', 'changed_world', 'missing_after', 'incomplete_after'])
  test(`merchant cancellation keeps ${defect} blocking`, async () => {
    const { source, before, retain } = await explicitMerchantCancellationExample();
    const end = source.events[3], proof = end.native_completion.explicit_selector_cancel;
    if (defect === 'old_unknown') { end.phase = 'unknown'; delete end.native_completion.explicit_selector_cancel; }
    if (defect === 'faulted') end.native_completion.native_task_status = 'Faulted';
    if (defect === 'missing_result') delete end.native_completion.native_bool_result;
    if (defect === 'foreign_child') proof.child_action_witness_id = 'foreign';
    if (defect === 'foreign_selector') proof.selector_owner_witness_id = 'foreign';
    if (defect === 'missing_selector') { delete proof.selector_owner_witness_id; delete source.events[1].native_witness.selector_owner_witness_id; }
    if (defect === 'missing_proof') delete end.native_completion.explicit_selector_cancel;
    if (defect === 'changed_world') { before.snapshot.persistent.content.player.gold--; end.successor_basis = retain(before); }
    if (defect === 'missing_after') end.successor_basis = null;
    if (defect === 'incomplete_after') end.successor_missing_reason = 'basis_incomplete';
    const report = convertNativeSemanticOriginals(source, sdk, core);
    assert.equal(report.native_cancellations.length, 0); assert.equal(report.complete, false);
  });

for (const defect of ['foreign_parent', 'foreign_operand', 'missing_child_completion', 'selected_card', 'false_without_cancel',
  'changed_public_fact', 'changed_read', 'changed_directory', 'different_session', 'other_unknown', 'foreign_bound', 'changed_operands'])
  test(`Smith cancellation does not forgive ${defect}`, async () => {
    const { source, before, middle, retain } = await smithCancellationExample();
    if (defect === 'foreign_bound') source.events[1].bound_action.bound_action_id = 'foreign';
    if (defect === 'changed_operands') source.events[1].bound_action.arguments = [{ role: 'target', referent_id: 'foreign' }];
    if (defect === 'foreign_parent') source.events[1].native_witness.parent_action_witness_id = 'foreign';
    if (defect === 'foreign_operand') source.events[1].native_witness.parent_owner_witness_id = 'foreign';
    if (defect === 'missing_child_completion') delete source.events[2].native_completion;
    if (defect === 'selected_card') { middle.snapshot.interaction.content.surface.selected_card_entity_ids = ['selected']; source.events[1].basis = retain(middle); }
    if (defect === 'false_without_cancel') source.events[1].bound_action.verb = 'open';
    if (defect === 'changed_public_fact') { before.snapshot.persistent.content.player.gold++; source.events[4].basis = retain(before); }
    if (defect === 'changed_read') { before.reads[0].response.content.extra_public_fact = true; source.events[4].basis = retain(before); }
    if (defect === 'changed_directory') { before.snapshot.bound_actions.actions[0].label += ' changed'; source.events[4].basis = retain(before); }
    if (defect === 'different_session') source.events[1].session_id = 'foreign';
    if (defect === 'other_unknown') source.events[0].native_action_type = 'AnotherNativeTask';
    const report = convertNativeSemanticOriginals(source, sdk, core);
    assert.equal(report.native_cancellations.length, 0);
    assert.equal(report.complete, false); assert.equal(report.trajectory, null);
    assert.ok(report.blocking_issues.some(issue => issue.reason === 'accepted_action_unknown'));
  });


test('reward open may finish while settling; real ready child and full endpoints still prove browsing', async () => {
  const report = await nativeBrowse('reward', 'settling-child');
  assert.equal(report.native_presentations.length, 1);
  assert.equal(report.native_presentations[0].comparison.equal, true);
  assert.equal(report.accepted_accounting.presentation, 2);
  assert.ok(report.actions.every(action => action.choice_id === null));
  assert.ok(report.captures.some(capture => capture.observation?.scene.stage === 'settling'));
});
test('a settling reward callback cannot hide different world facts from the same-instance intention', async () => {
  const report = await nativeBrowse('reward', 'settling-foreign-world');
  assert.equal(report.native_presentations.length, 0);
});

async function explicitSmithSettlingExample() {
  const f = await smithCancellationExample(), { source, before, retain } = f;
  const [parent, child, , parentEnd] = source.events;
  child.native_witness.selector_owner_witness_id = 'exact-selector';
  parentEnd.phase = 'cancelled';
  Object.assign(parentEnd.native_completion, { native_task_status: 'RanToCompletion', native_bool_result: false,
    explicit_selector_cancel: { ...structuredClone(child.native_witness), child_decision_id: child.decision_id,
      child_action_witness_id: child.action_witness_id, native_callback: child.native_action_type,
      native_acceptance_proven: true, terminal: true } });
  const settling = structuredClone(before);
  settling.snapshot.status = 'settling'; settling.snapshot.interaction.stage = 'settling';
  settling.snapshot.bound_actions.actions = [];
  settling.snapshot.bound_actions.materialized_count = settling.snapshot.bound_actions.total_count = 0;
  settling.snapshot.interaction.content.surface.options[0].enabled = false;
  parentEnd.successor_basis = retain(settling);
  return f;
}

test('explicit Smith cancel retains the Task settling basis and proves restoration at the next real input', async () => {
  const { source } = await explicitSmithSettlingExample();
  const original = structuredClone(source.events[3]);
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, true, JSON.stringify(report.blocking_issues));
  assert.equal(report.accepted_accounting.native_cancelled, 2);
  const after = report.native_cancellations[0].continuity_checkpoints.find(x => x.boundary === 'after');
  assert.equal(after.source_event_id, source.events[4].event_id);
  assert.deepEqual(after.source_public_basis, source.events[4].basis);
  assert.deepEqual(source.events[3], original);
});
for (const defect of ['missing_ready', 'changed_ready', 'foreign_run']) test(`settling Smith cancellation cannot infer restoration from ${defect}`, async () => {
  const { source, before, retain } = await explicitSmithSettlingExample();
  if (defect === 'missing_ready') source.events.splice(4);
  if (defect === 'changed_ready') {
    before.snapshot.persistent.content.player.gold++;
    source.events[4].basis = retain(before);
  }
  if (defect === 'foreign_run') source.events[4].run_id = source.events[5].run_id = 'foreign-run';
  const report = convertNativeSemanticOriginals(source, sdk, core, { runId: 'run-1' });
  assert.equal(report.complete, false);
  assert.equal(report.native_cancellations.length, 0);
});

function shopBrowseExample(defect = null) {
  const f = example(), { source, original, retain, row } = f;
  source.events.length = 0;
  const before = structuredClone(original);
  Object.assign(before.snapshot.interaction, { kind: 'shop_inventory', content_schema: 'sts2.player-environment/surface/shop_inventory-1',
    content: { surface: { kind: 'shop_inventory', shop_session_entity_id: 'shop-owner', cards: [], relics: [], potions: [] },
      context: { kind: 'shop', room_entity_id: 'shop-room' } } });
  const close = { bound_action_id: 'close-shop', verb: 'cancel', interaction_id: before.snapshot.interaction.interaction_id,
    arguments: [], label: 'Close inventory' };
  before.snapshot.bound_actions.actions = [close];
  before.snapshot.bound_actions.materialized_count = before.snapshot.bound_actions.total_count = 1;
  const middle = structuredClone(before);
  Object.assign(middle.snapshot.interaction, { kind: 'shop_room', content_schema: 'sts2.player-environment/surface/shop_room-1',
    content: { surface: { kind: 'shop_room', shop_session_entity_id: 'shop-owner', can_open_inventory: true },
      context: structuredClone(before.snapshot.interaction.content.context) } });
  const open = { ...close, bound_action_id: 'open-shop', verb: 'open', label: 'Open inventory' };
  middle.snapshot.bound_actions.actions = [open];
  const restored = structuredClone(before);
  if (defect === 'changed_read') restored.reads[0].response.content.new_visible_fact = 'changed';
  if (defect === 'different_owner') restored.snapshot.interaction.content.surface.shop_session_entity_id = 'other';
  const accept = (id, b, action, type) => row('decision_accepted', { decision_id: id, action_witness_id: 'root-' + id,
    native_action_type: type, basis: retain(b), bound_action: action,
    native_receipt: { kind: 'semantic_ui_accepted', carrier_ref: 'root-' + id } });
  accept('close', before, close, 'NMerchantInventory.Close');
  row('lifecycle', { decision_id: 'close', action_witness_id: 'root-close', phase: 'finished', successor_basis: retain(middle) });
  accept('reopen', middle, open, 'NMerchantRoom.OpenInventory');
  row('lifecycle', { decision_id: 'reopen', action_witness_id: 'root-reopen', phase: defect === 'unknown' ? 'unknown' : 'finished', successor_basis: retain(restored) });
  return f;
}
test('same-owner shop close/reopen with equal whole originals is zero-label presentation', () => {
  const { source } = shopBrowseExample();
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.native_presentations.length, 1);
  assert.equal(report.native_presentations[0].kind, 'shop_close_reopen');
  assert.equal(report.native_presentations[0].comparison.equal, true);
  assert.equal(report.accepted_accounting.presentation, 2);
  assert.ok(report.actions.every(x => x.choice_id === null));
});
for (const defect of ['changed_read', 'different_owner', 'unknown']) test(`shop browse keeps ${defect} unresolved`, () => {
  const { source } = shopBrowseExample(defect);
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.equal(report.complete, false);
  assert.equal(report.accepted_accounting.presentation, 0);
});

test('optional segments reuse shared-core observations without changing whole-run conversion claims', () => {
  const { source } = example();
  const plain = convertNativeSemanticOriginals(source, sdk, core);
  const withSegments = convertNativeSemanticOriginals(source, sdk, core, { includeSegments: true });
  assert.equal(withSegments.complete, plain.complete);
  assert.equal(withSegments.whole_run_recording_complete, plain.whole_run_recording_complete);
  assert.deepEqual(withSegments.trajectory, plain.trajectory);
  assert.equal(withSegments.segment_output.segments.length, 1);
  const segment = withSegments.segment_output.segments[0];
  assert.deepEqual(segment.points.map(point => point.chosen), plain.trajectory.points.slice(0, -1).map(point => point.chosen));
  assert.deepEqual(segment.points.map(point => point.mask.next), [true, false]);
  assert.ok(segment.points.every(point => point.mask.N && !point.mask.end));
  assert.equal(withSegments.segment_output.training_loader_consumed, false);
  assert.deepEqual(withSegments.segment_output.projection_claims, withSegments.projection_claims);
});

test('an unknown between choices cuts next and resumes only at a real complete uncommitted basis', () => {
  const { source } = example();
  source.events.splice(4, 0, { ...source.events[1], decision_id: 'unknown-choice', action_witness_id: 'unknown-root',
    native_receipt: { kind: 'game_action_accepted', carrier_ref: 'unknown-root' } },
    { ...source.events[3], decision_id: 'unknown-choice', action_witness_id: 'unknown-root', phase: 'unknown' });
  source.events.forEach((row, i) => { row.sequence = i + 1; row.event_id = `event-${i + 1}`; });
  const report = convertNativeSemanticOriginals(source, sdk, core, { includeSegments: true });
  assert.equal(report.complete, false);
  assert.equal(report.trajectory, null);
  assert.equal(report.segment_output.segments.length, 2);
  const [first, second] = report.segment_output.segments;
  assert.equal(first.points.length, 1);
  assert.equal(first.points[0].mask.next, false);
  assert.equal(second.points[0].mask.next, false);
  assert.equal(second.data_class, 'D');
  assert.match(first.end_reason, /accepted_action_unknown/);
  assert.equal(second.start_boundary.complete_basis, true);
});

for (const openPrefix of [false, true]) test(`an unfinished native decision cuts segments through the ${openPrefix ? 'open prefix' : 'closed bundle'} tail`, () => {
  const { source } = example();
  if (openPrefix) source.manifest = { ...source.manifest.content_identity,
    schema: 'sts2.human-annotator/p1-native-prefix-1', watermark: {} };
  source.events.splice(4, 0, { ...structuredClone(source.events[1]),
    decision_id: 'unfinished-parent', action_witness_id: 'unfinished-witness',
    native_receipt: { kind: 'game_action_accepted', carrier_ref: 'unfinished-witness' } });
  source.events.forEach((row, i) => { row.sequence = i + 1; row.event_id = `event-${i + 1}`; });
  const report = convertNativeSemanticOriginals(source, sdk, core, { includeSegments: true });
  assert.equal(report.complete, false);
  assert.equal(report.segment_output.segments.length, 1);
  assert.equal(report.segment_output.segments[0].points.length, 1);
  assert.equal(report.segment_output.segments[0].points[0].mask.next, false);
  const gap = report.segment_output.gaps.find(item => item.decision_ids.includes('unfinished-parent'));
  assert.equal(gap.through_sequence, source.events.at(-1).sequence);
  assert.ok(report.segment_output.discarded_choices.some(item => item.decision_id === 'end-turn'));
});
