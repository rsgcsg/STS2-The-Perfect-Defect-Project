import test from 'node:test';
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { convertOriginals } from '../source4-to-p1.mjs';
import { sourceRunFixture, menuBasis, position, transition } from './source4-run-scope-fixture.mjs';

const root = fileURLToPath(new URL('../..', import.meta.url));
const sdkRoot = resolve(process.env.SOURCE4_TEST_SDK_ROOT ?? resolve(root, 'components/connector/sdk/typescript/dist'));
const sdk = await import(pathToFileURL(resolve(sdkRoot, 'index.js')));
const core = await import(pathToFileURL(resolve(sdkRoot, 'p1Global/index.js')));
function convert(source, options = {}) {
  const before = JSON.stringify({ epochs: source.epochs, observations: source.observations,
    inputs: source.inputs, boundaries: source.boundaries });
  const report = convertOriginals(source, sdk, core, { hostKind: 'reference', ...options });
  assert.equal(JSON.stringify({ epochs: source.epochs, observations: source.observations,
    inputs: source.inputs, boundaries: source.boundaries }), before);
  assert.deepEqual(report.native_trace, source.inputs);
  return report;
}
test('explicit run scope keeps menu originals outside P1 and the first pre-Launch active publication inside', () => {
  const { source } = sourceRunFixture();
  const legacy = convert(source); assert.equal(legacy.complete, false);
  const report = convert(source, { runContinuityId: 'run-exact' });
  assert.equal(report.complete, true);
  assert.equal(report.captures[0].scope.disposition, 'outside');
  assert.equal(report.captures[1].scope.disposition, 'inside');
  assert.equal(report.captures[1].classification, 'p1_initial_boundary');
  assert.deepEqual(report.actions.map(action => action.choice.kind), ['combat.end_turn']);
  assert.equal(report.whole_run.complete, true);
});
test('same-epoch prelude needs exact inactive public facts and the native Launch cut, not the menu label', () => {
  const f = sourceRunFixture({ sameEpochPrelude: true });
  const report = convert(f.source, { runContinuityId: 'run-exact' });
  assert.equal(report.complete, true);
  assert.equal(report.captures[1].scope.reason, 'original_prelaunch_public_state_has_no_active_run');
  assert.equal(report.captures[2].scope.disposition, 'inside');
  // A state with the same menu label after the original Launch cut cannot be
  // thrown away just because P1 does not support that scene.
  f.publication(menuBasis(), 'run', f.firstIndex + 2);
  const late = convert(f.source, { runContinuityId: 'run-exact' });
  assert.equal(late.complete, false); assert.equal(late.captures.at(-1).scope.disposition, 'inside');
  assert.equal(late.captures.at(-1).status, 'unresolved');
});
test('partial and absent selected-run bases are retained even before the first complete active view', () => {
  for (const missing of [false, true]) {
    const f = sourceRunFixture({ sameEpochPrelude: true });
    const prelude = f.source.observations.find(row => row.epoch_id === 'run');
    if (missing) { prelude.captured_parts.public_basis = null;
      prelude.captured_parts.public_basis_missing_reason = 'original_basis_unavailable'; }
    else {
      const partial = menuBasis(); partial.snapshot.completeness.status = 'partial';
      partial.snapshot.completeness.missing = ['persistent_visible_state'];
      prelude.captured_parts = f.parts(partial, 'run');
    }
    const report = convert(f.source, { runContinuityId: 'run-exact' });
    assert.equal(report.complete, false); assert.notEqual(report.captures[1].scope.disposition, 'outside');
    assert.ok(report.unresolved_contexts.some(row => row.source_ref === '1'));
  }
});
test('unknown delivery and a post-Launch input at the same publication watermark stay in the run', () => {
  const f = sourceRunFixture(); f.source.inputs[0].outcome.delivery = 'unknown';
  const unknown = convert(f.source, { runContinuityId: 'run-exact' });
  assert.equal(unknown.complete, false); assert.equal(unknown.unresolved_inputs[0].input_id, 'input-first-p1');
  assert.equal(unknown.whole_run.original_unknown_inputs, 1);
  const prelude = sourceRunFixture({ sameEpochPrelude: true });
  prelude.source.inputs[0].captured_parts = prelude.parts(menuBasis(), 'run');
  const postCut = convert(prelude.source, { runContinuityId: 'run-exact' });
  assert.equal(postCut.complete, false);
  assert.equal(postCut.captures.find(row => row.source_kind === 'source_input').scope.disposition, 'inside');
});
test('all publications at and after native terminal remain, including unsupported same-continuity state', () => {
  const f = sourceRunFixture();
  const finalBasis = JSON.parse(f.source.bytes(f.source.observations.at(-1).captured_parts.public_basis.payload_ref));
  f.publication({ snapshot: finalBasis.snapshot, reads: finalBasis.reads }, 'run', f.firstIndex + 2);
  const repeated = convert(f.source, { runContinuityId: 'run-exact' });
  assert.equal(repeated.complete, true); assert.equal(repeated.whole_run.complete, true);
  assert.equal(repeated.captures.at(-1).scope.disposition, 'inside');
  f.publication(menuBasis(), 'run', f.firstIndex + 3);
  const unsupported = convert(f.source, { runContinuityId: 'run-exact' });
  assert.equal(unsupported.complete, false); assert.equal(unsupported.scope.natural_terminal_observed, true);
  assert.equal(unsupported.captures.at(-1).scope.disposition, 'inside');
});
test('bounded completeness does not invent a new native start or terminal', () => {
  for (const change of ['saved', 'unknown-origin', 'no-launch', 'no-native-terminal', 'mid-run-attach', 'no-p1-terminal']) {
    const f = sourceRunFixture({ terminal: change !== 'no-p1-terminal' });
    if (change === 'saved' || change === 'unknown-origin') {
      const origin = change === 'saved' ? 'saved' : 'unknown';
      f.source.epochs[1].transition.start_provenance = origin;
      f.source.boundaries[0].transition.start_provenance = origin;
    }
    if (change === 'no-launch') f.source.boundaries = f.source.boundaries.filter(row => row.kind !== 'launch');
    if (change === 'no-native-terminal') f.source.boundaries = f.source.boundaries.filter(row => row.kind !== 'terminal');
    if (change === 'mid-run-attach') {
      const epoch = f.source.epochs[1]; epoch.previous_epoch_id = null; epoch.transition = null; epoch.predecessor_seal = null;
      f.source.epochs = [epoch]; f.source.observations = f.source.observations.filter(row => row.epoch_id === 'run');
      f.source.boundaries = f.source.boundaries.filter(row => row.kind !== 'launch');
    }
    const report = convert(f.source, { runContinuityId: 'run-exact' });
    assert.equal(report.complete, true, change); assert.equal(report.whole_run.complete, false, change);
    assert.ok(report.whole_run.missing.length > 0, change);
  }
});
test('run pause and original gap prevent whole-run completeness', () => {
  for (const change of ['pause', 'gap']) {
    const f = sourceRunFixture();
    if (change === 'pause') f.source.boundaries.at(-1).paused_intervals.push({ epoch_id: 'run',
      stream_generation: 'generation-run', after_index: '1', through_index: '2',
      after_input_ordinal: '0', through_input_ordinal: '1', reason: 'recording_paused' });
    else f.source.observations.find(row => row.epoch_id === 'run').gap_after_index = '0';
    const report = convert(f.source, { runContinuityId: 'run-exact' });
    assert.equal(report.complete, false, change); assert.equal(report.whole_run.complete, false, change);
  }
});
test('multiple runs require the supplied continuity; other-run originals remain visible and cannot become its choices', () => {
  const f = sourceRunFixture();
  f.source.epochs.push(f.epoch('run-two', 'second-run', 'run', transition('setup_handoff', 'run-exact', 'second-run', 'new')));
  f.publication(f.combat, 'run-two', 1);
  f.source.boundaries.at(-1).position = position('run-two', 1);
  f.source.boundaries.at(-1).paused_intervals.push({ epoch_id: 'run-two', stream_generation: 'generation-run-two',
    after_index: '0', through_index: '1', after_input_ordinal: '1', through_input_ordinal: '1', reason: 'recording_paused' });
  const first = convert(f.source, { runContinuityId: 'run-exact' });
  assert.equal(first.complete, true); assert.equal(first.whole_run.complete, true);
  assert.equal(first.captures.at(-1).scope.disposition, 'outside');
  const second = convert(f.source, { runContinuityId: 'second-run' });
  assert.deepEqual(second.actions, []); assert.equal(second.whole_run.complete, false);
  assert.throws(() => convert(f.source, { runContinuityId: 'no-such-original-run' }), /source4_run_continuity_not_found/);
  assert.throws(() => convert(f.source, { runContinuityId: '' }), /source4_run_continuity_required/);
  f.source.epochs.push({ ...structuredClone(f.source.epochs[1]), epoch_id: 'run-again' });
  assert.throws(() => convert(f.source, { runContinuityId: 'run-exact' }), /source4_run_continuity_not_unique/);
});

