import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { gunzipSync } from 'node:zlib';
import { readVerifiedP1NativeBundle, readVerifiedP1NativePrefix } from '../p1-native-bundle.mjs';
import { convertNativeSemanticOriginals } from '../p1-native-to-p1.mjs';
import * as sdk from '../../components/connector/sdk/typescript/dist/index.js';
import * as core from '../../components/connector/sdk/typescript/dist/p1Global/index.js';

// Original production RecordingSessionStore/P1NativeSessionBundlePacker bytes,
// frozen here so this portable reader test needs neither .NET nor a running game.
// Bundle content ID: b5a853d9694edff7d9b7cb28bfeb2d090ee834da3e81a579c6bf3398e2bee486.
// Producer: P1NativeRecordingTests.ProductionStoreAndPackerPreserveExactBasisAndTwoSemanticActionsWithoutPickupLabels.
// Regenerate: STS2_P1_FIXTURE_OUT=<new-directory> dotnet test
// components/annotator/tests/STS2HumanAnnotator.Core.Tests/STS2HumanAnnotator.Core.Tests.csproj
// --filter FullyQualifiedName~P1NativeRecordingTests (then freeze the resulting bundle bytes).
const packedFixture = `
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
`;
const sha = value => createHash('sha256').update(value).digest('hex');
function fixture(run, encoded = packedFixture) {
  const directory = mkdtempSync(resolve(tmpdir(), 'p1-native-bundle-'));
  try {
    const files = JSON.parse(gunzipSync(Buffer.from(encoded, 'base64')).toString('utf8'));
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

test('production seed/progress originals survive packing but never enter player snapshots or P1', () => fixture(directory => {
  const source = readVerifiedP1NativeBundle(directory);
  const original = JSON.parse(source.bytes('initial-progress/run-synthetic.json'));
  assert.equal(original.run_seed.seed, 'X2-PRIVATE-SEED');
  assert.equal(original.run_seed.status, 'captured');
  assert.match(source.bytes(original.payload_ref).toString('utf8'), /X2-PRIVATE-PROGRESS/u);
  const bases = source.events.flatMap(row => [row.basis, row.successor_basis].filter(Boolean));
  assert.ok(bases.length > 0);
  for (const basis of bases) {
    const bytes = source.bytes(basis.payload_ref).toString('utf8');
    assert.doesNotMatch(bytes, /X2-PRIVATE|run_seed|initial_progress/u);
  }
  const report = convertNativeSemanticOriginals(source, sdk, core);
  assert.ok(report.captures.length > 0);
  assert.doesNotMatch(JSON.stringify(report), /X2-PRIVATE|run_seed|initial_progress/u);
  // Fail if a future converter starts reading either private sidecar: the
  // ordinary verified reader can read it, but P1 has no reason to do so.
  assert.deepEqual(convertNativeSemanticOriginals({ ...source, bytes(name) {
    assert.ok(!name.startsWith('initial-progress/')); return source.bytes(name);
  } }, sdk, core), report);
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

// Actual Store.ExportP1NativePrefix bytes, exported while one decision was pending.
// Prefix ID: p1-prefix-52adb3aa54944ffab35077d77d31fd3e.
// Original manifest SHA256: 1b7bc45919880740665ba858f53f3bf533375881e5b7b59b80ab7b1e8d77eba4.
// Producer test then appends its terminal event and closes the original recording;
// the immutable export retains this earlier open watermark and exact original bytes.
// Regenerate with STS2_P1_PREFIX_FIXTURE_OUT=<new-directory> and the same dotnet test command above.
const packedPrefixFixture = `
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
73QW8ZP6UBPgQI9isfBz4JvshmY/4cSVrpOw/Gzjv0n++7NyzIBJndAzR/AdGPXn3w8JW5OfGDKdd/6S4/fkmMEPEXYL/i3dF2Ht4XcDK1o6pKpBnTEv25On
9qRzvmb1Mzw30gK6iyQGltlbvXq9wL+s/O6ZPdJhP1KuCd2jPEfCMmmHEyv18l5/5+W377TtHm9PTnXfDbCcCbroPyC7+RnSsvdWmtdXqMSvS+qLZd2Mddld
/J8q0+rioaozoXoK6hPCy862h3tS6jECmcep4ifHcZfV8bNTDZ/FzQ/4gqe5M3k+XHZIvj/0U3qA/tjkcVD1SXYKTfwyj/VmzuDQWTTvIktzkJ1C5VlErNc1
wuoGgPB6/1vK5pGfOab7PbbQC6l/QzFHqaMg19GedeGnhIuA2+Nw2OWJsJcNebeD5oTc7SlD+BHcFmSHdC8ZAwdPdKYBa7qDGBi9Qblb8LnGMsrzeStZW09j
5TGoomvyIR5PtUAnNbiHOKwuil6WVd/OwTE921UcnmJbdO+QL2t7IePmwLjttF/A21sZj8r7x9E+ytiJPH9wB3mg+A54/7bot0dYgDB+ldeQeWxL99O27LRs
9Z2BlImkjB072VSrYqmv+O3A9852Rp2uKRcJfpqbVGPpPIop5F6XueyXuRsrU03eDCDv3cnj04daoryPGMX7KF5W09nn8hUak3FL3m3L+hbQSfGolsOOFKKn
9CVf9Roq+hxMWXGOlu+ol11R8/3VPUvz2HieX/psriy+v2nnOqZ/1L+1sF10bxXdtTT+TD67rGf/2e8+05XXm4DnvnF79I11W9uh3VPc1bv0+vJ4mHczCBZq
s7opY6o5jZnZV3296oehzVxH9/95cbLTfTjbY+5wvqHK/8j6Rznv9dML5aCK+qVv4HdpS+Xay/6+yfikL8in1/pWb7qyJjP3m6hrOelfIH15v09CsVJfoz5b
kInsL7/pMu4qa19z7A98mo0DnsAeaT1d1n9kEeRyBNsYKfzuL+x/EfuvyzpgmcOPuGZpwiabDztvt0PovT32YEW1NTzvNv4Xjr3eNz2Sdxa2QxEuU+aO6H44
2OkgZGGksmSUUazkr3jd9XvphQu6iyzyQl/HHqYibGXw9+l+yT3dkfbXXtb9K8KPZAOl/qrfilX1Yqv3vHqrHufc+cyyt3iZm6r3Sr0HFiz6kBS4YnQuh7V7
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
j1VjJdns//xfHztn8yHEAAA=
`;
const prefixFixture = run => fixture(run, packedPrefixFixture);
function mutatePrefix(directory, mutate) {
  const manifest = readJson(directory, 'p1-native-prefix-manifest.json'); mutate(manifest);
  writeJson(directory, 'p1-native-prefix-manifest.json', manifest);
}

test('production durable prefix remains open with its original pending decision and no Close', () => prefixFixture((directory, originalFiles) => {
  const result = readVerifiedP1NativePrefix(directory);
  assert.equal(sha(readFileSync(resolve(directory, 'p1-native-prefix-manifest.json'))),
    '1b7bc45919880740665ba858f53f3bf533375881e5b7b59b80ab7b1e8d77eba4');
  assert.equal(result.manifestSha256, '1b7bc45919880740665ba858f53f3bf533375881e5b7b59b80ab7b1e8d77eba4');
  assert.equal(result.manifest.schema, 'sts2.human-annotator/p1-native-prefix-1');
  assert.equal(result.manifest.status, 'open'); assert.equal(result.manifest.scope, 'durable_prefix');
  assert.equal(result.manifest.watermark.pending_count, 1);
  assert.equal(result.manifest.watermark.terminal_count, 0);
  assert.equal(result.events.length, 1); assert.equal(result.events[0].kind, 'decision_accepted');
  assert.equal(result.journal.length, 1); assert.equal(result.manifest.declared_origin, 'agent_native_ui');
  assert.equal(existsSync(resolve(directory, 'raw/p1-native-close.json')), false);
  assert.equal(existsSync(resolve(directory, 'raw/session-close-receipt.json')), false);
  for (const [name, value] of Object.entries(originalFiles)) if (name.startsWith('raw/'))
    assert.deepEqual(result.bytes(name.slice(4)), Buffer.from(value, 'base64'));
}));

test('prefix and closed bundle entry points never silently fall back to one another', () => {
  prefixFixture(directory => assert.throws(() => readVerifiedP1NativeBundle(directory)));
  fixture(directory => assert.throws(() => readVerifiedP1NativePrefix(directory)));
  prefixFixture(directory => {
    mutatePrefix(directory, manifest => { manifest.status = 'closed'; });
    assert.throws(() => readVerifiedP1NativePrefix(directory), /prefix_schema_or_scope/);
  });
});

test('prefix byte and event watermarks must match the exported original streams', () => {
  for (const [field, reason] of [['events_byte_count', /prefix_byte_watermark/],
    ['journal_byte_count', /prefix_byte_watermark/], ['event_count', /prefix_event_watermark/],
    ['pending_count', /prefix_event_watermark/]]) prefixFixture(directory => {
    mutatePrefix(directory, manifest => { manifest.watermark[field]++; });
    assert.throws(() => readVerifiedP1NativePrefix(directory), reason);
  });
});

test('prefix inventory and identities remain checked after raw hashes are repaired', () => {
  prefixFixture(directory => {
    writeFileSync(resolve(directory, 'raw/p1-native-close.json'), '{}');
    mutatePrefix(directory, manifest => { manifest.raw_file_sha256['p1-native-close.json'] = sha(Buffer.from('{}')); });
    assert.throws(() => readVerifiedP1NativePrefix(directory), /prefix_raw_file_outside_scope/);
  });
  prefixFixture(directory => {
    const manifest = readJson(directory, 'raw/recording-manifest.json'); manifest.timeline_id = 'foreign';
    writeJson(directory, 'raw/recording-manifest.json', manifest);
    mutatePrefix(directory, manifest => {
      manifest.raw_file_sha256['recording-manifest.json'] = sha(readFileSync(resolve(directory, 'raw/recording-manifest.json')));
    });
    assert.throws(() => readVerifiedP1NativePrefix(directory), /raw_manifest_identity/);
  });
});

test('prefix original bytes stay checked when consumed after initial verification', () => prefixFixture(directory => {
  const result = readVerifiedP1NativePrefix(directory), reference = result.events[0].basis;
  writeFileSync(resolve(directory, 'raw', reference.payload_ref), '{}');
  assert.throws(() => result.bytes(reference.payload_ref), /raw_original_changed/);
}));

function withExecutionFixture(run) {
  fixture((directory, files) => {
    const manifest = readJson(directory, 'raw/recording-manifest.json');
    manifest.p1_native_execution_schema_version = 1;
    writeJson(directory, 'raw/recording-manifest.json', manifest);
    const rows = readEvents(directory), accepted = rows.find(row => row.kind === 'decision_accepted');
    const execution = { ...accepted, kind: 'execution', event_id: 'execution-event', phase: 'before_execution',
      queued: true, queue_interval_ms: 42, execution_snapshot_id: accepted.basis.snapshot_id };
    rows.splice(rows.indexOf(accepted) + 1, 0, execution);
    rows.forEach((row, index) => row.sequence = index + 1);
    writeEvents(directory, rows);
    const close = readJson(directory, 'raw/p1-native-close.json');
    close.event_count = rows.length;
    writeJson(directory, 'raw/p1-native-close.json', close);
    reseal(directory, files);
    run(directory, files, rows, execution);
  });
}

test('explicit execution-version recording retains execution without inventing a new accepted decision', () => withExecutionFixture(directory => {
  const source = readVerifiedP1NativeBundle(directory);
  assert.equal(source.executionSchemaVersion, 1);
  assert.equal(source.events.filter(row => row.kind === 'execution').length, 1);
  assert.equal(source.events.filter(row => row.kind === 'decision_accepted').length, 2);
}));
for (const defect of ['foreign-witness', 'duplicate', 'unknown-version']) test(`execution reader rejects ${defect}`, () => withExecutionFixture((directory, files, rows, execution) => {
  if (defect === 'foreign-witness') execution.action_witness_id = 'foreign';
  if (defect === 'duplicate') { rows.splice(rows.indexOf(execution) + 1, 0, { ...execution, event_id: 'duplicate-execution' });
    rows.forEach((row, index) => row.sequence = index + 1); }
  if (defect === 'unknown-version') { const manifest = readJson(directory, 'raw/recording-manifest.json');
    manifest.p1_native_execution_schema_version = 2; writeJson(directory, 'raw/recording-manifest.json', manifest); }
  writeEvents(directory, rows); reseal(directory, files);
  assert.throws(() => readVerifiedP1NativeBundle(directory), /execution_binding_mismatch|execution_schema_unsupported/u);
}));
