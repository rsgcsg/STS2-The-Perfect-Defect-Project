import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
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
      phase: 'before_execution', basis, missing_reason: null, queued: true, queue_interval_ms: 120,
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
  assert.deepEqual(report.accepted_accounting, { accepted: 2, derived: 2, explained_gaps: 0 });
  assert.ok(report.actions.every(action => action.execution.queued && action.execution.queue_interval_ms === 120));
  assert.ok(report.actions.every(action => action.source_human_basis.snapshot_id !== action.execution.basis.snapshot_id));
  assert.equal(report.actions[0].successor.origin, 'native_before_execution');
  assert.ok(report.actions.every(action => report.captures[action.capture].origin === 'native_before_execution'));
  assert.equal(report.trajectory.points.length, 3);
});

for (const failure of ['cancel', 'target-death', 'operands-changed', 'missing-execution']) test(`queued ${failure} remains an explained gap`, () => {
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
