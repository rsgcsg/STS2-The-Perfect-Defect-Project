import { z } from "zod";
import { isJsonObject } from "./json.js";
import { decodePlayerRead, decodePlayerSnapshot } from "./protocol.js";
import { hasFinitePublicNumbers } from "./publicCombat.js";
import { assembleRetainedBytes, publicBytesSha256 } from "./retainedBytes.js";
import { SEALED_OBSERVATION_MAX_BYTES, SEALED_OBSERVATION_DEFAULT_CHUNK_BYTES,
  SEALED_OBSERVATION_MAX_CHUNK_BYTES, validateSealedChunkLimit } from "./sealedObservation.js";

/** Original current public facts. Consumer protocol completeness is a separate judgment. */
export const PUBLIC_BASIS_PROFILE = "public-basis-1" as const;
export const PUBLIC_BASIS_SCHEMA = "sts2.player-environment/public-basis-1" as const;
export const PUBLIC_BASIS_SCOPE = "current_public_snapshot_and_advertised_read_instances" as const;
export const PUBLIC_BASIS_READ_PROFILE = "public-basis-sealed-1" as const;
export const PUBLIC_BASIS_ROUTE = "/api/player-environment/public-basis";
const wire = (name: string) => `sts2.player-environment/public-basis-${name}-1`;
const id = z.string().min(1).max(128).regex(/^[A-Za-z0-9_.:-]+$/u);
const cursor = z.string().min(1).max(512).regex(/^[A-Za-z0-9_-]+$/u);
const digest = z.string().regex(/^[a-f0-9]{64}$/u);
const count = z.number().int().positive().max(SEALED_OBSERVATION_MAX_BYTES);
const timestamp = z.string().datetime({ offset: true });
const session = z.object({ runtime_instance_id: z.string().min(1), environment_fingerprint: z.string().min(1) }).strict();
const handleSchema = z.object({ schema: z.literal(wire("handle")), read_profile: z.literal(PUBLIC_BASIS_READ_PROFILE),
  input_profile: z.literal(PUBLIC_BASIS_PROFILE), capture_id: id, source_snapshot_id: id, session,
  generation_id: id, game_continuity_id: id.nullable(), captured_at: timestamp, expires_at: timestamp,
  total_bytes: count, sha256: digest, first_cursor: cursor, capture_ordinal: z.number().int().positive().safe()
}).strict().refine(value => Date.parse(value.expires_at) > Date.parse(value.captured_at), "invalid capture lifetime");
const chunkSchema = z.object({ schema: z.literal(wire("chunk")), capture_id: id, sha256: digest,
  offset: z.number().int().nonnegative().max(SEALED_OBSERVATION_MAX_BYTES), total_bytes: count,
  data_base64: z.string().min(4).max(4 * Math.ceil(SEALED_OBSERVATION_MAX_CHUNK_BYTES / 3))
    .regex(/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u),
  next_cursor: cursor.nullable(), end: z.boolean()
}).strict().refine(value => value.end === (value.next_cursor === null), "invalid chunk termination");
const releaseSchema = z.object({ schema: z.literal(wire("release")), capture_id: id, released: z.literal(true) }).strict();
const capabilitiesSchema = z.object({ schema: z.literal(wire("capabilities")), read_profile: z.literal(PUBLIC_BASIS_READ_PROFILE),
  input_profile: z.literal(PUBLIC_BASIS_PROFILE), capture_schema: z.literal(wire("handle")), chunk_schema: z.literal(wire("chunk")),
  release_schema: z.literal(wire("release")), current_route: z.literal(`${PUBLIC_BASIS_ROUTE}/current`),
  read_route: z.literal(`${PUBLIC_BASIS_ROUTE}/read`), release_route: z.literal(`${PUBLIC_BASIS_ROUTE}/release`),
  max_capsule_bytes: count, max_retained_bytes: z.number().int().positive().safe(), max_capsules: z.number().int().positive().safe(),
  ttl_ms: z.number().int().positive().safe(), default_chunk_bytes: z.number().int().positive(), min_chunk_bytes: z.number().int().positive(),
  max_chunk_bytes: z.number().int().positive().max(SEALED_OBSERVATION_MAX_CHUNK_BYTES), creates_mutation_authority: z.literal(false),
  scope: z.literal(PUBLIC_BASIS_SCOPE)
}).strict().refine(value => value.max_retained_bytes >= value.max_capsule_bytes && value.min_chunk_bytes <= value.default_chunk_bytes
  && value.default_chunk_bytes <= value.max_chunk_bytes, "inconsistent retention capability");
const readSchema = z.object({ read_id: id, kind: z.string().min(1), response: z.record(z.unknown()).nullable(),
  error: z.string().min(1).nullable() }).strict();
const basisSchema = z.object({ schema: z.literal(PUBLIC_BASIS_SCHEMA), revision: z.literal("1"),
  capture_scope: z.literal(PUBLIC_BASIS_SCOPE), capture_status: z.enum(["complete", "partial"]),
  snapshot: z.record(z.unknown()), reads: z.array(readSchema).max(10_000), missing: z.array(z.string().min(1))
}).strict();
export type PublicBasis = z.infer<typeof basisSchema>;
export type PublicBasisHandle = z.infer<typeof handleSchema>;
export type PublicBasisChunk = z.infer<typeof chunkSchema>;
export type PublicBasisRelease = z.infer<typeof releaseSchema>;
export type PublicBasisCapabilities = z.infer<typeof capabilitiesSchema>;
export interface FullPublicBasis { readonly capture: PublicBasisHandle; readonly basis: PublicBasis; readonly serializedObservation: string }
export const decodePublicBasisHandle = (value: unknown): PublicBasisHandle => handleSchema.parse(value);
export const decodePublicBasisChunk = (value: unknown): PublicBasisChunk => chunkSchema.parse(value);
export const decodePublicBasisRelease = (value: unknown): PublicBasisRelease => releaseSchema.parse(value);
export const decodePublicBasisCapabilities = (value: unknown): PublicBasisCapabilities => capabilitiesSchema.parse(value);

