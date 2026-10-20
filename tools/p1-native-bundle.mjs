/** Read the sealed production P1-native recording; this establishes integrity, never Human origin. */
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, lstatSync, realpathSync } from 'node:fs';
import { resolve, relative, sep } from 'node:path';
import { isDeepStrictEqual } from 'node:util';

const PROFILE = 'human-p1-native-decisions-1';
const SCHEMA = 'sts2.human-annotator/p1-native-session-bundle-1';
const hash = value => createHash('sha256').update(value).digest('hex');
const text = value => new TextDecoder('utf-8', { fatal: true }).decode(value);
const fail = reason => { throw new Error(`p1_native_bundle:${reason}`); };
const require = (condition, reason) => { if (!condition) fail(reason); };
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const identifier = value => typeof value === 'string' && value.length > 0;
const digest = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
function safePath(value) {
  require(typeof value === 'string' && !value.includes('\\') && !/[\r\n]/.test(value)
    && !value.startsWith('/') && value.split('/').every(part => part && part !== '.' && part !== '..'), 'path_invalid');
  return value;
}
function files(directory, prefix = '') {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const name = prefix + entry.name;
    require(!entry.isSymbolicLink(), 'symlink_not_original');
    if (entry.isDirectory()) return files(resolve(directory, entry.name), `${name}/`);
    require(entry.isFile(), 'ordinary_file_required');
    return [name];
  }).sort();
}
function sameKeys(first, second) {
  return isDeepStrictEqual([...first].sort(), [...second].sort());
}

function recording(identity, bytes) {
  const rawJson = name => JSON.parse(text(bytes(name)));
  const rawManifest = rawJson('recording-manifest.json');
  require(rawManifest.schema === 'sts2.human-annotator/recording-manifest-2'
    && rawManifest.schema_version === 2 && rawManifest.p1_native_schema_version === 1
    && rawManifest.capture_profile_id === PROFILE && rawManifest.session_id === identity.session_id
    && rawManifest.timeline_id === identity.timeline_id && rawManifest.p1_native_origin === identity.declared_origin,
  'raw_manifest_identity');
  require(rawManifest.p1_native_execution_schema_version == null || rawManifest.p1_native_execution_schema_version === 1,
    'execution_schema_unsupported');
  const executionSchemaVersion = rawManifest.p1_native_execution_schema_version ?? null;
  const profile = rawJson('capture-profile.json');
  require(profile.profile_id === PROFILE && profile.record_schema === 'sts2.human-annotator/p1-native-event-1'
    && rawManifest.capture_profile_sha256 === hash(Buffer.from(JSON.stringify(profile))), 'capture_profile_identity');
  const belongs = value => value.session_id === identity.session_id && value.timeline_id === identity.timeline_id;
  function rows(name, schema, consecutive) {
    const content = text(bytes(name));
    if (content === '') return [];
    const original = content.split('\n');
    require(original.pop() === '', 'jsonl_unsealed_line');
    const ids = new Set(); let previous = 0;
    return original.map(line => {
      require(line.length > 0, 'jsonl_empty_row');
      const row = JSON.parse(line);
      require(object(row) && row.schema === schema && belongs(row) && identifier(row.event_id)
        && !ids.has(row.event_id) && identifier(row.kind), 'row_identity');
      require(Number.isSafeInteger(row.sequence) && row.sequence > previous
        && (!consecutive || row.sequence === previous + 1), 'row_sequence');
      previous = row.sequence; ids.add(row.event_id); return row;
    });
  }
  const events = rows('p1-native-events.jsonl', 'sts2.human-annotator/p1-native-event-1', true);
  const journal = rows('run-journal.jsonl', 'sts2.human-annotator/run-journal-event-2', false);
  let accepted = 0, terminal = 0, issues = 0;
  const pending = new Map(), witnesses = new Set();
  function basis(reference) {
    require(object(reference) && identifier(reference.snapshot_id) && digest(reference.sha256)
      && Number.isSafeInteger(reference.byte_count) && reference.byte_count >= 0
      && identifier(reference.payload_ref), 'basis_reference');
    require(reference.payload_ref === `public-bases/sha256/${reference.sha256.slice(0, 2)}/${reference.sha256}.json`,
      'basis_content_address');
    const value = bytes(reference.payload_ref);
    require(value.length === reference.byte_count && hash(value) === reference.sha256, 'basis_bytes_mismatch');
    const original = JSON.parse(text(value));
    require(original.schema === 'sts2.player-environment/public-basis-1'
      && original.snapshot?.snapshot_id === reference.snapshot_id, 'basis_snapshot_identity');
  }
  for (const row of events) {
    for (const key of ['basis', 'successor_basis']) if (row[key] != null) basis(row[key]);
    if (row.kind === 'decision_accepted') {
      require(identifier(row.action_witness_id) && identifier(row.decision_id) && identifier(row.run_id)
        && !witnesses.has(row.action_witness_id), 'accepted_identity');
      witnesses.add(row.action_witness_id);
      pending.set(row.action_witness_id, { decision_id: row.decision_id, run_id: row.run_id, executed: false }); accepted++;
    }
    if (row.kind === 'execution') {
      const start = pending.get(row.action_witness_id);
      require(executionSchemaVersion === 1 && start && start.decision_id === row.decision_id
        && start.run_id === row.run_id && !start.executed, 'execution_binding_mismatch');
      require(row.phase === 'before_execution' && typeof row.queued === 'boolean'
        && Number.isFinite(row.queue_interval_ms) && row.queue_interval_ms >= 0, 'execution_boundary_invalid');
      require(row.basis == null || row.missing_reason != null || row.execution_snapshot_id === row.basis.snapshot_id,
        'execution_snapshot_mismatch');
      start.executed = true;
    }
    if (row.kind === 'issue') issues++;
    if (row.kind === 'lifecycle' && ['finished', 'cancelled', 'aborted', 'unknown'].includes(row.phase)) {
      const start = pending.get(row.action_witness_id);
      require(start && start.decision_id === row.decision_id && start.run_id === row.run_id, 'terminal_without_accepted');
      pending.delete(row.action_witness_id); terminal++;
    }
  }
  return { events, journal, accepted, terminal, issues, pending: pending.size, executionSchemaVersion };
}

