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
