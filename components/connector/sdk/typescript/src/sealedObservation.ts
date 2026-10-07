import { z } from "zod";
import { isJsonObject, type JsonObject } from "./json.js";
import { type DecodedPlayerPayload } from "./protocol.js";
import {
  TEXT_MENU_V2_PROFILE, TEXT_MENU_V2_OBSERVATION_CONTEXT_SCHEMA,
  decodeTextMenuV2Snapshot, type TextMenuV2ObservationContext
} from "./textMenuV2.js";

export const SEALED_OBSERVATION_READ_PROFILE = "text-menu-v2-sealed-1" as const;
export const SEALED_OBSERVATION_SCHEMA = "sts2.player-environment/sealed-observation-1" as const;
export const SEALED_OBSERVATION_CHUNK_SCHEMA = "sts2.player-environment/sealed-observation-chunk-1" as const;
export const SEALED_OBSERVATION_RELEASE_SCHEMA = "sts2.player-environment/sealed-observation-release-1" as const;
export const SEALED_OBSERVATION_CAPABILITIES_SCHEMA = "sts2.player-environment/sealed-observation-capabilities-1" as const;
export const SEALED_OBSERVATION_ROUTE = "/api/player-environment/sealed-observation";
export const SEALED_OBSERVATION_MAX_BYTES = 8 * 1024 * 1024;
export const SEALED_OBSERVATION_DEFAULT_CHUNK_BYTES = 64 * 1024;
export const SEALED_OBSERVATION_MIN_CHUNK_BYTES = 1024;
export const SEALED_OBSERVATION_MAX_CHUNK_BYTES = 1024 * 1024;

const id = z.string().min(1).max(128).regex(/^[A-Za-z0-9_.-]+$/u);
const cursor = z.string().min(1).max(512).regex(/^[A-Za-z0-9_-]+$/u);
const digest = z.string().regex(/^[a-f0-9]{64}$/u);
const timestamp = z.string().datetime({ offset: true });
const totalBytes = z.number().int().positive().max(SEALED_OBSERVATION_MAX_BYTES);
const session = z.object({ runtime_instance_id: z.string().min(1), environment_fingerprint: z.string().min(1) }).strict();
const captureSchema = z.object({
  schema: z.literal(SEALED_OBSERVATION_SCHEMA), read_profile: z.literal(SEALED_OBSERVATION_READ_PROFILE),
  input_profile: z.literal(TEXT_MENU_V2_PROFILE), capture_id: id, source_snapshot_id: id,
  session: session, generation_id: id, game_continuity_id: id.nullable(),
  captured_at: timestamp, expires_at: timestamp, total_bytes: totalBytes,
  sha256: digest, first_cursor: cursor, capture_ordinal: z.number().int().positive().max(Number.MAX_SAFE_INTEGER)
}).strict().superRefine((value, ctx) => {
  if (Date.parse(value.expires_at) <= Date.parse(value.captured_at))
    ctx.addIssue({ code: "custom", message: "capsule expires before its capture" });
});
const chunkSchema = z.object({
  schema: z.literal(SEALED_OBSERVATION_CHUNK_SCHEMA), capture_id: id, sha256: digest,
  offset: z.number().int().nonnegative().max(SEALED_OBSERVATION_MAX_BYTES), total_bytes: totalBytes,
  data_base64: z.string().min(4).max(4 * Math.ceil(SEALED_OBSERVATION_MAX_CHUNK_BYTES / 3))
    .regex(/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u),
  next_cursor: cursor.nullable(), end: z.boolean()
}).strict().superRefine((value, ctx) => {
  if (value.end !== (value.next_cursor === null))
    ctx.addIssue({ code: "custom", message: "chunk end and next cursor disagree" });
});
const releaseSchema = z.object({
  schema: z.literal(SEALED_OBSERVATION_RELEASE_SCHEMA), capture_id: id, released: z.literal(true)
}).strict();
const capabilitiesSchema = z.object({
  schema: z.literal(SEALED_OBSERVATION_CAPABILITIES_SCHEMA), read_profile: z.literal(SEALED_OBSERVATION_READ_PROFILE),
  input_profile: z.literal(TEXT_MENU_V2_PROFILE), capture_schema: z.literal(SEALED_OBSERVATION_SCHEMA),
  chunk_schema: z.literal(SEALED_OBSERVATION_CHUNK_SCHEMA), release_schema: z.literal(SEALED_OBSERVATION_RELEASE_SCHEMA),
  current_route: z.literal(`${SEALED_OBSERVATION_ROUTE}/current`),
  read_route: z.literal(`${SEALED_OBSERVATION_ROUTE}/read`), release_route: z.literal(`${SEALED_OBSERVATION_ROUTE}/release`),
  max_capsule_bytes: z.literal(SEALED_OBSERVATION_MAX_BYTES), max_retained_bytes: z.literal(64 * 1024 * 1024),
  max_capsules: z.literal(32), ttl_ms: z.literal(120_000), default_chunk_bytes: z.literal(SEALED_OBSERVATION_DEFAULT_CHUNK_BYTES),
  min_chunk_bytes: z.literal(SEALED_OBSERVATION_MIN_CHUNK_BYTES), max_chunk_bytes: z.literal(SEALED_OBSERVATION_MAX_CHUNK_BYTES),
  creates_mutation_authority: z.literal(false), scope: z.literal("sampled_public_text_menu_v2_current_cursor")
}).strict();