const bundle = resolve(root, 'components/evidence/tests/fixtures/source_session_v4_p1/bundle');
function cli(extra = []) {
  return spawnSync(process.execPath, [resolve(root, 'tools/source4-to-p1.mjs'), '--bundle', bundle,
    '--python', process.env.PYTHON ?? 'python', '--host-kind', 'reference', '--sdk-root', sdkRoot, ...extra],
  { encoding: 'utf8', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } });
}
test('real verified-bundle CLI preserves legacy mode and explicitly selects an original bounded run', () => {
  const manifestPath = resolve(bundle, 'source-session-bundle-manifest.json');
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  const inventory = () => Object.fromEntries([manifestPath, ...Object.keys(manifest.content_identity.raw_file_sha256)
    .map(path => resolve(bundle, 'raw', path))].map(path => [path,
    createHash('sha256').update(readFileSync(path)).digest('hex')]));
  const original = inventory();
  const legacy = cli(); assert.equal(legacy.status, 0, legacy.stderr);
  const old = JSON.parse(legacy.stdout); assert.equal(old.schema, 'sts2.p1/source4-conversion-report-2');
  assert.equal(old.complete, true); assert.equal('whole_run' in old, false);
  const scoped = cli(['--run-continuity', 'title-continuity']); assert.equal(scoped.status, 0, scoped.stderr);
  const report = JSON.parse(scoped.stdout); assert.equal(report.schema, 'sts2.p1/source4-conversion-report-3');
  assert.equal(report.complete, true); assert.equal(report.whole_run.complete, false);
  assert.equal(report.scope.game_continuity_id, 'title-continuity');
  assert.deepEqual(inventory(), original);
});
test('CLI refuses a missing or duplicate run selector rather than selecting the last run', () => {
  for (const [flags, reason] of [[['--run-continuity', 'foreign'], /source4_run_continuity_not_found/],
    [['--run-continuity'], /usage:/],
    [['--run-continuity', 'title-continuity', '--run-continuity', 'title-continuity'], /usage:/]]) {
    const child = cli(flags); assert.notEqual(child.status, 0); assert.equal(child.stdout, '');
    assert.match(child.stderr, reason);
  }
});
