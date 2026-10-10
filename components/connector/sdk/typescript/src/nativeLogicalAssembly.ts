import { createHash } from "node:crypto";
import { type DecodedPlayerPayload } from "./protocol.js";
import {
  decodeNativeLogicalCapture, decodeNativeLogicalContext, decodeNativeLogicalObservation,
  decodeNativeLogicalRead, decodeNativeLogicalCatalogPage, digestNativeLogicalActions,
  NATIVE_LOGICAL_MAX_READ_BYTES, NATIVE_LOGICAL_MAX_PAGE_BYTES,
  NATIVE_LOGICAL_SCOPE, validateNativeLogicalScope, type NativeLogicalScopeField,
  type NativeLogicalCapture, type NativeLogicalContext, type NativeLogicalObservation,
  type NativeLogicalAction, type NativeLogicalReadChunk, type NativeLogicalCatalogPage,
  type NativeLogicalRetain, type NativeLogicalRelease, type NativeLogicalRetention
} from "./nativeLogical.js";
import { parseNativeLogicalJson, freezeNativeLogical } from "./nativeLogicalWire.js";

export interface NativeLogicalByteReservation {
  /** Resize after the actual bounded HTTP page bytes are known; never increase beyond its admitted maximum. */
  resize(bytes: number): void | Promise<void>;
  release(): void | Promise<void>;
}
export interface NativeLogicalByteBudget {
  /** Runs before allocating the assembly buffer or requesting a catalog page. */
  reserve(input: { kind: "observation" | "catalog_page"; bytes: number; signal?: AbortSignal }):
    NativeLogicalByteReservation | Promise<NativeLogicalByteReservation>;
}
export interface NativeLogicalCaptureReader {
  read(input: { captureId: string; cursor: string; maxBytes?: number; signal?: AbortSignal }):
    Promise<DecodedPlayerPayload<NativeLogicalReadChunk>>;
  list(input: { catalogRef: string; streamGeneration: string; cursor?: string | null; limit?: number;
    maxPageBytes?: number; signal?: AbortSignal }):
    Promise<DecodedPlayerPayload<NativeLogicalCatalogPage> & { encodedByteCount: number }>;
  retain(captureId: string, signal?: AbortSignal): Promise<DecodedPlayerPayload<NativeLogicalRetain>>;
  release(retentionHandleId: string): Promise<DecodedPlayerPayload<NativeLogicalRelease>>;
}
export interface NativeLogicalAssemblyInput {
  capture: NativeLogicalCapture;
  context?: NativeLogicalContext | null;
  /** Ownership of this client-owned handle transfers to the returned FullCapture or error cleanup. */
  retention?: NativeLogicalRetention | null;
  /** SDK sidecar ownership moves into admission/assembly before validation. */
  readerLease?: NativeLogicalRetentionLease;
  signal?: AbortSignal;
  budget?: NativeLogicalByteBudget;
  chunkBytes?: number;
  pageLimit?: number;
  maxPageBytes?: number;
  /** Announced encoded page ceiling, including a negative budget envelope. */
  maxCatalogResponseBytes?: number;
  /** Consumer resource ceiling only; rejects the whole relation instead of filtering it. */
  maxActions?: number;
}
export interface NativeLogicalScopedAssemblyInput extends NativeLogicalAssemblyInput {
  eagerScope: readonly NativeLogicalScopeField[];
}

export function sameNativeLogicalCapture(left: NativeLogicalCapture, right: NativeLogicalCapture): boolean {
  const a = decodeNativeLogicalCapture(left).data;
  const b = decodeNativeLogicalCapture(right).data;
  return JSON.stringify(a) === JSON.stringify(b);
}

/** One immutable original reader pin. Its callback is bound by the requesting
 * SDK to the original client/transport, never to a later registration. */
export class NativeLogicalRetentionLease {
  #disposed?: Promise<void>;
  constructor(readonly capture: NativeLogicalCapture, readonly retention: NativeLogicalRetention,
    private readonly releaseOriginal: () => Promise<void>) {}
  dispose(): Promise<void> {
    return this.#disposed ??= Promise.resolve().then(this.releaseOriginal);
  }
}

/** Owns only this assembly's bytes, reservations and one reader pin. Borrowed
 * decoded values must not be retained by the consumer beyond its consumption lifetime. */
