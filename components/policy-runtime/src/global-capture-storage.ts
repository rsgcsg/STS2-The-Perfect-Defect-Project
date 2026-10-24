import { createHash } from "node:crypto";
import { readPublicBasisRecord, type FullPublicBasis } from "@rsgcsg/sts2-connector-client";
import { canonicalJson, type GlobalArtifact } from "./evidence.js";

export const GLOBAL_CAPTURE_RESPONSE_STORAGE = "sts2.policy-runtime/global-capture-response-1";
const sha = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
/** Physical representation only. Preserve the exact original wire bytes around
 * the base64 token; the source record owns the same unencoded payload once. */
export function normalizeGlobalCaptureResponse(bytes: Uint8Array, source: GlobalArtifact, full: FullPublicBasis): Buffer {
  const raw = Buffer.from(bytes), text = new TextDecoder("utf-8", { fatal: true }).decode(raw);
  let wire: any;
  try { wire = JSON.parse(text); } catch { return raw; }
  if (wire?.schema !== "sts2.player-environment/public-basis-chunk-1") return raw;
  const match = /"data_base64"\s*:\s*"([A-Za-z0-9+/=]*)"/u.exec(text);
  if (!match || match[1] !== wire.data_base64 || !Number.isSafeInteger(wire.offset) || typeof wire.data_base64 !== "string")
    throw new Error("global_capture_storage_token");
  const sliceBytes = Buffer.from(wire.data_base64, "base64").length;
  const payload = Buffer.from(full.serializedObservation, "utf8"), slice = payload.subarray(wire.offset, wire.offset + sliceBytes);
  if (wire.sha256 !== full.capture.sha256 || wire.total_bytes !== payload.length || slice.length !== sliceBytes
    || slice.toString("base64") !== wire.data_base64) throw new Error("global_capture_storage_source_mismatch");
  const start = match.index + match[0].length - match[1]!.length - 1;
  const { data_base64: _data, ...envelope } = wire;
  return Buffer.from(canonicalJson({ schema: GLOBAL_CAPTURE_RESPONSE_STORAGE, source,
    original_sha256: sha(raw), original_bytes: raw.length, slice_bytes: sliceBytes, envelope,
    prefix: text.slice(0, start), suffix: text.slice(start + match[1]!.length) }));
}
/** Legacy wire objects are returned unchanged. New references are reconstructed
 * before the existing SDK decoder/assembler applies every original invariant. */
export async function restoreGlobalCaptureResponse(bytes: Buffer,
  sourceBytes: (ref: GlobalArtifact) => Promise<Buffer>,
  sourceRecord?: (ref: GlobalArtifact) => Promise<FullPublicBasis>): Promise<Buffer> {
  let stored: any;
  try { stored = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); } catch { return bytes; }
  if (stored?.schema !== GLOBAL_CAPTURE_RESPONSE_STORAGE) return bytes;
  if (canonicalJson(Object.keys(stored).sort()) !== canonicalJson(["schema", "source", "original_sha256", "original_bytes", "slice_bytes", "envelope", "prefix", "suffix"].sort())
    || typeof stored.prefix !== "string" || typeof stored.suffix !== "string" || !/^[a-f0-9]{64}$/u.test(stored.original_sha256)
    || !Number.isSafeInteger(stored.original_bytes) || stored.original_bytes < 1 || stored.original_bytes > 96 * 1024 * 1024)
    throw new Error("global_capture_storage_fields");
  const full = sourceRecord ? await sourceRecord(stored.source)
    : await readPublicBasisRecord(new TextDecoder("utf-8", { fatal: true }).decode(await sourceBytes(stored.source)));
  const e = stored.envelope, payload = Buffer.from(full.serializedObservation, "utf8");
  if (!Number.isSafeInteger(e?.offset) || e.offset < 0 || !Number.isSafeInteger(stored.slice_bytes) || stored.slice_bytes < 1
    || e.offset + stored.slice_bytes > payload.length || e.sha256 !== full.capture.sha256 || e.total_bytes !== payload.length)
    throw new Error("global_capture_storage_slice");
  const raw = Buffer.from(stored.prefix + payload.subarray(e.offset, e.offset + stored.slice_bytes).toString("base64") + stored.suffix);
  const { data_base64: _data, ...envelope } = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
  if (raw.length !== stored.original_bytes || sha(raw) !== stored.original_sha256 || canonicalJson(envelope) !== canonicalJson(e))
    throw new Error("global_capture_storage_original_bytes");
  return raw;
}
