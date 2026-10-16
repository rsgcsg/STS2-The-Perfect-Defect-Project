import { createHash, randomUUID } from 'node:crypto';
import { decodePublicBasis, PUBLIC_BASIS_SCHEMA, PUBLIC_BASIS_SCOPE, PUBLIC_BASIS_PROFILE, PUBLIC_BASIS_READ_PROFILE } from '@rsgcsg/sts2-connector-client';

/** Called synchronously inside the existing driver actor. Observe and Read copy one
 * already materialized projection; no process request, await or live refresh occurs. */
export function captureManagedPublicBasis(session, { generationId, gameContinuityId, ordinal }) {
  const snapshot = structuredClone(session.observe());
  const reads = snapshot.reads.map(listed => {
    try { return { read_id: listed.read_id, kind: listed.kind,
      response: structuredClone(session.read({ readId: listed.read_id, expectedSnapshotId: snapshot.snapshot_id })), error: null }; }
    catch (error) { return { read_id: listed.read_id, kind: listed.kind, response: null, error: String(error.message) }; }
  });
  const missing = reads.flatMap(read => read.response === null ? [`read:${read.read_id}:${read.error}`]
    : read.response.completeness.status !== 'complete' || read.response.completeness.missing.length ? [`read:${read.read_id}:partial`] : []);
  if (snapshot.completeness.status !== 'complete' || snapshot.completeness.missing.length) missing.push('snapshot:partial');
  const basis = decodePublicBasis({ schema: PUBLIC_BASIS_SCHEMA, revision: '1', capture_scope: PUBLIC_BASIS_SCOPE,
    capture_status: missing.length ? 'partial' : 'complete', snapshot, reads, missing });
  const serializedObservation = JSON.stringify(basis), bytes = Buffer.from(serializedObservation);
  const now = Date.now();
  return { capture: { schema: 'sts2.player-environment/public-basis-handle-1', read_profile: PUBLIC_BASIS_READ_PROFILE,
    input_profile: PUBLIC_BASIS_PROFILE, capture_id: `capture-${randomUUID()}`, source_snapshot_id: snapshot.snapshot_id,
    session: snapshot.session, generation_id: generationId, game_continuity_id: gameContinuityId,
    captured_at: new Date(now).toISOString(), expires_at: new Date(now + 30_000).toISOString(),
    total_bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'), first_cursor: 'inline', capture_ordinal: ordinal },
    basis, serializedObservation };
}
