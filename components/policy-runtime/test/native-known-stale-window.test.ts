import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { digestNativeLogicalActions } from "@rsgcsg/sts2-connector-client";
import type { JsonObject, NativeLogicalTransportOperation, NativeLogicalTransportOptions, PlayerEnvironmentRestClient } from "@rsgcsg/sts2-connector-client";
import { AgentRunEvidence, canonicalJson } from "../src/evidence.js";
import { PolicyRuntime } from "../src/runtime.js";
import { NdjsonAgentSessionPort } from "../src/agent-session-port.js";
import { validateAgentExecutionPolicy, validateAgentManifest, type AgentManifest } from "../src/agent-session-contracts.js";
import type { NativeAgentRuntimeOwner } from "../src/agent-native-runtime.js";
import { SyntheticNativeHttp } from "./native-runtime-fixtures.js";
import { fixturePortCloser } from "./fixture-process.js";
import { fixtureProgress } from "./fixture-progress.js";

const shared = JSON.parse(readFileSync(new URL("../contracts/fixtures/sampled-current-carry-v1.json", import.meta.url), "utf8"));
const policy = JSON.parse(readFileSync(new URL("../contracts/fixtures/owned-current-known-stale-v1.json", import.meta.url), "utf8")).execution_policy;
const knownWindowPolicy = JSON.parse(readFileSync(new URL("../contracts/fixtures/native-known-stale-window-v3.json", import.meta.url), "utf8")).execution_policy;
const continuousPolicy = JSON.parse(readFileSync(new URL("../contracts/fixtures/continuous-strict-v2.json", import.meta.url), "utf8")).execution_policy;
const child = fileURLToPath(new URL("./fixtures/sampled-agent-child.mjs", import.meta.url));
const cleanup: (() => Promise<void>)[] = [];
function gate() { let release!: () => void; const promise = new Promise<void>(resolve => { release = resolve; }); return { promise, release }; }
function abortable(promise: Promise<void>, signal?: AbortSignal): Promise<void> {
  if (!signal) return promise;
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => reject(new Error(String(signal.reason ?? "aborted")));
    signal.addEventListener("abort", abort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
async function until(predicate: () => boolean) {
  for (let i = 0; i < 2000 && !predicate(); i++) await new Promise<void>(resolve => setImmediate(resolve));
  expect(predicate(), "bounded actual OS/SDK fixture phase reached").toBe(true);
}
async function handoff(runtime: NativeAgentRuntimeOwner) {
  const pending = (runtime as unknown as { budgetHandoff: Promise<void> | null }).budgetHandoff;
  expect(pending).not.toBeNull(); await pending;
}

// Real SDK assembly/dispatch and OS Ndjson child, with explicitly scripted
// source/authority responses. This does not replace the real C# Store pressure gate.
async function fixture(options: { legacy?: boolean; continuous?: boolean; window?: boolean; causalStale?: boolean; maxSubmissions?: number; maxPolicyCalls?: number; deadlineMs?: number; scenario?: string; failRenewal?: boolean; teacher?: boolean } = {}) {
  const root = await mkdtemp(join(tmpdir(), "owned-stale-runtime-")), source = new SyntheticNativeHttp(1, "interactive", options.failRenewal ? 1000 : 120000);
  const manifest = structuredClone(shared.manifest) as AgentManifest;
  // RED selects the original supported v1 policy deliberately, since the old
  // Runtime cannot admit policy3. Production never reads this test-only flag.
  const selectedPolicy = options.window && process.env.NATIVE_STALE_WINDOW_RED_POLICY1 !== "1"
    ? knownWindowPolicy : options.continuous ? continuousPolicy : policy;
  if (!options.legacy) { manifest.execution_policy = structuredClone(selectedPolicy); manifest.requirements.required_methods.push("current_owned"); }
  let teacherPython: string | undefined;
  if (options.teacher) {
    const candidates = process.env.E6_OWNED_STALE_TEACHER_PYTHON ? [process.env.E6_OWNED_STALE_TEACHER_PYTHON]
      : process.env.PYTHON ? [process.env.PYTHON] : ["python", "python3"];
    for (const candidate of candidates) {
      try { execFileSync(candidate, ["-c", "import sys;assert sys.version_info >= (3,11)"], { stdio: "pipe", timeout: 5000 }); teacherPython = candidate; break; } catch { /* Next existing interpreter only; never install. */ }
    }
    if (!teacherPython) throw new Error("existing Python3.11 source-test interpreter required");
    const code = "import json,sys\nfrom pathlib import Path\nfrom stpd.policy.native_teacher_agent import descriptor,write_artifact\np=json.loads(sys.argv[2]);d=descriptor(execution_policy=p);a=write_artifact(Path(sys.argv[1]),execution_policy=p)\nassert 'torch' not in sys.modules\nprint(json.dumps(dict(artifact=a,adapter=d['adapter'],version=d['agent_spec']['version'],input_spec=d['input_spec'])))";
    const descriptor = JSON.parse(execFileSync(teacherPython, ["-c", code, join(root, "private-teacher-code.json"), JSON.stringify(selectedPolicy)],
      { encoding: "utf8", timeout: 10000, env: { ...process.env, PYTHONPATH: fileURLToPath(new URL("../../../python", import.meta.url)) } }));
    manifest.adapter = descriptor.adapter; manifest.artifact = descriptor.artifact; manifest.input.input_spec = descriptor.input_spec;
    manifest.agent = { id: "stpd-native-public-program-teacher", version: descriptor.version, provider: "programmed_teacher", architecture: "finite_public_program" };
    manifest.support.interaction_kinds = ["*"]; manifest.support.action_verbs = ["*"];
  }
  source.capabilities.supported_methods.push("current_owned"); source.capabilities.implemented_mechanisms.push("native_current_reader_owned_v1");
  const calls: { operation: string; body: JsonObject }[] = [], releases: string[] = [], handles = new Set<string>();
  const offeredFacts = new Map<string, string>();
  const publicFacts = () => canonicalJson({ persistent: source.observation.persistent,
    interaction: source.observation.interaction, referents: source.observation.referents, catalog: source.actions });
  let factRevision = 0;
  const changePublicFacts = () => {
    // Genuine full visible rendering/member change; selected C member stays
    // offered. This mock revalidation proves no native legality/settlement.
    source.observation.persistent = { content_schema: "public-persistent-fixture",
      content: { rendered_cards: ["visible-card-" + ++factRevision] } };
    source.observation.revision = factRevision + 1;
    source.observation.snapshot_id = "facts-" + factRevision;
    source.observation.owner_occurrence.occurrence_id = "facts-occurrence-" + factRevision;
    source.capture.snapshot_id = source.observation.snapshot_id;
    source.observation.catalog.snapshot_id = source.observation.snapshot_id;
    source.rehash();
  };
  let serial = 0, clock = 0, behavior: "stale" | "delivered" | "unknown" | "partial" | "pending" = "stale";
  let submitGate: ReturnType<typeof gate> | undefined;
  let awaitGate: ReturnType<typeof gate> | undefined;
  let currentGate: ReturnType<typeof gate> | undefined;
  let controllerReleases = 0;
  let controllerReleaseGate: ReturnType<typeof gate> | undefined;
  let lastOffered: { observation: typeof source.observation; capture: typeof source.capture; actions: typeof source.actions } | undefined;
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
    releaseController: async () => { controllerReleases++; await controllerReleaseGate?.promise; return { raw: {}, data: { runtime_instance_id: source.capture.session.runtime_instance_id, status: "controller_released", controller: null } }; },
    nativeLogicalRequest: async (operation: NativeLogicalTransportOperation, body: JsonObject = {}, transportOptions: NativeLogicalTransportOptions = {}) => {
      transportOptions.signal?.throwIfAborted(); calls.push({ operation, body: structuredClone(body) });
      if (operation === "submit") {
        onSubmit?.();
        if (options.causalStale && behavior === "stale") changePublicFacts();
        await submitGate?.promise;
      }
      if (operation === "await" && awaitGate) await abortable(awaitGate.promise, transportOptions.signal);
      if (operation === "current_owned" && currentGate) await abortable(currentGate.promise, transportOptions.signal);
      let response;
      if (operation === "current_owned") {
        if (options.causalStale) offeredFacts.set(source.capture.snapshot_id, publicFacts());
        lastOffered = structuredClone({ observation: source.observation, capture: source.capture, actions: source.actions });
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
        const knownStale = options.causalStale
          ? offeredFacts.get(String(body.expected_snapshot_id)) !== publicFacts() : behavior === "stale";
        if (knownStale) Object.assign(value, { delivery: "not_started", execution: "not_started", effect: "not_observed", action: null, stages: [], reason: "stale_snapshot_or_binding", retry: "never_automatic",
          ...(options.causalStale ? { snapshot_id: body.expected_snapshot_id } : {}) });
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
  const closePort = fixturePortCloser(port);
  const inputs: unknown[] = [], next = port.next.bind(port);
  port.next = (...args) => { inputs.push(structuredClone(args[1])); return next(...args); };
  let runtime: NativeAgentRuntimeOwner;
  try { runtime = await PolicyRuntime.forAgent({ manifest, environment, port, evidence, mode: "auto", monotonicNow: () => clock,
    runtimeIdentity: { version: "fixture", code_sha256: "1".repeat(64) }, autoBudget: { maxSubmissions: options.maxSubmissions ?? 200, maxPolicyCalls: options.maxPolicyCalls ?? 1000, deadlineMs: options.deadlineMs ?? 60000 } }); }
  catch (error) { const diagnostics = Buffer.concat((port as unknown as { stderrChunks: Buffer[] }).stderrChunks).toString(); await closePort(); await rm(root, { recursive: true, force: true }); throw new Error(String(error) + "\n" + diagnostics, { cause: error }); }
  cleanup.push(async () => { await runtime.stop().catch(() => undefined); await closePort(); await rm(root, { recursive: true, force: true }); });
  const events = async () => (await readFile(join(evidence.directory, "events.jsonl"), "utf8")).trim().split("\n").filter(Boolean).map(line => JSON.parse(line));
  const advance = (i: number) => {
    applyFrame(i % 2 ? "inspect_b" : "map_c");
    source.observation.revision = i + 1; source.observation.snapshot_id = "advanced-" + i;
    source.observation.owner_occurrence.occurrence_id = "occurrence-" + i;
    source.capture.snapshot_id = source.observation.snapshot_id; source.observation.catalog.snapshot_id = source.observation.snapshot_id; source.rehash();
  };
  return { runtime, port, evidence, source, calls, handles, releases, inputs, events, applyFrame, advance,
    manifest: structuredClone(manifest), changePublicFacts,
    clock: (value: number) => { clock = value; },
    behavior: (value: typeof behavior) => { behavior = value; }, holdSubmit: () => { submitGate = gate(); return submitGate; },
    holdAwait: () => { awaitGate = gate(); return awaitGate; },
    holdCurrent: () => { currentGate = gate(); return currentGate; },
    controllerReleases: () => controllerReleases,
    holdControllerRelease: () => { controllerReleaseGate = gate(); return controllerReleaseGate; },
    restoreOffered: () => { if (!lastOffered) throw new Error("no original offered frame");
      source.observation = structuredClone(lastOffered.observation); source.capture = structuredClone(lastOffered.capture);
      source.actions.splice(0, source.actions.length, ...structuredClone(lastOffered.actions)); source.rehash(); },
    onSubmit: (fn: () => void) => { onSubmit = fn; }, failRelease: (fn: (id: string) => boolean) => { failRelease = fn; },
    expire: () => { clock = options.deadlineMs ?? 60000; } };
}
afterEach(async () => { vi.useRealTimers(); vi.restoreAllMocks(); for (const close of cleanup.splice(0)) await close(); });

describe("native policy3 causal continuous-goal falsifiers", () => {
  it("continues beyond eight genuine whole-facts refusals spread across delivered progress", async () => {
    const f = await fixture({ window: true, causalStale: true });
    const results: string[] = [];
    for (let index = 0; index < 18; index++) {
      f.changePublicFacts(); f.clock(index * 100);
      f.behavior(index % 2 === 0 ? "stale" : "delivered");
      const result = await f.runtime.tick(); results.push(result.type);
      expect(result.type, JSON.stringify({ index, results, last: f.runtime.status().last_result })).toBe(index % 2 === 0 ? "fresh_decision_required" : "delivered");
    }
    const events = await f.events();
    const terminal = events.filter(event => event.kind === "native_result");
    expect(terminal.filter(event => event.payload.result.delivery === "not_started")).toHaveLength(9);
    expect(terminal.filter(event => event.payload.result.delivery === "delivered")).toHaveLength(9);
    expect(events.filter(event => event.kind === "native_stale_decision_deferred")).toHaveLength(9);
    expect(f.calls.filter(call => call.operation === "submit")).toHaveLength(18);
    expect(new Set(f.calls.filter(call => call.operation === "submit").map(call => call.body.request_id)).size).toBe(18);
    expect(f.calls.filter(call => call.operation === "submit").every(call => call.body.bound_action_id === "map_a-action")).toBe(true);
    expect(f.runtime.status()).toMatchObject({ mode: "auto", tainted: false,
      autonomy_budget: { submissions_used: 18, policy_calls_used: 18 } });
    await f.runtime.stop(); expect(f.handles.size).toBe(0);
    const python = process.env.STS2_EVIDENCE_PYTHON ?? process.env.PYTHON;
    if (!python) throw new Error("formal existing Python3.11 required for real typed verifier");
    const code = "import sys;sys.path.insert(0,sys.argv[2]);from sts2_platform_evidence import verify_agent_session_run_evidence;r=verify_agent_session_run_evidence(sys.argv[1]);assert r.passed,r.findings;s=r.value.terminal_summary;assert (s['known_stale_rejections'],s['known_delivered'])==(9,9);assert not s['live_eligibility_proved'];print('typed original counters9/9 PASS')";
    expect(execFileSync(python, ["-c", code, f.evidence.directory,
      fileURLToPath(new URL("../../evidence", import.meta.url))], { encoding: "utf8", timeout: 10000 })).toContain("PASS");
  });

  it("continues three-plus short consecutive genuine changed-unit refusals before a fresh delivery", async () => {
    const f = await fixture({ window: true, causalStale: true });
    for (let index = 0; index < 4; index++) {
      f.changePublicFacts(); f.clock(index * 100);
      expect((await f.runtime.tick()).type, JSON.stringify({ index, last: f.runtime.status().last_result })).toBe("fresh_decision_required");
    }
    f.changePublicFacts(); f.clock(400); f.behavior("delivered");
    expect((await f.runtime.tick()).type).toBe("delivered");
    expect(f.runtime.status()).toMatchObject({ mode: "auto", tainted: false,
      autonomy_budget: { submissions_used: 5, policy_calls_used: 5 } });
    expect((await f.events()).filter(event => event.kind === "native_stale_decision_deferred")).toHaveLength(4);
    await f.runtime.stop(); expect(f.handles.size).toBe(0);
  });
});

describe("native policy3 exact composition contract", () => {
  it("accepts only the declared five fields and binds reader-owned sampled Current", () => {
    expect(validateAgentExecutionPolicy(knownWindowPolicy)).toEqual(knownWindowPolicy);
    for (const invalid of [ { ...knownWindowPolicy, known_stale_recovery_window_ms: 9999 },
      { ...knownWindowPolicy, known_stale_recovery_window_ms: true },
      { ...knownWindowPolicy, max_known_stale_rejections: 8 }, { ...knownWindowPolicy, extra: true },
      { ...knownWindowPolicy, schema: "unsupported-policy" } ]) expect(() => validateAgentExecutionPolicy(invalid)).toThrow();
    const manifest = structuredClone(shared.manifest) as AgentManifest;
    manifest.execution_policy = knownWindowPolicy; manifest.requirements.required_methods.push("current_owned");
    expect(validateAgentManifest(manifest).execution_policy).toEqual(knownWindowPolicy);
    manifest.requirements.required_methods = manifest.requirements.required_methods.filter(m => m !== "current_owned");
    expect(() => validateAgentManifest(manifest)).toThrow();
  });

  it("actual shared Teacher child uses policy3 over fresh complete original inputs", async () => {
    const f = await fixture({ window: true, causalStale: true, teacher: true });
    for (let i = 0; i < 4; i++) { f.changePublicFacts(); f.clock(i * 100); expect((await f.runtime.tick()).type).toBe("fresh_decision_required"); }
    f.changePublicFacts(); f.behavior("delivered");
    expect((await f.runtime.tick()).type).toBe("delivered");
    expect(f.manifest.agent.version).toBe("1.8.0"); expect(f.manifest.execution_policy).toEqual(knownWindowPolicy);
    expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(5);
    expect(new Set(f.calls.filter(c => c.operation === "submit").map(c => c.body.request_id)).size).toBe(5);
    await f.runtime.stop(); expect(f.handles.size).toBe(0);
  });
});

describe("native policy3 original owner deadline and cleanup", () => {
  it("expires while idle with no second tick or GET and freezes earlier local attribution", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const f = await fixture({ window: true, causalStale: true });
    expect((await f.runtime.tick()).type).toBe("fresh_decision_required");
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000); await handoff(f.runtime);
    const events = await f.events();
    expect(events.filter(e => e.kind === "handoff_to_human").map(e => e.payload.reason)).toEqual(["known_stale_recovery_exhausted"]);
    expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(1);
    expect(f.controllerReleases()).toBe(1); expect(f.handles.size).toBe(0);
    expect(f.calls.filter(c => c.operation === "detach")).toHaveLength(1);
    f.clock(70000);
    expect(f.runtime.status()).toMatchObject({ mode: "human", tainted: false,
      autonomy_budget: { state: "inactive", elapsed_ms: 10000, exhausted_reason: null,
        submissions_used: 1, policy_calls_used: 1 } });
    expect((await f.runtime.tick()).type).toBe("human");
  });

  it.each(["await", "current_owned"])("cancels an actual blocked %s during Next, without a later tick", async operation => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const f = await fixture({ window: true, causalStale: true });
    expect((await f.runtime.tick()).type).toBe("fresh_decision_required");
    if (operation === "await") { f.restoreOffered(); f.holdAwait(); } else f.holdCurrent();
    const before = f.calls.filter(c => c.operation === operation).length;
    const tick = f.runtime.tick();
    await until(() => f.calls.filter(c => c.operation === operation).length > before);
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000);
    expect(await tick).toMatchObject({ type: "not_admitted", reason: "known_stale_recovery_exhausted" });
    await handoff(f.runtime);
    expect(f.handles.size).toBe(0); expect(f.controllerReleases()).toBe(1);
    expect((await f.events()).filter(e => e.kind === "handoff_to_human")).toHaveLength(1);
    expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(1);
  });

  it("anchors receipt before blocked native-result Evidence and keeps original result before handoff", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const f = await fixture({ window: true, causalStale: true }), held = gate();
    const append = f.evidence.append.bind(f.evidence); let resultOffered = false;
    vi.spyOn(f.evidence, "append").mockImplementation(async (...args) => {
      if (args[0] === "native_result") { resultOffered = true; await held.promise; }
      return append(...args);
    });
    const tick = f.runtime.tick(); await until(() => resultOffered);
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000);
    expect(f.controllerReleases()).toBe(1);
    held.release(); expect(await tick).toMatchObject({ type: "not_delivered", reason: "known_stale_recovery_exhausted" });
    await handoff(f.runtime);
    const kinds = (await f.events()).map(e => e.kind);
    expect(kinds.indexOf("native_result")).toBeLessThan(kinds.indexOf("handoff_to_human"));
    expect(kinds.filter(k => k === "native_stale_decision_deferred")).toHaveLength(0);
    expect(kinds.filter(k => k === "handoff_to_human")).toHaveLength(1);
  });

  it("classifies a submitted late delivery once, after immediate control revoke, without refund", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const f = await fixture({ window: true, causalStale: true });
    await f.runtime.tick(); f.changePublicFacts(); f.clock(9999); f.behavior("delivered");
    const held = f.holdSubmit(), tick = f.runtime.tick();
    await until(() => f.calls.filter(c => c.operation === "submit").length === 2);
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000);
    expect(f.controllerReleases()).toBe(1);
    held.release(); expect((await tick).type).toBe("delivered"); await handoff(f.runtime);
    const events = await f.events();
    expect(events.filter(e => e.kind === "native_result").map(e => e.payload.result.delivery)).toEqual(["not_started", "delivered"]);
    expect(events.filter(e => e.kind === "handoff_to_human").map(e => e.payload.reason)).toEqual(["known_stale_recovery_exhausted"]);
    expect(f.runtime.status()).toMatchObject({ mode: "human", autonomy_budget: { submissions_used: 2, policy_calls_used: 2, state: "inactive" } });
    expect(f.handles.size).toBe(0);
  });

  it.each([6000, 10100])("retains earlier/equal original global deadline %s", async deadlineMs => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const f = await fixture({ window: true, causalStale: true, deadlineMs }); f.clock(100);
    await f.runtime.tick(); f.clock(deadlineMs); await vi.advanceTimersByTimeAsync(deadlineMs); await handoff(f.runtime);
    expect(f.runtime.status()).toMatchObject({ mode: "human", autonomy_budget: { state: "exhausted", exhausted_reason: "deadline" } });
    const events = await f.events();
    expect(events.filter(e => e.kind === "autonomy_budget_exhausted")).toHaveLength(1);
    expect(events.filter(e => e.kind === "handoff_to_human")).toHaveLength(0);
  });

  it("never reanchors changed Current, complete ACK/Next, repeated Auto or mode cycling", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const f = await fixture({ window: true, causalStale: true }); await f.runtime.tick();
    for (let i = 1; i < 6; i++) {
      f.clock(i * 1500); f.changePublicFacts(); expect((await f.runtime.tick()).type).toBe("fresh_decision_required");
    }
    await f.runtime.setMode("auto"); await f.runtime.setMode("one_step"); await f.runtime.setMode("auto");
    f.restoreOffered(); f.clock(9999); expect((await f.runtime.tick()).type).toBe("awaited");
    expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(6);
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000); await handoff(f.runtime);
    expect((await f.events()).filter(e => e.kind === "handoff_to_human").map(e => e.payload.reason)).toEqual(["known_stale_recovery_exhausted"]);
    expect(f.runtime.status().autonomy_budget).toMatchObject({ submissions_used: 6, policy_calls_used: 7 });
  });

  it("live delivery closes an episode and a later refusal starts a new one under the original wallet", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const f = await fixture({ window: true, causalStale: true }); await f.runtime.tick();
    f.clock(9000); f.changePublicFacts(); f.behavior("delivered"); expect((await f.runtime.tick()).type).toBe("delivered");
    f.clock(9500); f.changePublicFacts(); f.behavior("stale"); expect((await f.runtime.tick()).type).toBe("fresh_decision_required");
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000);
    expect(f.runtime.status().mode).toBe("auto");
    f.clock(19500); await vi.advanceTimersByTimeAsync(10000); await handoff(f.runtime);
    expect(f.runtime.status().autonomy_budget).toMatchObject({ submissions_used: 3, policy_calls_used: 3, elapsed_ms: 19500 });
  });

  it.each(["unknown", "partial", "pending"] as const)("strictly ends %s without defer or fresh retry", async behavior => {
    const f = await fixture({ window: true, causalStale: true }); f.behavior(behavior);
    expect((await f.runtime.tick()).type).toBe("unknown");
    expect((await f.runtime.tick()).type).toBe("human");
    const events = await f.events(); expect(events.filter(e => e.kind === "native_stale_decision_deferred")).toHaveLength(0);
    expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(1);
  });

  it("retains original submission limit instead of spending the window past the wallet", async () => {
    const f = await fixture({ window: true, causalStale: true, maxSubmissions: 1 });
    expect(await f.runtime.tick()).toMatchObject({ type: "not_delivered", reason: "autonomy_budget_exhausted" });
    expect(f.runtime.status().autonomy_budget).toMatchObject({ state: "exhausted", exhausted_reason: "submission_attempt_limit" });
    expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(1);
  });

  it.each(["human", "stop"])("winning %s remains sticky at a blocked receipt append", async winner => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const f = await fixture({ window: true, causalStale: true }), held = gate();
    const append = f.evidence.append.bind(f.evidence); let offered = false;
    vi.spyOn(f.evidence, "append").mockImplementation(async (...args) => {
      if (args[0] === "native_result") { offered = true; await held.promise; }
      return append(...args);
    });
    const tick = f.runtime.tick(); await until(() => offered);
    const changed = winner === "human" ? f.runtime.setMode("human") : f.runtime.stop();
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000);
    held.release(); await tick; await changed;
    expect(f.runtime.status().mode).toBe("human");
    expect((await f.events()).filter(e => e.kind === "handoff_to_human" && e.payload.reason === "known_stale_recovery_exhausted")).toHaveLength(0);
    expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(1);
  });

  it("expiry during second dispatch-binding append cannot start native submission", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const f = await fixture({ window: true, causalStale: true }); await f.runtime.tick();
    f.changePublicFacts(); f.clock(9999);
    const held = gate(), append = f.evidence.append.bind(f.evidence); let offered = false;
    vi.spyOn(f.evidence, "append").mockImplementation(async (...args) => {
      if (args[0] === "native_submission_requested") { offered = true; await held.promise; }
      return append(...args);
    });
    const tick = f.runtime.tick(); await until(() => offered);
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000); held.release();
    expect(await tick).toMatchObject({ type: "not_admitted", reason: "known_stale_recovery_exhausted" });
    await handoff(f.runtime);
    const events = await f.events();
    expect(events.filter(e => e.kind === "native_result")).toHaveLength(1);
    expect(events.filter(e => e.kind === "native_submission_not_started")).toHaveLength(1);
    expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(1);
  });

  it("failed original reader release taints local cleanup without retrying or dropping the local reason", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const f = await fixture({ window: true, causalStale: true }); await f.runtime.tick();
    f.failRelease(() => true); f.clock(10000); await vi.advanceTimersByTimeAsync(10000); await handoff(f.runtime);
    expect(f.runtime.status()).toMatchObject({ mode: "human", tainted: true });
    expect(f.handles.size).toBe(1);
    expect(f.releases).toHaveLength(1);
    expect((await f.events()).filter(e => e.kind === "handoff_to_human").map(e => e.payload.reason)).toEqual(["known_stale_recovery_exhausted"]);
  });

  it("failed handoff append taints and never leaves live continuation", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const f = await fixture({ window: true, causalStale: true }); await f.runtime.tick();
    const append = f.evidence.append.bind(f.evidence);
    vi.spyOn(f.evidence, "append").mockImplementation(async (...args) => {
      if (args[0] === "handoff_to_human") throw new Error("injected original handoff write failure");
      return append(...args);
    });
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000); await handoff(f.runtime);
    expect(f.runtime.status()).toMatchObject({ mode: "human", tainted: true });
    expect(f.handles.size).toBe(0); expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(1);
  });

  it("transport failure after SDK start remains unknown and never arms a known-stale episode", async () => {
    const f = await fixture({ window: true, causalStale: true });
    f.onSubmit(() => { throw new Error("injected submitted transport failure"); });
    expect((await f.runtime.tick()).type).toBe("unknown");
    expect(f.runtime.status()).toMatchObject({ mode: "human", tainted: true });
    expect((await f.events()).filter(e => e.kind === "native_stale_decision_deferred")).toHaveLength(0);
    expect(f.calls.filter(c => c.operation === "submit")).toHaveLength(1);
  });

  it("overlapping local expiry and Stop share one deferred release wire and durable classification", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const f = await fixture({ window: true, causalStale: true }); await f.runtime.tick();
    const held = f.holdControllerRelease();
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000);
    await until(() => f.controllerReleases() === 1);
    const stopped = f.runtime.stop(); await new Promise<void>(resolve => setImmediate(resolve));
    expect(f.controllerReleases()).toBe(1); expect(f.runtime.status().controller).toBe("unknown");
    held.release(); await handoff(f.runtime); await stopped;
    const events = await f.events();
    expect(events.filter(e => e.kind === "controller_released")).toHaveLength(1);
    expect(events.filter(e => e.kind === "handoff_to_human").map(e => e.payload.reason)).toEqual(["known_stale_recovery_exhausted"]);
    expect(f.controllerReleases()).toBe(1); expect(f.handles.size).toBe(0);
  });
});
