import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { digestNativeLogicalActions } from "@rsgcsg/sts2-connector-client";
import type { JsonObject, NativeLogicalTransportOperation, NativeLogicalTransportOptions, PlayerEnvironmentRestClient } from "@rsgcsg/sts2-connector-client";
import { AgentRunEvidence } from "../src/evidence.js";
import { PolicyRuntime } from "../src/runtime.js";
import { NdjsonAgentSessionPort } from "../src/agent-session-port.js";
import type { AgentManifest } from "../src/agent-session-contracts.js";
import type { NativeAgentRuntimeOwner } from "../src/agent-native-runtime.js";
import { SyntheticNativeHttp } from "./native-runtime-fixtures.js";

const shared = JSON.parse(readFileSync(new URL("../contracts/fixtures/sampled-current-carry-v1.json", import.meta.url), "utf8"));
const policy = JSON.parse(readFileSync(new URL("../contracts/fixtures/owned-current-known-stale-v1.json", import.meta.url), "utf8")).execution_policy;
const child = fileURLToPath(new URL("./fixtures/sampled-agent-child.mjs", import.meta.url));
const cleanup: (() => Promise<void>)[] = [];
function gate() { let release!: () => void; const promise = new Promise<void>(resolve => { release = resolve; }); return { promise, release }; }

