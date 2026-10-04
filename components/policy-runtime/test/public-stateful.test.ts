import { createHash } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import { EventEmitter } from "node:events";
import { PassThrough, Writable } from "node:stream";
import type { PlayerEnvironmentBoundAction, PlayerEnvironmentCapabilities, PlayerEnvironmentReceipt, PlayerEnvironmentSnapshot } from "@rsgcsg/sts2-connector-client";
import { candidateOrderDigest } from "../src/digest.js";
import { POLICY_PORT_V4_SCHEMA, validatePolicyManifest, type PolicyManifest, type PolicyConnector, type PublicStatefulControlMetadata, type PublicStatefulDecisionContext, type PublicStatefulMemoryNamespace } from "../src/contracts.js";
import type { AgentRunEvidence } from "../src/evidence.js";
import { NdjsonPolicyPort, servePublicStatefulPolicyPort } from "../src/policy-port.js";
import { PolicyRuntime } from "../src/runtime.js";
import { publicDecisionFingerprint } from "../src/semantic-cycle.js";
import { startPolicyRuntimeHttpServer } from "../src/server.js";
// @ts-expect-error Exercise the real JavaScript Workbench status decoder.
import { decodePolicyRuntimeStatus } from "../../../apps/workbench/src/policy-runtime-client.mjs";

const action = (id: string, label = id): PlayerEnvironmentBoundAction => ({ bound_action_id: id, verb: "end_turn", interaction_id: "interaction", arguments: [], label });
function snapshot(snapshotId = "snapshot-a", sequence = 7): PlayerEnvironmentSnapshot {
  return {
    protocol_version: "1.0.0", schema: "sts2.player-environment/snapshot-1", snapshot_id: snapshotId, sequence,
    observed_at: "2026-10-03T00:00:00.000Z", status: "interactive", persistent: null,
    interaction: { interaction_id: "interaction", kind: "test", stage: "ready", content_schema: "sts2.player-environment/surface/test-1", content: { surface: { kind: "test" }, context: { kind: "test" } }, capabilities: [] },
    referents: [], bound_actions: { schema: "sts2.player-environment/bound-actions-1", status: "complete", materialized_count: 1, total_count: 1, limit: 1, ordering_semantics: "connector_order", actions: [action("bound-a")] }, reads: [],
    completeness: { status: "complete", visible_information: "fixture", interaction_discovery: "fixture", missing: [], hidden_by_policy: [] },
    session: { runtime_instance_id: "runtime", environment_fingerprint: "environment" },
    information_policy: { id: "test", scope: "test", includes_hidden_information: false, unknown_field_behavior: "reject" }
  };
}

function manifest(): PolicyManifest {
  return {
    schema: "sts2.policy-runtime/policy-manifest-1", manifest_id: "public-m2-test",
    policy: { id: "public-m2", version: "1", provider: "fixture", architecture: "stateful-fixture" },
    adapter: { id: "public-m2-port4", version: "1", protocol: "sts2.policy-runtime/decision-only-ndjson-4", code_sha256: "c".repeat(64) },
    artifact: { id: "artifact", path: "artifact.bin", sha256: "a".repeat(64) },
    representation: { id: "public-snapshot", version: "1", input_schema: "sts2.player-environment/snapshot-1" },
    requirements: { connector_protocol_version: "1.0.0", environment: { host_kind: "test", connector_version: "1", connector_source_revision: "source", connector_artifact_sha256: "b".repeat(64), connector_module_version_id: "mvid", modset_status: "exact", modset_fingerprint: "modset", loaded_mod_ids: ["fixture-mod"] }, reads: [], whole_decision_admission: true, candidate_order_digest: "sha256-json-bound-action-id-order", score_count_matches_candidate_count: true, selected_index: true, successor_required: true },
    support: { game_versions: ["fixture-game"], game_commits: ["fixture-commit"], interaction_kinds: ["test"], action_verbs: ["end_turn"] },
    adapter_config: { public_stateful_profile: "sts2.policy-runtime/public-observation-stateful-v1" },
    claims: { full_run: false, selector: false, catalog_filtered: false, creates_action_authority: false, creates_native_operands: false }
  };
}

class FixtureConnector implements PolicyConnector {
  current = snapshot();
  submitCount = 0;
  observeCount = 0;
  delivery: PlayerEnvironmentReceipt["delivery"] = "not_delivered";
  async capabilities(): Promise<PlayerEnvironmentCapabilities> {
    return {
      protocol_version: "1.0.0", snapshot_schema: "sts2.player-environment/snapshot-1", action_schema: "sts2.player-environment/action-1", receipt_schema: "sts2.player-environment/receipt-1", control_schema: "sts2.player-environment/control-1", status: "ready",
      host: { id: "fixture", name: "fixture", version: "1", runtime_instance_id: this.current.session.runtime_instance_id, host_kind: "test", implementation: { source_revision: "source", module_version_id: "mvid", artifact_sha256: "b".repeat(64) } },
      game: { version: "fixture-game", commit: "fixture-commit", branch: null, main_assembly_hash: null, compatibility: { status: "exact", observation_allowed: true, detail: "fixture" }, modset: { status: "exact", fingerprint: "modset", scope: "fixture", loaded_mod_ids: ["fixture-mod"], detail: "fixture" } },
      environment_fingerprint: this.current.session.environment_fingerprint, verbs: ["end_turn"], snapshot_bound: true, single_controller: true, execution_available: true, control: { recommended_renewal_ms: 1000 }, evidence_profiles: [], non_claims: []
    };
  }
  async observeBundle() { this.observeCount += 1; return { observation: this.current, reads: [] }; }
  async acquireController() {}
  async releaseController() {}
  async submit(input: { requestId: string; expectedSnapshotId: string; boundActionId: string }): Promise<PlayerEnvironmentReceipt> {
    this.submitCount += 1;
    const successor = this.delivery === "delivered" ? snapshot(`successor-${this.current.sequence + 1}`, this.current.sequence + 1) : null;
    if (successor) this.current = successor;
    return { protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1", request_id: input.requestId, delivery: this.delivery,
      action: { bound_action_id: input.boundActionId, verb: "end_turn", arguments: [] },
      reason_code: this.delivery === "not_delivered" ? "stale_snapshot" : null,
      retry: { allowed: this.delivery === "not_delivered", reason: "fresh_snapshot_required" }, successor };
  }
}

