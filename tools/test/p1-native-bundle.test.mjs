import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { gunzipSync } from 'node:zlib';
import { readVerifiedP1NativeBundle } from '../p1-native-bundle.mjs';

// Original production RecordingSessionStore/P1NativeSessionBundlePacker bytes,
// frozen here so this portable reader test needs neither .NET nor a running game.
// Bundle content ID: ad485be8e5a7d29e35f57b133de5ad81a704806fbbc2953197972ab075104820.
// Producer: P1NativeRecordingTests.ProductionStoreAndPackerPreserveExactBasisAndTwoSemanticActionsWithoutPickupLabels.
// Regenerate: STS2_P1_FIXTURE_OUT=<new-directory> dotnet test
// components/annotator/tests/STS2HumanAnnotator.Core.Tests/STS2HumanAnnotator.Core.Tests.csproj
// --filter FullyQualifiedName~P1NativeRecordingTests (then freeze the resulting bundle bytes).
const packedFixture = `
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
`;
const sha = value => createHash('sha256').update(value).digest('hex');
function fixture(run) {
  const directory = mkdtempSync(resolve(tmpdir(), 'p1-native-bundle-'));
  try {
    const files = JSON.parse(gunzipSync(Buffer.from(packedFixture, 'base64')).toString('utf8'));
    for (const [name, value] of Object.entries(files)) {
      const path = resolve(directory, name); mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, Buffer.from(value, 'base64'));
    }
    run(directory, files);
  } finally { rmSync(directory, { recursive: true, force: true }); }
}
const readJson = (directory, name) => JSON.parse(readFileSync(resolve(directory, name), 'utf8'));
const writeJson = (directory, name, value) => writeFileSync(resolve(directory, name), JSON.stringify(value));
const readEvents = directory => readFileSync(resolve(directory, 'raw/p1-native-events.jsonl'), 'utf8').trimEnd().split('\n').map(JSON.parse);
const writeEvents = (directory, rows) => writeFileSync(resolve(directory, 'raw/p1-native-events.jsonl'), rows.map(JSON.stringify).join('\n') + '\n');
function outerChecksums(directory, originalFiles) {
  writeFileSync(resolve(directory, 'checksums.sha256'), Object.keys(originalFiles).filter(name => name !== 'checksums.sha256')
    .sort().map(name => `${sha(readFileSync(resolve(directory, name)))}  ${name}\n`).join(''));
}
function reseal(directory, originalFiles, { closeStreams = true } = {}) {
  if (closeStreams) {
    const close = readJson(directory, 'raw/p1-native-close.json');
    close.events_sha256 = sha(readFileSync(resolve(directory, 'raw/p1-native-events.jsonl')));
    close.run_journal_sha256 = sha(readFileSync(resolve(directory, 'raw/run-journal.jsonl')));
    writeJson(directory, 'raw/p1-native-close.json', close);
  }
  const identity = readJson(directory, 'content-identity.json');
  for (const name of Object.keys(identity.raw_file_sha256))
    identity.raw_file_sha256[name] = sha(readFileSync(resolve(directory, 'raw', name)));
  writeJson(directory, 'content-identity.json', identity);
  const manifest = readJson(directory, 'session-bundle-manifest.json');
  manifest.content_identity = identity;
  manifest.bundle_content_id = sha(readFileSync(resolve(directory, 'content-identity.json')));
  writeJson(directory, 'session-bundle-manifest.json', manifest);
  outerChecksums(directory, originalFiles);
}

test('production sealed bundle exposes the original streams and basis bytes without promoting origin', () => fixture((directory, originalFiles) => {
  const result = readVerifiedP1NativeBundle(directory);
  assert.equal(result.bundle, realpathSync(directory));
  assert.equal(result.manifest.content_identity.declared_origin, 'agent_native_ui');
  assert.deepEqual(result.manifest.content_identity.human_origin_attestation,
    { attested: false, method: 'explicit_owner_pack', machine_verifiable: false });
  assert.ok(result.events.some(row => row.kind === 'decision_accepted'));
  assert.ok(result.events.some(row => row.kind === 'lifecycle'));
  for (const row of result.events) for (const name of ['basis', 'successor_basis']) if (row[name]) {
    assert.deepEqual(result.bytes(row[name].payload_ref), Buffer.from(originalFiles[`raw/${row[name].payload_ref}`], 'base64'));
  }
  assert.deepEqual(result.journal, readFileSync(resolve(directory, 'raw/run-journal.jsonl'), 'utf8').trimEnd().split('\n').filter(Boolean).map(JSON.parse));
}));

test('checksums reject changed bytes and undeclared files', () => {
  fixture(directory => {
    writeFileSync(resolve(directory, 'raw/p1-native-events.jsonl'), ' ');
    assert.throws(() => readVerifiedP1NativeBundle(directory), /original_bytes_changed/);
  });
  fixture(directory => {
    writeFileSync(resolve(directory, 'extra.json'), '{}');
    assert.throws(() => readVerifiedP1NativeBundle(directory), /checksum_inventory_mismatch/);
  });
});