// Real SDK assembly/dispatch and OS Ndjson child, with explicitly scripted
// source/authority responses. This does not replace the real C# Store pressure gate.
async function fixture(options: { legacy?: boolean; maxSubmissions?: number; scenario?: string; failRenewal?: boolean; teacher?: boolean } = {}) {
  const root = await mkdtemp(join(tmpdir(), "owned-stale-runtime-")), source = new SyntheticNativeHttp(1, "interactive", options.failRenewal ? 1000 : 120000);
  const manifest = structuredClone(shared.manifest) as AgentManifest;
  if (!options.legacy) { manifest.execution_policy = structuredClone(policy); manifest.requirements.required_methods.push("current_owned"); }
  let teacherPython: string | undefined;
  if (options.teacher) {
    const candidates = process.env.E6_OWNED_STALE_TEACHER_PYTHON ? [process.env.E6_OWNED_STALE_TEACHER_PYTHON]
      : process.env.PYTHON ? [process.env.PYTHON] : ["python", "python3"];
    for (const candidate of candidates) {
      try { execFileSync(candidate, ["-c", "import sys;assert sys.version_info >= (3,11)"], { stdio: "pipe", timeout: 5000 }); teacherPython = candidate; break; } catch { /* Next existing interpreter only; never install. */ }
    }
    if (!teacherPython) throw new Error("existing Python3.11 source-test interpreter required");
    const code = "import json,sys\nfrom pathlib import Path\nfrom stpd.policy.native_teacher_agent import descriptor,write_artifact\np=json.loads(sys.argv[2]);d=descriptor(execution_policy=p);a=write_artifact(Path(sys.argv[1]),execution_policy=p)\nassert 'torch' not in sys.modules\nprint(json.dumps(dict(artifact=a,adapter=d['adapter'],version=d['agent_spec']['version'],input_spec=d['input_spec'])))";
    const descriptor = JSON.parse(execFileSync(teacherPython, ["-c", code, join(root, "private-teacher-code.json"), JSON.stringify(policy)],
      { encoding: "utf8", timeout: 10000, env: { ...process.env, PYTHONPATH: fileURLToPath(new URL("../../../python", import.meta.url)) } }));
    manifest.adapter = descriptor.adapter; manifest.artifact = descriptor.artifact; manifest.input.input_spec = descriptor.input_spec;
    manifest.agent = { id: "stpd-native-public-program-teacher", version: descriptor.version, provider: "programmed_teacher", architecture: "finite_public_program" };
    manifest.support.interaction_kinds = ["*"]; manifest.support.action_verbs = ["*"];
  }
  source.capabilities.supported_methods.push("current_owned"); source.capabilities.implemented_mechanisms.push("native_current_reader_owned_v1");
  const calls: { operation: string; body: JsonObject }[] = [], releases: string[] = [], handles = new Set<string>();
  let serial = 0, clock = 0, behavior: "stale" | "delivered" | "unknown" | "partial" | "pending" = "stale";
  let submitGate: ReturnType<typeof gate> | undefined;
  let onSubmit: (() => void) | undefined, failRelease: ((id: string) => boolean) | undefined;
  const applyFrame = (name: string) => {
    const frame = structuredClone(shared.frames[name]); source.observation = frame.observation; source.capture = frame.capture;
    source.actions.splice(0, source.actions.length, ...frame.catalog);
    if (options.teacher) {
      source.observation.interaction!.kind = "native_map";
      (source.observation.interaction!.content.surface as { kind: string }).kind = "map_navigation";
      source.actions[0]!.verb = "open_run_deck";
      source.observation.catalog.digest = digestNativeLogicalActions(source.actions);
    }
    source.rehash();
  };
  applyFrame("map_a");
  const environment = {
    registerClient: async (input: { clientInstanceId: string }) => ({ raw: {}, data: {
      runtime_instance_id: source.capture.session.runtime_instance_id,
      client: { client_session_id: "client-fixture", client_instance_id: input.clientInstanceId }, controller: null } }),
    acquireController: async () => ({ raw: {}, data: { runtime_instance_id: source.capture.session.runtime_instance_id, status: "controller_acquired",
      controller: { controller_lease_id: "lease-fixture", controller_generation: 1, client_session_id: "client-fixture", expires_at: new Date(Date.now() + 120000).toISOString() } } }),
    releaseController: async () => ({ raw: {}, data: { runtime_instance_id: source.capture.session.runtime_instance_id, status: "controller_released", controller: null } }),
    nativeLogicalRequest: async (operation: NativeLogicalTransportOperation, body: JsonObject = {}, transportOptions: NativeLogicalTransportOptions = {}) => {
      transportOptions.signal?.throwIfAborted(); calls.push({ operation, body: structuredClone(body) });
      if (operation === "submit") { onSubmit?.(); await submitGate?.promise; }
      let response;
      if (operation === "current_owned") {
        source.capture.capture_id = "owned-capture-" + ++serial;
        response = source.route(new URL("http://never-contacted.invalid/current"), body);
        const value = response.value as Record<string, unknown>, id = "reader-" + serial; handles.add(id);
        value.retention = { retention_handle_id: id, capture: structuredClone(source.capture), read_cursor: source.capture.read_cursor, expires_at: source.capture.expires_at };
      } else if (operation === "release") {
        const id = String(body.retention_handle_id); releases.push(id);
        if (failRelease?.(id)) throw new Error("injected original reader release failure");
        handles.delete(id); response = source.route(new URL("http://never-contacted.invalid/release"), body);
      } else if (operation === "submit") {
        if (behavior === "pending") return { raw: { error: { code: "request_pending", detail: "original pending" } }, encodedByteCount: 0, statusCode: 202 };
        response = source.route(new URL("http://never-contacted.invalid/actions"), body);
        const value = response.value as Record<string, unknown>;
        value.attribution = { runtime_instance_id: source.capture.session.runtime_instance_id, client_session_id: body.client_session_id,
          controller_lease_id: body.controller_lease_id, controller_generation: body.controller_generation,
          client_instance_id: "fixture", product_id: "test", product_name: "Test", product_version: "1" };
        if (behavior === "stale") Object.assign(value, { delivery: "not_started", execution: "not_started", effect: "not_observed", action: null, stages: [], reason: "stale_snapshot_or_binding", retry: "never_automatic" });
        if (behavior === "unknown" || behavior === "partial") Object.assign(value, { delivery: behavior === "partial" ? "partially_delivered" : "unknown", execution: "unknown", effect: "unknown" });
      } else if (operation === "renew" && options.failRenewal) response = { value: { schema: "sts2.player-environment/native-logical-renew-1", input_profile: "native-logical-v1", status: "subscription_expired", subscription: null, next_cursor: null, high_watermark: null, retained_start_cursor: null, gap: null, reason: "subscription_expired" } };
      else response = source.route(new URL("http://never-contacted.invalid/" + operation), body);
      if (operation === "attach" && options.failRenewal) (response.value as { subscription: { expires_at: string } }).subscription.expires_at = new Date(Date.now() + 1000).toISOString();
      return { raw: structuredClone(response.value) as JsonObject, encodedByteCount: Buffer.byteLength(JSON.stringify(response.value)), statusCode: response.status ?? 200 };
    }
  } as unknown as PlayerEnvironmentRestClient;
  const evidence = await AgentRunEvidence.createSession({ root, agentManifest: manifest, mode: "auto", runtimeVersion: "fixture", runtimeCodeSha256: "1".repeat(64) });
  const manifestPath = join(root, "manifest.json"); await writeFile(manifestPath, JSON.stringify(manifest));
  const port = teacherPython ? NdjsonAgentSessionPort.spawn(teacherPython, ["-m", "stpd.policy.native_teacher_agent", "--manifest", manifestPath], manifest.adapter, manifest.limits,
    { executionPolicy: manifest.execution_policy, env: { ...process.env, PYTHONPATH: fileURLToPath(new URL("../../../python", import.meta.url)) } })
    : NdjsonAgentSessionPort.spawn(process.execPath, [child, manifestPath, options.scenario ?? "normal"], manifest.adapter, manifest.limits, { executionPolicy: manifest.execution_policy });
  const inputs: unknown[] = [], next = port.next.bind(port);
  port.next = (...args) => { inputs.push(structuredClone(args[1])); return next(...args); };
  let runtime: NativeAgentRuntimeOwner;
  try { runtime = await PolicyRuntime.forAgent({ manifest, environment, port, evidence, mode: "auto", monotonicNow: () => clock,
    runtimeIdentity: { version: "fixture", code_sha256: "1".repeat(64) }, autoBudget: { maxSubmissions: options.maxSubmissions ?? 200, maxPolicyCalls: 1000, deadlineMs: 60000 } }); }
  catch (error) { const diagnostics = Buffer.concat((port as unknown as { stderrChunks: Buffer[] }).stderrChunks).toString(); port.close(); await rm(root, { recursive: true, force: true }); throw new Error(String(error) + "\n" + diagnostics, { cause: error }); }
  cleanup.push(async () => { await runtime.stop().catch(() => undefined); port.close(); await rm(root, { recursive: true, force: true }); });
  const events = async () => (await readFile(join(evidence.directory, "events.jsonl"), "utf8")).trim().split("\n").filter(Boolean).map(line => JSON.parse(line));
  const advance = (i: number) => {
    applyFrame(i % 2 ? "inspect_b" : "map_c");
    source.observation.revision = i + 1; source.observation.snapshot_id = "advanced-" + i;
    source.observation.owner_occurrence.occurrence_id = "occurrence-" + i;
    source.capture.snapshot_id = source.observation.snapshot_id; source.observation.catalog.snapshot_id = source.observation.snapshot_id; source.rehash();
  };
  return { runtime, port, evidence, source, calls, handles, releases, inputs, events, applyFrame, advance,
    behavior: (value: typeof behavior) => { behavior = value; }, holdSubmit: () => { submitGate = gate(); return submitGate; },
    onSubmit: (fn: () => void) => { onSubmit = fn; }, failRelease: (fn: (id: string) => boolean) => { failRelease = fn; },
    expire: () => { clock = 60000; } };
}
afterEach(async () => { vi.restoreAllMocks(); for (const close of cleanup.splice(0)) await close(); });