function cycleSnapshot(page: "claim" | "select", sequence: number): PlayerEnvironmentSnapshot {
  const value = snapshot(`snapshot-${sequence}`, sequence);
  const referentId = `referent-${sequence}`;
  const interactionId = `interaction-${sequence}`;
  const actionId = `action-${sequence}`;
  return { ...value, observed_at: `2026-10-03T00:00:${String(sequence).padStart(2, "0")}.000Z`,
    interaction: { ...value.interaction, interaction_id: interactionId,
      content: { surface: { kind: "test", page }, context: { kind: "test" } } },
    referents: [{ referent_id: referentId, role: "choice", kind: "entity", label: "same choice",
      state: { visible: true, enabled: true, observation_basis: "native_visible_fact" } }],
    bound_actions: { ...value.bound_actions, actions: [{ ...action(actionId), interaction_id: interactionId,
      subject_referent_id: referentId, label: "same choice" }] } };
}

class CyclingConnector extends FixtureConnector {
  current = cycleSnapshot("claim", 1);
  releaseFails = false;
  override async releaseController() { if (this.releaseFails) throw new Error("release unconfirmed"); }
  override async submit(input: { requestId: string; expectedSnapshotId: string; boundActionId: string }): Promise<PlayerEnvironmentReceipt> {
    this.submitCount += 1;
    const prior = this.current;
    if (this.delivery === "delivered") this.current = cycleSnapshot(
      prior.interaction.content.surface.page === "claim" ? "select" : "claim", prior.sequence + 1);
    return { protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1",
      request_id: input.requestId, delivery: this.delivery,
      action: { bound_action_id: input.boundActionId, verb: "end_turn", subject_referent_id: prior.referents[0]!.referent_id, arguments: [] },
      reason_code: this.delivery === "not_delivered" ? "stale_snapshot" : null,
      retry: { allowed: this.delivery === "not_delivered", reason: this.delivery === "not_delivered" ? "fresh_snapshot_required" : "never" },
      successor: this.delivery === "delivered" ? this.current : null };
  }
}

function evidence(events: Array<{ kind: string; payload: Record<string, unknown> }> = []): AgentRunEvidence {
  return { append: vi.fn(async (kind: string, payload: Record<string, unknown>) => { events.push({ kind, payload }); }), finalize: vi.fn(async () => {}) } as unknown as AgentRunEvidence;
}
function completion(decision: PublicStatefulDecisionContext, control: PublicStatefulControlMetadata) {
  return { continuity_token: control.continuity_token, episode_id: control.episode_id, segment_id: control.segment_id,
    observation_ordinal: control.observation_ordinal, snapshot_id: decision.bundle.observation.snapshot_id,
    sequence: decision.bundle.observation.sequence, previous_action_request_id: control.previous_action?.request_id ?? null };
}
function tokenCommitment(runId: string, token: string): string {
  return createHash("sha256")
    .update("sts2.policy-runtime/public-stateful-continuity-token-commitment-v1\0", "utf8")
    .update(runId, "utf8").update("\0", "utf8").update(token, "utf8").digest("hex");
}
function runtime(connector: FixtureConnector, policy: (decision: PublicStatefulDecisionContext, control: PublicStatefulControlMetadata) => void | Promise<void>, opts: { timeout?: number; events?: Array<{ kind: string; payload: Record<string, unknown> }>; evidenceSink?: AgentRunEvidence } = {}) {
  return new PolicyRuntime({ manifest: manifest(), connector, runId: "public-run", evidence: opts.evidenceSink ?? evidence(opts.events),
    runtimeIdentity: { version: "test", code_sha256: "d".repeat(64) }, policyTimeoutMs: opts.timeout,
    publicStatefulPolicy: async (decision, control) => {
      await policy(decision, control);
      return { output: { candidate_digest: decision.candidate_digest, scores: [1], selected_index: 0 }, completion: completion(decision, control) };
    } });
}

