import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readRecordedLaunch, privateRecordingSink, publicRecordingReport } from '../p1-managed-recording-replay.mjs';
import { EpisodeRecorder, readRecord } from '../../components/policy-runtime/dist/p1-global/recorder.js';

const sha = value => createHash('sha256').update(value).digest('hex');
function fixture() {
  const raw = Buffer.from('{"原样进度":true}\r\n');
  const descriptor = { schema: 'sts2.human-annotator/initial-progress-original-1', session_id: 's', timeline_id: 't',
    run_id: 'run-1', status: 'captured', run_seed: { status: 'captured', seed: 'PRIVATESEED123' },
    payload_ref: 'initial-progress/run-1.bin', bytes: raw.length, sha256: sha(raw) };
  const reference = 'initial-progress/run-1.json';
  const source = { manifest: { content_identity: { session_id: 's', timeline_id: 't', raw_file_sha256: { [reference]: 'verified-by-original-reader' } } },
    journal: [{ kind: 'run_started_native', run_id: 'run-1' }],
    bytes(name) { return name === reference ? Buffer.from(JSON.stringify(descriptor)) : raw; } };
  const trajectory = { points: [{ observation: { player: { content: { character_definition_id: 'DEFECT' } },
    run: { content: { ascension: 10 } } } }] };
  return { source, descriptor, trajectory, raw };
}
test('recorded launch binds session/run and preserves exact UTF8 including line endings', () => {
  const { source, trajectory, raw } = fixture();
  const launch = readRecordedLaunch(source, trajectory);
  assert.equal(launch.seed, 'PRIVATESEED123');
  assert.equal(launch.character, 'DEFECT'); assert.equal(launch.ascension, 10);
  assert.deepEqual(Buffer.from(launch.initialProgress.progress_json), raw);
  assert.equal(launch.initialProgress.recipe, 'captured-native-progress-v1');
});
for (const change of ['unknown-seed', 'unknown-progress', 'session', 'timeline', 'run', 'hash', 'bytes', 'missing-start', 'missing-witness']) {
  test(`launch ${change} fails before any Host exists`, () => {
    const { source, descriptor, trajectory } = fixture();
    if (change === 'unknown-seed') descriptor.run_seed.status = 'unknown';
    if (change === 'unknown-progress') descriptor.status = 'unknown';
    if (['session', 'timeline', 'run'].includes(change)) descriptor[change + '_id'] = 'other';
    if (change === 'hash') descriptor.sha256 = '0'.repeat(64);
    if (change === 'bytes') descriptor.bytes++;
    if (change === 'missing-start') source.journal = [];
    if (change === 'missing-witness') source.manifest.content_identity.raw_file_sha256 = {};
    assert.throws(() => readRecordedLaunch(source, trajectory), /recording_replay_/);
  });
}
test('private transport is explicitly diagnostic while public raw remains readable and exact', context => {
  const { source, trajectory } = fixture(), launch = readRecordedLaunch(source, trajectory);
  const directory = mkdtempSync(join(tmpdir(), 'recorded-replay-'));
  context.after(() => rmSync(directory, { recursive: true, force: true }));
  const filename = join(directory, 'replay.jsonl');
  const recorder = new EpisodeRecorder(filename, { schema: 'spireagent/managed-diagnostic-log-1' });
  const sink = privateRecordingSink(recorder, launch);
  sink.append('env_request', { seed: launch.seed, initial_progress: launch.initialProgress });
  sink.appendRaw('env_response', JSON.stringify({ actual_seed: launch.seed }));
  const publicRaw = '{ "public": "原样公开字节" }';
  sink.appendRaw('env_response', publicRaw, { command: 'capture' });
  recorder.close({ completed: false });
  const rows = readRecord(filename), content = readFileSync(filename, 'utf8');
  assert.equal(rows[0].schema, 'spireagent/managed-diagnostic-log-1');
  assert.equal(rows.filter(row => row.kind === 'private_launch_transport').length, 2);
  assert.equal(rows.find(row => row.kind === 'env_response').raw, publicRaw);
  assert.equal(content.includes(launch.seed), false);
  assert.equal(content.includes('progress_json'), false);
});

