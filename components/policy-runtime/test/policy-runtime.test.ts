import { describe, expect, it, vi } from "vitest";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { request as httpRequest, type ClientRequest } from "node:http";
import type { PlayerEnvironmentBoundAction, PlayerEnvironmentReceipt, PlayerEnvironmentSnapshot } from "@rsgcsg/sts2-connector-client";
import { admitWholeDecision } from "../src/admission.js";
import { candidateOrderDigest } from "../src/digest.js";
import { PolicyRuntime, admitWholeDecisionBundle } from "../src/runtime.js";
import { ConnectorPolicyClient } from "../src/connector.js";
import { DEFAULT_POLICY_ADAPTER_STARTUP_TIMEOUT_MS, NdjsonPolicyPort } from "../src/policy-port.js";
import { validateAdapterDecision, validatePolicyDecision, validatePolicyManifest, type ConnectorAdapterClient, type DecisionBundle, type PolicyConnector, type PolicyManifest } from "../src/contracts.js";
import { startPolicyRuntimeHttpServer } from "../src/server.js";
import { AgentRunEvidence, verifyEvidenceDirectory } from "../src/evidence.js";
// @ts-expect-error The Workbench consumer is a JavaScript package; exercise its real decoder.
import { decodePolicyRuntimeStatus, PolicyRuntimeClient } from "../../../apps/workbench/src/policy-runtime-client.mjs";

const manifest = (): PolicyManifest => ({
  schema: "sts2.policy-runtime/policy-manifest-1",
  manifest_id: "manifest-test",
  policy: { id: "policy-test", version: "1", provider: "fixture", architecture: "fixture" },
  adapter: { id: "stpd-decision-only", version: "1", protocol: "sts2.policy-runtime/decision-only-ndjson-1", code_sha256: "c".repeat(64) },
  artifact: { id: "artifact-test", path: "artifact.bin", sha256: "a".repeat(64) },
  representation: { id: "snapshot-representation", version: "1", input_schema: "sts2.player-environment/snapshot-1" },
  requirements: { connector_protocol_version: "1.0.0", environment: { host_kind: "test", connector_version: "1", connector_source_revision: "source", connector_artifact_sha256: "b".repeat(64), connector_module_version_id: "mvid", modset_status: "exact", modset_fingerprint: "modset", loaded_mod_ids: ["fixture-mod"] }, reads: [], whole_decision_admission: true, candidate_order_digest: "sha256-json-bound-action-id-order", score_count_matches_candidate_count: true, selected_index: true, successor_required: true },
  support: { game_versions: ["fixture-game"], game_commits: ["fixture-commit"], interaction_kinds: ["test"], action_verbs: ["end_turn"] },
  adapter_config: {},
  claims: { full_run: false, selector: false, catalog_filtered: false, creates_action_authority: false, creates_native_operands: false }
});

const action = (id: string): PlayerEnvironmentBoundAction => ({ bound_action_id: id, verb: "end_turn", interaction_id: "interaction", arguments: [], label: id });
const snapshot = (ids: string[], snapshotId = "snapshot-1", status: PlayerEnvironmentSnapshot["status"] = "interactive", sequence = 1): PlayerEnvironmentSnapshot => ({
  protocol_version: "1.0.0", schema: "sts2.player-environment/snapshot-1", snapshot_id: snapshotId, sequence, observed_at: "2026-08-25T00:00:00.000Z", status, persistent: null,
  interaction: { interaction_id: "interaction", kind: "test", stage: "ready", content_schema: "sts2.player-environment/surface/test-1", content: { surface: { kind: "test" }, context: { kind: "test" } }, capabilities: [] },
  referents: [], bound_actions: { schema: "sts2.player-environment/bound-actions-1", status: "complete", materialized_count: ids.length, total_count: ids.length, limit: ids.length || 1, ordering_semantics: "connector_order", actions: ids.map(action) }, reads: [],
  completeness: { status: "complete", visible_information: "test", interaction_discovery: "test", missing: [], hidden_by_policy: [] }, session: { runtime_instance_id: "runtime", environment_fingerprint: "environment" }, information_policy: { id: "test", scope: "test", includes_hidden_information: false, unknown_field_behavior: "reject" }
});
const bundle = (ids: string[], snapshotId?: string): DecisionBundle => ({ observation: snapshot(ids, snapshotId), reads: [] });

function decisionFor(current: DecisionBundle, selectedIndex: number | null = 0) {
  const digest = candidateOrderDigest(current.observation.bound_actions.actions);
  return { schema: "sts2.policy-runtime/decision-1" as const, decision_id: "decision-1", run_id: "run-1", manifest_id: "manifest-test", snapshot_id: current.observation.snapshot_id, candidate_digest: digest, candidate_count: current.observation.bound_actions.actions.length, scores: current.observation.bound_actions.actions.map((_action, index) => index), selected_index: selectedIndex, disposition: selectedIndex === null ? "abstain" as const : "admit" as const, issued_at: "2026-08-25T00:00:00.000Z" };
}

describe("strict policy contracts", () => {
  it("keeps mode and catalog out of the Policy Manifest", () => {
    const valid = manifest();
    expect(validatePolicyManifest(valid)).toEqual(valid);
    expect(() => validatePolicyManifest({ ...valid, mode: "auto" })).toThrow();
    expect(() => validatePolicyManifest({ ...valid, catalog: [] })).toThrow();
    expect(() => validatePolicyManifest({ ...valid, requirements: { ...valid.requirements, reads: ["run_deck", "run_deck"] } })).toThrow(/duplicates/);
    expect(() => validatePolicyManifest({ ...valid, requirements: { ...valid.requirements, environment: { ...valid.requirements.environment, connector_artifact_sha256: "invalid" } } })).toThrow(/SHA-256/);
    expect(() => validatePolicyManifest({ ...valid, requirements: { ...valid.requirements, environment: { ...valid.requirements.environment, loaded_mod_ids: ["fixture-mod", "fixture-mod"] } } })).toThrow(/duplicates/);
    expect(() => validatePolicyManifest({ ...valid, support: { ...valid.support, action_verbs: [] } })).toThrow(/must not be empty/);
    expect(() => validatePolicyManifest({ ...valid, support: { ...valid.support, interaction_kinds: ["test", "test"] } })).toThrow(/duplicates/);
  });

  it("uses a decision-only shape with no catalog or bound action", () => {
    const value = decisionFor(bundle(["a", "b"]), 1);
    const validated = validatePolicyDecision(value);
    expect(validated.selected_index).toBe(1);
    expect(validated).not.toHaveProperty("catalog");
    expect(validated).not.toHaveProperty("bound_action_id");
  });
});

describe("candidate order admission", () => {
  it("changes digest when the Connector order changes", () => {
    expect(candidateOrderDigest(["a", "b"])).not.toBe(candidateOrderDigest(["b", "a"]));
  });

  it.each([
    ["digest", (d: ReturnType<typeof decisionFor>) => ({ ...d, candidate_digest: "b".repeat(64) })],
    ["count", (d: ReturnType<typeof decisionFor>) => ({ ...d, candidate_count: 1 })],
    ["index", (d: ReturnType<typeof decisionFor>) => ({ ...d, selected_index: 2 })]
  ])("rejects %s drift", (_name, mutate) => {
    const current = bundle(["a", "b"]);
    const decision = mutate(decisionFor(current));
    expect(() => admitWholeDecision(decision, current, manifest(), "run-1")).toThrow();
  });

  it("rejects a reordered current bundle even when the adapter reuses the old digest", () => {
    const original = bundle(["a", "b"]);
    const reordered = bundle(["b", "a"]);
    expect(() => admitWholeDecision(decisionFor(original, 0), reordered, manifest(), "run-1")).toThrow(/digest/);
  });

  it("resolves the selected bound action only from the current bundle order", () => {
    const current = bundle(["a", "b"]);
    const admitted = admitWholeDecision(decisionFor(current, 1), current, manifest(), "run-1");
    expect(admitted.boundAction?.bound_action_id).toBe("b");
  });
});

describe("Connector Read materialization", () => {
  it("fetches only manifest-required advertised Reads and rejects unavailable requirements", async () => {
    const observed = {
      ...snapshot(["a"]),
      reads: [
        { read_id: "read-deck", kind: "run_deck", content_schema: "sts2.player-environment/read/run_deck-1", visibility_basis: "native_visible_fact", snapshot_bound: true as const, ordering_semantics: "native", hidden_by_policy: [] },
        { read_id: "read-piles", kind: "combat_piles", content_schema: "sts2.player-environment/read/combat_piles-1", visibility_basis: "native_visible_fact", snapshot_bound: true as const, ordering_semantics: "native", hidden_by_policy: [] }
      ]
    } satisfies PlayerEnvironmentSnapshot;
    const fetched: string[] = [];
    const client = {
      observe: async () => ({ raw: {}, data: observed }),
      read: async (readId: string, expectedSnapshotId: string) => {
        fetched.push(readId);
        const descriptor = observed.reads.find((read) => read.read_id === readId);
        if (!descriptor) throw new Error("unknown read");
        return { raw: {}, data: {
          protocol_version: "1.0.0", schema: "sts2.player-environment/read-1", read_id: readId,
          expected_snapshot_id: expectedSnapshotId, observed_snapshot_id: expectedSnapshotId,
          observed_at: "2026-08-25T00:00:00.000Z", kind: descriptor.kind,
          visibility_basis: descriptor.visibility_basis, ordering_semantics: descriptor.ordering_semantics,
          content_schema: descriptor.content_schema, content: {},
          completeness: { status: "complete", visible_information: "fixture", interaction_discovery: "fixture", missing: [], hidden_by_policy: [] },
          session: observed.session, information_policy: observed.information_policy
        } };
      }
    } as unknown as ConnectorAdapterClient;
    const connector = new ConnectorPolicyClient(client);
    const selected = await connector.observeBundle(["combat_piles"]);
    expect(fetched).toEqual(["read-piles"]);
    expect(selected.reads.map((read) => read.kind)).toEqual(["combat_piles"]);
    await expect(connector.observeBundle(["shop_catalog"])).rejects.toThrow("required_read_unavailable:shop_catalog");
  });

  it("submits with the latest renewed controller generation", async () => {
    let generation = 1;
    let submittedGeneration: number | null = null;
    const lease = () => ({
      runtime_instance_id: "runtime",
      controller: {
        controller_lease_id: "lease",
        controller_generation: generation,
        client_session_id: "client-session",
        expires_at: new Date(Date.now() + 1_000).toISOString()
      }
    });
    const client = {
      capabilities: async () => ({ raw: {}, data: {
        ...(await new FakeConnector(bundle(["a"])).capabilities()),
        control: { recommended_renewal_ms: 10_000 }
      } }),
      registerClient: async (input: { clientInstanceId: string }) => ({ raw: {}, data: {
        runtime_instance_id: "runtime",
        client: { client_session_id: "client-session", client_instance_id: input.clientInstanceId },
        controller: null
      } }),
      acquireController: async () => ({ raw: {}, data: lease() }),
      renewController: async () => { generation += 1; return { raw: {}, data: lease() }; },
      releaseController: async () => ({ raw: {}, data: { runtime_instance_id: "runtime", controller: null } }),
      submit: async (input: { controllerGeneration: number; requestId: string; boundActionId: string }) => {
        submittedGeneration = input.controllerGeneration;
        return { raw: {}, data: {
          protocol_version: "1.0.0",
          schema: "sts2.player-environment/receipt-1",
          request_id: input.requestId,
          delivery: "not_delivered",
          action: { bound_action_id: input.boundActionId, verb: "end_turn", arguments: [] },
          retry: { allowed: false, reason: "fixture" },
          successor: null
        } };
      }
    } as unknown as ConnectorAdapterClient;
    const connector = new ConnectorPolicyClient(client);

    await connector.acquireController();
    await connector.submit({ requestId: "request", expectedSnapshotId: "snapshot", boundActionId: "a" });

    expect(generation).toBe(2);
    expect(submittedGeneration).toBe(2);
    await connector.releaseController();
  });
});

