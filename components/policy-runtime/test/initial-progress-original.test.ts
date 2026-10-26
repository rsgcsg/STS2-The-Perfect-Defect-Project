import { createHash } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { readInitialProgressOriginal } from "../src/initial-progress-original.js";
const sha = (value: Buffer | string) => createHash("sha256").update(value).digest("hex");

it("reads literal private save bytes under the original descriptor binding and preserves unknown", async () => {
  const directory = await mkdtemp(join(tmpdir(), "initial-progress-original-"));
  try {
    const raw = Buffer.from([255, 0, 13, 10, 123, 32, 125, 10]), path = join(directory, "original.json");
    const metadata = { schema: "spireagent/native-initial-progress-original-v1", source: "native_progress_file", status: "captured",
      runtime_instance_id: "runtime", profile_generation_id: "generation", native_profile_id: 1, seed: "seed",
      captured_at: new Date().toISOString(), bytes: raw.length, sha256: sha(raw), reason: null };
    const descriptor = JSON.stringify(metadata);
    await writeFile(path, descriptor); await writeFile(join(directory, "initial-progress.bin"), raw);
    expect((await readInitialProgressOriginal(path, sha(descriptor))).bytes).toEqual(raw);
    await writeFile(join(directory, "initial-progress.bin"), Buffer.from("altered!"));
    await expect(readInitialProgressOriginal(path, sha(descriptor))).rejects.toThrow("initial_progress_original_bytes");
    await writeFile(path, JSON.stringify({ ...metadata, seed: "changed" }));
    await expect(readInitialProgressOriginal(path, sha(descriptor))).rejects.toThrow("initial_progress_original_descriptor_binding");
    const unknown = JSON.stringify({ ...metadata, status: "unknown", bytes: null, sha256: null,
      native_profile_id: null, profile_generation_id: null, reason: "native_file_unavailable" });
    await writeFile(path, unknown);
    const result = await readInitialProgressOriginal(path, sha(unknown));
    expect(result.bytes).toBeNull(); expect(result.metadata.status).toBe("unknown");
  } finally { await rm(directory, { recursive: true, force: true }); }
});