describe("owned Current stale continuation through actual SDK and Ndjson process", () => {
  it("composes the actual OS Python Teacher with a private source descriptor, without Torch or final producer promotion", async () => {
    const f = await fixture({ teacher: true });
    expect(f.port.executionPolicy).toEqual(policy); expect((await f.runtime.tick()).type).toBe("fresh_decision_required");
    expect((await f.runtime.tick()).type).toBe("awaited"); expect(f.runtime.status().session.state_version).toBe(1);
    f.applyFrame("inspect_b"); f.behavior("delivered"); expect((await f.runtime.tick()).type).toBe("delivered");
    expect(f.runtime.status()).toMatchObject({ agent: { agent_id: "stpd-native-public-program-teacher", agent_version: "1.2.0", adapter: { version: "1.2.0" } }, session: { state_version: 2, agent_state: "known" } });
    const submits = f.calls.filter(c => c.operation === "submit"); expect(submits).toHaveLength(2);
    expect(submits[1]!.body.bound_action_id).toBe("inspect_b-action"); expect(submits[1]!.body.request_id).not.toBe(submits[0]!.body.request_id);
    const e = await f.events(); expect(e.filter(x => x.kind === "agent_consumed")).toHaveLength(2); expect(e.filter(x => x.kind === "native_stale_decision_deferred")).toHaveLength(1);
    await f.runtime.stop(); expect(f.handles.size).toBe(0);
  });
  it("keeps W/prefix/child while unchanged Current only waits, then ACKs a changed unit and chooses a new request", async () => {
    const f = await fixture();
    const first = await f.runtime.tick(); expect(first.type, JSON.stringify(first)).toBe("fresh_decision_required");
    const initial = f.runtime.status().session;
    for (let i = 0; i < 20; i++) expect((await f.runtime.tick()).type).toBe("awaited");
    expect(f.runtime.status().session).toEqual(initial); expect(f.handles.size).toBe(1);
    expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(1);
    const outcome = (f.inputs[1] as Record<string, unknown>).operational_outcome;
    expect(outcome).toMatchObject({ state_version: 1, result: { request_id: first.type === "fresh_decision_required" ? first.original_request_id : "", action: null, stages: [], retry: "never_automatic" } });
    f.applyFrame("inspect_b"); f.behavior("delivered"); expect((await f.runtime.tick()).type).toBe("delivered");
    const second = f.runtime.status().session; expect(second.session_id).toBe(initial.session_id); expect(second.continuity_token).toBe(initial.continuity_token); expect(second.state_version).toBe(2);
    const submits = f.calls.filter(c => c.operation === "submit"); expect(submits[1]!.body.request_id).not.toBe(submits[0]!.body.request_id);
    expect(submits[1]!.body.expected_snapshot_id).not.toBe(submits[0]!.body.expected_snapshot_id); expect(f.handles.size).toBe(1);
    f.applyFrame("map_c"); expect((await f.runtime.tick()).type).toBe("delivered"); expect((f.inputs.at(-1) as Record<string, unknown>).operational_outcome).toBeNull();
    await f.runtime.stop(); expect(f.handles.size).toBe(0);
    const events = await f.events(); expect(events.filter(e => e.kind === "agent_consumed")).toHaveLength(3); expect(events.filter(e => e.kind === "native_stale_decision_deferred")).toHaveLength(1);
    const intents = events.filter(e => e.kind === "native_submission_requested"); expect(intents).toHaveLength(3); expect(intents.every(e => Object.keys(e.payload.dispatch_binding).length === 4)).toBe(true);
  });
  it("ends exactly at the third consecutive refusal and records the threshold original result", async () => {
    const f = await fixture();
    expect((await f.runtime.tick()).type).toBe("fresh_decision_required"); f.advance(1); expect((await f.runtime.tick()).type).toBe("fresh_decision_required"); f.advance(2);
    expect(await f.runtime.tick()).toMatchObject({ type: "not_delivered", reason: "known_stale_streak_limit" });
    const e = await f.events(); expect(e.filter(x => x.kind === "native_result")).toHaveLength(3); expect(e.filter(x => x.kind === "native_stale_decision_deferred").map(x => x.payload.consecutive_known_stale_rejections)).toEqual([1, 2]);
    expect(f.runtime.status()).toMatchObject({ mode: "human", tainted: false, session: { state_version: 3, agent_state: "known" } });
    await expect(f.runtime.setMode("auto")).rejects.toThrow("runtime_sample_segment_ended");
  });
  it("delivered resets only the streak; total eight still stops in the same accepted prefix", async () => {
    const f = await fixture(); let index = 0;
    for (let i = 1; i <= 8; i++) {
      if (index) f.advance(index); f.behavior("stale"); const tick = await f.runtime.tick();
      expect(tick).toMatchObject(i === 8 ? { type: "not_delivered", reason: "known_stale_rejection_limit" } : { type: "fresh_decision_required" }); index++;
      if (i < 8) { f.advance(index++); f.behavior("delivered"); expect((await f.runtime.tick()).type).toBe("delivered"); }
    }
    const e = await f.events(), d = e.filter(x => x.kind === "native_stale_decision_deferred");
    expect(d.map(x => x.payload.known_stale_rejections)).toEqual([1, 2, 3, 4, 5, 6, 7]); expect(d.every(x => x.payload.consecutive_known_stale_rejections === 1)).toBe(true);
    expect(e.filter(x => x.kind === "native_result")).toHaveLength(15); expect(f.runtime.status().autonomy_budget.submissions_used).toBe(15);
  });
  it("default legacy ends after one known refusal and keeps the five-field Next and old intent shape", async () => {
    const f = await fixture({ legacy: true }); expect((await f.runtime.tick()).type).toBe("not_delivered");
    expect(Object.keys(f.inputs[0] as object)).toHaveLength(5); expect(f.calls.some(c => c.operation === "current_owned")).toBe(false);
    const e = await f.events(); expect(e.filter(x => x.kind === "native_stale_decision_deferred")).toHaveLength(0); expect(e.find(x => x.kind === "native_submission_requested").payload.dispatch_binding).toBeUndefined();
  });
  it.each(["unknown", "partial", "pending"] as const)("keeps %s fenced without fresh-decision continuation", async behavior => {
    const f = await fixture(); f.behavior(behavior); expect((await f.runtime.tick()).type).toBe("unknown");
    expect((await f.events()).filter(x => x.kind === "native_stale_decision_deferred")).toHaveLength(0);
    await expect(f.runtime.setMode("auto")).rejects.toThrow(); expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(1);
  });
  it.each(["revision", "coherence", "regression"] as const)("rejects a refused-unit %s drift before another Consume or dispatch", async mutation => {
    const f = await fixture(); expect((await f.runtime.tick()).type).toBe("fresh_decision_required");
    if (mutation === "revision") f.source.observation.revision++;
    if (mutation === "regression") f.source.observation.revision--;
    if (mutation === "coherence") f.source.observation.persistent!.content = { replaced: true };
    f.source.rehash(); await f.runtime.tick();
    expect(f.runtime.status().session.state_version).toBe(1); expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(1); expect(f.runtime.status().mode).toBe("human"); expect(f.handles.size).toBe(1);
  });
  it("budget exhaustion wins before another fresh decision while still recording the actual refusal", async () => {
    const f = await fixture({ maxSubmissions: 1 }); expect(await f.runtime.tick()).toMatchObject({ type: "not_delivered", reason: "autonomy_budget_exhausted" });
    expect((await f.events()).filter(x => x.kind === "native_result")).toHaveLength(1); expect((await f.events()).filter(x => x.kind === "native_stale_decision_deferred")).toHaveLength(0); expect(f.runtime.status().autonomy_budget.submissions_used).toBe(1);
  });
  it.each(["human", "stop", "deadline"] as const)("preserves the already-winning %s reason when the original POST completes stale", async winner => {
    const f = await fixture(), held = f.holdSubmit(); let entered!: () => void; const entering = new Promise<void>(resolve => { entered = resolve; }); f.onSubmit(entered);
    const tick = f.runtime.tick(); await entering;
    const shutdown = winner === "human" ? f.runtime.setMode("human") : winner === "stop" ? f.runtime.stop() : Promise.resolve(f.expire());
    held.release(); const result = await tick; await shutdown;
    expect(result).toMatchObject({ type: "not_delivered", reason: winner === "human" ? "human_recovery" : winner === "stop" ? "stopped" : "autonomy_budget_exhausted" });
    const e = await f.events(); expect(e.filter(x => x.kind === "native_result")).toHaveLength(1); expect(e.filter(x => x.kind === "native_stale_decision_deferred")).toHaveLength(0);
  });
  it("preserves the first passive source-loss reason when renewal fails during a started stale submission", async () => {
    const f = await fixture({ failRenewal: true }), held = f.holdSubmit(); let entered!: () => void;
    const entering = new Promise<void>(resolve => { entered = resolve; }); f.onSubmit(entered);
    const tick = f.runtime.tick(); await entering;
    try { await vi.waitFor(() => expect(f.runtime.status().mode, JSON.stringify(f.calls.map(c => c.operation))).toBe("human"), { timeout: 2500 }); } finally { held.release(); }
    held.release(); expect(await tick).toMatchObject({ type: "not_delivered", reason: "native_subscription_unavailable" });
    await f.runtime.stop(); const e = await f.events();
    expect(e.filter(x => x.kind === "native_result")).toHaveLength(1); expect(e.filter(x => x.kind === "native_stale_decision_deferred")).toHaveLength(0);
    expect(e.find(x => x.kind === "agent_sample_segment_ended").payload.reason).toBe("native_subscription_unavailable");
  });
  it("durable-intent write failure never starts POST or charges a submission and still frees the reader", async () => {
    const f = await fixture(), append = f.evidence.append.bind(f.evidence);
    vi.spyOn(f.evidence, "append").mockImplementation((kind, payload, now) => kind === "native_submission_requested" ? Promise.reject(new Error("intent disk failure")) : append(kind, payload, now));
    await f.runtime.tick(); expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(0); expect(f.runtime.status().autonomy_budget.submissions_used).toBe(0);
    expect((await f.events()).filter(x => x.kind === "native_submission_not_started")).toHaveLength(0); await f.runtime.stop(); expect(f.handles.size).toBe(0);
  });
  it("Stop attempts every lease after the first original release and detach/evidence failures", async () => {
    const f = await fixture(); expect((await f.runtime.tick()).type).toBe("fresh_decision_required");
    const originalResult = structuredClone(f.runtime.status().last_result);
    f.failRelease(() => true); await expect(f.runtime.tick()).rejects.toThrow("owned-reader cleanup failed");
    // Immediate fail-closed state is checked before explicit outer compensation.
    expect(f.runtime.status()).toMatchObject({ mode: "human", controller: "released", tainted: true, session: { agent_state: "known" } });
    expect(f.runtime.status().last_result).toEqual(originalResult);
    const before = [...f.handles]; expect(before.length).toBeGreaterThan(0);
    vi.spyOn(f.evidence, "finalize").mockRejectedValue(new Error("finalize disk failure"));
    await expect(f.runtime.stop()).rejects.toThrow();
    expect(before.every(id => f.releases.includes(id))).toBe(true); expect(f.runtime.status()).toMatchObject({ lifecycle: "stopped", tainted: true, controller: "released" }); expect(f.port.byteBudget.used).toBe(0);
  });
});