export function readVerifiedP1NativeBundle(bundlePath) {
  const bundle = realpathSync(resolve(bundlePath));
  require(lstatSync(bundle).isDirectory(), 'directory_required');
  const inventory = files(bundle);
  const checksumBytes = readFileSync(resolve(bundle, 'checksums.sha256'));
  const checksums = new Map();
  const lines = text(checksumBytes).split('\n');
  if (lines.at(-1) === '') lines.pop();
  for (const line of lines) {
    const match = /^([0-9a-f]{64})  (.+)$/.exec(line);
    require(match !== null, 'checksum_line_invalid');
    const name = safePath(match[2]);
    require(name !== 'checksums.sha256' && !checksums.has(name), 'checksum_path_duplicate_or_self');
    checksums.set(name, match[1]);
  }
  require(sameKeys(inventory, [...checksums.keys(), 'checksums.sha256']), 'checksum_inventory_mismatch');
  function checked(name) {
    safePath(name);
    require(checksums.has(name), 'file_not_checksummed');
    const file = resolve(bundle, name), actual = realpathSync(file);
    require(lstatSync(file).isFile() && !lstatSync(file).isSymbolicLink()
      && !relative(bundle, actual).split(sep).includes('..'), 'original_path_changed');
    const value = readFileSync(file);
    require(hash(value) === checksums.get(name), 'original_bytes_changed');
    return value;
  }
  for (const name of checksums.keys()) checked(name);
  const json = name => JSON.parse(text(checked(name)));
  const manifest = json('session-bundle-manifest.json');
  const identityBytes = checked('content-identity.json');
  const identity = JSON.parse(text(identityBytes));
  require(manifest.schema === SCHEMA && object(identity) && identity.schema === SCHEMA, 'bundle_schema');
  require(digest(manifest.bundle_content_id) && hash(identityBytes) === manifest.bundle_content_id
    && isDeepStrictEqual(identity, manifest.content_identity), 'content_identity_mismatch');
  require(identity.capture_profile_id === PROFILE && identifier(identity.session_id)
    && identifier(identity.timeline_id) && identifier(identity.declared_origin)
    && identifier(identity.worker_id) && identifier(identity.campaign_id)
    && typeof identity.packer_source_revision === 'string' && /^[0-9a-f]{40}$/i.test(identity.packer_source_revision),
  'identity_fields');
  const attestation = identity.human_origin_attestation;
  require(object(attestation) && typeof attestation.attested === 'boolean'
    && attestation.method === 'explicit_owner_pack' && attestation.machine_verifiable === false
    && (!attestation.attested || identity.declared_origin === 'declared_human'), 'origin_attestation');
  const rawHashes = identity.raw_file_sha256;
  require(object(rawHashes), 'raw_inventory_required');
  require(sameKeys(inventory.filter(name => name.startsWith('raw/')).map(name => name.slice(4)),
    Object.keys(rawHashes)), 'raw_inventory_mismatch');
  for (const [name, sha] of Object.entries(rawHashes)) {
    safePath(name);
    require(digest(sha) && checksums.get(`raw/${name}`) === sha, 'raw_checksum_mismatch');
  }
  function bytes(name) {
    safePath(name);
    require(Object.hasOwn(rawHashes, name), 'raw_path_not_in_original_inventory');
    const value = checked(`raw/${name}`);
    require(hash(value) === rawHashes[name], 'raw_original_changed');
    return value;
  }
  const rawJson = name => JSON.parse(text(bytes(name)));
  const belongs = value => value.session_id === identity.session_id && value.timeline_id === identity.timeline_id;
  const { events, journal, accepted, terminal, issues, pending, executionSchemaVersion } = recording(identity, bytes);
  const receipt = rawJson('session-close-receipt.json');
  require(receipt.schema === 'sts2.human-annotator/session-close-1' && receipt.status === 'closed'
    && belongs(receipt), 'original_close_required');
  const close = rawJson('p1-native-close.json');
  require(close.schema === 'sts2.human-annotator/p1-native-close-1' && close.status === 'closed'
    && belongs(close) && close.declared_origin === identity.declared_origin, 'p1_close_identity');
  require(close.events_sha256 === rawHashes['p1-native-events.jsonl']
    && close.run_journal_sha256 === rawHashes['run-journal.jsonl'], 'closed_stream_hash');
  require(close.event_count === events.length && close.accepted_count === accepted
    && close.terminal_count === terminal && close.pending_count === pending && pending === 0
    && close.issue_count === issues, 'closed_stream_accounting');
  // The producer's declared origin and explicit attestation remain in manifest;
  // neither attested=true nor a closed file upgrades them to machine-proven Human data.
  return { bundle, manifest, events, journal, bytes, executionSchemaVersion,
    verifiedManifestSha256: hash(checked('session-bundle-manifest.json')) };
}

