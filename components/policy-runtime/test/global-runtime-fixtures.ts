import { createServer, type Server } from "node:http";
import { readFile, writeFile, mkdtemp } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { once } from "node:events";
import { createHash } from "node:crypto";
import type { AgentAdapterIdentity } from "../src/agent-session-contracts.js";
import { PlayerEnvironmentRestClient } from "@rsgcsg/sts2-connector-client";
import { type GlobalTransport, ReferenceGlobalTransport } from "../src/global-transport.js";
import { createGlobalProgramManifest } from "../src/global-program.js";
import { AgentRunEvidence } from "../src/evidence.js";
import { NdjsonAgentSessionPort } from "../src/agent-session-port.js";
import { globalRuntimeCodeDigest } from "../src/global-replay.js";
import { PolicyRuntime } from "../src/runtime.js";
import type { NativeAgentRuntimeOwner } from "../src/agent-native-runtime.js";
const fixture = (name: string): any => JSON.parse(readFileSync(new URL(`../../connector/sdk/typescript/test/fixtures/p1-global/${name}.json`, import.meta.url), "utf8"));
const sha = (v: string | Buffer) => createHash("sha256").update(v).digest("hex");
export class GlobalHttpFixture {
  nextBases: any[] = [];
  captureObserver: (() => void) | null = null;
  basis = fixture("synthetic-complete-combat"); server?: Server; address = ""; requests: { path: string; body: any }[] = [];
  private held = false; releases = 0; steps = 0; terminalAfterStep = true; captureGate: Promise<void> | null = null;
  delivery: string = "delivered"; wrongRequest = false; wrongAction = false; releaseFailure = false; releaseObserved: (() => void) | null = null;
  captures = new Map<string, { handle: any; bytes: Buffer }>();
  continuity: string | null = "fixture-episode"; afterStepContinuity: string | null | undefined; wrongSuccessorSession = false; postCaptureGate: Promise<void> | null = null; postCaptureEntered: (()=>void) | null = null;
  capabilities: any;
  constructor() {
    const session = this.basis.snapshot.session;
    this.capabilities = { protocol_version: "1.0.0", snapshot_schema: "sts2.player-environment/snapshot-1", action_schema: "sts2.player-environment/action-1",
      receipt_schema: "sts2.player-environment/receipt-1", control_schema: "sts2.player-environment/control-1", status: "ready",
      host: { id: "fixture", name: "fixture", version: "1", runtime_instance_id: session.runtime_instance_id, host_kind: "test",
        implementation: { source_revision: "a".repeat(40), module_version_id: "fixture-mvid", artifact_sha256: "b".repeat(64) } },
      game: { version: "fixture-game", commit: "fixture-commit", compatibility: { status: "exact", observation_allowed: true, detail: "synthetic" },
        modset: { status: "exact", fingerprint: "c".repeat(64), scope: "fixture", loaded_mod_ids: [], detail: "synthetic" } },
      environment_fingerprint: session.environment_fingerprint, verbs: ["play", "end_turn"], snapshot_bound: true, single_controller: true, execution_available: true,
      control: { recommended_renewal_ms: 1000 }, evidence_profiles: [], non_claims: ["synthetic_product_plumbing_only"] };
  }
  get controlHeld() { return this.held; }
  async start() {
    this.server = createServer(async (req, res) => {
      const raw: Buffer[] = []; for await (const chunk of req) raw.push(Buffer.from(chunk));
      const body = raw.length ? JSON.parse(Buffer.concat(raw).toString()) : {};
      const url = new URL(req.url!, "http://127.0.0.1"); this.requests.push({ path: url.pathname, body });
      const out = (value: any) => { res.setHeader("content-type", "application/json"); res.end(JSON.stringify(value)); };
      try {
        if (url.pathname === "/api/player-environment/capabilities") return out(this.capabilities);
        if (url.pathname.endsWith("/public-basis/capabilities")) return out({ schema: "sts2.player-environment/public-basis-capabilities-1", read_profile: "public-basis-sealed-1", input_profile: "public-basis-1",
          capture_schema: "sts2.player-environment/public-basis-handle-1", chunk_schema: "sts2.player-environment/public-basis-chunk-1", release_schema: "sts2.player-environment/public-basis-release-1",
          current_route: "/api/player-environment/public-basis/current", read_route: "/api/player-environment/public-basis/read", release_route: "/api/player-environment/public-basis/release",
          max_capsule_bytes: 8388608, max_retained_bytes: 16777216, max_capsules: 8, ttl_ms: 30000, default_chunk_bytes: 65536,
          min_chunk_bytes: 256, max_chunk_bytes: 1048576, creates_mutation_authority: false, scope: "current_public_snapshot_and_advertised_read_instances" });
        if (url.pathname.endsWith("/public-basis/current")) {
          if (this.captureGate) await this.captureGate;
          if(this.steps && this.postCaptureGate) { this.postCaptureEntered?.(); await this.postCaptureGate; }
          this.captureObserver?.();
          const value = { schema: "sts2.player-environment/public-basis-1", revision: "2", capture_scope: "current_public_snapshot_and_advertised_read_instances", capture_status: "complete", ...this.basis, snapshot: { ...this.basis.snapshot, input_profile: "public-basis-1" }, missing: this.basis.missing ?? [] };
          const bytes = Buffer.from(JSON.stringify(value)), id = `capture-${this.captures.size + 1}`;
          const handle = { schema: "sts2.player-environment/public-basis-handle-1", read_profile: "public-basis-sealed-1", input_profile: "public-basis-1", capture_id: id,
            source_snapshot_id: value.snapshot.snapshot_id, session: value.snapshot.session, generation_id: "generation-1", game_continuity_id: this.continuity,
            captured_at: new Date().toISOString(), expires_at: new Date(Date.now() + 30000).toISOString(), total_bytes: bytes.length, sha256: sha(bytes), first_cursor: "cursor_0", capture_ordinal: this.captures.size + 1 };
          this.captures.set(id, { handle, bytes }); return out(handle);
        }
        if (url.pathname.endsWith("/public-basis/read")) {
          const f = this.captures.get(url.searchParams.get("capture_id")!)!, offset = Number(url.searchParams.get("cursor")!.split("_")[1]), end = Math.min(f.bytes.length, offset + Number(url.searchParams.get("max_bytes")));
          return out({ schema: "sts2.player-environment/public-basis-chunk-1", capture_id: f.handle.capture_id, sha256: f.handle.sha256,
            offset, total_bytes: f.bytes.length, data_base64: f.bytes.subarray(offset, end).toString("base64"), next_cursor: end === f.bytes.length ? null : `cursor_${end}`, end: end === f.bytes.length });
        }
        if (url.pathname.endsWith("/public-basis/release")) return out({ schema: "sts2.player-environment/public-basis-release-1", capture_id: body.capture_id, released: true });
        const control = { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1", runtime_instance_id: this.capabilities.host.runtime_instance_id };
        if (url.pathname.endsWith("/clients/register")) return out({ ...control, client: { client_session_id: "client-1", client_instance_id: body.client_instance_id }, controller: null });
        if (url.pathname.endsWith("/controller/acquire") || url.pathname.endsWith("/controller/renew")) {
          this.held = true; return out({ ...control, status: "controller_acquired", detail: "synthetic", controller: { controller_lease_id: "lease-1", controller_generation: 1, client_session_id: "client-1", expires_at: new Date(Date.now() + 60000).toISOString() } });
        }
        if (url.pathname.endsWith("/controller/release")) {
          this.releases++; this.releaseObserved?.(); if (this.releaseFailure) { res.statusCode = 500; return out({ error: "release_unconfirmed" }); }
          this.held = false; return out({ ...control, status: "controller_released", detail: "synthetic", controller: null });
        }
        if (url.pathname === "/api/player-environment/actions") {
          if (body.input_profile !== "public-basis-1") throw new Error("public_basis_profile_required");
          this.steps++; const bound = this.basis.snapshot.bound_actions.actions.find((a: any) => a.bound_action_id === body.bound_action_id)!;
          const { interaction_id: _i, label: _l, ...action } = bound;
          if (this.wrongAction) action.bound_action_id = "foreign-action";
          if (this.nextBases.length) this.basis = this.nextBases.shift();
          else if (this.terminalAfterStep) { this.basis = fixture("managed-game_over"); this.basis.snapshot.session = this.capabilitiesSession(); for (const r of this.basis.reads) r.response.session = this.capabilitiesSession(); }
          else { this.basis.snapshot.snapshot_id += "-next"; this.basis.snapshot.sequence++; for (const r of this.basis.reads) { r.response.expected_snapshot_id = r.response.observed_snapshot_id = this.basis.snapshot.snapshot_id; } }
          if(this.afterStepContinuity!==undefined)this.continuity=this.afterStepContinuity;
          const successor={ ...structuredClone(this.basis.snapshot), input_profile: "public-basis-1" };if(this.wrongSuccessorSession)successor.session.runtime_instance_id="foreign-runtime";
          return out({ protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1", request_id: this.wrongRequest ? "foreign-request" : body.request_id,
            input_profile: "public-basis-1", delivery: this.delivery, action, reason_code: null, detail: null, retry: { allowed: false, reason: "synthetic" }, successor });
        }
        res.statusCode = 404; out({ error: "unexpected" });
      } catch (error) { res.statusCode = 500; out({ error: String(error) }); }
    });
    this.server.listen(0, "127.0.0.1"); await once(this.server, "listening"); const address = this.server.address();
    if (!address || typeof address === "string") throw new Error("fixture listener unavailable"); this.address = `http://127.0.0.1:${address.port}`;
  }
  private capabilitiesSession() { return { runtime_instance_id: this.capabilities.host.runtime_instance_id, environment_fingerprint: this.capabilities.environment_fingerprint }; }
  async close() { this.server?.closeAllConnections(); await new Promise<void>(done => this.server!.close(() => done())); }
}
export async function globalRuntimeFixture(options: { editManifest?: (manifest: any) => void; behavior?: string; budgetMs?: number; source?: GlobalHttpFixture; transport?: GlobalTransport; program?: {command:string;args:string[];adapter:AgentAdapterIdentity;artifact:Buffer;env?:NodeJS.ProcessEnv} } = {}) {
  const source = options.source ?? new GlobalHttpFixture(); await source.start();
  const root = await mkdtemp(join(tmpdir(), "global-product-")), transport = options.transport ?? new ReferenceGlobalTransport(new PlayerEnvironmentRestClient(source.address, 2000), { productVersion: "fixture", clientInstanceId: "fixture-client" });
  const identity = await transport.negotiate(new AbortController().signal), artifact = options.program?.artifact ?? Buffer.from('{"program":"synthetic conformance"}'), artifactPath = join(root, "program.json"); await writeFile(artifactPath, artifact);
  const child = fileURLToPath(new URL("./fixtures/global-agent-child.mjs", import.meta.url));
  const manifest = createGlobalProgramManifest({ episodeId: "synthetic-episode", identity, artifactPath, artifactBytes: artifact,
    adapter: options.program?.adapter ?? { id: "global-conformance", version: "1", protocol: "sts2.policy-runtime/agent-session-ndjson-1", code_sha256: sha(await readFile(child)) } });
  manifest.limits.agent_timeout_ms = 5000; options.editManifest?.(manifest);
  const manifestPath = join(root, "agent.json"); await writeFile(manifestPath, JSON.stringify(manifest));
  const runtimeCode = await globalRuntimeCodeDigest(fileURLToPath(new URL("../dist/", import.meta.url)));
  const evidence = await AgentRunEvidence.createSession({ root, agentManifest: manifest, runtimeVersion: "fixture", runtimeCodeSha256: runtimeCode, mode: "auto" });
  const port = NdjsonAgentSessionPort.spawn(options.program?.command ?? process.execPath, options.program?.args.map(a=>a==="{manifest}"?manifestPath:a) ?? [child, manifestPath, options.behavior ?? "act"], manifest.adapter, manifest.limits, {env:options.program?.env});
  const runtime: NativeAgentRuntimeOwner = await PolicyRuntime.forAgent({ manifest, globalTransport: transport, port, evidence,
    runtimeIdentity: { version: "fixture", code_sha256: runtimeCode }, mode: "auto", autoBudget: { deadlineMs: options.budgetMs ?? 10000, maxSubmissions: 20, maxPolicyCalls: 40 } });
  return { root, manifest, evidence, port, runtime, source, transport,
    events: async () => (await readFile(join(evidence.directory, "events.jsonl"), "utf8")).trim().split("\n").map((line: string) => JSON.parse(line)),
    close: async () => { await runtime.stop().catch(() => undefined); await source.close(); } };
}