describe("public Snapshot stateful protocol 4", () => {
  it("keeps old M0 status readable and rejects a private token in the new public view", async () => {
    const rt = runtime(new FixtureConnector(), async () => {});
    const oldStatus = { ...rt.status() };
    delete oldStatus.public_stateful_segment;
    expect(decodePolicyRuntimeStatus(oldStatus)).toEqual(oldStatus);
    const segment = await rt.beginPublicStatefulSegment("bounded_policy_segment");
    expect(decodePolicyRuntimeStatus(rt.status()).public_stateful_segment).toEqual(segment);
    expect(() => decodePolicyRuntimeStatus({ ...rt.status(), public_stateful_segment: {
      ...segment, continuity_token: "private" } })).toThrow();
    await rt.stop();
  });
  it("reports closure after One-Step even without a recovery-epoch change", async () => {
    const connector = new FixtureConnector(); connector.delivery = "delivered";
    const rt = runtime(connector, async () => {});
    const epoch = (await rt.readEnvironment()).recovery_epoch;
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("one_step");
    expect((await rt.tick()).type).toBe("delivered");
    expect(rt.status()).toMatchObject({ mode: "human", public_stateful_segment: null });
    expect((await rt.readEnvironment()).recovery_epoch).toBe(epoch);
    await expect(rt.setMode("auto")).rejects.toThrow(/explicitly begun/);
  });

  it("reports closure after the shared autonomy budget exhausts", async () => {
    const connector = new FixtureConnector(); connector.delivery = "delivered";
    const rt = new PolicyRuntime({ manifest: manifest(), connector, runId: "segment-budget",
      evidence: evidence(), runtimeIdentity: { version: "test", code_sha256: "d".repeat(64) },
      autoBudget: { maxSubmissions: 1, maxPolicyCalls: 2, deadlineMs: 10_000 },
      publicStatefulPolicy: (decision, control) => ({
        output: { candidate_digest: decision.candidate_digest, scores: [1], selected_index: 0 },
        completion: completion(decision, control) }) });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    await rt.tick();
    await rt.tick();
    expect(rt.status()).toMatchObject({ mode: "human", public_stateful_segment: null,
      autonomy_budget: { state: "exhausted" } });
  });

  it("binds HTTP segment controls to the current run, game and recovery epoch", async () => {
    const rt = runtime(new FixtureConnector(), async () => {});
    const service = await startPolicyRuntimeHttpServer(rt, { port: 0, autoDrive: false });
    const environment = await rt.readEnvironment();
    const current = { "x-sts2-game-instance-id": environment.runtime_instance_id,
      "x-sts2-recovery-epoch": String(environment.recovery_epoch) };
    const post = (route: string, headers: Record<string, string> = {}) => fetch(
      `${service.address}/v2/stateful-segment/${route}`, { method: "POST", headers: {
        "content-type": "application/json", "x-sts2-policy-run-id": "public-run", ...headers },
        body: JSON.stringify(route === "begin" ? { scope: "bounded_policy_segment" } : {}) });
    try {
      expect((await post("begin")).status).toBe(428);
      expect((await post("begin", { ...current, "x-sts2-game-instance-id": "another-game" })).status).toBe(409);
      expect((await post("begin", { ...current, "x-sts2-recovery-epoch": "1" })).status).toBe(409);
      expect(rt.status().public_stateful_segment).toBeNull();
      const begun = await post("begin", current);
      expect(begun.status).toBe(200);
      const view = (await begun.json()).segment;
      expect(rt.status().public_stateful_segment).toEqual(view);
      expect((await post("end")).status).toBe(428);
      expect((await post("end", { ...current, "x-sts2-game-instance-id": "another-game" })).status).toBe(409);
      expect(rt.status().public_stateful_segment).toEqual(view);
      expect((await post("end", current)).status).toBe(200);
      expect(rt.status().public_stateful_segment).toBeNull();
      expect((await post("begin", current)).status).toBe(409);
      const fresh = await rt.readEnvironment();
      expect((await post("begin", { ...current, "x-sts2-recovery-epoch": String(fresh.recovery_epoch) })).status).toBe(200);
      await rt.stop();
      expect(rt.status().public_stateful_segment).toBeNull();
    } finally { await service.close(); }
  });

  it("canonicalizes only declared envelope and handle IDs, preserving candidate multiplicity and business facts", () => {
    const first = cycleSnapshot("claim", 1), second = cycleSnapshot("claim", 9);
    expect(publicDecisionFingerprint(first)).toBe(publicDecisionFingerprint(second));
    const one = first.bound_actions.actions[0]!;
    const two = { ...one, bound_action_id: "other-handle" };
    const reordered = { ...first, bound_actions: { ...first.bound_actions,
      materialized_count: 2, total_count: 2, limit: 2, actions: [two, one] } };
    const swapped = { ...reordered, bound_actions: { ...reordered.bound_actions, actions: [one, two] } };
    expect(publicDecisionFingerprint(reordered)).toBe(publicDecisionFingerprint(swapped));
    expect(publicDecisionFingerprint(first)).not.toBe(publicDecisionFingerprint(reordered));
    const progressed = { ...second, persistent: { content_schema: "sts2.player-environment/persistent/run-player-1" as const,
      content: { hp: 20, energy: 1, deck: ["card"], reward: "taken" } } };
    expect(publicDecisionFingerprint(first)).not.toBe(publicDecisionFingerprint(progressed));
    const selected = { ...second, referents: [{ ...second.referents[0]!, state: { ...second.referents[0]!.state, selected: true } }] };
    expect(publicDecisionFingerprint(first)).not.toBe(publicDecisionFingerprint(selected));
  });

  it("preserves duplicate candidate identity with every catalog count held fixed", () => {
    const base = cycleSnapshot("claim", 1);
    const first = base.bound_actions.actions[0]!;
    const other = { ...first, bound_action_id: "other-handle", label: "other choice" };
    const duplicate = { ...first, bound_action_id: "other-handle" };
    const catalog = { ...base.bound_actions, materialized_count: 2, total_count: 2, limit: 2 };
    const distinct = { ...base, bound_actions: { ...catalog, actions: [first, other] } };
    const repeated = { ...base, bound_actions: { ...catalog, actions: [first, duplicate] } };
    expect(publicDecisionFingerprint(distinct)).not.toBe(publicDecisionFingerprint(repeated));
    expect(publicDecisionFingerprint(repeated)).toBe(publicDecisionFingerprint({
      ...repeated, bound_actions: { ...catalog, actions: [duplicate, first] }
    }));
  });

  it("allows one public A→B→A return, then hands a repeated semantic cycle to Human under the same wallet", async () => {
    const connector = new CyclingConnector(), events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
    connector.delivery = "delivered";
    const rt = runtime(connector, async () => {}, { events });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    for (let index = 0; index < 5; index += 1) {
      expect((await rt.tick()).type).toBe("delivered");
      expect(rt.status().mode).toBe("auto");
    }
    const guarded = await rt.tick();
    expect(guarded).toMatchObject({ type: "delivered", receipt: { delivery: "delivered" },
      successor: { sequence: 7 }, status: { mode: "human", controller: "released" } });
    expect(guarded.type === "delivered" && guarded.receipt.request_id).toBeTruthy();
    expect(connector.submitCount).toBe(6);
    expect(rt.status()).toMatchObject({ mode: "human", controller: "released", tainted: false,
      autonomy_budget: { submissions_used: 6, policy_calls_used: 6 } });
    expect(events.some(event => event.kind === "semantic_cycle_detected")).toBe(false);
    expect(rt.status().invalidations).toContain("semantic_cycle_detected");
    expect(rt.status().public_stateful_segment).toBeNull();
    expect(events.find(event => event.kind === "handoff_to_human")?.payload).toEqual({ reason: "semantic_cycle_detected" });
    expect(events.find(event => event.kind === "public_stateful_episode_ended")?.payload).toMatchObject({
      reason: "semantic_cycle_detected", memory_continuity: false });
    expect((await rt.tick()).type).toBe("human");
    expect(connector.submitCount).toBe(6);
  });

  it("also guards generic Snapshot port 1 Auto without requiring a stateful segment", async () => {
    const connector = new CyclingConnector(), events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
    connector.delivery = "delivered";
    const legacy = manifest();
    legacy.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-1";
    legacy.adapter_config = {};
    const rt = new PolicyRuntime({ manifest: legacy, connector, mode: "auto", runId: "port1-cycle",
      evidence: evidence(events), runtimeIdentity: { version: "test", code_sha256: "d".repeat(64) },
      policy: decision => ({ candidate_digest: decision.candidate_digest, scores: [1], selected_index: 0 }) });
    for (let index = 0; index < 5; index += 1) expect((await rt.tick()).type).toBe("delivered");
    expect(await rt.tick()).toMatchObject({ type: "delivered", receipt: { delivery: "delivered" },
      status: { mode: "human", controller: "released" } });
    expect(rt.status()).toMatchObject({ mode: "human", controller: "released", tainted: false,
      autonomy_budget: { submissions_used: 6, policy_calls_used: 6 } });
    expect(events.find(event => event.kind === "handoff_to_human")?.payload).toEqual({ reason: "semantic_cycle_detected" });
    expect(connector.submitCount).toBe(6);
    expect((await rt.tick()).type).toBe("human");
    expect(connector.submitCount).toBe(6);
  });

  it("keeps a delivered receipt but taints the run if cycle handoff evidence cannot be written", async () => {
    const connector = new CyclingConnector(); connector.delivery = "delivered";
    const legacy = manifest();
    legacy.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-1";
    legacy.adapter_config = {};
    const writer = { append: vi.fn(async (kind: string) => {
      if (kind === "handoff_to_human") throw new Error("evidence disk unavailable");
    }), finalize: vi.fn(async () => {}) } as unknown as AgentRunEvidence;
    const rt = new PolicyRuntime({ manifest: legacy, connector, mode: "auto", runId: "port1-cycle-evidence-failure",
      evidence: writer, runtimeIdentity: { version: "test", code_sha256: "d".repeat(64) },
      policy: decision => ({ candidate_digest: decision.candidate_digest, scores: [1], selected_index: 0 }) });
    for (let index = 0; index < 5; index += 1) expect((await rt.tick()).type).toBe("delivered");
    expect(await rt.tick()).toMatchObject({ type: "delivered", receipt: { delivery: "delivered" },
      status: { mode: "human", controller: "released", tainted: true, taint_reason: "handoff_evidence_write_failed" } });
    expect((await rt.tick()).type).toBe("not_admitted");
    expect(connector.submitCount).toBe(6);
  });

  it("does not infer no progress when port 1 requires Read contents absent from stable successor", async () => {
    const connector = new CyclingConnector(); connector.delivery = "delivered";
    const legacy = manifest();
    legacy.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-1";
    legacy.adapter_config = {};
    legacy.requirements.reads = ["run_deck"];
    const rt = new PolicyRuntime({ manifest: legacy, connector, mode: "auto", runId: "port1-with-reads",
      policy: decision => ({ candidate_digest: decision.candidate_digest, scores: [1], selected_index: 0 }) });
    for (let index = 0; index < 7; index += 1) expect((await rt.tick()).type).toBe("delivered");
    expect(rt.status().mode).toBe("auto");
    await rt.setMode("human");
  });

  it("resets cycle history at Human handoff and explicit segment begin", async () => {
    const connector = new CyclingConnector(); connector.delivery = "delivered";
    const rt = runtime(connector, async () => {});
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    expect((await rt.tick()).type).toBe("delivered");
    expect((await rt.tick()).type).toBe("delivered");
    await rt.setMode("human");
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    for (let index = 0; index < 4; index += 1) expect((await rt.tick()).type).toBe("delivered");
    expect(rt.status().mode).toBe("auto");
    await rt.setMode("human");
  });

  it("clears partial Auto history when control changes to Shadow and back", async () => {
    const connector = new CyclingConnector(); connector.delivery = "delivered";
    const rt = runtime(connector, async () => {});
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    expect((await rt.tick()).type).toBe("delivered");
    expect((await rt.tick()).type).toBe("delivered");
    await rt.setMode("shadow");
    expect((await rt.tick()).type).toBe("shadow");
    await rt.setMode("auto");
    for (let index = 0; index < 4; index += 1) expect((await rt.tick()).type).toBe("delivered");
    expect(rt.status().mode).toBe("auto");
    await rt.setMode("human");
  });

  it("keeps changing public progress out of the no-progress latch", async () => {
    const connector = new CyclingConnector(); connector.delivery = "delivered";
    const original = connector.submit.bind(connector);
    connector.submit = async (input) => {
      const result = await original(input);
      connector.current = { ...connector.current, persistent: { content_schema: "sts2.player-environment/persistent/run-player-1",
        content: { hp: 40 - connector.submitCount, energy: 2, deck: ["card"] } } };
      return { ...result, successor: connector.current };
    };
    const rt = runtime(connector, async () => {});
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    for (let index = 0; index < 7; index += 1) expect((await rt.tick()).type).toBe("delivered");
    expect(rt.status().mode).toBe("auto");
    await rt.setMode("human");
  });

  it("does not turn a failed cycle release into confirmed control or another submission", async () => {
    const connector = new CyclingConnector(); connector.delivery = "delivered"; connector.releaseFails = true;
    const rt = runtime(connector, async () => {});
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    for (let index = 0; index < 5; index += 1) expect((await rt.tick()).type).toBe("delivered");
    expect(await rt.tick()).toMatchObject({ type: "delivered", receipt: { delivery: "delivered" },
      status: { mode: "human", tainted: true, controller: "held" } });
    expect(rt.status()).toMatchObject({ mode: "human", tainted: true, controller: "held" });
    expect((await rt.tick()).type).toBe("not_admitted");
    expect(connector.submitCount).toBe(6);
  });

  it("never interprets an unknown receipt after partial repetition as a cycle or retries it", async () => {
    const connector = new CyclingConnector(), events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
    connector.delivery = "delivered";
    const rt = runtime(connector, async () => {}, { events });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    for (let index = 0; index < 5; index += 1) expect((await rt.tick()).type).toBe("delivered");
    connector.delivery = "unknown";
    expect((await rt.tick()).type).toBe("unknown");
    expect((await rt.tick()).type).toBe("not_admitted");
    expect(connector.submitCount).toBe(6);
    expect(rt.status().tainted).toBe(true);
    expect(events.some(event => event.kind === "handoff_to_human" && event.payload.reason === "semantic_cycle_detected")).toBe(false);
  });
  it("requires explicit Runtime-owned segment creation and keeps scope separate from its token", async () => {
    const connector = new FixtureConnector(), seen: Array<{ decision: PublicStatefulDecisionContext; control: PublicStatefulControlMetadata }> = [];
    const events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
    const rt = runtime(connector, async (decision, control) => { seen.push({ decision, control }); }, { events });
    expect(validatePolicyManifest(manifest()).adapter.protocol).toBe("sts2.policy-runtime/decision-only-ndjson-4");
    const bad = manifest(); bad.adapter_config = {};
    expect(() => validatePolicyManifest(bad)).toThrow(/model-neutral Runtime public observation profile/);
    const oldResearchProfile = manifest(); oldResearchProfile.adapter_config = { public_stateful_profile: "stpd/public-m2-observation-only-v1" };
    expect(() => validatePolicyManifest(oldResearchProfile)).toThrow(/model-neutral Runtime public observation profile/);
    await expect(rt.setMode("auto")).rejects.toThrow(/explicitly begun/);
    const begun = await rt.beginPublicStatefulSegment("bounded_policy_segment");
    expect(begun.scope).toBe("bounded_policy_segment");
    expect(rt.status().public_stateful_segment).toEqual(begun);
    expect(Object.keys(rt.status().public_stateful_segment!).sort()).toEqual(["episode_id", "scope", "segment_id"]);
    await expect(rt.beginPublicStatefulSegment("bounded_policy_segment")).rejects.toThrow(/already active/);
    await rt.setMode("shadow");
    expect((await rt.tick()).type).toBe("shadow");
    expect(seen[0]!.control).toMatchObject({ episode_scope: "bounded_policy_segment", episode_id: begun.episode_id, segment_id: begun.segment_id, observation_ordinal: 1 });
    expect(seen[0]!.control.continuity_token).not.toBe(begun.episode_id);
    expect(seen[0]!.decision).not.toHaveProperty("control");
    const started = events.find(event => event.kind === "public_stateful_episode_started")!;
    expect(started.payload.continuity_token_commitment).toBe(tokenCommitment("public-run", seen[0]!.control.continuity_token));
    const input = events.find(event => event.kind === "public_stateful_decision_input")!;
    expect(input.payload.continuity_token_commitment).toBe(started.payload.continuity_token_commitment);
    expect(JSON.stringify(events)).not.toContain(seen[0]!.control.continuity_token);
    expect(JSON.stringify(rt.status())).not.toContain(seen[0]!.control.continuity_token);
    const ended = await rt.endPublicStatefulSegment();
    expect(ended).toEqual(begun);
    expect(rt.status().public_stateful_segment).toBeNull();
    const endedEvent = events.find(event => event.kind === "public_stateful_episode_ended")!;
    expect(endedEvent.payload.continuity_token_commitment).toBe(started.payload.continuity_token_commitment);
    expect(JSON.stringify(events)).not.toContain(seen[0]!.control.continuity_token);
    await expect(rt.setMode("auto")).rejects.toThrow(/explicitly begun/);
  });

  it("scores the same Snapshot again after an explicit new segment in Shadow", async () => {
    const connector = new FixtureConnector(), events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
    const seen: Array<{ decision: PublicStatefulDecisionContext; control: PublicStatefulControlMetadata }> = [];
    const rt = runtime(connector, async (decision, control) => { seen.push({ decision, control }); }, { events });
    const first = await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("shadow");
    expect((await rt.tick()).type).toBe("shadow");
    await rt.endPublicStatefulSegment();
    const second = await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("shadow");
    expect((await rt.tick()).type).toBe("shadow");
    expect(seen).toHaveLength(2);
    expect(seen.map(item => item.control.observation_ordinal)).toEqual([1, 1]);
    expect(seen[0]!.decision.bundle.observation.snapshot_id).toBe(seen[1]!.decision.bundle.observation.snapshot_id);
    expect(seen[0]!.control.segment_id).toBe(first.segment_id);
    expect(seen[1]!.control.segment_id).toBe(second.segment_id);
    expect(events.filter(event => event.kind === "public_stateful_decision_input")).toHaveLength(2);
    await rt.setMode("human");
  });

  it("reuses an ordinal for a polled identity, ignores only observed_at, and accepts sequence gaps with a new identity", async () => {
    const connector = new FixtureConnector(), ordinals: number[] = [];
    const rt = runtime(connector, async (_decision, control) => { ordinals.push(control.observation_ordinal); });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    expect((await rt.tick()).type).toBe("not_delivered");
    connector.current = { ...connector.current, observed_at: "2026-10-03T00:00:01.000Z" };
    expect((await rt.tick()).type).toBe("not_delivered");
    connector.current = { ...connector.current, snapshot_id: "return-to-same-page", sequence: 19 };
    expect((await rt.tick()).type).toBe("not_delivered");
    expect(ordinals).toEqual([1, 1, 2]);
    await rt.setMode("human");
  });

  it("fails closed when one snapshot identity is reused with a conflicting canonical body", async () => {
    const connector = new FixtureConnector(), ordinals: number[] = [];
    const rt = runtime(connector, async (_decision, control) => { ordinals.push(control.observation_ordinal); });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("shadow");
    expect((await rt.tick()).type).toBe("shadow");
    connector.current = { ...connector.current, bound_actions: { ...connector.current.bound_actions, actions: [action("bound-a", "changed visible label")] } };
    const rejected = await rt.tick();
    expect(rejected).toMatchObject({ type: "not_admitted", reason: "public_snapshot_identity_conflict" });
    expect(ordinals).toEqual([1]);
    expect(connector.submitCount).toBe(0);
    expect(rt.status().mode).toBe("human");
  });

  it("keeps previous-action feedback stable for a same-observation submission retry", async () => {
    const connector = new FixtureConnector(), seen: Array<{ decision: PublicStatefulDecisionContext; control: PublicStatefulControlMetadata }> = [];
    connector.delivery = "delivered";
    const rt = runtime(connector, async (decision, control) => { seen.push({ decision, control }); });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    const first = await rt.tick();
    expect(first.type).toBe("delivered");
    if (first.type !== "delivered") throw new Error("fixture action was not delivered");
    connector.delivery = "not_delivered";
    expect((await rt.tick()).type).toBe("not_delivered");
    expect((await rt.tick()).type).toBe("not_delivered");
    expect(seen.map(item => item.control.observation_ordinal)).toEqual([1, 2, 2]);
    expect(seen[2]!.control.previous_action).toEqual(seen[1]!.control.previous_action);
    expect(seen[1]!.control.previous_action?.request_id).toBe(first.receipt.request_id);
    await rt.setMode("human");
  });

  it("fails closed when a new Snapshot moves to a different Connector session", async () => {
    const connector = new FixtureConnector(), rt = runtime(connector, async () => {});
    await rt.beginPublicStatefulSegment("single_game_episode");
    await rt.setMode("auto");
    expect((await rt.tick()).type).toBe("not_delivered");
    connector.current = { ...connector.current, snapshot_id: "new-session-page", sequence: 8,
      session: { runtime_instance_id: "runtime-restarted", environment_fingerprint: "new-environment" } };
    await expect(rt.tick()).resolves.toMatchObject({ type: "not_admitted", reason: "public_stateful_session_identity_drift" });
    expect(connector.submitCount).toBe(1);
    expect(rt.status().mode).toBe("human");
  });

  it("ends the segment when mode evidence fails so recovery needs an explicit begin", async () => {
    const connector = new FixtureConnector(), events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
    let failNextModeEvent = true;
    const ev = { append: vi.fn(async (kind: string, payload: Record<string, unknown>) => {
      if (kind === "mode_changed" && failNextModeEvent) { failNextModeEvent = false; throw new Error("fixture evidence failure"); }
      events.push({ kind, payload });
    }), finalize: vi.fn(async () => {}) } as unknown as AgentRunEvidence;
    const rt = new PolicyRuntime({ manifest: manifest(), connector, runId: "evidence-failure", evidence: ev,
      runtimeIdentity: { version: "test", code_sha256: "d".repeat(64) },
      publicStatefulPolicy: async (decision, control) => ({ output: { candidate_digest: decision.candidate_digest, scores: [1], selected_index: 0 }, completion: completion(decision, control) }) });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await expect(rt.setMode("shadow")).rejects.toThrow(/mode change failed closed/);
    expect(events.some(event => event.kind === "public_stateful_episode_ended" && event.payload.reason === "mode_change_evidence_write_failed")).toBe(true);
    await expect(rt.setMode("auto")).rejects.toThrow(/explicitly begun/);
  });

  it("taints a port 4 run when decision evidence fails after a public input was recorded", async () => {
    const connector = new FixtureConnector();
    const events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
    let failDecisionOnce = true;
    const faultEvidence = {
      append: vi.fn(async (kind: string, payload: Record<string, unknown>) => {
        if (kind === "decision" && failDecisionOnce) {
          failDecisionOnce = false;
          throw new Error("injected decision evidence write failure");
        }
        events.push({ kind, payload });
      }),
      finalize: vi.fn(async () => {}),
    } as unknown as AgentRunEvidence;
    const rt = runtime(connector, async () => {}, { events, evidenceSink: faultEvidence });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("shadow");

    await expect(rt.tick()).resolves.toMatchObject({ type: "not_admitted", reason: "agent_evidence_write_failed" });
    expect(rt.status().tainted).toBe(true);
    expect(connector.submitCount).toBe(0);
    expect(events.map(event => event.kind)).toContain("public_stateful_decision_input");
    expect(events.map(event => event.kind)).not.toContain("decision");
    expect(events.map(event => event.kind)).toContain("public_stateful_episode_ended");
    expect(events.map(event => event.kind)).toContain("runtime_tainted");
    expect(events.findIndex(event => event.kind === "public_stateful_episode_ended"))
      .toBeLessThan(events.findIndex(event => event.kind === "runtime_tainted"));
  });

  it("passes only a delivered action receipt and verified successor as next-call feedback", async () => {
    const connector = new FixtureConnector(), seen: PublicStatefulControlMetadata[] = [];
    connector.delivery = "delivered";
    const rt = runtime(connector, async (_decision, control) => { seen.push(control); });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    expect((await rt.tick()).type).toBe("delivered");
    expect((await rt.tick()).type).toBe("delivered");
    expect(seen[1]!.previous_action).toMatchObject({
      source_snapshot_id: "snapshot-a", bound_action_id: "bound-a",
      receipt: { delivery: "delivered" }, successor: { snapshot_id: "successor-8", sequence: 8 }
    });
    await rt.setMode("human");
  });

  it("does not retry an unknown delivery under the public stateful port", async () => {
    const connector = new FixtureConnector(), rt = runtime(connector, async () => {});
    connector.delivery = "unknown";
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    expect((await rt.tick()).type).toBe("unknown");
    expect((await rt.tick()).type).toBe("not_admitted");
    expect(connector.submitCount).toBe(1);
    expect(rt.status().tainted).toBe(true);
  });

  it("rotates token and segment after timeout; a late old completion cannot submit", async () => {
    const connector = new FixtureConnector(), events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
    let firstInput: { decision: PublicStatefulDecisionContext; control: PublicStatefulControlMetadata } | null = null;
    let resolveFirst!: (value: ReturnType<typeof completion>) => void;
    const rt = new PolicyRuntime({ manifest: manifest(), connector, runId: "late-run", evidence: evidence(events),
      runtimeIdentity: { version: "test", code_sha256: "d".repeat(64) }, policyTimeoutMs: 10,
      publicStatefulPolicy: async (decision, control) => {
        if (!firstInput) {
          firstInput = { decision, control };
          return { output: { candidate_digest: decision.candidate_digest, scores: [1], selected_index: 0 }, completion: await new Promise(resolve => { resolveFirst = resolve; }) };
        }
        return { output: { candidate_digest: decision.candidate_digest, scores: [1], selected_index: null }, completion: completion(decision, control) };
      } });
    await rt.beginPublicStatefulSegment("single_game_episode");
    await rt.setMode("shadow");
    expect((await rt.tick()).type).toBe("not_admitted");
    const old = firstInput!;
    const nextSegment = await rt.beginPublicStatefulSegment("single_game_episode");
    await rt.setMode("shadow");
    expect((await rt.tick()).type).toBe("shadow");
    expect(events.some(event => event.kind === "public_stateful_observation_segment_reset" && event.payload.memory_continuity === false)).toBe(true);
    expect(events.some(event => event.kind === "public_stateful_episode_ended" && event.payload.requires_explicit_begin === true)).toBe(true);
    const reset = events.find(event => event.kind === "public_stateful_observation_segment_reset")!;
    expect(reset.payload.previous_continuity_token_commitment).toBe(tokenCommitment("late-run", old.control.continuity_token));
    expect(reset.payload.continuity_token_commitment).not.toBe(reset.payload.previous_continuity_token_commitment);
    const ended = events.find(event => event.kind === "public_stateful_episode_ended")!;
    expect(ended.payload.continuity_token_commitment).toBe(reset.payload.continuity_token_commitment);
    expect(JSON.stringify(events)).not.toContain(old.control.continuity_token);
    resolveFirst(completion(old.decision, old.control));
    await new Promise<void>(resolve => setImmediate(resolve));
    expect(connector.submitCount).toBe(0);
    expect(nextSegment.episode_id).not.toBe(old.control.episode_id);
  });

  it("accepts only v4 completion watermarks and drops a late response after request cancellation", async () => {
    const writes: string[] = [];
    const stdin = new Writable({ write(chunk, _encoding, callback) { writes.push(chunk.toString()); callback(); } });
    const stdout = new PassThrough(), stderr = new PassThrough();
    const child = Object.assign(new EventEmitter(), { stdin, stdout, stderr, exitCode: null, kill: vi.fn(() => true) });
    const port = new NdjsonPolicyPort(child as unknown as ConstructorParameters<typeof NdjsonPolicyPort>[0]);
    stdout.write(`${JSON.stringify({ schema: POLICY_PORT_V4_SCHEMA, message_type: "ready", adapter: manifest().adapter })}\n`);
    await port.ready();
    const observation = snapshot();
    const decision: PublicStatefulDecisionContext = { run_id: "run", manifest: manifest(), bundle: { observation, reads: [] },
      candidate_digest: candidateOrderDigest(["bound-a"]), candidate_count: 1 };
    const control: PublicStatefulControlMetadata = { continuity_token: "token-a", episode_scope: "bounded_policy_segment",
      episode_id: "episode-a", segment_id: "segment-a", observation_ordinal: 1, previous_action: null };
    try {
      const first = port.decideV4(decision, control, new AbortController().signal, () => {});
      const firstId = (JSON.parse(writes[0]!) as { request_id: string }).request_id;
      const done = completion(decision, control);
      stdout.write(`${JSON.stringify({ schema: POLICY_PORT_V4_SCHEMA, message_type: "decision", request_id: firstId,
        output: { candidate_digest: decision.candidate_digest, scores: [1], selected_index: 0 }, completion: done })}\n`);
      await expect(first).resolves.toMatchObject({ completion: done });

      const controller = new AbortController();
      const late = port.decideV4(decision, control, controller.signal, () => {});
      const lateId = (JSON.parse(writes[1]!) as { request_id: string }).request_id;
      const rejected = expect(late).rejects.toThrow("cancelled");
      controller.abort();
      await rejected;
      stdout.write(`${JSON.stringify({ schema: POLICY_PORT_V4_SCHEMA, message_type: "decision", request_id: lateId,
        output: { candidate_digest: decision.candidate_digest, scores: [1], selected_index: 0 }, completion: done })}\n`);
      await new Promise<void>(resolve => setImmediate(resolve));
      expect(child.kill).not.toHaveBeenCalled();
    } finally { port.close(); stdin.destroy(); stdout.destroy(); stderr.destroy(); }
  });

  it("validates a v4 child request against generic Snapshot and the complete ordered catalog", async () => {
    const inputStream = new PassThrough(), outputStream = new PassThrough();
    const seen: PublicStatefulDecisionContext[] = [], namespaces: PublicStatefulMemoryNamespace[] = [];
    const policyServer = servePublicStatefulPolicyPort(namespace => {
      namespaces.push(namespace);
      return async decision => {
        seen.push(decision);
        return { candidate_digest: decision.candidate_digest, scores: [1], selected_index: 0 };
      };
    }, inputStream, outputStream);
    const observation = snapshot();
    const decision: PublicStatefulDecisionContext = { run_id: "run", manifest: manifest(), bundle: { observation, reads: [] },
      candidate_digest: candidateOrderDigest(["bound-a"]), candidate_count: 1 };
    const control: PublicStatefulControlMetadata = { continuity_token: "token-a", episode_scope: "single_game_episode",
      episode_id: "episode-a", segment_id: "segment-a", observation_ordinal: 1, previous_action: null };
    const readResponse = () => new Promise<Record<string, unknown>>(resolve => outputStream.once("data", chunk => resolve(JSON.parse(chunk.toString().trim()) as Record<string, unknown>)));
    const send = async (requestId: string, nextDecision: PublicStatefulDecisionContext, nextControl: PublicStatefulControlMetadata) => {
      const responseLine = readResponse();
      inputStream.write(`${JSON.stringify({ schema: POLICY_PORT_V4_SCHEMA, message_type: "decide", request_id: requestId, decision: nextDecision, control: nextControl })}\n`);
      return responseLine;
    };
    const response = await send("request-a", decision, control) as { message_type: string; output: { candidate_digest: string; scores: number[]; selected_index: number | null }; completion: ReturnType<typeof completion> };
    expect(response.message_type).toBe("decision");
    expect(response.completion).toEqual(completion(decision, control));
    expect(seen).toHaveLength(1);
    expect(seen[0]).not.toHaveProperty("control");
    expect(seen[0]).not.toHaveProperty("previous_action");

    const polledDecision: PublicStatefulDecisionContext = { ...decision, bundle: { observation: { ...observation, observed_at: "2026-10-03T00:00:01.000Z" }, reads: [] } };
    const cached = await send("request-a-retry", polledDecision, control) as { message_type: string; completion: ReturnType<typeof completion> };
    expect(cached.message_type).toBe("decision");
    expect(seen).toHaveLength(1);

    const changedBody: PublicStatefulDecisionContext = { ...decision, bundle: { observation: { ...observation,
      bound_actions: { ...observation.bound_actions, actions: [action("bound-a", "conflict")] } }, reads: [] } };
    const conflict = await send("request-a-conflict", changedBody, control);
    expect(conflict.message_type).toBe("error");
    expect(seen).toHaveLength(1);

    const priorActionAck: PublicStatefulControlMetadata["previous_action"] = {
      decision_id: "decision-before", source_snapshot_id: "snapshot-before", candidate_digest: "b".repeat(64),
      bound_action_id: "bound-before", request_id: "request-before", receipt: { delivery: "delivered", reason_code: null },
      successor: { snapshot_id: observation.snapshot_id, sequence: observation.sequence }
    };
    const ackControl = { ...control, previous_action: priorActionAck };
    const ackedRetry = await send("request-a-ack", decision, ackControl) as { message_type: string; output: { candidate_digest: string; scores: number[]; selected_index: number | null }; completion: ReturnType<typeof completion> };
    expect(ackedRetry.message_type).toBe("decision");
    expect(ackedRetry.output).toEqual(response.output);
    expect(ackedRetry.completion.previous_action_request_id).toBe("request-before");
    expect(seen).toHaveLength(1);

    const nextObservation = snapshot("snapshot-b", 19);
    const nextDecision: PublicStatefulDecisionContext = { ...decision, bundle: { observation: nextObservation, reads: [] } };
    const priorAction: PublicStatefulControlMetadata["previous_action"] = {
      decision_id: "decision-a", source_snapshot_id: "snapshot-a", candidate_digest: decision.candidate_digest,
      bound_action_id: "bound-a", request_id: "request-previous", receipt: { delivery: "delivered", reason_code: null },
      successor: { snapshot_id: "snapshot-b", sequence: 19 }
    };
    const nextControl = { ...control, observation_ordinal: 2, previous_action: priorAction };
    const next = await send("request-b", nextDecision, nextControl) as { message_type: string; completion: ReturnType<typeof completion> };
    expect(next.message_type).toBe("decision");
    expect(next.completion.previous_action_request_id).toBe("request-previous");
    expect(seen).toHaveLength(2);
    expect(seen[1]).not.toHaveProperty("control");
    expect(seen[1]).not.toHaveProperty("previous_action");

    const newNamespaceControl = { ...control, continuity_token: "token-b", episode_id: "episode-b", segment_id: "segment-b" };
    const newNamespaceDecision: PublicStatefulDecisionContext = { ...decision, bundle: { observation: snapshot("snapshot-c", 20), reads: [] } };
    const reset = await send("request-c", newNamespaceDecision, newNamespaceControl) as { message_type: string };
    expect(reset.message_type).toBe("decision");
    expect(namespaces).toHaveLength(2);
    expect(namespaces[1]!.continuity_token).toBe("token-b");
    expect(seen).toHaveLength(3);
    inputStream.end();
    await policyServer;
    outputStream.destroy();
  });
});