const canonical = (value: unknown): string => {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
    .map(([key, child]) => `${JSON.stringify(key)}:${canonical(child)}`).join(",")}}`;
  return JSON.stringify(value);
};
export function decodePublicBasis(value: unknown): PublicBasis {
  if (!isJsonObject(value)) throw new Error("public basis must contain only finite JSON facts");
  const basis = basisSchema.parse(value);
  if (!hasFinitePublicNumbers(basis)) throw new Error("public basis has nonfinite facts");
  const snapshot = decodePlayerSnapshot(basis.snapshot).data;
  if (snapshot.information_policy.includes_hidden_information !== false) throw new Error("source is not public");
  if (basis.reads.length !== snapshot.reads.length || new Set(basis.reads.map(read => read.read_id)).size !== basis.reads.length
    || new Set(snapshot.reads.map(read => read.read_id)).size !== snapshot.reads.length)
    throw new Error("public Read inventory is incomplete or duplicated");
  const missing: string[] = [];
  for (const listed of snapshot.reads) {
    const captured = basis.reads.find(read => read.read_id === listed.read_id);
    if (!captured || captured.kind !== listed.kind) throw new Error("public Read kind or identity changed");
    if (captured.response === null) {
      if (captured.error === null) throw new Error("failed Read has no cause");
      missing.push(`read:${listed.read_id}:${captured.error}`); continue;
    }
    const read = decodePlayerRead(captured.response).data;
    if (captured.error !== null || read.read_id !== listed.read_id || read.kind !== listed.kind
      || read.expected_snapshot_id !== snapshot.snapshot_id || read.observed_snapshot_id !== snapshot.snapshot_id
      || read.content_schema !== listed.content_schema || read.ordering_semantics !== listed.ordering_semantics
      || read.visibility_basis !== listed.visibility_basis
      || canonical(read.completeness.hidden_by_policy) !== canonical(listed.hidden_by_policy) || (read.target_referent_id ?? null) !== (listed.target_referent_id ?? null)
      || canonical(read.session) !== canonical(snapshot.session)
      || canonical(read.information_policy) !== canonical(snapshot.information_policy)) throw new Error("Read source binding mismatch");
    if (read.completeness.status !== "complete" || read.completeness.missing.length) missing.push(`read:${listed.read_id}:partial`);
  }
  if (snapshot.completeness.status !== "complete" || snapshot.completeness.missing.length) missing.push("snapshot:partial");
  if (canonical(basis.missing) !== canonical(missing) || basis.capture_status !== (missing.length ? "partial" : "complete"))
    throw new Error("public capture status does not match original inventory");
  return basis;
}

export async function verifyPublicBasis(capture: PublicBasisHandle, serializedObservation: string): Promise<FullPublicBasis> {
  capture = decodePublicBasisHandle(capture);
  const bytes = new TextEncoder().encode(serializedObservation);
  if (bytes.length !== capture.total_bytes || await publicBytesSha256(bytes) !== capture.sha256)
    throw new Error("public source bytes do not match retained identity");
  const basis = decodePublicBasis(JSON.parse(serializedObservation));
  if (basis.snapshot.snapshot_id !== capture.source_snapshot_id || canonical(basis.snapshot.session) !== canonical(capture.session))
    throw new Error("public source handle binding mismatch");
  return { capture, basis, serializedObservation };
}

export async function assemblePublicBasis(capture: PublicBasisHandle,
  read: (input: { captureId: string; cursor: string; maxBytes: number }) => Promise<PublicBasisChunk>,
  maxBytes = SEALED_OBSERVATION_DEFAULT_CHUNK_BYTES): Promise<FullPublicBasis> {
  capture = decodePublicBasisHandle(capture); validateSealedChunkLimit(maxBytes);
  const bytes = await assembleRetainedBytes(capture, async input => decodePublicBasisChunk(await read(input)), maxBytes);
  return verifyPublicBasis(capture, new TextDecoder("utf-8", { fatal: true }).decode(bytes));
}

export function serializePublicBasisRecord(full: FullPublicBasis): string {
  return JSON.stringify({ schema: wire("record"), capture: decodePublicBasisHandle(full.capture),
    serialized_observation: full.serializedObservation });
}
export async function readPublicBasisRecord(text: string): Promise<FullPublicBasis> {
  if (Buffer.byteLength(text, "utf8") > 6 * SEALED_OBSERVATION_MAX_BYTES + 16_384) throw new Error("public basis archive exceeds limit");
  const record = z.object({ schema: z.literal(wire("record")), capture: handleSchema,
    serialized_observation: z.string().min(1).max(SEALED_OBSERVATION_MAX_BYTES) }).strict().parse(JSON.parse(text));
  return verifyPublicBasis(record.capture, record.serialized_observation);
}