export class NativeLogicalCapturedObservation {
  #bytes?: Uint8Array;
  #observation?: NativeLogicalObservation;
  #cleanup?: () => Promise<void>;
  #disposed?: Promise<void>;
  #transfer?: () => NativeLogicalRetentionLease;
  constructor(readonly capture: NativeLogicalCapture, readonly context: NativeLogicalContext | null,
    bytes: Uint8Array, observation: NativeLogicalObservation,
    cleanup: () => Promise<void>, transfer?: () => NativeLogicalRetentionLease) {
    this.#bytes = bytes;
    this.#observation = observation;
    this.#cleanup = cleanup;
    this.#transfer = transfer;
  }
  get observation(): NativeLogicalObservation {
    if (!this.#observation) throw new Error("native logical full capture was disposed");
    return this.#observation;
  }
  get serializedObservation(): string {
    if (!this.#bytes) throw new Error("native logical full capture was disposed");
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(this.#bytes);
  }
  transferRetention(): NativeLogicalRetentionLease {
    if (this.#disposed || !this.#transfer) throw new Error("native logical reader ownership is unavailable or already transferred");
    const transfer = this.#transfer;
    this.#transfer = undefined;
    return transfer();
  }
  dispose(): Promise<void> {
    if (this.#disposed) return this.#disposed;
    this.#bytes?.fill(0);
    this.#bytes = undefined;
    this.#observation = undefined;
    this.#transfer = undefined;
    const cleanup = this.#cleanup;
    this.#cleanup = undefined;
    this.#disposed = cleanup ? Promise.resolve().then(cleanup) : Promise.resolve();
    return this.#disposed;
  }
}

export class NativeLogicalFullCapture extends NativeLogicalCapturedObservation {
  #actions?: readonly NativeLogicalAction[];
  constructor(capture: NativeLogicalCapture, context: NativeLogicalContext | null, bytes: Uint8Array,
    observation: NativeLogicalObservation, actions: readonly NativeLogicalAction[], cleanup: () => Promise<void>,
    transfer?: () => NativeLogicalRetentionLease) {
    super(capture, context, bytes, observation, cleanup, transfer);
    this.#actions = actions;
  }
  get actions(): readonly NativeLogicalAction[] {
    if (!this.#actions) throw new Error("native logical full capture was disposed");
    return this.#actions;
  }
  override dispose(): Promise<void> { this.#actions = undefined; return super.dispose(); }
}

/** One immutable capture plus the ordered WHOLE relation. No Current fallback,
 * action filtering, native getter, model update or inferred execution occurs. */
export function assembleNativeLogicalCapture(input: NativeLogicalAssemblyInput,
  reader: NativeLogicalCaptureReader): Promise<NativeLogicalFullCapture> {
  return assemble({ ...input, eagerScope: NATIVE_LOGICAL_SCOPE }, reader, true) as Promise<NativeLogicalFullCapture>;
}
export function assembleNativeLogicalObservation(input: NativeLogicalScopedAssemblyInput,
  reader: NativeLogicalCaptureReader): Promise<NativeLogicalCapturedObservation> {
  return assemble(input, reader, false);
}
async function assemble(input: NativeLogicalScopedAssemblyInput, reader: NativeLogicalCaptureReader,
  fullCatalog: boolean): Promise<NativeLogicalCapturedObservation> {
  const chunkBytes = input.chunkBytes ?? NATIVE_LOGICAL_MAX_READ_BYTES;
  const maxPageBytes = input.maxPageBytes ?? NATIVE_LOGICAL_MAX_PAGE_BYTES;
  const responsePageBytes = input.maxCatalogResponseBytes ?? NATIVE_LOGICAL_MAX_PAGE_BYTES;
  const pageLimit = input.pageLimit ?? 100;
  const maxActions = input.maxActions ?? 65536;
  let readerLease = input.readerLease;
  let retention = input.retention ?? readerLease?.retention ?? null;
  const reservations: NativeLogicalByteReservation[] = [];
  let bytes: Uint8Array | undefined;
  let actions: NativeLogicalAction[] | undefined;
  const reserve = async (kind: "observation" | "catalog_page", size: number) => {
    input.signal?.throwIfAborted();
    const admitted = await input.budget?.reserve({ kind, bytes: size, signal: input.signal });
    if (admitted) reservations.push(admitted);
    input.signal?.throwIfAborted();
    return admitted;
  };
  const cleanup = async () => {
    const errors: unknown[] = [];
    if (readerLease) {
      const owned = readerLease; readerLease = undefined; retention = null;
      try { await owned.dispose(); } catch (error) { errors.push(error); }
    } else if (retention) {
      const handle = retention.retention_handle_id;
      retention = null;
      try { await reader.release(handle); } catch (error) { errors.push(error); }
    }
    for (const reservation of reservations.splice(0)) {
      try { await reservation.release(); } catch (error) { errors.push(error); }
    }
    if (errors.length) throw new AggregateError(errors, "native logical assembly cleanup failed");
  };
  const transfer = input.readerLease ? () => {
    if (!readerLease) throw new Error("native logical original reader lease already transferred");
    const owned = readerLease; readerLease = undefined; retention = null;
    return owned;
  } : undefined;
  try {
    // A supplied reader pin transfers to this assembly even if its descriptor
    // is malformed. Keep all validation inside the owned cleanup boundary.
    const capture = decodeNativeLogicalCapture(input.capture).data;
    const context = input.context == null ? null : decodeNativeLogicalContext(input.context).data;
    if (readerLease && (!sameNativeLogicalCapture(readerLease.capture, capture) ||
        retention?.retention_handle_id !== readerLease.retention.retention_handle_id))
      throw new Error("native logical assembly sidecar does not own this original capture/reader");
    validateNativeLogicalScope(input.eagerScope);
    if (!Number.isInteger(chunkBytes) || chunkBytes <= 0 || chunkBytes > NATIVE_LOGICAL_MAX_READ_BYTES ||
        !Number.isInteger(maxPageBytes) || maxPageBytes <= 0 || maxPageBytes > NATIVE_LOGICAL_MAX_PAGE_BYTES ||
        !Number.isInteger(pageLimit) || pageLimit <= 0 || pageLimit > 65536 ||
        !Number.isInteger(responsePageBytes) || responsePageBytes <= 0 || responsePageBytes > NATIVE_LOGICAL_MAX_PAGE_BYTES || maxPageBytes > responsePageBytes ||
        !Number.isInteger(maxActions) || maxActions < 0 || maxActions > 65536)
      throw new Error("native logical assembly limits are outside the declared profile");
    if (context && (context.capture_ref !== capture.capture_id || context.observation_ref !== capture.snapshot_id ||
        context.stream_generation !== capture.stream_generation)) throw new Error("native logical context does not bind this capture");
    input.signal?.throwIfAborted();
    if (retention) {
      if (!sameNativeLogicalCapture(retention.capture, capture)) throw new Error("native logical retention changed original capture identity");
    } else {
      const result = (await reader.retain(capture.capture_id, input.signal)).data;
      if (result.status !== "retained" || !result.retention) throw new Error(`native logical capture retention failed: ${result.status}`);
      retention = result.retention;
      if (!sameNativeLogicalCapture(retention.capture, capture)) throw new Error("native logical retention changed original capture identity");
    }
    await reserve("observation", capture.byte_count);
    bytes = new Uint8Array(capture.byte_count);
    let offset = 0;
    let next: string | null = retention.read_cursor;
    const visited = new Set<string>();
    while (next !== null) {
      input.signal?.throwIfAborted();
      if (visited.has(next)) throw new Error("native logical read cursor cycle");
      visited.add(next);
      const chunk: NativeLogicalReadChunk = decodeNativeLogicalRead((await reader.read({ captureId: capture.capture_id, cursor: next,
        maxBytes: chunkBytes, signal: input.signal })).data).data;
      if (chunk.capture_id !== capture.capture_id || chunk.sha256 !== capture.sha256 ||
          chunk.total_bytes !== capture.byte_count || chunk.offset !== offset)
        throw new Error("native logical read identity or contiguous offset mismatch");
      const block = Buffer.from(chunk.data_base64, "base64");
      if (block.toString("base64") !== chunk.data_base64 || block.length !== Math.min(chunkBytes, bytes.length - offset))
        throw new Error("native logical read size or canonical base64 mismatch");
      bytes.set(block, offset);
      offset += block.length;
      if (chunk.complete !== (offset === bytes.length)) throw new Error("native logical read terminal position mismatch");
      next = chunk.next_cursor;
    }
    if (offset !== bytes.length || createHash("sha256").update(bytes).digest("hex") !== capture.sha256)
      throw new Error("native logical capture byte count or SHA-256 mismatch");
    const serialized = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
    const observation = decodeNativeLogicalObservation(parseNativeLogicalJson(serialized)).data;
    const descriptor = observation.catalog;
    if (observation.snapshot_id !== capture.snapshot_id || observation.session.runtime_instance_id !== capture.session.runtime_instance_id ||
        observation.session.environment_fingerprint !== capture.session.environment_fingerprint ||
        descriptor.stream_generation !== capture.stream_generation || descriptor.scope_id !== capture.scope_id)
      throw new Error("native logical observation/session/scope/generation mismatch");
    const missing = NATIVE_LOGICAL_SCOPE.filter(field => !input.eagerScope.includes(field));
    if (JSON.stringify(observation.completeness.included) !== JSON.stringify(input.eagerScope) ||
        JSON.stringify(observation.completeness.missing) !== JSON.stringify(missing) ||
        observation.completeness.status !== (missing.length === 0 ? "complete" : "partial") ||
        observation.completeness.full_reference_complete !== (missing.length === 0) ||
        missing.includes("persistent") && observation.persistent !== null ||
        missing.includes("interaction") && observation.interaction !== null ||
        missing.includes("referents") && observation.referents.length !== 0 ||
        missing.includes("catalog") && descriptor.status !== "not_captured" ||
        !missing.includes("catalog") && descriptor.status !== "complete")
      throw new Error("native logical captured fields/completeness do not match the requested scope");
    if (!fullCatalog) {
      input.signal?.throwIfAborted();
      const scoped = new NativeLogicalCapturedObservation(capture, context, bytes, observation, cleanup, transfer);
      bytes = undefined;
      return scoped;
    }
    if (!observation.completeness.full_reference_complete || descriptor.status !== "complete" || descriptor.total_count === null ||
        descriptor.digest === null || descriptor.total_count > 65536 || observation.interaction === null)
      throw new Error("native logical GetFull requires a complete full-reference observation and catalog");
    if (descriptor.total_count > maxActions) throw new Error("native logical whole catalog exceeds the consumer maxActions capacity");
    actions = [];
    let catalogCursor: string | null = null;
    visited.clear();
    do {
      if (catalogCursor !== null) {
        if (visited.has(catalogCursor)) throw new Error("native logical catalog cursor cycle");
        visited.add(catalogCursor);
      }
      // Even a one-byte requested page budget must be able to receive the
      // bounded page_budget_too_small envelope. Reserve its announced maximum.
      const reservation = await reserve("catalog_page", responsePageBytes);
      const response = await reader.list({ catalogRef: descriptor.catalog_ref, streamGeneration: capture.stream_generation,
        cursor: catalogCursor, limit: pageLimit, maxPageBytes, signal: input.signal });
      if (!Number.isSafeInteger(response.encodedByteCount) || response.encodedByteCount <= 0 || response.encodedByteCount > responsePageBytes)
        throw new Error("native logical catalog encoded byte budget mismatch");
      await reservation?.resize(response.encodedByteCount);
      input.signal?.throwIfAborted();
      const page = decodeNativeLogicalCatalogPage(response.data).data;
      if (page.status !== "complete") throw new Error(`native logical whole catalog unavailable: ${page.status}`);
      if (response.encodedByteCount > maxPageBytes) throw new Error("native logical catalog exceeded its requested page byte budget");
      if (page.catalog_ref !== descriptor.catalog_ref || page.snapshot_id !== capture.snapshot_id || page.stream_generation !== capture.stream_generation ||
          page.total_count !== descriptor.total_count || page.filtered_count !== descriptor.total_count ||
          page.digest !== descriptor.digest || page.filtered_digest !== descriptor.digest)
        throw new Error("native logical page is not the same full ordered relation");
      if (page.actions.length > pageLimit || actions.length + page.actions.length > descriptor.total_count ||
          page.actions.length === 0 && page.next_cursor !== null)
        throw new Error("native logical whole catalog count or progress mismatch");
      actions.push(...page.actions);
      catalogCursor = page.next_cursor;
    } while (catalogCursor !== null);
    if (actions.length !== descriptor.total_count || digestNativeLogicalActions(actions) !== descriptor.digest)
      throw new Error("native logical whole catalog count/digest mismatch");
    const publicIds = new Set(observation.referents.map(value => value.referent_id));
    for (const action of actions) {
      if (action.subject_referent_id !== null && !publicIds.has(action.subject_referent_id) ||
          action.arguments.some(value => !publicIds.has(value.referent_id)))
        throw new Error("native logical action operand has no current public referent");
    }
    if (observation.owner_occurrence.focus_referent_id !== null && !publicIds.has(observation.owner_occurrence.focus_referent_id))
      throw new Error("native logical focus has no current public referent");
    input.signal?.throwIfAborted();
    const full = new NativeLogicalFullCapture(capture, context, bytes, observation, freezeNativeLogical(actions), cleanup, transfer);
    bytes = undefined;
    actions = undefined;
    return full;
  } catch (error) {
    bytes?.fill(0);
    bytes = undefined;
    actions = undefined;
    try { await cleanup(); } catch (failure) { throw new AggregateError([error, failure], "native logical assembly and cleanup failed"); }
    throw error;
  }
}
