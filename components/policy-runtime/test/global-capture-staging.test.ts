import { describe, expect, it } from "vitest";
import { readFile, writeFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { readGlobalCaptureStaging } from "../src/evidence.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";
import { restoreGlobalCaptureResponse } from "../src/global-capture-storage.js";

const request = "capture-request-11111111-1111-1111-1111-111111111111";
describe("capture response durability before source/ref normalization", () => {
  it("blocks sealing with staging, releases it after durable source refs, and still replays", async () => {
    const f = await globalRuntimeFixture();
    try {
      const stage = await f.evidence.stageGlobalCaptureResponse(request, 1, Buffer.from("original-wire"));
      expect(await readGlobalCaptureStaging(f.evidence.directory)).toEqual([stage]);
      await expect(f.evidence.finalize({ status: "stopped", tainted: false, mode: "human" })).rejects.toThrow("staging is not finalized");
      await f.evidence.releaseGlobalCaptureResponse(stage);
      expect((await f.runtime.tick()).type).toBe("delivered"); expect((await f.runtime.tick()).type).toBe("closed");
      expect(await readGlobalCaptureStaging(f.evidence.directory)).toEqual([]);
      expect((await readdir(f.evidence.directory)).some(n => n.startsWith("capture-staging-"))).toBe(false);
      expect((await replayGlobalAgentRun(f.evidence.directory)).verdict).toBe("verified");
    } finally { await f.close(); }
  });
  it.each(["failure", "cancel"])("retains received original responses inline on a normal %s", async kind => {
    const f = await globalRuntimeFixture();
    const original = f.transport.capture.bind(f.transport);
    f.transport.capture = async options => {
      await original(options);
      if (kind === "cancel") throw new DOMException("synthetic capture cancel", "AbortError");
      throw new Error("synthetic capture failure");
    };
    try {
      expect((await f.runtime.tick()).type).toBe("not_admitted"); await f.runtime.stop();
      const rows = await f.events();
      const responses = rows.filter(e => e.kind === "global_transport_response" && e.payload.operation === "capture");
      expect(responses.length).toBeGreaterThan(0);
      for (const e of responses) {
        const bytes = await readFile(join(f.evidence.directory, e.payload.artifact.path));
        expect(JSON.parse(bytes.toString()).schema).not.toBe("sts2.policy-runtime/global-capture-response-1");
        expect(await restoreGlobalCaptureResponse(bytes, async () => { throw new Error("no source"); })).toEqual(bytes);
      }
      expect(await readGlobalCaptureStaging(f.evidence.directory)).toEqual([]);
      expect((await replayGlobalAgentRun(f.evidence.directory)).verdict).toBe("verified");
    } finally { await f.close(); }
  });
  it("finishes a late durable response hook before Cancel seals the run", async () => {
    const f = await globalRuntimeFixture();
    const bytes = Buffer.from("synthetic received wire prefix");
    let entered!: () => void, release!: () => void;
    const writing = new Promise<void>(resolve => { entered = resolve; });
    const gate = new Promise<void>(resolve => { release = resolve; });
    const original = f.evidence.stageGlobalCaptureResponse.bind(f.evidence);
    f.evidence.stageGlobalCaptureResponse = async (...args) => { entered(); await gate; return original(...args); };
    f.transport.capture = async options => {
      void options.onResponseBytes!(bytes);
      throw new DOMException("capture cancelled while response staging", "AbortError");
    };
    try {
      const tick = f.runtime.tick(); await writing;
      const stopped = f.runtime.stop(); release(); await tick; await stopped;
      expect(await readGlobalCaptureStaging(f.evidence.directory)).toEqual([]);
      const events = await f.events(), response = events.find(e => e.kind === "global_transport_response");
      expect(await readFile(join(f.evidence.directory, response.payload.artifact.path))).toEqual(bytes);
      expect(events.at(-1).kind).toBe("stopped");
      expect((await replayGlobalAgentRun(f.evidence.directory)).verdict).toBe("verified");
    } finally { release(); await f.close(); }
  });
  it("read-only recovery finds byte-identical received data after a hard-killed writer", async () => {
    const f = await globalRuntimeFixture();
    const original = Buffer.from('{"schema":"synthetic-capture-response","data_base64":"YWJj"}\r\n');
    const rawPath = join(f.root, "wire.bin"); await writeFile(rawPath, original);
    const child = spawn(process.execPath, [fileURLToPath(new URL("./fixtures/global-capture-stage-child.mjs", import.meta.url)),
      fileURLToPath(new URL("../dist/evidence.js", import.meta.url)), join(f.root, "agent.json"), f.root, rawPath], { stdio: ["ignore", "pipe", "pipe"] });
    try {
      const ready = await Promise.race([
        once(child.stdout!, "data").then(([data]) => String(data)),
        once(child, "exit").then(([code]) => { throw new Error(`writer exited before staging: ${code}`); })
      ]);
      expect(ready).toContain("staged");
      const exit = once(child, "exit"); child.kill("SIGKILL"); await exit;
      const directory = join(f.root, "hard-killed-capture"), refs = await readGlobalCaptureStaging(directory);
      expect(refs).toHaveLength(1); expect(refs[0]).toMatchObject({ capture_request_id: request, response_ordinal: 1, bytes: original.length });
      expect(await readFile(join(directory, refs[0]!.path))).toEqual(original);
      expect((await readdir(directory)).includes("evidence-manifest.json")).toBe(false);
      await writeFile(join(directory, refs[0]!.path), "corrupted");
      await expect(readGlobalCaptureStaging(directory)).rejects.toThrow("global_staging_bytes");
    } finally { if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL"); await f.close(); }
  });
});