describe("decision-only process boundary", () => {
  it("allows a bounded model startup window without weakening attestation", () => {
    expect(DEFAULT_POLICY_ADAPTER_STARTUP_TIMEOUT_MS).toBe(30_000);
  });

  it("drains bounded child diagnostics without blocking a decision", async () => {
    const script = [
      "process.stderr.write('x'.repeat(100000));",
      `process.stdout.write(JSON.stringify({schema:'sts2.policy-runtime/policy-port-1',message_type:'ready',adapter:${JSON.stringify(manifest().adapter)}})+'\\n');`,
      "const readline=require('node:readline').createInterface({input:process.stdin});",
      "readline.on('line',(line)=>{const request=JSON.parse(line);process.stdout.write(JSON.stringify({schema:request.schema,message_type:'decision',request_id:request.request_id,output:{candidate_digest:request.input.candidate_digest,scores:Array(request.input.candidate_count).fill(1),selected_index:0}})+'\\n');});"
    ].join("");
    const port = NdjsonPolicyPort.spawn(process.execPath, ["-e", script]);
    try {
      await expect(port.ready()).resolves.toEqual(manifest().adapter);
      const current = bundle(["a"]);
      const digest = candidateOrderDigest(current.observation.bound_actions.actions);
      await expect(port.decide({ run_id: "run-port", manifest: manifest(), bundle: current, candidate_digest: digest, candidate_count: 1 })).resolves.toEqual({ candidate_digest: digest, scores: [1], selected_index: 0 });
    } finally { port.close(); }
  });

  it("isolates a cancelled NDJSON decision from a later request", async () => {
    const script = [
      `process.stdout.write(JSON.stringify({schema:'sts2.policy-runtime/policy-port-1',message_type:'ready',adapter:${JSON.stringify(manifest().adapter)}})+'\\n');`,
      "const readline=require('node:readline').createInterface({input:process.stdin});",
      "let cancelledRequest;",
      "const emit=(request,scores,selected_index)=>process.stdout.write(JSON.stringify({schema:request.schema,message_type:'decision',request_id:request.request_id,output:{candidate_digest:request.input.candidate_digest,scores,selected_index}})+'\\n');",
      "readline.on('line',(line)=>{const request=JSON.parse(line);if(!cancelledRequest){cancelledRequest=request;return;}emit(cancelledRequest,[11],0);setImmediate(()=>emit(request,[22,23],1));});"
    ].join("");
    const port = NdjsonPolicyPort.spawn(process.execPath, ["-e", script]);
    const current = bundle(["a"]);
    const next = bundle(["a", "b"], "snapshot-next");
    const digest = candidateOrderDigest(current.observation.bound_actions.actions);
    const nextDigest = candidateOrderDigest(next.observation.bound_actions.actions);
    try {
      await port.ready();
      const controller = new AbortController();
      const cancelled = port.decide({ run_id: "run-cancelled", manifest: manifest(), bundle: current, candidate_digest: digest, candidate_count: 1 }, controller.signal);
      controller.abort();
      await expect(cancelled).rejects.toThrow("cancelled");
      await expect(port.decide({ run_id: "run-next", manifest: manifest(), bundle: next, candidate_digest: nextDigest, candidate_count: 2 })).resolves.toEqual({ candidate_digest: nextDigest, scores: [22, 23], selected_index: 1 });
    } finally { port.close(); }
  });

  it("fails closed on an unrelated NDJSON response id", async () => {
    const script = [
      `process.stdout.write(JSON.stringify({schema:'sts2.policy-runtime/policy-port-1',message_type:'ready',adapter:${JSON.stringify(manifest().adapter)}})+'\\n');`,
      "const readline=require('node:readline').createInterface({input:process.stdin});",
      "readline.once('line',(line)=>{const request=JSON.parse(line);process.stdout.write(JSON.stringify({schema:request.schema,message_type:'decision',request_id:'unrelated-request-id',output:{candidate_digest:request.input.candidate_digest,scores:[1],selected_index:0}})+'\\n');});"
    ].join("");
    const port = NdjsonPolicyPort.spawn(process.execPath, ["-e", script]);
    try {
      await port.ready();
      const current = bundle(["a"]);
      await expect(port.decide({ run_id: "run-unknown", manifest: manifest(), bundle: current, candidate_digest: candidateOrderDigest(current.observation.bound_actions.actions), candidate_count: 1 })).rejects.toThrow("unknown request id");
    } finally { port.close(); }
  });

  it("fails closed when the policy child exits during an active decision", async () => {
    const script = [
      `process.stdout.write(JSON.stringify({schema:'sts2.policy-runtime/policy-port-1',message_type:'ready',adapter:${JSON.stringify(manifest().adapter)}})+'\\n');`,
      "const readline=require('node:readline').createInterface({input:process.stdin});",
      "readline.once('line',()=>setTimeout(()=>process.exit(17),5));"
    ].join("");
    const port = NdjsonPolicyPort.spawn(process.execPath, ["-e", script]);
    try {
      await port.ready();
      const connector = new FakeConnector(bundle(["a"]));
      const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-child-exit", policy: (input, signal) => port.decide(input, signal) });
      const result = await runtime.tick();
      expect(result.type).toBe("not_admitted");
      expect(runtime.status().mode).toBe("human");
      expect(runtime.status().errors.at(-1)).toContain("policy child port closed");
      expect(connector.submitCount).toBe(0);
    } finally { port.close(); }
  });

  it("rejects adapter startup identity drift before any decision", async () => {
    const drifted = { ...manifest().adapter, code_sha256: "d".repeat(64) };
    const script = `process.stdout.write(JSON.stringify({schema:'sts2.policy-runtime/policy-port-1',message_type:'ready',adapter:${JSON.stringify(drifted)}})+'\\n');setInterval(()=>{},1000);`;
    const port = NdjsonPolicyPort.spawn(process.execPath, ["-e", script]);
    try {
      await expect(port.attest(manifest().adapter)).rejects.toThrow(/differs from Policy Manifest/);
    } finally { port.close(); }
  });
});

class FakeConnector implements PolicyConnector {
  acquireCount = 0;
  releaseCount = 0;
  submitCount = 0;
  observeCount = 0;
  stale = true;
  nextSequence = 2;
  observationQueue: DecisionBundle[] = [];
  requiredReadRequests: string[][] = [];
  receiptRequestIdOverride?: string;
  receiptBoundActionIdOverride?: string;
  releaseGate?: Promise<void>;
  successorActionIds?: string[];
  constructor(public current: DecisionBundle, private readonly delivery: PlayerEnvironmentReceipt["delivery"] = "delivered", private readonly submitFailure?: Error, private readonly releaseFailure?: Error) {}
  async capabilities() {
    return {
      protocol_version: "1.0.0", snapshot_schema: "sts2.player-environment/snapshot-1", action_schema: "sts2.player-environment/action-1", receipt_schema: "sts2.player-environment/receipt-1", control_schema: "sts2.player-environment/control-1", status: "ready",
      host: { id: "fixture", name: "fixture", version: "1", runtime_instance_id: "runtime", host_kind: "test", implementation: { source_revision: "source", module_version_id: "mvid", artifact_sha256: "b".repeat(64) } },
      game: { version: "fixture-game", commit: "fixture-commit", branch: null, main_assembly_hash: null, compatibility: { status: "exact", observation_allowed: true, detail: "fixture" }, modset: { status: "exact", fingerprint: "modset", scope: "fixture", loaded_mod_ids: ["fixture-mod"], detail: "fixture" } },
      environment_fingerprint: "environment", verbs: ["end_turn"], snapshot_bound: true, single_controller: true, execution_available: true, control: { recommended_renewal_ms: 1000 }, evidence_profiles: [], non_claims: []
    } as Awaited<ReturnType<PolicyConnector["capabilities"]>>;
  }
  async observeBundle(requiredReadKinds: readonly string[]) { this.observeCount += 1; this.requiredReadRequests.push([...requiredReadKinds]); if (this.stale) { this.stale = false; throw Object.assign(new Error("stale_state"), { code: "stale_state" }); } return this.observationQueue.shift() ?? this.current; }
  async acquireController() { this.acquireCount += 1; }
  async releaseController() { this.releaseCount += 1; if (this.releaseGate) await this.releaseGate; if (this.releaseFailure) throw this.releaseFailure; }
  async submit(input: { requestId: string; expectedSnapshotId: string; boundActionId: string }) {
    this.submitCount += 1;
    if (this.submitFailure) throw this.submitFailure;
    const successor = this.delivery === "delivered" ? snapshot(this.successorActionIds ?? [`next-${this.nextSequence}`], `snapshot-${this.nextSequence}`, "interactive", this.nextSequence) : null;
    if (successor) { this.current = { observation: successor, reads: [] }; this.nextSequence += 1; }
    return { protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1", request_id: this.receiptRequestIdOverride ?? input.requestId, delivery: this.delivery, action: { bound_action_id: this.receiptBoundActionIdOverride ?? input.boundActionId, verb: "end_turn", arguments: [] }, retry: { allowed: false, reason: "test" }, successor } as PlayerEnvironmentReceipt;
  }
}

function deferred<T = void>() {
  let resolve!: (value?: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = value => done(value as T);
    reject = fail;
  });
  return { promise, resolve, reject };
}