export type SealedObservationCapture = z.infer<typeof captureSchema>;
export type SealedObservationChunk = z.infer<typeof chunkSchema>;
export type SealedObservationRelease = z.infer<typeof releaseSchema>;
export type SealedObservationCapabilities = z.infer<typeof capabilitiesSchema>;
export interface FullTextMenuV2Observation {
  context: TextMenuV2ObservationContext;
  /** Exact original public Snapshot UTF-8 text, matching capture.sha256. */
  serializedSnapshot: string;
  /** Acquisition/scheduling metadata; not Model input or action authority. Already released by GetFull. */
  capture: SealedObservationCapture;
}
export interface SealedObservationReader {
  readSealed(input: { captureId: string; cursor: string; maxBytes?: number }): Promise<DecodedPlayerPayload<SealedObservationChunk>>;
}
function decode<T>(value: unknown, schema: z.ZodType<T>, label: string): DecodedPlayerPayload<T> {
  if (!isJsonObject(value)) throw new Error(`${label} is not an object`);
  const result = schema.safeParse(value);
  if (!result.success) throw new Error(`${label} failed strict decoding: ${result.error.message}`);
  return { raw: value as JsonObject, data: result.data };
}
export const decodeSealedObservationCapture = (value: unknown): DecodedPlayerPayload<SealedObservationCapture> =>
  decode(value, captureSchema, "sealed observation capture");
export const decodeSealedObservationChunk = (value: unknown): DecodedPlayerPayload<SealedObservationChunk> =>
  decode(value, chunkSchema, "sealed observation chunk");
export const decodeSealedObservationRelease = (value: unknown): DecodedPlayerPayload<SealedObservationRelease> =>
  decode(value, releaseSchema, "sealed observation release");
export const decodeSealedObservationCapabilities = (value: unknown): DecodedPlayerPayload<SealedObservationCapabilities> =>
  decode(value, capabilitiesSchema, "sealed observation capabilities");

export function validateSealedChunkLimit(maxBytes: number): void {
  if (!Number.isInteger(maxBytes) || maxBytes < SEALED_OBSERVATION_MIN_CHUNK_BYTES || maxBytes > SEALED_OBSERVATION_MAX_CHUNK_BYTES)
    throw new Error("sealed observation maxBytes is outside advertised limits");
}

/** Complete same-capture byte assembly. It never submits input or updates Model memory.
 * Complete bytes may describe settling/partial state; consumers still gate Model readiness. */
export async function assembleSealedTextMenuV2(
  capture: SealedObservationCapture, reader: SealedObservationReader,
  maxBytes = SEALED_OBSERVATION_DEFAULT_CHUNK_BYTES
): Promise<FullTextMenuV2Observation> {
  capture = decodeSealedObservationCapture(capture).data;
  validateSealedChunkLimit(maxBytes);
  const bytes = new Uint8Array(capture.total_bytes);
  const visited = new Set<string>();
  let offset = 0;
  let next: string | null = capture.first_cursor;
  // Every nonterminal chunk must fill its requested size; loop count is strictly bounded.
  while (next !== null) {
    if (visited.has(next)) throw new Error("sealed observation cursor cycle");
    visited.add(next);
    const chunk: SealedObservationChunk = decodeSealedObservationChunk((await reader.readSealed({
      captureId: capture.capture_id, cursor: next, maxBytes
    })).data).data;
    if (chunk.capture_id !== capture.capture_id || chunk.sha256 !== capture.sha256 ||
        chunk.total_bytes !== capture.total_bytes || chunk.offset !== offset)
      throw new Error("sealed observation identity or contiguous offset mismatch");
    const binary = atob(chunk.data_base64);
    const block = Uint8Array.from(binary, value => value.charCodeAt(0));
    // Reject noncanonical base64 as well as gaps, overlaps and overlong payloads.
    if (btoa(binary) !== chunk.data_base64 || block.length !== Math.min(maxBytes, bytes.length - offset))
      throw new Error("sealed observation chunk size or base64 mismatch");
    bytes.set(block, offset);
    offset += block.length;
    if (chunk.end !== (offset === bytes.length)) throw new Error("sealed observation premature or missing terminal chunk");
    next = chunk.next_cursor;
  }
  if (offset !== bytes.length) throw new Error("sealed observation assembly is incomplete");
  const actualDigest = Array.from(new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes.buffer)))
    .map(value => value.toString(16).padStart(2, "0")).join("");
  if (actualDigest !== capture.sha256) throw new Error("sealed observation digest mismatch");
  const serializedSnapshot = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  const snapshot = decodeTextMenuV2Snapshot(JSON.parse(serializedSnapshot)).data;
  if (snapshot.snapshot_id !== capture.source_snapshot_id ||
      snapshot.session.runtime_instance_id !== capture.session.runtime_instance_id ||
      snapshot.session.environment_fingerprint !== capture.session.environment_fingerprint)
    throw new Error("sealed observation source/session mismatch");
  return { capture, serializedSnapshot, context: { schema: TEXT_MENU_V2_OBSERVATION_CONTEXT_SCHEMA,
    snapshot, game_continuity_id: capture.game_continuity_id } };
}