test('short numeric private seed cannot corrupt diagnostic counts or JSON structure', () => {
  const result = publicRecordingReport({ seed: '1', episode_identity: { actual_seed: '1' }, planned_points: 140,
    matching_points: 11, completed: false, error: 'launch seed 1 failed' }, { seed: '1' });
  assert.equal(result.planned_points, 140); assert.equal(result.matching_points, 11);
  assert.equal(result.seed, undefined); assert.equal(result.episode_identity, undefined);
  assert.equal(result.error, 'launch seed [private-launch-original] failed');
});

import * as core from '../../components/connector/sdk/typescript/dist/p1Global/index.js';
import { selectorForwardPair } from '../../components/connector/sdk/typescript/test/fixtures/selector-forward.mjs';
import { matchReferenceChoice } from '../../components/policy-runtime/dist/p1-global/trajectory.js';
import { matchRecordedChoice } from '../recorded-replay-preferences.mjs';
function upgradePair() {
  const { outer } = selectorForwardPair();
  const observation = core.reduce(core.createSession('m060', 'managed'), outer).observation;
  observation.scene.content.surface.showing_upgrade_previews = true;
  const actual = structuredClone(observation);
  actual.scene.content.surface.showing_upgrade_previews = false;
  return { reference: { observation, chosen: observation.catalog[0].choice_id }, actual };
}
test('M060 ignores only the Boolean upgrade-view preference in recorded replay, preserving originals and raw difference', () => {
  const { reference, actual } = upgradePair(), before = JSON.stringify([reference, actual]);
  assert.equal(matchReferenceChoice(reference, actual).verdict, 'different');
  const match = matchRecordedChoice(reference, actual);
  assert.equal(match.verdict, 'equal');
  assert.equal(match.choice_id, actual.catalog[0].choice_id);
  assert.equal(match.comparison_exception.id, 'M060');
  assert.equal(match.comparison_exception.original_comparison.verdict, 'different');
  assert.equal(JSON.stringify([reference, actual]), before);
});
for (const field of ['preview', 'card', 'catalog', 'missing-toggle', 'invalid-toggle', 'other-scene']) {
  test(`M060 still rejects ${field} differences`, () => {
    const { reference, actual } = upgradePair();
    if (field === 'preview') actual.scene.content.surface.preview_options[0].after.description += ' changed';
    if (field === 'card') actual.scene.content.surface.cards[0].description += ' changed';
    if (field === 'catalog') actual.catalog[0].label += ' changed';
    if (field === 'missing-toggle') delete actual.scene.content.surface.showing_upgrade_previews;
    if (field === 'invalid-toggle') actual.scene.content.surface.showing_upgrade_previews = 'false';
    if (field === 'other-scene') actual.scene.native_kind = reference.observation.scene.native_kind = 'deck_card_selection';
    assert.notEqual(matchRecordedChoice(reference, actual).verdict, 'equal');
  });
}