test('a repaired checksum cannot replace the original content identity', () => fixture((directory, originalFiles) => {
  const manifest = readJson(directory, 'session-bundle-manifest.json');
  manifest.content_identity.timeline_id = 'foreign';
  writeJson(directory, 'session-bundle-manifest.json', manifest); outerChecksums(directory, originalFiles);
  assert.throws(() => readVerifiedP1NativeBundle(directory), /content_identity_mismatch/);
}));

test('stream ordering, terminal binding and Close counts survive repaired outer hashes', () => {
  for (const [mutate, reason] of [
    [rows => { rows[1].sequence = rows[0].sequence; }, /row_sequence/],
    [rows => { rows[0].timeline_id = 'foreign'; }, /row_identity/],
    [rows => { rows.find(row => row.kind === 'lifecycle').action_witness_id = 'foreign'; }, /terminal_without_accepted/],
  ]) fixture((directory, originalFiles) => {
    const rows = readEvents(directory); mutate(rows); writeEvents(directory, rows); reseal(directory, originalFiles);
    assert.throws(() => readVerifiedP1NativeBundle(directory), reason);
  });
  fixture((directory, originalFiles) => {
    const close = readJson(directory, 'raw/p1-native-close.json'); close.event_count++;
    writeJson(directory, 'raw/p1-native-close.json', close); reseal(directory, originalFiles);
    assert.throws(() => readVerifiedP1NativeBundle(directory), /closed_stream_accounting/);
  });
});

test('Close must bind the actual original stream hashes', () => fixture((directory, originalFiles) => {
  const close = readJson(directory, 'raw/p1-native-close.json'); close.events_sha256 = '0'.repeat(64);
  writeJson(directory, 'raw/p1-native-close.json', close); reseal(directory, originalFiles, { closeStreams: false });
  assert.throws(() => readVerifiedP1NativeBundle(directory), /closed_stream_hash/);
}));

test('basis references bind original payload size and snapshot identity', () => {
  for (const [mutate, reason] of [
    [basis => { basis.byte_count++; }, /basis_bytes_mismatch/],
    [basis => { basis.snapshot_id = 'foreign'; }, /basis_snapshot_identity/],
  ]) fixture((directory, originalFiles) => {
    const rows = readEvents(directory), row = rows.find(value => value.basis); mutate(row.basis);
    writeEvents(directory, rows); reseal(directory, originalFiles);
    assert.throws(() => readVerifiedP1NativeBundle(directory), reason);
  });
});

test('a missing basis remains explicit for semantic conversion', () => fixture((directory, originalFiles) => {
  const rows = readEvents(directory), row = rows.find(value => value.basis); row.basis = null;
  row.basis_missing_reason = 'original_capture_unavailable';
  writeEvents(directory, rows); reseal(directory, originalFiles);
  const result = readVerifiedP1NativeBundle(directory);
  assert.equal(result.events.find(value => value.event_id === row.event_id).basis, null);
}));

test('unknown disposition and noncontiguous journal sequence remain original facts', () => fixture((directory, originalFiles) => {
  const rows = readEvents(directory), terminal = rows.find(value => value.kind === 'lifecycle');
  terminal.phase = 'unknown'; terminal.successor_basis = null;
  terminal.successor_missing_reason = 'native_terminal_unobserved';
  writeEvents(directory, rows);
  const journal = readFileSync(resolve(directory, 'raw/run-journal.jsonl'), 'utf8').trimEnd().split('\n').map(JSON.parse);
  journal[0].sequence = 7;
  writeFileSync(resolve(directory, 'raw/run-journal.jsonl'), journal.map(JSON.stringify).join('\n') + '\n');
  reseal(directory, originalFiles);
  const result = readVerifiedP1NativeBundle(directory);
  assert.equal(result.events.find(value => value.event_id === terminal.event_id).phase, 'unknown');
  assert.equal(result.journal[0].sequence, 7);
}));

test('engineering provenance cannot carry a positive Human attestation', () => fixture((directory, originalFiles) => {
  const identity = readJson(directory, 'content-identity.json'); identity.human_origin_attestation.attested = true;
  writeJson(directory, 'content-identity.json', identity); reseal(directory, originalFiles);
  assert.throws(() => readVerifiedP1NativeBundle(directory), /origin_attestation/);
}));

test('bytes returned after verification still belong to the sealed original inventory', () => fixture(directory => {
  const result = readVerifiedP1NativeBundle(directory), reference = result.events.find(row => row.basis).basis;
  writeFileSync(resolve(directory, 'raw', reference.payload_ref), '{}');
  assert.throws(() => result.bytes(reference.payload_ref), /original_bytes_changed/);
  assert.throws(() => result.bytes('../content-identity.json'), /path_invalid/);
}));
