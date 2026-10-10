import { createServer, type Server } from "node:http";
import { once } from "node:events";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { digestNativeLogicalActions, PlayerEnvironmentRestClient,
  type NativeLogicalAction, type NativeLogicalCapabilities, type NativeLogicalCapture,
  type NativeLogicalObservation, type JsonObject } from "@rsgcsg/sts2-connector-client";
import { PolicyRuntime } from "../src/runtime.js";
import { AgentRunEvidence } from "../src/evidence.js";
import { NdjsonAgentSessionPort } from "../src/agent-session-port.js";
import type { AgentManifest } from "../src/agent-session-contracts.js";
import type { NativeAgentRuntimeOwner } from "../src/agent-native-runtime.js";
import { fixturePortCloser } from "./fixture-process.js";

const native = JSON.parse(readFileSync(new URL("../../connector/contracts/fixtures/native-logical-v1.json", import.meta.url), "utf8")).wire_samples as Record<string, JsonObject>;
const agent = JSON.parse(readFileSync(new URL("../contracts/fixtures/agent-session-v1.json", import.meta.url), "utf8")).manifest as AgentManifest;
export const CHILD = fileURLToPath(new URL("./fixtures/native-agent-child.mjs", import.meta.url));
export const METHODS = ["capabilities", "current", "read", "catalog", "resolve", "attach", "events", "await", "cancel_wait", "detach", "renew", "retain", "release", "submit", "result"];

