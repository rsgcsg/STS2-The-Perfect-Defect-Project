import { createHash } from "node:crypto";
import { lstat, readFile } from "node:fs/promises";
import { join } from "node:path";
import { gzip, gunzip } from "node:zlib";
import { promisify } from "node:util";
import type { GlobalArtifact } from "./evidence.js";

const compress = promisify(gzip), expand = promisify(gunzip);
export const GLOBAL_ARTIFACT_MAX_BYTES = 96 * 1024 * 1024;
/** The event binds original bytes; the immutable inventory binds stored bytes.
 * Explicit .gz paths are lossless storage v1, never a different Agent input. */
export async function encodeGlobalArtifact(bytes: Buffer): Promise<{ suffix: string; stored: Buffer }> {
  if (bytes.length < 64 * 1024) return { suffix: ".bin", stored: bytes };
  const stored = await compress(bytes, { level: 3 });
  return stored.length < bytes.length ? { suffix: ".bin.gz", stored } : { suffix: ".bin", stored: bytes };
}

export async function readGlobalArtifact(directory: string, ref: GlobalArtifact): Promise<Buffer> {
  if (!/^[a-f0-9]{64}$/u.test(ref.sha256) || !Number.isSafeInteger(ref.bytes) || ref.bytes < 1
    || ref.bytes > GLOBAL_ARTIFACT_MAX_BYTES
    || ![`.bin`, `.bin.gz`].some(suffix => ref.path === `global-${ref.sha256}${suffix}`))
    throw new Error("global_artifact_reference");
  const path = join(directory, ref.path), stat = await lstat(path);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 1 || stat.size > GLOBAL_ARTIFACT_MAX_BYTES)
    throw new Error("global_artifact_file");
  const stored = await readFile(path);
  const bytes = ref.path.endsWith(".gz") ? await expand(stored, { maxOutputLength: ref.bytes }) : stored;
  if (bytes.length !== ref.bytes || createHash("sha256").update(bytes).digest("hex") !== ref.sha256)
    throw new Error("global_artifact_bytes");
  return bytes;
}
