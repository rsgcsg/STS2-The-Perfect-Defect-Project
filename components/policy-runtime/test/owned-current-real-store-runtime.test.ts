import { readFileSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { realStoreBridge } from "../../connector/sdk/typescript/test/realStoreBridge.mjs";
import { SyntheticNativeHttp } from "./native-runtime-fixtures.js";
import { AgentRunEvidence } from "../src/evidence.js";
import { NdjsonAgentSessionPort } from "../src/agent-session-port.js";
import { PolicyRuntime } from "../src/runtime.js";
import type { AgentManifest } from "../src/agent-session-contracts.js";

const shared = JSON.parse(readFileSync(new URL("../contracts/fixtures/owned-current-known-stale-v1.json", import.meta.url), "utf8"));
const child = fileURLToPath(new URL("./fixtures/sampled-agent-child.mjs", import.meta.url));

// Whole ownership chain: real C# Store/Projector -> HTTP REST SDK -> SDK assembly
// -> Runtime -> actual programmed OS Ndjson child. Control/Await are synthetic;
// this gate proves storage/lifetime and does not execute native legality/game.
describe("Runtime ownership on actual game-free Store/Projector", () => {
  it("holds only the acknowledged basis across 100 discarded reads, replaces it and releases before local session close", async () => {
    const source = new SyntheticNativeHttp(1), backend = await realStoreBridge({ fallback: (url, body) => source.route(url, body).value });
    const root = await mkdtemp(join(tmpdir(), "owned-real-store-runtime-")), manifest = structuredClone(shared.manifest) as AgentManifest;
    manifest.support.interaction_kinds = ["*"]; manifest.support.action_verbs = ["*"];
    const manifestPath = join(root, "manifest.json"); await writeFile(manifestPath, JSON.stringify(manifest));
    const evidence = await AgentRunEvidence.createSession({ root, agentManifest: manifest, mode: "shadow", runtimeVersion: "fixture", runtimeCodeSha256: "1".repeat(64) });
    const port = NdjsonAgentSessionPort.spawn(process.execPath, [child, manifestPath], manifest.adapter, manifest.limits, { executionPolicy: manifest.execution_policy });
    let runtime;
    try {
      runtime = await PolicyRuntime.forAgent({ manifest, port, evidence, environment: backend.rest, mode: "shadow",
        runtimeIdentity: { version: "fixture", code_sha256: "1".repeat(64) }, autoBudget: { maxSubmissions: 200, maxPolicyCalls: 1000, deadlineMs: 60000 } });
      expect((await runtime.tick()).type).toBe("shadow"); const initial = await backend.stats(); expect(initial).toMatchObject({ live_captures: 1, handles: 1, now: 0 });
      for (let i = 0; i < 100; i++) { expect((await runtime.tick()).type).toBe("awaited"); expect(await backend.stats()).toEqual(initial); }
      expect(runtime.status().session.state_version).toBe(1);
      await backend.call("advance"); expect((await runtime.tick()).type).toBe("shadow"); expect(runtime.status().session.state_version).toBe(2);
      expect(await backend.stats()).toMatchObject({ live_captures: 1, handles: 1, now: 0 });
      await runtime.stop(); expect(await backend.stats()).toMatchObject({ charged_bytes: 0, charged_buffers: 0, live_captures: 0, handles: 0, now: 0 });
      expect(backend.requests.filter(r => r.operation === "actions")).toHaveLength(0);
      const events = (await readFile(join(evidence.directory, "events.jsonl"), "utf8")).trim().split("\n").map(line => JSON.parse(line));
      expect(events.filter(e => e.kind === "agent_sample_query_discarded")).toHaveLength(100); expect(events.filter(e => e.kind === "agent_consumed")).toHaveLength(2);
    } finally { await runtime?.stop().catch(() => undefined); port.close(); await backend.close(); await rm(root, { recursive: true, force: true }); }
  }, 30000);
  it("releases the acknowledged backend basis even when Stop evidence finalization fails", async () => {
    const source = new SyntheticNativeHttp(1), backend = await realStoreBridge({ fallback: (url, body) => source.route(url, body).value });
    const root = await mkdtemp(join(tmpdir(), "owned-real-store-stop-")), manifest = structuredClone(shared.manifest) as AgentManifest;
    manifest.support.interaction_kinds = ["*"]; manifest.support.action_verbs = ["*"];
    const path = join(root, "manifest.json"); await writeFile(path, JSON.stringify(manifest));
    const evidence = await AgentRunEvidence.createSession({ root, agentManifest: manifest, mode: "shadow", runtimeVersion: "fixture", runtimeCodeSha256: "1".repeat(64) });
    const port = NdjsonAgentSessionPort.spawn(process.execPath, [child, path], manifest.adapter, manifest.limits, { executionPolicy: manifest.execution_policy }); let runtime;
    try {
      runtime = await PolicyRuntime.forAgent({ manifest, port, evidence, environment: backend.rest, mode: "shadow", runtimeIdentity: { version: "fixture", code_sha256: "1".repeat(64) } });
      expect((await runtime.tick()).type).toBe("shadow"); expect((await backend.stats()).charged_bytes).toBeGreaterThan(0);
      vi.spyOn(evidence, "finalize").mockRejectedValue(new Error("injected finalization failure"));
      await expect(runtime.stop()).rejects.toThrow(); expect(await backend.stats()).toMatchObject({ charged_bytes: 0, charged_buffers: 0, live_captures: 0, handles: 0 }); expect(runtime.status()).toMatchObject({ lifecycle: "stopped", tainted: true });
    } finally { vi.restoreAllMocks(); await runtime?.stop().catch(() => undefined); port.close(); await backend.close(); await rm(root, { recursive: true, force: true }); }
  });
});

async function faultFixture() {
  const source = new SyntheticNativeHttp(1), backend = await realStoreBridge({ fallback: (url, body) => source.route(url, body).value });
  const root = await mkdtemp(join(tmpdir(), "owned-real-store-fault-")), manifest = structuredClone(shared.manifest) as AgentManifest;
  manifest.support.interaction_kinds = ["*"]; manifest.support.action_verbs = ["*"];
  const path = join(root, "manifest.json"); await writeFile(path, JSON.stringify(manifest));
  const evidence = await AgentRunEvidence.createSession({ root, agentManifest: manifest, mode: "shadow", runtimeVersion: "fixture", runtimeCodeSha256: "1".repeat(64) });
  const port = NdjsonAgentSessionPort.spawn(process.execPath, [child, path], manifest.adapter, manifest.limits, { executionPolicy: manifest.execution_policy });
  const runtime = await PolicyRuntime.forAgent({ manifest, port, evidence, environment: backend.rest, mode: "shadow", runtimeIdentity: { version: "fixture", code_sha256: "1".repeat(64) } });
  return { runtime, port, evidence, backend,
    close: async () => { vi.restoreAllMocks(); await runtime.stop().catch(() => undefined); port.close(); await backend.close(); await rm(root, { recursive: true, force: true }); } };
}

describe("owned reader transaction failures measured on actual Store", () => {
  it("releases the transferred stack reader if its Runtime map commit fails", async () => {
    const f = await faultFixture();
    try {
      const map = (f.runtime as unknown as { readerLeases: Map<string, unknown> }).readerLeases;
      vi.spyOn(map, "set").mockImplementationOnce(() => { throw new Error("injected reader map commit failure"); });
      await f.runtime.tick();
      expect(f.backend.requests.filter(r => r.operation === "current_owned")).toHaveLength(1);
      expect(f.backend.requests.filter(r => r.operation === "release" && r.body.retention_handle_id)).toHaveLength(1);
      expect(await f.backend.stats()).toMatchObject({ charged_bytes: 0, charged_buffers: 0, live_captures: 0, handles: 0 });
      expect(f.runtime.status()).toMatchObject({ mode: "human", session: { state_version: 0 } });
    } finally { await f.close(); }
  });
  it("releases the stack reader if acquisition registration evidence fails before map insertion", async () => {
    const f = await faultFixture(), append = f.evidence.append.bind(f.evidence);
    try {
      vi.spyOn(f.evidence, "append").mockImplementation((kind, payload, now) => kind === "native_acquisition_registered"
        ? Promise.reject(new Error("injected acquisition evidence failure")) : append(kind, payload, now));
      await f.runtime.tick().catch(() => undefined);
      expect(await f.backend.stats()).toMatchObject({ charged_bytes: 0, charged_buffers: 0, live_captures: 0, handles: 0 });
      expect(f.runtime.status()).toMatchObject({ mode: "human", tainted: true, session: { state_version: 0 } });
      expect(f.backend.requests.filter(r => r.operation === "release" && r.body.retention_handle_id)).toHaveLength(1);
    } finally { await f.close(); }
  });
  it("owns the decoded reader before assembly byte admission rejects the full capture", async () => {
    const f = await faultFixture(); let held: ReturnType<typeof f.port.byteBudget.reserve> | undefined;
    try {
      f.backend.afterReply(operation => {
        if (operation === "current_owned") held = f.port.byteBudget.reserve(f.port.byteBudget.maximum - f.port.byteBudget.used);
      });
      await f.runtime.tick().catch(() => undefined);
      expect(f.backend.requests.filter(r => r.operation === "current_owned")).toHaveLength(1);
      expect(f.backend.requests.filter(r => r.operation === "release" && r.body.retention_handle_id)).toHaveLength(1);
      expect(await f.backend.stats()).toMatchObject({ charged_bytes: 0, charged_buffers: 0, live_captures: 0, handles: 0 });
      expect(f.runtime.status().session.state_version).toBe(0);
    } finally { held?.release(); f.backend.afterReply(undefined); await f.close(); }
  });
  it("readiness evidence failure releases the discarded reader, fences immediately, and preserves the acknowledged basis", async () => {
    const f = await faultFixture(), append = f.evidence.append.bind(f.evidence);
    try {
      expect((await f.runtime.tick()).type).toBe("shadow"); const initial = await f.backend.stats();
      vi.spyOn(f.evidence, "append").mockImplementation((kind, payload, now) => kind === "agent_sample_query_discarded"
        ? Promise.reject(new Error("injected readiness evidence failure")) : append(kind, payload, now));
      await expect(f.runtime.tick()).rejects.toThrow("owned-reader cleanup failed");
      expect(await f.backend.stats()).toEqual(initial);
      expect(f.runtime.status()).toMatchObject({ mode: "human", tainted: true, controller: "released", session: { state_version: 1, agent_state: "known" } });
      await f.runtime.stop().catch(() => undefined); expect(await f.backend.stats()).toMatchObject({ charged_bytes: 0, live_captures: 0, handles: 0 });
    } finally { await f.close(); }
  });
  it("a failed original replacement-release response does not skip the new basis release on Stop", async () => {
    const f = await faultFixture(); let failed = false;
    try {
      expect((await f.runtime.tick()).type).toBe("shadow");
      f.backend.afterReply(operation => { if (operation === "release" && !failed) { failed = true; throw new Error("original release response lost after backend release"); } });
      await f.backend.call("advance"); await f.runtime.tick().catch(() => undefined);
      expect(failed).toBe(true); expect(f.runtime.status()).toMatchObject({ mode: "human", session: { state_version: 2, agent_state: "uncertain" } });
      expect(await f.backend.stats()).toMatchObject({ live_captures: 1, handles: 1 });
      await f.runtime.stop().catch(() => undefined);
      expect(await f.backend.stats()).toMatchObject({ charged_bytes: 0, charged_buffers: 0, live_captures: 0, handles: 0 });
      expect(f.backend.requests.filter(r => r.operation === "release" && r.body.retention_handle_id)).toHaveLength(2);
    } finally { await f.close(); }
  });
  it("detach failure still closes the child and frees the acknowledged reader and every local reservation", async () => {
    const f = await faultFixture();
    try {
      expect((await f.runtime.tick()).type).toBe("shadow");
      f.backend.afterReply(operation => { if (operation === "detach") throw new Error("injected original detach failure"); });
      await expect(f.runtime.stop()).rejects.toThrow("attempted all owned cleanup");
      expect(await f.backend.stats()).toMatchObject({ charged_bytes: 0, charged_buffers: 0, live_captures: 0, handles: 0 });
      expect(f.port.byteBudget.used).toBe(0); expect(f.runtime.status()).toMatchObject({ lifecycle: "stopped", tainted: true });
    } finally { f.backend.afterReply(undefined); await f.close(); }
  });
});
