import { createHash, randomUUID } from 'node:crypto';
import { checkPublicBasisLight, decodePublicBasis, PUBLIC_BASIS_SCHEMA, PUBLIC_BASIS_SCOPE, PUBLIC_BASIS_PROFILE, PUBLIC_BASIS_READ_PROFILE } from '@rsgcsg/sts2-connector-client';
import { deepFreeze } from './managed-deep-freeze.mjs';

export { deepFreeze };

/** Called synchronously inside the existing driver actor. Observe and Read copy one
 * already materialized projection; no process request, await or live refresh occurs.
 * fullCheck=false (fast chain, unsampled step) keeps every semantic gate but skips the
 * strict shape decode; the sampled steps and R7 games get the full decode. */
export function captureManagedPublicBasis(session, { generationId, gameContinuityId, ordinal, settledRelicCounters = false, fullCheck = true }) {
  // The old chain copies the projection so no consumer can reach the session's objects.
  // The fast chain (fullCheck=false) freezes them instead: same isolation, no copy, and
  // unchanged subtrees keep their identity from step to step.
  // Under the E5 projection memo, kept subtrees are already frozen and deepFreeze skips them.
  const own = fullCheck ? structuredClone : deepFreeze;
  const snapshot = own(session.observe());
  const reads = snapshot.reads.map(listed => {
    try { return { read_id: listed.read_id, kind: listed.kind,
      response: own(session.read({ readId: listed.read_id, expectedSnapshotId: snapshot.snapshot_id })), error: null }; }
    catch (error) { return { read_id: listed.read_id, kind: listed.kind, response: null, error: String(error.message) }; }
  });
  const missing = reads.flatMap(read => read.response === null ? [`read:${read.read_id}:${read.error}`]
    : read.response.completeness.status !== 'complete' || read.response.completeness.missing.length ? [`read:${read.read_id}:partial`] : []);
  if (snapshot.completeness.status !== 'complete' || snapshot.completeness.missing.length) missing.push('snapshot:partial');
  const settled = settledRelicCounters ? session.settledRelicCounters(snapshot.snapshot_id) : null;
  if (settled) missing.push(...settled.missing.map(value => `settled_relic_counters:${value}`));
  const basis = (fullCheck ? decodePublicBasis : checkPublicBasisLight)({ schema: PUBLIC_BASIS_SCHEMA, revision: settled ? '3' : '1', capture_scope: PUBLIC_BASIS_SCOPE,
    capture_status: missing.length ? 'partial' : 'complete', snapshot, reads, missing,
    ...(settled ? { settled_relic_counters: settled } : {}) });
  // Hashing the string hashes its UTF-8 bytes: same digest and length without a Buffer copy.
  const serializedObservation = JSON.stringify(basis);
  const now = Date.now();
  return { capture: { schema: 'sts2.player-environment/public-basis-handle-1', read_profile: PUBLIC_BASIS_READ_PROFILE,
    input_profile: PUBLIC_BASIS_PROFILE, capture_id: `capture-${randomUUID()}`, source_snapshot_id: snapshot.snapshot_id,
    session: snapshot.session, generation_id: generationId, game_continuity_id: gameContinuityId,
    captured_at: new Date(now).toISOString(), expires_at: new Date(now + 30_000).toISOString(),
    total_bytes: Buffer.byteLength(serializedObservation), sha256: createHash('sha256').update(serializedObservation).digest('hex'),
    first_cursor: 'inline', capture_ordinal: ordinal },
    basis, serializedObservation };
}