export class SyntheticNativeHttp {
  readonly requests: { path: string; body: JsonObject }[] = [];
  readonly capabilities = structuredClone(native.capabilities!) as unknown as NativeLogicalCapabilities;
  readonly actions: NativeLogicalAction[];
  observation = structuredClone(native.observation!) as unknown as NativeLogicalObservation;
  capture = structuredClone(native.capture!) as unknown as NativeLogicalCapture;
  bytes: Buffer = Buffer.alloc(0);
  subscription = structuredClone((native.attach as unknown as { subscription: Record<string, unknown> }).subscription);
  server?: Server; address = "";
  behavior: "delivered" | "pending" | "partial" | "unknown" | "transport_error" = "delivered";
  lookup: "delivered" | "pending" | "unknown" | "expired" = "delivered";
  waitGate: Promise<void> | null = null;
  submitGate: Promise<void> | null = null;
  acquireGate: Promise<void> | null = null;
  capabilitiesGate: Promise<void> | null = null;
  renewGate: Promise<void> | null = null;
  renewInFlight = 0;
  maxRenewInFlight = 0;
  eventKind = "observation";
  generation = 0;
  constructor(count = 3, status: NativeLogicalObservation["status"] = "interactive", readonly retentionMs = 120000) {
    this.actions = Array.from({ length: count }, (_, i) => ({ action_id: `action-${i}`, kind: "native_input", verb: "choose",
      label: `public choice ${i}`, subject_referent_id: `public-${i}`, arguments: [], effect_domain: "native" }));
    this.observation.status = status;
    this.observation.catalog.total_count = count; this.observation.catalog.digest = digestNativeLogicalActions(this.actions);
    this.observation.referents = this.actions.map(action => ({ referent_id: action.subject_referent_id!, role: "control", kind: "control",
      label: action.label, state: { visible: true, enabled: true, selected: null, focused: null, observation_basis: "native_visible_fact" },
      properties_schema: null, properties: null }));
    this.rehash();
    this.capabilities.host.host_kind = "test";
    this.capabilities.host.implementation = { source_revision: "d".repeat(40), artifact_sha256: "e".repeat(64), module_version_id: "fixture-mvid" };
    this.capabilities.game.version = "fixture-game"; this.capabilities.game.commit = "fixture-commit";
    this.capabilities.game.modset.status = "exact"; this.capabilities.game.modset.fingerprint = "f".repeat(64);
    this.capabilities.game.modset.loaded_mod_ids = ["fixture-mod"];
    this.capabilities.supported_methods = [...METHODS];
    this.capabilities.limits.retention_ms = retentionMs;
    this.subscription.starting_cursor = "cursor-0";
    this.subscription.expires_at = new Date(Date.now() + retentionMs).toISOString();
  }
  rehash(): void {
    this.bytes = Buffer.from(JSON.stringify(this.observation)); this.capture.byte_count = this.bytes.length;
    this.capture.sha256 = createHash("sha256").update(this.bytes).digest("hex");
    this.capture.captured_at = new Date().toISOString(); this.capture.expires_at = new Date(Date.now() + 120000).toISOString();
  }
  async start(): Promise<void> {
    this.server = createServer(async (request, response) => {
      const chunks: Buffer[] = []; for await (const part of request) chunks.push(Buffer.from(part));
      const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) as JsonObject : {};
      const url = new URL(request.url!, "http://127.0.0.1"); this.requests.push({ path: url.pathname, body });
      const renewing = url.pathname.endsWith("/renew");
      if (renewing) { this.renewInFlight += 1; this.maxRenewInFlight = Math.max(this.maxRenewInFlight, this.renewInFlight); }
      try {
        if (url.pathname.endsWith("/capabilities") && this.capabilitiesGate) await this.capabilitiesGate;
        if (url.pathname.endsWith("/renew") && this.renewGate) await this.renewGate;
        if (url.pathname.endsWith("/controller/acquire") && this.acquireGate) await this.acquireGate;
        if (url.pathname.endsWith("/await") && this.waitGate) await this.waitGate;
        if (url.pathname.endsWith("/actions") && this.submitGate) await this.submitGate;
        if (url.pathname.endsWith("/actions") && this.behavior === "transport_error") { response.destroy(); return; }
        const result = this.route(url, body);
        const wire = Buffer.from(JSON.stringify(result.value));
        response.writeHead(result.status ?? 200, { "content-type": "application/json", "content-length": wire.length }); response.end(wire);
      } catch (error) { response.writeHead(500); response.end(JSON.stringify({ error: String(error) })); }
      finally { if (renewing) this.renewInFlight -= 1; }
    });
    this.server.listen(0, "127.0.0.1"); await once(this.server, "listening");
    const address = this.server.address(); if (!address || typeof address === "string") throw new Error("synthetic HTTP listener unavailable");
    this.address = `http://127.0.0.1:${address.port}`;
  }
  route(url: URL, body: JsonObject): { value: unknown; status?: number } {
    const op = url.pathname.split("/").at(-1)!;
    if (url.pathname.endsWith("/clients/register")) return { value: { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
      runtime_instance_id: this.capture.session.runtime_instance_id, client: { client_session_id: "client-fixture", client_instance_id: body.client_instance_id }, controller: null } };
    if (url.pathname.includes("/controller/")) return { value: { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
      runtime_instance_id: this.capture.session.runtime_instance_id, status: `controller_${op === "release" ? "released" : "acquired"}`, detail: "synthetic controller",
      client: null, controller: op === "release" ? null : { controller_lease_id: "lease-fixture", controller_generation: 1,
        client_session_id: "client-fixture", expires_at: new Date(Date.now() + 60000).toISOString() } } };
    if (op === "capabilities") return { value: this.capabilities };
    if (op === "attach") { this.subscription.eager_scope = body.eager_scope; this.subscription.delivery_mode = body.delivery_mode;
      this.subscription.expires_at = new Date(Date.now() + this.retentionMs).toISOString();
      return { value: { schema: native.attach!.schema, status: "attached", subscription: this.subscription } }; }
    if (op === "events") {
      const event = structuredClone(((native.event_batch!.events as unknown as { event: Record<string, unknown> }[])[0]!).event);
      Object.assign(event, { cursor: "cursor-1", publication_index: "1", source_index: "1", kind: this.eventKind,
        source_phase: this.eventKind === "terminal" ? "summary_animation_started" : "initial_observation",
        ...(this.eventKind === "terminal" ? { source_seam: "native_terminal_entry" } : {}),
        payload_reference: this.capture, capture_ref: this.capture.capture_id });
      return { value: { schema: native.event_batch!.schema, events: body.after_cursor === "cursor-0" ? [{ event, availability: "available" }] : [],
        next_cursor: "cursor-1", high_watermark: "cursor-1", retained_start_cursor: "cursor-0", gap: null } };
    }
    if (op === "current") {
      return { value: { schema: native.current!.schema, input_profile: "native-logical-v1", status: "captured", capture: this.capture,
        context: { ...native.observation_context!, capture_ref: this.capture.capture_id, observation_ref: this.capture.snapshot_id }, retention: null, reason: null } };
    }
    if (op === "retain") return { value: { schema: "sts2.player-environment/native-logical-retain-1", input_profile: "native-logical-v1", status: "retained",
      retention: { retention_handle_id: "owned-pin", capture: this.capture, read_cursor: "read-0", expires_at: this.capture.expires_at }, reason: null } };
    if (op === "read") {
      const offset = body.cursor === this.capture.read_cursor ? 0 : Number(String(body.cursor).slice(5));
      const block = this.bytes.subarray(offset, offset + Number(body.max_bytes)), end = offset + block.length;
      return { value: { schema: native.read!.schema, capture_id: this.capture.capture_id, sha256: this.capture.sha256,
        offset, total_bytes: this.bytes.length, data_base64: block.toString("base64"), next_cursor: end === this.bytes.length ? null : `read-${end}`, complete: end === this.bytes.length } };
    }
    if (op === "catalog") {
      const start = body.cursor === null ? 0 : Number(String(body.cursor).slice(5));
      const actions = this.actions.slice(start, start + Number(body.limit)), end = start + actions.length;
      return { value: { schema: native.catalog_page!.schema, status: "complete", catalog_ref: this.observation.catalog.catalog_ref,
        snapshot_id: this.capture.snapshot_id, stream_generation: this.capture.stream_generation, total_count: this.actions.length,
        digest: this.observation.catalog.digest, filtered_count: this.actions.length, filtered_digest: this.observation.catalog.digest,
        actions, next_cursor: end < this.actions.length ? `page-${end}` : null, minimum_required_bytes: null } };
    }
    if (op === "resolve") return { value: { schema: "sts2.player-environment/native-logical-resolve-1", status: "unique", action: this.actions[0] ?? null } };
    if (op === "release") return { value: { schema: "sts2.player-environment/native-logical-release-1", input_profile: "native-logical-v1", status: "released",
      retention_handle_id: body.retention_handle_id, released: true, reason: null } };
    if (op === "renew") {
      if (Date.now() >= Date.parse(String(this.subscription.expires_at))) return { value: {
        schema: "sts2.player-environment/native-logical-renew-1", input_profile: "native-logical-v1", status: "subscription_expired",
        subscription: null, next_cursor: null, high_watermark: null, retained_start_cursor: null, gap: null, reason: "subscription_expired" } };
      this.subscription.expires_at = new Date(Date.now() + this.retentionMs).toISOString();
      return { value: { ...native.renew!, subscription: this.subscription,
        next_cursor: body.after_cursor, high_watermark: "cursor-1", retained_start_cursor: "cursor-0" } };
    }
    if (op === "await") return { value: { schema: native.await!.schema, status: "timeout", event: null, gap: null, reason: null } };
    if (op === "cancel_wait") return { value: { ...native.cancel_wait!, wait_id: body.wait_id, subscription_id: body.subscription_id } };
    if (op === "detach") return { value: { ...native.detach!, subscription_id: body.subscription_id } };
    if (op === "actions" || url.pathname.includes("/actions/")) {
      const requested = op === "actions" ? this.behavior : this.lookup;
      if (requested === "pending") return { status: 202, value: { error: { code: "request_pending", detail: "original synthetic request remains pending" } } };
      if (requested === "expired") return { status: 404, value: { error: { code: "result_expired", detail: "original result unavailable" } } };
      const result = { ...native.result!, request_id: body.request_id ?? op, snapshot_id: this.capture.snapshot_id,
        action: this.actions.find(a => a.action_id === body.bound_action_id) ?? this.actions[0] ?? null,
        delivery: requested === "partial" ? "partially_delivered" : requested, execution: requested === "delivered" ? "native_accepted" : "unknown",
        effect: requested === "delivered" ? "pending" : "unknown", cancel: "not_requested", reason: null, stages: [] };
      return { value: result };
    }
    throw new Error(`unhandled synthetic operation ${op}`);
  }
  async close(): Promise<void> {
    this.server?.closeAllConnections(); if (this.server) await new Promise<void>(resolve => this.server!.close(() => resolve()));
  }
}

