import { describe, expect, it } from "vitest";
import { readFile, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { readGlobalArtifact } from "../src/global-artifact-storage.js";

describe("GLOBAL lossless storage and failure prefixes", () => {
  it("retains more than the escaped 512 MiB logical run size without changing any byte", async () => {
    const f = await globalRuntimeFixture();
    try {
      let logical = 0, physical = 0;
      for (let i = 0; i < 34; i++) {
        const original = Buffer.alloc(16 * 1024 * 1024, 32); original.write(String(i));
        const ref = await f.evidence.storeGlobalArtifact("source", original);
        expect(ref.path.endsWith(".bin.gz")).toBe(true);
        expect(Buffer.compare(await readGlobalArtifact(f.evidence.directory, ref), original)).toBe(0);
        expect(await f.evidence.storeGlobalArtifact("source", original)).toEqual(ref);
        logical += ref.bytes; physical += (await stat(join(f.evidence.directory, ref.path))).size;
      }
      expect(logical).toBeGreaterThan(512 * 1024 * 1024);
      expect(physical).toBeLessThan(8 * 1024 * 1024);
    } finally { await f.close(); }
  }, 30000);
  it("rejects changed encoded bytes and expansion beyond the original bound", async () => {
    const f = await globalRuntimeFixture();
    try {
      const ref = await f.evidence.storeGlobalArtifact("source", Buffer.alloc(1024 * 1024, 32));
      await expect(readGlobalArtifact(f.evidence.directory, { ...ref, bytes: 100 })).rejects.toThrow();
      await writeFile(join(f.evidence.directory, ref.path), Buffer.from("broken gzip"));
      await expect(readGlobalArtifact(f.evidence.directory, ref)).rejects.toThrow();
    } finally { await f.close(); }
  });
  it("real Stop preserves an unsealed diagnostic inventory without inventing a typed seal", async () => {
    const f = await globalRuntimeFixture();
    try {
      const stage = await f.evidence.stageGlobalCaptureResponse(
        "capture-request-11111111-1111-1111-1111-111111111111", 1, Buffer.from("received wire prefix"));
      await expect(f.evidence.finalizeForStop({ status: "tainted", tainted: true, mode: "human" })).rejects.toThrow();
      const diagnostic = JSON.parse(await readFile(join(f.evidence.directory, "diagnostic-manifest.json"), "utf8"));
      expect(diagnostic).toMatchObject({ schema: "sts2.policy-runtime/unsealed-diagnostic-1", complete: false, status: "unsealed" });
      expect(diagnostic.files.some((entry: any) => entry.path === stage.path)).toBe(true);
      expect(await readFile(join(f.evidence.directory, stage.path), "utf8")).toBe("received wire prefix");
      await f.evidence.releaseGlobalCaptureResponse(stage);
    } finally { await f.close(); }
  });
});
