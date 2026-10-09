import { readFileSync } from "node:fs";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JsonObject, NativeLogicalTransportOperation, NativeLogicalTransportOptions, PlayerEnvironmentRestClient } from "@rsgcsg/sts2-connector-client";
import { AgentRunEvidence, AGENT_SAMPLE_STORAGE_LIMITS } from "../src/evidence.js";
import { AgentConsumptionLedger, type AgentAcquisition } from "../src/agent-session-consumption.js";
import { validateAgentManifest, type AgentManifest } from "../src/agent-session-contracts.js";
import { NdjsonAgentSessionPort } from "../src/agent-session-port.js";
import { PolicyRuntime } from "../src/runtime.js";
import type { NativeAgentRuntimeOwner } from "../src/agent-native-runtime.js";
import { SyntheticNativeHttp } from "./native-runtime-fixtures.js";

const shared = JSON.parse(readFileSync(new URL("../contracts/fixtures/sampled-current-carry-v1.json", import.meta.url), "utf8"));
const roots: string[] = [], owners: NativeAgentRuntimeOwner[] = [];
const child = fileURLToPath(new URL("./fixtures/sampled-agent-child.mjs", import.meta.url));
const sha = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");

// Uses SDK wire decoding/assembly with an in-memory scripted transport. No
// HTTP listener, fetch, real backend, game or numerical model is executed.
async function fixture(scenario = "normal", mode: "auto" | "one_step" | "shadow" | "human" = "auto") {
  const root = await mkdtemp(join(tmpdir(), "sampled-carry-")); roots.push(root);
  const source = new SyntheticNativeHttp(1); // route() only; never start().
  const manifest = structuredClone(shared.manifest) as AgentManifest;
  const calls: string[] = [];
  const environment = {
    registerClient: async (input: { clientInstanceId: string }) => ({ raw: {}, data: {
      runtime_instance_id: source.capture.session.runtime_instance_id,
      client: { client_session_id: "client-fixture", client_instance_id: input.clientInstanceId }, controller: null } }),
    acquireController: async () => ({ raw: {}, data: {
      runtime_instance_id: source.capture.session.runtime_instance_id, status: "controller_acquired",
      controller: { controller_lease_id: "lease-fixture", controller_generation: 1, client_session_id: "client-fixture", expires_at: new Date(Date.now() + 120000).toISOString() } } }),
    releaseController: async () => ({ raw: {}, data: { runtime_instance_id: source.capture.session.runtime_instance_id,
      status: "controller_released", controller: null } }),
    nativeLogicalRequest: async (operation: NativeLogicalTransportOperation, body: JsonObject = {}, options: NativeLogicalTransportOptions = {}) => {
      options.signal?.throwIfAborted(); calls.push(operation);
      const path = operation === "submit" ? "actions" : operation;
      const response = source.route(new URL("http://never-contacted.invalid/" + path), body);
      return { raw: structuredClone(response.value) as JsonObject, encodedByteCount: Buffer.byteLength(JSON.stringify(response.value)), statusCode: response.status ?? 200 };
    }
  } as unknown as PlayerEnvironmentRestClient;
  const evidence = await AgentRunEvidence.createSession({ root, agentManifest: manifest, mode, runtimeVersion: "fixture", runtimeCodeSha256: "1".repeat(64) });
  const manifestPath = join(root, "agent.json"); await writeFile(manifestPath, JSON.stringify(manifest));
  const port = NdjsonAgentSessionPort.spawn(process.execPath, [child, manifestPath, scenario], manifest.adapter, manifest.limits);
  const runtime = await PolicyRuntime.forAgent({ manifest, environment, port, evidence,
    runtimeIdentity: { version: "fixture", code_sha256: "1".repeat(64) }, mode,
    autoBudget: { maxSubmissions: 400, maxPolicyCalls: 1000, deadlineMs: 60000 } });
  owners.push(runtime);
  const setFrame = (name: string) => {
    const frame = structuredClone(shared.frames[name]);
    source.observation = frame.observation; source.capture = frame.capture;
    source.actions.splice(0, source.actions.length, ...frame.catalog);
    source.rehash();
  };
  setFrame("map_a");
  return { root, source, evidence, port, runtime, calls, setFrame };
}
async function events(evidence: AgentRunEvidence) { return (await readFile(join(evidence.directory, "events.jsonl"), "utf8")).trim().split("\n").map(value => JSON.parse(value)); }
function verify(directory: string): string {
  return execFileSync(process.env.STS2_EVIDENCE_PYTHON ?? "python3", ["-c",
    "import sys;from pathlib import Path;from sts2_platform_evidence import verify_agent_session_run_evidence;r=verify_agent_session_run_evidence(Path(sys.argv[1]));print(r.status);print(r.findings);sys.exit(0 if r.status=='pass' else 1)", directory],
    { encoding: "utf8", env: { ...process.env, PYTHONPATH: fileURLToPath(new URL("../../evidence", import.meta.url)) } });
}
afterEach(async () => { vi.restoreAllMocks(); for (const owner of owners.splice(0)) await owner.stop().catch(() => undefined); for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true }); });

