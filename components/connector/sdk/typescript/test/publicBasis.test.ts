import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { describe, it, expect } from 'vitest';
import { decodePublicBasis, verifyPublicBasis, readPublicBasisRecord, serializePublicBasisRecord,
  PUBLIC_BASIS_SCHEMA, PUBLIC_BASIS_SCOPE, PlayerEnvironmentRestClient } from '../src/index.js';
import { publicBytesSha256 } from '../src/retainedBytes.js';

function basis(): any {
  const original = JSON.parse(readFileSync(new URL('./fixtures/p1-global/synthetic-complete-combat.json', import.meta.url), 'utf8'));
  return { schema: PUBLIC_BASIS_SCHEMA, revision: '1', capture_scope: PUBLIC_BASIS_SCOPE,
    capture_status: 'complete', ...original, missing: [] };
}
async function archived() {
  const value = basis(); const serialized = JSON.stringify(value); const bytes = Buffer.from(serialized);
  const capture = { schema: 'sts2.player-environment/public-basis-handle-1', read_profile: 'public-basis-sealed-1',
    input_profile: 'public-basis-1', capture_id: 'capture-1', source_snapshot_id: value.snapshot.snapshot_id,
    session: value.snapshot.session, generation_id: 'generation-1', game_continuity_id: 'run-1',
    captured_at: '2026-10-16T00:00:00Z', expires_at: '2026-10-16T00:02:00Z',
    total_bytes: bytes.length, sha256: await publicBytesSha256(bytes), first_cursor: 'cursor_0', capture_ordinal: 1 } as const;
  return { value, serialized, bytes, capture };
}
describe('protocol-neutral original public basis', () => {
  it('retains exact bytes through archive and keeps P1 completeness separate', async () => {
    const f = await archived(); const full = await verifyPublicBasis(f.capture, f.serialized);
    expect((await readPublicBasisRecord(serializePublicBasisRecord(full))).serializedObservation).toBe(f.serialized);
    const b = basis(); b.reads = []; b.snapshot.reads = [];
    expect(decodePublicBasis(b).capture_status).toBe('complete'); // Complete original inventory, NOT a complete P1 input.
  });
  it.each(['omit', 'duplicate', 'foreign_snapshot', 'foreign_session', 'missing_reason', 'false_complete', 'hidden', 'nonfinite', 'ordering', 'visibility', 'hidden_policy'])(
    'rejects %s without backfilling', variant => {
      const b = basis();
      if (variant === 'omit') b.reads.pop();
      if (variant === 'duplicate') b.reads[1] = b.reads[0];
      if (variant === 'foreign_snapshot') b.reads[0].response.observed_snapshot_id = 'other';
      if (variant === 'foreign_session') b.reads[0].response.session.runtime_instance_id = 'other';
      if (variant === 'missing_reason') b.reads[0].response = null;
      if (variant === 'false_complete') { b.reads[0].response = null; b.reads[0].error = 'missing'; }
      if (variant === 'hidden') b.snapshot.information_policy.includes_hidden_information = true;
      if (variant === 'nonfinite') b.snapshot.extra = Infinity;
      if (variant === 'ordering') b.reads[0].response.ordering_semantics = 'different';
      if (variant === 'hidden_policy') b.reads[0].response.completeness.hidden_by_policy = ['different'];
      if (variant === 'visibility') b.reads[0].response.visibility_basis = 'different';
      expect(() => decodePublicBasis(b)).toThrow();
    });
  it('preserves a failed Read and refuses tampered original byte identity', async () => {
    const b = basis(); const read = b.reads[0]; read.response = null; read.error = 'native_unavailable';
    b.capture_status = 'partial'; b.missing = [`read:${read.read_id}:native_unavailable`];
    expect(decodePublicBasis(b).reads[0]!.error).toBe('native_unavailable');
    const f = await archived(); await expect(verifyPublicBasis(f.capture, f.serialized + ' ')).rejects.toThrow(/bytes/);
  });
  it('uses real HTTP chunks, releases retention and propagates cleanup failure', async () => {
    const f = await archived(); let releases = 0; let failRelease = false;
    const server = createServer((request, response) => {
      const url = new URL(request.url!, 'http://localhost'); response.setHeader('content-type', 'application/json');
      if (url.pathname.endsWith('/current')) response.end(JSON.stringify(f.capture));
      else if (url.pathname.endsWith('/read')) {
        const offset = Number(url.searchParams.get('cursor')!.split('_')[1]); const size = Number(url.searchParams.get('max_bytes'));
        const next = Math.min(offset + size, f.bytes.length);
        response.end(JSON.stringify({ schema: 'sts2.player-environment/public-basis-chunk-1', capture_id: 'capture-1', sha256: f.capture.sha256,
          offset, total_bytes: f.bytes.length, data_base64: f.bytes.subarray(offset, next).toString('base64'),
          next_cursor: next === f.bytes.length ? null : `cursor_${next}`, end: next === f.bytes.length }));
      } else { releases++; response.statusCode = failRelease ? 500 : 200;
        response.end(JSON.stringify({ schema: 'sts2.player-environment/public-basis-release-1', capture_id: 'capture-1', released: true })); }
    }); server.listen(0, '127.0.0.1'); await once(server, 'listening');
    try {
      const address = server.address() as { port: number }; const client = new PlayerEnvironmentRestClient(`http://127.0.0.1:${address.port}`, 1000);
      expect((await client.getFullPublicBasis({ maxBytes: 1024 }, {})).serializedObservation).toBe(f.serialized);
      expect(releases).toBe(1); failRelease = true;
      await expect(client.getFullPublicBasis({}, {})).rejects.toThrow(/cleanup failed/); expect(releases).toBe(2);
    } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
  });
  it('checks cancellation again after async send admission and never sends late', async () => {
    let sent = 0; const controller = new AbortController();
    const client = new PlayerEnvironmentRestClient('http://unused', 1000, (async () => { sent++; return new Response('{}'); }) as typeof fetch);
    await expect(client.capabilities({ signal: controller.signal, beforeSend: async () => { controller.abort(); } })).rejects.toThrow();
    expect(sent).toBe(0);
  });
  it('retains original invalid UTF8 before rejecting it', async () => {
    let original: Uint8Array | undefined;
    const bytes = new Uint8Array([0x7b, 0xff, 0x7d]);
    const client = new PlayerEnvironmentRestClient('http://unused', 1000, (async () => new Response(bytes)) as typeof fetch);
    await expect(client.capabilities({ onResponseBytes: value => { original = value; } })).rejects.toThrow();
    expect(original).toEqual(bytes);
  });
  it.each(['beforeSend', 'onResponseBytes'] as const)('bounds a stalled %s hook by external cancellation', async hookName => {
    const f = await archived(); const controller = new AbortController(); let releases = 0; let entered!: () => void;
    const started = new Promise<void>(resolve => { entered = resolve; });
    const client = new PlayerEnvironmentRestClient('http://fixture', 1000, (async (url: any) => {
      if (String(url).includes('/release')) { releases++; return Response.json({ schema: 'sts2.player-environment/public-basis-release-1', capture_id: 'capture-1', released: true }); }
      if (String(url).includes('/current')) return Response.json(f.capture);
      return Response.json({ schema: 'sts2.player-environment/public-basis-chunk-1', capture_id: 'capture-1', sha256: f.capture.sha256,
        offset: 0, total_bytes: f.bytes.length, data_base64: f.bytes.toString('base64'), next_cursor: null, end: true });
    }) as typeof fetch);
    const never = () => { entered(); return new Promise<void>(() => undefined); };
    // Only block a chunk hook after the capture handle is known, so cleanup has a retained owner.
    let responses = 0;
    const call = hookName === 'beforeSend'
      ? client.capabilities({ signal: controller.signal, beforeSend: never })
      : client.getFullPublicBasis({}, { signal: controller.signal, onResponseBytes: () => { if (++responses === 2) return never(); } });
    const rejected = expect(call).rejects.toThrow();
    await started; controller.abort(new Error('cancelled'));
    await Promise.race([rejected, new Promise<never>((_, reject) => setTimeout(() => reject(new Error('hook held cancellation')), 250))]);
    expect(releases).toBe(hookName === 'onResponseBytes' ? 1 : 0);
  });

});