describe("cross-interface Runtime control preconditions", () => {
  const makeRuntime = (connector: PolicyConnector = new FakeConnector(bundle(["a"]))) =>
    new PolicyRuntime({ manifest: manifest(), connector, runId: "run-bound", sleep: async () => {},
      policy: input => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
  const binding = { gameInstanceId: "runtime", recoveryEpoch: 0 };

  it("reads actual Connector without admitting environment, observing or acquiring control", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const capabilities = vi.spyOn(connector, "capabilities");
    const runtime = makeRuntime(connector);
    const before = runtime.status();
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0 });
    try {
      const response = await fetch(`${service.address}/v2/environment`);
      expect(response.status).toBe(200);
      const value = await response.json();
      const schema = JSON.parse(await readFile(new URL("../../../contracts/policy-runtime/environment.schema.json", import.meta.url), "utf8"));
      expect(value).toEqual({ schema: schema.$id, run_id: "run-bound", runtime_instance_id: "runtime", recovery_epoch: 0 });
      expect(Object.keys(value).sort()).toEqual(schema.required.sort());
      expect(capabilities).toHaveBeenCalledExactlyOnceWith({ fresh: true });
      expect(runtime.status()).toEqual(before);
      expect(decodePolicyRuntimeStatus(runtime.status())).toEqual(before);
      expect(connector.observeCount + connector.acquireCount + connector.submitCount).toBe(0);
      for (const headers of [{ origin: "https://foreign.invalid" }, { host: "foreign.invalid" }]) {
        const code = await new Promise<number>((resolve, reject) => {
          const request = httpRequest(`${service.address}/v2/environment`, { headers }, response => { response.resume(); resolve(response.statusCode!); });
          request.once("error", reject); request.end();
        });
        expect(code).toBe(403);
      }
      expect(capabilities).toHaveBeenCalledTimes(1);
    } finally { await service.close(); }
  });

  it("bypasses capability cache without replacing admitted identity", async () => {
    const original = await new FakeConnector(bundle(["a"])).capabilities();
    let identity = "runtime";
    const wire = { capabilities: vi.fn(async () => ({ data: { ...original, host: { ...original.host, runtime_instance_id: identity } } })) };
    const connector = new ConnectorPolicyClient(wire as unknown as ConnectorAdapterClient);
    expect((await connector.capabilities()).host.runtime_instance_id).toBe("runtime");
    identity = "replacement";
    expect((await connector.capabilities({ fresh: true })).host.runtime_instance_id).toBe("replacement");
    expect((await connector.capabilities()).host.runtime_instance_id).toBe("runtime");
    expect(wire.capabilities).toHaveBeenCalledTimes(2);
  });

  it("rejects another game before mode/tick, including after environment admission", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = makeRuntime(connector);
    const before = runtime.status();
    await expect(runtime.setMode("auto", { ...binding, gameInstanceId: "other" })).rejects.toMatchObject({ code: "runtime_game_mismatch", httpStatus: 409 });
    await expect(runtime.tick({ ...binding, gameInstanceId: "other" })).rejects.toMatchObject({ code: "runtime_game_mismatch" });
    expect(runtime.status()).toEqual(before);
    await runtime.setMode("shadow", binding);
    expect((await runtime.tick(binding)).type).toBe("shadow");
    const admitted = runtime.status();
    const caps = await connector.capabilities();
    vi.spyOn(connector, "capabilities").mockResolvedValue({ ...caps, host: { ...caps.host, runtime_instance_id: "replacement" } });
    await expect(runtime.readEnvironment()).rejects.toMatchObject({ code: "runtime_game_mismatch" });
    await expect(runtime.setMode("auto", { ...binding, gameInstanceId: "replacement" })).rejects.toMatchObject({ code: "runtime_game_mismatch" });
    expect(runtime.status()).toEqual(admitted);
    expect(connector.acquireCount + connector.submitCount).toBe(0);
  });

  it("invalidates delayed Auto and One-Step tick after another client's legacy Human", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = makeRuntime(connector);
    await runtime.setMode("one_step", binding);
    await runtime.setMode("human");
    expect((await runtime.readEnvironment()).recovery_epoch).toBe(1);
    await expect(runtime.setMode("auto", binding)).rejects.toMatchObject({ code: "runtime_recovery_epoch_mismatch" });
    await expect(runtime.tick(binding)).rejects.toMatchObject({ code: "runtime_recovery_epoch_mismatch" });
    expect(runtime.status().mode).toBe("human");
    expect(connector.observeCount + connector.acquireCount + connector.submitCount).toBe(0);
    const current = { ...binding, recoveryEpoch: 1 };
    await runtime.setMode("one_step", current);
    expect((await runtime.tick(current)).type).toBe("delivered");
    expect(connector.submitCount).toBe(1);
  });

  it.each(["human", "stop"] as const)("invalidates waiting intent immediately when %s enters owner", async recovery => {
    const connector = new FakeConnector(bundle(["a"]));
    const actual = await connector.capabilities();
    const entered = deferred(), finish = deferred();
    vi.spyOn(connector, "capabilities").mockImplementationOnce(async () => { entered.resolve(); await finish.promise; return actual; });
    const runtime = makeRuntime(connector);
    const pending = runtime.setMode("auto", binding);
    const rejected = expect(pending).rejects.toMatchObject({ code: "runtime_recovery_epoch_mismatch" });
    await entered.promise;
    const recovered = recovery === "human" ? runtime.setMode("human") : runtime.stop();
    finish.resolve(); await rejected; await recovered;
    expect(runtime.status().mode).toBe("human");
    expect(connector.acquireCount + connector.submitCount).toBe(0);
  });

  it("does not renew a pending environment read across Human recovery", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const actual = await connector.capabilities();
    const entered = deferred(), finish = deferred();
    vi.spyOn(connector, "capabilities").mockImplementationOnce(async () => { entered.resolve(); await finish.promise; return actual; });
    const runtime = makeRuntime(connector);
    const rejected = expect(runtime.readEnvironment()).rejects.toMatchObject({ code: "runtime_recovery_epoch_mismatch" });
    await entered.promise; await runtime.setMode("human");
    finish.resolve(); await rejected;
    expect((await runtime.readEnvironment()).recovery_epoch).toBe(1);
  });

  it("rejects queued commands and cancels scoring after Human without native delivery", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const entered = deferred(), finish = deferred();
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, sleep: async () => {}, policy: async input => {
      entered.resolve(); await finish.promise;
      return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 };
    } });
    await runtime.setMode("one_step", binding);
    const tick = runtime.tick(binding);
    await entered.promise;
    const rejected = expect(runtime.setMode("auto", binding)).rejects.toMatchObject({ code: "runtime_recovery_epoch_mismatch" });
    const human = runtime.setMode("human");
    finish.resolve();
    expect((await tick).type).toBe("not_admitted");
    await rejected; await human;
    expect(runtime.status().mode).toBe("human");
    expect(connector.submitCount).toBe(0);
  });

  it("bounds epoch exhaustion without blocking Human/Stop", async () => {
    const runtime = makeRuntime();
    (runtime as unknown as { recoveryEpoch: number }).recoveryEpoch = Number.MAX_SAFE_INTEGER;
    await runtime.setMode("human");
    await expect(runtime.readEnvironment()).rejects.toMatchObject({ code: "runtime_recovery_epoch_mismatch" });
    await expect(runtime.setMode("auto", { ...binding, recoveryEpoch: Number.MAX_SAFE_INTEGER })).rejects.toMatchObject({ code: "runtime_recovery_epoch_mismatch" });
    await expect(runtime.stop()).resolves.toMatchObject({ mode: "human", lifecycle: "stopped" });
  });

  it("preserves completed multi-tick results when a later recovery fence rejects", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = makeRuntime(connector);
    await runtime.setMode("auto", binding);
    const tick = runtime.tick.bind(runtime);
    let calls = 0;
    vi.spyOn(runtime, "tick").mockImplementation(async expected => {
      if (++calls === 2) {
        await runtime.setMode("human");
        // Another intentional new command may re-enter Auto. It cannot revive
        // the original client's multi-tick request carrying the old epoch.
        await runtime.setMode("auto", { ...binding, recoveryEpoch: 1 });
      }
      return tick(expected);
    });
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0, maxAutoTicks: 2 });
    try {
      const response = await fetch(`${service.address}/v2/tick`, { method: "POST", headers: {
        "content-type": "application/json", "x-sts2-policy-run-id": "run-bound",
        "x-sts2-game-instance-id": "runtime", "x-sts2-recovery-epoch": "0"
      }, body: JSON.stringify({ max_ticks: 2 }) });
      expect(response.status).toBe(200);
      const value = await response.json();
      expect(value.schema).toBe("sts2.policy-runtime/http-2/tick-1");
      expect(value.results.map((result: { type: string }) => result.type)).toEqual(["delivered", "not_admitted"]);
      expect(value.results[1].reason).toBe("runtime_recovery_epoch_mismatch");
      expect(connector.submitCount).toBe(1);
    } finally { await service.close(); }
  });

  it("does not let a matching control fence retry unknown native delivery", async () => {
    const connector = new FakeConnector(bundle(["a"]), "unknown");
    const runtime = makeRuntime(connector);
    await runtime.setMode("one_step", binding);
    expect((await runtime.tick(binding)).type).toBe("unknown");
    expect((await runtime.tick(binding)).type).toBe("not_admitted");
    await expect(runtime.setMode("auto", binding)).rejects.toThrow("runtime is tainted");
    expect(connector.submitCount).toBe(1);
    await runtime.setMode("human");
    expect(runtime.status().tainted).toBe(true);
    expect(connector.submitCount).toBe(1);
  });

  it("validates HTTP header syntax but allows recovery despite malformed new headers", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = makeRuntime(connector);
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0 });
    const send = (route: string, body: unknown, extra: Record<string, string | string[]>) => new Promise<{ status: number; data: { error?: string } }>((resolve, reject) => {
      const request = httpRequest(`${service.address}/v2/${route}`, { method: "POST", headers: { "content-type": "application/json", "x-sts2-policy-run-id": "run-bound", ...extra } }, response => {
        let text = ""; response.setEncoding("utf8"); response.on("data", (chunk: string) => { text += chunk; });
        response.once("end", () => resolve({ status: response.statusCode!, data: JSON.parse(text) }));
      });
      request.once("error", reject); request.end(JSON.stringify(body));
    });
    try {
      const invalid: Record<string, string | string[]>[] = [
        { "x-sts2-game-instance-id": "" }, { "x-sts2-game-instance-id": ["runtime", "runtime"] },
        ...["", "-1", "1.2", "01", "1e0", "9007199254740992", ["0", "0"]].map(epoch => ({ "x-sts2-recovery-epoch": epoch }))
      ];
      for (const extra of invalid) {
        expect((await send("mode", { mode: "auto" }, extra)).status).toBe(428);
        expect((await send("tick", { max_ticks: 1 }, extra)).status).toBe(428);
      }
      const headers = { "x-sts2-game-instance-id": "runtime", "x-sts2-recovery-epoch": "0" };
      expect((await send("mode", { mode: "one_step" }, headers)).status).toBe(200);
      expect((await send("mode", { mode: "human" }, { "x-sts2-game-instance-id": "other", "x-sts2-recovery-epoch": "bad" })).status).toBe(200);
      expect(await send("mode", { mode: "auto" }, headers)).toMatchObject({ status: 409, data: { error: "runtime_recovery_epoch_mismatch" } });
      expect(await send("tick", { max_ticks: 1 }, headers)).toMatchObject({ status: 409, data: { error: "runtime_recovery_epoch_mismatch" } });
      expect(connector.observeCount + connector.acquireCount + connector.submitCount).toBe(0);
      vi.spyOn(connector, "capabilities").mockRejectedValue(new Error("sensitive implementation detail"));
      const unavailable = await fetch(`${service.address}/v2/environment`);
      expect(unavailable.status).toBe(503);
      expect(await unavailable.json()).toEqual({ schema: "sts2.policy-runtime/http-2", error: "runtime_environment_unavailable" });
      expect((await send("stop", {}, { "x-sts2-game-instance-id": "other", "x-sts2-recovery-epoch": "bad" })).status).toBe(200);
      expect(runtime.status().lifecycle).toBe("stopped");
    } finally { await service.close(); }
  });
});