export async function nativeRuntimeFixture(options: { count?: number; status?: NativeLogicalObservation["status"]; child?: string; mode?: "human" | "shadow" | "one_step" | "auto";
  deadlineMs?: number; maxPolicyCalls?: number; gapPolicy?: "handoff" | "explicit_reset";
  retentionMs?: number;
  monotonicNow?: () => number;
  beforeInitialize?: (value: { source: SyntheticNativeHttp; port: NdjsonAgentSessionPort; evidence: AgentRunEvidence;
    environment: PlayerEnvironmentRestClient }) => void;
  numericalAgent?: { python: string; pythonPath: string; packagePath: string } } = {}) {
  const root = await mkdtemp(join(tmpdir(), "native-runtime-source-"));
  const source = new SyntheticNativeHttp(options.count, options.status, options.retentionMs); await source.start();
  let manifest = structuredClone(agent);
  manifest.support.interaction_kinds = ["*"]; manifest.support.action_verbs = ["*"];
  manifest.requirements.required_methods = [...METHODS];
  if (options.gapPolicy) manifest.input.gap_policy = options.gapPolicy;
  manifest.artifact.path = join(root, "package.json");
  const artifact = Buffer.from('{"scope":"programmed synthetic conformance only"}');
  manifest.artifact.sha256 = createHash("sha256").update(artifact).digest("hex"); await writeFile(manifest.artifact.path, artifact);
  if (options.child === "query") {
    manifest.input.history_mode = "scoped_query"; manifest.input.attachment.delivery_mode = "scoped";
    manifest.input.state_recovery = { mode: "none", max_state_bytes: 0, model_bindings: [] };
  }
  const manifestPath = join(root, "agent.json");
  if (options.numericalAgent) {
    const settings = join(root, "binding-settings.json");
    await writeFile(settings, JSON.stringify({ requirements: manifest.requirements, support: manifest.support,
      required_seams: manifest.input.attachment.required_seams }));
    const code = "import json,sys\nfrom pathlib import Path\nfrom stpd.policy.native_agent import bind_native_agent\ns=json.loads(Path(sys.argv[3]).read_text())\nbind_native_agent(Path(sys.argv[1]),Path(sys.argv[2]),manifest_id='native-numerical-source-fixture',**s)\n";
    execFileSync(options.numericalAgent.python, ["-c", code, options.numericalAgent.packagePath, manifestPath, settings],
      { env: { ...process.env, PYTHONPATH: options.numericalAgent.pythonPath, OMP_NUM_THREADS: "2", MKL_NUM_THREADS: "2" }, timeout: 30000 });
    manifest = JSON.parse(await readFile(manifestPath, "utf8")) as AgentManifest;
  } else await writeFile(manifestPath, JSON.stringify(manifest));
  const evidence = await AgentRunEvidence.createSession({ root, agentManifest: manifest, mode: options.mode ?? "human",
    runtimeVersion: "fixture", runtimeCodeSha256: "1".repeat(64) });
  const port = options.numericalAgent ? NdjsonAgentSessionPort.spawn(options.numericalAgent.python,
    ["-m", "stpd.policy.native_agent", "--package", options.numericalAgent.packagePath, "--manifest", manifestPath],
    manifest.adapter, manifest.limits, { executionPolicy: manifest.execution_policy, env: { ...process.env, PYTHONPATH: options.numericalAgent.pythonPath, OMP_NUM_THREADS: "2", MKL_NUM_THREADS: "2" } })
    : NdjsonAgentSessionPort.spawn(process.execPath, [CHILD, manifestPath, options.child ?? "act"], manifest.adapter, manifest.limits, { executionPolicy: manifest.execution_policy });
  const closePort = fixturePortCloser(port);
  let runtime: NativeAgentRuntimeOwner;
  const environment = new PlayerEnvironmentRestClient(source.address, 2000);
  try { options.beforeInitialize?.({ source, port, evidence, environment });
    runtime = await PolicyRuntime.forAgent({ manifest, environment, port, evidence,
    mode: options.mode ?? "human", monotonicNow: options.monotonicNow, runtimeIdentity: { version: "fixture", code_sha256: "1".repeat(64) },
    autoBudget: { deadlineMs: options.deadlineMs ?? 10000, maxPolicyCalls: options.maxPolicyCalls ?? 32, maxSubmissions: 16 } }); }
  catch (error) {
    const diagnostics = Buffer.concat((port as unknown as { stderrChunks: Buffer[] }).stderrChunks).toString("utf8");
    await closePort(); await source.close(); await rm(root, { recursive: true, force: true });
    throw new Error(`actual conformance child startup failed: ${String(error)}\n${diagnostics}`, { cause: error });
  }
  return { root, source, manifest, manifestPath, runtime, port, evidence,
    events: async () => (await readFile(join(evidence.directory, "events.jsonl"), "utf8")).trim().split("\n").filter(Boolean).map(line => JSON.parse(line)),
    close: async () => { await runtime.stop().catch(() => undefined); await closePort(); await source.close(); await rm(root, { recursive: true, force: true }); } };
}