/** Verify the immutable administrative export of an open recording, without closing it. */
export function readVerifiedP1NativePrefix(prefixPath) {
  const bundle = realpathSync(resolve(prefixPath));
  require(lstatSync(bundle).isDirectory(), 'directory_required');
  const inventory = files(bundle), manifestName = 'p1-native-prefix-manifest.json';
  const manifestBytes = readFileSync(resolve(bundle, manifestName));
  const manifest = JSON.parse(text(manifestBytes));
  require(manifest.schema === 'sts2.human-annotator/p1-native-prefix-1'
    && manifest.status === 'open' && manifest.scope === 'durable_prefix', 'prefix_schema_or_scope');
  require(identifier(manifest.prefix_id) && identifier(manifest.session_id) && identifier(manifest.timeline_id)
    && manifest.capture_profile_id === PROFILE && identifier(manifest.declared_origin), 'prefix_identity');
  const rawHashes = manifest.raw_file_sha256;
  require(object(rawHashes), 'raw_inventory_required');
  for (const [name, sha] of Object.entries(rawHashes)) {
    safePath(name);
    require(digest(sha), 'raw_checksum_mismatch');
    require(['recording-manifest.json', 'capture-profile.json', 'p1-native-events.jsonl', 'run-journal.jsonl'].includes(name)
      || name.startsWith('public-bases/'), 'prefix_raw_file_outside_scope');
  }
  require(sameKeys(inventory, [manifestName, ...Object.keys(rawHashes).map(name => `raw/${name}`)]),
    'prefix_inventory_mismatch');
  function bytes(name) {
    safePath(name);
    require(Object.hasOwn(rawHashes, name), 'raw_path_not_in_original_inventory');
    const file = resolve(bundle, 'raw', name), actual = realpathSync(file);
    require(lstatSync(file).isFile() && !lstatSync(file).isSymbolicLink()
      && !relative(bundle, actual).split(sep).includes('..'), 'original_path_changed');
    const value = readFileSync(file);
    require(hash(value) === rawHashes[name], 'raw_original_changed');
    return value;
  }
  for (const name of Object.keys(rawHashes)) bytes(name);
  const { events, journal, accepted, terminal, issues, pending, executionSchemaVersion } = recording(manifest, bytes);
  const watermark = manifest.watermark;
  require(object(watermark), 'prefix_watermark_required');
  const eventsBytes = bytes('p1-native-events.jsonl'), journalBytes = bytes('run-journal.jsonl');
  require(watermark.events_byte_count === eventsBytes.length && watermark.events_sha256 === hash(eventsBytes)
    && watermark.journal_byte_count === journalBytes.length && watermark.journal_sha256 === hash(journalBytes),
  'prefix_byte_watermark');
  require(watermark.event_count === events.length && watermark.accepted_count === accepted
    && watermark.terminal_count === terminal && watermark.pending_count === pending && watermark.issue_count === issues,
  'prefix_event_watermark');
  // Pending is an observed open-prefix fact. No Close or terminal event is synthesized.
  return { bundle, manifest, events, journal, bytes, executionSchemaVersion, manifestSha256: hash(manifestBytes) };
}