describe("sampled current carry, source/contract only", () => {
  it("freezes coherent sample quotas and rejects invalid closed mode combinations", () => {
    expect(shared.sample_storage_limits).toEqual(AGENT_SAMPLE_STORAGE_LIMITS);
    expect(validateAgentManifest(shared.manifest).input.history_mode).toBe("sampled_current");
    for (const mutation of [(m: AgentManifest) => m.input.attachment.eager_scope.push("persistent"),
      (m: AgentManifest) => m.requirements.required_methods = m.requirements.required_methods.filter(x => x !== "catalog"),
      (m: AgentManifest) => m.input.consumption_mode = "incremental_view",
      (m: AgentManifest) => m.input.state_recovery = { mode: "opaque", max_state_bytes: 1, model_bindings: [] }]) {
      const manifest = structuredClone(shared.manifest); mutation(manifest); expect(() => validateAgentManifest(manifest)).toThrow();
    }
  });
  it("accepts exactly three null-publication Map/Inspect/Map advances and preserves old full-reference rejection", () => {
    const ledger = new AgentConsumptionLedger(shared.manifest, "segment-1");
    for (const chain of shared.duplex_chain) {
      const f = shared.frames[chain.frame]; const report = chain.messages.find((m: { message_type: string }) => m.message_type === "consumed").completion;
      ledger.register({ acquisition_id: report.acquisition_id, capture: f.capture, observation: f.observation,
        catalog: f.catalog, catalog_materialized: true, publication_index: null });
      expect(ledger.accept(report).state_version).toBe(report.state_version);
    }
    const old = structuredClone(shared.manifest); old.input.history_mode = "full_reference"; old.input.attachment.delivery_mode = "full_reference";
    old.input.attachment.eager_scope = ["persistent", "interaction", "referents", "catalog"];
    const full = new AgentConsumptionLedger(old, "segment-1"), f = shared.frames.map_a;
    full.register({ acquisition_id: "acq-map_a", capture: f.capture, observation: f.observation, catalog: f.catalog, catalog_materialized: true, publication_index: null });
    expect(() => full.accept(shared.duplex_chain[0].messages[3].completion)).toThrow("full_reference_gap");
    ledger.close(); full.close();
  });
  it("uses full Current/C, persists original bytes before ACK, cleans 300 unchanged acquisitions, and independently verifies", async () => {
    const f = await fixture();
    expect((await f.runtime.tick()).type).toBe("delivered");
    expect(f.runtime.status().session.state_version).toBe(1);
    for (let i = 0; i < 300; i++) { const result = await f.runtime.tick(); expect(result.type, JSON.stringify({ i, status: f.runtime.status() })).toBe("awaited"); }
    expect(f.runtime.status().session.state_version).toBe(1);
    f.setFrame("inspect_b"); expect((await f.runtime.tick()).type).toBe("delivered");
    f.setFrame("map_c"); expect((await f.runtime.tick()).type).toBe("delivered");
    expect(f.runtime.status().session.state_version).toBe(3);
    await f.runtime.setMode("human");
    await expect(f.runtime.setMode("auto")).rejects.toThrow("runtime_sample_segment_ended");
    await f.runtime.stop();
    const recorded = await events(f.evidence);
    for (const event of recorded.filter(e => e.kind.startsWith("agent_sample_")))
      expect(Object.keys(event.payload).sort()).toEqual(shared.evidence.event_fields[event.kind]);
    const samples = recorded.filter(e => e.kind === "agent_sample_input_stored");
    expect(samples).toHaveLength(3); expect(recorded.filter(e => e.kind === "agent_sample_query_discarded")).toHaveLength(300);
    expect(recorded.filter(e => e.kind === "agent_sample_consume_ack_offered")).toHaveLength(3);
    const sample = samples[0].payload.metadata;
    expect(sha(await readFile(join(f.evidence.directory, sample.observation.path)))).toBe(sample.capture.sha256);
    expect(f.calls.filter(x => x === "catalog")).toHaveLength(303);
    expect(f.calls.filter(x => x === "submit")).toHaveLength(3);
    expect(verify(f.evidence.directory)).toContain("pass");
  }, 30000);
  it("does not sample complete empty-C wait, then ACKs actual ready summary before Close", async () => {
    const f = await fixture(); f.setFrame("empty_wait");
    expect((await f.runtime.tick()).type).toBe("awaited"); expect(f.runtime.status().session.state_version).toBe(0);
    f.setFrame("ready_summary"); expect((await f.runtime.tick()).type).toBe("closed");
    expect(f.runtime.status().session.state_version).toBe(1); expect(verify(f.evidence.directory)).toContain("pass");
  });
  it("initial Human permits first Auto, while OneStep ends its accepted segment", async () => {
    const f = await fixture("normal", "human"); await f.runtime.setMode("human"); await f.runtime.setMode("one_step");
    expect((await f.runtime.tick()).type).toBe("delivered");
    await expect(f.runtime.setMode("auto")).rejects.toThrow("runtime_sample_segment_ended");
    await f.runtime.stop(); expect(verify(f.evidence.directory)).toContain("pass");
  });
  it("disk failure before ACK never publishes ACK or state advance and keeps an uncertain child fenced", async () => {
    const f = await fixture(); vi.spyOn(f.evidence, "storeSampleAcquisition").mockRejectedValue(new Error("injected disk failure"));
    const result = await f.runtime.tick().catch(error => ({ type: "cleanup_failed", error }));
    expect(["unknown", "cleanup_failed"]).toContain(result.type);
    expect(f.runtime.status().session.state_version).toBe(0);
    expect((await events(f.evidence)).filter(e => e.kind === "agent_sample_consume_ack_offered")).toHaveLength(0);
    await expect(f.runtime.setMode("auto")).rejects.toThrow();
  });
  it.each(["unknown", "pending"] as const)("unknown/pending %s preserves original request and forbids reset/reentry", async behavior => {
    const f = await fixture(); f.source.behavior = behavior;
    expect((await f.runtime.tick()).type).toBe("unknown");
    await f.runtime.setMode("human"); await expect(f.runtime.setMode("auto")).rejects.toThrow();
    await f.runtime.stop(); expect(verify(f.evidence.directory)).toContain("pass");
  });
  it("rejects partial Current rather than synthesizing an empty action catalog", async () => {
    const f = await fixture();
    f.source.observation.completeness = { status: "partial", included: ["persistent", "interaction", "catalog"], missing: ["referents"], full_reference_complete: false };
    f.source.rehash();
    await f.runtime.tick();
    expect(f.runtime.status().session.state_version).toBe(0);
    expect((await events(f.evidence)).some(e => e.kind === "agent_consumed")).toBe(false);
    await expect(f.runtime.setMode("auto")).rejects.toThrow();
  });
  it("known stale/not-started delivery ends this segment without uncertainty or automatic retry", async () => {
    const f = await fixture(); const original = f.source.route.bind(f.source);
    vi.spyOn(f.source, "route").mockImplementation((url, body) => {
      const response = original(url, body);
      if (url.pathname.endsWith("actions")) Object.assign(response.value as object,
        { delivery: "rejected_before_input", execution: "not_started", effect: "not_observed", reason: "stale_snapshot" });
      return response;
    });
    expect((await f.runtime.tick()).type).toBe("not_delivered");
    expect(f.runtime.status().session.agent_state).toBe("known");
    expect(f.calls.filter(x => x === "submit")).toHaveLength(1);
    await expect(f.runtime.setMode("auto")).rejects.toThrow("runtime_sample_segment_ended");
    await f.runtime.stop(); expect(verify(f.evidence.directory)).toContain("pass");
  });
  it("reserves pending sample count before queued disk copies and rejects the ninth", async () => {
    const f = await fixture(), frame = shared.frames.map_a, raw = Buffer.from(frame.observation_utf8);
    const results = await Promise.allSettled(Array.from({ length: 9 }, (_, i) => f.evidence.storeSampleAcquisition({
      acquisition_id: "pending-" + i, input_spec: shared.input_spec, continuity_token: "segment", capture: frame.capture,
      observation: raw, catalog: frame.catalog, catalog_digest: frame.observation.catalog.digest })));
    expect(results.filter(r => r.status === "fulfilled")).toHaveLength(8);
    expect(results.filter(r => r.status === "rejected")).toHaveLength(1);
    expect((await readdir(f.evidence.directory)).filter(x => x.startsWith("agent-sample-"))).toHaveLength(24);
  });
  it("stores 300 representative 64 KiB original samples beyond opaque file limits without truncation", async () => {
    const root = await mkdtemp(join(tmpdir(), "sample-capacity-")); roots.push(root);
    const manifest = structuredClone(shared.manifest) as AgentManifest;
    const evidence = await AgentRunEvidence.createSession({ root, agentManifest: manifest, mode: "shadow", runtimeVersion: "fixture", runtimeCodeSha256: "1".repeat(64) });
    let bytes = 0;
    for (let i = 0; i < 300; i++) {
      const frame = structuredClone(shared.frames.map_a);
      frame.observation.persistent.content = { text: "x".repeat(64 * 1024) };
      const raw = Buffer.from(JSON.stringify(frame.observation));
      frame.capture.byte_count = raw.length; frame.capture.sha256 = sha(raw);
      const record = await evidence.storeSampleAcquisition({ acquisition_id: "capacity-" + i,
        input_spec: manifest.input.input_spec, continuity_token: "capacity-segment", capture: frame.capture,
        observation: raw, catalog: frame.catalog, catalog_digest: frame.observation.catalog.digest });
      expect(record.metadata.observation.bytes).toBe(raw.length); bytes += raw.length;
    }
    expect((await readdir(evidence.directory)).filter(x => x.startsWith("agent-sample-"))).toHaveLength(900);
    expect(bytes).toBeGreaterThan(16 * 1024 * 1024); // local storage is a separate compatibility boundary.
    expect(bytes).toBeLessThan(AGENT_SAMPLE_STORAGE_LIMITS.max_total_bytes);
  }, 30000);
  it("refuses to seal a partial sample disk write, retaining its original observation bytes", async () => {
    const f = await fixture(); const frame = structuredClone(shared.frames.map_a), raw = Buffer.from(frame.observation_utf8);
    const binding = { acquisition_id: "disk-failure", input_spec: shared.input_spec,
      continuity_token: "segment", publication_index: null, capture: frame.capture };
    // Determine the same canonical address without performing a successful first write.
    const { canonicalJson } = await import("../src/evidence.js");
    const address = "agent-sample-" + sha(Buffer.from(canonicalJson(binding)));
    await writeFile(join(f.evidence.directory, address + ".catalog.json"), "injected occupied destination");
    await expect(f.evidence.storeSampleAcquisition({ acquisition_id: "disk-failure", input_spec: shared.input_spec,
      continuity_token: "segment", capture: frame.capture, observation: raw, catalog: frame.catalog,
      catalog_digest: frame.observation.catalog.digest })).rejects.toThrow();
    expect(await readFile(join(f.evidence.directory, address + ".observation.json"))).toEqual(raw);
    await expect(f.evidence.finalize({ status: "tainted", tainted: true, mode: "human" })).rejects.toThrow("sample storage incomplete");
  });
  it("cancelled offered Current preserves unacknowledged original payload and does not call it consumed", async () => {
    const f = await fixture("hang_after_query"); const tick = f.runtime.tick();
    await vi.waitFor(async () => expect((await events(f.evidence)).some(e => e.kind === "agent_sample_query_offered")).toBe(true));
    await f.runtime.setMode("human"); await tick;
    expect(f.runtime.status().session.state_version).toBe(0);
    await f.runtime.stop();
    const record = await events(f.evidence); expect(record.filter(e => e.kind === "agent_sample_input_stored")).toHaveLength(1);
    expect(record.find(e => e.kind === "agent_sample_input_stored").payload.disposition).toBe("query_offered");
    expect(record.filter(e => e.kind === "agent_consumed")).toHaveLength(0);
    expect(verify(f.evidence.directory)).toContain("pass");
  });
});
