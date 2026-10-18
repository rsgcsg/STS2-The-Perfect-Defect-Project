import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import * as sdk from '../../components/connector/sdk/typescript/dist/index.js';
import * as core from '../../components/connector/sdk/typescript/dist/p1Global/index.js';
import { convertNativeSemanticOriginals } from '../p1-native-to-p1.mjs';

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
  return { source, blobs };
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
