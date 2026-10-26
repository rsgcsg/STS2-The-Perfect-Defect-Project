import { createHash } from "node:crypto";
import { lstat, readFile } from "node:fs/promises";
import { dirname, join } from "node:path";

export const INITIAL_PROGRESS_ORIGINAL_SCHEMA = "spireagent/native-initial-progress-original-v1";
export const MAX_INITIAL_PROGRESS_BYTES = 16 * 1024 * 1024;
export interface InitialProgressOriginalMetadata {
  schema: typeof INITIAL_PROGRESS_ORIGINAL_SCHEMA;
  source: "native_progress_file";
  status: "captured" | "unknown";
  runtime_instance_id: string;
  profile_generation_id: string | null;
  native_profile_id: number | null;
  seed: string;
  captured_at: string;
  bytes: number | null;
  sha256: string | null;
  reason: string | null;
}
export interface InitialProgressOriginal { metadata: InitialProgressOriginalMetadata; bytes: Buffer | null }

/** Private environment provenance. No parsing/normalization of native save bytes. */
export function validateInitialProgressOriginal(value: unknown): InitialProgressOriginalMetadata {
  const m = value as InitialProgressOriginalMetadata;
  const fields = ["schema", "source", "status", "runtime_instance_id", "profile_generation_id", "native_profile_id", "seed", "captured_at", "bytes", "sha256", "reason"];
  if (!m || typeof m !== "object" || Array.isArray(m)
      || JSON.stringify(Object.keys(m).sort()) !== JSON.stringify(fields.sort())
      || m.schema !== INITIAL_PROGRESS_ORIGINAL_SCHEMA || m.source !== "native_progress_file"
      || ![m.runtime_instance_id, m.seed].every(v => typeof v === "string" && v.length > 0 && v.length <= 128)
      || !(m.profile_generation_id === null || typeof m.profile_generation_id === "string" && m.profile_generation_id.length > 0 && m.profile_generation_id.length <= 128)
      || !(m.native_profile_id === null || Number.isSafeInteger(m.native_profile_id) && m.native_profile_id >= 1)
      || typeof m.captured_at !== "string" || !Number.isFinite(Date.parse(m.captured_at)))
    throw new Error("initial_progress_original_metadata");
  if (m.status === "captured") {
    if (m.profile_generation_id === null || m.native_profile_id === null
        || !Number.isSafeInteger(m.bytes) || m.bytes! < 1 || m.bytes! > MAX_INITIAL_PROGRESS_BYTES
        || typeof m.sha256 !== "string" || !/^[0-9a-f]{64}$/u.test(m.sha256) || m.reason !== null)
      throw new Error("initial_progress_original_captured");
  } else if (m.status !== "unknown" || m.bytes !== null || m.sha256 !== null
      || typeof m.reason !== "string" || !m.reason || m.reason.length > 256)
    throw new Error("initial_progress_original_unknown");
  return m;
}
export function assertInitialProgressOriginalBytes(metadata: InitialProgressOriginalMetadata, bytes: Buffer | null): void {
  validateInitialProgressOriginal(metadata);
  if (metadata.status === "captured" ? bytes === null || bytes.byteLength !== metadata.bytes
      || createHash("sha256").update(bytes).digest("hex") !== metadata.sha256 : bytes !== null)
    throw new Error("initial_progress_original_bytes");
}
export async function readInitialProgressOriginal(path: string, expectedSha256: string): Promise<InitialProgressOriginal> {
  const info = await lstat(path);
  if (!info.isFile() || info.isSymbolicLink() || info.size > 65536) throw new Error("initial_progress_original_descriptor");
  const descriptor = await readFile(path);
  if (!/^[0-9a-f]{64}$/u.test(expectedSha256) || createHash("sha256").update(descriptor).digest("hex") !== expectedSha256)
    throw new Error("initial_progress_original_descriptor_binding");
  const metadata = validateInitialProgressOriginal(JSON.parse(descriptor.toString("utf8")));
  let bytes: Buffer | null = null;
  if (metadata.status === "captured") {
    const file = join(dirname(path), "initial-progress.bin"), rawInfo = await lstat(file);
    if (!rawInfo.isFile() || rawInfo.isSymbolicLink() || rawInfo.size !== metadata.bytes) throw new Error("initial_progress_original_file");
    bytes = await readFile(file);
  }
  assertInitialProgressOriginalBytes(metadata, bytes);
  return { metadata, bytes };
}