describe("runtime integration fake", () => {
  const environmentDriftCases: Array<[string, string, (value: PolicyManifest) => void]> = [
    ["host_kind", "environment_host_kind_drift", (value) => { value.requirements.environment.host_kind = "headless"; }],
    ["connector version", "environment_connector_version_drift", (value) => { value.requirements.environment.connector_version = "different"; }],
    ["connector source", "environment_connector_source_revision_drift", (value) => { value.requirements.environment.connector_source_revision = "different"; }],
    ["connector artifact SHA", "environment_connector_artifact_sha256_drift", (value) => { value.requirements.environment.connector_artifact_sha256 = "c".repeat(64); }],
    ["connector MVID", "environment_connector_module_version_id_drift", (value) => { value.requirements.environment.connector_module_version_id = "different"; }],
    ["modset status", "environment_modset_status_drift", (value) => { value.requirements.environment.modset_status = "different"; }],
    ["modset fingerprint", "environment_modset_fingerprint_drift", (value) => { value.requirements.environment.modset_fingerprint = "different"; }],
    ["loaded mod IDs", "environment_loaded_mod_ids_drift", (value) => { value.requirements.environment.loaded_mod_ids = ["different-mod"]; }]
  ];

  it.each(environmentDriftCases)("fails closed before observe and scoring on %s drift", async (_field, reason, mutate) => {
    const connector = new FakeConnector(bundle(["a"]));
    const pinned = manifest();
    mutate(pinned);
    let scored = false;
    const runtime = new PolicyRuntime({ manifest: pinned, connector, mode: "auto", runId: "run-1", policy: () => { scored = true; return { candidate_digest: "a".repeat(64), scores: [1], selected_index: 0 }; } });
    const result = await runtime.tick();
    expect(result.type).toBe("not_admitted");
    if (result.type === "not_admitted") expect(result.reason).toBe(reason);
    expect(connector.observeCount).toBe(0);
    expect(scored).toBe(false);
    expect(connector.acquireCount).toBe(0);
    expect(connector.submitCount).toBe(0);
  });

  it("publishes host kind and loaded mod IDs in the admitted environment status", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "shadow", runId: "run-identity", staleRefresh: { maxAttempts: 2, baseBackoffMs: 0 }, policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    expect((await runtime.tick()).type).toBe("shadow");
    expect(runtime.status().environment).toMatchObject({ host_kind: "test", loaded_mod_ids: ["fixture-mod"] });
  });

  it("requests only the Reads declared by the Policy Manifest", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const withReads = manifest();
    withReads.requirements.reads = ["combat_piles"];
    const runtime = new PolicyRuntime({ manifest: withReads, connector, mode: "shadow", runId: "run-1", staleRefresh: { maxAttempts: 2, baseBackoffMs: 0 }, policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    expect((await runtime.tick()).type).toBe("shadow");
    expect(connector.requiredReadRequests).toEqual([["combat_piles"], ["combat_piles"]]);
  });

  it("scores a Shadow snapshot once and waits for a successor snapshot", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    let scoreCount = 0;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "shadow", runId: "run-1", staleRefresh: { maxAttempts: 2, baseBackoffMs: 0 }, policy: (input) => { scoreCount += 1; return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }; } });
    expect((await runtime.tick()).type).toBe("shadow");
    const duplicate = await runtime.tick();
    expect(duplicate.type).toBe("not_admitted");
    if (duplicate.type === "not_admitted") expect(duplicate.reason).toBe("snapshot_already_scored");
    expect(scoreCount).toBe(1);
    connector.current = bundle(["b"], "snapshot-2");
    expect((await runtime.tick()).type).toBe("shadow");
    expect(scoreCount).toBe(2);
  });

  it("hands Auto back to Human when the policy abstains", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-1", staleRefresh: { maxAttempts: 2, baseBackoffMs: 0 }, policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: null }) });
    expect((await runtime.tick()).type).toBe("not_executed");
    expect(runtime.status().mode).toBe("human");
    expect(connector.submitCount).toBe(0);
  });

  it("fails closed before scoring when the exact game identity is unsupported", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const unsupported = manifest();
    unsupported.support.game_commits = ["different-commit"];
    let scored = false;
    const runtime = new PolicyRuntime({ manifest: unsupported, connector, mode: "auto", runId: "run-1", policy: () => { scored = true; return { candidate_digest: "a".repeat(64), scores: [1], selected_index: 0 }; } });
    const result = await runtime.tick();
    expect(result.type).toBe("not_admitted");
    if (result.type === "not_admitted") expect(result.reason).toBe("game_commit_unsupported");
    expect(scored).toBe(false);
    expect(connector.acquireCount).toBe(0);
    expect(connector.submitCount).toBe(0);
  });

  it("refreshes a whole stale bundle and submits only the current indexed action", async () => {
    const connector = new FakeConnector(bundle(["a", "b"]));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "one_step", runId: "run-1", staleRefresh: { maxAttempts: 2, baseBackoffMs: 0 }, sleep: async () => {}, policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [0, 1], selected_index: 1 }) });
    const result = await runtime.tick();
    expect(result.type).toBe("delivered");
    if (result.type === "delivered") expect(result.bound_action.bound_action_id).toBe("b");
    expect(connector.submitCount).toBe(1);
    expect(connector.acquireCount).toBe(1);
    expect(connector.releaseCount).toBe(1);
  });

  it("taints and releases after unknown delivery without retry", async () => {
    const connector = new FakeConnector(bundle(["a"]), "unknown");
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-1", policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    const result = await runtime.tick();
    expect(result.type).toBe("unknown");
    expect(connector.submitCount).toBe(1);
    expect(connector.releaseCount).toBe(1);
    expect(runtime.status().tainted).toBe(true);
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(connector.submitCount).toBe(1);
    await expect(runtime.setMode("auto")).rejects.toThrow(/tainted/);
  });

  it("treats a mismatched Receipt as unknown and never retries", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    connector.receiptRequestIdOverride = "different-request";
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-receipt-drift", policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });

    const result = await runtime.tick();

    expect(result.type).toBe("unknown");
    expect(runtime.status().tainted).toBe(true);
    expect(runtime.status().taint_reason).toBe("receipt_correlation_failed");
    expect(connector.submitCount).toBe(1);
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(connector.submitCount).toBe(1);
  });

  it("does not acquire on auto mode and applies support to the whole catalog", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "human", runId: "run-1", policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    await runtime.setMode("auto");
    expect(connector.acquireCount).toBe(0);
    connector.current = { observation: { ...snapshot(["a"]), interaction: { ...snapshot(["a"]).interaction, kind: "unsupported" } }, reads: [] };
    const result = await runtime.tick();
    expect(result.type).toBe("not_admitted");
    expect(runtime.status().mode).toBe("human");
    expect(connector.acquireCount).toBe(0);
    expect(connector.releaseCount).toBe(0);
  });

  it("fails closed on policy error before any controller mutation", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-1", policy: () => { throw new Error("adapter offline"); } });
    const result = await runtime.tick();
    expect(result.type).toBe("not_admitted");
    expect(connector.acquireCount).toBe(0);
    expect(connector.submitCount).toBe(0);
    expect(runtime.status().mode).toBe("human");
    expect(runtime.status().errors.at(-1)).toContain("policy_failed");
  });

  it("cancels an active scored tick when Human mode is requested before submit", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    let resolvePolicy: ((decision: { candidate_digest: string; scores: number[]; selected_index: number }) => void) | undefined;
    let resolveStarted: (() => void) | undefined;
    let expectedDigest = "";
    const policyStarted = new Promise<void>((resolve) => { resolveStarted = resolve; });
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-cancel", policy: (input) => {
      expectedDigest = input.candidate_digest;
      resolveStarted!();
      return new Promise((resolveDecision) => { resolvePolicy = resolveDecision; });
    } });
    const tick = runtime.tick();
    await policyStarted;
    const human = runtime.setMode("human");
    resolvePolicy!({ candidate_digest: expectedDigest, scores: [1], selected_index: 0 });

    const result = await tick;
    await human;

    expect(result.type).toBe("not_admitted");
    if (result.type === "not_admitted") expect(result.reason).toBe("runtime_recovery_epoch_mismatch");
    expect(runtime.status().mode).toBe("human");
    expect(connector.acquireCount).toBe(0);
    expect(connector.submitCount).toBe(0);
  });

  it("releases a held controller while the next policy decision is still pending", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const entered = deferred<void>();
    const finish = deferred<{ candidate_digest: string; scores: number[]; selected_index: number | null }>();
    let calls = 0;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-slow-human", sleep: async () => {}, policy: async input => {
      if (calls++ === 0) return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 };
      entered.resolve();
      return finish.promise;
    } });

    expect((await runtime.tick()).type).toBe("delivered");
    const slowTick = runtime.tick();
    await entered.promise;
    const human = runtime.setMode("human");
    try {
      const outcome = await Promise.race([
        human.then(() => "recovered" as const),
        new Promise<"timeout">(resolve => setTimeout(() => resolve("timeout"), 100))
      ]);
      expect(outcome).toBe("recovered");
      expect(connector.releaseCount).toBe(1);
      expect(runtime.status()).toMatchObject({ mode: "human", controller: "released" });
    } finally {
      finish.resolve({ candidate_digest: candidateOrderDigest(["a"]), scores: [1], selected_index: 0 });
      await slowTick;
      await human;
    }
    expect(connector.submitCount).toBe(1);
  });

  it("stops and releases a held controller during unresolved policy without reviving a late rejection", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const entered = deferred<void>();
    const finish = deferred<{ candidate_digest: string; scores: number[]; selected_index: number | null }>();
    let calls = 0;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-slow-stop", sleep: async () => {}, policy: async input => {
      if (calls++ === 0) return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 };
      entered.resolve();
      return finish.promise;
    } });

    expect((await runtime.tick()).type).toBe("delivered");
    const slowTick = runtime.tick();
    await entered.promise;
    const stopped = runtime.stop();
    try {
      const outcome = await Promise.race([
        stopped.then(() => "stopped" as const),
        new Promise<"timeout">(resolve => setTimeout(() => resolve("timeout"), 100))
      ]);
      expect(outcome).toBe("stopped");
      expect(connector.releaseCount).toBe(1);
      expect(runtime.status()).toMatchObject({ lifecycle: "stopped", mode: "human", controller: "released" });
    } finally {
      finish.reject(new Error("late model failure"));
      await slowTick;
      await stopped;
    }
    expect(connector.submitCount).toBe(1);
  });

  it("does not claim a release before a slow policy has acquired a controller", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const entered = deferred<void>();
    const finish = deferred<{ candidate_digest: string; scores: number[]; selected_index: number | null }>();
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-slow-preacquire", sleep: async () => {}, policy: async input => {
      entered.resolve();
      return finish.promise;
    } });
    const slowTick = runtime.tick();
    await entered.promise;
    const human = runtime.setMode("human");
    try {
      const outcome = await Promise.race([
        human.then(() => "recovered" as const),
        new Promise<"timeout">(resolve => setTimeout(() => resolve("timeout"), 100))
      ]);
      expect(outcome).toBe("recovered");
      expect(connector.acquireCount).toBe(0);
      expect(connector.releaseCount).toBe(0);
      expect(runtime.status()).toMatchObject({ mode: "human", controller: "released" });
    } finally {
      finish.resolve({ candidate_digest: candidateOrderDigest(["a"]), scores: [1], selected_index: 0 });
      await slowTick;
      await human;
    }
  });

  it("recovers a slow tick through two loopback HTTP clients", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const entered = deferred<void>();
    const finish = deferred<{ candidate_digest: string; scores: number[]; selected_index: number | null }>();
    let calls = 0;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "human", runId: "run-http-recovery", sleep: async () => {}, policy: async input => {
      if (calls++ === 0) return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 };
      entered.resolve();
      return finish.promise;
    } });
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0, deferAutoDrive: true });
    const post = async (route: string, body: unknown) => {
      const response = await fetch(`${service.address}/v2/${route}`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-sts2-policy-run-id": runtime.status().run_id },
        body: JSON.stringify(body)
      });
      return { status: response.status, value: await response.json() as Record<string, unknown> };
    };
    try {
      expect((await post("mode", { mode: "auto" })).status).toBe(200);
      expect((await post("tick", { max_ticks: 1 })).status).toBe(200);

      const slowTick = post("tick", { max_ticks: 1 });
      await entered.promise;
      const recovery = post("mode", { mode: "human" });
      const recovered = await Promise.race([
        recovery,
        new Promise<"timeout">(resolve => setTimeout(() => resolve("timeout"), 100))
      ]);
      expect(recovered).not.toBe("timeout");
      if (recovered !== "timeout") expect(recovered.status).toBe(200);
      expect(runtime.status()).toMatchObject({ mode: "human", controller: "released" });

      finish.resolve({ candidate_digest: candidateOrderDigest(["a"]), scores: [1], selected_index: 0 });
      const result = await slowTick;
      expect(result.status).toBe(200);
      const results = result.value.results as Array<{ type: string; reason?: string }>;
      expect(results[0]?.type).toBe("not_admitted");
      expect(results[0]?.reason).toBe("runtime_recovery_epoch_mismatch");
      expect(connector.submitCount).toBe(1);
    } finally {
      finish.resolve({ candidate_digest: candidateOrderDigest(["a"]), scores: [1], selected_index: 0 });
      await service.close();
    }
  });

  it("keeps the controller held when a recovery release fails", async () => {
    const connector = new FakeConnector(bundle(["a"]), "delivered", undefined, new Error("release unavailable"));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-release-failure", policy: input => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    expect((await runtime.tick()).type).toBe("delivered");
    await expect(runtime.setMode("human")).rejects.toThrow("release unavailable");
    expect(runtime.status()).toMatchObject({ mode: "auto", controller: "held" });
    expect(runtime.status().errors.at(-1)).toContain("controller_release_failed");
    expect(connector.releaseCount).toBe(1);
  });

  it("waits for an in-flight submit and preserves delivered receipt when recovery follows it", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    connector.stale = false;
    const submitEntered = deferred<void>();
    const submitFinish = deferred<PlayerEnvironmentReceipt>();
    connector.submit = async input => {
      connector.submitCount += 1;
      submitEntered.resolve();
      return submitFinish.promise.then(receipt => ({ ...receipt, request_id: input.requestId }));
    };
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-submit-recovery", policy: input => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    const tick = runtime.tick();
    await submitEntered.promise;
    const human = runtime.setMode("human");
    const blocked = await Promise.race([
      human.then(() => "released" as const),
      new Promise<"waiting">(resolve => setTimeout(() => resolve("waiting"), 50))
    ]);
    expect(blocked).toBe("waiting");
    const successor = snapshot(["next"], "snapshot-submit-successor", "interactive", 2);
    connector.current = { observation: successor, reads: [] };
    submitFinish.resolve({ protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1", request_id: "placeholder", delivery: "delivered", action: { bound_action_id: "a", verb: "end_turn", arguments: [] }, retry: { allowed: false, reason: "test" }, successor });
    const result = await tick;
    await human;
    expect(result.type).toBe("not_admitted");
    if (result.type === "not_admitted") expect(result.reason).toBe("recovery_requested_after_delivery");
    expect(runtime.status()).toMatchObject({ mode: "human", controller: "released" });
    expect(runtime.status().last_receipt).toMatchObject({ delivery: "delivered", successor_snapshot_id: "snapshot-submit-successor" });
    expect(connector.submitCount).toBe(1);
    expect(connector.releaseCount).toBe(1);
  });

  it("fails closed and returns Human when a policy process does not answer", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-timeout", policyTimeoutMs: 5, policy: () => new Promise(() => {}) });
    const result = await runtime.tick();
    expect(result.type).toBe("not_admitted");
    expect(runtime.status().mode).toBe("human");
    expect(runtime.status().errors.at(-1)).toContain("policy decision timed out");
    expect(connector.acquireCount).toBe(0);
    expect(connector.submitCount).toBe(0);
  });

  it("treats submit exceptions as unknown and never retries them", async () => {
    const connector = new FakeConnector(bundle(["a"]), "delivered", new Error("connection lost after dispatch"));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-1", policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    const result = await runtime.tick();
    expect(result.type).toBe("unknown");
    expect(runtime.status().tainted).toBe(true);
    expect(connector.submitCount).toBe(1);
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(connector.submitCount).toBe(1);
  });

  it("polls past a settling receipt successor before accepting stable readiness", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    connector.observationQueue = [bundle(["a"]), { observation: snapshot(["settling"], "snapshot-2", "settling", 2), reads: [] }, { observation: snapshot(["stable"], "snapshot-3", "interactive", 3), reads: [] }];
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "one_step", runId: "run-1", successorPoll: { maxAttempts: 3, baseBackoffMs: 0 }, staleRefresh: { maxAttempts: 2, baseBackoffMs: 0 }, sleep: async () => {}, policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    const result = await runtime.tick();
    expect(result.type).toBe("delivered");
    if (result.type === "delivered") expect(result.successor.snapshot_id).toBe("snapshot-3");
  });

  it("accepts a distinct non-interactive terminal successor without inventing a next decision", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    connector.observationQueue = [{ observation: snapshot(["a"], "snapshot-initial", "interactive", 1), reads: [] }, { observation: snapshot([], "snapshot-terminal", "observed", 2), reads: [] }];
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "one_step", runId: "run-1", successorPoll: { maxAttempts: 2, baseBackoffMs: 0 }, staleRefresh: { maxAttempts: 2, baseBackoffMs: 0 }, sleep: async () => {}, policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    const result = await runtime.tick();
    expect(result.type).toBe("delivered");
    if (result.type === "delivered") expect(result.successor.status).toBe("observed");
  });

  it("rejects a successor from a different runtime identity as unknown", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const drifted = {
      ...snapshot(["next"], "snapshot-2", "interactive", 2),
      session: { runtime_instance_id: "restarted-runtime", environment_fingerprint: "different-environment" }
    };
    connector.observationQueue = [bundle(["a"]), { observation: drifted, reads: [] }];
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "one_step", runId: "run-successor-drift", successorPoll: { maxAttempts: 2, baseBackoffMs: 0 }, staleRefresh: { maxAttempts: 2, baseBackoffMs: 0 }, sleep: async () => {}, policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });

    const result = await runtime.tick();

    expect(result.type).toBe("unknown");
    expect(runtime.status().taint_reason).toContain("successor_environment_identity_drift");
    expect(connector.submitCount).toBe(1);
  });

  it("serves typed status and bounded sequential auto ticks on loopback", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "human", runId: "run-1", staleRefresh: { maxAttempts: 2, baseBackoffMs: 0 }, successorPoll: { maxAttempts: 2, baseBackoffMs: 0 }, sleep: async () => {}, policy: (input) => ({ candidate_digest: input.candidate_digest, scores: Array(input.candidate_count).fill(1), selected_index: 0 }) });
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0, maxAutoTicks: 2 });
    try {
      const modeResponse = await fetch(`${service.address}/v2/mode`, { method: "POST", headers: { "content-type": "application/json", "x-sts2-policy-run-id": runtime.status().run_id }, body: JSON.stringify({ mode: "auto" }) });
      expect(modeResponse.status).toBe(200);
      const modeEnvelope = await modeResponse.json();
      const httpContract = JSON.parse(await readFile(new URL("../../../contracts/policy-runtime/http.schema.json", import.meta.url), "utf8"));
      expect(modeEnvelope.schema).toBe(httpContract.$id);
      expect(modeEnvelope.status.controller).toBe("released");
      const tickResponse = await fetch(`${service.address}/v2/tick`, { method: "POST", headers: { "content-type": "application/json", "x-sts2-policy-run-id": runtime.status().run_id }, body: JSON.stringify({ max_ticks: 2 }) });
      expect(tickResponse.status).toBe(200);
      const result = await tickResponse.json() as { results: unknown[] };
      expect(result.results.length).toBe(2);
      expect(connector.submitCount).toBe(2);
    } finally { await service.close(); }
  });

  it("shares one finite submission wallet across changing loop snapshots", async () => {
    const connector = new FakeConnector(bundle(["loop", "return"]));
    connector.stale = false;
    connector.successorActionIds = ["loop", "return"];
    const seenCatalogs: string[][] = [];
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-budget-submit", autoBudget: { maxSubmissions: 6, maxPolicyCalls: 6, deadlineMs: 10_000 }, successorPoll: { maxAttempts: 2, baseBackoffMs: 0 }, sleep: async () => {}, policy: (input) => {
      seenCatalogs.push(input.bundle.observation.bound_actions.actions.map(action => action.bound_action_id));
      return { candidate_digest: input.candidate_digest, scores: Array(input.candidate_count).fill(1), selected_index: 0 };
    } });
    for (let index = 0; index < 6; index += 1) expect((await runtime.tick()).type).toBe("delivered");
    const blocked = await runtime.tick();
    expect(blocked).toMatchObject({ type: "not_admitted", reason: "autonomy_budget_exhausted" });
    expect(connector.submitCount).toBe(6);
    expect(runtime.status()).toMatchObject({ mode: "human", controller: "released", autonomy_budget: { state: "exhausted", submissions_used: 6, exhausted_reason: "submission_attempt_limit" } });
    expect(seenCatalogs).toHaveLength(6);
    expect(seenCatalogs.every(ids => ids.includes("return") && ids.length === 2)).toBe(true);
    expect(runtime.status().autonomy_budget.submissions_used).not.toBeGreaterThan(6);
  });

  it("bounds policy calls even when every decision abstains", async () => {
    const connector = new FakeConnector(bundle(["loop", "return"]));
    connector.stale = false;
    const scorer = vi.fn((input: { candidate_digest: string; candidate_count: number }) => ({ candidate_digest: input.candidate_digest, scores: Array(input.candidate_count).fill(1), selected_index: null }));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "shadow", runId: "run-budget-policy", autoBudget: { maxSubmissions: 6, maxPolicyCalls: 2, deadlineMs: 10_000 }, sleep: async () => {}, policy: scorer });
    expect((await runtime.tick()).type).toBe("shadow");
    connector.current = bundle(["b"], "snapshot-b");
    expect((await runtime.tick()).type).toBe("shadow");
    expect(await runtime.tick()).toMatchObject({ type: "not_admitted", reason: "autonomy_budget_exhausted" });
    expect(scorer).toHaveBeenCalledTimes(2);
    expect(connector.submitCount).toBe(0);
    expect(runtime.status().autonomy_budget).toMatchObject({ state: "exhausted", policy_calls_used: 2, exhausted_reason: "policy_call_limit" });
  });

  it("does not reset the shared wallet for duplicate Auto requests or new snapshots", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    connector.stale = false;
    const entered = deferred<void>();
    const finish = deferred<{ candidate_digest: string; scores: number[]; selected_index: number | null }>();
    let calls = 0;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "human", runId: "run-budget-shared", autoBudget: { maxSubmissions: 3, maxPolicyCalls: 6, deadlineMs: 10_000 }, sleep: async () => {}, policy: async input => {
      if (calls++ === 0) { entered.resolve(); return finish.promise; }
      return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 };
    } });
    await runtime.setMode("auto");
    const firstTick = runtime.tick();
    await entered.promise;
    const duplicateAuto = runtime.setMode("auto");
    expect(runtime.status().autonomy_budget.submissions_used).toBe(0);
    finish.resolve({ candidate_digest: candidateOrderDigest(["a"]), scores: [1], selected_index: 0 });
    expect((await firstTick).type).toBe("delivered");
    await duplicateAuto;
    const used = runtime.status().autonomy_budget;
    connector.current = bundle(["b"], "snapshot-new");
    await runtime.tick();
    expect(runtime.status().autonomy_budget.submissions_used).toBe(used.submissions_used + 1);
    expect(runtime.status().autonomy_budget.policy_calls_used).toBe(used.policy_calls_used + 1);
    expect(connector.submitCount).toBe(2);
  });

  it("cancels an unresolved policy at the monotonic deadline without reviving its late result", async () => {
    vi.useFakeTimers();
    try {
      let clock = 0;
      const connector = new FakeConnector(bundle(["a"]));
      connector.stale = false;
      const entered = deferred<void>();
      const finish = deferred<{ candidate_digest: string; scores: number[]; selected_index: number | null }>();
      const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "run-budget-deadline", autoBudget: { maxSubmissions: 6, maxPolicyCalls: 6, deadlineMs: 10 }, monotonicNow: () => clock, sleep: async () => {}, policy: async () => { entered.resolve(); return finish.promise; } });
      const tick = runtime.tick();
      await entered.promise;
      clock = 11;
      vi.advanceTimersByTime(10);
      expect(await tick).toMatchObject({ type: "not_admitted", reason: "autonomy_budget_exhausted" });
      expect(runtime.status()).toMatchObject({ mode: "human", controller: "released", autonomy_budget: { state: "exhausted", exhausted_reason: "deadline" } });
      finish.resolve({ candidate_digest: candidateOrderDigest(["a"]), scores: [1], selected_index: 0 });
      await Promise.resolve();
      expect(connector.submitCount).toBe(0);
    } finally { vi.useRealTimers(); }
  });

  it("shares the wallet between the background worker and concurrent HTTP ticks", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    connector.stale = false;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "human", runId: "run-budget-http", autoBudget: { maxSubmissions: 3, maxPolicyCalls: 6, deadlineMs: 10_000 }, successorPoll: { maxAttempts: 2, baseBackoffMs: 0 }, sleep: async () => {}, policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0, autoDrive: true, autoIdleMs: 0 });
    const post = (route: string, body: unknown) => fetch(`${service.address}/v2/${route}`, { method: "POST", headers: { "content-type": "application/json", "x-sts2-policy-run-id": runtime.status().run_id }, body: JSON.stringify(body) });
    try {
      expect((await post("mode", { mode: "auto" })).status).toBe(200);
      await Promise.all(Array.from({ length: 4 }, () => post("tick", { max_ticks: 2 })));
      await eventually(() => runtime.status().autonomy_budget.state === "exhausted");
      expect(connector.submitCount).toBe(3);
      expect(runtime.status().autonomy_budget.submissions_used).toBe(3);
      expect(runtime.status().mode).toBe("human");
    } finally { await service.close(); }
  });

  it("rejects cross-origin, non-JSON and rebound-host mutations before Runtime dispatch", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, policy: () => { throw new Error("must not score"); } });
    const modeSpy = vi.spyOn(runtime, "setMode");
    const tickSpy = vi.spyOn(runtime, "tick");
    const stopSpy = vi.spyOn(runtime, "stop");
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0 });
    try {
      for (const [route, body] of [["mode", { mode: "auto" }], ["tick", { max_ticks: 1 }], ["stop", {}]] as const) {
        for (const [headers, expected] of [
          [{ "content-type": "text/plain", origin: "https://untrusted.invalid" }, 403],
          [{ "content-type": "text/plain" }, 415],
          [{ "content-type": "application/json", origin: "https://untrusted.invalid" }, 403],
          [{ "content-type": "application/json", origin: "null" }, 403],
          [{ "content-type": "application/json", host: "untrusted.invalid" }, 403],
          [{ "content-type": "application/json", host: "127.0.0.1:1" }, 403]
        ] as const) {
          // Raw HTTP preserves the supplied Host; fetch implementations may replace it.
          const statusCode = await new Promise<number>((resolve, reject) => {
            const request = httpRequest(`${service.address}/v2/${route}`, { method: "POST", headers }, (response) => { response.resume(); resolve(response.statusCode!); });
            request.once("error", reject); request.end(JSON.stringify(body));
          });
          expect(statusCode, JSON.stringify({ route, headers })).toBe(expected);
        }
      }
      expect(modeSpy).not.toHaveBeenCalled(); expect(tickSpy).not.toHaveBeenCalled(); expect(stopSpy).not.toHaveBeenCalled();
      for (const origin of [undefined, service.address]) {
        const response = await fetch(`${service.address}/v2/mode`, { method: "POST", headers: { "x-sts2-policy-run-id": runtime.status().run_id, "content-type": "application/json; charset=utf-8", ...(origin ? { origin } : {}) }, body: JSON.stringify({ mode: "human" }) });
        expect(response.status).toBe(200);
      }
      expect(modeSpy).toHaveBeenCalledTimes(2);
      expect(connector.submitCount).toBe(0);
    } finally { await service.close(); }
  });

  it("rejects missing, duplicate, empty and foreign Runtime run preconditions before all mutations", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, runId: "run-current", policy: () => { throw new Error("must not score"); } });
    const mode = vi.spyOn(runtime, "setMode"), tick = vi.spyOn(runtime, "tick"), stop = vi.spyOn(runtime, "stop");
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0 });
    try {
      for (const [route, body] of [["mode", { mode: "auto" }], ["tick", { max_ticks: 1 }], ["stop", {}]] as const) {
        for (const [expectedRun, code] of [[undefined, 428], ["", 428], [["run-current", "run-current"], 428], ["run-previous", 409]] as const) {
          const result = await new Promise<{ code: number; body: string }>((resolve, reject) => {
            const request = httpRequest(`${service.address}/v2/${route}`, { method: "POST", headers: { "content-type": "application/json", ...(expectedRun === undefined ? {} : { "x-sts2-policy-run-id": expectedRun as string | string[] }) } }, (response) => {
              let data = ""; response.setEncoding("utf8"); response.on("data", (chunk: string) => { data += chunk; });
              response.once("end", () => resolve({ code: response.statusCode!, body: data }));
            });
            request.once("error", reject); request.end(JSON.stringify(body));
          });
          expect(result.code).toBe(code);
          expect(JSON.parse(result.body)).toEqual({ schema: "sts2.policy-runtime/http-2", error: code === 428 ? "runtime_run_precondition_required" : "runtime_run_mismatch" });
        }
        const old = await fetch(`${service.address}/${route}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
        expect(old.status).toBe(404);
      }
      expect(mode).not.toHaveBeenCalled(); expect(tick).not.toHaveBeenCalled(); expect(stop).not.toHaveBeenCalled();
      expect(connector.acquireCount).toBe(0); expect(connector.submitCount).toBe(0);
    } finally { await service.close(); }
  });

  it("does not mutate a replacement Runtime on the same port after a successful earlier status", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const old = new PolicyRuntime({ manifest: manifest(), connector, runId: "run-old", policy: () => { throw new Error("must not score"); } });
    const previous = await startPolicyRuntimeHttpServer(old, { port: 0 });
    const client = new PolicyRuntimeClient(previous.address);
    await client.readStatus();
    await previous.close();
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, runId: "run-replacement", policy: () => { throw new Error("must not score"); } });
    const mode = vi.spyOn(runtime, "setMode"), tick = vi.spyOn(runtime, "tick"), stop = vi.spyOn(runtime, "stop");
    const replacement = await startPolicyRuntimeHttpServer(runtime, { port: Number(new URL(previous.address).port) });
    try {
      await expect(client.setMode("auto")).rejects.toMatchObject({ code: "policy_runtime_run_rejected" });
      await expect(client.tick()).rejects.toMatchObject({ code: "policy_runtime_run_rejected" });
      // A background status refresh between One-Step phases cannot retarget
      // the tick: the caller retains the run returned by its mode command.
      await client.readStatus();
      await expect(client.tick("run-old")).rejects.toMatchObject({ code: "policy_runtime_run_rejected" });
      const response = await fetch(`${replacement.address}/v2/stop`, { method: "POST", headers: { "content-type": "application/json", "x-sts2-policy-run-id": "run-old" }, body: "{}" });
      expect(response.status).toBe(409);
      expect(mode).not.toHaveBeenCalled(); expect(tick).not.toHaveBeenCalled(); expect(stop).not.toHaveBeenCalled();
      expect(runtime.status().lifecycle).toBe("running"); expect(connector.submitCount).toBe(0);
    } finally { await replacement.close(); }
  });

  it.each([false, true])("notifies stop cleanup exactly once after success, even when response aborts=%s", async (abortResponse) => {
    const root = await mkdtemp(join(tmpdir(), "sts2-stop-disconnect-"));
    const evidence = await AgentRunEvidence.create({ root, policyManifest: manifest(), runtimeVersion: "0.1.0-rc.3", runtimeCodeSha256: "e".repeat(64), mode: "auto" });
    let finishScoring!: () => void;
    const scoring = new Promise<void>((resolve) => { finishScoring = resolve; });
    let scoringEntered = false;
    let policyCalls = 0;
    const connector = new FakeConnector(bundle(["a"]));
    const releaseBarrier = deferred<void>();
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", evidence, runId: evidence.runId, runtimeIdentity: { version: "0.1.0-rc.3", code_sha256: "e".repeat(64) }, policy: async (input) => {
      if (policyCalls++ === 0) return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 };
      scoringEntered = true; await scoring;
      return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 };
    } });
    await evidence.attestAdapter(manifest().adapter);
    expect((await runtime.tick()).type).toBe("delivered");
    expect(runtime.status()).toMatchObject({ mode: "auto", controller: "held" });
    if (abortResponse) connector.releaseGate = releaseBarrier.promise;
    const stopSpy = vi.spyOn(runtime, "stop");
    let cleanupCount = 0;
    let cleanupFinished!: () => void;
    const cleanup = new Promise<void>((resolve) => { cleanupFinished = resolve; });
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0, onStopped: async () => { cleanupCount++; await service.close(); cleanupFinished(); } });
    let responseClosed!: () => void;
    const disconnected = new Promise<void>((resolve) => { responseClosed = resolve; });
    let responseFinished = false;
    service.server.on("request", (request, response) => { if (request.url === "/v2/stop") { response.once("finish", () => { responseFinished = true; }); response.once("close", responseClosed); } });
    const tick = runtime.tick();
    try {
      await eventually(() => scoringEntered);
      let stopRequest!: ClientRequest;
      const stopped = new Promise<{ statusCode: number; body: string }>((resolve, reject) => {
        stopRequest = httpRequest(`${service.address}/v2/stop`, { method: "POST", headers: { "content-type": "application/json", "x-sts2-policy-run-id": runtime.status().run_id } }, (response) => {
          let body = ""; response.setEncoding("utf8"); response.on("data", (chunk: string) => { body += chunk; });
          response.once("end", () => resolve({ statusCode: response.statusCode!, body }));
        });
        stopRequest.once("error", reject); stopRequest.end("{}");
      });
      await eventually(() => stopSpy.mock.calls.length === 1);
      if (abortResponse) {
        // The first auto tick genuinely acquired and retained the controller.
        // Stop must now reach the connector release barrier before the caller
        // disconnects; a releaseGate alone is insufficient for a pre-acquire
        // slow tick because releaseController would be a no-op.
        await eventually(() => connector.releaseCount === 1);
        expect(runtime.status().lifecycle).toBe("running");
        expect(responseFinished).toBe(false);
        expect(cleanupCount).toBe(0);
        stopRequest.destroy(new Error("caller disconnected"));
        await expect(stopped).rejects.toThrow("caller disconnected");
        await disconnected;
        expect(runtime.status().lifecycle).toBe("running");
        expect(responseFinished).toBe(false);
        expect(cleanupCount).toBe(0);
        releaseBarrier.resolve();
        await tick;
      } else {
        const response = await stopped;
        expect(response.statusCode).toBe(200);
        expect(JSON.parse(response.body).status.lifecycle).toBe("stopped");
        expect(responseFinished).toBe(true);
        await tick;
      }
      await cleanup;
      expect(cleanupCount).toBe(1);
      expect(service.server.listening).toBe(false);
      expect(JSON.parse(await readFile(join(evidence.directory, "evidence-manifest.json"), "utf8")).complete).toBe(true);
    } finally { finishScoring(); releaseBarrier.resolve(); await tick; if (service.server.listening) await service.close(); }
  });

  it("passes real non-null Runtime environment status through the Workbench consumer", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "shadow", policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    expect((await runtime.tick()).type).toBe("shadow");
    const status = decodePolicyRuntimeStatus(runtime.status());
    expect(status.environment.host_kind).toBe("test");
    expect(status.environment.loaded_mod_ids).toEqual(["fixture-mod"]);
    expect(connector.acquireCount).toBe(0);
    for (const invalid of [{ host_kind: "invented" }, { loaded_mod_ids: ["duplicate", "duplicate"] }]) {
      expect(() => decodePolicyRuntimeStatus({ ...status, environment: { ...status.environment, ...invalid } })).toThrow();
    }
  });

  it("does not repeat a one-step command after the HTTP caller times out", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    let finishScoring!: () => void;
    const scoring = new Promise<void>((resolve) => { finishScoring = resolve; });
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "human", policy: async (input) => {
      await scoring;
      return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 };
    } });
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0 });
    const client = new PolicyRuntimeClient(service.address, { commandTimeoutMs: 100 });
    try {
      await client.readStatus();
      await client.setMode("one_step");
      await expect(client.tick()).rejects.toMatchObject({ code: "policy_runtime_command_unknown" });
      finishScoring();
      await eventually(() => connector.submitCount === 1 && runtime.status().mode === "human");
      await client.readStatus();
      await expect(client.tick()).rejects.toMatchObject({ code: "policy_runtime_command_unknown" });
      await expect(client.setMode("one_step")).rejects.toMatchObject({ code: "policy_runtime_command_unknown" });
      await client.setMode("human");
      expect(connector.submitCount).toBe(1);
      expect(runtime.status().tainted).toBe(false);
    } finally { finishScoring(); await service.close(); }
  });

  it("continuously shadows only new Snapshots without acquiring a controller", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    let scoreCount = 0;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "human", runId: "run-shadow", staleRefresh: { maxAttempts: 2, baseBackoffMs: 0 }, sleep: async () => {}, policy: (input) => { scoreCount += 1; return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }; } });
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0, autoDrive: true, maxAutoTicks: 2, autoIdleMs: 5 });
    try {
      const response = await fetch(`${service.address}/v2/mode`, { method: "POST", headers: { "content-type": "application/json", "x-sts2-policy-run-id": runtime.status().run_id }, body: JSON.stringify({ mode: "shadow" }) });
      expect(response.status).toBe(200);
      await eventually(() => scoreCount === 1);
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(scoreCount).toBe(1);
      connector.current = bundle(["b"], "snapshot-2");
      await eventually(() => scoreCount === 2);
      expect(connector.acquireCount).toBe(0);
      expect(connector.submitCount).toBe(0);
    } finally { await service.close(); }
  });

  it("can defer automatic policy work until startup identity is published", async () => {
    const connector = new FakeConnector(bundle(["a"]));
    let scoreCount = 0;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "shadow", runId: "run-deferred", policy: (input) => { scoreCount += 1; return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }; } });
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0, autoDrive: true, deferAutoDrive: true, autoIdleMs: 5 });
    try {
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(scoreCount).toBe(0);
      service.startDriving();
      await eventually(() => scoreCount === 1);
    } finally { await service.close(); }
  });

  it("seals an immutable Agent run when stopped", async () => {
    const root = await mkdtemp(join(tmpdir(), "sts2-agent-run-"));
    const evidence = await AgentRunEvidence.create({ root, runId: "run-evidence", policyManifest: manifest(), runtimeVersion: "0.1.0-rc.1", runtimeCodeSha256: "e".repeat(64), mode: "shadow" });
    await evidence.attestAdapter(manifest().adapter);
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: new FakeConnector(bundle(["a"])), mode: "shadow", runId: evidence.runId, evidence, runtimeIdentity: { version: "0.1.0-rc.1", code_sha256: "e".repeat(64) }, policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    expect((await runtime.tick()).type).toBe("shadow");
    const stopped = await runtime.stop();
    expect(stopped.lifecycle).toBe("stopped");
    await verifyEvidenceDirectory(evidence.directory);
    const runManifest = JSON.parse(await readFile(join(evidence.directory, "manifest.json"), "utf8")) as { status: string; ended_at: string | null };
    expect(runManifest.status).toBe("stopped");
    expect(runManifest.ended_at).not.toBeNull();
    expect(JSON.parse(await readFile(join(evidence.directory, "policy-manifest.json"), "utf8"))).toEqual(manifest());
    const adapterAttestation = JSON.parse(await readFile(join(evidence.directory, "adapter-attestation.json"), "utf8")) as { status: string; expected: unknown; actual: unknown };
    expect(adapterAttestation.status).toBe("attested");
    expect(adapterAttestation.expected).toEqual(manifest().adapter);
    expect(adapterAttestation.actual).toEqual(manifest().adapter);
    const immutableManifest = JSON.parse(await readFile(join(evidence.directory, "evidence-manifest.json"), "utf8")) as { files: Array<{ path: string }> };
    expect(immutableManifest.files.map((entry) => entry.path)).toEqual([
      "adapter-attestation.json",
      "events.jsonl",
      "manifest.json",
      "policy-manifest.json"
    ]);
    expect((await runtime.stop()).lifecycle).toBe("stopped");
  });

  it("serializes concurrent Agent evidence appends before immutable finalization", async () => {
    const root = await mkdtemp(join(tmpdir(), "sts2-agent-concurrent-evidence-"));
    const evidence = await AgentRunEvidence.create({ root, runId: "run-concurrent", policyManifest: manifest(), runtimeVersion: "0.1.0-rc.1", runtimeCodeSha256: "e".repeat(64), mode: "shadow" });
    await evidence.attestAdapter(manifest().adapter);

    await Promise.all(Array.from({ length: 12 }, (_value, index) => evidence.append("parallel", { index })));
    await evidence.finalize({ status: "stopped", tainted: false, mode: "human" });

    const events = (await readFile(join(evidence.directory, "events.jsonl"), "utf8")).trim().split("\n").map((line) => JSON.parse(line) as { sequence: number });
    expect(events.map((event) => event.sequence)).toEqual(Array.from({ length: 12 }, (_value, index) => index + 1));
    await verifyEvidenceDirectory(evidence.directory);
  });

  it("releases an already-held controller when mode evidence fails", async () => {
    const root = await mkdtemp(join(tmpdir(), "sts2-agent-mode-failure-"));
    const evidence = await AgentRunEvidence.create({ root, runId: "run-mode-failure", policyManifest: manifest(), runtimeVersion: "0.1.0-rc.1", runtimeCodeSha256: "e".repeat(64), mode: "auto" });
    await evidence.attestAdapter(manifest().adapter);
    const connector = new FakeConnector(bundle(["a"]));
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: evidence.runId, evidence, runtimeIdentity: { version: "0.1.0-rc.1", code_sha256: "e".repeat(64) }, policy: (input) => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    expect((await runtime.tick()).type).toBe("delivered");
    expect(runtime.status().controller).toBe("held");
    await evidence.finalize({ status: "stopped", tainted: false, mode: "auto" });

    await expect(runtime.setMode("auto")).rejects.toThrow(/evidence is unavailable/);
    expect(runtime.status().mode).toBe("human");
    expect(runtime.status().controller).toBe("released");
    expect(connector.releaseCount).toBe(1);
  });
});

async function eventually(predicate: () => boolean): Promise<void> {
  const deadline = Date.now() + 500;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error("condition was not reached before timeout");
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

describe("continuous native decision recovery", () => {
  const policy = (input: {candidate_digest: string; candidate_count: number}) => ({candidate_digest: input.candidate_digest, scores: Array(input.candidate_count).fill(1), selected_index: 0});

  it("rescans and rescores after a known stale non-delivery, with new request and action IDs", async () => {
    const connector = new FakeConnector(bundle(["old"]), "not_delivered");
    connector.stale = false;
    const original = connector.submit.bind(connector);
    const requests: Parameters<typeof connector.submit>[0][] = [];
    connector.submit = async input => {
      requests.push(input);
      const receipt = await original(input);
      connector.current = bundle(["fresh"], "snapshot-fresh");
      return {...receipt, reason_code: "stale_snapshot", retry: {allowed: true, reason: "fresh_snapshot_required"}};
    };
    const scorer = vi.fn(policy);
    const runtime = new PolicyRuntime({manifest: manifest(), connector, mode: "auto", policy: scorer});
    await runtime.tick();
    expect(runtime.status().mode).toBe("auto");
    expect(runtime.status().controller).toBe("released");
    await runtime.tick();
    expect(scorer).toHaveBeenCalledTimes(2);
    expect(requests[1]!.boundActionId).toBe("fresh");
    expect(requests[1]!.requestId).not.toBe(requests[0]!.requestId);
    await runtime.tick();
    expect(runtime.status().mode).toBe("human"); // bounded repeated stale failures
  });

  it.each(["not_delivered", "unknown"] as const)("never continues %s without explicit stale retry admission", async delivery => {
    const connector = new FakeConnector(bundle(["a"]), delivery);
    connector.stale = false;
    const runtime = new PolicyRuntime({manifest: manifest(), connector, mode: "auto", policy});
    await runtime.tick(); await runtime.tick();
    expect(connector.submitCount).toBe(1);
    expect(runtime.status().mode).toBe("human");
  });

  it("waits through multi-second enemy animations without another submission", async () => {
    const connector = new FakeConnector(bundle(["a"])); connector.stale = false;
    connector.observationQueue = [bundle(["a"]), ...Array.from({length: 24}, (_, i) => ({observation: snapshot([], `animation-${i}`, "settling", i+2), reads: []})), {observation: snapshot(["next"], "next", "interactive", 30), reads: []}];
    const sleep = vi.fn(async (_ms: number) => {});
    const runtime = new PolicyRuntime({manifest: manifest(), connector, mode: "auto", policy, sleep});
    expect((await runtime.tick()).type).toBe("delivered");
    expect(connector.submitCount).toBe(1);
    expect(sleep.mock.calls.reduce((total, [ms]) => total+ms, 0)).toBe(6000);
    expect(runtime.status().tainted).toBe(false);
  });

  it("hands off on bounded successor exhaustion and never resubmits", async () => {
    const connector = new FakeConnector(bundle(["a"])); connector.stale = false;
    connector.observationQueue = [bundle(["a"]), ...Array.from({length: 3}, (_, i) => ({observation: snapshot([], `settling-${i}`, "settling", i+2), reads: []}))];
    const runtime = new PolicyRuntime({manifest: manifest(), connector, mode: "auto", policy,
      successorPoll: {maxAttempts: 3, baseBackoffMs: 0}, sleep: async () => {}});
    expect((await runtime.tick()).type).toBe("unknown");
    expect(runtime.status().tainted).toBe(true);
    await runtime.tick();
    expect(connector.submitCount).toBe(1);
  });

  it("does not score or exit Auto during a settling frame", async () => {
    const connector = new FakeConnector({observation: snapshot([], "settling", "settling", 1), reads: []}); connector.stale = false;
    const scorer = vi.fn(policy);
    const runtime = new PolicyRuntime({manifest: manifest(), connector, mode: "auto", policy: scorer});
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(runtime.status().mode).toBe("auto");
    expect(scorer).not.toHaveBeenCalled();
    expect(connector.submitCount).toBe(0);
  });

  it("allows Human recovery during animation waiting without sending another action", async () => {
    const connector = new FakeConnector(bundle(["a"])); connector.stale = false;
    connector.observationQueue = [bundle(["a"]), {observation: snapshot([], "animation", "settling", 2), reads: []}];
    let recovery: Promise<unknown> | undefined;
    const runtime = new PolicyRuntime({manifest: manifest(), connector, mode: "auto", policy, sleep: async () => { recovery = runtime.setMode("human"); }});
    expect((await runtime.tick()).type).toBe("not_admitted");
    await recovery;
    expect(runtime.status().mode).toBe("human");
    expect(runtime.status().controller).toBe("released");
    expect(runtime.status().tainted).toBe(false);
    expect(connector.submitCount).toBe(1);
  });
});
