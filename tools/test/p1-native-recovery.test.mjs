import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { readVerifiedP1NativeBundle } from '../p1-native-bundle.mjs';
import { convertP1NativeBundle } from '../p1-native-to-p1.mjs';

// Frozen output of the actual C# Store -> recovery -> packer fixture, never
// independently invented bundle DTOs. Regenerate through the producer named
// by the fixture with STS2_P1_RECOVERY_FIXTURE_OUT=<new-directory>.
const fixture = JSON.parse(readFileSync(new URL('./fixtures/p1-native-recovered-bundle.json', import.meta.url)));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
async function withFixture(run) {
  const root = mkdtempSync(join(tmpdir(), 'p1-recovery-reader-'));
  const files = JSON.parse(gunzipSync(Buffer.from(fixture.gzip_base64, 'base64')));
  try {
    for (const [name, value] of Object.entries(files)) {
      const file = join(root, name); mkdirSync(dirname(file), { recursive: true });
      writeFileSync(file, Buffer.from(value, 'base64'));
    }
    await run(root, files);
  } finally { rmSync(root, { recursive: true, force: true }); }
}
const json = (root, name) => JSON.parse(readFileSync(join(root, name)));
const write = (root, name, value) => writeFileSync(join(root, name), JSON.stringify(value));
function reseal(root, files) {
  const close = json(root, 'raw/p1-native-close.json');
  close.events_sha256 = hash(readFileSync(join(root, 'raw/p1-native-events.jsonl')));
  close.run_journal_sha256 = hash(readFileSync(join(root, 'raw/run-journal.jsonl')));
  write(root, 'raw/p1-native-close.json', close);
  const identity = json(root, 'content-identity.json');
  for (const name of Object.keys(identity.raw_file_sha256))
    identity.raw_file_sha256[name] = hash(readFileSync(join(root, 'raw', name)));
  write(root, 'content-identity.json', identity);
  const manifest = json(root, 'session-bundle-manifest.json');
  manifest.content_identity = identity;
  manifest.bundle_content_id = hash(readFileSync(join(root, 'content-identity.json')));
  write(root, 'session-bundle-manifest.json', manifest);
  writeFileSync(join(root, 'checksums.sha256'), Object.keys(files).filter(name => name !== 'checksums.sha256')
    .sort().map(name => `${hash(readFileSync(join(root, name)))}  ${name}\n`).join(''));
}

test('production recovered prefix preserves one N, one unknown tail and no natural end', () => withFixture(async root => {
  const source = readVerifiedP1NativeBundle(root);
  assert.equal(source.manifest.bundle_content_id, fixture.bundle_content_id);
  assert.equal(source.recovery.p1_unknowns_added, 1);
  const report = await convertP1NativeBundle({ bundle: root, includeTargets: true, targetProjection: 'native-timeline-targets-1' });
  assert.equal(report.raw_unknown_count, 1);
  assert.equal(report.target_output.points.filter(p => p.mask.N).length, 1);
  assert.equal(report.whole_run_recording_complete, false);
  assert.equal(report.target_output.end_target, null);
  assert.ok(report.target_output.edges.every(e => !e.eligible));
  assert.ok(report.blocking_issues.some(e => e.reason === 'accepted_action_unknown'));
}));

for (const change of ['successful_tail', 'changed_original', 'wrong_unknown_count', 'wrong_journal'])
  test(`recovery independently rejects ${change} after all outer checksums are resealed`, () => withFixture((root, files) => {
    if (change === 'successful_tail' || change === 'changed_original') {
      const file = join(root, 'raw/p1-native-events.jsonl');
      const rows = readFileSync(file, 'utf8').trimEnd().split('\n').map(JSON.parse);
      if (change === 'successful_tail') rows.at(-1).phase = 'finished';
      else rows[0].native_action_type = 'changed_original_action';
      writeFileSync(file, rows.map(JSON.stringify).join('\n') + '\n');
    } else if (change === 'wrong_unknown_count') {
      const receipt = json(root, 'raw/recording-recovery.json'); receipt.p1_unknowns_added++;
      write(root, 'raw/recording-recovery.json', receipt);
    } else {
      const file = join(root, 'raw/run-journal.jsonl');
      const rows = readFileSync(file, 'utf8').trimEnd().split('\n').map(JSON.parse);
      rows.at(-2).detail = 'invented recovery'; writeFileSync(file, rows.map(JSON.stringify).join('\n') + '\n');
    }
    reseal(root, files);
    assert.throws(() => readVerifiedP1NativeBundle(root), /p1_native_bundle:recovery_/);
  }));