import { locateRecordedNativeInput } from '../recorded-native-input.mjs';
import { buildRecordedReplayPlan } from '../recorded-replay-plan.mjs';
function nativeToggle() {
  const { outer } = selectorForwardPair();
  Object.assign(outer, { capture_status: 'complete', missing: [] });
  const bound_action = outer.snapshot.bound_actions.actions.find(action => action.bound_action_id === 'toggle-binding');
  return { basis: outer, bound_action, original_terminal_phase: 'finished' };
}
test('unlabelled native input locates the existing current catalog member by one full public graph', () => {
  const witness = nativeToggle();
  const actual = JSON.parse(JSON.stringify(witness.basis).replaceAll('toggle-binding', 'current-toggle')
    .replaceAll('"toggle"', '"new-toggle"').replaceAll('owner-one', 'current-owner').replaceAll('task-one', 'current-task'));
  const before = JSON.stringify([witness, actual]);
  const input = locateRecordedNativeInput(witness, actual);
  assert.equal(input.bound_action_id, 'current-toggle');
  assert.ok(actual.snapshot.bound_actions.actions.includes(input));
  assert.equal(JSON.stringify([witness, actual]), before);
});
for (const problem of ['unknown', 'incomplete', 'stale', 'world', 'missing', 'duplicate']) {
  test(`unlabelled native input refuses ${problem}`, () => {
    const witness = nativeToggle(), actual = structuredClone(witness.basis);
    if (problem === 'unknown') witness.original_terminal_phase = 'unknown';
    if (problem === 'incomplete') witness.basis.capture_status = 'partial';
    if (problem === 'stale') witness.bound_action = { ...witness.bound_action, bound_action_id: 'stale' };
    if (problem === 'world') actual.snapshot.persistent.content.player.hp--;
    if (problem === 'missing') actual.snapshot.bound_actions.actions = actual.snapshot.bound_actions.actions.filter(a => a.bound_action_id !== 'toggle-binding');
    if (problem === 'duplicate') actual.snapshot.bound_actions.actions.push({ ...witness.bound_action, bound_action_id: 'duplicate' });
    actual.snapshot.bound_actions.materialized_count = actual.snapshot.bound_actions.total_count = actual.snapshot.bound_actions.actions.length;
    assert.throws(() => locateRecordedNativeInput(witness, actual), /recorded_native_input/);
  });
}
function planFixture() {
  const witness = nativeToggle();
  const observation = core.reduce(core.createSession('plan', 'reference'), witness.basis).observation;
  const ref = { payload_ref: 'basis.json', sha256: 'fixture' };
  const rows = [
    { kind: 'decision_accepted', sequence: 1, decision_id: 'good', event_id: 'good', bound_action: witness.bound_action,
      basis: ref, native_receipt: { kind: 'semantic_ui_accepted' } },
    { kind: 'lifecycle', sequence: 2, decision_id: 'good', event_id: 'done', phase: 'finished', successor_basis: ref },
    { kind: 'decision_accepted', sequence: 3, decision_id: 'unknown', event_id: 'unknown', bound_action: witness.bound_action,
      basis: ref, native_receipt: { kind: 'semantic_ui_accepted' } },
    { kind: 'lifecycle', sequence: 4, decision_id: 'unknown', event_id: 'unknown-end', phase: 'unknown', successor_basis: ref }
  ];
  const original = { events: rows, bytes() { return Buffer.from(JSON.stringify(witness.basis)); } };
  const conversion = { complete: false, whole_run_recording_complete: false, raw_unknown_count: 1,
    blocking_issues: [{ decision_id: 'good', reason: 'accepted_action_has_no_p1_choice' },
      { decision_id: 'unknown', reason: 'accepted_action_unknown' }],
    actions: [
      { decision_id: 'good', capture: 0, reason: 'accepted_action_has_no_p1_choice', result: { status: 'completed' } },
      { decision_id: 'unknown', capture: 2, reason: 'accepted_action_unknown', result: { status: 'unknown' } }],
    captures: [1, 2, 3].map(sequence => ({ sequence, complete: true, observation })), native_cancellations: [] };
  return { original, conversion };
}
test('replay plan keeps known native input unlabelled and stops before an original unknown', () => {
  const { original, conversion } = planFixture(), before = JSON.stringify(conversion);
  const plan = buildRecordedReplayPlan(original, conversion);
  assert.equal(plan.points[0].native_bridge.decision_id, 'good');
  assert.equal(plan.points[0].native_bridge.p1_compared, false);
  assert.equal(plan.points[0].chosen, null);
  assert.equal(plan.points.at(-1).bounded_end, true);
  assert.equal(plan.replay_scope.stop_before_sequence, 3);
  assert.equal(plan.points.some(p => p.sequence >= 3), false);
  assert.equal(JSON.stringify(conversion), before);
});

import { replayNativeTrajectory } from '../p1-native-replay.mjs';
for (const delivery of ['delivered', 'unknown']) {
  test(`native bridge ${delivery} never becomes a matching P1 action and never retries`, async () => {
    const witness = nativeToggle(), observation = core.reduce(core.createSession('bridge', 'reference'), witness.basis).observation;
    let steps = 0, closes = 0;
    const driver = { async start() {}, newMutationId() { return 'bridge-once'; },
      async request(request) {
        if (['reset', 'episode_identity'].includes(request.command)) return { identity: {} };
        if (request.command === 'capture') return { full: { basis: witness.basis } };
        if (request.command === 'step') { steps++; return { receipt: { delivery } }; }
        throw new Error('unexpected command');
      }, async close() { closes++; return { status: 'closed' }; } };
    const result = await replayNativeTrajectory({ trajectory: { points: [
      { observation, chosen: null, native_bridge: { ...witness, reason: 'accepted_action_has_no_p1_choice' } },
      { observation, chosen: null, bounded_end: true }] }, driver, record: { append() {} }, seed: 'fixture', initialProgress: {} });
    assert.equal(steps, 1); assert.equal(closes, 1);
    assert.equal(result.planned_points, 0); assert.equal(result.matching_choices, 0);
    assert.equal(result.full_p1_equivalence_complete, false); assert.equal(result.completed, false);
    assert.equal(result.unlabelled_native_inputs_replayed, Number(delivery === 'delivered'));
    assert.equal(result.matching_points, 0);
    assert.equal(result.bounded_checkpoint_matching, delivery === 'delivered');
    assert.equal(result.bounded_prefix_completed, delivery === 'delivered');
    assert.equal(result.unknown_delivery, delivery === 'unknown');
  });
}

test('journal pause/resume cannot qualify a continuous replay across unrecorded gameplay', () => {
  const { original, conversion } = planFixture();
  original.events.forEach((row, index) => { row.recorded_at = `2026-10-28T00:00:0${index + 1}Z`; });
  original.journal = [{ kind: 'recording_paused', recorded_at: '2026-10-28T00:00:02.500Z' }];
  conversion.blocking_issues = conversion.blocking_issues.filter(issue => issue.decision_id === 'good');
  conversion.actions[1].result.status = 'completed';
  const plan = buildRecordedReplayPlan(original, conversion);
  assert.equal(plan.replay_scope.stop_before_sequence, 3);
  assert.equal(plan.points.at(-1).bounded_end, true);
  original.journal[0].recorded_at = 'unavailable';
  assert.throws(() => buildRecordedReplayPlan(original, conversion), /no_safe_known_prefix/);
});

test('qualified cancellation stays a no-input checkpoint before a later gap', () => {
  const { original, conversion } = planFixture();
  conversion.actions[0].native_disposition = { disposition: 'cancelled', kind: 'rest_smith_cancel' };
  conversion.blocking_issues = conversion.blocking_issues.filter(issue => issue.decision_id !== 'good');
  conversion.captures[0].event_id = 'before'; conversion.captures[0].public_basis = { sha256: 'before-sha' };
  conversion.captures[1].event_id = 'after'; conversion.captures[1].public_basis = { sha256: 'after-sha' };
  conversion.native_cancellations = [{ continuity_checkpoints: [
    { source_event_id: 'before', source_public_basis: { sha256: 'before-sha' } },
    { source_event_id: 'after', source_public_basis: { sha256: 'after-sha' } }] }];
  const plan = buildRecordedReplayPlan(original, conversion);
  assert.equal(plan.points.filter(point => point.replay_checkpoint).length, 2);
  assert.equal(plan.points.some(point => point.native_bridge), false);
  assert.equal(plan.replay_scope.stop_before_sequence, 3);
});

test('cancellation checkpoint sharing the next action pre-capture compares before that action', () => {
  const { original, conversion } = planFixture();
  conversion.actions[0].match_status = 'matched'; conversion.actions[0].choice_id = 'c1';
  conversion.blocking_issues = conversion.blocking_issues.filter(issue => issue.decision_id !== 'good');
  conversion.captures[0].event_id = 'pre'; conversion.captures[0].public_basis = { sha256: 'pre-sha' };
  conversion.native_cancellations = [{ continuity_checkpoints: [
    { source_event_id: 'pre', source_public_basis: { sha256: 'pre-sha' } }] }];
  const plan = buildRecordedReplayPlan(original, conversion);
  assert.equal(plan.points[0].replay_checkpoint, true);
  assert.equal(plan.points[1].chosen, 'c1');
  assert.equal(plan.points[0].sequence, plan.points[1].sequence);
});

test('native locator shares exactly M060 and still rejects changed upgrade content', () => {
  const witness = nativeToggle(), actual = structuredClone(witness.basis);
  witness.basis.snapshot.interaction.content.surface.showing_upgrade_previews = true;
  actual.snapshot.interaction.content.surface.showing_upgrade_previews = false;
  assert.equal(locateRecordedNativeInput(witness, actual).bound_action_id, 'toggle-binding');
  actual.snapshot.interaction.content.surface.preview_options[0].after.description += ' changed';
  assert.throws(() => locateRecordedNativeInput(witness, actual), /public_graph_difference|locator_incomplete/);
});
